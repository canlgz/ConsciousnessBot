"""🤝 §0.76 通盤審計修正：四路平行審計（捕捉/時間解析/兌現引擎/條件託付/教學觸發）確認的破綻全修。

最嚴重（審計交叉 confirmed）：①「八點**不用**叫我了」被收成新約、到點真的去叫＝做相反的事＋零取消路徑；
② 錯時派發：明早8點→今晚20:00、7點15分→19:00（分被吞）、凌晨1點→13:00、半夜12點→正午、一個半小時→+30分、
星期五晚上8點→今晚；③ 主聊天 lane 不注入已學做法＋教一次 5.5 天靜默失效；④ 失約靜默＋貼圖無限重送。
"""

import os
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from telegram_monitor import monitor, selfstate, temporal, plasticity
from telegram_monitor.state import State

TZ = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 7, 5, 8, 0, 0, tzinfo=timezone.utc)     # 16:00 台北（週日）
NOW2350 = datetime(2026, 7, 5, 15, 50, 0, tzinfo=timezone.utc)  # 23:50


def _lc(t, now=NOW):
    return [datetime.fromtimestamp(e, timezone.utc).astimezone(TZ).strftime("%m-%d %H:%M")
            for e in temporal.all_clock_epochs(t, now, TZ)]


class FakeClient:
    def __init__(self):
        self.sent, self.stickers, self.dry_run = [], [], False

    def send(self, text):
        self.sent.append(text)
        return True

    def send_sticker(self, fid):
        self.stickers.append(fid)
        return True


def _coach():
    return SimpleNamespace(
        enabled=True, api_key="k", model="m", meter=SimpleNamespace(record=lambda *a, **k: None),
        voice_schedule_ack=lambda q, when, h, sticker_hint="": f"好，{when.strftime('%H:%M') if when else '到時候'}見。",
        voice_promise_keep=lambda when, facts, h, promised="", late=False, feeling_ground="", **k: f"到點！{promised}")


def _cfg(**over):
    base = dict(dry_run=False, telegram_chat_id="", scheduled_promise_enabled=True,
                promise_emit_enabled=True, promise_sched_ttl_sec=21600, timezone="Asia/Taipei",
                notify_cooldown_min=30, promise_cancel_enabled=True, promise_expire_apology_enabled=True,
                promise_sticker_enabled=True, send_stickers=True, sticker_no_repeat_enabled=True,
                sticker_file_ids=[], promise_reply_bridge_enabled=True, promise_ledger_enabled=True,
                sched_leave_autoarm_enabled=True, deferred_promise_enabled=True)
    base.update(over)
    return SimpleNamespace(**base)


def _msg(text, when=NOW):
    return {"message": {"chat": {"id": 1}, "text": text, "date": when.timestamp()}}


_BOOM = SimpleNamespace(load_embedding_records=lambda *_: (_ for _ in ()).throw(AssertionError("不該走資料路徑")))


class WrongTimeFixTest(unittest.TestCase):
    """審計 A 表 B：錯時派發（比漏記更糟——在錯的時刻真的發出去）。"""

    def test_tomorrow_morning_evening_day2(self):
        self.assertEqual(_lc("明早8點叫我"), ["07-06 08:00"])          # 原 bug：今晚 20:00
        self.assertEqual(_lc("明晚8點提醒我"), ["07-06 20:00"])        # 原 bug：今晚 20:00
        self.assertEqual(_lc("後天8點提醒我"), ["07-07 08:00"])        # 原 bug：提早兩天

    def test_minutes_not_dropped(self):
        self.assertEqual(_lc("8點55分提醒我出門"), ["07-05 20:55"])    # 原 bug：20:00（分被吞）
        self.assertEqual(_lc("晚上7點15分叫我"), ["07-05 19:15"])

    def test_smallhours_and_midnight(self):
        self.assertEqual(_lc("凌晨1點叫我"), ["07-06 01:00"])          # 原 bug：13:00
        self.assertEqual(_lc("半夜12點叫我"), ["07-06 00:00"])         # 原 bug：正午
        self.assertEqual(_lc("晚上12點叫我"), ["07-06 00:00"])
        self.assertEqual(_lc("12點叫我", NOW2350), ["07-06 00:00"])   # 23:50 說 12 點＝10 分鐘後的午夜

    def test_hour_and_half_and_days(self):
        self.assertEqual(_lc("一個半小時後提醒我"), ["07-05 17:30"])   # 原 bug：+30 分
        self.assertEqual(_lc("兩個半小時後叫我"), ["07-05 18:30"])
        self.assertEqual(_lc("兩天後提醒我"), ["07-07 16:00"])         # 原 HOLE：整包漏收
        self.assertEqual(_lc("中午一點跟我說一聲"), ["07-06 13:00"])   # 原 bug：正午蓋掉 1 點

    def test_weekday_date_blocked_to_guard(self):
        # 解析不了的日子（週幾/日期）→ **不對著被丟掉的日子排今天**（原 bug：星期五晚上8點→今晚 20:00）
        for t in ["星期五晚上8點提醒我繳卡費", "下禮拜三下午2點提醒我開會", "7月10號早上9點提醒我"]:
            self.assertEqual(_lc(t), [], t)
            self.assertTrue(selfstate.looks_like_timed_request(t), t)   # 守門接住＝誠實請對方講明確時間
        for t in ["下週一提醒我交報告", "星期五提醒我繳卡費"]:           # 原 TRUE HOLE：連守門都沒有
            self.assertTrue(selfstate.looks_like_timed_request(t), t)

    def test_degree_adverb_not_one_oclock(self):
        # 「溫柔一點/快一點」的程度副詞不是 1 點鐘（原 bug：幻影 13:00 亂發）
        self.assertEqual(_lc("十分鐘後提醒我喝水，講話溫柔一點"), ["07-05 16:10"])
        self.assertEqual(_lc("快一點回我"), [])
        self.assertEqual(_lc("一點叫我"), ["07-06 01:00"])             # 真的約 1 點仍照排（無程度前綴）


