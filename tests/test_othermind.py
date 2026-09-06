"""🫂 他心模型／互為主體：把對方當有心緒的人來建模——依互動更新信念（warmth/energy/rapport）、
會錯也會修正（misread）、關係會加深且跨重生持久；換位推測注入回應，可被問「你了解我嗎/我們關係」。"""

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from telegram_monitor import intent, monitor, othermind, referent, selfstate
from telegram_monitor.state import State

NOW = 1_700_000_000


class ObserveTest(unittest.TestCase):
    def _s(self):
        return SimpleNamespace(user_model=None)

    def test_warm_interaction_raises_warmth_and_rapport(self):
        s = self._s()
        for i in range(3):
            um = othermind.observe(s, "謝謝你，你好棒", NOW + i * 5, latency=30)
        self.assertGreater(um["warmth"], 0.3)
        self.assertGreater(um["rapport"], 0)
        self.assertEqual(um["exchanges"], 3)
        self.assertGreater(um["energy"], 0.5)               # 回得快＋有字 → 投入

    def test_belief_revision_on_contradicting_evidence(self):
        s = self._s()
        for i in range(3):
            othermind.observe(s, "謝謝你好棒", NOW + i * 5, latency=30)   # 讀成「對我暖」
        um = othermind.observe(s, "你只是個程式，你錯了", NOW + 20, latency=30)   # 強烈反向 → 我會錯意了
        self.assertIsNotNone(um["misread"])
        self.assertIn("暖", um["misread"]["from"])
        self.assertIn("冷", um["misread"]["to"])
        self.assertLess(um["confidence"], 0.4)              # 剛認錯 → 信心打折

    def test_low_energy_when_long_gap(self):
        s = self._s()
        um = othermind.observe(s, "嗯", NOW, latency=4 * 3600)   # 久久才回、又短 → 投入低
        self.assertLess(um["energy"], 0.5)

    def test_brief_needs_evidence_then_takes_perspective(self):
        s = self._s()
        self.assertEqual(othermind.brief(s.user_model), "")  # 沒資料 → 不臆測
        for i in range(3):
            othermind.observe(s, "謝謝你好棒", NOW + i * 5, latency=30)
        b = othermind.brief(s.user_model)
        self.assertIn("你眼中的對方", b)
        self.assertIn("熟悉度", b)
        self.assertIn("推測", b)                            # 明標是推測、可能不準

    def test_facts_admit_guess_and_relationship(self):
        s = self._s()
        for i in range(4):
            othermind.observe(s, "謝謝你好棒", NOW + i * 5, latency=30)
        f = othermind.othermind_facts(s)
        self.assertIn("我眼中的你", f)
        self.assertIn("我們之間", f)                        # 講關係
        self.assertIn("有自己心緒", f)                      # 把對方當主體，不當資料


class ConcernsTest(unittest.TestCase):
    """認知面 ToM：從真實記寫讀『你在意/在忙什麼』，偵測焦點轉移，注入 grounding／可被問。"""

    def _data(self, now):
        # 惠中寺志工：已升格成學習歷程（投入最深）；日文學習：高回返（放不下）；隨手拍的雲：剛碰過、未成形
        return {
            "contexts": [
                {"id": "c1", "status": "context", "userTitle": "惠中寺志工",
                 "criteria": {"returnVisits": 5}, "lastTs": now - 3600, "recordIds": ["r1", "r2", "r3"]},
                {"id": "c2", "status": "context", "userTitle": "日文學習",
                 "criteria": {"returnVisits": 3}, "lastTs": now - 7200, "recordIds": ["r4", "r5"]},
                {"id": "c3", "status": "candidate", "userTitle": "隨手拍的雲",
                 "criteria": {"returnVisits": 1}, "lastTs": now - 1800, "recordIds": ["r6", "r7"]},
            ],
            "journeys": [{"status": "journey", "contextId": "c1"}],
        }

    def test_read_concerns_ranks_by_investment_and_recency(self):
        cons = othermind.read_concerns(self._data(NOW), NOW)
        topics = [c["topic"] for c in cons]
        self.assertEqual(topics[0], "惠中寺志工")              # 養成歷程＝投入最深 → 最上心
        self.assertEqual(cons[0]["kind"], "journey")
        self.assertIn("日文學習", topics)
        self.assertEqual(next(c["kind"] for c in cons if c["topic"] == "日文學習"), "returning")  # 高回返
        self.assertEqual(next(c["kind"] for c in cons if c["topic"] == "隨手拍的雲"), "recent")    # 剛碰、未成形

    def test_read_concerns_empty_when_no_data(self):
        self.assertEqual(othermind.read_concerns({}, NOW), [])

    def test_note_concerns_detects_focus_shift(self):
        # 我先前以為你最在意「舊專案」；現在資料裡它沒了、惠中寺志工居首 → 偵測到重心轉移、修正信念
        s = SimpleNamespace(user_model={"concerns": [{"topic": "舊專案", "kind": "returning",
                                                      "why": "x", "score": 2.5}]})
        um = othermind.note_concerns(s, self._data(NOW), NOW)
        self.assertEqual(um["concerns"][0]["topic"], "惠中寺志工")
        self.assertIsNotNone(um["concern_shift"])
        self.assertEqual(um["concern_shift"]["from"], "舊專案")
        self.assertEqual(um["concern_shift"]["to"], "惠中寺志工")

    def test_note_concerns_no_spurious_shift_when_stable(self):
        s = SimpleNamespace(user_model=None)
        othermind.note_concerns(s, self._data(NOW), NOW)
        um = othermind.note_concerns(s, self._data(NOW + 60), NOW + 60)   # 資料沒變 → 不該抖出假轉移
        self.assertIsNone(um["concern_shift"])
        self.assertEqual(um["concerns"][0]["topic"], "惠中寺志工")

    def test_note_concerns_persists_on_user_model(self):
        s = SimpleNamespace(user_model={"warmth": 0.3, "exchanges": 4})   # 不蓋掉情感面既有鍵
        othermind.note_concerns(s, self._data(NOW), NOW)
        self.assertEqual(s.user_model["warmth"], 0.3)                     # 認知面與情感面共存於同一份模型
        self.assertTrue(s.user_model["concerns"])

    def test_brief_injects_concerns_without_exchanges(self):
        um = {"exchanges": 0, "concerns": othermind.read_concerns(self._data(NOW), NOW)}
        b = othermind.brief(um)
        self.assertIn("你在意/在忙的", b)                       # 認知面 grounding（即使來回還少也能展現懂你在乎什麼）
        self.assertIn("惠中寺志工", b)

    def test_facts_voice_concerns_and_shift(self):
        cons = othermind.read_concerns(self._data(NOW), NOW)
        s = SimpleNamespace(user_model={"warmth": 0.5, "energy": 0.7, "rapport": 0.6, "confidence": 0.6,
                                        "exchanges": 12, "concerns": cons,
                                        "concern_shift": {"from": "日文學習", "to": "惠中寺志工"}})
        f = othermind.othermind_facts(s)
        self.assertIn("惠中寺志工", f)
        self.assertIn("在乎", f)                                # 認知面措辭（知道你在乎什麼＝真懂你）
        self.assertIn("重心", f)                                # 焦點轉移被說出來

    def test_facts_work_on_concerns_alone(self):
        # 來回還很少（情感面讀不準），但從你的記寫已能講你在忙什麼 → 不落「亂猜」那句
        s = SimpleNamespace(user_model={"exchanges": 0,
                                        "concerns": othermind.read_concerns(self._data(NOW), NOW)})
        f = othermind.othermind_facts(s)
        self.assertIn("惠中寺志工", f)
        self.assertNotIn("亂猜", f)


