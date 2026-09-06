"""🔄 §1.05 蛻變自陳兌現：「N分鐘後告訴我你有什麼改變」到點真的說出改變（before→after diff），不再空喊。"""

import os
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from telegram_monitor import lifeloop, monitor, selfchange
from telegram_monitor.state import State


def _st(mood=0.0, arousal=0.0, hunger=0.0, gate=0, goals=None):
    ent = lifeloop.EntropyState()
    ent.mood, ent.arousal, ent.hunger = mood, arousal, hunger
    return SimpleNamespace(entropy=ent, gate_confirmed=gate, goals=goals or [])


class SnapshotDiffTest(unittest.TestCase):
    def test_snapshot_reads_state(self):
        s = _st(0.35, -0.25, 0.3, 2, [{"status": "active", "subject": "讀經", "progress": 0.2}])
        snap = selfchange.snapshot(s)
        self.assertEqual(snap["region"], "平靜、安穩")
        self.assertEqual(snap["gate"], 2)
        self.assertEqual(snap["goals"], {"讀經": 0.2})

    def test_is_change_behavior(self):
        self.assertTrue(selfchange.is_change_behavior("跟他說說我此刻有什麼不一樣（我的變化）"))
        self.assertFalse(selfchange.is_change_behavior("跟他問候"))
        self.assertFalse(selfchange.is_change_behavior(""))

    def test_diff_region_jump(self):
        base = selfchange.snapshot(_st(0.35, -0.25))
        now = _st(-0.2, 0.3)
        bits = selfchange.diff_facts(base, now)
        self.assertTrue(any("平靜、安穩" in b and "緊繃、煩躁" in b for b in bits))

    def test_diff_hunger_gate_goals(self):
        base = selfchange.snapshot(_st(hunger=0.3, gate=2, goals=[{"status": "active", "subject": "X", "progress": 0.2}]))
        now = _st(hunger=0.55, gate=3, goals=[{"status": "active", "subject": "X", "progress": 0.35}])
        bits = "／".join(selfchange.diff_facts(base, now))
        self.assertIn("更悶", bits)
        self.assertIn("Gate2→Gate3", bits)
        self.assertIn("20%→35%", bits)

    def test_new_and_dropped_goal(self):
        base = selfchange.snapshot(_st(goals=[{"status": "active", "subject": "舊", "progress": 0.3}]))
        now = _st(goals=[{"status": "active", "subject": "新", "progress": 0.1}])
        bits = "／".join(selfchange.diff_facts(base, now))
        self.assertIn("冒出一個想弄懂你「新」", bits)
        self.assertIn("「舊」那條，這會兒算是放下了", bits)

    def test_no_change_is_honest(self):
        s = _st(0.1, 0.05)
        base = selfchange.snapshot(s)
        g = selfchange.change_ground(base, s)
        self.assertIn("沒什麼大動靜", g)
        self.assertIn("別硬編", g)

    def test_change_ground_lists_facts(self):
        base = selfchange.snapshot(_st(0.35, -0.25))
        g = selfchange.change_ground(base, _st(-0.2, 0.3))
        self.assertIn("系統指標的前後差分", g)
        self.assertIn("不能宣稱已成長", g)

    def test_no_baseline_empty(self):
        self.assertEqual(selfchange.change_ground(None, _st()), "")
        self.assertEqual(selfchange.diff_facts(None, _st()), [])


class PromiseFulfillmentTest(unittest.TestCase):
    """整合：蛻變承諾成立→存 baseline；到點 _promise_keep_body 把真實變化餵進 voice。"""

    NOW = datetime(2026, 7, 9, 10, 30, tzinfo=timezone.utc)

    def _state(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        s.entropy = lifeloop.EntropyState()
        s.entropy.mood, s.entropy.arousal, s.entropy.hunger = 0.35, -0.25, 0.3
        s.gate_confirmed = 2
        s.goals = [{"status": "active", "subject": "讀誦經書", "progress": 0.2}]
        return s

    def _cfg(self, **kw):
        base = dict(dry_run=False, self_change_ground_enabled=True, promise_act_aligned=True)
        base.update(kw)
        return SimpleNamespace(**base)

    def test_baseline_snapshot_and_ground(self):
        s = self._state()
        base = selfchange.snapshot(s)
        p = {"behavior": "跟他說說我此刻有什麼不一樣（我的變化）", "change_baseline": base,
             "target_ts": self.NOW.timestamp() - 30}
        # 40 分鐘後狀態變了
        s.entropy.mood, s.entropy.arousal, s.entropy.hunger = -0.2, 0.3, 0.55
        s.gate_confirmed = 3
        s.goals[0]["progress"] = 0.35
        cap = {}
        coach = SimpleNamespace(enabled=True, _schedule_time_exact=True,
                                voice_promise_keep=lambda *a, **k: cap.update(k) or "（兌現句）")
        monitor._promise_keep_body(s, self._cfg(), coach, p, self.NOW, timezone.utc, False)
        cg = cap.get("change_ground", "")
        self.assertIn("緊繃、煩躁", cg)                          # 真實情緒座標移動
        self.assertIn("Gate2→Gate3", cg)
        self.assertIn("20%→35%", cg)

    def test_flag_off_no_ground(self):
        s = self._state()
        p = {"behavior": "跟他說說我此刻有什麼不一樣（我的變化）",
             "change_baseline": selfchange.snapshot(s), "target_ts": self.NOW.timestamp() - 30}
        cap = {}
        coach = SimpleNamespace(enabled=True, _schedule_time_exact=True,
                                voice_promise_keep=lambda *a, **k: cap.update(k) or "x")
        monitor._promise_keep_body(s, self._cfg(self_change_ground_enabled=False), coach, p, self.NOW, timezone.utc, False)
        self.assertEqual(cap.get("change_ground", ""), "")      # 旗標關＝不算差異＝逐位元同現狀


if __name__ == "__main__":
    unittest.main()
