"""📤 推播收口：LLM 偶吐 markdown（**粗體**、`程式`…），但本 bot 不設 parse_mode，
會原樣顯示成字面星號 → 在唯一送出口 strip 成純文字；同時不可誤傷正常內容。"""

import unittest
from unittest import mock

from telegram_monitor import notifier
from telegram_monitor.notifier import Notifier, strip_markdown


class StripMarkdownTest(unittest.TestCase):
    def test_strips_emphasis_and_code(self):
        # 截圖根因：粗體變字面星號
        self.assertEqual(strip_markdown("你是個**溫暖**又**沉靜**的人"), "你是個溫暖又沉靜的人")
        self.assertEqual(strip_markdown("**很有探索精神**"), "很有探索精神")
        self.assertEqual(strip_markdown("用 `print()` 試試看"), "用 print() 試試看")
        self.assertEqual(strip_markdown("這是 ~~刪掉~~ 的字"), "這是 刪掉 的字")
        self.assertEqual(strip_markdown("這是 *斜體* 字"), "這是 斜體 字")
        self.assertEqual(strip_markdown("這是 _斜體_ 字"), "這是 斜體 字")

    def test_strips_headings_and_bullets(self):
        self.assertEqual(strip_markdown("# 標題\n內容"), "標題\n內容")
        self.assertEqual(strip_markdown("## 小標題 在這"), "小標題 在這")
        self.assertEqual(strip_markdown("- 第一點\n- 第二點"), "第一點\n第二點")
        self.assertEqual(strip_markdown("* 項目一\n* 項目二"), "項目一\n項目二")

    def test_does_not_damage_plain_or_codey_content(self):
        # 孤立／夾在字裡的符號一律不碰
        for s in ["3 * 4 = 12", "a_b_c 變數名", "__init__ 方法", "數學：2*3 和 4*5",
                  "價格 $5 * 數量", "純文字沒有符號", "第一句。第二句。",
                  "emoji 狀態 🌱🌿🌳 ✅⏳⚠️", "・總筆數 5｜近 24h 2", "〈某主題〉— 已升格"]:
            self.assertEqual(strip_markdown(s), s, s)

    def test_empty_and_none_safe(self):
        self.assertEqual(strip_markdown(""), "")
        self.assertIsNone(strip_markdown(None))


class SendStripsTest(unittest.TestCase):
    def test_send_strips_before_transport(self):
        n = Notifier("TOKEN", 123, dry_run=False)
        with mock.patch.object(notifier.requests, "post") as post:
            post.return_value = mock.Mock(status_code=200, json=lambda: {"result": {"message_id": 7}})
            n.send("你是個**溫暖**的人")
        sent_text = post.call_args.kwargs["json"]["text"]
        self.assertEqual(sent_text, "你是個溫暖的人")          # 送到 Telegram 的是純文字
        self.assertNotIn("**", sent_text)


class SayCrossBubbleTest(unittest.TestCase):
    def test_bold_spanning_bubble_boundary_is_stripped(self):
        # 截圖根因：**…**  內含「？」（句界）→ bubble_split 把粗體跨串切開，每串只剩落單 ** →
        # 送出口的 per-bubble strip（成對才清）漏掉 → 字面星號外洩。修：_say 在分串**前**就清。
        from telegram_monitor import monitor
        txt = ("會不會對你來說，它更像是一種**在面對無可奈何時你能掌握的「肯定」？**"
               " 就像下雨讓行程變沉，但運動是你能穩定做到的事。")
        sent = []

        class C:
            dry_run = True

            def send(self, t):
                sent.append(t)
                return True

            def send_typing(self):
                pass

        monitor._TURN["bubbles"] = None
        monitor._say(C(), txt)
        self.assertGreater(len(sent), 1)                       # 仍分串（跨句）
        self.assertFalse(any("**" in b for b in sent))         # 跨串的 ** 也被清掉
        self.assertIn("肯定", "".join(sent))                   # 內容完整


if __name__ == "__main__":
    unittest.main()
