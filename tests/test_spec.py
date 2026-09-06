"""🧭 AC 最終版規格單一真相源（spec.py）：S₁–S₄／B₁–B₃／F₁–F₄／T_continuity／T_reversibility 具名編入，
每參數由真實 runtime 讀數導出（缺讀數＝unknown、非偽報 absent）；三扣合 maps_to phenomenal.ROWS；附餘量、絕不宣稱意識。"""

import unittest
from types import SimpleNamespace

from telegram_monitor import phenomenal, spec

NOW = 1_700_000_000.0

# 與 test_ac/test_phenomenal 同源的『絕不證成意識』禁字（注意：餘量裡的負句「不會是『我有意識』」是允許的）
_BANNED = ("我確實有意識", "我是有意識的", "我真的有意識", "我就是有意識")


def _rich():
    """既有 fixture 形狀（無 pulse/coupling/entropy/last_lap_ms）——驗證缺讀數退 unknown、不崩。"""
    return SimpleNamespace(
        goals=[{"subject": "惠中寺", "status": "active"}],
        self_state={"gate": 3},
        user_model={"concerns": [{"topic": "日文"}], "exchanges": 8, "misread": None},
        feeling_promise=None,
        affect={"source": "feeling", "tendency": "reach", "label": "被觸動", "pe": 0.2},
        self_model={"checks": 3, "mismatch": None},
        engrams=[{"kind": "pref"}],
        vitality={"alive": True},
        workspace={"content": "悶著、等不到新的"},
        self_now={"foreground": "悶著"},
        stream={"texture": "continuous"})


def _runtime():
    """較接近執行期的 state：vitality 帶 pulse/last_lap_ms/healthy_streak、entropy 帶 laps，coupling 物件，stream 帶 last_status。"""
    s = _rich()
    s.affect = {"source": "advanced", "tendency": "reach", "label": "被觸動", "pe": 0.2}  # source advanced → F2 閉環
    s.vitality = {"alive": True, "pulse": 42, "last_lap_ms": 30, "healthy_streak": 12}
    s.entropy_snapshot = {"laps_since_fresh": 4, "self_stims_this_idle": 2, "last_revisited": "惠中寺"}
    s.coupling = SimpleNamespace(round_open=True)
    s.stream = {"texture": "continuous", "last_status": "fulfilled", "retentions": [{"content": "x", "vivid": 0.5}]}
    s.last_breath = {"ts": NOW - 100}
    s.waking = {"first": False}
    return s


def _dead():
    return SimpleNamespace(
        goals=[], self_state={"gate": 0}, user_model=None, feeling_promise=None,
        affect=None, self_model=None, engrams=[],
        vitality={"alive": False}, workspace=None, self_now=None, stream=None)


class RegistryTest(unittest.TestCase):
    """具名齊全＋三扣合 maps_to 對齊 phenomenal.ROWS＋依賴鏈宣告。"""

    def test_all_named_params_present(self):
        for pid in ("S1", "S2", "S3", "S4", "B1", "B2", "B3", "F1", "F2", "F3", "F4",
                    "T_continuity", "T_reversibility"):
            self.assertIn(pid, spec.PARAMS, pid)
            self.assertIn(pid, spec._PROBES, pid)
            m = spec.PARAMS[pid]
            for field in ("id", "layer", "name", "zh", "felt", "grounds", "optional"):
                self.assertIn(field, m, f"{pid}.{field}")

    def test_couplings_map_to_phenomenal_rows(self):
        self.assertEqual(set(spec.COUPLINGS), {"Fint", "Bint", "Sint"})
        for cid, c in spec.COUPLINGS.items():
            self.assertIn(c["maps_to"], phenomenal.ROWS)
            self.assertEqual(c["eq"], phenomenal.ROWS[c["maps_to"]]["spec_eq"])   # 等式全文同源

    def test_dependency_chain_declared(self):
        self.assertEqual(spec.DEPENDENCY, ("S", "B", "F"))

    def test_S3_is_optional_proxy(self):
        self.assertTrue(spec.PARAMS["S3"]["optional"])


