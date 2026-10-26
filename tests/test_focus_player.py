from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

import focus_player
from webview.event import Event
from focus_player import FocusPlayer, LOGIN_COOKIES, allowed_navigation, video_url


BV = "BV1B7411m7LV"


class FocusPlayerTests(unittest.TestCase):
    def test_exact_video_url_and_part_validation(self):
        self.assertEqual(f"https://www.bilibili.com/video/{BV}/?p=2", video_url(BV, 2))
        for bad in ("https://evil.test/", BV + "?p=2", "BVabc", 2):
            with self.assertRaises(ValueError):
                video_url(bad)
        for bad in (0, 1000, True, 1.1, "2"):
            with self.assertRaises(ValueError):
                video_url(BV, bad)

    def test_navigation_stays_in_selected_video_or_official_login(self):
        for allowed in (
            video_url(BV), f"https://www.bilibili.com/video/{BV}?p=3",
            "https://passport.bilibili.com/login", "https://account.bilibili.com/h5/login",
        ):
            self.assertTrue(allowed_navigation(allowed, BV), allowed)
        for blocked in (
            "https://www.bilibili.com/", "https://www.bilibili.com/video/BV1xx411c7mD/",
            "https://passport.bilibili.com.evil.test/login", "https://evil.test/",
            f"https://www.bilibili.com:444/video/{BV}", f"http://www.bilibili.com/video/{BV}",
            f"https://user@www.bilibili.com/video/{BV}", "file:///C:/secret", "bilibili://video/1",
            f"https://www.bilibili.com/video/{BV}/../", "https://[invalid/",
        ):
            self.assertFalse(allowed_navigation(blocked, BV), blocked)

    def test_cookie_sync_uses_native_manager_and_allowlist(self):
        core = Mock()
        created = []
        def create(name, value, domain, path):
            cookie = Mock(Name=name)
            created.append((name, value, domain, path, cookie))
            return cookie
        core.CookieManager.CreateCookie.side_effect = create
        count = focus_player._sync_cookies(core, {"SESSDATA": "opaque-test-token", "bili_jct": "csrf-test", "unknown": "ignored"})
        self.assertEqual(count, 2)
        self.assertEqual({v[0] for v in created}, {"SESSDATA", "bili_jct"})
        self.assertTrue(all(v[2:4] == (".bilibili.com", "/") and v[4].IsSecure for v in created))
        self.assertTrue(next(v[4] for v in created if v[0] == "SESSDATA").IsHttpOnly)
        core.ExecuteScriptAsync.assert_not_called()
        core.Navigate.assert_not_called()

    def test_official_festival_redirect_must_keep_exact_selected_video(self):
        self.assertTrue(allowed_navigation(f"https://www.bilibili.com/festival/NobelPrize2026?bvid={BV}", BV))
        self.assertTrue(allowed_navigation(f"https://www.bilibili.com/festival/official-event/?p=2&bvid={BV}", BV))
        for blocked in (
            "https://www.bilibili.com/festival/NobelPrize2026",
            "https://www.bilibili.com/festival/NobelPrize2026?bvid=",
            "https://www.bilibili.com/festival/NobelPrize2026?bvid=BV1xx411c7mD",
            f"https://www.bilibili.com/festival/NobelPrize2026?bvid={BV}&bvid={BV}",
            f"https://www.bilibili.com/festival/NobelPrize2026?bvid={BV}&bvid=",
            f"https://www.bilibili.com/festival/../other?bvid={BV}",
            f"https://www.bilibili.com/festival/example/nested?bvid={BV}",
            f"https://www.bilibili.com.evil.test/festival/NobelPrize2026?bvid={BV}",
        ):
            self.assertFalse(allowed_navigation(blocked, BV), blocked)

    def test_cookie_cleanup_does_not_clear_entire_profile(self):
        core = Mock()
        focus_player._delete_login_cookies(core)
        self.assertEqual(core.CookieManager.DeleteCookies.call_count, len(LOGIN_COOKIES) * 2)
        core.CookieManager.DeleteAllCookies.assert_not_called()
        self.assertTrue(all(call.args[0] in LOGIN_COOKIES for call in core.CookieManager.DeleteCookies.call_args_list))

    def test_blocked_diagnostics_only_expose_valid_bvid_not_arbitrary_query(self):
        manager = FocusPlayer(Path("unused"), Mock(return_value={}), Mock())
        core = Mock()
        manager._blocked_notice(core, f"https://www.bilibili.com/festival/example?bvid={BV}&secret=do-not-store#private")
        self.assertEqual(manager.last_blocked_route, "https://www.bilibili.com/festival/example")
        self.assertEqual(manager.last_blocked_video_code, BV)
        manager._blocked_notice(core, "https://www.bilibili.com/festival/example?bvid=not-a-video&secret=do-not-store")
        self.assertEqual(manager.last_blocked_video_code, "")

    def test_remote_window_never_receives_application_api(self):
        with TemporaryDirectory() as temp:
            Path(temp, "focus_mode.js").write_text("/* document-start focus */", encoding="utf-8")
            manager = FocusPlayer(Path(temp), Mock(return_value={}), Mock())
            window = Mock()
            window.events.closed = Event(window)
            with patch.object(focus_player.webview, "create_window", return_value=window) as create, \
                    patch.object(focus_player.threading, "Thread"), \
                    patch.object(focus_player, 'close_window') as close:
                manager.open(BV, "测试标题", 1)
                args = create.call_args.kwargs
                self.assertIsNone(args["js_api"])
                self.assertNotIn("url", args)
                self.assertIn("正在准备", args["html"])
                self.assertEqual(create.call_count, 1)
                manager.open(BV, "同一窗口", 2)
                self.assertEqual(create.call_count, 1)
                window.expose.assert_not_called()
                manager.close()
                close.assert_called_once_with(window)
                window.destroy.assert_not_called()

    def test_closed_window_can_be_created_again(self):
        with TemporaryDirectory() as temp:
            Path(temp, "focus_mode.js").write_text("/* focus */", encoding="utf-8")
            manager = FocusPlayer(Path(temp), Mock(return_value={}), Mock())
            first, second = Mock(), Mock()
            first.events.closed = Event(first)
            second.events.closed = Event(second)
            with patch.object(focus_player.webview, "create_window", side_effect=[first, second]) as create, \
                    patch.object(focus_player.threading, "Thread"):
                manager.open(BV)
                manager._on_closed(first)
                manager.open(BV)
                self.assertEqual(create.call_count, 2)
                self.assertIs(manager._window, second)

    def test_clear_login_is_deferred_when_window_not_open(self):
        manager = FocusPlayer(Path("unused"), Mock(return_value={}), Mock())
        with patch.object(focus_player.webview, "windows", []):
            manager.clear_login()
        self.assertTrue(manager._pending_clear)

    def test_logout_cleans_shared_profile_even_when_player_is_closed(self):
        manager = FocusPlayer(Path("unused"), Mock(return_value={}), Mock())
        local_window = Mock()
        local_window.gui.renderer = "edgechromium"
        with patch.object(focus_player.webview, "windows", [local_window]), \
                patch.object(manager, "_ui", side_effect=lambda window, callback: callback()):
            manager.clear_login()
        self.assertFalse(manager._pending_clear)
        self.assertEqual(local_window.native.browser.webview.CoreWebView2.CookieManager.DeleteCookies.call_count,
                         len(LOGIN_COOKIES) * 2)

    def test_return_closes_player_before_returning_to_local_app(self):
        on_return = Mock()
        manager = FocusPlayer(Path("unused"), Mock(return_value={}), on_return)
        manager.close = Mock()
        manager._return()
        manager.close.assert_called_once()
        on_return.assert_called_once()


if __name__ == "__main__":
    unittest.main()
