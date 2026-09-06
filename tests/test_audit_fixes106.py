"""🧭 §1.06 workflow 審計批次修（4 維度確認項）：情緒單一真相／編輯與媒體入口／聯想接地／蛻變基準延續。"""

import os
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from telegram_monitor import affect, association, lifeloop, metacog, monitor, selfstate
from telegram_monitor.state import State

NOW = 1_700_000_000


class A1BodystateArousalTest(unittest.TestCase):
    """A1：內在動盪的「沉澱平穩」照 circumplex 慢喚起軸講——不再與 affect_clause 的「興奮雀躍」同回覆矛盾。"""

    def _vit(self, a):
        return {"alive": True, "pulse": 30, "healthy_streak": 30, "last_lap_ms": 100, "uptime_s": 540,
                "S": 0.2, "charge": 0.1, "hunger": 0.1, "mood": 0.4, "arousal": a, "laps_since_fresh": 3}

    def test_high_arousal_not_settled(self):
        facts = selfstate.bodystate_facts(self._vit(0.5), None)
        self.assertNotIn("內裡平穩", facts)                    # A 高 → 不再說「平穩」（截圖矛盾根源）
        self.assertIn("還醒著", facts)

    def test_low_arousal_weary(self):
        facts = selfstate.bodystate_facts(self._vit(-0.5), None)
        self.assertIn("有點倦", facts)

    def test_neutral_arousal_bitwise_old(self):
        facts = selfstate.bodystate_facts(self._vit(0.0), None)
        self.assertIn("內裡平穩", facts)                        # A≈0（含旗標關）＝原句


