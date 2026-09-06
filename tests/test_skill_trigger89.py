"""🧑‍🏫 §0.89 教過的做法「從未真的被觸發」：情境做法的**觸發條件被丟棄**，只留硬編訊號代號。

截圖：`/skills` 列出 `[｜情境·被質疑] 當使用者問「學會了嗎」時，回覆學到什麼＋感謝` 等做法，但使用者從沒看過它被正確觸發。

真因（實跑確認）：捕捉只存**觸發代號**（sit:testing）＋動作，**丟棄自然語言觸發條件**（「當使用者問『學會了嗎』時」）；
召回只看硬編 `_skill_signals_external["testing"]`，而它只認**攻擊式質疑**（你根本…）＋4 連發社交句——「學會了嗎」兩者皆不中
→ `[情境·被質疑]` 永不觸發。

修（旗標預設開、關＝逐位元同現狀）：
1. **broaden testing 訊號**：加考核/查驗式提問線索（學會了嗎/會了嗎/懂了嗎/考考你/你確定嗎…）＝使用者在測 bot——
   讓**既有**抽象 `[情境·被質疑]` 做法立刻能在「學會了嗎」觸發（不必重教）。
2. **cue 捕捉＋比對**（系統性）：外部情境做法把**觸發線索**存進 topic 段（如 cue「學會了嗎」）；`recall_skills` 對外部情境
   除硬編訊號外，也認 cue 是當前訊息子字串 → **未來**做法照使用者字面教的條件觸發（內在情境無使用者訊息、cur 空＝不走 cue）。
"""

import unittest
from types import SimpleNamespace

from telegram_monitor import plasticity, monitor, reaction


def _testing_sig(msg):
    st = SimpleNamespace(intent_reading={})
    cfg = SimpleNamespace(timezone="Asia/Taipei")
    return monitor._skill_signals_external(st, cfg, 1000.0, text=msg).get("testing")


def _fresh_skill(key, val):
    # 解析 key `route|topic|trigger` → 用 capture_skill（正確 _SKILL_GAIN，等同真實 propose→confirm 教學）
    parts = (key.split("|") + ["", "", ""])[:3]
    topic, trigger = parts[1], parts[2]
    e = []
    plasticity.capture_skill(e, "fact_or_chat", topic, val, now_ts=1000.0, trigger=trigger)
    return e


class BroadenedTestingTest(unittest.TestCase):
    def test_quiz_questions_fire_testing(self):
        # 🔍 §0.89 審查修：directed 精確/彈性正則 + 句首錨短線索；含「你」直接算，句首短線索（會了嗎/記得嗎…）安全收回。
        for m in ["學會了嗎", "你學會了嗎", "你會了嗎", "學起來了嗎", "懂了嗎", "考考你",
                  "你確定嗎", "你行不行", "記住了嗎", "學會了嗎？", "真的學會了嗎", "所以你學會了嗎",
                  "記得嗎", "學會沒", "會了嗎", "你學會這個了嗎", "你聽懂了嗎", "你搞懂了嗎",
                  "這樣懂了嗎", "現在懂了嗎", "你到底懂了嗎", "測試你", "你還記得嗎"]:
            self.assertTrue(_testing_sig(m), m)

    def test_challenge_still_fires(self):
        self.assertTrue(_testing_sig("你根本沒學會"))

    def test_non_quiz_non_challenge_stays_false(self):
        for m in ["今天天氣如何", "我學會了游泳", "幫我查一下記寫", "我被老闆說沒用"]:
            self.assertFalse(_testing_sig(m), m)

    def test_benign_substrings_do_not_fire(self):
        """🔍 §0.89 對抗式審查（HIGH＋MED＋LOW 過度觸發回歸）：
        - HIGH：句中第三方主詞（名詞/被副詞·動詞隔開）——老師學會了嗎/大家都懂了嗎/他聽懂了嗎/學生懂了嗎——句首錨擋掉。
        - LOW：測試你的網路速度（你＝所有格、非考 bot）——測試你(?!的) 守門。
        - 彈性正則間隙防鑽：你知道他學會了嗎（他在你與動詞之間）——動詞須緊接你。
        - 原 4×MED：參考你/開會了嗎/我真的學會了/學會了嗎啡/他學會了嗎。"""
        for m in ["他聽懂了嗎", "老師學會了嗎", "學生懂了嗎", "大家都懂了嗎", "全班都學會了嗎", "同事都學會了嗎",
                  "測試你的網路速度", "測試你的程式", "你知道他學會了嗎", "你猜他學會了嗎",
                  "我想參考你的意見", "思考你的問題", "你要開會了嗎", "我們約會了嗎",
                  "這件事你誤會了嗎", "我真的學會了很多東西", "他學會了嗎",
                  "我學會了嗎啡的正確劑量算法", "小孩功課會了嗎", "開會了嗎", "約會了嗎", "誤會了嗎", "體會了嗎"]:
            self.assertFalse(_testing_sig(m), m)


