"""Isolated official Bilibili player backed by the Windows WebView2 runtime.

The remote document has no application API and its native message bridge is
disabled before navigation. Existing, user-authorized login cookies are copied
through CookieManager, never through JavaScript, URLs, or logging.
"""
from __future__ import annotations

import html
import logging
from pathlib import Path
import re
import threading
from urllib.parse import parse_qs, urlparse

import webview
from webview.menu import Menu, MenuAction, MenuSeparator
from native_ui import close_window, show_window, PlayerFullscreen, PlaybackWatcher


BVID = re.compile(r"BV[0-9A-Za-z]{10}")
LOGIN_HOSTS = frozenset({"passport.bilibili.com", "account.bilibili.com"})
LOGIN_COOKIES = frozenset({"SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5", "sid"})
YOUTUBE_ID = re.compile(r'[A-Za-z0-9_-]{11}')
YOUTUBE_LOGIN_HOSTS = frozenset({'accounts.google.com', 'consent.youtube.com', 'consent.google.com'})
WAITING_HTML = """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline' 'unsafe-eval'">
<style>html,body{height:100%;margin:0;background:#101217;color:#dae1ee;font:16px 'Microsoft YaHei UI',sans-serif}body{display:grid;place-items:center}main{text-align:center}p{font-size:13px;color:#a8b3c9;line-height:1.8}</style>
<main><h2>正在准备官方网页播放器</h2><p>保留正常的画质和登录选项，隐藏推荐内容。<br>请稍候…</p></main></html>"""


def video_url(bvid: str, part: int = 1) -> str:
    if not isinstance(bvid, str) or not BVID.fullmatch(bvid):
        raise ValueError("视频编号无效。")
    if isinstance(part, bool) or not isinstance(part, int) or not 1 <= part <= 999:
        raise ValueError("分 P 序号应为 1 至 999 的整数。")
    return f"https://www.bilibili.com/video/{bvid}/?p={part}"


def allowed_navigation(url: str, bvid: str) -> bool:
    """Top-level navigation only; normal subframes/resources are not inspected."""
    try:
        parsed = urlparse(str(url))
        if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443):
            return False
        if parsed.hostname in LOGIN_HOSTS:
            return True
        if parsed.hostname != "www.bilibili.com":
            return False
        if parsed.path.rstrip("/") == f"/video/{bvid}":
            return True
        # Some ordinary video pages officially redirect to an event's player.
        # Accept only a single, explicit selection of this same video; a festival
        # landing page or a link selecting a different BV remains outside scope.
        if re.fullmatch(r"/festival/[A-Za-z0-9_-]+/?", parsed.path):
            return parse_qs(parsed.query, keep_blank_values=True, max_num_fields=64).get("bvid") == [bvid]
        return False
    except (ValueError, TypeError):
        return False


def youtube_url(video_id: str) -> str:
    if not isinstance(video_id, str) or not YOUTUBE_ID.fullmatch(video_id):
        raise ValueError('YouTube 视频编号无效。')
    return f'https://www.youtube.com/watch?v={video_id}'


def allowed_youtube_navigation(url: str, video_id: str) -> bool:
    try:
        p = urlparse(str(url))
        if p.scheme != 'https' or p.username or p.password or p.port not in (None, 443):
            return False
        if p.hostname in YOUTUBE_LOGIN_HOSTS:
            return True
        if p.hostname not in {'www.youtube.com', 'youtube.com', 'm.youtube.com'}:
            return False
        if p.path in {'/signin', '/oops', '/account'}:
            return True
        if p.path.rstrip('/') == '/watch':
            return parse_qs(p.query, max_num_fields=64).get('v') == [video_id]
        return p.path.rstrip('/') in {f'/shorts/{video_id}', f'/live/{video_id}'}
    except (ValueError, TypeError):
        return False


def _sync_cookies(core, values: dict) -> int:
    """Must be called on the WebView2 UI thread. No secret leaves this function."""
    if not isinstance(values, dict):
        raise ValueError("登录信息格式无效。")
    count = 0
    for name in LOGIN_COOKIES:
        value = values.get(name)
        if not isinstance(value, str) or not value or len(value) > 8192:
            continue
        cookie = core.CookieManager.CreateCookie(name, value, ".bilibili.com", "/")
        cookie.IsSecure = True
        cookie.IsHttpOnly = name in {"SESSDATA", "sid"}
        core.CookieManager.AddOrUpdateCookie(cookie)
        count += 1
    return count


