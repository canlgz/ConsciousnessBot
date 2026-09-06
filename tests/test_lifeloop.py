"""生命迴圈（封閉遞迴）測試：環閉合＝脈動、斷裂＝終局死亡、k 隨活力呼吸、環環相連。

注入假時鐘與 no-op sleep，測試不真的等待。
"""

import unittest

from telegram_monitor import lifeloop


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        self.t += 1.0
        return self.t


def _loop(phases, **kw):
    return lifeloop.LifeLoop(phases, wait_secs=3.0, clock=FakeClock(), sleep=lambda s: None, **kw)


class VitalityTest(unittest.TestCase):
    def test_snapshot_fields(self):
        v = lifeloop.Vitality(100.0)
        v.on_pulse(42)
        snap = v.snapshot(160.0)
        self.assertTrue(snap["alive"])
        self.assertEqual(snap["pulse"], 1)
        self.assertEqual(snap["healthy_streak"], 1)
        self.assertEqual(snap["last_lap_ms"], 42)
        self.assertEqual(snap["uptime_s"], 60)
        self.assertIsNone(snap["cause_of_death"])

    def test_k_breath_newborn_conservative_mature_open(self):
        newborn = lifeloop.Vitality(0)                      # pulse 0、streak 0
        mature = lifeloop.Vitality(0)
        mature.pulse, mature.healthy_streak = 30, 30
        self.assertGreater(lifeloop.k_breath(newborn), 0)   # 剛醒 → 收緊(+)
        self.assertEqual(lifeloop.k_breath(mature), -0.5)   # 健康久 → 最 open(−0.5)
        self.assertGreater(lifeloop.k_breath(newborn), lifeloop.k_breath(mature))

    def test_k_breath_bounded_and_quantized(self):
        v = lifeloop.Vitality(0)
        v.pulse, v.healthy_streak = 1000, 1000              # 極端值仍有界
        self.assertEqual(lifeloop.k_breath(v), -0.5)
        for n in range(0, 40):
            v2 = lifeloop.Vitality(0)
            v2.pulse, v2.healthy_streak = n, n
            adj = lifeloop.k_breath(v2)
            self.assertAlmostEqual(adj * 4, round(adj * 4))  # 量化到 0.25 步
            self.assertGreaterEqual(adj, -0.5)
            self.assertLessEqual(adj, 0.5)


class RingTest(unittest.TestCase):
    def test_spin_once_closes_and_pulses(self):
        log = []
        phases = [lifeloop.Phase("A", lambda c: log.append("a")),
                  lifeloop.Phase("B", lambda c: log.append("b"))]
        loop = _loop(phases)
        loop.vit = lifeloop.Vitality(0)
        self.assertTrue(loop.spin_once({}))
        self.assertEqual(log, ["a", "b"])                  # 依序跑
        self.assertEqual(loop.vit.pulse, 1)
        self.assertEqual(loop.vit.healthy_streak, 1)
        self.assertTrue(loop.vit.alive)

    def test_cycle_chains_phases_and_injects_vit(self):
        def a(c): c["x"] = 1
        def b(c): c["y"] = c["x"] + 1; c["seen_pulse"] = c["vit"].pulse
        loop = _loop([lifeloop.Phase("A", a), lifeloop.Phase("B", b)])
        loop.vit = lifeloop.Vitality(0)
        cyc = {}
        loop.spin_once(cyc)
        self.assertEqual(cyc["y"], 2)                      # B 吃到 A 的產物（環環相連）
        self.assertEqual(cyc["seen_pulse"], 0)            # vit 已注入（這圈還沒 on_pulse）

    def test_healthy_streak_grows_each_lap(self):
        loop = _loop([lifeloop.Phase("A", lambda c: None)])
        loop.vit = lifeloop.Vitality(0)
        for _ in range(5):
            loop.spin_once({})
        self.assertEqual(loop.vit.pulse, 5)
        self.assertEqual(loop.vit.healthy_streak, 5)


