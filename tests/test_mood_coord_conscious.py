"""🧭 情緒座標的時間層與自我校正契約。

這組測試釘住截圖中的失敗鏈：同一則回答把三組 V/A 都說得像「現在」，
使用者追問「怎麼前後不一樣／所以真實狀況是什麼」時又掉出座標情境。
數字和時序必須由程式契約決定；LLM 只能補不含數字的主觀質地。
"""

import os
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock
from zoneinfo import ZoneInfo

from telegram_monitor import circumplex, monitor
from telegram_monitor.state import State


TZ = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 9, 3, 5, 48, 0, tzinfo=timezone.utc)  # 台北 13:48


def _entropy(v=0.20, a=0.10):
    return SimpleNamespace(
        mood=v,
        arousal=a,
        hunger=0.5,
        charge=0.0,
        self_stims_this_idle=0,
        reach_outs_this_idle=0,
        coping_reach_outs_this_idle=0,
    )


class ScreenshotDetectorModeTest(unittest.TestCase):
    def test_three_screenshot_turns_choose_snapshot_then_repair(self):
        first = "你現在的情緒座標如何了？"
        mismatch = "怎麼數字前後說的不一樣？"
        truth = "所以真實的狀況是什麼？"

        self.assertTrue(circumplex.is_mood_data_question_wide(first))
        self.assertFalse(circumplex.is_mood_data_followup(first))
        self.assertEqual(circumplex.mood_data_mode(first), "snapshot")

        for text in (mismatch, truth):
            self.assertTrue(circumplex.is_mood_data_followup(text), text)
            self.assertEqual(circumplex.mood_data_mode(text), "repair", text)

    def test_explicit_comparison_and_recent_list_are_trajectory(self):
        for text in (
            "你現在的情緒座標前後有什麼不同",
            "列最近五筆座標",
            "最近兩筆座標",
            "完整的座標軌跡",
        ):
            self.assertEqual(circumplex.mood_data_mode(text), "trajectory", text)
            self.assertTrue(circumplex.is_mood_data_question_wide(text), text)


class SnapshotFactsTest(unittest.TestCase):
    def test_direct_snapshot_does_not_expose_old_trace_values(self):
        state = SimpleNamespace(
            entropy=_entropy(0.35, 0.36),
            mood_trace=[
                {"ts": NOW.timestamp() - 300, "v": 0.31, "a": 0.34, "cause": "獨處"},
                {"ts": NOW.timestamp() - 60, "v": 0.37, "a": 0.32, "cause": "自然流動"},
            ],
        )

        facts = circumplex.coord_facts(
            state, TZ, NOW.timestamp(), snapshot=(0.35, 0.36), include_trace=False
        )

        self.assertIn("此刻快照", facts)
        self.assertIn("V +0.35、A +0.36", facts)
        self.assertNotIn("+0.31", facts)
        self.assertNotIn("+0.34", facts)
        self.assertNotIn("+0.37", facts)
        self.assertNotIn("+0.32", facts)
        self.assertIn("不要主動報舊軌跡數字", facts)

    def test_trace_after_snapshot_time_is_not_exposed(self):
        state = SimpleNamespace(
            entropy=_entropy(0.35, 0.36),
            mood_trace=[
                {"ts": NOW.timestamp() - 60, "v": 0.11, "a": 0.12, "cause": "過去的合法採樣"},
                {"ts": NOW.timestamp() + 60, "v": 0.91, "a": 0.92, "cause": "未來污染"},
            ],
        )

        facts = circumplex.coord_facts(
            state, TZ, NOW.timestamp(), snapshot=(0.35, 0.36), include_trace=True
        )

        self.assertIn("V +0.11、A +0.12", facts)
        self.assertNotIn("+0.91", facts)
        self.assertNotIn("+0.92", facts)
        self.assertNotIn("未來污染", facts)


