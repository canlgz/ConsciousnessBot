"""🌊 §1.17 self_state 洪水收斂（SELF_STATE_CONVERGE）：使用者只問一件小事，self_state 卻吐一堆泡泡。

截圖根因（2026-07-10 19:27）：user 只問「代表此刻心情的貼圖」，bot 卻吐 8 顆泡泡的內在狀態報告。
verbosity.assess 對 self_state（_ROUTE_BASE=1）＋ deep-cue「說說／為什麼」(+1) ＋ 正向 mood(≥0.5, +1)
＝ level 3 → _BUBBLES[3]=8；self_state topic==None 的 flow 無條件串接多個子句源湊滿 8 顆＝洗版。

設計要點（不碰路由、不碰情緒數值，只用既有 verbosity 輸出 _TURN['bubbles'] 封頂）：
- 在 scale 算完、_TURN['bubbles']=scale.bubbles 已設之後、coherent-reply 封頂（同 min-combine）之前插入 self_state 專屬封頂。
- 只封串數（_say 依 _TURN['bubbles'] 把 8 句合併成 ≤cap 顆），**不動 scale.level**（不改 token 預算/coach 篇幅＝行為改變面積最小、只治洪水）。
- 與 §1.14 敵意收斂天然疊加取更小值：hostile 時 scale.bubbles 已是 2，min(2, cap)=2＝敵意仍更緊。
- 旗標 SELF_STATE_CONVERGE=0＝不封頂＝逐位元同現狀。全 stub、零網路。
"""

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from telegram_monitor import lifeloop, monitor, verbosity
from telegram_monitor.state import State

NOW_TS = 1_700_000_000


def _cfg(**over):
    base = dict(self_state_converge_enabled=True, self_state_bubble_cap=3)
    base.update(over)
    return SimpleNamespace(**base)


# ── 單元：_self_state_bubble_cap 純函式（封頂算式，旗標／route／cap 全覆蓋）─────────
class SelfStateBubbleCapTest(unittest.TestCase):
    def test_caps_self_state_flood_to_cap(self):
        # 截圖情境：scale.bubbles=8（self_state level 3）→ 封到 3
        self.assertEqual(monitor._self_state_bubble_cap(8, "self_state", _cfg()), 3)

    def test_below_cap_untouched(self):
        # 已在上限內＝不動（只封洪水、不放寬也不硬拉高）
        self.assertEqual(monitor._self_state_bubble_cap(2, "self_state", _cfg()), 2)

    def test_only_self_state_route_capped(self):
        # 只封 self_state；其它路由（fact_or_chat）不受影響＝仍 8
        self.assertEqual(monitor._self_state_bubble_cap(8, "fact_or_chat", _cfg()), 8)
        self.assertEqual(monitor._self_state_bubble_cap(8, "self_consciousness", _cfg()), 8)

    def test_none_bubbles_becomes_cap(self):
        # _TURN['bubbles'] 可能是 None（沒算到）→ self_state 時直接設 cap（避免無上限洩洪）
        self.assertEqual(monitor._self_state_bubble_cap(None, "self_state", _cfg()), 3)
        # 非 self_state 的 None 維持 None＝不介入
        self.assertIsNone(monitor._self_state_bubble_cap(None, "fact_or_chat", _cfg()))

    def test_stacks_with_hostile_min_combine(self):
        # §1.14 疊加：hostile 時 scale.bubbles=2 → min(2, 3)=2（敵意更緊、封頂不放寬）
        self.assertEqual(monitor._self_state_bubble_cap(2, "self_state", _cfg()), 2)

    def test_cap_adjustable(self):
        # 旗標可調：cap=2 → 封到 2；cap=5 且 bubbles=8 → 封到 5
        self.assertEqual(monitor._self_state_bubble_cap(8, "self_state", _cfg(self_state_bubble_cap=2)), 2)
        self.assertEqual(monitor._self_state_bubble_cap(8, "self_state", _cfg(self_state_bubble_cap=5)), 5)

    def test_cap_lower_bound_protects_zero_and_negative(self):
        # max(1,·) 下限保護：cap=0 或負值不會封成 0/負（至少留 1 顆）
        self.assertEqual(monitor._self_state_bubble_cap(8, "self_state", _cfg(self_state_bubble_cap=0)), 1)
        self.assertEqual(monitor._self_state_bubble_cap(8, "self_state", _cfg(self_state_bubble_cap=-3)), 1)

    def test_flag_off_bitwise_same(self):
        # SELF_STATE_CONVERGE=0 → 完全不封＝逐位元同現狀（維持 scale.bubbles）
        self.assertEqual(monitor._self_state_bubble_cap(8, "self_state", _cfg(self_state_converge_enabled=False)), 8)
        self.assertIsNone(monitor._self_state_bubble_cap(None, "self_state", _cfg(self_state_converge_enabled=False)))

    def test_default_when_flags_absent(self):
        # cfg 缺欄位時 getattr 預設：converge 預設開、cap 預設 3
        self.assertEqual(monitor._self_state_bubble_cap(8, "self_state", SimpleNamespace()), 3)


# ── 整合：handle_message 走 self_state → _TURN['bubbles'] 被封頂，且不碰路由/情緒 ──
class _FakeClient:
    def __init__(self):
        self.sent, self.stickers, self.dry_run = [], [], False

    def send(self, t):
        self.sent.append(t)
        return True

    def send_sticker(self, fid):
        self.stickers.append(fid)
        return True


