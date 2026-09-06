"""對話分串（像真人一串一串打字）：monitor.bubble_split / monitor._say。

原則：段內一句一串、整句別太長（≤ 上限）就不剁碎、過長的才依逗號切到 ≤ 上限（多串說完、不封頂串數）；
空行段落是硬邊界；證據性資料不走這裡（維持整塊）。
串與串之間補「輸入中…」＋短停頓（真人手感）；dry_run／無 typing 能力則略過停頓。
連續說話時若使用者插話 → 先優先回應、再橋接接回（InterruptTest）。
"""

import unittest
from types import SimpleNamespace
from unittest import mock

from telegram_monitor import monitor


class FakeClient:
    """一般 client：沒有 send_typing 能力 → _say 不會補停頓。"""
    def __init__(self, dry_run=False):
        self.sent, self.dry_run = [], dry_run

    def send(self, text):
        self.sent.append(text)
        return True


class TypingClient(FakeClient):
    def __init__(self, dry_run=False):
        super().__init__(dry_run)
        self.typing = 0

    def send_typing(self):
        self.typing += 1


class BubbleSplitTest(unittest.TestCase):
    def test_empty_and_single(self):
        self.assertEqual(monitor.bubble_split(""), [])
        self.assertEqual(monitor.bubble_split("我懂你說的。"), ["我懂你說的。"])

    def test_one_sentence_per_bubble(self):
        self.assertEqual(monitor.bubble_split("我懂。這條線繞很久了。"), ["我懂。", "這條線繞很久了。"])

    def test_no_cap_many_short_sentences(self):
        # 不再封頂 3 串：短句一句一串、全部說完（「多串說完」）
        b = monitor.bubble_split("一。二。三。四。五。")
        self.assertEqual(b, ["一。", "二。", "三。", "四。", "五。"])

    def test_long_sentence_stays_whole_not_comma_split(self):
        # 長句**不再**依逗號切碎：整句完整一串、結尾不掛逗號（截圖根因＝結尾掛「，」的醜片段）
        text = "這是一串刻意寫得比較長的話，長到明顯超過每串的上限字數，但它仍然是一個完整的句子。"
        b = monitor.bubble_split(text)
        self.assertEqual(b, [text])                       # 一句＝一串、整句不切
        self.assertFalse(b[-1].rstrip().endswith(("，", ",", "、", "；")))

    def test_short_sentence_with_comma_stays_one_bubble(self):
        # 短句即使有逗號也不切（句子是分串的最小單位）
        self.assertEqual(monitor.bubble_split("好的，嗯。"), ["好的，嗯。"])

    def test_one_bubble_per_sentence_no_comma_ending(self):
        # 一句一串：兩句完整句→兩串；長句不被切碎；沒有結尾掛逗號的醜片段
        text = "我最近一直在想這件事情，它好像比我以為的還要複雜很多，但又說不太上來到底是哪裡複雜。我也不知道。"
        b = monitor.bubble_split(text)
        self.assertEqual(len(b), 2)
        self.assertEqual("".join(b), text.replace("\n", ""))
        for x in b:
            self.assertFalse(x.rstrip().endswith(("，", ",", "、", "；")))

    def test_blank_line_paragraphs_respected(self):
        b = monitor.bubble_split("第一段想法。\n\n第二段想法。\n\n第三段。")
        self.assertEqual(b, ["第一段想法。", "第二段想法。", "第三段。"])

    def test_mixed_punctuation(self):
        self.assertEqual(monitor.bubble_split("好啊！我來看看。然後呢？"),
                         ["好啊！", "我來看看。", "然後呢？"])

    def test_mid_sentence_ellipsis_not_split_into_dangling_bubble(self):
        # 截圖根因：句中省略號『可能跟你…』被切成獨立泡泡（斷尾）。修後：整句一顆泡泡、不洩斷尾。
        s = "這只是假設啦，可能跟你…的狀況不太一樣，但忍不住想對看。"
        self.assertEqual(monitor.bubble_split(s), [s])
        self.assertFalse(monitor.bubble_split(s)[-1].rstrip().endswith("可能跟你…"))

    def test_sentence_final_ellipsis_still_splits(self):
        # 句末省略號（拖尾、後接新句）仍正常斷句成兩顆泡泡。
        self.assertEqual(monitor.bubble_split("卻是沉沉的負向…… 我突然在想。"),
                         ["卻是沉沉的負向……", "我突然在想。"])


