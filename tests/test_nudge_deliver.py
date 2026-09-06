"""🫸 §1.87 催促＝要我現在做（NUDGE_DELIVER）：把「所以呢？」從『再對帳一次』改成『現在就做』。

截圖根因（22:54–22:57）：使用者連丟五次「所以呢？」，bot 的五次回覆是
  「所以... 我還欠你一次猜測，對不對」／「嗯... 我還欠你一次猜測，對不對」／「我還欠你一次猜測，對不對」
  ／「我在等你決定什麼時候要我再猜一次呀」／「嗯，這段我剛剛才說過一次——你想聽哪部分，我換個說法講？」
＝誠實對帳三遍＋把球踢回去＋罐頭拒答，**一次都沒真的去猜**。

三個實測根因：
 ① selfstate.promise_status_kind('所以呢？') → 'outcome' ⇒ 偵測器**認得**這句話，只是被路由去 §1.13A
    的「據帳本誠實對帳」。§1.13A 擋掉了「我做到了」的謊（成功了），但誠實對帳連講四次就是新形狀的光說不做。
 ② §1.85 的回覆橋只救帳本裡 status=='owed' 的筆；「第四次猜測」是使用者在對話裡建立的、帳本裡沒有。
 ③ §1.71 重播守門用**逐字全等**：三句只差開頭語助詞就全部繞過（本檔實測釘住）。

修法＝零詞表的結構訊號：窗內最近兩則 bot 回覆**近乎相同** ⇒ 我在原地重複、沒有前進。刻意不去判
「這句暗示我做事」的語意（任何詞表都會漏），改判「我剛剛講過一樣的話」。全 stub、零網路。
"""

import io
import re
import unittest
from types import SimpleNamespace

from telegram_monitor import echo, monitor, persona, selfstate

NOW = 1_780_000_000.0

R1 = "所以... 我還欠你一次猜測，對不對"
R2 = "嗯... 我還欠你一次猜測，對不對"
R3 = "我還欠你一次猜測，對不對"
BALL = "我在等你決定什麼時候要我再猜一次呀"


def _hist(*model_texts, gap=30.0):
    """交錯的 user/model 對話史（舊→新），model 文字依序放入。"""
    out, t = [], NOW - gap * (len(model_texts) * 2)
    for m in model_texts:
        out.append({"role": "user", "text": "所以呢？", "ts": t})
        t += gap
        out.append({"role": "model", "text": m, "ts": t})
        t += gap
    return out


class DetectorSanityTest(unittest.TestCase):
    """釘住「偵測器本來就認得這句話」——問題從來不在偵測，在路由。"""

    def test_nudge_recognised_as_outcome(self):
        for t in ("所以呢？", "所以呢", "然後呢？", "然後呢"):
            self.assertEqual(selfstate.promise_status_kind(t), "outcome", t)


class ReplayGuardMissRegressionTest(unittest.TestCase):
    """釘住 §1.71 為什麼漏三次：逐字全等 vs 相似度。"""

    def test_particle_defeats_exact_match(self):
        for a, b in ((R1, R2), (R2, R3), (R1, R3)):
            na, nb = echo._norm(a), echo._norm(b)
            self.assertNotEqual(na, nb)                       # 全等＝False ⇒ _replay_filter 漏
            self.assertTrue(echo._looks_same(na, nb, 0.8))    # 相似度抓得到 ⇒ §1.87 用這個


class StuckDetectTest(unittest.TestCase):
    """結構訊號：窗內最近兩則回覆近乎相同＝卡住。零詞表、純函式。"""

    def test_incident_is_stuck(self):
        self.assertTrue(monitor._stuck_under_nudge(_hist(R1, R2), NOW))
        self.assertTrue(monitor._stuck_under_nudge(_hist(R2, R3), NOW))
        self.assertTrue(monitor._stuck_under_nudge(_hist(R1, R2, R3), NOW))

    def test_progress_is_not_stuck(self):
        self.assertFalse(monitor._stuck_under_nudge(
            _hist("我猜你是天秤座，因為你在意事情有沒有被擺平。",
                  "那我換一個：我猜你是水瓶座，因為你老在想機制怎麼運作。"), NOW))

    def test_needs_two_turns(self):
        self.assertFalse(monitor._stuck_under_nudge(_hist(R1), NOW))
        self.assertFalse(monitor._stuck_under_nudge([], NOW))
        self.assertFalse(monitor._stuck_under_nudge(None, NOW))

    def test_short_replies_exempt(self):
        # 「嗯。」「好。」這種合理重複不判卡住（否則正常附和會誤觸）
        self.assertFalse(monitor._stuck_under_nudge(_hist("嗯。", "嗯。"), NOW))
        self.assertFalse(monitor._stuck_under_nudge(_hist("好啊。", "好啊。"), NOW))

    def test_out_of_window_not_stuck(self):
        h = _hist(R1, R2)
        self.assertFalse(monitor._stuck_under_nudge(h, NOW + 99999))

    def test_glyph_prefix_ignored(self):
        # 主動訊息前綴（🤝/🫀…）不該讓兩則看起來不同
        self.assertTrue(monitor._stuck_under_nudge(_hist("🤝 " + R3, R3), NOW))


