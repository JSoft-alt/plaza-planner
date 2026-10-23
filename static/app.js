'use strict';
const $ = id => document.getElementById(id);
const state = {discoveryKind:"retail", origin:null, polygon:[], scan:null, tripId:null, selected:new Set(), busy:false,
  bootstrap:null, map:null, overlays:[], draft:null, mapPurpose:'area', drawing:false, moving:false,
  historyOffset:0, scheduleId:null, scheduleConfig:null, embedData:null, embedKey:null, googleChoice:null};
let pollTimer, toastTimer, mapPromise, confirmAction, focusBeforeMap;
let locationRequest = 0;
const coordinateLabel = (lat, lng) => `${Number(lat).toFixed(6)}, ${Number(lng).toFixed(6)}`;
const icon = name => `<svg class="action-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${{share:'<circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><path d="m9 10 6-4M9 14l6 4"/>',locate:'<circle cx="12" cy="12" r="7"/><circle cx="12" cy="12" r="2"/><path d="M12 2v3m0 14v3M2 12h3m14 0h3"/>',route:'<path d="m3 10 18-7-7 18-3-8-8-3Z"/>',map:'<path d="m3 6 6-3 6 3 6-3v15l-6 3-6-3-6 3V6Zm6-3v15m6-12v15"/>'}[name]}</svg>`;
const statusNames = {queued:'等待搜索',running:'搜索中',complete:'已完成',partial:'未完成',failed:'失败',cancelled:'已取消'};
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const dateText = value => value ? new Date(value*1000).toLocaleString('zh-CN',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}) : '—';
function toast(message){$('toast').textContent=message;$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,6000);}
function clearErrors(){document.querySelectorAll('.field-error').forEach(el=>el.textContent='');}
function showError(error, fallback='error-form'){
  if(error.errors){for(const [field,message] of Object.entries(error.errors)){const el=$('error-'+field);if(el)el.textContent=message;}}
  const el=$(fallback);if(el)el.textContent=error.message;
}
async function api(path, options={}){
  let response;
  try{response=await fetch('/api'+path,{...options,headers:{'Content-Type':'application/json','x-planner-csrf':state.bootstrap?.csrf??'',...options.headers}});}
  catch{throw new Error('无法连接本地服务，请检查应用是否运行后重试。');}
  if(!response.ok){let data;try{data=await response.json();}catch{data={detail:'服务暂不可用，请重试。'};}const err=new Error(data.detail||'操作失败，请重试。');err.errors=data.errors;throw err;}
  return response.json();
}
const post = (path, body={}) => api(path,{method:'POST',body:JSON.stringify(body)});
function config(){return {discovery_kind:state.discoveryKind,min_companies:Number($('minCompanies').value),origin:state.origin, mode:document.querySelector('[name=mode]:checked').value,
  diameter_miles:Number($('diameter').value),polygon:state.polygon,min_shops:Number($('minShops').value),
  batch_size:Number($('batchSize').value),baseline:Number($('baseline').value)};}
