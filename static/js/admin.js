function tab(id){document.querySelectorAll('.tab').forEach(t=>t.classList.remove('on'));document.getElementById('t-'+id).classList.add('on');if(id==='logs')loadLogs();if(id==='ads')loadAds();if(id==='reminders')loadReminders();}
async function jget(p){return (await fetch(p)).json();}
async function jpost(p,b){return (await fetch(p,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b||{})})).json();}
loadDash();loadCh();loadLib();loadRem();loadHdmi();loadSys();loadSets();renderSrcRows([]);
async function loadDash(){
  const s=await jget('/api/system');
  document.getElementById('dash').innerHTML=s.ok?`
    Episodes: <b>${s.library.episodes}</b> | Movies: <b>${s.library.movies}</b> | Commercials: <b>${s.library.commercials}</b><br>
    Disk free: <b>${(s.disk.free/1e9).toFixed(1)} GB</b> | mpv: <b>${s.mpv?'installed':'MISSING (fallback cvlc)'}</b> alive=${s.mpv_alive}<br>
    ${s.library.warnings.map(w=>`<div class="warn">${esc(w.path)}: ${esc(w.compat_warning)}</div>`).join('')||'<div class="okbox">No compatibility warnings.</div>'}`:'err';
}
async function rescan(full){const r=await jpost('/api/scan',{full});alert(`Scan: +${r.added} new, ${r.updated} updated, total ${r.total}. Metadata: ${r.metadata_enriched||0} enriched.`);loadDash();loadLib();}

// ---------- channels ----------
const SRC_TYPES=[
  {v:'show',label:'Show (all episodes)'},
  {v:'season',label:'Single season'},
  {v:'all_tv',label:'All TV shows'},
  {v:'all_movies',label:'All movies'},
  {v:'genre',label:'Movie genre'},
  {v:'movie',label:'Single movie'},
  {v:'movie_folder',label:'Movie folder'},
];
let CH_LIST=[];
async function loadCh(){
  const c=await jget('/api/channels');
  CH_LIST=c.channels;
  document.getElementById('chedit').innerHTML=CH_LIST.map((x,i)=>`<div class="ch-row">
    <span class="ch-move"><button class="ghost" ${i===0?'disabled':''} onclick="moveChan(${x.number},'up')" aria-label="Move ${esc(x.name)} up in guide order">↑</button><button class="ghost" ${i===CH_LIST.length-1?'disabled':''} onclick="moveChan(${x.number},'down')" aria-label="Move ${esc(x.name)} down in guide order">↓</button></span>
    <b>${x.number}</b> ${x.favorite?'★ ':''}${esc(x.name)} ${x.enabled?'':'[OFF]'} <i>(${esc(x.ordering)}/${esc(x.commercial_mode)} · ${x.sources.length} source${x.sources.length===1?'':'s'})</i>
    <button class="ghost" onclick="editCh(${x.number})">EDIT</button> <button class="ghost" onclick="delCh(${x.number})">DEL</button></div>`).join('')||'No channels yet.';
}
async function moveChan(n,dir){await jpost('/api/channel/'+n+'/move',{direction:dir});loadCh();}
function editCh(n){
  const x=CH_LIST.find(y=>y.number===n);if(!x)return;
  document.getElementById('cnum').value=x.number;document.getElementById('cname').value=x.name;
  document.getElementById('ccolor').value=x.color||'#1a3a6b';document.getElementById('corder').value=x.ordering;
  document.getElementById('ccom').value=x.commercial_mode;document.getElementById('cen').value=x.enabled?'1':'0';
  document.getElementById('cfav').value=x.favorite?'1':'0';
  renderSrcRows(x.sources.map(s=>({type:s.source_type,value:s.source_value})));
  document.getElementById('cheditTitle').textContent='EDIT CHANNEL '+x.number;
  document.getElementById('chmsg').textContent='';
  tab('ch');window.scrollTo(0,0);
}
function newChannel(){
  const nextNum=CH_LIST.length?Math.max(...CH_LIST.map(c=>c.number))+1:1;
  document.getElementById('cnum').value=nextNum;document.getElementById('cname').value='';
  document.getElementById('ccolor').value='#1a3a6b';document.getElementById('corder').value='shuffle';
  document.getElementById('ccom').value='between';document.getElementById('cen').value='1';document.getElementById('cfav').value='0';
  renderSrcRows([]);
  document.getElementById('cheditTitle').textContent='ADD CHANNEL';
  document.getElementById('chmsg').textContent='';
}
async function delCh(n){if(!confirm('Delete channel '+n+'?'))return;await fetch('/api/channel/'+n,{method:'DELETE'});loadCh();}
async function saveCh(){
  const body={number:+document.getElementById('cnum').value,name:document.getElementById('cname').value,
    color:document.getElementById('ccolor').value,ordering:document.getElementById('corder').value,
    commercial_mode:document.getElementById('ccom').value,enabled:document.getElementById('cen').value==='1',
    favorite:document.getElementById('cfav').value==='1',sources:collectSources()};
  const r=await jpost('/api/channel',body);
  document.getElementById('chmsg').textContent=r.ok?'Saved.':'Error';
  loadCh();
}

