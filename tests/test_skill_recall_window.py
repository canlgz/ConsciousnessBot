"""🧑‍🏫 §1.53 已學做法召回窗（SKILL_RECALL_WINDOW）：topic 型做法不再「幾乎永不觸發」。

使用者實測回報：「光只說學到 skill，都沒有真的能被觸發過」——結構性根因（實測，非猜）：
① recall 把**使用者當句**當 topic 比對源（monitor._skill_extra 傳 text）→ topic 型做法要「之後那句話
  **逐字包含**當初的主題標籤」才召回，且只看當句＝實務上幾乎永不觸發；
② 沒被召回＝不保鮮（touch 只在召回時發生）→ gain 0.6、半衰 21 天、門檻 0.5 ⇒ **~5.5 天靜默死亡**
  ＝死亡螺旋：越召不回越快死、死了更召不回；
③ 就算召回也只是無聲注入 prompt——使用者永遠看不到「做法真的套用了」的痕跡。

§1.53：A. 召回比對源放寬＝當句＋近 3 則使用者訊息（主題最近提過就算在場；§0.89 sit-cue 同源受惠）；
B. 召回命中印一行 [skill] log（can_lab console 可驗證）。旗標兩層分離：config 預設 True／monitor 端
getattr 預設 False＝只看當句＝逐位元同現狀。全 stub、零網路。
"""

import re
import unittest
from types import SimpleNamespace

from telegram_monitor import monitor, plasticity

NOW = 1_700_000_000.0
PROMPT = "回覆這個主題時，先給一個具體的小建議，再關心他的狀態"


# ── 純函式層：召回語意（比對源決定一切）────────────────────────────────────
class PureRecallTest(unittest.TestCase):
    def _engrams(self):
        e = []
        self.assertTrue(plasticity.capture_skill(e, "fact_or_chat", "睡眠", PROMPT, now_ts=NOW))
        return e

    def test_head_brittleness_pinned(self):
        # 釘住根因：當句不含主題標籤＝召不回（HEAD 把當句當唯一比對源＝幾乎永不觸發的直接原因）
        got = plasticity.recall_skills(self._engrams(), "fact_or_chat", "嗯，有什麼建議嗎",
                                       signals={"external": True}, now_ts=NOW + 3600)
        self.assertEqual(got, [])

    def test_windowed_src_recalls(self):
        # 比對源含近窗訊息（主題三句前提過）→ 召回（§1.53 呼叫端把窗接上後的語意）
        src = "嗯，有什麼建議嗎\n最近睡眠好差\n對啊"
        got = plasticity.recall_skills(self._engrams(), "fact_or_chat", src,
                                       signals={"external": True}, now_ts=NOW + 3600)
        self.assertEqual(got, [PROMPT])

    def test_silent_death_in_six_days_pinned(self):
        # 釘住死亡螺旋數學：沒被召回（無 touch）→ 6 天後權重衰到門檻下＝靜默失效
        e = self._engrams()
        src = "最近睡眠好差"
        self.assertEqual(plasticity.recall_skills(e, "fact_or_chat", src,
                                                  signals={"external": True}, now_ts=NOW + 4 * 86400), [PROMPT])
        self.assertEqual(plasticity.recall_skills(e, "fact_or_chat", src,
                                                  signals={"external": True}, now_ts=NOW + 6 * 86400), [])


# ── monitor._skill_extra：召回窗接線 ───────────────────────────────────────
class SkillExtraWindowTest(unittest.TestCase):
    def _state(self):
        e = []
        plasticity.capture_skill(e, "fact_or_chat", "睡眠", PROMPT, now_ts=NOW)
        hist = [{"role": "user", "text": "最近睡眠好差", "ts": NOW + 60},
                {"role": "model", "text": "嗯，辛苦了。", "ts": NOW + 70}]
        return SimpleNamespace(engrams=e, convo_history=hist, intent_reading={})

    def _cfg(self, win=True):
        base = dict(skill_recall_enabled=True, skill_situations_enabled=True,
                    skill_capability_gate_enabled=True, skill_use_refresh_enabled=False,
                    timezone="Asia/Taipei")
        if win is not None:
            base["skill_recall_window_enabled"] = win
        return SimpleNamespace(**base)

    def test_window_recalls_from_recent_context(self):
        # 截圖型情境：三句前談過睡眠、這句只說「有什麼建議嗎」→ 做法終於用得上
        out = monitor._skill_extra(self._state(), self._cfg(), "fact_or_chat", "嗯，有什麼建議嗎", NOW + 120)
        self.assertIn(PROMPT, out)

    def test_flag_off_bitwise_head_miss(self):
        # 【消融】旗標關/缺席＝只看當句＝召不回（同現狀＝使用者實測的「從沒觸發過」）
        for win in (False, None):
            out = monitor._skill_extra(self._state(), self._cfg(win=win),
                                       "fact_or_chat", "嗯，有什麼建議嗎", NOW + 120)
            self.assertEqual(out, "", str(win))

    def test_current_text_hit_works_both_ways(self):
        # 當句本來就含主題 → 開關都召回（窗只放寬、不收窄）
        for win in (True, False):
            out = monitor._skill_extra(self._state(), self._cfg(win=win),
                                       "fact_or_chat", "我睡眠又出問題了", NOW + 120)
            self.assertIn(PROMPT, out, str(win))

    def test_always_skill_fires_regardless(self):
        # always 常駐型不靠主題：本來就每個外部輪注入（「沒觸發過」的體感主要來自 topic 型）
        e = []
        plasticity.capture_skill(e, "", "", "回覆時語氣放輕、先接住情緒", trigger="always", now_ts=NOW)
        st = SimpleNamespace(engrams=e, convo_history=[], intent_reading={})
        for win in (True, False):
            out = monitor._skill_extra(st, self._cfg(win=win), "fact_or_chat", "早安", NOW + 120)
            self.assertIn("先接住情緒", out, str(win))


# ── 同步 ───────────────────────────────────────────────────────────────────
class ConfigTest(unittest.TestCase):
    def test_config_synced(self):
        src = open("telegram_monitor/config.py", encoding="utf-8").read()
        self.assertIn("SKILL_RECALL_WINDOW", src)
        self.assertIn("skill_recall_window_enabled", src)
        env = open(".env.example", encoding="utf-8").read()
        self.assertRegex(env, re.compile(r"^SKILL_RECALL_WINDOW=1", re.M))
        self.assertIn("SKILL_RECALL_WINDOW", open("README.md", encoding="utf-8").read())

    def test_getattr_defaults_false(self):
        self.assertFalse(getattr(SimpleNamespace(), "skill_recall_window_enabled", False))


if __name__ == "__main__":
    unittest.main()
