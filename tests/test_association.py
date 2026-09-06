"""💡 聯想湧現：跨主題語意橋的累積（純計算、無 LLM）→ 湧現一個使用者沒寫過的連結 → Aha 出聲。

累積即條件：橋強度過自適應門檻＋夠多支撐沉澱＋連續確認＋本圈被觸發（新記寫/自我刺激繞回）＋扣得上前景。
合成 embedding 驗證：靠近中的不同主題會累積成橋並在條件滿足時湧現一次；無關主題零湧現（偽陽性護欄）。
"""

import os
import random
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from telegram_monitor import ac, association as assoc, monitor, volition
from telegram_monitor.state import State

NOW_DT = datetime(2026, 6, 20, 4, 0, 0, tzinfo=timezone.utc)   # 台北 12:00（白天、非深夜＝可主動）


class FakeClient:
    def __init__(self, dry_run=False):
        self.sent, self.dry_run = [], dry_run

    def send(self, text):
        self.sent.append(text)
        return True

# 8 維方向：甲、乙「靠近但不同」（cos≈0.69）；無關 與 甲/乙 正交（cos≈0）。
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


# 跳躍：丙、丁兩方向遠（cos≈0.14），第三主題「橋」夾在正中間當踏腳石（DIR_MID＝C+D 方向）
DIR_C = [1.0, 0, 0, 0, 0.4, 0, 0, 0]
DIR_D = [0, 1.0, 0, 0, 0.4, 0, 0, 0]
DIR_MID = [1.0, 1.0, 0, 0, 0.8, 0, 0, 0]


def _recs_val(topic, base, n, rng, summary=None, category=None, valenced=None):
    """帶情緒登錄（reactions[].summary→valence）與大類（category）的記寫；valenced＝只前幾筆貼情緒（測支撐門檻）。"""
    out = []
    for i in range(n):
        r = {"topicLabel": topic, "type": "text", "ts": _iso(i),
             "text": f"{topic}-{i} 一些內容字", "embedding": _emb(base, rng)}
        if summary and (valenced is None or i < valenced):
            r["reactions"] = [{"summary": summary}]
        if category:
            r["category"] = category
        out.append(r)
    return out


def _leap_cents(seed=7):
    rng = random.Random(seed)
    return assoc.topic_centroids(_recs("丙", DIR_C, 5, rng) + _recs("丁", DIR_D, 5, rng)
                                 + _recs("橋", DIR_MID, 5, rng))


def _cat_cents(seed=11, cat_a="研究", cat_b="生活", warm_b=None):
    rng = random.Random(seed)
    full = (_recs_val("甲", DIR_A, 4, rng, category=cat_a)
            + _recs_val("乙", DIR_B, 4, rng, category=cat_b, summary=warm_b))
    return assoc.topic_centroids(full, min_recs=2)


class PureFnTest(unittest.TestCase):
    def test_topic_centroids_groups_and_drops_thin(self):
        rng = random.Random(1)
        full = _recs("甲", DIR_A, 5, rng) + _recs("乙", DIR_B, 5, rng) + _recs("丙", DIR_A, 2, rng)
        cents = assoc.topic_centroids(full)
        self.assertEqual(set(cents), {"甲", "乙"})            # 丙 < 4 筆 → 丟掉
        self.assertEqual(len(cents["甲"]["centroid"]), 8)
        self.assertGreater(cents["甲"]["intra"], 0.9)         # 同主題很凝聚

    def test_bridge_only_between_distinct_converging(self):
        cands = assoc.candidate_bridges(_converging_cents(), k=2.0)
        self.assertEqual(len(cands), 1)
        a, b, cos, mid = cands[0]
        self.assertEqual({a, b}, {"甲", "乙"})
        self.assertEqual(len(mid), 8)
        self.assertGreater(cos, 0.15)

    def test_no_bridge_for_unrelated_topics(self):
        rng = random.Random(3)
        cents = assoc.topic_centroids(_recs("甲", DIR_A, 5, rng) + _recs("無關", DIR_U, 5, rng))
        self.assertEqual(assoc.candidate_bridges(cents, k=2.0), [])   # cos≈0 < 絕對地板 → 不成橋

    def test_no_bridge_for_near_identical_topics(self):
        rng = random.Random(4)
        cents = assoc.topic_centroids(_recs("甲", DIR_A, 5, rng) + _recs("甲類似", DIR_A, 5, rng))
        self.assertEqual(assoc.candidate_bridges(cents, k=2.0), [])   # cos>1−gap → 幾乎同主題、不算跨


class AccumulateTest(unittest.TestCase):
    def test_strength_accumulates_on_trigger(self):
        a, cents = assoc.Associations(), _converging_cents()
        for _ in range(5):
            a.observe(cents, k=2.0, ingest_changed=True, revisit_topic=None, now_ts=0.0)
        br = list(a.bridges.values())[0]
        self.assertGreaterEqual(br["support"], 5)
        self.assertGreater(br["strength"], 0.6)

    def test_no_emergence_without_trigger(self):
        a, cents = assoc.Associations(), _converging_cents()
        evs = [a.observe(cents, k=2.0, ingest_changed=False, revisit_topic=None, now_ts=0.0) for _ in range(20)]
        self.assertEqual([e for e in evs if e], [])          # 從不被觸發 → 從不湧現（也被半衰減/汰除）

    def test_emergence_needs_confirm_and_support(self):
        a, cents = assoc.Associations(), _converging_cents()
        evs = [a.observe(cents, k=2.0, ingest_changed=True, revisit_topic=None, now_ts=100.0,
                         confirm_laps=4, min_support=3) for _ in range(10)]
        fired = [e for e in evs if e]
        self.assertEqual(len(fired), 1)                      # 只湧現一次（黏著）
        self.assertGreaterEqual(next(i for i, e in enumerate(evs) if e), 3)   # 不早於 confirm_laps 滿足
        self.assertEqual(fired[0]["why_now"], "new_record")

    def test_emergence_via_revisit_endpoint(self):
        a, cents = assoc.Associations(), _converging_cents()
        evs = [a.observe(cents, k=2.0, ingest_changed=False, revisit_topic="甲", now_ts=100.0,
                         confirm_laps=2, min_support=2) for _ in range(10)]
        fired = [e for e in evs if e]
        self.assertTrue(fired)
        self.assertEqual(fired[0]["why_now"], "revisit:甲")

    def test_event_shape_has_real_anchors(self):
        a, cents, ev = assoc.Associations(), _converging_cents(), None
        for _ in range(8):
            ev = a.observe(cents, k=2.0, ingest_changed=True, revisit_topic=None, now_ts=1.0,
                           confirm_laps=1, min_support=1) or ev
        self.assertEqual({ev["a"], ev["b"]}, {"甲", "乙"})
        self.assertTrue(ev["anchor_a"]["text"] and ev["anchor_b"]["text"])   # 真實記寫文字
        self.assertIn("support", ev)

    def test_summary_roundtrips_and_reseeds(self):
        a, cents = assoc.Associations(), _converging_cents()
        for _ in range(8):
            a.observe(cents, k=2.0, ingest_changed=True, revisit_topic=None, now_ts=1.0,
                      confirm_laps=1, min_support=1)
        s = a.summary()
        self.assertGreaterEqual(s["emerged_total"], 1)
        self.assertLessEqual(len(s["bridges"]), 8)
        a2 = assoc.Associations(summary=s)                   # 跨重生種回
        self.assertEqual(a2.emerged_total, s["emerged_total"])
        self.assertTrue(a2.bridges)

    def test_easy_mode_emerges_fast(self):
        # 觀察模式：第一圈被觸發即湧現（confirm=1/support=1/門檻寬）
        a, cents = assoc.Associations(), _converging_cents()
        ev = a.observe(cents, k=2.0, ingest_changed=True, revisit_topic=None, now_ts=1.0,
                       confirm_laps=1, min_support=1, easy=True)
        self.assertIsNotNone(ev)
        self.assertEqual({ev["a"], ev["b"]}, {"甲", "乙"})


