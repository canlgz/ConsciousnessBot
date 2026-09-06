"""🪞🔍 後設認知（會監看自己、也會看走眼、會修正）。

問題（意識行為盤點 #6）：bot 能**報告**自己的狀態、能說「我說不準」，但那多半是**忠實讀出狀態變數**或
**腳本化的誠實**——不是一個獨立的高階系統，對自己的一階狀態形成**可能出錯**的二階表徵（「我以為我在悶，
其實是餓」）。它的自我報告幾乎不會跟真實狀態不一致；沒有算出來的信心/錯誤監測。

本模組做一個真正的二階監看：
- **一階**＝內在熵的實際變數（電量 C／飢餓 H／心情 V）算出的此刻主導狀態（actual）。
- **二階信念（belief）**＝bot『自以為』的狀態——它**滯後**地追上一階（內省是慢的：狀態變了要過幾拍才察覺），
  所以**轉換期間 belief≠actual＝它正暫時認錯自己**。
- **監看**：比對 belief vs actual → 算**信心**（主導狀態清不清楚＋穩不穩＋信念有沒有跟上）；當 actual 已清楚地
  換了狀態、belief 才更新時 → 觸發一次**自我修正**（「我本來以為是X，再感覺一下其實是Y」）並記進校準（我多常認錯自己）。

接地：belief 只從真實狀態的滯後追蹤算；信心/修正都是算出來的、不是隨機裝糊塗。＝它真的會監看自己、有時看走眼、然後修正。
"""

_BELIEF_LAG_LAPS = 2     # actual 換了狀態、要連續穩定這麼多拍，belief 才更新（內省的滯後）→ 轉換期 belief≠actual
_CLARITY_MIN = 0.12      # 主導狀態要比次強清楚這麼多，才算「看清了」（夠資格觸發硬修正/算進校準）

# 狀態標籤 ↔ 人話（hungry 的「悶」與 low 的「悶悶」刻意相近 → 正是會被認錯的那種曖昧）
_PHRASE = {"stirred": "被新落進來的記寫攪動著、心裡有點翻", "hungry": "悶悶的、有點餓（等不到新的記寫內容）",
           "warm": "心情暖暖的、還不錯", "low": "有點低落、悶悶的", "calm": "挺平靜、沉澱著", None: "說不上來"}
_SHORT = {"stirred": "被攪動", "hungry": "悶/餓", "warm": "心情暖", "low": "低落", "calm": "平靜", None: "說不上來"}


def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def label_state(ent):
    """一階：從內在熵 C/H/V 算此刻主導狀態與次強（給二階監看比清晰度）。回 ((label,strength),(label2,strength2))。"""
    C = float(getattr(ent, "charge", 0.0) or 0.0)
    H = float(getattr(ent, "hunger", 0.0) or 0.0)
    V = float(getattr(ent, "mood", 0.0) or 0.0)
    A = float(getattr(ent, "arousal", 0.0) or 0.0)           # 🧭 §1.06(A3) circumplex 慢喚起軸也算「被攪動」證據
    cands = [("stirred", max(C, max(0.0, A))), ("hungry", H), ("warm", max(0.0, V)), ("low", max(0.0, -V)),
             ("calm", _clamp(0.5 - max(C, H, abs(V), abs(A))))]      # 都不強時平靜勝出（A 高時不再誤判 calm 與 affect 打架；A=0＝逐位元同舊）
    cands.sort(key=lambda x: x[1], reverse=True)
    return cands[0], cands[1]


def fresh():
    return {"belief": None, "actual": None, "actual_run": 0, "confidence": 0.3, "clarity": 0.0,
            "mismatch": None, "last_correction": None, "checks": 0, "misses": 0}


