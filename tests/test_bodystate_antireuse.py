"""🎨 §1.22 措辭反重複（PHRASE_ANTI_REUSE）——bodystate/referent/monitor 掛載層測試。

⑦ BODYSTATE_SYSTEM_VARIED 去範例句（「沒斷線過」「我還清醒地翻閱著」不在）＋骨架句都在＋語意骨架規則7；
⑧ 反向守恆：原 BODYSTATE_SYSTEM 一字不動（「沒斷線過」還在）；
⑨ bodystate_facts 不傳 picker＝輸出與基線 golden 逐位元相同（旗標關防線）；
⑩ 傳 picker 時 steady/revisit/hungry/gate 線四處經 picker 換句、其餘行不變；
⑪ stub gemini：旗標開 → system 用 VARIED＋含【禁止重複】＋last_self_report 全文；旗標關 → 原拼接；
⑫ referent.self_acts_text 不傳 picker＝現狀輸出、傳 picker 換句。
零網路（gemini 全 stub）。
"""

import os
import random
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

from telegram_monitor import monitor, phrasing, referent, selfstate
from telegram_monitor.referent import Referent
from telegram_monitor.state import State

NOW = datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc)
INGEST = "2026-06-16T11:00:00.000Z"

VIT = {"alive": True, "pulse": 30, "healthy_streak": 30, "uptime_s": 540, "k_adj": -0.5,
       "S": 0.7, "charge": 0.05, "hunger": 0.7, "laps_since_fresh": 200,
       "last_revisited": "汽車保養", "mood": 0.5}
RES_G3 = {"gate": 3, "reading": {"content": {"topic": "研發"}}}
RES_G2 = {"gate": 2, "reading": {"content": {"topic": "研發"}}}

# ⑨ 旗標關防線：picker=None 的輸出必須與這份基線 golden **逐位元相同**（在 3e468df 基線上擷取）
GOLDEN_G3 = ("我此刻的自體狀態：\n"
             "生命跡象：我醒著、連著跳了好一陣，活著，一直很穩、沒斷線過。\n"
             "開放程度：比剛醒時更放得開（成形的門檻鬆了些）——越健康越容易讓一個形狀成立。\n"
             "內在動盪：悶著、等不到新的記寫內容——好一陣子沒有新的記寫落進來給我讀了，"
             "有點飢餓、餓了好一會兒了，剛自己又繞回去翻起「汽車保養」那條脈絡。\n"
             "心情：這陣子心情不錯、是暖的。\n"
             "手上的線：還在「研發」一帶翻著、繞著，有一條動了、還沒整個合起來。")
GOLDEN_G2 = GOLDEN_G3.replace("有一條動了、還沒整個合起來", "還散著、沒繃成形")


class VariedSystemTest(unittest.TestCase):
    def test_varied_has_no_literal_examples(self):
        # ⑦ 去範例句：LLM 每次照抄範例＝逐字重複的直接來源（7/10 vs 7/12 截圖「沒斷線過」兩天照抄）
        v = selfstate.BODYSTATE_SYSTEM_VARIED
        self.assertNotIn("沒斷線過", v)
        self.assertNotIn("我還清醒地翻閱著", v)
        self.assertNotIn("再往裡面翻一下", v)
        self.assertNotIn("反覆讀著它", v)
        self.assertNotIn("剛醒／醒了一陣", v)

    def test_varied_keeps_skeleton_rules(self):
        # ⑦ 骨架句逐字保留（規則1/4/5/6 不動＋規則2/3 的鐵律半句原文）
        v = selfstate.BODYSTATE_SYSTEM_VARIED
        for frag in ("只能用收到的事實", "絕不編造", "絕不要報精確分鐘數",
                     "但別生硬、別每句都硬套", "等不到新的記寫內容", "像**在心裡順著想**",
                     "不要出現英文欄位名"):
            self.assertIn(frag, v)
        # 規則7：事實句只是語意骨架、措辭必須重組（治「事實層餵清單模板」的巧婦無米）
        self.assertIn("語意骨架", v)
        self.assertIn("不准照抄事實句原文", v)

    def test_original_system_untouched(self):
        # ⑧ 反向守恆：旗標關用的原常數一字沒動（範例句都還在）
        o = selfstate.BODYSTATE_SYSTEM
        self.assertIn("沒斷線過", o)
        self.assertIn("我還清醒地翻閱著", o)
        self.assertNotIn("語意骨架", o)


