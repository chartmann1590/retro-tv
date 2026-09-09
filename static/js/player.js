let CH=null, liveOffset=0, mediaKey=null, updating=false, triedHLS=false;
async function initPlayer(ch,off,wantFS){
  CH=ch;liveOffset=off||0;const v=document.getElementById('v');
  if(CH==null){document.getElementById('now').textContent='No channels configured.';return;}
  v.addEventListener('loadedmetadata',()=>{if(Number.isFinite(v.duration))v.currentTime=Math.min(liveOffset,Math.max(0,v.duration-0.1));v.play().then(()=>{document.getElementById('playPrompt').hidden=true;}).catch(()=>{document.getElementById('playPrompt').hidden=false;});});
  v.addEventListener('ended',()=>syncInfo(true));
  v.addEventListener('error',async()=>{
    if(triedHLS||!v.canPlayType('application/vnd.apple.mpegurl')){notify('This browser cannot play the file. Use “Play on living-room TV” for HDMI playback.');return;}
    triedHLS=true;
    try{const h=await tvApi('/api/hls/'+CH);liveOffset=h.offset;v.src=h.url;}catch(e){notify(e.message);}
  });
  setupWatchControls();
  if(wantFS&&document.documentElement.requestFullscreen)document.documentElement.requestFullscreen().catch(()=>{});
  await syncInfo();setInterval(()=>syncInfo(),5000);
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)syncInfo(true);});
  document.addEventListener('keydown',e=>{if(e.key==='ArrowUp'){e.preventDefault();step(1);}else if(e.key==='ArrowDown'){e.preventDefault();step(-1);}else if(e.key.toLowerCase()==='g')location.href='/guide';});
}
function goFS(){const v=document.getElementById('v'),target=document.getElementById('tvwrap');if(target.requestFullscreen)target.requestFullscreen().catch(()=>{});else if(v.webkitEnterFullscreen)v.webkitEnterFullscreen();v.play().then(()=>{document.getElementById('playPrompt').hidden=true;}).catch(()=>{document.getElementById('playPrompt').hidden=false;});}
async function playOnTV(){if(CH==null)return;await tvApi('/api/tune',{channel:CH});notify(`Channel ${CH} is playing on your TV.`);}
async function syncInfo(force=false){
  if(updating||CH==null||document.hidden)return;updating=true;
  try{
    const r=await tvApi('/api/now/'+CH),v=document.getElementById('v'),changed=mediaKey!==null&&mediaKey!==r.media_key;
    liveOffset=r.offset;
    if(changed||(force&&v.ended)){triedHLS=false;if(r.has_media){v.src='/stream/live/'+CH+'?program='+r.media_key;v.load();}else{v.removeAttribute('src');v.load();}}
    mediaKey=r.media_key;
    if(!changed&&!v.paused&&Number.isFinite(v.duration)&&Math.abs(v.currentTime-r.offset)>12)v.currentTime=Math.min(r.offset,Math.max(0,v.duration-0.1));
    document.getElementById('now').textContent=`${r.entry.title||''} ${r.entry.subtitle||''} | ${r.range}`;
    const pct=Math.max(0,Math.min(100,r.offset/(r.duration||1)*100));
    document.getElementById('bar').style.width=pct+'%';
    if(changed||!window.playerHasInfo){const osd=document.getElementById('osd');osd.classList.add('show');osd.innerHTML=`<div class="chnum">${String(CH).padStart(2,'0')}</div><div class="chname">${esc(r.entry.title)}</div><div class="prog">${esc(r.entry.subtitle)}</div><div class="timer">${esc(r.range)}</div>`;clearTimeout(window.playerOSD);window.playerOSD=setTimeout(()=>osd.classList.remove('show'),4000);window.playerHasInfo=true;}
  }catch(e){notify(e.message);}finally{updating=false;}
}
async function step(d){const j=await tvApi('/api/channels'),n=j.channels.filter(c=>c.enabled).map(c=>c.number);if(n.length)location.href='/watch/'+n[(n.indexOf(CH)+d+n.length)%n.length];}
function toggleMute(){const v=document.getElementById('v');v.muted=!v.muted;document.getElementById('watchMute').textContent=v.muted?'UNMUTE':'MUTE';}

let watchHideTimer;
function showWatchControls(){const w=document.getElementById('tvwrap');w.classList.add('controls-visible');clearTimeout(watchHideTimer);watchHideTimer=setTimeout(()=>{if(!document.getElementById('v').paused)w.classList.remove('controls-visible');},3000);}
function setupWatchControls(){
  const w=document.getElementById('tvwrap'),v=document.getElementById('v');
  w.addEventListener('pointermove',showWatchControls);
  w.addEventListener('pointerdown',showWatchControls);
  w.addEventListener('focusin',showWatchControls);
  v.addEventListener('pause',()=>{showWatchControls();document.getElementById('watchPause').textContent='▶ PLAY';});
  v.addEventListener('play',()=>{showWatchControls();document.getElementById('watchPause').textContent='Ⅱ PAUSE';});
  document.addEventListener('keydown',e=>{showWatchControls();if(e.key===' '&&e.target===w){e.preventDefault();togglePause();}else if(e.key.toLowerCase()==='f')goFS();});
  showWatchControls();
}
function togglePause(){const v=document.getElementById('v');if(v.paused)startWatching();else v.pause();}
function startWatching(){const v=document.getElementById('v');v.play().then(()=>{document.getElementById('playPrompt').hidden=true;showWatchControls();}).catch(()=>notify('Playback could not start. Try another channel.'));goFS();}
