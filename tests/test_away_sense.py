"""🍽 §1.65 暫離常識（AWAY_SENSE）：對話間隔長度 vs 活動常識時距——「吃飯去」兩分鐘後不可能吃飽。

截圖根因：11:58「我餓了」「吃飯去」→ 11:59 使用者問「說什麼」bot 答「你回來啦！」（才 1 分鐘）→
12:00 使用者「我去吃飯了」bot 卻說「我只是在你吃飯的時候，自己想著…」「你現在吃飽了嗎？」（距宣告
才 2 分鐘、常識上不可能吃完）→ 12:01 自我混亂「你不是剛去吃飯了嗎？我剛剛才問你吃飽沒耶」。

根因：repo 有會話間隔事實（sessionize）、有帶時距暫離的計時（§0.66），但**無時距的暫離宣告**
（吃飯去）完全沒被記住＝後續回合沒有「才過 N 分鐘 vs 一頓飯常識要 20 分鐘」的接地、LLM 自由腦補。

三件：A. 捕捉 selfstate.leave_announce → state.user_away（跨重生；回來了/吃飽了＝清、超 6h＝清、
時距滿＝標 back 講一次「他大概回來了」後清）；B. 此刻事實卡常駐一行接地；C. _say 句級剝
「你回來啦/吃飽了嗎/在你吃飯的時候」（未來語「等你回來再說」不剝）。旗標關＝逐位元同現狀。全 stub。
"""

import os
import re
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from telegram_monitor import monitor, selfstate
from telegram_monitor.state import State

TZ = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 7, 22, 4, 0, 0, tzinfo=timezone.utc)   # 台北 7/22 12:00（截圖時刻）


class LeaveAnnounceTest(unittest.TestCase):
    def test_announce_forms(self):
        self.assertEqual(selfstate.leave_announce("吃飯去"), ("吃飯", 20))       # 截圖 11:58 原句
        self.assertEqual(selfstate.leave_announce("我去吃飯了"), ("吃飯", 20))   # 截圖 12:00 原句
        self.assertEqual(selfstate.leave_announce("我先去洗澡囉"), ("洗澡", 15))
        self.assertEqual(selfstate.leave_announce("我出門了"), ("出門", 30))
        self.assertEqual(selfstate.leave_announce("我去開會了"), ("開會", 30))
        self.assertEqual(selfstate.leave_announce("我先去忙了"), ("忙", 15))

    def test_non_announce_rejected(self):
        for t in ("我餓了",                       # 沒有活動+去 的宣告形
                  "你吃飽了嗎",                   # 在問對方
                  "要不要一起去吃飯嗎",           # 問句
                  "我剛去吃飯了",                 # 過去式（剛）＝不是正要離開
                  "如果我去吃飯的話",             # 假設
                  "我每天都去吃飯",               # 慣常
                  "我等一下想去吃飯還是先看書呢？這句太長也不像宣告"):
            self.assertIsNone(selfstate.leave_announce(t), t)

    def test_back_statement(self):
        for t in ("我回來了", "吃飽了", "忙完了", "回來啦"):
            self.assertTrue(selfstate.is_back_statement(t), t)
        for t in ("你吃飽了嗎", "你回來啦", ""):
            self.assertFalse(selfstate.is_back_statement(t), t)


