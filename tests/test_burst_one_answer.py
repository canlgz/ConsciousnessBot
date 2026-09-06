"""🌊 §1.73 一波連發＝整體回一次（BURST_ONE_ANSWER）：3–12 則短訊也不是逐則各答。

截圖根因：使用者 10:15 連發「你氣噗噗喔」「這麼愛現」→ 10:16 七顆泡泡把兩句的意思各解讀兩遍
（「氣噗噗？」/「我沒有氣噗噗喔」、「有點愛現耶」/「你又說我愛現了」）。根因：連發合併只把多則
接成多行文字、從沒告訴 LLM「這是同一波」——LLM 逐句各答一遍、收尾又整體重答一輪（語意重複、
§1.71 逐字守門攔不到）。修：保留原始訊息邊界；同 route 整波提示，跨 route 則隔離演算後一次提交；
重複收斂只能擷取原句，長 wire 另依 Telegram 上限安全分塊。全 stub。
"""

import os
import re
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from zoneinfo import ZoneInfo

from telegram_monitor import config, lifeloop, monitor, notifier as notifiermod
from telegram_monitor.state import State

TZ = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 7, 25, 2, 15, 0, tzinfo=timezone.utc)


def _group(texts):
    ups = [{"update_id": 100 + i, "message": {"chat": {"id": 1}, "message_id": 200 + i,
                                              "date": NOW.timestamp() + i, "text": t}}
           for i, t in enumerate(texts)]
    return {"type": "text", "updates": ups}


class BurstNTest(unittest.TestCase):
    def test_coalesced_carries_burst_n(self):
        u = monitor.build_coalesced_update(_group(["你氣噗噗喔", "這麼愛現"]))
        self.assertEqual(u.get("burst_n"), 2)
        self.assertEqual(u["message"]["text"], "你氣噗噗喔\n這麼愛現")

    def test_hint_on_multi(self):
        cfg = SimpleNamespace(burst_one_answer_enabled=True)
        h = monitor._burst_one_hint(cfg, {"burst_n": 2})
        self.assertIn("同一波", h)
        self.assertIn("回應**一次**", h)
        self.assertIn("再回一輪", h)                            # 明令別回完又重回（截圖症狀）
        self.assertIn("把它們連成一段自然的話", h)             # paced 缺欄＝舊 prompt 原樣
        self.assertNotIn("送出層", h)

    def test_paced_hint_only_changes_generation_when_its_flag_is_on(self):
        update = {"burst_n": 3}
        legacy = monitor._burst_one_hint(
            SimpleNamespace(burst_one_answer_enabled=True, burst_paced_bubbles_enabled=False),
            update,
        )
        paced = monitor._burst_one_hint(
            SimpleNamespace(burst_one_answer_enabled=True, burst_paced_bubbles_enabled=True),
            update,
        )
        self.assertIn("把它們連成一段自然的話", legacy)
        self.assertNotIn("句子長短自然交錯", legacy)
        self.assertNotIn("分成幾顆泡泡", legacy)
        self.assertIn("句子長短自然交錯", paced)
        self.assertIn("分成幾顆泡泡", paced)
        self.assertNotIn("把它們連成一段自然的話", paced)

    def test_hint_silent_on_single(self):
        cfg = SimpleNamespace(burst_one_answer_enabled=True)
        self.assertEqual(monitor._burst_one_hint(cfg, {"burst_n": 1}), "")
        self.assertEqual(monitor._burst_one_hint(cfg, {}), "")   # 一般單則 update 無此欄＝''

    def test_post_intent_cost_tightening_is_shared_with_burst_planner(self):
        state = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        update = monitor.build_coalesced_update(_group(["花了好多 token，真浪費", "花費多少？"]))
        cfg = SimpleNamespace(burst_one_answer_enabled=True, cost_query_tighten_enabled=True)
        self.assertEqual(monitor._burst_route_kinds(update, state, cfg), ["fact_or_chat", "cost"])
        self.assertTrue(monitor._burst_needs_route_transaction(update, state, cfg))

    def test_hint_flag_off_bitwise(self):
        self.assertEqual(monitor._burst_one_hint(SimpleNamespace(), {"burst_n": 3}), "")


