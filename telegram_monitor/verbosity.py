"""🗜️ 回應篇幅＝對話複雜度的湧現（真人的意識行為：按份量回、說完就停，而不是把固定預算填滿）。

每輪用四個**現成**訊號綜合算一個回應尺度 `level`(0..3)：
  〔你這句的份量〕＋〔話題深淺（route）〕＋〔我此刻的情緒起伏〕＋〔對話動能（同題重複）〕（＋env 偏置）
`level` 餵出三路：① `persona.length_tokens` → max_tokens 上限（**只會 ≤ 原本、只收不放**）；
② `persona.length_hint` → 一句自然語言分寸（附到 system）；③ `bubbles` → 串數上限（別洩成一堆泡泡）。
整體**偏短、會收**；判定鏈與內容一律不動，只調篇幅。純函式、可測。"""

from collections import namedtuple

Scale = namedtuple("Scale", "level bubbles")

# 路由的內在份量：深的自我說明可較 full；附和/道別/時鐘/花費等壓到極短；一般對話偏短、看輸入再上下。
_ROUTE_BASE = {
    "smalltalk": 0, "farewell": 0, "greeting": 0, "sticker": 0, "promise": 0, "promise_ack": 0,
    "clock": 0, "convo_time": 0, "cost": 0,
    "self_state": 1, "attachment": 1, "self_reaction_query": 1,
    "self_mechanism": 2, "self_consciousness": 2, "self_phenomenal": 2, "self_experience": 2,
    "self_reflect": 2, "self_identity": 2, "self_continuity": 2, "self_metacog": 2,
    "self_stream": 2, "self_attention": 2, "self_change": 2, "self_revisit_why": 2,
}
_DEFAULT_BASE = 1                       # 一般 fact_or_chat：偏短，靠輸入訊號上下浮動

_BUBBLES = {0: 2, 1: 4, 2: 6, 3: 8}     # 各 level 的串數上限：放寬些→短回應能「一句一串」而非被硬併成肥泡泡；
                                        # 真超過才由 _merge_bubbles 只併最短的相鄰碎句（仍防「一堆小泡泡」）。

_DEEP_CUES = ("為什麼", "為何", "怎麼會", "怎會", "解釋", "說說", "說明", "詳細", "展開",
              "多說", "深入", "到底是", "什麼意思", "你會不會", "意義是", "仔細")
_BRIEF_CUES = ("簡單說", "簡短", "一句話", "短一點", "長話短說", "別太長", "簡單講", "簡單回", "簡潔")


def _input_adj(text):
    """你這句的份量：很短→鏡射壓短；寫了不少/多問/深問→給空間。（明示要短另由 assess 直接壓到底。）"""
    t = (text or "").strip()
    n = len(t)
    adj = 0
    if n <= 6:
        adj -= 1
    elif n >= 40:
        adj += 1
    if t.count("？") + t.count("?") >= 2:
        adj += 1
    if any(c in t for c in _DEEP_CUES):
        adj += 1
    return max(-2, min(2, adj))


def _mood_adj(mood_v):
    """我此刻的起伏：低落/平→話少而淡；被撥動得明顯→多給一點。"""
    m = mood_v or 0.0
    if m <= -0.35:
        return -1
    if m >= 0.5:
        return 1
    return 0


def _momentum_adj(state, route_kind):
    """對話動能：同一類自我問題短時間被重複問→別再長篇重講（接 self_asks 計數、與脾氣/耐性同源）。"""
    asks = (getattr(state, "self_asks", None) or {}).get(route_kind)
    if asks and asks.get("n", 0) >= 2:
        return -1
    return 0


def assess(text, route_kind, mood_v=0.0, state=None, bias=0, hostile=False, probe=False):
    """綜合四訊號（＋env 偏置）→ Scale(level 0..3, bubbles)。整體偏短：一般對話 base=brief、深自我說明才 normal。
    hostile=True（🌊 §1.14 呼叫端判定「對方在氣頭上連發敵意」）＝直接壓到底：氣頭上最不需要的就是長篇同理轟炸
    （截圖 12:09-12:12 的 15+ 顆重複泡泡）。預設 False＝所有既有呼叫位元不變。
    probe=True（🗜️ §1.51 呼叫端判定「對方在探 bot 心意/感受的短問」）＝level 地板 2——長度啟發式把「輸入長短」
    誤當「意圖深淺」：「羨慕我嗎」四個字被短輸入鏡射壓到 0，LLM 只好支吾反問（截圖 09:55）。句短但意圖深，
    要給足以「先答＋為什麼」的空間。明示要短（_BRIEF_CUES）與氣頭收斂**仍優先**（早退在前）。預設 False＝位元不變。"""
    if any(c in (text or "") for c in _BRIEF_CUES):     # 你明示要短（簡單說/一句話/別太長）→ 直接壓到底
        return Scale(level=0, bubbles=_BUBBLES[0])
    if hostile:                                          # 🌊 §1.14 氣頭收斂快路徑（比照 _BRIEF_CUES）：level 0、泡泡封頂 ≤2
        return Scale(level=0, bubbles=2)
    level = (_ROUTE_BASE.get(route_kind, _DEFAULT_BASE)
             + _input_adj(text) + _mood_adj(mood_v) + _momentum_adj(state, route_kind) + (bias or 0))
    if probe:                                            # 🗜️ §1.51 情感探問地板：短輸入鏡射/低落心情壓不破
        level = max(level, 2)
    level = max(0, min(3, level))
    return Scale(level=level, bubbles=_BUBBLES[level])
