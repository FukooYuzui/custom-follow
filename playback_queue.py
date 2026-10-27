"""Finite playback order containing only locally saved creators and videos."""
from dataclasses import dataclass
import hashlib
import json
import re
import threading


@dataclass(frozen=True)
class PlayTicket:
    revision: int
    key: str


def owned_videos(snapshot):
    creators = {c['uid'] for c in snapshot.get('channels', [])}
    return {v['key']: v for v in snapshot.get('videos', []) if v.get('uid') in creators and
            re.fullmatch(r'(?:BV[0-9A-Za-z]{10}|yt:[A-Za-z0-9_-]{11})', str(v.get('key', '')))}


class FollowedQueue:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self._lock = threading.RLock()
        self.revision = 0
        self.current = None
        self.order = []
        self.finished = False
        self.unwatched_only = False

    def start(self, key, order=None, unwatched_only=False, if_idle=False, revision=None):
        videos = owned_videos(self.snapshot())
        if unwatched_only:
            videos = {k: v for k, v in videos.items() if not v.get('watched', False)}
        if key not in videos:
            raise ValueError('该视频或博主已不在你的关注清单。')
        if order is not None and (not isinstance(order, list) or len(order) > 500 or any(not isinstance(k, str) for k in order)):
            raise ValueError('播放清单格式无效。')
        keys = list(dict.fromkeys(k for k in (order if order is not None else videos) if k in videos))
        if key not in keys:
            keys.insert(0, key)
        with self._lock:
            if if_idle and (self.is_active() or revision is not None and revision != self.revision):
                raise ValueError('正在播放，本次定时任务已跳过。')
            self.revision += 1
            self.current, self.order = key, keys
            self.finished, self.unwatched_only = False, unwatched_only
            return PlayTicket(self.revision, key)

    def is_active(self):
        with self._lock:
            return self.current is not None and not self.finished

    def mark_finished(self, ticket):
        with self._lock:
            if self.matches(ticket):
                self.finished = True

    def matches(self, ticket):
        with self._lock:
            return ticket is not None and ticket.revision == self.revision and ticket.key == self.current

    def move(self, ticket, delta=1):
        videos = owned_videos(self.snapshot())
        with self._lock:
            if not self.matches(ticket) or ticket.key not in videos or ticket.key not in self.order:
                return None
            index = self.order.index(ticket.key) + delta
            while 0 <= index < len(self.order):
                key = self.order[index]
                if key in videos and (not self.unwatched_only or not videos[key].get('watched', False)):
                    self.revision += 1
                    self.current = key
                    self.finished = False
                    return PlayTicket(self.revision, key)
                index += delta
            return None

    def next_part(self, ticket):
        with self._lock:
            if not self.matches(ticket):
                return None
            self.revision += 1
            self.finished = False
            return PlayTicket(self.revision, ticket.key)

    def remaining(self, ticket):
        """Count currently eligible future entries; reads a store snapshot.

        Native GUI callbacks must use a host-maintained cached view rather than
        call this method while another thread might be saving the store.
        """
        return self.view(ticket)['remaining']

    def view(self, ticket):
        """Return a detached display view of the current and upcoming works.

        Store reads deliberately precede the queue lock so a stalled disk write
        cannot prevent close/cancel from invalidating an in-flight request.
        """
        videos = owned_videos(self.snapshot())
        items = []
        with self._lock:
            if self.matches(ticket) and ticket.key in self.order:
                for key in self.order[self.order.index(ticket.key):]:
                    video = videos.get(key)
                    if video is None or (key != ticket.key and self.unwatched_only and video.get('watched', False)):
                        continue
                    items.append({
                        'key': key,
                        'title': str(video.get('title') or '未命名视频')[:200],
                        'author': str(video.get('author') or '博主')[:100],
                        'platform': 'youtube' if key.startswith('yt:') else 'bilibili',
                        'watched': bool(video.get('watched', False)),
                        'current': key == ticket.key,
                    })
        payload = json.dumps(items, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
        return {'version': hashlib.sha256(payload).hexdigest()[:20], 'items': items,
                'remaining': sum(not item['current'] for item in items)}

    def select(self, ticket, key):
        """Select only an eligible upcoming work from this exact queue lease."""
        videos = owned_videos(self.snapshot())
        with self._lock:
            if (not isinstance(key, str) or not self.matches(ticket) or key == ticket.key or
                    ticket.key not in videos or ticket.key not in self.order or key not in videos or
                    key not in self.order or self.order.index(key) <= self.order.index(ticket.key) or
                    self.unwatched_only and videos[key].get('watched', False)):
                return None
            self.revision += 1
            self.current, self.finished = key, False
            return PlayTicket(self.revision, key)

    def cancel(self, ticket=None):
        with self._lock:
            if ticket is not None and not self.matches(ticket):
                return False
            self.revision += 1
            self.current, self.order = None, []
            self.finished, self.unwatched_only = False, False
            return True
