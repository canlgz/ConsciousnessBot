"""🔮 §1.90 記寫預想（FORESIGHT）：一條會被真實記寫裁決的假設，而且中／不中都要回頭說。

使用者的需求（原話）：「讓bot主動去預想一些可能性，這對我記寫有幫助。」——這是 bot **自己指認出來的
能力缺口**：截圖 10:45 它說「我還不會主動去預想一些可能性，像『如果你接下來這樣做，會不會跟之前的某件事
有關係？』」，使用者接著說「我幫你達成」。

稽核實測確認：全 repo 的連結／回顧機制**都是回顧型**——💡 association 的兩個端點都是已經寫過的記寫
（emergence event 欄位零個未來/預測欄位）、🫧 volition 的意圖是「我想搞懂你某條線」、🌀 experience 只描述
已走過的軌跡、🌾 habits 判的是「今天還沒出現」的缺席。所以 bot 那句「我還不會」是誠實的。

**這個模組補的就是那一維：時間方向。**
命題形式＝使用者要的那一維：**可能的未來動作 →（連回）一筆真實過去記寫**——
「你最近還在動的是 A；A 再往前走一步的話，我猜停在『{B 的真實字句}』那裡的 B 會再回來一次。」

對記寫的三個具體幫助（不是文青話）：
  ① **被看見**：指名一條他可能已經忘了停在哪裡的線，並逐字複述那一筆停住的原文；
  ② **被連起來**：這兩條線是 embedding 累積出來的真實橋（support≥2＝跨時間沉澱，且 §1.81 漣漪只加
     strength 不計 support ⇒ 聊天無法獨力把無真實支撐的橋推上來），不是 LLM 想像；
  ③ **被推進**：給出一個可以動筆的切入點，而且 bot 會**自己回頭驗**——不是拋一個空泛問題就消失。

設計上最重要的三個決定：
- **命題與裁決必須對得上**：命題是「B 會再回來」，裁決就是「B 有沒有再出現一筆」——零參數、不需要
  embedding、不需要校門檻、使用者一看就懂中或沒中。（record 級 cosine 語意裁決是好東西，但門檻只在
  合成向量上測過，留待階段 2 當加值。）
- **說出口成功之後才落帳**（told_ts 才設）⇒ 沒說出口的假設永不裁決 ⇒ bot 不可能事後宣稱「我早就猜到」。
- **hit 不准邀功**：bot 講過之後使用者才去寫，本來就是這條 lane 的價值（那就是推進），但那代表「中了」
  **不等於猜得準**。payoff 是把停住的那一筆與新的那一筆**逐字並排**給他看。

本模組純函式、無 I/O、無 LLM、可離線單測；**絕不 import monitor**（避免循環 import）——需要用到的守門
（§1.36 幻覺、§1.84 記寫宣稱）由 monitor 注入為 callable。
"""

import re

_QUOTE_RE = re.compile(r"[「『]([^」』]{1,120})[」』]")
_NORM_RE = re.compile(r"[^0-9A-Za-z一-鿿]+")

# 🔮 §1.90 引號外的「過去指涉」——規則塊已明令「所有過去內容都必須放進「」裡逐字」，所以引號外出現這類
# 說法＝在講一件沒有逐字根據的往事。**這是對我們自己輸出的格式閘，不是對使用者語意的分類器**，因此不違反
# 「動詞表窮舉」禁令；誤判的代價只是退回程式模板（不是沉默），所以偏嚴無妨。
# 為什麼非有不可（規格實測）：「「B」那條線停在你上次提到的那場家庭聚餐之後就安靜了。」這句
# **裸 §1.36 判 False＝完全放行**（「你」後面接的是「上次」，不在 _RECALL_ATTR_RE 第二式的可選詞裡），
# 而那正是 bot 最典型的幻覺形態之一。
DEICTIC = re.compile(r"上次|上禮拜|上週|前陣子|那次|那天|那件事|那場|你提到|妳提到|你說|妳說|你寫|妳寫|之前你|之前妳|那筆")


def _norm(s):
    return _NORM_RE.sub("", s or "")


