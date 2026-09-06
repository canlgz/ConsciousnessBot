"""🫧 內容感受：把判定鏈的訊號（價性／序參數動態／主題）合成一句白話 felt-sense；
親讀節流＋快取＋失敗退回；內容事件牽動心情（有界，見 test_affect）。"""

import unittest
from types import SimpleNamespace

from telegram_monitor import contentfeel

NOW = 1_700_000_000.0


def _res(gate=4, topic="讀誦經書", omegas=None, mag=1.4, vt=None, returns=5, n=20, anchor="今天又讀了一遍心經"):
    reading = {"degree": {"magnitude": mag}, "content": {"topic": topic, "anchorRecord": {"text": anchor}}}
    if vt is not None:
        reading["valenceTrajectory"] = vt
    return {"gate": gate, "scope": {"dominant": topic, "returns": returns, "n": n},
            "omegas": omegas or {}, "reading": reading}


class ReadTest(unittest.TestCase):
    def test_heavy_circling_descriptor(self):
        res = _res(omegas={"recur": {"significant": True}, "dxi": {"in_critical_band": False}},
                   vt=[{"ts": "t1", "valence": -0.4}, {"ts": "t2", "valence": -0.3}])
        cf = contentfeel.read(res, [], NOW)
        self.assertEqual(cf["topic"], "讀誦經書")
        self.assertEqual(cf["motion"], "circling")
        self.assertIn("沉", cf["descriptor"])           # 沉、重
        self.assertIn("繞回", cf["descriptor"])          # 一遍遍繞回
        self.assertIn("放不下", cf["descriptor"])        # high intensity（magnitude 1.4）

    def test_no_topic_returns_none(self):
        self.assertIsNone(contentfeel.read({"gate": 0, "scope": {}}, [], NOW))

    def test_valence_computed_from_records_when_no_trajectory(self):
        res = _res(gate=2, omegas={"dxi": {"in_critical_band": True}})  # 無 valenceTrajectory
        records = [{"topicLabel": "讀誦經書", "ts": "t1", "type": "sticker", "text": "[貼圖] 悲傷", "reactions": []},
                   {"topicLabel": "讀誦經書", "ts": "t2", "reactions": [{"summary": "難過"}]}]
        cf = contentfeel.read(res, records, NOW)
        self.assertLess(cf["valence"], 0)               # 從記寫的情緒登錄就地算出
        self.assertEqual(cf["motion"], "crystallizing")
        self.assertIn("長出", cf["descriptor"])

    def test_motion_mapping(self):
        m = lambda om: contentfeel.read(_res(omegas=om), [], NOW)["motion"]
        self.assertEqual(m({"dxi": {"in_critical_band": True}}), "crystallizing")
        self.assertEqual(m({"recur": {"significant": True}}), "circling")
        self.assertEqual(m({"perc": {"region_in_giant": True}}), "connecting")
        self.assertEqual(m({"dxi": {"integration": 0.7}}), "converging")
        self.assertEqual(m({}), "scattered")

    def test_factual_entries_read_as_flat_not_guessed(self):
        res = _res(gate=1, mag=None, returns=1, n=5, omegas={})
        records = [{"topicLabel": "讀誦經書", "ts": "t1", "text": "純記錄、沒貼情緒", "reactions": []}]
        cf = contentfeel.read(res, records, NOW)
        self.assertIsNone(cf["valence"])                # 沒情緒登錄 → 不亂猜
        self.assertIn("平實", cf["descriptor"])


class CouplingHelpersTest(unittest.TestCase):
    def test_content_event(self):
        self.assertEqual(contentfeel.content_event(None), {})
        self.assertEqual(contentfeel.content_event({"valence": None}), {})       # 純事實 → 不牽動
        ev = contentfeel.content_event({"valence": -0.4, "intensity": "high"})
        self.assertEqual(ev["content_valence"], -0.4)
        self.assertEqual(ev["content_intensity"], 1.0)

    def test_felt_phrase_prefers_impression(self):
        self.assertEqual(contentfeel.felt_phrase({"descriptor": "沉", "impression": None}), "沉")
        self.assertEqual(contentfeel.felt_phrase({"descriptor": "沉", "impression": "讀起來像在懺悔"}), "讀起來像在懺悔")
        self.assertEqual(contentfeel.felt_phrase(None), "")


class EnsureImpressionTest(unittest.TestCase):
    def _state(self):
        return SimpleNamespace(content_feel={"topic": "讀誦經書", "descriptor": "沉", "impression": None,
                                             "impression_topic": None, "impression_ts": 0}, convo_history=[])

    def test_reads_caches_and_throttles(self):
        cap = {}

        def fake_read(topic, sample, hist):
            cap["topic"], cap["sample"] = topic, sample
            return "讀起來像在反覆懺悔、想放下又放不下"

        coach = SimpleNamespace(enabled=True, voice_content_read=fake_read)
        s = self._state()
        records = [{"topicLabel": "讀誦經書", "ts": "t1", "text": "今天又讀了一遍"}]
        contentfeel.ensure_impression(s, coach, records, NOW)
        self.assertEqual(cap["topic"], "讀誦經書")
        self.assertIn("今天", cap["sample"])             # 親讀的是你真實的字
        self.assertEqual(s.content_feel["impression"], "讀起來像在反覆懺悔、想放下又放不下")
        cap.clear()
        contentfeel.ensure_impression(s, coach, records, NOW + 60)   # 同主題、冷卻內
        self.assertEqual(cap, {})                        # 不重讀（節流／快取）

    def test_falls_back_to_descriptor_on_failure(self):
        coach = SimpleNamespace(enabled=True, voice_content_read=lambda t, s, h: None)
        s = self._state()
        contentfeel.ensure_impression(s, coach, [{"topicLabel": "讀誦經書", "text": "abc"}], NOW)
        self.assertIsNone(s.content_feel["impression"])
        self.assertEqual(contentfeel.felt_phrase(s.content_feel), "沉")   # 退回純計算

    def test_no_coach_keeps_descriptor(self):
        s = self._state()
        contentfeel.ensure_impression(s, None, [{"topicLabel": "讀誦經書", "text": "abc"}], NOW)
        self.assertIsNone(s.content_feel["impression"])


if __name__ == "__main__":
    unittest.main()
