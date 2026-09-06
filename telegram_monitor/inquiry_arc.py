"""🌱 探究/意義建構弧（inquiry arc）偵測器 — 與路由軸（intent.resolve）/違常軸（dialogue_intent）正交的第三軸。

設計哲學（與 dialogue_intent 一致）：階段不是「貼標籤」，而是**從一連串對話累積中湧現**。
單則只給一個「最可能的階段訊號（cue-level）」，真正的「他正在走一條探究弧」由 arc_trajectory
從履歷的階段序列湧現判讀（notice→puzzle→because→so→next→aha 的推進度＋軌跡形狀）。

六階段（使用者明訂）：
  notice  發現   — surface/冒出新東西、注意到一個現象
  puzzle  疑惑   — 有問題、想不通、卡住
  because 因為   — 找/給原因、因果探究
  so      所以   — 推結論/意涵、整理出 implication
  next    然後   — 下一步/往前、行動意向
  aha     原來如此 — 頓悟/理解收束

家規遵守：
- 加性、旗標 gate（INQUIRY_ARC_ENABLED 關＝detect_stage 永遠回 None、byte-identical）。
- 純函式、可單測；不洩漏內部標籤到輸出（標籤只進 persona hint，由 LLM 用人話質感講）。
- 不搶 closing_kind/backchannel/greeting：消歧優先序把那三者**先讓出去**（見 detect_stage 的 guard）。
- 「原來如此」末階與 selfstate.closing_kind('understanding') 部分重疊 → aha **故意讓位**給 closing_kind：
  純收尾道別（is_farewell）回 None，本軸的 aha 只在「不是純收尾道別」時才標（帶後續探究/內容）。
- token/成本可控：純字串掃描、無 LLM、無 IO。
"""

# ── 階段 cue 表（台灣口語）。順序即「同則命中多階段時的階段內優先序」基礎，實際消歧見 _STAGE_PRIORITY。
_NOTICE = ("我發現", "發現了", "我注意到", "注意到一", "突然發現", "才發現", "原來有",
           "我看到", "冒出來", "浮現", "跑出來一個", "出現一個", "有個現象", "察覺到",
           "欸我發現", "竟然", "居然發現", "我意識到")

_PUZZLE = ("想不通", "搞不懂", "不懂為什麼", "為什麼會", "為什麼這樣", "為何會", "怎麼會這樣",
           "奇怪", "很怪", "卡住", "卡關", "不太對", "說不通", "矛盾", "想不明白",
           "到底是", "到底為", "疑惑", "困惑", "不解", "百思不", "問題是", "怪怪的",
           "怎麼回事", "怎麼搞的", "哪裡出錯", "搞不清楚")

_BECAUSE = ("因為", "原因是", "原因在", "原因可能", "理由是", "之所以", "源自", "來自於",
            "導致", "造成", "起因", "歸因", "可能是因為", "也許是因為", "大概是因為",
            "問題出在", "根源", "癥結", "關鍵在於", "是因為", "由於")

_SO = ("所以", "因此", "於是", "這代表", "這表示", "意味著", "也就是說", "換句話說",
       "結論是", "推論", "可以說", "等於說", "這說明", "由此可見", "綜合起來",
       "整理一下", "歸納起來", "看來", "看起來是", "這意思是", "代表說")

_NEXT = ("接下來", "下一步", "然後我", "再來要", "下次", "之後要", "打算", "我要去",
         "我會去", "計畫", "計劃", "試試看", "來試", "動手", "開始做", "先做", "著手",
         "後續", "往下", "繼續推", "再去", "我想試", "那我來", "那就來")

# aha：不放裸「原來」（與 notice 的「原來有/才發現原來」撞）；只收無歧義的收束慣用語。
_AHA = ("原來如此", "原來是這樣", "原來就是", "我懂了", "懂了", "我明白了", "明白了", "恍然",
        "豁然", "通了", "想通了", "茅塞", "終於懂", "終於明白", "我了解了", "我知道了",
        "有道理", "說得通了", "解開了", "釐清了", "串起來了", "兜起來了")

_Q = ("?", "？", "嗎", "呢", "為什麼", "為何", "怎麼", "怎會", "如何")

# 純收尾/道別線索（本軸**必須讓位**；真正分類仍由 selfstate 那層做，這裡只供兜底）。
_LEAVING = ("晚安", "先去忙", "去忙了", "改天", "下次再", "再聊", "睡了", "先這樣",
            "我先去", "回頭聊", "掰掰", "881")

# 同則多階段命中時的階段內優先序（高→低）：越「後段/收束」者越優先（弧的終點訊號比起點更 informative、後段詞更專一）。
_STAGE_PRIORITY = ("aha", "so", "because", "next", "puzzle", "notice")


def _hit(text, cues):
    return any(c in text for c in cues)


