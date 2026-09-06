import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo
from telegram_monitor import monitor
from telegram_monitor.state import State


class StickerInventoryTest(unittest.TestCase):
    def test_screenshot_is_one_inventory_answer_without_sending(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = State(os.path.join(tmp, 's.json'))
            state.known_sticker_ids = [
                {'file_id':'one', 'emoji':'🙄', 'valence':'positive', 'desc':''},
                {'file_id':'two', 'emoji':'😘', 'valence':'positive', 'desc':''}]
            state.last_sticker_id = 'older'
            state.last_sticker_ts = 1788580000
            client = SimpleNamespace(dry_run=True, send=Mock(return_value=True), send_sticker=Mock(), send_typing=Mock())
            coach = SimpleNamespace(enabled=True, reply=Mock(side_effect=AssertionError('no invented inventory')))
            cfg = SimpleNamespace(dry_run=True, telegram_chat_id='', burst_one_answer_enabled=True,
                                  burst_paced_bubbles_enabled=True, timezone='Asia/Taipei')
            texts = ['你會使用哪些貼圖', '我都看到那幾個同樣的貼圖樣式']
            updates = [{'update_id':i+100, 'message':{'chat':{'id':1}, 'text':t, 'date':1788581220+i}}
                       for i,t in enumerate(texts)]
            update = monitor.build_coalesced_update({'type':'text', 'updates':updates})
            with patch.dict(monitor._TURN, {}, clear=True), patch.object(monitor, '_sleep'):
                monitor.handle_message(update, coach, None, {}, None, state, client, cfg, ZoneInfo('Asia/Taipei'))
            wire = ''.join(c.args[0] for c in client.send.call_args_list)
            self.assertEqual(wire.count('2 張'), 1)
            self.assertNotIn('剛剛送', wire)
            self.assertNotIn('🙄', wire)
            self.assertNotIn('你說的', wire)
            client.send_sticker.assert_not_called()
            self.assertEqual(state.last_sticker_id, 'older')

    def test_request_and_other_intents_not_swallowed(self):
        for text in ['送我一張貼圖', '你會使用哪些貼圖，然後十分鐘後叫我', '不要再傳貼圖', '你剛送了什麼貼圖']:
            self.assertFalse(monitor._sticker_inventory_question(text))

    def test_inventory_uses_sendable_pool_not_emoji(self):
        state = SimpleNamespace(known_sticker_ids=[{'file_id':'x','valence':'negative','emoji':'😘'}])
        self.assertIn('沒有可供', monitor._sticker_inventory_reply(state, SimpleNamespace()))
        self.assertIn('1 張', monitor._sticker_inventory_reply(state, SimpleNamespace(sticker_file_ids=['x','x'])))

    def test_screenshot_quote_owner_and_time(self):
        text = '欸，你說的那個「剛剛送你的🙄」，我這邊的紀錄顯示。'
        ground = {'now_ts':1788581220, 'user':['你會使用哪些貼圖'],
                  'model':[{'text':'像是剛剛送你的那個🙄，還有😘。','ts':1788494820}]}
        result, changed = monitor._quote_speaker_fix(text, ground)
        self.assertTrue(changed)
        self.assertNotIn('你說的', result)
        self.assertIn('我', result)
        self.assertNotIn('我剛說', result)

    def test_ambiguous_quote_not_reassigned(self):
        q = '剛剛送你的🙄'
        text = f'你說的那個「{q}」'
        result, changed = monitor._quote_speaker_fix(text, {'user':[q], 'model':[q]})
        self.assertFalse(changed)
        self.assertEqual(result, text)