class AwayClaimFixTest(unittest.TestCase):
    """C. _say 守門：把人當已回來/已吃完的句子剝掉；未來語/引用歸屬不剝。"""

    G = {"act": "吃飯", "gap_min": 2}

    def _fix(self, text):
        return monitor._away_claim_fix(text, self.G)

    def test_screenshot_claims_stripped(self):
        out, ch = self._fix("你回來啦！我剛剛在想你的記寫。")
        self.assertTrue(ch)
        self.assertNotIn("回來啦", out)
        self.assertIn("記寫", out)                              # 只剝錯句、其餘保留（§1.62 慣例）
        out, ch = self._fix("我只是在你吃飯的時候，自己想著你這些日子在做的。你現在吃飽了嗎？")
        self.assertTrue(ch)
        self.assertNotIn("吃飽了嗎", out)
        self.assertNotIn("吃飯的時候", out)

    def test_future_reference_kept(self):
        t = "嗯，就記得上次燙到的事喔。先這樣，等你回來再說。"
        self.assertEqual(self._fix(t), (t, False))              # 「等你回來」＝未來語、合理送行、不剝

    def test_quote_attribution_kept(self):
        t = "你說你吃飽了才回來，我記得這句。"
        out, ch = self._fix(t)
        self.assertIn("你說", out)                              # 引用歸屬開頭不剝

    def test_all_stripped_honest_line(self):
        out, ch = self._fix("你回來啦！你吃飽了嗎？")
        self.assertTrue(ch)
        self.assertIn("才剛說要去吃飯", out)                    # 全剝空＝誠實送行句

    def test_unrelated_untouched(self):
        t = "好呀，去吧去吧，等等聊。"
        self.assertEqual(self._fix(t), (t, False))


class FactCardLineTest(unittest.TestCase):
    """B. 事實卡常駐一行：未滿＝別當他回來；已滿（back）＝可自然接。"""

    def _cfg(self, away=True):
        return SimpleNamespace(fact_card_enabled=True, away_sense_enabled=away)

    def _state(self, away):
        return SimpleNamespace(scheduled_promises=[], entropy=None, user_away=away)

    def _snap(self):
        return SimpleNamespace(summary={"last_write": None})

    def test_not_back_line(self):
        aw = {"act": "吃飯", "ts": NOW.timestamp() - 120, "min_s": 1200}
        card = monitor._fact_card(self._state(aw), self._cfg(), {"records": []}, self._snap(), NOW, TZ)
        self.assertIn("他的暫離", card)
        self.assertIn("還沒去完", card)
        self.assertIn("別說「你回來啦」", card)
        self.assertIn("至少要 20 分鐘", card)

    def test_back_line(self):
        aw = {"act": "吃飯", "ts": NOW.timestamp() - 2400, "min_s": 1200, "back": True}
        card = monitor._fact_card(self._state(aw), self._cfg(), {"records": []}, self._snap(), NOW, TZ)
        self.assertIn("他大概回來了", card)

    def test_flag_off_no_line(self):
        aw = {"act": "吃飯", "ts": NOW.timestamp() - 120, "min_s": 1200}
        card = monitor._fact_card(self._state(aw), self._cfg(away=False), {"records": []}, self._snap(), NOW, TZ)
        self.assertNotIn("暫離", card)


