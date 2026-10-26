"""Local, focused Bilibili subscriptions with an official embedded player."""
from __future__ import annotations

import base64
import ctypes
import io
import json
import logging
import math
from logging.handlers import RotatingFileHandler
import os
import queue
from pathlib import Path
import re
import sys
import threading
import time
import webbrowser

import qrcode
import webview

from bili_api import BiliClient, BiliError
from state import Store, parse_uid, is_quiet
from windows_support import (Tray, acquire_instance, release_instance, save_session,
                             load_session, set_autostart, get_autostart)
from focus_player import FocusPlayer
from youtube_api import YouTubeClient, YouTubeError, youtube_video_id

ROOT = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
ASSETS = Path(getattr(sys, '_MEIPASS', Path(__file__).parent)) / 'assets'
LEGACY_DATA = ROOT.parent / 'BiliFocus' / 'data'
DEFAULT_DATA = LEGACY_DATA if getattr(sys, 'frozen', False) and ROOT.name != 'BiliFocus' and (LEGACY_DATA / 'state.json').is_file() else ROOT / 'data'
DATA = Path(os.environ.get('BILI_REMINDER_DATA', str(DEFAULT_DATA)))
BV_RE = re.compile(r'^BV[0-9A-Za-z]{10}$')


def video_bvid(video):
    key = str(video.get('key', ''))
    if BV_RE.fullmatch(key):
        return key
    match = re.search(r'/video/(BV[0-9A-Za-z]{10})(?:[/??#]|$)', str(video.get('url', '')))
    return match.group(1) if match else None


