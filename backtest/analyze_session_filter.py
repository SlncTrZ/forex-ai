#!/usr/bin/env python3
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from statistics import mean

from analyze_cluster_entry_policy import collect
from analyze_entry_timing import ROOT, simulate
from forex_ai.research.dataset import load_frozen_replay_dataset

BLOCKS = tuple((start, start + 4) for start in range(0, 24, 4))


def main():
    for symbol in ('EURUSDc', 'XAUUSDc'):
        dataset = load_frozen_replay_dataset(ROOT / symbol / 'replay.jsonl')
        events = dataset.events
        first = [cluster[0] for cluster in collect(events)]
        print('\n===', symbol, 'first-setups=', len(first), '===')
        for window in (45, 60):
            print('--', window, 'minutes --')
            for start, end in BLOCKS:
                picked = [(i, c) for i, c in first if start <= events[i].clock_utc.hour < end]
                if not picked:
                    continue
                values = [simulate(events, i, c, window)[0] for i, c in picked]
                weeks = defaultdict(list)
                for (i, _), value in zip(picked, values):
                    day = events[i].clock_utc.date()
                    monday = day - timedelta(days=day.weekday())
                    weeks[monday.isoformat()].append(value)
                weekly = {week: round(mean(vals), 3) for week, vals in sorted(weeks.items())}
                print(f'{start:02d}-{end-1:02d}UTC', 'n=', len(values), 'R=', round(mean(values), 4),
                      'positive_weeks=', sum(v > 0 for v in weekly.values()), '/', len(weekly), 'weekly=', weekly)


if __name__ == '__main__':
    main()
