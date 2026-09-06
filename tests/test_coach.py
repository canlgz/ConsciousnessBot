"""coach.build_memory_brief 純函式測試（不碰網路/Gemini）。"""

import unittest
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest import mock
from zoneinfo import ZoneInfo

from telegram_monitor import analyzer, coach, persona

TZ = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


class MemoryBriefTest(unittest.TestCase):
    def _brief(self):
        data = {
            "meta": {},
            "contexts": [{"id": "c1", "status": "context", "category": "教學",
                          "label": "形成性評量", "recordIds": ["r1"], "lastTs": iso(NOW),
                          "criteria": {"returnVisits": 4, "mediaKinds": 2}}],
            "journeys": [{"id": "j1", "contextId": "c1", "status": "journey",
                          "title": "課堂評量設計", "summary": "用提問觀察理解",
                          "markers": [{"type": "後設反思"}]}],
            "explorations": [],
            "records": [{"id": "r1", "ts": iso(NOW - timedelta(minutes=30)), "type": "text",
                         "category": "教學", "topicLabel": "形成性評量",
                         "text": "今天試了用即時提問抓學生的理解缺口"}],
        }
        snap = analyzer.analyze(data, NOW, TZ)
        return coach.build_memory_brief(data, snap, TZ)

    def test_brief_injects_dialogue_focus(self):
        data = {"meta": {}, "contexts": [], "journeys": [], "explorations": [],
                "records": [{"id": "r1", "ts": iso(NOW), "type": "text", "topicLabel": "研發日誌", "text": "x"}]}
        snap = analyzer.analyze(data, NOW, TZ)
        b = coach.build_memory_brief(data, snap, TZ, now=NOW,
                                     focus={"topic": "研發日誌", "fresh": True, "range_label": "剛剛這段（…）"})
        self.assertIn("此刻對話焦點", b)
        self.assertIn("研發日誌", b)
        self.assertIn("新東西", b)            # fresh → 提示「新東西」指最近一段
        self.assertNotIn("此刻對話焦點", coach.build_memory_brief(data, snap, TZ, now=NOW))  # 無 focus → 不注入

    def test_brief_injects_selfacts_block(self):
        # C：把 bot 自己的近期狀態/動作（剛繞回的舊線等）打包進 brief，讓接前文的問法有所本。
        data = {"meta": {}, "contexts": [], "journeys": [], "explorations": [],
                "records": [{"id": "r1", "ts": iso(NOW), "type": "text", "text": "x"}]}
        snap = analyzer.analyze(data, NOW, TZ)
        b = coach.build_memory_brief(data, snap, TZ, now=NOW,
                                     selfacts="・我最近一次自己繞回想起的舊線是「假日惠中寺行程」。")
        self.assertIn("我此刻的狀態", b)
        self.assertIn("假日惠中寺行程", b)
        self.assertIn("為什麼會想到它", b)        # 提示 LLM：接前文的問法多半指上面這些
        self.assertNotIn("我此刻的狀態", coach.build_memory_brief(data, snap, TZ, now=NOW))  # 無 selfacts → 不注入

    def test_history_contents_merges_consecutive_same_role(self):
        c = coach.Coach(SimpleNamespace(gemini_api_key="", gemini_model="m"))
        hist = [{"role": "model", "text": "🫀 自陳A"}, {"role": "model", "text": "🫧 招呼B"},
                {"role": "user", "text": "問X"}]
        contents = c._history_contents("brief", hist)
        roles = [x["role"] for x in contents]
        self.assertTrue(all(roles[i] != roles[i + 1] for i in range(len(roles) - 1)))  # 角色交替、無連續
        self.assertIn("自陳A", contents[1]["parts"][0]["text"])
        self.assertIn("招呼B", contents[1]["parts"][0]["text"])   # 兩則連續 model 合併

    def test_brief_contains_grounding(self):
        b = self._brief()
        self.assertIn("形成性評量", b)        # 脈絡標題
        self.assertIn("課堂評量設計", b)        # 歷程標題
        self.assertIn("後設反思", b)            # 轉折標記
        self.assertIn("即時提問", b)            # 近期記寫原文
        self.assertIn("總筆數 1", b)            # 現況數字

    def test_brief_includes_links_and_time(self):
        # 連結記寫即使很舊，也要進「外部連結（全部）」區、且帶現在/最後記寫
        data = {
            "meta": {}, "contexts": [], "journeys": [], "explorations": [],
            "records": (
                [{"id": "L", "ts": iso(NOW - timedelta(days=40)), "type": "link",
                  "text": "看到這篇 https://example.com/rag 很讚",
                  "urlPreview": {"url": "https://example.com/rag", "title": "RAG 入門"}}]
                + [{"id": f"r{i}", "ts": iso(NOW - timedelta(minutes=i)), "type": "text",
                    "text": f"近期雜記 {i}"} for i in range(60)]
            ),
        }
        snap = analyzer.analyze(data, NOW, TZ)
        b = coach.build_memory_brief(data, snap, TZ, now=NOW)
        self.assertIn("外部連結", b)
        self.assertIn("https://example.com/rag", b)   # 舊連結仍被完整帶出
        self.assertIn("RAG 入門", b)
        self.assertIn("時間感", b)                      # 時間感區塊（含「距上次記寫」）給「多久沒寫」用
        self.assertIn("距上次記寫", b)

    def test_brief_bounded(self):
        # 長文本應被截斷、整體有上限
        long_data = {
            "meta": {}, "contexts": [], "journeys": [], "explorations": [],
            "records": [{"id": f"r{i}", "ts": iso(NOW - timedelta(minutes=i)),
                         "type": "text", "topicLabel": "x", "text": "字" * 500}
                        for i in range(200)],
        }
        snap = analyzer.analyze(long_data, NOW, TZ)
        b = coach.build_memory_brief(long_data, snap, TZ, max_chars=3000)
        self.assertLessEqual(len(b), 3100)

    def test_grounding_internal_note_unified(self):
        # 🛡️ grounding_note=True → **焦點／我此刻狀態**兩區塊尾端各附統一守則（內部用、別主動當話題）。
        # ⚠️ 會話節奏**不附**：它本就該主動承接（剛回來先自然接住＝久別承接），與「別主動當話題」相牴觸。
        from telegram_monitor import persona
        data = {"meta": {}, "contexts": [], "journeys": [], "explorations": [],
                "records": [{"id": "r1", "ts": iso(NOW), "type": "text", "topicLabel": "研發日誌", "text": "x"}]}
        snap = analyzer.analyze(data, NOW, TZ)
        common = dict(now=NOW, focus={"topic": "研發日誌", "fresh": True},
                      selfacts="・我最近一次自己繞回想起的舊線是「假日惠中寺行程」。",
                      session_struct="隔了三小時才回來")
        b_on = coach.build_memory_brief(data, snap, TZ, grounding_note=True, **common)
        # 三區塊都在；守則只掛在焦點/狀態（出現 2 次），會話節奏不掛
        self.assertIn("此刻對話焦點", b_on)
        self.assertIn("我此刻的狀態", b_on)
        self.assertIn("會話節奏", b_on)
        self.assertEqual(b_on.count(persona.GROUNDING_INTERNAL_NOTE), 2)
        # 會話節奏段仍保有「剛回來就先自然接住」（主動承接）的指引、且其尾端不接守則
        self.assertIn("剛回來就先自然接住", b_on)
        # 既有 assertIn 子串仍在（純加性、不替換）
        self.assertIn("別反問他指的是什麼", b_on)
        self.assertIn("為什麼會想到它", b_on)
        # 預設（不傳 grounding_note=False）→ 不含統一守則（byte-identical），但既有句仍在
        b_off = coach.build_memory_brief(data, snap, TZ, **common)
        self.assertNotIn(persona.GROUNDING_INTERNAL_NOTE, b_off)
        self.assertIn("別反問他指的是什麼", b_off)

    def test_grounding_note_does_not_break_bound(self):
        # 🛡️ 統一守則短句加入後仍不撐破 max_chars（截斷天然兜底）
        long_data = {
            "meta": {}, "contexts": [], "journeys": [], "explorations": [],
            "records": [{"id": f"r{i}", "ts": iso(NOW - timedelta(minutes=i)),
                         "type": "text", "topicLabel": "x", "text": "字" * 500}
                        for i in range(200)],
        }
        snap = analyzer.analyze(long_data, NOW, TZ)
        b = coach.build_memory_brief(long_data, snap, TZ, max_chars=3000, grounding_note=True,
                                     focus={"topic": "x"}, selfacts="・y", session_struct="z")
        self.assertLessEqual(len(b), 3100)


