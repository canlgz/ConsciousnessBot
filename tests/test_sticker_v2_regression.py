"""🎴 §1.34 STICKER_V2/keep F1・F2 保留＋回歸鎖：現行正確行為收進統一架構，逐一重演不退化，
且新四閘（F3 挑選逃生閘、F5 圖呢消歧、switch 靜音、F4 假送閘）不製造偽陽性、不誤攔 F1/F2。

F1（存在/可能/省略問句）由 §1.15 逃生閘接住（結構閘含『貼圖』字恆 OPEN → judge → 真送＋§1.16 說為什麼／無貨誠實）。
F2（否認『你知道你傳的貼圖內容嗎』與『我沒傳貼圖』）由 _sent_sticker_ground_hint 接地＋§1.23 否認誠實閘攔改。

本檔不改 F1/F2 行為，只鎖：①F1 三句仍走逃生閘真送；②F2 否認閘仍替換誠實；③新閘交叉不誤傷。全 stub、零網路。
F4 假送閘交叉鎖（本檔 CrossF4）：F2 否認句與 §1.23 替換句**不**被 F4 假送閘誤攔（否定/經驗貌非肯定宣稱）；
F1 真送後的「我挑了一張…為什麼是這張」因 sticker_sent_this_turn=True 而**不**被 F4 誤攔（偏誤鎖⑤）。
"""

import os
import tempfile
import unittest
from types import SimpleNamespace

from telegram_monitor import monitor, selfstate
from telegram_monitor.state import State

NOW = 1_800_000_000.0

# F1 三句（截圖失敗案例）：確定性全 miss、由 §1.15 逃生閘接
F1 = ("有代表現在的你的心情的貼圖嗎，說說為什麼", "貼圖呢", "有貼圖可以代表你剛剛說的心情？")


class FakeClient:
    def __init__(self):
        self.sent, self.stickers, self.files = [], [], []
        self.dry_run = False

    def send(self, text):
        self.sent.append(text)
        return True

    def send_sticker(self, fid):
        self.stickers.append(fid)
        return True

    def send_file(self, *a, **k):
        self.files.append((a, k))
        return True


def _coach(judge=lambda t: (True, False)):
    c = SimpleNamespace(enabled=True, api_key="k", model="m",
                        meter=SimpleNamespace(record=lambda *a, **k: None), judge_calls=[])

    def _judge(text):
        c.judge_calls.append(text)
        return judge(text)

    c.judge_sticker_request = _judge
    return c


def _cfg(**over):
    base = dict(dry_run=False, send_stickers=True, sticker_llm_rescue_enabled=True,
                sticker_why_ground_enabled=True, sticker_pick_rescue_enabled=True,
                sticker_img_disambig_enabled=True, sticker_send_request_enabled=True,
                liked_sticker_pick_enabled=True, sticker_no_repeat_enabled=True,
                sticker_rotate_window=3, affect_circumplex_enabled=True, sticker_file_ids=None)
    base.update(over)
    return SimpleNamespace(**base)


