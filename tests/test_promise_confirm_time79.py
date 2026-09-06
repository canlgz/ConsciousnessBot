"""🤝 §0.79 「確認一下時間」落 fact_or_chat（軟時間接地）→ LLM 幻覺「現在是 00:00」。

截圖：承諾 23:51 立、約 00:06 自陳內在轉速。使用者 00:10 傳「確認一下時間」→ bot 00:11 回「現在是 00:00，我們約定好的
時間是 00:06」——把此刻（00:10/00:11）說早了 10 分，還把**已逾期**的 00:06 說成「還沒到、會準時」。兩分鐘後回「早安」
卻正確說「深夜十二點多」＝引擎時鐘沒壞。真因：「確認一下時間」不中任何時間/狀態辨識（promise_status_kind=''）→ 落
fact_or_chat＝只有 §0.36 的**軟**時間感接地（「別主動拿時間當話題」）→ LLM 面對直接時間問卻無硬錨、又見對話「約 00:06」
→ 幻覺回「現在是 00:00」。

修（旗標化、預設開、關＝逐位元同現狀）：
- Fix1（辨識→硬錨路由）：`_STATUS_CONFIRM_RE`＋promise_status_kind 回 'confirm'；monitor 把 confirm 一律路由 promise_ledger
  （硬錨〔此刻真的是 HH:MM——別把約定時刻說成現在〕＋每筆真時刻；空帳本也誠實接地）。
- Fix2（縱深防禦）：`_ledger_time_consistent`——帳本回覆若報了『不是此刻、也不是任何帳本約定時刻』的鐘點＝幻覺 →
  落回確定性 `promise_ledger_text`。
"""

import os
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from telegram_monitor import monitor, selfstate, config
from telegram_monitor.state import State

TZ = ZoneInfo("Asia/Taipei")
# 00:10 台北＝前一日 16:10 UTC（承諾 23:51 立、約 00:06）
NOW = datetime(2026, 7, 4, 16, 10, tzinfo=timezone.utc)
MADE = datetime(2026, 7, 4, 15, 51, tzinfo=timezone.utc)      # 23:51
TARGET = datetime(2026, 7, 4, 16, 6, tzinfo=timezone.utc)     # 00:06（相對 NOW 已逾期 4 分）


class FakeClient:
    def __init__(self):
        self.sent, self.stickers, self.dry_run = [], [], False

    def send(self, text):
        self.sent.append(text)
        return True

    def send_sticker(self, fid):
        self.stickers.append(fid)
        return True


def _coach(**over):
    base = dict(
        enabled=True, api_key="k", model="m",
        meter=SimpleNamespace(record=lambda *a, **k: None),
        reply=lambda text, facts="", *a, **k: (facts or "chat-reply"),   # 回音 facts＝可驗走了帳本硬錨
        ask=lambda *a, **k: ("chat", None, "chat-reply"),
    )
    base.update(over)
    return SimpleNamespace(**base)


def _cfg(**over):
    base = dict(dry_run=False, telegram_chat_id="", scheduled_promise_enabled=True,
                promise_emit_enabled=True, promise_sched_ttl_sec=21600, timezone="Asia/Taipei",
                notify_cooldown_min=30, promise_reply_bridge_enabled=True, promise_ledger_enabled=True,
                promise_status_ground_enabled=True, promise_status_empty_ground=True,
                promise_ledger_time_guard=True, promise_cancel_enabled=True)
    base.update(over)
    return SimpleNamespace(**base)


def _msg(text, when):
    return {"message": {"chat": {"id": 1}, "text": text, "date": when.timestamp()}}


class _Benign:
    def __getattr__(self, name):
        return lambda *a, **k: []


def _state_with_promise():
    s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
    s.owner_folder_id = "F"
    s.scheduled_promises = [{"target_ts": TARGET.timestamp(), "made_ts": MADE.timestamp(),
                             "fulfilled": False, "status": "pending",
                             "behavior": "跟他說說我此刻的內在轉速狀態"}]
    return s


class ConfirmRecognizerTest(unittest.TestCase):
    def test_confirm_positives(self):
        for t in ["確認一下時間", "確認時間", "對一下時間", "核對一下時間", "我們約幾點",
                  "約定的時間是幾點", "約好幾點", "時間對嗎", "時間對不對", "確認一下約定時間",
                  "確認我們約的時間"]:
            self.assertEqual(selfstate.promise_status_kind(t), "confirm", t)

    def test_confirm_negatives(self):
        # 非確認時間（第三方時刻/使用者自己行程/非時間確認/大約/裸現在幾點另有 clock 路由）
        for t in ["幾點的火車到", "我幾點要出門", "確認一下這個訂單", "確認訂單", "對一下答案",
                  "大約幾點到", "現在幾點", "幫我確認明天的會議"]:
            self.assertNotEqual(selfstate.promise_status_kind(t), "confirm", t)

    def test_confirm_negatives_tight(self):
        # 🛡️ 審查前置守門：「時間」後面還接東西＝不是確認時間本身（夠不夠/地點/安排/表/管理/對我很重要/對得上）
        for t in ["確認一下時間夠不夠", "確認一下時間地點", "確認一下時間安排", "你確認過時間表了嗎",
                  "時間對我很重要", "時間對得上嗎", "這時間對你方便嗎", "約會時間", "時間管理", "我約了醫生幾點"]:
            self.assertNotEqual(selfstate.promise_status_kind(t), "confirm", t)