class _FakeReader:
    def load_owner_data(self, _fid):
        return {"meta": {"lastIngestTs": 0}, "contexts": [], "journeys": []}

    def load_embedding_records(self, _fid):
        return []                              # 空語料＝判定鏈走 gate 0，仍走完 self_state 路徑（足以驗封頂）


def _coach():
    return SimpleNamespace(
        enabled=True, api_key="k", model="gemini-2.5-flash",
        meter=SimpleNamespace(record=lambda *a, **k: None),
        ask=lambda *a, **k: ("chat", None, "還是很雀躍啊。今天心情不錯。想跟你多聊聊。翻到逛街那條。思緒也淌著。"),
        reply=lambda *a, **k: "還是很雀躍啊。今天心情不錯。想跟你多聊聊。翻到逛街那條。思緒也淌著。",
        judge_sticker_request=lambda *a, **k: None,
        voice_sticker_ack=lambda *a, **k: "嗯，我在這。")


def _full_cfg(**over):
    base = dict(dry_run=False, telegram_chat_id="", mood_gain=1.0, timezone="Asia/Taipei",
                read_sticker_vision=False, anti_repeat_enabled=False, spontaneity_enabled=False,
                thread_sticky_enabled=False, hostile_converge_enabled=True,
                # §1.15/§1.16 貼圖逃生閘關掉＝這段測 §1.17 通則兜底、不被貼圖 rescue 早退攔截
                sticker_llm_rescue_enabled=False, sticker_why_ground_enabled=False,
                self_state_converge_enabled=True, self_state_bubble_cap=3)
    base.update(over)
    return SimpleNamespace(**base)


def _text_msg(text, ts=NOW_TS):
    return {"message": {"chat": {"id": 1}, "text": text, "date": ts}}


class SelfStateIntegrationTest(unittest.TestCase):
    # 「你現在感覺如何，說說為什麼」實測 intent.resolve→self_state、verbosity.assess(mood 0.6)→level3/bubbles8
    SELF_STATE_TEXT = "你現在感覺如何，說說為什麼"

    def setUp(self):
        self.state = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        self.state.owner_folder_id = "F"
        self.state.entropy = lifeloop.EntropyState()
        self.state.entropy.mood = 0.6                    # 正向 mood（≥0.5）＝_mood_adj +1，頂到 level 3
        self.client, self.coach = _FakeClient(), _coach()

    def _turn(self, update, cfg):
        with mock.patch("telegram_monitor.coach.build_memory_brief", return_value=""), \
             mock.patch("telegram_monitor.gemini.generate", return_value="（自陳）此刻還算穩。"), \
             mock.patch.object(monitor, "_say") as say:
            monitor.handle_message(update, self.coach, _FakeReader(),
                                   {"meta": {}, "records": [], "contexts": [], "journeys": []},
                                   object(), self.state, self.client, cfg, None)
        return say

    def test_self_state_flood_capped_to_three(self):
        cfg = _full_cfg()
        self._turn(_text_msg(self.SELF_STATE_TEXT), cfg)
        # §1.17 封頂：min(8, 3)=3
        self.assertEqual(monitor._TURN["bubbles"], 3)
        # 不動 scale.level：coach 收到的仍是 level 3（只封串數、不改 token 預算）
        self.assertEqual(self.coach._turn_length, 3)

    def test_flag_off_keeps_scale_bubbles(self):
        cfg = _full_cfg(self_state_converge_enabled=False)
        self._turn(_text_msg(self.SELF_STATE_TEXT), cfg)
        # 旗標關＝維持 scale.bubbles（8）＝逐位元同現狀
        self.assertEqual(monitor._TURN["bubbles"], 8)

    def test_does_not_touch_route_or_mood(self):
        cfg = _full_cfg()
        before_mood = self.state.entropy.mood
        before_arousal = getattr(self.state.entropy, "arousal", None)
        self._turn(_text_msg(self.SELF_STATE_TEXT), cfg)
        # §1.17 只讀 route.kind、只寫 _TURN['bubbles']；情緒座標數值不因封頂而變
        # （mood 可能因對話一般暖化微動，但封頂本身不改它——關旗標對照值相同才是真證明）
        cfg_off = _full_cfg(self_state_converge_enabled=False)
        st2 = State(os.path.join(tempfile.mkdtemp(), "s2.json"))
        st2.owner_folder_id = "F"
        st2.entropy = lifeloop.EntropyState()
        st2.entropy.mood = before_mood
        c2 = _FakeClient()
        with mock.patch("telegram_monitor.coach.build_memory_brief", return_value=""), \
             mock.patch("telegram_monitor.gemini.generate", return_value="（自陳）此刻還算穩。"), \
             mock.patch.object(monitor, "_say"):
            monitor.handle_message(_text_msg(self.SELF_STATE_TEXT), _coach(), _FakeReader(),
                                   {"meta": {}, "records": [], "contexts": [], "journeys": []},
                                   object(), st2, c2, cfg_off, None)
        # 開／關 §1.17 後情緒座標一致＝封頂沒污染情緒單一真相
        self.assertEqual(self.state.entropy.mood, st2.entropy.mood)
        self.assertEqual(getattr(self.state.entropy, "arousal", None),
                         getattr(st2.entropy, "arousal", None))


if __name__ == "__main__":
    unittest.main()