class Application:
    def __init__(self):
        DATA.mkdir(parents=True, exist_ok=True)
        self.store = Store(DATA / 'state.json')
        self.warning = ''
        try:
            cookies = load_session(DATA / 'session.bin')
        except Exception as exc:
            cookies = {}
            self.warning = str(exc)
        self.client = BiliClient(cookies=cookies)
        self.youtube = YouTubeClient()
        self.account = '已保存登录，正在验证' if cookies else ''
        self.account_valid = False
        self.window = None
        self.toast = None
        self.tray = None
        self.tray_ok = False
        self.quitting = False
        self.stopping = threading.Event()
        self.wake = threading.Event()
        self.ready = threading.Event()
        self.toast_ready = threading.Event()
        self.toast_version = 0
        self.toast_target = None
        self.checking = ''
        self.qr_client = None
        self.qr_key = None
        self.login_lock = threading.Lock()
        self.notifications = queue.Queue()
        self.notification_lock = threading.RLock()
        self.search_lock = threading.Lock()
        self.last_search = 0
        self.focus_player = FocusPlayer(ASSETS, self.playback_cookies, self.return_from_player)
        self.youtube_player = FocusPlayer(ASSETS, lambda: {}, self.return_from_player, platform='youtube')

    def playback_cookies(self):
        return load_session(DATA / 'session.bin')

    def return_from_player(self):
        self.show()


    def snapshot(self):
        data = self.store.snapshot()
        data['runtime'] = {'account': self.account, 'account_valid': self.account_valid,
                           'warning': self.warning, 'checking': self.checking,
                           'tray': self.tray_ok, 'autostart': get_autostart()}
        return data

    def emit(self, method, value):
        if self.ready.is_set() and not self.quitting:
            try:
                self.window.evaluate_js(f'window.{method}({json.dumps(value, ensure_ascii=True)})')
            except Exception:
                logging.warning('UI refresh unavailable')

    def changed(self):
        self.emit('receiveState', self.snapshot())

    def show(self):
        if self.window:
            self.window.show()
            self.window.restore()

    def close(self):
        if self.quitting:
            return True
        if self.tray_ok:
            self.emit('stopPlayer', None)
            self.focus_player.close()
            self.youtube_player.close()
            self.window.hide()
            return False
        self.quit()
        return True

    def quit(self):
        self.quitting = True
        self.stopping.set()
        self.wake.set()
        self.focus_player.close()
        self.youtube_player.close()
        if self.tray:
            self.tray.stop()
        if self.toast:
            self.toast.destroy()
        if self.window:
            self.window.destroy()

    def request_check(self):
        if self.store.snapshot()['settings']['paused']:
            return False
        accepted = self.store.request_check()
        if accepted:
            self.wake.set()
        return accepted

    def worker(self):
        try:
            initial_client = self.client
            name = initial_client.check_login()
            if initial_client is self.client:
                self.account = name or ''
                self.account_valid = bool(name)
                self.changed()
        except BiliError:
            if self.account:
                self.warning = '登录状态暂时无法验证；查询遇到限制时请重新扫码。'
        while not self.stopping.is_set():
            try:
                channels = self.store.due_channels()
                for channel in channels:
                    if self.stopping.is_set():
                        break
                    # Re-read after a manual pause/removal while an earlier request was running.
                    if not any(c['uid'] == channel['uid'] for c in self.store.due_channels()):
                        continue
                    self.checking = channel['name'] or channel['uid']
                    self.changed()
                    try:
                        since = channel.get('last_success_at') if channel.get('initialized') else None
                        active_client = self.youtube if channel.get('platform') == 'youtube' else self.client
                        cid = channel.get('channel_id', channel['uid'])
                        feed = active_client.fetch_videos(cid, since_ts=since)
                        if self.stopping.is_set():
                            break
                        if channel.get('platform') != 'youtube' and active_client is not self.client:
                            continue
                        news = self.store.ingest(channel['uid'], feed)
                        if getattr(feed, 'has_more', False) and channel.get('initialized'):
                            self.warning = '离线期间更新较多，本次仅检查了有限页数；更早投稿可在 博主主页查看。'
                        if news:
                            settings = self.store.snapshot()['settings']
                            if settings['popups'] and not is_quiet(settings):
                                self.notifications.put(news)
                    except BiliError as exc:
                        if not self.stopping.is_set() and (active_client is self.client or active_client is self.youtube):
                            self.store.record_error(channel['uid'], str(exc))
                    except Exception:
                        logging.exception('Checking channel failed')
                        self.store.record_error(channel['uid'], '检查未完成，请稍后重试；详细信息已记录在本地日志。')
                    finally:
                        self.checking = ''
                        self.changed()
                    if self.stopping.wait(2):
                        break
            except Exception:
                logging.exception('Monitor loop failed')
                self.warning = '本地数据写入或检查出现问题，请查看文件夹中的日志。'
                self.changed()
            self.wake.wait(3)
            self.wake.clear()

    def notification_worker(self):
        while not self.stopping.is_set():
            try:
                news = self.notifications.get(timeout=1)
            except queue.Empty:
                continue
            current = self.store.snapshot()
            settings = current['settings']
            allowed = {c['uid'] for c in current['channels'] if c['enabled']}
            retained = {v['key'] for v in current['videos']}
            news = [v for v in news if v['uid'] in allowed and v['key'] in retained]
            if not news:
                continue
            if settings['popups'] and not settings['paused'] and not is_quiet(settings):
                try:
                    self.notify(news)
                except Exception:
                    logging.exception('Notification failed')
                    self.warning = '弹窗暂未显示，更新已保存在视频列表。'
                    self.changed()
                self.stopping.wait(15)

    def notify(self, videos=None, test=False):
        if self.stopping.is_set():
            return
        if not self.toast_ready.wait(8):
            self.warning = '弹窗尚未就绪；更新仍保存在视频列表中。'
            return
        if test:
            title, body, key = '新视频通知预览', '你关注的博主有新视频时，可从这里打开定制关注。', None
        else:
            first = videos[0]
            title = f"{first['author']} 更新了" + (f" {len(videos)} 个视频" if len(videos) > 1 else '新视频')
            body, key = first['title'], first['key'] if len(videos) == 1 else None
        with self.notification_lock:
            self.toast_version += 1
            version = self.toast_version
            self.toast_target = key
            self.toast.run_js(f'window.setNotification({json.dumps(dict(title=title, body=body), ensure_ascii=True)})')
            self.toast.show()
        if self.store.snapshot()['settings']['sound']:
            import winsound
            winsound.MessageBeep(winsound.MB_OK)
        def hide_later():
            if not self.stopping.wait(14):
                with self.notification_lock:
                    if version == self.toast_version:
                        self.toast.hide()
        threading.Thread(target=hide_later, daemon=True).start()

    def boot(self):
        if getattr(sys, 'frozen', False) and get_autostart():
            try:
                set_autostart(True)
            except OSError:
                self.warning = '开机启动路径未能更新，请在设置中重新保存。'
        self.tray = Tray(self.show, self.request_check, self.quit)
        self.tray_ok = self.tray.start()
        if not self.tray_ok:
            self.warning = self.tray.last_error or '托盘不可用，请保持主窗口开启。'
            self.show()
        self.changed()
        threading.Thread(target=self.worker, name='bili-monitor', daemon=True).start()
        threading.Thread(target=self.notification_worker, name='bili-notifications', daemon=True).start()