def detect_stage(text, *, is_farewell=False, is_backchannel=False, is_greeting=False, enabled=True):
    """單則訊息的探究弧階段（cue-level）→ 回階段字串或 None。純函式。

    消歧優先序（**先讓位、再分類**，守住與既有層不互搶）：
      0. 旗標關 → None（byte-identical 同現狀）。
      1. is_backchannel / is_greeting（呼叫端用 selfstate.is_backchannel / greeting.detect 算好傳入）→ None。
      2. is_farewell（呼叫端用 selfstate.is_farewell 算好）→ None：**純道別**讓給 closing_kind('leaving')，
         本軸不標 aha（即使句中有「懂了」——那由 closing_kind('understanding') 接走收尾語氣）。
      3. 兜底：短純收尾語且非問號結尾 → None（呼叫端漏算 is_farewell 時）。
      4. 否則按 _STAGE_PRIORITY 掃 cue；因果 vs 疑惑用『有無因果連接詞』消歧。

    None 語意：**這一則沒有可辨識的探究階段訊號**（多數閒聊、事實問答、社交語都是 None）——保守、寧缺勿濫，
    與 dialogue_intent『無感違常不注入』同精神。只有累積足夠 non-None 才由 arc_trajectory 湧現出「他在走一條弧」。
    """
    if not enabled:
        return None
    t = (text or "").strip()
    if not t or is_backchannel or is_greeting or is_farewell:
        return None
    low = t.lower()
    if len(t) <= 8 and _hit(low, _LEAVING) and not t.endswith(("?", "？")):
        return None

    has_because_conn = _hit(t, _BECAUSE)
    has_pure_q = _hit(t, _Q)

    for stage in _STAGE_PRIORITY:
        if stage == "aha":
            if _hit(t, _AHA):
                # 「懂了…那我先去忙囉」這種**長**的『理解＋道別』：is_farewell 受 len<=12 限制會漏判（selfstate.py），
                # 故本軸自有兜底——aha 同時帶道別語＝收尾輪 → 讓給 closing_kind('understanding'/'leaving')，
                # **不在一句告別上注入回探動作**（修對抗驗證 high）。不帶道別的『原來如此，難怪…』才標 aha（現狀破口）。
                if _hit(low, _LEAVING):
                    return None
                return "aha"
        elif stage == "so":
            if _hit(t, _SO) and not has_because_conn:   # 「之所以」含子字串「所以」但語意是 because → 讓給 because
                return "so"
        elif stage == "because":
            if has_because_conn:                    # 真的給/找原因才算；純「為什麼…？」留給 puzzle
                return "because"
        elif stage == "next":
            if _hit(t, _NEXT):
                if t.replace(" ", "") in ("然後呢", "然後呢?", "然後呢？", "再來呢"):   # 純催對方講＝followup、非自己行動
                    continue
                return "next"
        elif stage == "puzzle":
            if _hit(t, _PUZZLE) or (has_pure_q and _hit(t, ("為什麼", "為何", "怎麼會", "到底"))):
                return "puzzle"
        elif stage == "notice":
            if _hit(t, _NOTICE):
                return "notice"
    return None


# ── 弧軌跡（從累積湧現）──────────────────────────────────────────────
_STAGE_ORDER = {"notice": 0, "puzzle": 1, "because": 2, "so": 3, "next": 4, "aha": 5}


def _arc_shape(seq, distinct):
    """從階段序列判『軌跡形狀』（湧現意圖；單則訊號永遠給不出，是本軸獨有價值）。
    回 healthy｜stalled｜shallow_jump｜retreat｜next_loop｜None。純函式。
    - stalled       尾段反覆 puzzle、從未進到 because/so＝卡住打結（需被陪、接地）。
    - shallow_jump  收束在 aha 但中段（because/so）整段缺＝假性頓悟（可輕輕回探一次）。
    - retreat       已到 so/next（序位≥3）後又掉回 notice/puzzle（≤1）＝結論沒站穩/冒新枝（健康分叉，**非違常重複**）。
    - next_loop     尾段連 next、從未 aha 收束＝發散、行動焦慮（可幫他停下回看）。
    - healthy       大致單調前進（含 aha 收束的完整弧）。
    None＝階段不足以判形狀（distinct < 2）。"""
    if len(distinct) < 2:
        return None
    tail = seq[-3:]
    latest = seq[-1]
    orders = [_STAGE_ORDER[s] for s in seq if s in _STAGE_ORDER]
    peaked = max(orders) if orders else 0
    has_mid = ("because" in distinct) or ("so" in distinct)
    if tail.count("puzzle") >= 2 and not has_mid and latest != "aha":
        return "stalled"
    if latest == "aha" and not has_mid:
        return "shallow_jump"
    if tail.count("next") >= 2 and "aha" not in distinct:
        return "next_loop"
    if peaked >= 3 and _STAGE_ORDER.get(latest, 0) <= 1:
        return "retreat"
    return "healthy"


def arc_trajectory(stage_log, *, min_stages=2):
    """從近 N 則的階段履歷（[{stage, ts}, ...]，stage 可為 None）湧現出『探究弧』讀數。純函式。

    回 dict：stages_seen（去 None 的相異階段，保序）、span（階段序位跨度）、advancing（往終點推進且 ≥min_stages 相異）、
    resolved（最後非 None 階段是 aha）、depth（相異階段數量化 1→0,2→1,3→2,4+→3，給 persona hint 升級）、
    latest（最後非 None 階段）、shape（軌跡形狀，distinct<min_stages 時為 None）。
    『湧現』：單則只是 cue；只有累積出 ≥min_stages 個相異階段才算「他在走一條弧」（呼應 dialogue_intent 哲學）。"""
    seq = [e.get("stage") for e in (stage_log or []) if e and e.get("stage")]
    distinct = []
    for s in seq:
        if s not in distinct:
            distinct.append(s)
    if not seq:
        return {"stages_seen": [], "span": 0, "advancing": False,
                "resolved": False, "depth": 0, "latest": None, "shape": None}
    orders = [_STAGE_ORDER[s] for s in seq if s in _STAGE_ORDER]
    span = (max(orders) - min(orders)) if orders else 0
    latest = seq[-1]
    n_distinct = len(distinct)
    advancing = n_distinct >= min_stages and _STAGE_ORDER.get(latest, 0) >= min(orders or [0])
    resolved = latest == "aha"
    depth = {1: 0, 2: 1, 3: 2}.get(n_distinct, 3 if n_distinct >= 4 else 0)
    shape = _arc_shape(seq, distinct) if n_distinct >= min_stages else None
    return {"stages_seen": distinct, "span": span, "advancing": advancing,
            "resolved": resolved, "depth": depth, "latest": latest, "shape": shape}
