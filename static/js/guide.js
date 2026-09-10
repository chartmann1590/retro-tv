let tvGuideVisible=new URLSearchParams(location.search).get("tv")==="1", overlayTimer;
const H=3;
let SLOT=180, SCALE=SLOT/1800, selectLive=true, initialGuide=true;
let T0=Math.floor(Date.now()/1800000)*1800, DATA=[], sel={r:0,c:0}, followNow=true, requestVersion=0;
let serverDelta=0, favOnly=false, pendingSelect=null;
const now=()=>Date.now()/1000+serverDelta;
const isLive=e=>now()>=e.start_ts&&now()<e.end_ts;
async function fetchGuide(){
  const version=++requestVersion;
  if(followNow)T0=Math.floor(now()/1800)*1800;
  try{
    const r=await tvApi(`/api/guide?hours=${H}&start=${T0}`);
    if(version!==requestVersion)return;
    if(r.server_time)serverDelta=r.server_time-Date.now()/1000;
    const previousId=DATA[sel.r]?.entries[sel.c]?.id;
    DATA=favOnly?r.guide.filter(row=>row.channel.favorite):r.guide;T0=r.start;
    let jumped=false;
    if(pendingSelect){
      const {channel,id}=pendingSelect;pendingSelect=null;
      const ri=DATA.findIndex(row=>row.channel.number===channel);
      if(ri>=0){sel.r=ri;const ci=DATA[ri].entries.findIndex(e=>e.id===id);if(ci>=0){sel.c=ci;jumped=true;}}
    }
    if(!jumped){
      if(initialGuide&&r.current_channel!=null){sel.r=Math.max(0,DATA.findIndex(row=>row.channel.number===r.current_channel));}
      initialGuide=false;
      if(selectLive){sel.c=Math.max(0,DATA[sel.r]?.entries.findIndex(isLive)??0);selectLive=false;}
      else if(previousId!=null){const i=DATA[sel.r]?.entries.findIndex(e=>e.id===previousId);if(i>=0)sel.c=i;}
    }else{initialGuide=false;selectLive=false;}
    sel.r=Math.max(0,Math.min(sel.r,DATA.length-1));
    sel.c=Math.max(0,Math.min(sel.c,(DATA[sel.r]?.entries.length||1)-1));
    document.getElementById('guideStatus').textContent=DATA.length?'Select a program · Tune it on your TV':(favOnly?'No favorite channels yet — set them in Setup.':'Select a program to see details');
    render();
  }catch(e){document.getElementById('guideStatus').textContent='Connection lost. Retrying…';}
}

