#!/usr/bin/env python3
from __future__ import annotations

from datetime import timedelta
from statistics import mean

from analyze_sensitivity import _resolve_dataset_root
from forex_ai.research.dataset import load_frozen_replay_dataset
from forex_ai.strategy.v1 import trend_pullback as strategy

ROOT = _resolve_dataset_root(None)[0]
SYMBOLS = ('EURUSDc', 'XAUUSDc')
WINDOWS = (30, 45, 60, 90, 120)


def simulate(events, index, candidate, minutes):
    risk = abs(candidate.reference_entry - candidate.stop_loss)
    mfe = mae = 0.0
    end = candidate.generated_at_utc + timedelta(minutes=minutes)
    last = events[index].snapshot
    for event in events[index + 1:]:
        if event.clock_utc > end:
            break
        bar = event.snapshot.timeframes['M15'].closed_bars[-1]
        last = event.snapshot
        if candidate.side == 'BUY':
            mfe = max(mfe, (bar.high - candidate.reference_entry) / risk)
            mae = max(mae, (candidate.reference_entry - bar.low) / risk)
            stop = bar.low <= candidate.stop_loss
            target = bar.high >= candidate.take_profit
        else:
            mfe = max(mfe, (candidate.reference_entry - bar.low) / risk)
            mae = max(mae, (bar.high - candidate.reference_entry) / risk)
            stop = bar.high >= candidate.stop_loss
            target = bar.low <= candidate.take_profit
        if stop:
            return -1.0, 'STOP', mfe, mae
        if target:
            return 2.0, 'TARGET', mfe, mae
    exit_price = last.bid if candidate.side == 'BUY' else last.ask
    signed = exit_price - candidate.reference_entry if candidate.side == 'BUY' else candidate.reference_entry - exit_price
    return signed / risk, 'EXPIRY', mfe, mae


def summarize(label, rows, window):
    if not rows:
        return
    outcomes = [row[window] for row in rows]
    print(label, 'n=', len(rows), 'R=', round(mean(x[0] for x in outcomes), 4),
          'MFE=', round(mean(x[2] for x in outcomes), 3), 'MAE=', round(mean(x[3] for x in outcomes), 3),
          'target=', sum(x[1] == 'TARGET' for x in outcomes), 'stop=', sum(x[1] == 'STOP' for x in outcomes))


def main():
    for symbol in SYMBOLS:
        dataset = load_frozen_replay_dataset(ROOT / symbol / 'replay.jsonl')
        events = dataset.events
        rows = []
        previous = None
        cluster = -1
        ordinal = 0
        for index, event in enumerate(events):
            result = strategy.evaluate(event.snapshot, strategy.DEFAULT_CONFIG, event.clock_utc)
            candidate = result.candidate
            if candidate is None:
                continue
            same_cluster = previous is not None and candidate.side == previous.side and candidate.generated_at_utc - previous.generated_at_utc <= timedelta(minutes=30)
            if same_cluster:
                ordinal += 1
            else:
                cluster += 1
                ordinal = 1
            previous = candidate
            row = {
                'candidate': candidate,
                'ordinal': ordinal,
                'dow': event.clock_utc.strftime('%a'),
                'hour': event.clock_utc.hour,
                'side': candidate.side,
            }
            for minutes in WINDOWS:
                row[minutes] = simulate(events, index, candidate, minutes)
            rows.append(row)

        first = [row for row in rows if row['ordinal'] == 1]
        print('\n===', symbol, 'candidates=', len(rows), 'clusters=', len(first), '===')
        for ordinal in (1, 2, 3):
            summarize(f'ordinal={ordinal}', [r for r in rows if r['ordinal'] == ordinal], 45)
        summarize('ordinal>=4', [r for r in rows if r['ordinal'] >= 4], 45)

        print('-- first candidate by side --')
        for side in ('BUY', 'SELL'):
            subset = [r for r in first if r['side'] == side]
            summarize(side + ' 45m', subset, 45)
            summarize(side + ' 60m', subset, 60)

        print('-- first candidate by weekday @60m --')
        for day in ('Mon', 'Tue', 'Wed', 'Thu', 'Fri'):
            summarize(day, [r for r in first if r['dow'] == day], 60)

        print('-- first candidate by UTC 4h block @60m --')
        for start in range(0, 24, 4):
            summarize(f'{start:02d}-{start + 3:02d}UTC', [r for r in first if start <= r['hour'] < start + 4], 60)

        print('-- MFE thresholds first candidate @60m --')
        for threshold in (0.25, 0.5, 1.0, 1.5, 2.0):
            count = sum(r[60][2] >= threshold for r in first)
            print('MFE>=', threshold, count, '/', len(first), round(count / len(first), 3))

        improved = [r for r in first if r[45][0] <= 0 and r[60][0] > r[45][0] + 0.15]
        print('-- 45m losers materially better by 60m --', len(improved))
        if improved:
            print('mean45=', round(mean(r[45][0] for r in improved), 3), 'mean60=', round(mean(r[60][0] for r in improved), 3))
            for row in sorted(improved, key=lambda r: r[60][0] - r[45][0], reverse=True)[:8]:
                c = row['candidate']
                print(c.generated_at_utc.isoformat(), c.side, 'R45=', round(row[45][0], 2), 'R60=', round(row[60][0], 2),
                      'MFE60=', round(row[60][2], 2), 'MAE60=', round(row[60][3], 2))


if __name__ == '__main__':
    main()
