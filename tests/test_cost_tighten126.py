"""💸 §1.26 COST_QUERY_TIGHTEN：罵 token 不再被當成本查詢。

截圖（2026-07-12 21:03–21:04）：使用者罵「還浪費我許多 AI 的 token」→ is_cost_question 因
「token」∈ _COST_MONEY_CUES 直接 True → bot 吐出 💸 Gemini API 花費報表＝罵句被當成本查詢。

修（COST_QUERY_TIGHTEN，兩層分離定式：config _bool True／monitor getattr False）：
(a) selfstate.is_cost_question 加關鍵字參 tight（預設 False＝intent.py 既有呼叫與 test_cost_query
    全部逐位元不變）。tight=True：①抱怨框排除**先行**（浪費/亂花/白花/花我的 → 直接 False，混合句
    「你浪費了多少 token」也 False）；②金錢線索詞命中後仍需問句形（多少/幾/嗎/？/?/呢/查/報/列一下）；
    ③「花費/成本＋多少/幾」分支原樣不動。三張詞表一字不改。
(b) monitor 路由守門（單一咽喉點）：intent.resolve 之後、分派之前，route.kind=='cost' 且旗標開
    且過不了 tight → 降回 intent 的預設聊天回退（fact_or_chat＝一般互動回覆，不進 💸 快路）。
全 stub、零網路。
"""

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from telegram_monitor import cost, intent, monitor, referent, selfstate
from telegram_monitor.state import State

NOW = 1_700_000_000

# 罵句（抱怨框）：tight=True 必須 False（截圖重演 C 的兩句）
COMPLAINTS = ["還浪費我許多 AI 的 token", "你很浪費 token"]
# 混合句拍板點：規格明定「抱怨框排除先行」→ 含罵詞就算帶「多少」也 False
MIXED = "你浪費了多少 token"
# 真查帳問句：tight=True 仍要全部 True（含 test_cost_query 既有全部正例——「現在的用量呢」釘住問句形集必含「呢」）
POSITIVES = ["這個月花了多少錢", "你花了我多少 token", "API 成本多少",
             "目前花費了多少？", "API 花了多少錢", "你燒了多少錢", "目前成本多少",
             "花費多少", "現在的用量呢", "token 用了多少", "這個月的開銷多少"]
# 既有負例（感性問句/無關句）：tight=True 仍 False
NEGATIVES = ["你在我身上花費的心力值得嗎", "我花了很多時間", "你花了多少心思", "花了多少精力",
             "幫我看記寫", "你現在怎樣", "現在幾點", "我睡多久了"]


class TightIsCostQuestionTest(unittest.TestCase):
    """(a) selfstate.is_cost_question(text, tight=True) 的判定語意。"""

    def test_complaints_false_under_tight(self):
        for q in COMPLAINTS:
            self.assertFalse(selfstate.is_cost_question(q, tight=True), q)

    def test_mixed_complaint_with_question_form_still_false(self):
        # 拍板點（open_risks）：抱怨框排除**先行** → 「你浪費了多少 token」帶問句形也 False
        self.assertFalse(selfstate.is_cost_question(MIXED, tight=True), MIXED)

    def test_positives_still_true_under_tight(self):
        for q in POSITIVES:
            self.assertTrue(selfstate.is_cost_question(q, tight=True), q)

    def test_negatives_still_false_under_tight(self):
        for q in NEGATIVES:
            self.assertFalse(selfstate.is_cost_question(q, tight=True), q)

    def test_money_cue_statement_without_question_form_false_under_tight(self):
        # 金錢線索詞（token）命中但整句是陳述、無問句形 → tight 不算查帳
        self.assertFalse(selfstate.is_cost_question("我很珍惜 token", tight=True))

    def test_default_unchanged_baseline(self):
        """基線護欄：tight 預設 False＝現狀逐位元——罵句照舊 True（證明旗標關零變動）。"""
        for q in COMPLAINTS + [MIXED]:
            self.assertTrue(selfstate.is_cost_question(q), q)

    def test_intent_routing_unchanged(self):
        """intent.py 本體不改：真問句照舊 cost；罵句在 intent 層也照舊 cost（守門在 monitor 端）。"""
        self.assertEqual(intent.resolve("目前花費了多少？", referent.Referent()).kind, "cost")
        self.assertEqual(intent.resolve("還浪費我許多 AI 的 token", referent.Referent()).kind, "cost")


class HandleMessageTightenTest(unittest.TestCase):
    """(b) monitor 路由守門端到端：旗標開＝罵句不吐 💸 走一般路徑；真問句照吐；旗標關＝現狀鎖定。"""

    def _run(self, text, cfg):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        s.cost_month_usd, s.cost_total_usd = 2.41, 3.22
        meter = cost.CostMeter(in_per_m=0.3, out_per_m=2.5, usd_twd=32,
                               window_min=10, threshold_twd=10, cooldown_min=30)
        called = {"reply": 0, "ask": 0}
        coach = SimpleNamespace(
            enabled=True, meter=meter,
            reply=lambda *a, **k: called.__setitem__("reply", called["reply"] + 1) or "（一般聊天）",
            ask=lambda *a, **k: called.__setitem__("ask", called["ask"] + 1) or ("chat", None, "（一般聊天）"))
        client = SimpleNamespace(sent=[], dry_run=False)
        client.send = lambda t: client.sent.append(t) or True
        update = {"message": {"chat": {"id": 1}, "text": text, "date": NOW}}
        with patch("telegram_monitor.coach.build_memory_brief", return_value="brief"):
            monitor.handle_message(update, coach, None, {"meta": {}, "records": []}, object(),
                                   s, client, cfg, None)
        return client, called

    def _cfg_on(self):
        return SimpleNamespace(dry_run=False, telegram_chat_id="", cost_query_tighten_enabled=True)

    def _cfg_off(self):
        # 既有測試假 cfg 形：**無** cost_query_tighten_enabled 屬性＝getattr 預設 False＝基線
        return SimpleNamespace(dry_run=False, telegram_chat_id="")

    def test_flag_on_complaint_no_cost_report(self):
        """截圖重演 C：旗標開＋「還浪費我許多 AI 的 token」→ 無 💸 報表、走一般互動回覆路徑。"""
        client, called = self._run("還浪費我許多 AI 的 token", self._cfg_on())
        joined = "".join(client.sent)
        self.assertNotIn("💸", joined)
        self.assertGreaterEqual(called["ask"] + called["reply"], 1)   # 走了一般回覆路徑（沒被吞掉）
        self.assertTrue(client.sent)                                  # 有回話（罵句仍被接住、不是沉默）

    def test_flag_on_real_question_still_reports(self):
        """真查帳問句不受影響：旗標開＋「目前花費了多少？」→ 照吐 💸 報表、不走 LLM。"""
        client, called = self._run("目前花費了多少？", self._cfg_on())
        joined = "".join(client.sent)
        self.assertIn("💸", joined)
        self.assertIn("US$2.41", joined)
        self.assertEqual(called["ask"], 0)
        self.assertEqual(called["reply"], 0)

    def test_flag_off_complaint_still_cost_route(self):
        """現狀鎖定：旗標關（假 cfg 無此欄）→ 罵句照舊進 cost 路由吐 💸（基線行為零變動）。"""
        client, _ = self._run("還浪費我許多 AI 的 token", self._cfg_off())
        self.assertIn("💸", "".join(client.sent))


if __name__ == "__main__":
    unittest.main()
