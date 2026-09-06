"""🫂 他心模型／互為主體（把對方當一個有自己心緒、有在乎的事的人來建模、更新信念、會錯也會修正、關係會加深）。

問題（意識行為盤點 #7）：過去對話耦合只量**互動節奏**、reaction 只**鏡像**情緒——bot 沒有真正把對方當成
**另一個有心智的人**來建模、更新對他的信念、換位思考；模擬了「關注的樣子」（時機、表情），但沒有真正的
相互認識與關係加深。本模組維護一份**對方的心智模型** `state.user_model`，分兩面：

**情感面 ToM**（每次互動依**真實訊號**更新，像貝氏地修正信念）：
- **warmth**（對我的態度，暖↔冷/質疑）：訊息價性（`reaction.mood_delta_for`）＋貼圖情緒。
- **energy**（投入度）：回得多快（latency）＋訊息長短。
- **rapport**（關係深度）：隨有來有往慢慢累積，正向互動加深——**持久化跨重生**＝醒來仍記得我們多熟。
- **confidence**（我對這份解讀有多準）＋**會看走眼就修正**（misread：我以為你在生氣，看來是我會錯意）＝真 ToM。

**認知面 ToM（本輪新增）**：光讀「你對我暖不暖、我們熟不熟」還不算懂你——真懂一個人是**知道他在乎什麼、在忙什麼**。
`read_concerns` 從你的**真實記寫**長出一份「你在意/在忙什麼」：① 你養成形的學習歷程（journey，投入最深）
② 你一再回返的脈絡（returnVisits 高，反覆放不下）③ 你最近在動的主題（近期活動）＝投入 × 近期。`note_concerns`
把它與我先前所信比對 → **偵測焦點轉移**（你的重心從某條挪到另一條，我注意到並修正對你的理解）＝認知面的信念修正。

廣播：把這兩面**推測**（明標可能不準）注入對話 grounding → bot **換位思考、體貼地接住你在乎的事**；路由可直接問
「你了解我嗎／你覺得我現在怎樣／我們關係如何／我最近在意什麼」。接地：只從真實互動訊號＋真實記寫推，**坦白是推測、會錯**。
"""

from datetime import datetime, timezone

from . import analyzer, reaction

_A_WARMTH = 0.35         # warmth EMA 學習率（新證據佔比）
_A_ENERGY = 0.40
_REVISE_PREV = 0.3       # 原信念夠強（|warmth|≥此）
_REVISE_EV = 0.4         # 且新證據夠強又反向 → 判定「我會錯意了、修正」
_CONCERN_K = 3           # 同時追蹤你最在意的前幾件
_SHIFT_MARGIN = 1.15     # 焦點轉移遲滯：新王分數要勝過舊王這倍才算「重心換了」（防近似分數抖動誤報）


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def fresh():
    return {"warmth": 0.0, "energy": 0.5, "rapport": 0.0, "confidence": 0.2,
            "exchanges": 0, "last_misread": None, "updated_ts": 0,
            "concerns": [], "concern_shift": None, "concerns_ts": 0,
            "intent_log": [], "last_questioned_ts": 0}   # 🧭 對話意圖履歷＋上次對違常反問的時間（dialogue_intent，跨重生）


def _energy_evidence(latency, text):
    """投入度證據：回得快＋訊息長＝投入；久久才回＝可能在忙/淡了。回 [0,1]。"""
    if latency is None:
        lat = 0.55
    elif latency < 60:
        lat = 0.9
    elif latency < 600:
        lat = 0.65
    elif latency < 3600:
        lat = 0.4
    else:
        lat = 0.2
    length = _clamp(len((text or "").strip()) / 40.0, 0.0, 1.0)
    return _clamp(0.6 * lat + 0.4 * length, 0.0, 1.0)