class Api:
    """Explicit bridge methods; no filesystem or arbitrary URL/command access."""
    def __init__(self, app):
        self._app = app

    def snapshot(self):
        return self._app.snapshot()

    def add_channel(self, text, platform='bilibili'):
        try:
            if platform == 'youtube':
                info, feed = self._app.youtube.resolve_channel(text)
                result = self._app.store.add_channel(info['channel_id'], info['name'], 'youtube')
                uid = result['uid']
                self._app.store.ingest(uid, feed)
            elif platform == 'bilibili':
                uid = parse_uid(text)
                result = self._app.store.add_channel(uid)
            else:
                raise ValueError('请选择 B站或 YouTube。')
            self._app.wake.set()
            self._app.changed()
            return {'ok': True, 'uid': uid, 'result': result}
        except (ValueError, OSError, YouTubeError) as exc:
            return {'ok': False, 'error': str(exc)}

    def search_channels(self, keyword):
        text = str(keyword).strip()
        if not text or len(text) > 40 or '://' in text or 'bilibili.com' in text or 'b23.tv' in text:
            return {'ok': False, 'error': '请输入 1～40 个字的 B站博主昵称；主页链接请直接添加。'}
        with self._app.search_lock:
            now = time.monotonic()
            if now - self._app.last_search < 5:
                return {'ok': False, 'error': '请稍等 5 秒再查找。'}
            self._app.last_search = now
            try:
                return {'ok': True, 'users': self._app.client.search_users(text)}
            except BiliError as exc:
                return {'ok': False, 'error': str(exc)}

    def remove_channel(self, uid):
        self._app.store.remove_channel(str(uid))
        self._app.changed()
        return {'ok': True}

    def toggle_channel(self, uid):
        self._app.store.toggle_channel(str(uid))
        self._app.changed()
        self._app.wake.set()
        return {'ok': True}

    def save_settings(self, settings):
        try:
            settings = dict(settings)
            autostart = settings.pop('autostart', None)
            self._app.store.update_settings(settings)
            if autostart is not None:
                set_autostart(bool(autostart))
            self._app.wake.set()
            self._app.changed()
            return {'ok': True}
        except (ValueError, OSError) as exc:
            return {'ok': False, 'error': str(exc)}

    def check_now(self):
        if self._app.store.snapshot()['settings']['paused']:
            return {'ok': False, 'error': '检查已暂停，请先在设置中恢复检查。'}
        accepted = self._app.request_check()
        self._app.changed()
        return {'ok': accepted, 'error': '' if accepted else '请至少间隔 60 秒再检查；暂时查询失败的博主会按计划重试。'}

    def play_video(self, key):
        video = next((v for v in self._app.store.snapshot()['videos'] if v['key'] == key), None)
        if not video:
            return {'ok': False, 'error': '该视频已不在你的关注列表。'}
        bvid = video_bvid(video)
        ytid = youtube_video_id(video)
        if ytid:
            self._app.store.mark_read(key)
            self._app.changed()
            return {'ok': True, 'video': video, 'video_id': ytid, 'platform': 'youtube'}
        if not bvid:
            return {'ok': False, 'error': '此视频暂不支持嵌入播放，可在 B 站打开。'}
        self._app.store.mark_read(key)
        self._app.changed()
        return {'ok': True, 'video': video, 'bvid': bvid}

    def open_external(self, key):
        video = next((v for v in self._app.store.snapshot()['videos'] if v['key'] == key), None)
        if video and video_bvid(video):
            webbrowser.open('https://www.bilibili.com/video/' + video_bvid(video))
        elif video and youtube_video_id(video):
            webbrowser.open('https://www.youtube.com/watch?v=' + youtube_video_id(video))

    def open_focus_video(self, key, part=1):
        video = next((v for v in self._app.store.snapshot()['videos'] if v['key'] == key), None)
        if not video or not (video_bvid(video) or youtube_video_id(video)):
            return {'ok': False, 'error': '该视频已不在你的关注列表，或视频编号无效。'}
        if isinstance(part, bool) or not isinstance(part, (int, float)) or not math.isfinite(part) or int(part) != part or not 1 <= part <= 999:
            return {'ok': False, 'error': '分 P 序号应为 1 至 999 的整数。'}
        try:
            if youtube_video_id(video):
                self._app.focus_player.close()
                self._app.youtube_player.open(youtube_video_id(video), video['title'])
            else:
                self._app.youtube_player.close()
                self._app.focus_player.open(video_bvid(video), video['title'], int(part))
            self._app.store.mark_read(key)
            self._app.changed()
            return {'ok': True, 'video': video, 'bvid': video_bvid(video), 'video_id': youtube_video_id(video), 'platform': video.get('platform', 'bilibili')}
        except Exception:
            logging.exception('Official player could not open')
            return {'ok': False, 'error': '官方播放窗口未能打开，请稍后重试，或切换到嵌入备用模式。'}

    def close_focus_video(self):
        self._app.focus_player.close()
        self._app.youtube_player.close()
        return {'ok': True}

    def open_space(self, uid):
        channel = next((c for c in self._app.store.snapshot()['channels'] if c['uid'] == str(uid)), None)
        if channel:
            if channel.get('platform') == 'youtube':
                webbrowser.open('https://www.youtube.com/channel/' + channel['channel_id'])
            else:
                webbrowser.open('https://space.bilibili.com/' + str(uid))

    def create_qr(self):
        with self._app.login_lock:
            try:
                client = BiliClient()
                result = client.create_qr()
                self._app.qr_client, self._app.qr_key = client, result['qrcode_key']
                buffer = io.BytesIO()
                qrcode.make(result['url']).save(buffer, format='PNG')
                return {'ok': True, 'image': 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode()}
            except BiliError as exc:
                return {'ok': False, 'error': str(exc)}

    def poll_qr(self):
        with self._app.login_lock:
            if not self._app.qr_key or not self._app.qr_client:
                return {'status': 'expired'}
            try:
                result = self._app.qr_client.poll_qr(self._app.qr_key)
                if result['status'] == 'success':
                    cookies = result['cookies']
                    save_session(DATA / 'session.bin', cookies)
                    client = BiliClient(cookies=cookies)
                    try:
                        name = client.check_login() or 'B 站用户'
                    except BiliError:
                        name = 'B 站用户'
                    self._app.client = client
                    self._app.account, self._app.account_valid = name, True
                    self._app.warning = ''
                    self._app.qr_key = None
                    self._app.store.request_check(reset_errors=True)
                    self._app.wake.set()
                    self._app.changed()
                    return {'status': 'success'}
                return {'status': result['status']}
            except Exception as exc:
                return {'status': 'error', 'error': str(exc) if isinstance(exc, (BiliError, OSError, RuntimeError)) else '登录未完成，请重新生成二维码。'}

    def logout(self):
        with self._app.login_lock:
            self._app.focus_player.clear_login()
            save_session(DATA / 'session.bin', {})
            self._app.client = BiliClient()
            self._app.account, self._app.account_valid = '', False
            self._app.qr_key = None
            self._app.changed()
        return {'ok': True}

    def test_notification(self):
        self._app.notify(test=True)
        return {'ok': True}

    def hide_window(self):
        if self._app.tray_ok:
            self._app.emit('stopPlayer', None)
            self._app.focus_player.close()
            self._app.youtube_player.close()
            self._app.window.hide()
            return {'ok': True}
        return {'ok': False, 'error': '托盘不可用，请保留窗口运行。'}

    def quit(self):
        threading.Thread(target=self._app.quit, daemon=True).start()