// ---------- channel source picker ----------
function srcValueHtml(type,value){
  if(type==='show')return `<input class="src-val" list="dlShows" value="${esc(value||'')}" placeholder="Show name">`;
  if(type==='season'){
    const [show,season]=(value||'').split(':');
    return `<input class="src-val src-show" list="dlShows" value="${esc(show||'')}" placeholder="Show name" style="flex:2"><input class="src-val src-season" type="number" min="1" value="${esc(season||'')}" placeholder="Season #" style="flex:1">`;
  }
  if(type==='genre')return `<input class="src-val" list="dlGenres" value="${esc(value||'')}" placeholder="Genre">`;
  if(type==='movie')return `<input class="src-val" list="dlMovies" value="${esc(value||'')}" placeholder="Movie file path">`;
  if(type==='movie_folder')return `<input class="src-val" list="dlFolders" value="${esc(value||'')}" placeholder="Movie folder path">`;
  return `<span class="hint">Applies to the whole library — no value needed.</span>`;
}
function srcRowHtml(type,value){
  type=type||'show';
  const opts=SRC_TYPES.map(t=>`<option value="${t.v}" ${t.v===type?'selected':''}>${t.label}</option>`).join('');
  return `<div class="src-row"><select class="src-type" onchange="srcTypeChanged(this)">${opts}</select><span class="src-val-wrap">${srcValueHtml(type,value)}</span><button class="ghost src-del" onclick="this.closest('.src-row').remove()" aria-label="Remove source">✕</button></div>`;
}
function srcTypeChanged(sel){sel.closest('.src-row').querySelector('.src-val-wrap').innerHTML=srcValueHtml(sel.value,'');}
function addSrcRow(type,value){document.getElementById('csrcRows').insertAdjacentHTML('beforeend',srcRowHtml(type,value));}
function renderSrcRows(sources){
  document.getElementById('csrcRows').innerHTML='';
  if(sources&&sources.length)sources.forEach(s=>addSrcRow(s.type,s.value));
  else addSrcRow('show','');
}
function collectSources(){
  return [...document.querySelectorAll('#csrcRows .src-row')].map(row=>{
    const type=row.querySelector('.src-type').value;
    if(type==='season'){
      const show=row.querySelector('.src-show')?.value.trim()||'';
      const season=row.querySelector('.src-season')?.value.trim()||'';
      return show&&season?{type,value:`${show}:${season}`}:null;
    }
    if(type==='all_tv'||type==='all_movies')return {type,value:''};
    const val=row.querySelector('.src-val')?.value.trim()||'';
    return val?{type,value:val}:null;
  }).filter(Boolean);
}

// ---------- library ----------
let LIB=null;
async function loadLib(){
  LIB=await jget('/api/library');
  document.getElementById('dlShows').innerHTML=LIB.shows.map(s=>`<option value="${esc(s.name)}">`).join('');
  document.getElementById('dlMovies').innerHTML=LIB.movie_list.map(m=>`<option value="${esc(m.path)}">${esc(m.title)}</option>`).join('');
  document.getElementById('dlGenres').innerHTML=LIB.genres.map(g=>`<option value="${esc(g)}">`).join('');
  document.getElementById('dlFolders').innerHTML=LIB.movie_folders.map(f=>`<option value="${esc(f)}">`).join('');
  renderLib();
}
function renderLib(){
  if(!LIB)return;
  const q=(document.getElementById('libSearch')?.value||'').trim().toLowerCase();
  const shows=LIB.shows.filter(s=>!q||s.name.toLowerCase().includes(q));
  const movies=LIB.movie_list.filter(m=>!q||m.title.toLowerCase().includes(q));
  document.getElementById('lib').innerHTML=`<b>Shows (${LIB.shows.length}):</b><br>${shows.map(s=>`<span class="chip">${esc(s.name)} (${s.c})</span>`).join('')||'none matching'}<br><br>
  <b>Movies (${LIB.movie_list.length}):</b><br>${movies.map(m=>`<span class="chip">${esc(m.title)}${m.year?` (${m.year})`:''}</span>`).join('')||'none matching'}<br><br>
  Episodes ${LIB.episodes} | Movies ${LIB.movies} | Commercials ${LIB.commercials}<br>${LIB.warnings.map(w=>`<div class="warn">${esc(w.path)}: ${esc(w.compat_warning)}</div>`).join('')}`;
}

