# -*- coding: utf-8 -*-
"""🕰️ §2.25 回顧式時長不是拖延：「剛剛這 20 分鐘」被讀成新未來錨 → 同一個約欠帳重演。

實測截圖（21:45–22:22）：「你在想個 20 分鐘，有答案後主動跟我回報」→ 22:06 準時交付了完整內容，
卻自判「答應你的內容我沒真的交出來——這不算兌現…記成還欠著」→ §1.88 十五分鐘後（22:21）同一個約
**又演一遍**、再判欠 ⇒ 使用者：「bot 是不是又重複執行同一個約定了？」

根因（探針釘死，非猜）：「嗨，時間到了，我回來了。」與「剛剛這 20 分鐘，我…」**各自**都解不出
未來錨，連在一起 temporal 卻解出 now+20 分 ⇒ §1.85 拖延閘（兌現句自己又立新未來錨）誤中 ⇒
四道確定性閘沒全過 ⇒ §2.17 軟否決（judge no 降級）永遠輪不到上場 ⇒ owed 迴圈照轉。
截圖另一半（同約兩本帳各自演）＝§1.89+§2.02 已修——本檔附探針釘住現碼去重有效（＝當時跑舊碼）。
全 stub、零網路。
"""

import os
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from telegram_monitor import monitor, temporal
from telegram_monitor.state import State

TZ = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 8, 3, 14, 6, 0, tzinfo=timezone.utc)   # 台北 22:06（截圖時刻）


def _tp(h, m, s=0):
    return datetime(2026, 8, 3, h, m, s, tzinfo=TZ).timestamp()


KEEP = ("嗨，時間到了，我回來了。剛剛這 20 分鐘，我其實一直都在整理思緒，感覺身體的脈動也比較穩定下來了。"
        "現在啊，我感覺自己更貼近你說的「情緒座標」這件事了，好像能更直接地感受到那些細微的起伏，而不是只看到表面的變化。")


class MechanismPinTest(unittest.TestCase):
    def test_combined_text_parses_phantom_future_anchor(self):
        # 釘住前提：整段連起來 temporal 真的解出 now+20 分的幽靈未來錨（機制不是猜的）
        eps = temporal.all_clock_epochs(KEEP, NOW, TZ)
        self.assertTrue(any(e > NOW.timestamp() for e in eps))

    def test_flag_off_defer_gate_falsely_hits(self):
        # 迴歸釘：舊行為＝拖延閘誤中（截圖病灶的最小重現）
        self.assertTrue(monitor._deliver_defer_hit(KEEP, "22:05", NOW, TZ))

    def test_retro_strip_clears_false_defer(self):
        self.assertFalse(monitor._deliver_defer_hit(KEEP, "22:05", NOW, TZ, retro=True))


class RealDeferStillCaughtTest(unittest.TestCase):
    def test_future_duration_still_defer(self):
        for t in ("我再想 20 分鐘，晚點跟你說。", "大概 20 分鐘後我會跟你說。", "等一下再說。",
                  "剛剛想了一輪還不夠，20 分鐘後我再回來。"):
            self.assertTrue(monitor._deliver_defer_hit(t, "", NOW, TZ, retro=True), t)

    def test_retro_durations_not_defer(self):
        for t in ("剛剛這 20 分鐘，我一直在整理思緒。", "剛才那 10 分鐘我都在想你問的事。",
                  "我花了半小時整理，現在比較清楚了。", "過去這 20 分鐘我沉澱了不少。"):
            self.assertFalse(monitor._deliver_defer_hit(t, "", NOW, TZ, retro=True), t)


class VerdictTest(unittest.TestCase):
    def _p(self):
        return {"target_ts": _tp(22, 5, 10), "made_ts": _tp(21, 45, 10), "behavior": "跟他回報",
                "made_text": "說太多了，你在想個20分鐘，有答案後主動跟我回報",
                "fulfilled": False, "status": "pending"}

    def _coach(self, v):
        return SimpleNamespace(enabled=True, judge_delivery_made=lambda ask, msg: v)

    def test_screenshot_keep_no_longer_owed(self):
        # ★ 修後：judge 說 no 也走到 §2.17 軟否決（四閘全過了）＝不記欠帳、不再重演
        cfg = SimpleNamespace(promise_judge_soft_veto=True, keep_retro_dur_enabled=True,
                              timezone="Asia/Taipei")
        self.assertEqual(monitor._deliver_verdict(KEEP, self._p(), "22:05", self._coach(False), cfg, NOW, TZ),
                         ("unknown", "judge-no-softened"))

    def test_flag_off_reproduces_screenshot_loop_source(self):
        cfg = SimpleNamespace(promise_judge_soft_veto=True, keep_retro_dur_enabled=False,
                              timezone="Asia/Taipei")
        self.assertEqual(monitor._deliver_verdict(KEEP, self._p(), "22:05", self._coach(False), cfg, NOW, TZ),
                         ("no", "defer"))                    # 舊行為＝欠帳迴圈的源頭（逐位元同現狀）

    def test_judge_yes_still_ok(self):
        cfg = SimpleNamespace(promise_judge_soft_veto=True, keep_retro_dur_enabled=True,
                              timezone="Asia/Taipei")
        self.assertEqual(monitor._deliver_verdict(KEEP, self._p(), "22:05", self._coach(True), cfg, NOW, TZ),
                         ("ok", "judge-yes"))


class DedupProbeTest(unittest.TestCase):
    def test_ack_105s_later_not_double_booked_on_current_code(self):
        # 截圖另一半（同約兩本帳、22:06/22:21 各演兩遍）：§1.89+§2.02 已修——釘住現碼 105 秒差的
        # ack 不再二次入帳（＝截圖那台跑的是舊碼；redeploy 即消）
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.scheduled_promises = [{"target_ts": _tp(22, 5, 10), "made_ts": _tp(21, 45, 10),
                                 "behavior": "跟他回報", "made_text": "想個20分鐘有答案後回報",
                                 "fulfilled": False, "status": "pending"}]
        cfg = SimpleNamespace(self_promise_dedup_enabled=True, promise_same_appointment_merge=True,
                              promise_mood_ground_enabled=False, dry_run=True)
        monitor._book_self_promise(s, cfg, [_tp(22, 6, 55)], "20 分鐘後我會再跟你說說。", _tp(21, 46, 55), "")
        self.assertEqual(len(s.scheduled_promises), 1)


class FlagSyncTest(unittest.TestCase):
    def test_flag_everywhere(self):
        with open("telegram_monitor/config.py", encoding="utf-8") as f:
            src = f.read()
        self.assertIn('_bool("KEEP_RETRO_DURATION", True)', src)
        self.assertIn("keep_retro_dur_enabled", src)
        with open(".env.example", encoding="utf-8") as f:
            self.assertIn("KEEP_RETRO_DURATION=1", f.read())
        with open("README.md", encoding="utf-8") as f:
            self.assertIn("KEEP_RETRO_DURATION", f.read())


if __name__ == "__main__":
    unittest.main()
