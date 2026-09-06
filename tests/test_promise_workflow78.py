"""🤝 §0.78 workflow 修正：約定時間感知/到點觸發/帳本一致 的全鏈補洞。

截圖真相（四張）：① bot 沒感知到「說一下/說說你的+感覺/狀態」這種**自陳型**約定 → 帳本空、靠對話史空口編造承諾、
亂報時刻（21:42 說成 21:40）；② 空帳本時「時間到了沒/還差多久」落回自由 LLM ＝自相矛盾（「還沒到」＋「已過兩分鐘」）；
③ 活躍對話中每拍前面的自發相都刷 last_push_ts ＝反連發守門恆真 ＝逾期承諾永遠發不出（21:47 那筆到點沒發）；
④ 帳本用寫死 1800s 當「沒做到」門檻，但引擎其實在 6h TTL 內都會補發 ＝帳本說「沒做到」、引擎還在補＝自打臉。

修正（旗標化、預設開、關＝逐位元同現狀）：
- FIX1 root：自陳動詞家族（說一下/說說/講一下/描述/聊一下…你的{感覺,狀態,內在,心情,想法,感受}）進捕捉閘。
  **當下**問（描述你的內在結構/說說你的狀態，無延後/時間）不算承諾 → 交回 self_phenomenal/狀態問句（regression 守門）。
- FIX2：計時狀態問句一律走帳本接地（空帳本也誠實答「此刻幾點＋沒記著約好什麼」），絕不落回自由 LLM。
- FIX3：反連發守門只擋「剛到點」的，**逾期**(>grace)的即使剛推過也照發（break 仍保證一拍至多一則）。
- FIX4/9：帳本「還欠著會補」vs「沒做到」用引擎同一 TTL；只有最早那筆說「馬上補」、其餘「排在後面依序補」。
- FIX5：捕捉去重只認**未兌現**筆、鍵含 behavior（已兌現的同時刻舊筆不擋新約）。
- FIX6：截尾 pending 依**最快到點**留（不被遠期筆擠掉）。
- FIX7：守約句若報了『不是約定時刻、也不是此刻』的時間＝LLM 幻覺 → 落回確定性模板。
"""

import os
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from telegram_monitor import monitor, selfstate, intent, referent, config
from telegram_monitor.state import State, _trim_sched_promises

TZ = ZoneInfo("Asia/Taipei")


class FakeClient:
    def __init__(self):
        self.sent, self.stickers, self.dry_run = [], [], False

    def send(self, text):
        self.sent.append(text)
        return True

    def send_sticker(self, file_id):
        self.stickers.append(file_id)
        return True


def _coach(**over):
    base = dict(
        enabled=True, api_key="k", model="m",
        meter=SimpleNamespace(record=lambda *a, **k: None),
        # reply 回音 facts（第二參數）＝可據此判斷走了哪條路（帳本路徑的 facts 含「沒記著」）
        reply=lambda text, facts="", *a, **k: (facts or "chat-reply"),
        ask=lambda *a, **k: ("chat", None, "chat-reply"),         # fact_or_chat function-calling lane
        remember_turn=lambda *a, **k: None,
        voice_schedule_ack=lambda q, when, h, sticker_hint="": (
            f"好，{when.strftime('%H:%M') if when else '到時候'}我會做。"),
        voice_promise_keep=lambda when, facts, h, promised="", late=False, feeling_ground="", **k:
            ("遲到了抱歉——" if late else "") + f"守約！{promised}",
    )
    base.update(over)
    return SimpleNamespace(**base)


def _cfg(**over):
    base = dict(dry_run=False, telegram_chat_id="", scheduled_promise_enabled=True,
                promise_emit_enabled=True, promise_sched_ttl_sec=21600, timezone="Asia/Taipei",
                notify_cooldown_min=30, promise_reply_bridge_enabled=True, promise_ledger_enabled=True,
                promise_status_ground_enabled=True, promise_status_empty_ground=True,
                promise_overdue_guard_exempt=True, promise_tick_resilient_enabled=True,
                sched_leave_autoarm_enabled=True, deferred_promise_enabled=True,
                sched_recur_daily_enabled=True, promise_act_aligned=True)
    base.update(over)
    return SimpleNamespace(**base)


def _msg(text, when):
    return {"message": {"chat": {"id": 1}, "text": text, "date": when.timestamp()}}


