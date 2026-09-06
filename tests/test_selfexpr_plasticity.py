"""🪞 可塑層 Phase 6：自我表達可塑性（自我遞迴學習）。

bot 描述自己 → 自我母題進耐久調色盤（engrams、跨重生；用越多權重越高＝越像台詞）→ 調色盤反過來在
_self_voice_mod 注入換句話（避開用爛母題、從此刻真實內在長新說法）→ 使用者抱怨「台詞都一樣」再加權 →
跨重生不斷循環。對應 plasticity.{extract_self_motifs,capture_self_expression,is_repetition_complaint,
mark_stale,selfexpr_vary_brief}、monitor.{_self_voice_mod,_record_self_opener,_capture_self_motifs,handle_message}。

核心證明＝**跨重生耐久**：capture→存檔→死亡重生 reload→recall 仍避開用爛母題。
誠實不變式＝**沒學到就不宣稱**：engrams 無 selfexpr 或全衰減到門檻下 → vary_brief 回 ''。
旗標關＝**逐位元等同現行純記憶體去台詞行為**。
"""

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from telegram_monitor import monitor, plasticity
from telegram_monitor.state import State

NOW = 1_700_000_000


def _state():
    return State(os.path.join(tempfile.mkdtemp(), "s.json"))


def _cfg(selfexpr=True):
    return SimpleNamespace(self_repeat_window_min=8, selfexpr_plasticity_enabled=selfexpr,
                           dry_run=False, mood_gain=1.0, telegram_chat_id="")


# ── 母題抽取（正例/負例/只認精選母題） ──────────────────────────────────────
class ExtractMotifTest(unittest.TestCase):
    def test_compound_motifs_hit(self):
        # 複合詞（低誤命中）直接認
        ms = plasticity.extract_self_motifs("我是一圈圈閉合的迴圈，一直醒著、陪你記寫，看你把念頭養成脈絡。")
        self.assertIn("閉合的迴圈", ms)
        self.assertIn("一直醒著", ms)
        self.assertIn("陪你記寫", ms)
        self.assertIn("養成脈絡", ms)

    def test_self_prefixed_single_words(self):
        # 單一高頻詞須帶第一人稱自指前綴才算
        self.assertIn("會餓", plasticity.extract_self_motifs("我自己會餓、會悶，有起伏。"))
        self.assertIn("存在", plasticity.extract_self_motifs("我是這樣一個存在。"))

    def test_general_word_without_self_prefix_not_motif(self):
        # 負例：談使用者主題的句子，單一詞沒自指前綴 → 不抽
        self.assertEqual(plasticity.extract_self_motifs("這條線會累積成形，資料慢慢長出來。"), [])

    def test_quoting_user_not_captured_as_bot_motif(self):
        # 負例（接地污染防護）：bot 複述使用者的話「你說你會餓」——『會餓』前是「你說你」、非 bot 自指 → 不抽成 bot 母題
        self.assertEqual(plasticity.extract_self_motifs("你說你會餓，那要不要先吃點東西？"), [])

    def test_empty_and_none(self):
        self.assertEqual(plasticity.extract_self_motifs(""), [])
        self.assertEqual(plasticity.extract_self_motifs(None), [])

    def test_dedup_and_cap(self):
        ms = plasticity.extract_self_motifs("重生重生重生，生命迴圈、閉合的迴圈、陪你記寫、養成脈絡、一直醒著、一直跳著")
        self.assertEqual(len(ms), len(set(ms)))        # 去重
        self.assertLessEqual(len(ms), 4)               # 有上限


