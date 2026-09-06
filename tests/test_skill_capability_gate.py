"""🚫 §0.73 技能能力閘：教到的做法**動作**是 bot 這管道**做不到的外部能力**（打電話/傳簡訊/寄 email/設鬧鐘/
偵測你上線或已讀/幫你訂餐叫車操作裝置）→ **學習當下就誠實拒絕**（不提議、不捕捉、不注入、不列進 /skills），
而非假裝學會存進帳本＝說到做不到。只揪明顯不可能者，能做到的（傳訊息/貼圖/emoji/到點敲你）不誤收。
旗標關＝逐位元同現狀（照舊會存進帳本）。
"""

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from telegram_monitor import monitor, plasticity, persona, lifeloop
from telegram_monitor.state import State

NOW = 1_700_000_000


class UnsupportedCapabilityDetectorTest(unittest.TestCase):
    def test_impossible_actions_flagged(self):
        for t in ["以後我難過時打電話給我", "每天早上傳簡訊叫我", "我一上線就跟我打招呼",
                  "我已讀不回就再敲我", "幫我設一個鬧鐘", "心情差時寄email鼓勵我",
                  "無聊時幫我叫車", "幫我開燈", "傳我的位置給朋友"]:
            self.assertIsNotNone(plasticity.unsupported_capability(t), t)

    def test_doable_actions_not_flagged(self):
        # 能做到的一律 None（免誤拒）：emoji/語氣/貼圖/打招呼/回訊息/到點敲你（走承諾管道）/主題轉譯…
        for t in ["以後回話帶點emoji、語氣誇張些", "難過時語氣放軟先接住情緒", "轉速太低時主動給貼圖邀我聊天",
                  "到晚上十點提醒我睡覺", "時間到主動敲我", "跟我打招呼", "傳個貼圖逗我",
                  "回訊息簡短一點", "談失眠時用比喻別條列", "重複問你時直接呵斥我停止", "按個愛心給我"]:
            self.assertIsNone(plasticity.unsupported_capability(t), t)

    def test_emoji_is_not_email(self):
        self.assertIsNone(plasticity.unsupported_capability("以後多用emoji"))     # 'email' 不是 'emoji' 子串
        self.assertIsNotNone(plasticity.unsupported_capability("寄email給我"))

    def test_no_substring_false_positives(self):
        # 對抗式審查 HIGH：裸雙字會被無辜複合詞吞掉而誤拒合法語氣做法——能力詞綁動作動詞後全不再誤命中
        for t in ["回話時重視訊息裡的情緒", "重視訊號別忽略他的求救", "在他迷惘時當他的導航給方向",
                  "站在你的位置替你著想", "難過時站在我的位置替我想", "陪我定位我自己的方向",
                  "談未來電影話題時熱情一點", "確認他已讀懂你的意思再繼續解釋", "先確認他有沒有讀懂再往下",
                  "回覆精簡訊息不要長篇大論", "回覆簡短信息", "稍微信任他一點", "他現在不在線上就別煩他"]:
            self.assertIsNone(plasticity.unsupported_capability(t), t)

    def test_near_miss_paraphrases_caught(self):
        # 審查 Finding 3：詞表已涵蓋類別的自然換法也要收（綁動詞版）
        for t in ["用電話叫我", "傳訊到我手機提醒我", "寄信給我打氣", "傳我的位置給朋友", "用微信傳給我"]:
            self.assertIsNotNone(plasticity.unsupported_capability(t), t)

    def test_empty_none(self):
        for t in ("", None, "   "):
            self.assertIsNone(plasticity.unsupported_capability(t))

    def test_label_is_human_readable(self):
        self.assertEqual(plasticity.unsupported_capability("打電話給我"), "打電話或視訊")


class SkillsBriefDropTest(unittest.TestCase):
    def _eng(self):
        eng = []   # 兩條不同觸發＝不同 key（always 與 sit 皆 route-agnostic，同觸發會撞 key 相互覆蓋）
        plasticity.capture_skill(eng, "smalltalk", "", "語氣放軟先接住情緒", trigger="always", now_ts=NOW)
        plasticity.capture_skill(eng, None, "", "難過時打電話給你安慰你", trigger="sit:low_vitality", now_ts=NOW)
        return eng

    def test_default_shows_all(self):
        brief = plasticity.skills_brief(self._eng(), now_ts=NOW)          # 預設 drop_unsupported=False＝逐位元同現狀
        self.assertIn("語氣放軟", brief)
        self.assertIn("打電話", brief)

    def test_drop_unsupported_hides_impossible(self):
        brief = plasticity.skills_brief(self._eng(), now_ts=NOW, drop_unsupported=True)
        self.assertIn("語氣放軟", brief)
        self.assertNotIn("打電話", brief)                                 # 做不到的不列進帳本檢視


