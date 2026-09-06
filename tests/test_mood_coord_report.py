"""🧭 §1.45 情緒座標數據自陳（MOOD_COORD_REPORT）：被問「內在的數據」時，照程式讀的 (V,A) 真數字＋軌跡講，
不再說「我還沒有那樣的辦法」（假的謙虛——circumplex 單一真相明明就有數字），也不再被「數據」劫持去吐 💸。

截圖根因（11:06–11:10）：①「你現在的情緒座標到哪了」→ self_state 只給定性「大概還是在低點吧」；②「要精確一點，
你有辦法全是內在的數據嗎」→「數據」∈ _DATA_CUES → 證據工具全開 → LLM 抓 api_cost 吐 💸 Gemini 花費報表（答非
所問）；③「你有辦法嗎？」→「要我精確地報出一個數字來，目前我還沒有那樣的辦法」＝機制有數字（circumplex.position）
卻無 lane 交出＝說自己做不到（違反不演不假裝：發現落差就補真能力）。

§1.45 四件：A. 軌跡捕捉——每輪互動的 (V,A) 更新完成後記 state.mood_trace（{ts,v,a,cause}，cause 由 _dv/敵意確定性
判、FIFO 40、跨重生）；B. circumplex.coord_facts——現值 V/A 數字＋象限標籤＋最近軌跡敘事（時刻/數字程式算、LLM
只准照抄）；C. is_mood_data_question 偵測＋fact_or_chat 注入＋self_state/cost/stats 誤收 re-route＋證據工具抑制
（「內在的數據」不是查記寫/花費）；D. persona.MOOD_COORD_HINT 明令「絕不說我沒辦法報數字——這份就是你的數據」。
旗標關＝不捕捉不注入不 re-route＝逐位元同現狀。全 stub、零網路。
"""

import os
import re
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from telegram_monitor import circumplex, monitor, persona
from telegram_monitor.state import State

TZ = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 7, 15, 3, 8, 0, tzinfo=timezone.utc)   # 台北 11:08（截圖時刻）


def _ent(v=-0.3, a=-0.1):
    return SimpleNamespace(mood=v, arousal=a, hunger=0.5, self_stims_this_idle=0,
                           reach_outs_this_idle=0, coping_reach_outs_this_idle=0)


class TraceTest(unittest.TestCase):
    def setUp(self):
        self.s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        self.s.entropy = _ent()

    def test_first_note_recorded(self):
        circumplex.trace_note(self.s, NOW.timestamp(), "跟你互動")
        self.assertEqual(len(self.s.mood_trace), 1)
        e = self.s.mood_trace[0]
        self.assertAlmostEqual(e["v"], -0.3)
        self.assertEqual(e["cause"], "跟你互動")

    def test_eps_gating_no_move_no_dup(self):
        circumplex.trace_note(self.s, NOW.timestamp(), "跟你互動")
        circumplex.trace_note(self.s, NOW.timestamp() + 60, "跟你互動")   # 沒動 → 不重記
        self.assertEqual(len(self.s.mood_trace), 1)

    def test_moved_recorded_and_cap(self):
        for i in range(60):
            self.s.entropy.mood = -1.0 + i * 0.03                        # 每次都動
            circumplex.trace_note(self.s, NOW.timestamp() + i, f"c{i}")
        self.assertLessEqual(len(self.s.mood_trace), circumplex.TRACE_CAP)


