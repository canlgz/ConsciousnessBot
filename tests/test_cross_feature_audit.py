"""🔍 §1.57 跨功能通盤審計（§1.46–§1.56 批次；使用者原話「要通盤檢查，不要壞一次改一處」）。

比照 2026-07-12 §1.10–§1.30 衝突審計（七叢集 76 組合、零真衝突）的精神：這批 11 個機制疊在同兩條管線
（_say 送出鏈、_BurstInterrupt 插話鏈）與同一組 tier-1 偵測器上，逐一交叉驗出三個**可重現**時序缺陷＋
把「多表重疊句的優先序」固化成常設矩陣測試。三個缺陷（管線審計實錘、逐行對碼確認）：

A. §1.50 wrap 濃縮不濾 §1.49 的 _skip——被判重複的殘句以濃縮形式又講一遍（monitor 舊 1270 行
   `"".join(bubbles[i:])`，違反自家註解「濃縮重複內容沒有意義」）；
B. §1.49「當前串是 dup → 送橋＋continue」分支排在 rewrite 之前——陳述插話該走 §1.50 wrap 的輪次，
   只因當前殘句剛好重複就被放回 bridge+resume（正是 §1.50 要壓掉的「橋＋殘尾」pattern）；
C. §1.54 ack 去重排在 _merge_bubbles 封頂**之後**——「我明白。」（≤6 字＝_is_fragment 碎句）被 merge
   併進內容句，canon 落不進家族＝去重失效、重複致意倖存。

修法（同旗標各自把關、旗標關＝逐位元同現狀）：A. wrap remaining 以 j∉_skip 過濾＋decide_resume 的
remaining 以去重後計；B. dup-current 不再抄近路——先過 rewrite 判定（wrap 用過濾後殘句），resume 時
橋後跳過 dup 串；C. 去重挪到封頂之前（旗標開＝先切不封頂→去重→再封頂；stash 缺席＝原單呼叫）。
全 stub、零網路。
"""

import unittest
from types import SimpleNamespace
from unittest import mock

from telegram_monitor import monitor

NOW_TS = 1_700_000_000


class _SayBase(unittest.TestCase):
    def setUp(self):
        monitor._TURN["bubbles"] = None
        monitor._TURN.pop("ack_dedup_recent", None)

    def tearDown(self):
        monitor._TURN.pop("ack_dedup_recent", None)

    def _client(self, cfg, polls, coach=None, history=None):
        sent, handled = [], []

        class C:
            dry_run = False

            def __init__(s):
                s.sent = sent

            def send(s, t):
                sent.append(t)
                return True

            def send_typing(s):
                pass

            def get_updates(s, offset=0, timeout=0):
                return polls.pop(0) if polls else []
        c = C()
        st = SimpleNamespace(tg_update_offset=0, convo_history=list(history or []))
        c._interrupt = monitor._BurstInterrupt(c, st, cfg, floor=0,
                                               handle_fn=lambda u: handled.append(u), coach=coach)
        c._handled = handled
        return c

    @staticmethod
    def _u(uid, text):
        return {"update_id": uid, "message": {"chat": {"id": 1}, "text": text, "date": NOW_TS}}

    COACH = SimpleNamespace(enabled=True,
                            voice_wrap_condense=lambda rt, h, natural=False: f"〔濃縮：{rt}〕")

    def _cfg(self, rewrite=True, wrap=True, trim=True):
        return SimpleNamespace(interrupt_rewrite_enabled=rewrite, interrupt_max_depth=2,
                               interrupt_statement_enabled=True, interrupt_continuation_defer=True,
                               interrupt_wrap_condense_enabled=True,
                               interrupt_statement_wrap_enabled=wrap,
                               interrupt_tail_trim_enabled=trim,
                               telegram_chat_id="", hostile_converge_enabled=True)


