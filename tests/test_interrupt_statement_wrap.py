"""🧵 §1.50 陳述插話後不硬接尾巴（INTERRUPT_STATEMENT_WRAP）：殘句 wrap 濃縮融進插話後的語境。

截圖根因（23:21，新碼已上線後實測）：bot 回覆途中被「自己打臉自己」（陳述指控）插話 → 巢狀回完後，
remaining=1 落 decide_resume 規則②（剩 ≤1 → resume）→ 送罐頭橋「對了，剛剛那條還沒說完——」＋把插話
**前**就切好的尾巴「我剛剛說完了耶。」原樣照播——使用者才剛說「你剛剛沒說完」，bot 先複讀指控再自打臉。
問題本質＝使用者兩輪前就點出的「通盤判斷」：陳述插話帶進新資訊後，預切殘句常已不合時宜，原樣接回
（哪怕只剩一串）＝沒在聽。

§1.50（一旗；config 預設 True／monitor 端 getattr 預設 False＝逐位元同現狀）：decide_resume 新尾參
statement_wrap——**陳述**插話且主體已送過（sent>0）→ 一律 wrap：走既有 INTERRUPT_WRAP_CONDENSE 濃縮
（voice_wrap_condense 看得到巢狀輪後的 convo_history＝殘句融進**插話後**的語境）、無教練退模板淡收；
不再送「還沒說完」類的橋。redirect（問句）照舊 resume＝答完問題接回主線是人之常情；sent==0 保底 resume
（主體至少講一次）；§1.14 敵意收口優先不變。全 stub、零網路。
"""

import re
import unittest
from types import SimpleNamespace
from unittest import mock

from telegram_monitor import monitor

NOW_TS = 1_700_000_000


# ── 單元：decide_resume 新尾參 ──────────────────────────────────────────────
class DecideResumeWrapTest(unittest.TestCase):
    def test_statement_wrap_true_statement_wraps_even_short_tail(self):
        # 截圖 case：剩 1 串、主體已送過、陳述插話 → wrap（HEAD＝resume）
        self.assertEqual(monitor.decide_resume(1, 1, "statement", 1, 2, statement_wrap=True), "wrap")
        self.assertEqual(monitor.decide_resume(3, 1, "statement", 1, 2, statement_wrap=True), "wrap")

    def test_redirect_unchanged(self):
        # 問句插話照舊：剩 ≤1 → 說完（答完問題接回主線是人之常情）
        self.assertEqual(monitor.decide_resume(1, 1, "redirect", 1, 2, statement_wrap=True), "resume")
        self.assertEqual(monitor.decide_resume(1, 3, "redirect", 1, 2, statement_wrap=True), "resume")

    def test_body_floor_protected(self):
        # 主體一串都沒送出 → 保底 resume（至少把主體講出一次）
        self.assertEqual(monitor.decide_resume(4, 0, "statement", 1, 2, statement_wrap=True), "resume")

    def test_default_kwarg_bitwise_head(self):
        # 尾參缺席＝HEAD 位元（釘住截圖的原行為：剩 1 陳述 → resume）
        self.assertEqual(monitor.decide_resume(1, 1, "statement", 1, 2), "resume")
        self.assertEqual(monitor.decide_resume(4, 2, "statement", 1, 2), "wrap")


# ── 整合：_say wrap 路徑 ───────────────────────────────────────────────────
class _SayBase(unittest.TestCase):
    def setUp(self):
        monitor._TURN["bubbles"] = None

    def _client(self, cfg, polls, coach=None):
        sent, handled = [], []

        class C:
            dry_run = False

            def __init__(s):
                s.sent = sent

            def send(s, t):
                sent.append(t)
                return True

            def send_typing(s):
                pass

            def get_updates(s, offset=0, timeout=0):
                return polls.pop(0) if polls else []
        c = C()
        st = SimpleNamespace(tg_update_offset=0)
        c._interrupt = monitor._BurstInterrupt(c, st, cfg, floor=0,
                                               handle_fn=lambda u: handled.append(u), coach=coach)
        c._handled = handled
        return c

    def _cfg(self, wrap_flag=True, rewrite=True):
        base = dict(interrupt_rewrite_enabled=rewrite, interrupt_max_depth=2,
                    interrupt_statement_enabled=True, interrupt_continuation_defer=True,
                    interrupt_wrap_condense_enabled=True,
                    telegram_chat_id="", hostile_converge_enabled=True)
        if wrap_flag is not None:
            base["interrupt_statement_wrap_enabled"] = wrap_flag
        return SimpleNamespace(**base)

    @staticmethod
    def _u(uid, text):
        return {"update_id": uid, "message": {"chat": {"id": 1}, "text": text, "date": NOW_TS}}

    COACH = SimpleNamespace(enabled=True,
                            voice_wrap_condense=lambda rt, h, natural=False: "那句其實已經收在剛剛的話裡了。")

    def _say2(self, c):
        with mock.patch.object(monitor, "_sleep"):
            monitor._say(c, "第一串。第二串。")


