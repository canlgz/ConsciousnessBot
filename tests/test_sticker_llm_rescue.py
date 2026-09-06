"""🎴🧠 §1.15 貼圖請求語意逃生閘（STICKER_LLM_RESCUE）：存在句／可能句／省略句
（「有代表現在的你的心情的貼圖嗎，說說為什麼」「貼圖呢」「有貼圖可以代表你剛剛說的心情？」
「有沒有貼圖可以表達你現在的感覺」）一個給予/祈使動詞都沒有 → 確定性偵測
is_sticker_send_request/followup/remember/preference 全 False → _maybe_sticker_send 回 False →
掉進 intent.resolve 被吃成 self_state、貼圖整件事丟掉。

解法＝完全複製 §1.12 承諾逃生閘的形狀：確定性全 miss ＋結構閘在場 → 呼叫**一次** gemini 判
「他是不是要我現在送一張貼圖給他本人／要不要順便說明為什麼」，**LLM 只判是不是＋要不要說明、
絕不產出 file_id/emoji/圖案內容**；真的送出去的一定是 known_sticker_ids 裡的真 file_id，沒貨誠實說沒有。

全 stub、絕不打網路（judge_sticker_request 由假 coach 提供可控回值＋呼叫計數）。
"""

import os
import tempfile
import unittest
from types import SimpleNamespace

from telegram_monitor import gemini, monitor, selfstate
from telegram_monitor.state import State

NOW = 1_800_000_000.0   # 任意固定時鐘（純本地、與真實無關）

# §1.15 四句 TRUE：存在句／省略句／可能句——確定性全 miss，該走送貼圖逃生閘
S_HAVE_WHY = "有代表現在的你的心情的貼圖嗎，說說為什麼"
S_STICKER_Q = "貼圖呢"
S_CAN_REPR = "有貼圖可以代表你剛剛說的心情？"
S_CAN_EXPR = "有沒有貼圖可以表達你現在的感覺"
FOUR_TRUE = (S_HAVE_WHY, S_STICKER_Q, S_CAN_REPR, S_CAN_EXPR)

# 對照：現行 _maybe_sticker_send 已能接（明確送/裸送）——必須仍由它接、零 LLM
S_SEND_DIRECT = "傳一張代表你心情的貼圖給我"
S_GIVE_ONE = "給我一張貼圖"
S_MORE_ONE = "再來一張貼圖"

# 感知/教學/否定/第三人——門③擋、零 LLM（讓 §0.93 感知路接手）
S_PERCEIVE1 = "你看得到貼圖的內容嗎"
S_PERCEIVE2 = "你知道剛剛傳的是什麼貼圖"
S_TEACH = "我教你以後開心就送貼圖"
S_NEG = "不要傳貼圖給我"
S_3RD = "幫我把這張貼圖傳給小明"     # regex 漏（給小明不中 3RD_RE）→ 走 LLM、由語意閘判否
S_PREF = "你喜歡哪一張貼圖"
S_TALK_OFF = "今天天氣真好"            # TALK=False 且無近期貼圖情境


class FakeClient:
    def __init__(self):
        self.sent, self.stickers, self.dry_run = [], [], False

    def send(self, text):
        self.sent.append(text)
        return True

    def send_sticker(self, file_id):
        self.stickers.append(file_id)
        return True


def _coach(judge=None):
    """假教練：judge_sticker_request 記錄呼叫＋回可控 (is_send, wants_why)；零網路。"""
    c = SimpleNamespace(enabled=True, api_key="k", model="m",
                        meter=SimpleNamespace(record=lambda *a, **k: None),
                        judge_calls=[])

    def _judge(text):
        c.judge_calls.append(text)
        if judge is None:
            return None
        return judge(text)

    c.judge_sticker_request = _judge
    return c