class _Benign:
    """寬容 datatools 樁：任何方法回空清單，讓 fact_or_chat 路徑跑完不炸（不當場否決測試意圖）。"""
    def __getattr__(self, name):
        return lambda *a, **k: []


_BOOM = SimpleNamespace(
    load_embedding_records=lambda *_: (_ for _ in ()).throw(AssertionError("約定/帳本路徑不該落資料查詢")))


def _hhmm(ts):
    return datetime.fromtimestamp(ts, timezone.utc).astimezone(TZ).strftime("%H:%M")


# ─────────────────────────────────────────────────────────────────────────────
# FIX 1（root）：自陳型約定捕捉 + 當下問不誤收（regression 守門）
# ─────────────────────────────────────────────────────────────────────────────
class SelfReportCaptureTest(unittest.TestCase):
    def test_timed_self_report_is_scheduled(self):
        # 帶時間的自陳約定 → 排程承諾（截圖：3分鐘後說一下你內在感覺、21:47時說說你的狀態）
        for t in ["3分鐘後說一下你內在感覺是怎麼運作的", "21:47時說說你的狀態",
                  "等一下八點說說你的心情", "五分鐘後講一下你此刻的感受"]:
            self.assertTrue(selfstate.is_scheduled_promise_request(t), t)

    def test_controls_not_scheduled(self):
        # 非感覺名詞、無時間、第三方 → 不誤收
        for t in ["3分鐘後說說你的工作", "說一下天氣", "說說你老闆的想法",
                  "5分鐘後說一下你的計畫", "講一下你的看法"]:
            self.assertFalse(selfstate.is_scheduled_promise_request(t), t)

    def test_immediate_phenomenal_not_promise(self):
        # 🛡️ regression 守門：當下問內在結構/狀態＝現象/狀態自陳，**不**被誤收成 feeling promise
        for t, want in [("描述你的內在結構", "self_phenomenal"),
                        ("你內在的結構是什麼", "self_phenomenal"),
                        ("說說你的狀態", "self_state")]:
            self.assertFalse(selfstate.is_feeling_promise_request(t), t)
            self.assertEqual(intent.resolve(t, referent.Referent()).kind, want, t)

    def test_deferred_no_clock_is_feeling_promise(self):
        # 無鐘點但帶純延後詞 → feeling promise（走 feel 觸發自陳）
        for t in ["等一下說說你的狀態", "待會講一下你的心情", "之後聊一下你的感受", "等一下說說你此刻的心情"]:
            self.assertTrue(selfstate.is_feeling_promise_request(t), t)

    def test_deferral_in_separate_clause_not_captured(self):
        # 🛡️ 審查 HIGH 修：延後詞在**另一子句**（講使用者自己的下一步）或**當下標記**在場 → 不算延後自陳承諾
        for t, want in [("說說你的感受，之後再聊別的", "self_state"),
                        ("講講你的狀態吧，等等我要忙了", "self_state"),
                        ("等等，描述你現在的內在", "self_state"),          # 現在＝當下、等等是語助
                        ("先說說你的感覺，等一下我要出門", "self_state")]:  # 先＝當下、等一下講使用者出門
            self.assertFalse(selfstate.is_feeling_promise_request(t), t)
            self.assertEqual(intent.resolve(t, referent.Referent()).kind, want, t)

    def test_flag_off_no_self_report_capture(self):
        # 🚪 逃生閘 SELF_REPORT_PROMISE=0：退回 §0.78 前＝無自陳捕捉。§0.83 分享/說明內在是**另一條**捕捉路（內在/運作），
        # 會捕捉「說一下你內在…運作」——測 §0.78 隔離故一併關 SCHED_SELF_EXPLAIN。
        os.environ["SELF_REPORT_PROMISE"] = "0"
        os.environ["SCHED_SELF_EXPLAIN"] = "0"
        try:
            for t in ["21:47時說說你的狀態", "等一下說說你的狀態", "3分鐘後說一下你內在感覺是怎麼運作的"]:
                self.assertFalse(selfstate.is_scheduled_promise_request(t), t)
                self.assertFalse(selfstate.is_feeling_promise_request(t), t)
        finally:
            os.environ.pop("SELF_REPORT_PROMISE", None)
            os.environ.pop("SCHED_SELF_EXPLAIN", None)

    def test_self_report_captures_into_ledger(self):
        # 端到端：帶時間的自陳約定真的入帳（不再空口）——NOW 21:30、約 21:47
        st = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        st.owner_folder_id = "F"
        now = datetime(2026, 7, 5, 13, 30, tzinfo=timezone.utc)   # 21:30 台北
        c = FakeClient()
        monitor.handle_message(_msg("21:47說說你的狀態", now), _coach(), _BOOM, {"meta": {}}, None, st, c, _cfg(), TZ)
        pend = [p for p in (st.scheduled_promises or []) if not p.get("fulfilled")]
        self.assertEqual(len(pend), 1)
        self.assertEqual(_hhmm(pend[0]["target_ts"]), "21:47")
        self.assertTrue(c.sent)                                    # 有答應（不是靜默）


