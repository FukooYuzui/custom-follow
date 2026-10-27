"""Read public YouTube channel updates, without keys or account credentials.

Prefer the official Atom feed. If that endpoint is unavailable, read only the
selected channel's public Videos tab and publication metadata. Relative dates
remain explicitly approximate; unknown dates never become polling timestamps.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
import re
import threading
import time
from urllib.parse import quote, unquote, urlparse
import xml.etree.ElementTree as ET

import requests
from bili_api import BiliError

CHANNEL_ID = re.compile(r'UC[A-Za-z0-9_-]{22}')
VIDEO_ID = re.compile(r'[A-Za-z0-9_-]{11}')
HOSTS = {'youtube.com', 'www.youtube.com', 'm.youtube.com'}
NS = {'a': 'http://www.w3.org/2005/Atom', 'yt': 'http://www.youtube.com/xml/schemas/2015',
      'm': 'http://search.yahoo.com/mrss/'}


class YouTubeError(BiliError):
    pass


@dataclass(frozen=True)
class YouTubeVideo:
    key: str
    title: str
    url: str
    published_at: float
    author: str
    cover: str = ''
    duration: str = ''
    published_label: str = ''
    timestamp_approximate: bool = False
    published_precision: str = ''


@dataclass(frozen=True)
class YouTubeFeed:
    name: str
    videos: list[YouTubeVideo]
    has_more: bool = False
    order_is_freshness: bool = False
    source: str = 'atom'


def channel_key(channel_id: str) -> str:
    if not CHANNEL_ID.fullmatch(str(channel_id)):
        raise ValueError('YouTube 频道 ID 应为 UC 开头的 24 位编号。')
    return 'yt:' + channel_id


def youtube_video_id(video: dict) -> str | None:
    key = str(video.get('key', ''))
    if key.startswith('yt:') and VIDEO_ID.fullmatch(key[3:]):
        return key[3:]
    return None


def channel_input(text: str) -> tuple[str | None, str]:
    """Return an ID or a normalized, strictly official channel-page URL."""
    text = str(text).strip()
    if text.startswith('yt:'):
        text = text[3:]
    if CHANNEL_ID.fullmatch(text):
        return text, f'https://www.youtube.com/channel/{text}/videos?hl=en'
    if text.startswith('@'):
        if not 1 <= len(text[1:]) <= 100 or re.search(r'[\s/?#\\\x00-\x1f]', text):
            raise ValueError('请输入有效的 YouTube @账号。')
        return None, 'https://www.youtube.com/' + quote(text, safe='@._-') + '/videos?hl=en'
    if text.startswith(('youtube.com/', 'www.youtube.com/', 'm.youtube.com/')):
        text = 'https://' + text
    try:
        p = urlparse(text)
        if p.scheme not in {'http', 'https'} or p.hostname not in HOSTS or p.username or p.password or p.port not in (None, 80, 443):
            raise ValueError
        parts = unquote(p.path).strip('/').split('/')
        if len(parts) >= 2 and parts[0] == 'channel' and CHANNEL_ID.fullmatch(parts[1]):
            return parts[1], f'https://www.youtube.com/channel/{parts[1]}/videos?hl=en'
        if parts and parts[0].startswith('@') and len(parts[0]) > 1:
            return channel_input(parts[0])
        if len(parts) >= 2 and parts[0] in {'user', 'c'} and re.fullmatch(r'[\w.-]{1,100}', parts[1], re.UNICODE):
            return None, f'https://www.youtube.com/{parts[0]}/{quote(parts[1])}/videos?hl=en'
    except (ValueError, TypeError):
        pass
    raise ValueError('请粘贴 YouTube 频道主页链接、填写 @账号，或 UC 开头的频道 ID。')


def _walk(value, key):
    if isinstance(value, dict):
        if key in value:
            yield value[key]
        for child in value.values():
            yield from _walk(child, key)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child, key)


def _initial_json(html: str, name: str) -> dict:
    assignment = r'(?:var\s+)?' + re.escape(name) + r'\s*=\s*|window\[\s*[\'\"]' + re.escape(name) + r'[\'\"]\s*\]\s*=\s*'
    for match in re.finditer(assignment, html):
        try:
            value, _ = json.JSONDecoder().raw_decode(html[match.end():].lstrip())
            if isinstance(value, dict):
                return value
        except ValueError:
            continue
    raise YouTubeError('YouTube 未返回可读取的页面资料，可能需要在官方页面处理验证。')


def initial_data(html: str) -> dict:
    return _initial_json(html, 'ytInitialData')


def _text(value) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return ''
    return str(value.get('simpleText') or value.get('content') or ''.join(str(x.get('text', '')) for x in value.get('runs', []) if isinstance(x, dict)))


def metadata(data: dict) -> tuple[str, str]:
    info = next(_walk(data, 'channelMetadataRenderer'), {})
    cid, name = str(info.get('externalId', '')), str(info.get('title', '')).strip()
    if not CHANNEL_ID.fullmatch(cid) or not name:
        raise YouTubeError('未找到有效的 YouTube 频道资料，请核对频道主页或 @账号。', False)
    return cid, name


def relative_timestamp(label: str, now: float) -> float | None:
    value = label.strip().lower().replace(',', '')
    if value in {'just now', '刚刚', '剛剛'}:
        return now
    match = re.search(r'(?<![\w.])(\d+)\s*(seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h|days?|d|weeks?|wks?|w|months?|mos?|mo|years?|yrs?|y)\s*ago\b', value)
    if match:
        unit = match[2]
        seconds = (2592000 if unit.startswith(('month', 'mo')) else
                   31536000 if unit.startswith(('year', 'yr', 'y')) else
                   604800 if unit.startswith(('week', 'wk', 'w')) else
                   86400 if unit.startswith(('day', 'd')) else
                   3600 if unit.startswith(('hour', 'hr', 'h')) else
                   60 if unit.startswith(('minute', 'min', 'm')) else 1)
        return max(0, now - int(match[1]) * seconds)
    # A page may ignore hl=en; accept common simplified/traditional labels too.
    match = re.search(r'(\d+)\s*(秒|分钟|分鐘|小时|小時|天|週|周|个月|個月|年)前', value)
    if match:
        seconds = {'秒': 1, '分钟': 60, '分鐘': 60, '小时': 3600, '小時': 3600,
                   '天': 86400, '週': 604800, '周': 604800, '个月': 2592000, '個月': 2592000, '年': 31536000}[match[2]]
        return max(0, now - int(match[1]) * seconds)
    return None


def parse_video_publication(html: str, expected_video: str, expected_channel: str) -> dict:
    """Read only official publication metadata, never playback/media URLs."""
    data = _initial_json(html, 'ytInitialPlayerResponse')
    details = data.get('videoDetails') or {}
    microformat = data.get('microformat') or {}
    micro = microformat.get('playerMicroformatRenderer', {}) if isinstance(microformat, dict) else {}
    if not isinstance(details, dict) or not isinstance(micro, dict):
        raise YouTubeError('YouTube 未提供有效的视频发布时间资料。')
    if details.get('videoId') != expected_video or micro.get('externalChannelId') != expected_channel:
        raise YouTubeError('YouTube 返回的视频或频道与当前关注不符。', False)
    value = str(micro.get('publishDate') or '')
    live = micro.get('liveBroadcastDetails') or {}
    if not value or details.get('isUpcoming') or (isinstance(live, dict) and live.get('isLiveNow')):
        raise YouTubeError('YouTube 暂未提供已发布视频的时间。')
    try:
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            stamp = datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp()
            precision, label = 'day', value
        else:
            date = datetime.fromisoformat(value.replace('Z', '+00:00'))
            if date.tzinfo is None:
                raise ValueError
            stamp, precision, label = date.timestamp(), 'second', ''
        if stamp <= 0 or stamp > time.time() + 300:
            raise ValueError
    except (ValueError, OverflowError, OSError):
        raise YouTubeError('YouTube 未返回有效的视频发布时间。') from None
    return {'published_at': stamp, 'published_precision': precision,
            'published_label': label, 'timestamp_approximate': precision != 'second'}


def parse_atom(xml: str, expected_id: str) -> YouTubeFeed:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        raise YouTubeError('YouTube 更新源格式异常，本次未更新检查记录。') from None
    cid = root.findtext('yt:channelId', '', NS)
    name = root.findtext('a:author/a:name', '', NS).strip() or root.findtext('a:title', '', NS).strip()
    if cid != expected_id or not name:
        raise YouTubeError('YouTube 未返回该频道的有效更新源，本次未建立空记录。')
    videos = []
    for entry in root.findall('a:entry', NS):
        vid = entry.findtext('yt:videoId', '', NS)
        if entry.findtext('yt:channelId', expected_id, NS) != expected_id or not VIDEO_ID.fullmatch(vid):
            raise YouTubeError('YouTube 更新源中的频道或视频编号异常。')
        title = entry.findtext('a:title', '', NS).strip()
        date = entry.findtext('a:published', '', NS)
        try:
            stamp = datetime.fromisoformat(date.replace('Z', '+00:00')).timestamp()
        except (ValueError, TypeError, OverflowError):
            raise YouTubeError('YouTube 未返回有效的视频发布时间。') from None
        if not title:
            raise YouTubeError('YouTube 视频信息不完整。')
        videos.append(YouTubeVideo('yt:' + vid, title, f'https://www.youtube.com/watch?v={vid}', stamp, name,
                                   f'https://i.ytimg.com/vi/{vid}/hqdefault.jpg'))
    return YouTubeFeed(name, sorted(videos, key=lambda v: v.published_at, reverse=True))


def parse_video_page(html: str, expected_id: str | None = None, now: float | None = None) -> tuple[str, YouTubeFeed]:
    data = initial_data(html)
    cid, name = metadata(data)
    if expected_id is not None and cid != expected_id:
        raise YouTubeError('YouTube 返回了其他频道的页面，已停止导入。', False)
    tabs = next(_walk(data.get('contents', {}), 'twoColumnBrowseResultsRenderer'), {}).get('tabs', [])
    selected = next((tab['tabRenderer'] for tab in tabs if isinstance(tab, dict) and isinstance(tab.get('tabRenderer'), dict) and tab['tabRenderer'].get('selected')), None)
    if selected is None:
        raise YouTubeError('未能确认 YouTube 的频道视频页，本次未更新检查记录。')
    content = selected.get('content', {})
    grid = next(_walk(content, 'richGridRenderer'), None)
    legacy_grid = next(_walk(content, 'gridRenderer'), None)
    if grid is None and legacy_grid is None:
        # A genuine empty channel supplies an explicit no-content message.
        messages = list(_walk(content, 'messageRenderer'))
        empty_text = ' '.join(_text(m.get('text') or m.get('message')) for m in messages)
        if messages and re.search(r"hasn't posted|no videos|没有视频|尚未發佈|尚未发布|未上[傳传]|no content", empty_text, re.I):
            return cid, YouTubeFeed(name, [], order_is_freshness=True, source='channel_page')
        raise YouTubeError('YouTube 视频列表格式发生变化，本次未更新检查记录。')
    timestamp = time.time() if now is None else now
    videos, seen, recognized = [], set(), 0
    # Only the selected channel tab. Never inspect home-page recommendations.
    for item in (grid or legacy_grid).get('contents', (grid or legacy_grid).get('items', [])):
        inner = item.get('richItemRenderer', {}).get('content', item) if isinstance(item, dict) else {}
        lockup = inner.get('lockupViewModel')
        renderer = inner.get('videoRenderer') or inner.get('gridVideoRenderer')
        labels = []
        if isinstance(lockup, dict) and lockup.get('contentType') == 'LOCKUP_CONTENT_TYPE_VIDEO':
            vid = str(lockup.get('contentId', ''))
            meta = lockup.get('metadata', {}).get('lockupMetadataViewModel', {})
            title = _text(meta.get('title'))
            labels = [text for parts in _walk(meta.get('metadata', {}), 'metadataParts') for part in parts if isinstance(part, dict)
                      for text in (_text(part.get('accessibilityLabel')), _text(part.get('text'))) if text]
            # Keep the label as supplied by the public page; it is approximate.
            published = next((x for x in labels if relative_timestamp(x, timestamp) is not None), '')
            duration = next((str(x.get('text', '')) for x in _walk(lockup.get('contentImage', {}), 'thumbnailBadgeViewModel') if re.fullmatch(r'\d+(?::\d{2}){1,2}', str(x.get('text', '')))), '')
        elif isinstance(renderer, dict):
            vid, title = str(renderer.get('videoId', '')), _text(renderer.get('title'))
            published, duration = _text(renderer.get('publishedTimeText')), _text(renderer.get('lengthText'))
        else:
            continue
        if not VIDEO_ID.fullmatch(vid) or vid in seen or not title:
            continue
        recognized += 1
        # Future premieres are not yet released videos.
        if any(re.search(r'scheduled|upcoming|premieres in|即将|即將|预定|預定', text, re.I) for text in
               [published, *(labels if isinstance(lockup, dict) else []), *(_text(x) for x in _walk(inner, 'label')),
                *(_text(x) for x in _walk(inner, 'badgeStyle'))]) or (isinstance(renderer, dict) and renderer.get('upcomingEventData')):
            continue
        seen.add(vid)
        stamp = relative_timestamp(published, timestamp)
        videos.append(YouTubeVideo('yt:' + vid, title, f'https://www.youtube.com/watch?v={vid}',
                                  stamp if stamp is not None else 0, name,
                                  f'https://i.ytimg.com/vi/{vid}/hqdefault.jpg', duration,
                                  published or '发布时间未提供', True, 'relative' if stamp is not None else 'unknown'))
    if not videos and not recognized and (grid or legacy_grid).get('contents', (grid or legacy_grid).get('items', [])):
        raise YouTubeError('未能识别 YouTube 频道里的视频，本次未更新检查记录。')
    return cid, YouTubeFeed(name, videos[:30], order_is_freshness=True, source='channel_page')


class YouTubeClient:
    def __init__(self, known_videos=None, metadata_budget=2):
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'Mozilla/5.0', 'Accept-Language': 'en-US,en;q=0.9'})
        self._lock = threading.RLock()
        self._rss_unavailable_until = {}
        self._publication_cache = {}
        self._publication_retry_at = {}
        self.metadata_budget = max(0, min(5, int(metadata_budget)))
        for video in known_videos or []:
            vid = youtube_video_id(video)
            if vid and video.get('published_at', 0) > 0 and (not video.get('timestamp_approximate', False) or video.get('published_precision') == 'day'):
                self._publication_cache[vid] = {key: video.get(key, default) for key, default in
                    (('published_at', 0), ('published_precision', 'second'), ('published_label', ''), ('timestamp_approximate', False))}

    def _get(self, url: str, *, feed=False, metadata=False):
        try:
            response = self.session.get(url, timeout=(4, 10) if metadata else (7, 22), allow_redirects=True)
        except requests.RequestException:
            raise YouTubeError('无法连接 YouTube，请确认本机网络或代理能正常访问 YouTube，稍后自动重试。') from None
        p = urlparse(response.url)
        if p.scheme != 'https' or p.hostname not in HOSTS:
            raise YouTubeError('YouTube 要求在官方页面完成同意或验证，请先在浏览器中处理。', False)
        if response.status_code in (403, 429):
            raise YouTubeError('YouTube 暂时限制了请求，请稍后再试，或在官方页面处理验证。')
        if response.status_code == 404 and feed:
            return None
        if response.status_code == 404:
            raise YouTubeError('没有找到这个 YouTube 频道，请核对主页链接或 @账号。', False)
        if response.status_code != 200:
            raise YouTubeError(f'YouTube 连接异常（HTTP {response.status_code}），请稍后再试。')
        if len(response.content) > 8_000_000:
            raise YouTubeError('YouTube 返回页面过大，已停止本次检查。')
        return response.text

    def resolve_channel(self, text: str) -> tuple[dict, YouTubeFeed]:
        cid, url = channel_input(text)
        with self._lock:
            if cid is not None:
                feed = self.fetch_videos(cid)
            else:
                cid, feed = parse_video_page(self._get(url))
                feed = self._try_atom(cid) or self._enrich_dates(feed, cid)
            return {'channel_id': cid, 'name': feed.name, 'url': f'https://www.youtube.com/channel/{cid}'}, feed

    def _try_atom(self, cid):
        if time.time() < self._rss_unavailable_until.get(cid, 0):
            return None
        try:
            xml = self._get(f'https://www.youtube.com/feeds/videos.xml?channel_id={cid}', feed=True)
            if xml is not None:
                return parse_atom(xml, cid)
        except YouTubeError:
            pass
        self._rss_unavailable_until[cid] = time.time() + 3600
        return None

    def _enrich_dates(self, feed, cid):
        budget, videos = self.metadata_budget, []
        for video in feed.videos:
            vid = video.key[3:]
            cached = self._publication_cache.get(vid)
            if cached is None and budget and time.time() >= self._publication_retry_at.get(vid, 0):
                budget -= 1
                try:
                    cached = parse_video_publication(self._get(f'https://www.youtube.com/watch?v={vid}&hl=en', metadata=True), vid, cid)
                    self._publication_cache[vid] = cached
                except YouTubeError:
                    self._publication_retry_at[vid] = time.time() + 1800
            videos.append(replace(video, **cached) if cached else video)
        return replace(feed, videos=videos)

    def fetch_videos(self, cid: str, since_ts=None) -> YouTubeFeed:
        channel_key(cid)
        with self._lock:
            atom = self._try_atom(cid)
            if atom is not None:
                return atom
            _, feed = parse_video_page(self._get(f'https://www.youtube.com/channel/{cid}/videos?hl=en'), cid)
            feed = self._enrich_dates(feed, cid)
            if since_ts and feed.videos and all(v.published_at > since_ts for v in feed.videos):
                return YouTubeFeed(feed.name, feed.videos, True, True, feed.source)
            return feed
