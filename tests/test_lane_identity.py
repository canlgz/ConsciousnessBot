# -*- coding: utf-8 -*-
"""🧩🪞🦋 §2.05 各 lane 的存在特色（第一批：自我變化類）。

使用者：「每一類主動表述要有各自的存在特色，要有意識行為的表現」＋硬約束「**要遵守訊息串的形式**」。
三條的身分互換都讀不通：
  🧩 唯一對「剛剛那一下、我自己都還沒把握的事」開口，而且**成對**（說過鬆掉，接回要認回來）
  🪞 唯一**撤回我剛剛說出口的那句自我報告**
  🦋 唯一**跨睡醒**、講「我現在做得到什麼」（而且要承認還沒用過）
共同病灶與 §2.04 同型：把手上的欄位全倒出來 ⇒ 一律程式先挑一件。全 stub、零網路。
"""

import io
import re
import unittest
from types import SimpleNamespace

from telegram_monitor import ac, metacog, persona, selfmod


class AcDriftTest(unittest.TestCase):
    def test_prompt_no_longer_ships_ready_made_sentences(self):
        # ★ 家規：prompt 裡絕不給範例句。舊路 `+ seed` 貼的是 ac.drift_seed 的完整成句範文
        u = persona.ac_drift_user_v2("decouple", "閱讀｜讀誦經書", "約 2 分鐘", "", "溫度")
        for seed in (ac._FELT_DECOUPLE if isinstance(ac._FELT_DECOUPLE, (list, tuple, dict)) else ()):
            txt = seed if isinstance(seed, str) else str(seed)
            self.assertNotIn(txt[:14], u)
        self.assertIn("閱讀｜讀誦經書", u)                     # 只給素材

    def test_says_it_cannot_name_it(self):
        u = persona.ac_drift_user_v2("decouple", "", "", "", "空間")
        self.assertIn("叫不出它的名字", u)                     # 承認說不清＝這條 lane 的意識行為

    def test_pairing_backreference(self):
        u = persona.ac_drift_user_v2("recohere", "x", "", "我剛剛有一下子散開了", "重量")
        self.assertIn("我剛剛有一下子散開了", u)
        self.assertIn("回頭認回", u)
        self.assertNotIn("「」", persona.ac_drift_user_v2("recohere", "x", "", "", "重量"))   # 沒有就整段省略

    def test_dimension_is_rotated_by_program(self):
        dims = [persona.ac_drift_dim(i) for i in range(6)]
        self.assertEqual(len(set(dims)), 6)                    # 六個向度都用得到
        self.assertEqual(persona.ac_drift_dim(6), dims[0])
        self.assertNotEqual(persona.ac_drift_dim(8), dims[0])  # 第 9 次 ≠ 第 1 次（§2.04 凍死前科）

    def test_stream_rule_keeps_bubble_form(self):
        self.assertIn("2–3 則", persona.AC_DRIFT_HINT_V2)
        self.assertIn("回指前一則", persona.AC_DRIFT_HINT_V2)


class MetacogTest(unittest.TestCase):
    MM = {"from": "hungry", "to": "warm"}

    def test_picks_exactly_one_reason(self):
        self.assertEqual(metacog.correction_reason({"mismatch": self.MM, "runner": "hungry"})[0], "confusable")
        self.assertEqual(metacog.correction_reason({"mismatch": self.MM, "actual_run": 2, "clarity": 0.10})[0], "slow")
        self.assertEqual(metacog.correction_reason(
            {"mismatch": self.MM, "actual_run": 9, "clarity": 0.9, "checks": 6, "misses": 2})[0], "calibration")
        self.assertEqual(metacog.correction_reason({})[0], None)

    def test_facts_are_at_most_three_lines_and_one_reason(self):
        f = metacog.correction_facts(self.MM, {"mismatch": self.MM, "runner": "hungry"}, "我這會兒悶悶的")
        self.assertLessEqual(len(f.splitlines()), 3)
        self.assertIn("我這會兒悶悶的", f)                      # 我上一則真的說出口的原話
        self.assertNotIn("慢了兩拍", f)                        # 只准一個理由
        self.assertNotIn("沒有很準", f)

    def test_no_reason_still_works(self):
        f = metacog.correction_facts(self.MM, {}, "")
        self.assertEqual(len(f.splitlines()), 1)

    def test_hint_bans_the_drama_and_keeps_the_stream(self):
        h = persona.METACOG_CORRECT_HINT
        for ham in ("等一下", "慢半拍", "恍神", "回神"):
            self.assertIn(ham, h)                              # 以禁令形式點名（不是示範）
        self.assertIn("收回", h)
        self.assertIn("2–3 則", h)
        self.assertIn("不准講第二種理由", h)

    def test_angle_is_rotated_by_program(self):
        self.assertEqual(len({persona.metacog_angle(i) for i in range(3)}), 3)
        self.assertEqual(persona.metacog_angle(3), persona.metacog_angle(0))