class FactsTest(unittest.TestCase):
    def test_facts_contain_numbers_label_and_trace(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.entropy = _ent(-0.30, -0.10)
        s.mood_trace = [
            {"ts": NOW.timestamp() - 300, "v": -0.10, "a": 0.00, "cause": "跟你互動"},
            {"ts": NOW.timestamp() - 120, "v": -0.30, "a": -0.10, "cause": "被說了重話"},
        ]
        txt = circumplex.coord_facts(s, TZ, NOW.timestamp())
        self.assertIn("-0.30", txt)                       # V 數字照程式讀
        self.assertIn("-0.10", txt)
        self.assertIn("被說了重話", txt)                   # 前後經過（cause）
        self.assertIn("11:0", txt)                        # 本地時刻程式算

    def test_facts_honest_when_no_trace(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.entropy = _ent(0.0, 0.0)
        txt = circumplex.coord_facts(s, TZ, NOW.timestamp())
        self.assertIn("剛開始記", txt)                     # 軌跡不足＝照實說


class DetectorTest(unittest.TestCase):
    def test_mood_data_questions_true(self):
        for t in ("所以，目前為止，你現在的情緒座標到哪了？",
                  "大概還是在低點吧？要精確一點，你有辦法全是內在的數據嗎",
                  "我是指，詮釋你自己內在的情緒座標相對應的數據",
                  "這部分有影響你的情緒座標？如何影響？"):
            self.assertTrue(circumplex.is_mood_data_question(t), t)

    def test_non_mood_false(self):
        for t in ("現在幾點", "這個月花多少錢", "我今天心情不好", "幫我列一下數據統計", "早安"):
            self.assertFalse(circumplex.is_mood_data_question(t), t)


class EndToEndTest(unittest.TestCase):
    class Cl:
        def __init__(self): self.sent, self.dry_run = [], False
        def send(self, t): self.sent.append(t); return True

    def _cfg(self, on=True):
        base = dict(dry_run=False, telegram_chat_id="", scheduled_promise_enabled=True,
                    promise_emit_enabled=True, promise_sched_ttl_sec=21600, timezone="Asia/Taipei",
                    notify_cooldown_min=30, promise_reply_bridge_enabled=False, promise_ledger_enabled=True,
                    sched_leave_autoarm_enabled=True, deferred_promise_enabled=True,
                    promise_llm_rescue_enabled=False, sticker_llm_rescue_enabled=False,
                    promise_keep_claim_guard_enabled=True, promise_said_ground_enabled=True,
                    bot_self_promise_enabled=False, promise_preempt_enabled=False,
                    sticker_sent_memory_enabled=True, send_stickers=True,
                    sticker_fakesend_guard_enabled=False, recall_ground_guard_enabled=False,
                    selfshare_reason_ground_enabled=False, wake_projection_guard_enabled=False,
                    user_habit_ground_enabled=False, self_feel_condense_enabled=False,
                    promise_deliver_content_enabled=False, mood_coord_report_enabled=on)
        return SimpleNamespace(**base)

    def _coach(self, voice):
        seen = {"ask": None, "evidence": None, "reply": None}
        c = SimpleNamespace(enabled=True, api_key="k", model="m",
                            meter=SimpleNamespace(record=lambda *a, **k: None), seen=seen)

        def ask(*a, **k):
            seen["ask"] = k.get("extra_system")
            seen["evidence"] = k.get("evidence_tools")
            return ("chat", None, voice)

        def reply(*a, **k):
            seen["reply"] = k.get("extra_system")
            return voice

        c.ask = ask
        c.reply = reply
        c.voice_schedule_ack = lambda q, w, h, sticker_hint="": "好。"
        c.voice_promise_ack = lambda q, h: "好。"
        c.judge_timed_request = lambda t: None
        c.judge_sticker_request = lambda t: None
        c.judge_self_promise = lambda t: None
        c.judge_promise_preempt = lambda *a, **k: False
        c.voice_promise_keep = lambda *a, **k: None
        c.voice_greeting = lambda *a, **k: "早安！"
        return c

    SNAP = SimpleNamespace(summary={"total": 1, "last24h": 0, "last7d": 0},
                           funnel={"candidate": 0, "context": 0, "journey": 0, "watch": 0},
                           heartbeat={"status": "ok"}, filed_records=[])
    BOOM = SimpleNamespace(load_embedding_records=lambda *_: [])

    def _run(self, question, voice="嗯。", on=True, ent=None):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        s.entropy = ent or _ent()
        cl = self.Cl()
        co = self._coach(voice)
        monitor.handle_message({"message": {"chat": {"id": 1}, "text": question, "date": NOW.timestamp()}},
                               co, self.BOOM, {"meta": {}, "records": []}, self.SNAP,
                               s, cl, self._cfg(on), TZ)
        return "".join(cl.sent), co.seen, s

    _VA_RE = re.compile(r"V [+-]\d\.\d{2}、A [+-]\d\.\d{2}")

    def test_data_question_gets_facts_and_no_evidence_tools(self):
        # 截圖 11:08：「內在的數據」不再吐 💸——證據工具抑制＋座標事實注入。
        # 注意：這句話本身也會微推座標（一般陪伴 +0.05）→ 報「更新後的此刻」數字（格式比對、不釘種子值）。
        out, seen, _ = self._run("大概還是在低點吧？要精確一點，你有辦法全是內在的數據嗎")
        got = (seen["ask"] or "") + (seen["reply"] or "")
        self.assertIn("情緒座標", got)                     # MOOD_COORD_HINT＋facts 已注入
        self.assertRegex(got, self._VA_RE)                 # 程式讀的真數字（V ±x.xx、A ±x.xx）
        if seen["evidence"] is not None:
            self.assertFalse(seen["evidence"])             # 證據工具已抑制（不會抓去 api_cost）

    def test_selfstate_hijack_rerouted(self):
        # 「你現在的情緒座標到哪了」原路 self_state → re-route 帶數字作答
        out, seen, _ = self._run("所以，目前為止，你現在的情緒座標到哪了？")
        got = (seen["ask"] or "") + (seen["reply"] or "")
        self.assertRegex(got, self._VA_RE)

    def test_hostile_message_traces_cause(self):
        # 「你太爛了」→ (V,A) 更新後記軌跡、cause＝被說了重話
        _, _, s = self._run("你太爛了", voice="嗚。")
        self.assertTrue(s.mood_trace)
        self.assertIn("被說了重話", s.mood_trace[-1]["cause"])

    def test_flag_off_byte_identical(self):
        out, seen, s = self._run("你有辦法全是內在的數據嗎", on=False)
        got = (seen["ask"] or "") + (seen["reply"] or "")
        self.assertNotIn("-0.30", got)                     # 不注入
        self.assertEqual(s.mood_trace, [])                 # 不捕捉
        if seen["evidence"] is not None:
            self.assertTrue(seen["evidence"])              # 證據閘照舊（現狀）


class PersistTest(unittest.TestCase):
    def test_mood_trace_survives_save_load(self):
        path = os.path.join(tempfile.mkdtemp(), "s.json")
        s = State(path)
        s.mood_trace = [{"ts": 1.0, "v": -0.3, "a": 0.1, "cause": "跟你互動"}]
        s.save()
        s2 = State.load(path)
        self.assertEqual(s2.mood_trace, [{"ts": 1.0, "v": -0.3, "a": 0.1, "cause": "跟你互動"}])


class ConfigTest(unittest.TestCase):
    def test_config_synced(self):
        src = open("telegram_monitor/config.py", encoding="utf-8").read()
        self.assertIn("MOOD_COORD_REPORT", src)
        self.assertIn("mood_coord_report_enabled", src)
        env = open(".env.example", encoding="utf-8").read()
        self.assertRegex(env, re.compile(r"^MOOD_COORD_REPORT=1", re.M))
        self.assertIn("MOOD_COORD_REPORT", open("README.md", encoding="utf-8").read())

    def test_persona_hint_exists(self):
        self.assertTrue(hasattr(persona, "MOOD_COORD_HINT"))
        self.assertIn("數據", persona.MOOD_COORD_HINT)

    def test_getattr_defaults_false(self):
        self.assertFalse(getattr(SimpleNamespace(), "mood_coord_report_enabled", False))


if __name__ == "__main__":
    unittest.main()
