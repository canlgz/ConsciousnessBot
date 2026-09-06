"""✒️ 被問「你的訊息/聯想為什麼有 markdown／是不是純文字」：要走純對話＋格式接地（FORMAT_HINT），
別掉進 function-calling／泛用聊天而 confabulate 出一套「內部運作格式」、暴露實作。"""

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from telegram_monitor import monitor, persona, selfstate
from telegram_monitor.state import State

NOW = 1_700_000_000


class DetectTest(unittest.TestCase):
    def test_direct_format_questions(self):
        for q in ["你的訊息串裡為什麼會有 markdown 格式？", "你回的字怎麼有星號",
                  "你的訊息為什麼有 ** 符號", "你的訊息是純文字嗎", "你的聯想為什麼有反引號",
                  "你的訊息為什麼有奇怪的格式"]:
            self.assertTrue(selfstate.is_format_question(q), q)

    def test_followup_only_when_recent(self):
        # 窗內省略追問（本身不帶格式詞）：在格式話題窗內才認
        q = "在你的自我聯想內容裡有嗎"
        self.assertTrue(selfstate.is_format_question(q, recent=True))
        self.assertFalse(selfstate.is_format_question(q, recent=False))

    def test_not_format_question(self):
        # 「格式/排版」泛詞＋一個「你」但不是問 bot 訊息格式（如問使用者自己的記寫格式）→ 不該誤抓
        for q in ["這個檔案是什麼格式", "今天天氣如何", "我多久沒理你", "你覺得我的記寫格式如何",
                  "你覺得我怎樣", "幫我看今天寫了什麼", "匯出格式可以選嗎"]:
            self.assertFalse(selfstate.is_format_question(q, recent=False), q)
        # 窗內也不該把無關句誤抓
        self.assertFalse(selfstate.is_format_question("幫我看今天寫了什麼", recent=True))


class _Coach:
    """記錄 reply/ask 各被呼叫的參數，方便斷言『走純對話 reply 且帶 FORMAT_HINT』。"""
    enabled = True
    meter = SimpleNamespace(record=lambda *a, **k: None)

    def __init__(self):
        self.reply_calls = []
        self.ask_calls = []

    def reply(self, q, brief, hist, mood_hint="", now_ts=None, self_presence=False, extra_system=""):
        self.reply_calls.append({"q": q, "extra_system": extra_system, "self_presence": self_presence})
        return "我跟你講話一直都是大白話，沒在用什麼格式。"

    def ask(self, q, brief, ctx, hist, mood_hint="", self_presence=False, now_ts=None, **_):
        self.ask_calls.append({"q": q})
        return ("chat", None, "（不該走到這。）")


def _run(text, state, coach, now=NOW):
    client = SimpleNamespace(sent=[], dry_run=False,
                             send=lambda t: client.sent.append(t) or True,
                             send_typing=lambda: None)
    cfg = SimpleNamespace(dry_run=False, telegram_chat_id="", mood_gain=1.0,
                          self_format_enabled=True, thread_sticky_enabled=False)
    up = {"message": {"chat": {"id": 1}, "text": text, "date": now}}
    with mock.patch("telegram_monitor.coach.build_memory_brief", return_value=""), \
         mock.patch.object(monitor, "_sleep", create=True):
        monitor.handle_message(up, coach, None, {"meta": {}, "records": []}, object(),
                               state, client, cfg, None)
    return client


class RouteTest(unittest.TestCase):
    def test_format_question_goes_to_reply_with_hint_not_tools(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        coach = _Coach()
        _run("你的訊息串裡為什麼會有 markdown 格式？", s, coach)
        # 走純對話 reply、帶 FORMAT_HINT；沒掉進 function-calling ask
        self.assertEqual(len(coach.reply_calls), 1)
        self.assertEqual(coach.reply_calls[0]["extra_system"], persona.FORMAT_HINT)
        self.assertEqual(len(coach.ask_calls), 0)
        # 開了格式窗 → 跨重生持久
        self.assertGreater(s.format_topic_ts, 0)

    def test_followup_within_window_also_grounded(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        coach = _Coach()
        _run("你的訊息串裡為什麼會有 markdown 格式？", s, coach, now=NOW)
        _run("在你的自我聯想內容裡有嗎", s, coach, now=NOW + 30)   # 省略追問、窗內
        self.assertEqual(len(coach.reply_calls), 2)
        self.assertIn(persona.FORMAT_HINT, coach.reply_calls[1]["extra_system"])  # 追問也接住格式接地（此輪另疊去台詞換句話 mod、故用 assertIn）
        self.assertEqual(len(coach.ask_calls), 0)

    def test_flag_off_falls_back(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        coach = _Coach()
        client = SimpleNamespace(sent=[], dry_run=False,
                                 send=lambda t: client.sent.append(t) or True, send_typing=lambda: None)
        cfg = SimpleNamespace(dry_run=False, telegram_chat_id="", mood_gain=1.0,
                              self_format_enabled=False, thread_sticky_enabled=False)
        up = {"message": {"chat": {"id": 1}, "text": "你的訊息串裡為什麼會有 markdown 格式？", "date": NOW}}
        with mock.patch("telegram_monitor.coach.build_memory_brief", return_value=""):
            monitor.handle_message(up, coach, None, {"meta": {}, "records": []}, object(),
                                   s, client, cfg, None)
        # 關旗標：不強制注入 FORMAT_HINT（落回原行為；不論走 reply 或 ask 都不帶 hint）
        for c in coach.reply_calls:
            self.assertNotEqual(c["extra_system"], persona.FORMAT_HINT)


if __name__ == "__main__":
    unittest.main()
