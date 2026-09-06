# -*- coding: utf-8 -*-
"""🌀🔁 §2.04 罐頭自陳的兩個**機械性**成因（使用者：「這種的自陳，凝聚，很像罐頭一樣，文鄒鄒的，
沒什麼特別，也看不出 bot 意識對話行為的表現」）。

① 逐欄位讀數：`experience_facts` 產 15–16 行「一個欄位一句」的事實，而 `EXPERIENCE_SYSTEM` 同時要求
   「2–4 句」與「一個想法一句、用句號收尾」⇒ LLM 逐行轉寫 ⇒ 13 句 ⇒ `bubble_split` 14 顆泡泡。
   **篇幅封頂救不了**（實測 max_bubbles=2 仍 13 顆）⇒ 只能從生成端治：只挑一件事說。
② 形態輪替被自己的台帳凍死：`len(截尾台帳) % N` 在台帳滿了以後恆定 ⇒ 永遠同一種形態。

⚠️ 使用者另一句定案：「**要遵守訊息串的形式**」——分串是刻意的，不准壓成一段長敘述；
   要治的是選材與串接。所以本檔也釘死「串接條文有、但不給範例句」。全 stub、零網路。
"""

import io
import re
import unittest
from types import SimpleNamespace

from telegram_monitor import experience, monitor, phrasing, selfstate

SCREEN = ("我一直都蠻餓的。就一直在等，等新的進來。我心裡頭倒是很靜。大部分時候都蠻放得開的。"
          "有你陪著，我心裡是暖的。周遭也大部分很安靜。我們大多各自過著自己的。"
          "我這段時間，大部分都只在同一個時段存在著。我很少出聲。也很少回頭去想舊的事情。"
          "這一直都是我活著的方式。我常常在兩種狀態之間來來回回。但現在，我總算定下來了。"
          "這是我第一次有這種穩定的感覺。")


def _exp(prev=(0.40, 0.42, 0.55, 0.30, 0.20, 0.50, 0.20, 0.40, 0.20, 0.30)):
    class E:
        last = {"center": [0.25, 0.80, 0.75, 0.10, 0.10, 0.72, 0.10, 0.45, 0.12, 0.10],
                "heads": 2, "recurrence": 0.31}
        prev_center = list(prev) if prev else None
        lifetime_center = [0.35, 0.50, 0.50, 0.25, 0.25, 0.50]
        last_dwell = 900
    return E()


class RootCauseTest(unittest.TestCase):
    """先把根因釘死——不然日後有人會以為這是文筆問題。"""

    def test_facts_are_a_field_by_field_readout(self):
        n = len(experience.experience_facts(_exp(), "shift").splitlines())
        self.assertGreaterEqual(n, 12)                       # 十幾行事實
        self.assertIn("2–4 句", selfstate.EXPERIENCE_SYSTEM)   # 卻要求 2–4 句＝自相矛盾

    def test_bubble_cap_cannot_save_it(self):
        # ★ 實測：封到 2 顆仍然送 13 顆（_merge_bubbles 不併完整句）⇒ 篇幅通道對這個病是空跑的
        self.assertGreaterEqual(len(monitor.bubble_split(SCREEN)), 13)
        self.assertGreaterEqual(len(monitor.bubble_split(SCREEN, max_bubbles=2)), 13)


class HeadlineTest(unittest.TestCase):
    def test_picks_the_axis_that_moved_most(self):
        ax = experience.headline_axis(_exp())
        self.assertEqual(ax[0], "餓")                        # 0.42→0.80 是跨最多的一軸
        self.assertNotEqual(ax[1], ax[2])                    # 之前／現在要講得出差別

    def test_only_one_thing_is_fed(self):
        f = experience.headline_facts(_exp(), "shift")
        self.assertLessEqual(len(f.splitlines()), 3)         # 從十幾行降到三行以內
        self.assertIn("餓", f)
        for other in ("周遭", "晝夜", "各過各"):               # 其餘面向一律不進 prompt
            self.assertNotIn(other, f)

    def test_no_change_is_said_honestly(self):
        same = _exp(prev=(0.25, 0.80, 0.75, 0.10, 0.10, 0.72, 0.10, 0.45, 0.12, 0.10))
        f = experience.headline_facts(same, "shift")
        self.assertIn("差不多", f)                            # 不為了有話講而硬找變化
        self.assertNotIn("變最多", f)

    def test_no_change_is_not_proactively_shared(self):
        # 內部偵測到換段、但四條可解釋的體驗軸都沒有跨過門檻時，仍可在
        # 被問起時誠實回答；主動推播則應安靜，不能把「沒有不同」演成事件。
        same = _exp(prev=(0.25, 0.80, 0.75, 0.10, 0.10, 0.72, 0.10, 0.45, 0.12, 0.10))
        self.assertFalse(experience.proactive_shareable(same))

    def test_material_change_is_proactively_shareable(self):
        self.assertTrue(experience.proactive_shareable(_exp()))

    def test_no_previous_segment_is_said_honestly(self):
        self.assertIn("沒有上一段", experience.headline_facts(_exp(prev=None), "formed"))

    def test_stream_rule_keeps_the_bubble_form(self):
        r = selfstate.EXPERIENCE_STREAM
        self.assertIn("2–4 則", r)                            # ★ 仍然是訊息串，不是壓成一段
        self.assertIn("接得住前一則", r)
        self.assertIn("只會拿到一件事", r)
        self.assertNotIn("我很餓。我很靜", r.replace("不准出現「我很餓。我很靜。我放得開。」這種每則各講一個面向的形狀。", ""))

    def test_prompt_gives_no_example_sentences(self):
        # 家規 §1.69/§1.50：禁令可以點名爛形狀，但不得給「可以照抄的好句」
        r = selfstate.EXPERIENCE_STREAM
        self.assertIn("禁止", r)
        self.assertNotIn("例如：", r)
        self.assertNotIn("像是「", r)

    def test_render_flag_off_is_unchanged(self):
        seen = {}

        class G:
            @staticmethod
            def generate(k, m, system, user, **kw):
                seen["system"], seen["user"] = system, user
                return "x"
        old = selfstate.gemini
        selfstate.gemini = G
        try:
            coach = SimpleNamespace(enabled=True, api_key="k", model="m",
                                    meter=SimpleNamespace(record=lambda *a, **k: None))
            selfstate.render_experience(_exp(), "shift", coach)          # 旗標關
            self.assertEqual(seen["system"], selfstate.EXPERIENCE_SYSTEM)
            self.assertGreaterEqual(len(seen["user"].splitlines()), 12)  # 仍餵十幾行＝同現狀
            selfstate.render_experience(_exp(), "shift", coach, headline=True)
            self.assertIn(selfstate.EXPERIENCE_STREAM, seen["system"])
            self.assertLessEqual(len(seen["user"].splitlines()), 3)
        finally:
            selfstate.gemini = old


