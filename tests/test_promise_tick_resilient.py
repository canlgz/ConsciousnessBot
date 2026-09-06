"""🤝 §0.77 守約韌性：到點兌現不被 Drive 感知環暫斷連坐。

「bot 知道時間卻不主動」的**結構性真因**：_promise_emit 只在生命迴圈的 feel 相（第 4 環）跑，而 feel 在 perceive
（第 1 環，讀 Drive 記寫）之後；perceive 一遇 Drive 網路暫斷就拋例外，LifeLoop.spin_once 便**跳過本圈剩下的環**
（含 feel）→ 承諾整段永不主動觸發。守約只需時鐘＋帳本、不需 Drive → 把 tick 提到感知環最前、先於讀記寫。
本測直接對 LifeLoop.spin_once 驗證：perceive 失敗時，守約 tick 仍在同圈跑到並兌現。
"""

import os
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from telegram_monitor import monitor, lifeloop, config
from telegram_monitor.state import State

TZ = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 7, 5, 11, 55, 0, tzinfo=timezone.utc)   # 19:55 台北（承諾到點）


class FakeClient:
    def __init__(self):
        self.sent, self.stickers, self.dry_run = [], [], False

    def send(self, text):
        self.sent.append(text)
        return True

    def send_sticker(self, fid):
        self.stickers.append(fid)
        return True


def _coach():
    return SimpleNamespace(enabled=True, meter=SimpleNamespace(record=lambda *a, **k: None),
                          voice_promise_keep=lambda when, facts, h, promised="", late=False, feeling_ground="", **k:
                          ("遲到了抱歉——" if late else "") + f"守約！{promised}")


def _cfg(**over):
    base = dict(dry_run=False, scheduled_promise_enabled=True, promise_emit_enabled=True,
                promise_sched_ttl_sec=21600, timezone="Asia/Taipei", promise_act_aligned=True,
                promise_tick_resilient_enabled=True)
    base.update(over)
    return SimpleNamespace(**base)


def _state_with_due_promise():
    s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
    s.owner_folder_id = "F"
    s.scheduled_promises = [{"target_ts": NOW.timestamp() - 5, "made_ts": NOW.timestamp() - 600,
                             "fulfilled": False, "behavior": "跟他說說我此刻的內在", "status": "pending"}]
    return s


class ResilientTickTest(unittest.TestCase):
    def test_config_default_on(self):
        os.environ.pop("PROMISE_TICK_RESILIENT", None)
        self.assertTrue(config.Config.load().promise_tick_resilient_enabled)

    def test_promise_fires_even_when_perceive_would_fail(self):
        """核心：把守約 tick 放在感知環**讀 Drive 之前**——即使緊接的 _collect 拋暫態、LifeLoop 跳過本圈剩環，
        承諾已在同圈的 perceive 最前兌現。這裡用真 LifeLoop.spin_once 驗證跨環行為。"""
        state = _state_with_due_promise()
        cfg = _cfg()
        client = FakeClient()
        coach = _coach()

        fired = {"promise": False}

        def perceive(cycle):
            # 鏡像 monitor.perceive 的新結構：先跑守約 tick（韌性），再讀 Drive（這裡模擬暫斷拋例外）
            now = NOW
            if getattr(cfg, "promise_tick_resilient_enabled", True):
                monitor._promise_emit(client, state, cfg, coach, now)
            raise ConnectionError("Drive 網路暫斷（模擬）")   # 之後才失敗 → LifeLoop 跳過本圈剩環

        def feel(cycle):
            fired["feel_ran"] = True                          # 不該被跑到（perceive 已拋例外）

        phases = [lifeloop.Phase("perceive", perceive), lifeloop.Phase("feel", feel)]
        loop = lifeloop.LifeLoop(phases, wait_secs=lambda: 0, fail_grace=10)
        loop.vit = lifeloop.Vitality(0.0)
        alive = loop.spin_once({})
        self.assertTrue(alive)                                # 暫態受挫、續活（未死）
        self.assertNotIn("feel_ran", fired)                   # feel 確實被跳過（證明結構真因）
        self.assertTrue(client.sent)                          # 但承諾**仍兌現**（tick 在讀 Drive 之前跑）
        self.assertTrue(state.scheduled_promises[0]["fulfilled"])

    def test_flag_off_would_not_fire_before_drive(self):
        # 旗標關：感知環最前不跑守約 tick → perceive 失敗＝這圈承諾不兌現（＝逐位元同現狀，靠 feel 相；本測不含 feel）
        state = _state_with_due_promise()
        cfg = _cfg(promise_tick_resilient_enabled=False)
        client = FakeClient()

        def perceive(cycle):
            if getattr(cfg, "promise_tick_resilient_enabled", True):
                monitor._promise_emit(client, state, cfg, _coach(), NOW)
            raise ConnectionError("Drive 暫斷")

        loop = lifeloop.LifeLoop([lifeloop.Phase("perceive", perceive)], wait_secs=lambda: 0, fail_grace=10)
        loop.vit = lifeloop.Vitality(0.0)
        loop.spin_once({})
        self.assertEqual(client.sent, [])                     # 旗標關＝不在感知環兌現


