"""📈 §1.97 作息守門改讀語意角色（HABIT_CLAIM_ROLE）——§1.96 明文留給這一章的事。

§1.42 的守門判準是一條**線性詞面樣式**：「你」＋「平常」＋「X點」要按順序出現在同一句。
實測對 §1.96 截圖那四句**全漏**，每一句漏的理由都不一樣：
  ①「今天醒得比平常晚一點嗎」   整句沒有「你」
  ②「對我來說，是比平常晚一些些」「我」根本不是作息的主人（那是立場框架，舊碼看到「我」就整句放過）
  ③「今天這樣晚一點才醒來」     連「平常」都沒說出口（尺被省略了）
  ④「你平常多半是在早上記寫的」 沒有鐘點；而且講的是**記寫**，卻只有對話統計可對（§1.96 的類別錯置）

改法不是往樣式加詞（那是本 repo 漏了 15+ 次的路），是把句子拆成四個**各自解析、不要求相鄰或順序**的
語意角色：主角／基準／述語／領域。全 stub、零網路。
"""

import io
import os
import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from telegram_monitor import habits, monitor, temporal
from telegram_monitor.state import State

TZ = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 7, 28, 8, 30, tzinfo=TZ)
NOW_TS = NOW.timestamp()

# §1.96 截圖那四句（原文照抄）
S1 = "今天醒得比平常晚一點嗎"
S2 = "對我來說，是比平常晚一些些"
S3 = "今天這樣晚一點才醒來"
S4 = "你平常多半是在早上記寫的"
FOUR = (S1, S2, S3, S4)


def _ts(days_ago, h, m):
    return (NOW - timedelta(days=days_ago)).replace(hour=h, minute=m).timestamp()


def _state():
    """截圖實物重建：八天早上 06:2x–06:5x 出現（中位 06:37），今天 07:38＝比平常晚。"""
    s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
    s.habit_events = [{"k": "msg", "ts": _ts(d, h, m)} for d, (h, m) in
                      enumerate([(6, 30), (6, 40), (6, 37), (6, 25), (6, 50), (6, 33), (6, 45), (6, 20)], start=1)]
    s.habit_events.append({"k": "msg", "ts": _ts(0, 7, 38)})
    return s


def _records():
    """記寫時刻集中在早上 07–08（＝§1.96 說的『記寫時段真來源』，與對話事件是兩回事）。"""
    return [{"topicLabel": "讀誦經書", "text": "今天讀到無住生心",
             "ts": datetime.fromtimestamp(_ts(d, h, 10), TZ).isoformat()}
            for d, h in [(1, 7), (2, 8), (3, 7), (4, 7), (5, 8), (0, 7)]]


def _ground(role=True):
    return habits.claim_guard_data(_state(), NOW_TS, TZ, daily_first=True,
                                   records=_records() if role else None, role=role)


class LegacyMissesAllFourTest(unittest.TestCase):
    """先釘住根因：§1.42 的兩條詞面樣式對那四句真的一句都不中（§1.96 commit 只寫在說明裡，沒有測試守著）。"""

    def test_word_shape_gate_misses_every_one(self):
        for s in FOUR:
            self.assertIsNone(monitor._HABIT_CLAIM_RE.search(s), s)
            self.assertIsNone(monitor._HABIT_COMP_RE.search(s), s)


