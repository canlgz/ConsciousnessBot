"""⏱ 對話時間軸：convo_history 帶時間戳，餵 LLM 時標相對時間 → 答得出「多久前/我睡多久」。"""

import time
import unittest
from types import SimpleNamespace
from unittest import mock

from telegram_monitor import coach as coachmod, monitor
from telegram_monitor.state import State


class RelTimeTest(unittest.TestCase):
    def test_buckets(self):
        self.assertEqual(coachmod._rel_time(10), "剛剛")
        self.assertEqual(coachmod._rel_time(600), "10分前")
        self.assertEqual(coachmod._rel_time(8 * 3600), "約8小時前")
        self.assertEqual(coachmod._rel_time(30 * 3600), "昨天")
        self.assertEqual(coachmod._rel_time(3 * 86400), "3天前")


class RememberTimestampTest(unittest.TestCase):
    def test_remember_stamps_time(self):
        s = State("/tmp/_convo_ts.json")
        monitor._remember(s, "user", "晚了，我要去睡覺了")
        self.assertIn("ts", s.convo_history[-1])
        self.assertAlmostEqual(s.convo_history[-1]["ts"], time.time(), delta=5)
        monitor._remember(s, "user", "舊訊息", ts=123.0)        # 可注入（測試/精確時間）
        self.assertEqual(s.convo_history[-1]["ts"], 123.0)


class HistoryContentsTimeTest(unittest.TestCase):
    def _coach(self):
        return coachmod.Coach(SimpleNamespace(gemini_api_key="k", gemini_model="m"))

    def test_turns_get_relative_time_tags(self):
        now = 1_700_000_000
        history = [
            {"role": "user", "text": "晚了，我要去睡覺了", "ts": now - 8 * 3600},
            {"role": "model", "text": "好喔，晚安囉", "ts": now - 8 * 3600},
            {"role": "user", "text": "我睡多久了？", "ts": now - 10},
        ]
        contents = self._coach()._history_contents("brief", history, now_ts=now)
        joined = "\n".join(p["text"] for c in contents for p in c["parts"])
        self.assertIn("〔約8小時前〕晚了，我要去睡覺了", joined)   # LLM 看得到「8 小時前說要睡」→ 能答睡多久
        self.assertIn("〔剛剛〕我睡多久了", joined)

    def test_no_now_ts_means_no_tags(self):
        history = [{"role": "user", "text": "嗨", "ts": 123.0}]
        contents = self._coach()._history_contents("brief", history, now_ts=None)
        joined = "\n".join(p["text"] for c in contents for p in c["parts"])
        self.assertIn("嗨", joined)
        self.assertNotIn("〔", joined)

    def test_missing_or_bad_legacy_timestamp_does_not_break_history(self):
        history = [{"role": "model", "text": "舊句", "ts": "bad"},
                   {"role": "user", "text": "沒有時間", "ts": None}]
        contents = self._coach()._history_contents("brief", history, now_ts=1_700_000_000)
        joined = "\n".join(p["text"] for c in contents for p in c["parts"])
        self.assertIn("舊句", joined)
        self.assertIn("沒有時間", joined)

    def test_omitted_now_uses_real_clock_for_special_voice_paths(self):
        now = 1_700_000_000
        history = [{"role": "model", "text": "昨天的我說今天", "ts": now - 30 * 3600}]
        with mock.patch.object(coachmod.time, "time", return_value=now):
            contents = self._coach()._history_contents("brief", history)
        joined = "\n".join(p["text"] for c in contents for p in c["parts"])
        self.assertIn("〔昨天〕昨天的我說今天", joined)


