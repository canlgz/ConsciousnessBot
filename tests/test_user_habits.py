"""📈 §1.42 使用者習慣模型（USER_HABIT_GROUND）：bot 記得並照真實統計講使用者的習慣，不再憑印象亂掰。

截圖根因：06:45 bot 說「看你今天好像醒得比較晚」、07:08 說「你通常會在早上十點左右跟我說早安」——被使用者抓包
「不要亂掰」；07:43 又改口「有時是早上八點多，有時也會到十點多」＝仍是編的。根因：全 repo 只有**記寫節奏**
（brief rhythm）接地，**對話習慣**（幾點說早安/一天第一句何時來）完全沒有資料——LLM 只能腦補。

§1.42 四件（比照既有紀律：確定性、旗標兩層分離、紅先綠後）：
 A. 捕捉：handle_message 把使用者訊息事件記進 state.habit_events（msg/first/greet_am…，FIFO 上限、跨重生持久化）。
 B. 統計：habits.stats 純函式算「多半落在 HH:MM–HH:MM（中位、樣本 N）」；樣本 <3＝誠實說不準。
 C. 接地注入：問「我平常大概幾點…」（is_user_habit_question）→ fact_or_chat/self_appraisal 注入真統計＋只准照抄；
    greeting lane 注入「今天第一句 vs 平常」的程式算比較（樣本不夠＝注入『別對他作息下判斷』警語）。
 D.（核心）_say 事後守門：回覆裡「你通常/平常…X點」「你…比平常早/晚」的作息宣稱，樣本不夠或與統計不符
    → 確定性剝掉、換照統計的誠實句（引用歸屬不剝、講 bot 自己不命中）。旗標關＝逐位元同現狀。全 stub、零網路。
"""

import os
import re
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from telegram_monitor import habits, monitor, persona
from telegram_monitor.state import State

TZ = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 7, 14, 23, 8, 0, tzinfo=timezone.utc)   # 台北 7/15 07:08（截圖時刻附近）


def _ts(day, hh, mm):
    """7 月 day 日台北時間 hh:mm 的 epoch。"""
    return datetime(2026, 7, day, hh, mm, 0, tzinfo=TZ).timestamp()


def _seed_greetings(state, hhmm_list):
    """種下多天的早安事件（greet_am ＋ first ＋ msg 一起，像真捕捉那樣）。"""
    for i, (hh, mm) in enumerate(hhmm_list):
        t = _ts(1 + i, hh, mm)
        state.habit_events.append({"k": "greet_am", "ts": t})
        state.habit_events.append({"k": "first", "ts": t})
        state.habit_events.append({"k": "msg", "ts": t})


class NoteTest(unittest.TestCase):
    def setUp(self):
        self.s = State(os.path.join(tempfile.mkdtemp(), "s.json"))

    def test_msg_always_recorded(self):
        habits.note(self.s, "隨便聊聊", _ts(10, 12, 0), prev_ts=_ts(10, 11, 50))
        self.assertEqual([e["k"] for e in self.s.habit_events], ["msg"])

    def test_first_after_long_gap(self):
        habits.note(self.s, "嗨", _ts(10, 8, 0), prev_ts=_ts(9, 22, 0))   # 隔 10 小時
        kinds = {e["k"] for e in self.s.habit_events}
        self.assertIn("first", kinds)

    def test_greeting_classified(self):
        habits.note(self.s, "早安", _ts(10, 8, 30), prev_ts=_ts(10, 8, 0))
        kinds = {e["k"] for e in self.s.habit_events}
        self.assertIn("greet_am", kinds)

    def test_habit_question_not_recorded_as_greeting(self):
        # 「我平常大概幾點跟你說早安？」＝在**問**問候習慣（greeting.detect 對 ≤12 字含早安會誤判 morning）
        # → 別記成 greet_am 汙染統計
        habits.note(self.s, "我平常大概幾點跟你說早安？", _ts(10, 7, 35), prev_ts=_ts(10, 7, 0))
        kinds = {e["k"] for e in self.s.habit_events}
        self.assertNotIn("greet_am", kinds)

    def test_cap_enforced(self):
        for i in range(500):
            habits.note(self.s, "嗨", _ts(1, 0, 0) + i * 60.0, prev_ts=_ts(1, 0, 0) + (i - 1) * 60.0)
        self.assertLessEqual(len(self.s.habit_events), habits._CAP)


