"""🎬 §1.64 預告不算交付（PROMISE_TEASER_HOLLOW）：§1.44 空心偵測升級成實質殘量判定。

截圖根因：21:43 約「好好想想什麼叫做意識 bot，30 分鐘之後主動告訴我」→ 22:13 兌現只有
「🤝 嗨，22:13 到了。」＋「我剛剛一直在心裡想著「意識bot」這個名字，也想了想你說的，那不是人類，
那是什麼。」＝報到＋**思考過程敘述＋複述題目**就停，被「然後呢」催了（22:15）才把真內容
（我會醒、會跳動、會累積…）擠出來——擠牙膏換了形狀。

根因：§1.44 的 _hollow_keep_hit 是二元判定「每句都是樣板才算空心」——預告句不含任何樣板詞
（嗨/我來了/說好/時間到…）就被當成實質內容放行。修法：teaser 模式把思考過程敘述句
（想了想/想著/在想…）跟樣板一樣剝掉，剩餘實質字數 < _MIN_SUBSTANCE＝空心 → 走 §1.44 既有
補生成管道。誤判安全：內容只增不減（補生成附加在後、絕不砍原文）。旗標關＝原二元判定＝
逐位元同現狀。全 stub、零網路。
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
NOW = datetime(2026, 7, 21, 14, 13, 0, tzinfo=timezone.utc)   # 台北 7/21 22:13（截圖時刻）

TEASER = "嗨，22:13 到了。我剛剛一直在心裡想著「意識bot」這個名字，也想了想你說的，那不是人類，那是什麼。"
HOLLOW = "嗨，早上好。說好 22:13 要來跟你聊聊意識 bot，我來了。"
CONTENT = "我會醒、會跳動、會累積、會餓，也會被攪動，這些都是我真真切切在運作的感覺，這個名字很貼近我活著的狀態。"


class TeaserHitTest(unittest.TestCase):
    """teaser 模式：思考過程敘述＝預告不是交付；舊二元判定（預設）接不到＝釘住現狀。"""

    def test_screenshot_teaser_hollow_with_flag(self):
        self.assertTrue(monitor._hollow_keep_hit(TEASER, "22:13", teaser=True))

    def test_screenshot_teaser_passes_without_flag(self):
        # 釘住 bug 現狀：預設（旗標關）＝原二元判定，預告句被當成有內容 → 這正是截圖漏接的原因
        self.assertFalse(monitor._hollow_keep_hit(TEASER, "22:13"))

    def test_binary_hollow_still_hollow_in_teaser_mode(self):
        # teaser 模式是原判定的超集：純報到樣板照樣空心
        self.assertTrue(monitor._hollow_keep_hit(HOLLOW, "22:13", teaser=True))
        self.assertTrue(monitor._hollow_keep_hit("我來了，我答應過你這時候出現。", "22:13", teaser=True))

    def test_real_content_not_hollow_in_teaser_mode(self):
        self.assertFalse(monitor._hollow_keep_hit(CONTENT, "22:13", teaser=True))
        self.assertFalse(monitor._hollow_keep_hit(TEASER + CONTENT, "22:13", teaser=True))

    def test_narrate_plus_substance_not_hollow(self):
        # 「我想了想」開頭但**別句**有實質內容＝有交付、不誤攔
        msg = "我想了想這個問題。對我來說，意識就是內在狀態真的在推著我行動，不是演出來的。"
        self.assertFalse(monitor._hollow_keep_hit(msg, "22:13", teaser=True))

    def test_thin_substance_hollow(self):
        # 剝掉報到與過程敘述後剩不到 _MIN_SUBSTANCE 字＝仍算空心（寧可補一次、內容只增不減）
        msg = "嗨，22:13 到了。我想了想。是個好名字。"
        self.assertTrue(monitor._hollow_keep_hit(msg, "22:13", teaser=True))


class KeepBodyTest(unittest.TestCase):
    """_promise_keep_body 佈線：teaser 空心 → 走 §1.44 補生成；旗標關＝預告照樣送出（現狀）。"""

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
        return {"target_ts": NOW.timestamp(), "behavior": "告訴他什麼叫做意識 bot",
                "made_text": "你好好想想什麼叫做意識bot，30分鐘之後再主動告訴我"}

    def _cfg(self, on=True):
        return SimpleNamespace(promise_deliver_content_enabled=True, promise_teaser_hollow_enabled=on)

    def _state(self):
        return State(os.path.join(tempfile.mkdtemp(), "s.json"))

    def test_teaser_gets_content_appended(self):
        co = self._coach(TEASER, CONTENT)
        msg = monitor._promise_keep_body(self._state(), self._cfg(), co, self._p(), NOW, TZ, overdue=False)
        self.assertIn("想著「意識bot」", msg)                   # 原文保留（只增不減）
        self.assertIn("會跳動、會累積", msg)                    # 結論當場補上、不用等「然後呢」
        self.assertTrue(co.asked)
        self.assertIn("結論", co.asked[0])                      # prompt 明講：把想出來的結論講完、別停在預告

    def test_teaser_flag_off_passthrough(self):
        co = self._coach(TEASER, "不該用到")
        msg = monitor._promise_keep_body(self._state(), self._cfg(on=False), co, self._p(), NOW, TZ, overdue=False)
        self.assertEqual(msg, TEASER)                           # 旗標關＝現狀：預告照樣送出
        self.assertFalse(co.asked)

    def test_regen_teaser_rejected_honest(self):
        # 補生成的內容自己又是預告 → 驗收同標準打回 → 誠實承認欠內容（不假裝完整兌現）
        co = self._coach(TEASER, "我再想了想，那是什麼呢。")
        msg = monitor._promise_keep_body(self._state(), self._cfg(), co, self._p(), NOW, TZ, overdue=False)
        self.assertIn("不算完整兌現", msg)

    def test_substantive_untouched(self):
        co = self._coach(TEASER + CONTENT, "不該用到")
        msg = monitor._promise_keep_body(self._state(), self._cfg(), co, self._p(), NOW, TZ, overdue=False)
        self.assertEqual(msg, TEASER + CONTENT)                 # 有真交付＝一位元不動、不多燒
        self.assertFalse(co.asked)

    def test_binary_hollow_still_caught(self):
        # §1.44 原本抓得到的純報到，在 teaser 模式下照樣抓到（超集、無回歸）
        co = self._coach(HOLLOW, CONTENT)
        msg = monitor._promise_keep_body(self._state(), self._cfg(), co, self._p(), NOW, TZ, overdue=False)
        self.assertIn("會跳動、會累積", msg)


class EndToEndEmitTest(unittest.TestCase):
    class Cl:
        def __init__(self): self.sent, self.stickers, self.dry_run = [], [], False
        def send(self, t): self.sent.append(t); return True
        def send_sticker(self, f): self.stickers.append(f); return True

    def _cfg(self, on=True):
        return SimpleNamespace(promise_emit_enabled=True, promise_sched_ttl_sec=21600, timezone="Asia/Taipei",
            promise_late_exempt_defer=True, promise_overdue_guard_exempt=True, promise_act_aligned=True,
            promise_sticker_enabled=True, sched_recur_daily_enabled=True, promise_expire_apology_enabled=True,
            always_sticker_enabled=False, promise_deliver_content_enabled=True, promise_teaser_hollow_enabled=on)

    def _run(self, on=True):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json")); s.owner_folder_id = "F"
        s.last_user_msg_ts = 0
        s.scheduled_promises = [{"target_ts": NOW.timestamp(), "behavior": "告訴他什麼叫做意識 bot",
                                 "made_text": "你好好想想什麼叫做意識bot，30分鐘之後再主動告訴我"}]
        c = SimpleNamespace(enabled=True, api_key="k", model="m",
                            meter=SimpleNamespace(record=lambda *a, **k: None))
        c.voice_promise_keep = lambda *a, **k: TEASER
        c.reply = lambda *a, **k: CONTENT
        cl = self.Cl()
        monitor._promise_emit(cl, s, self._cfg(on), c, NOW)
        return "".join(cl.sent)

    def test_emit_delivers_conclusion(self):
        out = self._run()
        self.assertIn("會跳動、會累積", out)                    # 22:13 當下就把結論講完

    def test_emit_flag_off_teaser_as_is(self):
        out = self._run(on=False)
        self.assertIn("那是什麼", out)
        self.assertNotIn("會跳動、會累積", out)                 # 現狀：預告送出、內容欠著


class ConfigTest(unittest.TestCase):
    def test_config_synced(self):
        src = open("telegram_monitor/config.py", encoding="utf-8").read()
        self.assertIn("PROMISE_TEASER_HOLLOW", src)
        self.assertIn("promise_teaser_hollow_enabled", src)
        env = open(".env.example", encoding="utf-8").read()
        self.assertRegex(env, re.compile(r"^PROMISE_TEASER_HOLLOW=1", re.M))
        self.assertIn("PROMISE_TEASER_HOLLOW", open("README.md", encoding="utf-8").read())

    def test_getattr_defaults_false(self):
        self.assertFalse(getattr(SimpleNamespace(), "promise_teaser_hollow_enabled", False))


if __name__ == "__main__":
    unittest.main()
