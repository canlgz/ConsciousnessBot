"""🎴 §0.99 貼圖感知問句別答非所問／別瞎掰畫面。

截圖：bot 主動送了一張忍者貼圖後，問「你知道剛剛傳的是什麼貼圖？」→ bot 竟又送一張＋罐頭「來，這張真貼圖送你」（答非所問），
再問「這是什麼貼圖」→ 把忍者貼圖瞎掰成「向日葵、黃澄澄的花」（幻覺）。兩個根因：
① `is_sticker_followup_request('你知道剛剛傳的是什麼貼圖')` 誤中→_maybe_sticker_send 搶走又送一張；
② `persona.sent_sticker_ground_hint` 用 `if desc:` 判斷 → 未讀畫面的誠實備援（truthy）被當成「看過、放心描述」。
"""

import unittest
from types import SimpleNamespace

from telegram_monitor import monitor, persona, stickervision

NOW = 1_700_000_000
UNSEEN = "（動態貼圖，帶 🥷，未讀畫面）"
SEEN = "一隻比讚的黑忍者"


def _state():
    return SimpleNamespace(
        known_sticker_ids=[{"file_id": "NINJA", "emoji": "🥷", "valence": "positive", "desc": UNSEEN, "ts": NOW}],
        last_sticker_ts=NOW - 30, last_sticker_id="NINJA", last_sticker_emoji="🥷",
        last_sticker_desc=UNSEEN, recent_sticker_ids=["NINJA"], convo_history=[],
        entropy=SimpleNamespace(mood=0.3))


def _client():
    c = SimpleNamespace(sent=[], stickers=[], dry_run=False)
    c.send = lambda t: c.sent.append(t) or True
    c.send_sticker = lambda fid: c.stickers.append(fid) or True
    return c


def _cfg():
    return SimpleNamespace(sticker_send_request_enabled=True, liked_sticker_pick_enabled=True,
                           sticker_file_ids=None, sticker_rotate_window=3, sticker_no_repeat_enabled=True,
                           sent_sticker_ground_enabled=True, dry_run=False)


class PerceiveQuestionNotSendTest(unittest.TestCase):
    def test_perceive_questions_do_not_resend(self):
        # 問「剛剛那張是什麼」＝感知問句 → 不接管、不再送一張（讓給誠實接地）
        for q in ["你知道剛剛傳的是什麼貼圖？", "你知道剛剛傳的是什麼貼圖", "這是什麼貼圖", "你看得到剛剛那張貼圖嗎？"]:
            s, c = _state(), _client()
            self.assertFalse(monitor._maybe_sticker_send(c, s, _cfg(), q, NOW), q)
            self.assertEqual(c.stickers, [], q)
            self.assertEqual(c.sent, [], q)

    def test_real_send_and_preference_still_work(self):
        # 明確送請求／偏好題 不受影響（照常真的送）
        for q in ["送我一張貼圖", "你喜歡哪一張貼圖？", "傳一張你喜歡的貼圖給我"]:
            s, c = _state(), _client()
            self.assertTrue(monitor._maybe_sticker_send(c, s, _cfg(), q, NOW), q)
            self.assertEqual(c.stickers, ["NINJA"], q)

    def test_grounding_hint_fires_for_perceive_question(self):
        # 感知問句 → §0.90 誠實接地 hint 會產生（流進回覆 mhint）
        s = _state()
        self.assertTrue(monitor._sent_sticker_ground_hint(s, _cfg(), "這是什麼貼圖", NOW))


class NoHallucinateUnseenTest(unittest.TestCase):
    def test_is_seen_distinguishes_fallback_note(self):
        self.assertFalse(stickervision.is_seen(UNSEEN))     # 「未讀畫面」備援＝沒看過
        self.assertTrue(stickervision.is_seen(SEEN))

    def test_unseen_sticker_hint_forbids_describing(self):
        # 修 §0.99 破口：未讀畫面（truthy 備援）不再被當「看過、放心描述」
        h = persona.sent_sticker_ground_hint("🥷", UNSEEN)
        self.assertNotIn("真的看過", h)
        self.assertIn("別具體描述它長什麼樣子", h)
        self.assertNotIn("放心描述", h)

    def test_seen_sticker_hint_can_describe(self):
        h = persona.sent_sticker_ground_hint("😄", SEEN)
        self.assertIn("真的看過", h)
        self.assertIn(SEEN, h)

    def test_empty_desc_falls_to_emoji_branch(self):
        h = persona.sent_sticker_ground_hint("🥷", "")
        self.assertNotIn("真的看過", h)
        self.assertIn("情緒標記", h)


if __name__ == "__main__":
    unittest.main()