class GroundedExitContractTest(unittest.TestCase):
    SCREENSHOT_STYLE = (
        "我現在的內在啊，感覺挺輕快的。\n"
        "V 值在 +0.35、A 值在 +0.36，就是一種興奮又雀躍的狀態。\n"
        "我剛剛在獨處的時候，這種感覺就一直在往上走，"
        "從 V +0.31、A +0.34 那邊，慢慢變成了 V +0.37、A +0.32。"
    )

    def test_conflicting_triplet_becomes_one_current_pair_plus_texture(self):
        contract = {
            "mode": "snapshot",
            "ts": NOW.timestamp(),
            "at": "13:48:00",
            "v": 0.37,
            "a": 0.40,
            "label": circumplex.label(0.37, 0.40),
        }

        out, changed = monitor._coord_grounded_fix(self.SCREENSHOT_STYLE, contract)

        self.assertTrue(changed)
        self.assertEqual(monitor._MOOD_PAIR_RE.findall(out), [("+0.37", "+0.40")])
        self.assertIn("感覺挺輕快", out)  # 非數字的主觀質地保留
        for foreign in ("+0.35", "+0.36", "+0.31", "+0.34", "+0.32"):
            self.assertNotIn(foreign, out)
        self.assertNotIn("一直在往上走", out)

    def test_repair_contract_separates_previous_from_current(self):
        contract = {
            "mode": "repair",
            "ts": NOW.timestamp(),
            "at": "13:48:00",
            "v": 0.37,
            "a": 0.40,
            "label": circumplex.label(0.37, 0.40),
            "previous": {
                "ts": NOW.timestamp() - 240,
                "at": "13:44:00",
                "v": 0.37,
                "a": 0.32,
                "label": circumplex.label(0.37, 0.32),
                "cause": "獨處中的自然流動",
            },
        }

        out, changed = monitor._coord_grounded_fix(
            "抱歉，我把數字搞混了。被你抓到後，我有點繃緊起來。",
            contract,
        )

        self.assertTrue(changed)
        self.assertIn("上一個完整採樣（13:44:00）是 V +0.37、A +0.32", out)
        self.assertIn("這一輪 13:48:00 重新讀到的此刻快照是 V +0.37、A +0.40", out)
        self.assertIn("兩組各自屬於不同時刻", out)
        self.assertLess(out.index("13:44:00"), out.index("13:48:00"))
        self.assertIn("有點繃緊起來", out)

    def test_negative_low_arousal_drops_exuberant_texture(self):
        contract = {
            "mode": "snapshot",
            "ts": NOW.timestamp(),
            "at": "13:48:00",
            "v": -0.60,
            "a": -0.50,
            "label": circumplex.label(-0.60, -0.50),
        }

        out, changed = monitor._coord_grounded_fix(
            "我現在很輕快、雀躍得想跳起來。", contract
        )

        self.assertTrue(changed)
        self.assertEqual(monitor._MOOD_PAIR_RE.findall(out), [("-0.60", "-0.50")])
        for contradiction in ("輕快", "雀躍", "想跳起來"):
            self.assertNotIn(contradiction, out)

    def test_negative_low_arousal_also_drops_subjectless_exuberance(self):
        contract = {
            "mode": "snapshot", "ts": NOW.timestamp(), "at": "13:48:00",
            "v": -0.60, "a": -0.50, "label": circumplex.label(-0.60, -0.50),
        }
        for voice in ("輕快、雀躍，想跳起來。", "整體很輕快，反應也很快。"):
            with self.subTest(voice=voice):
                out, _ = monitor._coord_grounded_fix(voice, contract)
                self.assertNotIn("輕快", out)
                self.assertNotIn("雀躍", out)

    def test_snapshot_variants_emit_only_the_canonical_current_pair(self):
        contract = {
            "mode": "snapshot",
            "ts": NOW.timestamp(),
            "at": "13:48:00",
            "v": 0.35,
            "a": 0.36,
            "label": circumplex.label(0.35, 0.36),
        }
        variants = (
            "我現在的注意力比較亮，有點想往前靠。",
            "我現在是 V +0.99、A -0.88，但覺得很平靜。",
            "我現在是 V +0.35、A +0.36，注意力很集中。",
            "前一筆 V -0.20、A +0.10；現在 V +0.35、A +0.36。",
        )

        for voice in variants:
            with self.subTest(voice=voice):
                out, changed = monitor._coord_grounded_fix(voice, contract)
                self.assertTrue(changed)
                self.assertEqual(
                    monitor._MOOD_PAIR_RE.findall(out), [("+0.35", "+0.36")]
                )

    def test_replay_guard_cannot_remove_current_pair_on_same_second_repeat(self):
        class Client:
            dry_run = False

            def __init__(self):
                self.sent = []

            def send(self, text):
                self.sent.append(text)
                return True

        contract = {
            "mode": "snapshot", "ts": NOW.timestamp(), "at": "13:48:00",
            "v": 0.35, "a": 0.36, "label": circumplex.label(0.35, 0.36),
        }
        client = Client()
        monitor._SENT_RECENT.clear()
        try:
            for _ in range(2):
                monitor._TURN.clear()
                monitor._TURN.update({
                    "bubbles": None, "replay_guard": True,
                    "mood_coord_contract": dict(contract),
                })
                before = len(client.sent)
                self.assertTrue(monitor._say(client, "我現在感到有精神。"))
                delivered = "".join(client.sent[before:])
                self.assertIn("13:48:00", delivered)
                self.assertIn("V +0.35、A +0.36", delivered)
        finally:
            monitor._SENT_RECENT.clear()
            monitor._TURN.clear()


