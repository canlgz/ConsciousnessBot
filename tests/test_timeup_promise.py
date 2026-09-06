"""🤝 §0.69「N分鐘…時間到…做某事」到點觸發承諾——截圖三例（大概要20分鐘時間到請跟我聊天／這次大概10分鐘
   時間到請跟我確認你可以回應我／等你5分鐘時間到回應我）全落 fact_or_chat＝沒入帳、bot 空口答應、到點不觸發。
   修：時間到＝到點觸發詞＋前方最近時距＝now＋時距；動作補 回應我/聊天；temporal 同步解析。"""

import os
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from telegram_monitor import intent, monitor, referent, selfstate, temporal
from telegram_monitor.state import State

NOW = datetime(2026, 7, 5, 2, 10, 0, tzinfo=timezone.utc)   # 10:10 台北（截圖時段）
TZ = ZoneInfo("Asia/Taipei")

MSG1 = "我先去讀經了，大概要 20 分鐘，時間到的時候，請跟我了聊天，並且給我一個溫暖的貼圖"
MSG2 = "我剛等你 20 分鐘都沒收到你主動回應，這次大概 10 分鐘，時間到的時候，請跟我確認你可以回應我，並且給我一個溫暖的貼圖"
MSG3 = "等你 5 分鐘，時間到回應我"


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
        voice_schedule_ack=lambda q, when, h, sticker_hint="": "好，記住了，時間到我回應你。",
        voice_promise_keep=lambda when, facts, h, promised="", late=False, feeling_ground="", **k: f"時間到了！{promised}")


def _cfg(**over):
    base = dict(dry_run=False, telegram_chat_id="", scheduled_promise_enabled=True,
                promise_emit_enabled=True, promise_sched_ttl_sec=21600, timezone="Asia/Taipei",
                notify_cooldown_min=30, promise_sticker_enabled=True, send_stickers=True,
                sticker_no_repeat_enabled=True, sticker_file_ids=[])
    base.update(over)
    return SimpleNamespace(**base)


_BOOM = SimpleNamespace(load_embedding_records=lambda *_: (_ for _ in ()).throw(AssertionError("不該報現況")))


def _msg(text, when=NOW):
    return {"message": {"chat": {"id": 1}, "text": text, "date": when.timestamp()}}