class CaptureHoleFixTest(unittest.TestCase):
    """審計 A 表 A：TRUE HOLE（沒捕捉＋沒守門＝LLM 自由空口答應）。"""

    def test_sticker_care_joke_alarm_captured(self):
        for t in ["十分鐘後給我貼圖", "8點給我一張貼圖", "8點關心我一下", "十分鐘後說個笑話給我聽",
                  "幫我設個鬧鐘，明天7點"]:
            self.assertTrue(selfstate.is_scheduled_promise_request(t), t)

    def test_behavior_labels(self):
        self.assertEqual(selfstate.extract_promise_behavior("8點關心我一下"), "關心他一下")
        self.assertEqual(selfstate.extract_promise_behavior("十分鐘後說個笑話給我聽"), "說個笑話給他聽")
        self.assertEqual(selfstate.extract_promise_behavior("幫我設個鬧鐘，明天7點"), "叫他起床")
        self.assertEqual(selfstate.extract_promise_behavior("8點提醒我吃藥"), "提醒他吃藥")   # 內容進標籤（原：固定『時間快到了』）
        self.assertEqual(selfstate.extract_promise_behavior("30分鐘後提醒我"), "提醒他『時間快到了』")  # 無內容→原標籤


class CancelTest(unittest.TestCase):
    """審計 B-1/2＋D-1/2：否定式反轉＋零取消路徑。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _state(self, proms=None):
        s = State(os.path.join(self.tmp, "s.json"))
        s.owner_folder_id = "F"
        if proms:
            s.scheduled_promises = proms
        return s

    def test_negation_not_captured_as_promise(self):
        for t in ["八點不用叫我了", "十分鐘後不用提醒我了", "明天早上八點不要叫我", "8點不用提醒我"]:
            self.assertFalse(selfstate.is_scheduled_promise_request(t), t)   # 原 bug：收成新約、到點真的去叫

    def test_negation_of_forget_still_captured(self):
        # 「別忘了8點叫我」＝要叫（否定的是忘）；「不要太晚叫我」語意仍是要叫——都不可誤擋
        self.assertTrue(selfstate.is_scheduled_promise_request("別忘了8點叫我"))

    def test_cancel_detector(self):
        for t in ["不用叫我了", "取消八點的約定", "剛剛說的不算", "那個提醒不用了", "不用再每天叫我了"]:
            self.assertTrue(selfstate.is_promise_cancel_request(t), t)
        for t in ["八點叫我", "我不用去上班了", "算了我自己來看看天氣"]:
            self.assertFalse(selfstate.is_promise_cancel_request(t), t)

    def test_cancel_marks_ledger_and_stops_fire(self):
        # 端到端：約了 → 取消 → 到點**不**發
        st = self._state()
        monitor.handle_message(_msg("八點叫我起床"), _coach(), _BOOM, {"meta": {}}, None, st, FakeClient(), _cfg(), TZ)
        self.assertTrue([p for p in st.scheduled_promises if not p.get("fulfilled")])
        cl = FakeClient()
        monitor.handle_message(_msg("不用叫我了"), _coach(), _BOOM, {"meta": {}}, None, st, cl, _cfg(), TZ)
        self.assertIn("取消", "".join(cl.sent))
        self.assertEqual(st.scheduled_promises[0].get("status"), "cancelled")
        cl2 = FakeClient()
        at8 = datetime(2026, 7, 5, 12, 0, tzinfo=timezone.utc)          # 20:00 台北
        monitor._promise_emit(cl2, st, _cfg(), _coach(), at8)
        self.assertEqual(cl2.sent, [])                                   # 已取消＝不發（原 bug：照發）

    def test_cancel_recur_daily(self):
        st = self._state([{"target_ts": NOW.timestamp() + 3600, "made_ts": NOW.timestamp() - 60,
                           "fulfilled": False, "behavior": "叫他起床", "status": "pending", "recur": "daily"}])
        st.last_user_msg_ts = NOW.timestamp()
        handled = monitor._maybe_promise_cancel(FakeClient(), st, _cfg(), "不用再每天叫我了", NOW, TZ, NOW.timestamp())
        self.assertTrue(handled)
        self.assertEqual(st.scheduled_promises[0].get("status"), "cancelled")
        self.assertNotIn("recur", st.scheduled_promises[0])              # recur 清掉＝真的停

    def test_cancel_without_pending_falls_through(self):
        st = self._state()
        handled = monitor._maybe_promise_cancel(FakeClient(), st, _cfg(), "不用叫我了", NOW, TZ, NOW.timestamp())
        self.assertFalse(handled)                                        # 無 pending＝不搶、照常聊天

    def test_flag_off_no_cancel(self):
        st = self._state([{"target_ts": NOW.timestamp() + 3600, "made_ts": NOW.timestamp(),
                           "fulfilled": False, "behavior": "叫他起床", "status": "pending"}])
        handled = monitor._maybe_promise_cancel(FakeClient(), st, _cfg(promise_cancel_enabled=False),
                                                "不用叫我了", NOW, TZ, NOW.timestamp())
        self.assertFalse(handled)


class StickerResendFixTest(unittest.TestCase):
    """審計 B-5：貼圖先送、文字失敗 → 原本每拍重送一張直到 TTL。修＝送過就蓋戳、重試拍不重送。"""

    class TextFailClient(FakeClient):
        def send(self, text):
            return False                                                 # 文字一直失敗

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_sticker_sent_once_across_retries(self):
        s = State(os.path.join(self.tmp, "s.json"))
        s.owner_folder_id = "F"
        s.known_sticker_ids = [{"file_id": "DUCK", "valence": "positive"}]
        s.scheduled_promises = [{"target_ts": NOW.timestamp() - 10, "made_ts": NOW.timestamp() - 600,
                                 "fulfilled": False, "behavior": "送他一張貼圖", "wants_sticker": True,
                                 "status": "pending"}]
        cl = self.TextFailClient()
        for i in range(5):                                               # 五拍重試（文字都失敗）
            tick = datetime.fromtimestamp(NOW.timestamp() + 200 * (i + 1), timezone.utc)
            monitor._promise_emit(cl, s, _cfg(), _coach(), tick)
        self.assertEqual(len(cl.stickers), 1)                            # 原 bug：5 拍 5 張


class LedgerHonestyTest(unittest.TestCase):
    """審計 B-3/4：剛過點的 pending ≠「沒做到」；cancelled 誠實分類。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _state(self, proms):
        s = State(os.path.join(self.tmp, "s.json"))
        s.owner_folder_id = "F"
        s.scheduled_promises = proms
        return s

    def test_just_overdue_pending_is_owed_not_missed(self):
        st = self._state([{"target_ts": NOW.timestamp() - 120, "made_ts": NOW.timestamp() - 600,
                           "fulfilled": False, "behavior": "提醒他吃藥", "status": "pending"}])
        facts = selfstate.promise_ledger_facts(st, NOW, TZ)
        self.assertIn("欠著", facts)                                      # 原 bug：「過了、我沒做到」兩分鐘後又補發＝自打臉
        self.assertNotIn("沒做到", facts)

    def test_cancelled_entry_honest(self):
        st = self._state([{"target_ts": NOW.timestamp() + 600, "made_ts": NOW.timestamp() - 600,
                           "fulfilled": True, "behavior": "叫他起床", "status": "cancelled"}])
        facts = selfstate.promise_ledger_facts(st, NOW, TZ)
        self.assertIn("取消", facts)
        self.assertNotIn("等著做", facts)


