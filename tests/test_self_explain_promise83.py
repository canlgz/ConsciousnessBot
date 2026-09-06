"""🤝 §0.83 「N分鐘後分享/說明你的內心運作機制/狀況」承諾捕捉漏 → 被內容型 self_mechanism 路由搶走、丟排程。

截圖（IMG_1412）：使用者「我要你10分鐘後再分享一下內心感覺的運作機制還有轉述的狀況」。這是請 bot 到點跟使用者
**分享/說明自己的內在運作**（bot 做得到的排程承諾）；但「分享…運作機制」被**內容型** self_mechanism 路由搶走
（intent 排 scheduled 之後、但 `is_scheduled_promise_request` 這裡漏收 → 落 self_mechanism/自由聊天）→ 丟了
「10分鐘後」排程 → 沒入帳 → LLM 假兌現＋亂報時刻（截圖的 11:40 是幻覺，真解析是 12:58）。

真因：動作＝溝通動詞（分享/說明/解釋/描述/說說/報告…）＋ **bot 自身內在內容詞**（內心/內在/運作/轉速/轉述/處境…），
`is_scheduled` 既有辨識沒有這一類「對使用者自陳內在」的動作 → 漏收。

修（旗標化 SCHED_SELF_EXPLAIN、預設開、關＝逐位元同現狀）：
- 捕捉：`_self_explain_hit`＝溝通動詞 + 內在內容詞；**須**有明確 bot 自身內在錨（內心/內在/運作/轉速/轉述/處境），
  泛詞（狀態/狀況/想法/心情/情緒/感覺）或第三方所有格（你老闆的想法／我媽的心情）**不收**（正向錨 + 第三方守門）。
- `is_scheduled_promise_request` / `looks_like_timed_request` / `extract_promise_behavior` 皆納入；`_USER_CMD_BOT_RE`
  句首「我要你V」守門放寬也認本旗標。
一旦真入帳＝走 scheduled 路由、乾淨 ack 後 return，LLM 沒機會演假兌現／亂報時刻；到點才由生命迴圈真發。
"""

import os
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from telegram_monitor import monitor, selfstate, config
from telegram_monitor.state import State

TZ = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 7, 6, 3, 19, tzinfo=timezone.utc)   # 11:19 台北


class FakeClient:
    def __init__(self):
        self.sent, self.stickers, self.dry_run = [], [], False

    def send(self, text):
        self.sent.append(text)
        return True

    def send_sticker(self, fid):
        return True


def _coach():
    return SimpleNamespace(
        enabled=True, api_key="k", model="m", meter=SimpleNamespace(record=lambda *a, **k: None),
        voice_schedule_ack=lambda q, when, h, sticker_hint="":
            f"好，我記下來了，{when.strftime('%H:%M') if when else '到時候'}我會跟你說。",
        voice_promise_keep=lambda when, facts, h, promised="", late=False, feeling_ground="", **k:
            f"到點了：{promised}",
        reply=lambda *a, **k: "x", ask=lambda *a, **k: ("chat", None, "x"))


class _Benign:
    def __getattr__(self, name):
        return lambda *a, **k: []


def _cfg(**over):
    base = dict(dry_run=False, telegram_chat_id="", scheduled_promise_enabled=True, promise_emit_enabled=True,
                promise_sched_ttl_sec=21600, timezone="Asia/Taipei", promise_capability_gate_enabled=True,
                promise_reply_bridge_enabled=True, promise_status_ground_enabled=True, promise_cancel_enabled=True,
                deferred_promise_enabled=True, offset_augmentation_enabled=True, sched_leave_autoarm_enabled=True,
                promise_ledger_enabled=True, sched_recur_daily_enabled=True, promise_act_aligned=True,
                promise_tick_resilient_enabled=True, promise_overdue_guard_exempt=True, promise_late_exempt_defer=True,
                global_clock_guard=True, global_time_anchor=True)
    base.update(over)
    return SimpleNamespace(**base)


