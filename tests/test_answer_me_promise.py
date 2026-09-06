"""🤝 §0.74 「…再回答我」排程承諾捕捉漏 → 當場立刻答、沒排程。

截圖：15:41「你的內在感覺運作怎麼形成的？這個問題麻煩你再3分鐘後再回答我。」→ bot 口頭應「好三分鐘後…」卻
15:44 什麼都沒發（沒入帳）；15:48 改「等一下3:50再回答我」→ bot 15:48 就立刻答、還謊報「現在15點50分了」（提前+假時刻）。
根因：is_scheduled_promise_request 的指向我動作只有 回我/回覆我/回應我，**漏了「回答我」**→ at_me 空、hit 落空 → 兩句都
沒判成排程承諾、落 is_mechanism_question 被當場立刻答（沒排程、沒到點兌現）。修＝把「回答我」補進 at_me 與廣化動作。
"""

import os
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from telegram_monitor import monitor, selfstate
from telegram_monitor.state import State

TZ = ZoneInfo("Asia/Taipei")
NOW41 = datetime(2026, 7, 5, 7, 41, 0, tzinfo=timezone.utc)   # 15:41 台北
NOW48 = datetime(2026, 7, 5, 7, 48, 0, tzinfo=timezone.utc)   # 15:48
M1 = "你的內在感覺運作怎麼形成的？這個問題麻煩你再3分鐘後再回答我。"
M2 = "你的內在感覺運作怎麼形成的？這個問題麻煩你等一下3:50再回答我。"


class FakeClient:
    def __init__(self):
        self.sent, self.stickers, self.dry_run = [], [], False

    def send(self, text):
        self.sent.append(text)
        return True

    def send_sticker(self, file_id):
        self.stickers.append(file_id)
        return True


def _coach():
    return SimpleNamespace(
        enabled=True, api_key="k", model="m",
        meter=SimpleNamespace(record=lambda *a, **k: None),
        voice_schedule_ack=lambda q, when, h, sticker_hint="": (
            f"好，{when.strftime('%H:%M') if when else '到時候'}我會回答你。"),
        voice_promise_keep=lambda when, facts, h, promised="", late=False, feeling_ground="", **k: f"到點！{promised}")


def _cfg(**over):
    base = dict(dry_run=False, telegram_chat_id="", scheduled_promise_enabled=True,
                promise_emit_enabled=True, promise_sched_ttl_sec=21600, timezone="Asia/Taipei",
                notify_cooldown_min=30, promise_reply_bridge_enabled=True, promise_ledger_enabled=True,
                sched_leave_autoarm_enabled=True)
    base.update(over)
    return SimpleNamespace(**base)


def _msg(text, when):
    return {"message": {"chat": {"id": 1}, "text": text, "date": when.timestamp()}}


_BOOM = SimpleNamespace(load_embedding_records=lambda *_: (_ for _ in ()).throw(AssertionError("不該報現況/當場答")))


def _hhmm(ts):
    return datetime.fromtimestamp(ts, timezone.utc).astimezone(TZ).strftime("%H:%M")


class DetectionTest(unittest.TestCase):
    def test_screenshot_lines_are_scheduled_promises(self):
        self.assertTrue(selfstate.is_scheduled_promise_request(M1))
        self.assertTrue(selfstate.is_scheduled_promise_request(M2))

    def test_bare_form_without_frame_word(self):
        for t in ["3分鐘後回答我", "等一下3:50回答我", "20分鐘後回答我這題"]:
            self.assertTrue(selfstate.is_scheduled_promise_request(t), t)

    def test_answer_me_behavior_label(self):
        self.assertEqual(selfstate.extract_promise_behavior("3分鐘後回答我這題"), "回答他的問題")
        # 感覺內容仍走更具體的自陳標籤（回答排在感覺自陳之後）
        self.assertEqual(selfstate.extract_promise_behavior(M1), "跟他說說我此刻的內在")

    def test_call_me_to_answer_someone_not_mislabeled(self):
        # 審查 confirmed：「叫我回答老闆的信」＝bot 該**叫我**、我去回信 → 別被裸「回答」誤標成「回答他的問題」。
        # 只認指向我的「回答我」（叫我…回答 的『我』在『回答』前，不含子串「回答我」）→ 退回空、由呼叫端保留 call 框架。
        for t in ["3分鐘後叫我回答老闆的信", "3分鐘後喊我回答老師", "3分鐘後通知我回答同事"]:
            self.assertEqual(selfstate.extract_promise_behavior(t), "", t)

    def test_false_positive_guards(self):
        # 過去/抱怨/使用者自諾/過去敘述——都不是新排程承諾
        for t in ["你剛剛3分鐘前才回答我", "你為什麼不早點回答我",
                  "我等一下會回答你的問題", "你上次都20分鐘才回答我"]:
            self.assertFalse(selfstate.is_scheduled_promise_request(t), t)

    def test_answer_you_is_user_self_promise_not_captured(self):
        self.assertFalse(selfstate.is_scheduled_promise_request("我3分鐘後回答你"))