class TagLeakTest(unittest.TestCase):
    def test_clean_voice_strips_leaked_time_tag(self):
        from telegram_monitor import coach as coachmod
        self.assertEqual(coachmod._clean_voice("〔剛剛〕怕啊，怎麼會不怕。"), "怕啊，怎麼會不怕。")
        self.assertEqual(coachmod._clean_voice("〔約8小時前〕嗨"), "嗨")
        self.assertEqual(coachmod._clean_voice("正常的話不動"), "正常的話不動")

    def test_history_tags_both_speakers(self):
        from telegram_monitor import coach as coachmod
        now = 1_700_000_000
        c = coachmod.Coach(SimpleNamespace(gemini_api_key="k", gemini_model="m"))
        hist = [{"role": "user", "text": "我要去睡了", "ts": now - 3600},
                {"role": "model", "text": "晚安囉", "ts": now - 3600}]
        joined = "\n".join(p["text"] for cc in c._history_contents("b", hist, now_ts=now) for p in cc["parts"])
        self.assertIn("〔約1小時前〕我要去睡了", joined)    # 使用者訊息有標
        self.assertIn("〔約1小時前〕晚安囉", joined)        # bot 舊話也有時錨，不能隔天被讀成「剛剛」

    def test_yesterday_model_today_word_keeps_yesterday_anchor(self):
        now = 1_700_000_000
        hist = [{"role": "model", "text": "嗯，我剛剛也讀誦完今天的進度。", "ts": now - 30 * 3600}]
        c = coachmod.Coach(SimpleNamespace(gemini_api_key="k", gemini_model="m"))
        joined = "\n".join(p["text"] for cc in c._history_contents("b", hist, now_ts=now)
                           for p in cc["parts"])
        self.assertIn("〔昨天〕嗯，我剛剛也讀誦完今天的進度。", joined)


class ConvoClockQuestionTest(unittest.TestCase):
    """⏱ 截圖根因：問「對話的時間點／幾點幾分／你有記下時間嗎」漏到 function-calling 被誤抓 records_in_time_range
    回 📂「那段你沒有記寫」（把對話時間當記寫資料查）。改：偵測絕對鐘點問句＋用 convo_history ts 報本地鐘點。"""

    def test_detects_absolute_conversation_time(self):
        from telegram_monitor import selfstate as ss
        for q in ["你有記下時間點嗎？告訴我時間", "對話的時間點", "沒有具體的幾點幾分嗎",
                  "剛剛那幾句是幾點說的", "我們對話的時間點是什麼"]:
            self.assertTrue(ss.is_convo_clock_question(q), q)

    def test_not_confused_with_clock_records_or_relative(self):
        from telegram_monitor import selfstate as ss
        self.assertFalse(ss.is_convo_clock_question("現在幾點"))          # clock（現在）
        self.assertFalse(ss.is_convo_clock_question("現在幾點幾分"))      # clock（含現在）
        self.assertFalse(ss.is_convo_clock_question("我記寫的具體時間"))   # 記寫資料 → records
        self.assertFalse(ss.is_convo_clock_question("我多久沒聊天"))      # 相對 convo_time
        self.assertFalse(ss.is_convo_clock_question("幫我看今天寫了什麼"))

    def test_routes_to_convo_time(self):
        from telegram_monitor import intent, referent
        ref = referent.resolve(SimpleNamespace(), 0)
        self.assertEqual(intent.resolve("對話的時間點", ref, cfg=SimpleNamespace(convo_clock_enabled=True)).kind,
                         "convo_time")
        # 關旗標 → 不搶（落回 fact_or_chat，同現狀）
        self.assertEqual(intent.resolve("對話的時間點", ref, cfg=SimpleNamespace(convo_clock_enabled=False)).kind,
                         "fact_or_chat")

    def test_clock_fact_reports_local_hhmm(self):
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Asia/Taipei")
        import datetime as dt
        now = dt.datetime(2026, 6, 27, 17, 23, tzinfo=tz).timestamp()
        hist = [{"role": "user", "text": "你失智了嗎", "ts": now - 14 * 60},
                {"role": "user", "text": "對話的時間點", "ts": now - 60}]
        fact = monitor._convo_clock_fact(hist, tz, now)
        self.assertIn("17:09", fact)                 # 14 分前那句的本地鐘點
        self.assertIn("（14分前）", fact)              # 與對話裡的相對標籤對齊
        self.assertEqual(monitor._convo_clock_fact([], tz, now), "")   # 無歷史→空
        self.assertEqual(monitor._convo_clock_fact(hist, None, now), "")  # 無 tz→空（誠實 unknown）

    def test_clock_fact_covers_far_back_not_just_recent(self):
        # 截圖根因：沮喪那幾句在「約20分前」，之後又有十幾輪 → 舊的 6-則窗會漏掉它們，bot 只能答相對、答不出鐘點。
        # 修：覆蓋整段保留歷史，遠一點的訊息也有絕對鐘點。
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Asia/Taipei")
        import datetime as dt
        now = dt.datetime(2026, 6, 27, 17, 43, tzinfo=tz).timestamp()
        hist = [{"role": "user", "text": "你失智了嗎", "ts": now - 20 * 60}]   # 20 分前那句沮喪
        for i in range(16):                                                    # 之後十幾輪較近的雜訊
            hist.append({"role": "user" if i % 2 == 0 else "model", "text": f"雜訊{i}", "ts": now - 8 * 60 + i * 20})
        fact = monitor._convo_clock_fact(hist, tz, now)
        self.assertIn("17:23", fact)                 # 20 分前那句的鐘點仍被算出（不只最近幾則）
        self.assertIn("（20分前）", fact)


