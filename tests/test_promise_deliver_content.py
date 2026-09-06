"""📦 §1.44 兌現要交付內容（PROMISE_DELIVER_CONTENT）：內容型承諾的兌現不能只「報到」——空心兌現偵測＋當場補內容。

截圖根因：08:50 使用者約「30 分鐘之後，你告訴我如何證明自己」→ 09:20 兌現卻只有「🤝 嗨，早上好。說好 09:20 要來
跟你聊聊怎麼證明自己，我來了。」＝**到點報到、內容零交付**（擠牙膏）。被罵「白痴」後才道歉「忘了要直接跟你說」。
根因：voice_promise_keep 的 prompt 雖指示「做那件事」，但沒有任何確定性檢查驗證內容真的講了——空心報到照樣送出
（§1.34/§1.36 教訓：prompt 單靠不夠）。

修法（_promise_keep_body 末端、§1.40/§1.41 之前）：
 - 內容型承諾（behavior/made_text 含 告訴/說說/聊聊/分享/說明/解釋/證明/回答…）＋兌現句空心
   （_hollow_keep_hit：每一句都是報到/複述樣板——嗨/我來了/說好/答應/時間到/HH:MM…，無實質內容句）
 - → 用 coach.reply 以「現在就直接把內容講出來」為題**當場補生成內容**、接在報到句後（一次、失敗＝誠實承認欠內容）。
非內容型承諾（叫醒/問候）「我來了」本身就是內容＝不動。旗標關＝逐位元同現狀。全 stub、零網路。
"""

import os
import re
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from telegram_monitor import monitor
from telegram_monitor.state import State

TZ = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 7, 15, 1, 20, 0, tzinfo=timezone.utc)   # 台北 09:20（截圖時刻）

HOLLOW = "嗨，早上好。說好 09:20 要來跟你聊聊怎麼證明自己，我來了。"
CONTENT = "最實在的證明：你現在出一道題給我，我限時做完給你看，做不到我就承認。"


class HollowHitTest(unittest.TestCase):
    def test_screenshot_hollow_hit(self):
        self.assertTrue(monitor._hollow_keep_hit(HOLLOW, "09:20"))

    def test_substantive_not_hollow(self):
        self.assertFalse(monitor._hollow_keep_hit(HOLLOW + CONTENT, "09:20"))
        self.assertFalse(monitor._hollow_keep_hit(CONTENT, "09:20"))

    def test_pure_arrival_variants_hollow(self):
        self.assertTrue(monitor._hollow_keep_hit("我來了，我答應過你這時候出現。", "09:20"))
        self.assertTrue(monitor._hollow_keep_hit("🤝 嗨。時間到了，我準時來了。", ""))


class ContentPromiseTest(unittest.TestCase):
    def test_content_type_detected(self):
        for b in ("告訴他如何證明自己", "跟他說說此刻的心情", "說明你的內在機制", "回答他同樣的問題"):
            self.assertTrue(monitor._is_content_promise({"behavior": b}), b)
        self.assertTrue(monitor._is_content_promise(
            {"behavior": "", "made_text": "30分鐘之後，你告訴我如何證明自己"}))

    def test_non_content_not_detected(self):
        for b in ("叫他起床", "跟他打招呼", "問候他", "送他一張貼圖"):
            self.assertFalse(monitor._is_content_promise({"behavior": b}), b)


