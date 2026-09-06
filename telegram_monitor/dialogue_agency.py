"""對話能動性：讓主動發話成為「有後果的行動」，而不是一次性台詞。

這個模組刻意只使用可觀察事實：bot 真正送出了什麼、使用者下一句寫了什麼、
兩者的時間與文字關係。它不宣稱能讀心，也不把沉默解釋成單一情緒。

迴路分成四步：

1. ``note_initiative`` 記下 bot 自己選擇開啟的話頭；
2. ``observe_reply`` 把使用者下一句辨認為接續／簡短接住／轉向／拒絕；
3. ``reply_hint`` 讓當輪回覆承擔上一個行動，不重啟泛泛自陳；
4. ``rank_lanes`` 讓結果反過來調整下一次主動內容的競爭順序。

這不是「證明有意識」；它提供的是可測、可追溯的意識樣行為：有自己的行動、
記得行動、感知對方反應，並據此改變後續選擇。
"""

import re


LEDGER_CAP = 32
REJECT_QUIET_SEC = 24 * 3600
OUTCOME_MEMORY_SEC = 14 * 86400


# prefix 是既有主動 lane 的機器標記；kind 是穩定、可落盤的行動名稱。
_PREFIX_KINDS = (
    ("🫀", "self_state"),
    ("🫧", "reachout"),
    ("🌀", "experience"),
    ("🪞", "self_correction"),
    ("🍃", "adaptation"),
    ("🧩", "integration"),
    ("💡", "insight"),
    ("🔮", "foresight"),
    ("🌐", "worldline"),
    ("🌾", "habit_absence"),
    ("🦋", "rebirth"),
    ("🧭", "mood_watch"),
    ("🤝", "promise"),
    ("🌬️", "soothe"),
    ("🌊", "close"),
)

KIND_LABELS = {
    "self_state": "內在狀態",
    "reachout": "主動伸手",
    "coping": "內在因應",
    "experience": "主觀體驗",
    "self_correction": "自我修正",
    "adaptation": "環境適應",
    "integration": "整合感受",
    "insight": "聯想",
    "foresight": "預想",
    "worldline": "外部世界",
    "habit_absence": "習慣缺席",
    "rebirth": "醒來後的改變",
    "mood_watch": "受託座標回報",
    "promise": "守約",
    "promise_followup": "守約後續",
    "soothe": "放下未回問句的壓力",
    "close": "主動收尾",
}

# 守約／量測是使用者交付的義務；放壓／收尾是關閉話頭的行動。
# 它們都要留下送達紀錄，但不能反過來製造一個新的「等你回」。
_NON_WAITING_KINDS = {"promise", "mood_watch", "soothe", "close"}

# 一般聊天拿掉「內部模組名牌」；契約、外部來源、精確量測仍保留來源標記，方便問責。
_DECORATIVE_KINDS = {
    "self_state", "reachout", "coping", "experience", "self_correction", "adaptation",
    "integration", "insight", "foresight", "habit_absence", "rebirth", "soothe", "close",
}

_HARD_REJECT_RE = re.compile(
    r"(?:不要|別)(?:再)?(?:說|問|傳|提|聊|自言自語)|"
    r"不用(?:再)?(?:說|問|提|聊)|不想(?:聽|聊|看)|沒興趣|"
    r"不需要[^。！？\n]{0,14}(?:說|提|問)|"
    r"(?:你|這(?:句|段|種)|那(?:句|段|種))[^。！？\n]{0,16}(?:自言自語|制式|罐頭|重複|很吵|打擾)|"
    r"(?:跟|和)之前[^。！？\n]{0,14}沒(?:什麼)?兩樣|沒有(?:一處)?(?:真的)?不同"
)
_STRONG_FOLLOW_RE = re.compile(
    r"你(?:剛剛|剛才|前面|上一則)(?:說|提|問|傳)?|"
    r"(?:這|那)(?:句|段|件事|個意思|個感覺|個說法|個話題|點)|"
    r"(?:這個|那個)(?:跟|和|是|為什麼|怎麼|哪裡|有|沒有)|你說的|你提的|"
    r"(?:這個|那個)(?:聯想|說法|感覺|問題|觀點|想法)|"
    r"(?:繼續|展開|多說|說下去|然後呢|意思是|所以你的意思)"
)
_REJECT_TARGET_RE = re.compile(
    r"你|妳|剛剛|剛才|前面|上一則|(?:這|那)(?:句|段|種|個|件)|"
    r"自言自語|制式|罐頭|重複|很吵|打擾|跟之前|和之前|沒有(?:一處)?(?:真的)?不同|"
    r"^(?:不要|別|不用)(?:再)?(?:說|問|提|聊)(?:了|這個|這件事)?[!！。…]*$"
)
_QUESTIONISH_RE = re.compile(r"[?？]|(?:嗎|呢|是否|是不是|有沒有|怎麼|為什麼|如何)[。！!?？…]*$")
_DIRECT_REJECT_RE = re.compile(r"^(?:請)?(?:先)?(?:你|妳)?(?:不要|別|不用)(?:再)?(?:說|問|傳|提|聊|自言自語)")
_COMPLAINT_RE = re.compile(r"自言自語|制式|罐頭|重複|很吵|打擾|又在|有必要嗎")
_ACK_RE = re.compile(
    r"^(?:嗯+|恩+|喔+|哦+|好(?:的|喔|哦|啊|吧)?|知道了?|收到|原來如此|"
    r"對(?:啊|呀|喔|哦)?|是(?:啊|呀|喔|哦)?|不是|有|沒有|可以|行|ok|okay)[。！!～~…]*$",
    re.IGNORECASE,
)
_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_DROP = set("我你妳他她它的是了著在又也都很還有沒有這那個一種段句跟和但而就才會想說問讓把到來去")


