"""🔁 §0.56 回覆前防重複提示：
① 連發多句被合併成一輪（含換行／多問號）→「合起來答一次、重疊別逐句重講」；
② 這幾分鐘內剛回過（相鄰不久的輪）→ 最近幾則 bot 摘錄「別重講、只補沒說到的」。
只作用在相鄰不久的輪；旗標關＝回 ''＝逐位元同現狀。純函式，離線可測。"""

import unittest
from types import SimpleNamespace

from telegram_monitor import monitor


NOW = 1_700_000_000


def _cfg(enabled=True, window_min=8):
    return SimpleNamespace(anti_repeat_enabled=enabled, anti_repeat_window_min=window_min)


def _st(history):
    return SimpleNamespace(convo_history=history)


class RecentModelTurnsTest(unittest.TestCase):
    def test_returns_recent_model_turns_in_window_oldest_first(self):
        hist = [
            {"role": "user", "text": "u1", "ts": NOW - 300},
            {"role": "model", "text": "m1", "ts": NOW - 250},
            {"role": "user", "text": "u2", "ts": NOW - 200},
            {"role": "model", "text": "m2", "ts": NOW - 150},
        ]
        out = monitor._recent_model_turns(hist, NOW, 600, k=3)
        self.assertEqual(out, ["m1", "m2"])          # 時序（舊→新）、略過 user

    def test_stops_at_window_boundary(self):
        hist = [
            {"role": "model", "text": "old", "ts": NOW - 1000},   # 窗外
            {"role": "model", "text": "new", "ts": NOW - 100},    # 窗內
        ]
        self.assertEqual(monitor._recent_model_turns(hist, NOW, 600, k=3), ["new"])

    def test_caps_at_k(self):
        hist = [{"role": "model", "text": f"m{i}", "ts": NOW - i} for i in range(6)]
        out = monitor._recent_model_turns(hist, NOW, 600, k=3)
        self.assertEqual(len(out), 3)

    def test_empty(self):
        self.assertEqual(monitor._recent_model_turns([], NOW, 600), [])
        self.assertEqual(monitor._recent_model_turns(None, NOW, 600), [])

    def test_non_monotonic_ts_does_not_early_break(self):
        # 🔴 審查 med 回歸：user 輪存 message.date、model 輪存 time.time()，崩潰補抓時 ts 非單調——
        # 夾在中間的超窗 user 輪不得用 break 截斷、漏掉真正在窗內的 model 輪（改 continue 逐則個別判窗）。
        hist = [
            {"role": "user", "text": "u_old", "ts": NOW - 7000},   # 超窗
            {"role": "model", "text": "m_in", "ts": NOW - 60},     # 窗內
            {"role": "user", "text": "u_backfill", "ts": NOW - 7000},  # 超窗（夾在中間）
            {"role": "model", "text": "m_in2", "ts": NOW - 30},    # 窗內
        ]
        self.assertEqual(monitor._recent_model_turns(hist, NOW, 600, k=3), ["m_in", "m_in2"])

    def test_window_zero_returns_empty(self):
        # 🔴 審查 low：window≤0＝關此提示（0 分＝沒有輪在窗內），非「無限窗」
        hist = [{"role": "model", "text": "m", "ts": NOW - 10}]
        self.assertEqual(monitor._recent_model_turns(hist, NOW, 0), [])


class AntiRepeatHintTest(unittest.TestCase):
    def test_coalesced_multi_gets_multi_hint(self):
        h = monitor._anti_repeat_hint(_st([]), "你為什麼在意這則\n它很特別嗎", NOW, _cfg())
        self.assertIn("合起來", h)

    def test_multi_question_marks_gets_multi_hint(self):
        h = monitor._anti_repeat_hint(_st([]), "為什麼？它特別嗎？", NOW, _cfg())
        self.assertIn("合起來", h)

    def test_recent_turn_gets_recent_hint_with_excerpt(self):
        hist = [{"role": "model", "text": "它算是你投入很多、持續很久的一條線", "ts": NOW - 100}]
        h = monitor._anti_repeat_hint(_st(hist), "它很特別嗎", NOW, _cfg())
        self.assertIn("剛對這串說過", h)
        self.assertIn("投入很多", h)                  # 摘錄真的 bot 內容進去

    def test_old_turn_single_msg_is_empty(self):
        hist = [{"role": "model", "text": "舊回覆", "ts": NOW - 1200}]   # 20 分前、窗外
        self.assertEqual(monitor._anti_repeat_hint(_st(hist), "它很特別嗎", NOW, _cfg()), "")

    def test_both_conditions_both_hints(self):
        hist = [{"role": "model", "text": "剛講過的內容", "ts": NOW - 60}]
        h = monitor._anti_repeat_hint(_st(hist), "為什麼呢\n它很特別嗎", NOW, _cfg())
        self.assertIn("合起來", h)
        self.assertIn("剛對這串說過", h)

    def test_flag_off_empty(self):
        hist = [{"role": "model", "text": "剛講過", "ts": NOW - 60}]
        self.assertEqual(monitor._anti_repeat_hint(_st(hist), "為什麼呢\n它很特別嗎", NOW, _cfg(enabled=False)), "")

    def test_single_msg_no_recent_is_empty(self):
        self.assertEqual(monitor._anti_repeat_hint(_st([]), "它很特別嗎", NOW, _cfg()), "")

    def test_multiline_statement_without_question_no_multi_hint(self):
        # 🔴 審查 low：多行純陳述（無問句線索）不套「合起來答一次」提示（那是給多問句用的）
        h = monitor._anti_repeat_hint(_st([]), "今天去了寺廟\n覺得很平靜\n想跟你說", NOW, _cfg())
        self.assertEqual(h, "")

    def test_window_zero_disables_recent_component(self):
        hist = [{"role": "model", "text": "剛講過", "ts": NOW - 60}]
        # 只有 recent component 會觸發（單句無多問）→ window 0 關掉它 → 空
        self.assertEqual(monitor._anti_repeat_hint(_st(hist), "它很特別嗎", NOW, _cfg(window_min=0)), "")


if __name__ == "__main__":
    unittest.main()
