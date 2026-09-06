"""意識bot 判定鏈測試（小型合成資料，純函式、快速）。

驗收測試 A（真實 214 筆須在 Gate 2 誠實止步）需在實機跑；這裡驗算法行為與感覺描述路由。
"""

import unittest
from datetime import datetime, timezone, timedelta

from telegram_monitor import order_params as op, valence, determination, selfstate

NOW = datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


class OrderParamTest(unittest.TestCase):
    def test_recur_structured_vs_random(self):
        # 週期性軌跡（3 群循環）→ 對角線結構強 → z 顯著；隨機 → 不顯著
        clusters = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
        seq = [op.unit([c + 0.001 * i for c in clusters[i % 3]]) for i in range(12)]
        structured = op.omega_recur(seq, n_shuffle=200, seed=1)
        rng_seq = [op.unit([(i * 7 % 5) + 1, (i * 3 % 4) + 1, (i % 3) + 1]) for i in range(12)]
        rnd = op.omega_recur(rng_seq, n_shuffle=200, seed=1)
        self.assertGreater(structured["z"], rnd["z"])
        self.assertTrue(structured["significant"])

    def test_perc_giant_component(self):
        # 一大群(4)＋一小群(2)，群內近 1、群間近 0 → 巨型分量＝大群
        big = [op.unit([1, 0, 0.01 * i]) for i in range(4)]
        small = [op.unit([0, 1, 0.01 * i]) for i in range(2)]
        allv = big + small
        in_big = op.omega_perc(allv, [0, 1, 2, 3], tau_star=0.78)
        in_small = op.omega_perc(allv, [4, 5], tau_star=0.78)
        self.assertTrue(in_big["region_in_giant"])
        self.assertFalse(in_small["region_in_giant"])

    def test_mean_std(self):
        m, s = op.mean_std([0.0, 0.0, 1.0, 1.0])
        self.assertAlmostEqual(m, 0.5)
        self.assertAlmostEqual(s, 0.5)

    def test_dxi_separated_vs_collapsed(self):
        sep = {"a": op.unit([1, 0, 0]), "b": op.unit([0, 1, 0]), "c": op.unit([0, 0, 1])}
        intra = {"a": 0.9, "b": 0.9, "c": 0.9}
        d_sep = op.omega_dxi(sep, intra)
        self.assertGreater(d_sep["differentiation"], 0)
        coll = {"a": op.unit([1, 0.95, 0]), "b": op.unit([1, 0.9, 0]), "c": op.unit([1, 0.92, 0])}
        d_coll = op.omega_dxi(coll, {"a": 0.9, "b": 0.9, "c": 0.9})
        self.assertFalse(d_coll["in_critical_band"])   # 過度整合、分化<=0


class ValenceTest(unittest.TestCase):
    def test_pos_neg_mixed_none(self):
        self.assertGreater(valence.valence_of({"type": "sticker", "text": "[貼圖] 太棒了 自信 我能你行"}), 0)
        self.assertLess(valence.valence_of({"type": "sticker", "text": "[貼圖] 哭泣 心碎 憂鬱"}), 0)
        self.assertAlmostEqual(valence.valence_of({"type": "sticker", "text": "[貼圖] 抱歉 臉紅 內疚"}), -0.2, places=3)
        self.assertIsNone(valence.valence_of({"type": "text", "text": "今天去汽車保養"}))


