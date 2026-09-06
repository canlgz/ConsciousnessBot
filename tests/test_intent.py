"""意向解析（intent.resolve）：單一入口、明確優先序——驗每個意向都認得出，且碰撞由優先序解
（不再靠 handle_message 的擺放順序隱性決定，例如含「為什麼/感覺」不再被別的意向劫走）。

「指什麼」已由 referent 單一化；這裡直接餵 Referent，只驗「要幹嘛」的判定與優先序。"""

import unittest

from telegram_monitor import intent, referent, selfstate


def _kind(text, ref=None):
    return intent.resolve(text, ref or referent.Referent()).kind


class IntentKindsTest(unittest.TestCase):
    def test_each_intent_is_recognised(self):
        self.assertEqual(_kind("你最近有改變嗎"), "self_change")
        self.assertEqual(_kind("你是誰"), "self_identity")
        self.assertEqual(_kind("你剛剛點了什麼表情"), "self_reaction_query")
        self.assertEqual(_kind("你這段日子是怎麼活過來的"), "self_experience")
        self.assertEqual(_kind("你會想要有自己的感覺嗎"), "self_reflect")
        self.assertEqual(_kind("你現在怎麼樣"), "self_state")
        self.assertEqual(_kind("晚安"), "greeting")          # 🕘 時間性問候先於 farewell（帶絕對時間感）
        self.assertEqual(_kind("早安"), "greeting")
        self.assertEqual(_kind("懂了，謝謝"), "farewell")     # 非時間性的收尾語仍走 farewell
        self.assertEqual(_kind("給我看庭院那張照片"), "attachment")
        self.assertEqual(_kind("現在幾點"), "clock")
        self.assertEqual(_kind("我們多久沒聊了"), "convo_time")
        self.assertEqual(_kind("幫我看今天寫了什麼"), "fact_or_chat")


