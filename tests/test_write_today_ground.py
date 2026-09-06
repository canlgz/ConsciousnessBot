"""🕐 §1.60 記寫時間脈絡接地（WRITE_TODAY_GROUND）：bot 不再把自己的推播時間當成使用者的記寫時間。

截圖根因（07/21 09:01–09:15，使用者原話「bot是不是誤以為9:01的bot的每日自動回報記寫狀況，當成是我剛剛
完成的記寫事情」——完全正確）：真相＝最後記寫 07/20 09:37（昨天、23 小時前，09:13 的確定性時鐘 lane 答對了）。
但 ①09:02 摘要感想先講錯「你**今天**又繼續讀經了呀」（LLM 把「近 24h 1 則」讀成「今天有寫」）；②這句錯話
進了對話史 → 之後每輪引用自己的錯話當證據（09:08「你今天早上不是才又記寫了嗎」）＝自我污染鏈；③09:15
把每日摘要的推播時間 9:01 說成「你今天早上 9 點 01 分記下讀誦經書的進度」＝把 bot 自己的報表當成使用者的
行為事件。接地明明存在（時鐘 lane 答對）、自由聊天 lane 卻不看。

§1.60 四件（一旗；config 預設 True／monitor 端 getattr 預設 False＝逐位元同現狀）：
A. _write_ground_data 純函式——今天已記寫 N 則＋最後記寫人話標籤（今天/昨天/前天/M/D＋HH:MM），全程式算；
B. 摘要/歸戶/事件的 reflect prompt 附【記寫時間錨】硬事實（最後記寫不是今天＝明令絕不說「今天有記」）；
C. 確定性「今天記寫宣稱」守門——今天其實零記寫時，凡「你＋今天＋記寫/寫了/讀經/記下…」的宣稱句整句換
   「你最近一次記寫是{標籤}——今天到現在還沒有新的記寫。」（否定句「你今天還沒記寫」＝誠實、不動）；
   互動 _say 與四個 reflect 送出點都擋；
D. 「今天做了/還沒做什麼」問句 → 注入今天記寫真實數據 hint（把摘要/推播時間當他的記寫時間＝明令禁止）。
全 stub、零網路。
"""

import os
import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock
from zoneinfo import ZoneInfo

from telegram_monitor import monitor
from telegram_monitor.state import State

TZ = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 7, 21, 1, 13, tzinfo=timezone.utc)          # 台北 07/21 09:13（截圖時刻）
LAST = datetime(2026, 7, 20, 1, 37, tzinfo=timezone.utc)         # 最後記寫 07/20 09:37（昨天）

REC_Y = {"id": "r1", "ts": LAST, "key": "生活|閱讀 讀誦經書", "status": "journey",
         "category": "生活", "topicLabel": "閱讀｜讀誦經書", "text": "又把第一、第二品讀完一輪"}


def _ground(today=0):
    label = "今天 09:01" if today else "昨天 09:37"
    return {"today_count": today, "last_label": label}


# ── A：ground 純函式 ────────────────────────────────────────────────────────
class GroundDataTest(unittest.TestCase):
    def test_yesterday_only(self):
        snap = SimpleNamespace(summary={"last_write": LAST})
        g = monitor._write_ground_data(snap, {"records": [REC_Y]}, NOW.timestamp(), TZ)
        self.assertEqual(g["today_count"], 0)
        self.assertIn("昨天", g["last_label"])
        self.assertIn("09:37", g["last_label"])

    def test_today_write_counted(self):
        rec_t = dict(REC_Y, id="r2", ts=NOW - timedelta(minutes=12))
        snap = SimpleNamespace(summary={"last_write": rec_t["ts"]})
        g = monitor._write_ground_data(snap, {"records": [REC_Y, rec_t]}, NOW.timestamp(), TZ)
        self.assertEqual(g["today_count"], 1)
        self.assertIn("今天", g["last_label"])


