"""🎴 貼圖視覺解讀：純判斷／整形小工具（stickervision）＋ monitor 收發整合
（_describe_sticker 收到即讀＋快取／_handle_sticker ack 接地／_record_sticker_sent＋§0.90 送出接地升級到真畫面）。"""

import os
import tempfile
import unittest
from types import SimpleNamespace

from telegram_monitor import monitor, persona, stickervision
from telegram_monitor.state import State

NOW_TS = 1_700_000_000


class StickerVisionPureTest(unittest.TestCase):
    def test_is_static(self):
        self.assertTrue(stickervision.is_static({"emoji": "🔥"}))                 # 缺欄位＝當靜態
        self.assertTrue(stickervision.is_static({}))
        self.assertFalse(stickervision.is_static({"is_animated": True}))
        self.assertFalse(stickervision.is_static({"is_video": True}))

    def test_mime_for(self):
        self.assertEqual(stickervision.mime_for({"emoji": "🔥"}), "image/webp")
        self.assertIsNone(stickervision.mime_for({"is_animated": True}))

    def test_clip(self):
        self.assertEqual(stickervision.clip("  一隻鯊魚\n抱著魚  "), "一隻鯊魚 抱著魚")   # 收合換行/空白
        self.assertEqual(stickervision.clip("「討抱的鯊魚」"), "討抱的鯊魚")             # 去包住整句的引號
        self.assertIsNone(stickervision.clip("   "))                                # 空→None
        self.assertIsNone(stickervision.clip(None))
        long = "字" * 60
        out = stickervision.clip(long, limit=40)
        self.assertTrue(out.endswith("…") and len(out) <= 41)                       # 超長截斷

    def test_fallback_note_is_honest(self):
        n = stickervision.fallback_note({"is_animated": True, "emoji": "😴", "set_name": "Shark"})
        self.assertIn("動態貼圖", n)
        self.assertIn("未讀畫面", n)          # 不假裝看到
        self.assertIn("😴", n)
        self.assertIn("Shark", n)
        self.assertIn("影片貼圖", stickervision.fallback_note({"is_video": True}))
        self.assertIn("貼圖", stickervision.fallback_note({}))


class DescribeStickerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _state(self):
        return State(os.path.join(self.tmp, f"s{id(self)}.json"))

    def _client(self, blob=b"IMG"):
        c = SimpleNamespace(downloads=[])
        c.download_file = lambda fid: (c.downloads.append(fid) or blob)
        return c

    def _coach(self, desc="一隻討抱的鯊魚"):
        c = SimpleNamespace(enabled=True, reads=[])
        c.read_sticker_image = lambda b, mime="image/webp": (c.reads.append((b, mime)) or desc)
        return c

    def _cfg(self, vision=True):
        return SimpleNamespace(read_sticker_vision=vision)

    def test_static_sticker_is_read_and_cached(self):
        s = self._state()
        client, coach = self._client(), self._coach()
        sticker = {"file_id": "F1", "file_unique_id": "U1", "emoji": "🥰"}
        d1 = monitor._describe_sticker(sticker, client, coach, s, self._cfg())
        self.assertEqual(d1, "一隻討抱的鯊魚")
        self.assertEqual(s.sticker_descs["U1"], "一隻討抱的鯊魚")     # 存進快取
        self.assertEqual(client.downloads, ["F1"])
        # 再讀一次同一張 → 走快取，不再下載/讀圖
        d2 = monitor._describe_sticker(sticker, client, coach, s, self._cfg())
        self.assertEqual(d2, "一隻討抱的鯊魚")
        self.assertEqual(client.downloads, ["F1"])                  # 沒有第二次下載
        self.assertEqual(len(coach.reads), 1)

    def test_animated_falls_back_without_download(self):
        s = self._state()
        client, coach = self._client(), self._coach()
        sticker = {"file_id": "F2", "file_unique_id": "U2", "emoji": "😴", "is_animated": True, "set_name": "Shark"}
        d = monitor._describe_sticker(sticker, client, coach, s, self._cfg())
        self.assertIn("動態貼圖", d)
        self.assertIn("未讀畫面", d)
        self.assertEqual(client.downloads, [])                      # 動態不下載、不花視覺
        self.assertNotIn("U2", s.sticker_descs)                     # fallback 不快取（容後可重試）

    def test_vision_off_falls_back(self):
        s = self._state()
        client, coach = self._client(), self._coach()
        sticker = {"file_id": "F3", "file_unique_id": "U3", "emoji": "🔥"}
        d = monitor._describe_sticker(sticker, client, coach, s, self._cfg(vision=False))
        self.assertIn("未讀畫面", d)
        self.assertEqual(client.downloads, [])

    def test_download_failure_falls_back(self):
        s = self._state()
        client, coach = self._client(blob=None), self._coach()      # 下載回 None
        sticker = {"file_id": "F4", "file_unique_id": "U4", "emoji": "🔥"}
        d = monitor._describe_sticker(sticker, client, coach, s, self._cfg())
        self.assertIn("未讀畫面", d)
        self.assertEqual(client.downloads, ["F4"])                  # 有試著下載
        self.assertEqual(coach.reads, [])                           # 但沒讀圖


