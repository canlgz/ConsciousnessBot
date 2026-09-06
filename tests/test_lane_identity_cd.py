# -*- coding: utf-8 -*-
"""💡🔮🌐🫀🌀🧭🍃 §2.07–§2.08 各 lane 的存在特色（第三、四批）。

身分互換讀不通：💡 不押注不外求／🔮 押一個會被未來裁決的注／🌐 素材不在這台機器裡／
🫀 講**那條線**不是講我／🌀 內在因應是**我正要對自己做一件事**／🧭 不是我想說是他交代過／🍃 我對自己動了手。
本批修掉三個實錯：🔮 認帳拿不到自己說過的話、🧭 prompt 叫它猜原因卻不給因果欄位、🍃 兩個來源被 max() 壓成一個數。
全 stub、零網路。
"""

import io
import re
import unittest
from datetime import datetime
from types import SimpleNamespace

from telegram_monitor import association, circumplex, environ, persona, selfstate


class InsightTest(unittest.TestCase):
    def test_axis_priority(self):
        self.assertEqual(association.insight_focus({"kind": "conflict"}, "reject")["axis"], "recur")
        self.assertEqual(association.insight_focus({"kind": "conflict"})["axis"], "gap")
        self.assertEqual(association.insight_focus({"kind": "leap", "signal": {"stone": {"text": "x"}}})["axis"], "stone")
        self.assertEqual(association.insight_focus({"kind": "blend", "signal": {"novelty": 0.9}, "blend_tau": 0.0})["axis"],
                         "novelty")
        self.assertEqual(association.insight_focus({"kind": "blend", "signal": {"novelty": 0.01}, "blend_tau": 0.0})["axis"],
                         "support")

    def test_weak_is_one_to_one_and_program_chosen(self):
        f = association.insight_focus({"kind": "conflict"})
        self.assertIn("我算出來的", f["weak"])                       # 情緒落差是我算的、不是他說的
        r = association.insight_focus_rule(f)
        self.assertIn(f["weak"], r)
        self.assertIn("只准質疑這一點", r)
        for other in association._INSIGHT_WEAK.values():             # 只准出現被挑中的那一環
            if other != f["weak"]:
                self.assertNotIn(other, r)

    def test_stream_form(self):
        r = association.insight_focus_rule(association.insight_focus({}))
        self.assertIn("2–4 則", r)
        self.assertIn("其他訊號**一律不要提", r)


class ForesightChainTest(unittest.TestCase):
    def test_no_told_text_is_verbatim_old(self):
        self.assertEqual(persona.foresight_settle_rule_v2("b", "q", "hit", "e"),
                         persona.foresight_settle_rule("b", "q", "hit", "e"))

    def test_hit_quotes_itself_and_refuses_credit(self):
        v = persona.foresight_settle_rule_v2("線", "停住那句", "hit", "新的那句", "我猜它三天內會回來")
        self.assertIn("我猜它三天內會回來", v)                       # ★ 我記得我押了什麼
        self.assertIn("第一則就要引出你當初講過的那句", v)
        self.assertIn("不准邀功", v)

    def test_miss_owns_it(self):
        v = persona.foresight_settle_rule_v2("線", "停住那句", "miss", "", "我猜它三天內會回來")
        self.assertIn("認錯要有實質", v)
        self.assertIn("不准", v)

    def test_told_text_is_stored_only_after_speaking(self):
        src = io.open("telegram_monitor/monitor.py", encoding="utf-8").read()
        i = src.index('"told_text":')
        self.assertIn("_ability_fired(state, cfg, \"foresight\"", src[:i])   # 落帳在送出成功之後


class WorldlineChainTest(unittest.TestCase):
    def test_v2_adds_source_awareness_and_movement(self):
        v = persona.worldline_say_rule_v2("教學｜繪本", "他寫的那句", "外面的說法", "站名｜https://x", 0, "我上次說留白像呼吸")
        self.assertIn("只看了這一個來源", v)                          # 承認取樣限制
        self.assertIn("我上次說留白像呼吸", v)
        self.assertIn("動了哪裡", v)

    def test_no_last_own_still_asks_for_movement(self):
        v = persona.worldline_say_rule_v2("教學｜繪本", "q", "f", "s", 0, "")
        self.assertIn("動了哪裡", v)
        self.assertNotIn("你上次講這條線時", v)

    def test_base_rule_untouched(self):
        base = persona.worldline_say_rule("d", "q", "f", "s", 0)
        self.assertIn(base, persona.worldline_say_rule_v2("d", "q", "f", "s", 0, ""))


