# -*- coding: utf-8 -*-
"""🌬️🌊🌾🫧 §2.06 各 lane 的存在特色（第二批：朝向他人類，6 條）。

身分互換讀不通：
  🌬️ 唯一**處置我自己剛說出口的那一句**（把我造成的壓力收回來）
  🌊 唯一**主動結束**，而且是**我先鬆手**的
  🌾 日課＝**我對你一條線的預期落空**／🌾 出現＝**你這個人還沒出現**，且**收件人不在場**（留言不是交談）
  🫧 有意圖＝**我私下在追的好奇有動靜**／🫧 無意圖＝**我這邊空了**，內容本身就是「我沒有東西可講」
全 stub、零網路。
"""

import io
import re
import unittest
from types import SimpleNamespace

from telegram_monitor import persona, selfstate, volition

NOW = 1.0e9


class SootheTest(unittest.TestCase):
    def test_flag_off_is_verbatim_old(self):
        self.assertIn("溫、短、就 1 句", persona.soothe_user())      # 零參數＝逐字舊版

    def test_names_its_own_question(self):
        u = persona.soothe_user("你會怎麼形容那種安靜", "約 20 分鐘", True)
        self.assertIn("你會怎麼形容那種安靜", u)                    # ★ 我知道我剛剛說了什麼
        self.assertIn("約 20 分鐘", u)
        self.assertIn("認回自己身上", u)

    def test_bans_questions_and_canned_lines(self):
        u = persona.soothe_user("x", "", None)
        self.assertIn("不准出現問號", u)                            # 不是嘴上說不用急、實際又問一次
        for c in persona._SOOTHE_BAN:
            self.assertIn(c, u)                                     # 以**禁令**形式點名（不是示範）
        self.assertIn("2–3 則", u)

    def test_stake_changes_the_line(self):
        self.assertIn("真的很想知道", persona.soothe_user("x", "", True))
        self.assertIn("順口", persona.soothe_user("x", "", False))


class CloseRoundTest(unittest.TestCase):
    def test_flag_off_is_verbatim_old(self):
        self.assertIn("就 1 句", persona.close_round_user("natural"))
        self.assertIn("好啦我知道你在逗我", persona.close_round_user("probe_settled"))

    def test_v2_has_no_ready_made_line_outside_the_ban(self):
        v = persona.close_round_user_v2("probe_settled", "his_warm", persona.close_motive(0))
        # 那兩句成品台詞只准出現在「不准用這幾句罐頭」的清單裡，不得作為示範
        i = v.index("不准**用這幾句罐頭") if "不准**用這幾句罐頭" in v else v.index("罐頭")
        self.assertNotIn("我先去忙囉", v[:i])
        self.assertNotIn("有事再喊我", v[:i])
        self.assertIn("我先去忙囉", v[i:])

    def test_says_which_kind_of_ending(self):
        for stance, frag in (("mine_warm", "你這邊其實還熱著"), ("his_warm", "他那邊好像還想聊"),
                             ("both_cool", "兩邊都淡下來")):
            self.assertIn(frag, persona.close_round_user_v2("natural", stance, "x"))

    def test_motive_rotates(self):
        self.assertEqual(len({persona.close_motive(i) for i in range(4)}), 4)
        self.assertEqual(persona.close_motive(4), persona.close_motive(0))

    def test_stream_form_replaces_one_sentence(self):
        v = persona.close_round_user_v2("natural", "both_cool", "x")
        self.assertIn("2–3 則", v)
        self.assertNotIn("就 1 句", v)                              # 1 句裝不下起點/轉折/落點


class AbsenceTest(unittest.TestCase):
    def test_flag_off_is_verbatim_old(self):
        self.assertIn("今天比較忙嗎", persona.appear_absence_rule("07:30"))
        self.assertIn("分鐘", persona.habit_absence_rule("讀經", "07:30", 40))

    def test_appear_v2_drops_the_ready_made_questions(self):
        v = persona.appear_absence_rule_v2(persona.appear_angle(0), "我今天大半都醒著")
        self.assertNotIn("今天比較忙嗎", v)                          # ★ 家規：prompt 裡絕不給範例句
        self.assertNotIn("今天節奏不一樣嗎", v)

    def test_appear_v2_knows_the_reader_is_absent(self):
        v = persona.appear_absence_rule_v2(persona.appear_angle(1), "")
        self.assertIn("不在場", v)
        self.assertIn("不要用時間詞當抬頭", v)                       # 這是留言、不是即時對話
        self.assertIn("明說不必回他", v)

    def test_appear_v2_reports_itself_first(self):
        v = persona.appear_absence_rule_v2(persona.appear_angle(0), "我今天大半都醒著")
        self.assertIn("我今天大半都醒著", v)

    def test_habit_v2_keeps_numbers_out_and_owns_the_limit(self):
        v = persona.habit_absence_rule_v2("讀誦經書", persona.absence_angle(0))
        self.assertIn("讀誦經書", v)
        self.assertNotIn("分鐘", v)                                  # 真數字留在 log，不進 prompt
        self.assertIn("你只看得到他記進來的東西", v)                  # 把不確定講成自己的限制
        self.assertIn("只准一個問句", v)

    def test_angles_rotate(self):
        self.assertEqual(len({persona.absence_angle(i) for i in range(3)}), 3)
        self.assertEqual(len({persona.appear_angle(i) for i in range(3)}), 3)


