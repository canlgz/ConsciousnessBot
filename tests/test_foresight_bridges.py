"""🔮 §1.93 修 §1.90 的死機制：`state.associations` 是**物件**不是 dict。

根因（`/foresight` 實測照出來的）：使用者部署後打 `/foresight`，回「線 27 條、可預想的候選 0 個；
這次沒說是因為：**帳本裡還沒有累積出跨主題橋**」——但帳本裡明明有橋。查下去發現 §1.90 寫的是
`isinstance(getattr(state, "associations", None), dict)`，而 `state.associations` 是
`association.Associations` **物件**（state.py:127、monitor.py:8263）⇒ 該判斷**恆為 False**
⇒ 橋清單恆空 ⇒ 這條 lane 從上線到現在**一次都不可能產生候選**。

**而且 §1.90 的測試跟著一起錯**：它餵的是手寫 dict `{"bridges": {...}}`，把同一個錯誤假設編碼進測試，
所以 62 條全綠也蓋不到這個洞。本檔因此**一律用真的 `association.Associations` 物件**建構——
同型錯誤（把物件當 dict、或反過來）不可能再過。

順帶釘住第二個發現：`Associations.summary()` 只存 key/a/b/kind/dir/cos/strength/emerged
⇒ **support 與 anchor 不跨重啟保留**，重生時被 `_blank_bridge()` 補成 0／空字串。
常重啟的部署（只跑 run-temp.sh）因此很難累到 support≥2——這是門檻要能調的理由。
全 stub、零網路。
"""

import io
import os
import re
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from telegram_monitor import association, monitor
from telegram_monitor.state import State

NOW = datetime(2026, 7, 27, 14, 0, 0, tzinfo=timezone.utc).timestamp()
Q = "今天讀到第三品，心很靜"
DATA = {"records": [{"topicLabel": "讀誦經書", "text": Q, "ts": "2026-07-15T08:00:00Z"},
                    {"topicLabel": "跑步", "text": "早上跑了五公里", "ts": "2026-07-27T08:00:00Z"}]}


def _real_assoc(support=3, emerged=False, anchors=True):
    """★ 用**真的** Associations 物件，不是手寫 dict——§1.90 的測試就是敗在這裡。"""
    a = association.Associations()
    a.bridges["blend::跑步|讀誦經書"] = {
        **association._blank_bridge(),
        "key": "blend::跑步|讀誦經書", "a": "跑步", "b": "讀誦經書", "kind": "blend",
        "cos": 0.55, "support": support, "emerged": emerged,
        "anchor_a": {"text": "早上跑了五公里" if anchors else "", "ts": None},
        "anchor_b": {"text": Q if anchors else "", "ts": None}}
    return a


def _cfg(**kw):
    d = dict(foresight_enabled=True, foresight_cooldown_min=720, notify_cooldown_min=30,
             foresight_min_support=2, foresight_dormant_min_days=5.0)
    d.update(kw)
    return SimpleNamespace(**d)


def _state(assoc=None, **kw):
    s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
    s.last_push_ts = NOW - 10 * 3600
    s.last_foresight_ts = 0
    s.associations = assoc
    for k, v in kw.items():
        setattr(s, k, v)
    return s


class AccessorTest(unittest.TestCase):
    """三種形狀都要吃得到——這就是當初漏掉的那一種。"""

    def test_real_associations_object(self):
        brs = monitor._foresight_bridges(_state(_real_assoc()))
        self.assertEqual(len(brs), 1)
        self.assertEqual(brs[0]["b"], "讀誦經書")

    def test_old_wrong_assumption_would_have_failed(self):
        # 釘住根因：Associations 物件**不是** dict，所以舊寫法的 isinstance 判斷恆 False
        self.assertFalse(isinstance(_real_assoc(), dict))

    def test_dict_shape_still_works(self):
        brs = monitor._foresight_bridges(_state({"bridges": {"k": {"a": "A", "b": "B"}}}))
        self.assertEqual(len(brs), 1)

    def test_summary_list_shape_fallback(self):
        # association_summary 的持久化形是 **list**（且沒有 anchor/support）
        s = _state(None)
        s.association_summary = {"emerged_total": 3, "bridges": [{"key": "k", "a": "A", "b": "B"}]}
        self.assertEqual(len(monitor._foresight_bridges(s)), 1)

    def test_none_is_empty(self):
        s = _state(None)
        s.association_summary = None
        self.assertEqual(monitor._foresight_bridges(s), [])


