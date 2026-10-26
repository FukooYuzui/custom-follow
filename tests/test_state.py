import json
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from state import Store, is_quiet, parse_uid


def video(key, published_at):
    return SimpleNamespace(key=key, title=f"视频 {key}", url=f"https://www.bilibili.com/video/{key}", published_at=published_at, author="作者")


def feed(*videos):
    return SimpleNamespace(name="测试 UP", videos=list(videos), has_more=False)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "state.json"
        self.store = Store(self.path)
        self.store.add_channel("42")

    def test_baseline_history_new_updates_and_restart_deduplication(self):
        self.assertEqual([], self.store.ingest("42", feed(video("old", 90)), now=100))
        first = self.store.snapshot()
        self.assertTrue(first["videos"][0]["read"])
        self.assertFalse(first["videos"][0]["is_new"])
        self.assertEqual(100, first["channels"][0]["baseline_at"])
        updates = self.store.ingest("42", feed(video("new", 110), video("old", 90)), now=120)
        self.assertEqual(["new"], [item["key"] for item in updates])
        self.assertFalse(updates[0]["read"])
        reopened = Store(self.path)
        self.assertEqual([], reopened.ingest("42", feed(video("new", 110)), now=130))
        reopened.mark_read("new")
        reopened.ingest("42", feed(video("new", 110)), now=140)
        self.assertTrue(reopened.snapshot()["videos"][0]["read"])

    def test_newly_seen_pinned_old_video_does_not_notify(self):
        self.store.ingest("42", feed(video("recent", 99)), now=100)
        self.assertEqual([], self.store.ingest("42", feed(video("pinned", 30)), now=120))
        self.assertEqual(2, len(self.store.snapshot()["videos"]))
        self.assertEqual("recent", self.store.snapshot()["channels"][0]["latest"]["key"])

    def test_removed_or_disabled_channels_ignore_inflight_results(self):
        self.store.toggle_channel("42")
        self.assertEqual([], self.store.ingest("42", feed(video("old", 90)), now=100))
        self.assertFalse(self.store.record_error("42", "失败", now=100))
        self.store.toggle_channel("42")
        self.store.ingest("42", feed(video("old", 90)), now=100)
        self.assertTrue(self.store.remove_channel("42"))
        self.assertEqual([], self.store.ingest("42", feed(video("new", 110)), now=120))
        self.assertEqual([], self.store.snapshot()["channels"])
        self.assertEqual([], self.store.snapshot()["videos"])

    def test_failures_preserve_history_and_baseline_and_back_off(self):
        self.store.ingest("42", feed(video("old", 90)), now=100)
        for index, delay in enumerate((300, 600, 1200, 2400, 3600, 3600), start=1):
            self.store.record_error("42", "查询受限", now=120)
            state = self.store.snapshot()
            channel = state["channels"][0]
            self.assertEqual(index, channel["failures"])
            self.assertEqual(120 + delay, channel["next_check"])
            self.assertEqual(100, channel["baseline_at"])
            self.assertEqual(100, channel["last_success_at"])
            self.assertEqual(1, len(state["videos"]))
        self.assertEqual([], self.store.due_channels(now=121))
        self.store.ingest("42", feed(video("new", 125)), now=130)
        self.assertEqual(0, self.store.snapshot()["channels"][0]["failures"])
        self.assertEqual(130, self.store.snapshot()["channels"][0]["last_success_at"])

    def test_pause_ignores_inflight_results_until_resume(self):
        self.store.ingest("42", feed(video("old", 90)), now=100)
        self.store.update_settings({"paused": True})
        before = self.store.snapshot()
        self.assertEqual([], self.store.ingest("42", feed(video("new", 110)), now=120))
        self.assertFalse(self.store.record_error("42", "在途错误", now=120))
        self.assertEqual(before, self.store.snapshot())
        self.store.update_settings({"paused": False})
        self.assertEqual(1, len(self.store.ingest("42", feed(video("new", 110)), now=130)))

    def test_old_store_migration_preserves_safe_success_boundary(self):
        self.store.ingest("42", feed(video("old", 90)), now=100)
        self.store.ingest("42", feed(), now=200)
        data = self.store.snapshot()
        del data["channels"][0]["last_success_at"]
        self.path.write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(200, Store(self.path).snapshot()["channels"][0]["last_success_at"])
        data["channels"][0].update(failures=2, checked_at=500)
        self.path.write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(100, Store(self.path).snapshot()["channels"][0]["last_success_at"])

    def test_interval_pause_manual_cooldown_and_login_retry(self):
        self.store.ingest("42", feed(), now=100)
        self.store.set_interval(10)
        self.assertEqual(700, self.store.snapshot()["channels"][0]["next_check"])
        self.store.update_settings({"paused": True})
        self.assertEqual([], self.store.due_channels(now=1000))
        self.store.update_settings({"paused": False})
        self.assertEqual(1, len(self.store.due_channels(now=1000)))
        self.store.record_error("42", "限流", now=100)
        with patch("state.time.time", return_value=200):
            self.assertTrue(self.store.request_check())
            self.assertEqual(400, self.store.snapshot()["channels"][0]["next_check"])
            self.assertFalse(self.store.request_check())
            self.assertTrue(self.store.request_check(reset_errors=True))
            self.assertEqual(0, self.store.snapshot()["channels"][0]["next_check"])

    def test_empty_initial_feed_still_establishes_baseline(self):
        self.store.ingest("42", feed(), now=100)
        self.assertTrue(self.store.snapshot()["channels"][0]["initialized"])
        self.assertEqual(1, len(self.store.ingest("42", feed(video("first", 101)), now=102)))

    def test_optional_cover_and_duration_are_preserved(self):
        source = video("sample", 90)
        source.cover = "https://i0.hdslb.com/bfs/archive/sample.jpg"
        source.duration = "03:24"
        self.store.ingest("42", feed(source), now=100)
        saved = self.store.snapshot()["videos"][0]
        self.assertEqual(source.cover, saved["cover"])
        self.assertEqual("03:24", saved["duration"])

    def test_duplicate_feed_entries_notify_once(self):
        self.store.ingest("42", feed(), now=100)
        self.assertEqual(1, len(self.store.ingest("42", feed(video("v", 101), video("v", 101)), now=102)))

    def test_capped_history_keeps_deduplication_records(self):
        self.store.ingest("42", feed(), now=100)
        self.store.ingest("42", feed(*(video(str(index), 101 + index) for index in range(501))), now=700)
        self.assertEqual(500, len(self.store.snapshot()["videos"]))
        self.assertEqual(501, len(self.store.snapshot()["channels"][0]["seen"]))
        self.assertEqual([], self.store.ingest("42", feed(video("0", 101)), now=800))

    def test_snapshot_detached_invalid_updates_and_duplicate_uid(self):
        detached = self.store.snapshot()
        detached["channels"].clear()
        self.assertEqual(1, len(self.store.snapshot()["channels"]))
        with self.assertRaises(ValueError):
            self.store.add_channel("42")
        for update in ({"interval_minutes": 2}, {"interval_minutes": 3.5}, {"quiet_start": "24:00"}, {"popups": "yes"}, {"unknown": 1}):
            with self.assertRaises(ValueError):
                self.store.update_settings(update)
        self.assertEqual(5, self.store.snapshot()["settings"]["interval_minutes"])

    def test_disk_failure_does_not_commit_memory(self):
        before = self.store.snapshot()
        with patch.object(self.store, "_save", side_effect=OSError("磁盘已满")):
            with self.assertRaises(OSError):
                self.store.add_channel("43")
        self.assertEqual(before, self.store.snapshot())

    def test_corrupt_file_is_preserved(self):
        self.path.write_text("{broken", encoding="utf-8")
        with self.assertRaises(ValueError):
            Store(self.path)
        self.assertEqual("{broken", self.path.read_text(encoding="utf-8"))


