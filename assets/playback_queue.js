/* Host-controlled playback state only. No Python or message bridge is exposed. */
(() => {
  'use strict';
  if(top!==self||window.CustomFollowPlayback||!window.BiliFocus)return;
  const youtube=['www.youtube.com','youtube.com','m.youtube.com'].includes(location.hostname);
  const initial=youtube?(new URL(location.href).searchParams.get('v')||location.pathname.match(/^\/(?:shorts|live)\/([\w-]{11})/)?.[1]):
    (location.pathname.match(/\/video\/(BV[0-9A-Za-z]{10})/)?.[1]||new URL(location.href).searchParams.get('bvid'));
  if(!initial)return;
  let token='',video=null,seenMain=false,attempted=false,seq=0,action=null,enabled=false;
  function isAd(){return youtube&&Boolean(document.querySelector('#movie_player.ad-showing,#movie_player.ad-interrupting'));}
  function actualId(){try{return youtube?document.querySelector('#movie_player')?.getVideoData?.()?.video_id:(window.player?.getManifest?.()?.bvid||window.__INITIAL_STATE__?.bvid||initial);}catch(_){return initial;}}
  function selected(){const id=actualId();return !id||id===initial;}
  function progress(){if(video&&!isAd()&&selected()&&Number.isFinite(video.duration)&&video.currentTime>0&&video.currentTime<video.duration-0.1)seenMain=true;}
  function reset(){seenMain=false;}
  function bind(){const next=document.querySelector(youtube?'#movie_player video':'[data-bilifocus-player] video');if(next===video)return;if(video){video.removeEventListener('timeupdate',progress);video.removeEventListener('loadstart',reset);}video=next;reset();if(video){video.addEventListener('timeupdate',progress);video.addEventListener('loadstart',reset);}}
  function request(name,value){action={seq:++seq,name,value};}
  function controls(config){
    const bar=document.getElementById(youtube?'cf-yt-toolbar':'bilifocus-toolbar');if(!bar)return;
    let button=document.getElementById('cf-autoplay');
    if(!button){button=document.createElement('button');button.type='button';button.id='cf-autoplay';button.addEventListener('click',()=>request('autoplay',!enabled));bar.append(button);const next=document.createElement('button');next.type='button';next.id='cf-next-video';next.textContent='下一条';next.addEventListener('click',()=>request('next'));bar.append(next);}
    enabled=!!config.autoplay;const text=enabled?'自动连播：开':'自动连播：关';if(button.textContent!==text)button.textContent=text;
    button.setAttribute('aria-pressed',String(enabled));button.title='只播放程序中已添加博主的作品；当前清单还剩 '+config.remaining+' 条，播完停止。';
    document.getElementById('cf-next-video').disabled=!config.remaining;
  }
  function stopPlatformNext(){
    if(youtube){const toggle=document.querySelector('#movie_player .ytp-autonav-toggle-button[aria-checked="true"]');if(toggle)toggle.click();}
    else try{const abort=window.nano?.HandoffKind?.Abort,p=window.player;if(abort!=null&&p?.setHandoff&&p.getHandoff?.()!==abort)p.setHandoff(abort);}catch(_){}
  }
  function observe(config){
    if(!config||config.code!==initial)return null;
    if(token!==config.token){token=config.token;seq=0;action=null;attempted=false;reset();}
    bind();controls(config);stopPlatformNext();
    const ad=isAd(),valid=selected();
    if(ad)seenMain=false;
    if(!ad&&!valid){video?.pause();return {token,code:initial,unexpected:true};}
    progress();
    if(config.start&&video&&!ad&&valid&&!attempted&&(video.readyState>=1||video.currentSrc||video.getAttribute('src')||video.srcObject)&&!video.ended){attempted=true;try{Promise.resolve(video.play()).catch(()=>window.BiliFocus?.showNotice('请点击播放器开始播放，之后会只连播你的关注清单。'));}catch(_) {}}
    const ended=!!(video&&!ad&&valid&&seenMain&&video.ended&&Number.isFinite(video.duration)&&video.duration>0&&video.currentTime>=video.duration-0.5);
    const part=youtube?1:Number(new URL(location.href).searchParams.get('p')||1);
    const pages=youtube?1:Math.max(1,Math.min(999,window.__INITIAL_STATE__?.videoData?.pages?.length||1));
    return {token,code:initial,ended,part,pages,action};
  }
  window.CustomFollowPlayback=Object.freeze({observe});
})();