class GroundingTest(unittest.TestCase):
    def test_relevance_gate(self):
        ev = {"a": "甲", "b": "乙"}
        self.assertTrue(assoc.is_relevant_ev(ev, "甲", []))
        self.assertTrue(assoc.is_relevant_ev(ev, None, ["乙"]))
        self.assertFalse(assoc.is_relevant_ev(ev, "丙", ["丁"]))

    def test_facts_grounded_and_hedged(self):
        ev = {"a": "甲", "b": "乙", "anchor_a": {"text": "甲的某句"}, "anchor_b": {"text": "乙的某句"},
              "cos": 0.7, "strength": 0.9, "support": 3, "why_now": "new_record"}
        facts = assoc.insight_facts(ev)
        self.assertIn("甲的某句", facts)
        self.assertIn("乙的某句", facts)
        self.assertIn("假設", facts)                          # 強制標假設、非事實
        self.assertNotIn("線三", facts)                       # 不引第三條
        txt = assoc.insight_text(ev)
        self.assertIn("甲", txt)
        self.assertIn("乙", txt)
        self.assertIn("連想", txt)


# ── 出聲路徑：把一個固定 event 注入 state.insight_pending，驗 _insight_emit 的閘/記憶/回饋 ──────
EV = {"a": "甲", "b": "乙",
      "anchor_a": {"text": "甲那條我寫過的某句", "ts": "2026-06-19T10:00:00Z"},
      "anchor_b": {"text": "乙那條我寫過的某句", "ts": "2026-06-18T09:00:00Z"},
      "cos": 0.7, "strength": 0.8, "support": 3, "why_now": "new_record"}
AHA = "欸我突然想到——你的「甲」跟「乙」好像是同一回事？這是我冒出來的連想、一個假設啦。"


class StubReader:
    def __init__(self, full):
        self._full = full

    def load_embedding_records(self, owner):
        return self._full


def _cfg(easy=False, seed_goal=True):
    return SimpleNamespace(association_enabled=True, association_cooldown_min=360,
                           association_confirm_laps=4, association_min_support=3,
                           notify_cooldown_min=30, association_seed_goal=seed_goal,
                           association_easy=easy, selfstate_sensitivity=2.0, timezone="Asia/Taipei")


def _coach(line=AHA, enabled=True):
    return SimpleNamespace(enabled=enabled, voice_insight=lambda facts, history, tone="": line)


def _state():
    s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
    s.owner_folder_id = "F"
    s.convo_history = []
    return s                            # last_user_msg_ts=0（不在場）、last_push/insight_ts=0（過冷卻）皆走預設


class InsightEmitTest(unittest.TestCase):
    def _pending(self, state, ts=None):
        state.insight_pending = {"event": EV, "ts": (NOW_DT.timestamp() if ts is None else ts)}

    def test_speaks_on_emergence_and_remembers(self):
        s, client = _state(), FakeClient()
        self._pending(s)
        monitor._insight_emit(client, s, _cfg(), _coach(), NOW_DT)
        self.assertTrue(client.sent and client.sent[0].startswith("💡 "))     # 帶 💡 出聲
        self.assertEqual(s.convo_history[-1]["role"], "model")                 # ★ 進記憶（修主動否認 bug）
        self.assertTrue(s.convo_history[-1]["text"].startswith("💡 "))
        self.assertIn("連想", s.convo_history[-1]["text"])
        self.assertIsNone(s.insight_pending)                                   # 說完清掉
        self.assertEqual(s.last_insight_ts, NOW_DT.timestamp())               # 起自有冷卻
        self.assertEqual(s.last_push_ts, NOW_DT.timestamp())                  # 與🌀/感覺共用冷卻（不疊）

    def test_suppressed_when_assoc_pref_active(self):
        # 🚫 截圖根因：使用者說過「停止聯想」→ bot 不主動出聲聯想（丟掉暫存湧現、不一面道歉一面再犯）
        from telegram_monitor import plasticity
        s, client = _state(), FakeClient()
        self._pending(s)
        s.engrams = plasticity.reinforce([], plasticity.KIND_PREF, "assoc",
                                         value="別自己亂聯想", now_ts=NOW_DT.timestamp())
        monitor._insight_emit(client, s, _cfg(), _coach(), NOW_DT)
        self.assertEqual(client.sent, [])               # 不出聲（被偏好 gate）
        self.assertIsNone(s.insight_pending)            # 暫存湧現丟掉、不事後補吐

    def test_not_suppressed_without_pref(self):
        # 沒這條偏好 → 照常出聲（逐位元同現狀）
        s, client = _state(), FakeClient()
        self._pending(s)
        monitor._insight_emit(client, s, _cfg(), _coach(), NOW_DT)
        self.assertTrue(client.sent and client.sent[0].startswith("💡 "))

    def test_seeds_goal_and_tightens_F(self):
        s, client = _state(), FakeClient()
        self._pending(s)
        before = ac.maintenance_pressure(s)
        monitor._insight_emit(client, s, _cfg(seed_goal=True), _coach(), NOW_DT)
        gs = volition.active(s)
        self.assertTrue(any(g.get("kind") == "understand_link" and g.get("subject") == "甲×乙" for g in gs))
        after = ac.maintenance_pressure(s)
        self.assertLess(after["F"], before["F"])             # 長一個被朝向的意圖 → F 扣合更緊（單調）
        self.assertLessEqual(after["overall"], before["overall"])   # 只收緊、從不放鬆任何一層

    def test_no_seed_goal_when_disabled(self):
        s, client = _state(), FakeClient()
        self._pending(s)
        monitor._insight_emit(client, s, _cfg(seed_goal=False), _coach(), NOW_DT)
        self.assertTrue(client.sent)                          # 還是有說
        self.assertEqual(volition.active(s), [])              # 但不長意圖（ASSOCIATION_SEED_GOAL=0）

    def test_no_llm_falls_back_to_template(self):
        s, client = _state(), FakeClient()
        self._pending(s)
        monitor._insight_emit(client, s, _cfg(), None, NOW_DT)   # 無教練／LLM
        joined = "".join(client.sent)
        self.assertIn("甲", joined)
        self.assertIn("乙", joined)
        self.assertIn("連想", joined)                          # 退誠實模板、仍接地標假設
        self.assertEqual(s.convo_history[-1]["role"], "model")   # 模板照樣進記憶

    def test_silent_when_no_pending(self):
        s, client = _state(), FakeClient()
        monitor._insight_emit(client, s, _cfg(), _coach(), NOW_DT)
        self.assertEqual(client.sent, [])
        self.assertEqual(s.convo_history, [])

    def test_silent_within_own_cooldown(self):
        s, client = _state(), FakeClient()
        self._pending(s)
        s.last_insight_ts = NOW_DT.timestamp() - 60          # 1 分鐘前剛湧現（< 360 分自有冷卻）
        monitor._insight_emit(client, s, _cfg(), _coach(), NOW_DT)
        self.assertEqual(client.sent, [])
        self.assertIsNotNone(s.insight_pending)              # 冷卻擋下、待之後再說（不丟）

    def test_silent_within_shared_cooldown(self):
        s, client = _state(), FakeClient()
        self._pending(s)
        s.last_push_ts = NOW_DT.timestamp() - 60             # 1 分鐘前剛推過別則（< 30 分共用反連發）
        monitor._insight_emit(client, s, _cfg(), _coach(), NOW_DT)
        self.assertEqual(client.sent, [])                    # 不緊接著別則推播（不洗版、不與🌀疊）

    def test_silent_past_ttl(self):
        s, client = _state(), FakeClient()
        self._pending(s, ts=NOW_DT.timestamp() - (monitor._INSIGHT_TTL_SEC + 60))
        monitor._insight_emit(client, s, _cfg(), _coach(), NOW_DT)
        self.assertEqual(client.sent, [])
        self.assertIsNone(s.insight_pending)                 # 那個當下過了 → 安靜清掉（不事後補述）

    def test_silent_when_user_present(self):
        s, client = _state(), FakeClient()
        self._pending(s)
        s.last_user_msg_ts = NOW_DT.timestamp() - 10         # 你剛說過話 → 在場，主動獨白讓位
        monitor._insight_emit(client, s, _cfg(), _coach(), NOW_DT)
        self.assertEqual(client.sent, [])
        self.assertIsNotNone(s.insight_pending)


