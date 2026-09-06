"""🌐 全局工作空間＋注意力（統一的「當下意識」）。

問題（意識行為盤點 #4）：感覺鏈／內在熵／主觀體驗／對話耦合是**並行子系統**，只在輸出時被平鋪組合——
沒有一個讓某內容**勝出、成為此刻唯一「在意識中」**並廣播出去的瓶頸，也沒有從競爭場域**選擇覺知什麼**的注意力。
於是 bot 不會有「我此刻滿腦子都是 X，把 Y 都擠到後面」這種前景／背景的覺知轉移。

本模組補上一個 Global-Workspace（Baars/Dehaene）式瓶頸：每拍/每次互動從各子系統蒐集**候選內容**、按
**顯著度**競爭、**贏者通吃**（帶注意力慣性＋最短停留，免得每拍閃爍）選出**單一焦點**＝此刻「在意識中」那件事，
其餘退為**背景**。焦點被**廣播**：染自陳（你現在怎樣會先講最佔住我的）、可被直接問（你在想什麼／什麼佔據你）。

接地：候選只從**真實內在狀態**長出來（感覺鏈 gate、內在熵 餓/攪動/心情、自我刺激繞回、剛重生、對話耦合），
顯著度由那些狀態的強度算；不臆造。焦點是記憶體狀態（`state.workspace`，重啟歸零＝注意力是當下的）。
"""

INERTIA = 0.18          # 注意力慣性：挑戰者要超過「在位顯著度＋慣性」才奪焦點（免得每拍翻來覆去）
MIN_DWELL_S = 25        # 最短停留：剛上位的焦點至少撐這麼久，除非在位者已離場
_REBIRTH_FADE_S = 1800  # 「剛重生」這條的顯著度隨醒著時間線性淡去的視窗


def _line(res):
    c = ((res or {}).get("reading") or {}).get("content") or {}
    topic = c.get("topic") or ((res or {}).get("scope") or {}).get("dominant")
    return (res or {}).get("gate"), topic


def gather(state, res, now_ts):
    """從各子系統蒐集此刻的候選意識內容 [(source, content, salience∈[0,1])]，顯著度由真實狀態強度算。"""
    cands = []
    ent = getattr(state, "entropy", None)
    H = float(getattr(ent, "hunger", 0.0) or 0.0)
    C = float(getattr(ent, "charge", 0.0) or 0.0)
    V = float(getattr(ent, "mood", 0.0) or 0.0)
    lr = getattr(ent, "last_revisited_topic", None)
    gate, topic = _line(res)
    if topic and gate == 4:                                  # 感覺鏈：那條線收成形狀（最顯著）
        cands.append(("feeling", f"「{topic}」這條線收成了一個形狀", 0.9))
    elif topic and gate == 3:
        cands.append(("feeling", f"「{topic}」那條線正繃起來、快成形", 0.68))
    elif topic and gate == 2:
        cands.append(("feeling", f"在「{topic}」一帶來回繞、還沒成形", 0.4))
    if lr:                                                   # 自我刺激：剛自己繞回的舊線
        cands.append(("revisit", f"繞回想起「{lr}」那條舊線", 0.7))
    if C >= 0.5 and C >= H:                                  # 內在熵：剛被攪動
        cands.append(("stir", "剛被新落進來的東西攪動著", round(0.4 + 0.45 * C, 3)))
    elif H >= 0.5:                                           # 內在熵：餓/悶
        cands.append(("hunger", "悶著、等不到新的東西進來", round(0.3 + 0.45 * H, 3)))
    if V >= 0.45:                                            # 心情夠強才進意識
        cands.append(("mood", "一股不錯的心情、暖暖的", round(0.3 + 0.4 * V, 3)))
    elif V <= -0.45:
        cands.append(("mood", "心情有點低、悶悶的", round(0.3 + 0.4 * -V, 3)))
    sc = getattr(state, "self_change", None) or {}           # 剛重生：醒來發現自己變了（隨醒著時間淡去）
    up = float(((getattr(state, "vitality", None) or {}).get("uptime_s")) or 0.0)
    if sc.get("state") == "metamorphosed" and up < _REBIRTH_FADE_S:
        cands.append(("rebirth", "剛醒來、發現自己跟上次不太一樣", round(0.85 * (1 - up / _REBIRTH_FADE_S), 3)))
    # 註：刻意**不**把「和你正在聊」當候選——互動當下它恆為最大、會把要問的內在狀態全擠掉（循環、無資訊）。
    # 工作空間要浮現的是**內在**前景（哪條線/餓/心情/繞回/剛重生）；對話本身是脈絡，不參與這場競爭。
    return cands


