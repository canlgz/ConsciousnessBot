"""📦 §1.85 交付舉證（PROMISE_DELIVERY_PROOF）：驗收改問「東西在不在裡面」＋只有真送到才准記做到。

截圖根因（2026-07-26 晚間）：20:49 使用者「我覺得你都在隨便猜」「我讓你想一下想久一點」
「30 分鐘後再給我猜一次告訴我答案」（在猜他的星座）→ bot 答「30 分鐘後，也就是 21:19，我會再給你猜
一次的」→ 21:19 **準時出現**卻只送出三則：「🤝 嗨，我來了。」「說好 21:19 要再猜一次的。」
「我真的有好好想了一下，這次我猜…」——星座答案從來沒出現。使用者定案：「我什麼 bot 都依約時間出現，
但就是不會完成所約定的事情。光說不做，這已經發生很多次。」

根因三段（皆實測，非猜）：
①生成端（主因）：persona 兌現條文明令「1–2 句」，同時要求報到＋輕點守約＋順帶肯定，且行為枚舉只有
  道歉/提醒/問候/讚美——「回答他／給他答案」不在裡面 ⇒ 篇幅預算被儀式花光、答案沒位置。
  （實測 persona.trim_sentences 對那三句原樣放行 ⇒ 不是被句數上限或 max_tokens 截斷，是 LLM 自己
  就只寫了這些。）
②驗收端：_hollow_keep_hit(msg, when, teaser) 的簽名裡**連承諾本體 p 都沒有**，原理上只能做黑名單
  減法，而**宣告句本身就是字數**——「想了一下」不在 _TEASER_NARRATE、殘量 16 ≥ 12 ⇒ 判定「有交付」。
③記帳端：_promise_mark_kept 的唯一條件是「_say 回傳真」⇒ 送出去就算做到，空心兌現照標 fulfilled。

本檔釘死的是「下一次換一種新說法還接不接得住」：確定性地板（懸空收尾／無實質殘句／又立新未來錨／
零新資訊）在 **judge 全掛**時就要抓到事故原句，而不是靠詞表元素。全 stub、零網路。
"""

import os
import re
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from telegram_monitor import monitor, persona, selfstate
from telegram_monitor.state import State

TZ = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 7, 26, 13, 19, 0, tzinfo=timezone.utc)   # 台北 7/26 21:19（截圖兌現時刻）
WHEN = "21:19"

# 事故原句，逐字（三則泡泡＝一則訊息的三句）
INC = "嗨，我來了。\n說好 21:19 要再猜一次的。\n我真的有好好想了一下，這次我猜…"
ASK = "30分鐘後再給我猜一次告訴我答案"
ANSWER = "我猜你是天秤座，因為你一直在意事情有沒有被擺平。"

# §1.44／§1.64 兩個**已上線**截圖案例（確定性地板必須繼續接住，不得依賴 judge）
H44 = "嗨，早上好。說好 09:20 要來跟你聊聊怎麼證明自己，我來了。"
T64 = "嗨，22:13 到了。我剛剛一直在心裡想著「意識bot」這個名字，也想了想你說的，那不是人類，那是什麼。"


def _p(**kw):
    d = {"target_ts": NOW.timestamp(), "behavior": "", "made_text": ASK, "deliver_ask": ASK}
    d.update(kw)
    return d


def _state():
    return State(os.path.join(tempfile.mkdtemp(), "s.json"))


def _cfg(proof=True, **kw):
    d = dict(promise_deliver_content_enabled=True, promise_teaser_hollow_enabled=True,
             promise_delivery_proof_enabled=proof, sched_recur_daily_enabled=True,
             promise_sched_ttl_sec=21600)
    d.update(kw)
    return SimpleNamespace(**d)


