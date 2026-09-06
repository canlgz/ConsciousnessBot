"""📈 §1.63 習慣觀測修真（HABIT_OBS_FIX）：§1.42 的統計是真算的、但**量錯了東西**。

截圖根因：bot 說「你一天跟我說上第一句話多半落在 09:54–20:58 之間（中位 12:01、樣本 29 次）」，
使用者打臉「不準確吧，我常 6–7 點跟你說早安」。三個觀測洞：
 ① "first"（≥4h 安靜後的第一句）一天可記多筆（中午/晚上再現身都算）＝分佈涵蓋全天，卻掛「一天第一句」標籤
    → daily_first_stats 以本地日曆日分組、取每日最早出現事件（msg/contact）＝真「一天第一句」。
 ② 純貼圖/照片/語音會更新 last_user_msg_ts（重置 first 的安靜計時）卻不留事件＝清晨的貼圖早安自己隱形、
    還害後續文字 gap<4h 不算 first（早晨被雙重抹掉）→ note_contact 入帳 "contact" 事件。
 ③ 「我常跟你說早安喔」（20:23 講的）被 greeting.detect 判成問候＝記成一筆晚上的 greet_am 汙染統計
    ＋被 greeting lane 質疑「怎麼說早安，現在都晚上了」→ greeting.is_mention 不記＋導回 fact_or_chat。
另加 /habits 對帳指令（確定性、不經 LLM）。旗標關＝逐位元同現狀（回 §1.42 原統計）。全 stub、零網路。
"""

import os
import re
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from telegram_monitor import greeting, habits, monitor
from telegram_monitor.state import State

TZ = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 7, 21, 12, 24, 0, tzinfo=timezone.utc)   # 台北 7/21 20:24（截圖時刻附近）


def _ts(day, hh, mm):
    """7 月 day 日台北時間 hh:mm 的 epoch。"""
    return datetime(2026, 7, day, hh, mm, 0, tzinfo=TZ).timestamp()


class MentionTest(unittest.TestCase):
    """③ 「提及問候」≠「執行問候」：頻率/時態副詞＋說類動詞＋問候詞＝在談問候、不是在問候。"""

    def test_habit_statement_is_mention(self):
        self.assertTrue(greeting.is_mention("我常跟你說早安喔"))          # 截圖 20:23 原句
        self.assertTrue(greeting.is_mention("我每天都跟你說晚安"))
        self.assertTrue(greeting.is_mention("我以前都會跟你道晚安"))
        self.assertTrue(greeting.is_mention("我剛剛有跟你說早安嗎"))

    def test_performing_greeting_not_mention(self):
        self.assertFalse(greeting.is_mention("早安"))
        self.assertFalse(greeting.is_mention("跟你說聲早安"))             # 此刻在執行問候（無頻率/時態標記）
        self.assertFalse(greeting.is_mention("早安呀今天天氣好好"))
        self.assertFalse(greeting.is_mention(""))
        self.assertFalse(greeting.is_mention(None))

    def test_mention_still_detected_as_greeting_by_detect(self):
        # 前提確認：detect 對截圖原句確實誤判 morning（≤12 字含「早安」）——is_mention 才有存在必要
        self.assertEqual(greeting.detect("我常跟你說早安喔"), "morning")


class NoteGuardTest(unittest.TestCase):
    """③ note(mention_guard=True)：談問候的陳述不記 greet_*；真問候照記；旗標關＝現狀（照記）。"""

    def setUp(self):
        self.s = State(os.path.join(tempfile.mkdtemp(), "s.json"))

    def test_mention_not_recorded_with_guard(self):
        habits.note(self.s, "我常跟你說早安喔", _ts(21, 20, 23), mention_guard=True)
        kinds = {e["k"] for e in self.s.habit_events}
        self.assertNotIn("greet_am", kinds)                    # 晚上講的這句不再變成一筆「早安事件」
        self.assertIn("msg", kinds)                            # msg/first 照記（它仍是一則訊息）

    def test_mention_recorded_without_guard(self):
        habits.note(self.s, "我常跟你說早安喔", _ts(21, 20, 23))   # 預設＝現狀
        self.assertIn("greet_am", {e["k"] for e in self.s.habit_events})

    def test_real_greeting_still_recorded_with_guard(self):
        habits.note(self.s, "早安", _ts(21, 6, 30), mention_guard=True)
        self.assertIn("greet_am", {e["k"] for e in self.s.habit_events})


