"""🤝⏱️ §1.08「向我證明你有什麼地方不同」漏收 → 空口答應＋幻覺時刻（截圖：說 10:14，實際 10:05+30=10:35）。

根因：溝通動詞表 `_SCHED_SELF_EXPLAIN_VERB` 沒有「證明/展示」類 → `_self_change_tell_hit` False →
① `is_scheduled_promise_request` False＝**沒入帳**；② `looks_like_timed_request` 也讀同一支 →
**空口答應守門一起瞎掉** → LLM 自由發揮「好，我記下來了…10:14」。一表修好兩層。
另修：兌現措辭把**約定時刻**講成「現在是 10:05」＝對當下時間的假宣稱。
"""

import unittest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from telegram_monitor import persona, selfstate, temporal

TZ = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 7, 10, 2, 5, tzinfo=timezone.utc)   # 台北 10:05
SCREENSHOT = "30分鐘之後，你再向我證明你有什麼地方不同"


class ProveVerbCapturedTest(unittest.TestCase):
    def test_screenshot_sentence_is_captured(self):
        self.assertTrue(selfstate.is_scheduled_promise_request(SCREENSHOT))

    def test_behavior_is_self_change(self):
        self.assertEqual(selfstate.extract_promise_behavior(SCREENSHOT),
                         "跟他說說我此刻有什麼不一樣（我的變化）")   # → §1.05 拍快照、到點真說出改變

    def test_guard_also_fixed(self):
        # 空口答應守門讀同一支 → 一起補上（萬一未來又漏收，守門會擋住「我記下來了」）
        self.assertTrue(selfstate.looks_like_timed_request(SCREENSHOT))

    def test_target_time_is_now_plus_30(self):
        eps = temporal.all_clock_epochs(SCREENSHOT, NOW, TZ)
        self.assertEqual(len(eps), 1)
        hhmm = datetime.fromtimestamp(eps[0], timezone.utc).astimezone(TZ).strftime("%H:%M")
        self.assertEqual(hhmm, "10:35")                     # 截圖 bot 說 10:14＝純幻覺

    def test_other_prove_forms(self):
        for s in ("20分鐘後跟我證明你有什麼不一樣",
                  "10分鐘後證明給我看你哪裡不同" if False else "10分鐘後展示給我你有什麼變化",
                  "半小時後向我展示你有什麼不同"):
            self.assertTrue(selfstate.is_scheduled_promise_request(s), s)

    def test_prove_inner_state_also_captured(self):
        s = "20分鐘後，向我證明你的內在狀態"
        self.assertTrue(selfstate.is_scheduled_promise_request(s))


class NoOverCaptureTest(unittest.TestCase):
    """at-me 綁定：不收使用者自己的計畫／第三方／泛談。"""

    def test_user_own_plan_not_captured(self):
        self.assertFalse(selfstate.is_scheduled_promise_request("我明天要向我老闆證明我有什麼不同"))

    def test_third_party_not_captured(self):
        self.assertFalse(selfstate.is_scheduled_promise_request("他向我證明了他有什麼不同"))

    def test_generic_talk_not_captured(self):
        self.assertFalse(selfstate.is_scheduled_promise_request("你覺得證明很重要嗎"))

    def test_bare_prove_without_at_me_not_captured(self):
        self.assertFalse(selfstate.is_scheduled_promise_request("30分鐘後證明一下這個定理"))


class ExactTimeClauseTest(unittest.TestCase):
    """兌現措辭：約定時刻 ≠ 此刻（截圖 10:06 卻說「現在是 10:05」）。"""

    def test_forbids_now_is_claim(self):
        c = persona._exact_time_clause("10:05")
        self.assertIn("不是此刻", c)
        self.assertIn("絕不要", c)
        self.assertIn("說好 10:05 的", c)

    def test_still_pins_exact_digits(self):
        c = persona._exact_time_clause("08:39")
        self.assertIn("一個都不能改", c)                     # §0.67 原防線不動
        self.assertIn("08:39", c)

    def test_promise_keep_user_carries_rule(self):
        u = persona.promise_keep_user("10:05", "〔此刻 10:06〕", promised="跟他說說我此刻有什麼不一樣（我的變化）")
        self.assertIn("不是此刻", u)


if __name__ == "__main__":
    unittest.main()