# ── A：wrap 濃縮不得包含已判重複的殘句 ─────────────────────────────────────
class WrapSkipFilterTest(_SayBase):
    HIST = [{"role": "model", "text": "我會一直記得今天。", "ts": NOW_TS}]

    def test_condensed_remaining_excludes_dup(self):
        # 陳述插話於 i=1；殘句 C 與近史重複 → §1.50 wrap 的濃縮素材必須只剩 B、不得把 C 又講回來
        c = self._client(self._cfg(), [[self._u(99, "我今天去了趟醫院")]],
                         coach=self.COACH, history=self.HIST)
        with mock.patch.object(monitor, "_sleep"):
            monitor._say(c, "我懂你的意思。這件事對你很重要。我會一直記得今天。")
        condensed = [b for b in c.sent if b.startswith("〔濃縮：")]
        self.assertEqual(len(condensed), 1)
        self.assertIn("這件事對你很重要。", condensed[0])
        self.assertNotIn("我會一直記得今天。", condensed[0])   # 已判重複＝不得濃縮回來
        self.assertNotIn("我會一直記得今天。", c.sent)

    def test_flag_off_condenses_all_bitwise(self):
        # 【消融】TAIL_TRIM 關＝_skip 恆空＝濃縮素材含全部殘句（HEAD 位元）
        c = self._client(self._cfg(trim=False), [[self._u(99, "我今天去了趟醫院")]],
                         coach=self.COACH, history=self.HIST)
        with mock.patch.object(monitor, "_sleep"):
            monitor._say(c, "我懂你的意思。這件事對你很重要。我會一直記得今天。")
        condensed = [b for b in c.sent if b.startswith("〔濃縮：")]
        self.assertEqual(len(condensed), 1)
        self.assertIn("我會一直記得今天。", condensed[0])


# ── B：dup-current 不得繞過 §1.50 statement wrap ───────────────────────────
class DupCurrentNoBypassTest(_SayBase):
    HIST = [{"role": "model", "text": "這件事對你很重要。", "ts": NOW_TS}]

    def test_statement_wrap_still_wins_when_current_is_dup(self):
        # 陳述插話於 i=1、當前殘句 B 是 dup、後面還有新內容 C → 該 wrap（濃縮 C）而不是 bridge+原樣 resume
        c = self._client(self._cfg(), [[self._u(99, "我今天去了趟醫院")]],
                         coach=self.COACH, history=self.HIST)
        with mock.patch.object(monitor, "_sleep"):
            monitor._say(c, "我懂你的意思。這件事對你很重要。之後我想再多問你一點。")
        self.assertFalse(any(b in c.sent for b in monitor._RESUME_BRIDGES))   # 不再橋接
        self.assertNotIn("這件事對你很重要。", c.sent)                        # dup 不重播
        self.assertNotIn("之後我想再多問你一點。", c.sent)                    # 也不原樣照播
        condensed = [b for b in c.sent if b.startswith("〔濃縮：")]
        self.assertEqual(len(condensed), 1)
        self.assertIn("之後我想再多問你一點。", condensed[0])

    def test_rewrite_off_dup_current_bridges_and_skips(self):
        # rewrite 關（無 wrap 路徑）＝§1.49 原行為：橋照送、dup 跳過、新內容照講
        c = self._client(self._cfg(rewrite=False), [[self._u(99, "我今天去了趟醫院")]],
                         coach=self.COACH, history=self.HIST)
        with mock.patch.object(monitor, "_sleep"):
            monitor._say(c, "我懂你的意思。這件事對你很重要。之後我想再多問你一點。")
        self.assertTrue(any(b in c.sent for b in monitor._RESUME_BRIDGES))
        self.assertNotIn("這件事對你很重要。", c.sent)
        self.assertIn("之後我想再多問你一點。", c.sent)


# ── C：ack 去重必須在 merge 封頂之前 ───────────────────────────────────────
class AckDedupBeforeMergeTest(_SayBase):
    def test_short_ack_deduped_even_under_bubble_cap(self):
        # 近史剛「嗯，我明白了。」；本則首句「我明白。」（≤6 字碎句）＋兩句內容、泡泡封頂 2 →
        # 舊序（merge→dedup）會把「我明白。」併進內容句＝去重失效；新序（dedup→merge）必須刪乾淨。
        monitor._TURN["bubbles"] = 2
        monitor._TURN["ack_dedup_recent"] = ["嗯，我明白了。"]
        sent = []

        class C:
            dry_run = False

            def send(s, t):
                sent.append(t)
                return True
        with mock.patch.object(monitor, "_sleep"):
            monitor._say(C(), "我明白。\n\n你說的其實我懂我也想多講一點。\n\n但我還是想解釋清楚一點才行。")
        joined = "\n".join(sent)
        self.assertNotIn("我明白。", joined)                  # 重複致意不得以任何形式倖存（含被併進內容句）
        self.assertIn("你說的其實我懂", joined)
        self.assertLessEqual(len(sent), 2)                    # 封頂仍有效（去重後再 merge）

    def test_stash_absent_single_path_bitwise(self):
        # stash 缺席（旗標關）＝原單呼叫路徑：merge 照舊、致意句照舊保留
        monitor._TURN["bubbles"] = 2
        sent = []

        class C:
            dry_run = False

            def send(s, t):
                sent.append(t)
                return True
        with mock.patch.object(monitor, "_sleep"):
            monitor._say(C(), "我明白。\n\n你說的其實我懂我也想多講一點。\n\n但我還是想解釋清楚一點才行。")
        self.assertIn("我明白。", "\n".join(sent))