function validNumbers(c){const minimum=c.discovery_kind==='offices'?(c.min_companies??3):c.min_shops;return Number.isInteger(minimum)&&minimum>=1&&minimum<=100&&c.diameter_miles>=.1&&c.diameter_miles<=30&&Number.isInteger(c.batch_size)&&c.batch_size>=1&&c.batch_size<=15&&c.baseline>=0&&c.baseline<360;}
function discoveryLabel(c){return c.discovery_kind==='offices'?'医院与办公楼':'商店与商业区';}
function thresholdText(c){return c.discovery_kind==='offices'?`每楼 / 办公区最少 ${c.min_companies??3} 家公司 / 机构`:`最少 ${c.min_shops} 家小店`;}
function syncDiscoveryKind(c){
  const offices=c.discovery_kind==='offices';
  document.querySelectorAll('[data-kind]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.kind===c.discovery_kind)));
  $('minShopsGroup').hidden=offices;$('minCompaniesGroup').hidden=!offices;
  $('minShops').disabled=offices;$('minCompanies').disabled=!offices;
  $('search').textContent=offices?'搜索并保存办公 / 医疗地点':'搜索并保存全部商业区';
  $('plannerDescription').textContent=offices?'找到医院、牙科、律师事务所与公司所在的楼宇或办公区，按方位分趟。':'找到合适的商业区，按方位分趟，再计算驾驶路线。';
  $('discoveryCoverage').textContent=offices?'按每栋楼或办公区内已映射的公司 / 医疗机构数筛选，不按距离聚成一簇。独立医院或诊所计为 1 家；设为 1 可纳入。地图可能缺少租户信息。':'依据已映射的小店数量筛选。地图数据可能缺失，结果不代表全部实体商户。';
  $('emptyDescription').textContent=offices?'确认起点与范围后，符合公司数量要求的楼宇、办公区与医疗机构会排在这里。':'确认地址并设定范围，符合条件的商业区会按方位依次排在这里。';
  $('emptySearchLabel').textContent=offices?'搜索办公 / 医疗地点':'搜索商业区';
  $('resultUnit').textContent=offices?'个办公 / 医疗地点':'个商业区';
  $('zeroResults').textContent=offices?'未找到满足公司数量要求的已映射楼宇或办公区。可将最少公司数设为 1 以纳入独立医院 / 诊所，或缩小范围后重试。':'未找到满足条件的已映射商业区。可降低小店数或扩大范围后重新搜索。';
}
function canSearch(){const c=config();return c.origin?.confirmed&&validNumbers(c)&&(c.mode==='circle'||(c.polygon.length>=3&&mapGeometry.covers(c.polygon,c.origin.lng,c.origin.lat)))&&!state.busy&&!['queued','running'].includes(state.scan?.status);}
function persist(){try{localStorage.setItem('plaza-planner-draft',JSON.stringify({config:config(),address:$('address').value,scanId:state.scan?.id,tripId:state.tripId}));}catch{/* Storage may be disabled; server history remains durable. */}}
function syncForm(){
  const c=config();syncDiscoveryKind(c);$('diameterGroup').hidden=c.mode==='polygon';$('areaSummary').hidden=c.mode!=='polygon';
  $('areaSummary').textContent=c.polygon.length?`已设定 ${c.polygon.length} 个顶点。搜索以边界为准，保留的直径不参与搜索。`:'请在地图中绘制边界；直径不参与搜索。';
  $('originSummary').hidden=!c.origin?.confirmed;$('originSummary').textContent=c.origin?`已确认：${c.origin.label}`:'';
  if(c.mode==='polygon'&&c.origin&&c.polygon.length>=3&&!mapGeometry.covers(c.polygon,c.origin.lng,c.origin.lat))$('error-polygon').textContent='起点必须位于绘制区域内或边界上，请移动起点或修改边界。';
  else if($('error-polygon').textContent.startsWith('起点必须'))$('error-polygon').textContent='';
  $('search').disabled=!canSearch();persist();queueOriginRoadData();
}
function validateNumberInput(id){
  const fields={diameter:['diameter_miles','直径须为 0.1–30 英里。'],minShops:['min_shops','请输入 1–100 的整数。'],minCompanies:['min_companies','请输入 1–100 的整数。'],batchSize:['batch_size','每趟须为 1–15 个新地点。'],baseline:['baseline','起始角须为 0–359.99 度。']};
  if(fields[id]){const [field,message]=fields[id];const el=$(id);$('error-'+field).textContent=el.value!==''&&el.checkValidity()?'':message;}
}
function restoreConfig(c){
  state.discoveryKind=c.discovery_kind??"retail";$('minCompanies').value=c.min_companies??3;state.origin=c.origin;state.polygon=c.polygon??[];$('address').value=c.origin?.label??'';
  document.querySelector(`[name=mode][value=${c.mode}]`).checked=true;
  $('diameter').value=c.diameter_miles;$('minShops').value=c.min_shops;$('batchSize').value=c.batch_size;$('baseline').value=c.baseline;
  syncForm();
}
async function lookupAddress(input, target, onChoose, provider='census') {
  const value = input.value.trim();
  if (value.length < 2) { target.textContent = '请补充完整地址或公开地点名称。'; return; }
  // Each click is intentional. No keystroke queries or automatic provider fallback.
  if (target.dataset.loading) return;
  target.dataset.loading = '1';
  target.textContent = '正在查找…';
  try {
    const {matches, attribution} = await post('/address', {address: value, provider});
    target.replaceChildren();
    const note = document.createElement('p'); note.className = 'match-note';
    note.textContent = matches.length ? '选择匹配的起点；位置不准时可在地图中校正。' : '未找到匹配。请补充城市、邮编，或在地图中放置起点。';
    target.append(note);
    for (const match of matches) {
      const button = document.createElement('button'); button.type = 'button'; button.className = 'match';
      const label = document.createElement('span'); label.textContent = match.label;
      const precision = document.createElement('small'); precision.textContent = match.precision;
      button.append(label, precision);
      button.onclick = () => onChoose({label: match.label, lat: match.lat, lng: match.lng, source: 'address', confirmed: true, accuracy_m: null});
      target.append(button);
    }
    const credit = document.createElement('p'); credit.className = 'match-note'; credit.textContent = attribution; target.append(credit);
  } catch (error) { target.textContent = error.message; }
  finally { delete target.dataset.loading; }
}
function publicLookup(input, target, onChoose) {
  target.replaceChildren();
  const note = document.createElement('p'); note.className = 'match-note';
  note.append('使用 OpenStreetMap 查询公开地点和地址；请勿提交私人地址或敏感位置。');
  const policy = document.createElement('a'); policy.href = 'https://operations.osmfoundation.org/policies/nominatim/';
  policy.target = '_blank'; policy.rel = 'noopener'; policy.textContent = ' 使用政策'; note.append(policy);
  const button = document.createElement('button'); button.type = 'button'; button.className = 'match'; button.textContent = '查询此公开地点';
  button.onclick = () => lookupAddress(input, target, onChoose, 'nominatim');
  target.append(note, button);
}

function newSearch(){state.routeToken=null;state.embedData=null;state.embedKey=null;state.googleChoice=null;$('googlePreview').hidden=true;clearTimeout(pollTimer);state.scan=null;state.tripId=null;state.selected.clear();state.busy=false;$('empty').hidden=false;$('scanResults').hidden=true;syncForm();view('planner');}
async function view(name){
  document.querySelectorAll('.view').forEach(el=>el.hidden=el.id!==name);
  document.querySelectorAll('.nav').forEach(el=>{el.classList.toggle('active',el.dataset.view===name);el.setAttribute('aria-current',el.dataset.view===name?'page':'false');});
  try{if(name==='history')await loadHistory();if(name==='schedules')await loadSchedules();}catch(error){toast(error.message);}
}
async function poll(){
  clearTimeout(pollTimer);if(!state.scan)return;
  try{const id=state.scan.id;const result=await api('/scans/'+id);state.lastPollAt=Date.now();if(state.scan?.id!==id)return;state.scan=result;renderResults();
    if(['queued','running'].includes(result.status))pollTimer=setTimeout(poll,1500);
  }catch(error){toast(error.message);pollTimer=setTimeout(poll,5000);}
}
async function reopen(id){
  state.routeToken=null;state.busy=false;clearTimeout(pollTimer);const rec=await api('/scans/'+id);state.scan=rec;state.selected.clear();
  if(!rec.trips.some(t=>t.id===state.tripId))state.tripId=rec.trips.at(-1)?.id??null;
  restoreConfig(rec.config);renderResults();view('planner');persist();
  if(['queued','running'].includes(rec.status))poll();
}
function searchFeedback(scan,now=Date.now()/1000,lastPollAt=null){
  const elapsed=Math.max(0,Math.floor(now-(scan.run_started_at??scan.created)));
  const time=`${Math.floor(elapsed/60)}:${String(elapsed%60).padStart(2,'0')}`;
  const a=scan.activity??{},phaseAge=Math.max(0,Math.floor(now-(a.phase_started_at??scan.run_started_at??scan.created)));
  const completed=scan.progress?` · 已完成 ${scan.progress.done}/${scan.progress.total} 个区域`:'';
  let stage=scan.status==='queued'?'等待搜索任务开始':scan.message;
  if(a.phase==='query')stage=`${a.attempt>1?'主服务不可用，正在尝试备用服务':'等待地图服务响应'} · ${a.provider??''}（第 ${a.attempt??1}/${a.attempts??1} 次请求）`;
  if(a.phase==='download')stage=`接收地图数据 · ${Math.round((a.bytes??0)/1024)} KB`;
  if(a.phase==='cache')stage=`复用 ${Math.max(1,Math.ceil((a.age_seconds??0)/60))} 分钟内的地图缓存`;
  if(a.phase==='filter')stage=`处理 ${a.elements??0} 个地图要素，筛选${scan.config?.discovery_kind==='offices'?'办公 / 医疗地点':'商业区'}并去重`;
  if(scan.cancel)stage='正在取消；当前网络请求结束后停止，不会再查询下一区域或备用服务。';
  else if(lastPollAt&&now-lastPollAt>10)stage='与本地应用的连接暂时中断，正在重连；已保存的搜索会保留。';
  else if(phaseAge>=20&&(!a.phase||a.phase==='query'))stage+=` · 本次服务已等待 ${phaseAge} 秒，响应较慢，可取消或继续等待`;
  return `已用 ${time}${completed} · ${stage}`;
}
function updateSearchFeedback(){
  if(!state.scan||!['queued','running'].includes(state.scan.status))return;
  $('searchTiming').textContent=searchFeedback(state.scan,Date.now()/1000,state.lastPollAt?state.lastPollAt/1000:null);
}
setInterval(updateSearchFeedback,1000);
function currentTrip(){return state.scan?.trips?.find(t=>t.id===state.tripId);}
function renderResults(){
  const s=state.scan;if(!s)return;$('empty').hidden=true;$('scanResults').hidden=false;
  const active=['queued','running'].includes(s.status);$('activeProgress').hidden=!active;
  $('progressText').textContent=s.message;$('cancelScan').disabled=s.cancel;$('cancelScan').textContent=s.cancel?'正在取消…':'取消搜索';
  if(s.progress&&!['query','download','filter'].includes(s.activity?.phase)){$('progress').max=s.progress.total;$('progress').value=s.progress.done;}else{$('progress').removeAttribute('value');}updateSearchFeedback();
  $('scanNotice').hidden=active||(s.status==='complete'&&!s.export_error);
  $('scanMessage').textContent=s.export_error||`${statusNames[s.status]}：${s.message}`;
  $('retryScan').hidden=s.status==='complete';$('retryScan').disabled=state.busy;
  $('resultCount').textContent=s.plazas.length;$('scanLabel').textContent=`${s.config.origin.label} · ${boundaryText(s.config)} · ${dateText(s.created)}${s.elapsed_seconds!=null?` · 耗时 ${Math.round(s.elapsed_seconds)} 秒`:''}`;
  $('tripSelect').innerHTML=s.trips.length?s.trips.map(t=>`<option value="${t.id}">第 ${t.number} 趟 · ${t.plaza_ids.length} 个地点${t.route?' · 已计算':''}</option>`).join(''):'<option value="">尚未分趟</option>';
  if(!s.trips.some(t=>t.id===state.tripId))state.tripId=s.trips.at(-1)?.id??null;
  $('tripSelect').value=state.tripId??'';$('tripSelect').disabled=state.busy;
  const assigned=new Set(s.trips.flatMap(t=>t.plaza_ids));
  const unfinished=s.trips.at(-1)&&(!s.trips.at(-1).route||(s.trips.at(-1).number>1&&!s.trips.at(-1).route.start));
  $('nextTrip').disabled=state.busy||s.status!=='complete'||(assigned.size===s.plazas.length&&!unfinished);
  $('nextTrip').textContent=unfinished?'继续当前趟':'取下一趟';
  const t=currentTrip();$('tripPanel').hidden=!t;
  if(t){
    const frozen=s.trips.some(next=>next.number>t.number&&next.start);
    const stale=t.number>1&&t.route&&!t.route.start;
    const start=t.start??t.route?.start??s.config.origin;
    $('tripTitle').textContent=`第 ${t.number} 趟 · ${t.plaza_ids.length} 个新地点`;
    $('tripDescription').textContent=`${start.previous_trip_number?`接续第 ${start.previous_trip_number} 趟终点 + `:'已确认起点 + '}${t.plaza_ids.length} 个新地点 · 队列 ${t.plaza_ids.map(id=>s.plazas.find(p=>p.id===id)?.rank).join('、')}${frozen?' · 后续行程已接续，路线已锁定':''}`;
    $('calculate').disabled=state.busy||frozen;$('calculate').textContent=state.busy?'正在计算…':frozen?'路线已锁定':t.route?'重新计算路线':'计算路线';
    $('returnOrigin').checked=t.route?.return_to_origin??false;$('returnOrigin').disabled=state.busy||frozen;
    $('routeError').textContent=t.error??(stale?'旧版路线尚未保存接续起点，请从前一趟起按顺序重新计算。':'');$('routeSummary').hidden=!t.route||stale;
    if(t.route&&!stale)renderRoute(t,start);
  }
  $('plazaList').replaceChildren();
  const times=Object.assign({},...s.trips.filter(t=>t.route).map(t=>Object.fromEntries(Object.entries(t.route.origin_seconds??{}).map(([id,seconds])=>[id,{seconds,number:t.number}]))));
  for(const p of s.plazas){
    const li=document.createElement('li');li.className='plaza'+(t?.plaza_ids.includes(p.id)?' current':'');
    li.innerHTML=`<input type="checkbox" aria-label="在地图中选中 ${escapeHtml(p.name)}" ${state.selected.has(p.id)?'checked':''}><span class="rank">${p.rank}</span><div><h3><button class="place-preview-button" data-place-preview="${escapeHtml(p.id)}">${escapeHtml(p.name)}</button></h3><p>${escapeHtml(p.address)}</p><p class="distance">${s.config.discovery_kind==='offices'?`${({building:'楼宇',office_zone:'办公区',facility:'独立机构 / 医疗设施'})[p.entity_kind]??'办公 / 医疗地点'} · 已映射 ${p.company_count} 家公司 / 机构 · `:''}${p.distance_miles.toFixed(1)} 英里${times[p.id]!=null?` · 第 ${times[p.id].number} 趟起点驾车约 ${Math.ceil(times[p.id].seconds/60)} 分钟`:''}</p></div><button class="icon-button">${icon('share')}分享</button>`;
    li.querySelector('input').onchange=e=>{e.target.checked?state.selected.add(p.id):state.selected.delete(p.id);$('previewSelection').disabled=!state.selected.size;};
    li.querySelector('[data-place-preview]').onclick=()=>selectGooglePlace(p.id);
    li.querySelector('.icon-button').onclick=event=>sharePlaza(p,event.currentTarget);
    $('plazaList').append(li);
  }
  $('previewSelection').disabled=!state.selected.size;$('zeroResults').hidden=active||s.plazas.length>0;syncForm();loadEmbeds();loadCoverageStatus();
}
function renderRoute(trip,start){
  const r=trip.route, byId=Object.fromEntries(state.scan.plazas.map(p=>[p.id,p]));
  const startName=start.name??start.label??coordinateLabel(start.lat,start.lng);
  $('routeSummary').innerHTML=`<div class="route-result"><strong>${Math.ceil(r.duration_seconds/60)} 分钟 <small>· ${(r.distance_meters/1609.344).toFixed(1)} 英里</small></strong><p>${escapeHtml(r.method)} · ${r.return_to_origin?'最后返回本趟起点':'在最后一个新地点结束'}</p><div class="route-start"><span class="stop-number start-number">起</span><div><strong>${escapeHtml(startName)}</strong><small>${start.previous_trip_number?`第 ${start.previous_trip_number} 趟终点，不占新地点名额`:'本趟固定起点'} · ${coordinateLabel(start.lat,start.lng)}</small></div>${start.id?`<button class="icon-button" data-share="${escapeHtml(start.id)}">${icon('share')}分享</button>`:''}</div><ol class="route-stops">${r.order.map((id,i)=>`<li><span class="stop-number">${i+1}</span><div><strong><button class="place-preview-button" data-place-preview="${escapeHtml(id)}">${escapeHtml(byId[id].name)}</button></strong><small>${escapeHtml(byId[id].address)}</small><small>本段 ${Math.ceil(r.legs_seconds[i]/60)} 分钟 · 队列 #${byId[id].rank}</small></div><button class="icon-button" data-share="${escapeHtml(id)}">${icon('share')}分享</button></li>`).join('')}${r.return_to_origin?`<li><span class="stop-number start-number">返</span><div><strong>返回本趟起点</strong><small>本段 ${Math.ceil(r.legs_seconds.at(-1)/60)} 分钟；下一趟从这里接续</small></div></li>`:''}</ol><div class="route-actions"><button id="openGoogleRoute" class="primary icon-button">${icon('route')}在 Google Maps 中打开</button><button id="previewRoute" class="icon-button">${icon('map')}地图预览</button></div><p>${escapeHtml(r.note)}</p></div>`;
  $('previewRoute').textContent='查看 OSRM 原路线';$('previewRoute').onclick=()=>openMap('route');
  $('routeSummary').querySelectorAll('[data-place-preview]').forEach(button=>button.onclick=()=>selectGooglePlace(button.dataset.placePreview));
  const googleButton=document.createElement('button');googleButton.className='icon-button';googleButton.innerHTML=icon('map')+'Google Maps 路线预览';googleButton.onclick=()=>{state.googleChoice='route';renderGooglePreview();$('googlePreview').scrollIntoView({behavior:'auto',block:'start'});};$('routeSummary').querySelector('.route-actions').append(googleButton);
  $('openGoogleRoute').onclick=()=>openGoogleRoute(trip,start);
  $('routeSummary').querySelectorAll('[data-share]').forEach(button=>button.onclick=()=>sharePlaza(byId[button.dataset.share],button));
}
function openGoogleRoute(trip,start){
  const r=trip.route;
  // Current routes store links; derive old first-batch links with the same limits.
  const points=[start,...r.order.map(id=>state.scan.plazas.find(p=>p.id===id)),...(r.return_to_origin?[start]:[])];
  const makeSegments=maxLegs=>Array.from({length:Math.ceil((points.length-1)/maxLegs)},(_,i)=>{
    const from=i*maxLegs,to=Math.min(from+maxLegs,points.length-1);
    const query=new URLSearchParams({api:'1',origin:coordinateLabel(points[from].lat,points[from].lng),destination:coordinateLabel(points[to].lat,points[to].lng),travelmode:'driving'});
    if(to-from>1)query.set('waypoints',points.slice(from+1,to).map(p=>coordinateLabel(p.lat,p.lng)).join('|'));
    return {from_index:from,to_index:to,url:'https://www.google.com/maps/dir/?'+query};
  });
  const links=r.google_maps??{desktop:makeSegments(10),mobile:makeSegments(4),legs:makeSegments(1)};
  $('mapsRouteTitle').textContent=`第 ${trip.number} 趟 · 在 Google Maps 中打开`;
  const name=index=>index===0?'起点':r.return_to_origin&&index===points.length-1?'返回起点':`第 ${index} 站`;
  const render=(target,segments)=>{
    $(target).replaceChildren();
    segments.forEach((part,i)=>{const a=document.createElement('a');a.className='route-link icon-button';a.href=part.url;a.target='_blank';a.rel='noopener noreferrer';a.innerHTML=`${icon('route')}<span>${segments.length===1?'打开本趟全部地点':`第 ${i+1} 段`}<small>${name(part.from_index)} → ${name(part.to_index)}</small></span>`;$(target).append(a);});
  };
  render('mapsDesktop',links.desktop);render('mapsMobile',links.mobile);render('mapsLegs',links.legs);
  $('mapsRouteDialog').showModal();
}
function queueOriginRoadData(){
  const origin=state.origin;if(!origin?.confirmed||!state.bootstrap)return;
  const key=`${origin.lng.toFixed(6)},${origin.lat.toFixed(6)}`;if(key===state.roadOriginKey)return;state.roadOriginKey=key;
  post('/routing/prepare',origin).then(showCoverageStatus).catch(error=>{$('coverageStatus').textContent=error.message;});
}
function showCoverageStatus(c){
  if(!c)return;const seconds=Math.max(0,Math.floor(Date.now()/1000-(c.started_at??Date.now()/1000)));
  const bytes=c.download_bytes!=null?` · 已下载 ${(c.download_bytes/1048576).toFixed(1)} MB${c.total_bytes?` / ${(c.total_bytes/1048576).toFixed(1)} MB`:''}`:'';
  state.coverageJob=c.job_id;const preparing=!['ready','idle','disabled','failed','cancelled'].includes(c.phase);$('cancelCoverage').hidden=!preparing||!c.job_id;$('cancelCoverage').disabled=Boolean(c.cancel_requested);
  $('coverageStatus').textContent=c.cancel_requested&&preparing?'正在停止路网准备；当前网络读取结束后停止。':c.error??`${c.message}${c.region?' · '+c.region:''}${bytes}${!['ready','idle','disabled','failed','cancelled'].includes(c.phase)?` · 已用 ${seconds} 秒，首次准备可能需要数分钟`:''}`;
}
$('cancelCoverage').onclick=async()=>{if(!state.coverageJob)return;try{showCoverageStatus(await post('/routing/cancel',{job_id:state.coverageJob}));}catch(error){toast(error.message);}};
let coverageLoading=false;
async function loadCoverageStatus(){if(coverageLoading||!state.bootstrap)return;coverageLoading=true;try{showCoverageStatus(await api('/routing/coverage'));}catch{}finally{coverageLoading=false;}}
setInterval(loadCoverageStatus,2000);
async function loadEmbeds(){
  const scan=state.scan;if(!scan)return;
  const t=currentTrip(),key=scan.id+':'+(t?.id??'')+':'+JSON.stringify(t?.route?.order??[])+':'+Boolean(t?.route);
  if(key===state.embedKey){renderGooglePreview();return;}state.embedKey=key;state.embedData=null;$('googlePreview').hidden=true;
  try{const data=await api(`/scans/${scan.id}/embeds?`+new URLSearchParams({tid:t?.id??''}));if(state.embedKey!==key)return;state.embedData=data;state.googleChoice=data.route?'route':scan.plazas[0]?.id??'origin';renderGooglePreview();}
  catch(error){if(state.embedKey===key){state.embedKey=null;$('googlePreviewNote').textContent=error.message;}}
}
function selectGooglePlace(id){state.googleChoice=id;renderGooglePreview();$('googlePreview').scrollIntoView({behavior:'auto',block:'start'});}
function renderGooglePreview(){
  const data=state.embedData;if(!data){$('googlePreview').hidden=true;return;}
  const choice=state.googleChoice??'origin',isRoute=choice==='route'&&data.route?.length;
  $('googlePreview').hidden=false;$('googleSegment').hidden=!isRoute||data.route.length<2;
  let url;
  if(isRoute){
    const selection=Number($('googleSegment').value)||0;
    const index=Math.min(selection,data.route.length-1);
    $('googleSegment').innerHTML=data.route.map((part,i)=>`<option value="${i}" ${i===index?'selected':''}>第 ${i+1} 段 · ${part.from_index===0?'起点':part.from_index} → ${part.to_index}</option>`).join('');
    url=data.route[index].url;$('googlePreviewTitle').textContent=`第 ${currentTrip()?.number??''} 趟 · Google Maps 路线`;
    $('googlePreviewNote').textContent='按已优化的站点顺序显示；道路由 Google 重新规划，完整站点顺序见下方列表。';
  }else{
    url=data.places[choice]??data.origin;const place=state.scan?.plazas.find(p=>p.id===choice);
    $('googlePreviewTitle').textContent=place?.name??'起点 · Google Maps';$('googlePreviewNote').textContent=place?.address??'地图位置使用已确认的起点坐标。';
  }
  if($('googleFrame').getAttribute('src')!==url)$('googleFrame').src=url;
}
$('googleSegment').onchange=renderGooglePreview;
function boundaryText(c){return c.mode==='polygon'?`绘制区域 · ${c.polygon.length} 个顶点`:`直径 ${c.diameter_miles} 英里`;}
async function loadHistory(){
  const data=await api('/history?'+new URLSearchParams({q:$('historyQuery').value,status:$('historyStatus').value,offset:state.historyOffset}));
  $('historyList').innerHTML=data.items.length?'':'<p class="hint">暂无符合条件的历史记录。</p>';
  for(const rec of data.items){const el=document.createElement('article');el.className='history-entry';el.innerHTML=`<div><h2>${escapeHtml(rec.config.origin.label)}</h2><p>${dateText(rec.created)} · ${escapeHtml(boundaryText(rec.config))}</p><p>${discoveryLabel(rec.config)} · ${rec.found} 个地点 · ${rec.trip_count} 趟 · ${statusNames[rec.status]}</p></div><div class="entry-actions"><button data-action="open">重新打开</button><button data-action="export">导出</button><button data-action="delete" class="text-button danger">删除</button></div>`;
    el.querySelector('[data-action=open]').onclick=()=>reopen(rec.id).catch(e=>toast(e.message));
    el.querySelector('[data-action=export]').onclick=()=>download(rec.id);
    el.querySelector('[data-action=delete]').onclick=()=>confirmDelete('删除这条搜索及其保存的行程和导出文件？定时计划不受影响。',async()=>{await api('/history/'+rec.id,{method:'DELETE'});if(state.scan?.id===rec.id)newSearch();await loadHistory();toast('记录已删除');});
    $('historyList').append(el);}
  $('historyPage').textContent=`${data.total} 条记录 · 第 ${Math.floor(state.historyOffset/50)+1} 页`;$('historyPrev').disabled=state.historyOffset===0;$('historyNext').disabled=state.historyOffset+50>=data.total;
}
async function download(id){
  try{const response=await fetch(`/api/scans/${id}/export`);if(!response.ok){const data=await response.json();throw new Error(data.detail||'导出失败，请重试。');}
    const blob=await response.blob();const name=response.headers.get('Content-Disposition')?.match(/filename\*=utf-8''([^;]+)/i)?.[1];
    const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=name?decodeURIComponent(name):`巡访-${id}.json`;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(a.href),1000);toast('JSON 导出已开始');
  }catch(error){toast(error.message);}
}
function confirmDelete(text, action){confirmAction=action;$('confirmText').textContent=text;$('confirmDialog').showModal();$('confirmNo').focus();}

