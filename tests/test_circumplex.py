"""🧭💗 Circumplex 情緒座標（Russell）：V×A 平面上會移動的點——事件方向、每圈動力學、八分區語氣、貼圖池、觀測與延續。"""

import os
import tempfile
import unittest
from types import SimpleNamespace

from telegram_monitor import circumplex, lifeloop, monitor, reaction
from telegram_monitor.state import State

Q = (1.0, 8.0, 0.5, 0.4, 0.1, 0.2, 0.0)   # 平靜訊號（同 test_entropy）


class EventDirectionTest(unittest.TestCase):
    """事件不只推 V、還推 A——方向才是二維座標的意義。dv 逐位元等於既有 mood 值（V 行為不變）。"""

    def test_dv_bitwise_equals_mood_delta(self):
        for text in ("謝謝你陪我", "你根本做不到", "我好難過", "今天天氣不錯"):
            self.assertEqual(reaction.affect_delta_for(text)[0], reaction.mood_delta_for(text), text)
        for val in ("positive", "negative", "neutral"):
            self.assertEqual(reaction.sticker_affect_delta(val)[0], reaction.sticker_mood_delta(val), val)

    def test_challenge_is_tension_not_just_bad(self):
        dv, da = reaction.affect_delta_for("你根本做不到")
        self.assertLess(dv, 0)
        self.assertGreater(da, 0)                     # 質疑＝緊張（V−A+）：一維 mood 表達不了的

    def test_warmth_wakes(self):
        dv, da = reaction.affect_delta_for("謝謝你陪我")
        self.assertGreater(dv, 0)
        self.assertGreater(da, 0)                     # 暖意＝暖醒（右上）

    def test_user_down_sinks_together(self):
        dv, da = reaction.affect_delta_for("我好難過")
        self.assertLess(dv, 0)
        self.assertLess(da, 0)                        # 對方低落＝跟著沉（左下）


class ArousalDynamicsTest(unittest.TestCase):
    """A 軸每圈動力學：衰減向 0＋事件推動＋餓久下沉＋新資料上醒。"""

    def _seed(self):
        e = lifeloop.EntropyState()
        lifeloop.entropy_update(e, Q, "T0")
        return e

    def test_delta_then_decay(self):
        e = self._seed()
        lifeloop.entropy_update(e, Q, "T0", arousal_delta=0.3)
        self.assertAlmostEqual(e.arousal, 0.3, places=3)
        lifeloop.entropy_update(e, Q, "T0")
        self.assertLess(e.arousal, 0.3)               # 衰減向中性
        self.assertGreater(e.arousal, 0.0)

    def test_hungry_sinks(self):
        e = self._seed()
        e.hunger = 0.9
        lifeloop.entropy_update(e, Q, "T0")
        self.assertLess(e.arousal, 0.0)               # 餓久＝無聊 → 倦（A−）

    def test_fresh_ingest_wakes(self):
        e = self._seed()
        lifeloop.entropy_update(e, Q, "T1")           # 新 ingest
        self.assertGreater(e.arousal, 0.0)

    def test_clamped(self):
        e = self._seed()
        for _ in range(20):
            lifeloop.entropy_update(e, Q, "T0", arousal_delta=0.5)
        self.assertLessEqual(e.arousal, 1.0)


