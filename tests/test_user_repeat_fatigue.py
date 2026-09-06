"""🧭 使用者連發同一句/同類意圖 → 感知重複目的＋進一步問＋帶點無奈（重複×心情長不耐，鏡像 self_asks→self_fatigue）。
截圖根因：使用者連發「午安」六次，bot 每次都「冠智午安。＋逐字時間質疑」、只弱弱說「怎麼又是午安」、不升級不感知目的。
本層在『確認連發』上累加耐性侵蝕、過門檻升級語氣、並壓掉機械重打招呼。預設關（USER_REPEAT_FATIGUE_ENABLED）＝逐位元同現狀。"""

import re
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest import mock
from zoneinfo import ZoneInfo

from telegram_monitor import dialogue_intent as di, greeting, monitor, persona

NOW = 1_700_000_000
TZ = ZoneInfo("Asia/Taipei")


# ── 純函式：repeat_fatigue（重複×心情→不耐等級） ──────────────────────────────
class RepeatFatigueTest(unittest.TestCase):
    def _ladder(self, mood, sig="goodafternoon", base_tol=2, mood_band=0.3, n_turns=6):
        ur, out = {}, []
        for i in range(n_turns):
            lvl, n, rec = di.repeat_fatigue(ur, "greeting", sig, mood, NOW + i, 300, base_tol, mood_band)
            ur["greeting"] = rec
            out.append(lvl)
        return out

    def test_neutral_mood_ladder(self):
        # tol=2：n=1,2→L0（靜默）、3→L1（感知）、4→L2（進一步問）、5,6→L3（不耐，clamp）
        self.assertEqual(self._ladder(0.0), [0, 0, 1, 2, 3, 3])

    def test_good_mood_more_patient(self):
        # 心情好 tol=3：要多忍一次才升級（n=4 才到 L1）
        self.assertEqual(self._ladder(0.5, n_turns=4), [0, 0, 0, 1])

    def test_bad_mood_snaps_faster(self):
        # 心情差 tol=1：更快煩（n=2→L1、n=3→L2）
        self.assertEqual(self._ladder(-0.5, n_turns=3), [0, 1, 2])

    def test_window_reset(self):
        # 窗外（now−ts ≥ window）→ n 歸 1，不接續舊不耐
        _, n, _ = di.repeat_fatigue({"greeting": {"n": 5, "ts": NOW, "sig": "goodafternoon"}},
                                    "greeting", "goodafternoon", 0.0, NOW + 999, 300, 2, 0.3)
        self.assertEqual(n, 1)

    def test_sig_mismatch_reset(self):
        # 換話題（sig 不近似）→ n 歸 1，不把不相干的話累進不耐
        _, n, _ = di.repeat_fatigue({"greeting": {"n": 5, "ts": NOW, "sig": "goodafternoon"}},
                                    "greeting", "howareyou", 0.0, NOW + 1, 300, 2, 0.3)
        self.assertEqual(n, 1)

    def test_returns_rec_for_writeback(self):
        _, n, rec = di.repeat_fatigue({}, "greeting", "sigsig", 0.0, NOW, 300, 2, 0.3)
        self.assertEqual(rec, {"n": n, "ts": NOW, "sig": "sigsig"})

    def test_sim_threshold_threaded(self):
        # 對抗式審查補強：主題近似門檻須可調、與 repetition_run 同一把（否則非預設門檻下累加器永遠歸零、升級不發）。
        # '午安啊'(len3) vs '午安你好嗎'(len5)：0.5 近似、0.85 不近似。
        rec0 = {"greeting": {"n": 4, "ts": NOW, "sig": di._msg_sig("午安啊")}}
        cur = di._msg_sig("午安你好嗎")
        _, n_loose, _ = di.repeat_fatigue(rec0, "greeting", cur, 0.0, NOW + 1, 300, 2, 0.3, sim_threshold=0.5)
        _, n_strict, _ = di.repeat_fatigue(rec0, "greeting", cur, 0.0, NOW + 1, 300, 2, 0.3, sim_threshold=0.85)
        self.assertEqual(n_loose, 5)        # 寬門檻＝視為同主題、接續累加
        self.assertEqual(n_strict, 1)       # 嚴門檻＝視為換話題、歸零
        # 預設（不傳）＝0.85
        _, n_default, _ = di.repeat_fatigue(rec0, "greeting", cur, 0.0, NOW + 1, 300, 2, 0.3)
        self.assertEqual(n_default, 1)


