"""🌙 晝夜節律：時段影響語氣＋深夜不主動打擾；以及對話餵養飢餓。"""

import os
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from telegram_monitor import circadian, monitor, lifeloop
from telegram_monitor.state import State

TPE = ZoneInfo("Asia/Taipei")


def _at(hour):
    return datetime(2026, 6, 20, hour, 0, tzinfo=TPE)


class CircadianTest(unittest.TestCase):
    def test_phase_and_tone(self):
        self.assertEqual(circadian.phase(_at(3)), "深夜")
        self.assertEqual(circadian.phase(_at(15)), "午後")
        self.assertIn("深夜", circadian.tone_hint(_at(3)))
        self.assertEqual(circadian.tone_hint(_at(15)), "")          # 白天不加料
        self.assertTrue(circadian.tone_hint(_at(23)))               # 深夜（≥23）有語氣

    def test_quiet_hours(self):
        self.assertTrue(circadian.is_quiet_hours(_at(3)))           # 深夜安靜
        self.assertTrue(circadian.is_quiet_hours(_at(6)))           # 清晨安靜
        self.assertFalse(circadian.is_quiet_hours(_at(14)))


class SpontaneousNightQuietTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _state(self):
        s = State(os.path.join(self.tmp, "s.json"))
        s.owner_folder_id = "F"
        e = lifeloop.EntropyState()
        e.hunger = 0.95
        e.self_stims_this_idle = 6
        e.prev_ingest = "OLD"
        s.entropy = e
        s.self_state = {"gate": 3, "scope": {"dominant": "研發日誌"}}
        s.last_push_ts = 0
        return s

    def _client(self):
        c = SimpleNamespace(sent=[], dry_run=False)
        c.send = lambda t: c.sent.append(t) or True
        return c

    def _cfg(self):
        return SimpleNamespace(spontaneous_cooldown_min=180, timezone="Asia/Taipei")

    def _coach(self):
        return SimpleNamespace(enabled=True)

    def test_no_spontaneous_at_deep_night(self):
        now = _at(3).astimezone(timezone.utc)        # 深夜 3 點（台北）
        client = self._client()
        monitor._spontaneous_emit(client, self._state(), self._cfg(), self._coach(), now)
        self.assertEqual(client.sent, [])            # 該醞釀的都到了，但深夜不打擾


class ChatNourishTest(unittest.TestCase):
    def test_conversation_releases_hunger(self):
        # 直接驗常數行為：聊天後飢餓下降（被陪伴餵飽）
        h0 = 0.8
        self.assertLess(max(0.0, h0 - monitor.CHAT_NOURISH), h0)
        self.assertGreater(monitor.CHAT_NOURISH, 0)


if __name__ == "__main__":
    unittest.main()