# ── C：宣稱守門純函式 ───────────────────────────────────────────────────────
class ClaimFixTest(unittest.TestCase):
    CLAIMS = ("你今天又繼續讀經了呀。",                                         # 09:02 摘要感想
              "可是，你今天早上不是才又記寫了你的讀經進度嗎？",                  # 09:08
              "但我看到你今天早上其實有記寫了你的讀經進度耶。",                  # 09:12
              "我這邊顯示你今天早上 9 點 01 分的時候，的確有記下「讀誦經書」的進度喔。",  # 09:15
              "這句就是你今天寫的啊。")                                         # 09/03 截圖

    def test_screenshot_claims_replaced_when_no_write_today(self):
        for t in self.CLAIMS:
            out, changed = monitor._write_claim_fix(t, _ground(today=0))
            self.assertTrue(changed, t)
            self.assertIn("昨天 09:37", out)
            self.assertIn("還沒有新的記寫", out)
            self.assertNotIn("今天早上", out)

    def test_honest_negation_untouched(self):
        for t in ("你今天還沒有新的記寫。", "你今天沒有記寫喔。"):
            out, changed = monitor._write_claim_fix(t, _ground(today=0))
            self.assertFalse(changed, t)

    def test_today_actually_wrote_untouched(self):
        out, changed = monitor._write_claim_fix("你今天又記寫了讀經進度。", _ground(today=1))
        self.assertFalse(changed)

    def test_other_sentences_kept(self):
        t = "早安。你今天又繼續讀經了呀。這個習慣很好。"
        out, changed = monitor._write_claim_fix(t, _ground(today=0))
        self.assertTrue(changed)
        self.assertIn("早安。", out)
        self.assertIn("這個習慣很好。", out)

    def test_ground_none_noop(self):
        t = "你今天又繼續讀經了呀。"
        self.assertEqual(monitor._write_claim_fix(t, None), (t, False))

    def test_causal_tail_from_wrong_claim_is_removed(self):
        msg = ("這句就是你今天寫的啊。"
               "所以不是沒動喔，你有動，而且是關於〔閱讀｜讀誦經書〕這條線。")
        out, changed = monitor._write_claim_fix(msg, _ground(today=0))
        self.assertTrue(changed)
        self.assertIn("昨天 09:37", out)
        self.assertNotIn("你有動", out)

    def test_words_inside_quote_are_not_treated_as_new_claim(self):
        msg = "你是在引用「我今天讀誦完了」這句，不是在說我真的做過。"
        self.assertEqual(monitor._write_claim_fix(msg, _ground(today=0)), (msg, False))


# ── D：今天問句偵測 ─────────────────────────────────────────────────────────
class TodayQuestionTest(unittest.TestCase):
    def test_positives(self):
        for t in ("我今天還沒做什麼？", "我今天什麼還沒做", "我今天什麼還沒有做", "今天做了什麼",
                  "我今天有做什麼？", "所以我到今天都沒動？"):
            self.assertTrue(monitor._TODAY_DONE_Q_RE.search(t), t)

    def test_negatives(self):
        for t in ("我今天做了蛋糕", "我今天沒有動力", "我今天沒有動機", "現在幾點", "早安", "今天天氣不錯"):
            self.assertFalse(monitor._TODAY_DONE_Q_RE.search(t), t)

    def test_hint_limits_truth_to_visible_writes(self):
        saved = dict(monitor._TURN)
        monitor._TURN["write_claim_ground"] = _ground(today=0)
        try:
            hint = monitor._today_write_hint(None, SimpleNamespace(write_today_ground_enabled=True),
                                             "所以我到今天都沒動？")
            self.assertIn("不等於他今天真的什麼都沒做", hint)
            self.assertIn("不要替他斷言", hint)
        finally:
            monitor._TURN.clear()
            monitor._TURN.update(saved)


