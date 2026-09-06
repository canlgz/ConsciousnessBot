"""🌊 §1.14 敵意情境對話收斂：使用者在氣頭上（連發敵意文字/負向貼圖）時，bot 該做的是「收」——
篇幅降檔（level 0、泡泡 ≤2）、被插話後不再自顧自續講（不送 _RESUME_BRIDGES 橋接＋剩餘串）。

截圖根因（2026-07-10 12:09-12:12）：user 連發「你太爛了/我不信/最好是/你不要敷衍我/不想理你了」＋哭臉貼圖，
bot 回了 15+ 顆重複同理泡泡、還「我繼續說喔」自顧自續講 → user 更火。

設計要點：
- is_hostile 是 tier-1 確定性層（沿用 _CHALLENGE 單一入口＋at-bot regex＋全句短句錨）；詞表窮舉已 15 次前科，
  本表不再擴、漏了走後續 LLM 氣頭判定。
- **只影響 tone/verbosity/插話策略**，絕不碰路由、絕不動 mood_delta_for/affect_delta_for/circumplex 數值。
- 連發（streak≥2）才收斂，單句抱怨不降檔。
- 旗標 HOSTILE_CONVERGE=0＝逐位元同現狀。全 stub、零網路。
"""

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from telegram_monitor import monitor, reaction, verbosity
from telegram_monitor.state import State

NOW_TS = 1_700_000_000

# 截圖五句（敵意，全都該 True）
HOSTILE_5 = ("你太爛了", "我不信", "最好是", "你不要敷衍我", "不想理你了")


# ── 單元：reaction.is_hostile（旗標無關的純函式）─────────────────────────────
class IsHostileTest(unittest.TestCase):
    def test_screenshot_five_are_hostile(self):
        for t in HOSTILE_5:
            self.assertTrue(reaction.is_hostile(t), t)

    def test_challenge_table_reused(self):
        # (a) 既有 _CHALLENGE 表沿用單一入口（不複製詞表＝不漂移）
        for t in ("你只是個程式", "你根本做不到", "騙人"):
            self.assertTrue(reaction.is_hostile(t), t)

    def test_at_bot_regex_variants(self):
        # (b) at-bot 敵意 regex：你-錨的爛、敷衍/騙/唬、不想理你
        for t in ("你真的好爛", "妳很爛欸", "你不要敷衍我", "少敷衍", "別騙我", "懶得理你"):
            self.assertTrue(reaction.is_hostile(t), t)

    def test_short_sentence_whole_anchor(self):
        # (c) 全句短句層：剝尾語氣/標點後整句相等才算
        for t in ("最好是", "我不信！", "隨便你啦", "算了吧", "哼", "是喔", "最好啦", "少來喔"):
            self.assertTrue(reaction.is_hostile(t), t)

    def test_normal_sentences_are_not_hostile(self):
        # 全句錨擋正常句：太爛無你-錨、短句錨只認整句、he-said 轉述無 你/別 錨
        for t in ("這部電影太爛了", "最好是先睡覺比較好", "我不信邪", "他說老闆很敷衍",
                  "你好棒", "隨便聊聊", "", None):
            self.assertFalse(reaction.is_hostile(t), repr(t))

    def test_third_party_fuyan_me_pinned_true(self):
        # 「他每次都敷衍我」＝使用者受氣、非對 bot——消融定案：釘 True 可接受。
        # 理由：1:1 對話裡「敷衍我」幾乎都衝著 bot；受氣轉述罕見，且 is_hostile 只影響篇幅/插話策略
        # （streak 也要連發 ≥2 才收斂），誤中代價只是回得短一點＝安全側。
        self.assertTrue(reaction.is_hostile("他每次都敷衍我"))