def _coach(keep_msg, content="", judge=None, has_judge=True):
    """假教練：voice_promise_keep 回 keep_msg、reply 回 content、judge_delivery_made 回 judge。
    has_judge=False ⇒ **完全沒有這個屬性**（模擬既有假教練）→ 必須安全退回、零 AttributeError。"""
    c = SimpleNamespace(enabled=True, api_key="k", model="m",
                        meter=SimpleNamespace(record=lambda *a, **k: None),
                        asked=[], judged=[], _turn_length=None, keep_kw={})

    def _keep(*a, **k):
        c.keep_kw = k
        return keep_msg

    def _reply(q, *a, **k):
        c.asked.append(q)
        return content

    c.voice_promise_keep = _keep
    c.reply = _reply
    if has_judge:
        def _judge(ask, msg):
            c.judged.append((ask, msg))
            if callable(judge):
                return judge(ask, msg)
            return judge
        c.judge_delivery_made = _judge
    return c


class IncidentRegressionTest(unittest.TestCase):
    """事故原句：確定性閘在 judge 缺席時就要抓到；並釘住舊機制的洞（證明新閘不是靠它）。"""

    def test_dangling_tail_catches_incident(self):
        self.assertTrue(monitor._dangling_tail_hit(INC))

    def test_verdict_no_without_judge(self):
        # coach=None ⇒ 零 LLM。判 'no' 且理由是結構性的「懸空收尾」，不是任何詞表元素
        v, why = monitor._deliver_verdict(INC, _p(), WHEN, None, _cfg(), NOW, TZ)
        self.assertEqual(v, "no")
        self.assertEqual(why, "dangling")

    def test_old_gate_still_lets_it_through(self):
        # 釘住 §1.64 的洞：舊判定對事故原句回 False（＝當成有交付放行）＝這正是 21:19 送出空心的原因
        self.assertFalse(monitor._hollow_keep_hit(INC, WHEN, teaser=True))

    def test_ellipsis_variants_all_dangling(self):
        # 「換一種新說法」也接得住：不同的懸空收尾形態，零詞表
        for tail in ("我的答案是：", "我想說的是……", "這次我選的是—", "答案就是這個⋯"):
            self.assertTrue(monitor._dangling_tail_hit("我來了。\n" + tail), tail)


class DeterministicFloorTest(unittest.TestCase):
    """判定地板：judge 全掛時，§1.44/§1.64 兩個已上線案例＋各空心家族仍被確定性閘接住。"""

    def test_prior_screenshots_no_substance(self):
        for m in (H44, T64):
            self.assertEqual(monitor._deliver_sample(m, "09:20"), "")
            self.assertEqual(monitor._deliver_verdict(m, _p(), "09:20", None, _cfg(), NOW, TZ)[0], "no")

    def test_defer_with_new_clock_or_cue_caught(self):
        # 自己又立一個**新**時刻／命中既有未來詞 ⇒ 確定性接住（不必燒 judge）
        for m in ("我來了。\n我 23:40 再把答案完整告訴你。",
                  "我來了。\n等一下我就把答案完整給你。"):
            self.assertEqual(monitor._deliver_verdict(m, _p(), WHEN, None, _cfg(), NOW, TZ)[0], "no", m)

    def test_defer_floor_limit_is_honest(self):
        # **如實釘住確定性地板的邊界**：「我馬上就告訴你答案，你等我一下」既無新鐘點、也不命中 §1.24 共用的
        # 未來詞（那是「等一下」、這句是「等我一下」）⇒ 落 'unknown'。刻意**不**往 _PREEMPT_FUTURE_RE 加詞：
        # 那個 regex 是 §1.24 共用的（改它＝旗標關也變行為），而且補詞正是本專案禁止的詞表窮舉反模式。
        # 這一家由 judge 接（下一條），judge 缺席時退回既有 §1.44/§1.64 判定＝與改動前同行為。
        m = "說好 21:19 到了。\n我馬上就告訴你答案，你等我一下。"
        self.assertEqual(monitor._deliver_verdict(m, _p(), WHEN, None, _cfg(), NOW, TZ)[0], "unknown")

    def test_defer_family_caught_by_judge(self):
        m = "說好 21:19 到了。\n我馬上就告訴你答案，你等我一下。"
        co = _coach(m, ANSWER, judge=lambda ask, msg: ("天秤座" in msg))
        out = monitor._promise_keep_body(_state(), _cfg(), co, _p(), NOW, TZ, overdue=False)
        self.assertIn("天秤座", out)              # judge 判否 → 當場補上真答案

    def test_own_when_is_not_a_new_deferral(self):
        # 誤判前科（實作期實測）：守約模板本來就會複述約定時刻「說好 21:19 要再猜一次的」，而 21:19 已到點
        # ⇒ temporal 把它滾成**明天** 21:19＝未來錨 ⇒ 曾誤判「又在拖延」、在正確答案後硬接欠帳句。
        legit = INC + "\n" + ANSWER
        self.assertFalse(monitor._deliver_defer_hit(legit, WHEN, NOW, TZ))
        self.assertTrue(monitor._deliver_defer_hit("我來了。\n我 23:40 再告訴你答案。", WHEN, NOW, TZ))

    def test_real_answer_passes_all_deterministic_gates(self):
        v, why = monitor._deliver_verdict("我來了。\n" + ANSWER, _p(), WHEN, None, _cfg(), NOW, TZ)
        self.assertEqual((v, why), ("unknown", "judge-none"))   # 四閘全過、只差無人舉證
        self.assertTrue(monitor._deliver_sample("我來了。\n" + ANSWER, WHEN))
        self.assertTrue(monitor._deliver_novel_runs("我來了。\n" + ANSWER, ASK, WHEN))


