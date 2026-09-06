"""對話時機感（純函式）：交錯/久別重逢/回得慢的分類，與「只內化、不明講」的語氣提示。"""

import unittest

from telegram_monitor import tempo


class TempoTest(unittest.TestCase):
    def test_crossed_when_proactive_overlaps(self):
        # bot 在對方送出後 3s 才主動開口（對方正打字就被講過去）→ 交錯
        mood, charge = tempo.read_tempo(user_msg_ts=1000.0, now_ts=1000.0,
                                        last_user_msg_ts=990.0, last_proactive_ts=1003.0)
        self.assertEqual(mood, "crossed")
        self.assertGreater(charge, 0)

    def test_not_crossed_when_user_clearly_replies_later(self):
        # bot 先開口、對方 30s 後才回（讀了才回）→ 正常回覆，不算交錯
        mood, _ = tempo.read_tempo(user_msg_ts=1030.0, now_ts=1030.0,
                                   last_user_msg_ts=990.0, last_proactive_ts=1000.0)
        self.assertNotEqual(mood, "crossed")

    def test_reunion_after_long_silence(self):
        mood, charge = tempo.read_tempo(user_msg_ts=100000.0, now_ts=100000.0,
                                        last_user_msg_ts=100000.0 - 8 * 3600, last_proactive_ts=0.0)
        self.assertEqual(mood, "reunion")
        self.assertGreater(charge, 0)

    def test_slow_when_message_waited(self):
        mood, charge = tempo.read_tempo(user_msg_ts=1000.0, now_ts=1000.0 + 120,
                                        last_user_msg_ts=999.0, last_proactive_ts=0.0)
        self.assertEqual(mood, "slow")
        self.assertGreater(charge, 0)

    def test_no_event_is_neutral(self):
        mood, charge = tempo.read_tempo(user_msg_ts=1000.0, now_ts=1001.0,
                                        last_user_msg_ts=995.0, last_proactive_ts=0.0)
        self.assertIsNone(mood)
        self.assertEqual(charge, 0.0)

    def test_mood_hint_forbids_naming_the_timing(self):
        # 每個 mood 都有語氣提示，且都帶「不要明講/不要道歉」這類禁令（只內化）
        for m in ("crossed", "reunion", "slow"):
            h = tempo.mood_hint(m)
            self.assertTrue(h)
            self.assertIn("不", h)
            self.assertIn("只內化", h)
        self.assertEqual(tempo.mood_hint(None), "")
        self.assertEqual(tempo.mood_hint(""), "")


if __name__ == "__main__":
    unittest.main()
