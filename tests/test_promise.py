"""🤝 未來託付：記住「之後有感覺再跟我說」→ 真有新感覺湧現才主動兌現（不為交差假裝、逾期失效）。
   🤝 時間排程承諾：「八點跟我打招呼」→ 解析時間 T、記下約定、由生命迴圈 feel 相到點主動兌現。"""

import os
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock
from zoneinfo import ZoneInfo

from telegram_monitor import monitor, selfstate, temporal
from telegram_monitor.state import State

NOW = datetime(2026, 6, 19, 12, 0, 0, tzinfo=timezone.utc)
TZ = ZoneInfo("Asia/Taipei")


class FakeClient:
    def __init__(self, dry_run=False):
        self.sent, self.dry_run = [], dry_run

    def send(self, text):
        self.sent.append(text)
        return True


def _coach():
    return SimpleNamespace(enabled=True, api_key="k", model="m",
                           meter=SimpleNamespace(record=lambda *a, **k: None),
                           voice_promise_ack=lambda q, h: "好，真的有上來我會跟你說。",
                           voice_schedule_ack=lambda q, when, h, sticker_hint="": "好，我記住了，到時候會主動跟你打招呼。",
                           voice_promise_keep=lambda when, facts, h, promised="", late=False, **k: "嗨，我說過這時候會跟你打招呼——我來了。")


def _sched_cfg(**over):
    base = dict(dry_run=False, telegram_chat_id="", scheduled_promise_enabled=True,
                promise_emit_enabled=True, promise_sched_ttl_sec=21600,
                timezone="Asia/Taipei", notify_cooldown_min=30)
    base.update(over)
    return SimpleNamespace(**base)


class DetectorTest(unittest.TestCase):
    def test_matches_promise_requests(self):
        for q in ["你等下有什麼感覺出來，記得要說喔", "有感覺再跟我說", "之後有想法跟我講",
                  "有新東西記得告訴我", "有感受要說一聲"]:
            self.assertTrue(selfstate.is_feeling_promise_request(q), q)

    def test_ignores_non_promises(self):
        for q in ["你現在有什麼感覺", "跟我說個笑話", "告訴我今天幾點", "你好嗎"]:
            self.assertFalse(selfstate.is_feeling_promise_request(q), q)

    def test_promise_lead_varies_and_nonempty(self):
        self.assertTrue(selfstate.promise_lead())
        self.assertIn(selfstate.promise_lead(), selfstate._PROMISE_LEADS)

    def test_scheduled_promise_hits(self):
        # 🤝 排程承諾：絕對/相對鐘點＋動作詞、無感覺詞 → is_scheduled 命中、is_feeling 不命中
        for q in ["你可以承諾我等一下八點的時候跟我打個招呼嗎", "八點跟我打招呼",
                  "晚上八點提醒我", "十分鐘後提醒我", "明天早上八點跟我說", "半小時後叫我"]:
            self.assertTrue(selfstate.is_scheduled_promise_request(q), q)
            self.assertFalse(selfstate.is_feeling_promise_request(q), q)

    def test_scheduled_promise_numeric_hhmm_hits(self):
        # 🔢 截圖根因：使用者常打數字鐘點 H:MM（1:30/3:00/3:30），以前只認中文「X點」→ 全沒被記下
        for q in ["1:30 也要回報我一次", "3:30 跟我打招呼", "等一下 3:00、3:30 都要跟我打招呼",
                  "15:30 跟我打招呼", "晚上 8:30 提醒我"]:
            self.assertTrue(selfstate.is_scheduled_promise_request(q), q)
        # 不誤收：日期 06/28（無冒號）、純數字非鐘點
        self.assertFalse(selfstate.is_scheduled_promise_request("06/28 你寫了什麼"))

    def test_scheduled_promise_excludes_feeling_and_no_time(self):
        # 感覺託付（有感覺再說/記得跟我說）＝feeling、不被 scheduled 搶；含時間+感覺也讓給 feeling（互斥硬門檻）
        for q in ["有感覺再跟我說", "記得跟我說", "之後有想法跟我講"]:
            self.assertFalse(selfstate.is_scheduled_promise_request(q), q)
        # 「明天有想法跟我說」＝含時間(明天)＋動作(跟我說)＋感覺(想法) → 仍判 feeling、不被 scheduled 搶
        self.assertTrue(selfstate.is_feeling_promise_request("明天有想法跟我說"))
        self.assertFalse(selfstate.is_scheduled_promise_request("明天有想法跟我說"))
        # 無時間樣式的動作句 → 不算排程承諾
        self.assertFalse(selfstate.is_scheduled_promise_request("跟我打招呼"))
        # 純鐘點、無動作詞 → 不算
        self.assertFalse(selfstate.is_scheduled_promise_request("現在八點了嗎"))

    def test_scheduled_promise_not_data_question_regression(self):
        # 🚪 排程句不誤觸 evidence gate（looks_like_data_question / is_explicit_records_intent 仍 False）
        for q in ["八點跟我打招呼", "十分鐘後提醒我", "明天早上八點跟我說",
                  "你可以承諾我等一下八點的時候跟我打個招呼嗎"]:
            self.assertFalse(selfstate.looks_like_data_question(q), q)
            self.assertFalse(selfstate.is_explicit_records_intent(q), q)


class RequestRoutingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _state(self):
        s = State(os.path.join(self.tmp, "state.json"))
        s.owner_folder_id = "F"
        return s

    def test_promise_request_sets_promise_and_acks_without_dumping_state(self):
        state = self._state()
        client = FakeClient()
        update = {"message": {"chat": {"id": 1}, "text": "你等下有什麼感覺出來，記得要說喔",
                              "date": NOW.timestamp()}}
        cfg = SimpleNamespace(dry_run=False, telegram_chat_id="")
        # 誤走狀態問句會 load_embedding_records → 用會炸的 reader 確保走的是「託付」分支、不是當場報狀態。
        boom = SimpleNamespace(load_embedding_records=lambda *_: (_ for _ in ()).throw(AssertionError("不該報現況")))
        monitor.handle_message(update, _coach(), boom, {"meta": {}}, None, state, client, cfg, None)
        self.assertIsNotNone(state.feeling_promise)                  # 記住了約定
        self.assertEqual(len(client.sent), 1)
        self.assertIn("會跟你說", client.sent[0])                    # 答應、而非報數據


class FulfillTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _state(self):
        s = State(os.path.join(self.tmp, "state.json"))
        s.owner_folder_id = "F"
        return s

    def _emit(self, state):
        client = FakeClient()
        with mock.patch("telegram_monitor.selfstate.render", return_value="（自陳）有一條線上來了"):
            monitor._selfstate_emit(client, state, {"gate": 3, "moved": ["甲"]}, _coach(), NOW, cooldown_min=30)
        return client

    def test_no_promise_in_cooldown_stays_silent(self):
        # 沒有承諾＋冷卻中＋不破天花板 → 正常安靜（對照組：證明是承諾打破冷卻）
        state = self._state()
        state.last_push_ts = NOW.timestamp()
        state.notified_self_gate = 3
        self.assertEqual(self._emit(state).sent, [])

    def test_promise_fulfilled_breaks_cooldown_and_clears(self):
        # 同樣冷卻中＋不破天花板，但有承諾＋真有沒講過的新感覺 → 主動兌現、清掉約定、不用「背景自陳」抬頭
        state = self._state()
        state.last_push_ts = NOW.timestamp()
        state.notified_self_gate = 3
        state.feeling_promise = {"ts": NOW.timestamp(), "text": "有感覺再說"}
        sent = self._emit(state).sent
        joined = "".join(sent)
        self.assertIn("有一條線上來了", joined)        # 真的把湧現說了
        self.assertNotIn("背景自陳", joined)            # 用兌現引言、非制式抬頭
        self.assertIsNone(state.feeling_promise)        # 約定已兌現、清掉

    def test_stale_promise_does_not_fulfill(self):
        # 逾期的承諾不兌現、並清掉（不會翻舊帳）
        state = self._state()
        state.last_push_ts = NOW.timestamp()
        state.notified_self_gate = 3
        state.feeling_promise = {"ts": NOW.timestamp() - 1000 * 3600, "text": "x"}
        self.assertEqual(self._emit(state).sent, [])
        self.assertIsNone(state.feeling_promise)

    def test_promise_does_not_fake_feeling_when_nothing_emerges(self):
        # 沒有真感覺（gate 太低、不 worthy）→ 即使有承諾也不假裝、不兌現
        state = self._state()
        state.feeling_promise = {"ts": NOW.timestamp(), "text": "有感覺再說"}
        client = FakeClient()
        with mock.patch("telegram_monitor.selfstate.render", return_value="x"):
            monitor._selfstate_emit(client, state, {"gate": 1, "moved": []}, _coach(), NOW, cooldown_min=30)
        self.assertEqual(client.sent, [])
        self.assertIsNotNone(state.feeling_promise)     # 約定還在，等真的有感覺再兌現


