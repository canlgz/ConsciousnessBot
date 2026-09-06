import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from telegram_monitor import monitor


VOICE = ('你說的是實際回覆沒有變好。\n'
         '我前面把程式修改說成已經改善，說得太滿了；是否改善，還是要看實際對話。\n'
         '這兩件事我應該分清楚，不該再用一段保證代替結果。')
DATA = '可核對的紀錄：\n第一項。第二項。'


class MixedBubbleDeliveryTest(unittest.TestCase):
    def test_capture_to_real_delivery_keeps_voice_paced(self):
        client = SimpleNamespace(dry_run=False, send=Mock(return_value=True), send_typing=Mock())
        cap = monitor._BurstReplyCapture(client)
        with patch.dict(monitor._TURN, {'paced_bubbles':True}, clear=True):
            cap.send(DATA)  # direct deterministic/evidence path
            monitor._say(cap, VOICE)
        segment = monitor._burst_capture_segment(cap.events)
        wire = monitor._bounded_burst_wire([segment])
        bubbles = monitor._burst_delivery_bubbles(wire, [segment], enabled=True)
        self.assertEqual(bubbles[0], DATA)
        self.assertGreater(len(bubbles), 2)
        self.assertLess(max(map(len,bubbles[1:])), 90)
        with patch.object(monitor, '_sleep') as sleep:
            ok, results = monitor._send_burst_bubbles(client, bubbles)
        self.assertTrue(ok)
        self.assertEqual(len(results), len(bubbles))
        self.assertEqual(client.send_typing.call_count, len(bubbles)-1)
        self.assertEqual(sleep.call_count, len(bubbles)-1)
        self.assertEqual(''.join(bubbles).replace('\n',''), wire.replace('\n',''))

    def test_data_in_middle_does_not_swallow_either_voice(self):
        wire = VOICE + '\n' + DATA + '\n' + VOICE
        parts = monitor._burst_delivery_bubbles(wire, [{'data':DATA,'voices':[VOICE,VOICE]}], True)
        self.assertIn(DATA, parts)
        self.assertGreater(len(parts), 3)
        self.assertEqual(''.join(parts).replace('\n',''), wire.replace('\n',''))

    def test_failed_bubble_stops_before_later_text(self):
        client = SimpleNamespace(dry_run=True, send=Mock(side_effect=[True,False,True]))
        ok, results = monitor._send_burst_bubbles(client, ['短句。','較長的一段。','不應送出。'])
        self.assertFalse(ok)
        self.assertEqual(results,[True,False])
        self.assertEqual(client.send.call_count,2)

    def test_all_voice_is_paced_and_protected_data_remains_whole(self):
        self.assertGreater(len(monitor._burst_delivery_bubbles(VOICE,[{'data':'','voices':[VOICE]}],True)),1)
        self.assertEqual(monitor._burst_delivery_bubbles(DATA,[{'data':DATA}],True),[DATA])
        self.assertEqual(monitor._burst_delivery_bubbles(VOICE,[{'data':'not in wire'}],True),[VOICE])