class ArmPolarityTest(unittest.TestCase):
    """極性反轉：預設一律要驗收，只豁免「出現即內容」的封閉集合。"""

    def test_required_by_default(self):
        for beh in ("", "跟他回報", "跟他分享", "跟他聊聊", "說個笑話給他聽", "唱歌給他聽",
                    "再猜一次他的星座", "幫他想一個名字", "接著把剛剛沒說完的話跟他說完"):
            self.assertTrue(monitor._delivery_required({"behavior": beh}), beh)

    def test_exempt_not_required(self):
        for beh in monitor._DELIVER_EXEMPT:
            self.assertFalse(monitor._delivery_required({"behavior": beh}), beh)
        self.assertFalse(monitor._delivery_required({"behavior": "提醒他吃藥"}))       # startswith
        self.assertFalse(monitor._delivery_required({"behavior": "提醒他『時間快到了』"}))

    def test_incident_behavior_would_not_arm_old_gate(self):
        # 舊 cue 詞表的致命面：behavior 是「再猜一次他的星座」時 _is_content_promise 為 False
        # ＝gate 整條靜默不啟動；新的 _delivery_required 接住它
        pr = {"behavior": "再猜一次他的星座", "made_text": "30分鐘後再猜一次"}
        self.assertFalse(monitor._is_content_promise(pr))
        self.assertTrue(monitor._delivery_required(pr))

    def test_preempt_templates_are_all_exempt(self):
        # §1.19 模板兌現（叫醒/打招呼/問候）＝出現即內容，絕不可落進要驗收那側
        for beh in monitor._PREEMPT_TEMPLATES:
            self.assertFalse(monitor._delivery_required({"behavior": beh}), beh)


