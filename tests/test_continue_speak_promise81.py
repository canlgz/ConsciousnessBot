"""🤝 §0.81 「5分鐘後繼續說剛剛沒說完的」承諾捕捉漏 → LLM 在同一輪自導自演假兌現。

截圖：bot 正說到「另一種感覺，比較是我自己本身的狀態」，使用者「我要你在5分鐘後繼續說你剛剛沒有說完的」。bot 先答
「好，我記下來了。五分鐘後我會繼續說」——接著**同一輪 11:19** 就吐「🤝 嗨，我來了，現在是 11:25」＋把等一下才要說的
內容現在演完＝**謊稱過了 5 分鐘、假裝已到點兌現**（此刻 11:19、卻說 11:25）。使用者：「這也曾發生幾次」。

真因：`is_scheduled_promise_request("...繼續說...")`=False——① 動作「繼續說」（bot 對使用者接著把話說完，做得到）
既有辨識沒有；② 句首「我要你…」被句首「我」守門誤擋（at_me 為 False）。兩者皆漏 → 沒入帳 → 落 fact_or_chat，
LLM 自導自演整段假兌現（比 §0.80「否認」更糟：這是假裝做到了）。

修（旗標化、預設開、關＝逐位元同現狀）：
- 捕捉：`_continue_speak_hit`（繼續/接著＋說/講/分享/回報、說/講下去、說/講完、再說一次、說給我聽）＝到點真把話說完；
  擋「說給第三方聽」、擋敘述「念給我聽**的**詩真好」。
- 守門：`_USER_CMD_BOT_RE` 放寬句首「我要你/我請你/我希望你…V」＝命令 bot（非自諾），不再誤擋。
- persona：本質區塊加「答應了就只答應、別當場假裝已到點」——絕不在同一則假裝時間過了（別寫「嗨我來了現在是11:25」、
  別把時鐘往前跳、別把等一下的內容現在演完）；此刻幾點就是幾點。
一旦真入帳＝走 scheduled 路由、給乾淨 ack 後 return，LLM 沒機會演假兌現；到點才由生命迴圈真發。
"""

import os
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from telegram_monitor import monitor, selfstate, persona, config
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
            f"好，我記下來了，{when.strftime('%H:%M') if when else '到時候'}我會接著說。",
        voice_promise_keep=lambda when, facts, h, promised="", late=False, feeling_ground="", **k:
            f"到點了：{promised}",
        reply=lambda *a, **k: "x", ask=lambda *a, **k: ("chat", None, "x"))


def _cfg(**over):
    base = dict(dry_run=False, telegram_chat_id="", scheduled_promise_enabled=True, promise_emit_enabled=True,
                promise_sched_ttl_sec=21600, timezone="Asia/Taipei", promise_capability_gate_enabled=True,
                promise_reply_bridge_enabled=True, promise_status_ground_enabled=True, promise_cancel_enabled=True,
                deferred_promise_enabled=True, offset_augmentation_enabled=True, sched_leave_autoarm_enabled=True,
                promise_ledger_enabled=True, sched_recur_daily_enabled=True, promise_act_aligned=True,
                promise_tick_resilient_enabled=True, promise_overdue_guard_exempt=True, promise_late_exempt_defer=True)
    base.update(over)
    return SimpleNamespace(**base)


class _Benign:
    def __getattr__(self, name):
        return lambda *a, **k: []


