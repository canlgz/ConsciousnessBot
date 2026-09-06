"""🧠 §1.21 差分自陳（SELF_REPORT_DELTA）純函式層測試。

驗證 selfreport 模組：
① snapshot 帶位與 selfstate.bodystate_facts / _hunger_duration_phrase 的分支門檻一字不差對齊
   （同帶內漂移＝浮點噪音＝不算變）；
② diff_facts 只列真的變的帶（人話短句、不報數字）；
③ prior_brief 注入段：上次全文摘錄＋「別重講」「真的變了」＋使用者原句前 60 字＋「先回應他這句」；
   prior=None／超 horizon → ''；全文 >200 字被截；
④ no_change_line 確定性無變化短句：≥4 變體互異、seq 取模輪替、含 topic、無阿拉伯數字、短；
⑤ State.last_self_report 持久化 round-trip（舊檔無此鍵容缺；從未設值＝save 不寫此鍵＝同現狀）。
"""

import json
import os
import re
import tempfile
import unittest
from types import SimpleNamespace

from telegram_monitor import selfreport
from telegram_monitor.state import State


def _vit(up_min=30, streak=9, k_adj=-0.1, charge=0.0, hunger=0.9, laps=700, mood=0.0, lr=None):
    """一份「醒了好長一段、很穩、悶著等新記寫」的 vitality 快照（欄位對齊 lifeloop 的 dict 形）。"""
    return {"alive": True, "uptime_s": up_min * 60, "healthy_streak": streak, "k_adj": k_adj,
            "S": 0.6, "charge": charge, "hunger": hunger, "laps_since_fresh": laps,
            "mood": mood, "arousal": 0.0, "last_revisited": lr}


def _res(gate=3, topic="逛街購物"):
    return {"gate": gate, "scope": {"dominant": topic}, "reading": {}}


def _state(mood=0.0, arousal=0.0):
    """最小 state：circumplex.position 只讀 entropy.mood / entropy.arousal。"""
    return SimpleNamespace(entropy=SimpleNamespace(mood=mood, arousal=arousal))


class SnapshotBandTest(unittest.TestCase):
    def test_span_bands_align_bodystate_facts(self):
        # uptime 2/10/30 分 → 0/1/2（同 bodystate_facts 的 <3 / <20 / ≥20 三帶）
        st = _state()
        for up, band in ((2, 0), (10, 1), (30, 2)):
            snap = selfreport.snapshot(_vit(up_min=up), _res(), st, 0.0)
            self.assertEqual(snap["span"], band, f"uptime {up} 分應落帶 {band}")

    def test_hband_aligns_hunger_duration_phrase(self):
        # laps 20/100/700/2000 → 0/1/3/4（同 _hunger_duration_phrase 的 <30/<150/<600/<1500/≥1500 帶界）
        st = _state()
        for laps, band in ((20, 0), (100, 1), (700, 3), (2000, 4)):
            snap = selfreport.snapshot(_vit(laps=laps), _res(), st, 0.0)
            self.assertEqual(snap["hband"], band, f"laps {laps} 應落帶 {band}")

    def test_same_band_float_noise_yields_empty_diff(self):
        # mood 0.30 → 0.32：同帶（<0.35）＝浮點噪音不觸發任何差分
        st = _state()
        a = selfreport.snapshot(_vit(mood=0.30), _res(), st, 0.0)
        b = selfreport.snapshot(_vit(mood=0.32), _res(), st, 0.0)
        self.assertEqual(selfreport.diff_facts(a, b), [])

    def test_snapshot_topic_gate_region(self):
        snap = selfreport.snapshot(_vit(), _res(gate=3, topic="讀誦經書"), _state(), 0.0)
        self.assertEqual(snap["topic"], "讀誦經書")
        self.assertEqual(snap["gate"], 3)
        self.assertEqual(snap["region"], "平穩")           # entropy (0,0) ＝中性區


class DiffFactsTest(unittest.TestCase):
    def test_topic_change_only_yields_one_bit_with_both_names(self):
        st = _state()
        a = selfreport.snapshot(_vit(), _res(topic="逛街購物"), st, 0.0)
        b = selfreport.snapshot(_vit(), _res(topic="汽車保養"), st, 0.0)
        bits = selfreport.diff_facts(a, b)
        self.assertEqual(len(bits), 1)
        self.assertIn("逛街購物", bits[0])
        self.assertIn("汽車保養", bits[0])

    def test_hband_up_mentions_longer_hunger(self):
        st = _state()
        a = selfreport.snapshot(_vit(laps=700), _res(), st, 0.0)
        b = selfreport.snapshot(_vit(laps=2000), _res(), st, 0.0)
        bits = selfreport.diff_facts(a, b)
        self.assertTrue(any("更久" in x for x in bits), bits)

    def test_identical_bands_yield_empty(self):
        st = _state()
        a = selfreport.snapshot(_vit(), _res(), st, 0.0)
        b = selfreport.snapshot(_vit(), _res(), st, 100.0)
        self.assertEqual(selfreport.diff_facts(a, b), [])

    def test_no_numbers_in_diff_sentences(self):
        # 家規：不報數字——把每個帶都改一輪，句子裡不得出現阿拉伯數字
        st_a, st_b = _state(), _state(mood=0.8, arousal=0.5)
        a = selfreport.snapshot(_vit(up_min=2, streak=1, k_adj=0.1, charge=0.9, hunger=0.1,
                                     laps=10, mood=-0.5), _res(gate=2, topic="甲"), st_a, 0.0)
        b = selfreport.snapshot(_vit(up_min=30, streak=9, k_adj=-0.1, charge=0.0, hunger=0.9,
                                     laps=2000, mood=0.5, lr="舊線"), _res(gate=4, topic="乙"), st_b, 0.0)
        bits = selfreport.diff_facts(a, b)
        self.assertTrue(bits)
        for x in bits:
            self.assertIsNone(re.search(r"\d", x), f"差分句不得帶數字：{x}")


