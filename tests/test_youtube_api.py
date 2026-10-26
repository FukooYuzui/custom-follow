import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

import requests

from youtube_api import (YouTubeClient, YouTubeError, YouTubeFeed, YouTubeVideo, channel_input,
                         channel_key, parse_atom, parse_video_page, relative_timestamp)
from state import Store
from focus_player import allowed_youtube_navigation, youtube_url

CID = 'UC' + 'a' * 22
VID = 'A' * 11
OTHER = 'B' * 11


def page(videos=None, channel_id=CID, malformed=False):
    contents = []
    for vid, title, age in videos or [(VID, 'Video title', '1 hour ago')]:
        contents.append({'richItemRenderer': {'content': {'lockupViewModel': {
            'contentId': vid, 'contentType': 'LOCKUP_CONTENT_TYPE_VIDEO',
            'metadata': {'lockupMetadataViewModel': {'title': {'content': title},
                'metadata': {'contentMetadataViewModel': {'metadataRows': [
                    {'metadataParts': [{'text': {'content': '10K views'}}, {'text': {'content': age}}]}
                ]}}}},
            'contentImage': {'thumbnailViewModel': {'overlays': [
                {'thumbnailBottomOverlayViewModel': {'badges': [{'thumbnailBadgeViewModel': {'text': '12:30'}}]}}
            ]}}
        }}}})
    data = {'metadata': {'channelMetadataRenderer': {'externalId': channel_id, 'title': 'Fixture channel'}},
            'contents': {'twoColumnBrowseResultsRenderer': {'tabs': [
                {'tabRenderer': {'title': 'Videos', 'selected': True, 'content': {} if malformed else {'richGridRenderer': {'contents': contents}}}}
            ]}}}
    return '<script>var ytInitialData = ' + json.dumps(data) + ';</script>'


def atom(channel_id=CID):
    return f'''<feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015">
      <yt:channelId>{channel_id}</yt:channelId><title>Fixture channel</title><author><name>Fixture channel</name></author>
      <entry><yt:videoId>{VID}</yt:videoId><yt:channelId>{channel_id}</yt:channelId><title>Video</title><published>2026-10-25T10:00:00Z</published></entry>
    </feed>'''


