let CUR_CH=null, PREV_CH=null, digits='', digitT, VOL=80, MUTED=false, CC=false, CHANNELS=[], commandQueue=Promise.resolve();
let guideOpen=false, gT0=0, gData=[], gSel={r:0,c:0}, favOnly=false, SEARCH_RESULTS=[], searchT;
let vodOpen=false, IS_VOD_PLAYING=false, sportsOpen=false;
function command(action){commandQueue=commandQueue.then(action).catch(e=>notify(e.message));return commandQueue;}
async function initRemote(){
  document.getElementById('digits').innerHTML=[1,2,3,4,5,6,7,8,9,'CLR',0,'GO'].map(d=>`<button class="${typeof d==='number'?'number-key':'ghost'}" onclick="digit('${d}')" aria-label="${d==='CLR'?'Clear channel number':d==='GO'?'Tune entered channel':'Digit '+d}">${d}</button>`).join('');
  await refreshNow();await refreshChs();
}
async function refreshNow(){
  if(document.hidden)return;
  try{
    const h=await tvApi('/api/hdmi');
    document.getElementById('connection').textContent='● CONNECTED';
    vodOpen=!!h.tv_vod?.visible;guideOpen=!!h.tv_guide?.visible;sportsOpen=!!h.tv_sports?.visible;
    IS_VOD_PLAYING=!!h.is_vod;
    if(!h.is_vod){CUR_CH=h.channel;PREV_CH=h.prev_channel;}
    document.body.classList.toggle('vod-mode',vodOpen);
    document.body.classList.toggle('guide-mode',guideOpen);
    document.body.classList.toggle('sports-mode',sportsOpen);
    document.getElementById('vodButton').textContent=vodOpen?'EXIT VOD':'ON DEMAND';
    const sb=document.getElementById('sportsButton');if(sb)sb.textContent=sportsOpen?'EXIT SPORTS':'SPORTS';
    setGuideButtonLabel(guideOpen?'EXIT GUIDE':'GUIDE');
    showVol(h);
    if(sportsOpen){updateSportsLcd(h.tv_sports);return;}
    if(vodOpen){updateVodLcd(h.tv_vod);return;}
    if(guideOpen){
      const entry=h.tv_guide.entry||{};
      document.getElementById('ncCh').textContent=String(h.tv_guide.channel??'--').padStart(2,'0');
      document.getElementById('ncTitle').textContent=entry.title||'TV Guide';
      document.getElementById('ncSub').textContent=entry.subtitle||'';
      document.getElementById('ncLive').textContent='GUIDE';
      return;
    }
    if(h.is_vod){
      IS_VOD_PLAYING=true;
      document.getElementById('ncCh').textContent='OD';
      document.getElementById('ncTitle').textContent=h.vod_info?.title||'On Demand';
      document.getElementById('ncSub').textContent=h.vod_info?.subtitle||'';
      document.getElementById('ncLive').textContent=h.paused?'PAUSED':'ON DEMAND';
    }else{
      IS_VOD_PLAYING=false;
      CUR_CH=h.channel;PREV_CH=h.prev_channel;
      const e=h.entry||{};
      document.getElementById('ncCh').textContent=CUR_CH==null?'--':String(CUR_CH).padStart(2,'0');
      document.getElementById('ncTitle').textContent=e.title||'Off air';
      document.getElementById('ncSub').textContent=e.subtitle||'';
      document.getElementById('ncLive').textContent=h.mpv_alive?(h.paused?'PAUSED':'LIVE'):'OFF AIR';
    }
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
function prevCh(){
  return command(async()=>{
    if(vodOpen){await sendTvVodNav('back');return;}
    if(sportsOpen){await sendTvSportsNav('back');return;}
    if(PREV_CH!=null)return tuneNow(PREV_CH);
    notify('No previous channel yet.');
  });
}
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
function goLive(){
  return command(async()=>{
    if(vodOpen)await closeVodNow();
    if(sportsOpen)await closeSportsNow();
    if(guideOpen)await closeGuideNow();
    await tvApi('/api/vod/stop',{});
    await refreshNow();
    notify('Resumed live cable TV');
  });
}
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
  if(vodOpen){
    await sendTvVodNav('search', q);
    return;
  }
  if(sportsOpen){
    await sendTvSportsNav('search', q);
    return;
  }
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
    await refreshNow();
    if(sportsOpen)await closeSportsNow();
    if(vodOpen)await closeVodNow();
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
  guideOpen=false;
  document.body.classList.remove('guide-mode');
  setGuideButtonLabel('GUIDE');
  try{
    await tvApi('/api/tv-guide',{action:'close'});
  }catch(e){}
  await refreshNow();
}
function dpadUp(){return command(async()=>{await refreshNow();if(vodOpen)await sendTvVodNav('up');else if(sportsOpen)await sendTvSportsNav('up');else if(guideOpen)await moveGuideCh(-1);else await chStepNow(1);});}
function dpadDown(){return command(async()=>{await refreshNow();if(vodOpen)await sendTvVodNav('down');else if(sportsOpen)await sendTvSportsNav('down');else if(guideOpen)await moveGuideCh(1);else await chStepNow(-1);});}
function dpadLeft(){return command(async()=>{await refreshNow();if(vodOpen)await sendTvVodNav('left');else if(sportsOpen)await sendTvSportsNav('left');else if(guideOpen)await moveGuideCell(-1);});}
function dpadRight(){return command(async()=>{await refreshNow();if(vodOpen)await sendTvVodNav('right');else if(sportsOpen)await sendTvSportsNav('right');else if(guideOpen)await moveGuideCell(1);});}
function dpadOk(){return command(async()=>{await refreshNow();if(vodOpen)await sendTvVodNav('select');else if(sportsOpen)await sendTvSportsNav('select');else if(guideOpen)await selectGuideNow();});}
async function chStepNow(d){if(!CHANNELS.length)await refreshChs();const n=CHANNELS.map(c=>c.number);if(!n.length)return;const i=n.indexOf(CUR_CH);await tuneNow(n[i<0?0:(i+d+n.length)%n.length]);}
async function sendGuideNav(action){
  const result=await tvApi('/api/tv-guide/nav',{action});
  await refreshNow();
  if(result.message)notify(result.message);
}
async function moveGuideCh(d){await sendGuideNav(d<0?'up':'down');}
async function moveGuideCell(d){await sendGuideNav(d<0?'left':'right');}
async function selectGuideNow(){await sendGuideNav('ok');}

// ---- On-Demand (VOD) On-TV Control ----
function toggleVod(){
  return command(async()=>{
    await refreshNow();
    if(guideOpen)await closeGuideNow();
    if(sportsOpen)await closeSportsNow();
    if(vodOpen){
      await closeVodNow();
      return;
    }
    await openVodNow();
  });
}

async function openVodNow(){
  try{
    const res=await tvApi('/api/tv-vod',{action:'open'});
    if(res.visible){
      vodOpen=true;
      document.body.classList.add('vod-mode');
      const b=document.getElementById('vodButton');
      if(b)b.textContent='EXIT VOD';
      updateVodLcd(res);
      notify('On Demand screen opened on TV');
    }else{
      notify('Could not open On Demand on TV');
    }
  }catch(err){
    notify('Failed to open VOD: '+err.message);
  }
}

async function closeVodNow(){
  vodOpen=false;
  document.body.classList.remove('vod-mode');
  const b=document.getElementById('vodButton');
  if(b)b.textContent='ON DEMAND';
  try{
    await tvApi('/api/tv-vod',{action:'close'});
  }catch(e){}
  await refreshNow();
  notify('Closed On Demand');
}

function updateVodLcd(st){
  if(!st)return;
  document.getElementById('ncCh').textContent='OD';
  document.getElementById('ncLive').textContent='ON DEMAND';
  if(st.mode==='show'){
    document.getElementById('ncTitle').textContent=st.title||st.show_name||'Episode';
    document.getElementById('ncSub').textContent=`${st.show_name} S${st.season} • Press OK`;
  }else{
    document.getElementById('ncTitle').textContent=st.title||'On Demand';
    const cat=st.category?`${st.category} • `:''
    const prompt=st.kind==='show'?'Press OK for Episodes':'Press OK to Play';
    document.getElementById('ncSub').textContent=cat+prompt;
  }
}

async function sendTvVodNav(action,query){
  try{
    const res=await tvApi('/api/tv-vod/nav',{action,query});
    if(res.playing){
      vodOpen=false;
      IS_VOD_PLAYING=true;
      document.body.classList.remove('vod-mode');
      const b=document.getElementById('vodButton');
      if(b)b.textContent='ON DEMAND';
      document.getElementById('ncCh').textContent='OD';
      document.getElementById('ncLive').textContent='ON DEMAND';
      document.getElementById('ncTitle').textContent=res.title||'On Demand';
      document.getElementById('ncSub').textContent=res.subtitle||'';
      notify(`▶ Playing on TV: ${res.title}`);
      return;
    }
    if(res.closed){
      vodOpen=false;
      document.body.classList.remove('vod-mode');
      const b=document.getElementById('vodButton');
      if(b)b.textContent='ON DEMAND';
      await refreshNow();
      return;
    }
    if(res.visible){
      updateVodLcd(res);
    }
  }catch(err){
    notify('Navigation error: '+err.message);
  }
}

// ---- On-TV Sports Center Control ----
function toggleSports(){
  return command(async()=>{
    await refreshNow();
    if(guideOpen)await closeGuideNow();
    if(vodOpen)await closeVodNow();
    if(sportsOpen){
      await closeSportsNow();
      return;
    }
    await openSportsNow();
  });
}

async function openSportsNow(){
  try{
    const res=await tvApi('/api/tv-sports',{action:'open'});
    if(res.visible){
      sportsOpen=true;
      document.body.classList.add('sports-mode');
      const b=document.getElementById('sportsButton');
      if(b)b.textContent='EXIT SPORTS';
      updateSportsLcd(res);
      notify('Sports Center opened on TV');
    }else{
      notify('Could not open Sports on TV');
    }
  }catch(err){
    notify('Failed to open Sports: '+err.message);
  }
}

async function closeSportsNow(){
  sportsOpen=false;
  document.body.classList.remove('sports-mode');
  const b=document.getElementById('sportsButton');
  if(b)b.textContent='SPORTS';
  try{
    await tvApi('/api/tv-sports',{action:'close'});
  }catch(e){}
  await refreshNow();
  notify('Closed Sports Center');
}

function updateSportsLcd(st){
  if(!st)return;
  document.getElementById('ncCh').textContent='SP';
  document.getElementById('ncLive').textContent='SPORTS';
  if(st.mode==='game'){
    document.getElementById('ncTitle').textContent=st.title||'Game Center';
    document.getElementById('ncSub').textContent=`${st.subtitle||''} • Press OK for Announcer`;
  }else{
    document.getElementById('ncTitle').textContent=st.title||'Sports Center';
    const cat=st.category?`${st.category} • `:''
    document.getElementById('ncSub').textContent=cat+'Press OK for Field & Plays';
  }
}

async function sendTvSportsNav(action,query){
  try{
    const res=await tvApi('/api/tv-sports/nav',{action,query});
    if(res.closed){
      sportsOpen=false;
      document.body.classList.remove('sports-mode');
      const b=document.getElementById('sportsButton');
      if(b)b.textContent='SPORTS';
      await refreshNow();
      return;
    }
    if(res.announced){
      notify('🎙️ Announcing play over TV audio...');
    }
    if(res.visible){
      updateSportsLcd(res);
    }
  }catch(err){
    notify('Sports navigation error: '+err.message);
  }
}

let pairExpiryTimer = null;
let pairExpiresAt = 0;

function togglePairModal(){
  const p = document.getElementById('rpair');
  if(!p) return;
  p.hidden = !p.hidden;
  if(!p.hidden){
    generateNewPairCode();
    refreshPairedDevices();
  } else {
    clearInterval(pairExpiryTimer);
  }
}

async function generateNewPairCode(){
  const codeEl = document.getElementById('pairCodeDisplay');
  const cdEl = document.getElementById('pairCountdown');
  if(codeEl) codeEl.textContent = '...';
  try{
    const r = await tvApi('/api/pair/generate', {});
    if(r.ok && r.code){
      if(codeEl) codeEl.textContent = r.code;
      pairExpiresAt = Date.now() + (r.expires_in * 1000);
      clearInterval(pairExpiryTimer);
      updatePairCountdown();
      pairExpiryTimer = setInterval(updatePairCountdown, 1000);
    }
  }catch(e){
    if(codeEl) codeEl.textContent = 'ERROR';
    notify('Failed to generate pair code: ' + e.message);
  }
}

function updatePairCountdown(){
  const cdEl = document.getElementById('pairCountdown');
  if(!cdEl) return;
  const rem = Math.max(0, Math.floor((pairExpiresAt - Date.now()) / 1000));
  const mins = Math.floor(rem / 60);
  const secs = rem % 60;
  cdEl.textContent = rem > 0 ? `Code expires in ${mins}:${String(secs).padStart(2, '0')}` : 'Code expired. Tap New Code.';
}

async function refreshPairedDevices(){
  const listEl = document.getElementById('pairedDevicesList');
  if(!listEl) return;
  try{
    const r = await tvApi('/api/pair/devices');
    const devs = r.devices || [];
    if(!devs.length){
      listEl.innerHTML = '<p class="hint" style="font-size:11px">No companion devices paired yet.</p>';
      return;
    }
    listEl.innerHTML = '<b style="font-size:11px">Paired Devices:</b>' + devs.map(d => `
      <div style="display:flex;justify-content:space-between;align-items:center;padding:4px 0;border-bottom:1px solid #233040;font-size:11px">
        <span><b>${esc(d.device_name)}</b></span>
        <button class="ghost" style="padding:2px 8px;font-size:9px" onclick="revokePairedDevice('${esc(d.device_id)}')">REVOKE</button>
      </div>
    `).join('');
  }catch(e){}
}

async function revokePairedDevice(deviceId){
  try{
    await tvApi('/api/pair/revoke', {device_id: deviceId});
    notify('Device revoked');
    refreshPairedDevices();
  }catch(e){
    notify('Failed to revoke: ' + e.message);
  }
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
