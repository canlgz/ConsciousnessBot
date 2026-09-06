"""時間感引擎測試（純函式、注入 now）。"""

import unittest
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from telegram_monitor import analyzer, temporal

TZ = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)  # Taipei 20:00 週一


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


class TemporalBuildTest(unittest.TestCase):
    def _build(self, records, contexts=None):
        data = {"meta": {}, "contexts": contexts or [], "journeys": [],
                "explorations": [], "records": records}
        snap = analyzer.analyze(data, NOW, TZ)
        return temporal.build(data, snap, TZ, NOW)

    def test_now_and_gap_and_drought(self):
        recs = [{"id": "a", "ts": iso(NOW - timedelta(days=4)), "type": "text", "text": "x"}]
        tc = self._build(recs)
        self.assertIn("週一", tc["now_local"])
        self.assertIn("晚上", tc["now_local"])
        self.assertEqual(tc["last_write_gap"], "4 天")
        self.assertTrue(tc["drought"])           # 4 天沒寫 ≥ 門檻

    def test_cooling_context(self):
        recs = [{"id": "r1", "ts": iso(NOW - timedelta(days=20)), "type": "text", "text": "x"}]
        ctx = [{"id": "c1", "status": "candidate", "label": "舊主題", "category": "隨想",
                "recordIds": ["r1"], "lastTs": iso(NOW - timedelta(days=20))}]
        tc = self._build(recs, ctx)
        self.assertTrue(any("舊主題" in line and "20 天沒回來" in line for line in tc["cooling"]))

    def test_on_this_day(self):
        recs = [{"id": "w", "ts": iso(NOW - timedelta(days=7)), "type": "text",
                 "topicLabel": "一週前的事", "text": "x"}]
        tc = self._build(recs)
        self.assertTrue(any("一週前的今天" in r for r in tc["on_this_day"]))

    def test_day_part(self):
        self.assertEqual(temporal.day_part(2), "深夜")
        self.assertEqual(temporal.day_part(9), "早上")
        self.assertEqual(temporal.day_part(20), "晚上")

    def test_brief_sections_carries_internal_guard(self):
        # 🛡️ 截圖根因：問聯想卻被回「我現在是晚上，跟早上你記寫的時間差好多喔」——時間感是內部用、不是話題。
        # 【時間感】區塊要附「除非被問起時間/節奏，否則別主動拿時間差當開場」的守則。
        recs = [{"id": "a", "ts": iso(NOW - timedelta(days=4)), "type": "text", "text": "x"}]
        tc = self._build(recs)
        lines = temporal.brief_sections(tc)
        self.assertEqual(lines[0], "【時間感】")
        guard = "\n".join(lines)
        self.assertIn("內部", guard)
        self.assertIn("別主動", guard)


class TimeRangeTest(unittest.TestCase):
    def test_last_week(self):
        rng = temporal.match_time_range("上週我在想什麼", NOW, TZ)
        self.assertIsNotNone(rng)
        start, end, label = rng
        self.assertIn("上週", label)
        self.assertEqual((end - start).days, 7)

    def test_n_days_ago(self):
        rng = temporal.match_time_range("三天前那則", NOW, TZ)  # 中文數字不解析；用阿拉伯數字
        self.assertIsNone(rng)
        rng2 = temporal.match_time_range("3 天前那則", NOW, TZ)
        self.assertIsNotNone(rng2)
        self.assertIn("3 天前", rng2[2])

    def test_no_match(self):
        self.assertIsNone(temporal.match_time_range("最好的歷程是哪個", NOW, TZ))

    def test_hours_window(self):
        rng = temporal.match_time_range("3小時前", NOW, TZ)
        self.assertIsNotNone(rng)
        start, end, label = rng
        self.assertIn("3 小時", label)
        self.assertAlmostEqual((end - start).total_seconds(), 3 * 3600, delta=2)

    def test_recent_no_number(self):
        rng = temporal.match_time_range("最近有寫什麼", NOW, TZ)
        self.assertIsNotNone(rng)
        self.assertIn("最近", rng[2])

    def test_n_days_within_is_rolling(self):
        rng = temporal.match_time_range("最近5天", NOW, TZ)
        self.assertIsNotNone(rng)
        start, end, _ = rng
        self.assertAlmostEqual((end - start).days, 5, delta=1)

    def test_records_in_range(self):
        recs = [
            {"id": "in", "ts": iso(NOW - timedelta(days=3))},   # 06/12 → 上週內（今天是週一 06/15）
            {"id": "out", "ts": iso(NOW)},                      # 本週
        ]
        rng = temporal.match_time_range("上週", NOW, TZ)
        hits = temporal.records_in_range(recs, rng[0], rng[1])
        self.assertEqual([r["id"] for r in hits], ["in"])

    def test_just_now_not_a_plain_match(self):
        # 「剛剛」是 session-based，不該被純函式 match_time_range 攔成 24h
        self.assertTrue(temporal.is_just_now("剛剛寫了哪些"))
        self.assertTrue(temporal.is_just_now("剛才那批"))
        self.assertFalse(temporal.is_just_now("最近寫了什麼"))
        self.assertIsNone(temporal.match_time_range("剛剛寫了哪些", NOW, TZ))


