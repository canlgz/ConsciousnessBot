import unittest
from telegram_monitor import foresight, worldline


class EvidenceBoundaryTest(unittest.TestCase):
    def test_future_records_do_not_activate_lines(self):
        rows = [{"topicLabel": "A", "_ts": 110, "text": "future"}]
        self.assertEqual(foresight.line_spans(rows, 100), {})

    def test_prediction_only_counts_observed_records_in_promised_window(self):
        hyp = {"b": "A", "born_ts": 100, "told_ts": 120, "ttl_s": 30}
        def result(ts, now=160):
            return foresight.verdict(hyp, [{"topicLabel": "A", "_ts": ts,
                                           "text": "actual", "id": "x"}], now)[0]
        self.assertEqual(result(110), "miss")  # before actually telling user
        self.assertEqual(result(151), "miss")  # after deadline
        self.assertEqual(result(140, 130), "open")  # future record
        self.assertEqual(result(140), "hit")

    def test_explicit_old_line_keeps_allowlist_and_repeat_limit(self):
        now = 1700000000
        rows = [{"topicLabel": "A", "_ts": now - 80 * 86400,
                 "text": "一段有具體內容的私人記寫", "id": "a"},
                {"topicLabel": "A", "_ts": now - 79 * 86400,
                 "text": "另一段有具體內容的私人記寫", "id": "b"}]
        spans = foresight.line_spans(rows, now)
        self.assertEqual(worldline.pick_lines(rows, spans, {"A"}, {}, now), [])
        self.assertEqual(len(worldline.pick_lines(rows, spans, {"A"}, {}, now,
                                                 include_dormant=True)), 1)
        self.assertEqual(worldline.pick_lines(rows, spans, set(), {}, now,
                                             include_dormant=True), [])
        self.assertEqual(worldline.pick_lines(rows, spans, {"A"}, {"A": now-1}, now,
                                             include_dormant=True), [])
        self.assertNotIn("私人記寫", worldline.build_query("A"))

    def test_sources_require_web_address(self):
        for url in ("", "invented", "file:///secret", "javascript:alert(1)", "http://["):
            self.assertFalse(worldline.sources_ok([("source", url)]))
        self.assertTrue(worldline.sources_ok([("source", "https://example.org/paper")]))

    def test_aftermath_is_not_future_knowledge(self):
        rows = [{"topicLabel": "A", "_ts": 200}]
        ledger = [{"label": "A", "ts": 100}]
        self.assertEqual(worldline.aftermath(ledger, rows, 150)[0][1], 0)
