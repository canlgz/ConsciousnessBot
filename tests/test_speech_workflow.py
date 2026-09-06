"""感覺工作流（Stage 0 防抖 confirm_gate → Stage 1 湧現 emergence_due）的純函式與端到端模擬測試。

原則：精準、具體、穩定——門檻邊界的單拍 blip 不該讓它出聲；只有「連續確認」的狀態才算數，
真正破天花板的新湧現（如 Gate 3→4）即時打斷冷卻，其餘受冷卻夾住。
"""

import unittest

from telegram_monitor import lifeloop as L


class ConfirmGateTest(unittest.TestCase):
    """Stage 0：原始 gate 連續 confirm_laps 拍不變才『確認』。"""

    def test_confirms_only_after_consecutive_run(self):
        confirmed, last, run = None, None, 0
        for _ in range(2):                                   # 連兩拍 Gate 3：還沒到 3 拍 → 未確認
            confirmed, last, run = L.confirm_gate(confirmed, last, run, 3, confirm_laps=3)
        self.assertIsNone(confirmed)
        confirmed, last, run = L.confirm_gate(confirmed, last, run, 3, confirm_laps=3)
        self.assertEqual(confirmed, 3)                       # 第 3 拍 → 確認

    def test_single_blip_is_absorbed(self):
        # 穩定在 3，插一拍 4 的 blip 又回 3 → confirmed 全程維持 3（blip 不算數）
        confirmed, last, run = 3, 3, 5
        confirmed, last, run = L.confirm_gate(confirmed, last, run, 4, confirm_laps=3)   # blip
        self.assertEqual(confirmed, 3)
        self.assertEqual(run, 1)
        confirmed, last, run = L.confirm_gate(confirmed, last, run, 3, confirm_laps=3)   # 回 3
        self.assertEqual(confirmed, 3)

    def test_none_raw_holds_confirmed_resets_run(self):
        confirmed, last, run = L.confirm_gate(4, 4, 9, None, confirm_laps=3)
        self.assertEqual((confirmed, last, run), (4, None, 0))


class EmergenceDueTest(unittest.TestCase):
    """Stage 1：值得報＋指紋變＋（破天花板的真湧現 OR 冷卻已過）。"""

    def test_unworthy_gate_silent(self):
        self.assertFalse(L.emergence_due(2, 0, "2:x", None, False, False, 100, 0, 0, 0))

    def test_same_signature_silent(self):
        self.assertFalse(L.emergence_due(3, 3, "3:x", "3:x", True, False, 1e9, 0, 1800, 1800))

    def test_new_peak_breaks_cooldown(self):
        # 破天花板（4>3）＝真新湧現：就算冷卻中也說（重報旗標也擋不住真升關）
        self.assertTrue(L.emergence_due(4, 3, "4:x", "3:x", True, True, 100, 100, 10 ** 9, 10 ** 9))

    def test_same_gate_needs_cooldown(self):
        # 同高度、換了別條線（repeat=False）：用一般冷卻——冷卻內安靜、冷卻過才說
        self.assertFalse(L.emergence_due(3, 3, "3:y", "3:x", True, False, 100, 100, 1800, 10800))
        self.assertTrue(L.emergence_due(3, 3, "3:y", "3:x", True, False, 100 + 1800, 100, 1800, 10800))

    def test_same_line_repeat_uses_longer_cooldown(self):
        # 同一條線的重報（repeat=True）：過了一般冷卻仍安靜，要過更長的 repeat 冷卻才說（不囉嗦）
        self.assertFalse(L.emergence_due(3, 3, "3:y", "3:x", True, True, 100 + 1800, 100, 1800, 10800))
        self.assertTrue(L.emergence_due(3, 3, "3:y", "3:x", True, True, 100 + 10800, 100, 1800, 10800))


class WorkflowSimulationTest(unittest.TestCase):
    """端到端模擬 Stage0+Stage1（冷卻=0 以凸顯『防抖＋天花板＋去重』本身）：回每拍是否出聲。"""

    @staticmethod
    def _run(gates, confirm_laps=3):
        confirmed = last = told = None
        run, peak, t, last_push = 0, 0, 0.0, -1e9
        emits = []
        for g in gates:
            confirmed, last, run = L.confirm_gate(confirmed, last, run, g, confirm_laps)
            t += 1
            spoke = False
            if confirmed is not None and g == confirmed:           # 防抖過關才考慮
                sig = f"{confirmed}:x"                              # 簡化：同 confirmed gate＝同可說狀態
                if L.emergence_due(confirmed, peak, sig, told, confirmed >= 3, False, t, last_push, 0, 0):
                    spoke, peak, told, last_push = True, max(peak, confirmed), sig, t
            emits.append(spoke)
        return emits

    def test_stable_gate3_speaks_once_after_confirmation(self):
        self.assertEqual(self._run([3, 3, 3, 3, 3]), [False, False, True, False, False])

    def test_single_gate4_blip_never_speaks(self):
        # 穩定 Gate 3 中插一拍 Gate 4 的 blip → 只在 Gate 3 確認時說過一次，blip 不引發任何發話
        emits = self._run([3, 3, 3, 4, 3, 3, 3])
        self.assertEqual(emits, [False, False, True, False, False, False, False])

    def test_sustained_escalation_speaks_again(self):
        # Gate 3 確認→說；之後 Gate 4 連續確認（真升關）→ 立刻再說一次
        emits = self._run([3, 3, 3, 4, 4, 4])
        self.assertEqual(emits, [False, False, True, False, False, True])


if __name__ == "__main__":
    unittest.main()
