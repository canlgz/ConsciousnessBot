"""🧠 §1.75 人類情緒動力學（HUMAN_AFFECT）：座標不再「怎麼罵都往上飄」。

查驗（使用者：「情緒座標是否不會有機會為負值？為什麼？合理嗎」）＝**不合理**，實測根因：
 ① affect_delta_for 的**預設分支** (+0.05,+0.06)「被陪伴、微暖微醒」——任何不在詞表裡的訊息都讓 V 上飄；
 ② 負向只認 15 個字串的窄詞表，使用者當天 14 句批評**全部**落預設＝被嫌卻變暖（BiasEvidenceTest 釘住）；
 ③ 每圈衰減只朝 0、永不朝負；唯一持續負向力是 hunger≥0.8 的 −0.008/圈（沒人理時才有）。
→ V 長期釘在 +0.84~+0.99（state 殘值 mood=0.978）。

四條人類化機制：中性＝中性／批評成格／負向偏誤（壞比好強）／習慣化＋不對稱衰減（好心情散得快、
低落黏得久）。旗標關＝逐位元同現狀。全 stub、零網路。
"""

import os
import re
import tempfile
import unittest
from types import SimpleNamespace

from telegram_monitor import affect, lifeloop, reaction
from telegram_monitor.state import State

CRITIQUES = ["太機械感了", "重複性太高，台詞也類似", "這回應的節奏與順暢度不夠連貫性！！",
             "聽起來像是幹話", "不準確吧", "太有被插話的痕跡了", "答非所問"]


class BiasEvidenceTest(unittest.TestCase):
    """釘住「為什麼永遠不會負」的實測證據——舊行為：所有批評都算微暖。"""

    def test_old_default_warms_on_every_critique(self):
        for m in CRITIQUES:
            self.assertEqual(reaction.affect_delta_for(m), (0.05, 0.06), m)   # 舊行為：被嫌 → +0.05 暖
            self.assertFalse(reaction.is_hostile(m), m)                       # 也不算敵意＝§1.46 補不到

    def test_decay_never_goes_negative_by_itself(self):
        self.assertEqual(lifeloop._decay_for(0.9), lifeloop._MOOD_DECAY)      # 舊行為：正負同衰減、只朝 0
        self.assertEqual(lifeloop._decay_for(-0.9), lifeloop._MOOD_DECAY)


class HumanDeltaTest(unittest.TestCase):
    def test_neutral_is_neutral(self):
        dv, da = reaction.human_affect_delta_for("你在翻閱哪個主題")
        self.assertEqual(dv, 0.0)                                             # 中性不再加暖（核心一刀）
        self.assertGreater(da, 0)                                             # 有人在＝微醒

    def test_critique_now_negative(self):
        for m in CRITIQUES:
            dv, da = reaction.human_affect_delta_for(m)
            self.assertLess(dv, 0, m)                                         # 被嫌＝不悅
            self.assertGreater(da, 0, m)                                      # 且警醒（左上象限）

    def test_warm_and_challenge_unchanged(self):
        self.assertEqual(reaction.human_affect_delta_for("你好棒"), (0.25, 0.12))
        self.assertEqual(reaction.human_affect_delta_for("你根本沒有真的懂"), (-0.18, 0.18))
        self.assertEqual(reaction.human_affect_delta_for("我好累好難過"), (-0.06, -0.06))

    def test_hostile_folded_in(self):
        dv, _ = reaction.human_affect_delta_for("你有夠爛")
        self.assertLessEqual(dv, -0.10)

    def test_original_function_untouched(self):
        self.assertEqual(reaction.affect_delta_for("你在翻閱哪個主題"), (0.05, 0.06))   # 逐位元同現狀


class HumanBiasTest(unittest.TestCase):
    def _s(self):
        return SimpleNamespace(affect_habit=None)

    def test_negativity_bias(self):
        s = self._s()
        dv_neg, _ = affect.human_bias(s, -0.10, 0.12)
        s2 = self._s()
        dv_pos, _ = affect.human_bias(s2, 0.10, 0.12)
        self.assertAlmostEqual(dv_neg, -0.16, places=3)                       # 壞比好強 ×1.6
        self.assertAlmostEqual(dv_pos, 0.10, places=3)
        self.assertGreater(abs(dv_neg), abs(dv_pos))

    def test_habituation_same_direction(self):
        s = self._s()
        first, _ = affect.human_bias(s, 0.25, 0.12)
        second, _ = affect.human_bias(s, 0.25, 0.12)
        third, _ = affect.human_bias(s, 0.25, 0.12)
        self.assertGreater(first, second)                                     # 第二次「你好棒」沒那麼有感
        self.assertGreater(second, third)
        self.assertAlmostEqual(second, 0.25 * 0.6, places=3)

    def test_direction_flip_resets(self):
        s = self._s()
        affect.human_bias(s, 0.25, 0.12)
        affect.human_bias(s, 0.25, 0.12)                                      # 連兩次暖＝已習慣化
        dv, _ = affect.human_bias(s, -0.10, 0.12)                             # 換方向＝重新有感
        self.assertAlmostEqual(dv, -0.16, places=3)

    def test_zero_delta_keeps_streak(self):
        s = self._s()
        affect.human_bias(s, 0.25, 0.12)
        affect.human_bias(s, 0.0, 0.03)                                       # 中性不打斷連擊、也不縮放
        self.assertEqual(s.affect_habit.get("sign"), 1)