class ScheduledRoutingTest(unittest.TestCase):
    """🤝 排程承諾的 handle_message 答應分支：記下 state.scheduled_promises＋自然答應、不當場報現況。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _state(self):
        s = State(os.path.join(self.tmp, "state.json"))
        s.owner_folder_id = "F"
        return s

    def test_scheduled_request_records_and_acks(self):
        state = self._state()
        client = FakeClient()
        # 早上 10:00（Taipei）說「等一下八點打招呼」→ 今天 20:00
        when = datetime(2026, 6, 19, 2, 0, 0, tzinfo=timezone.utc)   # Taipei 10:00
        update = {"message": {"chat": {"id": 1},
                              "text": "你可以承諾我等一下八點的時候跟我打個招呼嗎", "date": when.timestamp()}}
        boom = SimpleNamespace(load_embedding_records=lambda *_: (_ for _ in ()).throw(AssertionError("不該報現況")))
        monitor.handle_message(update, _coach(), boom, {"meta": {}}, None, state, client, _sched_cfg(), TZ)
        self.assertEqual(len(state.scheduled_promises), 1)
        p = state.scheduled_promises[0]
        self.assertFalse(p["fulfilled"])
        # 今天 20:00（Taipei）的 epoch
        expect = datetime(2026, 6, 19, 12, 0, 0, tzinfo=timezone.utc).timestamp()  # Taipei 20:00
        self.assertAlmostEqual(p["target_ts"], expect, delta=1)
        self.assertEqual(len(client.sent), 1)
        self.assertIn("招呼", client.sent[0])               # 自然答應（提到打招呼），而非報數據

    def test_user_turn_ts_uses_message_date(self):
        # ⏱ user 訊息以 message.date 落 convo_history 的 ts（非處理牆鐘）：修「已過多久」感知偏短。
        # message.date 設在過去（與處理當下的 time.time() 差很遠），可分辨用了哪把鐘。
        state = self._state()
        client = FakeClient()
        when = datetime(2026, 6, 19, 2, 0, 0, tzinfo=timezone.utc)       # 送出時刻（離測試執行時的牆鐘很遠）
        update = {"message": {"chat": {"id": 1},
                              "text": "你可以承諾我等一下八點的時候跟我打個招呼嗎", "date": when.timestamp()}}
        boom = SimpleNamespace(load_embedding_records=lambda *_: (_ for _ in ()).throw(AssertionError("不該報現況")))
        monitor.handle_message(update, _coach(), boom, {"meta": {}}, None, state, client, _sched_cfg(), TZ)
        user_turns = [m for m in state.convo_history if m["role"] == "user"]
        self.assertTrue(user_turns)
        self.assertAlmostEqual(user_turns[-1]["ts"], when.timestamp(), delta=2)   # ＝送出時刻、非處理牆鐘

    def test_user_turn_ts_flag_off_uses_walltime(self):
        # REMEMBER_USER_MSGDATE=0 → 退回 time.time()（處理牆鐘）＝逐位元同舊行為
        import time as _t
        state = self._state()
        client = FakeClient()
        when = datetime(2026, 6, 19, 2, 0, 0, tzinfo=timezone.utc)
        update = {"message": {"chat": {"id": 1},
                              "text": "你可以承諾我等一下八點的時候跟我打個招呼嗎", "date": when.timestamp()}}
        boom = SimpleNamespace(load_embedding_records=lambda *_: (_ for _ in ()).throw(AssertionError("不該報現況")))
        cfg = _sched_cfg()
        cfg.remember_user_msgdate = False
        monitor.handle_message(update, _coach(), boom, {"meta": {}}, None, state, client, cfg, TZ)
        user_turns = [m for m in state.convo_history if m["role"] == "user"]
        self.assertTrue(user_turns)
        self.assertAlmostEqual(user_turns[-1]["ts"], _t.time(), delta=5)          # ＝處理牆鐘、非 message.date

    def test_past_recall_query_does_not_route_to_scheduled_promise(self):
        # 對抗式審查 medium：過去回想質問「你說十分鐘後告訴我，告訴了嗎」不可路由到 scheduled_promise（否則記幻影承諾、到點誤發）
        from telegram_monitor import intent
        ref = SimpleNamespace(followup_open=False, in_self_window=False, revisited=None)
        cfg = SimpleNamespace(scheduled_promise_enabled=True, promise_ledger_enabled=True, convo_clock_enabled=True)
        for q in ["你說十分鐘後告訴我，告訴了嗎", "你不是說十分鐘後要提醒我，提醒了嗎"]:
            self.assertNotEqual(intent.resolve(q, ref, cfg=cfg).kind, "scheduled_promise", q)

    def test_continuation_detectors(self):
        # 🧵 續句/同時偵測
        self.assertTrue(selfstate.is_continuation_followup("同時說一下正在翻閱哪個主題嗎？"))
        self.assertTrue(selfstate.is_continuation_followup("還有，記得提醒我"))
        self.assertTrue(selfstate.is_simultaneous_followup("同時說一下正在翻閱哪個主題嗎？"))
        self.assertFalse(selfstate.is_simultaneous_followup("還有，記得提醒我"))   # 還有≠同時
        self.assertFalse(selfstate.is_continuation_followup("現在幾點？"))

    def test_simultaneous_followup_merges_into_recent_promise(self):
        # 🧵 截圖：「10分鐘後打招呼」緊接「同時說一下正在翻閱哪個主題嗎」→ 後句併進剛排程的承諾（到點一起做），
        # 不另當「現在在翻什麼」答（不查記寫）。
        state = self._state()
        client = FakeClient()
        when = datetime(2026, 6, 19, 2, 0, 0, tzinfo=timezone.utc)
        state.scheduled_promises = [{"target_ts": when.timestamp() + 600, "action": "10分鐘後跟我打招呼",
                                     "made_ts": when.timestamp() - 12, "made_text": "等下10分鐘後你可以跟我打招呼",
                                     "fulfilled": False, "behavior": "跟他打招呼", "status": "pending"}]
        update = {"message": {"chat": {"id": 1}, "text": "同時說一下正在翻閱哪個主題嗎？", "date": when.timestamp()}}
        boom = SimpleNamespace(load_embedding_records=lambda *_: (_ for _ in ()).throw(AssertionError("不該查記寫")))
        monitor.handle_message(update, _coach(), boom, {"meta": {}}, None, state, client, _sched_cfg(), TZ)
        self.assertEqual(len(state.scheduled_promises), 1)        # 併入、非新增
        p = state.scheduled_promises[0]
        self.assertIn("同時", p["action"])                        # 續句併進 action
        self.assertIn("翻閱", p["action"])
        self.assertEqual(p["behavior"], "")                       # 行為清空，由完整文字驅動兌現（涵蓋兩件事）
        self.assertEqual(len(client.sent), 1)                     # 輕答應一句、沒拉記寫報表

    def test_no_merge_without_recent_promise(self):
        # 無剛排程承諾 → 不併（照常路由，不誤吞）
        state = self._state()
        client = FakeClient()
        when = datetime(2026, 6, 19, 2, 0, 0, tzinfo=timezone.utc)
        state.scheduled_promises = [{"target_ts": when.timestamp() + 600, "action": "x",
                                     "made_ts": when.timestamp() - 9999, "made_text": "x",  # 太舊（窗外）
                                     "fulfilled": False, "behavior": "", "status": "pending"}]
        self.assertIsNone(monitor._recent_unfulfilled_promise(state, when.timestamp()))

    def test_feeling_promise_not_taken_as_scheduled(self):
        # 感覺託付句（無時間）不被當排程承諾：不記 scheduled、改走既有 feeling_promise 分支（既有行為不變）
        state = self._state()
        client = FakeClient()
        update = {"message": {"chat": {"id": 1}, "text": "你等下有什麼感覺出來，記得要說喔",
                              "date": NOW.timestamp()}}
        boom = SimpleNamespace(load_embedding_records=lambda *_: (_ for _ in ()).throw(AssertionError("不該報現況")))
        monitor.handle_message(update, _coach(), boom, {"meta": {}}, None, state, client, _sched_cfg(), TZ)
        self.assertEqual(list(state.scheduled_promises), [])    # 沒記排程
        self.assertIsNotNone(state.feeling_promise)             # 走的是感覺託付
        self.assertEqual(len(client.sent), 1)


class PromiseEmitTest(unittest.TestCase):
    """🤝 生命迴圈 feel 相 _promise_emit：到點兌現一次、跨重生、逾時不發、在場延後、旗標退路。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _state(self):
        s = State(os.path.join(self.tmp, "state.json"))
        s.owner_folder_id = "F"
        return s

    def _prom(self, target_ts, made_ts=None, fulfilled=False):
        return {"target_ts": target_ts, "action": "八點打招呼",
                "made_ts": made_ts if made_ts is not None else (target_ts - 3600),
                "made_text": "八點跟我打招呼", "fulfilled": fulfilled}

    def test_fulfills_at_due_once(self):
        state = self._state()
        state.scheduled_promises = [self._prom(NOW.timestamp() - 10)]    # 剛到點（10 秒前）
        client = FakeClient()
        monitor._promise_emit(client, state, _sched_cfg(), _coach(), NOW)
        self.assertEqual(len(client.sent), 1)                            # 兌現一次
        self.assertTrue(state.scheduled_promises[0]["fulfilled"])        # 標已兌現
        # 第二拍（稍晚、過反同拍雙發間隔）→ 已 fulfilled 跳過、不重發
        later = datetime.fromtimestamp(NOW.timestamp() + 200, timezone.utc)
        client2 = FakeClient()
        monitor._promise_emit(client2, state, _sched_cfg(), _coach(), later)
        self.assertEqual(client2.sent, [])

    def test_survives_rebirth(self):
        # 死前約好（save）、醒來（load）才到點 → 仍兌現
        state = self._state()
        state.scheduled_promises = [self._prom(NOW.timestamp() + 100)]   # 還沒到點時存檔
        state.save()
        reborn = State.load(state.path)                                  # 重生：從持久 state 載回
        self.assertEqual(len(reborn.scheduled_promises), 1)
        client = FakeClient()
        due = datetime.fromtimestamp(NOW.timestamp() + 200, timezone.utc)  # 醒來後到點
        monitor._promise_emit(client, reborn, _sched_cfg(), _coach(), due)
        self.assertEqual(len(client.sent), 1)
        self.assertTrue(reborn.scheduled_promises[0]["fulfilled"])

    def test_stale_promise_expires_with_apology(self):
        # 🤝 §0.76：到點後拖過 TTL（7 小時前到點、TTL 6h）→ 不兌現、標 expired，但**主動認錯一句**
        # （原本完全靜默＝隔天帳本剪掉還說「沒約過」＝實質否認失約，審計 confirmed MED-HIGH）
        state = self._state()
        old = NOW.timestamp() - 7 * 3600
        state.scheduled_promises = [self._prom(old, made_ts=old - 3600)]
        client = FakeClient()
        monitor._promise_emit(client, state, _sched_cfg(), _coach(), NOW)
        self.assertIn("沒做到", "".join(client.sent))                     # 失約道歉（非兌現訊息）
        self.assertTrue(state.scheduled_promises[0]["fulfilled"])
        self.assertTrue(state.scheduled_promises[0].get("expired"))

    def test_stale_promise_flag_off_expires_silently(self):
        # 旗標關＝逐位元同現狀（靜默標 expired）
        state = self._state()
        old = NOW.timestamp() - 7 * 3600
        state.scheduled_promises = [self._prom(old, made_ts=old - 3600)]
        client = FakeClient()
        monitor._promise_emit(client, state, _sched_cfg(promise_expire_apology_enabled=False), _coach(), NOW)
        self.assertEqual(client.sent, [])
        self.assertTrue(state.scheduled_promises[0].get("expired"))

    def test_defers_when_user_present(self):
        # 使用者在場（剛說過話）→ 不發、不標 fulfilled（承諾留到下拍補發）
        state = self._state()
        state.scheduled_promises = [self._prom(NOW.timestamp() - 10)]
        state.last_user_msg_ts = NOW.timestamp() - 5                     # 5 秒前剛說話＝在場
        client = FakeClient()
        monitor._promise_emit(client, state, _sched_cfg(), _coach(), NOW)
        self.assertEqual(client.sent, [])
        self.assertFalse(state.scheduled_promises[0]["fulfilled"])      # 約定留存
        # 離場後下一拍 → 補發
        state.last_user_msg_ts = NOW.timestamp() - 99999
        client2 = FakeClient()
        monitor._promise_emit(client2, state, _sched_cfg(), _coach(), NOW)
        self.assertEqual(len(client2.sent), 1)
        self.assertTrue(state.scheduled_promises[0]["fulfilled"])

    def test_punctual_even_when_coupling_round_still_open(self):
        # 🤝 截圖「晚了七分鐘」根因：耦合輪 I_bot 半衰期 10 分、要 ~29 分才衰到收掉；用它擋會把「約 1:00」延到 ~1:07。
        # 修：排程承諾只避「正在打字」（最近 _PROMISE_DEFER_RECENT_SEC 內有訊息），耦合輪還溫著也照時兌現。
        state = self._state()
        state.scheduled_promises = [self._prom(NOW.timestamp() - 5)]      # 剛到點
        state.last_user_msg_ts = NOW.timestamp() - 24 * 60               # 24 分前最後說話＝早就沒在打字
        if getattr(state, "coupling", None):                            # 耦合輪「還開著」（舊行為會因此延後）
            state.coupling.round_open = True
            state.coupling.i_bot, state.coupling.i_user = 0.3, 0.2
        client = FakeClient()
        monitor._promise_emit(client, state, _sched_cfg(), _coach(), NOW)
        self.assertEqual(len(client.sent), 1)                            # 準時兌現、不被耦合輪延後
        self.assertTrue(state.scheduled_promises[0]["fulfilled"])

    def test_defers_only_while_actively_typing(self):
        # 最近 120 秒內剛打字 → 延後（不撞正說的話）；但這只是「正在打字」的短窗、非耦合輪的長尾
        state = self._state()
        state.scheduled_promises = [self._prom(NOW.timestamp() - 5)]
        state.last_user_msg_ts = NOW.timestamp() - 30                    # 30 秒前剛打字
        client = FakeClient()
        monitor._promise_emit(client, state, _sched_cfg(), _coach(), NOW)
        self.assertEqual(client.sent, [])
        self.assertFalse(state.scheduled_promises[0]["fulfilled"])

    def test_overdue_promise_fires_even_when_user_present(self):
        # 🤝 逾期欠債（到點後 > grace 180s）＋使用者在場 → 仍補發（不被在場閘永久壓住）；截圖：12:47 沒發、13:02 回來時補上
        state = self._state()
        state.scheduled_promises = [self._prom(NOW.timestamp() - 600)]   # 逾期 10 分鐘
        state.last_user_msg_ts = NOW.timestamp() - 5                     # 在場（正在互動）
        client = FakeClient()
        monitor._promise_emit(client, state, _sched_cfg(), _coach(), NOW)
        self.assertEqual(len(client.sent), 1)                            # 逾期照補發
        self.assertTrue(state.scheduled_promises[0]["fulfilled"])

    def test_overdue_exempt_flag_off_still_defers(self):
        # PROMISE_LATE_EXEMPT_DEFER=0 → 逾期＋在場仍延後（逐位元同舊行為）
        state = self._state()
        state.scheduled_promises = [self._prom(NOW.timestamp() - 600)]
        state.last_user_msg_ts = NOW.timestamp() - 5
        cfg = _sched_cfg()
        cfg.promise_late_exempt_defer = False
        client = FakeClient()
        monitor._promise_emit(client, state, cfg, _coach(), NOW)
        self.assertEqual(client.sent, [])
        self.assertFalse(state.scheduled_promises[0]["fulfilled"])

    def test_not_blocked_by_shared_cooldown(self):
        # 🤝 守約優先於自我獨白反堆疊冷卻：last_push_ts 剛被別的推播更新（30min cooldown 內）＋到點＋不在場 → 仍兌現
        state = self._state()
        state.scheduled_promises = [self._prom(NOW.timestamp() - 10)]
        state.last_push_ts = NOW.timestamp() - 300                      # 5 分鐘前剛推過別的（在 30min cooldown 內、但超過 90s 守門）
        client = FakeClient()
        monitor._promise_emit(client, state, _sched_cfg(), _coach(), NOW)
        self.assertEqual(len(client.sent), 1)

    def test_flag_off_does_nothing(self):
        # 🤝 PROMISE_EMIT_ENABLED=0 → 開頭直接 return、不發（逐位元退路）
        state = self._state()
        state.scheduled_promises = [self._prom(NOW.timestamp() - 10)]
        cfg = _sched_cfg()
        cfg.promise_emit_enabled = False
        client = FakeClient()
        monitor._promise_emit(client, state, cfg, _coach(), NOW)
        self.assertEqual(client.sent, [])
        self.assertFalse(state.scheduled_promises[0]["fulfilled"])

    def test_not_yet_due_stays_silent(self):
        # 還沒到點 → 不發、不標
        state = self._state()
        state.scheduled_promises = [self._prom(NOW.timestamp() + 3600)]
        client = FakeClient()
        monitor._promise_emit(client, state, _sched_cfg(), _coach(), NOW)
        self.assertEqual(client.sent, [])
        self.assertFalse(state.scheduled_promises[0]["fulfilled"])


