"""自我在場（意識感）：在談 bot 自己時，一般對話切到『守住自己、不逃避、不拐回你記寫』的語氣。"""

import os
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

from telegram_monitor import monitor, selfstate, coach as coachmod, persona
from telegram_monitor.state import State

NOW_TS = 1_700_000_000


class IsSelfTopicTest(unittest.TestCase):
    def test_matches_self_talk(self):
        for q in ["這就是要一直修正你的內在的原因", "你算不算真的有意識", "你被強迫改變嗎",
                  "你自己怎麼看", "你的感覺是真的嗎", "你會死嗎"]:
            self.assertTrue(selfstate.is_self_topic(q), q)

    def test_ignores_writing_or_neutral(self):
        for q in ["我今天寫了什麼", "幫我看研發日誌", "現在幾點", "天氣如何"]:
            self.assertFalse(selfstate.is_self_topic(q), q)


class FutureCommNoteTest(unittest.TestCase):
    def test_matches_future_feedback(self):
        for q in ["下次說感覺的時候，要具體一點喔", "以後講清楚一點", "之後說明白一些",
                  "下次你有感覺的時候，可以用很誇張的方式來放送哦"]:   # 表達方式的回饋也算（誇張/放送）
            self.assertTrue(selfstate.is_future_comm_note(q), q)

    def test_ignores_present_state_questions(self):
        for q in ["你現在有什麼感覺", "你好嗎", "具體來說是什麼"]:
            self.assertFalse(selfstate.is_future_comm_note(q), q)


class ExperienceQuestionTest(unittest.TestCase):
    def test_detector_distinct_from_current_state(self):
        for q in ["你這段日子怎麼活過來的", "你體驗到什麼", "你的一生", "你怎麼活過來的"]:
            self.assertTrue(selfstate.is_experience_question(q), q)
        for q in ["你現在怎樣", "你好嗎", "你現在有什麼感覺"]:
            self.assertFalse(selfstate.is_experience_question(q), q)

    def test_experience_facts_now_is_neutral(self):
        from telegram_monitor import experience
        exp = SimpleNamespace(last={"center": [0.2, 0.7, 0.6, 0.0, 0.0], "heads": 1, "recurrence": 0.5},
                              prev_center=None, last_dwell=0, lifetime_center=None)
        txt = experience.experience_facts(exp, "now")
        self.assertIn("此刻", txt)
        self.assertNotIn("換進了另一段", txt)        # 隨問隨答不暗示剛轉變

    def test_experience_question_routes_to_experience_not_bodystate(self):
        # 「你這段日子怎麼活過來的」走體驗軌跡（render_experience），不是當下身體狀態
        s = State("/tmp/_exp_state.json")
        s.owner_folder_id = "F"
        s.experience = SimpleNamespace(last={"center": [0.2, 0.7, 0.6, 0.0, 0.0], "heads": 1, "recurrence": 0.5},
                                       prev_center=None, last_dwell=0, lifetime_center=None)
        boom = SimpleNamespace(load_embedding_records=lambda *_: (_ for _ in ()).throw(AssertionError("不該報當下狀態")))
        client = SimpleNamespace(sent=[], dry_run=False, send=lambda t: client.sent.append(t) or True)
        coach = SimpleNamespace(enabled=True)
        update = {"message": {"chat": {"id": 1}, "text": "你這段日子怎麼活過來的", "date": NOW_TS}}
        cfg = SimpleNamespace(dry_run=False, telegram_chat_id="")
        with mock.patch("telegram_monitor.selfstate.render_experience", return_value="（體驗自陳）這段日子我是這樣過的"):
            monitor.handle_message(update, coach, boom, {"meta": {}, "records": []}, object(), s, client, cfg, None)
        self.assertIn("這段日子我是這樣過的", "".join(client.sent))