class ConnectHintTest(unittest.TestCase):
    def test_recent_conversation_gives_connect_hint(self):
        now = 1_700_000_000
        state = SimpleNamespace(last_user_msg_ts=now - 60,
                                convo_history=[{"role": "user", "text": "你不怕自己的表現嗎", "ts": now - 60}])
        h = monitor._connect_hint(state, now)
        self.assertIn("承接", h)
        self.assertIn("你不怕自己的表現嗎", h)

    def test_old_conversation_no_connect(self):
        now = 1_700_000_000
        state = SimpleNamespace(last_user_msg_ts=now - 3 * 3600,   # 3 小時前 → 不必硬銜接
                                convo_history=[{"role": "user", "text": "早", "ts": now - 3 * 3600}])
        self.assertEqual(monitor._connect_hint(state, now), "")

    def test_interactive_uses_current_not_stale_previous(self):
        # 截圖根因：互動時這輪訊息還沒進 convo_history（回完才 _remember）→ 不傳 current 會抓到**上一輪**、
        # 承接錯對象（上輪誇獎→這輪問感受，卻又開頭「謝謝你這麼說」）。互動路徑要傳 current＝這輪實際說的話。
        now = 1_700_000_000
        state = SimpleNamespace(
            last_user_msg_ts=now,                       # dispatch 前已刷成 now（互動時窗永遠開）
            convo_history=[{"role": "user", "text": "你他媽的太厲害的回答", "ts": now - 180},
                           {"role": "model", "text": "謝謝你這麼說！", "ts": now - 175}])
        h = monitor._connect_hint(state, now, current="這段時間你有什麼特別感受嗎")
        self.assertIn("這段時間你有什麼特別感受嗎", h)     # 承接這輪實際問的話
        self.assertNotIn("你他媽的太厲害的回答", h)         # 不再承接上一輪誇獎 → 不會又「謝謝你這麼說」
        # 對照：主動推播不傳 current → 仍退回對話史最後一句（continuity 不變）
        self.assertIn("你他媽的太厲害的回答", monitor._connect_hint(state, now))


class ClockQuestionTest(unittest.TestCase):
    def test_detector(self):
        from telegram_monitor import selfstate, datatools
        for q in ["現在幾點", "今天幾號", "今天星期幾", "我多久沒寫", "最後一次記寫是什麼時候",
                  "你現在是什麼時間", "現在什麼時間", "現在的時間"]:   # 🕐 補「什麼時間」說法（截圖：被當主觀問句漏接）
            self.assertTrue(selfstate.is_clock_question(q), q)
        # 含時間詞的閒聊／別種時間問題 → 不是鐘錶問句（不該回時鐘）
        for q in ["對啊，一整天都在盯著這個意識 bot", "才兩分鐘嗎", "我睡多久了",
                  "那筆是什麼時間寫的", "你什麼時間方便"]:   # 問某筆記寫的時間 ≠ 問現在 → 不該回時鐘
            self.assertFalse(selfstate.is_clock_question(q), q)
        # get_current_time 已不在 LLM 工具表（改 fast-path）
        self.assertNotIn("get_current_time", [d["name"] for d in datatools.TOOL_DECLS])