// ---------- search ----------
let guideSearchResults=[], guideSearchT;
function onGuideSearch(){clearTimeout(guideSearchT);guideSearchT=setTimeout(runGuideSearch,250);}
async function runGuideSearch(){
  const q=document.getElementById('guideSearch').value.trim();
  const box=document.getElementById('guideSearchResults');
  if(!q){box.hidden=true;box.innerHTML='';return;}
  try{
    const r=await tvApi('/api/guide/search?q='+encodeURIComponent(q));
    guideSearchResults=r.results||[];
    box.innerHTML=guideSearchResults.map((e,i)=>`<div class="search-hit" onclick="pickGuideSearch(${i})"><b>CH ${String(e.channel_number).padStart(2,'0')}</b>${esc(e.title)} ${esc(e.subtitle||'')} <span class="hint">${e.is_live?'ON NOW':'at '+esc(e.start_fmt)}</span></div>`).join('')||'<div class="search-hit hint">No matches.</div>';
    box.hidden=false;
  }catch(err){box.innerHTML='<div class="search-hit hint">Search unavailable.</div>';box.hidden=false;}
}
function pickGuideSearch(i){
  const e=guideSearchResults[i];if(!e)return;
  document.getElementById('guideSearchResults').hidden=true;
  document.getElementById('guideSearch').value='';
  followNow=false;
  pendingSelect={channel:e.channel_number,id:e.id};
  T0=Math.floor(e.start_ts/1800)*1800;
  fetchGuide();
}
document.addEventListener('click',e=>{if(!e.target.closest('#guideSearchWrap'))document.getElementById('guideSearchResults').hidden=true;});
function toggleFavGuide(){
  favOnly=!favOnly;
  const b=document.getElementById('favToggleGuide');b.classList.toggle('active',favOnly);b.setAttribute('aria-pressed',String(favOnly));
  sel.r=0;selectLive=true;fetchGuide();
}
function render(){
  const grid=document.getElementById('grid');
  const channelWidth=matchMedia('(max-width:600px)').matches?108:166;
  SLOT=Math.max(180,(grid.clientWidth-channelWidth)/(H*2));SCALE=SLOT/1800;
  const width=H*3600*SCALE;
  const date=new Date(T0*1000).toLocaleDateString('en-US',{timeZone:window.TV_TIMEZONE,weekday:'long',month:'short',day:'numeric'});
  document.getElementById('guideDate').textContent=date;
  let html=`<div class="guide-row guide-head"><div class="guide-ch guide-corner">CHANNEL</div><div class="guide-timeline" style="width:${width}px">`;
  for(let i=0;i<H*2;i++)html+=`<div class="guide-time" style="width:${SLOT}px">${tvTime(T0+i*1800)}</div>`;
  html+='</div></div>';
  DATA.forEach((row,ri)=>{
    html+=`<div class="guide-row"><div class="guide-ch"><span class="gnum">${String(row.channel.number).padStart(2,'0')}</span><span>${esc(row.channel.name)}</span></div><div class="guide-track" style="width:${width}px">`;
    row.entries.forEach((e,ci)=>{
      const start=Math.max(T0,e.start_ts),end=Math.min(T0+H*3600,e.end_ts);
      if(end<=start)return;
      const live=isLive(e),selected=sel.r===ri&&sel.c===ci;
      html+=`<button class="gcell${live?' live':''}${selected?' sel':''}${e.kind==='commercial_break'?' brk':''}" data-r="${ri}" data-c="${ci}" aria-pressed="${selected}" style="left:${(start-T0)*SCALE}px;width:${(end-start)*SCALE}px" onclick="pick(${ri},${ci})" ondblclick="playOnTV()" title="${esc(e.title)} · ${esc(e.subtitle)} · ${tvTime(e.start_ts)}"><span class="gt">${esc(e.title)}</span><span class="gs">${live?'● ':''}${esc(e.subtitle||tvTime(e.start_ts))}</span></button>`;
    });
    if(now()>=T0&&now()<T0+H*3600)html+=`<span class="now-line" style="left:${(now()-T0)*SCALE}px" aria-hidden="true"></span>`;
    html+='</div></div>';
  });
  grid.innerHTML=DATA.length?html:'<p class="empty-state">No channels yet. Add your lineup in Setup.</p>';
  showDetail();
}
function pick(r,c){sel={r,c};document.querySelectorAll('.gcell').forEach(b=>{const selected=+b.dataset.r===r&&+b.dataset.c===c;b.classList.toggle('sel',selected);b.setAttribute('aria-pressed',String(selected));});showDetail();}
function reveal(){document.querySelector(`.gcell[data-r="${sel.r}"][data-c="${sel.c}"]`)?.scrollIntoView({block:'nearest',inline:'nearest'});}
function moveCh(d){if(!DATA.length)return;const old=DATA[sel.r]?.entries[sel.c];const r=Math.max(0,Math.min(DATA.length-1,sel.r+d));const c=Math.max(0,DATA[r].entries.findIndex(e=>e.end_ts>(old?.start_ts||T0)));pick(r,c);reveal();}
function moveCell(d){const row=DATA[sel.r];if(!row)return;pick(sel.r,Math.max(0,Math.min(row.entries.length-1,sel.c+d)));reveal();}
function moveT(d){followNow=false;T0+=d*3600;sel.c=0;fetchGuide();}
function resetT(){followNow=true;selectLive=true;sel.c=0;document.getElementById('grid').scrollLeft=0;fetchGuide();}
function curSel(){const row=DATA[sel.r];return {row,e:row?.entries[sel.c]};}
function showDetail(){
  clearTimeout(overlayTimer);
  if(tvGuideVisible)overlayTimer=setTimeout(syncTVGuide,120);
  const {row,e}=curSel();
  if(!e){document.getElementById('dTitle').textContent='No programs in this time window';document.getElementById('dBody').textContent='Try Now or choose another channel.';document.getElementById('dActions').innerHTML='';document.getElementById('dArt').hidden=true;return;}
  document.getElementById('dMeta').textContent=`CHANNEL ${row.channel.number} · ${tvTime(e.start_ts)} – ${tvTime(e.end_ts)}${isLive(e)?' · ON NOW':''}`;
  document.getElementById('dTitle').textContent=e.title;
  const art=document.getElementById('dArt');
  if(e.artwork){art.src=e.artwork;art.alt=e.title;art.hidden=false;}else{art.hidden=true;art.removeAttribute('src');}
  document.getElementById('dBody').innerHTML=`<div class="dsub">${esc(e.subtitle)}</div><p>${esc(e.description||'Your favorites, right where you left the dial.')}</p>`;
  const actions=document.getElementById('dActions');
  if(isLive(e)&&e.kind!=='slate'){
    actions.innerHTML=`<button onclick="playOnTV()">▶ TUNE TV</button><a class="text-link" href="/watch/${row.channel.number}">Watch here &rarr;</a>`;
  }else if((e.kind==='episode'||e.kind==='movie')&&e.start_ts>now()){
    actions.innerHTML=`<button class="ghost" onclick="remindMe(${e.id},this)">⏰ Remind Me</button><span class="hint">Coming up next</span>`;
  }else if(e.kind==='slate'){
    actions.innerHTML='<span class="hint">Off air</span>';
  }else if(e.start_ts>now()){
    actions.innerHTML='<span class="hint">Coming up next</span>';
  }else{
    actions.innerHTML='<span class="hint">Previously aired</span>';
  }
}
async function remindMe(id,btn){
  try{
    await tvApi('/api/reminders',{entry_id:id});
    btn.textContent='⏰ Reminder set';btn.disabled=true;
    notify('We’ll flash it on the TV — and push to your phone, if Gotify is set up in Settings — a couple minutes before it starts.');
  }catch(err){notify(err.message);}
}
async function playOnTV(){const {row,e}=curSel();if(!e||!isLive(e)||e.kind==='slate')return;await tvApi('/api/tune',{channel:row.channel.number});tvGuideVisible=false;guideControls();notify(`TV tuned to ${row.channel.number} · ${row.channel.name}`);}
document.addEventListener('keydown',e=>{
  if(/INPUT|TEXTAREA|SELECT/.test(e.target.tagName))return;
  const actions={ArrowUp:()=>moveCh(-1),ArrowDown:()=>moveCh(1),ArrowLeft:()=>moveCell(-1),ArrowRight:()=>moveCell(1)};
  if(actions[e.key]){e.preventDefault();actions[e.key]();}
  else if(e.key==='Enter'&&(e.target===document.body||e.target.closest('#grid'))){e.preventDefault();playOnTV();}
  else if(e.key==='Escape')location.href='/';
});
document.getElementById('guideZone').textContent=window.TV_TIMEZONE;
fetchGuide();setInterval(()=>{if(!document.hidden)fetchGuide();},30000);document.addEventListener('visibilitychange',()=>{if(!document.hidden)fetchGuide();});

function guideControls(){document.getElementById('tvGuideToggle').textContent=tvGuideVisible?'Close TV guide':'Show on TV';document.getElementById('guidePad').hidden=!tvGuideVisible;}
async function syncTVGuide(){const {row,e}=curSel();if(!tvGuideVisible||!row)return;try{await tvApi('/api/tv-guide',{channel:row.channel.number,entry_id:e?.id,start:T0});}catch(err){tvGuideVisible=false;guideControls();notify(err.message);}}
async function toggleTVGuide(){if(tvGuideVisible){await tvApi('/api/tv-guide',{action:'close'});tvGuideVisible=false;}else{tvGuideVisible=true;await syncTVGuide();}guideControls();}
guideControls();

function guideFullscreen(){if(!document.fullscreenElement)document.documentElement.requestFullscreen?.().catch(()=>notify('Use your browser’s fullscreen option.'));else document.exitFullscreen?.();}
function guideClock(){document.getElementById('guideClock').textContent=tvTime(Date.now()/1000);}
guideClock();setInterval(guideClock,15000);
// Kiosk/permitted browsers can enter immediately; mobile browsers require the button tap.
document.documentElement.requestFullscreen?.().catch(()=>{});

let resizeTimer;window.addEventListener("resize",()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(render,150);});
