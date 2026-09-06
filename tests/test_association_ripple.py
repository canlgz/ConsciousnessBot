"""🌊 內外搭配的「漣漪式」聯想湧現（association 層強化）：
外石（對話）＋遞迴石（最近聯想端點）＝弱觸發源，活化既有真實橋，改變「哪條橋、何時」湧現。

不變式（全程無 LLM、純向量/字串/標籤）：
- 漣漪只活化既有真實橋（端點本就 ∈ cents），絕不造對話節點/主題；
- 湧現內容仍引真實 anchor 片段；
- 漣漪 gain < new_record gain；漣漪只在本圈未被 new_record/revisit 觸發時施（嚴格 elif）；
- 加性可關：ripple_topics=None → 與現狀逐位元相同。
"""

import random
import unittest

from telegram_monitor import association as assoc


# 8 維方向：甲、乙「靠近但不同」（cos≈0.69）；丙與甲/乙 正交（cos≈0、不成橋）。
DIR_A = [1.0, 0.3, 0, 0, 0, 0, 0, 0]
DIR_B = [0.5, 1.0, 0, 0, 0, 0, 0, 0]
DIR_U = [0, 0, 0, 0, 1.0, 0.3, 0, 0]


def _iso(i):
    return f"2026-06-20T12:00:{i % 60:02d}.000Z"


def _emb(base, rng, jit=0.04):
    return [b + rng.uniform(-jit, jit) for b in base]


def _recs(topic, base, n, rng):
    return [{"topicLabel": topic, "type": "text", "ts": _iso(i),
             "text": f"{topic}-{i} 一些內容字", "embedding": _emb(base, rng)} for i in range(n)]


def _converging_cents(seed=5):
    rng = random.Random(seed)
    return assoc.topic_centroids(_recs("甲", DIR_A, 5, rng) + _recs("乙", DIR_B, 5, rng))


# ── conversation_ripple_topics：抽真實標籤、忽略 cents 外、尊重窗、空→set() ────────────
class ConversationRippleTopicsTest(unittest.TestCase):
    LABELS = {"惠中寺", "禪修", "研發"}

    def test_label_full_substring_hit(self):
        convo = [{"role": "user", "text": "今天又去了惠中寺一趟", "ts": 100.0}]
        got = assoc.conversation_ripple_topics(convo, self.LABELS, now_ts=100.0, window_sec=1800)
        self.assertEqual(got, {"惠中寺"})

    def test_topic_not_in_cents_ignored(self):
        # 對話提到「水管維修」但它不在 cents → 不漣漪（誠實退化、絕不造節點）
        convo = [{"role": "user", "text": "水管維修好麻煩", "ts": 100.0}]
        got = assoc.conversation_ripple_topics(convo, self.LABELS, now_ts=100.0, window_sec=1800)
        self.assertEqual(got, set())

    def test_respects_window(self):
        convo = [{"role": "user", "text": "惠中寺", "ts": 0.0}]      # 太久以前
        got = assoc.conversation_ripple_topics(convo, self.LABELS, now_ts=100000.0, window_sec=1800)
        self.assertEqual(got, set())

    def test_empty_history_is_empty_set(self):
        self.assertEqual(assoc.conversation_ripple_topics([], self.LABELS, 100.0, 1800), set())
        self.assertEqual(assoc.conversation_ripple_topics(None, self.LABELS, 100.0, 1800), set())

    def test_result_always_subset_of_cents(self):
        convo = [{"role": "user", "text": "惠中寺 禪修 研發 還有別的主題", "ts": 100.0}]
        got = assoc.conversation_ripple_topics(convo, self.LABELS, 100.0, 1800)
        self.assertTrue(got <= self.LABELS)

    def test_best_topic_recorded_strengthen_still_subset(self):
        # best_topic 補強仍 ∩ cents：recorded 含 cents 沒有的主題（<min_recs 被丟）→ 不會洩漏
        recorded = [{"topicLabel": "惠中寺"} for _ in range(5)] + [{"topicLabel": "邊緣主題"}]
        convo = [{"role": "user", "text": "惠中寺的事", "ts": 100.0}]
        got = assoc.conversation_ripple_topics(convo, self.LABELS, 100.0, 1800, recorded=recorded)
        self.assertTrue(got <= self.LABELS)
        self.assertIn("惠中寺", got)

    def test_two_char_coincidence_not_misfired(self):
        # 兩字巧合不應誤射：對話「今天天氣」不含完整標籤「研發/禪修/惠中寺」→ 不命中（防 best_topic 誤命中）
        convo = [{"role": "user", "text": "今天天氣真好", "ts": 100.0}]
        recorded = [{"topicLabel": lab} for lab in self.LABELS for _ in range(5)]
        got = assoc.conversation_ripple_topics(convo, self.LABELS, 100.0, 1800, recorded=recorded)
        self.assertEqual(got, set())


