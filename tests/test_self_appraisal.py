"""🪞📊 評斷我（使用者）：「我算早起嗎／我是不是很懶／我這樣算正常嗎」＝請 bot 拿我的節奏＋此刻時間做
grounded 判斷，**不是**去列我的記寫。截圖根因：這句漏到 fact_or_chat → function-calling 誤抓
records_in_time_range 吐「今天那段你沒有記寫」、答非所問還重複犯。旗標關＝逐位元同現狀（落回 fact_or_chat）。"""

import unittest
from types import SimpleNamespace

from telegram_monitor import selfstate, intent


def _ref():
    return SimpleNamespace(revisited=None, followup_open=False)


def _cfg(on):
    return SimpleNamespace(self_appraisal_enabled=on)


class IsSelfAppraisalTest(unittest.TestCase):
    def test_hits(self):
        # 截圖核心句＋作息/勤惰/量/頻率/特質/常態各框架，含強框架與「面向詞＋口語語助詞」弱框架
        for t in ("我算早起嗎", "我早起嗎", "我算晚睡嗎", "我是不是都很晚睡", "我太晚睡了嗎",
                  "我算勤勞嗎", "我是不是很懶", "我會不會太懶散", "我會不會記太少", "我記太少了嗎",
                  "我是不是太常記了", "我很少寫對吧", "我算龜毛嗎", "我是不是想太多",
                  "我這樣算正常嗎", "我這樣正不正常", "我作息正常嗎", "我規律嗎",
                  "我算早起喔", "我算早起啦", "我超懶的吧"):
            self.assertTrue(selfstate.is_self_appraisal_question(t), t)

    def test_misses(self):
        # 計算動作 / 收尾離開 / 明確資料問句 / 含「你」(→other_mind) / 純陳述非問句 / 無面向詞
        for t in ("算了", "算了吧", "我算一下", "幫我算", "我來算算看",
                  "你好嗎", "你覺得我算早起嗎", "你覺得我勤勞嗎",
                  "我是不是該走了", "我先去忙", "我該睡了", "我先去睡了",
                  "我今天記了幾筆", "我這個月寫了多少筆", "我多久沒寫了", "上次記寫是什麼時候",
                  "我今天早上寫了什麼", "幫我列出最近的記寫", "現在幾點", "我餓了", "我想睡覺",
                  "我很懶", "我覺得我很正常", "晚安", ""):
            self.assertFalse(selfstate.is_self_appraisal_question(t), t)


class RouteSelfAppraisalTest(unittest.TestCase):
    def test_routes_when_enabled(self):
        self.assertEqual(intent.resolve("我算早起嗎", _ref(), cfg=_cfg(True)).kind, "self_appraisal")
        self.assertEqual(intent.resolve("我是不是很懶", _ref(), cfg=_cfg(True)).kind, "self_appraisal")

    def test_byte_identical_when_off(self):
        # 無 cfg、或旗標關 → 仍落 fact_or_chat（與現狀逐位元相同、可回歸對照）
        self.assertEqual(intent.resolve("我算早起嗎", _ref()).kind, "fact_or_chat")
        self.assertEqual(intent.resolve("我算早起嗎", _ref(), cfg=_cfg(False)).kind, "fact_or_chat")

    def test_does_not_hijack_higher_priority(self):
        # 真問 bot 狀態仍走 self_state；含「你」的問句不被自評搶（維持現狀路由）
        self.assertEqual(intent.resolve("你現在感覺如何", _ref(), cfg=_cfg(True)).kind, "self_state")
        self.assertNotEqual(intent.resolve("你覺得我算早起嗎", _ref(), cfg=_cfg(True)).kind, "self_appraisal")

    def test_data_question_not_stolen(self):
        # 帶明確資料詞的問句即使旗標開也不進自評（留給 function-calling）
        self.assertNotEqual(intent.resolve("我多久沒寫了", _ref(), cfg=_cfg(True)).kind, "self_appraisal")


if __name__ == "__main__":
    unittest.main()