async function loadSched(){
  const ch=document.getElementById('gch').value||2;const day=document.getElementById('gday').value;
  const r=await jget(`/api/schedule?channel=${ch}&day=${day||''}`);
  document.getElementById('sched').innerHTML=(r.entries||[]).map(e=>`<div>${e.start_fmt}-${e.end_fmt} <b>${esc(e.title)}</b> ${esc(e.subtitle||'')} <i>(${esc(e.kind)})</i></div>`).join('')||'none';
  if(!document.getElementById('gday').value&&r.day)document.getElementById('gday').value=r.day;
}
async function regen(){const r=await jpost('/api/regen',{channel:document.getElementById('gch').value,day:document.getElementById('gday').value});alert(r.ok?`Regenerated ${r.entries} entries for ${r.day}`:(r.error||'failed'));}
async function loadRem(){
  const r=await jget('/api/remote');
  document.getElementById('rem').innerHTML=Object.entries(r.mappings).map(([a,c])=>`<div class="kv"><label>${a}</label><input data-act="${a}" value="${esc(c)}" placeholder="click then press key"></div>`).join('');
  document.querySelectorAll('#rem input').forEach(inp=>{
    inp.addEventListener('keydown',async e=>{e.preventDefault();inp.value=e.key;await jpost('/api/remote',{action:inp.dataset.act,code:e.key});});
    inp.addEventListener('change',async()=>{await jpost('/api/remote',{action:inp.dataset.act,code:inp.value});});
  });
  document.getElementById('devs').innerHTML=(r.devices||[]).map(d=>`<div>${esc(d.path)} ${d.readable?'(readable)':'(no read perm)'}</div>`).join('')||'no input devices';
}
async function loadHdmi(){const r=await jget('/api/hdmi');document.getElementById('hdmi').innerHTML=`HDMI channel: <b>${r.channel??'none'}</b> | mpv alive: ${r.mpv_alive} | mpv found: ${r.mpv} | Captions: <b>${r.cc==='1'?'ON':'OFF'}</b><br>${r.entry?`<b>${esc(r.entry.title)}</b> ${esc(r.entry.subtitle||'')}`:'no entry'}`;}
async function tune(){const r=await jpost('/api/tune',{channel:+document.getElementById('tunech').value});alert(JSON.stringify(r).slice(0,300));loadHdmi();}
async function restartPb(){const r=await jpost('/api/restart-playback',{});alert(JSON.stringify(r).slice(0,300));}
async function toggleCC(){const s=await jget('/api/captions');await jpost('/api/captions',{enabled:s.cc!=='1'});loadHdmi();}
async function loadSys(){const s=await jget('/api/system');document.getElementById('sys').innerHTML=`<pre>${JSON.stringify({disk:s.disk,mpv:s.mpv,sessions:s.sessions},null,2)}</pre>`;
  document.getElementById('streams').innerHTML=(s.sessions||[]).map(x=>`<div>${esc(x.id)} ch${x.channel}</div>`).join('')||'no browser sessions';}
async function loadSets(){const r=await jget('/api/settings');document.getElementById('sets').innerHTML=Object.entries(r.settings).map(([k,v])=>`<div class="kv"><label>${k}</label><input id="set-${k}" value="${esc(v)}"></div>`).join('');}
async function saveSets(){const o={};document.querySelectorAll('#sets input').forEach(i=>o[i.id.slice(4)]=i.value);await jpost('/api/settings',o);alert('Saved');}
async function loadLogs(){const r=await jget('/api/logs');document.getElementById('logs').textContent=(r.lines||[]).join('');}

// ---------- reminders ----------
async function loadReminders(){
  const r=await jget('/api/reminders');
  document.getElementById('reminders').innerHTML=(r.reminders||[]).map(x=>`<div class="ad-row" style="grid-template-columns:1fr 90px 150px 90px">
    <span>${esc(x.title)} ${esc(x.subtitle||'')}</span><span>CH ${x.channel_number}</span><span>${esc(x.start_fmt)}</span>
    <button class="ghost" onclick="cancelReminder(${x.id})">CANCEL</button></div>`).join('')||'No reminders set.';
}
async function cancelReminder(id){await fetch('/api/reminders/'+id,{method:'DELETE'});loadReminders();}

// ---------- commercial rotation ----------
async function loadAds(){
  const r=await jget('/api/commercial-rotation');
  document.getElementById('ads').innerHTML=(r.rotation||[]).map(a=>`<div class="ad-row">
    <span>${esc(a.name||('media #'+a.media_id))}${a.category?` <i>(${esc(a.category)})</i>`:''}</span>
    <span>${a.duration?Math.round(a.duration)+'s':'--'}</span>
    <span>${a.source_offset?'resumes @ '+Math.round(a.source_offset)+'s':'from start'}</span>
    <span>${a.last_played_ts?new Date(a.last_played_ts*1000).toLocaleString():'never aired'}</span>
  </div>`).join('')||'No commercials in rotation yet.';
}
