"""🚪 根因 1 核心閘：非記寫資料問句別觸發 Drive 證據工具（records_in_time_range 等）。

在 handle_message 落 fact_or_chat → coach.ask 這一處，攔截 evidence_tools kwarg，斷言：
- 非記寫的對話事件/meta 問句 → evidence_tools=False（LLM 不可達 records_in_time_range，不再回 📂「那段你沒有記寫」）。
- 明確查記寫（looks_like_data_question / is_explicit_records_intent，含對話脈絡指代/時間範圍）→ evidence_tools=True（仍走完整工具表）。
- 旗標關（evidence_gate_enabled=False）→ allow_evidence 恆 True（byte-identical 退路）。
"""

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from telegram_monitor import monitor, selfstate
from telegram_monitor.state import State

NOW_TS = 1_700_000_000


class IsExplicitRecordsIntentTest(unittest.TestCase):
    """🚪 補 looks_like_data_question 漏掉的口語/指代/時間範圍資料問句（單參數純函式）。"""

    def test_hits(self):
        for q in ["查我那段記寫", "列我寫過的東西", "翻一下那筆記寫", "找我記下的",
                  "剛剛那批呢", "昨天那些呢", "上週的呢", "靈感那條後來呢",
                  "我前天記的東西", "我最早那筆", "幫我回顧一下這個月"]:
            self.assertTrue(selfstate.is_explicit_records_intent(q), q)

    def test_misses_bot_directed_and_chat(self):
        # 指向 bot 的對話事件/meta（含你/妳）、在談 bot 自己、純聊天 → 不算查記寫
        for q in ["剛剛有人指責你", "你怎麼還沒開始分享聯想", "你剛剛點了什麼", "你剛說的多久前",
                  "你會死嗎", "天氣如何", "我今天心情不好", "對話的時間點"]:
            self.assertFalse(selfstate.is_explicit_records_intent(q), q)


class IsOpinionAboutEntryTest(unittest.TestCase):
    """🚪 §0.56：問 bot 看法/理由/態度（非要資料）→ True＝別開證據閘。"""

    def test_opinion_hits(self):
        for q in ["你好像很執著這一則記寫", "為什麼呢", "它很特別嗎", "你為什麼對這則這麼感興趣",
                  "你怎麼看這則記寫", "這則記寫對你來說特別嗎", "你怎麼會一直提這則", "它有什麼特別"]:
            self.assertTrue(selfstate.is_opinion_about_entry(q), q)

    def test_data_requests_miss(self):
        # 明確要內容/統計/列出/查/進度 → 不算純看法（證據閘照舊）
        for q in ["這則記寫寫了什麼", "列出讀誦經書的清單", "我這個月記了幾筆", "讀誦經書進度",
                  "把那批記寫調出來", "這則的內容是什麼"]:
            self.assertFalse(selfstate.is_opinion_about_entry(q), q)

    def test_plain_non_opinion_miss(self):
        for q in ["", "今天天氣真好", "我剛讀完地藏經"]:
            self.assertFalse(selfstate.is_opinion_about_entry(q), q)

    def test_genuine_lookup_with_opinion_word_not_suppressed(self):
        # 🔴 對抗式審查 high 回歸：真查詢（動作/指代/列舉/範疇詞）即使夾帶看法詞（在意/好奇/一直提）也**不算純看法**
        # → 不誤擋（否則使用者明明要調資料卻拿不到）。
        for q in ["查我很在意的那批記寫", "找我一直提的那條記寫", "我很在意的主題有哪些",
                  "你整理過的歷程裡我最好奇的那些", "列出我特別在意的那些記寫"]:
            self.assertFalse(selfstate.is_opinion_about_entry(q), q)

    def test_opinion_about_content_still_suppressed(self):
        # 🔴 對抗式審查 med 回歸：「內容/進度」不算檢索詞（太常是看法受詞）→ 問看法即使提到內容仍收證據閘
        for q in ["你怎麼看這則記寫的內容", "你為什麼這麼在意這則記寫的內容", "這則記寫的內容有什麼特別"]:
            self.assertTrue(selfstate.is_opinion_about_entry(q), q)