class NotificationApi:
    def __init__(self, app):
        self._app = app

    def open(self):
        with self._app.notification_lock:
            key = self._app.toast_target
            self._app.toast.hide()
        self._app.show()
        if key:
            self._app.emit('openVideo', key)

    def dismiss(self):
        self._app.toast.hide()


def run():
    if not acquire_instance():
        ctypes.windll.user32.MessageBoxW(None, '程序已经在运行。请点击右下角托盘中的粉色电视图标打开。', '定制关注', 64)
        return
    try:
        DATA.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(DATA / 'app.log', maxBytes=500_000, backupCount=2, encoding='utf-8')
        logging.basicConfig(level=logging.WARNING, handlers=[handler])
        app = Application()
        app.window = webview.create_window('定制关注', str(ASSETS / 'index.html'), js_api=Api(app),
                                          width=1220, height=820, min_size=(960, 660),
                                          hidden='--background' in sys.argv, background_color='#101217')
        app.window.events.loaded += app.ready.set
        app.window.events.closing += app.close
        class Rect(ctypes.Structure):
            _fields_ = [('left', ctypes.c_long), ('top', ctypes.c_long), ('right', ctypes.c_long), ('bottom', ctypes.c_long)]
        rect = Rect()
        ctypes.windll.user32.SystemParametersInfoW(48, 0, ctypes.byref(rect), 0)
        scale = ctypes.windll.user32.GetDpiForSystem() / 96.0
        app.toast = webview.create_window('定制关注 · 新视频', str(ASSETS / 'notification.html'), js_api=NotificationApi(app),
                                         width=400, height=190, x=int(rect.right / scale)-422, y=int(rect.bottom / scale)-212,
                                         frameless=True, resizable=False, on_top=True, hidden=True, focus=False,
                                         background_color='#191c24')
        app.toast.events.loaded += app.toast_ready.set
        webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER'] = True
        webview.start(app.boot, gui='edgechromium', debug=False, private_mode=False,
                      storage_path=str(DATA / 'webview'), icon=str(ASSETS / 'app.ico'))
    except Exception:
        logging.exception('Application startup failed')
        ctypes.windll.user32.MessageBoxW(None, '程序未能启动。请确认已安装 Microsoft Edge WebView2 运行时，并查看 data/app.log。', '定制关注 · 启动提示', 16)
    finally:
        release_instance()


if __name__ == '__main__':
    run()
