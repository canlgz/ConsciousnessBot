import unittest
from types import SimpleNamespace
from unittest import mock

from telegram_monitor import monitor


TEXT = ('我剛剛說的是，我看到你最近真的持續在讀誦經書，從八月底到昨天都幾乎每天有記寫，'
        '而且昨天早上09:24還有一則「剛剛讀完了日常進度，第7，8品達成」。'
        '我現在在回頭看，我的確是這麼說的耶。')


class VoiceClausePacingTest(unittest.TestCase):
    def test_screenshot_long_sentence_splits_without_losing_quote(self):
        parts = monitor._paced_voice_split(TEXT)
        self.assertGreater(len(parts), 2)
        self.assertEqual(''.join(parts), TEXT)
        self.assertTrue(any('「剛剛讀完了日常進度，第7，8品達成」' in p for p in parts))
        self.assertGreater(len(set(map(len, parts))), 1)
        self.assertLess(max(map(len, parts)), 70)

    def test_quotes_and_code_are_not_fragmented(self):
        for text in ['他寫「今天好了嗎？還沒有。」，我先確認。',
                     '```python\nprint("hello")\n```', '參考 https://example.com/a?x=1。']:
            parts = monitor._paced_voice_split(text)
            self.assertEqual(''.join(parts), text)
            if '「' in text:
                self.assertTrue(any('「今天好了嗎？還沒有。」' in p for p in parts))
            else:
                self.assertEqual(parts, [text])

    def test_single_reply_wire_is_paced_even_with_one_bubble_cap(self):
        sent, typing = [], []
        client = SimpleNamespace(send=lambda s: sent.append(s) or True,
                                 send_typing=lambda: typing.append(True), dry_run=False)
        with mock.patch.dict(monitor._TURN, {'paced_bubbles': True, 'bubbles': 1}, clear=True), \
                mock.patch.object(monitor, '_sleep'), mock.patch.object(monitor, '_jitter', return_value=1):
            monitor._say(client, TEXT)
        self.assertGreater(len(sent), 2)
        self.assertEqual(''.join(sent), TEXT)
        self.assertGreaterEqual(len(typing), len(sent) - 1)

    def test_burst_uses_same_splitter_but_data_remains_atomic(self):
        parts = monitor._burst_delivery_bubbles(TEXT, [{'data': '', 'voices': [TEXT]}], enabled=True)
        self.assertGreater(len(parts), 2)
        self.assertEqual(''.join(parts), TEXT)
        self.assertEqual(monitor._burst_delivery_bubbles(TEXT, [{'data': TEXT}], enabled=True), [TEXT])

    def test_short_answers_stay_short(self):
        self.assertEqual(monitor._paced_voice_split('晚安，好好休息。'), ['晚安，好好休息。'])

    def test_single_turn_activates_pacing_and_restores_scope_on_error(self):
        cfg = SimpleNamespace(burst_paced_bubbles_enabled=True)
        def inner(*args):
            self.assertTrue(monitor._TURN['paced_bubbles'])
            raise RuntimeError('test scope')
        with mock.patch.dict(monitor._TURN, {'paced_bubbles': False}, clear=True), \
                mock.patch.object(monitor, '_handle_message_inner', side_effect=inner):
            with self.assertRaises(RuntimeError):
                monitor.handle_message({}, None, None, None, None, None, None, cfg, None)
            self.assertFalse(monitor._TURN['paced_bubbles'])
