"""⏱️ §1.04 主動提起某條線的**真實時間感**：開場照真實記寫時間講、LLM 掰「很久沒」就退回誠實模板。

截圖根因：使用者 09:37 才寫過〔讀誦經書〕，bot 14:34 主動說「很久沒聽到你提起了」——舊模板寫死
「好一陣子沒聊了」（毫無資料根據）、LLM 再放大成「很久沒聽到」。時間感必須來自真實 records。
"""

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from telegram_monitor import lifeloop, monitor, volition
from telegram_monitor.state import State

NOW = datetime(2026, 7, 9, 6, 34, tzinfo=timezone.utc)   # 14:34 台北
GOAL = {"id": "understand:讀誦經書", "kind": "understand_topic", "subject": "讀誦經書",
        "desire": "想搞懂", "plan": ["問"], "status": "active", "progress": 0.0,
        "touches": 0, "born_ts": NOW.timestamp() - 86400, "last_advance_ts": NOW.timestamp() - 86400}


class RecencyPhraseTest(unittest.TestCase):
    def test_buckets(self):
        self.assertEqual(volition.recency_phrase(5), "今天才又寫到")
        self.assertEqual(volition.recency_phrase(30), "昨天才寫到")
        self.assertEqual(volition.recency_phrase(60), "前兩天才寫到")
        self.assertIn("5 天沒看到", volition.recency_phrase(126))
        self.assertEqual(volition.recency_phrase(None), "")


class StaleClaimGuardTest(unittest.TestCase):
    def test_claim_vs_fresh_conflicts(self):
        self.assertTrue(volition.stale_claim_conflicts("很久沒聽到你提起了。", 5))
        self.assertTrue(volition.stale_claim_conflicts("好一陣子沒聊這條了", 24))

    def test_claim_unverifiable_conflicts(self):
        self.assertTrue(volition.stale_claim_conflicts("好久沒聽你說了", None))   # 查不到＝無法查證＝不准宣稱

    def test_truly_stale_allowed(self):
        self.assertFalse(volition.stale_claim_conflicts("很久沒聽到你提起了。", 200))

    def test_no_claim_no_conflict(self):
        self.assertFalse(volition.stale_claim_conflicts("你今天寫的那條我很好奇", 5))
        self.assertFalse(volition.stale_claim_conflicts("", 5))


class ReachOutLineTest(unittest.TestCase):
    def test_fresh_topic_tells_truth(self):
        line = volition.reach_out_line(GOAL, hours=5)
        self.assertIn("今天才又寫到", line)
        self.assertNotIn("好一陣子", line)                    # 剛寫過就不掰「久」

    def test_unknown_makes_no_time_claim(self):
        line = volition.reach_out_line(GOAL, hours=None)
        for w in ("好一陣子", "很久沒", "好久沒", "天沒看到"):
            self.assertNotIn(w, line)                          # 不知道＝完全不做時間宣稱

    def test_truly_stale_says_days(self):
        self.assertIn("有 5 天沒看到你寫它了", volition.reach_out_line(GOAL, hours=126))

    def test_flag_off_bitwise_old(self):
        self.assertEqual(volition.reach_out_line(GOAL, hours=5, time_ground=False),
                         "欸，我私下一直想弄懂你「讀誦經書」那條——好一陣子沒聊了，哪天有空跟我說說它對你是什麼？")


