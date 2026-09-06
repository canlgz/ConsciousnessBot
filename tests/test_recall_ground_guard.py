"""🧭 §1.36 記寫回想不再編造（RECALL_GROUND_GUARD）：content-recall 偵測器 ＋ 強接地 hint ＋ 事後幻覺守門。

截圖根因：使用者問「我是在說什麼事覺得好累／那天寫了什麼／為什麼覺得X」＝在**回想自己某筆記寫的內容/原因**，
記寫原文其實只有一句「覺得自己好累」，bot 卻在接地層編造原文沒有的具體事由（照顧家人的身體狀況／不太順利的
家庭聚餐／一個禮拜沒睡好）＝接地層幻覺（使用者說「又發生編造」）。

三段防線（比照 §1.13B/§1.20/§1.23/§1.34 sibling）：
 A. selfstate.is_content_recall_question：偵測「問記寫內容/原因的回想」（排除花費/狀態/鐘點/指向 bot 的對話事件）。
 B. persona.RECALL_GROUND_HINT：命中才注入的強接地 hint（fact_or_chat 的 coach.ask 與 promise_ledger 的 coach.reply 兩 lane）。
 C.（核心）_say 事後幻覺守門：答案裡「歸因給記寫」的具體事由若不在 records 原文 → 整則替換誠實句
    （lane-agnostic：arm 於 handle_message、判定/替換於 _say、只讀 _TURN、不呼叫 LLM）。

偽陽性最關鍵（自證）：bot 講**自己**的感受/推理（無歸因框架）不攔；引用歸屬「你說我寫了X」不攔；
記寫原文真有的字句 substring 命中＝不算幻覺。全 stub、零網路。旗標關＝不 arm＝逐位元同現狀。
"""

import os
import re
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from telegram_monitor import monitor, selfstate, persona
from telegram_monitor.state import State

TZ = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 7, 13, 4, 8, 0, tzinfo=timezone.utc)   # 台北 12:08

# 截圖式的接地層編造答案（原文只有「覺得自己好累」，卻補了照顧家人/家庭/沒睡好等具體事由）
FABRICATION = ("6/25 那天你寫的，其實是因為家裡的一些事情，特別是照顧家人的身體狀況，"
               "加上工作也不太順利。你說自己那陣子一個禮拜沒睡好覺。")


class DetectorTest(unittest.TestCase):
    """A. is_content_recall_question：問記寫內容/原因的回想＝True；各有專路的排除集＝False。"""

    def test_content_recall_true(self):
        for t in ("我是在說什麼事覺得自己好累",
                  "我是在說什麼事覺得自己好累呢，我都忘記了",
                  "6/25那天我寫了什麼",
                  "我那天寫到什麼",
                  "為什麼我覺得那麼累"):
            self.assertTrue(selfstate.is_content_recall_question(t), t)

    def test_excluded_by_other_lanes_false(self):
        for t in ("這個月花多少錢",        # cost lane
                  "時間到了沒",            # promise 狀態閘
                  "現在幾點",              # convo_clock
                  "你剛剛點了什麼讚",       # 指向 bot 的對話事件
                  "剛剛有人指責你",         # 指向 bot 的對話事件
                  "你怎麼看這則"):         # 問 bot 看法（§0.56 專路）
            self.assertFalse(selfstate.is_content_recall_question(t), t)

    def test_empty_false(self):
        self.assertFalse(selfstate.is_content_recall_question(""))
        self.assertFalse(selfstate.is_content_recall_question(None))


class CorpusSummaryTest(unittest.TestCase):
    """C3 語料/摘要 helper：正規化串接、最近一筆截 30 字。"""

    def test_corpus_normalizes_and_strips_punct(self):
        corpus = monitor._recall_corpus([{"text": "家庭聚餐後好累，覺得撐不住"}])
        self.assertIn("家庭聚餐", corpus)
        self.assertNotIn("，", corpus)          # 標點剝除、只留 CJK+英數

    def test_summary_takes_latest_and_truncates(self):
        recs = [{"text": "覺得自己好累", "ts": "2026-06-25T10:00:00+08:00"},
                {"text": "更早的一筆", "ts": "2026-06-20T10:00:00+08:00"}]
        self.assertEqual(monitor._recall_summary(recs), "覺得自己好累")   # 新→舊取最新
        long = [{"text": "一" * 50, "ts": "2026-06-25T10:00:00+08:00"}]
        s = monitor._recall_summary(long)
        self.assertTrue(s.endswith("…"))
        self.assertLessEqual(len(s), 31)
        self.assertEqual(monitor._recall_summary([]), "")


