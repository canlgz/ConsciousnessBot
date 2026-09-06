"""🧵 §1.48 插話像人一樣接（INTERRUPT_COALESCE）：附和不打斷＋插話合批。

截圖根因（21:16–21:17）：bot 分串講解途中，使用者連丟三句附和「好久」「沒關係」「慢慢來」——
現行 _BurstInterrupt 把陳述也當即時插話（INTERRUPT_STATEMENT_ENABLED 預設開）、且 handle() **逐則**
巢狀回覆 → 「嗯。」「嗯。」「嗯，好。」洗版；接著 resume 橋「我繼續說喔，」把順暢的話硬切一刀。
真人講到一半看到對方點頭，不會停下來對每個點頭各回一句、再宣告要繼續說。

§1.48 兩件（一旗 INTERRUPT_COALESCE；config 預設 True／monitor 端 getattr 預設 False＝逐位元同現狀）：
A. 附和不打斷——整批插話都是 selfstate.is_backchannel（好久/沒關係/慢慢來/嗯/繼續說…全句錨、
   帶問號/嗎/呢/數字/指令不算）→ poll() defer（不消費、折進下一輪連發合併＝講完後一次溫和承接）：
   主體不中斷、零逐則「嗯。」、零橋接句。敵意短句不在表內＋is_hostile 雙保險（§1.14 收口優先）。
B. 插話合批——真插話（redirect/實質陳述）batch 先走既有連發合併純函式（group_bursts＋
   build_coalesced_update）把密集文字黏成**一則**再巢狀回覆＝一批一個回應；非純文字群照原逐則
   （貼圖訊號不丟）。全 stub、零網路。
"""

import re
import unittest
from types import SimpleNamespace
from unittest import mock

from telegram_monitor import monitor, selfstate

NOW_TS = 1_700_000_000


# ── 單元：selfstate.is_backchannel_interject（純函式、旗標無關）─────────────
class BackchannelTest(unittest.TestCase):
    def test_screenshot_three_and_common_hit(self):
        for t in ("好久", "沒關係", "慢慢來", "嗯", "嗯嗯", "好", "好的", "好喔", "ok", "OK",
                  "哈哈", "辛苦了", "繼續說", "你繼續", "我在聽", "收到", "懂了", "讚", "加油",
                  "慢慢來～", "好啦", "沒關係！", "是啊", "對啊", "了解"):
            self.assertTrue(selfstate.is_backchannel_interject(t), t)

    def test_questions_commands_content_not_backchannel(self):
        for t in ("真的嗎", "為什麼", "/status", "30分鐘後提醒我",
                  "我今天去了趟醫院", "說個故事來聽", "等你很久了欸怎麼還沒好", "", None):
            self.assertFalse(selfstate.is_backchannel_interject(t), repr(t))

    def test_hostile_only_shorts_not_in_table(self):
        # 純敵意短句（隨便/哼/算了/少來…）不在附和表＝不會被 defer 吃掉、照走 §1.14 收口
        for t in ("隨便", "隨便你", "哼", "算了", "少來"):
            self.assertFalse(selfstate.is_backchannel_interject(t), t)

    def test_ack_hostile_overlap_pinned(self):
        # 「是喔/呵呵」雙面詞：既有附和表收（單一入口沿用）**且** §1.14 敵意短句也收——
        # 消融定案：poll 端 is_hostile 守門先擋（敵意收口優先），本函式不另設黑名單（不複製詞表）。
        from telegram_monitor import reaction
        for t in ("是喔", "呵呵"):
            self.assertTrue(selfstate.is_backchannel_interject(t), t)
            self.assertTrue(reaction.is_hostile(t), t)


# ── 整合 harness（同 test_hostile_converge.SayHostileInterruptTest）─────────
class _SayBase(unittest.TestCase):
    def setUp(self):
        monitor._TURN["bubbles"] = None      # 隔離：_say 讀模組全域上限

    def _client(self, cfg, polls):
        """polls＝list of update 批（每次 get_updates 吐一批）。"""
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
        c._interrupt = monitor._BurstInterrupt(c, st, cfg, floor=0, handle_fn=lambda u: handled.append(u))
        c._handled = handled
        c._st = st
        return c

    def _cfg(self, coalesce=True, rewrite=False):
        base = dict(interrupt_rewrite_enabled=rewrite, interrupt_max_depth=2,
                    interrupt_statement_enabled=True, interrupt_continuation_defer=True,
                    telegram_chat_id="", hostile_converge_enabled=True)
        if coalesce is not None:                             # None＝欄位缺席（既有測試假 cfg 的樣子）
            base["interrupt_coalesce_enabled"] = coalesce
        return SimpleNamespace(**base)

    @staticmethod
    def _u(uid, text, ts=NOW_TS):
        return {"update_id": uid, "message": {"chat": {"id": 1}, "text": text, "date": ts}}

    def _say4(self, c):
        with mock.patch.object(monitor, "_sleep"):
            monitor._say(c, "第一串。第二串。第三串。第四串。")


