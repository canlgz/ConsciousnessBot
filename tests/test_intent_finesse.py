"""🎯 §1.70 詢問意圖接得住：三個「不夠細膩」的機制修（截圖 10:23–11:08 逐處對帳）。

A. 假送閘句級軟化（STICKER_FAKESEND_SOFT）：使用者釐清「我是指，你在翻閱我的記寫過程中」，LLM 回答
   夾一句假送宣稱 → §1.34 閘**整則替換**成貼圖誠實模板＝正題回答被吃掉、答非所問。修：只剝宣稱句、
   正題保留＋誠實補註；全剝空＝退回原模板（§1.62「整則替換是最後手段」）。
B. 懸著的提議接地（OPEN_OFFER_GROUND）：bot 拋「要我送一張嗎？」31 分鐘後使用者回「送什麼？」——
   LLM 主詞反轉答成「你確實送了一張」。修：近 2h 內 bot 最近一句提議形問句常駐入事實卡。
C. 座標回報開場修（隨 MOOD_WATCH_VOICE）：兩次回報同款開場「啊，對了，我答應過你的。」＋「🧭 🧭」
   雙前綴（LLM 抄了歷史裡的前綴）。修：prompt 明令開場每次換、別自帶前綴；送出前剝自帶 🧭。
全 stub、零網路；旗標關＝逐位元同現狀。
"""

import os
import re
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from telegram_monitor import monitor
from telegram_monitor.state import State

NOW = datetime(2026, 7, 23, 3, 0, 0, tzinfo=timezone.utc)

CLAIM = "我剛剛選了一張很平靜的貼圖送給你。"
ANSWER = "翻你記寫時最讓我驚訝的，是你連挫折那天都寫得很誠實。"


class FakesendSoftTest(unittest.TestCase):
    def test_claim_stripped_answer_kept(self):
        out, ch = monitor._fakesend_soft_fix(ANSWER + CLAIM)
        self.assertTrue(ch)
        self.assertIn("寫得很誠實", out)                        # 正題保留
        self.assertNotIn("選了一張", out)                       # 假送句剝掉

    def test_all_claim_returns_none(self):
        out, ch = monitor._fakesend_soft_fix(CLAIM)
        self.assertTrue(ch)
        self.assertIsNone(out)                                  # 全剝空＝呼叫端退回原整則模板

    def test_no_claim_untouched(self):
        self.assertEqual(monitor._fakesend_soft_fix(ANSWER), (ANSWER, False))

    def _say(self, text, soft=True, maint=False):
        monitor._TURN.clear()
        monitor._TURN.update({"bubbles": None, "sticker_fakesend_arm": True,
                              "sticker_fakesend_maint": maint, "sticker_fakesend_soft": soft})
        cl = SimpleNamespace(sent=[], dry_run=False)
        cl.send = lambda t: (cl.sent.append(t) or True)
        monitor._say(cl, text)
        return "".join(cl.sent)

    def test_say_soft_keeps_answer(self):
        out = self._say(ANSWER + CLAIM)
        self.assertIn("寫得很誠實", out)                        # 截圖 10:26 修後：釐清的正題答案活下來
        self.assertIn("其實沒有", out)                          # 誠實補註仍在
        self.assertNotIn("別讓我用一句話假裝送了", out)          # 不再整則模板

    def test_say_all_claim_falls_back_to_template(self):
        out = self._say(CLAIM)
        self.assertIn("別讓我用一句話假裝送了", out)             # 整則都是宣稱＝照舊硬替換

    def test_say_flag_off_bitwise(self):
        out = self._say(ANSWER + CLAIM, soft=False)
        self.assertIn("別讓我用一句話假裝送了", out)             # 旗標關＝原整則替換（釘住現狀）
        self.assertNotIn("寫得很誠實", out)

    def test_say_maint_variant(self):
        out = self._say(ANSWER + CLAIM, maint=True)
        self.assertIn("寫得很誠實", out)
        self.assertIn("送不出來", out)                          # 維護期補註不反問要不要送