class TimeupDetectorTest(unittest.TestCase):
    def test_screenshots_route_scheduled(self):
        ref = referent.Referent()
        for m in (MSG1, MSG2, MSG3):
            self.assertTrue(selfstate.is_scheduled_promise_request(m), m)
            self.assertEqual(intent.resolve(m, ref, None, cfg=_cfg()).kind, "scheduled_promise", m)

    def test_target_is_duration_before_timeup(self):
        # msg2：「我剛等你20分鐘…這次大概10分鐘，時間到」→ 取時間到前**最近**的 10 分鐘（非過去抱怨的 20）
        for m, want in ((MSG1, 20), (MSG2, 10), (MSG3, 5)):
            eps = temporal.all_clock_epochs(m, NOW, TZ)
            self.assertEqual(len(eps), 1, m)
            self.assertAlmostEqual((eps[0] - NOW.timestamp()) / 60, want, delta=0.1, msg=m)

    def test_more_timeup_forms(self):
        for m in ["煮個麵大概5分鐘，時間到了叫我", "休息10分鐘，時間到提醒我",
                  "等我半小時，時間到跟我說一聲", "我去洗澡，大概15分鐘，時間一到就回應我"]:
            self.assertTrue(selfstate.is_scheduled_promise_request(m), m)

    def test_guards_reject(self):
        # 無時距的時間到、過去抱怨、失約質問、回想語氣 → 不收（讓給 ledger/一般聊天）
        for m in ["時間到了我就走", "時間到了嗎", "時間到了記得關火", "會議時間到了", "現在時間到了沒",
                  "你為什麼沒有5分鐘時間到叫我", "你不是說5分鐘時間到就回我嗎", "我等了20分鐘時間到你都沒回"]:
            self.assertFalse(selfstate.is_scheduled_promise_request(m), m)

    def test_phantom_guards_self_review(self):
        # 自查：過去敘述（上次/那次/每次…才回我）＋裸「聊天」誤收使用者自己的計畫 → 全擋
        for m in ["上次時間到你20分鐘才回我", "那次20分鐘時間到你才回", "每次時間到我都等20分鐘",
                  "時間到了嗎？我等20分鐘了", "會議20分鐘，時間到了大家就走",
                  "8點聊天", "晚上8點我跟朋友聊天", "我想跟你聊聊", "改天回應我"]:
            self.assertFalse(selfstate.is_scheduled_promise_request(m), m)

    def test_directed_chat_still_captures(self):
        # 指向我的聊天仍收（別因收緊裸「聊天」而漏真請求）
        self.assertTrue(selfstate.is_scheduled_promise_request("5分鐘後跟我聊天"))
        # 道歉後重約（剛…都沒…這次…請）不被過去敘述誤擋（截圖 msg2 家族）
        self.assertTrue(selfstate.is_scheduled_promise_request(
            "上次沒做到，這次大概10分鐘，時間到請跟我確認你可以回應我"))

    def test_review_whynot_failure_complaint_to_ledger(self):
        # 審查 HIGH：失約質問（你怎麼沒有5分鐘時間到回應我/跟我聊）→ 讓給 ledger、不成幻影新承諾
        for t in ["你怎麼沒有5分鐘時間到回應我", "為什麼你都沒有5分鐘時間到回應我",
                  "你怎麼沒有10分鐘時間到回應我", "你怎麼沒有10分鐘時間到跟我聊"]:
            self.assertFalse(selfstate.is_scheduled_promise_request(t), t)
            self.assertTrue(selfstate.is_promise_ledger_question(t), t)

    def test_review_user_self_promise_rejected(self):
        # 審查 MED：「我8點回應你」＝使用者自諾（回應 bot）→ 不該被 bot 排給自己
        for t in ["我答應8點回應你", "我8點的時候回應你", "我會在8點回應你"]:
            self.assertFalse(selfstate.is_scheduled_promise_request(t), t)

    def test_review_duration_after_trigger_parses(self):
        # 審查 MED：時距在觸發詞**後**（時間到叫我，我大概20分鐘）也要解析出 epoch＝不空承諾
        for t, want in (("時間到叫我，我大概20分鐘", 20), ("時間到請叫我，大概10分鐘", 10)):
            eps = temporal.all_clock_epochs(t, NOW, TZ)
            self.assertEqual(len(eps), 1, t)
            self.assertAlmostEqual((eps[0] - NOW.timestamp()) / 60, want, delta=0.1, msg=t)

    def test_review_behavior_key_and_compound(self):
        # 審查 LOW：跟我聊/和我聊 有 behavior 標籤；複合「回應我並叫我起床」讓更具體的鬧鐘先命中
        self.assertEqual(selfstate.extract_promise_behavior("5分鐘後跟我聊"), "跟他聊聊")
        self.assertEqual(selfstate.extract_promise_behavior("8點回應我並叫我起床"), "叫他起床")

    def test_flag_off_reverts(self):
        os.environ["SCHED_TIMEUP"] = "0"
        try:
            self.assertFalse(selfstate.is_scheduled_promise_request(MSG3))
            self.assertEqual(temporal.all_clock_epochs(MSG3, NOW, TZ), [])
        finally:
            os.environ.pop("SCHED_TIMEUP", None)

    def test_behavior_labels(self):
        self.assertEqual(selfstate.extract_promise_behavior(MSG1), "跟他聊聊")
        self.assertEqual(selfstate.extract_promise_behavior(MSG3), "主動傳訊息給他")


class TimeupE2ETest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _state(self, stickers=None):
        s = State(os.path.join(self.tmp, "s.json"))
        s.owner_folder_id = "F"
        if stickers is not None:
            s.known_sticker_ids = stickers
        return s

    def test_capture_and_fire_msg3(self):
        st = self._state([])
        cl = FakeClient()
        monitor.handle_message(_msg(MSG3), _coach(), _BOOM, {"meta": {}}, None, st, cl, _cfg(), TZ)
        self.assertEqual(len(st.scheduled_promises), 1)
        p = st.scheduled_promises[0]
        self.assertAlmostEqual(p["target_ts"] - NOW.timestamp(), 300, delta=1)   # now+5min（真入帳、非空口）
        # 到點（09:15）→ _promise_emit 真的主動觸發
        fire = NOW + timedelta(minutes=5, seconds=30)
        cl2 = FakeClient()
        monitor._promise_emit(cl2, st, _cfg(), _coach(), fire.astimezone(timezone.utc))
        self.assertTrue(cl2.sent)                                                 # 主動回應真的發了
        self.assertTrue(st.scheduled_promises[0]["fulfilled"])

    def test_capture_and_fire_msg1_with_sticker(self):
        st = self._state([{"file_id": "DUCK", "emoji": "🦆", "valence": "positive", "ts": 0}])
        cl = FakeClient()
        monitor.handle_message(_msg(MSG1), _coach(), _BOOM, {"meta": {}}, None, st, cl, _cfg(), TZ)
        p = st.scheduled_promises[0]
        self.assertAlmostEqual((p["target_ts"] - NOW.timestamp()) / 60, 20, delta=0.1)
        self.assertTrue(p.get("wants_sticker"))                                   # 有溫暖貼圖＝送貼圖承諾
        fire = NOW + timedelta(minutes=20, seconds=30)
        cl2 = FakeClient()
        monitor._promise_emit(cl2, st, _cfg(), _coach(), fire.astimezone(timezone.utc))
        self.assertEqual(cl2.stickers, ["DUCK"])                                  # 到點真的送溫暖貼圖


if __name__ == "__main__":
    unittest.main()
