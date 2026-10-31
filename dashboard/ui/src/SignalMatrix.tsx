import {useEffect,useMemo,useState} from "react";
import type {CSSProperties} from "react";
import type {MatrixCell,SignalMatrixResponse} from "./types";

const fallbackColumns=["RSI","EMA","ADX","ST","VOL","SQZ","STRUCT","TRG"];
const strategies=[
 ["btc_squeeze_breakout_m15","BTC Squeeze","M15","BTCUSDm"],
 ["supertrend_stochastic_m5","ST + Stoch","M5","XAUUSDm"],
 ["supertrend_volume_m5","ST + Volume","M5","XAUUSDm"],
 ["smc_pro_fvg_v2","SMC Pro FVG","M5","XAUUSDm"],
 ["keylevel_breakout_v2","Keylevel","M5","XAUUSDm"],
 ["mtf_trend_continuation_v2","MTF Trend","M5","XAUUSDm"],
 ["pro_volatility_expansion_v2","Vol Expansion","M5","XAUUSDm"],
 ["ichimoku_mtf_v2","Ichimoku MTF","M15","XAUUSDm"],
] as const;

export function SignalMatrix(){
 const [data,setData]=useState<SignalMatrixResponse|null>(null);
 const [selected,setSelected]=useState<{strategy:string;cell:MatrixCell}|null>(null);
 useEffect(()=>{
  let cancelled=false;
  async function poll(){
   try{
    const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),5000);
    try{
     const result=await fetch("/api/signal-matrix",{cache:"no-store",signal:controller.signal});
     if(!result.ok)throw new Error("SIGNAL_MATRIX_UNAVAILABLE");
     const next=await result.json() as SignalMatrixResponse;
     if(!cancelled)setData(next);
    }finally{clearTimeout(timeout)}
   }catch{if(!cancelled)setData(null)}
  }
  void poll();
  const interval=setInterval(()=>void poll(),5000);
  return()=>{cancelled=true;clearInterval(interval)};
 },[]);
 const cols=data?.columns||fallbackColumns.map(id=>({id,label:id,description:id}));
 const rows=useMemo(()=>strategies.map(([id,name,tf,symbol])=>{
  const item=data?.matrices.find(m=>m.symbol===symbol)?.rows.find(r=>r.strategy_id===id);
  return {id,label:item?.label||name,tf:item?.timeframe||tf,symbol,enabled:item?.enabled??false,
   cells:item?.cells||cols.map(c=>({indicator:c.id,state:"UNKNOWN" as const,
    met:false,value:null,core:false,closed_utc:null,rule:"Waiting for closed-candle evidence"}))};
 }),[data,cols.length]);
 const details=selected;
 return <section className="lc-panel lc-matrix-panel">
  <div className="lc-panel-title"><span>03 / INDICATOR SIGNAL MATRIX</span><span>8 × {cols.length} · DIAGNOSTIC</span></div>
  <div className="lc-matrix-scroll">
   <div className="lc-matrix-table" role="table" aria-label="Strategy indicator diagnostic matrix" style={{"--indicator-count":cols.length,minWidth:Math.max(480,168+cols.length*47)} as CSSProperties}>
    <div className="lc-matrix-head" role="row">
     <span role="columnheader">STRATEGY</span>
     {cols.map(c=><span key={c.id} role="columnheader" title={c.description}>{c.label}</span>)}
    </div>
    {rows.map(row=><div className={row.enabled?"lc-matrix-row enabled":"lc-matrix-row"} role="row" key={row.id}>
     <span className="lc-matrix-strategy" role="rowheader" title={row.id}>
      <b>{row.label}</b><small>{row.symbol.startsWith("BTC")?"BTC":"XAU"} · {row.tf} · {row.enabled?"ON":"OFF"}</small>
     </span>
     {row.cells.map((cell,i)=><button key={cell.indicator||i} type="button" role="cell"
      className={`lc-matrix-cell ${cell.state.toLowerCase()} ${cell.met?"lit":""} ${cell.core?"core":""}`}
      title={`${row.label} · ${cell.indicator} · ${cell.state}\n${cell.rule}\n${cell.value??"NO DATA"} · ${cell.closed_utc||"NO CLOSED BAR"}`}
      aria-label={`${row.label} ${cell.indicator}: ${cell.state}`}
      onClick={()=>setSelected({strategy:row.label,cell})} />)}
    </div>)}
   </div>
  </div>
  <div className="lc-matrix-footer">
   {details?<span title={details.cell.rule}>{details.strategy} · {details.cell.indicator}:
    <b> {details.cell.state}</b> · {details.cell.value??"—"} · {details.cell.rule}</span>
    :<span>● GREEN BULL · RED BEAR · GRAY UNKNOWN / STALE</span>}
   <span>CLOSED CANDLES</span>
  </div>
 </section>;
}