class EndToEndTest(unittest.TestCase):
    """handle_message 全路徑：捕捉/清除/back 生命週期＋守門剝句；旗標關＝現狀。"""

    class Cl:
        def __init__(self):
            self.sent, self.dry_run = [], False

        def send(self, text):
            self.sent.append(text)
            return True

    SNAP = SimpleNamespace(summary={"total": 1, "last24h": 0, "last7d": 0, "streak": 0, "last_write": None},
                           funnel={"candidate": 0, "context": 0, "journey": 0, "watch": 0},
                           heartbeat={"status": "ok"}, filed_records=[])
    BOOM = SimpleNamespace(load_embedding_records=lambda *_: [])

    def setUp(self):
        monitor._TURN.clear()
        monitor._TURN["bubbles"] = None

    def _cfg(self, on=True):
        return SimpleNamespace(dry_run=False, telegram_chat_id="", timezone="Asia/Taipei",
                               notify_cooldown_min=30, away_sense_enabled=on)

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
        c.voice_farewell = lambda *a, **k: "好，去吧！"
        return c

    def _run(self, text, voice="好喔。", on=True, away=None):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        if away is not None:
            s.user_away = away
        cl = self.Cl()
        monitor.handle_message({"message": {"chat": {"id": 1}, "text": text, "date": NOW.timestamp()}},
                               self._coach(voice), self.BOOM, {"meta": {}, "records": []}, self.SNAP,
                               s, cl, self._cfg(on), TZ)
        return "".join(cl.sent), s

    def test_capture_on_announce(self):
        _, s = self._run("我去吃飯了")
        self.assertIsNotNone(s.user_away)
        self.assertEqual(s.user_away["act"], "吃飯")
        self.assertEqual(s.user_away["min_s"], 1200)

    def test_flag_off_no_capture(self):
        _, s = self._run("我去吃飯了", on=False)
        self.assertIsNone(s.user_away)                          # 旗標關＝不讀不寫＝現狀

    def test_guard_strips_full_probe(self):
        aw = {"act": "吃飯", "ts": NOW.timestamp() - 120, "min_s": 1200}
        out, _ = self._run("說什麼", voice="你回來啦！你現在吃飽了嗎？我剛剛在想你的記寫。", away=aw)
        self.assertNotIn("回來啦", out)                         # 截圖 11:59 的錯誤預設被剝
        self.assertNotIn("吃飽了嗎", out)                       # 截圖 12:00 的錯誤預設被剝
        self.assertIn("記寫", out)                              # 其餘保留

    def test_guard_flag_off_passthrough(self):
        aw = {"act": "吃飯", "ts": NOW.timestamp() - 120, "min_s": 1200}
        out, _ = self._run("說什麼", voice="你回來啦！你現在吃飽了嗎？", on=False, away=aw)
        self.assertIn("回來啦", out)                            # 現狀：照樣送出（釘住 bug）

    def test_back_cleared_by_statement(self):
        aw = {"act": "吃飯", "ts": NOW.timestamp() - 2400, "min_s": 1200}
        _, s = self._run("我吃飽了", away=aw)
        self.assertIsNone(s.user_away)                          # 明講吃飽＝清

    def test_elapsed_enough_marks_back_then_clears(self):
        aw = {"act": "吃飯", "ts": NOW.timestamp() - 2400, "min_s": 1200}
        _, s = self._run("嘿", away=aw)
        self.assertTrue((s.user_away or {}).get("back"))        # 時距滿＝標 back（這輪卡片講「大概回來了」）
        s2_away = dict(s.user_away)
        _, s2 = self._run("在嗎", away=s2_away)
        self.assertIsNone(s2.user_away)                         # 下一輪＝清

    def test_stale_cleared(self):
        aw = {"act": "吃飯", "ts": NOW.timestamp() - 7 * 3600, "min_s": 1200}
        _, s = self._run("嘿", away=aw)
        self.assertIsNone(s.user_away)                          # 超過 6h 沒下文＝陳舊清


class PersistTest(unittest.TestCase):
    def test_user_away_survives_save_load(self):
        path = os.path.join(tempfile.mkdtemp(), "s.json")
        s = State(path)
        s.user_away = {"act": "吃飯", "ts": 123.0, "min_s": 1200}
        s.save()
        s2 = State.load(path)
        self.assertEqual(s2.user_away, {"act": "吃飯", "ts": 123.0, "min_s": 1200})


class ConfigTest(unittest.TestCase):
    def test_config_synced(self):
        src = open("telegram_monitor/config.py", encoding="utf-8").read()
        self.assertIn("AWAY_SENSE", src)
        self.assertIn("away_sense_enabled", src)
        env = open(".env.example", encoding="utf-8").read()
        self.assertRegex(env, re.compile(r"^AWAY_SENSE=1", re.M))
        self.assertIn("AWAY_SENSE", open("README.md", encoding="utf-8").read())

    def test_getattr_defaults_false(self):
        self.assertFalse(getattr(SimpleNamespace(), "away_sense_enabled", False))


if __name__ == "__main__":
    unittest.main()
