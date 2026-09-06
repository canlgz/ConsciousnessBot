# -*- coding: utf-8 -*-
"""📊 §2.23 習慣盤點問句的專屬出口：「你觀察到我有哪些習慣」不再翻記錄原文吐 📁 當答案。

實測截圖 15:24：「你目前觀察到我有哪些習慣呢」→ bot 回 📁〔讀誦經書〕（第一筆）的記錄原文
（還是圖片描述、被截斷）＋「你很常回到《讀誦經書》這個主題呢」＝答非所問。
使用者定調：bot 不是應該有我的日常作息與習慣的**觀察與感受**？

根因（開檔釘死）：這句 is_user_habit_question **有**命中（fallback：我＋習慣＋呢）→ 落 fact_or_chat
＝function-calling 自由 lane——HABIT_GROUND_HINT 只管「講習慣要照統計塊」、管不住「去 quote 一筆
記錄原文當答案」；而手上明明有整套程式算的觀察（habit_facts：早安＋一天第一句＋記寫時段＋主題×時段），
對話作息一句都沒講。

修法（比照 clock/cost/stats：答案可全程式算的題給確定性出口）：is_habit_inventory 結構判準 →
咽喉點 re-route → 專屬 lane（素材全程式算、LLM 只講觀察與感受、不開工具）。全 stub、零網路。
"""

import os
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from telegram_monitor import habits, monitor, persona
from telegram_monitor.state import State

TZ = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 8, 2, 7, 24, 0, tzinfo=timezone.utc)   # 台北 15:24（截圖時刻）
SHOT = "你目前觀察到我有哪些習慣呢"


class DetectorTest(unittest.TestCase):
    def test_screenshot_sentence_hits(self):
        self.assertTrue(habits.is_habit_inventory(SHOT))

    def test_inventory_variants_hit(self):
        self.assertTrue(habits.is_habit_inventory("你對我的作息有什麼發現嗎"))
        self.assertTrue(habits.is_habit_inventory("你了解我的習慣嗎"))
        self.assertTrue(habits.is_habit_inventory("我有哪些習慣呢"))
        self.assertTrue(habits.is_habit_inventory("你知道我記寫裡的行為與習慣？"))

    def test_single_point_recall_stays_out(self):
        # 單點回顧＝照走原 fact_or_chat＋統計注入（§1.42 既有路徑），不被盤點劫走
        self.assertFalse(habits.is_habit_inventory("我平常大概幾點跟你說早安？"))
        self.assertFalse(habits.is_habit_inventory("我平常都幾點記寫"))

    def test_bot_subject_and_future_stay_out(self):
        self.assertFalse(habits.is_habit_inventory("你的習慣是什麼"))       # 問 bot 自己＝不收（同母判準）
        self.assertFalse(habits.is_habit_inventory("我明天八點要開會"))     # 未來安排＝不收
        self.assertFalse(habits.is_habit_inventory("早安"))
        self.assertFalse(habits.is_habit_inventory(""))


class PromptTest(unittest.TestCase):
    def test_prompt_bans_record_dump_and_invites_feel(self):
        p = persona.habit_inventory_user(SHOT, "【統計塊】")
        self.assertIn("觀察與感受", p)
        self.assertIn("不要引用任何一筆記錄的原文", p)     # 📁 病根明令禁止
        self.assertIn("你自己的限制", p)                    # §1.96 同款：樣本少講成自己的限制
        self.assertIn("猜想", p)                            # 感受/假設有位子（截圖那句「定錨」是好的、留下來）
        self.assertIn("【統計塊】", p)                      # 真統計塊在場


def _seed(state):
    for d in range(1, 8):
        t = datetime(2026, 7, d, 6, 55, 0, tzinfo=TZ).timestamp()
        state.habit_events += [{"k": "greet_am", "ts": t}, {"k": "first", "ts": t}, {"k": "msg", "ts": t}]