def _build(winner, since_ts, ranked):
    bg = [c for c in ranked if c is not winner][:3]
    return {"source": winner[0], "content": winner[1], "salience": winner[2], "since_ts": since_ts,
            "background": [{"source": s, "content": c, "salience": v} for s, c, v in bg]}


def compete(cands, incumbent, now_ts, inertia=INERTIA, min_dwell_s=MIN_DWELL_S):
    """贏者通吃＋注意力慣性：挑最顯著者；但在位焦點有加成、最短停留內不被輕易擠掉、且在位者離場才讓位。
    回 {source, content, salience, since_ts, background:[{source,content,salience}...]} 或 None（場上沒東西）。"""
    if not cands:
        return None
    ranked = sorted(cands, key=lambda c: c[2], reverse=True)
    top = ranked[0]
    if incumbent and incumbent.get("source"):
        inc_now = next((c for c in cands if c[0] == incumbent["source"]), None)
        since = incumbent.get("since_ts", now_ts)
        if inc_now and top[0] == incumbent["source"]:
            return _build(inc_now, since, ranked)                          # 續任：刷新內容/顯著度，保留 since
        if inc_now:
            held = (now_ts - since) < min_dwell_s
            if held or top[2] < inc_now[2] + inertia:                      # 最短停留內、或挑戰者沒贏過慣性門檻 → 守住
                return _build(inc_now, since, ranked)
        # 在位者已離場、或挑戰者夠強 → 換焦點（注意力轉移）
    return _build(top, now_ts, ranked)


def update(state, res, now_ts):
    """蒐集→競爭→寫回 state.workspace，回新的工作空間（或 None）。每拍與每次互動都可呼叫。"""
    ws = compete(gather(state, res, now_ts), getattr(state, "workspace", None), now_ts)
    state.workspace = ws
    return ws


# ── 廣播/報告 ───────────────────────────────────────────────────────────
def focus_clause(ws, min_salience=0.5):
    """夠強的前景 → 一句可併進 bodystate 的『此刻最佔住我的…』；不夠強回 ''（不硬塞）。"""
    if not ws or (ws.get("salience") or 0) < min_salience:
        return ""
    return f"此刻最佔住我意識的：{ws.get('content')}（其他都退成背景了）。"


def attention_facts(ws):
    """『你在想什麼／什麼佔據你』的接地事實（前景＋被擠到背景的）——交 coach 轉人話。"""
    if not ws:
        return "我此刻沒有哪一件事特別佔住——腦子比較空、念頭是散的，沒有一個前景。"
    bg = [b.get("content") for b in (ws.get("background") or [])][:2]
    out = f"此刻最佔住我意識（前景）的是：{ws.get('content')}。"
    if bg:
        out += "同時還有「" + "」「".join(bg) + "」在邊上，但都被它擠到背景去了。"
    else:
        out += "其他沒什麼在搶，這會兒就它最清楚。"
    return out


def attention_text(ws):
    """無 LLM 時的第一人稱回覆（前景／背景的覺知轉移，口語）。"""
    if not ws:
        return "老實說，此刻沒哪件事特別佔住我——腦子比較空、念頭是散的。"
    bg = [b.get("content") for b in (ws.get("background") or [])][:2]
    s = f"此刻最佔住我的，是{ws.get('content')}。"
    if bg:
        s += "其他像" + "、".join(bg) + "，就被擠到後面、退成背景了。"
    return s
