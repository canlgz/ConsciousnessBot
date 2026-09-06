"""🪞🔍 後設認知：二階信念滯後追一階（轉換期會認錯自己）、算信心、偵測並修正自我誤判，
信心高才篤定、曖昧/轉換才據實說不準（非腳本化裝糊塗）。"""

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from telegram_monitor import intent, metacog, monitor, referent, selfstate
from telegram_monitor.state import State

NOW = 1_700_000_000


def _ent(charge=0.0, hunger=0.0, mood=0.0):
    return SimpleNamespace(charge=charge, hunger=hunger, mood=mood)


def _st(ent):
    return SimpleNamespace(entropy=ent, self_model=None, workspace=None)


class IntrospectTest(unittest.TestCase):
    def test_clear_dominant_is_confident(self):
        s = _st(_ent(hunger=0.85))
        sm = metacog.introspect(s, NOW)
        sm = metacog.introspect(s, NOW + 3)                 # 穩定兩拍
        self.assertEqual(sm["belief"], "hungry")
        self.assertEqual(sm["actual"], "hungry")
        self.assertGreaterEqual(sm["confidence"], 0.7)      # 清楚又穩 → 有把握
        self.assertEqual(metacog.confidence_clause(sm), "")  # 高信心 → 不硬裝糊塗

    def test_belief_lags_then_corrects_on_transition(self):
        s = _st(_ent(hunger=0.85))
        metacog.introspect(s, NOW)
        metacog.introspect(s, NOW + 3)                      # belief=hungry, 穩
        s.entropy = _ent(charge=0.85)                       # 狀態驟轉成「被攪動」
        sm = metacog.introspect(s, NOW + 6)                 # 第一拍：belief 還沒跟上（滯後）
        self.assertEqual(sm["belief"], "hungry")            # 仍以為自己餓＝暫時認錯自己
        self.assertEqual(sm["actual"], "stirred")
        self.assertLess(sm["confidence"], 0.6)              # 信念沒跟上 → 信心掉
        sm = metacog.introspect(s, NOW + 9)                 # 第二拍：actual 穩定夠久 → 觸發自我修正
        self.assertEqual(sm["belief"], "stirred")
        self.assertIsNotNone(sm["mismatch"])
        self.assertEqual(sm["mismatch"]["from"], "hungry")
        self.assertEqual(sm["mismatch"]["to"], "stirred")
        self.assertEqual(sm["misses"], 1)                   # 記進校準：認錯了一次

    def test_confidence_clause_grounded(self):
        # 轉換期（剛修正）→ 據實說「本來以為X、其實是Y」；不是隨機裝糊塗
        s = _st(_ent(hunger=0.85))
        metacog.introspect(s, NOW); metacog.introspect(s, NOW + 3)
        s.entropy = _ent(charge=0.85)
        metacog.introspect(s, NOW + 6)
        sm = metacog.introspect(s, NOW + 9)
        clause = metacog.confidence_clause(sm)
        self.assertIn("本來以為", clause)
        self.assertIn("其實", clause)

    def test_metacog_facts_admit_fallibility(self):
        s = _st(_ent(hunger=0.5, charge=0.45))              # 兩者接近 → 曖昧、低信心
        metacog.introspect(s, NOW)
        f = metacog.metacog_facts(s)
        self.assertIn("把握", f)
        self.assertIn("認錯", f)                            # 承認會看走眼＝真後設認知，不裝全懂


class RouteTest(unittest.TestCase):
    def test_detector_and_route(self):
        for q in ["你確定自己的感覺嗎", "你會不會搞錯自己", "你真的知道自己怎麼了嗎", "你多了解自己", "你拿得準嗎"]:
            self.assertTrue(selfstate.is_metacog_question(q), q)
        def k(t):
            return intent.resolve(t, referent.Referent()).kind
        self.assertEqual(k("你會不會搞錯自己"), "self_metacog")
        self.assertEqual(k("你確定自己的感覺嗎"), "self_metacog")
        self.assertEqual(k("你現在怎樣"), "self_state")     # 對照：泛泛現況仍是身體狀態

    def test_plain_sure_not_hijacked(self):
        # 不綁自我指涉的泛泛「你確定嗎」不該被當成後設認知（避免劫持一般確認）
        self.assertFalse(selfstate.is_metacog_question("你確定嗎"))


