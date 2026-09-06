"""💸 目前花費了多少：問 API 估算成本要確定性查 api_cost、回實際數字——
不被自我在場窗吃成「我沒辦法直接告訴你」的感性回避（截圖修復）。"""

import os
import tempfile
import unittest
from types import SimpleNamespace

from telegram_monitor import cost, datatools, intent, monitor, referent, selfstate
from telegram_monitor.state import State

NOW = 1_700_000_000


class IsCostQuestionTest(unittest.TestCase):
    def test_positive(self):
        for q in ["目前花費了多少？", "API 花了多少錢", "你燒了多少錢", "目前成本多少",
                  "花費多少", "現在的用量呢", "token 用了多少", "這個月的開銷多少"]:
            self.assertTrue(selfstate.is_cost_question(q), q)

    def test_excludes_effort_and_time(self):
        # 含「花費」但問的是心力/時間＝感性問句，不是查帳
        for q in ["你在我身上花費的心力值得嗎", "我花了很多時間", "你花了多少心思", "花了多少精力"]:
            self.assertFalse(selfstate.is_cost_question(q), q)

    def test_not_other_questions(self):
        for q in ["幫我看記寫", "你現在怎樣", "現在幾點", "我睡多久了"]:
            self.assertFalse(selfstate.is_cost_question(q), q)

    def test_routes_to_cost(self):
        self.assertEqual(intent.resolve("目前花費了多少？", referent.Referent()).kind, "cost")
        self.assertEqual(intent.resolve("你花了多少心思", referent.Referent()).kind, "fact_or_chat")


class ApiCostTest(unittest.TestCase):
    def _meter(self):
        return cost.CostMeter(in_per_m=0.3, out_per_m=2.5, usd_twd=32,
                              window_min=10, threshold_twd=10, cooldown_min=30)

    def test_reports_persistent_month_total_today_in_twd_and_usd(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.cost_month_usd, s.cost_total_usd, s.cost_since_digest_usd = 2.41, 3.22, 0.11   # 對齊 Google 後台
        out = datatools.api_cost(SimpleNamespace(meter=self._meter(), state=s, now=None, tz=None))
        self.assertIn("💸", out)
        self.assertIn("本月以來", out)
        self.assertIn("累計總估", out)
        self.assertIn("今天", out)
        self.assertIn("US$2.41", out)            # 本月＝Google 月度
        self.assertIn("US$3.22", out)            # 累計＝Google 28 天
        self.assertIn("NT$77.1", out)            # 2.41×32（NT$ 與 US$ 並列、好對 Google）
        self.assertNotIn("本次啟動以來", out)     # 不再用會被重啟洗掉的 session 當主數字

    def test_cross_month_does_not_report_last_month_as_this_month(self):
        from datetime import datetime, timezone
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.cost_month_usd, s.cost_month_key = 5.0, "2026-05"      # 上個月的累計
        now = datetime(2026, 6, 1, tzinfo=timezone.utc)          # 已是六月
        out = datatools.api_cost(SimpleNamespace(meter=self._meter(), state=s, now=now, tz=timezone.utc))
        self.assertIn("本月以來：約 NT$0.0", out)                  # 換月後本月還沒花 → 報 0，不是上個月的 5
        self.assertNotIn("US$5.00", out)

    def test_no_meter(self):
        self.assertIn("沒啟用", datatools.api_cost(SimpleNamespace(meter=None, state=None)))


class HandleMessageCostTest(unittest.TestCase):
    """截圖修復：花費問句即使在自我在場窗內，也走確定性 api_cost、回數字，不走 coach.reply 感性回避。"""

    def test_cost_question_in_self_window_answers_with_figure(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        s.self_topic_ts = NOW                     # 自我在場窗開著（截圖的觸發條件）
        s.cost_month_usd, s.cost_total_usd = 2.41, 3.22
        meter = cost.CostMeter(0.3, 2.5, 32, 10, 10, 30)
        called = {"reply": False, "ask": False}
        coach = SimpleNamespace(
            enabled=True, meter=meter,
            reply=lambda *a, **k: called.__setitem__("reply", True) or "（感性回避）",
            ask=lambda *a, **k: called.__setitem__("ask", True) or ("chat", None, "x"))
        client = SimpleNamespace(sent=[], dry_run=False)
        client.send = lambda t: client.sent.append(t) or True
        update = {"message": {"chat": {"id": 1}, "text": "目前花費了多少？", "date": NOW}}
        monitor.handle_message(update, coach, None, {"meta": {}, "records": []}, object(),
                               s, client, SimpleNamespace(dry_run=False, telegram_chat_id=""), None)
        joined = "".join(client.sent)
        self.assertIn("💸", joined)
        self.assertIn("US$2.41", joined)          # 回了實際估算數字（本月＝Google 月度）
        self.assertFalse(called["reply"])         # 沒走自我在場感性回避
        self.assertFalse(called["ask"])           # 也沒丟給 function-calling 賭 LLM 挑工具


if __name__ == "__main__":
    unittest.main()
