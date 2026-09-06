"""🩺 §1.68 座標回報「只報一次就永遠靜默」修：在場延後＋送達才記帳＋/moodwatch 對帳。

截圖根因（§1.66/§1.67 上線後）：18:17 報過一次（基準推進到 0.69）之後，座標一路走到 0.95/1.00
（Δ 遠超門檻、冷卻早過）卻再也沒報。結構缺陷：原 emit **不管送沒送到都推進基準/冷卻**——
報告被互動打斷/送失敗吃掉一次，那次變動就被無聲消耗，之後座標再怎麼動都在新基準 0.10 內＝再也不報。

修法（比照 _promise_emit 的守約紀律）：
 ① 在場延後：使用者 120 秒內還在打字＝這拍不發、**不動基準**（停下來那拍照發、變動不消耗）；
 ② 送達才記帳：直接 client.send 驗證成功才推進基準/冷卻；失敗＝什麼都不動、下一拍重試（60s 退避）；
 ③ /moodwatch（/座標回報）對帳：基準/此刻/Δ/門檻/冷卻/tick 心跳——「為什麼沒報」當場看得到。
全 stub、零網路。
"""

import os
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from telegram_monitor import monitor
from telegram_monitor.state import State

TZ = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 7, 22, 11, 30, 0, tzinfo=timezone.utc)

CFG = SimpleNamespace(dry_run=False)


class FlakyCl:
    """send 依 script 回 True/False/raise——驗證送達才記帳。"""

    def __init__(self, script):
        self.script, self.sent, self.calls, self.dry_run = list(script), [], 0, False

    def send(self, t):
        self.calls += 1
        r = self.script.pop(0) if self.script else True
        if r == "raise":
            raise RuntimeError("boom")
        if r:
            self.sent.append(t)
        return bool(r)


def _state(v=0.30, a=-0.05, watch=None, last_user=0):
    s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
    s.entropy = SimpleNamespace(mood=v, arousal=a, hunger=0.0)
    s.mood_watch = watch
    s.last_user_msg_ts = last_user
    return s


W = lambda **kw: dict({"ts": 1.0, "last_v": 0.10, "last_a": 0.0, "last_report_ts": 0.0}, **kw)


class DeferTest(unittest.TestCase):
    def test_user_active_defers_and_keeps_baseline(self):
        s = _state(watch=W(), last_user=NOW.timestamp() - 30)   # 30 秒前還在打字
        cl = FlakyCl([True])
        monitor._mood_watch_emit(cl, s, CFG, NOW)
        self.assertEqual(cl.sent, [])                           # 不撞話
        self.assertEqual(s.mood_watch["last_v"], 0.10)          # 基準不動＝變動不被消耗
        self.assertEqual(s.mood_watch["last_report_ts"], 0.0)

    def test_user_quiet_fires(self):
        s = _state(watch=W(), last_user=NOW.timestamp() - 300)  # 停了五分鐘
        cl = FlakyCl([True])
        monitor._mood_watch_emit(cl, s, CFG, NOW)
        self.assertEqual(len(cl.sent), 1)
        self.assertIn("+0.10→+0.30", cl.sent[0])


class SendVerifyTest(unittest.TestCase):
    def test_send_false_keeps_baseline(self):
        s = _state(watch=W())
        cl = FlakyCl([False])
        monitor._mood_watch_emit(cl, s, CFG, NOW)
        self.assertEqual(s.mood_watch["last_v"], 0.10)          # 送失敗＝不記帳
        self.assertEqual(s.mood_watch["last_report_ts"], 0.0)
        self.assertEqual(s.mood_watch["fail_ts"], NOW.timestamp())

    def test_send_raise_keeps_baseline(self):
        s = _state(watch=W())
        cl = FlakyCl(["raise"])
        monitor._mood_watch_emit(cl, s, CFG, NOW)
        self.assertEqual(s.mood_watch["last_v"], 0.10)
        self.assertIn("fail_ts", s.mood_watch)

    def test_fail_backoff_then_retry_succeeds(self):
        s = _state(watch=W(fail_ts=NOW.timestamp() - 30))       # 30 秒前才失敗＝退避中
        cl = FlakyCl([True])
        monitor._mood_watch_emit(cl, s, CFG, NOW)
        self.assertEqual(cl.calls, 0)                           # 退避＝這拍不嘗試
        s.mood_watch["fail_ts"] = NOW.timestamp() - 90          # 退避過了
        monitor._mood_watch_emit(cl, s, CFG, NOW)
        self.assertEqual(len(cl.sent), 1)                       # 重試成功
        self.assertEqual(s.mood_watch["last_v"], 0.30)          # 這才記帳
        self.assertNotIn("fail_ts", s.mood_watch)

    def test_success_updates_push_cooldown(self):
        s = _state(watch=W())
        monitor._mood_watch_emit(FlakyCl([True]), s, CFG, NOW)
        self.assertEqual(s.last_push_ts, NOW.timestamp())       # 與其他推播共用冷卻


class StatusTextTest(unittest.TestCase):
    def test_no_subscription_honest(self):
        s = _state(watch=None)
        out = monitor._mood_watch_status_text(s, CFG, NOW.timestamp())
        self.assertIn("沒有", out)

    def test_armed_shows_delta_and_cooldown(self):
        s = _state(watch=W(last_report_ts=NOW.timestamp() - 600, tick_ts=NOW.timestamp() - 5))
        out = monitor._mood_watch_status_text(s, CFG, NOW.timestamp())
        self.assertIn("活著", out)
        self.assertIn("V +0.10", out)                           # 基準
        self.assertIn("V +0.30", out)                           # 此刻
        self.assertIn("門檻 0.10", out)
        self.assertIn("還剩 1200 秒", out)                      # 冷卻 1800−600
        self.assertIn("5 秒前跑過", out)                        # tick 心跳

    def test_fail_flagged(self):
        s = _state(watch=W(fail_ts=NOW.timestamp() - 10))
        self.assertIn("送出失敗", monitor._mood_watch_status_text(s, CFG, NOW.timestamp()))


class CommandTest(unittest.TestCase):
    class Cl:
        def __init__(self):
            self.sent, self.dry_run = [], False

        def send(self, t):
            self.sent.append(t)
            return True

    SNAP = SimpleNamespace(summary={"total": 1, "last24h": 0, "last7d": 0, "streak": 0, "last_write": None},
                           funnel={"candidate": 0, "context": 0, "journey": 0, "watch": 0},
                           heartbeat={"status": "ok"}, filed_records=[])
    BOOM = SimpleNamespace(load_embedding_records=lambda *_: [])

    def setUp(self):
        monitor._TURN.clear()
        monitor._TURN["bubbles"] = None

    def _run(self, on=True):
        s = _state(watch=W())
        s.owner_folder_id = "F"
        cl = self.Cl()
        cfg = SimpleNamespace(dry_run=False, telegram_chat_id="", timezone="Asia/Taipei",
                              notify_cooldown_min=30, mood_watch_enabled=on)
        monitor.handle_message({"message": {"chat": {"id": 1}, "text": "/moodwatch", "date": NOW.timestamp()}},
                               None, self.BOOM, {"meta": {}, "records": []}, self.SNAP, s, cl, cfg, TZ)
        return "".join(cl.sent)

    def test_command_reports_status(self):
        out = self._run()
        self.assertIn("座標回報訂閱", out)
        self.assertIn("門檻", out)

    def test_flag_off_generic(self):
        self.assertNotIn("座標回報訂閱", self._run(on=False))   # 旗標關＝落泛 '/' 固定招呼＝現狀


if __name__ == "__main__":
    unittest.main()
