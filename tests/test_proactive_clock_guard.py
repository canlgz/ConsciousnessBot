"""🕐 §1.31 PROACTIVE_CLOCK_GUARD — 主動 emit 硬鐘點守門測試（純函式＋換算＋執行期整合）。

🫧 spontaneous 主動出聲的正文是 LLM 自由生成（coach.voice_spontaneous），經 _say(prefix="🫧 ", state=…)
送出，繞過 monitor:925 互動路徑限定的五道誠實守門（含 §0.82 鐘點守門）→ LLM 可自編錯的此刻鐘點
（實測 09:02 台北卻說「下午三點了」）。本任務新增更寬的此刻鐘點宣稱守門，掛在兩個 🫧 emit 點。
全 stub、零網路；紅先綠後。§1.10–§1.30 逐格不動。
"""

import os
import tempfile
import unittest
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from telegram_monitor import monitor, lifeloop, plasticity
from telegram_monitor.state import State

TZ = ZoneInfo("Asia/Taipei")


def _now(h, m=0):
    return datetime(2026, 7, 12, h, m, tzinfo=TZ)


# ── A. 純函式單測：_scrub_proactive_clock ────────────────────────────────
class ScrubProactiveClockTest(unittest.TestCase):
    # 攔（改）：斷言結果不含原鐘點、含真實時段詞
    def test_afternoon_claim_at_morning_is_fixed(self):
        # now=09:02（早上）：「嗨，下午三點了！」→ 變更；含「早上」、不含「下午三點」
        out = monitor._scrub_proactive_clock("嗨，下午三點了！", _now(9, 2), TZ)
        self.assertNotIn("下午三點", out)
        self.assertIn("早上", out)
        self.assertEqual(out, "嗨，這會兒早上！")

    def test_present_prefix_afternoon_at_morning_is_fixed(self):
        # now=早上：「現在下午三點」→ 變更；含「這會兒早上」、不含「下午三點」
        out = monitor._scrub_proactive_clock("現在下午三點", _now(9, 2), TZ)
        self.assertNotIn("下午三點", out)
        self.assertIn("這會兒早上", out)

    def test_morning_claim_at_afternoon_is_fixed(self):
        # now=15:00（午後）：「早上八點了」→ 變更；含「午後」、不含「早上八點」
        out = monitor._scrub_proactive_clock("早上八點了", _now(15), TZ)
        self.assertNotIn("早上八點", out)
        self.assertIn("午後", out)

    def test_noon_claim_at_morning_is_fixed(self):
        # now=09:02：「這會兒中午十二點」→ 變更；含「這會兒早上」、不含「中午十二點」
        out = monitor._scrub_proactive_clock("這會兒中午十二點", _now(9, 2), TZ)
        self.assertNotIn("中午十二點", out)
        self.assertIn("這會兒早上", out)

    def test_boundary_same_daypart_wrong_hour_is_fixed(self):
        # now=15:00「下午四點了」→ 變更（同時段午後但 hour 16≠15＝矛盾）→ 含「午後」不含「四點」
        out = monitor._scrub_proactive_clock("下午四點了", _now(15), TZ)
        self.assertNotIn("四點", out)
        self.assertIn("午後", out)

    # 不碰（原樣 == 輸入，逐位元）
    def test_correct_claim_not_touched(self):
        # now=15:00：「下午三點了」→ 不變（不矛盾：claimed 15 == real 15、同時段午後）
        self.assertEqual(monitor._scrub_proactive_clock("下午三點了", _now(15), TZ), "下午三點了")

    def test_plan_future_not_touched(self):
        # 「下午三點我會來」→ 不變（無 present 標記＋計畫詞 會/來）
        self.assertEqual(monitor._scrub_proactive_clock("下午三點我會來", _now(9, 2), TZ), "下午三點我會來")

    def test_appointment_not_touched(self):
        # 「約在早上八點」→ 不變（無 present 標記＋計畫詞 約）
        self.assertEqual(monitor._scrub_proactive_clock("約在早上八點", _now(15), TZ), "約在早上八點")

    def test_reminder_bare_number_not_touched(self):
        # 「等等三點提醒你」→ 不變（無時段詞不匹配＋計畫詞）
        self.assertEqual(monitor._scrub_proactive_clock("等等三點提醒你", _now(9, 2), TZ), "等等三點提醒你")

    def test_past_narrative_not_touched(self):
        # 「昨天三點寫的」→ 不變（無時段詞＋過去標記）
        self.assertEqual(monitor._scrub_proactive_clock("昨天三點寫的", _now(9, 2), TZ), "昨天三點寫的")

    def test_daypart_only_no_clock_not_touched(self):
        # 「早上好」→ 不變（時段詞無鐘點，§1.30 允許）
        self.assertEqual(monitor._scrub_proactive_clock("早上好", _now(15), TZ), "早上好")

    def test_quoted_attribution_not_touched(self):
        # 「你剛剛說現在下午三點」→ 不變（_NOW_QUOTE_RE 引用歸屬放行）
        self.assertEqual(monitor._scrub_proactive_clock("你剛剛說現在下午三點", _now(9, 2), TZ),
                         "你剛剛說現在下午三點")

    # 🐛 審查修①：截圖正典形——鐘點宣稱後接教練問句（內含跨句過去/計畫詞）仍要攔
    def test_canonical_screenshot_form_is_fixed(self):
        # now=09:02：「嗨，下午三點了！你今天有沒有照著昨天猜的做」——『昨天』在下游**別的子句**，
        # 不該抑制上游『下午三點了』守門（舊 bug：12 字後窗跨句撈到昨天→漏改）。
        out = monitor._scrub_proactive_clock(
            "嗨，下午三點了！你今天有沒有照著昨天猜的做", _now(9, 2), TZ)
        self.assertNotIn("下午三點", out)
        self.assertIn("這會兒早上", out)
        self.assertIn("你今天有沒有照著昨天猜的做", out)   # 問句本體完整保留

    def test_canonical_form_plan_word_downstream_is_fixed(self):
        # 下游問句含計畫詞『要』但在別子句——同樣不該抑制
        out = monitor._scrub_proactive_clock(
            "早安，下午三點了，你等等要不要出去走走", _now(9, 2), TZ)
        self.assertNotIn("下午三點", out)
        self.assertIn("這會兒早上", out)
        self.assertIn("你等等要不要出去走走", out)

    # 過去/計畫詞在**同一子句**（緊貼鐘點、無標點）→ 保守放行（誤傷很糟，寧可漏改）
    def test_same_clause_past_word_still_passes(self):
        # 「昨天下午三點寫的」@09:02：昨天＋寫的同句 → 過去敘述，放行不改
        self.assertEqual(
            monitor._scrub_proactive_clock("昨天下午三點寫的", _now(9, 2), TZ),
            "昨天下午三點寫的")

    # 🐛 審查修②：午夜等價——真實 00:15 說「晚上十二點了」是正確宣稱，不可改
    def test_midnight_evening_twelve_at_0015_not_touched(self):
        self.assertEqual(
            monitor._scrub_proactive_clock("晚上十二點了", _now(0, 15), TZ),
            "晚上十二點了")

    def test_midnight_evening_twelve_hallucination_at_noon_is_fixed(self):
        # 真實 12:00 說「晚上十二點了」＝幻覺（claimed 午夜 0 ≠ 中午 12）→ 攔改
        out = monitor._scrub_proactive_clock("晚上十二點了", _now(12, 0), TZ)
        self.assertNotIn("十二點", out)
        self.assertIn("中午", out)

    def test_no_tz_passthrough(self):
        # tz=None → 原樣（算不出此刻）
        self.assertEqual(monitor._scrub_proactive_clock("下午三點了", _now(9, 2), None), "下午三點了")

    def test_empty_and_no_claim_passthrough(self):
        self.assertEqual(monitor._scrub_proactive_clock("", _now(9, 2), TZ), "")
        self.assertEqual(monitor._scrub_proactive_clock("今天心情不錯", _now(9, 2), TZ), "今天心情不錯")


