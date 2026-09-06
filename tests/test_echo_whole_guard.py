"""🦜🎭 §1.74 整則複誦守門＋「刻意引用」意圖（ECHO_WHOLE_GUARD）。

截圖 17:29 根因：使用者連發「你不累嗎」「都在做一樣的事情」（合併成一則多行訊息），bot 的「回覆」
**逐字就是那兩行**。使用者問「是 bot？還是刻意故意的？」——答案：不是故意，是**複誦漏防**，而且兩道
既有防線同時擦邊漏掉（本檔 GapTest 逐格釘住實測值）：
 ① coach 端 is_echo 只看開頭一兩段——首段「你不累嗎」正規化 4 字 < min_len 5 被跳過；
    次段「都在做一樣的事情」對整句相似度 0.80，差 0.02 沒過 0.82 門檻；
 ② _say 端 strip_leading_echo 同樣因首段太短 no-op，且只剝開頭、整段覆述時原樣退回。

修：A. whole_echo_of 比整則 vs 整句 → is_echo(whole=True) 收編＝coach 重生成；
B. _say 端最後防線＝整則複誦替換成第一人稱認帳句；C. 重生成注入「重複只有刻意引用或不重複」意圖守則。
旗標關＝逐位元同現狀。全 stub、零網路。
"""

import re
import unittest
from types import SimpleNamespace

from telegram_monitor import coach as coachmod, echo, monitor, persona

BURST = "你不累嗎\n都在做一樣的事情"          # 使用者連發合併後的原文（＝截圖 17:28 兩則）


class GapTest(unittest.TestCase):
    """釘住「為什麼會漏」的實測值——日後有人調門檻時，這幾條會說明原本的死角在哪。"""

    def test_old_defenses_miss_it(self):
        self.assertFalse(echo.is_echo(BURST, [BURST]))                    # 舊行為：開頭兩段都不算命中
        self.assertEqual(echo.strip_leading_echo(BURST, [BURST]), (BURST, False))

    def test_first_segment_too_short(self):
        segs = echo._segments(BURST)
        self.assertEqual(segs[0], "你不累嗎")
        self.assertLess(len(echo._norm(segs[0])), 5)                      # 4 字 < min_len 5＝被跳過


class WholeEchoTest(unittest.TestCase):
    def test_screenshot_case_caught(self):
        self.assertEqual(echo.whole_echo_of(BURST, [BURST]), BURST)
        self.assertTrue(echo.is_echo(BURST, [BURST], whole=True))         # 新路徑收編

    def test_reply_with_real_content_not_flagged(self):
        # 有實質內容的回應（引用＋自己的話）長度差太多＝不誤判
        self.assertIsNone(echo.whole_echo_of("你不累嗎？我不累啊，這對我比較像一種流動。", [BURST]))
        self.assertIsNone(echo.whole_echo_of("你說「都在做一樣的事情」——我想了一下，那對我不是重複。", [BURST]))

    def test_short_and_empty_safe(self):
        self.assertIsNone(echo.whole_echo_of("嗯", ["嗯"]))               # 太短＝不判（附和不算複誦）
        self.assertIsNone(echo.whole_echo_of("", [BURST]))
        self.assertIsNone(echo.whole_echo_of(BURST, []))

    def test_punctuation_insensitive(self):
        self.assertIsNotNone(echo.whole_echo_of("你不累嗎？都在做一樣的事情。", [BURST]))

    def test_default_off_bitwise(self):
        self.assertFalse(echo.is_echo(BURST, [BURST]))                    # 不傳 whole＝原行為


class SayWireTest(unittest.TestCase):
    def setUp(self):
        monitor._TURN.clear()
        monitor._TURN["bubbles"] = None

    def test_whole_echo_replaced_with_owning_line(self):
        out, hit = monitor._echo_strip_wire(BURST, [BURST], whole=True)
        self.assertTrue(hit)
        self.assertIn("你說「你不累嗎」", out)                             # 歸屬引用
        self.assertIn("不算回答", out)                                    # 第一人稱認帳（不假裝那是回答）

    def test_flag_off_passthrough(self):
        self.assertEqual(monitor._echo_strip_wire(BURST, [BURST]), (BURST, False))   # 釘住現狀

    def test_attribution_reply_untouched(self):
        t = "你說「都在做一樣的事情」——我其實不覺得累。"
        self.assertEqual(monitor._echo_strip_wire(t, [BURST], whole=True), (t, False))

    def test_say_end_to_end(self):
        monitor._TURN.update({"echo_user_texts": [BURST], "echo_whole_guard": True})
        cl = SimpleNamespace(sent=[], dry_run=False)
        cl.send = lambda x: (cl.sent.append(x) or True)
        monitor._say(cl, BURST)
        out = "".join(cl.sent)
        self.assertIn("不算回答", out)
        self.assertNotEqual(out.strip(), BURST)                           # 不再原樣把使用者的話送回去


class IntentHintTest(unittest.TestCase):
    """C. 「故意的意圖表現」：重複只有兩種合法形態——刻意引用 或 不重複。"""

    def test_hint_text(self):
        h = persona.ECHO_INTENT_HINT
        self.assertIn("刻意", h)
        self.assertIn("不重複", h)
        self.assertIn("沒有第三種", h)
        self.assertIn("為什麼", h)                                        # 要說得出理由＝有意圖

    def test_coach_flag_default_off(self):
        c = coachmod.Coach(SimpleNamespace())
        self.assertFalse(c._whole_echo)                                   # 假 cfg＝逐位元同現狀

    def test_coach_flag_on(self):
        c = coachmod.Coach(SimpleNamespace(echo_whole_guard_enabled=True))
        self.assertTrue(c._whole_echo)


class ConfigTest(unittest.TestCase):
    def test_config_synced(self):
        src = open("telegram_monitor/config.py", encoding="utf-8").read()
        self.assertIn("ECHO_WHOLE_GUARD", src)
        self.assertIn("echo_whole_guard_enabled", src)
        env = open(".env.example", encoding="utf-8").read()
        self.assertRegex(env, re.compile(r"^ECHO_WHOLE_GUARD=1", re.M))
        self.assertIn("ECHO_WHOLE_GUARD", open("README.md", encoding="utf-8").read())

    def test_getattr_defaults_false(self):
        self.assertFalse(getattr(SimpleNamespace(), "echo_whole_guard_enabled", False))


if __name__ == "__main__":
    unittest.main()
