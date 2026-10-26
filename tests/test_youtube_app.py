from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

import main
from youtube_api import YouTubeFeed, YouTubeVideo

CID='UC'+'a'*22
VID='A'*11


class YouTubeAppTests(unittest.TestCase):
    def setUp(self):
        tmp=TemporaryDirectory();self.addCleanup(tmp.cleanup)
        for name,value in [('DATA',Path(tmp.name)),('load_session',Mock(return_value={})),('get_autostart',Mock(return_value=False))]:
            p=patch.object(main,name,value);p.start();self.addCleanup(p.stop)
        self.youtube=Mock()
        p=patch.object(main,'YouTubeClient',return_value=self.youtube);p.start();self.addCleanup(p.stop)
        self.app=main.Application();self.app.changed=Mock();self.api=main.Api(self.app)
        self.feed=YouTubeFeed('Fixture',[YouTubeVideo('yt:'+VID,'Fixture','https://www.youtube.com/watch?v='+VID,90,'Fixture')])
        self.youtube.resolve_channel.return_value=({'channel_id':CID,'name':'Fixture'},self.feed)

    def test_add_initializes_without_notification(self):
        result=self.api.add_channel('@example','youtube')
        self.assertTrue(result['ok'])
        s=self.app.store.snapshot()
        self.assertEqual('youtube',s['channels'][0]['platform'])
        self.assertTrue(s['videos'][0]['read'])
        self.assertTrue(self.app.notifications.empty())

    def test_youtube_video_uses_its_own_player(self):
        self.api.add_channel('@example','youtube')
        self.app.focus_player=Mock();self.app.youtube_player=Mock()
        result=self.api.play_video('yt:'+VID)
        self.assertEqual(VID,result['video_id'])
        self.assertTrue(self.api.open_focus_video('yt:'+VID)['ok'])
        self.app.youtube_player.open.assert_called_once_with(VID,'Fixture')
        self.app.focus_player.open.assert_not_called()

    def test_only_explicitly_stored_youtube_videos_can_open(self):
        self.app.youtube_player=Mock()
        self.assertFalse(self.api.open_focus_video('yt:'+VID)['ok'])
        self.app.youtube_player.open.assert_not_called()

    def test_youtube_homepage_is_not_sent_to_bilibili(self):
        self.api.add_channel('@example','youtube')
        with patch.object(main.webbrowser,'open') as browser:
            self.api.open_space('yt:'+CID)
            browser.assert_called_once_with('https://www.youtube.com/channel/'+CID)

    def test_youtube_worker_dispatch_does_not_use_bilibili_session(self):
        self.app.store.add_channel(CID,'Fixture','youtube')
        self.youtube.fetch_videos.return_value=self.feed
        self.app.client=Mock();self.app.client.check_login.return_value=None
        self.app.stopping.wait=Mock(return_value=False)
        self.app.wake.wait=Mock(side_effect=lambda *args:self.app.stopping.set())
        self.app.worker()
        self.youtube.fetch_videos.assert_called_once_with(CID,since_ts=None)
        self.app.client.fetch_videos.assert_not_called()
        self.assertEqual(1,len(self.app.store.snapshot()['videos']))


if __name__=='__main__':
    unittest.main()
