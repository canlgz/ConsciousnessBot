"""🧭 §1.25 PROMISE_BEHAVIOR_FIX＋情緒座標兌現接地。

截圖（2026-07-12 20:59–21:05）根因（主管實測）：
① extract_promise_behavior('我跟你聊天一下，10 分鐘後告訴我你情緒座標的變化？') = '跟他聊聊'
   ——前導寒暄子句搶走 behavior、真正的委託「告訴他情緒座標的變化」整個丟了；
② 訂約時沒存情緒座標快照＝兌現零差分資料 → 21:05 兌現答非所問（改談程式更新的蛻變摘要）。

修（PROMISE_MOOD_GROUND，兩層分離定式）：
(a) extract_promise_behavior 加 mood_fix 參（預設 False＝47 處既有 assert 逐位元不變）：
    先用 §1.11 同一把 _strip_timeless_lead_me 剝無時間前導我-子句；情緒/心情座標句在
    _BEHAVIOR_MAP 之前抽出「告訴他情緒座標的變化」級別的明確標籤；
(b) 入帳（_book_scheduled_targets 與 _book_self_promise 兩點）存 circumplex 快照 {v,a,label,ts}；
(c) 兌現差分**程式算**（circumplex.shift_text：「從『X』往『Y』沉了一段（V…A…）」）、LLM 只准渲染、
    明令不得改談程式更新；無快照（舊帳）→ 誠實說「當時沒記下座標、只能說此刻是 X」。
selfchange.py 本體一行不動。全 stub、絕不打網路。
"""

import os
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from telegram_monitor import circumplex
from telegram_monitor import coach as coachmod
from telegram_monitor import config, monitor, selfstate
from telegram_monitor.state import State

TZ = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 7, 12, 12, 59, 0, tzinfo=timezone.utc)      # 台北 7/12 20:59（入帳）
EMIT = datetime(2026, 7, 12, 13, 9, 0, tzinfo=timezone.utc)      # 台北 7/12 21:09（兌現）
TARGET = datetime(2026, 7, 12, 21, 9, 0, tzinfo=TZ).timestamp()
S = "我跟你聊天一下，10 分鐘後告訴我你情緒座標的變化？"           # 截圖原句（半形空白版）
S_NOSP = "我跟你聊天一下，10分鐘後告訴我你情緒座標的變化？"       # 無空白版同測


class FakeClient:
    def __init__(self):
        self.sent, self.stickers, self.dry_run = [], [], False

    def send(self, text):
        self.sent.append(text)
        return True

    def send_sticker(self, file_id):
        self.stickers.append(file_id)
        return True


def _cfg(**over):
    base = dict(dry_run=True, telegram_chat_id="", scheduled_promise_enabled=True,
                promise_emit_enabled=True, promise_sched_ttl_sec=21600, timezone="Asia/Taipei",
                notify_cooldown_min=30, promise_mood_ground_enabled=True)
    base.update(over)
    return SimpleNamespace(**base)


def _cfg_flag_off(**over):
    """⑦ 消融：假 cfg **無** promise_mood_ground_enabled 屬性＝getattr 預設 False＝完全現狀。"""
    c = _cfg(**over)
    delattr(c, "promise_mood_ground_enabled")
    return c


def _state(tmp, v=0.0, a=0.0):
    s = State(os.path.join(tmp, f"s{v}{a}.json"))
    s.owner_folder_id = "F"
    s.entropy = SimpleNamespace(mood=v, arousal=a)                # circumplex.position 讀 entropy.mood/arousal
    return s


def _mood_promise(baseline=None, behavior="告訴他情緒座標的變化"):
    p = {"target_ts": TARGET, "action": S[:120], "made_ts": NOW.timestamp(),
         "made_text": S[:200], "fulfilled": False, "behavior": behavior, "status": "pending"}
    if baseline:
        p["mood_baseline"] = baseline
    return p


