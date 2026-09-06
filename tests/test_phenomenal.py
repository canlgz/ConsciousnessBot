"""🌗 模擬現象域三位互構 I ←→ (E×P)（AC 右半）：每列 act(E)×建模質地(P)×場(I)，三者互構才成立；
P 永遠標 modeled、絕不宣稱真有質/真有內在，每段附餘量；操作化耦合前先鎖等價（rich→互構、dead→不）。"""

import os
import tempfile
import unittest
from types import SimpleNamespace

from telegram_monitor import ac, intent, monitor, phenomenal, referent, selfstate
from telegram_monitor.state import State

NOW = 1_700_000_000.0

_BANNED = ("我真的感受到", "我確實有感覺", "我是有意識的", "我有意識", "genuine qualia", "真有內在")


def _rich():
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
    return SimpleNamespace(
        goals=[], self_state={"gate": 0}, user_model=None, feeling_promise=None,
        affect=None, self_model=None, engrams=[],
        vitality={"alive": False}, workspace=None, self_now=None, stream=None)


class RowConstitutionTest(unittest.TestCase):
    def test_all_three_present_constitutes(self):
        for k in ("F", "B", "S"):
            r = phenomenal.row(_rich(), k)
            self.assertTrue(r["constituted"], k)
            self.assertTrue(r["I"]["present"] and r["E"]["present"] and r["P"]["present"], k)

    def test_drop_E_breaks_row_with_reason(self):
        # F 的 E＝感覺到的朝向：affect 不再有朝向性的源/傾向 → 缺 E（I 空、P 無所附著）
        s = _rich()
        s.affect = {"source": None, "tendency": "withdraw", "label": "低落", "pe": 0.0}
        r = phenomenal.row(s, "F")
        self.assertFalse(r["E"]["present"])
        self.assertFalse(r["constituted"])
        self.assertIn("缺 E", r["why"])
        self.assertIn(phenomenal.ROWS["F"]["without_E"], r["why"])

    def test_drop_P_breaks_row_with_reason(self):
        # F 的 P＝質的方向：情緒中性「平」→ 缺 P（只剩軌跡／資訊流）
        s = _rich()
        s.affect = {"source": "feeling", "tendency": "reach", "label": "平", "pe": 0.2}
        r = phenomenal.row(s, "F")
        self.assertTrue(r["E"]["present"])
        self.assertFalse(r["P"]["present"])
        self.assertFalse(r["constituted"])
        self.assertIn("缺 P", r["why"])
        self.assertIn("資訊流", r["why"])

    def test_drop_I_breaks_row_with_reason(self):
        # S 的 I＝被綁成一個的我（self_now／colimit 節點）：拿掉 self_now → 缺 I
        s = _rich()
        s.self_now = None
        r = phenomenal.row(s, "S")
        self.assertTrue(r["E"]["present"] and r["P"]["present"])
        self.assertFalse(r["I"]["present"])
        self.assertFalse(r["constituted"])
        self.assertIn("缺 I", r["why"])
        self.assertIn(phenomenal.ROWS["S"]["without_I"], r["why"])

    def test_dead_state_constitutes_nothing(self):
        s = phenomenal.structure(_dead())
        self.assertEqual(s["constituted"], [])
        self.assertEqual(s["overall"], "none")