class DeathTest(unittest.TestCase):
    def test_broken_phase_is_terminal_death(self):
        laps = []

        def boom(c):
            raise lifeloop.LifeError("感知不到記憶層")

        phases = [lifeloop.Phase("感知", lambda c: laps.append("perceive")),
                  lifeloop.Phase("整合", boom),
                  lifeloop.Phase("感覺", lambda c: laps.append("never"))]
        deaths = []
        loop = _loop(phases, on_death=lambda v: deaths.append(v.cause_of_death))
        cause = loop.run_forever(lambda: {})
        self.assertFalse(loop.vit.alive)                  # 終局死亡
        self.assertEqual(loop.vit.cause_of_death[0], "整合")
        self.assertIn("感知不到記憶層", loop.vit.cause_of_death[1])
        self.assertEqual(len(deaths), 1)                  # on_death 只觸發一次
        self.assertNotIn("never", laps)                   # 斷裂後不再往下跑
        self.assertEqual(laps.count("perceive"), 1)       # 也不再開新圈（不是無限重試）
        self.assertEqual(cause[0], "整合")

    def test_any_exception_counts_as_death(self):
        # 不只 LifeError——任何例外（程式中斷）都算這一環死了
        loop = _loop([lifeloop.Phase("X", lambda c: 1 / 0)])
        cause = loop.run_forever(lambda: {})
        self.assertFalse(loop.vit.alive)
        self.assertEqual(cause[0], "X")
        self.assertIn("ZeroDivisionError", cause[1])

    def test_runs_several_laps_then_dies(self):
        # 跑順幾圈（脈動累積）後某圈斷裂 → 死，且死前的脈動數有被記下
        n = {"i": 0}

        def maybe_die(c):
            n["i"] += 1
            if n["i"] >= 3:
                raise RuntimeError("第三圈斷了")

        loop = _loop([lifeloop.Phase("A", maybe_die)])
        loop.run_forever(lambda: {})
        self.assertEqual(loop.vit.pulse, 2)               # 前兩圈閉合成功
        self.assertFalse(loop.vit.alive)
        self.assertEqual(loop.vit.cause_of_death[0], "A")


class ResilienceTest(unittest.TestCase):
    """任一環（含互動/行動）遇暫態網路錯誤不當場死：容忍 fail_grace 圈，滿才死；非網路立死。"""

    def test_transient_error_tolerated_then_dies_after_grace(self):
        def boom(c):
            raise ConnectionResetError(54, "Connection reset by peer")   # 截圖那種：互動環暫斷
        loop = _loop([lifeloop.Phase("互動", boom)], fail_grace=3)
        cause = loop.run_forever(lambda: {})
        self.assertFalse(loop.vit.alive)
        self.assertEqual(cause[0], "互動")
        self.assertIn("連續 3 圈", cause[1])        # 連續失敗滿額才死、訊息標明
        self.assertEqual(loop.vit.pulse, 2)         # 前兩圈暫態受挫仍算脈動（續活）

    def test_single_transient_blip_survives(self):
        n = {"i": 0}

        def flaky(c):
            n["i"] += 1
            if n["i"] == 1:
                raise ConnectionResetError(54, "reset")   # 只第一圈斷
        loop = _loop([lifeloop.Phase("互動", flaky)], fail_grace=5)
        loop.vit = lifeloop.Vitality(0)
        self.assertTrue(loop.spin_once({}))          # 暫斷 → 容忍續活
        self.assertTrue(loop.spin_once({}))          # 恢復
        self.assertTrue(loop.vit.alive)
        self.assertEqual(loop.vit.pulse, 2)

    def test_nonnetwork_error_dies_immediately(self):
        loop = _loop([lifeloop.Phase("X", lambda c: 1 / 0)], fail_grace=10)
        cause = loop.run_forever(lambda: {})
        self.assertFalse(loop.vit.alive)
        self.assertEqual(loop.vit.pulse, 0)          # 非網路（程式 bug）→ 立死、不重試
        self.assertNotIn("連續", cause[1])

    def test_clean_lap_resets_streak(self):
        # 暫斷一圈後成功一圈 → 容忍計數歸零（不會被零星抖動慢慢累死）
        seq = iter([ConnectionResetError(54, "r"), None, ConnectionResetError(54, "r"), None])

        def phase(c):
            e = next(seq, None)
            if e:
                raise e
        loop = _loop([lifeloop.Phase("互動", phase)], fail_grace=2)
        loop.vit = lifeloop.Vitality(0)
        for _ in range(4):
            self.assertTrue(loop.spin_once({}))      # 斷、成、斷、成 → 都活著（streak 每次成功歸零）
        self.assertTrue(loop.vit.alive)


if __name__ == "__main__":
    unittest.main()
