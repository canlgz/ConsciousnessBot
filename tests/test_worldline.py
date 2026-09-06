"""🌐 §1.95 外面的世界撞進你的線（WORLDLINE）。

需求原話：「bot每日從網路上找到最火熱的話題，然後主動與我討論，刺激我記寫交流」。
使用者已裁定三件事，本檔逐條釘死：
① **接受偏移**：搜的是他自己的標籤、排除新聞時事（做「你那條線在外面的說法」，不是頭條）
② **嚴格白名單**：沒授權的標籤**一個字都不外送**——這是隱私紅線，測試從多個角度圍堵
③ **不先驗證 grounding**：tools 欄位名可設定、原始錯誤字串留給 /worldline

另外兩條紅線：**拿不到來源網址就沉默**（沒來源不准說「我查到」）、
**沒有他的原文就只是新聞摘要**（首要失敗模式，用確定性四道閘擋）。全 stub、零網路。
"""

import io
import os
import re
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from telegram_monitor import monitor, worldline as wl
from telegram_monitor.state import State

DAY = 86400.0
NOW = datetime(2026, 7, 27, 14, 0, 0, tzinfo=timezone.utc).timestamp()
Q = "備課時我發現繪本的留白比文字更難處理"
LAB = "繪本教學"


def _rec(label, text, days_ago, cat="教學", rid="r1"):
    return {"topicLabel": label, "category": cat, "text": text, "_ts": NOW - days_ago * DAY, "id": rid}


def _recs():
    return [_rec(LAB, "第一次試留白", 3.0, rid="a"),
            _rec(LAB, Q, 1.0, rid="b"),
            _rec("閱讀習慣", "每天固定二十分鐘", 0.5, cat="閱讀", rid="c")]


def _spans(recs):
    from telegram_monitor import foresight
    return foresight.line_spans(recs, NOW)


def _cand():
    return {"label": LAB, "display": f"教學｜{LAB}", "quote": Q, "rec_id": "b"}


class LineLatestTest(unittest.TestCase):
    """自己實作的理由：既有 `_topic_latest_excerpt` 會串線、且 ts 全壞時不 fail-closed。"""

    def test_exact_match_and_latest(self):
        got = wl.line_latest(_recs(), LAB)
        self.assertEqual(got["text"], Q)             # 最近那一筆
        self.assertEqual(got["display"], f"教學｜{LAB}")

    def test_never_loose_matches(self):
        recs = [_rec("閱讀", "我今天讀了三十頁", 5.0, cat="閱讀"),
                _rec("閱讀習慣", "每天固定二十分鐘", 1.0, cat="閱讀")]
        self.assertEqual(wl.line_latest(recs, "閱讀")["text"], "我今天讀了三十頁")
        self.assertEqual(wl.line_latest(recs, "閱讀習慣")["text"], "每天固定二十分鐘")

    def test_old_impl_no_longer_bleeds(self):
        # 🫧 §1.98 已從源頭修好：這裡原本釘的是「既有那支會串線」（subject「閱讀」取到「閱讀習慣」那筆）＝
        # 當初不能沿用它的理由。現在兩支對這份資料**答案一致**——本測試從「釘住壞行為」翻成「釘住不再串線」。
        recs = [{"topicLabel": "閱讀", "text": "我今天讀了三十頁", "ts": "2026-07-20T08:00:00Z"},
                {"topicLabel": "閱讀習慣", "text": "每天固定二十分鐘", "ts": "2026-07-26T08:00:00Z"}]
        self.assertEqual(monitor._topic_latest_excerpt(recs, "閱讀"), "我今天讀了三十頁")
        self.assertEqual(monitor._topic_latest_excerpt(recs, "閱讀習慣"), "每天固定二十分鐘")

    def test_old_impl_now_fails_closed_on_bad_ts(self):
        # 🫧 §1.98 第二個實測缺陷也修了：ts 全解析不出時，舊碼仍會吐檔案順序最後一筆（沒有時間根據的「最近」）
        recs = [{"topicLabel": "閱讀", "text": "先寫的", "ts": "not-a-date"},
                {"topicLabel": "閱讀", "text": "後寫的", "ts": None}]
        self.assertEqual(monitor._topic_latest_excerpt(recs, "閱讀"), "")

    def test_line_latest_still_separate(self):
        # 但 worldline 仍保有自己這支：它吃 `_ts`（呼叫端先 parse 好的 epoch）、回 id/display/cap=60，
        # 且**只認完全相等**——與 monitor 那支的用途與資料形狀都不同，不是重複實作。
        self.assertEqual(wl.line_latest(_recs(), LAB)["text"], Q)
        self.assertEqual(monitor._topic_latest_excerpt(_recs(), LAB), "")   # 那支讀 ts，這份資料只有 _ts

    def test_fail_closed(self):
        self.assertEqual(wl.line_latest(_recs(), "不存在"), {})
        self.assertEqual(wl.line_latest([{"topicLabel": LAB, "text": "x", "_ts": None}], LAB), {})
        self.assertEqual(wl.line_latest([], LAB), {})


