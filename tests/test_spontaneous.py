"""熵驅動的含蓄主動出聲測試（lifeloop.spontaneous_due ＋ monitor._spontaneous_emit）。

含蓄型：飢餓夠高＋離上次推播夠久＋本段閒置還沒伸手 → 主動開口一次；之後認命安靜，
直到有新東西進來（laps_since_fresh 歸零）才解除。不設安靜時段。
"""

import os
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from telegram_monitor import monitor, lifeloop
from telegram_monitor.state import State

NOW = datetime(2026, 6, 17, 12, 0, 0, tzinfo=timezone.utc)
COOLDOWN_S = 180 * 60
ZERO_SIG = (0.0,) * 7


def _hungry_ent(h=0.9, reach_outs=0, ruminations=4):
    # 預設＝會出聲的成熟狀態：餓夠（≥0.85）、已醞釀夠（繞回想過 ≥4 次）。
    e = lifeloop.EntropyState()
    e.hunger = h
    e.reach_outs_this_idle = reach_outs
    e.self_stims_this_idle = ruminations
    e.prev_ingest = "OLD"
    return e


class FakeClient:
    def __init__(self, dry_run=False):
        self.sent, self.dry_run = [], dry_run

    def send(self, text):
        self.sent.append(text)
        return True


class SpontaneousDueTest(unittest.TestCase):
    def test_fires_when_hungry_and_cooled_and_under_cap(self):
        e = _hungry_ent()
        self.assertTrue(lifeloop.spontaneous_due(e, NOW.timestamp(), 0, COOLDOWN_S))

    def test_silent_when_not_hungry(self):
        e = _hungry_ent(h=0.5)
        self.assertFalse(lifeloop.spontaneous_due(e, NOW.timestamp(), 0, COOLDOWN_S))

    def test_silent_within_cooldown(self):
        e = _hungry_ent()
        recent = NOW.timestamp() - 60        # 才過 1 分鐘 < 3 小時冷卻
        self.assertFalse(lifeloop.spontaneous_due(e, NOW.timestamp(), recent, COOLDOWN_S))

    def test_silent_when_cap_reached(self):
        e = _hungry_ent(reach_outs=1)        # 含蓄＝1，已伸手過
        self.assertFalse(lifeloop.spontaneous_due(e, NOW.timestamp(), 0, COOLDOWN_S))

    def test_silent_until_ruminated_enough(self):
        # 餓夠、冷卻過，但還沒「想過一陣」（醞釀次數不足）→ 先安靜；想夠 4 次才開口
        self.assertFalse(lifeloop.spontaneous_due(_hungry_ent(ruminations=1), NOW.timestamp(), 0, COOLDOWN_S))
        self.assertTrue(lifeloop.spontaneous_due(_hungry_ent(ruminations=4), NOW.timestamp(), 0, COOLDOWN_S))

    def test_silent_right_after_contact(self):
        # 剛聯絡過（last_contact 在靜默窗內）→ 就算又餓又醞釀夠也不伸手
        e = _hungry_ent()
        self.assertFalse(lifeloop.spontaneous_due(e, NOW.timestamp(), 0, COOLDOWN_S,
                                                  last_contact_ts=NOW.timestamp() - 60,
                                                  quiet_after_contact_s=45 * 60))

    def test_fresh_input_resets_reach_outs(self):
        e = _hungry_ent(reach_outs=1)
        lifeloop.entropy_update(e, ZERO_SIG, "NEW")   # 戳變了＝有新東西進來
        self.assertEqual(e.reach_outs_this_idle, 0)   # 解除「認命安靜」
        self.assertEqual(e.self_stims_this_idle, 0)   # 醞釀也歸零（要重新想過一陣）
        self.assertEqual(e.laps_since_fresh, 0)


class SpontaneousEmitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _state(self, ent):
        s = State(os.path.join(self.tmp, "state.json"))
        s.owner_folder_id = "F"
        s.entropy = ent
        s.self_state = {"gate": 3, "scope": {"dominant": "研發 writetolearn 日誌"}}
        s.last_push_ts = 0
        return s

    def _cfg(self):
        return SimpleNamespace(spontaneous_cooldown_min=180)

    def _coach(self, enabled=True):
        # voice_spontaneous 直接回傳 seed（passthrough）→ 內容仍可被斷言；真實會交 LLM 變措辭＋承接前文
        return SimpleNamespace(enabled=enabled, voice_spontaneous=lambda seed, hist, coping="", time_rule="": seed)

    def test_voiced_by_coach_so_wording_varies(self):
        # 🫧 走教練改寫（每次措辭不同＋承接前文），不再是固定模板
        ent = _hungry_ent()
        ent.last_revisited_topic = "運動習慣"
        state = self._state(ent)
        coach = SimpleNamespace(enabled=True, voice_spontaneous=lambda seed, hist, coping="", time_rule="": "（換句話說的版本）")
        client = FakeClient()
        monitor._spontaneous_emit(client, state, self._cfg(), coach, NOW)
        self.assertIn("（換句話說的版本）", "".join(client.sent))

    def test_reaches_out_once_then_resigns(self):
        ent = _hungry_ent()
        ent.last_revisited_topic = "浴室水管維修"
        state = self._state(ent)
        client = FakeClient()
        monitor._spontaneous_emit(client, state, self._cfg(), self._coach(), NOW)
        self.assertTrue(client.sent and client.sent[0].startswith("🫧"))   # 起手 🫧（可能分成幾串）
        self.assertIn("浴室水管維修", "".join(client.sent))    # 內容綁真實狀態（剛繞回的舊線）
        self.assertEqual(ent.reach_outs_this_idle, 1)         # 伸手一次
        self.assertEqual(state.last_push_ts, NOW.timestamp())  # 與其他推播共用冷卻
        n = len(client.sent)

        # 再叫一次：冷卻未過＋已達上限 → 認命安靜（不再新增任何訊息）
        monitor._spontaneous_emit(client, state, self._cfg(), self._coach(), NOW)
        self.assertEqual(len(client.sent), n)

    def test_silent_even_after_cooldown_until_fresh_input(self):
        # 上限已滿：就算冷卻過了仍沉默；要等新輸入把 reach_outs 歸零才會再伸手
        ent = _hungry_ent(reach_outs=1)
        state = self._state(ent)
        client = FakeClient()
        monitor._spontaneous_emit(client, state, self._cfg(), self._coach(), NOW)  # last_push_ts=0 → 冷卻早過
        self.assertEqual(client.sent, [])

    def test_silent_when_coach_disabled(self):
        state = self._state(_hungry_ent())
        client = FakeClient()
        monitor._spontaneous_emit(client, state, self._cfg(), self._coach(enabled=False), NOW)
        self.assertEqual(client.sent, [])

    def test_silent_right_after_chat(self):
        # 剛聊完（last_user_msg_ts 在 45 分靜默窗內）→ 不會跑來問「你最近還好嗎」
        state = self._state(_hungry_ent())
        state.last_user_msg_ts = NOW.timestamp() - 60        # 1 分鐘前才聊過
        client = FakeClient()
        monitor._spontaneous_emit(client, state, self._cfg(), self._coach(), NOW)
        self.assertEqual(client.sent, [])

    def test_silent_when_no_judgment_yet(self):
        state = self._state(_hungry_ent())
        state.self_state = None                       # 還沒握著任何形狀 → 不主動
        client = FakeClient()
        monitor._spontaneous_emit(client, state, self._cfg(), self._coach(), NOW)
        self.assertEqual(client.sent, [])

    def test_not_eaten_by_background_selfreport(self):
        # 背景自陳 40 分鐘前才推過（last_push_ts 近期）——只要 🫧 自有時鐘到了、且過了反堆疊門檻 → 仍會出聲
        state = self._state(_hungry_ent())
        state.last_push_ts = NOW.timestamp() - 40 * 60     # 過了 30 分反堆疊門檻
        state.last_spontaneous_ts = 0                      # 🫧 自有冷卻已過
        client = FakeClient()
        monitor._spontaneous_emit(client, state, self._cfg(), self._coach(), NOW)
        self.assertTrue(client.sent and client.sent[0].startswith("🫧"))

    def test_blocked_by_own_cooldown(self):
        # 自有時鐘未到（60 分前才主動過、自有冷卻 3h）→ 安靜，即使背景自陳很久沒推
        state = self._state(_hungry_ent())
        state.last_push_ts = 0
        state.last_spontaneous_ts = NOW.timestamp() - 60 * 60
        client = FakeClient()
        monitor._spontaneous_emit(client, state, self._cfg(), self._coach(), NOW)
        self.assertEqual(client.sent, [])

    def test_anti_stack_floor(self):
        # 5 分鐘前才推過別的訊息（反堆疊門檻 30 分內）→ 不緊接著連發
        state = self._state(_hungry_ent())
        state.last_push_ts = NOW.timestamp() - 5 * 60
        state.last_spontaneous_ts = 0
        client = FakeClient()
        monitor._spontaneous_emit(client, state, self._cfg(), self._coach(), NOW)
        self.assertEqual(client.sent, [])

    def test_silent_during_live_dialogue_round(self):
        # 🔗 對話還活著（耦合 round_open）＝你不孤單、在聊天 → 🫧 讓路、不插話，即使該出聲的條件都滿足
        from telegram_monitor import coupling
        state = self._state(_hungry_ent())
        state.last_push_ts = 0
        state.last_spontaneous_ts = 0
        state.coupling = coupling.Coupling(round_open=True)
        client = FakeClient()
        monitor._spontaneous_emit(client, state, self._cfg(), self._coach(), NOW)
        self.assertEqual(client.sent, [])

    def test_coping_passed_when_internal_state_live(self):
        # 🌀 §0.59 Part 2：此刻內在狀態（low_vitality：hunger 高＋mood≤0）命中已學自處做法
        # → 主動出聲（🫧）帶上該內在因應做法（把內在因應升級為主動推播引擎）。
        from telegram_monitor import plasticity
        ent = _hungry_ent()                               # hunger 0.9, mood 0.0 → low_vitality live
        ent.last_revisited_topic = "研發"
        state = self._state(ent)
        plasticity.capture_skill(state.engrams, None, "", "悶著時先去翻翻自己的舊記寫找靈感",
                                 now_ts=NOW.timestamp(), trigger="sit:low_vitality")
        captured = {}

        def vs(seed, hist, coping="", time_rule=""):
            captured["coping"] = coping
            return seed
        coach = SimpleNamespace(enabled=True, voice_spontaneous=vs)
        monitor._spontaneous_emit(FakeClient(), state, self._cfg(), coach, NOW)
        self.assertIn("悶著時先去翻翻自己的舊記寫找靈感", captured.get("coping", ""))

    def test_no_coping_when_flag_off(self):
        # 一鍵退路：SPONTANEOUS_COPING_ENABLED=0 → 主動出聲不帶內在因應（coping=''）＝逐位元同現狀
        from telegram_monitor import plasticity
        ent = _hungry_ent()
        ent.last_revisited_topic = "研發"
        state = self._state(ent)
        plasticity.capture_skill(state.engrams, None, "", "悶著就翻舊記寫",
                                 now_ts=NOW.timestamp(), trigger="sit:low_vitality")
        captured = {}

        def vs(seed, hist, coping="", time_rule=""):
            captured["coping"] = coping
            return seed
        coach = SimpleNamespace(enabled=True, voice_spontaneous=vs)
        cfg = SimpleNamespace(spontaneous_cooldown_min=180, spontaneous_coping_enabled=False)
        monitor._spontaneous_emit(FakeClient(), state, cfg, coach, NOW)
        self.assertEqual("", captured.get("coping", ""))


