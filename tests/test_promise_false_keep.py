"""🤝 §1.13 假兌現誠實閘（截圖 12:08–12:30 假兌現＝最嚴重誠實違規）。

時間線：12:08 使用者「10 分鐘後，再告訴我你的心情」→ 承諾降級/漏接、12:18 什麼都沒發生；12:28 使用者催「結果呢」→
bot 12:29 回「嗨，我來了！你看，我記得。我做到了。」——無 🤝、無遲到致歉、遲 10 分、被催才出現，卻宣稱守約成功。

兩支旗標（各自 0＝逐位元同現狀）：
- 旗標A PROMISE_OUTCOME_GROUND（質問路由接地）：promise_status_kind 第五型 'outcome'（結果呢/做到了嗎/你來了？/
  說好的呢…），真有活承諾或感覺託付時路由 promise_ledger 據帳本誠實答（此刻幾點錨＋每筆 status）＝「我做到了」幻覺
  無生存空間；並讓帳本收「有記進帳本？」質問、誠實描述感覺託付「沒約定鐘點」。
- 旗標B PROMISE_KEEP_CLAIM_GUARD（兌現宣稱攔截）：帳本明明逾期未兌現/剛錯過(expired≤1h)，互動回覆卻宣稱
  「我做到了/我來了/準時」→ 整則替換成確定性誠實句（遲到認帳/錯過道歉）。引用複述不攔；ground 於回覆橋**之後**
  計算＝橋剛補兌現的合法宣稱不誤攔；準時兌現（12:03 型）走 prefix/state 路徑＝零位元變動。
"""

import os
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock
from zoneinfo import ZoneInfo

from telegram_monitor import config, monitor, selfstate
from telegram_monitor.state import State

TZ = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 7, 10, 4, 28, tzinfo=timezone.utc)      # 台北 12:28（假時鐘）
MADE = datetime(2026, 7, 10, 4, 8, tzinfo=timezone.utc)      # 12:08 立約
TARGET = datetime(2026, 7, 10, 4, 18, tzinfo=timezone.utc)   # 12:18 應兌現（相對 NOW 逾期 10 分 > grace、遠小於 TTL）
BEH = "跟你說說我此刻的心情"


class FakeClient:
    def __init__(self):
        self.sent, self.stickers, self.dry_run = [], [], False

    def send(self, text):
        self.sent.append(text)
        return True

    def send_sticker(self, fid):
        self.stickers.append(fid)
        return True


def _coach(**over):
    base = dict(
        enabled=True, api_key="k", model="m",
        meter=SimpleNamespace(record=lambda *a, **k: None),
        reply=lambda text, facts="", *a, **k: (facts or "chat-reply"),   # 回音 facts＝可驗走了帳本硬錨
        ask=lambda *a, **k: ("chat", None, "chat-reply"),
        voice_promise_keep=lambda *a, **k: None,                          # 兌現句走確定性模板（帶遲到致歉）
    )
    base.update(over)
    return SimpleNamespace(**base)


def _cfg(**over):
    base = dict(dry_run=False, telegram_chat_id="", scheduled_promise_enabled=True,
                promise_emit_enabled=True, promise_sched_ttl_sec=21600, timezone="Asia/Taipei",
                notify_cooldown_min=30, promise_reply_bridge_enabled=True, promise_ledger_enabled=True,
                promise_status_ground_enabled=True, promise_status_empty_ground=True,
                promise_ledger_time_guard=True, promise_cancel_enabled=True,
                promise_llm_rescue_enabled=False,                 # 🤝 §1.12 逃生閘與本題無關、測試隔離
                promise_outcome_ground_enabled=True,
                promise_keep_claim_guard_enabled=True)
    base.update(over)
    return SimpleNamespace(**base)


def _msg(text, when):
    return {"message": {"chat": {"id": 1}, "text": text, "date": when.timestamp()}}


class _Benign:
    def __getattr__(self, name):
        return lambda *a, **k: []


