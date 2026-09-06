# -*- coding: utf-8 -*-
"""🗣️ §2.24 嗆聲式聽不懂＋澄清錨定：「你究竟是在供殺小？」之後的四輪糊掉迴圈。

實測截圖（20:07–21:22）：
  20:07 bot 主動 musing「讀誦經書…像磁鐵一樣把我吸過去」（💡 吸子、by design）
  21:02 「你究竟是在供殺小？」＝台語「你到底在講什麼」（帶嗆）——三層全 miss：
        ・_CONFUSED_ASK_RE 不認 → 沒澄清接地 → LLM 對俚語迷航「欸，這句話不是你剛剛說的嗎？」
        ・reaction.is_hostile 不認 → §1.77 氣頭門不擋 → 21:03 被嗆完照送親親熊貼圖
        （§0.87 讀的是 bot 自己回覆的暖意、不看他在罵；且互動貼圖 lane 跑在 _remember(user)
        之前又沒帶本句＝文件寫的「本句敵意」臂在互動路徑一直是死的）
  21:14–21:21 「我剛剛說的？/有嗎/你說哪個？」全泛用 lane → LLM 引用複讀
  21:21 「什麼意思？」進 §1.56 澄清——但 recap 素材此時全是迷航過程本身 → 21:22
        「然後你就問我『你說哪個。』」＝把清單當劇本逐條敘事＝複述吵架、越繞越糊

§2.24A（CONFUSED_SLANG）：_SLANG_WTF_RE → 進澄清＋【語氣】接住情緒＋算氣頭（嗆聲當輪貼圖收斂、
補活本句臂＝_TURN["cur_user_text"]）。§2.24B（CLARIFY_ANCHOR）：澄清錨定最近一則實質 model 句、
禁逐條複述。全 stub、零網路。
"""

import os
import tempfile
import unittest
from types import SimpleNamespace

from telegram_monitor import monitor
from telegram_monitor.state import State

SHOT = "你究竟是在供殺小？"
NOW = 1780000000.0
MUSING = "這條線啊，老實說，它就像一個磁鐵一樣，會一直把我吸過去。"


def _hist():
    """截圖的對話史（由舊到新）：實質 musing → 嗆聲 → 迷航來回。"""
    turns = [("model", "嗯，我突然想到，你今天不是又提了「閱讀｜讀誦經書」嗎。"),
             ("model", MUSING),
             ("user", SHOT),
             ("model", "欸，這句話不是你剛剛說的嗎？"),
             ("user", "我剛剛說的？\n有嗎"),
             ("model", "我就是回你這個"),
             ("user", "你說哪個？"),
             ("model", "你說哪個「你究竟是在供殺小？」嗎？")]
    return [{"role": r, "text": t, "ts": NOW - (len(turns) - i) * 60} for i, (r, t) in enumerate(turns)]


def _state(hist=None):
    s = State(os.path.join(tempfile.mkdtemp(), "s.json"))
    s.convo_history = hist if hist is not None else _hist()
    return s


def _cfg(**kw):
    d = dict(confused_clarify_enabled=True, confused_slang_enabled=True, clarify_anchor_enabled=True,
             hostile_grace_enabled=True)
    d.update(kw)
    return SimpleNamespace(**d)


class SlangDetectorTest(unittest.TestCase):
    def test_screenshot_and_variants_hit(self):
        for t in (SHOT, "供三小", "公三小", "講三小啦", "衝三小", "你到底在講什麼", "供啥潲"):
            self.assertTrue(monitor._SLANG_WTF_RE.search(t), t)

    def test_no_false_positives(self):
        for t in ("我讀了三小時的書", "早安", "小三的問題", "供品要準備什麼", "你在說什麼"):
            self.assertFalse(monitor._SLANG_WTF_RE.search(t), t)
        # 「你在說什麼」本來就走 _CONFUSED_ASK_RE 原路徑（不歸 slang 管）
        self.assertTrue(monitor._CONFUSED_ASK_RE.match("你在說什麼"))