async function loadSchedules(){
  const {items}=await api('/schedules');$('scheduleList').innerHTML=items.length?'':'<p class="hint">尚无定时搜索。先在规划页确认起点与范围，再添加计划。</p>';
  for(const spec of items){const el=document.createElement('article');el.className='schedule-entry';el.innerHTML=`<div><h2>${escapeHtml(spec.name)}</h2><p>${discoveryLabel(spec.config)} · ${escapeHtml(spec.config.origin.label)} · ${escapeHtml(boundaryText(spec.config))} · ${thresholdText(spec.config)}</p><p>${escapeHtml(spec.cron)} · ${escapeHtml(spec.timezone)} · ${spec.enabled?'已启用':'已停用'}</p><p>下次：${spec.enabled?dateText(spec.next_run):'已停用'}${spec.running?' · 正在搜索':''}</p><p>上次结果：${escapeHtml(spec.last_outcome??'尚未运行')}</p></div><div class="entry-actions"><button data-action="edit">编辑</button><button data-action="toggle">${spec.enabled?'停用':'启用'}</button><button data-action="delete" class="text-button danger">删除</button></div>`;
    el.querySelector('[data-action=edit]').onclick=()=>openSchedule(spec);
    el.querySelector('[data-action=toggle]').onclick=async()=>{try{const {id,next_run,running,last_outcome,...body}=spec;await api('/schedules/'+id,{method:'PUT',body:JSON.stringify({...body,enabled:!spec.enabled})});await loadSchedules();}catch(error){toast(error.message);}};
    el.querySelector('[data-action=delete]').onclick=()=>confirmDelete('删除此定时搜索？已保存的历史记录会保留。',async()=>{await api('/schedules/'+spec.id,{method:'DELETE'});await loadSchedules();});
    $('scheduleList').append(el);}
}
function openSchedule(spec=null){
  clearErrors();const c=spec?.config??config();if(!c.origin?.confirmed||!validNumbers(c)||(c.mode==='polygon'&&c.polygon.length<3)){toast('请先在规划页确认起点与有效搜索范围。');return;}
  state.scheduleId=spec?.id??null;state.scheduleConfig=structuredClone(c);$('scheduleTitle').textContent=spec?'编辑定时搜索':'新增定时搜索';
  $('scheduleName').value=spec?.name??c.origin.label.slice(0,100);$('scheduleArea').textContent=`${c.origin.label} · ${boundaryText(c)} · ${discoveryLabel(c)} · ${thresholdText(c)} · 每趟 ${c.batch_size} 个地点`;
  $('scheduleTimezone').value=spec?.timezone??Intl.DateTimeFormat().resolvedOptions().timeZone??'America/New_York';
  $('scheduleEnabled').checked=spec?.enabled??true;$('frequency').value=spec?'advanced':'daily';$('cronExpression').value=spec?.cron??'0 9 * * *';frequencyChange();$('scheduleDialog').showModal();
}
function frequencyChange(){$('advancedCron').hidden=$('frequency').value!=='advanced';$('simpleTime').hidden=$('frequency').value==='advanced';}

