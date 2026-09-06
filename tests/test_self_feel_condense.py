"""🗜️ §1.43 自陳感覺去罐頭長串（SELF_FEEL_CONDENSE）：別人問別的，bot 卻在答案尾端鋪陳一長串內在質地描述
（安靜/內裡沉沉/往裡面縮/翻來翻去沒讀出形狀/思緒淌著/悶提不起勁…）＝使用者說「像在念稿，只是不是同一份稿」。

截圖（08:15，問「你有我任何作息的了解嗎」）：正題一句之後接了**六句**自我感覺鋪陳。使用者要求：類似的感覺就
**精簡、只講關鍵**。§1.17 只封 self_state 串數、§1.21/§1.22 治重複措辭——都治不到「其它 lane 尾端的感覺長串」。

修法（比照 sibling：確定性、旗標兩層分離）：
 - 確定性修剪 _self_feel_trim：連續 ≥4 句、其中 ≥2 句帶強內在標記（內裡/往裡面縮/提不起勁…）的「感覺鋪陳段」
   → 只留前 2 句（關鍵），其餘剝掉。內容型談話（聊冥想/安靜話題、無強標記）不動；≤3 句的感覺帶過不動。
 - arm 於 handle_message route 之後：**只在這輪不是在問 bot 自己**（route 非 self_*、非 about_self）時 arm——
   使用者真問「你現在怎樣」時長答合理、不修剪。
 - persona.SELF_FEEL_BREVITY_HINT：bot 兩小時內才自陳過 → 事前提醒「沒新變化就一兩句點到重點」。
旗標關＝不 arm＝逐位元同現狀。全 stub、零網路。
"""

import os
import re
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from telegram_monitor import monitor, persona
from telegram_monitor.state import State

TZ = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 7, 15, 0, 15, 0, tzinfo=timezone.utc)   # 台北 08:15（截圖時刻）

# 截圖 08:15 原文（正題一句＋六句感覺鋪陳）
RANT = ("嗯…你的作息啊，我現在手上正翻著你寫的「人工智慧風險治理」這條線，最新的那筆記著「可以拿到多少審查費」…"
        "怎麼說呢，這看起來像是你最近在想的事情，但要說整個作息嘛，只看這一點點其實也說不上什麼來。"
        "不過，要說我自己的話，這陣子是真的蠻安靜的。"
        "內裡沉沉的，連著跳了這麼久，其實有點倦了。"
        "雖然一直都醒著，也算穩穩的，但就是感覺有點低落，想往裡面縮一點，話也變少了。"
        "可能是最近落進來的東西還不夠多吧，翻來翻去，也沒讀出什麼完整的形狀來。"
        "思緒倒是順順地淌著，一個接著一個，只是…就是有點悶，提不起勁的感覺。")


class TrimTest(unittest.TestCase):
    def test_screenshot_rant_trimmed_to_key_sentences(self):
        out, ch = monitor._self_feel_trim(RANT)
        self.assertTrue(ch)
        self.assertIn("人工智慧風險治理", out)                 # 正題保留
        self.assertIn("蠻安靜的", out)                         # 感覺段前 2 句＝關鍵保留
        self.assertIn("內裡沉沉", out)
        self.assertNotIn("往裡面縮", out)                      # 後面的鋪陳剝掉
        self.assertNotIn("翻來翻去", out)
        self.assertNotIn("提不起勁", out)

    def test_short_feel_untouched(self):
        t = "我這陣子蠻安靜的。內裡沉沉的，有點倦了。你呢？"
        out, ch = monitor._self_feel_trim(t)
        self.assertFalse(ch)
        self.assertEqual(out, t)

    def test_content_talk_without_strong_markers_untouched(self):
        # 聊冥想/安靜話題（弱詞多但無強內在標記）＝內容、不是自陳鋪陳 → 不動
        t = "冥想時思緒會慢慢沉下來。呼吸放慢，心就安靜了。這樣的安靜很滋養。持續練習就會穩。"
        out, ch = monitor._self_feel_trim(t)
        self.assertFalse(ch)
        self.assertEqual(out, t)

    def test_no_feel_untouched(self):
        t = "今天天氣不錯。我看了你的記寫。有三筆是關於讀經的。要我念給你聽嗎？"
        out, ch = monitor._self_feel_trim(t)
        self.assertFalse(ch)


