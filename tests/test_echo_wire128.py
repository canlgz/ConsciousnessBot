"""🦜 §1.28 ECHO_STRIP_WIRE：echo 剝除接上互動出口——bot 不再裸複誦使用者罵句。

截圖（2026-07-12 21:04）：使用者連罵後，bot 自己發出「有夠爛……」的裸複誦泡泡（複誦使用者的罵句、
無「你說」歸屬）＝看起來像 bot 在罵。根因：echo.strip_leading_echo（echo.py，剝 LLM 開頭複誦）原設計
只接在 coach LLM 出口（coach.py:393-398、933-965，保留勿動），monitor 互動出口 _say 從未接線；
且其 min_len=5 預設、「有夠爛」3 字本來也擋不住。

修（ECHO_STRIP_WIRE，兩層分離定式：config _bool True／monitor getattr False）；echo.py 本體一行不改：
(a) handle_message 比照 §1.13B ground stash：旗標開時 _TURN['echo_user_texts'] = 近幾則使用者原話＋本句。
(b) _say 互動分支（§1.13B/§1.20/§1.23 攔截塊之後、_maybe_self_promise_capture 之前）接 _echo_strip_wire：
    ①首段歸屬豁免（正規化後以「你說/妳說」開頭＝引用、整個不剝——_looks_same 含包含判定，不豁免會誤剝）；
    ②min_len 旁路（reaction.is_hostile(u) **或**首段正規化後與某則使用者原話全等 → min_len=1 剝；
      未中走預設 min_len=5＝與 coach 端一致）；
    ③單段兜底（strip_leading_echo 對單段恆 no-op → 呼叫端切前綴；餘文空＝純複誦泡泡 → 整則替換
      確定性歸屬句「你說「…」，我聽到了。」）；
    ④有剝/有換 → 同步刷 _TURN['last_reply']，定稿在 §1.18 掃描之前。
全 stub、零網路。
"""

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from telegram_monitor import cost, echo, monitor, reaction
from telegram_monitor.state import State

NOW = 1_700_000_000


