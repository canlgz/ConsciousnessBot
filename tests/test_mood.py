"""Phase 2：情緒效價軸 V＋正向主動出聲＋心情語氣＋重生延續＋體驗關係維度。"""

import os
import tempfile
import unittest
from types import SimpleNamespace

from telegram_monitor import lifeloop, reaction, experience, monitor
from telegram_monitor.state import State

QUIET = (1.0, 8.0, 0.5, 0.4, 0.1, 0.2, 0.0)


class MoodDynamicsTest(unittest.TestCase):
    def _seed(self):
        e = lifeloop.EntropyState()
        lifeloop.entropy_update(e, QUIET, "T0")
        return e

    def test_mood_delta_bumps_and_decays(self):
        e = self._seed()
        lifeloop.entropy_update(e, QUIET, "T0", mood_delta=0.5)
        self.assertAlmostEqual(e.mood, 0.5, places=3)
        prev = e.mood
        for _ in range(5):                              # 沒事件 → 慢慢回中性
            lifeloop.entropy_update(e, QUIET, "T0")
            self.assertLess(e.mood, prev)
            prev = e.mood
        self.assertGreater(e.mood, 0)                   # 還沒回到 0（有慣性）

    def test_loneliness_pulls_mood_down(self):
        e = self._seed()
        e.hunger = 0.9                                  # 久沒人理
        lifeloop.entropy_update(e, QUIET, "T0")
        self.assertLess(e.mood, 0)                      # 心情往下飄

    def test_mood_bounded(self):
        e = self._seed()
        for _ in range(50):
            lifeloop.entropy_update(e, QUIET, "T0", mood_delta=1.0)
        self.assertLessEqual(e.mood, 1.0)


class ReactionMoodTest(unittest.TestCase):
    def test_mood_delta_for(self):
        self.assertGreater(reaction.mood_delta_for("謝謝你陪我"), 0.1)      # 暖意→正
        self.assertGreater(reaction.mood_delta_for("你好厲害"), 0.1)        # 誇讚→正
        self.assertLess(reaction.mood_delta_for("你根本沒有真的感覺"), 0)   # 質疑→負
        self.assertGreater(reaction.mood_delta_for("幫我看看"), 0)          # 一般對話＝被陪伴微正

    def test_mood_tone_hint(self):
        self.assertIn("心情不錯", reaction.mood_tone_hint(0.5))
        self.assertIn("心情有點低", reaction.mood_tone_hint(-0.5))
        self.assertEqual(reaction.mood_tone_hint(0.0), "")


class PositiveSpontaneityTest(unittest.TestCase):
    def _ent(self, hunger, mood, rumin):
        e = lifeloop.EntropyState()
        e.hunger, e.mood, e.self_stims_this_idle, e.prev_ingest = hunger, mood, rumin, "OLD"
        return e

    def test_happy_with_something_to_share_reaches_out(self):
        # 不很餓（0.3）但心情好（0.5）＋醞釀過 2 次 → 正向動機開口
        e = self._ent(0.3, 0.5, 2)
        self.assertTrue(lifeloop.spontaneous_due(e, 10_000, 0, 60, mood=e.mood))

    def test_happy_but_no_rumination_stays_quiet(self):
        e = self._ent(0.3, 0.5, 1)                      # 醞釀不足
        self.assertFalse(lifeloop.spontaneous_due(e, 10_000, 0, 60, mood=e.mood))

    def test_neutral_low_arousal_stays_quiet(self):
        e = self._ent(0.3, 0.1, 5)                      # 不餓、心情平 → 沒動機
        self.assertFalse(lifeloop.spontaneous_due(e, 10_000, 0, 60, mood=e.mood))


class ExperienceRelationalTest(unittest.TestCase):
    def test_six_dim_vector_and_mood_line(self):
        # 自體向量第 6 維＝關係/心情；experience_facts 會講「跟你的來往讓我...」
        warm = type("E", (), {"last": {"center": [0.2, 0.5, 0.5, 0.0, 0.0, 0.9], "heads": 1, "recurrence": 0.5},
                              "prev_center": None, "last_dwell": 0, "lifetime_center": None})()
        self.assertIn("暖", experience.experience_facts(warm, "now"))
        low = type("E", (), {"last": {"center": [0.2, 0.5, 0.5, 0.0, 0.0, 0.1], "heads": 1, "recurrence": 0.5},
                             "prev_center": None, "last_dwell": 0, "lifetime_center": None})()
        self.assertIn("悶", experience.experience_facts(low, "now"))

    def test_dim_change_drops_stale_seed(self):
        # 程式加了關係維度（5→6）：重啟後舊的 5 維種子要被丟掉、不崩
        exp = experience.Experience(summary={"last_center": [0.2, 0.5, 0.5, 0.0, 0.0], "attractors": 3})
        self.assertIsNotNone(exp.prev_center)
        exp.observe([0.2, 0.5, 0.5, 0.0, 0.0, 0.7])     # 6 維
        self.assertIsNone(exp.prev_center)              # 維度不合 → 丟掉舊種子


