'use strict';
const $=(selector,root=document)=>root.querySelector(selector);
const $$=(selector,root=document)=>[...root.querySelectorAll(selector)];
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const tabs=['rotation','map','diagnostics','finance','community','passport'];
const pretty=(x,max=2)=>x==null?'—':new Intl.NumberFormat(lang,{maximumFractionDigits:max}).format(x);
const usd=x=>new Intl.NumberFormat(lang,{style:'currency',currency:'USD',maximumFractionDigits:0}).format(x);
const btn=(id,k,cls='')=>'<button type="button" id="'+id+'" class="'+cls+'">'+t(k)+'</button>';
const input=(name,label,value='',attrs='')=>'<label>'+t(label)+'<input name="'+name+'" value="'+esc(value)+'" '+attrs+'></label>';
const num=(name,label,value,min=0,step='any')=>input(name,label,value,'type="number" min="'+min+'" step="'+step+'" required');
const options=(keys,current,labels=t)=>keys.map(key=>'<option value="'+esc(key)+'" '+(key===current?'selected':'')+'>'+esc(labels(key))+'</option>').join('');
const load=(key,fallback)=>{try{return JSON.parse(localStorage.getItem(key))??fallback;}catch{return fallback;}};
let data=load('agro-bootstrap',{field:{crop:'maize',hectares:8,lat:38.5449,lon:-121.7405,location_name:'Davis, California',mode:'farmer',tab:'rotation',layer:'Natural'},crops:{},catalog:[],buyers:[],ledger:[],contacts:[],outbox:[]});
let field=data.field, observations=load('agro-observations',null), years=load('agro-years',[]), calc=null, audit=null, recording=null, busy=false;
let month=11, polygon=field.polygon||[], weights=field.priorities||[50,50,50,50], scenario=field.stress||'normal';
let server=true, weatherRun=0, diagnostics=null, financeMetrics=null, ledgerMode=null;
const cropName=key=>data.crops[key]?.names[lang]||key;
const fieldCrop=()=>data.crops[field.crop]||{names:{[lang]:field.crop},price:300,yield_t:2,cost:550};
async function api(path,method='GET',body,signal){
 const binary=body instanceof Blob || body instanceof ArrayBuffer;
 const response=await fetch('/api/'+path,{method,signal,cache:'no-store',headers:body===undefined?{}:{'Content-Type':binary?'audio/wav':'application/json'},
   body:body===undefined?undefined:binary?body:JSON.stringify(body)});
 if(!response.ok)throw new Error((await response.json().catch(()=>({}))).detail||response.statusText);
 return response.json();
}
function saveLocal(){data.field=field;localStorage.setItem('agro-bootstrap',JSON.stringify(data));}
function notice(message){$('#notice').textContent=message;const inline=$('#voiceNotice');if(inline)inline.textContent=message;}
function guard(fn){return async e=>{try{await fn(e);}catch(error){notice(t('failed')+' · '+error.message);}};}
function values(form){return Object.fromEntries(new FormData(form));}
function bind(id,event,fn){$(id)?.addEventListener(event,guard(fn));}
async function refresh(){
 data=await api('bootstrap');field=data.field;polygon=field.polygon||[];saveLocal();
}
async function patch(values,redraw=true){
 const previous=field;field=await api('field','PATCH',values);
 if(previous.crop!==field.crop||previous.hectares!==field.hectares){financeMetrics=null;latestMonteCarlo=null;}
 if(previous.lat!==field.lat||previous.lon!==field.lon)await refresh();
 saveLocal();if(redraw)render();refreshOntology().catch(()=>{});
}
async function sync(cached=false){
 const token=++weatherRun;
 const current=await api(cached?'observations?cached=true':'observations');
 if(token!==weatherRun)return;
 field={...(current.field||field),tab:field.tab,layer:field.layer,mode:field.mode};diagnostics=current.diagnostics||null;calc=diagnostics?.calculations||calc;saveLocal();
 observations=current;localStorage.setItem('agro-observations',JSON.stringify(current));renderCards();refreshOntology().catch(()=>{});
 if(field.tab==='diagnostics'){
   const values=field.calculator||{system:field.irrigation_system||'drip',flow:field.pump_flow_ls||5};
   try{const computed=await api('calculations','POST',values);if(token===weatherRun)calc=computed;}catch{}
   if(token===weatherRun&&field.tab==='diagnostics')renderPanel();
 }
 if(field.tab==='map')paintAgronomy();
 if(field.tab==='passport'){certificate=null;renderPanel();}
}
async function plan(manual=null,length=manual?.length||years.length||5){
 certificate=null;
 years=await api('rotation','POST',{priorities:weights,stress:scenario,manual,length});
 field.sequence=years.map(y=>y.crop);localStorage.setItem('agro-years',JSON.stringify(years));saveLocal();
 if(field.tab==='rotation'||field.tab==='passport')renderPanel();
}
function card(label,value,level){
 return '<article class="card"><h3><span class="dot '+level+'"></span>'+t(label)+'</h3><p>'+esc(value)+'</p><button type="button" class="read-card" aria-label="'+t('read')+'">🔊</button></article>';
}
function plantingWindow(data){
 const forecast=data?.brief?.forecast;
 const rain=data?.diagnostics?.rain_7d_mm;
 const moisture=data?.diagnostics?.root_proxy_m3_m3??forecast?.weighted_soil_0_27_m3_m3;
 const low=forecast?.min_c,high=forecast?.max_c;
 if((Number.isFinite(moisture)&&moisture>.35)||(Number.isFinite(rain)&&rain>35))return ['plantingWet','warn'];
 if(Number.isFinite(rain)&&rain<10&&Number.isFinite(moisture)&&moisture<.18)return ['plantingDry','high'];
 if(![rain,moisture,low,high].every(Number.isFinite))return ['plantingUnknown','warn'];
 if(low<10||high>35)return ['plantingTemperature','warn'];
 return ['plantingSafe','good'];
}
function renderCards(){
 const daily=observations?.sources?.['Open-Meteo']?.data?.daily;
 const hourly=observations?.sources?.['Open-Meteo']?.data?.hourly;
 const rains=daily?.precipitation_sum?.filter(Number.isFinite)||[];
 const rain=rains.length?rains.reduce((a,b)=>a+b,0):null;
 const soil=hourly?.soil_moisture_0_to_1cm?.find(Number.isFinite);
 const crop=fieldCrop(), money=(crop.price*crop.yield_t-crop.cost)*field.hectares;
 const technical=field.mode==='agronomist';
 const waterMessage=rain==null?t('missing'):technical?t('rain')+' '+pretty(rain)+' mm':
   (rain<10?({en:'Little rain expected. Check if the crop needs water.',es:'Se espera poca lluvia. Revisa si el cultivo necesita agua.',pt:'Há previsão de pouca chuva. Veja se a cultura precisa de água.'})[lang]:t('rain')+' '+pretty(rain)+' mm');
 const soilMessage=soil==null?t('missing'):technical?t('moisture')+' '+pretty(soil*100)+'%':
   soil>.35?({en:'The topsoil looks wet. Check before driving a tractor.',es:'El suelo de arriba parece mojado. Comprueba su firmeza antes de pasar el tractor.',pt:'O solo superficial parece molhado. Verifique a firmeza antes de passar o trator.'})[lang]:t('moisture')+' '+pretty(soil*100)+'%';
 const [planting,signal]=plantingWindow(observations);
 $('#cards').innerHTML=card('waterCard',waterMessage,rain==null?'':rain<10?'warn':'good')+
  card('soilCard',soilMessage,soil==null?'':soil>.35?'warn':'good')+card('moneyCard',t('margin')+' '+usd(money),money<0?'high':'good')+card('plantingWindow',t(planting),signal);
 $$('.read-card').forEach(b=>b.onclick=()=>play(b.parentElement.querySelector('p').textContent));
}
function chart(label,points){
 const min=Math.min(...points),max=Math.max(...points);const line=points.map((p,i)=>
   [16+i*268/Math.max(1,points.length-1),108-(p-min)/(max-min||1)*84].join(',')).join(' ');
 return '<div class="tile"><div>'+t(label)+'</div><b class="metric">'+pretty(points.at(-1))+'</b><svg class="chart" viewBox="0 0 300 124" role="img" aria-label="'+t(label)+'"><path d="M16 24h268M16 66h268M16 108h268" stroke="#333240" fill="none"/><polyline points="'+line+'" fill="none" stroke="#c5a2ff" stroke-width="3" stroke-linejoin="round"/></svg><small>'+points.map(x=>pretty(x)).join(' · ')+'</small></div>';
}
function render(){
 localize();
 renderVoice();
 $('#mode').textContent=t(field.mode);
 $('#offline').checked=!!field.offline;
 $('#network').textContent=!server?t('backend'):t(field.offline?'offline':'local');
 $('#place').textContent=field.location_name;
 const f=$('#fieldForm');f.hectares.value=field.hectares;
 f.operation_type.innerHTML=options(['crops','livestock','integrated'],field.operation_type||'crops');
 f.livestock_heads.value=field.livestock_heads||0;
 const keys=[...new Set([...Object.keys(data.crops),field.crop])];
 f.crop.innerHTML=options(keys,field.crop,cropName)+'<option value="__custom">'+t('custom')+'</option>';
 $('#tabs').innerHTML=tabs.map(tab=>'<button data-tab="'+tab+'" class="'+(field.tab===tab?'active':'')+'" aria-pressed="'+(field.tab===tab)+'">'+t(tab)+'</button>').join('');
 renderCards();renderCommandCenter();renderPanel();
}
function renderPanel(){
 const tab=field.tab;
 if(tab!=='map'&&tileLayer){tileLayer.remove();tileLayer=null;}
 const content={rotation:rotationHTML,map:mapHTML,diagnostics:diagnosticsHTML,finance:financeHTML,community:communityHTML,passport:passportHTML}[tab];
 $('#panel').innerHTML='<h2>'+t(tab)+'</h2>'+content();
 ({rotation:rotationEvents,map:mapEvents,diagnostics:diagnosticEvents,finance:financeEvents,community:communityEvents,passport:passportEvents})[tab]();
}
const resourceChoices={equipment:['manual','tractor','mechanized'],labor:['low','medium','high'],market:['any','broad']};
let operationGraph=null,graphRevision=0,latestMonteCarlo=null,latestInfrastructure=null;
async function refreshOntology(){
 const revision=++graphRevision;
 const graph=await api('ontology');
 if(revision!==graphRevision)return;
 operationGraph=graph;renderCommandCenter();
 if(['diagnostics','finance','map'].includes(field.tab))renderPanel();
}
function renderCommandCenter(){
 const host=$('#ontologyGraph'),detail=$('#ontologyDetail');
 if(!operationGraph){host.textContent=t('loadingGraph');return;}
 const {nodes,diagnostics:d,infrastructure:i,finance:r}=operationGraph;
 const status=id=>nodes.find(n=>n.id===id)?.propagated_status||'attention';
 const farmRows=[
   ['climate_satellite',status('climate_satellite'),'decisionClimate',
     [[t('rain'),pretty(d.rain_7d_mm)+' mm'],[t('solarEnergy'),pretty(i.solar_kwh_day_per_10m2)+' kWh']]],
   ['soil_water',status('soil_water'),'decisionWater',
     [['pH',pretty(d.ph)],[t('deficit'),pretty(d.net_irrigation_mm)+' mm']]],
   ['profitAndRisk',status('cashflow_montecarlo'),'decisionProfit',
     [[t('profitProbabilityPlain'),pretty(r.probability_profitable_pct)+'%'],[t('normalProfit'),usd(r.p50)]]]
 ];
 host.innerHTML='<div class="decision-grid">'+farmRows.map(([title,state,description,metrics])=>
   '<article class="tile decision-card"><header><h3>'+esc(t(title))+'</h3><span class="decision-status '+state+'"><i aria-hidden="true"></i>'+esc(t(state))+'</span></header>'+
   '<dl>'+metrics.map(([label,value])=>'<div><dt>'+esc(label)+'</dt><dd>'+esc(value)+'</dd></div>').join('')+'</dl>'+
   '<p>'+esc(t(description))+'</p></article>').join('')+'</div>';
 detail.textContent=field.last_playbook?.steps?t('playbookSaved')+' · '+field.last_playbook.steps[1]?.packet?.bytes+' B':'';
}
function metricTiles(entries){
 return '<div class="grid">'+entries.map(([label,value])=>'<article><strong>'+esc(label)+'</strong><p class="metric">'+esc(value)+'</p></article>').join('')+'</div>';
}
function herdHTML(){
 const h=operationGraph?.livestock;
 if(!h?.heads)return '';
 const c=h.manure_credit_kg_year;
 return '<section class="tile"><h3>'+t('livestockPanel')+' · '+t(h.status)+'</h3>'+metricTiles([
  [t('thi'),pretty(h.thi)],[t('livestockHeads'),pretty(h.heads)],[t('stocking'),pretty(h.stocking_ua_ha)+' UA/ha'],
  [t('capacity'),pretty(h.capacity_ua_ha_30d)+' UA/ha'],[t('grazingDays'),pretty(h.grazing_days)],
  [t('herdWater'),pretty(h.water_l_day)+' L'],[t('dryForage'),pretty(h.forage_dm_kg_ha)+' kg/ha'],
  [t('greenHay'),pretty(h.forage_green_kg)+' / '+pretty(h.hay_kg)+' kg'],[t('manureCredit'),c.n+' / '+c.p+' / '+c.k+' kg N/P/K']
 ])+'<p>'+esc(h.forage_basis)+(h.status==='critical'?' · '+t('herdHeatAction'):'')+'</p></section>';
}
function scenarioHistogram(r){
 const bins=r.histogram||[];
 if(!bins.length)return '';
 const low=bins[0].min_usd,high=bins.at(-1).max_usd,peak=Math.max(1,...bins.map(b=>b.count));
 const left=22,right=634,base=127;
 const x=value=>left+(right-left)*(value-low)/(high-low||1);
 const bars=bins.map(b=>{
   const height=b.count/peak*100;
   const title=usd(b.min_usd)+' – '+usd(b.max_usd)+' · '+pretty(b.count)+' '+t('scenarioCount');
   return '<rect class="scenario-bar '+(b.outcome==='loss'?'loss':'gain')+'" x="'+(x(b.min_usd)+1).toFixed(2)+
     '" y="'+(base-height).toFixed(2)+'" width="'+Math.max(0,x(b.max_usd)-x(b.min_usd)-2).toFixed(2)+'" height="'+height.toFixed(2)+'" rx="3"><title>'+esc(title)+'</title></rect>';
 }).join('');
 const zero=low<0&&high>0?'<line class="scenario-zero" x1="'+x(0)+'" x2="'+x(0)+'" y1="18" y2="127"/><text x="'+x(0)+'" y="150" text-anchor="middle">'+esc(usd(0))+'</text>':'';
 const lost=bins.filter(b=>b.outcome==='loss').reduce((sum,b)=>sum+b.count,0);
 const caption=pretty(lost)+' '+t('lossScenarios')+' · '+pretty(r.runs-lost)+' '+t('gainScenarios');
 return '<figure class="scenario-chart"><svg viewBox="0 0 656 160" role="img" aria-label="'+esc(t('scenarioDistribution')+' · '+caption)+
   '"><path class="scenario-axis" d="M22 127H634"/>'+bars+zero+'<text x="22" y="150">'+esc(usd(low))+
   '</text><text x="634" y="150" text-anchor="end">'+esc(usd(high))+'</text></svg><figcaption>'+
   '<span class="loss-key">'+esc(pretty(lost)+' '+t('lossScenarios'))+'</span>'+
   '<span class="gain-key">'+esc(pretty(r.runs-lost)+' '+t('gainScenarios'))+'</span></figcaption></figure>';
}
function riskHTML(){
 const r=latestMonteCarlo||operationGraph?.finance;
 if(!r)return '';
 const probability=Math.min(100,Math.max(0,r.probability_profitable_pct));
 const figures=[[t('normalProfit'),usd(r.p50)],[t('hardYear'),usd(r.p10)],[t('goodHarvest'),usd(r.p90)]];
 const text=t('profitSummary').replace('{chance}',pretty(probability)).replace('{hard}',usd(r.p10)).replace('{good}',usd(r.p90));
 return '<section class="tile earnings-panel"><h3>'+esc(t('profitSimulation').replace('{runs}',pretty(r.runs,0)))+'</h3>'+
   '<div class="earnings-grid"><article><h4>'+esc(t('profitProbabilityPlain'))+'</h4><p class="metric">'+pretty(probability)+'%</p>'+
   '<progress class="profit-meter" max="100" value="'+probability+'" aria-label="'+esc(t('profitProbabilityPlain'))+'"></progress></article>'+
   figures.map(([label,value])=>'<article><h4>'+esc(label)+'</h4><p class="metric">'+esc(value)+'</p></article>').join('')+
   '</div><p class="earnings-summary">'+esc(text)+'</p><p class="muted">'+esc(t('cropRiskOnly'))+' · '+esc(r.weather_basis)+'</p>'+scenarioHistogram(r)+btn('recalcRisk','recalcRisk','primary')+'</section>';
}
function infrastructureHTML(){
 const v=latestInfrastructure||operationGraph?.infrastructure;
 if(!v)return '';
 return '<section class="tile"><h3>'+t('infrastructure')+'</h3><p>'+t('solarEnergy')+' '+pretty(v.solar_kwh_day_per_10m2)+
 ' kWh/10 m²/d · '+t('slope')+' '+pretty(v.field_slope_pct)+'% · '+t('roadRisk')+' '+t(v.road_screen)+
 ' · '+t('storageRisk')+' '+t(v.storage_screen)+'</p><p class="muted">'+t('infraScreen')+'</p></section>';
}
function resourceHTML(){
 return '<form class="resources">'+Object.entries(resourceChoices).map(([key,choices])=>'<label>'+t(key)+
 '<select name="'+key+'">'+options(choices,field[key]||({equipment:'tractor',labor:'medium',market:'any'})[key])+'</select></label>').join('')+
 '<button>'+t('save')+'</button></form>';
}
function resourceEvents(root){
 root.querySelector('.resources').onsubmit=guard(async e=>{
   e.preventDefault();await patch(values(e.target));await plan(null);notice(t('saved'));
 });
}
function annual(y){
 if(!y)return '';
 const months=(y.planting_months||[]).map(m=>new Intl.DateTimeFormat(lang,{month:'short',timeZone:'UTC'}).format(new Date(Date.UTC(2020,m-1,1))));
 const roles=(y.roles||[]).map(role=>role==='nitrogen'?t('roleNitrogen')+' +'+pretty(y.n_balance_kg_ha)+' kg N/ha':t('role_'+role));
 const details=[t('plantingMonths')+' '+(months.length?months.join(' / '):t('localWindow')),roles.join(' · '),
   t('margin')+' '+usd(y.margin),y.family,t(y.group),t(y.season),t('weed')+' '+t(y.weed_suppression),t('cover')+' '+(y.cover_names?.[lang]||(y.cover?cropName(y.cover):'—')),
   t(y.compatible?'fits':'unfit')];
 if(y.cover)details.push('+'+pretty(y.cover_som)+' pp SOM · '+pretty(y.cover_carbon_t_co2e_ha)+' t CO₂e/ha · '+t('erosion'));
 if(y.pest)details.push(t('breakHosts')+' '+y.pest[lang]);
 if(y.urea_bags)details.push(t('urea')+' '+pretty(y.urea_bags));
 return details.map(d=>'<small>'+esc(d)+'</small>').join('')+(y.bridge?'<strong>⚠ '+t('familyRest')+' ('+y.interval+')</strong>':'');
}
function rotationHistory(){
 return (data.rotations||[]).slice().reverse().map(record=>'<p>'+esc(new Date(record.at).toLocaleDateString(lang))+' · '+
 esc(record.sector)+' · '+esc(record.years.map(y=>cropName(y.crop)).join(' → '))+'</p>').join('');
}
function rotationHTML(){
 const crops=[...new Set([...Object.keys(data.crops),field.crop,...years.map(y=>y.crop)])];
 const total=years.reduce((a,y)=>a+y.margin,0), credit=years.reduce((a,y)=>a+(y.urea_bags||0),0);
 const co2=years.length?(years.at(-1).carbon-(field.som??2.4)/100*.58*3900*44/12)*field.hectares:null;
 return '<div class="toolbar">'+btn('strategyProfit','strategyProfit','primary')+btn('strategySoil','strategySoil')+btn('strategyWater','strategyWater')+'</div>'+
   '<p class="muted">'+t('ref')+'</p>'+resourceHTML()+'<div class="priorities">'+['soil','water','money','diversity'].map((k,i)=>
   '<label>'+t(k)+' <output>'+weights[i]+'</output><input type="range" min="0" max="100" data-weight="'+i+'" value="'+weights[i]+'"></label>').join('')+
   '</div><div class="toolbar"><select id="stress">'+options(['normal','dry','wet'],scenario)+'</select>'+btn('opt','optimize','primary')+btn('approve','approve')+'</div>'+
   '<div class="years">'+Array.from({length:years.length||5},(_,i)=>'<label class="annual">'+t('year')+' '+(i+1)+'<select data-year>'+options(crops,years[i]?.crop||field.sequence?.[i]||field.crop,cropName)+'</select>'+
   annual(years[i])+'</label>').join('')+'</div>'+
   (years.length?'<p class="tile"><strong>'+t('rotationTotal')+'</strong> · '+usd(total)+' · '+pretty(credit)+' '+t('urea')+' · '+pretty(co2)+' t CO₂e</p>':'')+
   (years.length?'<div class="grid">'+[['organic','som'],['carbon','carbon'],['nitrogen','nitrogen']].map(([label,key])=>chart(label,years.map(y=>y[key]))).join('')+'</div>':'')+'<h3>'+t('history')+'</h3>'+rotationHistory();
}
function rotationEvents(){
 for(const [id,strategy] of [['strategyProfit','profit'],['strategySoil','soil'],['strategyWater','water']])
   bind('#'+id,'click',()=>directAction('strategy/'+strategy));
 resourceEvents($('#panel'));
 bind('#approve','click',async()=>{data.rotations=await api('rotation/approve','POST');certificate=null;saveLocal();renderPanel();notice(t('saved'));});
 $$('[data-weight]').forEach(i=>i.oninput=()=>{weights[+i.dataset.weight]=+i.value;i.previousElementSibling.value=i.value;});
 $('#stress').onchange=e=>{scenario=e.target.value;};
 bind('#opt','click',()=>plan(null,5));
 $$('[data-year]').forEach(s=>s.onchange=guard(()=>plan($$('[data-year]').map(i=>i.value))));
}
const mapNode=document.createElement('div');
mapNode.className='map';mapNode.id='map';
let mapInstance, fieldCenter, boundary, vertices, imageLayer, imageURL, tileLayer, agronomyLayer, mapRevision=0;
const rasterKey=()=>[field.layer,field.lat,field.lon,mapDate(month).toISOString().slice(0,7)].join(':');
function mapDate(i){const now=new Date();return new Date(Date.UTC(now.getFullYear(),now.getMonth()-11+i,1));}
async function raster(mode,key,value){
 const db=await new Promise((resolve,reject)=>{
   const open=indexedDB.open('agro-rasters',1);
   open.onupgradeneeded=()=>open.result.createObjectStore('layers');
   open.onsuccess=()=>resolve(open.result);open.onerror=()=>reject(open.error);
 });
 try{
   return await new Promise((resolve,reject)=>{
     const tx=db.transaction('layers',mode==='put'?'readwrite':'readonly'), store=tx.objectStore('layers');
     const req=mode==='put'?store.put(value,key):store.get(key);
     tx.oncomplete=()=>resolve(req.result);tx.onerror=()=>reject(tx.error);tx.onabort=()=>reject(tx.error);
   });
 }finally{db.close();}
}
function area(){
 if(polygon.length<3)return 0;
 const r=Math.PI/180;
 const sum=polygon.reduce((total,[a,b],i)=>{
   const [c,d]=polygon[(i+1)%polygon.length];return total+(((d-b+540)%360)-180)*r*(Math.sin(a*r)+Math.sin(c*r));
 },0);
 return Math.abs(sum)*6371008.8**2/20000;
}
function mapHTML(){
 const date=new Intl.DateTimeFormat(lang,{month:'short',year:'numeric',timeZone:'UTC'}).format(mapDate(month));
 return infrastructureHTML()+'<div class="toolbar"><select id="layer" aria-label="'+t('map')+'">'+options(['Natural','NDVI','NDRE','SMAP','ECOSTRESS'],field.layer,x=>x)+
   '</select><label>'+esc(date)+' <input id="month" type="range" min="0" max="11" value="'+month+'"></label>'+
   btn('playMonths','playMonths')+btn('clear','clearPolygon')+btn('useArea','area')+' <b id="area"></b></div>'+
   '<p class="muted" id="mapStatus">'+t('mapHelp')+'</p><div id="mapHost"></div><div id="mapLegend" class="map-legend"></div><p class="muted">'+
   pretty(field.lat,5)+'°, '+pretty(field.lon,5)+'°</p><details><summary>'+t('importRaster')+'</summary>'+
   '<form id="rasterForm">'+input('file','image','','type="file" accept="image/png,image/jpeg,image/webp" required')+
   num('south','South',field.lat-.005,-90)+num('west','West',field.lon-.005,-180)+
   num('north','North',field.lat+.005,-90)+num('east','East',field.lon+.005,-180)+
   '<button>'+t('save')+'</button></form></details>';
}
const legendScales={
 NDVI:{colors:['#EF4444','#F59E0B','#10B981'],min:.20,max:.85,unit:''},
 NDRE:{colors:['#EF4444','#F59E0B','#10B981'],min:.20,max:.85,unit:''},
 SMAP:{colors:['#F97316','#06B6D4','#2563EB'],min:5,max:50,unit:'%'},
 ECOSTRESS:{colors:['#3B82F6','#EAB308','#DC2626'],min:10,max:50,unit:'°C'}
};
function monthlyMean(series,key,monthKey){
 const samples=(series[key]||[]).filter((v,i)=>Number.isFinite(v)&&series.time?.[i]?.startsWith(monthKey));
 return samples.length?samples.reduce((a,b)=>a+b,0)/samples.length:null;
}
function agronomyIndex(layer,monthKey){
 const brief=observations?.brief;
 const demo=field.satellite_demo;
 if(demo?.date.startsWith(monthKey)&&layer==='ECOSTRESS')return {value:demo.lst_c,source:t('demoReading')};
 if(demo?.date.startsWith(monthKey)&&layer==='SMAP')return {value:demo.root_m3_m3*100,source:t('demoReading')};
 if(!brief)return null;
 const matching=source=>brief.imported?.[source]?.observed?.startsWith(monthKey)?brief.imported[source]:null;
 const era=brief.era5||{}, forecast=brief.forecast||{};
 const current=brief.date?.startsWith(monthKey);
 const moisture=current?(forecast.weighted_soil_0_27_m3_m3??forecast.soil_m3_m3?.['3–9 cm']):monthlyMean(era,'soil_moisture_0_to_7cm_mean',monthKey);
 const temperature=(current?forecast.max_c:null)??monthlyMean(era,'temperature_2m_max',monthKey);
 if(layer==='SMAP'){
   const sample=matching('SMAP');
   if(Number.isFinite(sample?.metrics.root_m3_m3))return {value:sample.metrics.root_m3_m3*100,source:'SMAP · '+sample.observed};
   return Number.isFinite(moisture)?{value:moisture*100,source:t('modeledMoisture')}:null;
 }
 if(layer==='ECOSTRESS'){
   const sample=matching('ECOSTRESS');
   if(Number.isFinite(sample?.metrics.lst_c))return {value:sample.metrics.lst_c,source:'ECOSTRESS · '+sample.observed};
   return Number.isFinite(temperature)?{value:temperature,source:t('airTemperatureProxy')}:null;
 }
 const ph=brief.soil?.phh2o?.value??field.ph;
 const power=brief.power?.ALLSKY_SFC_SW_DWN;
 const solar=power?.date?.startsWith(monthKey.replace('-',''))?power.value:monthlyMean(era,'shortwave_radiation_sum',monthKey)/3.6;
 const archiveSolar=monthlyMean(era,'shortwave_radiation_sum',monthKey);
 const radiation=power?.date?.startsWith(monthKey.replace('-',''))?solar:archiveSolar==null?null:solar;
 if(![moisture,temperature,ph,radiation].every(Number.isFinite))return null;
 const clamp=x=>Math.max(0,Math.min(1,x));
 const health=.40*clamp(1-Math.abs(moisture-.27)/.23)+.25*clamp(1-Math.abs(temperature-24)/22)
   +.2*clamp(1-Math.abs(ph-6.5)/3)+.15*clamp(radiation/6);
 return {value:.20+.65*clamp(health-(layer==='NDRE'?.09:0)),source:t('vigorProxy')};
}
function ramp(scale,value){
 const x=Math.max(0,Math.min(1,(value-scale.min)/(scale.max-scale.min)))*2;
 const start=Math.min(1,Math.floor(x)),ratio=x-start;
 const from=scale.colors[start],to=scale.colors[start+1];
 return '#'+[1,3,5].map(i=>Math.round(parseInt(from.slice(i,i+2),16)*(1-ratio)+parseInt(to.slice(i,i+2),16)*ratio).toString(16).padStart(2,'0')).join('');
}
function paintAgronomy(){
 if(!mapInstance||!mapNode.isConnected||!$('#mapLegend'))return;
 agronomyLayer.clearLayers();
 const legend=$('#mapLegend'),scale=legendScales[field.layer];
 legend.replaceChildren();
 if(!scale)return;
 const indexed=agronomyIndex(field.layer,mapDate(month).toISOString().slice(0,7));
 const gradient='<div class="map-ramp" style="background:linear-gradient(to right,'+scale.colors.join(',')+')"></div>';
 const label=indexed?pretty(indexed.value,2)+' '+scale.unit+' · '+indexed.source+(polygon.length<3?' · '+t('regionalGrid'):''):t('missing');
 legend.innerHTML='<strong>'+esc(label)+'</strong>'+gradient+'<div class="ramp-range"><span>'+scale.min+' '+scale.unit+'</span><span>'+scale.max+' '+scale.unit+'</span></div>';
 if(!indexed)return;
 const color=ramp(scale,indexed.value);
 const feature={color,weight:2,fillColor:color,fillOpacity:.32,interactive:false,pane:'agronomy'};
 if(polygon.length>=3){L.polygon(polygon,feature).addTo(agronomyLayer);return;}
 const delta=.00055,lonDelta=delta/Math.max(.1,Math.cos(field.lat*Math.PI/180));
 for(let y=-1;y<=1;y++)for(let x=-1;x<=1;x++){
   const south=field.lat+(y-.5)*delta,west=field.lon+(x-.5)*lonDelta;
   L.rectangle([[south,west],[south+delta,west+lonDelta]],feature).addTo(agronomyLayer);
 }
}
function paintPolygon(){
 boundary.setLatLngs(polygon);paintAgronomy();vertices.clearLayers();
 polygon.forEach((point,i)=>{
   const icon=L.divIcon({className:'map-vertex',iconSize:[15,15]});
   const vertex=L.marker(point,{icon,draggable:true}).addTo(vertices);
   vertex.on('drag',e=>{const p=e.target.getLatLng();polygon[i]=[p.lat,p.lng];boundary.setLatLngs(polygon);paintAgronomy();$('#area').textContent=pretty(area())+' ha';});
   vertex.on('dragend',guard(()=>patch({polygon},false)));
 });
 $('#area').textContent=pretty(area())+' ha';
}
async function paintRaster(){
 const revision=++mapRevision, key=rasterKey();
 if(imageLayer){imageLayer.remove();imageLayer=null;}
 if(imageURL){URL.revokeObjectURL(imageURL);imageURL=null;}
 if(tileLayer){tileLayer.remove();tileLayer=null;}
 const status=$('#mapStatus');
 const local=await raster('get',key).catch(()=>null);
 if(revision!==mapRevision||!status?.isConnected)return;
 if(local){
   imageURL=URL.createObjectURL(local.image);
   imageLayer=L.imageOverlay(imageURL,local.bounds).addTo(mapInstance);
 } else if(server&&!field.offline&&navigator.onLine){
   const date=mapDate(month).toISOString().slice(0,10);
   const imagery=L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',{
     maxNativeZoom:19,maxZoom:21,attribution:'Imagery © Esri and contributors'});
   const names={Natural:['MODIS_Terra_CorrectedReflectance_TrueColor',9,'jpeg'],
     SMAP:['SMAP_L4_Analyzed_Root_Zone_Soil_Moisture',6,'png'],
     NDVI:['MODIS_Terra_L3_NDVI_Monthly',7,'png']};
   const layer=names[field.layer];
   const maps=[imagery];
   if(layer){
     const [name,level,format]=layer;
     const url='https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/'+name+'/default/'+date+
       '/GoogleMapsCompatible_Level'+level+'/{z}/{y}/{x}.'+format;
     const overlay=L.tileLayer(url,{opacity:field.layer==='Natural'?.55:.75,
       maxNativeZoom:level,maxZoom:21,attribution:'NASA EOSDIS GIBS'});
     maps.push(overlay);
     overlay.on('tileerror',()=>{status.textContent=t('noLayer')+' '+t('mapHelp');});
   }
   tileLayer=L.layerGroup(maps).addTo(mapInstance);
   status.textContent=(layer?'NASA GIBS · '+date:t('noLayer'))+' · '+t('mapHelp');
 } else {
   const Grid=L.GridLayer.extend({createTile(coords){
     const tile=document.createElement('canvas');tile.width=tile.height=256;
     const c=tile.getContext('2d'), point=mapInstance.unproject([coords.x*256,coords.y*256],coords.z);
     c.strokeStyle='#48736d';c.strokeRect(0,0,256,256);
     c.fillStyle='#a8d1c9';c.font='12px monospace';c.fillText(point.lat.toFixed(3)+'°, '+point.lng.toFixed(3)+'°',8,20);
     return tile;
   }});
   tileLayer=new Grid({noWrap:true,attribution:t('offlineGrid')}).addTo(mapInstance);
   status.textContent=t('offlineGrid')+' · '+t('noLayer')+' '+t('mapHelp');
 }
}
function mapEvents(){
 $('#mapHost').append(mapNode);
 if(!mapInstance){
   mapInstance=L.map(mapNode,{doubleClickZoom:false,maxZoom:21});
   mapInstance.createPane('agronomy').style.zIndex=425;
   agronomyLayer=L.layerGroup().addTo(mapInstance);
   boundary=L.polygon([],{color:'#cdb9ff',interactive:false}).addTo(mapInstance);
   vertices=L.layerGroup().addTo(mapInstance);
   mapInstance.on('click',guard(async e=>{
     if(!mapNode.isConnected||polygon.length>=100)return;
     const p=e.latlng.wrap();polygon.push([p.lat,p.lng]);paintPolygon();await patch({polygon},false);
   }));
 }
 mapInstance.invalidateSize();requestAnimationFrame(()=>{if(mapNode.isConnected)mapInstance.invalidateSize();});
 const center=[field.lat,field.lon].join(',');
 if(center!==fieldCenter){mapInstance.setView([field.lat,field.lon],15);fieldCenter=center;}
 paintPolygon();paintRaster();
 bind('#clear','click',async()=>{polygon=[];paintPolygon();await patch({polygon},false);});
 bind('#useArea','click',async()=>{const ha=Math.round(area()*100)/100;if(ha>0)await patch({hectares:ha});});
 bind('#layer','change',e=>patch({layer:e.target.value}));
 $('#month').onchange=e=>{month=+e.target.value;renderPanel();};
 bind('#playMonths','click',async()=>{
   for(let i=0;i<12&&field.tab==='map';i++){
     month=i;renderPanel();$('#playMonths').disabled=true;
     await new Promise(resolve=>setTimeout(resolve,650));
   }
   if(field.tab==='map')$('#playMonths').disabled=false;
 });
 bind('#rasterForm','submit',async e=>{
   e.preventDefault();const v=values(e.target);
   if(!['image/png','image/jpeg','image/webp'].includes(v.file.type)||v.file.size>15e6||
      !(+v.south>=-90&&+v.south<+v.north&&+v.north<=90&&+v.west>=-180&&+v.west<+v.east&&+v.east<=180))
     throw new Error(t('failed'));
   await raster('put',rasterKey(),{image:v.file,bounds:[[+v.south,+v.west],[+v.north,+v.east]]});
   await paintRaster();
 });
}
function table(columns,rows){
 return '<div class="table"><table><thead><tr>'+columns.map(k=>'<th>'+t(k)+'</th>').join('')+'</tr></thead><tbody>'+
 rows.map(row=>'<tr>'+row.map(v=>'<td>'+esc(v)+'</td>').join('')+'</tr>').join('')+'</tbody></table></div>';
}
function diagnosisCards(){
 const d=diagnostics;
 if(!d)return '';
 const c=d.calculations;
 const rows=[
   ['soilChemistry',d.soil_status,'pH '+pretty(d.ph)+' · '+t('organic')+' '+pretty(d.som)+'% · '+t('cropBand')+' '+d.ph_band.join('–'),
    t('lime')+' '+pretty(c.lime_t)+' t. '+t('limeAction')],
   ['irrigationBalance',d.water_status,t('rain')+' '+pretty(d.rain_7d_mm)+' mm · '+t('rootMoisture')+' '+pretty(d.root_proxy_m3_m3==null?null:d.root_proxy_m3_m3*100)+'%'+(d.moisture_source?' · '+d.moisture_source:''),
    pretty(d.net_irrigation_mm)+' mm · '+pretty(c.water_m3)+' m³ · '+pretty(c.pump_hours)+' h. '+t('waterAction')],
   ['climateRisk',d.climate_status,t('humidHours')+' '+pretty(d.humid_hours)+' h · '+t('maxTemp')+' '+pretty(d.max_c)+' °C',
    t(d.humid_hours>=6?'fungalAction':d.max_c>=36?'heatAction':'labelAction')]
 ];
 return '<div class="grid diagnosis-cards">'+rows.map(([title,status,metrics,action])=>'<article class="tile"><h3><span class="dot '+({optimal:'good',attention:'warn',critical:'high'})[status]+'"></span> '+t(title)+'</h3><strong>'+t(status)+'</strong><p>'+esc(metrics)+'</p><p>'+esc(action)+'</p></article>').join('')+'</div>';
}
function diagnosticsHTML(){
 const crop=fieldCrop();
 return herdHTML()+diagnosisCards()+ '<form id="calcForm">'+num('ph','ph',field.ph??6.1,3)+num('som','som',field.som??2.4,.1)+
 num('water','deficit',field.water??25)+
 '<label>'+t('irrigation')+'<select name="system">'+options(['drip','sprinkler','surface'],field.irrigation_system||'drip')+'</select></label>'+
 num('flow','flow',field.pump_flow_ls||5,.01)+num('n','N (kg/ha)',field.calculator?.n??60)+num('p','P₂O₅ (kg/ha)',field.calculator?.p??30)+num('k','K₂O (kg/ha)',field.calculator?.k??30)+
 '<button class="primary">'+t('calc')+'</button></form><p class="muted">'+t('ref')+'</p>'+
 (calc?'<div class="grid">'+[['hours','pump_hours'],['bags','npk_bags'],['lime','lime_t']].map(([label,key])=>
 '<div class="tile">'+t(label)+'<div class="metric">'+pretty(calc[key])+'</div></div>').join('')+'</div>':'')+
 (observations?.soil?.phh2o?'<p class="muted">'+t('ph')+' · '+pretty(observations.soil.phh2o.value)+' · '+esc(observations.soil.phh2o.source)+' · '+esc(observations.soil.phh2o.depth)+'</p>':'')+
 '<div class="grid"><article class="tile"><h3>'+t('scouting')+'</h3><p>'+t('scoutText')+'</p></article>'+
 '<article class="tile"><h3>'+t('zones')+'</h3><p>'+t('zoneBasis')+'</p>'+
 (calc?'<ol>'+[.8,1,1.2].map(f=>'<li>'+pretty(calc.water_m3/field.hectares*f)+' m³/ha · '+pretty(calc.npk_bags*50/field.hectares*f)+' kg NPK/ha</li>').join('')+'</ol>':'')+'</article>'+
 '<article class="tile"><h3>'+t('storage')+'</h3><p>'+((crop.moisture??null)===null?'—':pretty(crop.moisture)+'%')+
 '</p><p>'+t('gdd')+' · '+pretty(observations?.gdd)+'</p></article></div>'+btn('showEvidence','evidence')+' <label>'+t('importEvidence')+' <input id="importEvidence" type="file" accept="application/json,.json"></label>';
}
function diagnosticEvents(){
 bind('#calcForm','submit',async e=>{
   e.preventDefault();const v=values(e.target);
   const edits=Object.fromEntries(['ph','som','water'].filter(k=>+v[k]!==field[k]).map(k=>[k,+v[k]]));
   if(Object.keys(edits).length)await patch(edits,false);
   const settings={system:v.system,flow:+v.flow,n:+v.n,p:+v.p,k:+v.k};
   calc=await api('calculations?remember=true','POST',settings);
   field.calculator=settings;field.irrigation_system=v.system;field.pump_flow_ls=+v.flow;saveLocal();
   await sync(true);
   for(const [key,value] of Object.entries(calc))$('#calcForm').dataset[key]=String(value);
   const summary=[t('hours')+' '+pretty(calc.pump_hours),t('bags')+' '+pretty(calc.npk_bags),t('lime')+' '+pretty(calc.lime_t)];
   notice(summary.join(' · '));
   renderPanel();
   for(const k of ['n','p','k'])$('#calcForm').elements[k].value=v[k];
 });
 bind('#showEvidence','click',()=>{
   $('#sources').innerHTML=indexEvidence(data.catalog).map(([name,owner,url,topic])=>'<article><h3><a href="'+esc(url)+
     '" rel="noopener noreferrer" target="_blank">'+esc(name)+'</a></h3><p>'+esc(owner)+' · '+esc(topic)+' · '+
     t(observations?.brief?.evidence.find(e=>e.name===name)?.status||'catalog_only')+'</p></article>').join('')+Object.entries(observations?.sources||{}).map(([name,s])=>
       '<details><summary>'+esc(name)+' · '+esc(s.status)+' · '+esc(s.fetched||'—')+'</summary><pre>'+esc(JSON.stringify(s.data,null,2))+'</pre></details>').join('');
   const scientific=observations?.brief;
   if(scientific){
     const summary=document.createElement('details');
     summary.open=true;
     const heading=document.createElement('summary');heading.textContent=t('scienceBrief');
     const pre=document.createElement('pre');
     const {evidence,...values}=scientific;pre.textContent=JSON.stringify(values,null,2);
     summary.append(heading,pre);$('#sources').prepend(summary);
   }
   $('#evidence').showModal();
 });
 bind('#importEvidence','change',async e=>{
   const file=e.target.files[0];if(!file)return;
   if(file.size>20000)throw new Error(t('failed'));
   await api('evidence','POST',JSON.parse(await file.text()));
   await sync(true);
   notice(t('saved'));
 });
}
function ledgerHTML(){
 return table(['Date','activity','entryType','amount'],data.ledger.map(x=>[new Date(x.at).toLocaleDateString(lang),x.notes,
   t(x.type==='income'?'income':'expense'),usd(x.amount_usd??x.cost_usd??0)]));
}
function cashValues(){
 const c=fieldCrop(), area=field.hectares, cropCost=c.cost*area, sales=c.price*c.yield_t*area;
 const cash=[-.42*cropCost,-.24*cropCost,-.16*cropCost,-.1*cropCost,-.08*cropCost,sales];
 const today=new Date();
 for(const row of data.ledger){
   const date=new Date(row.at), i=(date.getFullYear()-today.getFullYear())*12+date.getMonth()-today.getMonth();
   if(i>=0&&i<cash.length)cash[i]+=(row.type==='income'?1:-1)*(row.amount_usd??row.cost_usd??0);
 }
 return cash;
}
function financeHTML(){
 const c=fieldCrop(), area=field.hectares;
 const rows=data.buyers.map(b=>({...b,net:b.price*(1-b.loss_pct/100)-b.distance_km*b.freight_per_t_km})).sort((a,b)=>b.net-a.net).slice(0,3);
 const flows=cashValues();let total=0;
 const metrics=financeMetrics;
 const monthLabels=flows.map((_,i)=>new Intl.DateTimeFormat(lang,{month:'short'}).format(new Date(new Date().getFullYear(),new Date().getMonth()+i,1)));
 const sensitivity=metrics?table(['scenario','price','grossRevenue','margin'],metrics.sensitivity.map(row=>[t(row.scenario),usd(row.price_usd_t),usd(row.revenue_usd),usd(row.margin_usd)])):'';
 const entry=ledgerMode?'<form id="ledgerForm">'+num('amount_usd','amount','',.01)+input('notes','activity','','maxlength="1000"')+'<button>'+t('save')+'</button></form>':'';

 const figures=metrics?[['netCash',usd(metrics.cashflow_usd)],['grossRevenue',usd(metrics.projected_revenue_usd)],
   ['costHa',usd(metrics.cost_per_ha)],['marginHa',usd(metrics.margin_per_ha)],['roi',pretty(metrics.roi_pct)+'%'],
   ['breakeven',pretty(metrics.break_even_usd_t)]]:[];
 return riskHTML()+'<p class="muted">'+t('ref')+'</p><div class="grid">'+figures.map(([k,v])=>'<div class="tile">'+t(k)+'<div class="metric">'+v+'</div></div>').join('')+
   '</div>'+chart('cash',flows.map(x=>total+=x))+'<div class="flow-months">'+monthLabels.map(m=>'<span>'+esc(m)+'</span>').join('')+'</div>'+
   '<h3>'+t('sensitivity')+'</h3>'+sensitivity+'<div class="toolbar">'+btn('download','CSV')+btn('expenseEntry','recordExpense')+btn('incomeEntry','recordSale')+'</div>'+
   entry+'<h3>'+t('activity')+'</h3>'+ledgerHTML()+
   '<h3>'+t('buyers')+'</h3><form id="buyers">'+input('name','buyer','','required maxlength="80"')+num('distance_km','distance','')+
   num('price','price','')+num('freight_per_t_km','freight',.16)+num('loss_pct','loss',2)+'<button>'+t('add')+'</button></form>'+
   table(['buyer','distance','price','net'],rows.map(b=>[b.name,pretty(b.distance_km),pretty(b.price),pretty(b.net)]))+
   '<h3>'+t('audit')+'</h3><form id="auditForm">'+num('price','landPrice',field.asking_price||'',.01)+'<button>'+t('calc')+
   '</button></form>'+(audit?'<p class="metric">'+audit.score+'/100 · '+t(audit.decision)+' · '+t('recovery')+' '+usd(audit.recovery_usd_ha)+'/ha</p>':'');
}
function financeEvents(){
 bind('#recalcRisk','click',()=>directAction('finance/simulate'));
 if(!financeMetrics)api('finance').then(result=>{financeMetrics=result;if(field.tab==='finance')renderPanel();}).catch(error=>notice(error.message));
 bind('#expenseEntry','click',()=>{ledgerMode='expense';renderPanel();});
 bind('#incomeEntry','click',()=>{ledgerMode='income';renderPanel();});
 bind('#ledgerForm','submit',async e=>{
   e.preventDefault();const v=values(e.target);const operation=ledgerMode;
   await directAction('ledger',{operation,amount_usd:+v.amount_usd,notes:v.notes});
   ledgerMode=null;if(field.tab==='finance')renderPanel();
 });

 bind('#buyers','submit',async e=>{e.preventDefault();const v=values(e.target);for(const k of Object.keys(v))if(k!=='name')v[k]=+v[k];
   data.buyers=await api('buyers','POST',v);saveLocal();renderPanel();});
 bind('#auditForm','submit',async e=>{e.preventDefault();audit=await api('audit?price='+encodeURIComponent(values(e.target).price));renderPanel();});
 bind('#download','click',()=>{
   const csv='month,net_usd\r\n'+cashValues().map((v,i)=>(i+1)+','+v.toFixed(2)).join('\r\n');
   const url=URL.createObjectURL(new Blob([csv],{type:'text/csv'}));
   const a=document.createElement('a');a.href=url;a.download='agro-mesh-cash.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
 });
}
const meshNodes={field:'nodeField',cooperative:'nodeCoop',satellite_gateway:'nodeGateway',satellite_uplink:'nodeUplink'};
function meshHopTable(packet){
 if(!packet.hops?.length)return '';
 return '<h4>'+t('meshHops')+'</h4>'+table(['meshNode','utcTimestamp','bytesLabel','checksum'],
   packet.hops.map(h=>[t(meshNodes[h.origin])+' → '+t(meshNodes[h.destination]),h.timestamp_utc,h.bytes,h.crc16]));
}
function packMeshFieldAlert(active,diagnostic){
 const encode=new TextEncoder();
 const suffix=' | '+(active.crop||'').replace(/[|\r\n]/g,' ').slice(0,24)+' '+pretty(active.hectares)+' ha | '+
    t('meshRain')+' '+pretty(diagnostic?.rain_7d_mm)+' mm | '+t('meshWater')+' '+pretty(active.water)+' mm';
 const label='AGRO MESH | ';
 const max=159-encode.encode(label+suffix).length;
 let place='',remaining=max;
 for(const character of active.location_name.replace(/[|\r\n]/g,' ')){
   remaining-=encode.encode(character).length;
   if(remaining<0)break;
   place+=character;
 }
 return label+place+suffix;
}
function communityHTML(){
 const opts=data.contacts.map(c=>'<option value="'+esc(c.name)+'">').join('');
 const last=data.outbox.at(-1);
 const contact=data.contacts.find(c=>c.name.toLowerCase()===last?.recipient.toLowerCase());
 return '<form id="contactForm">'+input('name','contact','','required maxlength="80"')+
 input('phone','phone','','type="tel" required')+'<button>'+t('add')+'</button></form>'+
 table(['contact','phone'],data.contacts.map(c=>[c.name,c.phone]))+
 '<h3>'+t('outbox')+'</h3><form id="msgForm">'+input('recipient','contact','','required maxlength="80" list="contactList"')+
 '<datalist id="contactList">'+opts+'</datalist>'+input('text','message','','maxlength="159" required')+
 '<output id="bytes">0 / 159 B</output><button>'+t('queue')+'</button></form>'+
 (last?'<div class="tile"><strong>'+esc(last.recipient)+'</strong><p>'+esc(last.text)+'</p><p>'+last.bytes+' B · '+esc(t(last.status))+'</p>'+
 (contact?'<a href="sms:'+esc(contact.phone)+'?body='+encodeURIComponent(last.text)+'">'+t('sms')+'</a>':'')+
 '<div>'+qrSVG(last.text)+'</div>'+meshHopTable(last)+'</div>':'')+
 '<h3>'+t('mesh')+'</h3><div id="mesh" class="mesh relay-track"><span>'+t('nodeField')+'</span><span>'+t('nodeCoop')+'</span><span>'+t('nodeGateway')+'</span><b id="relayBytes"></b></div>'+btn('simulate','simulate')+btn('autoMeshAlert','autoMeshAlert','primary');
}
function communityEvents(){
 bind('#contactForm','submit',async e=>{e.preventDefault();data.contacts=await api('contacts','POST',values(e.target));saveLocal();renderPanel();});
 const form=$('#msgForm');
 form.elements.text.oninput=()=>{
   const length=new TextEncoder().encode(form.elements.text.value).length;
   $('#bytes').textContent=length+' / 159 B';form.querySelector('button').disabled=length>159;
 };
 bind('#msgForm','submit',async e=>{e.preventDefault();data.outbox=await api('outbox','POST',values(e.target));saveLocal();renderPanel();});
 bind('#simulate','click',()=>directAction('outbox/relay'));
 bind('#autoMeshAlert','click',async()=>{
   const live=await api('observations?cached=true');
   const text=packMeshFieldAlert(live.field||field,live.diagnostics);
   data.outbox=await api('outbox','POST',{recipient:'Mesh',text});saveLocal();renderPanel();notice(t('saved'));
 });

}
let certificate;
function passportHTML(){
 const cert=certificate;
 if(!cert)return '<p class="muted" role="status">'+t('loadingCertificate')+'</p>';
 const checks=[
   ['locationArea',cert.location_name+' · '+pretty(cert.hectares)+' ha'],
   ['issueDate',cert.issued_at],
   ['operationType',t(cert.operation_type)+' · '+t('livestockHeads')+' '+pretty(cert.livestock_heads)],
   ['nitrogenImpact',pretty(cert.nitrogen_saved_kg)+' kg N · '+t('modeled')],
   ['auditedSources',cert.verified_sources?.join(' · ')||t('missing')],
   ['deforestation',t(cert.deforestation.status)+(cert.deforestation.loss_ha==null?'':' · '+pretty(cert.deforestation.loss_ha)+' ha · '+cert.deforestation.observed)],
   ['carbonSeal',cert.carbon.delta_t_co2e_ha==null?t(cert.carbon.status):pretty(cert.carbon.delta_t_co2e_ha)+' t CO₂e/ha · '+t('modeled')],
   ['waterSeal',pretty(cert.water.kg_per_m3)+' kg/m³ · '+t('modeled')],
   ['rotationSeal',t(cert.rotation.status)+' · '+cert.rotation.years.map(cropName).join(' → ')]
 ];
 return '<p class="muted">'+t('passportLabel')+'</p><h3>'+esc(cert.id)+'</h3>'+qrSVG(cert.qr)+
   '<div class="toolbar">'+btn('saveCertificate','exportCertificate')+btn('showQrData','qrData')+btn('verifyPassport','verifyPassport','primary')+'</div>'+
   '<p id="passportIntegrity" class="passport-integrity" role="status" hidden></p>'+
   '<pre id="passportQrData" hidden>'+esc(cert.qr)+'</pre>'+
   '<div class="grid">'+checks.map(([label,status])=>'<article class="tile"><strong>'+t(label)+
   '</strong><p>'+esc(status)+'</p></article>').join('')+'</div>'+ledgerHTML();
}
function passportEvents(){
 if(!certificate){
   api('passport').then(result=>{certificate=result;if(field.tab==='passport')renderPanel();}).catch(error=>notice(error.message));
   return;
 }
 bind('#verifyPassport','click',async()=>{
   const displayed=certificate;
   const verification=await api('passport/verify?payload='+encodeURIComponent(displayed.qr),'POST');
   const status=$('#passportIntegrity');
   if(!status||displayed!==certificate)return;
   const canonical=value=>value&&typeof value==='object'&&!Array.isArray(value)?
      Object.fromEntries(Object.keys(value).sort().map(k=>[k,canonical(value[k])])):Array.isArray(value)?value.map(canonical):value;
   const authentic=verification.valid&&verification.scope==='certificate'&&
      JSON.stringify(canonical(verification.certificate))===JSON.stringify(canonical(displayed));
   status.hidden=false;status.classList.toggle('verified',authentic);
   status.textContent=t(authentic?'passportAuthentic':'passportInvalid');
 });
 bind('#showQrData','click',()=>{$('#passportQrData').hidden=!$('#passportQrData').hidden;});
 bind('#saveCertificate','click',()=>{
   const url=URL.createObjectURL(new Blob([JSON.stringify(certificate,null,2)],{type:'application/json'}));
   const link=document.createElement('a');link.href=url;link.download=certificate.id+'.json';link.click();
   setTimeout(()=>URL.revokeObjectURL(url),1000);
 });
}
function bubble(text,user=false){
 const p=document.createElement('p');p.textContent=text;if(user)p.className='user';
 $('#chat').append(p);$('#chat').scrollTop=$('#chat').scrollHeight;
}
let voicePhase='idle', waveFrame=0, lastAction, orbLevel=0, speechContext, speakingAnalyser;
function unlockSpeech(){
 try{
   speechContext??=new AudioContext();
   if(speechContext.state!=='running')speechContext.resume().catch(()=>{});
 }catch{}
}
document.addEventListener('pointerdown',unlockSpeech,{once:true});
document.addEventListener('keydown',unlockSpeech,{once:true});
function renderOrb(){
 const canvas=$('#voiceOrb'), ctx=canvas.getContext('2d');
 const reduced=matchMedia('(prefers-reduced-motion: reduce)').matches;
 let angle=0, previous=0, level=0;
 function frame(now){
   const elapsed=Math.min(40,now-(previous||now));previous=now;
   angle+=elapsed*(voicePhase==='thinking'?.0035:.0006);
   let target=voicePhase==='listening'?orbLevel:0;
   if(voicePhase==='speaking'&&speakingAnalyser){
     speakingAnalyser.node.getFloatTimeDomainData(speakingAnalyser.samples);
     target=Math.min(1,Math.sqrt(speakingAnalyser.samples.reduce((sum,v)=>sum+v*v,0)/speakingAnalyser.samples.length)*4);
   }
   level+=(target-level)*.2;
   const spin=reduced?0:angle;
   const radius=72+level*29+(reduced?0:3*Math.sin(now/850));
   ctx.clearRect(0,0,240,240);
   ctx.save();ctx.translate(120,120);
   const halo=ctx.createRadialGradient(0,0,radius*.55,0,0,radius*1.32);
   halo.addColorStop(0,'#8b5cf675');halo.addColorStop(.76,'#10b98124');halo.addColorStop(1,'#080a0c00');
   ctx.fillStyle=halo;ctx.fillRect(-120,-120,240,240);
   ctx.beginPath();
   for(let i=0;i<=100;i++){
     const a=2*Math.PI*i/100,r=radius*(1+.065*Math.sin(a*3+spin*2)+.04*Math.sin(a*5-spin));
     ctx.lineTo(Math.cos(a)*r,Math.sin(a)*r);
   }
   ctx.closePath();ctx.clip();
   ctx.fillStyle='#0d1024';ctx.fillRect(-120,-120,240,240);
   ctx.globalCompositeOperation='screen';
   ['#10B981','#8B5CF6','#63e6c2'].forEach((color,i)=>{
     const a=spin+i*2.13,x=Math.cos(a)*radius*.54,y=Math.sin(a)*radius*.57;
     const g=ctx.createRadialGradient(x*.85,y*.85,2,x,y,radius*1.17);
     g.addColorStop(0,color);g.addColorStop(.55,color+'77');g.addColorStop(1,color+'00');
     ctx.fillStyle=g;ctx.fillRect(-120,-120,240,240);
   });
   ctx.globalCompositeOperation='source-over';
   for(let i=-4;i<=4;i++){
     const y=i*radius/5;
     ctx.beginPath();ctx.ellipse(0,y,Math.sqrt(radius*radius-y*y),radius*.18,spin*.18,0,Math.PI*2);
     ctx.strokeStyle='#d9fff735';ctx.stroke();
   }
   ctx.restore();
   requestAnimationFrame(frame);
 }
 requestAnimationFrame(frame);
}
function voiceState(state){
 const meter=$('#micLevel');if(meter){meter.hidden=state!=='listening';if(state!=='listening')meter.value=0;}
 voicePhase=state;$('#dialog').dataset.state=state;$('#voiceStatus').textContent=t(state);
}
function drawWave(analyser){
 const canvas=$('#waveform'), context=canvas.getContext('2d'), samples=new Float32Array(analyser.fftSize);
 const start=performance.now(), reduced=matchMedia('(prefers-reduced-motion: reduce)').matches;
 function frame(now){
   const width=Math.max(1,canvas.clientWidth), height=Math.max(1,canvas.clientHeight), dpr=devicePixelRatio||1;
   if(canvas.width!==Math.round(width*dpr)||canvas.height!==Math.round(height*dpr)){
     canvas.width=Math.round(width*dpr);canvas.height=Math.round(height*dpr);
   }
   context.setTransform(dpr,0,0,dpr,0,0);context.clearRect(0,0,width,height);
   analyser.getFloatTimeDomainData(samples);
   const rms=Math.sqrt(samples.reduce((n,x)=>n+x*x,0)/samples.length);
   orbLevel=Math.min(1,rms*6);const meter=$('#micLevel');if(meter)meter.value=Math.min(100,rms*500);
   const amplitude=(.035+Math.min(.42,rms*4))*height;
   const seconds=Math.floor((now-start)/1000);
   $('#voiceTimer').textContent='00:'+String(seconds).padStart(2,'0');
   ['#10b981','#8b5cf6','#c4b5fd','#64f5cc'].forEach((color,i)=>{
     context.beginPath();context.strokeStyle=color;context.globalAlpha=.85-i*.14;context.lineWidth=2;
     for(let x=0;x<=width;x+=2){
       const u=x/width, envelope=Math.sin(Math.PI*u)**1.5;
       const y=height/2+envelope*amplitude*Math.sin(u*(14+i*3)+(reduced?0:now/170)+i);
       if(x===0)context.moveTo(x,y);else context.lineTo(x,y);
     }
     context.stroke();
   });
   waveFrame=requestAnimationFrame(frame);
 }
 waveFrame=requestAnimationFrame(frame);
}
let cognitiveEngine='', speechEngine='';
function renderEngines(){
 const badge=$('#voiceEngine');
 badge.textContent=[cognitiveEngine,speechEngine].filter(Boolean).join(' · ');
 badge.hidden=!badge.textContent;
}
function setSpeechEngine(engine){
 speechEngine=({elevenlabs:'ElevenLabs',piper:'Piper Offline',pyttsx3:'Windows Offline','web-speech-local':'Web Speech'})[engine]||'';
 renderEngines();
}
async function play(text,locale=lang){
 if(!$('#read').checked)return;
 voiceState('speaking');
 try{
   const body=JSON.stringify({text,lang:locale});
   let spoken=false;
   try{
     const response=await fetch('/api/voice/speak',{method:'POST',cache:'no-store',
        headers:{'Content-Type':'application/json'},body});
     if(response.ok&&/^audio\/(wav|mpeg)(?:;|$)/i.test(response.headers.get('Content-Type')||'')){
       const engine=response.headers.get('X-Voice-Engine');
       const url=URL.createObjectURL(await response.blob());
       const audio=new Audio(url);
       let source, analyser;
       if(speechContext?.state==='running'){
         source=speechContext.createMediaElementSource(audio);
         analyser=speechContext.createAnalyser();
         analyser.fftSize=512;source.connect(analyser);analyser.connect(speechContext.destination);
         speakingAnalyser={node:analyser,samples:new Float32Array(analyser.fftSize)};
       }
       try{
         await new Promise((resolve,reject)=>{
           audio.addEventListener('ended',resolve,{once:true});
           audio.addEventListener('error',reject,{once:true});
           audio.play().catch(reject);
         });
         spoken=true;setSpeechEngine(engine);
       }catch{
         const fallback=await api('voice/speak?desktop_only=true','POST',{text,lang:locale});
         spoken=fallback.spoken;if(spoken)setSpeechEngine(fallback.engine);
       }finally{source?.disconnect();analyser?.disconnect();speakingAnalyser=null;URL.revokeObjectURL(url);}
     }else if(response.ok){const result=await response.json();spoken=result.spoken;if(spoken)setSpeechEngine(result.engine);}
   }catch(error){console.debug('Local server audio unavailable',error);}
   if(spoken)return;
   const synth=window.speechSynthesis;
   const voices=synth?.getVoices().filter(v=>v.localService)||[];
   const local=voices.find(v=>v.lang.toLowerCase().startsWith(locale))||voices[0];
   if(local){setSpeechEngine('web-speech-local');await new Promise(resolve=>{
     const utterance=new SpeechSynthesisUtterance(text);utterance.voice=local;utterance.lang=local.lang;
     utterance.onend=resolve;utterance.onerror=resolve;synth.speak(utterance);
   });}
 }finally{voiceState('idle');}
}
let attachedPhoto=null;
async function preparePhoto(file){
 if(!file?.type.startsWith('image/')||file.size>18_000_000)throw new Error(t('invalidPhoto'));
 const bitmap=await createImageBitmap(file);
 try{
   const canvas=document.createElement('canvas');
   const scale=Math.min(1,1024/Math.max(bitmap.width,bitmap.height));
   canvas.width=Math.max(1,Math.round(bitmap.width*scale));canvas.height=Math.max(1,Math.round(bitmap.height*scale));
   const ctx=canvas.getContext('2d',{alpha:false});
   ctx.drawImage(bitmap,0,0,canvas.width,canvas.height);
   const src=canvas.toDataURL('image/jpeg',.78);
   const thumbnail=document.createElement('canvas');
   const shrink=Math.min(1,128/Math.max(canvas.width,canvas.height));
   thumbnail.width=Math.max(1,Math.round(canvas.width*shrink));thumbnail.height=Math.max(1,Math.round(canvas.height*shrink));
   const sample=thumbnail.getContext('2d',{willReadFrequently:true});
   sample.drawImage(canvas,0,0,thumbnail.width,thumbnail.height);
   const rgba=sample.getImageData(0,0,thumbnail.width,thumbnail.height).data;
   const channels=[];
   for(let i=0;i<rgba.length;i+=4)channels.push(String.fromCharCode(rgba[i],rgba[i+1],rgba[i+2]));
   return {src,jpeg:src.split(',')[1],rgb:btoa(channels.join('')),width:thumbnail.width,height:thumbnail.height};
 }finally{bitmap.close();}
}
function displayPhoto(photo){
 attachedPhoto=photo;
 $('#photoTray').hidden=!photo;
 if(photo)$('#photoPreview').src=photo.src;else $('#photoPreview').removeAttribute('src');
 $('#ask').elements.text.required=!photo;
}
async function ask(text,turnLanguage=lang){
 voiceState('thinking');
 bubble(text,true);
 try{
   const photo=attachedPhoto;
   const response=photo?await api('ai/vision','POST',{text,lang:turnLanguage,jpeg:photo.jpeg,rgb:photo.rgb,width:photo.width,height:photo.height}):
      await api('ai/ask','POST',{text,lang:turnLanguage});
   if(photo&&attachedPhoto===photo)displayPhoto(null);
   await applyAgentResponse(response,turnLanguage);
   if(response.image_metrics)showVisionMetrics(response.image_metrics);
 }
 finally{if(voicePhase==='thinking')voiceState('idle');}
}
async function directAction(path,body){
 if(busy)return;
 busy=true;voiceState('thinking');
 try{await applyAgentResponse(await api(path+'?lang='+encodeURIComponent(lang),'POST',body),lang);}
 finally{busy=false;if(voicePhase==='thinking')voiceState('idle');}
}
function animateRelay(packet){
 const node=$('#mesh'),bytes=$('#relayBytes');
 if(!node||!bytes)return;
 bytes.textContent=packet.bytes+' B';
 node.classList.remove('run');void node.offsetWidth;node.classList.add('run');
}
async function applyAgentResponse(response,turnLanguage){
 const previous=field;field=response.updated_state;
 if(response.engine){
   cognitiveEngine=response.engine.startsWith('ollama:')?'Ollama '+response.engine.slice(7).replace(/:latest$/,''):
     response.engine.startsWith('ollama-vision:')?'Ollama Vision '+response.engine.split(':')[1]:
     response.engine.startsWith('ollama-color:')?'Ollama + HSV':response.engine==='image-color-rules'?'HSV Offline':'Offline Rules';
   speechEngine='';renderEngines();
 }
 const actions=response.actions||[];
 lastAction=actions.at(-1);
 certificate=actions.find(a=>a.tool==='tool_sustainable_passport')?.result;
 const selected=tool=>actions.find(action=>action.tool===tool);
 for(const action of actions){
   const result=action.result;
   if(typeof result.continuous==='boolean')$('#continuous').checked=result.continuous;
   if(result.years){years=result.years;weights=field.priorities;scenario=field.stress;}
   if(result.pump_hours!==undefined)calc=result;
   if(result.score!==undefined)audit=result;
   if(result.cashflow_usd!==undefined&&result.sensitivity)financeMetrics=result;
   if(result.calculations||result.diagnostics){
     diagnostics=result.diagnostics||result;calc=diagnostics.calculations;
   }
   if(action.tool==='tool_mesh_relay'){
     data.outbox=data.outbox.map(p=>p.id===result.id?result:p);
     lastRelay=result;
   }
   if(action.tool==='tool_satellite_demo')month=11;
   if(action.tool==='tool_run_monte_carlo')latestMonteCarlo=result;
   if(action.tool==='tool_rural_infrastructure')latestInfrastructure=result;
 }
 const moved=previous.lat!==field.lat||previous.lon!==field.lon;
 if(previous.crop!==field.crop||previous.hectares!==field.hectares)latestMonteCarlo=null;
 if(moved){latestMonteCarlo=null;latestInfrastructure=null;observations=null;polygon=field.polygon||[];if(!actions.some(a=>a.result.years))years=[];}
 if(actions.length){
   $('#dialog').classList.remove('expanded');
   if(!selected('tool_financial_advisor_and_cashflow')&&(moved||previous.crop!==field.crop||previous.hectares!==field.hectares))financeMetrics=null;
 }
 saveLocal();render();bubble(response.reply);
 const showTelemetry=selected('tool_nasa_impact');
 if(showTelemetry)await openTelemetry(showTelemetry.result);
 else if(actions.length)$('#panel').scrollIntoView({block:'nearest',behavior:'smooth'});
 if(lastRelay&&field.tab==='community')animateRelay(lastRelay);
 const refreshView=(async()=>{
   if(moved||selected('tool_log_field_activity')||selected('tool_send_voice_message_or_order')||selected('tool_execute_playbook')||
      selected('tool_financial_advisor_and_cashflow')||selected('tool_manage_and_rank_buyers'))await refresh();
   if(moved||previous.offline!==field.offline||selected('tool_complete_diagnosis')||selected('tool_satellite_demo'))await sync(true);
   if(selected('tool_update_field')&&!selected('tool_optimize_rotation')&&!selected('tool_rotation_strategy')&&previous.crop!==field.crop&&!moved)await plan();
   await refreshOntology();
   if(!selected('tool_mesh_relay')&&!showTelemetry)render();
 })().catch(error=>notice(t('failed')+' · '+error.message));
 await Promise.all([play(response.reply,turnLanguage),refreshView]);
}
let lastRelay;
async function openTelemetry(result=null){
 const telemetry=result||await api('telemetry');
 const imported=telemetry.readings.imported;
 const names=['NASA POWER','SRTM','USDA NRCS','SoilGrids','Open-Meteo','ERA5','SMAP','GPM IMERG','ECOSTRESS'];
 const readings={
   'NASA POWER':telemetry.readings.power,SRTM:telemetry.readings.terrain,
   'USDA NRCS':Object.fromEntries(Object.entries(telemetry.readings.soil).filter(([,v])=>v.source==='USDA NRCS')),
   SoilGrids:Object.fromEntries(Object.entries(telemetry.readings.soil).filter(([,v])=>v.source==='SoilGrids')),
   'Open-Meteo':telemetry.readings.forecast,ERA5:telemetry.readings.era5_recent};
 const active=observations?.sources||{};
 const list=names.map(name=>{
   const sample=imported[name]||readings[name], state=imported[name]?'imported':active[name]?.status||telemetry.statuses[name]?.status||'unavailable';
   const stamp=imported[name]?.observed||active[name]?.fetched||telemetry.statuses[name]?.fetched;
   return '<details><summary>'+esc(name)+' · '+t(state)+' '+esc(stamp||'')+'</summary><pre>'+esc(JSON.stringify(sample??{},null,2))+'</pre></details>';
 }).join('');
 const i=telemetry.impact,mesh=telemetry.mesh;
 $('#telemetryContent').innerHTML='<p>'+t('ref')+'</p><div class="grid">'+
   [[t('avoidedLoss'),usd(i.avoided_loss_usd)+' · '+t('modeled')],[t('waterImpact'),pretty(i.water_saved_m3)+' m³'],[t('nitrogenImpact'),pretty(i.nitrogen_kg)+' kg N · '+pretty(i.urea_bags)+' '+t('urea')],
   [t('carbonImpact'),pretty(i.carbon_t_co2e)+' t CO₂e'],[t('mesh'),mesh.packets+' · '+mesh.largest_packet+' / 159 B']].map(([title,value])=>
     '<div class="tile"><strong>'+esc(title)+'</strong><p>'+esc(value)+'</p></div>').join('')+'</div>'+
   '<p class="muted">'+t('avoidedLossBasis')+'</p>'+
   (telemetry.demo?'<p class="pill">'+t('demoReading')+' · ECOSTRESS 41.2 °C · SMAP 13%</p>':'')+list;
 if(!$('#telemetryDialog').open)$('#telemetryDialog').showModal();
}
function wav(samples,rate){
 let peak=0;
 for(const sample of samples)if(Number.isFinite(sample))peak=Math.max(peak,Math.abs(sample));
 const gain=peak>=.005?.85/peak:1;
 const count=Math.floor(samples.length*16000/rate), bytes=new ArrayBuffer(44+count*2), v=new DataView(bytes);
 const str=(offset,s)=>[...s].forEach((c,i)=>v.setUint8(offset+i,c.charCodeAt(0)));
 str(0,'RIFF');v.setUint32(4,36+count*2,true);str(8,'WAVE');str(12,'fmt ');v.setUint32(16,16,true);
 v.setUint16(20,1,true);v.setUint16(22,1,true);v.setUint32(24,16000,true);v.setUint32(28,32000,true);
 v.setUint16(32,2,true);v.setUint16(34,16,true);str(36,'data');v.setUint32(40,count*2,true);
 for(let i=0;i<count;i++){
   const start=Math.floor(i*rate/16000),end=Math.min(samples.length,Math.max(start+1,Math.floor((i+1)*rate/16000)));
   let sum=0;for(let j=start;j<end;j++)sum+=samples[j];
   v.setInt16(44+i*2,Math.max(-32768,Math.min(32767,Math.round(sum/(end-start)*gain*32768))),true);
 }
 return bytes;
}
const SILENCE_MS=2000, MAX_RECORD_MS=23000;
async function startRecording(){
 let stream;
 try{
   if(!navigator.mediaDevices?.getUserMedia)throw new Error(t('microphoneUnavailable'));
   stream=await navigator.mediaDevices.getUserMedia({audio:true});
 }catch(error){throw new Error((error.name==='NotAllowedError'||error.name==='SecurityError')?t('microphonePermission'):error.message);}
 const ctx=new AudioContext();
 try{
   await ctx.resume();
   const source=ctx.createMediaStreamSource(stream);
   const node=ctx.createScriptProcessor(4096,1,1);
   const analyser=ctx.createAnalyser();analyser.fftSize=512;
   const now=performance.now();
   const current={ctx,stream,source,node,analyser,chunks:[],locale:lang,started:now,lastSound:now,heard:false,length:0,timer:null};
   recording=current;
   current.parallelSpeech=captureBrowserSpeech(current.locale);
   const observe=(input,time)=>{
     const rms=Math.sqrt(input.reduce((sum,x)=>sum+x*x,0)/input.length);
     if(rms>=.002){current.heard=true;current.lastSound=time;}
   };
   node.onaudioprocess=e=>{
     if(recording!==current)return;
     const input=e.inputBuffer.getChannelData(0);
     current.chunks.push(new Float32Array(input));
     current.length+=input.length;
     observe(input,performance.now());
   };
   source.connect(analyser);source.connect(node);node.connect(ctx.destination);
   const volume=new Float32Array(analyser.fftSize);
   current.timer=setInterval(()=>{
     if(recording!==current)return;
     const time=performance.now();
     analyser.getFloatTimeDomainData(volume);
     observe(volume,time);
     if(time-current.started>=MAX_RECORD_MS||(current.heard&&time-current.lastSound>=SILENCE_MS)||
        (!current.length&&time-current.started>=4500)){
       void guard(stopRecording)();
     }
   },100);
   for(const track of stream.getAudioTracks()){
     track.addEventListener('ended',()=>{if(recording===current)void guard(stopRecording)();},{once:true});
   }
   notice('');$('#mic').classList.add('recording');voiceState('listening');drawWave(analyser);
 }catch(error){
   if(recording?.ctx===ctx){clearInterval(recording.timer);recording.parallelSpeech?.abort();recording=null;}
   stream.getTracks().forEach(track=>track.stop());ctx.close().catch(()=>{});throw error;
 }
}
function captureBrowserSpeech(locale){
 const Recognition=window.SpeechRecognition||window.webkitSpeechRecognition;
 if(!Recognition)return null;
 const recognizer=new Recognition();
 recognizer.lang=({en:'en-US',es:'es-ES',pt:'pt-BR'})[locale];
 recognizer.interimResults=true;recognizer.continuous=true;
 if(field.offline||!navigator.onLine){
   if(!('processLocally' in recognizer))return null;
   recognizer.processLocally=true;
 }
 const finalized=new Map();
 let interim='',finished=false;
 let finish;
 const ended=new Promise(resolve=>{finish=resolve;});
 const done=()=>{finished=true;finish();};
 recognizer.onresult=event=>{
   const partial=[];
   Array.from(event.results).forEach((result,index)=>{
     const text=result[0]?.transcript?.trim();
     if(!text)return;
     if(result.isFinal)finalized.set(index,text);
     else partial.push(text);
   });
   interim=partial.join(' ');
 };
 recognizer.onend=done;
 recognizer.onerror=done;
 try{recognizer.start();}catch{done();return null;}
 return {
   stop(){if(!finished)try{recognizer.stop();}catch{done();}},
   abort(){if(!finished)try{recognizer.abort();}catch{done();}},
   async transcript(){
     if(!finalized.size&&!interim&&!finished)await Promise.race([ended,new Promise(resolve=>setTimeout(resolve,400))]);
     return [...Array.from(finalized).sort(([a],[b])=>a-b).map(([,value])=>value),interim].filter(Boolean).join(' ');
   }
 };
}
async function stopRecording(){
 if(!recording)return;
 const {ctx,stream,source,node,analyser,chunks,length,locale,timer,parallelSpeech}=recording, rate=ctx.sampleRate;
 recording=null;busy=true;clearInterval(timer);cancelAnimationFrame(waveFrame);voiceState('transcribing');
 $('#mic').classList.remove('recording');
 try{
   parallelSpeech?.stop();
   node.onaudioprocess=null;source.disconnect();node.disconnect();analyser?.disconnect();
   stream.getTracks().forEach(track=>track.stop());ctx.close().catch(()=>{});
   const samples=new Float32Array(length);let offset=0;
   for(const chunk of chunks){samples.set(chunk,offset);offset+=chunk.length;}
   let text='';
   if(length){
     try{
       const result=await api('voice/transcribe?lang='+encodeURIComponent(locale),'POST',wav(samples,rate),AbortSignal.timeout(12000));
       text=result.text?.trim()||'';
     }catch{}
   }
   if(!text)text=(await parallelSpeech?.transcript())||'';
   if(!text){
     const previous=$('#notice').textContent;
     notice([previous,t('singleCaptureEmpty')].filter(Boolean).join(' · '));voiceState('idle');return;
   }
   notice('');await ask(text,locale);
   if($('#continuous').checked)await startRecording();
 }finally{parallelSpeech?.abort();busy=false;if(!recording)voiceState('idle');}
}
async function briefing(silent=false){
 const result=await api('voice/briefing?lang='+lang);
 if(!silent||result.critical){bubble(result.reply);if(!busy&&!recording)await play(result.reply);}
}
async function microphone(){
 $('#dialog').hidden=false;
 if(recording){await stopRecording();return;}
 if(busy)return;
 busy=true;
 try{
   await startRecording();
 }finally{busy=false;}
}
bind('#mic','click',microphone);bind('#heroMic','click',microphone);
const chipItems=[
 ['🌱','quickRotation',['Optimize a five-year rotation','Optimiza una rotación de cinco años','Otimize uma rotação de cinco anos']],
 ['💧','quickIrrigation',['Calculate irrigation and NPK','Calcula riego y NPK','Calcule irrigação e NPK']],
 ['📊','profitAndRisk',['Run a Monte Carlo risk simulation','Ejecuta Monte Carlo','Execute Monte Carlo']],
 ['🐄','livestockPanel',['Give me livestock and pasture advice','Dame un diagnóstico de ganado y pasturas','Dê um diagnóstico de gado e pastagem']],
 ['🛰️','quickNASA',['Simulate NASA satellite alert','Simula alerta satelital NASA','Simule alerta de satélite NASA']]
];
function renderVoice(){
 voiceState(voicePhase);
 $('#closeAgent').textContent=t($('#dialog').classList.contains('expanded')?'collapse':'expand');
 $('#chips').innerHTML=chipItems.map(([icon,key],i)=>'<button type="button" data-chip="'+i+'">'+icon+' '+esc(t(key))+'</button>').join('');
 $('#quickResources').innerHTML=resourceHTML();resourceEvents($('#quickSheet'));
 const action=lastAction, result=action?.result||{};
 const items=result.years?.map(y=>cropName(y.crop))||
  (result.score!==undefined?[result.score+'/100',t('recovery')+' '+usd(result.recovery_usd_ha)+'/ha']:
   result.pump_hours!==undefined?[pretty(result.pump_hours)+' h',pretty(result.npk_bags)+' × 50 kg']:
   result.bytes!==undefined?[result.recipient,result.bytes+' B · '+t('outbox')]:
   result.next_crop?[cropName(result.next_crop),result.rain_mm==null?t('missing'):pretty(result.rain_mm)+' mm']:
   result.comparison?[cropName(result.crop)+' '+usd(result.price)+'/t',cropName(result.comparison.crop)+' '+usd(result.comparison.price)+'/t']:
   result.price!==undefined?[cropName(result.crop),usd(result.price)+'/t',result.moisture==null?'—':pretty(result.moisture)+'%']:
   result.cashflow_usd!==undefined?[t('netCash')+' '+usd(result.cashflow_usd),t('margin')+' '+usd(result.projected_margin_usd)]:
   result.buyers?.length?[result.buyers[0].name,usd(result.buyers[0].net)+'/t']:
   result.qr?[result.id,t(result.rotation.status)]:
   result.satellite_demo?[t('demoReading'),pretty(result.satellite_demo.lst_c)+' °C · '+pretty(result.satellite_demo.root_m3_m3*100)+'%']:
   result.calculations?[t('soilChemistry')+' pH '+pretty(result.ph),t('hours')+' '+pretty(result.calculations.pump_hours)]:
   result.status==='relayed'?[t('relayed'),result.bytes+' B']:
   result.forecast_hours?[t('spray'),result.family]:
   result.cost_usd!==undefined?[t('activity'),usd(result.cost_usd)]:
   result.critical!==undefined?[t('scienceBrief'),result.brief?.date||'—']:
   result.offline!==undefined?[t(result.offline?'offline':'local')]:
   result.hectares!==undefined?[pretty(result.hectares)+' ha',result.location_name]:
   result.names?[result.names[lang],result.family,result.sow+' cm']:[]);
 $('#voiceResults').innerHTML=items.length?'<div class="tile">'+items.map(esc).join(' · ')+
  (action?.tab?'<button type="button" id="resultTab">'+esc(t(action.tab))+'</button>':'')+'</div>':'';
 bind('#resultTab','click',()=>openTab(action.tab));
}
async function openTab(tab){
 $('#dialog').classList.remove('expanded');
 await patch({tab});
 $('#tabs').scrollIntoView({block:'center'});
}
function showVisionMetrics(metrics){
 const result=$('#voiceResults'), tile=document.createElement('div');tile.className='tile';
 tile.textContent=t('photoFractions')+' · '+t('photoGreen')+' '+pretty(metrics.green_pct)+'% · '+t('photoYellow')+' '+pretty(metrics.yellow_pct)+'% · '+t('photoBrown')+' '+pretty(metrics.brown_pct)+'%';
 result.append(tile);
}
bind('#executePlaybook','click',()=>directAction('playbook/execute'));
bind('#photoButton','click',()=>$('#photoFile').click());
bind('#photoFile','change',async e=>{const file=e.target.files[0];e.target.value='';if(file)displayPhoto(await preparePhoto(file));});
bind('#photoRemove','click',()=>displayPhoto(null));
bind('#solarTheme','click',()=>{
 const active=document.documentElement.classList.toggle('theme-field');
 localStorage.setItem('agro-theme',active?'field':'dark');
 $('#solarTheme').setAttribute('aria-pressed',String(active));
});
const sunlight=localStorage.getItem('agro-theme')==='field';
document.documentElement.classList.toggle('theme-field',sunlight);
$('#solarTheme').setAttribute('aria-pressed',String(sunlight));
bind('#impactButton','click',()=>openTelemetry());
bind('#demoAlert','click',async()=>{$('#telemetryDialog').close();await directAction('demo/alert');});
bind('#workspaceMenu','click',()=>{$('#dialog').classList.remove('expanded');renderVoice();$('#tabs').scrollIntoView({block:'center'});});
bind('#closeAgent','click',()=>{$('#dialog').classList.toggle('expanded');renderVoice();});
bind('#quickOpen','click',()=>$('#quickSheet').showModal());
bind('#quickMap','click',()=>openTab('map'));
bind('#chips','click',async e=>{
 const chip=e.target.closest('[data-chip]');if(!chip||busy)return;
 const action=chipItems[Number(chip.dataset.chip)][2];
 if(Array.isArray(action)){busy=true;try{await ask(action[['en','es','pt'].indexOf(lang)]);}finally{busy=false;}}
 else await openTab(action);
});
bind('#ask','submit',async e=>{e.preventDefault();const text=values(e.target).text.trim()||(attachedPhoto?t('photoQuestion'):'');if(text&&!busy){e.target.reset();busy=true;try{await ask(text);}finally{busy=false;}}});
$$('[data-lang]').forEach(b=>b.onclick=()=>{lang=b.dataset.lang;render();});
bind('#mode','click',()=>patch({mode:field.mode==='farmer'?'agronomist':'farmer'}));
bind('#offline','change',async e=>{weatherRun++;await patch({offline:e.target.checked});await sync();});
bind('#sync','click',()=>sync());
bind('#tabs','click',async e=>{const tab=e.target.dataset.tab;if(!tab)return;if(server){await patch({tab});}else{field.tab=tab;render();}});
bind('#fieldForm','submit',async e=>{
 e.preventDefault();
 const v=values(e.target);
 const crop=v.crop==='__custom'?prompt(t('custom'))?.trim():v.crop;
 if(!crop)return;
 const changed=field.crop!==crop;
 await patch({crop,hectares:+v.hectares,operation_type:v.operation_type,livestock_heads:+v.livestock_heads});
 if(changed){years=[];await plan();}
});
async function setPlace(place){
 weatherRun++;observations=null;localStorage.removeItem('agro-observations');polygon=[];
 await patch({...place,polygon});$('#places').replaceChildren();await sync(true);
 if(field.tab==='diagnostics')$('#panel').scrollIntoView({block:'nearest'});
}
bind('#search','submit',async e=>{
 e.preventDefault();const q=values(e.target).q.trim();if(!q)return;
 const places=await api('geocode?q='+encodeURIComponent(q));
 $('#places').replaceChildren();
 if(!places.length)notice(t('noResults'));
 for(const p of places){
   const b=document.createElement('button');
   b.textContent=[p.name,p.admin1,p.country].filter(Boolean).join(', ');
   b.onclick=guard(()=>setPlace({location_name:b.textContent,lat:p.latitude,lon:p.longitude}));
   $('#places').append(b);
 }
});
bind('#gps','click',()=>new Promise((resolve,reject)=>{
 if(!navigator.geolocation){reject(new Error(t('failed')));return;}
 navigator.geolocation.getCurrentPosition(pos=>setPlace({location_name:t('gps'),lat:pos.coords.latitude,lon:pos.coords.longitude}).then(resolve,reject),reject,{timeout:12000});
}));
async function init(){
 render();
 try{
   await refresh();weights=field.priorities||weights;scenario=field.stress||scenario;render();
   await plan(field.sequence||null);
   sync(true).catch(()=>{});
   briefing(true).catch(()=>{});
 }catch{server=false;render();notice(t('backend'));}
}
renderOrb();init();
navigator.serviceWorker?.register('/sw.js').catch(()=>{});

