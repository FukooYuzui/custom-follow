const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {parseHTML}=require('linkedom');
const source=fs.readFileSync(path.join(__dirname,'../assets/playback_queue.js'),'utf8');
function fixture(youtube=true){
  const {window}=parseHTML('<html><body><div id="'+(youtube?'cf-yt-toolbar':'bilifocus-toolbar')+'"></div><div id="movie_player" data-bilifocus-player><video></video></div></body></html>');
  const p=window.document.querySelector('#movie_player'),v=p.querySelector('video');let code=youtube?'AAAAAAAAAAA':'BV1B7411m7LV',plays=0,pauses=0,timer=0;
  const timers=new Map();
  Object.assign(v,{duration:10,currentTime:2,ended:false,paused:false,readyState:4,play:()=>{plays++;v.paused=false;return Promise.resolve();},pause:()=>{pauses++;v.paused=true;}});
  p.getVideoData=()=>({video_id:code});
  const ctx={document:window.document,URL,top:null,self:null,location:new URL(youtube?'https://www.youtube.com/watch?v=AAAAAAAAAAA':'https://www.bilibili.com/video/BV1B7411m7LV/?p=1'),BiliFocus:{showNotice:()=>{}},__INITIAL_STATE__:{bvid:code,videoData:{pages:[{},{}]}},
    setTimeout:(callback,delay)=>{const key=++timer;timers.set(key,{callback,delay});return key;},clearTimeout:key=>timers.delete(key)};
  ctx.window=ctx;ctx.top=ctx;ctx.self=ctx;vm.createContext(ctx);vm.runInContext(source,ctx);
  const config={token:'1',key:youtube?'yt:'+code:code,code,autoplay:true,start:true,remaining:1};
  function key(key,target=window.document,props={}){const event=new window.Event('keydown',{bubbles:true,cancelable:true});Object.assign(event,{key,...props});target.dispatchEvent(event);return event;}
  return {ctx,p,v,doc:window.document,config,key,sample:()=>ctx.CustomFollowPlayback.observe(config),plays:()=>plays,pauses:()=>pauses,other:()=>{code='BBBBBBBBBBB';},
    flush:()=>{const pending=[...timers.values()];timers.clear();for(const task of pending)task.callback();},timers};
}
function withQueue(f){f.config.queue={version:1,remaining:1,items:[{key:f.config.key,title:'当前视频',author:'当前博主',platform:f.config.key.startsWith('yt:')?'youtube':'bilibili',watched:true,current:true},{key:'BV1xx411c7Ug',title:'<img src=x onerror=alert(1)> 下一条',author:'已关注的博主',platform:'bilibili',watched:false,current:false}]};f.sample();return f;}

