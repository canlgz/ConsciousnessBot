"""🤝 §0.66 約定時間未履行的全鏈修復（截圖 22:06–22:12「離開約二十分鐘→叫我」嘴上倒數、從未主動叫）：
   ① 暫離交代自動計時（我要離開約二十分鐘 → 真的記進帳本）；② 承諾狀態問句接地（你有叫我嗎/時間到了沒 →
   帳本真時鐘算術、不再 LLM 心算）；③ 回覆橋（已到點未兌現＋使用者正在說話 → 這一輪先把欠的做掉）；
   ④ 叫醒我動結式 at_me 同步（我要休息約二十分鐘，時間到了叫醒我）。"""

import os
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from telegram_monitor import intent, monitor, referent, selfstate
from telegram_monitor.state import State

NOW = datetime(2026, 7, 4, 13, 50, 0, tzinfo=timezone.utc)   # 台北 21:50（截圖時段）
TZ = ZoneInfo("Asia/Taipei")


class FakeClient:
    def __init__(self, dry_run=False):
        self.sent, self.dry_run = [], dry_run

    def send(self, text):
        self.sent.append(text)
        return True


def _coach(keeps=None):
    """keeps：list 收 voice_promise_keep 的 (when, promised, late) 呼叫記錄。"""
    def _keep(when, facts, h, promised="", late=False, feeling_ground="", **k):
        if keeps is not None:
            keeps.append({"when": when, "promised": promised, "late": late})
        return "抱歉我遲了時間到了該回來囉" if late else "時間到了該回來囉"   # 單短句＝_say 不拆泡泡（測則數才穩）
    return SimpleNamespace(enabled=True, api_key="k", model="m",
                           meter=SimpleNamespace(record=lambda *a, **k: None),
                           voice_promise_ack=lambda q, h: "好，有上來我會說。",
                           voice_schedule_ack=lambda q, when, h, sticker_hint="": (
                               f"好，我記住了——{when.strftime('%H:%M') if when else '到時候'}我會叫你。"),
                           voice_promise_keep=_keep,
                           reply=lambda text, facts, hist, **k: "〔grounded〕" + (facts or "")[:40])


def _cfg(**over):
    base = dict(dry_run=False, telegram_chat_id="", scheduled_promise_enabled=True,
                promise_emit_enabled=True, promise_sched_ttl_sec=21600,
                timezone="Asia/Taipei", notify_cooldown_min=30)
    base.update(over)
    return SimpleNamespace(**base)


def _state(tmp):
    s = State(os.path.join(tmp, "state.json"))
    s.owner_folder_id = "F"
    return s


_BOOM = SimpleNamespace(load_embedding_records=lambda *_: (_ for _ in ()).throw(AssertionError("不該報現況")))


def _msg(text, when=NOW):
    return {"message": {"chat": {"id": 1}, "text": text, "date": when.timestamp()}}


class StatusKindTest(unittest.TestCase):
    """承諾狀態問句三型：didcall（明確問 bot 有沒有叫）/timeup（時間到了沒）/remain（還差多久）。"""

    def test_didcall_forms(self):
        for q in ["你有叫我嗎？", "你剛剛有叫我嗎", "你有沒有提醒我", "你叫我了嗎",
                  "你還沒叫我", "你都沒叫我", "你有喊我嗎"]:
            self.assertEqual(selfstate.promise_status_kind(q), "didcall", q)

    def test_timeup_forms(self):
        for q in ["時間到了沒", "時間到了嗎", "到了沒", "到了嗎？", "你確定還沒到？", "還沒到嗎",
                  "是不是到了", "二十分鐘到了沒", "20分鐘過了嗎", "到時間了嗎", "時間到了",
                  "查一下時間，到了沒", "你確定到了嗎", "是不是時間到了"]:
            self.assertEqual(selfstate.promise_status_kind(q), "timeup", q)

    def test_remain_forms(self):
        for q in ["還差多久", "還剩幾分鐘", "還有多久"]:
            self.assertEqual(selfstate.promise_status_kind(q), "remain", q)

    def test_negatives(self):
        # 到/叫 指第三方或慣用語 → 不收（『包裹到了嗎』不是在問計時約定）
        for q in ["包裹到了嗎", "他到了沒", "你到了嗎", "車到了沒", "我到了", "想不到了嗎",
                  "還有多久到台北", "你叫我起床", "明天8點叫我", "你叫我阿明就好",
                  "意想不到了嗎", "你猜到了嗎", "外送到了嗎", "大家都到了嗎", "等你想到了嗎"]:
            self.assertEqual(selfstate.promise_status_kind(q), "", q)

    def test_status_not_new_request(self):
        # 狀態問句不可被誤收成新排程承諾（DID_Q/PAST_Q 守門既有行為）
        for q in ["你有叫我嗎？", "時間到了沒", "二十分鐘到了沒"]:
            self.assertFalse(selfstate.is_scheduled_promise_request(q), q)