class AskFallbackGroundingTest(unittest.TestCase):
    """ask 的任何降級／重試都不得丟掉本輪 self presence 與額外接地。"""

    EXTRA = "【座標真實快照】V +0.35、A +0.36"

    def _coach(self):
        c = coach.Coach(SimpleNamespace(gemini_api_key="k", gemini_model="m"))
        c.reply = mock.Mock(return_value="仍然照事實回覆。")
        return c

    def _ask(self, c, evidence_tools=True):
        return c.ask("你現在的情緒座標如何？", "brief", {}, [],
                     mood_hint="tone", now_ts=1234.0, self_presence=True,
                     evidence_tools=evidence_tools, extra_system=self.EXTRA)

    def _assert_ground_preserved(self, c):
        c.reply.assert_called_once_with(
            "你現在的情緒座標如何？", "brief", [],
            mood_hint="tone", now_ts=1234.0,
            self_presence=True, extra_system=self.EXTRA)

    def test_pure_chat_failure_preserves_grounding(self):
        c = self._coach()
        with mock.patch("telegram_monitor.coach.gemini.generate_chat",
                        side_effect=coach.gemini.GeminiError("boom")):
            self._ask(c, evidence_tools=False)
        self._assert_ground_preserved(c)

    def test_tool_call_failure_preserves_grounding(self):
        c = self._coach()
        with mock.patch("telegram_monitor.coach.gemini.generate_with_tools",
                        side_effect=coach.gemini.GeminiError("boom")):
            self._ask(c)
        self._assert_ground_preserved(c)

    def test_echo_retry_preserves_grounding(self):
        c = self._coach()
        with mock.patch("telegram_monitor.coach.gemini.generate_with_tools",
                        return_value={"function_call": None, "text": "照搬你的話"}), \
             mock.patch("telegram_monitor.coach.echo.is_echo", return_value=True):
            self._ask(c)
        self._assert_ground_preserved(c)

    def test_empty_tool_response_preserves_grounding(self):
        c = self._coach()
        with mock.patch("telegram_monitor.coach.gemini.generate_with_tools",
                        return_value={"function_call": None, "text": ""}):
            self._ask(c)
        self._assert_ground_preserved(c)