def _clip(s, n):
    return " ".join((s or "").split())[:n]


def line_spans(records, now_ts=None):
    """🔮 §1.90 每條線（topicLabel）最後一筆的時間與筆數：{label: {"last_ts": float, "n": int}}。
    **strip 後完全相等**分組——絕不寬鬆比對（habits.py:296 的教訓：「閱讀」會吃到「閱讀｜讀誦經書」）。
    ts 解析由呼叫端注入 parse_ts；解析不出／無 topicLabel 的筆跳過；全部解析不出 → {}（fail-closed）。"""
    out = {}
    for r in (records or []):
        lab = (r.get("topicLabel") or "").strip()
        ts = r.get("_ts")                                  # 呼叫端先用 analyzer.parse_ts 填好（本模組不碰 I/O）
        if not lab or not ts or (now_ts is not None and ts > now_ts):
            continue
        cur = out.setdefault(lab, {"last_ts": 0.0, "n": 0})
        cur["n"] += 1
        if ts > cur["last_ts"]:
            cur["last_ts"] = ts
    return out


def pick_candidates(bridges, spans, now_ts, *, min_support=2, min_cos=0.30,
                    active_days=7.0, dormant_min_days=5.0, dormant_max_days=60.0,
                    blocked=()):
    """🔮 §1.90 從**已經累積好**的跨主題橋裡挑「一端還在寫、另一端停住」的線。

    關鍵：階段 1 **不需要任何新的重運算**——`state.associations.bridges` 的每條橋已經帶
    a/b/cos/support/emerged/anchor_a/anchor_b，而 anchor_*.text 是 association._nearest_anchor 從
    topic_centroids（依 topicLabel **精確**分群）的真實 member 取出的**真實記寫原文**。
    ⇒ 引文的接地由**建構時**保證，不必事後查證、也不必 load_embedding_records。

    ★ 對**完整集合** sorted 取首，程式中不得出現任何 [-N:]／[:N] 先截尾再挑（§1.83 前科：新補的候選
      被 [-6:] 吃掉、機制邏輯對卻從沒生效）。"""
    out = []
    for br in (bridges or []):
        if br.get("emerged"):
            continue                                       # 已湧現＝💡 的地盤，按定義不再是前瞻對象
        if int(br.get("support") or 0) < min_support:
            continue                                       # 真實跨時間沉澱（漣漪不計 support）
        if float(br.get("cos") or 0.0) < min_cos:
            continue
        a, b = (br.get("a") or "").strip(), (br.get("b") or "").strip()
        if not a or not b or a not in spans or b not in spans:
            continue                                       # 兩端都要有可解析的真實記寫
        ta, tb = spans[a]["last_ts"], spans[b]["last_ts"]
        fresh, stale = (a, b) if ta >= tb else (b, a)      # 較新的一端＝還在動；較舊＝停住
        fresh_days = (now_ts - spans[fresh]["last_ts"]) / 86400.0
        stale_days = (now_ts - spans[stale]["last_ts"]) / 86400.0
        if fresh_days < 0 or fresh_days > active_days:
            continue                                       # A 其實也不動了＝兩條都涼，不預想
        if not (dormant_min_days <= stale_days <= dormant_max_days):
            continue                                       # <min＝兩條都熱（那是 💡 的事）；>max＝已翻篇
        _anchor = br.get("anchor_a") if a == stale else br.get("anchor_b")   # 取**停住那一端**的真實錨點
        quote = _clip((_anchor or {}).get("text") or "", 40)
        if not quote:
            continue                                       # 沒有真實引文＝丟掉候選（fail-closed，絕不硬講）
        pair = "|".join(sorted((fresh, stale)))
        if pair in (blocked or ()):
            continue
        out.append({"pair": pair, "a": fresh, "b": stale, "quote": quote,
                    "support": int(br.get("support") or 0), "cos": round(float(br.get("cos") or 0.0), 3),
                    "dormant_days": round(stale_days, 1), "active_days": round(fresh_days, 1)})
    out.sort(key=lambda c: (c["support"], c["cos"], c["dormant_days"]), reverse=True)
    return out


