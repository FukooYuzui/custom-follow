"""Deterministic protocol tests; no Bilibili requests are made here."""
import unittest
from unittest.mock import Mock

import requests

from bili_api import BiliClient, BiliError, parse_video


def video_item(bvid="BV1xx411c7Ug", timestamp=200, *, pinned=False, mid=42):
    item = {"type": "DYNAMIC_TYPE_AV", "visible": True, "modules": {
        "module_author": {"mid": mid, "name": "示例 UP 主", "pub_ts": timestamp},
        "module_dynamic": {"major": {"type": "MAJOR_TYPE_ARCHIVE", "archive": {
            "bvid": bvid, "title": "一条新视频", "jump_url": "https://untrusted.example/"}}}}}
    if pinned:
        item["modules"]["module_tag"] = {"text": "置顶"}
    return item


def page(items, more=False, offset=""):
    return {"code": 0, "data": {"items": items, "has_more": more, "offset": offset}}


class ParsingTests(unittest.TestCase):
    def test_direct_video_and_safe_destination(self):
        video = parse_video(video_item(), "42")
        self.assertEqual(video.key, "BV1xx411c7Ug")
        self.assertEqual(video.url, "https://www.bilibili.com/video/BV1xx411c7Ug")
        self.assertEqual(video.published_at, 200)

    def test_forward_wrong_author_and_hidden_are_not_new_video(self):
        forwarded = video_item()
        forwarded["type"] = "DYNAMIC_TYPE_FORWARD"
        forwarded["orig"] = video_item()
        hidden = video_item()
        hidden["visible"] = False
        for item in (forwarded, video_item(mid=43), hidden):
            self.assertIsNone(parse_video(item, "42"))

    def test_changed_video_shape_is_not_silent_success(self):
        item = video_item()
        item["modules"]["module_dynamic"]["major"]["archive"]["bvid"] = "bad"
        with self.assertRaises(BiliError):
            parse_video(item, "42")

    def test_pinned_old_item_does_not_cut_off_new_pages(self):
        client = BiliClient()
        client.PAGE_DELAY = 0
        client._get = Mock(side_effect=[
            page([video_item(timestamp=10, pinned=True), video_item("BV1bx411w7SF", 200)], True, "a"),
            page([video_item("BV1ax411w7SF", 150), video_item("BV1cx411w7SF", 90)], True, "b")])
        feed = client.fetch_videos("42", since_ts=100)
        self.assertEqual(client._get.call_count, 2)
        self.assertEqual(len(feed.videos), 4)
        self.assertFalse(feed.has_more)

    def test_initial_baseline_only_one_page(self):
        client = BiliClient()
        client._get = Mock(return_value=page([video_item()], True, "a"))
        self.assertTrue(client.fetch_videos(42).has_more)
        self.assertEqual(client._get.call_count, 1)

    def test_page_cap_and_deduplication(self):
        client = BiliClient()
        client.PAGE_DELAY = 0
        client._get = Mock(side_effect=[page([video_item()], True, str(i)) for i in range(5)])
        feed = client.fetch_videos(42, since_ts=1)
        self.assertEqual(client._get.call_count, 5)
        self.assertEqual(len(feed.videos), 1)
        self.assertTrue(feed.has_more)

    def test_equal_timestamp_does_not_cut_off(self):
        client = BiliClient()
        client.PAGE_DELAY = 0
        client._get = Mock(side_effect=[page([video_item(timestamp=100)], True, "a"),
                                       page([video_item("BV1bx411w7SF", 100)])])
        self.assertEqual(len(client.fetch_videos(42, 100).videos), 2)

    def test_invalid_page_never_becomes_empty_success(self):
        client = BiliClient()
        client._get = Mock(return_value={"code": 0, "data": {}})
        with self.assertRaises(BiliError):
            client.fetch_videos(42)

    def test_repeated_offset_stops_and_exposes_incomplete_history(self):
        client = BiliClient()
        client.PAGE_DELAY = 0
        client._get = Mock(return_value=page([video_item()], True, "same"))
        self.assertTrue(client.fetch_videos(42, 1).has_more)
        self.assertEqual(client._get.call_count, 2)


