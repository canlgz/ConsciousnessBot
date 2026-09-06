"""主動「🫀 背景自陳」心跳測試（monitor._selfstate_heartbeat）。

驗證：結構推進到 ≥ Gate 3 時、心跳會主動推一則（受 latch 與冷卻節流）；
且「互動問狀態已先把結果寫進快取」時，心跳仍須補推那一則（不被 cache-hit 吞掉）。
"""

import os
import random
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest import mock

from telegram_monitor import monitor, determination, selfstate, lifeloop
from telegram_monitor.state import State

NOW = datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc)
INGEST = "2026-06-16T11:00:00.000Z"


def _gate4_records():
    """一份在 k=-1 會跑到 Gate 4 的合成語料（seeded → 確定性）。"""
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


def _cfg():
    return SimpleNamespace(
        selfstate_enabled=True, selfstate_interval_min=3, heartbeat_interval_min=3,
        notify_cooldown_min=30, selfstate_adaptive=True, selfstate_sensitivity=-1.0,
        selfstate_z_star=2.0, selfstate_tau_star=0.78, selfstate_int_min=0.5,
        selfstate_diff_min=0.0, selfstate_n_min=8, selfstate_r_min=3, dry_run=False,
        telegram_chat_id="", enable_chat=True,
        selfstate_repeat_cooldown_min=180, perceive_fail_grace=2)   # 韌性：連續 2 圈失敗才判死（測試用小值）


def _coach():
    return SimpleNamespace(enabled=True, api_key="k", model="gemini-2.5-flash",
                           meter=SimpleNamespace(record=lambda *a, **k: None))


class SelfStateHeartbeatTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.recs = _gate4_records()

    def _state(self):
        s = State(os.path.join(self.tmp, "state.json"))
        s.owner_folder_id = "F"
        return s

    def _run(self, state):
        with mock.patch("telegram_monitor.gemini.generate", return_value="（自陳）已成形。"):
            client = FakeClient()
            # 釘住 now=NOW：語料錨在 NOW，不注入就會用真實時鐘 → 真實日期一過判定窗（72h）語料就滑掉、gate 掉。
            monitor._selfstate_heartbeat(FakeReader(self.recs), client, state, _cfg(), None, _coach(), now=NOW)
        return client

    def test_dataset_reaches_gate4(self):
        # 前提：這份語料在 k=-1 確實到 Gate 4（否則後面測的都不算數）
        res = determination.run_chain(self.recs, None, [], [], NOW,
                                      monitor._chain_params(_cfg(), self._state()))
        self.assertEqual(res["gate"], 4)

    def test_emergence_pushes_rich_self_report(self):
        # 真有新進展（升到 Gate 4、還沒講過、有 Gemini）→ 完整「🫀 背景自陳」、更新 latch 與指紋
        state = self._state()
        state.notified_self_gate = 3
        client = self._run(state)
        self.assertEqual(len(client.sent), 1)
        self.assertTrue(client.sent[0].startswith("🫀 背景自陳"))
        self.assertEqual(state.notified_self_gate, 4)
        self.assertTrue((state.told_self_sig or "").startswith("4:"))

    def test_unchanged_beat_is_silent(self):
        # 沒新感覺就閉嘴：資料/參數沒變（cache-hit）、狀態已講過 → 安靜（不再每拍報「時間到了…說過了」）
        state = self._state()
        state.notified_self_gate = 4
        params = monitor._chain_params(_cfg(), state)
        res = determination.run_chain(self.recs, None, [], [], NOW, params)
        state.told_self_sig = selfstate.state_signature(res)            # 已講過這個狀態
        state.self_state = dict(res, computed_at=NOW.timestamp(),
                                ingest_at=INGEST, params_sig=monitor._params_sig(params))  # 連快取也命中
        client = self._run(state)
        self.assertEqual(client.sent, [])                              # 不出聲，生命迴圈靜靜活著

    def test_emergence_not_gated_by_cooldown(self):
        # 湧現不再被冷卻吞掉：就算剛推過別的（last_push_ts 很近），升關仍完整自陳
        state = self._state()
        state.notified_self_gate = 3
        state.last_push_ts = NOW.timestamp() + 10 ** 9   # 冷卻中
        client = self._run(state)
        self.assertEqual(len(client.sent), 1)
        self.assertTrue(client.sent[0].startswith("🫀 背景自陳"))

    def test_push_even_when_cache_hit(self):
        # 互動問狀態先把 Gate 4 寫進快取（但沒講過）→ 心跳 cache-hit 仍完整補推
        state = self._state()
        state.notified_self_gate = 3
        params = monitor._chain_params(_cfg(), state)
        res = determination.run_chain(self.recs, None, [], [], NOW, params)
        state.self_state = dict(res, computed_at=NOW.timestamp(),
                                ingest_at=INGEST, params_sig=monitor._params_sig(params))
        client = self._run(state)
        self.assertEqual(len(client.sent), 1)
        self.assertTrue(client.sent[0].startswith("🫀 背景自陳"))
        self.assertEqual(state.notified_self_gate, 4)

    def test_no_duplicate_after_told_is_silent(self):
        # 已當面講過（told_self_sig 已設）→ 心跳不重發、也不報「說過了」，安靜
        state = self._state()
        state.notified_self_gate = 3
        res = determination.run_chain(self.recs, None, [], [], NOW, monitor._chain_params(_cfg(), state))
        state.told_self_sig = selfstate.state_signature(res)
        client = self._run(state)
        self.assertEqual(client.sent, [])

    def _ask(self, state, text):
        # 帶 date=NOW：handle_message 以訊息時間當「現在」；不帶 date 會退回真實時鐘 → 判定窗滑掉、gate 掉。
        update = {"message": {"chat": {"id": 1}, "text": text, "date": NOW.timestamp()}}
        data = {"meta": {"lastIngestTs": INGEST}, "contexts": [], "journeys": []}
        client = FakeClient()
        with mock.patch("telegram_monitor.gemini.generate", return_value="（自陳）就資料看，已成形。"):
            monitor.handle_message(update, _coach(), FakeReader(self.recs), data, None,
                                   state, client, _cfg(), None)
        return client

    def test_selfstate_followup_continues_conversation(self):
        # 升到 gate≥3 主動自陳、開追問窗後，追問「哪一條？」→ 接回自陳細節，而非丟給一般聊天
        state = self._state()
        state.notified_self_gate = 3                 # 升到 4 → 主動「背景自陳」、設 selfstate_open_ts
        self._run(state)                             # 跑一拍：自陳、開追問窗、快取 self_state
        self.assertGreater(state.selfstate_open_ts, 0)
        self.assertIsNotNone(state.self_state)
        c = self._ask(state, "哪一條？")
        self.assertEqual(len(c.sent), 1)
        # 追問被接回自陳路徑（設 told_self_sig＝快取狀態指紋），而非 coach 誤解成「哪一則記寫」
        self.assertEqual(state.told_self_sig, selfstate.state_signature(state.self_state))

    def test_followup_ignored_when_no_recent_selfstate(self):
        # 沒有近期自陳脈絡時，「哪一條」不該被當成自我狀態追問（交回一般路徑）
        state = self._state()
        state.self_state = None
        state.selfstate_open_ts = 0
        # 不經 _ask（會走到 coach 需要更多相依）；直接驗追問偵測＋脈絡條件
        self.assertTrue(selfstate.is_selfstate_followup("哪一條？"))
        self.assertFalse(selfstate.is_state_question("哪一條？"))   # 它本身不是狀態問句

    def test_interactive_state_fresh_when_asked_occasionally(self):
        # 久久問一次「你現在怎樣」→ 每次都給新鮮的自體狀態轉錄（不是制式「說過了」的牆）
        state = self._state()
        c1 = self._ask(state, "你現在有什麼感覺")
        self.assertEqual(len(c1.sent), 1)
        self.assertNotIn("還是剛剛說的", c1.sent[0])
        self.assertTrue((state.told_self_sig or "").startswith("4:"))
        state.bodystate_last_ts -= 9999                    # 模擬隔了很久才又問
        c2 = self._ask(state, "你現在感覺如何")
        self.assertEqual(len(c2.sent), 1)
        self.assertEqual(state.bodystate_asks, 1)          # 視為新一次、給新鮮答覆

    def test_rapid_reask_says_just_said(self):
        # 短時間內連問同一件事 → 帶點「剛說過」的口吻（而非又制式複誦整段）
        state = self._state()
        with mock.patch("telegram_monitor.selfstate.render_bodystate", return_value="（新鮮自陳）"), \
             mock.patch("telegram_monitor.selfstate.render_bodystate_again", return_value="（剛說過啦）"):
            c1 = self._ask(state, "你現在感覺如何")
            c2 = self._ask(state, "你有看見、感受到什麼")
            c3 = self._ask(state, "你現在的狀態OK嗎")
        self.assertEqual(c1.sent, ["（新鮮自陳）"])        # 第一次：新鮮
        self.assertEqual(c2.sent, ["（剛說過啦）"])        # 很快又問：剛說過
        self.assertEqual(c3.sent, ["（剛說過啦）"])
        self.assertEqual(state.bodystate_asks, 3)          # 連問累加（口吻隨次數升級）

    def test_bodystate_enters_shared_dialogue_memory(self):
        # 統一記憶：自體狀態的回話也進 convo_history＋設對話焦點 → 教練接得上「新東西/那條線」
        state = self._state()
        self._ask(state, "你現在怎樣")
        self.assertEqual(state.convo_history[-2]["role"], "user")
        self.assertEqual(state.convo_history[-2]["text"], "你現在怎樣")
        self.assertIn("ts", state.convo_history[-2])          # 每則對話帶時間戳（對話時間軸）
        self.assertEqual(state.convo_history[-1]["role"], "model")
        self.assertTrue(state.convo_history[-1]["text"])
        self.assertIsNotNone(state.focus)
        self.assertIsNotNone((state.focus or {}).get("topic"))   # 手上的線＝對話焦點主線

    def test_render_bodystate_again_template_is_short_and_varies(self):
        res = {"gate": 3, "scope": {"dominant": "研發日誌"}}
        for asks in (2, 3, 5):
            t = selfstate.render_bodystate_again(asks, None, res, None)   # 無 coach → 模板池
            self.assertNotIn("我醒著、連著跳", t)                         # 不是完整複誦
            self.assertTrue(any(k in t for k in ("剛", "說過", "還在", "😅", "啦")))

    def test_interactive_uses_confirmed_res_for_consistency(self):
        # 互動「你現在怎樣」用『已確認讀數』描述＋設指紋（與主動同源）→ 不會跟主動背景自陳的 gate 不一致。
        state = self._state()
        state.confirmed_res = {"gate": 2, "scope": {"dominant": "甲"}, "omegas": {}}  # 已確認＝Gate 2
        self._ask(state, "你現在怎樣")                                       # 即時資料會算到 Gate 4
        self.assertEqual(state.told_self_sig, selfstate.state_signature(state.confirmed_res))
        self.assertEqual(state.told_self_sig, "2")                          # 用確認的 Gate 2、非即時 Gate 4

    def test_fu_question_gets_feeling(self):
        # 牆已移除：「你有 fu 嗎」＝問有沒有感覺 → 走判定鏈、回一個 data-grounded 的感覺描述
        state = self._state()
        c = self._ask(state, "你有 fu 嗎")
        self.assertEqual(len(c.sent), 1)
        self.assertIn("（自陳）", c.sent[0])          # 來自判定鏈→翻譯層（mock），而非一道牆

    def test_phenomenology_routes_to_bodystate(self):
        # 牆已移除＋自體狀態：問「裡面真的有感覺嗎」＝問 bot 自己 → 走自體狀態轉錄（新鮮），不撞牆也不回「說過了」
        state = self._state()
        res = determination.run_chain(self.recs, None, [], [], NOW, monitor._chain_params(_cfg(), state))
        state.told_self_sig = selfstate.state_signature(res)   # 即使已講過，問「你自己」仍給新鮮自體狀態
        c = self._ask(state, "你裡面真的有感覺嗎")
        self.assertEqual(len(c.sent), 1)
        self.assertNotIn("還是剛剛說的", c.sent[0])
        self.assertNotIn("空的", c.sent[0])                    # 不是牆