class EmitNowProducesCandidateTest(unittest.TestCase):
    """端到端：修好之後，真的 Associations 物件能產出候選並說出口（之前恆為 0）。"""

    class Cl:
        dry_run = False

        def __init__(self):
            self.sent = []

        def send(self, t):
            self.sent.append(t)
            return True

    def _coach(self):
        c = SimpleNamespace(enabled=True, api_key="k", model="m",
                            meter=SimpleNamespace(record=lambda *a, **k: None), _turn_length=None)
        c.reply = lambda q, *a, **k: ("1" if "挑一個編號" in (q or "") else "")
        return c

    def _emit_cfg(self, **kw):
        d = dict(foresight_enabled=True, foresight_cooldown_min=720, foresight_ttl_days=7,
                 notify_cooldown_min=30, dry_run=True, quiet_start=1, quiet_end=6,
                 timezone="Asia/Taipei", foresight_min_support=2, foresight_dormant_min_days=5.0)
        d.update(kw)
        return SimpleNamespace(**d)

    def test_speaks_with_real_object(self):
        s = _state(_real_assoc())
        s.owner_folder_id, s.last_user_msg_ts = "F", NOW - 3 * 3600
        cl = self.Cl()
        monitor._foresight_emit(cl, s, self._emit_cfg(), self._coach(),
                                datetime.fromtimestamp(NOW, timezone.utc), data=DATA)
        out = "".join(cl.sent)
        self.assertTrue(cl.sent, "修好後仍然不出聲＝lane 還是死的")
        self.assertIn("讀誦經書", out)
        self.assertIn(Q, out)                      # 引文是真實記寫原文
        self.assertIsNotNone(s.foresight)

    def test_support_threshold_is_configurable(self):
        # 重啟會把 support 歸零 ⇒ 門檻必須可調，否則常重啟的部署永遠湊不到
        s = _state(_real_assoc(support=1))
        s.owner_folder_id, s.last_user_msg_ts = "F", NOW - 3 * 3600
        cl = self.Cl()
        monitor._foresight_emit(cl, s, self._emit_cfg(), self._coach(),
                                datetime.fromtimestamp(NOW, timezone.utc), data=DATA)
        self.assertEqual(cl.sent, [])              # 預設 2 → 擋下
        s2 = _state(_real_assoc(support=1))
        s2.owner_folder_id, s2.last_user_msg_ts = "F", NOW - 3 * 3600
        cl2 = self.Cl()
        monitor._foresight_emit(cl2, s2, self._emit_cfg(foresight_min_support=1), self._coach(),
                                datetime.fromtimestamp(NOW, timezone.utc), data=DATA)
        self.assertTrue(cl2.sent)                  # 調成 1 → 放行


class GateCountsTest(unittest.TestCase):
    """候選 0 個時要說得出卡在哪一關——否則門檻只能憑感覺調。"""

    def test_counts_walk_down(self):
        from telegram_monitor import foresight
        spans = foresight.line_spans(
            [{**r, "_ts": NOW - (12 * 86400 if r["topicLabel"] == "讀誦經書" else 3600)} for r in DATA["records"]])
        g = dict(monitor._foresight_gate_counts(monitor._foresight_bridges(_state(_real_assoc())), spans, NOW, _cfg()))
        self.assertEqual(g["橋總數"], 1)
        self.assertEqual(g["兩端都有錨點原文"], 1)

    def test_anchor_loss_after_restart_is_visible(self):
        # 重生後 anchor 空白 ⇒ 逐道閘會停在「兩端都有錨點原文 0」，一眼看得出是重啟導致
        from telegram_monitor import foresight
        spans = foresight.line_spans(
            [{**r, "_ts": NOW - (12 * 86400 if r["topicLabel"] == "讀誦經書" else 3600)} for r in DATA["records"]])
        g = dict(monitor._foresight_gate_counts(
            monitor._foresight_bridges(_state(_real_assoc(anchors=False))), spans, NOW, _cfg()))
        self.assertEqual(g["cos≥0.30"], 1)
        self.assertEqual(g["兩端都有錨點原文"], 0)

    def test_audit_reports_gates_and_restart_caveat(self):
        t = monitor._foresight_audit(_state(_real_assoc(support=1)), _cfg(), DATA, NOW)
        self.assertIn("逐道閘還剩", t)
        self.assertIn("support≥2", t)
        self.assertIn("不會跨重啟保留", t)          # 把「為什麼湊不到」直接寫給使用者看


class SummaryLosesAnchorsTest(unittest.TestCase):
    """釘住第二個發現：summary 不帶 anchor/support ⇒ 重生後歸零（門檻要可調的理由）。"""

    def test_summary_fields(self):
        a = _real_assoc()
        keys = set((a.summary().get("bridges") or [{}])[0])
        self.assertNotIn("anchor_a", keys)
        self.assertNotIn("support", keys)

    def test_rebirth_resets_support_and_anchor(self):
        reborn = association.Associations(summary=_real_assoc().summary())
        br = list(reborn.bridges.values())[0]
        self.assertEqual(int(br.get("support") or 0), 0)
        self.assertEqual((br.get("anchor_a") or {}).get("text") or "", "")


class ConfigTest(unittest.TestCase):
    def test_flags_synced(self):
        src = io.open("telegram_monitor/config.py", encoding="utf-8").read()
        for f in ("FORESIGHT_MIN_SUPPORT", "FORESIGHT_DORMANT_MIN_DAYS",
                  "foresight_min_support", "foresight_dormant_min_days"):
            self.assertIn(f, src)
        env = io.open(".env.example", encoding="utf-8").read()
        self.assertRegex(env, re.compile(r"^FORESIGHT_MIN_SUPPORT=2", re.M))

    def test_getattr_defaults(self):
        self.assertEqual(int(getattr(SimpleNamespace(), "foresight_min_support", 2)), 2)


if __name__ == "__main__":
    unittest.main()
