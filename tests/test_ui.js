const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {parseHTML}=require('linkedom');
const html=fs.readFileSync(path.join(__dirname,'../assets/index.html'),'utf8');
const {window}=parseHTML(html);
for(const select of window.document.querySelectorAll('select')){
  let value=select.querySelector('option')?.getAttribute('value')||'';
  Object.defineProperty(select,'value',{configurable:true,get:()=>value,set:v=>{value=String(v);}});
}
const calls=[];
const yt={key:'yt:AAAAAAAAAAA',title:'<img src=x onerror=alert(1)>',uid:'yt:UC'+'a'.repeat(22),platform:'youtube',author:'Test channel',published_at:10,read:true,cover:'https://i.ytimg.com/vi/AAAAAAAAAAA/hqdefault.jpg'};
const bili={key:'BV1B7411m7LV',title:'Bili fixture',uid:'42',platform:'bilibili',author:'Bili channel',published_at:9,read:true};
const ctx={document:window.document,Element:window.Element,Event:window.Event,URL,console,
  setTimeout:()=>1,clearTimeout:()=>{},setInterval:()=>1,addEventListener:()=>{}};
ctx.window=ctx;
vm.createContext(ctx);
vm.runInContext(html.split('<script>')[1].split('</script>')[0],ctx);
const snapshot={channels:[{uid:'42',name:'Bili channel',platform:'bilibili',enabled:true},{uid:yt.uid,name:'Test channel',platform:'youtube',enabled:true,channel_id:'UC'+'a'.repeat(22)}],
  videos:[yt,bili],settings:{interval_minutes:5,paused:false,popups:true,sound:false,quiet_enabled:false,quiet_start:'23:00',quiet_end:'08:00'},runtime:{}};
ctx.receiveState(snapshot);
const exactYT={...yt,published_at:1792167320,published_precision:'second',timestamp_approximate:false};
assert.match(ctx.publicationText(exactYT),/2026/,'YouTube cards show the publication year');
assert.match(ctx.publicationTitle(exactYT),/本地时间/);
assert.match(ctx.publicationText({...yt,published_at:1792167320,published_label:'10 days ago',published_precision:'relative',timestamp_approximate:true}),/^约 /);
assert.equal(ctx.publicationText({...yt,published_at:1792167320,published_label:'发布时间未提供',timestamp_approximate:true}),'发布时间未知','legacy check times cannot appear as publication times');
assert.equal(ctx.publicationText({...yt,published_at:1792108800,published_label:'2026-10-16',published_precision:'day',timestamp_approximate:true}),'2026-10-16');
assert.equal(window.document.querySelectorAll('.video-card').length,2);
assert.equal(window.document.querySelectorAll('.video-title img').length,0,'remote titles remain plain text');
assert.equal(window.document.querySelectorAll('.cover img').length,1,'YouTube covers are allowed');
window.document.getElementById('platformFilter').value='youtube';ctx.updateChannelFilter();ctx.renderVideos();
assert.equal(window.document.querySelectorAll('.video-card').length,1);
ctx.pywebview={api:{
  play_video:async key=>({ok:true,video:key===yt.key?yt:bili,video_id:key===yt.key?'AAAAAAAAAAA':null,bvid:key===bili.key?bili.key:null,platform:key===yt.key?'youtube':'bilibili'}),
  open_focus_video:async(...args)=>{calls.push(['focus',...args]);return {ok:true};},
  close_focus_video:async()=>{calls.push(['close']);return {ok:true};},
  add_channel:async(...args)=>{calls.push(['add',...args]);return {ok:true};}
}};
async function run(){
  await ctx.openVideo(yt.key);
  assert(calls.some(c=>c[0]==='focus'&&c[1]===yt.key));
  assert.deepEqual(Array.from(calls.find(c=>c[0]==='focus')[3]),[yt.key],'only the currently filtered list is sent to the host queue');
  assert.equal(window.document.getElementById('episodeControls').hidden,true);
  await ctx.selectPlaybackMode('embed');
  assert.equal(window.document.getElementById('autoplayFollowed').disabled,true,'backup embedded mode does not promise native queue automation');
  const frame=window.document.querySelector('#playerShell iframe');
  assert.equal(frame.getAttribute('src'),'https://www.youtube.com/embed/AAAAAAAAAAA?autoplay=0&rel=0');
  assert(calls.some(c=>c[0]==='close'),'changing player modes closes the other window');
  const previousCalls=calls.length;
  ctx.stopPlayerFromHost();
  assert.equal(window.document.querySelector('#playerShell iframe'),null,'host close unloads embedded playback');
  assert.equal(calls.length,previousCalls,'host close cannot call back into Python during native closing');
  assert.equal(vm.runInContext('playing',ctx),null);
  window.document.getElementById('creatorPlatform').value='youtube';ctx.updateCreatorPlatform();
  window.document.getElementById('uidInput').value='@example';await ctx.addChannel();
  assert(calls.some(c=>c[0]==='add'&&c[1]==='@example'&&c[2]==='youtube'));
  console.log('PASS: dual-platform UI, safe titles, covers, filters, YouTube add and player lifecycle');
}
run().catch(e=>{console.error(e);process.exitCode=1;});