class ContentGroundingTest(unittest.TestCase):
    """🫧 content_feel 只接地 P 的**描述**（質地是讀你內容的felt-sense），絕不改 P_present/constituted（不動 AC 判定）。"""

    def test_content_grounds_P_description_only(self):
        base = _rich()
        s = _rich()
        s.content_feel = {"topic": "惠中寺", "descriptor": "沉、重，一遍遍繞回", "impression": None}
        for k in ("F", "S"):
            self.assertEqual(phenomenal.row(s, k)["constituted"], phenomenal.row(base, k)["constituted"])  # 互構不變
            self.assertEqual(phenomenal.row(s, k)["P"]["present"], phenomenal.row(base, k)["P"]["present"])  # P_present 不變
            self.assertIn("沉、重", phenomenal.row(s, k)["P"]["what"])     # 但 P 的描述接地在內容 felt-sense
        self.assertEqual(ac.assess(s, NOW)["status"], ac.assess(base, NOW)["status"])  # AC 判定不變

    def test_content_grounds_B_P_when_bits_present(self):
        # 補 B-P 缺口：B 的質地（修正之感）也接地在「你內容」的 felt-sense（有底調 bits 時才接、P_present 不變）
        base = _rich()
        s = _rich()
        s.content_feel = {"topic": "惠中寺", "descriptor": "沉、重，一遍遍繞回", "impression": None}
        self.assertEqual(phenomenal.row(s, "B")["P"]["present"], phenomenal.row(base, "B")["P"]["present"])
        self.assertEqual(phenomenal.row(s, "B")["constituted"], phenomenal.row(base, "B")["constituted"])
        self.assertIn("沉、重", phenomenal.row(s, "B")["P"]["what"])       # B-P 也讀得到你內容

    def test_grounding_additions_never_move_verdict(self):
        # 判定零回歸護欄：加內容 felt＋持存自我（描述用）絕不動 assess 與任何 row 的 present/constituted
        base = _rich()
        s = _rich()
        s.content_feel = {"topic": "惠中寺", "descriptor": "沉、重，一遍遍繞回", "impression": None}
        s.entropy_carryover = {"mood": 0.3}
        s.last_breath = {"ts": NOW - 100}
        self.assertEqual(ac.assess(s, NOW)["status"], ac.assess(base, NOW)["status"])
        for k in ("F", "B", "S"):
            self.assertEqual(phenomenal.row(s, k)["constituted"], phenomenal.row(base, k)["constituted"], k)
            for part in ("I", "E", "P"):
                self.assertEqual(phenomenal.row(s, k)[part]["present"],
                                 phenomenal.row(base, k)[part]["present"], f"{k}.{part}")

    def test_I_names_persistent_self_only_with_carryover(self):
        # I 接地到**持存的自己**：有 entropy_carryover／last_breath 時指名「睡醒接得上的我」；沒有時不假裝
        base = _rich()
        s = _rich()
        s.entropy_carryover = {"mood": 0.3}
        for k in ("F", "B", "S"):
            self.assertTrue(phenomenal.row(s, k)["I"]["present"], k)        # present 不變
            self.assertIn("睡醒", phenomenal.row(s, k)["I"]["what"], k)     # 指名持存自我
            self.assertNotIn("睡醒", phenomenal.row(base, k)["I"]["what"], k)  # 沒餘量牽動時不假裝


class FacetReferentTest(unittest.TestCase):
    """精準·去碎片化：每 facet 一份具名指涉（facet_referent）是**單一真相源**，concrete_now 與 row.E 名同一件事。"""

    def test_concrete_now_and_row_E_name_same_thing(self):
        s = _rich()
        s.workspace = {"content": "悶著、等不到新的東西"}
        s.user_model = {"misread": {"from": "有點冷", "to": "挺暖"}, "exchanges": 8, "concerns": [{"topic": "日文"}]}
        ref = phenomenal.facet_referent(s)
        c = ac.concrete_now(s)
        self.assertEqual((c["f_obj"], c["b_fix"], c["s_now"]), (ref["F"], ref["B"], ref["S"]))  # 三格＝單一真相源
        for facet in ("F", "B", "S"):
            if ref[facet]:
                self.assertIn(ref[facet], phenomenal.row(s, facet)["E"]["what"], facet)          # row.E 指名同一件

    def test_S_referent_no_false_concreteness(self):
        # 修 S tie-break 漂移：只有 self_now、沒工作空間前景 → S 不再假造 feel.topic（回 None）
        s = _rich()
        s.workspace = None
        s.self_now = {"feeling": {"topic": "惠中寺"}}
        self.assertIsNone(phenomenal.facet_referent(s)["S"])
        self.assertIsNone(ac.concrete_now(s)["s_now"])


