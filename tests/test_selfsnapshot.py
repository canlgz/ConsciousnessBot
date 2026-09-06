"""自我快照一致性（Wave 1–2）：判定讀數帶『判定當時實際用的 k』，整合環與互動端共用同一構造，
/status 顯示與 Gate 同源同時點——根治「看數據不準」。"""

import os
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from telegram_monitor import analyzer, monitor
from telegram_monitor.state import State


class StashReadingTest(unittest.TestCase):
    def test_stash_carries_judged_time_k_and_context(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        params = {"adaptive": True, "sensitivity": 1.25, "z_star": 2.0, "tau_star": 0.78,
                  "int_min": 0.5, "diff_min": 0.0, "n_min": 8, "r_min": 3}
        monitor._stash_reading(s, {"gate": 3}, now_ts=1000.0, ingest="ING", params=params)
        ss = s.self_state
        self.assertEqual(ss["sensitivity"], 1.25)        # 判定當時實際用的 k
        self.assertEqual(ss["computed_at"], 1000.0)
        self.assertEqual(ss["ingest_at"], "ING")
        self.assertEqual(ss["params_sig"], monitor._params_sig(params))
        self.assertEqual(ss["gate"], 3)


class StatusKConsistencyTest(unittest.TestCase):
    def _snap(self):
        return analyzer.analyze({"records": [], "contexts": [], "journeys": [], "explorations": [], "meta": {}},
                                datetime(2026, 6, 16, tzinfo=timezone.utc), ZoneInfo("Asia/Taipei"))

    def test_status_shows_reading_k_not_rederived(self):
        # 讀數帶 sensitivity=0.5 + 活力呼吸 vit.k_adj=+0.4（另一拍）→ /status 必須顯示 0.5（判定當時），不是 base+0.4
        cfg = SimpleNamespace(selfstate_sensitivity=2.0, lifeloop_wait_secs=3.0)
        st = SimpleNamespace(sensitivity_override=None, pulse_override=None, env=None,
                             vitality={"k_adj": 0.4}, self_state={"gate": 4, "sensitivity": 0.5,
                                                                  "computed_at": None, "omegas": {}})
        out = monitor._status_text(st, cfg, self._snap())
        self.assertIn("k＝0.5", out)
        self.assertIn("判定當時", out)
        self.assertNotIn("有效 2.4", out)               # 不再用 base(2.0)+呼吸(0.4) 重推

    def test_status_falls_back_when_no_reading(self):
        cfg = SimpleNamespace(selfstate_sensitivity=2.0, lifeloop_wait_secs=3.0)
        st = SimpleNamespace(sensitivity_override=None, pulse_override=None, env=None,
                             vitality={"k_adj": 0.0}, self_state=None)
        out = monitor._status_text(st, cfg, self._snap())
        self.assertIn("即時估計", out)                   # 尚無判定 → 標明是即時估計


if __name__ == "__main__":
    unittest.main()
