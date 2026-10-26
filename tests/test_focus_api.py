"""Only locally subscribed videos can reach the official remote player."""
import unittest
from unittest.mock import Mock

from main import Api


class OfficialPlayerApiTests(unittest.TestCase):
    def setUp(self):
        self.app = Mock()
        self.video = dict(key='BV1cpJ86BEys', title='Fixture', uid='254463269')
        self.app.store.snapshot.return_value = {'videos': [self.video]}
        self.api = Api(self.app)

    def test_only_current_subscriptions_can_open_official_player(self):
        self.assertFalse(self.api.open_focus_video('BV1B7411m7LV')['ok'])
        self.app.focus_player.open.assert_not_called()
        self.assertTrue(self.api.open_focus_video(self.video['key'], 2)['ok'])
        self.app.focus_player.open.assert_called_once_with(self.video['key'], 'Fixture', 2)
        self.app.store.mark_read.assert_called_once_with(self.video['key'])

    def test_bad_part_never_enters_remote_url(self):
        for part in ('1&foo=bar', 0, -1, 1000, True, 1.2, float('nan'), float('inf')):
            with self.subTest(part=part):
                self.assertFalse(self.api.open_focus_video(self.video['key'], part)['ok'])
        self.app.focus_player.open.assert_not_called()

    def test_player_failure_does_not_mark_video_read(self):
        self.app.focus_player.open.side_effect = RuntimeError('fixture failure')
        with self.assertLogs(level='ERROR'):
            result = self.api.open_focus_video(self.video['key'])
        self.assertFalse(result['ok'])
        self.app.store.mark_read.assert_not_called()

    def test_switch_away_closes_remote_player(self):
        self.assertTrue(self.api.close_focus_video()['ok'])
        self.app.focus_player.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