class ClarifyHintTest(unittest.TestCase):
    def test_slang_enters_clarify_with_emotion_rule(self):
        # ★ 21:02 的修法：嗆聲句拿到澄清接地＋接住情緒守則（不是丟給 LLM 對俚語自由發揮）
        h = monitor._clarify_recap_hint(_state(), _cfg(), SHOT, NOW)
        self.assertIn("接住這份情緒", h)
        self.assertIn("別歡快", h)
        self.assertIn("由舊到新", h)                        # recap 時序清單還在

    def test_slang_flag_off_stays_out(self):
        h = monitor._clarify_recap_hint(_state(), _cfg(confused_slang_enabled=False), SHOT, NOW)
        self.assertEqual(h, "")                             # 旗標關＝俚語不進澄清＝逐位元同現狀

    def test_long_rant_with_slang_not_hijacked(self):
        long_t = "我今天跟同事吵架整個很煩三小事情一堆然後你又一直傳訊息來我真的快受不了了啦"
        self.assertEqual(monitor._clarify_recap_hint(_state(), _cfg(), long_t, NOW), "")

    def test_anchor_picks_substance_not_meta(self):
        # ★ 21:22 的修法：錨＝20:07 那則實質 musing；引用複讀輪與短迷航句都跳過
        h = monitor._clarify_recap_hint(_state(), _cfg(), "什麼意思？", NOW)
        self.assertIn(MUSING[:20], h)                       # 錨定實質內容
        self.assertIn("不要**逐條複述"[3:], h)              # 禁逐條複述
        self.assertIn("再引用他的話反問", h)
        # 引用複讀那句（含使用者原話）絕不能被選成錨
        self.assertNotIn("【他多半是看不懂你先前這一則（程式挑的、你最近一則實質內容）】\n・你說哪個", h)

    def test_anchor_flag_off_keeps_legacy_rules(self):
        h = monitor._clarify_recap_hint(_state(), _cfg(clarify_anchor_enabled=False,
                                                       confused_slang_enabled=False), "什麼意思？", NOW)
        self.assertIn("用白話把你前面真正想表達的重點重講一次就好", h)   # 原守則字句
        self.assertNotIn("先前這一則", h)

    def test_no_substantive_anchor_falls_back_to_legacy(self):
        hist = [{"role": "model", "text": "我就是回你這個", "ts": NOW - 120},
                {"role": "user", "text": "你說哪個？", "ts": NOW - 60}]
        h = monitor._clarify_recap_hint(_state(hist), _cfg(), "什麼意思？", NOW)
        self.assertIn("用白話把你前面真正想表達的重點重講一次就好", h)   # 挑不到錨＝原格式


class HostileNowTest(unittest.TestCase):
    def test_slang_text_counts_as_hostile(self):
        self.assertTrue(monitor._hostile_now(_state([]), _cfg(), SHOT))

    def test_flag_off_unchanged(self):
        self.assertFalse(monitor._hostile_now(_state([]), _cfg(confused_slang_enabled=False), SHOT))

    def test_dead_arm_revived_via_turn_stash(self):
        # ★ 21:03 的修法：互動貼圖 lane 沒帶本句 → 從 _TURN 讀到「這句就在嗆」
        monitor._TURN["cur_user_text"] = SHOT
        try:
            self.assertTrue(monitor._hostile_now(_state([]), _cfg()))
            self.assertFalse(monitor._hostile_now(_state([]), _cfg(confused_slang_enabled=False)))
        finally:
            monitor._TURN.pop("cur_user_text", None)

    def test_recent_history_slang_counts(self):
        hist = [{"role": "user", "text": SHOT, "ts": NOW - 60}]
        self.assertTrue(monitor._hostile_now(_state(hist), _cfg()))
        self.assertFalse(monitor._hostile_now(_state(hist), _cfg(confused_slang_enabled=False)))


class StickerGateTest(unittest.TestCase):
    class Cl:
        dry_run = False

        def __init__(self):
            self.stickers, self.sent = [], []

        def send_sticker(self, fid):
            self.stickers.append(fid)
            return True

        def send(self, t):
            self.sent.append(t)
            return True

    def test_no_cheerful_sticker_on_slang_turn(self):
        # ★ 截圖 21:03 場景：被嗆的當輪、bot 自己的回覆讀起來是暖的 → 貼圖仍收斂不送
        s = _state([])
        s.known_sticker_ids = [{"file_id": "F1", "emoji": "🥰", "val": "positive", "desc": "親親熊"}]
        cl = self.Cl()
        monitor._TURN["cur_user_text"] = SHOT
        monitor._TURN["last_reply"] = "我剛剛是想跟你說說「讀誦經書」這條線，因為它一直讓我很好奇。"
        try:
            monitor._maybe_sticker(cl, s, _cfg(send_stickers=True, dry_run=False,
                                               content_sticker_enabled=True, sticker_cooldown_min=0), NOW)
        finally:
            monitor._TURN.pop("cur_user_text", None)
            monitor._TURN.pop("last_reply", None)
        self.assertEqual(cl.stickers, [])                   # 氣頭收斂：一張都不送

    def test_wiring_stash_before_dispatch_and_popped_per_turn(self):
        with open("telegram_monitor/monitor.py", encoding="utf-8") as f:
            src = f.read()
        self.assertIn('_TURN["cur_user_text"] = (text or "").strip()', src)
        self.assertIn('_TURN.pop("cur_user_text", None)', src)
        # stash 在 route dispatch（greeting lane）之前＝互動貼圖 lane 讀得到本句
        self.assertLess(src.index('_TURN["cur_user_text"] = (text or "").strip()'),
                        src.index('if route.kind == "greeting":'))


class FlagSyncTest(unittest.TestCase):
    def test_flags_everywhere(self):
        with open("telegram_monitor/config.py", encoding="utf-8") as f:
            src = f.read()
        self.assertIn('_bool("CONFUSED_SLANG", True)', src)
        self.assertIn('_bool("CLARIFY_ANCHOR", True)', src)
        with open(".env.example", encoding="utf-8") as f:
            env = f.read()
        self.assertIn("CONFUSED_SLANG=1", env)
        self.assertIn("CLARIFY_ANCHOR=1", env)
        with open("README.md", encoding="utf-8") as f:
            md = f.read()
        self.assertIn("CONFUSED_SLANG", md)
        self.assertIn("CLARIFY_ANCHOR", md)


if __name__ == "__main__":
    unittest.main()
