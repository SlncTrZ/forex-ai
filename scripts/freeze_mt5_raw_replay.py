#!/usr/bin/env python3
from __future__ import annotations
import argparse,json
from datetime import datetime,timezone
from pathlib import Path
from forex_ai.research.dataset import freeze_replay_dataset
from forex_ai.research.mt5_dataset import build_replay_events_from_mt5_bars

def main()->int:
 p=argparse.ArgumentParser();p.add_argument('--symbol',required=True);p.add_argument('--point',type=float,required=True);p.add_argument('--m15',required=True);p.add_argument('--h1',required=True);p.add_argument('--h4',required=True);p.add_argument('--output',required=True);p.add_argument('--history-bars',type=int,default=60);a=p.parse_args()
 load=lambda x:json.loads(Path(x).read_text(encoding='utf-8'))
 m15,h1,h4=load(a.m15),load(a.h1),load(a.h4)
 events=build_replay_events_from_mt5_bars(symbol=a.symbol,point=a.point,m15_rows=m15,h1_rows=h1,h4_rows=h4,history_bars=a.history_bars)
 out=Path(a.output);man=freeze_replay_dataset(events,data_path=out,source_id=f'mt5-raw:{a.symbol}:m15={len(m15)}:h1={len(h1)}:h4={len(h4)}:history={a.history_bars}',created_at_utc=datetime.now(timezone.utc))
 print(f'records={man.record_count} first={man.first_clock_utc} last={man.last_clock_utc} sha256={man.dataset_sha256} event_fp={man.event_fingerprint}')
 return 0
if __name__=='__main__':raise SystemExit(main())
