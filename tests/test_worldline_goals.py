# -*- coding: utf-8 -*-
"""🌐 §2.16 worldline 對齊使用者的三個目標：刺激「繼續寫／延伸寫／創造新主題」。

使用者定調：「連結記寫內容與外部網路世界的話題，有不錯的話題之後，進一步主動與使用者閒聊，
來刺激使用者繼續記寫、延伸記寫、或者創造新的記寫主題。」通盤對帳出四個缺口，本檔逐一釘死：
  ①終身 40 次上限＝lane 遲早永久沉默（防呆變定時炸彈）→ 月結
  ②ledger 沒存查到的說法本身 → 追問「那是誰說的」零接地（§1.36 溫床）→ 存 finding＋注入
  ③完全看不出撞完他有沒有回去寫 → 撞後對帳進 /worldline
  ④「創造新主題」完全沒被服務到 → 邀請輪替（延伸 vs 開新線），隱私不變（搜尋仍只送授權標籤）
全 stub、零網路。
"""

import io
import os
import re
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from telegram_monitor import monitor, persona, worldline as wl
from telegram_monitor.state import State

NOW = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
NOW_TS = NOW.timestamp()


def _state(**kw):
    s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
    s.worldline_allow = ["讀誦經書"]
    s.last_user_msg_ts = 0
    s.last_push_ts = 0
    s.last_worldline_ts = 0
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def _cfg(**kw):
    d = dict(worldline_enabled=True, worldline_monthly_budget=True, worldline_followup_enabled=True,
             worldline_spark_enabled=True, worldline_chain_enabled=True, worldline_cooldown_h=24,
             worldline_max_searches=40, notify_cooldown_min=30, timezone="Asia/Taipei",
             quiet_start=1, quiet_end=6, dry_run=True)
    d.update(kw)
    return SimpleNamespace(**d)


CL = lambda: SimpleNamespace(sent=[], dry_run=True, send=lambda t: True)
COACH = SimpleNamespace(enabled=True)


class MonthlyBudgetTest(unittest.TestCase):
    """① 終身上限＝定時炸彈：搜滿 40 次整條 lane 永久沉默，與「持續刺激記寫」直接矛盾。"""

    def test_month_rollover_resets_the_counter(self):
        s = _state(worldline_search_n=40, worldline_month="2026-06")
        r = monitor._worldline_emit(CL(), s, _cfg(), COACH, NOW, data={"records": []})
        self.assertEqual(s.worldline_search_n, 0)
        self.assertEqual(s.worldline_month, "2026-07")
        self.assertNotIn("上限", r or "")                       # 不再被額度擋

    def test_same_month_cap_still_holds(self):
        s = _state(worldline_search_n=40, worldline_month="2026-07")
        r = monitor._worldline_emit(CL(), s, _cfg(), COACH, NOW, data={"records": []})
        self.assertIn("這個月", r or "")

    def test_flag_off_is_lifetime_cap(self):
        s = _state(worldline_search_n=40, worldline_month="2026-06")
        r = monitor._worldline_emit(CL(), s, _cfg(worldline_monthly_budget=False), COACH, NOW, data={"records": []})
        self.assertIn("總量上限", r or "")
        self.assertEqual(s.worldline_search_n, 40)              # 一位元不動

    def test_state_roundtrip(self):
        p = os.path.join(tempfile.mkdtemp(), "s.json")
        s = State(p)
        s.worldline_month, s.worldline_invite_n = "2026-07", 3
        s.save()
        r = State.load(p)
        self.assertEqual((r.worldline_month, r.worldline_invite_n), ("2026-07", 3))


class FollowupGroundingTest(unittest.TestCase):
    """② 追問剛帶回的說法時要照存檔講、不重編。"""

    LED = [{"label": "資訊基生生命觀", "display": "靈感｜資訊基生生命觀", "ts": NOW_TS - 600,
            "src": ["https://x/y"], "src_name": "quantamagazine.org",
            "finding": "生命可以被想像成自我永續的資訊流"}]

    def test_ledger_stores_the_finding(self):
        led = wl.push_ledger(None, {"label": "a", "display": "d", "rec_id": "x"},
                             [("站名", "https://s")], NOW_TS, finding="外面的說法")
        self.assertEqual(led[-1]["finding"], "外面的說法")
        self.assertEqual(led[-1]["src_name"], "站名")

    def test_no_finding_no_extra_keys(self):
        led = wl.push_ledger(None, {"label": "a"}, [("t", "u")], NOW_TS, finding="")
        self.assertNotIn("finding", led[-1])                    # 旗標關＝形狀同舊

    def test_mentioning_the_line_gets_the_facts(self):
        s = _state(worldline_ledger=self.LED)
        h = monitor._worldline_followup_hint(s, _cfg(), "你剛剛說的資訊基生生命觀那個說法是誰講的？", NOW_TS)
        self.assertIn("資訊流", h)
        self.assertIn("quantamagazine.org", h)
        self.assertIn("別重編", h)

    def test_right_after_the_collision_no_mention_needed(self):
        s = _state(worldline_ledger=self.LED)
        self.assertTrue(monitor._worldline_followup_hint(s, _cfg(), "真的假的", NOW_TS))   # 10 分鐘內＝在回那一則

    def test_stale_or_unrelated_is_empty(self):
        s = _state(worldline_ledger=self.LED)
        self.assertEqual(monitor._worldline_followup_hint(s, _cfg(), "今天天氣如何", NOW_TS + 3600), "")
        old = [dict(self.LED[0], ts=NOW_TS - 90000)]
        self.assertEqual(monitor._worldline_followup_hint(_state(worldline_ledger=old), _cfg(),
                                                          "資訊基生生命觀呢", NOW_TS), "")

    def test_flag_off_is_empty(self):
        s = _state(worldline_ledger=self.LED)
        self.assertEqual(monitor._worldline_followup_hint(s, _cfg(worldline_followup_enabled=False), "資訊基生生命觀", NOW_TS), "")

    def test_wired_into_both_chat_lanes(self):
        src = io.open("telegram_monitor/monitor.py", encoding="utf-8").read()
        self.assertGreaterEqual(src.count("_worldline_followup_hint(state, cfg, text"), 2)