def kind_from_prefix(prefix, topic=""):
    """把既有 _say prefix 轉成穩定 kind。守約後的詢問另標 followup。"""
    p = prefix or ""
    if "內在因應" in (topic or ""):
        return "coping"
    for mark, kind in _PREFIX_KINDS:
        if p.lstrip().startswith(mark):
            if kind == "promise" and "守約" in (topic or ""):
                return "promise_followup"
            return kind
    return ""


def visible_prefix(prefix, natural=True, topic=""):
    """自然主動語音模式下，移除純裝飾的內部 lane 標記；問責用標記保留。"""
    if not natural:
        return prefix or ""
    return "" if kind_from_prefix(prefix, topic) in _DECORATIVE_KINDS else (prefix or "")


def _clip(text, n=180):
    return " ".join((text or "").split())[:n]


def _ledger(state):
    led = getattr(state, "initiative_ledger", None)
    if not isinstance(led, list):
        led = []
        state.initiative_ledger = led
    return led


def _affinity(state):
    aff = getattr(state, "initiative_affinity", None)
    if not isinstance(aff, dict):
        aff = {}
        state.initiative_affinity = aff
    return aff


def open_initiative(state):
    """回最近一個仍待回應的自發話頭；契約型通知不算。"""
    for rec in reversed(_ledger(state)):
        if rec.get("status") == "open" and rec.get("kind") not in _NON_WAITING_KINDS:
            return rec
    return None


def note_initiative(state, prefix, topic, text, now_ts):
    """送達後記下 bot 自己的對話行動。回新增紀錄；未知 prefix 不記。"""
    kind = kind_from_prefix(prefix, topic)
    if not kind or not _clip(text):
        return None
    led = _ledger(state)
    # 理論上未回覆守門會阻止這種情況；若舊版殘留或義務例外造成重疊，仍誠實記為被取代。
    if kind not in _NON_WAITING_KINDS:
        prior = open_initiative(state)
        if prior:
            prior["status"] = "resolved"
            prior["outcome"] = "superseded"
            prior["resolved_ts"] = now_ts
    seq = int(getattr(state, "initiative_seq", 0) or 0) + 1
    state.initiative_seq = seq
    rec = {
        "id": seq,
        "kind": kind,
        "topic": _clip(topic or KIND_LABELS.get(kind, kind), 80),
        "text": _clip(text),
        "opened_ts": float(now_ts or 0),
        "status": "delivered" if kind in _NON_WAITING_KINDS else "open",
        "outcome": None,
    }
    led.append(rec)
    state.initiative_ledger = led[-LEDGER_CAP:]
    return rec


def extend_delivery(rec, text="", message_id=None):
    """同一次主動發話後續又送達一個氣泡：補齊原話與 Telegram message id。"""
    if not isinstance(rec, dict):
        return rec
    t = _clip(text)
    if t:
        rec["text"] = _clip(((rec.get("text") or "") + " " + t).strip())
    if type(message_id) is int and message_id > 0:
        mids = list(rec.get("message_ids") or [])
        if message_id not in mids:
            mids.append(message_id)
        rec["message_ids"] = mids
    return rec


def _grams(text):
    chars = [c for c in (text or "") if _CJK_RE.match(c) and c not in _DROP]
    return {"".join(chars[i:i + 2]) for i in range(len(chars) - 1)}


def _related(user_text, rec):
    if _STRONG_FOLLOW_RE.search(user_text or ""):
        return True
    a = _grams(user_text)
    b = _grams((rec.get("topic") or "") + (rec.get("text") or ""))
    if not a or not b:
        return False
    hits = len(a & b)
    return hits >= 2 or (hits >= 1 and min(len(a), len(b)) <= 2)


