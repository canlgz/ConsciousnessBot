"""🧑‍🏫🌊 §1.27 SKILL_OFFER_HOSTILE_GATE：敵意情境不發教學提議——氣頭上不把批評當教學。

截圖（2026-07-12 21:00–21:03）：使用者連續批評（「別騙人啦」「你明明就不行」「你每次搞砸了」…），
21:03「你每次搞砸了」的「每次」∈ _SKILL_CUES 觸發教學提議鏈 → bot 亂入括號提議
「(要不要我把你在質疑/測試我時的回應方式記成做法？…回我一聲「好」就學起來。)」＝把批評當教學、
答非所問還洩漏括號格式。

修（SKILL_OFFER_HOSTILE_GATE，兩層分離定式：config _bool True／monitor getattr False）：
_maybe_propose_skill 頂部（既有旗標檢查之後）守門，只**消費** §1.14 既有訊號（reaction.py 一行不改、
詞表鐵律不擴）：本句敵意（reaction.is_hostile）或 hostile_streak ≥1 或**近窗敵意**（近 5 則使用者訊息
內有敵意句——必要擴充：偵察實測觸發句「你每次搞砸了」is_hostile=False、前句「你明明就不行」也 False
已把 streak 歸零 ⇒ 只用兩訊號原事故照樣放行；近窗內「別騙人啦」=True 才擋得下）→ 提議直接丟棄
（不 _say、不 _remember、不寫冷卻、不暫存不補發）。平和情境照發。全 stub、零網路。
"""

import unittest
from types import SimpleNamespace
from unittest import mock

from telegram_monitor import monitor

NOW = 1_700_000_000
DET = {"should_propose": True, "topic_tag": "情緒低落", "distilled_prompt": "語氣放軟、先接住情緒"}

# 含「每次」（∈ _SKILL_CUES＝過得了 worth 前置門檻）的**平和**句：本句 is_hostile=False
PEACEFUL_TEACH = "我每次低潮的時候你就這樣回"
# 本句敵意：「騙人」∈ reaction._CHALLENGE → is_hostile=True；含「每次」＝過得了 worth 門檻
HOSTILE_TEACHLIKE = "你每次都騙人"
# 原事故觸發句：is_hostile=False、含「每次」（偵察實測）
INCIDENT_TEXT = "你每次搞砸了"
# 原事故近窗對話史：「別騙人啦」is_hostile=True、「你明明就不行」False（＝streak 已被歸零的情境）
INCIDENT_HISTORY = [
    {"role": "user", "text": "我認為你還有些問題"},
    {"role": "model", "text": "嗯……我聽著。"},
    {"role": "user", "text": "別騙人啦"},
    {"role": "user", "text": "你明明就不行"},
]


class _FakeClient:
    def __init__(self):
        self.sent = []

    def send(self, t):
        self.sent.append(t)
        return True


def _mk_state(**kw):
    base = dict(engrams=[], convo_history=[], skill_pending=None,
                last_skill_propose_ts=0, hostile_streak=0)
    base.update(kw)
    return SimpleNamespace(**base)


def _coach(det=DET):
    c = SimpleNamespace(enabled=True, calls=[])
    c.detect_skill_consensus = lambda text, history: (c.calls.append(text), det)[1]
    return c


def _cfg_on():
    return SimpleNamespace(skill_consensus_enabled=True, skill_offer_hostile_gate_enabled=True)


def _cfg_off():
    # 既有測試假 cfg 形：**無** skill_offer_hostile_gate_enabled 屬性＝getattr 預設 False＝基線
    return SimpleNamespace(skill_consensus_enabled=True)


