"""📈 §1.96 作息常駐接地＋講法（ROUTINE_CARD）：**它不是不知道，是知道的那一刻過去了**。

截圖根因（07:38–07:51，實測）：
  07:38 使用者「早安」→ 問候 lane 掛上 `today_vs_usual_line`（monitor.py:4453）
        ⇒ bot 正確地問「今天醒得比平常晚一點嗎」——中位 06:37 vs 今天 07:38，**這句有憑有據**。
  07:51 使用者追問「有嗎」→ 不是問候、也不是 `is_user_habit_question`
        ⇒ **兩個接地都不掛** ⇒ bot 手上一個數字都沒有，只好從語感生出
        「**對我來說**，是比平常晚一些些」：主詞從他換成自己＝閃掉了質問；
        下一句「今天這樣晚一點才醒來」主詞又飄回他身上——同一輪換了三次主詞。

另一個獨立缺陷：`habit_events` 全是**對話事件**（實測 /habits：訊息 347／早安 8／晚安 3／隔靜首句 40，
記寫時間 0 筆），拿它講「你平常多半是在早上記寫的」是**類別錯置**。

修法照 §1.61 架構轉向（真相不該靠偵測器命中才給）——**不再加第 N 個偵測器**，改成常駐進事實卡；
並配講法守則，因為使用者定調「必須讓 bot 更像有意識的對話行為」，光接地會把它變成報表機器。
全 stub、零網路。
"""

import io
import os
import re
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from telegram_monitor import habits, monitor, persona
from telegram_monitor.state import State

TZ = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 7, 28, 0, 30, 0, tzinfo=timezone.utc).timestamp()   # 台北 08:30


def _state(with_events=True):
    s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
    if not with_events:
        s.habit_events = []
        return s
    ev = []
    for d, (h, m) in enumerate([(6, 32), (6, 35), (6, 37), (6, 37), (6, 40), (6, 45), (7, 10)]):
        t = datetime(2026, 7, 21 + d, h, m, tzinfo=TZ).timestamp()
        ev += [{"k": "msg", "ts": t}, {"k": "greet_am", "ts": t}]
    t = datetime(2026, 7, 28, 7, 38, tzinfo=TZ).timestamp()              # 今天 07:38（截圖那次）
    ev += [{"k": "msg", "ts": t}, {"k": "greet_am", "ts": t}]
    s.habit_events = ev
    return s


def _recs(n=4):
    return [{"topicLabel": "繪本教學", "ts": f"2026-07-2{d}T01:00:00Z", "text": "x"}
            for d in range(3, 3 + n)]


class RoutineCardTest(unittest.TestCase):
    def test_has_real_numbers_from_screenshot_data(self):
        t = habits.routine_card(_state(), _recs(), NOW, TZ, daily_first=True)
        self.assertIn("07:38", t)                    # 今天
        self.assertIn("比平常晚", t)                  # 程式算的比較（不是語感）
        self.assertIn("樣本 8 次", t)                 # 樣本數要在，講法守則才有東西可誠實

    def test_conversation_and_writing_are_separated(self):
        # ★ 類別錯置的結構解：兩行分開、而且第二行直接寫「跟上面那條是兩回事」
        t = habits.routine_card(_state(), _recs(), NOW, TZ, daily_first=True)
        self.assertIn("他跟你說話的作息：", t)
        self.assertIn("他記寫的時段（跟上面那條是兩回事）：", t)

    def test_no_stats_says_do_not_judge(self):
        t = habits.routine_card(_state(with_events=False), _recs(), NOW, TZ, daily_first=True)
        self.assertIn("沒有", t)
        self.assertIn("別", t)                       # 明令別下判斷
        self.assertNotIn("比平常晚", t)

    def test_thin_writing_sample_is_honest(self):
        t = habits.routine_card(_state(), _recs(n=1), NOW, TZ, daily_first=True)
        self.assertIn("說不準", t)

    def test_no_stray_prefix_duplication(self):
        t = habits.routine_card(_state(), _recs(), NOW, TZ, daily_first=True)
        self.assertNotIn("時段：時段", t)
        self.assertNotIn("〔", t)                    # 內部標記不外洩


class VoiceHintTest(unittest.TestCase):
    """使用者定調：「必須讓 bot 更像有意識的對話行為」——這條守則就是為此存在。"""

    def test_forbids_switching_subject_to_self(self):
        h = persona.ROUTINE_VOICE_HINT
        self.assertIn("主詞永遠是他", h)
        self.assertIn("對我來說", h)                 # 明白點名截圖那句錯法
        self.assertIn("閃掉他的問題", h)

    def test_forbids_reciting_statistics(self):
        h = persona.ROUTINE_VOICE_HINT
        self.assertIn("不要唸統計", h)
        for w in ("中位數", "樣本", "分鐘數", "百分比"):
            self.assertIn(w, h)                      # 明列禁用詞

    def test_sample_scarcity_as_own_limit_not_disclaimer(self):
        h = persona.ROUTINE_VOICE_HINT
        self.assertIn("你自己的限制", h)
        self.assertIn("而不是免責聲明", h)

    def test_separates_talking_from_writing(self):
        self.assertIn("跟他什麼時候**記寫**是兩件事", persona.ROUTINE_VOICE_HINT)

    def test_no_example_sentences(self):
        # §1.69/§1.50 前科：prompt 給範例句 → LLM 逐字抄回、養成口頭禪
        self.assertNotIn("像「", persona.ROUTINE_VOICE_HINT)


class WiringTest(unittest.TestCase):
    def _src(self):
        return io.open("telegram_monitor/monitor.py", encoding="utf-8").read()

    def test_card_is_resident_not_detector_gated(self):
        s = self._src()
        self.assertIn("habits.routine_card(state,", s)
        i = s.index("habits.routine_card(state,")
        # 常駐＝掛在 _fact_card 裡，不在任何 is_*_question 分支內
        self.assertIn('getattr(cfg, "routine_card_enabled", False)', s[max(0, i - 400):i])
        self.assertNotIn("is_user_habit_question", s[max(0, i - 400):i])

    def test_voice_hint_injected_into_chat_lanes(self):
        self.assertGreaterEqual(self._src().count("_routine_voice_hint(state, cfg)"), 3)

    def test_card_directive_answers_him_not_itself(self):
        self.assertIn("他回頭追問時，答的是**他**、不是你", self._src())

    def test_flag_off_is_inert(self):
        self.assertEqual(monitor._routine_voice_hint(_state(), SimpleNamespace()), "")


class ConfigTest(unittest.TestCase):
    def test_flag_synced(self):
        src = io.open("telegram_monitor/config.py", encoding="utf-8").read()
        self.assertIn("ROUTINE_CARD", src)
        self.assertIn("routine_card_enabled", src)
        env = io.open(".env.example", encoding="utf-8").read()
        self.assertRegex(env, re.compile(r"^ROUTINE_CARD=1", re.M))
        self.assertIn("ROUTINE_CARD", io.open("README.md", encoding="utf-8").read())

    def test_getattr_defaults_false(self):
        self.assertFalse(getattr(SimpleNamespace(), "routine_card_enabled", False))


if __name__ == "__main__":
    unittest.main()