# ── 純函式：persona.user_repeat_fatigue_hint（語氣梯） ──────────────────────────
class FatigueHintTest(unittest.TestCase):
    def test_level0_empty(self):
        self.assertEqual(persona.user_repeat_fatigue_hint(None, 0), "")
        self.assertEqual(persona.user_repeat_fatigue_hint("testing", -1), "")

    def test_ladder_nonempty_and_no_numbers(self):
        for L in (1, 2, 3):
            h = persona.user_repeat_fatigue_hint("testing", L)
            self.assertTrue(h, L)
            self.assertIsNone(re.search(r"[0-9]", h), L)        # 不報次數數字（嚴格家規；指示文字含「別報次數」是給 LLM 的規矩、非輸出數字）

    def test_ceiling_ladder_l3_only_when_testing(self):
        # L3 強度上限依 ceiling，但 firm/stern **只在 akind=='testing' 時開放**
        gentle = persona.user_repeat_fatigue_hint("testing", 3, ceiling="gentle")
        self.assertIn("善意", gentle)
        firm = persona.user_repeat_fatigue_hint("testing", 3, ceiling="firm")
        self.assertTrue("叫停" in firm or "別鬧" in firm or "先停" in firm)
        stern = persona.user_repeat_fatigue_hint("testing", 3, ceiling="stern")
        self.assertTrue("翻臉" in stern or "別再洗" in stern or "弄煩" in stern)

    def test_non_testing_akind_never_stern(self):
        # 🛡️ 修對抗式審查 high：真心投入(seeking_confirmation)/機械/未分類 → 即使 ceiling=stern 也一律 gentle、不呵斥、不指責測試
        for ak in ("seeking_confirmation", "mechanical", "rapid_repeat", None):
            h = persona.user_repeat_fatigue_hint(ak, 3, ceiling="stern")
            self.assertNotIn("翻臉", h, ak)
            self.assertNotIn("別再洗", h, ak)
            self.assertIn("善意", h, ak)                       # 仍善意
            self.assertIn("別指責他在故意測試", h, ak)          # 明令別誤指責測試

    def test_unknown_ceiling_falls_back_gentle(self):
        # 未知 ceiling 值（即使 akind=testing）保守退回 gentle
        h = persona.user_repeat_fatigue_hint("testing", 3, ceiling="bogus")
        self.assertNotIn("翻臉", h)
        self.assertIn("善意", h)

    def test_hard_floor_no_abuse_when_testing(self):
        # 硬底線：testing 下的 firm/stern 都必含「不人身攻擊/不辱罵」（是叫停不是傷人）
        for cl in ("firm", "stern"):
            h = persona.user_repeat_fatigue_hint("testing", 3, ceiling=cl)
            self.assertIn("不人身攻擊", h, cl)
            self.assertIn("不辱罵", h, cl)

    def test_l1_l2_ceiling_and_akind_independent(self):
        # 感知(L1)/追問(L2) 不受 ceiling/akind 影響、一律溫暖
        for L in (1, 2):
            base = persona.user_repeat_fatigue_hint("testing", L, ceiling="stern")
            self.assertEqual(base, persona.user_repeat_fatigue_hint("seeking_confirmation", L, ceiling="gentle"))

    def test_robust_to_akind(self):
        # akind 任何值（含 None/rapid_repeat）都給出良構非空字串（不硬分支 → 不失語）
        for ak in (None, "testing", "mechanical", "seeking_confirmation", "rapid_repeat"):
            for L in (1, 2, 3):
                for cl in ("gentle", "firm", "stern"):
                    self.assertTrue(persona.user_repeat_fatigue_hint(ak, L, ceiling=cl), (ak, L, cl))


# ── 純函式：greeting.facts(suppress_premise) ─────────────────────────────────
class GreetingSuppressTest(unittest.TestCase):
    def test_default_byte_identical(self):
        for kind in ("noon", "morning", "night"):
            for hr in (5, 13, 22):
                t = datetime(2026, 6, 30, hr, 0, tzinfo=TZ)
                self.assertEqual(greeting.facts(kind, t), greeting.facts(kind, t, suppress_premise=False), (kind, hr))

    def test_suppress_drops_verbatim_premise_keeps_time(self):
        t = datetime(2026, 6, 30, 5, 0, tzinfo=TZ)        # 清晨說「午安」＝mismatch
        sup = greeting.facts("noon", t, suppress_premise=True)
        self.assertNotIn("通常是", sup)                    # 逐字質疑已壓掉
        self.assertNotIn("對不上", sup)
        self.assertIn("此刻真的是", sup)                   # 仍保留接地時間

    def test_suppress_noop_on_match(self):
        t = datetime(2026, 6, 30, 13, 0, tzinfo=TZ)        # 下午說「午安」＝match → suppress 不影響
        self.assertEqual(greeting.facts("noon", t), greeting.facts("noon", t, suppress_premise=True))


