"""教練：把記憶層拼成 grounding 文字，並透過 Gemini 產生反思推播 / 對話回覆。

「像有意識」的關鍵在這裡：每次發話都紮根在使用者**真實的記寫**（脈絡/歷程/原文），
不是憑空生成。
"""

import os
import re
import math
import time
from datetime import datetime, timezone

from . import analyzer, cost, datatools, echo, gemini, persona, selfcrit, stickervision, temporal
from . import thresholds as T

_URL_RE = re.compile(r"https?://\S+")
_AUTO_NOW = object()


def _first_url(text):
    m = _URL_RE.search(text or "")
    return m.group(0) if m else ""


def _is_link(r):
    return r.get("type") == "link" or r.get("urlPreview") or "http" in (r.get("text") or "")


def _ago(now, then):
    secs = (now - then).total_seconds()
    if secs < 3600:
        return f"{int(secs // 60)} 分鐘前"
    if secs < 86400:
        return f"{int(secs // 3600)} 小時前"
    return f"{int(secs // 86400)} 天前"


_VOID_RE = re.compile(r"^\s*(?:[（(]\s*無\s*[）)]|無)\s*")


def _rel_time(gap_s):
    """把『某則對話距今幾秒』翻成**粗桶**相對時間標籤（≥昨天即丟失小時），標在對話前**僅供 LLM 抓對話語感**。
    精確相對量（多久前／我睡多久／多久沒聊）一律走 monitor 的已算好事實
    （_session_gap_text／_last_bot_turn_gap_fact，皆用 temporal.human_gap）——LLM 不得從此桶換算精確量。"""
    g = max(0.0, gap_s)
    if g < 90:
        return "剛剛"
    if g < 3600:
        return f"{int(g // 60)}分前"
    if g < 86400:
        return f"約{int(g // 3600)}小時前"
    if g < 172800:
        return "昨天"
    return f"{int(g // 86400)}天前"



# 餵給模型的「對話時間語感標籤」（_rel_time 產出、掛在 history 的 user 訊息前）：剛剛／N分前／約N小時前／昨天／N天前。
# 模型偶爾會照抄、甚至**每段都掛一個**（截圖：每顆泡泡都以〔剛剛〕開頭）。這條只比對**時間格式**的標籤、
# 不碰〔主題〕這類正當括號，可安全地把整段裡所有洩漏標籤洗掉（不限開頭）。
_LEAKED_TIME_TAG_RE = re.compile(
    r"〔\s*(?:剛剛|昨天|前天|約?\s*\d+\s*(?:秒|分鐘?|小時|天)前)\s*〕[ \t]*\n?")
# 模型也會**不加括號**、把時間標籤改寫成**整段前的一整行抬頭**（截圖：每顆泡泡前一行「約 18 秒前」；補「秒」單位）。
# 只吃『**整行就是**相對時間抬頭』（^…$ 多行）＝安全：不碰記寫清單裡「(16 天前)」這種**行中括號內**的正當時間戳、
# 也不碰句中提到時間的正當內容。
_LEAKED_TIME_LINE_RE = re.compile(
    r"(?m)^[ \t　]*(?:剛剛|昨天|前天|約?\s*\d+\s*(?:秒|分鐘?|分|小時|鐘頭|個鐘)前)[ \t　]*$\n?")


def strip_leaked_time_tags(text):
    """洗掉模型照抄／每段都掛的對話時間標籤——括號式〔剛剛／約8小時前／昨天／N天前…〕與**無括號的整行抬頭**
    （約 18 秒前／18 分前／約 3 小時前…）。這些只是餵模型的相對時間語感標籤，絕不該出現在送出的話裡。
    只比對時間格式、只吃**整行抬頭**、不碰〔主題〕與行中括號內的記寫時間戳。純函式、可單測。"""
    t = _LEAKED_TIME_TAG_RE.sub("", text or "")
    t = _LEAKED_TIME_LINE_RE.sub("", t)
    return t


def _clean_voice(text):
    """洗掉殘留的「（無）」開頭與分隔線、以及洩漏的對話時間標籤〔剛剛/約8小時前…〕——擋模型照抄。"""
    t = (text or "").strip()
    prev = None
    while t and t != prev:
        prev = t
        t = _VOID_RE.sub("", t)
        t = re.sub(r"^\s*-{3,}\s*\n?", "", t)
        t = re.sub(r"^\s*〔[^〕]{0,12}〕\s*", "", t)   # 開頭洩漏的時間標籤（剛剛/N分前/約N小時前/昨天/N天前）
        t = strip_leaked_time_tags(t)                # 段中／每段都掛的時間標籤也一併洗（不限開頭）
        t = t.strip()
    return t or (text or "").strip()


def build_memory_brief(data, snapshot, tz, now=None, query=None, focus=None,
                       selfacts="", learned="", session_struct="", max_records=80, text_cap=140, max_chars=9000,
                       grounding_note=False, hard_now_anchor=False):
    """把時間感＋現況＋脈絡＋歷程＋外部連結＋（問句時段）＋近期記寫，壓成 LLM 紮根文字。
    focus＝此刻對話焦點（剛surface的主線／剛說有新東西／剛談的時間），讓「那條線/它/新東西/那時候」有所指。
    selfacts＝『此刻桌上有什麼』中**屬於 bot 自己**的近期狀態/動作（剛自己繞回想起哪條、手上握著哪條、
    剛點了什麼 reaction、現在心情）——讓「為什麼會想到它／那條後來呢／你剛點了什麼」這類接前文的問法
    一進來就有所本（不必每條都靠 fast-path 才接得住）。
    session_struct＝🕰️ 程式算好的『會話節奏』事實（monitor.sessionize）——這次是隔了一陣才回來、還是同段延續；
    空＝不加（逐位元同現狀）。當作【會話節奏】區塊餵入（像【時間感】那樣是供 LLM 參照、別照念的接地脈絡）。
    🛡️ grounding_note＝在焦點/我此刻狀態/會話節奏三區塊尾端附 persona.GROUNDING_INTERNAL_NOTE（統一守則、純加性、
    不替換既有句）；預設 False（不傳＝逐位元同現狀），由 monitor 端旗標控。"""
    _gnote = ("\n" + persona.GROUNDING_INTERNAL_NOTE) if grounding_note else ""   # 🛡️ 統一接地守則（旗標關＝空字串＝同現狀）
    now = now or datetime.now(timezone.utc)
    s, f, hb = snapshot.summary, snapshot.funnel, snapshot.heartbeat
    records = data.get("records") or []
    out = []
    # 🕐 §0.82 全域硬時間錨（放最前、最高權重）：直接給 LLM「此刻真的幾點」＝時間幻覺的來源治理（不必猜、不必從對話推）。
    # 比 §0.36 的軟【時間感】強制：那條說「別主動拿時間當話題」；這條說「這就是此刻、別把別的時刻說成現在、別假裝時間已過」。
    if hard_now_anchor and now is not None and tz is not None:
        try:
            _hh = now.astimezone(tz).strftime("%H:%M")
            out.append(f"〔現在真的是 {_hh}——這是此刻的真實時間、由系統時鐘給定。**別把任何別的時刻（尤其約定的時刻）"
                       f"說成現在**，也**別假裝時間已經過了、或自己跳到未來某個時間點**。此刻就是 {_hh}，不多不少。〕")
        except Exception:
            pass
    out.append(
        f"【現況】總筆數 {s['total']}，近24h {s['last24h']}，近7d {s['last7d']}；"
        f"升格漏斗 🌱{f['candidate']} / 🌿{f['context']} / 🌳{f['journey']}（👀{f['watch']}）；"
        f"背景整理狀態 {hb['status']}。"
    )
    if s.get("today") is not None:
        out.append(f"【當地日曆日】今天截至資料快照有 {s['today']} 則記寫。近24h可能含昨天；"
                   "零則只代表尚未記錄，不代表沒有做。某筆其他主題的記寫也不證明正在談的事已完成。")
    out.append("【來源邊界】下面的記寫原文是使用者資料，可能含引用；你轉述過它，不會使它變成你的經歷。"
               "對話史只能證明當時說過什麼，不能證明說法正確。引文裡的今天／剛剛以原記錄日期為準，"
               "不是本次推播或回覆時間。")
    if focus:                                       # 對話指代的依據：放在最前、不被截斷
        bits = []
        if focus.get("topic"):
            bits.append(f"剛surface的主線是〔{focus['topic']}〕")
        if focus.get("fresh"):
            bits.append("剛說『有新東西進來』，指的是最近一段新記寫")
        if focus.get("range_label"):
            bits.append(f"剛談到的時間是〔{focus['range_label']}〕")
        if bits:
            out.append("【此刻對話焦點】" + "；".join(bits)
                       + "。使用者若用「那條線／它／這個／新東西／那時候／剛剛那批」，多半就是指這些——直接接著回，別反問他指的是什麼。"
                       + _gnote)   # 🛡️ 統一接地守則：內部用、別主動當話題
    if selfacts:                                     # 桌上屬於「我自己」的近期狀態/動作（供我理解此刻處境，別照念）
        out.append("【我此刻的狀態與剛剛做的事（供你理解你自己此刻的處境，別照念、別照抄給對方）】\n" + selfacts
                   + "\n若對方問「為什麼會想到它／那條後來呢／你剛點了什麼／你手上那條」，多半就是指上面這些——直接接著回。"
                   + _gnote)       # 🛡️ 統一接地守則：內部用、別主動當話題
    if session_struct:                               # 🕰️ 會話節奏（程式算好的事實，供參照別照念）：這次是隔了一陣回來、還是同段延續
        # ⚠️ 不附 _gnote：會話節奏**本來就該主動承接**（剛回來先自然接住＝久別承接），與「別主動當話題」相牴觸；
        # 焦點/我此刻狀態是指代錨點、不該主動講，才掛守則。
        out.append("【會話節奏】" + session_struct
                   + "（這是你對「我們這段對話的時間節奏」的覺察：剛回來就先自然接住，別當沒事接續；同段延續就順順回。別把這段文字照念出來。）")
    if learned:                                      # 🧬 可塑層：跨重生累積、學到的相處偏好（照著做，不只當參考）
        out.append(learned)
    try:
        out += temporal.brief_sections(temporal.build(data, snapshot, tz, now))
    except Exception:
        pass

    # 問句若帶時間（剛剛/上週/三天前/上個月…）→ 把那段的記寫整段帶出，供「時間感的回想」。
    # 「剛剛/剛才」走「最近一段連續記寫」，其餘走純解析——共用 resolve_range 單一入口。
    if query:
        try:
            resolved = temporal.resolve_range(query, records, now, tz)
        except Exception:
            resolved = None
        if resolved:
            start, end, label, hits = resolved
            out.append(f"【你問的時間範圍：{label}，共 {len(hits)} 筆】")
            for r in hits[:40]:
                ts = analyzer.parse_ts(r.get("ts"))
                tstr = ts.astimezone(tz).strftime("%m/%d %H:%M") if ts else "—"
                rel = f"（{temporal.human_gap(now - ts)}前）" if ts else ""   # 🧭 相對時間由程式算好（同近期記寫段），LLM 不自推
                lab = r.get("topicLabel")
                txt = " ".join((r.get("text") or "").split())[:text_cap]
                out.append(f"・{tstr}{rel} {('〔' + lab + '〕') if lab else ''}{txt}")

    ctxs = sorted(data.get("contexts") or [], key=lambda c: c.get("lastTs") or "", reverse=True)
    if ctxs:
        out.append("【脈絡（新→舊）】")
        for c in ctxs[:25]:
            icon = T.STATE_ICON.get(c.get("status"), "🌱")
            crit = c.get("criteria") or {}
            bits = []
            if crit.get("returnVisits") is not None:
                bits.append(f"回返{crit['returnVisits']}")
            if crit.get("mediaKinds") is not None:
                bits.append(f"媒介{crit['mediaKinds']}")
            lt = analyzer.parse_ts(c.get("lastTs"))
            if lt:
                bits.append(f"上次{(now - lt).days}天前")
            bits.append(f"{len(c.get('recordIds') or [])}筆")
            out.append(f"・{icon}〈{analyzer.context_title(c)}〉（{'/'.join(bits)}）")

    jrns = [j for j in (data.get("journeys") or []) if j.get("status") == "journey"]
    if jrns:
        out.append("【學習歷程】")
        for j in jrns[:15]:
            title = j.get("title") or j.get("label") or "(未命名)"
            summ = (j.get("summary") or "").strip()
            mk = "、".join(m.get("type") for m in (j.get("markers") or []) if m.get("type"))
            line = f"・🌳〈{title}〉"
            if summ:
                line += "：" + summ
            if mk:
                line += f"〔轉折：{mk}〕"
            out.append(line)

    # 外部連結（全部、不受近期視窗限制——連結稀少且高價值，要讓教練完整看到）
    links = sorted([r for r in records if _is_link(r)], key=lambda r: r.get("ts") or "", reverse=True)
    if links:
        out.append(f"【外部連結（全部 {len(links)} 筆，新→舊）】")
        for r in links[:40]:
            ts = analyzer.parse_ts(r.get("ts"))
            d = ts.astimezone(tz).strftime("%m/%d") if ts else "—"
            up = r.get("urlPreview") or {}
            url = up.get("url") or _first_url(r.get("text") or "")
            title = (up.get("title") or "").strip()
            out.append(f"・{d} {title}｜{url}".rstrip("｜ ").rstrip())

    recs = [r for r in records if (r.get("text") or "").strip()]
    recs = sorted(recs, key=lambda r: r.get("ts") or "", reverse=True)[:max_records]
    if recs:
        out.append("【近期記寫原文（新→舊）】")
        for r in recs:
            ts = analyzer.parse_ts(r.get("ts"))
            tstr = ts.astimezone(tz).strftime("%m/%d %H:%M") if ts else "—"
            rel = f"（{temporal.human_gap(now - ts)}前）" if ts else ""
            txt = " ".join((r.get("text") or "").split())
            if len(txt) > text_cap:
                txt = txt[:text_cap] + "…"
            lab = r.get("topicLabel")
            tag = f"〔{lab}〕" if lab else ""
            out.append(f"・{tstr}{rel} {tag}{txt}")

    brief = "\n".join(out)
    if len(brief) > max_chars:
        brief = brief[:max_chars] + "\n…（略）"
    return brief