class AllowlistTest(unittest.TestCase):
    """★ 隱私紅線：沒授權的一個字都不外送。從多個角度圍堵。"""

    def test_empty_allowlist_yields_nothing(self):
        self.assertEqual(wl.pick_lines(_recs(), _spans(_recs()), set(), {}, NOW), [])

    def test_only_allowed_label_is_picked(self):
        got = wl.pick_lines(_recs(), _spans(_recs()), {LAB}, {}, NOW)
        self.assertEqual([c["label"] for c in got], [LAB])

    def test_other_line_never_leaks(self):
        got = wl.pick_lines(_recs(), _spans(_recs()), {LAB}, {}, NOW)
        self.assertNotIn("閱讀習慣", [c["label"] for c in got])

    def test_query_contains_label_only_never_content(self):
        q = wl.build_query(LAB)
        self.assertIn(LAB, q)
        self.assertNotIn(Q, q)
        for frag in ("留白", "備課時我發現"):
            self.assertNotIn(frag, q)                # 記寫內文一個字都不進 query

    def test_lane_returns_before_any_search_when_no_allow(self):
        s = _state(allow=[])
        called = []
        monitor._worldline_emit(_Cl(), s, _cfg(), _coach(hook=called), _dt(), data=_data())
        self.assertEqual(called, [])                 # 完全沒呼叫任何 API
        self.assertEqual(int(getattr(s, "worldline_search_n", 0) or 0), 0)


class DiagnoseTest(unittest.TestCase):
    """🌐 §2.00 「可撞的 0 條」必須說得出**每條各自卡在哪一關**（截圖 10:08：只有一個 0，無從排查）。"""

    def _diag(self, recs, allow, used=None):
        return dict(wl.diagnose(recs, _spans(recs), allow, used or {}, NOW))

    def test_label_not_in_records(self):
        d = self._diag(_recs(), {"人工智慧意識"})
        self.assertIn("沒有這個標籤", d["人工智慧意識"])
        self.assertIn("裸標籤", d["人工智慧意識"])            # 直接告訴他名字該長什麼樣

    def test_too_few_records(self):
        recs = [_rec("孤例", "只寫過這麼一筆而已喔", 1.0)]
        self.assertIn("只有 1 筆", self._diag(recs, {"孤例"})["孤例"])

    def test_line_went_quiet(self):
        recs = [_rec("停住的線", "很久以前寫的那一筆", 30.0), _rec("停住的線", "也是很久以前", 31.0)]
        self.assertIn("天前", self._diag(recs, {"停住的線"})["停住的線"])
        self.assertIn("§1.90", self._diag(recs, {"停住的線"})["停住的線"])   # 指出那條線歸誰管

    def test_quote_too_short(self):
        recs = [_rec("短的", "短", 1.0), _rec("短的", "也短", 2.0)]
        self.assertIn("字", self._diag(recs, {"短的"})["短的"])

    def test_recently_used(self):
        d = self._diag(_recs(), {LAB}, used={LAB: NOW - 2 * DAY})
        self.assertIn("才撞過", d[LAB])

    def test_ok_line_says_so(self):
        self.assertIn("撞得動", self._diag(_recs(), {LAB})[LAB])

    def test_ready_labels_names_only_never_content(self):
        got = wl.ready_labels(_recs(), _spans(_recs()), {}, NOW)
        self.assertIn(LAB, [lab for lab, _n in got])
        for lab, _n in got:                                   # 只給名字與筆數，內文一個字都不出現
            self.assertNotIn(Q, lab)

    def test_audit_shows_diagnosis_when_nothing_to_hit(self):
        s = _state(allow=["人工智慧意識"])
        out = monitor._worldline_audit(s, _cfg(), _data(), NOW)
        self.assertIn("人工智慧意識", out)
        self.assertIn("沒有這個標籤", out)
        self.assertIn("現在查得動的是", out)                    # 附上可以照抄的名字
        self.assertIn(LAB, out)

    def test_audit_has_no_diagnosis_when_a_line_is_ready(self):
        out = monitor._worldline_audit(_state(allow=[LAB]), _cfg(), _data(), NOW)
        self.assertNotIn("現在查得動的是", out)                 # 有得撞就不囉嗦
        self.assertIn("下一個查", out)


