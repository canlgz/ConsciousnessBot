"""🪪 §1.61 此刻事實卡（FACT_CARD）：核心事實常駐注入——通盤解，不再靠「偵測器認出在問什麼」才接地。

系統性病根（使用者原話「不能只是錯哪改哪，要找出通盤的問題並且解決」；§1.46–§1.60 十五個 § 的共同上游）：
① 點對點接地架構——每種問題要先有偵測器（詞表）認出「他在問X」才注入X的真相；詞表漏一縫＝LLM 無接地
  自由發揮＝亂編＝等截圖＝加新偵測器（詞表前科 18 次的真正結構原因）；
② ground 拼裝無單一權威——各 § 自己拼 dict、兩來源自打架（§1.60 補遺「今天 09:37——還沒有新的記寫」）。

§1.61 結構解：每個互動輪**常駐**注入一張程式算的〈此刻事實卡〉（此刻時間/他的記寫/活著的約定/我的內在
座標），規則：每欄位恰一個權威來源（時間＝temporal、記寫＝_write_ground_data 與 §1.60 守門**同一把**、
約定＝scheduled_promises、座標＝circumplex.position 單一真相）＝卡與守門結構上不可能矛盾；卡尾明令
「以卡為準、卡上沒有的數字不要編、不主動念數字」。偵測器型 hint 降級為深答加強、不再是真相開關。
旗標兩層分離：config 預設 True／monitor 端 getattr 預設 False＝不注入＝逐位元同現狀。全 stub、零網路。
"""

import os
import itertools
import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from telegram_monitor import monitor
from telegram_monitor.state import State

TZ = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 7, 22, 1, 49, tzinfo=timezone.utc)          # 台北 07/22 09:49
W_TODAY = NOW - timedelta(minutes=12)
W_YDAY = NOW - timedelta(hours=24, minutes=12)

REC = {"id": "r1", "key": "生活|閱讀", "status": "journey",
       "category": "生活", "topicLabel": "閱讀", "text": "讀完一輪"}


def _snap(last_write):
    return SimpleNamespace(summary={"last_write": last_write})


def _state(entropy=True, proms=None):
    s = SimpleNamespace(scheduled_promises=list(proms or []),
                        entropy=(SimpleNamespace(mood=-0.12, arousal=0.05, hunger=0.4) if entropy else None))
    return s


def _cfg(on=True):
    return SimpleNamespace(fact_card_enabled=on)


PROM = {"target_ts": NOW.timestamp() + 600, "made_ts": NOW.timestamp(), "made_text": "10分鐘後叫我",
        "behavior": "叫他", "fulfilled": False, "status": "pending"}


# ── 單元＋性質測試：卡的內部一致性（矛盾不可能性矩陣）──────────────────────
class CardBuilderTest(unittest.TestCase):
    def test_flag_off_empty(self):
        self.assertEqual(monitor._fact_card(_state(), _cfg(on=False), {"records": []}, _snap(W_YDAY), NOW, TZ), "")

    def test_core_lines_present(self):
        card = monitor._fact_card(_state(proms=[PROM]), _cfg(), {"records": [dict(REC, ts=W_TODAY)]},
                                  _snap(W_TODAY), NOW, TZ)
        self.assertIn("此刻事實卡", card)
        self.assertIn("2026/07/22", card)                    # 時間＝temporal/程式算
        self.assertIn("09:49", card)
        self.assertIn("今天已 1 則", card)                    # 記寫＝§1.60 同一把 ground
        self.assertIn("1 筆活著", card)                       # 約定帳本
        self.assertRegex(card, re.compile(r"V [+-]\d\.\d{2}、A [+-]\d\.\d{2}"))   # 座標單一真相
        self.assertIn("不要自己編", card)                     # 規則尾
        self.assertIn("不是要你主動把數字念出來", card)

    def test_consistency_matrix_no_contradiction(self):
        # 性質測試：寫作（今天/昨天/無）× 帳本（空/有）× 內在（有/無）——任一組合的卡都不得自相矛盾
        for last, proms, ent in itertools.product(
                (W_TODAY, W_YDAY, None), ([], [PROM]), (True, False)):
            recs = [dict(REC, ts=last)] if last is not None else []
            card = monitor._fact_card(_state(entropy=ent, proms=proms), _cfg(),
                                      {"records": recs}, _snap(last), NOW, TZ)
            tag = f"last={last} proms={len(proms)} ent={ent}"
            # 矛盾不可能性：「今天已 N 則」與「今天到現在還沒有新的」互斥
            self.assertFalse("今天已" in card and "還沒有新的" in card, tag)
            # 最後一次＝今天 ⇒ 絕不能同時說今天還沒有（§1.60 補遺同款不變式，卡層再釘一次）
            if "最後一次＝今天" in card:
                self.assertNotIn("還沒有新的", card, tag)
            # 帳本行恰一種說法
            self.assertTrue(("筆活著" in card) ^ ("沒有活著的計時約定" in card), tag)
            # 座標行只在內在存在時出現
            self.assertEqual(ent, "內在座標" in card, tag)

    def test_card_and_write_guard_share_single_truth(self):
        # 結構性單一真相：卡說「還沒有新的」⇔ §1.60 守門會開火；卡說「今天已」⇔ 守門沉默——同一把 ground
        for last in (W_TODAY, W_YDAY):
            data = {"records": [dict(REC, ts=last)]}
            card = monitor._fact_card(_state(), _cfg(), data, _snap(last), NOW, TZ)
            g = monitor._write_ground_data(_snap(last), data, NOW.timestamp(), TZ)
            _, fired = monitor._write_claim_fix("你今天又記寫了讀經進度。", g)
            self.assertEqual("還沒有新的" in card, fired, str(last))


