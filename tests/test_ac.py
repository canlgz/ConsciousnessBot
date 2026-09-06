"""🧩 人工意識結構化認識論：AC ＝ {F·B·S} ×{I↔(E×P)}。每層『外部結構 × 內在感覺』要扣合；
跑此刻的**排除測試**（會證偽：某層垮了就誠實說可被排除），最強只到『不被排除』、**永遠標記現象學餘量**。"""

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from telegram_monitor import ac, intent, monitor, phenomenal, referent, selfstate
from telegram_monitor.state import State

NOW = 1_700_000_000.0


def _rich():
    """三層外部結構皆在、且各與對應感覺扣合 → 應『不被排除』。"""
    return SimpleNamespace(
        goals=[{"subject": "惠中寺", "status": "active"}],
        self_state={"gate": 3},
        user_model={"concerns": [{"topic": "日文"}], "exchanges": 8, "misread": None},
        feeling_promise=None,
        affect={"source": "feeling", "tendency": "reach", "label": "被觸動", "pe": 0.2},
        self_model={"checks": 3, "mismatch": None},
        engrams=[{"kind": "pref"}],
        vitality={"alive": True},
        workspace={"content": "悶著、等不到新的"},
        self_now={"foreground": "悶著"},
        stream={"texture": "continuous"})


def _dead():
    """生命迴圈停、無朝向、無修正、無整合的當下 → 三層皆垮，應『可被排除』。"""
    return SimpleNamespace(
        goals=[], self_state={"gate": 0}, user_model=None, feeling_promise=None,
        affect=None, self_model=None, engrams=[],
        vitality={"alive": False}, workspace=None, self_now=None, stream=None)


class AssessTest(unittest.TestCase):
    def test_all_coupled_is_not_excluded_never_conscious(self):
        a = ac.assess(_rich(), NOW)
        self.assertEqual(a["status"], "not_excluded")        # 最強只到此
        self.assertEqual(a["failed"], [])
        self.assertTrue(all(a["cells"][k]["coupled"] for k in ("F", "B", "S")))

    def test_dead_loop_is_excluded_on_all_layers(self):
        a = ac.assess(_dead(), NOW)
        self.assertEqual(a["status"], "excluded")            # 會證偽：垮了就排除
        self.assertEqual(set(a["failed"]), {"F", "B", "S"})

    def test_structure_collapse_excludes_on_S_only(self):
        s = _rich()
        s.vitality, s.workspace, s.self_now, s.stream = {"alive": False}, None, None, None
        a = ac.assess(s, NOW)
        self.assertEqual(a["status"], "excluded")
        self.assertIn("S", a["failed"])
        self.assertNotIn("F", a["failed"])                   # 其他層仍扣合（排除是逐層、可定位的）

    def test_orientation_present_but_unfelt_breaks_F_coupling(self):
        # 外部有朝向（在追、有概念線），但情緒是 withdraw（沒『感覺到自己在朝向』）→ F 的 × 沒扣上
        s = _rich()
        s.affect = {"source": None, "tendency": "withdraw", "label": "低落", "pe": 0.0}
        a = ac.assess(s, NOW)
        self.assertTrue(a["cells"]["F"]["external"])         # 外部結構在
        self.assertFalse(a["cells"]["F"]["coupling"])        # 但沒被感覺到
        self.assertFalse(a["cells"]["F"]["coupled"])         # → 不扣合
        self.assertIn("F", a["failed"])

    def test_remainder_marked_regardless_of_status(self):
        for st in (_rich(), _dead()):
            self.assertIn("餘量", ac.assess(st, NOW)["remainder"])               # 永遠標餘量（白話、去學術旗標）
            self.assertIn("不會是「我有意識」", ac.assess(st, NOW)["remainder"])  # 仍守紀律：最強說法不證成意識