class PickTest(unittest.TestCase):
    def test_active_gate(self):
        recs = [_rec(LAB, Q, 30.0), _rec(LAB, "舊的", 40.0)]
        self.assertEqual(wl.pick_lines(recs, _spans(recs), {LAB}, {}, NOW), [])   # 停住的線交給 §1.90

    def test_min_records(self):
        recs = [_rec(LAB, Q, 1.0)]
        self.assertEqual(wl.pick_lines(recs, _spans(recs), {LAB}, {}, NOW), [])

    def test_no_repeat_within_7_days(self):
        used = {LAB: NOW - 3 * DAY}
        self.assertEqual(wl.pick_lines(_recs(), _spans(_recs()), {LAB}, used, NOW), [])

    def test_sorted_by_returns(self):
        recs = _recs() + [_rec("備課策略", "先想清楚這堂課要留下什麼", 2.0),
                          _rec("備課策略", "把提問寫在教案最前面", 1.5),
                          _rec("備課策略", "節奏比內容量更難抓", 1.2)]
        got = wl.pick_lines(recs, _spans(recs), {LAB, "備課策略"}, {}, NOW)
        self.assertEqual(got[0]["label"], "備課策略")    # 回返多的優先
        self.assertGreaterEqual(got[0]["visits"], got[1]["visits"])


