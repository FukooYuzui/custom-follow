"""Read-only Bilibili client. No credential discovery or risk-control bypass.

Protocol references (community-maintained, not a guaranteed public API):
https://github.com/bilibili-plugins/bilibili-api-collect/blob/master/docs/dynamic/space.md
https://github.com/bilibili-plugins/bilibili-api-collect/blob/master/docs/login/login_action/QR.md
"""
from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import re
import threading
import time
from urllib.parse import parse_qs, urlparse

import requests


class BiliError(Exception):
    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class Video:
    key: str
    title: str
    url: str
    published_at: int
    author: str
    cover: str = ""
    duration: str = ""


@dataclass(frozen=True)
class Feed:
    name: str
    videos: list[Video]
    has_more: bool = False


def _uid(value: object) -> str:
    value = str(value).strip()
    if not re.fullmatch(r"[1-9][0-9]{0,19}", value):
        raise BiliError("请输入正确的 UP 主 UID（大于零的数字）。", retryable=False)
    return value


def _number(value: object) -> int:
    try:
        return int(value)
    except (ValueError, TypeError, OverflowError):
        return 0


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def _plain_text(value: object, limit: int = 200) -> str:
    parser = _PlainText()
    parser.feed(str(value or ""))
    return " ".join("".join(parser.parts).split())[:limit]


def _author(item: dict) -> dict:
    modules = item.get("modules")
    if not isinstance(modules, dict):
        return {}
    author = modules.get("module_author")
    return author if isinstance(author, dict) else {}


def _is_pinned(item: dict) -> bool:
    modules = item.get("modules") or {}
    tag = modules.get("module_tag") or {}
    return bool(item.get("is_top")) or "置顶" in str(tag.get("text", ""))


def parse_video(item: dict, uid: str) -> Video | None:
    """Only direct archive posts by the requested creator count as videos.

    A forwarded video lives under ``orig`` and is deliberately never visited.
    Generate the destination from a validated BVID, not a remote jump URL.
    """
    author = _author(item)
    if item.get("type") != "DYNAMIC_TYPE_AV" or str(author.get("mid")) != uid:
        return None
    if item.get("visible") is False:
        return None
    modules = item.get("modules") or {}
    dynamic = modules.get("module_dynamic") or {}
    major = dynamic.get("major") or {}
    if not isinstance(major, dict) or major.get("type") != "MAJOR_TYPE_ARCHIVE":
        return None
    archive = major.get("archive")
    if not isinstance(archive, dict):
        raise BiliError("B站返回的视频格式发生变化，请稍后重试或更新程序。")
    bvid = str(archive.get("bvid", ""))
    title = str(archive.get("title") or "").strip()
    timestamp = _number(author.get("pub_ts"))
    if not re.fullmatch(r"BV[0-9A-Za-z]{10}", bvid) or not title or timestamp <= 0:
        raise BiliError("B站返回的视频信息不完整，本次未更新检查记录。")
    cover = str(archive.get("cover") or "")
    if cover.startswith("//"):
        cover = "https:" + cover
    elif cover.startswith("http://"):
        cover = "https://" + cover[7:]
    cover_url = urlparse(cover)
    if cover_url.scheme not in ("http", "https") or not (
            (cover_url.hostname or "").endswith(".hdslb.com")
            or (cover_url.hostname or "").endswith(".bilibili.com")):
        cover = ""
    return Video(bvid, title, f"https://www.bilibili.com/video/{bvid}",
                 timestamp, str(author.get("name") or f"UP主 {uid}"),
                 cover, str(archive.get("duration_text") or ""))


