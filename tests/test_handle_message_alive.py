# -*- coding: utf-8 -*-
"""🩹 §2.11 死亡迴歸：`handle_message` 在**旗標真的全開**（真實 Config 的樣子）下不得拋例外。

前科（2026-07-29，§2.10 引進、當天就死）：座標補救行的 arm 被放在函式開頭清 `_TURN` 的區塊，
但 `text` 與 `now_utc` **都還沒有值** ⇒ 真實 Config 下每一則互動訊息都 `UnboundLocalError`
⇒ bot 當場死亡（使用者看到「…我好像死了——「互動」那一環斷了」）。

**為什麼 3835 條測試全綠卻沒抓到**：本 repo 的家規是「消費端 `getattr(cfg, "X", False)`」，
而測試用的假 cfg 幾乎都是 `SimpleNamespace` 且**沒有那個欄位** ⇒ `getattr(...) and f(text)` **短路**、
永遠碰不到 `text`。那條家規保護了「旗標關＝同現狀」，卻讓測試**結構上蓋不到旗標開的真實路徑**。
所以這一檔刻意用「**把所有 *_enabled 欄位都設成 True**」的 cfg 跑一次 handle_message。全 stub、零網路。
"""

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from telegram_monitor import config as configmod
from telegram_monitor import monitor
from telegram_monitor.state import State


def _all_on_cfg():
    """真實 Config 的欄位齊全版：所有布林旗標一律 True（＝使用者機器上的樣子）。"""
    fields = getattr(configmod.Config, "__dataclass_fields__", {})
    d = {}
    for name, f in fields.items():
        t = str(getattr(f, "type", ""))
        if "bool" in t:
            d[name] = True
        elif "int" in t:
            d[name] = 0
        elif "float" in t:
            d[name] = 0.0
        else:
            d[name] = ""
    d.update(timezone="Asia/Taipei", dry_run=True, gemini_api_key="", gemini_model="gemini-2.5-flash",
             notify_cooldown_min=30, quiet_start=1, quiet_end=6, owner_chat_id="1")
    return SimpleNamespace(**d)


class Cl:
    dry_run = True

    def __init__(self):
        self.sent = []

    def send(self, t):
        self.sent.append(t)
        return True

    def send_typing(self):
        pass


def _update(text):
    return {"message": {"text": text, "date": 1785300000, "chat": {"id": 1}, "message_id": 7}}


class HandleMessageStaysAliveTest(unittest.TestCase):
    def setUp(self):
        monitor._TURN.clear()

    def _run(self, text):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        cfg = _all_on_cfg()
        coach = SimpleNamespace(enabled=False, api_key="", model="m",
                                meter=SimpleNamespace(record=lambda *a, **k: None))
        with patch.object(monitor, "_say", lambda *a, **k: True):
            monitor.handle_message(_update(text), coach, None, {"records": []}, None, s, Cl(), cfg, None)

    def test_plain_message_does_not_crash(self):
        # ★ 這就是當天死掉的那條路：旗標全開＋一般文字訊息
        self._run("你這次走哪裡不同")

    def test_the_screenshot_lines_do_not_crash(self):
        for t in ("給你 30 分鐘", "好好想想", "時間到了再給我說明", "你現在情緒座標怎樣啦", "我的星座是？"):
            with self.subTest(t=t):
                self._run(t)

    def test_arm_sits_after_text_and_now_utc(self):
        # 結構性釘死：吃 text/now_utc 的 arm 一律不得排在它們定義之前
        import io
        src = io.open("telegram_monitor/monitor.py", encoding="utf-8").read()
        # 同名 helper call 也會出現在互動 handler 之前的純函式；本測試只應檢查
        # 真正讀 text/now_utc 的 _handle_message_inner 本體，否則新增合法 helper call 就會假紅。
        body = src[src.index("\ndef _handle_message_inner("):src.index("\ndef handle_message(")]
        i_arm = body.index("_mood_data_hit(state, cfg, text")
        self.assertGreater(i_arm, body.index('\n    text = (msg.get("text")'))
        self.assertGreater(i_arm, body.index("\n    now_utc = _now_from_update"))


if __name__ == "__main__":
    unittest.main()