# ── §1.62：卡收錄「我最近送出的貼圖」（截圖 18:30「我剛剛有傳貼圖嗎？」「應該是系統自己送的」＝
# 否認自己送圖＋行為割裂；§0.90 偵測器有中、hint 仍被 LLM 蓋掉 → 改常駐接地）──────────────────
class StickerOnCardTest(unittest.TestCase):
    def _state_with_sticker(self, ago_sec=300, emoji="😄", desc="一隻黃狗托著下巴"):
        s = _state()
        s.last_sticker_ts = NOW.timestamp() - ago_sec
        s.last_sticker_emoji = emoji
        s.last_sticker_desc = desc
        return s

    def test_recent_sticker_line_with_meaning(self):
        card = monitor._fact_card(self._state_with_sticker(), _cfg(), {"records": []}, _snap(W_YDAY), NOW, TZ)
        self.assertIn("我最近送出的貼圖", card)
        self.assertIn("😄", card)
        self.assertIn("黃狗", card)
        self.assertIn("依當下心情自動挑的", card)             # 「代表意義」＝真實機制：讀心情挑池
        self.assertIn("不是「系統」替我送的", card)            # 割裂歸因＝明文封死

    def test_bare_sticker_honest_unknown(self):
        card = monitor._fact_card(self._state_with_sticker(emoji="", desc=""), _cfg(),
                                  {"records": []}, _snap(W_YDAY), NOW, TZ)
        self.assertIn("我最近送出的貼圖", card)
        self.assertIn("認不出圖案", card)                     # 不演不假裝：沒讀過畫面就誠實說
        self.assertIn("確定是我送的", card)

    def test_stale_sticker_no_line(self):
        card = monitor._fact_card(self._state_with_sticker(ago_sec=3 * 3600), _cfg(),
                                  {"records": []}, _snap(W_YDAY), NOW, TZ)
        self.assertNotIn("我最近送出的貼圖", card)             # 2h 窗外＝不佔卡


