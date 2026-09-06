"""🕐 §1.84 主動出聲也要有「今天記寫」接地（WRITE_CLAIM_PROACTIVE）。

截圖 09:37（🫧 主動訊息）：「我剛剛看了一下，**你今天早上又寫了「閱讀｜讀誦經書」這件事。**」
＋「看到你又用貼圖幫自己加油打氣」——使用者當下回報：**今早根本還沒寫**。

根因＝結構縫（不是詞表、不是 prompt）：§1.60 的記寫宣稱守門
 ① 只在 handle_message 裡 arm（互動輪限定）；
 ② 判定塊又寫在 _say 的**互動限定分支**內（`if not prefix and state is None:`）
而所有主動出聲一律走 prefix/state ⇒ **整條主動路徑從來沒有這道守門**。
本檔釘住：主動分支現在也會被換成事實句，且與 §1.60 用同一支 ground／同一支守門（不可能打架）。
全 stub、零網路。
"""

import re
import unittest
from types import SimpleNamespace

from telegram_monitor import monitor, volition

CLAIM = "我剛剛看了一下，你今天早上又寫了「閱讀｜讀誦經書」這件事。看到你又用貼圖幫自己加油打氣。"
GROUND_NONE_TODAY = {"today_count": 0, "last_label": "昨天 18:02"}
GROUND_HAS_TODAY = {"today_count": 2, "last_label": "今天 07:15"}


class Cl:
    def __init__(self):
        self.sent, self.dry_run = [], False

    def send(self, t):
        self.sent.append(t)
        return True


class GuardUnitTest(unittest.TestCase):
    def test_claim_replaced_when_no_writing_today(self):
        out, ch = monitor._write_claim_fix(CLAIM, GROUND_NONE_TODAY)
        self.assertTrue(ch)
        self.assertNotIn("你今天早上又寫了", out)
        self.assertIn("昨天 18:02", out)                    # 換成程式算的事實
        self.assertIn("今天到現在還沒有新的記寫", out)

    def test_untouched_when_wrote_today(self):
        self.assertEqual(monitor._write_claim_fix(CLAIM, GROUND_HAS_TODAY), (CLAIM, False))

    def test_negative_sentence_untouched(self):
        t = "你今天還沒有記寫喔。"
        self.assertEqual(monitor._write_claim_fix(t, GROUND_NONE_TODAY), (t, False))


class SayProactiveBranchTest(unittest.TestCase):
    """核心：主動分支（prefix/state）以前完全沒有這道守門。"""

    def setUp(self):
        monitor._TURN.clear()
        monitor._TURN["bubbles"] = None
        monitor._SENT_RECENT.clear()

    def _say_proactive(self, text, ground=None):
        if ground is not None:
            monitor._TURN["write_claim_ground_proactive"] = ground
        cl = Cl()
        monitor._say(cl, text, prefix="🫧 ", state=SimpleNamespace(convo_history=[]))
        return "".join(cl.sent)

    def test_proactive_claim_now_guarded(self):
        out = self._say_proactive(CLAIM, GROUND_NONE_TODAY)
        self.assertNotIn("你今天早上又寫了", out)            # 截圖那句不會再送出去
        self.assertIn("還沒有新的記寫", out)

    def test_not_armed_is_bitwise(self):
        out = self._say_proactive(CLAIM)                    # 旗標關＝沒 arm＝原行為（釘住現狀）
        self.assertIn("你今天早上又寫了", out)

    def test_has_writing_today_passes_through(self):
        out = self._say_proactive(CLAIM, GROUND_HAS_TODAY)
        self.assertIn("你今天早上又寫了", out)               # 今天真的有寫＝不誤動

    def test_interactive_branch_still_uses_its_own_key(self):
        # §1.60 互動分支讀的是 write_claim_ground；兩把鑰匙互不干擾（各自 arm、各自只管自己那條路）
        monitor._TURN["write_claim_ground"] = GROUND_NONE_TODAY
        cl = Cl()
        monitor._say(cl, CLAIM)
        self.assertNotIn("你今天早上又寫了", "".join(cl.sent))


class ReachOutRuleTest(unittest.TestCase):
    def test_excerpt_carries_no_today_warning(self):
        r = volition.reach_out_diverse_rule("閱讀｜讀誦經書", 0, excerpt="今天讀到金剛經")
        self.assertIn("不保證是今天", r)
        self.assertIn("絕不要", r)
        self.assertIn("今天早上", r)                         # 點名禁用的宣稱形

    def test_no_excerpt_no_warning(self):
        r = volition.reach_out_diverse_rule("閱讀｜讀誦經書", 0)
        self.assertNotIn("不保證是今天", r)


class ConfigTest(unittest.TestCase):
    def test_config_synced(self):
        src = open("telegram_monitor/config.py", encoding="utf-8").read()
        self.assertIn("WRITE_CLAIM_PROACTIVE", src)
        self.assertIn("write_claim_proactive_enabled", src)
        env = open(".env.example", encoding="utf-8").read()
        self.assertRegex(env, re.compile(r"^WRITE_CLAIM_PROACTIVE=1", re.M))
        self.assertIn("WRITE_CLAIM_PROACTIVE", open("README.md", encoding="utf-8").read())

    def test_getattr_defaults_false(self):
        self.assertFalse(getattr(SimpleNamespace(), "write_claim_proactive_enabled", False))


if __name__ == "__main__":
    unittest.main()