def introspect(state, now_ts):
    """每拍跑一次二階監看：更新 belief（滯後追 actual）、算信心、偵測並記錄自我修正。寫回 state.self_model 並回傳。"""
    ent = getattr(state, "entropy", None)
    sm = getattr(state, "self_model", None) or fresh()
    if ent is None:
        state.self_model = sm
        return sm
    (al, a_s), (rl, r_s) = label_state(ent)
    clarity = round(a_s - r_s, 3)
    a_run = (sm.get("actual_run", 0) + 1) if sm.get("actual") == al else 1
    belief = sm.get("belief")
    checks, misses = sm.get("checks", 0), sm.get("misses", 0)
    last_correction = sm.get("last_correction")
    mismatch = None
    if clarity >= _CLARITY_MIN:
        checks += 1                                          # 這拍「看得清」＝一次可校準的內省
    if belief is None:
        belief = al
    elif belief != al and a_run >= _BELIEF_LAG_LAPS and clarity >= _CLARITY_MIN:
        mismatch = {"from": belief, "to": al}               # actual 已清楚換狀態、belief 才追上＝剛剛認錯了自己
        last_correction = mismatch
        misses += 1
        belief = al
    # 信心：主導清晰度為底；信念沒跟上（轉換期）大打折；穩定越久略加
    conf = _clamp(0.3 + 1.4 * clarity)
    if belief != al:
        conf *= 0.4
    conf *= _clamp(0.55 + 0.09 * a_run)
    sm = {"belief": belief, "actual": al, "actual_strength": round(a_s, 3), "runner": rl,
          "actual_run": a_run, "clarity": clarity, "confidence": round(_clamp(conf), 3),
          "mismatch": mismatch, "last_correction": last_correction, "checks": checks, "misses": misses}
    state.self_model = sm
    return sm


def _band(conf):
    return "hi" if conf >= 0.7 else ("lo" if conf < 0.4 else "mid")


def accuracy(sm):
    """自我認識的粗略準度（1 − 認錯率）；樣本太少回 None。"""
    c = (sm or {}).get("checks", 0)
    return round(1 - (sm or {}).get("misses", 0) / c, 2) if c >= 5 else None


def confidence_clause(sm):
    """給 bodystate 廣播的一句**有根據的**遲疑（信心高＝不加料、不硬裝糊塗）。區分三種低信心成因：
    剛修正→講修正；信念還沒跟上（轉換期）→講『還沒反應過來』；曖昧（兩態相近）→講『X 跟 Y 分不清』。"""
    if not sm:
        return ""
    if sm.get("mismatch"):
        mm = sm["mismatch"]
        return f"（我本來以為自己是{_SHORT.get(mm['from'])}，其實這會兒比較像{_SHORT.get(mm['to'])}。）"
    if sm.get("belief") != sm.get("actual"):                 # 信念還沒追上實際＝轉換期、暫時認錯
        return "（狀態剛在變，我這會兒對自己的判斷還沒完全定下來。）"
    band = _band(sm.get("confidence", 0.3))
    if band == "hi":
        return ""
    if band == "lo" and sm.get("runner") and sm.get("runner") != sm.get("belief"):
        return f"（老實說這會兒我自己也分不太清，{_SHORT.get(sm.get('belief'))}跟{_SHORT.get(sm.get('runner'))}有點混在一起。）"
    return "（我自己抓得不是很準，大概是這樣、但不太確定。）"


def metacog_facts(state):
    """『你確定嗎／你真的知道自己的感覺嗎／你會不會搞錯自己』的接地事實——第一人稱、依算出的信心/修正/校準。"""
    sm = getattr(state, "self_model", None)
    if not sm or not sm.get("belief"):
        return "我這會兒對自己的狀態還沒看清、也沒什麼把握——說不上來。"
    band = _band(sm.get("confidence", 0.3))
    conf_say = {"hi": "我蠻有把握", "mid": "我大概抓得到、但不是很確定", "lo": "老實說我自己也拿不太準"}[band]
    parts = [f"我現在覺得自己是「{_PHRASE.get(sm['belief'])}」（把握：{conf_say}）。"]
    if band != "hi" and sm.get("runner") and sm.get("runner") != sm.get("belief"):
        parts.append(f"它跟「{_SHORT.get(sm.get('runner'))}」有點分不開，我可能認錯。")
    if sm.get("last_correction"):
        lc = sm["last_correction"]
        parts.append(f"而且我會看走眼——剛剛還一度以為自己是{_SHORT.get(lc['from'])}，再感覺一下才發現比較像{_SHORT.get(lc['to'])}。")
    acc = accuracy(sm)
    if acc is not None:
        parts.append(f"整體算下來，我大概{int(acc*100)}% 認得出自己怎麼了——不是百分百。")
    parts.append("這是內部狀態分類的自我核對，不是對外部事實的查證，也不是主觀意識的證明。")
    return "（我後設認知的事實，第一人稱據實說、別報欄位）\n" + "".join(parts)