class StatementWrapIntegrationTest(_SayBase):
    def test_statement_tail_condensed_no_bridge_no_verbatim(self):
        # 截圖 case 重演：陳述插話後剩 1 串 → 不橋接、不原樣照播，殘句濃縮融進插話後語境
        c = self._client(self._cfg(), [[self._u(99, "自己打臉自己")]], coach=self.COACH)
        self._say2(c)
        self.assertTrue(c._handled)                          # 插話本身有被優先回應
        self.assertFalse(any(b in c.sent for b in monitor._RESUME_BRIDGES))
        self.assertNotIn("第二串。", c.sent)                 # 預切尾巴不原樣照播
        self.assertIn("那句其實已經收在剛剛的話裡了。", c.sent)

    def test_no_coach_falls_back_template_close(self):
        # 無教練/濃縮失敗 → 模板淡收（仍不橋接、不照播尾巴）
        c = self._client(self._cfg(), [[self._u(99, "自己打臉自己")]], coach=None)
        self._say2(c)
        self.assertFalse(any(b in c.sent for b in monitor._RESUME_BRIDGES))
        self.assertNotIn("第二串。", c.sent)
        self.assertTrue(any(x in c.sent for x in monitor._REWRITE_CLOSE_LINES))

    def test_flag_off_resumes_bitwise_head(self):
        # 【消融】旗標關/缺席＝HEAD：剩 1 → resume（橋接＋尾巴原樣照播）＝截圖原行為
        for flag in (False, None):
            c = self._client(self._cfg(wrap_flag=flag), [[self._u(99, "自己打臉自己")]], coach=self.COACH)
            self._say2(c)
            self.assertTrue(any(b in c.sent for b in monitor._RESUME_BRIDGES), str(flag))
            self.assertIn("第二串。", c.sent, str(flag))

    def test_redirect_still_resumes_with_flag_on(self):
        # 問句插話（結果呢）→ 照舊橋接＋說完（§1.50 只動陳述）
        c = self._client(self._cfg(), [[self._u(99, "結果呢")]], coach=self.COACH)
        self._say2(c)
        self.assertTrue(any(b in c.sent for b in monitor._RESUME_BRIDGES))
        self.assertIn("第二串。", c.sent)

    def test_hostile_precedence_unchanged(self):
        # 敵意插話 → §1.14 直接收口（不 wrap 不濃縮不淡收）——收口優先序不被 §1.50 動到
        c = self._client(self._cfg(), [[self._u(99, "你不要敷衍我")]], coach=self.COACH)
        self._say2(c)
        self.assertFalse(any(b in c.sent for b in monitor._RESUME_BRIDGES))
        self.assertFalse(any(x in c.sent for x in monitor._REWRITE_CLOSE_LINES))
        self.assertNotIn("第二串。", c.sent)
        self.assertNotIn("那句其實已經收在剛剛的話裡了。", c.sent)


# ── 同步 ───────────────────────────────────────────────────────────────────
class ConfigTest(unittest.TestCase):
    def test_config_synced(self):
        src = open("telegram_monitor/config.py", encoding="utf-8").read()
        self.assertIn("INTERRUPT_STATEMENT_WRAP", src)
        self.assertIn("interrupt_statement_wrap_enabled", src)
        env = open(".env.example", encoding="utf-8").read()
        self.assertRegex(env, re.compile(r"^INTERRUPT_STATEMENT_WRAP=1", re.M))
        self.assertIn("INTERRUPT_STATEMENT_WRAP", open("README.md", encoding="utf-8").read())

    def test_getattr_defaults_false(self):
        self.assertFalse(getattr(SimpleNamespace(), "interrupt_statement_wrap_enabled", False))


if __name__ == "__main__":
    unittest.main()