# ── recursive_ripple_topics：拆 recent_insights 端點、∩ cents、尊重窗、last_insight 次要 ──
class RecursiveRippleTopicsTest(unittest.TestCase):
    LABELS = {"甲", "乙", "丙"}

    def test_pair_endpoints_extracted(self):
        recent = [{"pair": "甲|乙", "ts": 100.0}]
        got = assoc.recursive_ripple_topics(recent, self.LABELS, 100.0, 1800)
        self.assertEqual(got, {"甲", "乙"})

    def test_endpoint_outside_cents_dropped(self):
        # 端點掉出 cents（舊主題離開湖面）→ 不漣漪
        recent = [{"pair": "甲|已消失", "ts": 100.0}]
        got = assoc.recursive_ripple_topics(recent, self.LABELS, 100.0, 1800)
        self.assertEqual(got, {"甲"})

    def test_respects_window(self):
        recent = [{"pair": "甲|乙", "ts": 0.0}]
        got = assoc.recursive_ripple_topics(recent, self.LABELS, 100000.0, 1800)
        self.assertEqual(got, set())

    def test_last_insight_secondary_source(self):
        got = assoc.recursive_ripple_topics([], self.LABELS, 100.0, 1800,
                                            last_insight={"event": {"a": "甲", "b": "丙"}, "ts": 100.0})
        self.assertEqual(got, {"甲", "丙"})

    def test_empty_is_empty_set(self):
        self.assertEqual(assoc.recursive_ripple_topics(None, self.LABELS, 100.0, 1800), set())


# ── observe(ripple_topics=...)：漣漪推進、未命中不受影響、gain 較弱、雙重觸發護欄 ──────────
class ObserveRippleTest(unittest.TestCase):
    def test_ripple_gain_less_than_new_record_gain(self):
        self.assertLess(assoc._RIPPLE_GAIN, assoc._STRENGTH_GAIN)

    def test_ripple_pushes_untriggered_bridge(self):
        # 沒有新記寫、沒有 revisit，但端點在 ripple → 橋 strength 上升、_trig_why 帶 ripple、變可湧現觸發
        a, cents = assoc.Associations(), _converging_cents()
        a.observe(cents, k=2.0, ingest_changed=False, revisit_topic=None, now_ts=1.0,
                  ripple_topics={"甲"})
        br = list(a.bridges.values())[0]
        self.assertGreater(br["strength"], 0.0)
        self.assertEqual(br["support"], 0)        # 漣漪只調 strength（時機/醒目），不計 support（嚴謹接地）
        self.assertTrue((br["_trig_why"] or "").startswith("ripple:"))

    def test_ripple_conv_vs_insight_label(self):
        a, cents = assoc.Associations(), _converging_cents()
        a.observe(cents, k=2.0, ingest_changed=False, revisit_topic=None, now_ts=1.0,
                  ripple_topics={"甲"}, ripple_kinds={"甲": "insight"})
        self.assertEqual(list(a.bridges.values())[0]["_trig_why"], "ripple:insight")
        a2 = assoc.Associations()
        a2.observe(cents, k=2.0, ingest_changed=False, revisit_topic=None, now_ts=1.0,
                   ripple_topics={"甲"})
        self.assertEqual(list(a2.bridges.values())[0]["_trig_why"], "ripple:conv")

    def test_ripple_not_touching_bridge_no_effect(self):
        # ripple 主題不是任何橋的端點 → 橋完全不受影響：與「沒有 ripple」逐位元相同
        a1, cents = assoc.Associations(), _converging_cents()
        a1.observe(cents, k=2.0, ingest_changed=False, revisit_topic=None, now_ts=1.0,
                   ripple_topics={"無關主題"})
        a2 = assoc.Associations()
        a2.observe(cents, k=2.0, ingest_changed=False, revisit_topic=None, now_ts=1.0)
        # 兩者都因 strength=0 被 _prune 汰除 → 皆無存活橋（未命中＝零作用）
        self.assertEqual(set(a1.bridges), set(a2.bridges))

    def test_ripple_added_once_even_if_both_endpoints_in_set(self):
        # a、b 都在 ripple_set → 只加一次 _RIPPLE_GAIN（不加兩次）
        a, cents = assoc.Associations(), _converging_cents()
        a.observe(cents, k=2.0, ingest_changed=False, revisit_topic=None, now_ts=1.0,
                  ripple_topics={"甲", "乙"})
        br = list(a.bridges.values())[0]
        self.assertEqual(br["support"], 0)        # 漣漪不計 support；a、b 都在集合也只加一次 strength
        self.assertAlmostEqual(br["strength"], assoc._RIPPLE_GAIN, places=6)

    def test_double_count_guard_new_record_not_extra_ripple(self):
        # 已被 new_record 觸發的橋本圈不再額外吃漣漪 gain：strength 不超過純 new_record 路徑（嚴格 elif）。
        a1, cents = assoc.Associations(), _converging_cents()
        a1.observe(cents, k=2.0, ingest_changed=True, revisit_topic=None, now_ts=1.0)  # 純 new_record
        s_plain = list(a1.bridges.values())[0]["strength"]
        a2 = assoc.Associations()
        a2.observe(cents, k=2.0, ingest_changed=True, revisit_topic=None, now_ts=1.0,
                   ripple_topics={"甲", "乙"})                                        # new_record + ripple 同圈
        br2 = list(a2.bridges.values())[0]
        self.assertAlmostEqual(br2["strength"], s_plain, places=6)                    # 未雙重計分
        self.assertEqual(br2["support"], 1)                                           # support 不灌水
        self.assertEqual(br2["_trig_why"], "new_record")                             # 維持 new_record，不被 ripple 覆蓋

    def test_revisit_not_overridden_by_ripple(self):
        a, cents = assoc.Associations(), _converging_cents()
        a.observe(cents, k=2.0, ingest_changed=False, revisit_topic="甲", now_ts=1.0,
                  ripple_topics={"甲"})
        br = list(a.bridges.values())[0]
        self.assertEqual(br["_trig_why"], "revisit:甲")
        self.assertEqual(br["support"], 1)


