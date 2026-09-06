"""🌊 Multi-message delivery receipts 必須是 exact ids，不能把 max id 當連續高水位。"""

import json
import os
import tempfile
import unittest
from types import SimpleNamespace

from telegram_monitor import monitor
from telegram_monitor.state import State


def _update(uid):
    return {"update_id": uid, "message": {"text": str(uid)}}


class ExactReceiptFilterTest(unittest.TestCase):
    def test_hole_is_never_crossed_then_contiguous_receipts_advance(self):
        state = SimpleNamespace(tg_update_offset=1, burst_delivered_update_id=3,
                                burst_delivered_update_ids=[2, 3])
        got = monitor._apply_burst_delivery_receipts(
            state, [_update(1), _update(2), _update(3)])
        self.assertEqual([u["update_id"] for u in got], [1])
        self.assertEqual(state.tg_update_offset, 1)          # id1 是洞，絕不跳到 4

        state.burst_delivered_update_ids = [1, 2, 3]
        got = monitor._apply_burst_delivery_receipts(
            state, [_update(1), _update(2), _update(3)])
        self.assertEqual(got, [])
        self.assertEqual(state.tg_update_offset, 4)
        self.assertEqual(state.burst_delivered_update_ids, [1, 2, 3])

    def test_legacy_scalar_only_proves_that_one_id(self):
        state = SimpleNamespace(tg_update_offset=1, burst_delivered_update_id=3)
        got = monitor._apply_burst_delivery_receipts(
            state, [_update(1), _update(2), _update(3)])
        self.assertEqual([u["update_id"] for u in got], [1, 2])
        self.assertEqual(state.tg_update_offset, 1)
        self.assertEqual(state.burst_delivered_update_ids, [3])

    def test_returned_delivered_prefix_advances_across_random_update_id_jump(self):
        state = SimpleNamespace(tg_update_offset=0, burst_delivered_update_id=101,
                                burst_delivered_update_ids=[100, 101])
        got = monitor._apply_burst_delivery_receipts(
            state, [_update(100), _update(101)])
        self.assertEqual(got, [])
        self.assertEqual(state.tg_update_offset, 102)       # 不會每圈 offset=0 重抓後又濾空

    def test_returned_prefix_still_stops_at_first_unreceipted_parent(self):
        state = SimpleNamespace(tg_update_offset=0, burst_delivered_update_id=102,
                                burst_delivered_update_ids=[101, 102])
        got = monitor._apply_burst_delivery_receipts(
            state, [_update(100), _update(101), _update(102)])
        self.assertEqual([u["update_id"] for u in got], [100])
        self.assertEqual(state.tg_update_offset, 0)         # parent 100 是洞，不能靠後續 receipts 越過

    def test_non_monotonic_proxy_result_filters_but_never_advances_offset(self):
        state = SimpleNamespace(tg_update_offset=0, burst_delivered_update_id=101,
                                burst_delivered_update_ids=[101])
        got = monitor._apply_burst_delivery_receipts(
            state, [_update(101), _update(100)])
        self.assertEqual([u["update_id"] for u in got], [100])
        self.assertEqual(state.tg_update_offset, 0)

    def test_malformed_and_zero_ids_are_ignored(self):
        state = SimpleNamespace(tg_update_offset=0, burst_delivered_update_id=0,
                                burst_delivered_update_ids=[None, "bad", -1, 0, "7"])
        got = monitor._apply_burst_delivery_receipts(
            state, [_update(0), _update(7), {"update_id": "bad"}])
        self.assertEqual([u.get("update_id") for u in got], [0, "bad"])
        self.assertEqual(state.tg_update_offset, 0)
        self.assertEqual(state.burst_delivered_update_ids, [7])

    def test_receipts_ahead_of_a_hole_are_never_capped(self):
        state = SimpleNamespace(tg_update_offset=1, burst_delivered_update_id=0,
                                burst_delivered_update_ids=[])
        result = monitor._record_burst_delivery_receipts(state, range(2, 402))
        self.assertEqual(result, list(range(2, 402)))

    def test_nested_mixed_group_records_each_success_before_later_failure(self):
        first = {"update_id": 1, "message": {"chat": {"id": 1}, "date": 1000,
                                                "text": "先回這句"}}
        second = {"update_id": 2, "message": {"chat": {"id": 1}, "date": 1001,
                                                 "sticker": {"file_id": "S", "emoji": "🙂"}}}
        saves, handled = [], []
        state = SimpleNamespace(tg_update_offset=0, burst_delivered_update_id=0,
                                burst_delivered_update_ids=[])
        state.save = lambda: saves.append((state.tg_update_offset,
                                           list(state.burst_delivered_update_ids)))
        client = SimpleNamespace(dry_run=False)
        cfg = SimpleNamespace(interrupt_coalesce_enabled=True, burst_coalesce_sec=10.0,
                              burst_turn_gap_sec=20.0, burst_turn_max_span_sec=60.0,
                              burst_max_msgs=12, telegram_chat_id="1")

        def handle(update):
            handled.append(update["update_id"])
            if update["update_id"] == 2:
                raise RuntimeError("second failed")

        interrupt = monitor._BurstInterrupt(
            client, state, cfg, floor=0, handle_fn=handle, commit_offset=False)
        with self.assertRaisesRegex(RuntimeError, "second failed"):
            interrupt.handle([first, second])

        self.assertEqual(handled, [1, 2])
        self.assertEqual(state.tg_update_offset, 0)          # parent 洞仍在
        self.assertEqual(state.burst_delivered_update_ids, [1])
        self.assertEqual(saves, [(0, [1])])
        retry = monitor._apply_burst_delivery_receipts(state, [first, second])
        self.assertEqual([u["update_id"] for u in retry], [2])


class ReceiptPersistenceTest(unittest.TestCase):
    def test_legacy_state_migrates_scalar_conservatively(self):
        path = os.path.join(tempfile.mkdtemp(), "state.json")
        with open(path, "w", encoding="utf-8") as stream:
            json.dump({"tg_update_offset": 1, "burst_delivered_update_id": 3}, stream)
        loaded = State.load(path)
        self.assertEqual(loaded.tg_update_offset, 1)
        self.assertEqual(loaded.burst_delivered_update_ids, [3])

    def test_save_bounds_only_history_behind_offset(self):
        path = os.path.join(tempfile.mkdtemp(), "state.json")
        state = State(path)
        state.tg_update_offset = 300
        state.burst_delivered_update_ids = list(range(1, 701)) + [0, -1, "bad"]
        state.burst_delivered_update_id = 700
        state.save()
        loaded = State.load(path)
        self.assertEqual(loaded.burst_delivered_update_ids[:2], [44, 45])
        self.assertNotIn(1, loaded.burst_delivered_update_ids)
        self.assertIn(300, loaded.burst_delivered_update_ids)
        self.assertIn(700, loaded.burst_delivered_update_ids)


if __name__ == "__main__":
    unittest.main()