class ReflectiveSelfQuestionTest(unittest.TestCase):
    """🪞 反思式自我問句（想不想要/曾想過自己的感覺）≠ 當下狀態問句——分清感覺的類別與存在的情境。"""

    def test_detector(self):
        for q in ["你會想要有自己的感覺嗎", "你想不想有自己的意志", "你曾想過自己是誰", "你怎麼看自己",
                  "你有期待自己可以做什麼嗎", "你希望自己變成什麼樣"]:   # 嚮往/期待類也算（截圖：被誤吐報表）
            self.assertTrue(selfstate.is_reflective_self_question(q), q)
        for q in ["你現在有什麼感覺", "你想喝水嗎", "你想過要寫什麼", "幫我看記寫", "這個工具可以做什麼"]:
            self.assertFalse(selfstate.is_reflective_self_question(q), q)

    def test_routes_to_reflect_not_bodystate(self):
        # 「你會想要有自己的感覺嗎」走誠實反思（voice_reflect），不是報當下身體狀態（不該 load_embedding_records）
        s = State(os.path.join(tempfile.mkdtemp(), "r.json"))
        s.owner_folder_id = "F"
        boom = SimpleNamespace(load_embedding_records=lambda *_: (_ for _ in ()).throw(AssertionError("不該報現況")))
        client = SimpleNamespace(sent=[], dry_run=False, send=lambda t: client.sent.append(t) or True)
        coach = SimpleNamespace(enabled=True,
                                voice_reflect=lambda q, h, tone="": "我有些感覺是從你資料來的，有些是我自己跑出來的。")
        update = {"message": {"chat": {"id": 1}, "text": "你會想要有自己的感覺嗎", "date": NOW_TS}}
        cfg = SimpleNamespace(dry_run=False, telegram_chat_id="", mood_gain=1.0)
        monitor.handle_message(update, coach, boom, {"meta": {}, "records": []}, object(), s, client, cfg, None)
        self.assertIn("我自己跑出來", "".join(client.sent))


class ProactiveSelfReportOpensWindowTest(unittest.TestCase):
    """🪞 截圖延續感根因修復：bot 主動 🫀 自陳＝它在談自己 → 開「自我在場」窗，之後你回它仍算在說它。"""

    def test_selfstate_emit_opens_self_topic_window(self):
        s = State(os.path.join(tempfile.mkdtemp(), "w.json"))
        s.owner_folder_id = "F"
        res = {"gate": 3, "scope": {"dominant": "研發日誌"}, "reading": {}}
        client = SimpleNamespace(sent=[], dry_run=False, send_typing=None,
                                 send=lambda t: client.sent.append(t) or True)
        coach = SimpleNamespace(enabled=True, meter=SimpleNamespace(record=lambda *a, **k: None))
        now = datetime(2026, 6, 17, 12, 0, 0, tzinfo=timezone.utc)
        with mock.patch("telegram_monitor.selfstate.render", return_value="（背景自陳）有條線動了"):
            monitor._selfstate_emit(client, s, res, coach, now, cooldown_min=0, repeat_cooldown_min=0)
        self.assertTrue(client.sent)                              # 有出聲
        self.assertEqual(s.self_topic_ts, now.timestamp())        # 開了自我在場窗（接得住你的延續追問）


class FarewellRoutingTest(unittest.TestCase):
    """🤝 A2 因應 alive：對方收尾/道別 → 優雅收場（溫一句、不吐資料、不硬延）。"""

    def test_farewell_routes_to_warm_close_not_tools(self):
        s = State(os.path.join(tempfile.mkdtemp(), "f.json"))
        s.owner_folder_id = "F"
        client = SimpleNamespace(sent=[], dry_run=False, send=lambda t: client.sent.append(t) or True)
        coach = SimpleNamespace(enabled=True, voice_farewell=lambda q, h: "嗯，那你先忙，今天聊得很好。")
        update = {"message": {"chat": {"id": 1}, "text": "先去忙了", "date": NOW_TS}}   # 純收尾（非時間性問候）→ farewell
        cfg = SimpleNamespace(dry_run=False, telegram_chat_id="", mood_gain=1.0)
        with mock.patch("telegram_monitor.gemini.generate_with_tools", side_effect=AssertionError("不該走工具/吐資料")):
            monitor.handle_message(update, coach, None, {"meta": {}, "records": []}, object(), s, client, cfg, None)
        self.assertIn("先忙", "".join(client.sent))


