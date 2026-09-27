let CH=null, liveOffset=0, mediaKey=null, updating=false, vodItem=null, streamStart=0, streamOpenedAt=0;
async function initPlayer(ch,off,wantFS,vod){
  vodItem=vod||null;CH=vodItem?null:ch;liveOffset=off||0;const v=document.getElementById('v');
  if(vodItem){
    document.getElementById('now').textContent=`${vodItem.title||''} ${vodItem.subtitle||''}`.trim();
    v.addEventListener('timeupdate',()=>{
      if(v.duration){
        const pct=Math.max(0,Math.min(100,(v.currentTime/v.duration)*100));
        document.getElementById('bar').style.width=pct+'%';
      }
    });
    v.addEventListener('loadedmetadata',()=>{v.play().then(()=>{document.getElementById('playPrompt').hidden=true;}).catch(()=>{document.getElementById('playPrompt').hidden=false;});});
    v.src='/stream/file/'+vodItem.media_id+'?fragment=1&start=0';
    setupWatchControls();
    if(wantFS&&document.documentElement.requestFullscreen)document.documentElement.requestFullscreen().catch(()=>{});
    return;
  }
  if(CH==null){document.getElementById('now').textContent='No channels configured.';return;}
  v.addEventListener('loadedmetadata',()=>{v.play().then(()=>{document.getElementById('playPrompt').hidden=true;}).catch(()=>{document.getElementById('playPrompt').hidden=false;});});
  v.addEventListener('ended',()=>syncInfo(true));
  v.addEventListener('error',()=>notify('This show could not be streamed. Try another channel.'));
  setupWatchControls();
  if(wantFS&&document.documentElement.requestFullscreen)document.documentElement.requestFullscreen().catch(()=>{});
  await syncInfo();setInterval(()=>syncInfo(),5000);
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)syncInfo(true);});
  document.addEventListener('keydown',e=>{if(e.key==='ArrowUp'){e.preventDefault();step(1);}else if(e.key==='ArrowDown'){e.preventDefault();step(-1);}else if(e.key.toLowerCase()==='g')location.href='/guide';});
}
function goFS(){const v=document.getElementById('v'),target=document.getElementById('tvwrap');if(target.requestFullscreen)target.requestFullscreen().catch(()=>{});else if(v.webkitEnterFullscreen)v.webkitEnterFullscreen();v.play().then(()=>{document.getElementById('playPrompt').hidden=true;}).catch(()=>{document.getElementById('playPrompt').hidden=false;});}
async function playOnTV(){if(CH==null)return;await tvApi('/api/tune',{channel:CH});notify(`Channel ${CH} is playing on your TV.`);}
async function playVodOnTV(id){await tvApi('/api/vod/play',{media_id:id});notify('Playing on your living-room TV.');}
async function syncInfo(force=false){
  if(updating||CH==null||document.hidden)return;updating=true;
  try{
    const r=await tvApi('/api/now/'+CH),v=document.getElementById('v'),changed=mediaKey!==null&&mediaKey!==r.media_key;
    liveOffset=r.offset;
    if(mediaKey===null||changed||(force&&v.ended)){
      if(r.has_media){streamStart=r.offset;streamOpenedAt=Date.now();v.src='/stream/live/'+CH+'?fragment=1&start='+encodeURIComponent(streamStart)+'&program='+r.media_key;v.load();}
      else{v.removeAttribute('src');v.load();}
    }
    mediaKey=r.media_key;
    if(!changed&&!v.paused&&v.readyState>=2&&Date.now()-streamOpenedAt>30000&&Math.abs(streamStart+v.currentTime-r.offset)>90){streamStart=r.offset;streamOpenedAt=Date.now();v.src='/stream/live/'+CH+'?fragment=1&start='+encodeURIComponent(streamStart);v.load();}
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