class BallBackStripTest(unittest.TestCase):
    """輸出端確定性守門：卡住情境下禁止把選擇權推回去。"""

    def test_incident_ball_back_stripped(self):
        out, hit = monitor._ball_back_strip(BALL)
        self.assertTrue(hit)
        self.assertEqual(out, monitor._STUCK_OWN_IT)           # 全剝空＝誠實認卡，不無聲也不再空問一次
        self.assertNotIn("等你決定", out)

    def test_replay_fallback_phrase_is_ball_back(self):
        # 罐頭拒答那句本身就是踢球（截圖 22:57）
        self.assertTrue(monitor._BALL_BACK_RE.search(monitor._REPLAY_FALLBACK))

    def test_substance_kept_ball_back_removed(self):
        out, hit = monitor._ball_back_strip("我猜你是水瓶座。我在等你決定要不要我再猜。")
        self.assertTrue(hit)
        self.assertIn("水瓶座", out)                            # 正題保留（句級更正優先）
        self.assertNotIn("等你決定", out)

    def test_concrete_choice_not_stripped(self):
        # 「A 還是 B」具體二選一＝卡住時我們**要**它做的事，絕不能剝
        for m in ("我先猜一個，還是先講我為什麼這樣猜？",
                  "我猜水瓶座。要我講理由嗎？"):
            out, hit = monitor._ball_back_strip(m)
            self.assertFalse(hit, m)
            self.assertEqual(out, m)

    def test_normal_reply_untouched(self):
        m = "我猜你是水瓶座，因為你老在想機制怎麼運作。"
        self.assertEqual(monitor._ball_back_strip(m), (m, False))

    def test_empty_safe(self):
        self.assertEqual(monitor._ball_back_strip(""), ("", False))
        self.assertEqual(monitor._ball_back_strip(None), (None, False))


class HintTest(unittest.TestCase):
    """守則注入：旗標＋卡住旗都要在；內容要有那三條禁令。"""

    def _cfg(self, on=True):
        return SimpleNamespace(nudge_deliver_enabled=on)

    def tearDown(self):
        monitor._TURN.pop("nudge_stuck", None)

    def test_hint_needs_flag_and_stuck(self):
        monitor._TURN["nudge_stuck"] = True
        self.assertTrue(monitor._nudge_deliver_hint(None, self._cfg(), NOW))
        self.assertEqual(monitor._nudge_deliver_hint(None, self._cfg(on=False), NOW), "")
        monitor._TURN.pop("nudge_stuck", None)
        self.assertEqual(monitor._nudge_deliver_hint(None, self._cfg(), NOW), "")

    def test_hint_content(self):
        h = persona.NUDGE_DELIVER_HINT
        self.assertIn("現在就把那件事本身做出來", h)
        self.assertIn("絕對不要把選擇權推回給他", h)
        self.assertIn("不能沒有內容", h)
        self.assertIn("卡住", h)

    def test_hint_gives_no_example_sentences(self):
        # §1.69/§1.50 前科：prompt 給範例句 → LLM 逐字抄回、養出新口頭禪
        self.assertNotIn("像「", persona.NUDGE_DELIVER_HINT)