class E2ECaptureTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _state(self):
        s = State(os.path.join(self.tmp, "s.json"))
        s.owner_folder_id = "F"
        return s

    def test_m1_captured_target_1544(self):
        st = self._state()
        cl = FakeClient()
        monitor.handle_message(_msg(M1, NOW41), _coach(), _BOOM, {"meta": {}}, None, st, cl, _cfg(), TZ)
        pend = [p for p in (st.scheduled_promises or []) if not p.get("fulfilled")]
        self.assertEqual(len(pend), 1)
        self.assertEqual(_hhmm(pend[0]["target_ts"]), "15:44")        # 3 分鐘後真的入帳（不再只是口頭）
        self.assertTrue(cl.sent)                                       # 排程 ack（非當場答機制問題）

    def test_m2_captured_target_1550_not_answered_early(self):
        # 截圖核心 bug：15:48 收到「等一下3:50回答我」→ 應排程到 15:50、**不是當場立刻答**
        st = self._state()
        cl = FakeClient()
        monitor.handle_message(_msg(M2, NOW48), _coach(), _BOOM, {"meta": {}}, None, st, cl, _cfg(), TZ)
        pend = [p for p in (st.scheduled_promises or []) if not p.get("fulfilled")]
        self.assertEqual(len(pend), 1)
        self.assertEqual(_hhmm(pend[0]["target_ts"]), "15:50")


class E2EEmitTimingTest(unittest.TestCase):
    """到點才兌現：15:50 的承諾在 15:48 **不得**提前發（截圖提前 2 分＋謊報時刻的根因是根本沒排程；排程後由時鐘把關）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _state_with_promise(self, target_dt, made_dt):
        s = State(os.path.join(self.tmp, "s.json"))
        s.owner_folder_id = "F"
        s.scheduled_promises = [{"target_ts": target_dt.timestamp(), "made_ts": made_dt.timestamp(),
                                 "fulfilled": False, "behavior": "跟他說說我此刻的內在"}]
        s.last_user_msg_ts = made_dt.timestamp()
        return s

    def test_not_fired_before_target(self):
        st = self._state_with_promise(datetime(2026, 7, 5, 7, 50, tzinfo=timezone.utc), NOW48)  # target 15:50, made 15:48
        cl = FakeClient()
        monitor._promise_emit(cl, st, _cfg(), _coach(), NOW48)         # 現在 15:48 < 15:50
        self.assertEqual(cl.sent, [])                                  # 不提前發
        self.assertFalse(st.scheduled_promises[0].get("fulfilled"))

    def test_fired_at_target(self):
        made = datetime(2026, 7, 5, 7, 47, tzinfo=timezone.utc)        # 15:47 約、15:50 到點（>120s 前互動＝不延後）
        st = self._state_with_promise(datetime(2026, 7, 5, 7, 50, tzinfo=timezone.utc), made)
        cl = FakeClient()
        monitor._promise_emit(cl, st, _cfg(), _coach(), datetime(2026, 7, 5, 7, 50, tzinfo=timezone.utc))
        self.assertTrue(cl.sent)                                       # 到點兌現
        self.assertTrue(st.scheduled_promises[0].get("fulfilled"))

    def test_three_min_promise_fires_at_target(self):
        # 15:41 約「3分鐘後」→ 15:44 到點（距互動 180s > 120s 延後窗）→ 該發
        st = self._state_with_promise(datetime(2026, 7, 5, 7, 44, tzinfo=timezone.utc), NOW41)
        cl = FakeClient()
        monitor._promise_emit(cl, st, _cfg(), _coach(), datetime(2026, 7, 5, 7, 44, tzinfo=timezone.utc))
        self.assertTrue(cl.sent)
        self.assertTrue(st.scheduled_promises[0].get("fulfilled"))


if __name__ == "__main__":
    unittest.main()