class ConvoTimeRoutingTest(unittest.TestCase):
    def test_detector(self):
        from telegram_monitor import selfstate
        for q in ["我睡多久了", "那你知道我睡多久了", "我們多久沒聊", "距離上次說話多久", "我們聊多久了"]:
            self.assertTrue(selfstate.is_convo_time_question(q), q)
        # 🦜 冷落/不搭理的相隔問法（與「多久沒聊」同義）：截圖 bug「我有多久沒有理你了」被當成查數據
        for q in ["我有多久沒有理你了？", "多久沒理你", "多久沒理我", "多久沒搭理你", "我多久沒鳥你",
                  "幾天沒理你了", "我多久沒聯絡你"]:
            self.assertTrue(selfstate.is_convo_time_question(q), q)
        # 🗣️ 說話/聊/見面的相隔問法，連接詞跟/與/和/同都要接得住（截圖：「多久沒與你說話」漏接被吐 📊）
        for q in ["多久沒跟你說話", "多久沒與你說話", "多久沒和你說話", "多久沒同你說話",
                  "所以多久沒與你說話了", "多久沒跟你好好聊聊", "多久沒見面", "多久沒互動"]:
            self.assertTrue(selfstate.is_convo_time_question(q), q)
        for q in ["我多久沒寫", "現在幾點", "你現在怎樣",        # 「沒寫」是記寫時間、不是對話時間
                  "我多久沒整理筆記", "多久沒運動", "多久沒陪家人", "多久沒交報告"]:   # 帶「理/陪」但對象不是你/我 → 不誤收
            self.assertFalse(selfstate.is_convo_time_question(q), q)

    def test_session_gap_text(self):
        now = 1_700_000_000
        # 上次活動在 8h 前，現在回來問 → 含「現在」算出 8 小時的沉默
        hist = [{"role": "user", "text": "晚安", "ts": now - 8 * 3600}]
        self.assertIn("8 小時", monitor._session_gap_text(hist, now))
        # 一直在聊（都在近期）→ 沒有大間隔 → ''
        self.assertEqual(monitor._session_gap_text([{"role": "user", "text": "嗨", "ts": now - 30}], now), "")

    def test_convo_time_question_routes_to_chat_not_clock(self):
        # 「我睡多久了」要走純對話（coach.reply），不被 get_current_time 搶走 → reply 被呼叫、回答含算好的間隔。
        s = State("/tmp/_ct_route.json")
        s.owner_folder_id = "F"
        now = 1_700_000_000
        s.convo_history = [{"role": "user", "text": "我要去睡了", "ts": now - 8 * 3600},
                           {"role": "model", "text": "晚安", "ts": now - 8 * 3600}]
        called = {}

        def fake_reply(q, brief, hist, mood_hint="", now_ts=None):
            called["brief"] = brief
            return "你大概睡了 8 小時喔。"

        coach = SimpleNamespace(enabled=True, meter=SimpleNamespace(record=lambda *a, **k: None), reply=fake_reply)
        client = SimpleNamespace(sent=[], dry_run=False, send=lambda t: client.sent.append(t) or True)
        update = {"message": {"chat": {"id": 1}, "text": "我睡多久了", "date": now}}
        from unittest import mock
        # 走 function-calling 才會碰 ToolCtx/工具；這裡 reply 被呼叫＝確認走了純對話路徑。
        # 對話時間軸的「現在」改用牆鐘（time.time()，與 _remember 記點同源）→ 釘住牆鐘＝測試的固定 now，
        # 間隔才量得到 8 小時（不會拿 message.date 去減牆鐘記下的歷史點）。
        with mock.patch("telegram_monitor.coach.build_memory_brief", return_value="（brief）"), \
             mock.patch("telegram_monitor.monitor.time.time", return_value=now):
            monitor.handle_message(update, coach, None, {"meta": {}, "records": []}, object(),
                                   s, client, SimpleNamespace(dry_run=False, telegram_chat_id=""), None)
        self.assertIn("8 小時", "".join(client.sent))
        self.assertIn("8 小時", called["brief"])              # 算好的間隔事實有餵進去

    def test_timeline_measured_in_wall_clock_not_message_date(self):
        # 單一基準收口：對話時間軸記點（牆鐘）與量距（牆鐘）同源。message.date 與牆鐘大幅不一致
        # （離線補送舊訊息）時，「多久沒聊」仍用牆鐘量、不被 message.date 帶歪。
        wall_now = 1_700_000_000
        s = State("/tmp/_ct_wall.json")
        s.owner_folder_id = "F"
        s.convo_history = [{"role": "model", "text": "晚安", "ts": wall_now - 2 * 3600}]   # 牆鐘記下：2 小時前
        cap = {}

        def fake_reply(q, brief, hist, mood_hint="", now_ts=None):
            cap["brief"], cap["now_ts"] = brief, now_ts
            return "嗯，我在。"

        coach = SimpleNamespace(enabled=True, meter=SimpleNamespace(record=lambda *a, **k: None), reply=fake_reply)
        client = SimpleNamespace(sent=[], dry_run=False, send=lambda t: client.sent.append(t) or True)
        # message.date 故意比牆鐘早 100 小時（離線補送）：舊碼會拿它當「現在」去減牆鐘歷史點 → 算出離譜間隔
        update = {"message": {"chat": {"id": 1}, "text": "我睡多久了", "date": wall_now - 100 * 3600}}
        from unittest import mock
        with mock.patch("telegram_monitor.coach.build_memory_brief", return_value=""), \
             mock.patch("telegram_monitor.monitor.time.time", return_value=wall_now):
            monitor.handle_message(update, coach, None, {"meta": {}, "records": []}, object(),
                                   s, client, SimpleNamespace(dry_run=False, telegram_chat_id=""), None)
        self.assertIn("2 小時", cap["brief"])                 # 牆鐘量＝2 小時（非被 message.date 帶成 ~100h）
        self.assertNotIn("100", cap["brief"])
        self.assertEqual(cap["now_ts"], wall_now)             # 餵 coach 做相對時間標籤的也是牆鐘


