"""🎬 §1.69 暖收收尾去制式（WRAP_CLOSE_NATURAL）：收尾融語境、多元，不再每次掛同款收場白。

截圖根因：同一輪兩個泡泡尾端各掛「大概就是這樣了。」「先說到這裡。」——使用者：「太有被插話的痕跡了…
應視情況改為更融合原來語境的文字詞，也許是 bot 表達自己的感覺、或語助詞……多元一點」。
真根因：wrap_condense_user 的 prompt 把『大概就是這樣了』『先說到這』當**範例**給 LLM——範例被逐字
抄回、每次同款收場白。修：① LLM 側 prompt 改「禁令＋多元收法」（自然停住/情緒餘韻/語助詞）；
② 無教練模板退路 _pick_wrap_close 依此刻座標 V 分暗/平/亮三池、沿用避免連續重複。
旗標關＝原 prompt＋原四句池＝逐位元同現狀。全 stub、零網路。
"""

import re
import unittest
from types import SimpleNamespace

from telegram_monitor import monitor, persona


class PromptTest(unittest.TestCase):
    def test_natural_bans_stock_closers(self):
        p = persona.wrap_condense_user("剩餘的內容", natural=True)
        self.assertIn("禁用制式收場白", p)
        self.assertIn("大概就是這樣", p)                        # 禁令裡點名（不是範例）
        self.assertIn("先說到這", p)
        self.assertIn("語助詞", p)                              # 多元收法：語助詞
        self.assertIn("情緒餘韻", p)                            # 多元收法：餘韻
        self.assertIn("自然停住", p)                            # 多元收法：不加收場白
        self.assertNotIn("這種**陳述式**淡收", p)               # 舊「給範例」句不再出現

    def test_natural_keeps_existing_bans(self):
        p = persona.wrap_condense_user("剩餘的內容", natural=True)
        self.assertIn("你說？", p)                              # 反問收尾禁令保留
        self.assertIn("我先收著", p)                            # 收話動作宣告禁令保留
        self.assertIn("剩餘的內容", p)                          # 濃縮素材帶到

    def test_default_prompt_unchanged(self):
        # 旗標關＝原 prompt 逐位元（範例句仍在——釘住現狀，§1.69 只在 natural=True 才改）
        p = persona.wrap_condense_user("剩餘的內容")
        self.assertIn("像『大概就是這樣了』『先說到這』這種**陳述式**淡收", p)
        self.assertNotIn("禁用制式收場白", p)


class PickCloseTest(unittest.TestCase):
    def _interrupt(self, v=0.0):
        return SimpleNamespace(state=SimpleNamespace(entropy=SimpleNamespace(mood=v, arousal=0.0)),
                               _last_close=None)

    def _cfg(self, on=True):
        return SimpleNamespace(wrap_close_natural_enabled=on)

    def test_low_mood_dark_pool(self):
        it = self._interrupt(v=-0.5)
        for _ in range(6):
            self.assertIn(monitor._pick_wrap_close(it, self._cfg()), monitor._REWRITE_CLOSE_LOW)

    def test_high_mood_bright_pool(self):
        it = self._interrupt(v=0.6)
        for _ in range(6):
            self.assertIn(monitor._pick_wrap_close(it, self._cfg()), monitor._REWRITE_CLOSE_HIGH)

    def test_neutral_mid_pool(self):
        it = self._interrupt(v=0.0)
        for _ in range(6):
            self.assertIn(monitor._pick_wrap_close(it, self._cfg()), monitor._REWRITE_CLOSE_MID)

    def test_no_state_safe_mid(self):
        it = SimpleNamespace(state=None, _last_close=None)      # 無 state＝position 回 (0,0)＝平池、不炸
        self.assertIn(monitor._pick_wrap_close(it, self._cfg()), monitor._REWRITE_CLOSE_MID)

    def test_no_consecutive_repeat(self):
        it = self._interrupt(v=-0.5)
        prev = None
        for _ in range(12):
            cur = monitor._pick_wrap_close(it, self._cfg())
            self.assertNotEqual(cur, prev)                      # 避免連續重複（沿用 _last_close 機制）
            prev = cur

    def test_flag_off_original_pool(self):
        it = self._interrupt(v=-0.9)                            # 就算心情很沉
        for _ in range(6):
            self.assertIn(monitor._pick_wrap_close(it, self._cfg(on=False)),
                          monitor._REWRITE_CLOSE_LINES)         # 旗標關＝原四句池＝現狀


class CoachPassthroughTest(unittest.TestCase):
    def test_disabled_coach_none(self):
        c = object.__new__(__import__("telegram_monitor.coach", fromlist=["Coach"]).Coach)
        c.enabled = False
        self.assertIsNone(c.voice_wrap_condense("x", None, natural=True))   # 簽名收 natural、不炸


class ConfigTest(unittest.TestCase):
    def test_config_synced(self):
        src = open("telegram_monitor/config.py", encoding="utf-8").read()
        self.assertIn("WRAP_CLOSE_NATURAL", src)
        self.assertIn("wrap_close_natural_enabled", src)
        env = open(".env.example", encoding="utf-8").read()
        self.assertRegex(env, re.compile(r"^WRAP_CLOSE_NATURAL=1", re.M))
        self.assertIn("WRAP_CLOSE_NATURAL", open("README.md", encoding="utf-8").read())

    def test_getattr_defaults_false(self):
        self.assertFalse(getattr(SimpleNamespace(), "wrap_close_natural_enabled", False))


if __name__ == "__main__":
    unittest.main()