class EndToEndTest(unittest.TestCase):
    """合成 update 進 handle_message → 對話 lane 的 extra_system 真的帶到「同一波」指令。"""

    class Cl:
        def __init__(self):
            self.sent, self.dry_run = [], False

        def send(self, text):
            self.sent.append(text)
            return True

    SNAP = SimpleNamespace(summary={"total": 1, "last24h": 0, "last7d": 0, "streak": 0, "last_write": None},
                           funnel={"candidate": 0, "context": 0, "journey": 0, "watch": 0},
                           heartbeat={"status": "ok"}, filed_records=[])
    BOOM = SimpleNamespace(load_embedding_records=lambda *_: [])

    def setUp(self):
        monitor._TURN.clear()
        monitor._TURN["bubbles"] = None

    def _coach(self, seen, draft="好啦，我不氣。", repair=None, ask_data=None):
        c = SimpleNamespace(enabled=True, api_key="k", model="m",
                            meter=SimpleNamespace(record=lambda *a, **k: None))

        def ask(*a, **k):
            seen["extra"] = k.get("extra_system") or ""
            seen["hint"] = "\n".join(x for x in (k.get("mood_hint") or "", seen["extra"]) if x)
            return ("chat", ask_data, draft)

        def reply(*a, **k):
            seen["extra"] = k.get("extra_system") or ""
            seen["hint"] = "\n".join(x for x in (k.get("mood_hint") or "", seen["extra"]) if x)
            return draft

        c.ask, c.reply = ask, reply
        c.voice_schedule_ack = lambda q, w, h, sticker_hint="": "好。"
        c.voice_promise_ack = lambda q, h: "好。"
        c.judge_timed_request = lambda t: None
        c.judge_sticker_request = lambda t: None
        c.judge_self_promise = lambda t: None
        c.judge_promise_preempt = lambda *a, **k: False
        c.voice_promise_keep = lambda *a, **k: None
        c.voice_greeting = lambda *a, **k: "嗨！"
        if repair is not None:
            c.voice_burst_repair = repair
        return c

    def _run_detail(self, on=True, texts=None, draft="好啦，我不氣。", repair=None, ask_data=None):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        seen = {}
        u = monitor.build_coalesced_update(_group(texts or ["你氣噗噗喔", "這麼愛現"]))
        cfg = SimpleNamespace(dry_run=False, telegram_chat_id="", timezone="Asia/Taipei",
                              notify_cooldown_min=30, burst_one_answer_enabled=on)
        client = self.Cl()
        monitor.handle_message(u, self._coach(seen, draft=draft, repair=repair, ask_data=ask_data), self.BOOM,
                               {"meta": {}, "records": []}, self.SNAP, s, client, cfg, TZ)
        return SimpleNamespace(extra=seen.get("hint") or seen.get("extra") or "", sent=client.sent, state=s)

    def _run(self, on=True):
        return self._run_detail(on=on).extra

    def test_hint_reaches_lane(self):
        result = self._run_detail()
        self.assertEqual(len(result.sent), 1)                    # 每個 part 保留自己的 handler，最後只交付一個 wire

    def test_hint_reaches_smalltalk_lane_too(self):
        result = self._run_detail(texts=["嗯", "好"])
        self.assertEqual(len(result.sent), 1)                     # 結構性／附和 route 逐項演算，仍只交付一則

    def test_flag_off_no_hint(self):
        self.assertNotIn("同一波", self._run(on=False))         # 旗標關＝現狀

    def test_flag_off_does_not_run_repair(self):
        repair = mock.Mock(return_value="不該被使用。")
        self._run_detail(
            on=False,
            draft="第一句。第二句。第三句。第四句。第五句。第六句。",
            repair=repair,
        )
        repair.assert_not_called()

    def test_multi_intent_burst_repairs_to_one_wire_message_without_losing_intents(self):
        repair_seen = {}

        def repair(parts, draft):
            repair_seen["parts"] = parts
            repair_seen["draft"] = draft
            return [1, 3, 5]

        result = self._run_detail(
            texts=["有點忙？", "不要使用我一樣的貼圖"],
            draft=("嗯，我有點忙。好，我記下來了。我不會使用跟你一樣的貼圖。"
                   "其實我只是有點忙。好，我收到了。我會避開跟你用一樣的貼圖。"),
            repair=repair,
        )

        self.assertEqual(len(result.sent), 1)                       # multi-message user turn＝一顆 Telegram 泡泡
        self.assertIn("忙", result.sent[0])                         # 第一個意圖仍在
        self.assertIn("一樣的貼圖", result.sent[0])                  # 第二個意圖仍在

    def test_five_intents_still_send_one_bubble_without_fixed_sentence_cap(self):
        texts = ["有點忙？", "不要使用我一樣的貼圖", "也不要逐句重複我",
                 "先回真正不同的重點", "整段只回一次"]
        repair_seen = {}

        def repair(parts, draft):
            repair_seen["parts"] = parts
            repair_seen["draft"] = draft
            # 只提案留原稿前五句；程式再驗證後兩句真是近重複。
            return [1, 2, 3, 4, 5]

        result = self._run_detail(
            texts=texts,
            draft=("我剛才有點忙。我會避開跟你用同一張貼圖。我不會逐句重複。"
                   "我會先回真正不同的重點。這整段只回一次。"
                   "我剛才也有點忙。我也會避開跟你用同一張貼圖。"),
            repair=repair,
        )

        # planner 不必為了固定句數呼叫 repair；重點是五個不同意圖都留著且只送一次。
        self.assertEqual(len(result.sent), 1)                       # 五句仍是同一 user turn，只送一顆 wire bubble
        wire = result.sent[0]
        # 近義句不能由 SequenceMatcher 猜測刪除；即使保守多留，也不能漏掉五個不同要求。
        for expected in ("有點忙", "同一張", "逐句重複", "不同的重點", "只回一次"):
            self.assertIn(expected, wire)                           # 五個不同意圖一個都沒被固定三句上限裁掉

    def test_preempt_say_does_not_consume_main_one_wire_scope(self):
        # promise preempt 會在主答案前先 _say；舊的 _TURN.pop 契約會被它吃掉，導致主答案後段守門可再裂泡。
        def preempt(client, *args, **kwargs):
            monitor._say(client, "前置第一句。前置第二句。")

        with mock.patch.object(monitor, "_maybe_promise_preempt", side_effect=preempt):
            result = self._run_detail(
                draft="主答第一句完整內容。主答第二句完整內容。",
            )

        self.assertEqual(len(result.sent), 1)                       # transaction 把前置動作＋主答案原子併成一則
        self.assertIn("前置第一句", result.sent[0])
        self.assertIn("前置第二句", result.sent[0])
        self.assertIn("主答第一句", result.sent[0])
        self.assertIn("主答第二句", result.sent[0])

    def test_function_call_data_and_voice_share_one_wire_message(self):
        result = self._run_detail(
            texts=["解釋一下火星", "也說說你怎麼理解"],
            draft="我把這兩件事當成同一個問題回答。",
            ask_data="📚 已接地的資料塊",
        )
        self.assertEqual(len(result.sent), 1)
        self.assertIn("已接地的資料塊", result.sent[0])
        self.assertIn("同一個問題回答", result.sent[0])

    def test_long_data_reserves_room_for_complete_voice(self):
        voice = "這是完整的自然回覆，不可被 Telegram 截掉。"
        result = self._run_detail(
            texts=["查完整資料", "也說說你怎麼理解"],
            draft=voice,
            ask_data="資" * 5000,
        )
        self.assertEqual(len(result.sent), 1)
        self.assertLessEqual(monitor._utf16_units(result.sent[0]), 4096)
        self.assertIn("資料較長，已節錄", result.sent[0])
        self.assertTrue(result.sent[0].endswith(voice))

    def test_cross_route_greeting_and_clock_are_both_answered_once(self):
        result = self._run_detail(texts=["早安", "現在幾點？"])
        self.assertEqual(len(result.sent), 1)
        wire = result.sent[0]
        self.assertIn("嗨", wire)
        self.assertIn("🕐 現在", wire)
        self.assertLess(wire.index("嗨"), wire.index("🕐 現在"))
        self.assertEqual([e["role"] for e in result.state.convo_history[-2:]], ["user", "model"])
        self.assertEqual(result.state.convo_history[-2]["text"], "早安\n現在幾點？")

    def test_three_short_messages_across_routes_still_form_one_reply(self):
        result = self._run_detail(texts=["早安", "嗯", "現在幾點？"], draft="我有在聽。")
        self.assertEqual(len(result.sent), 1)
        wire = result.sent[0]
        for expected in ("嗨", "我有在聽", "🕐 現在"):
            self.assertIn(expected, wire)
        self.assertLess(wire.index("嗨"), wire.index("我有在聽"))
        self.assertLess(wire.index("我有在聽"), wire.index("🕐 現在"))

    def test_cross_route_later_part_sees_prior_users_but_no_speculative_model_drafts(self):
        texts = ["早安", "現在幾點？", "我今天被主管罵了，你覺得我該怎麼辦？"]
        update = monitor.build_coalesced_update(_group(texts))
        state = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        cfg = SimpleNamespace(dry_run=True, burst_one_answer_enabled=True)
        client, seen = self.Cl(), []

        def inner(part, _coach, _reader, _data, _snap, st, out, _cfg, _tz):
            seen.append([(e.get("role"), e.get("text")) for e in st.convo_history])
            text = part["message"]["text"]
            out.send("內部草稿：" + text)
            # 模擬真 handler 會把本 part 的 user/model 寫進 speculative clone；下一 part
            # 不得看見 model 草稿，卻必須看見前面真正的 user 原話。
            st.convo_history.extend([
                {"role": "user", "text": text, "ts": NOW.timestamp()},
                {"role": "model", "text": "尚未送出的 bot 草稿", "ts": NOW.timestamp()},
            ])

        with mock.patch.object(monitor, "_handle_message_inner", side_effect=inner):
            monitor._handle_burst_route_transaction(
                update, None, None, {}, self.SNAP, state, client, cfg, TZ)

        self.assertEqual(seen[0], [])
        self.assertEqual(seen[1], [("user", texts[0])])
        self.assertEqual(seen[2], [("user", texts[0]), ("user", texts[1])])
        self.assertFalse(any(role == "model" for history in seen for role, _text in history))

    def test_safe_short_conversation_is_generated_once_as_one_turn(self):
        texts = ["嗯", "我有點忙", "你覺得我該怎麼辦？"]
        update = monitor.build_coalesced_update(_group(texts))
        state = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        cfg = SimpleNamespace(dry_run=True, burst_one_answer_enabled=True)
        client, seen = self.Cl(), []

        def inner(part, _coach, _reader, _data, _snap, _state, out, _cfg, _tz):
            seen.append(part)
            out.send("我把你前面的停頓、現在的忙，和最後的問題一起讀了。")

        with mock.patch.object(monitor, "_handle_message_inner", side_effect=inner):
            monitor._handle_burst_route_transaction(
                update, None, None, {}, self.SNAP, state, client, cfg, TZ)

        self.assertEqual(len(seen), 1)                         # 不是先生三份草稿再用換行黏起來
        self.assertEqual(seen[0]["message"]["text"], "\n".join(texts))
        self.assertEqual(seen[0]["burst_n"], 3)
        self.assertEqual(client.sent, ["我把你前面的停頓、現在的忙，和最後的問題一起讀了。"])

    def test_non_slash_controls_can_never_enter_joined_conversation(self):
        state = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        cfg = SimpleNamespace(dry_run=True, burst_one_answer_enabled=True)
        cases = (
            ["敏感度 0.5", "現在呢？"],
            ["心跳 1.5", "你覺得呢？"],
            ["情緒座標如果有變動就主動回報", "另外也說說你怎麼想"],
            ["等一下再回答我", "另外也說說你怎麼想"],
            ["送我一張貼圖", "另外也說說你怎麼想"],
            ["這些貼圖都記住", "另外也說說你怎麼想"],
            ["回覆具體一點", "不要一直報數字"],
            ["明天十點你出現一下", "另外也說說你怎麼想"],
            ["我點哪一筆讚？", "另外也說說你怎麼想"],
            ["那個呢？", "另外也說說你怎麼想"],
            ["延長10分鐘", "另外也說說你怎麼想"],
        )
        for texts in cases:
            with self.subTest(texts=texts):
                update = monitor.build_coalesced_update(_group(texts))
                self.assertIsNone(monitor._burst_joined_conversation_kind(
                    update["burst_updates"], state, cfg))

    def test_cross_route_sticker_is_not_sent_outside_single_message_transaction(self):
        class StickerClient(self.Cl):
            def __init__(self, ok=True):
                super().__init__()
                self.ok = ok
                self.stickers = []

            def send_sticker(self, fid):
                self.stickers.append(fid)
                return self.ok

        def inner(part, _coach, _reader, _data, _snap, st, out, _cfg, _tz):
            if out.send_sticker("sticker-new"):
                monitor._record_sticker_sent(st, "sticker-new", NOW.timestamp())
            out.send("處理：" + part["message"]["text"])

        state = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        state.last_sticker_id = "sticker-old"
        update = monitor.build_coalesced_update(_group(["先看這件事", "再告訴我時間"]))
        cfg = SimpleNamespace(dry_run=False, burst_one_answer_enabled=True, react_cooldown_s=0)
        client = StickerClient()
        with mock.patch.object(monitor, "_handle_message_inner", side_effect=inner):
            monitor._handle_burst_route_transaction(
                update, None, None, {}, self.SNAP, state, client, cfg, TZ)

        self.assertEqual(client.stickers, [])                     # transaction 唯一外部動作是 sendMessage
        self.assertEqual(state.last_sticker_id, "sticker-old")  # capture 回 False，不虛構貼圖已送達
        self.assertEqual(len(client.sent), 1)

    def test_cross_route_sticker_client_is_never_called_even_if_it_would_fail(self):
        class StickerClient(self.Cl):
            def __init__(self):
                super().__init__()
                self.stickers = []

            def send_sticker(self, fid):
                self.stickers.append(fid)
                return False

        def inner(part, _coach, _reader, _data, _snap, st, out, _cfg, _tz):
            if out.send_sticker("sticker-new"):
                monitor._record_sticker_sent(st, "sticker-new", NOW.timestamp())
            out.send("不該先送出的文字")

        state = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        state.last_sticker_id = "sticker-old"
        state.convo_history = [{"role": "user", "text": "原歷史", "ts": NOW.timestamp() - 10}]
        update = monitor.build_coalesced_update(_group(["第一則", "現在幾點？"]))
        cfg = SimpleNamespace(dry_run=False, burst_one_answer_enabled=True)
        client = StickerClient()
        with mock.patch.object(monitor, "_handle_message_inner", side_effect=inner):
            monitor._handle_burst_route_transaction(
                update, None, None, {}, self.SNAP, state, client, cfg, TZ)

        self.assertEqual(client.stickers, [])                     # speculative capture 不穿透 send_sticker
        self.assertEqual(len(client.sent), 1)                     # 只送合成文字
        self.assertEqual(state.last_sticker_id, "sticker-old")  # clone 未提交，假成功不污染真狀態
        self.assertIn("不該先送出的文字", client.sent[0])

    def test_cross_route_reaction_is_suppressed_to_keep_single_external_action(self):
        class ReactClient(self.Cl):
            def __init__(self):
                super().__init__()
                self.reactions = []

            def set_reaction(self, mid, emoji):
                self.reactions.append((mid, emoji))
                return True

        def inner(part, _coach, _reader, _data, _snap, st, out, cfg, _tz):
            text = part["message"]["text"]
            monitor._maybe_react(part, text, st, out, cfg)       # capture 會攔，不逐則真點 reaction
            out.send("處理：" + text)

        state = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        update = monitor.build_coalesced_update(_group(["第一則", "第二則", "第三則"]))
        cfg = SimpleNamespace(dry_run=False, burst_one_answer_enabled=True, react_cooldown_s=0)
        client = ReactClient()
        with mock.patch.object(monitor, "_handle_message_inner", side_effect=inner), \
                mock.patch.object(monitor.reaction, "pick_reaction", return_value="🥰"):
            monitor._handle_burst_route_transaction(
                update, None, None, {}, self.SNAP, state, client, cfg, TZ)

        self.assertEqual(client.reactions, [])                     # one-wire transaction 不再另做第二個 Telegram action

    def test_two_same_route_schedules_keep_distinct_times_and_behaviors(self):
        result = self._run_detail(texts=["明天八點叫我起床", "明天九點提醒我吃藥"])
        self.assertEqual(len(result.sent), 1)
        pending = [p for p in result.state.scheduled_promises if p.get("status") == "pending"]
        self.assertEqual(len(pending), 2)
        self.assertEqual([p["behavior"] for p in pending], ["叫他起床", "提醒他吃藥"])
        self.assertEqual(pending[1]["target_ts"] - pending[0]["target_ts"], 3600)

    def test_pending_skill_confirmation_and_clock_both_survive_one_burst(self):
        state = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        state.owner_folder_id = "F"
        state.skill_pending = {
            "route_kind": "fact_or_chat", "topic_tag": "貼圖",
            "prompt": "不要使用跟我一樣的貼圖", "trigger": "always", "ts": time.time(),
        }
        update = monitor.build_coalesced_update(_group(["好", "現在幾點？"]))
        cfg = SimpleNamespace(dry_run=False, telegram_chat_id="", timezone="Asia/Taipei",
                              notify_cooldown_min=30, burst_one_answer_enabled=True)
        client, seen = self.Cl(), {}
        monitor.handle_message(update, self._coach(seen), self.BOOM, {"meta": {}, "records": []},
                               self.SNAP, state, client, cfg, TZ)
        self.assertEqual(len(client.sent), 1)
        self.assertIn("記起來", client.sent[0])
        self.assertIn("🕐 現在", client.sent[0])
        self.assertIsNone(state.skill_pending)
        self.assertTrue(any(e.get("kind") == "skill" for e in state.engrams))

    def test_later_cancel_removes_due_promise_bridge_from_same_burst(self):
        state = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        state.scheduled_promises = [{
            "target_ts": NOW.timestamp() - 120,
            "made_ts": NOW.timestamp() - 600,
            "made_text": "兩分鐘後叫我起床",
            "action": "兩分鐘後叫我起床",
            "behavior": "叫他起床",
            "fulfilled": False,
            "status": "pending",
        }]
        update = monitor.build_coalesced_update(_group(["先說說看", "不用叫我了"]))
        cfg = SimpleNamespace(
            dry_run=True,
            burst_one_answer_enabled=True,
            scheduled_promise_enabled=True,
            promise_emit_enabled=True,
            promise_reply_bridge_enabled=True,
            promise_cancel_enabled=True,
            promise_sched_ttl_sec=21600,
            timezone="Asia/Taipei",
        )
        client = self.Cl()
        coach = self._coach({})
        coach.voice_promise_keep = lambda *args, **kwargs: "到點了，我來叫你起床。"

        def inner(part, _coach, _reader, _data, _snap, st, out, _cfg, _tz):
            now_utc = monitor._now_from_update(part)
            text = part["message"]["text"]
            if part["update_id"] == 100:
                monitor._promise_reply_bridge(out, st, cfg, coach, now_utc, TZ)
            else:
                self.assertTrue(monitor._maybe_promise_cancel(
                    out, st, cfg, text, now_utc, TZ, now_utc.timestamp()))

        with mock.patch.object(monitor, "_handle_message_inner", side_effect=inner):
            monitor._handle_burst_route_transaction(
                update, coach, None, {}, self.SNAP, state, client, cfg, TZ)

        self.assertEqual(client.sent, [
            "好，10:13的「叫他起床」我取消掉了——不會再發囉。\n要再約隨時說。"
        ])
        promise = state.scheduled_promises[0]
        self.assertTrue(promise["fulfilled"])
        self.assertEqual(promise["status"], "cancelled")

    def test_attachment_upload_cannot_leak_from_speculative_capture(self):
        class FileClient(self.Cl):
            def __init__(self):
                super().__init__()
                self.files = []

            def send_file(self, *args, **kwargs):
                self.files.append((args, kwargs))
                return True

        def inner(part, _coach, _reader, _data, _snap, _state, out, _cfg, _tz):
            out.send("找到附件，傳給你看：")
            out.send_file("file", b"secret", "note.txt")

        state = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        update = monitor.build_coalesced_update(_group(["找附件", "現在幾點？"]))
        cfg = SimpleNamespace(dry_run=True, burst_one_answer_enabled=True)
        client = FileClient()
        with mock.patch.object(monitor, "_handle_message_inner", side_effect=inner):
            monitor._handle_burst_route_transaction(
                update, None, None, {}, self.SNAP, state, client, cfg, TZ)

        self.assertEqual(client.files, [])
        self.assertEqual(len(client.sent), 1)
        self.assertIn("沒有在合併回覆裡傳檔", client.sent[0])
        with self.assertRaises(AttributeError):
            monitor._BurstReplyCapture(client).send_poll("x")     # 未白名單的未來寫入 API 也不可穿透

    def test_delivery_failure_is_transient_keeps_state_and_receipt_then_retry_is_idempotent(self):
        class FlakyClient(self.Cl):
            def __init__(self):
                super().__init__()
                self.ok = False

            def send(self, text):
                self.sent.append(text)
                return self.ok

        def inner(part, _coach, _reader, _data, _snap, _state, out, _cfg, _tz):
            out.send("處理：" + part["message"]["text"])

        state = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        state.convo_history = [{"role": "user", "text": "原歷史", "ts": NOW.timestamp() - 10}]
        update = monitor.build_coalesced_update(_group(["早安", "現在幾點？"]))
        cfg = SimpleNamespace(dry_run=True, burst_one_answer_enabled=True)
        client = FlakyClient()
        with mock.patch.object(monitor, "_handle_message_inner", side_effect=inner):
            with self.assertRaises(monitor.BurstDeliveryError) as raised:
                monitor._handle_burst_route_transaction(
                    update, None, None, {}, self.SNAP, state, client, cfg, TZ)
            self.assertTrue(lifeloop._is_transient(raised.exception))
            self.assertEqual(state.convo_history[-1]["text"], "原歷史")
            self.assertEqual(state.burst_delivered_update_id, 0)

            client.ok = True
            monitor._handle_burst_route_transaction(
                update, None, None, {}, self.SNAP, state, client, cfg, TZ)
            sent_after_success = len(client.sent)
            monitor._handle_burst_route_transaction(
                update, None, None, {}, self.SNAP, state, client, cfg, TZ)

        self.assertEqual(len(client.sent), sent_after_success)     # receipt 命中，不重送已成功的 wire
        self.assertEqual(state.burst_delivered_update_id, update["update_id"])

    def test_delivery_receipt_survives_restart_and_prevents_resend_before_offset_commit(self):
        path = os.path.join(tempfile.mkdtemp(), "s.json")
        state = State(path)
        update = monitor.build_coalesced_update(_group(["早安", "現在幾點？"]))
        cfg = SimpleNamespace(dry_run=False, burst_one_answer_enabled=True,
                              burst_paced_bubbles_enabled=True)
        first = self.Cl()
        first.dry_run = True                                    # 仍持久化 state，只略過測試中的 typing sleep

        def inner(part, _coach, _reader, _data, _snap, _state, out, _cfg, _tz):
            monitor._say(out, "處理：" + part["message"]["text"] + "。我接住了。")

        with mock.patch.object(monitor, "_handle_message_inner", side_effect=inner):
            monitor._handle_burst_route_transaction(
                update, None, None, {}, self.SNAP, state, first, cfg, TZ)
        self.assertGreater(len(first.sent), 1)

        restarted = State.load(path)                            # 模擬 send 成功、outer offset 尚未 commit 就重啟
        self.assertEqual(restarted.burst_delivered_update_id, update["update_id"])
        second = self.Cl()
        second.dry_run = True
        with mock.patch.object(monitor, "_handle_message_inner", side_effect=AssertionError("不該重跑")):
            monitor._handle_burst_route_transaction(
                update, None, None, {}, self.SNAP, restarted, second, cfg, TZ)
        self.assertEqual(second.sent, [])

    def test_cross_route_long_segments_are_fairly_bounded_to_one_send(self):
        def inner(part, _coach, _reader, _data, _snap, _state, out, _cfg, _tz):
            ch = "甲" if "早安" in part["message"]["text"] else "乙"
            out.send(ch * 5000)

        state = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        update = monitor.build_coalesced_update(_group(["早安", "現在幾點？"]))
        cfg = SimpleNamespace(dry_run=True, burst_one_answer_enabled=True)
        client = self.Cl()
        with mock.patch.object(monitor, "_handle_message_inner", side_effect=inner):
            monitor._handle_burst_route_transaction(
                update, None, None, {}, self.SNAP, state, client, cfg, TZ)

        self.assertEqual(len(client.sent), 1)
        self.assertLessEqual(monitor._utf16_units(client.sent[0]), notifiermod.TELEGRAM_LIMIT)
        self.assertIn("甲", client.sent[0])
        self.assertIn("乙", client.sent[0])
        self.assertIn("已節錄", client.sent[0])

    def test_truncated_mood_pair_is_not_committed_as_delivered(self):
        state = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        previous = {"ts": 1.0, "at": "08:00", "v": 0.10, "a": 0.20,
                    "mode": "snapshot", "text": "08:00 V +0.10、A +0.20。"}
        state.mood_last_report = dict(previous)
        update = monitor.build_coalesced_update(_group(["現在情緒如何？", "再說另一件事"]))
        cfg = SimpleNamespace(dry_run=True, burst_one_answer_enabled=True)
        client = self.Cl()
        contract = {"ts": 2.0, "at": "09:00", "v": 0.35, "a": 0.36, "mode": "snapshot"}

        def inner(part, _coach, _reader, _data, _snap, st, out, _cfg, _tz):
            if "情緒" in part["message"]["text"]:
                payload = "甲" * 5000 + "\n09:00 V +0.35、A +0.36。"
                out.capture_delivery_meta(payload, contract)
                out.send(payload)
                # 模擬 speculative `_remember` 因 capture=True 先寫入；transaction 必須
                # 依 final actual wire 回復／重驗，不能照單提交。
                st.mood_last_report = dict(contract, text=payload[:240])
            else:
                out.send("乙" * 5000)

        with mock.patch.object(monitor, "_handle_message_inner", side_effect=inner):
            monitor._handle_burst_route_transaction(
                update, None, None, {}, self.SNAP, state, client, cfg, TZ)

        self.assertEqual(len(client.sent), 1)
        self.assertNotIn("09:00 V +0.35、A +0.36", client.sent[0])
        self.assertEqual(state.mood_last_report, previous)

    def test_visible_mood_pair_is_committed_from_final_wire(self):
        state = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        update = monitor.build_coalesced_update(_group(["現在情緒如何？", "也說說感受"]))
        cfg = SimpleNamespace(dry_run=True, burst_one_answer_enabled=True)
        client = self.Cl()
        contract = {"ts": 2.0, "at": "09:00", "v": 0.35, "a": 0.36, "mode": "snapshot"}

        def inner(part, _coach, _reader, _data, _snap, st, out, _cfg, _tz):
            payload = ("09:00 V +0.35、A +0.36。這是我此刻的座標。"
                       if "情緒" in part["message"]["text"] else "我也有把主觀感受放進來。")
            out.capture_delivery_meta(payload, contract if "情緒" in part["message"]["text"] else None)
            out.send(payload)

        with mock.patch.object(monitor, "_handle_message_inner", side_effect=inner):
            monitor._handle_burst_route_transaction(
                update, None, None, {}, self.SNAP, state, client, cfg, TZ)

        self.assertEqual(state.mood_last_report["at"], "09:00")
        self.assertEqual((state.mood_last_report["v"], state.mood_last_report["a"]), (0.35, 0.36))

    def test_cost_fields_follow_real_meter_on_success_and_render_failure(self):
        def make_case(fail_second=False):
            state = State(os.path.join(tempfile.mkdtemp(), "s.json"))
            state.cost_since_digest_usd = 1.0
            state.cost_since_digest_calls = 4
            state.cost_total_usd = 10.0
            state.cost_month_key = "2026-08"
            state.cost_month_usd = 8.0
            calls = {"n": 0}

            def sink(value):
                calls["n"] += 1
                state.cost_since_digest_usd += value
                state.cost_since_digest_calls += 1
                state.cost_total_usd += value
                if state.cost_month_key != "2026-09":
                    state.cost_month_key, state.cost_month_usd = "2026-09", 0.0
                state.cost_month_usd += value

            coach = SimpleNamespace(meter=SimpleNamespace(on_cost=sink))

            def inner(part, co, _reader, _data, _snap, _state, out, _cfg, _tz):
                co.meter.on_cost(0.25)
                if fail_second and calls["n"] == 2:
                    raise RuntimeError("render failed")
                out.send("處理：" + part["message"]["text"])

            update = monitor.build_coalesced_update(_group(["早安", "現在幾點？"]))
            cfg = SimpleNamespace(dry_run=True, burst_one_answer_enabled=True)
            client = self.Cl()
            with mock.patch.object(monitor, "_handle_message_inner", side_effect=inner):
                if fail_second:
                    with self.assertRaisesRegex(RuntimeError, "render failed"):
                        monitor._handle_burst_route_transaction(
                            update, coach, None, {}, self.SNAP, state, client, cfg, TZ)
                else:
                    monitor._handle_burst_route_transaction(
                        update, coach, None, {}, self.SNAP, state, client, cfg, TZ)
            return state

        for failed in (False, True):
            with self.subTest(render_failed=failed):
                state = make_case(failed)
                self.assertEqual(state.cost_since_digest_calls, 6)
                self.assertAlmostEqual(state.cost_since_digest_usd, 1.5)
                self.assertAlmostEqual(state.cost_total_usd, 10.5)
                self.assertEqual(state.cost_month_key, "2026-09")
                self.assertAlmostEqual(state.cost_month_usd, 0.5)

    def test_mixed_sticker_signal_commits_once_after_failed_delivery_retry(self):
        group = {
            "type": "mixed",
            "updates": [
                {"update_id": 300, "message": {"chat": {"id": 1}, "message_id": 400,
                                                  "date": NOW.timestamp(),
                                                  "sticker": {"file_id": "s1", "emoji": "🙂"}}},
                {"update_id": 301, "message": {"chat": {"id": 1}, "message_id": 401,
                                                  "date": NOW.timestamp() + 1, "text": "現在幾點？"}},
            ],
        }
        update = monitor.build_coalesced_update(group)
        self.assertEqual(update["burst_n"], 2)
        state = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        state.test_sticker_observations = 0

        class FlakyClient(self.Cl):
            def __init__(self):
                super().__init__()
                self.ok = False

            def send(self, text):
                self.sent.append(text)
                return self.ok

        def observe(_sticker, _update, target, _cfg, client=None, coach=None):
            target.test_sticker_observations += 1
            return "🙂", 0.2

        def inner(part, _coach, _reader, _data, _snap, _state, out, _cfg, _tz):
            out.send("時間回答")

        cfg = SimpleNamespace(dry_run=True, burst_one_answer_enabled=True)
        client = FlakyClient()
        with mock.patch.object(monitor, "sticker_signal", side_effect=observe), \
                mock.patch.object(monitor, "_handle_message_inner", side_effect=inner):
            with self.assertRaises(monitor.BurstDeliveryError):
                monitor._handle_burst_route_transaction(
                    update, None, None, {}, self.SNAP, state, client, cfg, TZ)
            self.assertEqual(state.test_sticker_observations, 0)
            client.ok = True
            monitor._handle_burst_route_transaction(
                update, None, None, {}, self.SNAP, state, client, cfg, TZ)
            monitor._handle_burst_route_transaction(
                update, None, None, {}, self.SNAP, state, client, cfg, TZ)

        self.assertEqual(state.test_sticker_observations, 1)

    def test_one_wire_scope_is_cleared_on_exception(self):
        update = monitor.build_coalesced_update(_group(["第一則", "第二則"]))
        cfg = SimpleNamespace(burst_one_answer_enabled=True)
        monitor._TURN["bubbles"] = 7
        with mock.patch.object(monitor, "_handle_message_inner", side_effect=RuntimeError("boom")):
            with self.assertRaisesRegex(RuntimeError, "boom"):
                monitor.handle_message(update, None, None, {}, None, None, self.Cl(), cfg, TZ)
        self.assertEqual(monitor._BURST_ONE_WIRE_STACK, [])          # 早退/例外不污染下一個主動 _say
        self.assertEqual(monitor._TURN["bubbles"], 7)               # turn-local bubble cap 也恢復

    def test_bounded_wire_removes_only_global_exact_duplicate_and_reads_as_one_reply(self):
        def voice(text):
            return {"text": text, "data": "", "voices": [text], "mood_contracts": []}

        wire = monitor._bounded_burst_wire([
            voice("嗯。"),
            voice("我會把你這幾句一起看。"),
            voice("嗯。"),                                      # 非相鄰 exact duplicate 也只留一次
            voice("目前是 067bd0e。"),
            voice("目前是 067bd0f。"),                          # 不同版本字串絕不能被近義刪重
        ])

        self.assertEqual(wire.count("嗯。"), 1)
        self.assertIn("嗯。 我會把你這幾句一起看。", wire)       # 自然短句不硬切成內部 handler 段落
        self.assertIn("067bd0e", wire)
        self.assertIn("067bd0f", wire)

    def test_bounded_wire_does_not_claim_complete_voices_when_they_cannot_fit(self):
        segments = [
            {"text": "甲" * 120, "data": "", "voices": ["甲" * 120], "mood_contracts": []},
            {"text": "乙" * 120, "data": "", "voices": ["乙" * 120], "mood_contracts": []},
        ]
        wire = monitor._bounded_burst_wire(segments, limit=100)
        self.assertLessEqual(monitor._utf16_units(wire), 100)
        self.assertIn("以下每個部分都已節錄", wire)
        self.assertNotIn("完整自然回覆", wire)
        self.assertIn("甲", wire)
        self.assertIn("乙", wire)