class ExistingAbstractSkillTest(unittest.TestCase):
    """既有 [情境·被質疑]（||sit:testing、無 cue）靠 broadened 訊號在「學會了嗎」觸發。"""

    def test_fires_on_quiz(self):
        eng = _fresh_skill("||sit:testing", "回覆自己從對話中具體學到了什麼，並表達感謝。")
        for msg in ["學會了嗎", "你學會了嗎"]:
            sig = monitor._skill_signals_external(SimpleNamespace(intent_reading={}),
                                                  SimpleNamespace(timezone="Asia/Taipei"), 1000.0, text=msg)
            r = plasticity.recall_skills(eng, "fact_or_chat", msg, signals=sig, now_ts=1000.0)
            self.assertEqual(len(r), 1, msg)

    def test_no_fire_on_unrelated(self):
        eng = _fresh_skill("||sit:testing", "回覆學到什麼＋感謝")
        sig = monitor._skill_signals_external(SimpleNamespace(intent_reading={}),
                                              SimpleNamespace(timezone="Asia/Taipei"), 1000.0, text="今天天氣如何")
        self.assertEqual(plasticity.recall_skills(eng, "fact_or_chat", "今天天氣如何", signals=sig, now_ts=1000.0), [])


class CueMatchTest(unittest.TestCase):
    """§0.89 cue 比對：外部情境做法存了觸發線索（topic 段）→ 使用者訊息含該 cue 即觸發（即使硬編訊號為 False）。"""

    def test_cue_fires_even_signal_off(self):
        eng = _fresh_skill("|學會了嗎|sit:testing", "回覆學到什麼＋感謝")
        for msg in ["學會了嗎", "嘿，學會了嗎？", "所以你學會了嗎"]:
            r = plasticity.recall_skills(eng, "fact_or_chat", msg, signals={"external": True, "testing": False},
                                         now_ts=1000.0)
            self.assertEqual(len(r), 1, msg)

    def test_cue_not_in_message_no_fire(self):
        eng = _fresh_skill("|學會了嗎|sit:testing", "回覆學到什麼＋感謝")
        r = plasticity.recall_skills(eng, "fact_or_chat", "今天天氣如何", signals={"external": True, "testing": False},
                                     now_ts=1000.0)
        self.assertEqual(r, [])

    def test_internal_situation_ignores_cue(self):
        # 內在情境（low_vitality）由內在訊號觸發、無使用者訊息（cur 空）——cue 路徑不介入
        eng = _fresh_skill("|轉速|sit:low_vitality", "主動提醒多寫訊息＋鼓勵貼圖")
        self.assertEqual(len(plasticity.recall_skills(eng, None, "", signals={"low_vitality": True}, now_ts=1000.0)), 1)
        self.assertEqual(plasticity.recall_skills(eng, None, "", signals={"low_vitality": False}, now_ts=1000.0), [])