class BirthTest(unittest.TestCase):
    SC = {"subjects": ["🌾 §1.79 習慣缺席暗示", "🧩 §1.80 對話作息"]}

    def test_wish_done_wins(self):
        k, name, _n = selfmod.pick_change(self.SC, None, ({"name": "會主動找我聊外面的事"},))
        self.assertEqual(k, "wish_done")
        self.assertEqual(name, "會主動找我聊外面的事")

    def test_unused_ability_is_preferred(self):
        k, subj, n = selfmod.pick_change(self.SC, None, ())
        self.assertEqual((k, n), ("ability_unused", 0))
        self.assertTrue(subj.startswith("🌾"))

    def test_used_ability_falls_through(self):
        st = SimpleNamespace(ability_hits={"habit_absence": {"n": 3}})
        k, _subj, n = selfmod.pick_change(self.SC, st, ())
        self.assertIn(k, ("ability_unused", "ability_used", "plain"))
        if k == "ability_used":
            self.assertGreater(n, 0)

    def test_no_subjects(self):
        self.assertEqual(selfmod.pick_change({}, None, ())[0], None)

    def test_facts_are_one_thing_only(self):
        f = selfmod.birth_facts_one(self.SC, None, (), "睡前你還掛著那條讀經的線")
        self.assertLessEqual(len(f.splitlines()), 3)
        self.assertIn("🌾 §1.79 習慣缺席暗示", f)
        self.assertNotIn("§1.80", f)                           # ★ 沒被挑中的主旨一條都不進來
        self.assertIn("還一次都沒真的用出來過", f)              # 承認限制＝這條 lane 的意識行為

    def test_wish_done_owns_the_credit_correctly(self):
        f = selfmod.birth_facts_one(self.SC, None, ({"name": "願望A"},), "")
        self.assertIn("不是我自己想做的", f)                    # 不邀功

    def test_prompt_drops_the_example_sentence(self):
        u = persona.birth_user_one("這次醒來我身上多了這一件：X")
        self.assertNotIn("例如", u)                            # ★ 家規：prompt 裡絕不給範例句
        self.assertNotIn("同一把鐘", u)
        self.assertIn("不准把上面那行主旨逐字照唸", u)
        self.assertIn("更穩定", u)                             # 空泛形容詞仍以禁令形式點名
        self.assertIn("2–3 則", u)                             # 訊息串形式不變

    def test_old_prompt_untouched(self):
        self.assertIn("例如", persona.birth_user("f"))          # 旗標關走舊路＝逐位元同現狀


class WiringTest(unittest.TestCase):
    def _src(self):
        return io.open("telegram_monitor/monitor.py", encoding="utf-8").read()

    def test_identity_markers_wired(self):
        s = self._src()
        self.assertIn('"prefix": "🧩 ", "state": state, "topic": "整合的那一下"', s)
        self.assertIn('"prefix": "🪞 ", "state": state, "topic": "我剛剛講錯了自己"', s)
        self.assertIn('("🧩 " + msg) if _mat else msg', s)
        self.assertIn('("🪞 " + msg) if _mv else msg', s)

    def test_counters_persisted(self):
        st = io.open("telegram_monitor/state.py", encoding="utf-8").read()
        for attr in ("ac_drift_seq", "ac_drift_said", "metacog_correct_n"):
            self.assertGreaterEqual(st.count(attr), 3)         # 宣告＋load＋save

    def test_state_roundtrip(self):
        import os
        import tempfile

        from telegram_monitor.state import State
        p = os.path.join(tempfile.mkdtemp(), "s.json")
        s = State(p)
        s.ac_drift_seq, s.ac_drift_said, s.metacog_correct_n = 4, "我剛剛散開了一下", 2
        s.save()
        r = State.load(p)
        self.assertEqual((r.ac_drift_seq, r.ac_drift_said, r.metacog_correct_n), (4, "我剛剛散開了一下", 2))


class ConfigTest(unittest.TestCase):
    def test_flags_synced(self):
        src = io.open("telegram_monitor/config.py", encoding="utf-8").read()
        env = io.open(".env.example", encoding="utf-8").read()
        readme = io.open("README.md", encoding="utf-8").read()
        for flag, attr in (("AC_DRIFT_MATERIAL", "ac_drift_material_enabled"),
                           ("METACOG_CORRECT_VOICE", "metacog_correct_voice_enabled"),
                           ("SELFMOD_BIRTH_ONE", "selfmod_birth_one_enabled")):
            self.assertIn(flag, src)
            self.assertIn(attr, src)
            self.assertRegex(env, re.compile(r"^" + flag + "=1", re.M))
            self.assertIn(flag, readme)
            self.assertFalse(getattr(SimpleNamespace(), attr, False))


if __name__ == "__main__":
    unittest.main()