class SayTest(unittest.TestCase):
    def setUp(self):
        monitor._TURN["bubbles"] = None      # 🧪 隔離：_say 讀模組全域上限，別讓別檔（handle_message）殘留值滲進來

    def test_sends_each_bubble_and_returns_ok(self):
        c = FakeClient()
        ok = monitor._say(c, "我懂。這條線繞很久了。也許先從那篇開始。")
        self.assertTrue(ok)
        self.assertEqual(len(c.sent), 3)

    def test_prefix_on_first_bubble_only(self):
        c = FakeClient()
        monitor._say(c, "一。二。", prefix="🫀 背景自陳\n")
        self.assertEqual(c.sent, ["🫀 背景自陳\n一。", "二。"])

    def test_single_sentence_is_one_bubble(self):
        c = FakeClient()
        monitor._say(c, "（自陳）已成形。")
        self.assertEqual(c.sent, ["（自陳）已成形。"])

    def test_typing_and_pause_between_bubbles(self):
        c = TypingClient()
        with mock.patch.object(monitor, "_sleep") as sl:
            monitor._say(c, "一。二。三。")
        self.assertEqual(len(c.sent), 3)
        self.assertEqual(c.typing, 2)            # 串與串之間（n-1 次）才「輸入中…」
        self.assertEqual(sl.call_count, 2)

    def test_strips_leaked_time_tags_per_bubble(self):
        # 截圖 bug：模型每段都掛〔剛剛〕→ 每顆泡泡都洩漏標籤。_say 是所有 voice 的單一出口，送出前一律洗掉。
        c = FakeClient()
        monitor._say(c, "嗯，你說的對。\n\n〔剛剛〕\n我剛剛那些聯想，都是從你的記寫裡抓的。\n\n〔剛剛〕\n但你這樣一說，我才意識到。")
        self.assertTrue(c.sent)
        for b in c.sent:
            self.assertNotIn("〔剛剛〕", b)
        self.assertEqual(c.sent[0], "嗯，你說的對。")
        self.assertTrue(c.sent[1].startswith("我剛剛那些聯想"))   # 標籤拔掉、內容保留（內文裡的「剛剛」不受影響）

    def test_topic_brackets_not_stripped(self):
        # 只洗時間格式標籤；〔主題〕這類正當括號不該被動到
        c = FakeClient()
        monitor._say(c, "我一直繞著〔靈感〕那條線走。")
        self.assertEqual(c.sent, ["我一直繞著〔靈感〕那條線走。"])

    def test_dry_run_skips_typing_pause_but_still_splits(self):
        c = TypingClient(dry_run=True)
        with mock.patch.object(monitor, "_sleep") as sl:
            monitor._say(c, "一。二。三。")
        self.assertEqual(len(c.sent), 3)
        self.assertEqual(c.typing, 0)
        self.assertEqual(sl.call_count, 0)

    def test_typing_delay_scales_with_length_and_caps(self):
        self.assertLess(monitor._typing_delay(2), monitor._typing_delay(40))   # 越長等越久
        self.assertEqual(monitor._typing_delay(9999), monitor._TYPING_MAX_S)   # 上限
        self.assertGreaterEqual(monitor._typing_delay(0), monitor._TYPING_MIN_S)  # 下限

    def test_say_pause_is_proportional_to_next_bubble(self):
        # 串與串之間的停頓＝依「下一串長度」算（真人花時間打字才吐字）：長串前等比較久。
        # 隨機拖拍 _jitter 在 _say 疊；這裡定為 1.0 以驗「比例」這條本身。
        c = TypingClient()
        long_b = "這串話比短的那串明顯長一些喔。"             # 一句、≤ 上限 → 仍是一串（不被再切短）
        with mock.patch.object(monitor, "_sleep") as sl, \
                mock.patch.object(monitor, "_jitter", return_value=1.0):
            monitor._say(c, "短。\n\n" + long_b)
        self.assertEqual(sl.call_count, 1)                       # 2 串→1 次停頓
        self.assertAlmostEqual(sl.call_args[0][0], monitor._typing_delay(len(long_b)), places=5)