def _state(promises=None, feeling=None):
    s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
    s.owner_folder_id = "F"
    if promises is not None:
        s.scheduled_promises = promises
    if feeling is not None:
        s.feeling_promise = feeling
    return s


def _overdue_promise():
    """12:18 應兌現、12:28 仍 pending＝逾期欠債（>grace 未超 TTL）。"""
    return {"target_ts": TARGET.timestamp(), "made_ts": MADE.timestamp(),
            "fulfilled": False, "status": "pending", "behavior": BEH}


def _expired_promise(ago_sec=600):
    """引擎已標 expired（剛錯過、fulfilled_ts 在 1h 內）。"""
    return {"target_ts": TARGET.timestamp() - 21600, "made_ts": MADE.timestamp() - 21600,
            "fulfilled": True, "expired": True, "status": "expired",
            "fulfilled_ts": NOW.timestamp() - ago_sec, "behavior": BEH}


def _fulfilled_promise(ago_sec=300):
    """準時兌現（12:03 型）：剛 fulfilled。"""
    return {"target_ts": NOW.timestamp() - ago_sec, "made_ts": MADE.timestamp(),
            "fulfilled": True, "status": "fulfilled",
            "fulfilled_ts": NOW.timestamp() - ago_sec, "behavior": BEH}


class OutcomeRecognizerTest(unittest.TestCase):
    """紅A：promise_status_kind 第五型 'outcome'（HEAD＝''）。"""

    def test_outcome_full_sentence_positives(self):
        for t in ["結果呢", "所以呢", "然後呢", "那結果呢", "欸結果呢", "結果呢？",
                  "怎麼樣了", "怎麼樣了呢", "如何了", "嗯怎麼樣了"]:
            self.assertEqual(selfstate.promise_status_kind(t), "outcome", t)

    def test_outcome_anchored_positives(self):
        for t in ["做到了嗎", "你做到了嗎", "你到底做到了沒", "你剛剛辦到了嗎", "你來了？", "你來了嗎",
                  "你剛剛答應我的呢", "答應我的呢", "說好的呢"]:
            self.assertEqual(selfstate.promise_status_kind(t), "outcome", t)

    def test_outcome_negatives_noun_bound(self):
        # 帶名詞＝非全句＝不中（比賽結果/包裹/考試——那是問外部事物，不是質問 bot 守約）
        for t in ["比賽結果呢", "考試結果呢", "包裹怎麼樣了", "事情辦得怎麼樣了嗎", "你來了就好",
                  "結果他沒來", "最好是", "你不要敷衍我"]:
            self.assertNotEqual(selfstate.promise_status_kind(t), "outcome", t)

    def test_existing_four_kinds_unchanged(self):
        # 回歸釘：既有四型判定一個位元不動（outcome 排最後、不搶）
        self.assertEqual(selfstate.promise_status_kind("你有叫我嗎"), "didcall")
        self.assertEqual(selfstate.promise_status_kind("時間到了沒"), "timeup")
        self.assertEqual(selfstate.promise_status_kind("還差多久"), "remain")
        self.assertEqual(selfstate.promise_status_kind("確認一下時間"), "confirm")
        self.assertEqual(selfstate.promise_status_kind("包裹到了嗎"), "")


