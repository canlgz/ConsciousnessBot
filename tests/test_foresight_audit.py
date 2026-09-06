"""🔮 §1.92 預想對帳（/foresight）：把「為什麼沒說」變成看得見的。

§1.90 的規格自己寫明「**沉默必須是可觀測的**——這條 lane 的預設失敗模式是『從不觸發』，沒有對帳就
沒人會發現」，也已經寫好了 `foresight.audit_text`——但**我當初漏掉沒把它掛上任何指令**。
使用者部署後 🔮 一直沒出現，卻無從判斷是候選不夠、還是節流卡住、還是手上那條還沒到期。
這正是 MEMORY「新機制常被無聲架空」的又一次現形：函式存在、邏輯正確、**從沒被呼叫到**。

本檔釘住：指令真的接上了、六種「沒說」的原因都分得出來、有候選時說得出下一個會拿誰來說。
全 stub、零網路。
"""

import io
import os
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from telegram_monitor import monitor
from telegram_monitor.state import State

NOW = datetime(2026, 7, 27, 14, 0, 0, tzinfo=timezone.utc).timestamp()
Q = "今天讀到第三品，心很靜"
DATA = {"records": [{"topicLabel": "讀誦經書", "text": Q, "ts": "2026-07-15T08:00:00Z"},
                    {"topicLabel": "跑步", "text": "早上跑了五公里", "ts": "2026-07-27T08:00:00Z"}]}


def _cfg(on=True, **kw):
    d = dict(foresight_enabled=on, foresight_cooldown_min=720, notify_cooldown_min=30)
    d.update(kw)
    return SimpleNamespace(**d)


def _state(bridges=True, **kw):
    s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
    s.last_push_ts = NOW - 10 * 3600
    s.last_foresight_ts = 0
    if bridges:
        s.associations = {"bridges": {"k": {
            "key": "k", "a": "跑步", "b": "讀誦經書", "kind": "blend", "cos": 0.55,
            "support": 3, "emerged": False,
            "anchor_a": {"text": "早上跑了五公里", "ts": None},
            "anchor_b": {"text": Q, "ts": None}}}}
    for k, v in kw.items():
        setattr(s, k, v)
    return s


class WhyNotTest(unittest.TestCase):
    """六種「沒說」的原因要分得出來——這才是對帳的價值。"""

    def test_not_blocked(self):
        t = monitor._foresight_audit(_state(), _cfg(), DATA, NOW)
        self.assertIn("沒有被擋", t)
        self.assertIn("候選 1 個", t)

    def test_flag_off(self):
        self.assertIn("FORESIGHT=0", monitor._foresight_audit(_state(), _cfg(on=False), DATA, NOW))

    def test_live_hypothesis(self):
        t = monitor._foresight_audit(_state(foresight={"b": "讀誦經書"}), _cfg(), DATA, NOW)
        self.assertIn("還沒到期", t)
        self.assertIn("settle 優先", t)

    def test_no_bridges(self):
        self.assertIn("還沒有累積出跨主題橋", monitor._foresight_audit(_state(bridges=False), _cfg(), DATA, NOW))

    def test_no_records(self):
        self.assertIn("讀不到任何可解析時間的記寫",
                      monitor._foresight_audit(_state(), _cfg(), {"records": []}, NOW))

    def test_no_qualifying_bridge(self):
        # 有橋但條件不夠——要說得出**是哪些條件**沒滿足，不能只說「沒有候選」
        s = _state()
        s.associations["bridges"]["k"]["support"] = 1
        t = monitor._foresight_audit(s, _cfg(), DATA, NOW)
        self.assertIn("support≥2", t)
        self.assertIn("停住 5–60 天", t)

    def test_own_cooldown_remaining(self):
        t = monitor._foresight_audit(_state(last_foresight_ts=NOW - 3600), _cfg(), DATA, NOW)
        self.assertIn("自有冷卻還剩", t)

    def test_shared_anti_burst_remaining(self):
        t = monitor._foresight_audit(_state(last_push_ts=NOW - 300), _cfg(), DATA, NOW)
        self.assertIn("共用反連發還剩", t)

    def test_miss_streak_doubles_reported_cooldown(self):
        led = [{"key": f"p{i}", "verdict": "miss", "ts": NOW - 30 * 86400} for i in range(3)]
        a = monitor._foresight_audit(_state(last_foresight_ts=NOW - 13 * 3600), _cfg(), DATA, NOW)
        b = monitor._foresight_audit(
            _state(last_foresight_ts=NOW - 13 * 3600, foresight_ledger=led), _cfg(), DATA, NOW)
        self.assertIn("沒有被擋", a)
        self.assertIn("自有冷卻還剩", b)          # 連三次想錯＝冷卻加倍，對帳要照實反映


class NextCandidateTest(unittest.TestCase):
    def test_shows_next_candidate_with_real_quote(self):
        t = monitor._foresight_audit(_state(), _cfg(), DATA, NOW)
        self.assertIn("下一個會拿來說的", t)
        self.assertIn("「跑步」還在動", t)
        self.assertIn("「讀誦經書」停了", t)
        self.assertIn(Q, t)                        # 引文是真實記寫原文

    def test_counts_from_ledger(self):
        led = [{"verdict": "hit"}, {"verdict": "miss"}, {"verdict": "hit"}]
        t = monitor._foresight_audit(_state(foresight_ledger=led), _cfg(), DATA, NOW)
        self.assertIn("猜中 2 次", t)
        self.assertIn("想錯 1 次", t)


class WiringTest(unittest.TestCase):
    """指令真的接上了——§1.90 漏掉的就是這一步（函式存在、從沒被呼叫到）。"""

    def test_command_mounted(self):
        src = io.open("telegram_monitor/monitor.py", encoding="utf-8").read()
        self.assertIn('low.startswith(("/foresight", "/預想"))', src)
        self.assertIn("client.send(_foresight_audit(state, cfg, data, time.time()))", src)

    def test_gated_by_flag(self):
        src = io.open("telegram_monitor/monitor.py", encoding="utf-8").read()
        i = src.index('low.startswith(("/foresight", "/預想"))')
        self.assertIn('getattr(cfg, "foresight_enabled", False)', src[i:i + 200])


if __name__ == "__main__":
    unittest.main()