def pair_blocked(pair, ledger, recent_insights, now_ts, window_s=14 * 86400):
    """🔮 §1.90 這一對最近講過／已經猜錯兩次／💡 剛講過 → 擋（反罐頭：同一對不准一直猜）。"""
    miss_n = 0
    for e in (ledger or []):
        if (e.get("key") or e.get("pair")) != pair:
            continue
        if (e.get("verdict") or "") == "miss":
            miss_n += 1
        ts = e.get("ts") or e.get("settled_ts") or 0
        if ts and 0 <= (now_ts - ts) <= window_s:
            return True
    if miss_n >= 2:
        return True                                        # 同一對猜錯兩次＝永久排除
    for e in (recent_insights or []):
        if (e.get("key") or e.get("pair")) == pair and 0 <= (now_ts - (e.get("ts") or 0)) <= 86400:
            return True                                    # 不與 💡 撞題
    return False


def corpus(records):
    """🔮 §1.90 自檢用語料：每筆 **text ＋ topicLabel** 正規化串接。
    ★ 必須含 topicLabel：monitor._recall_corpus 只收 r["text"]，規格實測「…「閱讀｜讀誦經書」…」在原語料
    被判成幻覺（True）、加了標籤後才是 False；topicLabel 是 drive_reader 白名單上的**真實欄位**，引用它
    從定義上就不是幻覺。"""
    return "".join(_norm((r.get("text") or "") + " " + (r.get("topicLabel") or "")) for r in (records or []))


def grounding_ok(text, cps, *, recall_hit=None, write_fix=None, wc_ground=None):
    """🔮 §1.90 出聲前的四道確定性接地自檢 → (ok, why)。**主動路徑上唯一的內容守門**。

    為什麼必須自己做：§1.36 記寫幻覺守門包在 _say 的**互動限定分支**內（`if not prefix and state is None:`），
    主動路徑完全不經過它 ⇒ 這條 lane 說錯話沒有任何事後守門會救。所以在送出前自己驗。

    ① 偽引用：所有「」『』內片段，正規化後必須是語料的 substring（引號內捏造＝最要命的幻覺）
    ② 歸因：把已驗證的引號片段換成〔〕後，對剩餘部分跑 §1.36 **同一支** recall_hit（不改它的本體）
    ③ 引號外過去指涉：DEICTIC 命中＝在講沒有逐字根據的往事（§1.36 對這款放行，見 DEICTIC 註解）
    ④ 記寫宣稱：§1.84 的 _write_claim_fix 判 changed＝這句在亂講「今天寫了什麼」
    """
    t = text or ""
    for q in _QUOTE_RE.findall(t):
        if _norm(q) and _norm(q) not in (cps or ""):
            return False, "偽引用"
    bare = _QUOTE_RE.sub("〔〕", t)
    if recall_hit is not None:
        try:
            if recall_hit(bare, cps or ""):
                return False, "歸因幻覺"
        except Exception:
            return False, "自檢例外"
    if DEICTIC.search(bare):
        return False, "引號外過去指涉"
    if write_fix is not None:
        try:
            _, changed = write_fix(t, wc_ground)
            if changed:
                return False, "記寫宣稱"
        except Exception:
            return False, "自檢例外"
    return True, ""


def verdict(hyp, records, now_ts):
    """🔮 §1.90 裁決——**100% 確定性、LLM 完全不參與「我猜中了沒有」**。
    只裁決真的說出口過的假設（told_ts）；沒說出口就不算數 ⇒ bot 不可能事後宣稱早就猜到。
    hit＝b 這條線在 born_ts 之後真的又出現一筆（取**最早**那筆當證據、逐字）；
    miss＝超過 TTL 仍沒有；其餘＝open。回 (verdict, evidence_text, evidence_id)。"""
    if not hyp or not hyp.get("told_ts"):
        return ("open", "", "")
    b = (hyp.get("b") or "").strip()
    born = max(hyp.get("born_ts") or 0, hyp.get("told_ts") or 0)
    deadline = born + float(hyp.get("ttl_s") or 0)
    best = None
    for r in (records or []):
        if (r.get("topicLabel") or "").strip() != b:
            continue                                       # 完全相等（絕不寬鬆比對）
        ts = r.get("_ts")
        if not ts or not (born < ts <= min(now_ts, deadline)):
            continue
        if best is None or ts < best[0]:
            best = (ts, r)
    if best is not None:
        r = best[1]
        return ("hit", _clip(r.get("text") or "", 40), str(r.get("id") or r.get("fileId") or ""))
    if (now_ts - born) > float(hyp.get("ttl_s") or 0):
        return ("miss", "", "")
    return ("open", "", "")