class TopicLatestHoursTest(unittest.TestCase):
    def test_raw_string_ts_does_not_crash(self):
        # 🩹 §1.07 死亡回歸：cycle["data"]["records"] 是 drive_reader 原始筆、ts 是 ISO **字串**
        # （只有 snap.filed_records 走過 analyzer.parse_ts 才是 datetime）。原本直接 now-ts →
        # TypeError: datetime - str → 「感覺」環炸開＝bot 終局死亡。
        recs = [{"topicLabel": "讀誦經書", "ts": "2026-07-09T01:37:00Z"},
                {"topicLabel": "讀誦經書", "ts": "2026-07-08T01:00:00+00:00"}]
        self.assertAlmostEqual(monitor._topic_latest_hours(recs, "讀誦經書", NOW), 4.95, places=1)

    def test_bad_or_missing_ts_is_unknown_not_crash(self):
        for recs in ([{"topicLabel": "X", "ts": "not-a-date"}],
                     [{"topicLabel": "X", "ts": ""}],
                     [{"topicLabel": "X", "ts": True}],
                     [{"topicLabel": "X"}]):
            self.assertIsNone(monitor._topic_latest_hours(recs, "X", NOW), recs)

    def test_naive_datetimes_do_not_crash(self):
        recs = [{"topicLabel": "X", "ts": (NOW - timedelta(hours=3)).replace(tzinfo=None)}]
        self.assertAlmostEqual(monitor._topic_latest_hours(recs, "X", NOW), 3.0, places=2)
        recs2 = [{"topicLabel": "X", "ts": NOW - timedelta(hours=3)}]
        self.assertAlmostEqual(monitor._topic_latest_hours(recs2, "X", NOW.replace(tzinfo=None)), 3.0, places=2)

    def test_spontaneous_emit_survives_raw_records(self):
        # 整合回歸：帶原始字串 ts 的 records 走完 _spontaneous_emit（＝「感覺」環）不炸
        import os, tempfile
        from telegram_monitor import lifeloop
        from telegram_monitor.state import State
        from unittest.mock import patch
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        s.entropy = lifeloop.EntropyState()
        s.self_state = {"gate": 2}
        s.goals = [dict(GOAL)]
        recs = [{"topicLabel": "讀誦經書", "ts": "2026-07-09T01:37:00Z"}]
        c = SimpleNamespace(sent=[], dry_run=True)
        c.send = lambda t: c.sent.append(t) or True
        c.send_typing = lambda: None
        coach = SimpleNamespace(enabled=True,
                                voice_spontaneous=lambda seed, hist, coping="", time_rule="": seed)
        cfg = SimpleNamespace(notify_cooldown_min=0, spontaneous_cooldown_min=0,
                              spontaneous_quiet_after_chat_min=0, spontaneous_dedup=False,
                              spontaneous_coping_enabled=False, reachout_time_ground_enabled=True,
                              always_sticker_enabled=False, send_stickers=False, dry_run=True)
        with patch.object(monitor, "_proactive_ok", lambda *a, **k: True), \
             patch.object(monitor.lifeloop, "spontaneous_due", lambda *a, **k: True):
            monitor._spontaneous_emit(c, s, cfg, coach, NOW, records=recs)   # 不得拋 TypeError
        self.assertTrue(c.sent)
        self.assertIn("今天才又寫到", "".join(c.sent))       # 且時間感照真實字串 ts 算出來

    def test_matches_and_computes(self):
        recs = [{"topicLabel": "讀誦經書", "ts": NOW - timedelta(hours=5)},
                {"topicLabel": "讀誦經書", "ts": NOW - timedelta(hours=30)},   # 取最近那筆
                {"topicLabel": "別條", "ts": NOW - timedelta(hours=1)}]
        self.assertAlmostEqual(monitor._topic_latest_hours(recs, "讀誦經書", NOW), 5.0, places=2)

    def test_display_form_match(self):
        # 「類別｜標籤」與裸標籤互認（goal.subject 來自 context_title＝有類別時就是顯示名）——兩個方向都要通
        recs = [{"topicLabel": "閱讀｜讀誦經書", "ts": NOW - timedelta(hours=5)}]
        self.assertIsNotNone(monitor._topic_latest_hours(recs, "讀誦經書", NOW))
        recs2 = [{"topicLabel": "讀誦經書", "category": "閱讀", "ts": NOW - timedelta(hours=5)}]
        self.assertIsNotNone(monitor._topic_latest_hours(recs2, "閱讀｜讀誦經書", NOW))

    def test_no_substring_bleed(self):
        # 🫧 §1.98：舊的寬鬆比對（互為子字串）會讓「閱讀」取到「閱讀習慣」那條線 → 引錯線的原文/算錯多久沒提
        recs = [{"topicLabel": "閱讀習慣", "ts": NOW - timedelta(hours=5)}]
        self.assertIsNone(monitor._topic_latest_hours(recs, "閱讀", NOW))
        self.assertIsNone(monitor._topic_latest_hours(recs, "閱讀習慣加長版", NOW))
        # 兩邊都帶類別時必須完全相等（同名標籤掛在不同類別下＝不同的線）
        self.assertIsNone(monitor._topic_latest_hours(
            [{"topicLabel": "閱讀｜心得", "ts": NOW - timedelta(hours=5)}], "教學｜心得", NOW))

    def test_unknown_cases(self):
        self.assertIsNone(monitor._topic_latest_hours([], "讀誦經書", NOW))
        self.assertIsNone(monitor._topic_latest_hours([{"topicLabel": "讀誦經書", "ts": None}], "讀誦經書", NOW))
        self.assertIsNone(monitor._topic_latest_hours(None, "", NOW))