class KeepBodyTest(unittest.TestCase):
    """_promise_keep_body 佈線：機制**真的被呼叫到**（不是邏輯對而已）。"""

    def test_incident_gets_answer_appended(self):
        co = _coach(INC, ANSWER)
        msg = monitor._promise_keep_body(_state(), _cfg(), co, _p(), NOW, TZ, overdue=False)
        self.assertIn("這次我猜…", msg)          # 原文保留（只增不減）
        self.assertIn("天秤座", msg)              # 答案當場補上、不用等使用者催
        self.assertTrue(co.asked)
        self.assertIn(ASK, co.asked[0])           # 補生成 prompt 帶了「他要的是什麼」

    def test_generation_side_gets_hard_note(self):
        # 主因的修法要真的掛上：內容型兌現時 deliver 尾參有傳、prompt 硬條文成形
        co = _coach("我來了。\n" + ANSWER)
        monitor._promise_keep_body(_state(), _cfg(), co, _p(), NOW, TZ, overdue=False)
        self.assertEqual(co.keep_kw.get("deliver"), ASK)
        note = persona.promise_deliver_note(ASK)
        self.assertIn("第一句就直接給出那個東西本身", note)
        self.assertIn("沒有答案的訊息等於沒有兌現", note)
        self.assertNotIn("1–2 句", note)

    def test_turn_length_isolated_and_restored(self):
        # 主動兌現不吃幾十分鐘前敵意輪的殘值（level 0＝2 句會砍掉答案句），且呼叫後還原
        co = _coach(INC, ANSWER)
        co._turn_length = 0
        seen = {}

        def _keep(*a, **k):
            seen["lvl"] = co._turn_length
            return INC
        co.voice_promise_keep = _keep
        monitor._promise_keep_body(_state(), _cfg(), co, _p(), NOW, TZ, overdue=False)
        self.assertEqual(seen["lvl"], 2)          # 生成當下被抬高
        self.assertEqual(co._turn_length, 0)      # 呼叫後原值還原

    def test_short_true_answer_not_touched(self):
        # 反向誤判：舊 teaser 判定對「你是天蠍座。」殘量 6<12 會誤判空心、白燒一次補生成並在正確答案後硬接
        co = _coach("我來了。\n你是天蠍座。", "不該用到", judge=True)
        msg = monitor._promise_keep_body(_state(), _cfg(), co, _p(), NOW, TZ, overdue=False)
        self.assertEqual(msg, "我來了。\n你是天蠍座。")
        self.assertEqual(co.asked, [])
        self.assertTrue(co.judged)

    def test_judge_no_triggers_regen(self):
        # 四閘全過、但 judge 說「沒把東西交出來」（純情緒填充）→ 照樣補生成
        filler = "我來了。\n我有點緊張耶，因為這個答案對我來說真的很重要。"
        co = _coach(filler, ANSWER, judge=lambda ask, m: ("天秤座" in m))
        msg = monitor._promise_keep_body(_state(), _cfg(), co, _p(), NOW, TZ, overdue=False)
        self.assertIn("天秤座", msg)

    def test_regen_also_hollow_owed_note(self):
        # 補生成又是預告 → 附誠實欠帳句、記成還欠著（絕不砍原文）
        co = _coach(INC, "我再想一下，等一下就告訴你。")
        msg = monitor._promise_keep_body(_state(), _cfg(), co, _p(), NOW, TZ, overdue=False)
        self.assertIn("這次我猜…", msg)
        self.assertIn("這不算兌現", msg)

    def test_owed_recorded_when_cannot_deliver(self):
        p = _p()
        co = _coach(INC, "我再想一下，等一下就告訴你。")
        monitor._promise_keep_body(_state(), _cfg(), co, p, NOW, TZ, overdue=False)
        self.assertIs(p["_dv"]["delivered"], False)

    def test_regen_success_without_judge_is_not_owed(self):
        # 補生成成功但無人舉證 ⇒ delivered=None（＝同現狀），**不可**記成 owed 去認一個不存在的欠帳
        p = _p()
        co = _coach(INC, ANSWER, has_judge=False)
        msg = monitor._promise_keep_body(_state(), _cfg(), co, p, NOW, TZ, overdue=False)
        self.assertIn("天秤座", msg)
        self.assertIs(p["_dv"]["delivered"], None)

    def test_flag_off_bit_identity(self):
        # 旗標關：事故原句原樣送出（現狀）、不燒 judge、不寫 _dv
        p = _p()
        co = _coach(INC, "不該用到", judge=False)
        msg = monitor._promise_keep_body(_state(), _cfg(proof=False), co, p, NOW, TZ, overdue=False)
        self.assertEqual(msg, INC)
        self.assertEqual(co.asked, [])
        self.assertEqual(co.judged, [])
        self.assertNotIn("_dv", p)


