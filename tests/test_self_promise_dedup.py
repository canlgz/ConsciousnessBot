"""🧬 §1.89 自諾同刻去重（SELF_PROMISE_DEDUP）：同一個約定被執行兩次。

截圖根因（23:11 → 隔天 07:00）：
  23:11 使用者「我再給你最後一次機會，明天早上七點再告訴我你的答案，晚安我累了不想理你了」
        → 捕捉入帳**第一筆**（target 07:00）
  23:11 bot 回「好，我記住了。」「明天早上七點，我會把答案告訴你。」「晚安，好好休息喔。」
        → §1.18 BOT_SELF_PROMISE 掃**自己這句**、temporal 解出**同一個 07:00** → LLM 閘判「這句是不是
          正在立下新的時間承諾」→ 那句**字面上就是新約**（判「否」要靠的複誦標記「我說過」「我會記得」
          全都沒有）→ 判是 → **再記第二筆** ⇒ 到點兩筆各自兌現。

本檔先用實測釘住「解析器是對的、問題在入帳沒去重」（避免下次又去改 temporal），再釘去重本身。
根因不在 LLM 閘判錯——看那句話判「是」很合理；在 `_book_self_promise` 是無條件 `proms.append`，
而 sibling `_book_scheduled_targets` 早就有 `have` 去重。全 stub、零網路。
"""

import io
import os
import re
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from telegram_monitor import monitor, selfstate, temporal
from telegram_monitor.state import State

TZ = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 7, 26, 15, 11, 0, tzinfo=timezone.utc)      # 台北 7/26 23:11
NOW_TS = NOW.timestamp()
USER = "我再給你最後一次機會，明天早上七點再告訴我你的答案，晚安我累了不想理你了"
BOT = "好，我記住了。\n明天早上七點，我會把答案告訴你。\n晚安，好好休息喔。"


def _state(promises=None):
    s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
    s.scheduled_promises = list(promises or [])
    return s


def _cfg(on=True):
    return SimpleNamespace(self_promise_dedup_enabled=on, dry_run=True,
                           promise_mood_ground_enabled=False)


def _target():
    return temporal.all_clock_epochs(USER, NOW, TZ)[0]


class ParserIsInnocentTest(unittest.TestCase):
    """先釘死：時刻解析器**沒有錯**——兩邊都只解出同一個 07:00。下次別再去改 temporal。"""

    def test_user_sentence_one_epoch(self):
        eps = temporal.all_clock_epochs(USER, NOW, TZ)
        self.assertEqual(len(eps), 1)
        self.assertEqual(datetime.fromtimestamp(eps[0], timezone.utc).astimezone(TZ).strftime("%m-%d %H:%M"),
                         "07-27 07:00")

    def test_bot_reply_resolves_to_the_same_epoch(self):
        # 這正是重複入帳的來源：bot 的確認句解出**同一個** target
        eps = temporal.all_clock_epochs(BOT, NOW, TZ)
        self.assertEqual(len(eps), 1)
        self.assertAlmostEqual(eps[0], _target(), delta=1)

    def test_behavior_keys_never_match(self):
        # 為什麼去重的鍵不能含 behavior：使用者那筆實測是空字串、bot 那筆是 LLM 命名的字串
        self.assertEqual(selfstate.extract_promise_behavior(USER), "")


