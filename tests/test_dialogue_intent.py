"""🧭 對話意圖湧現＋違常偵測＋主動結尾（dialogue_intent.py ＋ monitor 接線）。

核心場景（截圖根因）：使用者連發 5 次「早安」測試 bot——應從行為**湧現**出「刻意/在逗我」的判讀、
違常浮現、bot 溫暖呼應或好奇反問確認，而不是每次都當全新招呼熱情回。全偵測純函式、可單測、旗標可關。
"""

import unittest
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from telegram_monitor import dialogue_intent as di, persona, coupling as cpl, monitor


def _cfg(**over):
    base = dict(dialogue_intent_enabled=True, intent_log_max=20, intent_repeat_window_sec=300,
                intent_repeat_n=4, intent_sim_threshold=0.85, anomaly_probe_threshold=0.6,
                intent_degree_drive=True, intent_question_cooldown_min=30,
                proactive_close_enabled=True, close_after_min=12, soothe_after_min=7,
                notify_cooldown_min=30, timezone="Asia/Taipei")
    base.update(over)
    return SimpleNamespace(**base)


def _log(kind, sig, *tss):
    return [{"kind": kind, "sig": sig, "ts": t} for t in tss]


# ── 純函式：指紋／近似／連發／違常 ───────────────────────────────────
class PureTest(unittest.TestCase):
    def test_msg_sig_normalizes(self):
        self.assertEqual(di._msg_sig("早安！！ 😊"), "早安")
        self.assertEqual(di._msg_sig("  Hello, World!  "), "helloworld")
        self.assertEqual(di._msg_sig(""), "")

    def test_similar_boundaries(self):
        self.assertTrue(di._similar("早安", "早安"))                 # 連發同句
        self.assertTrue(di._similar("早安", "早安啊"))               # 加語助詞仍同句
        self.assertFalse(di._similar("早安", "午安"))                # 季節交替＝不同句
        self.assertFalse(di._similar("早安", "早安你好嗎最近如何"))   # 長度差太多＝不同句
        self.assertFalse(di._similar("早", "早安"))                  # 單字 vs 雙字＝不同形式（lenratio 0.5 嚴格排除）
        self.assertFalse(di._similar("", "早安"))

    def test_repetition_run_counts_dense(self):
        log = _log("greeting", "早安", 1000, 1045, 1090, 1135, 1180)   # 5 則、45s 間隔、span 180s
        run = di.repetition_run(log, 1180, 300, 0.85)
        self.assertEqual(run["count"], 5)
        self.assertEqual(run["kind"], "greeting")
        self.assertGreater(run["density"], 1.0)

    def test_repetition_run_window_cuts_spread(self):
        log = _log("greeting", "早安", 0, 1000, 2000, 3000, 3600)      # 散在 1 小時
        run = di.repetition_run(log, 3600, 300, 0.85)                  # 5 分窗 → 只剩窗內最後 1 則
        self.assertEqual(run["count"], 1)

    def test_repetition_run_kind_change_breaks(self):
        log = _log("greeting", "早安", 1000, 1045) + _log("smalltalk", "在嗎", 1090)
        run = di.repetition_run(log, 1090, 300, 0.85)
        self.assertEqual(run["count"], 1)                             # 最新是 smalltalk、與前面 greeting 不同類

    def test_anomaly_below_n_is_zero(self):
        log = _log("greeting", "早安", 1000, 1045, 1090)              # 只 3 次 < N=4
        run = di.repetition_run(log, 1090, 300, 0.85)
        self.assertEqual(di.anomaly_score(run, None, {}, 4), 0.0)

    def test_anomaly_rapport_damping(self):
        log = _log("greeting", "早安", 1000, 1045, 1090, 1135, 1180)
        run = di.repetition_run(log, 1180, 300, 0.85)
        stranger = di.anomaly_score(run, None, {"rapport": 0.0, "warmth": 0.0}, 4)
        regular = di.anomaly_score(run, None, {"rapport": 0.9, "warmth": 0.8}, 4)
        self.assertGreater(stranger, regular)                        # 熟＋暖 → 違常分被壓低（不誤判成測試）
        self.assertGreaterEqual(regular, 0.0)

    def test_classify_repetition(self):
        log = _log("greeting", "早安", 1000, 1045, 1090, 1135, 1180)
        run = di.repetition_run(log, 1180, 300, 0.85)
        cold = cpl.Coupling(i_user=0.2)
        self.assertEqual(di.classify_repetition(run, cold, {}, 4), "testing")       # 短社交語、低投入 → 在試/逗
        warm = cpl.Coupling(i_user=0.7)
        self.assertEqual(di.classify_repetition(run, warm, {}, 4), "seeking_confirmation")  # 高投入 → 真求確認
        thin = di.repetition_run(_log("greeting", "早安", 1000, 1045), 1045, 300, 0.85)
        self.assertIsNone(di.classify_repetition(thin, cold, {}, 4))                # 未達門檻

    def test_intent_degree_uses_coupling(self):
        self.assertEqual(di.intent_degree(None), 0.0)
        self.assertAlmostEqual(di.intent_degree(cpl.Coupling(i_bot=0.9, i_user=0.3)), 0.9)