class InterruptTest(unittest.TestCase):
    """連續說話時使用者插話 → _say 先優先回應、再用橋接句接回繼續說（不自顧自講完＝有人情味）。"""

    def setUp(self):
        monitor._TURN["bubbles"] = None      # 🧪 隔離：同上，_say 讀模組全域上限

    def _cfg(self):
        return SimpleNamespace(telegram_chat_id="")

    def _state(self):
        return SimpleNamespace(tg_update_offset=10)

    def _msg(self, uid, text):
        return {"update_id": uid, "message": {"chat": {"id": 1}, "text": text}}

    def test_interjection_pauses_handles_then_resumes(self):
        sent, handled = [], []
        polls = [[self._msg(11, "什麼是滲流？")]]            # 第一次 poll 回插話，之後空

        class C:
            dry_run = False
            def send(self, t): sent.append(t); return True
            def get_updates(self, offset=0, timeout=0): return polls.pop(0) if polls else []

        client, state = C(), self._state()
        client._interrupt = monitor._BurstInterrupt(
            client, state, self._cfg(), 10, lambda u: handled.append(u["message"]["text"]))
        monitor._say(client, "一。二。三。四。")              # 4 串

        self.assertEqual(handled, ["什麼是滲流？"])           # 優先回應了插話
        self.assertTrue(any(s in monitor._RESUME_BRIDGES for s in sent))   # 有「剛剛說到哪了」橋接句
        self.assertEqual(sent[0], "一。")                     # 第一串照常
        self.assertIn("四。", sent)                           # 後面也接著說完（沒被插話打斷掉）
        self.assertEqual(state.tg_update_offset, 12)          # 插話被消費、offset 前進（主迴圈不重複處理）

    def test_statement_continuation_deferred_not_interrupting(self):
        # 🌊 同一波的「陳述續打」（非問句，如「小心點」）講話途中到 → **不**當插話另起一段回應＋『繼續剛剛的』，
        # 而是 defer：整段照常講完、不消費（offset 不前進）→ 留給下一圈 _relate_coalesced 收進同一輪（修截圖根因）。
        sent, handled = [], []
        polls = [[self._msg(11, "小心點")]]

        class C:
            dry_run = False
            def send(self, t): sent.append(t); return True
            def get_updates(self, offset=0, timeout=0): return polls.pop(0) if polls else []

        client, state = C(), self._state()
        client._interrupt = monitor._BurstInterrupt(
            client, state, self._cfg(), 10, lambda u: handled.append(u["message"]["text"]))
        monitor._say(client, "一。二。三。四。")

        self.assertEqual(handled, [])                          # 陳述續打不被當插話即時回
        self.assertFalse(any(s in monitor._RESUME_BRIDGES for s in sent))   # 沒有『繼續剛剛的』橋接句
        self.assertEqual(sent, ["一。", "二。", "三。", "四。"])  # 整段照常講完
        self.assertEqual(state.tg_update_offset, 10)          # offset 未前進＝留給下一圈合併（不消費）

    def test_no_interjection_says_through_cleanly(self):
        sent = []

        class C:
            dry_run = False
            def send(self, t): sent.append(t); return True
            def get_updates(self, offset=0, timeout=0): return []

        client = C()
        client._interrupt = monitor._BurstInterrupt(client, self._state(), self._cfg(), 10, lambda u: None)
        monitor._say(client, "一。二。三。")
        self.assertEqual(sent, ["一。", "二。", "三。"])        # 沒插話 → 照常說完、沒有橋接句

    def test_bridge_varied_no_consecutive_repeat(self):
        # 接回話頭的橋接句要多元（不只「我剛剛說到哪了」一種誇飾）、且不連續重複
        intr = monitor._BurstInterrupt(SimpleNamespace(dry_run=True), self._state(), self._cfg(), 10, lambda u: None)
        seq = [intr.bridge() for _ in range(12)]
        self.assertTrue(all(b in monitor._RESUME_BRIDGES for b in seq))
        self.assertTrue(all(seq[i] != seq[i - 1] for i in range(1, len(seq))))   # 不連續重複
        self.assertGreaterEqual(len(set(seq)), 3)                                 # 確實有變化

    def test_sticker_or_reaction_does_not_interrupt(self):
        # 貼圖/reaction 不是「要我先回應的提問」→ 不打斷整段
        polls = [[{"update_id": 11, "message": {"chat": {"id": 1}, "sticker": {"file_id": "x"}}}]]

        class C:
            dry_run = False
            def send(self, t): return True
            def get_updates(self, offset=0, timeout=0): return polls.pop(0) if polls else []

        client = C()
        intr = monitor._BurstInterrupt(client, self._state(), self._cfg(), 10, lambda u: None)
        self.assertIsNone(intr.poll())                        # 只有貼圖 → 不算插話

    def test_dry_run_never_interrupts(self):
        class C:
            dry_run = True
            def get_updates(self, offset=0, timeout=0): raise AssertionError("dry_run 不該 poll")

        intr = monitor._BurstInterrupt(C(), self._state(), self._cfg(), 10, lambda u: None)
        self.assertIsNone(intr.poll())                        # dry_run/測試 → 整個停用

    def test_question_continuation_still_interrupts(self):
        # 問句續打（真的想打斷改問）講話途中到 → 仍即時插話、消費、offset 前進。
        polls = [[self._msg(11, "等等這是什麼意思")]]
        handled = []

        class C:
            dry_run = False
            def send(self, t): return True
            def get_updates(self, offset=0, timeout=0): return polls.pop(0) if polls else []

        client, state = C(), self._state()
        intr = monitor._BurstInterrupt(client, state, self._cfg(), 10, lambda u: handled.append(u))
        got = intr.poll()
        self.assertIsNotNone(got)                             # 問句 → 認定為插話
        intr.handle(got)
        self.assertEqual(len(handled), 1)
        self.assertEqual(state.tg_update_offset, 12)          # 消費、offset 前進


class RedirectDetectTest(unittest.TestCase):
    """_looks_like_redirect：問句/指令＝想打斷改問（即時）；純陳述＝同一波續打（折進下一輪）。"""

    def test_questions_are_redirect(self):
        for t in ("這是什麼？", "可以嗎", "等等這是什麼意思", "為什麼會這樣", "你是不是在說我", "how?"):
            self.assertTrue(monitor._looks_like_redirect(t), t)

    def test_commands_are_redirect(self):
        self.assertTrue(monitor._looks_like_redirect("/status"))
        self.assertTrue(monitor._looks_like_redirect("敏感度 0.5"))

    def test_statements_are_continuation(self):
        for t in ("小心點", "我會盯著你的", "嗯好", "辛苦了", "繼續說"):
            self.assertFalse(monitor._looks_like_redirect(t), t)

    def test_empty_is_not_redirect(self):
        self.assertFalse(monitor._looks_like_redirect(""))
        self.assertFalse(monitor._looks_like_redirect(None))


if __name__ == "__main__":
    unittest.main()
