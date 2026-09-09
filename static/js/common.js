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
