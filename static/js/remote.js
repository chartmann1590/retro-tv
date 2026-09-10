let CUR_CH=null, PREV_CH=null, digits='', digitT, VOL=80, MUTED=false, CC=false, CHANNELS=[], commandQueue=Promise.resolve();
let guideOpen=false, gT0=0, gData=[], gSel={r:0,c:0}, favOnly=false, SEARCH_RESULTS=[], searchT;
function command(action){commandQueue=commandQueue.then(action).catch(e=>notify(e.message));return commandQueue;}
async function initRemote(){
  document.getElementById('digits').innerHTML=[1,2,3,4,5,6,7,8,9,'CLR',0,'GO'].map(d=>`<button class="${typeof d==='number'?'number-key':'ghost'}" onclick="digit('${d}')" aria-label="${d==='CLR'?'Clear channel number':d==='GO'?'Tune entered channel':'Digit '+d}">${d}</button>`).join('');
  await refreshNow();await refreshChs();
}
async function refreshNow(){
  if(document.hidden||guideOpen)return;
  try{
    const h=await tvApi('/api/hdmi');CUR_CH=h.channel;PREV_CH=h.prev_channel;
    const e=h.entry||{};
    document.getElementById('connection').textContent='● CONNECTED';
    document.getElementById('ncCh').textContent=CUR_CH==null?'--':String(CUR_CH).padStart(2,'0');
    document.getElementById('ncTitle').textContent=e.title||'Off air';
    document.getElementById('ncSub').textContent=e.subtitle||'';
    document.getElementById('ncLive').textContent=h.mpv_alive?(h.paused?'PAUSED':'LIVE'):'OFF AIR';
    showVol(h);highlightChannel();
  }catch(e){document.getElementById('connection').textContent='RECONNECTING…';}
}
async function refreshChs(){
  const j=await tvApi('/api/channels');CHANNELS=j.channels.filter(c=>c.enabled);
  renderChList();
}
function renderChList(){
  const list=favOnly?CHANNELS.filter(c=>c.favorite):CHANNELS;
  document.getElementById('chlist').innerHTML=list.map(c=>`<button class="channel-choice ghost" data-channel="${c.number}" onclick="tune(${c.number})">${c.favorite?'<span class="fav-star">★</span>':''}<span class="channel-number">${String(c.number).padStart(2,'0')}</span><span>${esc(c.name)}</span><span class="channel-arrow">›</span></button>`).join('')||(favOnly?'<p class="hint">No favorite channels yet — set them in Setup.</p>':'');
  highlightChannel();
}
function toggleFavFilter(){
  favOnly=!favOnly;
  const b=document.getElementById('favToggle');b.classList.toggle('active',favOnly);b.setAttribute('aria-pressed',String(favOnly));
  renderChList();
}
function highlightChannel(){document.querySelectorAll('[data-channel]').forEach(b=>b.classList.toggle('selected',+b.dataset.channel===CUR_CH));}
async function tuneNow(ch){await tvApi('/api/tune',{channel:ch});await refreshNow();notify(`Channel ${ch} on your TV`);}
function tune(ch){return command(()=>tuneNow(ch));}
function chStep(d){return command(()=>chStepNow(d));}
function prevCh(){return command(()=>{if(PREV_CH!=null)return tuneNow(PREV_CH);notify('No previous channel yet.');});}
function digit(d){
  clearTimeout(digitT);
  if(d==='CLR')digits='';
  else if(d==='GO'){if(digits)tune(+digits);digits='';}
  else{digits=(digits+d).slice(-3);digitT=setTimeout(()=>{if(digits)tune(+digits);digits='';updateDigits();},1500);}
  updateDigits();
}
function updateDigits(){document.getElementById('digitDisplay').textContent=digits?'CH '+digits.padStart(3,'_'):'';}
function volStep(d){return command(async()=>{const r=await tvApi('/api/volume',{volume:Math.max(0,Math.min(100,VOL+d)),muted:false});showVol(r);});}
function muteToggle(){return command(async()=>showVol(await tvApi('/api/volume',{muted:!MUTED})));}
function pauseToggle(){return command(async()=>{showVol(await tvApi('/api/volume',{toggle_pause:true}));await refreshNow();});}
function goLive(){if(CUR_CH!=null)return tune(CUR_CH);}
function ccToggle(){return command(async()=>{showVol(await tvApi('/api/captions',{enabled:!CC}));});}
function showVol(r){
  VOL=+r.volume;MUTED=r.muted==='1'||r.muted===true;
  if('cc' in r)CC=r.cc==='1'||r.cc===true;
  document.getElementById('vol').textContent=MUTED?'MUTED':`VOL ${VOL}`;
  document.getElementById('muteButton').setAttribute('aria-pressed',String(MUTED));
  document.getElementById('pauseButton').textContent=r.paused?'▶ RESUME':'Ⅱ PAUSE';
  document.getElementById('ccButton').setAttribute('aria-pressed',String(CC));
  document.getElementById('ccButton').textContent=CC?'CC ON':'CC OFF';
}
function toggleSearch(){const p=document.getElementById('rsearch');p.hidden=!p.hidden;if(!p.hidden)document.getElementById('rsearchInput').focus();}
function doSearch(){clearTimeout(searchT);searchT=setTimeout(runSearch,250);}
async function runSearch(){
  const q=document.getElementById('rsearchInput').value.trim();
  const box=document.getElementById('rsearchResults');
  if(!q){box.innerHTML='';SEARCH_RESULTS=[];return;}
  try{
    const r=await tvApi('/api/guide/search?q='+encodeURIComponent(q));
    SEARCH_RESULTS=r.results||[];
    box.innerHTML=SEARCH_RESULTS.map((e,i)=>`<div class="search-hit" onclick="pickSearch(${i})"><b>CH ${String(e.channel_number).padStart(2,'0')}</b>${esc(e.title)} ${esc(e.subtitle||'')}<br><span class="hint">${e.is_live?'ON NOW':'at '+esc(e.start_fmt)}</span></div>`).join('')||'<p class="hint">No matches.</p>';
  }catch(err){box.innerHTML='<p class="hint">Search unavailable.</p>';}
}
async function pickSearch(i){
  const e=SEARCH_RESULTS[i];if(!e)return;
  document.getElementById('rsearch').hidden=true;
  if(e.is_live)return tune(e.channel_number);
  try{
    await tvApi('/api/reminders',{entry_id:e.id});
    notify(`⏰ Reminder set — ${e.title} at ${e.start_fmt} on CH ${String(e.channel_number).padStart(2,'0')}`);
  }catch(err){notify(err.message);}
}
function toggleReminders(){const p=document.getElementById('rreminders');p.hidden=!p.hidden;if(!p.hidden)loadReminderList();}
async function loadReminderList(){
  const box=document.getElementById('rreminderList');
  try{
    const r=await tvApi('/api/reminders');
    box.innerHTML=(r.reminders||[]).map(x=>`<div class="reminder-row"><span><b>${esc(x.title)}</b> ${esc(x.subtitle||'')}<br><span class="hint">CH ${String(x.channel_number).padStart(2,'0')} · ${esc(x.start_fmt)}</span></span><button class="ghost" onclick="cancelReminder(${x.id})">✕</button></div>`).join('')||'<p class="hint">No reminders set.</p>';
  }catch(err){box.innerHTML='<p class="hint">Could not load reminders.</p>';}
}
async function cancelReminder(id){await fetch('/api/reminders/'+id,{method:'DELETE'});loadReminderList();}
async function showInfo(){
  const p=document.getElementById('rinfo');p.hidden=!p.hidden;
  if(!p.hidden&&CUR_CH!=null){const d=await tvApi('/api/now/'+CUR_CH);p.innerHTML=`<b>${esc(d.entry.title)}</b><p>${esc(d.entry.subtitle)}</p><p>${esc(d.entry.description||'No description available.')}</p><small>${esc(d.range)}</small>`;await tvApi('/api/info',{});}
}
initRemote();setInterval(()=>{command(()=>refreshNow());},5000);setInterval(()=>{if(!document.hidden)refreshChs();},60000);document.addEventListener('visibilitychange',()=>{if(!document.hidden)command(refreshNow);});