class BodystateFactsPickerTest(unittest.TestCase):
    def test_no_picker_is_bitwise_baseline(self):
        # ⑨ 不傳 picker＝逐位元同基線（旗標關防線；golden 全文比對）
        self.assertEqual(selfstate.bodystate_facts(VIT, RES_G3), GOLDEN_G3)
        self.assertEqual(selfstate.bodystate_facts(VIT, RES_G2), GOLDEN_G2)

    def test_picker_swaps_the_four_spots_only(self):
        # ⑩ 傳 picker：steady/revisit/hungry/gate 線四處經 picker 換句，其餘行不變
        keys = []

        def marker(key, pool):
            keys.append((key, pool))
            # hungry 池的 {extra} 槽帶著 revisit 句（同真池形）；其餘 marker 無槽、format 不炸
            return "〈hungry〉{dur}{extra}" if key == "hungry" else f"〈{key}〉"

        out = selfstate.bodystate_facts(VIT, RES_G3, picker=marker)
        self.assertIn("〈steady〉", out)
        self.assertIn("，〈revisit〉", out)                    # 繞回句經 {extra} 槽嵌進飢餓整句
        self.assertIn("〈hungry〉", out)
        self.assertIn("〈line_g3〉", out)
        # 其餘行不動（開放程度/心情/標頭句照舊；釘詞「放得開」所在句原樣）
        self.assertIn("我此刻的自體狀態：", out)
        self.assertIn("開放程度：比剛醒時更放得開（成形的門檻鬆了些）", out)
        self.assertIn("心情：這陣子心情不錯、是暖的。", out)
        # 四處對到的池正確
        got = dict(keys)
        self.assertIs(got["steady"], phrasing.STEADY_EXT)
        self.assertIs(got["revisit"], phrasing.REVISIT_EXT)
        self.assertIs(got["hungry"], phrasing.HUNGRY_EXT)
        self.assertIs(got["line_g3"], phrasing.LINE_G3_EXT)
        # gate 2 → line_g2 池
        out2 = selfstate.bodystate_facts(VIT, RES_G2, picker=marker)
        self.assertIn("〈line_g2〉", out2)

    def test_picker_with_real_pool_formats_topic(self):
        # 真池整圈：revisit/hungry 帶槽句 format 後主題與「餓」都在、不留花括號槽
        state = State(os.path.join(tempfile.mkdtemp(), "state.json"))
        out = selfstate.bodystate_facts(VIT, RES_G3, picker=monitor._phrase_picker(state))
        self.assertIn("汽車保養", out)
        self.assertIn("餓", out)
        self.assertNotIn("{t}", out)
        self.assertNotIn("{dur}", out)

    def test_consecutive_picks_vary_wording(self):
        # 反重複真的在動：同一 state 連做兩次，四處句子與第一次不同（recent 跳過已用 idx）
        state = State(os.path.join(tempfile.mkdtemp(), "state.json"))
        picker = monitor._phrase_picker(state)
        a = selfstate.bodystate_facts(VIT, RES_G3, picker=picker)
        b = selfstate.bodystate_facts(VIT, RES_G3, picker=picker)
        self.assertNotEqual(a, b)
        self.assertIn("一直很穩、沒斷線過", a)                 # 第一抽＝index 0＝原句（行為連續）
        self.assertNotIn("一直很穩、沒斷線過", b)              # 第二抽換句了


class SelfActsPickerTest(unittest.TestCase):
    def _ref(self, gate):
        return Referent(held="研發日誌", held_gate=gate, revisited="假日惠中寺行程", mood=0.6)

    def test_no_picker_is_status_quo(self):
        # ⑫ 不傳 picker＝現狀輸出（test_referent 釘詞保留）
        txt = referent.self_acts_text(self._ref(3))
        self.assertIn("有一條動了、還沒整個合起來", txt)
        for frag in ("假日惠中寺行程", "自我刺激", "研發日誌", "心情偏暖"):
            self.assertIn(frag, txt)
        wander = referent.self_acts_text(Referent(held="讀誦經書", held_gate=1))
        self.assertIn("我最近在「讀誦經書」一帶繞，還沒繞出形狀。", wander)

    def test_picker_swaps_line_phrases(self):
        # ⑫ 傳 picker：gate 2/3 句與 wander 句經池換句（selfstate:2826 與 referent:100 同款句兩處一起接池）
        def marker(key, pool):
            return {"line_g3": "〈g3〉", "line_wander": "我最近在「{t}」邊上晃。"}[key]

        txt = referent.self_acts_text(self._ref(3), picker=marker)
        self.assertIn("〈g3〉", txt)
        self.assertIn("研發日誌", txt)                          # 主線名還在同一句
        wander = referent.self_acts_text(Referent(held="讀誦經書", held_gate=1), picker=marker)
        self.assertIn("我最近在「讀誦經書」邊上晃。", wander)
        self.assertNotIn("一帶繞", wander)


