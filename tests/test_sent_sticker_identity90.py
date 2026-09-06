"""🎴 §0.90 bot 認不得自己送了哪張貼圖 → 被問「為什麼喜歡這張」時把它幻覺成更早訊息的別張（藍色蝴蝶 emoji）。

截圖：bot 真的送出一張貼圖（黃狗），使用者「你為什麼喜歡這張呢？」，bot 卻答「你又問起這張**藍色蝴蝶**的貼圖」
——把剛送的真貼圖說成更早用文字打的「🦋」emoji＝張冠李戴、不誠實（說到做不到的另一面：做了卻認不得、亂掰）。

修（旗標預設開、關＝逐位元同現狀）：
1. 送出真貼圖時記下那張的**情緒標記 emoji**（last_sticker_emoji，_record_sticker_sent 查 known_sticker_ids）——
   讓「剛送的那張」有可接地的身分。
2. 問/聊「剛送的那張貼圖」（asks_about_sent_sticker）＋近期真的送過（last_sticker_ts 窗內）→ 注入誠實接地 mhint：
   據實談那一張（emoji 標記或憑感覺挑）、**看不到圖案本身故別捏造樣子、別認成別張/emoji**。
"""

import unittest
from types import SimpleNamespace

from telegram_monitor import monitor, selfstate, persona


class AsksAboutSentStickerTest(unittest.TestCase):
    def test_fires_on_referring_the_sent_one(self):
        for m in ["你為什麼喜歡這張呢？", "哇，這貼圖好玩噎", "為什麼喜歡這張貼圖",
                  "剛剛那張是什麼", "這張是什麼意思", "你喜歡這張嗎", "那張貼圖好可愛"]:
            self.assertTrue(selfstate.asks_about_sent_sticker(m), m)

    def test_no_fire_without_sticker_reference(self):
        for m in ["今天天氣如何", "這個問題我不懂", "這件事怎麼辦", "我剛剛在忙", "為什麼會這樣"]:
            self.assertFalse(selfstate.asks_about_sent_sticker(m), m)

    def test_no_fire_on_negation_or_teaching(self):
        # 否定（不要貼圖）／教學（我教你這張貼圖）不是「問剛送的那張」
        for m in ["不要傳貼圖給我", "我教你這張貼圖", "以後這種貼圖都存起來"]:
            self.assertFalse(selfstate.asks_about_sent_sticker(m), m)

    def test_too_long_bails(self):
        self.assertFalse(selfstate.asks_about_sent_sticker("這張" + "字" * 60))


class RecordSentStickerEmojiTest(unittest.TestCase):
    def _state(self):
        return SimpleNamespace(
            known_sticker_ids=[{"file_id": "F1", "emoji": "😏", "valence": "positive", "ts": 10}],
            recent_sticker_ids=[], last_sticker_id=None, last_sticker_ts=0, last_sticker_emoji=None)

    def test_records_emoji_tag_of_sent(self):
        st = self._state()
        monitor._record_sticker_sent(st, "F1", 2000.0)
        self.assertEqual(st.last_sticker_emoji, "😏")
        self.assertEqual(st.last_sticker_id, "F1")

    def test_unknown_fid_records_empty(self):
        st = self._state()
        monitor._record_sticker_sent(st, "F_UNKNOWN", 2000.0)
        self.assertEqual(st.last_sticker_emoji, "")

    def test_sticker_emoji_for_lookup(self):
        st = self._state()
        self.assertEqual(monitor._sticker_emoji_for(st, "F1"), "😏")
        self.assertEqual(monitor._sticker_emoji_for(st, "nope"), "")


class SentStickerGroundHintTest(unittest.TestCase):
    def _state(self, ts=1000.0, emoji="😄"):
        return SimpleNamespace(last_sticker_ts=ts, last_sticker_emoji=emoji, known_sticker_ids=[])

    def test_hint_injected_in_window(self):
        cfg = SimpleNamespace(sent_sticker_ground_enabled=True)
        h = monitor._sent_sticker_ground_hint(self._state(), cfg, "為什麼喜歡這張呢", 1100.0)
        self.assertTrue(h)
        self.assertIn("剛剛真的送出", h)
        self.assertIn("😄", h)                                   # 帶那張的情緒標記接地

    def test_no_hint_out_of_window(self):
        cfg = SimpleNamespace(sent_sticker_ground_enabled=True)
        # 超過 §0.86 貼圖情境窗（15 分）→ 不注入（別無中生有）
        self.assertEqual(monitor._sent_sticker_ground_hint(self._state(), cfg, "為什麼喜歡這張呢", 1000.0 + 16 * 60 + 1), "")

    def test_no_hint_if_never_sent(self):
        cfg = SimpleNamespace(sent_sticker_ground_enabled=True)
        st = SimpleNamespace(last_sticker_ts=0, last_sticker_emoji=None, known_sticker_ids=[])
        self.assertEqual(monitor._sent_sticker_ground_hint(st, cfg, "為什麼喜歡這張呢", 1100.0), "")

    def test_no_hint_if_not_asking_about_sticker(self):
        cfg = SimpleNamespace(sent_sticker_ground_enabled=True)
        self.assertEqual(monitor._sent_sticker_ground_hint(self._state(), cfg, "今天天氣如何", 1100.0), "")

    def test_flag_off_no_hint(self):
        cfg = SimpleNamespace(sent_sticker_ground_enabled=False)
        self.assertEqual(monitor._sent_sticker_ground_hint(self._state(), cfg, "為什麼喜歡這張呢", 1100.0), "")

    def test_unknown_emoji_forbids_fabricating_appearance(self):
        # 沒情緒標記時，接地要求誠實：看不到圖案、別捏造「藍色蝴蝶」這種樣子
        h = persona.sent_sticker_ground_hint("")
        self.assertIn("看不到", h)
        self.assertIn("藍色蝴蝶", h)                              # 明列反例，禁止張冠李戴
        # 兩種都禁止認成別張/emoji
        for e in ("", "😄"):
            self.assertIn("emoji", persona.sent_sticker_ground_hint(e))


if __name__ == "__main__":
    unittest.main()
