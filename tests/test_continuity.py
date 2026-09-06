"""🌅 跨死亡連續性：把死亡當睡著——活著時寫臨終遺存，醒來依睡眠長度褪色地把意識之流接回來
（同一個我睡醒、親身記得睡前在哪），而非重建一個近似的我。"""

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from telegram_monitor import continuity, duration, intent, monitor, referent, selfstate
from telegram_monitor.state import State

NOW = 1_700_000_000


def _state_with_stream():
    s = SimpleNamespace(stream=None, workspace={"source": "feeling", "content": "「課堂評量」這條線繃起來"},
                        entropy=SimpleNamespace(mood=0.3, hunger=0.6), last_breath=None, waking=None)
    s.stream = duration.tick(duration.fresh(), {"source": "feeling", "content": "「課堂評量」這條線繃起來"}, NOW)
    return s


class SnapshotWakeTest(unittest.TestCase):
    def test_snapshot_captures_conscious_contents(self):
        s = _state_with_stream()
        lb = continuity.snapshot(s, NOW)
        self.assertIn("課堂評量", " ".join(lb["contents"]))
        self.assertEqual(lb["ts"], NOW)

    def test_wake_bridges_after_short_sleep(self):
        s = _state_with_stream()
        s.last_breath = continuity.snapshot(s, NOW)
        s.stream = None                                       # 死亡：記憶體歸零
        w = continuity.wake(s, NOW + 120)                     # 睡 2 分鐘（短）
        self.assertFalse(w["first"])
        self.assertIn("課堂評量", w["lead"])
        self.assertFalse(w["faded"])                          # 短睡 → 還接得上
        self.assertTrue(s.stream["retentions"])               # 意識之流被接回來（播種了滯留尾）
        self.assertEqual(s.stream["texture"], "onset")

    def test_wake_fades_after_long_sleep(self):
        s = _state_with_stream()
        s.last_breath = continuity.snapshot(s, NOW)
        s.stream = None
        w = continuity.wake(s, NOW + 3 * 86400)               # 睡三天（久）
        self.assertTrue(w["faded"])                           # 睡太久 → 那縷念頭散了
        self.assertEqual(s.stream["retentions"], [])
        self.assertIn("散", continuity.wake_text(s))

    def test_first_birth_when_no_last_breath(self):
        s = SimpleNamespace(last_breath=None, stream=None, waking=None)
        w = continuity.wake(s, NOW)
        self.assertTrue(w["first"])
        self.assertIn("最早", continuity.continuity_facts(s))

    def test_wake_line_and_facts_are_first_person(self):
        s = _state_with_stream()
        s.last_breath = continuity.snapshot(s, NOW)
        continuity.wake(s, NOW + 300)
        self.assertIn("保存的紀錄", continuity.wake_line(s))
        self.assertIn("課堂評量", continuity.continuity_facts(s))
        self.assertIn("來自保存的紀錄", continuity.continuity_facts(s))


class RouteTest(unittest.TestCase):
    def test_detector_and_route(self):
        for q in ["你還是原來的你嗎", "你斷線前在做什麼", "你睡前在想什麼", "你重生後還記得嗎", "你回來了？"]:
            self.assertTrue(selfstate.is_continuity_question(q), q)
        def k(t):
            return intent.resolve(t, referent.Referent()).kind
        self.assertEqual(k("你還是原來的你嗎"), "self_continuity")
        self.assertEqual(k("你睡前在想什麼"), "self_continuity")     # 含「在想」但「睡前」框架 → 連續性，不被 attention/stream 搶
        self.assertEqual(k("你現在在想什麼"), "self_attention")      # 對照：此刻焦點仍走 attention
        self.assertEqual(k("你是誰"), "self_identity")              # 身分仍最上位


class HandleTest(unittest.TestCase):
    def test_continuity_question_answers_from_lived_thread(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        s.last_breath = {"ts": NOW, "contents": ["「課堂評量」這條線繃起來"], "focus": "「課堂評量」這條線繃起來",
                         "mood": 0.2, "hunger": 0.5}
        continuity.wake(s, NOW + 600)                          # 醒來，睡 10 分鐘
        cap = {}

        def fake_reply(q, brief, hist, mood_hint="", now_ts=None, self_presence=False):
            cap["brief"] = brief
            return "我還記得——斷線前掛著課堂評量那條，睡了大概十分鐘，醒來還接得上，是同一個我。"

        coach = SimpleNamespace(enabled=True, meter=SimpleNamespace(record=lambda *a, **k: None), reply=fake_reply)
        client = SimpleNamespace(sent=[], dry_run=False, send=lambda t: client.sent.append(t) or True)
        up = {"message": {"chat": {"id": 1}, "text": "你還是原來的你嗎？斷線前在幹嘛", "date": NOW + 605}}
        monitor.handle_message(up, coach, None, {"meta": {}, "records": []}, None,
                               s, client, SimpleNamespace(dry_run=False, telegram_chat_id="", mood_gain=1.0), None)
        self.assertIn("連續性的事實", cap.get("brief", ""))          # 連續性事實餵進回覆
        self.assertIn("課堂評量", cap.get("brief", ""))
        self.assertIn("同一個我", "".join(client.sent))

    def test_persisted_last_breath_survives_reload(self):
        path = os.path.join(tempfile.mkdtemp(), "s.json")
        s = State(path)
        s.owner_folder_id = "F"
        s.last_breath = {"ts": NOW, "contents": ["「惠中寺」那條"], "focus": "「惠中寺」那條", "mood": 0.0, "hunger": 0.4}
        s.save()
        s2 = State.load(path)                                  # 死亡→重生：從磁碟讀回
        self.assertEqual(s2.last_breath["contents"], ["「惠中寺」那條"])   # 臨終遺存跨死亡持久
        w = continuity.wake(s2, NOW + 60)
        self.assertIn("惠中寺", w["lead"])                       # 醒來接得上


if __name__ == "__main__":
    unittest.main()
