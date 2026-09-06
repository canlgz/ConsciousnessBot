"""🔮 §1.90 記寫預想（FORESIGHT）：一條會被真實記寫裁決的假設，中／不中都要回頭說。

使用者需求（原話）：「讓bot主動去預想一些可能性，這對我記寫有幫助。」——bot **自己指認出來的能力缺口**
（截圖 10:45：「我還不會主動去預想一些可能性，像『如果你接下來這樣做，會不會跟之前的某件事有關係？』」，
使用者回「我幫你達成」）。稽核實測確認全 repo 的連結機制都是回顧型 ⇒ 本章補的是**時間方向**那一維。

本檔釘住：候選挑選的每一道確定性條件、裁決與命題對得上、四道接地自檢在 bot 最要命的兩款幻覺上
（引號內捏造、引號外捏造）真的攔得住、退路模板自己過得了自檢、以及**走完整條 emit lane 的實跑**
（MEMORY「新機制常被無聲架空」：邏輯對不算數）。全 stub、零網路。
"""

import io
import os
import re
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from telegram_monitor import foresight, monitor, persona
from telegram_monitor.state import State

DAY = 86400.0
NOW = 1_782_400_000.0


def _rec(label, text, ts):
    return {"topicLabel": label, "text": text, "_ts": ts, "id": f"id-{label}-{int(ts)}"}


def _bridge(a, b, **kw):
    d = {"key": f"blend::{a}|{b}", "a": a, "b": b, "kind": "blend", "cos": 0.5,
         "support": 2, "emerged": False,
         "anchor_a": {"text": f"{a} 的真實原文", "ts": None},
         "anchor_b": {"text": f"{b} 的真實原文", "ts": None}}
    d.update(kw)
    return d


def _spans(fresh_days=1.0, stale_days=10.0):
    return {"甲線": {"last_ts": NOW - fresh_days * DAY, "n": 5},
            "乙線": {"last_ts": NOW - stale_days * DAY, "n": 3}}


class LineSpansTest(unittest.TestCase):
    def test_exact_label_grouping(self):
        # strict-equal 分組：「閱讀」絕不可以吃到「閱讀｜讀誦經書」（habits.py:296 的教訓）
        sp = foresight.line_spans([_rec("閱讀", "a", NOW - DAY), _rec("閱讀｜讀誦經書", "b", NOW)])
        self.assertEqual(sorted(sp), ["閱讀", "閱讀｜讀誦經書"])
        self.assertEqual(sp["閱讀"]["n"], 1)

    def test_skips_unparseable(self):
        sp = foresight.line_spans([{"topicLabel": "", "_ts": NOW}, {"topicLabel": "X", "_ts": None}])
        self.assertEqual(sp, {})

    def test_last_ts_is_max(self):
        sp = foresight.line_spans([_rec("A", "x", NOW - 5 * DAY), _rec("A", "y", NOW - DAY)])
        self.assertEqual(sp["A"]["last_ts"], NOW - DAY)
        self.assertEqual(sp["A"]["n"], 2)


