/* Presentation-only focus mode for the selected official YouTube video.
 * Never reads account credentials, downloads media, skips ads, or changes access rights.
 */
(() => {
  'use strict';
  if (top !== self || location.protocol !== 'https:' ||
      !['www.youtube.com','youtube.com','m.youtube.com'].includes(location.hostname) || window.BiliFocus) return;
  function code(url) {
    const u = new URL(url, location.href);
    if (!['www.youtube.com','youtube.com','m.youtube.com'].includes(u.hostname)) return null;
    const ids = u.searchParams.getAll('v');
    if (u.pathname === '/watch' && ids.length === 1 && /^[\w-]{11}$/.test(ids[0])) return ids[0];
    const m = u.pathname.match(/^\/(?:shorts|live)\/([\w-]{11})\/?$/);
    return m ? m[1] : null;
  }
  const selected = code(location.href);
  if (!selected) return;
  const OWN = 'data-cf-owned', HIDDEN = 'data-cf-hidden', PATH = 'data-cf-path';
  let mode = 'pending', player = null, timer = 0, ready = false, scans = 0;
  const started = Date.now(), hidden = new Set(), ancestors = new Set(), toggled = new WeakSet();
  const style = document.createElement('style');
  style.setAttribute(OWN, '');
  style.textContent = `
    html[data-cf-yt="pending"] body>:not([${OWN}]),html[data-cf-yt="failed"] body>:not([${OWN}]){visibility:hidden!important}
    html[data-cf-yt="focus"] body{margin:0!important;padding:0!important;overflow:hidden!important;background:#0d1017!important}
    html[data-cf-yt="focus"] [${HIDDEN}]{display:none!important}
    html[data-cf-yt="focus"] [${PATH}]{transform:none!important;filter:none!important;contain:none!important;overflow:visible!important}
    html[data-cf-yt="focus"] [data-cf-player]{position:fixed!important;inset:54px 0 0!important;width:100vw!important;height:calc(100vh - 54px)!important;min-width:0!important;max-width:none!important;max-height:none!important;margin:0!important;padding:0!important;z-index:10!important;background:#000!important}
    html[data-cf-yt="focus"] [data-cf-player] .html5-video-container{width:100%!important;height:100%!important}
    html[data-cf-yt="focus"] [data-cf-player] video{width:100%!important;height:100%!important;left:0!important;top:0!important;object-fit:contain!important}
    html[data-cf-yt="focus"] [data-cf-player] .ytp-chrome-bottom{left:12px!important;right:12px!important;width:calc(100% - 24px)!important}
    html[data-cf-yt="focus"] .ytp-endscreen-content,html[data-cf-yt="focus"] .ytp-ce-element,html[data-cf-yt="focus"] .ytp-pause-overlay{display:none!important}
    #cf-yt-toolbar,#cf-yt-wait,#cf-yt-notice{box-sizing:border-box!important;font:13px/1.5 "Microsoft YaHei UI",sans-serif!important;color:#e8edf7!important}
    #cf-yt-toolbar{position:fixed!important;inset:0 0 auto!important;height:54px!important;z-index:2147483000!important;display:flex!important;align-items:center!important;gap:14px!important;padding:0 18px!important;background:#171c28!important;border-bottom:1px solid #30394c!important}
    #cf-yt-toolbar b{color:#ff9caf!important;white-space:nowrap!important}#cf-yt-title{flex:1!important;overflow:hidden!important;text-overflow:ellipsis!important;white-space:nowrap!important}
    #cf-yt-toolbar button,#cf-yt-wait button{font:inherit!important;background:#30394d!important;color:#f2f5ff!important;border:1px solid #46536d!important;border-radius:7px!important;padding:6px 12px!important;cursor:pointer!important;white-space:nowrap!important}
    #cf-yt-wait{position:fixed!important;inset:54px 0 0!important;display:grid!important;place-content:center!important;background:#0d1017!important;padding:30px!important;z-index:2147482900!important;text-align:center!important}
    #cf-yt-wait p{color:#aab5c9!important;max-width:600px!important;margin:12px auto 20px!important}
    html[data-cf-yt="full"] #cf-yt-toolbar{inset:auto 16px 16px auto!important;width:auto!important;height:auto!important;padding:9px 12px!important;border-radius:9px!important}
    html[data-cf-yt="full"] #cf-yt-title{display:none!important}
    #cf-yt-notice{position:fixed!important;bottom:70px!important;left:50%!important;transform:translateX(-50%)!important;background:#28334b!important;padding:10px 18px!important;border-radius:8px!important;z-index:2147483100!important}
    html[data-cf-yt-fullscreen] #cf-yt-toolbar{display:none!important}
  `;
  function attach() {
    if (!document.documentElement) return;
    if (!style.isConnected) document.documentElement.append(style);
    document.documentElement.setAttribute('data-cf-yt', mode);
  }
  attach();
  function element(tag, text) { const e = document.createElement(tag); if(text !== undefined)e.textContent=text;return e; }
  function toolbar() {
    if (!document.body) return;
    let bar = document.getElementById('cf-yt-toolbar');
    if (!bar) {
      bar=element('div');bar.id='cf-yt-toolbar';bar.setAttribute(OWN,'');bar.setAttribute('role','region');bar.setAttribute('aria-label','YouTube 专注播放');
      const label=element('b','YouTube · 专注播放'), title=element('span');title.id='cf-yt-title';
      const toggle=element('button');toggle.type='button';toggle.addEventListener('click',()=>setMode(mode==='full'?'focus':'full'));
      bar.append(label,title,toggle);document.body.append(bar);
    }
    bar.querySelector('#cf-yt-title').textContent=(document.title||'正在载入官方播放器').replace(/ - YouTube$/,'');
    bar.querySelector('button').textContent=mode==='full'?'返回专注模式':'完整页面 / 登录';
  }
  function isDialog(node) {
    return node.matches('ytd-popup-container,tp-yt-paper-dialog,dialog,[role="dialog"],[aria-modal="true"],ytd-consent-bump-v2-lightbox');
  }
  function hide(node) {
    if (!(node instanceof Element) || node===player || node.contains(player) || node.hasAttribute(OWN) ||
        ['SCRIPT','STYLE','LINK','META'].includes(node.tagName) || isDialog(node) || node.querySelector('dialog[open],[role="dialog"][aria-modal="true"]')) return;
    node.setAttribute(HIDDEN,'');hidden.add(node);
  }
  function isolate() {
    for(const n of hidden)if(!n.isConnected||isDialog(n)||n.contains(player)){n.removeAttribute(HIDDEN);hidden.delete(n);}
    player.setAttribute('data-cf-player','');let branch=player;
    while(branch.parentElement&&branch.parentElement!==document.documentElement){const parent=branch.parentElement;parent.setAttribute(PATH,'');ancestors.add(parent);for(const sibling of parent.children)if(sibling!==branch)hide(sibling);branch=parent;if(parent===document.body)break;}
    // The ordinary official control disables autoplay. No media requests or
    // player rights are changed, and advertisements retain their normal controls.
    const autoplay=player.querySelector('.ytp-autonav-toggle-button[aria-checked="true"]');
    if(autoplay&&!toggled.has(autoplay)){toggled.add(autoplay);autoplay.click();}
  }
  function waiting() {
    let box=document.getElementById('cf-yt-wait');
    if(!box){box=element('div');box.id='cf-yt-wait';box.setAttribute(OWN,'');document.body.append(box);}
    const heading=element('h2',mode==='failed'?'暂未找到 YouTube 播放器':'正在载入 YouTube 播放器');
    const copy=element('p','网络、登录、验证或地区权限可能影响播放。可打开完整官方页面处理提示，再切回专注模式。');
    const button=element('button','显示完整页面');button.addEventListener('click',()=>setMode('full'));box.replaceChildren(heading,copy,button);
  }
  function scan() {
    timer=0;if(!document.body)return;scans++;attach();toolbar();
    const next=document.querySelector('#movie_player.html5-video-player,#movie_player,#player .html5-video-player');
    if(next&&next!==player){if(player)player.removeAttribute('data-cf-player');player=next;}
    ready=Boolean(player&&player.isConnected);
    if(mode==='full')return;
    if(ready){mode='focus';attach();document.getElementById('cf-yt-wait')?.remove();isolate();}
    else{mode=Date.now()-started>20000?'failed':'pending';attach();waiting();}
  }
  function schedule(){if(!timer)timer=setTimeout(scan,180);}
  function setMode(next){mode=next==='full'?'full':'pending';attach();if(mode==='full')document.getElementById('cf-yt-wait')?.remove();scan();window.dispatchEvent(new Event('resize'));}
  let noticeTimer;
  function showNotice(text){if(!document.body)return;let n=document.getElementById('cf-yt-notice');if(!n){n=element('div');n.id='cf-yt-notice';n.setAttribute(OWN,'');document.body.append(n);}n.textContent=String(text).slice(0,160);clearTimeout(noticeTimer);noticeTimer=setTimeout(()=>n.remove(),6000);}
  function different(url){try{const target=new URL(url,location.href);const id=code(target.href);return Boolean(id&&id!==selected)||(['www.youtube.com','youtube.com','m.youtube.com'].includes(target.hostname)&&target.pathname==='/' );}catch(_){return true;}}
  document.addEventListener('click',event=>{const a=event.target.closest?.('a[href]');if(a&&different(a.href)){event.preventDefault();event.stopImmediatePropagation();showNotice('请返回定制关注选择其他视频');}},true);
  for(const method of ['pushState','replaceState']){const original=history[method];history[method]=function(state,title,url){if(url&&different(url)){showNotice('请返回定制关注选择其他视频');return;}return original.apply(this,arguments);};}
  function begin(){if(!document.body)return;scan();const observer=new MutationObserver(records=>{if(records.some(r=>!(r.target instanceof Element)||(!r.target.closest(`[${OWN}]`)&&(!player||!player.contains(r.target)))))schedule();});observer.observe(document.body,{childList:true,subtree:true});setTimeout(schedule,2500);setTimeout(schedule,21000);}
  window.BiliFocus=Object.freeze({toggle:()=>setMode(mode==='full'?'focus':'full'),focus:()=>setMode('focus'),full:()=>setMode('full'),showNotice,
    status:()=>({mode,ready,playerFound:Boolean(player&&player.isConnected),hiddenBranches:hidden.size,scanCount:scans,platform:'youtube'})});
  document.addEventListener('fullscreenchange',()=>document.documentElement.toggleAttribute('data-cf-yt-fullscreen',Boolean(document.fullscreenElement)));
  document.addEventListener('DOMContentLoaded',begin,{once:true});if(document.body)begin();
})();