# ── B. _pcg_claimed_hour 單測 ────────────────────────────────────────────
class PcgClaimedHourTest(unittest.TestCase):
    def test_conversions(self):
        self.assertEqual(monitor._pcg_claimed_hour("下午", "三"), 15)
        self.assertEqual(monitor._pcg_claimed_hour("早上", "八"), 8)
        self.assertEqual(monitor._pcg_claimed_hour("中午", "十二"), 12)
        self.assertEqual(monitor._pcg_claimed_hour("凌晨", "一"), 1)
        self.assertEqual(monitor._pcg_claimed_hour("半夜", "十二"), 0)
        self.assertEqual(monitor._pcg_claimed_hour("晚上", "十一"), 23)
        self.assertEqual(monitor._pcg_claimed_hour("下午", "十二"), 12)
        # 🐛 審查修②：晚上/傍晚十二點＝午夜 0（比照半夜十二），非中午 12
        self.assertEqual(monitor._pcg_claimed_hour("晚上", "十二"), 0)
        self.assertEqual(monitor._pcg_claimed_hour("傍晚", "十二"), 0)
        self.assertEqual(monitor._pcg_claimed_hour("晚上", "九"), 21)   # 晚上九點仍＝21（未動）
        self.assertEqual(monitor._pcg_claimed_hour("傍晚", "六"), 18)   # 傍晚六點仍＝18（未動）
        self.assertIsNone(monitor._pcg_claimed_hour("下午", "亂碼"))


