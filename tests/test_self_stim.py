"""自我刺激（回看記憶）測試：閒太久繞回舊主題、內生擾動讓 S 不再凍結成平台。"""

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

from telegram_monitor import lifeloop, monitor, selfstate
from telegram_monitor.state import State

QUIET = (1.0, 8.0, 0.5, 0.4, 0.1, 0.2, 0.0)
NOW = datetime(2026, 6, 17, 12, 0, 0, tzinfo=timezone.utc)


def _seed(ingest="T0"):
    e = lifeloop.EntropyState()
    lifeloop.entropy_update(e, QUIET, ingest)
    return e


def _ts(h):
    return (NOW - timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


class SelfStimCoreTest(unittest.TestCase):
    def test_self_stim_adds_to_charge(self):
        e = _seed()
        lifeloop.entropy_update(e, QUIET, "T0", self_stim=0.3)   # 同訊號(變化0)＋刺激0.3
        self.assertAlmostEqual(e.charge, 0.3, places=6)

    def test_self_stim_default_is_noop(self):
        a, b = _seed(), _seed()
        lifeloop.entropy_update(a, QUIET, "T0")
        lifeloop.entropy_update(b, QUIET, "T0", self_stim=0.0)
        self.assertEqual(a.charge, b.charge)

    def test_self_stim_due_cadence(self):
        e = lifeloop.EntropyState()
        for n in range(0, 21):
            e.laps_since_fresh = n
            self.assertEqual(lifeloop.self_stim_due(e), n >= 8 and n % 4 == 0, n)

    def test_revisit_magnitude(self):
        self.assertEqual(lifeloop.revisit_magnitude(None, (1, 2, 3, 4, 5)), 0.0)
        self.assertEqual(lifeloop.revisit_magnitude((1, 2, 3, 4, 5), (1, 2, 3, 4, 5)), 0.0)
        m = lifeloop.revisit_magnitude((1, 1, 1, 1, 1), (9, 5, 4, 50, 300))
        self.assertGreater(m, 0.0)
        self.assertLessEqual(m, 1.0)

    def test_fresh_state_resets_cursor(self):
        e = lifeloop.EntropyState()
        self.assertEqual(e.revisit_idx, 0)
        self.assertIsNone(e.prev_revisit_vec)
        self.assertIsNone(e.snapshot()["last_revisited"])


class RevisitHelpersTest(unittest.TestCase):
    def test_revisit_topics_distinct_stable(self):
        recs = [{"topicLabel": "乙"}, {"topicLabel": "甲"}, {"topicLabel": "乙"},
                {"topicLabel": ""}, {"topicLabel": None}, {"topicLabel": "甲"}]
        self.assertEqual(monitor._revisit_topics(recs), ["乙", "甲"])   # 去重、去空白、穩定排序

    def test_revisit_signal_cheap_fields_only(self):
        recs = [{"topicLabel": "甲", "type": "text", "ts": _ts(2), "text": "abc"},
                {"topicLabel": "甲", "type": "image", "ts": _ts(1), "text": "de"},
                {"topicLabel": "乙", "type": "text", "ts": _ts(1), "text": "x"}]
        vec = monitor._revisit_signal(recs, "甲", NOW)
        self.assertEqual(len(vec), 5)
        self.assertEqual(vec[0], 2.0)              # 筆數
        self.assertEqual(vec[2], 2.0)              # 媒材 text+image
        self.assertAlmostEqual(vec[3], 1.0, places=1)   # 最近一筆距今約 1 小時
        self.assertEqual(monitor._revisit_signal(recs, "丙", NOW), (0.0, 0.0, 0.0, 0.0, 0.0))


class IdleStretchTest(unittest.TestCase):
    """頭號測試：長時間閒置不再凍結成平台（有自我刺激 → S 起伏；無 → flatline）。"""

    def _run(self, topics):
        e = _seed()
        prev_vec, idx, series = None, 0, []
        for _ in range(60):
            stim = 0.0
            if topics and lifeloop.self_stim_due(e):
                vec = topics[idx % len(topics)]
                idx += 1
                stim = lifeloop._REVISIT_GAIN * lifeloop.revisit_magnitude(prev_vec, vec)
                prev_vec = vec
            lifeloop.entropy_update(e, QUIET, "T0", self_stim=stim)
            series.append(round(e.S(), 3))
        return series

    def test_idle_stretch_no_longer_freezes(self):
        # 取飢餓已飽和的後段（H=1.0、S 本應釘 0.6）：自我刺激讓它在 0.6 之上鋸齒起伏
        topicA = (10.0, 3.0, 2.0, 1.0, 150.0)
        topicB = (3.0, 1.0, 1.0, 40.0, 50.0)
        tail = self._run([topicA, topicB])[-20:]
        self.assertGreaterEqual(min(tail), 0.6)   # 飢餓地板
        self.assertGreater(max(tail), 0.6)        # 自我刺激造成凸起
        self.assertGreater(len(set(tail)), 1)     # 不是平台

    def test_without_self_stim_flatlines(self):
        tail = self._run(topics=None)[-20:]        # 對照：沒有自我刺激 → 仍凍結在 0.6
        self.assertEqual(set(tail), {0.6})


class RevisitWhyRoutingTest(unittest.TestCase):
    """🔁 截圖修復：問「為什麼會想到『那條』」→ 走自我刺激原因（綁 last_revisited_topic），
    不再被 selfstate-followup（也含「為什麼」）誤接成重報主線 gate（脈絡飄掉）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _state(self):
        s = State(os.path.join(self.tmp, "s.json"))
        s.owner_folder_id = "F"
        s.entropy = lifeloop.EntropyState()
        s.entropy.last_revisited_topic = "假日惠中寺行程"     # 剛自發繞回的舊線
        s.vitality = {"hunger": 0.8, "alive": True}
        # 製造「本會被 selfstate-followup 攔截」的條件：剛自陳過主線、追問窗開著
        s.self_state = {"gate": 3, "scope": {"dominant": "研發 writetolearn 日誌"}}
        s.selfstate_open_ts = 1_700_000_000
        return s

    def test_detector_binds_to_revisited_topic(self):
        self.assertTrue(selfstate.is_revisit_why_question("為什麼會想到假日惠中寺行程", "假日惠中寺行程"))
        self.assertTrue(selfstate.is_revisit_why_question("你怎麼會突然想到那條", "假日惠中寺行程"))
        self.assertFalse(selfstate.is_revisit_why_question("假日惠中寺行程是什麼", "假日惠中寺行程"))  # 沒「為什麼+想到」
        self.assertFalse(selfstate.is_revisit_why_question("為什麼會想到那條", None))                # 沒繞回主題可綁

    def test_reason_facts_are_grounded_in_self_stim(self):
        f = selfstate.revisit_reason_facts({"hunger": 0.8}, "假日惠中寺行程")
        self.assertIn("假日惠中寺行程", f)
        self.assertIn("自己", f)            # 是 bot 自發（self-stim），不是使用者帶過去的
        self.assertNotIn("gate", f.lower())  # 不吐讀數/英文鍵名

    def test_routes_to_self_stim_reason_not_main_gate(self):
        state = self._state()
        client = SimpleNamespace(sent=[], dry_run=False, send=lambda t: client.sent.append(t) or True)
        cap = {}

        def voice(topic, facts, hist):
            cap["topic"], cap["facts"] = topic, facts
            return "其實是我自己悶著繞回去翻舊的線，剛好繞到「假日惠中寺行程」那條，不是你帶我過去的。"

        coach = SimpleNamespace(enabled=True, voice_revisit_reason=voice,
                                meter=SimpleNamespace(record=lambda *a, **k: None))
        update = {"message": {"chat": {"id": 1}, "text": "為什麼會想到假日惠中寺行程", "date": 1_700_000_050}}
        # 誤走 function-calling（一般對話）不該發生 → 讓 generate_with_tools 炸，確保走的是接地自我刺激分支
        with mock.patch("telegram_monitor.gemini.generate_with_tools", side_effect=AssertionError("不該走一般對話")):
            monitor.handle_message(update, coach, None, {"meta": {}, "records": []}, object(),
                                   state, client, SimpleNamespace(dry_run=False, telegram_chat_id="", mood_gain=1.0), None)
        joined = "".join(client.sent)
        self.assertEqual(cap["topic"], "假日惠中寺行程")               # 綁到自發繞回的那條
        self.assertIn("假日惠中寺行程", joined)
        self.assertIn("自己", cap["facts"])                          # 接地：bot 自發（self-stim）
        self.assertEqual(state.self_state["scope"]["dominant"], "研發 writetolearn 日誌")  # 主線狀態沒被當答案重報
        self.assertEqual(state.focus["topic"], "假日惠中寺行程")       # 焦點綁到那條，後續「那條在講什麼」接得上


if __name__ == "__main__":
    unittest.main()