# ── observe／read：意圖履歷與意圖向量 ────────────────────────────────
class ObserveReadTest(unittest.TestCase):
    def _state(self, **kw):
        return SimpleNamespace(user_model=kw.get("user_model", {}), coupling=kw.get("coupling"),
                               last_user_msg_ts=kw.get("last_user_msg_ts", 0), intent_reading=None)

    def test_observe_appends_and_trims(self):
        st = self._state()
        cfg = _cfg(intent_log_max=3)
        for i in range(5):
            di.observe(st, "早安", "greeting", 1000 + i, cfg)
        log = st.user_model["intent_log"]
        self.assertEqual(len(log), 3)                                # 環形裁尾
        self.assertEqual(log[-1]["sig"], "早安")

    def test_observe_flag_off_noop(self):
        st = self._state()
        di.observe(st, "早安", "greeting", 1000, _cfg(dialogue_intent_enabled=False))
        self.assertNotIn("intent_log", st.user_model)

    def test_read_testing_escalates_to_curious_probe(self):
        # 截圖場景：低 rapport 連發 5 次早安 → 違常浮現、種類 testing、升級好奇反問確認
        um = {"rapport": 0.0, "warmth": 0.0,
              "intent_log": _log("greeting", "早安", 1000, 1045, 1090, 1135, 1180)}
        st = self._state(user_model=um, coupling=cpl.Coupling(i_user=0.2))
        r = di.read(st, st.coupling, 1180, _cfg())
        self.assertGreaterEqual(r["anomaly"], 0.6)
        self.assertEqual(r["anomaly_kind"], "testing")
        self.assertEqual(r["style"], "curious_probe")
        self.assertTrue(r["should_question"])

    def test_read_warm_for_regular_not_probe(self):
        # 熟人熱情連發 → 阻尼壓低違常 → 溫暖呼應、不升級反問（不誤傷熟人）
        um = {"rapport": 0.9, "warmth": 0.8,
              "intent_log": _log("greeting", "早安", 1000, 1045, 1090, 1135, 1180)}
        st = self._state(user_model=um, coupling=cpl.Coupling(i_user=0.2))
        r = di.read(st, st.coupling, 1180, _cfg())
        self.assertNotEqual(r["style"], "curious_probe")
        self.assertFalse(r["should_question"])

    def test_read_question_cooldown(self):
        um = {"rapport": 0.0, "warmth": 0.0, "last_questioned_ts": 1170,   # 剛反問過（10s 前）
              "intent_log": _log("greeting", "早安", 1000, 1045, 1090, 1135, 1180)}
        st = self._state(user_model=um, coupling=cpl.Coupling(i_user=0.2))
        r = di.read(st, st.coupling, 1180, _cfg())
        self.assertFalse(r["should_question"])                       # 冷卻內 → 降回溫暖、不重複追問
        self.assertEqual(r["style"], "warm")

    def test_read_drive_off_no_probe(self):
        um = {"rapport": 0.0, "warmth": 0.0,
              "intent_log": _log("greeting", "早安", 1000, 1045, 1090, 1135, 1180)}
        st = self._state(user_model=um, coupling=cpl.Coupling(i_user=0.2))
        r = di.read(st, st.coupling, 1180, _cfg(intent_degree_drive=False))
        self.assertNotEqual(r["style"], "curious_probe")             # drive 關 → 不升級反問，仍可溫暖
        self.assertFalse(r["should_question"])

    def test_read_flag_off_none(self):
        st = self._state(user_model={"intent_log": _log("greeting", "早安", 1000, 1045, 1090, 1135, 1180)})
        self.assertIsNone(di.read(st, None, 1180, _cfg(dialogue_intent_enabled=False)))

    def test_intent_log_persists_across_rebirth(self):
        import tempfile
        from telegram_monitor import state as stmod, othermind
        p = tempfile.mktemp(suffix=".json")
        s = stmod.State.load(p)
        s.user_model = othermind.fresh()
        for t in (1000, 1045, 1090):
            di.observe(s, "早安", "greeting", t, _cfg())
        s.save()
        s2 = stmod.State.load(p)                                      # 醒來＝重生
        self.assertEqual(len(s2.user_model["intent_log"]), 3)         # 意圖履歷跨重生延續
        self.assertEqual(s2.user_model["intent_log"][-1]["sig"], "早安")


