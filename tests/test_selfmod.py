"""🦋 蛻變感知：偵測（注入假 git）、據實 render、問句偵測、handle_message 路由。"""

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from telegram_monitor import persona, selfmod, monitor
from telegram_monitor.state import State


class BirthPromptTest(unittest.TestCase):
    """🦋 重生報到的 prompt 要逼出鑑別度：具體點出這次改了什麼、禁用空泛感覺形容詞。"""

    def test_birth_user_demands_concrete_differentiation(self):
        p = persona.birth_user("這次新做的：\n・Wave 7：對話時間軸統一用牆鐘")
        self.assertIn("對話時間軸統一用牆鐘", p)        # 事實有進去（只能依此講）
        self.assertIn("具體", p)
        self.assertIn("分得開", p)                       # 要能跟別次重生區分
        self.assertIn("嚴禁", p)
        self.assertIn("更穩定", p)                       # 明列要避免的空泛詞（在禁用清單裡）


def _runner(responses):
    """假 git：依參數 tuple 回傳預錄輸出（None＝該指令失敗）。"""
    def run(*args, **kw):
        return responses.get(args)
    return run


class DetectTest(unittest.TestCase):
    def test_unknown_when_no_git(self):
        sc = selfmod.detect("abc", run=lambda *a, **k: None)   # 讀不到 HEAD
        self.assertEqual(sc["state"], "unknown")
        self.assertIsNone(sc["commit"])

    def test_first_birth_when_no_last_seen(self):
        run = _runner({("rev-parse", "HEAD"): "deadbeef" * 5,
                       ("rev-parse", "--short", "HEAD"): "deadbee"})
        sc = selfmod.detect(None, run=run)
        self.assertEqual(sc["state"], "first_birth")
        self.assertEqual(sc["short"], "deadbee")

    def test_same_self_when_unchanged(self):
        cur = "c" * 40
        run = _runner({("rev-parse", "HEAD"): cur, ("rev-parse", "--short", "HEAD"): "ccccccc"})
        sc = selfmod.detect(cur, run=run)
        self.assertEqual(sc["state"], "same_self")

    def test_metamorphosed_lists_subjects(self):
        cur, old = "n" * 40, "o" * 40
        run = _runner({
            ("rev-parse", "HEAD"): cur,
            ("rev-parse", "--short", "HEAD"): "nnnnnnn",
            ("log", "--no-merges", "--max-count=7", "--format=%s", f"{old}..HEAD"):
                "感覺：把對話時機納入考量\n對話：最近一次回最後那筆\n修 selfstate 時間耦合測試",
        })
        sc = selfmod.detect(old, run=run)
        self.assertEqual(sc["state"], "metamorphosed")
        self.assertEqual(len(sc["subjects"]), 3)
        self.assertFalse(sc["more"])
        self.assertIn("對話時機", sc["subjects"][0])

    def test_more_flag_when_truncated(self):
        cur, old = "n" * 40, "o" * 40
        subjects = "\n".join(f"改動 {i}" for i in range(8))   # 8 條 > 上限 6（多抓 1 條＝7 行）
        run = _runner({
            ("rev-parse", "HEAD"): cur, ("rev-parse", "--short", "HEAD"): "nnnnnnn",
            ("log", "--no-merges", "--max-count=7", "--format=%s", f"{old}..HEAD"): subjects,
        })
        sc = selfmod.detect(old, run=run)
        self.assertEqual(sc["state"], "metamorphosed")
        self.assertEqual(len(sc["subjects"]), 6)   # 只列上限
        self.assertTrue(sc["more"])

    def test_changed_unknown_when_no_subjects(self):
        cur, old = "n" * 40, "o" * 40
        run = _runner({("rev-parse", "HEAD"): cur, ("rev-parse", "--short", "HEAD"): "nnnnnnn",
                       ("log", "--no-merges", "--max-count=7", "--format=%s", f"{old}..HEAD"): ""})
        sc = selfmod.detect(old, run=run)
        self.assertEqual(sc["state"], "changed_unknown")


class FactsTest(unittest.TestCase):
    _LOG = ("log", "--no-merges", "--max-count=6", "--format=%s", "HEAD")

    def test_same_self_still_lists_recent_and_no_hash(self):
        # same_self 不再只說「我沒變」——仍列近期實際更新（有用），且不吐版本雜湊
        out = selfmod.facts({"state": "same_self", "short": "abc1234"},
                            run=_runner({self._LOG: "改 A\n改 B\n改 C"}))
        self.assertIn("沒有再變", out)
        self.assertIn("・改 A", out)
        self.assertNotIn("abc1234", out)

    def test_metamorphosed_mentions_update_and_lists(self):
        out = selfmod.facts({"state": "metamorphosed", "more": False, "subjects": ["x"]},
                            run=_runner({self._LOG: "改 A\n改 B"}))
        self.assertIn("又重生並更新過", out)
        self.assertIn("・改 A", out)

    def test_unknown_is_honest(self):
        self.assertIn("讀不到", selfmod.facts({"state": "unknown"}, run=lambda *a, **k: None))


class ChangeQuestionTest(unittest.TestCase):
    def test_matches_change_questions(self):
        for q in ["你有改變了嗎", "你有改變了什麼？", "現在呢？你有什麼不同了？",
                  "你說說看自己有哪裡不一樣", "你知道你不斷地被更改嗎", "那你有感覺自己被強迫改變嗎",
                  "你更新了什麼", "這版你是什麼", "說說看你這版有什麼", "你是什麼版本"]:
            self.assertTrue(selfmod.is_change_question(q), q)

    def test_ignores_plain_state_or_chat(self):
        for q in ["你現在有什麼感覺", "你好嗎", "今天天氣如何", "我最近寫了什麼"]:
            self.assertFalse(selfmod.is_change_question(q), q)


class HandleRoutingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _state(self, self_change):
        s = State(os.path.join(self.tmp, "state.json"))
        s.owner_folder_id = "F"
        s.self_change = self_change
        return s

    def test_change_question_routes_to_grounded_voicer(self):
        # 「這版你是什麼」要走蛻變分支（接地）→ 教練用 git 事實自然回，而非掉進一般對話亂答。
        state = self._state({"state": "metamorphosed", "more": False, "subjects": ["x"]})
        client = SimpleNamespace(sent=[], dry_run=False,
                                 send=lambda t: client.sent.append(t) or True)
        cap = {}

        def voice(q, f, h, tone=""):
            cap["facts"] = f
            return "（自然回覆）這陣子我加了對話時機感、蛻變感知這些。"

        coach = SimpleNamespace(enabled=True, voice_about_self=voice)
        update = {"message": {"chat": {"id": 1}, "text": "這版你是什麼", "date": 1_700_000_000}}
        # 誤走一般對話才會呼叫 generate_with_tools → 讓它炸，確保走的是接地蛻變分支。
        with mock.patch("telegram_monitor.gemini.generate_with_tools", side_effect=AssertionError("不該走一般對話")):
            monitor.handle_message(update, coach, None, {"meta": {}}, None,
                                   state, client, SimpleNamespace(dry_run=False, telegram_chat_id=""), None)
        self.assertIn("蛻變感知", "".join(client.sent))     # 出聲＝教練的自然回覆
        self.assertIn("・", cap["facts"])                    # 餵給教練的是 git 算出的真實近期變更
        self.assertNotIn("f8a742b", cap["facts"])            # 不吐版本雜湊


class BirthAnnounceTest(unittest.TestCase):
    """🦋 重生報到：程式真的變了才主動說一句改了什麼；沒變/首生/關閉開關都安靜。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _state(self, self_change):
        s = State(os.path.join(self.tmp, "state.json"))
        s.owner_folder_id = "F"
        s.self_change = self_change
        return s

    def _client(self):
        c = SimpleNamespace(sent=[], dry_run=False)
        c.send = lambda t: c.sent.append(t) or True
        return c

    def _coach(self, enabled=True):
        return SimpleNamespace(enabled=enabled,
                               voice_birth=lambda facts, hist, continuity="": "欸，我這次醒來跟上次有點不一樣——加了重生會主動報到。")

    def _cfg(self, on=True):
        return SimpleNamespace(dry_run=False, selfmod_announce_birth=on)

    def test_announces_when_metamorphosed(self):
        state, client = self._state({"state": "metamorphosed", "more": False, "subjects": ["x"]}), self._client()
        with mock.patch("telegram_monitor.selfmod.facts", return_value="自上次喚醒後我又重生並更新過。\n・改了 A"):
            monitor._announce_birth(client, state, self._coach(), self._cfg())
        self.assertTrue(client.sent and client.sent[0].startswith("🦋"))
        self.assertIn("這次醒來跟上次有點不一樣", "".join(client.sent))
        self.assertGreater(state.last_push_ts, 0)                  # 佔用共用冷卻：別讓第一圈自陳緊接著疊上

    def test_silent_when_same_self(self):
        state, client = self._state({"state": "same_self"}), self._client()
        monitor._announce_birth(client, state, self._coach(), self._cfg())
        self.assertEqual(client.sent, [])

    def test_silent_when_first_birth(self):
        state, client = self._state({"state": "first_birth"}), self._client()
        monitor._announce_birth(client, state, self._coach(), self._cfg())
        self.assertEqual(client.sent, [])

    def test_switch_off_silences_even_when_metamorphosed(self):
        state, client = self._state({"state": "metamorphosed", "subjects": ["x"], "more": False}), self._client()
        monitor._announce_birth(client, state, self._coach(), self._cfg(on=False))
        self.assertEqual(client.sent, [])

    def test_falls_back_to_git_facts_without_coach(self):
        # 無教練 → 退回 birth_facts（接地、只列「這次新做的」改動，非最近 N 條重疊）
        state, client = self._state({"state": "metamorphosed", "subjects": ["改了 B", "改了 A"], "more": False}), self._client()
        monitor._announce_birth(client, state, self._coach(enabled=False), self._cfg())
        joined = "".join(client.sent)
        self.assertIn("改了 B", joined)
        self.assertIn("改了 A", joined)

    def test_birth_facts_lists_only_new_subjects_for_differentiation(self):
        # 鑑別度核心：birth_facts 只列 detect 算出的「這次新增」subjects（last_seen..HEAD），不跑 git log HEAD（會重疊）
        a = selfmod.birth_facts({"state": "metamorphosed", "subjects": ["Wave 7：時間軸統一用牆鐘"], "more": False})
        b = selfmod.birth_facts({"state": "metamorphosed", "subjects": ["cost：花費算法更新"], "more": False})
        self.assertIn("時間軸統一用牆鐘", a)
        self.assertIn("花費算法更新", b)
        self.assertNotEqual(a, b)                                 # 不同次重生 → 不同內容
        # 列不出新主旨 → 退回一般 facts（不丟例外）
        self.assertIsInstance(selfmod.birth_facts({"state": "changed_unknown", "subjects": []}), str)


if __name__ == "__main__":
    unittest.main()
