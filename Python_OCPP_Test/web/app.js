'use strict';
let state = null, selected = '', connector = '1', openEvents = new Set();
const $ = id => document.getElementById(id);
const text = (id, value) => { $(id).textContent = value; };
const fmt = (v, digits=2) => Number.isFinite(v) ? v.toLocaleString(undefined, {maximumFractionDigits:digits, minimumFractionDigits:digits}) : '—';
const date = value => value && !isNaN(Date.parse(value)) ? new Date(value).toLocaleString() : '—';
const clock = value => value && !isNaN(Date.parse(value)) ? new Date(value).toLocaleTimeString() : '—';
function node(tag, value, cls) { const n = document.createElement(tag); if (value !== undefined) n.textContent = value; if (cls) n.className = cls; return n; }
function empty(el, message) { el.replaceChildren(node('div', message, 'empty')); }
function age(value) { if (!value) return 'never'; const s = Math.max(0, Math.floor((Date.now()-Date.parse(value))/1000)); return s < 60 ? `${s}s ago` : `${Math.floor(s/60)}m ago`; }
function options(el, entries, value) { el.replaceChildren(...entries.map(([key,label]) => { const n=node('option',label); n.value=key; return n; })); el.value=value; }
function sample(samples, measurand, phase=null) {
  return samples.filter(s => String(s.connector) === connector && s.measurand === measurand && (s.phase || null) === phase && (!s.location || s.location === 'Outlet') && (!s.format || s.format === 'Raw'))
    .sort((a,b) => String(b.received_at).localeCompare(String(a.received_at)))[0];
}
function numeric(s, kind) {
  if (!s || s.value === '' || !Number.isFinite(Number(s.value))) return null;
  const units = {power:{W:1,kW:1000},current:{A:1},voltage:{V:1},energy:{Wh:1,kWh:1000}};
  const factor = units[kind]?.[s.unit];
  return factor === undefined ? null : Number(s.value)*factor;
}
function valueText(s, kind, unit, divisor=1) { const n=numeric(s,kind); return n===null ? '—' : `${fmt(n/divisor)} ${unit}`; }
function chart(d) {
  const svg=$('chart'); svg.replaceChildren();
  const ns='http://www.w3.org/2000/svg';
  const points=(d?.history||[]).filter(p=>String(p.connector)===connector).map(p=>({t:Date.parse(p.timestamp||p.received_at),v:numeric(sample(p.samples,'Power.Active.Import'),'power')})).filter(p=>p.v!==null && Number.isFinite(p.t));
  $('chart-empty').hidden=points.length>0;
  const add=(tag,attrs)=>{const n=document.createElementNS(ns,tag); Object.entries(attrs).forEach(([k,v])=>n.setAttribute(k,v)); svg.append(n); return n;};
  const min=0, max=Math.max(1000,...points.map(p=>p.v));
  for(let i=0;i<4;i++){const y=15+i*55; add('line',{x1:45,y1:y,x2:790,y2:y,stroke:'#e5edf3','stroke-dasharray':'4 5'}); add('text',{x:0,y:y+4,fill:'#6c8091','font-size':12}).textContent=fmt(max*(1-i/3)/1000,1);}
  if(!points.length){text('chart-start','—');text('chart-end','—');return;}
  points.sort((a,b)=>a.t-b.t);
  const start=points[0].t, span=Math.max(1,points.at(-1).t-start);
  const coords=points.map(p=>[45+(p.t-start)/span*745,180-(p.v-min)/(max-min)*165]);
  add('path',{d:`M ${coords[0][0]},180 L ${coords.map(p=>p.join(',')).join(' L ')} L ${coords.at(-1)[0]},180 Z`,fill:'#eaf3fc'});
  add('polyline',{points:coords.map(p=>p.join(',')).join(' '),fill:'none',stroke:'#1265b4','stroke-width':2.5,'stroke-linejoin':'round'});
  if(coords.length===1)add('circle',{cx:coords[0][0],cy:coords[0][1],r:4,fill:'#1265b4'});
  text('chart-start',clock(new Date(start).toISOString()));text('chart-end',clock(new Date(points.at(-1).t).toISOString()));
}
function renderEvents(d) {
  const el=$('events'), query=$('search').value.toLowerCase(), direction=$('direction').value;
  const events=(d?.events||[]).filter(e=>(!direction||e.direction===direction)&&`${e.action} ${e.id} ${e.kind} ${e.raw}`.toLowerCase().includes(query));
  if(!events.length){empty(el,d?'No matching messages.':'Messages will appear after a charge point connects.');return;}
  const nodes=events.map(e=>{
    const key=`${e.time}|${e.direction}|${e.id}|${e.kind}`;
    const detail=node('details'),summary=node('summary'); detail.open=openEvents.has(key);
    summary.append(node('span',clock(e.time),'time'),node('span',e.direction==='in'?'Wallbox → server':'Server → wallbox','direction'),node('strong',e.action),node('span',e.kind,`tag ${e.kind==='ERROR'?'error':''}`));
    let raw=e.raw; try {raw=JSON.stringify(JSON.parse(raw),null,2);}catch{}
    detail.append(summary,node('pre',raw));
    if(e.truncated)detail.append(node('p','Display truncated at 12,000 characters.','notice'));
    detail.addEventListener('toggle',()=>{if(detail.open)openEvents.add(key);else openEvents.delete(key);});
    return detail;
  });
  el.replaceChildren(...nodes);
}
function render() {
  const devices=state?.devices||[];
  if(!devices.some(d=>d.id===selected)){selected=devices[0]?.id||'';openEvents.clear();}
  options($('device'),devices.length?devices.map(d=>[d.id,`${d.id}${d.connected?'':' · disconnected'}`]):[['','No charge points yet']],selected);
  const d=devices.find(d=>d.id===selected);
  renderConfiguration(d);
  const ids=[...new Set(['1',...Object.keys(d?.connectors||{}),...(d?.samples||[]).map(s=>String(s.connector))])].sort();
  if(!ids.includes(connector))connector=ids[0];
  options($('connector'),ids.map(id=>[id,id==='0'?'Station (connector 0)':`Connector ${id}`]),connector);
  const samples=d?.samples||[], status=d?.connectors?.[connector];
  const active=(d?.sessions||[]).find(s=>String(s.connector)===connector&&s.status==='Active');
  const recent=active||(d?.sessions||[]).find(s=>String(s.connector)===connector);
  const power=sample(samples,'Power.Active.Import');
  text('power',valueText(power,'power','kW',1000)); text('power-note',power?`Last received ${age(power.received_at)}`:'No total-power measurement received');
  text('energy',recent?.energy_wh!=null?`${fmt(recent.energy_wh/1000,3)} kWh`:'—');text('energy-note',recent?`Session ${recent.id} · ${recent.status.toLowerCase()}`:'No session recorded');
  text('offered',valueText(sample(samples,'Current.Offered'),'current','A'));
  text('status',status?.status||'—');text('error',status?`Error: ${status.errorCode||'not supplied'}`:'No status received');
  const stale=d?.last_seen && Date.now()-Date.parse(d.last_seen)>90000;
  $('connection').className=`connection ${d?.connected?(stale?'warning':'live'):''}`;
  text('connection',!d?'Waiting for a wallbox or simulator to connect. Settings are read after registration; no charging commands are sent.':!d.connected?`Disconnected · ${d.id} · Last received ${age(d.last_seen)}. Values below are retained, not live.`:stale?`Connected socket · No message for ${age(d.last_seen).replace(' ago','')}. Check the wallbox connection.`:`Connected · ${d.id} · Last message ${age(d.last_seen)} · Read-only monitoring`);
  $('phases').replaceChildren(...['L1','L2','L3'].map(phase=>{const n=node('div',undefined,'phase');n.append(node('b',phase),node('span',valueText(sample(samples,'Current.Import',phase),'current','A')),node('small',valueText(sample(samples,'Power.Active.Import',phase),'power','W')));return n;}));
  chart(d);
  const visible=samples.filter(s=>String(s.connector)===connector).sort((a,b)=>a.measurand.localeCompare(b.measurand)||String(a.phase||'').localeCompare(String(b.phase||'')));
  const tbody=$('samples');tbody.replaceChildren();
  for(const s of visible){const tr=node('tr');const parsed=Number(s.value);const value=s.format==='SignedData'?s.value:(s.value!==''&&Number.isFinite(parsed)?fmt(parsed):s.value);for(const v of [s.measurand,s.phase||'Total / unphased',value,s.unit_defaulted?(s.unit?`${s.unit} (default)`:'Not supplied'):s.unit,[s.context||'Sample.Periodic',s.location||'Outlet',s.format==='SignedData'?'SignedData':''].filter(Boolean).join(' / '),date(s.timestamp),age(s.received_at)])tr.append(node('td',v));tbody.append(tr);}
  if(!visible.length){const tr=node('tr'),td=node('td','No measurements received for this connector. Missing values are never shown as zero.');td.colSpan=7;tr.append(td);tbody.append(tr);}
  text('measurement-age',`${visible.length} reported values · latest per measurand / phase / context / location`);
  const details=$('details');details.replaceChildren();
  const fields=[['Charge point',d?.id],['Connection',d?(d.connected?'Connected':'Disconnected'):null],['Connected at',d?.connected_at?date(d.connected_at):null],['Disconnected at',d?.disconnected_at?date(d.disconnected_at):null],['Last heartbeat',d?.last_heartbeat?date(d.last_heartbeat):null],['Messages',d?`${d.received} received / ${d.sent} sent`:null],['OCPP errors',d?.protocol_errors],['Latest profile',d?.profile?`${d.profile.response?.status||'Awaiting response'} · ${date(d.profile.time)}`:null],...Object.entries(d?.boot||{}).map(([k,v])=>[k,typeof v==='object'?JSON.stringify(v):v])];
  for(const [k,v]of fields){details.append(node('dt',k),node('dd',v??'—'));}
  const sessions=$('sessions');const ss=(d?.sessions||[]).filter(s=>String(s.connector)===connector);
  sessions.replaceChildren(...ss.map(s=>{const el=node('div',undefined,'session'),head=node('div',undefined,'session-head');head.append(node('span',`#${s.id} · ${s.status}`),node('span',s.energy_wh!=null?`${fmt(s.energy_wh/1000,3)} kWh`:'—'));el.append(head,node('p',`${date(s.start)} — ${s.end?date(s.end):'not ended'}`),node('p',`ID tag: ${s.id_tag||'—'}${s.reason?` · Stop reason: ${s.reason}`:''}`));return el;}));
  if(!ss.length)empty(sessions,'No transactions recorded since this server started.');
  $('counts').replaceChildren(...Object.entries(d?.counts||{}).sort((a,b)=>b[1]-a[1]).map(([name,count])=>node('span',`${name} ${count}`)));
  renderEvents(d);
}
function renderConfiguration(d) {
  const c=d?.configuration, table=$('configuration-keys');
  const keys=c?.keys||[];
  const messages={waiting:'Waiting for the charge point to register.',loading:'Reading configuration from the charge point…',ready:`${keys.length} settings reported · Read ${date(c?.received_at)}`,unsupported:'The charge point does not support GetConfiguration.',error:'Configuration could not be read.'};
  text('capabilities-status',!d?'Connect a charge point to read its configuration.':`${messages[c?.status]||messages.waiting}${c?.error?' '+c.error:''}${!d.connected?' Disconnected; this is the last result.':''}${d.boot?.firmwareVersion==='sim'?' Simulator model — not a Wattpilot capability report.':''}`);
  table.replaceChildren(...[...keys].sort((a,b)=>a.key.localeCompare(b.key)).map(k=>{const tr=node('tr');tr.append(node('td',k.key),node('td',k.value===undefined?'Not supplied':k.value===''?'(empty)':k.value),node('td',k.readonly?'Read only':'Writable on device'));return tr;}));
  if(!keys.length){const tr=node('tr'),td=node('td',c?.status==='ready'?'The device returned no configuration keys.':'No configuration keys available yet.');td.colSpan=3;tr.append(td);table.append(tr);}
  const highlights=['SupportedFeatureProfiles','NumberOfConnectors','MeterValueSampleInterval','HeartbeatInterval','ChargingScheduleAllowedChargingRateUnit','ChargingScheduleMaxPeriods'];
  $('capability-summary').replaceChildren(...highlights.flatMap(name=>{const k=keys.find(k=>k.key===name);return k?[node('span',`${name}: ${k.value??'not supplied'}`)]:[];}));
  text('unknown-keys',c?.unknown_keys?.length?`Unrecognised keys: ${c.unknown_keys.join(', ')}`:'');
}
async function poll(){
  try{const response=await fetch('/api/state',{cache:'no-store'});if(!response.ok)throw Error(response.status);state=await response.json();text('feed',`Live feed · ${clock(state.server_time)}`);render();}
  catch{ text('feed','Dashboard server unreachable');$('connection').className='connection warning';text('connection','Connection to dashboard lost. Displayed values are the last received snapshot.'); }
  finally{setTimeout(poll,2000);}
}
$('device').addEventListener('change',e=>{selected=e.target.value;openEvents.clear();render();});
$('connector').addEventListener('change',e=>{connector=e.target.value;render();});
for(const id of ['search','direction'])$(id).addEventListener(id==='search'?'input':'change',()=>renderEvents(state?.devices.find(d=>d.id===selected)));
$('export').addEventListener('click',()=>{if(!state)return;const url=URL.createObjectURL(new Blob([JSON.stringify(state,null,2)],{type:'application/json'}));const a=node('a');a.href=url;a.download=`ocpp-snapshot-${new Date().toISOString().replaceAll(':','-')}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
render();poll();