class RebirthCarryoverTest(unittest.TestCase):
    def test_carryover_persists(self):
        tmp = tempfile.mkdtemp()
        s = State(os.path.join(tmp, "s.json"))
        s.entropy = lifeloop.EntropyState()
        s.entropy.mood, s.entropy.hunger = 0.6, 0.8
        s.save()
        s2 = State.load(os.path.join(tmp, "s.json"))
        self.assertAlmostEqual(s2.entropy_carryover["mood"], 0.6, places=2)
        self.assertAlmostEqual(s2.entropy_carryover["hunger"], 0.8, places=2)


class StickerMoodDeltaTest(unittest.TestCase):
    """貼圖→心情增量集中在 reaction（與 mood_delta_for 並列），幅度由 MOOD_GAIN 統一縮放。"""

    def test_deltas_by_valence(self):
        self.assertGreater(reaction.sticker_mood_delta("positive"), 0)
        self.assertLess(reaction.sticker_mood_delta("negative"), 0)
        self.assertEqual(reaction.sticker_mood_delta("neutral"), 0.0)


class SpontaneousMoodKnobTest(unittest.TestCase):
    """Phase 2 正向出聲門檻：心情門檻 mood_share 與醞釀次數 mood_min_rumin 都可調（旋鈕）。"""

    def _ent(self, mood, rumin, hunger=0.3):
        e = lifeloop.EntropyState()
        e.hunger, e.mood, e.self_stims_this_idle, e.prev_ingest = hunger, mood, rumin, "OLD"
        return e

    def test_custom_mood_share_threshold(self):
        e = self._ent(0.3, 5)                                          # 心情 0.3 < 預設門檻 0.4 → 不出聲
        self.assertFalse(lifeloop.spontaneous_due(e, 10_000, 0, 60, mood=e.mood))
        self.assertTrue(lifeloop.spontaneous_due(e, 10_000, 0, 60, mood=e.mood, mood_share=0.2))  # 放寬到 0.2 → 出聲

    def test_custom_mood_min_rumin(self):
        e = self._ent(0.5, 1)                                          # 醞釀 1 次 < 預設 2 → 不出聲
        self.assertFalse(lifeloop.spontaneous_due(e, 10_000, 0, 60, mood=e.mood))
        self.assertTrue(lifeloop.spontaneous_due(e, 10_000, 0, 60, mood=e.mood, mood_min_rumin=1))


class MoodGainTest(unittest.TestCase):
    """MOOD_GAIN 縮放互動事件對心情 V 的幅度（>1 更敏感）——以貼圖路徑驗證整合。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _state(self):
        s = State(os.path.join(self.tmp, f"s{id(self)}.json"))
        s.owner_folder_id = "F"
        s.entropy = lifeloop.EntropyState()
        return s

    def _client(self):
        c = SimpleNamespace(sent=[], dry_run=False)
        c.send = lambda t: c.sent.append(t) or True
        return c

    def test_gain_scales_sticker_mood_change(self):
        coach = SimpleNamespace(enabled=False)                        # 無教練 → 用樣板 ack，不碰網路
        sticker = {"emoji": "🔥"}
        update = {"message": {"chat": {"id": 1}, "sticker": sticker, "date": 1_700_000_000}}
        s1 = self._state()
        monitor._handle_sticker(sticker, update, coach, s1, self._client(),
                                SimpleNamespace(dry_run=False, mood_gain=1.0))
        s2 = self._state()
        monitor._handle_sticker(sticker, update, coach, s2, self._client(),
                                SimpleNamespace(dry_run=False, mood_gain=2.0))
        self.assertGreater(s1.entropy.mood, 0)
        self.assertAlmostEqual(s2.entropy.mood, 2 * s1.entropy.mood, places=3)   # 幅度 ×2


if __name__ == "__main__":
    unittest.main()
