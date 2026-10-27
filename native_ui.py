"""Non-blocking WinForms dispatch for lifecycle operations.

Never wait for JavaScript or a GUI-thread callback from a closing handler.
The application uses Windows WebView2; all delegates below are posted rather
than synchronously invoked, including acknowledgements from its pinned bridge.
"""
from __future__ import annotations

import logging
import json
import threading


def post_window(window, action) -> bool:
    if window is None:
        return False
    try:
        form = window.native
        if form is None or form.IsDisposed:
            return False
        from System import Action

        def invoke():
            try:
                if not form.IsDisposed:
                    action(form)
            except Exception as exc:
                # Closing/disposed windows are expected during a shutdown race.
                logging.debug('Deferred UI operation skipped (%s)', type(exc).__name__)

        form.BeginInvoke(Action(invoke))
        return True
    except Exception as exc:
        logging.debug('Window dispatch unavailable (%s)', type(exc).__name__)
        return False


def post_js(window, script: str) -> bool:
    def execute(form):
        core = form.browser.webview.CoreWebView2
        if core is not None:
            core.ExecuteScriptAsync(script)
    return post_window(window, execute)


def close_window(window) -> bool:
    return post_window(window, lambda form: form.Close())


def show_window(window) -> bool:
    def show(form):
        from System.Windows.Forms import FormWindowState
        if form.WindowState == FormWindowState.Minimized:
            form.WindowState = FormWindowState.Normal
        form.Show()
        form.Activate()
    return post_window(window, show)


class PlaybackWatcher:
    """Read a bounded playback sample asynchronously; expose no page RPC."""
    def __init__(self, window, is_current, config, on_signal):
        self.window, self.is_current = window, is_current
        self.config, self.on_signal = config, on_signal
        self.pending = False
        self.token, self.action_seq, self.ended_sent = '', 0, False
        self.timer = None
        self.handlers = []

    def install(self, form, core):
        from System import Action, String
        from System.Threading.Tasks import Task
        from System.Windows.Forms import Timer
        timer = Timer()
        timer.Interval = 500
        self.timer = timer

        def signal(config, kind, data):
            threading.Thread(target=self.on_signal, args=(config, kind, data),
                             name='followed-playback', daemon=True).start()

        def tick(sender, args):
            if self.pending or not self.is_current(self.window):
                return
            config = self.config()
            if not config:
                return
            if self.token != config['token']:
                self.token, self.action_seq, self.ended_sent = config['token'], 0, False
            script = 'window.CustomFollowPlayback?window.CustomFollowPlayback.observe(' + json.dumps(config, ensure_ascii=True) + '):null'
            self.pending = True
            def complete(task):
                self.pending = False
                try:
                    current = self.config() if self.is_current(self.window) else None
                    if not current or current['token'] != config['token']:
                        return
                    sample = json.loads(str(task.Result))
                    if not isinstance(sample, dict) or sample.get('token') != config['token'] or sample.get('code') != config['code']:
                        return
                    if sample.get('unexpected'):
                        core.ExecuteScriptAsync("window.BiliFocus?.showNotice('已停止清单外的视频，请返回关注列表选择作品。')")
                        return
                    action = sample.get('action')
                    if isinstance(action, dict) and type(action.get('seq')) is int and action['seq'] > self.action_seq:
                        self.action_seq = action['seq']
                        if action.get('name') in ('autoplay', 'next'):
                            signal(config, action['name'], action)
                        return
                    if sample.get('ended') is True and current['autoplay'] and not self.ended_sent:
                        self.ended_sent = True
                        signal(config, 'ended', sample)
                except Exception as exc:
                    logging.debug('Playback sample unavailable (%s)', type(exc).__name__)
            try:
                core.ExecuteScriptAsync(script).ContinueWith(Action[Task[String]](complete), form.browser.syncContextTaskScheduler)
            except Exception:
                self.pending = False

        def closed(sender, args):
            timer.Stop()
            timer.Dispose()
        timer.Tick += tick
        form.FormClosed += closed
        self.handlers = [tick, closed]
        timer.Start()


