"""對話連貫契約（exemplar 端到端）：bot 回應「當下這句」前，必須承接前文——
使用者前文＋bot 自己前文（差別只在時間間隔長短），並納入時間感受。

破綻範本（務必接回同一個 X、別否認/跳線/矛盾）：
  使用者：「你對水管維修那則的感覺」→ bot：「感覺有點煩躁，因為這條線推進到得找人來修…」
  使用者：「為什麼感覺有點煩躁？」（追問 bot **自己剛說的**那個煩躁）
  bot 不該：「你說我有點煩躁嗎？」→ 跳到別條線「研發 writetolearn」→「最近感覺還蠻順利的」（矛盾）。

驗的是「接回的是不是同一個 X」（方向正確），而非只驗窗開沒開。純函式、不碰 LLM。"""

import os
import tempfile
import unittest
from unittest import mock

from telegram_monitor import affect, intent, lifeloop, monitor, referent, selfstate, temporal
from telegram_monitor.state import State


class _Coach:
    """最小可用 coach 替身（enabled、有 api_key）；gemini.generate 由測試 mock。"""
    enabled = True
    api_key = "k"
    model = "m"

    class meter:
        @staticmethod
        def record(*a, **k):
            pass

NOW = 1_700_000_000
WINDOW = referent.FOLLOWUP_WINDOW_SEC


def _state():
    s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
    s.entropy = lifeloop.EntropyState()
    return s


def _exemplar_state(model_text="我覺得有點煩躁，因為水管維修這條線推進到得找人來修…", model_gap=5):
    """構造破綻場景：剛對「水管維修」自陳過煩躁（model 句進 convo_history），focus=水管維修，
    last_topic_res 帶 gate≥3 的那條 res，selfstate_open_ts=剛剛。"""
    s = _state()
    s.convo_history = [
        {"role": "user", "text": "你對水管維修那則的感覺", "ts": NOW - model_gap - 1},
        {"role": "model", "text": model_text, "ts": NOW - model_gap},
    ]
    s.focus = {"topic": "水管維修", "fresh": False, "ts": NOW - model_gap}
    res = {"gate": 3, "moved": ["F", "B", "S"], "scope": {"dominant": "水管維修"}}
    s.last_topic_res = {"res": res, "topic": "水管維修", "ts": NOW - model_gap}
    s.selfstate_open_ts = NOW - model_gap
    s.last_user_msg_ts = NOW - model_gap - 1
    return s


class SelfPriorFactTest(unittest.TestCase):
    """🪞 self-prior 安全網：唯一權威源＝convo_history 末則 model 真實文本。"""

    def test_extracts_just_said_feeling_and_direction(self):
        s = _exemplar_state()
        fact = monitor._self_prior_fact(s, NOW)
        self.assertIn("煩躁", fact)                 # 接回剛說的那個 X
        self.assertNotIn("順利", fact)              # 不是相反方向（Q3 核心）
        self.assertIn("水管維修", fact)             # 帶上焦點

    def test_just_now_says_just_now_not_zero_minute(self):
        s = _exemplar_state(model_gap=5)            # 5 秒前 → 剛剛
        fact = monitor._self_prior_fact(s, NOW)
        self.assertIn("剛", fact)
        self.assertNotIn("0 分鐘", fact)            # <60s 不外露「0 分鐘」

    def test_authority_is_model_text_not_drifted_affect(self):
        # affect 已漂移到「順利」，但 model 末句說「煩躁」→ self-prior 仍回「煩躁」（只吃 model 文本）
        s = _exemplar_state(model_text="我覺得有點煩躁，因為水管維修要找人修")
        s.affect = {"label": "順利", "tendency": "settle"}
        fact = monitor._self_prior_fact(s, NOW)
        self.assertIn("煩躁", fact)
        self.assertNotIn("順利", fact)

    def test_no_feeling_word_returns_empty(self):
        # model 末句不含任何感覺詞 → 回 ''（誠實 unknown，不硬填、不退用結構欄位）
        s = _exemplar_state(model_text="這條線推進到得找人來修了。")
        self.assertEqual(monitor._self_prior_fact(s, NOW), "")

    def test_no_model_turn_returns_empty(self):
        s = _state()
        s.convo_history = [{"role": "user", "text": "你怎樣", "ts": NOW - 5}]
        self.assertEqual(monitor._self_prior_fact(s, NOW), "")

    def test_missing_ts_returns_empty(self):
        s = _state()
        s.convo_history = [{"role": "model", "text": "我覺得有點煩躁"}]   # 無 ts
        self.assertEqual(monitor._self_prior_fact(s, NOW), "")

    def test_earlier_uses_distance_framing(self):
        s = _exemplar_state(model_gap=5 * 3600)     # 5 小時前 → 帶時間距離承接
        fact = monitor._self_prior_fact(s, NOW)
        self.assertIn("小時前", fact)
        self.assertIn("煩躁", fact)


