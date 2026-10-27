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
from datetime import datetime

import qrcode
import webview

from bili_api import BiliClient, BiliError
from state import Store, parse_uid, is_quiet
from windows_support import (Tray, acquire_instance, release_instance, save_session,
                             load_session, set_autostart, get_autostart)
from focus_player import FocusPlayer
from youtube_api import YouTubeClient, YouTubeError, youtube_video_id
from native_ui import post_window, post_js, close_window, show_window, install_bridge_delivery
from playback_queue import FollowedQueue, PlayTicket, owned_videos
from playback_schedule import unwatched_order, next_due

ROOT = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
ASSETS = Path(getattr(sys, '_MEIPASS', Path(__file__).parent)) / 'assets'
LEGACY_DATA = ROOT.parent / 'BiliFocus' / 'data'
DEFAULT_DATA = LEGACY_DATA if getattr(sys, 'frozen', False) and ROOT.name != 'BiliFocus' and (LEGACY_DATA / 'state.json').is_file() else ROOT / 'data'
DATA = Path(os.environ.get('BILI_REMINDER_DATA', str(DEFAULT_DATA)))
BV_RE = re.compile(r'^BV[0-9A-Za-z]{10}$')


class AppClosing(BiliError):
    def __init__(self):
        super().__init__('程序正在关闭，本次操作已取消。', False)


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
        self.youtube = YouTubeClient(known_videos=self.store.snapshot()['videos'])
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
        self.lifecycle_lock = threading.Lock()
        self.playback_allowed = '--background' not in sys.argv
        self.play_queue = FollowedQueue(self.store.snapshot)
        self.play_operation = threading.Lock()
        self.autoplay_followed = self.store.snapshot()['settings']['autoplay_followed']
        self.embedded_active = False
        self.schedule_wake = threading.Event()
        self.schedule_starting = None
        self.playback_view = {'revision': -1, 'queue': {'version': '', 'items': [], 'remaining': 0}}
        self.playback_view_lock = threading.Lock()
        self.play_transition = None
        can_open = lambda: self.playback_allowed and not self.quitting
        playback = dict(playback_config=self.playback_config, on_playback=self.playback_signal, on_closed=self.playback_closed)
        self.focus_player = FocusPlayer(ASSETS, self.playback_cookies, self.return_from_player, can_open=can_open, **playback)
        self.youtube_player = FocusPlayer(ASSETS, lambda: {}, self.return_from_player, platform='youtube', can_open=can_open, **playback)

    def playback_cookies(self):
        return load_session(DATA / 'session.bin')

    def playback_config(self, context):
        ticket = PlayTicket(context['revision'], context['key'])
        if self.quitting or not self.playback_allowed or not self.play_queue.matches(ticket):
            return None
        cached = self.playback_view
        queue = cached['queue'] if cached['revision'] == ticket.revision else {'version': '', 'items': [], 'remaining': 0}
        result = dict(key=ticket.key, revision=ticket.revision, token=f'{ticket.revision}:{ticket.key}',
                      autoplay=self.autoplay_followed, remaining=queue['remaining'], queue=queue, start=True)
        if context.get('transition'):
            result['transition'] = context['transition']
        return result

    def refresh_playback_view(self, ticket=None):
        # Only workers take this lock. The GUI reads the detached cached dict.
        with self.playback_view_lock:
            ticket = ticket or PlayTicket(self.play_queue.revision, self.play_queue.current)
            view = self.play_queue.view(ticket)
            if self.play_queue.matches(ticket):
                self.playback_view = {'revision': ticket.revision, 'queue': view}

    def set_autoplay(self, enabled):
        if type(enabled) is not bool:
            raise ValueError('自动连播开关必须为开或关。')
        self.store.update_settings({'autoplay_followed': enabled})
        self.autoplay_followed = enabled
        self.changed()

    def playback_closed(self, context):
        ticket = PlayTicket(context['revision'], context['key'])
        pending = self.play_transition
        if pending and pending['old'] == ticket and self.play_queue.matches(pending['new']):
            ticket = pending['new']
        if self.play_queue.cancel(ticket):
            self.emit('stopPlayerFromHost', None)

    def play_ticket(self, ticket, part=1, source=None):
        with self.play_operation:
            origin_pending = None
            if source:
                previous_context = source['context']
                origin_pending = {'old': PlayTicket(previous_context['revision'], previous_context['key']), 'new': ticket}
                self.play_transition = origin_pending
            try:
                # A close can happen after queue.move/select but before this
                # worker obtains the operation lock. Register first, then verify
                # the originating native window before touching the store.
                if source and not source['manager'].context_is_current(source['context']):
                    self.play_queue.cancel(ticket)
                    raise AppClosing()
                if not self.play_queue.matches(ticket) or self.quitting or not self.playback_allowed:
                    raise AppClosing()
                video = owned_videos(self.store.snapshot()).get(ticket.key)
                if video is None:
                    self.play_queue.cancel(ticket)
                    raise ValueError('该视频或博主已不在你的关注清单。')
                context = {'key': ticket.key, 'revision': ticket.revision}
                managers = (self.focus_player, self.youtube_player)
                keep_fullscreen = any(m._fullscreen and m._fullscreen.active for m in managers)
                manager = self.youtube_player if youtube_video_id(video) else self.focus_player
                other = self.focus_player if manager is self.youtube_player else self.youtube_player
                self.refresh_playback_view(ticket)
                if not self.play_queue.matches(ticket) or self.quitting or not self.playback_allowed:
                    raise AppClosing()
                previous, previous_context = None, None
                for candidate in managers:
                    old_context = candidate.context_snapshot()
                    if isinstance(old_context, dict) and (old_context['key'], old_context['revision']) != (ticket.key, ticket.revision):
                        previous, previous_context = candidate, old_context
                        break
                pending = None
                try:
                    if previous_context:
                        info = dict(title=video['title'], author=video.get('author', '博主'),
                                    platform=video.get('platform', 'bilibili'), part=int(part))
                        context['transition'] = info
                        pending = {'old': PlayTicket(previous_context['revision'], previous_context['key']), 'new': ticket}
                        self.play_transition = pending
                        if previous.prepare_transition(dict(info, cancellable=False)):
                            if self.stopping.wait(.22) or not self.play_queue.matches(ticket):
                                raise AppClosing()
                    if previous is not other:
                        other.close()
                    if youtube_video_id(video):
                        self.request(manager.open, youtube_video_id(video), video['title'], playback=context)
                    else:
                        self.request(manager.open, video_bvid(video), video['title'], int(part), playback=context)
                    if previous is other:
                        # Keep the old transition card visible until the destination
                        # has a native browser. This wait runs only on a worker.
                        deadline = time.monotonic() + 15
                        while not manager.ready.wait(.05):
                            if (self.stopping.is_set() or not self.play_queue.matches(ticket) or
                                    manager.last_error or time.monotonic() >= deadline):
                                break
                        if self.stopping.is_set() or not self.play_queue.matches(ticket):
                            raise AppClosing()
                        if not manager.ready.is_set() or manager.last_error:
                            self.play_queue.cancel(ticket)
                            manager.close_if_context(context)
                            raise RuntimeError('目标播放窗口未能准备就绪。')
                        if keep_fullscreen and manager._fullscreen and self.play_queue.matches(ticket):
                            manager._fullscreen.set(True)
                finally:
                    if pending is not None and self.play_transition is pending:
                        self.play_transition = None
                    if previous is other:
                        other.close_if_context(previous_context)
                if not self.play_queue.matches(ticket) or self.quitting or not self.playback_allowed:
                    manager.close()
                    raise AppClosing()
                if keep_fullscreen:
                    def restore():
                        if manager.ready.wait(15) and self.play_queue.matches(ticket) and manager._fullscreen:
                            manager._fullscreen.set(True)
                    threading.Thread(target=restore, daemon=True, name='continue-fullscreen').start()
                self.store.mark_read(ticket.key)
                self.changed()
                return {'ok': True, 'video': video, 'bvid': video_bvid(video), 'video_id': youtube_video_id(video),
                        'platform': video.get('platform', 'bilibili'), 'part': int(part), 'order': list(self.play_queue.order), 'revision': ticket.revision}
            finally:
                if origin_pending is not None and self.play_transition is origin_pending:
                    self.play_transition = None

    def playback_signal(self, config, kind, sample):
        ticket = PlayTicket(config['revision'], config['key'])
        if not self.playback_config(config):
            return
        next_ticket = None
        try:
            if kind == 'autoplay':
                self.set_autoplay(sample.get('value'))
                return
            if kind == 'cancel':
                if self.play_queue.cancel(ticket):
                    for manager in (self.focus_player, self.youtube_player):
                        manager.close_if_context(config)
                    self.emit('stopPlayerFromHost', None)
                return
            if kind not in ('next', 'ended', 'select'):
                return
            part, pages = sample.get('part'), sample.get('pages')
            if kind == 'ended':
                final_part = ticket.key.startswith('yt:') or (type(part) is int and type(pages) is int and 1 <= pages <= part <= 999)
                if final_part:
                    self.store.set_watched(ticket.key)
                self.play_queue.mark_finished(ticket)
                self.changed()
                if not self.autoplay_followed:
                    return
            if kind == 'select':
                next_ticket = self.play_queue.select(ticket, sample.get('value'))
                next_part = 1
                if next_ticket is None:
                    self.refresh_playback_view(ticket)
                    return
            elif kind == 'ended' and not ticket.key.startswith('yt:') and type(part) is int and type(pages) is int and 1 <= part < pages <= 999:
                next_ticket = self.play_queue.next_part(ticket)
                next_part = part + 1
            else:
                next_ticket = self.play_queue.move(ticket)
                next_part = 1
            if next_ticket is None:
                self.play_queue.mark_finished(ticket)
                if self.play_queue.matches(ticket):
                    manager = self.youtube_player if ticket.key.startswith('yt:') else self.focus_player
                    post_js(manager._window, "window.BiliFocus?.showNotice('当前关注清单已播完。')")
                return
            source = self.youtube_player if ticket.key.startswith('yt:') else self.focus_player
            result = self.play_ticket(next_ticket, next_part, source={'manager': source, 'context': config})
            if self.play_queue.matches(next_ticket):
                self.emit('playbackAdvanced', dict(result, previous_key=ticket.key))
        except AppClosing:
            pass
        except Exception:
            if next_ticket is not None and self.play_queue.cancel(next_ticket):
                manager = self.youtube_player if next_ticket.key.startswith('yt:') else self.focus_player
                manager.close_if_context({'key': next_ticket.key, 'revision': next_ticket.revision})
            logging.exception('Followed playback could not continue')
            self.emit('playbackError', '下一条视频未能打开，请手动重试；播放清单仍只包含你的关注。')

    def schedule_tick(self, now=None):
        if self.quitting or not self.ready.is_set():
            return None
        lease = self.store.claim_playback_schedule(now)
        if lease is None:
            return None
        ticket = None
        try:
            revision = self.play_queue.revision
            if self.play_queue.is_active() or self.embedded_active:
                message = '正在播放，本次定时任务已跳过。'
            else:
                order = unwatched_order(self.store.snapshot(), lease)
                if not order:
                    message = '没有符合条件的未看视频，本次未播放。'
                elif self.store.snapshot()['play_schedule']['generation'] != lease['generation']:
                    return None
                else:
                    with self.lifecycle_lock:
                        if self.quitting:
                            return None
                        self.playback_allowed = True
                    ticket = self.play_queue.start(order[0], order, unwatched_only=True, if_idle=True, revision=revision)
                    self.schedule_starting = ticket
                    if self.store.snapshot()['play_schedule']['generation'] != lease['generation']:
                        self.play_queue.cancel(ticket)
                        return None
                    result = self.play_ticket(ticket)
                    message = f'已开始播放未看清单，共 {len(order)} 条。'
                    if self.play_queue.matches(ticket):
                        self.emit('playbackStarted', result)
            self.store.finish_playback_schedule(lease, message)
            self.changed()
            return message
        except AppClosing:
            if not self.quitting:
                self.store.finish_playback_schedule(lease, '播放已取消。')
            return None
        except Exception as exc:
            if ticket:
                self.play_queue.cancel(ticket)
            logging.exception('Scheduled playback could not start')
            message = str(exc) if isinstance(exc, ValueError) else '定时播放未能启动，请检查网络后手动重试。'
            self.store.finish_playback_schedule(lease, message)
            self.changed()
            return message
        finally:
            if ticket is not None and self.schedule_starting == ticket:
                self.schedule_starting = None

    def schedule_worker(self):
        while not self.stopping.is_set():
            try:
                self.schedule_tick()
            except Exception:
                logging.exception('Playback appointment check failed')
            self.schedule_wake.wait(1)
            self.schedule_wake.clear()

    def request(self, function, *args, **kwargs):
        """Let an RPC caller stop waiting when the application is closing.

        The bounded network operation runs on a daemon; it never commits state.
        Commit steps stay in the RPC caller after this method returns, so a late
        response cannot modify data after cancellation or keep Python alive.
        """
        if self.stopping.is_set():
            raise AppClosing()
        done = threading.Event()
        result = []
        def run():
            try:
                if self.stopping.is_set():
                    raise AppClosing()
                result.append((True, function(*args, **kwargs)))
            except Exception as exc:
                result.append((False, exc))
            finally:
                done.set()
        threading.Thread(target=run, name='cancellable-request', daemon=True).start()
        while not done.wait(0.05):
            if self.stopping.is_set():
                raise AppClosing()
        if self.stopping.is_set():
            raise AppClosing()
        ok, value = result[0]
        if not ok:
            raise value
        return value

    def return_from_player(self):
        self.show()


    def snapshot(self):
        data = self.store.snapshot()
        data['runtime'] = {'account': self.account, 'account_valid': self.account_valid,
                           'warning': self.warning, 'checking': self.checking,
                           'tray': self.tray_ok, 'autostart': get_autostart(), 'playback_revision': self.play_queue.revision}
        config = data['play_schedule']
        data['runtime']['schedule_next'] = next_due(config)
        data['runtime']['schedule_unwatched'] = len(unwatched_order(data, config))
        return data

    def emit(self, method, value):
        if self.ready.is_set() and not self.quitting:
            post_js(self.window, f'window.{method}({json.dumps(value, ensure_ascii=True)})')

    def changed(self):
        self.refresh_playback_view()
        self.emit('receiveState', self.snapshot())

    def show(self):
        with self.lifecycle_lock:
            if self.quitting:
                return
            self.playback_allowed = True
        show_window(self.window)

    def hide_to_tray(self):
        with self.lifecycle_lock:
            if self.quitting or not self.tray_ok:
                return False
            self.playback_allowed = False
            self.embedded_active = False
        self.play_queue.cancel()
        # This callback may run on the GUI thread: enqueue work and return.
        # Do not evaluate_js, Invoke, join, or wait for a renderer here.
        def hide(form):
            core = form.browser.webview.CoreWebView2
            if core is not None:
                core.ExecuteScriptAsync('if(window.stopPlayerFromHost)window.stopPlayerFromHost();')
            form.Hide()
        post_window(self.window, hide)
        self.focus_player.close()
        self.youtube_player.close()
        return True

    def close(self):
        if self.quitting:
            return True
        if self.tray_ok:
            self.hide_to_tray()
            return False
        self.quit()
        # The queued Close will reenter with quitting=True after this returns.
        return False

    def quit(self):
        with self.lifecycle_lock:
            if self.quitting:
                return
            self.quitting = True
            self.playback_allowed = False
            self.embedded_active = False
            self.stopping.set()
            self.wake.set()
            self.schedule_wake.set()
        self.play_queue.cancel()
        self.focus_player.close()
        self.youtube_player.close()
        if self.tray:
            threading.Thread(target=self.tray.stop, name='tray-stop', daemon=True).start()
        close_window(self.toast)
        close_window(self.window)

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
        deadline = time.monotonic() + 8
        while not self.toast_ready.wait(0.05):
            if self.stopping.is_set():
                return
            if time.monotonic() >= deadline:
                self.warning = '弹窗尚未就绪；更新仍保存在视频列表中。'
                return
        if self.stopping.is_set():
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
            script = f'window.setNotification({json.dumps(dict(title=title, body=body), ensure_ascii=True)})'
            def display(form):
                if self.quitting or version != self.toast_version:
                    return
                core = form.browser.webview.CoreWebView2
                if core is not None:
                    core.ExecuteScriptAsync(script)
                form.Show()
            post_window(self.toast, display)
        if self.store.snapshot()['settings']['sound']:
            import winsound
            winsound.MessageBeep(winsound.MB_OK)
        def hide_later():
            if not self.stopping.wait(14):
                with self.notification_lock:
                    if version == self.toast_version:
                        post_window(self.toast, lambda form: form.Hide())
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
        threading.Thread(target=self.schedule_worker, name='playback-schedule', daemon=True).start()


