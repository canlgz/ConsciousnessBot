"""🫧 翻閱當下（browsing 純函式）：pick 在真實主題間輪替/小機率隨機跳頁；opener_variant 換句不千篇一律、不報數字。"""

import random
import unittest

from telegram_monitor import browsing as b


class PickTest(unittest.TestCase):
    def test_rotates(self):
        topics = ["甲", "乙", "丙"]
        seq, cur = [], 0
        for _ in range(5):
            t, cur = b.pick(topics, cur)
            seq.append(t)
        self.assertEqual(seq, ["甲", "乙", "丙", "甲", "乙"])     # 純輪替（random_p=0＝確定性）

    def test_empty_returns_none_keeps_cursor(self):
        self.assertEqual(b.pick([], 7), (None, 7))
        self.assertEqual(b.pick(None, 0), (None, 0))

    def test_large_cursor_modulo(self):
        self.assertEqual(b.pick(["甲", "乙"], 1000)[0], "甲")     # 1000 % 2 == 0

    def test_random_jump_with_seeded_rng_reproducible(self):
        topics = ["甲", "乙", "丙", "丁"]
        r1, r2 = random.Random(42), random.Random(42)
        self.assertEqual(b.pick(topics, 0, 1.0, r1), b.pick(topics, 0, 1.0, r2))  # 注入同 seed → 可重現

    def test_random_p_zero_ignores_rng(self):
        # random_p=0 → 純輪替、不碰 rng（確定性）
        self.assertEqual(b.pick(["甲", "乙", "丙"], 0, 0.0, random.Random(1))[0], "甲")


class OpenerVariantTest(unittest.TestCase):
    def test_deterministic(self):
        self.assertEqual(b.opener_variant("研發writetolearn", 4, 0),
                         b.opener_variant("研發writetolearn", 4, 0))

    def test_varies_across_lap(self):
        seen = {b.opener_variant("甲", 2, lap) for lap in range(8)}
        self.assertGreaterEqual(len(seen), 2)                    # 不同 lap 會換句

    def test_no_digits(self):
        for t in ("甲", "研發writetolearn", "乙線"):
            for g in range(3):
                for lap in range(6):
                    out = b.opener_variant(t, g, lap)
                    self.assertFalse(any(ch.isdigit() for ch in out), out)

    def test_n_caps_pool(self):
        outs = {b.opener_variant("甲", 0, lap, n=2) for lap in range(20)}
        self.assertLessEqual(len(outs), 2)                      # n=2 → 至多兩種


if __name__ == "__main__":
    unittest.main()