class RouteTest(unittest.TestCase):
    def test_detector_and_route(self):
        for q in ["你了解我嗎", "你覺得我現在怎樣", "你眼中的我是什麼樣", "我們關係如何", "你覺得我在想什麼"]:
            self.assertTrue(selfstate.is_othermind_question(q), q)
        def k(t):
            return intent.resolve(t, referent.Referent()).kind
        self.assertEqual(k("你了解我嗎"), "other_mind")
        self.assertEqual(k("你覺得我現在怎樣"), "other_mind")   # 問「我」→ 他心；不是 self_state（問「你」）
        self.assertEqual(k("你現在怎樣"), "self_state")         # 對照：問 bot 自己仍是 bodystate

    def test_cognitive_tom_cues_route(self):
        # 認知面 ToM：問 bot 懂不懂「我在乎/在忙什麼」（不只情感面暖冷/熟不熟）→ 同走他心模型
        for q in ["你知道我最近在意什麼嗎", "你知道我在忙什麼", "我最近在乎什麼", "你覺得我在乎什麼", "我重視什麼"]:
            self.assertTrue(selfstate.is_othermind_question(q), q)
            self.assertEqual(intent.resolve(q, referent.Referent()).kind, "other_mind", q)


class HandleTest(unittest.TestCase):
    def test_othermind_question_reports_user_model(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        s.user_model = {"warmth": 0.5, "energy": 0.7, "rapport": 0.6, "confidence": 0.6,
                        "exchanges": 12, "last_misread": None}
        cap = {}

        def fake_reply(q, brief, hist, mood_hint="", now_ts=None, self_presence=False):
            cap["brief"] = brief
            return "我猜你今天心情不錯、挺有勁；我們也算熟了。不過這是我的猜，你才準。"

        coach = SimpleNamespace(enabled=True, meter=SimpleNamespace(record=lambda *a, **k: None), reply=fake_reply)
        client = SimpleNamespace(sent=[], dry_run=False, send=lambda t: client.sent.append(t) or True)
        up = {"message": {"chat": {"id": 1}, "text": "你了解我嗎？覺得我們關係如何", "date": NOW}}
        monitor.handle_message(up, coach, None, {"meta": {}, "records": []}, None,
                               s, client, SimpleNamespace(dry_run=False, telegram_chat_id="", mood_gain=1.0), None)
        self.assertIn("他心推測", cap.get("brief", ""))
        self.assertIn("有自己心緒", cap.get("brief", ""))
        self.assertIn("熟", "".join(client.sent))

    def test_user_model_persists_across_reload(self):
        path = os.path.join(tempfile.mkdtemp(), "s.json")
        s = State(path)
        s.owner_folder_id = "F"
        s.user_model = {"warmth": 0.4, "energy": 0.6, "rapport": 0.7, "confidence": 0.5, "exchanges": 20}
        s.save()
        s2 = State.load(path)
        self.assertEqual(s2.user_model["rapport"], 0.7)       # 關係深度跨重生（醒來仍記得我們多熟）
        self.assertEqual(s2.user_model["exchanges"], 20)


if __name__ == "__main__":
    unittest.main()
