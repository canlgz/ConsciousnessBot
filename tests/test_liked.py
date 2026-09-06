"""👍 使用者對 bot 訊息按反應 → bot 知道「讚的是哪一筆」：
訂閱 message_reaction、訊息 id↔主題對照、last_liked 接地回答。"""

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from telegram_monitor import lifeloop, monitor, notifier
from telegram_monitor.state import State

NOW = 1_700_000_000


def _state():
    return State(os.path.join(tempfile.mkdtemp(), "s.json"))


class NotifierWiringTest(unittest.TestCase):
    def test_send_returns_message_id(self):
        n = notifier.Notifier("T", "123", dry_run=False)
        resp = SimpleNamespace(status_code=200, json=lambda: {"ok": True, "result": {"message_id": 555}})
        with mock.patch.object(notifier.requests, "post", return_value=resp):
            self.assertEqual(n.send("hi"), 555)                 # 回 message_id（truthy＝成功）

    def test_send_dry_run_still_truthy(self):
        self.assertIs(notifier.Notifier("T", "1", dry_run=True).send("x"), True)

    def test_get_updates_subscribes_message_reaction(self):
        n, cap = notifier.Notifier("T", "1"), {}

        def fake_get(url, params=None, timeout=None):
            cap["params"] = params
            return SimpleNamespace(status_code=200, json=lambda: {"result": []})

        with mock.patch.object(notifier.requests, "get", side_effect=fake_get):
            n.get_updates(offset=5, timeout=1)
        self.assertIn("message_reaction", cap["params"]["allowed_updates"])   # 沒這個就永遠收不到反應


class RecordAndLookupTest(unittest.TestCase):
    def test_say_records_real_ids_with_topic(self):
        s = _state()
        seq = iter([101, 102])
        c = SimpleNamespace(dry_run=False, send=lambda t: next(seq))   # 真 message_id
        monitor._say(c, "一。二。", state=s, topic="研發 X 線")
        self.assertEqual(s.recent_self_msgs[-1]["ids"], [101, 102])
        self.assertEqual(monitor._lookup_self_msg(s, 102)[0], "研發 X 線")
        self.assertEqual(monitor._lookup_self_msg(s, 999), (None, None))

    def test_say_skips_bool_results(self):
        # dry_run／測試假 client 回 True（bool）→ 不當作 message_id、不亂記
        s = _state()
        c = SimpleNamespace(dry_run=False, send=lambda t: True)
        monitor._say(c, "一。二。", state=s, topic="X")
        self.assertEqual(s.recent_self_msgs, [])

    def test_say_without_topic_records_nothing(self):
        s = _state()
        c = SimpleNamespace(dry_run=False, send=lambda t: 7)
        monitor._say(c, "一。", state=s)
        self.assertEqual(s.recent_self_msgs, [])


class HandleReactionTest(unittest.TestCase):
    def _cfg(self, **kw):
        base = dict(dry_run=False, telegram_chat_id="", mood_gain=1.0)
        base.update(kw)
        return SimpleNamespace(**base)

    def _seeded(self):
        s = _state()
        s.recent_self_msgs = [{"ids": [201], "topic": "研發 writetolearn 日誌→工程化", "text": "…", "ts": NOW}]
        s.entropy = lifeloop.EntropyState()
        return s

    def _mr(self, mid=201, emoji="👍", chat=1, removed=False):
        return {"chat": {"id": chat}, "message_id": mid, "date": NOW, "old_reaction": [],
                "new_reaction": [] if removed else [{"type": "emoji", "emoji": emoji}]}

    def test_sets_last_liked_with_topic_and_mood(self):
        s = self._seeded()
        monitor._handle_reaction(self._mr(), None, s, SimpleNamespace(dry_run=False), self._cfg())
        self.assertEqual(s.last_liked["topic"], "研發 writetolearn 日誌→工程化")
        self.assertEqual(s.last_liked["emoji"], "👍")
        self.assertGreater(s.entropy.mood, 0)              # 針對性肯定 → 心情↑
        self.assertEqual(s.last_user_msg_ts, NOW)          # 反應＝一次陪伴接觸
        self.assertTrue(any("按了 👍" in t.get("text", "") for t in s.convo_history))  # 進對話記憶

    def test_unknown_message_topic_none(self):
        s = self._seeded()
        monitor._handle_reaction(self._mr(mid=999, emoji="❤"), None, s, SimpleNamespace(dry_run=False), self._cfg())
        self.assertIsNone(s.last_liked["topic"])           # 查不到對照 → 誠實 None（fact 句會講「沒對到具體記寫」）

    def test_removed_reaction_ignored(self):
        s = self._seeded()
        monitor._handle_reaction(self._mr(removed=True), None, s, SimpleNamespace(dry_run=False), self._cfg())
        self.assertIsNone(s.last_liked)

    def test_foreign_chat_ignored(self):
        s = self._seeded()
        monitor._handle_reaction(self._mr(chat=999), None, s, SimpleNamespace(dry_run=False),
                                 self._cfg(telegram_chat_id="1"))
        self.assertIsNone(s.last_liked)


