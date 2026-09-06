"""🤐 未回覆的主動話題閘：沉默要跨重啟留得住。"""

import json
import os
import tempfile
import unittest

from telegram_monitor.state import State


class UnansweredProactiveStateTest(unittest.TestCase):
    def test_last_user_time_persists(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "state.json")
            state = State(path)
            state.last_user_msg_ts = 1234.5
            state.last_push_ts = 1300.0
            state.save()

            reborn = State.load(path)
            self.assertEqual(reborn.last_user_msg_ts, 1234.5)
            self.assertEqual(reborn.last_push_ts, 1300.0)

    def test_old_state_recovers_last_user_time_from_history(self):
        # 舊版尚未保存 last_user_msg_ts；升級後不能因一次重啟就把仍未回覆的話頭忘掉。
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "state.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"last_push_ts": 1300.0, "convo_history": [
                    {"role": "user", "text": "早", "ts": 1200.0},
                    {"role": "model", "text": "我在。", "ts": 1300.0},
                ]}, fh)

            self.assertEqual(State.load(path).last_user_msg_ts, 1200.0)


if __name__ == "__main__":
    unittest.main()
