"""🧬 可塑層（一生之內的學習）：相處偏好被學起來、強化/遺忘/固化、跨重生持久、並注入往後回覆的 grounding。"""

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from telegram_monitor import coach as coachmod, monitor, plasticity
from telegram_monitor.state import State

NOW = 1_700_000_000


class SubstrateTest(unittest.TestCase):
    def test_reinforce_adds_then_saturates(self):
        eng = []
        plasticity.reinforce(eng, plasticity.KIND_PREF, "length", value="講話簡短一點", now_ts=NOW)
        self.assertEqual(len(eng), 1)
        self.assertEqual(eng[0]["hits"], 1)
        w1 = eng[0]["weight"]
        plasticity.reinforce(eng, plasticity.KIND_PREF, "length", value="講話簡短一點", now_ts=NOW)
        self.assertEqual(len(eng), 1)                       # 同 key → 累加、不新增
        self.assertEqual(eng[0]["hits"], 2)
        self.assertGreater(eng[0]["weight"], w1)            # 重複 → 更強
        self.assertLessEqual(eng[0]["weight"], 1.0)         # 飽和、不超過 1

    def test_decay_and_consolidate_prune(self):
        eng = [{"kind": "pref", "key": "x", "value": "v", "weight": 0.5, "hits": 1,
                "born_ts": NOW, "last_ts": NOW}]
        # 很久以後（多個半衰期）→ 衰減到 floor 以下 → 固化時被修剪掉
        far = NOW + 200 * 86400
        self.assertEqual(plasticity.consolidate(eng, now_ts=far), [])
        # 剛強化過 → 固化保留
        self.assertEqual(len(plasticity.consolidate(eng, now_ts=NOW)), 1)

    def test_top_uses_decayed_weight_and_min(self):
        eng = []
        plasticity.reinforce(eng, plasticity.KIND_PREF, "a", value="A", now_ts=NOW)
        self.assertEqual(plasticity.top(eng, plasticity.KIND_PREF, min_weight=0.2, now_ts=NOW)[0]["key"], "a")
        self.assertEqual(plasticity.top(eng, plasticity.KIND_PREF, min_weight=0.9, now_ts=NOW), [])  # 門檻高 → 濾掉


class PreferenceDetectTest(unittest.TestCase):
    def test_reads_explicit_style_prefs(self):
        self.assertEqual(plasticity.read_preference("你可以講短一點嗎")["key"], "length")
        self.assertEqual(plasticity.read_preference("說具體一點啦")["key"], "detail")
        self.assertEqual(plasticity.read_preference("不要一直問我問題")["key"], "questions")
        self.assertEqual(plasticity.read_preference("別動不動就報一堆數字給我")["key"], "stats")

    def test_ignores_non_pref(self):
        for q in ["今天天氣如何", "我多久沒理你", "幫我看今天寫了什麼", "你現在怎樣"]:
            self.assertIsNone(plasticity.read_preference(q), q)

    def test_coherent_pref_detected(self):
        # 🧵 截圖根因：「別一直分段／逐句回答／講連貫」要學成持久偏好＋實際併泡泡
        for q in ["你還是分段回覆喔", "不要一直分段", "講連貫一點", "別逐句回覆", "一次說完", "別分這麼多"]:
            self.assertEqual((plasticity.read_preference(q) or {}).get("key"), "coherent", q)

    def test_assoc_pref_and_has_pref(self):
        # 🚫 截圖根因：「停止聯想」要學成持久偏好＋實際 gate 主動聯想
        for q in ["停止聯想", "不要再聯想了", "別亂聯想", "不要把它們想在一起", "別連想"]:
            self.assertEqual((plasticity.read_preference(q) or {}).get("key"), "assoc", q)
        eng = plasticity.reinforce([], plasticity.KIND_PREF, "assoc", value="別自己亂聯想", now_ts=NOW)
        self.assertTrue(plasticity.has_pref(eng, "assoc", now_ts=NOW))         # 剛強化＝活著
        self.assertFalse(plasticity.has_pref(eng, "length", now_ts=NOW))       # 沒這條
        self.assertFalse(plasticity.has_pref([], "assoc", now_ts=NOW))         # 空＝不擋
        self.assertFalse(plasticity.has_pref(eng, "assoc", now_ts=NOW + 10**9))  # 久遠衰減後自然恢復


class RecallTest(unittest.TestCase):
    def test_prefs_brief_lists_learned(self):
        eng = []
        plasticity.reinforce(eng, plasticity.KIND_PREF, "stats", value="別動不動報一堆數字/報表", now_ts=NOW)
        b = plasticity.prefs_brief(eng, now_ts=NOW)
        self.assertIn("相處偏好", b)
        self.assertIn("報一堆數字", b)
        self.assertEqual(plasticity.prefs_brief([], now_ts=NOW), "")    # 沒學到 → 不注入