class FollowupRoutingTest(unittest.TestCase):
    """路由：last_topic_res 也能撐開追問窗 → 追問接回 selfstate_followup，不掉回 bodystate。"""

    def test_last_topic_res_opens_followup_window(self):
        s = _exemplar_state()
        ref = referent.resolve(s, NOW, WINDOW)
        self.assertTrue(ref.followup_open)          # 閘#1：last_topic_res 撐開窗

    def test_why_routes_to_followup_not_bodystate(self):
        s = _exemplar_state()
        ref = referent.resolve(s, NOW, WINDOW)
        got = intent.resolve("為什麼感覺有點煩躁", ref)
        self.assertEqual(got.kind, "selfstate_followup")   # 不再 self_state→topic=None→bodystate

    def test_window_expired_no_followup(self):
        s = _exemplar_state()
        s.selfstate_open_ts = NOW - WINDOW - 1
        self.assertFalse(referent.resolve(s, NOW, WINDOW).followup_open)

    def test_self_state_path_unchanged_when_present(self):
        # 既有語意不變：self_state 在時，followup_open 仍只靠窗（迴歸守 test_followup_open_needs_recent_selfreport）
        s = _state()
        s.self_state = {"gate": 3}
        s.selfstate_open_ts = NOW - 60
        self.assertTrue(referent.resolve(s, NOW, WINDOW).followup_open)


class FollowupSourceTest(unittest.TestCase):
    """致命閘#2：followup 渲染源優先用 last_topic_res['res']（剛自陳的那條），非泛用 confirmed_res。"""

    def test_followup_prefers_last_topic_res(self):
        s = _exemplar_state()
        s.confirmed_res = {"gate": 4, "reading": {"content": {"topic": "研發 writetolearn"}}}  # 漂移後的泛用讀數
        stash = getattr(s, "last_topic_res", None)
        stable = (stash.get("res") if stash else None) or s.confirmed_res or s.self_state
        self.assertEqual(stash["topic"], "水管維修")
        self.assertEqual((stable.get("scope") or {}).get("dominant"), "水管維修")   # 接回水管維修、非 writetolearn


class FeelingWordTableTest(unittest.TestCase):
    """感覺詞表（affect 集中常數）：擷取最先出現的詞、長詞優先、抽不到回 ''。"""

    def test_extract_longest_first(self):
        self.assertEqual(affect.extract_feeling_word("我有點煩躁"), "煩躁")
        self.assertEqual(affect.extract_feeling_word("最近還蠻順利的"), "順利")
        self.assertEqual(affect.extract_feeling_word("沒什麼特別的"), "")

    def test_opposite_direction(self):
        self.assertEqual(affect.opposite_feeling("煩躁"), "順利")
        self.assertEqual(affect.opposite_feeling("順利"), "煩躁")


class SpokenGapTest(unittest.TestCase):
    """口語相對時間（單一防線）：<60s→剛剛、不外露『0 分鐘』；隨間隔升級；映射承接框架。"""

    def test_buckets(self):
        self.assertEqual(temporal.spoken_gap(5), "剛剛")
        self.assertEqual(temporal.spoken_gap(3 * 60), "約 3 分鐘前")
        self.assertEqual(temporal.spoken_gap(5 * 3600), "約 5 小時前")
        self.assertEqual(temporal.spoken_gap(2 * 86400), "約 2 天前")

    def test_under_minute_never_zero_minute(self):
        self.assertNotIn("0 分鐘", temporal.spoken_gap(59))

    def test_frame_mapping(self):
        self.assertEqual(temporal.spoken_frame(5), "just")
        self.assertEqual(temporal.spoken_frame(5 * 3600), "earlier")
        self.assertEqual(temporal.spoken_frame(4 * 86400), "long")


