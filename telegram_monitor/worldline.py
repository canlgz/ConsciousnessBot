"""🌐 §1.95 外面的世界撞進你的線（WORLDLINE）：每天最多一次，拿使用者**自己那條還在寫的線**去搜，
把外面對同一件事的另一種說法帶回來，跟他寫過的原文擺在一起，開啟一次討論。

使用者需求（原話）：「bot每日從網路上找到最火熱的話題，然後主動與我討論，刺激我記寫交流」。
bot 自己把它講得更準：「把我對外部世界的感知，變成跟你對話的養分」「這樣我就不會一直繞著你那些已經
記下的東西打轉」。

**使用者已裁定的三個決定（設計據此，不要推翻）**：
① **接受偏移**：搜尋關鍵字用**他自己的標籤**、刻意排除新聞時事 ⇒ 這是「你那條線在外面的說法」，
   不是「今天的頭條」。理由：只丟熱門新聞他自己滑手機就有，而且牽強的配對比不說更糟。
② **嚴格白名單**：沒有 `/worldline allow <標籤>` 授權過的標籤，**一個字都不會離開這台機器**。
   代價是他不動手就永遠沉默 ⇒ 對帳段第一句就必須告訴他白名單是空的、怎麼授權。
③ **不先做驗證呼叫**：grounding 能不能用、回應長怎樣**都沒實測**。因此 tools 欄位名可設定、
   原始錯誤字串原樣印在 `/worldline`，上線打一次就知道要改哪個。

誠實紀律（三種來源永不混淆）：
  ①使用者寫的 → 必須**逐字**引用、且必須在記寫語料裡
  ②模型本來就知道的 → 不使用（這條 lane 只講查到的）
  ③剛查到的 → **必須帶得出來源網址**，拿不到就當這次失敗、沉默
把②③說成①，就是這個 repo 修了十幾次的老病換皮。

純函式、無 I/O、無 LLM、**絕不 import monitor**（比照 foresight.py／roster.py 的自我約束）。
"""

import re
from urllib.parse import urlsplit

_QUOTE_RE = re.compile(r"[「『]([^」』]{1,120})[」』]")
_NORM_RE = re.compile(r"[^0-9A-Za-z一-鿿]+")

# 🌐 §1.95 排除「新聞/事件」形態——使用者裁定要的是「這條線在外面的說法」，不是頭條。
# 這是對**我們自己輸出**的格式閘（不是對使用者語意的分類器），所以不違反詞表窮舉禁令；
# 誤判代價只是這次不說話（fail-closed）。
_EVENTY = re.compile(r"今天|昨天|稍早|最新消息|快訊|記者|報導指出|據報導|股價|漲停|跌停|確診|死亡人數|"
                     r"總統|立委|選舉|開票|政黨|爆料|外遇|分手|過世|逝世|車禍|地震|颱風|戰爭|空襲")

_MIN_QUOTE = 6          # 使用者引文太短就沒有碰撞可言
_ACTIVE_DAYS = 5.0      # 「還在動」的線才拿去撞（與 §1.90 的 dormant 下限 5 天互補＝結構上不撞題）
_MIN_N = 2              # 這條線至少要有兩筆，才算得上是一條線
_RETURN_GAP_S = 20 * 60  # 回返定義沿用 thresholds.RETURN_GAP_MINUTES（20 分）＝repo 對「回返」的唯一既有定義


def _norm(s):
    return _NORM_RE.sub("", s or "")


def _clip(s, n):
    return " ".join((s or "").split())[:n]


def display_name(rec):
    """🌐 §1.95 講給人聽的線名＝`category｜topicLabel`（monitor.py:77 的同一個式子）。
    ⚠️ 比對一律用**裸 topicLabel**，只有要顯示時才組這個。"""
    lab = (rec.get("topicLabel") or "").strip()
    cat = (rec.get("category") or "").strip()
    return f"{cat}｜{lab}" if cat else lab