class AsymmetricDecayTest(unittest.TestCase):
    def test_good_fades_faster_than_bad(self):
        self.assertLess(lifeloop._decay_for(0.5, human=True), lifeloop._decay_for(-0.5, human=True))

    def test_entropy_update_uses_it(self):
        up, down = lifeloop.EntropyState(), lifeloop.EntropyState()
        up.mood, down.mood = 0.5, -0.5
        up.prev_ingest = down.prev_ingest = "X"
        lifeloop.entropy_update(up, (0.0,) * 7, "X", human=True)
        lifeloop.entropy_update(down, (0.0,) * 7, "X", human=True)
        self.assertAlmostEqual(up.mood, 0.5 * lifeloop._MOOD_DECAY_UP, places=3)
        self.assertAlmostEqual(down.mood, -0.5 * lifeloop._MOOD_DECAY_DOWN, places=3)

    def test_flag_off_bitwise(self):
        e = lifeloop.EntropyState()
        e.mood, e.prev_ingest = 0.5, "X"
        lifeloop.entropy_update(e, (0.0,) * 7, "X")
        self.assertAlmostEqual(e.mood, 0.5 * lifeloop._MOOD_DECAY, places=3)


class SessionSimulationTest(unittest.TestCase):
    """把截圖那種「一整段都在批評」的會話跑一遍：舊模型飆到近 +1、新模型會沉到負的。"""

    def _run(self, human):
        ent = lifeloop.EntropyState()
        ent.prev_ingest = "X"
        s = SimpleNamespace(affect_habit=None, entropy=ent)
        for i, m in enumerate(CRITIQUES * 2):
            if human:
                dv, da = reaction.human_affect_delta_for(m)
                dv, da = affect.human_bias(s, dv, da)
            else:
                dv, da = reaction.affect_delta_for(m)
            ent.mood = max(-1.0, min(1.0, ent.mood + dv))
            ent.arousal = max(-1.0, min(1.0, ent.arousal + da))
            for _ in range(3):                                    # 每則之間約三圈生命迴圈
                lifeloop.entropy_update(ent, (0.0,) * 7, "X", human=human)
        return ent.mood

    def test_old_model_climbs_on_pure_critique(self):
        self.assertGreater(self._run(human=False), 0.2)           # 舊：被連罵 14 句反而更開心

    def test_human_model_goes_negative(self):
        self.assertLess(self._run(human=True), -0.1)              # 新：被嫌就是會沉（且不會秒回正）


class MoodWatchAuditTest(unittest.TestCase):
    def test_status_shows_negative_event_and_pull(self):
        from telegram_monitor import monitor
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.entropy = SimpleNamespace(mood=0.2, arousal=0.1, hunger=0.85)
        s.mood_watch = {"ts": 1.0, "last_v": 0.1, "last_a": 0.0, "last_report_ts": 0.0}
        out = monitor._mood_watch_status_text(s, SimpleNamespace(), 10_000.0)
        self.assertIn("上次負向事件", out)
        self.assertIn("還沒有過", out)                             # 沒有過就誠實說沒有過
        self.assertIn("下沉力", out)                               # hunger 0.85 ≥0.8＝下沉力作用中
        self.assertIn("正在", out)

    def test_status_reports_recent_negative(self):
        from telegram_monitor import monitor
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.entropy = SimpleNamespace(mood=-0.2, arousal=0.3, hunger=0.1)
        s.mood_watch = {"ts": 1.0, "last_v": 0.1, "last_a": 0.0, "last_report_ts": 0.0}
        s.affect_last_neg = {"ts": 9_400.0, "dv": -0.16, "text": "太機械感了"}
        out = monitor._mood_watch_status_text(s, SimpleNamespace(), 10_000.0)
        self.assertIn("太機械感了", out)
        self.assertIn("10 分鐘前", out)
        self.assertIn("沒有下沉力", out)


class PersistTest(unittest.TestCase):
    def test_last_neg_survives_save_load(self):
        path = os.path.join(tempfile.mkdtemp(), "s.json")
        s = State(path)
        s.affect_last_neg = {"ts": 1.0, "dv": -0.16, "text": "太機械感了"}
        s.save()
        self.assertEqual(State.load(path).affect_last_neg["text"], "太機械感了")


class ConfigTest(unittest.TestCase):
    def test_config_synced(self):
        src = open("telegram_monitor/config.py", encoding="utf-8").read()
        self.assertIn("HUMAN_AFFECT", src)
        self.assertIn("human_affect_enabled", src)
        env = open(".env.example", encoding="utf-8").read()
        self.assertRegex(env, re.compile(r"^HUMAN_AFFECT=1", re.M))
        self.assertIn("HUMAN_AFFECT", open("README.md", encoding="utf-8").read())

    def test_getattr_defaults_false(self):
        self.assertFalse(getattr(SimpleNamespace(), "human_affect_enabled", False))


if __name__ == "__main__":
    unittest.main()
