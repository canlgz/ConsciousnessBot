"""🫧 展開/釐清「你說的那點」：指向 bot 自己前文的展開/釐清問句 → 走純對話接著前文講或反問確認意圖，
不誤撈資料清單（截圖：問「你說的共同性」卻去列〔情緒困擾〕）。旗標關＝逐位元同現狀。"""

import unittest
from types import SimpleNamespace

from telegram_monitor import selfstate, intent


def _ref():
    return SimpleNamespace(revisited=None, followup_open=False)


def _cfg(on):
    return SimpleNamespace(intent_confirm_enabled=on)


class IsElaboratePriorTest(unittest.TestCase):
    def test_hits(self):
        for t in ("說說看你說的共同性是什麼？", "你剛說的那個是什麼意思",
                  "你提到的那個展開說說", "你的意思是什麼", "你不是說那個嗎，再說清楚一點"):
            self.assertTrue(selfstate.is_elaborate_prior(t), t)

    def test_misses(self):
        for t in ("你說的對", "學校設備那幾筆", "你現在感覺如何",
                  "你說的學校設備有幾筆", "嗯好", ""):
            self.assertFalse(selfstate.is_elaborate_prior(t), t)


class RouteElaboratePriorTest(unittest.TestCase):
    def test_routes_when_enabled(self):
        self.assertEqual(intent.resolve("說說看你說的共同性是什麼？", _ref(), cfg=_cfg(True)).kind,
                         "elaborate_prior")

    def test_byte_identical_when_off(self):
        self.assertEqual(intent.resolve("說說看你說的共同性是什麼？", _ref()).kind, "fact_or_chat")
        self.assertEqual(intent.resolve("說說看你說的共同性是什麼？", _ref(), cfg=_cfg(False)).kind, "fact_or_chat")

    def test_does_not_hijack_real_state_question(self):
        # 真問狀態仍走 self_state、不被 elaborate_prior 搶（即使旗標開）
        self.assertEqual(intent.resolve("你現在感覺如何", _ref(), cfg=_cfg(True)).kind, "self_state")


if __name__ == "__main__":
    unittest.main()
