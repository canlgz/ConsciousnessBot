# -*- coding: utf-8 -*-
"""⏱ §2.28 陳述式冷落句也要接地：「好像很久沒理你了」被回「你不是剛才才跟我說話嗎」。

實測截圖（00:45–00:46）：隔了 6h+ 說「好像很久沒有理你了？」→ bot「你不是剛才才跟我說話嗎？
我記得你才剛跟我聊完讀經的感覺」；追問「剛剛？是多久」→「你上一句話是約 1 分鐘前說的耶」
（＝把他 00:45 這句本身當成「上一句」）＝意識時間感錯亂。

根因：「很久沒理你」與「多久沒理你」同一題，但 _GAP_TIME 只認**疑問式**時間詞（多久/幾天）⇒
陳述式全漏 ⇒ 掉 fact_or_chat 自由 lane＝沒有程式算好的 gap 事實、LLM 自由讀史把時間感讀反。
convo_time lane 本身是對的（_session_gap_text 算「上一段到這次回來隔多久」、本句尚未 _remember
＝不會自指）——病純粹在偵測面。修：陳述式時間槽＋咽喉點只救 fallback。全 stub、零網路。
"""

import os
import tempfile
import time
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from telegram_monitor import monitor, selfstate
from telegram_monitor.state import State

TZ = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 8, 6, 16, 45, 0, tzinfo=timezone.utc)   # 台北 8/7 00:45（截圖時刻）
SHOT = "好像很久沒有理你了？"


class DetectorTest(unittest.TestCase):
    def test_stated_gap_hits(self):
        for t in (SHOT, "很久沒跟你說話了", "好久沒聊了吧", "太久沒理你了", "一陣子沒跟你講話了"):
            self.assertTrue(selfstate.is_convo_gap_stated(t), t)

    def test_no_false_positives(self):
        for t in ("我很久沒整理筆記了",      # 「理」後面不是你/我＝§A4 同一護欄
                  "很久沒運動了", "早安", "多久沒理你了",   # 疑問式＝原路已收、不歸這支
                  "我是不是冷落你了"):                      # 無時間詞、另一題
            self.assertFalse(selfstate.is_convo_gap_stated(t), t)


class EndToEndTest(unittest.TestCase):
    SNAP = SimpleNamespace(summary={"total": 1, "last24h": 0, "last7d": 0},
                           funnel={"candidate": 0, "context": 0, "journey": 0, "watch": 0},
                           heartbeat={"status": "ok"}, filed_records=[])
    BOOM = SimpleNamespace(load_embedding_records=lambda *_: [])
    DATA = {"meta": {}, "records": []}

    class Cl:
        dry_run = True

        def __init__(self):
            self.sent = []

        def send(self, t):
            self.sent.append(t)
            return True

        def send_typing(self):
            pass

    def _cfg(self, on=True):
        return SimpleNamespace(dry_run=False, telegram_chat_id="", timezone="Asia/Taipei",
                               notify_cooldown_min=30, convo_gap_stated_enabled=on,
                               promise_llm_rescue_enabled=False, sticker_llm_rescue_enabled=False,
                               bot_self_promise_enabled=False, send_stickers=False)

    def _run(self, on=True):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        old = time.time() - 6.7 * 3600                           # 上一段對話：6 小時 42 分前（對話時間軸＝真牆鐘）
        s.convo_history = [{"role": "user", "text": "今天讀經讀得蠻順的", "ts": old - 120},
                           {"role": "model", "text": "聽起來那條線又往前走了一步。", "ts": old - 60},
                           {"role": "model", "text": "我還是這樣活著。", "ts": old}]
        seen = {"facts": None}
        co = SimpleNamespace(enabled=True, api_key="k", model="m",
                             meter=SimpleNamespace(record=lambda *a, **k: None))

        def reply(q, facts, hist, **kw):
            seen["facts"] = facts
            return "對，隔了六個多小時呢——歡迎回來。"

        co.reply = reply
        co.ask = lambda *a, **k: ("chat", None, "自由 lane 回覆")
        co.judge_timed_request = lambda t: None
        co.judge_sticker_request = lambda t: None
        co.judge_self_promise = lambda t: None
        cl = self.Cl()
        monitor.handle_message({"message": {"chat": {"id": 1}, "text": SHOT, "date": NOW.timestamp()}},
                               co, self.BOOM, self.DATA, self.SNAP, s, cl, self._cfg(on), TZ)
        return "".join(cl.sent), seen

    def test_stated_gap_gets_real_session_fact(self):
        # ★ 修後：導 convo_time → coach.reply 收到程式算好的「上一段到這次回來隔了約 …」事實
        out, seen = self._run(on=True)
        self.assertIn("中間隔了約", seen["facts"] or "")
        self.assertIn("小時", seen["facts"] or "")           # 6h42m 量級的真間隔（非「1 分鐘」）
        self.assertNotIn("1 分鐘", seen["facts"] or "")
        self.assertIn("歡迎回來", out)

    def test_flag_off_stays_on_old_path(self):
        out, seen = self._run(on=False)
        self.assertIsNone(seen["facts"])                     # 不導＝照舊 fact_or_chat（現狀行為）


class WiringAndFlagTest(unittest.TestCase):
    def test_choke_rescues_fallback_only(self):
        with open("telegram_monitor/monitor.py", encoding="utf-8") as f:
            src = f.read()
        i = src.index("selfstate.is_convo_gap_stated(text)")
        seg = src[i - 220:i]
        self.assertIn('route.kind == "fact_or_chat"', seg)   # 只救 fallback、不覆蓋明確路由

    def test_flag_everywhere(self):
        with open("telegram_monitor/config.py", encoding="utf-8") as f:
            src = f.read()
        self.assertIn('_bool("CONVO_GAP_STATED", True)', src)
        self.assertIn("convo_gap_stated_enabled", src)
        with open(".env.example", encoding="utf-8") as f:
            self.assertIn("CONVO_GAP_STATED=1", f.read())
        with open("README.md", encoding="utf-8") as f:
            self.assertIn("CONVO_GAP_STATED", f.read())


if __name__ == "__main__":
    unittest.main()