class Api:
    """Explicit bridge methods; no filesystem or arbitrary URL/command access."""
    def __init__(self, app):
        self._app = app

    def snapshot(self):
        return self._app.snapshot()

    def save_playback_schedule(self, values):
        try:
            config = self._app.store.save_playback_schedule(values)
            if self._app.schedule_starting is not None:
                self._app.play_queue.cancel(self._app.schedule_starting)
            self._app.schedule_wake.set()
            self._app.changed()
            return {'ok': True, 'schedule': config, 'next': next_due(config)}
        except (ValueError, OSError) as exc:
            return {'ok': False, 'error': str(exc)}

    def set_watched(self, key, watched):
        try:
            self._app.store.set_watched(key, watched)
            self._app.changed()
            return {'ok': True}
        except (ValueError, OSError) as exc:
            return {'ok': False, 'error': str(exc)}

    def set_embedded_active(self, enabled):
        if type(enabled) is not bool:
            return {'ok': False, 'error': '播放状态无效。'}
        self._app.embedded_active = enabled
        return {'ok': True}

    def add_channel(self, text, platform='bilibili'):
        try:
            if platform == 'youtube':
                info, feed = self._app.request(self._app.youtube.resolve_channel, text)
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
        except (ValueError, OSError, YouTubeError, AppClosing) as exc:
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
                return {'ok': True, 'users': self._app.request(self._app.client.search_users, text)}
            except BiliError as exc:
                return {'ok': False, 'error': str(exc)}

    def remove_channel(self, uid):
        self._app.store.remove_channel(str(uid))
        current = self._app.play_queue.current
        if current and current not in owned_videos(self._app.store.snapshot()):
            self.close_focus_video()
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
            self._app.autoplay_followed = self._app.store.snapshot()['settings']['autoplay_followed']
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

    def open_focus_video(self, key, part=1, order=None):
        if self._app.quitting or not self._app.playback_allowed:
            return {'ok': False, 'error': '播放已停止；请打开主窗口后再选择视频。'}
        video = next((v for v in self._app.store.snapshot()['videos'] if v['key'] == key), None)
        if not video or not (video_bvid(video) or youtube_video_id(video)):
            return {'ok': False, 'error': '该视频已不在你的关注列表，或视频编号无效。'}
        if isinstance(part, bool) or not isinstance(part, (int, float)) or not math.isfinite(part) or int(part) != part or not 1 <= part <= 999:
            return {'ok': False, 'error': '分 P 序号应为 1 至 999 的整数。'}
        ticket = None
        try:
            ticket = self._app.play_queue.start(key, order)
            return self._app.play_ticket(ticket, int(part))
        except AppClosing as exc:
            if ticket:
                self._app.play_queue.cancel(ticket)
            return {'ok': False, 'error': str(exc)}
        except Exception:
            if ticket:
                self._app.play_queue.cancel(ticket)
            logging.exception('Official player could not open')
            return {'ok': False, 'error': '官方播放窗口未能打开，请稍后重试，或切换到嵌入备用模式。'}

    def close_focus_video(self):
        self._app.play_queue.cancel()
        self._app.focus_player.close()
        self._app.youtube_player.close()
        return {'ok': True}

    def set_autoplay(self, enabled):
        try:
            self._app.set_autoplay(enabled)
            return {'ok': True}
        except (ValueError, OSError) as exc:
            return {'ok': False, 'error': str(exc)}

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
                result = self._app.request(client.create_qr)
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
                result = self._app.request(self._app.qr_client.poll_qr, self._app.qr_key)
                if result['status'] == 'success':
                    cookies = result['cookies']
                    save_session(DATA / 'session.bin', cookies)
                    client = BiliClient(cookies=cookies)
                    try:
                        name = self._app.request(client.check_login) or 'B 站用户'
                    except AppClosing:
                        raise
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
            self._app.hide_to_tray()
            return {'ok': True}
        return {'ok': False, 'error': '托盘不可用，请保留窗口运行。'}

    def quit(self):
        self._app.quit()
        return {'ok': True}


