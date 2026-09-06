"""🌊 同一波多意圖回覆收旂：LLM 只能提案保留哪些原句 index。

程式自己驗證每個被刪句是重複承接或同話者/同極性近重複；無法證明即原稿照留。
因此模型回「好。」、只留第一個意圖、翻轉我/你或否定、丟掉數字/引文，都不能通過。
"""

import unittest
from types import SimpleNamespace
from unittest import mock

from telegram_monitor import coach as coachmod
from telegram_monitor import monitor


SCREENSHOT_DRAFT = (
    "嗯，我有點那樣感覺。"
    "好，我記下來了。"
    "我會盡量不跟你用一樣的貼圖。"
    "嗯，我只是有點感覺你那聲「嗯」好像淡淡的，猜想你是不是正在忙，或是心情就比較平靜而已。"
    "好，我收到了。"
    "我會記住，盡量不要跟你用重複的貼圖。"
)
KEEP = [1, 3, 4, 5]
EXTRACTIVE = (
    "嗯，我有點那樣感覺。"
    "我會盡量不跟你用一樣的貼圖。"
    "嗯，我只是有點感覺你那聲「嗯」好像淡淡的，猜想你是不是正在忙，或是心情就比較平靜而已。"
    "好，我收到了。"
)


def _update(parts):
    return {"burst_n": len(parts), "burst_texts": list(parts),
            "message": {"text": "\n".join(parts)}}


def _repairing_coach(result=KEEP, side_effect=None):
    fn = mock.Mock(return_value=result, side_effect=side_effect)
    return SimpleNamespace(enabled=True, voice_burst_repair=fn), fn


def _cfg(on=True):
    return SimpleNamespace(burst_one_answer_enabled=on)


class RepairGateTest(unittest.TestCase):
    """只有多則合併＋程式先看到可能重複，才為 index 提案多燒一次 LLM。"""

    def test_single_message_never_calls_repair(self):
        original = "剛才是我猜的。你真正的狀態還是你自己最清楚。"
        co, fn = _repairing_coach()
        self.assertEqual(monitor._burst_reply_repair(_update(["有點忙？"]), original, co, _cfg()), original)
        fn.assert_not_called()

    def test_short_coherent_multi_message_reply_is_untouched(self):
        original = (
            "剛才是我從你的「嗯」猜的，不代表你真的在忙。"
            "之後我會避開跟你用一樣的貼圖。"
            "這兩件事我分清楚了。"
        )
        co, fn = _repairing_coach()
        got = monitor._burst_reply_repair(_update(["有點忙？", "不要使用我一樣的貼圖"]), original, co, _cfg())
        self.assertEqual(got, original)
        fn.assert_not_called()

    def test_paraphrase_deletion_is_rejected_even_for_screenshot_draft(self):
        parts = ["有點忙？", "不要使用我一樣的貼圖"]
        co, fn = _repairing_coach()
        got = monitor._burst_reply_repair(_update(parts), SCREENSHOT_DRAFT, co, _cfg())
        # 最後一句看似重複，卻不是逐字同句；SequenceMatcher 無法證明它沒有新承諾，保留原稿。
        self.assertEqual(got, SCREENSHOT_DRAFT)
        fn.assert_called_once_with(parts, SCREENSHOT_DRAFT)

    def test_exact_duplicate_and_extra_ack_can_be_removed_extractively(self):
        draft = "好，我收到了。我不會使用同一張貼圖。好，我記下來了。我不會使用同一張貼圖。"
        co, fn = _repairing_coach(result=[1, 2, 3])
        got = monitor._burst_reply_repair(_update(["好", "別用同一張貼圖"]), draft, co, _cfg())
        self.assertEqual(got, "好，我收到了。我不會使用同一張貼圖。好，我記下來了。")
        fn.assert_called_once()

    def test_weak_ack_cannot_delete_a_memory_claim(self):
        draft = "好，我收到了。我不會使用同一張貼圖。好，我記下來了。我不會使用同一張貼圖。"
        co, _fn = _repairing_coach(result=[1, 2])
        got = monitor._burst_reply_repair(_update(["好", "別用同一張貼圖"]), draft, co, _cfg())
        self.assertEqual(got, draft)                         # 「記下」是交付宣稱，只能刪 exact duplicate

    def test_empty_index_proposal_falls_back_to_original(self):
        co, fn = _repairing_coach(result=[])
        got = monitor._burst_reply_repair(
            _update(["有點忙？", "不要使用我一樣的貼圖"]), SCREENSHOT_DRAFT, co, _cfg())
        self.assertEqual(got, SCREENSHOT_DRAFT)
        fn.assert_called_once()

    def test_repair_exception_falls_back_to_original(self):
        co, fn = _repairing_coach(side_effect=RuntimeError("editor failed"))
        got = monitor._burst_reply_repair(
            _update(["有點忙？", "不要使用我一樣的貼圖"]), SCREENSHOT_DRAFT, co, _cfg())
        self.assertEqual(got, SCREENSHOT_DRAFT)
        fn.assert_called_once()

    def test_free_text_rewrite_is_rejected_even_if_very_short(self):
        co, fn = _repairing_coach(result="好。")
        got = monitor._burst_reply_repair(
            _update(["有點忙？", "不要使用我一樣的貼圖"]), SCREENSHOT_DRAFT, co, _cfg())
        self.assertEqual(got, SCREENSHOT_DRAFT)
        fn.assert_called_once()

    def test_index_proposal_that_drops_unique_intents_is_rejected(self):
        co, _fn = _repairing_coach(result=[1])
        got = monitor._burst_reply_repair(
            _update(["有點忙？", "不要使用我一樣的貼圖"]), SCREENSHOT_DRAFT, co, _cfg())
        self.assertEqual(got, SCREENSHOT_DRAFT)

    def test_duplicate_gate_rejects_role_polarity_and_protected_atom_changes(self):
        self.assertFalse(monitor._burst_duplicate_equiv("我不會用這張。", "你不會用這張。"))
        self.assertFalse(monitor._burst_duplicate_equiv("我不會用這張。", "我會用這張。"))
        self.assertFalse(monitor._burst_duplicate_equiv("我會在 08:00 叫你。", "我會在 09:00 叫你。"))
        self.assertFalse(monitor._burst_duplicate_equiv("你說「不要」。", "你說「可以」。"))

    def test_duplicate_gate_never_guesses_cjk_numbers_antonyms_dates_or_versions(self):
        pairs = [
            ("我會在8點叫你。", "我會在9點叫你。"),
            ("這是第3次提醒。", "這是第4次提醒。"),
            ("我喜歡你這個做法。", "我討厭你這個做法。"),
            ("我支持這件事。", "我反對這件事。"),
            ("明天再處理。", "昨天已處理。"),
            ("版本ABC123已部署。", "版本ABC124已部署。"),
            ("目前是067bd0e。", "目前是067bd0f。"),
        ]
        for left, right in pairs:
            with self.subTest(left=left, right=right):
                self.assertFalse(monitor._burst_duplicate_equiv(left, right))

    def test_flag_off_never_calls_repair(self):
        co, fn = _repairing_coach()
        got = monitor._burst_reply_repair(
            _update(["有點忙？", "不要使用我一樣的貼圖"]), SCREENSHOT_DRAFT, co, _cfg(False))
        self.assertEqual(got, SCREENSHOT_DRAFT)
        fn.assert_not_called()


