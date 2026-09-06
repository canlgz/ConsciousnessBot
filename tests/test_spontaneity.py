"""🌱 根因 4：「你怎麼還沒開始分享聯想」＝催促 bot 主動出聲分享聯想（問 bot 自己的自發行為、非查記寫）。

要走 self_spontaneity → 純對話（coach.reply, self_presence=True, extra_system=SPONTANEITY_HINT），開自我在場窗，
**不**進 coach.ask、**不**列 📂 清單。從嚴四共現＋排除資料詞，避免吃掉「列出聯想主題」這類其實要列資料的句。
"""

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from telegram_monitor import intent, monitor, persona, referent, selfstate
from telegram_monitor.state import State

NOW_TS = 1_700_000_000


class DetectTest(unittest.TestCase):
    def test_hits(self):
        for q in ["你怎麼還沒開始分享聯想", "你為什麼不主動說想法", "你怎麼不出聲你的念頭",
                  "你還沒分享你的聯想", "妳怎麼還沒主動說想法"]:
            self.assertTrue(selfstate.is_share_association_question(q), q)

    def test_misses_data_questions_still_go_to_tools(self):
        # 〔資料保留〕含資料詞 → 不是 meta、要去列資料（走工具），別被 self_spontaneity 吃掉
        for q in ["列出聯想主題", "我的聯想寫了什麼", "列出聯想紀錄", "哪些是你的聯想"]:
            self.assertFalse(selfstate.is_share_association_question(q), q)

    def test_misses_unrelated(self):
        for q in ["你的聯想格式如何", "你怎麼還沒回我", "分享聯想", "你會分享聯想嗎", "我今天心情不好"]:
            self.assertFalse(selfstate.is_share_association_question(q), q)


def _ref():
    return referent.Referent()


class RouteTest(unittest.TestCase):
    def _kind(self, text, spont=True):
        return intent.resolve(text, _ref(), None, SimpleNamespace(spontaneity_enabled=spont)).kind

    def test_routes_to_self_spontaneity_when_on(self):
        self.assertEqual(self._kind("你怎麼還沒開始分享聯想"), "self_spontaneity")

    def test_flag_off_falls_back_to_fact_or_chat(self):
        self.assertEqual(self._kind("你怎麼還沒開始分享聯想", spont=False), "fact_or_chat")

    def test_cfg_none_default_off_byte_identical(self):
        # cfg=None（純路由測試慣例）→ 預設 OFF＝逐位元同現狀（落 fact_or_chat）
        self.assertEqual(intent.resolve("你怎麼還沒開始分享聯想", _ref()).kind, "fact_or_chat")

    def test_does_not_hijack_mechanism(self):
        # 排在 self_mechanism 之後、窄判定 → 不搶上位機制問句
        self.assertEqual(self._kind("你的機制是什麼"), "self_mechanism")

    def test_data_list_question_stays_fact_or_chat(self):
        # 「列出聯想主題」含資料詞 → 不被 spontaneity 收，仍走 fact_or_chat（之後由工具列資料）
        self.assertEqual(self._kind("列出聯想主題"), "fact_or_chat")


class HandleRoutingTest(unittest.TestCase):
    def _state(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        return s

    def test_goes_to_reply_not_ask_opens_window_with_hint(self):
        state = self._state()
        client = SimpleNamespace(sent=[], dry_run=False,
                                 send=lambda t: client.sent.append(t) or True, send_typing=lambda: None)
        cap = {}

        def fake_reply(q, brief, hist, mood_hint="", now_ts=None, self_presence=False, extra_system="", **_):
            cap["reply"] = True
            cap["self_presence"] = self_presence
            cap["extra_system"] = extra_system
            return "我的聯想是自己冒出來的——真有上來時我自然會說給你聽。"

        coach = SimpleNamespace(enabled=True, reply=fake_reply,
                                meter=SimpleNamespace(record=lambda *a, **k: None))
        update = {"message": {"chat": {"id": 1}, "text": "你怎麼還沒開始分享聯想", "date": NOW_TS}}
        cfg = SimpleNamespace(dry_run=False, telegram_chat_id="", mood_gain=1.0, spontaneity_enabled=True)
        # 走 self_spontaneity 就不該碰 function-calling／工具（不列 📂 清單）
        with mock.patch("telegram_monitor.gemini.generate_with_tools", side_effect=AssertionError("不該走工具/列清單")), \
             mock.patch("telegram_monitor.coach.build_memory_brief", return_value=""):
            monitor.handle_message(update, coach, None, {"meta": {}, "records": []}, object(),
                                   state, client, cfg, None)
        self.assertTrue(cap.get("reply"))                       # 走純對話 reply
        self.assertTrue(cap.get("self_presence"))               # 自我在場
        self.assertEqual(cap.get("extra_system"), persona.SPONTANEITY_HINT)  # 帶自發湧現接地
        self.assertEqual(state.self_topic_ts, NOW_TS)           # 開了自我在場窗
        self.assertIn("自然會說", "".join(client.sent))

    def test_flag_off_does_not_route_spontaneity(self):
        # 旗標關 → 不走 self_spontaneity；這句落回 fact_or_chat（會走 coach.ask）
        state = self._state()
        client = SimpleNamespace(sent=[], dry_run=False,
                                 send=lambda t: client.sent.append(t) or True, send_typing=lambda: None)
        cap = {}

        def fake_ask(q, brief, ctx, hist, mood_hint="", self_presence=False, now_ts=None, evidence_tools=True, **_):
            cap["ask"] = True
            cap["evidence_tools"] = evidence_tools
            return ("chat", None, "（一般對話）")

        coach = SimpleNamespace(enabled=True, ask=fake_ask,
                                meter=SimpleNamespace(record=lambda *a, **k: None),
                                reply=lambda *a, **k: "（reply）")
        update = {"message": {"chat": {"id": 1}, "text": "你怎麼還沒開始分享聯想", "date": NOW_TS}}
        cfg = SimpleNamespace(dry_run=False, telegram_chat_id="", mood_gain=1.0,
                              spontaneity_enabled=False, evidence_gate_enabled=True, thread_sticky_enabled=False)
        with mock.patch("telegram_monitor.coach.build_memory_brief", return_value=""):
            monitor.handle_message(update, coach, None, {"meta": {}, "records": []}, object(),
                                   state, client, cfg, None)
        self.assertTrue(cap.get("ask"))                         # 落回 fact_or_chat
        self.assertFalse(cap["evidence_tools"])                 # 但 evidence_gate 兜底擋掉證據工具（不列 📂）


if __name__ == "__main__":
    unittest.main()