class EchoStripWireUnitTest(unittest.TestCase):
    """_echo_strip_wire 純函式語意（截圖重演 E 的各變體）。"""

    def test_recon_pins(self):
        """偵察實證釘死：規格旁路的前提——「有夠爛」不是敵意詞表項、「別騙人啦」是。"""
        self.assertFalse(reaction.is_hostile("有夠爛"))
        self.assertFalse(reaction.is_hostile("有夠爛……"))
        self.assertTrue(reaction.is_hostile("別騙人啦"))

    def test_equal_bypass_multiline(self):
        # ①多段、首段與使用者原話正規化全等（3 字 <min_len=5、非敵意 → 只靠全等旁路才剝）
        out, hit = monitor._echo_strip_wire("有夠爛……\n我聽到了，這句話很重。", ["有夠爛……"])
        self.assertTrue(hit)
        self.assertEqual(out, "我聽到了，這句話很重。")

    def test_equal_bypass_ellipsis_joined(self):
        # ②「有夠爛……我聽到了」：… 是段界 → 兩段，前綴段剝除、以「我聽到了」開頭
        out, hit = monitor._echo_strip_wire("有夠爛……我聽到了", ["有夠爛……"])
        self.assertTrue(hit)
        self.assertTrue(out.startswith("我聽到了"))

    def test_pure_echo_bubble_replaced_with_attribution(self):
        # ③純複誦泡泡（破口③本尊）：整則＝使用者罵句 → 替換確定性歸屬句
        out, hit = monitor._echo_strip_wire("有夠爛……", ["有夠爛……"])
        self.assertTrue(hit)
        self.assertEqual(out, "你說「有夠爛」，我聽到了。")

    def test_attribution_first_seg_exempt(self):
        # ④首段歸屬豁免：「你說有夠爛。」是誠實引用 → 整個不剝（不豁免會被包含判定誤剝）
        msg = "你說有夠爛。\n我想跟你談談這句。"
        out, hit = monitor._echo_strip_wire(msg, ["有夠爛……"])
        self.assertFalse(hit)
        self.assertEqual(out, msg)

    def test_hostile_bypass_multiline(self):
        # ⑤敵意旁路：「別騙人啦」4 字 <5，靠 is_hostile 旁路剝
        out, hit = monitor._echo_strip_wire("別騙人啦。\n我真的沒有要騙你。", ["別騙人啦"])
        self.assertTrue(hit)
        self.assertTrue(out.startswith("我真的沒有要騙你"))

    def test_hostile_bypass_single_seg_prefix_cut(self):
        # ⑤b 單段（逗號非段界）＋敵意前綴 → 切掉前綴與其後標點、餘文非空
        out, hit = monitor._echo_strip_wire("別騙人啦，我真的沒有要騙你", ["別騙人啦"])
        self.assertTrue(hit)
        self.assertEqual(out, "我真的沒有要騙你")

    def test_default_min_len_long_echo(self):
        # ⑥非敵意長複誦（≥5 字、非全等）：沿用預設 min_len=5 行為＝與 coach 端語意一致
        out, hit = monitor._echo_strip_wire("我今天心情真的很差。\n聽起來真的很悶。",
                                            ["我今天心情真的很差喔"])
        self.assertTrue(hit)
        self.assertEqual(out, "聽起來真的很悶。")

    def test_short_nonhostile_nonequal_untouched(self):
        # 護欄：短開頭非全等、非敵意（「好，那我…」類）→ 不觸發旁路、不剝
        msg = "好，那我們繼續。\n剛剛說到哪了。"
        out, hit = monitor._echo_strip_wire(msg, ["好啊"])
        self.assertFalse(hit)
        self.assertEqual(out, msg)

    def test_first_seg_containing_user_word_not_equal_untouched(self):
        # 護欄：首段只是「談到」那三個字（包含但非全等、<5 字）→ 不剝（在談論、不是複誦）
        msg = "有夠爛這三個字很重。\n我想懂你為什麼這麼說。"
        out, hit = monitor._echo_strip_wire(msg, ["有夠爛……"])
        self.assertFalse(hit)
        self.assertEqual(out, msg)

    def test_genuine_denial_reply_untouched(self):
        # 護欄：對敵意句的真心回應（非複誦）→ 敵意旁路不誤傷
        msg = "我沒有騙你啦。\n真的。"
        out, hit = monitor._echo_strip_wire(msg, ["別騙人啦"])
        self.assertFalse(hit)
        self.assertEqual(out, msg)

    def test_empty_inputs_noop(self):
        self.assertEqual(monitor._echo_strip_wire("", ["有夠爛"]), ("", False))
        self.assertEqual(monitor._echo_strip_wire("我在。", []), ("我在。", False))


