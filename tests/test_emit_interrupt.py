"""🗣️ 問題A：插話對**任何 bot 回應**生效（不只互動回覆）——自發出聲相（feel/adapt/act 的 💡🫧🫀🌀🍃… emit）
分串送出途中被「想打斷改問」插話，也先優先回應再橋接接回。

守護不變式（吸收對抗式審查 high/med）：
- 自發相 _BurstInterrupt(statement_defer=True)：只認真 redirect 插話、**陳述續打一律 defer 不消費**（不理會
  INTERRUPT_STATEMENT_ENABLED），留給下一圈 _relate_coalesced 合併（不破連發合併契約）。
- rewrite 在自發相**強制關**（背景自陳不被暖收截斷成半截）。
- 無插話＝逐位元同現狀；無 get_updates 能力（FakeClient）＝poll()→None＝零迴歸。
- EMIT_INTERRUPT_ENABLED=0 關＝相位層不掛 client._interrupt＝同現狀。
"""

import os
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

from telegram_monitor import monitor, lifeloop
from telegram_monitor.state import State

NOW = datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc)


def _msg(uid, text):
    return {"update_id": uid, "message": {"chat": {"id": 1}, "text": text}}


class EmitSayInterruptTest(unittest.TestCase):
    """直接驗 _say + statement_defer=True 的 _BurstInterrupt（自發相用的那種）：
    redirect 插話即時優先回應；陳述續打 defer 不消費（即使 INTERRUPT_STATEMENT_ENABLED 開）。"""

    def setUp(self):
        monitor._TURN["bubbles"] = None        # 隔離：別讓殘留串數上限把多串併少

    def _client(self, polls):
        sent, handled = [], []

        class C:
            dry_run = False
            def send(self, t): sent.append(t); return True
            def send_typing(self): pass
            def get_updates(self, offset=0, timeout=0): return polls.pop(0) if polls else []
        c = C()
        c.sent, c.handled = sent, handled
        return c

    def _attach(self, c, cfg, floor=0, offset=0):
        st = SimpleNamespace(tg_update_offset=offset)
        c._interrupt = monitor._BurstInterrupt(
            c, st, cfg, floor=floor, handle_fn=lambda u: c.handled.append(u["message"]["text"]),
            statement_defer=True)
        c._state = st
        return c

    def test_emit_redirect_interrupt_priority_then_resume(self):
        # 自發相分串送出途中收到 redirect（問句）→ 先優先回應、再橋接接回繼續說
        cfg = SimpleNamespace(telegram_chat_id="", interrupt_statement_enabled=True,
                              interrupt_rewrite_enabled=True, interrupt_max_depth=2)
        c = self._attach(self._client([[_msg(11, "等等，這是什麼意思？")]]), cfg, floor=10, offset=10)
        with mock.patch.object(monitor, "_sleep"):
            monitor._say(c, "第一串。第二串。第三串。第四串。")
        self.assertEqual(c.handled, ["等等，這是什麼意思？"])         # 優先回應了插話
        self.assertTrue(any(s in monitor._RESUME_BRIDGES for s in c.sent))  # 有橋接接回
        self.assertEqual(c._state.tg_update_offset, 12)              # redirect 被消費、offset 前進

    def test_emit_statement_deferred_even_when_global_flag_on(self):
        # 自發相：陳述續打即使 INTERRUPT_STATEMENT_ENABLED 開也 **不**消費（留給下一圈合併）＝守連發合併契約
        cfg = SimpleNamespace(telegram_chat_id="", interrupt_statement_enabled=True,
                              interrupt_rewrite_enabled=True, interrupt_max_depth=2)
        c = self._attach(self._client([[_msg(11, "小心點")]]), cfg, floor=10, offset=10)
        with mock.patch.object(monitor, "_sleep"):
            monitor._say(c, "第一串。第二串。第三串。第四串。")
        self.assertEqual(c.handled, [])                              # 陳述不被當插話即時回
        self.assertFalse(any(s in monitor._RESUME_BRIDGES for s in c.sent))  # 沒有『繼續剛剛的』橋接
        self.assertEqual(c.sent, ["第一串。", "第二串。", "第三串。", "第四串。"])  # 整段照常講完
        self.assertEqual(c._state.tg_update_offset, 10)             # offset 未前進＝留給下一圈合併

    def test_emit_rewrite_forced_off_no_truncation(self):
        # 自發相：rewrite 強制關——即使 INTERRUPT_REWRITE_ENABLED 開、被 redirect 插話，剩餘原串仍續送完（不暖收截斷）
        cfg = SimpleNamespace(telegram_chat_id="", interrupt_statement_enabled=False,
                              interrupt_rewrite_enabled=True, interrupt_max_depth=2)
        c = self._attach(self._client([[_msg(11, "等等這是什麼？")]]), cfg, floor=10, offset=10)
        with mock.patch.object(monitor, "_sleep"):
            monitor._say(c, "第一串。第二串。第三串。第四串。")
        self.assertEqual(c.handled, ["等等這是什麼？"])              # 仍優先回應
        self.assertIn("第四串。", c.sent)                            # 剩餘原串續送完（沒被暖收截斷）
        self.assertFalse(any(x in c.sent for x in monitor._REWRITE_CLOSE_LINES))  # 沒走 wrap 暖收

    def test_no_interrupt_capability_is_zero_regression(self):
        # 無 get_updates 能力（如 FakeClient）＝poll()→None＝逐位元同現狀（自發相 attach 後也零迴歸）
        cfg = SimpleNamespace(telegram_chat_id="", interrupt_statement_enabled=True,
                              interrupt_rewrite_enabled=True, interrupt_max_depth=2)

        class Fake:
            dry_run = False
            def __init__(self): self.sent = []
            def send(self, t): self.sent.append(t); return True
            def send_typing(self): pass
        f = Fake()
        st = SimpleNamespace(tg_update_offset=0)
        f._interrupt = monitor._BurstInterrupt(f, st, cfg, floor=-1,
                                               handle_fn=lambda u: None, statement_defer=True)
        with mock.patch.object(monitor, "_sleep"):
            monitor._say(f, "第一串。第二串。第三串。")
        self.assertEqual(f.sent, ["第一串。", "第二串。", "第三串。"])   # 沒插話偵測能力＝照常說完