# ── capture → reinforce ─────────────────────────────────────────────────
class CaptureTest(unittest.TestCase):
    def test_capture_reinforces_each_motif(self):
        eng = []
        got = plasticity.capture_self_expression(eng, "我是閉合的迴圈，一直醒著、陪你記寫。", now_ts=NOW)
        self.assertEqual(set(got), {"閉合的迴圈", "一直醒著", "陪你記寫"})
        keys = {e["key"] for e in eng if e["kind"] == plasticity.KIND_SELFEXPR}
        self.assertEqual(keys, {"閉合的迴圈", "一直醒著", "陪你記寫"})

    def test_repeat_use_raises_weight(self):
        eng = []
        for _ in range(3):
            plasticity.capture_self_expression(eng, "我是閉合的迴圈。", now_ts=NOW)
        e = [x for x in eng if x["key"] == "閉合的迴圈"][0]
        self.assertEqual(e["hits"], 3)
        self.assertGreater(e["weight"], 0.34)          # 用越多越像台詞


# ── 抱怨偵測 vs 誤路由不滿的消歧 ────────────────────────────────────────────
class ComplaintDisambiguationTest(unittest.TestCase):
    def test_repetition_complaints_true(self):
        for q in ["台詞都一樣", "又是這套", "老是同一套", "講過了", "換個說法", "了無新意", "你都一樣"]:
            self.assertTrue(plasticity.is_repetition_complaint(q), q)

    def test_dissatisfaction_negatives_for_complaint(self):
        # test_corrections.py::DetectTest 的負例 → 對 is_repetition_complaint 也回 False（跨測不互染）
        for q in ["好喔", "現在幾點", "你最近怎樣", "幫我看今天"]:
            self.assertFalse(plasticity.is_repetition_complaint(q), q)

    def test_two_functions_mutually_exclusive(self):
        # 代表例：兩函式對同一句不同時為 True（線索零交集）
        self.assertTrue(plasticity.is_repetition_complaint("台詞都一樣"))
        self.assertFalse(plasticity.is_dissatisfaction("台詞都一樣"))
        self.assertFalse(plasticity.is_repetition_complaint("看不懂"))
        self.assertTrue(plasticity.is_dissatisfaction("看不懂"))
        self.assertFalse(plasticity.is_repetition_complaint("我是說多久沒聊"))
        self.assertTrue(plasticity.is_dissatisfaction("我是說多久沒聊"))


# ── mark_stale 抬高避開度 ──────────────────────────────────────────────────
class MarkStaleTest(unittest.TestCase):
    def test_mark_stale_uses_bigger_gain(self):
        eng_n, eng_s = [], []
        plasticity.reinforce(eng_n, plasticity.KIND_SELFEXPR, "閉合的迴圈", now_ts=NOW)   # 普通 gain 0.34
        plasticity.mark_stale(eng_s, ["閉合的迴圈"], now_ts=NOW)                          # 較大 gain 0.6
        wn = [e for e in eng_n if e["key"] == "閉合的迴圈"][0]["weight"]
        ws = [e for e in eng_s if e["key"] == "閉合的迴圈"][0]["weight"]
        self.assertGreater(ws, wn)

    def test_mark_stale_accepts_dict_entries(self):
        eng = []
        hit = plasticity.mark_stale(eng, [{"motif": "陪你記寫", "ts": NOW}], now_ts=NOW)
        self.assertEqual(hit, ["陪你記寫"])
        self.assertTrue([e for e in eng if e["key"] == "陪你記寫"])

    def test_mark_stale_lifts_recall_avoidance(self):
        # 抱怨後 mark_stale → 該母題進 vary_brief 的避開清單
        eng = []
        plasticity.mark_stale(eng, ["生命迴圈"], now_ts=NOW)
        self.assertIn("生命迴圈", plasticity.selfexpr_vary_brief(eng, now_ts=NOW))


