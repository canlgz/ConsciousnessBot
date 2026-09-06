"""😀 貼圖＝對話情緒訊號：被收下（不再丟掉）、自然回應、染後續回覆語氣。"""

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from telegram_monitor import reaction, monitor
from telegram_monitor.state import State

NOW_TS = 1_700_000_000


class ReadStickerTest(unittest.TestCase):
    def test_valence(self):
        self.assertEqual(reaction.read_sticker("🔥"), "positive")
        self.assertEqual(reaction.read_sticker("👍"), "positive")
        self.assertEqual(reaction.read_sticker("😭"), "negative")
        self.assertEqual(reaction.read_sticker("🤔"), "neutral")
        self.assertEqual(reaction.read_sticker(""), "neutral")

    def test_tone_hint_nonempty_for_known(self):
        self.assertTrue(reaction.tone_hint("positive"))
        self.assertTrue(reaction.tone_hint("negative"))
        self.assertEqual(reaction.tone_hint(""), "")


class OwnReactionQuestionTest(unittest.TestCase):
    def test_detector(self):
        from telegram_monitor import selfstate
        for q in ["你能知道你點了一則訊息的情緒嗎", "你剛剛點了什麼表情", "你給我點了什麼", "你按了什麼情緒"]:
            self.assertTrue(selfstate.is_own_reaction_question(q), q)
        for q in ["你現在怎樣", "幫我看記寫", "我點了什麼"]:
            self.assertFalse(selfstate.is_own_reaction_question(q), q)

    def test_recalls_own_reaction_not_record_listing(self):
        import os
        import tempfile
        from telegram_monitor.state import State
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        s.last_sent_reaction = {"emoji": "🥰", "to": "等你喔", "ts": NOW_TS}
        boom = SimpleNamespace(load_embedding_records=lambda *_: (_ for _ in ()).throw(AssertionError("不該列記寫")))
        client = SimpleNamespace(sent=[], dry_run=False, send=lambda t: client.sent.append(t) or True)
        coach = SimpleNamespace(enabled=True)
        update = {"message": {"chat": {"id": 1}, "text": "你能知道你點了一則訊息的情緒嗎", "date": NOW_TS}}
        with mock.patch("telegram_monitor.gemini.generate_with_tools", side_effect=AssertionError("不該走工具")):
            monitor.handle_message(update, coach, boom, {"meta": {}, "records": []}, object(),
                                   s, client, SimpleNamespace(dry_run=False, telegram_chat_id=""), None)
        joined = "".join(client.sent)
        self.assertIn("🥰", joined)
        self.assertIn("等你喔", joined)


class PickReactionTest(unittest.TestCase):
    def test_warm_message_gets_heart(self):
        self.assertEqual(reaction.pick_reaction("等你喔"), "🥰")
        self.assertEqual(reaction.pick_reaction("謝謝你陪我"), "🥰")

    def test_praise_funny_down(self):
        self.assertEqual(reaction.pick_reaction("你好厲害"), "🔥")
        self.assertEqual(reaction.pick_reaction("哈哈好好笑"), "😁")
        self.assertEqual(reaction.pick_reaction("今天好累好難過"), "🤗")

    def test_agreement_gets_thumbs_up(self):
        # 認同/答應 → 👍（「讚」）——使用者點名的反應
        self.assertEqual(reaction.pick_reaction("好啊，那就這樣"), "👍")
        self.assertEqual(reaction.pick_reaction("對啊沒錯"), "👍")
        self.assertEqual(reaction.pick_reaction("收到，懂了"), "👍")

    def test_good_news_gets_party(self):
        self.assertEqual(reaction.pick_reaction("太好了"), "🎉")
        self.assertEqual(reaction.pick_reaction("終於搞定了"), "🎉")

    def test_praise_still_beats_agreement(self):
        # 含誇讚詞時走誇讚（🔥），不被泛泛「好」之類誤判成 👍
        self.assertEqual(reaction.pick_reaction("你好棒"), "🔥")

    def test_neutral_no_reaction(self):
        self.assertIsNone(reaction.pick_reaction("幫我看今天寫了什麼"))
        self.assertIsNone(reaction.pick_reaction(""))

    def test_hungry_bot_treasures_praise(self):
        self.assertEqual(reaction.pick_reaction("你好棒", {"hunger": 0.8}), "🥰")  # 餓/悶時更被珍惜


