"""🧭 §1.47 座標數據要真交付（MOOD_COORD_DELIVER）：省稱/催促接得住＋兌現帶真數＋無接地座標數字守門。

截圖根因（12:03–12:49，§1.45 上線後仍現形＝三個偵測器各漏一角）：
① 12:48「座標的數值變化呢？怎麼沒講」→ is_mood_data_question=False（只認字面「情緒座標」連寫或「內在＋數據/
  數值/讀數」；這句有「座標」沒「情緒」、有「數值」沒「內在」）→ §1.45 資料 lane 沒開、LLM 手上沒有程式讀的
  (V,A) → 支吾「啊，抱歉，我剛剛...」「怎麼回事。」；
② 12:49「說啊」→ 情境內短催促單句無指涉、天生偵測不到（§1.15 貼圖省略句同型問題）→ 又是無接地輪；
③ 12:49 兜出「我再回頭看了一下…數值大概是在 -0.7 左右」＝LLM 編的（單軸、「大概…左右」、「回頭看」也是演的
  ——coord_facts 給的是成對精確值），違反 §1.20 鐵律；
④ 12:33 兌現「說清楚（心裡）變化的細節」只有質性沒數字＝§1.25 _MOOD_PROMISE_RE 四詞（情緒座標/心情座標/
  情緒的變化/心情的變化）連 LLM 命名的「心裡變化」都不認 → 快照沒存、兌現不帶數。

§1.47 四件（一旗 MOOD_COORD_DELIVER；config 預設 True／monitor 端 getattr 預設 False＝逐位元同現狀）：
A. 寬偵測 circumplex.is_mood_data_question_wide——「座標」＋數值/變化 cue 不必「情緒」前綴；
B. 情境催促承接 circumplex.is_mood_data_prod＋state.mood_data_ctx_ts（跨重生）——命中數據題/兌現帶數後開窗，
   窗內仍須相鄰 wire 可驗證為座標對話，「說啊/怎麼沒講/數字呢」才視同數據題，避免轉題後被舊游標拉回；
C. §1.25 寬承諾偵測 _MOOD_PROMISE_WIDE_RE——內在詞±變化/變化的細節/裸「座標」也存快照、兌現帶真差分；
D. _say 座標契約守門——有接地也必須驗收：同輪凍結唯一 current，依 snapshot/trajectory/repair 重畫合法時序；
   沒接地卻冒出座標小數時仍換成程式真數字。引用歸屬不攔。全 stub、零網路。
"""

import os
import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from telegram_monitor import circumplex, monitor
from telegram_monitor.state import State

TZ = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 7, 17, 4, 3, 0, tzinfo=timezone.utc)   # 台北 12:03（截圖時刻）


def _ent(v=-0.3, a=-0.1):
    return SimpleNamespace(mood=v, arousal=a, hunger=0.5, self_stims_this_idle=0,
                           reach_outs_this_idle=0, coping_reach_outs_this_idle=0)


# ── 單元：A 寬偵測（純函式）────────────────────────────────────────────────
class WideDetectorTest(unittest.TestCase):
    def test_screenshot_sentences_now_hit(self):
        for t in ("座標的數值變化呢？怎麼沒講", "座標的數值變化呢", "座標到哪了",
                  "座標現在是多少", "你的座標數字是幾"):
            self.assertTrue(circumplex.is_mood_data_question_wide(t), t)

    def test_base_positives_still_hit(self):
        for t in ("你現在的情緒座標到哪了？", "你有辦法全是內在的數據嗎"):
            self.assertTrue(circumplex.is_mood_data_question_wide(t), t)

    def test_base_miss_pinned(self):
        # 釘住 §1.45 原判的漏（守旗標關對照的根：base 不動、寬判只在 §1.47 旗標下由呼叫端啟用）
        self.assertFalse(circumplex.is_mood_data_question("座標的數值變化呢？怎麼沒講"))

    def test_negatives(self):
        # 無「座標」錨＝不寬收（數據統計/花費/時刻/心情閒聊照舊不中）
        for t in ("現在幾點", "這個月花多少錢", "幫我列一下數據統計", "我今天心情不好",
                  "變化好大", "早安", "", None):
            self.assertFalse(circumplex.is_mood_data_question_wide(t), repr(t))