# ── A. 附和不打斷 ───────────────────────────────────────────────────────────
class BackchannelDeferTest(_SayBase):
    def test_backchannel_defers_no_bridge_full_delivery(self):
        # 「慢慢來」＝點頭 → defer：不巢狀回、不橋接、主體四串全數講完（截圖的「嗯。」洗版＋硬橋雙殺）
        c = self._client(self._cfg(), [[self._u(99, "慢慢來")]])
        self._say4(c)
        self.assertEqual(c._handled, [])                     # 沒有逐則巢狀回覆（折進下一輪合併）
        self.assertNotIn("我繼續說喔，", c.sent)
        self.assertFalse(any(b in c.sent for b in monitor._RESUME_BRIDGES))
        self.assertIn("第四串。", c.sent)                    # 主體不中斷、講完
        self.assertEqual(c._st.tg_update_offset, 0)          # 不消費＝留給下一輪 _relate_coalesced

    def test_backchannel_batch_all_defer(self):
        c = self._client(self._cfg(), [[self._u(99, "好久"), self._u(100, "沒關係", NOW_TS + 1),
                                        self._u(101, "慢慢來", NOW_TS + 2)]])
        self._say4(c)
        self.assertEqual(c._handled, [])
        self.assertFalse(any(b in c.sent for b in monitor._RESUME_BRIDGES))
        self.assertIn("第四串。", c.sent)

    def test_flag_off_backchannel_bridges_as_head(self):
        # 【消融】旗標關/缺席＝同現狀：陳述插話 → 逐則巢狀回＋橋接（HEAD 位元）
        for coalesce in (False, None):
            c = self._client(self._cfg(coalesce=coalesce), [[self._u(99, "慢慢來")]])
            self._say4(c)
            self.assertTrue(c._handled, str(coalesce))
            self.assertTrue(any(b in c.sent for b in monitor._RESUME_BRIDGES), str(coalesce))
            self.assertIn("第四串。", c.sent, str(coalesce))

    def test_redirect_still_interrupts_with_flag_on(self):
        # 真的想打斷改問（「結果呢」）→ 照舊：優先回應＋橋接續送（§1.48 只動附和）
        c = self._client(self._cfg(), [[self._u(99, "結果呢")]])
        self._say4(c)
        self.assertTrue(c._handled)
        self.assertTrue(any(b in c.sent for b in monitor._RESUME_BRIDGES))
        self.assertIn("第四串。", c.sent)

    def test_hostile_still_breaks_with_flag_on(self):
        # 敵意插話 → 照舊 §1.14 收口（不橋接、不續講）；「你不要敷衍我」不在附和表＝不會被 defer 吃掉
        c = self._client(self._cfg(), [[self._u(99, "你不要敷衍我")]])
        self._say4(c)
        self.assertTrue(c._handled)
        self.assertFalse(any(b in c.sent for b in monitor._RESUME_BRIDGES))
        self.assertNotIn("第二串。", c.sent)

    def test_ack_hostile_overlap_breaks_not_defers(self):
        # 雙面詞「是喔」（附和表∩敵意短句）：is_hostile 守門先擋 defer → 照走 §1.14 收口＝敵意優先
        c = self._client(self._cfg(), [[self._u(99, "是喔")]])
        self._say4(c)
        self.assertTrue(c._handled)                          # 沒被 defer 吃掉
        self.assertFalse(any(b in c.sent for b in monitor._RESUME_BRIDGES))
        self.assertNotIn("第二串。", c.sent)                 # §1.14：不續講、把話頭讓給對方


# ── B. 插話合批 ─────────────────────────────────────────────────────────────
class InterruptCoalesceTest(_SayBase):
    STATEMENTS = [("我今天去了趟醫院", NOW_TS), ("拿了報告，醫生說沒事", NOW_TS + 2)]

    def _batch(self):
        return [[self._u(99 + i, t, ts) for i, (t, ts) in enumerate(self.STATEMENTS)]]

    def test_statement_batch_coalesced_single_nested(self):
        # 密集兩句實質陳述 → 合成**一則**（換行接合）巢狀回覆一次；之後照舊橋接續送
        c = self._client(self._cfg(), self._batch())
        self._say4(c)
        self.assertEqual(len(c._handled), 1)
        self.assertEqual(c._handled[0]["message"]["text"], "我今天去了趟醫院\n拿了報告，醫生說沒事")
        self.assertTrue(any(b in c.sent for b in monitor._RESUME_BRIDGES))
        self.assertIn("第四串。", c.sent)
        self.assertEqual(c._st.tg_update_offset, 101)        # 兩則都已消費（offset 推進到最大 id+1）

    def test_flag_off_two_nested_bitwise_head(self):
        c = self._client(self._cfg(coalesce=False), self._batch())
        self._say4(c)
        self.assertEqual(len(c._handled), 2)                 # 【消融】同現狀：逐則巢狀回覆

    def test_sparse_statements_not_merged(self):
        # 相鄰 gap ≥ 預設 2.5s 門檻 → 不硬黏（照連發合併規則斷群、逐群回）
        polls = [[self._u(99, "我今天去了趟醫院", NOW_TS), self._u(100, "拿了報告，醫生說沒事", NOW_TS + 60)]]
        c = self._client(self._cfg(), polls)
        self._say4(c)
        self.assertEqual(len(c._handled), 2)


# ── 同步 ───────────────────────────────────────────────────────────────────
class ConfigTest(unittest.TestCase):
    def test_config_synced(self):
        src = open("telegram_monitor/config.py", encoding="utf-8").read()
        self.assertIn("INTERRUPT_COALESCE", src)
        self.assertIn("interrupt_coalesce_enabled", src)
        env = open(".env.example", encoding="utf-8").read()
        self.assertRegex(env, re.compile(r"^INTERRUPT_COALESCE=1", re.M))
        self.assertIn("INTERRUPT_COALESCE", open("README.md", encoding="utf-8").read())

    def test_getattr_defaults_false(self):
        self.assertFalse(getattr(SimpleNamespace(), "interrupt_coalesce_enabled", False))


if __name__ == "__main__":
    unittest.main()