class ReflectConnectTest(unittest.TestCase):
    """🔗 歸戶/摘要/事件反思也承接前文：剛剛還在對話 → 先接住前文脈絡再反思（不突兀跳成報事）。"""

    def test_reflection_user_weaves_connect_when_given(self):
        u = persona.reflection_user("filing", "事件描述", "記憶摘要", connect="〔承接前文〕剛剛還在聊 X")
        self.assertIn("〔承接前文〕剛剛還在聊 X", u)

    def test_reflection_user_clean_when_no_connect(self):
        u = persona.reflection_user("filing", "事件描述", "記憶摘要")
        self.assertNotIn("承接前文", u)

    def test_reflect_passes_connect_into_prompt(self):
        c = coach.Coach(SimpleNamespace(gemini_api_key="k", gemini_model="m"))
        cap = {}

        def fake_generate(api, model, system, user, **kw):
            cap["user"] = user
            return "（反思一句）"

        with mock.patch("telegram_monitor.gemini.generate", side_effect=fake_generate):
            out = c.reflect("filing", "事件", "brief", connect="〔承接前文〕對方最後說「在嗎」")
        self.assertEqual(out, "（反思一句）")
        self.assertIn("〔承接前文〕對方最後說「在嗎」", cap["user"])


class CleanVoiceTest(unittest.TestCase):
    def test_strips_void_prefix(self):
        self.assertEqual(coach._clean_voice("（無）\n好的，我們重新開始。"), "好的，我們重新開始。")
        self.assertEqual(coach._clean_voice("(無)\n---\n你好"), "你好")
        self.assertEqual(coach._clean_voice("無\n\n內容在這"), "內容在這")

    def test_keeps_normal(self):
        self.assertEqual(coach._clean_voice("正常一句話"), "正常一句話")

    def test_strips_leaked_time_tags_anywhere(self):
        # 截圖 bug：模型每段都掛〔剛剛〕（不只開頭）→ 整段都要洗，內文裡的「剛剛」不受影響
        out = coach._clean_voice("〔剛剛〕我剛剛那些聯想。\n\n〔剛剛〕但你這樣一說我才意識到。")
        self.assertNotIn("〔剛剛〕", out)
        self.assertIn("我剛剛那些聯想", out)
        self.assertIn("但你這樣一說", out)


