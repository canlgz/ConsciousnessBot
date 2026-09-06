"""主觀體驗向量擴維（Wave 3）：把環境活絡度／晝夜／關係張力半權折進自體向量＝五維在「活成的形狀」交會。
前六維不變（向後相容）；EXPERIENCE_RICH_VEC=0 退回六維；吸子仍成形；內省說得出新維度。"""

import os
import random
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from telegram_monitor import experience, lifeloop, monitor
from telegram_monitor.state import State

CYCLE = {"now": datetime(2026, 6, 21, 14, tzinfo=timezone.utc)}


def _state(**kw):
    s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
    s.entropy = lifeloop.EntropyState()
    s.k_breath_adj = 0.0
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def _cfg(rich=True):
    return SimpleNamespace(experience_rich_vec=rich, timezone="Asia/Taipei")


class ExperienceVecTest(unittest.TestCase):
    def test_rich_is_ten_dim_plain_is_six_prefix_identical(self):
        s = _state(env_activity=0.8)
        rich = monitor._experience_vec(s, _cfg(True), CYCLE, s.entropy, False)
        plain = monitor._experience_vec(s, _cfg(False), CYCLE, s.entropy, False)
        self.assertEqual(len(rich), 10)
        self.assertEqual(len(plain), 6)
        self.assertEqual(rich[:6], plain)                       # 前六維 byte-for-byte 不變（向後相容）

    def test_environment_folds_in_half_weight(self):
        hi = monitor._experience_vec(_state(env_activity=1.0), _cfg(), CYCLE, None, False)
        lo = monitor._experience_vec(_state(env_activity=0.0), _cfg(), CYCLE, None, False)
        self.assertGreater(hi[6], lo[6])
        self.assertAlmostEqual(hi[6], 0.5)                      # 半權上限
        self.assertEqual(lo[6], 0.0)

    def test_relationship_folds_in(self):
        s = _state()
        s.coupling = SimpleNamespace(i_bot=0.9, i_user=0.3)
        v = monitor._experience_vec(s, _cfg(), CYCLE, s.entropy, False)
        self.assertAlmostEqual(v[9], 0.45, places=3)           # 0.5 × max(0.9,0.3)

    def test_no_coupling_zero_relationship(self):
        s = _state()
        s.coupling = None
        v = monitor._experience_vec(s, _cfg(), CYCLE, s.entropy, False)
        self.assertEqual(v[9], 0.0)

    def test_circadian_dims_in_half_weight_range(self):
        v = monitor._experience_vec(_state(), _cfg(), CYCLE, None, False)
        for i in (7, 8):
            self.assertGreaterEqual(v[i], 0.0)
            self.assertLessEqual(v[i], 0.5)

    def test_circadian_differs_by_time_of_day(self):
        day = monitor._experience_vec(_state(), _cfg(), {"now": datetime(2026, 6, 21, 6, tzinfo=timezone.utc)},
                                      None, False)
        night = monitor._experience_vec(_state(), _cfg(), {"now": datetime(2026, 6, 21, 18, tzinfo=timezone.utc)},
                                        None, False)
        self.assertNotEqual((day[7], day[8]), (night[7], night[8]))   # 晝夜真的進了幾何


class FormationAndIntrospectionTest(unittest.TestCase):
    def _formed(self, center, n=120, jit=0.02, seed=0):
        rng = random.Random(seed)
        exp = experience.Experience()
        for _ in range(n):
            exp.observe(tuple(min(1.0, max(0.0, x + rng.uniform(-jit, jit))) for x in center))
        return exp

    def test_ten_dim_attractor_still_forms(self):
        # 十維、近群聚 → 仍要能收束成吸子（半權新維度不該擋住成形）
        center = (0.2, 0.2, 0.5, 0.0, 0.5, 0.7, 0.45, 0.25, 0.25, 0.45)
        exp = self._formed(center)
        self.assertTrue(exp.formed)
        self.assertEqual(len(exp.last["center"]), 10)

    def test_facts_narrate_environment_and_relationship(self):
        # 高環境活絡(c6=.45→.9)＋高關係(c9=.45→.9) → 內省說得出
        exp = self._formed((0.2, 0.2, 0.5, 0.0, 0.5, 0.7, 0.45, 0.25, 0.25, 0.45))
        facts = experience.experience_facts(exp, "now")
        self.assertIn("熱鬧", facts)
        self.assertIn("黏", facts)
        self.assertIn("熱鬧", experience.experience_preview(exp))

    def test_six_dim_facts_still_work(self):
        # 向後相容：六維向量的內省不受影響、也不誤觸新維度敘述
        exp = self._formed((0.2, 0.95, 0.5, 0.0, 0.0, 0.3))
        facts = experience.experience_facts(exp, "now")
        self.assertIn("餓", facts)
        self.assertNotIn("周遭", facts)


if __name__ == "__main__":
    unittest.main()