class ExtractBehaviorTest(unittest.TestCase):
    """(a) 抽取修：mood_fix=True 抽出真正的委託；預設 False＝逐位元基線。"""

    def test_screenshot_sentence_mood_fix(self):
        for t in (S, S_NOSP):
            b = selfstate.extract_promise_behavior(t, mood_fix=True)
            self.assertNotEqual(b, "跟他聊聊", t)                           # 前導寒暄不再搶走 behavior
            self.assertIn("情緒座標", b, t)                                 # 抽出「告訴他情緒座標的變化」級別
        self.assertEqual(selfstate.extract_promise_behavior(S, mood_fix=True), "告訴他情緒座標的變化")

    def test_default_is_bitwise_baseline(self):
        self.assertEqual(selfstate.extract_promise_behavior(S), "跟他聊聊")             # 基線護欄（截圖根因①原樣）
        self.assertEqual(selfstate.extract_promise_behavior(S, mood_fix=False), "跟他聊聊")

    def test_mood_word_variant(self):
        b = selfstate.extract_promise_behavior("10分鐘後跟我說你心情座標的變化", mood_fix=True)
        self.assertEqual(b, "告訴他心情座標的變化")                          # 心情詞回心情版
        b2 = selfstate.extract_promise_behavior("等等分享你情緒的變化給我", mood_fix=True)
        self.assertEqual(b2, "告訴他情緒座標的變化")

    def test_mood_fix_no_flip_on_existing_shapes(self):
        """偵察實測「29 釘句 0 翻盤」的抽樣護欄：非情緒座標句 mood_fix 開關同值。"""
        for t in ("八點提醒我吃藥", "11點叫我起床", "30分鐘後跟我說你的感覺",
                  "十分鐘後給我一張貼圖", "8點跟我打招呼", "20分鐘後說說你有什麼不一樣"):
            self.assertEqual(selfstate.extract_promise_behavior(t, mood_fix=True),
                             selfstate.extract_promise_behavior(t), t)


