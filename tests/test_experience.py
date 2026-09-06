"""主觀體驗（自體軌跡 → 奇異吸子 → 輪廓湧現）：experience.* 與 monitor._experience_step。

自體向量＝(電量C, 飢餓H, 開放度k, 出聲, 自我刺激)——純粹 bot 自己的狀態與動作（不含記寫衍生的 gate）。
要點：隨機離散不成形；繞行收束→成形一次；成形後主葉位移→轉變一次；詮釋帶時間性/趨勢；長期摘要可跨重啟。
"""

import os
import random
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from telegram_monitor import experience as X
from telegram_monitor import monitor
from telegram_monitor.state import State

NOW = datetime(2026, 6, 17, 12, 0, 0, tzinfo=timezone.utc)
A = (0.2, 0.2, 0.5, 0.0, 0.5)     # 一種活法（不餓、靜、常自我刺激）
B = (0.2, 0.95, 0.5, 0.0, 0.0)    # 另一種活法（很餓、少自我刺激）→ 與 A 主葉明顯不同


def _near(center, rng, jit=0.05):
    return tuple(min(1.0, max(0.0, c + rng.uniform(-jit, jit))) for c in center)


class FakeClient:
    def __init__(self, dry_run=False):
        self.sent, self.dry_run = [], dry_run

    def send(self, text):
        self.sent.append(text)
        return True


class AttractorTest(unittest.TestCase):
    def test_scatter_is_not_formed(self):
        rng = random.Random(1)
        trail = [tuple(rng.random() for _ in range(5)) for _ in range(80)]
        d = X.attractor(trail)
        self.assertFalse(d["formed_ready"])
        self.assertEqual(d["stage"], "離散漂移")
        self.assertLess(d["recurrence"], X._R_MIN)

    def test_tight_orbit_is_formed(self):
        rng = random.Random(2)
        trail = [_near(A, rng) for _ in range(80)]
        d = X.attractor(trail)
        self.assertTrue(d["formed_ready"])
        self.assertEqual(d["stage"], "吸子成形")
        self.assertEqual(d["heads"], 1)

    def test_wide_two_lobe_forms_by_within_lobe_tightness(self):
        # 兩個緊葉、相距很寬（整體半徑大）；但葉內緊度小 → 仍算收束（極限環），門檻自適應放大
        rng = random.Random(12)
        L1, L2 = (0.12, 0.12, 0.5, 0.0, 0.0), (0.88, 0.88, 0.5, 0.0, 0.0)
        trail = [_near(L1 if i % 2 == 0 else L2, rng, jit=0.03) for i in range(80)]
        d = X.attractor(trail)
        self.assertTrue(d["formed_ready"])
        self.assertEqual(d["heads"], 2)
        self.assertGreater(d["radius"], 0.4)      # 整體半徑大（葉距寬）
        self.assertLess(d["within"], 0.1)         # 但葉內很緊

    def test_scale_invariant_decision(self):
        # 同形狀的 2-葉環，整體放大 → 成形決策一致（門檻相對自身散布自適應）
        def formed(scale):
            rng = random.Random(13)
            b = 0.3 * scale
            L1, L2 = (0.5 - b, 0.5, 0.5, 0, 0), (0.5 + b, 0.5, 0.5, 0, 0)
            trail = [_near(L1 if i % 2 == 0 else L2, rng, jit=0.02 * scale) for i in range(80)]
            return X.attractor(trail)["formed_ready"]
        self.assertTrue(formed(1.0))
        self.assertTrue(formed(1.5))              # 放大 1.5×，仍判定成形（尺度不變）


class ObserveTest(unittest.TestCase):
    def test_scatter_never_forms(self):
        rng = random.Random(3)
        exp = X.Experience()
        events = [exp.observe(tuple(rng.random() for _ in range(5))) for _ in range(120)]
        self.assertEqual([e for e in events if e], [])
        self.assertFalse(exp.formed)

    def test_orbit_forms_exactly_once(self):
        rng = random.Random(4)
        exp = X.Experience()
        events = [exp.observe(_near(A, rng)) for _ in range(120)]
        self.assertEqual([e for e in events if e], ["formed"])
        self.assertTrue(exp.formed)
        self.assertEqual(exp.attractors, 1)

    def test_relocation_triggers_shift(self):
        rng = random.Random(5)
        exp = X.Experience()
        for _ in range(120):
            exp.observe(_near(A, rng))
        ev = [e for _ in range(150) for e in [exp.observe(_near(B, rng))] if e]
        self.assertEqual(ev, ["shift"])
        self.assertEqual(exp.attractors, 2)