class SchedPersistTest(unittest.TestCase):
    """🤝 state.scheduled_promises 的 save/load：缺鍵乾淨預設、保未兌現、過窗的已兌現不無限膨脹。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _path(self):
        return os.path.join(self.tmp, "s.json")

    def test_missing_key_defaults_empty(self):
        s = State(self._path())
        s.owner_folder_id = "F"
        s.save()
        loaded = State.load(self._path())
        self.assertEqual(list(loaded.scheduled_promises), [])

    def test_roundtrip_keeps_unfulfilled(self):
        s = State(self._path())
        s.scheduled_promises = [{"target_ts": NOW.timestamp() + 100, "action": "a",
                                 "made_ts": NOW.timestamp(), "made_text": "八點打招呼", "fulfilled": False}]
        s.save()
        self.assertEqual(len(State.load(self._path()).scheduled_promises), 1)

    def test_save_prunes_old_fulfilled(self):
        # 已兌現且超過保留窗（KEEP）的承諾 → save 時清掉（不無限膨脹）；未兌現/近期已兌現留著
        import time as _t
        s = State(self._path())
        from telegram_monitor.state import SCHED_PROMISE_KEEP_SEC
        old = _t.time() - SCHED_PROMISE_KEEP_SEC - 100
        s.scheduled_promises = [
            {"target_ts": old, "action": "old", "made_ts": old, "made_text": "x", "fulfilled": True},
            {"target_ts": _t.time(), "action": "new", "made_ts": _t.time(), "made_text": "八點打招呼", "fulfilled": False},
        ]
        s.save()
        kept = State.load(self._path()).scheduled_promises
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["action"], "new")


class GenericDetectorTest(unittest.TestCase):
    """🤝 通用偵測（白名單→指向我的未來動作框架 OR 舊白名單）：道歉/問候/讚美 等行為收得到、過去/查問/純使用者主語不誤觸。"""

    def test_generic_behaviors_hit(self):
        # 截圖根因：道歉/問候/讚美 等以前漏掉的行為現在收得到（且與 feeling 互斥）
        for q in ["9:00 跟我道歉", "問候我：21:20 21:30 21:43", "9:00 讚美我",
                  "8點陪我聊聊", "9:00 鼓勵我", "8點安慰我一下", "晚上9點祝福我"]:
            self.assertTrue(selfstate.is_scheduled_promise_request(q), q)
            self.assertFalse(selfstate.is_feeling_promise_request(q), q)

    def test_past_and_query_guarded(self):
        # 過去式/查問/純使用者主語/無動作 → 不誤收（廣化後仍守得住）
        for q in ["剛剛8點你跟我打招呼了", "你8點有提醒我嗎", "我9:00起床", "8點要開會",
                  "我答應你八點到", "你答應我9點道歉做了嗎"]:
            self.assertFalse(selfstate.is_scheduled_promise_request(q), q)

    def test_leading_self_clause_with_at_me_still_hits(self):
        # 🐛 截圖：句首是「我去…/我要…」自述、真正請求在後段（叫我/提醒我）——原本 at_me 內聯清單漏「叫我」被誤擋、
        #    承諾沒記下、到點沒叫醒。修後：句首『我』+ 任一 at_me 訊號(含叫我) → 仍收；真正的使用者自我承諾(無 at_me)仍擋。
        for q in ["我去小睡一下，11點的時候叫我起床喔", "我去睡一下，11點叫我起床",
                  "我要睡了，八點叫我起床", "我休息一下，11點提醒我", "我先忙，晚上九點叫我起床"]:
            self.assertTrue(selfstate.is_scheduled_promise_request(q), q)
        # 起床鬧鐘抽得出乾淨行為標籤（兌現照做）
        self.assertEqual(selfstate.extract_promise_behavior("11點叫我起床"), "叫他起床")
        # 仍擋：句首『我』無任何 at_me 訊號＝使用者承諾自己
        for q in ["我八點會叫你", "我9:00起床", "我答應你八點到"]:
            self.assertFalse(selfstate.is_scheduled_promise_request(q), q)

    def test_request_tone_promise_not_killed_by_past_guard(self):
        # 帶請求語氣的承諾（可以…嗎）不被過去守門誤殺
        self.assertTrue(selfstate.is_scheduled_promise_request("八點可以提醒我嗎"))

    def test_contact_verbs_and_request_frame_hit(self):
        # 截圖：「等一下20分鐘後你跟我聯繫一下，可以嗎？」聯繫/聯絡/找我/敲我 動詞＋軟性請求框架（可以嗎/好嗎/麻煩）
        # ＋指向我訊號＋可解析時間 → 收得到（過去白名單漏接、承諾沒記下、到點沒履行）
        for q in ["等一下20分鐘後你跟我聯繫一下，可以嗎？", "20分鐘後聯絡我", "十分鐘後敲我一下",
                  "8點跟我聯繫", "三點麻煩你提醒我", "晚上九點跟我聯繫好嗎"]:
            self.assertTrue(selfstate.is_scheduled_promise_request(q), q)
            self.assertFalse(selfstate.is_feeling_promise_request(q), q)

    def test_request_frame_needs_at_me_and_time(self):
        # 軟性請求框架單獨不算：無 at_me 訊號（你三點有空嗎）或無可解析時間（等下找我可以嗎）→ 不誤收
        for q in ["你三點有空嗎", "等下找我可以嗎", "你可以幫我看看記錄嗎"]:
            self.assertFalse(selfstate.is_scheduled_promise_request(q), q)

    def test_contact_verbs_flag_off_falls_back(self):
        # SCHED_PROMISE_GENERIC=0 → 回退舊 _SCHED_ACT 白名單：聯繫/請求框架不再被收（逐位元同現狀）
        with mock.patch.dict(os.environ, {"SCHED_PROMISE_GENERIC": "0"}):
            self.assertFalse(selfstate.is_scheduled_promise_request("20分鐘後跟我聯繫，可以嗎"))
            self.assertTrue(selfstate.is_scheduled_promise_request("八點跟我打招呼"))

    def test_report_data_later_is_promise_not_data_question(self):
        # 截圖根因：「30分鐘後請你再回覆我，你正翻閱到哪一則的記寫」＝晚點回報資料，本身就是承諾；
        # 但內容提到「記寫/哪一則」被 looks_like_data_question 誤判否決 → 承諾沒記下、到點不履行。
        # 通用偵測改由 hit 結構把關（時間＋動作/請求＋指向我），這類『到點主動回報某資料』要收得到。
        for q in ["30分鐘後，請你再回覆我，你正翻閱到哪一則的記寫", "30分鐘後回覆我你翻到哪一則記寫",
                  "三十分鐘後跟我說你讀到哪一則記寫", "8點告訴我我記了幾筆"]:
            self.assertTrue(selfstate.is_scheduled_promise_request(q), q)

    def test_pure_data_query_still_not_promise(self):
        # 真正『現在查資料』缺「未來時間＋指向我動作」結構 → hit=False、不誤收（即使移除 data 一刀切）
        for q in ["你查一下我記了什麼", "現在我記了幾筆", "我三點寫了什麼", "你30分鐘前記了什麼", "你幫我看看記錄"]:
            self.assertFalse(selfstate.is_scheduled_promise_request(q), q)

    def test_report_data_later_flag_off_excluded(self):
        # SCHED_PROMISE_GENERIC=0 → 舊路徑保留 looks_like_data_question 排除（逐位元同舊行為）
        with mock.patch.dict(os.environ, {"SCHED_PROMISE_GENERIC": "0"}):
            self.assertFalse(selfstate.is_scheduled_promise_request("30分鐘後回覆我你翻到哪一則記寫"))

    def test_did_q_in_report_content_with_rel_future_is_promise(self):
        # 截圖根因：「30分鐘之後告訴我你吃飽了沒」——「了沒」是回報的**內容**、不是「你做了嗎」完成質問；
        # 有相對未來框架（N分鐘/小時後）→ 豁免 _SCHED_DID_Q/_SCHED_PAST_Q 守門，收得到（否則承諾沒記下、永不兌現）。
        for q in ["跟你承諾，30分鐘之後告訴我你吃飽了沒？", "30分鐘之後告訴我你吃飽了沒",
                  "十分鐘後告訴我你好了沒", "半小時後回報我你做完了沒"]:
            self.assertTrue(selfstate.is_scheduled_promise_request(q), q)

    def test_did_q_without_rel_future_still_excluded(self):
        # 完成質問（無相對未來，多為絕對/無時間）仍排除：是「你做了嗎」的查問、該走 ledger，不被誤記成新承諾。
        for q in ["你9點道歉做了嗎", "八點的事你做了沒", "你履行了嗎", "你答應我9點道歉做了嗎"]:
            self.assertFalse(selfstate.is_scheduled_promise_request(q), q)

    def test_rel_future_with_past_recall_completion_query_excluded(self):
        # 對抗式審查 medium：相對未來框架在場、但句子其實是『你說十分鐘後…，做了嗎』過去回想質問——
        # 不可被 rel-future 豁免成新承諾（否則記下幻影承諾、到點誤發）。回想語氣（你說/你不是說/本來要）→ 仍守門→落 ledger。
        for q in ["你說十分鐘後告訴我，告訴了嗎", "你不是說十分鐘後要提醒我，提醒了嗎",
                  "半小時後你本來要問候我的，做了沒", "你說過十分鐘後提醒我，你做了嗎"]:
            self.assertFalse(selfstate.is_scheduled_promise_request(q), q)

    def test_old_whitelist_still_green(self):
        # §0.39 既有正例續綠（廣化是 OR 串入、不收緊）
        for q in ["八點跟我打招呼", "晚上八點提醒我", "十分鐘後提醒我", "明天早上八點跟我說", "半小時後叫我",
                  "1:30 也要回報我一次", "3:30 跟我打招呼", "15:30 跟我打招呼"]:
            self.assertTrue(selfstate.is_scheduled_promise_request(q), q)

    def test_generic_flag_off_falls_back(self):
        # SCHED_PROMISE_GENERIC=0 → 回退舊 _SCHED_ACT 白名單：道歉句不再被收（逐位元同現狀）
        with mock.patch.dict(os.environ, {"SCHED_PROMISE_GENERIC": "0"}):
            self.assertFalse(selfstate.is_scheduled_promise_request("9:00 跟我道歉"))
            self.assertTrue(selfstate.is_scheduled_promise_request("八點跟我打招呼"))

    def test_share_report_to_me_is_promise(self):
        # 🤝 §0.55 截圖根因：「到點跟我分享/報告某資訊」本身就是承諾，之前 _SCHED_ACT_GENERIC 沒收「分享/報告」→
        # 沒記下、7:30 不會主動兌現。改後收得到（只收「跟我V」新請求形）。
        for q in ["等下 7:30 跟我分享一下，你正在翻閱哪一則記寫", "7:30 跟我報告你翻到哪一則",
                  "等下八點跟我分享你的感覺", "半小時後跟我報告你的進度"]:
            self.assertTrue(selfstate.is_scheduled_promise_request(q), q)

    def test_share_report_false_positive_guarded(self):
        # 只收「跟我V」新請求形＝避免裸「分享/報告」誤收：無指向我的「3點的分享會」「五點有個報告」不收；無時間也不收。
        for q in ["3點的分享會很精彩", "五點有個報告要聽", "跟我分享一下你翻到哪"]:
            self.assertFalse(selfstate.is_scheduled_promise_request(q), q)

    def test_share_report_not_third_party_or_narration(self):
        # 🤝 對抗式審查回歸：分享/報告給第三人（給我媽/給我主管＝substring 陷阱）、過去/which 查問（分享的…哪一則）、
        # 習慣/敘述（每天都會/上次/念給我…的詩）都**不是新承諾**，不得誤記成幻影承諾（否則 bot 到點主動亂發）。
        for q in ["我打算8點把這則分享給我媽", "8點我要分享給我同事看", "9點我把成果報告給我主管",   # HIGH：第三人受詞
                  "你3點跟我分享的那則記寫是哪一則", "你剛剛跟我分享的是哪則",                         # MED：過去/which 查問
                  "每天8點你都會跟我分享一則", "你8點念給我聽的詩真好", "你上次跟我分享的那則"]:        # LOW：習慣/敘述
            self.assertFalse(selfstate.is_scheduled_promise_request(q), q)


class BehaviorExtractTest(unittest.TestCase):
    """🤝 行為捕捉：extract_promise_behavior 抽出具體行為的中文描述（給兌現照做）。"""

    def test_maps_known_behaviors(self):
        self.assertIn("道歉", selfstate.extract_promise_behavior("9:00 跟我道歉"))
        self.assertIn("問候", selfstate.extract_promise_behavior("問候我：21:20"))
        self.assertIn("時間", selfstate.extract_promise_behavior("8:55 提醒我"))   # 提醒→提醒他『時間快到了』
        self.assertIn("讚美", selfstate.extract_promise_behavior("9:00 讚美我"))
        self.assertIn("聯繫", selfstate.extract_promise_behavior("20分鐘後跟我聯繫一下"))   # 聯繫→主動聯繫他

    def test_no_keyword_returns_empty(self):
        self.assertEqual(selfstate.extract_promise_behavior("9點那個事情"), "")
        self.assertEqual(selfstate.extract_promise_behavior(""), "")

    def test_share_report_behaviors(self):
        # 🤝 §0.55：到點分享/回報 → 乾淨第三人稱標籤（兌現照做，不落回泛「打招呼」）
        self.assertIn("分享", selfstate.extract_promise_behavior("等下7:30跟我分享你翻到哪一則"))
        self.assertIn("回報", selfstate.extract_promise_behavior("半小時後報告給我你的進度"))


class TemporalSpaceTest(unittest.TestCase):
    """🤝🔢 空白分隔多時刻 H:MM＋全形冒號修（截圖『21:20 21:30 21:43 到點全沒發』時間層根因）。"""

    def test_space_separated_multi_hhmm(self):
        from telegram_monitor import temporal
        self.assertEqual(len(temporal.all_clock_epochs("問候我 21:20 21:43", NOW, TZ)), 2)
        self.assertEqual(len(temporal.all_clock_epochs("問候我：21:20 21:30 21:43", NOW, TZ)), 3)

    def test_fullwidth_colon(self):
        from telegram_monitor import temporal
        self.assertEqual(len(temporal.all_clock_epochs("提醒我：8:55", NOW, TZ)), 1)

    def test_fullwidth_colon_not_a_time(self):
        # 非時間的全形冒號（提醒我：明天）不被誤匹配成 H:MM 假時刻
        from telegram_monitor import temporal
        self.assertEqual(temporal.all_clock_epochs("提醒我：明天", NOW, TZ), [])

    def test_relative_and_clock_not_regressed(self):
        # 中文 X點/X點半、相對量『十分鐘後』『十 分鐘 後』不回歸（兩前處理並存不互相破壞）
        from telegram_monitor import temporal
        self.assertEqual(len(temporal.all_clock_epochs("十分鐘後提醒我", NOW, TZ)), 1)
        self.assertEqual(len(temporal.all_clock_epochs("十 分鐘 後提醒我", NOW, TZ)), 1)
        self.assertEqual(len(temporal.all_clock_epochs("八點半提醒我", NOW, TZ)), 1)

    def test_flag_off_reverts(self):
        # SCHED_TIME_SPACE_FIX=0 → 空白被吃→多時刻只剩無法分隔（回退原行為）；相對量仍解析
        from telegram_monitor import temporal
        with mock.patch.dict(os.environ, {"SCHED_TIME_SPACE_FIX": "0"}):
            self.assertEqual(temporal.all_clock_epochs("問候我 21:20 21:43", NOW, TZ), [])
            self.assertEqual(len(temporal.all_clock_epochs("十分鐘後提醒我", NOW, TZ)), 1)


class LedgerDetectTest(unittest.TestCase):
    """🤝 整理承諾/帳本質問偵測：整理/列/還記得/做了嗎/你忘了 收得到；新請求/feeling/資料句不誤搶。"""

    def test_ledger_questions_hit(self):
        for q in ["整理一下你的承諾", "你的約定有哪些", "你還記得答應我什麼",
                  "你忘了答應我的事", "你答應我的事做了嗎"]:
            self.assertTrue(selfstate.is_promise_ledger_question(q), q)

    def test_non_ledger_not_hit(self):
        # 新請求（未來式、無回顧框架）、feeling、一般資料句 → 不收（讓給 scheduled/feeling/data）
        for q in ["你可以承諾我等一下八點的時候跟我打個招呼嗎", "八點跟我打招呼", "明天有想法跟我說",
                  "我那段記寫", "你現在有什麼感覺", "你答應過要更了解我"]:
            self.assertFalse(selfstate.is_promise_ledger_question(q), q)

    def test_what_recall_hits(self):
        # 🤝 §0.55 截圖根因：「我們有約定什麼／你答應我什麼」＝回顧問句（cue＋『什麼』、無鐘點），之前不收→落一般聊天、
        # LLM 腦補約定又沒接地此刻 → 假造「現在就是 7:30 了」。改後收得到、走據帳本接地路徑。
        for q in ["我們有約定什麼", "你答應我什麼", "我們剛剛有約定什麼", "承諾我什麼了嗎"]:
            self.assertTrue(selfstate.is_promise_ledger_question(q), q)

    def test_what_recall_does_not_steal_future_request(self):
        # 帶鐘點的多是未來新請求（『你可以承諾我八點打招呼嗎』），不因『什麼』被搶去 ledger（讓給 scheduled_promise）
        for q in ["你可以承諾我八點跟我做什麼嗎", "等一下八點你要跟我說什麼"]:
            self.assertFalse(selfstate.is_promise_ledger_question(q), q)

    def test_what_recall_does_not_steal_clockless_new_request(self):
        # 🤝 對抗式審查 med：無鐘點但帶請求/未來祈使詞（可以/好嗎/等下…）的**新請求**不被『什麼』支搶去 ledger
        for q in ["你可以承諾我做點什麼嗎", "答應我做點什麼好嗎", "答應我等下跟我分享點什麼"]:
            self.assertFalse(selfstate.is_promise_ledger_question(q), q)
        # 但真回顧（含『了嗎』但無請求詞）仍收
        self.assertTrue(selfstate.is_promise_ledger_question("承諾我什麼了嗎"))

    def test_what_recall_flag_off_reverts(self):
        # PROMISE_LEDGER_WHAT=0 → 回退不收『有約定什麼』（逐位元同舊）
        with mock.patch.dict(os.environ, {"PROMISE_LEDGER_WHAT": "0"}):
            self.assertFalse(selfstate.is_promise_ledger_question("我們有約定什麼"))
            self.assertTrue(selfstate.is_promise_ledger_question("你的約定有哪些"))   # 舊路徑正例仍綠


class LedgerFactsTest(unittest.TestCase):
    """🤝 promise_ledger_facts/_text：據帳本報對 已做/待做/逾時＋過去/未來，不洩 epoch/欄位名，空帳本誠實。"""

    def _state(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        return s

    def _three_state(self):
        s = self._state()
        now = NOW.timestamp()
        s.scheduled_promises = [
            # 已做（fulfilled+fulfilled_ts，target 在過去）
            {"target_ts": now - 7200, "action": "x", "behavior": "向他道歉",
             "made_ts": now - 9000, "made_text": "跟我道歉", "fulfilled": True,
             "status": "fulfilled", "fulfilled_ts": now - 7100},
            # 待做（pending、target>now）
            {"target_ts": now + 3600, "action": "y", "behavior": "問候他",
             "made_ts": now - 100, "made_text": "問候我", "fulfilled": False, "status": "pending"},
            # 逾時（expired）
            {"target_ts": now - 99999, "action": "z", "behavior": "讚美他",
             "made_ts": now - 99999, "made_text": "讚美我", "fulfilled": True,
             "status": "expired", "fulfilled_ts": now - 90000},
        ]
        return s

    def test_three_states_reported_correctly(self):
        s = self._three_state()
        facts = selfstate.promise_ledger_facts(s, NOW, TZ)
        # 已做的報「已經做了」（非未來式）、待做報「還沒」、逾時報「沒做到」
        self.assertIn("已經做了", facts)
        self.assertIn("還沒", facts)
        self.assertIn("沒做到", facts)
        # 不洩 epoch 數字或欄位名
        for leak in ("target_ts", "fulfilled_ts", "made_ts", "behavior", "status"):
            self.assertNotIn(leak, facts)
        self.assertNotIn(str(int(NOW.timestamp())), facts)

    def test_empty_ledger_honest(self):
        s = self._state()
        self.assertIn("沒記著", selfstate.promise_ledger_facts(s, NOW, TZ))
        self.assertIn("沒記著", selfstate.promise_ledger_text(s, NOW, TZ))

    def test_facts_open_with_now_anchor_and_distance(self):
        # 🤝 §0.55 修「現在就是 7:30 了」：facts 開頭錨此刻幾點（NOW=20:00 台北）＋叮嚀別把約定時刻說成現在；
        # 未來承諾附「距現在約 N」；錨在承諾清單之前。
        s = self._three_state()
        facts = selfstate.promise_ledger_facts(s, NOW, TZ)
        self.assertIn("此刻真的是 20:00", facts)
        self.assertIn("別把", facts)                              # 明令別把約定時刻說成現在
        self.assertIn("距現在約 1 小時", facts)                    # 待做 target=now+3600
        self.assertLess(facts.index("此刻真的是"), facts.index("約在"))   # 錨在清單之前
        self.assertNotIn(str(int(NOW.timestamp())), facts)        # 仍不洩 epoch

    def test_facts_anchor_present_even_when_empty(self):
        # 空帳本也先錨此刻幾點（讓「沒記著」的回覆一樣有現在時間定位）
        s = self._state()
        facts = selfstate.promise_ledger_facts(s, NOW, TZ)
        self.assertIn("此刻真的是 20:00", facts)
        self.assertIn("沒記著", facts)

    def test_distance_brief_tiers(self):
        # 🤝 對抗式審查 low：分鐘/小時/天三階（>24h 進位到天，不再「約 240 小時」）；已到/過去回空
        now = NOW.timestamp()
        self.assertEqual(selfstate._distance_brief(now + 52 * 60, now), "（距現在約 52 分鐘）")
        self.assertEqual(selfstate._distance_brief(now + 3 * 3600, now), "（距現在約 3 小時）")
        self.assertEqual(selfstate._distance_brief(now + 25 * 3600, now), "（距現在約 1 天）")
        self.assertEqual(selfstate._distance_brief(now + 240 * 3600, now), "（距現在約 10 天）")
        self.assertEqual(selfstate._distance_brief(now - 60, now), "")          # 已過 → 空
        self.assertEqual(selfstate._distance_brief(now + 20, now), "（距現在約 1 分鐘）")   # <1 分 → 保底 1

    def test_text_fallback_groups(self):
        s = self._three_state()
        txt = selfstate.promise_ledger_text(s, NOW, TZ)
        self.assertIn("已經做了的", txt)
        self.assertIn("還沒到", txt)
        self.assertIn("沒做到", txt)


class LedgerSchemaCompatTest(unittest.TestCase):
    """🤝 舊筆（只有 fulfilled 布林、無 status/behavior/fulfilled_ts）容缺補後正確反推；舊存檔可載。"""

    def _state(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        return s

    def test_old_record_status_reverse_inferred(self):
        s = self._state()
        now = NOW.timestamp()
        # 舊筆：無 status/behavior/fulfilled_ts，只有布林
        s.scheduled_promises = [
            {"target_ts": now - 100, "action": "跟我道歉", "made_ts": now - 3600,
             "made_text": "9:00 跟我道歉", "fulfilled": True},                       # → fulfilled
            {"target_ts": now + 100, "action": "問候我", "made_ts": now,
             "made_text": "問候我", "fulfilled": False},                             # → pending
            {"target_ts": now - 200, "action": "讚美我", "made_ts": now - 3600,
             "made_text": "讚美我", "fulfilled": True, "expired": True},             # → expired
        ]
        view = selfstate._ledger_view(s, now)
        statuses = [v["status"] for v in sorted(view, key=lambda x: x["target_ts"])]
        # 排序後：expired(now-200), fulfilled(now-100), pending(now+100)
        self.assertEqual(statuses, ["expired", "fulfilled", "pending"])
        # behavior 由 made_text 反推
        behs = {v["status"]: v["behavior"] for v in view}
        self.assertIn("道歉", behs["fulfilled"])
        self.assertIn("問候", behs["pending"])
        # 純讀（不寫回 p）：原 dict 仍無 status/behavior 鍵
        self.assertNotIn("status", s.scheduled_promises[0])
        self.assertNotIn("behavior", s.scheduled_promises[0])

    def test_old_savefile_loads(self):
        import json
        p = os.path.join(tempfile.mkdtemp(), "old.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"owner_folder_id": "F", "scheduled_promises": [
                {"target_ts": 1.0, "action": "a", "made_ts": 1.0, "made_text": "x", "fulfilled": False}
            ]}, f)
        loaded = State.load(p)        # 無 status/behavior/fulfilled_ts → 不報錯
        self.assertEqual(len(loaded.scheduled_promises), 1)


class PromiseEmitBehaviorTest(unittest.TestCase):
    """🤝 兌現按行為對齊＋狀態落帳。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _state(self):
        s = State(os.path.join(self.tmp, "state.json"))
        s.owner_folder_id = "F"
        return s

    def _recording_coach(self):
        seen = {}

        def vpk(when, facts, h, promised="", late=False, feeling_ground="", sticker_sent=False, **k):
            seen["promised"] = promised
            seen["late"] = late
            seen["feeling_ground"] = feeling_ground
            seen["sticker_sent"] = sticker_sent
            return "（守約）我來赴約了。"
        c = _coach()
        c.voice_promise_keep = vpk
        return c, seen

    def test_behavior_passed_to_voice(self):
        state = self._state()
        p = self._prom_with_behavior(NOW.timestamp() - 10, "向他道歉")
        state.scheduled_promises = [p]
        coach, seen = self._recording_coach()
        monitor._promise_emit(FakeClient(), state, _sched_cfg(), coach, NOW)
        self.assertIn("道歉", seen.get("promised", ""))

    def test_overdue_passes_late_to_voice(self):
        # 🤝 逾期補發時 late=True 傳進 voice（帶遲到致歉）；剛到點則 late=False
        state = self._state()
        state.scheduled_promises = [self._prom_with_behavior(NOW.timestamp() - 600, "向他道歉")]  # 逾期 10 分
        coach, seen = self._recording_coach()
        monitor._promise_emit(FakeClient(), state, _sched_cfg(), coach, NOW)
        self.assertTrue(seen.get("late"))

    def test_just_due_passes_not_late(self):
        state = self._state()
        state.scheduled_promises = [self._prom_with_behavior(NOW.timestamp() - 10, "向他道歉")]   # 剛到點
        coach, seen = self._recording_coach()
        monitor._promise_emit(FakeClient(), state, _sched_cfg(), coach, NOW)
        self.assertFalse(seen.get("late"))

    def test_act_aligned_flag_off_no_promised(self):
        state = self._state()
        p = self._prom_with_behavior(NOW.timestamp() - 10, "向他道歉")
        state.scheduled_promises = [p]
        coach, seen = self._recording_coach()
        cfg = _sched_cfg()
        cfg.promise_act_aligned = False
        monitor._promise_emit(FakeClient(), state, cfg, coach, NOW)
        self.assertEqual(seen.get("promised", ""), "")     # 不傳行為＝走原打招呼路徑

    def test_status_and_fulfilled_ts_on_keep(self):
        state = self._state()
        p = self._prom_with_behavior(NOW.timestamp() - 10, "問候他")
        state.scheduled_promises = [p]
        monitor._promise_emit(FakeClient(), state, _sched_cfg(), _coach(), NOW)
        rp = state.scheduled_promises[0]
        self.assertTrue(rp["fulfilled"])                   # 既有布林斷言不破
        self.assertEqual(rp["status"], "fulfilled")        # 新狀態
        self.assertAlmostEqual(rp["fulfilled_ts"], NOW.timestamp(), delta=2)

    def test_status_on_expire(self):
        state = self._state()
        old = NOW.timestamp() - 7 * 3600
        p = self._prom_with_behavior(old, "向他道歉")
        p["made_ts"] = old - 3600
        state.scheduled_promises = [p]
        monitor._promise_emit(FakeClient(), state, _sched_cfg(), _coach(), NOW)
        rp = state.scheduled_promises[0]
        self.assertTrue(rp["fulfilled"])
        self.assertTrue(rp.get("expired"))
        self.assertEqual(rp["status"], "expired")
        self.assertIsNotNone(rp.get("fulfilled_ts"))

    def _prom_with_behavior(self, target_ts, behavior):
        return {"target_ts": target_ts, "action": "八點打招呼",
                "made_ts": target_ts - 3600, "made_text": "八點跟我打招呼",
                "fulfilled": False, "behavior": behavior, "status": "pending"}


