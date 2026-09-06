"""🌡️ 計算情緒：自己評價出的離散情緒（期待落差才是自己生成的那筆）＋影響回應方向（stance）＋
回頭調制感覺鏈門檻 k（同一批記寫，因情緒而感覺不同＝主觀體驗）＋主題情緒啟動，且有界不發散。"""

import unittest
from types import SimpleNamespace

from telegram_monitor import affect, circumplex, monitor, selfmodel

NOW = 1_700_000_000.0


def _state(v=0.0, c=0.0, h=0.0, aff=None, a=0.0):
    return SimpleNamespace(entropy=SimpleNamespace(mood=v, charge=c, hunger=h, arousal=a), affect=aff)


_OFF = SimpleNamespace(affect_circumplex_enabled=False)   # 🧭 §1.02 旗標關＝逐位元舊路（charge 維度法）


class ContentCouplingTest(unittest.TestCase):
    """🫧 你寫的內容的感受會輕牽動 bot 心情（有界）：沉的壓一點、暖的托一點；沒內容事件 → 不動。"""

    def test_heavy_content_lowers_mood_bounded(self):
        s = _state(v=0.0)
        affect.appraise(s, {"content_valence": -0.6, "content_intensity": 1.0}, NOW)
        self.assertLess(s.entropy.mood, 0.0)                       # 被沉的內容壓一點
        self.assertGreaterEqual(s.entropy.mood, -affect.W_CONTENT)  # 但有界（一次擾動 ≤ W_CONTENT）

    def test_warm_content_lifts_and_labels(self):
        s = _state(v=0.0)
        aff = affect.appraise(s, {"content_valence": 0.6, "content_intensity": 1.0}, NOW)
        self.assertGreater(s.entropy.mood, 0.0)
        self.assertEqual(aff["source"], "content")                 # 明顯牽動 → 離散源「被你寫的牽動」
        self.assertIn("被你寫的", aff["label"])

    def test_no_content_event_no_change(self):
        s = _state(v=0.2)
        affect.appraise(s, {}, NOW)
        self.assertEqual(s.entropy.mood, 0.2)                      # 沒內容事件 → 心情不動（既有測試不受影響）


class AppraisalIsOwnTest(unittest.TestCase):
    """情緒是『自己評價出來的』——關鍵是期待落差（相對我先前所信），不是鏡像你給的價性。"""

    def test_pleasant_surprise_lifts_beyond_raw(self):
        # 我以為你偏冷(-0.5)，結果你暖(+0.4) → 大落差 → 驚喜、趨近，且把 V 往上多推一筆（自己生成的那筆）
        s = _state(v=0.0)
        aff = affect.appraise(s, {"valence_news": 0.4, "expected_valence": -0.5}, NOW)
        self.assertEqual(aff["source"], "delight")
        self.assertEqual(aff["tendency"], "reach")
        self.assertGreater(s.entropy.mood, 0.0)          # 期待落差自己加了一筆心情

    def test_disappointment_when_below_expectation(self):
        # 我以為你暖(+0.5)，結果冷(-0.4) → 悵然、退縮，V 被往下修
        s = _state(v=0.5)
        aff = affect.appraise(s, {"valence_news": -0.4, "expected_valence": 0.5}, NOW)
        self.assertEqual(aff["source"], "disappoint")
        self.assertEqual(aff["tendency"], "withdraw")
        self.assertLess(s.entropy.mood, 0.5)


