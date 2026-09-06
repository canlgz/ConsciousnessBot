"""🔮 §1.90 記寫預想的 **end-to-end 實跑**：走完整條 `monitor._foresight_emit`。

MEMORY「新機制常被無聲架空」：邏輯對不算數——機制常被 [-N:] 截斷／繞過 _say／被測試 mock 藏住而
從未真的被呼叫到。本檔刻意**不 mock _say**，用假 client 驗真的送出了什麼字、帳本真的寫了什麼。
全 stub、零網路。
"""

import os
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from telegram_monitor import foresight, monitor
from telegram_monitor.state import State

DAY = 86400.0
NOW_DT = datetime(2026, 7, 27, 6, 0, 0, tzinfo=timezone.utc)     # 台北 14:00（非深夜）
NOW = NOW_DT.timestamp()

QUOTE = "今天讀到第三品，心很靜"
NEWREC = "又回到第三品，這次讀得比較慢"


class Cl:
    dry_run = False

    def __init__(self, ok=True):
        self.sent, self.ok = [], ok

    def send(self, t):
        self.sent.append(t)
        return self.ok

    def send_sticker(self, f):
        return True


def _coach(pick="1", voice="我猜「讀誦經書」會再回來一次。這只是我的猜測，不是已經成立的事。"):
    c = SimpleNamespace(enabled=True, api_key="k", model="m",
                        meter=SimpleNamespace(record=lambda *a, **k: None), asked=[], _turn_length=None)

    def _reply(q, *a, **k):
        c.asked.append(q)
        return pick if "挑一個編號" in (q or "") else voice
    c.reply = _reply
    return c


def _cfg(on=True, **kw):
    d = dict(foresight_enabled=on, foresight_cooldown_min=720, foresight_ttl_days=7,
             notify_cooldown_min=30, dry_run=True,
             quiet_start=1, quiet_end=6, timezone="Asia/Taipei")
    d.update(kw)
    return SimpleNamespace(**d)


def _data():
    """真實記寫（bridge 的 anchor 文字必須是這裡的原文——階段 1 的接地保證來自建構時）。"""
    return {"records": [
        {"topicLabel": "讀誦經書", "text": QUOTE, "ts": "2026-07-15T08:00:00Z", "id": "r1"},
        {"topicLabel": "跑步", "text": "早上跑了五公里", "ts": "2026-07-26T08:00:00Z", "id": "r2"},
    ]}


def _state(bridges=True):
    s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
    s.owner_folder_id = "F"
    s.last_user_msg_ts = NOW - 3 * 3600          # 早就不在場（_proactive_ok 過）
    s.last_push_ts = NOW - 10 * 3600
    s.last_foresight_ts = 0
    if bridges:
        s.associations = {"bridges": {"blend::跑步|讀誦經書": {
            "key": "blend::跑步|讀誦經書", "a": "跑步", "b": "讀誦經書", "kind": "blend",
            "cos": 0.55, "support": 3, "emerged": False,
            "anchor_a": {"text": "早上跑了五公里", "ts": None},
            "anchor_b": {"text": QUOTE, "ts": None}}}}
    return s


class ProposeTest(unittest.TestCase):
    def test_proposes_and_books_hypothesis(self):
        s, cl, co = _state(), Cl(), _coach()
        monitor._foresight_emit(cl, s, _cfg(), co, NOW_DT, data=_data())
        out = "".join(cl.sent)
        self.assertTrue(cl.sent, "什麼都沒送出＝lane 沒被走到")
        self.assertIn("讀誦經書", out)
        self.assertEqual(s.foresight["b"], "讀誦經書")
        self.assertEqual(s.foresight["quote"], QUOTE)
        self.assertTrue(s.foresight["told_ts"])          # 說出口成功**之後**才落帳
        self.assertEqual(s.last_foresight_ts, NOW)

    def test_flag_off_is_noop(self):
        s, cl, co = _state(), Cl(), _coach()
        monitor._foresight_emit(cl, s, _cfg(on=False), co, NOW_DT, data=_data())
        self.assertEqual(cl.sent, [])
        self.assertIsNone(s.foresight)
        self.assertEqual(co.asked, [])                   # 連 LLM 都沒燒
        self.assertEqual(s.last_foresight_ts, 0)

    def test_pick_zero_stays_silent_and_cheap(self):
        # 選項閘回 0＝沉默，且**不消耗候選、不寫 last_foresight_ts**（下一圈還能試）
        s, cl, co = _state(), Cl(), _coach(pick="0")
        monitor._foresight_emit(cl, s, _cfg(), co, NOW_DT, data=_data())
        self.assertEqual(cl.sent, [])
        self.assertIsNone(s.foresight)
        self.assertEqual(s.last_foresight_ts, 0)

    def test_ungrounded_llm_falls_back_to_template(self):
        # LLM 捏造引號內容 → 四道自檢攔下 → 退回程式模板（仍然說得出話，不是沉默）
        s, cl, co = _state(), Cl(), _coach(voice="「讀誦經書」停在「上禮拜跟家人吵架那件事」之後就安靜了。")
        monitor._foresight_emit(cl, s, _cfg(), co, NOW_DT, data=_data())
        out = "".join(cl.sent)
        self.assertTrue(cl.sent)
        self.assertNotIn("吵架", out)                     # 捏造的沒送出去
        self.assertIn(QUOTE, out)                         # 模板用的是真實 anchor
        self.assertIsNotNone(s.foresight)

    def test_send_failure_books_nothing(self):
        s, co = _state(), _coach()
        monitor._foresight_emit(Cl(ok=False), s, _cfg(), co, NOW_DT, data=_data())
        self.assertIsNone(s.foresight)                    # 送不出去＝不記帳、下次還能說
        self.assertEqual(s.last_foresight_ts, 0)

    def test_no_bridges_no_speech(self):
        s, cl, co = _state(bridges=False), Cl(), _coach()
        monitor._foresight_emit(cl, s, _cfg(), co, NOW_DT, data=_data())
        self.assertEqual(cl.sent, [])

    def test_own_cooldown(self):
        s, cl, co = _state(), Cl(), _coach()
        s.last_foresight_ts = NOW - 3600              # 1h 前才說過（冷卻 12h）
        monitor._foresight_emit(cl, s, _cfg(), co, NOW_DT, data=_data())
        self.assertEqual(cl.sent, [])

    def test_shared_anti_burst(self):
        s, cl, co = _state(), Cl(), _coach()
        s.last_push_ts = NOW - 60                     # 前面 lane 剛推過（共用 30 分反連發）
        monitor._foresight_emit(cl, s, _cfg(), co, NOW_DT, data=_data())
        self.assertEqual(cl.sent, [])

    def test_only_one_live_hypothesis(self):
        s, cl, co = _state(), Cl(), _coach()
        monitor._foresight_emit(cl, s, _cfg(), co, NOW_DT, data=_data())
        n = len(cl.sent)
        s.last_push_ts, s.last_foresight_ts = 0, 0    # 解除節流
        monitor._foresight_emit(cl, s, _cfg(), co, NOW_DT, data=_data())
        self.assertEqual(len(cl.sent), n)              # 在世假設還沒裁決＝不再開新的