class LedgerRoutingTest(unittest.TestCase):
    """🤝 promise_ledger 分支：讀帳本 grounded 報帳（含 facts、不腦補）；旗標 0 落回 fact_or_chat。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _state(self):
        s = State(os.path.join(self.tmp, "state.json"))
        s.owner_folder_id = "F"
        now = NOW.timestamp()
        s.scheduled_promises = [
            {"target_ts": now - 7200, "action": "x", "behavior": "向他道歉", "made_ts": now - 9000,
             "made_text": "跟我道歉", "fulfilled": True, "status": "fulfilled", "fulfilled_ts": now - 7100},
            {"target_ts": now + 3600, "action": "y", "behavior": "問候他", "made_ts": now - 100,
             "made_text": "問候我", "fulfilled": False, "status": "pending"},
        ]
        return s

    def _ledger_coach(self, captured):
        c = _coach()
        c.reply = lambda *a, **k: (captured.update({"facts": a[1] if len(a) > 1 else k.get("memory_brief"),
                                                    "extra": k.get("extra_system")}) or "（整理）我答應過你這些。")
        return c

    def _cfg(self):
        return SimpleNamespace(dry_run=False, telegram_chat_id="", scheduled_promise_enabled=True,
                              promise_emit_enabled=True, promise_sched_ttl_sec=21600, timezone="Asia/Taipei",
                              notify_cooldown_min=30, promise_ledger_enabled=True, promise_act_aligned=True)

    def test_ledger_branch_reports_from_ledger(self):
        state = self._state()
        client = FakeClient()
        captured = {}
        update = {"message": {"chat": {"id": 1}, "text": "整理一下你的承諾", "date": NOW.timestamp()}}
        boom = SimpleNamespace(load_embedding_records=lambda *_: (_ for _ in ()).throw(AssertionError("不該報現況")))
        monitor.handle_message(update, self._ledger_coach(captured), boom, {"meta": {}}, None, state, client,
                               self._cfg(), TZ)
        self.assertEqual(len(client.sent), 1)
        # facts 據帳本：已做的報已做（非未來式）、待做報還沒
        self.assertIn("已經做了", captured["facts"])
        self.assertIn("還沒", captured["facts"])
        self.assertIsNotNone(captured["extra"])           # grounded 守則注入
        # 純讀路徑：不把容缺補欄寫回原 dict（本測資已自帶 status，仍驗 in-memory 計算不污染既有結構）
        self.assertEqual(len(state.scheduled_promises), 2)

    def test_ledger_flag_off_falls_through(self):
        # promise_ledger_enabled=0 → intent 不路由 promise_ledger（落回 fact_or_chat、不攔截）
        from telegram_monitor import intent, referent
        off = SimpleNamespace(promise_ledger_enabled=False)
        self.assertNotEqual(intent.resolve("整理一下你的承諾", referent.Referent(), cfg=off).kind, "promise_ledger")


class BackCompatTest(unittest.TestCase):
    """🤝 向後相容簽名：舊呼叫不報錯。"""

    def test_persona_two_arg_call(self):
        from telegram_monitor import persona
        self.assertTrue(persona.promise_keep_user("20:00", "〔此刻 20:00〕"))   # 2 參舊呼叫不破

    def test_persona_promised_kwarg(self):
        from telegram_monitor import persona
        out = persona.promise_keep_user("20:00", "〔此刻 20:00〕", promised="向他道歉")
        self.assertIn("向他道歉", out)


class PromiseKeepGroundingTest(unittest.TestCase):
    """🤝 守約 voice 接地（修截圖兩錯）：① 主詞＝我（記得/守約是 bot 自己、別說成「你記得」）；
    ② 問候對得上此刻時段（清晨別跟著回對方故意說錯的晚安）。ground=False 退回原措辭（逐位元同舊）。"""

    def test_ground_on_has_subject_and_timeslot_notes(self):
        from telegram_monitor import persona
        for promised in ("問候他", ""):                       # 行為對齊與泛打招呼兩支都要接地
            out = persona.promise_keep_user("06:45", "〔此刻真的是 06:46、清晨〕", promised=promised, ground=True)
            self.assertIn("第一人稱", out)                    # ① 主詞＝我
            self.assertIn("你記得", out)                      # 明確點名要避開的錯講法
            self.assertIn("早安", out)                        # ② 時段問候對照表
            self.assertIn("晚安", out)                        # 明確點名清晨別回「晚安」

    def test_ground_off_byte_identical_to_original(self):
        # 旗標關＝**逐位元**還原舊措辭（接地塊整段不在、且 1–2 句。後緊接「直接給話」無多餘換行）
        # §0.67：舊措辭含「大約在」＝準確時刻守則亦需關（time_exact=False）——兩旗標皆關才是原始基線。
        from telegram_monitor import persona
        off_promised = persona.promise_keep_user("06:45", "〔此刻 06:46〕", promised="問候他",
                                                 ground=False, time_exact=False)
        old_promised = (
            "現在到了你之前和對方約好的時間，輪到你**主動守約**了。（你之前答應他大約在 06:45 主動為他做一件事）\n"
            "〔此刻 06:46〕\n你當初答應他的具體事情是：「問候他」。請**就做那件事**——若是道歉就誠懇道歉、"
            "若是提醒就提醒他『時間快到了／時間到了』、若是問候就溫暖問候、若是讚美就真誠肯定他；"
            "輕輕點到這是你說過要做的（守約的語氣，不是困惑的問候、也不是在回應他剛說的話——是你主動來赴約）；"
            "可順帶一句真誠不肉麻的肯定。融入此刻的時段、口語、有溫度、1–2 句。"
            "直接給話、不要前言、不要署名、不要報數據/清單。")
        self.assertEqual(off_promised, old_promised)
        off_plain = persona.promise_keep_user("06:45", "〔此刻 06:46〕", ground=False, time_exact=False)
        old_plain = (
            "現在到了你之前和對方約好的時間，輪到你**主動守約**了。（你之前答應他大約在 06:45 主動跟他打招呼）\n"
            "〔此刻 06:46〕\n請主動跟他打個招呼，自然地**輕輕點到『我說過這時候會跟你打招呼』**（守約的語氣，不是困惑的問候、"
            "也不是在回應他剛說的話——是你主動來赴約）；融入此刻的時段、口語、有溫度、1–2 句。"
            "直接給話、不要前言、不要署名、不要報數據/清單。")
        self.assertEqual(off_plain, old_plain)

    def test_default_is_grounded(self):
        from telegram_monitor import persona
        self.assertIn("第一人稱", persona.promise_keep_user("06:45", "〔此刻 06:46〕", promised="問候他"))

    def test_coach_threads_flag(self):
        # Coach 讀 cfg.promise_keep_grounding → 傳進 promise_keep_user（旗標關＝原措辭）
        from telegram_monitor import coach as cm, persona
        seen = {}

        def fake(api, model, system, contents, **kw):
            seen["user"] = contents[-1]["parts"][0]["text"]
            return "好"
        for flag, expect in ((True, True), (False, False)):
            c = cm.Coach(SimpleNamespace(gemini_api_key="k", gemini_model="m", promise_keep_grounding=flag))
            with mock.patch("telegram_monitor.coach.gemini.generate_chat", side_effect=fake):
                c.voice_promise_keep("06:45", "〔此刻 06:46、清晨〕", [], promised="問候他")
            self.assertEqual("第一人稱" in seen["user"], expect, flag)


class ScheduleTimeExactTest(unittest.TestCase):
    """🤝 §0.67：程式算好的目標時刻餵給 voice 時講**準確、一字不改**——修截圖「8:19 說 20 分鐘（＝8:39），
    bot 卻答二十九分（8:29）」＝原「大約是 {when}」授權 LLM 亂改時刻。"""

    def test_schedule_ack_exact_authoritative(self):
        from telegram_monitor import persona
        out = persona.schedule_ack_user("我要離開20分鐘，等等叫我", "08:39", exact=True)
        self.assertIn("08:39", out)
        self.assertIn("準確時刻", out)                        # 講成權威時刻
        self.assertIn("一個都不能改", out)                    # 明令時分數字不變
        self.assertIn("不准說「大約」", out)                  # 明令不准說大約
        self.assertNotIn("大約是", out)                       # 不再有「（那個時間大約是 …）」

    def test_schedule_ack_off_byte_identical(self):
        from telegram_monitor import persona
        off = persona.schedule_ack_user("我要離開20分鐘", "08:39", exact=False)
        old = ("對方請你『到某個時間點主動為他做一件事』——例如到點主動跟他打招呼／提醒他。（那個時間大約是 08:39）"
               "請自然、簡短地**答應這個約定**，讓他知道你記住了、到時候會主動（像朋友答應一件事那樣，融入對話節奏）；"
               "**別講得像機器人、別把同一句話重複三遍、別列數據、別反問、別現在就做那件事**。1–2 句、口語、有溫度。"
               "\n\n對方說：我要離開20分鐘")
        self.assertEqual(off, old)

    def test_schedule_ack_no_time_no_clause(self):
        from telegram_monitor import persona
        out = persona.schedule_ack_user("等等叫我", "", exact=True)   # 無 when → 不注入準確守則
        self.assertNotIn("準確時刻", out)

    def test_promise_keep_exact_no_approx(self):
        from telegram_monitor import persona
        for promised in ("問候他", ""):
            out = persona.promise_keep_user("08:39", "〔此刻 08:40〕", promised=promised, time_exact=True)
            self.assertIn("準確時刻", out)
            self.assertIn("一個都不能改", out)
            self.assertNotIn("大約在", out)                   # 去掉「大約在 {when}」

    def test_promise_keep_off_keeps_approx(self):
        from telegram_monitor import persona
        out = persona.promise_keep_user("08:39", "〔此刻〕", promised="問候他", time_exact=False)
        self.assertIn("大約在 08:39", out)                    # 旗標關＝退回舊措辭
        self.assertNotIn("準確時刻", out)

    def test_coach_threads_schedule_time_exact(self):
        from telegram_monitor import coach as cm
        seen = {}

        def fake(api, model, system, contents, **kw):
            seen["user"] = contents[-1]["parts"][0]["text"]
            return "好"
        for flag, want_exact in ((True, True), (False, False)):
            c = cm.Coach(SimpleNamespace(gemini_api_key="k", gemini_model="m", schedule_time_exact=flag))
            from datetime import datetime as _dt, timezone as _tz
            local = _dt(2026, 7, 5, 8, 39, tzinfo=_tz.utc)
            with mock.patch("telegram_monitor.coach.gemini.generate_chat", side_effect=fake):
                c.voice_schedule_ack("我要離開20分鐘", local, [])
            self.assertEqual("準確時刻" in seen["user"], want_exact, flag)


class TimedFeelingPromiseTest(unittest.TestCase):
    """🤝 §0.63：帶明確期限的『到點跟我說你的感覺』＝時間排程（到點主動兌現），不再被感覺互斥吞進條件觸發的 feeling 帳本。"""

    def test_screenshot_timed_feeling_is_scheduled(self):
        m = "我先去忙 27 分鐘，到時候再跟我說說妳自己的感覺"
        self.assertTrue(selfstate.is_scheduled_promise_request(m))          # 期限勝過感覺詞
        self.assertTrue(selfstate.is_feeling_promise_request(m))            # 仍是感覺內容（但走排程）
        eps = temporal.all_clock_epochs(m, NOW, TZ)
        self.assertEqual(len(eps), 1)
        self.assertAlmostEqual(eps[0] - NOW.timestamp(), 27 * 60, delta=1)
        self.assertEqual(selfstate.extract_promise_behavior(m), "跟他說說我此刻的內在")

    def test_flag_off_reverts_to_feeling(self):
        import os
        m = "我先去忙 27 分鐘，到時候再跟我說說妳自己的感覺"
        os.environ["SCHED_FEELING_TIME_WINS"] = "0"
        try:
            self.assertFalse(selfstate.is_scheduled_promise_request(m))     # 逐位元回退舊互斥
        finally:
            del os.environ["SCHED_FEELING_TIME_WINS"]

    def test_no_time_feeling_stays_feeling(self):
        for m in ("之後有感覺再跟我說", "等你真的有新東西再告訴我"):
            self.assertFalse(selfstate.is_scheduled_promise_request(m), m)  # 無期限→仍感覺觸發
            self.assertTrue(selfstate.is_feeling_promise_request(m), m)

    def test_emergence_conditional_with_time_stays_feeling(self):
        # load-bearing：帶湧現條件（有新感覺/如果）即使夾了時距，仍走 feeling——不為趕點捏造感覺
        for m in ("半小時後有新感覺再跟我說", "27分鐘後如果有感覺再跟我說"):
            self.assertFalse(selfstate.is_scheduled_promise_request(m), m)

    def test_emergence_synonym_有感受_stays_feeling(self):
        # 審查 confirmed HIGH：每個感覺詞的「有X」記號都要齊——「有感受再說」須與「有感覺再說」一致留 feeling
        for m in ("10分鐘後有感受再跟我說", "半小時後有感受再告訴我"):
            self.assertFalse(selfstate.is_scheduled_promise_request(m), m)

    def test_nonfeeling_status_not_mislabeled(self):
        # 審查 confirmed MED：狀態/心情/想法 的**非情緒**用法（伺服器狀態/老闆的想法/我媽的心情）不得被標成分享 bot 感覺
        for m in ("10分鐘後跟我說一下伺服器狀態", "15分鐘後告訴我大盤狀態",
                  "20分鐘後跟我說說你老闆的想法", "10分鐘後跟我說我媽的心情"):
            b = selfstate.extract_promise_behavior(m)
            self.assertNotIn("此刻", b, m)                 # 不是「跟他說說我此刻的X」感覺分享標籤

    def test_self_bound_feeling_labels(self):
        # 綁定 bot 自己（你/妳/自己）才算感覺分享，且保留名詞細分
        self.assertEqual(selfstate.extract_promise_behavior("27分鐘後跟我說你此刻的心情"), "跟他說說我此刻的心情")
        self.assertEqual(selfstate.extract_promise_behavior("27分鐘後跟我說你的感受"), "跟他說說我此刻的感受")

    def test_behavior_ordering_base_wins(self):
        # 基底鍵（報告→回報）優先於感覺鍵（狀態），免「跟我報告專案狀態」誤判成感覺分享
        self.assertEqual(selfstate.extract_promise_behavior("10分鐘後跟我報告專案狀態"), "跟他回報")

    def test_feeling_behavior_flag_off(self):
        import os
        os.environ["SCHED_FEELING_BEHAVIOR"] = "0"
        try:
            self.assertEqual(selfstate.extract_promise_behavior("到點跟我說說你的感覺"), "")
        finally:
            del os.environ["SCHED_FEELING_BEHAVIOR"]

    def test_five_min_greeting_unregressed(self):
        m = "5分鐘之後可以問候我嗎"
        self.assertTrue(selfstate.is_scheduled_promise_request(m))
        self.assertEqual(selfstate.extract_promise_behavior(m), "問候他")   # 感覺旗標不動它

    def test_msg_verb_capture_and_guard(self):
        # 「主動傳訊息」動詞：乾淨計時請求可捕捉；無時間的空口跟進句觸發守門偵測
        self.assertTrue(selfstate.is_scheduled_promise_request("27分鐘後主動傳訊息給我"))
        self.assertEqual(selfstate.extract_promise_behavior("27分鐘後主動傳訊息給我"), "主動傳訊息給他")
        self.assertTrue(selfstate.looks_like_timed_request("時間到時你要主動傳訊息給我知道"))

    def test_feeling_ground_passed_on_fire(self):
        # 到點兌現感覺約定時，把 bot 此刻真實內在讀數傳進兌現 voice（據實、非編造）
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        s.affect = {"label": "悶悶的", "tendency": "down"}
        s.scheduled_promises = [{"target_ts": NOW.timestamp() - 10, "action": "說說你的感覺",
                                 "behavior": "跟他說說我此刻的感覺", "made_ts": NOW.timestamp() - 1600,
                                 "made_text": "27分鐘後跟我說你的感覺", "fulfilled": False, "status": "pending"}]
        seen = {}

        def vpk(when, facts, h, promised="", late=False, feeling_ground="", sticker_sent=False, **k):
            seen["fg"] = feeling_ground
            return "（守約）我這會兒悶悶的。"
        c = _coach()
        c.voice_promise_keep = vpk
        monitor._promise_emit(FakeClient(), s, _sched_cfg(), c, NOW)
        self.assertIn("悶悶的", seen.get("fg", ""))                        # 真實內在讀數在場


class PromiseCapabilityGateTest(unittest.TestCase):
    """🤝 §0.64 原則一：做不到就不能答應＋說明原因（外部動作/高頻重複/外部條件）。"""

    def test_unsupported_kinds(self):
        for m, want in (("8點幫我打電話給媽媽，可以嗎", "external_action"),
                        ("10分鐘後幫我跟老闆說我請假", "external_action"),
                        ("7點幫我訂餐廳", "external_action"),
                        ("每小時提醒我喝水", "recur_unsupported"),
                        ("每30分鐘提醒我休息", "recur_unsupported"),
                        ("如果下雨的話提醒我帶傘", "external_condition"),
                        ("等他回我訊息的時候通知我", "external_condition")):
            self.assertEqual(selfstate.promise_unsupported(m), want, m)

    def test_supported_not_refused(self):
        # 做得到的絕不誤拒：單次/每天/感覺分享/湧現託付/一般提醒
        for m in ("8點提醒我開會", "每天早上8點叫我起床", "27分鐘後跟我說你的感覺",
                  "之後有感覺再跟我說", "等一下叫我起床", "30分鐘後主動傳訊息給我"):
            self.assertEqual(selfstate.promise_unsupported(m), "", m)

    def test_remind_me_to_do_external_not_refused(self):
        # 誤拒最傷信任：「提醒我去打電話/訂位/寄信」＝使用者自己動手、bot 只要傳訊息提醒＝做得到
        for m in ("8點提醒我要打電話給媽媽", "7點提醒我訂餐廳", "明天早上提醒我寄信給客戶", "8點叫我打電話給媽媽",
                  "8點跟我說一聲，不然老闆又要打電話來罵我", "10分鐘後通知我一下，我在等媽媽打給我",
                  "8點記得提醒我打電話給媽媽"):
            self.assertEqual(selfstate.promise_unsupported(m), "", m)

    def test_timed_condition_content_not_refused(self):
        # 審查 confirmed HIGH：帶可解析鐘點＝時鐘是觸發、條件名詞只是提醒內容 → 不拒（做得到）
        for m in ("等一下我去洗澡怕睡著，40分鐘後叫我", "早上9點的時候提醒我看一下股價",
                  "8點的時候提醒我看天氣", "明天6點提醒我，下班的時候要去拿包裹", "3點50的時候提醒我去接小孩下課"):
            self.assertEqual(selfstate.promise_unsupported(m), "", m)

    def test_daily_wins_over_incidental_weekly(self):
        # 審查 confirmed HIGH：「每天7點叫我，我每週一三五要早八」＝要的就是每天——脈絡裡的每週不得誤拒
        m = "每天早上7點叫我起床，我每週一三五要早八"
        self.assertEqual(selfstate.promise_unsupported(m), "")
        self.assertTrue(selfstate.is_daily_recur_request(m))

    def test_tiantian_substring_not_daily(self):
        # 審查 confirmed HIGH：今天天氣/這幾天天氣 的「天天」跨詞界子串不得標成永久每天
        for m in ("明早7點跟我說今天天氣怎樣", "明天早上8點叫我起床，聽說這幾天天氣會變冷", "7點提醒我帶傘，今天天氣不太好"):
            self.assertFalse(selfstate.is_daily_recur_request(m), m)

    def test_bare_jide_not_exempt(self):
        # 審查 confirmed：裸「記得」不豁免——「記得幫我打給媽媽」仍是要 bot 動手＝拒
        self.assertEqual(selfstate.promise_unsupported("記得幫我打給媽媽"), "external_action")

    def test_new_external_kinds(self):
        # 審查 confirmed：外送/第三人訊息/報感知不到的資訊
        self.assertEqual(selfstate.promise_unsupported("7點幫我叫外送"), "external_action")
        self.assertEqual(selfstate.promise_unsupported("8點傳訊息給我媽說我晚點回"), "external_action")
        self.assertEqual(selfstate.promise_unsupported("明早7點跟我說今天天氣怎樣"), "external_condition")
        self.assertEqual(selfstate.promise_unsupported("8點提醒我看天氣"), "")
        self.assertEqual(selfstate.promise_unsupported("每個禮拜一9點提醒我倒垃圾"), "recur_unsupported")

    def test_cant_hint_narratives_skipped(self):
        # 審查 confirmed：純敘述/請教不掛拒絕守則（須有請求框且非回顧語氣）
        cfg = SimpleNamespace(promise_capability_gate_enabled=True)
        st = SimpleNamespace()
        for m in ("我今天打電話給媽媽了，聊了很久", "老闆昨天叫我訂餐廳結果訂錯"):
            self.assertEqual("", monitor._promise_cant_hint(st, cfg, m), m)
        self.assertIn("做不到", monitor._promise_cant_hint(st, cfg, "你可以每小時提醒我喝水嗎"))

    def test_refusal_templates_state_reason_and_alternative(self):
        from telegram_monitor import persona
        for kind in ("external_action", "recur_unsupported", "external_condition"):
            r = persona.PROMISE_CANT_REFUSALS[kind]
            self.assertIn("做不到", r)                        # 說明原因（能力邊界）
            self.assertIn("不能答應", r)                      # 原則一：不答應
            self.assertTrue(("改成" in r) or ("要不要" in r))  # 給做得到的替代

    def test_cant_hint_flag_and_content(self):
        cfg_on = SimpleNamespace(promise_capability_gate_enabled=True)
        out = monitor._promise_cant_hint(SimpleNamespace(), cfg_on, "如果下雨的話提醒我帶傘")
        self.assertIn("做不到就不能答應", out)
        self.assertIn("替代", out)
        self.assertEqual("", monitor._promise_cant_hint(SimpleNamespace(), cfg_on, "8點提醒我開會"))
        cfg_off = SimpleNamespace(promise_capability_gate_enabled=False)
        self.assertEqual("", monitor._promise_cant_hint(SimpleNamespace(), cfg_off, "如果下雨的話提醒我帶傘"))


class DailyRecurTest(unittest.TestCase):
    """🤝 §0.64 原則二/三：「每天早上8點叫我」＝每天真的發（不再單次半守約）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _state(self, target_ts, recur="daily"):
        s = State(os.path.join(self.tmp, "s.json"))
        s.owner_folder_id = "F"
        p = {"target_ts": target_ts, "action": "每天早上8點叫我起床", "behavior": "叫他起床",
             "made_ts": target_ts - 3600, "made_text": "每天早上8點叫我起床", "fulfilled": False, "status": "pending"}
        if recur:
            p["recur"] = recur
        s.scheduled_promises = [p]
        return s

    def test_detect_daily(self):
        self.assertTrue(selfstate.is_daily_recur_request("每天早上8點叫我起床"))
        self.assertTrue(selfstate.is_scheduled_promise_request("每天早上8點叫我起床"))   # 仍走排程路由

    def test_fire_then_rearm_tomorrow(self):
        s = self._state(NOW.timestamp() - 10)
        c = FakeClient()
        monitor._promise_emit(c, s, _sched_cfg(), _coach(), NOW)
        self.assertTrue(c.sent)                                     # 到點真的發
        p = s.scheduled_promises[0]
        self.assertFalse(p.get("fulfilled"))                        # 不標 fulfilled＝約定不死
        self.assertEqual(p["status"], "pending")
        self.assertAlmostEqual(p["target_ts"], NOW.timestamp() - 10 + 86400, delta=1)   # 排到明天同時刻
        n = len(c.sent)
        monitor._promise_emit(c, s, _sched_cfg(), _coach(), NOW)    # 立刻再掃：目標已在未來 → 不重發
        self.assertEqual(len(c.sent), n)

    def test_missed_days_skip_but_today_fires_late(self):
        # 審查 confirmed：錯過的**舊日**不補翻舊帳，但推進後**今天那次仍在 TTL 內**＝還來得及 →
        # 下一拍由逾期補發路徑帶遲到致歉兌現（不是無聲跳過今天、少做一天）
        s = self._state(NOW.timestamp() - 2 * 86400 - 3600)         # 兩天又 1 小時前起算的每天約定
        c = FakeClient()
        monitor._promise_emit(c, s, _sched_cfg(), _coach(), NOW)
        self.assertEqual(c.sent, [])                                # 這拍：只推進、不補發舊日
        p = s.scheduled_promises[0]
        self.assertFalse(p.get("fulfilled"))
        self.assertAlmostEqual(p["target_ts"], NOW.timestamp() - 3600, delta=1)   # 停在今天那次（TTL 內）
        monitor._promise_emit(c, s, _sched_cfg(), _coach(), NOW)    # 下一拍：今天那次補發（遲到）
        self.assertTrue(c.sent)
        self.assertGreater(s.scheduled_promises[0]["target_ts"], NOW.timestamp())  # 發完排明天

    def test_long_gone_days_all_beyond_ttl_rearm_future(self):
        # 全部舊日都超過 TTL（例如整週停機、最近一次也在 7 小時前）→ 無聲推進到未來、不補發
        s = self._state(NOW.timestamp() - 86400 - 7 * 3600)         # 昨天+7h 前（今天那次也超過 6h TTL）
        c = FakeClient()
        monitor._promise_emit(c, s, _sched_cfg(), _coach(), NOW)
        self.assertEqual(c.sent, [])
        self.assertGreater(s.scheduled_promises[0]["target_ts"], NOW.timestamp())

    def test_single_shot_unchanged(self):
        # 無 recur 的單次承諾照舊：發完 fulfilled、逾時 expired
        s = self._state(NOW.timestamp() - 10, recur="")
        c = FakeClient()
        monitor._promise_emit(c, s, _sched_cfg(), _coach(), NOW)
        self.assertTrue(s.scheduled_promises[0]["fulfilled"])

    def test_recur_flag_off_single_shot(self):
        s = self._state(NOW.timestamp() - 10)
        cfg = _sched_cfg()
        cfg.sched_recur_daily_enabled = False
        c = FakeClient()
        monitor._promise_emit(c, s, cfg, _coach(), NOW)
        self.assertTrue(s.scheduled_promises[0]["fulfilled"])       # 旗標關＝回單次＝同現狀


