let CUR=null, PREV=null, digitBuf='', digitTimer;
async function refreshTV(){
  if(document.hidden)return;
  try{
    const h=await tvApi('/api/hdmi');CUR=h.channel;PREV=h.prev_channel;
    document.getElementById('channelDisplay').textContent=CUR==null?'---':String(CUR).padStart(3,'0');
    document.getElementById('signal').textContent=h.mpv_alive?(h.paused?'PAUSED':'LIVE · STEREO'):'OFF AIR';
    const e=h.entry||{};
    document.getElementById('programTitle').textContent=e.title||'Off air';
    document.getElementById('programSubtitle').textContent=e.subtitle||'';
    document.getElementById('programDescription').textContent=e.description||'Your own collection, playing around the clock.';
    document.getElementById('programTime').textContent=e.start_ts?`${tvTime(e.start_ts)} – ${tvTime(e.end_ts)}`:'';
    document.getElementById('programProgress').style.width=e.end_ts?`${Math.max(0,Math.min(100,100*(h.server_time-e.start_ts)/(e.end_ts-e.start_ts)))}%`:'0%';
    document.querySelectorAll('[data-channel]').forEach(b=>b.classList.toggle('selected',+b.dataset.channel===CUR));
  }catch(e){document.getElementById('signal').textContent='RECONNECTING';}
}
async function tuneHDMI(ch){await tvApi('/api/tune',{channel:ch});await refreshTV();notify(`TV tuned to channel ${ch}`);}
async function chDelta(d){const j=await tvApi('/api/channels');const n=j.channels.filter(c=>c.enabled).map(c=>c.number);if(!n.length)return;let i=n.indexOf(CUR);await tuneHDMI(n[(i+d+n.length)%n.length]);}
function prevCh(){if(PREV!=null)return tuneHDMI(PREV);notify('No previous channel yet.');}
document.addEventListener('keydown',async e=>{
  if(/INPUT|TEXTAREA|SELECT/.test(e.target.tagName))return;
  if(e.key==='PageUp'){e.preventDefault();await chDelta(1);}
  else if(e.key==='PageDown'){e.preventDefault();await chDelta(-1);}
  else if(e.key.toLowerCase()==='g')location.href='/guide';
  else if(e.key.toLowerCase()==='i')await tvApi('/api/info',{});
  else if(e.key.toLowerCase()==='m'){const h=await tvApi('/api/hdmi');await tvApi('/api/volume',{muted:h.muted!=='1'});}
  else if(e.key===' '){e.preventDefault();await tvApi('/api/volume',{toggle_pause:true});}
  else if(/^[0-9]$/.test(e.key)){digitBuf=(digitBuf+e.key).slice(-3);clearTimeout(digitTimer);digitTimer=setTimeout(()=>{tuneHDMI(+digitBuf);digitBuf='';},1000);}
});

refreshTV();setInterval(refreshTV,5000);document.addEventListener('visibilitychange',refreshTV);
const requested=new URLSearchParams(location.search).get('tune');
if(requested){history.replaceState(null,'','/');tuneHDMI(+requested);}