function showDestinationShare(plaza,data){
  $('shareName').textContent=plaza.name;$('shareInfo').textContent=data.message;$('shareUrl').value=data.url;
  $('nativeShare').hidden=!navigator.share;$('shareDialog').showModal();
}
async function sharePlaza(plaza,button){
  const original=button.innerHTML;button.disabled=true;
  // Safari requires transient user activation. Invoke the native sheet in this
  // click, before any network await. The accepted /maps/place URL is immediate.
  if(typeof navigator.share==='function'){
    const url=`https://www.google.com/maps/place/${Number(plaza.lat).toFixed(7)},${Number(plaza.lng).toFixed(7)}`;
    try{await navigator.share({title:plaza.name,url});}
    catch(error){if(error.name!=='AbortError')showDestinationShare(plaza,{url,message:'系统分享暂不可用，可复制地点链接，或点“系统分享”重试。'});}
    finally{button.disabled=false;button.innerHTML=original;}
    return;
  }
  button.textContent='取得链接…';
  try{const data=await post(`/scans/${state.scan.id}/share/${encodeURIComponent(plaza.id)}`);const url=new URL(data.url);
    if(!((url.hostname==='maps.app.goo.gl')||(url.hostname==='www.google.com'&&url.pathname.startsWith('/maps/place/')))||url.pathname.includes('/maps/dir'))throw new Error('地点链接格式不符，未分享。');
    showDestinationShare(plaza,data);
  }catch(error){toast(error.message);}finally{button.disabled=false;button.innerHTML=original;}
}

