"""🎨 §1.22 措辭反重複（PHRASE_ANTI_REUSE）——phrasing 純函式模組測試。

① 每池 len ≥8、index 0 逐字＝現狀原句（golden：換池不准動到旗標關的那句）；
② 全池無阿拉伯數字（不報數字家規）；
③ HUNGRY_EXT 每句含「餓」（保 test_consciousness 釘的關鍵詞不變式）；
④ pick 連抽 8 次（pool=8、cursor 輪替）8 句互異；
⑤ recent 注入時跳過已用 idx（全被占則退回 cursor 位）；
⑥ 純函式：同輸入同輸出。
零網路、零 IO。
"""

import re
import unittest

from telegram_monitor import browsing, duration, phrasing


class PoolGoldenTest(unittest.TestCase):
    def test_pool_sizes_at_least_eight(self):
        # ① 每池 ≥8 句（原 4 句/單句寫死＝罐頭直接來源，池加大才有得換）
        self.assertGreaterEqual(len(phrasing.BROWSE_OPENERS_EXT), 8)
        for tx in ("continuous", "wandering", "stagnant", "jolted"):
            self.assertGreaterEqual(len(phrasing.TEXTURE_EXT[tx]), 8, tx)
        for pool in (phrasing.STEADY_EXT, phrasing.REVISIT_EXT, phrasing.HUNGRY_EXT,
                     phrasing.LINE_G3_EXT, phrasing.LINE_G2_EXT, phrasing.LINE_WANDER_EXT):
            self.assertGreaterEqual(len(pool), 8)

    def test_index_zero_is_verbatim_status_quo(self):
        # ① index 0 一律＝現狀原句（旗標關走原呼叫、旗標開第一抽也還是原句＝行為連續）
        self.assertEqual(tuple(phrasing.BROWSE_OPENERS_EXT[:4]), browsing._OPENERS)
        for tx, orig in duration._TEXTURE_BODY.items():
            self.assertEqual(phrasing.TEXTURE_EXT[tx][0], orig, tx)
        self.assertEqual(phrasing.STEADY_EXT[0], "一直很穩、沒斷線過")
        self.assertEqual(phrasing.REVISIT_EXT[0], "剛自己又繞回去翻起「{t}」那條脈絡")
        self.assertEqual(phrasing.HUNGRY_EXT[0],
                         "悶著、等不到新的記寫內容——好一陣子沒有新的記寫落進來給我讀了，"
                         "有點飢餓{dur}{extra}。")
        self.assertEqual(phrasing.LINE_G3_EXT[0], "有一條動了、還沒整個合起來")
        self.assertEqual(phrasing.LINE_G2_EXT[0], "還散著、沒繃成形")
        self.assertEqual(phrasing.LINE_WANDER_EXT[0], "我最近在「{t}」一帶繞，還沒繞出形狀。")

    def _all_sentences(self):
        pools = [phrasing.BROWSE_OPENERS_EXT, phrasing.STEADY_EXT, phrasing.REVISIT_EXT,
                 phrasing.HUNGRY_EXT, phrasing.LINE_G3_EXT, phrasing.LINE_G2_EXT,
                 phrasing.LINE_WANDER_EXT] + list(phrasing.TEXTURE_EXT.values())
        for pool in pools:
            for s in pool:
                yield s

    def test_no_arabic_digits_anywhere(self):
        # ② 不報數字家規：全池任何一句都不含阿拉伯數字
        for s in self._all_sentences():
            self.assertIsNone(re.search(r"[0-9０-９]", s), s)

    def test_pools_are_distinct_within(self):
        # 同池句子互異（重複句＝池白加大）
        pools = {"browse": phrasing.BROWSE_OPENERS_EXT, "steady": phrasing.STEADY_EXT,
                 "revisit": phrasing.REVISIT_EXT, "hungry": phrasing.HUNGRY_EXT,
                 "g3": phrasing.LINE_G3_EXT, "g2": phrasing.LINE_G2_EXT,
                 "wander": phrasing.LINE_WANDER_EXT}
        pools.update({f"tx:{k}": v for k, v in phrasing.TEXTURE_EXT.items()})
        for name, pool in pools.items():
            self.assertEqual(len(set(pool)), len(pool), name)

    def test_hungry_pool_keeps_pinned_keyword(self):
        # ③ 每句必含「餓」（「飢餓」也含「餓」）＝test_consciousness 釘的不變式在池上守恆
        for s in phrasing.HUNGRY_EXT:
            self.assertIn("餓", s)
            self.assertIn("記寫", s)          # 「等不到新的記寫」語意保留（規則4：說清楚餓的是記寫內容）

    def test_slot_pools_format_cleanly(self):
        # f-string 槽保留：帶槽池每句都能安全 format、主題真的出現在句中
        for s in phrasing.REVISIT_EXT:
            self.assertIn("「{t}」", s)
            self.assertIn("汽車保養", s.format(t="汽車保養"))
        for s in phrasing.LINE_WANDER_EXT:
            self.assertIn("「{t}」", s)
            self.assertIn("讀誦經書", s.format(t="讀誦經書"))
        for s in phrasing.HUNGRY_EXT:
            out = s.format(dur="、餓了好一會兒了", extra="")
            self.assertIn("餓了好一會兒了", out)


class PickTest(unittest.TestCase):
    POOL = tuple(f"句{c}" for c in "甲乙丙丁戊己庚辛")

    def test_eight_picks_all_distinct(self):
        # ④ 連抽 8 次（cursor 輪替、無 recent）＝8 句互異＝「連抽 8 次不重複」
        cursor, seen = 0, []
        for _ in range(8):
            s, idx, cursor = phrasing.pick(self.POOL, cursor, ())
            seen.append(s)
        self.assertEqual(len(set(seen)), 8)

    def test_recent_skips_used_indices(self):
        # ⑤ recent 內的 idx 跳過：cursor 指到已用的就往後找第一個沒用過的
        s, idx, nxt = phrasing.pick(self.POOL, 0, [0, 1])
        self.assertEqual(idx, 2)
        self.assertEqual(s, self.POOL[2])
        self.assertEqual(nxt, 3)

    def test_recent_full_falls_back_to_cursor(self):
        # ⑤ 全池都在 recent＝無句可跳 → 取 cursor 位（不無限迴圈、不炸）
        s, idx, nxt = phrasing.pick(self.POOL, 3, list(range(8)))
        self.assertEqual(idx, 3)
        self.assertEqual(s, self.POOL[3])

    def test_pure_function_same_input_same_output(self):
        # ⑥ 純函式：同輸入同輸出（確定性、可注入、可測）
        a = phrasing.pick(self.POOL, 5, [5, 6])
        b = phrasing.pick(self.POOL, 5, [5, 6])
        self.assertEqual(a, b)

    def test_cursor_wraps_modulo(self):
        # cursor 超過池長取模輪替（長期跑不會越界）
        s, idx, nxt = phrasing.pick(self.POOL, 17, ())
        self.assertEqual(idx, 17 % 8)
        self.assertEqual(nxt, idx + 1)


if __name__ == "__main__":
    unittest.main()