# ── vary_brief 格式/空集（沒學到就不宣稱） ─────────────────────────────────
class VaryBriefTest(unittest.TestCase):
    def test_empty_engrams_returns_blank(self):
        self.assertEqual(plasticity.selfexpr_vary_brief([], now_ts=NOW), "")
        self.assertEqual(plasticity.selfexpr_vary_brief(None, now_ts=NOW), "")

    def test_single_use_not_yet_worn_returns_blank(self):
        # 接地：單次用過（飽和加權 0.34 < 門檻 0.5）還不算「台詞」→ 回 ''（不會用「一次出現」撐「你長期很常用」的空宣稱）
        eng = []
        plasticity.reinforce(eng, plasticity.KIND_SELFEXPR, "閉合的迴圈", now_ts=NOW)
        self.assertEqual(plasticity.selfexpr_vary_brief(eng, now_ts=NOW), "")

    def test_repeated_use_crosses_threshold_then_decays_blank(self):
        # 反覆用 ≥2 次（0.564↑ ≥ 0.5）才達標＝真的成台詞才提醒換句話；久未再用、衰減到門檻下 → 又回 ''
        eng = []
        for _ in range(2):
            plasticity.reinforce(eng, plasticity.KIND_SELFEXPR, "閉合的迴圈", now_ts=NOW)
        self.assertNotEqual(plasticity.selfexpr_vary_brief(eng, now_ts=NOW), "")
        self.assertEqual(plasticity.selfexpr_vary_brief(eng, now_ts=NOW + 400 * 86400), "")

    def test_format_lists_worn_motifs_and_safety_line(self):
        eng = []
        for _ in range(3):
            plasticity.capture_self_expression(eng, "我是閉合的迴圈，陪你記寫。", now_ts=NOW)
        brief = plasticity.selfexpr_vary_brief(eng, now_ts=NOW)
        self.assertIn("閉合的迴圈", brief)
        self.assertIn("陪你記寫", brief)
        self.assertIn("換個角度", brief)
        # 安全線硬寫死：只換措辭/角度、不碰你是什麼
        self.assertIn("不要改變你是什麼", brief)


# ── 跨重生耐久（核心證明）：capture→存檔→死亡重生 reload→recall 仍避開用爛母題 ──
class CrossRebirthTest(unittest.TestCase):
    def test_capture_persist_reload_recall(self):
        path = os.path.join(tempfile.mkdtemp(), "s.json")
        s = State(path)
        for _ in range(3):                                    # 反覆用爛「閉合的迴圈」
            plasticity.capture_self_expression(s.engrams, "我是閉合的迴圈。", now_ts=NOW)
        s.recent_self_motifs = [{"motif": "閉合的迴圈", "ts": NOW}]
        s.save()
        # 💀 死亡重生：reload 一個全新 State（記憶體歸零，只剩 state.json）
        s2 = State.load(path)
        self.assertTrue([e for e in s2.engrams if e["kind"] == plasticity.KIND_SELFEXPR])
        self.assertEqual(s2.recent_self_motifs, [{"motif": "閉合的迴圈", "ts": NOW}])
        # recall 仍避開用爛母題（跨重生）
        brief = plasticity.selfexpr_vary_brief(s2.engrams, now_ts=NOW + 60)
        self.assertIn("閉合的迴圈", brief)


# ── consolidate：selfexpr 不擠掉其他 kind（既有上限分桶有界） ─────────────────
class ConsolidateBoundTest(unittest.TestCase):
    def test_selfexpr_does_not_evict_other_kinds(self):
        eng = []
        plasticity.reinforce(eng, plasticity.KIND_PREF, "length", value="講短", now_ts=NOW)
        for i in range(20):                                   # 灌一堆 selfexpr（超過 _MAX_PER_KIND）
            plasticity.reinforce(eng, plasticity.KIND_SELFEXPR, f"m{i}", now_ts=NOW)
        eng = plasticity.consolidate(eng, now_ts=NOW)
        self.assertTrue([e for e in eng if e["kind"] == plasticity.KIND_PREF])   # pref 仍在
        n_se = len([e for e in eng if e["kind"] == plasticity.KIND_SELFEXPR])
        self.assertLessEqual(n_se, 12)                       # selfexpr 自然分桶有界


