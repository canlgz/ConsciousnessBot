"""🎴 §0.68 貼圖 vs emoji 認知 + 真的會送 telegram sticker + 教學捕捉：
   ① 貼圖/教學句不再被 is_attachment_request 的裸「圖」誤路由記寫附件；
   ② 承諾送貼圖 → 兌現時真的 send_sticker（非 emoji 假裝）；手邊沒貼圖＝誠實拒（§0.64）；
   ③ 貼圖概念守則（sticker≠emoji、別用符號假裝）。"""

import os
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from telegram_monitor import monitor, persona, reaction, selfstate
from telegram_monitor.state import State

NOW = datetime(2026, 7, 5, 1, 10, 0, tzinfo=timezone.utc)   # 09:10 台北（截圖時段）
TZ = ZoneInfo("Asia/Taipei")


class FakeClient:
    def __init__(self, sticker_ok=True):
        self.sent, self.stickers, self.dry_run = [], [], False
        self._sticker_ok = sticker_ok

    def send(self, text):
        self.sent.append(text)
        return True

    def send_sticker(self, file_id):
        self.stickers.append(file_id)
        return self._sticker_ok


def _coach():
    return SimpleNamespace(
        enabled=True, api_key="k", model="m",
        meter=SimpleNamespace(record=lambda *a, **k: None),
        voice_schedule_ack=lambda q, when, h, sticker_hint="": (
            "好，我記住了，時間到我會叫你" + ("，並且送你一張貼圖" if not sticker_hint else "。貼圖我還沒有") + "。"),
        voice_promise_keep=lambda when, facts, h, promised="", late=False, feeling_ground="", **k: f"時間到了！{promised}")


def _cfg(**over):
    base = dict(dry_run=False, telegram_chat_id="", scheduled_promise_enabled=True,
                promise_emit_enabled=True, promise_sched_ttl_sec=21600, timezone="Asia/Taipei",
                notify_cooldown_min=30, promise_sticker_enabled=True, send_stickers=True,
                sticker_no_repeat_enabled=True, sticker_file_ids=[])
    base.update(over)
    return SimpleNamespace(**base)


_BOOM = SimpleNamespace(load_embedding_records=lambda *_: (_ for _ in ()).throw(AssertionError("不該報現況")))


def _state(tmp, stickers=None):
    s = State(os.path.join(tmp, "s.json"))
    s.owner_folder_id = "F"
    if stickers is not None:
        s.known_sticker_ids = stickers
    return s


def _msg(text, when=NOW):
    return {"message": {"chat": {"id": 1}, "text": text, "date": when.timestamp()}}


_TAUGHT = [{"file_id": "DUCK", "emoji": "🦆", "valence": "positive", "ts": 0},
           {"file_id": "DRAGON", "emoji": "🐉", "valence": "positive", "ts": 0}]


class AttachmentGuardTest(unittest.TestCase):
    """貼圖/貼紙/sticker 與『我教你…』教學句不再被裸「圖/檔」誤判成要調記寫附件。"""

    def test_sticker_teaching_not_attachment(self):
        for q in ["我教你以後遇到這種狀況要送貼圖的時候都是這一種大的貼圖",
                  "我教你以後遇到這種狀況要送出貼圖的時候要選擇 telegram 的大 sticker",
                  "時間到叫我並且給我一個大的貼圖", "以後送貼圖用這種", "送我一張貼紙",
                  "等我20分鐘，時間到叫我，並且提供大的 telegram sticker"]:
            self.assertFalse(selfstate.is_attachment_request(q), q)

    def test_real_attachment_still_routes(self):
        for q in ["把我上次那張照片傳給我", "找我之前記下的圖片", "那個 pdf 檔傳給我看",
                  "我要看那張截圖", "幫我調那個語音檔", "傳那張圖給我看"]:
            self.assertTrue(selfstate.is_attachment_request(q), q)

    def test_near_term_fetch_not_over_blocked(self):
        # 審查 confirmed MED：「記得要/這種狀況」是近期祈使、非未來教學 → 別誤擋真附件請求
        for q in ["記得要把那張截圖傳給我", "這種狀況你把pdf給我", "這種時候把圖片給我"]:
            self.assertTrue(selfstate.is_attachment_request(q), q)

    def test_compound_behavior_keeps_primary(self):
        # 審查 LOW：複合承諾主行為（叫起床）不被貼圖標籤搶——貼圖送出由 wants_sticker 另行驅動
        self.assertEqual(selfstate.extract_promise_behavior("8點叫我起床順便傳貼圖"), "叫他起床")
        self.assertEqual(selfstate.extract_promise_behavior("20分鐘後給我一個貼圖"), "送他一張貼圖")

    def test_flag_off_restores_greedy(self):
        os.environ["ATTACHMENT_STICKER_GUARD"] = "0"
        try:
            # 旗標關＝裸「圖」照舊命中（貼圖也被當附件）＝逐位元同現狀
            self.assertTrue(selfstate.is_attachment_request("給我一個大的貼圖"))
        finally:
            os.environ.pop("ATTACHMENT_STICKER_GUARD", None)


