"""📦 §1.88 欠著的內容主動補交付（PROMISE_OWED_PUSH_MAX）——§1.85 的收尾件。

為什麼需要：§1.85 記下了 `status='owed'`（準時出聲、但答應的內容沒交出來），可續開交付的管道
**只有回覆橋**＝要等使用者下次開口。他不開口，欠著的內容就無聲躺在帳本裡——而使用者的原始抱怨
正是「我什麼 bot 都依約時間出現，但就是不會完成所約定的事情」，讓它躺著等於這個病沒治完。

本檔釘住：真的會自己送出去（不等他開口）、節流全部有效、交不出來會誠實結案成 owed_unmet
（帳本與 §1.13B 一律讀成「沒有做到」）、結案句**不把球踢回去**（§1.87 的價值）、旗標 0＝完全不執行。
全 stub、零網路。
"""

import io
import os
import re
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from telegram_monitor import monitor, selfstate
from telegram_monitor.state import State

TZ = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 7, 26, 13, 19, 0, tzinfo=timezone.utc)      # 台北 21:19（截圖那筆的約定時刻）
LATER = datetime(2026, 7, 26, 14, 30, 0, tzinfo=timezone.utc)    # 台北 22:30（欠了 71 分鐘）
ASK = "30分鐘後再給我猜一次告訴我答案"
ANSWER = "我猜你是天秤座，因為你一直在意事情有沒有被擺平。"
INC = "嗨，我來了。\n說好 21:19 要再猜一次的。\n我真的有好好想了一下，這次我猜…"


class Cl:
    dry_run = False

    def __init__(self, ok=True):
        self.sent, self.ok = [], ok

    def send(self, t):
        self.sent.append(t)
        return self.ok

    def send_sticker(self, f):
        return True


def _coach(keep_msg=INC, content=ANSWER):
    c = SimpleNamespace(enabled=True, api_key="k", model="m",
                        meter=SimpleNamespace(record=lambda *a, **k: None), asked=[], _turn_length=None)
    c.voice_promise_keep = lambda *a, **k: keep_msg
    c.reply = lambda q, *a, **k: (c.asked.append(q), content)[1]
    return c


def _cfg(push_max=1, proof=True, **kw):
    d = dict(promise_delivery_proof_enabled=proof, promise_emit_enabled=True,
             promise_owed_push_max=push_max, promise_deliver_content_enabled=True,
             promise_teaser_hollow_enabled=True, sched_recur_daily_enabled=True,
             promise_sched_ttl_sec=21600, timezone="Asia/Taipei", promise_act_aligned=True,
             promise_sticker_enabled=True, always_sticker_enabled=False)
    d.update(kw)
    return SimpleNamespace(**d)


def _owed_state(owed_ts=None, tries=1, push_n=0, status="owed"):
    s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
    s.owner_folder_id = "F"
    s.last_user_msg_ts = 0                                        # 早就沒在打字
    s.last_push_ts = 0
    ots = NOW.timestamp() if owed_ts is None else owed_ts
    s.scheduled_promises = [{
        "target_ts": NOW.timestamp(), "behavior": "再猜一次他的星座",
        "made_text": ASK, "deliver_ask": ASK,
        "fulfilled": True, "status": status, "delivery_owed": True,
        "owed_ts": ots, "owed_sent_ts": ots, "owed_tries": tries, "owed_push_n": push_n,
    }]
    return s