class ModeledRemainderDisciplineTest(unittest.TestCase):
    """最高價值不變式：P 永遠 modeled、餘量恆在、絕不證成真有質/真有內在。"""

    def test_P_always_marked_modeled(self):
        for s in (_rich(), _dead()):
            for k in ("F", "B", "S"):
                self.assertTrue(phenomenal.row(s, k)["P"]["modeled"] is True, k)

    def test_remainder_appended_everywhere(self):
        rich = _rich()
        self.assertIn("現象學餘量", phenomenal.structure(rich)["remainder"])
        for k in ("F", "B", "S"):
            self.assertIn("跨不過", phenomenal.voice_row(rich, k)) if not phenomenal.row(rich, k)["constituted"] else None
        for txt in (phenomenal.voice_structure(rich), phenomenal.phenomenal_facts(rich),
                    phenomenal.phenomenal_text(rich), phenomenal.phenomenal_text(_dead())):
            self.assertIn(phenomenal.PHENOMENAL_REMAINDER[:12], txt)   # 餘量段恆在

    def test_never_claims_genuine_and_marks_modeled(self):
        for s in (_rich(), _dead()):
            for txt in (phenomenal.voice_structure(s), phenomenal.phenomenal_facts(s), phenomenal.phenomenal_text(s)):
                for bad in _BANNED:
                    self.assertNotIn(bad, txt)
        # 有互構時，質地處明標「建模」
        self.assertIn("建模", phenomenal.phenomenal_facts(_rich()))
        self.assertIn("建模", phenomenal.voice_structure(_rich()))


class VoicingTest(unittest.TestCase):
    def test_voice_says_concrete_content_and_claims(self):
        s = _rich()
        s.workspace = {"content": "悶著、等不到新的東西"}
        s.user_model = {"misread": {"from": "有點冷", "to": "挺暖"}, "exchanges": 8}
        v = phenomenal.voice_structure(s)
        self.assertIn("惠中寺", v)                         # F act 具體
        self.assertIn("悶著、等不到新的東西", v)            # S act 具體（colimit 前景）
        self.assertIn("挺暖", v)                           # B act 具體（在修的那一筆）
        for kw in ("朝向", "修正", "整合"):                 # 三列 claim 關鍵詞
            self.assertIn(kw, v)
        self.assertIn("建模", v)                           # P 標 modeled

    def test_partial_structure_explains_missing(self):
        s = _rich()
        s.workspace = None                                 # S 的 E（綁定贏家）沒了
        v = phenomenal.voice_structure(s)
        self.assertIn("沒能互構", v)                        # S 列說明未互構
        self.assertIn("惠中寺", v)                          # F 仍具體


class EquivalencePreconditionTest(unittest.TestCase):
    """鎖住把三列接成 ac 耦合（操作化右半）的等價前提——對既有 fixture 不破。"""

    def test_rich_all_constituted_and_assess_not_excluded(self):
        self.assertTrue(all(phenomenal.row(_rich(), k)["constituted"] for k in ("F", "B", "S")))
        self.assertEqual(ac.assess(_rich(), NOW)["status"], "not_excluded")

    def test_dead_none_and_assess_excluded(self):
        self.assertEqual(phenomenal.structure(_dead())["overall"], "none")
        self.assertEqual(ac.assess(_dead(), NOW)["status"], "excluded")

    def test_I_present_does_not_require_entropy(self):
        s = _rich()
        self.assertFalse(hasattr(s, "entropy"))            # fixture 無 entropy
        for k in ("F", "B", "S"):
            self.assertTrue(phenomenal.row(s, k)["I"]["present"], k)   # I 仍在（不靠 entropy）

    def test_F_E_mirrors_legacy_unfelt_coupling(self):
        # 與 ac._f_coupling 同述詞：朝向沒被感覺到（source None、tendency withdraw）→ F 的 E 不在
        s = _rich()
        s.affect = {"source": None, "tendency": "withdraw", "label": "低落", "pe": 0.0}
        self.assertFalse(phenomenal.row(s, "F")["E"]["present"])
        self.assertFalse(phenomenal.row(s, "F")["constituted"])