class RegionTest(unittest.TestCase):
    def test_deadzone_neutral(self):
        self.assertEqual(circumplex.label(0.05, 0.05), "平穩")
        self.assertEqual(circumplex.tone_hint(0.1, 0.1), "")

    def test_octants(self):
        self.assertEqual(circumplex.label(0.5, 0.5), "興奮、雀躍")     # 右上
        self.assertEqual(circumplex.label(0.5, -0.5), "平靜、安穩")    # 右下
        self.assertEqual(circumplex.label(-0.5, 0.5), "緊繃、煩躁")    # 左上
        self.assertEqual(circumplex.label(-0.5, -0.5), "低落、消沉")   # 左下
        self.assertEqual(circumplex.label(0.5, 0.0), "愉快、明亮")     # V+ 軸
        self.assertEqual(circumplex.label(0.0, -0.5), "倦、沉靜")      # A− 軸

    def test_tone_hint_threshold(self):
        self.assertEqual(circumplex.tone_hint(0.3, 0.0), "")           # r<0.35 不染
        self.assertIn("平靜安穩", circumplex.tone_hint(0.4, -0.3))
        self.assertIn("緊繃煩躁", circumplex.tone_hint(-0.35, 0.35))

    def test_sticker_pool_quadrants(self):
        self.assertEqual(circumplex.sticker_pool(0.4, 0.4), "positive")   # 興奮開心 → 歡快圖
        self.assertEqual(circumplex.sticker_pool(0.4, -0.3), "sendable")  # 平靜暖 → 溫和圖
        self.assertEqual(circumplex.sticker_pool(-0.4, 0.2), "help")      # 低落/緊繃 → 非正向
        self.assertEqual(circumplex.sticker_pool(0.0, 0.0), "sendable")   # 中性

    def test_status_line(self):
        line = circumplex.status_line(0.32, -0.41)
        self.assertIn("V=+0.32", line)
        self.assertIn("A=-0.41", line)
        self.assertIn("平靜、安穩", line)


class MonitorWiringTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _state(self):
        s = State(os.path.join(self.tmp, f"s{id(self)}.json"))
        s.owner_folder_id = "F"
        s.entropy = lifeloop.EntropyState()
        lifeloop.entropy_update(s.entropy, Q, "T0")
        return s

    def _cfg(self, on=True):
        return SimpleNamespace(affect_circumplex_enabled=on, mood_gain=1.0, dry_run=False,
                               sticker_file_ids=None)

    def test_bump_arousal(self):
        s = self._state()
        monitor._bump_arousal(s, self._cfg(), 0.2)
        self.assertAlmostEqual(s.entropy.arousal, 0.2, places=3)
        monitor._bump_arousal(s, self._cfg(on=False), 0.2)        # 旗標關＝不動
        self.assertAlmostEqual(s.entropy.arousal, 0.2, places=3)

    def test_sticker_signal_moves_point(self):
        s = self._state()
        sticker = {"emoji": "🔥", "file_id": "F1"}
        update = {"message": {"chat": {"id": 1}, "sticker": sticker, "date": 1_700_000_000}}
        monitor.sticker_signal(sticker, update, s, self._cfg())
        self.assertGreater(s.entropy.mood, 0)                     # V+（既有）
        self.assertGreater(s.entropy.arousal, 0)                  # A+（新：被肯定＝醒）

    def test_affect_sticker_ids_by_quadrant(self):
        s = self._state()
        s.known_sticker_ids = [{"file_id": "POS", "valence": "positive", "ts": 2},
                               {"file_id": "NEU", "valence": "neutral", "ts": 1},
                               {"file_id": "NEG", "valence": "negative", "ts": 1}]
        s.entropy.mood, s.entropy.arousal = 0.4, 0.4              # 興奮開心
        self.assertEqual(monitor._affect_sticker_ids(s, self._cfg())[0], "POS")
        s.entropy.mood, s.entropy.arousal = 0.4, -0.4             # 平靜暖 → sendable（正+中性）
        self.assertIn("NEU", monitor._affect_sticker_ids(s, self._cfg()))
        s.entropy.mood, s.entropy.arousal = -0.4, 0.2             # 低落/緊繃 → help（非正向）
        ids = monitor._affect_sticker_ids(s, self._cfg())
        self.assertNotIn("POS", ids)
        s.entropy.mood, s.entropy.arousal = -0.4, 0.2             # 旗標關＝一維（V<0 → help）同結果但走舊路
        self.assertEqual(monitor._affect_sticker_ids(s, self._cfg(on=False)), ids)

    def test_carryover_roundtrip(self):
        s = self._state()
        s.entropy.mood, s.entropy.arousal = 0.6, -0.4
        s.save()
        s2 = State.load(s.path)
        self.assertAlmostEqual(s2.entropy_carryover["arousal"], -0.4, places=3)


if __name__ == "__main__":
    unittest.main()