def _delete_login_cookies(core) -> None:
    # Do not clear unrelated browser/profile data or cookies for other services.
    for name in LOGIN_COOKIES:
        core.CookieManager.DeleteCookies(name, ".bilibili.com", "/")
        core.CookieManager.DeleteCookies(name, "www.bilibili.com", "/")


class FocusPlayer:
    def __init__(self, assets_path: Path, cookie_provider, on_return, platform='bilibili', can_open=None,
                 playback_config=None, on_playback=None, on_closed=None):
        if platform not in {'bilibili', 'youtube'}:
            raise ValueError('不支持的播放平台。')
        self.platform = platform
        self.assets_path = Path(assets_path)
        self.cookie_provider = cookie_provider
        self.on_return = on_return
        self.can_open = can_open or (lambda: True)
        self.playback_config = playback_config
        self.on_playback, self.on_closed = on_playback, on_closed
        self._playback_context = None
        self._navigation_pending = False
        self._lock = threading.RLock()
        self._open_lock = threading.Lock()
        self._generation = 0
        self._window = None
        self._target = None
        self._native_ready = False
        self._pending_clear = False
        self._native_handlers = []
        self._fullscreen = None
        self.ready = threading.Event()
        self.last_warning = ""
        self.last_error = ""
        self.last_blocked_route = ""
        self.last_blocked_video_code = ""
        self.last_navigation = {}

    def _menu(self):
        return [
            Menu("播放", [
                MenuAction("返回关注列表", self._return),
                MenuAction("切换专注 / 完整页面", self._toggle),
                MenuAction("全屏 / 退出全屏 (F11)", self._toggle_fullscreen),
                MenuAction("刷新当前视频", self._refresh),
                MenuSeparator(),
                MenuAction("关闭播放窗口", self.close),
            ])
        ]

    def open(self, bvid: str, title: str = "", part: int = 1, playback=None) -> None:
        url = youtube_url(bvid) if self.platform == 'youtube' else video_url(bvid, part)
        script = (self.assets_path / ('youtube_focus.js' if self.platform == 'youtube' else 'focus_mode.js')).read_text(encoding="utf-8")
        if not script.strip():
            raise ValueError("专注播放资源缺失，请重新安装程序。")
        if self.playback_config:
            script += '\n' + (self.assets_path / 'playback_queue.js').read_text(encoding='utf-8')
        display_title = str(title or "B站视频").replace("\r", " ").replace("\n", " ")[:100]
        with self._lock:
            requested_generation = self._generation
        # Serialize open requests without holding the state lock over a native
        # GUI call. Navigation/close callbacks on that GUI thread need the same
        # state lock, and waiting on it during create_window can deadlock.
        with self._open_lock:
            with self._lock:
                allowed = self.can_open() and (not playback or not self.playback_config or bool(self.playback_config(playback)))
                if requested_generation != self._generation or not allowed:
                    return
                generation = self._generation
                self._target = (bvid, url, display_title)
                self._playback_context = dict(playback) if playback else None
                self._navigation_pending = True
                window = self._window
            if window is None:
                window = webview.create_window(
                    "官方网页专注播放 · " + display_title,
                    html=WAITING_HTML, js_api=None,
                    width=1280, height=850, min_size=(960, 620),
                    background_color="#101217", menu=self._menu(),
                )
                if window is None:
                    raise RuntimeError("无法创建播放窗口。")
                with self._lock:
                    allowed = self.can_open() and (not playback or not self.playback_config or bool(self.playback_config(playback)))
                    cancelled = generation != self._generation or not allowed
                    if not cancelled:
                        self.ready.clear()
                        self.last_error = ""
                        self.last_warning = ""
                        self._native_ready = False
                        self._window = window
                if cancelled:
                    close_window(window)
                    return
                window.events.closed += lambda: self._on_closed(window)
                threading.Thread(target=self._initialize, args=(window, script), daemon=True,
                                 name="bili-focus-setup").start()
            with self._lock:
                navigate_now = self._window is window and self._native_ready
            if navigate_now:
                self._navigate(window, url, display_title)
            show_window(window)

    @staticmethod
    def _ui(window, callback):
        """Invoke on WinForms STA; never wait for an async WebView task there."""
        from System import Func, Object
        form = window.native
        if form is None or form.IsDisposed:
            raise RuntimeError("播放窗口已经关闭。")
        if form.InvokeRequired:
            return form.Invoke(Func[Object](callback))
        return callback()

    def _is_current(self, window):
        with self._lock:
            return self._window is window

    def _initialize(self, window, script):
        try:
            if not window.events.loaded.wait(25):
                raise TimeoutError("本地播放窗口初始化超时。")
            if not self._is_current(window):
                return
            task = self._ui(window, lambda: self._install_native(window, script))
            # The browser completes this on its STA. Waiting happens on this worker.
            if not task.Wait(15000):
                raise TimeoutError("专注模式初始化超时。")
            task.GetAwaiter().GetResult()
            if not self._is_current(window):
                return
            self._copy_authorized_login(window)
            with self._lock:
                if self._window is not window:
                    return
                self._native_ready = True
                _, url, title = self._target
                self.ready.set()
            self._navigate(window, url, title, copy_login=False)
        except Exception:
            # .NET exceptions can carry source or URLs; never include them in logs.
            logging.error("Official focus player initialization failed")
            self.last_error = "专注播放器未能初始化。请关闭此窗口后重试，或检查 WebView2 运行时。"
            self._show_failure(window)

    def _install_native(self, window, script):
        browser = window.native.browser
        view = browser.webview
        core = view.CoreWebView2
        if core is None:
            raise RuntimeError("WebView2 尚未准备好。")
        # No Python bridge, file drops, or implicit external browser opening on
        # the remote document, even though the local app uses those facilities.
        view.WebMessageReceived -= browser.on_script_notify
        view.NavigationCompleted -= browser.on_navigation_completed
        core.NewWindowRequested -= browser.on_new_window_request
        core.Settings.IsWebMessageEnabled = False
        core.Settings.AreDefaultContextMenusEnabled = False
        core.Settings.AreDevToolsEnabled = False
        fullscreen = PlayerFullscreen(window, self._is_current)
        fullscreen.install(window.native, core)
        self._fullscreen = fullscreen
        watcher = None
        if self.playback_config and self.on_playback:
            def config():
                with self._lock:
                    context = self._playback_context
                    target = self._target
                    pending = self._navigation_pending
                if pending or not context or not target or not self.can_open():
                    return None
                result = self.playback_config(context)
                return dict(result, code=target[0]) if result else None
            watcher = PlaybackWatcher(window, self._is_current, config, self.on_playback)
            watcher.install(window.native, core)

        def navigation_start(sender, args):
            if not self._is_current(window):
                args.Cancel = True
                return
            with self._lock:
                bvid = self._target[0] if self._target else ""
                local_setup = not self._native_ready
            if local_setup and str(args.Uri) == "about:blank":
                return
            if not self._allowed(str(args.Uri), bvid):
                args.Cancel = True
                self._blocked_notice(core, str(args.Uri))

        def popup(sender, args):
            args.Handled = True
            if not self._is_current(window):
                return
            with self._lock:
                bvid = self._target[0] if self._target else ""
            url = str(args.Uri)
            # An explicit official login action can reuse this isolated window.
            # Other popups (including recommendations/external schemes) are stopped.
            login_hosts = YOUTUBE_LOGIN_HOSTS if self.platform == 'youtube' else LOGIN_HOSTS
            if self._allowed(url, bvid) and urlparse(url).hostname in login_hosts:
                core.Navigate(url)
            else:
                self._blocked_notice(core, url)

        def complete(sender, args):
            # run_js can be used without creating any JS-to-Python bridge.
            self.last_navigation = {
                "success": bool(args.IsSuccess),
                "http_status": int(getattr(args, "HttpStatusCode", 0)),
                "web_error": str(getattr(args, "WebErrorStatus", "")),
            }
            window.events.before_load.set()
            window.events.loaded.set()
            with self._lock:
                if self._window is window:
                    self._navigation_pending = False
            fullscreen.sync_document(core)
            fullscreen.preserve_navigation = False

        def source_changed(sender, args):
            if not self._is_current(window):
                return
            # SPA history updates do not necessarily emit NavigationStarting.
            # Do not let a site-side "next video" silently leave this selection.
            with self._lock:
                target = self._target
                ready = self._native_ready
            if ready and target and not self._allowed(str(core.Source), target[0]):
                self._blocked_notice(core, str(core.Source))
                core.Navigate(target[1])

        view.NavigationStarting += navigation_start
        view.NavigationCompleted += complete
        core.NewWindowRequested += popup
        core.SourceChanged += source_changed
        self._native_handlers = [navigation_start, popup, complete, source_changed, fullscreen, watcher]
        return core.AddScriptToExecuteOnDocumentCreatedAsync(script)

    def _copy_authorized_login(self, window):
        if self.platform == 'youtube':
            return  # Google login stays in its official website/profile only.
        try:
            values = self.cookie_provider() or {}
            def update():
                core = window.native.browser.webview.CoreWebView2
                if self._pending_clear:
                    _delete_login_cookies(core)
                    self._pending_clear = False
                return _sync_cookies(core, values)
            self._ui(window, update)
        except Exception:
            self.last_warning = "监测登录暂未同步到播放器，可在官方页面自行登录。"
            logging.warning("Official focus player login synchronization unavailable")

    def _allowed(self, url, code):
        return allowed_youtube_navigation(url, code) if self.platform == 'youtube' else allowed_navigation(url, code)

    def _navigate(self, window, url, title, copy_login=True):
        if not self._is_current(window):
            return
        if copy_login:
            self._copy_authorized_login(window)
        self._navigation_pending = True
        if self._fullscreen:
            self._fullscreen.preserve_navigation = self._fullscreen.active
        window.set_title("官方网页专注播放 · " + title)
        window.load_url(url)

    def _blocked_notice(self, core, url=""):
        self.last_warning = "已留在当前视频；请返回关注列表选择其他视频。"
        self.last_blocked_video_code = ""
        try:
            route = urlparse(url)
            self.last_blocked_route = f"{route.scheme}://{route.hostname or ''}{route.path}"[:300]
            codes = parse_qs(route.query, max_num_fields=64).get("bvid", [])
            if len(codes) == 1 and BVID.fullmatch(codes[0]):
                self.last_blocked_video_code = codes[0]
        except (TypeError, ValueError):
            self.last_blocked_route = "invalid-route"
        # Constant text only; never include the blocked URL or its query values.
        core.ExecuteScriptAsync(
            "if(window.BiliFocus&&window.BiliFocus.showNotice)"
            "window.BiliFocus.showNotice('请返回关注列表选择其他视频');"
        )

    def _show_failure(self, window):
        if self._is_current(window):
            try:
                window.load_html(WAITING_HTML.replace(
                    "正在准备官方网页播放器", "暂时无法打开专注播放器"
                ).replace("保留正常的画质和登录选项，隐藏推荐内容。<br>请稍候…",
                          html.escape(self.last_error)))
            except Exception:
                pass

    def _toggle(self):
        with self._lock:
            window = self._window
        if window and self._native_ready:
            self._ui(window, lambda: window.native.browser.webview.CoreWebView2.ExecuteScriptAsync(
                "if(window.BiliFocus&&window.BiliFocus.toggle)window.BiliFocus.toggle();"
            ))

    def _toggle_fullscreen(self):
        if self._fullscreen:
            self._fullscreen.toggle()

    def _refresh(self):
        with self._lock:
            window, target = self._window, self._target
        if window and self._native_ready and target:
            self._navigate(window, target[1], target[2])

    def clear_login(self) -> None:
        """Clear only synchronized Bilibili login cookies, including next open."""
        with self._lock:
            self._pending_clear = True
            window, target, ready = self._window, self._target, self._native_ready
        profile_window = window if window and ready else next((
            candidate for candidate in webview.windows
            if candidate.native is not None
            and getattr(candidate.gui, "renderer", None) == "edgechromium"
        ), None)
        if profile_window:
            try:
                self._ui(profile_window, lambda: _delete_login_cookies(profile_window.native.browser.webview.CoreWebView2))
                self._pending_clear = False
                if window and ready and target:
                    self._navigate(window, target[1], target[2], copy_login=False)
            except Exception:
                self.last_warning = "播放器登录清理暂未完成，关闭后重新打开会重试。"

    def _return(self):
        self.close()
        self.on_return()

    def _on_closed(self, window):
        context = None
        with self._lock:
            if self._window is window:
                context = self._playback_context
                self._generation += 1
                self._window = None
                self._native_ready = False
                self.ready.clear()
                self._native_handlers = []
                self._fullscreen = None
                self._playback_context = None
        if context and self.on_closed:
            self.on_closed(context)

    def close(self) -> None:
        with self._lock:
            self._generation += 1
            window = self._window
            context = self._playback_context
            self._window = None
            self._native_ready = False
            self._fullscreen = None
            self._native_handlers = []
            self._playback_context = None
            self.ready.clear()
        if context and self.on_closed:
            self.on_closed(context)
        if window:
            close_window(window)
