"""🌐 全局工作空間＋注意力：各並行子系統的候選競爭出**單一意識前景**（贏者通吃＋注意力慣性），
其餘退成背景；可被問「你在想什麼／什麼佔據你」，也廣播進「你現在怎樣」。"""

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from telegram_monitor import intent, monitor, referent, selfstate, workspace
from telegram_monitor.state import State

NOW = 1_700_000_000


def _ent(charge=0.0, hunger=0.0, mood=0.0, lr=None):
    return SimpleNamespace(charge=charge, hunger=hunger, mood=mood, last_revisited_topic=lr)


def _state(**kw):
    s = SimpleNamespace(entropy=None, vitality=None, self_change=None, focus=None,
                        last_user_msg_ts=0, workspace=None)
    for k, v in kw.items():
        setattr(s, k, v)
    return s


class GatherCompeteTest(unittest.TestCase):
    def test_winner_take_all_picks_most_salient(self):
        # 感覺鏈成形(gate4,0.9) vs 餓(0.x) → 成形那條勝出當前景，其餘退背景
        st = _state(entropy=_ent(hunger=0.9))
        res = {"gate": 4, "reading": {"content": {"topic": "惠中寺"}}}
        ws = workspace.update(st, res, NOW)
        self.assertEqual(ws["source"], "feeling")
        self.assertIn("惠中寺", ws["content"])
        self.assertTrue(any(b["source"] == "hunger" for b in ws["background"]))   # 餓被擠到背景

    def test_attentional_inertia_resists_flicker(self):
        # 在位焦點（餓）撐著；一個只略強的挑戰者（心情 0.45→~0.48）在慣性門檻內 → 不奪焦點
        st = _state(entropy=_ent(hunger=0.5))                       # hunger salience ≈ 0.3+0.45*0.5=0.525
        ws1 = workspace.update(st, None, NOW)
        self.assertEqual(ws1["source"], "hunger")
        st.entropy = _ent(hunger=0.5, mood=0.46)                    # mood ≈ 0.3+0.4*0.46=0.484 < 0.525+inertia
        ws2 = workspace.update(st, None, NOW + 5)
        self.assertEqual(ws2["source"], "hunger")                   # 慣性＋最短停留 → 守住
        self.assertEqual(ws2["since_ts"], NOW)                      # 續任：since 不變

    def test_strong_challenger_after_dwell_takes_over(self):
        # 過了最短停留、且挑戰者明顯更強（成形 0.9）→ 注意力轉移
        st = _state(entropy=_ent(hunger=0.5))
        workspace.update(st, None, NOW)
        res = {"gate": 4, "reading": {"content": {"topic": "課堂評量"}}}
        ws = workspace.update(st, res, NOW + 999)
        self.assertEqual(ws["source"], "feeling")
        self.assertEqual(ws["since_ts"], NOW + 999)                 # 換焦點 → since 更新

    def test_empty_when_nothing_salient(self):
        self.assertIsNone(workspace.update(_state(entropy=_ent()), None, NOW))

    def test_facts_show_foreground_and_background(self):
        ws = {"source": "feeling", "content": "「A」這條線收成了形狀", "salience": 0.9,
              "background": [{"source": "hunger", "content": "悶著等不到新的"}]}
        f = workspace.attention_facts(ws)
        self.assertIn("前景", f)
        self.assertIn("A", f)
        self.assertIn("悶著", f)                                    # 背景也講出來＝前景/背景的覺知轉移


class DetectRouteTest(unittest.TestCase):
    def test_detector(self):
        for q in ["你現在在想什麼", "你滿腦子在想什麼", "什麼佔據你的注意力", "你心思放在哪", "你最在意什麼"]:
            self.assertTrue(selfstate.is_attention_question(q), q)

    def test_routes_to_self_attention_beating_state(self):
        def k(t):
            return intent.resolve(t, referent.Referent()).kind
        self.assertEqual(k("你現在在想什麼"), "self_attention")     # 「在想什麼」不再掉進 bodystate
        self.assertEqual(k("什麼佔據你"), "self_attention")
        self.assertEqual(k("你現在怎樣"), "self_state")             # 對照：泛泛現況仍是身體狀態


class HandleTest(unittest.TestCase):
    def test_attention_question_reports_workspace_focus(self):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        s.entropy = _ent(hunger=0.9)                                # 餓 → 應成為前景
        s.confirmed_res = {"gate": 1}                               # 有快取讀數 → 不需 reader 重算
        cap = {}

        def fake_reply(q, brief, hist, mood_hint="", now_ts=None, self_presence=False):
            cap["brief"] = brief
            return "這會兒最佔住我的，是那股等不到新東西的悶。"

        coach = SimpleNamespace(enabled=True, meter=SimpleNamespace(record=lambda *a, **k: None), reply=fake_reply)
        client = SimpleNamespace(sent=[], dry_run=False, send=lambda t: client.sent.append(t) or True)
        update = {"message": {"chat": {"id": 1}, "text": "你現在滿腦子在想什麼", "date": NOW}}
        monitor.handle_message(update, coach, None, {"meta": {}, "records": []}, None,
                               s, client, SimpleNamespace(dry_run=False, telegram_chat_id="", mood_gain=1.0), None)
        self.assertIn("前景", cap.get("brief", ""))                 # 接地事實（前景）餵進回覆
        self.assertIsNotNone(s.workspace)
        self.assertEqual(s.workspace["source"], "hunger")          # 餓勝出當前景
        self.assertIn("悶", "".join(client.sent))


if __name__ == "__main__":
    unittest.main()