class CollisionGateTest(unittest.TestCase):
    """★ 首要失敗模式：退化成新聞摘要。四道確定性閘。"""

    def _cps(self):
        return wl.corpus(_recs())

    def test_good_message_passes(self):
        t = f"你在寫的「教學｜{LAB}」——你最近那筆是「{Q}」。外面有人主張留白該先於文字設計。"
        ok, why = wl.collision_ok(t, _cand(), self._cps())
        self.assertTrue(ok, why)

    def test_missing_user_quote_is_news_summary(self):
        t = f"關於「教學｜{LAB}」，外面有人主張留白該先於文字設計。"
        ok, why = wl.collision_ok(t, _cand(), self._cps())
        self.assertFalse(ok)
        self.assertIn("沒有引用他那一筆", why)

    def test_fabricated_quote_blocked(self):
        t = f"「教學｜{LAB}」你最近那筆是「{Q}」，你上次還寫過「我討厭繪本」。"
        ok, why = wl.collision_ok(t, _cand(), self._cps())
        self.assertFalse(ok)
        self.assertIn("沒寫過", why)

    def test_newsy_blocked(self):
        t = f"「教學｜{LAB}」你寫的是「{Q}」。今天有報導指出繪本市場大跌。"
        self.assertFalse(wl.collision_ok(t, _cand(), self._cps())[0])

    def test_missing_line_name_blocked(self):
        t = f"你最近那筆是「{Q}」。外面有人主張留白該先於文字設計。"
        ok, why = wl.collision_ok(t, _cand(), self._cps())
        self.assertFalse(ok)
        self.assertIn("哪條線", why)

    def test_fallback_template_passes_its_own_gate(self):
        t = wl.fallback_text(_cand(), "有人主張留白該先於文字設計", [("某研究", "https://x/y")])
        ok, why = wl.collision_ok(t, _cand(), self._cps())
        self.assertTrue(ok, why)
        self.assertIn(Q, t)                          # 逐字帶他的原文
        self.assertIn("https://x/y", t)              # 帶來源

    def test_fallback_survives_a_finding_that_quotes_itself(self):
        # 🌐 §2.01 ★ 真打一次 API 才看到的死結：grounding 回來的說法**自己帶「」**（引書名/術語）⇒
        # 第②閘判「引號裡有他沒寫過的東西」⇒ **連號稱「一定過得了」的程式模板都過不了**⇒ 這條 lane
        # 從上線起一次都不可能開口。§1.95 的單元測試剛好餵了沒有引號的 finding，所以全綠也蓋不到
        # （§1.93 同型：測試把同一個錯誤假設一起編碼進去）。
        f = "Adami 認為生命是一種「資訊現象」，他把資訊定義為「高於偶然性的預測能力」"
        t = wl.fallback_text(_cand(), f, [("某研究", "https://x/y")])
        ok, why = wl.collision_ok(t, _cand(), self._cps())
        self.assertTrue(ok, why)
        self.assertIn("資訊現象", t)                  # 字留著，只是不再被框成「他寫的」
        self.assertNotIn("「資訊現象」", t)

    def test_sanitize_repairs_instead_of_dropping_the_whole_reply(self):
        # 🌐 §2.01 §1.62 家規：修錯的那一處、別為了一個標記把整段好話丟掉退回模板
        good = (f"你最近在「教學｜{LAB}」這條線裡寫的是「{Q}」。"
                "而外面有人把它講成「另一種留白」，我覺得中間有個縫。")
        self.assertFalse(wl.collision_ok(good, _cand(), self._cps())[0])
        fixed = wl.sanitize_quotes(good, _cand(), self._cps())
        self.assertTrue(wl.collision_ok(fixed, _cand(), self._cps())[0])
        self.assertIn(Q, fixed)                       # 他的原文仍逐字、仍被引號框著
        self.assertIn(f"「教學｜{LAB}」", fixed)        # 線名也留著
        self.assertIn("另一種留白", fixed)              # 外面的話：字留著
        self.assertNotIn("「另一種留白」", fixed)        # 但引號拆掉＝不再宣稱是他寫的

    def test_prompt_forbids_quoting_the_outside_view(self):
        from telegram_monitor import persona
        r = persona.worldline_say_rule("教學｜繪本教學", Q, "外面的說法", "站名｜https://x/y")
        self.assertIn("只准用來包他寫的那句", r)


class SourcesTest(unittest.TestCase):
    def test_no_source_is_failure(self):
        self.assertFalse(wl.sources_ok([]))
        self.assertFalse(wl.sources_ok([("標題", "")]))
        self.assertTrue(wl.sources_ok([("標題", "https://a/b")]))

    def test_extract_grounding_is_defensive(self):
        from telegram_monitor import gemini
        data = {"candidates": [{"content": {"parts": [{"text": "內容"}]},
                                "groundingMetadata": {"groundingChunks": [
                                    {"web": {"uri": "https://a/b", "title": "T"}}, {}, {"web": {}}],
                                    "webSearchQueries": ["q1"]}}]}
        t, srcs, qs = gemini.extract_grounding(data)
        self.assertEqual((t, srcs, qs), ("內容", [("T", "https://a/b")], ["q1"]))

    def test_extract_grounding_survives_missing_metadata(self):
        from telegram_monitor import gemini
        t, srcs, qs = gemini.extract_grounding({"candidates": [{"content": {"parts": [{"text": "x"}]}}]})
        self.assertEqual((t, srcs, qs), ("x", [], []))


def _cfg(on=True, **kw):
    d = dict(worldline_enabled=on, worldline_cooldown_h=24, worldline_max_searches=40,
             worldline_tool_field="google_search", notify_cooldown_min=30, dry_run=True,
             quiet_start=1, quiet_end=6, timezone="Asia/Taipei", self_roster_enabled=True)
    d.update(kw)
    return SimpleNamespace(**d)