class _FakeClient:
    def __init__(self):
        self.sent = []

    def send(self, t):
        self.sent.append(t)
        return True


def _mk_state():
    return SimpleNamespace(engrams=[], convo_history=[], skill_pending=None, last_skill_propose_ts=0)


class ProposeCapabilityGateTest(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(monitor, "_say", lambda c, t, **kw: c.send(t))
        p.start(); self.addCleanup(p.stop)

    def _coach(self, det):
        return SimpleNamespace(enabled=True, detect_skill_consensus=lambda text, history: det)

    def test_impossible_skill_declined_not_proposed(self):
        c, st = _FakeClient(), _mk_state()
        cfg = SimpleNamespace(skill_consensus_enabled=True)
        det = {"should_propose": True, "topic_tag": "-", "distilled_prompt": "我難過時打電話給你安慰你"}
        monitor._maybe_propose_skill(c, st, cfg, self._coach(det), "以後我難過時打電話給我安慰我",
                                     "fact_or_chat", [], NOW)
        self.assertIsNone(st.skill_pending)                              # 沒進入待確認＝不會被學
        self.assertTrue(any("做不到" in m for m in c.sent))               # 誠實拒絕語送出
        self.assertTrue(any("打電話" in m for m in c.sent))               # 講清楚做不到什麼
        self.assertEqual(st.last_skill_propose_ts, NOW)                  # 走冷卻＝別每輪重講

    def test_doable_skill_still_proposed(self):
        c, st = _FakeClient(), _mk_state()
        cfg = SimpleNamespace(skill_consensus_enabled=True)
        det = {"should_propose": True, "topic_tag": "情緒低落", "distilled_prompt": "語氣放軟、先接住情緒"}
        monitor._maybe_propose_skill(c, st, cfg, self._coach(det), "以後我低潮時你這樣回",
                                     "fact_or_chat", [], NOW)
        self.assertIsNotNone(st.skill_pending)                          # 做得到的照常提議
        self.assertFalse(any("做不到" in m for m in c.sent))

    def test_flag_off_still_proposes_impossible(self):
        c, st = _FakeClient(), _mk_state()
        cfg = SimpleNamespace(skill_consensus_enabled=True, skill_capability_gate_enabled=False)
        det = {"should_propose": True, "topic_tag": "-", "distilled_prompt": "我難過時打電話給你"}
        monitor._maybe_propose_skill(c, st, cfg, self._coach(det), "以後我難過時打電話給我",
                                     "fact_or_chat", [], NOW)
        self.assertIsNotNone(st.skill_pending)                          # 旗標關＝逐位元同現狀（照舊提議）
        self.assertFalse(any("做不到" in m for m in c.sent))


class ConfirmCapabilityGateTest(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(monitor, "_say", lambda c, t, **kw: c.send(t))
        p.start(); self.addCleanup(p.stop)

    def _cfg(self, **kw):
        base = dict(dry_run=True)
        base.update(kw)
        return SimpleNamespace(**base)

    def test_confirm_impossible_declined_not_captured(self):
        # 縱深防禦：即使 pending 帶做不到的動作、對方點頭 → 仍拒絕、不捕捉
        c, st = _FakeClient(), _mk_state()
        st.skill_pending = {"route_kind": "smalltalk", "topic_tag": "", "prompt": "難過時打電話給你",
                            "trigger": "always", "ts": NOW}
        handled = monitor._handle_skill_confirm(c, st, self._cfg(), "好，學起來", NOW + 5)
        self.assertTrue(handled)
        self.assertIsNone(st.skill_pending)
        self.assertEqual(st.engrams, [])                                # 沒學進去
        self.assertTrue(any("做不到" in m for m in c.sent))
        self.assertNotIn(persona.SKILL_LEARNED_ACK, c.sent)

    def test_confirm_doable_still_captured(self):
        c, st = _FakeClient(), _mk_state()
        st.skill_pending = {"route_kind": "smalltalk", "topic_tag": "", "prompt": "語氣放軟先接住情緒",
                            "trigger": "always", "ts": NOW}
        handled = monitor._handle_skill_confirm(c, st, self._cfg(), "好", NOW + 5)
        self.assertTrue(handled)
        self.assertTrue(any(e.get("kind") == "skill" for e in st.engrams))   # 做得到的照常學進去
        self.assertIn(persona.SKILL_LEARNED_ACK, c.sent)

    def test_flag_off_confirm_captures_impossible(self):
        c, st = _FakeClient(), _mk_state()
        st.skill_pending = {"route_kind": "smalltalk", "topic_tag": "", "prompt": "難過時打電話給你",
                            "trigger": "always", "ts": NOW}
        handled = monitor._handle_skill_confirm(c, st, self._cfg(skill_capability_gate_enabled=False),
                                                "好", NOW + 5)
        self.assertTrue(handled)
        self.assertTrue(any(e.get("kind") == "skill" for e in st.engrams))   # 旗標關＝照舊存進帳本
        self.assertIn(persona.SKILL_LEARNED_ACK, c.sent)


class RecallInjectionFilterTest(unittest.TestCase):
    """殘留/漏網的做不到做法**不注入回覆 system**（免 LLM 據以在回覆裡謊稱要打電話＝說到做不到）。"""

    def _state_with_coping(self, prompt):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        e = lifeloop.EntropyState(); e.hunger = 0.72; e.mood = -0.1        # low_vitality live
        s.entropy = e
        plasticity.capture_skill(s.engrams, None, "", prompt, trigger="sit:low_vitality", now_ts=NOW)
        return s

    def _cfg(self, **kw):
        base = dict(skill_recall_enabled=True, skill_internal_coping_enabled=True,
                    skill_capability_gate_enabled=True)
        base.update(kw)
        return SimpleNamespace(**base)

    def test_impossible_coping_not_injected(self):
        s = self._state_with_coping("轉速太低時打電話給你陪你")
        self.assertEqual(monitor._self_skill_extra(s, self._cfg(), NOW), "")     # 做不到→不注入

    def test_doable_coping_injected(self):
        s = self._state_with_coping("轉速太低時主動告知並邀你聊天")
        self.assertIn("邀你聊天", monitor._self_skill_extra(s, self._cfg(), NOW))  # 做得到→照常注入

    def test_flag_off_injects_impossible(self):
        s = self._state_with_coping("轉速太低時打電話給你陪你")
        out = monitor._self_skill_extra(s, self._cfg(skill_capability_gate_enabled=False), NOW)
        self.assertIn("打電話", out)                                             # 旗標關＝逐位元同現狀


class AccountabilityOverlapFilterTest(unittest.TestCase):
    """審查 MED：做不到的殘留舊做法別觸發問責框——否則帳本檢視已濾掉它、卻叫 bot「照空清單答」＝反過來否認真教過的事。"""

    def _state(self):
        eng = []   # 模擬 §0.73 之前就學進去、跨重生殘留的做不到做法
        plasticity.capture_skill(eng, "smalltalk", "", "難過時打電話給你安慰你", trigger="always", now_ts=NOW)
        return SimpleNamespace(engrams=eng, scheduled_promises=None, feeling_promise=None)

    def _cfg(self, **kw):
        base = dict(skill_accountability_enabled=True, skill_capability_gate_enabled=True)
        base.update(kw)
        return SimpleNamespace(**base)

    def test_impossible_legacy_skill_does_not_trigger_frame(self):
        q = "你為什麼難過時都沒打電話給我"                       # 指涉那條做不到的舊做法、帶查核語氣
        self.assertTrue(plasticity.skills_overlap(self._state().engrams, q, now_ts=NOW))   # 前提：overlap 真的命中
        self.assertEqual(monitor._skill_accountability_extra(self._state(), self._cfg(), q, NOW), "")  # 但不注入問責框

    def test_flag_off_still_triggers_frame(self):
        q = "你為什麼難過時都沒打電話給我"
        out = monitor._skill_accountability_extra(self._state(), self._cfg(skill_capability_gate_enabled=False), q, NOW)
        self.assertIn("打電話", out)                            # 旗標關＝逐位元同現狀（照舊注入含那條的帳本）


if __name__ == "__main__":
    unittest.main()
