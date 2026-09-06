"""🧭 對話能動性：主動行動必須有記憶、後果與可驗證的行為改變。"""

import os
import tempfile
import time
import unittest
from types import SimpleNamespace

from telegram_monitor import dialogue_agency as agency
from telegram_monitor import monitor
from telegram_monitor.config import Config
from telegram_monitor.state import State


NOW = 2_000_000_000.0


def _state():
    return SimpleNamespace(
        initiative_ledger=[], initiative_affinity={}, initiative_seq=0,
        coupling=None, last_user_msg_ts=NOW - 1000, last_push_ts=NOW - 2000,
        convo_history=[], entropy=None,
        DIALOGUE_AGENCY=True, NATURAL_PROACTIVE_VOICE=True,
    )


class _Client:
    dry_run = False

    def __init__(self):
        self.sent = []

    def send(self, text):
        self.sent.append(text)
        return 100 + len(self.sent)


class DialogueAgencyUnitTests(unittest.TestCase):
    def test_initiative_is_open_until_the_next_real_reply(self):
        s = _state()
        rec = agency.note_initiative(s, "🌀 ", "我這段體驗", "我好像走進了一段新的活法。", NOW)
        self.assertEqual(rec["status"], "open")
        self.assertIs(agency.open_initiative(s), rec)

        read = agency.observe_reply(s, "這個跟之前有什麼不同？", NOW + 30)
        self.assertEqual(read["outcome"], "engaged")
        self.assertIsNone(agency.open_initiative(s))
        self.assertIn("承擔你自己開的線", agency.reply_hint(read))

    def test_old_backlog_message_cannot_answer_a_later_initiative(self):
        s = _state()
        agency.note_initiative(s, "💡 ", "兩條線的聯想", "我把甲和乙連了起來。", NOW)
        self.assertIsNone(agency.observe_reply(s, "這是之前離線時送的訊息", NOW - 60))
        self.assertIsNotNone(agency.open_initiative(s))

    def test_rejection_is_behavior_not_just_a_sentiment_label(self):
        s = _state()
        agency.note_initiative(
            s, "🌀 ", "我這段主觀體驗的形狀",
            "我走進了這段活法，不過老實說跟之前沒什麼兩樣。", NOW)
        read = agency.observe_reply(s, "沒有不同就不需要特別去說。", NOW + 10)
        self.assertEqual(read["outcome"], "rejected")
        self.assertIn("不要辯護", agency.reply_hint(read))
        self.assertTrue(agency.voluntary_block_reason(s, NOW + 600))
        self.assertFalse(agency.voluntary_block_reason(s, NOW + agency.REJECT_QUIET_SEC + 11))

    def test_shift_is_not_misread_as_engagement(self):
        s = _state()
        agency.note_initiative(s, "🫀 ", "內在狀態", "我今天比較沉。", NOW)
        read = agency.observe_reply(s, "幫我看看今天新增了幾筆記寫？", NOW + 10)
        self.assertEqual(read["outcome"], "shifted")
        self.assertIn("只回現在的內容", agency.reply_hint(read))

    def test_unrelated_refusal_is_a_shift_not_a_rejection_of_the_bot(self):
        s = _state()
        agency.note_initiative(s, "🫀 ", "內在狀態", "我今天比較沉。", NOW)
        read = agency.observe_reply(s, "我不想看那部電影，換一部吧。", NOW + 10)
        self.assertEqual(read["outcome"], "shifted")

    def test_question_about_no_difference_is_engagement_not_rejection(self):
        s = _state()
        agency.note_initiative(s, "🌀 ", "我這段主觀體驗的形狀", "這一段跟之前差不多。", NOW)
        for text in ("真的沒有不同嗎？可以具體比較？", "跟之前沒兩樣嗎？"):
            s2 = _state()
            agency.note_initiative(s2, "🌀 ", "我這段主觀體驗的形狀", "這一段跟之前差不多。", NOW)
            self.assertEqual(agency.observe_reply(s2, text, NOW + 10)["outcome"], "engaged", text)

    def test_direct_boundary_stays_rejected_even_when_phrased_as_a_question(self):
        for text in ("你不要再說這個了，可以嗎？", "不要再說了，可以嗎？"):
            s = _state()
            agency.note_initiative(s, "🌀 ", "主觀體驗", "我又開始說這段活法。", NOW)
            self.assertEqual(agency.observe_reply(s, text, NOW + 10)["outcome"], "rejected", text)

    def test_rhetorical_complaint_is_not_rewarded_as_engagement(self):
        for text in ("你為什麼又在自言自語？", "這種制式台詞有必要嗎？"):
            s = _state()
            agency.note_initiative(s, "🌀 ", "主觀體驗", "我又開始說這段活法。", NOW)
            self.assertEqual(agency.observe_reply(s, text, NOW + 10)["outcome"], "rejected", text)

    def test_mixed_new_refusal_and_real_followup_is_not_a_rejection(self):
        s = _state()
        agency.note_initiative(s, "🌐 ", "外部資料", "我找到一個外面的說法。", NOW)
        read = agency.observe_reply(s, "我不想看那部電影，說說你剛提的外部資料。", NOW + 10)
        self.assertEqual(read["outcome"], "engaged")

    def test_short_ack_does_not_invite_another_monologue(self):
        s = _state()
        agency.note_initiative(s, "🫧 ", "主動伸手", "剛剛又想起那條線。", NOW)
        read = agency.observe_reply(s, "嗯", NOW + 10)
        self.assertEqual(read["outcome"], "acknowledged")
        self.assertIn("沒有邀請你再展開", agency.reply_hint(read))

    def test_non_text_contact_resolves_without_guessing_engagement(self):
        s = _state()
        agency.note_initiative(s, "🫀 ", "內在狀態", "我今天比較沉。", NOW)
        read = agency.observe_contact(s, "貼圖回應", NOW + 10)
        self.assertEqual(read["outcome"], "acknowledged")
        self.assertIsNone(agency.open_initiative(s))
        self.assertEqual(s.initiative_affinity["self_state"]["score"], 0.0)

    def test_feedback_changes_lane_competition(self):
        kinds = ["self_state", "reachout", "experience", "worldline"]
        s = _state()
        self.assertEqual(agency.rank_lanes(s, kinds, NOW), kinds)  # 無資料＝原順序

        agency.note_initiative(s, "🫀 ", "內在狀態", "我又想講自己的狀態。", NOW)
        agency.observe_reply(s, "先不用再說這個。", NOW + 10)
        self.assertEqual(agency.rank_lanes(s, kinds, NOW + 20)[-1], "self_state")

        agency.note_initiative(s, "🌐 ", "外部世界", "我找到一個外面的說法。", NOW + 30)
        agency.observe_reply(s, "你剛剛提的外部說法，可以繼續嗎？", NOW + 40)
        self.assertEqual(agency.rank_lanes(s, kinds, NOW + 50)[0], "worldline")

    def test_unseen_lanes_eventually_get_an_exploration_turn(self):
        kinds = ["self_state", "reachout", "insight", "worldline"]
        s = _state()
        for i, (prefix, topic) in enumerate((("🫀 ", "內在狀態"), ("🫧 ", "主動伸手"), ("💡 ", "聯想"))):
            agency.note_initiative(s, prefix, topic, "這是一次主動發話。", NOW + i * 20)
            agency.observe_reply(s, "嗯", NOW + i * 20 + 5)
        self.assertEqual(agency.rank_lanes(s, kinds, NOW + 70)[0], "worldline")