class RecentSessionTest(unittest.TestCase):
    def test_recent_session_groups_last_burst_only(self):
        # 最近這批：19:07 / 19:03（相隔 4 分，同段）；18:06 距 19:03 達 57 分 → 斷開不算
        recs = [
            {"id": "old", "ts": iso(NOW - timedelta(days=1)), "type": "text", "text": "昨天"},
            {"id": "s0",  "ts": iso(NOW - timedelta(minutes=95)), "type": "text", "text": "18:06 那段"},
            {"id": "s1",  "ts": iso(NOW - timedelta(minutes=38)), "type": "text", "text": "19:03 自發敏感度"},
            {"id": "s2",  "ts": iso(NOW - timedelta(minutes=34)), "type": "text", "text": "19:07 水管修好"},
        ]
        start, end, label, hits = temporal.recent_session(recs, NOW, TZ)
        self.assertEqual([r["id"] for r in hits], ["s1", "s2"])   # 只剩最近這段連續記寫
        self.assertIn("剛剛這段", label)
        self.assertLess((end - start).total_seconds(), 30 * 60)   # 一小段，不是一整天

    def test_recent_session_anchors_on_last_write_not_now(self):
        # 即使最後一筆是幾小時前，「剛剛」仍抓得到那批（錨在最後一筆，不被 now 稀釋）
        recs = [
            {"id": "a", "ts": iso(NOW - timedelta(hours=5, minutes=2)), "type": "text", "text": "x"},
            {"id": "b", "ts": iso(NOW - timedelta(hours=5)), "type": "text", "text": "y"},
        ]
        _, _, _, hits = temporal.recent_session(recs, NOW, TZ)
        self.assertEqual([r["id"] for r in hits], ["a", "b"])

    def test_recent_session_empty(self):
        self.assertIsNone(temporal.recent_session([], NOW, TZ))

    def test_session_when_phrase_adapts_to_recency(self):
        # 「剛剛」有語感上限：6h 內才叫剛剛，久了改時段相對（今天中午/昨晚/前天/N 天前），不再誤稱剛剛
        now_l = NOW.astimezone(TZ)                       # 06/15 20:00 台北（晚上）
        self.assertEqual(temporal.session_when_phrase(now_l - timedelta(minutes=40), now_l), "剛剛這段")
        self.assertEqual(temporal.session_when_phrase(now_l - timedelta(hours=5), now_l), "剛剛這段")
        self.assertEqual(temporal.session_when_phrase(now_l - timedelta(hours=8), now_l), "今天中午那段")   # 12:00
        self.assertEqual(temporal.session_when_phrase(now_l - timedelta(hours=23), now_l), "昨晚那段")      # 06/14 21:00
        self.assertIn("前天", temporal.session_when_phrase(now_l - timedelta(days=2), now_l))
        self.assertEqual(temporal.session_when_phrase(now_l - timedelta(days=5), now_l), "5 天前那段")

    def test_recent_session_old_batch_uses_relative_when(self):
        # 截圖根因：昨晚 21:38 那批，到隔天回頭問，標籤不該叫「剛剛」，要用時段相對（昨晚）
        recs = [{"id": "x", "ts": iso(NOW - timedelta(hours=22, minutes=22)), "type": "text", "text": "昨晚寫的"}]
        _, _, label, hits = temporal.recent_session(recs, NOW, TZ)
        self.assertNotIn("剛剛", label)
        self.assertIn("昨晚", label)
        self.assertEqual([r["id"] for r in hits], ["x"])

    def test_resolve_range_routes_just_now_and_others(self):
        recs = [
            {"id": "x", "ts": iso(NOW - timedelta(minutes=5)), "type": "text", "text": "剛寫"},
            {"id": "y", "ts": iso(NOW - timedelta(days=10)), "type": "text", "text": "很久以前"},
        ]
        _, _, label, hits = temporal.resolve_range("剛剛寫了哪些", recs, NOW, TZ)
        self.assertIn("剛剛這段", label)
        self.assertEqual([r["id"] for r in hits], ["x"])
        # 非「剛剛」仍走原本的解析
        _, _, label2, _ = temporal.resolve_range("今天", recs, NOW, TZ)
        self.assertIn("今天", label2)
        self.assertIsNone(temporal.resolve_range("最好的歷程", recs, NOW, TZ))

    def test_anaphoric_resolves_instead_of_clarifying(self):
        recs = [
            {"id": "x", "ts": iso(NOW - timedelta(minutes=5)), "type": "text", "text": "剛寫"},
            {"id": "y", "ts": iso(NOW - timedelta(days=10)), "type": "text", "text": "很久以前"},
        ]
        self.assertTrue(temporal.is_anaphoric("那時候我寫了什麼"))
        self.assertFalse(temporal.is_anaphoric("今天我寫了什麼"))
        # 沒錨 → 退「最後一次記寫那段」（而非解析失敗）
        r = temporal.resolve_range("那時候我寫了什麼", recs, NOW, TZ)
        self.assertIsNotNone(r)
        self.assertIn("最後一次記寫那段", r[2])
        # 有對話錨 → 沿用該錨的標籤
        anchor = (NOW - timedelta(days=1), NOW, "上週（06/08起）")
        r2 = temporal.resolve_range("那時候呢", recs, NOW, TZ, anchor=anchor)
        self.assertEqual(r2[2], "上週（06/08起）")

    def test_fresh_deixis_detection(self):
        self.assertTrue(temporal.is_fresh_deixis("新東西是什麼"))
        self.assertTrue(temporal.is_fresh_deixis("剛進來的是什麼"))
        self.assertFalse(temporal.is_fresh_deixis("今天寫了什麼"))


