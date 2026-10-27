from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import threading
from unittest.mock import Mock, patch

import main
from focus_player import FocusPlayer, waiting_html

B1='BV1B7411m7LV';Y1='yt:AAAAAAAAAAA';B2='BV1xx411c7Ug'


class PlayerQueueHostTests(unittest.TestCase):
    def setUp(self):
        folder=TemporaryDirectory();self.addCleanup(folder.cleanup)
        for name,value in (('DATA',Path(folder.name)),('load_session',Mock(return_value={})),('get_autostart',Mock(return_value=False))):
            p=patch.object(main,name,value);p.start();self.addCleanup(p.stop)
        self.app=main.Application()
        self.app.changed=Mock();self.app.emit=Mock()
        self.app.focus_player=Mock(_fullscreen=None)
        self.app.youtube_player=Mock(_fullscreen=None)
        for manager in (self.app.focus_player,self.app.youtube_player):
            manager.context_snapshot.return_value=None
        self.app.store.add_channel('42','Bili')
        self.app.store.add_channel('UC'+'a'*22,'Tube','youtube')
        with self.app.store._lock:
            data=self.app.store.snapshot();data['videos']=[dict(key=k,uid='yt:UC'+'a'*22 if k==Y1 else '42',
                platform='youtube' if k==Y1 else 'bilibili',title='Title '+k,author='Creator',read=True,watched=False)
                for k in (B1,Y1,B2)];self.app.store._commit(data)

    def start(self):
        ticket=self.app.play_queue.start(B1,[B1,Y1]);self.app.play_ticket(ticket)
        return self.app.playback_config({'key':B1,'revision':ticket.revision})

    def test_gui_config_uses_cached_view_and_never_reads_store(self):
        config=self.start()
        with patch.object(self.app.store,'snapshot',side_effect=AssertionError('GUI read the store')):
            current=self.app.playback_config(config)
        self.assertEqual([B1,Y1],[v['key'] for v in current['queue']['items']])
        self.assertEqual(1,current['remaining'])

    def test_select_opens_only_a_valid_future_video_and_updates_main_ui(self):
        config=self.start()
        self.app.playback_signal(config,'select',{'value':Y1})
        self.app.youtube_player.open.assert_called_once()
        self.assertEqual('AAAAAAAAAAA',self.app.youtube_player.open.call_args.args[0])
        self.assertEqual(Y1,self.app.play_queue.current)
        self.assertEqual('playbackAdvanced',self.app.emit.call_args.args[0])

    def test_forged_or_stale_selection_cannot_leave_the_queue(self):
        config=self.start()
        for key in (B2,'https://evil.test/','yt:BBBBBBBBBBB'):
            self.app.playback_signal(config,'select',{'value':key})
        self.app.youtube_player.open.assert_not_called()
        self.app.play_queue.cancel()
        self.app.playback_signal(config,'select',{'value':Y1})
        self.app.youtube_player.open.assert_not_called()

    def test_removed_creator_is_reflected_in_the_next_cached_view(self):
        config=self.start()
        self.app.store.remove_channel('yt:UC'+'a'*22)
        self.app.refresh_playback_view()
        updated=self.app.playback_config(config)
        self.assertEqual(0,updated['remaining'])
        self.assertEqual([B1],[v['key'] for v in updated['queue']['items']])

    def test_stopping_from_transition_cancels_only_the_current_lease(self):
        config=self.start()
        self.app.playback_signal(config,'cancel',{})
        self.assertFalse(self.app.play_queue.is_active())
        self.app.focus_player.close_if_context.assert_called_once_with(config)
        new=self.app.play_queue.start(Y1)
        self.app.playback_signal(config,'cancel',{})
        self.assertTrue(self.app.play_queue.matches(new))

    def test_close_during_cross_platform_transition_cancels_destination(self):
        config=self.start()
        old={'key':B1,'revision':config['revision']}
        self.app.focus_player.context_snapshot.return_value=old
        self.app.focus_player.prepare_transition.return_value=True
        self.app.youtube_player.open.side_effect=lambda *a,**kw:self.app.playback_closed(old)
        self.app.playback_signal(config,'next',{})
        self.assertFalse(self.app.play_queue.is_active())
        self.assertIsNone(self.app.play_transition)
        self.app.youtube_player.close.assert_called()
        self.app.focus_player.close_if_context.assert_called_once_with(old)

    def test_close_in_store_read_after_advance_cannot_open_destination(self):
        config=self.start();old={'key':B1,'revision':config['revision']}
        original=self.app.store.snapshot;reads=0
        def snapshot():
            nonlocal reads
            reads+=1
            if reads==1:self.app.playback_closed(old)
            return original()
        with patch.object(self.app.store,'snapshot',side_effect=snapshot):
            self.app.playback_signal(config,'next',{})
        self.app.youtube_player.open.assert_not_called()
        self.assertFalse(self.app.play_queue.is_active())

    def test_failed_destination_never_reports_success_or_leaves_active_queue(self):
        config=self.start();old={'key':B1,'revision':config['revision']}
        self.app.focus_player.context_snapshot.return_value=old
        self.app.focus_player.prepare_transition.return_value=False
        self.app.youtube_player.ready.wait.return_value=False
        self.app.youtube_player.ready.is_set.return_value=False
        self.app.youtube_player.last_error='initialization failed'
        with self.assertLogs(level='ERROR'):self.app.playback_signal(config,'next',{})
        self.assertFalse(self.app.play_queue.is_active())
        self.assertEqual('playbackError',self.app.emit.call_args.args[0])
        self.app.youtube_player.close_if_context.assert_called()

    def test_serial_refresh_preserves_newer_view_without_blocking_gui_config(self):
        config=self.start();self.app.play_queue.unwatched_only=True
        original=self.app.play_queue.view;reading=threading.Event();release=threading.Event();first=True
        def view(ticket):
            nonlocal first
            result=original(ticket)
            if first:
                first=False;reading.set();release.wait(3)
            return result
        with patch.object(self.app.play_queue,'view',side_effect=view):
            old=threading.Thread(target=self.app.refresh_playback_view);old.start()
            self.assertTrue(reading.wait(1))
            self.app.store.set_watched(Y1)
            newer=threading.Thread(target=self.app.refresh_playback_view);newer.start()
            try:
                with patch.object(self.app.store,'snapshot',side_effect=AssertionError('GUI store read')):
                    self.assertEqual(1,self.app.playback_config(config)['remaining'])
            finally:
                release.set();old.join(3);newer.join(3)
        self.assertEqual(0,self.app.playback_config(config)['remaining'])


