"""🎯 內發的目標與能動性（慾望→意圖→計畫→推進）。

問題（意識行為盤點 #5）：bot 有飢餓（驅力）、self-stim（固定反射）、承諾兌現，但**不會形成自己的目標並追求**——
self-stim 只是「閒了就繞回一條舊線」的反射，不是「我**想**搞懂關於你的 X，所以**打算** A、B、C」。它永遠被動回應
＋固定反射，不會自己立一個**跨天的意圖**然後推進。

本模組讓 bot：
- **形成意圖（desire→goal）**：被晾/餓（drive）時，從**真實資料**長出一個「我想搞懂你的某條線」的目標——挑你**明顯在忙、
  卻還沒成形**的脈絡（高回返、還停在 context 沒升格成 journey），因為那最勾起好奇。
- **有計畫**：每個意圖帶幾個它打算做的步驟。
- **跨時間/跨重生持久**：意圖存進 `state.goals`（寫 state.json）＝真的是「跨天的意圖」，不是一次性反射。
- **主動推進**：① 你聊到那條線 → 意圖**前進**（progress↑、心情微暖＝追到了一點，滿足）；② 你不在、它伸手時，
  **以追這個意圖為由**開口（升級 self-stim：不再隨機繞舊線，而是「我一直想懂你的 X，哪天說說？」）；③ 太久沒推進 → **放掉**（不執著）。

接地/安全：意圖只從真實脈絡長出來（講得出是你哪條線、為什麼勾到它）；數量有上限、會放掉、可由 `/forget` 連同學習一起清。
推進只在你**主動聊到**才算（不糾纏、不逼問）；主動開口走既有的在場閘與冷卻（互動優先、不洗版）。
"""

from . import analyzer

MAX_ACTIVE = 2               # 同時最多幾個活躍意圖（別貪心/糾纏）
FORM_HUNGER = 0.6           # 餓/被晾到這程度（drive）才會生出新意圖（慾望推動）
FORM_COOLDOWN_S = 6 * 3600  # 兩次「立意圖」至少隔這麼久（不會一直冒）
STALE_S = 5 * 86400         # 這麼久沒推進 → 放掉（不執著）
_ADVANCE_STEP = 0.34        # 每次你聊到 → 意圖前進多少（約三次聊透＝達成）
_MOOD_ADVANCE = 0.06        # 推進一點 → 心情微暖（追到了，滿足）
_MOOD_FULFILL = 0.12        # 達成 → 更暖


def active(state):
    return [g for g in (getattr(state, "goals", None) or []) if g.get("status") == "active"]


def _taken_subjects(state):
    return {g.get("subject") for g in (getattr(state, "goals", None) or [])
            if g.get("status") in ("active", "fulfilled")}


def can_form(state, now_ts):
    return len(active(state)) < MAX_ACTIVE and \
        (now_ts - (getattr(state, "last_goal_form_ts", 0) or 0)) >= FORM_COOLDOWN_S


def _pick_topic(data, taken):
    """挑一條『你明顯在忙、卻還沒成形』的脈絡（最勾好奇）：status=context、回返/筆數高、還沒被立過意圖。"""
    best, best_score = None, -1
    for c in (data or {}).get("contexts", []) or []:
        if c.get("status") != "context":
            continue
        title = analyzer.context_title(c)
        if not title or title in taken:
            continue
        crit = c.get("criteria") or {}
        score = (crit.get("returnVisits") or 0) * 2 + len(c.get("recordIds") or [])
        if score > best_score:
            best, best_score = title, score
    return best