class PipelineTest(unittest.TestCase):
    """整合：observe 累積 → 湧現 → integrate 暫存 pending → feel 出聲＋進記憶＋長意圖（easy 一拍走完）。"""

    def test_easy_pipeline_speaks_remembers_seeds_goal(self):
        rng = random.Random(7)
        reader = StubReader(_recs("甲", DIR_A, 5, rng) + _recs("乙", DIR_B, 5, rng))
        s, cfg, coach = _state(), _cfg(easy=True), _coach()
        cycle = {"now": NOW_DT, "data": {"meta": {"lastIngestTs": 1}}, "self_stim_fired": False}
        monitor._association_step(reader, s, cfg, cycle)       # 新記寫觸發（ingest 從 None→1）
        self.assertIsNotNone(s.insight_pending)               # easy：第一個被觸發的拍即湧現
        self.assertEqual({s.insight_pending["event"]["a"], s.insight_pending["event"]["b"]}, {"甲", "乙"})
        client = FakeClient()
        monitor._insight_emit(client, s, cfg, coach, NOW_DT)
        self.assertTrue(client.sent and client.sent[0].startswith("💡 "))
        self.assertEqual(s.convo_history[-1]["role"], "model")
        self.assertTrue(any(g.get("kind") == "understand_link" for g in volition.active(s)))

    def test_no_emit_without_owner_folder(self):
        rng = random.Random(7)
        reader = StubReader(_recs("甲", DIR_A, 5, rng) + _recs("乙", DIR_B, 5, rng))
        s, cfg = _state(), _cfg(easy=True)
        s.owner_folder_id = None                              # 沒歸戶資料夾 → 整個累積/湧現都不啟動
        monitor._association_step(reader, s, cfg, {"now": NOW_DT, "data": {"meta": {}}, "self_stim_fired": False})
        self.assertIsNone(s.insight_pending)


# ── 跳躍 leap：遠的兩條線靠第三條線的真實記寫（踏腳石）接起來 ────────────────────────
class LeapTest(unittest.TestCase):
    def test_leap_pairs_distant_via_stepping_stone(self):
        cands = assoc.leap_candidates(_leap_cents(), k=0.0)
        pairs = {frozenset((a, b)): (kind, sig) for a, b, kind, cos, dir_, sig in cands}
        self.assertIn(frozenset(("丙", "丁")), pairs)         # 遠的兩條被接起來了
        kind, sig = pairs[frozenset(("丙", "丁"))]
        self.assertEqual(kind, "leap")
        self.assertEqual(sig["stone"]["topic"], "橋")          # 踏腳石是第三條真實的線
        self.assertTrue(sig["stone"]["text"] and sig["anchor_a"]["text"] and sig["anchor_b"]["text"])

    def test_no_leap_for_isolated_distant_pair(self):
        rng = random.Random(8)                                 # 沒有第三條線夾在中間 → 只是兩條無關的線、不是跳躍
        cents = assoc.topic_centroids(_recs("丙", DIR_C, 5, rng) + _recs("丁", DIR_D, 5, rng))
        self.assertEqual(assoc.leap_candidates(cents, k=0.0), [])

    def test_leap_excludes_blend_band(self):                   # 越靠越近的（blend 帶內）不算跳躍
        self.assertEqual(assoc.leap_candidates(_converging_cents(), k=0.0), [])


# ── 衝突 conflict：相近的兩條線、情緒卻相反 ─────────────────────────────────────────
class ConflictTest(unittest.TestCase):
    def _cents(self, seed=9, warm="讚", heavy="難過", cat="心情", base_b=DIR_A, valenced=None):
        rng = random.Random(seed)
        full = (_recs_val("暖", DIR_A, 4, rng, summary=warm, category=cat, valenced=valenced)
                + _recs_val("沉", base_b, 4, rng, summary=heavy, category=cat, valenced=valenced))
        return assoc.topic_centroids(full, min_recs=2)

    def test_conflict_opposite_valence_close_topics(self):
        cands = assoc.conflict_candidates(self._cents(), k=2.0)
        self.assertEqual(len(cands), 1)
        a, b, kind, cos, dir_, sig = cands[0]
        self.assertEqual(kind, "conflict")
        self.assertEqual(sig["warm"]["topic"], "暖")
        self.assertEqual(sig["heavy"]["topic"], "沉")
        self.assertGreater(sig["warm"]["valence"], 0)
        self.assertLess(sig["heavy"]["valence"], 0)

    def test_no_conflict_when_same_sign(self):                 # 兩邊都暖 → 沒有衝突
        self.assertEqual(assoc.conflict_candidates(self._cents(heavy="開心"), k=2.0), [])

    def test_no_conflict_below_support(self):                  # 每邊只有 1 筆情緒登錄 < _CONF_MIN_SUPPORT(2)
        self.assertEqual(assoc.conflict_candidates(self._cents(valenced=1), k=2.0), [])

    def test_conflict_via_same_category_even_if_far(self):     # 語意遠、但同大類也算「相近」
        cands = assoc.conflict_candidates(self._cents(base_b=DIR_U), k=2.0)
        self.assertEqual(len(cands), 1)
        self.assertTrue(cands[0][5]["same_category"])


# ── (pair, kind) 各自獨立累積；一圈只發跨 kind 最強的一條 ───────────────────────────
class AccumKindTest(unittest.TestCase):
    def _both_cents(self, seed=13):
        # 甲乙 cos≈0.69（落在 blend 帶）且同大類、情緒相反（同時 blend＋conflict）
        rng = random.Random(seed)
        full = (_recs_val("甲", DIR_A, 5, rng, summary="讚", category="研究")
                + _recs_val("乙", DIR_B, 5, rng, summary="難過", category="研究"))
        return assoc.topic_centroids(full, min_recs=2)

    def test_same_pair_two_kinds_independent(self):
        a, cents = assoc.Associations(), self._both_cents()
        a.observe(cents, k=0.0, ingest_changed=True, revisit_topic=None, now_ts=0.0)
        kinds = {b["kind"] for b in a.bridges.values() if {b["a"], b["b"]} == {"甲", "乙"}}
        self.assertEqual(kinds, {"blend", "conflict"})          # 同一對主題、兩種橋並存
        self.assertEqual(len({assoc._bridge_key("甲", "乙", kk) for kk in kinds}), 2)

    def test_one_event_per_lap_across_kinds(self):
        a, cents = assoc.Associations(), self._both_cents()
        evs = [a.observe(cents, k=0.0, ingest_changed=True, revisit_topic=None, now_ts=1.0,
                         confirm_laps=1, min_support=1) for _ in range(6)]
        fired = [e for e in evs if e]
        self.assertEqual(len(fired), 2)                         # 兩條橋各湧現一次（黏著），但每圈最多一條
        self.assertEqual(len({e["kind"] for e in fired}), 2)    # 一條 blend、一條 conflict


# ── 靈感類型分類（axis 2，由訊號自選、強的優先） ─────────────────────────────────
class ITypeTest(unittest.TestCase):
    def test_problem_when_endpoint_in_concerns(self):
        self.assertEqual(assoc.classify_itype("甲", "乙", "blend", {}, "new_record", ["甲"], _cat_cents()), "problem")

    def test_epiphany_on_revisit(self):
        self.assertEqual(assoc.classify_itype("甲", "乙", "blend", {}, "revisit:甲", [], _cat_cents()), "epiphany")

    def test_life_on_category_mismatch(self):
        self.assertEqual(assoc.classify_itype("甲", "乙", "blend", {}, "new_record", [], _cat_cents()), "life")

    def test_emotion_on_strong_valence(self):
        cents = _cat_cents(cat_a="研究", cat_b="研究")            # 同大類→life 不觸發，獨立測 emotion
        sig = {"warm": {"valence": 0.9}, "heavy": {"valence": -0.9}}
        self.assertEqual(assoc.classify_itype("甲", "乙", "conflict", sig, "new_record", [], cents), "emotion")

    def test_precedence_problem_over_all(self):
        sig = {"warm": {"valence": 0.9}, "heavy": {"valence": -0.9}}
        self.assertEqual(assoc.classify_itype("甲", "乙", "conflict", sig, "revisit:甲", ["甲"], _cat_cents()), "problem")

    def test_neutral_default_link(self):
        self.assertEqual(assoc.classify_itype("甲", "乙", "blend", {}, "new_record", [], _cat_cents(cat_a="研究", cat_b="研究")), "link")