# ═══════════════ 偵測器×路由衝突矩陣（96 句實測審計的固化） ═══════════════
import os
import re
import tempfile
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from telegram_monitor import circumplex, dialogue_intent, reaction, selfstate
from telegram_monitor.state import State

TZ = ZoneInfo("Asia/Taipei")
AUDIT_NOW = datetime(2026, 7, 19, 6, 0, 0, tzinfo=timezone.utc)


class TableIntersectionTest(unittest.TestCase):
    """常設防線：詞表交集必須**逐字受控**——未來往任何表加詞，若產生未經審計的重疊，這裡當場炸。"""

    def test_hostile_vs_ack_tables(self):
        # 已知雙面詞恰為 是喔/呵呵（poll 端 is_hostile 守門先擋＝敵意收口優先，§1.48 消融定案）
        self.assertEqual(set(reaction._HOSTILE_SHORT) & set(selfstate._ACK_WORDS), {"呵呵", "是喔"})
        self.assertEqual(set(reaction._HOSTILE_SHORT) & set(selfstate._INTERJECT_EXTRA), set())

    def test_mood_prod_disjoint_from_ack_and_interject(self):
        self.assertEqual(circumplex._MOOD_PROD_SET & set(selfstate._ACK_WORDS), set())
        self.assertEqual(circumplex._MOOD_PROD_SET & set(selfstate._INTERJECT_EXTRA), set())

    def test_doubt_vs_ack_overlap_pinned(self):
        # 「真的假的」既是附和也是質疑＝互補訊號（一個走純對話路由、一個抑制教學提議），非矛盾——釘住現狀
        self.assertEqual({c for c in dialogue_intent._DOUBT_CUES if c in selfstate._ACK_WORDS}, {"真的假的"})


class DetectorPinTest(unittest.TestCase):
    def test_shima_is_doubt_not_backchannel(self):
        # 「是嗎」：_ACK_NOT 含「嗎」＝backchannel 必不中；只作 §1.52 質疑守門（不搶路由）
        self.assertFalse(selfstate.is_backchannel("是嗎"))
        self.assertTrue(dialogue_intent.is_doubt_text("是嗎"))

    def test_hostile_default_branch_flip_battery(self):
        # §1.46 翻轉抽測：at-bot regex／全句短句錨命中且落預設分支者，全數翻成質疑方向
        for t in ("隨便你", "算了", "哼", "少來", "最好是", "你不要敷衍我"):
            self.assertEqual(reaction.hostile_affect_delta_for(t), (-0.18, 0.18), t)

    def test_timeup_moodprod_overlap_pinned(self):
        # 矩陣唯一真衝突的原料：三句同時 timeup＋moodprod（路由讓路見 MoodYieldTest）；
        # 另三句 timeup 漏收＝moodprod 獨得（§1.47 原本就活著的那半）
        for t in ("到了嗎", "到了沒", "還沒到嗎"):
            self.assertEqual(selfstate.promise_status_kind(t), "timeup", t)
            self.assertTrue(circumplex.is_mood_data_prod(t), t)
        for t in ("還沒嗎", "到了吧", "一樣還沒到嗎"):
            self.assertEqual(selfstate.promise_status_kind(t), "", t)
            self.assertTrue(circumplex.is_mood_data_prod(t), t)