class HallucinationHitTest(unittest.TestCase):
    """C3 確定性判定 _recall_hallucination_hit：真陽性 + 四偽陽性 + 引用歸屬排除。"""

    def test_true_positive_fabricated_reasons(self):
        corpus = monitor._recall_corpus([{"text": "覺得自己好累"}])
        ans = ("因為家裡的一些事情，特別是照顧家人的身體狀況，"
               "你說自己那陣子一個禮拜沒睡好覺。")
        self.assertTrue(monitor._recall_hallucination_hit(ans, corpus))

    def test_fp1_quotes_original_only(self):
        # 偽陽性①：答案只引用原文「覺得好累」，去停用詞後無殘留具體段 → False
        corpus = monitor._recall_corpus([{"text": "覺得自己好累"}])
        self.assertFalse(monitor._recall_hallucination_hit("你那天只寫了覺得好累，沒別的", corpus))

    def test_fp4_original_actually_has_it(self):
        # 偽陽性④：原文真有「家庭聚餐」→ substring 命中 corpus → 不算幻覺 → False
        corpus = monitor._recall_corpus([{"text": "家庭聚餐後好累，覺得撐不住"}])
        self.assertFalse(monitor._recall_hallucination_hit("你那筆是不是跟家庭聚餐有關", corpus))

    def test_fp3_bot_feeling_no_attribution_frame(self):
        # 偽陽性③：bot 講自己的感受、無「你說/你寫/特別是/跟…有關」歸因框架 → 不掃 → False
        corpus = monitor._recall_corpus([{"text": "覺得自己好累"}])
        self.assertFalse(monitor._recall_hallucination_hit("我也覺得有點累，想陪你", corpus))

    def test_bot_reasoning_because_not_hit(self):
        # 偽陽性③補強：bot 講自己的推理帶「因為」（刻意不收裸因為）→ 不攔
        corpus = monitor._recall_corpus([{"text": "覺得自己好累"}])
        self.assertFalse(monitor._recall_hallucination_hit(
            "我會這樣回，是因為我想先接住你的情緒。", corpus))

    def test_quote_attribution_excluded(self):
        # 引用歸屬：「你說我寫了照顧家人」＝複述對方談 bot、非 bot 幻覺 → False
        corpus = monitor._recall_corpus([{"text": "覺得自己好累"}])
        self.assertFalse(monitor._recall_hallucination_hit("你說我寫了照顧家人喔？我看看", corpus))

    def test_empty_corpus_any_attribution_hits(self):
        # 空語料（arm 了但沒有記寫原文）→ 任何具體歸因都算幻覺
        self.assertTrue(monitor._recall_hallucination_hit(
            "你說自己那陣子一個禮拜沒睡好覺。", ""))


class SayGateTest(unittest.TestCase):
    """C2 在 _say 互動出口直接驗證守門：arm＋命中→替換；arm 但已接地→放行；未 arm→逐位元不動。"""

    class Cl:
        def __init__(self):
            self.sent, self.dry_run = [], False

        def send(self, text):
            self.sent.append(text)
            return True

    def setUp(self):
        monitor._TURN.clear()
        monitor._TURN["bubbles"] = None

    def _say_out(self, text, **turn):
        monitor._TURN.update(turn)
        cl = self.Cl()
        monitor._say(cl, text)                              # prefix="" state=None＝互動分支
        return "".join(cl.sent)

    def test_armed_hallucination_replaced_with_summary(self):
        corpus = monitor._recall_corpus([{"text": "覺得自己好累"}])
        out = self._say_out(FABRICATION, recall_ground=corpus, recall_summary="覺得自己好累")
        self.assertIn("不足以支持", out)
        self.assertNotIn("你那天只寫", out)
        self.assertNotIn("覺得自己好累", out)
        self.assertNotIn("沒寫", out)
        self.assertNotIn("照顧家人", out)
        self.assertNotIn("沒睡好", out)

    def test_armed_no_summary_uses_generic(self):
        out = self._say_out(FABRICATION, recall_ground="", recall_summary="")
        self.assertIn("無法確認", out)
        self.assertNotIn("照顧家人", out)

    def test_armed_but_grounded_answer_passes(self):
        # arm 了但答案只引用原文＝不命中＝原封放行
        corpus = monitor._recall_corpus([{"text": "覺得自己好累"}])
        out = self._say_out("你那天只寫了覺得好累，沒別的", recall_ground=corpus, recall_summary="覺得自己好累")
        self.assertIn("只寫了覺得好累", out)

    def test_not_armed_passthrough(self):
        # 未 arm（recall_ground 不存在）＝守門恆 no-op＝逐位元同現狀
        out = self._say_out(FABRICATION)
        self.assertIn("照顧家人", out)