# ─────────────────────────────────────────────────────────────────────────────
# FIX 2：空帳本狀態問句也走誠實接地（不落回自由 LLM 自打臉）
# ─────────────────────────────────────────────────────────────────────────────
class EmptyGroundStatusTest(unittest.TestCase):
    def _state(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        return s

    def test_timeup_empty_ledger_grounds(self):
        # 帳本空、問「時間到了沒」→ 走 promise_ledger 誠實答「沒記著約好什麼」，不自由心算矛盾
        st = self._state()
        now = datetime(2026, 7, 5, 13, 30, tzinfo=timezone.utc)
        c = FakeClient()
        monitor.handle_message(_msg("時間到了沒", now), _coach(), _Benign(),
                               {"meta": {}}, None, st, c, _cfg(), TZ)
        self.assertIn("沒記著", "".join(c.sent))                   # reply 回音的是帳本 facts＝走了帳本接地

    def test_flag_off_empty_ledger_not_grounded(self):
        # 旗標關：空帳本的 timeup 不搶 → 落回聊天、reply 回音的不是帳本 facts（無「沒記著跟你約好」）＝逐位元同現狀
        from unittest import mock
        st = self._state()
        now = datetime(2026, 7, 5, 13, 30, tzinfo=timezone.utc)
        c = FakeClient()
        with mock.patch("telegram_monitor.coach.build_memory_brief", return_value=""):
            monitor.handle_message(_msg("時間到了沒", now), _coach(), _Benign(),
                                   {"meta": {}}, None, st, c, _cfg(promise_status_empty_ground=False), TZ)
        self.assertNotIn("沒記著跟你約好", "".join(c.sent))

    def test_didcall_still_grounds_when_empty(self):
        # didcall（你有叫我嗎）本就空帳本也接地——不受 empty_ground 旗標影響
        st = self._state()
        now = datetime(2026, 7, 5, 13, 30, tzinfo=timezone.utc)
        c = FakeClient()
        monitor.handle_message(_msg("你有叫我嗎", now), _coach(), _Benign(),
                               {"meta": {}}, None, st, c, _cfg(promise_status_empty_ground=False), TZ)
        self.assertIn("沒記著", "".join(c.sent))


# ─────────────────────────────────────────────────────────────────────────────
# FIX 3：逾期承諾豁免反連發守門（剛推過別則也照發）
# ─────────────────────────────────────────────────────────────────────────────
class OverdueGuardExemptTest(unittest.TestCase):
    NOW = datetime(2026, 7, 5, 13, 40, tzinfo=timezone.utc)

    def _state(self, target_offset):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        s.scheduled_promises = [{"target_ts": self.NOW.timestamp() + target_offset,
                                 "made_ts": self.NOW.timestamp() - 1200, "fulfilled": False,
                                 "behavior": "跟他說說我此刻的狀態", "status": "pending"}]
        return s

    def test_overdue_fires_despite_recent_push(self):
        # 逾期（早 grace 之外）＋剛推過別則（30s 前）→ 仍照發（活躍對話不再餓死逾期承諾）
        st = self._state(-600)                                     # 逾期 10 分（>180s grace）
        st.last_push_ts = self.NOW.timestamp() - 30               # 30s 前剛推過
        c = FakeClient()
        monitor._promise_emit(c, st, _cfg(), _coach(), self.NOW)
        self.assertTrue(c.sent)
        self.assertTrue(st.scheduled_promises[0]["fulfilled"])

    def test_just_due_defers_on_recent_push(self):
        # 剛到點（grace 內）＋剛推過 → 這拍延一拍避開連發（不逾期不搶拍）
        st = self._state(-5)                                       # 剛過點 5s（<grace）
        st.last_push_ts = self.NOW.timestamp() - 30
        c = FakeClient()
        monitor._promise_emit(c, st, _cfg(), _coach(), self.NOW)
        self.assertEqual(c.sent, [])
        self.assertFalse(st.scheduled_promises[0]["fulfilled"])

    def test_flag_off_recent_push_starves(self):
        # 旗標關＝退回舊整段 return：剛推過就啥都不發（逐位元同現狀）
        st = self._state(-600)
        st.last_push_ts = self.NOW.timestamp() - 30
        c = FakeClient()
        monitor._promise_emit(c, st, _cfg(promise_overdue_guard_exempt=False), _coach(), self.NOW)
        self.assertEqual(c.sent, [])


# ─────────────────────────────────────────────────────────────────────────────
# FIX 4 / FIX 9：帳本「還欠著會補」vs「沒做到」用引擎同一 TTL；只有最早那筆說「馬上補」
# ─────────────────────────────────────────────────────────────────────────────
class LedgerGraceTest(unittest.TestCase):
    NOW = datetime(2026, 7, 5, 13, 40, tzinfo=timezone.utc)       # 21:40 台北

    def _state(self, promises):
        s = SimpleNamespace(scheduled_promises=promises)
        return s

    def test_overdue_within_ttl_is_owing_not_missed(self):
        now_ts = self.NOW.timestamp()
        st = self._state([{"target_ts": now_ts - 300, "made_ts": now_ts - 600,
                           "fulfilled": False, "behavior": "向他道歉", "status": "pending"}])
        facts = selfstate.promise_ledger_facts(st, self.NOW, TZ, ttl=21600)
        self.assertIn("欠著", facts)
        self.assertNotIn("沒做到", facts)
        text = selfstate.promise_ledger_text(st, self.NOW, TZ, ttl=21600)
        self.assertIn("還欠著", text)
        self.assertNotIn("沒做到", text)

    def test_overdue_beyond_ttl_is_missed(self):
        now_ts = self.NOW.timestamp()
        st = self._state([{"target_ts": now_ts - 7 * 3600, "made_ts": now_ts - 8 * 3600,
                           "fulfilled": False, "behavior": "叫他起床", "status": "pending"}])
        facts = selfstate.promise_ledger_facts(st, self.NOW, TZ, ttl=21600)
        self.assertIn("沒做到", facts)
        text = selfstate.promise_ledger_text(st, self.NOW, TZ, ttl=21600)
        self.assertIn("沒做到", text)

    def test_only_earliest_owes_immediately(self):
        # FIX9：多筆逾期未過期——只有最早那筆說「馬上補」，其餘「排在後面依序補」
        now_ts = self.NOW.timestamp()
        st = self._state([
            {"target_ts": now_ts - 300, "made_ts": now_ts - 600, "fulfilled": False,
             "behavior": "向他道歉", "status": "pending"},
            {"target_ts": now_ts - 200, "made_ts": now_ts - 600, "fulfilled": False,
             "behavior": "問候他", "status": "pending"}])
        facts = selfstate.promise_ledger_facts(st, self.NOW, TZ, ttl=21600)
        self.assertEqual(facts.count("馬上補"), 1)
        self.assertIn("排在後面", facts)

    def test_ttl_from_config_routed(self):
        # 帳本 TTL 來自 cfg.promise_sched_ttl_sec（此處給小 TTL＝120s → 過點 5 分就算沒做到）
        now_ts = self.NOW.timestamp()
        st = self._state([{"target_ts": now_ts - 300, "made_ts": now_ts - 600,
                           "fulfilled": False, "behavior": "向他道歉", "status": "pending"}])
        facts = selfstate.promise_ledger_facts(st, self.NOW, TZ, ttl=120)
        self.assertIn("沒做到", facts)

    def test_daily_recur_overdue_beyond_ttl_is_owing_not_missed(self):
        # 🛡️ 審查 LOW 修：每天 recur 的過點筆（引擎會推進明天、不判過期）即使超 ttl 也算「欠著、會補」、不說「沒做到」
        now_ts = self.NOW.timestamp()
        st = self._state([{"target_ts": now_ts - 7 * 3600, "made_ts": now_ts - 8 * 3600,
                           "fulfilled": False, "behavior": "跟他打招呼", "status": "pending", "recur": "daily"}])
        facts = selfstate.promise_ledger_facts(st, self.NOW, TZ, ttl=21600)
        self.assertNotIn("沒做到", facts)
        text = selfstate.promise_ledger_text(st, self.NOW, TZ, ttl=21600)
        self.assertNotIn("沒做到", text)


# ─────────────────────────────────────────────────────────────────────────────
# FIX 5：捕捉去重只認未兌現筆（已兌現的同時刻舊筆不擋新約）
# ─────────────────────────────────────────────────────────────────────────────
class CaptureDedupTest(unittest.TestCase):
    def test_fulfilled_same_time_does_not_block_rerequest(self):
        st = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        st.owner_folder_id = "F"
        now = datetime(2026, 7, 5, 13, 30, tzinfo=timezone.utc)   # 21:30
        target = datetime(2026, 7, 5, 13, 47, tzinfo=timezone.utc).timestamp()   # 21:47
        # 稍早那筆同時刻、**已兌現**（模擬已發過）
        st.scheduled_promises = [{"target_ts": target, "made_ts": now.timestamp() - 3600,
                                  "fulfilled": True, "status": "fulfilled", "behavior": "跟他打招呼"}]
        c = FakeClient()
        monitor.handle_message(_msg("21:47跟我打招呼", now), _coach(), _BOOM, {"meta": {}}, None, st, c, _cfg(), TZ)
        pend = [p for p in (st.scheduled_promises or []) if not p.get("fulfilled")]
        self.assertEqual(len(pend), 1)                             # 新約成立、沒被已兌現舊筆吞掉
        self.assertEqual(_hhmm(pend[0]["target_ts"]), "21:47")

    def test_unfulfilled_same_time_same_behavior_dedups(self):
        st = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        st.owner_folder_id = "F"
        now = datetime(2026, 7, 5, 13, 30, tzinfo=timezone.utc)
        target = datetime(2026, 7, 5, 13, 47, tzinfo=timezone.utc).timestamp()
        st.scheduled_promises = [{"target_ts": target, "made_ts": now.timestamp() - 60,
                                  "fulfilled": False, "status": "pending", "behavior": "跟他打招呼"}]
        c = FakeClient()
        monitor.handle_message(_msg("21:47跟我打招呼", now), _coach(), _BOOM, {"meta": {}}, None, st, c, _cfg(), TZ)
        pend = [p for p in (st.scheduled_promises or []) if not p.get("fulfilled")]
        self.assertEqual(len(pend), 1)                             # 未兌現同時刻同行為＝不重疊


# ─────────────────────────────────────────────────────────────────────────────
# FIX 6：截尾 pending 依最快到點留（不被遠期筆擠掉）
# ─────────────────────────────────────────────────────────────────────────────
class TrimSoonestTest(unittest.TestCase):
    def test_soonest_pending_kept_over_far_future(self):
        base = 1_800_000_000
        proms = [{"target_ts": base + (i + 1) * 3600, "fulfilled": False, "behavior": f"far{i}"} for i in range(10)]
        proms.append({"target_ts": base + 30, "fulfilled": False, "behavior": "SOON"})   # 最快到點、最後 append
        kept = _trim_sched_promises(proms, cap=8)
        self.assertEqual(len(kept), 8)
        self.assertTrue(any(p["behavior"] == "SOON" for p in kept))

    def test_overdue_pending_never_dropped(self):
        # 逾期（target 最小）一定留（不因後續遠期筆多而被剪掉＝之後才有機會補發）
        base = 1_800_000_000
        proms = [{"target_ts": base + (i + 1) * 3600, "fulfilled": False, "behavior": f"far{i}"} for i in range(10)]
        proms.insert(0, {"target_ts": base - 9999, "fulfilled": False, "behavior": "OVERDUE"})
        kept = _trim_sched_promises(proms, cap=8)
        self.assertTrue(any(p["behavior"] == "OVERDUE" for p in kept))

    def test_flag_off_bytewise(self):
        os.environ["SCHED_RECUR_DAILY"] = "0"
        try:
            proms = [{"target_ts": i, "fulfilled": False} for i in range(12)]
            self.assertEqual(_trim_sched_promises(proms, cap=8), proms[-8:])
        finally:
            os.environ.pop("SCHED_RECUR_DAILY", None)

    def test_protect_keeps_fresh_far_future_promise(self):
        # 🛡️ 審查 MED 修：剛立的遠期新約（protect）——即使 cap 個更近的既有 pending，也保留（否則答應了卻不在帳本＝說到做不到）
        base = 1_800_000_000
        proms = [{"target_ts": base + (i + 1) * 3600, "fulfilled": False, "behavior": f"near{i}"} for i in range(8)]
        fresh = {"target_ts": base + 999999, "fulfilled": False, "behavior": "FRESH_FAR"}   # 最遠、最後 append
        proms.append(fresh)
        kept = _trim_sched_promises(proms, cap=8, protect=[fresh])
        self.assertEqual(len(kept), 8)
        self.assertTrue(any(p["behavior"] == "FRESH_FAR" for p in kept))   # 新約留住
        self.assertFalse(any(p["behavior"] == "near7" for p in kept))      # 被剪的是最遠的既有筆

    def test_protect_end_to_end_ack_matches_ledger(self):
        # 端到端：帳本已滿 8 筆更近 pending，使用者立新遠約 → 答應後那筆**真的在帳本**（不 ack 空頭）
        st = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        st.owner_folder_id = "F"
        now = datetime(2026, 7, 5, 13, 0, tzinfo=timezone.utc)   # 21:00
        base = now.timestamp()
        st.scheduled_promises = [{"target_ts": base + (i + 1) * 60, "made_ts": base - 60,
                                  "fulfilled": False, "status": "pending", "behavior": f"near{i}"} for i in range(8)]
        c = FakeClient()
        monitor.handle_message(_msg("23:30說說你的狀態", now), _coach(), _BOOM, {"meta": {}}, None, st, c, _cfg(), TZ)
        self.assertTrue(c.sent)                                  # 有答應
        hhmms = [_hhmm(p["target_ts"]) for p in st.scheduled_promises if not p.get("fulfilled")]
        self.assertIn("23:30", hhmms)                            # 答應的那筆真在帳本（沒被截尾剪掉）


# ─────────────────────────────────────────────────────────────────────────────
# FIX 7：守約句幻覺時刻 → 落回確定性模板
# ─────────────────────────────────────────────────────────────────────────────
class KeepTimeConsistentTest(unittest.TestCase):
    NOW = datetime(2026, 7, 5, 13, 47, tzinfo=timezone.utc)       # 21:47

    def test_allowed_times_pass(self):
        nl = self.NOW.astimezone(TZ)
        self.assertTrue(monitor._keep_time_consistent("現在21:47、約的是21:42、我來了", "21:42", nl))

    def test_hallucinated_time_rejected(self):
        nl = self.NOW.astimezone(TZ)
        self.assertFalse(monitor._keep_time_consistent("現在是21:40、已過兩分鐘", "21:42", nl))

    def test_no_time_passes(self):
        self.assertTrue(monitor._keep_time_consistent("嗨我來了，跟你說說我的狀態", "21:42", self.NOW))

    def test_zero_pad_normalized(self):
        self.assertTrue(monitor._keep_time_consistent("我約的是9:05", "09:05", None))

    def test_emit_falls_back_to_template_on_hallucination(self):
        # 端到端：LLM 吐幻覺時刻的守約句 → 落回模板（仍準時兌現、帶正確行為）
        st = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        st.owner_folder_id = "F"
        st.scheduled_promises = [{"target_ts": self.NOW.timestamp() - 5, "made_ts": self.NOW.timestamp() - 300,
                                  "fulfilled": False, "behavior": "向他道歉", "status": "pending"}]
        bad_coach = _coach(voice_promise_keep=lambda *a, **k: "現在是03:03，我還沒到呢")   # 幻覺時刻
        c = FakeClient()
        monitor._promise_emit(c, st, _cfg(), bad_coach, self.NOW)
        self.assertTrue(c.sent)
        self.assertNotIn("03:03", "".join(c.sent))                # 幻覺句被丟、走模板
        self.assertTrue(st.scheduled_promises[0]["fulfilled"])


# ─────────────────────────────────────────────────────────────────────────────
# 旗標預設開
# ─────────────────────────────────────────────────────────────────────────────
class ConfigDefaultTest(unittest.TestCase):
    def test_new_flags_default_on(self):
        for env in ("PROMISE_OVERDUE_GUARD_EXEMPT", "PROMISE_STATUS_EMPTY_GROUND"):
            os.environ.pop(env, None)
        cfg = config.Config.load()
        self.assertTrue(cfg.promise_overdue_guard_exempt)
        self.assertTrue(cfg.promise_status_empty_ground)


if __name__ == "__main__":
    unittest.main()
