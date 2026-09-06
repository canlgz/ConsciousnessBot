# -*- coding: utf-8 -*-
"""🩹 §2.12 旗標全開的煙霧測試：**使用者機器上的那條路**必須被真的執行過一次。

為什麼需要這一檔（§2.11 的死亡付出的代價）：
本 repo 的家規是「消費端一律 `getattr(cfg, "X", False)`」——它保證「旗標關＝逐位元同現狀」，
但也讓**測試用的假 cfg（`SimpleNamespace`，沒有那些欄位）永遠走不到旗標開的分支**。
§2.10 把 `text` 用在它被賦值之前，3835 條測試全綠、使用者機器上卻**每一則訊息都當場死**——
因為 `getattr(cfg, "X", False) and f(text)` 在測試裡總是短路。

§2.04–§2.08 這一輪又加了 16 個旗標＝16 條測試碰不到的路。所以這裡補一條**最低限度但真的會執行**的防線：
把 `Config` 的所有布林欄位都設成 True（＝他機器上的樣子），把每一條主動出聲 lane 與互動入口各跑一次，
**只要求不拋例外**——不驗內容（內容各有各的專屬測試），只驗「這條路真的跑得完」。全 stub、零網路。
"""

import os
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from telegram_monitor import config as configmod
from telegram_monitor import lifeloop, monitor
from telegram_monitor.state import State

NOW = datetime(2026, 7, 29, 10, 30, tzinfo=timezone.utc)


def all_on_cfg(**over):
    """真實 Config 的欄位齊全版：布林一律 True。新增旗標時**自動**被納入（讀 dataclass 欄位、不是手抄清單）。"""
    d = {}
    for name, f in getattr(configmod.Config, "__dataclass_fields__", {}).items():
        t = str(getattr(f, "type", ""))
        d[name] = True if "bool" in t else (0 if "int" in t else (0.0 if "float" in t else ""))
    d.update(timezone="Asia/Taipei", dry_run=True, gemini_api_key="", gemini_model="gemini-2.5-flash",
             notify_cooldown_min=0, quiet_start=1, quiet_end=6, owner_chat_id="1",
             worldline_cooldown_h=24, worldline_max_searches=40, worldline_tool_field="google_search",
             promise_sched_ttl_sec=3600, foresight_ttl_days=7)
    d.update(over)
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


def _state():
    s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
    s.entropy = lifeloop.EntropyState()
    s.self_state = {"gate": 2}
    s.owner_folder_id = "F"
    return s


COACH = SimpleNamespace(enabled=False, api_key="", model="m",
                        meter=SimpleNamespace(record=lambda *a, **k: None))


class EnabledCoach:
    """🩹 §2.14 coach-on 的分支也要被執行過：§2.12 只用 enabled=False 的 coach ⇒ 所有
    `coach and coach.enabled` 裡面的**參數組裝碼**（persona.*_rule / metacog.correction_facts /
    selfmod.pick_change …）一行都沒跑過——那正是 §2.05–§2.08 新加的那一層。
    api_key 留空＝任何直呼 gemini 的路（render_experience）自然跳過、零網路。
    judge_* 回 (False,'')＝不入帳；voice_*/reply 回一段夠長的字串＝走完送出鏈。"""
    enabled = True
    api_key = ""
    model = "m"
    dry_run = True
    meter = SimpleNamespace(record=lambda *a, **k: None)

    def __getattr__(self, name):
        if name.startswith("judge_"):
            return lambda *a, **k: (False, "")
        if name in ("premise_check",):                    # 真身回 dict 或 None；回 str 會讓 `_pc.get` 炸（實測）
            return lambda *a, **k: None
        if name == "ask":                                  # 真身回 (kind, data, voice) 三元組（實測 unpack 炸）
            return lambda *a, **k: ("chat", None, "這是一段夠長的測試回覆內容，讓後面的守門有東西可以檢查。")
        return lambda *a, **k: "這是一段夠長的測試回覆內容，讓後面的各道守門有東西可以檢查、也走得完送出鏈。"
DATA = {"records": [{"topicLabel": "讀誦經書", "category": "閱讀", "text": "今天讀到無住生心",
                     "ts": "2026-07-29T01:00:00Z", "id": "a"}]}


