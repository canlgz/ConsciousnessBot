"""🎴 §0.95 telegram sticker ≠ emoji（在「偏好題」這條路上一樣硬）＋偏好題⇒真的送出 sticker。

截圖根因：「30分鐘後，**告訴**我你喜歡的貼圖，且說為什麼喜歡」沒有送/傳動詞 → promise_wants_sticker=False
→ 到點只用文字講、沒送出真 sticker；而文字講貼圖就掉回拿 **emoji 標記（😂）當畫面**描述（「大笑到流淚的貼圖」）。
修：偏好題一律視為 wants_sticker（唯一誠實的回答方式就是把那張真圖送出來）＋守則硬性分清 emoji ≠ 畫面。
另補既有破口：純送貼圖承諾沒送成時 promised 被清空 → sticker_note 原本整個消失（守則失效）。
"""

import os
import tempfile
import unittest
from types import SimpleNamespace

from telegram_monitor import monitor, persona, selfstate
from telegram_monitor.state import State

NOW_TS = 1_700_000_000
SEEN_DESC = "一隻笑倒在地上的黃色小狗"
SCREENSHOT = "30分鐘後，告訴我你喜歡的貼圖，且說為什麼喜歡"


def _state(with_stock=True):
    s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
    s.owner_folder_id = "F"
    if with_stock:
        s.known_sticker_ids = [{"file_id": "F1", "emoji": "😂", "valence": "positive",
                                "desc": SEEN_DESC, "ts": 5}]
    s.entropy = SimpleNamespace(mood=0.4)
    return s


def _cfg(**kw):
    base = dict(dry_run=False, promise_sticker_enabled=True, liked_sticker_pick_enabled=True,
                sticker_file_ids=None, sticker_rotate_window=3, sticker_no_repeat_enabled=True,
                sticker_send_request_enabled=True)
    base.update(kw)
    return SimpleNamespace(**base)


def _client():
    c = SimpleNamespace(sent=[], stickers=[], dry_run=False)
    c.send = lambda t: c.sent.append(t) or True
    c.send_sticker = lambda fid: c.stickers.append(fid) or True
    return c


class StickerPromiseFlagsTest(unittest.TestCase):
    def test_preference_implies_wants_sticker(self):
        # 「告訴我你喜歡的貼圖」本身沒有送/傳動詞……
        self.assertFalse(selfstate.promise_wants_sticker(SCREENSHOT))
        # ……但偏好題唯一誠實的回答方式就是把那張真 sticker 送出來
        w, c, p = monitor._sticker_promise_flags(SCREENSHOT, _state(), _cfg())
        self.assertTrue(w, "偏好題要真的送出 sticker")
        self.assertTrue(c)
        self.assertTrue(p)

    def test_explicit_send_request_still_works(self):
        w, c, p = monitor._sticker_promise_flags("20分鐘後，傳給我一張你喜歡的貼圖", _state(), _cfg())
        self.assertTrue(w and c and p)

    def test_non_preference_sticker_promise_unchanged(self):
        w, c, p = monitor._sticker_promise_flags("8點問候我還要有貼圖", _state(), _cfg())
        self.assertTrue(w)
        self.assertFalse(p)                      # 非偏好題 → 沿用 §0.68 選圖、不走偏好

    def test_non_sticker_promise(self):
        w, c, p = monitor._sticker_promise_flags("30分鐘後叫我起床", _state(), _cfg())
        self.assertFalse(w or c or p)

    def test_prefers_marked_even_without_stock(self):
        # 沒貨 → 不標 wants（兌現不假裝），但 prefers 仍記著＝兌現文字才會誠實
        w, c, p = monitor._sticker_promise_flags(SCREENSHOT, _state(with_stock=False), _cfg())
        self.assertTrue(w)
        self.assertFalse(c)
        self.assertTrue(p)

    def test_flags_off_are_bitwise_same_as_before(self):
        self.assertEqual(monitor._sticker_promise_flags(SCREENSHOT, _state(), _cfg(promise_sticker_enabled=False)),
                         (False, False, False))
        w, _c, p = monitor._sticker_promise_flags(SCREENSHOT, _state(), _cfg(liked_sticker_pick_enabled=False))
        self.assertFalse(p)
        self.assertEqual(w, selfstate.promise_wants_sticker(SCREENSHOT))   # 退回舊判斷


class ImmediatePreferenceSendsRealStickerTest(unittest.TestCase):
    def test_asking_which_you_like_sends_the_real_sticker(self):
        s, c = _state(), _client()
        took = monitor._maybe_sticker_send(c, s, _cfg(), "你喜歡哪一張貼圖？", NOW_TS)
        self.assertTrue(took)
        self.assertEqual(c.stickers, ["F1"])                   # 真的送出 telegram sticker（自主送）
        said = "".join(c.sent)
        self.assertIn(SEEN_DESC, said)                         # 據實說出是哪一張（畫面，不是 emoji）
        self.assertNotIn("😂", said)                            # 絕不拿情緒標記 emoji 當貼圖


class EmojiIsNotThePictureTest(unittest.TestCase):
    def test_sent_and_seen_forbids_emoji_as_picture(self):
        u = persona.promise_keep_user("21:52", "〔此刻〕", promised="告訴他喜歡的貼圖",
                                      sticker_sent=True, sticker_desc=SEEN_DESC)
        self.assertIn(SEEN_DESC, u)
        self.assertIn("不是它的畫面", u)                        # emoji 標記 ≠ 畫面
        self.assertIn("冒充 telegram 貼圖", u)

    def test_wanted_but_not_sent_forbids_describing_any_picture(self):
        u = persona.promise_keep_user("21:52", "〔此刻〕", promised="送他一張貼圖",
                                      sticker_sent=False, sticker_wanted=True)
        self.assertIn("別描述任何一張", u)
        self.assertIn("不是它的畫面", u)

    def test_guard_survives_when_promised_blanked(self):
        # 既有破口：純送貼圖承諾沒送成 → _promise_keep_body 把 promised 清空 → sticker_note 原本整個消失
        u = persona.promise_keep_user("21:52", "〔此刻〕", promised="",
                                      sticker_sent=False, sticker_wanted=True)
        self.assertIn("沒有真的送出貼圖", u)                    # 守則仍在（不再消失）
        self.assertIn("別描述任何一張", u)

    def test_non_sticker_promise_untouched(self):
        u = persona.promise_keep_user("21:52", "〔此刻〕", promised="", sticker_sent=False, sticker_wanted=False)
        self.assertNotIn("貼圖", u)                             # 逐位元同現狀

    def test_unseen_desc_forbids_describing(self):
        u = persona.promise_keep_user("21:52", "〔此刻〕", promised="送他一張貼圖",
                                      sticker_sent=True, sticker_desc="（動態貼圖，帶 😂，未讀畫面）")
        self.assertIn("沒有真的看到那張的畫面", u)
        self.assertIn("絕對不要**描述它長什麼樣子", u)
        # 守則會**引用**「大笑到流淚」當反例（示範不可以這樣講）→ 檢查的是禁令在場，不是字串不在
        self.assertIn("不是它的畫面", u)
        self.assertIn("當成圖案來描述", u)


if __name__ == "__main__":
    unittest.main()