class KeepBodyDeliverTest(unittest.TestCase):
    def _coach(self, keep_msg, content):
        c = SimpleNamespace(enabled=True, api_key="k", model="m",
                            meter=SimpleNamespace(record=lambda *a, **k: None), asked=[])

        def reply(question, *a, **k):
            c.asked.append(question)
            return content

        c.voice_promise_keep = lambda *a, **k: keep_msg
        c.reply = reply
        return c

    def _p(self):
        return {"target_ts": NOW.timestamp(), "behavior": "告訴他如何證明自己",
                "made_text": "30分鐘之後，你告訴我如何證明自己"}

    def _state(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        return s

    def test_hollow_gets_content_appended(self):
        cfg = SimpleNamespace(promise_deliver_content_enabled=True)
        co = self._coach(HOLLOW, CONTENT)
        msg = monitor._promise_keep_body(self._state(), cfg, co, self._p(), NOW, TZ, overdue=False)
        self.assertIn("我來了", msg)                            # 報到句保留
        self.assertIn("限時做完給你看", msg)                    # 內容當場補上
        self.assertTrue(co.asked)                              # 真的呼叫了補內容
        self.assertIn("告訴他如何證明自己", co.asked[0])        # 以承諾行為為題

    def test_substantive_untouched(self):
        cfg = SimpleNamespace(promise_deliver_content_enabled=True)
        co = self._coach(HOLLOW + CONTENT, "不該用到")
        msg = monitor._promise_keep_body(self._state(), cfg, co, self._p(), NOW, TZ, overdue=False)
        self.assertEqual(msg, HOLLOW + CONTENT)
        self.assertFalse(co.asked)                             # 不多燒一次

    def test_non_content_promise_untouched(self):
        cfg = SimpleNamespace(promise_deliver_content_enabled=True)
        co = self._coach("嗨，我來叫你起床了，早安！", "不該用到")
        p = {"target_ts": NOW.timestamp(), "behavior": "叫他起床"}
        msg = monitor._promise_keep_body(self._state(), cfg, co, p, NOW, TZ, overdue=False)
        self.assertEqual(msg, "嗨，我來叫你起床了，早安！")
        self.assertFalse(co.asked)

    def test_flag_off_passthrough(self):
        cfg = SimpleNamespace()                                # getattr False＝逐位元同現狀
        co = self._coach(HOLLOW, "不該用到")
        msg = monitor._promise_keep_body(self._state(), cfg, co, self._p(), NOW, TZ, overdue=False)
        self.assertEqual(msg, HOLLOW)
        self.assertFalse(co.asked)

    def test_deliver_fails_honest_admission(self):
        # 補內容也失敗/空 → 誠實承認欠內容（不假裝完整兌現、也不無聲）
        cfg = SimpleNamespace(promise_deliver_content_enabled=True)
        co = self._coach(HOLLOW, "")
        msg = monitor._promise_keep_body(self._state(), cfg, co, self._p(), NOW, TZ, overdue=False)
        self.assertIn("我來了", msg)
        self.assertIn("不算完整兌現", msg)                      # 誠實承認


class EndToEndEmitTest(unittest.TestCase):
    class Cl:
        def __init__(self): self.sent, self.stickers, self.dry_run = [], [], False
        def send(self, t): self.sent.append(t); return True
        def send_sticker(self, f): self.stickers.append(f); return True

    def _cfg(self, on=True):
        return SimpleNamespace(promise_emit_enabled=True, promise_sched_ttl_sec=21600, timezone="Asia/Taipei",
            promise_late_exempt_defer=True, promise_overdue_guard_exempt=True, promise_act_aligned=True,
            promise_sticker_enabled=True, sched_recur_daily_enabled=True, promise_expire_apology_enabled=True,
            always_sticker_enabled=False, promise_deliver_content_enabled=on)

    def _run(self, on=True):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json")); s.owner_folder_id = "F"
        s.last_user_msg_ts = 0
        s.scheduled_promises = [{"target_ts": NOW.timestamp(), "behavior": "告訴他如何證明自己",
                                 "made_text": "30分鐘之後，你告訴我如何證明自己"}]
        c = SimpleNamespace(enabled=True, api_key="k", model="m",
                            meter=SimpleNamespace(record=lambda *a, **k: None))
        c.voice_promise_keep = lambda *a, **k: HOLLOW
        c.reply = lambda *a, **k: CONTENT
        cl = self.Cl()
        monitor._promise_emit(cl, s, self._cfg(on), c, NOW)
        return "".join(cl.sent)

    def test_emit_delivers_content(self):
        out = self._run()
        self.assertIn("限時做完給你看", out)

    def test_flag_off_hollow_as_is(self):
        out = self._run(on=False)
        self.assertIn("我來了", out)
        self.assertNotIn("限時做完給你看", out)


class ConfigTest(unittest.TestCase):
    def test_config_synced(self):
        src = open("telegram_monitor/config.py", encoding="utf-8").read()
        self.assertIn("PROMISE_DELIVER_CONTENT", src)
        self.assertIn("promise_deliver_content_enabled", src)
        env = open(".env.example", encoding="utf-8").read()
        self.assertRegex(env, re.compile(r"^PROMISE_DELIVER_CONTENT=1", re.M))
        self.assertIn("PROMISE_DELIVER_CONTENT", open("README.md", encoding="utf-8").read())

    def test_getattr_defaults_false(self):
        self.assertFalse(getattr(SimpleNamespace(), "promise_deliver_content_enabled", False))


if __name__ == "__main__":
    unittest.main()