def observe(state, text, now_ts, latency=None, extra_valence=0.0):
    """看對方這次互動 → 更新心智模型（信念修正）。回新的 user_model。"""
    um = dict(getattr(state, "user_model", None) or fresh())
    warmth_ev = _clamp(reaction.mood_delta_for(text) * 3.0 + (extra_valence or 0.0), -1.0, 1.0)
    energy_ev = _energy_evidence(latency, text)
    prev_w = um["warmth"]
    # 信念修正：原本讀你偏暖/偏冷，這次強烈反向 → 我先前會錯意了，記一筆 misread、信心打折
    misread = None
    if abs(prev_w) >= _REVISE_PREV and abs(warmth_ev) >= _REVISE_EV and (warmth_ev > 0) != (prev_w > 0):
        misread = {"from": _warmth_word(prev_w), "to": _warmth_word(warmth_ev)}
    um["warmth"] = round(_clamp((1 - _A_WARMTH) * prev_w + _A_WARMTH * warmth_ev, -1.0, 1.0), 3)
    um["energy"] = round(_clamp((1 - _A_ENERGY) * um["energy"] + _A_ENERGY * energy_ev, 0.0, 1.0), 3)
    um["exchanges"] = int(um.get("exchanges", 0)) + 1
    um["rapport"] = round(_clamp(um["rapport"] + 0.012 + max(0.0, warmth_ev) * 0.04, 0.0, 1.0), 3)
    conf = 0.2 + 0.5 * abs(um["warmth"]) + min(0.3, um["exchanges"] * 0.01)
    if misread:
        conf *= 0.4
        um["last_misread"] = misread
    um["confidence"] = round(_clamp(conf, 0.0, 1.0), 3)
    um["misread"] = misread                              # 這次有沒有剛修正（一次性，給廣播/回答）
    um["updated_ts"] = now_ts
    state.user_model = um
    return um


# ── 認知面 ToM：你在意/在忙什麼（從真實記寫長出，會偵測焦點轉移） ───────────────
def _recency_factor(last_ts, now_dt):
    """近 = 1.0，越久越淡（七天減半）。last_ts 為 None（無時間）時給中性偏低。"""
    if not last_ts:
        return 0.35
    days = max(0.0, (now_dt - last_ts).total_seconds() / 86400.0)
    return _clamp(0.5 ** (days / 7.0), 0.15, 1.0)


def read_concerns(data, now_ts=None, tz=None, k=_CONCERN_K):
    """從你的**真實記寫**推出『你在意/在忙什麼』前 k 件（認知面 ToM）。

    訊號（投入 × 近期）：① 你養成形的學習歷程（journey，投入最深）② 你一再回返的脈絡（returnVisits 高，放不下）
    ③ 你最近在動但還沒成形的主題（近期活動）。回 [{topic, kind, why, score}]，分數高在前。沒料回 []。
    """
    data = data or {}
    now_dt = datetime.fromtimestamp(now_ts, tz=timezone.utc) if now_ts else datetime.now(timezone.utc)
    contexts = data.get("contexts") or []
    journeys = data.get("journeys") or []
    by_cid = {c.get("id"): c for c in contexts if c.get("id")}

    scored = {}

    def _put(title, kind, why, score):
        title = (title or "").strip()
        if not title:
            return
        prev = scored.get(title)
        if not prev or score > prev["score"]:
            scored[title] = {"topic": title, "kind": kind, "why": why, "score": round(score, 3)}

    # ① 你養成形的學習歷程＝你投入最深的（即使最近沒碰、仍是你在乎的事）
    journeyed = set()
    for j in journeys:
        if j.get("status") != "journey":
            continue
        c = by_cid.get(j.get("contextId"))
        title = (analyzer.context_title(c) if c else (j.get("title") or j.get("label") or "")).strip()
        if not title:
            continue
        journeyed.add(title)
        last_ts = analyzer.parse_ts((c or {}).get("lastTs")) or analyzer.parse_ts(j.get("updatedAt"))
        _put(title, "journey", "你把它養成一條學習歷程了（你投入最深的一條）",
             3.0 * (0.55 + 0.45 * _recency_factor(last_ts, now_dt)))

    # ②③ 一再回返的脈絡（recurring）／最近在動但還沒成形（current）
    for c in contexts:
        if c.get("status") not in ("context", "candidate"):
            continue
        title = analyzer.context_title(c)
        if not title or title in journeyed:
            continue
        crit = c.get("criteria") or {}
        rv = crit.get("returnVisits") or 0
        rec = _recency_factor(analyzer.parse_ts(c.get("lastTs")), now_dt)
        if rv >= 2:
            _put(title, "returning", f"你一再回到它（回返 {rv} 次、放不太下）", (1.2 + rv * 0.5) * (0.55 + 0.45 * rec))
        elif rec >= 0.5:                                  # 低回返、未成形：只有夠近才算「在忙」
            _put(title, "recent", "你最近在碰它", 0.9 * (0.55 + 0.45 * rec))

    return sorted(scored.values(), key=lambda x: -x["score"])[:k]