async function run(){
  const f=fixture();f.sample();f.v.paused=true;f.sample();assert.equal(f.plays(),1,'polling cannot resume manually paused media');
  f.v.dispatchEvent(new f.doc.defaultView.Event('loadstart'));f.sample();assert.equal(f.plays(),1,'quality reload cannot force playback again');
  f.p.classList.add('ad-showing');f.v.ended=true;f.v.currentTime=10;assert.equal(f.sample().ended,false,'ads never complete a followed video');
  f.p.classList.remove('ad-showing');assert.equal(f.sample().ended,false,'ad ending before the next main progress event is ignored');
  f.v.ended=false;f.v.currentTime=3;f.sample();f.v.currentTime=10;f.v.ended=true;assert.equal(f.sample().ended,true,'real main media completion is reported');
  f.doc.querySelector('#cf-autoplay').click();assert.equal(f.sample().action.name,'autoplay');assert.equal(f.sample().action.value,false);
  f.other();assert.equal(f.sample().unexpected,true);assert.equal(f.pauses(),1,'unselected platform media is stopped');
  const b=fixture(false);b.sample();b.v.currentTime=10;b.v.ended=true;assert.equal(b.sample().pages,2);assert.equal(b.sample().part,1);
  b.v.duration=Infinity;assert.equal(b.sample().ended,false,'live streams cannot trigger a false end');

  const q=withQueue(fixture()),doc=q.doc;
  assert.equal(doc.querySelector('#cf-queue-button').textContent,'待播列表（1）');
  assert.equal(doc.querySelectorAll('.cf-queue-row').length,2);
  assert.equal(doc.querySelectorAll('#cf-queue-list img').length,0,'queue metadata is rendered as plain text');
  assert.match(doc.querySelectorAll('.cf-queue-row')[1].textContent,/<img src=x/);
  assert.equal(doc.querySelector('.cf-queue-row[data-current="true"]').disabled,true);
  for(const id of ['cf-playback-ui','cf-playback-ui-style']){assert(doc.getElementById(id).hasAttribute('data-bilifocus-owned'));assert(doc.getElementById(id).hasAttribute('data-cf-owned'));}
  doc.querySelector('#cf-queue-button').click();assert.equal(doc.querySelector('#cf-playback-ui').getAttribute('data-open'),'true');
  doc.querySelectorAll('.cf-queue-row')[1].click();const choice=q.sample().action;
  assert.equal(choice.name,'select');assert.equal(choice.value,'BV1xx411c7Ug');assert.equal(doc.querySelector('#cf-playback-ui').getAttribute('data-open'),'false');
  assert.equal(doc.querySelector('#cf-playback-transition').getAttribute('data-active'),'true');assert.equal(doc.querySelector('#cf-transition-stop').hidden,true,'outgoing transition is not a second cancellation race');
  const rows=doc.querySelectorAll('.cf-queue-row');q.sample();assert.equal(doc.querySelectorAll('.cf-queue-row')[1],rows[1],'unchanged versions preserve list scroll and focus');
  q.config.queue={version:2,items:[q.config.queue.items[0]],remaining:0};q.config.remaining=0;q.sample();
  assert.equal(doc.querySelectorAll('.cf-queue-row').length,1);assert.match(doc.querySelector('#cf-queue-empty').textContent,/没有待播/);
  q.config.queue={version:3,items:[]};q.sample();assert.match(doc.querySelector('#cf-queue-empty').textContent,/当前没有待播/);
  assert.equal(q.ctx.CustomFollowPlayback.observe({...q.config,code:'BBBBBBBBBBB'}),null,'foreign page configs cannot create actions');

  const keys=withQueue(fixture(false));keys.key('q');assert.equal(keys.doc.querySelector('#cf-playback-ui').getAttribute('data-open'),'true');
  keys.key('Escape');assert.equal(keys.doc.querySelector('#cf-playback-ui').getAttribute('data-open'),'false');
  const input=keys.doc.createElement('input');keys.doc.body.append(input);keys.key('q',input);assert.equal(keys.doc.querySelector('#cf-playback-ui').getAttribute('data-open'),'false','typing in login inputs cannot open the queue');
  keys.key('q',keys.doc,{ctrlKey:true});assert.equal(keys.doc.querySelector('#cf-playback-ui').getAttribute('data-open'),'false');
  Object.defineProperty(keys.doc,'fullscreenElement',{value:keys.p,writable:true,configurable:true});
  keys.doc.dispatchEvent(new keys.doc.defaultView.Event('fullscreenchange'));
  assert.equal(keys.doc.getElementById('cf-playback-ui').parentNode,keys.p,'UI follows the DOM fullscreen container');
  assert.equal(keys.doc.getElementById('cf-playback-ui').getAttribute('data-reveal'),'true');keys.flush();assert.equal(keys.doc.getElementById('cf-playback-ui').getAttribute('data-reveal'),'false','fullscreen queue entry does not remain over the video');
  keys.doc.fullscreenElement=null;keys.doc.documentElement.setAttribute('data-bilifocus-fullscreen','');keys.sample();assert.equal(keys.doc.getElementById('cf-playback-ui').getAttribute('data-fullscreen'),'true','native window fullscreen also has a queue entry');
  assert.match(keys.doc.getElementById('cf-playback-ui-style').textContent,/prefers-reduced-motion/);

  const early=fixture();early.doc.body.remove();Object.defineProperty(early.doc,'body',{value:null,writable:true,configurable:true});
  assert.equal(early.ctx.CustomFollowPlayback.prepareTransition({title:'加载之前',author:'关注博主',platform:'youtube'}),true,'transition can be injected before body exists');
  assert.equal(early.doc.getElementById('cf-transition-title').textContent,'加载之前');
  early.doc.getElementById('cf-transition-stop').click();assert.equal(early.sample().action.name,'cancel','stop clicked before first native poll is retained across token initialization');
  const body=early.doc.createElement('body');early.doc.documentElement.append(body);early.doc.body=body;early.doc.dispatchEvent(new early.doc.defaultView.Event('DOMContentLoaded'));assert(early.doc.getElementById('cf-playback-ui').parentNode===body,'deferred UI mounts in the later body');
  const stopped=fixture();stopped.ctx.CustomFollowPlayback.prepareTransition({title:'准备停止'});stopped.doc.getElementById('cf-transition-stop').click();stopped.sample();assert.equal(stopped.plays(),0,'stop before the first poll never briefly starts playback');

  const incoming=withQueue(fixture());incoming.ctx.CustomFollowPlayback.prepareTransition({title:'新视频',author:'下一位博主',platform:'bilibili',part:2});
  assert.match(incoming.doc.querySelector('#cf-transition-author').textContent,/第 2 P/);
  incoming.config.transition={title:'新视频',author:'下一位博主',platform:'bilibili',part:2};
  incoming.v.paused=false;incoming.sample();incoming.flush();assert.equal(incoming.doc.querySelector('#cf-playback-transition').getAttribute('data-active'),'false','incoming card fades once selected media actually advances');
  incoming.v.paused=true;incoming.sample();assert.equal(incoming.doc.querySelector('#cf-playback-transition').getAttribute('data-active'),'false','a normal poll does not recreate dismissed transitions');
  incoming.ctx.CustomFollowPlayback.prepareTransition(incoming.config.transition);assert.equal(incoming.doc.querySelector('#cf-playback-transition').getAttribute('data-active'),'false','duplicate NavigationCompleted injection does not reopen a finished card');
  incoming.ctx.CustomFollowPlayback.prepareTransition({title:'网络较慢'});incoming.doc.querySelector('#cf-transition-view').click();assert.equal(incoming.doc.querySelector('#cf-playback-transition').getAttribute('data-active'),'false','viewer can reveal login and network prompts without waiting');
  const denied=fixture();denied.v.play=()=>Promise.reject(new Error('gesture required'));denied.config.transition={title:'点击开始'};denied.sample();await new Promise(resolve=>setImmediate(resolve));assert.equal(denied.doc.querySelector('#cf-playback-transition').getAttribute('data-active'),'false','failed autoplay leaves the official player accessible');
  const ad=fixture();ad.p.classList.add('ad-showing');ad.config.transition={title:'下一条'};ad.sample();ad.flush();
  assert.equal(ad.pauses(),0,'incoming cards do not pause official ads');
  assert.equal(ad.doc.querySelector('#cf-playback-transition').getAttribute('data-active'),'false','playing official ads retain visible controls');
  assert.equal(ad.sample().ended,false,'showing ad controls still does not advance the queue');
  console.log('PASS: followed playback samples, safe queue selection, versioned updates, early cancel, fullscreen access and transition lifecycle');
}
run().catch(error=>{console.error(error);process.exitCode=1;});