# ── 接地/標假設不變式：每種操作的 facts/text 都含真實片段＋火花標記、不引第四主題 ──────────
def _ev(kind, **kw):
    base = {"a": "甲", "b": "乙", "kind": kind, "anchor_a": {"text": "甲那句"}, "anchor_b": {"text": "乙那句"},
            "cos": 0.3, "strength": 0.8, "support": 3, "why_now": "new_record", "itype": "link", "signal": {}}
    base.update(kw)
    return base


PAIR = assoc.pair_key({"a": "甲", "b": "乙"})   # 排序鍵（乙<甲，故＝"乙|甲"）；別在斷言裡寫死字面


class SparkGroundingTest(unittest.TestCase):
    def test_blend_facts_grounded_hedged(self):
        f = assoc.insight_facts(_ev("blend"))
        self.assertIn("甲那句", f)
        self.assertIn("乙那句", f)
        self.assertIn("假設", f)
        self.assertNotIn("丙", f)                               # 不引第三/四主題

    def test_leap_facts_name_stepping_stone(self):
        f = assoc.insight_facts(_ev("leap", signal={"stone": {"text": "橋那句", "topic": "橋"}}))
        self.assertIn("甲那句", f)
        self.assertIn("橋那句", f)                              # 具名踏腳石（唯一允許的第三條真實的線）
        self.assertIn("假設", f)
        self.assertIn("連得起來", f)

    def test_conflict_facts_show_valence_contrast(self):
        f = assoc.insight_facts(_ev("conflict", signal={
            "warm": {"topic": "甲", "text": "甲那句", "valence": 0.9},
            "heavy": {"topic": "乙", "text": "乙那句", "valence": -0.9}, "valence_gap": 1.8}))
        self.assertIn("正向", f)
        self.assertIn("負向", f)
        self.assertIn("張力", f)
        self.assertIn("假設", f)

    def test_each_kind_text_hedged(self):
        for kind in ("blend", "leap", "conflict"):
            txt = assoc.insight_text(_ev(kind, signal={"stone": {"text": "橋那句", "topic": "橋"}}))
            self.assertIn("假設", txt)
            self.assertIn("甲", txt)
            self.assertIn("乙", txt)


# ── 出聲路徑：leap/conflict 事件也照常 💡 出聲＋進記憶＋長 kind-aware 意圖收緊 F ──────────
class SparkEmitTest(unittest.TestCase):
    def _emit(self, ev, coach="default"):
        s, client = _state(), FakeClient()
        s.insight_pending = {"event": ev, "ts": NOW_DT.timestamp()}
        monitor._insight_emit(client, s, _cfg(), (_coach() if coach == "default" else coach), NOW_DT)
        return s, client

    def test_leap_event_speaks_remembers_seeds_kind_goal(self):
        s, client = self._emit(_ev("leap", a="丙", b="丁",
                                    signal={"stone": {"text": "橋那句", "topic": "橋"}}))
        self.assertTrue(client.sent and client.sent[0].startswith("💡 "))
        self.assertEqual(s.convo_history[-1]["role"], "model")
        g = next(g for g in volition.active(s) if g["subject"] == "丙×丁")
        self.assertEqual(g["kind"], "understand_link")          # subject/kind 不變（AC 不動）
        self.assertEqual(g["spark_kind"], "leap")
        self.assertIn("跳接", g["desire"])                       # 文案隨 kind 變

    def test_conflict_event_tightens_F(self):
        s = _state()
        s.insight_pending = {"event": _ev("conflict", a="甲", b="乙"), "ts": NOW_DT.timestamp()}
        before = ac.maintenance_pressure(s)
        monitor._insight_emit(FakeClient(), s, _cfg(), _coach(), NOW_DT)
        after = ac.maintenance_pressure(s)
        self.assertLess(after["F"], before["F"])                # 長一個被朝向的意圖 → F 更緊
        self.assertTrue(any(g["spark_kind"] == "conflict" for g in volition.active(s)))

    def test_no_llm_falls_back_to_kind_template(self):
        s, client = self._emit(_ev("conflict", a="甲", b="乙"), coach=None)
        joined = "".join(client.sent)
        self.assertIn("相反", joined)                            # 衝突模板
        self.assertIn("假設", joined)
        self.assertEqual(s.convo_history[-1]["role"], "model")


# ── easy 端到端：leap/conflict 在觀察模式下一圈走完（累積→湧現→出聲→記憶→意圖） ──────────
class SparkPipelineTest(unittest.TestCase):
    def test_easy_conflict_pipeline(self):
        rng = random.Random(21)
        full = (_recs_val("暖", DIR_A, 5, rng, summary="讚", category="心情")
                + _recs_val("沉", DIR_B, 5, rng, summary="難過", category="心情"))
        s, cfg, coach = _state(), _cfg(easy=True), _coach()
        cycle = {"now": NOW_DT, "data": {"meta": {"lastIngestTs": 1}}, "self_stim_fired": False}
        monitor._association_step(StubReader(full), s, cfg, cycle)
        self.assertIsNotNone(s.insight_pending)
        kinds_seen = {b["kind"] for b in s.associations.bridges.values()}
        self.assertIn("conflict", kinds_seen)                   # 衝突橋確實被建/累積
        client = FakeClient()
        monitor._insight_emit(client, s, cfg, coach, NOW_DT)
        self.assertTrue(client.sent and client.sent[0].startswith("💡 "))
        self.assertEqual(s.convo_history[-1]["role"], "model")


# ── 回饋「記得＋誠實再犯」＋ 去重/換句話 ──────────────────────────────────────────
class FeedbackReadTest(unittest.TestCase):
    def test_reject_cues(self):
        for t in ("這兩個其實無關", "感覺不太相關", "扯不上吧", "想太多了", "牽強"):
            self.assertEqual(assoc.read_feedback(t), "reject", t)

    def test_affirm_cues(self):
        for t in ("有道理欸", "對欸沒錯", "真的有關", "蠻準的"):
            self.assertEqual(assoc.read_feedback(t), "affirm", t)

    def test_reject_precedence_over_fun(self):
        self.assertEqual(assoc.read_feedback("哈哈，兩者無關，但很有趣"), "reject")  # 截圖那句

    def test_bare_fun_or_chitchat_is_none(self):
        self.assertIsNone(assoc.read_feedback("哈哈很有趣"))
        self.assertIsNone(assoc.read_feedback("今天天氣真好"))

    def test_dissatisfaction_counts_as_reject(self):
        self.assertEqual(assoc.read_feedback("我看不懂你在說什麼"), "reject")   # 重用 plasticity 不滿訊號


class FeedbackRecordTest(unittest.TestCase):
    DRY = SimpleNamespace(dry_run=True)

    def _ready(self, a="甲", b="乙", kind="blend"):
        s = _state()
        s.last_insight = {"event": _ev(kind, a=a, b=b), "ts": NOW_DT.timestamp()}
        return s

    def test_records_reject_and_returns_brief(self):
        s = self._ready()
        brief = monitor._record_insight_feedback(s, "這兩個其實無關", NOW_DT.timestamp() + 60, self.DRY)
        self.assertIn("甲", brief)
        self.assertIn("記下來", brief)                       # 回話據實「記下來了」
        self.assertEqual(s.assoc_feedback[PAIR]["sentiment"], "reject")
        self.assertIsNone(s.last_insight)                    # 消費掉
        self.assertTrue(any(e["kind"] == "assoc_fb" and e["key"] == PAIR for e in s.engrams))  # 跨重生印痕

    def test_affirm_recorded(self):
        s = self._ready()
        monitor._record_insight_feedback(s, "有道理欸", NOW_DT.timestamp() + 60, self.DRY)
        self.assertEqual(s.assoc_feedback[PAIR]["sentiment"], "affirm")

    def test_flags_bridge_when_present(self):
        s = self._ready()
        s.associations = assoc.Associations()
        s.associations.bridges["blend::"+PAIR] = {"a": "甲", "b": "乙", "feedback": None}
        monitor._record_insight_feedback(s, "無關啦", NOW_DT.timestamp() + 60, self.DRY)
        self.assertEqual(s.associations.bridges["blend::"+PAIR]["feedback"], "reject")

    def test_no_record_when_stale(self):
        s = self._ready()
        s.last_insight["ts"] = NOW_DT.timestamp() - 99999    # 過了回饋窗
        self.assertEqual(monitor._record_insight_feedback(s, "無關", NOW_DT.timestamp(), self.DRY), "")
        self.assertEqual(s.assoc_feedback, {})

    def test_no_record_when_ambiguous(self):
        s = self._ready()
        self.assertEqual(monitor._record_insight_feedback(s, "今天天氣真好", NOW_DT.timestamp() + 30, self.DRY), "")
        self.assertIsNotNone(s.last_insight)                 # 沒明確線索 → 不亂記、不消費

    def test_feedback_persists_roundtrip(self):
        s = self._ready()
        monitor._record_insight_feedback(s, "無關", NOW_DT.timestamp() + 60, self.DRY)
        s.save()
        from telegram_monitor.state import State
        s2 = State.load(s.path)
        self.assertEqual(s2.assoc_feedback[PAIR]["sentiment"], "reject")   # 跨重生記得


