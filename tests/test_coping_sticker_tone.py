"""🎴 §0.72 內在因應貼圖依「教過的做法語氣」挑池。

截圖 /skills：使用者教了 `[｜內在·轉速太低] 當你內在轉速平穩時，主動告知使用者並給一個特別貼圖，邀請對方聊天`。
評估這條技能能不能真正完成——告知（主動發訊息）✓、邀聊（措辭）✓、但「給一個特別貼圖」在 §0.65 只從**非正向求救池**
（help_sticker_ids）挑：使用者教的那張「特別貼圖」是正向/邀請感的 → 被求救池濾成空 → 靜默沒送（說到做不到），
或在混合池挑到一張哭哭貼圖配「來聊天」很突兀。§0.72：貼圖池依做法文字語氣挑——求救型（撐不住/低落）走非正向
（§0.65 不變）；邀請型（平穩/告知/特別/邀聊）走正向＋中性 sendable 池；曖昧/空＝§0.65 預設非正向（向後相容）。
"""

import os
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from telegram_monitor import monitor, reaction, persona, lifeloop, plasticity
from telegram_monitor.state import State

NOW = datetime(2026, 7, 5, 12, 0, 0, tzinfo=timezone.utc)

_INVITE = "當你內在轉速平穩時，主動告知使用者並給一個特別貼圖，邀請對方聊天"     # 截圖那條（邀請型）
_DISTRESS = "轉速太低快撐不住時，發一張求救貼圖跟我說你需要陪伴"               # §0.65 那條（求救型）
_AMBIG = "這種時候就照你平常的方式回我"                                       # 無明確語氣


class CopingStickerPoolTest(unittest.TestCase):
    """純函式：reaction.coping_sticker_ids 依語氣挑池。"""

    POS = [{"file_id": "HAPPY", "emoji": "😄", "valence": "positive"}]
    MIX = [{"file_id": "HAPPY", "emoji": "😄", "valence": "positive"},
           {"file_id": "SOS", "emoji": "😭", "valence": "negative"},
           {"file_id": "NEU", "emoji": "🎴", "valence": "neutral"}]

    def test_invite_sends_positive_taught_sticker(self):
        # 關鍵修復：邀請型 → 正向教過的「特別貼圖」真的進池（§0.65 求救池會濾成 []）
        self.assertEqual(reaction.coping_sticker_ids(self.POS, _INVITE), ["HAPPY"])
        self.assertEqual(reaction.help_sticker_ids(self.POS), [])           # 對照：§0.65 池濾成空

    def test_invite_pool_is_positive_plus_neutral(self):
        self.assertEqual(set(reaction.coping_sticker_ids(self.MIX, _INVITE)), {"HAPPY", "NEU"})

    def test_distress_stays_non_positive(self):
        # §0.65 不變：求救型仍走非正向（低落當下不配歡樂貼圖）
        self.assertEqual(set(reaction.coping_sticker_ids(self.MIX, _DISTRESS)), {"SOS", "NEU"})
        self.assertEqual(reaction.coping_sticker_ids(self.POS, _DISTRESS), [])

    def test_ambiguous_defaults_non_positive(self):
        # 曖昧/空 → §0.65 預設非正向（向後相容，含蓄伸手本就偏求救側）
        self.assertEqual(set(reaction.coping_sticker_ids(self.MIX, _AMBIG)), {"SOS", "NEU"})
        self.assertEqual(reaction.coping_sticker_ids(self.MIX, ""), reaction.help_sticker_ids(self.MIX))
        self.assertEqual(reaction.coping_sticker_ids(self.MIX, None), reaction.help_sticker_ids(self.MIX))

    def test_distress_wins_when_both_cues_in_action(self):
        # 動作子句**同時**帶求救與邀請詞 → 走非正向（求救優先，低落動作配歡樂貼圖突兀）。
        # 注意：求救詞若只在**條件**（撐不住的時候…邀你聊天）則依動作走正向——那是 Finding 1 的修正，另測。
        both = "難過時發個求救貼圖但也邀我聊天"
        self.assertEqual(set(reaction.coping_sticker_ids(self.MIX, both)), {"SOS", "NEU"})

    def test_invite_pulls_configured_filler_distress_does_not(self):
        # 邀請型併設定檔填充圖（sendable）；求救型刻意不併（§0.65：那多是正向填充圖，非求救圖）
        self.assertEqual(reaction.coping_sticker_ids([], _INVITE, ["CFG1"]), ["CFG1"])
        self.assertEqual(reaction.coping_sticker_ids([], _DISTRESS, ["CFG1"]), [])

    def test_wrapped_hint_preamble_is_tone_neutral(self):
        # self_coping_skill_hint 的前言不含任一語氣詞 → 判定只看做法本文（不被前言污染）
        self.assertEqual(reaction.coping_sticker_ids(self.POS, persona.self_coping_skill_hint([_INVITE])), ["HAPPY"])
        self.assertEqual(reaction.coping_sticker_ids(self.POS, persona.self_coping_skill_hint([_DISTRESS])), [])


