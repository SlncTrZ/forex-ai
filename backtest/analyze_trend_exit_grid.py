#!/usr/bin/env python3
from __future__ import annotations

from datetime import timedelta
import os
from pathlib import Path
from statistics import mean

from analyze_sensitivity import ACTUAL_BY_BASE, _cluster_lifecycle_replay, _config, _resolve_dataset_root
from forex_ai.research.dataset import load_frozen_replay_dataset
from forex_ai.strategy.v1 import trend_pullback

TARGETS = (0.75, 1.0, 1.25, 1.5, 2.0)
EXPIRIES = (30, 45, 60, 90)


def week_slices(events):
    first = events[0].clock_utc.date()
    monday = first - timedelta(days=first.weekday())
    for offset in range(4):
        start = monday + timedelta(days=7 * offset)
        end = start + timedelta(days=7)
        yield start.isoformat(), tuple(e for e in events if start <= e.clock_utc.date() < end)


def main():
    root, standard = _resolve_dataset_root(None)
    print('dataset=', root)
    requested = os.getenv('FOREX_AI_RESEARCH_SYMBOL')
    symbols = (requested,) if requested else ('EURUSD', 'XAUUSD')
    for base in symbols:
        actual = ACTUAL_BY_BASE[base]
        dataset = load_frozen_replay_dataset(root / actual / 'replay.jsonl')
        rows = []
        for target_r in TARGETS:
            for expiry in EXPIRIES:
                cfg = _config(trend_pullback.DEFAULT_CONFIG, target_r=target_r, expiry_minutes=expiry)
                full = _cluster_lifecycle_replay(dataset.events, trend_pullback.evaluate, cfg)
                weekly = []
                for label, events in week_slices(dataset.events):
                    result = _cluster_lifecycle_replay(events, trend_pullback.evaluate, cfg)
                    weekly.append((label, result.expectancy_r, result.total_r, result.closed))
                exps = [x[1] for x in weekly]
                rows.append({
                    'target_r': target_r,
                    'expiry': expiry,
                    'full_exp': full.expectancy_r,
                    'full_total': full.total_r,
                    'closed': full.closed,
                    'positive_weeks': sum(x > 0 for x in exps),
                    'mean_week': mean(exps),
                    'worst_week': min(exps),
                    'best_week': max(exps),
                    'weekly': weekly,
                    'exits': full.exits,
                })
        rows.sort(key=lambda r: (r['positive_weeks'], r['worst_week'], r['mean_week'], r['full_exp']), reverse=True)
        print('\n===', base, 'robust ranking ===')
        for row in rows[:12]:
            print(row)
        baseline = next(r for r in rows if r['target_r'] == 2.0 and r['expiry'] == 45)
        print('BASELINE', baseline)


if __name__ == '__main__':
    main()