class SkillViewRefreshTest(unittest.TestCase):
    """🤝 §0.89 proactive skill 存活修（workflow 診斷）：教一次的內在因應做法在首次觸發前就 5.5 天靜默淡忘、
    /skills 只顯示「已淡忘」＝教了卻永遠等不到發生。修：/skills 檢視＝保鮮——刷新仍活著做法的 last_ts（只重置遺忘時鐘、
    不加權重、不觸發任何行為＝零過度觸發風險）；已淡忘的不刷（誠實）。旗標關＝不刷＝逐位元同現狀。"""

    BASE = 1_700_000_000.0
    D = 86400.0

    def _skill(self, trigger="sit:low_vitality"):
        e = []
        plasticity.capture_skill(e, "fact_or_chat", "轉速", "主動提醒多寫訊息＋鼓勵貼圖",
                                 now_ts=self.BASE, trigger=trigger)
        return e

    def test_view_refreshes_alive_skill_last_ts(self):
        eng = self._skill()
        day5 = self.BASE + 5 * self.D
        self.assertGreaterEqual(plasticity._decayed_weight(eng[0], day5), plasticity._SKILL_ACTIVE)  # 仍活
        plasticity.skills_brief(eng, now_ts=day5, refresh_alive=True)
        self.assertEqual(eng[0]["last_ts"], day5)                       # 遺忘時鐘被重置到檢視當下
        # 重置後再過 5.4 天仍活（不重置的話早在 5.5 天死了）
        self.assertGreaterEqual(plasticity._decayed_weight(eng[0], day5 + 5.4 * self.D), plasticity._SKILL_ACTIVE)

    def test_dormant_skill_not_refreshed(self):
        eng = self._skill(trigger="sit:testing")
        day10 = self.BASE + 10 * self.D
        lt = eng[0]["last_ts"]
        self.assertLess(plasticity._decayed_weight(eng[0], day10), plasticity._SKILL_ACTIVE)  # 已淡忘
        plasticity.skills_brief(eng, now_ts=day10, refresh_alive=True)
        self.assertEqual(eng[0]["last_ts"], lt)                          # 已淡忘的不刷（誠實）

    def test_same_value_dormant_not_resurrected(self):
        """🔍 §0.89 對抗式審查（MED 修）：休眠做法與某活做法**同 value、不同 key** 時，按 value 刷會誤復活它——
        改按唯一 key 刷（reinforce 對同 (kind,key) 就地更新＝key 唯一），只動那條活的。"""
        same = "回覆自己學到什麼並表達感謝"
        eng = []
        plasticity.capture_skill(eng, "fact_or_chat", "x", same, now_ts=self.BASE + 5 * self.D, trigger="sit:testing")
        plasticity.capture_skill(eng, "fact_or_chat", "y", same, now_ts=self.BASE, trigger="sit:user_repeat")
        day = self.BASE + 10 * self.D
        alive = next(e for e in eng if "testing" in (e.get("key") or ""))
        dorm = next(e for e in eng if "user_repeat" in (e.get("key") or ""))
        self.assertGreaterEqual(plasticity._decayed_weight(alive, day), plasticity._SKILL_ACTIVE)
        self.assertLess(plasticity._decayed_weight(dorm, day), plasticity._SKILL_ACTIVE)
        lt_dorm = dorm["last_ts"]
        plasticity.skills_brief(eng, now_ts=day, refresh_alive=True)
        self.assertEqual(alive["last_ts"], day)                          # 活的刷新
        self.assertEqual(dorm["last_ts"], lt_dorm)                       # 同 value 的休眠條**不**被復活

    def test_flag_off_byte_identical(self):
        eng = self._skill()
        day5 = self.BASE + 5 * self.D
        lt = eng[0]["last_ts"]
        b_off = plasticity.skills_brief(eng, now_ts=day5, refresh_alive=False)
        self.assertEqual(eng[0]["last_ts"], lt)                          # 旗標關＝不改 last_ts
        # 輸出字串與開/關無關（只差 last_ts 副作用）
        self.assertEqual(b_off, plasticity.skills_brief(self._skill(), now_ts=day5, refresh_alive=False))


if __name__ == "__main__":
    unittest.main()