class PickCandidatesTest(unittest.TestCase):
    def _pick(self, br=None, sp=None, **kw):
        return foresight.pick_candidates(br or [_bridge("甲線", "乙線")], sp or _spans(), NOW, **kw)

    def test_happy_path_direction(self):
        c = self._pick()
        self.assertEqual(len(c), 1)
        self.assertEqual(c[0]["a"], "甲線")           # 還在動的那端
        self.assertEqual(c[0]["b"], "乙線")           # 停住的那端
        self.assertEqual(c[0]["quote"], "乙線 的真實原文")   # 引文取**停住那端**的 anchor
        self.assertEqual(c[0]["pair"], "|".join(sorted(("甲線", "乙線"))))

    def test_direction_flips_with_spans(self):
        c = self._pick(sp={"甲線": {"last_ts": NOW - 10 * DAY, "n": 3},
                           "乙線": {"last_ts": NOW - DAY, "n": 5}})
        self.assertEqual((c[0]["a"], c[0]["b"]), ("乙線", "甲線"))
        self.assertEqual(c[0]["quote"], "甲線 的真實原文")

    def test_emerged_excluded(self):
        self.assertEqual(self._pick(br=[_bridge("甲線", "乙線", emerged=True)]), [])

    def test_support_and_cos_floor(self):
        self.assertEqual(self._pick(br=[_bridge("甲線", "乙線", support=1)]), [])
        self.assertEqual(self._pick(br=[_bridge("甲線", "乙線", cos=0.1)]), [])

    def test_both_hot_excluded(self):
        # 兩端都還熱＝那是 💡 的事，不是預想
        self.assertEqual(self._pick(sp=_spans(stale_days=1.0)), [])

    def test_too_old_excluded(self):
        self.assertEqual(self._pick(sp=_spans(stale_days=90.0)), [])   # 已翻篇

    def test_active_end_must_be_active(self):
        self.assertEqual(self._pick(sp=_spans(fresh_days=20.0, stale_days=40.0)), [])

    def test_missing_anchor_dropped(self):
        br = _bridge("甲線", "乙線")
        br["anchor_b"] = {"text": "", "ts": None}
        self.assertEqual(self._pick(br=[br]), [])       # 沒真實引文＝丟掉（fail-closed，絕不硬講）

    def test_end_not_in_spans_dropped(self):
        self.assertEqual(self._pick(sp={"甲線": {"last_ts": NOW - DAY, "n": 2}}), [])

    def test_blocked(self):
        p = "|".join(sorted(("甲線", "乙線")))
        self.assertEqual(self._pick(blocked=(p,)), [])

    def test_sorted_over_full_set_no_truncation(self):
        # §1.83 前科：候選被 [-N:] 先截尾再挑。這裡驗「最強的那個一定排第一」，即使它在輸入的最後面
        sp = {f"L{i}": {"last_ts": NOW - DAY, "n": 2} for i in range(8)}
        sp["STALE"] = {"last_ts": NOW - 10 * DAY, "n": 2}
        brs = [_bridge(f"L{i}", "STALE", support=2, cos=0.4) for i in range(7)]
        brs.append(_bridge("L7", "STALE", support=9, cos=0.9))       # 最強、放最後
        c = foresight.pick_candidates(brs, sp, NOW)
        self.assertEqual(len(c), 8)
        self.assertEqual(c[0]["a"], "L7")
        self.assertEqual(c[0]["support"], 9)

    def test_source_is_free_no_recompute(self):
        # 階段 1 的關鍵：只吃已累積好的 bridge 欄位，不需要 embedding/質心
        c = self._pick()[0]
        self.assertEqual(set(c) , {"pair", "a", "b", "quote", "support", "cos", "dormant_days", "active_days"})


class PairBlockedTest(unittest.TestCase):
    P = "甲線|乙線"

    def test_recent_pair_blocked(self):
        led = [{"key": self.P, "ts": NOW - 3 * DAY, "verdict": "hit"}]
        self.assertTrue(foresight.pair_blocked(self.P, led, [], NOW))

    def test_old_pair_allowed(self):
        led = [{"key": self.P, "ts": NOW - 30 * DAY, "verdict": "hit"}]
        self.assertFalse(foresight.pair_blocked(self.P, led, [], NOW))

    def test_two_misses_forever(self):
        led = [{"key": self.P, "ts": NOW - 40 * DAY, "verdict": "miss"},
               {"key": self.P, "ts": NOW - 30 * DAY, "verdict": "miss"}]
        self.assertTrue(foresight.pair_blocked(self.P, led, [], NOW))   # 同一對不准猜第三次

    def test_insight_collision(self):
        self.assertTrue(foresight.pair_blocked(self.P, [], [{"pair": self.P, "ts": NOW - 3600}], NOW))

    def test_clean(self):
        self.assertFalse(foresight.pair_blocked(self.P, [], [], NOW))


class VerdictTest(unittest.TestCase):
    """裁決與命題對得上：命題是「B 會再回來」，裁決就是「B 有沒有再出現一筆」。零參數。"""

    def _hyp(self, told=True, ttl=7 * DAY):
        return {"pair": "甲線|乙線", "a": "甲線", "b": "乙線", "quote": "乙線 的真實原文",
                "born_ts": NOW - 2 * DAY, "told_ts": (NOW - 2 * DAY) if told else 0, "ttl_s": ttl}

    def test_hit_takes_earliest_new_record(self):
        recs = [_rec("乙線", "後來又寫的第二筆", NOW - 0.5 * DAY),
                _rec("乙線", "後來又寫的第一筆", NOW - DAY)]
        vd, ev, evid = foresight.verdict(self._hyp(), recs, NOW)
        self.assertEqual(vd, "hit")
        self.assertEqual(ev, "後來又寫的第一筆")       # 最早那筆、逐字
        self.assertTrue(evid)

    def test_old_record_is_not_evidence(self):
        recs = [_rec("乙線", "假設之前就寫過的", NOW - 5 * DAY)]
        self.assertEqual(foresight.verdict(self._hyp(), recs, NOW)[0], "open")

    def test_other_line_is_not_evidence(self):
        self.assertEqual(foresight.verdict(self._hyp(), [_rec("甲線", "別條線", NOW)], NOW)[0], "open")

    def test_label_match_is_exact(self):
        self.assertEqual(foresight.verdict(self._hyp(), [_rec("乙線｜子題", "x", NOW)], NOW)[0], "open")

    def test_miss_after_ttl(self):
        h = self._hyp()
        self.assertEqual(foresight.verdict(h, [], NOW + 10 * DAY)[0], "miss")

    def test_never_told_never_judged(self):
        # 沒說出口的假設永不裁決 ⇒ bot 不可能事後宣稱「我早就猜到」
        h = self._hyp(told=False)
        self.assertEqual(foresight.verdict(h, [_rec("乙線", "新的", NOW)], NOW)[0], "open")
        self.assertEqual(foresight.verdict(h, [], NOW + 99 * DAY)[0], "open")


