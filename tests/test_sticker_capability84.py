"""🎴 §0.84 貼圖能力意識＋emoji 區分＋多樣化：bot 不再否認自己能送 telegram 貼圖、不拿 emoji 假裝、能多樣輪替、能當下應請求送。

三截圖：
- 8:00 承諾「跟我問候+特別的貼圖」到點兌現只吐「這個送給你：✨」＝拿 emoji 假裝貼圖（§0.68 旗艦 bug 復發，因「特別的貼圖」
  無給-動詞、promise_wants_sticker 漏收 → 沒標 wants_sticker → 沒觸發真送、也沒掛防假裝守則）。
- 被質疑時 bot **否認能力**：「我沒辦法直接送出 Telegram 的貼圖」「我只能用文字/符號」「我沒有那個選取或送出的介面或能力」
  ＝§0.80 反應式自我模型錯認（把『暫時沒存貨』說成『沒有能力』）。
- 使用者「開心的貼圖」→ bot「試著送」卻只吐「😄」＝當下請求沒有真送路徑。

修（旗標化、預設開、關＝逐位元同現狀）：
1. persona 自我模型：essence＋sticker_concept_hint＋sticker_send_reply 一律**肯定能送**、**絕不否認**、分清 emoji≠貼圖。
2. 偵測：promise_wants_sticker 補「還要有特別的貼圖」要-有框架（_STICKER_WANT_RE）；新增 is_sticker_send_request（當下請求）。
3. 兌現防假裝：promise_keep_user 想送但這次沒送出（sticker_wanted）也掛防 emoji 假裝守則（不只送成功時）。
4. 當下送圖：_maybe_sticker_send——有貨真送、無貨誠實說能送但還沒存到（STICKER_SEND_REQUEST）。
5. 多樣化：eligible_sticker_ids 避開最近 N 張（STICKER_ROTATE_WINDOW，=1 同 §0.59）。
"""

import os
import tempfile
import unittest
from types import SimpleNamespace

from telegram_monitor import monitor, reaction, persona, selfstate, config
from telegram_monitor.state import State


class FakeClient:
    def __init__(self):
        self.sent, self.stickers, self.dry_run = [], [], False

    def send(self, text):
        self.sent.append(text)
        return True

    def send_sticker(self, fid):
        self.stickers.append(fid)
        return True


def _cfg(**over):
    base = dict(sticker_send_request_enabled=True, sticker_rotate_window=3, sticker_no_repeat_enabled=True,
                sticker_file_ids=[], dry_run=False, send_stickers=True, scheduled_promise_enabled=True,
                promise_sticker_enabled=True)
    base.update(over)
    return SimpleNamespace(**base)


class RotationTest(unittest.TestCase):
    def test_window_avoids_recent_n(self):
        ids = ["a", "b", "c", "d"]
        self.assertEqual(reaction.eligible_sticker_ids(ids, ["a", "b", "c"], 3), ["d"])
        self.assertEqual(sorted(reaction.eligible_sticker_ids(ids, ["d"], 3)), ["a", "b", "c"])

    def test_relax_when_pool_le_window(self):
        # 池 ≤ window（候選全被避開）→ 至少避開最近一張、不至於送不出
        ids = ["a", "b"]
        self.assertEqual(reaction.eligible_sticker_ids(ids, ["a", "b"], 3), ["a"])   # 避最近一張 b → a

    def test_window1_byte_identity(self):
        # window=1＝§0.59 只排除最近一張（逐位元同現狀）
        ids = ["a", "b", "c", "d"]
        self.assertEqual(sorted(reaction.eligible_sticker_ids(ids, ["c"], 1)), ["a", "b", "d"])
        self.assertEqual(reaction.eligible_sticker_ids(["x"], ["x"], 3), ["x"])       # 單張照送

    def test_window0_no_exclusion(self):
        ids = ["a", "b", "c"]
        self.assertEqual(reaction.eligible_sticker_ids(ids, ["a", "b"], 0), ids)      # no_repeat 關＝不排除

    def test_positive_pool(self):
        known = [{"file_id": "p1", "valence": "positive"}, {"file_id": "n1", "valence": "negative"},
                 {"file_id": "m1", "valence": "neutral"}]
        self.assertEqual(reaction.positive_sticker_ids(known, ["cfg1"]), ["p1", "cfg1"])   # 只正向＋設定檔


