"""🌊 連發合併：把相鄰夠密集的數則 owner 訊息視為同一邏輯輪次、整體回一次（不再每則各回一次）。
純函式（分群/合成/是否發完）＋整合（relate 派發：合併、保留尾群、offset 消費才推進＝不漏訊息）。
旗標關＝逐則派發＝現行行為。"""

import os
import time
import unittest
from types import SimpleNamespace
from unittest import mock
from zoneinfo import ZoneInfo

from telegram_monitor import config, monitor

TZ = ZoneInfo("Asia/Taipei")


class BurstWindowDefaultTest(unittest.TestCase):
    """🌊 連發合併窗預設＝閱讀時間（截圖：6s 太短把『你為什麼沒有主動報告』＋『你失職』切兩輪）。"""

    def test_default_window_is_reading_time(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            for k in ("BURST_COALESCE_SEC", "BURST_TURN_GAP_SEC", "BURST_TURN_MAX_SPAN_SEC",
                      "BURST_MAX_WAIT_SEC"):
                os.environ.pop(k, None)
            cfg = config.Config.load(env_file="/dev/null")
        self.assertEqual(cfg.burst_coalesce_sec, 10.0)   # 等使用者打完這串想法再整段回（~10s 閱讀時間）
        self.assertEqual(cfg.burst_turn_gap_sec, 20.0)   # 已在同次 fetch 的未回訊息，用較寬 turn 邊界整體理解
        self.assertEqual(cfg.burst_turn_max_span_sec, 60.0)  # 多則短間隔可延續一分鐘，仍受 max_msgs=12 封頂
        self.assertEqual(cfg.burst_max_wait_sec, 60.0)   # 持續輸入最多等一分鐘；單則仍由 10s settle 決定

    def test_window_env_tunable_back_to_old(self):
        with mock.patch.dict(os.environ, {"BURST_COALESCE_SEC": "6"}):
            self.assertEqual(config.Config.load(env_file="/dev/null").burst_coalesce_sec, 6.0)  # 嫌慢一鍵回舊值

    def test_turn_gap_and_span_env_override(self):
        with mock.patch.dict(os.environ, {"BURST_TURN_GAP_SEC": "17.5",
                                          "BURST_TURN_MAX_SPAN_SEC": "45"}):
            cfg = config.Config.load(env_file="/dev/null")
        self.assertEqual(cfg.burst_turn_gap_sec, 17.5)
        self.assertEqual(cfg.burst_turn_max_span_sec, 45.0)


def _tu(uid, text=None, sticker=None, date=None, chat=1, kind=None):
    """造一則 update。kind='reaction'/'edited' 走特殊鍵；否則一般 message（text 或 sticker）。"""
    if kind == "reaction":
        return {"update_id": uid, "message_reaction": {"chat": {"id": chat}, "date": date}}
    m = {"chat": {"id": chat}}
    if text is not None:
        m["text"] = text
    if sticker is not None:
        m["sticker"] = sticker
    if date is not None:
        m["date"] = date
    return {"update_id": uid, ("edited_message" if kind == "edited" else "message"): m}


class GroupBurstsTest(unittest.TestCase):
    def test_dense_texts_merge_into_one(self):
        ups = [_tu(1, "在嗎", date=1000), _tu(2, "我想問", date=1001), _tu(3, "水管", date=1002)]
        g = monitor.group_bursts(ups, "1", 2.5, 12)
        self.assertEqual(len(g), 1)
        self.assertEqual(g[0]["type"], "text")
        self.assertEqual(g[0]["max_update_id"], 3)

    def test_gap_splits(self):
        ups = [_tu(1, "早安", date=1000), _tu(2, "對了", date=1010)]      # 隔 10s > 門檻
        self.assertEqual(len(monitor.group_bursts(ups, "1", 2.5, 12)), 2)

    def test_screenshot_two_rapid_msgs_merge_at_widened_gap(self):
        # 截圖根因：「不要再聯想了」「我等下會讓你睡著一下」鄰近兩則隔 ~5s——舊 gap=4 切成兩輪(兩種口吻)，
        # 放寬到 6 併成一輪一口氣回。
        ups = [_tu(1, "不要再聯想了", date=1000), _tu(2, "我等下會讓你睡著一下", date=1005)]
        self.assertEqual(len(monitor.group_bursts(ups, "1", 4.0, 12)), 2)   # 舊預設：分兩輪（重現 bug）
        self.assertEqual(len(monitor.group_bursts(ups, "1", 6.0, 12)), 1)   # 舊新預設：併一輪

    def test_screenshot_question_then_accusation_merge_at_10s(self):
        # 截圖根因：「你為什麼沒有主動報告」＋「你失職」隔 ~8s——6s 窗切成兩輪（第二輪重複道歉、莫名收尾），
        # 放寬到 10s（閱讀時間）併成一輪、整段一口氣回（使用者期望的自然回應）。
        ups = [_tu(1, "你為什麼沒有主動報告", date=1000), _tu(2, "你失職", date=1008)]
        self.assertEqual(len(monitor.group_bursts(ups, "1", 6.0, 12)), 2)    # 舊預設 6s：分兩輪（重現 bug）
        g = monitor.group_bursts(ups, "1", 10.0, 12)                          # 新預設 10s：併一輪
        self.assertEqual(len(g), 1)
        self.assertEqual(monitor.build_coalesced_update(g[0])["message"]["text"], "你為什麼沒有主動報告\n你失職")

    def test_command_is_hard_boundary(self):
        ups = [_tu(1, "你好", date=1000), _tu(2, "/status", date=1000), _tu(3, "再問", date=1000)]
        g = monitor.group_bursts(ups, "1", 2.5, 12)
        self.assertEqual([x["type"] for x in g], ["text", "command", "text"])   # 指令自成群、切斷前後

    def test_edited_and_reaction_and_media_are_hard_boundaries(self):
        ups = [_tu(1, "a", date=1000), _tu(2, "b", date=1000, kind="edited"),
               _tu(3, None, sticker=None, date=1000), _tu(4, "c", date=1000, kind="reaction")]
        g = monitor.group_bursts(ups, "1", 2.5, 12)
        self.assertEqual([x["type"] for x in g], ["text", "edited", "media", "reaction"])

    def test_foreign_chat_isolated(self):
        ups = [_tu(1, "a", date=1000, chat=999)]
        self.assertEqual(monitor.group_bursts(ups, "1", 2.5, 12)[0]["type"], "foreign")

    def test_sticker_only_and_mixed(self):
        st = {"file_id": "F", "emoji": "🥰"}
        self.assertEqual(monitor.group_bursts([_tu(1, sticker=st, date=1000), _tu(2, sticker=st, date=1001)],
                                              "1", 2.5, 12)[0]["type"], "sticker_only")
        self.assertEqual(monitor.group_bursts([_tu(1, "看這個", date=1000), _tu(2, sticker=st, date=1001)],
                                              "1", 2.5, 12)[0]["type"], "mixed")

    def test_max_msgs_caps_group(self):
        ups = [_tu(i, "x", date=1000 + i * 0.01) for i in range(5)]
        g = monitor.group_bursts(ups, "1", 2.5, 3)
        self.assertTrue(all(len(x["updates"]) <= 3 for x in g))
        self.assertEqual(sum(len(x["updates"]) for x in g), 5)        # 不漏任何一則

    def test_total_span_cap_stops_chain_merge(self):
        # 每一個相鄰 gap 都只有 15s（小於 turn gap 20s），但 0→30s 已超過一輪總跨度；
        # 不可因鏈式相鄰而把整段離線 backlog 永遠黏成一群。
        ups = [_tu(1, "第一件", date=1000), _tu(2, "第二件", date=1015), _tu(3, "第三件", date=1030)]
        groups = monitor.group_bursts(ups, "1", 20.0, 12, max_span_sec=20.0)
        self.assertEqual([len(g["updates"]) for g in groups], [2, 1])
        self.assertEqual([[u["update_id"] for u in g["updates"]] for g in groups], [[1, 2], [3]])

    def test_missing_date_does_not_merge(self):
        ups = [_tu(1, "a"), _tu(2, "b")]                              # 都沒 date → 不併（保守）
        self.assertEqual(len(monitor.group_bursts(ups, "1", 2.5, 12)), 2)

    def test_covers_every_update(self):
        ups = [_tu(1, "a", date=1000), _tu(2, "/k 5", date=1000), _tu(3, "b", date=1000)]
        g = monitor.group_bursts(ups, "1", 2.5, 12)
        self.assertEqual(sum(len(x["updates"]) for x in g), 3)        # 絕不漏訊息


class BuildCoalescedTest(unittest.TestCase):
    def test_joins_text_takes_last_meta(self):
        ups = [_tu(1, "在嗎", date=1000), _tu(2, "水管", date=1002)]
        g = monitor.group_bursts(ups, "1", 2.5, 12)[0]
        synth = monitor.build_coalesced_update(g)
        self.assertEqual(synth["message"]["text"], "在嗎\n水管")
        self.assertEqual(synth["message"]["date"], 1002)             # 末則時刻（last_user_msg_ts 不被舊 date 拉低）
        self.assertEqual(synth["update_id"], 2)

    def test_single_element_passthrough(self):
        g = {"type": "text", "updates": [_tu(7, "嗨", date=1000)], "max_update_id": 7}
        self.assertEqual(monitor.build_coalesced_update(g)["message"]["text"], "嗨")

    def test_mixed_transaction_preserves_order_across_failed_delivery_retry(self):
        sticker = {"file_id": "S1", "emoji": "🙂"}
        group = {
            "type": "mixed",
            "updates": [
                _tu(300, "第一段", date=1000),
                _tu(301, sticker=sticker, date=1001),
                _tu(302, "第二段", date=1002),
            ],
        }
        update = monitor.build_coalesced_update(group)
        state = SimpleNamespace(
            tg_update_offset=0,
            burst_delivered_update_id=0,
            burst_delivered_update_ids=[],
            convo_history=[],
            mood_last_report=None,
            event_order=[],
            save=lambda: None,
        )
        cfg = SimpleNamespace(dry_run=True, burst_one_answer_enabled=True)

        class FlakyClient:
            dry_run = True

            def __init__(self):
                self.ok = False
                self.sent = []

            def send(self, text):
                self.sent.append(text)
                return self.ok

        client = FlakyClient()

        def observe(st, _update, target, _cfg, client=None, coach=None):
            target.event_order.append("sticker:" + (st.get("file_id") or ""))
            return st.get("emoji") or "", "positive"

        def handle(part, _coach, _reader, _data, _snap, target, out, _cfg, _tz):
            text = part["message"]["text"]
            target.event_order.append("text:" + text)
            out.send("處理：" + text)

        with mock.patch.object(monitor, "sticker_signal", side_effect=observe), \
                mock.patch.object(monitor, "_handle_message_inner", side_effect=handle):
            with self.assertRaises(monitor.BurstDeliveryError):
                monitor._handle_burst_route_transaction(
                    update, None, None, {}, None, state, client, cfg, TZ)
            self.assertEqual(state.event_order, [])                 # 失敗 clone 不可污染 real state
            self.assertEqual(state.burst_delivered_update_ids, [])

            client.ok = True
            monitor._handle_burst_route_transaction(
                update, None, None, {}, None, state, client, cfg, TZ)
            sent_after_success = len(client.sent)
            monitor._handle_burst_route_transaction(
                update, None, None, {}, None, state, client, cfg, TZ)

        self.assertEqual(state.event_order,
                         ["text:第一段", "sticker:S1", "text:第二段"])
        self.assertEqual(set(state.burst_delivered_update_ids), {300, 301, 302})
        self.assertEqual(len(client.sent), sent_after_success)       # exact receipt 命中，不重送／重套貼圖


class SettleTest(unittest.TestCase):
    def _g(self, *dates, n=None, typ="text"):
        ups = [_tu(i + 1, "x", date=d) for i, d in enumerate(dates)]
        return {"type": typ, "updates": ups, "max_update_id": len(dates)}

    def test_settled_by_silence(self):
        g = self._g(1000)
        self.assertTrue(monitor._burst_settled(g, now=1010, gap_sec=2.5, max_wait_sec=6, max_msgs=12, pending_since=0))

    def test_single_message_settles_at_gap_even_when_max_wait_is_one_minute(self):
        g = self._g(1000)
        self.assertTrue(monitor._burst_settled(
            g, now=1010.1, gap_sec=10.0, max_wait_sec=60.0, max_msgs=12, pending_since=1000.0))

    def test_not_settled_when_recent(self):
        g = self._g(1000)
        self.assertFalse(monitor._burst_settled(g, now=1001, gap_sec=2.5, max_wait_sec=6, max_msgs=12, pending_since=0))

    def test_settled_by_max_msgs(self):
        g = self._g(1000, 1000, 1000)
        self.assertTrue(monitor._burst_settled(g, now=1000, gap_sec=2.5, max_wait_sec=6, max_msgs=3, pending_since=0))

    def test_settled_by_max_wait(self):
        g = self._g(1000)
        self.assertTrue(monitor._burst_settled(g, now=1000.5, gap_sec=2.5, max_wait_sec=6, max_msgs=12, pending_since=994))

    def test_growable_types(self):
        self.assertTrue(monitor._burst_growable({"type": "text"}))
        self.assertFalse(monitor._burst_growable({"type": "command"}))


class RelateDispatchTest(unittest.TestCase):
    """整合：relate 互動相在旗標開時把 burst 合併成一次 handle_message、不漏訊息、offset 消費才推進。"""

    def _relate(self, client, state, cfg):
        phases = monitor._life_phases(None, client, state, cfg, TZ, None, True, None)
        return next(p.fn for p in phases if p.name == "互動")

    def _state(self):
        return SimpleNamespace(tg_update_offset=0, _burst_pending_since=0.0, _burst_pending_id=None,
                               convo_history=[], save=lambda: None)

    def _cfg(self, **kw):
        d = dict(burst_coalesce_enabled=True, burst_coalesce_sec=2.5, burst_max_wait_sec=6.0,
                 burst_max_msgs=12, telegram_chat_id="1", dry_run=False, heartbeat_interval_min=10, mood_gain=1.0)
        d.update(kw)
        return SimpleNamespace(**d)

    def _client(self, batches):
        q = list(batches)
        return SimpleNamespace(dry_run=False, sent=[], send=lambda t: True,
                               get_updates=lambda offset=0, timeout=0: (q.pop(0) if q else []),
                               _interrupt=None)

    def test_dense_burst_settled_dispatches_once_joined(self):
        calls = []
        orig = monitor.handle_message
        monitor.handle_message = lambda u, *a, **k: calls.append(u)
        try:
            burst = [_tu(1, "在嗎", date=1000), _tu(2, "我想問", date=1001), _tu(3, "水管的事", date=1002)]
            client, state, cfg = self._client([burst]), self._state(), self._cfg()
            self._relate(client, state, cfg)({"data": {}, "snap": None})
        finally:
            monitor.handle_message = orig
        self.assertEqual(len(calls), 1)                              # 三句合併成「一次」回應
        self.assertEqual(calls[0]["message"]["text"], "在嗎\n我想問\n水管的事")
        self.assertEqual(state.tg_update_offset, 4)                  # 消費後 offset 推進過全部

    def test_restart_receipt_prunes_delivered_prefix_before_new_tail_is_grouped(self):
        # ids 1–2 已經單次送達且 receipt 落盤，但 outer offset 仍停在 1；重啟後 id3
        # 緊鄰到達。即使 client 忽略 offset 回傳 1/2/3，也只能派發真正的新 id3。
        batch = [_tu(1, "已送第一則", date=1000), _tu(2, "已送第二則", date=1001),
                 _tu(3, "重啟後的新訊息", date=1002)]
        offsets, calls = [], []

        class Client:
            dry_run = False
            _interrupt = None

            def get_updates(self, offset=0, timeout=0):
                offsets.append(offset)
                return list(batch)                                 # 故意模擬不遵守 offset 的代理／fixture

            def send(self, _text):
                return True

        state = self._state()
        state.tg_update_offset = 1
        state.burst_delivered_update_id = 2
        state.burst_delivered_update_ids = [1, 2]             # exact receipts 才能證明 prefix 兩則都已送達
        cfg = self._cfg(burst_coalesce_sec=2.5, burst_turn_gap_sec=20.0,
                        burst_turn_max_span_sec=60.0, burst_max_wait_sec=6.0)

        with mock.patch.object(monitor, "handle_message", side_effect=lambda u, *a, **k: calls.append(u)):
            self._relate(Client(), state, cfg)({"data": {}, "snap": None})

        self.assertEqual(offsets, [3])
        self.assertEqual([u.get("update_id") for u in calls], [3])
        self.assertEqual([u["message"]["text"] for u in calls], ["重啟後的新訊息"])
        self.assertEqual(state.tg_update_offset, 4)

    def test_initial_snapshot_uses_wider_turn_gap_without_extra_wait(self):
        # 兩則都已在同一次 getUpdates snapshot，bot 中間還沒回過：即使相隔 15s > settle 10s，
        # 仍應依 turn gap 20s 當成同一個 user turn；settle 仍只負責「末則安靜多久才答」。
        calls = []
        batch = [_tu(1, "有點忙？", date=1000),
                 _tu(2, "不要使用我一樣的貼圖", date=1015)]
        client, state = self._client([batch]), self._state()
        cfg = self._cfg(burst_coalesce_sec=10.0, burst_turn_gap_sec=20.0, burst_max_wait_sec=20.0)

        with mock.patch.object(monitor, "handle_message", side_effect=lambda u, *a, **k: calls.append(u)):
            self._relate(client, state, cfg)({"data": {}, "snap": None})

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].get("burst_n"), 2)
        self.assertEqual(calls[0]["message"]["text"], "有點忙？\n不要使用我一樣的貼圖")
        self.assertEqual(state.tg_update_offset, 3)

    def test_initial_snapshot_five_messages_merge_across_24_second_turn(self):
        # 使用者連續打五則、每則只隔 6s：總跨度 24s 已超過舊 20s span，但仍是 bot 尚未回過的同一輪。
        # turn gap=20 判相鄰連續，span=60 容納整串；max_msgs=12 仍是硬上限。
        texts = ["有點忙？", "不要使用我一樣的貼圖", "也不要逐句重複我",
                 "先回真正不同的重點", "整段只回一次"]
        batch = [_tu(i + 1, text, date=1000 + i * 6) for i, text in enumerate(texts)]
        calls = []
        client, state = self._client([batch]), self._state()
        cfg = self._cfg(burst_coalesce_sec=10.0, burst_turn_gap_sec=20.0,
                        burst_turn_max_span_sec=60.0, burst_max_wait_sec=60.0, burst_max_msgs=12)

        with mock.patch.object(monitor, "handle_message", side_effect=lambda u, *a, **k: calls.append(u)):
            self._relate(client, state, cfg)({"data": {}, "snap": None})

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].get("burst_n"), 5)
        self.assertEqual(calls[0].get("burst_texts"), texts)
        self.assertEqual(calls[0]["message"]["text"], "\n".join(texts))
        self.assertEqual(state.tg_update_offset, 6)

    def test_three_through_twelve_short_gap_messages_each_dispatch_once(self):
        now = time.time()
        for n in range(3, 13):
            with self.subTest(n=n):
                texts = [f"第{i}則" for i in range(1, n + 1)]
                batch = [_tu(i, text, date=now - 100 + (i - 1) * 5)
                         for i, text in enumerate(texts, 1)]
                calls = []
                client, state = self._client([batch]), self._state()
                cfg = self._cfg(burst_coalesce_sec=10.0, burst_turn_gap_sec=20.0,
                                burst_turn_max_span_sec=60.0, burst_max_wait_sec=60.0,
                                burst_max_msgs=12)
                with mock.patch.object(monitor, "handle_message",
                                       side_effect=lambda u, *a, **k: calls.append(u)):
                    self._relate(client, state, cfg)({"data": {}, "snap": None})
                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0].get("burst_n"), n)
                self.assertEqual(calls[0].get("burst_texts"), texts)
                self.assertEqual(state.tg_update_offset, n + 1)

    def test_recent_tail_held_not_dispatched_offset_unchanged(self):
        calls = []
        orig = monitor.handle_message
        monitor.handle_message = lambda u, *a, **k: calls.append(u)
        try:
            now = time.time()
            burst = [_tu(1, "在嗎", date=now), _tu(2, "還在打字", date=now)]   # date≈now → 未靜默 → 尾群保留
            client, state, cfg = self._client([burst]), self._state(), self._cfg()
            self._relate(client, state, cfg)({"data": {}, "snap": None})
        finally:
            monitor.handle_message = orig
        self.assertEqual(calls, [])                                  # 還沒發完 → 先不回（debounce 跨圈）
        self.assertEqual(state.tg_update_offset, 0)                  # offset 不推進＝下圈 Telegram 重送（不漏）
        self.assertTrue(state._burst_pending_since > 0)              # 記了首見時刻（給 max_wait）

    def test_split_batch_interrupt_does_not_dispatch_second_update_twice(self):
        # 同一次 getUpdates 已經拿到 u1/u2，但兩則距 13s 被 burst gap=10s 切成兩群。
        # 回 u1 的多泡泡途中會再 poll；這個 poll 必須從「當初整批 fetch 的最大 id+1」開始，
        # 不能只從 u1 的 id+1 重拉已在本地 flush 清單裡的 u2，否則 u2 會巢狀處理一次、
        # 外層 for g in flush 又處理一次（現行可穩定重現 [u1,u2,u2]）。
        batch = [_tu(1, "有點忙？", date=1000),
                 _tu(2, "不要使用我一樣的貼圖", date=1013)]
        offsets, calls = [], []

        class Client:
            dry_run = False

            def __init__(self):
                self._interrupt = None
                self.sent = []

            def get_updates(self, offset=0, timeout=0):
                offsets.append(offset)
                # 鏡像 Telegram offset 語意：第一拍 offset=0 回整批；之後只回 id>=offset。
                return [u for u in batch if u.get("update_id", 0) >= offset]

            def send(self, text):
                self.sent.append(text)
                return True

            def send_typing(self):
                pass

        client, state = Client(), self._state()
        cfg = self._cfg(burst_coalesce_sec=10.0, burst_turn_gap_sec=10.0, burst_max_wait_sec=20.0,
                        interrupt_statement_enabled=True,
                        interrupt_wave_guard_enabled=True, interrupt_wave_sec=12.0,
                        interrupt_continuation_defer=True, interrupt_coalesce_enabled=True,
                        interrupt_rewrite_enabled=False, hostile_converge_enabled=True)

        def handle(u, *args, **kwargs):
            calls.append(u.get("update_id"))
            # 兩顆泡泡才會在第二顆前觸發 _BurstInterrupt.poll。
            monitor._say(client, "第一句。第二句。")

        with mock.patch.object(monitor, "handle_message", side_effect=handle), \
             mock.patch.object(monitor, "_sleep"):
            self._relate(client, state, cfg)({"data": {}, "snap": None})

        self.assertEqual(calls, [1, 2])                            # u2 只能由外層 flush 處理一次
        self.assertEqual(offsets[0], 0)                            # 主拉取從 state offset 開始
        self.assertGreater(offsets[1], 2)                          # 途中 poll 必須跳過整批 fetch max=2
        self.assertEqual(state.tg_update_offset, 3)

    def test_held_tail_disables_interrupt_so_new_update_cannot_skip_it(self):
        # u1 已閉合、u2 剛到仍要 hold；回 u1 的多泡泡途中若錯開 interrupt，會抓到 u3 並把 offset
        # 推到 4，讓尚未派發的 u2 永久被越過。只要存在 held tail，前群就不得掛 interrupt。
        now = time.time()
        initial = [_tu(1, "先前已說完的事", date=now - 100),
                   _tu(2, "我還在打這一句", date=now)]
        newcomer = _tu(3, "真正後來的新訊息", date=now + 1)
        offsets, calls = [], []

        class Client:
            dry_run = False

            def __init__(self):
                self._interrupt = None
                self.sent = []

            def get_updates(self, offset=0, timeout=0):
                offsets.append(offset)
                return initial if len(offsets) == 1 else [newcomer]

            def send(self, text):
                self.sent.append(text)
                return True

            def send_typing(self):
                pass

        client, state = Client(), self._state()
        cfg = self._cfg(burst_coalesce_sec=10.0, burst_turn_gap_sec=20.0, burst_max_wait_sec=20.0,
                        interrupt_statement_enabled=True,
                        interrupt_wave_guard_enabled=True, interrupt_wave_sec=12.0,
                        interrupt_continuation_defer=True, interrupt_coalesce_enabled=True,
                        interrupt_rewrite_enabled=False, hostile_converge_enabled=True)

        def handle(u, *args, **kwargs):
            calls.append(u.get("update_id"))
            monitor._say(client, "第一句。第二句。")

        with mock.patch.object(monitor, "handle_message", side_effect=handle), \
             mock.patch.object(monitor, "_sleep"):
            self._relate(client, state, cfg)({"data": {}, "snap": None})

        self.assertEqual(calls, [1])                              # u2 保留、u3 也留給下一圈按序處理
        self.assertEqual(offsets, [0])                            # u1 回覆途中完全沒有 interrupt poll
        self.assertEqual(state.tg_update_offset, 2)               # 只 ack u1，絕不可越過 held u2
        self.assertEqual(state._burst_pending_id, 2)

    def test_new_tail_does_not_inherit_expired_timer_from_previous_group(self):
        # 12 則達 max_msgs 先閉合，第 13 則是剛到的新尾群。舊 pending id=1 即使已等超過
        # max_wait，也只屬於前群；若錯套給 id13，id13 會同一拍被立即 flush，拆成兩輪回答。
        now = time.time()
        batch = [_tu(i, f"第{i}則", date=now - (13 - i)) for i in range(1, 14)]
        calls = []
        client = self._client([batch])
        state = self._state()
        state._burst_pending_id = 1
        state._burst_pending_since = now - 120
        cfg = self._cfg(burst_coalesce_sec=10.0, burst_turn_gap_sec=20.0,
                        burst_turn_max_span_sec=60.0, burst_max_wait_sec=60.0, burst_max_msgs=12)

        with mock.patch.object(monitor, "handle_message", side_effect=lambda u, *a, **k: calls.append(u)):
            self._relate(client, state, cfg)({"data": {}, "snap": None})

        self.assertEqual([u.get("burst_n") for u in calls], [12])  # 只派滿 12 的前群
        self.assertEqual(state.tg_update_offset, 13)                 # id13 仍可由 offset=13 重拉
        self.assertEqual(state._burst_pending_id, 13)
        self.assertGreaterEqual(state._burst_pending_since, now)

    def _nested_interrupt_case(self, fail_outer):
        now = time.time()
        outer = _tu(1, "外層訊息", date=now - 100)
        nested = _tu(2, "等等，這是什麼意思？", date=now)
        batches = [[outer], [nested]]
        calls, offsets, saved_offsets = [], [], []

        class Client:
            dry_run = False

            def __init__(self):
                self._interrupt = None
                self.sent = []

            def get_updates(self, offset=0, timeout=0):
                offsets.append(offset)
                return batches.pop(0) if batches else []

            def send(self, text):
                self.sent.append(text)
                return True

            def send_typing(self):
                pass

        client = Client()
        state = self._state()
        state.save = lambda: saved_offsets.append(state.tg_update_offset)
        cfg = self._cfg(burst_coalesce_sec=10.0, burst_turn_gap_sec=20.0,
                        burst_turn_max_span_sec=60.0, burst_max_wait_sec=60.0,
                        interrupt_statement_enabled=True, interrupt_wave_guard_enabled=False,
                        interrupt_continuation_defer=True, interrupt_coalesce_enabled=True,
                        interrupt_rewrite_enabled=False, hostile_converge_enabled=True)

        def handle(u, *args, **kwargs):
            uid = u.get("update_id")
            calls.append(uid)
            if uid == 2:
                # 即使 nested handler 此刻保存 state，offset 仍必須停在 parent base；不能把高水位提早落盤。
                state.save()
                return
            monitor._say(client, "外層第一句。外層第二句。")
            if fail_outer:
                raise RuntimeError("outer failed after nested success")

        monitor._TURN["bubbles"] = None
        with mock.patch.object(monitor, "handle_message", side_effect=handle), \
             mock.patch.object(monitor, "_sleep"):
            if fail_outer:
                with self.assertRaisesRegex(RuntimeError, "outer failed"):
                    self._relate(client, state, cfg)({"data": {}, "snap": None})
            else:
                self._relate(client, state, cfg)({"data": {}, "snap": None})
        return state, calls, offsets, saved_offsets

    def test_nested_success_outer_failure_does_not_skip_parent(self):
        state, calls, offsets, saved = self._nested_interrupt_case(fail_outer=True)
        self.assertEqual(calls, [1, 2])
        self.assertEqual(offsets, [0, 2])
        self.assertEqual(saved, [0, 0])                             # handler save＋exact receipt durable save 都不越過 parent 洞
        self.assertEqual(state.burst_delivered_update_ids, [2])
        self.assertEqual(state.tg_update_offset, 0)                 # 下圈仍從 parent 重拉，不永久漏掉 id1

    def test_nested_offset_commits_with_successful_parent(self):
        state, calls, offsets, saved = self._nested_interrupt_case(fail_outer=False)
        self.assertEqual(calls, [1, 2])
        self.assertEqual(offsets, [0, 2])
        self.assertEqual(saved, [0, 0, 3])                          # nested 處理/收據均存 base；parent 成功才 commit
        self.assertEqual(state.burst_delivered_update_ids, [2])
        self.assertEqual(state.tg_update_offset, 3)

    def test_flag_off_dispatches_per_message(self):
        calls = []
        orig = monitor.handle_message
        monitor.handle_message = lambda u, *a, **k: calls.append(u)
        try:
            burst = [_tu(1, "a", date=1000), _tu(2, "b", date=1001)]
            client, state, cfg = self._client([burst]), self._state(), self._cfg(burst_coalesce_enabled=False)
            self._relate(client, state, cfg)({"data": {}, "snap": None})
        finally:
            monitor.handle_message = orig
        self.assertEqual(len(calls), 2)                              # 關＝逐則派發＝現行行為
        self.assertEqual(state.tg_update_offset, 3)


class InterruptOffsetCommitTest(unittest.TestCase):
    """插話 update 的 offset 只能在 handle_fn 成功後 commit；回覆中途崩潰要留給下次重試。"""

    def test_handler_failure_does_not_advance_past_failed_update(self):
        failed = _tu(11, "這則處理到一半崩潰", date=1000)
        state = SimpleNamespace(tg_update_offset=10)
        cfg = SimpleNamespace(interrupt_coalesce_enabled=True,
                              burst_coalesce_sec=10.0, burst_max_msgs=12,
                              telegram_chat_id="1")
        client = SimpleNamespace(dry_run=False)

        def boom(_update):
            raise RuntimeError("reply failed")

        interrupt = monitor._BurstInterrupt(client, state, cfg, floor=10, handle_fn=boom)
        with self.assertRaisesRegex(RuntimeError, "reply failed"):
            interrupt.handle([failed])

        self.assertEqual(state.tg_update_offset, 10)               # 失敗 update 未 ack，下次可重拉


if __name__ == "__main__":
    unittest.main()