class LikedFactAndDetectorTest(unittest.TestCase):
    def test_liked_fact_grounded_with_topic(self):
        s = _state()
        s.last_liked = {"topic": "研發 X 線", "emoji": "👍", "ts": NOW}
        f = monitor._liked_fact(s, NOW + 10)
        self.assertIn("研發 X 線", f)
        self.assertIn("具體", f)

    def test_liked_fact_empty_after_window(self):
        s = _state()
        s.last_liked = {"topic": "X", "emoji": "👍", "ts": NOW}
        self.assertEqual(monitor._liked_fact(s, NOW + monitor._LIKE_WINDOW_SEC + 1), "")

    def test_liked_fact_no_topic_variant(self):
        s = _state()
        s.last_liked = {"topic": None, "emoji": "❤", "ts": NOW}
        self.assertTrue(monitor._liked_fact(s, NOW + 5))

    def test_liked_fact_none(self):
        self.assertEqual(monitor._liked_fact(_state(), NOW), "")

    def test_asks_which_liked(self):
        for q in ["你知道我點哪筆讚？", "我剛剛按的是哪一筆", "哪條讚的", "哪一則是我點的"]:
            self.assertTrue(monitor._asks_which_liked(q), q)
        for q in ["你現在怎樣", "幫我看記寫", "謝謝你"]:
            self.assertFalse(monitor._asks_which_liked(q), q)


class HandleMessageIntegrationTest(unittest.TestCase):
    def _client(self):
        c = SimpleNamespace(sent=[], dry_run=False)
        c.send = lambda t: c.sent.append(t) or True
        return c

    def _cfg(self):
        return SimpleNamespace(dry_run=False, telegram_chat_id="", mood_gain=1.0)

    def test_reaction_update_routes_and_is_silent(self):
        s = _state()
        s.recent_self_msgs = [{"ids": [201], "topic": "研發 X 線", "text": "…", "ts": NOW}]
        s.entropy = lifeloop.EntropyState()
        client = self._client()
        update = {"message_reaction": {"chat": {"id": 1}, "message_id": 201, "date": NOW,
                                       "new_reaction": [{"type": "emoji", "emoji": "👍"}]}}
        monitor.handle_message(update, None, None, {"meta": {}}, None, s, client, self._cfg(), None)
        self.assertEqual(s.last_liked["topic"], "研發 X 線")
        self.assertEqual(client.sent, [])                  # 反應事件本身靜默、不插話

    def test_which_liked_answer_is_grounded(self):
        s = _state()
        s.last_liked = {"message_id": 201, "emoji": "👍", "topic": "研發 writetolearn 日誌→工程化",
                        "text": "…", "valence": "positive", "ts": NOW}
        cap = {}

        def fake_reply(text, brief, hist, **kw):
            cap["brief"] = brief
            return "（回覆）你讚的是那條 writetolearn 工程化的線。"

        coach = SimpleNamespace(enabled=True, meter=SimpleNamespace(record=lambda *a, **k: None), reply=fake_reply)
        client = self._client()
        update = {"message": {"chat": {"id": 1}, "text": "你知道我點哪筆讚？", "date": NOW}}
        monitor.handle_message(update, coach, None, {"meta": {}, "records": []}, object(),
                               s, client, self._cfg(), None)
        self.assertIn("研發 writetolearn 日誌→工程化", cap["brief"])   # topic 接地進 brief
        self.assertTrue(client.sent)                                   # 有回（不是「我沒看到」）


if __name__ == "__main__":
    unittest.main()
