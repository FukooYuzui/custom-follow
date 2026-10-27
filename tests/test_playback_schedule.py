import copy
from datetime import datetime, timedelta
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import Mock, patch

import main
from playback_queue import FollowedQueue
from playback_schedule import DEFAULT_SCHEDULE, due_slot, next_due, unwatched_order
from state import Store
from bili_api import Feed, Video

B1, B2, Y1 = 'BV1B7411m7LV', 'BV1xx411c7Ug', 'yt:AAAAAAAAAAA'
CID = 'UC' + 'a' * 22
NOW = datetime(2026, 10, 27, 20, 0, 5)


class ScheduleFixture(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory(); self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / 'state.json'
        self.store = Store(self.path)
        self.store.add_channel('42', 'Bili')
        self.store.add_channel(CID, 'Tube', 'youtube')
        with self.store._lock:
            data = self.store.snapshot()
            data['videos'] = [dict(key=k, uid='yt:' + CID if k == Y1 else '42', title=k,
                platform='youtube' if k == Y1 else 'bilibili', read=True, published_at=i + 1,
                watched=False) for i, k in enumerate((B1, Y1, B2))]
            self.store._commit(data)

    def enable(self, **values):
        return self.store.save_playback_schedule(dict(enabled=True, time='20:00', **values), now=NOW)


class ScheduleTests(ScheduleFixture):
    def test_watched_status_survives_recurring_fetch(self):
        store = Store(self.path.parent / 'refresh.json'); store.add_channel('42')
        feed = Feed('Bili', [Video(B1, 'video', 'https://www.bilibili.com/video/'+B1, 1, 'Bili')])
        store.ingest('42', feed, now=10)
        store.set_watched(B1, now=11)
        store.ingest('42', feed, now=20)
        item = Store(store.path).snapshot()['videos'][0]
        self.assertTrue(item['watched']); self.assertEqual(11, item['watched_at'])
    def test_exact_minute_once_per_occurrence_and_restart(self):
        config = self.enable()
        self.assertIsNone(due_slot(config, NOW.replace(hour=19, minute=59)))
        lease = self.store.claim_playback_schedule(NOW)
        self.assertIsNotNone(lease)
        self.assertIsNone(self.store.claim_playback_schedule(NOW + timedelta(seconds=10)))
        reopened = Store(self.path)
        self.assertIsNone(reopened.claim_playback_schedule(NOW + timedelta(seconds=20)))
        self.assertIsNotNone(reopened.claim_playback_schedule(NOW + timedelta(days=1)))

    def test_sleep_or_restart_after_due_minute_never_catches_up(self):
        config = self.enable()
        self.assertIsNone(due_slot(config, NOW.replace(minute=1, second=0)))
        self.assertEqual('2026-10-28T20:00', next_due(config, NOW.replace(hour=21)))

    def test_once_disables_itself_and_does_not_run_on_another_day(self):
        self.enable(mode='once', date='2026-10-27')
        self.assertIsNotNone(self.store.claim_playback_schedule(NOW))
        self.assertFalse(self.store.snapshot()['play_schedule']['enabled'])
        self.assertIsNone(self.store.claim_playback_schedule(NOW + timedelta(days=1)))

    def test_midnight_is_eligible_and_clock_rollback_does_not_duplicate(self):
        config = dict(DEFAULT_SCHEDULE, enabled=True, time='00:00')
        midnight = NOW.replace(hour=0, minute=0)
        slot = due_slot(config, midnight)
        self.assertEqual('2026-10-27T00:00', slot)
        config['last_slot'] = slot
        self.assertIsNone(due_slot(config, midnight))
        self.assertEqual('2026-10-28T00:00', next_due(config, midnight))

    def test_invalid_inputs_and_past_once_do_not_change_saved_plan(self):
        before = self.store.snapshot()
        for values in ({'time': '24:00'}, {'enabled': 'yes'}, {'mode': 'weekly'}, {'date': '2026-02-30'},
                       {'last_slot': 'injected'}, {'platform': 'unknown'}, {'channel_uid': '99'},
                       {'enabled': True, 'mode': 'once', 'date': '2026-10-26'}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.store.save_playback_schedule(values, now=NOW)
            self.assertEqual(before, self.store.snapshot())

    def test_claim_is_atomic_between_threads(self):
        self.enable(); results = []
        threads = [threading.Thread(target=lambda: results.append(self.store.claim_playback_schedule(NOW))) for _ in range(8)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(1, sum(x is not None for x in results))

    def test_editing_schedule_invalidates_old_result(self):
        self.enable(); lease = self.store.claim_playback_schedule(NOW)
        self.store.save_playback_schedule({'enabled': False}, now=NOW)
        self.assertFalse(self.store.finish_playback_schedule(lease, 'stale'))

    def test_selection_requires_followed_unwatched_and_applies_scope_and_order(self):
        config = dict(DEFAULT_SCHEDULE)
        self.assertEqual([B1, Y1, B2], unwatched_order(self.store.snapshot(), config))
        config['order'] = 'newest'
        self.store.set_watched(Y1)
        self.assertEqual([B2, B1], unwatched_order(self.store.snapshot(), config))
        config.update(platform='youtube')
        self.assertEqual([], unwatched_order(self.store.snapshot(), config))
        config.update(platform='', channel_uid='42')
        self.assertEqual([B2, B1], unwatched_order(self.store.snapshot(), config))
        self.store.remove_channel('42')
        self.assertEqual([], unwatched_order(self.store.snapshot(), config))

    def test_unknown_dates_sort_last(self):
        data = self.store.snapshot(); next(v for v in data['videos'] if v['key'] == B1)['published_at'] = 0
        for order in ('newest', 'oldest'):
            self.assertEqual(B1, unwatched_order(data, dict(DEFAULT_SCHEDULE, order=order))[-1])

    def test_watched_migration_does_not_misuse_notification_read_flag(self):
        data = self.store.snapshot()
        for v in data['videos']: v.pop('watched')
        data.pop('play_schedule')
        self.path.write_text(json.dumps(data), encoding='utf8')
        migrated = Store(self.path)
        self.assertTrue(all(v['read'] and not v['watched'] for v in migrated.snapshot()['videos']))
        self.assertFalse(migrated.snapshot()['play_schedule']['enabled'])
        migrated.set_watched(B1, now=10)
        self.assertTrue(next(v for v in Store(self.path).snapshot()['videos'] if v['key'] == B1)['watched'])
        migrated.set_watched(B1, False)
        self.assertFalse(next(v for v in migrated.snapshot()['videos'] if v['key'] == B1)['watched'])

    def test_queue_skips_watched_changes_and_does_not_interrupt_active_selection(self):
        queue = FollowedQueue(self.store.snapshot)
        t = queue.start(B1, [B1, Y1, B2], unwatched_only=True)
        self.store.set_watched(Y1)
        self.assertEqual(B2, queue.move(t).key)
        with self.assertRaises(ValueError): queue.start(Y1, if_idle=True)
        old = queue.revision; queue.cancel()
        with self.assertRaises(ValueError): queue.start(B1, if_idle=True, revision=old)


class ScheduledApplicationTests(ScheduleFixture):
    def setUp(self):
        super().setUp()
        for name, value in (('DATA', self.path.parent), ('load_session', Mock(return_value={})), ('get_autostart', Mock(return_value=False))):
            p = patch.object(main, name, value); p.start(); self.addCleanup(p.stop)
        self.app = main.Application(); self.store = self.app.store
        self.app.focus_player = Mock(_fullscreen=None)
        self.app.youtube_player = Mock(_fullscreen=None)
        self.app.emit = Mock(); self.app.changed = Mock()
        self.app.ready.set()

    def test_hidden_app_opens_only_unwatched_and_reports_schedule(self):
        self.enable(); self.store.set_watched(B1)
        self.app.playback_allowed = False
        self.app.schedule_tick(NOW)
        self.app.youtube_player.open.assert_called_once()
        self.assertEqual(Y1, self.app.play_queue.current)
        self.assertTrue(self.app.play_queue.unwatched_only)
        self.assertEqual('playbackStarted', self.app.emit.call_args.args[0])
        self.assertTrue(self.app.playback_allowed)

    def test_active_or_embedded_playback_is_not_interrupted(self):
        self.enable(); self.app.embedded_active = True
        self.assertIn('跳过', self.app.schedule_tick(NOW))
        self.app.youtube_player.open.assert_not_called(); self.app.focus_player.open.assert_not_called()
        self.app.embedded_active = False
        self.app.play_queue.start(B1)
        self.assertIn('跳过', self.app.schedule_tick(NOW + timedelta(days=1)))

    def test_empty_plan_claims_once_without_creating_player(self):
        self.enable()
        for k in (B1, B2, Y1): self.store.set_watched(k)
        self.assertIn('没有', self.app.schedule_tick(NOW))
        self.assertIsNone(self.app.schedule_tick(NOW))
        self.app.focus_player.open.assert_not_called()

    def test_manual_open_does_not_mark_watched_but_completion_does_even_autoplay_off(self):
        t = self.app.play_queue.start(B1)
        self.app.play_ticket(t)
        self.assertFalse(next(v for v in self.store.snapshot()['videos'] if v['key'] == B1)['watched'])
        self.app.set_autoplay(False)
        self.app.playback_signal(self.app.playback_config({'key': B1, 'revision': t.revision}), 'ended', {'part': 1, 'pages': 1})
        self.assertTrue(next(v for v in self.store.snapshot()['videos'] if v['key'] == B1)['watched'])
        self.assertFalse(self.app.play_queue.is_active())
        self.app.youtube_player.open.assert_not_called()

    def test_partial_work_is_not_marked_watched_and_closed_app_cannot_start(self):
        t = self.app.play_queue.start(B1)
        self.app.set_autoplay(False)
        self.app.playback_signal(self.app.playback_config({'key': B1, 'revision': t.revision}), 'ended', {'part': 1, 'pages': 2})
        self.assertFalse(next(v for v in self.store.snapshot()['videos'] if v['key'] == B1)['watched'])
        self.app.quitting = True; self.enable()
        self.assertIsNone(self.app.schedule_tick(NOW))

    def test_failed_open_is_not_watched_and_does_not_block_future_schedule(self):
        self.enable(); self.app.focus_player.open.side_effect = RuntimeError('test failure')
        with self.assertLogs(level='ERROR'): self.app.schedule_tick(NOW)
        self.assertFalse(self.app.play_queue.is_active())
        self.assertTrue(all(not v['watched'] for v in self.store.snapshot()['videos']))

    def test_disabling_while_launching_cancels_lease(self):
        self.enable()
        def cancel(*args, **kwargs): main.Api(self.app).save_playback_schedule({'enabled': False})
        self.app.focus_player.open.side_effect = cancel
        self.app.schedule_tick(NOW)
        self.assertFalse(self.app.play_queue.is_active())
        self.assertFalse(self.store.snapshot()['play_schedule']['enabled'])


if __name__ == '__main__': unittest.main()
