"""🧬 可塑層 Phase 3：路由更正記憶——被糾正（看不懂/不是這個意思/我是說…）後改寫到明確路由，
就把「那種句式 → 正確 route」學起來；約兩次一致才生效（安全），只救泛用 fallback（fact_or_chat）、絕不劫持明確意向。"""

import os
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from telegram_monitor import intent, monitor, plasticity, referent
from telegram_monitor.state import State

NOW = 1_700_000_000


class DetectTest(unittest.TestCase):
    def test_dissatisfaction(self):
        for q in ["看不懂", "我不是這個意思", "你又會錯意了", "我是說多久沒聊", "答非所問啦", "我問的不是這個"]:
            self.assertTrue(plasticity.is_dissatisfaction(q), q)
        for q in ["好喔", "現在幾點", "你最近怎樣", "幫我看今天"]:
            self.assertFalse(plasticity.is_dissatisfaction(q), q)

    def test_signature_normalizes_and_min_len(self):
        self.assertEqual(plasticity.signature("多久沒，甩我？"), "多久沒甩我")
        self.assertIsNone(plasticity.signature("嗯嗯"))          # 太短 → None（不亂學）


class LearnRecallTest(unittest.TestCase):
    def test_rejects_unsafe_route(self):
        eng = []
        self.assertFalse(plasticity.learn_correction(eng, "多久沒甩我啊", "self_identity", now_ts=NOW))
        self.assertEqual(eng, [])                                # 不安全路由不學

    def test_needs_two_consistent_to_activate(self):
        eng = []
        plasticity.learn_correction(eng, "多久沒甩我啊", "convo_time", now_ts=NOW)
        self.assertIsNone(plasticity.recall_route(eng, "我多久沒甩我啊", now_ts=NOW))   # 一次 → 未達門檻、不生效
        plasticity.learn_correction(eng, "多久沒甩我啊", "convo_time", now_ts=NOW)
        self.assertEqual(plasticity.recall_route(eng, "我多久沒甩我啊了", now_ts=NOW), "convo_time")  # 兩次一致 → 生效（子字串命中）

    def test_recall_decays_away(self):
        eng = []
        for _ in range(2):
            plasticity.learn_correction(eng, "多久沒甩我啊", "convo_time", now_ts=NOW)
        self.assertEqual(plasticity.recall_route(eng, "多久沒甩我啊", now_ts=NOW), "convo_time")
        self.assertIsNone(plasticity.recall_route(eng, "多久沒甩我啊", now_ts=NOW + 200 * 86400))   # 久未強化 → 淡忘


class IntentOverrideTest(unittest.TestCase):
    def _eng_active(self):
        eng = []                                          # intent.resolve 用真實 time.time() 算衰減 → 印痕得「現在」才新鮮
        for _ in range(2):
            plasticity.learn_correction(eng, "多久沒甩我", "convo_time", now_ts=time.time())
        return eng

    def test_overrides_only_fallback(self):
        eng = self._eng_active()
        # 自然路由是 fact_or_chat（「甩我」沒被任何 regex 認出）→ 被學到的更正救起來
        self.assertEqual(intent.resolve("我多久沒甩我了", referent.Referent(), eng).kind, "convo_time")

    def test_never_hijacks_definite_intent(self):
        eng = self._eng_active()
        # 即使句中含已學 key，但自然路由是明確意向（身分）→ 絕不被覆蓋
        self.assertEqual(intent.resolve("你是誰，多久沒甩我", referent.Referent(), eng).kind, "self_identity")

    def test_no_engrams_no_override(self):
        self.assertEqual(intent.resolve("我多久沒甩我了", referent.Referent(), None).kind, "fact_or_chat")


class CaptureTest(unittest.TestCase):
    """端到端 capture：誤路由 → 不滿＋改寫 → 學起來（engram 出現）。"""

    def _coach(self):
        return SimpleNamespace(enabled=True, meter=SimpleNamespace(record=lambda *a, **k: None),
                               reply=lambda *a, **k: "好。", ask=lambda *a, **k: ("chat", None, "嗯。"))

    def _send(self, state, coach, client, text, t):
        up = {"message": {"chat": {"id": 1}, "text": text, "date": t}}
        with mock.patch("telegram_monitor.coach.build_memory_brief", return_value=""), \
             mock.patch("telegram_monitor.monitor.time.time", return_value=t):
            monitor.handle_message(up, coach, None, {"meta": {}, "records": []}, None,
                                   state, client, SimpleNamespace(dry_run=False, telegram_chat_id="", mood_gain=1.0), None)

    def test_dissat_then_rephrase_learns(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        coach, client = self._coach(), SimpleNamespace(sent=[], dry_run=False,
                                                       send=lambda t: client.sent.append(t) or True)
        # T1：怪句式「多久沒甩我」→ 自然 fact_or_chat（被誤路由）
        self._send(s, coach, client, "多久沒甩我", NOW)
        self.assertEqual(s.last_routed["kind"], "fact_or_chat")
        # T2：不滿＋改寫同一句「我是說，多久沒聊」（dissat＋convo_time）→ 當場學 bad_text→convo_time
        self._send(s, coach, client, "我是說，多久沒聊", NOW + 30)
        corr = [e for e in s.engrams if e["kind"] == "correction"]
        self.assertTrue(corr, "應學到一條路由更正")
        self.assertEqual(corr[0]["key"], "多久沒甩我")
        self.assertEqual(corr[0]["value"], "convo_time")

    def test_forget_clears(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        s.engrams = [{"kind": "correction", "key": "多久沒甩我", "value": "convo_time",
                      "weight": 0.9, "hits": 2, "born_ts": NOW, "last_ts": NOW}]
        client = SimpleNamespace(sent=[], dry_run=False, send=lambda t: client.sent.append(t) or True)
        self._send(s, self._coach(), client, "/forget", NOW)
        self.assertEqual(s.engrams, [])
        self.assertIn("忘掉", "".join(client.sent))


if __name__ == "__main__":
    unittest.main()