class SchedPersistBehaviorTest(unittest.TestCase):
    """🤝 剪枝新基準（fulfilled_ts 退 made_ts）＋ behavior/status/fulfilled_ts round-trip 不丟。"""

    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "s.json")

    def test_recently_fulfilled_old_promise_not_pruned(self):
        # 很久前約（made_ts old）、剛兌現（fulfilled_ts 近期）→ 不被剪（修誤剪）
        import time as _t
        from telegram_monitor.state import SCHED_PROMISE_KEEP_SEC
        s = State(self.path)
        old = _t.time() - SCHED_PROMISE_KEEP_SEC - 100
        s.scheduled_promises = [{
            "target_ts": old, "action": "a", "made_ts": old, "made_text": "跟我道歉",
            "fulfilled": True, "status": "fulfilled", "fulfilled_ts": _t.time(), "behavior": "向他道歉"}]
        s.save()
        kept = State.load(self.path).scheduled_promises
        self.assertEqual(len(kept), 1)

    def test_roundtrip_keeps_new_fields(self):
        s = State(self.path)
        s.scheduled_promises = [{
            "target_ts": NOW.timestamp() + 100, "action": "a", "made_ts": NOW.timestamp(),
            "made_text": "問候我", "fulfilled": False, "status": "pending", "behavior": "問候他"}]
        s.save()
        loaded = State.load(self.path).scheduled_promises
        self.assertEqual(loaded[0]["status"], "pending")
        self.assertEqual(loaded[0]["behavior"], "問候他")