# ── 自我遞迴（思緒鏈）：上一湧現端點當 ripple → 下圈該橋前進 ──────────────────────────
class SelfRecursiveTest(unittest.TestCase):
    def test_prior_insight_endpoint_advances_bridge(self):
        a, cents = assoc.Associations(), _converging_cents()
        # 第一圈：純漣漪推進到某 strength
        a.observe(cents, k=2.0, ingest_changed=False, revisit_topic=None, now_ts=1.0,
                  ripple_topics={"甲"})
        s1 = list(a.bridges.values())[0]["strength"]
        # 下一圈再把同一湧現端點當 ripple（思緒鏈）→ 即使有半衰減，strength 仍前進
        a.observe(cents, k=2.0, ingest_changed=False, revisit_topic=None, now_ts=2.0,
                  ripple_topics={"甲"})
        s2 = list(a.bridges.values())[0]["strength"]
        self.assertGreater(s2, s1)                                       # 思緒鏈：strength 仍前進（再計時/醒目）
        self.assertEqual(list(a.bridges.values())[0]["support"], 0)      # 但純漣漪不累 support（不算真實沉澱）

    def test_pure_ripple_cannot_emerge_without_real_support(self):
        # 嚴謹接地（owner 定奪）：純漣漪（無 new_record/revisit）永遠不累 support →
        # 即使一直聊它/前一聯想一直牽動它、跑很多圈，也**不會**湧現＝聊天不能憑空製造洞見。
        a, cents = assoc.Associations(), _converging_cents()
        ev = None
        for _ in range(30):
            ev = a.observe(cents, k=2.0, ingest_changed=False, revisit_topic=None, now_ts=10.0,
                           confirm_laps=2, min_support=2, ripple_topics={"甲", "乙"}) or ev
        self.assertIsNone(ev)
        self.assertEqual(list(a.bridges.values())[0]["support"], 0)

    def test_ripple_retimes_real_supported_bridge_with_real_anchor(self):
        # 漣漪能讓「已有真實累積（revisit 沉澱出 support）」的潛在連結浮上來、重新計時 → 湧現；
        # why_now 帶 ripple，但錨仍是**真實片段**（內容永遠接地）。
        a, cents = assoc.Associations(), _converging_cents()
        for t in (1.0, 2.0):                                  # 先用真實 revisit 累出 support≥min_support
            a.observe(cents, k=2.0, ingest_changed=False, revisit_topic="甲", now_ts=t,
                      confirm_laps=2, min_support=2)
        ev = None
        for i in range(4):                                    # 再用漣漪推一把（再計時）→ 湧現
            ev = a.observe(cents, k=2.0, ingest_changed=False, revisit_topic=None, now_ts=3.0 + i,
                           confirm_laps=2, min_support=2, ripple_topics={"甲", "乙"}) or ev
        self.assertIsNotNone(ev)
        self.assertTrue((ev["why_now"] or "").startswith("ripple:"))
        self.assertTrue(ev["anchor_a"]["text"])              # 錨是真實片段、非空
        self.assertTrue(ev["anchor_b"]["text"])


