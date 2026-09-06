# -*- coding: utf-8 -*-
"""🌾 §2.15 習慣／作息管線的端到端驗收＋一個持久化的洞。

使用者：「之前有要你去設計 bot 觀察與偵測使用者的習慣跟作息，這部分有做好嗎，通盤檢查一下。」
這一族（§1.42/§1.63/§1.79/§1.80/§1.96/§1.97）的單元測試很多，但**從資格到出聲的整條鏈**
從來沒有一條測試走完過——而「無聲架空」前科最多的正是這一族。本檔用貼近真實的資料把它走完。

通盤檢查的結論（其餘逐項實測都沒事，僅一個實錘）：
  ・捕捉端 ✅（note 在 last_user_msg_ts 更新前、編輯不記、mention_guard 有傳）
  ・健康閘 ✅（ingest_ts 缺席退回 ①②③，不是死 lane）
  ・常駐卡 ✅（routine_card 進事實卡、講法守則 6 個注入點）
  ・旗標 ✅（六面全部 config 預設 True）
  ・❌ `last_habit_absence_ts`（20h 冷卻）**從來沒進 state.py 的 load/save**——最諷刺的是
    state.py:284 的註解半個月前就把它當「§1.79 前科」引用，卻沒人真的填。重啟一次冷卻歸零：
    per-line-per-day 台帳擋得住同一條線，擋不住「重啟後另一條合格的線又問一次」＝一天可能被問兩次。
全 stub、零網路。
"""

import os
import tempfile
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from telegram_monitor import habits, monitor
from telegram_monitor.state import State

TZ = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 7, 29, 10, 0, tzinfo=TZ)   # 今天 10:00＝在節奏窗內、離 21:00 硬上限遠
NOW_TS = NOW.timestamp()


def _recs(include_today_line=False):
    """16 天每天早上寫「讀誦經書」；今天另有一筆別的線（快照看得見今天）。"""
    out = []
    for d in range(1, 17):
        t = (NOW - timedelta(days=d)).replace(hour=7, minute=10 + (d % 4) * 10)
        out.append({"topicLabel": "讀誦經書", "category": "閱讀", "text": f"第{d}天的讀經",
                    "ts": t.isoformat(), "id": f"r{d}"})
    out.append({"topicLabel": "晨間隨筆", "category": "生活", "text": "今天先寫了別的",
                "ts": NOW.replace(hour=8, minute=30).isoformat(), "id": "today1"})
    if include_today_line:
        out.append({"topicLabel": "讀誦經書", "category": "閱讀", "text": "今天也讀了",
                    "ts": NOW.replace(hour=7, minute=20).isoformat(), "id": "today2"})
    return out


def _state():
    s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
    s.habit_events = [{"k": "msg", "ts": (NOW - timedelta(days=d)).replace(hour=8, minute=0).timestamp()}
                      for d in range(1, 10)]
    s.habit_events.append({"k": "msg", "ts": NOW.replace(hour=8, minute=0).timestamp()})   # 今天出現過＝醒著 ≥90 分
    s.last_user_msg_ts = NOW.replace(hour=8, minute=0).timestamp()                          # 2h 前＝不在場
    s.last_push_ts = 0
    return s


def _cfg(**kw):
    d = dict(habit_absence_enabled=True, habit_absence_convo_enabled=True,
             habit_absence_one_thing_enabled=True, notify_cooldown_min=0,
             timezone="Asia/Taipei", dry_run=True, quiet_start=1, quiet_end=6,
             self_roster_enabled=False)
    d.update(kw)
    return SimpleNamespace(**d)


class Coach:
    enabled = True

    def __getattr__(self, n):
        return lambda *a, **k: "我今天到現在還沒看到你那條讀經的線，有點好奇是不是換了節奏。"


class Cl:
    dry_run = True

    def __init__(self):
        self.sent = []

    def send(self, t):
        self.sent.append(t)
        return True

    def send_typing(self):
        pass


