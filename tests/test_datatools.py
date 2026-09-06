"""datatools 確定性工具 + dispatch + coach.ask 路由（mock Gemini）。"""

import unittest
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest import mock
from zoneinfo import ZoneInfo

from telegram_monitor import analyzer, datatools, coach

TZ = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 6, 15, 11, 41, tzinfo=timezone.utc)  # Taipei 19:41 週一


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _ctx(meter=None, state=None):
    data = {"meta": {}, "contexts": [], "journeys": [], "explorations": [], "records": [
        {"id": "a", "ts": iso(NOW - timedelta(days=73)), "type": "text", "topicLabel": "讀誦經書", "text": "每天讀誦"},
        {"id": "b", "ts": iso(NOW - timedelta(hours=2)), "type": "text", "topicLabel": "讀誦經書", "text": "今天的進度"},
        {"id": "c", "ts": iso(NOW - timedelta(days=3)), "type": "image", "topicLabel": "社區庭院景觀", "text": "花圃"},
        {"id": "d", "ts": iso(NOW - timedelta(days=1)), "type": "text", "topicLabel": "社區庭院景觀", "text": "Hoki"},
    ]}
    snap = analyzer.analyze(data, NOW, TZ)
    return datatools.ToolCtx(data, snap, TZ, NOW, meter=meter, state=state)


