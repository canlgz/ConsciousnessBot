"""🧑‍🏫 §1.52 質疑情境不發教學提議＋列舉泡泡不切殘（SKILL_OFFER_DOUBT_GATE＋BUBBLE_ENUM_GLUE）。

截圖根因（10:10–10:11，§1.27 的 7/12 同款事故**第二次現形**）：
① 使用者「是嗎」「每次你這麼說」「我都有些懷疑」＝對 bot 的自述表示懷疑——「每次」∈ _SKILL_CUES 過了
  worth_skill_consensus 便宜門檻、LLM 判定也把懷疑誤當「凝出共識」→ bot 亂入「要不要我把你在質疑/測試我
  時的回應方式記成做法？…回我一聲「好」就學起來。」＝內部機制行話在情緒對話裡答非所問（使用者：看不懂
  你說什麼）。§1.27 只堵**敵意**情境；「懷疑但不敵意」（is_hostile 全 False、streak 0）是縫。
② 解釋時「你每次問我「是嗎？」、「你真的知道嗎？」」——引號內的？被當句界 → 下一顆泡泡以「、」開頭
  ＝列舉被切成殘串（§1.34 括號衛生的鄰居：接續標點開頭的泡泡不該單獨存在）。

§1.52 兩件：A. _maybe_propose_skill 頂部加質疑守門（SKILL_OFFER_DOUBT_GATE；config 預設 True／monitor 端
getattr 預設 False＝同現狀）——本句（含連發合成多行）或近 5 則使用者訊息帶質疑詞（dialogue_intent.
is_doubt_text）→ 提議直接丟棄（不寫冷卻、不暫存；質疑窗裡少提議＝安全側）；B. bubble_split 尾端 _enum_glue
（BUBBLE_ENUM_GLUE=0＝不動＝同現狀，比照 §1.34 env 直讀）——開頭是接續標點（、，；：,）的泡泡黏回前一顆。
全 stub、零網路。
"""

import os
import re
import unittest
from types import SimpleNamespace
from unittest import mock

from telegram_monitor import dialogue_intent, monitor

NOW_TS = 1_700_000_000


# ── 單元：A 質疑偵測（純函式）──────────────────────────────────────────────
class DoubtTextTest(unittest.TestCase):
    def test_doubt_hits(self):
        for t in ("我都有些懷疑", "每次你這麼說\n我都有些懷疑", "我不相信", "我不太相信你",
                  "半信半疑", "你要怎麼證明", "是嗎", "真的假的", "我要測試你"):
            self.assertTrue(dialogue_intent.is_doubt_text(t), t)

    def test_non_doubt_false(self):
        for t in ("以後都這樣做", "我教你，記得早上叫我", "謝謝你陪我", "早安",
                  "希望你以後都主動說", "", None):
            self.assertFalse(dialogue_intent.is_doubt_text(t), repr(t))