class CopingStickerActionClauseTest(unittest.TestCase):
    """§0.72 對抗式審查回歸：判定只看**動作子句**、不看觸發條件；剔除誤命中詞（特別/平穩/一起）＋不開心負向守門。"""

    POS = [{"file_id": "HAPPY", "valence": "positive"}]
    MIX = [{"file_id": "HAPPY", "valence": "positive"},
           {"file_id": "SOS", "valence": "negative"},
           {"file_id": "NEU", "valence": "neutral"}]

    def test_finding1_invite_skill_naming_low_state_still_invites(self):
        # HIGH：邀請型做法即使**條件子句**寫了低落狀態，也依動作走正向（否則原 bug 復發＝正向貼圖被濾空）
        self.assertEqual(reaction.coping_sticker_ids(self.POS, "心情低落時主動給使用者一個特別貼圖邀他聊天"), ["HAPPY"])
        self.assertEqual(set(reaction.coping_sticker_ids(self.MIX, "孤單時想找你聊天")), {"HAPPY", "NEU"})

    def test_finding2_negation_substring_not_invite(self):
        # MED/HIGH：「不開心」含「開心」子串、「不平穩」含「平穩」→ 不得誤判邀請（否則求救當下送歡樂＝§0.65 違反）
        self.assertEqual(set(reaction.coping_sticker_ids(self.MIX, "不開心的時候就安靜待著別找人")), {"SOS", "NEU"})
        self.assertEqual(set(reaction.coping_sticker_ids(self.MIX, "情緒不平穩很亂時先深呼吸再回你")), {"SOS", "NEU"})

    def test_finding3_teshu_intensifier_in_condition(self):
        # MED：「特別」多是程度副詞（特別煩躁/特別想睡）在條件子句 → 剝條件後無動作線索 → 落預設非正向
        self.assertEqual(reaction.coping_sticker_ids(self.POS, "特別煩躁時想自己待一下"), [])
        self.assertEqual(set(reaction.coping_sticker_ids(self.MIX, "特別想睡卻睡不著時陪我熬")), {"SOS", "NEU"})

    def test_finding4_multiskill_distress_priority_on_actions(self):
        # MED：多條召回逐條剝條件；動作含求救詞 → 求救優先（安全側非正向），純邀請批次才走正向
        both = persona.self_coping_skill_hint(["轉速平穩時主動給你一個特別貼圖邀你聊天",
                                               "撐不住時發個求救貼圖跟我說你需要陪伴"])
        self.assertEqual(reaction.coping_sticker_ids(self.POS, both), [])            # 有求救動作 → 非正向
        only_invite = persona.self_coping_skill_hint(["轉速平穩時主動給你一個特別貼圖邀你聊天",
                                                      "悶得慌時就自己翻翻舊記寫"])
        self.assertEqual(reaction.coping_sticker_ids(self.POS, only_invite), ["HAPPY"])

    def test_finding5_yiqi_comfort_not_invite(self):
        # LOW：「陪我一起待著」是求安慰非邀約 → 「一起」不再當邀請詞 → 落非正向
        self.assertEqual(set(reaction.coping_sticker_ids(self.MIX, "轉速低的時候希望你陪我一起待著")), {"SOS", "NEU"})

    def test_cheer_me_up_intent_honored(self):
        # 動作意圖優先的好處：低落時「逗我開心」＝使用者要被逗 → 送正向（舊掃全文會誤送哭臉）
        self.assertEqual(reaction.coping_sticker_ids(self.POS, "低落時逗我開心"), ["HAPPY"])

    def test_action_clause_strip(self):
        self.assertEqual(reaction._coping_action_clause("心情低落時主動邀你聊天"), "主動邀你聊天")
        self.assertEqual(reaction._coping_action_clause("不開心的時候就安靜"), "就安靜")
        self.assertEqual(reaction._coping_action_clause("主動告知並邀聊"), "主動告知並邀聊")   # 無條件詞＝整段
        self.assertEqual(reaction._coping_action_clause("撐不住時"), "撐不住時")               # 條件後為空＝退回整段