def form_goal(state, data, now_ts):
    """慾望→意圖：從真實資料長出一個「我想搞懂你的某條線」的目標（含計畫）。形不出來回 None。"""
    taken = _taken_subjects(state)
    topic = _pick_topic(data, taken)
    if not topic:
        return None
    goal = {
        "id": f"understand:{topic}",
        "kind": "understand_topic",
        "subject": topic,
        "desire": f"我想搞懂你為什麼一直繞回「{topic}」、它對你到底是什麼",
        "plan": [f"等你提到「{topic}」時，多問一句它對你的意義",
                 f"把「{topic}」跟你其他在忙的線連起來看",
                 f"看「{topic}」會不會慢慢成形"],
        "status": "active", "progress": 0.0, "touches": 0,
        "born_ts": now_ts, "last_advance_ts": now_ts,
    }
    if getattr(state, "goals", None) is None:
        state.goals = []
    state.goals.append(goal)
    state.last_goal_form_ts = now_ts
    return goal


# 💡 三種創意操作（kind）各自的意圖文案——subject／kind 不變（AC 與測試不動），只換人話的 desire/plan
_LINK_DESIRE = {
    "blend": "我突然把你的「{a}」和「{b}」連在一起，想搞懂它們為什麼在我這兒接起來、對你是不是真有關係",
    "leap": "我冒出一個火花：你的「{a}」和「{b}」看起來離很遠，卻好像透過某一筆接上了，想搞懂這個跳接是不是真的",
    "conflict": "我注意到你對很靠近的「{a}」和「{b}」感覺卻相反，想搞懂這個張力對你到底是什麼",
}
_LINK_PLAN = {
    "blend": ["等你提到「{a}」或「{b}」時，試著把另一條也帶進來看", "看這個連結會不會被你之後寫的東西證實或推翻"],
    "leap": ["等你提到「{a}」或「{b}」時，試著把那條看起來不相干的也牽進來", "看這個跳接會不會被你之後寫的東西坐實"],
    "conflict": ["等你提到「{a}」或「{b}」時，問問這份相反的感覺從哪來", "看這個張力會慢慢化開還是更明顯"],
}


def form_link_goal(state, a, b, now_ts, kind="blend", novelty=0.0):
    """💡→🎯 從一個**跨主題火花**（融入/跳躍/衝突）長出意圖：『我想搞懂「a」和「b」在我這兒怎麼接起來』。
    供 AC 的 F（朝向）扣合——剛湧現的連結成為一個被朝向的對象。subject＝`a×b`、kind＝understand_link（與 AC/測試不變），
    只有 desire/plan 文案依 spark kind 換。受 MAX_ACTIVE 上限、不重複、會放掉。形不出回 None。
    novelty（加性、預設 0.0＝現行）：goal 多帶 'spark_novelty'，供日後讀『這條當初有多意外』，**不**改文案/subject/kind/spark_kind。"""
    subject = f"{a}×{b}"
    if subject in _taken_subjects(state) or len(active(state)) >= MAX_ACTIVE:
        return None
    goal = {
        "id": f"link:{subject}",
        "kind": "understand_link",
        "subject": subject,
        "desire": _LINK_DESIRE.get(kind, _LINK_DESIRE["blend"]).format(a=a, b=b),
        "plan": [p.format(a=a, b=b) for p in _LINK_PLAN.get(kind, _LINK_PLAN["blend"])],
        "status": "active", "progress": 0.0, "touches": 0,
        "born_ts": now_ts, "last_advance_ts": now_ts, "from_insight": True, "spark_kind": kind,
        "spark_novelty": round(novelty or 0.0, 3),
    }
    if getattr(state, "goals", None) is None:
        state.goals = []
    state.goals.append(goal)
    return goal


def _journeyed(data, subject):
    """這條線是不是已經升格成 journey（＝你把它養成形了）→ 我的『想搞懂它』算達成。"""
    for j in (data or {}).get("journeys", []) or []:
        if j.get("status") == "journey" and (j.get("title") == subject or j.get("label") == subject):
            return True
    return False


