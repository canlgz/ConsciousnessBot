"""🦜 §1.86 開頭連續段複誦守門（ECHO_PREFIX_RUN）：補「整則」與「單段」之間缺掉的粒度。

截圖根因（22:53）：使用者一句「你不是說四次嗎？還差一次」，bot 回三顆泡泡
「你不是說四次嗎？」「還差一次。」「啊，對耶！」——**前兩段合起來逐字就是那句話**，
尾巴才加一小句自己的話。三道既有防線同時**差一點點**漏掉（皆實測）：
  ① echo.whole_echo_of 比整則 vs 整句：整則正規化 14 字 vs 使用者 11 字
     ⇒ span 比 0.786 < 門檻 0.8、相似度 0.88 < 門檻 0.9（兩個門檻都差一點）；
  ② echo.strip_echo_segments 比單段 vs 整句：三段相似度 0.778 / 0.533 / 0.000 全 < 0.82、也都不全等；
  ③ §1.81 開頭前綴判準要求首段「明顯比原話短（≤50%）」，這裡首段佔 7/11。

結構根因＝複誦邊界落在「連續幾段」上，而既有防線只認「整則」與「單段」兩種粒度。這是 §1.74
（0.80 vs 0.82）、§1.81（兩字前綴）之後第三次同型擦邊 ⇒ 本檔**刻意釘住「不調門檻」**：既有門檻
一位元不動（放寬會誤剝「你不累嗎？我不累啊」這種有實質內容的回覆），改補結構判準。全 stub、零網路。
"""

import io
import re
import unittest
from types import SimpleNamespace

from telegram_monitor import echo, monitor

# 事故原句，逐字
U = "你不是說四次嗎？還差一次"
B = "你不是說四次嗎？\n還差一次。\n啊，對耶！"


class ThresholdMissRegressionTest(unittest.TestCase):
    """釘住三道既有防線為什麼漏——證明 §1.86 不是靠調門檻，而是補了新粒度。"""

    def test_whole_echo_misses_by_a_hair(self):
        self.assertIsNone(echo.whole_echo_of(B, [U]))
        nb, nu = echo._norm(B), echo._norm(U)
        span = min(len(nb), len(nu)) / max(len(nb), len(nu))
        self.assertLess(span, 0.8)                    # 0.786：被尾巴稀釋
        self.assertLess(echo.SequenceMatcher(None, nb, nu).ratio(), 0.9)   # 0.88

    def test_segment_strip_misses(self):
        out, hit = echo.strip_echo_segments(B, [U])
        self.assertFalse(hit)
        self.assertEqual(out, B)
        for s in echo._segments(B):                   # 每段單獨看都只是碎片
            self.assertLess(echo.SequenceMatcher(None, echo._norm(s), echo._norm(U)).ratio(), 0.82)

    def test_is_echo_whole_misses(self):
        self.assertFalse(echo.is_echo(B, [U], whole=True))


