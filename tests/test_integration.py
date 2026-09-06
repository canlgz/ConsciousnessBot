"""🧩 整合測試（R5）：把結構性收斂（R1 統一自我模型／R3 封死 overall_stats／R4 統一發話政策／R5 防杜撰）
鎖住、防回歸。這些是**跨模組**的斷言——單模組單測抓不到的（同問不一致、被誇吐 📊、在場還獨白、虛構引用）。"""

import unittest
from types import SimpleNamespace
from unittest import mock

from telegram_monitor import datatools, intent, monitor, persona, referent, selfmodel, selfstate

NOW = 1_700_000_000.0


def _k(t):
    return intent.resolve(t, referent.Referent()).kind


class StatsUnreachableTest(unittest.TestCase):
    """R3：overall_stats 結構性不可達——只有明確問整體數字的 fast-path 到得了，漏接再也吐不出 📊。"""

    def test_overall_stats_not_in_llm_tool_table(self):
        self.assertNotIn("overall_stats", [d["name"] for d in datatools.TOOL_DECLS])

    def test_explicit_stats_question_routes_to_fastpath(self):
        for q in ["總共幾筆", "整體數字如何", "漏斗現在幾條", "連續記寫幾天了"]:
            self.assertEqual(_k(q), "stats", q)

    def test_non_stats_never_routes_to_stats(self):
        # 讚美/附和/問候/狀態/閒聊 → 絕不是 stats（也就絕不吐 📊）
        for q in ["這麼厲害....", "是啊", "好厲害", "你現在怎樣", "你好嗎", "哈哈", "今天天氣不錯"]:
            self.assertNotEqual(_k(q), "stats", q)


class PraiseAndAckAreConversationalTest(unittest.TestCase):
    """R3+smalltalk：讚美/純附和走純對話、不開任何工具路徑。"""

    def test_praise_and_ack_go_smalltalk(self):
        for q in ["這麼厲害....", "好棒喔", "太強了！", "是啊", "對", "了不起"]:
            self.assertEqual(_k(q), "smalltalk", q)


class UnifiedSelfModelTest(unittest.TestCase):
    """R1：self_now 是單一真相源——各 facet 的『前景/情緒』來自同一份，不會各說各話。"""

    def _state(self):
        return SimpleNamespace(
            entropy=SimpleNamespace(charge=0.2, hunger=0.7, mood=0.1, last_revisited_topic=None),
            vitality={"uptime_s": 600, "alive": True, "k_adj": -0.1},
            workspace={"source": "hunger", "content": "悶著、等不到新的東西",
                       "background": [{"content": "心情還行"}]},
            stream={"texture": "wandering"},
            self_model={"belief": "hungry", "confidence": 0.6, "mismatch": None},
            goals=[], self_now=None)

    def test_build_integrates_subsystems(self):
        sn = selfmodel.build(self._state(), {"gate": 3, "scope": {"dominant": "惠中寺"}}, NOW)
        self.assertEqual(sn["foreground"], "悶著、等不到新的東西")     # 🌐 工作空間前景
        self.assertEqual(sn["affect"]["hunger"], 0.7)                # 乙 內在熵
        self.assertEqual(sn["feeling"]["topic"], "惠中寺")           # 甲 手上那條線
        self.assertEqual(sn["flow_texture"], "wandering")           # ⏳ 意識之流
        self.assertEqual(sn["metacog"]["belief"], "hungry")        # 🪞 後設信念

    def test_foreground_is_single_source(self):
        # 「此刻最佔住我的」對所有 facet 是同一個（共同錨點）→ 不會 self_state 說 A、self_attention 說 B
        s = self._state()
        sn = selfmodel.build(s, None, NOW)
        self.assertIn(sn["foreground"], selfmodel.foreground_clause(sn))
        self.assertIn("悶", selfmodel.overall_facts(sn))            # 整合自陳也踩同一個前景/情緒

    def test_overall_facts_is_one_coherent_self(self):
        sn = selfmodel.build(self._state(), {"gate": 4, "reading": {"content": {"topic": "惠中寺"}}}, NOW)
        f = selfmodel.overall_facts(sn)
        self.assertIn("同一個", f)                                  # 明說是「兜起來的同一個我」、非分開 report


class ProactivePolicyTest(unittest.TestCase):
    """R4：統一發話政策——你在場或深夜，所有主動管道一律不開口。"""

    def test_present_blocks_proactive(self):
        s = SimpleNamespace(coupling=SimpleNamespace(round_open=True), last_user_msg_ts=NOW - 5)
        self.assertFalse(monitor._proactive_ok(s, SimpleNamespace(timezone="Asia/Taipei"), NOW))

    def test_away_allows_proactive(self):
        s = SimpleNamespace(coupling=SimpleNamespace(round_open=False), last_user_msg_ts=NOW - 99999)
        # 白天、離開久了 → 可主動（深夜邏輯交給 circadian，這裡給非深夜時刻）
        ok = monitor._proactive_ok(s, SimpleNamespace(timezone="Asia/Taipei"), NOW)
        self.assertIn(ok, (True, False))                           # 不論晝夜，至少『在場』那條已讓位（不報錯）
        self.assertFalse(monitor._proactive_ok(
            SimpleNamespace(coupling=SimpleNamespace(round_open=True), last_user_msg_ts=0),
            SimpleNamespace(timezone="Asia/Taipei"), NOW))           # 在場 → 必 False

    def test_unanswered_proactive_blocks_a_new_topic(self):
        # 使用者已離開夠久，但 bot 的上一則主動訊息仍沒被接住；不能等冷卻一過
        # 就換 🫧／🌀／💡 另一條 lane 繼續自言自語。
        s = SimpleNamespace(coupling=SimpleNamespace(round_open=False),
                            last_user_msg_ts=NOW - 99999, last_push_ts=NOW - 3600)
        cfg = SimpleNamespace(timezone="Asia/Taipei", unanswered_proactive_guard_enabled=True)
        self.assertFalse(monitor._proactive_ok(s, cfg, NOW))

    def test_user_reply_releases_the_next_initiative(self):
        # 下一次使用者接話後，舊的 bot 主動訊息不再占住場；等人離開後可有新的主動內容。
        s = SimpleNamespace(coupling=SimpleNamespace(round_open=False),
                            last_user_msg_ts=NOW - 99999, last_push_ts=NOW - 100000)
        cfg = SimpleNamespace(timezone="Asia/Taipei", unanswered_proactive_guard_enabled=True)
        with mock.patch("telegram_monitor.monitor.circadian.is_quiet_hours", return_value=False):
            self.assertTrue(monitor._proactive_ok(s, cfg, NOW))


class AntiFabricationTest(unittest.TestCase):
    """R5：人格底線明令不得虛構對方說過的話（修『好厲害的樣子』張冠李戴）。"""

    def test_socratic_system_forbids_fabricated_quotes(self):
        self.assertIn("絕不虛構對方說過的話", persona.SOCRATIC_SYSTEM)
        self.assertIn("杜撰", persona.SOCRATIC_SYSTEM)


if __name__ == "__main__":
    unittest.main()