def _state(allow=(LAB,), **kw):
    s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
    s.worldline_allow = list(allow)
    s.last_user_msg_ts = NOW - 3 * 3600
    s.last_push_ts = NOW - 10 * 3600
    s.last_worldline_ts = 0
    for k, v in kw.items():
        setattr(s, k, v)
    return s


class _Cl:
    dry_run = False

    def __init__(self, ok=True):
        self.sent, self.ok = [], ok

    def send(self, t):
        self.sent.append(t)
        return self.ok


def _coach(finding="有人主張留白該先於文字設計", sources=(("某研究", "https://x/y"),),
           raise_exc=None, hook=None):
    c = SimpleNamespace(enabled=True, api_key="k", model="m",
                        meter=SimpleNamespace(record=lambda *a, **k: None))
    c.reply = lambda *a, **k: ""
    from telegram_monitor import gemini

    def _g(*a, **k):
        if hook is not None:
            hook.append(1)
        if raise_exc:
            raise raise_exc
        return (finding, list(sources), ["q"])
    c._patch = _g
    gemini.generate_grounded = _g
    return c


def _dt():
    return datetime.fromtimestamp(NOW, timezone.utc)


def _data():
    return {"records": [{"topicLabel": LAB, "category": "教學", "text": "第一次試留白",
                         "ts": "2026-07-24T14:00:00Z", "id": "a"},
                        {"topicLabel": LAB, "category": "教學", "text": Q,
                         "ts": "2026-07-26T14:00:00Z", "id": "b"}]}


class LaneTest(unittest.TestCase):
    def tearDown(self):
        import importlib
        from telegram_monitor import gemini
        importlib.reload(gemini)

    def test_flag_off_is_noop(self):
        s, cl, called = _state(), _Cl(), []
        monitor._worldline_emit(cl, s, _cfg(on=False), _coach(hook=called), _dt(), data=_data())
        self.assertEqual((cl.sent, called), ([], []))

    def test_speaks_with_quote_and_source(self):
        s, cl = _state(), _Cl()
        monitor._worldline_emit(cl, s, _cfg(), _coach(), _dt(), data=_data())
        out = "".join(cl.sent)
        self.assertTrue(cl.sent)
        self.assertIn(Q, out)                        # 他的原文逐字
        self.assertIn("https://x/y", out)            # 來源
        self.assertEqual(s.worldline_ledger[-1]["label"], LAB)

    def test_every_message_carries_its_source(self):
        # 🌐 §2.01 實測：LLM 版常把來源整個略掉（prompt 給了它、它沒寫）⇒「必須帶得出來源」那條紅線
        # 原本只有退回程式模板時才成立。現在確定性補一行，不靠 LLM 記得。
        s, cl = _state(), _Cl()
        co = _coach()
        co.reply = lambda *a, **k: f"你在「教學｜{LAB}」寫的「{Q}」讓我想到外面一個說法。"
        monitor._worldline_emit(cl, s, _cfg(), co, _dt(), data=_data())
        self.assertIn("https://x/y", "".join(cl.sent))

    def test_no_sources_stays_silent(self):
        s, cl = _state(), _Cl()
        monitor._worldline_emit(cl, s, _cfg(), _coach(sources=()), _dt(), data=_data())
        self.assertEqual(cl.sent, [])                # 沒來源＝不說「我查到」
        self.assertEqual(s.worldline_search_n, 1)    # 但花掉的那次要記帳

    def test_newsy_finding_stays_silent(self):
        s, cl = _state(), _Cl()
        monitor._worldline_emit(cl, s, _cfg(), _coach(finding="今天有報導指出繪本市場大跌"), _dt(), data=_data())
        self.assertEqual(cl.sent, [])

    def test_api_error_recorded_for_probe(self):
        from telegram_monitor import gemini
        s, cl = _state(), _Cl()
        monitor._worldline_emit(cl, s, _cfg(), _coach(raise_exc=gemini.GeminiError("400 unknown field google_search")),
                                _dt(), data=_data())
        self.assertEqual(cl.sent, [])
        self.assertIn("400", s.worldline_probe)      # ★ 上線後靠這個字串知道要改哪個 tools 欄位名

    def test_cooldown(self):
        s, cl = _state(last_worldline_ts=NOW - 3600), _Cl()
        monitor._worldline_emit(cl, s, _cfg(), _coach(), _dt(), data=_data())
        self.assertEqual(cl.sent, [])

    def test_budget_cap(self):
        s, cl = _state(worldline_search_n=40), _Cl()
        monitor._worldline_emit(cl, s, _cfg(), _coach(), _dt(), data=_data())
        self.assertEqual(cl.sent, [])