class LeaveDetectorTest(unittest.TestCase):
    """暫離交代（無指向我動詞的計時交代）＝人類同伴聽到就會記時間。"""

    def test_leave_statements_hit_with_secs(self):
        for q, secs in [("我要離開約二十分鐘", 1200), ("我離開約20分鐘", 1200), ("我去睡半小時", 1800),
                        ("我先出去大概十分鐘", 600), ("我出去買個東西，差不多15分鐘", 900),
                        ("我去洗個澡，大概15分鐘", 900), ("等等我要出門約一小時", 3600),
                        ("我去開會，約兩小時", 7200), ("我暫離十分鐘", 600)]:
            self.assertTrue(selfstate.is_leave_duration_statement(q), q)
            self.assertEqual(selfstate.leave_duration_secs(q), secs, q)

    def test_leave_negatives(self):
        # 過去/慣常敘述、假設、第三人、否定、超常理時長 → 不收
        for q in ["我離開了二十分鐘", "我離開二十分鐘了", "我剛出去二十分鐘", "我出去過二十分鐘",
                  "如果我離開二十分鐘你會想我嗎", "我媽要出去二十分鐘", "他要離開二十分鐘",
                  "我朋友去睡半小時", "我不要離開二十分鐘", "我出去等了二十分鐘",
                  "要是我去睡半小時呢", "我們出去二十分鐘", "我出門三十小時", "你出去二十分鐘",
                  "我睡了半小時"]:
            self.assertFalse(selfstate.is_leave_duration_statement(q), q)

    def test_review_phantom_families_rejected(self):
        # 對抗式審查 confirmed（HIGH×4＋MED×2、逐項實測重現後改結構白名單縫隙）：
        # 稱謂繞過／慣常（每天→還會被蓋 recur=daily）／過去敘述／否定「沒」／別句時距偷渡／假設封閉表
        for q in ["我阿姨要出去二十分鐘", "我表哥要出去二十分鐘", "我老闆去開會兩小時",
                  "我男友去洗澡大概十分鐘", "我同學出去買東西三十分鐘", "我學長要離開二十分鐘",
                  "我每天出去散步三十分鐘", "我通常午睡半小時", "我平常都休息十分鐘而已",
                  "我昨天出去三十分鐘，都沒人理我", "我上次離開二十分鐘你就找不到我",
                  "我今天早上出去運動三十分鐘", "我就離開二十分鐘而已，你就已讀我",
                  "我出去二十分鐘而已你就睡著了", "我沒有要出去二十分鐘", "我又沒有離開三十分鐘",
                  "我去休息，你先自己聽歌兩小時吧", "我先忙，這部片長兩小時",
                  "我去開會，下午的會要開三小時", "假設一下，我離開二十分鐘怎麼辦",
                  "我夢到我離開二十分鐘"]:
            self.assertFalse(selfstate.is_leave_duration_statement(q), q)

    def test_review_digit_not_eaten_by_filler(self):
        # 審查修的回歸：動詞→時距填充段不得啃走數字（「約**二**十分鐘」→十分鐘、「**3**0分鐘」→0分鐘）
        for q, secs in [("我要離開約二十分鐘", 1200), ("我離開約20分鐘", 1200),
                        ("我先去休息二十分鐘", 1200), ("我要去忙30分鐘", 1800),
                        ("我可能出去二十分鐘", 1200), ("我出門一趟，20分鐘", 1200),
                        ("我去睡個覺，三十分鐘", 1800)]:
            self.assertEqual(selfstate.leave_duration_secs(q), secs, q)

    def test_review_third_party_will_extended(self):
        # 審查註記：3rd-will 稱謂表補齊（阿姨/老闆/護理師…）——「X 會叫我」＝別人會叫、不是請 bot
        for q in ["我阿姨十分鐘後會叫我", "我老闆半小時後會提醒我", "護理師二十分鐘後會通知我",
                  "我媽二十分鐘後會叫我"]:
            self.assertFalse(selfstate.is_scheduled_promise_request(q), q)
        # 混合句裡的真請求不誤殺（無「你」也一樣：怪≠叫醒動詞）
        self.assertTrue(selfstate.is_scheduled_promise_request("他會怪我，十分鐘後叫我"))

    def test_wake_verb_at_me_sync(self):
        # 🤝 §0.66：叫「醒」我動結式把受詞隔開（不含子串「叫我」）→ 原本句首「我」主語排除誤擋
        self.assertTrue(selfstate.is_scheduled_promise_request("我要休息約二十分鐘，時間到了叫醒我"))
        self.assertTrue(selfstate.is_scheduled_promise_request("我睡半小時，到時喊醒我"))
        # 第三方叫醒敘述不收（無 at_me 之外的守門仍在）
        self.assertFalse(selfstate.is_scheduled_promise_request("我媽二十分鐘後會叫醒我"))

    def test_leave_routes_to_scheduled_promise(self):
        ref = referent.Referent()
        self.assertEqual(intent.resolve("我要離開約二十分鐘", ref, None, cfg=_cfg()).kind,
                         "scheduled_promise")
        # 旗標關 → 照舊 fact_or_chat
        off = _cfg(sched_leave_autoarm_enabled=False)
        self.assertEqual(intent.resolve("我要離開約二十分鐘", ref, None, cfg=off).kind, "fact_or_chat")


class LeaveAutoArmCaptureTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_capture_target_and_ack(self):
        state = _state(self.tmp)
        client = FakeClient()
        monitor.handle_message(_msg("我要離開約二十分鐘"), _coach(), _BOOM, {"meta": {}}, None,
                               state, client, _cfg(), TZ)
        self.assertEqual(len(state.scheduled_promises), 1)
        p = state.scheduled_promises[0]
        self.assertAlmostEqual(p["target_ts"], NOW.timestamp() + 1200, delta=1)   # now＋20分＝絕對時刻
        self.assertTrue(p["behavior"])                                            # 有預設行為標籤（兌現 voice 用）
        self.assertEqual(len(client.sent), 1)
        self.assertIn("22:10", client.sent[0])                                    # 答應帶真實時刻、非空口

    def test_restatement_dedup_window(self):
        # 審查 MED：now+時距的 target 每次都不同秒、round() 精確去重永遠撞不上——重述同一個暫離會疊兩顆計時、
        # 到點連叫兩次。修＝暫離形 ±3 分鐘鄰近窗去重（仍照常答應、不疊計時）。
        state = _state(self.tmp)
        client = FakeClient()
        monitor.handle_message(_msg("我去睡半小時"), _coach(), _BOOM, {"meta": {}}, None,
                               state, client, _cfg(), TZ)
        later = NOW + timedelta(seconds=90)
        monitor.handle_message(_msg("欸我去睡半小時喔", when=later), _coach(), _BOOM, {"meta": {}}, None,
                               state, client, _cfg(), TZ)
        self.assertEqual(len(state.scheduled_promises), 1)   # 只有一顆計時（不疊）
        self.assertEqual(len(client.sent), 2)                # 兩次都自然答應

    def test_flag_off_no_capture(self):
        state = _state(self.tmp)
        client = FakeClient()
        cfg = _cfg(sched_leave_autoarm_enabled=False)
        monitor.handle_message(_msg("我要離開約二十分鐘"), SimpleNamespace(
            enabled=False), _BOOM, {"meta": {}}, None, state, client, cfg, TZ)
        self.assertFalse(getattr(state, "scheduled_promises", None))