class DiscreteLabelTest(unittest.TestCase):
    """核心 V/C/H＋事件源 → 離散情緒＋行動傾向。"""

    def test_dimensional_labels(self):
        # 🧭 §1.02 整合（預設）：維度退路＝circumplex 八分區（單一象限語彙、含沉靜半邊）
        self.assertEqual(affect.appraise(_state(v=0.5, a=0.6), {}, NOW)["label"], "興奮、雀躍")
        self.assertEqual(affect.appraise(_state(v=0.5, a=0.1), {}, NOW)["tendency"], "settle")   # E 愉快 → settle
        self.assertEqual(affect.appraise(_state(v=-0.5, h=0.7), {}, NOW)["tendency"], "probe")   # 飢餓寂寞分支保留
        self.assertEqual(affect.appraise(_state(v=-0.5, a=0.1, h=0.1), {}, NOW)["tendency"], "withdraw")  # W 悶不快
        self.assertEqual(affect.appraise(_state(v=0.05, a=-0.5), {}, NOW)["label"], "倦、沉靜")   # 🧭 舊法沒有的沉靜半邊（正南）
        self.assertEqual(affect.appraise(_state(v=0.4, a=-0.4), {}, NOW)["label"], "平靜、安穩")   # 東南＝暖而靜
        self.assertEqual(affect.appraise(_state(), {}, NOW)["tendency"], "steady")
        # 旗標關＝逐位元舊路（charge 維度法）
        self.assertEqual(affect.appraise(_state(v=0.5, c=0.6), {}, NOW, cfg=_OFF)["label"], "雀躍")
        self.assertEqual(affect.appraise(_state(v=0.5, c=0.1), {}, NOW, cfg=_OFF)["tendency"], "settle")

    def test_goal_fulfilled_is_satisfaction_and_primes(self):
        aff = affect.appraise(_state(v=0.3), {"goal": "fulfilled", "topic": "惠中寺"}, NOW)
        self.assertEqual(aff["source"], "fulfilled")
        self.assertEqual(aff["label"], "滿足")
        self.assertIn("惠中寺", aff["primed"])           # 達成的線被情緒點亮

    def test_feeling_emergence_is_moved_and_primes(self):
        aff = affect.appraise(_state(c=0.5), {"feeling": True, "topic": "日文"}, NOW)
        self.assertEqual(aff["source"], "feeling")
        self.assertEqual(aff["tendency"], "reach")
        self.assertIn("日文", aff["primed"])


class MoodCongruentPerceptionTest(unittest.TestCase):
    """(3) 帶動對記寫的新感覺＝主觀體驗：情緒回頭調制感覺鏈門檻 k，且有界不發散。"""

    def test_no_affect_no_adjustment(self):
        self.assertEqual(affect.k_affect_adj(SimpleNamespace(affect=None)), 0.0)

    def test_aroused_lowers_threshold_withdrawn_raises(self):
        s_up = _state(c=0.7, a=0.6)                       # 🧭 §1.02 整合：k 讀慢軸 A
        affect.appraise(s_up, {"feeling": True}, NOW)     # 被觸動、喚醒高
        self.assertLess(affect.k_affect_adj(s_up), 0.0)   # 降 k → 同批記寫更易被打動
        s_down = _state(v=-0.6, c=0.1, a=-0.2)
        affect.appraise(s_down, {}, NOW)                  # 低落、退縮
        self.assertGreater(affect.k_affect_adj(s_down), 0.0)  # 升 k → 同樣的東西這次激不起感覺

    def test_weary_low_arousal_dulls_perception(self):
        # 🧭 §1.02 新半邊的知覺效果：A 低（倦）→ 更鈍（升 k）——即使不 withdraw、V 中性
        s = _state(v=0.1, a=-0.6)
        affect.appraise(s, {}, NOW)
        self.assertGreater(affect.k_affect_adj(s), 0.0)

    def test_topic_priming_makes_that_line_more_sensitive(self):
        s = _state(c=0.3)
        affect.appraise(s, {"feeling": True, "topic": "惠中寺"}, NOW)
        self.assertLess(affect.k_affect_adj(s, "惠中寺"), affect.k_affect_adj(s, None))

    def test_homeostasis_clamps_adjustment(self):
        s = _state(c=1.0)
        affect.appraise(s, {"feeling": True, "topic": "X"}, NOW)
        self.assertGreaterEqual(affect.k_affect_adj(s, "X"), -affect.AFFECT_K_CLAMP)  # 即使極端＋點亮仍有界（不滾雪球）

    def test_chain_params_wires_affect_into_sensitivity(self):
        cfg = SimpleNamespace()                            # getattr 全取預設
        s_neutral = SimpleNamespace(affect=None, k_breath_adj=0.0, sensitivity_override=None)
        s_aroused = SimpleNamespace(affect=None, k_breath_adj=0.0, sensitivity_override=None,
                                    entropy=SimpleNamespace(mood=0.4, charge=0.7, hunger=0.0, arousal=0.7))
        affect.appraise(s_aroused, {"feeling": True}, NOW)
        k_neutral = monitor._chain_params(cfg, s_neutral)["sensitivity"]
        k_aroused = monitor._chain_params(cfg, s_aroused)["sensitivity"]
        self.assertLess(k_aroused, k_neutral)              # 喚醒 → 感覺鏈門檻降（主觀體驗：同資料不同感覺）