class AllowEvidenceTest(unittest.TestCase):
    def _run(self, text, self_topic_ts=0, evidence_gate=True, opinion_suppress=True):
        """跑 handle_message、把這句逼進 fact_or_chat 的 coach.ask，回攔到的 evidence_tools。"""
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        s.self_topic_ts = self_topic_ts
        cap = {}

        def fake_ask(q, brief, ctx, hist, mood_hint="", self_presence=False, now_ts=None, evidence_tools=True, **_):
            cap["evidence_tools"] = evidence_tools
            return ("chat", None, "（一般對話）")

        coach = SimpleNamespace(enabled=True, ask=fake_ask,
                                meter=SimpleNamespace(record=lambda *a, **k: None),
                                reply=lambda *a, **k: "（不該走 reply）")
        client = SimpleNamespace(sent=[], dry_run=False,
                                 send=lambda t: client.sent.append(t) or True, send_typing=lambda: None)
        cfg = SimpleNamespace(dry_run=False, telegram_chat_id="", mood_gain=1.0,
                              evidence_gate_enabled=evidence_gate,
                              evidence_opinion_suppress=opinion_suppress,   # 🚪 §0.56 看法問句收證據閘
                              anti_repeat_enabled=False,   # 隔離：本測只驗證據閘、不驗防重複
                              spontaneity_enabled=False,   # 隔離：別讓 self_spontaneity 先接走（單測閘本身）
                              thread_sticky_enabled=False)
        update = {"message": {"chat": {"id": 1}, "text": text, "date": NOW_TS}}
        with mock.patch("telegram_monitor.coach.build_memory_brief", return_value=""):
            monitor.handle_message(update, coach, None, {"meta": {}, "records": []}, object(),
                                   s, client, cfg, None)
        return cap

    # ── 非記寫問句 → 擋出證據工具（根因 1，順帶兜底 2/4）──────────────────────
    def test_conversation_event_question_blocks_evidence(self):
        cap = self._run("剛剛有人指責你")
        self.assertIn("evidence_tools", cap)
        self.assertFalse(cap["evidence_tools"])     # 對話事件、非查記寫 → 不給證據工具

    def test_meta_spontaneity_question_blocks_evidence_when_spont_off(self):
        # spontaneity 關時這句漏到 fact_or_chat → 閘仍兜底擋掉證據工具（不列 📂）
        cap = self._run("你怎麼還沒開始分享聯想")
        self.assertFalse(cap["evidence_tools"])

    def test_bot_directed_event_question_blocks_evidence(self):
        cap = self._run("你剛剛是不是在生氣")   # 指向 bot 的對話事件（非記寫）→ 落 fact_or_chat 仍不可達證據工具
        self.assertIn("evidence_tools", cap)
        self.assertFalse(cap["evidence_tools"])

    # ── 真資料問句 → 仍給完整工具表（最高守護）────────────────────────────
    def test_explicit_list_question_allows_evidence(self):
        cap = self._run("列出上週的記寫")
        self.assertTrue(cap["evidence_tools"])

    def test_count_question_allows_evidence(self):
        cap = self._run("我寫了幾筆")
        self.assertTrue(cap["evidence_tools"])

    def test_oral_records_intent_allows_evidence(self):
        cap = self._run("查我那段記寫")           # 動作＋記寫指涉（is_explicit_records_intent）
        self.assertTrue(cap["evidence_tools"])

    # ── 🔴 high issue 回歸：對話脈絡指代/時間範圍的真資料問句仍可達工具 ──────────
    def test_anaphoric_data_recall_allows_evidence(self):
        # 「剛剛那批呢／昨天那些呢／靈感那條後來呢／我前天記的東西」＝靠對話脈絡指代的記寫回想 → 仍要拿得到工具，
        # 否則被誤擋成純聊天（誤答/捏造風險）。這正是審查抓到、原設計會沉默回歸的一類。
        for q in ["剛剛那批呢", "昨天那些呢", "靈感那條後來呢", "我前天記的東西", "上週的呢"]:
            cap = self._run(q, self_topic_ts=NOW_TS - 9999)   # 窗外、純資料回想
            self.assertTrue(cap["evidence_tools"], q)

    # ── 🔴 對抗式驗證抓到的回歸：記-動詞真資料問句不可被誤擋 ──────────────────
    def test_ji_verb_data_questions_allow_evidence(self):
        # 「記了/記什麼/記過/記的」＝「寫了…」同義；bot 歡迎詞就寫「問某段時間記了什麼」，這類真資料問句必須仍走工具。
        for q in ["我記了什麼", "最近記了什麼", "某段時間記了什麼", "今天記了什麼", "這段時間記了什麼"]:
            cap = self._run(q, self_topic_ts=NOW_TS - 9999)
            self.assertTrue(cap["evidence_tools"], q)

    def test_remember_phrasing_not_mistaken_for_data(self):
        # 「記得」＝對話（你記得我嗎），不是查記寫 → 不該因加了記-動詞詞而誤判成資料問句
        for q in ["你記得我嗎", "我記得那件事"]:
            self.assertFalse(selfstate.looks_like_data_question(q), q)

    # ── 🚪 §0.56 看法/理由問句（即使含「記寫」cue）收證據閘、別倒整份 📂 ──────────────
    def test_opinion_about_entry_blocks_evidence(self):
        # 「你好像很執著這一則記寫」含「記寫」→ looks_like_data_question=True，但這是問看法/態度 → 收回證據閘
        cap = self._run("你好像很執著這一則記寫，看你已經提過很多次")
        self.assertFalse(cap["evidence_tools"])

    def test_opinion_burst_blocks_evidence(self):
        # 截圖合併串：執著/為什麼/特別嗎 皆看法、無資料請求 → 不列 📂
        cap = self._run("你好像很執著這一則記寫，看你已經提過很多次\n為什麼呢\n它很特別嗎")
        self.assertFalse(cap["evidence_tools"])

    def test_opinion_with_explicit_data_still_allows(self):
        # 明確要資料（寫了什麼/列出）即使也帶看法詞 → 仍給證據工具（不誤收）
        for q in ["這則記寫寫了什麼", "把讀誦經書的清單列出來", "我這個月記了幾筆"]:
            self.assertTrue(self._run(q)["evidence_tools"], q)

    def test_opinion_suppress_flag_off_reverts(self):
        # EVIDENCE_OPINION_SUPPRESS=0 → 看法問句含「記寫」又回到開證據閘（逐位元同舊）
        cap = self._run("你好像很執著這一則記寫，看你已經提過很多次", opinion_suppress=False)
        self.assertTrue(cap["evidence_tools"])

    def test_genuine_lookup_with_opinion_word_still_allows(self):
        # 🔴 審查 high 端到端：真查詢＋看法詞 → 仍給證據工具（不被 opinion-suppress 誤擋）
        for q in ["查我很在意的那批記寫", "我很在意的主題有哪些"]:
            self.assertTrue(self._run(q)["evidence_tools"], q)

    def test_master_gate_off_ignores_opinion_suppress(self):
        # 🔴 審查 med：EVIDENCE_GATE_ENABLED=0（一鍵退路）時，opinion-suppress 不得動 allow_evidence（恆 True、byte-identical）
        cap = self._run("你好像很執著這一則記寫，看你已經提過很多次", evidence_gate=False)
        self.assertTrue(cap["evidence_tools"])

    # ── byte-identical 退路 ──────────────────────────────────────────────
    def test_flag_off_is_byte_identical(self):
        # 旗標關 → 即使是該擋的非資料句也恆 True（逐位元同現狀＝永遠拿完整工具表）
        cap = self._run("剛剛有人指責你", evidence_gate=False)
        self.assertTrue(cap["evidence_tools"])


if __name__ == "__main__":
    unittest.main()