class WantsStickerDetectorTest(unittest.TestCase):
    def test_positives(self):
        for q in ["提供大的 telegram sticker", "等我20分鐘，時間到叫我，並且提供大的 telegram sticker",
                  "給我一個大的貼圖", "時間到叫我並送我貼圖", "送我一張貼紙", "準備一張貼圖給我",
                  "傳貼圖給我", "把貼圖給我",
                  # 審查 MED：旗艦講法「一張＋telegram＋中文貼圖」（telegram 修飾語別吃爆縫隙）
                  "送你一張大的 telegram 貼圖", "送我一張大的 telegram 貼圖", "送你一張 tg 貼圖"]:
            self.assertTrue(selfstate.promise_wants_sticker(q), q)

    def test_negatives(self):
        for q in ["你剛傳的貼圖好可愛", "我發現這貼圖很讚", "我們來聊貼圖設計",
                  "我回想起那張貼圖", "這貼圖是誰做的", "我傳了貼圖給我媽", "8點叫我起床"]:
            self.assertFalse(selfstate.promise_wants_sticker(q), q)

    def test_negation_and_third_party_rejected(self):
        # 審查 confirmed HIGH：否定（別/不要送貼圖）＝相反意思；第三人收件（給我弟/給客戶）＝不是送給我 → 不收
        for q in ["8點提醒我不要送貼圖", "8點提醒我別送貼圖", "不要傳貼圖給我", "不准送貼圖",
                  "8點提醒我傳貼圖給我弟", "提醒我8點別傳貼圖給客戶", "我傳貼圖給我弟", "傳給我妹一張貼圖"]:
            self.assertFalse(selfstate.promise_wants_sticker(q), q)

    def test_double_negative_still_wants(self):
        # 「別忘了送貼圖給我」＝雙重否定＝要送（別 直接接 忘、非給-動詞）→ 仍收
        self.assertTrue(selfstate.promise_wants_sticker("8點提醒我別忘了送貼圖給我"))
        self.assertTrue(selfstate.promise_wants_sticker("記得別忘記傳貼圖給我"))

    def test_behavior_label(self):
        self.assertEqual(selfstate.extract_promise_behavior("等我20分鐘，時間到叫我，並且提供大的 telegram sticker"),
                         "送他一張貼圖")
        self.assertEqual(selfstate.extract_promise_behavior("20分鐘後給我一個貼圖"), "送他一張貼圖")
        self.assertEqual(selfstate.extract_promise_behavior("8點叫我起床"), "叫他起床")   # 非貼圖不受影響


class ConceptHintTest(unittest.TestCase):
    def test_have_sendable(self):
        h = persona.sticker_concept_hint(have_sendable=True)
        self.assertIn("sticker", h.lower())
        self.assertIn("emoji", h.lower())
        self.assertIn("有", h)

    def test_no_sendable(self):
        h = persona.sticker_concept_hint(have_sendable=False)
        self.assertIn("沒有", h)
        self.assertIn("誠實", h)
        self.assertIn("不要", h)   # 別用 emoji 湊數假裝

    def test_have_sendable_no_over_promise(self):
        # 審查 confirmed MED：一般聊天沒把 LLM 措辭接到真送出 → 別叫模型「你把話講好、系統會送」
        h = persona.sticker_concept_hint(have_sendable=True)
        self.assertNotIn("你把話講好", h)
        self.assertIn("這則回覆本身不會自動附貼圖", h)   # 明說本則不自動送

    def test_monitor_hint_gate(self):
        tmp = tempfile.mkdtemp()
        st = _state(tmp, _TAUGHT)
        self.assertTrue(monitor._sticker_concept_hint(st, _cfg(), "你可以送我貼圖嗎"))
        self.assertEqual(monitor._sticker_concept_hint(st, _cfg(), "今天天氣真好"), "")   # 沒提貼圖
        self.assertEqual(monitor._sticker_concept_hint(st, _cfg(sticker_concept_guard_enabled=False),
                                                       "送我貼圖"), "")                    # 旗標關