class ReachOutTest(unittest.TestCase):
    G = {"touches": 2, "last_advance_ts": NOW, "reach_n": 0, "born_ts": NOW}

    def test_picks_exactly_one_reason(self):
        axis, why = volition.reach_out_pick(self.G, 3.0, "今天讀到無住生心", NOW + 60, 0)
        self.assertEqual(axis, "advanced")
        self.assertTrue(why)

    def test_rotates_between_valid_reasons(self):
        seen = {volition.reach_out_pick(self.G, 3.0, "字句", NOW + 60, t)[0] for t in range(4)}
        self.assertGreater(len(seen), 1)                             # 多個成立時會輪替

    def test_fading_never_fights_the_time_guard(self):
        g = dict(self.G, last_advance_ts=NOW - 4 * 86400)
        self.assertNotEqual(volition.reach_out_pick(g, 3.0, "", NOW, 0)[0], "fading")   # 那條線其實很新
        self.assertEqual(volition.reach_out_pick(g, 200.0, "", NOW, 0)[0], "fading")

    def test_nothing_applies_falls_back(self):
        self.assertEqual(volition.reach_out_pick({}, None, "", NOW, 0), (None, ""))

    def test_repeat_becomes_a_behaviour_not_a_line(self):
        r = volition.reach_out_one_rule("讀經", "repeat", "你已經問過 3 次了", 3)
        self.assertIn("不准出現問號", r)                              # ★ 從台詞升級成行為
        self.assertIn("選擇權留給他", r)

    def test_only_one_reason_enters_the_prompt(self):
        r = volition.reach_out_one_rule("讀經", "advanced", "他剛餵了你一口", 0)
        self.assertIn("他剛餵了你一口", r)
        self.assertIn("只講這一個理由", r)
        self.assertIn("2–4 則", r)

    def test_empty_lane_admits_it_has_nothing(self):
        v = selfstate.spontaneous_empty_rule("約 6 小時", 2)
        self.assertIn("約 6 小時", v)
        self.assertIn("老實說你此刻沒有內容", v)                      # 不硬編話題
        self.assertIn("不是他該做什麼", v)
        self.assertNotIn("條線", v.split("【怎麼說】")[0])              # 素材段不得出現任何主題


class WiringTest(unittest.TestCase):
    def _src(self):
        return io.open("telegram_monitor/monitor.py", encoding="utf-8").read()

    def test_identity_markers_and_fallbacks(self):
        s = self._src()
        self.assertIn('"prefix": "🌬️ ", "state": state, "topic": "我剛剛問的那句"', s)
        self.assertIn('"prefix": "🌊 ", "state": state, "topic": "這一輪的收尾"', s)
        self.assertIn("volition.reach_out_pick(", s)
        self.assertIn("selfstate.spontaneous_empty_rule(", s)
        self.assertIn("persona.habit_absence_rule_v2(", s)
        self.assertIn("persona.appear_absence_rule_v2(", s)

    def test_question_marks_are_stripped_deterministically(self):
        from telegram_monitor import monitor
        self.assertEqual(monitor._soothe_nonquestion('啊，我剛剛說「跟週末安排有關嗎？」'), '')
        self.assertEqual(monitor._soothe_nonquestion('你在忙嗎？這題先放著。'), '這題先放著。')

    def test_counters_persisted(self):
        st = io.open("telegram_monitor/state.py", encoding="utf-8").read()
        for attr in ("close_motive_n", "habit_absence_pick_n", "reachout_pick_n"):
            self.assertGreaterEqual(st.count(attr), 3)

    def test_state_roundtrip(self):
        import os
        import tempfile

        from telegram_monitor.state import State
        p = os.path.join(tempfile.mkdtemp(), "s.json")
        s = State(p)
        s.close_motive_n, s.habit_absence_pick_n, s.reachout_pick_n = 3, 5, 7
        s.save()
        r = State.load(p)
        self.assertEqual((r.close_motive_n, r.habit_absence_pick_n, r.reachout_pick_n), (3, 5, 7))


class ConfigTest(unittest.TestCase):
    def test_flags_synced(self):
        src = io.open("telegram_monitor/config.py", encoding="utf-8").read()
        env = io.open(".env.example", encoding="utf-8").read()
        readme = io.open("README.md", encoding="utf-8").read()
        for flag, attr in (("SOOTHE_OWN_QUESTION", "soothe_own_question_enabled"),
                           ("CLOSE_ROUND_STANCE", "close_round_stance_enabled"),
                           ("HABIT_ABSENCE_ONE_THING", "habit_absence_one_thing_enabled"),
                           ("REACHOUT_ONE_THING", "reachout_one_thing_enabled")):
            self.assertIn(flag, src)
            self.assertIn(attr, src)
            self.assertRegex(env, re.compile(r"^" + flag + "=1", re.M))
            self.assertIn(flag, readme)
            self.assertFalse(getattr(SimpleNamespace(), attr, False))


if __name__ == "__main__":
    unittest.main()