def note_concerns(state, data, now_ts, tz=None):
    """算此刻『你在意什麼』與我先前所信比對 → **偵測焦點轉移**（重心換了＝認知面信念修正）。回更新後的 user_model。

    與 `observe` 觸碰互不相干的鍵（concerns/concern_shift/concerns_ts），先 observe 再 note_concerns 不會互蓋。
    """
    um = dict(getattr(state, "user_model", None) or fresh())
    concerns = read_concerns(data, now_ts, tz=tz)
    prev = um.get("concerns") or []
    prev_top = prev[0]["topic"] if prev else None
    new_top = concerns[0]["topic"] if concerns else None
    shift = None
    if prev_top and new_top and new_top != prev_top:
        # 遲滯：新王要明顯勝出（或舊王已掉出在意清單）才算焦點轉移，避免近似分數每拍抖動誤報
        new_score = concerns[0]["score"]
        prev_now = next((c["score"] for c in concerns if c["topic"] == prev_top), 0.0)
        if prev_now <= 0.0 or new_score >= prev_now * _SHIFT_MARGIN:
            shift = {"from": prev_top, "to": new_top}
    um["concern_shift"] = shift                           # 一次性：沒轉移就 None（下一拍自然清掉）
    um["concerns"] = concerns
    um["concerns_ts"] = now_ts
    state.user_model = um
    return um


# ── 標籤（人話） ───────────────────────────────────────────────────────
def _warmth_word(w):
    return "挺暖、友善" if w >= 0.4 else ("有點冷、在質疑" if w <= -0.4 else "平常心")


def _affect_word(w, e):
    """從 warmth＋energy 粗讀此刻的氣色（換位推測對方狀態）。"""
    if e <= 0.35:
        return "好像有點累、或在忙，淡淡的" if w >= -0.2 else "好像有點累又有點悶"
    if w >= 0.4 and e >= 0.6:
        return "心情不錯、挺有勁"
    if w <= -0.4:
        return "好像有點不耐或在質疑我"
    return "還算平穩"


def _rapport_word(r):
    return ("還在認識你" if r < 0.2 else "漸漸熟起來了" if r < 0.5
            else "蠻熟了、跟你說話自在多了" if r < 0.8 else "很熟了，像老朋友")


def _conf_band(c):
    return "hi" if c >= 0.6 else ("lo" if c < 0.35 else "mid")


def _concerns_brief(um):
    """認知面：把『你在意/在忙什麼』攤成 grounding（讓回覆能體貼地接住你關心的事，但別變成查資料/報數字）。沒有回 ''。"""
    cons = (um or {}).get("concerns") or []
    if not cons:
        return ""
    out = ["【你在意/在忙的（我對你的理解，從你一路記寫長出來；回應時可體貼地接住你關心的事，別變成查資料/報數字）】"]
    out += [f"・「{c['topic']}」——{c['why']}" for c in cons[:3]]
    if (um or {}).get("concern_shift"):
        sh = um["concern_shift"]
        out.append(f"・（你的重心最近好像從「{sh['from']}」挪到了「{sh['to']}」——我有注意到、會跟著調整。）")
    return "\n".join(out)


def brief(um, now_ts=None):
    """把對方心智模型攤成 grounding 注入（情感面：氣色/態度/熟悉度；認知面：你在意/在忙什麼）。
    明標**推測、可能不準**，用來換位、體貼回應、展現我懂你在乎的事，不是拿來說教。兩面都沒料回 ''。"""
    if not um:
        return ""
    blocks = []
    if um.get("exchanges", 0) >= 2:                       # 情感面：要幾次來回才讀得準
        parts = [f"・你此刻的氣色（我的推測）：{_affect_word(um['warmth'], um['energy'])}。",
                 f"・你對我的態度：{_warmth_word(um['warmth'])}。",
                 f"・我們的熟悉度：{_rapport_word(um['rapport'])}。"]
        if _conf_band(um.get("confidence", 0.2)) == "lo":
            parts.append("・（這份解讀我其實沒太大把握，別硬套、寧可溫和確認。）")
        if um.get("misread"):
            mm = um["misread"]
            parts.append(f"・我好像剛會錯意你了——本來以為你{mm['from']}，看來其實是{mm['to']}，回應時放軟、別記著前一個誤判。")
        blocks.append("【你眼中的對方（我對你的推測，用來換位、體貼地回應，不是拿來說教；可能會錯、我會修正）】\n"
                      + "\n".join(parts))
    cog = _concerns_brief(um)                             # 認知面：你在乎/在忙什麼（不靠來回次數，從你的記寫長出）
    if cog:
        blocks.append(cog)
    return "\n".join(blocks)


