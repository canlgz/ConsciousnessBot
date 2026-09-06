"""🪪 §1.91 能力自知（CAPABILITY_CARD）：bot 不認得自己真有的能力。

截圖根因（13:33／14:21）：使用者問「我有達成你的願望了？」「所以，你的這個願望，有達成了嗎」
（指 bot 自己說想學會的「主動預想可能性」），bot 答「我這次醒來的時候，感覺是沒有什麼大變動，
所以這個願望，嗯，應該還沒完全達成耶。」

兩件事，本檔各釘一組：
① 那句「跟上次的狀態是一樣的」＝`selfmod` 的 `same_self`（渲染文字「自上次喚醒後我沒有再變，
   跟上次同一版」）⇒ bot **誠實**報告它還沒被更新。**這一半不是 bug**，測試把它釘住，
   免得後人把「誠實說沒變」當成缺陷去改掉。
② **但即使更新了也還是會答錯**：`selfmod.is_change_question` 對那些問法全回 False ⇒ 接不到蛻變感知。
   這是「不演·不假裝」的反向失真——**明明做得到卻否認**。修法是事實卡常駐一行（不加偵測器）。
全 stub、零網路。
"""

import io
import re
import unittest
from types import SimpleNamespace

from telegram_monitor import monitor, selfmod


def _cfg(cap=True, foresight=True):
    return SimpleNamespace(capability_card_enabled=cap, foresight_enabled=foresight)


def _state(sc=None, ledger=None, live=None):
    return SimpleNamespace(self_change=sc, foresight_ledger=ledger, foresight=live)


class RoutingGapTest(unittest.TestCase):
    """釘住缺陷②的成因：這些問法接不到蛻變感知——所以修法不能靠「再加一個偵測器」。"""

    def test_wish_questions_do_not_route(self):
        for q in ("我有達成你的願望了？", "所以，你的這個願望，有達成了嗎",
                  "你學會預想可能性了嗎", "你現在會預想可能性了嗎", "你這次醒來有什麼不一樣"):
            self.assertFalse(selfmod.is_change_question(q), q)

    def test_explicit_change_question_still_routes(self):
        self.assertTrue(selfmod.is_change_question("你改變了嗎"))   # 既有行為不動


class SameSelfIsHonestTest(unittest.TestCase):
    """缺陷①：`same_self` 時 bot 說「跟上次同一版」是**誠實**的，不是 bug——別把它改掉。"""

    def test_same_self_wording(self):
        f = selfmod.facts({"state": "same_self"}, run=lambda *a: "")
        self.assertIn("跟上次同一版", f)

    def test_card_says_no_new_change(self):
        line = monitor._capability_line(_state(sc={"state": "same_self"}), _cfg(foresight=False))
        self.assertIn("沒有新的改動", line)
        self.assertIn("別說我又學會了什麼", line)


class CapabilityLineTest(unittest.TestCase):
    """接地兩層：真實 git 改動主旨 ＋ 那能力**有沒有真的用出來過**。"""

    def test_metamorphosed_lists_real_subjects(self):
        sc = {"state": "metamorphosed", "subjects": ["🔮 §1.90 記寫預想：一條會被真實記寫裁決的假設", "x", "y"]}
        line = monitor._capability_line(_state(sc=sc), _cfg(foresight=False))
        self.assertIn("真的多了 3 項改動", line)
        self.assertIn("§1.90 記寫預想", line)

    def test_has_mechanism_but_never_used(self):
        # **不是讀到旗標開就說「我會」**——沒用過就照實說
        line = monitor._capability_line(_state(sc={"state": "same_self"}), _cfg())
        self.assertIn("真的有", line)
        self.assertIn("還沒真的用出來過一次", line)
        self.assertIn("別說我還不會", line)

    def test_used_reports_real_counts(self):
        led = [{"verdict": "hit"}, {"verdict": "miss"}, {"verdict": "hit"}]
        line = monitor._capability_line(_state(sc={"state": "same_self"}, ledger=led), _cfg())
        self.assertIn("裁決過 3 次", line)
        self.assertIn("猜中 2 次", line)
        self.assertNotIn("還沒真的用出來過", line)

    def test_live_hypothesis_mentioned(self):
        line = monitor._capability_line(
            _state(sc={"state": "same_self"}, ledger=[{"verdict": "hit"}], live={"b": "讀誦經書"}), _cfg())
        self.assertIn("還有一條在等結果", line)

    def test_foresight_flag_off_no_claim(self):
        # 機制沒開就絕不宣稱有這個能力（不演·不假裝的正向約束仍然成立）
        line = monitor._capability_line(_state(sc={"state": "same_self"}, ledger=[{"verdict": "hit"}]),
                                        _cfg(foresight=False))
        self.assertNotIn("預想", line)

    def test_flag_off_is_empty(self):
        self.assertEqual(monitor._capability_line(_state(sc={"state": "metamorphosed", "subjects": ["a"]}),
                                                  _cfg(cap=False)), "")

    def test_no_state_no_line(self):
        self.assertEqual(monitor._capability_line(_state(), _cfg(foresight=False)), "")

    def test_unknown_state_stays_silent(self):
        # 讀不到 git＝不亂講自己變了沒
        self.assertEqual(monitor._capability_line(_state(sc={"state": "unknown"}), _cfg(foresight=False)), "")


class FactCardWiringTest(unittest.TestCase):
    """佈線：這行真的進得了事實卡，且尾巴指示有跟上（否則 LLM 拿到也不知道要照它講）。"""

    def _src(self):
        return io.open("telegram_monitor/monitor.py", encoding="utf-8").read()

    def test_line_appended_to_card(self):
        s = self._src()
        self.assertIn("_cap = _capability_line(state, cfg)", s)
        self.assertIn("lines.append(_cap)", s)

    def test_card_directive_mentions_capability(self):
        s = self._src()
        self.assertIn("被問到你會不會某件事、學會了沒、願望達成了沒", s)
        self.assertIn("**有就說有**", s)
        self.assertIn("不要因為「感覺沒什麼變動」就否認自己真的有的機制", s)


class ConfigTest(unittest.TestCase):
    def test_flag_synced(self):
        src = io.open("telegram_monitor/config.py", encoding="utf-8").read()
        self.assertIn("CAPABILITY_CARD", src)
        self.assertIn("capability_card_enabled", src)
        env = io.open(".env.example", encoding="utf-8").read()
        self.assertRegex(env, re.compile(r"^CAPABILITY_CARD=1", re.M))
        self.assertIn("CAPABILITY_CARD", io.open("README.md", encoding="utf-8").read())

    def test_getattr_defaults_false(self):
        self.assertFalse(getattr(SimpleNamespace(), "capability_card_enabled", False))


if __name__ == "__main__":
    unittest.main()