class RecurrenceRenderTest(unittest.TestCase):
    def test_facts_honest_recur_on_prior_reject(self):
        f = assoc.insight_facts(_ev("blend", a="甲", b="乙"), prior="reject")
        self.assertIn("老實承認", f)
        self.assertIn("無關", f)

    def test_text_honest_recur_on_prior_reject(self):
        txt = assoc.insight_text(_ev("blend", a="甲", b="乙"), prior="reject")
        self.assertIn("我知道你上次說", txt)
        self.assertIn("無關", txt)
        self.assertNotIn("保證", txt)                        # 不空口保證以後不犯

    def test_no_recur_frame_without_prior(self):
        self.assertNotIn("老實承認", assoc.insight_facts(_ev("blend", a="甲", b="乙")))


class DedupVarietyRenderTest(unittest.TestCase):
    def test_openers_vary_by_variant(self):
        openers = {assoc.insight_text(_ev("blend", a="甲", b="乙"), variant=i)[:6] for i in range(4)}
        self.assertGreater(len(openers), 1)                  # 不再每句同一個開頭

    def test_facts_carries_recent_opener_hint(self):
        f = assoc.insight_facts(_ev("blend"), recent_openers=["欸——我突然", "等等，我發現"])
        self.assertIn("換一種說法", f)


class InsightDedupEmitTest(unittest.TestCase):
    def test_dedup_silent_for_recent_pair(self):
        s = _state()
        s.recent_insights = [{"pair": PAIR, "ts": NOW_DT.timestamp() - 60}]   # 1 分鐘前才說過
        s.insight_pending = {"event": _ev("blend", a="甲", b="乙"), "ts": NOW_DT.timestamp()}
        client = FakeClient()
        monitor._insight_emit(client, s, _cfg(), _coach(), NOW_DT)
        self.assertEqual(client.sent, [])                    # 去重靜默、不洗版
        self.assertIsNone(s.insight_pending)

    def test_emits_and_records_recent_and_opener(self):
        s = _state()
        s.insight_pending = {"event": _ev("blend", a="甲", b="乙"), "ts": NOW_DT.timestamp()}
        monitor._insight_emit(FakeClient(dry_run=True), s, _cfg(), _coach(), NOW_DT)
        self.assertTrue(any(r["pair"] == PAIR for r in s.recent_insights))   # 進去重台帳
        self.assertEqual(s.last_insight["event"]["a"], "甲")  # 留作下一句回饋的對象
        self.assertTrue(s.recent_insight_openers)

    def test_prior_reject_does_not_repeat_without_new_permission(self):
        s = _state()
        s.assoc_feedback = {PAIR: {"sentiment": "reject", "ts": 0, "kind": "blend"}}
        s.insight_pending = {"event": _ev("blend", a="甲", b="乙"), "ts": NOW_DT.timestamp()}
        client = FakeClient(dry_run=True)
        monitor._insight_emit(client, s, _cfg(), None, NOW_DT)   # 無 coach → 模板
        self.assertEqual(client.sent, [])
        self.assertIsNone(s.insight_pending)


# ── 💡 暖度勢能 warmth（孵化前感，純函式＋observe 暴露最暖未湧現橋） ───────────────────
def _br(strength=0.0, confirm_run=0, support=0, **kw):
    b = {"strength": strength, "confirm_run": confirm_run, "support": support}
    b.update(kw)
    return b


class WarmthTest(unittest.TestCase):
    def test_warmth_monotone_and_clamped(self):
        thr, cl, ms = 0.66, 4, 3
        full = assoc.warmth(_br(strength=0.66, confirm_run=4, support=3), thr, cl, ms)
        self.assertEqual(full, 1.0)                              # 三者皆達門檻 → 1.0
        low = assoc.warmth(_br(strength=0.1, confirm_run=1, support=1), thr, cl, ms)
        self.assertLess(low, 1.0)
        self.assertGreaterEqual(low, 0.0)
        # 任一比例上升 → 暖度單調不減
        a = assoc.warmth(_br(strength=0.3, confirm_run=2, support=2), thr, cl, ms)
        b = assoc.warmth(_br(strength=0.5, confirm_run=2, support=2), thr, cl, ms)
        self.assertGreaterEqual(b, a)
        # 任一未達門檻 → < 1.0
        self.assertLess(assoc.warmth(_br(strength=0.66, confirm_run=4, support=1), thr, cl, ms), 1.0)
        # 夾 [0,1]：超門檻仍封頂 1.0
        self.assertEqual(assoc.warmth(_br(strength=2.0, confirm_run=99, support=99), thr, cl, ms), 1.0)

    def test_warmth_quantized_no_jitter(self):
        thr, cl, ms = 0.66, 4, 3
        w1 = assoc.warmth(_br(strength=0.40, confirm_run=2, support=2), thr, cl, ms)
        w2 = assoc.warmth(_br(strength=0.401, confirm_run=2, support=2), thr, cl, ms)
        self.assertEqual(w1, w2)                                 # 微小變化量化到同一步階、不每圈漂
        self.assertAlmostEqual((round(w1 / 0.05) * 0.05), w1, places=6)   # 確實落在 0.05 步階

    def test_warmth_safe_on_missing(self):
        self.assertEqual(assoc.warmth(None, 0.66), 0.0)
        self.assertEqual(assoc.warmth(_br(), 0.0), 0.0)         # 零門檻安全回 0

    def test_warmth_off_is_none(self):
        a, cents = assoc.Associations(), _converging_cents()
        a.observe(cents, k=2.0, ingest_changed=True, revisit_topic=None, now_ts=0.0)
        self.assertIsNone(a.last.get("warmest"))               # 旗標關（warmth=False）→ 不算、為 None

    def test_warmth_easy_short_circuits_none(self):
        a, cents = assoc.Associations(), _converging_cents()
        a.observe(cents, k=2.0, ingest_changed=True, revisit_topic=None, now_ts=0.0,
                  easy=True, warmth=True)
        self.assertIsNone(a.last.get("warmest"))               # easy 即使開也短路為 None（梯度塌陷、設計決策）

    def test_observe_warmest_points_at_unemerged(self):
        a, cents = assoc.Associations(), _converging_cents()
        # realistic、尚未湧現的前幾拍：最暖橋應指向 甲|乙 那條未 emerged 橋
        for _ in range(2):
            a.observe(cents, k=2.0, ingest_changed=True, revisit_topic=None, now_ts=0.0,
                      confirm_laps=4, min_support=3, warmth=True)
        warmest = a.last.get("warmest")
        self.assertIsNotNone(warmest)
        self.assertEqual(warmest["pair"], assoc.pair_key({"a": "甲", "b": "乙"}))
        self.assertGreater(warmest["warmth"], 0.0)
        # 推到湧現後，該橋已 emerged → 不再被當「最暖未湧現橋」
        for _ in range(8):
            a.observe(cents, k=2.0, ingest_changed=True, revisit_topic=None, now_ts=1.0,
                      confirm_laps=4, min_support=3, warmth=True)
        self.assertIsNone(a.last.get("warmest"))               # 唯一的橋已 emerged → warmest 回 None