class _Base(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(monitor, "_say", lambda c, t, **kw: c.send(t))
        p.start()
        self.addCleanup(p.stop)

    def _propose(self, st, text, cfg, coach=None):
        cl, co = _FakeClient(), (coach or _coach())
        self._history_before = list(st.convo_history)
        monitor._maybe_propose_skill(cl, st, cfg, co, text, "fact_or_chat",
                                     st.convo_history, NOW)
        return cl, co

    def _assert_suppressed(self, st, cl, co, msg=""):
        """壓下＝乾淨丟棄：stub 零呼叫、無提議泡泡、無 pending、不寫冷卻、對話史零新增、無新 skill。"""
        self.assertEqual(co.calls, [], msg)                       # 偵測 LLM 零呼叫（省成本、也不提議）
        self.assertEqual(cl.sent, [], msg)                        # 無括號提議送出
        self.assertIsNone(st.skill_pending, msg)
        self.assertEqual(st.last_skill_propose_ts, 0, msg)        # 不寫冷卻（不影響之後平和提議）
        self.assertEqual(st.convo_history, self._history_before, msg)  # 無 _remember（對話史零新增）
        self.assertEqual(st.engrams, [], msg)                     # plasticity 無新 skill


class HostileGateTest(_Base):
    def test_1_streak_blocks_peaceful_teachlike(self):
        """① §1.14 連擊訊號：hostile_streak=1＋含「每次」的平和句 → 提議壓下。"""
        st = _mk_state(hostile_streak=1)
        cl, co = self._propose(st, PEACEFUL_TEACH, _cfg_on())
        self._assert_suppressed(st, cl, co, "streak≥1 應壓下")

    def test_2_hostile_text_blocks(self):
        """② 本句敵意：「你每次都騙人」（騙人 ∈ _CHALLENGE）→ 壓下。"""
        st = _mk_state()
        cl, co = self._propose(st, HOSTILE_TEACHLIKE, _cfg_on())
        self._assert_suppressed(st, cl, co, "本句敵意應壓下")

    def test_3_incident_replay_near_window_blocks(self):
        """③ 截圖重演 D：streak=0（前句「你明明就不行」非敵意已歸零）、近 5 則內有「別騙人啦」、
        本句「你每次搞砸了」is_hostile=False → 近窗條款命中、壓下（只用兩訊號原事故照樣放行）。"""
        st = _mk_state(convo_history=list(INCIDENT_HISTORY))
        cl, co = self._propose(st, INCIDENT_TEXT, _cfg_on())
        self._assert_suppressed(st, cl, co, "近窗敵意應壓下")

    def test_4_peaceful_context_still_proposes(self):
        """④ 平和情境基線保留：streak=0、無敵意史、教學句 → 照發（提議行出現、pending 設好）。"""
        st = _mk_state(convo_history=[{"role": "user", "text": "今天聊得很開心"}])
        cl, co = self._propose(st, PEACEFUL_TEACH, _cfg_on())
        self.assertIsNotNone(st.skill_pending)
        self.assertEqual(st.skill_pending["prompt"], "語氣放軟、先接住情緒")
        self.assertTrue(any("做法" in m for m in cl.sent))         # 提議句真的送出
        self.assertEqual(len(co.calls), 1)

    def test_5_flag_off_locks_current_behavior(self):
        """⑤ 現狀鎖定：旗標關（假 cfg 無此欄）→ ①②③ 全部照發（證明旗標關零變動）。"""
        for text, st in ((PEACEFUL_TEACH, _mk_state(hostile_streak=1)),
                         (HOSTILE_TEACHLIKE, _mk_state()),
                         (INCIDENT_TEXT, _mk_state(convo_history=list(INCIDENT_HISTORY)))):
            cl, co = self._propose(st, text, _cfg_off())
            self.assertIsNotNone(st.skill_pending, text)           # 照發（基線）
            self.assertTrue(any("做法" in m for m in cl.sent), text)

    def test_6_state_without_fields_is_safe(self):
        """容缺：state 無 hostile_streak 欄（讀取端 getattr 預設 0）→ 平和照發；
        state 無 convo_history 欄＝近窗當空清單 → 敵意本句仍壓下、不炸。"""
        st = SimpleNamespace(engrams=[], convo_history=[], skill_pending=None, last_skill_propose_ts=0)
        cl, co = self._propose(st, PEACEFUL_TEACH, _cfg_on())     # 無 hostile_streak 欄
        self.assertIsNotNone(st.skill_pending)
        st2 = SimpleNamespace(engrams=[], skill_pending=None, last_skill_propose_ts=0)  # 無 convo_history 欄
        cl2, co2 = _FakeClient(), _coach()
        monitor._maybe_propose_skill(cl2, st2, _cfg_on(), co2, HOSTILE_TEACHLIKE, "fact_or_chat", [], NOW)
        self.assertIsNone(st2.skill_pending)                      # 本句敵意照樣壓下（近窗容缺不炸）
        self.assertEqual(cl2.sent, [])


if __name__ == "__main__":
    unittest.main()
