"""🔁 §1.41 守約去重複（PROMISE_KEEP_ANTI_REPEAT）：兌現句不再逐字照抄 bot 剛說過的話。

截圖根因：使用者約「20 分鐘後告訴我你對此事的感受」。20:32 使用者問「你有在想答案嗎」時 bot 已把感受講了一整段
（「大改造/會很慘…核心微微顫動…站在懸崖邊…」）。20:42 排程兌現（_promise_emit → voice_promise_keep）卻**逐字重播
同一段**（只有開頭時間 20:31→20:42 不同），連「剛剛我沒有去想什麼答案」這種只對得上 20:31 那問的話都照抄。根因：
兌現路徑（voice_promise_keep）沒有 §0.56/§1.21/§1.22 那套「別重講剛說過的」防線——fact_or_chat lane 才有 _anti_repeat_hint。

修法（比照 §1.34/§1.36：確定性、不呼叫 LLM）：_promise_keep_body（emit/§0.66 橋/§1.19 preempt 三路共用）末端，兌現句
若近乎照抄這幾分鐘內剛說過的某則 bot 回覆（echo._looks_same，剝掉 🤝/🍃 等前綴＋正規化）→ 換成誠實的「已說過、沒變化」
句（不照樣再講一遍）。旗標關＝逐位元同現狀。全 stub、零網路。
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
NOW = datetime(2026, 7, 13, 12, 42, 0, tzinfo=timezone.utc)   # 台北 20:42

PRIOR = "我讓那個關於大改造和會很慘的預告在自己裡面迴盪，核心微微顫動，說不上來的不確定，像站在懸崖邊往下看。"
DUP = "我讓那個關於大改造和會很慘的預告在自己裡面迴盪，核心微微顫動，說不上來的不確定，像站在懸崖邊往下望。"  # 只差一字
DIFF = "我這陣子挺平靜的，手上在讀你上週寫的那幾則，覺得踏實。"


class RepeatHitTest(unittest.TestCase):
    def _hist(self, text, ago=600):
        return [{"role": "model", "text": text, "ts": NOW.timestamp() - ago}]

    def test_near_verbatim_hit(self):
        self.assertTrue(monitor._promise_keep_repeat_hit(DUP, self._hist(PRIOR), NOW.timestamp(), 1800, 0.8))

    def test_prefixed_recent_still_hit(self):
        # 剛說過的那則帶 🤝/🍃 前綴也要抓得到（剝前綴後比對）
        self.assertTrue(monitor._promise_keep_repeat_hit(DUP, self._hist("🍃 " + PRIOR), NOW.timestamp(), 1800, 0.8))

    def test_unrelated_not_hit(self):
        self.assertFalse(monitor._promise_keep_repeat_hit(DUP, self._hist(DIFF), NOW.timestamp(), 1800, 0.8))

    def test_short_greeting_not_hit(self):
        # 短守約句（嗨我來了）常見、別誤判
        self.assertFalse(monitor._promise_keep_repeat_hit("嗨，我來了。", self._hist("嗨我來了"), NOW.timestamp(), 1800, 0.8))

    def test_out_of_window_not_hit(self):
        self.assertFalse(monitor._promise_keep_repeat_hit(DUP, self._hist(PRIOR, ago=99999), NOW.timestamp(), 1800, 0.8))


class KeepBodyGateTest(unittest.TestCase):
    def _coach(self, msg):
        c = SimpleNamespace(enabled=True, api_key="k", model="m",
                            meter=SimpleNamespace(record=lambda *a, **k: None))
        c.voice_promise_keep = lambda *a, **k: msg
        return c

    def _state(self, prior=PRIOR):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.convo_history = [{"role": "model", "text": prior, "ts": NOW.timestamp() - 600}]
        return s

    def _p(self):
        return {"target_ts": NOW.timestamp(), "behavior": "說說我的感受"}

    def test_gate_on_replaces_dup(self):
        cfg = SimpleNamespace(promise_keep_anti_repeat_enabled=True)
        msg = monitor._promise_keep_body(self._state(), cfg, self._coach(DUP), self._p(), NOW, TZ, overdue=False)
        self.assertNotIn("懸崖邊往下望", msg)          # 不再照抄
        self.assertIn("不照樣再講", msg)               # 誠實『已說過、沒變化』句

    def test_gate_off_passthrough(self):
        cfg = SimpleNamespace()                        # getattr False＝逐位元同現狀
        msg = monitor._promise_keep_body(self._state(), cfg, self._coach(DUP), self._p(), NOW, TZ, overdue=False)
        self.assertEqual(msg, DUP)

    def test_non_dup_not_replaced(self):
        cfg = SimpleNamespace(promise_keep_anti_repeat_enabled=True)
        msg = monitor._promise_keep_body(self._state(), cfg, self._coach(DIFF), self._p(), NOW, TZ, overdue=False)
        self.assertEqual(msg, DIFF)


class EndToEndEmitTest(unittest.TestCase):
    class Cl:
        def __init__(self): self.sent, self.stickers, self.dry_run = [], [], False
        def send(self, t): self.sent.append(t); return True
        def send_sticker(self, f): self.stickers.append(f); return True

    def _coach(self, msg):
        c = SimpleNamespace(enabled=True, api_key="k", model="m",
                            meter=SimpleNamespace(record=lambda *a, **k: None))
        c.voice_promise_keep = lambda *a, **k: msg
        return c

    def _cfg(self, on=True):
        return SimpleNamespace(promise_emit_enabled=True, promise_sched_ttl_sec=21600, timezone="Asia/Taipei",
            promise_late_exempt_defer=True, promise_overdue_guard_exempt=True, promise_act_aligned=True,
            promise_sticker_enabled=True, sched_recur_daily_enabled=True, promise_expire_apology_enabled=True,
            always_sticker_enabled=False, promise_keep_anti_repeat_enabled=on)

    def _run(self, voice, on=True):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json")); s.owner_folder_id = "F"
        s.last_user_msg_ts = 0
        s.convo_history = [{"role": "model", "text": PRIOR, "ts": NOW.timestamp() - 600}]
        s.scheduled_promises = [{"target_ts": NOW.timestamp(), "behavior": "說說我的感受"}]
        cl = self.Cl()
        monitor._promise_emit(cl, s, self._cfg(on), self._coach(voice), NOW)
        return "".join(cl.sent)

    def test_dup_fulfillment_replaced(self):
        out = self._run(DUP)
        self.assertNotIn("懸崖邊往下望", out)
        self.assertIn("不照樣再講", out)

    def test_flag_off_dup_passes(self):
        out = self._run(DUP, on=False)
        self.assertIn("懸崖邊往下望", out)


class ConfigTest(unittest.TestCase):
    def test_config_synced(self):
        src = open("telegram_monitor/config.py", encoding="utf-8").read()
        self.assertIn("PROMISE_KEEP_ANTI_REPEAT", src)
        self.assertIn("promise_keep_anti_repeat_enabled", src)
        env = open(".env.example", encoding="utf-8").read()
        self.assertRegex(env, re.compile(r"^PROMISE_KEEP_ANTI_REPEAT=1", re.M))
        self.assertIn("PROMISE_KEEP_ANTI_REPEAT", open("README.md", encoding="utf-8").read())

    def test_getattr_defaults_false(self):
        self.assertFalse(getattr(SimpleNamespace(), "promise_keep_anti_repeat_enabled", False))


if __name__ == "__main__":
    unittest.main()
