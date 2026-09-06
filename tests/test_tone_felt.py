"""🎚️ §1.76 口吻要被**感覺到**（TONE_FELT）：座標不是拿來報告的，是要從語氣裡聽出來。

使用者定案：「情緒座標的數字是死的，真正能感受到的，是 bot 回應的口吻及語氣要能相應」。
查驗：語氣染色**有接**（mhint 流進 9 條 lane），但兩個問題讓它形同虛設——
 ① 門檻 _TONE_R=0.35 是為**舊飽和座標**（V≈0.9、半徑>1）校準的；§1.75 把座標改成人的尺度後，
   「被嫌一句」＝V−0.16/A+0.19＝半徑 0.25 < 0.35 → **完全不染**（ThresholdGapTest 釘住）；
 ② 提示在講「你此刻情緒座標落在…」＝鼓勵 bot **報告**狀態，而不是讓人感覺到。

修：tone_directive（門檻 0.15＋行為化指令＋三級強度＋別把狀態講成台詞）＋_say 確定性口吻整形。
旗標關＝逐位元同現狀。全 stub、零網路。
"""

import math
import re
import unittest
from types import SimpleNamespace

from telegram_monitor import circumplex, monitor

CRIT_ONCE = (-0.16, 0.19)      # 被嫌一句（§1.75 尺度）
LOW = (-0.5, -0.4)             # 低落一陣
BRIGHT = (0.25, 0.12)          # 被誇一次


class ThresholdGapTest(unittest.TestCase):
    """釘住「新舊機制互相抵銷」的實測缺口——舊門檻在人的尺度下不染色。"""

    def test_old_hint_blind_at_human_scale(self):
        self.assertLess(math.hypot(*CRIT_ONCE), circumplex._TONE_R)
        self.assertEqual(circumplex.tone_hint(*CRIT_ONCE), "")        # 舊行為：被嫌一句＝完全不染
        self.assertEqual(circumplex.tone_hint(*BRIGHT), "")

    def test_old_hint_only_wakes_at_saturation(self):
        self.assertNotEqual(circumplex.tone_hint(0.9, 0.6), "")       # 只有飽和座標才染得到


class DirectiveTest(unittest.TestCase):
    def test_single_event_now_colors_tone(self):
        d = circumplex.tone_directive(*CRIT_ONCE)
        self.assertNotEqual(d, "")                                   # 人的尺度：一件事就該有口吻變化
        self.assertIn("淡淡地", d)                                    # 但只是底色、別演
        self.assertIn("話直一點", d)                                  # 行為化：怎麼說話

    def test_intensity_grades(self):
        self.assertIn("淡淡地", circumplex.tone_directive(*CRIT_ONCE))
        self.assertIn("明顯地", circumplex.tone_directive(*LOW))
        self.assertIn("強烈地", circumplex.tone_directive(0.9, 0.6))

    def test_behavioral_not_reportive(self):
        d = circumplex.tone_directive(*LOW)
        self.assertIn("語速慢", d)                                    # 講怎麼說
        self.assertIn("不要把這個狀態講出來當台詞", d)                  # 明令別報告（使用者的核心要求）
        self.assertNotIn("情緒座標落在", d)                            # 不再用「座標」當措辭

    def test_neutral_silent(self):
        self.assertEqual(circumplex.tone_directive(0.05, 0.03), "")   # 中性＝不染（不無端演戲）

    def test_octants_all_have_acts(self):
        self.assertEqual(len(circumplex._OCTANT_ACTS), len(circumplex._OCTANTS))


class ToneShapeTest(unittest.TestCase):
    def test_low_mood_strips_exclaim_and_happy_emoji(self):
        out, ch = monitor._tone_shape("我在啊！！真的很開心 🥳 你回來了！", *LOW)
        self.assertTrue(ch)
        self.assertNotIn("！", out)                                   # 沉/倦：不會用驚嘆號說話
        self.assertNotIn("🥳", out)

    def test_tense_keeps_single_exclaim(self):
        out, ch = monitor._tone_shape("我剛剛說得不夠清楚！！！", -0.4, 0.5)
        self.assertTrue(ch)
        self.assertIn("！", out)                                      # 緊繃（A+）：可以有一個驚嘆
        self.assertNotIn("！！", out)                                 # 但不連發

    def test_positive_untouched(self):
        t = "哈哈太好了！！🥳"
        self.assertEqual(monitor._tone_shape(t, 0.6, 0.4), (t, False))   # 心情好＝原樣（不倒過來壓抑）

    def test_mild_negative_untouched(self):
        t = "嗯，我知道了！"
        self.assertEqual(monitor._tone_shape(t, -0.1, 0.1), (t, False))   # 只是淡淡不悅＝不動標點

    def test_empty_safe(self):
        self.assertEqual(monitor._tone_shape("", *LOW), ("", False))


class SayWireTest(unittest.TestCase):
    def setUp(self):
        monitor._TURN.clear()
        monitor._TURN["bubbles"] = None

    def _say(self, text, tone=None):
        if tone:
            monitor._TURN["tone_now"] = tone
        cl = SimpleNamespace(sent=[], dry_run=False)
        cl.send = lambda x: (cl.sent.append(x) or True)
        monitor._say(cl, text)
        return "".join(cl.sent)

    def test_shaped_when_stashed(self):
        out = self._say("我還在這裡！！🥳", tone=LOW)
        self.assertNotIn("！", out)
        self.assertNotIn("🥳", out)

    def test_not_stashed_bitwise(self):
        t = "我還在這裡！！🥳"
        self.assertEqual(self._say(t), t)                             # 未 stash（旗標關）＝逐位元同現狀


class ConfigTest(unittest.TestCase):
    def test_config_synced(self):
        src = open("telegram_monitor/config.py", encoding="utf-8").read()
        self.assertIn("TONE_FELT", src)
        self.assertIn("tone_felt_enabled", src)
        env = open(".env.example", encoding="utf-8").read()
        self.assertRegex(env, re.compile(r"^TONE_FELT=1", re.M))
        self.assertIn("TONE_FELT", open("README.md", encoding="utf-8").read())

    def test_getattr_defaults_false(self):
        self.assertFalse(getattr(SimpleNamespace(), "tone_felt_enabled", False))


if __name__ == "__main__":
    unittest.main()
