"""🤝 §1.11 句首「我」修正：無時間前導子句剝除（capture＋structural guard 同一把）＋§1.09 時間判準改問 temporal。

截圖（2026-07-10 12:08）：「我跟你對談一下，10 分鐘後，再告訴我你的心情」——捕捉與守門都被
句首「我」啟發式殺死（它看整句第一個字、不看「我」子句是否真的綁著時間；句尾的 at-me 被無視），
於是整句**降級成 feeling promise**（無鐘點帳）→ bot 說「記下來了」半真半假、12:18 什麼都沒發生、
12:28 被催才出現還謊稱守約成功。主管消融實測：拿掉前導子句「我跟你對談一下，」同句就活＝根因確立。

兩支獨立旗標（selfstate 慣例：直接讀環境變數，設 0＝逐位元同現狀）：
- SCHED_HEAD_ME_FIX：句首「我」守門前先剝除**不含時間**的前導我-子句（我-子句自己綁著時間＝真自諾，保留原判）。
  capture（is_scheduled_promise_request）與 §1.09 structural guard 共用同一把 helper＝單一真相。
- PROMISE_GUARD_TEMPORAL：§1.09 結構閘的時間判準改問 temporal.next_clock_epoch（真的解得出未來時刻才算），
  並讓**光禿祈使句**（有你、有時間，但無 at-me 詞也無請求詞）開火——「別廢話，20分鐘後，說說你當時的心情」。
"""

import os
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from telegram_monitor import intent, referent, selfstate

TZ = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 7, 10, 4, 8, 0, tzinfo=timezone.utc)   # 台北 12:08（截圖時刻）

S1 = "別廢話了，20 分鐘後，再告訴我你不同的地方在哪裡？"     # 截圖 #1（全鏈成功的那句，防退化）
S2 = "我跟你對談一下，10 分鐘後，再告訴我你的心情"           # 截圖 #2（本次漏收的那句）
S2B = "我跟你對談一下，10 分鐘後，再告訴我你不同的地方在哪裡"  # 主管消融句：前綴殺死已知成功句
BALD = "別廢話，20分鐘後，說說你當時的心情"                   # §1.10 光禿祈使句（無 at-me、無請求詞、有你）


class HeadMeCaptureTest(unittest.TestCase):
    """【紅】句首「我」但我-子句沒綁時間＝前導寒暄，剝掉後照常捕捉。"""

    def test_screenshot2_sentence_captured(self):
        self.assertTrue(selfstate.is_scheduled_promise_request(S2), S2)

    def test_ablation_sentence_captured(self):
        self.assertTrue(selfstate.is_scheduled_promise_request(S2B), S2B)

    def test_known_good_sentences_still_captured(self):
        # 防退化（部分 HEAD 已綠）：at-me 在場／頭子句自己帶時間 等既有成功形一律不受剝除影響。
        for s in (S1,
                  "10分鐘後，再告訴我你的心情",
                  "我先去忙，30分鐘後跟我說你的感覺",
                  "我問你，5分鐘後提醒我喝水",
                  "我10分鐘後回來，回來時跟我打招呼"):
            self.assertTrue(selfstate.is_scheduled_promise_request(s), s)

    def test_strip_helper_semantics(self):
        # 單子句自諾（無分隔字元）＝不剝；頭子句自己綁時間＝真自諾、不剝；無時間前導子句＝剝到底。
        self.assertEqual(selfstate._strip_timeless_lead_me("我30分鐘後要去開會"), "我30分鐘後要去開會")
        self.assertEqual(selfstate._strip_timeless_lead_me("我10分鐘後回來，回來時跟我打招呼"),
                         "我10分鐘後回來，回來時跟我打招呼")
        self.assertEqual(selfstate._strip_timeless_lead_me("我先去忙，30分鐘後跟我說你的感覺"),
                         "30分鐘後跟我說你的感覺")
        self.assertEqual(selfstate._strip_timeless_lead_me("我跟你對談一下，10分鐘後，再告訴我你的心情"),
                         "10分鐘後，再告訴我你的心情")