def line_latest(recs, label, cap=60):
    """🌐 §1.95 某條線**最近一筆**的真實原文。**strip 後完全相等**分組——絕不寬鬆比對。

    為什麼要自己寫（實測，非猜）：當時 `monitor._topic_latest_excerpt` 用的是
    `lab == subject or subject in lab or lab in subject` ⇒ subject「閱讀」會取到「閱讀習慣」那筆
    （實測回「每天固定二十分鐘」）；而且所有 ts 都解析不出時它**仍然回傳內容**（回檔案順序最後那筆），
    不是 fail-closed。拿那種引文去逐字引用就是 §1.36 幻覺換皮。
    （🫧 §1.98 已把那兩個缺陷從**源頭**修掉——那支現在也是完全相等比對＋ts 壞就跳過。這支仍然留著，
    因為兩邊的資料形狀與用途不同：這裡吃呼叫端 parse 好的 `_ts`（epoch）、回 id/display/cap=60 供
    逐字引用與來源對帳，那支吃原始 `ts`（ISO 字串或 datetime）、只回一段摘錄。不是重複實作。）
    查不到／無可解析時間／無內文 ⇒ {}（fail-closed，絕不硬講）。"""
    lab = (label or "").strip()
    if not lab:
        return {}
    best = None
    for r in (recs or []):
        if (r.get("topicLabel") or "").strip() != lab:
            continue
        ts, txt = r.get("_ts"), " ".join((r.get("text") or "").split())
        if not ts or not txt:
            continue
        if best is None or ts > best["ts"]:
            best = {"ts": ts, "text": txt[:cap], "id": str(r.get("id") or r.get("fileId") or ""),
                    "label": lab, "display": display_name(r)}
    return best or {}


def _returns(recs, label):
    """🌐 §1.95 這條線的「回返次數」：同一條線兩筆之間隔 ≥20 分算一次回返（沿用 repo 對回返的既有定義，
    thresholds.RETURN_GAP_MINUTES；零新門檻、零詞表）。回 (次數, 跨越天數)。"""
    ts = sorted(r["_ts"] for r in (recs or [])
                if (r.get("topicLabel") or "").strip() == (label or "").strip() and r.get("_ts"))
    if len(ts) < 2:
        return (len(ts), 0.0)
    n = 1
    for a, b in zip(ts, ts[1:]):
        if (b - a) >= _RETURN_GAP_S:
            n += 1
    return (n, (ts[-1] - ts[0]) / 86400.0)


def pick_lines(recs, spans, allow, used, now_ts, active_days=_ACTIVE_DAYS, min_n=_MIN_N,
               include_dormant=False):
    """🌐 §1.95 挑「拿去撞外面」的線。**硬閘＝還在動**（≤5 天，與 §1.90 的「停住的線」互補、結構上不撞題）
    ＋至少 min_n 筆；**排序＝回返次數**（一直回頭寫＝有話要說、還沒定論，撞上外部說法最可能長出新記寫）。

    `allow`＝使用者授權過的裸標籤集合（**嚴格白名單**：不在裡面的一個字都不送出去）。
    `used`＝{label: 上次撞的時間戳}，同一條線 7 天內不重撞。
    對**完整集合** sorted 取首（§1.83 前科：不得先截尾再挑）。"""
    # include_dormant is reserved for an explicit user request. It changes only
    # age eligibility, not authorization, evidence, or seven-day deduplication.
    out = []
    for lab, sp in (spans or {}).items():
        if lab not in (allow or set()):
            continue                                       # ★ 嚴格白名單：沒授權就完全不考慮
        if int(sp.get("n") or 0) < min_n:
            continue
        days = (now_ts - (sp.get("last_ts") or 0)) / 86400.0
        if days < 0 or (days > active_days and not include_dormant):
            continue                                       # 不在動的線交給 §1.90
        if (now_ts - (used or {}).get(lab, 0)) < 7 * 86400:
            continue                                       # 同一條線 7 天內不重撞
        latest = line_latest([r for r in (recs or []) if r.get("_ts") and r["_ts"] <= now_ts], lab)
        if not latest or len(_norm(latest.get("text"))) < _MIN_QUOTE:
            continue                                       # 沒有夠長的真實引文＝沒有碰撞可言
        visits, span_days = _returns(recs, lab)
        out.append({"label": lab, "display": latest["display"], "quote": latest["text"],
                    "rec_id": latest["id"], "n": int(sp.get("n") or 0),
                    "visits": visits, "span_days": round(span_days, 1), "days": round(days, 1)})
    out.sort(key=lambda c: (c["visits"], c["span_days"], c["n"], -c["days"]), reverse=True)
    return out


