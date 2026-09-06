"""🎴 §1.40 送貼圖承諾兌現的假送硬守門（PROMISE_STICKER_FAKESEND_GUARD）：§1.34 假送閘的**主動路徑版**。

截圖根因：使用者約「想想改變＋代表 sticker，20 分鐘後告訴我」。bot 沒有任何可送的真 Telegram 貼圖（沒教過、沒設
sticker_file_ids → reaction.sendable_sticker_ids 空），兌現時 _promise_send_sticker 送不出（_sticker_sent=False），
但 LLM 兌現句仍懸空宣告「這次我選這張貼圖，來代表我現在的心情：」——宣告了卻沒貼圖出來。§1.34 假送閘只守互動
出口（state=None）；主動兌現走 _say(prefix="🤝 ", state=…) 繞過它。

修法（比照 §1.34/§1.36：確定性、不呼叫 LLM）：_promise_keep_body（emit/§0.66 橋/§1.19 preempt 三路共用）末端，
這輪貼圖沒真送出（sticker_ok=False）＋旗標開時，把「挑/選/送了…貼圖／這張貼圖代表…」的宣告句確定性剝掉（引用歸屬
「你說我選了…」、否定「我沒選貼圖」、自帶誠實「想送但還沒存到」皆不誤剝）；整句被剝空＝換誠實句。旗標關＝逐位元同現狀。
"""

import os
import re
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from telegram_monitor import monitor
from telegram_monitor.state import State

TZ = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 7, 13, 14, 43, 0, tzinfo=timezone.utc)   # 台北 22:43（截圖時刻）

DANGLING = "嗨，我來了。這次我選這張貼圖，來代表我現在的心情："
MIXED = "我對貼圖和 emoji 有更深的理解了。這次我選這張貼圖，來代表我現在的心情："
HONEST = "我好想選一張貼圖給你，但手邊還沒存到能送的，先老實說。"


class ClaimHitTest(unittest.TestCase):
    def test_present_tense_claim_hits(self):
        self.assertTrue(monitor._sticker_claim_hit("這次我選這張貼圖，來代表我現在的心情："))
        self.assertTrue(monitor._sticker_claim_hit("我挑了一張貼圖給你。"))
        self.assertTrue(monitor._sticker_claim_hit("這張貼圖代表我此刻的心情。"))

    def test_quote_attribution_not_hit(self):
        self.assertFalse(monitor._sticker_claim_hit("你說我選了一張貼圖給你？"))

    def test_negation_not_hit(self):
        self.assertFalse(monitor._sticker_claim_hit("我這次沒選貼圖。"))
        self.assertFalse(monitor._sticker_claim_hit("我沒辦法送貼圖給你。"))


class StripTest(unittest.TestCase):
    def test_dangling_becomes_honest(self):
        out, ch = monitor._strip_sticker_claim(DANGLING)
        self.assertTrue(ch)
        self.assertNotIn("選這張貼圖", out)
        self.assertFalse(out.rstrip().endswith("："))

    def test_mixed_keeps_real_content(self):
        out, ch = monitor._strip_sticker_claim(MIXED)
        self.assertTrue(ch)
        self.assertIn("更深的理解", out)
        self.assertNotIn("選這張貼圖", out)

    def test_honest_not_stripped(self):
        # 自帶誠實限定（但手邊還沒存到）＝老實話，不剝
        out, ch = monitor._strip_sticker_claim(HONEST)
        self.assertFalse(ch)
        self.assertEqual(out, HONEST)

    def test_no_claim_unchanged(self):
        t = "嗨，我來了，說好這時候跟你說我的改變。"
        out, ch = monitor._strip_sticker_claim(t)
        self.assertFalse(ch)
        self.assertEqual(out, t)


class KeepBodyGateTest(unittest.TestCase):
    """_promise_keep_body 末端閘：sticker_ok=False＋旗標開→剝；旗標關/真送(sticker_ok=True)→不剝。"""

    def _coach(self, msg):
        c = SimpleNamespace(enabled=True, api_key="k", model="m",
                            meter=SimpleNamespace(record=lambda *a, **k: None))
        c.voice_promise_keep = lambda *a, **k: msg
        return c

    def _p(self):
        return {"target_ts": NOW.timestamp(), "behavior": "說說我的改變和貼圖", "wants_sticker": True}

    def test_gate_on_strips_when_not_sent(self):
        cfg = SimpleNamespace(promise_sticker_fakesend_guard_enabled=True)
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        msg = monitor._promise_keep_body(s, cfg, self._coach(DANGLING), self._p(), NOW, TZ,
                                         overdue=False, sticker_ok=False)
        self.assertNotIn("選這張貼圖", msg)
        self.assertFalse(msg.rstrip().endswith("："))

    def test_gate_off_passthrough(self):
        cfg = SimpleNamespace()                                # 未設此欄＝getattr False＝逐位元同現狀
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        msg = monitor._promise_keep_body(s, cfg, self._coach(DANGLING), self._p(), NOW, TZ,
                                         overdue=False, sticker_ok=False)
        self.assertEqual(msg, DANGLING)

    def test_real_sent_not_stripped(self):
        # sticker_ok=True（真送出）→ 宣告是真的、不剝
        cfg = SimpleNamespace(promise_sticker_fakesend_guard_enabled=True)
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        msg = monitor._promise_keep_body(s, cfg, self._coach(DANGLING), self._p(), NOW, TZ,
                                         overdue=False, sticker_ok=True)
        self.assertEqual(msg, DANGLING)


class ConfigTest(unittest.TestCase):
    def test_config_synced(self):
        src = open("telegram_monitor/config.py", encoding="utf-8").read()
        self.assertIn("PROMISE_STICKER_FAKESEND_GUARD", src)
        self.assertIn("promise_sticker_fakesend_guard_enabled", src)
        env = open(".env.example", encoding="utf-8").read()
        self.assertRegex(env, re.compile(r"^PROMISE_STICKER_FAKESEND_GUARD=1", re.M))
        self.assertIn("PROMISE_STICKER_FAKESEND_GUARD", open("README.md", encoding="utf-8").read())

    def test_getattr_defaults_false(self):
        self.assertFalse(getattr(SimpleNamespace(), "promise_sticker_fakesend_guard_enabled", False))


if __name__ == "__main__":
    unittest.main()