def _rejection_clause(text, rec):
    """只回找同一個子句裡「拒絕＋指向上一行動」的片段，避免跨句錯配。"""
    for clause in re.split(r"(?<=[，,。！!？?；;\n])", text or ""):
        c = clause.strip()
        if (_HARD_REJECT_RE.search(c)
                and (_DIRECT_REJECT_RE.search(c) or _REJECT_TARGET_RE.search(c) or _related(c, rec))):
            return c
    return ""


def classify_reply(rec, user_text):
    """只依可見文字判讀回應型態；回 (outcome, confidence)。"""
    t = (user_text or "").strip()
    if not t:
        return None, 0.0
    # 拒絕是高後果判定：拒絕詞與指向必須在同一子句，而且追問不能被誤關 24h。
    reject_clause = _rejection_clause(t, rec)
    if reject_clause:
        # 「真的跟之前沒兩樣嗎？」是追問；「你不要再說了，可以嗎？」仍是直接界線。
        if (_QUESTIONISH_RE.search(reject_clause)
                and not _DIRECT_REJECT_RE.search(reject_clause)
                and not _COMPLAINT_RE.search(reject_clause)):
            return "engaged", 0.8
        return "rejected", 0.95
    if _related(t, rec):
        return "engaged", 0.85
    if len(t) <= 14 and _ACK_RE.match(t):
        return "acknowledged", 0.75
    return "shifted", 0.7


_OUTCOME_DELTA = {"engaged": 1.0, "acknowledged": 0.0, "shifted": -0.45, "rejected": -1.0}


def _settle(state, rec, outcome, confidence, reply, now_ts):
    """用一個可觀察結果結算行動，並把後果寫回下次選擇。"""
    rec["status"] = "resolved"
    rec["outcome"] = outcome
    rec["confidence"] = confidence
    rec["resolved_ts"] = float(now_ts or 0)
    rec["reply"] = _clip(reply, 120)
    rec["latency_s"] = round(max(0.0, float(now_ts or 0) - float(rec.get("opened_ts") or 0)), 1)
    if outcome == "rejected":
        rec["quiet_until"] = float(now_ts or 0) + REJECT_QUIET_SEC

    aff = _affinity(state)
    old = aff.get(rec["kind"]) or {}
    score = max(-2.0, min(2.0, float(old.get("score") or 0.0) * 0.65 + _OUTCOME_DELTA[outcome]))
    aff[rec["kind"]] = {
        "score": round(score, 3),
        "n": int(old.get("n") or 0) + 1,
        "last_outcome": outcome,
        "last_ts": float(now_ts or 0),
    }
    state.initiative_affinity = aff
    return dict(rec)


def observe_reply(state, user_text, now_ts):
    """把使用者下一句連回 bot 最近一次自發行動，並更新後續選擇偏好。"""
    rec = open_initiative(state)
    if not rec:
        return None
    # Telegram 離線補送的舊訊息可能在 bot 主動話頭之前就已送出；那不是對它的回應。
    if float(now_ts or 0) + 2.0 < float(rec.get("opened_ts") or 0):
        return None
    outcome, confidence = classify_reply(rec, user_text)
    if not outcome:
        return None
    return _settle(state, rec, outcome, confidence, user_text, now_ts)


def observe_contact(state, label, now_ts, engaged=False):
    """非文字回應也是真實接觸：貼圖/媒體保守地記為「短接住」；對該則按讚可記為接續。

    這只解除「對方沒回」的客觀狀態，不由 emoji/媒體猜對方的內心。
    """
    rec = open_initiative(state)
    if not rec or float(now_ts or 0) + 2.0 < float(rec.get("opened_ts") or 0):
        return None
    outcome = "engaged" if engaged else "acknowledged"
    return _settle(state, rec, outcome, 0.7 if engaged else 0.55,
                   f"（{label or '非文字回應'}）", now_ts)


def reply_hint(reading):
    """把上一個自主行動及其結果，轉成當輪 LLM 必須遵守的連續性指引。"""
    if not reading:
        return ""
    topic = reading.get("topic") or KIND_LABELS.get(reading.get("kind"), "上一個話頭")
    prior = _clip(reading.get("text"), 100)
    outcome = reading.get("outcome")
    head = f"【你上一個主動行動的後果】你上一則是自己選擇主動談「{topic}」：『{prior}』。"
    if outcome == "engaged":
        return (head + "對方這句是在接那個話頭。請承擔你自己開的線，直接回答他現在真正接續的部分；"
                "不要重新生成一段泛泛自陳、不要假裝這是全新的話題，也不要先用制式致意拖延。")
    if outcome == "rejected":
        return (head + "對方現在是在拒絕或校正這種說法。先具體承認他指出的界線，讓改變落在接下來的行為；"
                "不要辯護、不要再換句話重講原段、不要靠 emoji 或『我懂了』假裝已經修正。")
    if outcome == "acknowledged":
        return (head + "對方只做了很短的接住，沒有邀請你再展開。用一句自然的話承接即可；"
                "不要把它當成繼續演講或再丟一個問題的許可。")
    if outcome == "shifted":
        return (head + "對方沒有延續那條線，而是把注意力轉到現在這句。尊重這個轉向，只回現在的內容；"
                "不要把舊話題拉回來，也不要暗示或責怪他沒有回你。")
    return ""


