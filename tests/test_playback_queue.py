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

    def test_view_contains_current_and_filtered_future_with_stable_metadata_version(self):
        self.data['videos'][0].update(title='Bili one',author='Bili creator',watched=True)
        self.data['videos'][1].update(title='Tube one',author='Tube creator')
        ticket=self.queue.start(B1)
        view=self.queue.view(ticket)
        self.assertEqual([B1,Y1,B2],[item['key'] for item in view['items']])
        self.assertEqual([True,False,False],[item['current'] for item in view['items']])
        self.assertEqual('youtube',view['items'][1]['platform'])
        self.assertEqual('Bili one',view['items'][0]['title'])
        self.assertTrue(view['items'][0]['watched'])
        self.assertEqual(2,view['remaining'])
        self.assertEqual(view['version'],self.queue.view(ticket)['version'])
        view['items'][0]['title']='caller mutation'
        self.assertEqual('Bili one',self.queue.view(ticket)['items'][0]['title'])
        self.data['videos'][1]['title']='updated title'
        self.assertNotEqual(view['version'],self.queue.view(ticket)['version'])

    def test_view_and_remaining_skip_removed_and_newly_watched_future_entries(self):
        ticket=self.queue.start(B1,unwatched_only=True)
        self.data['videos'][0]['watched']=True
        self.data['videos'][1]['watched']=True
        view=self.queue.view(ticket)
        self.assertEqual([B1,B2],[item['key'] for item in view['items']])
        self.assertEqual(1,self.queue.remaining(ticket))
        self.data['videos']=[v for v in self.data['videos'] if v['key']!=B2]
        self.assertEqual([B1],[item['key'] for item in self.queue.view(ticket)['items']])
        self.assertEqual(0,self.queue.remaining(ticket))

    def test_view_after_advance_has_no_past_entries_and_stale_view_is_empty(self):
        old=self.queue.start(B1)
        current=self.queue.move(old)
        self.assertEqual([Y1,B2],[item['key'] for item in self.queue.view(current)['items']])
        self.assertEqual([],self.queue.view(old)['items'])
        self.assertEqual(0,self.queue.view(old)['remaining'])
        self.queue.cancel()
        self.assertEqual(self.queue.view(None)['version'],self.queue.view(current)['version'])

    def test_select_requires_exact_queue_lease_and_an_owned_future_item(self):
        original=self.queue.start(B1,[B1,Y1,B2])
        for invalid in (B1,'yt:BBBBBBBBBBB','https://www.youtube.com/watch?v=AAAAAAAAAAA','BV1xx411c7UV',None,{}):
            self.assertIsNone(self.queue.select(original,invalid))
        self.queue.mark_finished(original)
        chosen=self.queue.select(original,B2)
        self.assertEqual(B2,chosen.key)
        self.assertEqual(original.revision+1,chosen.revision)
        self.assertTrue(self.queue.is_active())
        self.assertIsNone(self.queue.select(original,Y1),'an old list event cannot override the new selection')
        self.assertIsNone(self.queue.select(chosen,B1),'the upcoming drawer cannot select a past entry')

    def test_select_rechecks_membership_and_scheduled_watched_state(self):
        ticket=self.queue.start(B1,unwatched_only=True)
        self.data['channels']=[{'uid':'42'}]
        self.assertIsNone(self.queue.select(ticket,Y1))
        self.data['videos'][2]['watched']=True
        self.assertIsNone(self.queue.select(ticket,B2))
        self.assertEqual(B1,self.queue.current)

    def test_view_store_read_does_not_hold_queue_lock_or_delay_cancel(self):
        ticket=self.queue.start(B1)
        reading=threading.Event();release=threading.Event();cancelled=threading.Event();result=[]
        def slow_snapshot():
            reading.set();release.wait(3)
            return copy.deepcopy(self.data)
        self.queue.snapshot=slow_snapshot
        worker=threading.Thread(target=lambda:result.append(self.queue.view(ticket)))
        worker.start()
        self.assertTrue(reading.wait(1))
        cancel=threading.Thread(target=lambda:(self.queue.cancel(),cancelled.set()))
        cancel.start()
        try:
            self.assertTrue(cancelled.wait(1),'cancel must not wait for a store snapshot')
        finally:
            release.set();worker.join(3);cancel.join(3)
        self.assertEqual([],result[0]['items'],'a cancelled lease cannot return a stale playable list')


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
