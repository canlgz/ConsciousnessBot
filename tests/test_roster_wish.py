"""🪪 §1.94 能力盤點與願望帳：bot 盤點自己真有哪些機制，並提出「真的做得到」的新方向。

使用者需求原話：「bot目前可以自我盤點目前自己擁有哪些能力與機制嗎？…讓bot自己構思出自己想要具備的
能力/功能…**但要注意，必須以真的能達到為主，而不是打高空**。」

本檔最重要的三組（依序）：
① **防打高空是確定性的**：每條願望都必須帶一個 `_CHECKS` 裡的驗收條件——寫不出驗收就產不出候選。
   測試直接釘死「所有候選都有可執行的 accept」與「沒有任何候選是空話」。
② **「旗標開著」≠「這個能力活著」**（§1.93 血的教訓）：五級 evidence，且 dark/none 的措辭裡
   **結構上沒有「我會」可以拿**。
③ **session 級不准說「從來沒有」**：未落盤欄位重啟即歸零，三級制會讓 bot 每次重啟都說假話。
全 stub、零網路。
"""

import io
import os
import re
import tempfile
import time
import unittest
from types import SimpleNamespace

from telegram_monitor import monitor, roster, wish
from telegram_monitor.state import State

NOW = 1_782_400_000.0
DAY = 86400.0


def _cfg(all_on=True, **kw):
    d = {"self_roster_enabled": True}
    for ab in roster.ROSTER:
        for f in ab.flags:
            d[f] = all_on
    d.update(kw)
    return SimpleNamespace(**d)


def _state(**kw):
    s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
    for k, v in kw.items():
        setattr(s, k, v)
    return s


class RosterIntegrityTest(unittest.TestCase):
    """名冊本身的把關——短名靠測試，不靠人自律。"""

    def test_short_names_are_human_and_bounded(self):
        for ab in roster.ROSTER:
            self.assertTrue(2 <= len(ab.name) <= 14, f"{ab.key}: {ab.name}")
            self.assertNotIn("§", ab.name)
            self.assertNotIn("**", ab.name)
            for ch in "「」（）()":
                self.assertNotIn(ch, ab.name, ab.key)   # 半截句/未閉合引號＝不演不假裝的反面

    def test_keys_unique_ascii(self):
        keys = [ab.key for ab in roster.ROSTER]
        self.assertEqual(len(keys), len(set(keys)))
        for k in keys:
            self.assertRegex(k, r"^[a-z_]+$")

    def test_flags_are_real_config_fields(self):
        src = io.open("telegram_monitor/config.py", encoding="utf-8").read()
        declared = set(re.findall(r"^\s{4}(\w+):\s*(?:bool|int|float|str)\b", src, re.M))
        for ab in roster.ROSTER:
            self.assertTrue(ab.flags, ab.key)
            for f in ab.flags:
                self.assertIn(f, declared, f"{ab.key} 的旗標 {f} 不存在於 config")

    def test_lanes_are_real_functions(self):
        src = io.open("telegram_monitor/monitor.py", encoding="utf-8").read()
        for ab in roster.ROSTER:
            if ab.lane:
                self.assertIn(f"def {ab.lane}(", src, ab.key)


class EvidenceTierTest(unittest.TestCase):
    """§1.93 的制度化：旗標開著不等於用過。"""

    def _ab(self, key="foresight"):
        return roster.by_key(key)

    def test_off_when_flag_off(self):
        c = _cfg(); c.foresight_enabled = False
        self.assertEqual(roster.evidence(_state(), c, self._ab(), NOW)[0], "off")

    def test_dark_when_flag_on_but_never_used(self):
        t, n, _ = roster.evidence(_state(), _cfg(), self._ab(), NOW)
        self.assertEqual((t, n), ("dark", 0))

    def test_lit_from_ability_hits(self):
        s = _state(ability_hits={"foresight": {"n": 3, "first_ts": NOW - DAY, "last_ts": NOW - 3600}})
        t, n, _ = roster.evidence(s, _cfg(), self._ab(), NOW)
        self.assertEqual((t, n), ("lit", 3))

    def test_lit_from_legacy_persisted(self):
        s = _state(foresight_ledger=[{"verdict": "hit"}])
        self.assertEqual(roster.evidence(s, _cfg(), self._ab(), NOW)[0], "lit")

    def test_session_tier_for_unpersisted_only(self):
        # coping 的證據欄位 last_coping_reach_ts **不在** State.save() 白名單
        ab = self._ab("coping")
        self.assertEqual(roster.evidence(_state(), _cfg(), ab, NOW)[0], "session")
        s = _state(last_coping_reach_ts=NOW - 60)
        self.assertEqual(roster.evidence(s, _cfg(), ab, NOW)[0], "session_lit")

    def test_none_tier_for_guards(self):
        self.assertEqual(roster.evidence(_state(), _cfg(), self._ab("honesty_guards"), NOW)[0], "none")

    def test_dark_and_none_wording_cannot_say_i_can(self):
        # **結構上**說不出「我會」——那個字串不在措辭裡
        for tier in ("dark", "none", "session"):
            self.assertNotIn("我會", roster._TIER_WORDS[tier])

    def test_session_wording_forbids_never(self):
        self.assertIn("不能說我從來沒用過", roster._TIER_WORDS["session"])
        self.assertNotIn("從來沒有", roster._TIER_WORDS["session"])