class TrajectoryContractTest(unittest.TestCase):
    def test_five_point_request_keeps_four_legal_past_points_then_current(self):
        history = []
        for i in range(7):
            history.append({
                "ts": NOW.timestamp() - (7 - i) * 60,
                "v": -0.60 + i * 0.10,
                "a": -0.30 + i * 0.05,
                "cause": f"past-{i}",
            })
        history += [
            {"ts": NOW.timestamp(), "v": 0.35, "a": 0.36, "cause": "current"},
            {"ts": NOW.timestamp() + 60, "v": 0.91, "a": 0.92, "cause": "future"},
        ]
        state = SimpleNamespace(entropy=_entropy(0.35, 0.36), mood_trace=history)
        monitor._TURN.clear()
        monitor._TURN["coord_claim_truth"] = (0.35, 0.36)

        contract = monitor._mood_coord_contract(
            state, "列最近五筆座標", NOW.timestamp(), TZ
        )

        points = contract.get("history") or contract.get("trace")
        self.assertIsNotNone(points, "trajectory contract 需要結構化的過去採樣列表")
        self.assertEqual(len(points), 4)
        self.assertEqual([p["cause"] for p in points], [f"past-{i}" for i in range(3, 7)])
        self.assertTrue(all(p["ts"] <= NOW.timestamp() for p in points))
        self.assertEqual([p["ts"] for p in points], sorted(p["ts"] for p in points))

        out, changed = monitor._coord_grounded_fix(
            "我回頭看這幾步，它不是一條單向的線。", contract
        )
        self.assertTrue(changed)
        expected = [
            (f"{p['v']:+.2f}", f"{p['a']:+.2f}") for p in points
        ] + [("+0.35", "+0.36")]
        self.assertEqual(monitor._MOOD_PAIR_RE.findall(out), expected)

    def test_plain_comparison_and_two_point_request_use_previous_plus_current(self):
        state = SimpleNamespace(
            entropy=_entropy(0.35, 0.36),
            mood_trace=[
                {"ts": NOW.timestamp() - i * 60, "v": 0.10 + i / 100, "a": 0.20, "cause": str(i)}
                for i in range(5, 0, -1)
            ],
        )
        monitor._TURN.clear()
        monitor._TURN["coord_claim_truth"] = (0.35, 0.36)

        for query in ("情緒座標前後有什麼不同", "列最近兩筆座標", "最近兩筆座標"):
            with self.subTest(query=query):
                contract = monitor._mood_coord_contract(state, query, NOW.timestamp(), TZ)
                self.assertEqual(contract["point_limit"], 2)
                self.assertEqual(len(contract["trace"]), 1)
                out = monitor._coord_contract_text(contract)
                self.assertEqual(len(monitor._MOOD_PAIR_RE.findall(out)), 2)