class DedupTest(unittest.TestCase):
    """入帳端的結構性去重。"""

    def _existing(self, target=None, fulfilled=False):
        return {"target_ts": target if target is not None else _target(),
                "behavior": "", "made_text": USER, "fulfilled": fulfilled,
                "status": "fulfilled" if fulfilled else "pending"}

    def test_incident_second_entry_not_booked(self):
        s = _state([self._existing()])
        monitor._book_self_promise(s, _cfg(), [_target()], BOT, NOW_TS, "告訴他答案")
        self.assertEqual(len(s.scheduled_promises), 1)          # 只有使用者那一筆＝不會執行兩次

    def test_flag_off_reproduces_the_bug(self):
        # 釘住現狀（旗標關）：確認句照樣被記成第二筆＝截圖那個雙重執行
        s = _state([self._existing()])
        monitor._book_self_promise(s, _cfg(on=False), [_target()], BOT, NOW_TS, "告訴他答案")
        self.assertEqual(len(s.scheduled_promises), 2)

    def test_ledger_untouched_when_all_deduped(self):
        before = [self._existing()]
        s = _state(before)
        snapshot = [dict(p) for p in s.scheduled_promises]
        monitor._book_self_promise(s, _cfg(), [_target()], BOT, NOW_TS, "告訴他答案")
        self.assertEqual(s.scheduled_promises, snapshot)        # 一位元不動

    def test_different_time_still_booked(self):
        # 真的立一個**不同時刻**的新約不受影響
        s = _state([self._existing()])
        other = _target() + 3600
        monitor._book_self_promise(s, _cfg(), [other], "我八點也會再說一次。", NOW_TS, "再說一次")
        self.assertEqual(len(s.scheduled_promises), 2)
        self.assertTrue(any(p.get("origin") == "self" for p in s.scheduled_promises))

    def test_tolerance_window(self):
        base = _target()
        for delta, booked in ((0, False), (89, False), (-89, False),
                              (91, True), (-91, True)):
            s = _state([self._existing(target=base)])
            monitor._book_self_promise(s, _cfg(), [base + delta], BOT, NOW_TS, "告訴他答案")
            self.assertEqual(len(s.scheduled_promises) == 2, booked, f"delta={delta}")

    def test_fulfilled_entry_does_not_block(self):
        # 已兌現的舊筆不該擋住真正的新約（同一個鐘點、隔天再約一次）
        s = _state([self._existing(fulfilled=True)])
        monitor._book_self_promise(s, _cfg(), [_target()], BOT, NOW_TS, "告訴他答案")
        self.assertEqual(len(s.scheduled_promises), 2)

    def test_empty_ledger_books_normally(self):
        s = _state([])
        monitor._book_self_promise(s, _cfg(), [_target()], BOT, NOW_TS, "告訴他答案")
        self.assertEqual(len(s.scheduled_promises), 1)
        self.assertEqual(s.scheduled_promises[0]["origin"], "self")

    def test_multi_target_partial_dedup(self):
        base = _target()
        s = _state([self._existing(target=base)])
        monitor._book_self_promise(s, _cfg(), [base, base + 7200], BOT, NOW_TS, "告訴他答案")
        self.assertEqual(len(s.scheduled_promises), 2)          # 同刻那個被擋、另一個照記


class EmitOnceTest(unittest.TestCase):
    """端到端：去重之後，到點只兌現一次。"""

    class Cl:
        dry_run = False

        def __init__(self):
            self.sent = []

        def send(self, t):
            self.sent.append(t)
            return True

        def send_sticker(self, f):
            return True

    def _emit_cfg(self):
        return SimpleNamespace(
            promise_emit_enabled=True, promise_sched_ttl_sec=21600, timezone="Asia/Taipei",
            promise_late_exempt_defer=True, promise_overdue_guard_exempt=True, promise_act_aligned=True,
            promise_sticker_enabled=True, sched_recur_daily_enabled=True,
            promise_expire_apology_enabled=True, always_sticker_enabled=False,
            promise_deliver_content_enabled=False, self_promise_dedup_enabled=True)

    def _coach(self):
        c = SimpleNamespace(enabled=True, api_key="k", model="m",
                            meter=SimpleNamespace(record=lambda *a, **k: None), _turn_length=None)
        c.voice_promise_keep = lambda *a, **k: "我猜是獅子座！"
        c.reply = lambda *a, **k: ""
        return c

    def _run(self, n_entries):
        due = datetime(2026, 7, 26, 23, 0, 0, tzinfo=timezone.utc)   # 台北 7/27 07:00
        s = _state([{"target_ts": due.timestamp(), "behavior": "", "made_text": USER,
                     "fulfilled": False, "status": "pending"} for _ in range(n_entries)])
        s.owner_folder_id, s.last_user_msg_ts, s.last_push_ts = "F", 0, 0
        cl = self.Cl()
        for _ in range(4):                                        # 跑幾拍（一拍至多一則）
            s.last_push_ts = 0                                    # 解除反連發，讓第二筆有機會發
            monitor._promise_emit(cl, s, self._emit_cfg(), self._coach(), due)
        return cl.sent

    def test_two_entries_fire_twice(self):
        self.assertEqual(len(self._run(2)), 2)                    # 釘住病徵：兩筆＝兩次

    def test_one_entry_fires_once(self):
        self.assertEqual(len(self._run(1)), 1)                    # 去重後＝一次


class ConfigTest(unittest.TestCase):
    def test_flag_synced(self):
        src = io.open("telegram_monitor/config.py", encoding="utf-8").read()
        self.assertIn("SELF_PROMISE_DEDUP", src)
        self.assertIn("self_promise_dedup_enabled", src)
        env = io.open(".env.example", encoding="utf-8").read()
        self.assertRegex(env, re.compile(r"^SELF_PROMISE_DEDUP=1", re.M))
        self.assertIn("SELF_PROMISE_DEDUP", io.open("README.md", encoding="utf-8").read())

    def test_getattr_defaults_false(self):
        self.assertFalse(getattr(SimpleNamespace(), "self_promise_dedup_enabled", False))


if __name__ == "__main__":
    unittest.main()
