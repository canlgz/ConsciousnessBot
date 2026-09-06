"""🤝 §0.71 偏移增補：「然後時間到的時候再隔3分鐘給我一個貼圖」＝在**前約時間之後**再 N 分鐘增補一個動作
   （前約 13:47 叫我 → 貼圖 13:50）。target＝錨點 prior.target＋N（非 now＋N）。修截圖：被 §0.69 timeup 誤算
   成 now＋3＝13:20、提早 27 分亂發。"""

import os
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from telegram_monitor import monitor, selfstate, temporal
from telegram_monitor.state import State

TZ = ZoneInfo("Asia/Taipei")
NOW16 = datetime(2026, 7, 5, 5, 16, 0, tzinfo=timezone.utc)   # 13:16 台北
NOW17 = datetime(2026, 7, 5, 5, 17, 0, tzinfo=timezone.utc)   # 13:17
_TAUGHT = [{"file_id": "DUCK", "emoji": "🦆", "valence": "positive", "ts": 0},
           {"file_id": "DRAGON", "emoji": "🐉", "valence": "positive", "ts": 0}]


class FakeClient:
    def __init__(self):
        self.sent, self.stickers, self.dry_run = [], [], False

    def send(self, text):
        self.sent.append(text)
        return True

    def send_sticker(self, file_id):
        self.stickers.append(file_id)
        return True


def _coach():
    return SimpleNamespace(
        enabled=True, api_key="k", model="m",
        meter=SimpleNamespace(record=lambda *a, **k: None),
        voice_schedule_ack=lambda q, when, h, sticker_hint="": (
            f"好，{when.strftime('%H:%M') if when else '到時候'}我會再回應你。"),
        voice_promise_keep=lambda when, facts, h, promised="", late=False, feeling_ground="", **k: f"到點！{promised}")


def _cfg(**over):
    base = dict(dry_run=False, telegram_chat_id="", scheduled_promise_enabled=True,
                promise_emit_enabled=True, promise_sched_ttl_sec=21600, timezone="Asia/Taipei",
                notify_cooldown_min=30, promise_sticker_enabled=True, send_stickers=True,
                sticker_no_repeat_enabled=True, sticker_file_ids=[], continuation_promise_enabled=True,
                offset_augmentation_enabled=True)
    base.update(over)
    return SimpleNamespace(**base)


def _msg(text, when):
    return {"message": {"chat": {"id": 1}, "text": text, "date": when.timestamp()}}


_BOOM = SimpleNamespace(load_embedding_records=lambda *_: (_ for _ in ()).throw(AssertionError("不該報現況")))


def _hhmm(ts):
    return datetime.fromtimestamp(ts, timezone.utc).astimezone(TZ).strftime("%H:%M")


class MisparseGuardTest(unittest.TestCase):
    def test_offset_not_parsed_as_now_relative(self):
        # §0.71 Part A：「時間到再隔3分鐘」的偏移時距不被 timeup 當 now+3（截圖 13:20 根因）
        self.assertIsNone(temporal._timeup_epoch("然後時間到的時候再隔3分鐘給我一個貼圖", NOW17))

    def test_069_timeup_still_works(self):
        # §0.69 的合法後方時距（非偏移）仍解析
        e = temporal._timeup_epoch("時間到叫我，我大概20分鐘", NOW17)
        self.assertAlmostEqual((e - NOW17.timestamp()) / 60, 20, delta=0.1)
        e2 = temporal._timeup_epoch("等你5分鐘時間到回應我", NOW17)
        self.assertAlmostEqual((e2 - NOW17.timestamp()) / 60, 5, delta=0.1)