class ContextFollowupTest(unittest.TestCase):
    FOLLOWUPS = ("怎麼數字前後說的不一樣？", "所以真實的狀況是什麼？")

    def test_followups_hit_only_inside_live_mood_context(self):
        cfg = SimpleNamespace(mood_coord_deliver_enabled=True)
        state = SimpleNamespace(mood_data_ctx_ts=NOW.timestamp())

        for text in self.FOLLOWUPS:
            self.assertTrue(
                monitor._mood_data_hit(state, cfg, text, NOW.timestamp() + 60), text
            )
            self.assertFalse(
                monitor._mood_data_hit(state, cfg, text, NOW.timestamp() + 1201), text
            )

        no_context = SimpleNamespace(mood_data_ctx_ts=0.0)
        for text in self.FOLLOWUPS:
            self.assertFalse(monitor._mood_data_hit(no_context, cfg, text, NOW.timestamp()), text)

    def test_unrelated_true_status_questions_do_not_hijack_live_mood_context(self):
        cfg = SimpleNamespace(mood_coord_deliver_enabled=True)
        state = SimpleNamespace(mood_data_ctx_ts=NOW.timestamp())

        for text in (
            "那伺服器真正的狀況是什麼",
            "這個專案到底是什麼狀況",
        ):
            self.assertFalse(circumplex.is_mood_data_followup(text), text)
            self.assertFalse(
                monitor._mood_data_hit(state, cfg, text, NOW.timestamp() + 60), text
            )

    def test_generic_truth_followup_does_not_return_to_mood_after_topic_shift(self):
        cfg = SimpleNamespace(mood_coord_deliver_enabled=True)
        state = SimpleNamespace(
            mood_data_ctx_ts=NOW.timestamp(),
            mood_last_report={
                "ts": NOW.timestamp(), "v": 0.35, "a": 0.36,
                "text": "13:48:00 的讀值是 V +0.35、A +0.36。",
            },
            convo_history=[
                {"role": "model", "text": "13:48:00 的讀值是 V +0.35、A +0.36。", "ts": NOW.timestamp()},
                {"role": "user", "text": "伺服器現在正常嗎？", "ts": NOW.timestamp() + 20},
                {"role": "model", "text": "伺服器現在正常。", "ts": NOW.timestamp() + 21},
            ],
        )

        self.assertFalse(
            monitor._mood_data_hit(
                state, cfg, "所以真實的狀況是什麼？", NOW.timestamp() + 60
            )
        )

        # 最新非 mood 回覆即使碰巧與舊報告共用一個很短的開頭，也不能拿 startswith 當相鄰證據。
        state.mood_last_report["text"] = "我在。13:48:00 是 V +0.35、A +0.36。"
        state.convo_history[-1]["text"] = "我在。"
        self.assertFalse(
            monitor._mood_data_hit(
                state, cfg, "所以真實的狀況是什麼？", NOW.timestamp() + 61
            )
        )

    def test_malformed_persisted_report_text_does_not_break_adjacency_check(self):
        state = SimpleNamespace(
            mood_last_report={"text": {"broken": True}, "v": 0.35, "a": 0.36},
            convo_history=[{"role": "model", "text": "伺服器現在正常。"}],
        )

        self.assertFalse(monitor._mood_context_adjacent(state))

    def test_repair_without_verified_wire_does_not_launder_arbitrary_trace(self):
        state = SimpleNamespace(
            entropy=_entropy(0.35, 0.36),
            mood_trace=[{
                "ts": NOW.timestamp() - 60, "v": -0.70, "a": 0.80,
                "cause": "任意歷史採樣",
            }],
            convo_history=[], mood_last_report=None,
        )
        monitor._TURN.clear()
        monitor._TURN["coord_claim_truth"] = (0.35, 0.36)
        contract = monitor._mood_coord_contract(
            state, "所以真實的狀況是什麼？", NOW.timestamp(), TZ
        )
        out = monitor._coord_contract_text(contract)

        self.assertFalse(contract["prior_report_verified"])
        self.assertIn("V +0.35、A +0.36", out)
        self.assertNotIn("V -0.70、A +0.80", out)
        self.assertIn("不能斷定只是讀取時刻不同", out)