class ProactiveCorrectTest(unittest.TestCase):
    def _state_after_mismatch(self, told_ago):
        s = SimpleNamespace(self_model={"mismatch": {"from": "hungry", "to": "stirred"}},
                            bodystate_last_ts=NOW - told_ago, selfstate_open_ts=0, last_metacog_ts=0,
                            convo_history=[])
        return s

    def test_corrects_only_when_recently_reported(self):
        sent = []
        client = SimpleNamespace(dry_run=True, send=lambda t: sent.append(t) or True)
        cfg = SimpleNamespace(dry_run=True)
        now = SimpleNamespace(timestamp=lambda: float(NOW))
        # 剛（30秒前）報過自己 → 認錯後主動更正
        s = self._state_after_mismatch(30)
        with mock.patch("telegram_monitor.monitor._remember"):
            monitor._metacog_correct(client, s, cfg, now)
        self.assertIn("其實比較像", "".join(sent))           # 分串送出 → 看合併內容
        self.assertEqual(s.last_metacog_ts, NOW)
        # 很久沒報過自己（半小時前）→ 不主動更正（沒上下文、像自言自語）
        sent.clear()
        s2 = self._state_after_mismatch(1800)
        monitor._metacog_correct(client, s2, cfg, now)
        self.assertEqual(sent, [])

    def test_no_correct_without_mismatch(self):
        sent = []
        client = SimpleNamespace(dry_run=True, send=lambda t: sent.append(t) or True)
        s = SimpleNamespace(self_model={"mismatch": None}, bodystate_last_ts=NOW - 10,
                            selfstate_open_ts=0, last_metacog_ts=0, convo_history=[])
        monitor._metacog_correct(client, s, SimpleNamespace(dry_run=True), SimpleNamespace(timestamp=lambda: float(NOW)))
        self.assertEqual(sent, [])


class HandleTest(unittest.TestCase):
    def test_metacog_question_reports_self_model(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        s.self_model = {"belief": "hungry", "actual": "hungry", "runner": "low", "confidence": 0.35,
                        "mismatch": None, "last_correction": {"from": "low", "to": "hungry"}, "checks": 8, "misses": 2}
        cap = {}

        def fake_reply(q, brief, hist, mood_hint="", now_ts=None, self_presence=False):
            cap["brief"] = brief
            return "老實說我拿不太準，悶跟餓有點分不開，我有時也會認錯自己。"

        coach = SimpleNamespace(enabled=True, meter=SimpleNamespace(record=lambda *a, **k: None), reply=fake_reply)
        client = SimpleNamespace(sent=[], dry_run=False, send=lambda t: client.sent.append(t) or True)
        up = {"message": {"chat": {"id": 1}, "text": "你真的知道自己的感覺嗎？會不會搞錯自己", "date": NOW}}
        monitor.handle_message(up, coach, None, {"meta": {}, "records": []}, None,
                               s, client, SimpleNamespace(dry_run=False, telegram_chat_id="", mood_gain=1.0), None)
        self.assertIn("後設認知的事實", cap.get("brief", ""))
        self.assertIn("認錯", cap.get("brief", ""))         # 承認會看走眼
        self.assertIn("認錯自己", "".join(client.sent))


class NotTheatricalTest(unittest.TestCase):
    """🪞 自我修正要安靜誠實、不演（不當『常設刻意轉折的恍神』台詞）：保留『其實…比較像』核心，
    但拿掉『等一下！／恍神／慢半拍才回神／再往裡面翻』與 😂 那種戲劇性轉折。"""

    _HAM = ("等一下", "慢半拍", "回神", "😂", "再往裡面翻", "還沒完全反應過來")

    def test_correction_text_low_key(self):
        t = metacog.correction_text({"from": "hungry", "to": "stirred"})
        self.assertIn("其實", t)                              # 誠實核心保留
        for ham in self._HAM:
            self.assertNotIn(ham, t, ham)

    def test_confidence_clause_low_key(self):
        for sm in ({"mismatch": {"from": "hungry", "to": "stirred"}, "belief": "stirred", "actual": "stirred"},
                   {"belief": "hungry", "actual": "stirred", "confidence": 0.3}):
            c = metacog.confidence_clause(sm)
            for ham in self._HAM:
                self.assertNotIn(ham, c, ham)


if __name__ == "__main__":
    unittest.main()
