"""🤝 §1.10 「20分鐘後，說說你當時的心情」承諾捕捉漏 → 掉進一般聊天路由、LLM 自編幻覺時刻。

截圖（1:1 聊天室）：10:17 使用者「別廢話，20 分鐘後，說說你當時的心情」。這是請 bot 到那個未來時刻（10:37/10:38）
自陳心情——合法的到點自陳承諾；正解該是 10:38。但 bot 卻回「20 分鐘後，也就是 11:08，我會跟你說說我那時候的心情」
＝一個既不是 now+Δ、也不是 target 的無主數字（相差 +30 分）。

真因（實測確認、非讀碼臆測）：`_FEELING_SELF_RE`（selfstate.py）第二支只在「你/妳」與情緒名詞之間放行
**現在式**時間指示詞（現在/此刻/這會兒）。使用者說「你**當時**的心情」——在有未來時間錨（20分鐘後）時，「當時」
＝「到那個未來時刻的你」，同樣是合法的到點自陳；但它不在白名單 → 整條 `_FEELING_SELF_RE` 落空 → `_self_report_hit`
＝False → `is_scheduled_promise_request`/`is_feeling_promise_request` 皆 False → 承諾從未入帳、句子掉進一般對話路由，
由 LLM 自由即興出 11:08。時間數學無辜：temporal 對同一句一直解析出正確的 10:38（實測），只是捕捉層把它丟了。

修：把「當時/那時…」這類**指涉時刻**指示詞補進第二支那個**封閉的時間指示詞群組**（present + referenced-moment），
不是往窮舉動詞表再加詞、也不開任意字元 gap（那會誤收「說說你老闆/你朋友當時的心情」＝重演 §0.78 偽陽性）。
"""

import os
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from telegram_monitor import monitor, selfstate, temporal
from telegram_monitor.state import State

TZ = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 7, 10, 2, 18, tzinfo=timezone.utc)   # 10:18 台北（bot 回覆當下）


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
            f"好，我記下來了，{when.strftime('%H:%M') if when else '到時候'}我會跟你說。",
        voice_promise_keep=lambda when, facts, h, promised="", late=False, feeling_ground="", **k:
            f"到點了：{promised}",
        reply=lambda *a, **k: "x", ask=lambda *a, **k: ("chat", None, "x"))


class _Benign:
    def __getattr__(self, name):
        return lambda *a, **k: []


def _cfg(**over):
    base = dict(dry_run=False, telegram_chat_id="", scheduled_promise_enabled=True, promise_emit_enabled=True,
                promise_sched_ttl_sec=21600, timezone="Asia/Taipei", promise_capability_gate_enabled=True,
                promise_reply_bridge_enabled=True, promise_status_ground_enabled=True, promise_cancel_enabled=True,
                deferred_promise_enabled=True, offset_augmentation_enabled=True, sched_leave_autoarm_enabled=True,
                promise_ledger_enabled=True, sched_recur_daily_enabled=True, promise_act_aligned=True,
                promise_tick_resilient_enabled=True, promise_overdue_guard_exempt=True, promise_late_exempt_defer=True,
                global_clock_guard=True, global_time_anchor=True)
    base.update(over)
    return SimpleNamespace(**base)


class CaptureTest(unittest.TestCase):
    def test_self_referent_moment_captured(self):
        # 「當時/那時/那時候」＝指涉（未來到點的）時刻，與 現在/此刻 一樣是合法自陳指示詞 → 皆須捕捉
        for t in ["別廢話，20 分鐘後，說說你當時的心情", "20分鐘後說說你那時的心情",
                  "20分鐘後說說你那時候的感受", "20分鐘後告訴我你到時的想法",
                  # present 白名單原本就過的仍過（不回歸）
                  "20分鐘後說說你此刻的心情", "20分鐘後說說你的心情"]:
            self.assertTrue(selfstate.is_scheduled_promise_request(t), t)
            self.assertTrue(selfstate.is_feeling_promise_request(t), t)

    def test_third_party_moment_not_captured(self):
        # 🛡️ 第三方守門：老闆/朋友不是時間指示詞、會在 regex 中斷開——「你老闆/你朋友當時的心情」≠ bot 自陳 → 不收
        # （關鍵：不可用「你[^標點]{0,4}的?(情緒名詞)」字元 gap，否則會把老闆的心情誤當自陳，重演 §0.78 偽陽性）
        for t in ["20分鐘後說說你老闆的心情", "20分鐘後說說你朋友當時的心情",
                  "20分鐘後說說你同事那時的感受"]:
            self.assertFalse(selfstate.is_scheduled_promise_request(t), t)
            self.assertFalse(selfstate.is_feeling_promise_request(t), t)

    def test_temporal_was_never_wrong(self):
        # 時間數學無辜：同一句 temporal 一直解析出正確 10:38（now 10:18 + 20 分），11:08 純屬未接地的 LLM 幻覺
        eps = temporal.all_clock_epochs("別廢話，20 分鐘後，說說你當時的心情", NOW, TZ)
        self.assertEqual(len(eps), 1)
        self.assertEqual(
            datetime.fromtimestamp(eps[0], timezone.utc).astimezone(TZ).strftime("%H:%M"), "10:38")


class EndToEndTest(unittest.TestCase):
    def test_grounded_10_38_not_hallucinated_11_08(self):
        st = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        st.owner_folder_id = "F"
        c = FakeClient()
        monitor.handle_message(
            {"message": {"chat": {"id": 1},
                         "text": "別廢話，20 分鐘後，說說你當時的心情",
                         "date": NOW.timestamp()}},
            _coach(), _Benign(), {"meta": {}}, None, st, c, _cfg(), TZ)
        pend = [p for p in (st.scheduled_promises or []) if not p.get("fulfilled")]
        self.assertEqual(len(pend), 1)                                # 真入帳（不再掉進一般對話路由）
        self.assertEqual(
            datetime.fromtimestamp(pend[0]["target_ts"], timezone.utc).astimezone(TZ).strftime("%H:%M"),
            "10:38")                                                  # 接地的正確到點（20 分鐘後），非 11:08
        joined = "".join(c.sent)
        self.assertIn("10:38", joined)                               # ack 講對的到點時刻
        self.assertNotIn("11:08", joined)                            # 截圖的幻覺數字不再出現


if __name__ == "__main__":
    unittest.main()