class EndToEndTest(unittest.TestCase):
    """capture（明講偏好）→ 存進 state.engrams → 跨重啟讀回 → 注入下一次回覆的 brief。"""

    def test_pref_learned_persisted_and_injected(self):
        path = os.path.join(tempfile.mkdtemp(), "s.json")
        s = State(path)
        s.owner_folder_id = "F"
        cap = {}

        def fake_reply(q, brief, hist, mood_hint="", now_ts=None, self_presence=False):
            cap["brief"] = brief
            return "好，我盡量直接講。"

        def fake_ask(q, brief, *a, **k):
            cap["brief"] = brief
            return ("chat", None, "嗯。")

        coach = SimpleNamespace(enabled=True, meter=SimpleNamespace(record=lambda *a, **k: None),
                                reply=fake_reply, ask=fake_ask)
        client = SimpleNamespace(sent=[], dry_run=False, send=lambda t: client.sent.append(t) or True)
        cfg = SimpleNamespace(dry_run=False, telegram_chat_id="", mood_gain=1.0)

        # 第一次：使用者明講偏好「別一直報數字」（走自我在場 reply，因前一句沒在窗內也會落 ask；用真 brief 即可）
        up1 = {"message": {"chat": {"id": 1}, "text": "拜託別一直報數字給我", "date": NOW}}
        with mock.patch("telegram_monitor.coach.build_memory_brief", side_effect=lambda *a, **k: k.get("learned", "")):
            monitor.handle_message(up1, coach, None, {"meta": {}, "records": []}, object(),
                                   s, client, cfg, None)
        # 偏好已被學起來、存檔
        self.assertTrue(any(e["kind"] == "pref" and e["key"] == "stats" for e in s.engrams))

        # 模擬「死亡→重生」：從磁碟重新載入 state（記憶體歸零，但印痕應跨重生延續）
        s2 = State.load(path)
        self.assertTrue(any(e["key"] == "stats" for e in s2.engrams))    # 跨重生還記得

        # 重生後下一次對話：學到的偏好應注入 brief（影響回覆）
        up2 = {"message": {"chat": {"id": 1}, "text": "嗨", "date": NOW + 600}}
        with mock.patch("telegram_monitor.coach.build_memory_brief", side_effect=lambda *a, **k: k.get("learned", "")):
            monitor.handle_message(up2, coach, None, {"meta": {}, "records": []}, object(),
                                   s2, client, cfg, None)
        self.assertIn("報一堆數字", cap.get("brief", ""))                 # 學到的偏好確實進了 grounding

    def test_coherent_pref_caps_to_moderate_segments(self):
        # 🧵 學到「別一直分段」後：統一回答但**分段依序送出**（封到適中上限，非 1 坨牆、也非逐句碎泡泡），保有可被插話的空檔
        import time as _t
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        # handle_message 的 convo_now=time.time()（真實牆鐘）；偏好印痕須以同源時間強化才不被衰減掉
        s.engrams = plasticity.reinforce([], plasticity.KIND_PREF, "coherent", value="講連貫", now_ts=_t.time())
        coach = SimpleNamespace(enabled=True, meter=SimpleNamespace(record=lambda *a, **k: None),
                                reply=lambda *a, **k: "第一句。第二句。第三句。第四句。第五句。",
                                ask=lambda *a, **k: ("chat", None, "第一句。第二句。第三句。第四句。第五句。"))
        client = SimpleNamespace(sent=[], dry_run=False, send=lambda t: client.sent.append(t) or True,
                                 send_typing=lambda: None)
        cfg = SimpleNamespace(dry_run=False, telegram_chat_id="", mood_gain=1.0,
                              coherent_reply_enabled=True, coherent_reply_bubbles=3)
        up = {"message": {"chat": {"id": 1}, "text": "今天天氣如何", "date": NOW}}
        with mock.patch("telegram_monitor.coach.build_memory_brief", return_value=""), \
             mock.patch.object(monitor, "_sleep"):
            monitor.handle_message(up, coach, None, {"meta": {}, "records": []}, object(), s, client, cfg, None)
        voice_msgs = [m for m in client.sent if "第" in m and "句" in m]
        self.assertTrue(1 < len(voice_msgs) <= 3)          # 5 句封到適中段數（>1＝仍分段依序送、≤3＝非碎泡泡；非 1 坨牆）
        self.assertIn("第五句", "".join(voice_msgs))       # 內容完整、沒被丟


if __name__ == "__main__":
    unittest.main()
