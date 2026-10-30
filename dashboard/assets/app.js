(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const tabs = ['overview','trading','orders','activity','systems'];
  const headings = {overview:['System overview','Everything happening in your trading infrastructure, in one place.'],trading:['Trading control','Manage execution authority without interrupting monitoring or reconciliation.'],orders:['Orders & positions','Follow the actual broker execution lifecycle and unmanaged exposure.'],activity:['Activity & audit','Forensic event history, safety decisions and runtime messages.'],systems:['Infrastructure','Live service health, deployment fingerprint and operational safeguards.']};
  let state = null, tab = 'overview', pending = null, logs = [];
  const escapeText = (value) => String(value ?? '—');
  const text = (id, value) => { const e=$(id);if(e)e.textContent=escapeText(value); };
  const short = (value, n=80) => escapeText(value).slice(0,n);
  function when(v) {if(!v) return '—'; const d=new Date(v);return Number.isNaN(d.getTime())?short(v,24):d.toLocaleString('en-GB',{timeZone:'UTC',day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit'}); }
  const num = (n) => n == null || Number.isNaN(Number(n))?'—':Number(n).toLocaleString('en-US',{maximumFractionDigits:0});
  const money = (n,c) => n == null?'—':new Intl.NumberFormat('en-US',{style:'currency',currency:['USD','EUR','GBP','JPY','VND'].includes(c)?c:'USD',minimumFractionDigits:2,maximumFractionDigits:2}).format(Number(n));
  const setPill=(id,value,kind='neutral')=> {const e=$(id);e.className='pill '+kind;e.textContent=value;};
  const el = (tag,cls,content) => {const e=document.createElement(tag);if(cls)e.className=cls;if(content!=null)e.textContent=escapeText(content);return e;};
  const clear=(node)=> { while(node.firstChild) node.removeChild(node.firstChild); };
  async function req(url,options={}) {const ctrl=new AbortController();const timeout=setTimeout(()=>ctrl.abort(),10000); try{const r=await fetch(url,{cache:'no-store',credentials:'same-origin',signal:ctrl.signal,...options});let data=await r.json();if(!r.ok)throw new Error(data.error||'HTTP_'+r.status);return data;}finally{clearTimeout(timeout);}}
  function toast(message,bad=false){const e=$('toast');e.textContent=message;e.style.borderColor=bad?'#a04b59':'#2c6352';e.style.background=bad?'#45252f':'#153a31';e.classList.remove('hidden');setTimeout(()=>e.classList.add('hidden'),4500);}
  function showTab(next){if(!tabs.includes(next))return;tab=next;for(const t of tabs){$('tab-'+t).classList.toggle('hidden',t!==next);}document.querySelectorAll('[data-tab]').forEach(n=>n.classList.toggle('selected',n.dataset.tab===next));text('page-crumb',next==='overview'?'Overview':headings[next][0]);text('page-title',headings[next][0]);text('page-desc',headings[next][1]);if(next==='activity')loadLogs();if(next==='orders')loadOrders();}
  document.querySelectorAll('[data-tab]').forEach(b=>b.addEventListener('click',()=>showTab(b.dataset.tab)));
  document.querySelectorAll('[data-go]').forEach(b=>b.addEventListener('click',()=>showTab(b.dataset.go)));
  function healthClass(ok) {return ok?'success':'danger';}
  function update(data){
    state=data; $('connection-error').classList.add('hidden');
    const h=data.health||{},c=data.control||{},a=data.account||{},m=data.metrics||{},f=a.financials||{};
    text('hero-state',h.healthy?'Infrastructure operational':'Infrastructure degraded');
    text('hero-account',a.type||'UNKNOWN');text('hero-entries',c.entries_allowed_by_control&&c.execution_enabled?'GATED / ARMED':'BLOCKED');
    text('hero-release',short(data.release?.version||'—',25));
    text('equity',money(f.equity,f.currency));text('balance',money(f.balance,f.currency));
    text('equity-foot',a.age_seconds==null?'No account snapshot':'Broker data '+num(a.age_seconds)+'s old');
    text('unknown-count',num(m.uncertain_orders));text('audit-count',num(m.audit_24h));
    const armed=!!c.armed&&!c.kill_switch;
    setPill('overview-arm-pill',armed?'ARMED':'DISARMED',armed?'success':'warning');setPill('trade-state',armed?'ARMED':'DISARMED',armed?'success':'warning');
    text('account-type',a.type);text('account-binding',a.bound?'VERIFIED':'NOT VERIFIED');
    text('kill-state',c.kill_switch?'ACTIVE':'CLEAR');text('expiry-state',when(c.expiry));
    setPill('health-pill',h.healthy?'HEALTHY':'DEGRADED',healthClass(h.healthy));
    text('heartbeat-state',(h.heartbeat_state||'—')+' • '+(h.heartbeat_age_seconds!=null?Math.round(h.heartbeat_age_seconds)+'s ago':'—'));
    text('db-state',h.db_integrity);text('disk-state',h.free_gb+' GB');
    text('risk-state',(data.risk?.max_per_trade_pct||'—')+'%');
    text('large-arm',armed?'ARMED':'DISARMED');$('large-arm').style.color=armed?'#7cebbd':'#e6edf7';
    text('trade-reason',c.reason||'—');
    text('release-full',data.release?.version||'—');
    text('sys-db',h.db_integrity);text('sys-disk',h.free_gb+' GB');text('sys-blockers',(h.reasons||[]).length?' '+h.reasons.length+' active':'None detected');
    const tp=$('top-health');tp.className='status-tag '+(h.healthy?'':'bad');tp.innerHTML='<i></i>'+(h.healthy?' HEALTHY':' DEGRADED');
    text('last-updated','Updated '+new Date().toLocaleTimeString('en-GB',{hour:'2-digit',minute:'2-digit',second:'2-digit'}));
    text('service-summary',data.services?.filter(x=>x.status==='active').length+' / '+data.services?.length+' active');
    renderServices(data.services||[]);
  }
  function renderServices(services){const host=$('services-list');clear(host);services.forEach(s=>{const row=el('div','service-row');const info=el('div');info.append(el('b','',s.name),el('small','',s.unit));const status=el('span','service-status '+(s.status==='inactive'?'disabled':s.status==='active'?'':'warning'),String(s.status).toUpperCase());row.append(info,status);host.append(row);});}
  function preview(events){const host=$('overview-events');clear(host);const arr=(events||[]).slice(0,5);if(!arr.length){host.append(el('div','empty','No recent audit events'));return;}arr.forEach(v=>{const row=el('div','mini-event');row.append(el('span','event-icon','◇'),el('div','event-name',v.event),el('span','event-source',short(v.component,22)),el('time','',when(v.time)));host.append(row);});}
  function populate(table, rows, keys){const host=$(table);clear(host);if(!rows.length){const tr=el('tr');const td=el('td','','No records found');td.colSpan=keys.length;tr.append(td);host.append(tr);return;}rows.forEach(row=>{const tr=el('tr');keys.forEach(k=>{const td=el('td');const val=row[k];if(k==='side' || k==='state' || k==='level'){td.append(el('span','table-badge '+(String(val).toLowerCase()==='buy'?'buy':String(val).toLowerCase()==='sell'?'sell':''),short(val,36)));}else if(k==='time'||k.includes('at_utc'))td.textContent=when(val);else td.textContent=short(val,160);tr.append(td);});host.append(tr);});}
  function renderLogs(){const filter=$('log-filter').value.trim().toLowerCase();const found=logs.filter(v=>!filter||Object.values(v).some(x=>String(x??'').toLowerCase().includes(filter)));populate('logs-body',found,['time','level','component','event','details']);}
  async function loadLogs(){try{const v=await req('/api/events?source='+encodeURIComponent($('log-source').value)+'&limit=120');logs=v.events||[];renderLogs();}catch(e){toast('Audit unavailable: '+e.message,true);}}
  async function loadOrders(){try{const v=await req('/api/orders');populate('orders-body',v.orders||[],['symbol','side','volume','state','updated_at_utc','last_reason']);populate('positions-body',v.positions||[],['symbol','side','volume','ownership','last_seen_at_utc']);}catch(e){toast('Orders unavailable: '+e.message,true);}}
  async function refresh(silent=false){try{const v=await req('/api/status');update(v);if(!silent)toast('Telemetry updated');const logs=await req('/api/events?source=audit&limit=5');preview(logs.events||[]);}catch(e){const box=$('connection-error');box.classList.remove('hidden');box.textContent='Connection error — '+e.message+' · Dashboard controls are unavailable until telemetry recovers.';const tp=$('top-health');tp.className='status-tag bad';tp.innerHTML='<i></i> OFFLINE';}}
  let modalAction=null;
  function closeModal(){$('modal').classList.add('hidden');$('modal-confirm').value='';$('modal-error').classList.add('hidden');modalAction=null;}
  function openModal(action,check=null){modalAction={action,check};let phrase='',title='',desc='',detail='';if(action==='arm'){phrase='ARM '+check.account_type;title='Authorize '+check.account_type+' trading';desc='This enables real MT5 order execution for a bounded lease. Preflight will run again before committing.';detail='Account: '+check.account_type+' · Duration: '+$('lease-duration').selectedOptions[0].textContent+' · Broker identity: freshly verified';}else if(action==='disarm'){phrase='DISARM';title='Disarm trading';desc='Block new entries without stopping the trading infrastructure or clearing an active Kill Switch.';detail='State change: ARMED → DISARMED. Existing positions remain untouched.';}else{phrase='EMERGENCY STOP';title='Activate emergency kill switch';desc='This immediately blocks new entries and requires a separate offline reset to allow them again.';detail='Kill Switch: ACTIVE · ARM: DISARMED. Existing broker positions will not be liquidated.';}
    modalAction.phrase=phrase;text('modal-title',title);text('modal-desc',desc);text('modal-detail',detail);text('expected-confirm',phrase);$('modal-confirm').value='';$('modal-submit').disabled=true;$('modal-error').classList.add('hidden');$('modal').classList.remove('hidden');$('modal-confirm').focus();
    $('modal-submit').className='btn '+(action==='kill'?'btn-danger':'btn-primary');
  }
  async function startArm(){const b=$('arm-btn');b.disabled=true;b.textContent='Checking broker…';try{const check=await req('/api/preflight');if(!check.ready){showTab('trading');const host=$('preflight-results');renderPreflight(check);toast('ARM blocked by '+check.reasons.length+' preflight gate(s)',true);return;}renderPreflight(check);openModal('arm',check);}catch(e){toast('ARM unavailable: '+e.message,true);}finally{b.disabled=false;b.textContent='◇   ARM trading';}}
  function renderPreflight(check){const host=$('preflight-results');clear(host);const gate=el('div','gate '+(check.ready?'good':'bad'),(check.ready?'✓ ':'✕ ')+ 'MT5 account: '+(check.account_type||'UNAVAILABLE'));host.append(gate);if(check.ready)host.append(el('div','gate good','✓ All broker, strategy, risk and control admission checks passed'));else(check.reasons||[]).forEach(reason=>host.append(el('div','gate bad','✕ '+reason)));}
  async function submitModal(){const current=modalAction;if(!current)return;const confirm=$('modal-confirm').value;if(confirm!==current.phrase)return;const payload={confirmation:confirm};if(current.action==='arm')payload.minutes=Number($('lease-duration').value);$('modal-submit').disabled=true;$('modal-error').classList.add('hidden');try{await req('/api/control/'+current.action,{method:'POST',headers:{'Content-Type':'application/json','X-Forex-AI-Action':'operator-v1'},body:JSON.stringify(payload)});closeModal();toast('Control updated and written to the audit journal');await refresh(true);}catch(e){text('modal-error',e.message);$('modal-error').classList.remove('hidden');$('modal-submit').disabled=false;await refresh(true);}}
  $('arm-btn').addEventListener('click',startArm);
  $('disarm-btn').addEventListener('click',()=>openModal('disarm'));
  $('kill-btn').addEventListener('click',()=>openModal('kill'));
  $('preflight-btn').addEventListener('click',async()=>{const b=$('preflight-btn');b.disabled=true;try{renderPreflight(await req('/api/preflight'));}catch(e){renderPreflight({ready:false,account_type:'UNAVAILABLE',reasons:[e.message]});}finally{b.disabled=false;}});
  $('modal-close').addEventListener('click',closeModal);$('modal-cancel').addEventListener('click',closeModal);
  $('modal').addEventListener('click',(e)=>{if(e.target===$('modal'))closeModal();});
  document.addEventListener('keydown',(e)=>{if(e.key==='Escape')closeModal();});
  $('modal-confirm').addEventListener('input',()=>{$('modal-submit').disabled=!modalAction||$('modal-confirm').value!==modalAction.phrase;});
  $('modal-submit').addEventListener('click',submitModal);
  $('refresh-btn').addEventListener('click',()=>refresh(false));
  $('log-source').addEventListener('change',loadLogs);$('log-filter').addEventListener('input',renderLogs);$('log-refresh').addEventListener('click',loadLogs);

  // Direct broker tick history over one shared SSE stream. Operational status
  // deliberately keeps its slower polling; no HTTP request is made per tick.
  let tickStream=null, tickOnline=false, tickHealth='CONNECTING', latestTick=null;
  let pointSeries=[], tapeSeries=[], latestStreamId=0, totalTicks=0, streamGaps=0, paintScheduled=false;
  const brokerFormat=(price)=>Number.isFinite(Number(price)) ? Number(price).toFixed(3) : '—';
  function ingestTick(event) {
    if(!event || event.type!=='tick' || !Number.isFinite(Number(event.bid)) || !Number.isFinite(Number(event.ask)))return;
    if(event.id && event.id<=latestStreamId)return;
    latestStreamId=Math.max(latestStreamId,Number(event.id)||0);
    latestTick=event; totalTicks++;
    lastCollectorPoll=Math.max(lastCollectorPoll,Number(event.received_ms)||0);
    const mid=(Number(event.bid)+Number(event.ask))/2;
    pointSeries.push(mid);if(pointSeries.length>300)pointSeries.shift();
    tapeSeries.unshift(event);if(tapeSeries.length>25)tapeSeries.pop();
    if(!paintScheduled){paintScheduled=true;requestAnimationFrame(()=>{paintScheduled=false;paintMarket();});}
  }
  function marketState() {
    const age=latestTick ? Date.now()-Number(latestTick.broker_time_ms) : Infinity;
    const pollAge=lastCollectorPoll?Date.now()-lastCollectorPoll:Infinity;
    let label='WAITING', kind='warning';
    if(!tickOnline || tickHealth==='RECONNECTING' || tickHealth==='STOPPED'){
      label='DISCONNECTED';kind='danger';
    } else if(pollAge>5000){label='COLLECTOR STALE';kind='danger';}
    else if(!latestTick){label='AWAITING TICKS';kind='warning';}
    else if(age>5000){label='MARKET IDLE';kind='warning';}
    else {label='LIVE STREAM';kind='success';}
    setPill('tick-state',label,kind);
    text('tick-age',Number.isFinite(age)?(age<0?'CLOCK SKEW':Math.round(age)+' ms'):'—');
    text('tick-poll',Number.isFinite(pollAge)?Math.max(0,Math.round(pollAge))+' ms ago':'—');
  }
  let lastCollectorPoll=0;
  function applySnapshot(s) {
    if(!s)return;
    if(Number(s.latest_id)<latestStreamId){
      // The collector restarted; old event IDs are not comparable.
      latestStreamId=0;latestTick=null;pointSeries=[];tapeSeries=[];
    }
    tickHealth=s.stream_state||'CONNECTING';
    lastCollectorPoll=Number(s.last_poll_ms||0);
    streamGaps=Math.max(streamGaps,Number(s.overflows||0));
    if(Array.isArray(s.quotes)){
      // Quote snapshot is a recovery hint, not a historical tick event.
      for(const item of s.quotes){if(item.type==='tick' && (!latestTick || Number(item.id)>latestStreamId)){ingestTick(item);}}
    }
    if(s.stream_state==='RECONNECTING')text('tick-message','MT5 bridge unavailable; prices may be stale. Reconnecting…');
    marketState();
  }
  function paintMarket(){
    if(!latestTick)return;
    const prior=tapeSeries[1];
    const diff=prior ? Number(latestTick.bid)-Number(prior.bid) : 0;
    text('tick-symbol',latestTick.symbol);
    text('tick-bid',brokerFormat(latestTick.bid));
    text('tick-ask',brokerFormat(latestTick.ask));
    text('tick-spread',Number(latestTick.spread).toFixed(3)+' (price)');
    text('tick-time',new Date(Number(latestTick.broker_time_ms)).toISOString().slice(11,23));
    text('tick-total',num(totalTicks));text('tick-gaps',num(streamGaps));
    const delta=$('tick-direction');delta.textContent=(diff>0?'▲ +':diff<0?'▼ ':'')+diff.toFixed(3);
    delta.classList.toggle('down',diff<0);
    const tape=$('tick-tape');clear(tape);
    for(const item of tapeSeries){
      const row=el('div','tape-item');
      row.append(el('span','',new Date(Number(item.broker_time_ms)).toISOString().slice(11,23)),
                 el('span','',brokerFormat(item.bid)),el('span','',brokerFormat(item.ask)));
      tape.append(row);
    }
    drawTickChart();
    marketState();
  }
  function drawTickChart(){
    const canvas=$('tick-canvas');const ctx=canvas.getContext('2d');
    const width=Math.max(170,canvas.clientWidth);const height=Math.max(150,canvas.clientHeight);
    const dpr=Math.min(window.devicePixelRatio||1,2);
    if(canvas.width!==Math.round(width*dpr)||canvas.height!==Math.round(height*dpr)){
      canvas.width=Math.round(width*dpr);canvas.height=Math.round(height*dpr);
    }
    ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,width,height);
    const points=pointSeries;if(!points.length)return;
    const hi=Math.max(...points),lo=Math.min(...points),range=Math.max(hi-lo,0.001);
    const pad=22;const plotH=height-pad*2;
    ctx.strokeStyle='rgba(101,229,186,.11)';ctx.lineWidth=1;
    for(let i=0;i<5;i++){const y=pad+(i/4)*plotH;ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(width,y);ctx.stroke();}
    const coords=points.map((value,i)=>[points.length>1?(i/(points.length-1))*(width-4):width-4,
      pad+plotH-((value-lo)/range)*plotH]);
    ctx.beginPath();coords.forEach(([x,y],i)=>i?ctx.lineTo(x,y):ctx.moveTo(x,y));
    ctx.strokeStyle='#72edc3';ctx.lineWidth=2;ctx.lineJoin='round';ctx.stroke();
    const [lx,ly]=coords[coords.length-1];
    ctx.beginPath();ctx.arc(lx,ly,4.4,0,Math.PI*2);ctx.fillStyle='#b1ffda';ctx.fill();
    text('chart-low','LOW '+brokerFormat(lo));text('chart-high','HIGH '+brokerFormat(hi));
  }
  function launchTickStream(){
    if(!window.EventSource){text('tick-message','Browser does not support Server-Sent Events.');return;}
    tickStream=new EventSource('/api/stream/ticks');
    tickStream.onopen=()=>{tickOnline=true;marketState();text('tick-message','Connected to live MT5 tick history · broker timestamps shown in UTC');};
    tickStream.addEventListener('snapshot',e=>{try{applySnapshot(JSON.parse(e.data));}catch{}});
    tickStream.addEventListener('tick',e=>{try{ingestTick(JSON.parse(e.data));}catch{}});
    tickStream.addEventListener('health',e=>{try{const v=JSON.parse(e.data);tickHealth=v.state;marketState();}catch{}});
    tickStream.addEventListener('poll',e=>{try{lastCollectorPoll=Number(JSON.parse(e.data).last_poll_ms)||0;marketState();}catch{}});
    tickStream.addEventListener('gap',e=>{streamGaps++;try{const v=JSON.parse(e.data);if(v.snapshot)applySnapshot(v.snapshot);text('tick-message','Tick stream gap detected: '+(v.reason||'history truncated')+' · do not assume complete tape');}catch{}text('tick-gaps',num(streamGaps));});
    tickStream.onerror=()=>{tickOnline=false;text('tick-message','Stream interrupted; automatic reconnect in progress.');marketState();};
  }
  window.addEventListener('resize',()=>{if(latestTick)drawTickChart();});
  setInterval(marketState,1000);
  launchTickStream();
  refresh(true);setInterval(()=>{if(!document.hidden)refresh(true);},12000);
})();