class RolesHitAllFourTest(unittest.TestCase):
    """修完那四句必須全中——而且每句被讀出來的角色都要對（不是碰巧命中）。"""

    def test_all_four_hit(self):
        whole = "。".join(FOUR)
        for s in FOUR:
            self.assertIsNotNone(habits.claim_roles(s, whole=whole), s)

    def test_s1_subject_elided_still_about_him(self):
        r = habits.claim_roles(S1)
        self.assertEqual(r["who"], "user")                 # 整句沒有「你」，主角仍是他
        self.assertEqual(r["pred"][0], "dir")
        self.assertEqual(r["pred"][1], "晚")
        self.assertEqual(r["domain"], "appear")            # 「醒」＝出現域

    def test_s1_degree_one_is_not_one_oclock(self):
        # 實作期實測踩到：「晚**一點**」若被讀成鐘點「一點」，§1.96 那句**有憑有據**的話會被判成亂掰
        self.assertEqual(habits.claim_roles(S1)["pred"][0], "dir")
        self.assertEqual(habits.claim_roles("你平常下午一點才起床")["pred"][0], "clock")   # 有時段詞＝真鐘點

    def test_s2_stance_frame_is_not_the_owner(self):
        r = habits.claim_roles(S2)
        self.assertEqual(r["who"], "user")                 # 「對我來說」是立場框架，不是作息的主人
        self.assertEqual(r["pred"], ("dir", "晚"))

    def test_s3_implicit_yardstick(self):
        r = habits.claim_roles(S3)
        self.assertEqual(r["ground"], "implicit")          # 尺沒說出口，但比的就是他平常
        self.assertEqual(r["domain"], "appear")

    def test_s4_daypart_and_write_domain(self):
        r = habits.claim_roles(S4)
        self.assertEqual(r["pred"], ("daypart", "早上"))    # 沒有鐘點也算述語
        self.assertEqual(r["domain"], "write")             # 領域＝記寫 ⇒ 之後拿記寫統計驗，不是對話事件
        self.assertEqual(r["who"], "user")

    def test_domain_resolved_across_the_whole_reply(self):
        # §1.96 那則的真實形狀：第一句自己沒有領域錨，錨在同一則的下一句（「才醒來」）
        self.assertEqual(habits.claim_roles(S2, whole=S2 + "。" + S3)["domain"], "appear")


class RoleOwnerTest(unittest.TestCase):
    def test_bot_self_report_is_not_a_claim_about_him(self):
        for s in ("我平常九點才醒。", "我平常都是清晨就醒了。", "我這幾天都比平常晚起床。"):
            self.assertEqual(habits.claim_roles(s)["who"], "bot", s)

    def test_stance_frame_before_him_still_him(self):
        self.assertEqual(habits.claim_roles("我覺得你今天比平常晚醒。")["who"], "user")


class FalsePositiveFenceTest(unittest.TestCase):
    """守門會**刪掉**真話，所以偽陽性比偽陰性貴。這些句子一律不得進門。"""

    def test_no_time_predicate(self):
        self.assertIsNone(habits.claim_roles("你平常都這樣想嗎？"))

    def test_no_yardstick_is_just_narration(self):
        self.assertIsNone(habits.claim_roles("你八點跟我說早安。"))     # 講今天，不是講「平常」

    def test_advice_and_arrangement(self):
        # 「早一點睡比較好」的「比較」修飾的是「好」、在方向詞**之後**＝建議不是斷言（結構條件，非詞表）
        self.assertIsNone(habits.claim_roles("早一點睡比較好。"))
        self.assertIsNone(habits.claim_roles("你晚一點再看吧。"))
        self.assertIsNone(habits.claim_roles("等等早一點睡喔。"))
        self.assertIsNone(habits.claim_roles("你平常晚一點再回我就好。"))

    def test_comparison_about_another_activity(self):
        # 光禿禿的「比平常晚」才預設是在講他的出現；後面接了別的活動＝比的是別件事，別拿說話統計去驗
        self.assertIsNone(habits.claim_roles("今天比較晚吃飯喔。"))
        self.assertIsNone(habits.claim_roles("今天天氣比平常好一些。"))

    def test_unanchored_clock_or_daypart_left_to_the_old_path(self):
        # 沒有領域錨的鐘點/時段不新增命中（否則「你平常晚上都在忙吧」會被拿去跟『第一句話』的統計對照）
        self.assertIsNone(habits.claim_roles("你平常晚上都在忙吧。"))

    def test_guard_own_honest_line_never_self_triggers(self):
        line = "我手上記到的是：你記寫大多在 07:10 到 08:10 之間，我也才看到 6 次，可能只是我剛好看到的都那樣。"
        self.assertIsNone(habits.claim_roles(line))


