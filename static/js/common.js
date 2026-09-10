'use strict';
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function tvTime(ts){return new Date(ts*1000).toLocaleTimeString('en-US',{timeZone:window.TV_TIMEZONE,hour:'numeric',minute:'2-digit'});}
function notify(message){const n=document.getElementById('notice');n.textContent=message;n.hidden=false;clearTimeout(window.noticeTimer);window.noticeTimer=setTimeout(()=>n.hidden=true,4500);}
async function tvApi(path,data){
  const response=await fetch(path,{cache:'no-store',signal:AbortSignal.timeout(12000),...(data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)})});
  const result=await response.json();
  if(!response.ok||result.ok===false)throw new Error(result.error||'The receiver could not complete that request.');
  return result;
}
function clockTick(){document.getElementById('clock').textContent=new Date().toLocaleString('en-US',{timeZone:window.TV_TIMEZONE,weekday:'short',hour:'numeric',minute:'2-digit'});}
clockTick();setInterval(()=>{if(!document.hidden)clockTick();},15000);
window.addEventListener('unhandledrejection',e=>{notify(e.reason?.message||'Connection lost. Check your Wi-Fi and try again.');e.preventDefault();});

// Favorite-channel "starting soon" toast, polled from every page so it fires
// wherever the viewer happens to have a tab open (phone remote, guide, etc.).
const _alertedFavIds=new Set();
async function checkFavAlerts(){
  try{
    const r=await tvApi('/api/favorites/upcoming');
    for(const e of r.upcoming||[]){
      if(_alertedFavIds.has(e.id))continue;
      _alertedFavIds.add(e.id);
      const mins=Math.max(1,Math.round((e.start_ts-Date.now()/1000)/60));
      notify(`★ ${e.title} starts in ${mins} min on CH ${String(e.channel_number).padStart(2,'0')}${e.channel_name?' · '+e.channel_name:''}`);
    }
  }catch(e){/* best-effort; a missed poll just tries again next cycle */}
}
checkFavAlerts();setInterval(()=>{if(!document.hidden)checkFavAlerts();},30000);

// Reminder banner: shared across every page since the phone remote is this app's
// only truly interactive control surface for the TV -- tapping TUNE NOW here is
// the "click it" action; on the TV itself the same reminder shows as a plain
// mpv OSD banner (playback.osd_message) that just auto-hides after the same window.
const _seenReminderIds=new Set();
let _reminderTimer=null, _reminderChannel=null;
async function checkReminders(){
  try{
    const r=await tvApi('/api/reminders/due');
    const fresh=(r.due||[]).find(rem=>!_seenReminderIds.has(rem.id));
    if(fresh){_seenReminderIds.add(fresh.id);showReminderBanner(fresh);}
  }catch(e){/* best-effort */}
}
function showReminderBanner(rem){
  const b=document.getElementById('reminderBanner');
  if(!b)return;
  _reminderChannel=rem.channel_number;
  document.getElementById('reminderTitle').textContent=`⏰ ${rem.title}`;
  document.getElementById('reminderSub').textContent=`${rem.subtitle?rem.subtitle+' · ':''}CH ${String(rem.channel_number).padStart(2,'0')} · ${rem.start_fmt}`;
  b.hidden=false;
  clearTimeout(_reminderTimer);
  _reminderTimer=setTimeout(dismissReminderBanner,10000);
}
function dismissReminderBanner(){const b=document.getElementById('reminderBanner');if(b)b.hidden=true;clearTimeout(_reminderTimer);_reminderChannel=null;}
async function reminderTuneNow(){
  if(_reminderChannel==null)return;
  const ch=_reminderChannel;
  dismissReminderBanner();
  try{await tvApi('/api/tune',{channel:ch});notify(`Tuned to CH ${ch}`);}catch(e){notify(e.message);}
}
checkReminders();setInterval(()=>{if(!document.hidden)checkReminders();},15000);