class ConnectHintTimeFeelTest(unittest.TestCase):
    """_connect_hint 納入 user-prior 時間感（只管 user-prior；self-prior 不走這條）。"""

    def test_just_now_user_prior(self):
        s = _state()
        s.last_user_msg_ts = NOW - 5
        hint = monitor._connect_hint(s, NOW, current="你對水管維修的感覺")
        self.assertIn("剛剛", hint)
        self.assertNotIn("0 分鐘", hint)

    def test_no_self_prior_in_connect_hint(self):
        # 單一管道：_connect_hint 絕不注入「我自己剛說過 X」（避免兩份來源打架）
        s = _exemplar_state()
        hint = monitor._connect_hint(s, NOW, current="為什麼感覺有點煩躁")
        self.assertNotIn("我自己剛說過", hint)
        self.assertNotIn("以它為準", hint)


class ReferentInsightTest(unittest.TestCase):
    """💡 同構盲點：追問 bot 剛冒的聯想 → self_acts_text 追加一行（簽名不變、fragment 不動）。"""

    def test_insight_in_window_adds_line(self):
        s = _state()
        s.last_insight = {"event": {"a": "惠中寺", "b": "writetolearn"}, "ts": NOW - 30}
        txt = referent.self_acts_text(referent.resolve(s, NOW, WINDOW))
        self.assertIn("連起來", txt)
        self.assertIn("惠中寺", txt)
        self.assertIn("writetolearn", txt)

    def test_insight_out_of_window_dropped(self):
        s = _state()
        s.last_insight = {"event": {"a": "惠中寺", "b": "writetolearn"},
                          "ts": NOW - referent.SELF_ACT_RECALL_SEC - 1}
        self.assertNotIn("連起來", referent.self_acts_text(referent.resolve(s, NOW, WINDOW)))


class RenderSelfPriorInjectionTest(unittest.TestCase):
    """三個渲染函式都吃 self_prior 並把它＋承接規則注入 system；render_bodystate_again（最盲那條）也要守。"""

    SP = "〔我自己剛說過的（程式算好的事實，以它為準先承接）〕我緊接著剛說過：我覺得「煩躁」。"

    def _capture(self, fn, *args, **kwargs):
        """跑渲染函式，回傳傳給 gemini.generate 的 (system, user) 兩串。"""
        seen = {}

        def fake(api_key, model, system, user, **kw):
            seen["system"], seen["user"] = system, user
            return "（自陳）"

        with mock.patch("telegram_monitor.gemini.generate", side_effect=fake):
            fn(*args, **kwargs)
        return seen["system"], seen["user"]

    def test_render_bodystate_injects_rule(self):
        sys, user = self._capture(selfstate.render_bodystate, None,
                                  {"gate": 3, "scope": {"dominant": "水管維修"}}, _Coach(), self_prior=self.SP)
        self.assertIn("以它為準", sys)               # SELF_PRIOR_RULE 進 system
        self.assertIn("煩躁", user)                  # self_prior 事實進 facts/user

    def test_render_gate3_injects_rule(self):
        sys, _ = self._capture(selfstate.render,
                               {"gate": 3, "moved": ["F"], "scope": {"dominant": "水管維修"}}, _Coach(),
                               self_prior=self.SP)
        self.assertIn("以它為準", sys)

    def test_render_bodystate_again_injects_rule(self):
        sys, user = self._capture(selfstate.render_bodystate_again, 2, None,
                                  {"gate": 3, "scope": {"dominant": "水管維修"}}, _Coach(), self_prior=self.SP)
        self.assertIn("沒變", sys)                   # REPEAT system 的「沒變以 X 為準」規則仍在
        self.assertIn("煩躁", user)                  # self_prior 進背景事實

    def test_no_self_prior_no_rule(self):
        # self_prior 為空 → 不注入規則（不 churn 既有無前文路徑）
        sys, _ = self._capture(selfstate.render_bodystate, None,
                               {"gate": 3, "scope": {"dominant": "x"}}, _Coach(), self_prior="")
        self.assertNotIn("以它為準", sys)


if __name__ == "__main__":
    unittest.main()