class StatsTest(unittest.TestCase):
    def test_stats_median_range(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        _seed_greetings(s, [(8, 10), (8, 30), (9, 0), (8, 50), (9, 20)])
        st = habits.stats(s.habit_events, "greet_am", TZ, NOW.timestamp())
        self.assertIsNotNone(st)
        n, med, lo, hi = st
        self.assertEqual(n, 5)
        self.assertTrue(8 * 60 <= lo <= med <= hi <= 10 * 60)

    def test_insufficient_none(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        _seed_greetings(s, [(8, 10), (8, 30)])                 # 只有 2 筆
        self.assertIsNone(habits.stats(s.habit_events, "greet_am", TZ, NOW.timestamp()))

    def test_old_events_outside_window_ignored(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        old = datetime(2026, 3, 1, 8, 30, tzinfo=TZ).timestamp()
        for i in range(5):
            s.habit_events.append({"k": "greet_am", "ts": old + i * 86400})
        self.assertIsNone(habits.stats(s.habit_events, "greet_am", TZ, NOW.timestamp()))


class DetectorTest(unittest.TestCase):
    def test_habit_questions_true(self):
        for t in ("我平常大概幾點跟你說早安，你再想想",
                  "我平常大概幾點跟你說早安？",
                  "我通常都什麼時候寫記寫",
                  "我的作息大概是怎樣",
                  "那你知道我現有的記寫裡的行為與習慣？",   # 截圖 08:12（泛型第二路）
                  "你現在有我任何作息的了解嗎？"):          # 截圖 08:14
            self.assertTrue(habits.is_user_habit_question(t), t)

    def test_non_habit_false(self):
        for t in ("現在幾點",                     # 問鐘點、非習慣
                  "你平常幾點會跟我說話",          # 問 bot、主詞非我
                  "我想知道你的習慣",              # 問 bot 的習慣（含我但受詞是你的習慣）
                  "我明天八點要開會",              # 未來安排、非習慣回顧
                  "早安"):
            self.assertFalse(habits.is_user_habit_question(t), t)


class FactsTest(unittest.TestCase):
    def test_grounded_facts_contain_stats(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        _seed_greetings(s, [(8, 10), (8, 30), (9, 0), (8, 50)])
        txt = habits.habit_facts(s, [], NOW.timestamp(), TZ)
        self.assertIn("早安", txt)
        self.assertIn("樣本", txt)
        self.assertNotIn("說不準", txt.split("早安")[1][:40])   # 早安那行是接地統計、不是說不準

    def test_insufficient_honest(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        txt = habits.habit_facts(s, [], NOW.timestamp(), TZ)
        self.assertIn("說不準", txt)                            # 零樣本＝誠實說不準

    def test_records_writing_hours(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        recs = [{"text": "x", "ts": datetime(2026, 7, d, 22, 15, tzinfo=TZ).isoformat()} for d in (1, 2, 3, 4)]
        txt = habits.habit_facts(s, recs, NOW.timestamp(), TZ)
        self.assertIn("記寫", txt)
        self.assertIn("晚上", txt)

    def test_topic_habit_analysis(self):
        # 記寫的事後習慣分析：同主題 ≥3 筆＝重複行為 → 「你常記「讀誦經書」（4 筆，多在 7–8 點）」
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        recs = ([{"text": "x", "topicLabel": "讀誦經書", "ts": datetime(2026, 7, d, 7, 30, tzinfo=TZ).isoformat()}
                 for d in (1, 2, 3, 4)]
                + [{"text": "y", "topicLabel": "火鍋用餐", "ts": datetime(2026, 7, 2, 19, 0, tzinfo=TZ).isoformat()}])
        txt = habits.habit_facts(s, recs, NOW.timestamp(), TZ)
        self.assertIn("你常記「讀誦經書」", txt)
        self.assertIn("4 筆", txt)
        self.assertNotIn("你常記「火鍋用餐」", txt)          # 只有 1 筆＝不算習慣

    def test_topic_habit_insufficient_honest(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        txt = habits.habit_facts(s, [], NOW.timestamp(), TZ)
        self.assertIn("主題性的固定習慣", txt)
        self.assertIn("說不準", txt)


class TodayVsUsualTest(unittest.TestCase):
    def test_insufficient_returns_caution(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        line = habits.today_vs_usual_line(s, NOW.timestamp(), TZ)
        self.assertIn("別", line)                               # 樣本不夠＝警語（別對他作息下判斷）

    def test_grounded_comparison(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        _seed_greetings(s, [(8, 50), (9, 0), (9, 10), (8, 40)])
        s.habit_events.append({"k": "first", "ts": _ts(15, 7, 7)})   # 今天第一句 07:07＝比平常早
        line = habits.today_vs_usual_line(s, NOW.timestamp(), TZ)
        self.assertIn("早", line)
        self.assertNotIn("晚了", line)


class ClaimGuardTest(unittest.TestCase):
    """D. _say 守門：亂掰的作息宣稱被換成照統計的誠實句。"""

    class Cl:
        def __init__(self):
            self.sent, self.dry_run = [], False

        def send(self, text):
            self.sent.append(text)
            return True

    def setUp(self):
        monitor._TURN.clear()
        monitor._TURN["bubbles"] = None

    def _out(self, text, ground=None):
        if ground is not None:
            monitor._TURN["habit_claim_ground"] = ground
        cl = self.Cl()
        monitor._say(cl, text)
        return "".join(cl.sent)

    G_STATS = {"greet_am": (5, 8 * 60 + 50, 8 * 60 + 10, 9 * 60 + 20), "first": (5, 8 * 60 + 50, 8 * 60 + 10, 9 * 60 + 20),
               "today_first_min": 7 * 60 + 7}
    G_EMPTY = {"greet_am": None, "first": None, "today_first_min": None}

    def test_fabricated_ten_oclock_replaced(self):
        # 截圖 07:08：統計明明是 08:10–09:20，卻宣稱「十點左右」→ 剝掉＋照統計誠實句
        out = self._out("你通常會在早上十點左右跟我說早安，不過今天我們現在才開始聊。", ground=self.G_STATS)
        self.assertNotIn("十點左右", out)
        self.assertIn("08", out)                                # 換成真統計

    def test_no_data_claim_replaced_honest(self):
        # 零樣本卻宣稱「八點多到十點多」（截圖 07:43 仍在掰）→ 換誠實「還沒累積夠樣本」
        out = self._out("你平常跟我說早安的時間有時是早上八點多，有時也會到十點多。", ground=self.G_EMPTY)
        self.assertNotIn("十點多", out)
        self.assertIn("樣本", out)

    def test_comparative_without_data_stripped(self):
        # 截圖 06:45「看你今天好像醒得比較晚」＝零統計的比較宣稱 → 剝
        out = self._out("早安。看你今天好像醒得比較晚，是嗎？", ground=self.G_EMPTY)
        self.assertNotIn("比較晚", out)

    def test_comparative_wrong_direction_stripped(self):
        # 今天 07:07 其實比中位 08:50 早，宣稱「比平常晚」＝方向錯 → 剝
        out = self._out("你今天比平常晚了一點點。", ground=self.G_STATS)
        self.assertNotIn("晚了一點點", out)

    def test_comparative_correct_direction_passes(self):
        out = self._out("你今天比平常早了一些呢。", ground=self.G_STATS)
        self.assertIn("比平常早", out)

    def test_claim_matching_stats_passes(self):
        out = self._out("你平常大概九點左右跟我說早安。", ground=self.G_STATS)
        self.assertIn("九點左右", out)                          # 與統計相符＝放行

    def test_quote_attribution_not_stripped(self):
        out = self._out("你說我以為你平常十點跟我說早安？", ground=self.G_STATS)
        self.assertIn("十點", out)

    def test_bot_self_not_hit(self):
        t = "我平常都在晚上比較安靜。"
        out = self._out(t, ground=self.G_STATS)
        self.assertEqual(out, t)

    def test_not_armed_passthrough(self):
        t = "你通常會在早上十點左右跟我說早安。"
        out = self._out(t)                                      # 未 arm＝逐位元同現狀
        self.assertEqual(out, t)


class EndToEndTest(unittest.TestCase):
    def _cfg(self, on=True):
        base = dict(dry_run=False, telegram_chat_id="", scheduled_promise_enabled=True,
                    promise_emit_enabled=True, promise_sched_ttl_sec=21600, timezone="Asia/Taipei",
                    notify_cooldown_min=30, promise_reply_bridge_enabled=False, promise_ledger_enabled=True,
                    sched_leave_autoarm_enabled=True, deferred_promise_enabled=True,
                    promise_llm_rescue_enabled=False, sticker_llm_rescue_enabled=False,
                    promise_keep_claim_guard_enabled=True, promise_said_ground_enabled=True,
                    bot_self_promise_enabled=False, promise_preempt_enabled=False,
                    sticker_sent_memory_enabled=True, send_stickers=True,
                    sticker_fakesend_guard_enabled=False, recall_ground_guard_enabled=False,
                    selfshare_reason_ground_enabled=False, wake_projection_guard_enabled=False,
                    user_habit_ground_enabled=on)
        return SimpleNamespace(**base)

    def _coach(self, voice):
        seen = {"ask": None, "reply": None}
        c = SimpleNamespace(enabled=True, api_key="k", model="m",
                            meter=SimpleNamespace(record=lambda *a, **k: None), seen=seen)

        def ask(*a, **k):
            seen["ask"] = k.get("extra_system")
            return ("chat", None, voice)

        def reply(*a, **k):
            seen["reply"] = k.get("extra_system")
            return voice

        c.ask = ask
        c.reply = reply
        c.voice_schedule_ack = lambda q, w, h, sticker_hint="": "好。"
        c.voice_promise_ack = lambda q, h: "好。"
        c.judge_timed_request = lambda t: None
        c.judge_sticker_request = lambda t: None
        c.judge_self_promise = lambda t: None
        c.judge_promise_preempt = lambda *a, **k: False
        c.voice_promise_keep = lambda *a, **k: None
        c.voice_greeting = lambda *a, **k: "早安！"
        return c

    SNAP = SimpleNamespace(summary={"total": 1, "last24h": 0, "last7d": 0},
                           funnel={"candidate": 0, "context": 0, "journey": 0, "watch": 0},
                           heartbeat={"status": "ok"}, filed_records=[])
    BOOM = SimpleNamespace(load_embedding_records=lambda *_: [])   # 旗標關 leg 會走真 self_state lane＝要可疊代

    def _run(self, question, voice, on=True, seed=True):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        if seed:
            _seed_greetings(s, [(8, 10), (8, 30), (9, 0), (8, 50)])
        cl = ClaimGuardTest.Cl()
        co = self._coach(voice)
        monitor.handle_message({"message": {"chat": {"id": 1}, "text": question, "date": NOW.timestamp()}},
                               co, self.BOOM, {"meta": {}, "records": []}, self.SNAP,
                               s, cl, self._cfg(on), TZ)
        return "".join(cl.sent), co.seen, s

    def test_habit_question_gets_facts_and_guard(self):
        FAB = "你通常會在早上十點左右跟我說早安。"
        out, seen, _ = self._run("我平常大概幾點跟你說早安，你再想想", FAB)
        self.assertIn("只准", seen["ask"] or "")                # HABIT_GROUND_HINT＋事實已注入
        self.assertIn("樣本", seen["ask"] or "")
        self.assertNotIn("十點左右", out)                        # 亂掰被守門換掉

    def test_capture_via_handle_message(self):
        _, _, s = self._run("早安", "早安！", seed=False)
        kinds = {e["k"] for e in s.habit_events}
        self.assertIn("greet_am", kinds)
        self.assertIn("msg", kinds)

    def test_short_habit_question_not_hijacked_by_greeting_lane(self):
        # 短句「我平常大概幾點跟你說早安？」被 greeting.detect 誤判 morning → 旗標開＝讓路 fact_or_chat
        # （coach.ask 收到問題＋注入統計），不再被 greeting lane 用「早安！」打發
        FAB = "你通常會在早上十點左右跟我說早安。"
        out, seen, _ = self._run("我平常大概幾點跟你說早安？", FAB)
        self.assertNotEqual(out, "早安！")
        self.assertIn("樣本", seen["ask"] or "")               # 走到 ask＝有注入統計

    def test_short_habit_question_flag_off_stays_greeting(self):
        # 旗標關＝照舊進 greeting lane（現狀行為、byte-identical）
        out, _, _ = self._run("我平常大概幾點跟你說早安？", "不會用到", on=False, seed=False)
        self.assertEqual(out, "早安！")

    def test_selfstate_hijack_rerouted(self):
        # 截圖 08:14：「你現在有我任何作息的了解嗎？」被 intent 收成 self_state → bot 大講自己的感覺（答非所問）。
        # 旗標開＝re-route fact_or_chat（about_self 落自我在場純對話 reply、仍注入真統計）
        out, seen, _ = self._run("你現在有我任何作息的了解嗎？", "你一天跟我說上第一句話多半在早上。")
        _got = (seen["ask"] or "") + (seen["reply"] or "")
        self.assertIn("樣本", _got)                            # 不論 ask/reply lane＝都有注入統計
        # 旗標關＝照舊 self_state lane＝ask/reply 都不經 extra_system 注入統計（現狀行為）
        _, seen2, _ = self._run("你現在有我任何作息的了解嗎？", "不會用到", on=False, seed=False)
        self.assertNotIn("樣本", (seen2["ask"] or "") + (seen2["reply"] or ""))

    def test_flag_off_byte_identical(self):
        FAB = "你通常會在早上十點左右跟我說早安。"
        out, seen, s = self._run("我平常大概幾點跟你說早安，你再想想", FAB, on=False, seed=False)
        self.assertEqual(out, FAB)                              # 不守門
        self.assertNotIn("只准", seen["ask"] or "")             # 不注入
        self.assertEqual(s.habit_events, [])                    # 不捕捉


class PersistTest(unittest.TestCase):
    def test_habit_events_survive_save_load(self):
        path = os.path.join(tempfile.mkdtemp(), "s.json")
        s = State(path)
        s.habit_events.append({"k": "greet_am", "ts": 123.0})
        s.save()
        s2 = State.load(path)
        self.assertEqual(s2.habit_events, [{"k": "greet_am", "ts": 123.0}])


class ConfigTest(unittest.TestCase):
    def test_config_synced(self):
        src = open("telegram_monitor/config.py", encoding="utf-8").read()
        self.assertIn("USER_HABIT_GROUND", src)
        self.assertIn("user_habit_ground_enabled", src)
        env = open(".env.example", encoding="utf-8").read()
        self.assertRegex(env, re.compile(r"^USER_HABIT_GROUND=1", re.M))
        self.assertIn("USER_HABIT_GROUND", open("README.md", encoding="utf-8").read())

    def test_persona_hint_exists(self):
        self.assertTrue(hasattr(persona, "HABIT_GROUND_HINT"))

    def test_getattr_defaults_false(self):
        self.assertFalse(getattr(SimpleNamespace(), "user_habit_ground_enabled", False))


if __name__ == "__main__":
    unittest.main()
