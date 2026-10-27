const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {parseHTML}=require('linkedom');
const script=fs.readFileSync(path.join(__dirname,'../assets/youtube_focus.js'),'utf8');
function page(host='www.youtube.com'){
  const {window}=parseHTML('<html><body><ytd-app><header>Recommendations navigation</header><main><div id="player"><div id="movie_player" class="html5-video-player"><video></video><div class="ytp-chrome-bottom">Quality</div></div></div><aside>Suggested videos</aside></main><ytd-popup-container></ytd-popup-container></ytd-app></body></html>');
  const history={calls:[],pushState(...args){this.calls.push(args);},replaceState(...args){this.calls.push(args);}};
  const ctx={document:window.document,Element:window.Element,MutationObserver:window.MutationObserver,Event:window.Event,URL,history,
    location:{protocol:'https:',hostname:host,href:'https://'+host+'/watch?v=AAAAAAAAAAA',pathname:'/watch',search:'?v=AAAAAAAAAAA'},
    Date:{now:()=>100000},setTimeout:()=>1,clearTimeout:()=>{},dispatchEvent:()=>{}};
  ctx.window=ctx;ctx.self=ctx;ctx.top=ctx;vm.createContext(ctx);vm.runInContext(script,ctx);return {ctx,doc:window.document,history};
}
const {ctx,doc,history}=page();
assert.equal(ctx.BiliFocus.status().mode,'focus');
const fullscreenTarget=doc.querySelector('#movie_player');
let fullscreenRequests=0;
fullscreenTarget.requestFullscreen=()=>{fullscreenRequests++;doc.fullscreenElement=fullscreenTarget;doc.dispatchEvent(new ctx.Event('fullscreenchange'));return Promise.resolve();};
doc.exitFullscreen=()=>{doc.fullscreenElement=null;doc.dispatchEvent(new ctx.Event('fullscreenchange'));return Promise.resolve();};
doc.querySelector('.cf-yt-fullscreen').click();
assert.equal(fullscreenRequests,1);
assert(doc.documentElement.hasAttribute('data-cf-yt-fullscreen'));
ctx.BiliFocus.setHostFullscreen(true);doc.exitFullscreen();
assert(doc.documentElement.hasAttribute('data-cf-yt-fullscreen'));
ctx.BiliFocus.setHostFullscreen(false);
assert(!doc.documentElement.hasAttribute('data-cf-yt-fullscreen'));
assert.equal(doc.querySelector('#movie_player').parentElement.id,'player','official player stays in its original DOM');
assert(doc.querySelector('aside').hasAttribute('data-cf-hidden'));
assert(doc.querySelector('header').hasAttribute('data-cf-hidden'));
assert(!doc.querySelector('ytd-popup-container').hasAttribute('data-cf-hidden'),'official dialogs remain available');
assert(doc.querySelector('video'),'official video and controls are preserved');
ctx.BiliFocus.full();assert.equal(doc.documentElement.getAttribute('data-cf-yt'),'full');
ctx.BiliFocus.focus();assert.equal(ctx.BiliFocus.status().mode,'focus');
history.pushState({},'', '/watch?v=BBBBBBBBBBB');assert.equal(history.calls.length,0,'other-video SPA navigation is blocked');
history.pushState({},'', '/watch?v=AAAAAAAAAAA&t=30');assert.equal(history.calls.length,1);
ctx.BiliFocus.showNotice('<img src=x onerror=alert(1)>');
assert.equal(doc.querySelector('#cf-yt-notice img'),null);
assert.equal(page('evil.test').ctx.BiliFocus,undefined);
console.log('PASS: YouTube focus isolation, controls, login dialogs, mode switching and selected-video routing');