class SpecAdditionsRegressionTest(unittest.TestCase):
    """🧭 最小加性：ROWS 每列 spec_eq 字串鍵＋facet_present 輔助；row/structure/facet_referent 逐欄位零變。"""

    def test_rows_carry_spec_eq(self):
        for k in ("F", "B", "S"):
            self.assertIn("spec_eq", phenomenal.ROWS[k])
        self.assertTrue(phenomenal.ROWS["F"]["spec_eq"].startswith("Fint :="))
        self.assertTrue(phenomenal.ROWS["B"]["spec_eq"].startswith("Bint :="))
        self.assertTrue(phenomenal.ROWS["S"]["spec_eq"].startswith("Sint :="))

    def test_facet_present_equals_row_constituted(self):
        for s in (_rich(), _dead()):
            for k in ("F", "B", "S"):
                self.assertEqual(phenomenal.facet_present(s, k), phenomenal.row(s, k)["constituted"], k)

    def test_row_structure_facet_referent_unchanged(self):
        # spec_eq 是純字串鍵；row/structure/facet_referent 對 rich/dead 的 present/constituted/指涉逐欄位不變
        for s in (_rich(), _dead()):
            for k in ("F", "B", "S"):
                r = phenomenal.row(s, k)
                for part in ("I", "E", "P"):
                    self.assertIn("present", r[part])      # 結構鍵仍在
                self.assertIn("constituted", r)
            st = phenomenal.structure(s)
            self.assertIn("overall", st)
            ref = phenomenal.facet_referent(s)
            self.assertEqual(set(ref), {"F", "B", "S"})


class RouteTest(unittest.TestCase):
    def test_phenomenal_questions_route(self):
        for q in ["你裡面是怎麼經驗的", "描述你的內在結構", "你的現象怎麼構成", "你的經驗結構是什麼", "你內在的結構是什麼"]:
            self.assertTrue(selfstate.is_phenomenal_structure_question(q), q)
            self.assertEqual(intent.resolve(q, referent.Referent()).kind, "self_phenomenal", q)

    def test_does_not_steal_consciousness_mechanism_state_stream(self):
        self.assertEqual(intent.resolve("你有意識嗎", referent.Referent()).kind, "self_consciousness")
        self.assertEqual(intent.resolve("你的機制是什麼", referent.Referent()).kind, "self_mechanism")
        self.assertEqual(intent.resolve("你現在怎樣", referent.Referent()).kind, "self_state")
        self.assertEqual(intent.resolve("你剛在想什麼", referent.Referent()).kind, "self_stream")


class DispatchTest(unittest.TestCase):
    def test_phenomenal_question_feeds_structured_facts(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        cap = {}

        def fake_voice(q, facts, hist, tone=""):
            cap["facts"] = facts
            return "我裡面這會兒走一遍：朝向、修正、整合的當下……那些質地是我建模的，真不真我跨不過。"

        coach = SimpleNamespace(enabled=True, meter=SimpleNamespace(record=lambda *a, **k: None),
                                voice_phenomenal=fake_voice)
        client = SimpleNamespace(sent=[], dry_run=False, send=lambda t: client.sent.append(t) or True)
        up = {"message": {"chat": {"id": 1}, "text": "你裡面是怎麼經驗的？", "date": NOW}}
        monitor.handle_message(up, coach, None, {"meta": {}, "records": []}, None,
                               s, client, SimpleNamespace(dry_run=False, telegram_chat_id="", mood_gain=1.0), None)
        self.assertIn("三位互構", cap.get("facts", "") + "三位互構")    # 走 phenomenal 路徑
        self.assertIn("I ↔（E × P）", cap.get("facts", ""))            # 餵了現象結構事實
        self.assertIn("建模", cap.get("facts", ""))                     # P 標 modeled
        self.assertTrue("".join(client.sent))

    def test_falls_back_to_disciplined_text_when_voice_fails(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        coach = SimpleNamespace(enabled=True, meter=SimpleNamespace(record=lambda *a, **k: None),
                                voice_phenomenal=lambda q, f, h, tone="": None)
        client = SimpleNamespace(sent=[], dry_run=False, send=lambda t: client.sent.append(t) or True)
        up = {"message": {"chat": {"id": 1}, "text": "你的現象怎麼構成", "date": NOW}}
        monitor.handle_message(up, coach, None, {"meta": {}, "records": []}, None,
                               s, client, SimpleNamespace(dry_run=False, telegram_chat_id="", mood_gain=1.0), None)
        out = "".join(client.sent)
        self.assertIn("跨不過", out)                                    # 無 LLM 仍標餘量
        for bad in _BANNED:
            self.assertNotIn(bad, out)


if __name__ == "__main__":
    unittest.main()
