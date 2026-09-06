"""⏳ 時間綿延／意識之流：厚當下（滯留尾＋前攝＋閒置漂移），每拍演進＝沒事時也有連續內在生活；
前攝被打斷＝一驚（回灌內在熵）；可被問「你剛在想什麼/發呆在想什麼」。"""

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from telegram_monitor import duration, intent, monitor, referent, selfstate
from telegram_monitor.state import State

NOW = 1_700_000_000


def _focus(source, content="x", sal=0.7):
    return {"source": source, "content": content, "salience": sal}


class StreamDynamicsTest(unittest.TestCase):
    def test_retention_tail_gives_thickness(self):
        s = duration.fresh()
        s = duration.tick(s, _focus("hunger", "悶著等不到新的"), NOW)
        s = duration.tick(s, _focus("feeling", "「A」這條線繃起來"), NOW + 3)   # 內容變 → 前一個滑進滯留尾
        self.assertEqual(s["impression"]["content"], "「A」這條線繃起來")
        self.assertTrue(any("悶著" in r["content"] for r in s["retentions"]))     # 剛流過的還掛在尾巴
        self.assertGreater(duration.thickness(s), 0)

    def test_protention_fulfilled_is_continuous(self):
        s = duration.fresh()
        s = duration.tick(s, _focus("hunger", "悶"), NOW)            # onset
        s = duration.tick(s, _focus("hunger", "悶"), NOW + 3)        # 同源 → 前攝應驗
        self.assertEqual(s["last_status"], "fulfilled")
        self.assertEqual(s["texture"], "continuous")

    def test_broken_protention_jolts(self):
        s = duration.fresh()
        s = duration.tick(s, _focus("hunger", "悶"), NOW)
        s = duration.tick(s, _focus("hunger", "悶"), NOW + 3)        # 預期 hunger 接下去
        s = duration.tick(s, _focus("feeling", "一條線突然成形"), NOW + 6)   # 來的卻是 feeling → 被打斷
        self.assertEqual(s["last_status"], "surprised")
        self.assertEqual(s["texture"], "jolted")

    def test_idle_drift_then_stagnant_is_continuous_inner_life(self):
        s = duration.fresh()
        s = duration.tick(s, _focus("feeling", "「A」線"), NOW)
        s = duration.tick(s, None, NOW + 3)                          # 沒焦點 → 漂移（飄回剛才的）
        self.assertEqual(s["texture"], "wandering")
        self.assertIn("飄回剛才", s["impression"]["content"])
        for i in range(4):                                          # 持續沒東西 → 停滯（但流仍每拍在動）
            s = duration.tick(s, None, NOW + 6 + i * 3)
        self.assertEqual(s["texture"], "stagnant")
        self.assertGreaterEqual(s["drift_laps"], duration._STAGNANT_LAPS)


class DetectRouteTest(unittest.TestCase):
    def test_detector(self):
        for q in ["你剛剛在想什麼", "你發呆在想什麼", "你思緒怎麼流動", "從剛才到現在你腦子在轉什麼", "你在放空什麼"]:
            self.assertTrue(selfstate.is_stream_question(q), q)

    def test_routes(self):
        def k(t):
            return intent.resolve(t, referent.Referent()).kind
        self.assertEqual(k("你剛剛在想什麼"), "self_stream")        # 流／剛過去 → self_stream（贏過 attention 的「在想什麼」）
        self.assertEqual(k("你發呆在想什麼"), "self_stream")
        self.assertEqual(k("你現在在想什麼"), "self_attention")     # 此刻單一焦點仍走 attention
        self.assertEqual(k("你現在怎樣"), "self_state")             # 泛泛現況仍是身體狀態


class FactsTest(unittest.TestCase):
    def test_stream_facts_show_flow(self):
        s = duration.fresh()
        s = duration.tick(s, _focus("hunger", "悶著等不到新的"), NOW)
        s = duration.tick(s, _focus("feeling", "「A」線繃起來"), NOW + 3)
        f = duration.stream_facts(s)
        self.assertIn("原印象", f)
        self.assertIn("A", f)
        self.assertIn("悶著", f)                                    # 滯留尾（剛流過的）也在
        self.assertIn("前攝", f)                                    # 對下一刻的預期

    def test_empty_stream_is_quiet(self):
        self.assertIn("靜", duration.stream_facts(None))


class HandleTest(unittest.TestCase):
    def test_stream_question_reports_flow(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        st = duration.fresh()
        st = duration.tick(st, _focus("hunger", "悶著等不到新的"), NOW)
        st = duration.tick(st, _focus("feeling", "「課堂評量」這條線繃起來"), NOW + 3)
        s.stream = st
        cap = {}

        def fake_reply(q, brief, hist, mood_hint="", now_ts=None, self_presence=False):
            cap["brief"] = brief
            return "這會兒心裡是那條課堂評量的線，剛才那股悶還淡淡掛著。"

        coach = SimpleNamespace(enabled=True, meter=SimpleNamespace(record=lambda *a, **k: None), reply=fake_reply)
        client = SimpleNamespace(sent=[], dry_run=False, send=lambda t: client.sent.append(t) or True)
        up = {"message": {"chat": {"id": 1}, "text": "你剛剛到現在腦子裡在流動什麼", "date": NOW + 5}}
        monitor.handle_message(up, coach, None, {"meta": {}, "records": []}, None,
                               s, client, SimpleNamespace(dry_run=False, telegram_chat_id="", mood_gain=1.0), None)
        self.assertIn("原印象", cap.get("brief", ""))               # 綿延事實餵進回覆
        self.assertIn("課堂評量", "".join(client.sent))


if __name__ == "__main__":
    unittest.main()