# ── close_decision：主動結尾的意圖/違常判定 ─────────────────────────
class CloseDecisionTest(unittest.TestCase):
    def test_probe_settled(self):
        # 已反問過(last_questioned_ts 近)＋仍違常＋對方淡下來(沉默 > soothe_after) → probe_settled
        um = {"rapport": 0.0, "warmth": 0.0, "last_questioned_ts": 9000,
              "intent_log": _log("greeting", "早安", 8800, 8845, 8890, 8935, 8980)}
        st = SimpleNamespace(user_model=um, coupling=cpl.Coupling(i_user=0.2), last_user_msg_ts=8980)
        now = 8980 + 8 * 60                                          # 距最後訊息 8 分（>7）＝淡了
        ok, reason = di.close_decision(st, st.coupling, now, _cfg())
        self.assertTrue(ok)
        self.assertEqual(reason, "probe_settled")

    def test_natural_convergence_from_just_closed(self):
        st = SimpleNamespace(user_model={}, coupling=cpl.Coupling(just_closed="ignored"), last_user_msg_ts=0)
        ok, reason = di.close_decision(st, st.coupling, 5000, _cfg())
        self.assertTrue(ok)
        self.assertEqual(reason, "natural_convergence")

    def test_flag_off(self):
        st = SimpleNamespace(user_model={}, coupling=cpl.Coupling(just_closed="ignored"), last_user_msg_ts=0)
        self.assertEqual(di.close_decision(st, st.coupling, 5000, _cfg(proactive_close_enabled=False)), (False, None))

    def test_probe_settled_needs_user_msg_ts(self):
        # 剛重生 last_user_msg_ts=0 → 不該因「now-0 很大」誤判 quiet 而觸發 probe_settled
        um = {"rapport": 0.0, "warmth": 0.0, "last_questioned_ts": 9000,
              "intent_log": _log("greeting", "早安", 8800, 8845, 8890, 8935, 8980)}
        st = SimpleNamespace(user_model=um, coupling=cpl.Coupling(i_user=0.2), last_user_msg_ts=0)
        self.assertEqual(di.close_decision(st, st.coupling, 9000 + 480, _cfg()), (False, None))


# ── persona hint：適性語氣（不報數字、含推測詞、可關）────────────────
class HintTest(unittest.TestCase):
    def test_level0_or_none_empty(self):
        self.assertEqual(persona.dialogue_intent_hint("none", 0), "")
        self.assertEqual(persona.dialogue_intent_hint("warm", 0), "")
        self.assertEqual(persona.dialogue_intent_hint("", 2), "")

    def test_hint_has_no_digits_and_hedges(self):
        for style in ("warm", "curious_probe"):
            for lvl in (1, 2, 3):
                h = persona.dialogue_intent_hint(style, lvl)
                self.assertTrue(h)
                self.assertFalse(any(ch.isdigit() for ch in h), f"{style}/{lvl} 不該含數字：{h}")
                self.assertNotIn("第", h)                            # 別「第N次」
        probe = persona.dialogue_intent_hint("curious_probe", 2)
        self.assertTrue(any(w in probe for w in ("好像", "是不是", "推測")))   # 含推測語氣

    def test_greeting_user_anomaly_hint_additive(self):
        # anomaly_hint='' → 與舊版逐位元相同（byte-identical when off）
        self.assertEqual(persona.greeting_user("早安", "FACTS"),
                         persona.greeting_user("早安", "FACTS", ""))
        with_hint = persona.greeting_user("早安", "FACTS", "（覺察提示）")
        self.assertIn("（覺察提示）", with_hint)


# ── monitor._maybe_close_round：主動暖收（含閘、讓位、flag-off）──────
class FakeClient:
    def __init__(self, dry_run=True):
        self.sent, self.dry_run = [], dry_run

    def send(self, text):
        self.sent.append(text)
        return True


