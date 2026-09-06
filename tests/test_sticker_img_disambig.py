"""🎴🧠 §1.34 STICKER_V2/F5 貼圖情境「圖呢／圖」消歧（STICKER_IMG_DISAMBIG）：is_attachment_request('圖呢')=True、
('圖')=True（裸『圖』是 _MEDIA_CUES 媒體線索）→ route=attachment → coach.select_attachments + _present_attachments
撈 Drive 記寫附件、傳咖啡廳照片（今日 red）。貼圖情境下的『圖呢/圖』是在問**剛送的那張貼圖**、非要調記寫附件。

消歧須上移到有 state 的層（selfstate 純函式無 state 可判『近期剛送過貼圖』）→ 在 attachment 分派段最前、
select_attachments 之前插 _sticker_img_disambig：近期貼圖情境＋裸圖省略句 → 不撈附件、改走貼圖誠實路徑。

以直接呼叫 _sticker_img_disambig 決策點驗證（回 True＝呼叫端 return、附件段整段不執行＝select_attachments 到不了）。
全 stub、零網路。
"""

import os
import tempfile
import unittest
from types import SimpleNamespace

from telegram_monitor import monitor, selfstate
from telegram_monitor.state import State

NOW = 1_800_000_000.0


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


def _cfg(**over):
    base = dict(dry_run=False, sticker_img_disambig_enabled=True, send_stickers=True)
    base.update(over)
    return SimpleNamespace(**base)


class ImgDisambigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _state(self, real_send=True, near=True, desc="一隻黃色小狗歪頭"):
        s = State(os.path.join(self.tmp, "s.json"))
        s.known_sticker_ids = [{"file_id": "DOG_FID", "file_unique_id": "U1",
                                "emoji": "🐶", "valence": "positive", "ts": NOW if near else 0, "desc": desc}]
        if real_send:
            s.last_sticker_id = "DOG_FID"
            s.last_sticker_ts = NOW if near else 0
            s.last_sticker_desc = desc
            s.last_sticker_emoji = "🐶"
        else:
            s.last_sticker_id = None
            s.last_sticker_ts = 0        # 沒真送過（可能只教過）；near 由 known ts 撐起 recent_ctx
        return s

    # ───────── 前提釘死：is_attachment_request('圖呢')=('圖')=True（今日恆走 attachment 撈附件） ─────────
    def test_bare_img_is_attachment_request_today(self):
        for t in ("圖呢", "圖"):
            self.assertTrue(selfstate.is_attachment_request(t), t)
        # 對照：明確附件詞仍 True（不受影響）
        for t in ("照片呢", "附件呢", "把那個PDF給我"):
            self.assertTrue(selfstate.is_attachment_request(t), t)

    # ───────── GREEN：旗標開＋近期真送＋『圖呢/圖』→ 消歧攔（回 True）、談剛送那張、零 send_file/send_sticker ─────────
    def test_bare_img_disambig_grounds_sent_sticker(self):
        for t in ("圖呢", "圖"):
            st, cl = self._state(), FakeClient()
            ok = monitor._sticker_img_disambig(cl, st, _cfg(), t, NOW)
            self.assertTrue(ok, t)                        # 消費本輪＝呼叫端 return、select_attachments 到不了
            self.assertEqual(cl.files, [], t)             # 不撈附件
            self.assertEqual(cl.stickers, [], t)          # 消歧只談、不重送
            self.assertTrue(any("一隻黃色小狗歪頭" in s for s in cl.sent), t)  # 據實談剛送那張的畫面

    # ───────── 偏誤鎖①：旗標關（getattr 預設 False）→ 回 False（照走 attachment）＝逐位元同現狀 ─────────
    def test_flag_off_falls_to_attachment(self):
        for t in ("圖呢", "圖"):
            st, cl = self._state(), FakeClient()
            ok = monitor._sticker_img_disambig(cl, st, _cfg(sticker_img_disambig_enabled=False), t, NOW)
            self.assertFalse(ok, t)
            self.assertEqual(cl.sent, [], t)

    # ───────── 偏誤鎖②：無近期貼圖情境（known 老、無真送）→ 回 False（照走 attachment） ─────────
    def test_no_recent_ctx_falls_to_attachment(self):
        for t in ("圖呢", "圖"):
            st, cl = self._state(real_send=False, near=False), FakeClient()
            ok = monitor._sticker_img_disambig(cl, st, _cfg(), t, NOW)
            self.assertFalse(ok, t)
            self.assertEqual(cl.sent, [], t)

    # ───────── 偏誤鎖③：明確附件詞『照片呢/附件呢/把那個 PDF 給我』即使 recent_ctx=True 也照走 attachment ─────────
    def test_explicit_attachment_still_attachment(self):
        for t in ("照片呢", "附件呢", "把那個PDF給我", "語音呢"):
            st, cl = self._state(), FakeClient()
            ok = monitor._sticker_img_disambig(cl, st, _cfg(), t, NOW)
            self.assertFalse(ok, t)
            self.assertEqual(cl.sent, [], t)

    # ───────── 偏誤鎖④：近期貼圖情境但無真送（last_sticker_id 空、只教過）→ 誠實『沒送』句、仍不撈附件 ─────────
    def test_recent_ctx_no_real_send_honest(self):
        for t in ("圖呢", "圖"):
            st, cl = self._state(real_send=False, near=True), FakeClient()
            ok = monitor._sticker_img_disambig(cl, st, _cfg(), t, NOW)
            self.assertTrue(ok, t)                        # 仍消費本輪、不撈附件
            self.assertEqual(cl.files, [], t)
            self.assertTrue(any("沒送貼圖" in s for s in cl.sent), t)

    # ───────── switch item②：SEND_STICKERS=0＋『圖呢』→ 不撈附件、走維護誠實句（不拿別的圖代替） ─────────
    def test_send_stickers_off_maintenance_line(self):
        for t in ("圖呢", "圖"):
            st, cl = self._state(), FakeClient()
            ok = monitor._sticker_img_disambig(cl, st, _cfg(send_stickers=False), t, NOW)
            self.assertTrue(ok, t)
            self.assertEqual(cl.files, [], t)
            self.assertEqual(cl.stickers, [], t)
            self.assertTrue(any("維護中" in s for s in cl.sent), t)

    # ───────── 交叉：『貼圖呢』含貼圖字＝談貼圖本身、非裸圖 → 不被消歧誤攔（交 §1.15 逃生閘） ─────────
    def test_sticker_word_not_disambig(self):
        st, cl = self._state(), FakeClient()
        ok = monitor._sticker_img_disambig(cl, st, _cfg(), "貼圖呢", NOW)
        self.assertFalse(ok)
        self.assertEqual(cl.sent, [])


if __name__ == "__main__":
    unittest.main()
