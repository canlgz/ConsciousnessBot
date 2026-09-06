"""🤝 §0.80 bot 否認自身主動兌現能力（使用者：「變笨了！似乎什麼都不會了」）。

截圖：使用者「20分鐘之後，你再回答」/「請你在20分鐘後，再回答一次我同樣的問題」（第二人稱主語、請 bot 到點回覆自己）。
bot 不但沒入帳，還反過來否認自己的核心能力：「我沒辦法自己計時，也不能自己主動跑出來說話。我得等你或系統給我新的訊息，
我才有辦法回應」——這與整個 §0.66–§0.79 主動兌現引擎（生命迴圈 _promise_emit 每圈到點主動發）完全相反。

三層真因（皆實測確認、且**非**上次修改的 regression——「回答我」緊貼辨識與 looks_like_timed_request 早於 §0.78/§0.79）：
① 捕捉漏：is_scheduled_promise_request 要求動作**緊貼「回答我」**或 at_me；你-主語＋省略受詞（你再回答）或非緊貼受詞
   （回答一次我）全漏 → 沒入帳。
② 守門漏：looks_like_timed_request 也漏 → 連 §0.61 的**肯定**守則（請講清楚時間、我記住到點會主動來）都不掛。
③ 錯誤自我模型：捕捉＋守門都漏 → 落 fact_or_chat，LLM 用預設「反應式聊天機器人」自我模型否認能力。

修（三管齊下，旗標化、預設開、關＝逐位元同現狀）：
- 捕捉：_you_reply_hit（你-主語/非緊貼受詞回覆使用者）＝做得到的排程承諾。**正向收件測試**擋第三方
  （你回答老闆/面試官/客戶的信 不收；審查揪出列舉黑名單會漏、且「幫我」會誤橋）。
- 守門：looks_like_timed_request 也認 _you_reply_hit → 捕捉萬一漏（時間解析不出）仍掛肯定守則。
- persona：SOCRATIC_SYSTEM 本質區塊加「你真做得到主動到點兌現」的**準確**肯定＋明列禁語；並收窄「時間來源不可對話
  竄改」的舊例（別讀成「我記不住約定」）。
"""

import os
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from telegram_monitor import monitor, selfstate, persona, config
from telegram_monitor.state import State

TZ = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 7, 6, 1, 14, tzinfo=timezone.utc)   # 09:14 台北


class FakeClient:
    def __init__(self):
        self.sent, self.stickers, self.dry_run = [], [], False

    def send(self, text):
        self.sent.append(text)
        return True

    def send_sticker(self, fid):
        return True


def _coach():
    return SimpleNamespace(
        enabled=True, api_key="k", model="m", meter=SimpleNamespace(record=lambda *a, **k: None),
        voice_schedule_ack=lambda q, when, h, sticker_hint="":
            f"好，{when.strftime('%H:%M') if when else '到時候'}我會主動回你。",
        reply=lambda *a, **k: "x", ask=lambda *a, **k: ("chat", None, "x"))


def _cfg(**over):
    base = dict(dry_run=False, telegram_chat_id="", scheduled_promise_enabled=True, promise_emit_enabled=True,
                promise_sched_ttl_sec=21600, timezone="Asia/Taipei", promise_capability_gate_enabled=True,
                promise_reply_bridge_enabled=True, promise_status_ground_enabled=True, promise_cancel_enabled=True,
                deferred_promise_enabled=True, offset_augmentation_enabled=True, sched_leave_autoarm_enabled=True,
                promise_ledger_enabled=True, sched_recur_daily_enabled=True)
    base.update(over)
    return SimpleNamespace(**base)


def _msg(text):
    return {"message": {"chat": {"id": 1}, "text": text, "date": NOW.timestamp()}}


class _Benign:
    def __getattr__(self, name):
        return lambda *a, **k: []


