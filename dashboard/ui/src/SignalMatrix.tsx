import {useEffect,useMemo,useState} from "react";
import type {CSSProperties} from "react";
import type {MatrixCell,SignalMatrixResponse,ShadowScoreResponse} from "./types";

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
 const [shadow,setShadow]=useState<ShadowScoreResponse|null>(null);
 const [selected,setSelected]=useState<{strategy:string;cell:MatrixCell}|null>(null);
 const [pickedRow,setPickedRow]=useState<string>("btc_squeeze_breakout_m15");
 useEffect(()=>{
  let cancelled=false;
  async function poll(){
   try{
    const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),5000);
    try{
     const results=await Promise.allSettled([
      fetch("/api/signal-matrix",{cache:"no-store",signal:controller.signal}).then(async r=>{
       if(!r.ok)throw new Error("SIGNAL_MATRIX_UNAVAILABLE");
       return await r.json() as SignalMatrixResponse;
      }),
      fetch("/api/shadow-scores",{cache:"no-store",signal:controller.signal}).then(async r=>{
       if(!r.ok)throw new Error("SHADOW_SCORES_UNAVAILABLE");
       return await r.json() as ShadowScoreResponse;
      }),
     ]);
     if(!cancelled){
      setData(results[0].status==="fulfilled"?results[0].value:null);
      setShadow(results[1].status==="fulfilled"?results[1].value:null);
     }
    }finally{clearTimeout(timeout)}
   }catch{if(!cancelled){setData(null);setShadow(null)}}
  }
  void poll();
  const interval=setInterval(()=>void poll(),5000);
  return()=>{cancelled=true;clearInterval(interval)};
 },[]);
 const cols=data?.columns||fallbackColumns.map(id=>({id,label:id,description:id}));
 const quantCols=cols.filter(c=>["RSI","EMA","ADX","VOL"].includes(c.id));
 const actionCols=cols.filter(c=>!["RSI","EMA","ADX","VOL"].includes(c.id));
 const rows=useMemo(()=>strategies.map(([id,name,tf,symbol])=>{
  const item=data?.matrices.find(m=>m.symbol===symbol)?.rows.find(r=>r.strategy_id===id);
  const research=shadow?.scores.find(s=>s.symbol===symbol)?.rows.find(r=>r.strategy_id===id);
  return {id,label:item?.label||name,tf:item?.timeframe||tf,symbol,enabled:item?.enabled??false, research,
   cells:item?.cells||cols.map(c=>({indicator:c.id,state:"UNKNOWN" as const,
    met:false,value:null,core:false,closed_utc:null,rule:"Waiting for closed-candle evidence"}))};
 }),[data,shadow,cols.length]);
 const details=selected;
 const focus=rows.find(r=>r.id===pickedRow);
 const focusResearch=focus?.research;
 const focusScenario=focusResearch?.scenarios.find(s=>s.direction===focusResearch.best_direction);
 return <section className="lc-panel lc-matrix-panel">
  <div className="lc-panel-title"><span>03 / INDICATOR SIGNAL MATRIX</span><span>8 × {cols.length} · SHADOW {shadow?.threshold??75}/100</span></div>
  <div className="lc-matrix-scroll">
   <div className="lc-matrix-clusters">
    <div className="lc-cluster lc-cluster-quant">
     <div className="lc-cluster-label">QUANT DYNAMICS</div>
     <div className="lc-matrix-table" role="table" aria-label="Quant indicator diagnostic matrix" style={{"--indicator-count":quantCols.length} as CSSProperties}>
      <div className="lc-matrix-head" role="row"><span role="columnheader">STRATEGY / SCORE</span>
       {quantCols.map(c=><span key={c.id} role="columnheader" title={c.description}>{c.label}</span>)}
      </div>
      {rows.map((row,index)=><div className={row.enabled?"lc-matrix-row enabled":"lc-matrix-row"} role="row" key={row.id}>
       <span className="lc-matrix-strategy" role="rowheader">
        <button className="lc-shadow-row" type="button" onClick={()=>setPickedRow(row.id)} title={`${row.label} · ${row.research?.verdict||"NO AUDIT"}`}>
         <b>{String(index+1).padStart(2,"0")} · {row.label}</b>
         <strong className={row.research?.verdict==="SHADOW_THRESHOLD_MET"?"lc-shadow-high":""}>{row.research?.best_score??"—"}</strong>
        </button>
        <small>{row.symbol.startsWith("BTC")?"BTC":"XAU"} · {row.tf} · {row.enabled?"ON":"OFF"}</small>
       </span>
       {quantCols.map(c=>{const cell=row.cells.find(v=>v.indicator===c.id);return cell?<button key={c.id} type="button" role="cell"
          className={`lc-matrix-cell ${cell.state.toLowerCase()} ${cell.met?"lit":""} ${cell.core?"core":""}`}
          title={`${row.label} · ${cell.indicator}: ${cell.state}\n${cell.rule}\n${cell.value??"NO DATA"} · ${cell.closed_utc||"NO CLOSED BAR"}`}
          aria-label={`${row.label} ${cell.indicator}: ${cell.state}`}
          onClick={()=>{setSelected({strategy:row.label,cell});setPickedRow(row.id)}}/>:null})}
      </div>)}
     </div>
    </div>
    <div className="lc-cluster-sep"/>
    <div className="lc-cluster lc-cluster-action">
     <div className="lc-cluster-label">PRICE ACTION</div>
     <div className="lc-matrix-table" role="table" aria-label="Price action indicator diagnostic matrix" style={{"--indicator-count":actionCols.length} as CSSProperties}>
      <div className="lc-matrix-head" role="row"><span role="columnheader">#</span>
       {actionCols.map(c=><span key={c.id} role="columnheader" title={c.description}>{c.label==="STRUCT"?"STR":c.label}</span>)}
      </div>
      {rows.map((row,index)=><div className={row.enabled?"lc-matrix-row enabled":"lc-matrix-row"} role="row" key={row.id}>
       <span className="lc-matrix-number" role="rowheader" title={row.label}>{String(index+1).padStart(2,"0")}</span>
       {actionCols.map(c=>{const cell=row.cells.find(v=>v.indicator===c.id);return cell?<button key={c.id} type="button" role="cell"
          className={`lc-matrix-cell ${cell.state.toLowerCase()} ${cell.met?"lit":""} ${cell.core?"core":""}`}
          title={`${row.label} · ${cell.indicator}: ${cell.state}\n${cell.rule}\n${cell.value??"NO DATA"} · ${cell.closed_utc||"NO CLOSED BAR"}`}
          aria-label={`${row.label} ${cell.indicator}: ${cell.state}`}
          onClick={()=>{setSelected({strategy:row.label,cell});setPickedRow(row.id)}}/>:null})}
      </div>)}
     </div>
    </div>
   </div>
  </div>
  <div className="lc-shadow-inspector" role="status" aria-label="Shadow-only score breakdown">
   <div className="lc-shadow-info">
    <strong>SHADOW / {focus?.label||"SELECT STRATEGY"}</strong>
    <span>{focusResearch?.best_score==null?"—":focusResearch.best_score} / 100 · {focusResearch?.best_direction||"—"} · {focusResearch?.verdict||"NO AUDIT"}</span>
   </div>
   <div className="lc-shadow-components">
    {(focusScenario?.components||[]).map(part=><span key={part.id} title={`${part.id}: ${part.points}/${part.max} · available ${part.available}`}>
     <b>{part.id.toUpperCase()}</b> {part.points}/{part.max}
    </span>)}
    {!focusScenario&&<span>NO FRESH SCORING EVIDENCE</span>}
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
