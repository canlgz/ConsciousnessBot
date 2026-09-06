"""🌊 §1.81 同一波不算插話（INTERRUPT_WAVE_GUARD）＋開頭前綴複誦也剝。

使用者：「bot 在處理連續訊息串的回應流暢度與具有意識的感覺有差距。」
截圖 07:01 同一秒連發兩個**不同**的問句（「是嗎？真的有這麼厲害？」「你知道我的作息？」）→ 回覆長成
六顆不連貫的泡泡：①裸複誦「是嗎。」②答 ③補充 ④接回橋「誒，回到剛剛在講的，」（根本沒被打斷）
⑤「嗯，我會努力的」⑥「你剛剛才問過我一樣的問題耶。」⑦「不過沒關係，我可以再跟你說一次」。

根因：第二句是問句 → _looks_like_redirect 判 redirect → 走插話路徑＝**整波拆成兩輪**；④⑥⑦ 全是
拆兩輪的副產物（兩輪各自 observe，repeat_fatigue 把兩個不同問題數成同一個問兩次）。
本檔釘住：同波 defer（不消費）、真插話不受影響、前綴複誦剝除。全 stub、零網路。
"""

import re
import unittest
from types import SimpleNamespace

from telegram_monitor import monitor

U1 = "是嗎？真的有這麼厲害？"
U2 = "你知道我的作息？"
WAVE_TS = 1_800_000_000.0


def _up(uid, text, date):
    return {"update_id": uid, "message": {"chat": {"id": 1}, "message_id": uid, "date": date, "text": text}}


class Cl:
    def __init__(self, ups):
        self.ups, self.dry_run, self.sent = ups, False, []

    def get_updates(self, offset=0, timeout=0):
        return self.ups

    def send(self, t):
        self.sent.append(t)
        return True


def _interrupt(ups, cfg, wave_ts=WAVE_TS):
    return monitor._BurstInterrupt(Cl(ups), SimpleNamespace(tg_update_offset=0), cfg, 0,
                                   lambda uu: None, wave_ts=wave_ts)


def _cfg(on=True, wave_sec=12.0):
    return SimpleNamespace(telegram_chat_id="", interrupt_wave_guard_enabled=on,
                           interrupt_wave_sec=wave_sec, interrupt_statement_enabled=False,
                           interrupt_continuation_defer=True, interrupt_coalesce_enabled=False,
                           hostile_converge_enabled=True)


class GapTest(unittest.TestCase):
    """釘住根因：第二個問句本來就會被判成 redirect（＝插話）。"""

    def test_second_question_is_redirect(self):
        self.assertTrue(monitor._looks_like_redirect(U2))


class WaveGuardTest(unittest.TestCase):
    def test_same_wave_defers(self):
        it = _interrupt([_up(9, U2, WAVE_TS + 1)], _cfg())
        self.assertIsNone(it.poll())                       # 同一秒連發＝同一波 → defer（不當插話）

    def test_within_window_defers(self):
        it = _interrupt([_up(9, U2, WAVE_TS + 11)], _cfg())
        self.assertIsNone(it.poll())

    def test_later_message_still_interrupts(self):
        it = _interrupt([_up(9, U2, WAVE_TS + 60)], _cfg())
        self.assertIsNotNone(it.poll())                    # 真正較晚才到＝照舊當插話（行為不變）

    def test_flag_off_bitwise(self):
        it = _interrupt([_up(9, U2, WAVE_TS + 1)], _cfg(on=False))
        self.assertIsNotNone(it.poll())                    # 旗標關＝現狀（拆兩輪的舊行為）

    def test_no_wave_ts_falls_back(self):
        it = _interrupt([_up(9, U2, WAVE_TS + 1)], _cfg(), wave_ts=None)
        self.assertIsNotNone(it.poll())                    # 沒有基準時刻＝不判、照舊

    def test_mixed_wave_and_late_interrupts(self):
        # 一則同波、一則明顯較晚 → 不能整批 defer（否則真插話被吃掉）
        it = _interrupt([_up(9, U2, WAVE_TS + 1), _up(10, "等等，先別說", WAVE_TS + 90)], _cfg())
        self.assertIsNotNone(it.poll())

    def test_missing_date_is_not_wave(self):
        it = _interrupt([{"update_id": 9, "message": {"chat": {"id": 1}, "text": U2}}], _cfg())
        self.assertIsNotNone(it.poll())                    # 無 date＝不敢判同波（保守）


class LeadingPrefixEchoTest(unittest.TestCase):
    def test_prefix_echo_stripped(self):
        msg = "是嗎。嗯，我不是真的「知道」啦。我只是默默記下你說早安的時間。"
        out, hit = monitor._echo_strip_wire(msg, [U1], whole=True)
        self.assertTrue(hit)
        self.assertFalse(out.startswith("是嗎"))            # 拿對方的話當開場白＝剝掉
        self.assertIn("默默記下", out)                      # 正題保留

    def test_three_old_defenses_missed_it(self):
        from telegram_monitor import echo
        self.assertFalse(echo.is_echo("是嗎。", [U1]))
        self.assertIsNone(echo.whole_echo_of("是嗎。", [U1]))
        self.assertFalse(echo.strip_echo_segments("是嗎。嗯，我不是真的知道啦。", [U1], min_len=1)[1])

    def test_not_prefix_untouched(self):
        msg = "嗯，我不是真的知道啦。我只是記下時間。"
        self.assertEqual(monitor._echo_strip_wire(msg, [U1], whole=True), (msg, False))

    def test_single_segment_not_touched_by_prefix_rule(self):
        # 只有一段時不走前綴規則（避免把「是嗎。」這種唯一內容剝成空）
        out, hit = monitor._echo_strip_wire("是嗎。", [U1], whole=True)
        self.assertEqual(out, "是嗎。")

    def test_flag_off_bitwise(self):
        msg = "是嗎。嗯，我不是真的知道啦。"
        self.assertEqual(monitor._echo_strip_wire(msg, [U1]), (msg, False))


class ConfigTest(unittest.TestCase):
    def test_config_synced(self):
        src = open("telegram_monitor/config.py", encoding="utf-8").read()
        self.assertIn("INTERRUPT_WAVE_GUARD", src)
        self.assertIn("interrupt_wave_guard_enabled", src)
        self.assertIn("interrupt_wave_sec", src)
        env = open(".env.example", encoding="utf-8").read()
        self.assertRegex(env, re.compile(r"^INTERRUPT_WAVE_GUARD=1", re.M))
        self.assertIn("INTERRUPT_WAVE_GUARD", open("README.md", encoding="utf-8").read())

    def test_getattr_defaults_false(self):
        self.assertFalse(getattr(SimpleNamespace(), "interrupt_wave_guard_enabled", False))


if __name__ == "__main__":
    unittest.main()