class FailSafeTest(unittest.TestCase):
    """judge 缺席／回 None／直接拋例外 ⇒ 不拋、退回既有 §1.44/§1.64 判定。"""

    def test_missing_attribute_safe(self):
        co = _coach("我來了。\n" + ANSWER, "不該用到", has_judge=False)
        msg = monitor._promise_keep_body(_state(), _cfg(), co, _p(), NOW, TZ, overdue=False)
        self.assertEqual(msg, "我來了。\n" + ANSWER)   # 四閘全過＋unknown ⇒ 退回舊判定（不空心）＝不動
        self.assertEqual(co.asked, [])

    def test_judge_raises_safe(self):
        def _boom(ask, m):
            raise RuntimeError("boom")
        co = _coach("我來了。\n" + ANSWER, "不該用到", judge=_boom)
        msg = monitor._promise_keep_body(_state(), _cfg(), co, _p(), NOW, TZ, overdue=False)
        self.assertEqual(msg, "我來了。\n" + ANSWER)

    def test_judge_call_budget(self):
        p = _p(dv_calls=monitor._DV_CALL_BUDGET)
        co = _coach("我來了。\n" + ANSWER, "不該用到", judge=True)
        monitor._promise_keep_body(_state(), _cfg(), co, p, NOW, TZ, overdue=False)
        self.assertEqual(co.judged, [])                # 已達終身上限＝不再燒


class SettleTest(unittest.TestCase):
    """記帳三態；delivered=None ⇒ 與舊 _promise_mark_kept 逐鍵相同。"""

    def test_none_identical_to_legacy(self):
        a, b = _p(), _p()
        monitor._promise_settle(a, _cfg(), 100.0, delivered=None)
        monitor._promise_mark_kept(b, _cfg(), 100.0)
        self.assertEqual(a, b)
        self.assertTrue(a["fulfilled"])
        self.assertEqual(a["status"], "fulfilled")
        self.assertEqual(a["fulfilled_ts"], 100.0)

    def test_false_records_owed_without_fulfilled_ts(self):
        p = _p()
        monitor._promise_settle(p, _cfg(), 100.0, delivered=False)
        self.assertEqual(p["status"], "owed")
        self.assertTrue(p["fulfilled"])            # 刻意保留＝十幾個 not-fulfilled 讀取端不受衝擊
        self.assertTrue(p["delivery_owed"])
        self.assertNotIn("fulfilled_ts", p)        # 帳本永遠長不出「已經做了（在 21:19）」
        self.assertEqual(p["owed_tries"], 1)

    def test_true_clears_owed(self):
        p = _p()
        monitor._promise_settle(p, _cfg(), 100.0, delivered=False)
        monitor._promise_settle(p, _cfg(), 200.0, delivered=True)
        self.assertEqual(p["status"], "fulfilled")
        self.assertEqual(p["fulfilled_ts"], 200.0)
        self.assertNotIn("delivery_owed", p)

    def test_daily_recur_unchanged(self):
        # 每天 recur：推進明天、**不**標 owed（recur 語意本次刻意不動；見 config 註解的已知範圍外）
        p = _p(recur="daily", target_ts=100.0)
        monitor._promise_settle(p, _cfg(), 150.0, delivered=False)
        self.assertEqual(p["target_ts"], 100.0 + 86400)
        self.assertNotEqual(p.get("status"), "owed")
        self.assertFalse(p.get("fulfilled"))