class ForceTest(unittest.TestCase):
    """🌐 §2.01 使用者：「要有作用，等了一陣子還是沒看到」——他親口要的那次要**當場**做，
    沒做成也要**當場講原因**（原本每條失敗路徑都只 return，安靜到看不出差別）。"""

    def tearDown(self):
        import importlib
        from telegram_monitor import gemini
        importlib.reload(gemini)

    def test_explicit_search_revisits_old_authorized_line(self):
        data = _data()
        for row in data["records"]:
            row["ts"] = "2026-01-01T14:00:00Z" if row["id"] == "a" else "2026-01-02T14:00:00Z"
        cl, called = _Cl(), []
        result = monitor._worldline_emit(cl, _state(), _cfg(),
                                        _coach(hook=called), _dt(), data=data, force=True)
        self.assertEqual(result, "")
        self.assertEqual(len(called), 1)
        self.assertTrue(cl.sent)

    def test_force_bypasses_politeness_gates(self):
        # 他在場＋5 分鐘前才推播過＋自有冷卻還沒過：自動路徑三道都擋，force 全部跳過
        s = _state(last_worldline_ts=NOW - 3600, last_push_ts=NOW - 300)
        s.last_user_msg_ts = NOW - 10
        cl = _Cl()
        auto = monitor._worldline_emit(cl, s, _cfg(), _coach(), _dt(), data=_data())
        self.assertEqual(cl.sent, [])                                   # 自動路徑：安靜
        self.assertIn("你人在", auto)                                    # 但說得出是哪道閘擋的
        r = monitor._worldline_emit(cl, s, _cfg(), _coach(), _dt(), data=_data(), force=True)
        self.assertEqual(r, "")                                         # ""＝真的開口了
        self.assertTrue(cl.sent)

    def test_force_still_respects_allowlist_and_budget(self):
        # ★ 白名單與總量是**隱私與花錢**的閘，不是禮貌的閘——force 不得跨過
        cl, called = _Cl(), []
        r = monitor._worldline_emit(cl, _state(allow=[]), _cfg(), _coach(hook=called), _dt(),
                                    data=_data(), force=True)
        self.assertIn("還沒授權", r)
        self.assertEqual((cl.sent, called), ([], []))
        r2 = monitor._worldline_emit(cl, _state(worldline_search_n=40), _cfg(), _coach(hook=called),
                                     _dt(), data=_data(), force=True)
        self.assertIn("總量上限", r2)
        self.assertEqual(called, [])                                    # 一次 API 都沒打

    def test_every_silent_path_now_says_why(self):
        import importlib

        from telegram_monitor import gemini
        # ⚠️ _coach() 會 patch gemini 模組層的 generate_grounded ⇒ 每個 case 要**現做**（先前一次寫成先
        # 全部建好再跑，最後一個 patch 蓋掉前兩個，測試當場抓到）
        cases = [(lambda: _coach(sources=()), "來源"),
                 (lambda: _coach(finding="今天有報導指出繪本市場大跌"), "新聞"),
                 (lambda: _coach(raise_exc=gemini.GeminiError("400 unknown field")), "400")]
        for make, frag in cases:
            importlib.reload(gemini)
            r = monitor._worldline_emit(_Cl(), _state(), _cfg(), make(), _dt(), data=_data(), force=True)
            self.assertIn(frag, r or "", frag)

    def test_flag_off_says_so_instead_of_silence(self):
        r = monitor._worldline_emit(_Cl(), _state(), _cfg(on=False), _coach(), _dt(), data=_data(), force=True)
        self.assertIn("WORLDLINE=0", r)

    def test_command_wired(self):
        src = io.open("telegram_monitor/monitor.py", encoding="utf-8").read()
        self.assertIn('_arg in ("now", "現在", "撞", "撞一下", "去查", "查")', src)
        self.assertIn("force=True", src)
        self.assertIn('client.send("🌐 這次沒查成：" + _wl_r)', src)      # 沒做成＝當場講原因


