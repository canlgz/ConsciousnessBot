"""🫧 §1.72 聯想自陳去公式化（REACHOUT_DIVERSE）：不再每次同款「欸…想弄懂…有空跟我說說」。

截圖根因：主動聯想每次同一形（reach_out_line 四模板同款＋意圖長期釘同一主題、LLM 只輕改寫）＝
使用者「每次看到都是同一種訊息，台詞也類似…讓聯想自陳有更像人類意識性的表述」。
修法（§1.69 成功模式：禁令＋形態選單、不給範例句）＋具體素材（引最近記寫字句）＋重複自覺
（同主題已提 ≥2 次＝別再問、改分享）。旗標關＝原模板腔＝逐位元同現狀。全 stub、零網路。
"""

import os
import re
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from telegram_monitor import lifeloop, monitor, volition
from telegram_monitor.state import State

NOW = datetime(2026, 7, 24, 4, 0, 0, tzinfo=timezone.utc)


class RuleTest(unittest.TestCase):
    def test_bans_and_menu(self):
        r = volition.reach_out_diverse_rule("閱讀｜讀誦經書", 0)
        self.assertIn("禁用", r)
        self.assertIn("想弄懂它對你", r)                        # 點名爛句式（在禁令裡、不是範例）
        self.assertIn("有空跟我說說", r)
        self.assertIn("小小的疑問", r)
        self.assertIn("不索取回答", r)                          # 形態選單：自語式聯想
        self.assertIn("具體內容", r)                            # 形態選單：具體好奇
        self.assertIn("少用", r)                                # 直接邀請＝少用

    def test_excerpt_material(self):
        r = volition.reach_out_diverse_rule("閱讀｜讀誦經書", 0, excerpt="今天讀到金剛經的無住生心")
        self.assertIn("無住生心", r)                            # 具體素材帶進 prompt

    def test_repeat_awareness(self):
        r = volition.reach_out_diverse_rule("閱讀｜讀誦經書", 3)
        self.assertIn("提過 3 次", r)
        self.assertIn("別再問他", r)                            # 重複自覺：改分享、不再問
        self.assertNotIn("別再問他", volition.reach_out_diverse_rule("閱讀｜讀誦經書", 1))


class ExcerptTest(unittest.TestCase):
    RECS = [{"topicLabel": "閱讀｜讀誦經書", "text": "今天讀到金剛經的無住生心，很有感覺", "ts": "2026-07-23T09:00:00Z"},
            {"topicLabel": "閱讀｜讀誦經書", "text": "舊的一筆", "ts": "2026-07-20T09:00:00Z"},
            {"topicLabel": "運動", "text": "跑了五公里", "ts": "2026-07-23T10:00:00Z"}]

    def test_latest_matching_excerpt(self):
        out = monitor._topic_latest_excerpt(self.RECS, "閱讀｜讀誦經書")
        self.assertIn("無住生心", out)                          # 取同主題**最新**一筆
        self.assertNotIn("舊的一筆", out)

    def test_no_match_empty(self):
        self.assertEqual(monitor._topic_latest_excerpt(self.RECS, "園藝"), "")
        self.assertEqual(monitor._topic_latest_excerpt(None, "閱讀"), "")

    def test_cap(self):
        recs = [{"topicLabel": "閱讀", "text": "很長" * 100, "ts": "2026-07-23T09:00:00Z"}]
        self.assertLessEqual(len(monitor._topic_latest_excerpt(recs, "閱讀")), 40)


class EmitWireTest(unittest.TestCase):
    """_spontaneous_emit 佈線：diverse 規則進 time_rule、成功送出後 reach_n +1；旗標關＝現狀。"""

    class Cl:
        def __init__(self):
            self.sent, self.dry_run = [], False

        def send(self, t):
            self.sent.append(t)
            return True

    def _state(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        ent = lifeloop.EntropyState()
        ent.hunger = 0.95
        ent.self_stims_this_idle = 6                            # 醞釀夠（同 test_spontaneous._hungry_ent）
        ent.prev_ingest = "OLD"
        ent.last_revisited_topic = "閱讀｜讀誦經書"
        s.entropy = ent
        s.self_state = {"gate": 3, "scope": {"dominant": "閱讀｜讀誦經書"}}
        s.last_push_ts = 0
        s.goals = [{"subject": "閱讀｜讀誦經書", "status": "active", "born_ts": NOW.timestamp() - 3600}]
        return s

    def _cfg(self, on=True):
        return SimpleNamespace(spontaneous_cooldown_min=180, reachout_diverse_enabled=on)

    def _coach(self, seen):
        def vs(seed, hist, coping="", time_rule="", **kw):
            seen["rule"] = time_rule
            return seed
        return SimpleNamespace(enabled=True, voice_spontaneous=vs)

    def test_rule_threaded_and_counter(self):
        s, seen = self._state(), {}
        monitor._spontaneous_emit(self.Cl(), s, self._cfg(), self._coach(seen), NOW,
                                  records=ExcerptTest.RECS)
        self.assertIn("禁用", seen.get("rule") or "")            # 選單＋禁令真的進了 prompt
        self.assertIn("無住生心", seen.get("rule") or "")        # 具體素材真的進了 prompt
        self.assertEqual(s.goals[0].get("reach_n"), 1)          # 重複自覺計數 +1

    def test_flag_off_bitwise(self):
        s, seen = self._state(), {}
        monitor._spontaneous_emit(self.Cl(), s, self._cfg(on=False), self._coach(seen), NOW,
                                  records=ExcerptTest.RECS)
        self.assertNotIn("禁用", seen.get("rule") or "")         # 旗標關＝原 time_rule＝現狀
        self.assertNotIn("reach_n", s.goals[0])                 # 不計數


class ConfigTest(unittest.TestCase):
    def test_config_synced(self):
        src = open("telegram_monitor/config.py", encoding="utf-8").read()
        self.assertIn("REACHOUT_DIVERSE", src)
        self.assertIn("reachout_diverse_enabled", src)
        env = open(".env.example", encoding="utf-8").read()
        self.assertRegex(env, re.compile(r"^REACHOUT_DIVERSE=1", re.M))
        self.assertIn("REACHOUT_DIVERSE", open("README.md", encoding="utf-8").read())

    def test_getattr_defaults_false(self):
        self.assertFalse(getattr(SimpleNamespace(), "reachout_diverse_enabled", False))


if __name__ == "__main__":
    unittest.main()
