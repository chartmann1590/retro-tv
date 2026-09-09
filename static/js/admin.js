function tab(id){document.querySelectorAll('.tab').forEach(t=>t.classList.remove('on'));document.getElementById('t-'+id).classList.add('on');if(id==='logs')loadLogs();}
async function jget(p){return (await fetch(p)).json();}
async function jpost(p,b){return (await fetch(p,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b||{})})).json();}
loadDash();loadCh();loadLib();loadRem();loadHdmi();loadSys();loadSets();
async function loadDash(){
  const s=await jget('/api/system');
  document.getElementById('dash').innerHTML=s.ok?`
    Episodes: <b>${s.library.episodes}</b> | Movies: <b>${s.library.movies}</b> | Commercials: <b>${s.library.commercials}</b><br>
    Disk free: <b>${(s.disk.free/1e9).toFixed(1)} GB</b> | mpv: <b>${s.mpv?'installed':'MISSING (fallback cvlc)'}</b> alive=${s.mpv_alive}<br>
    ${s.library.warnings.map(w=>`<div class="warn">${w.path}: ${w.compat_warning}</div>`).join('')||'<div class="okbox">No compatibility warnings.</div>'}`:'err';
}
async function rescan(full){const r=await jpost('/api/scan',{full});alert(`Scan: +${r.added} new, ${r.updated} updated, total ${r.total}. Metadata: ${r.metadata_enriched||0} enriched.`);loadDash();loadLib();}
async function loadCh(){
  const c=await jget('/api/channels');
  document.getElementById('chedit').innerHTML=c.channels.map(x=>`<div><b>${x.number}</b> ${x.name} ${x.enabled?'':'[OFF]'} (${x.ordering}/${x.commercial_mode})
    <button class="ghost" onclick="editCh(${x.number})">EDIT</button> <button class="ghost" onclick="delCh(${x.number})">DEL</button></div>`).join('')||'No channels yet.';
}
async function editCh(n){const c=await jget('/api/channels');const x=c.channels.find(y=>y.number===n);if(!x)return;
  document.getElementById('cnum').value=x.number;document.getElementById('cname').value=x.name;
  document.getElementById('ccolor').value=x.color||'#1a3a6b';document.getElementById('corder').value=x.ordering;
  document.getElementById('ccom').value=x.commercial_mode;document.getElementById('cen').value=x.enabled?'1':'0';
  const s=await (await fetch('/api/channels')).json();
  const srcs=await jget('/api/channels');
  // fetch sources via library? simplified: keep textarea
  tab('ch');window.scrollTo(0,0);
}
async function delCh(n){if(!confirm('Delete channel '+n+'?'))return;await fetch('/api/channel/'+n,{method:'DELETE'});loadCh();}
async function saveCh(){
  const lines=document.getElementById('csrc').value.split('\n').map(s=>s.trim()).filter(Boolean);
  const sources=lines.map(l=>{
    if(l.startsWith('show:'))return{type:'show',value:l.slice(5)};
    if(l.startsWith('season:'))return{type:'season',value:l.slice(7)};
    if(l==='all_tv')return{type:'all_tv',value:''};
    if(l==='all_movies')return{type:'all_movies',value:''};
    if(l.startsWith('movie:'))return{type:'movie',value:l.slice(6)};
    return{type:'show',value:l};
  });
  const body={number:+document.getElementById('cnum').value,name:document.getElementById('cname').value,
    color:document.getElementById('ccolor').value,ordering:document.getElementById('corder').value,
    commercial_mode:document.getElementById('ccom').value,enabled:document.getElementById('cen').value==='1',sources};
  const r=await jpost('/api/channel',body);
  document.getElementById('chmsg').textContent=r.ok?'Saved.':'Error';
  loadCh();
}
async function loadLib(){
  const l=await jget('/api/library');
  document.getElementById('lib').innerHTML=`<b>Shows:</b><br>${l.shows.map(s=>`<span class="chip">${s.name} (${s.c})</span>`).join('')||'none'}<br><br>
  Episodes ${l.episodes} | Movies ${l.movies} | Commercials ${l.commercials}<br>${l.warnings.map(w=>`<div class="warn">${w.path}: ${w.compat_warning}</div>`).join('')}`;
}
async function loadSched(){
  const ch=document.getElementById('gch').value||2;const day=document.getElementById('gday').value;
  const r=await jget(`/api/schedule?channel=${ch}&day=${day||''}`);
  document.getElementById('sched').innerHTML=(r.entries||[]).map(e=>`<div>${e.start_fmt}-${e.end_fmt} <b>${e.title}</b> ${e.subtitle||''} <i>(${e.kind})</i></div>`).join('')||'none';
  if(!document.getElementById('gday').value&&r.day)document.getElementById('gday').value=r.day;
}
async function regen(){const r=await jpost('/api/regen',{channel:document.getElementById('gch').value,day:document.getElementById('gday').value});alert(r.ok?`Regenerated ${r.entries} entries for ${r.day}`:(r.error||'failed'));}
async function loadRem(){
  const r=await jget('/api/remote');
  document.getElementById('rem').innerHTML=Object.entries(r.mappings).map(([a,c])=>`<div class="kv"><label>${a}</label><input data-act="${a}" value="${c}" placeholder="click then press key"></div>`).join('');
  document.querySelectorAll('#rem input').forEach(inp=>{
    inp.addEventListener('keydown',async e=>{e.preventDefault();inp.value=e.key;await jpost('/api/remote',{action:inp.dataset.act,code:e.key});});
    inp.addEventListener('change',async()=>{await jpost('/api/remote',{action:inp.dataset.act,code:inp.value});});
  });
  document.getElementById('devs').innerHTML=(r.devices||[]).map(d=>`<div>${d.path} ${d.readable?'(readable)':'(no read perm)'}</div>`).join('')||'no input devices';
}
async function loadHdmi(){const r=await jget('/api/hdmi');document.getElementById('hdmi').innerHTML=`HDMI channel: <b>${r.channel??'none'}</b> | mpv alive: ${r.mpv_alive} | mpv found: ${r.mpv}<br>${r.entry?`<b>${r.entry.title}</b> ${r.entry.subtitle||''}`:'no entry'}`;}
async function tune(){const r=await jpost('/api/tune',{channel:+document.getElementById('tunech').value});alert(JSON.stringify(r).slice(0,300));loadHdmi();}
async function restartPb(){const r=await jpost('/api/restart-playback',{});alert(JSON.stringify(r).slice(0,300));}
async function loadSys(){const s=await jget('/api/system');document.getElementById('sys').innerHTML=`<pre>${JSON.stringify({disk:s.disk,mpv:s.mpv,sessions:s.sessions},null,2)}</pre>`;
  document.getElementById('streams').innerHTML=(s.sessions||[]).map(x=>`<div>${x.id} ch${x.channel}</div>`).join('')||'no browser sessions';}
async function loadSets(){const r=await jget('/api/settings');document.getElementById('sets').innerHTML=Object.entries(r.settings).map(([k,v])=>`<div class="kv"><label>${k}</label><input id="set-${k}" value="${v}"></div>`).join('');}
async function saveSets(){const o={};document.querySelectorAll('#sets input').forEach(i=>o[i.id.slice(4)]=i.value);await jpost('/api/settings',o);alert('Saved');}
async function loadLogs(){const r=await jget('/api/logs');document.getElementById('logs').textContent=(r.lines||[]).join('');}