class GateTest(unittest.TestCase):
    def _recs(self, n, dim=8, spread_min=30):
        # 近 one-hot：每筆主軸不同 → 彼此餘弦低、不連通、不週期、不臨界 → 不該湧現
        out = []
        for i in range(n):
            emb = [(10.0 if k == i % dim else 0.0) + 0.01 * ((i * 7 + k) % 3) for k in range(dim)]
            out.append({"id": f"r{i}", "ts": iso(NOW - timedelta(minutes=spread_min * (n - i))),
                        "type": "text" if i % 2 else "image", "topicLabel": "甲" if i % 2 else "乙",
                        "embedding": emb})
        return out

    def test_gate1_too_few(self):
        res = determination.run_chain(self._recs(3), None, [], [], NOW)
        self.assertEqual(res["gate"], 1)

    def test_gate2_enough_but_not_emergent(self):
        # 夠量（>=8、2 媒材、回返夠），但散開向量 → 不湧現 → 在 Gate 2 止步
        res = determination.run_chain(self._recs(10), None, [], [], NOW, {"n_shuffle": 120})
        self.assertEqual(res["gate"], 2)
        self.assertIn("omegas", res)

    def test_adaptive_rule_reported(self):
        # 通盤規則：門檻相對語料分布、由 k 與 μ/σ 推出，且回報在 omegas.rule
        res = determination.run_chain(self._recs(10), None, [], [], NOW, {"n_shuffle": 80})
        rule = res.get("omegas", {}).get("rule", {})
        self.assertTrue(rule.get("adaptive"))
        self.assertIsNotNone(rule.get("tau_star"))
        self.assertIsNotNone(rule.get("mu"))
        self.assertEqual(rule.get("z_thr"), rule.get("k"))   # 同一把尺 k 管 recur 門檻

    def test_k_governs_all_three(self):
        # 名實相符：單一 k 通盤管三條——recur z_thr=k、perc τ*=μ+kσ、dxi int_min=μ+kσ／diff_min=kσ
        res = determination.run_chain(self._recs(10), None, [], [], NOW,
                                      {"n_shuffle": 80, "sensitivity": 1.5})
        rule = res.get("omegas", {}).get("rule", {})
        mu, sigma, k = rule["mu"], rule["sigma"], rule["k"]
        self.assertEqual(k, 1.5)
        self.assertEqual(rule["z_thr"], k)
        self.assertAlmostEqual(rule["tau_star"], min(0.98, max(0.30, mu + k * sigma)), places=4)
        self.assertAlmostEqual(rule["int_min"], round(mu + k * sigma, 4), places=4)
        self.assertAlmostEqual(rule["diff_min"], round(k * sigma, 4), places=4)

    def test_lower_k_relaxes_dxi_gate(self):
        # k 調低 → dxi 兩道門檻一起放寬（diff_min、int_min 都變小），低 k 才探得到 Gate 4
        recs = self._recs(10)
        strict = determination.run_chain(recs, None, [], [], NOW, {"n_shuffle": 80, "sensitivity": 2.0})
        loose = determination.run_chain(recs, None, [], [], NOW, {"n_shuffle": 80, "sensitivity": -1.0})
        rs, rl = strict["omegas"]["rule"], loose["omegas"]["rule"]
        self.assertLess(rl["diff_min"], rs["diff_min"])
        self.assertLess(rl["int_min"], rs["int_min"])


