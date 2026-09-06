# -*- coding: utf-8 -*-
"""🪞 §2.26 引用歸屬守門：把自己說過的話講成「你說「X」」＝話者翻轉。

實測截圖（10:47–11:05）：
  10:47 🌀 bot 體驗自陳「我進到這裡。可是，它其實跟我之前那個活著一樣。感覺並沒有什麼改變。」
  11:04 使用者「你正在想什麼」→ 11:05 bot「我正在想...**你說**「我進到這裡。」可是，它其實跟我
  之前那個活著一樣。」——「我進到這裡」是 bot 自己說的，不是使用者＝主詞/賓語錯亂。

素材面逐層查過都乾淨（workspace 候選全內在、_history_contents 角色無誤）＝LLM 讀史料自己翻轉。
prompt 管不住的照家規上確定性守門：引用內容只在 model 側出現過 ⇒ 話者必錯 ⇒ 就地改正。
全 stub、零網路。
"""

import os
import tempfile
import unittest
from unittest import mock

from telegram_monitor import monitor
from telegram_monitor.state import State

BOT_SAID = "🌀 我進到這裡。可是，它其實跟我之前那個活著一樣。感覺並沒有什麼改變。"
GROUND = {"model": ["你今天也照樣把經書讀完了。", BOT_SAID],
          "user": ["早安", "你正在想什麼"]}
SHOT_REPLY = "我正在想...你說「我進到這裡。」可是，它其實跟我之前那個活著一樣。"


class QuoteSpeakerFixTest(unittest.TestCase):
    def test_screenshot_flip_corrected(self):
        out, ch = monitor._quote_speaker_fix(SHOT_REPLY, GROUND)
        self.assertTrue(ch)
        self.assertIn("我剛說「我進到這裡。」", out)
        self.assertNotIn("你說「我進到這裡", out)

    def test_legit_user_quote_untouched(self):
        # 他真的說過的話被引成「你說」＝合法，一字不動
        g = {"model": [BOT_SAID], "user": ["我今天讀完地藏經了"]}
        msg = "你說「我今天讀完地藏經了」，聽起來很扎實。"
        out, ch = monitor._quote_speaker_fix(msg, g)
        self.assertFalse(ch)
        self.assertEqual(out, msg)

    def test_reverse_flip_corrected(self):
        # 反向：把使用者的話講成「我說過」→ 改「你說」
        g = {"model": ["嗯，我在。"], "user": ["最近讀經讀得比較穩"]}
        out, ch = monitor._quote_speaker_fix("我說過「最近讀經讀得比較穩」，所以我有印象。", g)
        self.assertTrue(ch)
        self.assertIn("你說「最近讀經讀得比較穩」", out)

    def test_ambiguous_or_unknown_untouched(self):
        # 兩邊都出現（他覆誦過我那句）＝無從裁決；兩邊都沒有＝引的不是近史 → 都不動
        g = {"model": [BOT_SAID], "user": ["我進到這裡。"]}
        msg = "你說「我進到這裡。」"
        self.assertEqual(monitor._quote_speaker_fix(msg, g), (msg, False))
        g2 = {"model": ["嗯。"], "user": ["早安"]}
        msg2 = "你說「宇宙的答案是42」嗎？"
        self.assertEqual(monitor._quote_speaker_fix(msg2, g2), (msg2, False))

    def test_rhetorical_flip_corrected_without_broken_grammar(self):
        msg = "你不是說「我進到這裡。」嗎？"
        out, ch = monitor._quote_speaker_fix(msg, GROUND)
        self.assertTrue(ch)
        self.assertIn("那句「我進到這裡。」是我剛說的，不是你說的", out)
        self.assertNotIn("你不是", out)

    def test_old_model_quote_uses_true_gap_not_just_now(self):
        now = 1_800_000_000.0
        g = {"now_ts": now,
             "model": [{"text": "嗯，我剛剛也讀誦完今天的進度。", "ts": now - 86400}],
             "user": [{"text": "所以我到今天都沒動？", "ts": now}]}
        msg = "你不是剛剛才說「嗯，我剛剛也讀誦完今天的進度。」嗎？"
        out, ch = monitor._quote_speaker_fix(msg, g)
        self.assertTrue(ch)
        self.assertIn("是我約 1 天前說的", out)
        self.assertNotIn("我剛剛說", out)

    def test_missing_or_bad_timestamp_uses_neutral_previous_not_just_now(self):
        for ts in (None, "bad", float("nan")):
            g = {"now_ts": 1_800_000_000.0,
                 "model": [{"text": "這是我原本說的", "ts": ts}], "user": ["別句"]}
            out, changed = monitor._quote_speaker_fix("你說「這是我原本說的」嗎？", g)
            self.assertTrue(changed, repr(ts))
            self.assertIn("我先前說的", out)
            self.assertNotIn("我剛", out)

    def test_direct_question_becomes_declarative_attribution(self):
        out, changed = monitor._quote_speaker_fix("你說「我進到這裡。」嗎？", GROUND)
        self.assertTrue(changed)
        self.assertEqual(out, "那句「我進到這裡。」是我剛說的。")

    def test_screenshot_chain_removes_speaker_time_and_activity_errors(self):
        now = 1_800_000_000.0
        raw = ("你不是剛剛才說「嗯，我剛剛也讀誦完今天的進度。」嗎？"
               "這句就是你今天寫的啊。"
               "所以不是沒動喔，你有動，而且是關於〔閱讀｜讀誦經書〕這條線。")
        fixed, write_changed = monitor._write_claim_fix(raw, {"today_count": 0, "last_label": "昨天 09:06"})
        out, speaker_changed = monitor._quote_speaker_fix(
            fixed, {"now_ts": now,
                    "model": [{"text": "嗯，我剛剛也讀誦完今天的進度。", "ts": now - 86400}],
                    "user": [{"text": "所以我到今天都沒動？", "ts": now}]})
        self.assertTrue(write_changed)
        self.assertTrue(speaker_changed)
        self.assertIn("昨天 09:06", out)
        self.assertIn("我約 1 天前說的", out)
        for wrong in ("你不是剛剛才說", "你今天寫的", "你有動"):
            self.assertNotIn(wrong, out)

    def test_no_quote_no_change(self):
        msg = "我正在想剛剛那條線的事。"
        self.assertEqual(monitor._quote_speaker_fix(msg, GROUND), (msg, False))