function showMapError(message) {
  $('mapError').replaceChildren();
  const text = document.createElement('p'); text.textContent = message;
  const button = document.createElement('button'); button.textContent = '重试加载';
  button.onclick = () => setupMap().catch(error => showMapError(error.message));
  $('mapError').append(text, button); $('mapError').hidden = false; $('applyArea').disabled = true;
}
async function openMap(purpose = 'area', newOrigin = null) {
  focusBeforeMap = document.activeElement;
  state.mapPurpose = purpose;
  state.draft = structuredClone(purpose === 'area' ? config() : state.scan.config);
  if (newOrigin) state.draft.origin = newOrigin;
  if (purpose === 'route') state.draft.origin = currentTrip()?.route?.start ?? currentTrip()?.start ?? state.draft.origin;
  state.drawing = false; state.moving = false;
  $('mapDialog').showModal();
  $('mapTitle').textContent = purpose === 'area' ? '设定搜索区域' : purpose === 'route' ? '本趟驾驶路线' : '所选地点';
  $('mapEditorTools').hidden = purpose !== 'area'; $('vertexEditor').hidden = purpose !== 'area';
  $('applyArea').hidden = purpose !== 'area'; $('confirmOriginLabel').hidden = purpose !== 'area';
  $('mapError').hidden = true; $('mapValidation').textContent = '';
  $('confirmOrigin').checked = Boolean(state.draft.origin?.confirmed);
  $('mapMode').value = state.draft.mode; $('mapDiameter').value = state.draft.diameter_miles;
  $('mapAddress').value = $('address').value; $('mapMatches').replaceChildren();
  mapInstructions(); renderVertices();
  try { await setupMap(); } catch (error) { if ($('mapDialog').open) showMapError(error.message); }
}
async function setupMap() {
  if (!window.maplibregl || !window.terraDraw || !window.terraDrawMaplibreGlAdapter) {
    throw new Error('地图组件加载失败，请刷新页面重试。');
  }
  if (!state.map) {
    $('applyArea').disabled = true;
    state.map = new maplibregl.Map({container: 'areaMap', style: state.bootstrap.map_style,
      center: [-73.98, 40.75], zoom: 11, attributionControl: true, fadeDuration: 0,
      dragRotate: false, touchPitch: false});
    state.map.addControl(new maplibregl.NavigationControl({showCompass: false}), 'top-right');
    state.map.touchZoomRotate.disableRotation();
    state.map.doubleClickZoom.disable();
    state.map.on('click', event => {
      if (state.mapPurpose !== 'area' || !state.draft) return;
      if (state.drawing) {
        if (state.draft.polygon.length >= 100) { $('mapValidation').textContent = '边界最多 100 个顶点。'; return; }
        state.draft.polygon.push([event.lngLat.lng, event.lngLat.lat]); drawMap(false);
      } else if (state.moving) {
        setDraftOrigin({lat: event.lngLat.lat, lng: event.lngLat.lng, label: coordinateLabel(event.lngLat.lat, event.lngLat.lng), source: 'pin', accuracy_m: null, confirmed: true});
      }
    });
    mapPromise = new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('地图加载超时，请检查网络后重试。')), 25000);
      state.map.once('error', () => { if (!state.map.isStyleLoaded()) { clearTimeout(timer); reject(new Error('免费底图暂不可用，请检查网络后重试。')); } });
      state.map.once('load', () => {
        clearTimeout(timer);
        try {
          state.map.addSource('planner-areas', {type: 'geojson', data: {type: 'FeatureCollection', features: []}});
          state.map.addLayer({id: 'planner-fill', type: 'fill', source: 'planner-areas', filter: ['==', ['geometry-type'], 'Polygon'],
            paint: {'fill-color': ['get', 'color'], 'fill-opacity': .13}});
          state.map.addLayer({id: 'planner-lines', type: 'line', source: 'planner-areas',
            paint: {'line-color': ['get', 'color'], 'line-width': ['coalesce', ['get', 'width'], 2]}});
          const {TerraDraw, TerraDrawPolygonMode, TerraDrawSelectMode} = terraDraw;
          state.editor = new TerraDraw({adapter: new terraDrawMaplibreGlAdapter.TerraDrawMapLibreGLAdapter({map: state.map, coordinatePrecision: 9}),
            modes: [new TerraDrawPolygonMode(), new TerraDrawSelectMode({
              allowManualDeselection: false,
              flags: {polygon: {feature: {coordinates: {draggable: true, deletable: true, midpoints: {draggable: true}}}}}
            })]});
          state.editor.start(); state.editor.setMode('static');
          state.editor.on('change', () => {
            if (state.syncingEditor || state.mapPurpose !== 'area' || !state.draft) return;
            const feature = state.editor.getSnapshot().find(item => item.properties.mode === 'polygon' && !item.properties.selectionPoint && !item.properties.midPoint);
            if (feature?.geometry.type === 'Polygon') {
              state.draft.polygon = feature.geometry.coordinates[0].slice(0, -1).map(p => [...p]);
              renderVertices(); mapInstructions();
            }
          });
          resolve();
        } catch (error) { reject(error); }
      });
    });
  }
  try { await mapPromise; }
  catch (error) {
    state.editor?.stop(); state.editor = null; state.map?.remove(); state.map = null; mapPromise = null;
    throw error;
  }
  if (!$('mapDialog').open || !state.draft) return;
  $('mapError').hidden = true; state.map.resize(); drawMap(true); $('applyArea').disabled = false; mapInstructions();
}
function addMarker(position, label, title, draggable = false, onMove = null) {
  const element = document.createElement('button'); element.className = 'pin-label';
  element.type = 'button'; element.textContent = label; element.title = title; element.setAttribute('aria-label', title);
  const marker = new maplibregl.Marker({element, draggable}).setLngLat(position).addTo(state.map);
  // MapLibre supplies pointer/touch dragging. Arrow keys provide a pin correction
  // alternative without requiring a pointing device or typing coordinates.
  if (onMove) {
    marker.on('dragend', () => onMove(marker.getLngLat()));
    element.addEventListener('keydown', event => {
      const delta = {ArrowUp: [0,-6], ArrowDown: [0,6], ArrowLeft: [-6,0], ArrowRight: [6,0]}[event.key];
      if (!delta) return; event.preventDefault(); event.stopPropagation();
      const point = state.map.project(marker.getLngLat());
      const location = state.map.unproject([point.x + delta[0], point.y + delta[1]]);
      const focusedTitle = title;
      onMove(location);
      const replacement = state.overlays.find(item => item.getElement().getAttribute('aria-label') === focusedTitle);
      replacement?.getElement().focus();
    });
  }
  state.overlays.push(marker); return marker;
}
function drawMap(fit = false) {
  // Source existence is the readiness signal. isStyleLoaded() becomes false
  // again while Terra Draw adds sources or background map tiles are loading.
  if (!state.map?.getSource('planner-areas') || !state.draft || !state.editor) { mapInstructions(); return; }
  for (const marker of state.overlays) marker.remove(); state.overlays = [];
  const c = state.draft, editable = state.mapPurpose === 'area';
  const bounds = new maplibregl.LngLatBounds(), features = [];
  const addArea = (feature, color) => { feature.properties.color = color; features.push(feature); };
  state.syncingEditor = true;
  try {
    state.editor.setMode('static'); state.editor.clear();
    if (c.origin) {
      const position = [c.origin.lng, c.origin.lat]; bounds.extend(position);
      addMarker(position, '起', '起点，可拖动或用方向键校正', editable,
        editable ? location => setDraftOrigin({...c.origin, lat: location.lat, lng: location.lng, label: coordinateLabel(location.lat, location.lng), source: 'pin', accuracy_m: null, confirmed: true}, false) : null);
      if (editable && c.origin.accuracy_m) addArea(mapGeometry.circle(...position, c.origin.accuracy_m), '#407fb3');
      if (c.mode === 'circle' && state.mapPurpose !== 'route') {
        const circle = mapGeometry.circle(...position, c.diameter_miles * 1609.344 / 2);
        addArea(circle, '#286b4d'); circle.geometry.coordinates[0].forEach(p => bounds.extend(p));
      }
    }
    if (c.mode === 'polygon' && c.polygon.length && state.mapPurpose !== 'route') {
      c.polygon.forEach(p => bounds.extend(p));
      if (c.polygon.length >= 3) {
        const feature = mapGeometry.polygon(c.polygon);
        if (editable && !state.drawing && !state.moving) {
          feature.id = 'c8778730-2b31-4f24-8c50-1e72a379a019'; feature.properties.mode = 'polygon';
          const [validation] = state.editor.addFeatures([feature]);
          if (validation.valid) { $('mapValidation').textContent = ''; state.editor.setMode('select'); state.editor.selectFeature(feature.id); }
          else { addArea(feature, '#a32e28'); $('mapValidation').textContent = '边界无效，请修改顶点或重新绘制。'; }
        } else addArea(feature, '#286b4d');
      } else if (c.polygon.length === 2) {
        features.push({type: 'Feature', properties: {color: '#286b4d'}, geometry: {type: 'LineString', coordinates: c.polygon}});
      }
      if (editable && state.drawing) c.polygon.forEach((p,i) => addMarker(p, String(i+1), `边界顶点 ${i+1}`));
    }
    if (!editable && state.scan) {
      const route = currentTrip()?.route;
      const ids = state.mapPurpose === 'route' ? route?.order ?? [] : [...state.selected];
      const byId = Object.fromEntries(state.scan.plazas.map(p => [p.id,p]));
      ids.forEach((id,index) => { const p = byId[id]; if (!p) return;
        bounds.extend([p.lng,p.lat]); addMarker([p.lng,p.lat], state.mapPurpose === 'route' ? String(index+1) : String(p.rank), p.name); });
      if (state.mapPurpose === 'route' && route) {
        features.push({type: 'Feature', properties: {color: '#245da2', width: 5}, geometry: route.geometry});
        route.geometry.coordinates.forEach(p => bounds.extend(p));
      }
    }
  } finally { state.syncingEditor = false; }
  state.map.getSource('planner-areas').setData({type: 'FeatureCollection', features});
  if (fit && !bounds.isEmpty()) state.map.fitBounds(bounds, {padding: 55, maxZoom: 16, duration: 0});
  renderVertices(); mapInstructions();
}
function setDraftOrigin(origin, fit = true) {
  locationRequest++; state.draft.origin = origin; $('mapAddress').value = origin.label??origin.name??coordinateLabel(origin.lat,origin.lng); $('confirmOrigin').checked = Boolean(origin.confirmed); state.moving = false; drawMap(fit);
}
function mapInstructions() {
  const c = state.draft; if (!c) return;
  $('mapDiameterLabel').hidden = c.mode === 'polygon';
  for (const id of ['drawPolygon','clearPolygon','undoVertex','finishPolygon']) $(id).hidden = c.mode !== 'polygon';
  $('finishPolygon').disabled = !state.drawing || c.polygon.length < 3; $('undoVertex').disabled = !c.polygon.length;
  $('mapInstruction').textContent = state.moving ? '点按地图放置起点；也可在起点标记上用方向键校正。' : state.drawing
    ? `逐个点按边界顶点（已添加 ${c.polygon.length} 个）；点击“完成绘制”结束。`
    : c.mode === 'polygon' ? '仅使用绘制区域。拖动顶点修改边界；拖动边中点可增加顶点。直径不参与搜索。'
    : '圆形范围以起点为中心。拖动起点，或选中起点标记后用方向键校正。';
  const o = c.origin;
  const outside=state.mapPurpose==='area'&&c.mode==='polygon'&&c.polygon.length>=3&&o&&!mapGeometry.covers(c.polygon,o.lng,o.lat);
  if(outside)$('mapValidation').textContent='起点必须位于绘制区域内或边界上，请移动起点或修改边界。';
  else if($('mapValidation').textContent.startsWith('起点必须'))$('mapValidation').textContent='';
  if(state.mapPurpose==='area')$('applyArea').disabled=Boolean(outside)||(c.mode==='polygon'&&(c.polygon.length<3||state.drawing))||!$('mapError').hidden;
  $('mapOriginLabel').textContent = o ? `${(o.name??o.label)===coordinateLabel(o.lat,o.lng)?'':(o.name??o.label)+' · '}坐标 ${coordinateLabel(o.lat,o.lng)}${o.accuracy_m ? ` · 设备报告精度约 ±${Math.round(o.accuracy_m)} 米${o.accuracy_m>100 ? '，精度较低，请移动起点或核对后确认' : ''}` : ''}` : '尚未选择起点；请查找地址或点击“移动起点”放置。';
  $('confirmOriginLabel').hidden = state.mapPurpose !== 'area';
}
function renderVertices() {
  const c = state.draft; if (!c) return;
  $('vertexEditor').hidden = state.mapPurpose !== 'area' || c.mode !== 'polygon'; $('vertexRows').replaceChildren();
  c.polygon.forEach((point,index) => {
    const row = document.createElement('div'); row.className = 'vertex-row';
    row.innerHTML = `<span>${index+1}</span><input type="number" step="0.000001" min="-180" max="180" aria-label="顶点 ${index+1} 经度" value="${point[0]}"><input type="number" step="0.000001" min="-85" max="85" aria-label="顶点 ${index+1} 纬度" value="${point[1]}"><button>删除</button>`;
    row.querySelectorAll('input').forEach((input,axis) => input.onchange = () => {
      if (!input.checkValidity()) return; c.polygon[index][axis] = Number(input.value); drawMap(false);
    });
    row.querySelector('button').onclick = () => { c.polygon.splice(index,1); drawMap(false); };
    $('vertexRows').append(row);
  });
}
function closeMap() {
  state.syncingEditor = true;
  try { state.editor?.setMode('static'); state.editor?.clear(); }
  finally { state.syncingEditor = false; }
  locationRequest++; $('mapLocate').disabled=false; $('mapLocate').innerHTML=icon('locate')+'使用当前位置';
  $('mapDialog').close(); state.draft = null; state.drawing = false; state.moving = false; focusBeforeMap?.focus();
}

