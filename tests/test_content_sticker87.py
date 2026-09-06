"""🎴 §0.87 內容驅動的情緒貼圖：bot 讀自己這則回覆、若表達**強正向/暖**情緒 → 配一張**正向**真貼圖強化。

保守設計（使用者要求＋維護者不變式）：
- 只在**明確強正向/暖**時配（恭喜/太好了/真為你開心/抱抱你…）；負向/安慰型 v1 不配（用哭臉配安慰＝情緒不對，§0.72）。
- **情緒對得上才配、配不到就不送**（正向池空→不硬送、更不用 emoji 假裝）。
- 共用既有貼圖冷卻＝稀有、每次出現有份量。
- 只送真貼圖、文字不 emoji 假裝（§0.84）。
誠實界限：現有捕捉只帶粗價性（正/負/中性），v1 只做粗配對；細情緒（雀躍 vs 溫暖）需更細標籤。
"""

import os
import tempfile
import unittest
from types import SimpleNamespace

from telegram_monitor import monitor, reaction, config
from telegram_monitor.state import State


class FakeClient:
    def __init__(self):
        self.sent, self.stickers, self.dry_run = [], [], False

    def send(self, text):
        self.sent.append(text)
        return True

    def send_sticker(self, fid):
        self.stickers.append(fid)
        return True


def _cfg(**over):
    base = dict(send_stickers=True, dry_run=False, sticker_cooldown_min=20, content_sticker_enabled=True,
                sticker_no_repeat_enabled=True, sticker_rotate_window=3, sticker_file_ids=[])
    base.update(over)
    return SimpleNamespace(**base)


def _state(known=None, vitality=None, last_ts=0.0):
    s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
    s.owner_folder_id = "F"
    s.known_sticker_ids = known if known is not None else [{"file_id": "pos1", "valence": "positive", "ts": 0},
                                                           {"file_id": "neg1", "valence": "negative", "ts": 0}]
    s.vitality = vitality
    s.last_sticker_ts = last_ts
    return s


class ReplyEmotionTest(unittest.TestCase):
    def test_strong_positive(self):
        for t in ["太好了，真為你開心！", "恭喜你完成了", "太棒了", "給你一個大大的抱抱", "好厲害", "真心替你高興"]:
            self.assertEqual(reaction.reply_emotion(t), "positive", t)

    def test_neutral_or_negative_none(self):
        for t in ["嗯，我看到了", "那你覺得呢？", "這件事有點難", "我不太確定", "辛苦了"]:
            self.assertIsNone(reaction.reply_emotion(t), t)

    def test_negated_positive_screened(self):
        # 🛡️ 審查 MED：帶正向線索卻被否定/推託（先別恭喜/不用恭喜/這不值得慶祝/沒辦法抱抱你）→ 不配（情緒相反、說到做不到）
        for t in ["先別急著恭喜，這件事還沒定案。", "不用恭喜我啦", "這不值得慶祝",
                  "我沒辦法真的抱抱你，我沒有身體。", "別說好厲害了，其實我出了錯。"]:
            self.assertIsNone(reaction.reply_emotion(t), t)


class ContentStickerTest(unittest.TestCase):
    def setUp(self):
        monitor._TURN["last_reply"] = None                # 隔離跨測試殘值

    def test_strong_positive_sends_positive_sticker(self):
        st = _state(); c = FakeClient()
        monitor._maybe_sticker(c, st, _cfg(), 10 ** 7, reply_text="太好了，真為你開心！")
        self.assertEqual(c.stickers, ["pos1"])            # 正向池挑到 pos1
        self.assertEqual(c.sent, [])                      # 不吐 emoji

    def test_neutral_reply_no_content_sticker(self):
        st = _state(vitality=None); c = FakeClient()
        monitor._maybe_sticker(c, st, _cfg(), 10 ** 7, reply_text="嗯，我看到了")
        self.assertEqual(c.stickers, [])
        self.assertEqual(c.sent, [])

    def test_flag_off_byte_identity(self):
        st = _state(vitality=None); c = FakeClient()
        monitor._maybe_sticker(c, st, _cfg(content_sticker_enabled=False), 10 ** 7, reply_text="太好了，真為你開心！")
        self.assertEqual(c.stickers, [])
        self.assertEqual(c.sent, [])

    def test_no_positive_sticker_no_emoji_fake(self):
        # 只有負向貼圖 → 正向池空、sendable 也空（負向被排除）→ 不送、不用 emoji 假裝
        st = _state(known=[{"file_id": "neg1", "valence": "negative", "ts": 0}], vitality=None)
        c = FakeClient()
        monitor._maybe_sticker(c, st, _cfg(), 10 ** 7, reply_text="太好了，真為你開心！")
        self.assertEqual(c.stickers, [])
        self.assertEqual(c.sent, [])

    def test_cooldown_respected(self):
        st = _state(last_ts=10 ** 7); c = FakeClient()             # 剛送過 → 冷卻內
        monitor._maybe_sticker(c, st, _cfg(), 10 ** 7 + 60, reply_text="太好了，真為你開心！")
        self.assertEqual(c.stickers, [])

    def test_reads_turn_last_reply_when_no_arg(self):
        # 不顯式帶 reply_text → 取 _TURN['last_reply']（_say 於 chokepoint 記下）
        st = _state(); c = FakeClient()
        monitor._TURN["last_reply"] = "太好了，真為你開心！"
        monitor._maybe_sticker(c, st, _cfg(), 10 ** 7)
        self.assertEqual(c.stickers, ["pos1"])
        monitor._TURN["last_reply"] = None

    def test_say_stashes_interactive_reply(self):
        # _say（互動回覆＝無 prefix/state）把文字記進 _TURN['last_reply']；帶 prefix 的主動則不記
        monitor._TURN["bubbles"] = 3
        c = FakeClient()
        monitor._say(c, "太好了，真為你開心！")
        self.assertEqual(monitor._TURN.get("last_reply"), "太好了，真為你開心！")
        monitor._say(c, "🤝 到點了", prefix="🤝 ", state=_state(), topic="約定")
        self.assertEqual(monitor._TURN.get("last_reply"), "太好了，真為你開心！")   # 主動兌現不覆蓋


class ConfigDefaultTest(unittest.TestCase):
    def test_flag_default_on(self):
        os.environ.pop("CONTENT_STICKER", None)
        self.assertTrue(config.Config.load().content_sticker_enabled)


if __name__ == "__main__":
    unittest.main()