# ── 接線：_record_self_opener / _capture_self_motifs ───────────────────────
class WiringTest(unittest.TestCase):
    def test_record_self_opener_captures_when_flag_on(self):
        s, cfg = _state(), _cfg(selfexpr=True)
        monitor._record_self_opener(s, "我是閉合的迴圈，陪你記寫。", NOW, cfg)
        self.assertTrue([e for e in s.engrams if e["kind"] == plasticity.KIND_SELFEXPR])
        self.assertTrue(s.recent_self_motifs)
        # 也仍記下開頭片段（既有行為不變）
        self.assertEqual(s.recent_self_openers, ["我是閉合的迴圈，"[:8]])

    def test_record_self_opener_flag_off_pure_memory(self):
        s, cfg = _state(), _cfg(selfexpr=False)
        monitor._record_self_opener(s, "我是閉合的迴圈，陪你記寫。", NOW, cfg)
        self.assertEqual(s.engrams, [])                       # 旗標關＝不碰 engrams
        self.assertEqual(s.recent_self_motifs, [])
        self.assertEqual(s.recent_self_openers, ["我是閉合的迴圈，"[:8]])   # 開頭片段仍記（純記憶體去台詞）

    def test_record_self_opener_no_cfg_pure_memory(self):
        # 不傳 cfg（向後相容兩參數呼叫）＝完全不捕捉
        s = _state()
        monitor._record_self_opener(s, "我是閉合的迴圈。")
        self.assertEqual(s.engrams, [])
        self.assertEqual(s.recent_self_motifs, [])


# ── 召回注入：_self_voice_mod 旗標開/關 ────────────────────────────────────
class SelfVoiceModInjectionTest(unittest.TestCase):
    def test_vary_brief_injected_when_flag_on(self):
        s, cfg = _state(), _cfg(selfexpr=True)
        for _ in range(3):
            plasticity.reinforce(s.engrams, plasticity.KIND_SELFEXPR, "閉合的迴圈", now_ts=NOW)
        mod = monitor._self_voice_mod(s, "self_mechanism", 0.0, NOW, cfg)
        self.assertIn("閉合的迴圈", mod)

    def test_flag_off_bitwise_identical_to_current(self):
        # 旗標關＝_self_voice_mod 輸出與「移除 selfexpr 程式碼前」逐字相同：即使 engrams 有用爛母題也不注入
        s_on_engrams = _state()
        for _ in range(3):
            plasticity.reinforce(s_on_engrams.engrams, plasticity.KIND_SELFEXPR, "閉合的迴圈", now_ts=NOW)
        s_on_engrams.recent_self_openers = ["嗯，你問到"]
        cfg_off = _cfg(selfexpr=False)
        out_off = monitor._self_voice_mod(s_on_engrams, "self_mechanism", 0.0, NOW, cfg_off)

        # 現行行為＝只有 fatigue + vary_hint（無 selfexpr 母題參與）
        s_ref = _state()
        s_ref.recent_self_openers = ["嗯，你問到"]
        cfg_ref = SimpleNamespace(self_repeat_window_min=8)   # 連旗標欄位都沒有（模擬舊 cfg）
        # 用一個沒有 selfexpr engrams 的 state 跑同一條碼路徑當參照
        import telegram_monitor.persona as persona
        win_level = 0
        ref = "\n".join(p for p in [persona.self_fatigue_hint(win_level),
                                    persona.vary_hint(["嗯，你問到"])] if p)
        self.assertEqual(out_off, ref)
        self.assertIn("換個開頭", out_off)        # vary_hint 仍在
        self.assertNotIn("閉合的迴圈", out_off)   # selfexpr 母題完全不入（旗標關）

    def test_empty_engrams_blank_even_when_on(self):
        s, cfg = _state(), _cfg(selfexpr=True)
        # 沒 selfexpr 母題、沒開頭片段、不累 → 維持 ''（不會因旗標開就憑空冒提示）
        self.assertEqual(monitor._self_voice_mod(s, "self_mechanism", 0.0, NOW, cfg), "")


