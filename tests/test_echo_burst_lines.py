"""🦜 §1.82 連發合併的每一行也當複誦候選 ＋ 同波判定改「相鄰鏈」。

使用者實測回報：「實測回答不行，很不順暢！」——07:18 四連發（是嗎／我要檢查看看／你為什麼會了解
我的作息呢？／有在偷窺我嗎）→ 07:19 十二顆泡泡：前兩顆**逐字複誦**其中兩句、中間又冒出接回橋
「好，回到剛剛──」與誤判重複「哈哈哈，你又問了一次。」、最後三顆把前面答過的再答一遍。

兩個新缺口（本檔逐一釘死）：
 A. 連發合併後 text 是多行黏成的**一整塊**，個別句子從不在 echo 候選裡 → §1.74（比整則）／
    §1.81（前綴要 >2 倍長）／§1.77（exact 集合沒那行）**三道全漏**。
 B. §1.81 同波窗只錨在「正在回應的那則」，四則連打 30–40 秒就有人掉出 12 秒窗 → 整波又被拆開。
全 stub、零網路。
"""

import re
import unittest
from types import SimpleNamespace

from telegram_monitor import echo, monitor

BURST = "是嗎\n我要檢查看看\n你為什麼會了解我的作息呢？\n有在偷窺我嗎"
LINES = [ln for ln in BURST.split("\n") if ln.strip()]
REPLY = "是嗎。你為什麼會了解我的作息呢？哈哈哈，你這樣說很有趣耶！我沒有在偷窺你啦。"
WAVE = 1_800_000_000.0


class GapATest(unittest.TestCase):
    """釘住缺口 A：只有「整塊」當候選時，三道防線全漏。"""

    def test_blob_only_misses_everything(self):
        self.assertFalse(echo.is_echo(REPLY, [BURST]))
        self.assertIsNone(echo.whole_echo_of(REPLY, [BURST]))
        self.assertFalse(echo.strip_echo_segments(REPLY, [BURST], min_len=5)[1])

    def test_with_lines_it_is_caught(self):
        out, hit = echo.strip_echo_segments(REPLY, [BURST] + LINES, min_len=5)
        self.assertTrue(hit)
        self.assertNotIn("是嗎。", out)                       # 第 1 句的逐字複誦剝掉
        self.assertNotIn("你為什麼會了解我的作息呢", out)      # 第 3 句的逐字複誦也剝掉
        self.assertIn("沒有在偷窺你", out)                     # 正題保留

    def test_wire_strips_with_lines(self):
        out, hit = monitor._echo_strip_wire(REPLY, [BURST] + LINES, whole=True)
        self.assertTrue(hit)
        self.assertNotIn("你為什麼會了解我的作息呢", out)


def _up(uid, text, date):
    return {"update_id": uid, "message": {"chat": {"id": 1}, "message_id": uid, "date": date, "text": text}}


class Cl:
    def __init__(self, ups):
        self.ups, self.dry_run = ups, False

    def get_updates(self, offset=0, timeout=0):
        return self.ups

    def send(self, t):
        return True


def _cfg(on=True, wave_sec=12.0):
    return SimpleNamespace(telegram_chat_id="", interrupt_wave_guard_enabled=on,
                           interrupt_wave_sec=wave_sec, interrupt_statement_enabled=False,
                           interrupt_continuation_defer=True, interrupt_coalesce_enabled=False,
                           hostile_converge_enabled=True)


def _it(ups, cfg, wave_ts=WAVE):
    return monitor._BurstInterrupt(Cl(ups), SimpleNamespace(tg_update_offset=0), cfg, 0,
                                   lambda uu: None, wave_ts=wave_ts)


class GapBChainTest(unittest.TestCase):
    def test_chain_of_four_stays_one_wave(self):
        # 四則各距前一則 10 秒（總跨度 30 秒 > 12 秒窗）——舊規則會判插話，相鄰鏈要判同一波
        ups = [_up(9 + i, LINES[i], WAVE + 10 * (i + 1)) for i in range(3)]
        it = _it(ups, _cfg())
        self.assertIsNone(it.poll())
        self.assertEqual(it.wave_ts, WAVE + 30)               # 錨點推進到最新那則

    def test_gap_breaks_chain(self):
        # 中間隔了 90 秒＝真的是後來才想到要打斷 → 照舊當插話
        ups = [_up(9, LINES[0], WAVE + 5), _up(10, LINES[1], WAVE + 95)]
        self.assertIsNotNone(_it(ups, _cfg()).poll())

    def test_single_late_message_interrupts(self):
        self.assertIsNotNone(_it([_up(9, LINES[2], WAVE + 60)], _cfg()).poll())

    def test_flag_off_bitwise(self):
        ups = [_up(9 + i, LINES[i], WAVE + 10 * (i + 1)) for i in range(3)]
        self.assertIsNotNone(_it(ups, _cfg(on=False)).poll())

    def test_anchor_not_advanced_when_not_chained(self):
        ups = [_up(9, LINES[0], WAVE + 200)]
        it = _it(ups, _cfg())
        it.poll()
        self.assertEqual(it.wave_ts, WAVE)                    # 沒同波＝錨點不動


class ConfigTest(unittest.TestCase):
    def test_config_synced(self):
        src = open("telegram_monitor/config.py", encoding="utf-8").read()
        self.assertIn("ECHO_BURST_LINES", src)
        self.assertIn("echo_burst_lines_enabled", src)
        env = open(".env.example", encoding="utf-8").read()
        self.assertRegex(env, re.compile(r"^ECHO_BURST_LINES=1", re.M))
        self.assertIn("ECHO_BURST_LINES", open("README.md", encoding="utf-8").read())

    def test_getattr_defaults_false(self):
        self.assertFalse(getattr(SimpleNamespace(), "echo_burst_lines_enabled", False))


if __name__ == "__main__":
    unittest.main()
