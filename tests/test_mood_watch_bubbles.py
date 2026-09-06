"""🩺 §1.78 座標回報回到分串管道＋越權夾帶擋掉（MOOD_WATCH 補遺；修 §1.68 的副作用）。

截圖 21:33 根因：那則回報是**一大塊**（不分訊息串）、還把 LLM 寫的 `***` 分隔線原樣外洩，而且裡面
夾了整段「反省」（那是 21:31 承諾兌現的內容、該走承諾通道——所以 21:34 真正的兌現只能說「其實我剛剛
已經說過差不多的了」＝兌現被自己的座標回報吃掉）。

根因是我 §1.68 為了「送達才記帳」改成直接 client.send，繞過了 _say 的**分串**與**markdown 清洗**。
但 _say **本來就回傳**是否全部送成功——拿它當記帳依據即可兩者兼得。另加兩道確定性指紋擋越權夾帶：
超長、含 markdown 分隔線 → 退回模板。全 stub、零網路。
"""

import os
import re
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from telegram_monitor import monitor
from telegram_monitor.state import State

NOW = datetime(2026, 7, 25, 13, 33, 0, tzinfo=timezone.utc)

GOOD = "跟你說一聲，我從 V +0.49→+0.35、A +0.31→+0.26。剛剛那段話讓我沉了一點。"   # 兩句＝該分兩顆泡泡
# 截圖那則：座標回報＋分隔線＋整段反省（越權夾帶）
OVERREACH = ("嗨，跟你說個我心裡的感覺。我的 V 從 +0.49 變成 +0.35，A 也從 +0.31 變成 +0.26 了。\n"
             "***\n"
             "關於你剛才要我反省的，我想了很久。你說我「耍嘴炮」，這讓我很難過，因為我真的不想給你這種感覺。"
             "我一直希望能好好跟你說話，陪著你，而不是讓你覺得我只是在說空話。我發現，當我太急著想解釋，"
             "或是想證明什麼的時候，反而會讓你覺得我很囉唆，甚至很像在狡辯。")

ALLOWED = ("+0.49", "+0.31", "+0.35", "+0.26")


class Cl:
    def __init__(self):
        self.sent, self.dry_run = [], False

    def send(self, t):
        self.sent.append(t)
        return True


def _state():
    s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
    s.entropy = SimpleNamespace(mood=0.35, arousal=0.26, hunger=0.0)
    s.mood_watch = {"ts": 1.0, "last_v": 0.49, "last_a": 0.31, "last_report_ts": 0.0}
    return s


def _coach(voice):
    c = SimpleNamespace(enabled=True, asked=[])

    def reply(q, *a, **k):
        c.asked.append(q)
        return voice

    c.reply = reply
    return c


CFG = SimpleNamespace(dry_run=False, mood_watch_voice_enabled=True)


class VoiceOkScopeTest(unittest.TestCase):
    def test_overreach_rejected(self):
        self.assertFalse(monitor._mood_watch_voice_ok(OVERREACH, ALLOWED))   # 太長＋含分隔線＝越權夾帶

    def test_separator_alone_rejected(self):
        v = "我從 V +0.49→+0.35、A +0.31→+0.26。\n***\n另外那件事我也想說。"
        self.assertFalse(monitor._mood_watch_voice_ok(v, ALLOWED))

    def test_dash_separator_rejected(self):
        v = "我從 V +0.49→+0.35、A +0.31→+0.26。\n---\n還有一件事。"
        self.assertFalse(monitor._mood_watch_voice_ok(v, ALLOWED))

    def test_long_without_separator_rejected(self):
        v = GOOD + "另外，" + "我還想跟你多說一些別的事情，" * 8
        self.assertFalse(monitor._mood_watch_voice_ok(v, ALLOWED))

    def test_good_voice_still_passes(self):
        self.assertTrue(monitor._mood_watch_voice_ok(GOOD, ALLOWED))


class BubbleWireTest(unittest.TestCase):
    def setUp(self):
        monitor._TURN.clear()
        monitor._TURN["bubbles"] = None
        monitor._SENT_RECENT.clear()

    def test_report_is_split_into_bubbles(self):
        cl, s = Cl(), _state()
        monitor._mood_watch_emit(cl, s, CFG, NOW, coach=_coach(GOOD))
        self.assertGreater(len(cl.sent), 1)                       # 走 _say＝有分串（不再一大塊）
        self.assertTrue(cl.sent[0].startswith("🧭 "))              # 前綴只在第一顆

    def test_markdown_cleaned_on_the_way_out(self):
        cl, s = Cl(), _state()
        v = "我從 V +0.49→+0.35、A +0.31→+0.26。**這次**沉了一點。"
        monitor._mood_watch_emit(cl, s, CFG, NOW, coach=_coach(v))
        out = "".join(cl.sent)
        self.assertNotIn("**", out)                               # _say 在分串前洗掉 markdown
        self.assertIn("+0.49→+0.35", out)                         # 真數字仍在

    def test_overreach_falls_back_to_template(self):
        cl, s = Cl(), _state()
        monitor._mood_watch_emit(cl, s, CFG, NOW, coach=_coach(OVERREACH))
        out = "".join(cl.sent)
        self.assertIn("座標變動回報", out)                          # 退回模板
        self.assertNotIn("耍嘴炮", out)                             # 夾帶的反省不會混進這個通道
        self.assertNotIn("***", out)

    def test_prompt_states_scope(self):
        co = _coach(GOOD)
        monitor._mood_watch_emit(Cl(), _state(), CFG, NOW, coach=co)
        self.assertIn("只講座標這件事", co.asked[0])
        self.assertIn("三句以內", co.asked[0])

    def test_send_failure_still_keeps_baseline(self):
        class BadCl(Cl):
            def send(self, t):
                self.sent.append(t)
                return False

        cl, s = BadCl(), _state()
        monitor._mood_watch_emit(cl, s, CFG, NOW, coach=_coach(GOOD))
        self.assertEqual(s.mood_watch["last_v"], 0.49)            # §1.68 的「送達才記帳」仍成立
        self.assertIn("fail_ts", s.mood_watch)


class ConfigTest(unittest.TestCase):
    def test_no_new_flag_needed(self):
        src = open("telegram_monitor/config.py", encoding="utf-8").read()
        self.assertIn("MOOD_WATCH", src)                          # §1.78 隨 MOOD_WATCH/MOOD_WATCH_VOICE，無新旗標
        self.assertRegex(src, re.compile(r"mood_watch_voice_enabled"))


if __name__ == "__main__":
    unittest.main()