class ResolverTest(unittest.TestCase):
    def test_get_current_time(self):
        out = datatools.get_current_time(_ctx())
        self.assertIn("2026/06/15", out)
        self.assertIn("19:41", out)
        self.assertIn("前）", out)              # 相對「多久前」

    def test_list_topic_records_fuzzy_and_real_times(self):
        out = datatools.list_topic_records(_ctx(), topic="讀誦經")
        self.assertIn("讀誦經書", out)
        self.assertIn("共 2 筆", out)
        self.assertNotIn("社區", out)
        self.assertIn("前）", out)                            # 🕐 每筆都帶程式算好的相對時間（LLM 不必自己推日期）

    def test_ts_label_grounds_relative_time(self):
        # 相對時間由程式從真實 now 算（避免 LLM 拿絕對日期幻覺『三天前』）
        ctx = datatools.ToolCtx({}, SimpleNamespace(gaps=[]), TZ, NOW)
        ts = NOW - timedelta(days=1)
        label = datatools._ts_label(ctx, ts)
        self.assertIn("1 天前", label)
        self.assertNotIn("3 天前", label)

    def test_list_topic_earliest_only(self):
        out = datatools.list_topic_records(_ctx(), topic="讀誦經", earliest_only=True)
        self.assertIn("第一筆", out)
        self.assertIn("每天讀誦", out)
        self.assertNotIn("今天的進度", out)

    def test_list_topic_latest_only(self):
        # 「最近一次/最後一筆」＝距今最近那筆（2h 前），不是從第一筆倒出來
        out = datatools.list_topic_records(_ctx(), topic="讀誦經", latest_only=True)
        self.assertIn("最近一筆", out)
        self.assertIn("今天的進度", out)            # 2h 前＝最近
        self.assertNotIn("每天讀誦", out)           # 73 天前＝最舊，不該出現

    def test_list_topic_by_category_not_topic(self):
        # 「靈感那幾則」＝靈感是個**大類**（非議題）→ 列那大類底下各議題的實際內容，不再「找不到主題」
        data = {"meta": {}, "contexts": [], "journeys": [], "records": [
            {"id": "x", "ts": iso(NOW - timedelta(days=1)), "type": "text", "category": "靈感",
             "topicLabel": "資訊基生生命觀", "text": "把生命看成資訊自我維持"},
            {"id": "y", "ts": iso(NOW - timedelta(hours=2)), "type": "text", "category": "靈感",
             "topicLabel": "資訊基生生命觀", "text": "混沌與湧現"},
            {"id": "z", "ts": iso(NOW), "type": "text", "category": "研究", "topicLabel": "RAG", "text": "向量檢索"}]}
        snap = analyzer.analyze(data, NOW, TZ)
        state = SimpleNamespace(focus=None)
        out = datatools.list_topic_records(datatools.ToolCtx(data, snap, TZ, NOW, state=state), topic="靈感")
        self.assertNotIn("找不到", out)
        self.assertIn("資訊基生生命觀", out)               # 大類底下的議題
        self.assertIn("混沌與湧現", out)                    # 實際內容（不只名字）
        self.assertNotIn("向量檢索", out)                   # 別的大類不混進來
        self.assertEqual(state.focus["topic"], "資訊基生生命觀")   # 焦點綁到大類主議題，接得住下一句

    def test_best_category_and_topic_recent_texts(self):
        recs = [{"category": "靈感", "topicLabel": "X", "type": "text", "ts": iso(NOW - timedelta(days=1)), "text": "甲"},
                {"category": "靈感", "topicLabel": "X", "type": "text", "ts": iso(NOW), "text": "乙"},
                {"category": "生活", "topicLabel": "Y", "type": "text", "ts": iso(NOW), "text": "丙"}]
        self.assertEqual(datatools.best_category("靈感那幾則", recs), "靈感")
        self.assertIsNone(datatools.best_category("毫不相干", recs))
        self.assertEqual(datatools.topic_recent_texts(recs, "X"), ["甲", "乙"])   # 只該議題、由舊到新

    def test_list_funnel_topics_lists_titles_not_numbers(self):
        # 「零散的念頭/快成形/學習歷程」→ 列出實際主題標題清單（不是漏斗數字）
        data = {"meta": {}, "records": [], "explorations": [],
                "journeys": [{"id": "j1", "contextId": "c3", "status": "journey"}],
                "contexts": [
                    {"id": "c1", "status": "candidate", "label": "冰箱食材採購"},
                    {"id": "c2", "status": "candidate", "label": "浴室水管維修"},
                    {"id": "c3", "status": "journey", "label": "研發 writetolearn 日誌"},
                    {"id": "c4", "status": "context", "label": "社區庭院景觀"}]}
        ctx = datatools.ToolCtx(data, SimpleNamespace(gaps=[]), TZ, NOW)
        scattered = datatools.list_funnel_topics(ctx, stage="scattered")
        self.assertIn("冰箱食材採購", scattered)
        self.assertIn("浴室水管維修", scattered)
        self.assertNotIn("社區庭院景觀", scattered)        # 那是 context、不是零散
        self.assertNotIn("研發", scattered)                # journey 也不是零散
        self.assertIn("研發 writetolearn 日誌", datatools.list_funnel_topics(ctx, stage="journey"))
        self.assertIn("社區庭院景觀", datatools.list_funnel_topics(ctx, stage="context"))

    def test_list_funnel_topics_stuck_uses_gaps(self):
        snap = SimpleNamespace(gaps=[{"line": "🌿 〈研發 日誌〉只差：補一張圖"}])
        ctx = datatools.ToolCtx({"contexts": [], "journeys": []}, snap, TZ, NOW)
        out = datatools.list_funnel_topics(ctx, stage="stuck")
        self.assertIn("補一張圖", out)

    def test_recent_filings_groups_by_category_topic(self):
        snap = SimpleNamespace(filed_records=[
            {"id": "a", "ts": NOW, "category": "生活", "topicLabel": "冰箱食材採購", "key": "生活|冰箱食材採購", "status": "candidate"},
            {"id": "b", "ts": NOW, "category": "生活", "topicLabel": "冰箱食材採購", "key": "生活|冰箱食材採購", "status": "candidate"},
            {"id": "c", "ts": NOW, "category": None, "topicLabel": "研發 writetolearn 日誌", "key": "|研發 writetolearn 日誌", "status": "journey"}])
        out = datatools.recent_filings(datatools.ToolCtx({}, snap, TZ, NOW))
        self.assertIn("冰箱食材採購", out)
        self.assertIn("×2", out)                       # 同 大類｜議題 計數
        self.assertIn("研發 writetolearn 日誌", out)

    def test_recent_filings_empty_is_honest(self):
        out = datatools.recent_filings(datatools.ToolCtx({}, SimpleNamespace(filed_records=[]), TZ, NOW))
        self.assertIn("還沒有", out)

    def test_topic_time_spans_longest(self):
        out = datatools.topic_time_spans(_ctx(), order="longest")
        self.assertIn("〈讀誦經書〉", out.splitlines()[0])   # 73 天跨度最大

    def test_topic_span_single_topic(self):
        # 〔讀誦經書〕第一筆（73 天前）→ 最後一筆（2h 前）＝橫跨 72 整天、共 2 筆
        out = datatools.topic_span(_ctx(), topic="讀誦經")
        self.assertIn("讀誦經書", out)
        self.assertIn("橫跨 72 天", out)
        self.assertIn("共 2 筆", out)
        self.assertIn("第一筆距今", out)

    def test_topic_span_all_records(self):
        out = datatools.topic_span(_ctx())          # 留空＝全部記寫
        self.assertIn("全部記寫", out)
        self.assertIn("橫跨 72 天", out)            # 最舊 73 天前 → 最新 2h 前
        self.assertIn("共 4 筆", out)

    def test_topic_span_single_record_no_span(self):
        data = {"meta": {}, "contexts": [], "journeys": [], "records": [
            {"id": "a", "ts": iso(NOW - timedelta(days=2)), "type": "text", "topicLabel": "孤線", "text": "只有一筆"}]}
        snap = analyzer.analyze(data, NOW, TZ)
        out = datatools.topic_span(datatools.ToolCtx(data, snap, TZ, NOW), topic="孤線")
        self.assertIn("只有 1 筆", out)
        self.assertIn("還談不上跨度", out)

    def test_topic_span_unknown_topic(self):
        self.assertIn("找不到", datatools.topic_span(_ctx(), topic="毫不相干的主題"))

    def test_topic_span_binds_focus(self):
        state = SimpleNamespace(focus=None)
        datatools.topic_span(_ctx(state=state), topic="讀誦經")
        self.assertEqual(state.focus["topic"], "讀誦經書")     # 焦點綁住，下一句「那條線」接得回

    def test_days_since_month_day(self):
        # NOW=2026/06/15；6/13 → 2 天
        out = datatools.days_since(_ctx(), date="6/13")
        self.assertIn("2026/06/13", out)
        self.assertIn("是 2 天", out)

    def test_days_since_full_date_and_chinese(self):
        self.assertIn("是 14 天", datatools.days_since(_ctx(), date="2026/06/01"))
        self.assertIn("是 2 天", datatools.days_since(_ctx(), date="6月13日"))

    def test_days_since_today(self):
        self.assertIn("就是今天", datatools.days_since(_ctx(), date="6/15"))

    def test_days_since_future_md_rolls_back_a_year(self):
        # 12/25 落在 6/15 之後 → 視為去年同日（往回算才合理）
        self.assertIn("2025/12/25", datatools.days_since(_ctx(), date="12/25"))

    def test_days_since_unparseable_is_honest(self):
        self.assertIn("看不懂", datatools.days_since(_ctx(), date="前陣子"))

    def test_span_tools_dispatch_wired(self):
        self.assertIn("橫跨", datatools.dispatch("topic_span", {"topic": "讀誦經"}, _ctx()))
        self.assertIn("是 2 天", datatools.dispatch("days_since", {"date": "6/13"}, _ctx()))
        self.assertIn("topic_span", datatools.EVIDENCE_TOOLS)        # 引述記寫 → 接一句人話
        self.assertNotIn("days_since", datatools.EVIDENCE_TOOLS)     # 純日期計算 → 只回數、不接人話

    def test_records_in_time_range(self):
        out = datatools.records_in_time_range(_ctx(), range="今天")
        self.assertIn("今天", out)
        self.assertIn("今天的進度", out)        # 2h 前那筆在今天

    def test_records_in_time_range_just_now_is_last_session(self):
        # 「剛剛」抓最近一段連續記寫，不是整天——最近一筆是 2h 前那則，自成一段
        out = datatools.records_in_time_range(_ctx(), range="剛剛")
        self.assertIn("剛剛這段", out)
        self.assertIn("今天的進度", out)

    def test_just_now_when_nothing_recent_says_so(self):
        # 問「剛剛」但最近一批其實是昨晚 → 誠實點破、不把昨晚的當「剛剛」回（截圖時間感不準的根治）
        old = datetime(2026, 6, 14, 13, 38, tzinfo=timezone.utc)   # 台北 06/14 21:38（昨晚）
        data = {"meta": {}, "contexts": [], "journeys": [], "records": [
            {"id": "z", "ts": iso(old), "type": "text", "topicLabel": "研發", "text": "昨晚那筆"}]}
        snap = analyzer.analyze(data, NOW, TZ)
        out = datatools.records_in_time_range(datatools.ToolCtx(data, snap, TZ, NOW), range="剛剛")
        self.assertIn("其實沒有剛落進來", out)        # 點破：剛剛其實沒有
        self.assertNotIn("剛剛這段", out)             # 不再把昨晚那批叫剛剛
        self.assertIn("昨晚", out)

    def test_anaphoric_time_falls_back_to_last_writing(self):
        # 「那時候」沒有絕對錨、也沒對話錨 → 接「最後一次記寫那段」，不再回「看不懂」
        out = datatools.records_in_time_range(_ctx(), range="那時候")
        self.assertNotIn("看不懂", out)
        self.assertIn("最後一次記寫那段", out)
        self.assertIn("今天的進度", out)               # 最後一筆（2h 前）那段

    def test_fresh_deixis_is_recent_session(self):
        # 「新東西是什麼」＝最近一段新記寫（接 recent_session），不再回「看不懂」
        out = datatools.records_in_time_range(_ctx(), range="新東西是什麼")
        self.assertNotIn("看不懂", out)
        self.assertIn("剛剛這段", out)
        self.assertIn("今天的進度", out)

    def test_deictic_topic_binds_to_focus(self):
        # 「那條線」＝對話焦點剛 surface 的主線 → 綁回去列那條
        state = SimpleNamespace(focus={"topic": "讀誦經書"}, last_range=None)
        out = datatools.list_topic_records(_ctx(state=state), topic="那條線")
        self.assertIn("讀誦經書", out)
        self.assertIn("共 2 筆", out)
        self.assertEqual(state.focus["topic"], "讀誦經書")     # 記住焦點供下一句

    def test_deictic_topic_without_focus_is_graceful(self):
        out = datatools.list_topic_records(_ctx(), topic="那條線")   # state=None、無焦點
        self.assertIn("找不到", out)                                 # 不崩、退「找不到」

    def test_anaphoric_time_uses_conversation_anchor(self):
        # 先問「今天」（確立時間錨）→ 再問「那時候」應接回「今天」那段，而非最後一次記寫
        state = SimpleNamespace(last_range=None)
        datatools.records_in_time_range(_ctx(state=state), range="今天")
        self.assertIsNotNone(state.last_range)
        out = datatools.records_in_time_range(_ctx(state=state), range="那個時候")
        self.assertNotIn("看不懂", out)
        self.assertIn("今天", out)
        self.assertNotIn("每天讀誦", out)        # 73 天前，不在「剛剛」

    def test_dispatch_unknown_returns_none(self):
        self.assertIsNone(datatools.dispatch("no_such_tool", {}, _ctx()))

    def test_api_cost_with_meter(self):
        from telegram_monitor.cost import CostMeter
        m = CostMeter(1.0, 1.0, 32.0, 10, 10, 30)
        out = datatools.api_cost(_ctx(meter=m, state=SimpleNamespace(
            cost_since_digest_usd=0.1, cost_total_usd=0.1, cost_month_usd=0.1, cost_month_key="2026-06")))
        self.assertIn("NT$3.2", out)            # 0.1 USD × 32（累計/今天/本月）
        self.assertIn("US$0.10", out)


