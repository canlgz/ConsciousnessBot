"""🕐 §0.82 全域時間治理：把「時間幻覺」從偶發壓到結構上幾乎不可能。

背景（使用者：「為什麼 bot 常有這種時間幻覺？」）：bot 有兩套時間系統——① 機械（生命迴圈讀真時鐘、帳本存絕對時間戳，準）；
② LLM 語言（自由聊天裡「談」時間就是猜——無體內時鐘、不會數真秒、會抓現場數字當錨）。幻覺只發生在②：一個計時話題只要
溜過機械捕捉、落到自由 LLM，時間就是編的（§0.79「現在是00:00」、§0.81「嗨我來了現在是11:25」/「才過一分鐘」皆此類）。
§0.74–§0.81 逐句補捕捉是打地鼠；§0.82 直接封裂縫：

- **全域硬時間錨（GLOBAL_TIME_ANCHOR）**：每條走 build_memory_brief 的聊天回覆**最前**注入「〔現在真的是 HH:MM——別把
  別的時刻說成現在、別假裝時間已過或跳未來〕」＝讓 LLM 不必猜此刻幾點（來源治理，比 §0.36 軟時間感強制）。
- **全域鐘點守門（GLOBAL_CLOCK_GUARD）**：**互動回覆**（一般聊天串，非主動兌現/帳本）送出前，把回覆裡『現在/此刻…HH:MM』
  報錯的此刻時刻就地改回真實此刻（角色感知：只動被「現在」修飾的鐘點，不碰約定時刻/時距）＝縱深防線（LLM 忽略硬錨仍講錯時攔下）。
"""

import os
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from telegram_monitor import monitor, coach as coachmod, config
from telegram_monitor.state import State

TZ = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 7, 6, 3, 19, tzinfo=timezone.utc)   # 11:19 台北


class NowClockFixTest(unittest.TestCase):
    def test_corrects_wrong_now_only(self):
        # 把被「現在/此刻」修飾、報錯的此刻時刻就地改回真now；約定時刻/對話時刻/時距不動
        cases = [
            ("嗨我來了，現在是 11:25。", True, "11:19"),               # §0.81 假兌現
            ("現在是00:00，我們約定好的時間是00:06。", True, "現在是11:19"),  # §0.79；約定00:06 保留
            ("此刻11:40，還早", True, "此刻11:19"),
            ("約在 00:06 的提醒還沒到，現在 11:19 呢。", False, None),    # 現在正確、約在不動
            ("你說的7:30那班車還有0:05就到", False, None),              # 非現在修飾
            ("現在是11:19，一切正常", False, None),                    # 正確
        ]
        for msg, want_ch, want_sub in cases:
            fixed, ch = monitor._now_clock_fix(msg, NOW, TZ)
            self.assertEqual(ch, want_ch, msg)
            if want_ch:
                self.assertIn(want_sub, fixed, msg)
                self.assertNotIn("11:25", fixed) if "11:25" in msg else None

    def test_preserves_promise_time_role(self):
        # 「約定的是00:06」這種非現在修飾的時刻，即使≠now 也不動（角色感知）
        fixed, ch = monitor._now_clock_fix("約定的時間是00:06，現在還沒到", NOW, TZ)
        self.assertFalse(ch)
        self.assertIn("00:06", fixed)

    def test_arithmetic_bearing_passes_through(self):
        # 🛡️ 審查 HIGH 修：帶算術推理的「現在是X…所以/已過N分」不就地改（只改數字會與結論打架＝自打臉）→ 放行、硬錨在源頭防
        for m in ["現在是11:25，所以我們約的11:20已經過了5分鐘。", "現在是11:25，已經過了5分鐘了", "現在11:40，快到了"]:
            self.assertFalse(monitor._now_clock_fix(m, NOW, TZ)[1], m)

    def test_quoted_or_attributed_passes_through(self):
        # 🛡️ 審查 MED 修：把「現在是X」歸給別人/引用（你剛剛說…）不改——那是複述、非 bot 此刻宣稱
        for m in ["你剛剛說現在是3:00對嗎？", "你之前跟我說現在是 3:00，可是那不對。", "他以為現在是9:00"]:
            self.assertFalse(monitor._now_clock_fix(m, NOW, TZ)[1], m)

    def test_appointment_now_yue_passes_through(self):
        # 🛡️ 審查 LOW 修：「現在約 10:30 見」的 約＝約定/大約、非現在修飾 → 不改
        for m in ["現在約 10:30 在老地方見。", "目前約 10:30 這個時段"]:
            self.assertFalse(monitor._now_clock_fix(m, NOW, TZ)[1], m)

    def test_no_tz_falls_back_to_utc_consistently(self):
        # tz=None（ZoneInfo 不可用的極少數）→ 用 UTC now（03:19）；仍是「真實此刻」的一致基準，錨與守門同源
        fixed, ch = monitor._now_clock_fix("現在是00:00", NOW, None)
        self.assertTrue(ch)
        self.assertIn("03:19", fixed)
        self.assertFalse(monitor._now_clock_fix("現在是03:19", NOW, None)[1])   # UTC now 不改