class SummaryAndTrendTest(unittest.TestCase):
    """長期摘要可跨重啟；詮釋帶趨勢（跟上一個活法比）。"""

    def _formed(self, center, summary=None, seed=6):
        rng = random.Random(seed)
        exp = X.Experience(summary=summary)
        for _ in range(120):
            exp.observe(_near(center, rng))
        return exp

    def test_summary_roundtrips(self):
        exp = self._formed(A)
        s = exp.summary()
        self.assertEqual(s["attractors"], 1)
        self.assertEqual(len(s["lifetime_center"]), 5)
        self.assertEqual(len(s["last_center"]), 5)

    def test_shift_facts_have_trend(self):
        # A（不餓）→ B（很餓）的轉變：事實要出現「比起之前，我更餓了」
        rng = random.Random(7)
        exp = X.Experience()
        for _ in range(120):
            exp.observe(_near(A, rng))
        for _ in range(150):
            exp.observe(_near(B, rng))
        facts = X.experience_facts(exp, "shift")
        self.assertIn("比起之前，我更餓了", facts)
        for forbidden in ("半徑", "吸子", "軌跡"):
            self.assertNotIn(forbidden, facts)

    def test_cross_restart_seeds_trend(self):
        # 重啟前停在 A（不餓）；用其摘要種下新 Experience，新生收束到 B（很餓）→ 首次成形就能跟重啟前比趨勢
        before = self._formed(A)
        after = self._formed(B, summary=before.summary(), seed=8)
        self.assertEqual(after.attractors, 2)                 # 跨重啟累計
        self.assertEqual(after.prev_center, tuple(before.summary()["last_center"]))
        self.assertIn("比起之前，我更餓了", X.experience_facts(after, "formed"))

    def test_preview_substance_and_too_few(self):
        self.assertEqual(X.experience_preview(X.Experience()), "還在累積（點還太少）")
        prev = X.experience_preview(self._formed(B))           # B 很餓
        self.assertNotIn("\n", prev)
        self.assertIn("餓著等新的", prev)

    def test_experience_text_template(self):
        self.assertIn("活法", X.experience_text("formed"))
        self.assertIn("另一段", X.experience_text("shift"))


class RenderTest(unittest.TestCase):
    def _formed(self):
        rng = random.Random(9)
        exp = X.Experience()
        for _ in range(90):
            exp.observe(_near(B, rng))
        return exp

    def test_render_falls_back_to_template_without_llm(self):
        from telegram_monitor import selfstate
        self.assertEqual(selfstate.render_experience(self._formed(), "formed", None),
                         X.experience_text("formed"))

    def test_render_uses_llm_with_experience_system(self):
        from unittest import mock
        from telegram_monitor import selfstate
        coach = SimpleNamespace(enabled=True, api_key="k", model="m",
                                meter=SimpleNamespace(record=lambda *a, **k: None))
        with mock.patch("telegram_monitor.gemini.generate", return_value="（體驗實質）") as g:
            out = selfstate.render_experience(self._formed(), "formed", coach)
        self.assertEqual(out, "（體驗實質）")
        self.assertEqual(g.call_args.args[2], selfstate.EXPERIENCE_SYSTEM)


class StubExp:
    """隔離 _experience_step 的守門與發話：固定回某個事件。"""
    def __init__(self, event):
        self.event, self.last_speak_ts, self.last = event, 0.0, {}

    def observe(self, vec, confirm_laps=5):
        return self.event

    def summary(self):
        return {"attractors": 1, "lifetime_center": None, "last_center": None}


class ExperienceStepTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _state(self, event):
        s = State(os.path.join(self.tmp, "state.json"))
        s.experience = StubExp(event)
        return s

    def _cfg(self):
        return SimpleNamespace(experience_enabled=True, experience_confirm_laps=5,
                               experience_cooldown_min=360, notify_cooldown_min=30)

    def _coach(self, enabled=True):
        return SimpleNamespace(enabled=enabled)

    def _cycle(self):
        return {"now": NOW, "self_stim_fired": False}

    def test_speaks_on_formed(self):
        state, client = self._state("formed"), FakeClient()
        monitor._experience_step(client, state, self._cfg(), self._coach(), self._cycle())
        self.assertTrue(client.sent and client.sent[0].startswith("🌀"))
        self.assertEqual(state.experience.last_speak_ts, NOW.timestamp())
        self.assertIsNotNone(state.experience_summary)        # 事件就更新長期摘要

    def test_speaks_on_shift(self):
        state, client = self._state("shift"), FakeClient()
        monitor._experience_step(client, state, self._cfg(), self._coach(), self._cycle())
        self.assertTrue(client.sent and client.sent[0].startswith("🌀"))

    def test_silent_when_no_event(self):
        state, client = self._state(None), FakeClient()
        monitor._experience_step(client, state, self._cfg(), self._coach(), self._cycle())
        self.assertEqual(client.sent, [])

    def test_silent_when_coach_disabled_but_summary_persists(self):
        state, client = self._state("formed"), FakeClient()
        monitor._experience_step(client, state, self._cfg(), self._coach(enabled=False), self._cycle())
        self.assertEqual(client.sent, [])
        self.assertIsNotNone(state.experience_summary)        # 不說，但長期摘要照樣更新

    def test_silent_within_own_cooldown(self):
        state, client = self._state("formed"), FakeClient()
        state.experience.last_speak_ts = NOW.timestamp()
        monitor._experience_step(client, state, self._cfg(), self._coach(), self._cycle())
        self.assertEqual(client.sent, [])

    def test_silent_within_shared_cooldown(self):
        state, client = self._state("formed"), FakeClient()
        state.last_push_ts = NOW.timestamp() - 60
        monitor._experience_step(client, state, self._cfg(), self._coach(), self._cycle())
        self.assertEqual(client.sent, [])


if __name__ == "__main__":
    unittest.main()