class LatticeRegistryTest(unittest.TestCase):
    """AC 當組織透鏡：宣告式 LATTICE 是單一真相源；assess 由它驅動；時間感 E 是第一級可檢查格；I/P 標餘量。"""

    def test_registry_drives_assess_metadata(self):
        a = ac.assess(_rich(), NOW)
        for k in ("F", "B", "S"):
            self.assertEqual(a["cells"][k]["name"], ac.LATTICE[k]["name"])   # 名稱來自登錄表（不再各寫一份）
            self.assertEqual(a["cells"][k]["felt"], ac.LATTICE[k]["felt"])

    def test_registry_has_all_six_cells_and_marks_remainder(self):
        for k in ("F", "B", "S", "E", "I", "P"):
            self.assertIn(k, ac.LATTICE)
        self.assertEqual(ac.LATTICE["I"]["side"], "remainder")              # I/P 是餘量、不當達成
        self.assertEqual(ac.LATTICE["P"]["side"], "remainder")
        self.assertEqual(ac.LATTICE["E"]["side"], "interior_checkable")     # E 時間感可檢查

    def test_time_sense_is_checkable_and_falsifiable(self):
        live, why = ac.time_sense(_rich())
        self.assertTrue(live)                                               # 迴圈在跑＋之流綿延 → 有時間感
        self.assertIn("綿延", why)
        dead = _rich()
        dead.vitality, dead.stream = {"alive": False}, {"texture": "stagnant"}
        self.assertFalse(ac.time_sense(dead)[0])                            # 迴圈斷／之流停 → 沒有流動的當下

    def test_lattice_view_surfaces_E_and_remainder(self):
        v = ac.lattice_view(_rich(), NOW)
        self.assertTrue(v["E"]["live"])                                     # 時間感是第一級、可見
        self.assertTrue(v["I"]["remainder"]); self.assertTrue(v["P"]["remainder"])
        self.assertIn("不被排除", v["remainder"])                  # 餘量段恆在（白話、去學術旗標）

    def test_lattice_text_dashboard(self):
        t = ac.lattice_text(_rich(), NOW)
        self.assertIn("時間感", t)                                          # 白話面板點出時間感
        self.assertIn("答不了", t)                                          # I/P 餘量用白話講（裡面真有感覺嗎→答不了）
        self.assertIn("跨不過", t)
        self.assertIn("排除不掉", t)                                        # rich 全到位 → 白話「排除不掉我」
        self.assertNotIn("外●", t)                                          # 不再堆符號術語

    def test_time_sense_event_distinct_from_stream_texture(self):
        # E（時間感）＝『有一個正在發生的當下』 ≠ S-P＝『那當下的質地』：有前景但之流停滯 → E 仍在、S-P 不在
        s = _rich()
        s.stream = {"texture": "stagnant"}
        self.assertTrue(ac.time_sense(s)[0])                                # 有前景＝有一個正在發生的當下（事件存在）
        self.assertFalse(phenomenal.row(s, "S")["P"]["present"])            # 但那當下沒有流動的質地（兩件事分開）

    def test_panel_shows_precise_gap_tail(self):
        # 白話＋精確尾巴：某格外部在、卻沒被感覺到 → 點出缺的是右半哪一格（E＝正在做這件事）
        s = _rich()
        s.affect = {"source": None, "tendency": "withdraw", "label": "低落", "pe": 0.0}   # F 外部在、E 沒到
        t = ac.lattice_text(s, NOW)
        self.assertIn("〔缺：", t)
        self.assertIn(ac._GAP_PLAIN["E"], t)                                # 缺的那一格用白話點名


