import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock
from telegram_monitor import monitor, silence

NOW = datetime(2026, 9, 5, 3, tzinfo=timezone.utc)

def history(text):
    return [{'role': 'user', 'text': text, 'ts': NOW.timestamp() - 1801},
            {'role': 'model', 'text': '跟週末的安排有關係嗎？', 'ts': NOW.timestamp() - 1800}]


class SilencePolicyTest(unittest.TestCase):
    def test_context_changes_hypotheses_not_authorization(self):
        for text, kind in [('今天週六', 'unknown'), ('晚安，我累了', 'away'),
                           ('你又跳針了', 'boundary'), ('查好再告訴我', 'awaiting_result')]:
            reading = silence.interpret(history(text), NOW.timestamp())
            self.assertEqual(reading['kind'], kind)
            self.assertFalse(reading['send_followup'])
            self.assertTrue(reading['evidence'])
        self.assertEqual(len(silence.interpret(history('今天週六'), NOW.timestamp())['possibilities']), 3)

    def test_new_contact_or_reply_invalidates_old_silence(self):
        h = history('今天週六')
        self.assertIsNone(silence.interpret(h, NOW.timestamp(), last_contact_ts=NOW.timestamp() - 10))
        h.append({'role': 'user', 'text': '我回來了', 'ts': NOW.timestamp()})
        self.assertIsNone(silence.interpret(h, NOW.timestamp()))

    def test_both_timeout_exits_stay_silent_across_repeated_ticks(self):
        cfg = SimpleNamespace(unanswered_proactive_guard_enabled=True, soothe_after_min=7)
        state = SimpleNamespace(convo_history=history('今天週六'), last_user_msg_ts=NOW.timestamp()-1801)
        client, coach = mock.Mock(), mock.Mock()
        for _ in range(3):
            monitor._soothe_unanswered(client, state, cfg, coach, NOW)
            self.assertFalse(monitor._maybe_close_round(client, state, cfg, coach, NOW))
        self.assertEqual(state.silence_reading['kind'], 'unknown')
        self.assertEqual(client.mock_calls, [])
        self.assertEqual(coach.mock_calls, [])

    def test_waiting_for_result_does_not_cancel_obligations(self):
        promises = [{'status': 'pending', 'when': NOW.timestamp()+60}]
        state = SimpleNamespace(convo_history=history('查好告訴我'), scheduled_promises=promises)
        monitor._silence_policy(state, SimpleNamespace(unanswered_proactive_guard_enabled=True), NOW)
        self.assertIs(state.scheduled_promises, promises)
        self.assertEqual(state.silence_reading['action'], 'await_task_result')

    def test_stop_feedback_is_narrow_and_does_not_swallow_other_requests(self):
        for text in ['你幹嘛跳針了？', '不要再問了', '你又跳針']:
            self.assertTrue(silence.is_stop_feedback(text), text)
        for text in ['不要再問了，請查明天天氣', '他說你跳針', '什麼是跳針']:
            self.assertFalse(silence.is_stop_feedback(text), text)
