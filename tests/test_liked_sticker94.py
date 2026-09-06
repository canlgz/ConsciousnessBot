"""🎴 §0.94 「我喜歡哪一張」＝真的有偏好：挑圖不再 random.choice、回話據實說出**是哪一張**（用真的看過的畫面描述）。

涵蓋：stickervision.is_seen（看過 vs 未讀畫面備援）／reaction.pick_liked_entry（確定性偏好序）／
selfstate.is_sticker_preference_request（偏好題偵測）／persona.sticker_preference_reply＋promise_keep_user（誠實措辭）／
monitor._send_liked_sticker・_maybe_sticker_send・_promise_send_sticker（挑圖與接地）／旗標關＝逐位元同現狀。
"""

import os
import tempfile
import unittest
from types import SimpleNamespace

from telegram_monitor import monitor, persona, reaction, selfstate, stickervision
from telegram_monitor.state import State

NOW_TS = 1_700_000_000
SEEN_DESC = "一隻張牙舞爪大叫的綠恐龍"
UNSEEN_DESC = "（動態貼圖，帶 😴，未讀畫面）"


class IsSeenTest(unittest.TestCase):
    def test_seen_vs_unseen(self):
        self.assertTrue(stickervision.is_seen(SEEN_DESC))
        self.assertFalse(stickervision.is_seen(UNSEEN_DESC))       # fallback_note 的誠實備援＝沒看過
        self.assertFalse(stickervision.is_seen(""))
        self.assertFalse(stickervision.is_seen(None))
        self.assertFalse(stickervision.is_seen(stickervision.fallback_note({"is_animated": True})))


class PickLikedEntryTest(unittest.TestCase):
    """偏好序：① 真的看過畫面 ② 價性合心情 ③ 最近學到。刻意確定性——偏好要穩定才叫偏好。"""

    def test_seen_beats_unseen_even_if_newer(self):
        known = [{"file_id": "F_SEEN", "desc": SEEN_DESC, "valence": "positive", "ts": 100},
                 {"file_id": "F_UNSEEN", "desc": UNSEEN_DESC, "valence": "positive", "ts": 900}]
        e = reaction.pick_liked_entry(known, None, mood=0.5)
        self.assertEqual(e["file_id"], "F_SEEN")                   # 看過＞沒看過（看過才談得出、不必瞎掰）

    def test_valence_matches_mood(self):
        known = [{"file_id": "F_POS", "desc": "笑臉", "valence": "positive", "ts": 1},
                 {"file_id": "F_NEU", "desc": "發呆", "valence": "neutral", "ts": 1}]
        self.assertEqual(reaction.pick_liked_entry(known, None, mood=0.5)["file_id"], "F_POS")
        self.assertEqual(reaction.pick_liked_entry(known, None, mood=-0.5)["file_id"], "F_NEU")  # 心情低→不挑歡快圖

    def test_tie_breaks_on_most_recently_learned(self):
        known = [{"file_id": "F_OLD", "desc": "貓", "valence": "positive", "ts": 1},
                 {"file_id": "F_NEW", "desc": "狗", "valence": "positive", "ts": 5}]
        self.assertEqual(reaction.pick_liked_entry(known, None, mood=0.5)["file_id"], "F_NEW")

    def test_deterministic_same_state_same_answer(self):
        known = [{"file_id": "A", "desc": "貓", "valence": "positive", "ts": 3},
                 {"file_id": "B", "desc": "狗", "valence": "positive", "ts": 2}]
        picks = {reaction.pick_liked_entry(known, None, mood=0.4)["file_id"] for _ in range(20)}
        self.assertEqual(picks, {"A"})                             # 不是 random：同 state 永遠同一張

    def test_avoids_recently_sent(self):
        known = [{"file_id": "F_SEEN", "desc": SEEN_DESC, "valence": "positive", "ts": 100},
                 {"file_id": "F_OTHER", "desc": "一隻貓", "valence": "positive", "ts": 1}]
        e = reaction.pick_liked_entry(known, None, mood=0.5, recent=["F_SEEN"], window=3)
        self.assertEqual(e["file_id"], "F_OTHER")                  # 沿用 §0.84 多樣化

    def test_negative_excluded_and_configured_fallback(self):
        self.assertIsNone(reaction.pick_liked_entry([{"file_id": "N", "valence": "negative"}], None))
        e = reaction.pick_liked_entry(None, ["CFG1"], mood=0.5)    # 只有設定檔 file_id（無 desc）→ 仍挑得出
        self.assertEqual(e["file_id"], "CFG1")
        self.assertFalse(stickervision.is_seen(e["desc"]))
        self.assertIsNone(reaction.pick_liked_entry(None, None))


class PreferenceRequestDetectTest(unittest.TestCase):
    def test_hits(self):
        for t in ("20分鐘後，可以傳給我一張你喜歡哪一張貼圖",
                  "20分鐘後，告訴我你喜歡哪一張貼圖",
                  "傳一張你喜歡的貼圖給我",
                  "你最喜歡哪張貼圖？"):
            self.assertTrue(selfstate.is_sticker_preference_request(t), t)

    def test_misses(self):
        for t in ("我喜歡這張貼圖",              # 主語是使用者、不是 bot
                  "送我一張貼圖",                # 只是要一張、沒問偏好
                  "不要傳你喜歡的貼圖",          # 否定守門
                  "我教你這張貼圖的意思",        # 教學守門
                  "你喜歡吃什麼",                # 沒有貼圖字
                  ""):
            self.assertFalse(selfstate.is_sticker_preference_request(t), t)