class FakeUpdatesClient:
    """有 get_updates 能力的假 client（供相位層 attach 後 poll 用）。"""
    dry_run = False

    def __init__(self, polls=None):
        self.sent = []
        self._polls = list(polls or [])

    def send(self, text):
        self.sent.append(text)
        return True

    def send_typing(self):
        pass

    def get_updates(self, offset=0, timeout=0):
        return self._polls.pop(0) if self._polls else []


def _phase_cfg(emit_on=True):
    return SimpleNamespace(
        heartbeat_interval_min=3, telegram_chat_id="",
        emit_interrupt_enabled=emit_on, interrupt_statement_enabled=True,
        interrupt_rewrite_enabled=True, interrupt_max_depth=2,
        notify_cooldown_min=30, selfstate_repeat_cooldown_min=180)


class FeelPhaseAttachesInterruptTest(unittest.TestCase):
    """相位層（feel）執行期間掛一次 emit-path _BurstInterrupt（statement_defer=True），各 emit 的 _say 共用；
    旗標關＝不掛＝同現狀。用 patch 把 _selfstate_emit 換成探針，捕捉 feel 跑時的 client._interrupt 形狀。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _state(self):
        s = State(os.path.join(self.tmp, "state.json"))
        s.owner_folder_id = "F"
        s.gate_confirmed = 3
        s.tg_update_offset = 50
        return s

    def _feel(self, state, cfg):
        """跑 feel 相位，回 feel 執行當下捕捉到的 client._interrupt（由探針記下）。"""
        captured = {}
        client = FakeUpdatesClient()
        phases = monitor._life_phases(object(), client, state, cfg, None, None,
                                      chat_on=True, pacer={"last_tick": 1e18})
        feel = next(p for p in phases if p.name == "感覺").fn

        def probe(*a, **k):
            captured["intr"] = getattr(client, "_interrupt", None)
        # 把所有 emit 換成 no-op（只留探針在第一個），避免實跑 LLM/門檻
        with mock.patch.object(monitor, "_selfstate_emit", side_effect=probe), \
             mock.patch.object(monitor, "_spontaneous_emit"), \
             mock.patch.object(monitor, "_metacog_correct"), \
             mock.patch.object(monitor, "_soothe_unanswered"), \
             mock.patch.object(monitor, "_experience_step"), \
             mock.patch.object(monitor, "_ac_drift_emit"), \
             mock.patch.object(monitor, "_insight_emit"), \
             mock.patch.object(monitor, "_volition_step"):
            feel({"res": {"gate": 3}, "now": NOW, "data": {"meta": {}, "records": []}, "snap": object()})
        # 相位結束後 client._interrupt 應被 teardown 回 None（不洩進 relate）
        self.assertIsNone(getattr(client, "_interrupt", None))
        return captured.get("intr")

    def test_feel_attaches_emit_interrupt_when_enabled(self):
        intr = self._feel(self._state(), _phase_cfg(emit_on=True))
        self.assertIsNotNone(intr)                                  # feel 期間有掛 interrupt
        self.assertTrue(intr.statement_defer)                      # 自發相＝陳述一律 defer
        self.assertEqual(intr.floor, 49)                           # floor＝tg_update_offset-1（只認其後新到）

    def test_feel_no_attach_when_disabled_is_current_behavior(self):
        intr = self._feel(self._state(), _phase_cfg(emit_on=False))
        self.assertIsNone(intr)                                     # 旗標關＝不掛＝同現狀


if __name__ == "__main__":
    unittest.main()
