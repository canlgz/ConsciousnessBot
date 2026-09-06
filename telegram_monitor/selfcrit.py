"""🙇 反自責反芻：偵測 bot 的回覆是不是陷在『反覆道歉／檢討自己』的迴圈——即使已經答對了，
還一直「我搞砸了、又把數字報出來、我會努力學會、下次好好回答」。

根因：被誤路由吐錯後的道歉進了對話歷史，LLM 反芻那個自責語氣（截圖：問「多久沒理你」已答對
「七個小時」，卻仍尾隨一串自責）。這裡只做**輕量字串偵測**（給對話路徑「重生成一次」用）＋剝掉純自責段的兜底。
一次誠懇的致歉是人之常情、**不攔**；攔的是**堆疊／跨則反覆**的反芻。
"""

import re

# 兩類記號，刻意挑**明確**詞組、避免誤傷正常內容（不收裸「我又」「我太」——「我又想到一條」不是自責）：
# ① 道歉/認錯（回顧）——單獨出現一次是人之常情（道歉＋更正），**不單獨當反芻**。
_APOLOGY = (
    "抱歉", "對不起", "不好意思", "請原諒", "原諒我", "見諒", "真的很抱歉",
    "搞砸", "我搞錯", "我弄錯", "會錯意", "我又錯", "我又會錯", "我又把", "又把數字", "又把那些數字",
    "又報了", "又報錯", "數字報出來", "把數字跑出來", "我不該", "都是我的錯", "是我的錯", "我的疏忽",
    "我失誤", "我又失常", "讓你失望", "我太遜",
)
# ② 未來檢討承諾（前瞻）——「我會努力學會／下次好好回答」這種**幾乎只在反芻時出現**（對 bot 而言是填充話），
#    出現一次就算反芻的強訊號。
_PROMISE = (
    "我會努力", "努力學會", "我會學會", "我會好好", "好好回答", "下次你問", "下次我會", "下次好好",
    "下次一定", "我會改進", "我會記住下次", "我會記得下次",
)
_SENT = re.compile(r"[\n。！？!?…]+")


def _blame_hits(text):
    t = text or ""
    return sum(1 for b in _APOLOGY if b in t) + sum(1 for b in _PROMISE if b in t)


def recent_model_blamed(history, k=3):
    """最近 k 則 bot 自己的話裡，是不是已經在自責/道歉（→ 這則又自責＝跨則反芻）。"""
    seen = 0
    for turn in reversed(history or []):
        if turn.get("role") != "model":
            continue
        if _blame_hits(turn.get("text") or ""):
            return True
        seen += 1
        if seen >= k:
            break
    return False


def is_self_blame_spiral(reply, history=None):
    """回覆是否陷在反覆自責：① 出現未來檢討承諾（我會努力學會／下次好好回答）＝反芻填充；
    或 ② 帶道歉/認錯、且最近幾則 bot 已經在道歉（跨則反芻）。**單一句誠懇致歉＋更正**（且近期沒在道歉）不算。"""
    t = reply or ""
    if any(p in t for p in _PROMISE):
        return True
    return any(a in t for a in _APOLOGY) and recent_model_blamed(history)


def strip_self_blame(reply):
    """剝掉**純自責/道歉**的句子，保留有實質內容的；剝完沒料 → 原樣退回（交呼叫端重生成）。回 (清過, 是否有剝)。"""
    segs = [s.strip() for s in _SENT.split(reply or "") if s.strip()]
    if len(segs) <= 1:
        return reply, False
    kept = [s for s in segs if _blame_hits(s) == 0]
    if not kept or len(kept) == len(segs):
        return reply, False
    return "。".join(kept) + "。", True