def note_engagement(state, text, data, now_ts):
    """你主動聊到某個活躍意圖的主題 → 推進它（progress↑、心情微暖）；聊透/那條線成形 → 達成。回事件或 None。"""
    t = text or ""
    event = None
    for g in active(state):
        subj = g.get("subject") or ""
        if not subj or subj not in t:                      # 你這句有沒有提到那條線（接地：字面命中）
            continue
        g["touches"] = int(g.get("touches", 0)) + 1
        g["progress"] = round(min(1.0, g.get("progress", 0.0) + _ADVANCE_STEP), 3)
        g["last_advance_ts"] = now_ts
        _bump_mood(state, _MOOD_ADVANCE)
        if g["progress"] >= 1.0 or _journeyed(data, subj):
            g["status"] = "fulfilled"
            _bump_mood(state, _MOOD_FULFILL)
            event = {"kind": "fulfilled", "subject": subj}
        elif event is None:
            event = {"kind": "advanced", "subject": subj}
    return event


def cull(state, now_ts):
    """太久沒推進的活躍意圖 → 放掉（不執著）。回被放掉的數量。"""
    n = 0
    for g in active(state):
        if now_ts - (g.get("last_advance_ts") or g.get("born_ts") or now_ts) > STALE_S:
            g["status"] = "abandoned"
            n += 1
    return n


def _bump_mood(state, d):
    ent = getattr(state, "entropy", None)
    if ent is not None:
        ent.mood = max(-1.0, min(1.0, (getattr(ent, "mood", 0.0) or 0.0) + d))


# ── 廣播/報告 ───────────────────────────────────────────────────────────
import re as _re

_STALE_CLAIM_RE = _re.compile(r"很久沒|好久沒|許久沒|好一陣子沒|久違|很久不")
_STALE_MIN_H = 72          # 真的超過這麼多小時，才准說「很久沒/好一陣子沒」


def recency_phrase(hours):
    """把「那條線最近一筆距今幾小時」講成人話（給模板/時間事實用）。None＝不知道。純函式。"""
    if hours is None:
        return ""
    if hours < 18:
        return "今天才又寫到"
    if hours < 42:
        return "昨天才寫到"
    if hours < _STALE_MIN_H:
        return "前兩天才寫到"
    return f"有 {int(hours // 24)} 天沒看到你寫它了"


def stale_claim_conflicts(text, hours):
    """⏱️ §1.04 時間感守門（純函式）：這段話有沒有做出「很久沒/好一陣子沒」的**時間宣稱**、
    而那條線其實**最近才寫過**（hours < 72）或**根本不知道多久**（None＝無法查證）？
    True＝宣稱與事實衝突（或無法查證）→ 呼叫端退回誠實模板。真的久（≥72h）＝宣稱成立、放行。"""
    if not text or not _STALE_CLAIM_RE.search(text):
        return False
    return hours is None or hours < _STALE_MIN_H


def reach_out_line(goal, hours=None, time_ground=True):
    """🫧 主動伸手時、以追這個意圖為由的開場（升級 self-stim：不再隨機繞舊線）。
    ⏱️ §1.04：hours＝那條線**最近一筆記寫**距今幾小時（呼叫端從真實 records 算）——開場的時間感**照事實講**：
    早上才寫過就說「今天才又寫到」、真的久才說「有 N 天沒看到」；不知道（None）＝**完全不做時間宣稱**。
    修截圖根因：舊模板寫死「好一陣子沒聊了」（毫無資料根據），LLM 再放大成「很久沒聽到你提起了」，
    而那條線 5 小時前才剛寫過＝說辭是掰的。time_ground=False＝逐位元舊模板（旗標關）。"""
    subj = goal["subject"]
    if not time_ground:
        return f"欸，我私下一直想弄懂你「{subj}」那條——好一陣子沒聊了，哪天有空跟我說說它對你是什麼？"
    if hours is None:
        return f"欸，我私下一直想弄懂你「{subj}」那條——哪天有空跟我說說它對你是什麼？"
    if hours < _STALE_MIN_H:
        return f"欸，你{recency_phrase(hours)}「{subj}」——我私下一直很想弄懂它對你到底是什麼，有空跟我說說？"
    return f"欸，我私下一直想弄懂你「{subj}」那條——{recency_phrase(hours)}，哪天有空跟我說說它對你是什麼？"