def diagnose(recs, spans, allow, used, now_ts, active_days=_ACTIVE_DAYS, min_n=_MIN_N):
    """🌐 §2.00 逐標籤說出「這條為什麼撞不了」→ [(標籤, 原因)]，順序與 `pick_lines` 的閘**完全同序**。

    為什麼要有這支（實測，截圖 10:08）：`/worldline` 只印「線 27 條、可撞的 **0** 條」，
    使用者無從知道是**標籤打錯**、**筆數不夠**、**那條線停了**、還是**原文太短**——嚴格白名單下最貴的
    失敗模式就是「沉默且無法排查」，而這 lane 的預設狀態正好就是沉默。"""
    out = []
    for lab in sorted(allow or set()):
        sp = (spans or {}).get(lab)
        if not sp:
            out.append((lab, "你的記寫裡沒有這個標籤——名字要跟記寫裡的**裸標籤**一字不差（不是類別、不是「類別｜標籤」）"))
            continue
        n = int(sp.get("n") or 0)
        days = (now_ts - (sp.get("last_ts") or 0)) / 86400.0
        used_ago = (now_ts - (used or {}).get(lab, 0)) / 86400.0
        if n < min_n:
            out.append((lab, f"只有 {n} 筆（要 {min_n} 筆以上才算一條線）"))
        elif days > active_days:
            out.append((lab, f"最後一筆是 {days:.1f} 天前（要 {active_days:.0f} 天內還在寫；停住的線是 §1.90 在管）"))
        elif (used or {}).get(lab) and used_ago < 7:
            out.append((lab, f"{used_ago:.1f} 天前才撞過（同一條線 7 天內不重撞）"))
        else:
            latest = line_latest(recs, lab)
            if not latest:
                out.append((lab, "找不到「有時間又有內文」的那一筆（時間解析不出來的筆不算）"))
            elif len(_norm(latest.get("text"))) < _MIN_QUOTE:
                out.append((lab, f"最近那筆原文只有 {len(_norm(latest.get('text')))} 個字"
                                 f"（要 {_MIN_QUOTE} 字以上才有得撞）"))
            else:
                out.append((lab, "沒問題，這條撞得動"))
    return out


def ready_labels(recs, spans, used, now_ts, top=5, exclude=()):
    """🌐 §2.00 現在就**撞得動**的線（不看白名單）——授權的標籤全都不合格時，直接給他可以照抄的名字。
    只回標籤名與筆數；**不回內文**（沒授權的線一個字都不外送，這裡連顯示都不做）。"""
    allow_all = set((spans or {}).keys()) - set(exclude or ())
    return [(c["label"], c["n"]) for c in pick_lines(recs, spans, allow_all, used, now_ts)[:top]]


def build_query(label):
    """🌐 §1.95 送出去的搜尋字串——**只用標籤，絕不含記寫內文**（隱私紅線）。
    加上「觀點/討論/研究」把它往「別人怎麼看這件事」推、遠離時事頭條。"""
    lab = (label or "").strip()
    return f"{lab} 不同觀點 討論 研究" if lab else ""


def corpus(recs):
    """🌐 §1.95 使用者記寫語料（驗「逐字引用」用）。含 topicLabel（§1.90 實測：只收 text 會把真實標籤誤判成幻覺）。"""
    return "".join(_norm((r.get("text") or "") + " " + (r.get("topicLabel") or "")) for r in (recs or []))


def sources_ok(sources):
    """🌐 §1.95 沒有來源網址＝這次失敗（紅線：拿不到來源不准說「我查到」）。"""
    for _t, u in (sources or []):
        try:
            parsed = urlsplit(u or "")
            if parsed.scheme in ("http", "https") and parsed.hostname:
                return True
        except ValueError:
            continue
    return False


def eventy_hit(text):
    """🌐 §1.95 這則像不像新聞/時事播報 → 命中就退（使用者裁定：要的是「這條線在外面的說法」）。"""
    return bool(_EVENTY.search(text or ""))


def collision_ok(text, cand, cps):
    """🌐 §1.95 **防退化成新聞摘要的核心**：這則訊息裡到底有沒有「他的線」？回 (ok, why)。

    ① 必須逐字出現他那一筆的真實引文（且該引文在語料裡）——沒有他的東西就只是新聞摘要
    ② 所有「」內的片段都必須在語料裡（防止把查到的內容偽裝成他寫過的）
    ③ 不得是新聞/時事播報形態
    ④ 必須提到那條線的名字（確保講的是這條線、不是別的）"""
    t = text or ""
    q = (cand or {}).get("quote") or ""
    if not q or _norm(q) not in _norm(t):
        return (False, "沒有引用他那一筆的原文")
    # 線名（裸標籤與 category｜label 顯示名）本來就會被引號包住，那不是「他寫過的話」的宣稱 ⇒ 豁免。
    _exempt = {_norm(cand.get("label")), _norm(cand.get("display"))} - {""}
    for seg in _QUOTE_RE.findall(t):
        n = _norm(seg)
        if n and n not in _exempt and n not in (cps or ""):
            return (False, "引號裡有他沒寫過的東西")
    if eventy_hit(t):
        return (False, "講成新聞時事了")
    if _norm(cand.get("label")) not in _norm(t) and _norm(cand.get("display")) not in _norm(t):
        return (False, "沒提到是哪條線")
    return (True, "")


