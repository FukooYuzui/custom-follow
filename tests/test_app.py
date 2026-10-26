"""Application contract tests: no network, GUI, registry writes, or real user data."""
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import Mock, patch

import main
from bili_api import BiliError, Feed, Video


OLD = "BV1xx411c7Ug"
NEW = "BV1bx411w7SF"


def feed(bvid=OLD, timestamp=90):
    return Feed("测试作者", [Video(bvid, "视频标题", f"https://www.bilibili.com/video/{bvid}", timestamp, "测试作者")])


class AppTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        for name, value in (("DATA", Path(directory.name)), ("load_session", Mock(return_value={})),
                            ("get_autostart", Mock(return_value=False))):
            patcher = patch.object(main, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = Mock()
        self.client.check_login.return_value = None
        self.client.fetch_videos.return_value = feed()
        patcher = patch.object(main, "BiliClient", return_value=self.client)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.app = main.Application()
        self.app.changed = Mock()
        self.app.notify = Mock()
        self.api = main.Api(self.app)

    def run_worker_once(self):
        # Stop after one scheduling round, with no actual sleep or threads.
        self.app.stopping.wait = Mock(return_value=False)
        self.app.wake.wait = Mock(side_effect=lambda *args: self.app.stopping.set())
        self.app.worker()

    def add_and_initialize(self):
        self.app.store.add_channel("42")
        self.app.store.ingest("42", feed(), now=100)

    def test_clean_install_has_no_example_up_or_videos(self):
        self.assertEqual([], self.api.snapshot()["channels"])
        self.assertEqual([], self.api.snapshot()["videos"])

    def test_add_rejects_unrelated_link_and_duplicate_subscription(self):
        self.assertFalse(self.api.add_channel("https://www.bilibili.com/video/" + OLD)["ok"])
        self.assertTrue(self.api.add_channel("https://space.bilibili.com/42")["ok"])
        self.assertFalse(self.api.add_channel("42")["ok"])
        self.assertEqual(1, len(self.api.snapshot()["channels"]))

    def test_only_owned_list_videos_can_be_played_or_opened(self):
        self.add_and_initialize()
        self.assertFalse(self.api.play_video(NEW)["ok"])
        with patch.object(main.webbrowser, "open") as browser:
            self.api.open_external(NEW)
            browser.assert_not_called()
            self.api.open_external(OLD)
            browser.assert_called_once_with("https://www.bilibili.com/video/" + OLD)
        self.api.remove_channel("42")
        self.assertFalse(self.api.play_video(OLD)["ok"])

    def test_play_marks_new_video_read(self):
        self.add_and_initialize()
        self.app.store.ingest("42", feed(NEW, 110), now=120)
        self.assertFalse(self.app.store.snapshot()["videos"][0]["read"])
        result = self.api.play_video(NEW)
        self.assertTrue(result["ok"])
        self.assertEqual(NEW, result["bvid"])
        self.assertTrue(self.app.store.snapshot()["videos"][0]["read"])

    def test_bad_settings_are_reported_without_mutation(self):
        before = self.app.store.snapshot()["settings"]
        result = self.api.save_settings({"interval_minutes": 1})
        self.assertFalse(result["ok"])
        self.assertEqual(before, self.app.store.snapshot()["settings"])

    def test_network_failure_preserves_successful_boundary(self):
        self.add_and_initialize()
        self.app.store.record_error("42", "断网", now=200)
        self.client.fetch_videos.return_value = feed(NEW, 110)
        self.run_worker_once()
        self.client.fetch_videos.assert_called_once_with("42", since_ts=100)
        self.assertEqual(0, self.app.store.snapshot()["channels"][0]["failures"])
        queued = self.app.notifications.get_nowait()
        self.assertEqual([NEW], [video["key"] for video in queued])

    def test_pause_during_request_ignores_inflight_result(self):
        self.add_and_initialize()
        def complete_after_pause(*args, **kwargs):
            self.app.store.update_settings({"paused": True})
            return feed(NEW, 110)
        self.client.fetch_videos.side_effect = complete_after_pause
        self.run_worker_once()
        self.app.notify.assert_not_called()
        self.assertTrue(self.app.notifications.empty())
        self.assertEqual([OLD], [video["key"] for video in self.app.store.snapshot()["videos"]])
        self.assertEqual(100, self.app.store.snapshot()["channels"][0]["last_success_at"])

    def test_removal_during_request_never_restores_subscription(self):
        self.add_and_initialize()
        def complete_after_removal(*args, **kwargs):
            self.app.store.remove_channel("42")
            return feed(NEW, 110)
        self.client.fetch_videos.side_effect = complete_after_removal
        self.run_worker_once()
        self.app.notify.assert_not_called()
        self.assertTrue(self.app.notifications.empty())
        self.assertEqual([], self.app.store.snapshot()["channels"])
        self.assertEqual([], self.app.store.snapshot()["videos"])

    def test_quit_during_request_does_not_notify_or_write_late_result(self):
        self.add_and_initialize()
        def complete_after_quit(*args, **kwargs):
            self.app.quitting = True
            self.app.stopping.set()
            return feed(NEW, 110)
        self.client.fetch_videos.side_effect = complete_after_quit
        self.run_worker_once()
        self.app.notify.assert_not_called()
        self.assertTrue(self.app.notifications.empty())
        self.assertEqual(100, self.app.store.snapshot()["channels"][0]["last_success_at"])

    def test_login_change_discards_old_client_error(self):
        self.add_and_initialize()
        def fail_old_client(*args, **kwargs):
            self.app.client = Mock()
            self.app.store.request_check(reset_errors=True)
            raise BiliError("旧会话过期")
        self.client.fetch_videos.side_effect = fail_old_client
        self.run_worker_once()
        state = self.app.store.snapshot()["channels"][0]
        self.assertEqual(0, state["failures"])
        self.assertEqual("", state["error"])
        self.assertEqual(0, state["next_check"])

    def test_notification_failure_does_not_turn_success_into_network_backoff(self):
        self.add_and_initialize()
        self.client.fetch_videos.return_value = feed(NEW, 110)
        self.app.notify.side_effect = RuntimeError("通知窗口已关闭")
        with patch.object(main.logging, "exception"):
            self.run_worker_once()
            self.app.stopping.clear()
            self.app.stopping.wait = Mock(side_effect=lambda *args: self.app.stopping.set())
            self.app.notification_worker()
        channel = self.app.store.snapshot()["channels"][0]
        self.assertEqual(0, channel["failures"])
        self.assertEqual("", channel["error"])
        self.assertEqual(NEW, self.app.store.snapshot()["videos"][0]["key"])
        self.assertIn("保存在视频列表", self.app.warning)

    def test_close_with_tray_stops_player_and_keeps_process(self):
        self.app.tray_ok = True
        self.app.window = Mock()
        self.app.emit = Mock()
        self.assertFalse(self.app.close())
        self.app.emit.assert_called_once_with("stopPlayer", None)
        self.app.window.hide.assert_called_once()
        self.assertFalse(self.app.stopping.is_set())


if __name__ == "__main__":
    unittest.main()
