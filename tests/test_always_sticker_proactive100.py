"""🎴 §1.00 教過的 always 常駐做法「主動回應後送情緒貼圖」→ 也覆蓋**守約兌現**等主動路徑（不只反思/自發）。

截圖：/skills 有「常駐風格·主動回應後送情緒貼圖」，但 bot 到點守約「嗨，我來了！…」（10:31）沒送貼圖——
根因：§0.96 只掛在 reflect/自發，沒掛在**守約兌現**（🤝）這條主動路徑。此測試驗證守約兌現後 always-sticker 真的送。
"""

import os
import tempfile
import time
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from telegram_monitor import monitor, plasticity
from telegram_monitor.state import State

TZ = ZoneInfo("Asia/Taipei")
ALWAYS = plasticity.SKILL_TRIGGER_ALWAYS


class FakeClient:
    def __init__(self):
        self.sent, self.stickers, self.dry_run = [], [], False

    def send(self, text):
        self.sent.append(text)
        return True

    def send_sticker(self, file_id):
        self.stickers.append(file_id)
        return True

    def send_typing(self):
        pass


def _coach():
    return SimpleNamespace(
        enabled=True, api_key="k", model="m", meter=SimpleNamespace(record=lambda *a, **k: None),
        voice_promise_keep=lambda when, facts, h, promised="", late=False, feeling_ground="", **k: "嗨，我來了！說好回來跟你說說話的。")


def _cfg(**over):
    base = dict(dry_run=False, telegram_chat_id="", scheduled_promise_enabled=True, promise_emit_enabled=True,
               promise_sched_ttl_sec=21600, timezone="Asia/Taipei", notify_cooldown_min=30,
               promise_overdue_guard_exempt=True, promise_act_aligned=True, promise_sticker_enabled=True,
               # 🎴 always-sticker 相關
               always_sticker_enabled=True, send_stickers=True, sticker_cooldown_min=20, sticker_file_ids=None,
               sticker_rotate_window=3, sticker_no_repeat_enabled=True, liked_sticker_pick_enabled=True)
    base.update(over)
    return SimpleNamespace(**base)


def _always_skill(weight=0.6):
    now = time.time()
    return {"kind": plasticity.KIND_SKILL, "key": f"||{ALWAYS}",
            "value": "主動回應使用者時，最後必須傳送一張代表自己情緒的貼圖",
            "weight": weight, "hits": 1, "born_ts": now, "last_ts": now}


class PromiseFulfillmentAlwaysStickerTest(unittest.TestCase):
    NOW = datetime(2026, 7, 5, 2, 30, tzinfo=timezone.utc)   # 10:30 台北

    def _state(self, with_skill=True, stock=True):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        s.entropy = SimpleNamespace(mood=0.4, hunger=0.2)
        s.engrams = [_always_skill()] if with_skill else []
        if stock:
            s.known_sticker_ids = [{"file_id": "POS", "valence": "positive", "emoji": "😄", "desc": "笑臉", "ts": 2}]
        s.last_sticker_ts = 0
        # 一筆**非送貼圖**的到點承諾（「跟我說說話」，沒有 wants_sticker）
        s.scheduled_promises = [{"target_ts": self.NOW.timestamp() - 30, "made_ts": self.NOW.timestamp() - 600,
                                 "fulfilled": False, "behavior": "回來跟你說說話", "status": "pending"}]
        return s

    def test_fulfillment_sends_always_sticker(self):
        s, c = self._state(), FakeClient()
        monitor._promise_emit(c, s, _cfg(), _coach(), self.NOW)
        self.assertTrue(s.scheduled_promises[0]["fulfilled"])    # 守約兌現
        self.assertTrue(c.sent)                                  # 有文字兌現句
        self.assertEqual(c.stickers, ["POS"])                    # 🎴 §1.00 兌現後依常駐做法真的送了情緒貼圖

    def test_no_skill_no_sticker(self):
        s, c = self._state(with_skill=False), FakeClient()
        monitor._promise_emit(c, s, _cfg(), _coach(), self.NOW)
        self.assertTrue(s.scheduled_promises[0]["fulfilled"])
        self.assertEqual(c.stickers, [])                         # 沒教這條做法 → 不送＝逐位元同現狀

    def test_flag_off_no_sticker(self):
        s, c = self._state(), FakeClient()
        monitor._promise_emit(c, s, _cfg(always_sticker_enabled=False), _coach(), self.NOW)
        self.assertEqual(c.stickers, [])                         # ALWAYS_STICKER=0 → no-op

    def test_no_real_sticker_no_emoji_fakery(self):
        s, c = self._state(stock=False), FakeClient()
        monitor._promise_emit(c, s, _cfg(), _coach(), self.NOW)
        self.assertTrue(s.scheduled_promises[0]["fulfilled"])
        self.assertEqual(c.stickers, [])                         # 沒相符真貼圖＝不送、絕不 emoji 假裝


if __name__ == "__main__":
    unittest.main()
