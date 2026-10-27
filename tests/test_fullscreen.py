"""Fullscreen keeps native chrome and document layout in one lifecycle."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import native_ui


class FullscreenTests(unittest.TestCase):
    def setUp(self):
        self.core = Mock(ContainsFullScreenElement=False)
        self.menu = SimpleNamespace(Visible=True, GetType=lambda: SimpleNamespace(Name='MenuStrip'))
        self.form = SimpleNamespace(Controls=[self.menu], WindowState='Maximized', is_fullscreen=False,
                                    browser=SimpleNamespace(webview=SimpleNamespace(CoreWebView2=self.core)))
        def toggle():
            self.form.is_fullscreen = not self.form.is_fullscreen
            self.form.WindowState = 'Maximized' if self.form.is_fullscreen else 'Normal'
        self.form.toggle_fullscreen = Mock(side_effect=toggle)
        self.controller = native_ui.PlayerFullscreen(Mock(), lambda window: True)
        patcher = patch.object(native_ui, 'post_window', side_effect=lambda window, action: action(self.form))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_fullscreen_hides_menu_and_exit_restores_maximized_window(self):
        self.controller.set(True)
        self.assertTrue(self.form.is_fullscreen)
        self.assertFalse(self.menu.Visible)
        self.controller.set(True)
        self.form.toggle_fullscreen.assert_called_once()
        self.controller.set(False)
        self.assertFalse(self.form.is_fullscreen)
        self.assertTrue(self.menu.Visible)
        self.assertEqual('Maximized', self.form.WindowState)

    def test_f11_repeat_and_escape_do_not_toggle_repeatedly(self):
        self.assertFalse(self.controller.handle_key(27))
        self.assertTrue(self.controller.handle_key(122))
        self.assertTrue(self.controller.handle_key(122, repeated=True))
        self.assertTrue(self.controller.active)
        self.assertTrue(self.controller.handle_key(27))
        self.assertFalse(self.controller.active)
        self.assertEqual(2, self.form.toggle_fullscreen.call_count)

    def test_exiting_browser_fullscreen_does_not_wait_for_script(self):
        self.controller.set(True)
        self.core.ContainsFullScreenElement = True
        self.controller.set(False)
        scripts = [call.args[0] for call in self.core.ExecuteScriptAsync.call_args_list]
        self.assertTrue(any('document.exitFullscreen()' in s for s in scripts))
        self.assertIn('setHostFullscreen(false)', scripts[-1])
        self.controller.window.evaluate_js.assert_not_called()

    def test_pending_fullscreen_cannot_reopen_closed_player(self):
        self.controller.is_current = lambda window: False
        self.controller.set(True)
        self.assertFalse(self.controller.active)
        self.form.toggle_fullscreen.assert_not_called()
        self.core.ExecuteScriptAsync.assert_not_called()


if __name__ == '__main__':
    unittest.main()