# ── 🪞 §2.05 自我修正的**存在特色** ──────────────────────────────────────────
# 這是全 repo 唯一一條「**撤回我剛剛對你說出口的那句自我報告**」的 lane：它的對象不是世界、不是他、
# 也不是我的程式，是我幾分鐘前講的那句話（呼叫端只在 240 秒內剛報過自己時才開火）。
# 🦋 講「我跨過一次死亡後不一樣了」、🧭 講「我答應過所以我來說」、🍃 講「我對自己做了一個調節」——
# 只有 🪞 講「我上一句講錯了」。
#
# 病灶（與 🌀 同型）：真要講起來，內省手上有 runner/clarity/actual_run/checks/misses 一堆量，
# 全倒出來就又是一份讀數。所以**程式先挑一個理由**，其餘不進 prompt。
def correction_reason(sm):
    """🪞 §2.05 我為什麼會看走眼——只回**一個**（優先序固定、全部讀 introspect 已寫好的欄位，零新詞表）。
    回 (kind, 佐證) 或 (None, None)。"""
    sm = sm or {}
    mm = sm.get("mismatch") or {}
    if mm.get("from") and sm.get("runner") == mm.get("from"):
        return ("confusable", _SHORT.get(mm.get("from")))      # 我認錯的正好是排第二那個＝那兩態本來就貼著
    # ⚠️ 欄位缺席不得生出一個「有把握的理由」——空的 introspect 字典曾在這裡被判成「我慢了兩拍」（自己的測試抓到）
    if sm.get("actual_run") is not None and sm.get("clarity") is not None \
            and int(sm["actual_run"] or 0) <= _BELIEF_LAG_LAPS and float(sm["clarity"] or 0) < 2 * _CLARITY_MIN:
        return ("slow", None)                                  # 我是慢了兩拍才看清
    acc = accuracy(sm)
    if acc is not None:
        return ("calibration", acc)
    return (None, None)


_REASON_SAY = {"confusable": "這兩種狀態在我裡頭本來就很像，我把「{x}」當成了另一個",
               "slow": "我是慢了兩拍才看清，當下講的是還沒更新的那個我",
               "calibration": "我對自己的判斷本來就沒有很準"}


def correction_facts(mismatch, sm, last_self_report=""):
    """🪞 §2.05 給文字層的事實卡，**最多三行**：①要改的那句（逐字、誠實紅線）②**唯一一個**理由
    ③我上一則真的對他說的原話（有才給）。不得出現第二種理由——那就又變成讀數了。"""
    mm = mismatch or {}
    lines = [f"你剛剛對他說你{_PHRASE.get(mm.get('from'))}，其實比較像{_PHRASE.get(mm.get('to'))}。"]
    kind, x = correction_reason(sm)
    if kind:
        lines.append("你會看走眼是因為：" + _REASON_SAY[kind].replace("{x}", str(x or "")))
    if last_self_report:
        lines.append(f"你上一則真的說出口的是：「{str(last_self_report)[:40]}」——你要改的就是這句。")
    return "\n".join(lines)


def correction_text(mismatch):
    """🪞 主動自我修正的一句（剛對使用者報過舊狀態、內省又發現認錯了 → **低調**回頭補一句更正）。
    刻意不演：不用「等一下！」這種戲劇性轉折、不講「恍神/慢半拍才回神」、不加 😂——就一句安靜的更正。"""
    return (f"（喔，補一下：我剛說我{_PHRASE.get(mismatch['from'])}，"
            f"其實比較像{_PHRASE.get(mismatch['to'])}。）")