class AftermathTest(unittest.TestCase):
    """③ 這條 lane 的存在理由是刺激記寫——之前完全看不出有沒有效。"""

    LED = [{"label": "讀誦經書", "display": "閱讀｜讀誦經書", "ts": NOW_TS}]

    def test_counts_records_within_three_days(self):
        recs = [{"topicLabel": "讀誦經書", "_ts": NOW_TS + 86400},
                {"topicLabel": "讀誦經書", "_ts": NOW_TS + 4 * 86400},   # 過窗＝不算
                {"topicLabel": "別條", "_ts": NOW_TS + 3600}]
        rows = wl.aftermath(self.LED, recs, NOW_TS + 5 * 86400)
        self.assertEqual(rows, [("閱讀｜讀誦經書", 1, True)])

    def test_window_not_full_yet(self):
        rows = wl.aftermath(self.LED, [], NOW_TS + 3600)
        self.assertEqual(rows[0][1:], (0, False))               # 「還在看」不是「沒效」

    def test_audit_shows_it(self):
        s = _state(worldline_ledger=self.LED)
        out = wl.audit_text(s, _cfg(), [], {"讀誦經書"}, {}, aftermath_rows=wl.aftermath(
            self.LED, [{"topicLabel": "讀誦經書", "_ts": NOW_TS + 3600}], NOW_TS + 5 * 86400))
        self.assertIn("寫了 1 筆", out)


class SparkInviteTest(unittest.TestCase):
    """④ 「創造新的記寫主題」之前完全沒被服務到。"""

    def test_rotation(self):
        self.assertEqual([persona.worldline_invite(i) for i in range(4)],
                         ["new_line", "extend", "new_line", "extend"])

    def test_new_line_invite_has_the_discipline(self):
        v = persona.worldline_say_rule_v2("d", "q", "f", "s", 0, "", invite="new_line")
        self.assertIn("開一條新線", v)
        self.assertIn("從沒出現過的具體概念名", v)
        self.assertIn("不要硬湊" if "不要硬湊" in v else "別硬湊", v)   # 沒有就退回一般邀請

    def test_empty_invite_is_verbatim_207(self):
        self.assertEqual(persona.worldline_say_rule_v2("d", "q", "f", "s", 0, ""),
                         persona.worldline_say_rule_v2("d", "q", "f", "s", 0, "", invite=""))

    def test_monthly_line_in_audit(self):
        s = _state(worldline_search_n=5)
        out = wl.audit_text(s, _cfg(), [], {"讀誦經書"}, {})
        self.assertIn("這個月已經查 5 次", out)
        out2 = wl.audit_text(s, _cfg(worldline_monthly_budget=False), [], {"讀誦經書"}, {})
        self.assertIn("已經真的查過 5 次", out2)                  # 旗標關＝舊句


class ConfigTest(unittest.TestCase):
    def test_flags_synced(self):
        src = io.open("telegram_monitor/config.py", encoding="utf-8").read()
        env = io.open(".env.example", encoding="utf-8").read()
        readme = io.open("README.md", encoding="utf-8").read()
        for flag, attr in (("WORLDLINE_MONTHLY_BUDGET", "worldline_monthly_budget"),
                           ("WORLDLINE_FOLLOWUP", "worldline_followup_enabled"),
                           ("WORLDLINE_SPARK", "worldline_spark_enabled")):
            self.assertIn(flag, src)
            self.assertIn(attr, src)
            self.assertRegex(env, re.compile(r"^" + flag + "=1", re.M))
            self.assertIn(flag, readme)
            self.assertFalse(getattr(SimpleNamespace(), attr, False))


if __name__ == "__main__":
    unittest.main()