class TransitionWindowTests(unittest.TestCase):
    def test_cancelled_lease_cannot_initialize_or_navigate_late(self):
        manager=FocusPlayer(Path('assets'),lambda:{},lambda:None,playback_config=lambda context:None)
        window=Mock();manager._window=window;manager._playback_context={'key':Y1,'revision':2}
        manager._install_native=Mock();manager._navigate=Mock()
        manager._initialize(window,'test script')
        manager._install_native.assert_not_called()
        manager._navigate.assert_not_called()
    def test_loading_card_escapes_video_text_and_respects_reduced_motion(self):
        result=waiting_html({'title':'<script>alert(1)</script>','author':'A & B','platform':'youtube','part':2})
        self.assertIn('&lt;script&gt;',result)
        self.assertNotIn('<script>alert(1)',result)
        self.assertIn('A &amp; B',result)
        self.assertIn('YouTube · P2',result)
        self.assertIn('prefers-reduced-motion',result)

    def test_deferred_close_does_not_close_a_reused_window(self):
        manager=FocusPlayer(Path('assets'),lambda:{},lambda:None)
        window=Mock();manager._window=window
        manager._playback_context={'key':Y1,'revision':2}
        with patch('focus_player.close_window') as close:
            self.assertFalse(manager.close_if_context({'key':B1,'revision':1}))
            self.assertIs(manager._window,window)
            close.assert_not_called()
            self.assertTrue(manager.close_if_context({'key':Y1,'revision':2}))
            close.assert_called_once_with(window)


if __name__=='__main__':unittest.main()
