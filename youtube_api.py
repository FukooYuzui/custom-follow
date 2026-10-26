"""Read public YouTube channel updates, without keys or account credentials.

Prefer the official Atom feed. If that endpoint is unavailable, read only the
selected channel's public Videos tab. Page-derived publication times are marked
approximate; update detection then uses the newest-first order and seen IDs.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
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


def initial_data(html: str) -> dict:
    for match in re.finditer(r'(?:var\s+)?ytInitialData\s*=\s*', html):
        try:
            value, _ = json.JSONDecoder().raw_decode(html[match.end():].lstrip())
            if isinstance(value, dict):
                return value
        except ValueError:
            continue
    raise YouTubeError('YouTube 未返回可读取的频道列表。可能需要在官方页面处理验证，或页面格式已变化。')


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
    match = re.search(r'(\d+)\s*(second|minute|hour|day|week|month|year)s?\s+ago', value)
    if match:
        seconds = {'second': 1, 'minute': 60, 'hour': 3600, 'day': 86400,
                   'week': 604800, 'month': 2592000, 'year': 31536000}[match[2]]
        return max(0, now - int(match[1]) * seconds)
    # A page may ignore hl=en; accept common simplified/traditional labels too.
    match = re.search(r'(\d+)\s*(秒|分钟|分鐘|小时|小時|天|週|周|个月|個月|年)前', value)
    if match:
        seconds = {'秒': 1, '分钟': 60, '分鐘': 60, '小时': 3600, '小時': 3600,
                   '天': 86400, '週': 604800, '周': 604800, '个月': 2592000, '個月': 2592000, '年': 31536000}[match[2]]
        return max(0, now - int(match[1]) * seconds)
    return None


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
    videos, seen = [], set()
    # Only the selected channel tab. Never inspect home-page recommendations.
    for item in (grid or legacy_grid).get('contents', (grid or legacy_grid).get('items', [])):
        inner = item.get('richItemRenderer', {}).get('content', item) if isinstance(item, dict) else {}
        lockup = inner.get('lockupViewModel')
        renderer = inner.get('videoRenderer') or inner.get('gridVideoRenderer')
        if isinstance(lockup, dict) and lockup.get('contentType') == 'LOCKUP_CONTENT_TYPE_VIDEO':
            vid = str(lockup.get('contentId', ''))
            meta = lockup.get('metadata', {}).get('lockupMetadataViewModel', {})
            title = _text(meta.get('title'))
            labels = [_text(part.get('text')) for parts in _walk(meta.get('metadata', {}), 'metadataParts') for part in parts if isinstance(part, dict)]
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
        # Future premieres are not yet released videos.
        if any('scheduled' in text.lower() or 'upcoming' in text.lower() for text in [published, *(_text(x) for x in _walk(inner, 'label'))]):
            continue
        seen.add(vid)
        stamp = relative_timestamp(published, timestamp)
        videos.append(YouTubeVideo('yt:' + vid, title, f'https://www.youtube.com/watch?v={vid}',
                                  stamp if stamp is not None else timestamp - len(videos), name,
                                  f'https://i.ytimg.com/vi/{vid}/hqdefault.jpg', duration,
                                  published or '发布时间未提供', True))
    if not videos and (grid or legacy_grid).get('contents', (grid or legacy_grid).get('items', [])):
        raise YouTubeError('未能识别 YouTube 频道里的视频，本次未更新检查记录。')
    return cid, YouTubeFeed(name, videos[:30], order_is_freshness=True, source='channel_page')


class YouTubeClient:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'Mozilla/5.0', 'Accept-Language': 'en-US,en;q=0.9'})
        self._lock = threading.RLock()
        self._rss_unavailable_until = {}

    def _get(self, url: str, *, feed=False):
        try:
            response = self.session.get(url, timeout=(7, 22), allow_redirects=True)
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
                self._rss_unavailable_until[cid] = time.time() + 3600
            return {'channel_id': cid, 'name': feed.name, 'url': f'https://www.youtube.com/channel/{cid}'}, feed

    def fetch_videos(self, cid: str, since_ts=None) -> YouTubeFeed:
        channel_key(cid)
        with self._lock:
            if time.time() >= self._rss_unavailable_until.get(cid, 0):
                xml = self._get(f'https://www.youtube.com/feeds/videos.xml?channel_id={cid}', feed=True)
                if xml is not None:
                    try:
                        return parse_atom(xml, cid)
                    except YouTubeError:
                        pass
                self._rss_unavailable_until[cid] = time.time() + 3600
            _, feed = parse_video_page(self._get(f'https://www.youtube.com/channel/{cid}/videos?hl=en'), cid)
            if since_ts and feed.videos and all(v.published_at > since_ts for v in feed.videos):
                return YouTubeFeed(feed.name, feed.videos, True, True, feed.source)
            return feed