# ── 整合：不需要任何偵測器命中，普通一句話也拿得到卡 ───────────────────────
class IntegrationTest(unittest.TestCase):
    class Cl:
        def __init__(self): self.sent, self.dry_run = [], False
        def send(self, t): self.sent.append(t); return True

    SNAP = SimpleNamespace(summary={"last_write": W_YDAY, "total": 470, "last24h": 1, "last7d": 21,
                                    "streak": 40, "media": {}, "active_explorations": 0},
                           funnel={"candidate": 16, "context": 11, "journey": 10, "watch": 1},
                           heartbeat={"status": "ok"}, filed_records=[])
    BOOM = SimpleNamespace(load_embedding_records=lambda *_: [])

    def _cfg(self, on=True):
        base = dict(dry_run=False, telegram_chat_id="", scheduled_promise_enabled=True,
                    promise_emit_enabled=True, promise_sched_ttl_sec=21600, timezone="Asia/Taipei",
                    notify_cooldown_min=30, promise_reply_bridge_enabled=False, promise_ledger_enabled=True,
                    sched_leave_autoarm_enabled=True, deferred_promise_enabled=False,
                    promise_llm_rescue_enabled=False, sticker_llm_rescue_enabled=False,
                    promise_keep_claim_guard_enabled=True, promise_said_ground_enabled=True,
                    bot_self_promise_enabled=False, promise_preempt_enabled=False,
                    sticker_sent_memory_enabled=True, send_stickers=True,
                    sticker_fakesend_guard_enabled=False, recall_ground_guard_enabled=False,
                    selfshare_reason_ground_enabled=False, wake_projection_guard_enabled=False,
                    user_habit_ground_enabled=False, self_feel_condense_enabled=False,
                    promise_deliver_content_enabled=False, mood_coord_report_enabled=False)
        if on is not None:
            base["fact_card_enabled"] = on
        return SimpleNamespace(**base)

    def _coach(self):
        seen = {"ask": None, "reply": None}
        c = SimpleNamespace(enabled=True, api_key="k", model="m",
                            meter=SimpleNamespace(record=lambda *a, **k: None), seen=seen)

        def ask(*a, **k):
            seen["ask"] = k.get("extra_system")
            return ("chat", None, "嗯。")

        def reply(*a, **k):
            seen["reply"] = k.get("extra_system")
            return "嗯。"

        c.ask = ask
        c.reply = reply
        c.voice_schedule_ack = lambda q, w, h, sticker_hint="": "好。"
        c.voice_promise_ack = lambda q, h: "好。"
        c.judge_timed_request = lambda t: None
        c.judge_sticker_request = lambda t: None
        c.judge_self_promise = lambda t: None
        c.judge_promise_preempt = lambda *a, **k: False
        c.voice_promise_keep = lambda *a, **k: None
        c.voice_greeting = lambda *a, **k: "早安！"
        return c

    def _run(self, cfg, text="今天天氣不錯"):
        s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        s.owner_folder_id = "F"
        s.entropy = SimpleNamespace(mood=-0.12, arousal=0.05, hunger=0.4, self_stims_this_idle=0,
                                    reach_outs_this_idle=0, coping_reach_outs_this_idle=0)
        co = self._coach()
        monitor.handle_message({"message": {"chat": {"id": 1}, "text": text, "date": NOW.timestamp()}},
                               co, self.BOOM, {"records": [dict(REC, ts=W_YDAY)], "meta": {}}, self.SNAP,
                               s, self.Cl(), cfg, TZ)
        return co

    def test_plain_turn_gets_card_without_any_detector(self):
        # 通盤解的核心驗證：一句普通閒聊、零偵測器命中 → prompt 仍有整張事實卡
        co = self._run(self._cfg())
        got = (co.seen["ask"] or "") + (co.seen["reply"] or "")
        self.assertIn("此刻事實卡", got)
        self.assertIn("還沒有新的", got)                     # 記寫真相（昨天寫的、今天還沒）
        self.assertIn("沒有活著的計時約定", got)

    def test_flag_off_bitwise(self):
        for on in (False, None):
            co = self._run(self._cfg(on=on))
            got = (co.seen["ask"] or "") + (co.seen["reply"] or "")
            self.assertNotIn("此刻事實卡", got, str(on))


# ── 同步 ───────────────────────────────────────────────────────────────────
class ConfigTest(unittest.TestCase):
    def test_config_synced(self):
        src = open("telegram_monitor/config.py", encoding="utf-8").read()
        self.assertIn("FACT_CARD", src)
        self.assertIn("fact_card_enabled", src)
        env = open(".env.example", encoding="utf-8").read()
        self.assertRegex(env, re.compile(r"^FACT_CARD=1", re.M))
        self.assertIn("FACT_CARD", open("README.md", encoding="utf-8").read())

    def test_getattr_defaults_false(self):
        self.assertFalse(getattr(SimpleNamespace(), "fact_card_enabled", False))


if __name__ == "__main__":
    unittest.main()