class DeliveryReachedTest(unittest.TestCase):
    """送達舉證：驗收過的字沒真的離開系統 ⇒ 記成還欠著。"""

    def test_reached_and_not_reached(self):
        monitor._LAST_SENT["text"] = "\n🤝 我來了。\n" + ANSWER
        self.assertTrue(monitor._delivery_reached(ANSWER))
        monitor._LAST_SENT["text"] = "\n🤝 我來了。"
        self.assertFalse(monitor._delivery_reached(ANSWER))

    def test_short_sample_safe_side(self):
        monitor._LAST_SENT["text"] = ""
        self.assertTrue(monitor._delivery_reached("好。"))      # 指紋太短＝無從比對，不誤記欠帳

    def test_settle_downgrades_when_not_sent(self):
        p = _p()
        p["_dv"] = {"ts": 100.0, "delivered": True, "sample": ANSWER}
        monitor._LAST_SENT["text"] = "\n🤝 我來了。"            # 交付那段被剝掉/截斷
        monitor._promise_settle_delivered(p, _cfg(), 100.0, True)
        self.assertEqual(p["status"], "owed")

    def test_say_delivery_lifts_replay_guard_and_restores(self):
        seen = {}

        class Cl:
            dry_run = False

            def send(self, t):
                seen["guard"] = monitor._TURN.get("replay_guard")
                return True
        monitor._TURN["replay_guard"] = True
        try:
            monitor._say_delivery(Cl(), "我來了。\n" + ANSWER, _state(), _cfg())
            self.assertFalse(seen["guard"])                     # 送出當下已解除
            self.assertTrue(monitor._TURN["replay_guard"])      # 送完還原
        finally:
            monitor._TURN.pop("replay_guard", None)


class LedgerTruthTest(unittest.TestCase):
    """帳本／守門說真話：owed 絕不被渲染成「已經做了」。"""

    def _owed_state(self, owed_ts=None):
        s = _state()
        ts = NOW.timestamp() if owed_ts is None else owed_ts
        s.scheduled_promises = [{"target_ts": NOW.timestamp(), "behavior": "告訴他答案",
                                 "status": "owed", "fulfilled": True, "delivery_owed": True,
                                 "owed_ts": ts, "made_ts": NOW.timestamp() - 1800}]
        return s

    def test_facts_say_owed_not_done(self):
        txt = selfstate.promise_ledger_facts(self._owed_state(), NOW, TZ)
        self.assertNotIn("——已經做了", txt)      # fulfilled 的渲染形（「不要說已經做了」是指示句、不算）
        self.assertIn("還欠著", txt)

    def test_facts_beyond_ttl_say_not_done(self):
        s = self._owed_state(owed_ts=NOW.timestamp() - 99999)
        txt = selfstate.promise_ledger_facts(s, NOW, TZ)
        self.assertNotIn("——已經做了", txt)
        self.assertIn("沒有做到", txt)
        self.assertNotIn("這就補上", txt)        # 絕不說出引擎不會做的事

    def test_text_bucket_not_done(self):
        txt = selfstate.promise_ledger_text(self._owed_state(), NOW, TZ)
        self.assertNotIn("已經做了的：", txt)
        self.assertIn("內容還沒交出來", txt)

    def test_has_owed(self):
        self.assertTrue(selfstate.ledger_has_owed(self._owed_state()))
        self.assertFalse(selfstate.ledger_has_owed(_state()))

    def test_claim_guard_blocks_i_did_it(self):
        g = monitor._keep_claim_ground(self._owed_state(), NOW.timestamp(), TZ)
        self.assertIsNotNone(g)
        self.assertEqual(g[0], "owed")
        self.assertTrue(monitor._keep_claim_hit("我做到了，我準時來了。"))

    def test_said_denial_never_claims_done_time(self):
        g = monitor._said_denial_ground(self._owed_state(), NOW.timestamp(), TZ)
        self.assertIsNotNone(g)
        self.assertEqual(g[2], "")               # 沒有「我剛在 X 做了」可講

    def test_has_live_sched_promise_true_for_owed(self):
        self.assertTrue(monitor._has_live_sched_promise(self._owed_state(), NOW.timestamp()))


