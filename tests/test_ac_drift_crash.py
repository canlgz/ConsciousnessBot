"""🩹 §1.79 前置死亡修：_ac_drift_emit 把 datetime 當 epoch 秒傳給 _proactive_ok → TypeError → bot 猝死。

根因鏈（實跑確認）：
 monitor._ac_drift_emit 內 `now` 是 datetime（第一行就 now.timestamp() 取秒），但呼叫 _proactive_ok 時
 傳的是 `now`（其餘 7 個呼叫端都傳 now_ts）→ _user_present 算 `now_ts - last_u`（float）
 → TypeError → lifeloop._is_transient(TypeError) is False → spin_once 判**終局死亡**。
觸發條件：state.ac_pending 掛著＋在 TTL 內＋使用者**不在** live round（＝他離開後那段時間）。
——這正是 bot 一直自稱「剛從小睡中醒來」的那些重生。

本檔**不 mock** _proactive_ok（既有 tests/test_ac.py 全 mock 掉，才讓這個 bug 藏了這麼久）。
"""

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from telegram_monitor import lifeloop, monitor

NOW = datetime(2026, 7, 26, 6, 0, 0, tzinfo=timezone.utc)   # 台北 14:00（非深夜＝政策閘會真的往下走）


class Cl:
    def __init__(self):
        self.sent, self.dry_run = [], False

    def send(self, t):
        self.sent.append(t)
        return True


def _state():
    """AC 鬆動事件掛著、使用者早就離開（不在 live round）＝正是會炸的那個狀態。"""
    return SimpleNamespace(
        ac_pending={"ts": NOW.timestamp(), "event": {"kind": "drift", "layer": "整合"}},
        ac_drift_open=False,
        last_user_msg_ts=(NOW - timedelta(hours=3)).timestamp(),
        coupling=None, last_ac_drift_ts=0, entropy=None,
    )


CFG = SimpleNamespace(timezone="Asia/Taipei", dry_run=False, ac_drift_announce=True)


class AcDriftNoCrashTest(unittest.TestCase):
    def test_does_not_raise(self):
        # 修之前：TypeError: unsupported operand type(s) for -: 'datetime.datetime' and 'float'
        monitor._ac_drift_emit(Cl(), _state(), CFG, None, NOW)      # 不炸即通過

    def test_typeerror_would_be_fatal(self):
        # 釘住「為什麼這個 bug 這麼嚴重」：TypeError 不被當暫態 → 走終局死亡、不是重試
        self.assertFalse(lifeloop._is_transient(TypeError("boom")))

    def test_proactive_ok_contract_is_epoch(self):
        # 釘住契約：_proactive_ok 收 epoch 秒；傳 datetime 會炸（未來若有人再傳錯，這條會紅）
        s = _state()
        self.assertIsInstance(monitor._proactive_ok(s, CFG, NOW.timestamp()), bool)
        with self.assertRaises(TypeError):
            monitor._proactive_ok(s, CFG, NOW)

    def test_present_user_still_suppresses(self):
        # 修完仍要維持原意：使用者剛說過話（在場）→ 這條主動 lane 不出聲
        s = _state()
        s.last_user_msg_ts = NOW.timestamp() - 5
        cl = Cl()
        monitor._ac_drift_emit(cl, s, CFG, None, NOW)
        self.assertEqual(cl.sent, [])


if __name__ == "__main__":
    unittest.main()