document.querySelectorAll('.nav').forEach(el=>el.onclick=()=>view(el.dataset.view));
document.querySelectorAll('#searchForm input').forEach(input=>input.addEventListener('input',()=>{if(input.id==='address'&&state.origin&&input.value!==state.origin.label){state.origin=null;$('addressMatches').replaceChildren();}syncForm();}));
document.querySelectorAll('#searchForm input[type=number]').forEach(input=>input.addEventListener('input',()=>validateNumberInput(input.id)));
$('lookup').onclick=()=>lookupAddress($('address'),$('addressMatches'),origin=>{state.origin=origin;$('address').value=origin.label;$('addressMatches').replaceChildren();$('error-origin').textContent='';syncForm();});
$('publicLookup').onclick=()=>publicLookup($('address'),$('addressMatches'),origin=>{state.origin=origin;$('address').value=origin.label;$('addressMatches').replaceChildren();$('error-origin').textContent='';syncForm();});
$('address').addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();$('lookup').click();}});
$('newSearch').onclick=newSearch;$('openMap').onclick=()=>openMap();
function locateCurrent(inMap=false){
  const button=$(inMap?'mapLocate':'locate'),target=$(inMap?'mapValidation':'error-origin');
  if(!window.isSecureContext){target.textContent='当前位置需要 HTTPS；请通过本机 localhost 或 Tailscale HTTPS 地址打开。';return;}
  if(!navigator.geolocation){target.textContent='此浏览器不支持定位，请输入地址。';return;}
  const request=++locationRequest,draft=state.draft;
  const original=button.innerHTML;button.disabled=true;button.textContent='正在定位…';target.textContent='';
  const finish=()=>{button.disabled=false;button.innerHTML=original;};
  navigator.geolocation.getCurrentPosition(position=>{
    finish();if(request!==locationRequest||(inMap&&(!$('mapDialog').open||state.draft!==draft)))return;
    const {latitude:lat,longitude:lng,accuracy}=position.coords;
    const origin={lat,lng,label:coordinateLabel(lat,lng),source:'device',accuracy_m:accuracy,confirmed:false};
    if(inMap){state.drawing=false;setDraftOrigin(origin);}else openMap('area',origin);
  },error=>{finish();if(request!==locationRequest)return;target.textContent=({1:'定位权限被拒绝。请在浏览器中允许定位，或输入地址。',2:'设备暂时无法提供位置，请重试或输入地址。',3:'定位超时，请重试或输入地址。'})[error.code]??'定位失败，请输入地址。';},{enableHighAccuracy:true,timeout:20000,maximumAge:0});
}
$('locate').innerHTML=icon('locate')+'使用当前位置';$('locate').classList.add('icon-button');
$('mapLocate').innerHTML=icon('locate')+'使用当前位置';
$('locate').onclick=()=>locateCurrent();$('mapLocate').onclick=()=>locateCurrent(true);
$('closeMapsRoute').onclick=()=>$('mapsRouteDialog').close();
$('checkRouting').onclick=async()=>{
  const button=$('checkRouting');button.disabled=true;$('routingStatus').textContent='正在检查本趟起点附近的矩阵与道路服务…';
  try{const trip=currentTrip();const start=trip?.start??trip?.route?.start??state.scan?.config.origin??state.origin;
    const c=structuredClone(state.scan?.config??config());c.origin={lat:start.lat,lng:start.lng,label:start.name??start.label??coordinateLabel(start.lat,start.lng),source:'pin',confirmed:true,accuracy_m:null};
    const result=await post('/routing/check',c);if(result.preparing){showCoverageStatus(result.coverage);$('routingStatus').textContent='正在自动准备路网，请查看下方进度；完成后可计算路线。';}else $('routingStatus').textContent=result.message;
  }catch(error){$('routingStatus').textContent=error.message;}finally{button.disabled=false;}
};
$('searchForm').onsubmit=async event=>{event.preventDefault();clearErrors();if(!canSearch())return;state.busy=true;syncForm();try{state.scan=await post('/scans',config());state.tripId=null;state.selected.clear();renderResults();poll();}catch(error){showError(error);}finally{state.busy=false;syncForm();}};
$('cancelScan').onclick=async()=>{try{await post(`/scans/${state.scan.id}/cancel`);poll();}catch(error){toast(error.message);}};
$('retryScan').onclick=async()=>{state.busy=true;renderResults();try{state.scan=await post(`/scans/${state.scan.id}/retry`);poll();}catch(error){toast(error.message);}finally{state.busy=false;renderResults();}};
$('exportScan').onclick=()=>download(state.scan.id);
document.querySelectorAll('[data-kind]').forEach(button=>button.onclick=()=>{if(state.discoveryKind===button.dataset.kind)return;state.discoveryKind=button.dataset.kind;for(const id of ['minShops','minCompanies'])if(!$(id).checkValidity()||$(id).value==='')$(id).value=3;clearErrors();newSearch();});
$('nextTrip').onclick=async()=>{if(state.busy)return;state.busy=true;renderResults();try{state.scan=await post(`/scans/${state.scan.id}/trips`);state.tripId=state.scan.trips.at(-1)?.id;}catch(error){toast(error.message);}finally{state.busy=false;renderResults();}};
$('tripSelect').onchange=()=>{state.tripId=$('tripSelect').value;renderResults();persist();};
$('calculate').onclick=async()=>{if(state.busy)return;const closed=$('returnOrigin').checked,tid=state.tripId,sid=state.scan.id,token=Symbol('route');state.routeToken=token;state.busy=true;renderResults();
  try{let retry=true;for(;;){if(state.routeToken!==token)return;const result=await post(`/scans/${sid}/trips/${tid}/route`,{return_to_origin:closed,retry_coverage:retry});retry=false;
      if(state.routeToken!==token)return;
      if(!result.preparing){state.scan=result;state.googleChoice='route';break;}
      showCoverageStatus(result.coverage);$('calculate').textContent='正在准备道路数据…';await new Promise(resolve=>setTimeout(resolve,1800));}}
  catch(error){if(state.routeToken===token){const t=currentTrip();if(t)t.error=error.message;}}finally{if(state.routeToken===token){state.busy=false;renderResults();}}};