# ── 旗標關 / ripple_topics=None → observe 與現狀逐位元相同（回歸）─────────────────────
class RippleOffBitIdenticalTest(unittest.TestCase):
    def _run(self, **kw):
        a, cents = assoc.Associations(), _converging_cents()
        evs = [a.observe(cents, k=2.0, ingest_changed=True, revisit_topic=None, now_ts=1.0,
                         confirm_laps=2, min_support=2, **kw) for _ in range(6)]
        return [e for e in evs if e], a

    def test_none_equals_not_passing(self):
        f_default, a1 = self._run()
        f_none, a2 = self._run(ripple_topics=None)
        self.assertEqual([(e["a"], e["b"], e["kind"], e["why_now"]) for e in f_default],
                         [(e["a"], e["b"], e["kind"], e["why_now"]) for e in f_none])
        self.assertEqual(set(a1.bridges), set(a2.bridges))
        for k in a1.bridges:
            self.assertAlmostEqual(a1.bridges[k]["strength"], a2.bridges[k]["strength"], places=9)
            self.assertEqual(a1.bridges[k]["support"], a2.bridges[k]["support"])
            self.assertEqual(a1.bridges[k]["_trig_why"], a2.bridges[k]["_trig_why"])

    def test_none_why_now_stays_in_legacy_domain(self):
        # ripple_topics=None 時 why_now 只可能是 new_record（不出現 ripple:*）
        f_none, _ = self._run(ripple_topics=None)
        for e in f_none:
            self.assertIn(e["why_now"], ("new_record",))


# ── 接地：對話/遞迴提到但 cents 沒有的主題，絕不生成橋/節點 ───────────────────────────
class RippleGroundingTest(unittest.TestCase):
    def test_ripple_topic_outside_cents_creates_no_bridge(self):
        a, cents = assoc.Associations(), _converging_cents()
        before = set(a.bridges)
        a.observe(cents, k=2.0, ingest_changed=False, revisit_topic=None, now_ts=1.0,
                  ripple_topics={"從沒存在的主題"})
        # 只會出現由真實 cents 偵測到的橋（甲|乙），不會憑空多出含「從沒存在的主題」的橋
        for key in a.bridges:
            self.assertNotIn("從沒存在的主題", key)
        self.assertTrue(all("甲" in k and "乙" in k for k in a.bridges))


# ── insight_facts/insight_text 對 ripple why_now：加性、無 ripple 時逐位元同現狀 ──────────
class RippleFactsTest(unittest.TestCase):
    def _ev(self, why_now="new_record"):
        return {"a": "甲", "b": "乙", "kind": "blend", "why_now": why_now, "cos": 0.7, "support": 3,
                "anchor_a": {"text": "甲那句"}, "anchor_b": {"text": "乙那句"}, "signal": {}, "itype": None}

    def test_new_record_facts_unchanged(self):
        # 加入 ripple 分支後，new_record 文案逐位元不變
        f = assoc.insight_facts(self._ev("new_record"))
        self.assertIn("他剛又寫了東西、把它觸發起來", f)

    def test_ripple_conv_facts_branch(self):
        f = assoc.insight_facts(self._ev("ripple:conv"))
        self.assertIn("被你剛聊到的牽動", f)
        self.assertIn("甲那句", f)        # 片段仍來自 ev anchor

    def test_ripple_insight_text_branch(self):
        t = assoc.insight_text(self._ev("ripple:insight"))
        self.assertIn("上一個念頭", t)


if __name__ == "__main__":
    unittest.main()