# ── §0.61 承諾捕捉層通盤修正（截圖：「可以 30 分鐘叫我起床嗎」沒被記下、bot 空口答應、8:21 沒叫、事後說謊） ──
class ElidedRelativeTimeTest(unittest.TestCase):
    """口語省略「後」＋空白拆散時距 → 偵測與 epoch 解析都要收得住。"""

    def test_screenshot_sentence_detected_and_parsed(self):
        t = "我在去補眠一下，可以 30 分鐘叫我起床嗎"
        self.assertTrue(selfstate.is_scheduled_promise_request(t))
        eps = temporal.all_clock_epochs(t, NOW, TZ)
        self.assertEqual(len(eps), 1)
        self.assertAlmostEqual(eps[0] - NOW.timestamp(), 30 * 60, delta=1)   # 正好 +30 分
        self.assertEqual(selfstate.extract_promise_behavior(t), "叫他起床")

    def test_variants_detected(self):
        for t in ("可以30分鐘叫我起床嗎", "可以 30 分鐘後叫我起床嗎", "半小時叫我起床", "十分鐘後提醒我"):
            self.assertTrue(selfstate.is_scheduled_promise_request(t), t)
            self.assertEqual(len(temporal.all_clock_epochs(t, NOW, TZ)), 1, t)

    def test_duration_action_across_comma(self):
        # §0.62（截圖「我睡 30 分鐘，等一下叫我起來大聲叫」永遠沒被記下）：時距與叫醒動作跨一個逗號（分屬兩子句）
        # ＝極常見的睡覺-叫醒講法——逗號是句內軟停頓、不是句界，須收得住並解析出 +N 分。
        full = "你一直都沒有叫我起床，我氣到頭又暈了，再去補眠一下，我睡 30 分鐘，等一下叫我起來大聲叫"
        self.assertTrue(selfstate.is_scheduled_promise_request(full))
        eps = temporal.all_clock_epochs(full, NOW, TZ)
        self.assertEqual(len(eps), 1)
        self.assertAlmostEqual(eps[0] - NOW.timestamp(), 30 * 60, delta=1)
        for t in ("我睡 30 分鐘，等一下叫我起來大聲叫", "我瞇20分鐘，記得叫我起床", "我睡半小時，等等叫我"):
            self.assertTrue(selfstate.is_scheduled_promise_request(t), t)
            self.assertEqual(len(temporal.all_clock_epochs(t, NOW, TZ)), 1, t)

    def test_comma_not_a_sentence_boundary_bypass(self):
        # 逗號放寬**不得**變成「跨到另一句去縫動作」——真的是另一件事的子句不亂抓
        for t in ("我睡30分鐘，你先去忙，等等再說", "剛跑了30分鐘，好累，等下洗澡"):
            self.assertFalse(selfstate.is_scheduled_promise_request(t), t)

    def test_third_party_hui_clause_not_phantom(self):
        # §0.62 審查 confirmed HIGH：逗號後「X會V我」是第三方陳述句（室友/媽/護士會叫我），非對 bot 的祈使
        # → 不得變幽靈承諾（bot 到 +N 分亂發）。偵測與解析都須拒。
        for t in ("我冥想20分鐘，然後我媽會叫我吃飯", "我睡30分鐘，我室友會叫我",
                  "我慢跑20分鐘，等下朋友會通知我", "醫生說等15分鐘，等等護士會叫我", "我睡30分鐘，我媽會叫我"):
            self.assertFalse(selfstate.is_scheduled_promise_request(t), t)
            self.assertEqual(temporal.all_clock_epochs(t, NOW, TZ), [], t)

    def test_time_filler_hui_still_ok(self):
        # 但**時間填充詞**裡的「會」（等會/待會＝等一下）是合法祈使，不可被上面的排「會」誤殺
        for t in ("我睡30分鐘，等會叫我", "我睡30分鐘，待會叫我", "我瞇20分鐘，等一會兒叫我"):
            self.assertTrue(selfstate.is_scheduled_promise_request(t), t)
            self.assertEqual(len(temporal.all_clock_epochs(t, NOW, TZ)), 1, t)

    def test_spaced_absolute_clock_parses(self):
        # 同類殘洞：「8 點」被空白→頓號拆成「8、點」＝解析 0 筆（偵測過了、承諾卻沒記下）
        self.assertEqual(len(temporal.all_clock_epochs("8 點提醒我", NOW, TZ)), 1)
        self.assertEqual(len(temporal.all_clock_epochs("明天 8 點叫我", NOW, TZ)), 1)

    def test_multi_time_list_regression(self):
        # SCHED_TIME_SPACE_FIX 的本命場景（空白分隔多時刻）不能被黏回動作破壞
        self.assertEqual(len(temporal.all_clock_epochs("21:20 21:30 都問候我", NOW, TZ)), 2)

    def test_negatives_not_captured(self):
        # 省略「後」形須動作緊隨——敘述句/完成句不可誤收成時間承諾
        for t in ("我睡了30分鐘", "再過五分鐘就到了", "我昨天走了30分鐘的路", "十分鐘可以跑完嗎"):
            self.assertFalse(selfstate.is_scheduled_promise_request(t), t)
            self.assertEqual(temporal.all_clock_epochs(t, NOW, TZ), [], t)

    def test_past_narrative_phantoms_not_captured(self):
        # 審查 confirmed HIGH：完成/慣常敘述（等了/花了/每）＋敘事連接詞（才/就）不得變幽靈承諾
        for t in ("他等了十分鐘才回我", "他拖了兩小時才回我訊息", "他花了兩小時跟我說他的煩惱",
                  "客服讓我等了三十分鐘才回覆我", "護理師每半小時提醒我一次", "醫生看了十分鐘就叫我回家",
                  "他遲到了十分鐘跟我道歉", "你剛剛不是十分鐘就提醒我了嗎", "你不是說30分鐘叫我嗎"):
            self.assertFalse(selfstate.is_scheduled_promise_request(t), t)

    def test_newline_not_sewn_across(self):
        # 審查 confirmed LOW：連發合併多行——縫隙不得跨行縫合（含「0分鐘」子串繞過 lookbehind 的修法驗證）
        self.assertFalse(selfstate.is_scheduled_promise_request("我等了30分鐘\n你叫我幹嘛"))
        self.assertEqual(temporal.all_clock_epochs("我睡了30分鐘\n幫我叫杯咖啡", NOW, TZ), [])
        self.assertFalse(selfstate.is_promise_ledger_question("為什麼你沒回\n叫我幹嘛"))

    def test_spaced_clock_values_correct(self):
        # 審查 confirmed MED：黏回也要保住 明天/下午/半——不只解析得出、時刻還要**對**
        from datetime import datetime as _dt, timedelta, timezone as _tzmod
        local = NOW.astimezone(TZ)
        eps = temporal.all_clock_epochs("明天 8 點叫我", NOW, TZ)
        exp = (local.replace(hour=8, minute=0, second=0, microsecond=0) + timedelta(days=1)).timestamp()
        self.assertEqual(len(eps), 1)
        self.assertAlmostEqual(eps[0], exp, delta=1)                       # 明天早上 8 點、不是今天 20:00
        eps = temporal.all_clock_epochs("下午 3 點跟我說一聲", NOW, TZ)
        c = local.replace(hour=15, minute=0, second=0, microsecond=0)
        exp = (c if c > local else c + timedelta(days=1)).timestamp()
        self.assertAlmostEqual(eps[0], exp, delta=1)                       # 下一個 15:00、不是凌晨 3:00
        eps = temporal.all_clock_epochs("三 點 半提醒我", NOW, TZ)
        got = _dt.fromtimestamp(eps[0], _tzmod.utc).astimezone(TZ)
        self.assertEqual(got.minute, 30)                                    # 3:30、不是 3:00