class StripLeakedTimeTagsTest(unittest.TestCase):
    def test_strips_time_formats(self):
        for t in ("〔剛剛〕x", "〔3分前〕x", "〔約8小時前〕x", "〔約 8 小時前〕x", "〔昨天〕x", "〔5天前〕x"):
            self.assertEqual(coach.strip_leaked_time_tags(t), "x", t)

    def test_leaves_topic_brackets_and_plain_text(self):
        self.assertEqual(coach.strip_leaked_time_tags("〔讀誦經書〕共 2 筆"), "〔讀誦經書〕共 2 筆")
        self.assertEqual(coach.strip_leaked_time_tags("我昨天去了健身房"), "我昨天去了健身房")
        self.assertEqual(coach.strip_leaked_time_tags("剛surface的主線是〔靈感〕"), "剛surface的主線是〔靈感〕")

    def test_strips_bracketed_seconds(self):
        # 補「秒」單位（截圖）：括號式也涵蓋
        self.assertEqual(coach.strip_leaked_time_tags("〔約 18 秒前〕x"), "x")

    def test_strips_unbracketed_standalone_time_header_lines(self):
        # 🐛 截圖根因：模型把時間標籤改寫成**無括號的整行抬頭**（每段前一行「約 18 秒前」）→ 整行洗掉
        leak = ("約 18 秒前\n我會努力看看。\n\n約 18 秒前\n剛剛看著你的記寫，發現你圍繞著「家庭時光」寫了不少東西呢。"
                "\n\n約 18 秒前\n好像有把一些片段慢慢串起來的感覺。")
        out = coach.strip_leaked_time_tags(leak)
        self.assertNotIn("約 18 秒前", out)
        self.assertIn("我會努力看看", out)
        self.assertIn("串起來的感覺", out)
        for v in ("剛剛\nX", "18分前\nX", "約 3 小時前\nX", "昨天\nX", "前天\nX"):
            self.assertEqual(coach.strip_leaked_time_tags(v), "X", v)

    def test_keeps_midline_time_references(self):
        # 只吃『整行就是時間抬頭』：句中/括號內的正當時間不動（記寫清單時間戳、句中提到的相對時間）
        rec = "・2026/06/15 09:06 (16 天前) 今天應該是注重第一第二瓶"
        self.assertEqual(coach.strip_leaked_time_tags(rec), rec)
        for k in ("我約 18 秒前就在想這件事了", "這是三天前的事", "約 40 分鐘前你提過——所以我記著"):
            self.assertEqual(coach.strip_leaked_time_tags(k), k, k)

    def test_clean_voice_strips_screenshot_leak_end_to_end(self):
        leak = "約 18 秒前\n我會努力看看。\n\n約 18 秒前\n好像有把一些片段慢慢串起來的感覺。"
        out = coach._clean_voice(leak)
        self.assertNotIn("秒前", out)
        self.assertIn("我會努力看看", out)


class ShapedTest(unittest.TestCase):
    """🗜️ _shaped：洗淨＋依本輪複雜度收斂到 N 個完整句（短但完整、永不半句）。"""

    def _coach(self):
        return coach.Coach(SimpleNamespace(gemini_api_key="", gemini_model="m"))

    def test_shaped_trims_to_turn_length(self):
        c = self._coach()
        c._turn_length = 0                                   # level 0 → 2 句
        self.assertEqual(c._shaped("一句。二句。三句。四句。"), "一句。二句。")

    def test_shaped_none_turn_length_no_trim(self):
        c = self._coach()
        c._turn_length = None
        self.assertEqual(c._shaped("一句。二句。三句。"), "一句。二句。三句。")

    def test_shaped_also_cleans(self):
        c = self._coach()
        c._turn_length = None
        self.assertEqual(c._shaped("（無）\n好的。"), "好的。")

    def test_shaped_floor_keeps_multipart_complete(self):
        # 深層自我說明（機制/意識/現象）天生多面：floor 取 max(本輪上限, floor)，
        # 避免『預告兩種卻只講完一種』被句數上限砍成半截（截圖根因）。
        c = self._coach()
        c._turn_length = 2                                    # level 2 → 一般句數上限 5
        text = "一。二。三。四。五。六。七。"
        self.assertEqual(c._shaped(text), "一。二。三。四。五。")            # 無 floor＝上限 5
        self.assertEqual(c._shaped(text, floor=6), "一。二。三。四。五。六。")  # floor 提高到 6
        c._turn_length = None                                 # 不收斂時 floor 不強加截斷
        self.assertEqual(c._shaped(text, floor=6), text)


if __name__ == "__main__":
    unittest.main()