$('previewSelection').onclick=()=>openMap('selection');
$('filterHistory').onclick=()=>{state.historyOffset=0;loadHistory().catch(e=>toast(e.message));};
$('historyQuery').onkeydown=e=>{if(e.key==='Enter')$('filterHistory').click();};
$('historyPrev').onclick=()=>{state.historyOffset=Math.max(0,state.historyOffset-50);loadHistory().catch(e=>toast(e.message));};
$('historyNext').onclick=()=>{state.historyOffset+=50;loadHistory().catch(e=>toast(e.message));};
$('clearHistory').onclick=()=>confirmDelete('清空全部历史、行程及导出文件？定时搜索会保留，之后仍会产生新记录。',async()=>{await api('/history',{method:'DELETE'});state.scan=null;newSearch();await view('history');toast('历史已清空，定时搜索已保留');});
$('confirmNo').onclick=()=>$('confirmDialog').close();$('confirmYes').onclick=async()=>{const action=confirmAction;$('confirmYes').disabled=true;try{await action();$('confirmDialog').close();}catch(error){toast(error.message);}finally{$('confirmYes').disabled=false;}};
$('addSchedule').onclick=()=>openSchedule();$('closeSchedule').onclick=()=>$('scheduleDialog').close();$('frequency').onchange=frequencyChange;
$('useCurrentSettings').onclick=()=>{const c=config();if(!c.origin?.confirmed||!validNumbers(c)||(c.mode==='polygon'&&c.polygon.length<3)){$('scheduleError').textContent='请先在规划页确认有效起点与搜索范围。';return;}state.scheduleConfig=structuredClone(c);$('scheduleArea').textContent=`${c.origin.label} · ${boundaryText(c)} · ${discoveryLabel(c)} · ${thresholdText(c)} · 每趟 ${c.batch_size} 个地点`;$('scheduleError').textContent='';};
$('scheduleForm').onsubmit=async event=>{event.preventDefault();clearErrors();const mode=$('frequency').value;const [hour,minute]=$('scheduleTime').value.split(':');
  const expression=mode==='advanced'?$('cronExpression').value:`${Number(minute)} ${Number(hour)} * * ${mode==='weekdays'?'1,2,3,4,5':mode==='weekly'?'1':'*'}`;
  $('saveSchedule').disabled=true;try{const body={name:$('scheduleName').value,config:state.scheduleConfig,cron:expression,timezone:$('scheduleTimezone').value,enabled:$('scheduleEnabled').checked};await api('/schedules'+(state.scheduleId?'/'+state.scheduleId:''),{method:state.scheduleId?'PUT':'POST',body:JSON.stringify(body)});$('scheduleDialog').close();await loadSchedules();toast('定时搜索已保存');}catch(error){showError(error,'scheduleError');}finally{$('saveSchedule').disabled=false;}};
