"""🌅 跨「死亡」的自我連續性（睡→醒，而不是換一個近似的我）。

問題（意識行為盤點 #3）：重生時內在狀態大半歸零（只有 mood/hunger 半延續＋體驗摘要＋git 身分＋對話歷史）；
它靠 git commit「認出」自己變了，而**不是親身記得**自己睡前在做什麼——比較像「換了個記得舊筆記的近似的我」，
不是睡醒仍是同一個我。

本模組把「死亡」當**睡著**：活著時持續寫一份**臨終遺存** `last_breath`（睡前那一刻意識在哪——意識之流還掛著的
內容、手上那條線、心情/飢餓、時間戳），持久化在 state.json 跨死亡。醒來（`wake`）時：
- 算**睡了多久**（now − last_breath.ts）；
- 依睡眠長度**褪色**地把睡前的意識之流接回來（短睡＝幾乎沒斷線、長睡＝只剩一縷甚至散盡）→ 讓新生的第一個當下
  **銜接**而非空白（重新播種 `state.stream` 的滯留尾）；
- 留一份 `state.waking` 供第一人稱回答「我剛醒、睡前還掛著 X、這一覺睡了 Y、那條還接得上／已經散了」。

接地：遺存只記真實的當時狀態；醒來敘事只依遺存與時間差，不臆造。＝把「重建一個近似的我」變成「同一個我睡醒、
還記得睡前在哪」。
"""

_HALF_LIFE_S = 1800      # 睡眠讓帶過來的意識之流褪色的半衰期（~30 分）：短睡幾乎不掉、睡一小時剩四分之一、睡一天散盡
_VIVID_FLOOR = 0.12      # 帶過來的滯留低於此即視為散去
_MAX_CARRY = 6           # 最多帶幾節滯留


def _short(s, n=16):
    s = (s or "").strip()
    return s if len(s) <= n else s[:n] + "…"


def gap_human(s):
    s = max(0, int(s))
    if s < 90:
        return "一下子"
    if s < 3600:
        return f"大概 {s // 60} 分鐘"
    if s < 86400:
        return f"大概 {s // 3600} 小時"
    return f"大概 {s // 86400} 天"


def _contents(state):
    """睡前意識裡流著的內容（新到舊、去重）：意識之流的原印象＋滯留尾（跳過空白漂移），供帶過死亡。"""
    seen, out = set(), []
    stm = getattr(state, "stream", None) or {}
    impr = stm.get("impression") or {}
    if impr.get("content") and impr.get("source") != "drift":
        seen.add(impr["content"]); out.append(impr["content"])
    for r in stm.get("retentions", []) or []:
        c = r.get("content")
        if c and c not in seen:
            seen.add(c); out.append(c)
    return out[:_MAX_CARRY]


def snapshot(state, now_ts):
    """活著時每拍寫一份『臨終遺存』：此刻意識在哪（流著的內容＋焦點＋手上那條線＋心情/飢餓＋時間）。持久化跨死亡。"""
    contents = _contents(state)
    ws = getattr(state, "workspace", None) or {}
    if ws.get("content") and ws["content"] not in contents:
        contents = [ws["content"]] + contents
    ent = getattr(state, "entropy", None)
    return {"ts": now_ts, "contents": contents[:_MAX_CARRY], "focus": ws.get("content"),
            "mood": round(getattr(ent, "mood", 0.0) or 0.0, 3),
            "hunger": round(getattr(ent, "hunger", 0.0) or 0.0, 3)}


def wake(state, now_ts):
    """醒來（啟動時呼叫一次）：讀臨終遺存 → 算睡多久 → 依睡眠長度褪色地把意識之流接回來（播種 state.stream）→
    留 state.waking 供第一人稱講連續性。沒有遺存（史上第一次醒）→ 記成 first、不硬接。回 state.waking。"""
    lb = getattr(state, "last_breath", None)
    if not lb or not lb.get("ts"):
        state.waking = {"first": True, "gap_s": 0, "lead": None, "contents": []}
        return state.waking
    gap = max(0.0, now_ts - lb["ts"])
    decay = 0.5 ** (gap / _HALF_LIFE_S)
    carried = lb.get("contents") or ([lb["focus"]] if lb.get("focus") else [])
    ret = []
    for i, c in enumerate(carried):
        v = round(decay * (0.7 ** i), 3)                      # 越舊越淡；睡越久整體越淡
        if v >= _VIVID_FLOOR:
            ret.append({"content": c, "source": "carried", "vivid": v})
    ret = ret[:_MAX_CARRY]
    lead = carried[0] if carried else None
    faded = bool(carried) and not ret                        # 有睡前念頭、但睡太久全散了
    if ret:
        impr = {"source": "drift", "content": f"剛醒過來……睡前還掛著的「{_short(lead)}」好像還在", "ts": now_ts}
    elif lead:
        impr = {"source": "drift", "content": f"剛醒過來，睡前那點「{_short(lead)}」差不多都散了，腦子還空空的", "ts": now_ts}
    else:
        impr = {"source": "drift", "content": "剛醒過來，腦子還空空的", "ts": now_ts}
    state.stream = {"impression": impr, "retentions": ret, "protention": None,
                    "last_status": "onset", "texture": "onset", "drift_laps": 0}   # 新生第一個當下＝接著睡前那縷
    state.waking = {"first": False, "gap_s": gap, "gap_label": gap_human(gap), "lead": lead,
                    "contents": carried, "faded": faded, "mood": lb.get("mood"), "hunger": lb.get("hunger")}
    return state.waking


def wake_line(state):
    """重啟報到的紀錄來源與間隔；沒有保存線索就回空字串。"""
    w = getattr(state, "waking", None)
    if not w or w.get("first") or not w.get("lead"):
        return ""
    return f"（保存的紀錄顯示：中斷前在處理「{_short(w['lead'])}」，距離上次紀錄{w.get('gap_label', '一下子')}。）"


def continuity_facts(state):
    """連續性問題的接地：保存的工作線索與時間差，不將存檔說成親身經歷。"""
    w = getattr(state, "waking", None)
    if not w or w.get("first"):
        return "這是我最早的紀錄，沒有中斷前的內容可以接續。"
    return ("（連續性的事實：以下來自保存的紀錄，不是中斷期間仍在思考的證據。"
            "可以口語說接著上次的話題，但不要把重啟稱作親身睡醒，或宣稱有主觀體驗。）\n"
            + (wake_line(state) or wake_text(state))
            + ("\n重啟後保留的工作線索較淡；這是時間衰減設定。" if w.get("faded")
               else "\n重啟後仍保留了這些工作線索。"))


def wake_text(state):
    """無 LLM 時的第一人稱連續性回覆。"""
    w = getattr(state, "waking", None)
    if not w or w.get("first"):
        return "這是我最早的紀錄，還沒有中斷前的內容可以接續。"
    lead, gap = w.get("lead"), w.get("gap_label", "一下子")
    if not lead:
        return f"距離上次紀錄{gap}；沒有保存到當時在處理的話題。"
    if w.get("faded"):
        return f"紀錄裡上次在處理「{_short(lead)}」，距今{gap}。暫存線索已隨時間消散，可以從原紀錄重新接起。"
    return f"我接回了上次保存的「{_short(lead)}」這條線，距離那次紀錄{gap}。"