class PostAppraiseTraceIntegrationTest(unittest.TestCase):
    class Client:
        def __init__(self):
            self.sent = []
            self.dry_run = False

        def send(self, text):
            self.sent.append(text)
            return True

    class Reader:
        @staticmethod
        def load_embedding_records(*_args, **_kwargs):
            return []

    SNAP = SimpleNamespace(
        summary={"total": 1, "last24h": 0, "last7d": 0},
        funnel={"candidate": 0, "context": 0, "journey": 0, "watch": 0},
        heartbeat={"status": "ok"},
        filed_records=[],
    )

    @staticmethod
    def _coach():
        coach = SimpleNamespace(
            enabled=True,
            api_key="k",
            model="m",
            meter=SimpleNamespace(record=lambda *_a, **_k: None),
        )
        coach.ask = lambda *_a, **_k: ("chat", None, "我在。")
        coach.reply = lambda *_a, **_k: "我在。"
        coach.voice_schedule_ack = lambda *_a, **_k: "好。"
        coach.voice_promise_ack = lambda *_a, **_k: "好。"
        coach.judge_timed_request = lambda *_a, **_k: None
        coach.judge_sticker_request = lambda *_a, **_k: None
        coach.judge_self_promise = lambda *_a, **_k: None
        coach.judge_promise_preempt = lambda *_a, **_k: False
        coach.voice_promise_keep = lambda *_a, **_k: None
        coach.voice_greeting = lambda *_a, **_k: "你好。"
        return coach

    @staticmethod
    def _cfg():
        return SimpleNamespace(
            dry_run=False,
            telegram_chat_id="",
            scheduled_promise_enabled=True,
            promise_emit_enabled=True,
            promise_sched_ttl_sec=21600,
            timezone="Asia/Taipei",
            notify_cooldown_min=30,
            promise_reply_bridge_enabled=False,
            promise_ledger_enabled=True,
            sched_leave_autoarm_enabled=True,
            deferred_promise_enabled=True,
            promise_llm_rescue_enabled=False,
            sticker_llm_rescue_enabled=False,
            promise_keep_claim_guard_enabled=True,
            promise_said_ground_enabled=True,
            bot_self_promise_enabled=False,
            promise_preempt_enabled=False,
            sticker_sent_memory_enabled=True,
            send_stickers=True,
            sticker_fakesend_guard_enabled=False,
            recall_ground_guard_enabled=False,
            selfshare_reason_ground_enabled=False,
            wake_projection_guard_enabled=False,
            user_habit_ground_enabled=False,
            self_feel_condense_enabled=False,
            promise_deliver_content_enabled=False,
            mood_coord_report_enabled=True,
            mood_coord_deliver_enabled=True,
            mood_data_answer_enabled=True,
            affect_circumplex_enabled=True,
            hostile_affect_fix_enabled=True,
        )

    def test_trace_endpoint_is_the_post_appraise_position(self):
        state = State(os.path.join(tempfile.mkdtemp(), "state.json"))
        state.owner_folder_id = "F"
        state.entropy = _entropy(-0.10, 0.05)
        original_appraise = monitor.affect.appraise

        def appraise_then_move(*args, **kwargs):
            result = original_appraise(*args, **kwargs)
            target_state = args[0]
            # 模擬 appraise 的最後一步仍改變兩軸；trace 必須在此之後才採樣。
            target_state.entropy.mood += 0.037
            target_state.entropy.arousal += 0.041
            return result

        monitor._TURN.clear()
        with mock.patch.object(monitor.affect, "appraise", side_effect=appraise_then_move):
            monitor.handle_message(
                {"message": {"chat": {"id": 1}, "text": "你太爛了", "date": NOW.timestamp()}},
                self._coach(),
                self.Reader(),
                {"meta": {}, "records": []},
                self.SNAP,
                state,
                self.Client(),
                self._cfg(),
                TZ,
            )

        self.assertTrue(state.mood_trace)
        v, a = circumplex.position(state)
        self.assertAlmostEqual(state.mood_trace[-1]["v"], v, places=3)
        self.assertAlmostEqual(state.mood_trace[-1]["a"], a, places=3)