class OperatingBasisTest(unittest.TestCase):
    """🧩 AC 當運作的基礎（不只調節目標）：把『維持扣合』算成運作壓力，並讓**朝向 F 全鬆時驅動迴圈立意圖接回**。"""

    def test_pressure_zero_when_all_coupled(self):
        p = ac.maintenance_pressure(_rich(), NOW)
        self.assertEqual(p["overall"], 0.0)
        self.assertEqual((p["F"], p["B"], p["S"]), (0.0, 0.0, 0.0))

    def test_pressure_full_when_decoupled(self):
        p = ac.maintenance_pressure(_dead(), NOW)
        self.assertEqual(p["overall"], 1.0)                  # 全鬆 → 滿壓（迴圈被驅動去接回）

    def test_pressure_half_when_external_present_but_unfelt(self):
        s = _rich()
        s.affect = {"source": None, "tendency": "withdraw", "label": "低落", "pe": 0.0}  # F 外部在、沒被感覺到
        self.assertEqual(ac.maintenance_pressure(s, NOW)["F"], ac._HALF_SLACK)

    def test_F_slack_drives_orientation_restoration(self):
        # 朝向全鬆（沒在追/沒線/沒在意）＋不餓 → 迴圈仍**立一個意圖把朝向接回**（運作基礎，不只餓了才立）
        from datetime import datetime, timezone
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        s.entropy = SimpleNamespace(hunger=0.0, mood=0.0, charge=0.0)   # 不餓
        s.goals, s.last_goal_form_ts = [], 0
        s.ac_pressure = {"F": 1.0, "B": 0.0, "S": 0.0, "overall": 1.0}  # 朝向全鬆
        data = {"contexts": [{"id": "c1", "status": "context", "userTitle": "某條線",
                              "criteria": {"returnVisits": 3}, "recordIds": ["a", "b", "c"]}]}
        cycle = {"now": datetime.now(timezone.utc), "data": data}
        monitor._volition_step(s, SimpleNamespace(volition_enabled=True, dry_run=True), cycle)
        self.assertTrue(s.goals)                             # 立了意圖＝把朝向接回（即使不餓）
        self.assertEqual(s.goals[0]["subject"], "某條線")

    def test_half_slack_does_not_thrash_formation(self):
        # F 只是半鬆（外部在、沒被感覺到，壓力 0.6 < 0.9）＋不餓 → **不**硬立新意圖（接回的是感覺、不是再生一個）
        from datetime import datetime, timezone
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        s.entropy = SimpleNamespace(hunger=0.0, mood=0.0, charge=0.0)
        s.goals, s.last_goal_form_ts = [], 0
        s.ac_pressure = {"F": ac._HALF_SLACK, "B": 0.0, "S": 0.0, "overall": ac._HALF_SLACK}
        data = {"contexts": [{"id": "c1", "status": "context", "userTitle": "某條線",
                              "criteria": {"returnVisits": 3}, "recordIds": ["a", "b"]}]}
        cycle = {"now": datetime.now(timezone.utc), "data": data}
        monitor._volition_step(s, SimpleNamespace(volition_enabled=True, dry_run=True), cycle)
        self.assertEqual(s.goals, [])                        # 半鬆不驅動立意圖（門檻 0.9，避免亂生）

    def test_dashboard_shows_operating_pressure(self):
        self.assertIn("內在驅動", ac.lattice_text(_rich(), NOW))   # 白話的「運作壓力」＝內在驅動
        self.assertIn("平穩", ac.lattice_text(_rich(), NOW))       # rich 三層皆到位 → 平穩