class PushDeliversTest(unittest.TestCase):
    """核心：不等他開口，自己把欠的內容送出去。"""

    def test_pushes_content_without_user_speaking(self):
        s, cl, co = _owed_state(), Cl(), _coach()
        monitor._promise_owed_push(cl, s, _cfg(), co, LATER)
        out = "".join(cl.sent)
        self.assertIn("天秤座", out)                               # 真的把答案送出去了
        p = s.scheduled_promises[0]
        self.assertEqual(p["owed_push_n"], 1)
        self.assertEqual(p["status"], "fulfilled")                # 這次交付成功 → 結清

    def test_retry_drops_the_checkin_ceremony(self):
        # owed 重試輪不該把幾十分鐘前那套報到寒暄再演一次（他早就收到過了）——直接進內容
        s, cl, co = _owed_state(), Cl(), _coach()
        monitor._promise_owed_push(cl, s, _cfg(), co, LATER)
        out = "".join(cl.sent)
        self.assertIn("天秤座", out)
        self.assertIn("21:19", out)                                # 時刻由程式算（when），不是 LLM 產的
        self.assertNotIn("我來了", out)
        self.assertNotIn("這次我猜…", out)

    def test_first_time_keep_still_appends(self):
        # 非 owed（第一次兌現）維持 §1.85 原行為：報到句保留、內容接在後面（只增不減）
        s = _owed_state()
        p = s.scheduled_promises[0]
        p.update({"status": "pending", "fulfilled": False})
        p.pop("delivery_owed", None)
        msg = monitor._promise_keep_body(s, _cfg(), _coach(), p, LATER, TZ, overdue=False)
        self.assertIn("這次我猜…", msg)                            # 原文保留
        self.assertIn("天秤座", msg)

    def test_not_overdue_wording(self):
        # 它**準時出現過**、只是沒交付 ⇒ 不能說「抱歉我遲了」（語意錯置）
        s, cl, co = _owed_state(), Cl(), _coach()
        monitor._promise_owed_push(cl, s, _cfg(), co, LATER)
        self.assertNotIn("抱歉我遲了", "".join(cl.sent))

    def test_one_promise_per_tick(self):
        s, cl, co = _owed_state(), Cl(), _coach()
        p2 = dict(s.scheduled_promises[0])
        p2["behavior"] = "另一件事"
        s.scheduled_promises.append(p2)
        monitor._promise_owed_push(cl, s, _cfg(), co, LATER)
        touched = [q for q in s.scheduled_promises if q.get("owed_push_n")]
        self.assertEqual(len(touched), 1)

    def test_hollow_regen_records_owed_again(self):
        # 補交付又交不出來 → 附誠實欠帳句、仍記 owed（tries 累加），不假裝完成
        s, cl = _owed_state(), Cl()
        co = _coach(content="我再想一下，等一下就告訴你。")
        monitor._promise_owed_push(cl, s, _cfg(), co, LATER)
        p = s.scheduled_promises[0]
        self.assertIn("這不算兌現", "".join(cl.sent))
        self.assertEqual(p["status"], "owed")
        self.assertNotIn("fulfilled_ts", p)
        self.assertEqual(p["owed_tries"], 2)

    def test_send_failure_leaves_owed(self):
        s, co = _owed_state(), _coach()
        monitor._promise_owed_push(Cl(ok=False), s, _cfg(), co, LATER)
        self.assertEqual(s.scheduled_promises[0]["status"], "owed")   # 沒送出去＝還是欠著


class ThrottleTest(unittest.TestCase):
    """節流：五道閘各自有效。"""

    def test_user_typing_defers(self):
        s, cl = _owed_state(), Cl()
        s.last_user_msg_ts = LATER.timestamp() - 10               # 剛剛還在打字
        monitor._promise_owed_push(cl, s, _cfg(), _coach(), LATER)
        self.assertEqual(cl.sent, [])

    def test_recent_push_defers(self):
        s, cl = _owed_state(), Cl()
        s.last_push_ts = LATER.timestamp() - 5
        monitor._promise_owed_push(cl, s, _cfg(), _coach(), LATER)
        self.assertEqual(cl.sent, [])

    def test_min_gap_since_owed(self):
        s, cl = _owed_state(), Cl()
        monitor._promise_owed_push(cl, s, _cfg(), _coach(), NOW)   # 才剛欠上（0 秒）
        self.assertEqual(cl.sent, [])

    def test_push_max_exhausted(self):
        s, cl = _owed_state(push_n=1), Cl()
        monitor._promise_owed_push(cl, s, _cfg(push_max=1), _coach(), LATER)
        self.assertNotIn("天秤座", "".join(cl.sent))               # 不再補交付（可能走結案）

    def test_shared_tries_cap_with_bridge(self):
        # 回覆橋已用掉總次數上限 ⇒ 主動補交付不再送內容（不重複交付）
        s, cl = _owed_state(tries=monitor._OWED_MAX_TRIES), Cl()
        monitor._promise_owed_push(cl, s, _cfg(), _coach(), LATER)
        self.assertNotIn("天秤座", "".join(cl.sent))

    def test_flag_zero_does_nothing(self):
        s, cl = _owed_state(), Cl()
        monitor._promise_owed_push(cl, s, _cfg(push_max=0), _coach(), LATER)
        self.assertEqual(cl.sent, [])
        self.assertEqual(s.scheduled_promises[0]["status"], "owed")   # 連結案也不做

    def test_proof_flag_off_does_nothing(self):
        s, cl = _owed_state(), Cl()
        monitor._promise_owed_push(cl, s, _cfg(proof=False), _coach(), LATER)
        self.assertEqual(cl.sent, [])

    def test_non_owed_untouched(self):
        for st in ("fulfilled", "owed_unmet", "pending", "expired"):
            s, cl = _owed_state(status=st), Cl()
            monitor._promise_owed_push(cl, s, _cfg(), _coach(), LATER)
            self.assertEqual(cl.sent, [], st)


