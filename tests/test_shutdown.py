"""Shutdown races must not wait for a renderer, network request or tray."""
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import Mock, patch

import main
import native_ui
import focus_player
from focus_player import FocusPlayer


class ShutdownTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        for name, value in (("DATA", Path(folder.name)), ("load_session", Mock(return_value={}))):
            patcher = patch.object(main, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.app = main.Application()
        self.app.window = Mock()
        self.app.toast = Mock()
        self.app.focus_player = Mock()
        self.app.youtube_player = Mock()

    def test_repeated_quit_does_not_wait_for_tray_or_renderer(self):
        entered, release, returned = threading.Event(), threading.Event(), threading.Event()
        def stalled_tray():
            entered.set()
            release.wait(3)
        self.app.tray = Mock(stop=stalled_tray)
        self.app.window.evaluate_js.side_effect = AssertionError('Synchronous renderer call')
        try:
            with patch.object(main, 'close_window') as close:
                def quit_twice():
                    self.app.quit()
                    self.app.quit()
                    returned.set()
                caller = threading.Thread(target=quit_twice, daemon=True)
                caller.start()
                self.assertTrue(returned.wait(1), 'quit waited for a blocked tray')
                self.assertTrue(entered.wait(1))
                self.assertEqual(close.call_count, 2)
                self.app.focus_player.close.assert_called_once()
                self.app.youtube_player.close.assert_called_once()
                self.assertTrue(self.app.stopping.is_set())
                self.assertTrue(self.app.close())
        finally:
            release.set()

    def test_no_tray_close_cancels_current_event_and_queues_exit(self):
        self.app.tray_ok = False
        with patch.object(main, 'close_window') as close:
            self.assertFalse(self.app.close())
            self.assertTrue(self.app.close(), 'queued close must be accepted')
        self.assertEqual(close.call_count, 2)
        self.assertTrue(self.app.quitting)

    def test_cancelled_youtube_add_does_not_wait_or_commit_late_response(self):
        started, release, completed, returned = [threading.Event() for _ in range(4)]
        result = []
        def slow_resolve(text):
            started.set()
            release.wait(3)
            completed.set()
            return {'channel_id': 'UC' + 'a' * 22, 'name': 'Delayed'}, []
        self.app.youtube.resolve_channel = slow_resolve
        caller = threading.Thread(target=lambda: (result.append(main.Api(self.app).add_channel('@test', 'youtube')), returned.set()), daemon=True)
        caller.start()
        try:
            self.assertTrue(started.wait(1))
            with patch.object(main, 'close_window'):
                self.app.quit()
            self.assertTrue(returned.wait(1), 'API worker remained blocked by network')
            self.assertFalse(result[0]['ok'])
        finally:
            release.set()
            caller.join(2)
        self.assertTrue(completed.wait(1))
        self.assertEqual([], self.app.store.snapshot()['channels'])

    def test_close_during_window_creation_disposes_late_window(self):
        manager = FocusPlayer(main.ASSETS, lambda: {}, Mock())
        entered, release = threading.Event(), threading.Event()
        late = Mock()
        def slow_create(*args, **kwargs):
            entered.set()
            release.wait(3)
            return late
        with patch.object(focus_player.webview, 'create_window', side_effect=slow_create), \
                patch.object(focus_player, 'close_window') as close, \
                patch.object(focus_player, 'show_window') as show:
            caller = threading.Thread(target=manager.open, args=('BV1B7411m7LV',), daemon=True)
            caller.start()
            try:
                self.assertTrue(entered.wait(1))
                manager.close()
            finally:
                release.set()
                caller.join(2)
            self.assertFalse(caller.is_alive())
            close.assert_called_once_with(late)
            show.assert_not_called()
            self.assertIsNone(manager._window)

    def test_bridge_acknowledgement_never_waits_but_expressions_keep_result(self):
        window = Mock()
        window._custom_follow_bridge_delivery = False
        original = window.evaluate_js
        original.return_value = 42
        native_ui.install_bridge_delivery(window)
        with patch.object(native_ui, 'post_js') as post:
            self.assertIsNone(window.evaluate_js('window.pywebview._returnValuesCallbacks["quit"]["1"]("ok")'))
            post.assert_called_once()
            original.assert_not_called()
            self.assertEqual(42, window.evaluate_js('6*7'))
            callback = Mock()
            window.evaluate_js('Promise.resolve(42)', callback)
            original.assert_called_with('Promise.resolve(42)', callback)

    def test_queued_player_open_cannot_reopen_hidden_application(self):
        manager = FocusPlayer(main.ASSETS, lambda: {}, Mock(), can_open=lambda: False)
        with patch.object(focus_player.webview, 'create_window') as create:
            manager.open('BV1B7411m7LV')
        create.assert_not_called()

    def test_notification_wait_is_cancelled_when_exiting_during_startup(self):
        waiting, returned = threading.Event(), threading.Event()
        original_wait = self.app.toast_ready.wait
        def wait(timeout):
            waiting.set()
            return original_wait(timeout)
        self.app.toast_ready.wait = wait
        caller = threading.Thread(target=lambda: (self.app.notify(test=True), returned.set()), daemon=True)
        caller.start()
        self.assertTrue(waiting.wait(1))
        with patch.object(main, 'close_window'):
            self.app.quit()
        self.assertTrue(returned.wait(1), 'notification kept API thread alive during exit')
        caller.join(1)


if __name__ == '__main__':
    unittest.main()