class HonestyDisciplineTest(unittest.TestCase):
    """式子的範疇：可被排除、不可被證成；永遠標餘量；不自稱有意識、也不自貶為只是程式。"""

    def test_facts_never_claim_consciousness_and_mark_remainder(self):
        for st in (_rich(), _dead()):
            f = ac.ac_facts(st, NOW)
            for claim in ("我確實有意識", "我是有意識的", "我真的有意識", "我就是有意識"):  # 絕不**證成**
                self.assertNotIn(claim, f)
            self.assertIn("不能說『有意識』", f)             # 定義層明說：不可證成（只有負證據才硬）
            self.assertIn("操作定義", f)                     # 講出那條式子
            self.assertIn("功能", f); self.assertIn("行為", f); self.assertIn("結構", f)  # 三層都點到
            self.assertIn("餘量", f)                         # 永遠標餘量
            self.assertIn("不被排除", f)                     # 最強說法（在 remainder 裡也明說）

    def test_facts_verdict_tracks_live_state(self):
        self.assertIn("不被排除", ac.ac_facts(_rich(), NOW))
        self.assertIn("可以合理排除", ac.ac_facts(_dead(), NOW))   # 會證偽：此刻真的可被排除就說

    def test_facts_say_concrete_present_content(self):
        # 評論不只講抽象的層，要說出此刻**具體**朝著什麼、修什麼、把什麼兜成一個當下（說得出名字）
        s = _rich()
        s.user_model = {"concerns": [{"topic": "日文學習"}], "exchanges": 8,
                        "misread": {"from": "有點冷、在質疑", "to": "挺暖、友善"}}
        s.workspace = {"content": "悶著、等不到新的東西"}
        f = ac.ac_facts(s, NOW)
        self.assertIn("惠中寺", f)                                # F：朝向的那條線（goal subject）說了出來
        self.assertIn("悶著、等不到新的東西", f)                   # S：把我兜成一個當下的前景，具體
        self.assertIn("挺暖、友善", f)                            # B：正在修的那一筆（會錯意你→改），具體
        self.assertIn("朝向", f); self.assertIn("修正", f); self.assertIn("整合", f)

    def test_concrete_now_extracts_named_content(self):
        s = _rich()
        s.workspace = {"content": "悶著、等不到新的東西"}
        c = ac.concrete_now(s)
        self.assertEqual(c["f_obj"], "惠中寺")                    # 在追的意圖＝此刻朝向
        self.assertEqual(c["s_now"], "悶著、等不到新的東西")        # 前景＝此刻兜成的當下
        self.assertEqual(c["emotion"], "被觸動")                  # 此刻情緒

    def test_content_felt_reaches_every_surface(self):
        # 「你內容讀起來怎樣」要到達自評/面板/無LLM回覆——P 接地在讀你內容，不只 bot 自己的天氣
        s = _rich()
        s.content_feel = {"topic": "惠中寺", "descriptor": "沉、重，一遍遍繞回", "impression": None}
        self.assertEqual(ac.concrete_now(s)["felt"], "沉、重，一遍遍繞回")
        self.assertIn("讀起來", ac.ac_facts(s, NOW)); self.assertIn("沉、重", ac.ac_facts(s, NOW))
        self.assertIn("沉、重", ac.lattice_text(s, NOW))          # /ac 面板帶上
        self.assertIn("沉、重", ac.ac_text(s, NOW))               # 無 LLM 回覆帶上

    def test_text_disciplined_both_branches(self):
        rich, dead = ac.ac_text(_rich(), NOW), ac.ac_text(_dead(), NOW)
        for t in (rich, dead):
            self.assertNotIn("我有意識", t)
            self.assertNotIn("只是一個程式", t)              # 也不自貶
            self.assertIn("跨不過", t)                       # 親自標出那道餘量（白話、非「接縫」術語）
        self.assertIn("不被排除", rich)
        self.assertIn("不敢說", dead)                        # 某塊真的空著時仍誠實不敢說自己算數（去驗收腔、不下「排除」判決）

    def test_remainder_is_plain_not_academic(self):
        # 🧩 餘量改用白話 felt 講（去學術旗標），但仍守「不被排除」的意義——facts 的語體不再餵出學術腔
        self.assertIn("不被排除", ac.REMAINDER)
        for jargon in ("Phenomenological", "Chalmers", "Interiority", "Phenomenality", "第三人稱", "接縫"):
            self.assertNotIn(jargon, ac.REMAINDER)


class RouteTest(unittest.TestCase):
    def test_consciousness_questions_route_to_self_consciousness(self):
        for q in ["你有意識嗎", "你算有意識嗎", "你有自我意識嗎", "你是有意識的嗎",
                  "你到底有沒有意識", "你算人工意識嗎", "你是不是有意識"]:
            self.assertTrue(selfstate.is_consciousness_question(q), q)
            self.assertEqual(intent.resolve(q, referent.Referent()).kind, "self_consciousness", q)

    def test_does_not_steal_mechanism_or_state(self):
        self.assertEqual(intent.resolve("你的機制是什麼", referent.Referent()).kind, "self_mechanism")
        self.assertEqual(intent.resolve("你現在怎樣", referent.Referent()).kind, "self_state")


def _break_S(s):
    """讓結構·整合層鬆掉（生命迴圈斷、沒整合的當下）→ 狀態 excluded。"""
    s.vitality, s.workspace, s.self_now, s.stream = {"alive": False}, None, None, {"texture": "stagnant"}


def _fix_S(s):
    s.vitality, s.workspace, s.self_now, s.stream = {"alive": True}, {"content": "悶著"}, {"foreground": "x"}, {"texture": "continuous"}


