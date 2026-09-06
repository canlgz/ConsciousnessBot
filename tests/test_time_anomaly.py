"""⏱ 時間納入違常：同一意圖(kind)間隔太近再現＝違反常理（不靠字面相似，「現在幾點/現在的時間是」同屬 clock 也算）；
工具路徑(時間/資料)帶「你剛問過」覺察。旗標關＝逐位元同現狀。"""

import unittest
from types import SimpleNamespace

from telegram_monitor import dialogue_intent as di, monitor


def _cfg(**kw):
    base = dict(dialogue_intent_enabled=True, intent_repeat_window_sec=300, intent_sim_threshold=0.85,
                intent_repeat_n=4, anomaly_probe_threshold=0.6, intent_degree_drive=True,
                intent_question_cooldown_min=30, time_anomaly_enabled=False, time_anomaly_gap_sec=120)
    base.update(kw)
    return SimpleNamespace(**base)


class LastSameKindGapTest(unittest.TestCase):
    def test_gap_by_kind_not_words(self):
        log = [{"kind": "clock", "ts": 1000, "sig": "現在幾點"},
               {"kind": "greeting", "ts": 1010, "sig": "早安"},
               {"kind": "clock", "ts": 1040, "sig": "現在的時間是"}]
        self.assertEqual(di.last_same_kind_gap(log, "clock", 1040), 40)      # 不同字、同 kind → 算得到
        self.assertEqual(di.last_same_kind_gap(log, "greeting", 1040), 30)

    def test_none_when_no_prior(self):
        self.assertIsNone(di.last_same_kind_gap([{"kind": "clock", "ts": 1040}], "clock", 1040))
        self.assertIsNone(di.last_same_kind_gap([], "clock", 1040))

    def test_only_strictly_before_now(self):
        log = [{"kind": "clock", "ts": 1040}, {"kind": "clock", "ts": 1040}]   # 都等於 now → 無「之前」
        self.assertIsNone(di.last_same_kind_gap(log, "clock", 1040))


class ReadTimeAnomalyTest(unittest.TestCase):
    def _state(self):
        return SimpleNamespace(user_model={"intent_log": [
            {"kind": "clock", "ts": 1000, "sig": "現在幾點"},
            {"kind": "clock", "ts": 1040, "sig": "現在的時間是"}]})

    def test_rapid_same_kind_flags_warm_when_on(self):
        r = di.read(self._state(), None, 1040, _cfg(time_anomaly_enabled=True))
        self.assertEqual(r["style"], "warm")                 # 間隔 40s 太近 → 溫暖點出（即使字面不同、未達連發數）
        self.assertEqual(r["anomaly_kind"], "rapid_repeat")
        self.assertGreaterEqual(r["level"], 1)

    def test_byte_identical_when_off(self):
        r = di.read(self._state(), None, 1040, _cfg(time_anomaly_enabled=False))
        self.assertEqual(r["style"], "none")                 # 旗標關＝現狀（字面不相似、未達連發 → 無感）
        self.assertIsNone(r["anomaly_kind"])

    def test_not_flagged_when_gap_large(self):
        st = SimpleNamespace(user_model={"intent_log": [
            {"kind": "clock", "ts": 1000, "sig": "現在幾點"},
            {"kind": "clock", "ts": 1400, "sig": "現在的時間是"}]})   # 隔 400s > 120
        r = di.read(st, None, 1400, _cfg(time_anomaly_enabled=True))
        self.assertEqual(r["style"], "none")


class RecentRepeatAckTest(unittest.TestCase):
    def _state(self, gap):
        return SimpleNamespace(user_model={"intent_log": [
            {"kind": "clock", "ts": 1000}, {"kind": "clock", "ts": 1000 + gap}]})

    def test_ack_when_recent_and_on(self):
        ack = monitor._recent_repeat_ack(self._state(40), "clock", 1040,
                                         _cfg(time_anomaly_enabled=True), "時間")
        self.assertTrue(ack and "時間" in ack and "剛剛" in ack)

    def test_empty_when_off(self):
        self.assertEqual(monitor._recent_repeat_ack(self._state(40), "clock", 1040,
                                                    _cfg(time_anomaly_enabled=False), "時間"), "")

    def test_empty_when_gap_large(self):
        self.assertEqual(monitor._recent_repeat_ack(self._state(400), "clock", 1400,
                                                    _cfg(time_anomaly_enabled=True), "時間"), "")

    def test_no_digits_leaked(self):
        ack = monitor._recent_repeat_ack(self._state(40), "clock", 1040,
                                         _cfg(time_anomaly_enabled=True), "時間")
        self.assertFalse(any(ch.isdigit() for ch in ack))    # 「剛剛」不報精確秒數


if __name__ == "__main__":
    unittest.main()
