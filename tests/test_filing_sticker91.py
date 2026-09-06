"""🎴 §0.91 /skills 裡「學會的事」必須真的被精確執行，不是空頭支票。

截圖：/skills 列 `[fact_or_chat｜發現新的記寫內容] 當發現新的記寫內容時，要感覺內容並傳送對應的貼圖`，
但歸戶事件（📝 剛歸戶 N 則記寫）真的發生時，bot 只回文字、**從沒送過對應貼圖**。

真因（實跑確認）：做法只被當**文字注入 LLM 提示**，而「送貼圖」是 LLM 產文字**做不到**的機械動作；且這條的觸發是**歸戶事件**、
不走反應式召回 lane（key `fact_or_chat|發現新的記寫內容` 只在使用者訊息路由到 fact_or_chat＋主題吻合時召回，歸戶事件永不觸發）。
＝雙重壞：錯 lane ＋ 無執行 hook ＝說到（/skills 列學會了）做不到（從沒真送）。

修（比照 §0.65 內在因應的真執行 lane，旗標 FILING_STICKER 預設開、關＝逐位元同現狀）：
歸戶通知的感受之後 → 若此刻真有這條**活**做法（active_skill_value）＋有相符真貼圖＋貼圖冷卻過 → **真的 send_sticker** 一張
對應內容情緒的真貼圖（reaction.note_sticker_ids 依 v 的情緒挑池：正向/低落/中性）。沒教/已淡忘/無相符真貼圖＝不送、絕不 emoji 假裝。
"""

import unittest
from types import SimpleNamespace

from telegram_monitor import reaction, plasticity, monitor


class NoteContentValenceTest(unittest.TestCase):
    def test_positive(self):
        for t in ["今天很開心，完成了專案，太棒了", "為你開心", "太感動了好窩心"]:
            self.assertEqual(reaction.note_content_valence(t), "positive", t)

    def test_low(self):
        for t in ["研發卡關了，有點低落、心碎", "這陣子壓力好大，好累", "撞牆了好無力", "有點焦慮迷惘"]:
            self.assertEqual(reaction.note_content_valence(t), "low", t)

    def test_neutral(self):
        for t in ["記了一些一般的事", "研發 writetolearn 日誌", "今天去買菜"]:
            self.assertIsNone(reaction.note_content_valence(t), t)

    def test_negated_low_not_low(self):
        # 前 3 字內有否定＝不算低落（鬆一口氣、走出來了）——避免配求救貼圖
        for t in ["沒有壓力了，鬆一口氣", "不再卡關了", "沒那麼焦慮了", "已經不low了"]:
            self.assertNotEqual(reaction.note_content_valence(t), "low", t)

    def test_bare_fan_not_low(self):
        # 「煩」單字太泛：麻煩你/不厭其煩 不是低落；只有煩惱/煩躁才算
        self.assertIsNone(reaction.note_content_valence("麻煩你幫我看一下"))
        self.assertEqual(reaction.note_content_valence("研發好煩惱"), "low")

    def test_low_first_incidental_praise(self):
        # 🔍 審查 HIGH：低落**先判**——句中誇別人的正向詞不能蓋過「我在低落」（否則歡樂貼圖配哭臉記寫）
        for t in ["看到大家都好棒只有我一個人原地打轉好想哭", "你好棒，可是我今天壓力大到快崩潰"]:
            self.assertEqual(reaction.note_content_valence(t), "low", t)

    def test_relief_phrasings_not_low(self):
        # 🔍 審查 MED：釋懷/遠距否定 → 不算低落（免對「鬆一口氣」配求救貼圖）
        for t in ["沒有那麼大的壓力", "這件事讓我鬆一口氣總算擺脫了焦慮", "壓力好轉了", "不那麼焦慮了"]:
            self.assertNotEqual(reaction.note_content_valence(t), "low", t)
        self.assertEqual(reaction.note_content_valence("今天壓力好大好想哭"), "low")  # 真低落仍低落


class NoteStickerPoolTest(unittest.TestCase):
    KNOWN = [{"file_id": "P1", "emoji": "😄", "valence": "positive", "ts": 1},
             {"file_id": "N1", "emoji": "🥺", "valence": "negative", "ts": 2},
             {"file_id": "M1", "emoji": "🙂", "valence": None, "ts": 3}]

    def test_positive_uses_positive_pool(self):
        self.assertEqual(reaction.note_sticker_ids(self.KNOWN, "太棒了好開心"), ["P1"])

    def test_low_uses_nonpositive_pool(self):
        self.assertEqual(reaction.note_sticker_ids(self.KNOWN, "卡關低落"), ["N1", "M1"])

    def test_neutral_uses_sendable(self):
        self.assertEqual(reaction.note_sticker_ids(self.KNOWN, "記了些事"), ["P1", "M1"])

    def test_low_with_only_positive_stickers_is_empty(self):
        # §0.72 教訓：低落當下沒有非正向貼圖可送＝不送（空），不拿歡樂貼圖硬配
        self.assertEqual(reaction.note_sticker_ids([{"file_id": "P1", "valence": "positive"}], "好卡關好低落"), [])


