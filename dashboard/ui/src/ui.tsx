import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as echarts from "echarts/core";
import { LineChart, PieChart, BarChart } from "echarts/charts";
import { GridComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
echarts.use([LineChart, PieChart, BarChart, GridComponent, TooltipComponent, CanvasRenderer]);
import { Activity, AlertTriangle, ArrowDownRight, ArrowUpRight, Blocks, Check, ChevronDown, CircleHelp, Command, Cpu, GitBranch, Layers3, LockKeyhole, Maximize2, Network, Radio, RefreshCw, Search, ShieldCheck, ShieldOff, Terminal, Wifi, X, Zap } from "lucide-react";
import { GraphCanvas } from "./GraphCanvas";
import type { GraphNode, GraphResponse, OperatorEvent, Quote, StatusPayload, RegimeResponse } from "./types";

const COLORS=["#7effca","#41a7a0","#b7f883","#e1b666","#ed6875","#6d91bf","#a78bdc","#337966"];
const fmt=(n:number|null|undefined)=>n==null?"—":new Intl.NumberFormat("en-US",{maximumFractionDigits:1}).format(n);
const money=(n:number|null|undefined,unit="USD")=>n==null?"—":unit==="USC"?new Intl.NumberFormat("en-US",{maximumFractionDigits:2}).format(Number(n))+" USC":new Intl.NumberFormat("en-US",{style:"currency",currency:unit||"USD",maximumFractionDigits:2}).format(Number(n));
const time=(s:string|undefined)=>s?new Date(s).toLocaleTimeString("en-GB",{timeZone:"UTC",hour:"2-digit",minute:"2-digit",second:"2-digit"}):"—";
const fetchJson=async<T,>(url:string):Promise<T>=>{const c=new AbortController();const deadline=setTimeout(()=>c.abort(),8000);try{const r=await fetch(url,{signal:c.signal,cache:"no-store"});if(!r.ok)throw new Error("HTTP "+r.status);return await r.json() as T;}finally{clearTimeout(deadline)}};
const PiePalette=COLORS;
function Chart({option,height=145}:{option:Record<string,unknown>;height?:number}){
 const host=useRef<HTMLDivElement>(null),chart=useRef<echarts.ECharts|null>(null);
 useEffect(()=>{
   if(!host.current)return;
   const inst=echarts.init(host.current,undefined,{renderer:"canvas",devicePixelRatio:Math.min(window.devicePixelRatio||1,2)});
   chart.current=inst;
   const observer=new ResizeObserver(()=>inst.resize());
   observer.observe(host.current);
   return()=>{observer.disconnect();inst.dispose();chart.current=null;};
 },[]);
 useEffect(()=>{chart.current?.setOption(option,{notMerge:true,lazyUpdate:true});},[option]);
 return <div ref={host} style={{height,width:"100%"}}/>;
}
const grid={left:8,right:8,top:12,bottom:8,containLabel:false};
const lineStyle={color:"#73f4b9",width:2,shadowColor:"rgba(92,255,181,.22)",shadowBlur:6};
function sparkOption(values:number[]):Record<string,unknown>{
 return {animationDuration:280,backgroundColor:"transparent",grid,
   tooltip:{trigger:"axis",backgroundColor:"#0b1d1c",borderColor:"#276b56",textStyle:{color:"#d1f7e9",fontFamily:"monospace",fontSize:10}},
   xAxis:{type:"category",boundaryGap:false,data:values.map((_,i)=>i),show:false},
   yAxis:{type:"value",scale:true,show:false},
   series:[{type:"line",data:values,symbol:"none",smooth:.27,lineStyle,
     areaStyle:{color:{type:"linear",x:0,y:0,x2:0,y2:1,colorStops:[{offset:0,color:"rgba(83,251,174,.27)"},{offset:1,color:"rgba(23,77,67,0)"}]}}}]};
}
function donutOption(data:{name:string;value:number}[]):Record<string,unknown>{
 return {backgroundColor:"transparent",color:PiePalette,tooltip:{trigger:"item",backgroundColor:"#0b1d1c",borderColor:"#276b56",textStyle:{color:"#d1f7e9",fontSize:10}},
   series:[{type:"pie",radius:["64%","83%"],center:["50%","50%"],avoidLabelOverlap:true,label:{show:false},
     itemStyle:{borderColor:"#0b1414",borderWidth:3},data,animationDuration:500}]};
}
function paretoOption(data:{name:string;value:number}[]):Record<string,unknown>{
 return {color:["#62d9aa"],animationDuration:500,grid:{left:6,right:15,top:5,bottom:8,containLabel:true},
   tooltip:{trigger:"axis",axisPointer:{type:"shadow"},backgroundColor:"#0b1d1c",borderColor:"#276b56",textStyle:{color:"#d1f7e9",fontSize:10}},
   xAxis:{type:"value",axisLabel:{color:"#557e78",fontSize:9},splitLine:{lineStyle:{color:"#213b37",type:"dashed"}}},
   yAxis:{type:"category",data:data.map(d=>d.name),axisLabel:{color:"#8faeaa",fontSize:9,overflow:"truncate",width:115},axisLine:{show:false},axisTick:{show:false}},
   series:[{type:"bar",barMaxWidth:12,data:data.map(d=>d.value),itemStyle:{borderRadius:[0,3,3,0]}}]};
}
function Panel({title,right,children,className=""}:{title:string;right?:React.ReactNode;children:React.ReactNode;className?:string}){
 return <section className={`frame ${className}`}><div className="panel-title"><span className="panel-heading"><span className="panel-mini-marker"/>{title}</span>{right}</div>{children}</section>;
}
function StatusDot({ok}:{ok:boolean}){return <span className={`status-dot ${ok?"online":"offline"}`}/>;}
function TinyMetric({label,value,sub,critical=false}:{label:string;value:string;sub?:string;critical?:boolean}){
 return <div className="top-metric"><div className="top-metric-head">{label}<span className="metric-rule"/></div><div className={`top-metric-value ${critical?"text-coral":"text-glow"}`}>{value}</div><div className="top-metric-sub">{sub||"VERIFIED JOURNAL"}</div></div>;
}
function useLiveData(){
 const [status,setStatus]=useState<StatusPayload|null>(null);
 const [graph,setGraph]=useState<GraphResponse|null>(null);
 const [orders,setOrders]=useState<{orders:{state:string;symbol:string;side:string;volume:string;updated_at_utc:string}[]} | null>(null);
 const [events,setEvents]=useState<OperatorEvent[]>([]);
 const [regimes,setRegimes]=useState<RegimeResponse|null>(null);
 const [quote,setQuote]=useState<Quote|null>(null);
 const [mid,setMid]=useState<number[]>([]);
 const [feedState,setFeedState]=useState("CONNECTING");
 const [streamConnected,setStreamConnected]=useState(false);
 const [error,setError]=useState("");
 const [ageTick,setAgeTick]=useState(0);
 const [refreshVersion,setRefreshVersion]=useState(0);
 const tickSeq=useRef(0),graphIds=useRef("");
 const updateGraph=useCallback(async()=>{
    try{const x=await fetchJson<GraphResponse>("/api/graph?limit=110");
      if(x.schema_version!==1||x.source!=="forex_ai_sqlite_journal")throw new Error("INVALID_GRAPH_SOURCE");
      const fingerprint=x.nodes.map(n=>n.id).join("|")+"#"+x.edges.map(e=>e.id).join("|");
      if(fingerprint!==graphIds.current){graphIds.current=fingerprint;setGraph(x);}
    }catch(e){setError("GRAPH DATA UNAVAILABLE");}
 },[]);
 const refresh=useCallback(async()=>{
  const results=await Promise.allSettled([
   fetchJson<StatusPayload>("/api/status"),
   fetchJson<{orders:{state:string;symbol:string;side:string;volume:string;updated_at_utc:string}[]}>("/api/orders"),
   fetchJson<{events:OperatorEvent[]}>("/api/events?source=system&limit=15"),
   fetchJson<RegimeResponse>("/api/regimes"),
  ]);
  if(results[0].status==="fulfilled"){setStatus(results[0].value);setError("");}
  else setError("RUNTIME TELEMETRY UNAVAILABLE");
  if(results[1].status==="fulfilled")setOrders(results[1].value);
  if(results[2].status==="fulfilled")setEvents(results[2].value.events);
  if(results[3].status==="fulfilled"&&results[3].value.source==="forex_ai_regime_audit")setRegimes(results[3].value);
  else setRegimes(null);
 },[]);
 useEffect(()=>{void refresh();void updateGraph();
  const a=setInterval(()=>void refresh(),12000),b=setInterval(()=>void updateGraph(),18000),
    c=setInterval(()=>setAgeTick(x=>x+1),1000);
  return()=>{clearInterval(a);clearInterval(b);clearInterval(c)};
 },[refresh,updateGraph,refreshVersion]);
 useEffect(()=>{
  const source=new EventSource("/api/stream/ticks");
  const accept=(e:MessageEvent)=>{try{const t=JSON.parse(e.data) as Quote;if(t.id&&t.id<=tickSeq.current)return;
      tickSeq.current=t.id||tickSeq.current;setQuote(t);
      setMid(series=>[...series.slice(-90),(Number(t.bid)+Number(t.ask))/2]);
  }catch{ /* Malformed broker frame is discarded */ }};
  source.onopen=()=>setStreamConnected(true);
  source.onerror=()=>{setStreamConnected(false);setFeedState("RECONNECTING");};
  source.addEventListener("tick",accept);
  source.addEventListener("snapshot",(e:MessageEvent)=>{try{const s=JSON.parse(e.data);setFeedState(s.stream_state||"CONNECTING");
     if(s.latest_id<tickSeq.current)tickSeq.current=0;
     for(const t of (s.quotes||[]) as Quote[])if(t.type==="tick")accept({data:JSON.stringify(t)} as MessageEvent);
   }catch{/* no fake recovery */}});
  source.addEventListener("health",(e:MessageEvent)=>{try{setFeedState(JSON.parse(e.data).state)}catch{}});
  source.addEventListener("gap",()=>{setFeedState("GAP DETECTED");});
  return()=>source.close();
 },[]);
 const triggerRefresh=()=>setRefreshVersion(v=>v+1);
 void ageTick;
 return {status,graph,orders,events,regimes,quote,mid,feedState,streamConnected,error,refresh:triggerRefresh};
}
type Action="arm"|"disarm"|"kill";
function ControlModal({action,close,updated}:{action:Action|null;close:()=>void;updated:()=>void}){
 const [confirmation,setConfirmation]=useState("");
 const [expected,setExpected]=useState("");
 const [message,setMessage]=useState("");
 const [busy,setBusy]=useState(false);
 const [ready,setReady]=useState(false);
 useEffect(()=>{setConfirmation("");setMessage("");setReady(false);
   if(!action)return;
   if(action==="arm"){fetchJson<{ready:boolean;account_type:string;reasons:string[]}>("/api/preflight").then(x=>{
     setExpected("ARM "+x.account_type);setReady(x.ready);if(!x.ready)setMessage("PREFLIGHT BLOCKED · "+(x.reasons||[]).join(", "));
   }).catch(()=>setMessage("BROKER PREFLIGHT UNAVAILABLE"));}
   else {setExpected(action==="disarm"?"DISARM":"EMERGENCY STOP");setReady(true);}
 },[action]);
 if(!action)return null;
 const handleSubmit=async()=>{if(confirmation!==expected||!ready||busy)return;setBusy(true);try{
   const body:Record<string,unknown>={confirmation};
   const result=await fetch("/api/control/"+action,{method:"POST",headers:{"Content-Type":"application/json","X-Forex-AI-Action":"operator-v1"},body:JSON.stringify(body)});
   const payload=await result.json();
   if(!result.ok)throw new Error(payload.error||"CONTROL_DENIED");
   updated();close();
 }catch(e){setMessage(String(e))}finally{setBusy(false)}};
 return <div className="modal-backdrop" role="presentation"><div className="modal-window" role="dialog" aria-modal="true" aria-label="Trading authorization">
   <div className="flex justify-between items-center"><div className="section-eyebrow">OPERATOR AUTHORIZATION // GUARDRAILS</div><button className="icon-btn" onClick={close} aria-label="Close"><X size={15}/></button></div>
   <h2 className="modal-heading">{action==="arm"?"AUTHORIZE TRADING":action==="disarm"?"DISARM EXECUTION":"EMERGENCY KILL SWITCH"}</h2>
   <p className="muted-copy">{action==="arm"?"ARM remains active until you DISARM or KILL. Fresh broker/risk preflight is revalidated at submission.":action==="kill"?"New entries are blocked. This does not close existing positions. An offline reset is required.":"Stop new entries; preserve any existing emergency safety lock."}</p>
   <div className="control-guide">Type <strong>{expected||"CHECKING…"}</strong> to confirm. No action is sent before explicit confirmation.</div>
   <input className="confirmation-input" value={confirmation} onChange={e=>setConfirmation(e.target.value)} placeholder="EXACT CONFIRMATION" spellCheck={false}/>
   {message&&<div className="error-line">{message}</div>}
   <div className="flex justify-end gap-3 mt-5"><button className="flat-btn" onClick={close}>CANCEL</button><button className={`flat-btn ${action==="kill"?"danger-btn":"primary-btn"}`} disabled={busy||!ready||confirmation!==expected} onClick={()=>void handleSubmit()}>{busy?"VERIFYING…":"CONFIRM CONTROL"}</button></div>
 </div></div>;
}
export function CommandCenter(){
 const {status,graph,orders,events,regimes,quote,mid,feedState,streamConnected,error,refresh}=useLiveData();
 const [selected,setSelected]=useState<GraphNode|null>(null);
 const [filter,setFilter]=useState<string>("ALL");
 const [action,setAction]=useState<Action|null>(null);
 const [clock,setClock]=useState(Date.now());
 useEffect(()=>{const i=setInterval(()=>setClock(Date.now()),1000);return()=>clearInterval(i)},[]);
 const isLive=streamConnected&&feedState==="CONNECTED"&&quote!=null&&clock-quote.broker_time_ms<5000;
 const filtered=useMemo(()=>{
  if(!graph)return null;
  if(filter==="ALL")return graph;
  // Keep each selected node's upstream provenance, not isolated floating dots.
  const seen=new Set(graph.nodes.filter(n=>n.type===filter).map(n=>n.id));
  const inputs=new Map<string,string[]>();
  for(const e of graph.edges){const p=inputs.get(e.target)||[];p.push(e.source);inputs.set(e.target,p);}
  const frontier=[...seen];
  while(frontier.length){const current=frontier.pop()!;
    for(const upstream of inputs.get(current)||[]){if(!seen.has(upstream)){seen.add(upstream);frontier.push(upstream);}}
  }
  const nodes=graph.nodes.filter(n=>seen.has(n.id));
  return {...graph,nodes,edges:graph.edges.filter(e=>seen.has(e.source)&&seen.has(e.target))};
 },[graph,filter]);
 const kinds=["ALL","strategy","candidate","risk","intent","execution"];
 const strategyEntries=(graph?.summary.strategies||[]).slice(0,7);
 const donut=strategyEntries.map((r)=>({name:r.id,value:r.count}));
 const pareto=[...strategyEntries].reverse().map(x=>({name:x.id.replace(/_v\d+$/,"").replace("supertrend_","ST_").slice(0,18),value:x.count}));
 const total=graph?.scope.total_candidates;
 const riskRate=graph?.summary.visible_risk_approval_rate;
 const account=status?.account;
 const armed=!!status?.control.entries_allowed_by_control && !!status?.control.execution_enabled;
 const staleQuoteMs=quote?Math.max(0,clock-quote.broker_time_ms):null;
 const cueLabels=["STRATEGY","CANDIDATE","RISK","ORDER INTENT","EXECUTION"];
 const cueTypes=["strategy","candidate","risk","intent","execution"];
 const stages=cueTypes.map(type=>graph?.nodes.some(n=>n.type===type)??false);
 const statusLine=feedState==="CONNECTED"?"DIRECT MT5 MARKET FEED":feedState;
 return <div className="command-shell">
  <header className="header-bar">
   <div className="brand-area"><div className="brand-icon"><Network size={22} strokeWidth={1.6}/></div>
     <div><div className="brand-name">FOREX<span>·</span>AI <span className="brand-suffix">// COMMAND CENTER</span></div><div className="brand-sub">AUTONOMOUS TRADING INTELLIGENCE <span className="green">//</span> OPERATOR EDITION 02</div></div></div>
   <div className="header-right">
    <div className="header-signal"><StatusDot ok={!!status?.health.healthy}/><span>SYSTEM {status?.health.healthy?"NOMINAL":"DEGRADED"}</span></div>
    <div className="header-signal hidden sm:flex"><StatusDot ok={isLive}/><span>MARKET {isLive?"LIVE":"STALE"}</span></div>
    <div className="header-clock">{new Date(clock).toLocaleTimeString("en-GB",{timeZone:"UTC",hour12:false})}<span>UTC</span></div>
    <button className="icon-btn" onClick={refresh} aria-label="Refresh telemetry"><RefreshCw size={15}/></button>
   </div>
  </header>
  <div className="system-ticker"><div className="ticker-l"><span className="signal-square"/><span>OPS / COMMAND</span><i/> <span>NODE .227</span><i/><span>PRIVATE SSH</span></div>
    <div className="ticker-r"><span className="ticker-status">{error||statusLine}</span><span className="cursor-blink">■</span></div></div>
  <main className="work-area">
   <section className="metrics-ribbon">
    <TinyMetric label="GRAPH NODES" value={fmt(graph?.summary.visible_nodes)} sub={graph?"VERIFIED · VISIBLE SAMPLE":"AWAITING JOURNAL"}/>
    <TinyMetric label="GRAPH EDGES" value={fmt(graph?.summary.visible_edges)} sub="DIRECTED RELATIONSHIPS"/>
    <TinyMetric label="CANDIDATE EVENTS" value={fmt(total)} sub="HISTORICAL JOURNAL"/>
    <TinyMetric label="RISK APPROVAL" value={riskRate==null?"—":riskRate+"%"} sub="LATEST RISK / SAMPLE"/>
    <TinyMetric label="ACCOUNT EQUITY" value={money(account?.financials?.equity,account?.financials?.currency)} sub={account?.type||"NO BROKER SNAPSHOT"}/>
    <TinyMetric label="EXECUTION GATE" value={armed?"ARMED":"BLOCKED"} sub={status?.control.kill_switch?"KILL SWITCH ACTIVE":status?.control.reason||"WAITING FOR STATE"} critical={!armed}/>
   </section>
   <div className="core-layout">
    <section className="frame network-panel">
     <div className="network-toolbar">
      <div><div className="section-eyebrow">01 / EXECUTION TOPOLOGY <span className="tiny-divider"/> VERIFIED JOURNAL</div><h1 className="core-title">The Decision <em>Network</em><span className="title-plus"> +</span></h1><p className="toolbar-note">STRATEGY → SIGNAL → RISK → INTENT → EXECUTION <span className="text-mint">/ WEBGL GRAPH</span></p></div>
      <div className="network-toolbar-right"><span className="readout-tag"><Radio size={12}/> {graph?"JOURNAL SYNCED":"SYNCING"}</span><button className="square-icon" aria-label="Refresh graph" onClick={refresh}><Maximize2 size={14}/></button></div>
     </div>
     <div className="graph-tools"><div className="graph-filters">{kinds.map(k=><button key={k} onClick={()=>{setFilter(k);setSelected(null)}} className={`graph-filter ${filter===k?"active":""}`}>{k==="ALL"?"ALL NODES":k.toUpperCase()}</button>)}</div><div className="graph-count">{filtered?.nodes.length||0} NODES <span>/</span> {filtered?.edges.length||0} LINKS</div></div>
     <div className="graph-body"><GraphCanvas data={filtered} selected={selected?.id||null} onSelect={setSelected}/>
      <div className="graph-coordinates">X: 0.000 <span>Y: 0.000</span> | ORBITAL VIEW / DIRECTED LINKS</div>
      <div className="graph-legend">{Object.entries({strategy:"#8bffcc",candidate:"#4d9d92",risk:"#edb95f",intent:"#ff6879",execution:"#cfa4ff"}).map(([label,color])=><span key={label}><i style={{background:color}}/>{label.toUpperCase()}</span>)}</div>
      {selected&&<div className="node-inspector"><button onClick={()=>setSelected(null)} aria-label="Close node details"><X size={12}/></button><div className="inspect-head">NODE INSPECTOR <span>/ {selected.type.toUpperCase()}</span></div><strong>{selected.label}</strong><small>{selected.id}</small><div className="inspect-fields">{Object.entries(selected).filter(([k])=>!["id","label","type","reference"].includes(k)).slice(0,5).map(([k,v])=><div key={k}><span>{k}</span><b>{Array.isArray(v)?v.join(" · "):String(v??"—")}</b></div>)}</div></div>}
     </div>
     <div className="graph-footer"><span><StatusDot ok={!!graph}/> {graph?"TOPOLOGY GENERATED FROM SQL JOURNAL":"AWAITING GRAPH"}</span><span>LIMIT {graph?.scope.limit||110} CANDIDATES · READ-ONLY</span><span>CLICK ANY NODE TO INSPECT</span></div>
    </section>
    <aside className="side-stack">
     <Panel title="01 / MARKET LOAD" right={<span className={`dot-label ${isLive?"":"off"}`}>{isLive?"LIVE":"STALE"}</span>} className="load-panel">
      <div className="load-top"><div><span className="tiny-label">LATEST BID</span><strong className="big-quote">{quote?quote.bid.toFixed(3):"—"}</strong><span className="subreadout">ASK <b>{quote?quote.ask.toFixed(3):"—"}</b> <i/> SPRD <b>{quote?quote.spread.toFixed(3):"—"}</b></span></div>
        <div className="circle-meter"><span>{isLive?"LIVE":"WAIT"}</span><small>TICK FEED</small></div></div>
      <Chart option={sparkOption(mid)} height={72}/>
      <div className="bottom-scale"><span>TICK AGE <b>{staleQuoteMs==null?"—":fmt(staleQuoteMs)+" ms"}</b></span><span>INGEST <b>{quote?fmt(quote.age_at_receive_ms)+" ms":"—"}</b></span></div>
     </Panel>
     <Panel title="02 / PIPELINE COMPOSITION" right={<span className="tiny-label">GRAPH SAMPLE</span>} className="compute-panel">
       <div className="stage-bars">{cueLabels.map((label,i)=>{const n=graph?.nodes.filter(x=>x.type===cueTypes[i]).length||0;const mx=Math.max(1,...cueTypes.map(k=>graph?.nodes.filter(x=>x.type===k).length||0));return <div className="stage-bar-row" key={label}><span>{label}</span><div className="stage-bar-track"><i style={{width:`${100*n/mx}%`,background:COLORS[i]}}/></div><b>{n}</b></div>})}</div>
     </Panel>
     <Panel title="03 / CYCLE STATE" right={<span className="dot-label">JOURNAL</span>} className="cycle-panel">
      <div className="cycle-rail">{cueLabels.map((lab,i)=><div key={lab} className={`cycle-step ${stages[i]?"present":""}`}><span className="cycle-no">0{i+1}</span><span>{lab}</span><i>{stages[i]?"●":"○"}</i></div>)}</div>
      <div className="cycle-foot">STAGE PRESENCE IS JOURNAL-DERIVED</div>
     </Panel>
     <Panel title="04 / BROKER TELEMETRY" right={<Activity size={13} className="text-mint"/>} className="telemetry-panel">
       <div className="telemetry-table">
        <div><span>MT5 MODE</span><b>{account?.type||"—"}</b></div><div><span>ACCOUNT BINDING</span><b className={account?.bound?"text-mint":"text-coral"}>{account?.bound?"VERIFIED":"NOT VERIFIED"}</b></div>
        <div><span>HB AGE</span><b>{status?.health.heartbeat_age_seconds==null?"—":fmt(status.health.heartbeat_age_seconds)+"s"}</b></div>
        <div><span>POSITIONS</span><b>{fmt(status?.metrics.external_positions)}</b></div><div><span>DB INTEGRITY</span><b>{status?.health.db_integrity||"—"}</b></div>
       </div>
     </Panel>
     <Panel title="05 / ISOLATED EXECUTION" right={<LockKeyhole size={13} className="text-mint"/>} className="sandbox-panel">
      <div className="sandbox-grid">
       <div><span>ENVIRONMENT</span><strong>{account?.type||"UNKNOWN"}</strong></div>
       <div><span>AUTHORIZATION</span><strong className={armed?"text-mint":"text-coral"}>{armed?"ARMED":"BLOCKED"}</strong></div>
      </div>
      <div className="control-strip"><button onClick={()=>setAction("arm")} disabled={!status}><ShieldCheck size={12}/> ARM</button><button onClick={()=>setAction("disarm")} disabled={!status}><ShieldOff size={12}/> DISARM</button><button className="kill" disabled={!status} onClick={()=>setAction("kill")}><AlertTriangle size={12}/> KILL</button></div>
     </Panel>
     <Panel title="06 / DISPATCH QUEUE" right={<span className="tiny-label">{orders?.orders.length||0} RECENT</span>} className="dispatch-panel">
      <div className="dispatch-list">{(orders?.orders||[]).slice(0,4).map((o,i)=><div key={i} className="dispatch-row"><span className="dispatch-clock">{time(o.updated_at_utc)}</span><span className="dispatch-symbol">{o.symbol}</span><span className={`dispatch-side ${o.side==="BUY"?"buy":""}`}>{o.side}</span><span className="dispatch-state">{o.state}</span></div>)}
      {!orders?.orders.length&&<div className="no-records">NO RECENT ORDER INTENTS</div>}</div>
     </Panel>
    </aside>
   </div>
   <div className="bottom-layout">
     <Panel title="07 / SIGNAL DISTRIBUTION BY STRATEGY" right={<span className="tiny-label">ALL HISTORICAL CANDIDATES</span>} className="bottom-panel">
      <div className="donut-block"><div className="donut-holder"><Chart height={175} option={donutOption(donut)}/><div className="donut-center"><b>{fmt(total)}</b><span>CANDIDATES</span></div></div><div className="donut-legend">{strategyEntries.slice(0,5).map((r,i)=><div key={r.id}><span className="legend-dot" style={{background:COLORS[i]}}/><span title={r.id}>{r.id.replace(/_v\d+$/,"")}</span><b>{fmt(r.count)}</b></div>)}</div></div>
     </Panel>
     <Panel title="08 / EVALUATOR BENCHMARK" right={<span className="tiny-label">SAMPLE SCOPE</span>} className="bottom-panel">
      <div className="benchmark-main"><div><span className="tiny-label">LATEST RISK APPROVAL</span><strong>{riskRate==null?"—":riskRate+"%"}</strong><small>{graph?.scope.risk_evaluated_in_sample||0} risk decisions in visible sample</small></div>
       <div className="benchmark-split"><span className="passed" style={{width:`${riskRate||0}%`}}/><span className="blocked" style={{width:`${100-(riskRate||0)}%`}}/></div>
       <div className="benchmark-labels"><span><i className="passed-dot"/> APPROVED</span><span><i className="blocked-dot"/> BLOCKED</span></div>
       <p className="benchmark-disclaimer"><CircleHelp size={13}/> Before/after baseline is not yet instrumented. No simulated improvement percentages.</p></div>
     </Panel>
     <Panel title="09 / STRATEGY ACTIVITY PARETO" right={<span className="tiny-label">CANDIDATE COUNTS</span>} className="bottom-panel">
       <Chart option={paretoOption(pareto)} height={175}/>
       <div className="chart-note">SORTED BY HISTORICAL SIGNAL FREQUENCY · NOT PROFITABILITY</div>
     </Panel>
   </div>
   <Panel title="10 / D3 MARKET REGIME" right={<span className="tiny-label">CLOSED H1 · AUDIT-VERIFIED · READ-ONLY</span>} className="regime-panel">
    <div className="regime-grid">
     {(regimes?.regimes||[]).map(item=><div className="regime-item" key={item.symbol}>
      <div className="regime-top"><b>{item.symbol}</b><span className={item.age_seconds!=null&&item.age_seconds>10800?"text-coral":"text-mint"}>{item.age_seconds==null?"UNKNOWN AGE":item.age_seconds>10800?"STALE":"H1 VERIFIED"}</span></div>
      <strong className={item.regime==="UPTREND"?"text-mint":item.regime==="DOWNTREND"?"text-coral":""}>{item.regime}</strong>
      <div className="regime-meta">QUANT {item.quant_regime} · D3 {item.model_regime||"—"} · CONF {item.confidence==null?"—":Math.round(item.confidence*100)+"%"}</div>
      <div className="regime-meta">{item.reason_code} · {item.latency_ms}ms · {time(item.closed_h1_utc||undefined)} UTC</div>
      <div className="regime-meta" title={item.feature_fingerprint}>MODEL {item.model_fingerprint?.slice(0,12)} · FEATURE {item.feature_fingerprint?.slice(0,12)}</div>
     </div>)}
     {!regimes?.regimes.length&&<div className="no-records">NO D3 REGIME AUDIT YET — NOT A VERIFIED MARKET CLASSIFICATION</div>}
    </div>
   </Panel>
   <footer className="footer-line"><span>FOREX-AI // CYBERPUNK COMMAND CENTER V2</span><span>JOURNAL-VERIFIED // PRIVATE UNIX SOCKET // NO MOCK PRODUCTION DATA</span><span>RELEASE {status?.release.version||"—"}</span></footer>
  </main>
  {error&&<div className="error-toast"><AlertTriangle size={15}/>{error}</div>}
  <ControlModal action={action} close={()=>setAction(null)} updated={refresh}/>
 </div>;
}
