"""🎴🧠 §1.34 STICKER_V2/F3 挑選祈使逃生閘（STICKER_PICK_RESCUE）：貼圖情境下「挑一張給我／你挑一張給我吧／
幫我選一個」對 selfstate 八個偵測器全 False，_STICKER_BARE_SEND_RE 動詞白名單只有 傳/送/發/給（無 挑/選），且
§1.15 逃生閘結構閘要求『貼圖字 OR (recent_ctx AND 呢/嗎/? 省略問句)』——挑選祈使兩者皆無 → 連 LLM 逃生閘都碰不到
→ 全掉 fact_or_chat（今日 red）。

解法（禁止手刻大詞表＝第 16 次踩坑）：在 _maybe_llm_sticker_rescue 結構閘新增**第三分支**＝『近期剛在貼圖情境
（_recent_sticker_ctx）AND 窄挑選祈使前置式（_sticker_pick_imperative：短句＋挑/選＋指向本人一張/一個/給我/傳我/送我、
排除明確附件名詞）』只當**便宜前置過濾**放行，真正是非交既有 coach.judge_sticker_request 燒一次 LLM 判、真送圖讀
circumplex（§1.15 鐵律不變）。selfstate 八偵測器不動。

全 stub、絕不打網路（judge_sticker_request 由假 coach 提供可控回值＋呼叫計數）。
"""

import os
import tempfile
import unittest
from types import SimpleNamespace

from telegram_monitor import gemini, monitor, selfstate
from telegram_monitor.state import State

NOW = 1_800_000_000.0

# F3 三句（測繪 A/C 實測 miss）：確定性八偵測器全 False、結構閘前兩分支不中 → 需第三分支才進 LLM
PICK_THREE = ("挑一張給我", "你挑一張給我吧", "幫我選一個")

# 偏誤鎖對照
S_PICK_ATTACH = "挑一張照片給我"   # 明確附件詞 → 前置式不放行、交附件路徑（_sticker_pick_imperative=False）
S_PICK_3RD = "挑一張給我弟"        # 第三人 → 門③ _STICKER_3RD_RE 更早擋、零 LLM
S_OFFTOPIC_REST = "幫我選個餐廳"    # 離題（有 選 但無 一張/一個/給我…）→ 前置式不放行、零 LLM
S_OFFTOPIC_TIME = "幫我挑個時間"    # 同上


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
    c = SimpleNamespace(enabled=True, api_key="k", model="m",
                        meter=SimpleNamespace(record=lambda *a, **k: None),
                        judge_calls=[])

    def _judge(text):
        c.judge_calls.append(text)
        return None if judge is None else judge(text)

    c.judge_sticker_request = _judge
    return c


def _cfg(**over):
    base = dict(dry_run=False, sticker_llm_rescue_enabled=True, sticker_why_ground_enabled=True,
                sticker_pick_rescue_enabled=True, send_stickers=True,
                sticker_send_request_enabled=True, liked_sticker_pick_enabled=True,
                sticker_no_repeat_enabled=True, sticker_rotate_window=3, affect_circumplex_enabled=True,
                sticker_file_ids=None)
    base.update(over)
    return SimpleNamespace(**base)


class PickRescueTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _state(self, near=True):
        s = State(os.path.join(self.tmp, "s.json"))
        s.known_sticker_ids = [{"file_id": "CHICKEN_FID", "file_unique_id": "U1",
                                "emoji": "🐔", "valence": "neutral", "ts": NOW if near else 0, "desc": ""}]
        s.last_sticker_ts = NOW if near else 0
        return s

    # ───────── 前提釘死：F3 三句確定性八偵測器全 miss（否則逃生閘不該搶＝門②會擋） ─────────
    def test_pick_three_all_deterministic_miss(self):
        for t in PICK_THREE:
            self.assertFalse(selfstate.is_sticker_send_request(t), t)
            self.assertFalse(selfstate.is_sticker_followup_request(t), t)
            self.assertFalse(selfstate.is_sticker_remember_request(t), t)
            self.assertFalse(selfstate.is_sticker_preference_request(t), t)
            self.assertFalse(selfstate.is_bare_send_request(t), t)
            self.assertFalse(selfstate.is_attachment_request(t), t)

    # ───────── GREEN：旗標開＋近期貼圖情境 → 三句進第三分支、燒一次 LLM、判是＝真送一張 ─────────
    def test_pick_three_send_via_third_branch(self):
        judge = lambda t: (True, False)
        for t in PICK_THREE:
            st, cl, co = self._state(), FakeClient(), _coach(judge=judge)
            ok = monitor._maybe_llm_sticker_rescue(cl, st, _cfg(), co,
                                                   SimpleNamespace(kind="self_state"), t, NOW)
            self.assertTrue(ok, t)
            self.assertEqual(len(co.judge_calls), 1, t)               # 有燒且只燒一次
            self.assertEqual(cl.stickers, ["CHICKEN_FID"], t)         # 真送出那張真貼圖（file_id 由程式挑、非 LLM 產）

    # ───────── 偏誤鎖①：旗標關（getattr 預設 False）→ 三句回 False、零 LLM＝逐位元同現狀 ─────────
    def test_flag_off_bitwise(self):
        for t in PICK_THREE:
            st, cl, co = self._state(), FakeClient(), _coach(judge=lambda x: (True, True))
            ok = monitor._maybe_llm_sticker_rescue(cl, st, _cfg(sticker_pick_rescue_enabled=False), co,
                                                   SimpleNamespace(kind="self_state"), t, NOW)
            self.assertFalse(ok, t)
            self.assertEqual(co.judge_calls, [], t)
            self.assertEqual(cl.stickers, [], t)

    # ───────── 偏誤鎖②：無近期貼圖情境（last_sticker_ts 老、known 空）→ 不放行、零 LLM ─────────
    def test_no_recent_ctx_no_llm(self):
        for t in PICK_THREE:
            st, cl, co = self._state(near=False), FakeClient(), _coach(judge=lambda x: (True, True))
            ok = monitor._maybe_llm_sticker_rescue(cl, st, _cfg(), co,
                                                   SimpleNamespace(kind="self_state"), t, NOW)
            self.assertFalse(ok, t)
            self.assertEqual(co.judge_calls, [], t)

    # ───────── 偏誤鎖③：離題『幫我選個餐廳/幫我挑個時間』即使 recent_ctx=True → 前置式不放行、零 LLM、不誤送 ─────────
    def test_offtopic_pick_no_llm(self):
        for t in (S_OFFTOPIC_REST, S_OFFTOPIC_TIME):
            st, cl, co = self._state(), FakeClient(), _coach(judge=lambda x: (True, True))
            ok = monitor._maybe_llm_sticker_rescue(cl, st, _cfg(), co,
                                                   SimpleNamespace(kind="self_state"), t, NOW)
            self.assertFalse(ok, t)
            self.assertEqual(co.judge_calls, [], t)
            self.assertEqual(cl.stickers, [], t)

    # ───────── 偏誤鎖④：明確附件詞『挑一張照片給我』→ 前置式不放行（交附件路徑）、零 LLM ─────────
    def test_pick_with_attachment_noun_not_sticker(self):
        st, cl, co = self._state(), FakeClient(), _coach(judge=lambda x: (True, True))
        ok = monitor._maybe_llm_sticker_rescue(cl, st, _cfg(), co,
                                               SimpleNamespace(kind="self_state"), S_PICK_ATTACH, NOW)
        self.assertFalse(ok)
        self.assertEqual(co.judge_calls, [])

    # ───────── 偏誤鎖⑤：第三人『挑一張給我弟』→ 門③ _STICKER_3RD_RE 更早擋、零 LLM、不送 ─────────
    def test_pick_third_person_gated(self):
        st, cl, co = self._state(), FakeClient(), _coach(judge=lambda x: (True, True))
        ok = monitor._maybe_llm_sticker_rescue(cl, st, _cfg(), co,
                                               SimpleNamespace(kind="self_state"), S_PICK_3RD, NOW)
        self.assertFalse(ok)
        self.assertEqual(co.judge_calls, [])
        self.assertEqual(cl.stickers, [])

    # ───────── LLM 判否＝安全退回自然聊天（regex 漏的長尾由語意閘兜） → 回 False、不送 ─────────
    def test_llm_judge_no_safe_false(self):
        st, cl, co = self._state(), FakeClient(), _coach(judge=lambda x: (False, False))
        ok = monitor._maybe_llm_sticker_rescue(cl, st, _cfg(), co,
                                               SimpleNamespace(kind="self_state"), "挑一張給我", NOW)
        self.assertFalse(ok)
        self.assertEqual(len(co.judge_calls), 1)                     # 真的燒了（前置式放行）
        self.assertEqual(cl.stickers, [])                           # 判否＝不送

    # ───────── 失敗安全：judge 拋 GeminiError／回 None → 回 False、不送（不假裝） ─────────
    def test_judge_failure_safe_false(self):
        def _boom(t):
            raise gemini.GeminiError("網路炸了")
        for j in (_boom, lambda t: None):
            st, cl, co = self._state(), FakeClient(), _coach(judge=j)
            ok = monitor._maybe_llm_sticker_rescue(cl, st, _cfg(), co,
                                                   SimpleNamespace(kind="self_state"), "挑一張給我", NOW)
            self.assertFalse(ok)
            self.assertEqual(cl.stickers, [])

    # ───────── switch 協同：SEND_STICKERS=0 → 逃生閘整體靜音、零 LLM、不送 ─────────
    def test_send_stickers_off_silent(self):
        for t in PICK_THREE:
            st, cl, co = self._state(), FakeClient(), _coach(judge=lambda x: (True, True))
            ok = monitor._maybe_llm_sticker_rescue(cl, st, _cfg(send_stickers=False), co,
                                                   SimpleNamespace(kind="self_state"), t, NOW)
            self.assertFalse(ok, t)
            self.assertEqual(co.judge_calls, [], t)
            self.assertEqual(cl.stickers, [], t)


if __name__ == "__main__":
    unittest.main()