def _gate4_records():
    """一份在 k=-1 會跑到 Gate 4 的合成語料（seeded → 確定性；同 test_selfreport_monitor）。"""
    rng = random.Random(7)
    bases = {"甲": [1.0, 0.8, 0.2, 0.0, 0.0, 0.0, 0.0, 0.0],
             "乙": [0.9, 0.9, 0.3, 0.1, 0.0, 0.0, 0.0, 0.0],
             "丙": [0.8, 1.0, 0.2, 0.0, 0.1, 0.0, 0.0, 0.0]}
    order = ["甲", "乙", "甲", "丙", "甲", "乙", "甲", "丙", "甲", "乙", "甲", "丙"]
    recs = []
    for i, lab in enumerate(order):
        emb = [b + rng.uniform(-0.25, 0.25) for b in bases[lab]]
        ts = (NOW - timedelta(minutes=40 * (len(order) - i))).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        recs.append({"id": f"r{i}", "ts": ts, "type": ["text", "image", "audio", "link"][i % 4],
                     "topicLabel": lab, "embedding": emb})
    return recs


class FakeClient:
    def __init__(self):
        self.sent = []
        self.dry_run = False

    def send(self, text):
        self.sent.append(text)
        return True


class FakeReader:
    def __init__(self, records):
        self.records = records

    def load_owner_data(self, _fid):
        return {"meta": {"lastIngestTs": INGEST}, "contexts": [], "journeys": []}

    def load_embedding_records(self, _fid):
        return self.records


def _cfg(**over):
    base = dict(
        selfstate_enabled=True, selfstate_interval_min=3, heartbeat_interval_min=3,
        notify_cooldown_min=30, selfstate_adaptive=True, selfstate_sensitivity=-1.0,
        selfstate_z_star=2.0, selfstate_tau_star=0.78, selfstate_int_min=0.5,
        selfstate_diff_min=0.0, selfstate_n_min=8, selfstate_r_min=3, dry_run=False,
        telegram_chat_id="", enable_chat=True,
        selfstate_repeat_cooldown_min=180, perceive_fail_grace=2)
    base.update(over)
    return SimpleNamespace(**base)


def _coach():
    return SimpleNamespace(enabled=True, api_key="k", model="gemini-2.5-flash",
                           meter=SimpleNamespace(record=lambda *a, **k: None))