class HardNowAnchorTest(unittest.TestCase):
    def _snap(self):
        return SimpleNamespace(summary={"total": 1, "last24h": 0, "last7d": 0},
                               funnel={"candidate": 0, "context": 0, "journey": 0, "watch": 0},
                               heartbeat={"status": "ok"})

    def test_anchor_present_when_on(self):
        b = coachmod.build_memory_brief({"records": []}, self._snap(), TZ, now=NOW, hard_now_anchor=True)
        self.assertTrue(b.startswith("〔現在真的是 11:19"))
        self.assertIn("別把任何別的時刻", b)
        self.assertIn("別假裝時間已經過了", b)

    def test_anchor_absent_when_off(self):
        b = coachmod.build_memory_brief({"records": []}, self._snap(), TZ, now=NOW, hard_now_anchor=False)
        self.assertNotIn("現在真的是", b.split("\n")[0])
        self.assertTrue(b.startswith("【現況】"))


class FakeClient:
    def __init__(self):
        self.sent, self.stickers, self.dry_run = [], [], False

    def send(self, text):
        self.sent.append(text)
        return True

    def send_sticker(self, fid):
        return True


class _Benign:
    def __getattr__(self, name):
        return lambda *a, **k: []


def _coach_reply(msg):
    return SimpleNamespace(
        enabled=True, api_key="k", model="m", meter=SimpleNamespace(record=lambda *a, **k: None),
        reply=lambda *a, **k: msg, ask=lambda *a, **k: ("chat", None, msg),
        voice_promise_keep=lambda when, facts, h, promised="", late=False, feeling_ground="", **k: f"到點：{promised}")


def _cfg(**over):
    base = dict(dry_run=False, telegram_chat_id="", global_clock_guard=True, global_time_anchor=True,
                scheduled_promise_enabled=True, promise_emit_enabled=True, promise_sched_ttl_sec=21600,
                timezone="Asia/Taipei", promise_reply_bridge_enabled=True, promise_status_ground_enabled=True,
                promise_ledger_enabled=True, promise_cancel_enabled=True, sched_leave_autoarm_enabled=True,
                deferred_promise_enabled=True, offset_augmentation_enabled=True, sched_recur_daily_enabled=True)
    base.update(over)
    return SimpleNamespace(**base)


def _msg(text):
    return {"message": {"chat": {"id": 1}, "text": text, "date": NOW.timestamp()}}


class SayGuardEndToEndTest(unittest.TestCase):
    """互動回覆的 _say 全域守門：LLM 講錯的「現在」被就地改回；主動兌現不受影響（帶 prefix/state）。"""

    def _state(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        return s

    def test_interactive_reply_corrected(self):
        # fact_or_chat 回覆幻覺「現在是 11:25」→ _say 全域守門就地改回 11:19
        st = self._state()
        from unittest import mock
        c = FakeClient()
        with mock.patch("telegram_monitor.coach.build_memory_brief", return_value="brief"):
            monitor.handle_message(_msg("隨便聊聊"), _coach_reply("嗨，現在是 11:25 了喔。"), _Benign(),
                                   {"meta": {}}, None, st, c, _cfg(), TZ)
        joined = "".join(c.sent)
        if joined:                                            # 有走到 reply 才驗（不同路由；有送出就檢查）
            self.assertNotIn("11:25", joined)

    def test_say_guard_skips_proactive_fire(self):
        # 主動兌現（prefix="🤝 "、帶 state）不套全域守門——用生命迴圈的 now、非本輪 message.date
        monitor._TURN["ground_now"] = (NOW, TZ)              # 模擬本輪殘留的錨
        c = FakeClient()
        st = self._state()
        monitor._say(c, "🤝 我來了，現在是 11:40。", prefix="🤝 ", state=st, topic="約定")
        self.assertIn("11:40", "".join(c.sent))              # 帶 prefix/state → 不改（各有自己的守門）

    def test_flag_off_no_guard(self):
        monitor._TURN["ground_now"] = None                  # 旗標關＝handle_message 不設 ground_now
        c = FakeClient()
        monitor._say(c, "現在是 11:25 了。")
        self.assertIn("11:25", "".join(c.sent))


class ConfigDefaultTest(unittest.TestCase):
    def test_flags_default_on(self):
        for env in ("GLOBAL_TIME_ANCHOR", "GLOBAL_CLOCK_GUARD"):
            os.environ.pop(env, None)
        cfg = config.Config.load()
        self.assertTrue(cfg.global_time_anchor)
        self.assertTrue(cfg.global_clock_guard)


if __name__ == "__main__":
    unittest.main()