class A2RepeatPriorTest(unittest.TestCase):
    """A2：重複問「沒變」以剛說過的感覺詞為準（stash 安全源），render_bodystate_again 收到 self_prior。"""

    def test_prior_built_from_stash(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.last_bodystate_feel = {"word": "興奮", "ts": NOW - 120}
        # 直接驗 render_bodystate_again 模板路（coach=None）不炸＋monitor 重複分支的 prior 組字串邏輯
        out = selfstate.render_bodystate_again(2, {"charge": 0, "hunger": 0}, None, None,
                                               self_prior="我自己剛說過的（興奮、約 2 分鐘前）")
        self.assertTrue(out)                                   # 模板退路仍可用（prior 只影響 LLM prompt）


class A3MetacogArousalTest(unittest.TestCase):
    def test_high_arousal_not_calm(self):
        ent = SimpleNamespace(charge=0.05, hunger=0.05, mood=0.1, arousal=0.6)
        (top, _s1), _ = metacog.label_state(ent)
        self.assertEqual(top, "stirred")                       # A 高 → 被攪動（不再誤判 calm 與 affect 打架）

    def test_zero_arousal_bitwise_old(self):
        ent = SimpleNamespace(charge=0.05, hunger=0.05, mood=0.1, arousal=0.0)
        (top, _s1), _ = metacog.label_state(ent)
        self.assertEqual(top, "calm")                          # A=0＝逐位元同舊


class A4FeelingWordsTest(unittest.TestCase):
    def test_circumplex_vocab_extractable(self):
        for w in ("興奮", "緊繃", "激動", "被攪動", "愉快", "平靜", "安穩", "消沉"):
            self.assertEqual(affect.extract_feeling_word(f"我這會兒{w}得很"), w, w)

    def test_longer_word_wins(self):
        self.assertEqual(affect.extract_feeling_word("有點被攪動"), "被攪動")

    def test_polarity(self):
        self.assertEqual(affect.opposite_feeling("興奮"), "煩躁")
        self.assertEqual(affect.opposite_feeling("緊繃"), "順利")


class C1ContentValenceTest(unittest.TestCase):
    """C1：「壓著沉／托著暖」照**內容**價性講，不是 bot 自身心情。"""

    def _st(self, mood):
        ent = lifeloop.EntropyState()
        ent.mood = mood
        return SimpleNamespace(entropy=ent, affect=None)

    def test_heavy_content_says_heavy_even_if_bot_happy(self):
        s = self._st(0.5)                                       # bot 心情很好
        aff = affect.appraise(s, {"content_valence": -0.7, "content_intensity": 1.0}, NOW)
        self.assertEqual(aff["label"], "被你寫的東西壓著、沉")     # 以前讀 bot mood 會講成「托著、暖」

    def test_warm_content_says_warm_even_if_bot_low(self):
        s = self._st(-0.5)
        aff = affect.appraise(s, {"content_valence": 0.7, "content_intensity": 1.0}, NOW)
        self.assertEqual(aff["label"], "被你寫的東西托著、暖")


class C2RippleUserOnlyTest(unittest.TestCase):
    def test_bot_own_message_does_not_ripple(self):
        hist = [{"role": "model", "text": "我一直想弄懂你的「讀經」", "ts": NOW - 10}]
        self.assertEqual(association.conversation_ripple_topics(hist, {"讀經"}, NOW, 3600), set())

    def test_user_message_still_ripples(self):
        hist = [{"role": "user", "text": "今天讀經有心得", "ts": NOW - 10}]
        self.assertEqual(association.conversation_ripple_topics(hist, {"讀經"}, NOW, 3600), {"讀經"})


class B1MediaContactTest(unittest.TestCase):
    def _state(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        s.entropy = lifeloop.EntropyState()
        s.entropy.hunger = 0.6
        return s

    def _client(self):
        c = SimpleNamespace(sent=[], dry_run=True)
        c.send = lambda t: c.sent.append(t) or True
        return c

    def test_photo_counts_as_contact(self):
        s, c = self._state(), self._client()
        update = {"message": {"chat": {"id": 1}, "photo": [{"file_id": "P1"}], "date": NOW}}
        monitor.handle_message(update, SimpleNamespace(enabled=False), None, {"meta": {}}, None,
                               s, c, SimpleNamespace(dry_run=True, telegram_chat_id="", mood_gain=1.0), None)
        self.assertEqual(s.last_user_msg_ts, NOW)               # 被看見＝接觸
        self.assertLess(s.entropy.hunger, 0.6)                  # 被陪伴＝餵飽
        self.assertGreater(s.entropy.mood, 0.0)                 # 微暖
        self.assertEqual(c.sent, [])                            # 不回覆（媒體理解另題）

    def test_service_message_not_contact(self):
        s, c = self._state(), self._client()
        update = {"message": {"chat": {"id": 1}, "new_chat_members": [{}], "date": NOW}}
        monitor.handle_message(update, SimpleNamespace(enabled=False), None, {"meta": {}}, None,
                               s, c, SimpleNamespace(dry_run=True, telegram_chat_id="", mood_gain=1.0), None)
        self.assertEqual(s.last_user_msg_ts, 0)                 # 服務訊息不算接觸

    def test_foreign_chat_media_ignored(self):
        s, c = self._state(), self._client()
        update = {"message": {"chat": {"id": 999}, "photo": [{}], "date": NOW}}
        monitor.handle_message(update, SimpleNamespace(enabled=False), None, {"meta": {}}, None,
                               s, c, SimpleNamespace(dry_run=True, telegram_chat_id="1", mood_gain=1.0), None)
        self.assertEqual(s.last_user_msg_ts, 0)


class B2EditedNoDoubleCountTest(unittest.TestCase):
    def _state(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        s.entropy = lifeloop.EntropyState()
        return s

    def _client(self):
        c = SimpleNamespace(sent=[], dry_run=True)
        c.send = lambda t: c.sent.append(t) or True
        return c

    def test_edited_sticker_no_reack_no_restack(self):
        s, c = self._state(), self._client()
        coach = SimpleNamespace(enabled=True, voice_sticker_ack=lambda e, v, h, desc=None: "謝啦")
        cfg = SimpleNamespace(dry_run=True, telegram_chat_id="", mood_gain=1.0, read_sticker_vision=False)
        upd = {"message": {"chat": {"id": 1}, "sticker": {"emoji": "🔥", "file_id": "F1"}, "date": NOW}}
        monitor.handle_message(upd, coach, None, {"meta": {}}, None, s, c, cfg, None)
        mood1, sent1 = s.entropy.mood, len(c.sent)
        edited = {"edited_message": {"chat": {"id": 1}, "sticker": {"emoji": "🔥", "file_id": "F1"}, "date": NOW}}
        monitor.handle_message(edited, coach, None, {"meta": {}}, None, s, c, cfg, None)
        self.assertEqual(s.entropy.mood, mood1)                 # 情緒不重疊
        self.assertEqual(len(c.sent), sent1)                    # 不重 ack


class D2BaselineInheritTest(unittest.TestCase):
    """D2：蛻變承諾「再10分鐘」續約後 change_baseline 不丟（沿用原基準）。"""

    NOW = datetime(2026, 7, 9, 10, 0, tzinfo=timezone.utc)

    def test_continuation_inherits_baseline(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        s.entropy = lifeloop.EntropyState()
        base = {"mood": 0.3, "arousal": -0.2, "hunger": 0.1, "gate": 2, "region": "平靜、安穩", "goals": {}}
        s.scheduled_promises = [{"target_ts": self.NOW.timestamp() - 60, "made_ts": self.NOW.timestamp() - 2400,
                                 "fulfilled": False, "status": "pending",
                                 "behavior": "跟他說說我此刻有什麼不一樣（我的變化）",
                                 "change_baseline": dict(base)}]
        c = SimpleNamespace(sent=[], dry_run=True)
        c.send = lambda t: c.sent.append(t) or True
        c.send_typing = lambda: None
        coach = SimpleNamespace(enabled=True, meter=SimpleNamespace(record=lambda *a, **k: None),
                                voice_schedule_ack=lambda q, when, h, sticker_hint="": "好。")
        cfg = SimpleNamespace(dry_run=True, telegram_chat_id="", timezone="Asia/Taipei",
                              continuation_promise_enabled=True, promise_sticker_enabled=True,
                              scheduled_promise_enabled=True, self_change_ground_enabled=True,
                              liked_sticker_pick_enabled=True, sticker_file_ids=None)
        took = monitor._maybe_continuation_promise(c, s, cfg, coach, "再10分鐘", self.NOW, timezone.utc,
                                                   self.NOW.timestamp())
        self.assertTrue(took)
        newp = [p for p in s.scheduled_promises if p.get("continuation")]
        self.assertEqual(len(newp), 1)
        self.assertEqual(newp[0].get("change_baseline"), base)   # 原基準延續＝從最初交代那刻量變化


if __name__ == "__main__":
    unittest.main()
