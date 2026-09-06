"""內在熵 S 測試（純核心）：電量遇變化噴高/安靜衰減、飢餓冷清成長/新進釋放、S 有界、S→k 量化收緊。"""

import random
import unittest

from telegram_monitor import lifeloop

QUIET = (1.0, 8.0, 0.5, 0.4, 0.1, 0.2, 0.0)
BIG = (4.0, 20.0, 2.5, 0.9, 0.6, 0.9, 0.5)


def _seed(sig=QUIET, ingest="T0"):
    e = lifeloop.EntropyState()
    lifeloop.entropy_update(e, sig, ingest)        # 第一圈：種下 prev_sig/prev_ingest（平靜）
    return e


class EntropyTest(unittest.TestCase):
    def test_first_lap_is_calm(self):
        e = lifeloop.EntropyState()
        adj = lifeloop.entropy_update(e, QUIET, "T0")
        self.assertEqual(e.charge, 0.0)            # 沒有 prev → 變化 0
        self.assertEqual(e.hunger, 0.0)            # 第一圈不成長
        self.assertEqual(adj, 0.0)

    def test_charge_spikes_on_change(self):
        e = _seed()
        lifeloop.entropy_update(e, BIG, "T1")      # 大變化＋新 ingest
        self.assertGreater(e.charge, 0.3)

    def test_charge_decays_on_quiet_laps(self):
        e = _seed()
        lifeloop.entropy_update(e, BIG, "T1")      # 噴高
        peak, prev = e.charge, e.charge
        for _ in range(4):
            lifeloop.entropy_update(e, BIG, "T1")  # 同訊號、同 ingest → 只衰減
            self.assertLess(e.charge, prev)        # 嚴格遞減
            prev = e.charge
        self.assertLess(e.charge, peak)

    def test_hunger_grows_when_no_fresh_ingest(self):
        e = _seed()
        prev = e.hunger
        for i in range(1, 6):
            lifeloop.entropy_update(e, QUIET, "T0")    # 同 ingest（冷清）
            self.assertGreater(e.hunger, prev)
            self.assertEqual(e.laps_since_fresh, i)
            prev = e.hunger

    def test_hunger_discharges_on_fresh_ingest(self):
        e = _seed()
        for _ in range(8):
            lifeloop.entropy_update(e, QUIET, "T0")    # 累積飢餓
        hungry = e.hunger
        self.assertGreater(hungry, 0.0)
        lifeloop.entropy_update(e, QUIET, "T1")        # 新 ingest → 釋放
        self.assertLess(e.hunger, hungry)
        self.assertEqual(e.laps_since_fresh, 0)

    def test_S_bounded(self):
        e = lifeloop.EntropyState()
        rng = random.Random(0)
        for i in range(200):
            sig = tuple(rng.uniform(-5, 30) for _ in range(7))
            lifeloop.entropy_update(e, sig, f"T{i % 3}")
            self.assertGreaterEqual(e.S(), 0.0)
            self.assertLessEqual(e.S(), 1.0)

    def test_k_adj_quantized_signed_monotonic(self):
        self.assertEqual(lifeloop.entropy_k_adj(0.0), 0.0)
        self.assertEqual(lifeloop.entropy_k_adj(1.0), 0.5)     # 高 S → +0.5（收緊）
        self.assertEqual(lifeloop.entropy_k_adj(0.5), 0.25)
        prev = -1
        for i in range(11):
            v = lifeloop.entropy_k_adj(i / 10)
            self.assertIn(v, (0.0, 0.25, 0.5))                # 量化三檔
            self.assertGreaterEqual(v, prev)                  # 單調不減、非負
            prev = v

    def test_frequent_vs_sparse_reads(self):
        # 讀取頻率：頻繁新讀入 → 飢餓壓低；冷清 → 飢餓高
        fresh = _seed()
        for i in range(1, 11):
            lifeloop.entropy_update(fresh, (1.0, 8.0 + i, 0.5, 0.4, 0.1, 0.2, 0.0), f"F{i}")
        sparse = _seed()
        for _ in range(10):
            lifeloop.entropy_update(sparse, QUIET, "T0")
        self.assertLess(fresh.hunger, sparse.hunger)
        self.assertEqual(fresh.laps_since_fresh, 0)
        self.assertGreater(sparse.laps_since_fresh, 5)

    def test_ext_perturb_raises_charge(self):
        # 外在擾動（如對話時機：交錯/久別/回得慢）與記寫變化同管道注入電量 C
        base = _seed()
        lifeloop.entropy_update(base, QUIET, "T0")                       # 安靜：只衰減 → charge 0
        perturbed = _seed()
        lifeloop.entropy_update(perturbed, QUIET, "T0", ext_perturb=0.35)
        self.assertGreater(perturbed.charge, base.charge)
        self.assertAlmostEqual(perturbed.charge, 0.35, places=6)

    def test_snapshot_fields(self):
        e = _seed()
        snap = e.snapshot()
        self.assertEqual(set(snap), {"charge", "hunger", "S", "laps_since_fresh",
                                     "last_revisited", "self_stims_this_idle", "mood", "arousal"})   # 🧭 circumplex A 軸


if __name__ == "__main__":
    unittest.main()