class EndToEndArmTest(unittest.TestCase):
    """經真 handle_message arm：fact_or_chat（ask）與 promise_ledger（reply）兩 lane 都替換（lane-agnostic）；
    純資料問句不 arm；旗標關＝編造原句原封送出＋不注入 hint。"""

    def _cfg(self, **over):
        base = dict(dry_run=False, telegram_chat_id="", scheduled_promise_enabled=True,
                    promise_emit_enabled=True, promise_sched_ttl_sec=21600, timezone="Asia/Taipei",
                    notify_cooldown_min=30, promise_reply_bridge_enabled=False, promise_ledger_enabled=True,
                    sched_leave_autoarm_enabled=True, deferred_promise_enabled=True,
                    promise_llm_rescue_enabled=False, sticker_llm_rescue_enabled=False,
                    promise_keep_claim_guard_enabled=True, promise_said_ground_enabled=True,
                    bot_self_promise_enabled=False, promise_preempt_enabled=False,
                    sticker_sent_memory_enabled=True, send_stickers=True,
                    sticker_fakesend_guard_enabled=False,
                    recall_ground_guard_enabled=True)
        base.update(over)
        return SimpleNamespace(**base)

    def _coach(self, reply_text):
        seen = {"ask": None, "reply": None}
        c = SimpleNamespace(enabled=True, api_key="k", model="m",
                            meter=SimpleNamespace(record=lambda *a, **k: None), seen=seen)

        def ask(*a, **k):
            seen["ask"] = k.get("extra_system")
            return ("chat", None, reply_text)

        def reply(*a, **k):
            seen["reply"] = k.get("extra_system")
            return reply_text

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
    BOOM = SimpleNamespace(load_embedding_records=lambda *_: None)

    def _run(self, text, reply_text, cfg=None, records=None):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        cl = SayGateTest.Cl()
        co = self._coach(reply_text)
        if records is None:
            records = [{"text": "覺得自己好累", "ts": "2026-06-25T10:00:00+08:00",
                        "topicLabel": "疲憊", "id": "r1"}]
        data = {"meta": {}, "records": records}
        monitor.handle_message({"message": {"chat": {"id": 1}, "text": text, "date": NOW.timestamp()}},
                               co, self.BOOM, data, self.SNAP, s, cl, cfg or self._cfg(), TZ)
        return "".join(cl.sent), co.seen

    def test_fact_or_chat_replaced_and_hint_injected(self):
        out, seen = self._run("我是在說什麼事覺得自己好累", FABRICATION)
        self.assertIn("不足以支持", out)
        self.assertNotIn("你那天只寫", out)
        self.assertNotIn("覺得自己好累", out)
        self.assertNotIn("照顧家人", out)
        self.assertNotIn("家庭", out)
        self.assertNotIn("沒睡好", out)
        self.assertIn("絕不補一個聽起來合理的原因", seen["ask"] or "")   # RECALL_GROUND_HINT 已注入

    def test_promise_ledger_lane_replaced(self):
        # lane-agnostic：帶「忘記了」路由 promise_ledger（coach.reply），同樣經 _say 替換
        out, seen = self._run("我是在說什麼事覺得自己好累呢，我都忘記了", FABRICATION)
        self.assertIn("不足以支持", out)
        self.assertNotIn("你那天只寫", out)
        self.assertNotIn("照顧家人", out)
        self.assertNotIn("沒睡好", out)
        self.assertIn("絕不補一個聽起來合理的原因", seen["reply"] or "")

    def test_non_recall_chat_not_armed_passthrough(self):
        # 偽陽性②同理：非記寫回想的一般聊天句 → 偵測器 False → 不 arm → coach 原答案原封送出、不注入 hint
        # （「這個月花多少錢」等純資料問句偵測器 False 已由 DetectorTest 釘；那類走 cost/data 快路不經 coach.ask）。
        out, seen = self._run("陪我聊聊天嘛", FABRICATION)
        self.assertEqual(out, FABRICATION)
        self.assertNotIn("絕不補一個聽起來合理的原因", seen["ask"] or "")

    def test_flag_off_byte_identical(self):
        # 旗標關＝不 arm＝編造原句原封送出（byte-identical）＋不注入 hint
        out, seen = self._run("我是在說什麼事覺得自己好累", FABRICATION,
                              cfg=self._cfg(recall_ground_guard_enabled=False))
        self.assertEqual(out, FABRICATION)
        self.assertNotIn("絕不補一個聽起來合理的原因", seen["ask"] or "")


class ConfigSyncTest(unittest.TestCase):
    def test_flag_declared_and_env_readme_synced(self):
        src = open("telegram_monitor/config.py", encoding="utf-8").read()
        self.assertIn("RECALL_GROUND_GUARD", src)
        self.assertIn("recall_ground_guard_enabled", src)
        env = open(".env.example", encoding="utf-8").read()
        self.assertRegex(env, re.compile(r"^RECALL_GROUND_GUARD=1", re.M))
        readme = open("README.md", encoding="utf-8").read()
        self.assertIn("RECALL_GROUND_GUARD", readme)

    def test_persona_hint_exists(self):
        self.assertTrue(hasattr(persona, "RECALL_GROUND_HINT"))
        self.assertIn("絕不補一個聽起來合理的原因", persona.RECALL_GROUND_HINT)

    def test_monitor_getattr_defaults_false(self):
        # 兩層旗標分離：monitor 端 getattr 預設 False＝既有測試假 cfg 未設此欄→不 arm→零翻修
        cfg = SimpleNamespace()
        self.assertFalse(getattr(cfg, "recall_ground_guard_enabled", False))


if __name__ == "__main__":
    unittest.main()