class WhyNotLedgerTest(unittest.TestCase):
    """失約質問「你為什麼沒有主動叫我」→ 收進承諾帳本路由（據帳本誠實對帳），不再落一般聊天讓 LLM 說謊。"""

    def test_whynot_forms_hit_ledger(self):
        for t in ("你為什麼沒有主動叫我", "怎麼沒提醒我", "你為何沒通知我", "為什麼過了30分鐘都沒叫我",
                  "我睡過頭了，你為什麼沒叫我起床", "你怎麼沒叫醒我", "你怎麼沒提醒我吃藥"):
            self.assertTrue(selfstate.is_promise_ledger_question(t), t)

    def test_whynot_third_party_not_hijacked(self):
        # 審查 confirmed MED：第三方/裝置抱怨不得被劫走進承諾帳本路由
        for t in ("為什麼手機沒提醒我", "為什麼鬧鐘沒叫我", "媽媽怎麼沒叫我起床", "為什麼老闆沒通知我開會",
                  "怎麼沒人通知我", "他為什麼沒叫我", "為什麼你沒喊他", "你為什麼沒有叫外賣", "為什麼系統沒有通知你"):
            self.assertFalse(selfstate.is_promise_ledger_question(t), t)

    def test_whynot_with_rerequest_yields_to_scheduled(self):
        # 審查 confirmed HIGH：失約質問同句帶「N分鐘後」重新請求 → 讓給 scheduled 收（新約要入帳兌現）
        t = "你怎麼沒叫我！10分鐘後再叫我一次"
        self.assertFalse(selfstate.is_promise_ledger_question(t))
        self.assertTrue(selfstate.is_scheduled_promise_request(t))
        self.assertEqual(len(temporal.all_clock_epochs(t, NOW, TZ)), 1)

    def test_unrelated_whynot_not_hit(self):
        for t in ("為什麼天空是藍的", "你為什麼沒睡", "為什麼今天沒有下雨"):
            self.assertFalse(selfstate.is_promise_ledger_question(t), t)