class ProactiveLanesSmokeTest(unittest.TestCase):
    """每條主動 lane 在**旗標全開**下跑一次：只要求不拋（內容由各自的測試把關）。"""

    LANES = ("_spontaneous_emit", "_coping_emit", "_metacog_correct", "_soothe_unanswered",
             "_promise_emit", "_habit_absence_emit", "_ac_drift_emit", "_insight_emit",
             "_foresight_emit", "_worldline_emit", "_mood_watch_emit")

    def test_every_lane_runs_without_raising(self):
        for name in self.LANES:
            fn = getattr(monitor, name, None)
            self.assertIsNotNone(fn, name)
            with self.subTest(lane=name):
                s, cfg = _state(), all_on_cfg()
                kw = {}
                if "data" in fn.__code__.co_varnames[:fn.__code__.co_argcount]:
                    kw["data"] = DATA
                if "records" in fn.__code__.co_varnames[:fn.__code__.co_argcount]:
                    kw["records"] = DATA["records"]
                args = [Cl(), s, cfg]
                if "coach" in fn.__code__.co_varnames[:fn.__code__.co_argcount]:
                    args.append(COACH)
                args.append(NOW)
                try:
                    fn(*args, **kw)
                except TypeError as e:                     # 簽名不合＝這條 lane 的呼叫形狀變了，也該被看見
                    self.fail(f"{name} 簽名對不上：{e}")

    def test_experience_step_runs(self):
        s, cfg = _state(), all_on_cfg()
        monitor._experience_step(Cl(), s, cfg, COACH, {"now": NOW, "data": DATA, "res": None, "snap": None})

    def test_every_lane_runs_with_coach_on(self):
        # 🩹 §2.14 同一批 lane、coach **開著**再跑一次：voice_* 呼叫前的參數組裝碼這次真的會執行
        for name in self.LANES:
            fn = getattr(monitor, name)
            with self.subTest(lane=name):
                s, cfg = _state(), all_on_cfg()
                kw = {}
                if "data" in fn.__code__.co_varnames[:fn.__code__.co_argcount]:
                    kw["data"] = DATA
                if "records" in fn.__code__.co_varnames[:fn.__code__.co_argcount]:
                    kw["records"] = DATA["records"]
                args = [Cl(), s, cfg]
                if "coach" in fn.__code__.co_varnames[:fn.__code__.co_argcount]:
                    args.append(EnabledCoach())
                args.append(NOW)
                fn(*args, **kw)


class InteractiveEntrySmokeTest(unittest.TestCase):
    """互動入口（§2.11 死在這裡）：旗標全開＋各種形狀的訊息都要活著回來。"""

    # ⚠️ 不放 `/status` 這類**吃 snap 的指令**：本檔傳 snap=None，那些指令會 AttributeError；
    # 那是測試替身的形狀問題、不是產品路徑（真實迴圈一定給得出 snap），指令各有自己的測試。
    MSGS = ("你這次走哪裡不同", "你現在情緒座標怎樣啦", "給你 30 分鐘", "好好想想",
            "時間到了再給我說明", "我的星座是？", "你怎麼這麼多廢話", "嗯")

    def test_handle_message_survives(self):
        for t in self.MSGS:
            with self.subTest(text=t):
                monitor._TURN.clear()
                s = _state()
                upd = {"message": {"text": t, "date": 1785300000, "chat": {"id": 1}, "message_id": 7}}
                with patch.object(monitor, "_say", lambda *a, **k: True):
                    monitor.handle_message(upd, COACH, None, DATA, None, s, Cl(), all_on_cfg(), None)

    def test_handle_message_survives_with_coach_on(self):
        # snap 在 production **不可能是 None**（_collect 失敗直接拋給 LifeLoop 重試）⇒ coach-on 分支
        # 會讀 snapshot.summary，這裡照真實形狀給 analyzer.analyze 的產物，不是 None。
        from telegram_monitor import analyzer
        snap = analyzer.analyze(DATA, NOW, None, 6)
        for t in self.MSGS:
            with self.subTest(text=t):
                monitor._TURN.clear()
                s = _state()
                upd = {"message": {"text": t, "date": 1785300000, "chat": {"id": 1}, "message_id": 7}}
                with patch.object(monitor, "_say", lambda *a, **k: True):
                    monitor.handle_message(upd, EnabledCoach(), None, DATA, snap, s, Cl(), all_on_cfg(), None)


class ConventionTest(unittest.TestCase):
    def test_cfg_builder_reads_dataclass_not_a_hand_list(self):
        # 新旗標必須**自動**進來：手抄清單會隨時間腐爛，那正是這道防線失效的方式
        import inspect
        src = inspect.getsource(all_on_cfg)
        self.assertIn("__dataclass_fields__", src)

    def test_covers_every_bool_flag(self):
        cfg = all_on_cfg()
        for name, f in getattr(configmod.Config, "__dataclass_fields__", {}).items():
            if "bool" in str(getattr(f, "type", "")):
                self.assertIs(getattr(cfg, name), True, name)


if __name__ == "__main__":
    unittest.main()