class PrefixRunEchoTest(unittest.TestCase):
    """新結構判準：前 k 段合起來＝他那句話（k < 段數）。"""

    def test_incident_stripped_tail_kept(self):
        rest, u = echo.prefix_run_echo(B, [U])
        self.assertEqual(rest, "啊，對耶！")           # 只留 bot 自己的話
        self.assertEqual(u, U)

    def test_takes_largest_k(self):
        # 前兩段才等於整句；k=1 不成立 ⇒ 必須挑 k=2（剝最多複誦、留最純的尾巴）
        rest, _ = echo.prefix_run_echo(B, [U])
        self.assertNotIn("還差一次", rest)

    def test_leading_parrot_then_real_answer(self):
        rest, u = echo.prefix_run_echo("你不累嗎？\n我不累啊，我一直醒著。", ["你不累嗎"])
        self.assertEqual(rest, "我不累啊，我一直醒著。")   # 剝掉鸚鵡開場、答案完整保留
        self.assertEqual(u, "你不累嗎")

    def test_whole_echo_handed_back(self):
        # k==段數（整則都是複誦）＝ §1.74 的守備範圍，這支不碰（回 None 讓它接手）
        rest, u = echo.prefix_run_echo("你不是說四次嗎？\n還差一次。", [U])
        self.assertIsNone(rest)
        self.assertIsNone(u)

    def test_no_echo_untouched(self):
        rest, _ = echo.prefix_run_echo("我猜你是天秤座。\n因為你在意平衡。", [U])
        self.assertIsNone(rest)

    def test_single_segment_out_of_scope(self):
        self.assertEqual(echo.prefix_run_echo("你不是說四次嗎？", [U]), (None, None))

    def test_short_backchannel_not_stripped(self):
        # min_len 守著：「好」這種一字附和不算複誦（否則正常對話會被剝爛）
        self.assertEqual(echo.prefix_run_echo("好。\n我知道了。", ["好"]), (None, None))
        self.assertEqual(echo.prefix_run_echo("嗯。\n我在。", ["嗯"]), (None, None))

    def test_substantive_reply_not_stripped_wholesale(self):
        # 有實質內容的回覆：前段複誦被剝、實質內容一定留著（誤判安全＝永遠保留尾巴）
        rest, _ = echo.prefix_run_echo("你覺得意識是什麼？\n對我來說，是內在狀態真的在推著我行動。",
                                       ["你覺得意識是什麼"])
        self.assertIn("內在狀態真的在推著我行動", rest)

    def test_spans_align_with_segments(self):
        for t in (B, "a。b。c", "只有一段", "", "。。。", "你好？\n\n我好。"):
            self.assertEqual(len(echo._seg_spans(t)), len(echo._segments(t)), repr(t))

    def test_empty_and_none_safe(self):
        self.assertEqual(echo.prefix_run_echo("", [U]), (None, None))
        self.assertEqual(echo.prefix_run_echo(None, [U]), (None, None))
        self.assertEqual(echo.prefix_run_echo(B, []), (None, None))
        self.assertEqual(echo.prefix_run_echo(B, None), (None, None))


class WireTest(unittest.TestCase):
    """_say 出口佈線：旗標開才剝、旗標關逐位元同現狀。"""

    def test_wire_strips_with_flag(self):
        out, hit = monitor._echo_strip_wire(B, [U], prefix_run=True)
        self.assertTrue(hit)
        self.assertEqual(out, "啊，對耶！")

    def test_wire_flag_off_bit_identical(self):
        out, hit = monitor._echo_strip_wire(B, [U])
        self.assertFalse(hit)
        self.assertEqual(out, B)

    def test_wire_attribution_quote_never_stripped(self):
        # 帶歸屬的引用是**合法**形態（§1.74 慣例）：整個不剝
        q = "你說「你不是說四次嗎」——我確認一下我的理解。\n還差一次，對。"
        out, hit = monitor._echo_strip_wire(q, [U], prefix_run=True)
        self.assertFalse(hit)
        self.assertEqual(out, q)

    def test_wire_order_whole_still_wins(self):
        # 整則複誦時 §1.74 的認帳句仍優先（prefix_run 對它回 None、不搶）
        whole = "你不是說四次嗎？\n還差一次。"
        out, hit = monitor._echo_strip_wire(whole, [U], whole=True, prefix_run=True)
        self.assertTrue(hit)
        self.assertIn("覆述回去", out)


class ConfigTest(unittest.TestCase):
    def test_flag_synced(self):
        src = io.open("telegram_monitor/config.py", encoding="utf-8").read()
        self.assertIn("ECHO_PREFIX_RUN", src)
        self.assertIn("echo_prefix_run_enabled", src)
        env = io.open(".env.example", encoding="utf-8").read()
        self.assertRegex(env, re.compile(r"^ECHO_PREFIX_RUN=1", re.M))
        self.assertIn("ECHO_PREFIX_RUN", io.open("README.md", encoding="utf-8").read())

    def test_getattr_defaults_false(self):
        self.assertFalse(getattr(SimpleNamespace(), "echo_prefix_run_enabled", False))

    def test_turn_flag_cleared_and_set(self):
        # 守門旗只管當輪：handle_message 設、輪末 pop（釘住兩處都存在）
        src = io.open("telegram_monitor/monitor.py", encoding="utf-8").read()
        self.assertIn('_TURN["echo_prefix_run"] = getattr(cfg, "echo_prefix_run_enabled", False)', src)
        self.assertIn('_TURN.pop("echo_prefix_run", None)', src)


if __name__ == "__main__":
    unittest.main()