class BotTurnTimeRoutingTest(unittest.TestCase):
    """🆕『你剛說的多久前』族＝問**最近一則 bot 訊息距今多久**，指涉物與『多久沒聊』不同。
    原本落一般 function-calling、被指涉 bot 訊息無相對標籤 → LLM 只能自推＝範本同型破口。"""

    def test_bot_turn_detector(self):
        from telegram_monitor import selfstate
        for q in ["你剛說的多久前", "你上一句是多久前說的", "你剛那句多久前", "你剛剛說的多久前",
                  "你上一句多久前", "你剛講的多久前"]:
            self.assertTrue(selfstate.is_bot_turn_time_question(q), q)
            self.assertTrue(selfstate.is_convo_time_question(q), q)   # 進 convo_time fast-path
        # 守住不誤收（不含『多久』的『你說什麼／你剛說錯了』）
        for q in ["你說什麼", "你剛說錯了", "你剛剛說的話"]:
            self.assertFalse(selfstate.is_bot_turn_time_question(q), q)

    def test_last_bot_turn_gap_fact(self):
        now = 1_700_000_000
        # 有 bot（model）訊息 → 回含 human_gap 的事實句（量的是最近一則 model 訊息距今）
        hist = [{"role": "user", "text": "嗨", "ts": now - 5 * 3600},
                {"role": "model", "text": "在啊", "ts": now - 2 * 3600},
                {"role": "user", "text": "你剛說的多久前", "ts": now - 5}]
        fact = monitor._last_bot_turn_gap_fact(hist, now)
        self.assertIn("2 小時", fact)
        self.assertIn("我上一句", fact)
        # 無 model 訊息 → 回 ''（→ 觸發 convo_time 路徑的 unknown 誠實句）
        self.assertEqual(monitor._last_bot_turn_gap_fact(
            [{"role": "user", "text": "嗨", "ts": now - 60}], now), "")
        self.assertEqual(monitor._last_bot_turn_gap_fact([], now), "")

    def test_bot_turn_question_uses_bot_gap_fact(self):
        # 『你剛說的多久前』→ 走純對話、brief 含『我上一句…約 2 小時』（_last_bot_turn_gap_fact），非沉默段。
        s = State("/tmp/_ct_botturn.json")
        s.owner_folder_id = "F"
        now = 1_700_000_000
        s.convo_history = [{"role": "user", "text": "嗨", "ts": now - 5 * 3600},
                           {"role": "model", "text": "在啊", "ts": now - 2 * 3600}]
        cap = {}

        def fake_reply(q, brief, hist, mood_hint="", now_ts=None):
            cap["brief"], cap["mood_hint"] = brief, mood_hint
            return "我上一句大概兩小時前說的。"

        coach = SimpleNamespace(enabled=True, meter=SimpleNamespace(record=lambda *a, **k: None), reply=fake_reply)
        client = SimpleNamespace(sent=[], dry_run=False, send=lambda t: client.sent.append(t) or True)
        update = {"message": {"chat": {"id": 1}, "text": "你剛說的多久前", "date": now}}
        from unittest import mock
        with mock.patch("telegram_monitor.coach.build_memory_brief", return_value="（brief）"), \
             mock.patch("telegram_monitor.monitor.time.time", return_value=now):
            monitor.handle_message(update, coach, None, {"meta": {}, "records": []}, object(),
                                   s, client, SimpleNamespace(dry_run=False, telegram_chat_id=""), None)
        self.assertIn("我上一句", cap["brief"])     # 用的是 bot-turn 事實，不是沉默段事實
        self.assertIn("2 小時", cap["brief"])

    def test_convo_time_unknown_when_no_groundable_reading(self):
        # 算不出相對量（無對應 bot 訊息）→ coach 路徑的 brief 含誠實 unknown 授權（不得估、不得自推）。
        s = State("/tmp/_ct_unknown.json")
        s.owner_folder_id = "F"
        now = 1_700_000_000
        s.convo_history = [{"role": "user", "text": "嗨", "ts": now - 30}]   # 只有 user、無 model
        cap = {}

        def fake_reply(q, brief, hist, mood_hint="", now_ts=None):
            cap["brief"] = brief
            return "我不太確定那是多久前耶。"

        coach = SimpleNamespace(enabled=True, meter=SimpleNamespace(record=lambda *a, **k: None), reply=fake_reply)
        client = SimpleNamespace(sent=[], dry_run=False, send=lambda t: client.sent.append(t) or True)
        update = {"message": {"chat": {"id": 1}, "text": "你剛說的多久前", "date": now}}
        from unittest import mock
        with mock.patch("telegram_monitor.coach.build_memory_brief", return_value="（brief）"), \
             mock.patch("telegram_monitor.monitor.time.time", return_value=now):
            monitor.handle_message(update, coach, None, {"meta": {}, "records": []}, object(),
                                   s, client, SimpleNamespace(dry_run=False, telegram_chat_id=""), None)
        self.assertIn("不太確定", cap["brief"])      # 誠實 unknown 授權有餵進去（不讓 LLM 自推）

    def test_no_coach_no_history_keeps_unknown_sentence(self):
        # 非 coach 路徑、無對話時間紀錄 → 仍輸出既有 unknown 句（誠實紀律續守）。
        self.assertEqual(monitor._session_gap_text([], 1_700_000_000), "")


class PersonaNoSelfCalcTest(unittest.TestCase):
    """🚫 移除『教 LLM 自算衍生事實』的 prompt 破口；加『別自己推算、照已算好的事實講』防線。"""

    def test_socratic_drops_self_calc_example(self):
        from telegram_monitor import persona
        # 嚴格綁 SOCRATIC_SYSTEM 字串（不掃模組/檔案/coach 輸出——別處 fixture 仍含『睡了 8 小時』）
        self.assertNotIn("你大概睡了 8 小時", persona.SOCRATIC_SYSTEM)
        self.assertNotIn("→「", persona.SOCRATIC_SYSTEM)

    def test_socratic_has_no_self_calc_guard(self):
        from telegram_monitor import persona
        self.assertIn("別自己", persona.SOCRATIC_SYSTEM)
        self.assertIn("已算好的事實", persona.SOCRATIC_SYSTEM)

    def test_no_self_calc_hint_exists(self):
        from telegram_monitor import persona
        self.assertIn("已經", persona.NO_SELF_CALC_HINT)
        self.assertIn("別自己", persona.NO_SELF_CALC_HINT)
        self.assertIn("已算好的事實", persona.CONVO_TIME_HINT)


if __name__ == "__main__":
    unittest.main()