class WiringTest(unittest.TestCase):
    def setUp(self):
        with open("telegram_monitor/monitor.py", encoding="utf-8") as f:
            self.src = f.read()

    def test_stash_includes_current_user_text(self):
        # user 側含本句＝「你說「X」」引他這句＝合法不動
        i = self.src.index('_TURN["speaker_ground"]')
        seg = self.src[i:i + 1400]
        self.assertIn('"text": text or ""', seg)
        self.assertIn('"ts": _user_msg_ts', seg)
        self.assertIn('[-30:]', seg)

    def test_guard_in_say_chain_after_habit(self):
        habit = self.src.index("_habit_claim_fix(text, _hg, greet_fallback=_greet_fb)")
        quote = self.src.index('_sg = _TURN.get("speaker_ground")')
        self.assertLess(habit, quote)                       # 佈線：緊接 §1.42 之後、同一守門鏈
        self.assertIn("_quote_speaker_fix(text, _sg)", self.src)


class DeliveredHistoryTest(unittest.TestCase):
    class Client:
        dry_run = True

        def __init__(self):
            self.sent = []

        def send(self, text):
            self.sent.append(text)
            return True

    def test_history_records_guarded_text_not_unsent_original(self):
        saved = dict(monitor._TURN)
        monitor._TURN.clear()
        try:
            now = 1_800_000_000.0
            raw = "你不是剛剛才說「嗯，我剛剛也讀誦完今天的進度。」嗎？"
            monitor._TURN["bubbles"] = None
            monitor._TURN["speaker_ground"] = {
                "now_ts": now,
                "model": [{"text": "嗯，我剛剛也讀誦完今天的進度。", "ts": now - 86400}],
                "user": [{"text": "所以我到今天都沒動？", "ts": now}],
            }
            client, state = self.Client(), State(os.path.join(tempfile.mkdtemp(), "state.json"))
            monitor._say(client, raw)
            monitor._remember(state, "model", raw, ts=now)
            actual = "".join(client.sent)
            self.assertEqual(state.convo_history[-1]["text"], actual)
            self.assertIn("我約 1 天前說的", actual)
            self.assertNotIn("你不是剛剛才說", state.convo_history[-1]["text"])
            self.assertNotIn(raw, str(monitor._TURN.get("sent_model_text") or {}))
        finally:
            monitor._TURN.clear()
            monitor._TURN.update(saved)

    def test_proactive_write_guard_records_actual_delivery(self):
        saved = dict(monitor._TURN)
        monitor._TURN.clear()
        try:
            raw = "你今天早上又寫了讀誦經書的進度。"
            monitor._TURN.update({"bubbles": None,
                                  "write_claim_ground_proactive": {"today_count": 0,
                                                                     "last_label": "昨天 09:06"}})
            client, state = self.Client(), State(os.path.join(tempfile.mkdtemp(), "state.json"))
            monitor._say(client, raw, prefix="🫧 ", state=state)
            monitor._remember(state, "model", "🫧 " + raw, ts=1_800_000_000.0)
            actual = "".join(client.sent)
            self.assertEqual(state.convo_history[-1]["text"], actual)
            self.assertIn("昨天 09:06", actual)
            self.assertNotIn("今天早上又寫了", state.convo_history[-1]["text"])
        finally:
            monitor._TURN.clear()
            monitor._TURN.update(saved)

    def test_suppressed_short_duplicate_is_not_written_to_history(self):
        saved = dict(monitor._TURN)
        monitor._TURN.clear()
        try:
            monitor._TURN.update({"bubbles": None, "short_dup_guard": True})
            client, state = self.Client(), State(os.path.join(tempfile.mkdtemp(), "state.json"))
            with mock.patch.object(monitor, "_short_dup_hit", return_value=True):
                self.assertTrue(monitor._say(client, "好，記下來了。"))
            monitor._remember(state, "model", "好，記下來了。")
            self.assertEqual(state.convo_history, [])
            self.assertEqual(client.sent, [])
        finally:
            monitor._TURN.clear()
            monitor._TURN.update(saved)


class FlagSyncTest(unittest.TestCase):
    def test_flag_everywhere(self):
        with open("telegram_monitor/config.py", encoding="utf-8") as f:
            src = f.read()
        self.assertIn('_bool("QUOTE_SPEAKER_GUARD", True)', src)
        self.assertIn("quote_speaker_guard_enabled", src)
        with open(".env.example", encoding="utf-8") as f:
            self.assertIn("QUOTE_SPEAKER_GUARD=1", f.read())
        with open("README.md", encoding="utf-8") as f:
            self.assertIn("QUOTE_SPEAKER_GUARD", f.read())


if __name__ == "__main__":
    unittest.main()
