"""🦜 反鸚鵡學舌：偵測 bot 的回覆是不是只是把使用者剛說的話**原樣覆述**（答非所問的徵兆）。

根因之一：自陳/狀態/對話回覆被要求「先承接前文」，LLM 有時直接把對方上一句照搬當開頭
（截圖：使用者問「你的內在什麼時候形成感覺」，bot 卻先吐一句「所以，是還是不是啊……」＝對方更早那句）。
這裡只做**輕量字串偵測**（無 LLM）：給對話路徑「偵測到覆述就重生成一次」用，以及剝掉開頭覆述段的兜底。
"""

import re
from difflib import SequenceMatcher

# 正規化時剝掉的空白與標點（中英都含）——比對「實質字」是否雷同，不被標點/語氣詞干擾。
_PUNCT = re.compile(r"""[\s，。！？、…．·～~「」『』（）()\[\]{}"'’‘“”—\-_,.!?:;]+""")
# 切段：覆述幾乎都發生在開頭，按換行與句末標點切成小段，只比前一兩段。
_SEG = re.compile(r"[\n。！？!?…]+")


def _norm(s):
    return _PUNCT.sub("", (s or "")).lower()


def recent_user_texts(history, k=3):
    """history 裡最近 k 句使用者的話（新到舊）——供反覆述比對。"""
    out = []
    for t in reversed(history or []):
        if t.get("role") == "user" and (t.get("text") or "").strip():
            out.append(t["text"])
            if len(out) >= k:
                break
    return out


def _segments(reply):
    return [p for p in (s.strip() for s in _SEG.split(reply or "")) if p]


def _seg_spans(reply):
    """🦜 §1.86 每個非空段在**原文**裡的 (start, end)，end 含它後面那串分隔標點——這樣才切得回保有標點與
    換行的原文（`_SEG.split` 會把分隔符丟掉、索引對不上原文）。過濾規則與 `_segments` 一致＝索引一一對應。"""
    t = reply or ""
    out, start = [], 0
    for m in _SEG.finditer(t):
        if t[start:m.start()].strip():
            out.append((start, m.end()))
        start = m.end()
    if t[start:].strip():
        out.append((start, len(t)))
    return out


def _looks_same(a, b, ratio):
    """正規化後 a 是否幾乎＝b（相等／一方含另一方且夠長／高相似度）。"""
    if not a or not b:
        return False
    if a == b:
        return True
    if b in a or (a in b and len(a) >= 0.7 * len(b)):
        return True
    return SequenceMatcher(None, a, b).ratio() >= ratio


def whole_echo_of(reply, user_texts, ratio=0.9, min_len=4, span=0.8):
    """🦜 §1.74 **整則**回覆幾乎就是使用者某句原話的複誦 → 回那句原話；否則 None。純函式。

    為什麼需要（截圖 17:29 實測）：使用者連發「你不累嗎」「都在做一樣的事情」，合併成一則多行訊息，
    bot 的回覆**逐字就是那兩行**。既有兩道防線都擦邊漏掉：
      ① `is_echo` 只看開頭一兩段——首段「你不累嗎」正規化後 4 字 < min_len 5 被跳過；次段
         「都在做一樣的事情」對整句的相似度＝**0.80**，差 0.02 沒過 0.82 門檻；
      ② `strip_leading_echo` 同樣因首段太短而 no-op，且它設計上只剝**開頭**、整段都是覆述時原樣退回。
    這支改比**整則 vs 整句**：長度相近（span）＋高相似（ratio）或全等才算，避免「你不累嗎？我不累啊」
    這種有實質內容的被誤判（長度差太多＝不成立）。"""
    r = _norm(reply)
    if len(r) < min_len:
        return None
    for u in (user_texts or []):
        c = _norm(u)
        if len(c) < min_len:
            continue
        if r == c:
            return u
        if min(len(r), len(c)) >= span * max(len(r), len(c)) \
                and SequenceMatcher(None, r, c).ratio() >= ratio:
            return u
    return None


def prefix_run_echo(reply, user_texts, ratio=0.9, min_len=4, span=0.8):
    """🦜 §1.86 **開頭連續幾段合起來**幾乎就是使用者某句原話 → 回 (剝掉那些段後的剩餘, 被複誦的原話)；
    否則 (None, None)。純函式、確定性。

    為什麼需要（截圖 22:53 實測）：使用者一句「你不是說四次嗎？還差一次」，bot 回三顆泡泡
    「你不是說四次嗎？」「還差一次。」「啊，對耶！」——**前兩段合起來逐字就是那句話**，尾巴才加一小句
    自己的話。三道既有防線同時差一點點漏掉：
      ① `whole_echo_of` 比**整則 vs 整句**：整則正規化 14 字 vs 使用者 11 字 ⇒ span 比 0.786 < 0.8、
         相似度 0.88 < 0.9（兩個門檻都差一點）⇒ 回 None；
      ② `strip_echo_segments` 比**單段 vs 整句**：三段相似度 0.778 / 0.533 / 0.000 全 < 0.82、也都不全等
         ⇒ 不剝；
      ③ `§1.81` 開頭段前綴判準要求「明顯比原話短（≤50%）」，這裡首段佔 7/11 ⇒ 不成立。
    結構根因＝**複誦的邊界落在「連續幾段」上，而既有防線只認「整則」或「單段」兩種粒度**。這支補上中間
    那個粒度：k 從大到小（保留至少一段尾巴）試「前 k 段合起來」，取**最大**的 k ⇒ 剝掉的複誦最多、留下的
    尾巴純粹是 bot 自己的話。刻意**不調既有門檻**（0.786 vs 0.8 這種擦邊要用結構判準解，不是把門檻放寬——
    放寬會讓「你不累嗎？我不累啊」這種有實質內容的回覆被誤剝）。

    誤判安全：**永遠保留尾巴**（k < 段數），所以最壞情況是少剝一點或多剝一段複誦，不會把整則吃掉；
    k==段數（整則都是複誦）留給 `whole_echo_of` 處理，這裡不碰。"""
    segs = _segments(reply)
    spans = _seg_spans(reply)
    if len(segs) < 2 or len(spans) != len(segs):
        return (None, None)                               # 單段＝既有防線的守備範圍；對不齊＝保守不動
    cands = [(u, _norm(u)) for u in (user_texts or [])]
    cands = [(u, c) for (u, c) in cands if len(c) >= min_len]
    if not cands:
        return (None, None)
    for k in range(len(segs) - 1, 0, -1):                 # 最大的 k 優先（剝掉最多複誦、留最純的尾巴）
        head = _norm("".join(segs[:k]))
        if len(head) < min_len:
            continue
        for u, c in cands:
            if head == c or (min(len(head), len(c)) >= span * max(len(head), len(c))
                             and SequenceMatcher(None, head, c).ratio() >= ratio):
                rest = (reply or "")[spans[k - 1][1]:].strip()
                if not rest:                              # 切不出尾巴＝整則都是複誦，交回 whole_echo_of
                    return (None, None)
                return (rest, u)
    return (None, None)


