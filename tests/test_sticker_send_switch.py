"""🔇 §1.34 STICKER_V2/switch SEND_STICKERS=0 一致靜音：關＝不送、不宣稱、不撈附件代替（收斂 §1.33 雙讀法）。
根因（probe_switch）：互動送圖底層 _send_real_sticker_from/_send_liked_sticker 只 gate dry_run、不 gate cfg.send_stickers，
2740/2744/2749 呼叫點也沒包 send_stickers → SEND_STICKERS=0 下 _maybe_sticker_send('送我一張貼圖') 仍真送。
反觀主動 lane 全部 `if not cfg.send_stickers…return`。

設計：互動三 lane（_maybe_sticker_remember/_maybe_sticker_send/_maybe_llm_sticker_rescue）**入口**加
`if not getattr(cfg,'send_stickers', True): return False`（沿用主動 lane 既有讀法、getattr 預設 True＝既有測試未設此欄仍送
＝零翻修）→ 關時三 lane 一律 decline、落一般聊天 lane（該 lane 於 SEND_STICKERS=0 已注入維護 hint，§1.33）。
附件替代抑制與 F4 假送閘協同見 test_sticker_img_disambig / F4 專測。全 stub、零網路。
"""

import os
import tempfile
import unittest
from types import SimpleNamespace

from telegram_monitor import monitor, selfstate
from telegram_monitor.state import State

NOW = 1_800_000_000.0


class FakeClient:
    def __init__(self):
        self.sent, self.stickers = [], []
        self.dry_run = False

    def send(self, text):
        self.sent.append(text)
        return True

    def send_sticker(self, fid):
        self.stickers.append(fid)
        return True


def _coach(judge=None):
    c = SimpleNamespace(enabled=True, api_key="k", model="m",
                        meter=SimpleNamespace(record=lambda *a, **k: None),
                        judge_calls=[])

    def _judge(text):
        c.judge_calls.append(text)
        return None if judge is None else judge(text)

    c.judge_sticker_request = _judge
    return c


def _cfg(**over):
    base = dict(dry_run=False, send_stickers=True, sticker_send_request_enabled=True,
                sticker_llm_rescue_enabled=True, sticker_why_ground_enabled=True,
                sticker_pick_rescue_enabled=True, liked_sticker_pick_enabled=True,
                sticker_no_repeat_enabled=True, sticker_rotate_window=3,
                affect_circumplex_enabled=True, sticker_file_ids=None)
    base.update(over)
    return SimpleNamespace(**base)


class SendSwitchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _state(self):
        s = State(os.path.join(self.tmp, "s.json"))
        s.known_sticker_ids = [{"file_id": "CHICKEN_FID", "file_unique_id": "U1",
                                "emoji": "🐔", "valence": "neutral", "ts": NOW, "desc": ""}]
        s.last_sticker_ts = NOW
        return s

    # ───────── RED→GREEN：cfg.send_stickers=False → _maybe_sticker_send('送我一張貼圖') 回 False、零真送 ─────────
    def test_send_off_silences_maybe_sticker_send(self):
        st, cl = self._state(), FakeClient()
        ok = monitor._maybe_sticker_send(cl, st, _cfg(send_stickers=False), "送我一張貼圖", NOW)
        self.assertFalse(ok)                              # 不接管＝落一般聊天 lane（維護 hint）
        self.assertEqual(cl.stickers, [])                 # 不真送
        self.assertEqual(cl.sent, [])                     # 不宣稱「來，這張真貼圖送你」

    # ───────── 三 lane 全靜音：remember / send / llm_rescue 於 send_stickers=False 全回 False、零副作用 ─────────
    def test_all_three_lanes_silenced(self):
        st, cl, co = self._state(), FakeClient(), _coach(judge=lambda t: (True, True))
        cfg = _cfg(send_stickers=False)
        self.assertFalse(monitor._maybe_sticker_remember(cl, st, cfg, "這些貼圖都記下來", NOW))
        self.assertFalse(monitor._maybe_sticker_send(cl, st, cfg, "再來一張貼圖", NOW))
        self.assertFalse(monitor._maybe_llm_sticker_rescue(cl, st, cfg, co,
                                                           SimpleNamespace(kind="self_state"), "貼圖呢", NOW))
        self.assertEqual(cl.stickers, [])
        self.assertEqual(cl.sent, [])
        self.assertEqual(co.judge_calls, [])              # 逃生閘也不燒 LLM

    # ───────── 偏誤鎖①：cfg.send_stickers=True → 互動照送（保護既有 §0.84/§1.15 送圖格） ─────────
    def test_send_on_still_sends(self):
        st, cl = self._state(), FakeClient()
        ok = monitor._maybe_sticker_send(cl, st, _cfg(send_stickers=True), "送我一張貼圖", NOW)
        self.assertTrue(ok)
        self.assertEqual(cl.stickers, ["CHICKEN_FID"])

    # ───────── 偏誤鎖②：fake cfg 未設 send_stickers 欄（getattr 預設 True）→ 互動照送＝逐位元同現狀 ─────────
    def test_missing_field_defaults_send(self):
        st, cl = self._state(), FakeClient()
        cfg = SimpleNamespace(dry_run=False, sticker_send_request_enabled=True,
                              liked_sticker_pick_enabled=True, sticker_no_repeat_enabled=True,
                              sticker_rotate_window=3, affect_circumplex_enabled=True,
                              sticker_file_ids=None, sticker_why_ground_enabled=True)
        self.assertFalse(hasattr(cfg, "send_stickers"))
        ok = monitor._maybe_sticker_send(cl, st, cfg, "送我一張貼圖", NOW)
        self.assertTrue(ok)
        self.assertEqual(cl.stickers, ["CHICKEN_FID"])


if __name__ == "__main__":
    unittest.main()
