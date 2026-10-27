"""Thread-safe local subscriptions, history, and update detection.

No network requests or desktop actions are made in this module. A first successful
fetch establishes a baseline and adds recent videos for viewing without notifying.
"""
from __future__ import annotations

import copy
import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading
import time
from datetime import datetime
from typing import Any


DEFAULT_SETTINGS = {
    "interval_minutes": 5,
    "paused": False,
    "popups": True,
    "sound": False,
    "quiet_enabled": False,
    "quiet_start": "23:00",
    "quiet_end": "08:00",
}


def parse_uid(text: str) -> str:
    """Accept a numeric UID or an exact space.bilibili.com profile link."""
    text = str(text).strip()
    if re.fullmatch(r"[0-9]{1,20}", text):
        value = int(text)
    else:
        match = re.search(
            r"(?<![\w./:@-])(?:https?://)?space\.bilibili\.com/"
            r"([0-9]{1,20})(?![0-9A-Za-z._~-])",
            text,
            flags=re.IGNORECASE,
        )
        if not match:
            raise ValueError("请输入 UP 主的数字 UID 或 space.bilibili.com 开头的个人空间链接。")
        value = int(match.group(1))
    if value < 1 or value > 2**63 - 1:
        raise ValueError("UID 必须是有效的正整数。")
    return str(value)


def _clock_minutes(value: Any) -> int:
    if not isinstance(value, str) or not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value):
        raise ValueError("免打扰时间请使用 00:00 至 23:59 格式。")
    hours, minutes = map(int, value.split(":"))
    return hours * 60 + minutes


def is_quiet(settings: dict, now: datetime | None = None) -> bool:
    """Return whether quiet hours apply; equal endpoints mean all day."""
    if not settings.get("quiet_enabled", False):
        return False
    current = now or datetime.now()
    start = _clock_minutes(settings.get("quiet_start", "23:00"))
    end = _clock_minutes(settings.get("quiet_end", "08:00"))
    minute = current.hour * 60 + current.minute
    if start == end:
        return True
    return start <= minute < end if start < end else minute >= start or minute < end


def _timestamp(now: float | None) -> float:
    value = time.time() if now is None else float(now)
    if not math.isfinite(value) or value < 0:
        raise ValueError("时间戳无效。")
    return value


def _field(item: Any, name: str, default: Any = None) -> Any:
    return item.get(name, default) if isinstance(item, dict) else getattr(item, name, default)


def _publication_precision(video):
    if not _field(video, 'published_at', 0):
        return 'unknown'
    precision = _field(video, 'published_precision', '')
    if precision in {'second', 'day', 'relative', 'unknown'}:
        return precision
    if not _field(video, 'timestamp_approximate', False):
        return 'second'
    return 'relative' if _field(video, 'published_label', '') not in {'', '发布时间未提供'} else 'unknown'


