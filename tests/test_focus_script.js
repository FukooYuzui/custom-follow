/* DOM-level tests. Run npm ci, then npm test. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {parseHTML} = require('linkedom');
const source = fs.readFileSync(path.join(__dirname, '../assets/focus_mode.js'), 'utf8');
const baseTime = 1900000000000;

function page(html, options = {}) {
  const {window} = parseHTML(html);
  let elapsed = 0;
  let id = 0;
  const timers = new Map();
  const ctx = {
    document: window.document, Element: window.Element, Event: window.Event,
    MutationObserver: window.MutationObserver,
    location: {protocol:'https:', hostname:'www.bilibili.com', pathname:'/video/BV1B7411m7LV/', href:'https://www.bilibili.com/video/BV1B7411m7LV/', ...options.location},
    URL, URLSearchParams,
    Date: {now: () => baseTime + elapsed},
    setTimeout: (fn, delay) => { timers.set(++id, {fn, at:elapsed + delay}); return id; },
    clearTimeout: timer => timers.delete(timer),
    dispatchEvent: () => {},
    history: {pushes: [], pushState(...args) {this.pushes.push(args);}, replaceState(...args) {this.pushes.push(args);}},
  };
  ctx.window = ctx;
  ctx.self = ctx;
  ctx.top = options.childFrame ? {} : ctx;
  vm.createContext(ctx);
  vm.runInContext(source, ctx);
  async function advance(milliseconds) {
    elapsed += milliseconds;
    await Promise.resolve();
    for (let pass = 0; pass < 20; pass++) {
      const due = [...timers].filter(([, timer]) => timer.at <= elapsed);
      if (!due.length) break;
      for (const [key, timer] of due) { timers.delete(key); timer.fn(); }
      await Promise.resolve();
    }
  }
  return {ctx, doc:window.document, advance};
}

const fixture = `<!doctype html><html><head><title>科学视频</title></head><body>
  <div id="app"><header class="bili-header"><button class="header-login-entry">登录</button></header>
  <div id="mirror-vdcon"><div class="left-container"><h1>标题</h1><div id="playerWrap"><div id="bilibili-player"><div class="bpx-player-container"><video id="video"></video><button id="quality">清晰度</button><div class="pay-panel" id="payment">官方权限提示</div></div></div></div><section id="commentapp">评论</section><div class="video-toolbar-container"><button class="login-trigger">点赞</button><div class="bili-dialog-m" style="display:none"><button>隐藏对话框</button></div></div><div id="danmukuBox"><button class="bili-danmaku-login">登录发送弹幕</button></div></div><aside class="right-container">相关推荐</aside></div></div>
  <div id="portal"></div><div class="bili-mini-login-window" id="login">官方登录</div>
</body></html>`;

async function run() {
  for (const opts of [
    {location:{hostname:'example.com'}}, {location:{protocol:'http:'}},
    {location:{pathname:'/'}}, {location:{pathname:'/video/not-a-bvid/'}}, {childFrame:true},
    {location:{pathname:'/festival/NobelPrize2026', search:''}},
    {location:{pathname:'/festival/NobelPrize2026', search:'?bvid=BV1B7411m7LV&bvid=BV1B7411m7LV'}},
    {location:{pathname:'/festival/NobelPrize2026/other', search:'?bvid=BV1B7411m7LV'}}
  ]) {
    const {ctx, doc} = page(fixture, opts);
    assert.equal(ctx.BiliFocus, undefined, 'unrelated pages/frames must be untouched');
    assert.equal(doc.getElementById('bilifocus-toolbar'), null);
  }
  const p = page(fixture);
  const {ctx, doc} = p;
  assert.equal(ctx.BiliFocus.status().mode, 'focus');
  assert.equal(ctx.BiliFocus.status().playerFound, true);
  assert.equal(doc.getElementById('video').parentElement.className, 'bpx-player-container', 'player DOM must not be moved');
  assert.equal(doc.getElementById('bilibili-player').parentElement.id, 'playerWrap');
  for (const selector of ['.bili-header', '.right-container', '#commentapp', '.video-toolbar-container', '#danmukuBox']) {
    assert.ok(doc.querySelector(selector).hasAttribute('data-bilifocus-hidden'), selector + ' should be hidden');
  }
  for (const id of ['login', 'quality', 'payment', 'video']) {
    assert.equal(doc.getElementById(id).hasAttribute('data-bilifocus-hidden'), false, 'official access controls remain untouched');
  }
  const count = doc.querySelectorAll('#bilifocus-toolbar').length;
  vm.runInContext(source, ctx);
  assert.equal(doc.querySelectorAll('#bilifocus-toolbar').length, count, 'duplicate injection is harmless');
  ctx.BiliFocus.full();
  assert.equal(doc.documentElement.getAttribute('data-bilifocus'), 'full');
  assert.equal(doc.querySelector('.bilifocus-toggle').textContent, '返回专注模式');
  ctx.BiliFocus.toggle();
  assert.equal(ctx.BiliFocus.status().mode, 'focus');
  ctx.history.pushState(null, '', '/video/BV1B7411m7LV/?p=2');
  ctx.history.replaceState(null, '', '/video/BV1B7411m7LV/?p=3');
  assert.equal(ctx.history.pushes.length, 2, 'same video part changes remain available');
  ctx.history.pushState(null, '', '/video/BV1GJ411x7h7/');
  assert.equal(ctx.history.pushes.length, 2, 'different video SPA routes must be blocked');
  assert.ok(doc.getElementById('bilifocus-notice').textContent.includes('关注列表'));
  ctx.BiliFocus.showNotice('<img src=x>');
  assert.equal(doc.getElementById('bilifocus-notice').textContent, '<img src=x>');
  assert.equal(doc.querySelector('#bilifocus-notice img'), null);
  assert.equal(ctx.BiliFocus.status().danmakuHidden, true);
  doc.querySelector('.bilifocus-danmaku').click();
  assert.equal(ctx.BiliFocus.status().danmakuHidden, false);
  assert.equal(doc.documentElement.hasAttribute('data-bilifocus-danmaku-hidden'), false);
  assert.equal(doc.querySelector('.bilifocus-danmaku').textContent, '弹幕：显示');

  const festival = page(fixture, {location:{pathname:'/festival/NobelPrize2026',search:'?bvid=BV1B7411m7LV',href:'https://www.bilibili.com/festival/NobelPrize2026?bvid=BV1B7411m7LV'}});
  assert.equal(festival.ctx.BiliFocus.status().mode, 'focus');
  festival.ctx.history.pushState(null, '', '?bvid=BV1B7411m7LV&p=2');
  assert.equal(festival.ctx.history.pushes.length, 1);
  festival.ctx.history.pushState(null, '', '?bvid=BV1GJ411x7h7');
  festival.ctx.history.pushState(null, '', '/festival/NobelPrize2026');
  assert.equal(festival.ctx.history.pushes.length, 1, 'activity-page recommendations and aggregate routes must be blocked');

  const portal = doc.getElementById('portal');
  assert.ok(portal.hasAttribute('data-bilifocus-hidden'));
  const newDialog = doc.createElement('div');
  newDialog.setAttribute('role', 'dialog');
  newDialog.textContent = '官方验证';
  portal.append(newDialog);
  await p.advance(0);
  await p.advance(200);
  assert.equal(portal.hasAttribute('data-bilifocus-hidden'), false, 'late portal dialogs must become visible');

  doc.title = '<img src=x onerror=alert(1)>';
  ctx.BiliFocus.focus();
  assert.equal(doc.querySelector('.bilifocus-title').textContent, '<img src=x onerror=alert(1)>');
  assert.equal(doc.querySelector('.bilifocus-title img'), null, 'page titles are only text');

  const missing = page('<html><head></head><body><main>platform error</main></body></html>');
  assert.equal(missing.ctx.BiliFocus.status().mode, 'pending');
  await missing.advance(21000);
  await missing.advance(200);
  assert.equal(missing.ctx.BiliFocus.status().mode, 'failed');
  assert.ok(missing.doc.getElementById('bilifocus-wait').textContent.includes('暂未找到'));
  missing.ctx.BiliFocus.full();
  assert.equal(missing.doc.getElementById('bilifocus-wait'), null, 'manual full-page escape remains available');
  console.log('PASS: scope guards, DOM preservation, distraction isolation, dialog preservation, mode toggle, late dialogs, safe text, failure fallback');
}
run().catch(error => {console.error(error);process.exitCode = 1;});