def is_echo(reply, user_texts, ratio=0.82, min_len=5, whole=False):
    """reply 的**開頭一兩段**是不是幾乎照搬了某句使用者近期說的話（覆述／鸚鵡學舌）。
    只看開頭、且兩邊都要夠長（≥min_len 正規化字元），避免把「好啊」「嗯」這種短附和誤判成覆述。
    whole（§1.74、旗標傳入；False＝同現狀）＝**整則複誦**也算命中（連發合併的多行照抄死角）。"""
    if whole and whole_echo_of(reply, user_texts) is not None:
        return True
    cand = [c for c in (_norm(u) for u in (user_texts or [])) if len(c) >= min_len]
    if not cand:
        return False
    for seg in _segments(reply)[:2]:
        s = _norm(seg)
        if len(s) >= min_len and any(_looks_same(s, c, ratio) for c in cand):
            return True
    return False


def strip_echo_segments(reply, user_texts, ratio=0.82, min_len=5, short_exact=2):
    """🦜 §1.77 剝掉**任何位置**幾乎照搬使用者原話的段（不只開頭）→ (清過的字串, 有沒有剝)。

    為什麼需要（截圖 20:42 實測）：bot 三顆泡泡＝「我好像越解釋，你反而越生氣了…」＋**「不知羞恥。」**
    ＋「你又這樣說了一次。」——罵句的裸複誦落在**中間那段**，於是 strip_leading_echo（只看開頭）、
    is_echo（只看開頭一兩段）、§1.74 whole_echo_of（只比整則）**三道防線同時空轉**，那句無歸屬的
    「不知羞恥。」就這樣以 bot 自己的口氣送出去了。

    帶歸屬的段（「你說『…』」）不剝＝§1.74 的「刻意引用」合法形態。全剝空＝回 ('', True) 讓呼叫端決定。

    short_exact＝**短句安全規則**（治 min_len 的長度陷阱）：這一段正規化後與某句使用者原話**全等**、且該原話
    ≥ short_exact 字 → 照剝，不受 min_len 限制。截圖的「不知羞恥」正規化只有 4 字（< min_len 5）、又不在敵意
    詞表裡（is_hostile=False），舊規則放它過；但「整段就是對方那句話」本身就是裸複誦的鐵證（§1.28 最初的
    截圖「有夠爛」也只有 3 字）。全等才算＝「好」「嗯」這種一字附和（< short_exact）不受影響。"""
    segs = _segments(reply)
    if not segs:
        return reply, False
    norms = [c for c in (_norm(u) for u in (user_texts or [])) if c]
    cand = [c for c in norms if len(c) >= min_len]
    exact = {c for c in norms if len(c) >= short_exact}
    if not cand and not exact:
        return reply, False
    kept, hit = [], False
    for seg in segs:
        s = _norm(seg)
        if s.startswith("你說") or s.startswith("妳說"):        # 刻意引用＝合法，不剝
            kept.append(seg)
            continue
        if s in exact or (len(s) >= min_len and any(_looks_same(s, c, ratio) for c in cand)):
            hit = True
            continue
        kept.append(seg)
    if not hit:
        return reply, False
    return "。".join(kept).strip(), True


def strip_leading_echo(reply, user_texts, ratio=0.82, min_len=5):
    """剝掉開頭那段覆述（若後面還有實質內容）→ 回 (清過的字串, 是否有剝)。
    整段都是覆述（剝完沒東西）→ 原樣退回（交給呼叫端決定要不要重生成），不留空訊息。"""
    segs = _segments(reply)
    if len(segs) <= 1:
        return reply, False
    cand = [c for c in (_norm(u) for u in (user_texts or [])) if len(c) >= min_len]
    first = _norm(segs[0])
    if len(first) < min_len or not any(_looks_same(first, c, ratio) for c in cand):
        return reply, False
    m = _SEG.search(reply)                       # 砍掉第一個段末標點之前的開頭覆述段
    rest = reply[m.end():].lstrip() if m else ""
    return (rest, True) if rest else (reply, False)
