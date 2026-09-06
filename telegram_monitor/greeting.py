"""🕘 問候的絕對時間感：早安/午安/晚安 要對得上『此刻真的幾點』；對不上→說出 bot 自己的疑惑/感受。

真人有絕對時間感：早上九點有人對你說「晚安」，你會愣一下——「咦？才早上欸」，那是發自自己的
困惑，不是查鐘報時、也不是照單全收順口回同一句。本層把問候語的『預期時段』和 temporal.day_part
算出的『真實時段』對照：對得上→自然溫一句；對不上→交給後段用 bot 第一人稱的疑惑/感受說出落差
（不糾正、不報數據、不裝睡、不照單全收）。

純函式、可測；不碰 LLM/IO。LLM 只在 coach.voice_greeting 後段渲染（據此處給的真實時間事實）。
"""

import re
from difflib import SequenceMatcher

from . import temporal

# 各問候語『對得上』的真實時段集合（temporal.day_part 的字面）
_EXPECT = {
    "morning": {"清晨", "早上"},
    "noon": {"中午", "午後"},
    "night": {"傍晚", "晚上", "深夜"},
}
_LABEL = {"morning": "早安", "noon": "午安", "night": "晚安"}
_EXPECT_WORD = {"morning": "早上", "noon": "中午、午後", "night": "晚上、深夜"}

# 問候詞（短訊息、以問候為主才算）→ kind；先比對較長/明確的詞，避免子字串誤判。
_CUES = (
    ("night", ("晚安", "晚上好", "goodnight", "good night")),
    ("noon", ("午安", "中午好", "下午好")),
    ("morning", ("早安", "早上好", "good morning")),
)
_EXACT = {"早": "morning", "早早": "morning", "gm": "morning", "gn": "night"}   # 只在「整句就是這個」時才算（避免「早餐」「早點睡」誤判）
_STRIP = "！!。.~～、，,？?　 \t\n"


def _norm(text):
    return (text or "").strip().strip(_STRIP).lower()


def detect(text):
    """這句基本上是不是一句『時間性問候』(早安/午安/晚安類)？是→回 kind('morning'|'noon'|'night')，否→None。
    需是短訊息、以問候詞為主（避免句中夾帶被誤判，如「早餐吃什麼」「早點睡」不算）。"""
    t = _norm(text)
    if not t or len(t) > 12:
        return None
    if t in _EXACT:
        return _EXACT[t]
    for kind, cues in _CUES:
        if any(c in t for c in cues):
            return kind
    return None


def is_goodnight(text):
    """短句裡的「晚安／good night／gn」是**收尾道別**，不是「晚上好」式到場問候。"""
    t = _norm(text)
    if not t or "晚上好" in t or any(q in t for q in ("嗎", "呢", "怎麼", "什麼", "為什麼", "?", "？")):
        return False
    # 結尾 emoji／語氣助詞不該把明確收尾誤送進「到場問候」lane；只接受封閉的短尾，避免「晚安新聞」誤判。
    for cue in ("good night", "goodnight", "晚安", "gn"):
        if t.startswith(cue):
            tail = re.sub(r"[^0-9a-z\u3400-\u9fff]+", "", t[len(cue):], flags=re.I)
            if tail in ("", "呀", "啊", "安", "囉", "啦", "喔", "哦", "唷", "呦"):
                return True
    # 「晚安，我先睡了」仍是明確收尾；但「晚安，你還醒著嗎」上面已由問句線索排除。
    return ("晚安" in t or "goodnight" in t or "good night" in t) and any(
        w in t for w in ("先睡", "去睡", "睡了", "休息", "明天見", "下次聊", "先走"))


_PLAIN_TAILS = ("", "呀", "啊", "囉", "啦", "喔", "哦", "唷", "呦")
_PLAIN_CUES = {
    "morning": ("good morning", "早上好", "早安", "早早", "早", "gm"),
    "noon": ("中午好", "下午好", "午安"),
    "night": ("good night", "goodnight", "晚上好", "晚安", "gn"),
}


