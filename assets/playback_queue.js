/* Host-controlled playback UI and samples. No Python or message bridge is exposed. */
(() => {
  'use strict';
  if(top!==self||window.CustomFollowPlayback||!window.BiliFocus)return;
  const youtube=['www.youtube.com','youtube.com','m.youtube.com'].includes(location.hostname);
  const initial=youtube?(new URL(location.href).searchParams.get('v')||location.pathname.match(/^\/(?:shorts|live)\/([\w-]{11})/)?.[1]):
    (location.pathname.match(/\/video\/(BV[0-9A-Za-z]{10})/)?.[1]||new URL(location.href).searchParams.get('bvid'));
  if(!initial)return;
  const KEY=/^(?:BV[0-9A-Za-z]{10}|yt:[A-Za-z0-9_-]{11})$/;
  let token='',video=null,seenMain=false,attempted=false,seq=0,action=null,enabled=false;
  let queueVersion=null,items=[],drawerOpen=false,lastFocus=null,ui=null,style=null;
  let revealTimer=0,transitionTimer=0,transitionEpoch=0,transitionActive=false,transitionIncoming=false;
  let transitionStarted=0,transitionSeen='',transitionIdentity='',endedTransition=false;

  function owned(node){node.setAttribute('data-bilifocus-owned','');node.setAttribute('data-cf-owned','');return node;}
  function element(tag,id,text){const n=document.createElement(tag);if(id)n.id=id;if(text!==undefined)n.textContent=String(text);return n;}
  function button(id,text,click){const n=element('button',id,text);n.type='button';n.addEventListener('click',click);return n;}
  function plain(value,limit=300){return String(value??'').replace(/[\r\n]/g,' ').slice(0,limit);}
  function own(id){return ui?.querySelector('#'+id);}
  function isFullscreen(){return Boolean(document.fullscreenElement||document.documentElement?.hasAttribute('data-bilifocus-fullscreen')||document.documentElement?.hasAttribute('data-cf-yt-fullscreen'));}
  function mount(){
    if(!ui||!document.documentElement)return;
    const full=document.fullscreenElement;
    const target=full&&full.tagName!=='VIDEO'?full:(document.body||document.documentElement);
    if(ui.parentNode!==target)target.append(ui);
    if(style&&!style.isConnected)document.documentElement.append(style);
    ui.setAttribute('data-fullscreen',String(isFullscreen()));
  }
  function ensureUi(){
    if(ui){mount();return;}
    style=owned(element('style','cf-playback-ui-style'));
    style.textContent=`
      #cf-playback-ui{position:fixed!important;inset:0!important;z-index:2147483200!important;pointer-events:none!important;font:13px/1.5 "Microsoft YaHei UI","Microsoft YaHei",sans-serif!important;color:#edf1fb!important;text-align:left!important;box-sizing:border-box!important;visibility:visible!important}
      #cf-playback-ui *,#cf-playback-ui *::before,#cf-playback-ui *::after{box-sizing:border-box!important}
      #cf-playback-ui button{font:inherit!important;cursor:pointer!important;color:inherit!important;border:1px solid #46536d!important;border-radius:8px!important;background:#293348!important;padding:8px 12px!important;margin:0!important;text-transform:none!important;letter-spacing:normal!important}
      #cf-playback-ui button:hover{background:#3b465e!important}#cf-playback-ui button:focus-visible{outline:2px solid #f49abb!important;outline-offset:3px!important}#cf-playback-ui button:disabled{cursor:default!important}
      #cf-queue-mask{position:absolute!important;inset:0!important;background:#0008!important;opacity:0!important;visibility:hidden!important;transition:opacity 220ms ease,visibility 220ms!important;pointer-events:none!important}
      #cf-queue-panel{position:absolute!important;inset:0 0 0 auto!important;width:min(420px,100vw)!important;max-width:100%!important;height:100%!important;display:flex!important;flex-direction:column!important;padding:22px!important;background:#171e2bfa!important;border-left:1px solid #39445c!important;box-shadow:-12px 0 40px #0006!important;transform:translateX(104%)!important;visibility:hidden!important;transition:transform 240ms ease,visibility 240ms!important;pointer-events:none!important;overflow:hidden!important}
      #cf-playback-ui[data-open="true"] #cf-queue-mask{opacity:1!important;visibility:visible!important;pointer-events:auto!important}#cf-playback-ui[data-open="true"] #cf-queue-panel{transform:translateX(0)!important;visibility:visible!important;pointer-events:auto!important}
      #cf-queue-heading{display:flex!important;align-items:center!important;justify-content:space-between!important;gap:12px!important;flex-shrink:0!important}#cf-queue-heading h2{font:600 20px/1.5 "Microsoft YaHei UI",sans-serif!important;color:#f4f6fc!important;margin:0!important}#cf-queue-close{font-size:18px!important;line-height:1!important;padding:9px 12px!important}
      #cf-queue-copy{font-size:12px!important;color:#a2b0c8!important;margin:10px 0 16px!important}#cf-queue-list{flex:1 1 auto!important;min-height:0!important;overflow-y:auto!important;overflow-x:hidden!important;overscroll-behavior:contain!important;scrollbar-width:thin!important;scrollbar-color:#4d5c77 transparent!important;padding:2px 4px 8px 2px!important;display:flex!important;flex-direction:column!important;gap:9px!important}
      #cf-queue-list .cf-queue-row{width:100%!important;display:grid!important;grid-template-columns:26px minmax(0,1fr)!important;gap:10px!important;align-items:start!important;padding:13px 11px!important;text-align:left!important;flex-shrink:0!important;background:#202a3b!important;border-color:#354159!important}
      #cf-queue-list .cf-queue-row[data-current="true"]{background:#392a3e!important;border-color:#98667e!important;opacity:1!important}#cf-queue-list .cf-queue-number{font-size:11px!important;color:#8fa1bd!important;padding-top:2px!important}#cf-queue-list .cf-queue-title{display:block!important;color:#edf1fb!important;font-weight:600!important;line-height:1.65!important;overflow-wrap:anywhere!important;font-size:13px!important}#cf-queue-list .cf-queue-meta{display:block!important;margin-top:7px!important;font-size:11px!important;color:#9eafc9!important;overflow-wrap:anywhere!important}#cf-queue-list .cf-queue-state{display:inline-block!important;margin-top:8px!important;font-size:10px!important;color:#fac7dc!important}
      #cf-queue-empty{padding:30px 12px!important;color:#aebcd2!important;line-height:1.9!important;text-align:center!important}#cf-queue-foot{margin-top:13px!important;font-size:11px!important;color:#8195b3!important;flex-shrink:0!important}
      #cf-queue-fab{position:absolute!important;top:18px!important;right:22px!important;display:none!important;opacity:0!important;visibility:hidden!important;pointer-events:none!important;transition:opacity 180ms ease,visibility 180ms!important;box-shadow:0 4px 24px #0005!important;background:#202a3be8!important}
      #cf-playback-ui[data-fullscreen="true"] #cf-queue-fab{display:inline-flex!important}#cf-playback-ui[data-fullscreen="true"][data-reveal="true"]:not([data-open="true"]) #cf-queue-fab{opacity:1!important;visibility:visible!important;pointer-events:auto!important}
      #cf-playback-transition{position:absolute!important;inset:0!important;z-index:3!important;background:radial-gradient(ellipse at 50% 35%,#392537 0%,#151925 46%,#0c1018 85%)!important;display:grid!important;place-items:center!important;padding:28px!important;opacity:0!important;visibility:hidden!important;pointer-events:none!important;transition:opacity 250ms ease,visibility 250ms!important}
      #cf-playback-transition[data-active="true"]{opacity:1!important;visibility:visible!important;pointer-events:auto!important}#cf-transition-card{width:min(620px,100%)!important;min-width:0!important;text-align:center!important;transform:translateY(10px)!important;transition:transform 250ms ease!important}#cf-playback-transition[data-active="true"] #cf-transition-card{transform:translateY(0)!important}
      #cf-transition-spinner{display:block!important;width:36px!important;height:36px!important;border:2px solid #624557!important;border-top-color:#f49abb!important;border-radius:50%!important;margin:0 auto 24px!important;animation:cf-playback-spin 1100ms linear infinite!important}#cf-transition-kicker{color:#f3a3c2!important;font-size:12px!important;letter-spacing:2px!important;margin:0 0 14px!important}#cf-transition-title{font:600 clamp(19px,3vw,28px)/1.6 "Microsoft YaHei UI",sans-serif!important;color:#f3f5fb!important;overflow-wrap:anywhere!important;margin:0!important}#cf-transition-author{color:#afbdd2!important;font-size:13px!important;margin:16px 0 0!important;overflow-wrap:anywhere!important}#cf-transition-copy{color:#8093b0!important;font-size:12px!important;margin:28px 0 0!important}#cf-transition-actions{display:flex!important;gap:10px!important;justify-content:center!important;flex-wrap:wrap!important;margin-top:18px!important}#cf-transition-stop{color:#f6bfd5!important;border-color:#745063!important}#cf-transition-actions button[hidden]{display:none!important}
      @keyframes cf-playback-spin{to{transform:rotate(360deg)}}
      @media(max-width:1100px){#bilifocus-toolbar,#cf-yt-toolbar{gap:9px!important;padding-left:12px!important;padding-right:12px!important}#bilifocus-toolbar button,#cf-yt-toolbar button{padding-left:9px!important;padding-right:9px!important}#cf-yt-title{min-width:0!important}}
      @media(max-width:480px){#cf-queue-panel{padding:17px!important}#cf-transition-card{width:100%!important}#cf-playback-transition{padding:22px!important}}
      @media(prefers-reduced-motion:reduce){#cf-queue-mask,#cf-queue-panel,#cf-queue-fab,#cf-playback-transition,#cf-transition-card{transition:none!important}#cf-transition-spinner{animation:none!important}}
    `;
    ui=owned(element('div','cf-playback-ui'));ui.setAttribute('data-open','false');ui.setAttribute('data-reveal','false');
    ui.addEventListener('click',event=>event.stopPropagation());ui.addEventListener('pointerdown',event=>event.stopPropagation());
    const mask=element('div','cf-queue-mask');mask.setAttribute('aria-hidden','true');mask.addEventListener('click',()=>setDrawer(false));
    const panel=element('section','cf-queue-panel');panel.setAttribute('role','dialog');panel.setAttribute('aria-modal','true');panel.setAttribute('aria-labelledby','cf-queue-title');panel.setAttribute('aria-hidden','true');panel.inert=true;
    const heading=element('div','cf-queue-heading');heading.append(element('h2','cf-queue-title','待播列表'),button('cf-queue-close','×',()=>setDrawer(false)));heading.lastElementChild.setAttribute('aria-label','关闭待播列表');
    panel.append(heading,element('p','cf-queue-copy','只播放你加入程序的博主作品。'),element('div','cf-queue-list'),element('p','cf-queue-foot','Q 打开 / 关闭列表 · 播完清单即停止'));
    panel.addEventListener('keydown',trapFocus);
    const fab=button('cf-queue-fab','待播列表',()=>setDrawer(!drawerOpen));fab.title='待播列表（Q）';fab.setAttribute('aria-controls','cf-queue-panel');fab.setAttribute('aria-expanded','false');
    const transition=element('div','cf-playback-transition');transition.setAttribute('data-active','false');transition.setAttribute('aria-hidden','true');
    const card=element('div','cf-transition-card');const spinner=element('span','cf-transition-spinner');spinner.setAttribute('aria-hidden','true');
    const title=element('h2','cf-transition-title');title.setAttribute('aria-live','polite');
    const actions=element('div','cf-transition-actions');actions.append(button('cf-transition-view','显示播放器',()=>hideTransition()),button('cf-transition-stop','停止播放',()=>{request('cancel');hideTransition();}));
    card.append(spinner,element('p','cf-transition-kicker','即将播放'),title,element('p','cf-transition-author'),element('p','cf-transition-copy','正在打开官方播放器…'),actions);transition.append(card);
    ui.append(mask,panel,fab,transition);renderQueue();mount();
  }
  function trapFocus(event){
    if(event.key!=='Tab'||!drawerOpen)return;
    const focusable=[...document.getElementById('cf-queue-panel').querySelectorAll('button:not([disabled])')];
    if(!focusable.length)return;
    const index=focusable.indexOf(document.activeElement);
    if(event.shiftKey&&index<=0){event.preventDefault();focusable.at(-1).focus?.();}
    else if(!event.shiftKey&&(index<0||index===focusable.length-1)){event.preventDefault();focusable[0].focus?.();}
  }
  function setDrawer(open){
    ensureUi();drawerOpen=Boolean(open);ui.setAttribute('data-open',String(drawerOpen));
    const panel=own('cf-queue-panel');panel.inert=!drawerOpen;panel.setAttribute('aria-hidden',String(!drawerOpen));
    for(const id of ['cf-queue-button','cf-queue-fab'])document.getElementById(id)?.setAttribute('aria-expanded',String(drawerOpen));
    if(drawerOpen){lastFocus=document.activeElement;document.getElementById('cf-queue-close')?.focus?.();}
    else if(lastFocus?.isConnected)lastFocus.focus?.();
  }
  function reveal(){
    if(!isFullscreen())return;ensureUi();ui.setAttribute('data-reveal','true');clearTimeout(revealTimer);
    revealTimer=setTimeout(()=>ui?.setAttribute('data-reveal','false'),2400);
  }
  function renderQueue(){
    if(!ui)return;
    const list=own('cf-queue-list');if(!list)return;list.replaceChildren();
    if(!items.length){list.append(element('p','cf-queue-empty','当前没有待播视频。请回到关注清单选择一部作品。'));return;}
    items.forEach((item,index)=>{
      const row=button('', '',()=>{if(item.current)return;setDrawer(false);prepareTransition({...item,cancellable:false});request('select',item.key);});
      row.className='cf-queue-row';row.setAttribute('data-key',item.key);row.setAttribute('data-current',String(item.current));
      row.disabled=item.current;if(item.current)row.setAttribute('aria-current','true');
      const number=element('span','',item.current?'▶':String(index+1));number.className='cf-queue-number';
      const body=element('span'),title=element('span','',item.title||'未命名视频');title.className='cf-queue-title';
      const meta=element('span','',(item.platform==='youtube'?'YouTube':'B站')+' · '+(item.author||'博主'));meta.className='cf-queue-meta';
      const state=element('span','',item.current?'正在播放':item.watched?'已看':'未看');state.className='cf-queue-state';body.append(title,meta,state);row.append(number,body);
      row.setAttribute('aria-label',(item.current?'正在播放：':'播放：')+item.title);list.append(row);
    });
    if(items.every(item=>item.current))list.append(element('p','cf-queue-empty','后面没有待播视频，当前作品播完后停止。'));
  }
  function updateQueue(config){
    const queue=config.queue||{},source=Array.isArray(queue.items)?queue.items.slice(0,500):[];
    const version=String(queue.version??JSON.stringify(source));if(version===queueVersion)return;queueVersion=version;
    const seen=new Set();items=source.filter(item=>item&&typeof item.key==='string'&&KEY.test(item.key)&&!seen.has(item.key)&&seen.add(item.key)).map(item=>({key:item.key,title:plain(item.title),author:plain(item.author,120),platform:item.platform==='youtube'?'youtube':'bilibili',watched:item.watched===true,current:item.key===config.key}));
    renderQueue();
  }
  function isAd(){return youtube&&Boolean(document.querySelector('#movie_player.ad-showing,#movie_player.ad-interrupting'));}
  function actualId(){try{return youtube?document.querySelector('#movie_player')?.getVideoData?.()?.video_id:(window.player?.getManifest?.()?.bvid||window.__INITIAL_STATE__?.bvid||initial);}catch(_){return initial;}}
  function selected(){const id=actualId();return !id||id===initial;}
  function progress(){if(video&&!isAd()&&selected()&&Number.isFinite(video.duration)&&video.currentTime>0&&video.currentTime<video.duration-0.1)seenMain=true;}
  function reset(){seenMain=false;}
  function bind(){const next=document.querySelector(youtube?'#movie_player video':'[data-bilifocus-player] video');if(next===video)return;if(video){video.removeEventListener('timeupdate',progress);video.removeEventListener('loadstart',reset);}video=next;reset();if(video){video.addEventListener('timeupdate',progress);video.addEventListener('loadstart',reset);}}
  function request(name,value){action={seq:++seq,name,value};}
  function hideTransition(){
    transitionEpoch++;clearTimeout(transitionTimer);transitionTimer=0;transitionActive=false;
    const node=own('cf-playback-transition');if(node){node.setAttribute('data-active','false');node.setAttribute('aria-hidden','true');}
  }
  function prepareTransition(info={}){
    const identity=JSON.stringify([plain(info.key||info.id||''),plain(info.title),plain(info.author,120),info.platform==='youtube'?'youtube':'bilibili',Number.isInteger(info.part)?info.part:1,info.cancellable!==false]);
    if(identity===transitionIdentity)return true;
    transitionIdentity=identity;
    ensureUi();bind();if(info.cancellable===false&&video&&!video.paused){try{video.pause();}catch(_){}}
    transitionEpoch++;clearTimeout(transitionTimer);transitionTimer=0;transitionActive=true;transitionIncoming=info.cancellable!==false;transitionStarted=Date.now();
    const target=own('cf-playback-transition');target.setAttribute('data-active','true');target.setAttribute('aria-hidden','false');
    const part=Number.isInteger(info.part)&&info.part>1?' · 第 '+info.part+' P':'';
    own('cf-transition-title').textContent=plain(info.title)||'下一条关注视频';
    own('cf-transition-author').textContent=(info.platform==='youtube'?'YouTube':'B站')+' · '+(plain(info.author,120)||'我的关注')+part;
    own('cf-transition-stop').hidden=!transitionIncoming;
    own('cf-transition-view').hidden=!transitionIncoming;
    return true;
  }
  function finishTransition(){
    if(!transitionActive||!transitionIncoming||transitionTimer)return;
    const epoch=transitionEpoch,delay=Math.max(0,220-(Date.now()-transitionStarted));
    transitionTimer=setTimeout(()=>{transitionTimer=0;if(transitionEpoch===epoch)hideTransition();},delay);
  }
  function nextItem(){return items.find(item=>!item.current);}
  function controls(config){
    ensureUi();updateQueue(config);
    enabled=!!config.autoplay;
    const count=items.filter(item=>!item.current).length;
    own('cf-queue-fab').textContent='待播列表（'+count+'）';
    const bar=document.getElementById(youtube?'cf-yt-toolbar':'bilifocus-toolbar');if(!bar)return;
    let queueButton=document.getElementById('cf-queue-button');
    if(!queueButton){queueButton=button('cf-queue-button','待播列表',()=>setDrawer(!drawerOpen));queueButton.title='查看和选择后续作品（Q）';queueButton.setAttribute('aria-controls','cf-queue-panel');queueButton.setAttribute('aria-expanded',String(drawerOpen));bar.append(queueButton);}
    queueButton.textContent='待播列表（'+count+'）';
    let autoplay=document.getElementById('cf-autoplay');
    if(!autoplay){autoplay=button('cf-autoplay','',()=>request('autoplay',!enabled));bar.append(autoplay);bar.append(button('cf-next-video','下一条',()=>{const next=nextItem();if(next)prepareTransition({...next,cancellable:false});request('next');}));}
    const text=enabled?'自动连播：开':'自动连播：关';if(autoplay.textContent!==text)autoplay.textContent=text;
    autoplay.setAttribute('aria-pressed',String(enabled));autoplay.title='只播放程序中已添加博主的作品；当前清单还剩 '+config.remaining+' 条，播完停止。';
    document.getElementById('cf-next-video').disabled=!config.remaining;
  }
  function stopPlatformNext(){
    if(youtube){const toggle=document.querySelector('#movie_player .ytp-autonav-toggle-button[aria-checked="true"]');if(toggle)toggle.click();}
    else try{const abort=window.nano?.HandoffKind?.Abort,p=window.player;if(abort!=null&&p?.setHandoff&&p.getHandoff?.()!==abort)p.setHandoff(abort);}catch(_){}
  }
  function observe(config){
    if(!config||config.code!==initial)return null;
    if(token!==config.token){const earlyCancel=!token&&action?.name==='cancel'?action:null;token=config.token;seq=earlyCancel?.seq||0;action=earlyCancel;attempted=false;endedTransition=false;queueVersion=null;reset();}
    bind();controls(config);stopPlatformNext();
    if(config.transition&&transitionSeen!==config.token){transitionSeen=config.token;if(!transitionActive)prepareTransition(config.transition);}
    if(action?.name==='cancel'){video?.pause();return {token,code:initial,action};}
    const ad=isAd(),valid=selected();
    if(ad)seenMain=false;
    if(!ad&&!valid){video?.pause();return {token,code:initial,unexpected:true};}
    progress();
    if(config.start&&video&&!ad&&valid&&!attempted&&(video.readyState>=1||video.currentSrc||video.getAttribute('src')||video.srcObject)&&!video.ended){attempted=true;try{Promise.resolve(video.play()).catch(()=>{hideTransition();window.BiliFocus?.showNotice('请点击播放器开始播放，之后会只连播你的关注清单。');});}catch(_) {hideTransition();}}
    if(transitionActive&&transitionIncoming&&video&&(ad||valid)&&!video.paused&&video.currentTime>0&&video.readyState>=2)finishTransition();
    const ended=!!(video&&!ad&&valid&&seenMain&&video.ended&&Number.isFinite(video.duration)&&video.duration>0&&video.currentTime>=video.duration-0.5);
    const part=youtube?1:Number(new URL(location.href).searchParams.get('p')||1);
    const pages=youtube?1:Math.max(1,Math.min(999,window.__INITIAL_STATE__?.videoData?.pages?.length||1));
    if(ended&&enabled&&!endedTransition){const next=!youtube&&part<pages?{...items.find(item=>item.current),part:part+1}:nextItem();if(next){endedTransition=true;prepareTransition({...next,cancellable:false});}}
    return {token,code:initial,ended,part,pages,action};
  }
  document.addEventListener('keydown',event=>{
    if(event.ctrlKey||event.altKey||event.metaKey||event.repeat||event.target?.isContentEditable||event.target?.closest?.('input,textarea,select,[contenteditable="true"]'))return;
    if(String(event.key).toLowerCase()==='q'){event.preventDefault();event.stopImmediatePropagation();setDrawer(!drawerOpen);}
    else if(event.key==='Escape'&&drawerOpen){event.preventDefault();event.stopImmediatePropagation();setDrawer(false);}
  },true);
  document.addEventListener('pointermove',event=>{if(Number.isFinite(event.clientY)&&event.clientY<=88)reveal();},{passive:true});
  document.addEventListener('fullscreenchange',()=>{mount();if(isFullscreen())reveal();});
  document.addEventListener('DOMContentLoaded',mount);
  document.addEventListener('readystatechange',mount);
  window.CustomFollowPlayback=Object.freeze({observe,prepareTransition});
})();