class CaptureTest(unittest.TestCase):
    def test_self_explain_captures(self):
        for t in ["我要你10分鐘後再分享一下內心感覺的運作機制還有轉述的狀況",
                  "10分鐘後跟我說明你的內在運作", "5分鐘後說說你此刻的內心轉速",
                  "8點跟我解釋一下你內在是怎麼運作的", "待會報告一下你內心的處境給我"]:
            self.assertTrue(selfstate._self_explain_hit(t), t)

    def test_scheduled_with_time(self):
        # 帶明確時距／時刻 → 真入排程承諾（不再落 self_mechanism）
        for t in ["我要你10分鐘後再分享一下內心感覺的運作機制還有轉述的狀況",
                  "10分鐘後跟我說明你的內在運作", "5分鐘後說說你此刻的內心轉速"]:
            self.assertTrue(selfstate.is_scheduled_promise_request(t), t)

    def test_third_party_inner_not_captured(self):
        # 🛡️ 審查（第三方修）：講**別人的**內在（你老闆的想法／我媽的心情）≠ bot 自陳 → 不收
        for t in ["20分鐘後跟我說說你老闆的想法", "10分鐘後分享我媽的心情",
                  "5分鐘後說明你同事的內心", "8點報告對方的情緒"]:
            self.assertFalse(selfstate._self_explain_hit(t), t)

    def test_external_subject_strict_word_not_captured(self):
        # 🛡️ 審查 HIGH 修：舊 strict.search 的開放類洩漏——泛內在詞（運作/轉速/轉述/處境）＋非 bot 主語（機器/伺服器/
        # 演算法/公司/偶像/貓…，皆**不在**任何列舉角色表）被誤收成「跟他說說我內在此刻的運作與狀態」＝把講機器誤標成講自己。
        # 改正向綁定後：這些「某物的運作／某人的內在」皆不收（不列黑名單、靠『的』前非 bot governor 判外部所有）。
        for t in ["10分鐘後說明這台機器的運作", "20分鐘後說明伺服器的運作",
                  "10分鐘後說明這個演算法的運作", "10分鐘後說說你們公司的運作",
                  "20分鐘後說說你偶像的內在", "5分鐘後說明你貓的處境",
                  "10分鐘後說明系統運作", "10分鐘後解釋這個程式的內在邏輯",
                  "8點報告引擎轉速的狀況"]:
            self.assertFalse(selfstate._self_explain_hit(t), t)
            # 且行為標籤不再冒出第一人稱自陳「我內在此刻的運作」（免到點主動兌現答非所問）
            self.assertNotEqual(selfstate.extract_promise_behavior(t), "跟他說說我內在此刻的運作與狀態", t)

    def test_generic_status_not_captured(self):
        # 泛詞（伺服器/專案/大盤的狀態·狀況）非 bot 內在錨 → 不收（避免把外部狀態誤當自陳）
        for t in ["10分鐘後說明伺服器的狀態", "5分鐘後報告專案的狀況", "8點分享一下大盤的想法"]:
            self.assertFalse(selfstate._self_explain_hit(t), t)

    def test_no_time_not_scheduled(self):
        # 有自陳動作但無時間 → 非排程（走一般自陳，不冒排入帳）
        for t in ["分享一下你的內心運作", "說說你內在的轉速"]:
            self.assertFalse(selfstate.is_scheduled_promise_request(t), t)

    def test_behavior_label_no_time_word(self):
        # §0.83 專屬退路標籤不含「現在/此刻」以外的錯時；純自陳、兌現時由生命迴圈給真時刻
        lbl = selfstate.extract_promise_behavior("5分鐘後說說你此刻的內心轉速")
        self.assertTrue(lbl)
        self.assertIn("運作", lbl)

    def test_looks_like_timed(self):
        self.assertTrue(selfstate.looks_like_timed_request("我要你10分鐘後再分享一下內心的運作"))


class FlagOffTest(unittest.TestCase):
    def test_flag_off_bytewise(self):
        # SCHED_SELF_EXPLAIN=0 → _self_explain_hit 全回 False＝逐位元同現狀
        os.environ["SCHED_SELF_EXPLAIN"] = "0"
        try:
            for t in ["我要你10分鐘後再分享一下內心感覺的運作機制還有轉述的狀況",
                      "10分鐘後跟我說明你的內在運作", "5分鐘後說說你此刻的內心轉速"]:
                self.assertFalse(selfstate._self_explain_hit(t), t)
        finally:
            os.environ.pop("SCHED_SELF_EXPLAIN", None)

    def test_self_explain_command_relax_independent(self):
        # 🛡️ 審查 LOW 修：兩旗標各自獨立管自己的「我要你V」守門放寬。先關掉 §0.81 泛命令放寬（CONTINUE=0），隔離出
        # §0.83 自陳內在命令的放寬：SELF_EXPLAIN=1 仍放行（_self_cmd）、SELF_EXPLAIN=0 退回舊句首我守門。
        msg = "我要你10分鐘後分享你的內在運作"
        os.environ["SCHED_CONTINUE_SPEAK"] = "0"
        try:
            os.environ["SCHED_SELF_EXPLAIN"] = "1"
            self.assertTrue(selfstate.is_scheduled_promise_request(msg))
            os.environ["SCHED_SELF_EXPLAIN"] = "0"
            self.assertFalse(selfstate.is_scheduled_promise_request(msg))
        finally:
            os.environ.pop("SCHED_CONTINUE_SPEAK", None)
            os.environ.pop("SCHED_SELF_EXPLAIN", None)


class EndToEndTest(unittest.TestCase):
    def test_real_capture_clean_ack_no_fake_fire_then_real_fire(self):
        st = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        st.owner_folder_id = "F"
        c = FakeClient()
        monitor.handle_message(
            {"message": {"chat": {"id": 1},
                         "text": "我要你10分鐘後再分享一下內心感覺的運作機制還有轉述的狀況",
                         "date": NOW.timestamp()}},
            _coach(), _Benign(), {"meta": {}}, None, st, c, _cfg(), TZ)
        pend = [p for p in (st.scheduled_promises or []) if not p.get("fulfilled")]
        self.assertEqual(len(pend), 1)                                # 真入帳（不再落 self_mechanism 丟排程）
        self.assertEqual(
            datetime.fromtimestamp(pend[0]["target_ts"], timezone.utc).astimezone(TZ).strftime("%H:%M"), "11:29")
        joined = "".join(c.sent)
        self.assertIn("11:29", joined)                               # ack 講對的到點時刻（10 分鐘後）
        for fake in ["11:40", "12:48", "我來了", "現在是"]:            # **不**在同輪假兌現／幻覺時刻（截圖的 11:40）
            self.assertNotIn(fake, joined)
        # 同一時刻（11:19）跑生命迴圈：**不**兌現（未到點）
        c2 = FakeClient()
        monitor._promise_emit(c2, st, _cfg(), _coach(), NOW)
        self.assertEqual(c2.sent, [])
        # 11:29 到點：真發
        c3 = FakeClient()
        monitor._promise_emit(c3, st, _cfg(), _coach(), NOW + timedelta(minutes=10))
        self.assertTrue(c3.sent)


class ConfigDefaultTest(unittest.TestCase):
    def test_flag_default_on(self):
        os.environ.pop("SCHED_SELF_EXPLAIN", None)
        self.assertTrue(config.Config.load().sched_self_explain_enabled)


if __name__ == "__main__":
    unittest.main()
