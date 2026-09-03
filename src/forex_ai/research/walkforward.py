from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Sequence


@dataclass(frozen=True)
class Fold:
    train_start: datetime
    train_end: datetime
    validation_start: datetime
    validation_end: datetime
    test_start: datetime | None = None
    test_end: datetime | None = None

    def __post_init__(self) -> None:
        if not (self.train_start < self.train_end <= self.validation_start < self.validation_end):
            raise ValueError("train/validation boundaries overlap or leak")
        if self.test_start is not None:
            if self.test_end is None or not (self.validation_end <= self.test_start < self.test_end):
                raise ValueError("validation/test boundaries overlap or leak")


def rolling_folds(boundaries: Sequence[datetime], *, anchored: bool = False) -> tuple[Fold, ...]:
    """Build folds from ordered boundaries [b0,b1,b2,b3,...].

    Each fold consumes train, validation and test windows. Test is untouched by
    parameter selection; callers receive it only as a distinct field.
    """
    points = tuple(boundaries)
    if len(points) < 4 or any(a >= b for a, b in zip(points[:-1], points[1:])):
        raise ValueError("strictly increasing boundaries required")
    folds: list[Fold] = []
    for idx in range(len(points) - 3):
        train_start = points[0] if anchored else points[idx]
        train_end = points[idx + 1]
        validation_start = train_end
        validation_end = points[idx + 2]
        test_start = validation_end
        test_end = points[idx + 3]
        folds.append(Fold(train_start, train_end, validation_start, validation_end, test_start, test_end))
    return tuple(folds)


def assert_timestamp_in_split(timestamp: datetime, fold: Fold, split: str) -> bool:
    if split == "train":
        return fold.train_start <= timestamp < fold.train_end
    if split == "validation":
        return fold.validation_start <= timestamp < fold.validation_end
    if split == "test" and fold.test_start is not None and fold.test_end is not None:
        return fold.test_start <= timestamp < fold.test_end
    raise ValueError(f"unknown split {split}")
