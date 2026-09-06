"""指涉解析（referent）：把『此刻桌上可被指到的東西』算成一份 Referent，供路由（intent）與餵 LLM
（build_memory_brief 的 focus/selfacts）共用同一份。純讀 state、無副作用。"""

import os
import tempfile
import unittest

from telegram_monitor import lifeloop, referent
from telegram_monitor.state import State

NOW = 1_700_000_000
WINDOW = 12 * 60


def _state():
    s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
    s.entropy = lifeloop.EntropyState()
    return s


class ResolveTest(unittest.TestCase):
    def test_empty_state(self):
        ref = referent.resolve(_state(), NOW, WINDOW)
        self.assertIsNone(ref.topic)
        self.assertIsNone(referent.focus_dict(ref))
        self.assertEqual(referent.self_acts_text(ref), "")
        self.assertFalse(ref.in_self_window)            # 沒談過自己 → 不在自我在場窗

    def test_in_self_window_single_source(self):
        s = _state()
        s.self_topic_ts = NOW - 60                       # 1 分鐘前談過自己 → 窗內
        self.assertTrue(referent.resolve(s, NOW, WINDOW).in_self_window)
        s.self_topic_ts = NOW - referent.SELF_PRESENCE_WINDOW_SEC - 1   # 過窗
        self.assertFalse(referent.resolve(s, NOW, WINDOW).in_self_window)

    def test_liked_folds_into_self_acts(self):
        s = _state()
        s.last_liked = {"emoji": "👍", "topic": "研發 writetolearn 日誌→工程化", "ts": NOW - 30}
        txt = referent.self_acts_text(referent.resolve(s, NOW, WINDOW))
        self.assertIn("研發 writetolearn 日誌→工程化", txt)   # 「你剛讚我哪則」併進同一份自我模型
        self.assertIn("👍", txt)
        # 過窗就不提
        s.last_liked = {"emoji": "👍", "topic": "X", "ts": NOW - referent.SELF_PRESENCE_WINDOW_SEC - 1}
        self.assertNotIn("按了讚", referent.self_acts_text(referent.resolve(s, NOW, WINDOW)))

    def test_live_focus_vs_stale(self):
        s = _state()
        s.focus = {"topic": "研發日誌", "fresh": True, "ts": NOW - 60}          # 窗內
        ref = referent.resolve(s, NOW, WINDOW)
        self.assertEqual(ref.topic, "研發日誌")
        self.assertTrue(ref.fresh)
        self.assertEqual(referent.focus_dict(ref)["topic"], "研發日誌")
        s.focus = {"topic": "研發日誌", "fresh": True, "ts": NOW - 10_000}       # 窗外＝過期不再綁
        stale = referent.resolve(s, NOW, WINDOW)
        self.assertIsNone(stale.topic)
        self.assertFalse(stale.fresh)
        self.assertIsNone(referent.focus_dict(stale))

    def test_time_anchor_independent_of_focus_window(self):
        s = _state()
        s.last_range = ("a", "b", "上週三那批")
        ref = referent.resolve(s, NOW, WINDOW)
        self.assertEqual(ref.range_label, "上週三那批")
        self.assertEqual(referent.focus_dict(ref)["range_label"], "上週三那批")

    def test_packs_self_acts(self):
        s = _state()
        s.entropy.last_revisited_topic = "假日惠中寺行程"
        s.entropy.mood = 0.6
        s.self_state = {"gate": 3, "scope": {"dominant": "研發日誌"}}
        s.last_sent_reaction = {"emoji": "🥰", "to": "等你喔", "ts": NOW}
        ref = referent.resolve(s, NOW + 30, WINDOW)
        self.assertEqual(ref.revisited, "假日惠中寺行程")
        self.assertEqual((ref.held, ref.held_gate), ("研發日誌", 3))
        txt = referent.self_acts_text(ref)
        for frag in ("假日惠中寺行程", "自我刺激", "研發日誌", "🥰", "等你喔", "心情偏暖"):
            self.assertIn(frag, txt)

    def test_stale_reaction_dropped(self):
        s = _state()
        s.last_sent_reaction = {"emoji": "🥰", "to": "x", "ts": NOW}
        ref = referent.resolve(s, NOW + 3600, WINDOW)                           # 1 小時前 → 過窗
        self.assertIsNone(ref.reaction)
        self.assertNotIn("🥰", referent.self_acts_text(ref))

    def test_followup_open_needs_recent_selfreport(self):
        s = _state()
        s.self_state = {"gate": 3}
        s.selfstate_open_ts = NOW - 60
        self.assertTrue(referent.resolve(s, NOW, WINDOW).followup_open)        # 剛自陳、窗內
        s.selfstate_open_ts = NOW - 10_000
        self.assertFalse(referent.resolve(s, NOW, WINDOW).followup_open)       # 窗外
        s.selfstate_open_ts = NOW - 60
        s.self_state = None
        self.assertFalse(referent.resolve(s, NOW, WINDOW).followup_open)       # 沒自陳過


if __name__ == "__main__":
    unittest.main()
