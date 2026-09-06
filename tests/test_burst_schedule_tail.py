import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock
from zoneinfo import ZoneInfo

from telegram_monitor import monitor
from telegram_monitor.state import State

NOW = 1788581220
FIRST = '我給你40分鐘想想自己的內在狀況'
TAIL = '時間到了再跟我分享'


def parts(texts):
    return [{'update_id': 800+i, 'message': {'chat': {'id': 1}, 'message_id': 900+i,
             'date': NOW+i, 'text': text}} for i, text in enumerate(texts)]


class ScheduleTailTest(unittest.TestCase):
    def test_screenshot_and_multiple_dependent_tails(self):
        for texts in ([FIRST, TAIL], [FIRST, TAIL, '到時候再告訴我']):
            self.assertEqual(monitor._burst_schedule_prefix(parts(texts), SimpleNamespace()), len(texts))

    def test_distinct_actions_never_disappear(self):
        for tail in ('再過20分鐘提醒我', '時間到了給我貼圖', '不要了', '/help',
                     '時間到了跟我分享天氣', '時間到了告訴我媽媽', '現在就說', '我今天還没讀經'):
            self.assertEqual(monitor._burst_schedule_prefix(parts([FIRST, tail]), SimpleNamespace()), 1)
        self.assertEqual(monitor._burst_schedule_prefix(parts(['我今天很忙', TAIL]), SimpleNamespace()), 1)

    def test_actual_transaction_creates_and_confirms_one_promise(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = State(os.path.join(tmp, 'state.json'))
            cfg = SimpleNamespace(dry_run=True, telegram_chat_id='', timezone='Asia/Taipei', burst_one_answer_enabled=True,
                                  scheduled_promise_enabled=True, sched_timeup_enabled=True,
                                  promise_emit_enabled=True, promise_sched_ttl_sec=21600)
            coach = SimpleNamespace(enabled=True, voice_schedule_ack=mock.Mock(return_value='好，40分鐘後再分享。'),
                                    meter=SimpleNamespace(record=lambda *a, **k: None))
            client = SimpleNamespace(dry_run=True, send=mock.Mock(return_value=True))
            update = monitor.build_coalesced_update({'type':'text', 'updates':parts([FIRST, TAIL])})
            with mock.patch.dict(monitor._TURN, {}, clear=True):
                monitor.handle_message(update, coach, None, {'records':[]}, None,
                                       state, client, cfg, ZoneInfo('Asia/Taipei'))
                # Replayed updates must not create or confirm the promise again.
                monitor.handle_message(update, coach, None, {'records':[]}, None,
                                       state, client, cfg, ZoneInfo('Asia/Taipei'))
            self.assertEqual(len(state.scheduled_promises), 1)
            self.assertEqual(state.scheduled_promises[0]['target_ts'], NOW + 2400)
            self.assertEqual(coach.voice_schedule_ack.call_count, 1)
            self.assertEqual(client.send.call_count, 1)
            wire = client.send.call_args.args[0]
            self.assertEqual(wire.count('40分鐘'), 1)


if __name__ == '__main__':
    unittest.main()