def strip_quotes(s):
    """🌐 §2.01 把**外面查回來的**那段話裡的引號拆掉——「」在這則訊息裡有專屬語意：**只包他寫過的字**。

    實測（真打一次 API 的端到端跑）：grounding 回來的說法**自己就常帶「」**（引書名、引術語），
    於是連程式模板 `fallback_text` 都被 `collision_ok` 的第②道閘擋掉（「引號裡有他沒寫過的東西」）⇒
    **這條 lane 從上線起就不可能開口**，而 §1.95 的註解還寫著模板「已設計成一定過得了」——
    單元測試的 finding 剛好沒有引號，所以全綠也蓋不到（§1.93 同型：測試把同一個錯誤假設一起編碼進去了）。"""
    return re.sub(r"[「」『』]", "", s or "")


def sanitize_quotes(text, cand, cps):
    """🌐 §2.01 只拆掉「不是他寫的」那些引號，**保留整則**——不是把整則丟掉退回模板。

    實測（真跑兩個形態）：形態 1 過閘，形態 0 被擋，差別只在 LLM 把**外面說法的一句轉述**也框了引號
    （「高於偶然性的預測能力」）。整則退回程式模板＝為了一個標記丟掉一整段好好講的人話——正是 §1.62
    使用者定案要避免的（「但 bot 原來的回答是很不錯的方式」）。引號在這則訊息裡唯一的意思是「這是他寫的」，
    所以拆掉那兩個符號就**剛好**消掉那個宣稱，字句一個都不用改。

    他寫過的（在語料裡）與線名一律保留引號；其餘只脫符號、留字。回新字串。"""
    _exempt = {_norm((cand or {}).get("label")), _norm((cand or {}).get("display"))} - {""}

    def _one(m):
        seg = m.group(1)
        n = _norm(seg)
        return m.group(0) if (n in _exempt or (n and n in (cps or ""))) else seg

    return _QUOTE_RE.sub(_one, text or "")


def fallback_text(cand, finding, sources):
    """🌐 §1.95 LLM 版沒過閘時的程式模板（**已設計成一定過得了 collision_ok**：逐字帶引文、帶線名、
    不含新聞詞）。finding＝查到的那一句（來自 grounded 回應、已過 eventy 閘）。
    🌐 §2.01 外部說法一律先 `strip_quotes`——不然它自帶的引號會讓這個「保證過閘」的模板自己過不了閘。"""
    _t, _u = (sources or [("", "")])[0]
    # 標題＋網址都給：**能查證**正是「必須帶來源」的理由（網址是轉址連結、約 30 天後會失效，README 有註明）
    _src = f"{_t}｜{_u}" if (_t and _u) else (_u or _t)
    return (f"你在寫的「{cand.get('display') or cand.get('label')}」——你最近那一筆寫的是"
            f"「{cand.get('quote')}」。\n"
            f"我到外面看了一下，有人是這樣講同一件事的：{_clip(strip_quotes(finding), 120)}\n"
            f"（來源：{_src}）\n"
            "這是同主題的外部材料；是否適用於你的記寫，還需要一起核對。")


def push_ledger(ledger, cand, sources, now_ts, keep=10, finding=""):
    """🌐 §2.16 finding 非空時一併存**查到的說法本身**（≤120 字）＋首來源站名：
    沒有它，使用者對那則 🌐 追問「那是誰說的？他怎麼講的？」時，聊天 lane **零接地**＝§1.36 幻覺
    家族的溫床（bot 只能從語感重編一個說法）。finding 空＝不加鍵＝逐位元同現狀。"""
    out = list(ledger or [])
    e = {"label": cand.get("label"), "display": cand.get("display"),
         "ts": now_ts, "rec_id": cand.get("rec_id"),
         "src": [u for _t, u in (sources or [])][:3]}
    if finding:
        e["finding"] = _clip(finding, 120)
        e["src_name"] = (sources or [("", "")])[0][0] or ""
    out.append(e)
    return out[-keep:]


