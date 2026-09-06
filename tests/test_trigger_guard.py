"""🫧 精準化「報內在狀態」觸發：順口帶感覺字眼的陳述句（如「卻能感受到」）不再誤觸發 bodystate 報告，
但真的問句（你現在感覺如何／你對X有什麼感覺）照常進 self_state。旗標關＝逐位元同現狀。"""

import unittest
from types import SimpleNamespace

from telegram_monitor import selfstate, selfref, intent


def _ref():
    return SimpleNamespace(revisited=None, followup_open=False)


def _cfg(precise):
    return SimpleNamespace(state_trigger_precise=precise)


class GenuineStateQueryTest(unittest.TestCase):
    def test_feeling_statements_are_not_queries(self):
        for t in ("卻能感受到", "是你看不到的、卻能感受到",
                  "不是，是改造你的那些 code，是你看不到的、卻能感受到",
                  "我能感受到你的努力", "因為感受到的東西很多"):
            self.assertFalse(selfstate.is_genuine_state_query(t), t)

    def test_real_state_questions_pass(self):
        for t in ("你現在感覺如何", "你還好嗎", "你好嗎", "你最近怎樣", "你狀態如何",
                  "有沒有感覺", "你對讀誦經書那段有什麼感覺"):
            self.assertTrue(selfstate.is_genuine_state_query(t), t)

    def test_empty(self):
        self.assertFalse(selfstate.is_genuine_state_query(""))
        self.assertFalse(selfstate.is_genuine_state_query(None))


class SelfAspectGateTest(unittest.TestCase):
    def test_precise_drops_feeling_statement(self):
        self.assertIsNone(selfref.self_aspect("卻能感受到", _cfg(True)))      # 收緊後不再回 state
        self.assertEqual(selfref.self_aspect("卻能感受到", _cfg(False)), "state")  # 關＝舊行為
        self.assertEqual(selfref.self_aspect("卻能感受到"), "state")              # cfg=None＝舊行為（byte-identical）

    def test_precise_keeps_real_question(self):
        self.assertEqual(selfref.self_aspect("你現在感覺如何", _cfg(True)), "state")
        self.assertEqual(selfref.self_aspect("你對讀誦經書那段有什麼感覺", _cfg(True)), "state")

    def test_is_about_self_stays_broad(self):
        # 自我在場窗仍用寬門檻（不對稱設計）：談到 bot＋帶感覺字眼 → 仍開窗（只是不報 state）
        self.assertTrue(selfref.is_about_self("卻能感受到"))


class IntentResolveGateTest(unittest.TestCase):
    def test_feeling_statement_not_self_state_when_precise(self):
        self.assertNotEqual(intent.resolve("卻能感受到", _ref(), cfg=_cfg(True)).kind, "self_state")

    def test_feeling_statement_self_state_when_off(self):
        self.assertEqual(intent.resolve("卻能感受到", _ref()).kind, "self_state")              # 旗標關＝現狀
        self.assertEqual(intent.resolve("卻能感受到", _ref(), cfg=_cfg(False)).kind, "self_state")

    def test_real_question_self_state_either_way(self):
        self.assertEqual(intent.resolve("你現在感覺如何", _ref(), cfg=_cfg(True)).kind, "self_state")
        self.assertEqual(intent.resolve("你現在感覺如何", _ref()).kind, "self_state")


if __name__ == "__main__":
    unittest.main()