class BookingSnapshotTest(unittest.TestCase):
    """(b) 入帳存 circumplex 快照（兩個入帳點；label/座標與程式值一致）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        monitor._TURN["bubbles"] = None
        monitor._TURN.pop("self_promise_skip", None)

    def test_book_scheduled_targets_snapshot(self):
        st = _state(self.tmp, v=0.30, a=-0.20)
        monitor._book_scheduled_targets(FakeClient(), st, _cfg(), None, S, [TARGET], NOW, TZ,
                                        NOW.timestamp())
        p = (st.scheduled_promises or [None])[0]
        self.assertIsNotNone(p)
        self.assertEqual(p["behavior"], "告訴他情緒座標的變化")             # 呼叫點帶 mood_fix=旗標
        mb = p.get("mood_baseline")
        self.assertIsNotNone(mb)
        v, a = circumplex.position(st)
        self.assertEqual(mb["v"], v)                                        # 座標＝程式讀值
        self.assertEqual(mb["a"], a)
        self.assertEqual(mb["label"], circumplex.label(v, a))               # label＝程式算
        self.assertEqual(round(mb["ts"]), round(NOW.timestamp()))

    def test_book_self_promise_snapshot(self):
        st = _state(self.tmp, v=0.10, a=0.10)
        bot_text = "到了 21:09，我會主動跟你說我情緒座標的變化。"
        monitor._book_self_promise(st, _cfg(), [TARGET], bot_text, NOW.timestamp(), "")
        p = (st.scheduled_promises or [None])[0]
        self.assertIsNotNone(p)
        mb = p.get("mood_baseline")                                         # made_text 命中情緒座標＝也存快照
        self.assertIsNotNone(mb)
        self.assertEqual(mb["label"], circumplex.label(*circumplex.position(st)))

    def test_non_mood_promise_no_snapshot(self):
        st = _state(self.tmp, v=0.30, a=-0.20)
        monitor._book_scheduled_targets(FakeClient(), st, _cfg(), None, "明天早上七點叫我起床喔",
                                        [TARGET], NOW, TZ, NOW.timestamp())
        self.assertNotIn("mood_baseline", st.scheduled_promises[0])         # 非情緒座標類＝零新鍵

    def test_flag_off_no_snapshot_and_baseline_behavior(self):
        st = _state(self.tmp, v=0.30, a=-0.20)
        monitor._book_scheduled_targets(FakeClient(), st, _cfg_flag_off(), None, S, [TARGET], NOW, TZ,
                                        NOW.timestamp())
        p = st.scheduled_promises[0]
        self.assertNotIn("mood_baseline", p)                                # ⑦ 旗標關＝mood 欄不寫
        self.assertEqual(p["behavior"], "跟他聊聊")                          # 抽取也回基線（mood_fix=False）


class ShiftTextTest(unittest.TestCase):
    """circumplex.shift_text：差分人話全程式算（V 降=沉/升=亮、A 升=繃/降=鬆）。"""

    def test_sink_direction(self):
        base = {"v": 0.30, "a": 0.10, "label": circumplex.label(0.30, 0.10), "ts": 0}
        s = circumplex.shift_text(base, -0.30, 0.05)
        self.assertIn("從『", s)
        self.assertIn("往『", s)
        self.assertIn("沉", s)                                              # V 往下＝沉
        self.assertIn("V -0.60", s)                                         # 差分數值程式算
        self.assertIn(f"『{circumplex.label(-0.30, 0.05)}』", s)

    def test_brighten_and_tense(self):
        base = {"v": -0.30, "a": -0.20, "label": circumplex.label(-0.30, -0.20), "ts": 0}
        s = circumplex.shift_text(base, 0.30, 0.40)
        self.assertIn("亮", s)                                              # V 往上＝亮
        self.assertIn("繃", s)                                              # A 往上＝繃
        s2 = circumplex.shift_text({"v": 0.0, "a": 0.5, "label": "激動、被攪動", "ts": 0}, 0.0, 0.1)
        self.assertIn("鬆", s2)                                             # A 往下＝鬆

    def test_no_move(self):
        base = {"v": 0.10, "a": 0.05, "label": circumplex.label(0.10, 0.05), "ts": 0}
        s = circumplex.shift_text(base, 0.11, 0.05)
        self.assertIn("幾乎沒", s)                                          # 沒動就誠實說沒動


class KeepBodyMoodGroundTest(unittest.TestCase):
    """(c) 兌現差分：emit/bridge/preempt 同走 _promise_keep_body 一條鏈＝一處測全覆蓋。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _keep_prompt(self, st, p, cfg=None, reply="我往低落沉了一段。"):
        captured = {}

        def _fake_chat(api_key, model, system, contents, **kw):
            captured["prompt"] = contents[-1]["parts"][0]["text"]
            return reply

        co = coachmod.Coach(SimpleNamespace(gemini_api_key="k"))
        with patch("telegram_monitor.gemini.generate_chat", _fake_chat):
            msg = monitor._promise_keep_body(st, cfg or _cfg(), co, p, EMIT, TZ, False)
        return msg, captured.get("prompt", "")

    def test_diff_rendered_only_from_program_facts(self):
        """③ 快照 0.30/0.10 → 此刻 -0.30/0.05：prompt 含程式算好的「從『X』往『Y』」差分＋方向詞「沉」
        ＋「不要改談程式更新」明令（截圖破口⑤：答非所問改談蛻變摘要）。"""
        base = {"v": 0.30, "a": 0.10, "label": circumplex.label(0.30, 0.10), "ts": NOW.timestamp()}
        st = _state(self.tmp, v=-0.30, a=0.05)
        msg, prompt = self._keep_prompt(st, _mood_promise(baseline=base))
        self.assertTrue(msg)
        expect = circumplex.shift_text(base, *circumplex.position(st))
        self.assertIn(expect, prompt)                                       # 差分整串＝程式算、LLM 只渲染
        self.assertIn("從『", prompt)
        self.assertIn("往『", prompt)
        self.assertIn("沉", prompt)                                         # 方向詞正確（推低→沉）
        self.assertIn("不要改談程式更新", prompt)                           # 明令不得答非所問
        self.assertIn("蛻變", prompt)

    def test_old_promise_without_baseline_honest_downgrade(self):
        """④ 舊帳無 mood_baseline → prompt 含「當時沒記下座標」誠實降級＋此刻 label（程式算）。"""
        st = _state(self.tmp, v=-0.40, a=-0.10)
        msg, prompt = self._keep_prompt(st, _mood_promise())
        self.assertIn("當時沒記下座標", prompt)
        self.assertIn(circumplex.label(-0.40, -0.10), prompt)

    def test_template_fallback_states_diff(self):
        """⑤ voice 失敗（coach=None）→ 確定性模板直述差分句。"""
        base = {"v": 0.30, "a": 0.10, "label": circumplex.label(0.30, 0.10), "ts": NOW.timestamp()}
        st = _state(self.tmp, v=-0.30, a=0.05)
        msg = monitor._promise_keep_body(st, _cfg(), None, _mood_promise(baseline=base), EMIT, TZ, False)
        self.assertIn("從『", msg)
        self.assertIn("沉", msg)
        self.assertIn("21:09", msg)                                         # 時刻程式算
        msg2 = monitor._promise_keep_body(st, _cfg(), None, _mood_promise(), EMIT, TZ, False)
        self.assertIn("當時沒記下座標", msg2)                               # 無快照＝模板也誠實降級

    def test_mood_word_follows_promise(self):
        """模板的「情緒/心情」用詞跟著承諾走（心情座標的約不說成情緒座標）。"""
        base = {"v": 0.0, "a": 0.0, "label": "平穩", "ts": NOW.timestamp()}
        p = _mood_promise(baseline=base, behavior="告訴他心情座標的變化")
        p["made_text"] = "10 分鐘後告訴我你心情座標的變化？"
        st = _state(self.tmp, v=-0.30, a=0.0)
        msg = monitor._promise_keep_body(st, _cfg(), None, p, EMIT, TZ, False)
        self.assertIn("心情座標", msg)

    def test_flag_off_bitwise_status_quo_strict_fake_coach(self):
        """⑦ 旗標關＝完全現狀：不算差分、不傳新 kwarg——用**嚴格簽名**假教練證明零新參數。"""
        def keep_strict(when, time_facts, history, promised="", late=False, feeling_ground="",
                        sticker_sent=False, sticker_wanted=False, sticker_desc="", change_ground=""):
            return "嗨，我來啦。"

        co = SimpleNamespace(enabled=True, voice_promise_keep=keep_strict)
        st = _state(self.tmp, v=-0.30, a=0.05)
        base = {"v": 0.30, "a": 0.10, "label": "平穩", "ts": NOW.timestamp()}
        msg = monitor._promise_keep_body(st, _cfg_flag_off(), co, _mood_promise(baseline=base),
                                         EMIT, TZ, False)
        self.assertEqual(msg, "嗨，我來啦。")                                # 嚴格簽名沒炸＝零新 kwarg
        msg2 = monitor._promise_keep_body(st, _cfg_flag_off(), None, _mood_promise(baseline=base),
                                          EMIT, TZ, False)
        self.assertEqual(msg2, "我說過21:09要為你做一件事——告訴他情緒座標的變化。")   # 模板同現狀


