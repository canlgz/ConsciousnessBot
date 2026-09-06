"""🤝 §0.92「20分鐘後告訴我/說說你有什麼不一樣（＋附貼圖）」排程承諾捕捉漏：被 self_change 內容路由搶走。

查驗根因（實跑確認）：intent.resolve 先判 self_aspect=='change'（『你有什麼不一樣』）在最前就 return self_change，
**永不走到下方的 scheduled_promise**（該 route 才會入帳、由生命迴圈到點兌現）→ 這三句只被當「現在就答蛻變」、
丟了「20分鐘後」的時間與約定 → 沒進 state.scheduled_promises → bot 只 LLM 空口答應、到點什麼都不做。
次因：『說說你有什麼不一樣』另外還過不了 is_scheduled_promise_request（無 at_me、動作不被認得）。

修（皆旗標預設開、關＝逐位元同現狀）：
1. intent：計時未來承諾**優先於** self_change/self_identity 內容路由（只在真是計時承諾時讓路；bare「你變了嗎」仍 self_change）。
2. selfstate：_self_change_tell_hit（溝通動詞＋bot 自身蛻變詞）補進 is_scheduled_promise_request 動作判定＋行為標籤。
"""

import os
import unittest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from telegram_monitor import selfstate, intent, temporal


class _Ref:
    followup_open = False

    def __getattr__(self, k):
        return None


class _Cfg:
    scheduled_promise_enabled = True
    sched_over_selfcontent_enabled = True
    promise_ledger_enabled = True

    def __getattr__(self, k):
        return None


THREE = [
    "20分鐘之後，告訴我你有什麼不一樣，還附上貼圖，好嗎",
    "20分鐘之後，說說你有什麼不一樣，還附上貼圖，好嗎",
    "20分鐘之後，告訴我你有什麼不一樣，而且要附上貼圖",
]


class SelfChangeTellHitTest(unittest.TestCase):
    def test_positive(self):
        for t in ["告訴我你有什麼不一樣", "說說你有什麼不一樣", "講講你哪裡不同了",
                  "分享你有什麼變化", "說說看自己有什麼不一樣", "跟我說你哪裡不一樣"]:
            self.assertTrue(selfstate._self_change_tell_hit(t), t)

    def test_negative_third_party_or_no_verb(self):
        for t in ["他有什麼不一樣", "告訴我他哪裡不一樣", "你有什麼不一樣",  # 無溝通動詞（純問句）
                  "說說今天天氣", "告訴我這題怎麼算"]:
            self.assertFalse(selfstate._self_change_tell_hit(t), t)

    def test_negative_possessive_noun_not_bot_self(self):
        # 🔍 §0.92 審查修：「你身邊/你朋友/你家裡…有什麼變化」＝所有格名詞、非 bot 自身蛻變 → 不收；
        # 「你不一樣的想法」（不一樣當定語）也不收（疑問結構須緊接你）。
        for t in ["分享你身邊有什麼變化", "說說你朋友有什麼不一樣", "說說你家裡有什麼變化",
                  "分享你不一樣的想法", "分享你不同的看法", "告訴我你覺得我有什麼不一樣"]:
            self.assertFalse(selfstate._self_change_tell_hit(t), t)

    def test_negative_adjectival_opinion(self):
        # 🔍 §0.92 審查修（MED）：蛻變詞後接「的＋意見類名詞」＝問差異**意見**、非自身蛻變 → 不收；
        # 「不一樣的地方」（非意見名詞）＝自身蛻變面向 → 仍收。
        for t in ["5分鐘後說說你有什麼不一樣的想法", "告訴我你有什麼不同的意見",
                  "分享你有什麼不同的計畫", "說說你有什麼不一樣的看法"]:
            self.assertFalse(selfstate._self_change_tell_hit(t), t)
        self.assertTrue(selfstate._self_change_tell_hit("告訴我你有什麼不一樣的地方"))

    def test_flag_off(self):
        os.environ["SCHED_SELF_CHANGE"] = "0"
        try:
            self.assertFalse(selfstate._self_change_tell_hit("說說你有什麼不一樣"))
        finally:
            os.environ.pop("SCHED_SELF_CHANGE", None)


