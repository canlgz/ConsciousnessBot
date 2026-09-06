"""🎴 §0.96 教過的 always 常駐做法「主動回應後送情緒貼圖」→ 接真執行 lane（不再只是文字注入、真的 send_sticker）。

根因：always 型做法只被當文字注入**反應式**回覆、主動路徑根本不注入它，而「送貼圖」是 LLM 產文字做不到的機械動作
→ 教了永遠不發（/skills 列得出、卻從不觸發）。此測試驗證：教了活著→主動訊息後真送；沒教/已淡忘/抑制型/冷卻中/旗標關→不送。
"""

import os
import tempfile
import time
import unittest
from types import SimpleNamespace

from telegram_monitor import monitor, plasticity
from telegram_monitor.state import State

ALWAYS = plasticity.SKILL_TRIGGER_ALWAYS


def _engram_always(text, weight=0.6, age_days=0):
    now = time.time()
    return {"kind": plasticity.KIND_SKILL, "key": f"||{ALWAYS}", "value": text,
            "weight": weight, "hits": 1, "born_ts": now - age_days * 86400,
            "last_ts": now - age_days * 86400}


class ActiveTriggerSkillValueTest(unittest.TestCase):
    def test_finds_live_always_sticker_skill(self):
        eng = [_engram_always("主動回應使用者時，最後必須傳送一張代表自己情緒的貼圖")]
        val, key = plasticity.active_trigger_skill_value(
            eng, ALWAYS, (monitor._ALWAYS_STICKER_SEND_CUES, monitor._ALWAYS_STICKER_WORD_CUES))
        self.assertIsNotNone(val)
        self.assertEqual(key, f"||{ALWAYS}")

    def test_only_matches_the_given_trigger(self):
        # 同樣文字但存成 sit: 觸發 → 用 ALWAYS 查不到（觸發型別限定）
        e = _engram_always("送一張情緒貼圖")
        e["key"] = "|悶|sit:low_vitality"
        val, _ = plasticity.active_trigger_skill_value(
            [e], ALWAYS, (monitor._ALWAYS_STICKER_SEND_CUES, monitor._ALWAYS_STICKER_WORD_CUES))
        self.assertIsNone(val)

    def test_decayed_below_threshold_not_active(self):
        eng = [_engram_always("送一張情緒貼圖", age_days=10)]   # 0.6·0.5^(10/21) ≈ 0.43 < 0.5
        val, _ = plasticity.active_trigger_skill_value(
            eng, ALWAYS, (monitor._ALWAYS_STICKER_SEND_CUES, monitor._ALWAYS_STICKER_WORD_CUES))
        self.assertIsNone(val)


class MaybeAlwaysStickerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _state(self, skill_text="主動回應使用者時，最後必須傳送一張代表自己情緒的貼圖", mood=0.4, stock=True):
        s = State(os.path.join(self.tmp, f"s{id(self)}{mood}.json"))
        s.engrams = [_engram_always(skill_text)] if skill_text else []
        s.entropy = SimpleNamespace(mood=mood)
        if stock:
            s.known_sticker_ids = [{"file_id": "POS", "valence": "positive", "emoji": "😄", "desc": "笑臉", "ts": 2},
                                   {"file_id": "SAD", "valence": "negative", "emoji": "😢", "desc": "哭臉", "ts": 1}]
        return s

    def _client(self):
        c = SimpleNamespace(sent=[], stickers=[], dry_run=False)
        c.send = lambda t: c.sent.append(t) or True
        c.send_sticker = lambda fid: c.stickers.append(fid) or True
        return c

    def _cfg(self, **kw):
        base = dict(dry_run=False, always_sticker_enabled=True, send_stickers=True,
                    sticker_cooldown_min=20, sticker_file_ids=None, sticker_rotate_window=3,
                    sticker_no_repeat_enabled=True)
        base.update(kw)
        return SimpleNamespace(**base)

    def test_sends_emotion_sticker_when_skill_active(self):
        s, c = self._state(mood=0.4), self._client()
        self.assertTrue(monitor._maybe_always_sticker(c, s, self._cfg(), time.time()))
        self.assertEqual(c.stickers, ["POS"])                  # 心情非負 → 正向/中性 sendable 池（不含負向）
        self.assertEqual(c.sent, [])                           # 只送貼圖、不吐文字/emoji

    def test_low_mood_sends_non_positive(self):
        s, c = self._state(mood=-0.4), self._client()
        monitor._maybe_always_sticker(c, s, self._cfg(), time.time())
        self.assertEqual(c.stickers, ["SAD"])                  # 心情低 → 非正向 help 池（代表當下情緒、不硬裝開心）

    def test_no_skill_no_send(self):
        s, c = self._state(skill_text=""), self._client()
        self.assertFalse(monitor._maybe_always_sticker(c, s, self._cfg(), time.time()))
        self.assertEqual(c.stickers, [])

    def test_suppression_skill_does_not_send(self):
        s, c = self._state(skill_text="主動回應時別再送貼圖了"), self._client()
        self.assertFalse(monitor._maybe_always_sticker(c, s, self._cfg(), time.time()))
        self.assertEqual(c.stickers, [])

    def test_dormant_skill_does_not_send(self):
        s, c = self._state(), self._client()
        s.engrams = [_engram_always("送一張情緒貼圖", age_days=10)]   # 衰減到 <0.5
        self.assertFalse(monitor._maybe_always_sticker(c, s, self._cfg(), time.time()))

    def test_cooldown_blocks(self):
        s, c = self._state(), self._client()
        now = time.time()
        s.last_sticker_ts = now - 60                            # 1 分前才送過 < 20 分
        self.assertFalse(monitor._maybe_always_sticker(c, s, self._cfg(), now))

    def test_no_real_sticker_no_emoji_fakery(self):
        s, c = self._state(stock=False), self._client()
        self.assertFalse(monitor._maybe_always_sticker(c, s, self._cfg(), time.time()))
        self.assertEqual(c.sent, [])                            # 沒真貼圖＝不送，絕不退回 emoji 假裝

    def test_flag_off_is_noop(self):
        s, c = self._state(), self._client()
        self.assertFalse(monitor._maybe_always_sticker(c, s, self._cfg(always_sticker_enabled=False), time.time()))
        self.assertEqual(c.stickers, [])

    def test_use_refresh_keeps_skill_alive(self):
        s, c = self._state(), self._client()
        old_ts = s.engrams[0]["last_ts"] = time.time() - 3 * 86400
        monitor._maybe_always_sticker(c, s, self._cfg(), time.time())
        self.assertGreater(s.engrams[0]["last_ts"], old_ts)    # 用到＝保鮮（打破衰減死亡）


if __name__ == "__main__":
    unittest.main()