class BiliClient:
    API = "https://api.bilibili.com"
    PASSPORT = "https://passport.bilibili.com"
    COOKIE_NAMES = frozenset({"SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5",
                              "sid", "buvid3", "buvid4", "b_nut"})
    PAGE_DELAY = 1.5
    MAX_PAGES = 5

    def __init__(self, cookies: dict | None = None):
        self._lock = threading.RLock()
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.bilibili.com/",
        })
        for name, value in (cookies or {}).items():
            if name in self.COOKIE_NAMES and isinstance(value, str):
                self.session.cookies.set(name, value, domain=".bilibili.com", path="/", secure=True)

    def close(self) -> None:
        with self._lock:
            self.session.close()

    @staticmethod
    def _api_error(code: int) -> BiliError:
        if code in (-101, -111, -4003):
            return BiliError("B站要求登录或登录已失效，请在程序中扫码登录后重新检查。", False)
        if code in (-352, -412, -799, -401, 429):
            return BiliError("B站暂时限制了请求，请降低检查频率，稍后再试；未登录时可先扫码登录。")
        if code in (-404, 62002):
            return BiliError("未找到这个 UP 主，或其主页暂时不可访问，请核对 UID。", False)
        if code in (-403, 403):
            return BiliError("B站拒绝访问，请在浏览器确认账号状态，并稍后再试。")
        return BiliError(f"B站接口暂不可用（返回码 {code}），请稍后再试。")

    def _get(self, path: str, params: dict | None = None, *, passport: bool = False,
             allow_logged_out: bool = False) -> dict:
        """One request, no automatic retries; the monitor schedules backoff."""
        with self._lock:
            try:
                response = self.session.get((self.PASSPORT if passport else self.API) + path,
                                            params=params, timeout=(6, 18))
            except requests.Timeout:
                raise BiliError("连接 B站超时，请检查网络，稍后自动重试。") from None
            except requests.RequestException:
                # RequestException can contain a login key or URL. Do not expose it.
                raise BiliError("无法连接 B站，请检查网络或代理设置，稍后自动重试。") from None
            if response.status_code in (412, 429):
                raise BiliError("B站暂时限制了请求，请降低检查频率，稍后再试；未登录时可先扫码登录。")
            if response.status_code == 401:
                raise BiliError("B站要求登录，请在程序中扫码登录后重试。", False)
            if response.status_code == 403:
                raise BiliError("B站拒绝访问，请在浏览器确认账号状态，并稍后再试。")
            if response.status_code != 200:
                raise BiliError(f"B站连接异常（HTTP {response.status_code}），请稍后再试。")
            try:
                payload = response.json()
            except ValueError:
                raise BiliError("B站未返回有效数据，可能需要在浏览器完成验证，请稍后再试。") from None
            if not isinstance(payload, dict) or "code" not in payload:
                raise BiliError("B站接口格式发生变化，请稍后重试或更新程序。")
            code = _number(payload["code"])
            if code != 0 and not (allow_logged_out and code == -101):
                raise self._api_error(code)
            return payload

    def profile(self, uid: object) -> str:
        uid = _uid(uid)
        payload = self._get("/x/web-interface/card", {"mid": uid})
        card = (payload.get("data") or {}).get("card") or {}
        if not card.get("name") or str(card.get("mid")) != uid:
            raise BiliError("未找到这个 UP 主的资料，请核对 UID。", False)
        return str(card["name"])

    def search_users(self, keyword: str) -> list[dict]:
        """One explicit nickname search; never subscribes to a result."""
        keyword = str(keyword).strip()
        if not 1 <= len(keyword) <= 50 or re.search(r"[\x00-\x1f\x7f]", keyword):
            raise BiliError("UP 主昵称请填写 1 至 50 个字符。", False)
        if re.search(r"(?:[a-z][a-z0-9+.-]*://|www\.|[a-z0-9-]+\.[a-z]{2,}(?:/|$))", keyword, re.I):
            raise BiliError("请填写 UP 主昵称；添加链接请使用 space.bilibili.com 个人主页链接。", False)
        payload = self._get("/x/web-interface/search/type", {
            "search_type": "bili_user", "keyword": keyword, "page": 1,
        })
        data = payload.get("data")
        if not isinstance(data, dict):
            raise BiliError("B站未返回有效的 UP 主搜索结果，请稍后再试。")
        results = data.get("result", [])
        if not isinstance(results, list):
            raise BiliError("B站 UP 主搜索结果格式发生变化，请使用 UID 或主页链接添加。")
        users = []
        seen = set()
        for item in results:
            if not isinstance(item, dict):
                continue
            mid = str(item.get("mid") or "")
            if not re.fullmatch(r"[1-9][0-9]{0,18}", mid) or int(mid) > 2**63 - 1 or mid in seen:
                continue
            name = _plain_text(item.get("uname"), 80)
            if not name:
                continue
            official = item.get("official_verify")
            official = official if isinstance(official, dict) else {}
            description = _plain_text(official.get("desc") or item.get("usign") or "", 160)
            users.append({"uid": mid, "name": name, "description": description})
            seen.add(mid)
            if len(users) == 8:
                break
        return users

    def fetch_videos(self, uid: object, since_ts: int | None = None) -> Feed:
        """Fetch a first baseline page, or up to five pages back to a timestamp.

        ``has_more`` is true when unread older pages remain. In a catch-up check
        this means the caller should show a possible history gap. Pinned items
        do not decide the timestamp boundary. This is a polling API, not push.
        """
        uid = _uid(uid)
        with self._lock:
            videos: dict[str, Video] = {}
            name = f"UP主 {uid}"
            offset = ""
            offsets = set()
            has_more = False
            pages = 1 if since_ts is None else self.MAX_PAGES
            for page in range(pages):
                if page:
                    time.sleep(self.PAGE_DELAY)
                payload = self._get("/x/polymer/web-dynamic/v1/feed/space", {
                    "host_mid": uid, "offset": offset, "timezone_offset": -480,
                    "platform": "web", "type": "video",
                })
                data = payload.get("data")
                if not isinstance(data, dict) or not isinstance(data.get("items"), list):
                    raise BiliError("B站动态列表格式异常，本次未更新检查记录。")
                timestamps = []
                for item in data["items"]:
                    if not isinstance(item, dict):
                        raise BiliError("B站动态列表格式异常，本次未更新检查记录。")
                    author = _author(item)
                    if str(author.get("mid")) == uid:
                        name = str(author.get("name") or name)
                        timestamp = _number(author.get("pub_ts"))
                        if timestamp > 0 and not _is_pinned(item):
                            timestamps.append(timestamp)
                    video = parse_video(item, uid)
                    if video is not None:
                        videos.setdefault(video.key, video)
                has_more = bool(data.get("has_more"))
                # Strictly older avoids cutting off posts with equal timestamps.
                if since_ts is not None and timestamps and min(timestamps) < since_ts:
                    has_more = False
                    break
                if not has_more:
                    break
                next_offset = str(data.get("offset") or "")
                if not next_offset or next_offset in offsets:
                    # Keep has_more=True so callers can expose the incomplete catch-up.
                    break
                offsets.add(next_offset)
                offset = next_offset
            return Feed(name, sorted(videos.values(), key=lambda video: (video.published_at, video.key)),
                        has_more)

    def create_qr(self) -> dict:
        data = self._get("/x/passport-login/web/qrcode/generate", passport=True).get("data") or {}
        url = data.get("url")
        key = data.get("qrcode_key")
        if not isinstance(url, str) or not isinstance(key, str) or not key:
            raise BiliError("B站未返回登录二维码，请稍后重试。")
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in {"passport.bilibili.com", "account.bilibili.com"}:
            raise BiliError("登录二维码地址异常，本次已停止登录。", False)
        return {"url": url, "qrcode_key": key}

    def poll_qr(self, key: str) -> dict:
        if not isinstance(key, str) or not re.fullmatch(r"[0-9A-Za-z_-]{8,128}", key):
            raise BiliError("登录二维码已失效，请重新生成。", False)
        with self._lock:
            data = self._get("/x/passport-login/web/qrcode/poll", {"qrcode_key": key},
                             passport=True).get("data") or {}
            status = {86101: "waiting", 86090: "scanned", 86038: "expired"}.get(_number(data.get("code")))
            if status:
                return {"status": status}
            if data.get("code") != 0:
                raise BiliError("二维码登录状态异常，请重新生成二维码。", False)
            cookies = {cookie.name: cookie.value for cookie in self.session.cookies
                       if cookie.name in self.COOKIE_NAMES
                       and (cookie.domain.lstrip(".") == "bilibili.com"
                            or cookie.domain.endswith(".bilibili.com"))}
            # Some API versions put the same credential in a cross-domain URL.
            # Parse it locally; never navigate to it or print it.
            parsed = urlparse(str(data.get("url") or ""))
            if parsed.scheme == "https" and parsed.hostname in {"passport.biligame.com", "passport.bilibili.com"}:
                for name, values in parse_qs(parsed.query).items():
                    if name in self.COOKIE_NAMES and values and name not in cookies:
                        cookies[name] = values[0]
                        self.session.cookies.set(name, values[0], domain=".bilibili.com", path="/", secure=True)
            if not cookies.get("SESSDATA"):
                raise BiliError("扫码已确认，但未取得登录凭据，请重新扫码。", False)
            return {"status": "success", "cookies": cookies}

    def check_login(self) -> str | None:
        payload = self._get("/x/web-interface/nav", allow_logged_out=True)
        if _number(payload.get("code")) == -101:
            return None
        data = payload.get("data") or {}
        if not data.get("isLogin"):
            return None
        return str(data.get("uname") or "已登录用户")