class GroundingTest(unittest.TestCase):
    """四道自檢：主動路徑上唯一的內容守門（§1.36 包在 _say 的互動限定分支內，這條 lane 走不到）。"""

    RECS = [_rec("讀誦經書", "今天讀到第三品，心很靜", NOW - 10 * DAY),
            _rec("跑步", "早上跑了五公里", NOW - DAY)]

    def _cps(self):
        return foresight.corpus(self.RECS)

    def _ok(self, t):
        return foresight.grounding_ok(t, self._cps(),
                                      recall_hit=monitor._recall_hallucination_hit,
                                      write_fix=monitor._write_claim_fix,
                                      wc_ground={"today_count": 0, "last_label": "昨天 08:12"})

    def test_corpus_includes_topic_label(self):
        # 實測根因：monitor._recall_corpus 只收 text ⇒ 引用真實標籤會被誤判成幻覺
        self.assertIn("讀誦經書", self._cps())
        self.assertNotIn("讀誦經書", monitor._recall_corpus(self.RECS))

    def test_verbatim_quote_passes(self):
        ok, why = self._ok("「讀誦經書」這條線停在「今天讀到第三品，心很靜」之後就沒再動了。我猜它會再回來一次。")
        self.assertTrue(ok, why)

    def test_fabricated_quote_blocked(self):
        ok, why = self._ok("「讀誦經書」停在「上禮拜跟家人吵架的那件事」之後就安靜了。")
        self.assertFalse(ok)
        self.assertEqual(why, "偽引用")

    def test_fabricated_outside_quote_blocked(self):
        # §1.36 對這款**放行**（實測）——③ 才攔得住，這是 bot 最典型的幻覺形態之一
        t = "「讀誦經書」那條線停在你上次提到的那場家庭聚餐之後就安靜了。"
        self.assertFalse(monitor._recall_hallucination_hit(t, self._cps()))
        ok, why = self._ok(t)
        self.assertFalse(ok)
        self.assertEqual(why, "引號外過去指涉")

    def test_write_claim_blocked(self):
        ok, why = self._ok("你今天又寫了「跑步」這條線，我猜「讀誦經書」會再回來。")
        self.assertFalse(ok)
        self.assertIn(why, ("記寫宣稱", "引號外過去指涉"))

    def test_empty_safe(self):
        self.assertTrue(self._ok("我猜那條線會再回來一次。")[0])


class FallbackTemplateTest(unittest.TestCase):
    """退路模板必須自己過得了四道自檢（否則 fail-closed ⇒ 永遠沉默）。"""

    RECS = [_rec("讀誦經書", "今天讀到第三品，心很靜", NOW - 10 * DAY),
            _rec("跑步", "早上跑了五公里", NOW - DAY)]

    def _ok(self, t):
        return foresight.grounding_ok(t, foresight.corpus(self.RECS),
                                      recall_hit=monitor._recall_hallucination_hit,
                                      write_fix=monitor._write_claim_fix,
                                      wc_ground={"today_count": 0, "last_label": "昨天 08:12"})[0]

    def _cand(self):
        return {"a": "跑步", "b": "讀誦經書", "quote": "今天讀到第三品，心很靜"}

    def test_all_four_propose_variants_pass(self):
        for v in range(4):
            t = foresight.propose_text(self._cand(), v)
            self.assertTrue(self._ok(t), f"variant {v}: {t}")

    def test_settle_templates_pass(self):
        h = {"b": "讀誦經書", "quote": "今天讀到第三品，心很靜"}
        self.assertTrue(self._ok(foresight.settle_text(h, "hit", "早上跑了五公里")))
        self.assertTrue(self._ok(foresight.settle_text(h, "miss")))

    def test_disclaimer_wording_trap(self):
        # 寫給後人的坑：免責句若寫「不是你寫過的事」，§1.36 第二式會抓到、去停用詞後剩「過事」不在語料
        # ⇒ **免責句自己觸發幻覺守門**。模板一律用「不是已經成立的事」。
        bad = "我猜「讀誦經書」會再回來。這只是我的猜測，不是你寫過的事。"
        self.assertFalse(self._ok(bad))
        good = "我猜「讀誦經書」會再回來。這只是我的猜測，不是已經成立的事。"
        self.assertTrue(self._ok(good))

    def test_templates_avoid_banned_phrasings(self):
        for v in range(4):
            t = foresight.propose_text(self._cand(), v)
            self.assertNotIn("今天", t.replace("今天讀到第三品，心很靜", ""))   # 引文本身除外
            self.assertNotIn("有關", t)
            self.assertFalse(monitor._BALL_BACK_RE.search(t))                  # 不把球踢回去（§1.87）