class NextClockEpochTest(unittest.TestCase):
    """🤝 排程承諾的時間解析：把『八點／晚上八點／十分鐘後／明天X點』解析成下一個該時刻的 epoch。"""

    def _local(self, h, m=0, day=15):
        # 給定本地（Taipei）時刻的 aware datetime
        return datetime(2026, 6, day, h, m, tzinfo=TZ)

    def _epoch_local(self, text, now_local):
        ts = temporal.next_clock_epoch(text, now_local.astimezone(timezone.utc), TZ)
        if ts is None:
            return None
        return datetime.fromtimestamp(ts, timezone.utc).astimezone(TZ)

    def test_plain_eight_before_evening_means_today_2000(self):
        # 現在 19:00 說「八點」→ 今天 20:00（未過的最近八點，非隔天早上）
        out = self._epoch_local("等一下八點跟我打招呼", self._local(19))
        self.assertEqual((out.day, out.hour, out.minute), (15, 20, 0))

    def test_plain_eight_after_2000_means_tomorrow_0800(self):
        # 現在 21:00 說「八點」→ 今天 08:00 與 20:00 都過了 → 明天 08:00
        out = self._epoch_local("八點打招呼", self._local(21))
        self.assertEqual((out.day, out.hour, out.minute), (16, 8, 0))

    def test_plain_eight_morning_before_0800_means_today_0800(self):
        # 現在 06:00 說「八點」→ 今天 08:00（最近的未來八點）
        out = self._epoch_local("八點提醒我", self._local(6))
        self.assertEqual((out.day, out.hour, out.minute), (15, 8, 0))

    def test_evening_eight_always_2000(self):
        # 「晚上八點」無論現在幾點＝20:00（不是 08:00）→ 深夜政策才不會誤擾
        out = self._epoch_local("晚上八點跟我打招呼", self._local(10))
        self.assertEqual((out.day, out.hour, out.minute), (15, 20, 0))

    def test_evening_eight_already_past_means_tomorrow(self):
        out = self._epoch_local("晚上八點提醒我", self._local(22))
        self.assertEqual((out.day, out.hour, out.minute), (16, 20, 0))

    def test_morning_eight_explicit(self):
        # 「早上八點」強制上午時段＝08:00
        out = self._epoch_local("明天早上八點跟我說", self._local(9))
        self.assertEqual((out.day, out.hour), (16, 8))
        # 「早上」未帶數字時無鐘點 → 應 None（沒有 X 點＝不是可兌現的排程承諾）
        self.assertIsNone(self._epoch_local("明天早上跟我說", self._local(9)))

    def test_relative_ten_minutes(self):
        out = self._epoch_local("十分鐘後提醒我", self._local(14, 0))
        self.assertEqual((out.hour, out.minute), (14, 10))

    def test_relative_arabic_minutes(self):
        out = self._epoch_local("30分鐘後跟我說", self._local(14, 0))
        self.assertEqual((out.hour, out.minute), (14, 30))

    def test_relative_hours(self):
        out = self._epoch_local("兩小時後打招呼", self._local(14, 0))
        self.assertEqual((out.day, out.hour), (15, 16))

    def test_half_hour(self):
        out = self._epoch_local("半小時後提醒我", self._local(14, 0))
        self.assertEqual((out.hour, out.minute), (14, 30))

    def test_tomorrow_clock(self):
        out = self._epoch_local("明天九點跟我說", self._local(14, 0))
        self.assertEqual((out.day, out.hour), (16, 9))

    def test_tomorrow_evening_clock(self):
        out = self._epoch_local("明天晚上八點打招呼", self._local(14, 0))
        self.assertEqual((out.day, out.hour), (16, 20))

    def test_half_clock(self):
        # 「八點半」＝今天 20:30（現在 19:00）
        out = self._epoch_local("八點半跟我打招呼", self._local(19))
        self.assertEqual((out.hour, out.minute), (20, 30))

    def test_non_time_returns_none(self):
        self.assertIsNone(self._epoch_local("有感覺再跟我說", self._local(14)))
        self.assertIsNone(self._epoch_local("你好嗎", self._local(14)))

    def test_tz_none_returns_none(self):
        self.assertIsNone(temporal.next_clock_epoch("八點打招呼", NOW, None))

    def test_now_none_returns_none(self):
        self.assertIsNone(temporal.next_clock_epoch("八點打招呼", None, TZ))

    # 🔢 數字 H:MM（截圖根因：1:30/3:00/3:30 沒被排程記下，因只認中文「X點」）
    def test_numeric_hhmm_ambiguous_picks_first_future(self):
        # 3:30 在 12:33 → 今天 15:30（03:30 已過、15:30 未來）
        self.assertEqual(self._epoch_local("3:30 跟我打招呼", self._local(12, 33)), self._local(15, 30))

    def test_numeric_hhmm_before_morning_means_today_am(self):
        # 3:30 在 02:00 → 今天 03:30（最近的未來）
        self.assertEqual(self._epoch_local("3:30 跟我打招呼", self._local(2, 0)), self._local(3, 30))

    def test_numeric_hhmm_24h_explicit(self):
        # 15:30＝24h 制，固定下午（不歧義）
        self.assertEqual(self._epoch_local("15:30 跟我打招呼", self._local(9, 0)), self._local(15, 30))
        # 已過 → 明天同時刻
        self.assertEqual(self._epoch_local("15:30 跟我打招呼", self._local(16, 0)), self._local(15, 30, day=16))

    def test_numeric_hhmm_with_evening_word(self):
        self.assertEqual(self._epoch_local("晚上 8:30 跟我打招呼", self._local(7, 0)), self._local(20, 30))

    def test_numeric_one_thirty(self):
        # 1:30 在 12:36 → 今天 13:30
        self.assertEqual(self._epoch_local("1:30 也要回報我一次", self._local(12, 36)), self._local(13, 30))

    def test_date_slash_not_mistaken_for_time(self):
        self.assertIsNone(self._epoch_local("06/28 寫了什麼", self._local(12)))

    def test_all_clock_epochs_multiple_in_one_message(self):
        # 「3:00、3:30 都要打招呼」→ 記成兩筆（由早到晚）
        now = self._local(14, 38)
        eps = temporal.all_clock_epochs("等一下 3:00、3:30 都要跟我打招呼", now.astimezone(timezone.utc), TZ)
        got = [datetime.fromtimestamp(e, timezone.utc).astimezone(TZ) for e in eps]
        self.assertEqual(got, [self._local(15, 0), self._local(15, 30)])


if __name__ == "__main__":
    unittest.main()