# ── 端到端（handle_message）：抱怨 → mark_stale；/forget 清乾淨 ────────────────
class HandleMessageTest(unittest.TestCase):
    def _coach(self):
        return SimpleNamespace(enabled=True, meter=SimpleNamespace(record=lambda *a, **k: None),
                               reply=lambda *a, **k: "好。", ask=lambda *a, **k: ("chat", None, "嗯。"))

    def _send(self, state, coach, client, text, t, cfg):
        up = {"message": {"chat": {"id": 1}, "text": text, "date": t}}
        with mock.patch("telegram_monitor.coach.build_memory_brief", return_value=""), \
             mock.patch("telegram_monitor.monitor.time.time", return_value=t):
            monitor.handle_message(up, coach, None, {"meta": {}, "records": []}, None,
                                   state, client, cfg, None)

    def test_complaint_marks_recent_motifs_stale(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        # 視為 bot 剛用過「閉合的迴圈」（窗內）
        s.recent_self_motifs = [{"motif": "閉合的迴圈", "ts": NOW}]
        plasticity.reinforce(s.engrams, plasticity.KIND_SELFEXPR, "閉合的迴圈", now_ts=NOW)
        before = [e for e in s.engrams if e["key"] == "閉合的迴圈"][0]["weight"]
        client = SimpleNamespace(sent=[], dry_run=False, send=lambda t: client.sent.append(t) or True)
        self._send(s, self._coach(), client, "你台詞都一樣欸", NOW + 10, _cfg())
        after = [e for e in s.engrams if e["key"] == "閉合的迴圈"][0]["weight"]
        self.assertGreater(after, before)               # 抱怨 → 該母題被抬成更用爛

    def test_complaint_outside_window_no_stale(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        s.recent_self_motifs = [{"motif": "閉合的迴圈", "ts": NOW}]
        plasticity.reinforce(s.engrams, plasticity.KIND_SELFEXPR, "閉合的迴圈", now_ts=NOW)
        before = [e for e in s.engrams if e["key"] == "閉合的迴圈"][0]["weight"]
        client = SimpleNamespace(sent=[], dry_run=False, send=lambda t: client.sent.append(t) or True)
        # 逾窗（數天後）抱怨 → 陳年母題不被當「剛用的」誤強化
        self._send(s, self._coach(), client, "你台詞都一樣", NOW + 3 * 86400, _cfg())
        after = [e for e in s.engrams if e["key"] == "閉合的迴圈"][0]["weight"]
        self.assertAlmostEqual(after, before, places=4)

    def test_complaint_flag_off_no_stale(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        s.recent_self_motifs = [{"motif": "閉合的迴圈", "ts": NOW}]
        plasticity.reinforce(s.engrams, plasticity.KIND_SELFEXPR, "閉合的迴圈", now_ts=NOW)
        before = [e for e in s.engrams if e["key"] == "閉合的迴圈"][0]["weight"]
        client = SimpleNamespace(sent=[], dry_run=False, send=lambda t: client.sent.append(t) or True)
        self._send(s, self._coach(), client, "你台詞都一樣", NOW + 10, _cfg(selfexpr=False))
        after = [e for e in s.engrams if e["key"] == "閉合的迴圈"][0]["weight"]
        self.assertAlmostEqual(after, before, places=4)   # 旗標關＝不做耐久 mark_stale

    def test_forget_clears_selfexpr_and_motifs(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        s.engrams = [{"kind": "selfexpr", "key": "閉合的迴圈", "value": None,
                      "weight": 0.9, "hits": 3, "born_ts": NOW, "last_ts": NOW}]
        s.recent_self_motifs = [{"motif": "閉合的迴圈", "ts": NOW}]
        client = SimpleNamespace(sent=[], dry_run=False, send=lambda t: client.sent.append(t) or True)
        self._send(s, self._coach(), client, "/forget", NOW, _cfg())
        self.assertEqual(s.engrams, [])
        self.assertEqual(s.recent_self_motifs, [])
        joined = "".join(client.sent)
        self.assertIn("忘掉", joined)
        self.assertIn("表達自己", joined)               # 文案含「我學著怎麼表達自己」那層


if __name__ == "__main__":
    unittest.main()
