import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from telegram_monitor import association, intent, monitor, referent
from telegram_monitor.state import State


class AssociationMethodRouteTest(unittest.TestCase):
    def test_method_not_mood_or_change_monologue(self):
        for text in ("你現在聯想的方式有改變嗎", "你的聯想方法跟以前有什麼不同",
                     "你怎麼聯想的", "聯想的依據是什麼"):
            self.assertEqual(intent.resolve(text, referent.Referent()).kind,
                             "self_mechanism", text)

    def test_content_and_user_method_are_not_intercepted(self):
        for text in ("你剛剛聯想了什麼", "我的聯想方式有改變嗎",
                     "分享一個新的聯想", "你現在感覺如何", "你怎麼還沒開始分享聯想"):
            self.assertNotEqual(intent.resolve(text, referent.Referent()).kind,
                                "self_mechanism", text)

    def test_future_request_keeps_schedule(self):
        self.assertEqual(intent.resolve("十分鐘後告訴我你聯想的方法",
                                        referent.Referent()).kind, "scheduled_promise")

    def test_disabled_association_is_not_claimed_active(self):
        self.assertIn("沒有啟用", association.method_reply(
            SimpleNamespace(association_enabled=False)))

    def test_screenshot_through_public_handler(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = State(os.path.join(tmp, "state.json"))
            state.owner_folder_id = "F"
            state.convo_history = [
                {"role": "model", "text": "我剛剛覺得很餓，翻了國科會計畫", "ts": 1699999900}]
            client = SimpleNamespace(sent=[], dry_run=True)
            client.send = lambda text: client.sent.append(text) or True
            coach = SimpleNamespace(enabled=True,
                                    meter=SimpleNamespace(record=lambda *a, **k: None),
                                    voice_mechanism=mock.Mock(side_effect=AssertionError("not grounded")))
            text = "你現在聯想的方式有改變嗎"
            update = {"message": {"chat": {"id": 1}, "text": text, "date": 1700000000}}
            with mock.patch.object(monitor, "_maybe_propose_skill"), \
                 mock.patch("telegram_monitor.selfstate.render_bodystate",
                            side_effect=AssertionError("wrong state route")):
                monitor.handle_message(update, coach, None, {"meta": {}, "records": []}, None,
                                       state, client, SimpleNamespace(dry_run=True,
                                       telegram_chat_id="", mood_gain=1.0), None)
            sent = "".join(client.sent)
            self.assertIn("候選", sent)
            self.assertIn("版本", sent)
            self.assertNotIn("很餓", sent)
            self.assertNotIn("國科會", sent)
            self.assertNotIn("你那邊的門檻", sent)
            coach.voice_mechanism.assert_not_called()