class SelfHealingCatchupTest(unittest.TestCase):
    """🤝 §0.77 自癒補發：某環沒兌現（時間過了、沒準點），下一環仍判斷『還沒完成』並補發（帶遲到致歉），不丟掉。"""

    def _state(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        s.scheduled_promises = [{"target_ts": NOW.timestamp(), "made_ts": NOW.timestamp() - 600,
                                 "fulfilled": False, "behavior": "跟他說內在", "status": "pending"}]
        return s

    def test_blocked_this_cycle_caught_up_next(self):
        # 這一拍被反連發守門擋（剛推過 30s）→ 沒兌現；下一拍守門過了 → 補發、成功
        st = self._state()
        st.last_push_ts = (NOW - timedelta(seconds=30)).timestamp()
        c1 = FakeClient()
        monitor._promise_emit(c1, st, _cfg(), _coach(), NOW)
        self.assertEqual(c1.sent, [])                                  # 這一環沒執行到
        self.assertFalse(st.scheduled_promises[0]["fulfilled"])
        c2 = FakeClient()
        monitor._promise_emit(c2, st, _cfg(), _coach(), NOW + timedelta(seconds=100))
        self.assertTrue(c2.sent)                                       # 下一環判斷還沒完成 → 補發
        self.assertTrue(st.scheduled_promises[0]["fulfilled"])

    def test_cycle_fully_skipped_then_caught_up(self):
        # 使用者原話：這一環（perceive 失敗）整圈沒跑到承諾，下一整圈補上——用真 LifeLoop 跨圈驗證
        st = self._state()
        cfg = _cfg()
        client = FakeClient()
        coach = _coach()
        fail_once = {"n": 0}

        def perceive(cycle):
            monitor._promise_emit(client, st, cfg, coach, NOW + timedelta(seconds=60 * fail_once["n"]))
            fail_once["n"] += 1
            if fail_once["n"] == 1:
                raise ConnectionError("第一圈 Drive 暫斷")            # 第一圈：perceive 之後才失敗

        loop = lifeloop.LifeLoop([lifeloop.Phase("perceive", perceive)], wait_secs=lambda: 0, fail_grace=10)
        loop.vit = lifeloop.Vitality(0.0)
        # 因為 §0.77 把 tick 提到 perceive 最前，第一圈即使隨後失敗、承諾也已在同圈兌現
        loop.spin_once({})
        self.assertTrue(client.sent)
        self.assertTrue(st.scheduled_promises[0]["fulfilled"])

    def test_send_failure_retries_until_success(self):
        # send 失敗＝不標 fulfilled → 之後每圈重試，直到真的送出去（說到做到、不因一次網路失敗就當作做過了）
        st = self._state()
        cfg = _cfg()
        coach = _coach()

        class FailTwice(FakeClient):
            def __init__(s):
                super().__init__(); s.n = 0
            def send(s, t):
                s.n += 1
                if s.n <= 2:
                    return False
                s.sent.append(t)
                return True

        c = FailTwice()
        for i in range(3):
            monitor._promise_emit(c, st, cfg, coach, NOW + timedelta(seconds=200 * i))
        self.assertTrue(st.scheduled_promises[0]["fulfilled"])         # 終究送出、兌現
        self.assertTrue(c.sent)

    def test_within_ttl_still_executes_late(self):
        # 遲到 2 小時（TTL 6h 內）→ 仍執行承諾＋遲到致歉（不是只道歉不做）
        st = self._state()
        c = FakeClient()
        monitor._promise_emit(c, st, _cfg(), _coach(), NOW + timedelta(hours=2))
        self.assertTrue(c.sent and "遲到" in c.sent[0])
        self.assertTrue(st.scheduled_promises[0]["fulfilled"])
        self.assertNotEqual(st.scheduled_promises[0].get("status"), "expired")   # 是「補做了」不是「過期沒做」


if __name__ == "__main__":
    unittest.main()