class RuleBlockTest(unittest.TestCase):
    """規則塊：禁令齊全、**不給範例句**（§1.69/§1.50 前科）、形態由程式輪替。"""

    def test_propose_rule_bans(self):
        r = persona.foresight_propose_rule("A", "B", "Q", 0)
        for must in ("逐字", "不准提任何時間", "不准承諾任何未來動作", "不要把選擇丟回去",
                     "會不會跟…有關", "不要報任何數字"):
            self.assertIn(must, r)

    def test_variants_rotate(self):
        got = {persona.foresight_propose_rule("A", "B", "Q", v).split("【必守】")[0] for v in range(4)}
        self.assertEqual(len(got), 4)                    # 四個形態真的不同（程式決定、不靠 LLM 自覺）

    def test_pick_rule_digit_only(self):
        r = persona.foresight_pick_rule([{"a": "A", "b": "B", "quote": "Q"}])
        self.assertIn("只准回一個阿拉伯數字", r)
        self.assertIn("不要自己想新的主題", r)

    def test_hit_rule_bans_taking_credit(self):
        r = persona.foresight_settle_rule("B", "Q", "hit", "E")
        self.assertIn("不要邀功", r)
        self.assertIn("也可能是因為你先提過", r)

    def test_miss_rule_bans_excuses(self):
        r = persona.foresight_settle_rule("B", "Q", "miss")
        self.assertIn("不准找補", r)
        self.assertIn("不要問他為什麼沒寫", r)


class LedgerTest(unittest.TestCase):
    def test_push_and_cap(self):
        led = []
        for i in range(12):
            led = foresight.push_ledger(led, {"pair": f"p{i}", "a": "a", "b": "b", "born_ts": i}, "miss", NOW)
        self.assertEqual(len(led), 8)
        self.assertEqual(led[-1]["pair"], "p11")

    def test_audit_text_observable(self):
        s = SimpleNamespace(foresight=None, foresight_ledger=[{"verdict": "hit"}, {"verdict": "miss"}])
        t = foresight.audit_text(s, [], {"A": {}}, blocked_why="冷卻中")
        self.assertIn("猜中 1 次", t)
        self.assertIn("想錯 1 次", t)
        self.assertIn("冷卻中", t)


class StatePersistenceTest(unittest.TestCase):
    """§1.79 前科（last_habit_absence_ts 從沒進 state.py ⇒ 重生即歸零）：三處都要有。"""

    def test_three_places(self):
        src = io.open("telegram_monitor/state.py", encoding="utf-8").read()
        for f in ("foresight", "foresight_ledger", "last_foresight_ts"):
            self.assertGreaterEqual(src.count(f), 3, f)

    def test_roundtrip(self):
        p = os.path.join(tempfile.mkdtemp(), "s.json")
        s = State(p)
        s.foresight = {"pair": "x|y", "b": "y", "born_ts": 1.0}
        s.foresight_ledger = [{"key": "x|y", "verdict": "miss", "ts": 1.0}]
        s.last_foresight_ts = 123.0
        s.save()
        s2 = State.load(p)     # ★ load 是 classmethod；State(p) 只是建構、不會讀檔
        self.assertEqual(s2.foresight["pair"], "x|y")
        self.assertEqual(s2.foresight_ledger[0]["verdict"], "miss")
        self.assertEqual(s2.last_foresight_ts, 123.0)


class ConfigTest(unittest.TestCase):
    def test_flags_synced(self):
        src = io.open("telegram_monitor/config.py", encoding="utf-8").read()
        for f in ("FORESIGHT", "foresight_enabled", "foresight_cooldown_min", "foresight_ttl_days"):
            self.assertIn(f, src)
        env = io.open(".env.example", encoding="utf-8").read()
        self.assertRegex(env, re.compile(r"^FORESIGHT=1", re.M))
        self.assertRegex(env, re.compile(r"^FORESIGHT_TTL_DAYS=7", re.M))
        self.assertIn("FORESIGHT", io.open("README.md", encoding="utf-8").read())

    def test_getattr_defaults_false(self):
        self.assertFalse(getattr(SimpleNamespace(), "foresight_enabled", False))


if __name__ == "__main__":
    unittest.main()