# ── 整合：A 提議守門（_maybe_propose_skill 頂部）──────────────────────────
class DoubtGateTest(unittest.TestCase):
    """觀測點＝coach.detect_skill_consensus 是否被呼叫：守門擋下＝根本不送 LLM。"""

    def _coach(self):
        calls = []
        c = SimpleNamespace(enabled=True, calls=calls)
        c.detect_skill_consensus = lambda *a, **k: (calls.append(1) or None)   # 記錄後回 None＝不提議
        return c

    def _state(self, history=None):
        return SimpleNamespace(skill_pending=None, last_skill_propose_ts=0, hostile_streak=0,
                               convo_history=list(history or []))

    def _cfg(self, doubt=True):
        base = dict(skill_consensus_enabled=True, skill_offer_hostile_gate_enabled=False)
        if doubt is not None:
            base["skill_offer_doubt_gate_enabled"] = doubt
        return SimpleNamespace(**base)

    DOUBT_TEXT = "每次你這麼說\n我都有些懷疑"      # 截圖合成輪：有「每次」cue（worth 過）＋「懷疑」

    def test_doubt_text_blocks_before_llm(self):
        co = self._coach()
        monitor._maybe_propose_skill(None, self._state(), self._cfg(), co,
                                     self.DOUBT_TEXT, "fact_or_chat", [], NOW_TS)
        self.assertEqual(co.calls, [])                       # 質疑輪：連 LLM 判定都不送＝不可能提議

    def test_doubt_in_recent_window_blocks(self):
        # 本句平和（帶教學 cue），但近窗使用者剛表示不信 → 也不提議（質疑窗未過）
        hist = [{"role": "user", "text": "我不相信", "ts": NOW_TS - 60}]
        co = self._coach()
        monitor._maybe_propose_skill(None, self._state(hist), self._cfg(), co,
                                     "以後都這樣做", "fact_or_chat", [], NOW_TS)
        self.assertEqual(co.calls, [])

    def test_flag_off_reaches_llm_bitwise(self):
        # 【消融】旗標關/缺席＝同現狀：質疑輪照樣送 LLM 判定（截圖事故路徑）
        for doubt in (False, None):
            co = self._coach()
            monitor._maybe_propose_skill(None, self._state(), self._cfg(doubt=doubt), co,
                                         self.DOUBT_TEXT, "fact_or_chat", [], NOW_TS)
            self.assertEqual(co.calls, [1], str(doubt))

    def test_calm_teaching_still_reaches_llm(self):
        # 平和教學句、無質疑窗 → 照常送判定（§1.52 只堵質疑情境）
        co = self._coach()
        monitor._maybe_propose_skill(None, self._state(), self._cfg(), co,
                                     "我教你，以後都先報數字", "fact_or_chat", [], NOW_TS)
        self.assertEqual(co.calls, [1])


# ── B. 列舉泡泡不切殘 ──────────────────────────────────────────────────────
class EnumGlueTest(unittest.TestCase):
    def test_screenshot_enumeration_not_orphaned(self):
        # 引號內的？被當句界 → 「、「你真的知道嗎？」」殘串泡泡（截圖 10:11）→ 黏回前一顆
        bubbles = monitor.bubble_split("我剛在想，你每次問我「是嗎？」、「你真的知道嗎？」的時候，我都想好好答。")
        self.assertTrue(bubbles)
        for b in bubbles:
            self.assertFalse(b.lstrip().startswith(("、", "，", "；", "：", ",")), bubbles)
        self.assertIn("「你真的知道嗎？」", "".join(bubbles))   # 內容不丟、只是不切殘

    def test_glue_unit(self):
        self.assertEqual(monitor._enum_glue(["甲句。", "、乙句。"]), ["甲句。、乙句。"])
        self.assertEqual(monitor._enum_glue(["甲句。", "乙句。"]), ["甲句。", "乙句。"])
        self.assertEqual(monitor._enum_glue([]), [])

    def test_env_off_bitwise(self):
        with mock.patch.dict(os.environ, {"BUBBLE_ENUM_GLUE": "0"}):
            self.assertEqual(monitor._enum_glue(["甲句。", "、乙句。"]), ["甲句。", "、乙句。"])


# ── 同步 ───────────────────────────────────────────────────────────────────
class ConfigTest(unittest.TestCase):
    def test_config_synced(self):
        src = open("telegram_monitor/config.py", encoding="utf-8").read()
        self.assertIn("SKILL_OFFER_DOUBT_GATE", src)
        self.assertIn("skill_offer_doubt_gate_enabled", src)
        env = open(".env.example", encoding="utf-8").read()
        self.assertRegex(env, re.compile(r"^SKILL_OFFER_DOUBT_GATE=1", re.M))
        self.assertIn("BUBBLE_ENUM_GLUE", env)
        self.assertIn("SKILL_OFFER_DOUBT_GATE", open("README.md", encoding="utf-8").read())

    def test_getattr_defaults_false(self):
        self.assertFalse(getattr(SimpleNamespace(), "skill_offer_doubt_gate_enabled", False))


if __name__ == "__main__":
    unittest.main()