class CaptureTest(unittest.TestCase):
    def test_continue_speak_captures(self):
        for t in ["我要你在5分鐘後繼續說你剛剛沒有說完的", "你在5分鐘後繼續說你剛剛沒有說完的",
                  "5分鐘後繼續說你剛剛沒說完的", "5分鐘後接著講", "10分鐘後把剛剛沒說完的說完",
                  "5分鐘後說給我聽", "我希望你3分鐘後繼續說", "8點你再說一次"]:
            self.assertTrue(selfstate.is_scheduled_promise_request(t), t)

    def test_behavior_label(self):
        self.assertEqual(selfstate.extract_promise_behavior("我要你在5分鐘後繼續說你剛剛沒有說完的"),
                         "接著把剛剛沒說完的話跟他說完")

    def test_third_party_and_narration_not_captured(self):
        for t in ["5分鐘後說給老闆聽", "5分鐘後繼續說給客戶聽", "你8點念給我聽的詩真好",
                  "繼續說啊", "你剛剛說完了嗎", "我要你的答案"]:
            self.assertFalse(selfstate.is_scheduled_promise_request(t), t)

    def test_user_command_bot_not_self_promise(self):
        # 「我要你V」＝命令 bot（放寬句首我守門）；「我8點回應你」＝自諾（仍擋）
        self.assertTrue(selfstate.is_scheduled_promise_request("我要你5分鐘後繼續說"))
        self.assertTrue(selfstate.is_scheduled_promise_request("我要你8點道歉"))       # bot 對使用者道歉＝做得到
        self.assertFalse(selfstate.is_scheduled_promise_request("我8點回應你"))

    def test_command_bot_possessive_third_party_not_captured(self):
        # 🛡️ 審查 HIGH 修：「我要你老闆/媽/朋友…V」＝所有格第三方（你的X）→ 正向命令框架擋（你後接人物名詞不放寬守門）
        for t in ["我要你老闆8點道歉", "我要你媽8點道歉", "我要你老闆8點問候", "我要你朋友8點鼓勵", "我你8點道歉"]:
            self.assertFalse(selfstate.is_scheduled_promise_request(t), t)

    def test_completion_narration_and_thirdparty_delivery_not_captured(self):
        # 🛡️ 審查 MED 修：不收裸「說完/講完」→ 擋敘述「我講完了」與第三方「說完報告給老闆」
        for t in ["8點的簡報我講完了", "他8點說完會議紀錄給老闆", "8點講完報告給客戶",
                  "你8點說完進度給老闆", "8點說完給主管", "你講完了嗎"]:
            self.assertFalse(selfstate.is_scheduled_promise_request(t), t)

    def test_command_bot_relax_reverts_with_flag(self):
        # 審查（byte-identity 修）：泛命令框架放寬只受 SCHED_CONTINUE_SPEAK 管（§0.83 審查 LOW 修後兩旗標各自獨立）——
        # 非自陳內在的泛命令「我要你8點道歉」，SCHED_CONTINUE_SPEAK=0 單獨即退回舊句首我守門（不受 SCHED_SELF_EXPLAIN 影響）
        os.environ["SCHED_CONTINUE_SPEAK"] = "0"
        try:
            self.assertFalse(selfstate.is_scheduled_promise_request("我要你8點道歉"))
        finally:
            os.environ.pop("SCHED_CONTINUE_SPEAK", None)

    def test_flag_off_bytewise(self):
        os.environ["SCHED_CONTINUE_SPEAK"] = "0"
        try:
            for t in ["5分鐘後繼續說你剛剛沒說完的", "5分鐘後接著講", "5分鐘後說給我聽"]:
                self.assertFalse(selfstate.is_scheduled_promise_request(t), t)
        finally:
            os.environ.pop("SCHED_CONTINUE_SPEAK", None)


class EndToEndTest(unittest.TestCase):
    def test_real_capture_no_fake_fire_then_real_fire(self):
        st = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        st.owner_folder_id = "F"
        c = FakeClient()
        monitor.handle_message(
            {"message": {"chat": {"id": 1}, "text": "我要你在5分鐘後繼續說你剛剛沒有說完的", "date": NOW.timestamp()}},
            _coach(), _Benign(), {"meta": {}}, None, st, c, _cfg(), TZ)
        pend = [p for p in (st.scheduled_promises or []) if not p.get("fulfilled")]
        self.assertEqual(len(pend), 1)                                # 真入帳（不再空口/假兌現）
        self.assertEqual(datetime.fromtimestamp(pend[0]["target_ts"], timezone.utc).astimezone(TZ).strftime("%H:%M"), "11:24")
        joined = "".join(c.sent)
        self.assertIn("11:24", joined)                               # ack 講對的到點時刻
        for fake in ["11:25", "我來了", "現在是"]:                    # **不**在同輪假裝已到點
            self.assertNotIn(fake, joined)
        # 同一時刻（11:19）跑生命迴圈：**不**兌現（未到點，不假發）
        c2 = FakeClient()
        monitor._promise_emit(c2, st, _cfg(), _coach(), NOW)
        self.assertEqual(c2.sent, [])
        # 11:24 到點：真發
        c3 = FakeClient()
        monitor._promise_emit(c3, st, _cfg(), _coach(), NOW + timedelta(minutes=5))
        self.assertTrue(c3.sent)


class PersonaTest(unittest.TestCase):
    def test_essence_forbids_fake_fastforward(self):
        s = persona.SOCRATIC_SYSTEM
        self.assertIn("別當場假裝已經到點", s)
        self.assertIn("別自己把時鐘往前跳", s)
        self.assertIn("此刻幾點就是幾點", s)


class ConfigDefaultTest(unittest.TestCase):
    def test_flag_default_on(self):
        os.environ.pop("SCHED_CONTINUE_SPEAK", None)
        self.assertTrue(config.Config.load().sched_continue_speak_enabled)


if __name__ == "__main__":
    unittest.main()