class BurstMetadataTest(unittest.TestCase):
    def test_burst_texts_preserve_newline_inside_one_message(self):
        group = {
            "type": "text",
            "updates": [
                {"update_id": 1, "message": {"chat": {"id": 1}, "message_id": 11,
                                                "date": 1000, "text": "第一行\n仍是同一則"}},
                {"update_id": 2, "message": {"chat": {"id": 1}, "message_id": 12,
                                                "date": 1001, "text": "第二則"}},
            ],
            "max_update_id": 2,
        }
        out = monitor.build_coalesced_update(group)
        self.assertEqual(out["burst_texts"], ["第一行\n仍是同一則", "第二則"])
        self.assertEqual(out["burst_updates"], group["updates"])
        self.assertEqual(out["burst_all_updates"], group["updates"])
        self.assertEqual(out["message"]["text"], "第一行\n仍是同一則\n第二則")


class CoachRepairTest(unittest.TestCase):
    def test_prompt_requests_only_original_sentence_indices(self):
        captured = {}

        def fake_chat(api_key, model, system, contents, **kwargs):
            captured.update(api_key=api_key, model=model, system=system,
                            contents=contents, kwargs=kwargs)
            return "1, 3, 4, 5"

        cfg = SimpleNamespace(gemini_api_key="k", gemini_model="m")
        co = coachmod.Coach(cfg)
        parts = ["有點忙？", "不要使用我一樣的貼圖"]
        with mock.patch("telegram_monitor.coach.gemini.generate_chat", side_effect=fake_chat) as gen:
            got = co.voice_burst_repair(parts, SCREENSHOT_DRAFT)

        gen.assert_called_once()
        self.assertEqual(got, KEEP)
        self.assertLessEqual(captured["kwargs"]["temperature"], 0.2)
        self.assertIn("句子選擇器", captured["system"])
        self.assertIn("編號", captured["system"])
        self.assertIn("重複", captured["system"])
        payload = "\n".join(p["text"] for turn in captured["contents"] for p in turn.get("parts", []))
        self.assertIn(parts[0], payload)
        self.assertIn(parts[1], payload)
        self.assertIn("1. 嗯，我有點那樣感覺。", payload)

    def test_non_index_model_output_is_rejected(self):
        cfg = SimpleNamespace(gemini_api_key="k", gemini_model="m")
        co = coachmod.Coach(cfg)
        with mock.patch("telegram_monitor.coach.gemini.generate_chat", return_value="我建議留第一句"):
            self.assertIsNone(co.voice_burst_repair(["a", "b"], "第一句。第二句。"))


if __name__ == "__main__":
    unittest.main()