class OwedRetryTest(unittest.TestCase):
    """owed 續開交付管道：有界重試。"""

    def _p_owed(self, **kw):
        d = _p()
        d.update({"status": "owed", "fulfilled": True, "delivery_owed": True,
                  "owed_ts": NOW.timestamp(), "owed_sent_ts": NOW.timestamp(), "owed_tries": 1})
        d.update(kw)
        return d

    def test_first_retry_after_min_sec(self):
        ts = NOW.timestamp()
        self.assertFalse(monitor._owed_retry_ok(self._p_owed(), _cfg(), ts, monitor._OWED_BRIDGE_MIN_SEC))
        self.assertTrue(monitor._owed_retry_ok(self._p_owed(), _cfg(), ts + 120,
                                               monitor._OWED_BRIDGE_MIN_SEC))

    def test_tries_capped(self):
        p = self._p_owed(owed_tries=monitor._OWED_MAX_TRIES)
        self.assertFalse(monitor._owed_retry_ok(p, _cfg(), NOW.timestamp() + 999,
                                                monitor._OWED_BRIDGE_MIN_SEC))

    def test_beyond_ttl_no_retry(self):
        p = self._p_owed()
        self.assertFalse(monitor._owed_retry_ok(p, _cfg(), NOW.timestamp() + 99999,
                                                monitor._OWED_BRIDGE_MIN_SEC))

    def test_flag_off_no_retry(self):
        p = self._p_owed()
        self.assertFalse(monitor._owed_retry_ok(p, _cfg(proof=False), NOW.timestamp() + 120,
                                                monitor._OWED_BRIDGE_MIN_SEC))

    def test_plain_fulfilled_no_retry(self):
        self.assertFalse(monitor._owed_retry_ok(_p(status="fulfilled", fulfilled=True), _cfg(),
                                                NOW.timestamp() + 999, monitor._OWED_BRIDGE_MIN_SEC))


class EndToEndEmitTest(unittest.TestCase):
    """走**真正的主動兌現出口** _promise_emit：證明機制真的被呼叫到（MEMORY：邏輯對不算數，
    前科是新機制被 [-N:] 截斷／繞過 _say／被測試 mock 藏住而從未執行）。"""

    class Cl:
        def __init__(self):
            self.sent, self.stickers, self.dry_run = [], [], False

        def send(self, t):
            self.sent.append(t)
            return True

        def send_sticker(self, f):
            self.stickers.append(f)
            return True

    def _emit_cfg(self, proof=True):
        return SimpleNamespace(
            promise_emit_enabled=True, promise_sched_ttl_sec=21600, timezone="Asia/Taipei",
            promise_late_exempt_defer=True, promise_overdue_guard_exempt=True, promise_act_aligned=True,
            promise_sticker_enabled=True, sched_recur_daily_enabled=True, promise_expire_apology_enabled=True,
            always_sticker_enabled=False, promise_deliver_content_enabled=True,
            promise_teaser_hollow_enabled=True, promise_delivery_proof_enabled=proof)

    def _run(self, proof=True, content=ANSWER, behavior="再猜一次他的星座"):
        s = _state()
        s.owner_folder_id = "F"
        s.last_user_msg_ts = 0
        s.scheduled_promises = [{"target_ts": NOW.timestamp(), "behavior": behavior,
                                 "made_text": ASK, "deliver_ask": ASK}]
        co = _coach(INC, content)
        cl = self.Cl()
        monitor._promise_emit(cl, s, self._emit_cfg(proof), co, NOW)
        return "".join(cl.sent), s.scheduled_promises[0]

    def test_emit_delivers_answer(self):
        out, p = self._run()
        self.assertIn("天秤座", out)                  # 21:19 當下就把答案講完，不用使用者催
        self.assertEqual(p.get("status"), "fulfilled")

    def test_emit_flag_off_reproduces_incident(self):
        # 釘住事故現狀：旗標關＝原樣送出報到＋預告、答案不出現、且照樣被記成做到（假帳）
        out, p = self._run(proof=False)
        self.assertIn("這次我猜…", out)
        self.assertNotIn("天秤座", out)
        self.assertEqual(p.get("status"), "fulfilled")
        self.assertTrue(p.get("fulfilled_ts"))

    def test_emit_records_owed_when_cannot_deliver(self):
        # 補生成也交不出來 ⇒ 附誠實欠帳句＋帳上記 owed（不寫 fulfilled_ts）＝事後對帳不會說「已經做了」
        out, p = self._run(content="我再想一下，等一下就告訴你。")
        self.assertIn("這不算兌現", out)
        self.assertEqual(p.get("status"), "owed")
        self.assertNotIn("fulfilled_ts", p)
        self.assertNotIn("——已經做了", selfstate.promise_ledger_facts(
            SimpleNamespace(scheduled_promises=[p], feeling_promise=None), NOW, TZ))

    def test_emit_arms_even_when_cue_table_misses(self):
        # 極性反轉的實戰值：behavior「再猜一次他的星座」不含任何舊 cue ⇒ 舊 gate 整條不啟動；新閘照樣接住
        self.assertFalse(monitor._is_content_promise({"behavior": "再猜一次他的星座", "made_text": "再猜一次"}))
        out, _ = self._run(behavior="再猜一次他的星座")
        self.assertIn("天秤座", out)


