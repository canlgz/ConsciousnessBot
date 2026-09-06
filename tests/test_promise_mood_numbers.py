# -*- coding: utf-8 -*-
"""🧭 §2.20 座標守約必須帶真數字：接地行有進 prompt、LLM 轉述時把數字丟掉＝沒有座標的座標報告。

實測 23:21：「嗨，我現在的感覺，用座標來說，是…亮了一點、繃了一段。」——§1.25/§1.47 算好的
接地行（含 V/A 差分）確實進了 prompt，數字被轉述丟掉 ⇒ 交付驗收判沒交 ⇒ owed 連鎖。
🧭 訂閱回報有 §1.67 的數字驗收，守約路徑一直沒有。全 stub、零網路。
"""

import io
import os
import re
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from telegram_monitor import monitor
from telegram_monitor.state import State

NOW = datetime(2026, 7, 30, 15, 19, tzinfo=timezone.utc)
TZ = ZoneInfo("Asia/Taipei")
VAGUE = "嗨，我現在的感覺，用座標來說，是往「興奮、雀躍」的方向亮了一點、繃了一段。"


def _p(state):
    return {"target_ts": NOW.timestamp() - 60, "made_ts": NOW.timestamp() - 1860, "behavior": "跟他回報",
            "made_text": "30 分鐘後跟我報告你這段時間的座標變動", "fulfilled": False, "status": "pending",
            "mood_baseline": monitor._mood_snapshot(state, NOW.timestamp() - 1860)}


def _cfg(**kw):
    d = dict(promise_mood_ground_enabled=True, mood_coord_deliver_enabled=True,
             promise_mood_numbers_enabled=True, promise_delivery_proof_enabled=False,
             promise_deliver_content_enabled=False, promise_teaser_hollow_enabled=False,
             timezone="Asia/Taipei", dry_run=True, sched_recur_daily_enabled=True)
    d.update(kw)
    return SimpleNamespace(**d)


def _coach(reply):
    c = SimpleNamespace(enabled=True)
    c.voice_promise_keep = lambda *a, **k: reply
    return c


class MoodNumbersTest(unittest.TestCase):
    def setUp(self):
        self.s = State(os.path.join(tempfile.mkdtemp(), "s.json"))

    def test_wide_predicate_covers_the_screenshot_promise(self):
        # 釘住前提：這句承諾（behavior 抽掉「座標」二字）靠 made_text 的寬判仍然接得到基準
        self.assertTrue(monitor._mood_promise_hit(_cfg(), _p(self.s)))

    def test_vague_keep_gets_the_grounded_line_appended(self):
        msg = monitor._promise_keep_body(self.s, _cfg(), _coach(VAGUE), _p(self.s), NOW, TZ, False)
        self.assertRegex(msg, re.compile(r"[+-]\d\.\d\d"))    # ★ 真數字一定在
        self.assertIn(VAGUE.rstrip("。")[:12], msg)           # 只增不減：LLM 的話保留

    def test_keep_with_numbers_untouched(self):
        withnum = "我說好要報：從『平穩』往上亮了一段（V +0.30、A +0.15）。"
        msg = monitor._promise_keep_body(self.s, _cfg(), _coach(withnum), _p(self.s), NOW, TZ, False)
        self.assertEqual(msg.count("V +"), 1)                 # 不重複附

    def test_flag_off_is_verbatim_old(self):
        msg = monitor._promise_keep_body(self.s, _cfg(promise_mood_numbers_enabled=False),
                                         _coach(VAGUE), _p(self.s), NOW, TZ, False)
        self.assertNotRegex(msg, re.compile(r"[+-]\d\.\d\d"))

    def test_non_mood_promise_untouched(self):
        p = {"target_ts": NOW.timestamp() - 60, "made_ts": NOW.timestamp() - 1860, "behavior": "跟他打招呼",
             "made_text": "30分鐘後跟我打聲招呼", "fulfilled": False, "status": "pending"}
        msg = monitor._promise_keep_body(self.s, _cfg(), _coach("嗨，我來跟你打招呼了。"), p, NOW, TZ, False)
        self.assertNotRegex(msg, re.compile(r"[+-]\d\.\d\d"))  # 沒接地的承諾不硬塞數字

    def test_guard_sits_on_the_common_path(self):
        # ⚠️ 第一版插在 `if not msg:` 模板分支裡＝LLM 有產出時永遠走不到（自己實測抓到）——釘死位置
        src = io.open("telegram_monitor/monitor.py", encoding="utf-8").read()
        i = src.index("§2.20 座標守約沒帶數字")
        j = src.index("# 📦 §1.44 兌現要交付內容（截圖 09:20")
        self.assertLess(i, j)                                 # 在 §1.44 檢查之前、模板段之後的共同路徑上
        self.assertGreater(i, src.index('msg = f"{late_pre}我說過{when or \'這時候\'}跟你打招呼——嗨，我來了。"'))


class ConfigTest(unittest.TestCase):
    def test_flag_synced(self):
        src = io.open("telegram_monitor/config.py", encoding="utf-8").read()
        self.assertIn("PROMISE_MOOD_NUMBERS", src)
        self.assertIn("promise_mood_numbers_enabled", src)
        self.assertRegex(io.open(".env.example", encoding="utf-8").read(),
                         re.compile(r"^PROMISE_MOOD_NUMBERS=1", re.M))
        self.assertIn("PROMISE_MOOD_NUMBERS", io.open("README.md", encoding="utf-8").read())
        self.assertFalse(getattr(SimpleNamespace(), "promise_mood_numbers_enabled", False))


if __name__ == "__main__":
    unittest.main()
