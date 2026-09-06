"""🧹 §1.29 貼圖記憶註解外洩守門＋🕐 §1.30 主動 emit 時間幻覺守門。

截圖（2026-07-12 08:15/09:02）：
- §1.29：bot 首句冒出「（我送了一張貼圖：抱抱——(動態貼圖…未讀畫面)）」——這是 §1.23 寫進 convo_history 的
  **內部記憶註解**（給 LLM 知道自己送過貼圖用），卻被當成「bot 說過的話」由下一輪 LLM 照抄吐出來。
  修：_say 送出前統一洗掉此註解（比照 strip_leaked_time_tags 的單一防線，互動＋主動全覆蓋）。
- §1.30：「🫧 嗨，下午三點了！」——主動 emit 的開場白由 LLM 生成、無此刻時間接地、prompt 也沒禁報鐘點
  → LLM 自己編了「下午三點」（實際 09:02）。修：spontaneous voice prompt 禁報具體鐘點＋餵正確時段。
"""

import os
import unittest
from unittest.mock import patch

from telegram_monitor import monitor, persona


class StickerAnnotationLeakTest(unittest.TestCase):
    """§1.29：送出前洗掉「（我送了一張貼圖…）」內部註解——不論互動或主動路徑。"""

    ANNO = "（我送了一張貼圖：🫂——一個吊在繩上的忍者）"

    def test_strips_leading_annotation(self):
        with patch.dict(os.environ, {"STICKER_SENT_MEMORY": "1"}):
            out = monitor._strip_sticker_memo(self.ANNO + "\n哎呀，聽到你超難過，我也覺得有點悶悶的。")
        self.assertNotIn("我送了一張貼圖", out)
        self.assertIn("哎呀", out)

    def test_strips_standalone_annotation(self):
        with patch.dict(os.environ, {"STICKER_SENT_MEMORY": "1"}):
            out = monitor._strip_sticker_memo(self.ANNO)
        self.assertEqual(out.strip(), "")

    def test_strips_animated_desc_variant(self):
        anno = "（我送了一張貼圖：抱抱——(動態貼圖，帶 🫂、出自「抱抱」，未讀畫面)）"
        with patch.dict(os.environ, {"STICKER_SENT_MEMORY": "1"}):
            out = monitor._strip_sticker_memo(anno + "真的辛苦了。")
        self.assertNotIn("我送了一張貼圖", out)
        self.assertIn("真的辛苦了", out)

    def test_real_message_untouched(self):
        msg = "我送你一張貼圖好嗎？你喜歡哪一種？"     # 「送你一張貼圖」不是註解格式（無「（我送了一張貼圖」）
        with patch.dict(os.environ, {"STICKER_SENT_MEMORY": "1"}):
            self.assertEqual(monitor._strip_sticker_memo(msg), msg)

    def test_flag_off_no_strip(self):
        with patch.dict(os.environ, {"STICKER_SENT_MEMORY": "0"}):
            self.assertEqual(monitor._strip_sticker_memo(self.ANNO), self.ANNO)   # 關＝逐位元同現狀

    # ── §1.29 補遺（2026-07-22 15:57 實洩復發）：LLM 照抄註解時會**改寫**——丟「我」、外層全形括號
    # 變半形（實洩結尾是兩個半形 ))）——舊 regex（錨「（我送了一張貼圖」＋只認全形收尾）完全不命中。
    def test_leaked_paraphrase_exact_string_stripped(self):
        leaked = ("（送了一張貼圖： 💭——(動態貼圖，帶 💭、出自「Pinku The Cat」，未讀畫面))\n"
                  "你今天午後又提到了「閱讀｜讀誦經書」這件事。")
        with patch.dict(os.environ, {"STICKER_SENT_MEMORY": "1"}):
            out = monitor._strip_sticker_memo(leaked)
        self.assertNotIn("送了一張貼圖", out)
        self.assertNotIn("Pinku", out)
        self.assertIn("你今天午後又提到了", out)

    def test_paraphrase_variants_stripped(self):
        for anno in ("(我送了一張貼圖：🫂——忍者)",                       # 全半形反轉
                     "（剛剛送了一張貼圖：💭——(動態貼圖))",              # 剛剛變體＋巢狀＋半形收尾
                     "（送了一張貼圖）"):                                # 極簡形
            with patch.dict(os.environ, {"STICKER_SENT_MEMORY": "1"}):
                out = monitor._strip_sticker_memo(anno + "正文在這。")
            self.assertNotIn("送了一張貼圖", out, anno)
            self.assertIn("正文在這", out, anno)

    def test_unbalanced_leak_strips_to_line_end(self):
        # 括號永遠配不平時（LLM 亂寫）＝刪到行尾——洩漏獨立成段、正文在下一行不受傷
        leaked = "（送了一張貼圖： 💭——(動態貼圖，帶 💭\n真正想說的話在這一行。"
        with patch.dict(os.environ, {"STICKER_SENT_MEMORY": "1"}):
            out = monitor._strip_sticker_memo(leaked)
        self.assertNotIn("送了一張貼圖", out)
        self.assertIn("真正想說的話在這一行", out)

    def test_plain_speech_about_sending_untouched(self):
        # 沒有前導括號＝bot 正常口語（「我剛剛送了一張貼圖給你」）——不是註解、不洗
        msg = "我剛剛送了一張貼圖給你，有收到嗎？"
        with patch.dict(os.environ, {"STICKER_SENT_MEMORY": "1"}):
            self.assertEqual(monitor._strip_sticker_memo(msg), msg)


class ProactiveClockGroundTest(unittest.TestCase):
    """§1.30：主動出聲的 prompt 禁報具體鐘點＋帶正確時段。"""

    def test_spontaneous_user_forbids_clock_when_grounded(self):
        u = persona.spontaneous_user("繞回讀誦經書那條線", "", daypart="早上")
        self.assertIn("早上", u)
        for kw in ("不報", "別報", "不要報", "鐘點", "幾點"):
            if kw in u:
                break
        else:
            self.fail("spontaneous_user 應含禁報鐘點的明令")

    def test_spontaneous_user_default_bit_identical(self):
        # daypart 空（預設）＝逐位元同現狀（不注入時段/禁令）
        base = persona.spontaneous_user("繞回讀誦經書那條線", "")
        self.assertNotIn("此刻是", base)
        self.assertNotIn("不報具體鐘點", base)


if __name__ == "__main__":
    unittest.main()