class MonitorAntiReuseTest(unittest.TestCase):
    """⑪ handle_message 掛載（stub gemini、零網路；仿 test_selfreport_monitor 的 stub 形）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.recs = _gate4_records()

    def _state(self):
        s = State(os.path.join(self.tmp, "state.json"))
        s.owner_folder_id = "F"
        return s

    def _ask(self, state, text, cfg, captured):
        def fake_gen(key, model, system, user, **kw):
            captured.append((system, user))
            return "（自陳）就資料看，已成形。"
        update = {"message": {"chat": {"id": 1}, "text": text, "date": NOW.timestamp()}}
        data = {"meta": {"lastIngestTs": INGEST}, "contexts": [], "journeys": []}
        client = FakeClient()
        with mock.patch("telegram_monitor.gemini.generate", side_effect=fake_gen):
            monitor.handle_message(update, _coach(), FakeReader(self.recs), data, None,
                                   state, client, cfg, None)
        return client

    def test_flag_on_uses_varied_with_forbidden_block(self):
        # ⑪ 旗標開＋有上次自陳原話 → system 用 VARIED、含【禁止重複】＋上次全文（負面示例）
        state = self._state()
        state.last_self_report = {"text": "我一直都醒著，很穩、沒斷線過，思緒順順地淌著。",
                                  "ts": NOW.timestamp() - 3600}
        cfg = _cfg(phrase_anti_reuse_enabled=True)
        cap = []
        c = self._ask(state, "你現在怎樣", cfg, cap)
        self.assertEqual(len(c.sent), 1)
        body_calls = [(s, u) for s, u in cap if "我此刻的自體狀態" in u]
        self.assertEqual(len(body_calls), 1)
        sys_, _u = body_calls[0]
        self.assertTrue(sys_.startswith(selfstate.BODYSTATE_SYSTEM_VARIED))
        self.assertIn("【禁止重複】", sys_)
        self.assertIn("我一直都醒著，很穩、沒斷線過，思緒順順地淌著。", sys_)
        # 送出後：開頭片段記進 bodystate 專用 key（照 _record_self_opener 精神、記憶體）
        ops = (state.recent_phrase_use or {}).get("bodystate_opener") or []
        self.assertEqual(ops, ["（自陳）就資料看"])

    def test_flag_on_without_prior_omits_negative_example(self):
        # ⑪ §1.22 單獨開（§1.21 沒開＝last_self_report 空）→ 負面示例段自動省略、VARIED 照用
        state = self._state()
        cfg = _cfg(phrase_anti_reuse_enabled=True)
        cap = []
        self._ask(state, "你現在怎樣", cfg, cap)
        sys_, _u = [(s, u) for s, u in cap if "我此刻的自體狀態" in u][0]
        self.assertTrue(sys_.startswith(selfstate.BODYSTATE_SYSTEM_VARIED))
        self.assertNotIn("【禁止重複】", sys_)

    def test_flag_off_is_bitwise_baseline_system(self):
        # ⑪ 旗標關（cfg 無此欄＝getattr 預設 False）→ system 逐位元＝原 BODYSTATE_SYSTEM 拼接、零 §1.22 痕跡
        state = self._state()
        state.last_self_report = {"text": "上次原話。", "ts": NOW.timestamp() - 3600}
        cap = []
        self._ask(state, "你現在怎樣", _cfg(), cap)
        sys_, u = [(s, u) for s, u in cap if "我此刻的自體狀態" in u][0]
        self.assertTrue(sys_.startswith(selfstate.BODYSTATE_SYSTEM))
        for token in ("【禁止重複】", "語意骨架"):
            self.assertNotIn(token, sys_)
            self.assertNotIn(token, u)
        self.assertEqual(state.recent_phrase_use, {})           # 沒記任何池使用
        self.assertEqual(state.phrase_cursor, {})

    def test_anti_block_shapes(self):
        # _anti_block：有原話＝禁止重複段；有近期開頭＝vary 尾句；都沒有＝至少一條泛用換說法指示（非空）
        state = self._state()
        self.assertTrue(monitor._anti_block(state))              # 永不為空（VARIED 掛載靠它非空）
        state.last_self_report = {"text": "上次原話。", "ts": 0}
        blk = monitor._anti_block(state)
        self.assertIn("【禁止重複】", blk)
        self.assertIn("上次原話。", blk)
        state.recent_phrase_use = {"bodystate_opener": ["我還醒著、翻著"]}
        blk2 = monitor._anti_block(state)
        self.assertIn("換個開頭和句型", blk2)                    # persona.vary_hint 同款尾句
        self.assertIn("我還醒著、翻著", blk2)

    def test_pick_phrase_records_recent_and_cursor(self):
        # monitor._pick_phrase：cursor 推進＋recent 記末 6（記憶體、不進 state.json）
        state = self._state()
        pool = tuple(f"句{c}" for c in "甲乙丙丁戊己庚辛")
        got = [monitor._pick_phrase(state, "k", pool) for _ in range(8)]
        self.assertEqual(len(set(got)), 8)                       # 連抽 8 次不重複
        self.assertEqual(len(state.recent_phrase_use["k"]), 6)   # 只留末 6
        self.assertEqual(state.phrase_cursor["k"], 8)
        state.save()
        import json
        with open(state.path, encoding="utf-8") as f:
            d = json.load(f)
        self.assertNotIn("recent_phrase_use", d)                 # 可丟記憶：不落檔
        self.assertNotIn("phrase_cursor", d)


if __name__ == "__main__":
    unittest.main()