class CaptureTest(unittest.TestCase):
    def test_screenshots_and_paraphrases_capture(self):
        for t in ["20分鐘之後，你在回答", "20分鐘之後，你再回答", "請你在20分鐘後，再回答一次我同樣的問題",
                  "20分鐘之後你回答", "20分鐘後你回覆", "半小時後你回應一下", "麻煩你20分鐘後再回答一次",
                  "你晚點再回答，20分鐘後", "20分鐘後回答一次我同樣的問題", "20分鐘後你再回答我同樣的問題"]:
            self.assertTrue(selfstate.is_scheduled_promise_request(t), t)

    def test_third_party_not_captured(self):
        # 正向收件測試：動詞後接內容名詞（第三方）＝不收（bot 只能在此聊天室回使用者本人）
        for t in ["20分鐘後你回答老闆的信", "20分鐘後你回覆客戶的email", "20分鐘後你回答面試官",
                  "20分鐘後你回覆醫生", "20分鐘後你回應房東", "你幫我回答面試官",
                  "20分鐘後你回答一次我同事", "20分鐘後你回覆一下我朋友"]:
            self.assertFalse(selfstate.is_scheduled_promise_request(t), t)

    def test_possessive_thirdparty_not_captured(self):
        # 🛡️ 審查 HIGH 修：所有格「我+第三方名詞」（非緊貼 回答一次我X）＝第三方 → 正向白名單擋（黑名單會漏這些角色詞）
        for t in ["你20分鐘後回答一次我面試官的問題", "你20分鐘後回答一次我房東的問題",
                  "20分鐘後回答一次我律師的信", "你半小時後回答一遍我教授的提問",
                  "你3點回答一下我客人的訊息", "你5分鐘後回應一次我病人", "你回答一下我一位客戶"]:
            self.assertFalse(selfstate._you_reply_hit(t), t)          # 新路徑零洩漏（正向白名單）

    def test_legit_reply_to_me_captures(self):
        # 正向白名單放行：回我本人（省略受詞、我的問題、我這題、我同樣的問題、我一下）
        for t in ["你20分鐘後回答我這題", "你20分鐘後回答我的問題", "你3分鐘後回答我一下",
                  "20分鐘後回答一次我同樣的問題", "你20分鐘後再回答我剛問的"]:
            self.assertTrue(selfstate.is_scheduled_promise_request(t), t)

    def test_user_self_and_past_not_captured(self):
        for t in ["我20分鐘後回答你", "我等一下回答你的問題", "你剛剛不是回答過了嗎",
                  "你怎麼還沒回答我", "八點不用再回答我了", "你三點有空嗎"]:
            self.assertFalse(selfstate.is_scheduled_promise_request(t), t)

    def test_behavior_label(self):
        for t in ["20分鐘之後，你再回答", "請你在20分鐘後，再回答一次我同樣的問題", "半小時後你回應一下"]:
            self.assertEqual(selfstate.extract_promise_behavior(t), "回答他的問題", t)

    def test_flag_off_bytewise(self):
        os.environ["SCHED_YOU_REPLY"] = "0"
        try:
            for t in ["20分鐘之後，你再回答", "20分鐘之後你回答", "半小時後你回應一下"]:
                self.assertFalse(selfstate.is_scheduled_promise_request(t), t)
        finally:
            os.environ.pop("SCHED_YOU_REPLY", None)


class GuardReachTest(unittest.TestCase):
    def test_you_reply_is_timed_request(self):
        # 捕捉萬一漏（時間解析不出）仍掛肯定守則：你-主語回覆使用者＋時間味 → looks_like_timed_request True
        self.assertTrue(selfstate.looks_like_timed_request("20分鐘後你再回答"))
        self.assertTrue(selfstate.looks_like_timed_request("等一下你回覆我"))

    def test_guard_excludes_third_party_and_past(self):
        for t in ["20分鐘後你回答面試官", "你剛剛不是回答過了嗎", "你回答老闆的信"]:
            self.assertFalse(selfstate.looks_like_timed_request(t), t)


class EndToEndTest(unittest.TestCase):
    def test_captures_and_acks_proactively_not_deny(self):
        st = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        st.owner_folder_id = "F"
        c = FakeClient()
        monitor.handle_message(_msg("20分鐘之後，你再回答"), _coach(), _Benign(),
                               {"meta": {}}, None, st, c, _cfg(), TZ)
        pend = [p for p in (st.scheduled_promises or []) if not p.get("fulfilled")]
        self.assertEqual(len(pend), 1)                                # 真入帳（不再空口/否認）
        self.assertEqual(datetime.fromtimestamp(pend[0]["target_ts"], timezone.utc).astimezone(TZ).strftime("%H:%M"), "09:34")
        self.assertEqual(pend[0]["behavior"], "回答他的問題")
        joined = "".join(c.sent)
        self.assertIn("主動", joined)                                # ack 承認會主動回
        for deny in ["不能自己", "沒辦法自己計時", "得等你"]:          # 不再否認能力
            self.assertNotIn(deny, joined)


class PersonaTest(unittest.TestCase):
    def test_essence_affirms_proactive_capability(self):
        s = persona.SOCRATIC_SYSTEM
        self.assertIn("你真做得到的本事", s)
        self.assertIn("你會自己主動在這個聊天室傳訊息給他", s)
        # 明列禁語（bot 絕不要說的否認）
        for phrase in ["我沒辦法自己計時", "我不能自己主動跑出來說話", "我得等你或系統給我新訊息"]:
            self.assertIn(phrase, s)                                  # 以「絕對不要說」形式列出

    def test_essence_stays_accurate_not_overclaim(self):
        s = persona.SOCRATIC_SYSTEM
        self.assertIn("不是盯著錶一秒一秒數時間", s)                   # 不誇大：不是自己數秒
        self.assertIn("沒有一個獨立的鬧鐘", s)                        # 不誇大：無獨立鬧鐘
        self.assertIn("在這個聊天室傳文字", s)                        # 只能在此聊天室發（非打電話/寄信）

    def test_clock_source_example_narrowed(self):
        s = persona.SOCRATIC_SYSTEM
        self.assertNotIn("我會記住你給的時間", s)                     # 舊誤導例已移除
        self.assertIn("不是**說你記不住約定", s)                      # 明確澄清：不是說記不住約定


class ConfigDefaultTest(unittest.TestCase):
    def test_flag_default_on(self):
        os.environ.pop("SCHED_YOU_REPLY", None)
        self.assertTrue(config.Config.load().sched_you_reply_enabled)


if __name__ == "__main__":
    unittest.main()