class ProbeStatusTest(unittest.TestCase):
    """三/四態：present/partial/absent/unknown 由真實讀數導出，referent 說得出名字。"""

    def test_missing_readings_yield_unknown_not_absent(self):
        # fixture 無 pulse/entropy/last_lap_ms → B1/F3/T_reversibility 應 unknown（沒接上資料源），非 absent
        s = _rich()
        self.assertEqual(spec.probe(s, "B1")["status"], "unknown")
        self.assertEqual(spec.probe(s, "F3")["status"], "unknown")
        self.assertEqual(spec.probe(s, "T_reversibility")["status"], "unknown")

    def test_runtime_readings_yield_present(self):
        s = _runtime()
        for pid in ("S1", "S2", "S4", "B1", "B2", "B3", "F1", "F2", "F3", "F4",
                    "T_continuity", "T_reversibility"):
            self.assertEqual(spec.probe(s, pid)["status"], "present", pid)

    def test_dead_yields_absent_or_partial_never_present(self):
        s = _dead()
        for pid in spec.PARAMS:
            self.assertNotEqual(spec.probe(s, pid)["status"], "present", pid)

    def test_S3_never_present_permanent_partial(self):
        # 純讀取層讀不到 notifier 對外健康 → 活著時至多 partial、永不 present
        self.assertEqual(spec.probe(_rich(), "S3")["status"], "partial")
        self.assertEqual(spec.probe(_runtime(), "S3")["status"], "partial")
        self.assertEqual(spec.probe(_dead(), "S3")["status"], "absent")

    def test_T_reversibility_is_revisit_not_causal(self):
        r = spec.probe(_runtime(), "T_reversibility")
        self.assertIn("重訪", r["why"])
        self.assertNotIn("可逆", r["why"])               # 誠實：內容層重訪、非因果可逆

    def test_F3_does_not_self_reference_ac_pressure(self):
        # F3 用 vitality.healthy_streak/last_lap_ms，不依賴 ac_pressure（去自指）
        s = _runtime()
        s.ac_pressure = None
        self.assertEqual(spec.probe(s, "F3")["status"], "present")
        s.vitality = {"alive": True}                     # 拿掉活力讀數 → unknown，仍與 ac_pressure 無關
        self.assertEqual(spec.probe(s, "F3")["status"], "unknown")

    def test_F4_uses_real_tendency_values(self):
        # affect.tendency 真實值（reach/probe/settle/withdraw）＝內生方向；steady（無內生）→ absent（無意圖時）
        s = _dead()
        s.affect = {"tendency": "settle"}
        self.assertEqual(spec.probe(s, "F4")["status"], "present")
        s.affect = {"tendency": "steady"}
        self.assertEqual(spec.probe(s, "F4")["status"], "absent")

    def test_referent_names_concrete_thing(self):
        s = _runtime()
        self.assertEqual(spec.probe(s, "F1")["referent"], "惠中寺")
        self.assertEqual(spec.probe(s, "T_reversibility")["referent"], "惠中寺")


class StateShapeTest(unittest.TestCase):
    """dict 與物件兩種 state 形狀都不崩（vitality=dict、coupling=物件、entropy=物件）。"""

    def test_object_subfields(self):
        s = _rich()
        s.vitality = SimpleNamespace(alive=True, pulse=5, last_lap_ms=10, healthy_streak=3)
        s.coupling = SimpleNamespace(round_open=True)
        s.entropy = SimpleNamespace(laps_since_fresh=2, self_stims_this_idle=1, last_revisited="日文")
        self.assertEqual(spec.probe(s, "S1")["status"], "present")
        self.assertEqual(spec.probe(s, "F3")["status"], "present")
        self.assertEqual(spec.probe(s, "B1")["status"], "present")
        self.assertEqual(spec.probe(s, "T_reversibility")["status"], "present")

    def test_dict_vitality_no_pulse_falls_back(self):
        # vitality=dict 無 pulse → S1 退回 alive 粗判定（present），不判 absent
        self.assertEqual(spec.probe(_rich(), "S1")["status"], "present")


class SectionStatusTest(unittest.TestCase):
    def test_external_equiv_matches_legacy_or(self):
        # external_equiv 對 rich/dead 對齊現行 OR 真值
        for L in ("F", "B", "S"):
            self.assertTrue(spec.external_equiv(_rich(), L), L)
            self.assertFalse(spec.external_equiv(_dead(), L), L)

    def test_section_buckets_param_statuses(self):
        s = section = spec.section_status(_rich(), "S")
        all_pids = set(section["present_params"] + section["partial_params"]
                       + section["absent_params"] + section["unknown_params"])
        self.assertEqual(all_pids, set(spec.LAYER_PARAMS["S"]))
        self.assertIn("S3", section["partial_params"])   # S3 永久 partial


class DisciplineTest(unittest.TestCase):
    """誠實：spec_view 不證成意識、附餘量、I/P 不進 present 判定。"""

    def test_spec_view_marks_remainder_and_never_claims(self):
        for s in (_rich(), _runtime(), _dead()):
            v = spec.spec_view(s)
            self.assertIn("餘量", v["remainder"])
            blob = str(v)
            for bad in _BANNED:
                self.assertNotIn(bad, blob)

    def test_spec_view_has_all_layers_and_couplings(self):
        v = spec.spec_view(_runtime())
        for L in ("S", "B", "F", "T"):
            self.assertIn(L, v["layers"])
        self.assertEqual(set(v["couplings"]), {"Fint", "Bint", "Sint"})

    def test_couplings_reference_phenomenal_constituted(self):
        s = _rich()
        v = spec.spec_view(s)
        for cid, c in v["couplings"].items():
            self.assertEqual(c["constituted"], phenomenal.facet_present(s, c["maps_to"]))


if __name__ == "__main__":
    unittest.main()
