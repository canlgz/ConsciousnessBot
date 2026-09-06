# -*- coding: utf-8 -*-
"""🧭 §2.19 一次性 vs 常設的搶路由：「20分鐘後告訴我座標變動」不是「立一個常設訂閱」。

實測截圖 22:14：那句同時命中 `is_mood_watch_request` 與排程承諾捕捉（兩者皆 True），mood_watch
排在前面就贏 ⇒ 回了一整段「基準/門檻/取消方式」的訂閱 ack，使用者當場糾正「不要搞錯了，我是指
20 分鐘的時間後」。判別＝結構訊號、零詞表：帶未來時間錨＝一次性。全 stub、零網路。
"""

import io
import os
import re
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from telegram_monitor import monitor, selfstate
from telegram_monitor.state import State

NOW = datetime(2026, 7, 30, 14, 14, tzinfo=timezone.utc)
ONESHOT = "20分鐘後，告訴我這段時間內，情緒座標的前後變動狀態。"
STANDING = "座標有變動就主動跟我說"


class Cl:
    dry_run = True

    def __init__(self):
        self.sent = []

    def send(self, t):
        self.sent.append(t)
        return True

    def send_typing(self):
        pass


def _cfg(**kw):
    d = dict(mood_watch_enabled=True, mood_watch_oneshot_yield=True, mood_watch_delta=0.10,
             timezone="Asia/Taipei", dry_run=True)
    d.update(kw)
    return SimpleNamespace(**d)


def _state():
    return State(os.path.join(tempfile.mkdtemp(), "s.json"))


class RootCauseTest(unittest.TestCase):
    def test_both_detectors_hit_the_screenshot_line(self):
        # 釘住根因：不是哪個偵測器壞了，是**兩個都中**、排序決定誰贏
        self.assertTrue(selfstate.is_mood_watch_request(ONESHOT))
        self.assertTrue(selfstate.is_scheduled_promise_request(ONESHOT))


class YieldTest(unittest.TestCase):
    def test_oneshot_yields_to_the_scheduler(self):
        s, cl = _state(), Cl()
        r = monitor._maybe_mood_watch(cl, s, _cfg(), ONESHOT, NOW, NOW.timestamp())
        self.assertFalse(r)                                   # 讓路（呼叫端會繼續走排程承諾捕捉）
        self.assertIsNone(getattr(s, "mood_watch", None))     # 沒立訂閱
        self.assertEqual(cl.sent, [])                         # 沒送那段訂閱 ack

    def test_standing_request_still_subscribes(self):
        s, cl = _state(), Cl()
        r = monitor._maybe_mood_watch(cl, s, _cfg(), STANDING, NOW, NOW.timestamp())
        self.assertTrue(r)
        self.assertIsNotNone(s.mood_watch)

    def test_cancel_untouched(self):
        s, cl = _state(), Cl()
        s.mood_watch = {"ts": 1.0, "last_v": 0, "last_a": 0, "last_report_ts": 0.0, "made_text": "x"}
        self.assertTrue(monitor._maybe_mood_watch(cl, s, _cfg(), "不用再回報座標", NOW, NOW.timestamp()))
        self.assertIsNone(s.mood_watch)

    def test_flag_off_is_old_behaviour(self):
        s, cl = _state(), Cl()
        r = monitor._maybe_mood_watch(cl, s, _cfg(mood_watch_oneshot_yield=False), ONESHOT, NOW, NOW.timestamp())
        self.assertTrue(r)                                    # 旗標關＝照舊搶走（逐位元同現狀）
        self.assertIsNotNone(s.mood_watch)


class ConfigTest(unittest.TestCase):
    def test_flag_synced(self):
        src = io.open("telegram_monitor/config.py", encoding="utf-8").read()
        self.assertIn("MOOD_WATCH_ONESHOT_YIELD", src)
        self.assertIn("mood_watch_oneshot_yield", src)
        self.assertRegex(io.open(".env.example", encoding="utf-8").read(),
                         re.compile(r"^MOOD_WATCH_ONESHOT_YIELD=1", re.M))
        self.assertIn("MOOD_WATCH_ONESHOT_YIELD", io.open("README.md", encoding="utf-8").read())
        self.assertFalse(getattr(SimpleNamespace(), "mood_watch_oneshot_yield", False))


if __name__ == "__main__":
    unittest.main()