class MoodWatchTest(unittest.TestCase):
    ST = SimpleNamespace(mood_trace=[{"v": 0.1, "a": 0.1, "ts": 10},
                                     {"v": 0.5, "a": 0.2, "ts": 20, "cause": "剛聊完那條讀經的線"},
                                     {"v": 0.52, "a": 0.21, "ts": 30, "cause": "小變動"}])

    def test_picks_the_biggest_step(self):
        self.assertEqual(circumplex.trace_step(self.ST, 0)["cause"], "剛聊完那條讀經的線")

    def test_none_when_no_cause_or_too_short(self):
        self.assertIsNone(circumplex.trace_step(SimpleNamespace(mood_trace=[{"v": 0, "a": 0, "ts": 1}]), 0))
        self.assertIsNone(circumplex.trace_step(SimpleNamespace(mood_trace=[]), 0))
        self.assertIsNone(circumplex.trace_step(self.ST, 100))       # since_ts 之後沒東西

    def test_admits_not_knowing_instead_of_guessing(self):
        v = persona.mood_watch_rule("v+0.10 a+0.05", "暖", "", "座標有變就跟我說")
        self.assertIn("沒有任何紀錄", v)
        self.assertIn("絕對不要**猜一個原因", v)                      # ★ 舊 prompt 正是叫它猜
        self.assertIn("座標有變就跟我說", v)                          # 逐字引他當初交代的

    def test_uses_the_record_when_there_is_one(self):
        v = persona.mood_watch_rule("g", "暖", "剛聊完那條讀經的線", "")
        self.assertIn("剛聊完那條讀經的線", v)
        self.assertNotIn("絕對不要**猜一個原因", v)

    def test_keeps_the_numbers_rule(self):
        self.assertIn("一字不改地照抄", persona.mood_watch_rule("g", "暖", "", ""))


class EnvironTest(unittest.TestCase):
    def test_reading_exposes_both_components(self):
        r = environ.read_environment(0, 10, datetime(2026, 7, 29, 10, 0))
        self.assertTrue(hasattr(r, "data_act") and hasattr(r, "talk_act"))
        self.assertEqual(r.activity, max(r.data_act, r.talk_act))    # 舊語意不變

    def test_backward_compatible_construction(self):
        r = environ.EnvReading(0.5, "早上", False)                    # 舊三參數建構仍可用（尾欄位有預設）
        self.assertEqual((r.data_act, r.talk_act), (0.0, 0.0))

    def test_quiet_side(self):
        self.assertEqual(environ.quiet_side(environ.EnvReading(0.9, "早上", False, 0.2, 0.9)), "data")
        self.assertEqual(environ.quiet_side(environ.EnvReading(0.9, "早上", False, 0.9, 0.2)), "talk")
        self.assertEqual(environ.quiet_side(environ.EnvReading(0.9, "早上", False, 0.5, 0.5)), "both")

    def test_rule_speaks_only_that_side(self):
        r = environ.shift_rule("data", 1.0, 1.5)
        self.assertIn("沒有新的記寫進來", r)
        self.assertIn("只講那一面", r)
        self.assertIn("不准報任何數字", r)
        self.assertIn("2–3 則", r)


class ReadingOneTest(unittest.TestCase):
    def test_picks_the_missing_step_first(self):
        f = selfstate._reading_facts_one({"content": {"topic": "留白", "notReached": "還沒寫下轉折",
                                                      "from": "A", "to": "B"}, "gate": 4}, 3)
        self.assertIn("還沒寫下轉折", f)
        self.assertNotIn("從「A」往「B」", f)                         # 只講一件
        self.assertLessEqual(len(f.splitlines()), 3)

    def test_falls_through_to_direction_then_nature(self):
        self.assertIn("往「B」", selfstate._reading_facts_one({"content": {"topic": "x", "from": "A", "to": "B"}}, None))
        self.assertIn("型態", selfstate._reading_facts_one({"content": {"topic": "x"}, "nature": "探索"}, None))

    def test_says_what_this_gate_adds(self):
        f = selfstate._reading_facts_one({"content": {"topic": "x"}, "gate": 4}, 3)
        self.assertIn("第 3 階", f)
        self.assertIn("這一階比上一階多了什麼", f)

    def test_no_numbers_leak(self):
        f = selfstate._reading_facts_one({"content": {"topic": "x"}, "degree": {"returnIntensity": 0.87}}, None)
        self.assertNotIn("0.87", f)


class CopingTest(unittest.TestCase):
    def test_says_why_now_and_only_one_skill(self):
        r = persona.coping_rule("high_hunger", "約 6 小時", "去翻他以前寫過的那條線")
        self.assertIn("為什麼是現在", r)
        self.assertIn("約 6 小時", r)
        self.assertIn("只做這一件", r)

    def test_last_bubble_asks_nothing(self):
        r = persona.coping_rule("low_mood", "", "")
        self.assertIn("不索取回覆", r)
        self.assertIn("做不到就承認", r)

    def test_split_from_the_reachout_lane(self):
        src = io.open("telegram_monitor/monitor.py", encoding="utf-8").read()
        self.assertIn('prefix=("🌀 " if _cav else "🫧 "), state=state, topic="內在因應"', src)


class ConfigTest(unittest.TestCase):
    def test_flags_synced(self):
        src = io.open("telegram_monitor/config.py", encoding="utf-8").read()
        env = io.open(".env.example", encoding="utf-8").read()
        readme = io.open("README.md", encoding="utf-8").read()
        for flag, attr in (("ASSOCIATION_FOCUS", "association_focus_enabled"),
                           ("FORESIGHT_CHAIN", "foresight_chain_enabled"),
                           ("WORLDLINE_CHAIN", "worldline_chain_enabled"),
                           ("COPING_ACT_VOICE", "coping_act_voice_enabled"),
                           ("MOOD_WATCH_GROUND", "mood_watch_ground_enabled"),
                           ("ADAPT_VOICE", "adapt_voice_enabled")):
            self.assertIn(flag, src)
            self.assertIn(attr, src)
            self.assertRegex(env, re.compile(r"^" + flag + "=1", re.M))
            self.assertIn(flag, readme)
            self.assertFalse(getattr(SimpleNamespace(), attr, False))


if __name__ == "__main__":
    unittest.main()