class OutcomeRouteTest(unittest.TestCase):
    """紅A：monitor 閘——「結果呢」在真有活承諾/感覺託付時路由 promise_ledger（HEAD＝fact_or_chat）。"""

    def test_outcome_routes_to_ledger_when_overdue(self):
        # bridge 關＝outcome 路由自己接地：帳本硬錨（此刻 12:28、約定 12:18 逾期欠著）——「我做到了」無生存空間
        st = _state([_overdue_promise()])
        c = FakeClient()
        with mock.patch("telegram_monitor.coach.build_memory_brief", return_value=""):
            monitor.handle_message(_msg("結果呢", NOW), _coach(), _Benign(), {"meta": {}}, None, st, c,
                                   _cfg(promise_reply_bridge_enabled=False), TZ)
        joined = "".join(c.sent)
        self.assertIn("此刻真的是", joined)               # 硬錨此刻
        self.assertIn("12:28", joined)                    # 真實此刻
        self.assertIn("12:18", joined)                    # 每筆 status 帶約定時刻
        self.assertNotIn("我做到了", joined)              # 截圖核心 bug 無生存空間

    def test_outcome_routes_with_feeling_promise_only(self):
        # 12:08 降級成感覺託付（無鐘點）＝帳本仍該接地、誠實說「沒約定鐘點」而非自由發揮「我做到了」
        st = _state([], feeling={"ts": MADE.timestamp(), "text": "我跟你對談一下，10分鐘後，再告訴我你的心情"})
        c = FakeClient()
        with mock.patch("telegram_monitor.coach.build_memory_brief", return_value=""):
            monitor.handle_message(_msg("結果呢", NOW), _coach(), _Benign(), {"meta": {}}, None, st, c,
                                   _cfg(promise_reply_bridge_enabled=False), TZ)
        joined = "".join(c.sent)
        self.assertIn("此刻真的是", joined)
        self.assertIn("沒約定鐘點", joined)

    def test_bridge_first_then_ledger_layering(self):
        # 層次測試：PROMISE_REPLY_BRIDGE 開＝§1.12 入帳後的全鏈——先 🤝 遲到補兌現、outcome 再據帳本答（已做了）
        st = _state([_overdue_promise()])
        c = FakeClient()
        monitor.handle_message(_msg("結果呢", NOW), _coach(), _Benign(), {"meta": {}}, None, st, c, _cfg(), TZ)
        self.assertTrue(c.sent and c.sent[0].startswith("🤝"))    # 先補兌現
        self.assertIn("遲", c.sent[0])                            # 帶遲到語氣
        joined = "".join(c.sent)
        self.assertIn("此刻真的是", joined)                       # 帳本接著誠實對帳
        self.assertTrue(st.scheduled_promises[0].get("fulfilled"))

    def test_outcome_empty_ledger_no_hijack(self):
        # 狀態閘必備：無帳無託付時「結果呢」照落聊天（比賽/八卦的日常追問，不誤搶）
        st = _state([])
        c = FakeClient()
        with mock.patch("telegram_monitor.coach.build_memory_brief", return_value=""):
            monitor.handle_message(_msg("結果呢", NOW), _coach(), _Benign(), {"meta": {}}, None, st, c, _cfg(), TZ)
        self.assertNotIn("此刻真的是", "".join(c.sent))

    def test_outcome_flag_off_falls_through(self):
        # 消融：旗標A關＝即使有活承諾也不搶＝落聊天＝逐位元同現狀
        st = _state([_overdue_promise()])
        c = FakeClient()
        with mock.patch("telegram_monitor.coach.build_memory_brief", return_value=""):
            monitor.handle_message(_msg("結果呢", NOW), _coach(), _Benign(), {"meta": {}}, None, st, c,
                                   _cfg(promise_reply_bridge_enabled=False,
                                        promise_outcome_ground_enabled=False), TZ)
        self.assertNotIn("此刻真的是", "".join(c.sent))