class ScheduledDetectionTest(unittest.TestCase):
    def test_three_phrasings_are_scheduled(self):
        for t in THREE:
            self.assertTrue(selfstate.is_scheduled_promise_request(t), t)
            self.assertTrue(selfstate.promise_wants_sticker(t), t)
            self.assertEqual(selfstate.extract_promise_behavior(t), "跟他說說我此刻有什麼不一樣（我的變化）", t)

    def test_no_time_not_scheduled(self):
        # 無時間＝不是排程承諾（time gate）；純自我蛻變問句仍是問句
        for t in ["說說你有什麼不一樣", "告訴我你有什麼不一樣"]:
            self.assertFalse(selfstate.is_scheduled_promise_request(t), t)

    def test_third_party_not_scheduled(self):
        # 用「說說他…」隔離 §0.92：無 at_me、第三人稱（_self_change_tell_hit 要 你/妳/自己）→ 不收
        # （「告訴我他…」本就因既有「告訴我 X」＝at_me 動作被收，非本節新增，故不用它當反例）
        self.assertFalse(selfstate.is_scheduled_promise_request("20分鐘後說說他有什麼不一樣"))


class RoutingTest(unittest.TestCase):
    def _route(self, t, cfg=None):
        return intent.resolve(t, _Ref(), None, cfg=cfg or _Cfg()).kind

    def test_three_phrasings_route_scheduled(self):
        for t in THREE:
            self.assertEqual(self._route(t), "scheduled_promise", t)

    def test_bare_selfchange_stays_selfchange(self):
        for t in ["你有什麼不一樣", "你變了嗎", "你這版跟之前哪裡不同", "你哪裡不一樣了"]:
            self.assertEqual(self._route(t), "self_change", t)

    def test_bare_identity_stays_identity(self):
        self.assertEqual(self._route("你是誰"), "self_identity")

    def test_flag_off_routes_back_to_selfchange(self):
        class CfgOff(_Cfg):
            sched_over_selfcontent_enabled = False
        # 旗標關：即使是計時承諾，仍照現狀被 self_change 搶（逐位元同現狀）
        self.assertEqual(self._route(THREE[0], CfgOff()), "self_change")

    def test_verb_plus_clock_after_you_routes_scheduled_not_reaction(self):
        # 🔍 §0.92 審查修（HIGH）：讓路須**直接**去 scheduled，不落回中間 self_* 路由——否則「告訴我你8點有什麼不一樣」
        # 被 is_own_reaction_question 的「你…點…有什麼」誤配成 self_reaction_query（答非所問＋承諾沒入帳）。
        for t in ["告訴我你8點有什麼不一樣", "跟我說你9點有什麼變化", "回報你8點有什麼不同"]:
            self.assertEqual(self._route(t), "scheduled_promise", t)

    def test_past_ledger_question_still_ledger(self):
        # 過去質問（你忘了…要告訴我你有什麼不一樣嗎）＝對帳、非新約 → 仍讓給 promise_ledger（不被誤記成新承諾）
        self.assertEqual(self._route("你忘了20分鐘後要告訴我你有什麼不一樣嗎"), "promise_ledger")


class EndToEndTimeParseTest(unittest.TestCase):
    def test_time_parses_to_plus20(self):
        tz = ZoneInfo("Asia/Taipei")
        now = datetime(2026, 7, 8, 15, 0, 0, tzinfo=timezone.utc)
        for t in THREE:
            epochs = temporal.all_clock_epochs(t, now, tz)
            self.assertTrue(epochs, t)
            self.assertAlmostEqual((epochs[0] - now.timestamp()) / 60, 20, delta=1)


if __name__ == "__main__":
    unittest.main()