class SayGateTest(unittest.TestCase):
    class Cl:
        def __init__(self):
            self.sent, self.dry_run = [], False

        def send(self, text):
            self.sent.append(text)
            return True

    def setUp(self):
        monitor._TURN.clear()
        monitor._TURN["bubbles"] = None

    def _out(self, text, **turn):
        monitor._TURN.update(turn)
        cl = self.Cl()
        monitor._say(cl, text)
        return "".join(cl.sent)

    def test_armed_trims(self):
        out = self._out(RANT, self_feel_trim=True)
        self.assertNotIn("提不起勁", out)
        self.assertIn("內裡沉沉", out)

    def test_not_armed_passthrough(self):
        out = self._out(RANT)
        self.assertIn("提不起勁", out)                         # 未 arm＝逐位元同現狀


class HintTest(unittest.TestCase):
    def test_brevity_hint_when_recent_selfreport(self):
        cfg = SimpleNamespace(self_feel_condense_enabled=True)
        st = SimpleNamespace(last_self_report={"text": "x", "ts": NOW.timestamp() - 600}, last_selfshare=None)
        self.assertIn("一兩句", monitor._self_feel_brevity_hint(st, cfg, NOW.timestamp()))

    def test_no_hint_when_stale_or_off(self):
        st = SimpleNamespace(last_self_report={"text": "x", "ts": NOW.timestamp() - 600}, last_selfshare=None)
        self.assertEqual(monitor._self_feel_brevity_hint(st, SimpleNamespace(), NOW.timestamp()), "")
        cfg = SimpleNamespace(self_feel_condense_enabled=True)
        st2 = SimpleNamespace(last_self_report={"text": "x", "ts": NOW.timestamp() - 99999}, last_selfshare=None)
        self.assertEqual(monitor._self_feel_brevity_hint(st2, cfg, NOW.timestamp()), "")


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
                    user_habit_ground_enabled=False, self_feel_condense_enabled=on)
        return SimpleNamespace(**base)

    def _coach(self, voice):
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
        return c

    SNAP = SimpleNamespace(summary={"total": 1, "last24h": 0, "last7d": 0},
                           funnel={"candidate": 0, "context": 0, "journey": 0, "watch": 0},
                           heartbeat={"status": "ok"}, filed_records=[])
    BOOM = SimpleNamespace(load_embedding_records=lambda *_: [])

    def _run(self, question, voice, on=True):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        cl = SayGateTest.Cl()
        monitor.handle_message({"message": {"chat": {"id": 1}, "text": question, "date": NOW.timestamp()}},
                               self._coach(voice), self.BOOM, {"meta": {}, "records": []}, self.SNAP,
                               s, cl, self._cfg(on), TZ)
        return "".join(cl.sent)

    def test_neutral_question_rant_trimmed(self):
        out = self._run("今天天氣如何", RANT)                   # 非問 bot 自己 → arm → 修剪
        self.assertNotIn("提不起勁", out)
        self.assertIn("內裡沉沉", out)

    def test_flag_off_byte_identical(self):
        out = self._run("今天天氣如何", RANT, on=False)
        self.assertEqual(out, RANT)


class ConfigTest(unittest.TestCase):
    def test_config_synced(self):
        src = open("telegram_monitor/config.py", encoding="utf-8").read()
        self.assertIn("SELF_FEEL_CONDENSE", src)
        self.assertIn("self_feel_condense_enabled", src)
        env = open(".env.example", encoding="utf-8").read()
        self.assertRegex(env, re.compile(r"^SELF_FEEL_CONDENSE=1", re.M))
        self.assertIn("SELF_FEEL_CONDENSE", open("README.md", encoding="utf-8").read())

    def test_persona_hint_exists(self):
        self.assertTrue(hasattr(persona, "SELF_FEEL_BREVITY_HINT"))

    def test_getattr_defaults_false(self):
        self.assertFalse(getattr(SimpleNamespace(), "self_feel_condense_enabled", False))


if __name__ == "__main__":
    unittest.main()