class ActiveSkillValueTest(unittest.TestCase):
    CUES = (monitor._FILING_NOTE_CUES, monitor._FILING_STICKER_ACTION_CUES)
    BASE = 1_700_000_000.0

    def _eng(self, topic, val, ago_days=0):
        e = []
        plasticity.capture_skill(e, "fact_or_chat", topic, val, now_ts=self.BASE - ago_days * 86400.0)
        return e

    def test_detects_taught_skill(self):
        e = self._eng("發現新的記寫內容", "當發現新的記寫內容時，要感覺內容並傳送對應的貼圖")
        val, key = plasticity.active_skill_value(e, self.CUES, now_ts=self.BASE + 50)
        self.assertTrue(val and "貼圖" in val)
        self.assertEqual(key, e[0]["key"])                # 回 key 供按 key 保鮮

    def test_unrelated_skill_none(self):
        e = self._eng("讀誦經書", "少提讀誦經書這個主題")
        self.assertEqual(plasticity.active_skill_value(e, self.CUES, now_ts=self.BASE + 50), (None, None))

    def test_note_skill_without_sticker_action_none(self):
        # 有記寫線索、但動作不是送貼圖（只是「回一句感受」）→ 不匹配（不誤觸真送）
        e = self._eng("發現新的記寫內容", "當發現新的記寫內容時，回一句貼近的感受")
        self.assertEqual(plasticity.active_skill_value(e, self.CUES, now_ts=self.BASE + 50), (None, None))

    def test_synonym_note_words_detected(self):
        # 🔍 審查 LOW under-detect：記錄/記事/新增/新內容 等 note-app 同義詞也要認得（否則列在 /skills 卻永不執行）
        for val in ["看到你新增的記錄就配一張對應貼圖", "有新內容進來時依情緒傳一張對應貼圖", "使用者記事更新時傳對應貼圖"]:
            e = self._eng("發現新的記寫內容", val)
            v, k = plasticity.active_skill_value(e, self.CUES, now_ts=self.BASE + 50)
            self.assertTrue(v, val)
            self.assertTrue(monitor._FILING_STICKER_SEND_RE.search(v), val)

    def test_dormant_skill_not_returned(self):
        e = self._eng("發現新的記寫內容", "當發現新的記寫內容時，要感覺內容並傳送對應的貼圖", ago_days=40)
        self.assertLess(plasticity._decayed_weight(e[0], self.BASE), plasticity._SKILL_ACTIVE)
        self.assertEqual(plasticity.active_skill_value(e, self.CUES, now_ts=self.BASE), (None, None))

    def test_refresh_by_key_not_value(self):
        # §0.89 教訓：同 value、不同 key 的休眠做法**不**被 use-refresh 復活（按 key 刷）
        alive = "當發現新的記寫內容時，要感覺內容並傳送對應的貼圖"
        e = []
        plasticity.capture_skill(e, "fact_or_chat", "發現新的記寫內容", alive, now_ts=self.BASE + 5 * 86400.0)
        plasticity.capture_skill(e, "smalltalk", "記寫貼圖", alive, now_ts=self.BASE)  # 同 value 舊條、不同 key
        dorm = next(x for x in e if x["key"].startswith("smalltalk"))
        lt_dorm = dorm["last_ts"]
        val, key = plasticity.active_skill_value(e, self.CUES, now_ts=self.BASE + 10 * 86400.0)
        plasticity.touch_skill_key(e, key, now_ts=self.BASE + 10 * 86400.0)
        self.assertEqual(dorm["last_ts"], lt_dorm)         # 同 value 的休眠條不被復活


class FakeClient:
    def __init__(self, ok=True):
        self.sent = []
        self.dry_run = False
        self._ok = ok

    def send_sticker(self, fid):
        self.sent.append(fid)
        return self._ok


