"""🤝🛡️ §1.09 空口答應守門與捕捉層**脫鉤**：結構性判準（時間樣式＋指向 bot 的請求），不看動詞表。

為什麼：§0.92「說說你有什麼不一樣」→ §0.83「說明你的內心運作」→ §1.08「向我證明你有什麼地方不同」，
同一類 bug 三次——捕捉靠**窮舉動詞表**，每個新說法都會漏；而守門 `looks_like_timed_request` **讀同一批表**，
於是漏收時最後一道防線也一起瞎掉，LLM 便自由發揮「好，我記下來了」＋自己編一個時刻。
本層讓守門獨立：動詞表再漏，只要句子結構是「時間＋指向 bot 的請求」，守則照掛（bot 誠實說「請把時間講清楚」）。
"""

import os
import unittest
from unittest.mock import patch

from telegram_monitor import persona, selfstate


class StructuralGuardCatchesUnknownVerbsTest(unittest.TestCase):
    """核心價值：動詞表**完全沒有**的新說法，守門仍接得住。"""

    def test_verbs_absent_but_guard_fires(self):
        for s in ("30分鐘後，你再用你的方式讓我知道你哪裡不同",
                  "20分鐘後，你要記得回來找我",
                  "半小時後請你再看一下這件事",
                  "40分鐘後，你再秀一手給我瞧瞧"):
            self.assertTrue(selfstate.looks_like_timed_request(s), s)
            # 且確實**不是**靠動詞表（證明結構性分支在起作用）
            self.assertTrue(selfstate._timed_request_structural(s.replace(" ", "")), s)

    def test_screenshot_sentence(self):
        self.assertTrue(selfstate.looks_like_timed_request("30分鐘之後，你再向我證明你有什麼地方不同"))

    def test_at_me_alone_is_enough(self):
        self.assertTrue(selfstate._timed_request_structural("10分鐘後跟我說一下"))

    def test_second_person_plus_ask_frame(self):
        self.assertTrue(selfstate._timed_request_structural("半小時後請你再看一下這件事"))


class StructuralGuardNegativesTest(unittest.TestCase):
    def test_user_own_plan(self):
        self.assertFalse(selfstate.looks_like_timed_request("我30分鐘後要去開會"))

    def test_third_party_will(self):
        # 🤝 §1.09 也修既有過度觸發：舊版動詞表分支「叫我」誤中、該分支從無第三方守門
        self.assertFalse(selfstate.looks_like_timed_request("他30分鐘後會叫我"))
        self.assertFalse(selfstate.looks_like_timed_request("他10分鐘後會提醒我"))

    def test_third_party_recipient(self):
        self.assertFalse(selfstate.looks_like_timed_request("30分鐘後打電話給我媽"))

    def test_mixed_sentence_still_counts_as_to_bot(self):
        self.assertTrue(selfstate.looks_like_timed_request("他等一下會叫我，你30分鐘後也提醒我一下"))

    def test_past_or_query(self):
        self.assertFalse(selfstate.looks_like_timed_request("你剛剛有提醒我嗎"))

    def test_data_question_no_bot_target(self):
        self.assertFalse(selfstate.looks_like_timed_request("10分鐘後的會議在哪"))

    def test_statement_with_my_possessive(self):
        # 「我的」刻意不在結構性 at-me 表（敘述、非請求）
        self.assertFalse(selfstate.looks_like_timed_request("30分鐘後我的會議開始"))

    def test_no_time_pattern(self):
        self.assertFalse(selfstate.looks_like_timed_request("你喜歡哪一張貼圖"))

    def test_too_short(self):
        self.assertFalse(selfstate.looks_like_timed_request("30分後"))


class FlagOffTest(unittest.TestCase):
    def test_structural_branch_disabled(self):
        with patch.dict(os.environ, {"PROMISE_GUARD_STRUCTURAL": "0"}):
            # 動詞表沒有的說法 → 退回舊行為（漏掉）
            self.assertFalse(selfstate._timed_request_structural("30分鐘後，你再用你的方式讓我知道你哪裡不同"))
            self.assertFalse(selfstate.looks_like_timed_request("30分鐘後，你再用你的方式讓我知道你哪裡不同"))
            # 動詞表有的仍照舊命中
            self.assertTrue(selfstate.looks_like_timed_request("30分鐘後叫我起床"))

    def test_third_party_guard_survives_flag_off(self):
        # 第三方守門是純收緊，與結構性旗標無關
        with patch.dict(os.environ, {"PROMISE_GUARD_STRUCTURAL": "0"}):
            self.assertFalse(selfstate.looks_like_timed_request("他30分鐘後會叫我"))


class GuardTextTest(unittest.TestCase):
    def test_guard_text_generalised(self):
        g = persona.PROMISE_ACK_GUARD
        self.assertIn("到某個時間主動為他做某件事", g)     # 不再只講「叫醒/提醒」
        self.assertIn("絕對不要答應", g)
        self.assertIn("忽略此提示", g)                     # 誤觸逃生閥仍在


if __name__ == "__main__":
    unittest.main()