class AuditTest(unittest.TestCase):
    def test_empty_allowlist_tells_how_to_authorize(self):
        t = monitor._worldline_audit(_state(allow=[]), _cfg(), _data(), NOW)
        self.assertIn("還不能查任何東西", t)
        self.assertIn("/worldline allow", t)          # 不講這個他會以為壞了

    def test_shows_exact_string_that_leaves_the_machine(self):
        t = monitor._worldline_audit(_state(), _cfg(), _data(), NOW)
        self.assertIn(wl.build_query(LAB), t)
        self.assertIn("只有這行字會離開這台機器", t)

    def test_stays_short_and_drops_internals(self):
        # 🌐 §2.01 使用者：「描述需要簡化簡單清楚點」——砍掉他不需要決定的東西
        t = monitor._worldline_audit(_state(), _cfg(), _data(), NOW)
        self.assertLessEqual(len(t.splitlines()), 6)
        for noise in ("回返", "可撞的", "線 ", "不會被送出去"):
            self.assertNotIn(noise, t)
        self.assertNotIn(Q, t)                        # 那筆本來就不外送，列出來只會讓人以為它會
        self.assertIn("/worldline now", t)            # 想現在看就有得催

    def test_when_line_counts_the_politeness_gates(self):
        # 🌐 §2.01 舊版說「沒有被擋，下一拍你不在場時就會去撞」，卻沒算 _proactive_ok 與共用 30 分反連發
        # ⇒ 使用者一直在聊天時它結構上不可能開口，對帳卻說「沒被擋」。
        s = _state()
        s.last_user_msg_ts = NOW - 30                 # 他剛說完話＝在場
        self.assertIn("你人在", monitor._worldline_audit(s, _cfg(), _data(), NOW))
        s2 = _state()
        s2.last_push_ts = NOW - 5 * 60                # 5 分鐘前才主動說過話
        self.assertIn("分鐘前才主動說過話", monitor._worldline_audit(s2, _cfg(), _data(), NOW))

    def test_audit_names_the_unanswered_initiative_gate(self):
        s = _state()
        s.last_user_msg_ts = NOW - 3600
        s.last_push_ts = NOW - 1800
        cfg = _cfg(unanswered_proactive_guard_enabled=True)
        self.assertIn("還沒收到回應", monitor._worldline_audit(s, cfg, _data(), NOW))

    def test_command_wired(self):
        src = io.open("telegram_monitor/monitor.py", encoding="utf-8").read()
        self.assertIn('low.startswith(("/worldline", "/外面", "/世界"))', src)
        self.assertIn('_arg.startswith("allow ")', src)
        self.assertIn('_arg.startswith("mute ")', src)


class ConfigTest(unittest.TestCase):
    def test_flag_default_on_but_allowlist_still_gates(self):
        # 🌐 §2.00 旗標改預設開（使用者要它真的運作、且他不編 .env）；**同意閘改由白名單獨扛**——
        # 所以這裡同時釘死「白名單空＝完全不動」那條路徑仍在（見 AllowlistTest）。
        src = io.open("telegram_monitor/config.py", encoding="utf-8").read()
        self.assertIn('worldline_enabled=_bool("WORLDLINE", True)', src)
        self.assertIn("WORLDLINE_TOOL_FIELD", src)
        msrc = io.open("telegram_monitor/monitor.py", encoding="utf-8").read()
        self.assertIn("if not allow:\n        return", msrc)                # ★ 白名單空＝第一時間 return

    def test_getattr_defaults_false(self):
        self.assertFalse(getattr(SimpleNamespace(), "worldline_enabled", False))

    def test_roster_registered(self):
        from telegram_monitor import roster
        ab = roster.by_key("worldline")
        self.assertIsNotNone(ab)
        self.assertEqual(ab.audit, "/worldline")


if __name__ == "__main__":
    unittest.main()
