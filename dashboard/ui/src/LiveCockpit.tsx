import {useCallback,useEffect,useMemo,useRef,useState} from "react";
import {Activity,AlertTriangle,BarChart3,GitBranch,LockKeyhole,RefreshCw,ShieldCheck,ShieldOff,X,Zap} from "lucide-react";
import type {Quote,StatusPayload,RegimeDecision,ConfluenceDecision} from "./types";

type LiveStatus=StatusPayload & {
 account:StatusPayload["account"] & {financial_state?:string;financial_source?:string;financial_reason?:string};
 metrics:StatusPayload["metrics"] & {floating_pnl?:number|null;realized_pnl_utc_day?:number|null};
 health:StatusPayload["health"] & {heartbeat_state?:string;reasons:string[]};
};
type Watch={quote:Quote|null;history:Quote[]};
type Control="arm"|"disarm"|"kill";
const watchSymbols=["XAUUSDm","BTCUSDm"];
const cash=(v:number|null|undefined)=>v==null?"—":new Intl.NumberFormat("en-US",{style:"currency",currency:"USD",minimumFractionDigits:2}).format(v);
const nfmt=(v:number|null|undefined,dp=2)=>v==null?"—":Number(v).toLocaleString("en-US",{minimumFractionDigits:dp,maximumFractionDigits:dp});
const limit=(v:number)=>Math.max(0,Math.min(1,v));
const norm=(v:number)=>Math.max(-1,Math.min(1,v));
const nowDate=()=>new Date().toLocaleTimeString("en-GB",{hour12:false});
async function fetchData<T>(path:string):Promise<T>{const c=new AbortController(),timer=setTimeout(()=>c.abort(),6500);try{const r=await fetch(path,{cache:"no-store",signal:c.signal});if(!r.ok)throw Error("HTTP "+r.status);return await r.json() as T;}finally{clearTimeout(timer)}}

