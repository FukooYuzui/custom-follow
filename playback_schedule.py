"""Local-clock playback appointments and followed-only unwatched selection."""
from datetime import date, datetime, timedelta
import math
import re

from playback_queue import owned_videos

DEFAULT_SCHEDULE = {
    'enabled': False, 'mode': 'daily', 'time': '20:00', 'date': '',
    'platform': '', 'channel_uid': '', 'order': 'oldest',
    'generation': 0, 'last_slot': '', 'last_result': '', 'last_run_at': 0,
}
EDITABLE_FIELDS = {'enabled', 'mode', 'time', 'date', 'platform', 'channel_uid', 'order'}


def validate_schedule(value):
    if not isinstance(value, dict) or set(value) != set(DEFAULT_SCHEDULE):
        raise ValueError('定时播放设置格式无效。')
    if type(value['enabled']) is not bool or value['mode'] not in ('daily', 'once'):
        raise ValueError('请选择每天播放或仅播放一次。')
    if not isinstance(value['time'], str) or not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]', value['time']):
        raise ValueError('播放时间请使用 00:00 至 23:59 格式。')
    if not isinstance(value['date'], str):
        raise ValueError('请选择有效的播放日期。')
    if value['date']:
        try:
            if not isinstance(value['date'], str) or date.fromisoformat(value['date']).isoformat() != value['date']:
                raise ValueError()
        except (TypeError, ValueError):
            raise ValueError('请选择有效的播放日期。') from None
    if value['enabled'] and value['mode'] == 'once' and not value['date']:
        raise ValueError('仅播放一次时需要选择日期。')
    if value['platform'] not in ('', 'bilibili', 'youtube') or value['order'] not in ('oldest', 'newest'):
        raise ValueError('播放范围或排序方式无效。')
    uid = value['channel_uid']
    if not isinstance(uid, str) or uid and not re.fullmatch(r'(?:[1-9]\d{0,19}|yt:UC[A-Za-z0-9_-]{22})', uid):
        raise ValueError('请选择程序中已添加的博主。')
    if type(value['generation']) is not int or value['generation'] < 0:
        raise ValueError('定时播放版本无效。')
    if not isinstance(value['last_slot'], str) or not isinstance(value['last_result'], str):
        raise ValueError('定时播放记录无效。')
    if isinstance(value['last_run_at'], bool) or not isinstance(value['last_run_at'], (float, int)) or not math.isfinite(value['last_run_at']) or value['last_run_at'] < 0:
        raise ValueError('定时播放记录时间无效。')


def target_today(config, now):
    day = now.date() if config['mode'] == 'daily' else date.fromisoformat(config['date'])
    return datetime.combine(day, datetime.strptime(config['time'], '%H:%M').time())


def due_slot(config, now=None):
    now = now or datetime.now()
    if not config['enabled']:
        return None
    target = target_today(config, now)
    slot = target.isoformat(timespec='minutes')
    # Only the selected minute is eligible: waking/restarting later never
    # unexpectedly plays an overdue appointment.
    if 0 <= (now - target).total_seconds() < 60 and config['last_slot'] != slot:
        return slot
    return None


def next_due(config, now=None):
    if not config['enabled']:
        return ''
    now = now or datetime.now()
    target = target_today(config, now)
    if config['mode'] == 'daily':
        if now >= target + timedelta(minutes=1) or config['last_slot'] == target.isoformat(timespec='minutes'):
            target += timedelta(days=1)
    elif now >= target + timedelta(minutes=1) or config['last_slot'] == target.isoformat(timespec='minutes'):
        return ''
    return target.isoformat(timespec='minutes')


def unwatched_order(snapshot, config):
    videos = [v for v in owned_videos(snapshot).values() if not v.get('watched', False)
              and (not config['platform'] or v.get('platform', 'bilibili') == config['platform'])
              and (not config['channel_uid'] or v['uid'] == config['channel_uid'])]
    known = [v for v in videos if v.get('published_at', 0) > 0]
    unknown = [v for v in videos if not v.get('published_at', 0) > 0]
    known.sort(key=lambda v: (v['published_at'], v['key']), reverse=config['order'] == 'newest')
    return [v['key'] for v in known + sorted(unknown, key=lambda v: v['key'])]
