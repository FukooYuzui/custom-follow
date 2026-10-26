/* BiliFocus: presentation-only changes to the official video page.
 * No credentials, media URLs, permissions or account actions are inspected.
 * Installed at document creation by the native WebView2 host.
 */
(() => {
  'use strict';
  if (window.top !== window.self || location.protocol !== 'https:' ||
      location.hostname !== 'www.bilibili.com' || window.BiliFocus) return;
  const pathVideo = location.pathname.match(/^\/video\/(BV[0-9A-Za-z]{10})(?:\/|$)/);
  const festivalPath = /^\/festival\/[A-Za-z0-9_-]+\/?$/.test(location.pathname);
  const festivalValues = new URLSearchParams(location.search || '').getAll('bvid');
  const festivalVideo = festivalPath && festivalValues.length === 1 &&
    /^BV[0-9A-Za-z]{10}$/.test(festivalValues[0]) ? festivalValues[0] : null;
  const initialBvid = pathVideo ? pathVideo[1] : festivalVideo;
  if (!initialBvid) return;

  const ATTR = 'data-bilifocus';
  const OWN = 'data-bilifocus-owned';
  const HIDDEN = 'data-bilifocus-hidden';
  const PATH = 'data-bilifocus-path';
  const ROOT = 'data-bilifocus-player';
  const STYLE_ID = 'bilifocus-style';
  const TOOLBAR_ID = 'bilifocus-toolbar';
  const WAIT_ID = 'bilifocus-wait';
  const NOTICE_ID = 'bilifocus-notice';
  const roots = ['#bilibili-player', '#bilibiliPlayer', '#bilibili-player-wrap', '.bpx-player-container'];
  const knownDistractions = [
    '#biliMainHeader', '.bili-header', '.right-container', '.right-container-inner',
    '.rec-list', '.recommend-list-v1', '.video-info-container', '.video-toolbar-container',
    '.video-desc-container', '.video-tag-container', '#commentapp', '#comment',
    '.comment-container', '.video-comments', '.fixed-sidenav-storage',
    '.up-info-container', '.up-panel-container', '.video-sections-content-list',
    '.ad-report', '.slide_ad', '.right-bottom-banner', '#arc_toolbar_report',
    '#videoToolbar', '.video-toolbar', '.video-toolbar-left', '.video-toolbar-right',
    '.danmaku-box', '.danmaku-wrap', '#danmukuBox', '.video-danmuku',
    '.video-sections', '.video-desc-wrapper', '.video-staffs', '.video-toolbar_bottom'
  ];
  // The root video player is never filtered internally. External dialogs retain
  // the platform's own login, verification, subscription and payment behavior.
  const dialogSelector = [
    'dialog', '[role="dialog"]', '[aria-modal="true"]',
    '.bili-mini-login-window', '.bili-mini-mask', '.bili-login-dialog',
    '.bili-login-panel', '.bili-login-container', '.bili-dialog-m', '.bili-dialog-bomb',
    '.bili-popup', '.geetest_panel', '.geetest_holder', '.geetest_wind',
    '.bili-captcha', '.bili-risk-dialog', '.bili-pay-dialog', '.bili-purchase-dialog',
    'iframe[class*="login-window"]', '[id="bili-login-dialog"]'
  ].join(',');

  let mode = 'pending';
  let player = null;
  let ready = false;
  let observer = null;
  let scanTimer = 0;
  let scans = 0;
  let started = false;
  let latestTitle = '';
  let noticeTimer = 0;
  let queuedNotice = '';
  let danmakuHidden = true;
  const hidden = new Set();
  const markedPath = new Set();
  const startedAt = Date.now();

  const style = document.createElement('style');
  style.id = STYLE_ID;
  style.setAttribute(OWN, '');
  style.textContent = `
    html[${ATTR}="pending"] body > :not([${OWN}]),
    html[${ATTR}="failed"] body > :not([${OWN}]) { visibility: hidden !important; }
    html[${ATTR}="focus"],html[${ATTR}="pending"],html[${ATTR}="failed"] {
      background:#0c0e14!important; color-scheme:dark;
    }
    html[${ATTR}="focus"] body, html[${ATTR}="pending"] body, html[${ATTR}="failed"] body {
      margin:0!important; padding:0!important; min-width:0!important;
      background:#0c0e14!important; overflow:hidden!important;
    }
    html[${ATTR}="focus"] [${HIDDEN}] { display:none!important; }
    html[${ATTR}="focus"] [${PATH}]:not(body) {
      transform:none!important; filter:none!important; perspective:none!important;
      contain:none!important; overflow:visible!important; min-width:0!important;
    }
    html[${ATTR}="focus"] [${ROOT}] {
      position:fixed!important; inset:54px 0 0!important; width:100vw!important;
      height:calc(100vh - 54px)!important; min-width:0!important; min-height:0!important;
      max-width:none!important; max-height:none!important; margin:0!important;
      padding:0!important; background:#000!important; box-sizing:border-box!important;
    }
    html[${ATTR}="focus"] [${ROOT}] > .bpx-player-container {
      width:100%!important; height:100%!important; min-height:0!important;
    }
    html[${ATTR}="focus"] [${ROOT}] .bpx-player-primary-area { height:100%!important; }
    html[${ATTR}="focus"] [${ROOT}] .bpx-player-sending-area,
    html[${ATTR}="focus"] [${ROOT}] .bpx-player-sending-bar { display:none!important; }
    html[${ATTR}="focus"][data-bilifocus-danmaku-hidden] .bpx-player-dm-wrap,
    html[${ATTR}="focus"][data-bilifocus-danmaku-hidden] .bpx-player-row-dm-wrap,
    html[${ATTR}="focus"][data-bilifocus-danmaku-hidden] .bpx-player-adv-dm-wrap,
    html[${ATTR}="focus"][data-bilifocus-danmaku-hidden] .bpx-player-bas-dm-wrap,
    html[${ATTR}="focus"][data-bilifocus-danmaku-hidden] .bpx-player-render-dm-wrap,
    html[${ATTR}="focus"][data-bilifocus-danmaku-hidden] .bpx-player-cmd-dm-wrap {
      opacity:0!important; visibility:hidden!important; pointer-events:none!important;
    }
    /* These optional legacy panels are recommendations, not access dialogs. */
    html[${ATTR}="focus"] .bpx-player-ending-related,
    html[${ATTR}="focus"] .bpx-player-ending-related-list { display:none!important; }
    #${TOOLBAR_ID}, #${WAIT_ID}, #${NOTICE_ID} {
      font-family:"Microsoft YaHei UI","Microsoft YaHei",sans-serif!important;
      box-sizing:border-box!important; color:#e8edf7!important;
      font-size:12px!important; line-height:1.5!important; text-align:left!important;
    }
    #${TOOLBAR_ID} {
      position:fixed!important; inset:0 0 auto!important; height:54px!important;
      z-index:2147483000!important; display:flex!important; align-items:center!important;
      gap:16px!important; padding:0 18px!important; background:#171c28!important;
      border-bottom:1px solid #30394c!important;
    }
    #${TOOLBAR_ID} .bilifocus-brand { color:#f49abb!important; font-weight:700!important; flex-shrink:0!important; }
    #${TOOLBAR_ID} .bilifocus-title { overflow:hidden!important; text-overflow:ellipsis!important; white-space:nowrap!important; flex:1!important; min-width:0!important; }
    #${TOOLBAR_ID} button, #${WAIT_ID} button {
      appearance:none!important; background:#30394d!important; color:#f1f4fb!important;
      border:1px solid #46536d!important; border-radius:7px!important; padding:6px 12px!important;
      font:inherit!important; cursor:pointer!important; margin:0!important; white-space:nowrap!important;
    }
    #${TOOLBAR_ID} button:hover, #${WAIT_ID} button:hover { background:#414b62!important; }
    #${TOOLBAR_ID} button:focus-visible, #${WAIT_ID} button:focus-visible { outline:2px solid #f49abb!important; outline-offset:2px!important; }
    #${WAIT_ID} {
      position:fixed!important; inset:54px 0 0!important; display:grid!important;
      place-content:center!important; padding:28px!important; background:#0c0e14!important;
      z-index:2147482900!important;
    }
    #${WAIT_ID} .bilifocus-wait-title { font-size:20px!important; font-weight:700!important; margin-bottom:12px!important; }
    #${WAIT_ID} .bilifocus-wait-copy { color:#a7b4cc!important; max-width:530px!important; margin-bottom:20px!important; }
    #${NOTICE_ID} {
      position:fixed!important; left:50%!important; bottom:35px!important;
      transform:translateX(-50%)!important; max-width:min(560px,calc(100vw - 48px))!important;
      z-index:2147483100!important; background:#283349!important; border:1px solid #586986!important;
      border-radius:10px!important; padding:12px 18px!important; box-shadow:0 8px 30px #0007!important;
      pointer-events:none!important; overflow-wrap:anywhere!important;
    }
    html[${ATTR}="full"] #${TOOLBAR_ID} {
      inset:auto 18px 16px auto!important; width:auto!important; height:auto!important;
      padding:9px 12px!important; border:1px solid #46536d!important; border-radius:10px!important;
      box-shadow:0 3px 20px #0005!important;
    }
    html[${ATTR}="full"] #${TOOLBAR_ID} .bilifocus-title { display:none!important; }
    html[${ATTR}="full"] #${TOOLBAR_ID} .bilifocus-danmaku { display:none!important; }
    html[data-bilifocus-fullscreen] #${TOOLBAR_ID} { display:none!important; }
    @media(max-width:700px) { #${TOOLBAR_ID} { gap:8px!important;padding:0 10px!important; } #${TOOLBAR_ID} .bilifocus-brand {font-size:11px!important;} }
  `;

  function attachStyle() {
    if (!document.documentElement) return false;
    if (!document.getElementById(STYLE_ID)) document.documentElement.append(style);
    document.documentElement.setAttribute(ATTR, mode);
    document.documentElement.toggleAttribute('data-bilifocus-danmaku-hidden', danmakuHidden);
    return true;
  }
  // document_start can precede <head> and <body>; the root is sufficient.
  attachStyle();

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function ensureToolbar() {
    if (!document.body) return;
    let bar = document.getElementById(TOOLBAR_ID);
    if (!bar) {
      bar = element('div');
      bar.id = TOOLBAR_ID;
      bar.setAttribute(OWN, '');
      bar.setAttribute('role', 'region');
      bar.setAttribute('aria-label', 'B站专注播放控制');
      bar.append(element('span', 'bilifocus-brand', '官方网页专注播放'));
      const title = element('span', 'bilifocus-title', '正在载入官方播放器…');
      title.title = '清晰度取决于视频和账号权限；如需网页登录，可暂时显示完整页面。';
      bar.append(title);
      const danmakuButton = element('button', 'bilifocus-danmaku', '弹幕：隐藏');
      danmakuButton.type = 'button';
      danmakuButton.addEventListener('click', () => {
        danmakuHidden = !danmakuHidden;
        document.documentElement.toggleAttribute('data-bilifocus-danmaku-hidden', danmakuHidden);
        ensureToolbar();
      });
      bar.append(danmakuButton);
      const toggleButton = element('button', 'bilifocus-toggle', '完整页面 / 登录');
      toggleButton.type = 'button';
      toggleButton.title = '显示完整官网页面以登录或处理播放器提示；可随时返回专注模式。';
      toggleButton.addEventListener('click', toggle);
      bar.append(toggleButton);
      document.body.append(bar);
    }
    const name = document.title.replace(/[_\-]哔哩哔哩.*$/, '').trim();
    if (name && name !== latestTitle) {
      latestTitle = name;
      bar.querySelector('.bilifocus-title').textContent = name;
    }
    const label = mode === 'full' ? '返回专注模式' : '完整页面 / 登录';
    const button = bar.querySelector('.bilifocus-toggle');
    if (button.textContent !== label) button.textContent = label;
    const danmakuButton = bar.querySelector('.bilifocus-danmaku');
    const danmakuLabel = danmakuHidden ? '弹幕：隐藏' : '弹幕：显示';
    if (danmakuButton.textContent !== danmakuLabel) danmakuButton.textContent = danmakuLabel;
    danmakuButton.title = danmakuHidden ? '点击显示弹幕；此开关不会改变你的B站账号设置。' : '点击隐藏弹幕；此开关不会改变你的B站账号设置。';
    danmakuButton.setAttribute('aria-pressed', String(!danmakuHidden));
  }

  function showWaiting(failed = false) {
    if (!document.body) return;
    let waiting = document.getElementById(WAIT_ID);
    if (!waiting) {
      waiting = element('div');
      waiting.id = WAIT_ID;
      waiting.setAttribute(OWN, '');
      const content = element('div');
      content.append(element('div', 'bilifocus-wait-title'));
      content.append(element('div', 'bilifocus-wait-copy'));
      const button = element('button', '', '显示完整官网页面');
      button.type = 'button';
      button.addEventListener('click', () => setMode('full'));
      content.append(button);
      waiting.append(content);
      document.body.append(waiting);
    }
    waiting.querySelector('.bilifocus-wait-title').textContent = failed ? '暂未找到官方播放器' : '正在载入官方播放器';
    waiting.querySelector('.bilifocus-wait-copy').textContent = failed
      ? '可能是网络、平台验证或页面布局变化。你可以显示完整官网页面处理提示，再切回专注模式。程序不会跳过登录、验证或播放权限。'
      : '清晰度和可播放内容由 B站及账号权限决定。登录或验证需要你自己在官方页面完成。';
  }

  function isDialogBranch(node) {
    if (!(node instanceof Element)) return false;
    const candidates = [...node.querySelectorAll(dialogSelector)];
    if (node.matches(dialogSelector)) candidates.unshift(node);
    return candidates.some(dialog => {
      if (dialog.hidden || dialog.getAttribute('aria-hidden') === 'true' ||
          (dialog.tagName === 'DIALOG' && !dialog.hasAttribute('open'))) return false;
      // A hidden/empty login placeholder or a login trigger must not preserve
      // its whole recommendation/action-toolbar branch.
      for (let current = dialog; current && current !== node.parentElement; current = current.parentElement) {
        if (current.hidden || current.getAttribute('aria-hidden') === 'true' || current.style.display === 'none') return false;
        if (typeof window.getComputedStyle === 'function') {
          const computed = window.getComputedStyle(current);
          if (computed.visibility === 'hidden' ||
              (computed.display === 'none' && !current.hasAttribute(HIDDEN))) return false;
        }
      }
      if (!dialog.children.length && !dialog.textContent.trim() &&
          !['IFRAME','DIALOG'].includes(dialog.tagName)) return false;
      return true;
    });
  }
  function hideNode(node) {
    if (!(node instanceof Element) || node.hasAttribute(OWN) ||
        ['SCRIPT','STYLE','LINK','META'].includes(node.tagName) ||
        node === player || node.contains(player) || isDialogBranch(node)) return;
    node.setAttribute(HIDDEN, '');
    hidden.add(node);
  }
  function unmarkDetached() {
    for (const node of hidden) {
      if (!node.isConnected || isDialogBranch(node) || node === player || node.contains(player)) {
        node.removeAttribute(HIDDEN);
        hidden.delete(node);
      }
    }
    for (const node of markedPath) {
      if (!node.isConnected || !node.contains(player)) {
        node.removeAttribute(PATH);
        markedPath.delete(node);
      }
    }
  }
  function findPlayer() {
    for (const selector of roots) {
      const node = document.querySelector(selector);
      if (node) return node;
    }
    return null;
  }
  function isolate() {
    if (!player || !player.isConnected) return;
    unmarkDetached();
    player.setAttribute(ROOT, '');
    let branch = player;
    while (branch.parentElement && branch.parentElement !== document.documentElement) {
      const parent = branch.parentElement;
      parent.setAttribute(PATH, '');
      markedPath.add(parent);
      for (const sibling of parent.children) if (sibling !== branch) hideNode(sibling);
      branch = parent;
      if (parent === document.body) break;
    }
    for (const selector of knownDistractions) {
      for (const node of document.querySelectorAll(selector)) {
        if (!player.contains(node)) hideNode(node);
      }
    }
  }

  function scan() {
    scanTimer = 0;
    if (!document.documentElement || !document.body) return;
    scans++;
    attachStyle();
    ensureToolbar();
    const nextPlayer = findPlayer();
    if (nextPlayer && player !== nextPlayer) {
      if (player) player.removeAttribute(ROOT);
      player = nextPlayer;
    }
    ready = Boolean(player && player.isConnected);
    if (mode === 'full') return;
    if (ready) {
      mode = 'focus';
      document.documentElement.setAttribute(ATTR, mode);
      const waiting = document.getElementById(WAIT_ID);
      if (waiting) waiting.remove();
      isolate();
    } else {
      mode = Date.now() - startedAt >= 20000 ? 'failed' : 'pending';
      document.documentElement.setAttribute(ATTR, mode);
      showWaiting(mode === 'failed');
    }
  }
  function scheduleScan() {
    if (!scanTimer) scanTimer = setTimeout(scan, 160);
  }
  function setMode(nextMode) {
    mode = nextMode === 'full' ? 'full' : (findPlayer() ? 'focus' : 'pending');
    document.documentElement.setAttribute(ATTR, mode);
    if (mode === 'full') {
      const waiting = document.getElementById(WAIT_ID);
      if (waiting) waiting.remove();
    }
    ensureToolbar();
    scan();
    // Notify platform resize listeners without changing media or account state.
    window.dispatchEvent(new Event('resize'));
  }
  function toggle() { setMode(mode === 'full' ? 'focus' : 'full'); }

  function showNotice(text) {
    queuedNotice = String(text || '').slice(0, 280);
    if (!document.body || !queuedNotice) return;
    let notice = document.getElementById(NOTICE_ID);
    if (!notice) {
      notice = element('div');
      notice.id = NOTICE_ID;
      notice.setAttribute(OWN, '');
      notice.setAttribute('role', 'status');
      document.body.append(notice);
    }
    notice.textContent = queuedNotice;
    queuedNotice = '';
    clearTimeout(noticeTimer);
    noticeTimer = setTimeout(() => { if (notice.isConnected) notice.remove(); }, 6000);
  }
  function isDifferentVideo(url) {
    try {
      const target = new URL(String(url), location.href);
      if (target.hostname !== 'www.bilibili.com') return false;
      const match = target.pathname.match(/^\/video\/(BV[0-9A-Za-z]{10})(?:\/|$)/);
      if (match) return match[1] !== initialBvid;
      if (/^\/festival\/[A-Za-z0-9_-]+\/?$/.test(target.pathname)) {
        const values = target.searchParams.getAll('bvid');
        return values.length !== 1 || values[0] !== initialBvid;
      }
      return false;
    } catch (_) { return false; }
  }
  function blockOtherVideo() {
    showNotice('为保持专注，请回到关注列表选择下一条视频。当前窗口只播放你选中的视频及其分 P。');
  }
  document.addEventListener('click', event => {
    const target = event.target instanceof Element ? event.target.closest('a[href]') : null;
    if (target && isDifferentVideo(target.href || target.getAttribute('href'))) {
      event.preventDefault();
      event.stopImmediatePropagation();
      blockOtherVideo();
    }
  }, true);
  // SPA recommendations can change a video without a full native navigation.
  // Same-video part changes, login/payment links and resource loading are untouched.
  if (window.history) {
    for (const method of ['pushState', 'replaceState']) {
      const original = window.history[method];
      if (typeof original !== 'function') continue;
      window.history[method] = function (...args) {
        if (args.length > 2 && args[2] != null && isDifferentVideo(args[2])) {
          blockOtherVideo();
          return;
        }
        return original.apply(this, args);
      };
    }
  }

  function begin() {
    if (started || !document.body) return;
    started = true;
    scan();
    if (queuedNotice) showNotice(queuedNotice);
    observer = new MutationObserver(records => {
      const relevant = records.some(record => {
        const target = record.target;
        if (target instanceof Element && target.closest(`[${OWN}]`)) return false;
        // Danmaku/video controls may mutate continuously; do not rescan for them.
        return !player || !player.contains(target);
      });
      if (relevant) scheduleScan();
    });
    observer.observe(document.body, {childList:true, subtree:true});
    setTimeout(scheduleScan, 2000);
    setTimeout(scheduleScan, 21000);
  }
  window.BiliFocus = Object.freeze({
    toggle,
    focus: () => setMode('focus'),
    full: () => setMode('full'),
    showNotice,
    status: () => ({ mode, ready, playerFound:Boolean(player && player.isConnected),
      playerSelector:player ? (player.id ? '#' + player.id : '.bpx-player-container') : null,
      hiddenBranches:hidden.size, scanCount:scans, danmakuHidden })
  });
  document.addEventListener('fullscreenchange', () => {
    document.documentElement.toggleAttribute('data-bilifocus-fullscreen', Boolean(document.fullscreenElement));
  });
  document.addEventListener('DOMContentLoaded', begin, {once:true});
  if (document.body) begin();
  else if (!document.documentElement) {
    const starter = new MutationObserver(() => { if (attachStyle()) starter.disconnect(); });
    starter.observe(document, {childList:true, subtree:true});
  }
})();