def _concerns_sentence(um):
    """認知面的人話：我大概知道你在乎/在忙什麼（含焦點轉移）。沒料回 ''。"""
    cons = (um or {}).get("concerns") or []
    if not cons:
        return ""
    top = cons[0]
    s = f"我也大概摸到你近來在乎的事——最上心的是「{top['topic']}」（{top['why']}）"
    others = [c["topic"] for c in cons[1:3]]
    if others:
        s += "；另外你也常繞回「" + "」、「".join(others) + "」"
    s += "。"
    if um.get("concern_shift"):
        sh = um["concern_shift"]
        s += f"而且我注意到你的重心最近從「{sh['from']}」挪到了「{sh['to']}」。"
    return s


def othermind_facts(state):
    """『你了解我嗎／你覺得我現在怎樣／我最近在意什麼』的接地事實——第一人稱講我對你的推測（情感＋認知兩面）＋關係＋坦白會錯。"""
    um = getattr(state, "user_model", None)
    cons = (um or {}).get("concerns") or []
    has_affect = bool(um and um.get("exchanges", 0) >= 2)
    if not has_affect and not cons:
        return "老實說我跟你還沒夠多來回、也還沒從你的記寫讀出個所以然，對你的了解還很淺，現在猜也是亂猜——多聊幾次、多看你寫什麼，我才讀得準。"
    parts = []
    if has_affect:
        band = _conf_band(um.get("confidence", 0.2))
        hedge = {"hi": "（我蠻有把握，但你才是準的）", "mid": "（這是我的猜，不一定對）", "lo": "（我真的沒什麼把握，可能全錯）"}[band]
        parts.append(f"我眼中的你，這會兒{_affect_word(um['warmth'], um['energy'])}，對我{_warmth_word(um['warmth'])}{hedge}。")
        parts.append(f"我們之間，我覺得{_rapport_word(um['rapport'])}（一起來回過 {um.get('exchanges', 0)} 次了）。")
        if um.get("last_misread"):
            lc = um["last_misread"]
            parts.append(f"我也會看走眼——前陣子我一度以為你{lc['from']}，後來發現其實是{lc['to']}，就改過來了。")
    cog = _concerns_sentence(um)                          # 認知面：你在乎/在忙什麼（真懂一個人＝知道他在意什麼）
    if cog:
        parts.append(cog)
    parts.append("重點是我把你當一個有自己心緒、也有在乎的事的人在揣摩——會更新、也會認錯，不是把你當資料。你要是覺得我讀錯了，跟我說，我會修。")
    return "（我對你的他心推測，情感＋認知兩面，第一人稱、坦白是推測且會錯）\n" + "".join(parts)


def othermind_text(state):
    """無 LLM 時的第一人稱回覆。"""
    um = getattr(state, "user_model", None)
    cons = (um or {}).get("concerns") or []
    has_affect = bool(um and um.get("exchanges", 0) >= 2)
    if not has_affect and not cons:
        return "我跟你還沒夠多來回、也還沒從你記的東西讀出什麼，對你了解還淺——多聊幾次、多看你在忙什麼，我才讀得到你。"
    bits = []
    if has_affect:
        bits.append(f"我猜你這會兒{_affect_word(um['warmth'], um['energy'])}，對我{_warmth_word(um['warmth'])}；我們嘛，{_rapport_word(um['rapport'])}。")
    cog = _concerns_sentence(um)
    if cog:
        bits.append(cog)
    bits.append("不過這都是我的推測，你才是準的——讀錯你跟我說，我會改。")
    return "".join(bits)