class MaybeCloseTest(unittest.TestCase):
    def _now(self):
        return datetime(2026, 6, 25, 13, 0, tzinfo=ZoneInfo("Asia/Taipei"))    # 白天、非深夜

    def _state(self, **kw):
        base = dict(user_model={}, coupling=None, convo_history=[], last_user_msg_ts=0,
                    last_soothe_ts=0, last_close_ts=0, last_push_ts=0, soothed_for_ts=0)
        base.update(kw)
        return SimpleNamespace(**base)

    def test_closes_on_just_closed(self):
        now = self._now()
        st = self._state(coupling=cpl.Coupling(just_closed="ignored"))
        c = FakeClient()
        self.assertTrue(monitor._maybe_close_round(c, st, _cfg(), None, now))
        self.assertEqual(len(c.sent), 1)
        self.assertEqual(st.coupling.last_closure, "understanding")   # 暖收（非被晾）
        self.assertIsNone(st.coupling.just_closed)                    # 消費掉
        self.assertEqual(st.last_close_ts, now.timestamp())

    def test_flag_off_never_closes(self):
        now = self._now()
        st = self._state(coupling=cpl.Coupling(just_closed="ignored"))
        c = FakeClient()
        self.assertFalse(monitor._maybe_close_round(c, st, _cfg(proactive_close_enabled=False), None, now))
        self.assertEqual(c.sent, [])

    def test_yields_to_recent_soothe(self):
        now = self._now()
        st = self._state(coupling=cpl.Coupling(just_closed="ignored"), last_soothe_ts=now.timestamp() - 60)
        c = FakeClient()
        self.assertFalse(monitor._maybe_close_round(c, st, _cfg(), None, now))   # 剛緩和過 → 讓位、不收

    def test_natural_close_blocked_when_present(self):
        now = self._now()
        st = self._state(coupling=cpl.Coupling(just_closed="ignored"),
                         last_user_msg_ts=now.timestamp() - 30)                  # 剛說過話＝在場
        c = FakeClient()
        self.assertFalse(monitor._maybe_close_round(c, st, _cfg(), None, now))   # 還在場 → 別硬收


class ObserveIntentWiringTest(unittest.TestCase):
    """monitor._observe_intent：連發合併成的多行訊息逐行記（burst 開啟仍偵測得到）；編輯訊息不記。"""

    def _state(self):
        import tempfile
        from telegram_monitor import state as stmod, othermind
        s = stmod.State.load(tempfile.mktemp(suffix=".json"))
        s.user_model = othermind.fresh()
        s.coupling = cpl.Coupling(i_user=0.2)
        s.last_user_msg_ts = 10000.0
        return s

    def _route_ref(self, st, text, now):
        from telegram_monitor import intent, referent
        ref = referent.resolve(st, now, monitor.SELFSTATE_FOLLOWUP_SEC)
        return intent.resolve(text, ref), ref

    def test_coalesced_multiline_burst_detected(self):
        # burst 把 5 次早安合成成一則「早安\n早安\n…」→ _observe_intent 逐行記 5 筆 greeting → 違常浮現、好奇反問
        st, now = self._state(), 10000.0
        text = "\n".join(["早安"] * 5)
        route, ref = self._route_ref(st, text, now)
        hint = monitor._observe_intent(st, {"message": {"text": text, "chat": {"id": 1}}}, text, route, ref, now, _cfg())
        log = st.user_model["intent_log"]
        self.assertEqual(len(log), 5)                                # 逐行記、非合成一筆
        self.assertTrue(all(e["kind"] == "greeting" for e in log))   # 每行各自解析回 greeting（非 fact_or_chat）
        self.assertEqual(st.intent_reading["anomaly_kind"], "testing")
        self.assertEqual(st.intent_reading["style"], "curious_probe")
        self.assertTrue(hint)

    def test_edited_message_not_observed(self):
        # 編輯訊息（edited_message，改錯字）不算新行為 → 不記，避免被誤算成連發違常
        st, now = self._state(), 10000.0
        route, ref = self._route_ref(st, "早安", now)
        hint = monitor._observe_intent(st, {"edited_message": {"text": "早安", "chat": {"id": 1}}}, "早安", route, ref, now, _cfg())
        self.assertEqual(st.user_model.get("intent_log", []), [])     # 編輯不進履歷
        self.assertEqual(hint, "")

    def test_flag_off_no_observe_no_hint(self):
        st, now = self._state(), 10000.0
        route, ref = self._route_ref(st, "早安", now)
        hint = monitor._observe_intent(st, {"message": {"text": "早安", "chat": {"id": 1}}}, "早安", route, ref, now,
                                       _cfg(dialogue_intent_enabled=False))
        self.assertEqual(st.user_model.get("intent_log", []), [])
        self.assertEqual(hint, "")


if __name__ == "__main__":
    unittest.main()
