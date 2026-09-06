"""🎴 §0.98 內容型做法「讀到〈某類〉記寫→貼圖」接真：用 Gemini 語意比對記寫內容（非字面主題子字串），命中就真的送貼圖。"""

import os
import tempfile
import time
import unittest
from types import SimpleNamespace

from telegram_monitor import monitor, plasticity
from telegram_monitor.state import State


def _topic_engram(topic, value, weight=0.6, age_days=0):
    now = time.time()
    return {"kind": plasticity.KIND_SKILL, "key": f"fact_or_chat|{topic}|", "value": value,
            "weight": weight, "hits": 1, "born_ts": now - age_days * 86400, "last_ts": now - age_days * 86400}


class ActiveTopicSkillsTest(unittest.TestCase):
    def test_lists_live_topic_skills_only(self):
        eng = [_topic_engram("英文相關的記寫", "讀到英文相關的記寫時，跟我說記寫內容並貼圖"),
               _topic_engram("舊主題", "做法", age_days=10),          # 衰減 <0.5 → 不列
               {"kind": plasticity.KIND_SKILL, "key": "||always", "value": "always做法", "weight": 0.6,
                "born_ts": time.time(), "last_ts": time.time()}]     # always 型 → 不是 topic 型
        out = plasticity.active_topic_skills(eng)
        self.assertEqual([s["topic"] for s in out], ["英文相關的記寫"])
        self.assertEqual(out[0]["key"], "fact_or_chat|英文相關的記寫|")


class MaybeTopicContentStickerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _state(self, topic="英文相關的記寫", value="讀到英文相關的記寫時，跟我說記寫內容並貼圖", mood=0.3, stock=True):
        s = State(os.path.join(self.tmp, f"s{id(self)}{mood}.json"))
        s.engrams = [_topic_engram(topic, value)] if topic else []
        s.entropy = SimpleNamespace(mood=mood)
        if stock:
            s.known_sticker_ids = [{"file_id": "POS", "valence": "positive", "emoji": "😄", "desc": "笑臉", "ts": 2}]
        return s

    def _client(self):
        c = SimpleNamespace(sent=[], stickers=[], dry_run=False)
        c.send = lambda t: c.sent.append(t) or True
        c.send_sticker = lambda fid: c.stickers.append(fid) or True
        return c

    def _coach(self, matches=True):
        calls = []
        return SimpleNamespace(enabled=True, calls=calls,
                               content_matches_topic=lambda content, topic: (calls.append((content, topic)) or matches))

    def _cfg(self, **kw):
        base = dict(dry_run=False, topic_content_sticker_enabled=True, send_stickers=True,
                    sticker_cooldown_min=20, sticker_file_ids=None, sticker_rotate_window=3,
                    sticker_no_repeat_enabled=True, topic_content_max_judge=2)
        base.update(kw)
        return SimpleNamespace(**base)

    def test_sends_when_content_semantically_matches(self):
        s, c, coach = self._state(), self._client(), self._coach(matches=True)
        sent = monitor._maybe_topic_content_sticker(c, s, self._cfg(), coach, "Today I studied English idioms", time.time())
        self.assertTrue(sent)
        self.assertEqual(c.stickers, ["POS"])
        self.assertEqual(coach.calls[0][1], "英文相關的記寫")       # 用主題去語意比對內容

    def test_no_send_when_content_does_not_match(self):
        s, c, coach = self._state(), self._client(), self._coach(matches=False)
        self.assertFalse(monitor._maybe_topic_content_sticker(c, s, self._cfg(), coach, "今天去買菜", time.time()))
        self.assertEqual(c.stickers, [])

    def test_no_llm_call_when_no_such_skill(self):
        # 沒教這種做法 → 不呼叫 LLM＝零額外成本
        s, c, coach = self._state(topic=""), self._client(), self._coach()
        self.assertFalse(monitor._maybe_topic_content_sticker(c, s, self._cfg(), coach, "English notes", time.time()))
        self.assertEqual(coach.calls, [])

    def test_topic_skill_without_sticker_intent_skipped(self):
        # 做法沒有「貼圖」意圖 → 不進候選、不判斷（也不送）
        s, c, coach = self._state(value="讀到英文相關的記寫時，跟我說記寫內容"), self._client(), self._coach()
        self.assertFalse(monitor._maybe_topic_content_sticker(c, s, self._cfg(), coach, "English", time.time()))
        self.assertEqual(coach.calls, [])

    def test_suppression_skill_skipped(self):
        s, c, coach = self._state(value="讀到英文記寫時別送貼圖"), self._client(), self._coach()
        self.assertFalse(monitor._maybe_topic_content_sticker(c, s, self._cfg(), coach, "English", time.time()))
        self.assertEqual(coach.calls, [])

    def test_cooldown_blocks_before_llm(self):
        s, c, coach = self._state(), self._client(), self._coach()
        now = time.time()
        s.last_sticker_ts = now - 60
        self.assertFalse(monitor._maybe_topic_content_sticker(c, s, self._cfg(), coach, "English", now))
        self.assertEqual(coach.calls, [])                          # 冷卻先擋＝連 LLM 都不呼叫

    def test_flag_off_is_noop(self):
        s, c, coach = self._state(), self._client(), self._coach()
        self.assertFalse(monitor._maybe_topic_content_sticker(c, s, self._cfg(topic_content_sticker_enabled=False), coach, "English", time.time()))
        self.assertEqual(coach.calls, [])

    def test_use_refresh_on_match(self):
        s, c, coach = self._state(), self._client(), self._coach(matches=True)
        old = s.engrams[0]["last_ts"] = time.time() - 3 * 86400
        monitor._maybe_topic_content_sticker(c, s, self._cfg(), coach, "English", time.time())
        self.assertGreater(s.engrams[0]["last_ts"], old)

    def test_no_stock_no_emoji_fakery(self):
        s, c, coach = self._state(stock=False), self._client(), self._coach(matches=True)
        self.assertFalse(monitor._maybe_topic_content_sticker(c, s, self._cfg(), coach, "English", time.time()))
        self.assertEqual(c.sent, [])


if __name__ == "__main__":
    unittest.main()