class YouTubeProtocolTests(unittest.TestCase):
    def test_channel_input_normalizes_only_official_channels(self):
        self.assertEqual(CID, channel_input(CID)[0])
        self.assertEqual(CID, channel_input('https://youtube.com/channel/' + CID + '/videos')[0])
        self.assertIn('/@test/videos', channel_input('@test')[1])
        self.assertIn('/@test/videos', channel_input('https://www.youtube.com/@test/about')[1])
        for bad in ('https://youtube.com.evil.test/@x', 'https://youtube.com@evil.test/@x',
                    'https://www.youtube.com/watch?v=' + VID, 'https://127.0.0.1/@x', '@../x', 'a plain nickname'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                channel_input(bad)

    def test_atom_belongs_to_selected_channel(self):
        f = parse_atom(atom(), CID)
        self.assertEqual('yt:' + VID, f.videos[0].key)
        self.assertFalse(f.videos[0].timestamp_approximate)
        with self.assertRaises(YouTubeError):
            parse_atom(atom('UC' + 'b'*22), CID)

    def test_placeholder_xml_is_not_an_empty_success(self):
        with self.assertRaises(YouTubeError):
            parse_atom('<feed xmlns="http://www.w3.org/2005/Atom"><title>YouTube video feed</title></feed>', CID)

    def test_modern_page_only_reads_the_selected_videos_tab(self):
        cid, f = parse_video_page(page(), CID, now=100000)
        self.assertEqual(CID, cid)
        self.assertEqual(1, len(f.videos))
        self.assertEqual(96400, f.videos[0].published_at)
        self.assertEqual('12:30', f.videos[0].duration)
        self.assertTrue(f.videos[0].timestamp_approximate)
        self.assertTrue(f.order_is_freshness)

    def test_changed_page_and_wrong_channel_fail_explicitly(self):
        for value in (page(malformed=True), page(channel_id='UC' + 'b'*22), '<html>challenge</html>'):
            with self.subTest(value=value[:60]), self.assertRaises(YouTubeError):
                parse_video_page(value, CID)

    def test_relative_dates_are_marked_approximate_and_handle_languages(self):
        self.assertEqual(100000 - 7200, relative_timestamp('2 hours ago', 100000))
        self.assertEqual(100000 - 7200, relative_timestamp('2 小時前', 100000))
        self.assertIsNone(relative_timestamp('Scheduled for tomorrow', 100000))

    def test_rss_404_falls_back_without_retrying_rss_each_poll(self):
        client = YouTubeClient()
        client._get = Mock(side_effect=[None, page(), page()])
        self.assertEqual('channel_page', client.fetch_videos(CID).source)
        client.fetch_videos(CID)
        urls = [call.args[0] for call in client._get.call_args_list]
        self.assertEqual(1, sum('/feeds/videos.xml' in u for u in urls))
        self.assertEqual(2, sum('/videos?hl=en' in u for u in urls))

    def test_connectivity_errors_never_expose_transport_exception(self):
        client = YouTubeClient()
        client.session.get = Mock(side_effect=requests.Timeout('secret fixture'))
        with self.assertRaises(YouTubeError) as context:
            client.fetch_videos(CID)
        self.assertNotIn('secret fixture', str(context.exception))

    def test_official_player_cannot_navigate_to_other_videos_or_domains(self):
        self.assertTrue(allowed_youtube_navigation(youtube_url(VID), VID))
        self.assertTrue(allowed_youtube_navigation('https://accounts.google.com/ServiceLogin', VID))
        for url in ('https://www.youtube.com/watch?v='+OTHER, 'https://www.youtube.com/',
                    'https://www.youtube.com/watch?v='+VID+'&v='+OTHER,
                    'https://youtube.com.evil.test/watch?v='+VID, 'file:///C:/private'):
            self.assertFalse(allowed_youtube_navigation(url, VID))


class YouTubeStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'state.json')
        self.store.add_channel(CID, 'Fixture', 'youtube')

    def video(self, vid, timestamp):
        return YouTubeVideo('yt:'+vid, vid, 'https://www.youtube.com/watch?v='+vid, timestamp, 'Fixture', timestamp_approximate=True)

    def test_initial_import_never_notifies_and_preserves_platform(self):
        self.assertEqual([], self.store.ingest(channel_key(CID), YouTubeFeed('Fixture',[self.video(VID,90)],order_is_freshness=True), now=100))
        self.assertEqual('youtube', self.store.snapshot()['videos'][0]['platform'])
        self.assertEqual('youtube', self.store.snapshot()['channels'][0]['platform'])

    def test_same_hour_new_id_not_missed_by_approximate_date(self):
        self.store.ingest(channel_key(CID), YouTubeFeed('Fixture',[self.video(VID,90)],order_is_freshness=True), now=100)
        f = YouTubeFeed('Fixture',[self.video(OTHER,95),self.video(VID,90)],order_is_freshness=True)
        news = self.store.ingest(channel_key(CID), f, now=110)
        self.assertEqual(['yt:'+OTHER], [v['key'] for v in news])
        self.assertEqual([], self.store.ingest(channel_key(CID), f, now=120))

    def test_older_newly_visible_items_below_previous_head_do_not_notify(self):
        self.store.ingest(channel_key(CID), YouTubeFeed('Fixture',[self.video(VID,90)],order_is_freshness=True), now=100)
        news = self.store.ingest(channel_key(CID), YouTubeFeed('Fixture',[self.video(VID,90),self.video(OTHER,80)],order_is_freshness=True), now=110)
        self.assertEqual([],news)

    def test_bilibili_and_youtube_subscriptions_remain_independent(self):
        self.store.add_channel('42')
        self.store.remove_channel(channel_key(CID))
        self.assertEqual(['42'],[c['uid'] for c in self.store.snapshot()['channels']])


if __name__ == '__main__':
    unittest.main()