class SettleTest(unittest.TestCase):
    def _with_hyp(self, born_days=2.0):
        s = _state()
        s.foresight = {"pair": "跑步|讀誦經書", "a": "跑步", "b": "讀誦經書", "quote": QUOTE,
                       "born_ts": NOW - born_days * DAY, "told_ts": NOW - born_days * DAY,
                       "ttl_s": 7 * DAY, "verdict": "open", "settled_ts": 0.0,
                       "evidence": "", "evidence_id": ""}
        return s

    def test_hit_shows_both_lines_verbatim(self):
        s, cl = self._with_hyp(), Cl()
        d = _data()
        d["records"].append({"topicLabel": "讀誦經書", "text": NEWREC,
                             "ts": "2026-07-26T12:00:00Z", "id": "r3"})
        co = _coach(voice=f"我先前猜「讀誦經書」會再出現，它真的回來了。當初停在「{QUOTE}」，這次是「{NEWREC}」。")
        monitor._foresight_emit(cl, s, _cfg(), co, NOW_DT, data=d)
        out = "".join(cl.sent)
        self.assertIn(QUOTE, out)
        self.assertIn(NEWREC, out)                     # 交付物＝兩筆逐字並排
        self.assertIsNone(s.foresight)
        self.assertEqual(s.foresight_ledger[-1]["verdict"], "hit")

    def test_miss_owns_it(self):
        s, cl, co = self._with_hyp(born_days=9.0), Cl(), _coach(
            voice=f"我先前猜「讀誦經書」會再出現，結果沒有。我想錯了——那條線目前還停在「{QUOTE}」。")
        monitor._foresight_emit(cl, s, _cfg(), co, NOW_DT, data=_data())
        out = "".join(cl.sent)
        self.assertIn("想錯", out)
        self.assertIsNone(s.foresight)
        self.assertEqual(s.foresight_ledger[-1]["verdict"], "miss")

    def test_open_stays_quiet(self):
        s, cl, co = self._with_hyp(), Cl(), _coach()
        monitor._foresight_emit(cl, s, _cfg(), co, NOW_DT, data=_data())
        self.assertEqual(cl.sent, [])                  # 還沒到期也還沒回來＝不吵
        self.assertIsNotNone(s.foresight)

    def test_settle_beats_propose(self):
        # 回頭認帳永遠優先於再開新的
        s, cl = self._with_hyp(born_days=9.0), Cl()
        co = _coach(voice=f"我先前猜錯了——那條線目前還停在「{QUOTE}」。")
        monitor._foresight_emit(cl, s, _cfg(), co, NOW_DT, data=_data())
        self.assertNotIn("挑一個編號", "".join(co.asked))   # 沒去挑新候選

    def test_miss_streak_doubles_cooldown(self):
        s, cl, co = _state(), Cl(), _coach()
        s.foresight_ledger = [{"key": f"p{i}", "verdict": "miss", "ts": NOW - 30 * DAY} for i in range(3)]
        s.last_foresight_ts = NOW - 13 * 3600          # 過了 12h、但沒過加倍後的 24h
        monitor._foresight_emit(cl, s, _cfg(), co, NOW_DT, data=_data())
        self.assertEqual(cl.sent, [])                  # 連三次想錯＝退讓


class RememberTest(unittest.TestCase):
    def test_proactive_message_enters_history(self):
        # §1.59 鐵則：新增任何 client.send 出口都要問「這則有沒有進 convo_history」
        # ——bot 說過的話自己看不到＝之後必否認
        s, cl, co = _state(), Cl(), _coach()
        monitor._foresight_emit(cl, s, _cfg(), co, NOW_DT, data=_data())
        hist = getattr(s, "convo_history", None) or []
        self.assertTrue(any("🔮" in (h.get("text") or "") for h in hist), "主動訊息沒進對話史")


if __name__ == "__main__":
    unittest.main()