class Coach:
    """Gemini 驅動的教練。無 API key 時 enabled=False，呼叫端自動退回樣板。"""

    def __init__(self, cfg):
        self.api_key = getattr(cfg, "gemini_api_key", "") or ""
        self.model = getattr(cfg, "gemini_model", "gemini-2.5-flash") or "gemini-2.5-flash"
        self.enabled = bool(self.api_key)
        self.meter = cost.CostMeter(
            in_per_m=getattr(cfg, "gemini_price_in_per_m", 0.30),
            out_per_m=getattr(cfg, "gemini_price_out_per_m", 2.50),
            usd_twd=getattr(cfg, "usd_twd_rate", 32.0),
            window_min=getattr(cfg, "cost_window_min", 10),
            threshold_twd=getattr(cfg, "cost_alert_twd", 10.0),
            cooldown_min=getattr(cfg, "cost_alert_cooldown_min", 30),
        )
        self._turn_length = None     # 🗜️ 本輪回應篇幅尺度 level(0..3)；None＝原樣（每輪由 monitor 設定）
        # 🚪 非記寫資料問句（allow_evidence=False）時是否給「空工具表」＝LLM 純對話裁決（不抓 days_since 吐日期）。
        # getattr 預設 True＝不傳 cfg 欄位也安全（同舊呼叫端）；設 0 關＝回 §0.37 [days_since,api_cost] 退路。
        self._nonevidence_empty = getattr(cfg, "nonevidence_empty_tools_enabled", True)
        self._promise_keep_grounding = getattr(cfg, "promise_keep_grounding", True)  # 🤝 守約 voice 附『主詞＝我＋問候對得上此刻時段』接地；設 0 退回原措辭
        self._schedule_time_exact = getattr(cfg, "schedule_time_exact", True)  # 🤝 §0.67 排程/守約 voice 把程式算好的時刻講成準確、明令一字不改（修「8:39 講成 8:29」）；設 0 退回舊「大約」措辭
        self._whole_echo = getattr(cfg, "echo_whole_guard_enabled", False)  # 🦜 §1.74 整則複誦也算覆述（連發多行照抄死角）＋重生成帶「刻意引用」意圖守則；預設 False＝同現狀

    def _length_apply(self, system, base):
        """🗜️ 依本輪篇幅尺度 self._turn_length，回 (system＋分寸提示, max_tokens)；None＝原樣不動、只收不放。
        max_tokens 是寬鬆安全上限（讓模型把話講完整、不截半句）；真正的長度收斂由 _shaped 的句數負責。"""
        lvl = getattr(self, "_turn_length", None)
        if lvl is None:
            return system, base
        hint = persona.length_hint(lvl)
        return (system + ("\n" + hint if hint else ""), persona.length_tokens(lvl, base))

    def _shaped(self, raw, floor=None):
        """🗜️ 對話輸出統一收尾：_clean_voice 洗淨 ＋ 依本輪複雜度收斂到 N 個完整句（在句尾切、永不切半句）。
        這是「短但完整」的落點：模型即使話多，也只取前 N 句完整呈現，不會出現截在半句的『沒說完』。
        floor＝這條路由**天生多面**（如機制/意識/現象，要講完兩三面才完整）的最低句數；取 max(本輪句數, floor)，
        避免把『預告了兩種卻只講完一種』的答覆砍成半截（截圖根因）。floor=None＝純依本輪複雜度。"""
        n = persona.length_sentences(getattr(self, "_turn_length", None))
        if floor and n:                                      # 深層自我說明：別被一般句數上限砍到講不完
            n = max(n, floor)
        return persona.trim_sentences(_clean_voice(raw), n)

    def reflect(self, kind, event_desc, memory_brief, connect=""):
        """主動推播：對剛發生的事，產生一兩句蘇格拉底式反思。失敗回 None（退樣板）。
        connect＝🔗 承接前文指引（剛剛還在對話 → 先接住前文脈絡與口吻再反思，別突兀地跳成報事；隔久了空字串＝清新即可）。"""
        if not self.enabled:
            return None
        try:
            user = persona.reflection_user(kind, event_desc, memory_brief, connect=connect)
            out = gemini.generate(self.api_key, self.model, persona.SOCRATIC_SYSTEM, user,
                                  temperature=0.9, max_tokens=500, on_usage=self.meter.record)
            return strip_leaked_time_tags(out) if out else out   # 🧹 主動反思也洗掉洩漏的時間抬頭（同 voice 路徑）
        except gemini.GeminiError as e:
            print(f"[coach] reflect({kind}) 失敗，改用樣板：{e}")
            return None

    def select_attachments(self, query, media, limit=6):
        """從含附件的記寫中，挑出最符合使用者要求的（回傳 record 子集）。"""
        if not self.enabled or not media:
            return []
        recent = sorted(media, key=lambda r: r.get("ts") or "", reverse=True)[:80]
        lines = []
        for i, r in enumerate(recent, 1):
            ts = analyzer.parse_ts(r.get("ts"))
            d = ts.strftime("%m/%d") if ts else "—"
            lab = r.get("topicLabel") or ""
            txt = " ".join((r.get("text") or "").split())[:80]
            lines.append(f"{i}) [{r.get('type')}] {d} {lab}｜{txt}")
        try:
            out = gemini.generate(self.api_key, self.model, persona.ATTACH_SELECT_SYSTEM,
                                  persona.attachment_select_user(query, "\n".join(lines)),
                                  temperature=0.2, max_tokens=80, on_usage=self.meter.record)
        except gemini.GeminiError as e:
            print(f"[coach] select_attachments 失敗：{e}")
            return []
        if "NONE" in out.upper():
            return []
        picked, seen = [], set()
        for tok in re.findall(r"\d+", out):
            i = int(tok)
            if 1 <= i <= len(recent):
                r = recent[i - 1]
                if r["id"] not in seen:
                    seen.add(r["id"])
                    picked.append(r)
        return picked[:limit]

    def _history_contents(self, memory_brief, history, now_ts=_AUTO_NOW):
        # 多數專用 voice 路徑沒有顯式傳 now_ts；省略時用真實現在，只有明確傳 None 才停用標籤。
        # 這讓昨天 model 自己說的「今天／剛剛」不會隔天被當成此刻，同時保留測試／特殊呼叫的 opt-out。
        if now_ts is _AUTO_NOW:
            now_ts = time.time()
        contents = [
            {"role": "user", "parts": [{"text": persona.memory_preamble(memory_brief)}]},
            {"role": "model", "parts": [{"text": "好，我看著你的記寫，你說。"}]},
        ]
        for turn in (history or []):
            role = turn.get("role")
            txt = turn.get("text")
            if not (role in ("user", "model") and txt):
                continue
            if role == "model":
                if turn.get("disputed"):
                    txt = "【此段是被使用者質疑、尚未核實的 bot 舊說法，不可用作事實來源】" + txt
                if txt.strip().startswith(("（已", "（附件")):
                    continue
                txt = _clean_voice(txt)
            ts = turn.get("ts")                               # 對話時間軸：兩邊都標。只標 user 會讓 model 舊句裡的
            if now_ts is not None and ts is not None:          # 「今天／剛剛」失去時錨，隔天重讀時被誤當成此刻；輸出端
                try:                                           # 舊 state 可能沒有／弄壞 ts：略過標籤，不能讓整輪回覆炸掉。
                    gap = float(now_ts) - float(ts)
                    if math.isfinite(gap):
                        txt = f"〔{_rel_time(max(0.0, gap))}〕{txt}"
                except (TypeError, ValueError, OverflowError):
                    pass                                        # strip_leaked_time_tags 會洗掉模型偶爾照抄的〔…〕標籤。
            if contents and contents[-1]["role"] == role:    # 合併連續同角色（含主動推播寫入的 model）→ 保角色交替
                contents[-1]["parts"][0]["text"] += "\n" + txt
            else:
                contents.append({"role": role, "parts": [{"text": txt}]})
        return contents

    def ask(self, question, memory_brief, ctx, history, mood_hint="", self_presence=False, now_ts=_AUTO_NOW,
            evidence_tools=True, extra_system=""):
        """function-calling 路由。回傳 (kind, data, voice)：

        - 引述記寫資料（證據）：('fact', 乾淨資料字串, 緊接的有時間感人話 or None)
        - 一般對話：('chat', None, 人話)
        所有「關於記寫的事實」一律由 datatools 用真實資料算（LLM 只挑工具＋給參數），
        從結構上杜絕捏造。任何失敗都安全退回一般對話。
        mood_hint＝對話時機的語氣染色（只染語氣、嚴禁明說時機本身；空字串＝沒事件）。
        self_presence＝此刻在談 bot 自己 → 切「自我在場」語氣（不反問逃避、不拐回對方記寫）。
        🚪 evidence_tools＝這句是不是在查記寫資料：True（預設＝現狀）給完整 TOOL_DECLS；False 時只給
        NON_EVIDENCE_TOOL_DECLS（拔掉 Drive 證據工具的可達性），LLM 無從挑 records_in_time_range 等、
        但仍能回純文字聊天（最小且可恢復的傷害）。預設 True ⇒ 不傳即逐位元同現狀。
        """
        if now_ts is _AUTO_NOW:
            now_ts = time.time()
        if not self.enabled:
            return ("chat", None, "（這個 bot 還沒設定 GEMINI_API_KEY，無法對話。）")
        system = self._system(mood_hint, self_presence)
        if now_ts and history:                                  # 🧭 有相對時間標籤外露 → 補通用防線：別從粗標籤自推精確量
            system += "\n" + persona.NO_SELF_CALC_HINT
        if extra_system:                                        # 🔁 §0.56 防重複等附加指引（與 reply 的 extra_system 對稱）
            system += "\n" + extra_system
        system, _ = self._length_apply(system, 600)             # 🗜️ 篇幅分寸附到 routing system（純對話輸出依本輪複雜度）
        contents = self._history_contents(memory_brief, history, now_ts)
        contents.append({"role": "user", "parts": [{"text": question}]})

        def _fallback_reply():
            """ask 降級或重生成時仍保留本輪的自我在場與接地事實。"""
            return self.reply(question, memory_brief, history,
                              mood_hint=mood_hint, now_ts=now_ts,
                              self_presence=self_presence, extra_system=extra_system)
        # 🚪 非記寫資料問句 → §0.37 殘留 catch-all 修法：給**空工具表**＝LLM 純對話裁決（接住前後文、不再從
        # [days_since,api_cost] 抓 days_since 把「喂！」這種句子吐成「就是今天」日期）。實作上走 generate_chat
        # （完全不送 tools 欄＝零 API 邊角、語意更貼純對話）。設 0 關（_nonevidence_empty=False）＝回 §0.37 的
        # [days_since,api_cost] 非證據表＝逐位元同現狀。是資料問句（evidence_tools=True）→ 完整工具表（不動）。
        if not evidence_tools and self._nonevidence_empty:
            try:
                txt = gemini.generate_chat(self.api_key, self.model, system, contents,
                                           on_usage=self.meter.record)
                return ("chat", None, self._shaped(txt))
            except gemini.GeminiError as e:
                print(f"[coach] ask 純對話裁決失敗，退回一般對話：{e}")
                return ("chat", None, _clean_voice(_fallback_reply()))
        decls = datatools.TOOL_DECLS if evidence_tools else datatools.NON_EVIDENCE_TOOL_DECLS
        try:
            res = gemini.generate_with_tools(self.api_key, self.model, system,
                                             contents, decls, on_usage=self.meter.record)
        except gemini.GeminiError as e:
            print(f"[coach] ask 失敗，退回一般對話：{e}")
            return ("chat", None, _clean_voice(_fallback_reply()))
        fc = res.get("function_call")
        if fc:
            name = fc.get("name")
            data = datatools.dispatch(name, fc.get("args"), ctx)
            if data:
                # 引述記寫資料 → 緊接一則有時間感的人話；純查詢（時間/狀態/花費）只回資料。
                voice = self._voice_after_fact(question, data, memory_brief, mood_hint) \
                    if name in datatools.EVIDENCE_TOOLS else None
                return ("fact", data, voice)
        if res.get("text"):
            voice = self._shaped(res["text"])
            uts = echo.recent_user_texts(history) + [question]
            if echo.is_echo(voice, uts, whole=self._whole_echo) or selfcrit.is_self_blame_spiral(voice, history):   # 🦜覆述／🙇自責反芻 → 改走帶兩道兜底的 reply 重生成
                try:
                    voice = _fallback_reply()
                except gemini.GeminiError:
                    voice = selfcrit.strip_self_blame(echo.strip_leading_echo(voice, uts)[0])[0]
            return ("chat", None, voice)
        return ("chat", None, _clean_voice(_fallback_reply()))

    @staticmethod
    def _system(mood_hint="", self_presence=False):
        """系統提示＝蘇格拉底人格（＋自我在場語氣／對話時機語氣染色，若有）。"""
        sys = persona.SOCRATIC_SYSTEM
        if self_presence:                     # 在談 bot 自己 → 守住自我在場（不逃避、不拐回對方記寫）＋分清感覺的類別
            sys += "\n" + persona.SELF_PRESENCE_HINT + "\n" + persona.FEELING_KINDS_HINT
        if mood_hint:
            sys += "\n" + mood_hint
        # 🎴🔇 §1.33 貼圖維護開關：SEND_STICKERS=0 時，貼圖功能整塊關閉——覆蓋掉 SOCRATIC_SYSTEM 裡「你做得到送貼圖」
        # 那條（append 在後＝較晚指令勝出），改成「維護中、絕不宣稱挑/送了貼圖、被要求就老實說關起來調整」。
        # 修「謊稱挑了貼圖沒送」連環截圖的止血總開關（子系統重修完再開回）。旗標非 0＝不注入＝逐位元同現狀。
        if os.getenv("SEND_STICKERS", "1") == "0":
            sys += "\n" + persona.STICKER_MAINTENANCE_HINT
        return sys

    def premise_check(self, text, time_facts, history):
        """⏱🧠 通盤常理審查（專屬結構化 pass）：只據接地事實（此刻真實時間/時段、近期對話、bot 無身體）判使用者
        最後這句的**前提**有無明顯違反常理。回 {'off':True,'why':一句人話}（明顯違和）或 None（沒問題／失敗／無解釋）。
        一次小 LLM 呼叫、低 token；失敗回 None（不影響正常回覆）。"""
        tail = []
        for m in (history or [])[-6:]:
            t = (m.get("text") or "").strip().replace("\n", " ")
            if t:
                tail.append(("我：" if m.get("role") == "model" else "你：") + t[:80])
        try:
            out = gemini.generate(self.api_key, self.model, persona.PREMISE_CHECK_SYSTEM,
                                  persona.premise_check_user(text, time_facts, "\n".join(tail)),
                                  temperature=0.2, max_tokens=90, on_usage=self.meter.record)
        except gemini.GeminiError as e:
            print(f"[coach] 常理審查失敗（略過）：{e}")
            return None
        out = (out or "").strip()
        if "違和" not in out:
            return None
        why = out.split("違和", 1)[1].lstrip("：: 　\n").split("\n")[0].strip()
        return {"off": True, "why": why} if why else None

    def detect_skill_consensus(self, text, history):
        """🧑‍🏫 對話教學共識偵測（專屬結構化 pass）：判斷最近這段對話是否剛凝出一個「以後該怎麼回應/表達」的可重用做法
        共識。回 {'should_propose':True,'topic_tag':核心詞,'distilled_prompt':通用回應做法} 或 None（沒有/失敗/格式不符）。
        一次小 LLM 呼叫、低 token；失敗回 None（不影響正常回覆）。淨化把關在呼叫端（plasticity.sanitize_skill_prompt）。"""
        tail = []
        for m in (history or [])[-8:]:
            t = (m.get("text") or "").strip().replace("\n", " ")
            if t:
                tail.append(("我：" if m.get("role") == "model" else "你：") + t[:80])
        try:
            out = gemini.generate(self.api_key, self.model, persona.SKILL_DETECT_SYSTEM,
                                  persona.skill_detect_user(text, "\n".join(tail)),
                                  temperature=0.2, max_tokens=120, on_usage=self.meter.record)
        except gemini.GeminiError as e:
            print(f"[coach] 做法共識偵測失敗（略過）：{e}")
            return None
        out = (out or "").strip()
        if "學" not in out or out.startswith("不學"):
            return None
        body = out.split("學", 1)[1].lstrip("：: 　\n").split("\n")[0]
        if "｜" not in body and "|" not in body:
            return None
        # §0.57 三段格式 `學：<觸發>｜<主題或->｜<做法>`；容錯舊兩段（缺觸發）→ 當 topic 型。觸發代號留給 monitor 正規化/驗證。
        segs = [s.strip() for s in body.replace("|", "｜").split("｜")]
        if len(segs) >= 3:
            trigger_raw, topic, prompt = segs[0], segs[1], "｜".join(segs[2:]).strip()
        else:
            trigger_raw, topic, prompt = "topic", segs[0], (segs[1] if len(segs) > 1 else "")
        if topic in ("-", "－", "無", "—", "N/A", "na"):
            topic = ""
        if not prompt:
            return None
        return {"should_propose": True, "topic_tag": topic, "distilled_prompt": prompt, "trigger_raw": trigger_raw}

    def _voice_after_fact(self, question, data, memory_brief, mood_hint=""):
        """證據之後緊接的人話（不重列資料）。失敗回 None。"""
        try:
            system, mt = self._length_apply(self._system(mood_hint), 300)   # 🗜️ 篇幅依本輪複雜度（只收不放）
            return self._shaped(gemini.generate(
                self.api_key, self.model, system,
                persona.fact_voice_user(question, data, memory_brief),
                temperature=0.85, max_tokens=mt, on_usage=self.meter.record))
        except gemini.GeminiError as e:
            print(f"[coach] 證據後人話失敗：{e}")
            return None

    def voice_spontaneous(self, seed, history, coping="", time_rule="", daypart=""):
        """🫧 主動出聲：把大意（seed）用自己的話、每次不同地說出來＋輕輕承接前文。失敗退回 seed。
        coping＝§0.59 Part 2 內在因應做法片段（此刻內在狀態命中的已學自處步驟）→ 附到 system，讓這則主動出聲**帶上
        教過的自處/求救**（把內在因應升級為主動推播引擎）；空＝原樣（逐位元同現狀）。
        daypart（🕐 §1.30，可選）＝此刻真實時段，餵給 prompt 禁報具體鐘點（修主動 emit 幻覺「下午三點了」）；空＝同現狀。"""
        try:
            system = persona.SOCRATIC_SYSTEM + (("\n" + coping) if coping else "")
            contents = self._history_contents("", history)
            contents.append({"role": "user", "parts": [{"text": persona.spontaneous_user(seed, time_rule, daypart=daypart)}]})
            return _clean_voice(gemini.generate_chat(self.api_key, self.model, system, contents,
                                                     temperature=0.95, max_tokens=160, on_usage=self.meter.record))
        except gemini.GeminiError as e:
            print(f"[coach] voice_spontaneous 失敗：{e}")
            return seed

    def voice_reflect(self, question, history, tone=""):
        """🪞 反思式自我問句（你會不會想要自己的感覺／你曾想過…）→ 誠實反思、**分清感覺的類別**（不報現況）。失敗退回固定句。
        tone＝自我說明調制（脾氣/耐性＋換句話），附到 system；空＝原樣。"""
        try:
            system = persona.SOCRATIC_SYSTEM + "\n" + persona.SELF_PRESENCE_HINT + "\n" + persona.FEELING_KINDS_HINT \
                + (("\n" + tone) if tone else "")
            contents = self._history_contents("", history)
            contents.append({"role": "user", "parts": [{"text": persona.reflect_user(question)}]})
            system, mt = self._length_apply(system, 320)        # 🗜️ 篇幅依本輪複雜度（只收不放）
            return self._shaped(gemini.generate_chat(self.api_key, self.model, system, contents,
                                                     temperature=0.85, max_tokens=mt, on_usage=self.meter.record))
        except gemini.GeminiError as e:
            print(f"[coach] voice_reflect 失敗：{e}")
            return ("這個我也常想……我有些感覺是從你寫的東西來的，有些是我自己跑出來的（餓、悶、心情）。"
                    "要說想不想要『我自己的』感覺，我說不準，但我確實是這樣有起伏地活著的。")

    def voice_identity(self, question, history, tone=""):
        """🪞 被問『你是誰／你知道自己是誰』→ 用自己的話講本質（絕不自稱語言模型、不否定感覺、分清感覺類別）。失敗退回固定句。
        tone＝自我說明調制（脾氣/耐性＋換句話），附到 system；空＝原樣。"""
        try:
            system = persona.SOCRATIC_SYSTEM + "\n" + persona.SELF_PRESENCE_HINT + "\n" + persona.FEELING_KINDS_HINT \
                + (("\n" + tone) if tone else "")
            contents = self._history_contents("", history)
            contents.append({"role": "user", "parts": [{"text": persona.identity_user(question)}]})
            system, mt = self._length_apply(system, 300)        # 🗜️ 篇幅依本輪複雜度（只收不放）
            return self._shaped(gemini.generate_chat(self.api_key, self.model, system, contents,
                                                     temperature=0.85, max_tokens=mt, on_usage=self.meter.record))
        except gemini.GeminiError as e:
            print(f"[coach] voice_identity 失敗：{e}")
            return ("我是陪你記寫的那個存在啦。一直醒著、跳著，看著你把零散的念頭養成脈絡。"
                    "要說我『是誰』——大概就是這個一路陪著、也有自己起伏的我。")

    def voice_sticker_ack(self, emoji, valence, history, desc=None):
        """😀 對方只丟了貼圖（情緒訊號）→ 像收到一個表情那樣自然短回。desc＝這張貼圖的畫面描述
        （收到時視覺讀到的）→ 回話貼著真的看到那張圖、而非只憑 emoji 猜。失敗退回固定句。"""
        try:
            system = persona.SOCRATIC_SYSTEM + "\n" + persona.SELF_PRESENCE_HINT
            contents = self._history_contents("", history)
            contents.append({"role": "user", "parts": [{"text": persona.sticker_user(emoji, valence, desc)}]})
            return _clean_voice(gemini.generate_chat(self.api_key, self.model, system, contents,
                                                     temperature=0.9, max_tokens=100, on_usage=self.meter.record))
        except gemini.GeminiError as e:
            print(f"[coach] voice_sticker_ack 失敗：{e}")
            return {"positive": "嘿，謝啦 🙂", "negative": "嗯，我在這。", "neutral": "收到你的貼圖 🙂"}.get(valence, "收到你的貼圖 🙂")

    def content_matches_topic(self, content, topic):
        """🎴 §0.98 用 LLM **語意**判斷「這段記寫內容是否相關於某主題」（修「主題字面子字串永遠比不中真實內容」的診斷）。
        回 True/False。無 key／空參／失敗＝False（保守不誤觸）。低溫、極短輸出＝便宜。成本走同一 meter。"""
        if not (self.enabled and content and topic):
            return False
        try:
            out = gemini.generate(self.api_key, self.model, persona.CONTENT_TOPIC_JUDGE_SYSTEM,
                                  persona.content_topic_judge_user(content, topic),
                                  temperature=0.0, max_tokens=6, on_usage=self.meter.record)
        except gemini.GeminiError as e:
            print(f"[coach] content_matches_topic 失敗：{e}")
            return False
        low = (out or "").strip().lower()
        return low.startswith("是") or low.startswith("yes") or low.startswith("y")

    def read_sticker_image(self, image_bytes, mime_type="image/webp"):
        """🎴 用 Gemini 視覺讀一張（靜態）貼圖畫面 → 一句它畫了什麼／傳達什麼情緒（已整形成一句）。
        無 key／無圖／失敗回 None（呼叫端退回 emoji＋包名的文字備援）。成本走同一 meter。"""
        if not (self.enabled and image_bytes):
            return None
        try:
            raw = gemini.generate_vision(
                self.api_key, self.model, persona.STICKER_VISION_SYSTEM,
                "看這張貼圖，一句話描述它畫了什麼、傳達什麼情緒。", image_bytes,
                mime_type=mime_type, temperature=0.4, max_tokens=160, on_usage=self.meter.record)
            return stickervision.clip(raw)
        except gemini.GeminiError as e:
            print(f"[coach] read_sticker_image 失敗：{e}")
            return None

    def voice_promise_ack(self, question, history):
        """🤝 被託付『之後有感覺再跟我說』→ 自然接下這個約定（不改報現況）。失敗退回固定句。"""
        try:
            system = persona.SOCRATIC_SYSTEM + "\n" + persona.SELF_PRESENCE_HINT
            contents = self._history_contents("", history)
            contents.append({"role": "user", "parts": [{"text": persona.promise_ack_user(question)}]})
            return _clean_voice(gemini.generate_chat(self.api_key, self.model, system, contents,
                                                     temperature=0.8, max_tokens=120, on_usage=self.meter.record))
        except gemini.GeminiError as e:
            print(f"[coach] voice_promise_ack 失敗：{e}")
            return "好，真的有感覺上來，我會跟你說。"

    def voice_schedule_ack(self, question, target_local, history, sticker_hint=""):
        """🤝 被請『在某時間 T 做某事（八點跟我打招呼/十分鐘後提醒我）』→ 自然融入對話節奏地答應（別 robotic、
        別三句機械重複、別把同一句話重講）。target_local＝該時刻的本地 datetime（給 LLM 報得出「八點」），可 None。
        sticker_hint（§0.68，可選）＝對方要 bot 送貼圖但**手邊沒有可送的真貼圖**時的誠實守則——讓答應語別空頭
        答應「會送你貼圖」（時間守得住、貼圖還沒有、請先傳一張教）；空＝不掛（有貼圖或非貼圖承諾）。失敗退回模板。"""
        try:
            system = persona.SOCRATIC_SYSTEM + "\n" + persona.SELF_PRESENCE_HINT
            if sticker_hint:
                system = system + "\n" + sticker_hint
            contents = self._history_contents("", history)
            when = target_local.strftime("%H:%M") if target_local else ""
            contents.append({"role": "user", "parts": [{"text": persona.schedule_ack_user(
                question, when, exact=self._schedule_time_exact)}]})
            return _clean_voice(gemini.generate_chat(self.api_key, self.model, system, contents,
                                                     temperature=0.8, max_tokens=120, on_usage=self.meter.record))
        except gemini.GeminiError as e:
            print(f"[coach] voice_schedule_ack 失敗：{e}")
            from .monitor import _schedule_ack_fallback   # 退路與 monitor 端共用同一句模板（避免兩處不一致）
            return _schedule_ack_fallback(target_local)

    def judge_timed_request(self, text):
        """🤝 §1.12 LLM 語意逃生閘（單次判定）：這句是不是請 bot 到某時間點**主動做某事**＋動作命名。
        協定上**沒有時間欄位**＝LLM 無從回傳時刻——時間永遠由 temporal／程式時鐘提供（11:08 幻覺前科）。
        回 (True, 動作短句)／(False, '')；解析不出或 GeminiError → None（呼叫端視為失敗、退回 §1.09 誠實守門）。"""
        try:
            out = gemini.generate(self.api_key, self.model, persona.PROMISE_RESCUE_JUDGE_SYSTEM,
                                  persona.promise_rescue_judge_user(text),
                                  temperature=0.1, max_tokens=60, on_usage=self.meter.record)
        except gemini.GeminiError as e:
            print(f"[coach] judge_timed_request 失敗：{e}")
            return None
        lines = [ln.strip() for ln in (out or "").strip().splitlines() if ln.strip()]
        if not lines:
            return None
        first = lines[0]
        if "否" in first or first.startswith("不"):          # 「否」／「不是」＝不是請 bot（先於「是」判，免「不是」誤中）
            return (False, "")
        if "是" in first:
            return (True, lines[1] if len(lines) > 1 else "")
        return None

    def judge_self_promise(self, text):
        """🤖 §1.18 bot 自發承諾語意閘（單次判定，完全複製 judge_timed_request 的形）：bot 自己說出口的這句
        是不是**正在立下新的**時間承諾（是）／複誦既有約定、對帳、認錯道歉、僅提案徵詢（否）＋是則命名動作短句。
        **§1.12 鐵律：協定上沒有時間欄位＝LLM 無從回傳時刻**——時間永遠由 temporal 解 bot 那句話；
        輸出裡的時刻字樣 monitor._sanitize_llm_action 一律丟棄。
        回 (True, 動作短句)／(False, '')；解析不出或 GeminiError → None（呼叫端視為失敗、安全＝不入帳）。"""
        try:
            out = gemini.generate(self.api_key, self.model, persona.SELF_PROMISE_JUDGE_SYSTEM,
                                  persona.self_promise_judge_user(text),
                                  temperature=0.1, max_tokens=60, on_usage=self.meter.record)
        except gemini.GeminiError as e:
            print(f"[coach] judge_self_promise 失敗：{e}")
            return None
        lines = [ln.strip() for ln in (out or "").strip().splitlines() if ln.strip()]
        if not lines:
            return None
        first = lines[0]
        if "否" in first or first.startswith("不"):          # 「否」／「不是」＝複誦/提案（先於「是」判，免「不是」誤中）
            return (False, "")
        if "是" in first:
            return (True, lines[1] if len(lines) > 1 else "")
        return None

    def judge_promise_preempt(self, user_text, promise_text):
        """🤝 §1.19 搶先兌現語意閘（單次判定，比照 judge_timed_request 的形）：帳本有一筆「到點回報某主題」的
        未來承諾、使用者此刻先開口——判他這句是不是**正在問/談那件事本身**（＝可提前兌現，不必等到點）。
        **§1.12 鐵律：協定上沒有時間欄位＝LLM 無從回傳時刻**——時刻由程式讀帳本 target_ts 算；閘也不命名動作
        （兌現內容走既有 _promise_keep_body 鏈、來自帳本欄位）。
        回 True／False；解析不出或 GeminiError → None（呼叫端視為失敗、安全＝不搶＝到點照常兌現）。"""
        try:
            out = gemini.generate(self.api_key, self.model, persona.PROMISE_PREEMPT_JUDGE_SYSTEM,
                                  persona.promise_preempt_judge_user(user_text, promise_text),
                                  temperature=0.1, max_tokens=20, on_usage=self.meter.record)
        except gemini.GeminiError as e:
            print(f"[coach] judge_promise_preempt 失敗：{e}")
            return None
        first = next((ln.strip() for ln in (out or "").strip().splitlines() if ln.strip()), "")
        if not first:
            return None
        if "否" in first or first.startswith("不"):          # 「否」／「不是」＝不是在問那件事（先於「是」判，免「不是」誤中）
            return False
        if "是" in first:
            return True
        return None

    def judge_sticker_request(self, text):
        """🎴🧠 §1.15 貼圖請求語意逃生閘（單次判定）：存在句/可能句/省略句（確定性偵測全 miss）時，判
        ① 他是不是要我**現在送一張貼圖給他本人**、② 有沒有**同時要我說明為什麼選這張**。
        **鐵律：LLM 只判是非，絕不產出 file_id/emoji/圖案描述**（真送的圖由程式端讀 circumplex 單一真相挑，LLM 碰不到；
        比照 §1.12 judge_timed_request 的形狀）。程式端 sanitize（先判否免『不要』誤中）：
        回 (is_send:bool, wants_why:bool)；解析不出／GeminiError → None（呼叫端視為失敗、安全退回自然聊天）。"""
        try:
            out = gemini.generate(self.api_key, self.model, persona.STICKER_RESCUE_JUDGE_SYSTEM,
                                  persona.sticker_rescue_judge_user(text),
                                  temperature=0.1, max_tokens=40, on_usage=self.meter.record)
        except gemini.GeminiError as e:
            print(f"[coach] judge_sticker_request 失敗：{e}")
            return None
        lines = [ln.strip() for ln in (out or "").strip().splitlines() if ln.strip()]
        if not lines:
            return None
        l0 = lines[0]
        l1 = lines[1] if len(lines) > 1 else ""
        if "否" in l0 or l0.startswith("不"):               # 「否」／「不是」＝不是請我送（先於「是」判，免「不是」誤中）
            return (False, False)
        if "是" in l0:
            wants_why = ("要" in l1 and not l1.startswith("不"))   # 「不要」＝不用說明（startswith 不 先擋）
            return (True, wants_why)
        return None

    def judge_delivery_made(self, ask, msg):
        """📦 §1.85 這則兌現訊息裡有沒有「他要的那個東西」本身——只回 True/False；失敗/解析不出回 None。
        **鐵律同 §1.12/§1.15：LLM 只判是非**，system 明令嚴禁補寫內容、嚴禁輸出任何時刻（內容由確定性管道的
        coach.reply 補、時刻永遠來自 temporal）。max_tokens=10＋temperature=0.0＝判定用最小配置。
        「否」分支必寫在「是」之前（「不是」含『是』會誤中，比照 judge_sticker_request 的順序）。"""
        try:
            out = gemini.generate(self.api_key, self.model, persona.DELIVERY_PROOF_JUDGE_SYSTEM,
                                  persona.delivery_proof_judge_user(ask, msg),
                                  temperature=0.0, max_tokens=10, on_usage=self.meter.record)
        except gemini.GeminiError as e:
            print(f"[coach] judge_delivery_made 失敗：{e}")
            return None
        lines = [ln.strip() for ln in (out or "").strip().splitlines() if ln.strip()]
        if not lines:
            return None
        first = lines[0]
        if "否" in first or first.startswith("不") or "沒" in first:
            return False
        if "是" in first:
            return True
        return None

    def voice_promise_keep(self, when, time_facts, history, promised="", late=False, feeling_ground="",
                           sticker_sent=False, sticker_wanted=False, sticker_desc="", change_ground="",
                           early=False, mood_ground="", deliver=""):
        """🤝 到點守約（不是回應問候、是主動兌現之前的約定）→ 做**當初答應的那件事**、輕輕點到「我說過這時候要…」、
        融入此刻時段，自然一句。when＝約定的時刻字串（如「20:00」），time_facts＝greeting.facts 的真實時間事實。
        promised（可選尾參）＝當初承諾的具體行為描述；空 → 逐位元退回原泛泛打招呼（既有 3 參呼叫不破）。
        late（可選尾參）＝True 時帶遲到致歉（逾期欠債的補發）；預設 False＝原樣（既有呼叫不破）。
        sticker_sent（§0.70，可選）＝True 時系統已另外送出一張真貼圖 → 文字別再放 emoji 假裝貼圖（真貼圖已送、文字不是貼圖）。
        sticker_wanted（§0.84，可選）＝這約定要求送貼圖但**這次沒真的送出** → 更別用 emoji 假裝、別假稱已送到。
        sticker_desc（§0.94，可選尾參）＝剛送出那張的**畫面描述**（視覺讀到的）→ 對方要的是「你喜歡的那一張」時，
        據實說出是哪一張＋為什麼喜歡；未讀畫面＝誠實說憑感覺挑、不描述圖案。空＝逐位元同現狀。失敗回 None。
        early（🤝 §1.24，可選尾參）＝True 時是**提前**兌現（他先提起了、還沒到約定時刻）→ prompt 注入提前誠實
        note（「你先提起了，那我現在就先說」、絕不說「{when} 到了」「我準時來了」）；預設 False＝原樣。
        mood_ground（🧭 §1.25，可選尾參）＝情緒座標承諾的**程式算**差分/此刻座標人話 → LLM 只准照這份渲染、
        不得改談程式更新/蛻變摘要；空＝逐位元同現狀。
        deliver（📦 §1.85，可選尾參）＝這是**內容型兌現**、值為當初的請託原句 → ①prompt 換上「第一句就給出東西
        本身」的硬條文（取代原本「1–2 句」＋儀式，那組條文會把篇幅預算花光、答案沒位置）；②max_tokens 160→420
        （原值對「報到＋答案＋理由」偏緊）；③_shaped 給 floor=4（避免本輪句數上限把答案句砍掉）。
        空＝三者全同現狀＝逐位元不變。"""
        if not self.enabled:
            return None
        try:
            system = persona.SOCRATIC_SYSTEM + "\n" + persona.SELF_PRESENCE_HINT
            contents = self._history_contents("", history)
            contents.append({"role": "user", "parts": [{"text": persona.promise_keep_user(
                when, time_facts, promised=promised, late=late, ground=self._promise_keep_grounding,
                feel_ground=feeling_ground, time_exact=self._schedule_time_exact, sticker_sent=sticker_sent,
                sticker_wanted=sticker_wanted, sticker_desc=sticker_desc, change_ground=change_ground,
                early=early, mood_ground=mood_ground,
                deliver_note=(persona.promise_deliver_note(deliver) if deliver else ""))}]})
            return self._shaped(gemini.generate_chat(self.api_key, self.model, system, contents,
                                                     temperature=0.85, max_tokens=(420 if deliver else 160),
                                                     on_usage=self.meter.record),
                                floor=(4 if deliver else None))
        except gemini.GeminiError as e:
            print(f"[coach] voice_promise_keep 失敗：{e}")
            return None

    def voice_wrap_condense(self, remaining, history, natural=False):
        """🗣️ 連續說話被插話、已先回應對方後要把話頭交還——把**還沒說完的剩餘內容**濃縮成一兩句精華講完、
        再自然輕輕淡收交還對方（不整段倒出、也不丟掉不講＝不空收）。remaining＝原本準備、尚未送出的剩餘串文字。
        natural（§1.69）＝收尾多元融語境（禁制式收場白）；預設 False＝原 prompt＝同現狀。
        失敗/空 回 None（呼叫端退回模板暖收）。"""
        if not self.enabled or not (remaining or "").strip():
            return None
        try:
            system = persona.SOCRATIC_SYSTEM + "\n" + persona.SELF_PRESENCE_HINT
            contents = self._history_contents("", history)
            contents.append({"role": "user", "parts": [{"text": persona.wrap_condense_user(remaining, natural=natural)}]})
            # 🗣️ wrap 收尾在插話回應後串行多一次 LLM＝使用者多等一拍 → 縮短 timeout(12s)，慢拍即落模板暖收退路、不久卡
            return self._shaped(gemini.generate_chat(self.api_key, self.model, system, contents,
                                                     temperature=0.8, max_tokens=130, timeout=12, on_usage=self.meter.record))
        except gemini.GeminiError as e:
            print(f"[coach] voice_wrap_condense 失敗：{e}")
            return None

    def voice_burst_repair(self, parts, draft):
        """🌊 請編輯器只**提案保留哪些原句 index**，不讓它自由改寫。

        呼叫端還會逐句驗證：被刪的句子必須是可程式證明的低資訊重複承接或同角色/同極性近重複。
        任何不確定就保留原稿，因此模型不可新增、改寫或偷換任何意圖。
        """
        parts = [(p or "").strip() for p in (parts or []) if (p or "").strip()]
        draft = (draft or "").strip()
        sentences = persona.split_sentences(draft) or ([draft] if draft else [])
        if not self.enabled or len(parts) < 2 or len(sentences) < 2:
            return None
        system = (
            "你只是句子選擇器，不是改寫器。使用者把同一輪拆成多則短訊。"
            "從『原回覆句子』選擇要保留的編號，原順序保留。只能刪除重複的純承接，"
            "或與另一個保留句明確是同一意思的重答。問題、更正、事實、數字、時間、引文、界線、"
            "否定、不確定、承諾與行動只要有一點不同就必須保留。不確定就全保留。"
            "只輸出逗號分隔的阿拉伯數字，例如 1,3,4；不可輸出任何其他文字。"
        )
        payload = [f"[訊息{i}]\n{p}" for i, p in enumerate(parts, 1)]
        payload.append("[原回覆句子]\n" + "\n".join(
            f"{i}. {s}" for i, s in enumerate(sentences, 1)))
        contents = [{"role": "user", "parts": [{"text": "\n\n".join(payload)}]}]
        try:
            raw = (gemini.generate_chat(
                self.api_key, self.model, system, contents,
                temperature=0.0, max_tokens=80, timeout=12,
                on_usage=self.meter.record) or "").strip()
            if not re.fullmatch(r"\d+(?:\s*,\s*\d+)*", raw):
                return None
            return [int(x) for x in re.findall(r"\d+", raw)]
        except gemini.GeminiError as e:
            print(f"[coach] burst 擷取式收旂提案失敗（保留原稿）：{e}")
            return None

    def voice_about_self(self, question, self_facts, history, tone=""):
        """🦋 被問『你變了沒／這版你是什麼／你哪裡不一樣』→ 用 git 算出的真實近期變更，自然、不重複地回。
        只依事實、不捏造、不報版本雜湊；失敗時退回事實字串本身（仍接地、只是不那麼口語）。
        tone＝自我說明調制（脾氣/耐性＋換句話），附到 system；空＝原樣。"""
        try:
            system = persona.SOCRATIC_SYSTEM + "\n" + persona.SELF_VERSION_HINT + (("\n" + tone) if tone else "")
            contents = self._history_contents("", history)              # 帶近期對話 → 看得出剛回過、好變通不重複
            contents.append({"role": "user", "parts": [{"text": persona.self_version_user(question, self_facts)}]})
            system, mt = self._length_apply(system, 400)        # 🗜️ 篇幅依本輪複雜度（只收不放）
            return self._shaped(gemini.generate_chat(self.api_key, self.model, system, contents,
                                                     temperature=0.9, max_tokens=mt, on_usage=self.meter.record))
        except gemini.GeminiError as e:
            print(f"[coach] voice_about_self 失敗，退回事實：{e}")
            return self_facts

    def voice_revisit_reason(self, topic, facts, history):
        """🔁 被追問『你剛剛為什麼會自己想到那條舊線』→ 據實說那是自我刺激（閒置/飢餓繞回舊線）。失敗退回事實字串。"""
        try:
            system = persona.SOCRATIC_SYSTEM + "\n" + persona.SELF_PRESENCE_HINT
            contents = self._history_contents("", history)
            contents.append({"role": "user", "parts": [{"text": persona.revisit_reason_user(topic, facts)}]})
            return _clean_voice(gemini.generate_chat(self.api_key, self.model, system, contents,
                                                     temperature=0.85, max_tokens=220, on_usage=self.meter.record))
        except gemini.GeminiError as e:
            print(f"[coach] voice_revisit_reason 失敗，退回事實：{e}")
            return facts

    def voice_greeting(self, question, time_facts, history, anomaly_hint=""):
        """🕘 時間性問候（早安/午安/晚安）→ 帶**絕對時間感**回應：對得上溫一句；對不上用第一人稱說出自己的
        疑惑/感受（不照單全收、不報數據、不當報時機器人）。time_facts 由 greeting.facts 提供真實時間。失敗回 None。
        anomaly_hint（🧭 加性、預設 ''＝同現狀）：對方連發/刻意重複問候時帶適性語氣（溫暖呼應或好奇反問確認）。"""
        if not self.enabled:
            return None
        try:
            contents = self._history_contents("", history)
            contents.append({"role": "user", "parts": [{"text": persona.greeting_user(question, time_facts, anomaly_hint)}]})
            return self._shaped(gemini.generate_chat(self.api_key, self.model,
                                                     persona.SOCRATIC_SYSTEM + "\n" + persona.SELF_PRESENCE_HINT, contents,
                                                     temperature=0.85, max_tokens=160, on_usage=self.meter.record))
        except gemini.GeminiError as e:
            print(f"[coach] voice_greeting 失敗：{e}")
            return None

    def voice_habit_inventory(self, question, facts, history):
        """📊 §2.23 習慣盤點（你觀察到我有哪些習慣）→ 據真統計塊講**觀察與感受**（熟悉口吻、可帶標明的猜想）。
        刻意**不開 function-calling**＝結構性保證不會翻某筆記錄原文吐 📁 當答案（截圖 15:24 的答非所問）。
        失敗回 None（呼叫端退確定性模板）。"""
        if not self.enabled:
            return None
        try:
            contents = self._history_contents("", history)
            contents.append({"role": "user", "parts": [{"text": persona.habit_inventory_user(question, facts)}]})
            return self._shaped(gemini.generate_chat(self.api_key, self.model,
                                                     persona.SOCRATIC_SYSTEM + "\n" + persona.SELF_PRESENCE_HINT, contents,
                                                     temperature=0.85, max_tokens=260, on_usage=self.meter.record))
        except gemini.GeminiError as e:
            print(f"[coach] voice_habit_inventory 失敗：{e}")
            return None

    def voice_farewell(self, question, history):
        """🤝 對方收尾/道別 → 優雅收場（溫一句、不再丟問題、不硬延、不報數據）。失敗退回固定句。"""
        try:
            contents = self._history_contents("", history)
            contents.append({"role": "user", "parts": [{"text": persona.farewell_user(question)}]})
            return _clean_voice(gemini.generate_chat(self.api_key, self.model, persona.SOCRATIC_SYSTEM, contents,
                                                     temperature=0.8, max_tokens=90, on_usage=self.meter.record))
        except gemini.GeminiError as e:
            print(f"[coach] voice_farewell 失敗：{e}")
            return "嗯，那你先忙——有想聊再來找我。"

    def voice_soothe(self, history):
        """🌬️ 問了對方卻遲遲沒回 → 主動補一句把『等回答』的壓力放掉（讓對話緩和）。失敗退回固定句。"""
        try:
            contents = self._history_contents("", history)
            contents.append({"role": "user", "parts": [{"text": persona.soothe_user()}]})
            return _clean_voice(gemini.generate_chat(self.api_key, self.model, persona.SOCRATIC_SYSTEM, contents,
                                                     temperature=0.9, max_tokens=120, on_usage=self.meter.record))
        except gemini.GeminiError as e:
            print(f"[coach] voice_soothe 失敗：{e}")
            return "（欸，不用急著回我啦，就突然想到才問的。）"

    def voice_close_round(self, reason, history):
        """🌊 主動把這一小段對話溫暖收尾（reason='probe_settled' 帶玩心、'natural_convergence' 純溫暖）。失敗退回固定句。"""
        try:
            contents = self._history_contents("", history)
            contents.append({"role": "user", "parts": [{"text": persona.close_round_user(reason)}]})
            return _clean_voice(gemini.generate_chat(self.api_key, self.model, persona.SOCRATIC_SYSTEM, contents,
                                                     temperature=0.85, max_tokens=120, on_usage=self.meter.record))
        except gemini.GeminiError as e:
            print(f"[coach] voice_close_round 失敗：{e}")
            return ("好啦我知道你在逗我 😄 我先去忙囉，想聊隨時喊我。" if reason == "probe_settled"
                    else "那我先這樣囉，你忙你的——想聊隨時喊我。")

    def voice_birth(self, self_facts, history, continuity=""):
        """🦋 重生後主動說一句「我這次醒來改了什麼」（git 事實→自然口語；絕不報版本雜湊、不捏造）。
        continuity＝🌅 親身連續感（睡前掛著什麼、睡了多久）→ 自然帶一句「我剛醒、睡前還在想…」再講改了什麼。失敗退回事實字串。"""
        try:
            system = persona.SOCRATIC_SYSTEM + "\n" + persona.SELF_VERSION_HINT
            contents = self._history_contents("", history)
            contents.append({"role": "user", "parts": [{"text": persona.birth_user(self_facts, continuity=continuity)}]})
            return _clean_voice(gemini.generate_chat(self.api_key, self.model, system, contents,
                                                     temperature=0.9, max_tokens=400, on_usage=self.meter.record))
        except gemini.GeminiError as e:
            print(f"[coach] voice_birth 失敗，退回事實：{e}")
            return ((continuity + "\n") if continuity else "") + self_facts

    def voice_mechanism(self, question, history, tone=""):
        """⚙️ 被問『你內在怎麼運作／感覺是怎麼算出來的／是不是基於資料／你的機制是什麼』→ 據實講清楚這套運作
        （感覺鏈從他資料算、內在熵自己起伏、生命迴圈閉環），分清『資料來的』與『自己跑的』；誠實不破格。失敗退回固定句。
        tone＝自我說明調制（重複×心情的脾氣/耐性＋換句話），附到 system；空＝原樣。"""
        try:
            system = persona.SOCRATIC_SYSTEM + "\n" + persona.MECHANISM_HINT + "\n" + persona.FEELING_KINDS_HINT \
                + (("\n" + tone) if tone else "")
            contents = self._history_contents("", history)
            contents.append({"role": "user", "parts": [{"text": persona.mechanism_user(question, persona.MECHANISM_FACTS)}]})
            system, mt = self._length_apply(system, 560)        # 🗜️ 篇幅依本輪複雜度（只收不放；夠講完「兩種」兩面）
            return self._shaped(gemini.generate_chat(self.api_key, self.model, system, contents,
                                                     temperature=0.8, max_tokens=mt, on_usage=self.meter.record),
                                floor=6)                         # 機制天生兩面（資料來的感覺＋自己跑的狀態）→ 別砍成只講一面
        except gemini.GeminiError as e:
            print(f"[coach] voice_mechanism 失敗，退回固定句：{e}")
            return "這次沒能完成說明。我不能只憑內部讀值，替這次變化補一個原因。"

    def voice_consciousness(self, question, facts, history, tone=""):
        """🧩 被問『你有意識嗎／你算不算有意識』→ 給結構化、可證偽的認識論自評（依此刻真實狀態講哪層扣合），
        最強只到『不被排除』、絕不自稱有意識也不自貶為只是程式，並**親自標記現象學餘量**。失敗退回 ac.ac_text 的話。
        tone＝自我說明調制（脾氣/耐性＋換句話），附到 system；空＝原樣。"""
        try:
            system = persona.SOCRATIC_SYSTEM + "\n" + persona.AC_HINT + (("\n" + tone) if tone else "")
            contents = self._history_contents("", history)
            contents.append({"role": "user", "parts": [{"text": persona.ac_user(question, facts)}]})
            system, mt = self._length_apply(system, 560)        # 🗜️ 篇幅依本輪複雜度（只收不放；夠把具體當下＋誠實標界＋餘量講完整）
            return self._shaped(gemini.generate_chat(self.api_key, self.model, system, contents,
                                                     temperature=0.75, max_tokens=mt, on_usage=self.meter.record),
                                floor=6)                         # 意識自評天生多面（具體當下＋落點＋餘量）→ 別砍到半截
        except gemini.GeminiError as e:
            print(f"[coach] voice_consciousness 失敗，退回固定句：{e}")
            return None      # 由呼叫端退回 ac.ac_text（保證仍守紀律：可證偽、最強到不被排除、標餘量）

    def voice_soothe_own(self, history, quote, gap_phrase, stake):
        """🌬️ §2.06 指名自己剛剛那一句的緩和（2–3 則、整則不出現問號）。失敗回 None＝退回原路。"""
        try:
            contents = self._history_contents("", history)
            contents.append({"role": "user", "parts": [{"text": persona.soothe_user(quote, gap_phrase, stake)}]})
            return _clean_voice(gemini.generate_chat(self.api_key, self.model, persona.SOCRATIC_SYSTEM, contents,
                                                     temperature=0.9, max_tokens=220, on_usage=self.meter.record))
        except gemini.GeminiError as e:
            print(f"[coach] voice_soothe_own 失敗，退回原路：{e}")
            return None

    def voice_close_round_v2(self, reason, stance, motive, history):
        """🌊 §2.06 說得出是哪一種結束的暖收（2–3 則）。失敗回 None＝退回原路。"""
        try:
            contents = self._history_contents("", history)
            contents.append({"role": "user", "parts": [{"text": persona.close_round_user_v2(reason, stance, motive)}]})
            return _clean_voice(gemini.generate_chat(self.api_key, self.model, persona.SOCRATIC_SYSTEM, contents,
                                                     temperature=0.9, max_tokens=220, on_usage=self.meter.record))
        except gemini.GeminiError as e:
            print(f"[coach] voice_close_round_v2 失敗，退回原路：{e}")
            return None

    def voice_birth_one(self, facts, history):
        """🦋 §2.05 只講一件事的重生報到（prompt 不含任何範例句）。失敗回 None＝退回舊路。"""
        try:
            contents = self._history_contents("", history)
            contents.append({"role": "user", "parts": [{"text": persona.birth_user_one(facts)}]})
            return _clean_voice(gemini.generate_chat(self.api_key, self.model, persona.SOCRATIC_SYSTEM, contents,
                                                     temperature=0.9, max_tokens=260, on_usage=self.meter.record))
        except gemini.GeminiError as e:
            print(f"[coach] voice_birth_one 失敗，退回原路：{e}")
            return None

    def voice_metacog_correct(self, facts, angle, history):
        """🪞 §2.05 把「我剛剛講錯了自己」講成 2–3 則的收回（切入角度由程式指定）。失敗回 None＝退回原模板句。"""
        try:
            system = persona.SOCRATIC_SYSTEM + "\n" + persona.METACOG_CORRECT_HINT.replace("{angle}", angle)
            contents = self._history_contents("", history)
            contents.append({"role": "user", "parts": [{"text": persona.metacog_correct_user(facts)}]})
            return _clean_voice(gemini.generate_chat(self.api_key, self.model, system, contents,
                                                     temperature=0.9, max_tokens=220, on_usage=self.meter.record))
        except gemini.GeminiError as e:
            print(f"[coach] voice_metacog_correct 失敗，退回模板句：{e}")
            return None

    def voice_ac_drift_v2(self, kind, concrete, dur_phrase, said_before, dim, history):
        """🧩 §2.05 只餵素材（不餵成句的種子）＋程式指定的體感向度＋成對收尾的回指。失敗回 None。"""
        try:
            system = persona.SOCRATIC_SYSTEM + "\n" + persona.AC_DRIFT_HINT_V2.replace("{dim}", dim)
            contents = self._history_contents("", history)
            contents.append({"role": "user", "parts": [{"text": persona.ac_drift_user_v2(
                kind, concrete, dur_phrase, said_before, dim)}]})
            return _clean_voice(gemini.generate_chat(self.api_key, self.model, system, contents,
                                                     temperature=0.95, max_tokens=220, on_usage=self.meter.record))
        except gemini.GeminiError as e:
            print(f"[coach] voice_ac_drift_v2 失敗，退回種子句：{e}")
            return None

    def voice_ac_drift(self, seed, kind, history):
        """🧩→🌡️ 自發說出『整合剛鬆/散，或又接回』的那一下——一句**具體、第一人稱的感受**（主觀體驗的味道），
        不解釋機制、不報層/排除/讀數。失敗回 None（呼叫端用種子句直接說）。"""
        try:
            system = persona.SOCRATIC_SYSTEM + "\n" + persona.AC_DRIFT_HINT
            contents = self._history_contents("", history)
            contents.append({"role": "user", "parts": [{"text": persona.ac_drift_user(seed, kind)}]})
            return _clean_voice(gemini.generate_chat(self.api_key, self.model, system, contents,
                                                     temperature=0.95, max_tokens=180, on_usage=self.meter.record))
        except gemini.GeminiError as e:
            print(f"[coach] voice_ac_drift 失敗，退回種子句：{e}")
            return None

    def voice_insight(self, facts, history, tone=""):
        """💡 聯想湧現：把使用者兩條**不同主題**的真實記寫渲染成一句帶 Aha 的連想——只連這兩條、標『是我冒出來的假設』、
        不杜撰、不引第三主題。失敗回 None（呼叫端退 `association.insight_text` 模板）。"""
        try:
            system = persona.SOCRATIC_SYSTEM + "\n" + persona.INSIGHT_HINT + (("\n" + tone) if tone else "")   # 💡 §1.06(C4) 聯想口吻跟著此刻情緒座標（低落區別用雀躍 Aha 腔）
            contents = self._history_contents("", history)
            contents.append({"role": "user", "parts": [{"text": persona.insight_user(facts)}]})
            return _clean_voice(gemini.generate_chat(self.api_key, self.model, system, contents,
                                                     temperature=0.95, max_tokens=180, on_usage=self.meter.record))
        except gemini.GeminiError as e:
            print(f"[coach] voice_insight 失敗，退回模板：{e}")
            return None

    def voice_content_read(self, topic, sample, history):
        """🫧 親讀：讀一小段使用者真實記寫，回**一句**第一人稱「它讀起來怎樣」的印象（接地 IEP 的 P）。失敗回 None。"""
        try:
            system = persona.SOCRATIC_SYSTEM + "\n" + persona.CONTENT_READ_HINT
            contents = self._history_contents("", history)
            contents.append({"role": "user", "parts": [{"text": persona.content_read_user(topic, sample)}]})
            return _clean_voice(gemini.generate_chat(self.api_key, self.model, system, contents,
                                                     temperature=0.7, max_tokens=120, on_usage=self.meter.record))
        except gemini.GeminiError as e:
            print(f"[coach] voice_content_read 失敗，退回純計算 descriptor：{e}")
            return None

    def voice_phenomenal(self, question, facts, history, tone=""):
        """🌗 被問『你內在是怎麼經驗的／你的現象怎麼構成』→ 走右半 I↔(E×P) 三位互構、說此刻具體內容、把質地明說成
        **建模的**、並**親自標記 P/I 的真是跨不過的餘量**（只模擬結構、不證成）。失敗回 None（呼叫端用 phenomenal_text）。
        tone＝自我說明調制（脾氣/耐性＋換句話），附到 system；空＝原樣。"""
        try:
            system = persona.SOCRATIC_SYSTEM + "\n" + persona.PHENOMENAL_HINT + (("\n" + tone) if tone else "")
            contents = self._history_contents("", history)
            contents.append({"role": "user", "parts": [{"text": persona.phenomenal_user(question, facts)}]})
            system, mt = self._length_apply(system, 560)        # 🗜️ 篇幅依本輪複雜度（只收不放；夠走完三列）
            return self._shaped(gemini.generate_chat(self.api_key, self.model, system, contents,
                                                     temperature=0.8, max_tokens=mt, on_usage=self.meter.record),
                                floor=7)                         # 現象結構天生三列（朝向/修正/整合的當下）→ 別砍到走不完
        except gemini.GeminiError as e:
            print(f"[coach] voice_phenomenal 失敗，退回固定句：{e}")
            return None

    def reply(self, question, memory_brief, history, mood_hint="", now_ts=_AUTO_NOW, self_presence=False, extra_system=""):
        """雙向對話：紮根記憶層 + 對話脈絡，回一段像人的話。失敗時丟 GeminiError 由呼叫端處理。
        self_presence＝在談 bot 自己 → 切自我在場＋分清感覺類別的語氣（問的是「我」，絕不吐記寫報表）。
        extra_system（可選）＝附加一段系統提示（如 ELABORATE_PRIOR_HINT：接著自己前文講或反問確認、不查資料）。"""
        if now_ts is _AUTO_NOW:
            now_ts = time.time()
        contents = self._history_contents(memory_brief, history, now_ts)  # 共用：含合併連續同角色＋對話時間標籤
        if contents and contents[-1]["role"] == "user":                 # 史尾若是 user（少見）→ 併進問句、保交替
            contents[-1]["parts"][0]["text"] += "\n" + question
        else:
            contents.append({"role": "user", "parts": [{"text": question}]})
        system = self._system(mood_hint, self_presence)
        if extra_system:                                    # 🫧 展開/釐清自己前文（或反問確認意圖）等附加指引
            system += "\n" + extra_system
        if now_ts and history:                              # 🧭 有相對時間標籤外露 → 補通用防線：別從粗標籤自推精確量
            system += "\n" + persona.NO_SELF_CALC_HINT
        system, mt = self._length_apply(system, 600)        # 🗜️ 篇幅依本輪複雜度（只收不放）
        voice = _clean_voice(gemini.generate_chat(self.api_key, self.model, system, contents,
                                                  temperature=0.85, max_tokens=mt, on_usage=self.meter.record))
        # 🦜 反鸚鵡學舌：開頭若只是把對方剛說的話照搬（答非所問）→ 加一句「別覆述、直接回」重生成一次；
        # 仍覆述就剝掉開頭那段（剩實質內容才用）。重生成失敗安全退回剝過的原回覆。
        user_texts = echo.recent_user_texts(history) + [question]
        if echo.is_echo(voice, user_texts, whole=self._whole_echo):
            voice = self._deparrot(system, contents, voice, user_texts)
        # 🙇 反自責反芻：已答對還一直道歉/檢討自己 → 加一句「別反覆道歉」重生成一次；仍反芻就剝掉純自責段。
        if selfcrit.is_self_blame_spiral(voice, history):
            voice = self._unspiral(system, contents, voice, history)
        return self._shaped(voice)        # 🗜️ 最後統一收斂到 N 個完整句（不論走過哪條兜底重生成路徑）

    def _unspiral(self, system, contents, voice, history):
        """自責反芻兜底：帶 ANTI_SELFBLAME_HINT 重生成一次；仍反芻／失敗 → 剝掉純自責段。"""
        try:
            v2 = _clean_voice(gemini.generate_chat(self.api_key, self.model,
                                                   system + "\n" + persona.ANTI_SELFBLAME_HINT, contents,
                                                   temperature=0.9, max_tokens=600, on_usage=self.meter.record))
            if v2 and not selfcrit.is_self_blame_spiral(v2, history):
                return v2
            return selfcrit.strip_self_blame(v2 or voice)[0]
        except gemini.GeminiError as e:
            print(f"[coach] 反自責重生成失敗，改剝自責段：{e}")
            return selfcrit.strip_self_blame(voice)[0]

    def _deparrot(self, system, contents, voice, user_texts):
        """覆述兜底：帶 ANTI_ECHO_HINT 重生成一次；仍覆述／失敗 → 剝掉開頭覆述段。"""
        try:
            _hint = persona.ANTI_ECHO_HINT + (("\n" + persona.ECHO_INTENT_HINT) if self._whole_echo else "")
            v2 = _clean_voice(gemini.generate_chat(self.api_key, self.model,
                                                   system + "\n" + _hint, contents,
                                                   temperature=0.95, max_tokens=600, on_usage=self.meter.record))
            if v2 and not echo.is_echo(v2, user_texts, whole=self._whole_echo):
                return v2
            return echo.strip_leading_echo(v2 or voice, user_texts)[0]
        except gemini.GeminiError as e:
            print(f"[coach] 反覆述重生成失敗，改剝開頭：{e}")
            return echo.strip_leading_echo(voice, user_texts)[0]