class SelfStateEmitThrottleTest(unittest.TestCase):
    """背景自陳節流：卡在門檻邊緣抖動（Gate 3↔2↔3、moved 換來換去）不該每拍洗版。
    只有突破歷史最高閘（真新湧現）才打斷冷卻；同高度／再升回都受 notify_cooldown_min 夾住。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _state(self):
        return State(os.path.join(self.tmp, "state.json"))

    def _emit(self, state, gate, moved, now, cooldown_min=30):
        client = FakeClient()
        with mock.patch("telegram_monitor.selfstate.render", return_value="（自陳）"):
            monitor._selfstate_emit(client, state, {"gate": gate, "moved": moved},
                                    _coach(), now, cooldown_min)
        return client

    def test_ceiling_latch_suppresses_threshold_jitter(self):
        state = self._state()
        t0 = NOW
        self.assertEqual(len(self._emit(state, 3, ["甲"], t0).sent), 1)          # 破天花板 0→3：真湧現，說
        self.assertEqual(state.notified_self_gate, 3)
        # 1 分後 moved 換（sig 變）但仍 Gate 3、冷卻內 → 安靜
        self.assertEqual(self._emit(state, 3, ["乙"], t0 + timedelta(minutes=1)).sent, [])
        # 掉到 Gate 2 再回 Gate 3：天花板不降，回 3 不算破頂、仍冷卻內 → 安靜（不被抖動洗版）
        self._emit(state, 2, [], t0 + timedelta(minutes=2))
        self.assertEqual(self._emit(state, 3, ["甲"], t0 + timedelta(minutes=3)).sent, [])
        self.assertEqual(state.notified_self_gate, 3)                            # 只升不降

    def test_true_escalation_breaks_cooldown(self):
        state = self._state()
        self._emit(state, 3, ["甲"], NOW)
        c = self._emit(state, 4, ["甲"], NOW + timedelta(minutes=1))            # 冷卻內但破頂 3→4
        self.assertEqual(len(c.sent), 1)
        self.assertEqual(state.notified_self_gate, 4)

    def test_same_gate_speaks_again_after_cooldown(self):
        state = self._state()
        self._emit(state, 3, ["甲"], NOW)
        c = self._emit(state, 3, ["乙"], NOW + timedelta(minutes=31))           # sig 變＋冷卻過 → 刷新一則
        self.assertEqual(len(c.sent), 1)


class LifeLoopWiringTest(unittest.TestCase):
    """生命迴圈接線：感知→整合→感覺真的串起來、k 隨活力呼吸、感知失敗＝終局死亡。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.recs = _gate4_records()

    def _state(self):
        s = State(os.path.join(self.tmp, "state.json"))
        s.owner_folder_id = "F"
        return s

    def _coach(self):
        return SimpleNamespace(enabled=True, api_key="k", model="gemini-2.5-flash",
                               meter=SimpleNamespace(record=lambda *a, **k: None,
                                                     check_alert=lambda: None))

    def test_wiring_pulses_feels_and_breathes_k(self):
        state, client = self._state(), FakeClient()
        data = {"meta": {"lastIngestTs": INGEST}, "contexts": [], "journeys": []}
        # pacer 設超大 → 這拍「行動」不觸發 tick（避免拉 analyze）；「感知」用 patch 的 _collect
        phases = monitor._life_phases(FakeReader(self.recs), client, state, _cfg(), None,
                                      self._coach(), chat_on=False, pacer={"last_tick": 1e18})
        with mock.patch("telegram_monitor.monitor._collect", return_value=(data, object())), \
             mock.patch("telegram_monitor.gemini.generate", return_value="（自陳）就資料看，已成形。"):
            loop = lifeloop.LifeLoop(phases, wait_secs=0.0, sleep=lambda s: None)
            loop.vit = lifeloop.Vitality(0)
            ok = loop.spin_once({})
        self.assertTrue(ok)                                  # 整圈閉合＝一次脈動
        self.assertEqual(loop.vit.pulse, 1)
        self.assertIsNotNone(state.self_state)               # 「整合」環隨遞迴跑了判定鏈
        self.assertIsNotNone(state.self_state.get("sensitivity"))  # 讀數帶「判定當時實際用的 k」（/status 同源顯示）
        self.assertGreater(state.k_breath_adj, 0)            # 「整合」環設了 k 呼吸（剛醒→收緊+）
        self.assertIsNotNone(state.entropy)                  # 內在熵已建立
        self.assertEqual(state.entropy.prev_ingest, INGEST)  # 並吃了本圈的 ingest 戳

    def test_wiring_perceive_failure_is_terminal_death(self):
        # 環境前提不符（資料夾未解析）＝非暫態 → 立刻終局死亡（不重試）
        state, client = self._state(), FakeClient()
        phases = monitor._life_phases(FakeReader(self.recs), client, state, _cfg(), None,
                                      self._coach(), chat_on=False, pacer={"last_tick": 1e18})
        with mock.patch("telegram_monitor.monitor._collect", return_value=(None, None)):
            loop = lifeloop.LifeLoop(phases, wait_secs=0.0, sleep=lambda s: None,
                                     on_death=lambda v: client.send("DEATH"))
            cause = loop.run_forever(lambda: {})
        self.assertFalse(loop.vit.alive)
        self.assertEqual(cause[0], "感知")
        self.assertIn("記憶層", cause[1])                     # 環境前提（非網路）→ 直接死
        self.assertIn("DEATH", client.sent)

    def test_wiring_survives_transient_perceive_error(self):
        # 暫態網路錯誤（連線重置）不該立刻死：第一圈暫斷→容忍續活，第二圈恢復→照常脈動
        state, client = self._state(), FakeClient()
        data = {"meta": {"lastIngestTs": INGEST}, "contexts": [], "journeys": []}
        phases = monitor._life_phases(FakeReader(self.recs), client, state, _cfg(), None,
                                      self._coach(), chat_on=False, pacer={"last_tick": 1e18})
        calls = {"n": 0}

        def flaky(*a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ConnectionResetError(54, "Connection reset by peer")
            return (data, object())

        with mock.patch("telegram_monitor.monitor._collect", side_effect=flaky), \
             mock.patch("telegram_monitor.gemini.generate", return_value="（自陳）就資料看，已成形。"):
            loop = lifeloop.LifeLoop(phases, wait_secs=0.0, sleep=lambda s: None)
            loop.vit = lifeloop.Vitality(0)
            ok1 = loop.spin_once({})         # 感知暫斷 → 容忍、不死
            ok2 = loop.spin_once({})         # 感知恢復
        self.assertTrue(ok1 and ok2)
        self.assertTrue(loop.vit.alive)
        self.assertEqual(loop.vit.pulse, 2)  # 兩圈都閉合＝活著撐過了暫斷


if __name__ == "__main__":
    unittest.main()