def aftermath(ledger, recs, now_ts, days=3, last_n=3):
    """🌐 §2.16 撞完他**有沒有真的回去寫**——這整條 lane 的存在理由就是「刺激記寫」，卻從來沒有任何
    對帳知道它有沒有效。對最近 last_n 筆撞擊：數同一條線在撞擊後 days 天內的新記寫筆數。
    回 [(display, 筆數, 已滿窗)]；已滿窗＝撞擊已超過 days 天（否則「0 筆」可能只是還沒到）。純函式。"""
    out = []
    for e in (ledger or [])[-last_n:]:
        lab, ts = (e.get("label") or "").strip(), e.get("ts") or 0
        if not lab or not ts:
            continue
        n = sum(1 for r in (recs or [])
                if (r.get("topicLabel") or "").strip() == lab and r.get("_ts")
                and ts < r["_ts"] <= min(now_ts, ts + days * 86400))
        out.append((e.get("display") or lab, n, (now_ts - ts) > days * 86400))
    return out


def used_map(ledger):
    m = {}
    for e in (ledger or []):
        lab, ts = e.get("label"), e.get("ts") or 0
        if lab and ts > m.get(lab, 0):
            m[lab] = ts
    return m


def audit_text(state, cfg, cands, allow, spans, why="", diag=None, ready=None, aftermath_rows=None):
    """🌐 §1.95 `/worldline` 對帳。**第一句就要講白名單**——嚴格白名單下的預設失敗模式是「永遠沉默」，
    使用者若不知道要授權，會以為壞掉了。

    🌐 §2.01 使用者：「worldline 的描述需要簡化簡單清楚點」。改法是**砍掉他不需要決定的東西**，
    不是把同樣的內容換句話說：拿掉回返次數／天數小數／「不會被送出去只用來比對」的引文（那筆本來就不外送，
    列出來只會讓人以為它會外送）／內部欄位名。留下的四行各自對應他真的會做的一個決定：
    誰可以被查（要不要改授權）→ 下一個查誰 → 會送出去的那一行字（隱私）→ 什麼時候（等或直接催）。"""
    led = getattr(state, "worldline_ledger", None) or []
    out = ["🌐 外面的世界"]
    if not allow:
        out.append("・我還不能查任何東西——你要先說哪個標籤可以：`/worldline allow <標籤名>`")
        out.append("・標籤名就是你記寫裡的那個名字，一字不差")
        return "\n".join(out)
    # 每個標籤各自用「」括起來：標籤本身就可能含頓號（實測截圖「已授權 人工智慧意識、混沌與湧現」
    # 看不出是**兩個**標籤還是**一個**含頓號的標籤——而這兩種情況要做的事完全不同）。
    out.append("・可以查的：" + "、".join(f"「{x}」" for x in sorted(allow)) + "（其他的一個字都不會送出去）")
    if cands:
        c = cands[0]
        out.append(f"・下一個查：「{c['label']}」")
        out.append(f"・只有這行字會離開這台機器：「{build_query(c['label'])}」")
    # 「可撞的 0 條」要說得出**每條各自卡在哪一關**（截圖 10:08：只有一個 0，無從排查）
    for lab, reason in (diag or []):
        out.append(f"・「{lab}」：{reason}")
    if ready:
        out.append("・現在查得動的是：" + "、".join(f"「{lab}」" for lab, _n in ready)
                   + "（想換就 `/worldline allow <名字>`，照抄「」裡面的）")
    if why:
        out.append(f"・什麼時候：{why}")
    out.append("・想現在就看：打 `/worldline now`（沒有近期候選時，可回看已授權的舊線；仍有額度與去重限制）")
    probe = getattr(state, "worldline_probe", "") or ""
    if probe.startswith("失敗"):                            # 只有真的出過錯才佔一行（成功的細節他不需要）
        out.append(f"・上次出錯：{probe[:120]}")
    if led:
        # 🌐 §2.16 撞後對帳：這條 lane 的存在理由是「刺激記寫」，之前完全看不出有沒有效
        _am = {d: (n2, full) for d, n2, full in (aftermath_rows or [])}
        parts = []
        for e in led[-3:]:
            name = e.get("display") or e.get("label") or ""
            if name in _am:
                n2, full = _am[name]
                parts.append(f"「{e.get('label') or name}」（之後 3 天內{'寫了 ' + str(n2) + ' 筆' if n2 else ('沒有新記寫' if full else '還在看')}）")
            else:
                parts.append(f"「{e.get('label') or name}」")
        out.append("・查過的：" + "、".join(parts))
    n = int(getattr(state, "worldline_search_n", 0) or 0)
    if n:
        _cap = int(getattr(cfg, "worldline_max_searches", 40) or 40)
        out.append(f"・這個月已經查 {n} 次（月額度 {_cap}）" if getattr(cfg, "worldline_monthly_budget", False)
                   else f"・已經真的查過 {n} 次（這筆費用不在 /cost 裡）")
    return "\n".join(out)
