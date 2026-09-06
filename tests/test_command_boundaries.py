import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo
from telegram_monitor import monitor
from telegram_monitor.state import State


class CommandBoundariesTest(unittest.TestCase):
    def run_command(self, text, cost='費用估算 NT$9.5', error=None):
        with tempfile.TemporaryDirectory() as tmp:
            state = State(os.path.join(tmp, 'state.json'))
            state.engrams = [{'sentinel': 'must survive unknown commands'}]
            client = SimpleNamespace(send=Mock(return_value=True), dry_run=True)
            coach = SimpleNamespace(enabled=False, meter=None)
            cfg = SimpleNamespace(dry_run=True, telegram_chat_id='')
            snap = SimpleNamespace(funnel={'candidate':1, 'context':2, 'journey':3}, summary={'streak':4})
            with patch.dict(monitor._TURN, {}, clear=True), \
                 patch.object(monitor.datatools, 'api_cost', return_value=cost, side_effect=error) as query:
                monitor.handle_message({'message':{'chat':{'id':1}, 'text':text, 'date':1788581220}},
                                       coach, None, {}, snap, state, client, cfg, ZoneInfo('Asia/Taipei'))
            return client.send.call_args.args[0], state, query

    def test_cost_without_model_is_real_query(self):
        for text in ('/cost', '/COST'):
            reply, _, query = self.run_command(text)
            self.assertEqual(reply, '費用估算 NT$9.5')
            query.assert_called_once()

    def test_failed_query_not_misunderstanding_or_success(self):
        reply, _, _ = self.run_command('/cost', error=RuntimeError('secret detail'))
        self.assertIn('讀取費用紀錄失敗', reply)
        self.assertNotIn('secret detail', reply)

    def test_unknown_commands_never_execute_prefixes_or_echo_arguments(self):
        for text in ('/xyz password123', '/forgetall', '/kidding 2', '/costly', '/xyz 敏感度 5'):
            reply, state, query = self.run_command(text)
            self.assertIn('還不支援', reply)
            self.assertNotIn('嗨，我在', reply)
            self.assertNotIn('password123', reply)
            self.assertEqual(len(state.engrams), 1)
            self.assertIsNone(state.sensitivity_override)
            query.assert_not_called()

    def test_cost_hint_and_unsupported_arguments(self):
        reply, _, query = self.run_command('/costs')
        self.assertIn('你是想查', reply)
        query.assert_not_called()
        reply, _, query = self.run_command('/cost month')
        self.assertIn('不支援附加參數', reply)
        query.assert_not_called()

    def test_disabled_function_distinguished_from_unknown(self):
        reply, _, _ = self.run_command('/agency')
        self.assertIn('沒有啟用', reply)

    def test_help_and_start_still_work(self):
        self.assertIn('/cost', self.run_command('/help')[0])
        self.assertIn('嗨，我在', self.run_command('/start')[0])