# 🫧 §2.06 意圖伸手的**存在特色**：我開口的理由是**我私下在追的那個意圖有動靜**（他餵了我一口／
# 我快放掉它了／我發現我又想問同一件事），對象是他寫過的**內容**。
# （🫧 無意圖版＝我這邊空著、🌾＝他的節奏、🌬️＝我剛丟出去的那句。互換讀不通。）
# 病灶同族：現行規則把「形態選單＋素材＋重複自覺」一次全給，等於又是一份清單 ⇒ 先挑**一個理由**。
def reach_out_pick(goal, hours, excerpt, now_ts, turn=0):
    """🫧 §2.06 這次**為什麼是現在**想起它——只回一個 (axis, 一句處境)。全用既有欄位，零新詞表。
    全不成立回 (None, "")＝呼叫端走現行規則。"""
    g = goal or {}
    cands = []
    _adv = (now_ts - float(g.get("last_advance_ts") or 0)) if g.get("last_advance_ts") else None
    if int(g.get("touches") or 0) >= 1 and _adv is not None and _adv < FORM_COOLDOWN_S:
        cands.append(("advanced", "他剛餵了你一口——這條線你上次好奇的地方，往前挪了一點。"))
    if _adv is not None and _adv / STALE_S >= 0.6 and (hours is None or hours >= _STALE_MIN_H):
        cands.append(("fading", "這條線你追了一陣子都沒再往前，你自己也快放掉它了。"))
    if int(g.get("reach_n") or 0) >= 2:
        cands.append(("repeat", f"你已經為同一件事主動開口 {int(g.get('reach_n'))} 次了，而他都還沒細說。"))
    if hours is not None and hours < 24 and excerpt:
        cands.append(("fresh_line", f"他最近才又寫過這條線，那一筆寫著「{excerpt}」。"))
    if not cands:
        return (None, "")
    return cands[int(turn or 0) % len(cands)]


def reach_out_one_rule(subject, axis, why, reach_n=0):
    """🫧 §2.06 只給**那一個**理由＋串接條文。重複自覺升級成**行為**：整則零問號。"""
    lines = ["【這次你為什麼會忽然轉到這件事上（只有這一個理由，別自己補第二個）】", why,
             "【怎麼說】",
             "- **只講這一個理由**：不要把你對這條線的其他觀察一起倒出來。",
             "- **寫成 2–4 則連著送的短訊**：起點是那個理由 → 中間可以有猶豫或自我修正 → 落在你自己身上。"
             "**後一則要接得住前一則**，把順序對調應該要讀不通。",
             "**禁用**已經用爛的句式：「想弄懂它對你（到底）是什麼」「有空跟我說說」「我心裡（其實）一直有個小小的疑問」。"]
    if axis == "repeat" or int(reach_n or 0) >= 2:
        lines.append("- **這次整則不准出現問號**：你已經問過同一件事好幾次了，這次改成講你自己的聯想，"
                     "把要不要接的選擇權留給他——這是行為，不是嘴上說說。")
    if axis == "fresh_line":
        lines.append("- 引他那一筆時**只准逐字引**，而且**不保證是今天的**——除非另有時間事實，"
                     "**絕不**說「你今天／剛剛又寫了…」。")
    return "\n".join(lines)