class BurstPacedBubbleTest(unittest.TestCase):
    """🫧 §2.30：語意仍是一答；純 voice 才在真送達層展開成有呼吸的泡泡。"""

    PAYLOADS = [
        "你辛苦了，好好休息。",
        "今天沒有聊很多，但我有把你剛才連著說的幾件事一起收到。",
        "嗯。",
        "明天醒來，我們再接著說。",
    ]

    def setUp(self):
        monitor._TURN.clear()

    @staticmethod
    def _voice_segments(text):
        return [{"text": text, "data": "", "voices": [text], "mood_contracts": []}]

    @staticmethod
    def _inner(part, _coach, _reader, _data, _snap, _state, out, _cfg, _tz):
        if int(part.get("burst_n", 1) or 1) > 1:
            payload = "".join(BurstPacedBubbleTest.PAYLOADS)
        else:
            index = int(part["update_id"]) - 100
            payload = BurstPacedBubbleTest.PAYLOADS[index]
        out.capture_delivery_meta(payload)
        out.send(payload)

    @staticmethod
    def _real_say_inner(part, _coach, _reader, _data, _snap, _state, out, _cfg, _tz):
        if int(part.get("burst_n", 1) or 1) > 1:
            payload = "".join(BurstPacedBubbleTest.PAYLOADS)
        else:
            index = int(part["update_id"]) - 100
            payload = BurstPacedBubbleTest.PAYLOADS[index]
        monitor._say(out, payload)

    def test_planner_uses_complete_sentences_with_natural_long_short_rhythm(self):
        wire = "".join(self.PAYLOADS)
        bubbles = monitor._burst_delivery_bubbles(
            wire, segments=self._voice_segments(wire), enabled=True)

        self.assertEqual(bubbles, self.PAYLOADS)
        self.assertEqual("".join(bubbles), wire)                 # 呈現層沒有改寫 canonical 內容
        lengths = [len(value) for value in bubbles]
        self.assertLess(min(lengths), max(lengths) / 3)          # 不是硬切成等長機器片段
        self.assertTrue(all(value.endswith(("。", "！", "？")) for value in bubbles))
        self.assertEqual(
            monitor._burst_delivery_bubbles(
                wire, segments=self._voice_segments(wire), enabled=False),
            [wire],
        )

    def test_planner_glues_quoted_enumeration_seam_without_losing_text(self):
        wire = "你每次問我「是嗎？」、「你真的知道嗎？」\n我都有聽到。"
        bubbles = monitor._burst_delivery_bubbles(
            wire, segments=self._voice_segments(wire), enabled=True)

        self.assertFalse(any(value.lstrip().startswith(("、", "，", "；", "：", ","))
                             for value in bubbles))
        self.assertEqual("".join(bubbles), wire.replace("\n", ""))
        self.assertEqual(bubbles[0], "你每次問我「是嗎？」、「你真的知道嗎？」")

    def test_planner_hard_caps_pathological_sentence_flood_without_dropping_content(self):
        sentences = [f"第{i}句。" for i in range(1, 13)]
        wire = "".join(sentences)
        bubbles = monitor._burst_delivery_bubbles(
            wire, segments=self._voice_segments(wire), enabled=True)

        self.assertGreater(len(bubbles), 1)
        self.assertLessEqual(len(bubbles), monitor._BURST_PACED_MAX_BUBBLES)
        self.assertEqual("".join(bubbles), wire)
        self.assertTrue(all(monitor._utf16_units(value) <= notifiermod.TELEGRAM_LIMIT
                            for value in bubbles))

    def test_planner_cap_stays_within_telegram_limit_at_full_wire_budget(self):
        wire = "句。" * (notifiermod.TELEGRAM_LIMIT // 2)
        self.assertEqual(monitor._utf16_units(wire), notifiermod.TELEGRAM_LIMIT)
        bubbles = monitor._burst_delivery_bubbles(
            wire, segments=self._voice_segments(wire), enabled=True)

        self.assertLessEqual(len(bubbles), monitor._BURST_PACED_MAX_BUBBLES)
        self.assertEqual("".join(bubbles), wire)
        self.assertTrue(all(monitor._utf16_units(value) <= notifiermod.TELEGRAM_LIMIT
                            for value in bubbles))

    def test_evidence_or_unknown_metadata_stays_one_block(self):
        wire = "📚 可核對資料：第一項。第二項。\n\n我的看法是這樣。"
        data_segment = [{"text": wire, "data": "📚 可核對資料：第一項。第二項。",
                         "voices": ["我的看法是這樣。"], "mood_contracts": []}]
        self.assertEqual(
            monitor._burst_delivery_bubbles(wire, segments=data_segment, enabled=True),
            ["📚 可核對資料：第一項。第二項。", "我的看法是這樣。"],
        )
        self.assertEqual(
            monitor._burst_delivery_bubbles(wire, segments=None, enabled=True),
            [wire],
        )
        self.assertEqual(
            monitor._burst_delivery_bubbles(wire, segments=[wire], enabled=True),
            [wire],
        )

    def test_delivery_types_before_each_followup_and_delays_by_its_length(self):
        class Client:
            dry_run = False

            def __init__(self):
                self.sent, self.typing, self.events = [], 0, []

            def send_typing(self):
                self.typing += 1
                self.events.append(("typing", None))

            def send(self, text):
                self.sent.append(text)
                self.events.append(("send", text))
                return 700 + len(self.sent)

        client = Client()
        with mock.patch.object(monitor, "_jitter", return_value=1.0), \
                mock.patch.object(monitor, "_sleep",
                                  side_effect=lambda delay: client.events.append(("sleep", delay))) as sleep:
            ok, results = monitor._send_burst_bubbles(client, self.PAYLOADS)

        self.assertTrue(ok)
        self.assertEqual(results, [701, 702, 703, 704])
        self.assertEqual(client.sent, self.PAYLOADS)
        self.assertEqual(client.typing, len(self.PAYLOADS) - 1)
        self.assertEqual(
            [call.args[0] for call in sleep.call_args_list],
            [monitor._typing_delay(len(value)) for value in self.PAYLOADS[1:]],
        )
        expected_events = [("send", self.PAYLOADS[0])]
        for value in self.PAYLOADS[1:]:
            expected_events.extend([
                ("typing", None),
                ("sleep", monitor._typing_delay(len(value))),
                ("send", value),
            ])
        self.assertEqual(client.events, expected_events)

    def test_failed_bubble_stops_before_any_later_typing_sleep_or_send(self):
        class Client:
            dry_run = False

            def __init__(self):
                self.events = []

            def send_typing(self):
                self.events.append(("typing", None))

            def send(self, text):
                self.events.append(("send", text))
                return len([event for event in self.events if event[0] == "send"]) < 2

        client = Client()
        with mock.patch.object(monitor, "_jitter", return_value=1.0), \
                mock.patch.object(
                    monitor, "_sleep",
                    side_effect=lambda delay: client.events.append(("sleep", delay))):
            ok, results = monitor._send_burst_bubbles(client, self.PAYLOADS[:3])

        self.assertFalse(ok)
        self.assertEqual(results, [True, False])
        self.assertEqual(client.events, [
            ("send", self.PAYLOADS[0]),
            ("typing", None),
            ("sleep", monitor._typing_delay(len(self.PAYLOADS[1]))),
            ("send", self.PAYLOADS[1]),
        ])

    def test_typing_failure_does_not_eat_the_actual_text(self):
        class Client:
            dry_run = False

            def __init__(self):
                self.sent = []

            def send_typing(self):
                raise RuntimeError("typing unavailable")

            def send(self, text):
                self.sent.append(text)
                return 800 + len(self.sent)

        client = Client()
        with mock.patch.object(monitor, "_sleep") as sleep:
            ok, results = monitor._send_burst_bubbles(client, self.PAYLOADS[:2])
        self.assertTrue(ok)
        self.assertEqual(results, [801, 802])
        self.assertEqual(client.sent, self.PAYLOADS[:2])
        sleep.assert_not_called()

    def test_three_plus_messages_send_as_one_history_turn_but_many_bubbles(self):
        class Client:
            dry_run = True

            def __init__(self):
                self.sent = []

            def send(self, text):
                self.sent.append(text)
                return 900 + len(self.sent)

        state = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        update = monitor.build_coalesced_update(_group(["第一則", "第二則", "第三則", "第四則"]))
        cfg = SimpleNamespace(dry_run=True, burst_one_answer_enabled=True,
                              burst_paced_bubbles_enabled=True)
        client = Client()
        with mock.patch.object(monitor, "_handle_message_inner", side_effect=self._inner):
            monitor._handle_burst_route_transaction(
                update, None, None, {}, EndToEndTest.SNAP, state, client, cfg, TZ)

        self.assertEqual(client.sent, self.PAYLOADS)
        self.assertEqual([entry["role"] for entry in state.convo_history[-2:]], ["user", "model"])
        self.assertEqual(state.convo_history[-2]["text"], "第一則\n第二則\n第三則\n第四則")
        self.assertEqual(state.convo_history[-1]["text"], "".join(self.PAYLOADS))
        self.assertEqual(set(state.burst_delivered_update_ids), {100, 101, 102, 103})

    def test_real_say_path_renders_once_and_paces_one_logical_history_answer(self):
        class Client:
            dry_run = True

            def __init__(self):
                self.sent = []

            def send(self, text):
                self.sent.append(text)
                return 950 + len(self.sent)

        state = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        update = monitor.build_coalesced_update(_group(["第一則", "第二則", "第三則", "第四則"]))
        cfg = SimpleNamespace(dry_run=True, burst_one_answer_enabled=True,
                              burst_paced_bubbles_enabled=True)
        client = Client()
        with mock.patch.object(monitor, "_handle_message_inner",
                               side_effect=self._real_say_inner) as handler:
            monitor._handle_burst_route_transaction(
                update, None, None, {}, EndToEndTest.SNAP, state, client, cfg, TZ)

        self.assertEqual(handler.call_count, 1)                    # 純對話整波只生成一次
        self.assertEqual(client.sent, self.PAYLOADS)
        self.assertEqual([entry["role"] for entry in state.convo_history[-2:]],
                         ["user", "model"])
        self.assertEqual(state.convo_history[-1]["text"], "\n".join(self.PAYLOADS))

    def test_real_say_data_lead_keeps_evidence_intact_and_paces_voice(self):
        class Client:
            dry_run = True

            def __init__(self):
                self.sent = []

            def send(self, text):
                self.sent.append(text)
                return 980 + len(self.sent)

        data_block = "📚 可核對資料：\n第一項。\n第二項。"
        voice = "我看到的是同一條線。還有一點值得留意。"

        def inner(_part, _coach, _reader, _data, _snap, _state, out, _cfg, _tz):
            monitor._say(out, voice, wire_lead=data_block + "\n\n")

        state = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        update = monitor.build_coalesced_update(_group(["查第一項", "也查第二項"]))
        cfg = SimpleNamespace(dry_run=True, burst_one_answer_enabled=True,
                              burst_paced_bubbles_enabled=True)
        client = Client()
        with mock.patch.object(monitor, "_handle_message_inner", side_effect=inner):
            monitor._handle_burst_route_transaction(
                update, None, None, {}, EndToEndTest.SNAP, state, client, cfg, TZ)

        self.assertGreater(len(client.sent), 1)
        self.assertEqual(data_block, client.sent[0])
        self.assertEqual("".join("".join(client.sent).split()),
                         "".join((data_block + voice).split()))
        self.assertEqual([entry["role"] for entry in state.convo_history[-2:]],
                         ["user", "model"])

    def test_middle_failure_is_fail_fast_and_commits_nothing_then_retry_is_idempotent(self):
        class ScriptedClient:
            dry_run = True

            def __init__(self, results):
                self.results, self.sent = list(results), []

            def send(self, text):
                self.sent.append(text)
                return self.results[len(self.sent) - 1]

        state = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        original = [{"role": "user", "text": "原歷史", "ts": NOW.timestamp() - 10}]
        state.convo_history = list(original)
        update = monitor.build_coalesced_update(_group(["第一則", "第二則", "第三則", "第四則"]))
        cfg = SimpleNamespace(dry_run=True, burst_one_answer_enabled=True,
                              burst_paced_bubbles_enabled=True)
        failed = ScriptedClient([1001, False, 1003, 1004])
        replay_before, short_before, last_before = (
            list(monitor._SENT_RECENT), list(monitor._SHORT_SENT), dict(monitor._LAST_SENT))

        with mock.patch.object(monitor, "_handle_message_inner", side_effect=self._inner):
            with self.assertRaisesRegex(monitor.BurstDeliveryError, r"1/4 bubbles"):
                monitor._handle_burst_route_transaction(
                    update, None, None, {}, EndToEndTest.SNAP, state, failed, cfg, TZ)

            self.assertEqual(failed.sent, self.PAYLOADS[:2])      # 第 2 顆失敗後不再送
            self.assertEqual(state.convo_history, original)
            self.assertEqual(state.burst_delivered_update_id, 0)
            self.assertEqual(state.burst_delivered_update_ids, [])
            self.assertEqual(monitor._SENT_RECENT, replay_before)
            self.assertEqual(monitor._SHORT_SENT, short_before)
            self.assertEqual(monitor._LAST_SENT, last_before)

            retried = ScriptedClient([1101, 1102, 1103, 1104])
            monitor._handle_burst_route_transaction(
                update, None, None, {}, EndToEndTest.SNAP, state, retried, cfg, TZ)
            sent_after_success = list(retried.sent)
            monitor._handle_burst_route_transaction(
                update, None, None, {}, EndToEndTest.SNAP, state, retried, cfg, TZ)

        self.assertEqual(retried.sent, sent_after_success)        # exact receipt 命中，不再送
        self.assertEqual(retried.sent, self.PAYLOADS)
        self.assertEqual(failed.sent[0], retried.sent[0])         # 已知邊界：失敗前綴會在 retry 重送
        self.assertEqual(set(state.burst_delivered_update_ids), {100, 101, 102, 103})

    def test_partial_failure_stays_uncommitted_on_disk_then_full_retry_gets_durable_receipt(self):
        class ScriptedClient:
            dry_run = True

            def __init__(self, results):
                self.results, self.sent = list(results), []

            def send(self, text):
                self.sent.append(text)
                return self.results[len(self.sent) - 1]

        path = os.path.join(tempfile.mkdtemp(), "s.json")
        state = State(path)
        original = [{"role": "user", "text": "磁碟原歷史", "ts": NOW.timestamp() - 10}]
        state.convo_history = list(original)
        state.save()
        update = monitor.build_coalesced_update(_group(["第一則", "第二則", "第三則", "第四則"]))
        cfg = SimpleNamespace(dry_run=False, burst_one_answer_enabled=True,
                              burst_paced_bubbles_enabled=True)

        failed = ScriptedClient([1801, False, 1803, 1804])
        with mock.patch.object(monitor, "_handle_message_inner",
                               side_effect=self._real_say_inner):
            with self.assertRaisesRegex(monitor.BurstDeliveryError, r"1/4 bubbles"):
                monitor._handle_burst_route_transaction(
                    update, None, None, {}, EndToEndTest.SNAP, state, failed, cfg, TZ)

        restarted = State.load(path)
        self.assertEqual(restarted.convo_history, original)
        self.assertEqual(restarted.burst_delivered_update_ids, [])

        retried = ScriptedClient([1901, 1902, 1903, 1904])
        with mock.patch.object(monitor, "_handle_message_inner",
                               side_effect=self._real_say_inner):
            monitor._handle_burst_route_transaction(
                update, None, None, {}, EndToEndTest.SNAP, restarted, retried, cfg, TZ)
        self.assertEqual(retried.sent, self.PAYLOADS)
        self.assertEqual(failed.sent[0], retried.sent[0])          # Telegram 無 transaction：前綴會重送

        durable = State.load(path)
        after_success_history = list(durable.convo_history)
        no_resend = ScriptedClient([])
        with mock.patch.object(monitor, "_handle_message_inner",
                               side_effect=AssertionError("receipt 命中後不該重跑")):
            monitor._handle_burst_route_transaction(
                update, None, None, {}, EndToEndTest.SNAP, durable, no_resend, cfg, TZ)
        self.assertEqual(no_resend.sent, [])
        self.assertEqual(durable.convo_history, after_success_history)

    def test_mood_and_deferred_state_commit_only_after_every_paced_bubble_succeeds(self):
        class ScriptedClient:
            dry_run = True

            def __init__(self, results):
                self.results, self.sent = list(results), []

            def send(self, text):
                self.sent.append(text)
                return self.results[len(self.sent) - 1]

        previous = {"ts": 1.0, "at": "08:00", "v": 0.10, "a": 0.20,
                    "mode": "snapshot", "text": "08:00 V +0.10、A +0.20。"}
        contract = {"ts": 2.0, "at": "09:00", "v": 0.35, "a": 0.36,
                    "mode": "snapshot"}
        main_payload = "09:00 V +0.35、A +0.36，我這一刻有點雀躍。"
        followup = "我也把你的追問接住了。"
        state = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        state.mood_last_report = dict(previous)
        state.test_deferred_commits = 0
        update = monitor.build_coalesced_update(_group(["現在情緒如何？", "也說說感受"]))
        cfg = SimpleNamespace(dry_run=True, burst_one_answer_enabled=True,
                              burst_paced_bubbles_enabled=True)

        def inner(part, _coach, _reader, _data, _snap, st, out, _cfg, _tz):
            joined = int(part.get("burst_n", 1) or 1) > 1
            is_mood_part = joined or "情緒" in part["message"]["text"]
            if not is_mood_part:
                out.capture_delivery_meta(followup)
                out.send(followup)
                return
            payload = main_payload + (followup if joined else "")
            out.capture_delivery_meta(payload, contract)
            out.send(payload)

            def commit(_actual):
                st.test_deferred_commits += 1

            out.defer_delivery_action(commit, kind="promise", required=main_payload)

        with mock.patch.object(monitor, "_handle_message_inner", side_effect=inner):
            with self.assertRaisesRegex(monitor.BurstDeliveryError, r"1/2 bubbles"):
                monitor._handle_burst_route_transaction(
                    update, None, None, {}, EndToEndTest.SNAP, state,
                    ScriptedClient([2001, False]), cfg, TZ)
            self.assertEqual(state.mood_last_report, previous)
            self.assertEqual(state.test_deferred_commits, 0)

            monitor._handle_burst_route_transaction(
                update, None, None, {}, EndToEndTest.SNAP, state,
                ScriptedClient([2101, 2102]), cfg, TZ)

        self.assertEqual(state.mood_last_report["at"], "09:00")
        self.assertEqual((state.mood_last_report["v"], state.mood_last_report["a"]),
                         (0.35, 0.36))
        self.assertEqual(state.test_deferred_commits, 1)

    def test_send_exception_returns_visible_prefix_for_truthful_failure_count(self):
        class Client:
            dry_run = True

            def __init__(self):
                self.sent = []

            def send(self, text):
                self.sent.append(text)
                if len(self.sent) == 2:
                    raise ConnectionError("network down")
                return 1200 + len(self.sent)

        ok, results = monitor._send_burst_bubbles(Client(), self.PAYLOADS[:3])
        self.assertFalse(ok)
        self.assertEqual(results, [1201])

    def test_each_proactive_topic_records_only_its_actual_bubble_id(self):
        state = SimpleNamespace(DIALOGUE_AGENCY=False, recent_self_msgs=[])
        capture = monitor._BurstReplyCapture(SimpleNamespace())
        capture.defer_proactive_delivery(
            state, "🫧", "第一條線", self.PAYLOADS[0], track_initiative=False)
        capture.defer_proactive_delivery(
            state, "🫧", "第二條線", self.PAYLOADS[1], track_initiative=False)
        wire = self.PAYLOADS[0] + self.PAYLOADS[1]
        capture.finalize_deferred(wire)
        capture.bind_delivery_result([1301, 1302], self.PAYLOADS[:2])

        self.assertEqual([item["ids"] for item in state.recent_self_msgs], [[1301], [1302]])
        self.assertEqual([item["topic"] for item in state.recent_self_msgs],
                         ["第一條線", "第二條線"])

    def test_repeated_shared_phrase_maps_message_ids_by_order_not_substring(self):
        state = SimpleNamespace(DIALOGUE_AGENCY=False, recent_self_msgs=[])
        capture = monitor._BurstReplyCapture(SimpleNamespace())
        capture.defer_proactive_delivery(
            state, "🫧", "第一條線", "嗯。", track_initiative=False)
        capture.defer_proactive_delivery(
            state, "🫧", "第二條線", "嗯。好好休息。", track_initiative=False)
        capture.finalize_deferred("嗯。嗯。好好休息。")
        capture.bind_delivery_result([1401, 1402, 1403], ["嗯。", "嗯。", "好好休息。"])

        self.assertEqual([item["ids"] for item in state.recent_self_msgs],
                         [[1401], [1402, 1403]])
        self.assertEqual([item["topic"] for item in state.recent_self_msgs],
                         ["第一條線", "第二條線"])

    def test_ambiguous_cross_topic_bubble_is_not_bound_to_either_topic(self):
        state = SimpleNamespace(DIALOGUE_AGENCY=False, recent_self_msgs=[])
        capture = monitor._BurstReplyCapture(SimpleNamespace())
        capture.defer_proactive_delivery(
            state, "🫧", "第一條線", "第一句。", track_initiative=False)
        capture.defer_proactive_delivery(
            state, "🫧", "第二條線", "第二句。", track_initiative=False)
        capture.finalize_deferred("第一句。第二句。")
        capture.bind_delivery_result([1501], ["第一句。第二句。"])

        self.assertEqual(state.recent_self_msgs, [])

    def test_real_say_provenance_skips_identical_ordinary_bubble_before_topic(self):
        state = SimpleNamespace(DIALOGUE_AGENCY=False, recent_self_msgs=[])
        capture = monitor._BurstReplyCapture(SimpleNamespace())
        start = len(capture.events)
        monitor._say(capture, "嗯。")
        ordinary_range = (start, len(capture.events))
        start = len(capture.events)
        monitor._say(capture, "嗯。", state=state, topic="真正話題",
                     track_initiative=False)
        topic_range = (start, len(capture.events))
        ranges = [ordinary_range, topic_range]
        segments = [monitor._burst_capture_segment(capture.events[start:end])
                    for start, end in ranges]
        wire = monitor._bounded_burst_wire(segments)
        bubbles = monitor._burst_delivery_bubbles(wire, segments, enabled=True)

        capture.finalize_deferred(wire)
        capture.bind_delivery_result([1601], bubbles, wire=wire, segments=segments,
                                     segment_ranges=ranges)

        self.assertEqual(bubbles, ["嗯。"])                       # proactive duplicate source被去重
        self.assertEqual(state.recent_self_msgs, [])              # 不可把 ordinary 的 id 錯綁給它

    def test_real_say_multiline_capture_still_binds_every_topic_bubble(self):
        state = SimpleNamespace(DIALOGUE_AGENCY=False, recent_self_msgs=[])
        capture = monitor._BurstReplyCapture(SimpleNamespace())
        start = len(capture.events)
        monitor._say(capture, "第一句。第二句。", state=state, topic="真路徑",
                     track_initiative=False)
        ranges = [(start, len(capture.events))]
        segments = [monitor._burst_capture_segment(capture.events[start:end])
                    for start, end in ranges]
        wire = monitor._bounded_burst_wire(segments)
        bubbles = monitor._burst_delivery_bubbles(wire, segments, enabled=True)

        capture.finalize_deferred(wire)
        capture.bind_delivery_result([1701, 1702], bubbles, wire=wire, segments=segments,
                                     segment_ranges=ranges)

        self.assertEqual(bubbles, ["第一句。", "第二句。"])
        self.assertEqual(state.recent_self_msgs[0]["ids"], [1701, 1702])

    def test_deduped_proactive_source_cannot_retarget_same_text_in_later_ordinary_segment(self):
        state = SimpleNamespace(DIALOGUE_AGENCY=False, recent_self_msgs=[])
        capture = monitor._BurstReplyCapture(SimpleNamespace())
        ranges = []

        start = len(capture.events)
        monitor._say(capture, "嗯。")
        ranges.append((start, len(capture.events)))
        start = len(capture.events)
        monitor._say(capture, "嗯。", state=state, topic="被去重的話題",
                     track_initiative=False)
        ranges.append((start, len(capture.events)))
        start = len(capture.events)
        monitor._say(capture, "嗯。後話。")
        ranges.append((start, len(capture.events)))

        segments = [monitor._burst_capture_segment(capture.events[start:end])
                    for start, end in ranges]
        wire = monitor._bounded_burst_wire(segments)
        bubbles = monitor._burst_delivery_bubbles(wire, segments, enabled=True)
        capture.finalize_deferred(wire)
        capture.bind_delivery_result(
            [2201, 2202, 2203], bubbles, wire=wire, segments=segments,
            segment_ranges=ranges)

        self.assertEqual(bubbles, ["嗯。", "嗯。", "後話。"])
        self.assertEqual(state.recent_self_msgs, [])

    def test_cap_merged_topic_and_ordinary_text_gets_no_overbroad_message_id(self):
        state = SimpleNamespace(DIALOGUE_AGENCY=False, recent_self_msgs=[])
        capture = monitor._BurstReplyCapture(SimpleNamespace())
        ranges = []

        start = len(capture.events)
        monitor._say(capture, "主動句。", state=state, topic="主動話題",
                     track_initiative=False)
        ranges.append((start, len(capture.events)))
        start = len(capture.events)
        monitor._say(capture, "普通2。普通3。普通4。普通5。普通6。普通7。")
        ranges.append((start, len(capture.events)))

        segments = [monitor._burst_capture_segment(capture.events[start:end])
                    for start, end in ranges]
        wire = monitor._bounded_burst_wire(segments)
        bubbles = monitor._burst_delivery_bubbles(wire, segments, enabled=True)
        capture.finalize_deferred(wire)
        capture.bind_delivery_result(
            list(range(2301, 2301 + len(bubbles))), bubbles, wire=wire,
            segments=segments, segment_ranges=ranges)

        self.assertEqual(len(bubbles), monitor._BURST_PACED_MAX_BUBBLES)
        self.assertEqual(bubbles[0], "主動句。普通2。")
        self.assertEqual(state.recent_self_msgs, [])

    def test_excerpted_wire_never_retargets_topic_to_same_text_inside_other_source(self):
        state = SimpleNamespace(DIALOGUE_AGENCY=False, recent_self_msgs=[])
        capture = monitor._BurstReplyCapture(SimpleNamespace())
        ranges = []

        start = len(capture.events)
        monitor._say(capture, "前言。嗯。" + "甲" * 6000)
        ranges.append((start, len(capture.events)))
        start = len(capture.events)
        monitor._say(capture, "嗯。", state=state, topic="超限後的真話題",
                     track_initiative=False)
        ranges.append((start, len(capture.events)))

        segments = [monitor._burst_capture_segment(capture.events[start:end])
                    for start, end in ranges]
        wire = monitor._bounded_burst_wire(segments)
        bubbles = monitor._burst_delivery_bubbles(wire, segments, enabled=True)
        capture.finalize_deferred(wire)
        capture.bind_delivery_result(
            list(range(2401, 2401 + len(bubbles))), bubbles, wire=wire,
            segments=segments, segment_ranges=ranges)

        self.assertIn("已節錄", wire)
        self.assertGreaterEqual(wire.count("嗯。"), 2)
        self.assertEqual(state.recent_self_msgs, [])


class ConfigTest(unittest.TestCase):
    def test_config_synced(self):
        src = Path("telegram_monitor/config.py").read_text(encoding="utf-8")
        self.assertIn("BURST_ONE_ANSWER", src)
        self.assertIn("burst_one_answer_enabled", src)
        self.assertIn("BURST_PACED_BUBBLES", src)
        self.assertIn("burst_paced_bubbles_enabled", src)
        env = Path(".env.example").read_text(encoding="utf-8")
        self.assertRegex(env, re.compile(r"^BURST_ONE_ANSWER=1", re.M))
        self.assertRegex(env, re.compile(r"^BURST_PACED_BUBBLES=1", re.M))
        self.assertIn("BURST_ONE_ANSWER", Path("README.md").read_text(encoding="utf-8"))
        self.assertIn("BURST_PACED_BUBBLES", Path("README.md").read_text(encoding="utf-8"))

    def test_getattr_defaults_false(self):
        self.assertFalse(getattr(SimpleNamespace(), "burst_one_answer_enabled", False))
        self.assertFalse(getattr(SimpleNamespace(), "burst_paced_bubbles_enabled", False))

    def test_runtime_config_defaults_paced_delivery_on_and_allows_rollback(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertTrue(config.Config.load(env_file="/dev/null").burst_paced_bubbles_enabled)
        with mock.patch.dict(os.environ, {"BURST_PACED_BUBBLES": "0"}, clear=True):
            self.assertFalse(config.Config.load(env_file="/dev/null").burst_paced_bubbles_enabled)

    def test_plain_run_script_defaults_to_codex_deployment_branch(self):
        src = Path("run.sh").read_text(encoding="utf-8")
        self.assertIn('origin/codex/proactive-voice', src)
        self.assertIn('target="codex/proactive-voice"', src)
        self.assertLess(src.index('origin/codex/proactive-voice'), src.index("refs/remotes/origin/claude/*"))


class TelegramTransportBoundaryTest(unittest.TestCase):
    def test_real_notifier_receives_prebounded_data_voice_payload(self):
        voice = "完整 voice 🫧"
        payload, fragment = monitor._data_voice_payloads("資" * 6000, voice)[0]
        self.assertEqual(fragment, voice)
        self.assertTrue(payload.endswith(voice))
        self.assertLessEqual(monitor._utf16_units(payload), notifiermod.TELEGRAM_LIMIT)
        response = SimpleNamespace(status_code=200, text="", json=lambda: {"result": {"message_id": 7}})
        client = notifiermod.Notifier("token", "chat")
        with mock.patch("telegram_monitor.notifier.requests.post", return_value=response) as post:
            self.assertEqual(client.send(payload), 7)
        self.assertEqual(post.call_args.kwargs["json"]["text"], payload)  # Notifier 沒有再靜默截掉尾端

    def test_utf16_and_grapheme_boundaries_are_not_split(self):
        self.assertEqual(monitor._utf16_units("🫧" * 2048), 4096)
        self.assertEqual(len(monitor._split_telegram_text("🫧" * 2048)), 1)
        self.assertEqual(len(monitor._split_telegram_text("🫧" * 2049)), 2)
        for cluster in ("e\u0301", "👩‍👩‍👧‍👦", "👍🏽", "🇹🇼", "❤️"):
            text = cluster * 1500
            chunks = monitor._split_telegram_text(text)
            self.assertEqual("".join(chunks), text)
            self.assertTrue(all(monitor._utf16_units(c) <= 4096 for c in chunks))
        spaced = ("一段有空白的長文字。 \n" * 500)
        self.assertEqual("".join(monitor._split_telegram_text(spaced)), spaced)

    def test_nearly_full_voice_keeps_excerpt_notice_in_an_atomic_transport_chunk(self):
        voice = "聲" * 4090
        specs = monitor._data_voice_payloads("資料" * 3000, voice)
        self.assertEqual(len(specs), 2)
        self.assertIn("資料較長，已節錄", specs[0][0])
        self.assertEqual(specs[1], (voice, voice))
        self.assertTrue(all(monitor._utf16_units(payload) <= 4096 for payload, _fragment in specs))
        marker = "…（資料較長，已節錄）\n\n"
        exact_voice = "聲" * (4096 - monitor._utf16_units(marker))
        exact = monitor._data_voice_payloads("資料" * 20, exact_voice)
        self.assertEqual(exact, [(marker + exact_voice, exact_voice)])


if __name__ == "__main__":
    unittest.main()
