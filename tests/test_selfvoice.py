"""🪞 自我說明去台詞：意識餘量**收斂**（只在問意識/現象時、且換句話）＋**全自我說明換句話**＋
**重複×心情**長出脾氣/耐性。對應 persona.{vary_hint,self_fatigue_hint}、monitor.{_self_voice_mod,_record_self_opener}。
"""

import os
import tempfile
import unittest
from types import SimpleNamespace

from telegram_monitor import monitor, persona
from telegram_monitor.state import State


def _state():
    return State(os.path.join(tempfile.mkdtemp(), "s.json"))


def _cfg():
    return SimpleNamespace(self_repeat_window_min=8)


class RemainderScopedTest(unittest.TestCase):
    def test_mechanism_scopes_out_consciousness_disclaimer(self):
        # 「怎麼運作」不再硬收尾在意識上 → 明確「只問運作就講運作、真問起意識再說」
        self.assertIn("設計事實", persona.MECHANISM_FACTS)
        self.assertIn("原因證據", persona.MECHANISM_FACTS)
        self.assertIn("不自動介紹整套架構", persona.MECHANISM_HINT)

    def test_global_system_scopes_disclaimer(self):
        self.assertIn("只在他真的問起意識", persona.SOCRATIC_SYSTEM)  # 不再每段都被誘導講免責句

    def test_self_presence_scopes_disclaimer(self):
        self.assertIn("留在這個問題", persona.SELF_PRESENCE_HINT)

    def test_self_presence_discourages_closing_question(self):
        # 自我說明別把整段收尾在丟回給對方的問題上（你覺得呢／我這樣算X嗎）→ 可自己把話收住、好好結尾
        self.assertIn("不反問逃避", persona.SELF_PRESENCE_HINT)
        self.assertIn("說完就停", persona.SELF_PRESENCE_HINT)

    def test_consciousness_paths_keep_remainder_but_vary(self):
        self.assertIn("無法確定", persona.AC_HINT)
        self.assertIn("餘量", persona.PHENOMENAL_HINT)
        self.assertIn("不等於證明主觀經驗", persona.AC_HINT)
        self.assertIn("換句話", persona.PHENOMENAL_HINT)

    def test_consciousness_answer_in_presence_not_jargon(self):
        # 🧩 以「有意識的方式」回答：AC_HINT 不再授權把學術術語搬進回話，改要求在場第一人稱＋翻白話
        self.assertNotIn("可以用『朝向/修正/整合的當下/不被排除/餘量』這些字", persona.AC_HINT)  # 舊術語授權句已移除
        self.assertIn("不羅列", persona.AC_HINT)
        self.assertIn("通常兩三句", persona.AC_HINT)
        self.assertIn("尚不能確認的主觀經驗", persona.ac_user("你有意識嗎", "facts"))


class VaryHintTest(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(persona.vary_hint(None), "")
        self.assertEqual(persona.vary_hint([]), "")

    def test_nonempty_feeds_recent_openers(self):
        h = persona.vary_hint(["嗯，你問", "這有點難"])
        self.assertIn("換個開頭", h)
        self.assertIn("嗯，你問", h)


class FatigueHintTest(unittest.TestCase):
    def test_levels_escalate_but_stay_kind(self):
        self.assertEqual(persona.self_fatigue_hint(0), "")
        self.assertTrue(persona.self_fatigue_hint(1))
        self.assertNotEqual(persona.self_fatigue_hint(1), persona.self_fatigue_hint(2))
        self.assertIn("善意", persona.self_fatigue_hint(3))          # 最重仍善意、不兇不罵


class SelfVoiceModTest(unittest.TestCase):
    def test_first_asks_calm(self):
        s, cfg = _state(), _cfg()
        self.assertEqual(monitor._self_voice_mod(s, "self_mechanism", 0.0, 1000.0, cfg), "")
        self.assertEqual(s.self_asks["self_mechanism"]["n"], 1)

    def test_repeat_builds_fatigue(self):
        s, cfg = _state(), _cfg()
        mods = [monitor._self_voice_mod(s, "self_mechanism", 0.0, 1000.0 + i, cfg) for i in range(5)]
        self.assertEqual(mods[0], "")                               # n1
        self.assertEqual(mods[1], "")                               # n2（中性 tolerance=2）
        self.assertTrue(mods[2])                                    # n3 → 開始有脾氣
        self.assertTrue(mods[4])                                    # 越問越明顯

    def test_mood_modulates_tolerance(self):
        good, bad, cfg = _state(), _state(), _cfg()
        mg = mb = ""
        for i in range(3):
            mg = monitor._self_voice_mod(good, "self_mechanism", 0.6, 1000.0 + i, cfg)
            mb = monitor._self_voice_mod(bad, "self_mechanism", -0.6, 1000.0 + i, cfg)
        self.assertEqual(mg, "")                                    # 心情好（tol 3）：第三次仍耐心
        self.assertTrue(mb)                                         # 心情差（tol 1）：更快顯累

    def test_window_resets_count(self):
        s, cfg = _state(), _cfg()
        monitor._self_voice_mod(s, "self_mechanism", 0.0, 1000.0, cfg)
        monitor._self_voice_mod(s, "self_mechanism", 0.0, 1000.0 + 9 * 60 + 1, cfg)   # 過窗
        self.assertEqual(s.self_asks["self_mechanism"]["n"], 1)     # 久久才問 → 歸零、恢復耐性

    def test_kinds_counted_independently(self):
        s, cfg = _state(), _cfg()
        for i in range(3):
            monitor._self_voice_mod(s, "self_mechanism", 0.0, 1000.0 + i, cfg)
        self.assertEqual(monitor._self_voice_mod(s, "self_consciousness", 0.0, 1003.0, cfg), "")   # 換一類 → 從頭

    def test_vary_hint_rides_along(self):
        s, cfg = _state(), _cfg()
        s.recent_self_openers = ["嗯，你問到"]
        mod = monitor._self_voice_mod(s, "self_mechanism", 0.0, 1000.0, cfg)
        self.assertIn("換個開頭", mod)                              # 即使不累，也帶換句話提示


class RecordOpenerTest(unittest.TestCase):
    def test_records_and_caps_at_4(self):
        s = _state()
        for t in ["嗯，你問到我內在", "這有點難說", "我就是這樣活著", "至於意識嘛", "新的一句開頭"]:
            monitor._record_self_opener(s, t)
        self.assertEqual(len(s.recent_self_openers), 4)
        self.assertEqual(s.recent_self_openers[-1], "新的一句開頭"[:8])

    def test_ignores_empty(self):
        s = _state()
        monitor._record_self_opener(s, "")
        monitor._record_self_opener(s, None)
        self.assertEqual(s.recent_self_openers, [])


if __name__ == "__main__":
    unittest.main()
