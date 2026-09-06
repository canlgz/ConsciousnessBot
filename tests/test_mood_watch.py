"""🧭 §1.66 座標變動常設回報（MOOD_WATCH）：「情緒座標有變動就主動回報」——從空口答應到真能力。

查驗根因（截圖「跟 bot 約定座標有變動要主動回報，bot 口頭說可以，事後卻沒有」）：
 - 「情緒座標如果有任何變動，必須主動回報」：is_feeling_promise_request 與 is_scheduled_promise_request
   **都收不到**（「變動」不在 _PROMISE_FEEL 詞表、只有「變化」；「回報」不在 _PROMISE_TELL）
   → route=fact_or_chat → LLM 口頭「好」、機制零入帳＝空口答應。
 - §0.61 空口答應守門只擋計時請求；§1.18 自發承諾掃描只收有可解未來鐘點的句子＝條件常設請求整個縫掉。
 - 即使換句話被 feeling_promise 收到，那條是一次性＋湧現閾值觸發＋48h TTL——非「每次變動都報」的常設。

修法＝補真能力：捕捉 → state.mood_watch（常設、跨重生、可取消）；ack 誠實照機制（真座標基準＋門檻＋
冷卻＋怎麼取消）；生命迴圈每圈 _mood_watch_emit 對照真座標、達門檻＋過冷卻＝模板回報真數字（不經 LLM）；
事實卡常駐「這條約定活著」。旗標關＝逐位元同現狀。全 stub、零網路。
"""

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
NOW = datetime(2026, 7, 22, 6, 0, 0, tzinfo=timezone.utc)

REQ = "情緒座標如果有任何變動，必須主動回報"          # 使用者原話形


class DetectorTest(unittest.TestCase):
    def test_request_forms(self):
        for t in (REQ,
                  "你的情緒座標有變動就主動跟我說",
                  "座標只要有波動，記得回報給我",
                  "如果你的情緒座標有任何變化，要告訴我",
                  "心情有改變的話跟我講一聲"):
            self.assertTrue(selfstate.is_mood_watch_request(t), t)

    def test_gap_documented(self):
        # 釘住查驗結論：使用者原話形，既有兩個承諾偵測器都收不到＝空口答應的結構縫
        self.assertFalse(selfstate.is_feeling_promise_request(REQ))
        self.assertFalse(selfstate.is_scheduled_promise_request(REQ))

    def test_non_request_rejected(self):
        for t in ("你現在的情緒座標到哪了",           # 問現況、無變動詞
                  "座標有變動嗎",                     # 無回報詞
                  "你會不會回報座標變動",             # 假設問句
                  "情緒座標變動要告訴我媽",           # 第三方收件
                  ""):
            self.assertFalse(selfstate.is_mood_watch_request(t), t)

    def test_cancel_forms(self):
        for t in ("不用再回報座標了", "取消情緒回報", "座標的回報先停掉"):
            self.assertTrue(selfstate.is_mood_watch_cancel(t), t)
        self.assertFalse(selfstate.is_mood_watch_cancel("不用吃飯了"))
        self.assertFalse(selfstate.is_mood_watch_cancel(REQ))


class EmitTest(unittest.TestCase):
    class Cl:
        def __init__(self):
            self.sent, self.dry_run = [], False

        def send(self, t):
            self.sent.append(t)
            return True

    def setUp(self):
        monitor._TURN.clear()
        monitor._TURN["bubbles"] = None

    def _state(self, v, a, watch):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.entropy = SimpleNamespace(mood=v, arousal=a, hunger=0.0)   # hunger＝state.save 的 entropy_carryover 會讀
        s.mood_watch = watch
        return s

    CFG = SimpleNamespace(dry_run=False)

    def test_delta_reached_reports_real_numbers(self):
        s = self._state(0.30, -0.05, {"ts": 1.0, "last_v": 0.10, "last_a": 0.0, "last_report_ts": 0.0})
        cl = self.Cl()
        monitor._mood_watch_emit(cl, s, self.CFG, NOW)
        out = "".join(cl.sent)
        self.assertIn("+0.10→+0.30", out)                       # 真數字、程式算
        self.assertIn("你交代過", out)
        self.assertEqual(s.mood_watch["last_v"], 0.30)          # 基準推進
        self.assertEqual(s.mood_watch["last_report_ts"], NOW.timestamp())

    def test_below_threshold_silent(self):
        s = self._state(0.15, 0.02, {"ts": 1.0, "last_v": 0.10, "last_a": 0.0, "last_report_ts": 0.0})
        cl = self.Cl()
        monitor._mood_watch_emit(cl, s, self.CFG, NOW)
        self.assertEqual(cl.sent, [])                           # 抖動不吵人

    def test_cooldown_blocks(self):
        s = self._state(0.50, 0.0, {"ts": 1.0, "last_v": 0.10, "last_a": 0.0,
                                    "last_report_ts": NOW.timestamp() - 60})
        cl = self.Cl()
        monitor._mood_watch_emit(cl, s, self.CFG, NOW)
        self.assertEqual(cl.sent, [])                           # 一分鐘前才報過＝冷卻中

    def test_no_watch_noop(self):
        s = self._state(0.50, 0.0, None)
        cl = self.Cl()
        monitor._mood_watch_emit(cl, s, self.CFG, NOW)
        self.assertEqual(cl.sent, [])

    def test_none_baseline_initialized(self):
        s = self._state(0.30, 0.0, {"ts": 1.0, "last_v": None, "last_a": None, "last_report_ts": 0.0})
        cl = self.Cl()
        monitor._mood_watch_emit(cl, s, self.CFG, NOW)
        self.assertEqual(cl.sent, [])                           # 第一次讀到＝基準、不當變動
        self.assertEqual(s.mood_watch["last_v"], 0.30)