# ── B＋C：tick reflect 路徑（摘要感想不再說「今天」）──────────────────────
class ReflectGroundTest(unittest.TestCase):
    SNAP = SimpleNamespace(filed_records=[], heartbeat={"status": "ok"},
                           summary={"total": 470, "last24h": 1, "last7d": 21, "streak": 40,
                                    "media": {}, "last_write": LAST, "active_explorations": 0},
                           funnel={"candidate": 16, "context": 11, "journey": 10, "watch": 1},
                           gaps=[], formed_journeys=[], formed_contexts=[], near_upgrade_sig={})

    def _run(self, flag=True):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        s.last_push_ts = NOW.timestamp() - 3600
        s.last_digest_date = "2026-07-20"
        s.convo_history = []
        client = SimpleNamespace(dry_run=False, sent=[])
        client.send = lambda t: client.sent.append(t) or True
        seen = {"desc": None}
        coach = SimpleNamespace(enabled=True,
                                meter=SimpleNamespace(record=lambda *a, **k: None, usd_twd=32.0),
                                seen=seen)

        def reflect(kind, desc, brief, connect=""):
            seen["desc"] = desc
            return "你今天又繼續讀經了呀。感覺你最近讀經的進度很穩定。"
        coach.reflect = reflect
        cfg = dict(dry_run=False, llm_voice=True, notify_filings=True, filing_max_age_h=24,
                   telegram_chat_id="", notify_cooldown_min=30)
        if flag is not None:
            cfg["write_today_ground_enabled"] = flag
        with mock.patch("telegram_monitor.coach.build_memory_brief", return_value=""), \
             mock.patch.object(monitor, "_digest_due", return_value=True), \
             mock.patch.object(monitor, "_compute_events", return_value={"any": False}):
            monitor.tick(None, client, s, SimpleNamespace(**cfg), TZ, now=NOW, coach=coach,
                         precollected=({"records": [REC_Y], "meta": {}}, self.SNAP))
        return client, seen

    def test_anchor_injected_and_claim_fixed(self):
        client, seen = self._run()
        self.assertIn("記寫時間錨", seen["desc"] or "")                 # B：reflect prompt 帶硬事實
        joined = "\n".join(client.sent)
        self.assertNotIn("你今天又繼續讀經了呀", joined)                 # C：說錯也被出口守門換掉
        self.assertIn("昨天 09:37", joined)

    def test_flag_off_bitwise(self):
        for flag in (False, None):
            client, seen = self._run(flag=flag)
            self.assertNotIn("記寫時間錨", seen["desc"] or "", str(flag))
            self.assertIn("你今天又繼續讀經了呀", "\n".join(client.sent), str(flag))   # 截圖行為釘住