# ── 單元：B 情境催促（純函式；指涉靠呼叫端情境窗）──────────────────────────
class ProdDetectorTest(unittest.TestCase):
    def test_prods_hit(self):
        for t in ("說啊", "說呀", "快說", "講啊", "說吧", "然後呢", "怎麼沒講",
                  "怎麼不說", "數字呢", "數值呢", "座標呢", "說啊！", "快說啦",
                  "還沒嗎", "還沒到嗎", "一樣還沒到嗎", "到了嗎", "到了沒"):   # §1.47 補遺：條件式約定的進度催問
            self.assertTrue(circumplex.is_mood_data_prod(t), t)

    def test_non_prods_false(self):
        for t in ("好啊", "嗯", "早安", "你太爛了", "說個故事來聽", "我不想說",
                  "說說你今天做了什麼", "", None):
            self.assertFalse(circumplex.is_mood_data_prod(t), repr(t))


# ── 單元：C §1.25 寬承諾偵測 ────────────────────────────────────────────────
class WideMoodPromiseTest(unittest.TestCase):
    P1 = {"behavior": "說清楚變化的細節", "made_text": "你再想想，30分鐘之後，再說清楚變化的細節吧"}
    P2 = {"behavior": "跟他說說心裡變化的細節", "made_text": ""}

    def _cfg(self, on=True):
        return SimpleNamespace(mood_coord_deliver_enabled=on)

    def test_screenshot_promises_hit_wide(self):
        for p in (self.P1, self.P2):
            self.assertTrue(monitor._mood_promise_hit(self._cfg(), p), p)
            self.assertFalse(monitor._is_mood_promise(p), p)          # 釘住 §1.25 原判的漏

    def test_base_promise_still_hits_even_flag_off(self):
        p = {"behavior": "跟你說我情緒座標的變化", "made_text": ""}
        self.assertTrue(monitor._mood_promise_hit(self._cfg(on=False), p))   # 原判不受旗標影響

    def test_flag_off_wide_does_not_hit(self):
        self.assertFalse(monitor._mood_promise_hit(self._cfg(on=False), self.P1))

    def test_non_mood_promises_false(self):
        for p in ({"behavior": "叫他起床", "made_text": "7點叫我起床"},
                  {"behavior": "跟他打招呼", "made_text": ""}):
            self.assertFalse(monitor._mood_promise_hit(self._cfg(), p), p)


