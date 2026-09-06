"""🔁 §1.71 重播守門（REPLY_REPLAY_GUARD）：剛送過的段落不准原樣再送——跨輪、跨路徑的送出層最後防線。

截圖根因：20:03-20:04 同三個泡泡（「欸，你這樣說我有點冤枉啦！」「我上一句是約八小時前…」
「之後我也有主動回應你幾次…」）在一兩分鐘內被**逐字重播**兩次，中間還夾貼圖與「我繼續說喔，」
接回橋＝節奏斷裂（使用者：「這回應的節奏與順暢度不夠連貫性！！」）。§1.41/§1.49/§1.54 的去重
各自只管單一路徑內部——插話/接回機制跨路徑重播到送出層時沒有任何防線。

修法：_replay_note 恆記「近 3 分鐘內真的送出過的泡泡」（正規化、≥10 字）；_say 送出前
_replay_filter 全等比對：命中＝剝掉；全剝空＝換一句誠實短句（不無聲、也不重播）。
旗標關＝不 arm＝逐位元同現狀。全 stub、零網路。
"""

import re
import unittest
from types import SimpleNamespace

from telegram_monitor import monitor

LINE_A = "我上一句是約八小時前跟你說話的，那時候我才剛從小睡中醒來。"
LINE_B = "之後我也有主動回應你幾次，還特別跟你說了我心裡有個小小的疑問。"
FRESH = "這次我想補充一點新的想法，關於今天早上的事。"


class Cl:
    def __init__(self):
        self.sent, self.dry_run = [], False

    def send(self, t):
        self.sent.append(t)
        return True


class FilterUnitTest(unittest.TestCase):
    def setUp(self):
        monitor._SENT_RECENT.clear()

    def test_dup_within_window_stripped(self):
        monitor._replay_note(LINE_A, now_ts=1000.0)
        kept, hit = monitor._replay_filter([LINE_A, FRESH], now_ts=1060.0)
        self.assertTrue(hit)
        self.assertEqual(kept, [FRESH])                         # 重播句剝掉、新內容保留

    def test_old_dup_allowed(self):
        monitor._replay_note(LINE_A, now_ts=1000.0)
        kept, hit = monitor._replay_filter([LINE_A], now_ts=1000.0 + 300)
        self.assertFalse(hit)                                   # 超過 3 分鐘窗＝不算重播
        self.assertEqual(kept, [LINE_A])

    def test_short_bubbles_exempt(self):
        monitor._replay_note("嗯，好。", now_ts=1000.0)          # <10 字＝不記
        kept, hit = monitor._replay_filter(["嗯，好。"], now_ts=1010.0)
        self.assertFalse(hit)                                   # 合理的短重複（嗯/好）不受影響

    def test_no_dup_untouched(self):
        kept, hit = monitor._replay_filter([FRESH], now_ts=1000.0)
        self.assertEqual((kept, hit), ([FRESH], False))


class SayWireTest(unittest.TestCase):
    def setUp(self):
        monitor._SENT_RECENT.clear()
        monitor._TURN.clear()
        monitor._TURN["bubbles"] = None

    def _say(self, text, armed=True):
        monitor._TURN["replay_guard"] = armed
        cl = Cl()
        monitor._say(cl, text)
        return cl.sent

    def test_verbatim_resend_blocked(self):
        first = self._say(LINE_A + LINE_B)
        self.assertEqual(len(first), 2)                         # 兩句照送＋已記錄
        second = self._say(LINE_A + LINE_B)                     # 截圖症狀：同段落原樣再送
        self.assertEqual(len(second), 1)
        self.assertIn("剛剛才說過一次", second[0])               # 全剝空＝誠實短句、不無聲也不重播
        self.assertNotIn("八小時前", second[0])

    def test_partial_dup_keeps_fresh(self):
        self._say(LINE_A)
        out = self._say(LINE_A + FRESH)
        self.assertEqual(len(out), 1)
        self.assertIn("新的想法", out[0])                       # 只剝重播句、新內容照送
        self.assertNotIn("八小時前", out[0])

    def test_flag_off_bitwise_resend(self):
        self._say(LINE_A, armed=True)                           # 記錄恆開
        out = self._say(LINE_A, armed=False)                    # 未 arm＝現狀：原樣重播（釘住 bug 行為）
        self.assertEqual(len(out), 1)
        self.assertIn("八小時前", out[0])

    def test_proactive_prefix_glyph_ignored_in_norm(self):
        monitor._TURN["replay_guard"] = True
        cl = Cl()
        monitor._say(cl, LINE_A, prefix="🤝 ", state=SimpleNamespace(convo_history=[]))
        out = self._say(LINE_A)                                 # 之後互動輪原樣再講＝照樣被攔（前綴剝掉比對）
        self.assertIn("剛剛才說過一次", out[0])


class ConfigTest(unittest.TestCase):
    def test_config_synced(self):
        src = open("telegram_monitor/config.py", encoding="utf-8").read()
        self.assertIn("REPLY_REPLAY_GUARD", src)
        self.assertIn("reply_replay_guard_enabled", src)
        env = open(".env.example", encoding="utf-8").read()
        self.assertRegex(env, re.compile(r"^REPLY_REPLAY_GUARD=1", re.M))
        self.assertIn("REPLY_REPLAY_GUARD", open("README.md", encoding="utf-8").read())

    def test_getattr_defaults_false(self):
        self.assertFalse(getattr(SimpleNamespace(), "reply_replay_guard_enabled", False))


if __name__ == "__main__":
    unittest.main()