# ── 💡 新奇度 novelty（驚奇語氣＋同值 tie-break，皆加性） ─────────────────────────────
class NoveltyPureTest(unittest.TestCase):
    def test_leap_more_novel_than_blend(self):
        # 同 blend_tau 下，越遠（cos 低）的 leap 越意外；近的 blend 越不意外
        nleap = assoc._novelty("leap", 0.1, blend_tau=0.5)
        nblend = assoc._novelty("blend", 0.9)
        self.assertGreater(nleap, nblend)

    def test_conflict_novelty_grows_with_gap(self):
        small = assoc._novelty("conflict", 0.7, {"valence_gap": 0.5})
        big = assoc._novelty("conflict", 0.7, {"valence_gap": 1.8})
        self.assertGreater(big, small)
        self.assertLessEqual(big, 1.0)

    def test_novelty_clamped(self):
        self.assertGreaterEqual(assoc._novelty("blend", 2.0), 0.0)      # cos>1 → 0
        self.assertLessEqual(assoc._novelty("leap", -1.0, blend_tau=0.5), 1.0)


class NoveltyObserveTest(unittest.TestCase):
    def _both_cents(self, seed=13):
        rng = random.Random(seed)
        full = (_recs_val("甲", DIR_A, 5, rng, summary="讚", category="研究")
                + _recs_val("乙", DIR_B, 5, rng, summary="難過", category="研究"))
        return assoc.topic_centroids(full, min_recs=2)

    def test_event_carries_novelty_when_on(self):
        a, cents = assoc.Associations(), _converging_cents()
        ev = None
        for _ in range(8):
            ev = a.observe(cents, k=2.0, ingest_changed=True, revisit_topic=None, now_ts=1.0,
                           confirm_laps=1, min_support=1, novelty=True) or ev
        self.assertIn("novelty", ev)
        self.assertGreaterEqual(ev["novelty"], 0.0)

    def test_novelty_off_bit_identical(self):
        # novelty=False vs 不傳：emerged 事件序列與 bridges 完全一致、且 event 不含 novelty 鍵
        def run(**kw):
            a, cents = assoc.Associations(), self._both_cents()
            evs = [a.observe(cents, k=0.0, ingest_changed=True, revisit_topic=None, now_ts=1.0,
                             confirm_laps=1, min_support=1, **kw) for _ in range(6)]
            return [e for e in evs if e], a
        f_default, a1 = run()
        f_off, a2 = run(novelty=False)
        self.assertEqual([(e["a"], e["b"], e["kind"]) for e in f_default],
                         [(e["a"], e["b"], e["kind"]) for e in f_off])
        for e in f_default + f_off:
            self.assertNotIn("novelty", e)                      # 關時逐位元同現狀（不加鍵）
        self.assertEqual(set(a1.bridges), set(a2.bridges))

    def test_novelty_tiebreak_only_on_equal_strength(self):
        # _both_cents：blend 與 conflict 同對、每拍同 gain → strength 完全相等(==)
        a, cents = assoc.Associations(), self._both_cents()
        evs = [a.observe(cents, k=0.0, ingest_changed=True, revisit_topic=None, now_ts=1.0,
                         confirm_laps=1, min_support=1, novelty=True) for _ in range(6)]
        fired = [e for e in evs if e]
        self.assertEqual(len(fired), 2)
        # strength 相等時 novelty 高者先湧現：conflict（價差大）的 novelty 高於 blend（cos≈0.69）
        self.assertEqual(fired[0]["kind"], "conflict")
        self.assertGreaterEqual(fired[0]["novelty"], fired[1]["novelty"])

    def test_novelty_does_not_relax_gates(self):
        # 高 novelty 也不放寬四閘：從不被觸發 → 從不湧現
        a, cents = assoc.Associations(), self._both_cents()
        evs = [a.observe(cents, k=0.0, ingest_changed=False, revisit_topic=None, now_ts=1.0,
                         confirm_laps=1, min_support=1, novelty=True) for _ in range(20)]
        self.assertEqual([e for e in evs if e], [])


class NoveltyFactsTest(unittest.TestCase):
    def test_facts_no_novelty_byte_identical(self):
        ev = _ev("blend")
        self.assertNotIn("novelty", ev)
        self.assertEqual(assoc.insight_facts(ev), assoc.insight_facts(_ev("blend")))   # 無 novelty 鍵→逐字同現狀

    def test_facts_high_novelty_adds_surprised_tone_still_hedged(self):
        f = assoc.insight_facts(_ev("blend", novelty=0.9))
        self.assertIn("意外", f)                                # 高→驚訝口吻提示
        self.assertIn("假設", f)                                # 仍標假設
        self.assertIn("甲那句", f)                              # 片段仍全來自 ev
        self.assertNotIn("丙", f)                              # 不新增片段/第三條

    def test_facts_low_novelty_plain_tone(self):
        f = assoc.insight_facts(_ev("blend", novelty=0.1))
        self.assertIn("順理成章", f)
        self.assertIn("假設", f)


# ── 💡 回饋吸收 graded feedback（三態＋tally/n 累積，與 read_feedback 相容） ─────────────
class GradeFeedbackTest(unittest.TestCase):
    def test_partial_cues(self):
        for t in ("方向對但細節不準", "沒想過倒有點啟發", "大方向是對的"):
            self.assertEqual(assoc.grade_feedback(t)["sentiment"], "partial", t)

    def test_reject_precedence_over_partial(self):
        # 同句既有 reject 又像 partial → reject 優先（護欄）
        self.assertEqual(assoc.grade_feedback("方向對啦但其實無關")["sentiment"], "reject")

    def test_affirm_and_none(self):
        self.assertEqual(assoc.grade_feedback("有道理欸")["sentiment"], "affirm")
        self.assertIsNone(assoc.grade_feedback("今天天氣真好")["sentiment"])

    def test_read_feedback_unchanged_and_compatible(self):
        # read_feedback 仍只回 reject|affirm|None（partial 句對它＝None，非矛盾）
        self.assertIsNone(assoc.read_feedback("方向對但細節不準"))
        self.assertEqual(assoc.read_feedback("這兩個其實無關"), "reject")
        self.assertEqual(assoc.read_feedback("有道理欸"), "affirm")


class FeedbackTallyTest(unittest.TestCase):
    DRY = SimpleNamespace(dry_run=True, association_feedback_graded=True)
    DRY_OFF = SimpleNamespace(dry_run=True, association_feedback_graded=False)

    def _ready(self, a="甲", b="乙"):
        s = _state()
        s.last_insight = {"event": _ev("blend", a=a, b=b), "ts": NOW_DT.timestamp()}
        return s

    def test_tally_accumulates_on_repeat_affirm(self):
        s = self._ready()
        monitor._record_insight_feedback(s, "有道理欸", NOW_DT.timestamp() + 60, self.DRY)
        rec = s.assoc_feedback[PAIR]
        self.assertEqual(rec["sentiment"], "affirm")
        self.assertEqual(rec["n"], 1)
        self.assertGreater(rec["tally"], 0)
        s.last_insight = {"event": _ev("blend", a="甲", b="乙"), "ts": NOW_DT.timestamp() + 120}
        monitor._record_insight_feedback(s, "對欸沒錯", NOW_DT.timestamp() + 180, self.DRY)
        self.assertEqual(s.assoc_feedback[PAIR]["n"], 2)         # n 遞增
        self.assertGreater(s.assoc_feedback[PAIR]["tally"], rec["tally"])   # tally 累加

    def test_reject_lowers_tally(self):
        s = self._ready()
        monitor._record_insight_feedback(s, "有道理欸", NOW_DT.timestamp() + 60, self.DRY)
        up = s.assoc_feedback[PAIR]["tally"]
        s.last_insight = {"event": _ev("blend", a="甲", b="乙"), "ts": NOW_DT.timestamp() + 120}
        monitor._record_insight_feedback(s, "其實無關啦", NOW_DT.timestamp() + 180, self.DRY)
        self.assertLess(s.assoc_feedback[PAIR]["tally"], up)     # reject 下修
        self.assertEqual(s.assoc_feedback[PAIR]["sentiment"], "reject")   # 最新 sentiment 仍寫

    def test_off_has_no_tally(self):
        s = self._ready()
        monitor._record_insight_feedback(s, "有道理欸", NOW_DT.timestamp() + 60, self.DRY_OFF)
        rec = s.assoc_feedback[PAIR]
        self.assertEqual(rec["sentiment"], "affirm")
        self.assertNotIn("tally", rec)                          # 關＝向後相容、無新鍵
        self.assertNotIn("n", rec)

    def test_partial_recorded_when_graded(self):
        s = self._ready()
        brief = monitor._record_insight_feedback(s, "方向對但細節不準", NOW_DT.timestamp() + 60, self.DRY)
        self.assertEqual(s.assoc_feedback[PAIR]["sentiment"], "partial")
        self.assertIn("方向", brief)                            # partial 走中性 brief