class HonestCloseTest(unittest.TestCase):
    """交不出來＝誠實結案；帳本與守門一律讀成「沒有做到」；結案句不把球踢回去。"""

    def _close(self):
        s, cl = _owed_state(tries=monitor._OWED_MAX_TRIES), Cl()
        monitor._promise_owed_push(cl, s, _cfg(), _coach(), LATER)
        return s.scheduled_promises[0], "".join(cl.sent)

    def test_marks_owed_unmet_without_fulfilled_ts(self):
        p, _ = self._close()
        self.assertEqual(p["status"], "owed_unmet")
        self.assertTrue(p["delivery_owed"])
        self.assertNotIn("fulfilled_ts", p)                       # 永遠長不出「已經做了（在 HH:MM）」
        self.assertTrue(p["owed_final_ts"])

    def test_close_message_owns_it(self):
        _, out = self._close()
        self.assertIn("這不算做到", out)
        self.assertIn("21:19", out)                               # 時刻由程式算（temporal/程式時鐘鐵律）

    def test_close_message_does_not_push_ball_back(self):
        # §1.87 的價值：不說「要我重試就跟我說一聲」那種把責任推回使用者的話
        _, out = self._close()
        self.assertFalse(monitor._BALL_BACK_RE.search(out))
        self.assertNotIn("跟我說一聲", out)

    def test_closed_once_only(self):
        s, cl = _owed_state(tries=monitor._OWED_MAX_TRIES), Cl()
        monitor._promise_owed_push(cl, s, _cfg(), _coach(), LATER)
        n = len(cl.sent)
        s.last_push_ts = 0
        monitor._promise_owed_push(cl, s, _cfg(), _coach(), LATER)
        self.assertEqual(len(cl.sent), n)                          # status 變了＝不會再結案一次

    def test_ledger_reads_owed_unmet_as_not_done(self):
        p, _ = self._close()
        st = SimpleNamespace(scheduled_promises=[p], feeling_promise=None)
        facts = selfstate.promise_ledger_facts(st, LATER, TZ)
        self.assertNotIn("——已經做了", facts)
        self.assertIn("沒有做到", facts)
        self.assertNotIn("這就補上", facts)                        # 引擎不會再補了＝不准這樣說
        self.assertTrue(selfstate.ledger_has_owed(st))

    def test_claim_guard_still_blocks_i_did_it(self):
        p, _ = self._close()
        st = SimpleNamespace(scheduled_promises=[p])
        g = monitor._keep_claim_ground(st, LATER.timestamp(), TZ)
        self.assertIsNotNone(g)
        self.assertEqual(g[0], "owed")

    def test_settle_owed_pure(self):
        p = {"status": "owed", "fulfilled": True, "fulfilled_ts": 1.0}
        monitor._promise_settle_owed(p, 99.0)
        self.assertEqual(p["status"], "owed_unmet")
        self.assertNotIn("fulfilled_ts", p)
        self.assertEqual(p["owed_final_ts"], 99.0)


class WiringTest(unittest.TestCase):
    """佈線：掛在**網路無關**的韌性 tick（Drive 斷線也照補），獨立 try＝不影響存活。"""

    def test_hooked_into_resilient_tick(self):
        src = io.open("telegram_monitor/monitor.py", encoding="utf-8").read()
        self.assertIn("_promise_owed_push(client, state, cfg, coach, now)", src)
        i = src.index("_promise_owed_push(client, state, cfg, coach, now)")
        j = src.index("data, snap = _collect(")
        self.assertLess(i, j)                                      # 在 _collect 之前＝不依賴 Drive
        self.assertIn("§1.88 補交付 tick 失敗", src)                # 有自己的 try/except

    def test_no_crash_on_empty_state(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        monitor._promise_owed_push(Cl(), s, _cfg(), _coach(), LATER)   # 空帳本不炸


class ConfigTest(unittest.TestCase):
    def test_flag_synced(self):
        src = io.open("telegram_monitor/config.py", encoding="utf-8").read()
        self.assertIn("PROMISE_OWED_PUSH_MAX", src)
        self.assertIn("promise_owed_push_max", src)
        env = io.open(".env.example", encoding="utf-8").read()
        self.assertRegex(env, re.compile(r"^PROMISE_OWED_PUSH_MAX=1", re.M))
        self.assertIn("PROMISE_OWED_PUSH_MAX", io.open("README.md", encoding="utf-8").read())

    def test_getattr_default_zero_means_off(self):
        self.assertEqual(int(getattr(SimpleNamespace(), "promise_owed_push_max", 0) or 0), 0)


if __name__ == "__main__":
    unittest.main()