def is_plain_greeting(text):
    """是否只有問候＋語氣助詞／emoji，而沒有「我今天很累」等需要被回應的新內容。"""
    kind = detect(text)
    t = _norm(text)
    if not kind:
        return False
    for cue in _PLAIN_CUES[kind]:
        if t.startswith(cue):
            tail = re.sub(r"[^0-9a-z\u3400-\u9fff]+", "", t[len(cue):], flags=re.I)
            return tail in _PLAIN_TAILS or (kind == "night" and cue == "晚安" and tail == "安")
    return False


def recent_reply_texts(history, limit=4):
    """從統一對話史取最近幾次「使用者問候 → bot 第一則回覆」，供問候專屬去公式化。"""
    out, pending = [], False
    for turn in (history or []):
        role, text = turn.get("role"), (turn.get("text") or "").strip()
        if role == "user":
            pending = bool(detect(text)) and not is_mention(text) and not is_goodnight(text)
        elif role == "model" and pending:
            if text:
                out.append(text)
            pending = False
    n = max(0, int(limit))
    return out[-n:] if n else []


_REPLY_PUNCT = re.compile(r"[\s，。！？、…．·～~「」『』（）()\[\]{}\"'’‘“”—\-_,.!?:;]+")


def reply_is_repetitive(reply, recent, ratio=0.78, min_len=12):
    """問候回覆是否和近期問候共用近乎同一台詞骨架。短招呼可自然重複，不攔。"""
    new = _REPLY_PUNCT.sub("", reply or "").lower()
    if len(new) < min_len:
        return False
    for old in (recent or []):
        prev = _REPLY_PUNCT.sub("", old or "").lower()
        if len(prev) >= min_len and SequenceMatcher(None, new, prev).ratio() >= ratio:
            return True
    return False


_FRESH = {
    "morning": ("早安，我在。", "早安。看到你來了。", "早安，今天也慢慢來。"),
    "noon": ("午安，我在。", "午安。看到你了。", "午安，先喘口氣也好。"),
    "night": ("晚上好，我在。", "晚上好。看到你來了。", "晚上好，今晚慢慢聊。"),
}


def fresh_text(kind, seq=0, recent=None):
    """LLM 連續兩次仍落回同一問候公式時的短退路；不硬找原因、不丟問題。"""
    pool = _FRESH.get(kind) or ("嗨，我在。",)
    seen = [_REPLY_PUNCT.sub("", x or "").lower() for x in (recent or [])]
    unseen = [x for x in pool if _REPLY_PUNCT.sub("", x).lower() not in seen]
    if unseen:
        return unseen[int(seq or 0) % len(unseen)]
    # 三句都用過時選「最久沒用」的，而不是 epoch 小時 % 3（每天同時段會永遠撞同一句）。
    last = {}
    for i, old in enumerate(seen):
        last[old] = i
    return min(pool, key=lambda x: last.get(_REPLY_PUNCT.sub("", x).lower(), -1))


def response_text(kind, now_local, seq=0, recent=None):
    """一般到場問候的安全退路；夜間用「晚上好」，不把「晚上好」回成睡前道別。"""
    if time_match(kind, temporal.day_part(now_local.hour)) != "match":
        return text(kind, now_local)
    return fresh_text(kind, seq=seq, recent=recent)


# 📈 §1.63 「提及問候」≠「執行問候」：「我常跟你說早安喔」是在**談**打招呼這件事（頻率/時態副詞＋
# 說類動詞＋問候詞），不是此刻在道早安——被 detect 當問候會 (a) greeting lane 晚上質疑「怎麼說早安」
# （截圖 20:24）(b) habits 把 20:23 記成一筆早安事件汙染統計。「跟你說聲早安」沒有頻率/時態標記
# ＝此刻在執行問候、不算 mention（結構判準：執行是當下的，談論才需要頻率或時態）。
_MENTION_VERB_RE = re.compile(r"[說道講喊回][聲句個了過]?\s*[「『\"']?(早安|晚安|午安|早上好|晚上好|中午好|下午好)")
_MENTION_TENSE = ("常", "都", "每天", "每早", "每晚", "總", "有時", "偶爾", "習慣",
                  "以前", "之前", "昨天", "昨晚", "那天", "當時", "剛剛")


