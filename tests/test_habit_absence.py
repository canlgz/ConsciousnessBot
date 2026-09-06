"""🌾 §1.79 習慣缺席暗示（HABIT_ABSENCE）：日課今天還沒看到 → 一句好奇（**不是提醒**）。

使用者需求：「一旦有明確的作息習慣資料後，如果 bot 發現平常使用者有做的事情沒做，需要主動詢問
或者暗示（不是提醒）……bot 能感覺到，明明是例行的事情，為什麼還沒看到使用者去做。」

設計前提＝**不對稱失敗**：偽陰性沒人發現；偽陽性（他其實做了／只是今天晚一點）＝待辦追殺＋
複製 §1.63/§1.70B/§1.77 的語意讀反前科。所以全層 fail-closed。本檔把每一條抑制路徑都釘死。
全 stub、零網路。
"""

import os
import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from telegram_monitor import habits, monitor, persona
from telegram_monitor.state import State

TZ = timezone(timedelta(hours=8))
BASE = datetime(2026, 7, 26, tzinfo=TZ)
LABEL = "閱讀｜讀誦經書"


def _rec(days_ago, hh, mm, label=LABEL):
    d = (BASE - timedelta(days=days_ago)).replace(hour=hh, minute=mm)
    return {"topicLabel": label, "ts": d.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"), "text": "x"}


def _hist(n=30):
    """30 天穩定早上 07:20–08:00 的日課。"""
    return [_rec(d, 7, 20 + (d % 40)) for d in range(1, n + 1)]


def _recs(extra=(), today_other=True):
    r = _hist()
    if today_other:
        r.append(_rec(0, 8, 0, "生活｜雜記"))      # 今天有別的筆＝快照看得見今天
    return r + list(extra)


def _now(h, m=0):
    return BASE.replace(hour=h, minute=m).timestamp()


class ProfileTest(unittest.TestCase):
    def test_stable_routine_qualifies(self):
        p = habits.abs_routine_profile(habits.abs_topic_daily_samples(_recs(), LABEL, TZ, _now(10)), _now(10), TZ)
        self.assertIsNotNone(p)
        self.assertEqual(p["n_days"], 30)
        self.assertLessEqual(p["iqr"], habits._ABS_MAX_IQR_MIN)

    def test_too_few_days_rejected(self):
        r = [_rec(d, 7, 30) for d in range(1, 8)] + [_rec(0, 8, 0, "生活｜雜記")]
        self.assertIsNone(habits.abs_routine_profile(
            habits.abs_topic_daily_samples(r, LABEL, TZ, _now(10)), _now(10), TZ))

    def test_scattered_times_rejected(self):
        # 每天都有寫，但時間從早到晚亂跑＝沒有「平常幾點」可言
        r = [_rec(d, 6 + (d * 7) % 14, 0) for d in range(1, 31)] + [_rec(0, 8, 0, "生活｜雜記")]
        self.assertIsNone(habits.abs_routine_profile(
            habits.abs_topic_daily_samples(r, LABEL, TZ, _now(10)), _now(10), TZ))

    def test_decaying_routine_rejected(self):
        # 前陣子天天做、最近一週停了＝在衰退，不要戳
        r = [_rec(d, 7, 30) for d in range(10, 41)] + [_rec(0, 8, 0, "生活｜雜記")]
        self.assertIsNone(habits.abs_routine_profile(
            habits.abs_topic_daily_samples(r, LABEL, TZ, _now(10)), _now(10), TZ))

    def test_exact_label_grouping(self):
        # 「閱讀」與「閱讀｜讀誦經書」不得互相汙染（完全相等分組）
        s = habits.abs_topic_daily_samples(_recs([_rec(1, 22, 0, "閱讀")]), "閱讀", TZ, _now(10))
        self.assertEqual(len(s), 1)


class HealthTest(unittest.TestCase):
    def test_snapshot_without_today_blocks(self):
        h = habits.abs_records_health(_recs(today_other=False), TZ, _now(10))
        self.assertFalse(h["ok"])
        self.assertEqual(h["reason"], "snapshot_missing_today")   # 快照還看不見今天＝不能判缺席

    def test_pending_filing_blocks(self):
        r = _recs() + [{"topicLabel": "", "ts": (BASE.replace(hour=9)).astimezone(timezone.utc)
                        .isoformat().replace("+00:00", "Z"), "text": "剛寫的"}]
        h = habits.abs_records_health(r, TZ, _now(10))
        self.assertFalse(h["ok"])
        self.assertEqual(h["reason"], "pending_filing")           # 今天有未歸戶的筆＝那筆可能就是這條線

    def test_stale_snapshot_blocks(self):
        h = habits.abs_records_health(_recs(), TZ, _now(10), ingest_ts=_now(10) - 8 * 3600)
        self.assertFalse(h["ok"])
        self.assertEqual(h["reason"], "stale_snapshot")

    def test_morning_only_writer_not_stale(self):
        # 釘住實測踩到的坑：他只在早上寫，晚上「最新一筆」很舊——那不是資料舊，是沒有新的。
        # 判 stale 必須看**快照抓取時刻**，否則這機制對晨型使用者永遠不觸發。
        h = habits.abs_records_health(_recs(), TZ, _now(20), ingest_ts=_now(20) - 300)
        self.assertTrue(h["ok"])

    def test_unparsable_blocks(self):
        h = habits.abs_records_health([{"topicLabel": "x", "ts": None}], TZ, _now(10))
        self.assertFalse(h["ok"])
        self.assertEqual(h["reason"], "unparsable")


class VerdictTest(unittest.TestCase):
    """六種情境（用真實 profile 跑 abs_candidates）——只有第一種該開口。"""

    def _cands(self, now_h, now_m, first_min, extra=()):
        n = _now(now_h, now_m)
        return habits.abs_candidates(_recs(extra), TZ, n, today_first_min=first_min, ingest_ts=n - 300)

    def test_absent_in_window_fires(self):
        c = self._cands(10, 0, 8 * 60)
        self.assertTrue(c)
        self.assertEqual(c[0]["label"], LABEL)
        self.assertGreater(c[0]["overdue"], 0)

    def test_too_early_silent(self):
        self.assertEqual(self._cands(8, 30, 7 * 60), [])           # 還沒到 due

    def test_done_today_silent(self):
        self.assertEqual(self._cands(10, 0, 8 * 60, extra=[_rec(0, 7, 35)]), [])

    def test_just_arrived_silent(self):
        self.assertEqual(self._cands(10, 0, 9 * 60 + 30), [])      # 今天才活動 30 分＝還早

    def test_window_closed_silent(self):
        self.assertEqual(self._cands(15, 0, 8 * 60), [])           # 過窗＝永遠沉默、不補問

    def test_user_absent_today_silent(self):
        self.assertEqual(self._cands(10, 0, None), [])             # 他今天根本沒出現＝人不在，不是習慣缺席

    def test_skip_labels_respected(self):
        n = _now(10)
        self.assertEqual(habits.abs_candidates(_recs(), TZ, n, today_first_min=8 * 60,
                                               skip_labels=[LABEL], ingest_ts=n - 300), [])


class WordingTest(unittest.TestCase):
    def test_rule_is_wonder_not_reminder(self):
        r = persona.habit_absence_rule(LABEL, "07:40", 47)
        self.assertIn("不是提醒", r)
        self.assertIn("絕不斷言", r)
        self.assertIn("可以輕輕問一句", r)     # 🌾 §1.80 措辭鬆綁：使用者原話「主動詢問…閒聊了解為何」＝問是可以的
        self.assertIn("不逼問", r)             # 禁的是逼問/催辦，不是問本身
        self.assertIn("我還沒看到", r)
        self.assertNotIn("記得去", r)                              # 不出現催辦句式


class EmitTest(unittest.TestCase):
    class Cl:
        def __init__(self):
            self.sent, self.dry_run = [], False

        def send(self, t):
            self.sent.append(t)
            return True

    def setUp(self):
        monitor._TURN.clear()
        monitor._TURN["bubbles"] = None
        monitor._SENT_RECENT.clear()

    def _state(self, first_min=8 * 60):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.entropy = SimpleNamespace(mood=0.0, arousal=0.0, hunger=0.3)
        s.last_user_msg_ts = _now(10) - 4 * 3600            # 不在場（可主動）
        s.last_push_ts = 0
        s.coupling = None
        d = BASE.replace(hour=first_min // 60, minute=first_min % 60)
        s.habit_events = [{"k": "msg", "ts": d.timestamp()}]
        return s

    def _cfg(self, on=True):
        return SimpleNamespace(habit_absence_enabled=on, timezone="Asia/Taipei", dry_run=False,
                               notify_cooldown_min=30)

    def _data(self, extra=()):
        return {"records": _recs(extra),
                "meta": {"lastIngestTs": datetime.fromtimestamp(_now(10) - 300, timezone.utc)
                         .isoformat().replace("+00:00", "Z")}}

    def _coach(self, voice="今天還沒看到你那條讀經的線，我這邊安安靜靜的，有點好奇。"):
        c = SimpleNamespace(enabled=True, asked=[])

        def reply(q, *a, **k):
            c.asked.append(q)
            return voice

        c.reply = reply
        return c

    def test_fires_and_books_ledger(self):
        s, cl, co = self._state(), self.Cl(), self._coach()
        monitor._habit_absence_emit(cl, s, self._cfg(), co, BASE.replace(hour=10), data=self._data())
        out = "".join(cl.sent)
        self.assertIn("好奇", out)
        self.assertTrue(out.startswith("🌾 "))
        self.assertEqual(s.habit_absence[LABEL]["day"], "2026-07-26")     # 台帳記上＝今天不再問
        self.assertIn("不是提醒", co.asked[0])

    def test_same_day_asked_once(self):
        s, co = self._state(), self._coach()
        s.habit_absence = {LABEL: {"day": "2026-07-26", "ts": _now(9)}}
        cl = self.Cl()
        monitor._habit_absence_emit(cl, s, self._cfg(), co, BASE.replace(hour=10), data=self._data())
        self.assertEqual(cl.sent, [])

    def test_user_present_suppresses(self):
        s, cl = self._state(), self.Cl()
        s.last_user_msg_ts = _now(10) - 30                  # 剛說過話＝互動優先
        monitor._habit_absence_emit(cl, s, self._cfg(), self._coach(), BASE.replace(hour=10), data=self._data())
        self.assertEqual(cl.sent, [])

    def test_own_cooldown_suppresses(self):
        s, cl = self._state(), self.Cl()
        s.last_habit_absence_ts = _now(10) - 3600           # 一小時前才問過（自有 20h 冷卻）
        monitor._habit_absence_emit(cl, s, self._cfg(), self._coach(), BASE.replace(hour=10), data=self._data())
        self.assertEqual(cl.sent, [])

    def test_flag_off_bitwise(self):
        s, cl = self._state(), self.Cl()
        monitor._habit_absence_emit(cl, s, self._cfg(on=False), self._coach(), BASE.replace(hour=10),
                                    data=self._data())
        self.assertEqual(cl.sent, [])
        self.assertFalse(getattr(s, "habit_absence", {}))

    def test_no_coach_silent(self):
        s, cl = self._state(), self.Cl()
        monitor._habit_absence_emit(cl, s, self._cfg(), None, BASE.replace(hour=10), data=self._data())
        self.assertEqual(cl.sent, [])


class AuditTest(unittest.TestCase):
    def test_audit_explains_why_silent(self):
        s = SimpleNamespace(habit_events=[{"k": "msg", "ts": _now(8)}], habit_absence={})
        cfg = SimpleNamespace(habit_absence_enabled=True)
        data = {"records": _recs(), "meta": {}}
        out = monitor._habit_absence_audit(s, cfg, data, _now(10), TZ)
        self.assertIn("日課偵測", out)
        self.assertIn(LABEL, out)
        self.assertIn("我會等到", out)                       # 講得出「幾點才覺得怪」

    def test_audit_reports_unhealthy_data(self):
        s = SimpleNamespace(habit_events=[], habit_absence={})
        cfg = SimpleNamespace(habit_absence_enabled=True)
        out = monitor._habit_absence_audit(s, cfg, {"records": _recs(today_other=False)}, _now(10), TZ)
        self.assertIn("還不能判", out)


class PersistTest(unittest.TestCase):
    def test_ledger_survives_save_load(self):
        path = os.path.join(tempfile.mkdtemp(), "s.json")
        s = State(path)
        s.habit_absence = {LABEL: {"day": "2026-07-26", "ts": 1.0}}
        s.save()
        self.assertEqual(State.load(path).habit_absence[LABEL]["day"], "2026-07-26")


class ConfigTest(unittest.TestCase):
    def test_config_synced(self):
        src = open("telegram_monitor/config.py", encoding="utf-8").read()
        self.assertIn("HABIT_ABSENCE", src)
        self.assertIn("habit_absence_enabled", src)
        env = open(".env.example", encoding="utf-8").read()
        self.assertRegex(env, re.compile(r"^HABIT_ABSENCE=1", re.M))
        self.assertIn("HABIT_ABSENCE", open("README.md", encoding="utf-8").read())

    def test_getattr_defaults_false(self):
        self.assertFalse(getattr(SimpleNamespace(), "habit_absence_enabled", False))


if __name__ == "__main__":
    unittest.main()