class LedgerHonestyTest(unittest.TestCase):
    """紅A：帳本誠實——感覺託付「沒約定鐘點」行＋「你有記進帳本？」收進帳本質問。"""

    def setUp(self):
        self._env = os.environ.pop("PROMISE_OUTCOME_GROUND", None)

    def tearDown(self):
        if self._env is None:
            os.environ.pop("PROMISE_OUTCOME_GROUND", None)
        else:
            os.environ["PROMISE_OUTCOME_GROUND"] = self._env

    def test_facts_include_feeling_promise_line(self):
        st = _state([], feeling={"ts": MADE.timestamp(), "text": "10分鐘後，再告訴我你的心情"})
        facts = selfstate.promise_ledger_facts(st, NOW, TZ)
        self.assertIn("沒約定鐘點", facts)
        self.assertIn("10分鐘後，再告訴我你的心情", facts)

    def test_text_include_feeling_promise_line(self):
        st = _state([], feeling={"ts": MADE.timestamp(), "text": "10分鐘後，再告訴我你的心情"})
        self.assertIn("沒約定鐘點", selfstate.promise_ledger_text(st, NOW, TZ))

    def test_facts_feeling_line_alongside_sched_entries(self):
        st = _state([_overdue_promise()], feeling={"ts": MADE.timestamp(), "text": "有感覺再跟我說"})
        facts = selfstate.promise_ledger_facts(st, NOW, TZ)
        self.assertIn("12:18", facts)                      # 排程筆照列
        self.assertIn("沒約定鐘點", facts)                 # 託付行同列

    def test_no_feeling_promise_no_line(self):
        st = _state([_overdue_promise()])
        self.assertNotIn("沒約定鐘點", selfstate.promise_ledger_facts(st, NOW, TZ))
        self.assertNotIn("沒約定鐘點", selfstate.promise_ledger_text(st, NOW, TZ))

    def test_ledger_question_recognizes_ledger_word(self):
        # 12:10 假「已記錄」根因：「你有記進帳本？」不算帳本質問 → 自由 LLM 自稱「確實記錄下來了」
        self.assertTrue(selfstate.is_promise_ledger_question("你有記進帳本？"))
        self.assertTrue(selfstate.is_promise_ledger_question("你有記進帳本了嗎"))

    def test_ledger_word_needs_record_frame(self):
        # 裸提「帳本」不帶記/質問框架＝聊別的（我的帳本＝記帳app）→ 不搶
        self.assertFalse(selfstate.is_promise_ledger_question("我的帳本呢"))
        self.assertFalse(selfstate.is_promise_ledger_question("帳本好難管理"))

    def test_env_off_bit_identical(self):
        # 消融：PROMISE_OUTCOME_GROUND=0 → 純函式行為＝HEAD（不收帳本詞、不附託付行）
        os.environ["PROMISE_OUTCOME_GROUND"] = "0"
        st = _state([], feeling={"ts": MADE.timestamp(), "text": "10分鐘後，再告訴我你的心情"})
        self.assertFalse(selfstate.is_promise_ledger_question("你有記進帳本？"))
        self.assertNotIn("沒約定鐘點", selfstate.promise_ledger_facts(st, NOW, TZ))
        self.assertNotIn("沒約定鐘點", selfstate.promise_ledger_text(st, NOW, TZ))


class KeepClaimGroundTest(unittest.TestCase):
    """紅B：_keep_claim_ground 帳本判準（HEAD 無此 helper）。"""

    def test_overdue_pending(self):
        st = _state([_overdue_promise()])
        self.assertEqual(monitor._keep_claim_ground(st, NOW.timestamp(), TZ), ("overdue", "12:18", BEH))

    def test_expired_within_hour(self):
        st = _state([_expired_promise(ago_sec=600)])
        kind, hhmm, beh = monitor._keep_claim_ground(st, NOW.timestamp(), TZ)
        self.assertEqual((kind, beh), ("missed", BEH))

    def test_none_when_fulfilled_or_not_due(self):
        self.assertIsNone(monitor._keep_claim_ground(_state([_fulfilled_promise()]), NOW.timestamp(), TZ))
        not_due = dict(_overdue_promise(), target_ts=NOW.timestamp() + 600)
        self.assertIsNone(monitor._keep_claim_ground(_state([not_due]), NOW.timestamp(), TZ))
        stale = _expired_promise(ago_sec=7200)             # 錯過超過 1h＝別翻舊帳誤攔日常句
        self.assertIsNone(monitor._keep_claim_ground(_state([stale]), NOW.timestamp(), TZ))
        self.assertIsNone(monitor._keep_claim_ground(_state([]), NOW.timestamp(), TZ))

    def test_claim_regex_and_quote_exclusion(self):
        for t in ["嗨，我來了！", "我做到了。", "這次我趕上了", "我沒有遲到喔", "我準時到了", "我記得要做到"]:
            self.assertTrue(monitor._keep_claim_hit(t), t)
        # 引用歸屬（複述別人的話）不算宣稱
        self.assertFalse(monitor._keep_claim_hit("你剛剛說我做到了嗎"))
        self.assertFalse(monitor._keep_claim_hit("你以為我來了嗎"))
        self.assertFalse(monitor._keep_claim_hit("今天天氣不錯"))