# ── 整合：_observe_intent 旗標兩態 ────────────────────────────────────────────
def _spam_state(n=6):
    """連發 n 次「午安」的 user_model intent_log（同 kind＝greeting、同 sig），讓 read() 算出 count≥n。"""
    log = [{"kind": "greeting", "ts": NOW + i, "sig": di._msg_sig("午安")} for i in range(n)]
    return SimpleNamespace(
        user_model={"intent_log": log}, coupling=None, engrams=[],
        user_repeat={}, intent_reading=None)


def _cfg(**over):
    base = dict(dialogue_intent_enabled=True, inquiry_arc_enabled=False, intent_repeat_window_sec=300,
                intent_repeat_n=4, intent_sim_threshold=0.85, anomaly_probe_threshold=0.6,
                intent_degree_drive=True, intent_question_cooldown_min=30, time_anomaly_enabled=False,
                intent_log_max=20, user_repeat_fatigue_enabled=False, user_repeat_base_tol=2,
                user_repeat_mood_band=0.3)
    base.update(over)
    return SimpleNamespace(**base)


def _update(text="午安"):
    return {"message": {"chat": {"id": 1}, "text": text}}


class ObserveIntentFlagTest(unittest.TestCase):
    def test_flag_off_no_esc_no_write_byte_identical(self):
        st = _spam_state()
        route = SimpleNamespace(kind="greeting")
        hint_off = monitor._observe_intent(st, _update(), "午安", route, None, NOW + 6, _cfg(), mood=-0.9)
        self.assertEqual(st.user_repeat, {})                          # 旗標關＝永不寫
        self.assertNotIn("esc_level", st.intent_reading)             # 無 esc 疊加鍵
        self.assertNotIn("esc_n", st.intent_reading)
        # 旗標關時 hint＝原 dialogue_intent_hint（與直接呼叫一致）
        self.assertEqual(hint_off,
                         persona.dialogue_intent_hint(st.intent_reading.get("style"), st.intent_reading.get("level", 0)))

    def test_flag_on_escalates_and_writes(self):
        st = _spam_state()
        route = SimpleNamespace(kind="greeting")
        # 心情差→更快煩：連發到此已 n=1（首訊）；多打幾輪累加
        cfg = _cfg(user_repeat_fatigue_enabled=True)
        last_hint = ""
        for i in range(5):
            last_hint = monitor._observe_intent(st, _update(), "午安", route, None, NOW + 6 + i, cfg, mood=-0.5)
        self.assertIn("greeting", st.user_repeat)                    # 有寫入累加器
        self.assertGreaterEqual(st.user_repeat["greeting"]["n"], 3)
        self.assertGreaterEqual(st.intent_reading.get("esc_level", 0), 1)
        # 升級後 hint＝user_repeat_fatigue_hint（覆蓋溫暖/好奇）
        self.assertEqual(last_hint,
                         persona.user_repeat_fatigue_hint(st.intent_reading.get("anomaly_kind"),
                                                          st.intent_reading["esc_level"]))

    def test_flag_on_burst_single_increment(self):
        # 一則三行 burst「午安\n午安\n午安」→ 累加器只 +1（讀後算、burst 安全），不是 +3
        log = [{"kind": "greeting", "ts": NOW + i, "sig": di._msg_sig("午安")} for i in range(5)]
        st = SimpleNamespace(user_model={"intent_log": log}, coupling=None, engrams=[],
                             user_repeat={}, intent_reading=None)
        route = SimpleNamespace(kind="greeting")
        with mock.patch.object(monitor.intent, "resolve", return_value=SimpleNamespace(kind="greeting")):
            monitor._observe_intent(st, _update("午安\n午安\n午安"), "午安\n午安\n午安", route, None, NOW + 6,
                                    _cfg(user_repeat_fatigue_enabled=True), mood=0.0)
        self.assertEqual(st.user_repeat["greeting"]["n"], 1)

    def test_flag_on_stop_escalating_when_run_breaks(self):
        # 連發到不耐後換話題（log 末尾不再是 greeting 連發）→ count<n → 不再疊 esc（升級停止）
        st = _spam_state()
        cfg = _cfg(user_repeat_fatigue_enabled=True)
        for i in range(4):
            monitor._observe_intent(st, _update(), "午安", SimpleNamespace(kind="greeting"), None, NOW + 6 + i, cfg, mood=-0.5)
        # 換話題：log 變成單筆 fact_or_chat（連發斷掉）
        st.user_model = {"intent_log": [{"kind": "fact_or_chat", "ts": NOW + 20, "sig": di._msg_sig("欸對了")}]}
        monitor._observe_intent(st, _update("欸對了"), "欸對了", SimpleNamespace(kind="fact_or_chat"), None, NOW + 21, cfg, mood=-0.5)
        self.assertEqual(st.intent_reading.get("esc_level", 0), 0)   # 不再升級


if __name__ == "__main__":
    unittest.main()