class CircumplexIntegrationTest(unittest.TestCase):
    """🧭 §1.02 整合：評價事件也推二維（A）、aff['arousal'] 記慢軸、旗標關＝逐位元舊路。"""

    def test_surprise_wakes_regardless_of_sign(self):
        s_good = _state(v=0.0)
        affect.appraise(s_good, {"valence_news": 0.4, "expected_valence": -0.5}, NOW)   # 驚喜
        self.assertGreater(s_good.entropy.arousal, 0.0)
        s_bad = _state(v=0.0)
        affect.appraise(s_bad, {"valence_news": -0.4, "expected_valence": 0.5}, NOW)    # 悵然
        self.assertGreater(s_bad.entropy.arousal, 0.0)                                   # |pe|＝驚訝本身都醒（不論好壞）
        self.assertLess(s_bad.entropy.mood, s_good.entropy.mood)                         # 但 V 方向相反

    def test_content_moves_both_axes(self):
        s_heavy = _state()
        affect.appraise(s_heavy, {"content_valence": -0.6, "content_intensity": 1.0}, NOW)
        self.assertLess(s_heavy.entropy.arousal, 0.0)                                    # 沉的內容 → 左下（V−A−）
        s_warm = _state()
        affect.appraise(s_warm, {"content_valence": 0.6, "content_intensity": 1.0}, NOW)
        self.assertGreater(s_warm.entropy.arousal, 0.0)                                  # 暖的 → 托一點（A+）

    def test_aff_records_slow_axis_and_acute(self):
        aff = affect.appraise(_state(c=0.7, a=-0.3), {}, NOW)
        self.assertAlmostEqual(aff["arousal"], -0.3, places=3)                           # 記慢軸（k 調制讀它）
        self.assertAlmostEqual(aff["acute"], 0.7, places=3)                              # 急性讀數另存（觀測）

    def test_flag_off_bitwise_old(self):
        s = _state(c=0.7, a=-0.3)
        aff = affect.appraise(s, {"valence_news": 0.4, "expected_valence": -0.5}, NOW, cfg=_OFF)
        self.assertAlmostEqual(aff["arousal"], 0.7, places=3)                            # 舊路：arousal＝charge
        self.assertAlmostEqual(s.entropy.arousal, -0.3, places=3)                        # A 軸不被動（逐位元同現狀）