class SpontaneousGuardIntegrationTest(unittest.TestCase):
    """整合：goal 伸手＋那條線 5 小時前才寫過＋LLM 掰「很久沒聽到你提起」→ 退回誠實模板；prompt 收到時間事實。"""

    def _run(self, voice_out, hours=5):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        s.entropy = lifeloop.EntropyState()
        s.entropy.last_revisited_topic = "讀誦經書"
        s.self_state = {"gate": 2}
        s.goals = [dict(GOAL)]
        recs = [{"topicLabel": "讀誦經書", "ts": NOW - timedelta(hours=hours)}] if hours is not None else []
        c = SimpleNamespace(sent=[], dry_run=True)
        c.send = lambda t: c.sent.append(t) or True
        c.send_typing = lambda: None
        cap = {}
        coach = SimpleNamespace(enabled=True,
                                voice_spontaneous=lambda seed, hist, coping="", time_rule="":
                                (cap.update(seed=seed, time_rule=time_rule) or voice_out))
        cfg = SimpleNamespace(notify_cooldown_min=0, spontaneous_cooldown_min=0,
                              spontaneous_quiet_after_chat_min=0, spontaneous_dedup=False,
                              spontaneous_coping_enabled=False, reachout_time_ground_enabled=True,
                              always_sticker_enabled=False, send_stickers=False, dry_run=True,
                              spontaneous_h_thresh=0.0, spontaneous_min_ruminations=0,
                              spontaneous_mood_share=9.9, spontaneous_mood_min_rumin=99)
        with patch.object(monitor, "_proactive_ok", lambda *a, **k: True), \
             patch.object(monitor.lifeloop, "spontaneous_due", lambda *a, **k: True):
            monitor._spontaneous_emit(c, s, cfg, coach, NOW, records=recs)
        return c, cap

    def test_fabricated_staleness_falls_back_to_honest_seed(self):
        c, cap = self._run("唉，我很久沒聽到你提起「讀誦經書」了，跟我說說？", hours=5)
        said = "".join(c.sent)
        self.assertTrue(c.sent)
        self.assertNotIn("很久沒聽到", said)                  # 掰的被守門擋下
        self.assertIn("今天才又寫到", said)                    # 退回照事實講的模板
        self.assertIn("時間事實", cap["time_rule"])           # prompt 有掛真實時間
        self.assertIn("5 小時前", cap["time_rule"])

    def test_honest_voice_passes_through(self):
        c, _ = self._run("你今天早上又寫到讀經了，我一直很想懂它對你是什麼。", hours=5)
        self.assertIn("今天早上", "".join(c.sent))            # 沒掰＝放行 LLM 版


if __name__ == "__main__":
    unittest.main()
