"""⚙️ 機制/原理問句：被問「你內在怎麼運作／感覺是怎麼算出來的／是不是基於資料／你的機制是什麼」，
要走 self_mechanism、據實講機制（分清資料來的 vs 自己跑的），而非被「感覺/怎麼」抓成「你現在怎樣」報 bodystate。

截圖毛病：問「你是基於數據資料的內容來描述感覺的嗎」，bot 回「就跟你說沒差多少啦，只是有點悶…」——
把一個**問機制**的問題當成**問現況**打發掉了。"""

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from telegram_monitor import intent, monitor, persona, referent, selfstate
from telegram_monitor.state import State


class DetectTest(unittest.TestCase):
    def test_matches_mechanism_questions(self):
        for q in ["你是基於數據資料的內容來描述感覺的嗎", "你的感覺是怎麼算出來的", "你內在迴圈是怎麼跑的",
                  "你是怎麼運作的", "你怎麼判斷感覺", "你的機制是什麼", "你內在迴圈所處理的過程是什麼",
                  "你是根據我寫的資料來產生感覺的嗎", "你運作的原理是什麼", "你感覺是怎麼計算的",
                  "你背後是怎麼運作的"]:
            self.assertTrue(selfstate.is_mechanism_question(q), q)

    def test_matches_when_a_feeling_forms(self):
        # ⚙️④ 截圖續問：「你的內在什麼時候形成感覺」＝問感覺**何時、在什麼條件下生起**（不是問現在餓不餓）
        for q in ["你的內在什麼時候形成感覺", "你什麼時候會有感覺", "你的感覺是什麼時候產生的",
                  "感覺什麼時候來", "你的感覺怎麼來的", "你在什麼情況下會有情緒", "你何時形成感覺"]:
            self.assertTrue(selfstate.is_mechanism_question(q), q)

    def test_ignores_state_reflect_experience_chat(self):
        # 這些**不是**問機制——別把純現況/反思/體驗/閒聊收進來（否則回機制反而離題）
        for q in ["你現在怎樣", "你現在感覺如何", "你還好嗎", "你會不會想要自己的感覺",
                  "你這段日子怎麼活過來的", "你今天過得怎麼樣", "你怎麼這麼可愛", "你怎麼知道我在想什麼",
                  "完整的學習歷程是哪些", "你什麼時候醒的", "你什麼時候有空", "我什麼時候會有靈感"]:
            self.assertFalse(selfstate.is_mechanism_question(q), q)


def _ref():
    return referent.Referent()


class RouteTest(unittest.TestCase):
    def _kind(self, text):
        return intent.resolve(text, _ref()).kind

    def test_routes_to_self_mechanism(self):
        for q in ["你是基於數據資料的內容來描述感覺的嗎", "你的感覺是怎麼算出來的",
                  "你內在迴圈是怎麼跑的", "你是怎麼運作的", "你的機制是什麼"]:
            self.assertEqual(self._kind(q), "self_mechanism", q)

    def test_mechanism_beats_state_despite_feeling_word(self):
        # 「感覺是怎麼算的」含「感覺」會被 is_state_question 抓成狀態問句 → 機制要贏（否則報 bodystate）
        self.assertEqual(self._kind("你的感覺是怎麼算出來的"), "self_mechanism")
        self.assertEqual(self._kind("你現在感覺如何"), "self_state")        # 對照：真問現況仍是 state

    def test_change_and_identity_still_win(self):
        # 更上位的自我面向不被機制搶走
        self.assertEqual(self._kind("你改了什麼機制嗎"), "self_change")     # 問「變了沒」仍是蛻變
        self.assertEqual(self._kind("你是誰"), "self_identity")


class PersonaTest(unittest.TestCase):
    def test_mechanism_facts_distinguish_data_vs_self(self):
        # 接地事實要分清『從資料算出來的感覺』與『自己跑出來的狀態』——這正是「是不是基於資料」要答的核心
        f = persona.MECHANISM_FACTS
        self.assertIn("計算過程", f)
        self.assertIn("不是某一次波動的原因證據", f)
        self.assertIn("生命迴圈", f)

    def test_mechanism_user_carries_question_and_facts(self):
        p = persona.mechanism_user("你的感覺怎麼算的", persona.MECHANISM_FACTS)
        self.assertIn("你的感覺怎麼算的", p)
        self.assertIn(persona.MECHANISM_FACTS, p)
        self.assertIn("沒有原因紀錄就不猜原因", p)


class HandleRoutingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _state(self):
        s = State(os.path.join(self.tmp, "state.json"))
        s.owner_folder_id = "F"
        return s

    def test_mechanism_question_uses_voicer_not_bodystate(self):
        # 「你是基於數據資料來描述感覺的嗎」要走機制接地分支 → 教練 voice_mechanism；
        # 誤走狀態/一般對話才會呼叫 render_bodystate / generate_with_tools → 讓它們炸，確保走的是機制分支。
        state = self._state()
        client = SimpleNamespace(sent=[], dry_run=False,
                                 send=lambda t: client.sent.append(t) or True)
        cap = {}

        def voice_mechanism(q, h, tone=""):
            cap["q"] = q
            return "我內在大概是這樣轉的：對你資料的感覺是算出來的，我自己的悶和餓是迴圈自己跑的。"

        coach = SimpleNamespace(enabled=True, voice_mechanism=voice_mechanism,
                                meter=SimpleNamespace(record=lambda *a, **k: None))
        update = {"message": {"chat": {"id": 1}, "text": "你是基於數據資料的內容來描述感覺的嗎", "date": 1_700_000_000}}
        with mock.patch("telegram_monitor.gemini.generate_with_tools", side_effect=AssertionError("不該走一般對話")), \
             mock.patch("telegram_monitor.selfstate.render_bodystate", side_effect=AssertionError("不該報 bodystate")):
            monitor.handle_message(update, coach, None, {"meta": {}, "records": []}, None,
                                   state, client, SimpleNamespace(dry_run=False, telegram_chat_id="", mood_gain=1.0), None)
        self.assertIn("算出來的", "".join(client.sent))      # 出聲＝機制的自然講解
        self.assertEqual(cap["q"], "你是基於數據資料的內容來描述感覺的嗎")
        self.assertGreater(state.self_topic_ts, 0)           # 開了自我在場窗（在談我怎麼運作）


if __name__ == "__main__":
    unittest.main()