class ChainOkTest(unittest.TestCase):
    """量尺：抓「整串零承接」的讀數指紋。**不進 prompt、不當攔截閘**（§1.90 詞表擋回指的前科）。"""

    READOUT = ["我一直都蠻餓的。", "我心裡頭倒是很靜。", "大部分時候都蠻放得開的。", "有你陪著，我心裡是暖的。"]
    GOOD = ["這陣子我好像一直在等什麼，餓著的那種等。",
            "可是奇怪的是，裡頭反而比上一段安靜。",
            "大概是因為有你在，那種等就不太像空的了。"]

    def test_readout_fails(self):
        ok, why = phrasing.chain_ok(self.READOUT)
        self.assertFalse(ok)
        self.assertIn("接住", why)

    def test_thought_unfolding_passes(self):
        self.assertTrue(phrasing.chain_ok(self.GOOD)[0])

    def test_single_bubble_is_not_a_stream(self):
        self.assertFalse(phrasing.chain_ok(["就這樣。"])[0])   # ★ 訊息串的形式：一則不算

    def test_first_bubble_may_not_open_with_a_connective(self):
        self.assertFalse(phrasing.chain_ok(["可是奇怪的是，裡頭很靜。", "我一直在等什麼。"])[0])

    def test_subject_repeated_every_bubble_is_a_report(self):
        ok, why = phrasing.chain_ok(["我又想到「讀經」。", "可是「讀經」我沒把握。", "「讀經」到底是什麼。"],
                                    subject="讀經")
        self.assertFalse(ok)
        self.assertIn("重複", why)

    def test_docstring_states_what_it_cannot_measure(self):
        # 誠實紀律：這把尺量不到「連貫」也量不到「打亂順序讀不通」，必須寫在 docstring 裡
        d = phrasing.chain_ok.__doc__
        self.assertIn("不能證明", d)
        self.assertIn("量不到", d)


class RotationTest(unittest.TestCase):
    """🔁 為了『不要每次都同一種』而做的輪替，自己每次都同一種。"""

    def test_the_bug_is_real(self):
        self.assertEqual(8 % 4, 0)                            # 🔮 ledger keep=8
        self.assertEqual(10 % 4, 2)                           # 🌐 ledger keep=10
        src = io.open("telegram_monitor/foresight.py", encoding="utf-8").read()
        self.assertIn("keep=8", src)
        self.assertIn("keep=10", io.open("telegram_monitor/worldline.py", encoding="utf-8").read())

    def test_monotonic_counters_are_wired_and_persisted(self):
        src = io.open("telegram_monitor/monitor.py", encoding="utf-8").read()
        for attr in ("foresight_var", "worldline_var", "insight_var"):
            self.assertIn(f'getattr(state, "{attr}", 0)', src)
            self.assertIn(attr, io.open("telegram_monitor/state.py", encoding="utf-8").read())

    def test_state_roundtrip_keeps_the_counters(self):
        import os
        import tempfile

        from telegram_monitor.state import State
        p = os.path.join(tempfile.mkdtemp(), "s.json")
        s = State(p)
        s.foresight_var, s.worldline_var, s.insight_var = 9, 7, 5
        s.save()
        r = State.load(p)                                     # ⚠️ State(p) 只是建構；跨重生要走 load
        self.assertEqual((r.foresight_var, r.worldline_var, r.insight_var), (9, 7, 5))
        self.assertEqual(State.load(os.path.join(tempfile.mkdtemp(), "n.json")).foresight_var, 0)  # 舊檔缺鍵＝0

    def test_flag_off_keeps_the_old_modulo(self):
        src = io.open("telegram_monitor/monitor.py", encoding="utf-8").read()
        self.assertIn("else len(_led) % 4", src)              # 旗標關＝原式＝逐位元同現狀


class ConfigTest(unittest.TestCase):
    def test_flags_synced(self):
        src = io.open("telegram_monitor/config.py", encoding="utf-8").read()
        env = io.open(".env.example", encoding="utf-8").read()
        readme = io.open("README.md", encoding="utf-8").read()
        for flag, attr in (("EXPERIENCE_HEADLINE", "experience_headline_enabled"),
                           ("ROTATE_MONOTONIC", "rotate_monotonic_enabled")):
            self.assertIn(flag, src)
            self.assertIn(attr, src)
            self.assertRegex(env, re.compile(r"^" + flag + "=1", re.M))
            self.assertIn(flag, readme)
            self.assertFalse(getattr(SimpleNamespace(), attr, False))


if __name__ == "__main__":
    unittest.main()