class ConfirmRoutesToLedgerTest(unittest.TestCase):
    def test_confirm_time_grounds_not_hallucinate(self):
        # 端到端：截圖原句「確認一下時間」→ 走硬錨帳本（reply 收到含「此刻真的是」的 facts），不落軟接地
        st = _state_with_promise()
        c = FakeClient()
        monitor.handle_message(_msg("確認一下時間", NOW), _coach(), _Benign(),
                               {"meta": {}}, None, st, c, _cfg(), TZ)
        joined = "".join(c.sent)
        self.assertIn("此刻真的是", joined)              # 硬錨此刻（不是軟時間感）
        self.assertIn("00:10", joined)                   # 真實此刻，不是幻覺 00:00
        self.assertNotIn("00:00", joined)                # 截圖核心 bug：把此刻 00:10 說成 00:00
        self.assertNotIn("還沒到", joined)               # 也不把**逾期**的 00:06 說成「還沒到、會準時」（截圖的第二重謊）
        # （附帶：§0.66 回覆橋會先把逾期那筆遲到補發「🤝 抱歉我遲了…」，帳本再誠實顯示已做——比截圖的空口更對）

    def test_confirm_empty_ledger_does_not_hijack(self):
        # 🛡️ 審查 MED 修：空帳本的 confirm **不**硬錨帳本（免第三方「對一下時間，走吧」被劫持成「沒記著約好什麼」）——
        # 落回聊天路徑（這裡 mock build_memory_brief 讓它跑完），不吐帳本 sentinel。
        from unittest import mock
        st = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        st.owner_folder_id = "F"
        c = FakeClient()
        with mock.patch("telegram_monitor.coach.build_memory_brief", return_value=""):
            monitor.handle_message(_msg("對一下時間，走吧", NOW), _coach(), _Benign(),
                                   {"meta": {}}, None, st, c, _cfg(), TZ)
        self.assertNotIn("沒記著跟你約好", "".join(c.sent))

    def test_confirm_route_flag_off_falls_through(self):
        # 旗標關 PROMISE_CONFIRM_ROUTE=0：即使有活承諾，confirm 也不搶＝落回聊天＝逐位元同現狀
        from unittest import mock
        st = _state_with_promise()
        c = FakeClient()
        with mock.patch("telegram_monitor.coach.build_memory_brief", return_value=""):
            monitor.handle_message(_msg("確認一下時間", NOW), _coach(), _Benign(),
                                   {"meta": {}}, None, st, c, _cfg(promise_confirm_route_enabled=False), TZ)
        self.assertNotIn("此刻真的是", "".join(c.sent))


class LedgerTimeGuardTest(unittest.TestCase):
    def test_consistent_helper(self):
        st = _state_with_promise()
        self.assertFalse(monitor._ledger_time_consistent("現在是00:00，約定00:06", st, NOW, TZ))  # 00:00 幻覺
        self.assertTrue(monitor._ledger_time_consistent("現在是00:10，約定的是00:06", st, NOW, TZ))
        self.assertTrue(monitor._ledger_time_consistent("我這邊沒記著約好什麼", st, NOW, TZ))       # 無鐘點

    def test_role_aware_catches_promise_time_as_now(self):
        # 🛡️ 審查 HIGH 修：把**約定時刻 00:06 說成現在**＝最常見幻覺，寬允許集抓不到；角色感知（只查「現在…」修飾的鐘點）抓得到
        st = _state_with_promise()
        self.assertFalse(monitor._ledger_time_consistent("現在是00:06，剛好到約定時間，我準時", st, NOW, TZ))
        self.assertFalse(monitor._ledger_time_consistent("現在是23:51", st, NOW, TZ))            # made_ts 說成現在

    def test_role_aware_no_false_reject_non_now_times(self):
        # 🛡️ 審查 LOW 修：非「現在」修飾的鐘點（約定的是 X／你說的 X 那班車／還有 0:05）不誤殺
        st = _state_with_promise()
        self.assertTrue(monitor._ledger_time_consistent("約定的時間是00:06，還沒到", st, NOW, TZ))
        self.assertTrue(monitor._ledger_time_consistent("你說的7:30那班車還有0:05就到", st, NOW, TZ))

    def test_hallucinated_reply_falls_back_to_deterministic(self):
        # 端到端：LLM 在帳本回覆吐幻覺「現在是 00:00」→ 守門攔下、落回確定性帳本文字（不含 00:00）
        st = _state_with_promise()
        bad = _coach(reply=lambda text, facts="", *a, **k: "現在是00:00，我們約定好的時間是00:06，會準時說。")
        c = FakeClient()
        monitor.handle_message(_msg("確認一下時間", NOW), bad, _Benign(),
                               {"meta": {}}, None, st, c, _cfg(), TZ)
        joined = "".join(c.sent)
        self.assertNotIn("00:00", joined)                # 幻覺句被丟
        self.assertIn("00:06", joined)                   # 落回確定性帳本、帶正確約定時刻

    def test_guard_flag_off_keeps_hallucination(self):
        # 旗標關＝不守門＝逐位元同現狀（幻覺句原樣送出）。§0.82 全域鐘點守門是第二層、亦會攔——測 §0.79 隔離故一併關掉。
        st = _state_with_promise()
        bad = _coach(reply=lambda text, facts="", *a, **k: "現在是00:00，約定00:06。")
        c = FakeClient()
        monitor.handle_message(_msg("確認一下時間", NOW), bad, _Benign(),
                               {"meta": {}}, None, st, c,
                               _cfg(promise_ledger_time_guard=False, global_clock_guard=False), TZ)
        self.assertIn("00:00", "".join(c.sent))


class ConfigDefaultTest(unittest.TestCase):
    def test_flag_default_on(self):
        os.environ.pop("PROMISE_LEDGER_TIME_GUARD", None)
        self.assertTrue(config.Config.load().promise_ledger_time_guard)


if __name__ == "__main__":
    unittest.main()