# ── C＋D：互動路徑 ─────────────────────────────────────────────────────────
class InteractiveGroundTest(unittest.TestCase):
    class Cl:
        def __init__(self): self.sent, self.dry_run = [], False
        def send(self, t): self.sent.append(t); return True

    SNAP = ReflectGroundTest.SNAP
    BOOM = SimpleNamespace(load_embedding_records=lambda *_: [])

    def _cfg(self, on=True):
        base = dict(dry_run=False, telegram_chat_id="", scheduled_promise_enabled=True,
                    promise_emit_enabled=True, promise_sched_ttl_sec=21600, timezone="Asia/Taipei",
                    notify_cooldown_min=30, promise_reply_bridge_enabled=False, promise_ledger_enabled=True,
                    sched_leave_autoarm_enabled=True, deferred_promise_enabled=False,
                    promise_llm_rescue_enabled=False, sticker_llm_rescue_enabled=False,
                    promise_keep_claim_guard_enabled=True, promise_said_ground_enabled=True,
                    bot_self_promise_enabled=False, promise_preempt_enabled=False,
                    sticker_sent_memory_enabled=True, send_stickers=True,
                    sticker_fakesend_guard_enabled=False, recall_ground_guard_enabled=False,
                    selfshare_reason_ground_enabled=False, wake_projection_guard_enabled=False,
                    user_habit_ground_enabled=False, self_feel_condense_enabled=False,
                    promise_deliver_content_enabled=False, mood_coord_report_enabled=False)
        if on is not None:
            base["write_today_ground_enabled"] = on
        return SimpleNamespace(**base)

    def _coach(self, voice):
        seen = {"ask": None, "reply": None}
        c = SimpleNamespace(enabled=True, api_key="k", model="m",
                            meter=SimpleNamespace(record=lambda *a, **k: None), seen=seen)

        def ask(*a, **k):
            seen["ask"] = k.get("extra_system")
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

    def _run(self, cfg, text, voice, history=None):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        if history is not None:
            s.convo_history = history
        cl, co = self.Cl(), self._coach(voice)
        monitor.handle_message({"message": {"chat": {"id": 1}, "text": text, "date": NOW.timestamp()}},
                               co, self.BOOM, {"records": [REC_Y], "meta": {}}, self.SNAP,
                               s, cl, cfg, TZ)
        return "\n".join(cl.sent), co.seen

    def test_elliptical_today_correction_uses_record_not_yesterday_quote(self):
        history = [{"role": "model", "text": "你最近真的持續在讀誦經書耶。",
                    "ts": NOW.timestamp() - 3000}]
        out, seen = self._run(self._cfg(), "我今天還沒吧？", "可是你昨天明明有讀。", history)
        self.assertIn("今天還沒有新記寫", out)
        self.assertIn("昨天 09:37", out)
        self.assertNotIn("可是", out)
        self.assertIsNone(seen["ask"])

    def test_elliptical_correction_does_not_hijack_other_topics(self):
        for previous, text in [("你吃早餐了嗎？", "我今天還沒吧？"),
                               ("你最近持續讀經。", "我今天還沒吃早餐"),
                               ("你最近持續讀經。", "我今天還沒吧？另外幫我查天氣")]:
            state = SimpleNamespace(convo_history=[{"role": "model", "text": previous,
                                                    "ts": NOW.timestamp()}])
            self.assertFalse(monitor._today_write_context(state, text, NOW.timestamp()))

    def test_context_requires_adjacent_recent_model(self):
        history = [{"role": "model", "text": "持續讀經", "ts": NOW.timestamp() - 30000}]
        state = SimpleNamespace(convo_history=history)
        self.assertFalse(monitor._today_write_context(state, "我今天還沒吧？", NOW.timestamp()))
        history.append({"role": "user", "text": "換個話題", "ts": NOW.timestamp()})
        self.assertFalse(monitor._today_write_context(state, "我今天還沒吧？", NOW.timestamp()))

    def test_correction_does_not_turn_other_records_into_proof_of_reading(self):
        state = SimpleNamespace(convo_history=[{"role": "model", "text": "持續讀經",
                                                "ts": NOW.timestamp()}])
        with mock.patch.dict(monitor._TURN, {"write_claim_ground": {
                "today_count": 1, "last_label": "今天 09:00"}}, clear=True):
            self.assertEqual(monitor._today_write_correction(
                state, self._cfg(), "我今天還沒吧？", NOW.timestamp()), "")

    def test_today_question_gets_facts_and_claim_guard(self):
        out, seen = self._run(self._cfg(), "我今天什麼還沒做",
                              "但我看到你今天早上其實有記寫了你的讀經進度耶。")
        got = (seen["ask"] or "") + (seen["reply"] or "")
        self.assertIn("還沒有新的記寫", got)                            # D：真實數據 hint 進 prompt
        self.assertNotIn("你今天早上其實有記寫", out)                    # C：出口守門把幻覺宣稱換掉
        self.assertIn("昨天 09:37", out)

    def test_stop_feedback_does_not_generate_another_version_of_question(self):
        cfg = self._cfg()
        cfg.unanswered_proactive_guard_enabled = True
        out, seen = self._run(cfg, "你幹嘛跳針了？", "抱歉，所以跟週末安排有關嗎？")
        self.assertIn("不再追問", out)
        self.assertNotIn("週末", out)
        self.assertIsNone(seen["ask"])
        self.assertIsNone(seen["reply"])

    def test_flag_off_bitwise(self):
        for on in (False, None):
            out, seen = self._run(self._cfg(on=on), "我今天什麼還沒做",
                                  "但我看到你今天早上其實有記寫了你的讀經進度耶。")
            got = (seen["ask"] or "") + (seen["reply"] or "")
            self.assertNotIn("還沒有新的記寫", got, str(on))
            self.assertIn("你今天早上其實有記寫", out, str(on))          # 截圖行為釘住


