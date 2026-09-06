"""🗣️ 插話即時改寫：bot 分串送出途中被插話 → 遞迴判斷剩餘怎麼辦（簡化暖收 wrap／剩無幾就說完 resume）。
純函式 _interrupt_kind / decide_resume；_say 薄殼依 action 改寫剩餘（旗標關＝原樣續送、逐位元同現狀）。"""

import unittest
from types import SimpleNamespace
from unittest import mock

from telegram_monitor import monitor


class InterruptKindTest(unittest.TestCase):
    def _ups(self, *texts):
        return [{"update_id": i, "message": {"chat": {"id": 1}, "text": t}} for i, t in enumerate(texts)]

    def test_redirect_question_or_command(self):
        self.assertEqual(monitor._interrupt_kind(self._ups("等等，這是什麼意思？")), "redirect")
        self.assertEqual(monitor._interrupt_kind(self._ups("可不可以先講這個")), "redirect")

    def test_statement_is_statement(self):
        self.assertEqual(monitor._interrupt_kind(self._ups("小心點")), "statement")
        self.assertEqual(monitor._interrupt_kind(self._ups("我只是隨口說說")), "statement")


class DecideResumeTest(unittest.TestCase):
    def test_depth_cap_forces_wrap(self):
        self.assertEqual(monitor.decide_resume(remaining=5, sent=2, kind="redirect", depth=2, max_depth=2), "wrap")

    def test_almost_done_resumes(self):
        self.assertEqual(monitor.decide_resume(1, 3, "redirect", 1, 2), "resume")   # 剩 ≤1 → 說完

    def test_body_not_sent_resumes(self):
        # 主體一串都還沒送出 → 至少把主體講出一次（下限保護），不被插話完全吃掉
        self.assertEqual(monitor.decide_resume(4, 0, "redirect", 1, 2), "resume")

    def test_redirect_with_remaining_and_body_wraps(self):
        self.assertEqual(monitor.decide_resume(4, 2, "redirect", 1, 2), "wrap")     # 簡化暖收

    def test_statement_same_rule(self):
        self.assertEqual(monitor.decide_resume(4, 2, "statement", 1, 2), "wrap")