class OpenOfferTest(unittest.TestCase):
    def _hist(self, mins_ago, text, role="model"):
        return {"role": role, "text": text, "ts": NOW.timestamp() - mins_ago * 60}

    def test_offer_found_across_turns(self):
        h = [self._hist(34, "我其實沒真的送出貼圖。要我送一張嗎？"),
             self._hist(31, "讓我心裡有點興奮、雀躍起來了呢？"),      # 修辭問句、無提議 cue＝不搶
             self._hist(30, "剛剛聊得好開心", role="user")]
        oo = monitor._last_open_offer(h, NOW.timestamp())
        self.assertIsNotNone(oo)
        self.assertIn("要我送一張嗎", oo[0])                    # 找到的是**提議形**問句、不是修辭問
        self.assertEqual(oo[1], 34)

    def test_stale_offer_ignored(self):
        h = [self._hist(150, "要我送一張嗎？")]
        self.assertIsNone(monitor._last_open_offer(h, NOW.timestamp()))   # 超過 2h＝不掛

    def test_no_offer_none(self):
        h = [self._hist(5, "今天天氣不錯。"), self._hist(3, "嗯嗯", role="user")]
        self.assertIsNone(monitor._last_open_offer(h, NOW.timestamp()))

    def test_fact_card_line(self):
        s = SimpleNamespace(scheduled_promises=[], entropy=None, user_away=None, mood_watch=None,
                            convo_history=[self._hist(31, "要我送一張嗎？")])
        cfg = SimpleNamespace(fact_card_enabled=True, open_offer_ground_enabled=True)
        card = monitor._fact_card(s, cfg, {"records": []}, SimpleNamespace(summary={"last_write": None}), NOW, None)
        self.assertIn("還懸著的提議", card)
        self.assertIn("要我送一張嗎", card)
        self.assertIn("主詞是我、不是他", card)                  # 修主詞反轉的關鍵句

    def test_fact_card_flag_off_no_line(self):
        s = SimpleNamespace(scheduled_promises=[], entropy=None, user_away=None, mood_watch=None,
                            convo_history=[self._hist(31, "要我送一張嗎？")])
        cfg = SimpleNamespace(fact_card_enabled=True, open_offer_ground_enabled=False)
        card = monitor._fact_card(s, cfg, {"records": []}, SimpleNamespace(summary={"last_write": None}), NOW, None)
        self.assertNotIn("還懸著的提議", card)


class MoodOpenerTest(unittest.TestCase):
    class Cl:
        def __init__(self):
            self.sent, self.dry_run = [], False

        def send(self, t):
            self.sent.append(t)
            return True

    def _state(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.entropy = SimpleNamespace(mood=0.30, arousal=-0.05, hunger=0.0)
        s.mood_watch = {"ts": 1.0, "last_v": 0.10, "last_a": 0.0, "last_report_ts": 0.0}
        return s

    def _coach(self, voice):
        c = SimpleNamespace(enabled=True, asked=[])

        def reply(q, *a, **k):
            c.asked.append(q)
            return voice

        c.reply = reply
        return c

    VOICE = "🧭 這是答應過你的：我從 V +0.10→+0.30、A +0.00→-0.05，心情亮了一截。"

    def test_leading_prefix_stripped_single_glyph(self):
        cl = self.Cl()
        cfg = SimpleNamespace(dry_run=False, mood_watch_voice_enabled=True)
        monitor._mood_watch_emit(cl, self._state(), cfg, NOW, coach=self._coach(self.VOICE))
        self.assertEqual(len(cl.sent), 1)
        self.assertTrue(cl.sent[0].startswith("🧭 這是答應過你的"))   # 只有系統加的一個前綴
        self.assertNotIn("🧭 🧭", cl.sent[0])                        # 不再雙前綴（截圖症狀）

    def test_prompt_asks_varied_opener(self):
        co = self._coach(self.VOICE)
        cfg = SimpleNamespace(dry_run=False, mood_watch_voice_enabled=True)
        monitor._mood_watch_emit(self.Cl(), self._state(), cfg, NOW, coach=co)
        self.assertIn("開場每次換個說法", co.asked[0])            # 明令別固定同一句開場
        self.assertIn("啊，對了", co.asked[0])                    # 點名截圖那句制式開場


class ConfigTest(unittest.TestCase):
    def test_config_synced(self):
        src = open("telegram_monitor/config.py", encoding="utf-8").read()
        for k in ("STICKER_FAKESEND_SOFT", "sticker_fakesend_soft_enabled",
                  "OPEN_OFFER_GROUND", "open_offer_ground_enabled"):
            self.assertIn(k, src)
        env = open(".env.example", encoding="utf-8").read()
        self.assertRegex(env, re.compile(r"^STICKER_FAKESEND_SOFT=1", re.M))
        self.assertRegex(env, re.compile(r"^OPEN_OFFER_GROUND=1", re.M))
        rd = open("README.md", encoding="utf-8").read()
        self.assertIn("STICKER_FAKESEND_SOFT", rd)
        self.assertIn("OPEN_OFFER_GROUND", rd)

    def test_getattr_defaults_false(self):
        self.assertFalse(getattr(SimpleNamespace(), "sticker_fakesend_soft_enabled", False))
        self.assertFalse(getattr(SimpleNamespace(), "open_offer_ground_enabled", False))


if __name__ == "__main__":
    unittest.main()