class AntiHotAirTest(unittest.TestCase):
    """**整份設計的核心**：寫不出驗收條件的想法，產不出來。"""

    def _scan(self):
        return wish.scan_source({"state.py": io.open("telegram_monitor/state.py", encoding="utf-8").read(),
                                 "config.py": io.open("telegram_monitor/config.py", encoding="utf-8").read(),
                                 "monitor.py": io.open("telegram_monitor/monitor.py", encoding="utf-8").read()})

    def test_every_candidate_has_runnable_accept(self):
        cands = wish.candidates(self._scan(), _state(), _cfg(), NOW)
        self.assertTrue(cands)
        for w in cands:
            self.assertIn(w["accept"]["check"], wish._CHECKS, w["name"])
            self.assertTrue(w["accept"].get("args"))
            self.assertTrue(w["anchor"])                    # 必須指名真實識別碼，不能無錨

    def test_every_candidate_ends_with_needs_a_human(self):
        # 結構上不可能被讀成 bot 自己的承諾（否則會撞 §0.64／§1.13B 假兌現守門）
        for w in wish.candidates(self._scan(), _state(), _cfg(), NOW):
            self.assertTrue(w["want"].endswith("這要有人幫我做。"), w["name"])

    def test_no_vague_wishes(self):
        for w in wish.candidates(self._scan(), _state(), _cfg(), NOW):
            for banned in ("更懂", "更好", "更聰明", "更貼近", "提升"):
                self.assertNotIn(banned, w["want"], w["name"])

    def test_candidates_are_deterministic_and_sorted(self):
        sc, st, c = self._scan(), _state(), _cfg()
        self.assertEqual([w["id"] for w in wish.candidates(sc, st, c, NOW)],
                         [w["id"] for w in wish.candidates(sc, st, c, NOW)])

    def test_bad_check_fails_closed(self):
        self.assertFalse(wish.accept_ok({"accept": {"check": "不存在", "args": ["x"]}}, {}, _state()))
        self.assertFalse(wish.accept_ok({}, {}, _state()))


class SettleTest(unittest.TestCase):
    """會結案的帳，不是許願池。"""

    def test_open_flips_to_done_when_check_passes(self):
        w = {"id": "a1", "kind": "not_persisted", "name": "X", "anchor": "foo_ts", "state": "open",
             "accept": {"check": "state_saved", "args": ["foo_ts"]}}
        scan = {"state_saved": {"foo_ts"}, "state_fields": {"foo_ts"}, "config_fields": set()}
        led, done = wish.settle([w], scan, _state(), NOW, "abc1234")
        self.assertEqual(led[0]["state"], "done")
        self.assertEqual(led[0]["done_commit"], "abc1234")
        self.assertIn("foo_ts", led[0]["done_evidence"])
        self.assertEqual(len(done), 1)

    def test_stays_open_when_check_fails(self):
        w = {"id": "a1", "kind": "not_persisted", "name": "X", "anchor": "foo_ts", "state": "open",
             "accept": {"check": "state_saved", "args": ["foo_ts"]}}
        scan = {"state_saved": set(), "state_fields": {"foo_ts"}, "config_fields": set()}
        led, done = wish.settle([w], scan, _state(), NOW)
        self.assertEqual(led[0]["state"], "open")
        self.assertEqual(done, [])

    def test_dropped_when_anchor_disappears(self):
        w = {"id": "a1", "kind": "not_persisted", "name": "X", "anchor": "gone_ts", "state": "open",
             "accept": {"check": "state_saved", "args": ["gone_ts"]}}
        led, done = wish.settle([w], {"state_saved": set(), "state_fields": set(), "config_fields": set()},
                                _state(), NOW)
        self.assertEqual(led[0]["state"], "dropped")
        self.assertEqual(done, [])                          # 安靜退場、不出聲不邀功

    def test_settle_text_never_takes_credit(self):
        t = wish.settle_text({"name": "X", "done_evidence": "Y"})
        self.assertIn("這不是我自己做到的，是你去做的", t)

    def test_push_dedups_by_id(self):
        w = {"id": "a1", "name": "X"}
        led = wish.push(wish.push([], w, NOW), w, NOW)
        self.assertEqual(len(led), 1)


