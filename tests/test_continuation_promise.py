"""🤝 §0.70 延續性約定：第一次約定成立後，第二次用極簡續約語詞（「再10分鐘」「延長10分鐘」）→ 繼承最近一筆
   排程承諾（behavior/wants_sticker）、new target＝now＋新時距 重新入帳（否則 route=fact_or_chat、bot 只 LLM
   空口答應、到點不觸發）。＋送真貼圖守約時文字別再吐 emoji 假裝（✨）。"""

import os
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from telegram_monitor import monitor, persona, selfstate
from telegram_monitor.state import State

NOW = datetime(2026, 7, 5, 3, 36, 0, tzinfo=timezone.utc)   # 11:36 台北
TZ = ZoneInfo("Asia/Taipei")
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
        voice_promise_keep=lambda when, facts, h, promised="", late=False, feeling_ground="", **k: f"時間到了！{promised}")


def _cfg(**over):
    base = dict(dry_run=False, telegram_chat_id="", scheduled_promise_enabled=True,
                promise_emit_enabled=True, promise_sched_ttl_sec=21600, timezone="Asia/Taipei",
                notify_cooldown_min=30, promise_sticker_enabled=True, send_stickers=True,
                sticker_no_repeat_enabled=True, sticker_file_ids=[], continuation_promise_enabled=True)
    base.update(over)
    return SimpleNamespace(**base)


def _msg(text, when=NOW):
    return {"message": {"chat": {"id": 1}, "text": text, "date": when.timestamp()}}


_BOOM = SimpleNamespace(load_embedding_records=lambda *_: (_ for _ in ()).throw(AssertionError("不該報現況")))


class ContinuationDetectorTest(unittest.TestCase):
    def test_positives(self):
        for t, secs in [("再 10 分鐘", 600), ("再10分鐘", 600), ("再等我10分鐘", 600),
                        ("再給我5分鐘", 300), ("等你10分鐘", 600),
                        ("時間改10分鐘", 600), ("延長10分鐘", 600), ("再半小時", 1800), ("那再5分鐘吧", 300)]:
            self.assertTrue(selfstate.is_continuation_duration(t), t)
            self.assertEqual(selfstate.continuation_duration_secs(t), secs, t)

    def test_negatives(self):
        # 有別的內容、已是完整排程/暫離形、無時距 → 不是純續約
        for t in ["再10分鐘我要去開會", "等你5分鐘時間到回應我", "我先去讀經20分鐘",
                  "10分鐘後叫我", "你好嗎", "等一下", "再說吧", "我愛你"]:
            self.assertFalse(selfstate.is_continuation_duration(t), t)

    def test_bare_duration_needs_lead_word(self):
        # 審查 MED-2：純裸時距（無 再/延長/改… 續約意圖詞）太曖昧（可能在答別的問題）→ 不收
        for t in ["10分鐘", "半小時", "約10分鐘", "就10分鐘"]:
            self.assertFalse(selfstate.is_continuation_duration(t), t)