class IntentPriorityTest(unittest.TestCase):
    """碰撞由優先序解（這條順序＝路由的單一真相）。"""

    def test_change_beats_state_even_with_feeling_word(self):
        # 含「感覺」但問的是『自己的變化』→ 蛻變，不被「感覺」劫持成「你現在怎樣」（§0.7）
        self.assertEqual(_kind("你有沒有感覺到自己的變化"), "self_change")

    def test_revisit_why_beats_selfstate_followup(self):
        # 「為什麼…」同時命中『自陳追問』與『為什麼想到那條』→ 後者（綁自發繞回的線）優先，不去重報主線 gate
        ref = referent.Referent(revisited="假日惠中寺行程", followup_open=True)
        got = intent.resolve("為什麼會想到假日惠中寺行程", ref)
        self.assertEqual(got.kind, "self_revisit_why")
        self.assertEqual(got.topic, "假日惠中寺行程")            # 指涉物（由 referent 解析）一併帶出

    def test_plain_why_after_selfreport_is_followup(self):
        # 沒有「想到那條」、但剛自陳過（followup_open）→ 一般「為什麼/哪一條」走自陳追問
        self.assertEqual(_kind("哪一條？為什麼", referent.Referent(followup_open=True)), "selfstate_followup")

    def test_followup_needs_open_window(self):
        # 沒剛自陳（followup_open=False）→ 同一句不算自陳追問，落回一般處理
        self.assertNotEqual(_kind("哪一條？為什麼"), "selfstate_followup")

    def test_promise_beats_state(self):
        # 「之後有感覺再跟我說」含「感覺」（會被當狀態），但託付要贏——否則被當場報現況
        self.assertEqual(_kind("等你之後真的有感覺，再跟我說"), "promise")

    def test_scheduled_promise_routes_and_beats_feeling(self):
        # 🤝 「等一下八點跟我打招呼」＝時間排程承諾（有鐘點＋動作詞、無感覺詞）→ scheduled_promise，排在 promise 之前
        self.assertEqual(_kind("你可以承諾我等一下八點的時候跟我打個招呼嗎"), "scheduled_promise")
        self.assertEqual(_kind("十分鐘後提醒我"), "scheduled_promise")
        # 含感覺詞的託付仍走 feeling promise（互斥：感覺優先）
        self.assertEqual(_kind("明天有想法跟我說"), "promise")

    def test_scheduled_promise_flag_off_falls_through(self):
        # 🤝 SCHEDULED_PROMISE_ENABLED=0 → 不偵測排程承諾、順流（八點打招呼落回一般處理＝不再是 scheduled_promise）
        from types import SimpleNamespace
        off = SimpleNamespace(scheduled_promise_enabled=False)
        self.assertNotEqual(intent.resolve("八點跟我打招呼", referent.Referent(), cfg=off).kind, "scheduled_promise")

    def test_promise_ledger_routes(self):
        # 🤝 整理承諾/還記得/你忘了/做了嗎 → promise_ledger（據帳本報帳）
        for q in ["整理一下你的承諾", "你的約定有哪些", "你還記得答應我什麼",
                  "你忘了答應我的事", "你答應我的事做了嗎"]:
            self.assertEqual(_kind(q), "promise_ledger", q)

    def test_ledger_beats_scheduled_for_past_question(self):
        # 🤝 過去質問『你忘了9點要道歉嗎』『你答應我9點道歉做了嗎』→ ledger 先攔，不被當成新排程承諾誤記
        self.assertEqual(_kind("你忘了9點要道歉嗎"), "promise_ledger")
        self.assertEqual(_kind("你答應我9點道歉做了嗎"), "promise_ledger")

    def test_ledger_does_not_hijack_concrete_intents(self):
        # 🤝 ledger 不劫持既有明確意向：新排程請求/feeling 託付/變化/身分 仍走原意向
        self.assertEqual(_kind("你可以承諾我等一下八點的時候跟我打個招呼嗎"), "scheduled_promise")
        self.assertEqual(_kind("明天有想法跟我說"), "promise")
        self.assertEqual(_kind("你最近有改變嗎"), "self_change")
        self.assertEqual(_kind("你是誰"), "self_identity")

    def test_promise_ledger_flag_off_falls_through(self):
        from types import SimpleNamespace
        off = SimpleNamespace(promise_ledger_enabled=False)
        self.assertNotEqual(intent.resolve("整理一下你的承諾", referent.Referent(), cfg=off).kind, "promise_ledger")

    def test_revisit_why_needs_a_revisited_topic(self):
        # 沒有自發繞回的線可綁 → 不誤判成 revisit_why
        self.assertNotEqual(_kind("為什麼會想到那條"), "self_revisit_why")

    def test_farewell_only_for_pure_closing(self):
        # 純收尾才優雅收場；「懂了」後又拋問題＝還在聊，不算收尾（避免把繼續的對話當道別）
        self.assertTrue(selfstate.is_farewell("晚安"))
        self.assertTrue(selfstate.is_farewell("懂了，謝謝"))
        self.assertFalse(selfstate.is_farewell("懂了，那另一個問題是什麼？"))
        self.assertNotEqual(_kind("懂了，那另一個問題是什麼？"), "farewell")

    def test_reflective_feeling_question_beats_state(self):
        # 「你會想要有自己的感覺嗎」含「感覺」會被貪婪抓成狀態問句→報現況；反思式要贏、走誠實反思（分清類別）
        self.assertEqual(_kind("你會想要有自己的感覺嗎"), "self_reflect")
        self.assertEqual(_kind("你想不想有自己的意志"), "self_reflect")
        self.assertEqual(_kind("你現在有什麼感覺"), "self_state")          # 對照：真的問現況仍是 state
        self.assertEqual(_kind("你是誰"), "self_identity")                # 身分仍贏過反思（更上位）


if __name__ == "__main__":
    unittest.main()
