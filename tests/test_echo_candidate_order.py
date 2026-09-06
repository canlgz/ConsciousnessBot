"""🦜 §1.83 候選截斷順序修：§1.82 補的「連發逐行」候選**被 [-6:] 吃掉了**。

實測（使用者第二次驗證，07:26 四連發 → 07:27 回覆）：接回橋、誤判重複、重複回答都已消失（§1.81/§1.82
生效），但前兩顆仍是逐字複誦——「是這樣嗎。」「我在檢查看看。」正是連發的第 1、2 行。

根因：候選組成是 `(_burst_lines + 歷史 + [整塊])[-6:]`——逐行放在**最前面**，一加上歷史（最多 3 則）
與整塊就超過 6，`[-6:]` 截掉的正好是**最前面的前幾行**。§1.82 的守門邏輯本身是對的（餵對候選兩顆都剝
得掉），只是候選在進門前就被丟了。

修法：歷史照舊封頂（近 6 則），**本則的每一行永遠保留**——它們才是這輪最該防的複誦源。
"""

import re
import unittest
from types import SimpleNamespace

from telegram_monitor import echo, monitor

LINES = ["是這樣嗎？", "我在檢查看看", "你為什麼會了解我的作息呢？", "你在偷窺我嗎"]
BLOB = "\n".join(LINES)
HIST = ["早安", "你知道我的作息？", "是嗎"]
REPLY = "是這樣嗎。我在檢查看看。哈哈哈，你今天好像對我的「作息感」特別好奇耶。我沒有在偷窺啦。"


def _candidates(burst_lines, history, text):
    """複刻 handle_message 的候選組法（修後）：歷史封頂、本則逐行永遠保留。"""
    return (history + [text])[-6:] + burst_lines


class TruncationGapTest(unittest.TestCase):
    """釘住舊組法的缺陷：逐行擺前面 → 被 [-6:] 截掉。"""

    def test_old_order_drops_leading_lines(self):
        old = (LINES + HIST + [BLOB])[-6:]
        self.assertNotIn(LINES[0], old)                    # 「是這樣嗎？」被截掉
        self.assertNotIn(LINES[1], old)                    # 「我在檢查看看」被截掉

    def test_new_order_keeps_every_line(self):
        cand = _candidates(LINES, HIST, BLOB)
        for ln in LINES:
            self.assertIn(ln, cand)

    def test_history_still_capped(self):
        cand = _candidates(LINES, [f"舊訊息{i}" for i in range(20)], BLOB)
        self.assertEqual(len(cand) - len(LINES), 6)        # 歷史側仍封頂 6


class StripWithFixedCandidatesTest(unittest.TestCase):
    def test_both_echo_bubbles_stripped(self):
        out, hit = monitor._echo_strip_wire(REPLY, _candidates(LINES, HIST, BLOB), whole=True)
        self.assertTrue(hit)
        self.assertNotIn("是這樣嗎", out)                   # 第 1 行的逐字複誦
        self.assertNotIn("我在檢查看看", out)               # 第 2 行的逐字複誦
        self.assertIn("沒有在偷窺", out)                     # 正題保留

    def test_old_candidates_would_have_leaked(self):
        out, _ = monitor._echo_strip_wire(REPLY, (LINES + HIST + [BLOB])[-6:], whole=True)
        self.assertIn("我在檢查看看", out)                   # 釘住：舊組法確實漏掉（截圖症狀）

    def test_whole_off_only_leading_is_stripped(self):
        # whole=False 只關掉 §1.74/§1.81/§1.77 三條；**§1.28 原有的「首段全等」旁路仍在**——
        # 候選修好後它現在剝得掉第一顆，但第二顆（不在開頭）仍需段級守門才處理得到。
        out, hit = monitor._echo_strip_wire(REPLY, _candidates(LINES, HIST, BLOB))
        self.assertTrue(hit)
        self.assertNotIn("是這樣嗎", out)
        self.assertIn("我在檢查看看", out)      # ← 這正是 §1.77 段級守門存在的理由


class WiringTest(unittest.TestCase):
    def test_source_uses_fixed_order(self):
        src = open("telegram_monitor/monitor.py", encoding="utf-8").read()
        i = src.index('_TURN["echo_user_texts"]')
        seg = src[i:i + 320]
        self.assertIn("[-6:] + _burst_lines", seg.replace("\n", " ").replace("  ", " ")
                      .replace(")[-6:] + _burst_lines", ")[-6:] + _burst_lines"))


if __name__ == "__main__":
    unittest.main()