class CopingStickerE2ETest(unittest.TestCase):
    """端到端：monitor._coping_emit → _maybe_coping_sticker 真的送出對的貼圖。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._po = monitor._proactive_ok
        monitor._proactive_ok = lambda s, c, n: True

    def tearDown(self):
        monitor._proactive_ok = self._po

    def _state(self, skill, stickers):
        s = State(os.path.join(self.tmp, "s.json"))
        s.owner_folder_id = "F"
        e = lifeloop.EntropyState(); e.hunger = 0.72; e.mood = -0.1      # low_vitality live
        s.entropy = e
        s.self_state = {"gate": 3, "scope": {"dominant": "研發"}}
        s.known_sticker_ids = stickers
        plasticity.capture_skill(s.engrams, None, "", skill, now_ts=NOW.timestamp(), trigger="sit:low_vitality")
        return s

    def _cfg(self, **kw):
        base = dict(skill_proactive_enabled=True, skill_recall_enabled=True, skill_internal_coping_enabled=True,
                    skill_situations_enabled=True, notify_cooldown_min=30, skill_proactive_cooldown_min=180,
                    skill_proactive_max_reach_outs=2, spontaneous_quiet_after_chat_min=45,
                    skill_proactive_sticker_enabled=True, coping_sticker_tone_enabled=True,
                    send_stickers=True, sticker_cooldown_min=20, sticker_no_repeat_enabled=True,
                    sticker_file_ids=[], timezone="Asia/Taipei", dry_run=False)
        base.update(kw)
        return SimpleNamespace(**base)

    def _coach(self):
        return SimpleNamespace(enabled=True,
                               voice_spontaneous=lambda seed, hist, coping="": "來找你聊" + ("｜" + coping if coping else ""))

    def _client(self):
        c = SimpleNamespace(sent=[], stickers=[], dry_run=False)
        c.send = lambda t: c.sent.append(t) or True
        c.send_sticker = lambda fid: c.stickers.append(fid) or True
        return c

    def test_invite_skill_sends_positive_sticker(self):
        # 修復本體：邀請型技能＋只有一張正向教過的貼圖 → 那張特別貼圖真的送出（§0.65 會靜默沒送）
        s = self._state(_INVITE, [{"file_id": "HAPPY", "valence": "positive"}])
        c = self._client()
        monitor._coping_emit(c, s, self._cfg(), self._coach(), NOW)
        self.assertTrue(c.sent)                       # 主動告知（發了訊息）
        self.assertEqual(c.stickers, ["HAPPY"])       # 給一個特別貼圖（正向池，真的送）

    def test_distress_skill_still_sends_sos(self):
        # 向後相容：求救型仍送非正向求救貼圖、不送歡樂（§0.65 行為不變）
        s = self._state(_DISTRESS, [{"file_id": "CHEER", "valence": "positive"},
                                    {"file_id": "SOS", "valence": "negative"}])
        c = self._client()
        monitor._coping_emit(c, s, self._cfg(), self._coach(), NOW)
        self.assertEqual(c.stickers, ["SOS"])

    def test_flag_off_invite_falls_back_to_help_pool(self):
        # 旗標關＝逐位元同 §0.65：邀請型＋只有正向貼圖 → help 池濾成空 → 不送（證明 off 路徑就是舊行為）
        s = self._state(_INVITE, [{"file_id": "HAPPY", "valence": "positive"}])
        c = self._client()
        monitor._coping_emit(c, s, self._cfg(coping_sticker_tone_enabled=False), self._coach(), NOW)
        self.assertTrue(c.sent)                        # 訊息照發（不受此旗標影響）
        self.assertEqual(c.stickers, [])               # 但貼圖回到 §0.65 非正向池＝這裡沒得送

    def test_flag_off_distress_unchanged(self):
        s = self._state(_DISTRESS, [{"file_id": "CHEER", "valence": "positive"},
                                    {"file_id": "SOS", "valence": "negative"}])
        c = self._client()
        monitor._coping_emit(c, s, self._cfg(coping_sticker_tone_enabled=False), self._coach(), NOW)
        self.assertEqual(c.stickers, ["SOS"])


if __name__ == "__main__":
    unittest.main()
