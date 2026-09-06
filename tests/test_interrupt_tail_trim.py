"""🧵 §1.49 插話後殘句取捨（INTERRUPT_TAIL_TRIM）：道別即收＋殘句去重。

截圖根因（23:00）：使用者「晚安 我累了」＋「希望你能遵守承諾」→ bot 回覆途中被第二句（實質陳述、非附和
＝§1.48 不 defer）插話 → 巢狀回「我會記得的。」後照舊橋接「嗯，我接著說，」再把殘句講完——結果：
①**道別輪**還宣告要繼續說、續講客套尾巴（「嗯，好好休息。」）＝人不會這樣收晚安；
②殘句「我會記得的。」和巢狀輪剛說過的**一字不差重播**＝像沒在聽自己講話。

§1.49 兩件（一旗 INTERRUPT_TAIL_TRIM；config 預設 True／monitor 端 getattr 預設 False＝逐位元同現狀）：
A. 道別即收——這輪在回道別（_dispatch_one 把 is_farewell 判定 stash 在 interrupt 物件上；_TURN 會被
   巢狀輪開頭清掉、不能放那）**或**插話本身是道別（_pending_farewell）→ 答完插話直接收口：不橋接、
   不續殘句（§1.14 敵意收口的道別版）。
B. 殘句去重——殘句若已在「這輪已送出的串」或「近幾則 model 回覆」（含剛才巢狀輪送出的，_remember 已入
   convo_history）出現過（≥5 字、子串/超串）＝跳過不重播；殘句**全**被去掉＝連橋都不送（沒內容就別宣告
   要繼續）。真插話的橋接/rewrite/wrap 行為（有實質新內容時）不動。全 stub、零網路。
"""

import re
import unittest
from types import SimpleNamespace
from unittest import mock

from telegram_monitor import monitor, selfstate

NOW_TS = 1_700_000_000


# ── 單元：_pending_farewell／is_farewell ────────────────────────────────────
class PendingFarewellTest(unittest.TestCase):
    def _cfg(self, chat=""):
        return SimpleNamespace(telegram_chat_id=chat)

    @staticmethod
    def _u(text, chat_id=1):
        return {"update_id": 1, "message": {"chat": {"id": chat_id}, "text": text}}

    def test_screenshot_farewells_hit(self):
        self.assertTrue(selfstate.is_farewell("晚安 我累了"))          # 截圖 23:00 原句
        self.assertTrue(monitor._pending_farewell([self._u("晚安我先睡了")], self._cfg()))
        self.assertTrue(monitor._pending_farewell([self._u("先這樣，掰掰")], self._cfg()))

    def test_non_farewell_and_foreign_false(self):
        self.assertFalse(monitor._pending_farewell([self._u("希望你能遵守承諾")], self._cfg()))
        self.assertFalse(monitor._pending_farewell([self._u("晚安")], self._cfg(chat="9")))   # 非 owner chat
        self.assertFalse(monitor._pending_farewell([], self._cfg()))
        self.assertFalse(monitor._pending_farewell(None, self._cfg()))


# ── 整合 harness（同 test_interrupt_coalesce）──────────────────────────────
class _SayBase(unittest.TestCase):
    def setUp(self):
        monitor._TURN["bubbles"] = None

    def _client(self, cfg, polls, closing=False, history=None):
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
        st = SimpleNamespace(tg_update_offset=0, convo_history=list(history or []))
        c._interrupt = monitor._BurstInterrupt(c, st, cfg, floor=0, handle_fn=lambda u: handled.append(u))
        if closing:
            c._interrupt.closing_turn = True     # 產線由 _dispatch_one 依 is_farewell(這輪訊息) stash
        c._handled = handled
        return c

    def _cfg(self, trim=True):
        base = dict(interrupt_rewrite_enabled=False, interrupt_max_depth=2,
                    interrupt_statement_enabled=True, interrupt_continuation_defer=True,
                    telegram_chat_id="", hostile_converge_enabled=True)
        if trim is not None:
            base["interrupt_tail_trim_enabled"] = trim
        return SimpleNamespace(**base)

    @staticmethod
    def _u(uid, text):
        return {"update_id": uid, "message": {"chat": {"id": 1}, "text": text, "date": NOW_TS}}

    def _say(self, c, text="第一串。第二串。第三串。第四串。"):
        with mock.patch.object(monitor, "_sleep"):
            monitor._say(c, text)