class HelpersTests(unittest.TestCase):
    def test_uid_input(self):
        examples = {
            "42": "42", "00042": "42", "https://space.bilibili.com/42": "42",
            "space.bilibili.com/42?spm_id_from=333.999": "42",
            "这是空间：https://space.bilibili.com/42/ 分享给你": "42",
        }
        for value, expected in examples.items():
            self.assertEqual(expected, parse_uid(value))
        for value in ("0", "-42", "UP 主", "https://www.bilibili.com/video/BV1", "https://evilspace.bilibili.com/42", "https://space.bilibili.com.evil/42", "https://space.bilibili.com/42abc", "https://evil.com/space.bilibili.com/42"):
            with self.assertRaises(ValueError, msg=value):
                parse_uid(value)

    def test_quiet_hours_cross_midnight_boundaries_and_all_day(self):
        settings = {"quiet_enabled": True, "quiet_start": "23:00", "quiet_end": "08:00"}
        for hour, minute, expected in ((22, 59, False), (23, 0, True), (0, 0, True), (7, 59, True), (8, 0, False)):
            self.assertEqual(expected, is_quiet(settings, datetime(2026, 1, 1, hour, minute)))
        settings.update(quiet_start="08:00", quiet_end="08:00")
        self.assertTrue(is_quiet(settings, datetime(2026, 1, 1, 16)))
        settings["quiet_enabled"] = False
        self.assertFalse(is_quiet(settings, datetime(2026, 1, 1, 16)))


if __name__ == "__main__":
    unittest.main()