class EndToEndTest(unittest.TestCase):
    SNAP = SimpleNamespace(summary={"total": 1, "last24h": 0, "last7d": 0},
                           funnel={"candidate": 0, "context": 0, "journey": 0, "watch": 0},
                           heartbeat={"status": "ok"}, filed_records=[])
    BOOM = SimpleNamespace(load_embedding_records=lambda *_: [])
    DATA = {"meta": {}, "records": [{"topicLabel": "讀誦經書", "category": "閱讀",
                                     "text": "一本名為《地藏菩薩本願經》的佛教經書", "fileId": None,
                                     "ts": "2026-06-13T02:28:00Z", "id": "r1"}]}

    class Cl:
        dry_run = True

        def __init__(self):
            self.sent = []

        def send(self, t):
            self.sent.append(t)
            return True

        def send_typing(self):
            pass

    def _cfg(self, on=True):
        return SimpleNamespace(dry_run=False, telegram_chat_id="", timezone="Asia/Taipei",
                               notify_cooldown_min=30, user_habit_ground_enabled=True,
                               habit_obs_fix_enabled=True, habit_inventory_enabled=on,
                               promise_llm_rescue_enabled=False, sticker_llm_rescue_enabled=False,
                               bot_self_promise_enabled=False, send_stickers=False)

    def _coach(self, voice):
        seen = {"inv": None, "ask": None}
        c = SimpleNamespace(enabled=True, api_key="k", model="m", seen=seen,
                            meter=SimpleNamespace(record=lambda *a, **k: None))

        def inv(q, facts, hist):
            seen["inv"] = facts
            return voice

        def ask(*a, **k):
            seen["ask"] = True
            return ("chat", None, "自由 lane 的回覆")

        c.voice_habit_inventory = inv
        c.ask = ask
        c.reply = lambda *a, **k: "自由 lane 的回覆"
        c.voice_greeting = lambda *a, **k: "早安！"
        c.judge_timed_request = lambda t: None
        c.judge_sticker_request = lambda t: None
        c.judge_self_promise = lambda t: None
        return c

    def _run(self, voice, on=True):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        _seed(s)
        cl, co = self.Cl(), self._coach(voice)
        monitor.handle_message({"message": {"chat": {"id": 1}, "text": SHOT, "date": NOW.timestamp()}},
                               co, self.BOOM, self.DATA, self.SNAP, s, cl, self._cfg(on), TZ)
        return "".join(cl.sent), co.seen

    def test_inventory_lane_answers_with_observations(self):
        VOICE = "我記到的是，你大多六點五十幾分就會出現，早安也差不多那時候——我猜你是想給每天找個定錨。準嗎？"
        out, seen = self._run(VOICE)
        self.assertEqual(out, VOICE)                        # 走專屬 lane、原話送出
        self.assertIn("他的習慣", seen["inv"] or "")        # 素材＝habit_facts 真統計塊
        self.assertIn("記寫", seen["inv"] or "")            # 對話作息＋記寫時段都在盤點素材裡
        self.assertIsNone(seen["ask"])                      # ★ 不進 function-calling 自由 lane＝不可能翻記錄吐 📁
        self.assertNotIn("📁", out)

    def test_llm_fail_falls_back_to_honest_template(self):
        out, _ = self._run(None)
        self.assertIn("我手上真的記到的觀察", out)          # 誠實開場
        self.assertIn("早安", out)                          # 統計行在（對話作息真的被講出來）
        self.assertNotIn("📁", out)

    def test_flag_off_stays_on_old_path(self):
        out, seen = self._run("不會用到", on=False)
        self.assertIsNone(seen["inv"])                      # 專屬 lane 不啟動
        self.assertTrue(seen["ask"] or out)                 # 照舊落自由 lane（現狀行為）


class WiringTest(unittest.TestCase):
    def test_reroute_after_142_and_lane_before_fact_or_chat(self):
        with open("telegram_monitor/monitor.py", encoding="utf-8") as f:
            src = f.read()
        r142 = src.index('route.kind in ("greeting", "self_state", "other_mind")')
        rinv = src.index("habits.is_habit_inventory(text)")
        lane = src.index('if route.kind == "habit_inventory":')
        self.assertLess(r142, rinv)                         # 盤點 re-route 蓋過 §1.42 的 fact_or_chat 導向
        self.assertLess(rinv, lane)
        # lane 素材＝habit_facts、出口走 _say（守門兜底照舊）
        seg = src[lane:lane + 1200]
        self.assertIn("habits.habit_facts", seg)
        self.assertIn("_say(client, msg)", seg)


class FlagSyncTest(unittest.TestCase):
    def test_flag_everywhere(self):
        with open("telegram_monitor/config.py", encoding="utf-8") as f:
            src = f.read()
        self.assertIn('_bool("HABIT_INVENTORY", True)', src)
        self.assertIn("habit_inventory_enabled", src)
        with open(".env.example", encoding="utf-8") as f:
            self.assertIn("HABIT_INVENTORY=1", f.read())
        with open("README.md", encoding="utf-8") as f:
            self.assertIn("HABIT_INVENTORY", f.read())


if __name__ == "__main__":
    unittest.main()