class MaybeFilingStickerTest(unittest.TestCase):
    NOW = 1_700_000_100.0
    CFG = dict(filing_sticker_enabled=True, send_stickers=True, dry_run=False,
               sticker_cooldown_min=20, sticker_file_ids=None, sticker_rotate_window=3,
               sticker_no_repeat_enabled=True)

    def _state(self, skill=True, known=None):
        e = []
        if skill:
            plasticity.capture_skill(e, "fact_or_chat", "發現新的記寫內容",
                                     "當發現新的記寫內容時，要感覺內容並傳送對應的貼圖", now_ts=1_700_000_000.0)
        return SimpleNamespace(
            engrams=e,
            known_sticker_ids=(known if known is not None else
                               [{"file_id": "P1", "emoji": "😄", "valence": "positive", "ts": 1},
                                {"file_id": "M1", "emoji": "🙂", "valence": None, "ts": 2}]),
            recent_sticker_ids=[], last_sticker_id=None, last_sticker_ts=0, last_sticker_emoji=None)

    def _cfg(self, **over):
        return SimpleNamespace(**{**self.CFG, **over})

    def test_sends_and_records_and_refreshes(self):
        c, st = FakeClient(), self._state(skill=True)
        r = monitor._maybe_filing_sticker(c, st, self._cfg(), "看你記下這些", self.NOW)
        self.assertTrue(r)
        self.assertEqual(len(c.sent), 1)
        self.assertIn(st.last_sticker_emoji, ("😄", "🙂"))          # §0.90 記下送出那張的標記
        self.assertEqual(st.engrams[0]["last_ts"], self.NOW)          # use-refresh 那條做法

    def test_no_skill_noop(self):
        c, st = FakeClient(), self._state(skill=False)
        self.assertFalse(monitor._maybe_filing_sticker(c, st, self._cfg(), "任何內容", self.NOW))
        self.assertEqual(c.sent, [])

    def test_flag_off_byte_identical(self):
        c, st = FakeClient(), self._state(skill=True)
        self.assertFalse(monitor._maybe_filing_sticker(c, st, self._cfg(filing_sticker_enabled=False), "內容", self.NOW))
        self.assertEqual(c.sent, [])

    def test_cooldown_blocks(self):
        c, st = FakeClient(), self._state(skill=True)
        st.last_sticker_ts = self.NOW - 60                            # 1 分鐘前 < 20 分冷卻
        self.assertFalse(monitor._maybe_filing_sticker(c, st, self._cfg(), "內容", self.NOW))
        self.assertEqual(c.sent, [])

    def test_low_note_no_positive_sticker_honest_noop(self):
        c = FakeClient()
        st = self._state(skill=True, known=[{"file_id": "P1", "valence": "positive"}])
        self.assertFalse(monitor._maybe_filing_sticker(c, st, self._cfg(), "好卡關、好低落、心碎", self.NOW))
        self.assertEqual(c.sent, [])                                  # 沒非正向貼圖＝不送、不拿歡樂硬配

    def test_low_note_sends_support_sticker(self):
        c = FakeClient()
        st = self._state(skill=True, known=[{"file_id": "P1", "valence": "positive"},
                                            {"file_id": "N1", "valence": "negative"}])
        self.assertTrue(monitor._maybe_filing_sticker(c, st, self._cfg(), "好卡關、好低落", self.NOW))
        self.assertEqual(c.sent, ["N1"])                             # 挑非正向的支持貼圖

    def test_dry_run_noop(self):
        c, st = FakeClient(), self._state(skill=True)
        self.assertFalse(monitor._maybe_filing_sticker(c, st, self._cfg(dry_run=True), "內容", self.NOW))
        self.assertEqual(c.sent, [])

    def test_suppression_and_rolereversal_skills_do_not_fire(self):
        # 🔍 審查 HIGH/MED：語意相反（別/少/不要送）＋角色反轉/非送動作（貼了貼圖記錄、日誌裡提貼圖）→ 不誤觸真送
        for val in ["發現新的記寫內容時，別再傳貼圖給我", "發現新記寫時少送貼圖", "發現新記寫時不用送貼圖",
                    "別在記寫日誌裡提貼圖", "整理歸戶清單時別配貼圖太吵", "當我在日誌裡貼了貼圖，幫我把它記錄下來"]:
            e = []
            plasticity.capture_skill(e, "fact_or_chat", "發現新的記寫內容", val, now_ts=1_700_000_000.0)
            st = SimpleNamespace(engrams=e, known_sticker_ids=[{"file_id": "M1", "valence": None}],
                                 recent_sticker_ids=[], last_sticker_id=None, last_sticker_ts=0, last_sticker_emoji=None)
            c = FakeClient()
            self.assertFalse(monitor._maybe_filing_sticker(c, st, self._cfg(), "看你記下這些", self.NOW), val)
            self.assertEqual(c.sent, [], val)

    def test_distress_note_sends_support_not_positive(self):
        # 🔍 審查 MED：情緒配對餵**記寫內容**——低落記寫即使 bot 感受偏暖，也走非正向 help 池（不配歡樂）
        c = FakeClient()
        st = self._state(skill=True, known=[{"file_id": "POS", "valence": "positive"},
                                            {"file_id": "SUP", "valence": "negative"}])
        content = "今天卡關卡到崩潰，壓力好大，撐不住\n（bot 感受）看見你寫下這些，我覺得好窩心"
        self.assertTrue(monitor._maybe_filing_sticker(c, st, self._cfg(), content, self.NOW))
        self.assertEqual(c.sent, ["SUP"])                            # 低落內容→支持貼圖，非歡樂


if __name__ == "__main__":
    unittest.main()
