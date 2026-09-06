"""🎴 §0.93（路由補強，與貼圖視覺 §0.93 互補）：被問「你看得到貼圖內容嗎」要**誠實作答**，不是又送一張逃避。

截圖：使用者問「你能看到你剛剛傳給我的貼圖內容？」，bot 卻**又送一張貼圖**。
根因（實跑確認）：這句含「傳給我…貼圖」→ `is_sticker_send_request=True` → `_maybe_sticker_send` 又送一張，
那個「你看得到內容嗎」的**能力 meta 問句**根本沒被回答——即使貼圖視覺（收到即讀圖、記描述）已能真的看圖，
也因這條**錯路由**而白費（訊息被送貼圖路徑攔截、到不了帶畫面描述的接地回覆）。

修（旗標 STICKER_PERCEIVE_Q 預設開、關＝逐位元同現狀）：
- selfstate.asks_can_perceive_sticker：感知動詞（看得到/看得懂/知道…）＋貼圖，或 貼圖＋（是什麼意思/內容…）。
- is_sticker_send_request 早退守門：這類 meta 問句不當送請求。
- _sent_sticker_ground_hint 觸發條件納入 asks_can_perceive_sticker → 問句直達誠實接地（有畫面描述就談畫面）。
"""

import os
import unittest
from types import SimpleNamespace

from telegram_monitor import selfstate, monitor, intent, config


class _Ref:
    followup_open = False

    def __getattr__(self, k):
        return None


class PerceiveDetectTest(unittest.TestCase):
    def test_detected(self):
        for q in ["你能看到你剛剛傳給我的貼圖內容？", "你看得懂那張貼圖嗎", "你知道你送的貼圖是什麼意思嗎",
                  "你能理解貼圖的內容嗎", "那張貼圖是什麼意思", "貼圖代表什麼", "你看得到貼圖的內容嗎"]:
            self.assertTrue(selfstate.asks_can_perceive_sticker(q), q)

    def test_not_detected(self):
        for q in ["傳一張貼圖給我", "送我一張開心的貼圖", "今天天氣如何", "你喜歡這張嗎"]:
            self.assertFalse(selfstate.asks_can_perceive_sticker(q), q)

    def test_neg_teach_guarded(self):
        # 🔍 §0.93 審查（LOW）：與姊妹偵測一致——教學（我教你這張的意思）／送-否定（別再問貼圖…）不算能力問句
        for q in ["我教你這張貼圖的意思", "別再問貼圖是什麼意思", "以後這種貼圖是什麼意思都記住"]:
            self.assertFalse(selfstate.asks_can_perceive_sticker(q), q)


class RouteNotSendTest(unittest.TestCase):
    def test_perception_question_not_send_request(self):
        for q in ["你能看到你剛剛傳給我的貼圖內容？", "你看得懂那張貼圖嗎", "你知道你送的貼圖是什麼意思嗎",
                  "你能理解貼圖的內容嗎"]:
            self.assertFalse(selfstate.is_sticker_send_request(q), q)

    def test_legit_send_still_works(self):
        for q in ["傳一張開心的貼圖給我", "送我一張貼圖", "再傳一張貼圖", "給我一張可愛的貼圖",
                  "你看看能不能傳一張貼圖給我", "傳張貼圖給我看看"]:
            self.assertTrue(selfstate.is_sticker_send_request(q), q)

    def test_flag_off_restores_old_routing(self):
        os.environ["STICKER_PERCEIVE_Q"] = "0"
        try:
            self.assertTrue(selfstate.is_sticker_send_request("你能看到你剛剛傳給我的貼圖內容？"))
        finally:
            os.environ.pop("STICKER_PERCEIVE_Q", None)


class RouteReachesGroundingTest(unittest.TestCase):
    """🔍 §0.93 審查（HIGH）：光讓 is_sticker_send_request 放行還不夠——「你能看到你剛剛傳給我的貼圖內容？」含「傳給我」
    的『給』會被 _OWN_REACT_RE 誤配成 self_reaction_query（handler 硬編、不吃 mhint → §0.90/§0.93 接地被丟棄、答非所問）。
    須讓路到 fact_or_chat（吃 mhint、由接地誠實作答）。原 isolation 測試漏了這條 dispatch gap。"""

    def _route(self, t):
        return intent.resolve(t, _Ref(), None, cfg=config.Config.load()).kind

    def test_perception_question_routes_to_fact_or_chat_not_reaction(self):
        for q in ["你能看到你剛剛傳給我的貼圖內容？", "你看得懂剛剛傳給我的貼圖嗎"]:
            self.assertEqual(self._route(q), "fact_or_chat", q)   # 非 self_reaction_query

    def test_genuine_reaction_question_still_reaction(self):
        # 真的問 bot 點的 emoji reaction → 仍 self_reaction_query（守門不誤傷）
        self.assertEqual(self._route("你剛剛對我點了什麼表情"), "self_reaction_query")


class GroundComposesWithVisionTest(unittest.TestCase):
    """路由補強 → 問句直達接地；接地本身（貼圖視覺 §0.93）帶畫面描述時據實談畫面。"""

    def _cfg(self):
        return SimpleNamespace(sent_sticker_ground_enabled=True)

    def test_perception_question_reaches_ground_with_vision_desc(self):
        st = SimpleNamespace(last_sticker_ts=1000.0, last_sticker_emoji="🥰",
                             last_sticker_desc="一隻藍色的魚，眼睛是兩顆愛心", known_sticker_ids=[])
        h = monitor._sent_sticker_ground_hint(st, self._cfg(), "你能看到你剛剛傳給我的貼圖內容？", 1100.0)
        self.assertTrue(h)
        self.assertIn("真的看過", h)              # 有畫面描述→據實談畫面
        self.assertIn("藍色的魚", h)

    def test_no_desc_falls_back_to_emoji_tag(self):
        st = SimpleNamespace(last_sticker_ts=1000.0, last_sticker_emoji="😬",
                             last_sticker_desc="", known_sticker_ids=[])
        h = monitor._sent_sticker_ground_hint(st, self._cfg(), "你看得懂那張貼圖嗎", 1100.0)
        self.assertTrue(h)
        self.assertIn("😬", h)                    # 沒畫面→退回情緒標記

    def test_no_fire_if_never_sent(self):
        st = SimpleNamespace(last_sticker_ts=0, last_sticker_emoji=None, last_sticker_desc=None, known_sticker_ids=[])
        self.assertEqual(monitor._sent_sticker_ground_hint(st, self._cfg(), "你看得懂貼圖嗎", 1100.0), "")


if __name__ == "__main__":
    unittest.main()