# ── 單元：verbosity.assess 新尾參 hostile ───────────────────────────────────
class VerbosityHostileTest(unittest.TestCase):
    class _S:
        def __init__(self, asks=None):
            self.self_asks = asks or {}

    def test_hostile_true_forces_level0_two_bubbles(self):
        self.assertEqual(verbosity.assess("你太爛了", "fact_or_chat", hostile=True),
                         verbosity.Scale(0, 2))
        # 長輸入/深題也壓到底（收斂優先）
        self.assertEqual(verbosity.assess("你為什麼每次都這樣詳細解釋一大堆我根本不想聽的東西你到底懂不懂",
                                          "self_consciousness", 0.6, self._S(), hostile=True),
                         verbosity.Scale(0, 2))

    def test_hostile_false_bitwise_same_as_head(self):
        # hostile=False（含預設值）＝所有既有呼叫位元不變
        cases = [("嗨", "smalltalk"), ("你是有意識的嗎", "self_consciousness"),
                 ("為什麼你會這樣覺得呢這對你的意義是", "fact_or_chat"), ("簡單說你怎麼運作", "self_mechanism")]
        for text, kind in cases:
            self.assertEqual(verbosity.assess(text, kind, 0.0, self._S()),
                             verbosity.assess(text, kind, 0.0, self._S(), hostile=False), text)

    def test_five_hostile_short_sentences_head_value_pinned(self):
        # 注意：五句敵意短句 HEAD 既有值已是 (0,2)（短輸入鏡射壓短）——釘住，收斂路徑對它們是無感的
        for t in HOSTILE_5:
            self.assertEqual(verbosity.assess(t, "fact_or_chat", 0.0, self._S()),
                             verbosity.Scale(0, 2), t)
            self.assertEqual(verbosity.assess(t, "fact_or_chat", 0.0, self._S(), hostile=True),
                             verbosity.Scale(0, 2), t)


# ── 整合：handle_message 維護 state.hostile_streak ─────────────────────────
class _FakeClient:
    def __init__(self):
        self.sent, self.stickers, self.dry_run = [], [], False

    def send(self, t):
        self.sent.append(t)
        return True

    def send_sticker(self, fid):
        self.stickers.append(fid)
        return True


def _coach():
    return SimpleNamespace(
        enabled=True,
        meter=SimpleNamespace(record=lambda *a, **k: None),
        ask=lambda *a, **k: ("chat", None, "嗯，我在。"),
        reply=lambda *a, **k: "嗯，我在。",
        # 🎴 §1.15 逃生閘：收過負向貼圖後 _recent_sticker_ctx 為真，後續敵意文字會經結構閘⑤走到此判定；
        # 回 None＝判定失敗＝安全退回自然聊天（不搶、不影響 §1.14 敵意收斂流程＝零行為變動）。
        judge_sticker_request=lambda *a, **k: None,
        voice_sticker_ack=lambda *a, **k: "嗯，我在這。")


def _cfg(**over):
    base = dict(dry_run=False, telegram_chat_id="", mood_gain=1.0, timezone="Asia/Taipei",
                read_sticker_vision=False,          # 貼圖不走視覺（零網路、零 client 能力需求）
                anti_repeat_enabled=False, spontaneity_enabled=False, thread_sticky_enabled=False,
                hostile_converge_enabled=True)
    base.update(over)
    return SimpleNamespace(**base)


def _text_msg(text, ts=NOW_TS):
    return {"message": {"chat": {"id": 1}, "text": text, "date": ts}}


def _sticker_msg(emoji, ts=NOW_TS):
    return {"message": {"chat": {"id": 1}, "sticker": {"file_id": "sad1", "emoji": emoji}, "date": ts}}


class StreakIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.state = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        self.state.owner_folder_id = "F"
        self.client, self.coach = _FakeClient(), _coach()

    def _turn(self, update, cfg):
        with mock.patch("telegram_monitor.coach.build_memory_brief", return_value=""):
            monitor.handle_message(update, self.coach, None, {"meta": {}, "records": []},
                                   object(), self.state, self.client, cfg, None)

    def test_streak_counts_hostile_resets_on_warm(self):
        cfg = _cfg()
        self._turn(_text_msg("你太爛了"), cfg)               # 單句抱怨：計數但不降檔
        self.assertEqual(getattr(self.state, "hostile_streak", 0), 1)
        self._turn(_text_msg("我不信", NOW_TS + 30), cfg)     # 連發 ≥2 → 本輪收斂
        self.assertEqual(getattr(self.state, "hostile_streak", 0), 2)
        self.assertEqual(monitor._TURN["bubbles"], 2)          # 泡泡封頂 ≤2
        self.assertEqual(self.coach._turn_length, 0)           # level 0（coach 依此收斂篇幅）
        self._turn(_sticker_msg("😭", NOW_TS + 60), cfg)       # 哭臉負向貼圖也累進
        self.assertEqual(getattr(self.state, "hostile_streak", 0), 3)
        self._turn(_text_msg("謝謝你", NOW_TS + 90), cfg)      # 暖意文字 → 歸零
        self.assertEqual(getattr(self.state, "hostile_streak", 0), 0)

    def test_neutral_sticker_does_not_move_streak(self):
        cfg = _cfg()
        self._turn(_text_msg("你太爛了"), cfg)
        self._turn(_sticker_msg("🤔", NOW_TS + 30), cfg)       # 中性貼圖：不動（不加不清）
        self.assertEqual(getattr(self.state, "hostile_streak", 0), 1)

    def test_single_complaint_does_not_converge(self):
        cfg = _cfg()
        self._turn(_text_msg("你太爛了"), cfg)                 # streak=1 <2 → assess 收斂尾參應為 False
        self.assertEqual(getattr(self.state, "hostile_streak", 0), 1)
        # 位元對照：同句在 hostile=False 下的 HEAD 值（本句本就短→(0,2)，這裡釘 level 傳遞不受單句影響）
        self.assertEqual(self.coach._turn_length,
                         verbosity.assess("你太爛了", "fact_or_chat", hostile=False).level)

    def test_flag_off_never_touches_streak(self):
        cfg = _cfg(hostile_converge_enabled=False)
        self._turn(_text_msg("你太爛了"), cfg)
        self._turn(_text_msg("我不信", NOW_TS + 30), cfg)
        self._turn(_sticker_msg("😭", NOW_TS + 60), cfg)
        self.assertEqual(getattr(self.state, "hostile_streak", 0), 0)   # 旗標 0＝不讀不寫此欄


