"""Finite playback order containing only locally saved creators and videos."""
from dataclasses import dataclass
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

    def start(self, key, order=None):
        videos = owned_videos(self.snapshot())
        if key not in videos:
            raise ValueError('该视频或博主已不在你的关注清单。')
        if order is not None and (not isinstance(order, list) or len(order) > 500 or any(not isinstance(k, str) for k in order)):
            raise ValueError('播放清单格式无效。')
        keys = list(dict.fromkeys(k for k in (order if order is not None else videos) if k in videos))
        if key not in keys:
            keys.insert(0, key)
        with self._lock:
            self.revision += 1
            self.current, self.order = key, keys
            return PlayTicket(self.revision, key)

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
                if key in videos:
                    self.revision += 1
                    self.current = key
                    return PlayTicket(self.revision, key)
                index += delta
            return None

    def next_part(self, ticket):
        with self._lock:
            if not self.matches(ticket):
                return None
            self.revision += 1
            return PlayTicket(self.revision, ticket.key)

    def remaining(self, ticket):
        with self._lock:
            if not self.matches(ticket) or ticket.key not in self.order:
                return 0
            return len(self.order) - self.order.index(ticket.key) - 1

    def cancel(self, ticket=None):
        with self._lock:
            if ticket is not None and not self.matches(ticket):
                return False
            self.revision += 1
            self.current, self.order = None, []
            return True