class SayRewriteTest(unittest.TestCase):
    """_say 薄殼：旗標開→wrap 時改送暖收一句並停送剩餘；旗標關→原樣續送（同現狀）。"""

    def setUp(self):
        monitor._TURN["bubbles"] = None        # 隔離：別讓他測殘留的串數上限把四串併少（_say 讀此模組全域）

    def _client(self, cfg, redirect_at_bubble=True, coach=None, poll_text="等等，這是什麼？"):
        sent = []
        # 第一次 poll 回插話、之後回 None（模擬送第 2 串前被插話一次）
        polls = [[{"update_id": 99, "message": {"chat": {"id": 1}, "text": poll_text}}]]

        class C:
            dry_run = False                                 # poll 在 dry_run 下停用，需 False 才會偵測插話
            def __init__(s):
                s.sent = sent
            def send(s, t):
                sent.append(t); return True
            def send_typing(s):
                pass
            def get_updates(s, offset=0, timeout=0):
                return polls.pop(0) if polls else []
        c = C()
        st = SimpleNamespace(tg_update_offset=0)
        handled = []
        c._interrupt = monitor._BurstInterrupt(c, st, cfg, floor=0, handle_fn=lambda u: handled.append(u),
                                               coach=coach)
        c._handled = handled
        return c

    def _condense_coach(self, ret="（濃縮）大概是這樣，你說？"):
        seen = {}

        def vwc(remaining, history, natural=False):
            seen["remaining"] = remaining
            return ret
        return SimpleNamespace(enabled=True, voice_wrap_condense=vwc), seen

    def test_rewrite_on_wraps_remaining(self):
        cfg = SimpleNamespace(interrupt_rewrite_enabled=True, interrupt_max_depth=2, interrupt_statement_enabled=False)
        c = self._client(cfg)
        with mock.patch.object(monitor, "_sleep"):
            monitor._say(c, "第一串。第二串。第三串。第四串。")   # 4 串；送第2串前被 redirect 插話
        self.assertTrue(c._handled)                              # 插話有被優先處理
        self.assertIn("第一串。", c.sent)                        # 主體第一串已送
        self.assertTrue(any(x in c.sent for x in monitor._REWRITE_CLOSE_LINES))   # 改送暖收
        self.assertNotIn("第四串。", c.sent)                     # 剩餘原串不再全送（簡化）

    def test_continuation_followup_deferred_not_treated_as_interrupt(self):
        # 🧵 續句（同時/還有…）即使帶問號，也 defer 折進下一輪、不在回應途中當 redirect 單獨答
        cfg = SimpleNamespace(interrupt_rewrite_enabled=True, interrupt_max_depth=2,
                              interrupt_statement_enabled=True, interrupt_continuation_defer=True)
        c = self._client(cfg, poll_text="同時說一下正在翻閱哪個主題嗎？")
        with mock.patch.object(monitor, "_sleep"):
            monitor._say(c, "第一串。第二串。第三串。第四串。")
        self.assertFalse(c._handled)                             # 續句沒被當插話處理（defer）
        self.assertIn("第四串。", c.sent)                        # 原串照常送完、沒被打斷
        self.assertFalse(any(x in c.sent for x in monitor._REWRITE_CLOSE_LINES))   # 沒觸發暖收

    def test_continuation_defer_flag_off_still_interrupts(self):
        # 旗標關＝同現狀：帶問號的續句仍被當插話 redirect 處理
        cfg = SimpleNamespace(interrupt_rewrite_enabled=True, interrupt_max_depth=2,
                              interrupt_statement_enabled=True, interrupt_continuation_defer=False)
        c = self._client(cfg, poll_text="同時說一下正在翻閱哪個主題嗎？")
        with mock.patch.object(monitor, "_sleep"):
            monitor._say(c, "第一串。第二串。第三串。第四串。")
        self.assertTrue(c._handled)                              # 旗標關→續句被當插話（重現舊行為）

    def test_rewrite_off_is_byte_identical_resume(self):
        cfg = SimpleNamespace(interrupt_rewrite_enabled=False, interrupt_max_depth=2, interrupt_statement_enabled=False)
        c = self._client(cfg)
        with mock.patch.object(monitor, "_sleep"):
            monitor._say(c, "第一串。第二串。第三串。第四串。")
        self.assertTrue(c._handled)
        self.assertIn("第四串。", c.sent)                        # 原樣續送完（現行行為）
        self.assertFalse(any(x in c.sent for x in monitor._REWRITE_CLOSE_LINES))

    def test_wrap_condenses_remaining_with_coach(self):
        # 🗣️ wrap 時把**還沒送出的剩餘串**濃縮成一句送出（非空收丟內容）；不再送模板暖收
        cfg = SimpleNamespace(interrupt_rewrite_enabled=True, interrupt_max_depth=2,
                              interrupt_statement_enabled=False, interrupt_wrap_condense_enabled=True)
        coach, seen = self._condense_coach()
        c = self._client(cfg, coach=coach)
        with mock.patch.object(monitor, "_sleep"):
            monitor._say(c, "第一串。第二串。第三串。第四串。")
        self.assertIn("第一串。", c.sent)                                  # 主體已送
        self.assertIn("（濃縮）大概是這樣，你說？", c.sent)                  # 濃縮句送出
        self.assertFalse(any(x in c.sent for x in monitor._REWRITE_CLOSE_LINES))  # 不再用空收模板
        # 濃縮拿到的是「還沒送出的剩餘串」（含第二串起）
        self.assertIn("第二串。", seen["remaining"])
        self.assertNotIn("第一串。", seen["remaining"])                    # 已送出的主體不重複丟進去

    def test_wrap_falls_back_to_template_when_condense_fails(self):
        # 教練回 None（LLM 失敗）→ 退回模板暖收（不致空手）
        cfg = SimpleNamespace(interrupt_rewrite_enabled=True, interrupt_max_depth=2,
                              interrupt_statement_enabled=False, interrupt_wrap_condense_enabled=True)
        coach, _ = self._condense_coach(ret=None)
        c = self._client(cfg, coach=coach)
        with mock.patch.object(monitor, "_sleep"):
            monitor._say(c, "第一串。第二串。第三串。第四串。")
        self.assertTrue(any(x in c.sent for x in monitor._REWRITE_CLOSE_LINES))   # 退回模板暖收

    def test_wrap_condense_strips_markdown_and_time_tags(self):
        # 濃縮句是 LLM 直送（繞過 _say 頂端清洗）→ 必須補洗 markdown／洩漏時間標籤再送（鎖住回歸）
        cfg = SimpleNamespace(interrupt_rewrite_enabled=True, interrupt_max_depth=2,
                              interrupt_statement_enabled=False, interrupt_wrap_condense_enabled=True)
        coach, _ = self._condense_coach(ret="**重點**就是這個〔3分前〕，你說？")
        c = self._client(cfg, coach=coach)
        with mock.patch.object(monitor, "_sleep"):
            monitor._say(c, "第一串。第二串。第三串。第四串。")
        wrapline = [m for m in c.sent if "就是這個" in m]
        self.assertTrue(wrapline)
        self.assertNotIn("**", wrapline[0])                 # markdown 已洗
        self.assertNotIn("〔3分前〕", wrapline[0])            # 洩漏時間標籤已洗

    def test_wrap_whitespace_only_condense_falls_back(self):
        # 濃縮回純空白（strip 後為空）→ 退回模板暖收、不送空泡泡
        cfg = SimpleNamespace(interrupt_rewrite_enabled=True, interrupt_max_depth=2,
                              interrupt_statement_enabled=False, interrupt_wrap_condense_enabled=True)
        coach, _ = self._condense_coach(ret="   ")
        c = self._client(cfg, coach=coach)
        with mock.patch.object(monitor, "_sleep"):
            monitor._say(c, "第一串。第二串。第三串。第四串。")
        self.assertTrue(any(x in c.sent for x in monitor._REWRITE_CLOSE_LINES))   # 退回模板
        self.assertNotIn("   ", c.sent)                     # 不送空白泡泡

    def test_wrap_condense_flag_off_uses_template(self):
        # INTERRUPT_WRAP_CONDENSE_ENABLED=0 → 即使有教練也走原模板暖收（逐位元同現狀）
        cfg = SimpleNamespace(interrupt_rewrite_enabled=True, interrupt_max_depth=2,
                              interrupt_statement_enabled=False, interrupt_wrap_condense_enabled=False)
        coach, seen = self._condense_coach()
        c = self._client(cfg, coach=coach)
        with mock.patch.object(monitor, "_sleep"):
            monitor._say(c, "第一串。第二串。第三串。第四串。")
        self.assertTrue(any(x in c.sent for x in monitor._REWRITE_CLOSE_LINES))   # 模板暖收
        self.assertNotIn("remaining", seen)                                       # 沒呼叫濃縮


if __name__ == "__main__":
    unittest.main()