def _cfg(**over):
    base = dict(dry_run=False, sticker_llm_rescue_enabled=True, sticker_why_ground_enabled=True,
                sticker_send_request_enabled=True, liked_sticker_pick_enabled=True,
                sticker_no_repeat_enabled=True, sticker_rotate_window=3, affect_circumplex_enabled=True,
                sticker_file_ids=None)
    base.update(over)
    return SimpleNamespace(**base)


class StickerRescueTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _state(self):
        s = State(os.path.join(self.tmp, "s.json"))
        # seed 一張真貼圖（19:29 使用者傳的紅雞）＋近期貼圖情境（_recent_sticker_ctx True）
        s.known_sticker_ids = [{"file_id": "CHICKEN_FID", "file_unique_id": "U1",
                                "emoji": "🐔", "valence": "neutral", "ts": NOW, "desc": ""}]
        s.last_sticker_ts = NOW
        return s

    # ───────── TRUE：四句存在/省略/可能句走送貼圖路徑（燒且只燒一次 LLM、真送一張） ─────────
    def test_true_four_sentences_send_via_rescue(self):
        judge = lambda t: (True, False)
        for txt in FOUR_TRUE:
            st, cl, co = self._state(), FakeClient(), _coach(judge=judge)
            ok = monitor._maybe_llm_sticker_rescue(cl, st, _cfg(), co,
                                                   SimpleNamespace(kind="self_state"), txt, NOW)
            self.assertTrue(ok, txt)
            self.assertEqual(len(co.judge_calls), 1, txt)              # 有燒且只燒一次
            self.assertEqual(cl.stickers, ["CHICKEN_FID"], txt)        # 真的送出那張真貼圖

    # ───────── 不得退化：現行 _maybe_sticker_send 已能接的請求，仍由它接、rescue 零 LLM ─────────
    def test_direct_send_still_handled_by_maybe_sticker_send_zero_llm(self):
        for txt in (S_SEND_DIRECT, S_GIVE_ONE, S_MORE_ONE):
            st, cl = self._state(), FakeClient()
            self.assertTrue(monitor._maybe_sticker_send(cl, st, _cfg(), txt, NOW), txt)  # 現行已 True
            # 若真跑到 rescue：門②（確定性偵測 True）→ return False 且不燒 LLM
            st2, cl2, co = self._state(), FakeClient(), _coach(judge=lambda t: (True, True))
            ok = monitor._maybe_llm_sticker_rescue(cl2, st2, _cfg(), co,
                                                   SimpleNamespace(kind="self_state"), txt, NOW)
            self.assertFalse(ok, txt)
            self.assertEqual(co.judge_calls, [], txt)                  # 省成本

    # ───────── FALSE：感知/教學/否定——門③擋、零 LLM ─────────
    def test_perceive_teach_neg_gated_zero_llm(self):
        for txt in (S_PERCEIVE1, S_PERCEIVE2, S_TEACH, S_NEG):
            st, cl, co = self._state(), FakeClient(), _coach(judge=lambda t: (True, True))
            ok = monitor._maybe_llm_sticker_rescue(cl, st, _cfg(), co,
                                                   SimpleNamespace(kind="self_state"), txt, NOW)
            self.assertFalse(ok, txt)
            self.assertEqual(co.judge_calls, [], txt)
            self.assertEqual(cl.stickers, [], txt)

    # ───────── 第三人：regex 漏（給小明）→ 走 LLM、語意閘判否 → 不送 ─────────
    def test_third_person_reaches_llm_but_judged_no(self):
        st, cl, co = self._state(), FakeClient(), _coach(judge=lambda t: (False, False))
        ok = monitor._maybe_llm_sticker_rescue(cl, st, _cfg(), co,
                                               SimpleNamespace(kind="self_state"), S_3RD, NOW)
        self.assertFalse(ok)
        self.assertEqual(len(co.judge_calls), 1)                      # 真的燒了（regex 漏、由 LLM 兜）
        self.assertEqual(cl.stickers, [])                            # 判否＝不送

    # ───────── 偏好題：由 _maybe_sticker_send §0.94 先接、rescue 門②擋、零 LLM ─────────
    def test_preference_gated_by_maybe_sticker_send(self):
        st, cl = self._state(), FakeClient()
        self.assertTrue(monitor._maybe_sticker_send(cl, st, _cfg(), S_PREF, NOW))
        st2, cl2, co = self._state(), FakeClient(), _coach(judge=lambda t: (True, True))
        ok = monitor._maybe_llm_sticker_rescue(cl2, st2, _cfg(), co,
                                               SimpleNamespace(kind="self_state"), S_PREF, NOW)
        self.assertFalse(ok)
        self.assertEqual(co.judge_calls, [])

    # ───────── 結構閘：非貼圖字＋無近期情境 → 門⑤擋、零 LLM ─────────
    def test_structural_gate_blocks_offtopic(self):
        st, cl, co = self._state(), FakeClient(), _coach(judge=lambda t: (True, True))
        st.last_sticker_ts = 0            # 清掉近期貼圖情境
        st.known_sticker_ids = [dict(st.known_sticker_ids[0], ts=0)]
        ok = monitor._maybe_llm_sticker_rescue(cl, st, _cfg(), co,
                                               SimpleNamespace(kind="self_state"), S_TALK_OFF, NOW)
        self.assertFalse(ok)
        self.assertEqual(co.judge_calls, [])

    # ───────── 失敗安全：judge 拋 GeminiError／回 None → 回 False、不送（不假裝） ─────────
    def test_judge_failure_safe_false(self):
        def _boom(t):
            raise gemini.GeminiError("網路炸了")
        for j in (_boom, lambda t: None):
            st, cl, co = self._state(), FakeClient(), _coach(judge=j)
            ok = monitor._maybe_llm_sticker_rescue(cl, st, _cfg(), co,
                                                   SimpleNamespace(kind="self_state"), S_HAVE_WHY, NOW)
            self.assertFalse(ok)
            self.assertEqual(cl.stickers, [])

    # ───────── LLM 判否＝自然聊天 → 回 False、不送 ─────────
    def test_judge_no_means_natural_chat(self):
        st, cl, co = self._state(), FakeClient(), _coach(judge=lambda t: (False, False))
        ok = monitor._maybe_llm_sticker_rescue(cl, st, _cfg(), co,
                                               SimpleNamespace(kind="self_state"), S_STICKER_Q, NOW)
        self.assertFalse(ok)
        self.assertEqual(len(co.judge_calls), 1)
        self.assertEqual(cl.stickers, [])

    # ───────── 旗標關：四句 TRUE 全回 False 且零 LLM（逐位元同現狀） ─────────
    def test_flag_off_bitwise_head(self):
        for txt in FOUR_TRUE:
            st, cl, co = self._state(), FakeClient(), _coach(judge=lambda t: (True, True))
            ok = monitor._maybe_llm_sticker_rescue(cl, st, _cfg(sticker_llm_rescue_enabled=False), co,
                                                   SimpleNamespace(kind="self_state"), txt, NOW)
            self.assertFalse(ok, txt)
            self.assertEqual(co.judge_calls, [], txt)
            self.assertEqual(cl.stickers, [], txt)


class DeterministicMissMatrixTest(unittest.TestCase):
    """釘住前提：四句 TRUE 的確定性偵測**真的全 miss**（否則逃生閘不該搶＝門②會擋）。"""

    def test_four_sentences_all_deterministic_miss(self):
        for txt in FOUR_TRUE:
            self.assertFalse(selfstate.is_sticker_send_request(txt), txt)
            self.assertFalse(selfstate.is_sticker_followup_request(txt), txt)
            self.assertFalse(selfstate.is_sticker_remember_request(txt), txt)
            self.assertFalse(selfstate.is_sticker_preference_request(txt), txt)


if __name__ == "__main__":
    unittest.main()