class ConfigTest(unittest.TestCase):
    def test_config_synced(self):
        src = open("telegram_monitor/config.py", encoding="utf-8").read()
        self.assertIn("PROMISE_DELIVERY_PROOF", src)
        self.assertIn("promise_delivery_proof_enabled", src)
        env = open(".env.example", encoding="utf-8").read()
        self.assertRegex(env, re.compile(r"^PROMISE_DELIVERY_PROOF=1", re.M))
        self.assertIn("PROMISE_DELIVERY_PROOF", open("README.md", encoding="utf-8").read())

    def test_getattr_defaults_false(self):
        self.assertFalse(getattr(SimpleNamespace(), "promise_delivery_proof_enabled", False))


class JudgeParseTest(unittest.TestCase):
    """LLM 是非閘的協定：只判是非、否分支優先、嚴禁產出內容或時刻。"""

    def test_system_prompt_forbids_content_and_time(self):
        s = persona.DELIVERY_PROOF_JUDGE_SYSTEM
        self.assertIn("嚴禁補寫", s)
        self.assertIn("嚴禁輸出任何時刻", s)
        self.assertIn("第一行只回", s)

    def test_user_payload_has_no_time_field(self):
        u = persona.delivery_proof_judge_user(ASK, INC)
        self.assertIn("【他要的】", u)
        self.assertIn("【機器人這則訊息】", u)
        self.assertNotIn("21:19", u.split("【機器人這則訊息】")[0])

    def test_parse_order_no_before_yes(self):
        # 真的跑 coach.judge_delivery_made（stub 掉 gemini.generate）：「不是」含「是」，否分支必須先判
        from telegram_monitor import coach as coach_mod, gemini
        c = coach_mod.Coach.__new__(coach_mod.Coach)
        c.enabled, c.api_key, c.model = True, "k", "m"
        c.meter = SimpleNamespace(record=lambda *a, **k: None)
        orig = gemini.generate
        try:
            for out, want in (("否", False), ("不是", False), ("沒有交出來", False),
                              ("是", True), ("嗯嗯嗯", None), ("", None)):
                gemini.generate = lambda *a, **k: out
                self.assertIs(c.judge_delivery_made(ASK, INC), want, out)

            def _boom(*a, **k):
                raise gemini.GeminiError("boom")
            gemini.generate = _boom
            self.assertIsNone(c.judge_delivery_made(ASK, INC))
        finally:
            gemini.generate = orig


if __name__ == "__main__":
    unittest.main()