class ContactTest(unittest.TestCase):
    """② note_contact：貼圖/媒體接觸入帳 "contact" 事件（FIFO 上限共用）。"""

    def test_contact_recorded(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        habits.note_contact(s, _ts(21, 6, 30))
        self.assertEqual(s.habit_events, [{"k": "contact", "ts": _ts(21, 6, 30)}])

    def test_cap_enforced(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        for i in range(habits._CAP + 50):
            habits.note_contact(s, 1000.0 + i)
        self.assertEqual(len(s.habit_events), habits._CAP)
        self.assertEqual(s.habit_events[0]["ts"], 1000.0 + 50)   # 舊的先丟


class DailyFirstStatsTest(unittest.TestCase):
    """① daily_first_stats：本地日曆日分組、每日最早（msg/contact）——一天一筆，不再被晚間再現身拉爆。"""

    def _seed_day(self, s, day, hhmm_list, kind="msg"):
        for hh, mm in hhmm_list:
            s.habit_events.append({"k": kind, "ts": _ts(day, hh, mm)})

    def test_earliest_per_day_only(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        for d in (18, 19, 20):                                  # 三天：早 06:30、午 12:00、晚 20:00 都有訊息
            self._seed_day(s, d, [(6, 30), (12, 0), (20, 0)])
        st = habits.daily_first_stats(s.habit_events, TZ, NOW.timestamp())
        self.assertIsNotNone(st)
        n, med, lo, hi = st
        self.assertEqual(n, 3)                                  # 樣本＝天數，不是事件數
        self.assertEqual(med, 6 * 60 + 30)                      # 每天只取最早＝06:30
        self.assertEqual((lo, hi), (6 * 60 + 30, 6 * 60 + 30))  # 晚間的 12:00/20:00 進不了分佈

    def test_contact_counts_as_first(self):
        # 清晨貼圖（contact）06:30＋上午第一句文字 09:54 → 一天第一次出現＝06:30（不再是 09:54）
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        for d in (18, 19, 20):
            self._seed_day(s, d, [(6, 30)], kind="contact")
            self._seed_day(s, d, [(9, 54)])
        st = habits.daily_first_stats(s.habit_events, TZ, NOW.timestamp())
        self.assertEqual(st[1], 6 * 60 + 30)

    def test_greet_and_first_kinds_ignored(self):
        # 資料源只有 msg/contact——greet_am/first 是衍生標記、不重複入樣
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        for d in (18, 19, 20):
            s.habit_events.append({"k": "greet_am", "ts": _ts(d, 5, 0)})
            self._seed_day(s, d, [(6, 30)])
        st = habits.daily_first_stats(s.habit_events, TZ, NOW.timestamp())
        self.assertEqual(st[1], 6 * 60 + 30)                    # 05:00 的 greet_am 不算出現事件

    def test_insufficient_days_none(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        self._seed_day(s, 20, [(6, 30), (7, 0), (8, 0), (9, 0)])   # 4 筆但只有 1 天
        self.assertIsNone(habits.daily_first_stats(s.habit_events, TZ, NOW.timestamp()))

    def test_window_excludes_old_days(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        old = NOW.timestamp() - 60 * 86400
        for i in range(5):
            s.habit_events.append({"k": "msg", "ts": old + i * 86400})
        self.assertIsNone(habits.daily_first_stats(s.habit_events, TZ, NOW.timestamp()))

    def test_today_first_minutes_calendar_day(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.habit_events.append({"k": "msg", "ts": _ts(20, 23, 30)})   # 昨晚 23:30——舊 18h 窗會誤當「今天」
        self.assertIsNone(habits.today_first_minutes(s.habit_events, TZ, NOW.timestamp()))
        s.habit_events.append({"k": "contact", "ts": _ts(21, 6, 30)})
        s.habit_events.append({"k": "msg", "ts": _ts(21, 12, 0)})
        self.assertEqual(habits.today_first_minutes(s.habit_events, TZ, NOW.timestamp()), 6 * 60 + 30)


class ConsumerTest(unittest.TestCase):
    """三個消費端（habit_facts／today_vs_usual_line／claim_guard_data）換 daily_first；預設參數＝現狀。"""

    def _seed(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        for d in (17, 18, 19, 20):                              # 四天：真第一句 06:30；中午/晚上另有 first（舊統計的汙染源）
            s.habit_events.append({"k": "msg", "ts": _ts(d, 6, 30)})
            s.habit_events.append({"k": "first", "ts": _ts(d, 6, 30)})
            s.habit_events.append({"k": "msg", "ts": _ts(d, 13, 0)})
            s.habit_events.append({"k": "first", "ts": _ts(d, 13, 0)})
            s.habit_events.append({"k": "msg", "ts": _ts(d, 21, 0)})
            s.habit_events.append({"k": "first", "ts": _ts(d, 21, 0)})
        return s

    def test_claim_guard_daily_first(self):
        s = self._seed()
        g = habits.claim_guard_data(s, NOW.timestamp(), TZ, daily_first=True)
        self.assertEqual(g["first"][1], 6 * 60 + 30)            # 真「一天第一句」＝06:30
        g_old = habits.claim_guard_data(s, NOW.timestamp(), TZ)  # 現狀：分佈被 13:00/21:00 拉開
        self.assertGreater(g_old["first"][3], 12 * 60)

    def test_habit_facts_daily_first(self):
        s = self._seed()
        out = habits.habit_facts(s, [], NOW.timestamp(), TZ, daily_first=True)
        self.assertIn("06:30", out)
        self.assertNotIn("21:00", out)                          # 晚間再現身不再冒充「一天第一句」

    def test_today_vs_usual_daily_first(self):
        s = self._seed()
        s.habit_events.append({"k": "contact", "ts": _ts(21, 6, 0)})   # 今天清晨貼圖出現 06:00
        s.habit_events.append({"k": "msg", "ts": _ts(21, 12, 0)})
        line = habits.today_vs_usual_line(s, NOW.timestamp(), TZ, daily_first=True)
        self.assertIn("06:00", line)                            # 今天第一句＝真的最早（貼圖），不是 12:00
        self.assertIn("早", line)

    def test_default_params_unchanged(self):
        # 不帶新參數＝§1.42 原行為（旗標關的呼叫端就是這樣呼叫）
        s = self._seed()
        self.assertEqual(habits.claim_guard_data(s, NOW.timestamp(), TZ),
                         habits.claim_guard_data(s, NOW.timestamp(), TZ, daily_first=False))
        self.assertEqual(habits.habit_facts(s, [], NOW.timestamp(), TZ),
                         habits.habit_facts(s, [], NOW.timestamp(), TZ, daily_first=False))


class AuditTest(unittest.TestCase):
    """④ /habits 的 audit_text：事件量＋統計＋最近事件本地時刻（確定性、不經 LLM）。"""

    def test_audit_contains_counts_and_stats(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        for d in (18, 19, 20):
            s.habit_events.append({"k": "contact", "ts": _ts(d, 6, 30)})
            s.habit_events.append({"k": "msg", "ts": _ts(d, 9, 54)})
            s.habit_events.append({"k": "greet_am", "ts": _ts(d, 9, 54)})
        out = habits.audit_text(s, NOW.timestamp(), TZ)
        self.assertIn("習慣觀測對帳", out)
        self.assertIn("06:30", out)                             # daily-first 統計反映貼圖出現
        self.assertIn("貼圖/媒體 3", out)
        self.assertIn("07/20 09:54", out)                       # 最近事件的本地時刻

    def test_audit_empty_honest(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        out = habits.audit_text(s, NOW.timestamp(), TZ)
        self.assertIn("還沒有任何事件", out)


class EndToEndTest(unittest.TestCase):
    """handle_message 全路徑：貼圖入帳／mention 不記＋不進 greeting lane／/habits 指令；旗標關＝現狀。"""

    class Cl:
        def __init__(self):
            self.sent, self.dry_run = [], False

        def send(self, text):
            self.sent.append(text)
            return True

    SNAP = SimpleNamespace(summary={"total": 1, "last24h": 0, "last7d": 0, "streak": 0},
                           funnel={"candidate": 0, "context": 0, "journey": 0, "watch": 0},
                           heartbeat={"status": "ok"}, filed_records=[])
    BOOM = SimpleNamespace(load_embedding_records=lambda *_: [])

    def setUp(self):
        monitor._TURN.clear()
        monitor._TURN["bubbles"] = None

    def _cfg(self, on=True, ground=True):
        return SimpleNamespace(dry_run=False, telegram_chat_id="", timezone="Asia/Taipei",
                               notify_cooldown_min=30, user_habit_ground_enabled=ground,
                               habit_obs_fix_enabled=on)

    def _coach(self, voice="好喔。"):
        c = SimpleNamespace(enabled=True, api_key="k", model="m",
                            meter=SimpleNamespace(record=lambda *a, **k: None))
        c.ask = lambda *a, **k: ("chat", None, voice)
        c.reply = lambda *a, **k: voice
        c.voice_schedule_ack = lambda q, w, h, sticker_hint="": "好。"
        c.voice_promise_ack = lambda q, h: "好。"
        c.judge_timed_request = lambda t: None
        c.judge_sticker_request = lambda t: None
        c.judge_self_promise = lambda t: None
        c.judge_promise_preempt = lambda *a, **k: False
        c.voice_promise_keep = lambda *a, **k: None
        c.voice_greeting = lambda *a, **k: "早安！"
        c.voice_sticker_ack = lambda *a, **k: "貼圖收到。"
        return c

    def _run(self, update_body, on=True, ground=True, coach="default"):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        cl = self.Cl()
        co = self._coach() if coach == "default" else coach
        monitor.handle_message({"message": dict(update_body, chat={"id": 1}, date=NOW.timestamp())},
                               co, self.BOOM, {"meta": {}, "records": []}, self.SNAP,
                               s, cl, self._cfg(on, ground), TZ)
        return "".join(cl.sent), s

    def test_sticker_contact_captured(self):
        _, s = self._run({"sticker": {"emoji": "🌞", "file_id": "f1", "file_unique_id": "u1"}}, coach=None)
        self.assertIn({"k": "contact", "ts": NOW.timestamp()}, s.habit_events)

    def test_sticker_flag_off_no_event(self):
        _, s = self._run({"sticker": {"emoji": "🌞", "file_id": "f1", "file_unique_id": "u1"}},
                         on=False, coach=None)
        self.assertEqual(s.habit_events, [])                    # 旗標關＝不記＝現狀

    def test_mention_not_greeting_lane_and_not_recorded(self):
        out, s = self._run({"text": "我常跟你說早安喔"})
        self.assertNotEqual(out, "早安！")                       # 不再被 greeting lane 用問候打發/質疑
        self.assertNotIn("greet_am", {e["k"] for e in s.habit_events})   # 不再記成晚上的早安事件

    def test_mention_flag_off_stays_greeting(self):
        out, s = self._run({"text": "我常跟你說早安喔"}, on=False)
        self.assertEqual(out, "早安！")                          # 現狀：進 greeting lane
        self.assertIn("greet_am", {e["k"] for e in s.habit_events})      # 現狀：照記（汙染如舊）

    def test_real_greeting_unaffected(self):
        out, s = self._run({"text": "早安"})
        self.assertEqual(out, "早安！")                          # 真問候照走 greeting lane
        self.assertIn("greet_am", {e["k"] for e in s.habit_events})

    def test_habits_command(self):
        out, _ = self._run({"text": "/habits"}, coach=None)
        self.assertIn("習慣觀測對帳", out)

    def test_habits_command_flag_off_generic(self):
        out, _ = self._run({"text": "/habits"}, on=False, coach=None)
        self.assertNotIn("習慣觀測對帳", out)                    # 旗標關＝落泛 '/' 固定招呼＝現狀


class ConfigTest(unittest.TestCase):
    def test_config_synced(self):
        src = open("telegram_monitor/config.py", encoding="utf-8").read()
        self.assertIn("HABIT_OBS_FIX", src)
        self.assertIn("habit_obs_fix_enabled", src)
        env = open(".env.example", encoding="utf-8").read()
        self.assertRegex(env, re.compile(r"^HABIT_OBS_FIX=1", re.M))
        self.assertIn("HABIT_OBS_FIX", open("README.md", encoding="utf-8").read())

    def test_getattr_defaults_false(self):
        self.assertFalse(getattr(SimpleNamespace(), "habit_obs_fix_enabled", False))


if __name__ == "__main__":
    unittest.main()