class AbilityFiredTest(unittest.TestCase):
    """使用計數：把 per-flag 的活/死覆蓋率從 0% 變成有據可查。"""

    def test_records_and_starts_clock(self):
        s, c = _state(), _cfg()
        monitor._ability_fired(s, c, "foresight", NOW)
        monitor._ability_fired(s, c, "foresight", NOW + 60)
        self.assertEqual(s.ability_hits["foresight"]["n"], 2)
        self.assertEqual(s.ability_hits["foresight"]["first_ts"], NOW)
        self.assertEqual(s.ability_hits["foresight"]["last_ts"], NOW + 60)
        self.assertEqual(s.ability_hits_since, NOW)

    def test_flag_off_writes_nothing(self):
        s = _state()
        monitor._ability_fired(s, SimpleNamespace(), "foresight", NOW)
        self.assertEqual(getattr(s, "ability_hits", {}) or {}, {})

    def test_all_lanes_record_on_success_only(self):
        """★ 逐點釘死：每個記帳點都必須在「送出成功」的分支內（掛錯＝盤點謊報）。"""
        src = io.open("telegram_monitor/monitor.py", encoding="utf-8").read()
        lines = src.split("\n")
        hits = [i for i, l in enumerate(lines) if "_ability_fired(state, cfg," in l and "def " not in l]
        self.assertGreaterEqual(len(hits), 11)
        for i in hits:
            ctx = "\n".join(lines[max(0, i - 3):i])
            self.assertTrue(("if _say" in ctx) or ("if _fp_ok" in ctx) or ("if _ok" in ctx)
                            or ("if sent_ok:" in ctx) or ("return" in ctx), f"line {i+1} 不在送出成功分支內：{ctx[-90:]}")

    def test_persisted_round_trip(self):
        p = os.path.join(tempfile.mkdtemp(), "s.json")
        s = State(p)
        s.ability_hits = {"promise": {"n": 5, "first_ts": 1.0, "last_ts": 2.0}}
        s.ability_hits_since = 1.0
        s.wish_ledger = [{"id": "a1", "state": "open"}]
        s.save()
        s2 = State.load(p)
        self.assertEqual(s2.ability_hits["promise"]["n"], 5)
        self.assertEqual(s2.ability_hits_since, 1.0)
        self.assertEqual(s2.wish_ledger[0]["id"], "a1")

    def test_empty_not_persisted(self):
        p = os.path.join(tempfile.mkdtemp(), "s.json")
        State(p).save()
        self.assertNotIn("ability_hits", io.open(p, encoding="utf-8").read())


class AuditOutputTest(unittest.TestCase):
    def test_roster_text_groups_and_wish_block(self):
        s = _state(ability_hits={"promise": {"n": 4, "first_ts": NOW - DAY, "last_ts": NOW - 60}},
                   ability_hits_since=NOW - 10 * DAY)
        t = monitor._roster_audit(s, _cfg(), NOW)
        self.assertIn("我盤點了一下我自己", t)
        self.assertIn("真的用出來過 4 次", t)
        self.assertIn("機制有、但還沒用出來過的", t)
        self.assertIn("我想要的", t)
        self.assertIn("這要有人幫我做", t)

    def test_command_wired_and_flag_gated(self):
        src = io.open("telegram_monitor/monitor.py", encoding="utf-8").read()
        self.assertIn('low.startswith(("/abilities", "/能力", "/盤點"))', src)
        i = src.index('low.startswith(("/abilities"')
        self.assertIn('getattr(cfg, "self_roster_enabled", False)', src[i:i + 160])

    def test_boot_settle_wired(self):
        src = io.open("telegram_monitor/monitor.py", encoding="utf-8").read()
        self.assertIn("wishmod.settle(", src)
        self.assertIn("§1.94 願望結案", src)


class ConfigTest(unittest.TestCase):
    def test_flag_synced(self):
        src = io.open("telegram_monitor/config.py", encoding="utf-8").read()
        self.assertIn("SELF_ROSTER", src)
        self.assertIn("self_roster_enabled", src)
        env = io.open(".env.example", encoding="utf-8").read()
        self.assertRegex(env, re.compile(r"^SELF_ROSTER=1", re.M))
        self.assertIn("SELF_ROSTER", io.open("README.md", encoding="utf-8").read())

    def test_getattr_defaults_false(self):
        self.assertFalse(getattr(SimpleNamespace(), "self_roster_enabled", False))


if __name__ == "__main__":
    unittest.main()