class DetectTest(unittest.TestCase):
    def test_promise_wants_sticker_want_form(self):
        for t in ["8:00跟我問候，而且還要有特別的貼圖哦", "8點叫我起床，也要一張貼圖",
                  "10分鐘後想要一張可愛貼圖", "8點問候我還要有貼圖"]:
            self.assertTrue(selfstate.promise_wants_sticker(t), t)

    def test_promise_wants_sticker_still_excludes(self):
        for t in ["8點不要送我貼圖", "8點傳貼圖給我弟", "你剛傳的貼圖好可愛"]:
            self.assertFalse(selfstate.promise_wants_sticker(t), t)

    def test_promise_wants_sticker_flag_off_reverts(self):
        # STICKER_SEND_REQUEST=0 → 要-有框架不收（退回 §0.68 只給-動詞）＝逐位元同現狀
        os.environ["STICKER_SEND_REQUEST"] = "0"
        try:
            self.assertFalse(selfstate.promise_wants_sticker("8點問候我還要有特別的貼圖"))
            self.assertTrue(selfstate.promise_wants_sticker("8點送我一張貼圖"))   # 給-動詞仍收
        finally:
            os.environ.pop("STICKER_SEND_REQUEST", None)

    def test_is_sticker_send_request_positive(self):
        for t in ["開心的貼圖", "送我一張貼圖", "來張可愛的貼圖", "傳個貼圖給我",
                  "再送一張貼圖", "一張大貼圖", "給我一張開心的貼圖"]:
            self.assertTrue(selfstate.is_sticker_send_request(t), t)

    def test_is_sticker_send_request_negative(self):
        for t in ["我教你以後送這種貼圖", "你剛傳的貼圖好可愛", "不要送我貼圖",
                  "傳貼圖給我弟", "我們來聊貼圖設計", "這張貼圖是誰做的"]:
            self.assertFalse(selfstate.is_sticker_send_request(t), t)


class PersonaTest(unittest.TestCase):
    def test_essence_affirms_sticker_capability(self):
        s = persona.SOCRATIC_SYSTEM
        self.assertIn("送真的貼圖也是你真做得到的本事", s)
        self.assertIn("沒辦法送 telegram 貼圖", s)   # 明列這句禁語（前有「絕不要對他說」）
        self.assertIn("暫時沒存貨", s)

    def test_concept_hint_never_denies(self):
        for have in (True, False):
            h = persona.sticker_concept_hint(have_sendable=have)
            self.assertIn("真的做得到", h)            # 肯定能力
            self.assertIn("絕不要", h)                # 禁止否認
            self.assertNotIn("你把話講好", h)         # §0.68 不過度承諾（一般聊天不接真送）

    def test_send_reply_never_denies(self):
        no_stock = persona.sticker_send_reply(sent=False, have_sendable=False)
        self.assertIn("能", no_stock)
        self.assertNotIn("沒辦法", no_stock)
        self.assertNotIn("只能用文字", no_stock)
        self.assertTrue(persona.sticker_send_reply(sent=True).startswith("來"))

    def test_promise_keep_sticker_wanted_note(self):
        # 想送但這次沒送出 → 掛防 emoji 假裝守則（截圖 8:00「✨」）
        u = persona.promise_keep_user("08:00", "x", promised="問候他", sticker_wanted=True)
        self.assertIn("沒有真的送出貼圖", u)
        self.assertIn("假裝", u)
        # 沒要求貼圖時不掛（逐位元同現狀）
        u2 = persona.promise_keep_user("08:00", "x", promised="問候他")
        self.assertNotIn("假裝", u2)


