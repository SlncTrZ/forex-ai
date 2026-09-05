#!/usr/bin/env python3
from __future__ import annotations

from datetime import timedelta
from statistics import mean

from analyze_entry_timing import ROOT, simulate
from forex_ai.research.dataset import load_frozen_replay_dataset
from forex_ai.strategy.v1 import trend_pullback as strategy


def collect(events):
    clusters = []
    current = []
    previous = None
    for index, event in enumerate(events):
        result = strategy.evaluate(event.snapshot, strategy.DEFAULT_CONFIG, event.clock_utc)
        candidate = result.candidate
        if candidate is None:
            continue
        same = previous is not None and candidate.side == previous.side and candidate.generated_at_utc - previous.generated_at_utc <= timedelta(minutes=30)
        if not same:
            if current:
                clusters.append(current)
            current = []
        current.append((index, candidate))
        previous = candidate
    if current:
        clusters.append(current)
    return clusters


def main():
    for symbol in ('EURUSDc', 'XAUUSDc'):
        dataset = load_frozen_replay_dataset(ROOT / symbol / 'replay.jsonl')
        events = dataset.events
        clusters = collect(events)
        print('\n===', symbol, 'clusters=', len(clusters), '===')
        for window in (45, 60):
            print('-- window', window, 'minutes --')
            for ordinal in (1, 2, 3, 4):
                picked = [cluster[ordinal - 1] for cluster in clusters if len(cluster) >= ordinal]
                values = [simulate(events, index, candidate, window)[0] for index, candidate in picked]
                if not values:
                    continue
                by_week = {}
                for (index, candidate), value in zip(picked, values):
                    day = events[index].clock_utc.date()
                    monday = day - timedelta(days=day.weekday())
                    by_week.setdefault(monday.isoformat(), []).append(value)
                weekly = {week: round(mean(vals), 4) for week, vals in sorted(by_week.items())}
                print('ordinal', ordinal, 'n=', len(values), 'expectancy=', round(mean(values), 4),
                      'positive_weeks=', sum(v > 0 for v in weekly.values()), 'weekly=', weekly)


if __name__ == '__main__':
    main()