class SendableHelpersTest(unittest.TestCase):
    def test_has_sendable(self):
        tmp = tempfile.mkdtemp()
        self.assertTrue(monitor._has_sendable_sticker(_state(tmp, _TAUGHT), _cfg()))
        self.assertFalse(monitor._has_sendable_sticker(_state(tmp, []), _cfg()))
        # 只有負向貼圖（求救圖）→ sendable 排負向 → 不算「可回送」
        neg = [{"file_id": "CRY", "emoji": "😭", "valence": "negative", "ts": 0}]
        self.assertFalse(monitor._has_sendable_sticker(_state(tmp, neg), _cfg()))

    def test_send_real_sticker_picks_and_records(self):
        tmp = tempfile.mkdtemp()
        st = _state(tmp, _TAUGHT)
        cl = FakeClient()
        self.assertTrue(monitor._send_real_sticker(cl, st, _cfg()))
        self.assertEqual(len(cl.stickers), 1)
        self.assertIn(cl.stickers[0], ("DUCK", "DRAGON"))
        self.assertEqual(st.last_sticker_id, cl.stickers[0])

    def test_send_real_sticker_none_when_empty(self):
        tmp = tempfile.mkdtemp()
        cl = FakeClient()
        self.assertFalse(monitor._send_real_sticker(cl, _state(tmp, []), _cfg()))
        self.assertEqual(cl.stickers, [])