# 🔮 §1.90 程式退路文案（LLM 版沒過自檢時用；全部已實測通過四道自檢）。
# **寫給後人的坑（實測，別改回去）**：免責句不可寫「不是你寫過的事」——§1.36 第二式
# `(?:你|妳)…(?:寫|記|提)(?:了|到|的)?(span)` 會抓到、去停用詞後剩「過事」不在語料 ⇒ 免責句自己觸發幻覺
# 守門。要寫「不是已經成立的事」。模板一律不用「你＋寫/記」句式、不用「跟…有關」、不出現「今天」。
_PROPOSE_FORMS = (
    "「{b}」這條線停在「{quote}」之後就沒再回去過。「{a}」那邊還在動——這兩條在我這裡靠得很近。"
    "我猜「{b}」會再回來一次。只是我的猜測，不是已經成立的事。",
    "我心裡浮出一個猜測：「{b}」會再回來一次。理由是「{a}」還在動，而「{b}」停在「{quote}」那裡之後就安靜了。"
    "猜錯的話我再說。",
    "「{quote}」——「{b}」這條線停在這句之後就沒動了。「{a}」還在走，我猜這兩條會再碰一次。"
    "這是我的假設，不是已經成立的事。",
    "我想賭一把：「{b}」會再回來。停住的那一筆是「{quote}」，而「{a}」最近還在動。不準的話我會自己說。",
)


def propose_text(cand, variant=0):
    return _PROPOSE_FORMS[variant % len(_PROPOSE_FORMS)].format(
        a=cand.get("a") or "", b=cand.get("b") or "", quote=cand.get("quote") or "")


def settle_text(hyp, vd, evidence=""):
    b, quote = hyp.get("b") or "", hyp.get("quote") or ""
    if vd == "hit":
        return (f"我先前猜「{b}」會再出現，它真的回來了。當初停在「{quote}」，這次是「{evidence}」"
                "——兩筆擺在一起看，像是同一條線接上了。")
    return f"我先前猜「{b}」會再出現，結果沒有。我想錯了——那條線目前還停在「{quote}」。"


def push_ledger(ledger, hyp, vd, now_ts, keep=8):
    """🔮 §1.90 裁決完的假設進帳（保最後 keep 筆）。"""
    out = list(ledger or [])
    out.append({"key": hyp.get("pair") or "", "pair": hyp.get("pair") or "",
                "a": hyp.get("a") or "", "b": hyp.get("b") or "",
                "ts": hyp.get("born_ts") or 0, "verdict": vd, "settled_ts": now_ts})
    return out[-keep:]


def audit_text(state, cands, spans, blocked_why=""):
    """🔮 §1.90 對帳段（掛 /habits）——**這條 lane 的預設失敗模式是「從不觸發」，沉默必須可觀測**。"""
    led = getattr(state, "foresight_ledger", None) or []
    hit = sum(1 for e in led if (e.get("verdict") or "") == "hit")
    miss = sum(1 for e in led if (e.get("verdict") or "") == "miss")
    live = getattr(state, "foresight", None)
    parts = [f"🔮 記寫預想：線 {len(spans or {})} 條、可預想的候選 {len(cands or [])} 個"]
    if live:
        parts.append(f"在世假設「{live.get('b') or ''}」會再回來（還沒到期）")
    else:
        parts.append("目前沒有在世的假設")
    parts.append(f"過去猜中 {hit} 次、想錯 {miss} 次")
    if blocked_why:
        parts.append(f"這次沒說是因為：{blocked_why}")
    return "；".join(parts) + "。"
