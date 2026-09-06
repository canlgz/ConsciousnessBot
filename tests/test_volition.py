"""🎯 內發的目標與能動性：bot 自己從真實資料立一個『想搞懂你某條線』的意圖（含計畫、跨重生持久）、
你聊到時推進（心情微暖）、聊透/成形達成、太久沒推進放掉；伸手時以追意圖為由開口；可被問「你在追什麼」。"""

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from telegram_monitor import intent, monitor, referent, selfstate, volition
from telegram_monitor.state import State

NOW = 1_700_000_000.0


def _data(topic="形成性評量", returns=5, status="context", journeys=None):
    return {"contexts": [{"id": "c1", "status": status, "label": topic,
                          "recordIds": ["r1", "r2", "r3"], "criteria": {"returnVisits": returns}}],
            "journeys": journeys or [], "records": []}


class FormTest(unittest.TestCase):
    def _state(self):
        return SimpleNamespace(goals=[], last_goal_form_ts=0, entropy=SimpleNamespace(hunger=0.8, mood=0.0))

    def test_forms_goal_from_engaged_unformed_topic(self):
        s = self._state()
        g = volition.form_goal(s, _data("形成性評量"), NOW)
        self.assertIsNotNone(g)
        self.assertEqual(g["subject"], "形成性評量")
        self.assertIn("搞懂", g["desire"])
        self.assertTrue(g["plan"])                          # 有計畫（打算怎麼追）
        self.assertEqual(g["status"], "active")

    def test_form_cooldown_and_cap(self):
        s = self._state()
        volition.form_goal(s, _data("A"), NOW)
        self.assertFalse(volition.can_form(s, NOW + 60))     # 剛立過 → 冷卻內不再立
        self.assertTrue(volition.can_form(s, NOW + volition.FORM_COOLDOWN_S + 1))
        # 補到上限 → 不再立
        s.goals.append({"subject": "B", "status": "active"})
        self.assertFalse(volition.can_form(s, NOW + volition.FORM_COOLDOWN_S + 1))

    def test_no_topic_no_goal(self):
        s = self._state()
        self.assertIsNone(volition.form_goal(s, {"contexts": [], "journeys": []}, NOW))   # 沒有在忙的線 → 立不出


class PursueTest(unittest.TestCase):
    def _state_with_goal(self):
        s = SimpleNamespace(goals=[], last_goal_form_ts=0, entropy=SimpleNamespace(hunger=0.8, mood=0.0))
        volition.form_goal(s, _data("形成性評量"), NOW)
        return s

    def test_engagement_advances_and_warms_mood(self):
        s = self._state_with_goal()
        ev = volition.note_engagement(s, "我今天又在想形成性評量怎麼設計", _data(), NOW + 100)
        self.assertEqual(ev["kind"], "advanced")
        self.assertGreater(s.goals[0]["progress"], 0)
        self.assertGreater(s.entropy.mood, 0)               # 追到一點 → 心情微暖（滿足）

    def test_fulfilled_when_topic_becomes_journey(self):
        s = self._state_with_goal()
        jdata = _data("形成性評量", journeys=[{"status": "journey", "title": "形成性評量"}])
        ev = volition.note_engagement(s, "形成性評量我整理成一條歷程了", jdata, NOW + 100)
        self.assertEqual(ev["kind"], "fulfilled")
        self.assertEqual(s.goals[0]["status"], "fulfilled")  # 那條線成形了 → 我「想搞懂它」達成

    def test_no_engagement_when_topic_absent(self):
        s = self._state_with_goal()
        self.assertIsNone(volition.note_engagement(s, "今天天氣真好", _data(), NOW + 100))

    def test_cull_abandons_stale(self):
        s = self._state_with_goal()
        s.goals[0]["last_advance_ts"] = NOW
        self.assertEqual(volition.cull(s, NOW + volition.STALE_S + 1), 1)   # 太久沒推進 → 放掉
        self.assertEqual(s.goals[0]["status"], "abandoned")

    def test_reach_out_is_goal_directed(self):
        s = self._state_with_goal()
        line = volition.reach_out_line(volition.active(s)[0])
        self.assertIn("形成性評量", line)
        self.assertIn("想", line)                            # 「我一直想弄懂…」＝追意圖、非隨機繞舊線


class RouteTest(unittest.TestCase):
    def test_detector_and_route(self):
        for q in ["你有什麼目標", "你在追什麼", "你想搞懂什麼", "你自己想做什麼", "你的意圖是什麼"]:
            self.assertTrue(selfstate.is_goals_question(q), q)
        def k(t):
            return intent.resolve(t, referent.Referent()).kind
        self.assertEqual(k("你有什麼目標"), "self_goals")
        self.assertEqual(k("你在追什麼"), "self_goals")
        self.assertEqual(k("你會想要有自己的感覺嗎"), "self_reflect")   # 對照：抽象欲求仍是 reflect，不被搶


class HandleStepPersistTest(unittest.TestCase):
    def test_step_forms_and_persists_across_reload(self):
        path = os.path.join(tempfile.mkdtemp(), "s.json")
        s = State(path)
        s.owner_folder_id = "F"
        s.entropy = SimpleNamespace(hunger=0.8, mood=0.0)
        cycle = {"now": SimpleNamespace(timestamp=lambda: NOW), "data": _data("惠中寺")}
        monitor._volition_step(s, SimpleNamespace(volition_enabled=True, dry_run=False), cycle)
        self.assertTrue(any(g["subject"] == "惠中寺" for g in s.goals))   # 自己立了意圖
        s.save()
        s2 = State.load(path)
        self.assertTrue(any(g["subject"] == "惠中寺" for g in s2.goals))  # 跨重生持久＝跨天的意圖

    def test_goals_question_reports_intentions(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        s.goals = [{"id": "understand:惠中寺", "kind": "understand_topic", "subject": "惠中寺",
                    "desire": "我想搞懂你為什麼一直繞回「惠中寺」", "plan": ["多問一句它對你的意義"],
                    "status": "active", "progress": 0.34, "touches": 1, "born_ts": NOW, "last_advance_ts": NOW}]
        cap = {}

        def fake_reply(q, brief, hist, mood_hint="", now_ts=None, self_presence=False):
            cap["brief"] = brief
            return "我自己一直想搞懂你惠中寺那條，這是我想弄明白的，不是你叫我做的。"

        coach = SimpleNamespace(enabled=True, meter=SimpleNamespace(record=lambda *a, **k: None), reply=fake_reply)
        client = SimpleNamespace(sent=[], dry_run=False, send=lambda t: client.sent.append(t) or True)
        up = {"message": {"chat": {"id": 1}, "text": "你自己有什麼目標？在追什麼嗎", "date": NOW}}
        monitor.handle_message(up, coach, None, {"meta": {}, "records": [], "contexts": [], "journeys": []}, None,
                               s, client, SimpleNamespace(dry_run=False, telegram_chat_id="", mood_gain=1.0), None)
        self.assertIn("內發意圖的事實", cap.get("brief", ""))
        self.assertIn("惠中寺", cap.get("brief", ""))
        self.assertIn("不是你叫我做的", "".join(client.sent))


if __name__ == "__main__":
    unittest.main()
