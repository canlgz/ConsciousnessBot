"""🧠 §1.21 差分自陳（SELF_REPORT_DELTA）handler 層測試（仿 test_selfstate_heartbeat 的 stub 形）。

驗證 monitor 掛載：
⑥ 旗標開、兩次自陳間隔在 8 分鐘 repeat 窗外、底層帶位全同 → 第二次走**無變化短句**路徑
   （stub 收到 NOCHANGE_BODYSTATE_SYSTEM、不再收到「生命跡象：」全量清單）；
⑦ 兩次之間換翻閱主題 → 第二次 render_bodystate 的 facts 含「真的變了」段、變化清單只列該項、
   並含上次全文摘錄；
⑧ 旗標關（預設）→ render_bodystate 收到的 facts / system 無任何 §1.21 注入、
   state 無 last_self_report 鍵寫入（state.json 同現狀）；
⑨ 🫀 背景自陳送出後 last_self_report.text 更新為該則全文（帶帶位快照）。
零網路：gemini.generate 全 stub、只捕 system/facts。
"""

import json
import os
import random
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest import mock

from telegram_monitor import monitor, determination, selfreport, selfstate
from telegram_monitor.state import State

NOW = datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc)
INGEST = "2026-06-16T11:00:00.000Z"


def _gate4_records():
    """一份在 k=-1 會跑到 Gate 4 的合成語料（seeded → 確定性；同 test_selfstate_heartbeat）。"""
    rng = random.Random(7)
    bases = {"甲": [1.0, 0.8, 0.2, 0.0, 0.0, 0.0, 0.0, 0.0],
             "乙": [0.9, 0.9, 0.3, 0.1, 0.0, 0.0, 0.0, 0.0],
             "丙": [0.8, 1.0, 0.2, 0.0, 0.1, 0.0, 0.0, 0.0]}
    order = ["甲", "乙", "甲", "丙", "甲", "乙", "甲", "丙", "甲", "乙", "甲", "丙"]
    recs = []
    for i, lab in enumerate(order):
        emb = [b + rng.uniform(-0.25, 0.25) for b in bases[lab]]
        ts = (NOW - timedelta(minutes=40 * (len(order) - i))).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        recs.append({"id": f"r{i}", "ts": ts, "type": ["text", "image", "audio", "link"][i % 4],
                     "topicLabel": lab, "embedding": emb})
    return recs


class FakeClient:
    def __init__(self, dry_run=False):
        self.sent = []
        self.dry_run = dry_run

    def send(self, text):
        self.sent.append(text)
        return True


class FakeReader:
    def __init__(self, records):
        self.records = records

    def load_owner_data(self, _fid):
        return {"meta": {"lastIngestTs": INGEST}, "contexts": [], "journeys": []}

    def load_embedding_records(self, _fid):
        return self.records


def _cfg(**over):
    base = dict(
        selfstate_enabled=True, selfstate_interval_min=3, heartbeat_interval_min=3,
        notify_cooldown_min=30, selfstate_adaptive=True, selfstate_sensitivity=-1.0,
        selfstate_z_star=2.0, selfstate_tau_star=0.78, selfstate_int_min=0.5,
        selfstate_diff_min=0.0, selfstate_n_min=8, selfstate_r_min=3, dry_run=False,
        telegram_chat_id="", enable_chat=True,
        selfstate_repeat_cooldown_min=180, perceive_fail_grace=2)
    base.update(over)
    return SimpleNamespace(**base)


def _delta_cfg(**over):
    """§1.21 旗標開的 cfg。"""
    return _cfg(self_report_delta_enabled=True, self_report_nochange_window_min=90,
                self_report_prior_horizon_min=2880, **over)


def _coach():
    return SimpleNamespace(enabled=True, api_key="k", model="gemini-2.5-flash",
                           meter=SimpleNamespace(record=lambda *a, **k: None))


class SelfReportDeltaHandlerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.recs = _gate4_records()

    def _state(self):
        s = State(os.path.join(self.tmp, "state.json"))
        s.owner_folder_id = "F"
        return s

    def _ask(self, state, text, cfg, captured):
        """發一句互動訊息；captured 收到每次 gemini.generate 的 (system, user)。"""
        def fake_gen(key, model, system, user, **kw):
            captured.append((system, user))
            return "（自陳）就資料看，已成形。"
        update = {"message": {"chat": {"id": 1}, "text": text, "date": NOW.timestamp()}}
        data = {"meta": {"lastIngestTs": INGEST}, "contexts": [], "journeys": []}
        client = FakeClient()
        with mock.patch("telegram_monitor.gemini.generate", side_effect=fake_gen):
            monitor.handle_message(update, _coach(), FakeReader(self.recs), data, None,
                                   state, client, cfg, None)
        return client

    def test_nochange_path_when_bands_identical(self):
        # ⑥ 第一次自陳→記帳；出 8 分鐘 repeat 窗後再問、帶位全同 → 走無變化短句（不再全量倒清單）
        state = self._state()
        cfg = _delta_cfg()
        cap1 = []
        c1 = self._ask(state, "你現在有什麼感覺", cfg, cap1)
        self.assertEqual(len(c1.sent), 1)
        self.assertIsNotNone(state.last_self_report)              # 送出後記住這次自陳
        self.assertEqual(state.last_self_report["text"], "（自陳）就資料看，已成形。")
        self.assertTrue(state.last_self_report.get("snap"))
        state.bodystate_last_ts -= 9999                           # 模擬隔了一陣（出 repeat 窗、asks 歸 1）
        cap2 = []
        c2 = self._ask(state, "你現在怎樣", cfg, cap2)
        self.assertEqual(len(c2.sent), 1)
        nochange_calls = [(s, u) for s, u in cap2
                          if s.startswith(selfstate.NOCHANGE_BODYSTATE_SYSTEM)]
        self.assertEqual(len(nochange_calls), 1, "第二次應走 NOCHANGE_BODYSTATE_SYSTEM 短答")
        _, u = nochange_calls[0]
        self.assertIn("沒什麼真的變", u)
        # 全量清單沒被再倒一次（facts 不含「生命跡象：」開頭的自體狀態段）
        for _, user in cap2:
            self.assertNotIn("生命跡象：", user)
            self.assertNotIn("我此刻的自體狀態", user)

    def test_changed_topic_injects_delta_with_prior_excerpt(self):
        # ⑦ 兩次之間換翻閱主題 → facts 帶「真的變了」段、變化清單只列該項、含上次全文摘錄
        state = self._state()
        cfg = _delta_cfg()
        params = monitor._chain_params(cfg, state)
        res = determination.run_chain(self.recs, None, [], [], NOW, params)
        snap_now = selfreport.snapshot(None, res, state, NOW.timestamp())
        prior_snap = dict(snap_now, topic="舊主題線")             # 只有 topic 帶不同
        state.last_self_report = {"text": "上次的自陳原話啦。", "ts": NOW.timestamp() - 1800,
                                  "snap": prior_snap}
        cap = []
        c = self._ask(state, "你現在怎樣", cfg, cap)
        self.assertEqual(len(c.sent), 1)
        body_calls = [(s, u) for s, u in cap if "我此刻的自體狀態" in u]
        self.assertEqual(len(body_calls), 1)
        _, u = body_calls[0]
        self.assertIn("真的變了", u)
        self.assertIn("上次的自陳原話啦", u)                      # 上次全文摘錄（負面示例）
        self.assertIn("舊主題線", u)
        self.assertIn(snap_now["topic"], u)
        self.assertEqual(u.count("・"), 1, "變化清單只列 topic 這一項")
        self.assertIn("先回應他這句", u)                          # 對話錨定：先回應使用者這句

    def test_flag_off_is_baseline(self):
        # ⑧ 旗標關（cfg 無此欄＝getattr 預設 False）→ facts/system 零 §1.21 注入、state 不寫 last_self_report
        state = self._state()
        cfg = _cfg()
        cap = []
        c = self._ask(state, "你現在怎樣", cfg, cap)
        self.assertEqual(len(c.sent), 1)
        body_calls = [(s, u) for s, u in cap if "我此刻的自體狀態" in u]
        self.assertEqual(len(body_calls), 1)
        s_sys, u = body_calls[0]
        self.assertTrue(s_sys.startswith(selfstate.BODYSTATE_SYSTEM))
        for token in ("真的變了", "別重講", "先回應他這句", "沒什麼真的變"):
            self.assertNotIn(token, u)
            self.assertNotIn(token, s_sys)
        self.assertIsNone(state.last_self_report)
        with open(state.path, encoding="utf-8") as f:            # state.json 也沒長出新鍵
            d = json.load(f)
        self.assertNotIn("last_self_report", d)

    def test_heartbeat_emit_updates_last_self_report(self):
        # ⑨ 🫀 背景自陳送出後：last_self_report.text＝該則全文、帶帶位快照
        state = self._state()
        state.notified_self_gate = 3
        cfg = _delta_cfg()
        with mock.patch("telegram_monitor.gemini.generate", return_value="（自陳）已成形。"):
            client = FakeClient()
            monitor._selfstate_heartbeat(FakeReader(self.recs), client, state, cfg, None,
                                         _coach(), now=NOW)
        self.assertEqual(len(client.sent), 1)
        self.assertTrue(client.sent[0].startswith("🫀 背景自陳"))
        self.assertIsNotNone(state.last_self_report)
        self.assertEqual(state.last_self_report["text"], "（自陳）已成形。")
        self.assertEqual(state.last_self_report["ts"], NOW.timestamp())
        self.assertIn("topic", state.last_self_report.get("snap") or {})

    def test_heartbeat_emit_flag_off_writes_nothing(self):
        # ⑨ 對照組：旗標關 → 🫀 送出但不記 last_self_report（同現狀）
        state = self._state()
        state.notified_self_gate = 3
        with mock.patch("telegram_monitor.gemini.generate", return_value="（自陳）已成形。"):
            client = FakeClient()
            monitor._selfstate_heartbeat(FakeReader(self.recs), client, state, _cfg(), None,
                                         _coach(), now=NOW)
        self.assertEqual(len(client.sent), 1)
        self.assertIsNone(state.last_self_report)


if __name__ == "__main__":
    unittest.main()