class PreferenceReplyTest(unittest.TestCase):
    def test_says_which_one_when_seen(self):
        r = persona.sticker_preference_reply(True, SEEN_DESC)
        self.assertIn(SEEN_DESC, r)                                # 據實說出是哪一張
        self.assertIn("我自己喜歡的", r)

    def test_honest_when_not_seen(self):
        r = persona.sticker_preference_reply(True, UNSEEN_DESC)
        self.assertIn("沒真的看到", r)                              # 不假裝看到
        self.assertNotIn("恐龍", r)
        self.assertNotIn(UNSEEN_DESC, r)

    def test_not_sent_falls_back_to_honest_84(self):
        self.assertEqual(persona.sticker_preference_reply(False, SEEN_DESC, have_sendable=False),
                         persona.sticker_send_reply(False, have_sendable=False))

    def test_promise_keep_user_grounds_on_real_picture(self):
        u = persona.promise_keep_user("21:09", "〔此刻 21:09〕", promised="送他一張貼圖",
                                      sticker_sent=True, sticker_desc=SEEN_DESC)
        self.assertIn(SEEN_DESC, u)
        self.assertIn("真的看過", u)
        u2 = persona.promise_keep_user("21:09", "〔此刻 21:09〕", promised="送他一張貼圖",
                                       sticker_sent=True, sticker_desc=UNSEEN_DESC)
        self.assertIn("沒有真的看到", u2)                            # 未讀畫面 → 別描述圖案
        u3 = persona.promise_keep_user("21:09", "〔此刻 21:09〕", promised="送他一張貼圖", sticker_sent=True)
        self.assertNotIn("真的看過", u3)                            # 不帶 desc＝逐位元同現狀


class SendLikedStickerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _state(self):
        s = State(os.path.join(self.tmp, f"s{id(self)}.json"))
        s.owner_folder_id = "F"
        s.known_sticker_ids = [
            {"file_id": "F_UNSEEN", "desc": UNSEEN_DESC, "emoji": "😴", "valence": "positive", "ts": 900},
            {"file_id": "F_SEEN", "desc": SEEN_DESC, "emoji": "😄", "valence": "positive", "ts": 100},
        ]
        s.entropy = SimpleNamespace(mood=0.5)
        return s

    def _client(self):
        c = SimpleNamespace(sent=[], stickers=[], dry_run=False)
        c.send = lambda t: c.sent.append(t) or True
        c.send_sticker = lambda fid: c.stickers.append(fid) or True
        return c

    def _cfg(self, **kw):
        base = dict(dry_run=False, liked_sticker_pick_enabled=True, sticker_file_ids=None,
                    sticker_rotate_window=3, sticker_no_repeat_enabled=True,
                    sticker_send_request_enabled=True, promise_sticker_enabled=True)
        base.update(kw)
        return SimpleNamespace(**base)

    def test_sends_the_one_it_has_actually_seen(self):
        s, c = self._state(), self._client()
        self.assertTrue(monitor._send_liked_sticker(c, s, self._cfg(), now_ts=NOW_TS))
        self.assertEqual(c.stickers, ["F_SEEN"])                   # 看過畫面的那張，不是隨機/最新
        self.assertEqual(s.last_sticker_desc, SEEN_DESC)           # 送出時知道自己送了什麼

    def test_flag_off_falls_back_to_existing_random_path(self):
        s, c = self._state(), self._client()
        monitor._send_liked_sticker(c, s, self._cfg(liked_sticker_pick_enabled=False), now_ts=NOW_TS)
        self.assertEqual(len(c.stickers), 1)                       # 仍送得出（走 §0.68 既有選圖）

    def test_immediate_preference_request_says_which_one(self):
        s, c = self._state(), self._client()
        took = monitor._maybe_sticker_send(c, s, self._cfg(), "傳一張你喜歡的貼圖給我", NOW_TS)
        self.assertTrue(took)
        self.assertEqual(c.stickers, ["F_SEEN"])
        said = "".join(c.sent)                                     # _say 會分串成泡泡，看整段
        self.assertIn(SEEN_DESC, said)                             # 回話據實說出是哪一張（非罐頭句）
        self.assertNotIn("來，這張真貼圖送你", said)

    def test_plain_send_request_keeps_old_canned_reply(self):
        s, c = self._state(), self._client()
        monitor._maybe_sticker_send(c, s, self._cfg(), "送我一張貼圖", NOW_TS)
        self.assertEqual(c.sent, ["來，這張真貼圖送你 :)"])          # 非偏好題＝逐位元同現狀

    def test_promise_send_sticker_uses_preference_only_when_flagged(self):
        s, c = self._state(), self._client()
        self.assertTrue(monitor._promise_send_sticker(c, s, self._cfg(), {"prefers_sticker": True}))
        self.assertEqual(c.stickers, ["F_SEEN"])                   # 偏好題 → 挑我喜歡的那張
        s2, c2 = self._state(), self._client()
        monitor._promise_send_sticker(c2, s2, self._cfg(), {})     # 非偏好題 → 沿用 §0.68 既有選圖
        self.assertEqual(len(c2.stickers), 1)


if __name__ == "__main__":
    unittest.main()