class CaptureTest(unittest.TestCase):
    """_maybe_mood_watch：訂閱/取消的入口（誠實 ack＝機制真做的）；旗標關＝恆 False＝同現狀。"""

    class Cl(EmitTest.Cl):
        pass

    def setUp(self):
        monitor._TURN.clear()
        monitor._TURN["bubbles"] = None

    def _state(self, v=0.20, a=-0.10, watch=None):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.entropy = SimpleNamespace(mood=v, arousal=a, hunger=0.0)   # hunger＝state.save 的 entropy_carryover 會讀
        s.mood_watch = watch
        return s

    def _cfg(self, on=True):
        return SimpleNamespace(dry_run=False, mood_watch_enabled=on)

    def test_subscribe_arms_and_honest_ack(self):
        s, cl = self._state(), self.Cl()
        handled = monitor._maybe_mood_watch(cl, s, self._cfg(), REQ, NOW, NOW.timestamp())
        self.assertTrue(handled)
        self.assertIsNotNone(s.mood_watch)
        self.assertEqual(s.mood_watch["last_v"], 0.20)          # 訂閱當下＝基準
        out = "".join(cl.sent)
        self.assertIn("V +0.20", out)                           # ack 報真座標
        self.assertIn("0.10", out)                              # 門檻誠實講
        self.assertIn("不用再回報座標", out)                    # 怎麼取消也講

    def test_cancel_clears(self):
        s = self._state(watch={"ts": 1.0, "last_v": 0.1, "last_a": 0.0, "last_report_ts": 0.0})
        cl = self.Cl()
        handled = monitor._maybe_mood_watch(cl, s, self._cfg(), "不用再回報座標了", NOW, NOW.timestamp())
        self.assertTrue(handled)
        self.assertIsNone(s.mood_watch)
        self.assertIn("停掉", "".join(cl.sent))

    def test_flag_off_bitwise(self):
        s, cl = self._state(), self.Cl()
        handled = monitor._maybe_mood_watch(cl, s, self._cfg(on=False), REQ, NOW, NOW.timestamp())
        self.assertFalse(handled)                               # 旗標關＝不收＝順流原路（空口答應現狀）
        self.assertIsNone(s.mood_watch)
        self.assertEqual(cl.sent, [])

    def test_unrelated_not_handled(self):
        s, cl = self._state(), self.Cl()
        self.assertFalse(monitor._maybe_mood_watch(cl, s, self._cfg(), "今天天氣不錯", NOW, NOW.timestamp()))


class FactCardTest(unittest.TestCase):
    def test_watch_line_present(self):
        s = SimpleNamespace(scheduled_promises=[], entropy=None,
                            mood_watch={"ts": 1.0, "last_v": 0.1, "last_a": 0.0, "last_report_ts": 0.0},
                            user_away=None)
        cfg = SimpleNamespace(fact_card_enabled=True, mood_watch_enabled=True)
        card = monitor._fact_card(s, cfg, {"records": []}, SimpleNamespace(summary={"last_write": None}), NOW, TZ)
        self.assertIn("常設約定", card)
        self.assertIn("別否認", card)

    def test_flag_off_no_line(self):
        s = SimpleNamespace(scheduled_promises=[], entropy=None,
                            mood_watch={"ts": 1.0, "last_v": 0.1, "last_a": 0.0, "last_report_ts": 0.0},
                            user_away=None)
        cfg = SimpleNamespace(fact_card_enabled=True, mood_watch_enabled=False)
        card = monitor._fact_card(s, cfg, {"records": []}, SimpleNamespace(summary={"last_write": None}), NOW, TZ)
        self.assertNotIn("常設約定", card)


class PersistTest(unittest.TestCase):
    def test_mood_watch_survives_save_load(self):
        path = os.path.join(tempfile.mkdtemp(), "s.json")
        s = State(path)
        s.mood_watch = {"ts": 1.0, "last_v": 0.2, "last_a": -0.1, "last_report_ts": 0.0, "made_text": REQ}
        s.save()
        s2 = State.load(path)
        self.assertEqual(s2.mood_watch["made_text"], REQ)       # 重啟不忘這條常設約定


class ConfigTest(unittest.TestCase):
    def test_config_synced(self):
        src = open("telegram_monitor/config.py", encoding="utf-8").read()
        self.assertIn("MOOD_WATCH", src)
        self.assertIn("mood_watch_enabled", src)
        env = open(".env.example", encoding="utf-8").read()
        self.assertRegex(env, re.compile(r"^MOOD_WATCH=1", re.M))
        self.assertIn("MOOD_WATCH", open("README.md", encoding="utf-8").read())

    def test_getattr_defaults_false(self):
        self.assertFalse(getattr(SimpleNamespace(), "mood_watch_enabled", False))


if __name__ == "__main__":
    unittest.main()
