"""🕰️ 會話節奏（monitor.sessionize → build_memory_brief 的【會話節奏】段）：用對話時間戳算「這次是隔一陣
回來 vs 同段延續、分幾段」，讓所有回覆路徑都有時間節奏感（助判意圖）。grounded（量走 temporal.human_gap）、
純函式、關旗標＝不加此段（逐位元同現狀）。"""

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from telegram_monitor import monitor, coach

TZ = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 6, 26, 12, 0, tzinfo=timezone.utc)


def _h(*ts):
    return [{"role": "user", "text": "x", "ts": t} for t in ts]


class SessionizeTest(unittest.TestCase):
    def test_two_segments(self):
        now = 100000
        out = monitor.sessionize(_h(90000, 90100, 99900, 99950), now, gap_sec=3600)
        self.assertIn("分成 2 段", out)
        self.assertIn("2 小時", out)            # prev_gap=9800s → human_gap=2 小時（程式算、不自推）
        self.assertIn("又回來開的", out)

    def test_two_segments_with_tail_when_last_gap_long(self):
        now = 100000
        out = monitor.sessionize(_h(80000, 80100, 90000), now, gap_sec=3600)   # last_gap=10000≥3600 → 帶尾巴
        self.assertIn("分成 2 段", out)
        self.assertIn("到你這句約過了", out)

    def test_single_session_recent_returns_empty(self):
        now = 100000
        self.assertEqual(monitor.sessionize(_h(99000, 99500, 99900), now, gap_sec=3600), "")

    def test_single_session_but_long_since_last(self):
        now = 100000
        out = monitor.sessionize(_h(80000, 80500), now, gap_sec=3600)   # 同段、但距上一句 19500s≥3600
        self.assertIn("連續的", out)
        self.assertIn("又開口", out)
        self.assertIn("5 小時", out)            # human_gap(19500)=5 小時

    def test_insufficient_data(self):
        self.assertEqual(monitor.sessionize([], 100000), "")
        self.assertEqual(monitor.sessionize(_h(100), 100000), "")
        self.assertEqual(monitor.sessionize([{"role": "user", "text": "x"}], 100000), "")  # 無 ts

    def test_no_now_ts(self):
        self.assertEqual(monitor.sessionize(_h(1, 5000), 0), "")


class BriefSessionStructTest(unittest.TestCase):
    def _snap(self):
        return SimpleNamespace(
            summary={"total": 0, "last24h": 0, "last7d": 0, "streak": 0},
            funnel={"candidate": 0, "context": 0, "journey": 0, "watch": 0},
            heartbeat={"status": "idle"})

    def test_session_struct_injected(self):
        brief = coach.build_memory_brief({}, self._snap(), TZ, now=NOW, session_struct="我們近期的對話分成 2 段。")
        self.assertIn("【會話節奏】", brief)
        self.assertIn("我們近期的對話分成 2 段。", brief)

    def test_default_no_session_section(self):
        # 預設不傳 → 不含【會話節奏】（逐位元同現狀）
        self.assertNotIn("【會話節奏】", coach.build_memory_brief({}, self._snap(), TZ, now=NOW))


if __name__ == "__main__":
    unittest.main()
