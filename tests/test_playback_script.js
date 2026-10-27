const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {parseHTML}=require('linkedom');
const source=fs.readFileSync(path.join(__dirname,'../assets/playback_queue.js'),'utf8');
function fixture(youtube=true){
  const {window}=parseHTML('<html><body><div id="'+(youtube?'cf-yt-toolbar':'bilifocus-toolbar')+'"></div><div id="movie_player" data-bilifocus-player><video></video></div></body></html>');
  const p=window.document.querySelector('#movie_player'),v=p.querySelector('video');let code=youtube?'AAAAAAAAAAA':'BV1B7411m7LV',plays=0,pauses=0;
  Object.assign(v,{duration:10,currentTime:2,ended:false,readyState:4,play:()=>{plays++;return Promise.resolve();},pause:()=>{pauses++;}});
  p.getVideoData=()=>({video_id:code});
  const ctx={document:window.document,URL,top:null,self:null,location:new URL(youtube?'https://www.youtube.com/watch?v=AAAAAAAAAAA':'https://www.bilibili.com/video/BV1B7411m7LV/?p=1'),BiliFocus:{showNotice:()=>{}},__INITIAL_STATE__:{bvid:code,videoData:{pages:[{},{}]}}};
  ctx.window=ctx;ctx.top=ctx;ctx.self=ctx;vm.createContext(ctx);vm.runInContext(source,ctx);
  const config={token:'1',code,autoplay:true,start:true,remaining:1};
  return {ctx,p,v,doc:window.document,config,sample:()=>ctx.CustomFollowPlayback.observe(config),plays:()=>plays,pauses:()=>pauses,other:()=>{code='BBBBBBBBBBB';}};
}
const f=fixture();f.sample();f.sample();assert.equal(f.plays(),1,'polling cannot resume manually paused media');
f.v.dispatchEvent(new f.doc.defaultView.Event('loadstart'));f.sample();assert.equal(f.plays(),1,'quality reload cannot force playback again');
f.p.classList.add('ad-showing');f.v.ended=true;f.v.currentTime=10;assert.equal(f.sample().ended,false,'ads never complete a followed video');
f.p.classList.remove('ad-showing');assert.equal(f.sample().ended,false,'ad ending before the next main progress event is ignored');
f.v.ended=false;f.v.currentTime=3;f.sample();f.v.currentTime=10;f.v.ended=true;assert.equal(f.sample().ended,true,'real main media completion is reported');
f.doc.querySelector('#cf-autoplay').click();assert.equal(f.sample().action.name,'autoplay');assert.equal(f.sample().action.value,false);
f.other();assert.equal(f.sample().unexpected,true);assert.equal(f.pauses(),1,'unselected platform media is stopped');
const b=fixture(false);b.sample();b.v.currentTime=10;b.v.ended=true;assert.equal(b.sample().pages,2);assert.equal(b.sample().part,1);
b.v.duration=Infinity;assert.equal(b.sample().ended,false,'live streams cannot trigger a false end');
console.log('PASS: followed-only playback signals, ad boundary, finite end, parts, pause preservation and unexpected media guard');