class CoachAskRoutingTest(unittest.TestCase):
    def _coach(self):
        return coach.Coach(SimpleNamespace(gemini_api_key="k", gemini_model="gemini-2.5-flash"))

    def test_fact_route_data_then_voice(self):
        c = self._coach()
        with mock.patch("telegram_monitor.gemini.generate_with_tools",
                        return_value={"function_call": {"name": "topic_time_spans", "args": {"order": "longest"}},
                                      "text": None}), \
             mock.patch("telegram_monitor.gemini.generate", return_value="你又繞回這條了喔"):
            kind, data, voice = c.ask("哪個主題最久", "brief", _ctx(), [])
        self.assertEqual(kind, "fact")
        self.assertIn("讀誦經書", data)            # 第一則：真實資料
        self.assertEqual(voice, "你又繞回這條了喔")   # 第二則：緊接的人話

    def test_pure_lookup_has_no_voice(self):
        c = self._coach()
        # get_current_time 不算「引述記寫證據」→ 只回資料、不接人話（不會呼叫 generate）
        with mock.patch("telegram_monitor.gemini.generate_with_tools",
                        return_value={"function_call": {"name": "get_current_time", "args": {}}, "text": None}):
            kind, data, voice = c.ask("現在幾點", "brief", _ctx(), [])
        self.assertEqual(kind, "fact")
        self.assertIsNone(voice)

    def test_chat_route_returns_voice(self):
        c = self._coach()
        with mock.patch("telegram_monitor.gemini.generate_with_tools",
                        return_value={"function_call": None, "text": "你今天想聊什麼"}):
            kind, data, voice = c.ask("嗨", "brief", _ctx(), [])
        self.assertEqual(kind, "chat")
        self.assertIsNone(data)
        self.assertEqual(voice, "你今天想聊什麼")

    def test_evidence_tools_default_includes_all(self):
        # 🚪 不傳 evidence_tools（預設 True）→ decls == 完整 TOOL_DECLS（逐位元同現狀）
        c = self._coach()
        cap = {}

        def grab(api, model, system, contents, decls, on_usage=None):
            cap["decls"] = decls
            return {"function_call": None, "text": "好"}

        with mock.patch("telegram_monitor.gemini.generate_with_tools", side_effect=grab):
            c.ask("嗨", "brief", _ctx(), [])
        self.assertIs(cap["decls"], datatools.TOOL_DECLS)         # 同一物件＝逐位元同現狀
        names = {d["name"] for d in cap["decls"]}
        self.assertIn("records_in_time_range", names)             # 證據工具在桌上（同現狀）

    def _coach_legacy(self):
        # 🚪 §0.37 退路：旗標關（nonevidence_empty_tools_enabled=False）→ evidence_tools=False 仍走 [days_since,api_cost] 非證據表
        return coach.Coach(SimpleNamespace(gemini_api_key="k", gemini_model="gemini-2.5-flash",
                                           nonevidence_empty_tools_enabled=False))

    def test_evidence_tools_off_empty_pure_chat(self):
        # 🚪 旗標開（預設）：evidence_tools=False → 走 generate_chat（空工具表＝LLM 純對話裁決），
        #    **不**呼叫 generate_with_tools（不 dispatch days_since、不吐「就是今天」日期）。
        c = self._coach()
        gwt = mock.Mock(return_value={"function_call": {"name": "days_since", "args": {}}, "text": None})
        with mock.patch("telegram_monitor.gemini.generate_with_tools", gwt), \
             mock.patch("telegram_monitor.gemini.generate_chat", return_value="喂，我在啊，怎麼了？") as gc:
            kind, data, voice = c.ask("喂！", "brief", _ctx(), [], evidence_tools=False)
        self.assertEqual(kind, "chat")                            # 純對話裁決
        self.assertIsNone(data)
        self.assertIn("我在", voice)                              # 接住前後文，而非吐日期
        self.assertEqual(gwt.call_count, 0)                       # 一次都沒呼叫 function-calling（不抓 days_since）
        self.assertTrue(gc.called)                                # 走純對話路徑

    def test_evidence_tools_off_legacy_flag_keeps_records(self):
        # 🚪 退路：旗標關 → evidence_tools=False 仍走 function-calling 的 [days_since,api_cost] 非證據表（§0.37 逐位元同現狀）
        c = self._coach_legacy()
        cap = {}

        def grab(api, model, system, contents, decls, on_usage=None):
            cap["decls"] = decls
            return {"function_call": None, "text": "我們聊聊"}

        with mock.patch("telegram_monitor.gemini.generate_with_tools", side_effect=grab):
            c.ask("剛剛有人指責你", "brief", _ctx(), [], evidence_tools=False)
        names = {d["name"] for d in cap["decls"]}
        self.assertFalse(names & datatools.EVIDENCE_TOOLS, names)  # 一個證據工具都不在
        self.assertEqual(names, {"days_since", "api_cost"})        # §0.37 非證據表退路


if __name__ == "__main__":
    unittest.main()