class DaypartOkTest(unittest.TestCase):
    """時段比對：格子與分界**由 temporal.day_part 導出**（零新表），用分鐘距離而不是「相鄰格」。"""

    def test_ranges_derived_from_day_part(self):
        for lo, hi in habits._daypart_ranges("早上"):
            for m in range(lo, hi, 60):
                self.assertEqual(temporal.day_part(m // 60), "早上")
        self.assertEqual(len(habits._daypart_ranges("深夜")), 2)      # 跨午夜＝兩段
        self.assertEqual(habits._daypart_ranges("認不得的詞"), [])

    def test_colloquial_tolerance(self):
        self.assertTrue(habits.daypart_ok("早上", 6 * 60 + 10, 7 * 60 + 30))   # 06:37 是「清晨」，口語說早上不算掰
        self.assertTrue(habits.daypart_ok("清晨", 6 * 60 + 10, 7 * 60 + 30))

    def test_gross_misplacement_rejected(self):
        # 實作期實測踩到：用「相鄰格」判時，深夜與清晨相鄰 ⇒「深夜記寫」對真統計 07:10–08:10 竟然過關
        self.assertFalse(habits.daypart_ok("深夜", 7 * 60 + 10, 8 * 60 + 10))
        self.assertFalse(habits.daypart_ok("中午", 6 * 60 + 10, 7 * 60 + 30))
        self.assertFalse(habits.daypart_ok("晚上", 6 * 60 + 10, 7 * 60 + 30))

    def test_unknown_word_fails_open(self):
        self.assertTrue(habits.daypart_ok("某個時段", 0, 60))          # 認不得＝不下判斷，不亂剝


class WriteDomainStatsTest(unittest.TestCase):
    """§1.96 的類別錯置在這裡有了結構解：記寫域有自己的統計來源。"""

    def test_record_stats_from_records_not_events(self):
        st = habits.record_stats(_records(), TZ, NOW_TS)
        self.assertIsNotNone(st)
        self.assertEqual(st[0], 6)
        self.assertTrue(7 * 60 <= st[1] <= 8 * 60 + 30)
        self.assertIsNone(habits.record_stats([], TZ, NOW_TS))         # 樣本不足＝None（誠實說不準）
        self.assertIsNone(habits.record_stats([{"ts": "not-a-date"}] * 5, TZ, NOW_TS))

    def test_today_record_minutes(self):
        self.assertEqual(habits.today_record_minutes(_records(), TZ, NOW_TS), 7 * 60 + 10)
        self.assertIsNone(habits.today_record_minutes([], TZ, NOW_TS))

    def test_guard_data_role_off_is_unchanged(self):
        g = habits.claim_guard_data(_state(), NOW_TS, TZ, daily_first=True)
        self.assertEqual(set(g), {"greet_am", "first", "today_first_min"})   # 舊鍵一個不多


class SayWiringTest(unittest.TestCase):
    """實跑守門出口（不是只跑純函式）：arm→_say→真的改到輸出。旗標關＝逐位元同現狀。"""

    class Cl:
        def __init__(self):
            self.sent, self.dry_run = [], False

        def send(self, text):
            self.sent.append(text)
            return True

    def setUp(self):
        monitor._TURN.clear()
        monitor._TURN["bubbles"] = None

    def _out(self, text, ground):
        monitor._TURN.clear()
        monitor._TURN["bubbles"] = None
        if ground is not None:
            monitor._TURN["habit_claim_ground"] = ground
        cl = self.Cl()
        monitor._say(cl, text)
        return "".join(cl.sent)

    def test_screenshot_reply_passes_because_it_was_true(self):
        # §1.96 已判定：中位 06:37 vs 今天 07:38＝他今天真的比平常晚 ⇒ 守門讀懂了，但**不該**改它
        t = S2 + "。" + S3 + "。"
        self.assertEqual(self._out(t, _ground()), t)

    def test_question_form_with_right_direction_passes(self):
        t = "今天醒得比平常晚一點嗎？"
        self.assertEqual(self._out(t, _ground()), t)

    def test_wrong_direction_without_you_is_now_caught(self):
        # 這正是舊守門漏掉的形狀：沒有「你」⇒ 詞面樣式不中 ⇒ 方向講反了也照送
        out = self._out("今天醒得比平常早了一些呢。", _ground())
        self.assertNotIn("比平常早", out)
        self.assertIn("06:", out)                                    # 換成真實記到的區間

    def test_write_claim_checked_against_writing_hours(self):
        g = _ground()
        self.assertEqual(self._out(S4 + "。", g), S4 + "。")           # 記寫真在早上＝放行
        out = self._out("你平常多半是在深夜記寫的。", g)
        self.assertNotIn("深夜", out)
        self.assertIn("記寫", out)                                    # 誠實句講的是**記寫**，不是說話

    def test_write_claim_without_write_stats_is_stripped(self):
        g = habits.claim_guard_data(_state(), NOW_TS, TZ, daily_first=True, records=[], role=True)
        out = self._out(S4 + "。", g)
        self.assertNotIn("早上", out)
        self.assertIn("說不準", out)

    def test_honest_line_follows_1_96_voice(self):
        out = self._out("今天醒得比平常早了一些呢。", _ground())
        for banned in ("中位", "樣本", "百分比"):
            self.assertNotIn(banned, out)                            # §1.96 明列禁用的報表詞
        self.assertIn("我也才看到", out)                              # 樣本少＝講成**自己的限制**

    def test_idempotent_and_inert_to_other_gates(self):
        once = self._out("今天醒得比平常早了一些呢。", _ground())
        self.assertEqual(self._out(once, _ground()), once)           # 自己補的誠實句不會再觸發自己
        self.assertEqual(self._out(once, None), once)               # 也不觸發 _say 上的其他閘

    def test_quote_attribution_and_bot_self_untouched(self):
        for t in ("你說我以為你比平常晚醒？", "我平常都是清晨就醒了。", "早一點睡比較好。", "今天比較晚吃飯喔。"):
            self.assertEqual(self._out(t, _ground()), t, t)

    def test_flag_off_is_byte_identical(self):
        g_off = habits.claim_guard_data(_state(), NOW_TS, TZ, daily_first=True)
        for t in (S2 + "。" + S3 + "。", "今天醒得比平常早了一些呢。", S4 + "。"):
            self.assertEqual(self._out(t, g_off), t, t)
        self.assertEqual(self._out("你通常會在早上十點左右跟我說早安。", None),
                         "你通常會在早上十點左右跟我說早安。")        # 未 arm＝完全不動

    def test_legacy_path_wording_unchanged_when_role_off(self):
        g = habits.claim_guard_data(_state(), NOW_TS, TZ, daily_first=True)
        out = self._out("你通常會在早上十點左右找我。", g)             # 出現統計有值（早安事件則無）
        self.assertIn("照我真的記到的", out)                          # 舊路徑的措辭一字不改
        self.assertIn("中位", out)


class SameYardstickAsFactCardTest(unittest.TestCase):
    """同源不變式（§1.61 慣例）：§1.96 事實卡告訴 bot 的方向，與 §1.97 守門用來驗的方向**永遠一致**——
    否則會出現「卡上叫它說晚、守門把『晚』剝掉」這種同一台機器兩把尺。"""

    def _state_with_today(self, h, m):
        s = _state()
        s.habit_events = [e for e in s.habit_events if e["ts"] < _ts(0, 0, 0)]
        s.habit_events.append({"k": "msg", "ts": _ts(0, h, m)})
        return s

    def test_card_direction_and_guard_verdict_agree(self):
        for h, m in [(5, 30), (6, 37), (7, 38), (9, 0)]:
            s = self._state_with_today(h, m)
            card = habits.today_vs_usual_line(s, NOW_TS, TZ, daily_first=True)
            g = habits.claim_guard_data(s, NOW_TS, TZ, daily_first=True, records=_records(), role=True)
            for word in ("早", "晚"):
                claimed_ok = f"比平常{word}" in card                     # 卡上就是這麼講的
                r = habits.claim_roles(f"你今天比平常{word}了一些。")
                verdict = monitor._habit_role_verdict(r, g, "你今天比平常晚了一些。")
                self.assertEqual(claimed_ok, verdict is None,
                                 f"今天 {h:02d}:{m:02d}／宣稱「{word}」／卡：{card}")


class ConfigTest(unittest.TestCase):
    def test_flag_synced(self):
        src = io.open("telegram_monitor/config.py", encoding="utf-8").read()
        self.assertIn("HABIT_CLAIM_ROLE", src)
        self.assertIn("habit_claim_role_enabled", src)
        env = io.open(".env.example", encoding="utf-8").read()
        self.assertRegex(env, re.compile(r"^HABIT_CLAIM_ROLE=1", re.M))
        self.assertIn("HABIT_CLAIM_ROLE", io.open("README.md", encoding="utf-8").read())

    def test_getattr_defaults_false(self):
        self.assertFalse(getattr(SimpleNamespace(), "habit_claim_role_enabled", False))

    def test_arm_site_guards_records_behind_the_flag(self):
        src = io.open("telegram_monitor/monitor.py", encoding="utf-8").read()
        self.assertIn('records=((data or {}).get("records") if _hb_role else None)', src)


if __name__ == "__main__":
    unittest.main()