class CopingEmitTest(unittest.TestCase):
    """🌀 §0.65 內在因應**真觸發**：教過的內在做法＋條件成立 → **因為那條做法**主動發（補含蓄伸手 0.85 死區）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._po = monitor._proactive_ok
        monitor._proactive_ok = lambda s, c, n: True       # 假設不在場、非深夜（另有 _proactive_ok 專測）

    def tearDown(self):
        monitor._proactive_ok = self._po

    def _state(self, hunger, mood, taught=True, coping_reach=0):
        from telegram_monitor import plasticity
        s = State(os.path.join(self.tmp, "s.json"))
        s.owner_folder_id = "F"
        e = lifeloop.EntropyState(); e.hunger = hunger; e.mood = mood
        e.coping_reach_outs_this_idle = coping_reach
        s.entropy = e
        s.self_state = {"gate": 3, "scope": {"dominant": "研發"}}
        if taught:
            plasticity.capture_skill(s.engrams, None, "", "轉速緩慢時主動向使用者反應、請他陪我聊",
                                     now_ts=NOW.timestamp(), trigger="sit:low_vitality")
        return s

    def _cfg(self, **kw):
        base = dict(skill_proactive_enabled=True, skill_recall_enabled=True, skill_internal_coping_enabled=True,
                    skill_situations_enabled=True, notify_cooldown_min=30, skill_proactive_cooldown_min=180,
                    skill_proactive_max_reach_outs=2, spontaneous_quiet_after_chat_min=45,
                    skill_proactive_sticker_enabled=False, timezone="Asia/Taipei", dry_run=False)
        base.update(kw)
        return SimpleNamespace(**base)

    def _coach(self):
        return SimpleNamespace(enabled=True, voice_spontaneous=lambda seed, hist, coping="", time_rule="": "悶悶的，來找你聊" + ("｜"+coping if coping else ""))

    def test_fires_in_the_dead_zone(self):
        # 使用者情境：教過 low_vitality 做法、被晾（hunger=0.72＝含蓄伸手 0.85 到不了，但 low_vitality 0.7 成立）→ 主動發
        s = self._state(0.72, -0.1); c = FakeClient()
        monitor._coping_emit(c, s, self._cfg(), self._coach(), NOW)
        self.assertTrue(c.sent and c.sent[0].startswith("🫧"))
        self.assertIn("轉速緩慢時主動向使用者反應", "".join(c.sent))     # 帶上教過的做法
        self.assertEqual(s.entropy.coping_reach_outs_this_idle, 1)
        self.assertEqual(s.last_coping_reach_ts, NOW.timestamp())

    def test_only_for_taught_users(self):
        s = self._state(0.72, -0.1, taught=False); c = FakeClient()
        monitor._coping_emit(c, s, self._cfg(), self._coach(), NOW)
        self.assertEqual(c.sent, [])                                   # 沒教過＝coping 空＝不發

    def test_condition_not_live_no_fire(self):
        s = self._state(0.3, 0.2); c = FakeClient()                    # hunger 低、mood 正 → 無 live 內在訊號
        monitor._coping_emit(c, s, self._cfg(), self._coach(), NOW)
        self.assertEqual(c.sent, [])

    def test_own_budget_cap(self):
        s = self._state(0.72, -0.1, coping_reach=2); c = FakeClient()  # 本段預算用完
        monitor._coping_emit(c, s, self._cfg(), self._coach(), NOW)
        self.assertEqual(c.sent, [])

    def test_own_cooldown(self):
        s = self._state(0.72, -0.1); s.last_coping_reach_ts = NOW.timestamp() - 60; c = FakeClient()
        monitor._coping_emit(c, s, self._cfg(), self._coach(), NOW)
        self.assertEqual(c.sent, [])

    def test_quiet_after_chat(self):
        s = self._state(0.72, -0.1); s.last_user_msg_ts = NOW.timestamp() - 60; c = FakeClient()  # 剛聊完
        monitor._coping_emit(c, s, self._cfg(), self._coach(), NOW)
        self.assertEqual(c.sent, [])

    def test_anti_burst_shares_last_push(self):
        s = self._state(0.72, -0.1); s.last_push_ts = NOW.timestamp() - 60; c = FakeClient()  # 剛有別則推播
        monitor._coping_emit(c, s, self._cfg(), self._coach(), NOW)
        self.assertEqual(c.sent, [])

    def test_flag_off_byte_identical(self):
        s = self._state(0.72, -0.1); c = FakeClient()
        monitor._coping_emit(c, s, self._cfg(skill_proactive_enabled=False), self._coach(), NOW)
        self.assertEqual(c.sent, [])
        self.assertEqual(s.entropy.coping_reach_outs_this_idle, 0)     # 無任何狀態變動
        self.assertEqual(s.last_coping_reach_ts, 0)

    def test_budget_resets_on_fresh_ingest(self):
        # 有新輸入（entropy_update 換 ingest 戳）→ 內在因應伸手預算歸零（這段獨處重新起算）
        e = lifeloop.EntropyState(); e.coping_reach_outs_this_idle = 2; e.prev_ingest = "OLD"
        lifeloop.entropy_update(e, (0.0,) * 7, "NEW")
        self.assertEqual(e.coping_reach_outs_this_idle, 0)

    def test_help_sticker_bypasses_positive_gate(self):
        # 🎴 §0.65 求救貼圖：低狀態（pick_self_reaction 會回 None）也送，繞過正向心情閘
        s = self._state(0.72, -0.1)
        s.known_sticker_ids = [{"file_id": "HELP", "valence": "neutral"}]
        c = FakeClient(); c.stickers = []
        c.send_sticker = lambda fid: c.stickers.append(fid) or True
        monitor._coping_emit(c, s, self._cfg(skill_proactive_sticker_enabled=True), self._coach(), NOW)
        self.assertTrue(c.sent)                                        # 主動訊息
        self.assertEqual(c.stickers, ["HELP"])                        # ＋求救貼圖（負向狀態仍送）

    def test_help_sticker_prefers_distress_not_cheerful(self):
        # 審查 confirmed：求救時要送真的求救貼圖（負向🥺😭），不能送歡樂貼圖（正向）——與回送池相反向
        s = self._state(0.72, -0.1)
        s.known_sticker_ids = [{"file_id": "CHEER", "valence": "positive"},
                               {"file_id": "SOS", "valence": "negative"}]
        c = FakeClient(); c.stickers = []
        c.send_sticker = lambda fid: c.stickers.append(fid) or True
        monitor._coping_emit(c, s, self._cfg(skill_proactive_sticker_enabled=True), self._coach(), NOW)
        self.assertEqual(c.stickers, ["SOS"])                         # 送求救、不送歡樂


if __name__ == "__main__":
    unittest.main()