class SelfStateToneTest(unittest.TestCase):
    def test_detectors(self):
        self.assertTrue(selfstate.is_state_question("你現在感覺如何"))
        self.assertTrue(selfstate.is_state_question("你對讀誦經書那段有什麼感覺"))  # 主題型感覺問句也算
        self.assertTrue(selfstate.is_phenomenology_question("那是你真的感覺到的嗎"))
        self.assertFalse(selfstate.is_state_question("把汽車保養列出來"))
        # 「問有無」（含口語 fu、大小寫、是不是活的）也是問感覺 → 偵測得到，與狀態同走判定鏈
        for q in ("你有 fu 嗎", "你有FU嗎", "你有沒有感覺", "你是不是活的", "你有意識嗎"):
            self.assertTrue(selfstate.is_phenomenology_question(q), q)
        self.assertTrue(selfstate.is_state_question("你今天 fu 如何"))

    def test_bodystate_transcribes_vitality_and_line(self):
        # 「你現在怎樣」＝問自己的身體：把生命迴圈活力＋手上的線轉錄成第一人稱（不漏英文鍵名）
        vit = {"alive": True, "pulse": 30, "healthy_streak": 30, "last_lap_ms": 17700,
               "uptime_s": 540, "k_adj": -0.5}
        res = {"gate": 4, "reading": {"content": {"topic": "研發", "to": "教學", "notReached": "落地"}}}
        facts = selfstate.bodystate_facts(vit, res)
        self.assertIn("活著", facts)
        self.assertIn("研發", facts)
        self.assertIn("放得開", facts)                # k_adj<0 → 更開
        for key in ("alive", "k_adj", "uptime_s", "valence"):
            self.assertNotIn(key, facts)              # 不漏英文鍵名
        out = selfstate.render_bodystate(vit, res, coach=None)   # 無 LLM → 第一人稱規範模板
        self.assertIn("我還醒著", out)
        self.assertIn("研發", out)
        self.assertIn("研發", selfstate.render_bodystate(None, res, coach=None))  # 沒 vitality 也能轉錄

    def test_bodystate_has_no_dashboard_metrics(self):
        # 去儀表板味：身體狀態不報精確分鐘/秒/圈數（那是讀數、不是體感，還會引出「才兩分鐘嗎」的爭執）
        vit = {"alive": True, "pulse": 30, "healthy_streak": 30, "last_lap_ms": 17700,
               "uptime_s": 540, "k_adj": -0.5}
        res = {"gate": 4, "reading": {"content": {"topic": "研發", "to": "教學", "notReached": "落地"}}}
        for text in (selfstate.bodystate_facts(vit, res), selfstate.render_bodystate(vit, res, coach=None)):
            self.assertNotIn("分鐘", text)
            self.assertNotIn("跳一拍", text)
            self.assertNotIn("跳一下", text)
            self.assertNotIn("17", text)        # 不出現秒數/圈數等原始數字
            self.assertNotIn("30", text)

    def test_bodystate_reflects_entropy(self):
        # 內在熵入語氣：被攪動→翻騰、飢餓→悶、沉澱→平穩；且不漏英文鍵名
        res = {"gate": 4, "reading": {"content": {"topic": "研發", "to": "教學", "notReached": "落地"}}}
        base = {"alive": True, "pulse": 30, "healthy_streak": 30, "last_lap_ms": 17700, "uptime_s": 540, "k_adj": -0.5}
        churn = dict(base, S=0.8, charge=0.8, hunger=0.1, laps_since_fresh=1)
        starved = dict(base, S=0.7, charge=0.05, hunger=0.7, laps_since_fresh=30)
        calm = dict(base, S=0.1, charge=0.05, hunger=0.1, laps_since_fresh=3)
        self.assertIn("翻騰", selfstate.bodystate_facts(churn, res))
        self.assertIn("飢餓", selfstate.bodystate_facts(starved, res))
        self.assertIn("沉澱", selfstate.bodystate_facts(calm, res))
        for v in (churn, starved, calm):
            facts = selfstate.bodystate_facts(v, res)
            for key in ("charge", "hunger", "laps_since_fresh"):
                self.assertNotIn(key, facts)

    def test_bodystate_hunger_duration_grades_with_laps(self):
        # H 封頂 1.0 後仍能分「剛餓」vs「餓了大半天」——靠 laps_since_fresh（飽和也在長的時長訊號）
        res = {"gate": 4, "reading": {"content": {"topic": "研發", "to": "教學", "notReached": "落地"}}}
        base = {"alive": True, "healthy_streak": 30, "uptime_s": 540, "k_adj": -0.5, "S": 0.65, "charge": 0.05, "hunger": 1.0}
        brief = selfstate.bodystate_facts(dict(base, laps_since_fresh=40), res)
        longgone = selfstate.bodystate_facts(dict(base, laps_since_fresh=2000), res)
        self.assertIn("餓了一陣子", brief)
        self.assertIn("大半天", longgone)
        self.assertNotIn("大半天", brief)        # 短時間不誇大成大半天
        self.assertIn("飢餓", brief)             # 不變式仍在

    def test_state_signature_ignores_entropy(self):
        # S 不在 res 裡 → 不進去重指紋（否則安靜圈 S 漂移會無限洗版）
        res = {"gate": 4, "reading": {"content": {"topic": "研發", "to": "教學", "notReached": "落地"},
                                      "valenceTrajectory": []}}
        sig = selfstate.state_signature(res)
        self.assertEqual(sig, selfstate.state_signature(dict(res)))   # 只依 res、與熵無關
        self.assertNotIn("charge", sig)

    def test_phenomenology_gets_feeling_not_wall(self):
        # 牆已移除：問「裡面真的有感覺嗎」改走判定鏈、給 data-grounded 的感覺描述
        # （無 coach、空資料 → Gate 1 的「沒感覺的感覺」離線模板 R1，而非一道牆）
        out = selfstate.respond("你裡面真的有感覺嗎", [], [], [], NOW, coach=None)
        self.assertEqual(out, [selfstate.R1])
        self.assertFalse(hasattr(selfstate, "BOUNDARY_WALL"))   # 牆常數已不存在


