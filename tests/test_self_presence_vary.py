"""🪞 自我在場純對話路徑也套「去台詞換句話＋重複×心情長脾氣」(_self_voice_mod)——修截圖：使用者連發「真的嗎」
challenge bot 這個存在時，bot 逐字重複同一段自我說明、毫無變化也無情緒。這條路徑原本獨缺此調制（所有 self_* 路由都有）。
連發幾次後：vary_hint 逼換句話、self_fatigue 升級語氣（前面聊過→又問啦😅無奈）。旗標關＝不套、不記 opener＝逐位元同現狀。"""

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from telegram_monitor import monitor, persona
from telegram_monitor.state import State

NOW = 1_700_000_000


class _Coach:
    enabled = True
    meter = SimpleNamespace(record=lambda *a, **k: None)

    def __init__(self):
        self.reply_calls = []
        self.ask_calls = []

    def reply(self, q, brief, hist, mood_hint="", now_ts=None, self_presence=False, extra_system=""):
        self.reply_calls.append({"extra_system": extra_system, "self_presence": self_presence})
        return "我現在是清晨醒來的狀態，心情是暖的、踏實的。所以我跟你說的都是真的。"

    def ask(self, q, brief, ctx, hist, mood_hint="", self_presence=False, now_ts=None, **_):
        self.ask_calls.append({"q": q})
        return ("chat", None, "（不該走到這。）")


def _cfg(vary=True):
    return SimpleNamespace(dry_run=False, telegram_chat_id="", mood_gain=1.0,
                           self_presence_vary_enabled=vary)


def _run(text, state, coach, cfg, now=NOW):
    state.self_topic_ts = now                              # 維持自我在場窗 → in_self_window=True
    client = SimpleNamespace(sent=[], dry_run=False,
                             send=lambda t: client.sent.append(t) or True,
                             send_typing=lambda: None)
    up = {"message": {"chat": {"id": 1}, "text": text, "date": now}}
    with mock.patch("telegram_monitor.coach.build_memory_brief", return_value=""), \
         mock.patch.object(monitor, "_sleep", create=True):
        monitor.handle_message(up, coach, None, {"meta": {}, "records": []}, object(),
                               state, client, cfg, None)
    return client


class SelfPresenceVaryTest(unittest.TestCase):
    def _spam(self, vary):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        coach = _Coach()
        cfg = _cfg(vary=vary)
        for i in range(4):                                # 連發「真的嗎」四次
            _run("真的嗎", s, coach, cfg, now=NOW + i)
        return s, coach

    def test_reaches_self_presence_reply_not_tools(self):
        s, coach = self._spam(vary=True)
        self.assertEqual(len(coach.reply_calls), 4)       # 走純對話 reply（自我在場）
        self.assertEqual(len(coach.ask_calls), 0)         # 沒掉進 function-calling
        self.assertTrue(all(c["self_presence"] for c in coach.reply_calls))

    def test_flag_on_escalates_vary_then_fatigue(self):
        s, coach = self._spam(vary=True)
        ex = [c["extra_system"] for c in coach.reply_calls]
        self.assertNotIn("換句話", ex[0])                 # 第1次：無前文 opener、無脾氣 → mod 空
        self.assertIn("換句話", ex[1])                    # 第2次：vary_hint 逼換句話（不逐字重複）
        self.assertTrue(any("無奈" in e or "又問" in e for e in ex[2:]))   # 連發到 L2 → self_fatigue 帶無奈
        # 累加器與 opener 都被寫（先前獨缺＝逐字重複根因）
        self.assertEqual(s.self_asks.get("self_presence", {}).get("n"), 4)
        self.assertTrue(s.recent_self_openers)

    def test_flag_off_byte_identical(self):
        s, coach = self._spam(vary=False)
        ex = [c["extra_system"] for c in coach.reply_calls]
        self.assertFalse(any("換句話" in e for e in ex))   # 不套 vary
        self.assertFalse(any("無奈" in e or "前面好像聊過" in e for e in ex))  # 不套 fatigue
        self.assertNotIn("self_presence", s.self_asks)     # 計數器未碰
        self.assertEqual(s.recent_self_openers, [])        # opener 未記


if __name__ == "__main__":
    unittest.main()