class Store:
    """Atomic JSON state store. Returned data is always detached from live state."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(data, dict) or data.get("version") != 1:
                    raise ValueError("状态文件版本不受支持")
                if not isinstance(data.get("channels"), list) or not isinstance(data.get("videos"), list):
                    raise ValueError("状态文件格式无效")
                for channel in data["channels"]:
                    if not isinstance(channel, dict) or not isinstance(channel.get("uid"), str):
                        raise ValueError("订阅记录格式无效")
                    if not isinstance(channel.get("seen", []), list):
                        raise ValueError("视频去重记录格式无效")
                    channel.setdefault('platform', 'youtube' if channel['uid'].startswith('yt:') else 'bilibili')
                    channel.setdefault('channel_id', channel['uid'][3:] if channel['platform'] == 'youtube' else channel['uid'])
                    # Older stores recorded attempts and successful fetches in one
                    # field. After an error, only the initial baseline is known safe.
                    channel.setdefault("last_success_at", (
                        channel.get("checked_at") if not channel.get("failures")
                        else channel.get("baseline_at")
                    ) if channel.get("initialized") else None)
                for video in data['videos']:
                    video.setdefault('platform', 'youtube' if video.get('uid', '').startswith('yt:') else 'bilibili')
                    if video['platform'] == 'youtube':
                        video['published_precision'] = _publication_precision(video)
                        if video['published_precision'] == 'unknown':
                            video['published_at'] = 0
                data['videos'].sort(key=lambda item: (item.get('published_at', 0), item['key']), reverse=True)
                settings = dict(DEFAULT_SETTINGS)
                settings.update(data.get("settings", {}))
                self._validate_settings(settings)
                data["settings"] = settings
                data.setdefault("last_manual_check", 0)
                self._data = data
            except (ValueError, TypeError, AttributeError) as exc:
                raise ValueError(f"无法读取本地数据文件，请保留文件后再修复：{self.path.name}") from exc
        else:
            self._data = {
                "version": 1,
                "settings": dict(DEFAULT_SETTINGS),
                "channels": [],
                "videos": [],
                "last_manual_check": 0,
            }
            self._save(self._data)

    def _save(self, data: dict) -> None:
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent,
                prefix=self.path.name + ".", suffix=".tmp", delete=False,
            ) as stream:
                temporary = Path(stream.name)
                json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _commit(self, data: dict) -> None:
        self._save(data)
        self._data = data

    @staticmethod
    def _validate_settings(settings: dict) -> None:
        unknown = set(settings) - set(DEFAULT_SETTINGS)
        if unknown:
            raise ValueError("存在不支持的设置项。")
        interval = settings.get("interval_minutes", 5)
        if isinstance(interval, bool) or not isinstance(interval, (float, int)) or not math.isfinite(interval) or int(interval) != interval or not 3 <= interval <= 60:
            raise ValueError("检查间隔应为 3 至 60 分钟的整数。")
        for name in ("paused", "popups", "sound", "quiet_enabled"):
            if not isinstance(settings.get(name), bool):
                raise ValueError("开关设置值必须是布尔值。")
        _clock_minutes(settings.get("quiet_start"))
        _clock_minutes(settings.get("quiet_end"))

    def snapshot(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._data)

    def add_channel(self, uid: str, name: str = "", platform: str = 'bilibili') -> dict:
        if platform == 'youtube':
            channel_id = str(uid).removeprefix('yt:')
            if not re.fullmatch(r'UC[A-Za-z0-9_-]{22}', channel_id):
                raise ValueError('YouTube 频道 ID 无效。')
            uid = 'yt:' + channel_id
        elif platform == 'bilibili':
            uid = parse_uid(uid)
            channel_id = uid
        else:
            raise ValueError('不支持的关注平台。')
        with self._lock:
            if any(channel["uid"] == uid for channel in self._data["channels"]):
                raise ValueError("已经添加过这位 UP 主了。")
            channel = {
                "uid": uid, "name": str(name).strip() or f"UP 主 {uid}",
                'platform': platform, 'channel_id': channel_id,
                "enabled": True, "initialized": False, "baseline_at": None,
                "seen": [], "checked_at": None, "last_success_at": None, "next_check": 0,
                "failures": 0, "error": "", "latest": None,
            }
            data = copy.deepcopy(self._data)
            data["channels"].append(channel)
            self._commit(data)
            return copy.deepcopy(channel)

    def remove_channel(self, uid: str) -> bool:
        uid = str(uid)
        with self._lock:
            data = copy.deepcopy(self._data)
            filtered = [channel for channel in data["channels"] if channel["uid"] != uid]
            if len(filtered) == len(data["channels"]):
                return False
            data["channels"] = filtered
            data["videos"] = [video for video in data["videos"] if video["uid"] != uid]
            self._commit(data)
            return True

    def toggle_channel(self, uid: str) -> bool:
        with self._lock:
            data = copy.deepcopy(self._data)
            channel = next((item for item in data["channels"] if item["uid"] == str(uid)), None)
            if channel is None:
                raise ValueError("这位 UP 主已不在列表中。")
            channel["enabled"] = not channel["enabled"]
            if channel["enabled"]:
                channel["next_check"] = 0
            self._commit(data)
            return channel["enabled"]

    def update_settings(self, values: dict) -> dict:
        if not isinstance(values, dict):
            raise ValueError("设置格式无效。")
        with self._lock:
            data = copy.deepcopy(self._data)
            previous = data["settings"]
            merged = dict(previous)
            merged.update(values)
            self._validate_settings(merged)
            merged["interval_minutes"] = int(merged["interval_minutes"])
            data["settings"] = merged
            if merged["interval_minutes"] != previous["interval_minutes"]:
                for channel in data["channels"]:
                    # A configuration change must not cancel server-error backoff.
                    if channel.get("checked_at") is not None and not channel.get("failures"):
                        channel["next_check"] = channel["checked_at"] + merged["interval_minutes"] * 60
            self._commit(data)
            return copy.deepcopy(merged)

    def set_interval(self, minutes: int) -> dict:
        return self.update_settings({"interval_minutes": minutes})

    def mark_read(self, video_key: str) -> bool:
        with self._lock:
            data = copy.deepcopy(self._data)
            video = next((item for item in data["videos"] if item["key"] == str(video_key)), None)
            if video is None:
                return False
            if not video["read"]:
                video["read"] = True
                self._commit(data)
            return True

    def ingest(self, uid: str, feed: Any, now: float | None = None) -> list[dict]:
        checked_at = _timestamp(now)
        with self._lock:
            data = copy.deepcopy(self._data)
            channel = next((item for item in data["channels"] if item["uid"] == str(uid)), None)
            # Ignore work which completed after the user removed or disabled a UP.
            if channel is None or not channel["enabled"] or data["settings"]["paused"]:
                return []
            first_fetch = not channel["initialized"]
            if first_fetch:
                channel["baseline_at"] = checked_at
            name = str(_field(feed, "name", "") or "").strip()
            if name:
                channel["name"] = name
            seen = set(channel["seen"])
            history = {video["key"]: video for video in data["videos"]}
            notifications = []
            fresh_videos = []
            sources = _field(feed, 'videos', []) or []
            previous_latest = (channel.get('latest') or {}).get('key')
            order_cutoff = next((i for i, source in enumerate(sources) if _field(source, 'key') == previous_latest), len(sources))
            for index, source in enumerate(sources):
                key = str(_field(source, "key", "") or "")
                if not key:
                    continue
                published = _timestamp(_field(source, "published_at", 0) or 0)
                # YouTube's Videos tab has newest-first IDs but relative dates.
                # Compare the previous leading ID rather than treating its date
                # estimate as an exact timestamp and missing same-hour uploads.
                fresh_by_order = bool(_field(feed, 'order_is_freshness', False)) and index < order_cutoff
                is_new = not first_fetch and key not in seen and (published > channel["baseline_at"] or fresh_by_order)
                previous = history.get(key)
                video = {
                    "key": key, "title": str(_field(source, "title", "") or "未命名视频"),
                    "url": str(_field(source, "url", "") or ""),
                    "cover": str(_field(source, "cover", "") or ""),
                    "duration": str(_field(source, "duration", "") or ""),
                    "published_at": published,
                    "author": str(_field(source, "author", "") or channel["name"]),
                    "uid": str(uid), "is_new": is_new,
                    'platform': channel.get('platform', 'bilibili'),
                    'published_label': str(_field(source, 'published_label', '') or ''),
                    'timestamp_approximate': bool(_field(source, 'timestamp_approximate', False)),
                    'published_precision': _publication_precision(source),
                    "read": not is_new, "detected_at": checked_at,
                }
                if previous is not None:
                    video.update({field: previous[field] for field in ("is_new", "read", "detected_at")})
                    rank = {'unknown': 0, 'relative': 1, 'day': 2, 'second': 3}
                    if rank[_publication_precision(previous)] > rank[video['published_precision']]:
                        for field in ('published_at', 'published_label', 'timestamp_approximate'):
                            video[field] = previous.get(field, '' if field == 'published_label' else False)
                        video['published_precision'] = _publication_precision(previous)
                history[key] = video
                fresh_videos.append(video)
                if is_new:
                    notifications.append(copy.deepcopy(video))
                seen.add(key)
            channel.update({
                "initialized": True, "seen": sorted(seen), "checked_at": checked_at,
                "last_success_at": checked_at,
                "next_check": checked_at + data["settings"]["interval_minutes"] * 60,
                "failures": 0, "error": "", "history_incomplete": bool(_field(feed, "has_more", False)),
                'source': str(_field(feed, 'source', 'bilibili')),
            })
            if fresh_videos and _field(feed, 'order_is_freshness', False):
                channel['latest'] = copy.deepcopy(fresh_videos[0])
            elif fresh_videos:
                candidates = fresh_videos + ([channel["latest"]] if channel["latest"] else [])
                channel["latest"] = copy.deepcopy(max(candidates, key=lambda item: (item["published_at"], item["key"])))
            data["videos"] = sorted(history.values(), key=lambda item: (item["published_at"], item["key"]), reverse=True)[:500]
            self._commit(data)
            return sorted(notifications, key=lambda item: (item["published_at"], item["key"]))

    def record_error(self, uid: str, message: str, now: float | None = None) -> bool:
        checked_at = _timestamp(now)
        with self._lock:
            data = copy.deepcopy(self._data)
            channel = next((item for item in data["channels"] if item["uid"] == str(uid)), None)
            if channel is None or not channel["enabled"] or data["settings"]["paused"]:
                return False
            channel["failures"] = int(channel.get("failures", 0)) + 1
            delay = min(60, 5 * 2 ** min(channel["failures"] - 1, 4)) * 60
            channel["error"] = str(message)[:1200]
            channel["checked_at"] = checked_at
            channel["next_check"] = checked_at + delay
            self._commit(data)
            return True

    def due_channels(self, now: float | None = None) -> list[dict]:
        current = _timestamp(now)
        with self._lock:
            if self._data["settings"]["paused"]:
                return []
            return copy.deepcopy([
                channel for channel in self._data["channels"]
                if channel["enabled"] and channel["next_check"] <= current
            ])

    def request_check(self, reset_errors: bool = False) -> bool:
        current = time.time()
        with self._lock:
            last = self._data.get("last_manual_check", 0)
            if not reset_errors and last and current - last < 60:
                return False
            data = copy.deepcopy(self._data)
            data["last_manual_check"] = current
            for channel in data["channels"]:
                if channel["enabled"]:
                    # Respect platform backoff unless a new login explicitly changed it.
                    if reset_errors or not channel["failures"] or channel["next_check"] <= current:
                        channel["next_check"] = 0
                    if reset_errors:
                        channel["failures"] = 0
                        channel["error"] = ""
            self._commit(data)
            return True