class ContinuationE2ETest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _state(self, stickers=None, prior=None):
        s = State(os.path.join(self.tmp, "s.json"))
        s.owner_folder_id = "F"
        if stickers is not None:
            s.known_sticker_ids = stickers
        if prior is not None:
            s.scheduled_promises = prior
        return s

    def test_continuation_inherits_prior_and_rearms(self):
        # 先前約定：回應＋貼圖（已完結）；「再10分鐘」→ 繼承、new target=now+10、真入帳
        prior = [{"target_ts": NOW.timestamp() - 300, "made_ts": NOW.timestamp() - 600,
                  "fulfilled": True, "fulfilled_ts": NOW.timestamp() - 300,
                  "behavior": "送他一張貼圖", "wants_sticker": True, "status": "fulfilled"}]
        st = self._state(list(_TAUGHT), prior)
        cl = FakeClient()
        monitor.handle_message(_msg("再10分鐘"), _coach(), _BOOM, {"meta": {}}, None, st, cl, _cfg(), TZ)
        pend = [p for p in st.scheduled_promises if not p.get("fulfilled")]
        self.assertEqual(len(pend), 1)
        p = pend[0]
        self.assertAlmostEqual(p["target_ts"] - NOW.timestamp(), 600, delta=1)   # now+10min
        self.assertEqual(p["behavior"], "送他一張貼圖")                            # 繼承前約行為
        self.assertTrue(p.get("wants_sticker"))                                   # 繼承送貼圖意圖
        self.assertTrue(p.get("continuation"))
        self.assertTrue(cl.sent)                                                  # 有答應（非沉默）
        # 到點真的觸發（送真貼圖，DRAGON≠上次 DUCK 的多樣化不強制、但至少送出一張）
        fire = NOW + timedelta(minutes=10, seconds=30)
        cl2 = FakeClient()
        monitor._promise_emit(cl2, st, _cfg(), _coach(), fire.astimezone(timezone.utc))
        self.assertEqual(len(cl2.stickers), 1)

    def test_no_prior_does_not_capture(self):
        # 無先前約定 →「再10分鐘」無所指 → 不搶（落一般聊天）；handled=False
        st = self._state([])
        cl = FakeClient()
        handled = monitor._maybe_continuation_promise(cl, st, _cfg(), _coach(), "再10分鐘", NOW, TZ, NOW.timestamp())
        self.assertFalse(handled)
        self.assertFalse(getattr(st, "scheduled_promises", None))

    def test_prior_out_of_window_not_inherited(self):
        # 先前約定太舊（>1h）→ 不繼承
        prior = [{"target_ts": NOW.timestamp() - 9000, "made_ts": NOW.timestamp() - 9000,
                  "fulfilled": True, "fulfilled_ts": NOW.timestamp() - 9000, "behavior": "送他一張貼圖"}]
        st = self._state([], prior)
        handled = monitor._maybe_continuation_promise(FakeClient(), st, _cfg(), _coach(), "再10分鐘",
                                                      NOW, TZ, NOW.timestamp())
        self.assertFalse(handled)

    def test_continuation_no_sticker_capability_honest(self):
        # 前約想要貼圖，但此刻手邊沒貼圖 → 不續標 wants_sticker（兌現不假裝）
        prior = [{"target_ts": NOW.timestamp() - 300, "made_ts": NOW.timestamp() - 600,
                  "fulfilled": True, "fulfilled_ts": NOW.timestamp() - 300,
                  "behavior": "送他一張貼圖", "wants_sticker": True}]
        st = self._state([], prior)   # 沒貼圖
        cl = FakeClient()
        monitor.handle_message(_msg("再10分鐘"), _coach(), _BOOM, {"meta": {}}, None, st, cl, _cfg(), TZ)
        p = [q for q in st.scheduled_promises if not q.get("fulfilled")][0]
        self.assertFalse(p.get("wants_sticker"))

    def test_continuation_of_continuation_supersedes(self):
        # 審查 MED-1：「再10分鐘」後又「再5分鐘」＝改成 5 分（取代），不是疊兩筆到點連發兩次
        prior = [{"target_ts": NOW.timestamp() - 300, "made_ts": NOW.timestamp() - 600,
                  "fulfilled": True, "fulfilled_ts": NOW.timestamp() - 300, "behavior": "送他一張貼圖"}]
        st = self._state(list(_TAUGHT), prior)
        monitor.handle_message(_msg("再10分鐘"), _coach(), _BOOM, {"meta": {}}, None, st, FakeClient(), _cfg(), TZ)
        n2 = NOW + timedelta(minutes=2)
        monitor.handle_message(_msg("再5分鐘", n2), _coach(), _BOOM, {"meta": {}}, None, st, FakeClient(), _cfg(), TZ)
        pend = [p for p in st.scheduled_promises if not p.get("fulfilled")]
        self.assertEqual(len(pend), 1)                          # 取代、不疊
        self.assertAlmostEqual(pend[0]["target_ts"] - n2.timestamp(), 300, delta=1)   # 改成 5 分

    def test_long_horizon_pending_inheritable(self):
        # 審查 LOW-2：還活著的 pending（>1h 前排的長程約定）也能被「再10分鐘」延（未兌現＝活著、不受 window 限）
        prior = [{"target_ts": NOW.timestamp() + 1800, "made_ts": NOW.timestamp() - 4200,
                  "fulfilled": False, "behavior": "叫他起床"}]
        st = self._state([], prior)
        handled = monitor._maybe_continuation_promise(FakeClient(), st, _cfg(), _coach(), "再10分鐘",
                                                      NOW, TZ, NOW.timestamp())
        self.assertTrue(handled)

    def test_flag_off_no_continuation(self):
        prior = [{"target_ts": NOW.timestamp() - 300, "made_ts": NOW.timestamp() - 600,
                  "fulfilled": True, "fulfilled_ts": NOW.timestamp() - 300, "behavior": "送他一張貼圖"}]
        st = self._state([], prior)
        handled = monitor._maybe_continuation_promise(FakeClient(), st, _cfg(continuation_promise_enabled=False),
                                                      _coach(), "再10分鐘", NOW, TZ, NOW.timestamp())
        self.assertFalse(handled)


class StickerSentNoFakeTest(unittest.TestCase):
    """🎴 §0.70：送真貼圖守約時，文字別再放 emoji 假裝（截圖：送了真鴨子貼圖、文字卻又吐「✨」）。"""

    def test_prompt_has_anti_fake_note(self):
        out = persona.promise_keep_user("11:31", "〔此刻 11:32〕", promised="送他一張貼圖", sticker_sent=True)
        self.assertIn("系統已經幫你", out)
        self.assertIn("絕對不要", out)
        self.assertIn("真貼圖是另外那張圖", out)

    def test_off_byte_identical(self):
        out = persona.promise_keep_user("11:31", "〔此刻 11:32〕", promised="送他一張貼圖", sticker_sent=False)
        self.assertNotIn("系統已經幫你", out)

    def test_emit_passes_sticker_sent(self):
        # 送出真貼圖時 sticker_sent=True 傳進 voice_promise_keep
        seen = {}

        def vpk(when, facts, h, promised="", late=False, feeling_ground="", sticker_sent=False, sticker_wanted=False, sticker_desc="", change_ground=""):
            seen["sticker_sent"] = sticker_sent
            seen["sticker_wanted"] = sticker_wanted
            return "這是說好的貼圖，收下 :)"
        coach = _coach()
        coach.voice_promise_keep = vpk
        tmp = tempfile.mkdtemp()
        st = State(os.path.join(tmp, "s.json"))
        st.owner_folder_id = "F"
        st.known_sticker_ids = list(_TAUGHT)
        st.scheduled_promises = [{"target_ts": NOW.timestamp() - 30, "made_ts": NOW.timestamp() - 300,
                                  "fulfilled": False, "behavior": "送他一張貼圖", "wants_sticker": True,
                                  "status": "pending"}]
        monitor._promise_emit(FakeClient(), st, _cfg(), coach, NOW)
        self.assertTrue(seen.get("sticker_sent"))   # 真貼圖已送→告知 voice 別 emoji 假裝


if __name__ == "__main__":
    unittest.main()
