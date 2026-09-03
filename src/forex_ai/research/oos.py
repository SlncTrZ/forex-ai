from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Iterable

from forex_ai.strategy.v1.contracts import fingerprint

from .dataset import FrozenReplayDataset
from .evaluation import EvaluationMetrics, evaluate_trades
from .replay import ReplayArtifact, ReplayEngine, ReplayEvent
from .walkforward import Fold, assert_timestamp_in_split


@dataclass(frozen=True)
class SplitReplayEvidence:
    split: str
    start_utc: datetime
    end_utc: datetime
    event_count: int
    replay_artifact: ReplayArtifact
    metrics: EvaluationMetrics

    @property
    def fingerprint(self) -> str:
        return fingerprint({
            "split": self.split,
            "start_utc": self.start_utc,
            "end_utc": self.end_utc,
            "event_count": self.event_count,
            "dataset_fingerprint": self.replay_artifact.dataset_fingerprint,
            "strategy_config_fingerprint": self.replay_artifact.strategy_config_fingerprint,
            "cost_model_fingerprint": self.replay_artifact.cost_model_fingerprint,
            "trades": [asdict(trade) for trade in self.replay_artifact.trades],
            "candidate_count": self.replay_artifact.candidate_count,
            "rejected_count": self.replay_artifact.rejected_count,
            "metrics": asdict(self.metrics),
        })


@dataclass(frozen=True)
class WalkForwardEvidence:
    dataset_manifest_fingerprint: str
    fold: Fold
    strategy_config_fingerprint: str
    cost_model_fingerprint: str
    train: SplitReplayEvidence
    validation: SplitReplayEvidence
    test: SplitReplayEvidence

    @property
    def fingerprint(self) -> str:
        return fingerprint({
            "dataset_manifest_fingerprint": self.dataset_manifest_fingerprint,
            "fold": asdict(self.fold),
            "strategy_config_fingerprint": self.strategy_config_fingerprint,
            "cost_model_fingerprint": self.cost_model_fingerprint,
            "train": self.train.fingerprint,
            "validation": self.validation.fingerprint,
            "test": self.test.fingerprint,
        })


@dataclass(frozen=True)
class OOSAcceptancePolicy:
    min_test_trades: int
    min_expectancy_r: float
    max_drawdown_account_currency: float | None = None
    require_positive_ci_lower_bound: bool = False

    def __post_init__(self) -> None:
        if self.min_test_trades <= 0:
            raise ValueError("min_test_trades must be > 0")
        if self.max_drawdown_account_currency is not None and self.max_drawdown_account_currency < 0:
            raise ValueError("max_drawdown_account_currency must be >= 0")


@dataclass(frozen=True)
class OOSAcceptanceResult:
    approved: bool
    reason_codes: tuple[str, ...]
    evidence_fingerprint: str


def _split_bounds(fold: Fold, split: str) -> tuple[datetime, datetime]:
    if split == "train":
        return fold.train_start, fold.train_end
    if split == "validation":
        return fold.validation_start, fold.validation_end
    if split == "test" and fold.test_start is not None and fold.test_end is not None:
        return fold.test_start, fold.test_end
    raise ValueError(f"fold does not define split {split}")


def split_events(events: Iterable[ReplayEvent], fold: Fold, split: str) -> tuple[ReplayEvent, ...]:
    return tuple(event for event in events if assert_timestamp_in_split(event.clock_utc, fold, split))


def _run_split(
    engine: ReplayEngine,
    events: tuple[ReplayEvent, ...],
    *,
    fold: Fold,
    split: str,
    account_r_value: float,
) -> SplitReplayEvidence:
    start, end = _split_bounds(fold, split)
    if not events:
        raise ValueError(f"{split} split contains no replay events")
    artifact = engine.run(events, account_r_value=account_r_value)
    return SplitReplayEvidence(
        split=split,
        start_utc=start,
        end_utc=end,
        event_count=len(events),
        replay_artifact=artifact,
        metrics=evaluate_trades(artifact.trades),
    )


def run_walk_forward_fold(
    dataset: FrozenReplayDataset,
    *,
    fold: Fold,
    engine: ReplayEngine,
    account_r_value: float = 1.0,
) -> WalkForwardEvidence:
    if fold.test_start is None or fold.test_end is None:
        raise ValueError("walk-forward evidence requires a final test split")
    if not dataset.events:
        raise ValueError("frozen dataset is empty")
    if fold.train_start < dataset.events[0].clock_utc or fold.test_end > dataset.events[-1].clock_utc:
        raise ValueError("fold boundaries exceed frozen dataset range")
    train_events = split_events(dataset.events, fold, "train")
    validation_events = split_events(dataset.events, fold, "validation")
    test_events = split_events(dataset.events, fold, "test")
    train = _run_split(engine, train_events, fold=fold, split="train", account_r_value=account_r_value)
    validation = _run_split(engine, validation_events, fold=fold, split="validation", account_r_value=account_r_value)
    test = _run_split(engine, test_events, fold=fold, split="test", account_r_value=account_r_value)
    return WalkForwardEvidence(
        dataset_manifest_fingerprint=dataset.manifest.fingerprint,
        fold=fold,
        strategy_config_fingerprint=engine.config.fingerprint,
        cost_model_fingerprint=engine.cost_model.fingerprint,
        train=train,
        validation=validation,
        test=test,
    )


def assess_oos(evidence: WalkForwardEvidence, policy: OOSAcceptancePolicy) -> OOSAcceptanceResult:
    reasons: list[str] = []
    metrics = evidence.test.metrics
    if metrics.trade_count < policy.min_test_trades:
        reasons.append("INSUFFICIENT_OOS_TRADES")
    if metrics.expectancy_r < policy.min_expectancy_r:
        reasons.append("OOS_EXPECTANCY_BELOW_THRESHOLD")
    if (
        policy.max_drawdown_account_currency is not None
        and metrics.max_drawdown_account_currency > policy.max_drawdown_account_currency
    ):
        reasons.append("OOS_DRAWDOWN_LIMIT")
    if policy.require_positive_ci_lower_bound and metrics.ci_expectancy_r_95[0] <= 0:
        reasons.append("OOS_EXPECTANCY_CI_NOT_POSITIVE")
    unique = tuple(dict.fromkeys(reasons))
    return OOSAcceptanceResult(not unique, unique, evidence.fingerprint)