class ReplyBridgeTest(unittest.TestCase):
    """回覆橋：使用者正在說話＋帳本有已到點未兌現 → 這一輪先兌現。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _armed(self, target_delta_sec, recur=""):
        state = _state(self.tmp)
        p = {"target_ts": NOW.timestamp() + target_delta_sec, "action": "叫我", "made_ts": NOW.timestamp() - 1200,
             "made_text": "二十分鐘後叫我", "fulfilled": False, "behavior": "叫你起來", "status": "pending"}
        if recur:
            p["recur"] = recur
        state.scheduled_promises = [p]
        return state

    def test_due_promise_fulfilled_on_reply(self):
        state = self._armed(-120)                       # 到點 2 分鐘（< grace＝生命迴圈在場延後窗）
        client = FakeClient()
        keeps = []
        monitor._promise_reply_bridge(client, state, _cfg(), _coach(keeps), NOW, TZ)
        self.assertEqual(len(client.sent), 1)
        self.assertTrue(state.scheduled_promises[0]["fulfilled"])
        self.assertEqual(state.scheduled_promises[0]["status"], "fulfilled")
        self.assertFalse(keeps[0]["late"])              # 剛到點＝正常守約語氣、不用道歉

    def test_overdue_promise_late_tone(self):
        state = self._armed(-600)                       # 逾期 10 分（> grace）→ 帶遲到致歉
        keeps = []
        monitor._promise_reply_bridge(FakeClient(), state, _cfg(), _coach(keeps), NOW, TZ)
        self.assertTrue(keeps[0]["late"])
        self.assertTrue(state.scheduled_promises[0]["fulfilled"])

    def test_not_due_untouched(self):
        state = self._armed(+240)                       # 還差 4 分 → 不動（留給 _promise_emit 到點發）
        client = FakeClient()
        monitor._promise_reply_bridge(client, state, _cfg(), _coach(), NOW, TZ)
        self.assertEqual(client.sent, [])
        self.assertFalse(state.scheduled_promises[0]["fulfilled"])

    def test_stale_over_ttl_untouched(self):
        state = self._armed(-30000)                     # 超過 TTL（6h）→ 不在對話裡翻舊帳（留給 _promise_emit 標 expired）
        client = FakeClient()
        monitor._promise_reply_bridge(client, state, _cfg(), _coach(), NOW, TZ)
        self.assertEqual(client.sent, [])
        self.assertFalse(state.scheduled_promises[0]["fulfilled"])

    def test_recur_daily_advances_not_fulfilled(self):
        state = self._armed(-120, recur="daily")
        client = FakeClient()
        monitor._promise_reply_bridge(client, state, _cfg(sched_recur_daily_enabled=True),
                                      _coach(), NOW, TZ)
        p = state.scheduled_promises[0]
        self.assertEqual(len(client.sent), 1)
        self.assertFalse(p.get("fulfilled"))            # 每天：發完推進明天、繼續 pending
        self.assertGreater(p["target_ts"], NOW.timestamp())

    def test_flag_off_noop(self):
        state = self._armed(-120)
        client = FakeClient()
        monitor._promise_reply_bridge(client, state, _cfg(promise_reply_bridge_enabled=False),
                                      _coach(), NOW, TZ)
        self.assertEqual(client.sent, [])
        self.assertFalse(state.scheduled_promises[0]["fulfilled"])

    def test_one_per_turn(self):
        state = _state(self.tmp)
        state.scheduled_promises = [
            {"target_ts": NOW.timestamp() - 300, "fulfilled": False, "behavior": "叫你", "status": "pending"},
            {"target_ts": NOW.timestamp() - 200, "fulfilled": False, "behavior": "提醒你", "status": "pending"}]
        client = FakeClient()
        monitor._promise_reply_bridge(client, state, _cfg(), _coach(), NOW, TZ)
        self.assertEqual(len(client.sent), 1)           # 一輪最多兌現一筆（不轟炸）
        self.assertEqual(sum(1 for p in state.scheduled_promises if p.get("fulfilled")), 1)

    def test_bridge_fires_inside_handle_message(self):
        # 端到端：22:12 使用者問「你有叫我嗎？」→ 橋先兌現、帳本路由再據實作答（截圖劇本收攏）
        state = self._armed(0)
        state.scheduled_promises[0]["target_ts"] = NOW.timestamp() - 120
        client = FakeClient()
        monitor.handle_message(_msg("你有叫我嗎？"), _coach(), _BOOM, {"meta": {}}, None,
                               state, client, _cfg(), TZ)
        self.assertTrue(state.scheduled_promises[0]["fulfilled"])
        self.assertGreaterEqual(len(client.sent), 2)    # 先守約訊息、再接地回覆
        self.assertIn("時間到了", client.sent[0])
        self.assertIn("grounded", client.sent[-1])      # 之後的回覆走帳本路由（剛兌現的那筆據實可查）


class StatusRerouteTest(unittest.TestCase):
    """狀態問句 → 帳本路由：didcall 空帳本也接地；timeup/remain 需帳本有活著的排程承諾。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_has_live_sched_promise(self):
        state = _state(self.tmp)
        self.assertFalse(monitor._has_live_sched_promise(state, NOW.timestamp()))
        state.scheduled_promises = [{"target_ts": NOW.timestamp() + 600, "fulfilled": False}]
        self.assertTrue(monitor._has_live_sched_promise(state, NOW.timestamp()))          # pending
        state.scheduled_promises = [{"target_ts": NOW.timestamp() - 600, "fulfilled": True,
                                     "fulfilled_ts": NOW.timestamp() - 600}]
        self.assertTrue(monitor._has_live_sched_promise(state, NOW.timestamp()))          # 剛完結（2h 內）
        state.scheduled_promises = [{"target_ts": NOW.timestamp() - 90000, "fulfilled": True,
                                     "fulfilled_ts": NOW.timestamp() - 90000}]
        self.assertFalse(monitor._has_live_sched_promise(state, NOW.timestamp()))         # 太舊＝不搶

    def test_timeup_grounds_when_live_promise(self):
        state = _state(self.tmp)
        state.scheduled_promises = [{"target_ts": NOW.timestamp() + 240, "fulfilled": False,
                                     "behavior": "叫你起來", "status": "pending"}]
        client = FakeClient()
        monitor.handle_message(_msg("時間到了沒"), _coach(), _BOOM, {"meta": {}}, None,
                               state, client, _cfg(), TZ)
        self.assertEqual(len(client.sent), 1)
        self.assertIn("grounded", client.sent[0])       # 走了帳本路由（coach.reply 接到帳本 facts）
        self.assertIn("21:50", client.sent[0])          # facts 帶「此刻真的是 21:50」錨（真時鐘、非心算）

    def test_didcall_grounds_even_with_empty_ledger(self):
        state = _state(self.tmp)
        client = FakeClient()
        monitor.handle_message(_msg("你有叫我嗎？"), _coach(), _BOOM, {"meta": {}}, None,
                               state, client, _cfg(), TZ)
        self.assertEqual(len(client.sent), 1)
        self.assertIn("grounded", client.sent[0])       # 空帳本也接地（誠實說沒記著、不心算倒數）

    def test_timeup_empty_ledger_not_hijacked(self):
        # 沒有任何計時脈絡時「到了沒」可能在問包裹/人 → 不搶（gate 條件本身單測；避免 fact_or_chat 重依賴）
        state = _state(self.tmp)
        self.assertEqual(selfstate.promise_status_kind("到了沒"), "timeup")
        self.assertFalse(monitor._has_live_sched_promise(state, NOW.timestamp()))


if __name__ == "__main__":
    unittest.main()