class HeadMeGuardTest(unittest.TestCase):
    """【紅】守門（looks_like_timed_request）也用同一把剝除＝capture/guard 行為不再不對稱。"""

    def test_screenshot2_guard_fires(self):
        self.assertTrue(selfstate.looks_like_timed_request(S2), S2)

    def test_bald_imperative_fires_with_clock(self):
        # 光禿祈使句：temporal 解得出未來時刻＋句中有「你」→ 開火（PROMISE_GUARD_TEMPORAL，需傳 now/tz）。
        self.assertTrue(selfstate.looks_like_timed_request(BALD, NOW, TZ), BALD)

    def test_structural_shares_strip(self):
        self.assertTrue(selfstate._timed_request_structural(S2.replace(" ", "")), S2)


class MustRejectTest(unittest.TestCase):
    """【必擋 FALSE】自諾／第三方／過去質問：capture 與 guard（含帶 now 的新判準）都不收。"""

    def test_user_own_plan_and_third_party(self):
        for s in ("我30分鐘後要去開會",
                  "30分鐘後我要去開會",
                  "我媽20分鐘後會叫我起床",
                  "你昨天說十分鐘後告訴我，你告訴了嗎"):
            self.assertFalse(selfstate.is_scheduled_promise_request(s), s)
            self.assertFalse(selfstate.looks_like_timed_request(s), s)
            self.assertFalse(selfstate.looks_like_timed_request(s, NOW, TZ), s)   # 新判準也不誤收

    def test_meeting_chat_not_captured(self):
        # 已知殘留偽陽（guard 允許 True＝交 §1.12 LLM 層裁決）；capture 必須 False。
        self.assertFalse(selfstate.is_scheduled_promise_request("20分鐘後我要開會，你覺得呢"))

    def test_user_self_promise_guard_false(self):
        # 「我8點回應你」＝使用者自諾（單子句、無分隔）——剝除不動它、guard 不掛。
        self.assertFalse(selfstate.looks_like_timed_request("我8點回應你"))
        self.assertFalse(selfstate.looks_like_timed_request("我8點回應你", NOW, TZ))


class DataReportPathTest(unittest.TestCase):
    """【資料路徑不誤標】§0.76：到點回報資料＝排程承諾、不落 feeling。"""

    def test_server_status_is_scheduled_not_feeling(self):
        s = "10分鐘後跟我說伺服器狀態"
        self.assertFalse(selfstate.is_feeling_promise_request(s), s)
        route = intent.resolve(s, referent.Referent(), cfg=None)
        self.assertEqual(route.kind, "scheduled_promise", s)


class FlagAblationTest(unittest.TestCase):
    """旗標 0＝逐位元同現狀（HEAD 位元行為）。"""

    def test_head_me_fix_off_restores_head_bits(self):
        with patch.dict(os.environ, {"SCHED_HEAD_ME_FIX": "0"}):
            self.assertFalse(selfstate.is_scheduled_promise_request(S2), S2)       # HEAD：句首我殺死
            self.assertFalse(selfstate.is_scheduled_promise_request(S2B), S2B)
            # 既有成功形不受旗標影響
            self.assertTrue(selfstate.is_scheduled_promise_request(S1), S1)

    def test_guard_temporal_off_restores_head_bits(self):
        with patch.dict(os.environ, {"PROMISE_GUARD_TEMPORAL": "0"}):
            self.assertFalse(selfstate.looks_like_timed_request(BALD, NOW, TZ), BALD)   # HEAD：光禿句不開火

    def test_guard_without_now_keeps_old_branch(self):
        # 既有呼叫端（不傳 now）＝走舊 _TIMED_REQ_TIMEY 分支＝逐位元同現狀：光禿句照舊 False。
        self.assertFalse(selfstate.looks_like_timed_request(BALD), BALD)


if __name__ == "__main__":
    unittest.main()