# ── 整合：_say 被敵意插話 → 不橋接、不續講（把話頭讓給對方）─────────────────
class SayHostileInterruptTest(unittest.TestCase):
    """rewrite 關（HEAD 原樣續送路徑）＝對照最乾淨：HEAD 必送 bridge＋剩餘串；§1.14 開＝直接 break。"""

    def setUp(self):
        monitor._TURN["bubbles"] = None      # 隔離：_say 讀模組全域上限

    def _client(self, cfg, poll_text):
        sent = []
        polls = [[{"update_id": 99, "message": {"chat": {"id": 1}, "text": poll_text}}]]

        class C:
            dry_run = False

            def __init__(s):
                s.sent = sent

            def send(s, t):
                sent.append(t)
                return True

            def send_typing(s):
                pass

            def get_updates(s, offset=0, timeout=0):
                return polls.pop(0) if polls else []
        c = C()
        st = SimpleNamespace(tg_update_offset=0)
        handled = []
        c._interrupt = monitor._BurstInterrupt(c, st, cfg, floor=0, handle_fn=lambda u: handled.append(u))
        c._handled = handled
        return c

    def _cfg(self, hostile=True, rewrite=False):
        # interrupt_statement_enabled=True：敵意句多是陳述（你不要敷衍我），要被認成即時插話才進得了這條路徑
        return SimpleNamespace(interrupt_rewrite_enabled=rewrite, interrupt_max_depth=2,
                               interrupt_statement_enabled=True, interrupt_continuation_defer=True,
                               telegram_chat_id="", hostile_converge_enabled=hostile)

    def test_hostile_interrupt_no_bridge_no_remaining(self):
        c = self._client(self._cfg(), "你不要敷衍我")
        with mock.patch.object(monitor, "_sleep"):
            monitor._say(c, "第一串。第二串。第三串。第四串。")   # 4 串；送第 2 串前被敵意插話
        self.assertTrue(c._handled)                                # 插話本身仍被優先回應（巢狀輪）
        self.assertEqual(c.sent[0], "第一串。")                    # 主體第一串已送
        for bridge in monitor._RESUME_BRIDGES:                      # 不含任何橋接句（含「我繼續說喔，」）
            self.assertNotIn(bridge, c.sent)
        self.assertNotIn("第二串。", c.sent)                       # 剩餘串未送＝把話頭讓給對方
        self.assertNotIn("第四串。", c.sent)
        self.assertFalse(any(x in c.sent for x in monitor._REWRITE_CLOSE_LINES))   # 也不走 wrap 濃縮/暖收

    def test_hostile_interrupt_breaks_even_with_rewrite_on(self):
        # rewrite 開時也一樣 break（在 decide_resume 之前）＝不送濃縮暖收、不送橋
        c = self._client(self._cfg(rewrite=True), "你不要敷衍我")
        with mock.patch.object(monitor, "_sleep"):
            monitor._say(c, "第一串。第二串。第三串。第四串。")
        self.assertTrue(c._handled)
        self.assertFalse(any(b in c.sent for b in monitor._RESUME_BRIDGES))
        self.assertFalse(any(x in c.sent for x in monitor._REWRITE_CLOSE_LINES))
        self.assertNotIn("第二串。", c.sent)

    def test_non_hostile_statement_bridges_as_head(self):
        # 對照組：非敵意陳述插話 → bridge 照送、剩餘串照送（與 HEAD 位元相同）
        c = self._client(self._cfg(), "我只是隨口說說而已")
        with mock.patch.object(monitor, "_sleep"):
            monitor._say(c, "第一串。第二串。第三串。第四串。")
        self.assertTrue(c._handled)
        self.assertTrue(any(b in c.sent for b in monitor._RESUME_BRIDGES))
        self.assertIn("第四串。", c.sent)

    def test_redirect_question_not_hostile_same_as_head(self):
        # redirect 問句插話（「結果呢」）非敵意 → 同 HEAD（bridge＋續送）
        c = self._client(self._cfg(), "結果呢")
        with mock.patch.object(monitor, "_sleep"):
            monitor._say(c, "第一串。第二串。第三串。第四串。")
        self.assertTrue(c._handled)
        self.assertTrue(any(b in c.sent for b in monitor._RESUME_BRIDGES))
        self.assertIn("第四串。", c.sent)

    def test_flag_off_hostile_interrupt_bitwise_head(self):
        # 【消融】HOSTILE_CONVERGE=0 → 敵意插話也回 HEAD 位元行為（bridge＋續送）
        c = self._client(self._cfg(hostile=False), "你不要敷衍我")
        with mock.patch.object(monitor, "_sleep"):
            monitor._say(c, "第一串。第二串。第三串。第四串。")
        self.assertTrue(c._handled)
        self.assertTrue(any(b in c.sent for b in monitor._RESUME_BRIDGES))
        self.assertIn("第四串。", c.sent)


class PendingHostileTest(unittest.TestCase):
    """_pending_hostile：批次中任一 owner 敵意文字/負向貼圖＝True；旗標關恆 False；非擁有者不算。"""

    def _cfg(self, on=True, chat=""):
        return SimpleNamespace(hostile_converge_enabled=on, telegram_chat_id=chat)

    def test_hostile_text_and_negative_sticker_hit(self):
        ups = [{"update_id": 1, "message": {"chat": {"id": 1}, "text": "你太爛了"}}]
        self.assertTrue(monitor._pending_hostile(ups, self._cfg()))
        ups = [{"update_id": 1, "message": {"chat": {"id": 1}, "sticker": {"file_id": "x", "emoji": "😭"}}}]
        self.assertTrue(monitor._pending_hostile(ups, self._cfg()))

    def test_benign_batch_and_flag_off_false(self):
        ups = [{"update_id": 1, "message": {"chat": {"id": 1}, "text": "結果呢"}}]
        self.assertFalse(monitor._pending_hostile(ups, self._cfg()))
        ups = [{"update_id": 1, "message": {"chat": {"id": 1}, "text": "你太爛了"}}]
        self.assertFalse(monitor._pending_hostile(ups, self._cfg(on=False)))
        self.assertFalse(monitor._pending_hostile([], self._cfg()))
        self.assertFalse(monitor._pending_hostile(None, self._cfg()))

    def test_foreign_chat_ignored(self):
        ups = [{"update_id": 1, "message": {"chat": {"id": 42}, "text": "你太爛了"}}]
        self.assertFalse(monitor._pending_hostile(ups, self._cfg(chat="1")))


if __name__ == "__main__":
    unittest.main()