# ── A. 道別即收 ─────────────────────────────────────────────────────────────
class ClosingTurnTest(_SayBase):
    def test_closing_turn_breaks_no_bridge_no_tail(self):
        # 這輪在回「晚安 我累了」（closing stash）→ 插話答完就收口：不橋接、不續客套尾巴
        c = self._client(self._cfg(), [[self._u(99, "希望你能遵守承諾")]], closing=True)
        self._say(c)
        self.assertTrue(c._handled)                          # 插話本身有被優先回應
        self.assertFalse(any(b in c.sent for b in monitor._RESUME_BRIDGES))
        self.assertNotIn("第二串。", c.sent)

    def test_farewell_interject_breaks(self):
        # 插話本身是道別（「晚安我先睡了」）→ 同收口（對方都要走了還「我接著說」＝不像人）
        c = self._client(self._cfg(), [[self._u(99, "晚安我先睡了")]])
        self._say(c)
        self.assertTrue(c._handled)
        self.assertFalse(any(b in c.sent for b in monitor._RESUME_BRIDGES))
        self.assertNotIn("第二串。", c.sent)

    def test_flag_off_closing_bridges_as_head(self):
        # 【消融】旗標關/缺席＝同現狀：道別輪插話照樣橋接續送
        for trim in (False, None):
            c = self._client(self._cfg(trim=trim), [[self._u(99, "希望你能遵守承諾")]], closing=True)
            self._say(c)
            self.assertTrue(any(b in c.sent for b in monitor._RESUME_BRIDGES), str(trim))
            self.assertIn("第四串。", c.sent, str(trim))


# ── B. 殘句去重 ─────────────────────────────────────────────────────────────
class TailDedupTest(_SayBase):
    HIST = [{"role": "model", "text": "我會記得的。", "ts": NOW_TS}]   # 剛才巢狀輪說過（_remember 入史）

    def test_dup_tail_skipped_rest_continues(self):
        # 殘句「我會記得的。」剛說過 → 跳過；還有新內容（好好休息喔）→ 橋接照送、新內容照講
        c = self._client(self._cfg(), [[self._u(99, "希望你能遵守承諾")]], history=self.HIST)
        self._say(c, "晚安。我會記得的。好好休息喔。")
        self.assertNotIn("我會記得的。", c.sent)             # 不一字不差重播（截圖 23:00 ×2 根因）
        self.assertIn("好好休息喔。", c.sent)                # 真正的新內容不丟
        self.assertTrue(any(b in c.sent for b in monitor._RESUME_BRIDGES))

    def test_all_dup_no_bridge_clean_close(self):
        # 殘句全是重複 → 連橋都不送（沒內容就別宣告「我接著說」）
        c = self._client(self._cfg(), [[self._u(99, "希望你能遵守承諾")]], history=self.HIST)
        self._say(c, "晚安。我會記得的。")
        self.assertEqual(c.sent, ["晚安。"])

    def test_flag_off_dup_replayed_bitwise(self):
        # 【消融】同現狀：重複殘句照樣重播＋橋接
        c = self._client(self._cfg(trim=False), [[self._u(99, "希望你能遵守承諾")]], history=self.HIST)
        self._say(c, "晚安。我會記得的。")
        self.assertIn("我會記得的。", c.sent)
        self.assertTrue(any(b in c.sent for b in monitor._RESUME_BRIDGES))

    def test_no_dup_no_history_unchanged(self):
        # 沒有重複＝照舊：橋接＋殘句全送（§1.49 只剪重複與道別，不動真插話的接續本體）
        c = self._client(self._cfg(), [[self._u(99, "希望你能遵守承諾")]])
        self._say(c)
        self.assertTrue(any(b in c.sent for b in monitor._RESUME_BRIDGES))
        self.assertIn("第四串。", c.sent)

    def test_short_tail_not_dedup(self):
        # <5 字的殘句（嗯，好。）不做子串判重（太短誤殺率高）——保守側
        hist = [{"role": "model", "text": "好。", "ts": NOW_TS}]
        c = self._client(self._cfg(), [[self._u(99, "希望你能遵守承諾")]], history=hist)
        self._say(c, "第一串。好。")
        self.assertIn("好。", c.sent)


# ── 同步 ───────────────────────────────────────────────────────────────────
class ConfigTest(unittest.TestCase):
    def test_config_synced(self):
        src = open("telegram_monitor/config.py", encoding="utf-8").read()
        self.assertIn("INTERRUPT_TAIL_TRIM", src)
        self.assertIn("interrupt_tail_trim_enabled", src)
        env = open(".env.example", encoding="utf-8").read()
        self.assertRegex(env, re.compile(r"^INTERRUPT_TAIL_TRIM=1", re.M))
        self.assertIn("INTERRUPT_TAIL_TRIM", open("README.md", encoding="utf-8").read())

    def test_getattr_defaults_false(self):
        self.assertFalse(getattr(SimpleNamespace(), "interrupt_tail_trim_enabled", False))


if __name__ == "__main__":
    unittest.main()