class SkillLifecycleFixTest(unittest.TestCase):
    """審計 C-1/2/7：主 lane 注入、用到保鮮、記憶滿誠實。"""

    def test_touch_skills_refreshes_decay_clock(self):
        eng = []
        t0 = 1_000_000                                      # 非零（0 會被 last_ts or born_ts 的 falsy 邏輯跳過＝測試假象）
        plasticity.capture_skill(eng, None, "", "回話帶點emoji", trigger="always", now_ts=t0)
        day6 = t0 + 6 * 86400
        self.assertEqual(plasticity.recall_skills(eng, "fact_or_chat", "", signals={"external": True}, now_ts=day6), [])
        # 第 5 天有用到 → 刷 last_ts → 第 6 天仍活著
        eng2 = []
        plasticity.capture_skill(eng2, None, "", "回話帶點emoji", trigger="always", now_ts=t0)   # noqa: 同上非零 t0
        plasticity.touch_skills(eng2, ["回話帶點emoji"], now_ts=t0 + 5 * 86400)
        self.assertEqual(plasticity.recall_skills(eng2, "fact_or_chat", "", signals={"external": True}, now_ts=day6),
                         ["回話帶點emoji"])

    def test_skills_brief_marks_dormant(self):
        eng = [{"kind": "skill", "key": "||always", "value": "回話帶點emoji", "weight": 0.3,
                "hits": 1, "born_ts": 0, "last_ts": 0}]
        brief = plasticity.skills_brief(eng, now_ts=0)
        self.assertIn("已淡忘", brief)                                    # 原 bug：召回已死、帳本照列成「學過」
        eng[0]["weight"] = 0.9
        self.assertNotIn("已淡忘", plasticity.skills_brief(eng, now_ts=0))

    def test_full_memory_honest_ack(self):
        # 12 條滿額＋確認學第 13 條 → consolidate 擠掉最弱的新條 → 誠實說「記憶滿了、沒能留下」（原 bug：嘴上說記起來了）
        st = SimpleNamespace(engrams=[], convo_history=[], skill_pending=None, last_skill_propose_ts=0)
        for i in range(12):
            plasticity.capture_skill(st.engrams, "smalltalk", f"主題{i:02d}", f"做法{i}", gain=0.9, now_ts=1000)
        st.skill_pending = {"route_kind": "smalltalk", "topic_tag": "新主題", "prompt": "新做法",
                            "trigger": "", "ts": 2000}
        cl = FakeClient()
        import unittest.mock as um
        with um.patch.object(monitor, "_say", lambda c, t, **kw: c.send(t)):
            handled = monitor._handle_skill_confirm(cl, st, SimpleNamespace(dry_run=True), "好", 2005)
        self.assertTrue(handled)
        joined = "".join(cl.sent)
        self.assertIn("滿", joined)
        self.assertNotIn("我記起來了", joined)

    def test_testing_signal_fires_on_challenge_text(self):
        # 審計 C-5：真質疑句（你根本答非所問）原本永不觸發 sit:testing——改成句面質疑線索也算
        st = SimpleNamespace(engrams=[], intent_reading={})
        plasticity.capture_skill(st.engrams, None, "", "被質疑時別急著辯解、先承認不確定",
                                 trigger="sit:testing", now_ts=NOW.timestamp())
        cfg = SimpleNamespace(skill_recall_enabled=True, skill_situations_enabled=True,
                              skill_capability_gate_enabled=True, skill_use_refresh_enabled=True,
                              timezone="Asia/Taipei")
        out = monitor._skill_extra(st, cfg, "fact_or_chat", "你根本答非所問吧", NOW.timestamp())
        self.assertIn("別急著辯解", out)