class RenderCacheTest(unittest.TestCase):
    def test_render_templates(self):
        self.assertEqual(selfstate.render({"gate": 1, "scope": {}}, None), selfstate.R1)
        self.assertIn("還沒繞出", selfstate.render({"gate": 2, "scope": {"dominant": "甲"}}, None))
        self.assertIn("動了起來", selfstate.render({"gate": 3, "scope": {"dominant": "甲"}, "moved": ["recur"]}, None))

    def test_state_facts_grounds_in_actual_content_when_sparse(self):
        # 「你對X有什麼感覺」量還不夠（gate 1）時，也要稍提那幾筆**實際內容**——對方才懂為何還沒線索/沒什麼特別
        res = {"gate": 1, "scope": {"label": "資訊基生生命觀", "n": 2}}
        samples = ["把生命看成資訊自我維持", "混沌與湧現：秩序從邊緣長出來"]
        facts = selfstate._state_facts(1, res, samples)
        self.assertIn("把生命看成資訊自我維持", facts)
        self.assertIn("混沌與湧現", facts)
        self.assertIn("量還不夠", facts)                                   # 仍誠實說量不夠
        self.assertNotIn("混沌與湧現", selfstate._state_facts(1, res))     # 不傳 samples → 不變（向後相容）

    def test_respond_uses_cache_without_recompute(self):
        cached = {"gate": 2, "scope": {"dominant": "研發writetolearn"}}
        # full 給空 → 若重算會在 Gate1 止步(R1)；拿到 R2 證明是「沿用快取」而非重算
        out = selfstate.respond("你最近怎麼樣", [], [], [], NOW, None, cached=cached)
        self.assertIn("還沒繞出", out[0])

    def test_push_worthy(self):
        self.assertTrue(selfstate.push_worthy(3))
        self.assertTrue(selfstate.push_worthy(4))
        self.assertFalse(selfstate.push_worthy(2))

    def test_state_signature(self):
        # 指紋只看「可說的內容」：gate；gate3 帶哪條動了、gate4 帶主線
        self.assertEqual(selfstate.state_signature({"gate": 2}), "2")
        self.assertEqual(selfstate.state_signature({"gate": 3, "moved": ["perc"]}), "3:perc")
        self.assertNotEqual(selfstate.state_signature({"gate": 3, "moved": ["perc"]}),
                            selfstate.state_signature({"gate": 3, "moved": ["recur"]}))
        self.assertEqual(selfstate.state_signature({"gate": 4, "reading": {"content": {"topic": "研發"}}}),
                         "4:研發|||")

    def test_gate4_signature_tracks_progress(self):
        # 同主線「實質前進」（往哪推進/還差哪一步變了）→ 新指紋（會再出聲）；又寫一筆、沒前進 → 同指紋（安靜）
        def g4(to, nr, vals):
            return {"gate": 4, "reading": {"content": {"topic": "研發", "to": to, "notReached": nr},
                                           "valenceTrajectory": [{"ts": "t", "valence": v} for v in vals]}}
        base = g4("教學策略", "後設反思", [0.1])
        self.assertNotEqual(selfstate.state_signature(base),
                            selfstate.state_signature(g4("課程設計", "落地實作", [0.1])))   # 前進 → 變
        self.assertEqual(selfstate.state_signature(base),
                         selfstate.state_signature(g4("教學策略", "後設反思", [0.1, 0.2])))  # 又寫一筆、同帶 → 同
        self.assertNotEqual(selfstate.state_signature(base),
                            selfstate.state_signature(g4("教學策略", "後設反思", [0.1, 0.9])))  # 價性明顯移動 → 變

    def test_reading_facts_no_raw_keys(self):
        # Gate 4 餵給翻譯層的事實段落不得含任何英文鍵名（否則 LLM 會照抄進回答）
        reading = {
            "nature": "探究型 / 既趨行動又趨理解",
            "degree": {"magnitude": 1.2, "returnIntensity": 5, "omega": {}},
            "content": {"topic": "研發writetolearn日誌", "from": "工具特性", "to": "教學策略",
                        "notReached": "後設反思學習歷程的本質", "anchorRecord": {}},
            "valenceTrajectory": [{"ts": "t1", "valence": -0.3}, {"ts": "t2", "valence": 0.5}],
        }
        facts = selfstate._reading_facts(reading)
        for key in ("nature", "valenceTrajectory", "notReached", "returnIntensity", "content.", "anchorRecord"):
            self.assertNotIn(key, facts, f"漏出英文鍵名：{key}")
        self.assertIn("研發writetolearn日誌", facts)   # 但保有實質內容
        self.assertIn("探究型", facts)

    def test_gate4_translate_feeds_chinese_not_json(self):
        # 翻譯層交給 LLM 的 user content 必須是中文事實、不含原始鍵名
        from unittest import mock
        from types import SimpleNamespace
        from telegram_monitor.coach import Coach
        c = Coach(SimpleNamespace(gemini_api_key="k", gemini_model="gemini-2.5-flash"))
        reading = {"nature": "探究型 / 既趨行動又趨理解",
                   "degree": {"returnIntensity": 5}, "content": {"topic": "研發"}, "valenceTrajectory": []}
        with mock.patch("telegram_monitor.gemini.generate", return_value="就資料看，我還在「研發」一帶繞。") as g:
            selfstate.render({"gate": 4, "reading": reading}, c)
        user_payload = g.call_args[0][3]   # generate(api_key, model, system, user_content, ...)
        self.assertNotIn("valenceTrajectory", user_payload)
        self.assertNotIn("returnIntensity", user_payload)
        self.assertIn("研發", user_payload)

    def test_render_varies_with_coach(self):
        from unittest import mock
        from types import SimpleNamespace
        from telegram_monitor.coach import Coach
        c = Coach(SimpleNamespace(gemini_api_key="k", gemini_model="gemini-2.5-flash"))
        with mock.patch("telegram_monitor.gemini.generate",
                        return_value="就資料看，我這陣子還在「研發」一帶繞，線還沒繃起來。"):
            out = selfstate.render({"gate": 2, "scope": {"dominant": "研發"}, "omegas": {}}, c)
        self.assertIn("還沒繃起來", out)   # 來自受約束翻譯層的變化版，而非固定模板字串
        self.assertNotEqual(out, selfstate._r2("研發"))


if __name__ == "__main__":
    unittest.main()