# ── 整合 harness（同 test_mood_coord_report）───────────────────────────────
class _Base(unittest.TestCase):
    class Cl:
        def __init__(self): self.sent, self.dry_run = [], False
        def send(self, t): self.sent.append(t); return True

    def _cfg(self, deliver=True, coord=True, rescue=False, mood_ground=False):
        base = dict(dry_run=False, telegram_chat_id="", scheduled_promise_enabled=True,
                    promise_emit_enabled=True, promise_sched_ttl_sec=21600, timezone="Asia/Taipei",
                    notify_cooldown_min=30, promise_reply_bridge_enabled=False, promise_ledger_enabled=True,
                    sched_leave_autoarm_enabled=True, deferred_promise_enabled=True,
                    promise_llm_rescue_enabled=rescue, sticker_llm_rescue_enabled=False,
                    promise_keep_claim_guard_enabled=True, promise_said_ground_enabled=True,
                    bot_self_promise_enabled=False, promise_preempt_enabled=False,
                    sticker_sent_memory_enabled=True, send_stickers=True,
                    sticker_fakesend_guard_enabled=False, recall_ground_guard_enabled=False,
                    selfshare_reason_ground_enabled=False, wake_projection_guard_enabled=False,
                    user_habit_ground_enabled=False, self_feel_condense_enabled=False,
                    promise_deliver_content_enabled=False, mood_coord_report_enabled=coord,
                    promise_mood_ground_enabled=mood_ground)
        if deliver is not None:                            # None＝欄位缺席（既有測試假 cfg 的樣子）
            base["mood_coord_deliver_enabled"] = deliver
        return SimpleNamespace(**base)

    def _coach(self, voice="嗯。", timed=None):
        seen = {"ask": None, "evidence": None, "reply": None}
        c = SimpleNamespace(enabled=True, api_key="k", model="m",
                            meter=SimpleNamespace(record=lambda *a, **k: None), seen=seen)

        def ask(*a, **k):
            seen["ask"] = k.get("extra_system")
            seen["evidence"] = k.get("evidence_tools")
            return ("chat", None, voice)

        def reply(*a, **k):
            seen["reply"] = k.get("extra_system")
            return voice

        c.ask = ask
        c.reply = reply
        c.voice_schedule_ack = lambda q, w, h, sticker_hint="": "好。"
        c.voice_promise_ack = lambda q, h: "好。"
        c.judge_timed_request = lambda t: timed
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

    def _state(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        s.entropy = _ent()
        return s

    def _turn(self, s, text, cfg, voice="嗯。", when=NOW, timed=None):
        cl, co = self.Cl(), self._coach(voice, timed=timed)
        monitor.handle_message({"message": {"chat": {"id": 1}, "text": text, "date": when.timestamp()}},
                               co, self.BOOM, {"meta": {}, "records": []}, self.SNAP,
                               s, cl, cfg, TZ)
        return "".join(cl.sent), co.seen

    _VA_RE = re.compile(r"V [+-]\d\.\d{2}、A [+-]\d\.\d{2}")


# ── 整合：A＋B（省稱命中→注入；窗內催促→注入；旗標關＝同 §1.45）──────────────
class DeliverIntegrationTest(_Base):
    def test_wide_question_gets_facts_and_no_evidence(self):
        s = self._state()
        out, seen = self._turn(s, "座標的數值變化呢？怎麼沒講", self._cfg())
        got = (seen["ask"] or "") + (seen["reply"] or "")
        self.assertRegex(got, self._VA_RE)                 # 程式讀的真數字進了 prompt
        if seen["evidence"] is not None:
            self.assertFalse(seen["evidence"])             # 證據工具收回（不再吐 💸）
        self.assertGreater(getattr(s, "mood_data_ctx_ts", 0), 0)   # 情境窗已開

    def test_prod_within_window_gets_facts(self):
        s = self._state()
        self._turn(s, "座標的數值變化呢？怎麼沒講", self._cfg(), when=NOW)
        out, seen = self._turn(s, "說啊", self._cfg(), when=NOW + timedelta(minutes=1))
        got = (seen["ask"] or "") + (seen["reply"] or "")
        self.assertRegex(got, self._VA_RE)                 # 12:49「說啊」不再是無接地輪

    def test_prod_outside_window_no_facts(self):
        s = self._state()
        self._turn(s, "座標的數值變化呢？怎麼沒講", self._cfg(), when=NOW)
        out, seen = self._turn(s, "說啊", self._cfg(), when=NOW + timedelta(minutes=40))
        got = (seen["ask"] or "") + (seen["reply"] or "")
        # 對話防重提示可以引用上一輪真的送達的 pair；但窗外裸催促不得重新注入一份座標 facts。
        self.assertNotIn("【內部狀態快照", got)

    def test_prod_without_context_no_facts(self):
        s = self._state()
        out, seen = self._turn(s, "說啊", self._cfg())
        got = (seen["ask"] or "") + (seen["reply"] or "")
        self.assertNotRegex(got, self._VA_RE)              # 沒有情境、單獨「說啊」＝普通聊天

    def test_flag_off_bitwise_s145(self):
        # 【消融】deliver 關/缺席＝只剩 §1.45 原判：省稱句不注入、游標不寫
        for deliver in (False, None):
            s = self._state()
            out, seen = self._turn(s, "座標的數值變化呢？怎麼沒講", self._cfg(deliver=deliver))
            got = (seen["ask"] or "") + (seen["reply"] or "")
            self.assertNotRegex(got, self._VA_RE, str(deliver))
            self.assertEqual(getattr(s, "mood_data_ctx_ts", 0), 0, str(deliver))

    def test_base_question_still_works(self):
        s = self._state()
        out, seen = self._turn(s, "你現在的情緒座標到哪了？", self._cfg())
        got = (seen["ask"] or "") + (seen["reply"] or "")
        self.assertRegex(got, self._VA_RE)


# ── 整合：C 兌現帶真數（12:03 入帳存快照 → 12:33 兌現帶差分）────────────────
class PromiseGroundIntegrationTest(_Base):
    S1203 = "你再想想，30分鐘之後，再說清楚變化的細節吧"

    def _book(self, deliver=True):
        s = self._state()
        cfg = self._cfg(deliver=deliver, rescue=True, mood_ground=True)
        # 12:03 原句確定性捕捉 miss（已實測 sched=False）→ 走 §1.12 LLM 逃生閘、動作由 LLM 命名（同截圖）
        self._turn(s, self.S1203, cfg, timed=(True, "跟他說說心裡變化的細節"))
        return s, cfg

    def test_booking_stores_snapshot(self):
        s, _ = self._book()
        proms = getattr(s, "scheduled_promises", None) or []
        self.assertTrue(proms)
        self.assertTrue(proms[0].get("mood_baseline"), proms[0])     # §1.47：寬判也存快照
        mb = proms[0]["mood_baseline"]
        self.assertLess(mb["v"], 0.0)                                # 快照＝入帳當下程式讀的座標（這句話本身
        self.assertIn("label", mb)                                   # 已微推過 mood，所以只釘符號＋結構）

    def test_flag_off_no_snapshot_bitwise_s125(self):
        s, _ = self._book(deliver=False)
        proms = getattr(s, "scheduled_promises", None) or []
        self.assertTrue(proms)
        self.assertFalse(proms[0].get("mood_baseline"))              # §1.25 原判照舊 miss（同現狀）

    def test_fulfillment_carries_real_diff(self):
        s, cfg = self._book()
        s.entropy.mood = -0.7                                        # 30 分鐘裡真的沉了（截圖 12:33「沉得更低」）
        cl = self.Cl()
        monitor._promise_emit(cl, s, cfg, self._coach(), NOW + timedelta(minutes=31))
        sent = "\n".join(cl.sent)
        self.assertTrue(sent)
        self.assertIn("座標", sent)                                  # 兌現句帶座標
        self.assertIn("（V ", sent)                                  # 程式算的差分數字（shift_text）
        self.assertGreater(getattr(s, "mood_data_ctx_ts", 0), 0)     # 兌現剛講過座標＝開情境窗（「怎麼沒講」接得住）


# ── 整合：D 無接地座標數字守門 ──────────────────────────────────────────────
class CoordClaimGuardTest(_Base):
    FAKE = "我再回頭看了一下，剛剛沉得更低的那一刻，我的數值大概是在 -0.7 左右。"

    def test_ungrounded_fake_number_replaced(self):
        s = self._state()
        out, _ = self._turn(s, "怎麼回事", self._cfg(), voice=self.FAKE)
        self.assertNotIn("-0.7", out)                                # 編的數字不放行
        self.assertRegex(out, re.compile(r"V [+-]\d\.\d{2}"))        # 換成程式此刻讀的真數字

    def test_grounded_turn_still_validates_numbers(self):
        # grounded 只代表模型看過 facts，不代表它轉述正確；不存在於契約的 -0.7 仍不能冒充座標。
        s = self._state()
        out, _ = self._turn(s, "座標的數值變化呢？怎麼沒講", self._cfg(), voice=self.FAKE)
        self.assertNotIn("-0.7", out)
        self.assertRegex(out, self._VA_RE)

    def test_quote_attribution_untouched(self):
        s = self._state()
        v = "你說我的數值是 -0.7 嗎？我沒有講過這個數。"
        out, _ = self._turn(s, "怎麼回事", self._cfg(), voice=v)
        self.assertIn("-0.7", out)                                   # 引用歸屬不攔

    def test_plain_reply_untouched(self):
        s = self._state()
        out, _ = self._turn(s, "怎麼回事", self._cfg(), voice="嗯，我在。剛剛走神了一下。")
        self.assertEqual(out, "嗯，我在。剛剛走神了一下。")

    def test_flag_off_fake_passes_bitwise(self):
        for deliver in (False, None):
            s = self._state()
            out, _ = self._turn(s, "怎麼回事", self._cfg(deliver=deliver), voice=self.FAKE)
            self.assertIn("-0.7", out, str(deliver))                 # 【消融】同現狀：編的數字照送


# ── 持久化＋同步 ───────────────────────────────────────────────────────────
class PersistTest(unittest.TestCase):
    def test_ctx_ts_survives_save_load(self):
        path = os.path.join(tempfile.mkdtemp(), "s.json")
        s = State(path)
        s.mood_data_ctx_ts = 1234.5
        s.save()
        self.assertEqual(State.load(path).mood_data_ctx_ts, 1234.5)


class ConfigTest(unittest.TestCase):
    def test_config_synced(self):
        src = open("telegram_monitor/config.py", encoding="utf-8").read()
        self.assertIn("MOOD_COORD_DELIVER", src)
        self.assertIn("mood_coord_deliver_enabled", src)
        env = open(".env.example", encoding="utf-8").read()
        self.assertRegex(env, re.compile(r"^MOOD_COORD_DELIVER=1", re.M))
        self.assertIn("MOOD_COORD_DELIVER", open("README.md", encoding="utf-8").read())

    def test_getattr_defaults_false(self):
        self.assertFalse(getattr(SimpleNamespace(), "mood_coord_deliver_enabled", False))


if __name__ == "__main__":
    unittest.main()