class PreemptIntegrationTest(unittest.TestCase):
    """§1.24 × §1.25 整合：21:05 提前兌現心情座標＝誠實（先說）＋接地（差分）一次到位。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        monitor._TURN["bubbles"] = None
        for k in ("self_promise_ctx", "self_promise_skip", "promise_guard_llm_no"):
            monitor._TURN.pop(k, None)

    def test_preempt_keep_is_honest_and_grounded(self):
        class _Benign:
            def __getattr__(self, name):
                return lambda *a, **k: []

        st = _state(self.tmp, v=-0.30, a=0.05)
        # handle_message 會碰 entropy 的代謝欄位（餵飽/歸零）——補齊、心情推動照真實鏈路走
        st.entropy.hunger = 0.5
        st.entropy.charge = 0.8
        st.entropy.self_stims_this_idle = 0
        st.entropy.reach_outs_this_idle = 0
        st.entropy.coping_reach_outs_this_idle = 0
        base = {"v": 0.30, "a": 0.10, "label": circumplex.label(0.30, 0.10), "ts": NOW.timestamp()}
        p = _mood_promise(baseline=base, behavior="告訴他心情座標的變化")
        st.scheduled_promises = [p]
        co = SimpleNamespace(enabled=True, api_key="k", model="m",
                             meter=SimpleNamespace(record=lambda *a, **k: None))
        co.reply = lambda *a, **k: "嗯。"
        co.ask = lambda *a, **k: ("chat", None, "嗯。")
        co.voice_schedule_ack = lambda *a, **k: "好。"
        co.voice_promise_ack = lambda *a, **k: "好。"
        co.voice_greeting = lambda *a, **k: "嗨。"
        co.voice_promise_keep = lambda *a, **k: None                        # 走模板退路
        co.judge_timed_request = lambda t: None
        co.judge_self_promise = lambda t: None
        co.judge_promise_preempt = lambda u, m: True
        cfg = SimpleNamespace(dry_run=False, telegram_chat_id="", scheduled_promise_enabled=True,
                              promise_emit_enabled=True, promise_sched_ttl_sec=21600,
                              timezone="Asia/Taipei", notify_cooldown_min=30,
                              promise_reply_bridge_enabled=False, promise_ledger_enabled=True,
                              sched_leave_autoarm_enabled=True, deferred_promise_enabled=True,
                              promise_llm_rescue_enabled=True, bot_self_promise_enabled=False,
                              promise_preempt_enabled=True, promise_preempt_window_sec=5400,
                              promise_preempt_future_guard_enabled=True,
                              promise_mood_ground_enabled=True)
        cl = FakeClient()
        ask = datetime(2026, 7, 12, 13, 5, 0, tzinfo=timezone.utc)           # 台北 21:05
        with patch("telegram_monitor.coach.build_memory_brief", return_value="brief"):
            monitor.handle_message({"message": {"chat": {"id": 1}, "text": "你現在就跟我說心情座標吧",
                                                "date": ask.timestamp()}},
                                   co, _Benign(), {"meta": {}}, None, st, cl, cfg, TZ)
        hs = [m for m in cl.sent if m.startswith("🤝")]
        self.assertEqual(len(hs), 1)
        msg = hs[0]
        self.assertNotIn("到了", msg)                                       # 不謊稱 21:09 到了
        self.assertIn("先", msg)                                            # 提前誠實
        self.assertIn("從『", msg)                                          # 差分接地（不是蛻變摘要）
        self.assertIn("沉", msg)
        self.assertNotIn("程式", msg)


class ConfigSyncTest(unittest.TestCase):
    def test_flag_default_true(self):
        os.environ.pop("PROMISE_MOOD_GROUND", None)
        cfg = config.Config.load()
        self.assertIs(cfg.promise_mood_ground_enabled, True)

    def test_flag_env_off(self):
        with patch.dict(os.environ, {"PROMISE_MOOD_GROUND": "0"}):
            cfg = config.Config.load()
            self.assertIs(cfg.promise_mood_ground_enabled, False)


if __name__ == "__main__":
    unittest.main()
