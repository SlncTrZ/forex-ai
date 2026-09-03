#!/usr/bin/env python3
from __future__ import annotations

import argparse, json
from pathlib import Path

from forex_ai.config import load_runtime_config
from forex_ai.mt5.client import MT5Client


def main() -> int:
    p=argparse.ArgumentParser(); p.add_argument('--symbol',required=True); p.add_argument('--timeframe',required=True,choices=('M15','H1','H4')); p.add_argument('--start-pos',type=int,default=0); p.add_argument('--count',type=int,default=1000); p.add_argument('--output',required=True); a=p.parse_args()
    cfg=load_runtime_config(); c=MT5Client(cfg)
    if not c.connect(): raise RuntimeError('MT5_CONNECT_FAILED')
    try:
        names={str(r.get('name')) for r in c.symbols()}; actual=a.symbol if a.symbol in names else None
        if actual is None:
            matches=sorted(n for n in names if n.startswith(a.symbol))
            if len(matches)!=1: raise RuntimeError(f'SYMBOL_MAPPING_UNRESOLVED:{a.symbol}:{matches}')
            actual=matches[0]
        tf=c.constants()[a.timeframe]; rows=c.bars(actual,tf,a.count,start_pos=a.start_pos)
        out=Path(a.output).expanduser(); existing=[]
        if out.exists(): existing=json.loads(out.read_text(encoding='utf-8'))
        merged={int(r['time']):r for r in existing}
        merged.update({int(r['time']):r for r in rows})
        data=[merged[k] for k in sorted(merged)]
        out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(data,separators=(',',':'))+'\n',encoding='utf-8')
        print(f'symbol={actual} timeframe={a.timeframe} fetched={len(rows)} total={len(data)} first={data[0]["time"] if data else None} last={data[-1]["time"] if data else None}')
        return 0
    finally: c.close()

if __name__=='__main__': raise SystemExit(main())