$('mapClose').onclick=closeMap;$('mapCancel').onclick=closeMap;$('mapDialog').addEventListener('cancel',event=>{event.preventDefault();closeMap();});
$('mapLookup').onclick=()=>lookupAddress($('mapAddress'),$('mapMatches'),origin=>{setDraftOrigin({...origin,confirmed:false});$('mapMatches').replaceChildren();});
$('publicMapLookup').onclick=()=>publicLookup($('mapAddress'),$('mapMatches'),origin=>{setDraftOrigin({...origin,confirmed:false});$('mapMatches').replaceChildren();});
$('mapAddress').onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();$('mapLookup').click();}};
$('mapMode').onchange=()=>{state.draft.mode=$('mapMode').value;state.drawing=false;drawMap(true);};
$('mapDiameter').oninput=()=>{const value=Number($('mapDiameter').value);if(value>=.1&&value<=30){state.draft.diameter_miles=value;drawMap(true);}};
$('movePin').onclick=()=>{state.moving=true;state.drawing=false;drawMap(false);};
$('drawPolygon').onclick=()=>{state.draft.polygon=[];state.drawing=true;state.moving=false;$('mapValidation').textContent='';drawMap(false);};
$('undoVertex').onclick=()=>{state.draft.polygon.pop();drawMap(false);};
$('finishPolygon').onclick=()=>{state.drawing=false;drawMap(false);};
$('clearPolygon').onclick=()=>{state.draft.polygon=[];state.drawing=false;$('mapValidation').textContent='';drawMap(false);};
$('addVertex').onclick=()=>{if(!state.map||state.draft.polygon.length>=100)return;const center=state.map.getCenter();state.draft.polygon.push([center.lng,center.lat]);drawMap(false);};
$('applyArea').onclick=async()=>{
  const c=state.draft;if(!c.origin||!$('confirmOrigin').checked){$('mapValidation').textContent='请先选择并核对确认起点；位置不准时可移动起点标记。';return;}
  if(c.mode==='circle'&&!$('mapDiameter').checkValidity()){$('mapValidation').textContent='直径须为 0.1–30 英里。';return;}
  if(state.drawing){$('mapValidation').textContent='请先完成绘制。';return;}
  $('applyArea').disabled=true;try{c.origin.confirmed=true;await post('/validate',{...c,min_shops:3,min_companies:3,batch_size:5,baseline:0});const old=state.scan?.config;const changed=old&&JSON.stringify([old.origin.lat,old.origin.lng,old.mode,old.diameter_miles,old.polygon])!==JSON.stringify([c.origin.lat,c.origin.lng,c.mode,c.diameter_miles,c.polygon]);restoreConfig(c);clearErrors();closeMap();if(changed){newSearch();toast('起点或范围已更新，请重新搜索。原有搜索与路线保留在历史中。');}}catch(error){$('mapValidation').textContent=Object.values(error.errors??{}).join(' ')||error.message;}finally{$('applyArea').disabled=false;}
};
$('closeShare').onclick=()=>$('shareDialog').close();
$('copyShare').onclick=async()=>{try{await navigator.clipboard.writeText($('shareUrl').value);toast('地点链接已复制');}catch{$('shareUrl').focus();$('shareUrl').select();toast('请复制已选中的地点链接');}};
$('nativeShare').onclick=async()=>{try{await navigator.share({title:$('shareName').textContent,url:$('shareUrl').value});}catch(error){if(error.name!=='AbortError')toast('系统分享不可用，请复制链接。');}};

async function init(){
  try{state.bootstrap=await api('/bootstrap');
    let saved;try{saved=JSON.parse(localStorage.getItem('plaza-planner-draft')??'null');}catch{}
    if(saved?.config){restoreConfig(saved.config);$('address').value=saved.address??$('address').value;state.tripId=saved.tripId;}
    if(saved?.scanId){try{await reopen(saved.scanId);}catch{state.scan=null;syncForm();}if(saved.config){restoreConfig(saved.config);$('address').value=saved.address??$('address').value;}}
    syncForm();
  }catch(error){$('error-form').textContent=error.message;}
}
init();