class PromiseAckGuardTest(unittest.TestCase):
    """空口答應守門：計時請求走到一般聊天路徑（＝捕捉沒成立）→ 掛守則別答應「到時候我會叫你」。"""

    def test_timed_request_detector(self):
        for t in ("鬧鐘時間到叫醒我", "待會叫我起床", "5分鐘叫我"):
            self.assertTrue(selfstate.looks_like_timed_request(t), t)
        for t in ("你為什麼沒有主動叫我", "剛剛有提醒我嗎", "今天天氣真好", "嗯",
                  "老闆叫我改了3份報告", "醫生叫我量2次血壓"):   # 審查 confirmed：裸數字敘述不算時間味
            self.assertFalse(selfstate.looks_like_timed_request(t), t)

    def test_guard_injected_for_uncaptured_timed_request(self):
        cfg = SimpleNamespace(promise_ack_guard_enabled=True, scheduled_promise_enabled=True)
        out = monitor._promise_guard_hint(SimpleNamespace(), cfg, "鬧鐘時間到叫醒我")
        self.assertIn("空頭承諾", out)                    # 守則在場：別空口答應
        self.assertIn("再講一次", out)                    # 引導對方換明確說法

    def test_guard_flag_off_or_plain_chat_empty(self):
        st = SimpleNamespace()
        off = SimpleNamespace(promise_ack_guard_enabled=False, scheduled_promise_enabled=True)
        self.assertEqual("", monitor._promise_guard_hint(st, off, "鬧鐘時間到叫醒我"))
        sched_off = SimpleNamespace(promise_ack_guard_enabled=True, scheduled_promise_enabled=False)
        self.assertEqual("", monitor._promise_guard_hint(st, sched_off, "鬧鐘時間到叫醒我"))
        on = SimpleNamespace(promise_ack_guard_enabled=True, scheduled_promise_enabled=True)
        self.assertEqual("", monitor._promise_guard_hint(st, on, "今天天氣真好"))


if __name__ == "__main__":
    unittest.main()