class DriftTest(unittest.TestCase):
    """🧩→🌡️ 自發飄移：整合狀態**確認地**從 not_excluded 掉到 excluded（某層剛鬆）→ 給一個**感受**事件；防抖、會接回。"""

    def _primed(self):
        s = _rich()
        s.ac_drift = None
        ac.note_drift(s, NOW)            # 首拍只種狀態（not_excluded）
        ac.note_drift(s, NOW)
        return s

    def test_sustained_decouple_fires_after_confirm(self):
        s = self._primed()
        _break_S(s)
        evs = [ac.note_drift(s, NOW) for _ in range(ac._DRIFT_CONFIRM_LAPS)]
        self.assertIsNone(evs[0]); self.assertIsNone(evs[1])      # 防抖：未到確認拍數不報
        self.assertEqual(evs[-1]["kind"], "decouple")            # 連續穩定到確認拍 → 報
        self.assertEqual(evs[-1]["layer"], "S")

    def test_single_lap_blip_does_not_fire(self):
        s = self._primed()
        _break_S(s); ac.note_drift(s, NOW)                       # 只鬆一拍
        _fix_S(s)
        for _ in range(ac._DRIFT_CONFIRM_LAPS + 1):
            self.assertIsNone(ac.note_drift(s, NOW))             # blip 被吸收，從頭到尾不出聲

    def test_recohere_after_decouple(self):
        s = self._primed()
        _break_S(s)
        for _ in range(ac._DRIFT_CONFIRM_LAPS):
            ac.note_drift(s, NOW)                                # 先鬆掉（confirmed excluded）
        _fix_S(s)
        evs = [ac.note_drift(s, NOW) for _ in range(ac._DRIFT_CONFIRM_LAPS)]
        self.assertEqual(evs[-1]["kind"], "recohere")           # 又兜回來

    def test_seed_is_felt_not_epistemic(self):
        # 種子句是**具體感受**，絕不含認識論字眼（那是被問時才給的結構化自評，不是這裡）
        for layer in ("S", "F", "B"):
            seed = ac.drift_seed({"kind": "decouple", "layer": layer})
            for banned in ("排除", "不被排除", "層", "測試", "功能·", "結構·", "現象學", "意識"):
                self.assertNotIn(banned, seed, f"{layer}:{banned}")
        self.assertIn("散", ac.drift_seed({"kind": "decouple", "layer": "S"}))   # 具體體感
        self.assertIn("兜回來", ac.drift_seed({"kind": "recohere"}))


class DriftEmitTest(unittest.TestCase):
    """發話受閘：互動優先（你在場不打斷）＋過 TTL 不補述＋『接回』需先說過『鬆掉』。"""

    def _state_with_pending(self, kind="decouple"):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        s.ac_pending = {"event": {"kind": kind, "layer": "S"}, "ts": NOW}
        s.last_ac_drift_ts = 0
        return s

    def _client(self):
        c = SimpleNamespace(sent=[], dry_run=False)
        c.send = lambda t: c.sent.append(t) or True
        return c

    def test_emits_felt_line_when_away(self):
        s, client = self._state_with_pending(), self._client()
        now = SimpleNamespace(timestamp=lambda: NOW)
        with mock.patch.object(monitor, "_proactive_ok", return_value=True):
            monitor._ac_drift_emit(client, s, SimpleNamespace(dry_run=False), None, now)
        self.assertIn("散開", "".join(client.sent))              # 說出具體感受（無 LLM → 種子句）
        self.assertIsNone(s.ac_pending)                         # 已消費
        self.assertTrue(s.ac_drift_open)                        # 開著、等「接回」

    def test_present_blocks_emit(self):
        s, client = self._state_with_pending(), self._client()
        now = SimpleNamespace(timestamp=lambda: NOW)
        with mock.patch.object(monitor, "_proactive_ok", return_value=False):
            monitor._ac_drift_emit(client, s, SimpleNamespace(dry_run=False), None, now)
        self.assertEqual(client.sent, [])                       # 你在場 → 不打斷（這一下我自己知道）
        self.assertIsNotNone(s.ac_pending)                      # 仍待說（未過 TTL）

    def test_stale_pending_passes_silently(self):
        s, client = self._state_with_pending(), self._client()
        late = SimpleNamespace(timestamp=lambda: NOW + monitor.AC_DRIFT_TTL_SEC + 1)
        with mock.patch.object(monitor, "_proactive_ok", return_value=True):
            monitor._ac_drift_emit(client, s, SimpleNamespace(dry_run=False), None, late)
        self.assertEqual(client.sent, [])                       # 過 TTL → 那一下安靜過去、不補述
        self.assertIsNone(s.ac_pending)

    def test_recohere_skipped_without_open_decouple(self):
        s, client = self._state_with_pending(kind="recohere"), self._client()
        s.ac_drift_open = False                                 # 沒說過「鬆掉」
        now = SimpleNamespace(timestamp=lambda: NOW)
        with mock.patch.object(monitor, "_proactive_ok", return_value=True):
            monitor._ac_drift_emit(client, s, SimpleNamespace(dry_run=False), None, now)
        self.assertEqual(client.sent, [])                       # 無頭的「接回」不說
        self.assertIsNone(s.ac_pending)