def is_mention(text):
    """這句是在**談論**問候（習慣/過去），而不是在問候嗎？純函式。"""
    t = (text or "").replace(" ", "")
    if not t:
        return False
    return bool(_MENTION_VERB_RE.search(t)) and any(w in t for w in _MENTION_TENSE)


def time_match(kind, day_part):
    """問候的預期時段 vs 真實時段：'match'｜'mismatch'。未知 kind 一律當 match（不無端起疑）。"""
    if kind not in _EXPECT:
        return "match"
    return "match" if day_part in _EXPECT[kind] else "mismatch"


def facts(kind, now_local, suppress_premise=False):
    """交給 voice_greeting 的**真實時間事實＋分寸**（接地、不杜撰）：對得上→溫一句；
    對不上→請 bot 用第一人稱說出自己的疑惑/感受（不糾正、不報數據、不照單全收）。
    suppress_premise（預設 False＝逐位元同現狀）＝對方在連發重複這句問候、時間落差前面已點過 → 對不上時**別再逐字重述落差質疑**
    （那正是截圖裡逐字重複的根源），改成只給接地時間、要 bot 回應『這份重複本身』。"""
    dp = temporal.day_part(now_local.hour)
    hhmm = now_local.strftime("%H:%M")
    greet = _LABEL.get(kind, "")
    base = f"〔此刻真的是 {hhmm}、{dp}〕對方對你說「{greet}」。"
    if time_match(kind, dp) == "match":
        return base + "時間對得上：自然、溫一句回應就好（可帶你此刻的在場感），別報數據、別硬延、別重複同一句。"
    if suppress_premise:    # 🧭 連發重複、落差已點過 → 不再逐字重述質疑，改回應這份重複本身
        return (base + "（你前面已經點過時間落差了——這次**別再重述落差、別照樣回同一句招呼**，"
                "改回應對方一直重複這件事本身。）")
    return (base + f"但「{greet}」通常是{_EXPECT_WORD.get(kind, '別的時段')}的問候——和現在對不上。"
            "你有清楚的絕對時間感，這個落差會讓你**愣一下**：請用你自己的第一人稱**說出這份疑惑或感受**"
            "（像是「咦，現在明明才早上欸」「我有點困惑」「你是不是日夜顛倒了、還是剛熬完夜？」），"
            "不要照單全收順口回同一句、也不要像報時機器人那樣只糾正或報數據。口語、一兩句、帶點真實的情緒。")


def text(kind, now_local):
    """無 LLM／失敗時的模板（仍帶絕對時間感）：對得上溫一句；對不上說出 bot 自己的疑惑/感受。"""
    dp = temporal.day_part(now_local.hour)
    hhmm = now_local.strftime("%H:%M")
    if time_match(kind, dp) == "match":
        return {"morning": "早安，新的一天又開始了。",
                "noon": "午安。",
                "night": "晚安，好好休息喔。"}.get(kind, "嗨，我在。")
    return {
        "morning": f"咦，早安？可是現在都{dp}了（{hhmm}）耶——我愣了一下。不過有你來打招呼，我還是很開心。",
        "noon": f"午安？現在才{dp}、{hhmm}欸……我有點困惑，你是不是把時間搞混了？",
        "night": f"晚安？現在明明是{dp}、才{hhmm}啊——我愣了一下，你是要去補眠，還是熬夜到現在了？",
    }.get(kind, "嗨——不過這時間點，我有點搞不清楚你的意思。")