class RequestAndLoginTests(unittest.TestCase):
    def test_user_search_strips_highlight_and_deduplicates(self):
        client = BiliClient()
        client._get = Mock(return_value={"code": 0, "data": {"result": [
            {"mid": 730732, "uname": '<em class="keyword">毕导</em>THU',
             "usign": "物理 &amp; 生活", "official_verify": {"desc": ""}},
            {"mid": 730732, "uname": "重复结果"},
            {"mid": "not-a-uid", "uname": "错误结果"},
        ]}})
        users = client.search_users("毕导")
        self.assertEqual(users, [{"uid": "730732", "name": "毕导THU", "description": "物理 & 生活"}])
        self.assertEqual(client._get.call_args.args[1]["search_type"], "bili_user")

    def test_user_search_is_bounded_and_can_be_empty(self):
        client = BiliClient()
        client._get = Mock(return_value={"code": 0, "data": {"result": [
            {"mid": i, "uname": str(i)} for i in range(1, 21)]}})
        self.assertEqual(len(client.search_users("示例")), 8)
        client._get.return_value = {"code": 0, "data": {"result": []}}
        self.assertEqual(client.search_users("示例"), [])

    def test_user_search_rejects_arbitrary_url_without_request(self):
        client = BiliClient()
        client._get = Mock()
        for value in ("https://example.com/x", "www.example.com", "", "a" * 51):
            with self.assertRaises(BiliError):
                client.search_users(value)
        client._get.assert_not_called()

    def test_http_risk_control_has_one_request_and_actionable_error(self):
        client = BiliClient()
        client.session.get = Mock(return_value=Mock(status_code=412))
        with self.assertRaisesRegex(BiliError, "降低检查频率"):
            client.profile(42)
        self.assertEqual(client.session.get.call_count, 1)

    def test_transport_error_does_not_leak_secrets(self):
        client = BiliClient()
        client.session.get = Mock(side_effect=requests.ConnectionError("SECRET KEY"))
        with self.assertRaises(BiliError) as error:
            client.profile(42)
        self.assertNotIn("SECRET", str(error.exception))

    def test_login_required_and_signed_out(self):
        client = BiliClient()
        response = Mock(status_code=200)
        response.json.return_value = {"code": -101, "data": {"isLogin": False}}
        client.session.get = Mock(return_value=response)
        self.assertIsNone(client.check_login())
        with self.assertRaises(BiliError) as error:
            client.profile(42)
        self.assertFalse(error.exception.retryable)

    def test_qr_wait_scan_expire(self):
        client = BiliClient()
        for code, state in ((86101, "waiting"), (86090, "scanned"), (86038, "expired")):
            client._get = Mock(return_value={"code": 0, "data": {"code": code}})
            self.assertEqual(client.poll_qr("fake-key-123")["status"], state)

    def test_qr_success_uses_cookie_and_excludes_unrelated_domain(self):
        client = BiliClient({"SESSDATA": "test-token"})
        client.session.cookies.set("sid", "unrelated", domain="example.org")
        client._get = Mock(return_value={"code": 0, "data": {"code": 0}})
        result = client.poll_qr("fake-key-123")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["cookies"], {"SESSDATA": "test-token"})

    def test_qr_url_is_validated(self):
        client = BiliClient()
        client._get = Mock(return_value={"code": 0, "data": {
            "url": "https://example.org/login", "qrcode_key": "fake-key-123"}})
        with self.assertRaises(BiliError):
            client.create_qr()

    def test_current_account_host_is_supported(self):
        client = BiliClient()
        client._get = Mock(return_value={"code": 0, "data": {
            "url": "https://account.bilibili.com/h5/account-h5/auth/scan-web?key=example",
            "qrcode_key": "fake-key-123"}})
        self.assertIn("account.bilibili.com", client.create_qr()["url"])

    def test_missing_session_is_not_success(self):
        client = BiliClient()
        client._get = Mock(return_value={"code": 0, "data": {"code": 0}})
        with self.assertRaises(BiliError):
            client.poll_qr("fake-key-123")


if __name__ == "__main__":
    unittest.main()