function PriceChart({history,symbol}:{history:Quote[];symbol:string}){
 const points=useMemo(()=>{
  const vals=history.map(q=>(q.bid+q.ask)/2);
  if(vals.length<2)return "";
  const low=Math.min(...vals),high=Math.max(...vals),span=high-low||.01;
  return vals.map((v,i)=>`${7+i/(vals.length-1)*486},${137-125*(v-low)/span}`).join(" ");
 },[history]);
 return <div className="lc-chart" role="img" aria-label={`Recent verified ${symbol} broker ticks`}>
   <svg viewBox="0 0 500 145" preserveAspectRatio="none">
    <path d="M0 35 H500 M0 75 H500 M0 115 H500" stroke="#203e38" strokeWidth=".5" fill="none"/>
    {points&&<polyline points={points} fill="none" stroke={symbol.startsWith("BTC")?"#72edc7":"#c1bb88"} strokeWidth="2.2" strokeLinejoin="round" strokeLinecap="round"/>}
   </svg>
   {!points&&<span className="lc-empty-chart">NO RECENT VERIFIED TICKS</span>}
 </div>
}
function Gauge({name,value,min=0,max=1,subtitle}:{name:string;value:number|null;min?:number;max?:number;subtitle:string}){
 const frac=value==null?0:limit((value-min)/(max-min));
 const formatted=value==null?"—":name==="REGIME LEVEL"?String(Math.round(value))+"/5":value.toFixed(2);
 return <div className="lc-gauge">
   <div className="lc-gauge-top"><strong>{name}</strong><b>{formatted}</b></div>
   <div className={min<0?"lc-track bipolar":"lc-track"}><div className="lc-track-mid"/>
     {value!=null&&<i style={min<0?{left:`${Math.min(frac,.5)*100}%`,width:`${Math.abs(frac-.5)*100}%`}:{left:0,width:`${frac*100}%`}}/>}
   </div>
   <small>{subtitle}</small>
 </div>
}
function Card({label,value,subtitle,negative}:{label:string;value:string;subtitle:string;negative?:boolean}){
 return <div className="lc-fin-card"><small>{label}</small><strong className={negative?"lc-danger":""}>{value}</strong><span>{subtitle}</span></div>;
}
function ControlDialog({action,onClose,onComplete}:{action:Control|null;onClose:()=>void;onComplete:()=>void}){
 const [expect,setExpect]=useState(""),[confirm,setConfirm]=useState(""),[error,setError]=useState(""),[ready,setReady]=useState(false),[busy,setBusy]=useState(false);
 useEffect(()=>{setConfirm("");setError("");setReady(false);
  if(!action)return;
  if(action==="arm"){void fetchData<{account_type:string;ready:boolean;reasons:string[]}>("/api/preflight").then(v=>{
   setExpect("ARM "+v.account_type);setReady(v.ready);if(!v.ready)setError(v.reasons.join(", ")||"PRECHECK BLOCKED");
  }).catch(()=>setError("BROKER PREFLIGHT UNAVAILABLE"));}
  else{setExpect(action==="kill"?"EMERGENCY STOP":"DISARM");setReady(true)}
 },[action]);
 if(!action)return null;
 const submit=async()=>{if(!ready||busy||confirm!==expect)return;setBusy(true);
  try{const response=await fetch("/api/control/"+action,{method:"POST",headers:{"Content-Type":"application/json","X-Forex-AI-Action":"operator-v1"},body:JSON.stringify({confirmation:confirm})});
   const res=await response.json();if(!response.ok)throw Error(res.error||"CONTROL_REJECTED");onComplete();onClose();
  }catch(e){setError(String(e))}finally{setBusy(false)}};
 return <div className="lc-overlay" role="presentation"><section className="lc-dialog" role="dialog" aria-modal="true" aria-label="Trading authorization">
  <div className="lc-panel-title">OWNER AUTHORIZATION <button onClick={onClose} aria-label="Close"><X size={16}/></button></div>
  <h2>{action.toUpperCase()}</h2><p>Type <strong>{expect||"CHECKING BROKER…"}</strong> to confirm. This action changes the persisted trading control only. No orders are sent by the browser.</p>
  <input value={confirm} onChange={e=>setConfirm(e.target.value)} spellCheck={false} placeholder="EXACT CONFIRMATION" />
  {error&&<p className="lc-danger">{error}</p>}
  <button disabled={!ready||confirm!==expect||busy} onClick={()=>void submit()}>{busy?"VERIFYING…":"CONFIRM"}</button>
 </section></div>;
}
export function LiveCockpit(){
 const [status,setStatus]=useState<LiveStatus|null>(null),[regimes,setRegimes]=useState<RegimeDecision[]>([]),
  [confluence,setConfluence]=useState<ConfluenceDecision[]>([]),[watch,setWatch]=useState<Record<string,Watch>>({}),
  [selected,setSelected]=useState("BTCUSDm"),[clock,setClock]=useState(Date.now()),[stream,setStream]=useState("CONNECTING"),
  [details,setDetails]=useState(false),[detailData,setDetailData]=useState<any>(null),
  [action,setAction]=useState<Control|null>(null),[error,setError]=useState("");
 const seq=useRef<Record<string,number>>({});
 const refresh=useCallback(async()=>{
  const [s,r,c]=await Promise.allSettled([
   fetchData<LiveStatus>("/api/status"),
   fetchData<{regimes:RegimeDecision[]}>("/api/regimes"),
   fetchData<{decisions:ConfluenceDecision[]}>("/api/confluence"),
  ]);
  if(s.status==="fulfilled"){setStatus(s.value);setError("")}else{setStatus(null);setError("STATUS UNAVAILABLE")}
  setRegimes(r.status==="fulfilled"?r.value.regimes:[]);
  setConfluence(c.status==="fulfilled"?c.value.decisions:[]);
 },[]);
 useEffect(()=>{void refresh();const poll=setInterval(()=>void refresh(),5000),timer=setInterval(()=>setClock(Date.now()),1000);
  return()=>{clearInterval(poll);clearInterval(timer)}},[refresh]);
 useEffect(()=>{const s=new EventSource("/api/stream/ticks");
  const accept=(v:Quote)=>{if(!watchSymbols.includes(v.symbol)||typeof v.broker_time_ms!=="number")return;
   if(v.id&&v.id<=(seq.current[v.symbol]||0))return;
   seq.current[v.symbol]=v.id||seq.current[v.symbol]||0;
   setWatch(old=>{const prev=old[v.symbol];return {...old,[v.symbol]:{
    quote:v,history:[...(prev?.history||[]).filter(q=>q.broker_time_ms!==v.broker_time_ms),v].slice(-160)
   }}})};
  s.onopen=()=>setStream("CONNECTED");s.onerror=()=>setStream("RECONNECTING");
  s.addEventListener("tick",(e:MessageEvent)=>{try{accept(JSON.parse(e.data) as Quote)}catch{}});
  s.addEventListener("snapshot",(e:MessageEvent)=>{try{const obj=JSON.parse(e.data);setStream(obj.stream_state||"CONNECTING");
   for(const q of (obj.quotes||[]) as Quote[])if(q.type==="tick")accept(q);
  }catch{}});
  s.addEventListener("gap",()=>setStream("DATA GAP"));
  s.addEventListener("health",(e:MessageEvent)=>{try{setStream(JSON.parse(e.data).state||"CONNECTED")}catch{}});
  return()=>s.close();
 },[]);
 const current=watch[selected]||{quote:null,history:[]};
 const quote=current.quote;
 const tickAge=quote?Math.max(0,(clock-quote.broker_time_ms)/1000):null;
 const tickLive=tickAge!=null&&tickAge<15;
 const marketClosed=selected==="XAUUSDm"&&!tickLive&&new Date(clock).getUTCDay()===6;
 const marketLabel=tickLive?"LIVE":marketClosed?"OFF SESSION":tickAge!=null?"STALE":"NO TICKS";
 const regime=regimes.find(v=>v.symbol===selected&&v.age_seconds!=null&&v.age_seconds<10800&&v.reason_code==="REGIME_CONFIRMED");
 const conf=regime?.confidence??null;
 // Regime 0-5 quantizes the actual D3 confidence, NOT five invented confirmations.
 const regimeLevel=conf==null?null:Math.min(5,Math.max(0,Math.round(conf*5)));
 const signal=useMemo(()=>{
  const h=current.history.map(q=>(q.bid+q.ask)/2);
  if(!tickLive||h.length<4)return {bull:null as number|null,momentum:null as number|null,volatility:null as number|null};
  const movements=h.slice(1).map((v,i)=>v-h[i]);
  const net=h[h.length-1]-h[0],path=movements.reduce((s,x)=>s+Math.abs(x),0);
  const bull=path>0?norm(net/path):0;
  const max=Math.max(...h),min=Math.min(...h);
  const spread=quote?.spread||0;
  // Observed price range vs its broker spread, not a probability.
  const vol=spread>0?limit((max-min)/(10*spread)):null;
  return {bull,momentum:Math.abs(bull),volatility:vol};
 },[current.history,tickLive,quote?.spread]);
 const financeOk=status?.account.financial_state==="LIVE"&&status.account.financials!=null;
 const acct=financeOk?status?.account.financials:null;
 const floating=financeOk?status?.metrics.floating_pnl:null,realized=financeOk?status?.metrics.realized_pnl_utc_day:null;
 const scanner=status?.services.find(s=>s.name==="candidate-scan"&&s.status==="failed");
 const scannerStatus=status?.services.find(s=>s.unit==="forex-ai-candidate-scan.service")?.status;
 const activeScanner=scannerStatus!=null&&scannerStatus!=="failed";
 const systemOk=Boolean(status?.health.healthy)&&!scanner;
 const entryOk=Boolean(status?.control.entries_allowed_by_control&&status.control.execution_enabled);
 const loadDetails=async()=>{setDetails(true);try{
  const [graph,orders]=await Promise.all([fetchData<any>("/api/graph?limit=55"),fetchData<any>("/api/orders")]);
  setDetailData({graph,orders});
 }catch{setDetailData({error:"DETAIL TELEMETRY UNAVAILABLE"})}};
 return <div className="lc-root">
  <header className="lc-header">
   <div className="lc-logo"><Zap size={20}/><div><strong>FOREX-AI <em>/ COMMAND CENTER</em></strong><small>LIVE TRADING COCKPIT · {status?.release.version||"—"}</small></div></div>
   <div className="lc-header-right">
    <span className={systemOk?"lc-chip ok":"lc-chip bad"}>● {systemOk?"SYSTEM ONLINE":"SYSTEM DEGRADED"}</span>
    <span className={stream==="CONNECTED"?"lc-chip ok":"lc-chip warn"}>● TICK LINK {stream}</span>
    <span className="lc-clock">{nowDate()}</span><button onClick={()=>void refresh()} aria-label="Refresh"><RefreshCw size={15}/></button>
   </div>
  </header>
  <section className="lc-finance">
   <Card label="BALANCE · MT5" value={cash(acct?.balance)} subtitle={financeOk?"BROKER VERIFIED":"BROKER DATA UNAVAILABLE"}/>
   <Card label="EQUITY · MT5" value={cash(acct?.equity)} subtitle={financeOk?"LIVE BROKER SNAPSHOT":"STALE — VALUE HIDDEN"}/>
   <Card label="FLOATING PnL" value={cash(floating)} subtitle="OPEN BROKER EXPOSURE" negative={floating!=null&&floating<0}/>
   <Card label="REALIZED PnL" value={cash(realized)} subtitle="UTC TODAY · TRADE DEALS" negative={realized!=null&&realized<0}/>
  </section>
  <main className="lc-main">
   <section className="lc-panel lc-market">
    <div className="lc-panel-title"><span>01 / BROKER MARKET STREAM</span><span>{stream==="CONNECTED"?"● CONNECTED":"● "+stream}</span></div>
    <nav className="lc-symbol-tabs">
     {watchSymbols.map(s=>{const q=watch[s]?.quote;const age=q?Math.max(0,(clock-q.broker_time_ms)/1000):null;
      const live=age!=null&&age<15;const off=s.startsWith("XAU")&&new Date(clock).getUTCDay()===6&&!live;
      return <button key={s} onClick={()=>setSelected(s)} className={selected===s?"selected":""}>
       <b>{s.replace("m","")}</b><span className={live?"lc-green":off?"lc-gold":"lc-red"}>{live?"● LIVE":off?"● OFF SESSION":"● STALE"}</span>
      </button>})}
    </nav>
    <div className="lc-quote">
     <div><small>{selected} · BID</small><strong>{quote?nfmt(quote.bid,selected.startsWith("BTC")?2:3):"—"}</strong></div>
     <div><small>ASK</small><strong>{quote?nfmt(quote.ask,selected.startsWith("BTC")?2:3):"—"}</strong></div>
     <div><small>SPREAD</small><strong>{quote?nfmt(quote.spread):"—"}</strong></div>
     <div><small>MARKET</small><strong className={tickLive?"lc-green":"lc-gold"}>{marketLabel}</strong></div>
    </div>
    <PriceChart history={current.history} symbol={selected}/>
    <div className="lc-market-foot"><span>{current.history.length} VERIFIED TICKS</span><span>LAST TICK {tickAge==null?"—":Math.round(tickAge)+"s AGO"}</span><span>MONITOR ONLY · BROKER DATA</span></div>
   </section>
   <section className="lc-panel lc-dynamics">
    <div className="lc-panel-title"><span>02 / QUANT + D3 DYNAMICS</span><span>READ ONLY</span></div>
    <div className="lc-regime-head"><div><small>CONFIRMED REGIME</small><strong>{regime?.regime||"UNAVAILABLE"}</strong></div><div><small>D3</small><strong>{regime?regime.model_regime:"NO FRESH AUDIT"}</strong></div></div>
    <div className="lc-gauges">
     <Gauge name="REGIME LEVEL" value={regimeLevel} max={5} subtitle="0–5 · QUANTIZED D3 CONFIDENCE"/>
     <Gauge name="CONFIDENCE" value={conf} subtitle="0.00–1.00 · LAST CLOSED H1"/>
     <Gauge name="BULL / BEAR" value={signal.bull} min={-1} max={1} subtitle="−1…+1 · VERIFIED TICK DIRECTION"/>
     <Gauge name="MOMENTUM" value={signal.momentum} subtitle="0–1 · TICK DIRECTIONAL EFFICIENCY"/>
     <Gauge name="VOLATILITY" value={signal.volatility} subtitle="0–1 · RANGE / 10× SPREAD"/>
    </div>
    <div className="lc-score-line"><span>LAST CONFLUENCE</span><b>{confluence.find(x=>x.symbol===selected)?.score??"—"} / 100</b><span>{confluence.find(x=>x.symbol===selected)?.verdict||"NO CANDIDATE"}</span></div>
   </section>
  </main>
  <section className="lc-bottom">
   <div className="lc-panel lc-health"><div className="lc-panel-title">03 / INFRASTRUCTURE HEALTH</div>
    <div className="lc-health-items">
     <span className="lc-green">● MT5 {status?.account.bound?"BOUND":"UNVERIFIED"}</span>
     <span className={activeScanner?"lc-green":"lc-red"}>● SCANNER {scannerStatus==="failed"?"FAILED":scannerStatus==="active"?"RUNNING":scannerStatus==="inactive"?"IDLE":"UNKNOWN"}</span>
     <span className={regimes.length?"lc-green":"lc-gold"}>● D3 {regimes.length?"AUDITED":"NO AUDIT"}</span>
     <span className={financeOk?"lc-green":"lc-red"}>● EQUITY {financeOk?"LIVE":"STALE"}</span>
     <span>● ACCOUNT {status?.account.type||"—"}</span>
    </div>
   </div>
   <div className="lc-panel lc-controls"><div className="lc-panel-title">04 / OWNER CONTROLS <span>{entryOk?"ARMED":"BLOCKED"}</span></div>
    <div className="lc-control-row">
     <button disabled={!status} onClick={()=>setAction("arm")}><ShieldCheck size={14}/> ARM</button>
     <button disabled={!status} onClick={()=>setAction("disarm")}><ShieldOff size={14}/> DISARM</button>
     <button disabled={!status} className="danger" onClick={()=>setAction("kill")}><AlertTriangle size={14}/> KILL</button>
     <button onClick={()=>void loadDetails()}><BarChart3 size={14}/> DETAILS</button>
    </div>
   </div>
  </section>
  <footer className="lc-footer"><span>v{status?.release.version?.replace(/^v/,"")||"—"} · PRIVATE SSH · READ-ONLY MARKET METRICS</span><span>{error||status?.health.reasons.join(" · ")||"AWAITING SYSTEM STATUS"}</span></footer>
  <ControlDialog action={action} onClose={()=>setAction(null)} onComplete={()=>void refresh()}/>
  {details&&<div className="lc-overlay"><section className="lc-detail" role="dialog" aria-modal="true" aria-label="Execution details">
   <div className="lc-panel-title">JOURNAL & EXECUTION DETAILS <button onClick={()=>setDetails(false)}><X size={16}/></button></div>
   <div className="lc-detail-grid"><div><h3>GRAPH PROVENANCE</h3><p>Nodes: {detailData?.graph?.summary?.visible_nodes??"—"} · Edges: {detailData?.graph?.summary?.visible_edges??"—"}</p>
   <pre>{JSON.stringify(detailData?.graph?.summary||{},null,2)}</pre></div>
   <div><h3>RECENT ORDER INTENTS</h3><pre>{JSON.stringify((detailData?.orders?.orders||[]).slice(0,10).map((o:any)=>({symbol:o.symbol,state:o.state,side:o.side,updated:o.updated_at_utc})),null,2)}</pre></div></div>
  </section></div>}
 </div>;
}
