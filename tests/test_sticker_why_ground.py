"""🎴 §1.16 複合請求：送圖＋據心情說明為什麼（STICKER_WHY_GROUND）。
「送一張代表你此刻心情的貼圖，說說為什麼」＝兩件事：① 真的送出一張 ② 依此刻情緒說明為什麼是這張。
現行只有罐頭 sticker_send_reply（「來，這張真貼圖送你 :)」）——第二件事完全沒做。

接地素材＝circumplex 單一真相：選圖與說明讀**同一份 (V,A)**（circumplex.position/label/sticker_pool），
否則同回覆自相矛盾。絕不描述不存在的貼圖：desc 只有 stickervision.is_seen 才准入描述句。
無貨＝誠實說沒存到＋請對方教一張，絕不用 emoji 假裝（§0.95）。

全 stub、零網路（judge_sticker_request 由假 coach 回可控值；circumplex 靠設 entropy.mood/arousal 驅動）。
"""

import os
import tempfile
import unittest
from types import SimpleNamespace

from telegram_monitor import circumplex, lifeloop, monitor
from telegram_monitor.state import State

NOW = 1_800_000_000.0

S_COMPOUND = "有代表現在的你的心情的貼圖嗎，說說為什麼"


class FakeClient:
    def __init__(self):
        self.sent, self.stickers, self.dry_run = [], [], False

    def send(self, text):
        self.sent.append(text)
        return True

    def send_sticker(self, file_id):
        self.stickers.append(file_id)
        return True


def _coach(judge):
    c = SimpleNamespace(enabled=True, api_key="k", model="m",
                        meter=SimpleNamespace(record=lambda *a, **k: None), judge_calls=[])

    def _j(text):
        c.judge_calls.append(text)
        return judge(text)

    c.judge_sticker_request = _j
    return c


def _cfg(**over):
    base = dict(dry_run=False, sticker_llm_rescue_enabled=True, sticker_why_ground_enabled=True,
                sticker_send_request_enabled=True, liked_sticker_pick_enabled=True,
                sticker_no_repeat_enabled=True, sticker_rotate_window=3, affect_circumplex_enabled=True,
                sticker_file_ids=None)
    base.update(over)
    return SimpleNamespace(**base)


class WhyGroundTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _state(self, mood=0.4, arousal=0.4, desc="", emoji=""):
        s = State(os.path.join(self.tmp, "s.json"))
        s.known_sticker_ids = [{"file_id": "CHICKEN_FID", "file_unique_id": "U1",
                                "emoji": emoji, "valence": "neutral", "ts": NOW, "desc": desc}]
        s.last_sticker_ts = NOW
        s.entropy = lifeloop.EntropyState()
        s.entropy.mood, s.entropy.arousal = mood, arousal
        return s

    # ───────── 複合請求兩件事都做：送圖 ＋ 依 circumplex 說明為什麼 ─────────
    def test_compound_sends_and_explains(self):
        st, cl = self._state(mood=0.4, arousal=0.4), FakeClient()   # 雀躍（positive 池）、雞中性 desc=''
        co = _coach(judge=lambda t: (True, True))                   # 是＋要說為什麼
        ok = monitor._maybe_llm_sticker_rescue(cl, st, _cfg(), co,
                                               SimpleNamespace(kind="self_state"), S_COMPOUND, NOW)
        self.assertTrue(ok)
        self.assertEqual(cl.stickers, ["CHICKEN_FID"])              # ① 送了圖
        reply = cl.sent[-1]
        label = circumplex.label(*circumplex.position(st))
        self.assertIn(label, reply)                                 # ② 用此刻 circumplex 情緒詞（如「興奮、雀躍」）
        self.assertTrue("憑" in reply or "看不到" in reply, reply)  # desc='' → 誠實接地（憑感覺挑／看不到圖案）

    # ───────── 單一真相：情緒詞來自 circumplex.label；改 entropy 到平靜 → 用詞跟著變 ─────────
    def test_single_truth_label_follows_circumplex(self):
        st, cl = self._state(mood=0.3, arousal=-0.3), FakeClient()  # 平靜、安穩
        co = _coach(judge=lambda t: (True, True))
        monitor._maybe_llm_sticker_rescue(cl, st, _cfg(), co,
                                          SimpleNamespace(kind="self_state"), S_COMPOUND, NOW)
        label = circumplex.label(*circumplex.position(st))
        self.assertIn("平靜", label)                                # 前提：真是平靜系
        self.assertIn(label, cl.sent[-1])

    # ───────── 不匹配誠實（19:34 雞對照）：想要正向池卻只有中性雞 → 誠實『不完全一樣』且仍送出 ─────────
    def test_mismatch_honest_still_sends(self):
        st, cl = self._state(mood=0.5, arousal=0.5), FakeClient()   # 雀躍＝positive 池；只有中性雞
        self.assertEqual(circumplex.sticker_pool(*circumplex.position(st)), "positive")
        co = _coach(judge=lambda t: (True, True))
        monitor._maybe_llm_sticker_rescue(cl, st, _cfg(), co,
                                          SimpleNamespace(kind="self_state"), S_COMPOUND, NOW)
        self.assertEqual(cl.stickers, ["CHICKEN_FID"])             # 有貨必送
        self.assertIn("不完全一樣", cl.sent[-1])                    # 誠實：感覺不完全一樣

    # ───────── 絕不捏造畫面：desc='' → 回覆不得出現任何圖案描述詞 ─────────
    def test_no_fabricated_picture_when_desc_empty(self):
        st, cl = self._state(mood=0.4, arousal=0.4, desc=""), FakeClient()
        co = _coach(judge=lambda t: (True, True))
        monitor._maybe_llm_sticker_rescue(cl, st, _cfg(), co,
                                          SimpleNamespace(kind="self_state"), S_COMPOUND, NOW)
        reply = cl.sent[-1]
        for fake in ("蝴蝶", "向日葵", "小狗", "花"):
            self.assertNotIn(fake, reply)

    # ───────── 真看過畫面：is_seen True → 回覆才准出現該 desc 字串 ─────────
    def test_seen_desc_appears_in_reply(self):
        seen = "一隻紅色的雞站著"
        st, cl = self._state(mood=0.4, arousal=0.4, desc=seen), FakeClient()
        co = _coach(judge=lambda t: (True, True))
        monitor._maybe_llm_sticker_rescue(cl, st, _cfg(), co,
                                          SimpleNamespace(kind="self_state"), S_COMPOUND, NOW)
        self.assertIn(seen, cl.sent[-1])

    # ───────── 無貨誠實：sendable 空 → 沒硬送、誠實說沒存到＋請對方教＋不 emoji 假裝 ─────────
    def test_no_stock_honest_no_fake(self):
        st, cl = self._state(mood=0.4, arousal=0.4), FakeClient()
        st.known_sticker_ids = []                                   # 清空 → sendable 空
        co = _coach(judge=lambda t: (True, True))
        ok = monitor._maybe_llm_sticker_rescue(cl, st, _cfg(), co,
                                               SimpleNamespace(kind="self_state"), S_COMPOUND, NOW)
        self.assertTrue(ok)                                        # 已接管此則（誠實說沒貨）
        self.assertEqual(cl.stickers, [])                          # 沒硬送
        reply = cl.sent[-1]
        label = circumplex.label(*circumplex.position(st))
        self.assertIn(label, reply)
        self.assertTrue("還沒存到" in reply or "傳一張" in reply, reply)
        # §0.95：不得以 emoji 冒充貼圖（誠實聲明 emoji 不是貼圖／不拿它假裝）
        self.assertIn("不是貼圖", reply)

    # ───────── 確定性 why 備援：judge 漏判要不要說（回 wants_why=False）但句含「為什麼」→ 仍走 grounded ─────────
    def test_deterministic_why_fallback(self):
        st, cl = self._state(mood=0.4, arousal=0.4), FakeClient()
        co = _coach(judge=lambda t: (True, False))                 # LLM 漏判要不要說為什麼
        monitor._maybe_llm_sticker_rescue(cl, st, _cfg(), co,
                                          SimpleNamespace(kind="self_state"), S_COMPOUND, NOW)  # 句含「為什麼」
        reply = cl.sent[-1]
        label = circumplex.label(*circumplex.position(st))
        self.assertIn(label, reply)                                # _WHY_RE 兜住 → 仍 grounded（含 label）

    # ───────── 旗標關（§1.16）：送圖仍發生但回覆＝罐頭 sticker_send_reply（不含 label）＝逐位元同 §1.15-only ─────────
    def test_why_ground_flag_off_falls_back_to_canned(self):
        st, cl = self._state(mood=0.4, arousal=0.4), FakeClient()
        co = _coach(judge=lambda t: (True, True))
        monitor._maybe_llm_sticker_rescue(cl, st, _cfg(sticker_why_ground_enabled=False), co,
                                          SimpleNamespace(kind="self_state"), S_COMPOUND, NOW)
        self.assertEqual(cl.stickers, ["CHICKEN_FID"])             # §1.15 送圖仍發生
        reply = cl.sent[-1]
        label = circumplex.label(*circumplex.position(st))
        self.assertNotIn(label, reply)                             # 罐頭句不含情緒 label
        self.assertIn("真貼圖", reply)                              # ＝persona.sticker_send_reply(True) 罐頭


if __name__ == "__main__":
    unittest.main()