# ── §1.60 補遺：ground 單一真相（07/22 09:49 截圖「最近一次記寫是今天 09:37——今天到現在還沒有新的記寫」
# ＝守門模板自己吐出自相矛盾句：today_count 從 data.records 掃、ts 格式缺漏被 except 吞成 0；last_label
# 從 snap.summary.last_write 算、正確知道今天——兩源自打架、模板照印）─────────────────────────────
class GroundConsistencyTest(unittest.TestCase):
    def test_summary_says_today_overrides_broken_records(self):
        # 單一真相：summary.last_write 是今天 → 今天至少 1 則，records 的 ts 缺漏（None/字串）不可信
        today_write = NOW - timedelta(minutes=12)
        snap = SimpleNamespace(summary={"last_write": today_write})
        for bad_records in ([], [{"id": "x", "ts": None}], [{"id": "x", "ts": "2026-07-21T09:37:00"}]):
            g = monitor._write_ground_data(snap, {"records": bad_records}, NOW.timestamp(), TZ)
            self.assertGreaterEqual(g["today_count"], 1, repr(bad_records))
            self.assertIn("今天", g["last_label"])

    def test_contradictory_ground_never_emits_contradiction(self):
        # 縱深：就算未來又出現不一致 ground（count=0 但標籤是今天）——矛盾句在每個出口都不可能成立
        bad = {"today_count": 0, "last_label": "今天 09:37"}
        t = "你今天又記寫了讀經進度。"
        out, changed = monitor._write_claim_fix(t, bad)
        self.assertFalse(changed)                             # 守門不開火（寧可放行原句、不吐矛盾）
        self.assertNotIn("還沒有新的記寫", monitor._write_anchor_line(bad))
        monitor._TURN["write_claim_ground"] = bad
        try:
            hint = monitor._today_write_hint(SimpleNamespace(), SimpleNamespace(write_today_ground_enabled=True),
                                             "我今天什麼還沒做")
            self.assertNotIn("還沒有新的記寫", hint)
        finally:
            monitor._TURN.pop("write_claim_ground", None)

    def test_yesterday_ground_still_fires(self):
        # 回歸：真的昨天＝守門照常（既有行為不受補遺影響）
        out, changed = monitor._write_claim_fix("你今天又記寫了讀經進度。",
                                                {"today_count": 0, "last_label": "昨天 09:37"})
        self.assertTrue(changed)
        self.assertIn("昨天 09:37", out)


# ── 同步 ───────────────────────────────────────────────────────────────────
class ConfigTest(unittest.TestCase):
    def test_config_synced(self):
        src = open("telegram_monitor/config.py", encoding="utf-8").read()
        self.assertIn("WRITE_TODAY_GROUND", src)
        self.assertIn("write_today_ground_enabled", src)
        env = open(".env.example", encoding="utf-8").read()
        self.assertRegex(env, re.compile(r"^WRITE_TODAY_GROUND=1", re.M))
        self.assertIn("WRITE_TODAY_GROUND", open("README.md", encoding="utf-8").read())

    def test_getattr_defaults_false(self):
        self.assertFalse(getattr(SimpleNamespace(), "write_today_ground_enabled", False))


if __name__ == "__main__":
    unittest.main()