class StickerPromiseE2ETest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_capture_with_sticker_and_fulfill_sends_real(self):
        st = _state(self.tmp, list(_TAUGHT))
        cl = FakeClient()
        monitor.handle_message(_msg("等我20分鐘，時間到叫我，並且提供大的 telegram sticker"),
                               _coach(), _BOOM, {"meta": {}}, None, st, cl, _cfg(), TZ)
        p = st.scheduled_promises[0]
        self.assertTrue(p.get("wants_sticker"))
        self.assertEqual(p.get("behavior"), "送他一張貼圖")
        # 兌現（09:30）→ 守約文字 + 真的送一張真貼圖
        later = NOW + timedelta(minutes=20, seconds=30)
        cl2 = FakeClient()
        monitor._promise_emit(cl2, st, _cfg(), _coach(), later.astimezone(timezone.utc))
        self.assertEqual(len(cl2.stickers), 1)             # 真貼圖！不是 emoji
        self.assertIn(cl2.stickers[0], ("DUCK", "DRAGON"))

    def test_no_sticker_honest_refusal_and_no_fake(self):
        st = _state(self.tmp, [])                          # 手邊沒有可送貼圖
        cl = FakeClient()
        monitor.handle_message(_msg("等我20分鐘，時間到叫我，並且提供大的 telegram sticker"),
                               _coach(), _BOOM, {"meta": {}}, None, st, cl, _cfg(), TZ)
        p = st.scheduled_promises[0]
        self.assertFalse(p.get("wants_sticker"))           # 沒能力＝不標＝兌現不假裝
        # ack 走了 sticker_hint 分支（誠實：貼圖還沒有）
        self.assertTrue(any("還沒有" in s for s in cl.sent))
        # 兌現時不送任何貼圖（不假裝）
        later = NOW + timedelta(minutes=20, seconds=30)
        cl2 = FakeClient()
        monitor._promise_emit(cl2, st, _cfg(), _coach(), later.astimezone(timezone.utc))
        self.assertEqual(cl2.stickers, [])

    def test_bridge_also_sends_real_sticker(self):
        st = _state(self.tmp, list(_TAUGHT))
        st.scheduled_promises = [{"target_ts": NOW.timestamp() - 120, "fulfilled": False,
                                  "behavior": "送他一張貼圖", "status": "pending", "wants_sticker": True}]
        cl = FakeClient()
        monitor._promise_reply_bridge(cl, st, _cfg(), _coach(), NOW, TZ)
        self.assertEqual(len(cl.stickers), 1)
        self.assertTrue(st.scheduled_promises[0]["fulfilled"])

    def test_capability_race_no_fake_claim(self):
        # 極少數競態：捕捉時有貼圖（標 wants_sticker），到點前貼圖池被清空 → 兌現**不宣稱送貼圖**、也不送（不說到做不到）
        st = _state(self.tmp, list(_TAUGHT))
        cl = FakeClient()
        monitor.handle_message(_msg("等我20分鐘，時間到叫我，並且提供大的 telegram sticker"),
                               _coach(), _BOOM, {"meta": {}}, None, st, cl, _cfg(), TZ)
        st.known_sticker_ids = []                          # 池清空
        later = NOW + timedelta(minutes=20, seconds=30)
        cl2 = FakeClient()
        monitor._promise_emit(cl2, st, _cfg(), _coach(), later.astimezone(timezone.utc))
        self.assertEqual(cl2.stickers, [])                 # 沒送
        self.assertNotIn("送他一張貼圖", "".join(cl2.sent))  # 文字也不宣稱送了貼圖

    def test_never_capable_no_claim_at_fulfillment(self):
        # 審查 confirmed MED：捕捉時就沒貼圖（wants_sticker 沒標，但 behavior 標籤仍是「送他一張貼圖」）
        # → 兌現時也**不宣稱送貼圖**、不送（behavior 標籤獨立於能力，靠兌現守則兜住）
        st = _state(self.tmp, [])
        cl = FakeClient()
        monitor.handle_message(_msg("等我20分鐘，時間到叫我，並且提供大的 telegram sticker"),
                               _coach(), _BOOM, {"meta": {}}, None, st, cl, _cfg(), TZ)
        p = st.scheduled_promises[0]
        self.assertFalse(p.get("wants_sticker"))
        self.assertEqual(p.get("behavior"), "送他一張貼圖")   # 標籤仍在（不看能力）
        later = NOW + timedelta(minutes=20, seconds=30)
        cl2 = FakeClient()
        monitor._promise_emit(cl2, st, _cfg(), _coach(), later.astimezone(timezone.utc))
        self.assertEqual(cl2.stickers, [])
        self.assertNotIn("送他一張貼圖", "".join(cl2.sent))    # 兌現守則兜住：不假裝

    def test_send_failure_no_claim(self):
        # 審查 confirmed MED：池裡有 file_id 但 send_sticker 失敗（失效 file_id/網路）→ 文字不宣稱送了貼圖
        st = _state(self.tmp, list(_TAUGHT))
        cl = FakeClient()
        monitor.handle_message(_msg("等我20分鐘，時間到叫我，並且提供大的 telegram sticker"),
                               _coach(), _BOOM, {"meta": {}}, None, st, cl, _cfg(), TZ)
        later = NOW + timedelta(minutes=20, seconds=30)
        cl2 = FakeClient(sticker_ok=False)                 # 送出失敗
        monitor._promise_emit(cl2, st, _cfg(), _coach(), later.astimezone(timezone.utc))
        self.assertEqual(len(cl2.stickers), 1)             # 有嘗試送一張（先送再宣稱）
        self.assertIn(cl2.stickers[0], ("DUCK", "DRAGON"))
        self.assertNotIn("送他一張貼圖", "".join(cl2.sent))  # 但送失敗→文字不宣稱

    def test_flag_off_no_wants_sticker(self):
        st = _state(self.tmp, list(_TAUGHT))
        cl = FakeClient()
        monitor.handle_message(_msg("等我20分鐘，時間到叫我，並且提供大的 telegram sticker"),
                               _coach(), _BOOM, {"meta": {}}, None, st, cl, _cfg(promise_sticker_enabled=False), TZ)
        p = st.scheduled_promises[0]
        self.assertFalse(p.get("wants_sticker"))           # 旗標關＝不特別處理＝同現狀


if __name__ == "__main__":
    unittest.main()