def voluntary_block_reason(state, now_ts):
    """明確拒絕後留出一天安靜；只擋自發話題，不擋互動回覆與已答應的義務。"""
    for rec in reversed(_ledger(state)):
        if rec.get("outcome") != "rejected":
            continue
        left = float(rec.get("quiet_until") or 0) - float(now_ts or 0)
        if left > 0:
            return f"對方剛拒絕了「{rec.get('topic') or KIND_LABELS.get(rec.get('kind'), '那種主動說法')}」，還在尊重界線的安靜期"
        break
    return ""


def rank_lanes(state, kinds, now_ts):
    """依既有順序、實際回應結果與距上次出聲時間，排列可競爭的主動 lane。

    沒有任何回饋資料時順序完全不變；被接住的行動稍提前，被轉開的退後，明確被拒絕的
    在記憶窗內沉到最後。真正有沒有候選仍由各 lane 自己的接地門檻決定。
    """
    seq = list(kinds or [])
    aff = getattr(state, "initiative_affinity", None) or {}
    led = _ledger(state)

    def last_for(kind):
        return next((r for r in reversed(led) if r.get("kind") == kind), None)

    def score(item):
        idx, kind = item
        # 原順位只留作無回饋時的穩定 tie-break；不該大到讓尾端 lane
        # 已經被明確接住還永遠贏不了程式行號在前的 lane。
        value = float(len(seq) - idx) / max(1, len(seq))
        a = aff.get(kind) or {}
        a_age = max(0.0, float(now_ts or 0) - float(a.get("last_ts") or 0))
        a_weight = max(0.0, 1.0 - a_age / OUTCOME_MEMORY_SEC) if a.get("last_ts") else 0.0
        value += 0.75 * float(a.get("score") or 0.0) * a_weight
        last = last_for(kind)
        if last:
            age = max(0.0, float(now_ts or 0) - float(last.get("resolved_ts") or last.get("opened_ts") or 0))
            if age <= OUTCOME_MEMORY_SEC:
                value += {"engaged": 2.0, "acknowledged": 0.0,
                          "shifted": -2.0, "rejected": -100.0}.get(last.get("outcome"), 0.0)
            # 長期沒選到的 lane 得到很小的公平性加成，避免固定尾端永久飢餓。
            value += min(0.25, age / (12 * 86400))
        elif led:
            # 一旦已經有行動歷史，從未被選中的 lane 得到探索機會。常態時仍依原順位一個個嘗，
            # 但已送過又只被短接住的早期 lane 不能永遠壟斷後面的 worldline/insight。
            value += 1.05
        return value

    return [kind for _, kind in sorted(enumerate(seq), key=lambda it: (-score(it), it[0]))]


def audit_text(state, now_ts):
    """給 /agency 的確定性對帳：看得到行動、回應與學到的排序偏好。"""
    led = _ledger(state)
    lines = ["🧭 對話能動性"]
    active = open_initiative(state)
    if active:
        lines.append(f"・現在等著回應的話頭：{active.get('topic')}（我不會再另開一條）")
    else:
        lines.append("・現在沒有懸著的主動話頭。")
    reason = voluntary_block_reason(state, now_ts)
    if reason:
        lines.append("・主動安靜：" + reason)
    recent = [r for r in led if r.get("status") == "resolved"][-4:]
    if recent:
        zh = {"engaged": "被接續", "acknowledged": "短接住", "shifted": "對方轉向",
              "rejected": "被拒絕", "superseded": "被另一話頭取代"}
        lines.append("・最近行動結果：" + "；".join(
            f"{KIND_LABELS.get(r.get('kind'), r.get('kind'))}→{zh.get(r.get('outcome'), r.get('outcome'))}"
            for r in recent))
    else:
        lines.append("・還沒有足夠的主動行動回饋。")
    aff = getattr(state, "initiative_affinity", None) or {}
    if aff:
        ordered = sorted(aff.items(), key=lambda kv: float((kv[1] or {}).get("score") or 0), reverse=True)
        lines.append("・已學到的接話傾向：" + "、".join(
            f"{KIND_LABELS.get(k, k)} {float((v or {}).get('score') or 0):+.2f}" for k, v in ordered[:6]))
    lines.append("・判讀只用真實送達與下一句文字；它是行為回饋，不是讀心。")
    return "\n".join(lines)