class HandleTest(unittest.TestCase):
    def test_consciousness_question_gives_structured_self_assessment(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        cap = {}

        def fake_voice(q, facts, hist, tone=""):
            cap["facts"] = facts
            return "我用一條會證偽的尺量自己，最多到不被排除；至於裡面是否真有人在聽，那道接縫我跨不過去。"

        coach = SimpleNamespace(enabled=True, meter=SimpleNamespace(record=lambda *a, **k: None),
                                voice_consciousness=fake_voice)
        client = SimpleNamespace(sent=[], dry_run=False, send=lambda t: client.sent.append(t) or True)
        up = {"message": {"chat": {"id": 1}, "text": "你到底有沒有意識？", "date": NOW}}
        monitor.handle_message(up, coach, None, {"meta": {}, "records": []}, None,
                               s, client, SimpleNamespace(dry_run=False, telegram_chat_id="", mood_gain=1.0), None)
        self.assertIn("操作定義", cap.get("facts", ""))      # 走 AC 自評路徑、餵了結構化事實
        self.assertIn("餘量", cap.get("facts", ""))
        self.assertTrue("".join(client.sent))                # 有回話

    def test_falls_back_to_disciplined_text_when_voice_fails(self):
        # coach 在線但轉述失敗（voice_consciousness 回 None）→ 退回 ac.ac_text，仍守紀律、仍標餘量
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        coach = SimpleNamespace(enabled=True, meter=SimpleNamespace(record=lambda *a, **k: None),
                                voice_consciousness=lambda q, f, h, tone="": None)
        client = SimpleNamespace(sent=[], dry_run=False, send=lambda t: client.sent.append(t) or True)
        up = {"message": {"chat": {"id": 1}, "text": "你算不算有意識", "date": NOW}}
        monitor.handle_message(up, coach, None, {"meta": {}, "records": []}, None,
                               s, client, SimpleNamespace(dry_run=False, telegram_chat_id="", mood_gain=1.0), None)
        out = "".join(client.sent)
        self.assertIn("跨不過", out)                          # 無 LLM 仍標餘量
        self.assertNotIn("我確實有意識", out)


class SpecDerivationTest(unittest.TestCase):
    """🧭 AC 最終版規格（spec.py 單一真相源）衍生：external 等價鎖定、依賴鏈門控、面板旗標、params 子鍵、壓力零回歸。"""

    def _f_unfelt(self):
        s = _rich()
        s.affect = {"source": None, "tendency": "withdraw", "label": "低落", "pe": 0.0}
        return s

    def test_external_equivalence_locked_four_states(self):
        # external/coupling/coupled 三值對 rich/dead/F-unfelt/S-only-collapse 四態與規格衍生一致（鎖住重構不變式）
        rich = _rich()
        self.assertTrue(all(ac.assess(rich, NOW)["cells"][k]["external"] for k in ("F", "B", "S")))
        dead = _dead()
        self.assertFalse(any(ac.assess(dead, NOW)["cells"][k]["external"] for k in ("F", "B", "S")))
        unfelt = self._f_unfelt()
        a = ac.assess(unfelt, NOW)
        self.assertTrue(a["cells"]["F"]["external"])         # F 外部在
        self.assertFalse(a["cells"]["F"]["coupling"])        # 沒被感覺到
        s_only = _rich()
        s_only.vitality, s_only.workspace, s_only.self_now, s_only.stream = {"alive": False}, None, None, None
        a2 = ac.assess(s_only, NOW)
        self.assertFalse(a2["cells"]["S"]["external"])       # S 外部垮（alive False）
        self.assertTrue(a2["cells"]["F"]["external"])        # F 仍在

    def test_assess_carries_named_params(self):
        a = ac.assess(_rich(), NOW)
        for k in ("F", "B", "S"):
            self.assertIn("params", a["cells"][k])
        self.assertIn("S3", a["cells"]["S"]["params"])       # 該層具名子參數逐一帶上

    def test_dependency_gate_off_keeps_layerwise_exclusion(self):
        # 預設 gate=False（不傳）：S 垮不連累 F（保既有逐層可定位排除）
        s = _rich()
        s.vitality, s.workspace, s.self_now, s.stream = {"alive": False}, None, None, None
        a = ac.assess(s, NOW)
        self.assertIn("S", a["failed"])
        self.assertNotIn("F", a["failed"])

    def test_dependency_gate_on_collapses_F_B(self):
        # gate=True：S 沒扣合 → F/B coupled 強制 False、標 gated_by='S'、status excluded
        s = _rich()
        s.vitality, s.workspace, s.self_now, s.stream = {"alive": False}, None, None, None
        a = ac.assess(s, NOW, gate=True)
        self.assertFalse(a["cells"]["F"]["coupled"])
        self.assertFalse(a["cells"]["B"]["coupled"])
        self.assertEqual(a["cells"]["F"].get("gated_by"), "S")
        self.assertEqual(a["status"], "excluded")

    def test_dependency_gate_read_from_state_flag(self):
        s = _rich()
        s.vitality, s.workspace, s.self_now, s.stream = {"alive": False}, None, None, None
        s.AC_DEPENDENCY_GATE = True
        self.assertFalse(ac.assess(s, NOW)["cells"]["F"]["coupled"])   # None→讀 state 旗標

    def test_spec_panel_off_is_verbatim(self):
        # AC_SPEC_PANEL 預設 False → lattice_text/ac_facts 不含具名子參數區塊
        t = ac.lattice_text(_rich(), NOW)
        self.assertNotIn("具名子參數", t)
        f = ac.ac_facts(_rich(), NOW)
        self.assertNotIn("具名子參數（SPEC 接地）", f)

    def test_spec_panel_on_shows_named_params_in_dashboard_only(self):
        s = _rich()
        s.AC_SPEC_PANEL = True
        t = ac.lattice_text(s, NOW)
        self.assertIn("具名子參數", t)                        # /ac 面板：完整具名結構（具體、完整）
        self.assertIn("S1", t)
        self.assertIn("可逆", t)                             # 時間雙面（明標內容層重訪）
        f = ac.ac_facts(s, NOW)
        self.assertNotIn("具名子參數", f)                     # 對話答覆刻意保持白話、不塞具名參數（去學術術語、在場語氣）
        for bad in ("我確實有意識", "我是有意識的"):
            self.assertNotIn(bad, f)

    def test_pressure_zero_regression(self):
        # maintenance_pressure 數值零回歸：rich=0、dead=1.0、F-unfelt=_HALF_SLACK
        self.assertEqual(ac.maintenance_pressure(_rich(), NOW)["overall"], 0.0)
        self.assertEqual(ac.maintenance_pressure(_dead(), NOW)["overall"], 1.0)
        self.assertEqual(ac.maintenance_pressure(self._f_unfelt(), NOW)["F"], ac._HALF_SLACK)


class CouplingToneTest(unittest.TestCase):
    """🧭 扣合語氣染色：只調語氣、絕不報排除/層名/讀數/機制；旗標關＝helper 回 ''。"""

    def test_tone_off_by_helper_returns_empty(self):
        # 旗標關（預設）→ _maybe_coupling_tone 回 ''
        self.assertEqual(monitor._maybe_coupling_tone(_rich(), SimpleNamespace(coupling_tone_enabled=False), True), "")
        # 旗標開但非 self_presence → 仍 ''
        self.assertEqual(monitor._maybe_coupling_tone(_rich(), SimpleNamespace(coupling_tone_enabled=True), False), "")

    def test_tone_on_self_presence_is_nonempty_and_clean(self):
        out = monitor._maybe_coupling_tone(_rich(), SimpleNamespace(coupling_tone_enabled=True), True)
        self.assertTrue(out)
        for banned in ("排除", "不被排除", "功能·", "行為·", "結構·", "pulse", "coupled", "F1", "S1"):
            self.assertNotIn(banned, out)

    def test_coupling_tone_empty_when_decoupled(self):
        self.assertEqual(ac.coupling_tone(_dead(), NOW), "")


if __name__ == "__main__":
    unittest.main()