class NotificationApi:
    def __init__(self, app):
        self._app = app

    def open(self):
        with self._app.notification_lock:
            key = self._app.toast_target
            post_window(self._app.toast, lambda form: form.Hide())
        self._app.show()
        if key:
            self._app.emit('openVideo', key)

    def dismiss(self):
        post_window(self._app.toast, lambda form: form.Hide())


def run():
    if not acquire_instance():
        ctypes.windll.user32.MessageBoxW(None, '程序已经在运行。请点击右下角托盘中的粉色电视图标打开。', '定制关注', 64)
        return
    try:
        arguments = os.environ.get('WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS', '')
        if '--autoplay-policy=' not in arguments:
            os.environ['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS'] = arguments + ' --autoplay-policy=no-user-gesture-required'
        DATA.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(DATA / 'app.log', maxBytes=500_000, backupCount=2, encoding='utf-8')
        logging.basicConfig(level=logging.WARNING, handlers=[handler])
        app = Application()
        app.window = webview.create_window('定制关注', str(ASSETS / 'index.html'), js_api=Api(app),
                                          width=1220, height=820, min_size=(960, 660),
                                          hidden='--background' in sys.argv, background_color='#101217')
        app.window.events.loaded += app.ready.set
        app.window.events.closing += app.close
        install_bridge_delivery(app.window)
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
        install_bridge_delivery(app.toast)
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