class StickerGroundingTest(unittest.TestCase):
    """收到即讀→描述進記憶；ack 貼著看到的圖；送出時 §0.90 接地升級到真畫面。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _state(self):
        s = State(os.path.join(self.tmp, f"s{id(self)}.json"))
        s.owner_folder_id = "F"
        return s

    def _client(self):
        c = SimpleNamespace(sent=[], stickers=[], dry_run=False, downloads=[])
        c.send = lambda t: c.sent.append(t) or True
        c.send_sticker = lambda fid: c.stickers.append(fid) or True
        c.download_file = lambda fid: (c.downloads.append(fid) or b"IMG")
        return c

    def test_handle_sticker_grounds_ack_and_memory(self):
        s, client = self._state(), self._client()
        cap = {}
        coach = SimpleNamespace(
            enabled=True,
            read_sticker_image=lambda b, mime="image/webp": "一隻討抱的鯊魚",
            voice_sticker_ack=lambda e, v, h, desc=None: cap.setdefault("desc", desc) or "嘿，抱一個 🥰")
        sticker = {"file_id": "F1", "file_unique_id": "U1", "emoji": "🥰"}
        update = {"message": {"chat": {"id": 1}, "sticker": sticker, "date": NOW_TS}}
        monitor._handle_sticker(sticker, update, coach, s, client,
                                SimpleNamespace(dry_run=False, read_sticker_vision=True))
        self.assertEqual(cap["desc"], "一隻討抱的鯊魚")                   # ack 拿到畫面描述
        self.assertEqual(s.known_sticker_ids[0]["desc"], "一隻討抱的鯊魚")  # 描述進記憶
        self.assertTrue(any("一隻討抱的鯊魚" in m["text"] for m in s.convo_history if m["role"] == "user"))

    def test_record_sticker_sent_captures_desc(self):
        # 送出時記下那張的畫面描述（§0.90 從情緒標記升級到真畫面）
        s = self._state()
        s.known_sticker_ids = [{"file_id": "F1", "emoji": "🥰", "valence": "positive", "desc": "一隻討抱的鯊魚"}]
        monitor._record_sticker_sent(s, "F1", NOW_TS)
        self.assertEqual(s.last_sticker_desc, "一隻討抱的鯊魚")
        self.assertEqual(s.last_sticker_id, "F1")

    def test_sent_ground_hint_uses_real_picture_when_desc(self):
        # 有畫面描述 → 提示解除「你看不到圖案」限制、讓 bot 據實談畫面
        with_desc = persona.sent_sticker_ground_hint("🥰", "一隻討抱的鯊魚")
        self.assertIn("一隻討抱的鯊魚", with_desc)
        self.assertIn("真的看過", with_desc)
        # 有 emoji 標記、無畫面描述 → 沿用 §0.90 舊行為（憑情緒標記談、別具體描述圖案）
        emoji_only = persona.sent_sticker_ground_hint("🥰", "")
        self.assertIn("情緒標記", emoji_only)
        self.assertIn("不是你親眼看到的圖案", emoji_only)
        self.assertNotIn("真的看過", emoji_only)
        # 全無（既沒描述也沒情緒標記）→ 明講看不到、別捏造樣子
        blank = persona.sent_sticker_ground_hint("", "")
        self.assertIn("看不到", blank)
        self.assertNotIn("真的看過", blank)


if __name__ == "__main__":
    unittest.main()