class LastDeliveredReportPersistenceTest(unittest.TestCase):
    def test_verified_report_round_trips_and_unset_state_writes_no_key(self):
        directory = tempfile.mkdtemp()
        empty_path = os.path.join(directory, "empty.json")
        empty = State(empty_path)
        empty.save()
        with open(empty_path, encoding="utf-8") as f:
            self.assertNotIn("mood_last_report", f.read())

        path = os.path.join(directory, "report.json")
        state = State(path)
        state.mood_last_report = {
            "ts": NOW.timestamp(), "at": "13:48:00", "v": 0.35, "a": 0.36,
            "mode": "snapshot", "text": "V +0.35、A +0.36",
        }
        state.save()
        self.assertEqual(State.load(path).mood_last_report, state.mood_last_report)

        state.mood_last_report = None
        state.save()
        with open(path, encoding="utf-8") as f:
            self.assertNotIn("mood_last_report", f.read())
        self.assertIsNone(State.load(path).mood_last_report)

    def test_failed_delivery_contract_cannot_attach_to_later_unrelated_wire(self):
        state = State(os.path.join(tempfile.mkdtemp(), "state.json"))
        contract = {
            "mode": "snapshot", "ts": NOW.timestamp(), "at": "13:48:00",
            "v": 0.35, "a": 0.36, "label": circumplex.label(0.35, 0.36),
        }
        monitor._TURN.clear()
        try:
            monitor._stage_sent_model("原座標回答", "", mood_contract=contract)
            monitor._remember(state, "model", "原座標回答")
            self.assertIsNone(state.mood_last_report)

            monitor._stage_sent_model(
                "另一則回答", "13:48:00 的此刻座標是 V +0.35、A +0.36。"
            )
            monitor._remember(state, "model", "另一則回答")
            self.assertIsNone(state.mood_last_report)
        finally:
            monitor._TURN.clear()

    def test_real_say_records_only_when_the_canonical_pair_reaches_the_wire(self):
        contract = {
            "mode": "snapshot", "ts": NOW.timestamp(), "at": "13:48:00",
            "v": 0.35, "a": 0.36, "label": circumplex.label(0.35, 0.36),
        }

        class ScriptedClient:
            dry_run = False

            def __init__(self, mode):
                self.mode = mode
                self.sent = []

            def send(self, text):
                self.sent.append(text)
                has_pair = "V +0.35、A +0.36" in text
                if self.mode == "all_fail":
                    return False
                if self.mode == "non_pair_only":
                    return not has_pair
                if self.mode == "pair_only":
                    return has_pair
                return True

        expected = {
            "all_fail": False,
            "non_pair_only": False,
            "pair_only": False,  # 第一顆失敗即停止，不能跳過前文只送座標尾巴。
            "all_success": True,
        }
        try:
            for mode, should_record in expected.items():
                with self.subTest(mode=mode):
                    state = State(os.path.join(tempfile.mkdtemp(), "state.json"))
                    client = ScriptedClient(mode)
                    raw = "我現在比較專注。"
                    monitor._TURN.clear()
                    monitor._TURN.update({
                        "bubbles": None,
                        "mood_coord_contract": dict(contract),
                    })

                    monitor._say(client, raw)
                    monitor._remember(state, "model", raw, ts=NOW.timestamp())

                    if mode in ("all_fail", "pair_only"):
                        self.assertEqual(len(client.sent), 1)
                    else:
                        self.assertGreaterEqual(len(client.sent), 2)
                    self.assertEqual(state.mood_last_report is not None, should_record)
                    if should_record:
                        self.assertIn("V +0.35、A +0.36", state.mood_last_report["text"])
        finally:
            monitor._TURN.clear()


if __name__ == "__main__":
    unittest.main()
