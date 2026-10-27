from pathlib import Path
from tempfile import TemporaryDirectory
import copy
import threading
import unittest
from unittest.mock import Mock, patch

import main
from playback_queue import FollowedQueue, owned_videos

B1='BV1B7411m7LV'
B2='BV1xx411c7Ug'
Y1='yt:AAAAAAAAAAA'


class FollowedQueueTests(unittest.TestCase):
    def setUp(self):
        self.data={'channels':[{'uid':'42'},{'uid':'yt:channel'}], 'videos':[
            {'key':B1,'uid':'42'},{'key':Y1,'uid':'yt:channel'},{'key':B2,'uid':'42'},
            {'key':'yt:BBBBBBBBBBB','uid':'not-followed'}]}
        self.queue=FollowedQueue(lambda:copy.deepcopy(self.data))

    def test_queue_is_finite_filtered_and_contains_only_saved_creators(self):
        t=self.queue.start(B1,[B1,'https://evil.test/',B1,Y1,'yt:BBBBBBBBBBB'])
        self.assertEqual([B1,Y1],self.queue.order)
        t=self.queue.move(t)
        self.assertEqual(Y1,t.key)
        self.assertIsNone(self.queue.move(t),'the last video cannot wrap to recommendations')

    def test_removed_creators_and_videos_are_skipped_at_advance(self):
        t=self.queue.start(B1)
        self.data['channels']=[{'uid':'42'}]
        self.assertEqual(B2,self.queue.move(t).key)

    def test_stale_end_after_manual_selection_or_close_cannot_advance(self):
        old=self.queue.start(B1)
        latest=self.queue.start(Y1)
        self.assertIsNone(self.queue.move(old))
        self.assertFalse(self.queue.cancel(old))
        self.assertTrue(self.queue.matches(latest))
        self.queue.cancel()
        self.assertIsNone(self.queue.move(latest))

    def test_duplicate_end_and_part_events_are_consumed_once(self):
        old=self.queue.start(B1)
        part=self.queue.next_part(old)
        self.assertIsNone(self.queue.move(old))
        next_video=self.queue.move(part)
        self.assertIsNone(self.queue.move(part))
        self.assertEqual(Y1,next_video.key)

    def test_arbitrary_video_or_channel_cannot_start(self):
        for key in ('https://www.youtube.com/watch?v=AAAAAAAAAAA','yt:BBBBBBBBBBB','no-video'):
            with self.assertRaises(ValueError):self.queue.start(key)


class AutoplayApplicationTests(unittest.TestCase):
    def setUp(self):
        folder=TemporaryDirectory();self.addCleanup(folder.cleanup)
        for name,value in (('DATA',Path(folder.name)),('load_session',Mock(return_value={})),('get_autostart',Mock(return_value=False))):
            p=patch.object(main,name,value);p.start();self.addCleanup(p.stop)
        self.app=main.Application()
        self.app.focus_player=Mock(_fullscreen=None)
        self.app.youtube_player=Mock(_fullscreen=None)
        self.app.changed=Mock();self.app.emit=Mock()
        self.app.store.add_channel('42')
        self.app.store.add_channel('UC'+'a'*22,'Tube','youtube')
        with self.app.store._lock:
            s=self.app.store.snapshot()
            s['videos']=[{'key':B1,'uid':'42','title':'Bili','read':True,'is_new':False,'detected_at':1},
                         {'key':Y1,'uid':'yt:UC'+'a'*22,'title':'Tube','platform':'youtube','read':True,'is_new':False,'detected_at':1}]
            self.app.store._commit(s)

    def config(self,ticket):
        return self.app.playback_config({'key':ticket.key,'revision':ticket.revision})

    def test_end_continues_to_saved_other_platform_and_updates_local_ui(self):
        ticket=self.app.play_queue.start(B1)
        self.app.playback_signal(self.config(ticket),'ended',{'part':1,'pages':1})
        self.app.youtube_player.open.assert_called_once()
        self.assertEqual('AAAAAAAAAAA',self.app.youtube_player.open.call_args.args[0])
        self.assertEqual(Y1,self.app.play_queue.current)
        self.assertEqual('playbackAdvanced',self.app.emit.call_args.args[0])

    def test_disabled_or_cancelled_autoplay_never_opens_next_video(self):
        ticket=self.app.play_queue.start(B1)
        config=self.config(ticket)
        self.app.set_autoplay(False)
        self.app.playback_signal(config,'ended',{'part':1,'pages':1})
        self.app.youtube_player.open.assert_not_called()
        self.app.play_queue.cancel()
        self.app.playback_signal(config,'next',{})
        self.app.youtube_player.open.assert_not_called()

    def test_bilibili_parts_continue_before_the_next_work(self):
        ticket=self.app.play_queue.start(B1)
        self.app.playback_signal(self.config(ticket),'ended',{'part':1,'pages':2})
        self.app.focus_player.open.assert_called_once()
        self.assertEqual(2,self.app.focus_player.open.call_args.args[2])
        self.app.youtube_player.open.assert_not_called()

    def test_native_close_cancels_pending_continuation(self):
        ticket=self.app.play_queue.start(B1)
        context={'key':ticket.key,'revision':ticket.revision}
        self.app.playback_closed(context)
        self.assertFalse(self.app.play_queue.matches(ticket))
        self.assertEqual('stopPlayerFromHost',self.app.emit.call_args.args[0])


if __name__=='__main__':unittest.main()