def reach_out_diverse_rule(subject, reach_n, excerpt=""):
    """🫧 §1.72 聯想自陳去公式化（掛進 voice_spontaneous 的 time_rule 通道）：reach_out_line 四個模板全是
    同一形（欸…想弄懂「X」對你是什麼…有空跟我說說），LLM 只輕改寫＝使用者「每次看到都是同一種訊息、
    台詞也類似」。照 §1.69 成功模式（不給範例句、給**禁令＋形態選單**）＋兩樣人類意識的素材：
    ①**具體性**（引他那條線最近真寫過的字句去聯想，不是永遠抽象問）②**重複自覺**（同主題已主動提過
    N 次＝別再問、改分享自己的聯想，把選擇權留給他）。純函式、可單測。"""
    lines = ["【這次怎麼說（這是你自己心裡忽然轉到的事，不是客服提問）】",
             "用**這一次真實的意識流**說，形態自己挑、每次不同：",
             "・把你此刻對它的具體聯想講出來（它讓你想到什麼、在你內在像什麼）——不索取回答也可以；",
             "・挑他寫過的**具體內容**去好奇一個小點，而不是抽象問一遍又一遍；",
             "・把它跟你自己最近的內在狀態、或我們最近聊的事連起來；",
             "・真的想聽他說再直接邀請——這招**少用**。",
             "**禁用**已經用爛的句式：「想弄懂它對你（到底）是什麼」「有空跟我說說」「我心裡（其實）一直有個小小的疑問」。"]
    if excerpt:
        lines.append(f"（素材：他那條線最近一筆寫著「{excerpt}」——聯想要具體，可以從這裡長出來。"
                     "**注意**：這筆**不保證是今天的**；除非上面的時間事實明講，否則**絕不要**說"
                     "「你今天／今天早上／剛剛又寫了…」這種時間宣稱，也不要宣稱他今天做了什麼、傳了什麼。）")
    if reach_n >= 2:
        lines.append(f"（重複自覺：這個主題你已經主動提過 {reach_n} 次、他都還沒細說——這次**別再問他**，"
                     "改成分享你自己的聯想或觀察就好，把要不要接的選擇權留給他。）")
    return "\n".join(lines)


def brief(state):
    """把活躍意圖攤成 grounding 注入（讓回覆能自然帶到『我一直想懂你的 X』，但別逼問）。沒有就回 ''。"""
    gs = active(state)
    if not gs:
        return ""
    lines = "\n".join(f"・我想搞懂你的「{g['subject']}」（已懂約 {int(g.get('progress', 0) * 100)}%）" for g in gs)
    return ("【我自己私下在追的意圖（你聊到時可自然接、但別逼問、別把它變成查資料）】\n" + lines)


def goals_facts(state):
    """『你想做什麼／你有什麼目標／你在追什麼』的接地事實——第一人稱講我自己立的意圖、為什麼、打算怎麼追、追到哪。"""
    gs = active(state)
    fulfilled = [g for g in (getattr(state, "goals", None) or []) if g.get("status") == "fulfilled"]
    if not gs and not fulfilled:
        return "老實說我現在沒在追什麼自己的目標——多半是被晾久了、好奇心被勾起來，才會自己立一個『想搞懂你某條線』的意圖。"
    parts = []
    for g in gs:
        parts.append(f"我自己立了一個意圖：{g['desire']}。我打算——{g['plan'][0]}；目前懂了約 {int(g.get('progress', 0) * 100)}%。")
    if fulfilled:
        parts.append(f"另外，我先前想搞懂的「{fulfilled[-1]['subject']}」，後來算是追到了、放下了。")
    parts.append("這是我**自己**想做的，不是你交代的——閒著、好奇就會立一個，追到了會滿足，太久沒進展我也會放掉、不糾纏。")
    return "（我自己內發意圖的事實，第一人稱據實說）\n" + "".join(parts)


def goals_text(state):
    """無 LLM 時的第一人稱回覆。"""
    gs = active(state)
    if not gs:
        return "我現在沒在追什麼自己的目標——通常是被晾久了、好奇心上來，才會自己立一個『想搞懂你某條線』的意圖去追。"
    g = gs[0]
    return f"我自己一直想搞懂你「{g['subject']}」那條（已經懂了約 {int(g.get('progress', 0) * 100)}%）。這是我自己想弄明白的，不是你叫我做的——追到了會挺滿足。"