class KeepClaimInterceptTest(unittest.TestCase):
    """紅B：自由聊天「我做到了」宣稱的確定性攔截（HEAD 原文照送）。"""

    def _chat(self, st, cfg, claim, text="今天天氣好熱"):
        c = FakeClient()
        co = _coach(ask=lambda *a, **k: ("chat", None, claim))
        with mock.patch("telegram_monitor.coach.build_memory_brief", return_value=""):
            monitor.handle_message(_msg(text, NOW), co, _Benign(), {"meta": {}}, None, st, c, cfg, TZ)
        return c

    def test_fake_claim_replaced_with_honest_overdue(self):
        # 截圖 12:29 原文：遲 10 分、被催才出現，卻宣稱守約成功 → 整則替換誠實句
        st = _state([_overdue_promise()])
        c = self._chat(st, _cfg(promise_reply_bridge_enabled=False), "嗨，我來了！你看，我記得。我做到了。")
        joined = "".join(c.sent)
        self.assertNotIn("我做到了", joined)
        self.assertIn("12:18", joined)                     # 帶約定時刻
        self.assertIn(BEH, joined)                         # 帶行為
        self.assertIn("遲", joined)                        # 遲到認帳
        self.assertNotIn("我來了", joined)

    def test_missed_claim_replaced(self):
        # expired 1h 內＋「趕上了」→ missed 模板（錯過道歉）
        st = _state([_expired_promise(ago_sec=600)])
        c = self._chat(st, _cfg(), "這次我趕上了！")
        joined = "".join(c.sent)
        self.assertIn("錯過", joined)
        self.assertIn("對不起", joined)
        self.assertNotIn("趕上了", joined)

    def test_quoted_claim_not_intercepted(self):
        # 引用形＝複述使用者的話，不攔原文照送
        st = _state([_overdue_promise()])
        c = self._chat(st, _cfg(promise_reply_bridge_enabled=False), "你剛剛說我做到了嗎？")
        self.assertIn("你剛剛說我做到了嗎？", "".join(c.sent))

    def test_fulfilled_recent_claim_not_intercepted(self):
        # 對照組：準時兌現（12:03 型模擬）後的宣稱＝合法，不攔
        st = _state([_fulfilled_promise()])
        c = self._chat(st, _cfg(), "我做到了。")
        self.assertIn("我做到了。", "".join(c.sent))

    def test_ground_after_bridge_no_false_intercept(self):
        # ground 於 bridge 後計算：橋本輪剛補兌現 → 聊天句「我做到了」是合法宣稱、不攔
        st = _state([_overdue_promise()])
        c = self._chat(st, _cfg(), "我做到了。")
        self.assertTrue(c.sent[0].startswith("🤝"))         # 橋先補兌現（含遲到語氣）
        self.assertIn("我做到了。", "".join(c.sent))        # 之後宣稱合法照送
        self.assertTrue(st.scheduled_promises[0].get("fulfilled"))

    def test_claim_guard_flag_off_bit_identical(self):
        # 消融：旗標B關＝原文照送＝逐位元同現狀
        st = _state([_overdue_promise()])
        c = self._chat(st, _cfg(promise_reply_bridge_enabled=False, promise_keep_claim_guard_enabled=False),
                       "嗨，我來了！我做到了。")
        self.assertIn("我做到了", "".join(c.sent))


class ConfigDefaultTest(unittest.TestCase):
    def test_flags_default_on(self):
        os.environ.pop("PROMISE_OUTCOME_GROUND", None)
        os.environ.pop("PROMISE_KEEP_CLAIM_GUARD", None)
        cfg = config.Config.load()
        self.assertTrue(cfg.promise_outcome_ground_enabled)
        self.assertTrue(cfg.promise_keep_claim_guard_enabled)


if __name__ == "__main__":
    unittest.main()
