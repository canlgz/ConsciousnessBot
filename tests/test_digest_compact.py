"""日報按當地日曆日呈現，與昨天的記寫及內部升格門檻分離。"""
import unittest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from telegram_monitor import analyzer, notifier, monitor


class CompactDigestTest(unittest.TestCase):
    tz = ZoneInfo("Asia/Taipei")
    now = datetime(2026, 9, 5, 1, 4, tzinfo=timezone.utc)

    def snapshot(self, timestamps):
        return analyzer.analyze({"records": [{"ts": ts, "type": "text"} for ts in timestamps],
                                 "contexts": [], "journeys": [], "meta": {}}, self.now, self.tz)

    def test_yesterday_in_last24h_is_not_today(self):
        snap = self.snapshot(["2026-09-04T01:24:00Z"])
        self.assertEqual(snap.summary["last24h"], 1)
        self.assertEqual(snap.summary["today"], 0)
        body = notifier.format_digest(snap, self.tz, self.now)
        self.assertIn("今天：目前尚無新記寫", body)
        self.assertIn("09/04 09:24", body)

    def test_local_midnight_and_future(self):
        snap = self.snapshot(["2026-09-04T15:59:59Z", "2026-09-04T16:00:00Z",
                              "2026-09-05T01:05:00Z"])
        self.assertEqual(snap.summary["today"], 1)

    def test_no_threshold_checklist_and_alert_preserved(self):
        snap = self.snapshot([])
        snap.gaps = [{"line": "還要在不同時段（間隔≥20分）回到主題2次" * 100}] * 6
        snap.heartbeat = {"status": "stalled", "age_hours": 30}
        body = notifier.format_digest(snap, self.tz, self.now)
        self.assertNotIn("間隔", body)
        self.assertNotIn("升格", body)
        self.assertIn("背景可能卡住", body)
        self.assertLess(len(body), 350)
        self.assertLessEqual(len(body.splitlines()), 9)
        description = monitor._describe_digest(snap)
        self.assertNotIn("間隔", description)
        self.assertIn("今日截至統計時新增 0 則", description)
        self.assertIn("最近持續不代表今天已完成", description)

    def test_missing_day_stat_is_not_reported_as_zero(self):
        snap = self.snapshot([])
        del snap.summary["today"]
        self.assertIn("尚無可確認的日統計", notifier.format_digest(snap, self.tz, self.now))
