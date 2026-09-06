"""🎨 §1.67 座標回報去機械感（MOOD_WATCH_VOICE）：守約內容不鬆、口吻是自己的。

使用者回饋（§1.66 上線後截圖）：「太機械感了」。兩個機械源：
 ① 主動回報是純模板「座標變動回報（你交代過…）：V…→…」＝系統通知腔；
 ② 訂閱 ack 的座標數字**明明是程式算的**、卻沒掛 §1.47 的 mood_coord_grounded 接地豁免旗
   ＝被座標守門誤咬成「照程式此刻讀的真數字：V…」模板（截圖 18:16 兩句殭硬輸出的真身）。

修法：① _mood_watch_emit 先請 coach.reply 用第一人稱潤色——數字程式算、只准原樣照抄，
_mood_watch_voice_ok 確定性逐字驗收（四個 ±X.XX 都要在、不准多任何小數）；不過＝退回模板
（守約永不漏發）；② ack 掛豁免＋措辭放軟。旗標關＝恆模板＝逐位元同 §1.66。全 stub、零網路。
"""

import os
import re
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from telegram_monitor import monitor
from telegram_monitor.state import State

NOW = datetime(2026, 7, 22, 10, 20, 0, tzinfo=timezone.utc)

ALLOWED = ("+0.10", "+0.00", "+0.30", "-0.05")
GOOD_VOICE = "跟你說一聲，這是你交代過的：我從 V +0.10→+0.30、A +0.00→-0.05，像是被剛剛的對話鼓舞了一下。"


class VoiceOkTest(unittest.TestCase):
    def test_good_voice_passes(self):
        self.assertTrue(monitor._mood_watch_voice_ok(GOOD_VOICE, ALLOWED))

    def test_missing_number_rejected(self):
        self.assertFalse(monitor._mood_watch_voice_ok("我變成 V +0.30 了，感覺不錯。", ALLOWED))

    def test_foreign_decimal_rejected(self):
        bad = GOOD_VOICE + "大概變動了 0.25 吧。"                 # 編了一個程式沒算過的小數
        self.assertFalse(monitor._mood_watch_voice_ok(bad, ALLOWED))

    def test_unsigned_copies_rejected(self):
        bad = "我從 V 0.10→0.30、A 0.00→-0.05。"                 # 掉了正負號＝沒有原樣照抄
        self.assertFalse(monitor._mood_watch_voice_ok(bad, ALLOWED))

    def test_empty_rejected(self):
        self.assertFalse(monitor._mood_watch_voice_ok("", ALLOWED))
        self.assertFalse(monitor._mood_watch_voice_ok(None, ALLOWED))


class EmitVoiceTest(unittest.TestCase):
    class Cl:
        def __init__(self):
            self.sent, self.dry_run = [], False

        def send(self, t):
            self.sent.append(t)
            return True

    def setUp(self):
        monitor._TURN.clear()
        monitor._TURN["bubbles"] = None

    def _state(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.entropy = SimpleNamespace(mood=0.30, arousal=-0.05, hunger=0.0)
        s.mood_watch = {"ts": 1.0, "last_v": 0.10, "last_a": 0.0, "last_report_ts": 0.0}
        return s

    def _coach(self, voice):
        c = SimpleNamespace(enabled=True, api_key="k", model="m", asked=[],
                            meter=SimpleNamespace(record=lambda *a, **k: None))

        def reply(question, *a, **k):
            c.asked.append(question)
            return voice

        c.reply = reply
        return c

    def _cfg(self, voice_on=True):
        return SimpleNamespace(dry_run=False, mood_watch_voice_enabled=voice_on)

    def test_voice_used_when_valid(self):
        cl, co = self.Cl(), self._coach(GOOD_VOICE)
        monitor._mood_watch_emit(cl, self._state(), self._cfg(), NOW, coach=co)
        out = "".join(cl.sent)
        self.assertIn("被剛剛的對話鼓舞", out)                   # 用了口吻版
        self.assertIn("+0.10→+0.30", out)                       # 真數字仍在
        self.assertNotIn("座標變動回報（你交代過", out)          # 不再是系統通知腔
        self.assertIn("只准原樣照抄", co.asked[0])               # prompt 明令數字紀律

    def test_invalid_voice_falls_back_to_template(self):
        cl = self.Cl()
        monitor._mood_watch_emit(cl, self._state(), self._cfg(), NOW,
                                 coach=self._coach("我感覺變動了大概 0.25 左右。"))
        out = "".join(cl.sent)
        self.assertIn("座標變動回報", out)                       # 驗收不過＝退回模板（守約不漏發）
        self.assertIn("+0.10→+0.30", out)
        self.assertNotIn("0.25", out)                           # 編的數字送不出去

    def test_no_coach_template(self):
        cl = self.Cl()
        monitor._mood_watch_emit(cl, self._state(), self._cfg(), NOW, coach=None)
        self.assertIn("座標變動回報", "".join(cl.sent))

    def test_flag_off_template_even_with_coach(self):
        cl, co = self.Cl(), self._coach(GOOD_VOICE)
        monitor._mood_watch_emit(cl, self._state(), self._cfg(voice_on=False), NOW, coach=co)
        self.assertIn("座標變動回報", "".join(cl.sent))          # 旗標關＝§1.66 模板行為
        self.assertFalse(co.asked)                              # 也不多燒 LLM


class AckGroundedTest(unittest.TestCase):
    """② 訂閱 ack 掛 §1.47 接地豁免＝不再被座標守門咬成「照程式此刻讀的真數字」模板。"""

    class Cl(EmitVoiceTest.Cl):
        pass

    def setUp(self):
        monitor._TURN.clear()
        monitor._TURN["bubbles"] = None

    def test_subscribe_sets_grounded_exemption(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.entropy = SimpleNamespace(mood=0.20, arousal=-0.10, hunger=0.0)
        cl = self.Cl()
        cfg = SimpleNamespace(dry_run=False, mood_watch_enabled=True)
        handled = monitor._maybe_mood_watch(cl, s, cfg, "情緒座標如果有任何變動，必須主動回報",
                                            NOW, NOW.timestamp())
        self.assertTrue(handled)
        self.assertTrue(monitor._TURN.get("mood_coord_grounded"))   # 豁免旗掛上＝守門不誤咬
        out = "".join(cl.sent)
        self.assertIn("基準", out)                                  # 基準說明完整送達
        self.assertIn("0.10", out)                                  # 門檻說明不再被吃掉


class ConfigTest(unittest.TestCase):
    def test_config_synced(self):
        src = open("telegram_monitor/config.py", encoding="utf-8").read()
        self.assertIn("MOOD_WATCH_VOICE", src)
        self.assertIn("mood_watch_voice_enabled", src)
        env = open(".env.example", encoding="utf-8").read()
        self.assertRegex(env, re.compile(r"^MOOD_WATCH_VOICE=1", re.M))
        self.assertIn("MOOD_WATCH_VOICE", open("README.md", encoding="utf-8").read())

    def test_getattr_defaults_false(self):
        self.assertFalse(getattr(SimpleNamespace(), "mood_watch_voice_enabled", False))


if __name__ == "__main__":
    unittest.main()
