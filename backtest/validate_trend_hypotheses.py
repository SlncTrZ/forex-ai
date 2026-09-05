#!/usr/bin/env python3
from __future__ import annotations

from collections import Counter
from datetime import timedelta
import os
from pathlib import Path
from statistics import mean

from analyze_sensitivity import _config
from forex_ai.research.dataset import load_frozen_replay_dataset
from forex_ai.strategy.v1 import trend_pullback

def _backtest_root() -> Path:
    explicit = os.getenv('FOREX_AI_BACKTEST_ROOT')
    if explicit:
        return Path(explicit).expanduser()
    runtime_root = Path(os.getenv('FOREX_AI_RUNTIME_ROOT', '~/apps/forex-ai')).expanduser()
    return runtime_root / 'backtest'


DATASETS = {
    'IS': _backtest_root() / 'data' / '2026-08-10_2026-09-04',
    'OOS': _backtest_root() / 'data' / '2026-07-13_2026-08-07',
}
ACTUAL = {'EURUSD': 'EURUSDc', 'XAUUSD': 'XAUUSDc'}


def replay(events, config, *, setup_filter=lambda event, candidate: True):
    opened = None
    previous_candidate = None
    rs = []
    exits = Counter()
    setup_count = accepted_setups = skipped_open = 0

    for event in events:
        bar = event.snapshot.timeframes['M15'].closed_bars[-1]
        if opened is not None:
            candidate, entry, risk = opened
            stop = bar.low <= candidate.stop_loss if candidate.side == 'BUY' else bar.high >= candidate.stop_loss
            target = bar.high >= candidate.take_profit if candidate.side == 'BUY' else bar.low <= candidate.take_profit
            expired = event.clock_utc >= candidate.expires_at_utc
            if stop or target or expired:
                if stop:
                    exit_price, reason = candidate.stop_loss, 'STOP'
                elif target:
                    exit_price, reason = candidate.take_profit, 'TARGET'
                else:
                    exit_price = event.snapshot.bid if candidate.side == 'BUY' else event.snapshot.ask
                    reason = 'EXPIRY'
                signed = exit_price - entry if candidate.side == 'BUY' else entry - exit_price
                rs.append(signed / risk)
                exits[reason] += 1
                opened = None

        result = trend_pullback.evaluate(event.snapshot, config, event.clock_utc)
        candidate = result.candidate
        if candidate is None:
            continue
        same_cluster = (
            previous_candidate is not None
            and candidate.side == previous_candidate.side
            and candidate.generated_at_utc - previous_candidate.generated_at_utc <= timedelta(minutes=30)
        )
        previous_candidate = candidate
        if same_cluster:
            continue

        setup_count += 1
        if not setup_filter(event, candidate):
            continue
        accepted_setups += 1
        if opened is not None:
            skipped_open += 1
            continue
        risk = abs(candidate.reference_entry - candidate.stop_loss)
        if risk > 0:
            opened = (candidate, candidate.reference_entry, risk)

    return {
        'setups': setup_count,
        'accepted_setups': accepted_setups,
        'closed': len(rs),
        'skipped_open': skipped_open,
        'exp': mean(rs) if rs else 0.0,
        'total': sum(rs),
        'win_rate': sum(x > 0 for x in rs) / len(rs) if rs else 0.0,
        'exits': dict(exits),
    }


def by_week(events, config, setup_filter):
    weeks = sorted({(e.clock_utc.date() - timedelta(days=e.clock_utc.weekday())).isoformat() for e in events})
    rows = []
    for week in weeks:
        start = next(e.clock_utc.date() - timedelta(days=e.clock_utc.weekday()) for e in events if (e.clock_utc.date() - timedelta(days=e.clock_utc.weekday())).isoformat() == week)
        subset = tuple(e for e in events if start <= e.clock_utc.date() < start + timedelta(days=7))
        result = replay(subset, config, setup_filter=setup_filter)
        rows.append((week, result['closed'], result['exp'], result['total']))
    return rows


def main():
    hypotheses = {
        'EURUSD': [
            ('baseline_45', trend_pullback.DEFAULT_CONFIG, lambda e, c: True),
            ('expiry_60', _config(trend_pullback.DEFAULT_CONFIG, expiry_minutes=60), lambda e, c: True),
            ('skip_monday_45', trend_pullback.DEFAULT_CONFIG, lambda e, c: e.clock_utc.weekday() != 0),
        ],
        'XAUUSD': [
            ('baseline_45', trend_pullback.DEFAULT_CONFIG, lambda e, c: True),
            ('late_16_23utc_45', trend_pullback.DEFAULT_CONFIG, lambda e, c: 16 <= e.clock_utc.hour < 24),
        ],
    }

    for label, root in DATASETS.items():
        print('\n###', label, root)
        for base, specs in hypotheses.items():
            events = load_frozen_replay_dataset(root / ACTUAL[base] / 'replay.jsonl').events
            print('\n', base)
            for name, config, predicate in specs:
                result = replay(events, config, setup_filter=predicate)
                print(name, result)
                print(' weeks', by_week(events, config, predicate))


if __name__ == '__main__':
    main()
