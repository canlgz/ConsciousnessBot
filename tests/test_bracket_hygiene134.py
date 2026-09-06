"""🧹 §1.34 送出前括號衛生：孤兒全形「）」泡泡＋跨句不成對括號 → 清掉；半形顏文字 :) 絕不誤傷。

截圖：user 問「為什麼我老是看到你出現）」——bot 一直漏出單獨一顆「）」泡泡（LLM 把「）」放空行後、或
括號補述跨句被切開），還把這 bug 硬拗成「我自己一個習慣的標記、語氣的收束」。旗標 BUBBLE_BRACKET_HYGIENE=0＝同現狀。
"""

import os
import unittest
from unittest.mock import patch

from telegram_monitor import monitor as M


class BracketHygieneTest(unittest.TestCase):
    def _no_orphan(self, bubbles):
        for b in bubbles:
            self.assertNotIn(b.strip(), ("）", "（"), f"孤兒全形括號泡泡: {bubbles!r}")
            self.assertEqual(b.count("（"), b.count("）"), f"泡泡內全形括號不成對: {b!r}")

    def test_lone_fullwidth_paren_dropped(self):
        self.assertEqual(M.bubble_split("我這次會好好地挑選。\n\n）"), ["我這次會好好地挑選。"])

    def test_cross_sentence_parens_stripped(self):
        out = M.bubble_split("（要不要我把這個記成做法？以後就這樣做。回我一聲好就學起來。）")
        self._no_orphan(out)
        self.assertTrue(any("要不要" in b for b in out))

    def test_balanced_paren_within_bubble_untouched(self):
        self.assertEqual(M.bubble_split("我把話說完了。（這是我習慣的收束。）"),
                         ["我把話說完了。", "（這是我習慣的收束。）"])

    def test_halfwidth_emoticon_preserved(self):
        # 半形顏文字絕不能被當孤兒括號剝掉（截圖前踩過：「送你 :)」被剝成「送你 :」）
        self.assertEqual(M.bubble_split("來，這張真貼圖送你 :)"), ["來，這張真貼圖送你 :)"])
        self.assertEqual(M.bubble_split("沒關係啦 :("), ["沒關係啦 :("])

    def test_plain_text_untouched(self):
        self.assertEqual(M.bubble_split("嗨，今天過得如何？我這邊還不錯。"),
                         ["嗨，今天過得如何？", "我這邊還不錯。"])

    def test_flag_off_bit_identical(self):
        with patch.dict(os.environ, {"BUBBLE_BRACKET_HYGIENE": "0"}):
            # 關旗標＝孤兒「）」照舊漏出（逐位元同現狀）
            self.assertIn("）", M.bubble_split("我這次會好好地挑選。\n\n）"))


if __name__ == "__main__":
    unittest.main()