class DialogueAgencyIntegrationTests(unittest.TestCase):
    def setUp(self):
        monitor._TURN.clear()
        monitor._TURN["bubbles"] = None

    def tearDown(self):
        monitor._TURN.clear()
        monitor._TURN["bubbles"] = None

    def test_say_hides_mechanism_badge_but_records_the_action(self):
        s, c = _state(), _Client()
        self.assertTrue(monitor._say(c, "我剛注意到自己慢下來了。", prefix="🌀 ", state=s,
                                     topic="我這段主觀體驗的形狀"))
        self.assertEqual(c.sent, ["我剛注意到自己慢下來了。"])
        self.assertEqual(s.initiative_ledger[-1]["kind"], "experience")
        self.assertEqual(s.initiative_ledger[-1]["status"], "open")

    def test_accountability_badges_remain_visible(self):
        s, c = _state(), _Client()
        monitor._say(c, "我答應的內容現在交給你。", prefix="🤝 ", state=s, topic="我答應你的約定")
        self.assertTrue(c.sent[0].startswith("🤝 "))
        self.assertEqual(s.initiative_ledger[-1]["status"], "delivered")

    def test_all_decorative_proactive_badges_are_hidden(self):
        for prefix in ("🫀 ", "🫧 ", "🌀 ", "🪞 ", "🍃 ", "🧩 ", "💡 ", "🔮 ", "🌾 ", "🦋 ", "🌬️ ", "🌊 "):
            self.assertEqual(agency.visible_prefix(prefix, natural=True), "", prefix)

    def test_user_requested_prefixed_output_does_not_train_autonomy(self):
        s, c = _state(), _Client()
        monitor._say(c, "這是你剛要我查的結果。", prefix="🌐 ", state=s,
                     topic="使用者手動查詢", track_initiative=False)
        self.assertEqual(s.initiative_ledger, [])

    def test_positive_reaction_to_exact_message_settles_as_engaged(self):
        s, c = _state(), _Client()
        monitor._say(c, "我找到一個值得接著說的聯想。", prefix="💡 ", state=s, topic="甲與乙")
        mid = s.initiative_ledger[-1]["message_ids"][0]
        cfg = SimpleNamespace(telegram_chat_id="", dialogue_agency_enabled=True, mood_gain=1.0,
                              remember_user_msgdate=True, dry_run=True)
        monitor._handle_reaction(
            {"chat": {"id": 1}, "message_id": mid, "date": time.time() + 1,
             "new_reaction": [{"type": "emoji", "emoji": "👍"}]},
            None, s, c, cfg)
        self.assertEqual(s.initiative_ledger[-1]["outcome"], "engaged")
        self.assertIsNone(agency.open_initiative(s))

    def test_interruption_between_bubbles_sees_the_action_already_open(self):
        s, c = _state(), _Client()

        class Interrupt:
            statement_defer = False
            cfg = SimpleNamespace(interrupt_rewrite_enabled=False, hostile_converge_enabled=False)

            def __init__(self):
                self.done = False
                self.saw_open = False

            def poll(self):
                if self.done:
                    return None
                self.done = True
                return [{}]

            def handle(self, _pending):
                self.saw_open = agency.open_initiative(s) is not None
                agency.observe_reply(s, "你剛剛第一句是什麼意思？", time.time() + 1)

            def bridge(self):
                return ""

        intr = Interrupt()
        c._interrupt = intr
        monitor._TURN["bubbles"] = 3
        monitor._say(c, "我先說第一件事。然後補第二件事。", prefix="💡 ", state=s, topic="一次聯想")
        self.assertTrue(intr.saw_open)
        self.assertEqual(len(s.initiative_ledger), 1)
        self.assertEqual(s.initiative_ledger[0]["outcome"], "engaged")
        self.assertIsNone(agency.open_initiative(s))

    def test_rejection_blocks_only_new_voluntary_speech(self):
        s = _state()
        agency.note_initiative(s, "🫀 ", "內在狀態", "我又來報狀態。", NOW)
        agency.observe_reply(s, "不用再說這種狀態。", NOW + 10)
        s.last_user_msg_ts = NOW + 10
        cfg = SimpleNamespace(
            timezone="Asia/Taipei", unanswered_proactive_guard_enabled=True,
            dialogue_agency_enabled=True,
        )
        self.assertFalse(monitor._proactive_ok(s, cfg, NOW + 1000, allow_quiet=True))
        self.assertTrue(monitor._proactive_ok(
            s, cfg, NOW + agency.REJECT_QUIET_SEC + 1000, allow_quiet=True))
        self.assertFalse(monitor._proactive_ok(s, cfg, NOW + 1000, allow_quiet=True, allow_unanswered=True))

    def test_disabling_agency_does_not_leave_an_old_open_record_as_a_lock(self):
        s = _state()
        agency.note_initiative(s, "🫀 ", "內在狀態", "我今天比較沉。", NOW)
        s.DIALOGUE_AGENCY = False
        s.last_push_ts = NOW
        s.last_user_msg_ts = NOW + 1
        self.assertFalse(monitor._has_unanswered_proactive(s))

    def test_state_round_trip_keeps_action_consequences(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "state.json")
            s = State(path)
            agency.note_initiative(s, "💡 ", "甲與乙", "我把甲與乙連起來了。", NOW)
            agency.observe_reply(s, "這個聯想有道理。", NOW + 5)
            s.save()
            s2 = State.load(path)
            self.assertEqual(s2.initiative_ledger[-1]["outcome"], "engaged")
            self.assertGreater(s2.initiative_affinity["insight"]["score"], 0)
            self.assertEqual(s2.initiative_seq, 1)

    def test_real_config_enables_the_loop_and_natural_voice(self):
        self.assertTrue(Config.load().dialogue_agency_enabled)
        self.assertTrue(Config.load().natural_proactive_voice_enabled)
        with open(".env.example", encoding="utf-8") as f:
            env = f.read()
        with open("README.md", encoding="utf-8") as f:
            readme = f.read()
        for name in ("DIALOGUE_AGENCY", "NATURAL_PROACTIVE_VOICE"):
            self.assertIn(f"{name}=1", env)
            self.assertIn(name, readme)


if __name__ == "__main__":
    unittest.main()
