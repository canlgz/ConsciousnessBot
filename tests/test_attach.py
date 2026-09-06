"""附件 / 訊息時間戳 / 指令 的純函式測試（mock Gemini，不碰網路/Drive/Telegram）。

事實工具（時間/清單/跨度/花費/狀態）已移到 datatools，於 test_datatools.py 測試。
"""

import unittest
from unittest import mock
from types import SimpleNamespace

from telegram_monitor import monitor, coach, selfstate


def _media(i, ts, lab, txt):
    return {"id": f"m{i}", "ts": ts, "type": "image", "topicLabel": lab,
            "text": txt, "fileId": f"fid{i}"}


class AttachIntentTest(unittest.TestCase):
    def test_detects_media_request(self):
        self.assertTrue(selfstate.is_attachment_request("給我看庭院那張照片"))
        self.assertTrue(selfstate.is_attachment_request("把那份 pdf 調出來"))

    def test_plain_chat_not_flagged(self):
        self.assertFalse(selfstate.is_attachment_request("我最近是不是太散了？"))


class SelectAttachmentsTest(unittest.TestCase):
    def _coach(self):
        return coach.Coach(SimpleNamespace(gemini_api_key="k", gemini_model="gemini-2.5-flash"))

    def test_picks_by_returned_indices(self):
        media = [
            _media(1, "2026-06-15T10:00:00Z", "社區庭院景觀", "中庭新花圃"),
            _media(2, "2026-06-14T10:00:00Z", "浴室水管", "水管接頭"),
            _media(3, "2026-06-13T10:00:00Z", "教學", "板書照片"),
        ]
        with mock.patch("telegram_monitor.gemini.generate", return_value="1, 3"):
            picked = self._coach().select_attachments("庭院和板書的照片", media)
        self.assertEqual([r["id"] for r in picked], ["m1", "m3"])  # 依 ts 新→舊編號

    def test_none_returns_empty(self):
        media = [_media(1, "2026-06-15T10:00:00Z", "x", "y")]
        with mock.patch("telegram_monitor.gemini.generate", return_value="NONE"):
            self.assertEqual(self._coach().select_attachments("不相關的問題", media), [])

    def test_disabled_without_key(self):
        c = coach.Coach(SimpleNamespace(gemini_api_key="", gemini_model="x"))
        self.assertEqual(c.select_attachments("看圖", [_media(1, "t", "a", "b")]), [])


class NowFromUpdateTest(unittest.TestCase):
    def test_now_from_telegram_message_date(self):
        from datetime import datetime, timezone
        ts = 1718456460  # 固定 epoch；用 Telegram 訊息時間戳當「現在」
        got = monitor._now_from_update({"message": {"date": ts}})
        self.assertEqual(got, datetime.fromtimestamp(ts, tz=timezone.utc))

    def test_now_falls_back_without_date(self):
        got = monitor._now_from_update({"message": {}})
        self.assertIsNotNone(got.tzinfo)


class SensitivityOverrideTest(unittest.TestCase):
    def _cfg(self):
        return SimpleNamespace(selfstate_adaptive=True, selfstate_sensitivity=2.0,
                               selfstate_z_star=2.0, selfstate_tau_star=0.78, selfstate_int_min=0.5,
                               selfstate_diff_min=0.0, selfstate_n_min=8, selfstate_r_min=3)

    def test_override_takes_precedence(self):
        st = SimpleNamespace(sensitivity_override=None)
        self.assertEqual(monitor._chain_params(self._cfg(), st)["sensitivity"], 2.0)
        st.sensitivity_override = 0.5
        p = monitor._chain_params(self._cfg(), st)
        self.assertEqual(p["sensitivity"], 0.5)
        # 覆蓋進入參數簽章 → 快取會因 k 改變而失效
        self.assertIn("0.5", monitor._params_sig(p))

    def test_loop_wait_secs(self):
        cfg = SimpleNamespace(lifeloop_wait_secs=3.0)
        st = SimpleNamespace(pulse_override=None)
        self.assertEqual(monitor._loop_wait_secs(cfg, st), 3.0)        # 預設環間 3 秒
        st.pulse_override = 5
        self.assertEqual(monitor._loop_wait_secs(cfg, st), 5.0)        # /pulse 手動 5 秒
        st.pulse_override = 0.2
        self.assertEqual(monitor._loop_wait_secs(cfg, st), 0.5)        # 下限 0.5 秒


class StatusTest(unittest.TestCase):
    def test_status_shows_dials_and_gate(self):
        from datetime import datetime, timezone
        from zoneinfo import ZoneInfo
        from telegram_monitor import analyzer
        cfg = SimpleNamespace(selfstate_sensitivity=2.0, lifeloop_wait_secs=3.0)
        st = SimpleNamespace(sensitivity_override=1.0, pulse_override=5, vitality=None, env=None,
                             self_state={"gate": 2, "omegas": {"rule": {"adaptive": True}},
                                         "computed_at": None, "sensitivity": 1.0})
        snap = analyzer.analyze({"records": [], "contexts": [], "journeys": [], "explorations": [], "meta": {}},
                                datetime(2026, 6, 16, tzinfo=timezone.utc), ZoneInfo("Asia/Taipei"))
        out = monitor._status_text(st, cfg, snap)
        self.assertIn("k＝1.0", out)            # 顯示「判定當時實際用的 k」（讀數內的 sensitivity），非另一拍重推
        self.assertIn("判定當時", out)
        self.assertIn("環間 5 秒", out)        # /pulse 覆蓋＝迴圈轉速（秒）
        self.assertIn("Gate 2", out)

    def test_status_shows_entropy(self):
        from datetime import datetime, timezone
        from zoneinfo import ZoneInfo
        from telegram_monitor import analyzer
        cfg = SimpleNamespace(selfstate_sensitivity=2.0, lifeloop_wait_secs=3.0)
        st = SimpleNamespace(sensitivity_override=None, pulse_override=None, self_state=None,
                             vitality={"alive": True, "pulse": 5, "healthy_streak": 5, "last_lap_ms": 10,
                                       "uptime_s": 60, "k_adj": 0.0, "S": 0.7, "charge": 0.6, "hunger": 0.3,
                                       "laps_since_fresh": 4})
        snap = analyzer.analyze({"records": [], "contexts": [], "journeys": [], "explorations": [], "meta": {}},
                                datetime(2026, 6, 16, tzinfo=timezone.utc), ZoneInfo("Asia/Taipei"))
        out = monitor._status_text(st, cfg, snap)
        self.assertIn("內在熵 S", out)
        self.assertIn("0.7", out)


class CommandReplyTest(unittest.TestCase):
    def test_start_is_deterministic(self):
        from datetime import datetime, timezone
        from zoneinfo import ZoneInfo
        from telegram_monitor import analyzer
        snap = analyzer.analyze({"records": [], "contexts": [], "journeys": [],
                                 "explorations": [], "meta": {}},
                                datetime(2026, 6, 15, tzinfo=timezone.utc), ZoneInfo("Asia/Taipei"))
        a = monitor._command_reply(snap)
        b = monitor._command_reply(snap)
        self.assertEqual(a, b)        # 每次一致（不像 LLM 每次不同）
        self.assertIn("我在", a)


if __name__ == "__main__":
    unittest.main()