# ── C. 執行期整合：_spontaneous_emit ／ _coping_emit ─────────────────────
_CONFLICT = "嗨，下午三點了，有點想你"     # LLM 自由正文（含衝突此刻宣稱；刻意不帶計畫/過去詞，讓守門真動手）


class _EmitBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._po = monitor._proactive_ok
        monitor._proactive_ok = lambda s, c, n: True

    def tearDown(self):
        monitor._proactive_ok = self._po

    def _client(self):
        c = SimpleNamespace(sent=[], stickers=[], dry_run=True)
        c.send = lambda t: c.sent.append(t) or True
        c.send_sticker = lambda fid: c.stickers.append(fid) or True
        return c


class SpontaneousClockGuardTest(_EmitBase):
    def _state(self):
        s = State(os.path.join(self.tmp, "s.json"))
        s.owner_folder_id = "F"
        e = lifeloop.EntropyState()
        e.hunger = 0.9
        e.reach_outs_this_idle = 0
        e.self_stims_this_idle = 4
        e.prev_ingest = "OLD"
        e.last_revisited_topic = "運動習慣"
        s.entropy = e
        s.self_state = {"gate": 3, "scope": {"dominant": "研發"}}
        s.last_push_ts = 0
        return s

    def _cfg(self, **kw):
        base = dict(spontaneous_cooldown_min=180, notify_cooldown_min=30,
                    spontaneous_quiet_after_chat_min=45, timezone="Asia/Taipei")
        base.update(kw)
        return SimpleNamespace(**base)

    def _coach(self):
        # LLM 自由正文＝含衝突此刻宣稱（不理 seed）
        return SimpleNamespace(enabled=True,
                               voice_spontaneous=lambda seed, hist, coping="", time_rule="": _CONFLICT)

    def test_flag_on_scrubs_conflicting_clock(self):
        s, c = self._state(), self._client()
        monitor._spontaneous_emit(c, s, self._cfg(proactive_clock_guard_enabled=True),
                                  self._coach(), _now(9, 2))
        joined = "".join(c.sent)
        self.assertTrue(c.sent)
        self.assertNotIn("下午三點", joined)
        self.assertIn("早上", joined)

    def test_flag_off_passes_through(self):
        s, c = self._state(), self._client()
        monitor._spontaneous_emit(c, s, self._cfg(), self._coach(), _now(9, 2))  # 旗標不設＝getattr 預設 False
        joined = "".join(c.sent)
        self.assertTrue(c.sent)
        self.assertIn("下午三點了", joined)     # 逐位元＝原 LLM 正文（同現狀）


_INVITE = "當你內在轉速平穩時，主動告知使用者並給一個特別貼圖，邀請對方聊天"


class CopingClockGuardTest(_EmitBase):
    def _state(self):
        s = State(os.path.join(self.tmp, "s.json"))
        s.owner_folder_id = "F"
        e = lifeloop.EntropyState()
        e.hunger = 0.72
        e.mood = -0.1
        s.entropy = e
        s.self_state = {"gate": 3, "scope": {"dominant": "研發"}}
        s.known_sticker_ids = []
        from telegram_monitor.state import State as _S  # noqa
        plasticity.capture_skill(s.engrams, None, "", _INVITE, now_ts=_now(9, 2).timestamp(),
                                 trigger="sit:low_vitality")
        return s

    def _cfg(self, **kw):
        base = dict(skill_proactive_enabled=True, skill_recall_enabled=True,
                    skill_internal_coping_enabled=True, skill_situations_enabled=True,
                    notify_cooldown_min=30, skill_proactive_cooldown_min=180,
                    skill_proactive_max_reach_outs=2, spontaneous_quiet_after_chat_min=45,
                    skill_proactive_sticker_enabled=False, timezone="Asia/Taipei", dry_run=True)
        base.update(kw)
        return SimpleNamespace(**base)

    def _coach(self):
        return SimpleNamespace(enabled=True,
                               voice_spontaneous=lambda seed, hist, coping="": _CONFLICT)

    def test_flag_on_scrubs_conflicting_clock(self):
        s, c = self._state(), self._client()
        monitor._coping_emit(c, s, self._cfg(proactive_clock_guard_enabled=True), self._coach(), _now(9, 2))
        joined = "".join(c.sent)
        self.assertTrue(c.sent)
        self.assertNotIn("下午三點", joined)
        self.assertIn("早上", joined)

    def test_flag_off_passes_through(self):
        s, c = self._state(), self._client()
        monitor._coping_emit(c, s, self._cfg(), self._coach(), _now(9, 2))
        joined = "".join(c.sent)
        self.assertTrue(c.sent)
        self.assertIn("下午三點了", joined)


if __name__ == "__main__":
    unittest.main()