# ── 💡 洞見台帳 insight_ledger（整合·沉澱真實 anchor 內容） ──────────────────────────
class LedgerTest(unittest.TestCase):
    EV = {"a": "甲", "b": "乙",
          "anchor_a": {"text": "甲那條我寫過的某句", "ts": "x"},
          "anchor_b": {"text": "乙那條我寫過的某句", "ts": "y"},
          "cos": 0.7, "strength": 0.8, "support": 3, "why_now": "new_record",
          "kind": "blend", "itype": "link", "novelty": 0.3}

    def _cfg_ledger(self, on=True):
        c = _cfg()
        c.association_ledger = on
        return c

    def test_ledger_records_real_anchors_only(self):
        s, client = _state(), FakeClient()
        s.insight_pending = {"event": dict(self.EV), "ts": NOW_DT.timestamp()}
        monitor._insight_emit(client, s, self._cfg_ledger(on=True), _coach(), NOW_DT)
        self.assertEqual(len(s.insight_ledger), 1)
        entry = s.insight_ledger[0]
        self.assertEqual(entry["pair"], PAIR)
        self.assertEqual(entry["anchor_a_clip"], "甲那條我寫過的某句")   # 真實 anchor
        self.assertEqual(entry["anchor_b_clip"], "乙那條我寫過的某句")
        self.assertEqual(entry["novelty"], 0.3)
        self.assertIsNone(entry["feedback"])
        # 不存渲染句（AHA）
        self.assertNotIn(AHA, str(entry))

    def test_ledger_off_writes_nothing(self):
        s, client = _state(), FakeClient()
        s.insight_pending = {"event": dict(self.EV), "ts": NOW_DT.timestamp()}
        monitor._insight_emit(client, s, self._cfg_ledger(on=False), _coach(), NOW_DT)
        self.assertEqual(s.insight_ledger, [])                  # 關＝不寫

    def test_ledger_ring_capped_at_24(self):
        s = _state()
        s.insight_ledger = [{"pair": f"p{i}", "a": "x", "b": "y", "kind": "blend",
                             "itype": None, "novelty": None, "anchor_a_clip": "",
                             "anchor_b_clip": "", "born_ts": 0, "feedback": None} for i in range(24)]
        client = FakeClient()
        s.insight_pending = {"event": dict(self.EV), "ts": NOW_DT.timestamp()}
        monitor._insight_emit(client, s, self._cfg_ledger(on=True), _coach(), NOW_DT)
        self.assertEqual(len(s.insight_ledger), 24)             # 環形上限
        self.assertEqual(s.insight_ledger[-1]["pair"], PAIR)   # 最新在尾
        self.assertEqual(s.insight_ledger[0]["pair"], "p1")    # 最舊被擠出

    def test_ledger_roundtrip_and_affirm_flag(self):
        s, client = _state(), FakeClient()
        s.insight_pending = {"event": dict(self.EV), "ts": NOW_DT.timestamp()}
        monitor._insight_emit(client, s, self._cfg_ledger(on=True), _coach(), NOW_DT)
        s.save()
        from telegram_monitor.state import State
        s2 = State.load(s.path)
        self.assertEqual(s2.insight_ledger[0]["anchor_a_clip"], "甲那條我寫過的某句")   # 跨重生
        # affirm 回饋 → 同 pair 台帳筆升格標記
        s2.last_insight = {"event": dict(self.EV), "ts": NOW_DT.timestamp()}
        monitor._record_insight_feedback(s2, "有道理欸", NOW_DT.timestamp() + 60,
                                         SimpleNamespace(dry_run=True))
        self.assertEqual(s2.insight_ledger[0]["feedback"], "affirm")


# ── 💡 volition spark_novelty 透傳（文案/subject/kind/spark_kind 不變） ──────────────
class SparkNoveltyTest(unittest.TestCase):
    def test_seed_goal_passes_novelty(self):
        s, client = _state(), FakeClient()
        ev = _ev("leap", a="丙", b="丁", novelty=0.8,
                 signal={"stone": {"text": "橋那句", "topic": "橋"}})
        s.insight_pending = {"event": ev, "ts": NOW_DT.timestamp()}
        monitor._insight_emit(client, s, _cfg(), _coach(), NOW_DT)
        g = next(g for g in volition.active(s) if g["subject"] == "丙×丁")
        self.assertEqual(g["spark_novelty"], 0.8)
        self.assertEqual(g["kind"], "understand_link")          # kind 不變
        self.assertEqual(g["spark_kind"], "leap")               # spark_kind 不變
        self.assertIn("跳接", g["desire"])                      # desire 文案不變（保護斷言）

    def test_form_link_goal_default_novelty_zero(self):
        s = _state()
        g = volition.form_link_goal(s, "甲", "乙", NOW_DT.timestamp())
        self.assertEqual(g["spark_novelty"], 0.0)               # 預設＝現行