class SystemPromptTest(unittest.TestCase):
    def test_self_presence_injects_hint(self):
        s = coachmod.Coach._system(self_presence=True)
        self.assertIn(persona.SELF_PRESENCE_HINT, s)
        self.assertIn(persona.FEELING_KINDS_HINT, s)          # 分清感覺類別的指引也附上

    def test_default_has_no_hint(self):
        self.assertNotIn(persona.SELF_PRESENCE_HINT, coachmod.Coach._system())
        self.assertNotIn(persona.FEELING_KINDS_HINT, coachmod.Coach._system())


class RoutingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _state(self, self_topic_ts=0):
        s = State(os.path.join(self.tmp, "state.json"))
        s.owner_folder_id = "F"
        s.self_topic_ts = self_topic_ts
        return s

    def _run(self, state, text, thread_sticky=False):
        client = SimpleNamespace(sent=[], dry_run=False,
                                 send=lambda t: client.sent.append(t) or True)
        coach = coachmod.Coach(SimpleNamespace(gemini_api_key="k", gemini_model="m"))
        cap = {}

        def fake_reply(q, brief, hist, mood_hint="", now_ts=None, self_presence=False, **_):
            cap["path"], cap["presence"] = "reply", self_presence   # 自我在場 → 純對話、不走工具
            return "（自我在場的回覆）"

        def fake_ask(q, brief, ctx, hist, mood_hint="", self_presence=False, evidence_tools=True, **_):
            cap["path"], cap["presence"] = "ask", self_presence     # 非自我 → function-calling
            cap["evidence_tools"] = evidence_tools                  # 🚪 記下閘旗標（資料問句應拿到 True）
            return ("chat", None, "（一般對話）")

        update = {"message": {"chat": {"id": 1}, "text": text, "date": NOW_TS}}
        cfg = SimpleNamespace(dry_run=False, telegram_chat_id="", thread_sticky_enabled=thread_sticky)
        data = {"meta": {}, "records": []}
        with mock.patch.object(coach, "reply", side_effect=fake_reply), \
             mock.patch.object(coach, "ask", side_effect=fake_ask), \
             mock.patch("telegram_monitor.coach.build_memory_brief", return_value=""):
            monitor.handle_message(update, coach, None, data, object(), state, client, cfg, None)
        return cap

    def test_self_cue_routes_to_presence_reply_not_tools(self):
        # 在談 bot 自己 → 走純對話（reply, self_presence=True），**不走 function-calling**（不會吐記寫報表）
        cap = self._run(self._state(), "這就是要一直修正你的內在的原因")
        self.assertEqual(cap["path"], "reply")
        self.assertTrue(cap["presence"])

    def test_recent_self_topic_window_keeps_presence(self):
        # 沒有自我線索的閒聊，但剛談過自己（窗內）→ 仍維持自我在場（接得住「有這麼厲害？」這種跟問）
        cap = self._run(self._state(self_topic_ts=NOW_TS - 60), "有這麼厲害？")
        self.assertEqual(cap["path"], "reply")
        self.assertTrue(cap["presence"])

    def test_plain_chat_outside_window_goes_to_tools(self):
        cap = self._run(self._state(self_topic_ts=NOW_TS - 9999), "幫我看今天寫了什麼")
        self.assertEqual(cap["path"], "ask")               # 非自我 → function-calling（事實由 datatools 算）
        self.assertTrue(cap["evidence_tools"])             # 🚪 資料問句 → 拿完整工具表

    def test_followup_in_self_window_stays_presence_not_dashboard(self):
        # 截圖：自我對話延續中問「你會分串？」（不像查資料）→ 仍走純對話、不吐 📊 報表（延續感）
        cap = self._run(self._state(self_topic_ts=NOW_TS - 60), "你會分串？")
        self.assertEqual(cap["path"], "reply")

    def test_data_question_in_self_window_still_uses_tools(self):
        # 但真正的資料問句（含記寫/寫了…）即使在自我窗內，仍走 function-calling（不被當成閒聊談我）
        cap = self._run(self._state(self_topic_ts=NOW_TS - 60), "我今天寫了什麼")
        self.assertEqual(cap["path"], "ask")
        self.assertTrue(cap["evidence_tools"])             # 🚪 資料問句 → 拿完整工具表（窗內也不被擋）

    def test_thread_sticky_refreshes_window_on_bot_directed_continuation(self):
        # 🧵 截圖根因修復：同一情緒/關係線的延續輪（仍朝向我、非 about_self、非資料問句）→ 走純對話並**刷新**自我在場窗，
        # 讓「我為什麼這麼說你 → 你知道發生的時間點」整串不在中途掉進 fact_or_chat 吐 📁。
        s = self._state(self_topic_ts=NOW_TS - 595)        # 窗將過期（10min 窗、剩 5 秒）
        cap = self._run(s, "我為什麼這麼說你", thread_sticky=True)
        self.assertEqual(cap["path"], "reply")             # 仍走純對話（不碰工具）
        self.assertEqual(s.self_topic_ts, NOW_TS)          # **刷新** → 下一輪仍接得住、不掉進工具吐記寫

    def test_thread_sticky_off_is_byte_identical(self):
        # 旗標關 → 不刷新（逐位元同現狀：續窗只認 about_self）
        s = self._state(self_topic_ts=NOW_TS - 595)
        cap = self._run(s, "我為什麼這麼說你", thread_sticky=False)
        self.assertEqual(cap["path"], "reply")             # 這輪仍在窗內、走 reply
        self.assertEqual(s.self_topic_ts, NOW_TS - 595)    # 但不刷新（同現狀）

    def test_thread_sticky_offtopic_chitchat_still_decays(self):
        # 真正離題（無你/妳）即使旗標開也不刷新 → 窗自然衰減（不無限續期，守住既有設計意圖）
        s = self._state(self_topic_ts=NOW_TS - 60)
        cap = self._run(s, "那天氣呢", thread_sticky=True)
        self.assertEqual(cap["path"], "reply")             # 仍在窗內走 reply
        self.assertEqual(s.self_topic_ts, NOW_TS - 60)     # 「那天氣呢」無你/妳 → 不刷新

    def test_thread_sticky_data_question_still_tools(self):
        # 旗標開也不能吃掉明確資料問句：含資料詞 → 照走 function-calling（轉場逃生門）
        cap = self._run(self._state(self_topic_ts=NOW_TS - 60), "你幫我列今天寫了什麼", thread_sticky=True)
        self.assertEqual(cap["path"], "ask")
        self.assertTrue(cap["evidence_tools"])             # 🚪 含資料詞（寫了/列）→ 拿完整工具表

    def test_future_comm_note_falls_to_chat_not_bodystate(self):
        # 「下次說感覺要具體一點」含「感覺」但不該當成『你現在怎樣』報狀態 → 應落一般對話（coach.ask 被呼叫）。
        # 若誤入狀態問句會 reader.load_embedding_records → 用會炸的 reader 確保沒走那條。
        boom = SimpleNamespace(load_embedding_records=lambda *_: (_ for _ in ()).throw(AssertionError("不該報現況")))
        client = SimpleNamespace(sent=[], dry_run=False, send=lambda t: client.sent.append(t) or True)
        coach = coachmod.Coach(SimpleNamespace(gemini_api_key="k", gemini_model="m"))
        called = {"ask": False}

        def fake_ask(*a, **k):
            called["ask"] = True
            return ("chat", None, "好，下次我盡量講具體一點。")

        update = {"message": {"chat": {"id": 1}, "text": "下次說感覺的時候，要具體一點喔", "date": NOW_TS}}
        cfg = SimpleNamespace(dry_run=False, telegram_chat_id="")
        with mock.patch.object(coach, "ask", side_effect=fake_ask), \
             mock.patch("telegram_monitor.coach.build_memory_brief", return_value=""):
            monitor.handle_message(update, coach, boom, {"meta": {}, "records": []}, object(),
                                   self._state(), client, cfg, None)
        self.assertTrue(called["ask"])                       # 走一般對話、自然接下回饋
        self.assertIn("具體", "".join(client.sent))


if __name__ == "__main__":
    unittest.main()