class OnDemandTest(unittest.TestCase):
    def _state(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        return s

    def test_empty_pool_honest_no_denial_no_fake(self):
        st = self._state()
        c = FakeClient()
        handled = monitor._maybe_sticker_send(c, st, _cfg(), "開心的貼圖", 1000.0)
        self.assertTrue(handled)
        self.assertEqual(c.stickers, [])                 # 沒貨＝沒真送
        reply = "".join(c.sent)
        self.assertIn("能", reply)                       # 肯定能送
        self.assertNotIn("沒辦法", reply)                # 不否認
        self.assertNotIn("只能用文字", reply)            # 不自我矮化成只會送文字
        self.assertIn("不是貼圖", reply)                 # 分清 emoji≠貼圖（提到符號是為了區分、非假裝）

    def test_with_pool_real_send(self):
        st = self._state()
        st.known_sticker_ids = [{"file_id": "happy1", "valence": "positive"},
                                {"file_id": "happy2", "valence": "positive"}]
        c = FakeClient()
        monitor._maybe_sticker_send(c, st, _cfg(), "開心的貼圖", 1000.0)
        self.assertEqual(len(c.stickers), 1)             # 真送一張
        self.assertIn(c.stickers[0], ("happy1", "happy2"))
        self.assertTrue("".join(c.sent).startswith("來"))
        self.assertEqual(st.recent_sticker_ids, [c.stickers[0]])   # recent 有記

    def test_scheduled_sticker_promise_not_stolen(self):
        # 帶未來時刻的送貼圖約定 → 交給排程承諾引擎，on-demand 不接管
        st = self._state()
        st.known_sticker_ids = [{"file_id": "s1", "valence": "positive"}]
        c = FakeClient()
        handled = monitor._maybe_sticker_send(c, st, _cfg(), "8點送我一張貼圖", 1000.0)
        self.assertFalse(handled)
        self.assertEqual(c.stickers, [])

    def test_teaching_not_treated_as_request(self):
        st = self._state()
        st.known_sticker_ids = [{"file_id": "s1", "valence": "positive"}]
        c = FakeClient()
        self.assertFalse(monitor._maybe_sticker_send(c, st, _cfg(), "我教你以後送這種貼圖", 1000.0))

    def test_flag_off_noop(self):
        st = self._state()
        c = FakeClient()
        self.assertFalse(monitor._maybe_sticker_send(c, st, _cfg(sticker_send_request_enabled=False),
                                                     "開心的貼圖", 1000.0))
        self.assertEqual(c.sent, [])


class CompoundKeepTest(unittest.TestCase):
    """複合承諾（問候+貼圖）貼圖沒貨時：保留主行為 問候他、只是不送/不假裝貼圖（sticker_wanted 守則擋 emoji 假裝）。"""

    def _coach(self, seen):
        def vpk(when, facts, h, promised="", late=False, feeling_ground="", sticker_sent=False, sticker_wanted=False, sticker_desc="", change_ground=""):
            seen.update(promised=promised, sticker_sent=sticker_sent, sticker_wanted=sticker_wanted)
            return "早安。"
        return SimpleNamespace(enabled=True, api_key="k", model="m",
                               meter=SimpleNamespace(record=lambda *a, **k: None), voice_promise_keep=vpk)

    def _emit_cfg(self):
        return SimpleNamespace(dry_run=False, promise_emit_enabled=True, promise_sticker_enabled=True,
                               promise_act_aligned=True, sched_feeling_ground_enabled=True,
                               sticker_no_repeat_enabled=True, sticker_rotate_window=3, sticker_file_ids=[],
                               promise_late_exempt_defer=True, promise_overdue_guard_exempt=True, timezone="Asia/Taipei")

    def test_compound_keeps_primary_behavior(self):
        from datetime import datetime, timezone
        now = datetime(2026, 7, 7, 0, 0, tzinfo=timezone.utc)
        st = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        st.owner_folder_id = "F"
        st.scheduled_promises = [{"target_ts": now.timestamp() - 30, "made_ts": now.timestamp() - 300,
                                  "fulfilled": False, "behavior": "問候他", "wants_sticker": True, "status": "pending"}]
        seen = {}
        c = FakeClient()
        monitor._promise_emit(c, st, self._emit_cfg(), self._coach(seen), now)
        self.assertEqual(c.stickers, [])                 # 空池＝沒真送
        self.assertEqual(seen["promised"], "問候他")     # 主行為保留（不因貼圖沒貨被丟）
        self.assertTrue(seen["sticker_wanted"])          # 仍告知 voice 別 emoji 假裝
        self.assertFalse(seen["sticker_sent"])

    def test_sticker_only_promise_still_cleared(self):
        # 純送貼圖承諾（behavior=送他一張貼圖）沒貨時仍清空＝不空口宣稱送了貼圖（§0.68 不變）
        from datetime import datetime, timezone
        now = datetime(2026, 7, 7, 0, 0, tzinfo=timezone.utc)
        st = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        st.owner_folder_id = "F"
        st.scheduled_promises = [{"target_ts": now.timestamp() - 30, "made_ts": now.timestamp() - 300,
                                  "fulfilled": False, "behavior": "送他一張貼圖", "wants_sticker": True, "status": "pending"}]
        seen = {}
        monitor._promise_emit(FakeClient(), st, self._emit_cfg(), self._coach(seen), now)
        self.assertEqual(seen["promised"], "")           # 純貼圖承諾沒貨 → 清空落回打招呼


class DiversityWiringTest(unittest.TestCase):
    def test_pick_and_record_rotate(self):
        st = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        cfg = _cfg()
        ids = ["a", "b", "c", "d"]
        picks = set()
        for _ in range(12):
            fid = monitor._pick_sticker(ids, st, cfg)
            monitor._record_sticker_sent(st, fid, 1.0)
            picks.add(fid)
        self.assertEqual(picks, set(ids))                # 12 次應輪遍全部 4 張
        self.assertLessEqual(len(st.recent_sticker_ids), 8)

    def test_pick_window1_excludes_only_last(self):
        st = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        st.recent_sticker_ids = ["a"]
        cfg = _cfg(sticker_rotate_window=1)
        for _ in range(30):
            self.assertNotEqual(monitor._pick_sticker(["a", "b"], st, cfg), None)


class ReviewFixTest(unittest.TestCase):
    """§0.84 對抗式審查修正回歸。"""

    def test_negated_want_frames_not_captured(self):
        # 🛡️ 審查 HIGH：否定+要-有框架（不要有貼圖/不想要貼圖/別要有貼圖/不要一張貼圖/不要貼圖）＝別做相反的事 → 不收
        for t in ["不要有貼圖", "不想要貼圖", "不要一張貼圖", "別要有貼圖", "不要貼圖",
                  "問候我但不要有貼圖", "8點問候我不要有貼圖"]:
            self.assertFalse(selfstate.is_sticker_send_request(t), t)
            self.assertFalse(selfstate.promise_wants_sticker(t), t)
        # 正例不受影響
        for t in ["還要有特別的貼圖", "想要一張可愛貼圖", "送我一張貼圖"]:
            self.assertTrue(selfstate.promise_wants_sticker(t), t)

    def test_send_reply_no_empty_retry_promise(self):
        # 🛡️ 審查 LOW：have_sendable 但沒送出 → 別空口答應重試、別假稱卡頓
        r = persona.sticker_send_reply(sent=False, have_sendable=True)
        self.assertNotIn("再試一次", r)
        self.assertNotIn("卡了一下", r)
        self.assertIn("沒把貼圖送出去", r)


class MoreFormsTest(unittest.TestCase):
    """🎴 §0.86 承接/再送/記住 偵測。"""

    def test_more_forms_with_word_captured(self):
        for t in ["其他的貼圖", "另一張貼圖", "再一張貼圖", "換一個貼圖", "還有別的貼圖嗎", "多給我一張貼圖"]:
            self.assertTrue(selfstate.is_sticker_send_request(t), t)

    def test_followup_forms_without_word(self):
        for t in ["還有嗎", "再一個", "再一張", "換一個", "另一個",
                  "我剛剛傳給你的一個", "傳給我剛剛那張", "剛剛那個"]:
            self.assertTrue(selfstate.is_sticker_followup_request(t), t)

    def test_followup_negatives(self):
        for t in ["你好嗎", "再想想", "換個話題", "我剛剛在忙", "不要再送了"]:
            self.assertFalse(selfstate.is_sticker_followup_request(t), t)

    def test_remember_request(self):
        for t in ["這些 sticker 都記下來", "記住這些貼圖", "這幾張貼圖存起來", "貼圖記下來"]:
            self.assertTrue(selfstate.is_sticker_remember_request(t), t)
        for t in ["不要記這些貼圖", "這張貼圖好可愛", "記得吃藥"]:
            self.assertFalse(selfstate.is_sticker_remember_request(t), t)


class FollowupWiringTest(unittest.TestCase):
    """🎴 §0.86 承接請求要**近期貼圖情境**才真送；記住請求給正確可再送確認。"""

    def _state_taught(self, ts=1000.0):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        s.known_sticker_ids = [{"file_id": "s1", "valence": "positive", "ts": ts},
                               {"file_id": "s2", "valence": "neutral", "ts": ts}]
        s.last_sticker_ts = ts
        return s

    def test_followup_sends_with_recent_ctx(self):
        for msg in ["其他的貼圖", "還有嗎", "再一個", "我剛剛傳給你的一個",
                    "我要你傳給我，我剛剛傳給你的一個"]:
            st = self._state_taught()
            c = FakeClient()
            handled = monitor._maybe_sticker_send(c, st, _cfg(), msg, 1100.0)   # within 15min
            self.assertTrue(handled, msg)
            self.assertEqual(len(c.stickers), 1, msg)                            # 真送一張
            self.assertTrue("".join(c.sent).startswith("來"), msg)

    def test_bare_followup_needs_recent_ctx(self):
        # 「還有嗎」無近期貼圖情境 → 不接管（落一般聊天）
        st = self._state_taught(ts=0.0)                                          # 遠古 → 非近期
        c = FakeClient()
        self.assertFalse(monitor._maybe_sticker_send(c, st, _cfg(), "還有嗎", 10 ** 7))
        self.assertEqual(c.sent, [])

    def test_remember_ack_resendable_not_feeling(self):
        st = self._state_taught()
        c = FakeClient()
        self.assertTrue(monitor._maybe_sticker_remember(c, st, _cfg(), "這些 sticker 都記下來", 1100.0))
        r = "".join(c.sent)
        self.assertIn("記起來", r)
        self.assertIn("送", r)
        self.assertNotIn("感覺", r)                                             # 不框成「記下感覺」
        self.assertNotIn("描述", r)


class BareSendTest(unittest.TestCase):
    """🎴 §0.88 極泛「傳給我/給我」——bot 剛在講貼圖時＝送真貼圖（別吐「（貼圖：😏）」emoji 假裝）。"""

    def test_bare_send_detect(self):
        for t in ["傳給我", "給我", "傳一張給我", "發給我", "傳過來", "傳一張"]:
            self.assertTrue(selfstate.is_bare_send_request(t), t)

    def test_bare_send_not_over_capture(self):
        for t in ["把報告傳給我", "傳訊息給我", "傳給你", "傳給我弟", "不要傳給我", "傳給我剛剛的資料"]:
            self.assertFalse(selfstate.is_bare_send_request(t), t)

    def test_bare_send_no_role_reversal_or_bare_verb(self):
        # 🛡️ 審查 MED：使用者自陳要傳（我傳一張＝教下一張貼圖）＋裸單字動詞不收（否則 bot 回送沒人要的貼圖）
        for t in ["我傳一張", "我發一張", "我要傳一張", "傳", "送", "給", "發"]:
            self.assertFalse(selfstate.is_bare_send_request(t), t)

    def _state(self, bot_last="如果說要一張貼圖來形容，我會選一個帶著微笑的表情。", recent=True):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        s.known_sticker_ids = [{"file_id": "s1", "valence": "positive", "ts": 1000.0}]
        s.last_sticker_ts = 1000.0 if recent else 0
        s.convo_history = [{"role": "model", "text": bot_last, "ts": 1000.0}]
        return s

    def test_bare_send_after_sticker_talk_sends_real(self):
        # 截圖：bot 講了要選哪張貼圖 → 「傳給我」→ 送真貼圖、不吐 emoji 假裝
        for msg in ["傳給我", "給我", "傳一張給我"]:
            st = self._state()
            c = FakeClient()
            self.assertTrue(monitor._maybe_sticker_send(c, st, _cfg(), msg, 1100.0), msg)
            self.assertEqual(len(c.stickers), 1, msg)                # 真送一張
            self.assertTrue("".join(c.sent).startswith("來"), msg)

    def test_bare_send_needs_sticker_context(self):
        # bot 上一則**不是**在講貼圖 → 「傳給我」不接管（可能是「把報告傳給我」之類）
        st = self._state(bot_last="好的，幫你查到 3 筆記寫。")
        c = FakeClient()
        self.assertFalse(monitor._maybe_sticker_send(c, st, _cfg(), "傳給我", 1100.0))
        self.assertEqual(c.stickers, [])

    def test_concept_hint_forbids_paren_emoji_fake(self):
        h = persona.sticker_concept_hint(have_sendable=True)
        self.assertIn("（貼圖：X）", h)                              # 明令禁止「（貼圖：😏）」寫法


class ConfigDefaultTest(unittest.TestCase):
    def test_flags_default(self):
        for env in ("STICKER_SEND_REQUEST", "STICKER_ROTATE_WINDOW"):
            os.environ.pop(env, None)
        cfg = config.Config.load()
        self.assertTrue(cfg.sticker_send_request_enabled)
        self.assertEqual(cfg.sticker_rotate_window, 3)


if __name__ == "__main__":
    unittest.main()