# ── 💡 §0.54「真的有特色才分享」硬門檻：is_distinctive 純函式 + _insight_emit 前置把關 ─────────
class DistinctiveGateTest(unittest.TestCase):
    def test_leap_conflict_absolute_floors(self):
        # leap/conflict novelty 本就以 τ 正規化/全幅 → 絕對地板 0.34（各 kind 自己那把尺，不混排）
        self.assertTrue(assoc.is_distinctive({"kind": "leap", "novelty": 0.34}))
        self.assertFalse(assoc.is_distinctive({"kind": "leap", "novelty": 0.33}))
        self.assertTrue(assoc.is_distinctive({"kind": "conflict", "novelty": 0.34}))
        self.assertFalse(assoc.is_distinctive({"kind": "conflict", "novelty": 0.20}))

    def test_blend_normalized_relative_to_tau(self):
        # blend＝1−cos，天花板隨 τ 浮動 → 用相對 τ 正規化位置 pos=novelty/(1−τ) 比 0.45，而非絕對值
        self.assertTrue(assoc.is_distinctive({"kind": "blend", "novelty": 0.31, "blend_tau": 0.69}))   # pos 1.0
        self.assertFalse(assoc.is_distinctive({"kind": "blend", "novelty": 0.10, "blend_tau": 0.50}))  # pos 0.2
        self.assertTrue(assoc.is_distinctive({"kind": "blend", "novelty": 0.27, "blend_tau": 0.40}))   # pos 0.45（邊界）

    def test_blend_high_tau_not_silenced(self):
        # ★ 回歸守門（審查 HIGH）：τ 高（0.85）時 novelty 天花板只有 0.15；用絕對地板 0.30 會把整個 blend kind 封殺。
        # 正規化後 pos=0.10/0.15≈0.667≥0.45 → 仍算有特色、照樣放行（不因 τ 高而完全靜音）。
        self.assertTrue(assoc.is_distinctive({"kind": "blend", "novelty": 0.10, "blend_tau": 0.85}))

    def test_blend_fallback_absolute_when_no_tau(self):
        # 沒帶 blend_tau（novelty 關/舊事件）→ 保守絕對地板 0.22（只濾幾乎同義的 blend、寧鬆勿過度封口）
        self.assertTrue(assoc.is_distinctive({"kind": "blend", "novelty": 0.22}))
        self.assertFalse(assoc.is_distinctive({"kind": "blend", "novelty": 0.21}))

    def test_is_distinctive_degrades_true_without_novelty(self):
        # 沒算 novelty（ASSOCIATION_NOVELTY=0）→ 無從評斷 → 放行（不無故封口）
        self.assertTrue(assoc.is_distinctive({"kind": "blend"}))
        self.assertTrue(assoc.is_distinctive({"kind": "leap", "novelty": None}))
        self.assertTrue(assoc.is_distinctive({}))
        self.assertTrue(assoc.is_distinctive(None))
        self.assertTrue(assoc.is_distinctive({"kind": "blend", "novelty": "壞值"}))   # 壞值→守門失敗偏不封口

    def test_is_distinctive_scale_and_nonfinite(self):
        self.assertTrue(assoc.is_distinctive({"kind": "leap", "novelty": 0.0}, scale=0))       # ≤0＝關閉此門檻
        self.assertTrue(assoc.is_distinctive({"kind": "leap", "novelty": 0.0}, scale=-1))
        self.assertTrue(assoc.is_distinctive({"kind": "leap", "novelty": 0.0}, scale=float("nan")))   # 非有限＝關閉（fail-open）
        self.assertTrue(assoc.is_distinctive({"kind": "leap", "novelty": float("nan")}))       # novelty 非有限→放行
        self.assertFalse(assoc.is_distinctive({"kind": "leap", "novelty": 0.5}, scale=2))      # >1 更嚴（地板 0.68）
        self.assertTrue(assoc.is_distinctive({"kind": "leap", "novelty": 0.2}, scale=0.5))     # <1 更寬（地板 0.17）
        # blend 縮放作用在正規化地板：pos 0.5 vs 0.45×2=0.90 → 擋
        self.assertFalse(assoc.is_distinctive({"kind": "blend", "novelty": 0.25, "blend_tau": 0.5}, scale=2))

    def test_emit_suppresses_bland_spark_and_drops_it(self):
        # 非 easy＋新奇度低於地板 → 不出聲、且**清掉暫存**（平庸火花安靜過去、不積壓）
        s, client = _state(), FakeClient()
        s.insight_pending = {"event": _ev("blend", a="甲", b="乙", novelty=0.1), "ts": NOW_DT.timestamp()}
        monitor._insight_emit(client, s, _cfg(), _coach(), NOW_DT)
        self.assertEqual(client.sent, [])
        self.assertIsNone(s.insight_pending)                   # 丟掉（對比冷卻是保留待說）

    def test_emit_speaks_for_distinctive_spark(self):
        # 非 easy＋新奇度過地板 → 照常出聲
        s, client = _state(), FakeClient()
        s.insight_pending = {"event": _ev("leap", a="丙", b="丁", novelty=0.9,
                                          signal={"stone": {"text": "橋那句", "topic": "橋"}}),
                             "ts": NOW_DT.timestamp()}
        monitor._insight_emit(client, s, _cfg(), _coach(), NOW_DT)
        self.assertTrue(client.sent and client.sent[0].startswith("💡 "))
        self.assertIsNone(s.insight_pending)

    def test_emit_speaks_when_novelty_absent(self):
        # 沒 novelty 鍵 → is_distinctive 放行 → 照常出聲（不因守門而封口）
        s, client = _state(), FakeClient()
        s.insight_pending = {"event": _ev("blend", a="甲", b="乙"), "ts": NOW_DT.timestamp()}
        monitor._insight_emit(client, s, _cfg(), _coach(), NOW_DT)
        self.assertTrue(client.sent and client.sent[0].startswith("💡 "))

    def test_easy_mode_bypasses_gate(self):
        # easy（觀察模式）比照 relevance 一律放行：低新奇度仍出聲
        s, client = _state(), FakeClient()
        s.insight_pending = {"event": _ev("blend", a="甲", b="乙", novelty=0.05), "ts": NOW_DT.timestamp()}
        monitor._insight_emit(client, s, _cfg(easy=True), _coach(), NOW_DT)
        self.assertTrue(client.sent and client.sent[0].startswith("💡 "))

    def test_flag_off_bypasses_gate(self):
        # association_distinctive=0 → 不擋（逐位元同舊）
        s, client = _state(), FakeClient()
        c = _cfg()
        c.association_distinctive = False
        s.insight_pending = {"event": _ev("blend", a="甲", b="乙", novelty=0.05), "ts": NOW_DT.timestamp()}
        monitor._insight_emit(client, s, c, _coach(), NOW_DT)
        self.assertTrue(client.sent and client.sent[0].startswith("💡 "))

    def test_thresh_scale_makes_gate_stricter(self):
        # thresh>1 更嚴：blend 0.4（無 blend_tau→走 fallback 絕對地板 0.22）在 scale=2（地板 0.44）下被擋
        s, client = _state(), FakeClient()
        c = _cfg()
        c.association_distinctive_thresh = 2.0
        s.insight_pending = {"event": _ev("blend", a="甲", b="乙", novelty=0.4), "ts": NOW_DT.timestamp()}
        monitor._insight_emit(client, s, c, _coach(), NOW_DT)
        self.assertEqual(client.sent, [])
        self.assertIsNone(s.insight_pending)

    def test_thresh_zero_disables_gate_end_to_end(self):
        # ★ 回歸守門（審查 HIGH）：thresh=0 應**關閉**此門檻（別被 `or 1.0` 誤改成 1.0）→ 平庸火花仍出聲
        s, client = _state(), FakeClient()
        c = _cfg()
        c.association_distinctive_thresh = 0.0
        s.insight_pending = {"event": _ev("blend", a="甲", b="乙", novelty=0.05), "ts": NOW_DT.timestamp()}
        monitor._insight_emit(client, s, c, _coach(), NOW_DT)
        self.assertTrue(client.sent and client.sent[0].startswith("💡 "))   # 門檻關閉 → 照常出聲


# ── 💡 easy 端到端：開各旗標仍出聲、進記憶、接地（鏡射 easy_pipeline） ──────────────────
class FlaggedPipelineTest(unittest.TestCase):
    def _cfg_all_on(self):
        c = _cfg(easy=True)
        c.association_novelty = True
        c.association_warmth = True
        c.association_feedback_graded = True
        c.association_ledger = True
        return c

    def test_easy_pipeline_still_speaks_remembers(self):
        rng = random.Random(7)
        reader = StubReader(_recs("甲", DIR_A, 5, rng) + _recs("乙", DIR_B, 5, rng))
        s, cfg, coach = _state(), self._cfg_all_on(), _coach()
        cycle = {"now": NOW_DT, "data": {"meta": {"lastIngestTs": 1}}, "self_stim_fired": False}
        monitor._association_step(reader, s, cfg, cycle)
        self.assertIsNotNone(s.insight_pending)
        client = FakeClient()
        monitor._insight_emit(client, s, cfg, coach, NOW_DT)
        self.assertTrue(client.sent and client.sent[0].startswith("💡 "))
        self.assertEqual(s.convo_history[-1]["role"], "model")
        self.assertTrue(s.insight_ledger)                       # 旗標開→台帳有寫
        self.assertIsNone(s.associations.last.get("warmest"))   # easy 下 warmest 仍為 None


class SpontaneousDedupTest(unittest.TestCase):
    """🫧 自發出聲內容去重（控重複／自然降頻）：association.was_recent / push_recent 純函式。"""

    def test_was_recent(self):
        from telegram_monitor import association
        led = [{"key": "惠中寺", "ts": 1000}]
        self.assertTrue(association.was_recent("惠中寺", led, 1000 + 100, 43200))    # 12h 內＝剛講過
        self.assertFalse(association.was_recent("惠中寺", led, 1000 + 50000, 43200))  # 超過 12h
        self.assertFalse(association.was_recent("別條", led, 1000 + 100, 43200))      # 不同條
        self.assertFalse(association.was_recent("惠中寺", led, 1100, 0))              # dedup_s=0＝不擋
        self.assertFalse(association.was_recent("", led, 1100, 43200))               # 空 key 不擋

    def test_push_recent_dedups_and_caps(self):
        from telegram_monitor import association
        led = []
        for i in range(10):
            led = association.push_recent(f"線{i}", led, i)
        self.assertEqual(len(led), 8)                          # 保最後 8
        self.assertEqual(led[-1], {"key": "線9", "ts": 9})
        led2 = association.push_recent("線9", led, 100)         # 同 key → 去重後追加（不重複堆）
        self.assertEqual(sum(1 for e in led2 if e["key"] == "線9"), 1)
        self.assertEqual(led2[-1]["ts"], 100)


if __name__ == "__main__":
    unittest.main()