class OffsetDetectorTest(unittest.TestCase):
    def test_positives(self):
        for t, secs in [("然後時間到的時候再隔 3 分鐘給我一個貼圖", 180), ("時間到再過5分鐘叫我", 300),
                        ("再隔十分鐘給我貼圖", 600), ("然後再隔3分鐘回應我", 180)]:
            self.assertTrue(selfstate.is_offset_augmentation(t), t)
            self.assertEqual(selfstate.offset_augmentation_secs(t), secs, t)

    def test_negatives(self):
        for t in ["等你5分鐘時間到回應我", "再10分鐘", "時間到叫我", "給我一個貼圖",
                  "再隔3分鐘就好", "10分鐘後叫我"]:
            self.assertFalse(selfstate.is_offset_augmentation(t), t)

    def test_false_positive_guards_self_review(self):
        # 自查：「不過3分鐘」（不過＝但是，含「過」）、使用者自己的計畫（我再過10分鐘就到了/再過5分鐘我睡覺）、
        # 隔壁（隔後非時距）、過去敘述（過了3分鐘我就走）→ 全不收
        for t in ["不過3分鐘給我貼圖", "我再過10分鐘就到了", "再過5分鐘我睡覺",
                  "隔壁10分鐘後叫我", "過了3分鐘我就走", "又隔了好久"]:
            self.assertFalse(selfstate.is_offset_augmentation(t), t)

    def test_review_subject_and_third_party_guards(self):
        # 審查 HIGH：句首「我」使用者自己的計畫＋第三方（室友會/打給朋友/傳給我媽）→ 不收（免幻影）
        for t in ["我再過10分鐘打給我的朋友", "再過10分鐘我室友會叫我",
                  "我再隔10分鐘就跟我老闆開會", "我再過5分鐘傳訊息給我媽"]:
            self.assertFalse(selfstate.is_offset_augmentation(t), t)

    def test_review_no_now_relative_leak(self):
        # 審查 MED：偏移標記表 A⊇B——任何 之後過/隔/過 形的後方時距都不被 timeup 當 now+N（不留 13:20-style 漏洞）
        for t in ["時間到之後過3分鐘叫我", "時間到隔3分鐘給我貼圖", "時間到過5分鐘叫我"]:
            self.assertIsNone(temporal._timeup_epoch(t.replace(" ", ""), NOW17), t)


class OffsetE2ETest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _state(self, stickers=None):
        s = State(os.path.join(self.tmp, "s.json"))
        s.owner_folder_id = "F"
        if stickers is not None:
            s.known_sticker_ids = stickers
        return s

    def test_screenshot_offset_is_prior_plus_n(self):
        # 截圖劇本：13:47 叫我（正確）→「再隔3分鐘給貼圖」→ 貼圖在 13:50（＝13:47+3），不是 13:20（now+3）
        st = self._state(list(_TAUGHT))
        monitor.handle_message(_msg("等一下 1:47 可以叫我嗎？我有事要告訴你", NOW16),
                               _coach(), _BOOM, {"meta": {}}, None, st, FakeClient(), _cfg(), TZ)
        call = [p for p in st.scheduled_promises if not p.get("fulfilled")][0]
        self.assertEqual(_hhmm(call["target_ts"]), "13:47")
        cl = FakeClient()
        monitor.handle_message(_msg("然後時間到的時候再隔 3 分鐘給我一個貼圖", NOW17),
                               _coach(), _BOOM, {"meta": {}}, None, st, cl, _cfg(), TZ)
        pend = sorted([p for p in st.scheduled_promises if not p.get("fulfilled")], key=lambda p: p["target_ts"])
        self.assertEqual(len(pend), 2)                              # 錨點 13:47 + 增補 13:50（兩筆各發）
        self.assertEqual(_hhmm(pend[0]["target_ts"]), "13:47")
        self.assertEqual(_hhmm(pend[1]["target_ts"]), "13:50")     # 關鍵：13:50 非 13:20
        self.assertEqual(pend[1]["behavior"], "送他一張貼圖")
        self.assertTrue(pend[1].get("wants_sticker"))
        self.assertEqual(pend[1].get("offset_of"), round(pend[0]["target_ts"]))

    def test_no_anchor_does_not_capture(self):
        # 無可當錨的未兌現前約 → 偏移無所指 → 不搶
        st = self._state([])
        handled = monitor._maybe_offset_augmentation(FakeClient(), st, _cfg(), _coach(),
                                                     "然後時間到的時候再隔3分鐘給我一個貼圖", NOW17, TZ, NOW17.timestamp())
        self.assertFalse(handled)

    def test_stale_anchor_not_used(self):
        # 錨點的 target 早該發（>grace 陳舊）→ 不當錨（那約定該自己先發、不是被偏移引用）
        st = self._state([])
        st.scheduled_promises = [{"target_ts": NOW17.timestamp() - 600, "made_ts": NOW17.timestamp() - 1200,
                                  "fulfilled": False, "behavior": "叫他起床"}]
        handled = monitor._maybe_offset_augmentation(FakeClient(), st, _cfg(), _coach(),
                                                     "再隔3分鐘給我貼圖", NOW17, TZ, NOW17.timestamp())
        self.assertFalse(handled)

    def test_flag_off_no_offset(self):
        st = self._state(list(_TAUGHT))
        st.scheduled_promises = [{"target_ts": NOW17.timestamp() + 1800, "made_ts": NOW17.timestamp(),
                                  "fulfilled": False, "behavior": ""}]
        handled = monitor._maybe_offset_augmentation(FakeClient(), st, _cfg(offset_augmentation_enabled=False),
                                                     _coach(), "再隔3分鐘給我貼圖", NOW17, TZ, NOW17.timestamp())
        self.assertFalse(handled)


if __name__ == "__main__":
    unittest.main()