class PickSelfReactionTest(unittest.TestCase):
    """bot『按自己內在情緒』：對方訊息不鮮明時，依自己當下心情/飢餓表達一下（只發正向/溫的）。"""

    def test_warm_mood_reacts_heart(self):
        self.assertEqual(reaction.pick_self_reaction({"mood": 0.6, "hunger": 0.1}), "🥰")

    def test_hungry_treasures_contact(self):
        self.assertEqual(reaction.pick_self_reaction({"mood": 0.1, "hunger": 0.8}), "🤗")

    def test_low_or_neutral_stays_silent(self):
        self.assertIsNone(reaction.pick_self_reaction({"mood": 0.1, "hunger": 0.2}))      # 心情平、不餓→不點
        self.assertIsNone(reaction.pick_self_reaction({"mood": -0.6, "hunger": 0.9}))     # 低落→不亂點負面到對方頭上
        self.assertIsNone(reaction.pick_self_reaction(None))


class MaybeReactSentenceDrivenTest(unittest.TestCase):
    """reaction 只**鏡像對方這句話的情緒**：中性訊息不按——不拿 bot 自己的心情去點對方的中性訊息。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _state(self):
        return State(os.path.join(self.tmp, "state.json"))

    def _client(self):
        c = SimpleNamespace(reacted=[], dry_run=False)
        c.set_reaction = lambda mid, emoji: (c.reacted.append((mid, emoji)) or True)
        return c

    def test_neutral_message_not_reacted_even_if_bot_in_good_mood(self):
        state, client = self._state(), self._client()
        state.vitality = {"mood": 0.8, "hunger": 0.9}                 # bot 自己心情很好/很餓，但對方這句是中性的
        update = {"message": {"chat": {"id": 1}, "message_id": 7, "text": "幫我看今天寫了什麼", "date": NOW_TS}}
        monitor._maybe_react(update, "幫我看今天寫了什麼", state, client, SimpleNamespace(dry_run=False))
        self.assertEqual(client.reacted, [])                          # 中性句 → 不按（不靠 bot 心情點對方）

    def test_reacts_by_sentence_emotion(self):
        state, client = self._state(), self._client()
        update = {"message": {"chat": {"id": 1}, "message_id": 9, "text": "謝謝你陪我", "date": NOW_TS}}
        monitor._maybe_react(update, "謝謝你陪我", state, client, SimpleNamespace(dry_run=False))
        self.assertEqual(client.reacted, [(9, "🥰")])                 # 鏡像那句話的暖意


class SendableStickerIdsTest(unittest.TestCase):
    """🎴 回送真貼圖的 file_id 池：對方教過的非負向 ∪ 設定檔指定，去重保序。"""

    def test_filters_negative_and_dedups(self):
        known = [{"file_id": "a", "valence": "positive"}, {"file_id": "b", "valence": "negative"},
                 {"file_id": "c", "valence": "neutral"}, {"file_id": "a", "valence": "positive"}]
        self.assertEqual(reaction.sendable_sticker_ids(known, ["c", "d"]), ["a", "c", "d"])

    def test_empty(self):
        self.assertEqual(reaction.sendable_sticker_ids(None, None), [])

    def test_help_sticker_ids_is_inverse(self):
        # 🆘 §0.65 求救貼圖池＝非正向（負向🥺😭＋中性），與回送池相反向；不併設定檔
        known = [{"file_id": "cheer", "valence": "positive"}, {"file_id": "sos", "valence": "negative"},
                 {"file_id": "mid", "valence": "neutral"}, {"file_id": "sos", "valence": "negative"}]
        self.assertEqual(reaction.help_sticker_ids(known), ["sos", "mid"])   # 排正向、去重保序
        self.assertEqual(reaction.help_sticker_ids(None), [])


class MaybeStickerTest(unittest.TestCase):
    """🎴 偶爾在回話後吐真貼圖（強化互動感）：心情好才丟、自有冷卻保持稀有、可關；
    有對方教過的真貼圖就送真貼圖，沒有才退回單顆 emoji。"""

    def _state(self, mood=0.6, last=0, stickers=None):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.vitality = {"mood": mood}
        s.last_sticker_ts = last
        s.known_sticker_ids = stickers or []
        return s

    def _client(self, with_sticker=False):
        c = SimpleNamespace(sent=[], stickers=[], dry_run=False)
        c.send = lambda t: c.sent.append(t) or True
        if with_sticker:
            c.send_sticker = lambda fid: c.stickers.append(fid) or True
        return c

    def _cfg(self, **kw):
        base = dict(dry_run=False, send_stickers=True, sticker_cooldown_min=20)
        base.update(kw)
        return SimpleNamespace(**base)

    def test_sends_real_sticker_when_captured(self):
        # 心情好 + 有對方教過的正向貼圖 → 送真貼圖（file_id），不是 emoji
        s = self._state(mood=0.6, stickers=[{"file_id": "FID1", "valence": "positive"}])
        c = self._client(with_sticker=True)
        monitor._maybe_sticker(c, s, self._cfg(), NOW_TS)
        self.assertEqual(c.stickers, ["FID1"])
        self.assertEqual(c.sent, [])
        self.assertEqual(s.last_sticker_ts, NOW_TS)

    def test_falls_back_to_emoji_when_no_real_sticker(self):
        # 還沒學到任何真貼圖 → 退回單顆 emoji（放大成貼圖感）
        s, c = self._state(mood=0.6), self._client(with_sticker=True)
        monitor._maybe_sticker(c, s, self._cfg(), NOW_TS)
        self.assertEqual(c.sent, ["🥰"])
        self.assertEqual(c.stickers, [])
        self.assertEqual(s.last_sticker_ts, NOW_TS)

    def test_uses_configured_file_ids(self):
        # 沒學到對方貼圖、但設定檔有指定 → 送設定檔的 file_id
        s, c = self._state(mood=0.6), self._client(with_sticker=True)
        monitor._maybe_sticker(c, s, self._cfg(sticker_file_ids=["CFG1"]), NOW_TS)
        self.assertEqual(c.stickers, ["CFG1"])

    def test_sends_emoji_when_client_has_no_send_sticker(self):
        s = self._state(mood=0.6, stickers=[{"file_id": "FID1", "valence": "positive"}])
        c = self._client(with_sticker=False)                     # 舊 client 沒 send_sticker 能力
        monitor._maybe_sticker(c, s, self._cfg(), NOW_TS)
        self.assertEqual(c.sent, ["🥰"])

    def test_silent_when_neutral_mood(self):
        s, c = self._state(mood=0.1), self._client(with_sticker=True)
        monitor._maybe_sticker(c, s, self._cfg(), NOW_TS)
        self.assertEqual(c.sent, [])
        self.assertEqual(c.stickers, [])

    def test_silent_within_cooldown(self):
        s, c = self._state(mood=0.6, last=NOW_TS - 60), self._client(with_sticker=True)  # 1 分前才吐過 < 20 分
        monitor._maybe_sticker(c, s, self._cfg(), NOW_TS)
        self.assertEqual(c.sent, [])
        self.assertEqual(c.stickers, [])

    def test_switch_off(self):
        s, c = self._state(mood=0.6), self._client(with_sticker=True)
        monitor._maybe_sticker(c, s, self._cfg(send_stickers=False), NOW_TS)
        self.assertEqual(c.sent, [])
        self.assertEqual(c.stickers, [])

    def test_no_repeat_excludes_last_sticker(self):
        # 🎴 §0.59 Part 3a：可送 >1 張時，選圖排除上一張送過的 → 連兩次不會同款
        s = self._state(mood=0.6, stickers=[{"file_id": "A", "valence": "positive"},
                                            {"file_id": "B", "valence": "positive"}])
        c = self._client(with_sticker=True)
        s.last_sticker_id = "A"                                   # 上次送的是 A
        monitor._maybe_sticker(c, s, self._cfg(), NOW_TS)
        self.assertEqual(c.stickers, ["B"])                      # 只剩 B 可選（排除 A）
        self.assertEqual(s.last_sticker_id, "B")                 # 記住這次送的

    def test_no_repeat_flag_off_allows_repeat(self):
        # 旗標關＝照舊 random.choice（單張池仍可能重複）；此處只驗證不因去重而改變可選集合
        s = self._state(mood=0.6, stickers=[{"file_id": "A", "valence": "positive"}])
        c = self._client(with_sticker=True)
        s.last_sticker_id = "A"
        monitor._maybe_sticker(c, s, self._cfg(sticker_no_repeat_enabled=False), NOW_TS)
        self.assertEqual(c.stickers, ["A"])                      # 旗標關 → 仍可送同一張

    def test_last_sticker_id_not_recorded_on_emoji_fallback(self):
        # 送貼圖失敗退回 emoji 時，不可把「沒真的送出的貼圖」記成 last_sticker_id（否則下次排除到使用者沒看過的那張）
        s = self._state(mood=0.6, stickers=[{"file_id": "A", "valence": "positive"},
                                            {"file_id": "B", "valence": "positive"}])
        c = self._client(with_sticker=True)
        c.send_sticker = lambda fid: False                       # 貼圖送出失敗
        monitor._maybe_sticker(c, s, self._cfg(), NOW_TS)
        self.assertEqual(c.sent, ["🥰"])                         # 退回 emoji
        self.assertIsNone(s.last_sticker_id)                     # 沒記（因為貼圖沒真的送出）


class MaybeReactTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _state(self):
        return State(os.path.join(self.tmp, "state.json"))

    def _client(self):
        c = SimpleNamespace(reacted=[], dry_run=False)
        c.set_reaction = lambda mid, emoji: (c.reacted.append((mid, emoji)) or True)
        return c

    def test_reacts_to_warm_message(self):
        state, client = self._state(), self._client()
        update = {"message": {"chat": {"id": 1}, "message_id": 42, "text": "等你喔", "date": NOW_TS}}
        monitor._maybe_react(update, "等你喔", state, client, SimpleNamespace(dry_run=False))
        self.assertEqual(client.reacted, [(42, "🥰")])
        self.assertEqual(state.last_react_sent_ts, NOW_TS)

    def test_cooldown_blocks_repeat(self):
        state, client = self._state(), self._client()
        state.last_react_sent_ts = NOW_TS - 10        # 才 10 秒前按過 < 90 秒冷卻
        update = {"message": {"chat": {"id": 1}, "message_id": 43, "text": "愛你", "date": NOW_TS}}
        monitor._maybe_react(update, "愛你", state, client, SimpleNamespace(dry_run=False))
        self.assertEqual(client.reacted, [])

    def test_no_react_to_commands_or_neutral(self):
        state, client = self._state(), self._client()
        for txt in ("/status", "現在幾點"):
            monitor._maybe_react({"message": {"chat": {"id": 1}, "message_id": 1, "text": txt, "date": NOW_TS}},
                                 txt, state, client, SimpleNamespace(dry_run=False))
        self.assertEqual(client.reacted, [])


class StickerHandlingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _state(self):
        s = State(os.path.join(self.tmp, "state.json"))
        s.owner_folder_id = "F"
        return s

    def _client(self):
        c = SimpleNamespace(sent=[], dry_run=False)
        c.send = lambda t: c.sent.append(t) or True
        return c

    def test_sticker_message_is_handled_not_dropped(self):
        # 純貼圖（沒文字）：以前會被「沒文字就 return」丟掉；現在要回一句並記為最近反應。
        state, client = self._state(), self._client()
        coach = SimpleNamespace(enabled=True, voice_sticker_ack=lambda e, v, h, desc=None: "嘿，謝啦 🔥")
        update = {"message": {"chat": {"id": 1}, "sticker": {"emoji": "🔥"}, "date": NOW_TS}}
        cfg = SimpleNamespace(dry_run=False, telegram_chat_id="")
        monitor.handle_message(update, coach, None, {"meta": {}}, None, state, client, cfg, None)
        self.assertEqual(client.sent, ["嘿，謝啦 🔥"])
        self.assertEqual(state.last_reaction["valence"], "positive")
        self.assertEqual(state.last_user_msg_ts, NOW_TS)          # 貼圖也算聯絡

    def test_incoming_sticker_file_id_is_remembered(self):
        # 對方傳的真貼圖 file_id 被記住（之後 bot 心情好時可回送同一張）
        state, client = self._state(), self._client()
        coach = SimpleNamespace(enabled=True, voice_sticker_ack=lambda e, v, h, desc=None: "謝啦")
        update = {"message": {"chat": {"id": 1}, "sticker": {"emoji": "🔥", "file_id": "FID9"}, "date": NOW_TS}}
        monitor.handle_message(update, coach, None, {"meta": {}}, None, state, client,
                               SimpleNamespace(dry_run=False, telegram_chat_id=""), None)
        self.assertEqual([s["file_id"] for s in state.known_sticker_ids], ["FID9"])
        self.assertEqual(state.known_sticker_ids[0]["valence"], "positive")

    def test_positive_sticker_nudges_entropy_charge(self):
        from telegram_monitor import lifeloop
        state, client = self._state(), self._client()
        state.entropy = lifeloop.EntropyState()
        coach = SimpleNamespace(enabled=True, voice_sticker_ack=lambda e, v, h, desc=None: "謝啦")
        update = {"message": {"chat": {"id": 1}, "sticker": {"emoji": "🔥"}, "date": NOW_TS}}
        monitor.handle_message(update, coach, None, {"meta": {}}, None, state, client,
                               SimpleNamespace(dry_run=False, telegram_chat_id=""), None)
        self.assertGreater(state.tempo_charge_pending, 0)         # 被肯定＝一點正向擾動
        self.assertEqual(state.entropy.self_stims_this_idle, 0)   # 互動＝陪伴、醞釀歸零

    def test_recent_sticker_colors_next_reply_tone(self):
        # 收到正面貼圖後，下一則文字回覆的系統提示帶入該情緒（語氣參考）。
        state, client = self._state(), self._client()
        state.last_reaction = {"emoji": "🔥", "valence": "positive", "ts": NOW_TS}
        cap = {}

        def fake_ask(q, brief, ctx, hist, mood_hint="", self_presence=False, **_):
            cap["mood_hint"] = mood_hint
            return ("chat", None, "（回覆）")

        coach = SimpleNamespace(enabled=True, meter=SimpleNamespace(record=lambda *a, **k: None), ask=fake_ask)
        update = {"message": {"chat": {"id": 1}, "text": "你覺得呢", "date": NOW_TS}}
        with mock.patch("telegram_monitor.coach.build_memory_brief", return_value=""):
            monitor.handle_message(update, coach, None, {"meta": {}, "records": []}, object(),
                                   state, client, SimpleNamespace(dry_run=False, telegram_chat_id=""), None)
        self.assertIn("鼓勵的貼圖", cap["mood_hint"])              # 正面貼圖情緒進了語氣提示


if __name__ == "__main__":
    unittest.main()