class WiringTest(unittest.TestCase):
    """佈線：路由讓開、_say 兩道守門、輪初清旗——**釘住原始碼真的接上了**（MEMORY：邏輯對不算數）。"""

    def _src(self):
        return io.open("telegram_monitor/monitor.py", encoding="utf-8").read()

    def test_route_yields_when_stuck(self):
        s = self._src()
        self.assertIn('_TURN["nudge_stuck"] = True', s)
        self.assertIn("_stuck_under_nudge(getattr(state, \"convo_history\", None)", s)
        self.assertIn("_to_ledger = False", s)

    def test_say_guards_wired(self):
        s = self._src()
        self.assertIn("text, _bb = _ball_back_strip(text)", s)
        # 🔁 §2.03 全剝空的收尾從二選一變成三選一（卡住／同一波／其餘），**卡住仍優先**
        self.assertIn('if _TURN.get("nudge_stuck"):\n                    bubbles = [_STUCK_OWN_IT]', s)
        self.assertIn("_REPLAY_FALLBACK", s)

    def test_stuck_still_wins_over_same_wave(self):
        # 他在催、而且我在原地重複＝認卡優先於「同一波的短承接」（短承接會顯得在敷衍催促）
        import types
        cl = types.SimpleNamespace(sent=[], dry_run=False)
        cl.send = lambda t: cl.sent.append(t) or True
        monitor._TURN.clear()
        monitor._TURN.update({"bubbles": None, "replay_guard": True,
                              "nudge_stuck": True, "replay_same_wave": True})
        monitor._replay_note("我還欠你一次猜測，對不對，這句夠長了會被記下來")
        monitor._say(cl, "我還欠你一次猜測，對不對，這句夠長了會被記下來")
        self.assertEqual("".join(cl.sent), monitor._STUCK_OWN_IT)

    def test_hint_injected_into_chat_lanes(self):
        s = self._src()
        self.assertGreaterEqual(s.count("_nudge_deliver_hint(state, cfg, convo_now)"), 3)

    def test_turn_flag_cleared(self):
        self.assertIn('_TURN.pop("nudge_stuck", None)', self._src())


class SayEndToEndTest(unittest.TestCase):
    """走真正的 `_say` 出口——**實跑證明守門真的被呼叫到**（MEMORY「新機制常被無聲架空」：邏輯對不算數）。

    ⚠️ 這批守門包在 `if not prefix and state is None:` 內＝**互動路徑限定**（`_say(client, text)`）。
    實作期我第一次驗證時傳了 `state=`，守門整個沒被走到、踢球句原樣送出——那正是被無聲架空的形狀。
    這裡刻意用互動路徑呼叫並釘死，之後任何人把它搬離該區塊都會當場紅燈。"""

    class Cl:
        dry_run = False

        def __init__(self):
            self.sent = []

        def send(self, t):
            self.sent.append(t)
            return True

    def setUp(self):
        monitor._TURN["nudge_stuck"] = True
        monitor._TURN["echo_user_texts"] = ["所以呢？"]

    def tearDown(self):
        monitor._TURN.pop("nudge_stuck", None)
        monitor._TURN.pop("echo_user_texts", None)
        monitor._TURN.pop("replay_guard", None)
        monitor._SENT_RECENT.clear()

    def test_ball_back_replaced_on_interactive_path(self):
        cl = self.Cl()
        monitor._say(cl, BALL)                              # 互動路徑＝無 prefix、state=None
        out = "".join(cl.sent)
        self.assertNotIn("等你決定", out)
        self.assertIn("卡住", out)

    def test_substance_survives(self):
        cl = self.Cl()
        monitor._say(cl, "我猜你是水瓶座。我在等你決定要不要我再猜。")
        out = "".join(cl.sent)
        self.assertIn("水瓶座", out)
        self.assertNotIn("等你決定", out)

    def test_flag_off_bit_identical(self):
        monitor._TURN.pop("nudge_stuck", None)
        cl = self.Cl()
        monitor._say(cl, BALL)
        self.assertEqual("".join(cl.sent), BALL)

    def test_replay_fallback_is_own_it_when_stuck(self):
        import time
        monitor._replay_note(R3, now_ts=time.time())
        monitor._TURN["replay_guard"] = True
        cl = self.Cl()
        monitor._say(cl, R3, state=None)
        out = "".join(cl.sent)
        self.assertIn("卡住", out)
        self.assertNotIn("你想聽哪部分", out)               # 罐頭拒答（＝踢球）不再出現


class ConfigTest(unittest.TestCase):
    def test_flag_synced(self):
        src = io.open("telegram_monitor/config.py", encoding="utf-8").read()
        self.assertIn("NUDGE_DELIVER", src)
        self.assertIn("nudge_deliver_enabled", src)
        env = io.open(".env.example", encoding="utf-8").read()
        self.assertRegex(env, re.compile(r"^NUDGE_DELIVER=1", re.M))
        self.assertIn("NUDGE_DELIVER", io.open("README.md", encoding="utf-8").read())

    def test_getattr_defaults_false(self):
        self.assertFalse(getattr(SimpleNamespace(), "nudge_deliver_enabled", False))


if __name__ == "__main__":
    unittest.main()