class Review76RegressionTest(unittest.TestCase):
    """§0.76 對抗式審查回歸（12 發現全修）。"""

    def test_f1_cancel_not_overtriggered(self):
        # HIGH：外部事物/內容抱怨/第三方取消 不觸發取消
        for t in ["幫我取消訂閱那個頻道的提醒", "不要再跟我說這些了", "他把鬧鐘取消了"]:
            self.assertFalse(selfstate.is_promise_cancel_request(t), t)
        self.assertTrue(selfstate.is_promise_cancel_request("八點那個就免了吧"))   # LOW-MED：免了+鐘點 收

    def test_f2_mixed_request_not_swallowed(self):
        # HIGH：否定管別的事＋句中有新約 → 新約照收（不被取消吞）
        for t in ["不要只提醒一次，8點跟9點都提醒我", "你不用特別提醒我沒關係，但8點叫我一下",
                  "我取消了訂房，晚上8點提醒我去退款"]:
            self.assertTrue(selfstate.is_scheduled_promise_request(t), t)

    def test_f3_feeling_now_requests_not_stolen(self):
        # HIGH regression：現在式請求不被新感覺詞搶成託付
        for t in ["我心情不好，你跟我說說話", "我想到了！告訴我答案", "我很煩惱，告訴我怎麼辦",
                  "我好無聊，來找我聊天", "我剛想到一件事就告訴我媽"]:
            self.assertFalse(selfstate.is_feeling_promise_request(t), t)
        self.assertTrue(selfstate.is_feeling_promise_request("你有煩惱要讓我知道"))

    def test_f4_unmatched_clock_asks_not_guesses(self):
        # MED-HIGH：帶鐘點但對不到任何 pending → 誠實說沒記著、不亂砍最近那筆
        tmp = tempfile.mkdtemp()
        st = State(os.path.join(tmp, "s.json")); st.owner_folder_id = "F"
        st.scheduled_promises = [
            {"target_ts": NOW.timestamp() + 4 * 3600, "made_ts": NOW.timestamp() - 100,
             "fulfilled": False, "behavior": "叫他起床", "status": "pending"},      # 20:00
            {"target_ts": NOW.timestamp() + 5 * 3600, "made_ts": NOW.timestamp() - 50,
             "fulfilled": False, "behavior": "提醒他吃藥", "status": "pending"}]    # 21:00
        cl = FakeClient()
        handled = monitor._maybe_promise_cancel(cl, st, _cfg(), "7點的那個提醒不用了", NOW, TZ, NOW.timestamp())
        self.assertTrue(handled)
        self.assertIn("沒記著", "".join(cl.sent))
        self.assertTrue(all(not p.get("fulfilled") for p in st.scheduled_promises))   # 兩筆都沒被砍

    def test_f5_days_plus_clock_composes(self):
        # MED：兩天後早上8點＝+2 天的 08:00（單筆，非兩筆都錯）
        got = _lc("兩天後早上8點提醒我")
        self.assertEqual(got, ["07-07 08:00"])

    def test_f6_day_window_stops_at_punctuation(self):
        self.assertEqual(_lc("星期五見！今晚8點提醒我"), ["07-05 20:00"])
        self.assertEqual(_lc("星期五要開會。明天早上8點叫我"), ["07-06 08:00"])
        self.assertEqual(_lc("星期五晚上8點提醒我繳卡費"), [])                       # 緊鄰仍擋

    def test_f7_remind_tail_quality(self):
        self.assertEqual(selfstate.extract_promise_behavior("8點提醒我媽的生日"),
                         "提醒他『時間快到了』")                                     # 不產生「提醒他媽的」
        self.assertEqual(selfstate.extract_promise_behavior("8點提醒我：吃藥"), "提醒他吃藥")

    def test_f9_wan_yidian_not_1am(self):
        self.assertEqual(_lc("晚一點叫我"), [])                                      # 晚一點＝待會、非凌晨1點
        self.assertEqual(_lc("凌晨一點叫我"), ["07-06 01:00"])

    def test_f10_all_cancelled_ledger_text(self):
        tmp = tempfile.mkdtemp()
        st = State(os.path.join(tmp, "s.json")); st.owner_folder_id = "F"
        st.scheduled_promises = [{"target_ts": NOW.timestamp() + 600, "made_ts": NOW.timestamp() - 60,
                                  "fulfilled": True, "behavior": "叫他起床", "status": "cancelled"}]
        txt = selfstate.promise_ledger_text(st, NOW, TZ)
        self.assertNotIn("——。", txt)                                               # 不吐斷句
        self.assertIn("取消", txt)


class FeelingConditionalArmTest(unittest.TestCase):
    """審計 D-4/5：bot 內在狀態託付（你無聊時來找我聊）接上真感覺託付引擎。"""

    def test_bot_state_conditionals_armed(self):
        for t in ["你無聊的時候來找我聊", "你心情不好的時候跟我說", "想到什麼再告訴我",
                  "有新的想法跟我分享", "你有煩惱要讓我知道"]:
            self.assertTrue(selfstate.is_feeling_promise_request(t), t)


if __name__ == "__main__":
    unittest.main()