class PlayerFullscreen:
    """Pair WebView2 fullscreen with the actual borderless Windows window.

    Remote pages keep their Python/message bridge disabled. Native browser
    events and accelerator keys are the only inputs to this controller.
    """
    def __init__(self, window, is_current):
        self.window = window
        self.is_current = is_current
        self.active = False
        self._menus = []
        self._previous_state = None
        self._held_keys = set()
        self.preserve_navigation = False
        self.handlers = []

    def _apply(self, form, enabled):
        if not self.is_current(self.window):
            return
        core = form.browser.webview.CoreWebView2
        if not enabled:
            self.preserve_navigation = False
        if not enabled and core.ContainsFullScreenElement:
            core.ExecuteScriptAsync('if(document.fullscreenElement)document.exitFullscreen().catch(()=>{});')
        if self.active != enabled:
            if enabled:
                self._previous_state = form.WindowState
                self._menus = [(c, bool(c.Visible)) for c in form.Controls if c.GetType().Name == 'MenuStrip']
                for menu, _ in self._menus:
                    menu.Visible = False
            if bool(form.is_fullscreen) != enabled:
                form.toggle_fullscreen()
            if not enabled:
                for menu, visible in self._menus:
                    menu.Visible = visible
                if self._previous_state is not None:
                    form.WindowState = self._previous_state
                self._menus = []
            self.active = enabled
        self.sync_document(core)

    def sync_document(self, core):
        value = 'true' if self.active else 'false'
        core.ExecuteScriptAsync('if(window.BiliFocus&&window.BiliFocus.setHostFullscreen)'
                                f'window.BiliFocus.setHostFullscreen({value});')

    def set(self, enabled):
        return post_window(self.window, lambda form: self._apply(form, bool(enabled)))

    def toggle(self):
        return post_window(self.window, lambda form: self._apply(form, not self.active))

    def handle_key(self, key, repeated=False):
        if key == 122:  # F11, including when the webpage consumes keyboard input.
            if not repeated:
                self.toggle()
            return True
        if key == 27 and self.active:
            self.set(False)
            return True
        return False

    def install(self, form, core):
        def browser_fullscreen(sender, args):
            if not core.ContainsFullScreenElement and self.preserve_navigation:
                return
            self.set(bool(core.ContainsFullScreenElement))

        def form_key(sender, args):
            if args.Handled or args.Control or args.Alt:
                return
            key = int(args.KeyCode)
            repeated = key in self._held_keys
            self._held_keys.add(key)
            if self.handle_key(key, repeated):
                args.Handled = True

        def key_up(sender, args):
            self._held_keys.discard(int(args.KeyCode))

        def deactivate(sender, args):
            self._held_keys.clear()

        form.KeyPreview = True
        form.KeyDown += form_key
        form.KeyUp += key_up
        form.Deactivate += deactivate
        core.ContainsFullScreenElementChanged += browser_fullscreen
        form.browser.webview.KeyDown += form_key
        form.browser.webview.KeyUp += key_up
        self.handlers = [browser_fullscreen, form_key, key_up, deactivate]


def install_bridge_delivery(window) -> None:
    """Don't block a bridge thread delivering a return value during shutdown.

    pywebview 6.2 routes its RPC acknowledgements through synchronous evaluate_js,
    even though it ignores the result. Dispatch those acknowledgements without
    waiting; expressions whose Python result is actually needed keep the SDK's
    normal implementation. A closed renderer can no longer strand an RPC thread.
    """
    if getattr(window, '_custom_follow_bridge_delivery', False):
        return
    original = window.evaluate_js

    def evaluate(script, callback=None):
        if callback is None and isinstance(script, str) and script.startswith('window.pywebview._returnValuesCallbacks['):
            post_js(window, script)
            return None
        return original(script, callback)

    window.evaluate_js = evaluate
    window._custom_follow_bridge_delivery = True