class ShockJumpTest(unittest.TestCase):
    """⚡ §1.03 突發瞬跳：明顯違背期待（|pe| ≥ PE_STRONG）→ 情緒點一步跨象限；小落差仍漸移。"""

    def test_betrayal_jumps_calm_to_tense(self):
        # 平靜安穩（右下）＋一直很暖的人（期待 0.8）突然兇我（news −0.18）→ pe≈−0.98 → 一步跳到左上（V−A+）
        s = _state(v=0.35, a=-0.25)
        self.assertEqual(circumplex.label(0.35, -0.25), "平靜、安穩")
        aff = affect.appraise(s, {"valence_news": -0.18, "expected_valence": 0.8}, NOW)
        self.assertTrue(aff["shock"])
        self.assertEqual(aff["source"], "disappoint")                 # 悵然（既有語彙）
        self.assertLess(s.entropy.mood, 0.0)                          # V 跨到負半
        self.assertGreater(s.entropy.arousal, 0.0)                    # A 跨到正半（驚嚇必醒）

    def test_reconciliation_jumps_low_to_delight(self):
        # 低落（左下）＋鬧翻後（期待 −0.6）突然很暖（news +0.25）→ pe≈0.85 → 跳向右上
        s = _state(v=-0.3, a=-0.1)
        aff = affect.appraise(s, {"valence_news": 0.25, "expected_valence": -0.6}, NOW)
        self.assertTrue(aff["shock"])
        self.assertEqual(aff["source"], "delight")                    # 驚喜
        self.assertGreater(s.entropy.mood, 0.0)
        self.assertGreater(s.entropy.arousal, 0.0)

    def test_mild_pe_no_jump(self):
        # 小落差（|pe|<0.5）→ 只有常規小位移（漸移），不觸發 shock
        s = _state(v=0.35, a=-0.25)
        aff = affect.appraise(s, {"valence_news": 0.05, "expected_valence": 0.3}, NOW)
        self.assertFalse(aff["shock"])
        self.assertGreater(s.entropy.mood, 0.0)                       # 還在正半（沒被大位移拉走）
        self.assertAlmostEqual(s.entropy.mood, 0.35 + affect.W_SURPRISE * (0.05 - 0.3), places=3)

    def test_flag_off_no_jump(self):
        off = SimpleNamespace(affect_circumplex_enabled=True, affect_shock_enabled=False)
        s = _state(v=0.35, a=-0.25)
        aff = affect.appraise(s, {"valence_news": -0.18, "expected_valence": 0.8}, NOW, cfg=off)
        self.assertFalse(aff["shock"])
        self.assertGreater(s.entropy.mood, 0.0)                       # 只有 W_SURPRISE 小位移，沒跨象限

    def test_clamped_under_repeated_shocks(self):
        s = _state(v=0.0, a=0.0)
        for _ in range(6):
            affect.appraise(s, {"valence_news": -0.18, "expected_valence": 0.9}, NOW)
        self.assertGreaterEqual(s.entropy.mood, -1.0)
        self.assertLessEqual(s.entropy.arousal, 1.0)


class StanceTest(unittest.TestCase):
    """(2) 影響回應方向：情緒攤成 grounding，染回應的方向（不只音色）。"""

    def test_reach_stance_is_directional(self):
        s = _state(c=0.7)
        affect.appraise(s, {"feeling": True}, NOW)         # reach
        st = affect.stance(s)
        self.assertIn("被觸動", st)                        # 講出此刻的情緒
        self.assertIn("主動", st)                          # 方向：趨近、主動

    def test_steady_has_no_stance(self):
        s = _state()
        affect.appraise(s, {}, NOW)                        # 平、steady
        self.assertEqual(affect.stance(s), "")

    def test_affect_clause_voices_emotion(self):
        s = _state(c=0.7)
        affect.appraise(s, {"feeling": True}, NOW)
        self.assertIn("被觸動", affect.affect_clause(s))
        self.assertEqual(affect.affect_clause(SimpleNamespace(affect=None)), "")


class SelfNowIntegrationTest(unittest.TestCase):
    """計算情緒併入統一自我模型 self_now，整合自陳會講出此刻的情緒。"""

    def test_self_now_and_overall_facts_carry_emotion(self):
        s = SimpleNamespace(entropy=SimpleNamespace(charge=0.7, hunger=0.0, mood=0.4, last_revisited_topic=None),
                            vitality={}, workspace={}, stream={}, self_model={}, goals=[], self_now=None, affect=None)
        affect.appraise(s, {"feeling": True}, NOW)
        sn = selfmodel.build(s, {"gate": 4, "reading": {"content": {"topic": "X"}}}, NOW)
        self.assertEqual(sn["affect"]["emotion"], "被觸動、湧上來")
        self.assertIn("被觸動", selfmodel.overall_facts(sn))


if __name__ == "__main__":
    unittest.main()