def _data(recs):
    return {"records": recs, "meta": {"lastIngestTs": NOW.isoformat()}}


class AbsenceLaneSpeaksTest(unittest.TestCase):
    """從資格到出聲的整條鏈——這一族第一條真的走完的測試。"""

    def test_qualifying_line_gets_one_gentle_line(self):
        s, cl = _state(), Cl()
        monitor._habit_absence_emit(cl, s, _cfg(), Coach(), NOW, data=_data(_recs()))
        self.assertEqual(len(cl.sent), 1)
        self.assertTrue(cl.sent[0].startswith("🌾 "))
        self.assertIn("讀誦經書", s.habit_absence)                    # 台帳記了今天
        self.assertEqual(s.habit_absence_pick_n, 1)                   # 角度輪替推進

    def test_done_today_stays_silent(self):
        s, cl = _state(), Cl()
        monitor._habit_absence_emit(cl, s, _cfg(), Coach(), NOW, data=_data(_recs(include_today_line=True)))
        self.assertEqual(cl.sent, [])                                 # 今天寫了＝一個字都不說
        self.assertEqual(s.habit_absence_pick_n, 0)                   # 沉默不推進輪替

    def test_same_line_same_day_only_once(self):
        s, cl = _state(), Cl()
        data = _data(_recs())
        monitor._habit_absence_emit(cl, s, _cfg(), Coach(), NOW, data=data)
        s.last_habit_absence_ts = 0                                   # 就算冷卻被清掉（模擬重啟）
        monitor._habit_absence_emit(cl, s, _cfg(), Coach(), NOW + timedelta(minutes=30), data=data)
        self.assertEqual(len(cl.sent), 1)                             # 台帳仍擋住同一條線

    def test_user_present_stays_silent(self):
        s, cl = _state(), Cl()
        s.last_user_msg_ts = NOW_TS - 60                              # 一分鐘前才說過話＝在場
        monitor._habit_absence_emit(cl, s, _cfg(), Coach(), NOW, data=_data(_recs()))
        self.assertEqual(cl.sent, [])

    def test_stale_snapshot_stays_silent(self):
        s, cl = _state(), Cl()
        data = {"records": _recs(), "meta": {"lastIngestTs": (NOW - timedelta(hours=9)).isoformat()}}
        monitor._habit_absence_emit(cl, s, _cfg(), Coach(), NOW, data=data)
        self.assertEqual(cl.sent, [])                                 # 快照太舊＝寧可沉默


class CooldownPersistsTest(unittest.TestCase):
    """❌→✅ 這次通盤檢查唯一的實錘：20h 冷卻從來沒落盤（state.py 註解自己點名的前科）。"""

    def test_roundtrip(self):
        p = os.path.join(tempfile.mkdtemp(), "s.json")
        s = State(p)
        s.last_habit_absence_ts = 12345.0
        s.save()
        self.assertEqual(State.load(p).last_habit_absence_ts, 12345.0)

    def test_declared_on_fresh_state(self):
        self.assertEqual(State(os.path.join(tempfile.mkdtemp(), "s.json")).last_habit_absence_ts, 0)

    def test_cooldown_actually_blocks_after_rebirth(self):
        # 重啟後（load 回冷卻）：另一條合格的線也不得在 20h 內再問
        p = os.path.join(tempfile.mkdtemp(), "s.json")
        s = State(p)
        s.habit_events = _state().habit_events
        s.last_user_msg_ts = NOW.replace(hour=8, minute=0).timestamp()
        s.last_habit_absence_ts = NOW_TS - 3600                       # 一小時前才問過
        s.save()
        r = State.load(p)
        r.last_push_ts = 0
        cl = Cl()
        monitor._habit_absence_emit(cl, r, _cfg(), Coach(), NOW, data=_data(_recs()))
        self.assertEqual(cl.sent, [])                                 # 修前：load 丟掉冷卻 ⇒ 這裡會再問一次


if __name__ == "__main__":
    unittest.main()