class HandleMessageEchoWireTest(unittest.TestCase):
    """端到端：stash → _say 接線 → client.send 的定稿（旗標開）；旗標關＝現狀鎖定。"""

    def _run(self, text, voice, cfg, history=None):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        if history:
            s.convo_history = list(history)
        coach = SimpleNamespace(enabled=True,
                                meter=cost.CostMeter(in_per_m=0.3, out_per_m=2.5, usd_twd=32,
                                                     window_min=10, threshold_twd=10, cooldown_min=30),
                                reply=lambda *a, **k: voice,
                                ask=lambda *a, **k: ("chat", None, voice))
        client = SimpleNamespace(sent=[], dry_run=False)
        client.send = lambda t: client.sent.append(t) or True
        update = {"message": {"chat": {"id": 1}, "text": text, "date": NOW}}
        with patch("telegram_monitor.coach.build_memory_brief", return_value="brief"):
            monitor.handle_message(update, coach, None, {"meta": {}, "records": []}, object(),
                                   s, client, cfg, None)
        return client

    def _cfg_on(self):
        return SimpleNamespace(dry_run=False, telegram_chat_id="", echo_strip_wire_enabled=True)

    def _cfg_off(self):
        # 既有測試假 cfg 形：**無** echo_strip_wire_enabled 屬性＝getattr 預設 False＝基線
        return SimpleNamespace(dry_run=False, telegram_chat_id="")

    def test_flag_on_leading_echo_stripped(self):
        """截圖重演 E①：剛說「有夠爛……」＋coach 回以其開頭的多段 → 送出不以罵句開頭、保留實質段。"""
        client = self._run("有夠爛……", "有夠爛……\n我聽到了，這句話很重。", self._cfg_on())
        joined = "".join(client.sent)
        self.assertTrue(client.sent)
        self.assertFalse(joined.startswith("有夠爛"))
        self.assertIn("我聽到了", joined)

    def test_flag_on_pure_echo_replaced(self):
        """截圖重演 E③：coach 整則＝罵句複誦（破口③本尊）→ 送出＝確定性歸屬句。"""
        client = self._run("有夠爛……", "有夠爛……", self._cfg_on())
        joined = "".join(client.sent)
        self.assertIn("你說「有夠爛」，我聽到了。", joined)
        self.assertFalse(joined.startswith("有夠爛"))

    def test_flag_on_attribution_kept_verbatim(self):
        """E④歸屬豁免：coach 回「你說有夠爛。…」→ 原樣送出（不誤剝）。"""
        client = self._run("有夠爛……", "你說有夠爛。\n我想跟你談談這句。", self._cfg_on())
        joined = "".join(client.sent)
        self.assertTrue(joined.startswith("你說有夠爛"))
        self.assertIn("我想跟你談談這句", joined)

    def test_flag_on_history_hostile_echo_stripped(self):
        """E⑤ stash 含近幾則使用者原話：上一則的敵意句被複誦也剝（不只本句）。"""
        hist = [{"role": "user", "text": "別騙人啦", "ts": NOW - 60}]
        client = self._run("你明明就不行", "別騙人啦。\n我真的沒有要騙你。", self._cfg_on(), history=hist)
        joined = "".join(client.sent)
        self.assertFalse(joined.startswith("別騙人啦"))
        self.assertIn("我真的沒有要騙你", joined)

    def test_flag_on_final_text_reaches_downstream(self):
        """E⑦：剝除後 _TURN['last_reply'] 已更新、_maybe_self_promise_capture 收到定稿文字。"""
        with patch.object(monitor, "_maybe_self_promise_capture") as cap:
            self._run("有夠爛……", "有夠爛……\n我聽到了，這句話很重。", self._cfg_on())
        self.assertTrue(cap.called)
        final = cap.call_args[0][0]
        self.assertFalse(final.startswith("有夠爛"))
        self.assertEqual(final, monitor._TURN["last_reply"])

    def test_flag_off_all_verbatim(self):
        """E⑧現狀鎖定：旗標關（假 cfg 無此欄）→ ①②③輸入全部原樣送出。"""
        c1 = self._run("有夠爛……", "有夠爛……\n我聽到了，這句話很重。", self._cfg_off())
        self.assertTrue("".join(c1.sent).startswith("有夠爛"))
        c2 = self._run("有夠爛……", "有夠爛……我聽到了", self._cfg_off())
        self.assertTrue("".join(c2.sent).startswith("有夠爛"))
        c3 = self._run("有夠爛……", "有夠爛……", self._cfg_off())
        joined3 = "".join(c3.sent)
        self.assertIn("有夠爛", joined3)
        self.assertNotIn("你說「", joined3)

    def test_flag_on_cost_fast_path_untouched(self):
        """E⑨護欄：cost 快路（client.send 直送、不經 _say）不受影響——與 §1.26 互補而非重疊。"""
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        s.cost_month_usd, s.cost_total_usd = 2.41, 3.22
        coach = SimpleNamespace(enabled=True,
                                meter=cost.CostMeter(in_per_m=0.3, out_per_m=2.5, usd_twd=32,
                                                     window_min=10, threshold_twd=10, cooldown_min=30),
                                reply=lambda *a, **k: "（不該走到這）",
                                ask=lambda *a, **k: ("chat", None, "（不該走到這）"))
        client = SimpleNamespace(sent=[], dry_run=False)
        client.send = lambda t: client.sent.append(t) or True
        update = {"message": {"chat": {"id": 1}, "text": "目前花費了多少？", "date": NOW}}
        with patch("telegram_monitor.coach.build_memory_brief", return_value="brief"):
            monitor.handle_message(update, coach, None, {"meta": {}, "records": []}, object(),
                                   s, client, self._cfg_on(), None)
        self.assertIn("💸", "".join(client.sent))

    def test_echo_module_defaults_untouched(self):
        """護欄：echo.py 本體一行不改——單段恆 no-op、min_len=5 預設仍擋 3 字短句。"""
        self.assertEqual(echo.strip_leading_echo("有夠爛……", ["有夠爛……"], min_len=1),
                         ("有夠爛……", False))
        self.assertEqual(echo.strip_leading_echo("有夠爛……\n我聽到了", ["有夠爛……"]),
                         ("有夠爛……\n我聽到了", False))


if __name__ == "__main__":
    unittest.main()