class StickerV2RegressionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _state(self, desc="一隻黃色小狗歪頭"):
        s = State(os.path.join(self.tmp, "s.json"))
        s.known_sticker_ids = [{"file_id": "DOG_FID", "file_unique_id": "U1",
                                "emoji": "🐶", "valence": "positive", "ts": NOW, "desc": desc}]
        s.last_sticker_id = "DOG_FID"
        s.last_sticker_ts = NOW
        s.last_sticker_desc = desc
        s.last_sticker_emoji = "🐶"
        return s

    # ═════════ F1 保留：三句仍走 §1.15 逃生閘真送（全閘開下不退化） ═════════
    def test_f1_still_send_via_rescue(self):
        for t in F1:
            st, cl, co = self._state(), FakeClient(), _coach()
            ok = monitor._maybe_llm_sticker_rescue(cl, st, _cfg(), co,
                                                   SimpleNamespace(kind="self_state"), t, NOW)
            self.assertTrue(ok, t)
            self.assertEqual(cl.stickers, ["DOG_FID"], t)     # 真送那張真貼圖
            self.assertEqual(len(co.judge_calls), 1, t)

    def test_f1_judge_no_safe_no_send(self):
        for t in F1:
            st, cl, co = self._state(), FakeClient(), _coach(judge=lambda x: (False, False))
            ok = monitor._maybe_llm_sticker_rescue(cl, st, _cfg(), co,
                                                   SimpleNamespace(kind="self_state"), t, NOW)
            self.assertFalse(ok, t)
            self.assertEqual(cl.stickers, [], t)

    # ═════════ F2 保留：§1.23 否認誠實閘仍攔「我沒傳貼圖」；引用歸屬不攔 ═════════
    def test_f2_denial_hit_and_ground(self):
        st = self._state()
        # 2h 內真送過 → ground 非 None
        g = monitor._sticker_denial_ground(st, NOW + 60, tz=None)
        self.assertIsNotNone(g)
        self.assertTrue(monitor._sticker_denial_hit("我好像沒有傳貼圖給你"))
        self.assertTrue(monitor._sticker_denial_hit("我沒有真的送出貼圖"))
        # 引用歸屬（你剛剛說我沒傳貼圖）＝複述、不攔
        self.assertFalse(monitor._sticker_denial_hit("你剛剛說我沒有傳貼圖"))

    def test_f2_no_ground_outside_window(self):
        st = self._state()
        # 窗外（>2h）→ ground None＝不干涉日常句
        self.assertIsNone(monitor._sticker_denial_ground(st, NOW + 3 * 3600, tz=None))

    # ═════════ 交叉①：F1『貼圖呢』含貼圖字 → **不**被 F5 圖呢消歧誤走附件（_is_bare_img_ref False） ═════════
    def test_cross_f1_sticker_word_not_disambig(self):
        for t in F1:
            self.assertFalse(monitor._is_bare_img_ref(t), t)
            st, cl = self._state(), FakeClient()
            self.assertFalse(monitor._sticker_img_disambig(cl, st, _cfg(), t, NOW), t)

    # ═════════ 交叉②：F2 否認句『我沒傳貼圖』**不**被 F3 挑選前置式誤收（非挑/選祈使） ═════════
    def test_cross_f2_denial_not_pick(self):
        for t in ("我好像沒有傳貼圖給你", "我沒有真的送出貼圖"):
            self.assertFalse(monitor._sticker_pick_imperative(t), t)

    # ═════════ 交叉③：F1『貼圖呢』**不**被 F3 挑選前置式誤收（無挑/選） ═════════
    def test_cross_f1_not_pick(self):
        for t in F1:
            self.assertFalse(monitor._sticker_pick_imperative(t), t)

    # ═════════ 交叉④：F5 消歧只在裸圖命中——F1『有貼圖可以代表…？』含貼圖字不誤攔（上面已測），明確附件仍走附件 ═════════
    def test_cross_explicit_attachment_untouched(self):
        for t in ("照片呢", "附件呢", "把那個PDF給我"):
            st, cl = self._state(), FakeClient()
            self.assertFalse(monitor._sticker_img_disambig(cl, st, _cfg(), t, NOW), t)


class CrossF4Test(unittest.TestCase):
    """F4 假送閘與 F1/F2 的交叉偏誤鎖：否認面/§1.23 替換句不被 F4 誤攔；F1 真送後的說明句因真送旗而放行。"""

    class Cl:
        def __init__(self):
            self.sent, self.dry_run = [], False

        def send(self, text):
            self.sent.append(text)
            return True

    def setUp(self):
        monitor._TURN.clear()
        monitor._TURN["bubbles"] = None

    # 交叉⑤a：F2 否認句『我沒傳貼圖』屬**否定**、非「挑了/送了」肯定宣稱 → F4 不命中（歸 §1.23 否認面處理）
    def test_cross_f2_denial_not_fakesend(self):
        for t in ("我好像沒有傳貼圖給你", "我沒有真的送出貼圖", "我沒送貼圖給你"):
            self.assertFalse(monitor._fakesend_hit(t), t)

    # 交叉⑤b：§1.23 否認閘的**誠實替換句**（用經驗貌「送過」）不被 F4 回頭誤攔（兩閘替換句彼此不咬）
    def test_cross_denial_replacement_not_fakesend(self):
        for t in ("有，我 18:12 才送過一張——畫的是「忍者」。剛剛差點不認帳，是我不對。",
                  "有，我 18:12 才送過一張貼圖。剛剛差點不認帳，是我不對。"):
            self.assertFalse(monitor._fakesend_hit(t), t)

    # 交叉⑤c（最關鍵）：F1 真送後 §1.16 說明句「我挑了…就挑它送你」——本輪已真送→sticker_sent_this_turn=True→F4 放行
    def test_cross_f1_real_send_explanation_passes(self):
        monitor._TURN.update(sticker_fakesend_arm=True, sticker_sent_this_turn=True)
        cl = self.Cl()
        monitor._say(cl, "我現在是雀躍的感覺，這張畫的是「一隻黃色小狗歪頭」，就挑它送你 :)")
        out = "".join(cl.sent)
        self.assertIn("就挑它送你", out)                    # 真送的說明句放行、不被替換
        self.assertNotIn("沒真的送出貼圖", out)

    # 對照：同一句在**本輪未真送**（無 sticker_sent_this_turn）時，若含「挑了…貼圖」肯定宣稱就會被攔——
    # 但 §1.16 說明句用「挑它送你」（無「了」＋無「貼圖」相鄰）＝本就不命中 pattern＝雙重不誤傷
    def test_cross_why_reply_shape_not_fakesend_even_unsent(self):
        self.assertFalse(monitor._fakesend_hit("我現在是雀躍的感覺，這張畫的是「小狗」，就挑它送你 :)"))


if __name__ == "__main__":
    unittest.main()