// ---- On-TV guide, driven by the phone's D-pad (nothing navigates away from /remote) ----
function setGuideButtonLabel(text){const b=document.getElementById('guideButton');if(b)b.textContent=text;}
const gNow=()=>Date.now()/1000;
const gIsLive=e=>gNow()>=e.start_ts&&gNow()<e.end_ts;
function gRow(){return gData[gSel.r];}
function gEntry(){return gRow()?.entries[gSel.c];}
async function fetchGuideWindow(){const j=await tvApi(`/api/guide?hours=3&start=${gT0}`);gData=j.guide;}
function showGuideOSD(){
  const row=gRow(),e=gEntry();
  if(!row)return;
  document.getElementById('ncCh').textContent=String(row.channel.number).padStart(2,'0');
  document.getElementById('ncTitle').textContent=e?e.title:'No programs in this window';
  document.getElementById('ncSub').textContent=e?(e.subtitle||''):'';
  document.getElementById('ncLive').textContent='GUIDE';
}
async function pushGuideOverlay(){
  const row=gRow(),e=gEntry();
  if(!row)return;
  await tvApi('/api/tv-guide',{channel:row.channel.number,entry_id:e?.id,start:gT0});
  showGuideOSD();
}
function openGuide(){
  return command(async()=>{
    if(guideOpen){await closeGuideNow();return;}
    gT0=Math.floor(gNow()/1800)*1800;
    await fetchGuideWindow();
    if(!gData.length){notify('No channels in the guide yet.');return;}
    gSel.r=Math.max(0,gData.findIndex(row=>row.channel.number===CUR_CH));
    gSel.c=Math.max(0,gRow().entries.findIndex(gIsLive));
    guideOpen=true;
    document.body.classList.add('guide-mode');
    setGuideButtonLabel('EXIT GUIDE');
    await pushGuideOverlay();
  });
}
async function closeGuideNow(){
  await tvApi('/api/tv-guide',{action:'close'});
  guideOpen=false;
  document.body.classList.remove('guide-mode');
  setGuideButtonLabel('GUIDE');
  await refreshNow();
}
function dpadUp(){return command(async()=>{if(guideOpen)await moveGuideCh(-1);else await chStepNow(1);});}
function dpadDown(){return command(async()=>{if(guideOpen)await moveGuideCh(1);else await chStepNow(-1);});}
function dpadLeft(){return command(async()=>{if(guideOpen)await moveGuideCell(-1);});}
function dpadRight(){return command(async()=>{if(guideOpen)await moveGuideCell(1);});}
function dpadOk(){return command(async()=>{if(guideOpen)await selectGuideNow();});}
async function chStepNow(d){if(!CHANNELS.length)await refreshChs();const n=CHANNELS.map(c=>c.number);if(!n.length)return;const i=n.indexOf(CUR_CH);await tuneNow(n[i<0?0:(i+d+n.length)%n.length]);}
async function moveGuideCh(d){
  const old=gEntry();
  const r=Math.max(0,Math.min(gData.length-1,gSel.r+d));
  let c=gData[r].entries.findIndex(e=>e.end_ts>(old?.start_ts||gT0));
  gSel={r,c:c<0?0:c};
  await pushGuideOverlay();
}
async function moveGuideCell(d){
  const row=gRow();
  if(!row)return;
  let c=gSel.c+d;
  if(c<0||c>=row.entries.length){
    const prevId=row.entries[gSel.c]?.id;
    gT0+=d*3600;
    await fetchGuideWindow();
    const nr=gRow();
    c=nr?nr.entries.findIndex(e=>e.id===prevId):-1;
    if(c<0)c=d>0?Math.max(0,(nr?.entries.length||1)-1):0;
  }
  gSel.c=Math.max(0,c);
  await pushGuideOverlay();
}
async function selectGuideNow(){
  const row=gRow(),e=gEntry();
  if(!e||!gIsLive(e)||e.kind==='slate'){notify('That program isn’t airing now.');return;}
  await tvApi('/api/tune',{channel:row.channel.number});
  guideOpen=false;
  document.body.classList.remove('guide-mode');
  setGuideButtonLabel('GUIDE');
  await refreshNow();
  notify(`Tuned to ${row.channel.number} · ${row.channel.name}`);
}

function fitRemote(){
  const layout=document.querySelector('.remote-layout'), handset=document.querySelector('.remote-handset');
  if(!layout||!handset)return;
  handset.style.transform='';
  const scale=Math.min(1,(layout.clientHeight-4)/handset.scrollHeight);
  handset.style.transform=scale<1?`scale(${scale})`:'';
}
if(document.querySelector('.remote-handset')){
  new ResizeObserver(fitRemote).observe(document.querySelector('.remote-handset'));
  window.addEventListener('resize',fitRemote);
  window.addEventListener('orientationchange',fitRemote);
  fitRemote();
}