class PriorBriefTest(unittest.TestCase):
    def _snaps(self):
        st = _state()
        a = selfreport.snapshot(_vit(laps=700), _res(topic="逛街購物"), st, 0.0)
        b = selfreport.snapshot(_vit(laps=2000), _res(topic="逛街購物"), st, 0.0)
        return a, b

    def test_brief_contains_prior_text_delta_and_user_anchor(self):
        a, b = self._snaps()
        prior = {"text": "我一直都醒著，很穩、沒斷線過。", "ts": 1000.0, "snap": a}
        user = "你變的更有意識了，跟之前不太一樣，說說看你自己有感覺到嗎"
        brief = selfreport.prior_brief(prior, b, 1000.0 + 1800, user)
        self.assertIn("我一直都醒著", brief)              # 上次全文摘錄（負面示例）
        self.assertIn("別重講", brief)
        self.assertIn("真的變了", brief)
        self.assertIn("先回應他這句", brief)
        self.assertIn(user[:60], brief)                    # 使用者原句前 60 字
        self.assertIn("約 30 分鐘前", brief)               # {ago} 程式算（temporal.spoken_gap）

    def test_no_prior_or_over_horizon_returns_empty(self):
        a, b = self._snaps()
        self.assertEqual(selfreport.prior_brief(None, b, 1000.0, "你好嗎"), "")
        stale = {"text": "舊話", "ts": 0.0, "snap": a}
        self.assertEqual(selfreport.prior_brief(stale, b, 2880 * 60 + 1.0, "你好嗎"), "")

    def test_prior_text_truncated_to_200(self):
        a, b = self._snaps()
        prior = {"text": "長" * 300, "ts": 1000.0, "snap": a}
        brief = selfreport.prior_brief(prior, b, 1000.0 + 60, "你現在怎樣")
        self.assertIn("長" * 200, brief)
        self.assertNotIn("長" * 201, brief)

    def test_empty_diff_admits_no_change_honestly(self):
        a, _ = self._snaps()
        prior = {"text": "上次的話。", "ts": 1000.0, "snap": a}
        brief = selfreport.prior_brief(prior, dict(a), 1000.0 + 600, "你現在怎樣")
        self.assertIn("沒什麼真的變", brief)
        self.assertIn("別硬編", brief)


class NoChangeLineTest(unittest.TestCase):
    def test_four_variants_rotate_with_topic_no_digits(self):
        res = _res(topic="研發日誌")
        lines = [selfreport.no_change_line(None, res, s) for s in range(4)]
        self.assertEqual(len(set(lines)), 4)               # 四句互異（seq 取模輪替）
        for line in lines:
            self.assertIn("研發日誌", line)
            self.assertLessEqual(len(line), 40)
            self.assertIsNone(re.search(r"\d", line), f"無變化短句不得帶數字：{line}")
        # seq 取模：4 ＝繞回第 0 句
        self.assertEqual(selfreport.no_change_line(None, res, 4), lines[0])

    def test_generic_form_without_topic(self):
        res = {"gate": 1, "scope": {}, "reading": {}}
        lines = [selfreport.no_change_line(None, res, s) for s in range(4)]
        self.assertEqual(len(set(lines)), 4)
        for line in lines:
            self.assertLessEqual(len(line), 40)
            self.assertIsNone(re.search(r"\d", line))


class StatePersistTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "state.json")

    def test_round_trip(self):
        s = State(self.path)
        s.last_self_report = {"text": "上次自陳的原話", "ts": 123.5,
                              "snap": {"topic": "甲", "gate": 3, "hband": 2}}
        s.save()
        s2 = State.load(self.path)
        self.assertEqual(s2.last_self_report,
                         {"text": "上次自陳的原話", "ts": 123.5,
                          "snap": {"topic": "甲", "gate": 3, "hband": 2}})

    def test_old_file_without_key_loads_none(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"owner_folder_id": "F"}, f)
        s = State.load(self.path)                          # d.get 容缺，不炸
        self.assertIsNone(s.last_self_report)

    def test_never_set_means_key_not_written(self):
        # 旗標關＝從不寫此欄 → save 出的 JSON 不帶此鍵（state.json 逐位元同現狀）
        s = State(self.path)
        s.save()
        with open(self.path, encoding="utf-8") as f:
            d = json.load(f)
        self.assertNotIn("last_self_report", d)


if __name__ == "__main__":
    unittest.main()