class MoodYieldTest(unittest.TestCase):
    """§1.57 讓路修正：座標情境窗內、空帳本的「到了嗎」→ 交座標數據；有活承諾→照舊對帳。"""

    class Cl:
        def __init__(self): self.sent, self.dry_run = [], False
        def send(self, t): self.sent.append(t); return True

    def _cfg(self, deliver=True):
        return SimpleNamespace(dry_run=False, telegram_chat_id="", scheduled_promise_enabled=True,
                               promise_emit_enabled=True, promise_sched_ttl_sec=21600, timezone="Asia/Taipei",
                               notify_cooldown_min=30, promise_reply_bridge_enabled=False, promise_ledger_enabled=True,
                               sched_leave_autoarm_enabled=True, deferred_promise_enabled=False,
                               promise_llm_rescue_enabled=False, sticker_llm_rescue_enabled=False,
                               promise_keep_claim_guard_enabled=True, promise_said_ground_enabled=True,
                               bot_self_promise_enabled=False, promise_preempt_enabled=False,
                               sticker_sent_memory_enabled=True, send_stickers=True,
                               sticker_fakesend_guard_enabled=False, recall_ground_guard_enabled=False,
                               selfshare_reason_ground_enabled=False, wake_projection_guard_enabled=False,
                               user_habit_ground_enabled=False, self_feel_condense_enabled=False,
                               promise_deliver_content_enabled=False, mood_coord_report_enabled=True,
                               mood_coord_deliver_enabled=deliver)

    def _coach(self):
        seen = {"ask": None, "reply": None}
        c = SimpleNamespace(enabled=True, api_key="k", model="m",
                            meter=SimpleNamespace(record=lambda *a, **k: None), seen=seen)

        def ask(*a, **k):
            seen["ask"] = k.get("extra_system")
            return ("chat", None, "嗯。")

        def reply(*a, **k):
            seen["reply"] = k.get("extra_system")
            return "嗯。"

        c.ask = ask
        c.reply = reply
        c.voice_schedule_ack = lambda q, w, h, sticker_hint="": "好。"
        c.voice_promise_ack = lambda q, h: "好。"
        c.judge_timed_request = lambda t: None
        c.judge_sticker_request = lambda t: None
        c.judge_self_promise = lambda t: None
        c.judge_promise_preempt = lambda *a, **k: False
        c.voice_promise_keep = lambda *a, **k: None
        c.voice_greeting = lambda *a, **k: "早安！"
        return c

    SNAP = SimpleNamespace(summary={"total": 1, "last24h": 0, "last7d": 0},
                           funnel={"candidate": 0, "context": 0, "journey": 0, "watch": 0},
                           heartbeat={"status": "ok"}, filed_records=[])
    BOOM = SimpleNamespace(load_embedding_records=lambda *_: [])
    _VA_RE = re.compile(r"V [+-]\d\.\d{2}、A [+-]\d\.\d{2}")

    def _state(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        s.entropy = SimpleNamespace(mood=-0.2, arousal=0.1, hunger=0.5, self_stims_this_idle=0,
                                    reach_outs_this_idle=0, coping_reach_outs_this_idle=0)
        return s

    def _turn(self, s, text, cfg, when):
        co = self._coach()
        monitor.handle_message({"message": {"chat": {"id": 1}, "text": text, "date": when.timestamp()}},
                               co, self.BOOM, {"meta": {}, "records": []}, self.SNAP,
                               s, self.Cl(), cfg, TZ)
        return co

    def test_empty_ledger_in_mood_window_yields_to_coords(self):
        s, cfg = self._state(), self._cfg()
        self._turn(s, "你現在的情緒座標到哪了？", cfg, AUDIT_NOW)          # 開座標情境窗
        co = self._turn(s, "到了嗎", cfg, AUDIT_NOW.replace(minute=1))
        got = (co.seen["ask"] or "") + (co.seen["reply"] or "")
        self.assertRegex(got, self._VA_RE)                                # 交程式讀的真數字、不再「沒記著約好什麼」

    def test_live_promise_still_wins(self):
        # 有活著的時間承諾＝照舊對帳（時間之約優先於座標窗）：不落聊天 lane＝不注入座標
        s, cfg = self._state(), self._cfg()
        self._turn(s, "你現在的情緒座標到哪了？", cfg, AUDIT_NOW)
        s.scheduled_promises = [{"target_ts": AUDIT_NOW.timestamp() + 600, "made_ts": AUDIT_NOW.timestamp(),
                                 "made_text": "10分鐘後叫我", "behavior": "叫他", "fulfilled": False,
                                 "status": "pending"}]
        co = self._turn(s, "到了嗎", cfg, AUDIT_NOW.replace(minute=1))
        got = (co.seen["ask"] or "") + (co.seen["reply"] or "")
        self.assertNotRegex(got, self._VA_RE)

    def test_flag_off_ledger_grabs_bitwise(self):
        # 【消融】deliver 關＝prod 不算命中＝timeup 照舊空帳接地（同現狀）
        s, cfg = self._state(), self._cfg(deliver=False)
        self._turn(s, "你現在的情緒座標到哪了？", cfg, AUDIT_NOW)
        co = self._turn(s, "到了嗎", cfg, AUDIT_NOW.replace(minute=1))
        got = (co.seen["ask"] or "") + (co.seen["reply"] or "")
        self.assertNotRegex(got, self._VA_RE)


if __name__ == "__main__":
    unittest.main()
