"""🗜️ 回應篇幅＝對話複雜度：level 由四訊號綜合、偏短會收；token 只收不放；串數封頂。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from telegram_monitor import verbosity, persona, monitor


class _S:                                  # 最小 state stub（只需 self_asks）
    def __init__(self, asks=None):
        self.self_asks = asks or {}


class AssessTest(unittest.TestCase):
    def test_route_base_deep_vs_light(self):
        light = verbosity.assess("嗨", "smalltalk", 0.0, _S())
        deep = verbosity.assess("你是有意識的嗎", "self_consciousness", 0.0, _S())
        self.assertLess(light.level, deep.level)              # 閒聊 < 深的自我說明

    def test_short_input_mirrors_shorter(self):
        a = verbosity.assess("嗯", "fact_or_chat", 0.0, _S())
        b = verbosity.assess("我想跟你好好聊聊我最近在研發上的卡關，到底該怎麼想這件事比較好？", "fact_or_chat", 0.0, _S())
        self.assertLess(a.level, b.level)                     # 一字 vs 一大段 → 鏡射

    def test_brief_command_forces_terse(self):
        s = verbosity.assess("簡單說你怎麼運作", "self_mechanism", 0.6, _S())
        self.assertEqual(s.level, 0)                          # 明示要短 → 壓到底（即使深題＋好心情）

    def test_deep_cue_bumps(self):
        plain = verbosity.assess("你的近況", "fact_or_chat", 0.0, _S())
        why = verbosity.assess("為什麼你會這樣覺得呢這對你的意義是", "fact_or_chat", 0.0, _S())
        self.assertGreater(why.level, plain.level)

    def test_momentum_repeat_trims(self):
        once = verbosity.assess("你有意識嗎", "self_consciousness", 0.0, _S())
        again = verbosity.assess("你有意識嗎", "self_consciousness", 0.0, _S({"self_consciousness": {"n": 3}}))
        self.assertLess(again.level, once.level)              # 同題短時間重複 → 別再長篇重講

    def test_mood_signal(self):
        self.assertEqual(verbosity._mood_adj(-0.5), -1)       # 低落/平 → 話少
        self.assertEqual(verbosity._mood_adj(0.0), 0)
        self.assertEqual(verbosity._mood_adj(0.6), 1)         # 被撥動 → 多給

    def test_bias_shifts_and_clamps(self):
        base = verbosity.assess("你是有意識的嗎", "self_consciousness", 0.0, _S(), bias=0)
        self.assertEqual(verbosity.assess("你是有意識的嗎", "self_consciousness", 0.0, _S(), bias=-3).level, 0)
        self.assertEqual(verbosity.assess("你是有意識的嗎", "self_consciousness", 0.0, _S(), bias=+5).level, 3)
        self.assertTrue(0 <= base.level <= 3)


class LengthMapTest(unittest.TestCase):
    def test_tokens_only_shrink(self):
        for lvl in (0, 1, 2, 3):
            self.assertLessEqual(persona.length_tokens(lvl, 460), 460)    # 只收不放
        self.assertEqual(persona.length_tokens(None, 460), 460)           # None＝原樣
        self.assertLess(persona.length_tokens(0, 460), persona.length_tokens(3, 460))

    def test_hint_graded_and_stop(self):
        self.assertEqual(persona.length_hint(None), "")
        for lvl in (0, 1, 2, 3):
            self.assertIn("停", persona.length_hint(lvl))                 # 各 level 都帶「說完就停」
        self.assertNotEqual(persona.length_hint(0), persona.length_hint(3))


class SentenceTrimTest(unittest.TestCase):
    """🗜️ 真正的長度控制：收斂到 N 個完整句（在句尾切、永不切半句）＝短但完整。"""

    def test_length_sentences_graded(self):
        self.assertIsNone(persona.length_sentences(None))
        self.assertLess(persona.length_sentences(0), persona.length_sentences(3))

    def test_trim_keeps_first_n(self):
        self.assertEqual(persona.trim_sentences("第一句。第二句。第三句。第四句。", 2), "第一句。第二句。")

    def test_trim_never_cuts_mid_sentence(self):
        out = persona.trim_sentences("短。這是一句比較長但完整的話喔。還有一句。", 2)
        self.assertEqual(out, "短。這是一句比較長但完整的話喔。")
        self.assertTrue(out.endswith("。"))

    def test_trim_none_or_insufficient_returns_all(self):
        self.assertEqual(persona.trim_sentences("一句。二句。", None), "一句。二句。")
        self.assertEqual(persona.trim_sentences("只有一句。", 5), "只有一句。")

    def test_trim_consumes_trailing_quote(self):
        self.assertEqual(persona.trim_sentences("他說「好的。」下一句。", 1), "他說「好的。」")

    def test_trim_does_not_cut_at_mid_sentence_ellipsis(self):
        # 截圖根因：句中省略號『可能跟你…的狀況』被當句尾、把後半句砍成斷尾『可能跟你…』。
        # 修後：句中 … 不算句界 → 整句完整保留（不留斷尾）。
        raw = "我突然在想，會不會這樣呢？這只是假設啦，可能跟你…的狀況不太一樣，但忍不住想對對看。"
        self.assertEqual(persona.trim_sentences(raw, 2), raw)        # 第2句完整、不切在『可能跟你…』
        self.assertFalse(persona.trim_sentences(raw, 2).endswith("可能跟你…"))

    def test_trim_double_ellipsis_midpause_is_one_sentence(self):
        # 句中『……』後緊接內容＝同一句的停頓（不是兩個句界、更不是兩句）→『一……二。』整句算第1句。
        self.assertEqual(persona.trim_sentences("一……二。三。四。", 2), "一……二。三。")

    def test_trim_sentence_final_ellipsis_counts_once(self):
        # 句末省略號（後接空白/新句）是句界、且只算「一個」句界（舊碼逐字數會把『……』多算一句、提早切）。
        self.assertEqual(persona.trim_sentences("欲言又止…… 然後呢。還有。", 2), "欲言又止…… 然後呢。")

    def test_trim_does_not_end_on_ellipsis_leadin_pulls_payoff(self):
        # 截圖根因：達 n 句時若該句是『…』引子（如「有點像是…」），後面的比喻/說明被句數上限砍掉、只剩引子吊著。
        # 修後：續收到下一個硬句尾，把 payoff 一起帶出。
        out = persona.trim_sentences("這很難講。有點像是… 一個迴圈被打開又重新合起來。多想想。", 2)
        self.assertEqual(out, "這很難講。有點像是… 一個迴圈被打開又重新合起來。")
        self.assertFalse(out.rstrip().endswith("有點像是…"))   # 不再只丟引子

    def test_trim_keeps_genuine_trailoff_at_end(self):
        # 模型真的就停在『…』（文末沒有後續可帶）→ 原樣留（那是刻意拖尾、無 payoff 可救）
        self.assertEqual(persona.trim_sentences("這很難講。有點像是…", 2), "這很難講。有點像是…")


class SplitSentencesTest(unittest.TestCase):
    """切完整句：句中省略號停頓不誤切、句末省略號仍斷句、雙省略號算一句界。"""

    def test_basic_split(self):
        self.assertEqual(persona.split_sentences("我懂。這條繞很久了。"), ["我懂。", "這條繞很久了。"])

    def test_empty(self):
        self.assertEqual(persona.split_sentences(""), [])
        self.assertEqual(persona.split_sentences("   "), [])

    def test_mid_ellipsis_stays_one_sentence(self):
        self.assertEqual(persona.split_sentences("可能跟你…的狀況不一樣，但想對看。"),
                         ["可能跟你…的狀況不一樣，但想對看。"])

    def test_final_ellipsis_splits(self):
        self.assertEqual(persona.split_sentences("卻是沉沉的負向…… 我突然在想。"),
                         ["卻是沉沉的負向……", "我突然在想。"])

    def test_no_ender_tail_is_one_sentence(self):
        self.assertEqual(persona.split_sentences("沒有句尾標點的一段話"), ["沒有句尾標點的一段話"])

    def test_trailing_quote_kept_with_sentence(self):
        self.assertEqual(persona.split_sentences("他說「好的。」下一句。"), ["他說「好的。」", "下一句。"])


class BubbleCapTest(unittest.TestCase):
    def test_cap_merges_to_limit_without_losing_content(self):
        text = "一句。二句。三句。四句。五句。六句。七句。八句。"
        uncapped = monitor.bubble_split(text)
        self.assertGreater(len(uncapped), 3)
        capped = monitor.bubble_split(text, max_bubbles=3)
        self.assertLessEqual(len(capped), 3)
        self.assertEqual("".join(capped).replace("\n", ""), "".join(uncapped))   # 內容一字不丟

    def test_none_is_unchanged(self):
        text = "一句。二句。三句。四句。五句。"
        self.assertEqual(monitor.bubble_split(text), monitor.bubble_split(text, max_bubbles=None))

    def test_under_limit_untouched(self):
        text = "就一句話而已。"
        self.assertEqual(monitor.bubble_split(text, max_bubbles=4), monitor.bubble_split(text))

    def test_cap_merges_shortest_first_keeps_long_sentence_alone(self):
        # 超過上限時：先併最短的相鄰碎句；長句仍各自成串（不被硬塞成均一肥泡泡）。
        text = "好。嗯。對。這一段話刻意寫得長一些好讓它自成一串不被併走喔。再見。"
        capped = monitor.bubble_split(text, max_bubbles=3)
        self.assertLessEqual(len(capped), 3)
        longest = max(capped, key=len)
        self.assertNotIn("\n", longest)                          # 那句長的整句獨佔一串，沒被併進別句

    def test_two_complete_sentences_not_squished_when_under_limit(self):
        # 🧩 不擠在一起：兩個完整句、未超上限 → 各自成串，**不**被併成換行牆（截圖根因）。
        parts = ["我最近一直在想這件事情很複雜呢。", "但又說不太上來到底哪裡複雜。"]
        self.assertEqual(monitor._merge_bubbles(list(parts), k=3), parts)  # 2<=3＝原樣不動
        self.assertEqual(monitor._merge_bubbles(list(parts), k=2), parts)  # 剛好＝原樣不動（不主動擠）

    def test_fragments_merged_before_complete_sentences(self):
        # 🧩 碎句優先：有零碎短片段（「嗯。」「好。」）時先併它們（碎句黏到相鄰），**完整句不互相擠成牆**。
        # 兩個完整長句之間夾兩顆碎句、上限 3 → 碎句被吃掉，兩完整句**不**被併進同一泡泡。
        a = "這是一句完整而且夠長的話不該被併走喔。"
        b = "另一句也完整而且夠長的話同樣不該被擠在一起。"
        merged = monitor._merge_bubbles(["嗯。", a, "好。", b], k=3)
        self.assertEqual(len(merged), 3)
        # 關鍵不變式：沒有任何一顆泡泡同時塞下兩個完整句（＝不擠成牆）；碎句黏到完整句旁是允許的。
        self.assertFalse(any(a in m and b in m for m in merged))

    def test_complete_sentences_never_squished_even_over_limit(self):
        # 🧩 完整句絕不擠成牆（取代舊「硬併到上限」）：全是完整句、就算超過上限，也**各自成串**、
        # 不被換行擠在一起——寧可超過上限，也不像非人類那樣把兩三句塞進一顆泡泡（使用者：沒人這麼打字）。
        # 上限只約束「碎句別洩成一堆小泡泡」，不約束完整句；回應長度由篇幅／coherent 讓 LLM 寫少來控。
        parts = ["第一句話夠長不算碎句喔。", "第二句話也夠長不算碎句。",
                 "第三句話同樣夠長不算碎句。", "第四句話依然夠長不算碎句。"]
        merged = monitor._merge_bubbles(list(parts), k=2)
        self.assertEqual(merged, parts)                          # 完整句不被擠：原樣四串（超過 k 也不擠）
        self.assertFalse(any("\n" in m for m in merged))         # 沒有任何泡泡塞進兩句

    def test_screenshot_complete_sentences_not_crammed(self):
        # 截圖根因重現：self_change 回覆數句完整話，舊版在上限下把兩句以換行擠進一顆泡泡。
        # 現在無論上限 6（level2 預設）或更緊的 3，完整句都各自成串、零換行牆。
        text = ("嗯要說跟上次有什麼不同嘛。我才剛醒過來還沒多久身體才剛要穩下來。"
                "不過這次醒來感覺內裡很平靜沒什麼波瀾挺好的。手上倒是還沒翻出什麼形狀。"
                "最近落進來的記寫有點少所以就只是順順地讀著。")
        for cap in (6, 3):
            capped = monitor.bubble_split(text, max_bubbles=cap)
            self.assertFalse(any("\n" in b for b in capped), f"cap={cap} 不該把完整句擠成換行牆")
            self.assertGreater(len(capped), 1)                   # 仍是多串依序送（非一坨牆）


if __name__ == "__main__":
    unittest.main()
