"""確定性資料工具（單一事實來源）。

系統性解法：所有「關於記寫的事實」（時間、清單、跨度、計數、時間範圍、花費）都由這裡用
**真實資料**算出來。LLM 只負責「挑哪個工具 + 給參數」（function calling），事實一律不經
LLM 生成——這就根治了「捏造時間/清單/跨度」這類問題，也不必再為每種問法寫一條 regex。

要新增一種「可被問的事實」，只要在這裡加一個 resolver + 一筆 TOOL_DECLS。
"""

import re
from datetime import date as _date

from . import analyzer, temporal

_TYPE_TAG = {"image": "[圖]", "sticker": "[貼圖]", "audio": "[語音]",
             "video": "[影片]", "file": "[檔]", "link": "[連結]"}

# 主題指代：「那條線/它/這條/那個主題…」→ 綁到對話焦點剛 surface 的主線（只在參數很短＝純指代時才當指代）。
_DEICTIC_TOPIC_RE = re.compile(r"那條線|那條|這條線|這條|那個主題|這個主題|那主題|剛剛那條|剛那條|剛說的那條|那一條|^它$|^它的|^這個$")

# 「6/13 / 6月13日 / 2026/06/13」這類日期 → days_since 拿來算「到現在幾天」。完整年月日優先；只有月日就推年份。
_DATE_FULL_RE = re.compile(r"(\d{4})\s*[/\-.年]\s*(\d{1,2})\s*[/\-.月]\s*(\d{1,2})")
_DATE_MD_RE = re.compile(r"(\d{1,2})\s*[/\-.月]\s*(\d{1,2})")


def _parse_date_loose(s, now_local):
    """把『6/13 / 6月13日 / 2026/06/13』解析成 date；解析不到回 None。
    只有月/日 → 推年份（落在未來就算去年同日，對應『6/13 到現在』這種往回算）。純函式、可單測。"""
    s = s or ""
    m = _DATE_FULL_RE.search(s)
    if m:
        try:
            return _date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = _DATE_MD_RE.search(s)
    if m:
        mo, d = int(m.group(1)), int(m.group(2))
        try:
            cand = _date(now_local.year, mo, d)
        except ValueError:
            return None
        if cand > now_local.date():       # 落在未來 → 視為去年同日（往回算才合理）
            try:
                cand = _date(now_local.year - 1, mo, d)
            except ValueError:
                return None
        return cand
    return None


def _deictic_topic_target(ctx, topic):
    """topic 若是純指代（那條線/它…）→ 回對話焦點的主線；否則回 None（照常走 best_topic）。"""
    t = (topic or "").strip()
    if not (t and len(t) <= 6 and _DEICTIC_TOPIC_RE.search(t)):
        return None
    return ((getattr(ctx.state, "focus", None) or {}).get("topic")) if getattr(ctx, "state", None) else None


class ToolCtx:
    """跑工具需要的一切（真實資料）。"""
    def __init__(self, data, snap, tz, now, meter=None, state=None):
        self.data = data
        self.snap = snap
        self.tz = tz
        self.now = now
        self.meter = meter
        self.state = state


def _records(ctx):
    return ctx.data.get("records") or []


def _lcs_len(a, b):
    if not a or not b:
        return 0
    dp = [0] * (len(b) + 1)
    best = 0
    for ca in a:
        ndp = [0] * (len(b) + 1)
        for j, cb in enumerate(b):
            if ca == cb:
                ndp[j + 1] = dp[j] + 1
                best = max(best, ndp[j + 1])
        dp = ndp
    return best


def best_topic(query, records, min_score=2):
    counts = {}
    for r in records:
        lab = r.get("topicLabel")
        if lab:
            counts[lab] = counts.get(lab, 0) + 1
    best, best_score = None, 0
    for lab in counts:
        s = _lcs_len(query or "", lab)
        if s > best_score:
            best, best_score = lab, s
    return best if best_score >= min_score else None


def best_category(query, records, min_score=2):
    """把問句對到一個**大類**（category，如「靈感／生活／研究／閱讀」）——當它不是某個議題、而是整個大類時用。
    與 best_topic 同法，但比對 `category`。沒有夠像的回 None。"""
    counts = {}
    for r in records:
        cat = r.get("category")
        if cat:
            counts[cat] = counts.get(cat, 0) + 1
    best, best_score = None, 0
    for cat in counts:
        s = _lcs_len(query or "", cat)
        if s > best_score:
            best, best_score = cat, s
    return best if best_score >= min_score else None


def topic_recent_texts(records, topic, k=3):
    """那條線（議題或大類）最近 k 筆記寫的精簡內容——給「你對X有什麼感覺」在量還不夠時也稍提實際內容
    （讓對方明白那零星幾點在講什麼、為什麼還連不成線索）。回字串清單（可空）。"""
    rs = [r for r in (records or [])
          if (r.get("topicLabel") == topic or r.get("category") == topic)
          and (r.get("text") or "").strip() and analyzer.parse_ts(r.get("ts"))]
    rs.sort(key=lambda r: analyzer.parse_ts(r.get("ts")))
    out = []
    for r in rs[-k:]:
        t = " ".join((r.get("text") or "").split())
        out.append(t[:50] + ("…" if len(t) > 50 else ""))
    return out


# ── resolvers（每個回傳給使用者看的文字；事實全來自真實資料）──────────────
def get_current_time(ctx, **_):
    local = ctx.now.astimezone(ctx.tz)
    out = (f"🕐 現在 {local.strftime('%Y/%m/%d')}（{temporal.WEEKDAYS[local.weekday()]}）"
           f"{local.strftime('%H:%M')}（{temporal.day_part(local.hour)}）")
    rec_ts = [t for t in (analyzer.parse_ts(r.get("ts")) for r in _records(ctx)) if t]
    if rec_ts:
        lw = max(rec_ts)
        out += f"\n你最後一次記寫：{lw.astimezone(ctx.tz).strftime('%m/%d %H:%M')}（{temporal.human_gap(ctx.now - lw)}前）"
    return out


def _ts_label(ctx, ts, datefmt="%Y/%m/%d %H:%M"):
    """記寫時間給 LLM 的標籤＝絕對日期 ＋ **程式算好的相對時間**（如「1 天前」）。
    為什麼：LLM 自己拿絕對日期推「N 天前」會算錯（截圖：06/23 的記寫被說成『三天前』）；
    這裡用真實牆上時間 ctx.now 算好相對差，LLM 直接照講即可，不再幻覺相對時間。"""
    return f"{ts.astimezone(ctx.tz).strftime(datefmt)}（{temporal.human_gap(ctx.now - ts)}前）"


def overall_stats(ctx, **_):
    f, s = ctx.snap.funnel, ctx.snap.summary
    return (f"📊 目前：總 {s['total']} 筆｜近24h {s['last24h']}｜近7d {s['last7d']}；"
            f"連續記寫 {s['streak']} 天；"
            f"漏斗 🌱{f['candidate']} 進行中・🌿{f['context']} 候選歷程・🌳{f['journey']} 學習歷程（👀{f['watch']}）。")


_FUNNEL_HEADS = {
    "scattered": "🌱 還零散、還沒成形的念頭（進行中）",
    "context":   "🌿 快成形的（候選歷程）",
    "journey":   "🌳 已成形的學習歷程",
}


def list_funnel_topics(ctx, stage="scattered", **_):
    """列出某漏斗階段的『主題清單』（不是數字）——把「零散的念頭/快成形/學習歷程/卡住」這種說法
    轉成實際的主題標題清單。零散念頭＝candidate、快成形＝context、學習歷程＝journey、卡住＝snap.gaps。"""
    data = ctx.data
    contexts = data.get("contexts") or []
    journeys = data.get("journeys") or []
    st = (stage or "scattered").strip().lower()
    if st in ("stuck", "卡住", "差一步"):
        lines = [g.get("line") for g in (getattr(ctx.snap, "gaps", None) or []) if g.get("line")][:8]
        if not lines:
            return "目前沒有卡在升格邊緣（差一步）的主題。"
        return "🧱 卡住／差一步的主題：\n" + "\n".join("・" + l for l in lines)
    if st in ("context", "候選歷程", "快成形"):
        titles = [analyzer.context_title(c) for c in contexts if c.get("status") == "context"]
        head = _FUNNEL_HEADS["context"]
    elif st in ("journey", "學習歷程", "已成形"):
        by_id = {c.get("id"): c for c in contexts}
        titles = [analyzer.context_title(by_id[j.get("contextId")]) if by_id.get(j.get("contextId"))
                  else (j.get("userTitle") or j.get("label") or "(未命名)")
                  for j in journeys if j.get("status") == "journey"]
        head = _FUNNEL_HEADS["journey"]
    else:                                            # scattered / candidate（預設＝最常被問的「零散的念頭」）
        titles = [analyzer.context_title(c) for c in contexts if c.get("status") == "candidate"]
        head = _FUNNEL_HEADS["scattered"]
    seen, uniq = set(), []                           # 去重保序
    for t in titles:
        if t and t not in seen:
            seen.add(t)
            uniq.append(t)
    if not uniq:
        return f"{head}：目前沒有。"
    body = "\n".join("・" + t for t in uniq[:40])
    more = f"\n…（共 {len(uniq)} 條，先列前 40）" if len(uniq) > 40 else ""
    return f"📂 {head}（{len(uniq)} 條）：\n{body}{more}"


def _records_under_category(ctx, cat, recs):
    """列「某個**大類**底下」的記寫，依議題分組、各帶最近幾筆內容——回答「靈感／生活…那幾則說了什麼」。"""
    under = [r for r in recs if r.get("category") == cat and analyzer.parse_ts(r.get("ts"))]
    if not under:
        return f"「{cat}」這個大類底下這會兒沒有記寫。"
    under.sort(key=lambda r: analyzer.parse_ts(r.get("ts")))
    by_topic = {}
    for r in under:
        by_topic.setdefault(r.get("topicLabel") or "(未命名)", []).append(r)
    if getattr(ctx, "state", None) is not None:               # 焦點＝這大類裡筆數最多那條議題（下一句「那條線」接得回）
        dom = max(by_topic, key=lambda k: len(by_topic[k]))
        ctx.state.focus = dict(getattr(ctx.state, "focus", None) or {}, topic=dom, ts=ctx.now.timestamp())
    lines = [f"📂 「{cat}」這個大類底下（共 {len(under)} 筆，依議題）："]
    for lab, rs in sorted(by_topic.items(), key=lambda kv: -len(kv[1])):
        lines.append(f"〔{lab}〕{len(rs)} 筆：")
        for r in rs[-5:]:                                     # 每條議題列最近 5 筆，帶實際內容
            ts = analyzer.parse_ts(r.get("ts"))
            tag = _TYPE_TAG.get(r.get("type"), "")
            txt = " ".join((r.get("text") or "").split())[:120]
            lines.append(f"・{_ts_label(ctx, ts)} {tag}{txt}".rstrip())
    return "\n".join(lines)


def list_topic_records(ctx, topic=None, earliest_only=False, latest_only=False, **_):
    recs = _records(ctx)
    if topic:
        topic = _deictic_topic_target(ctx, topic) or topic   # 「那條線/它」→ 綁回剛提到的主線
        label = best_topic(topic, recs)
        if not label:
            cat = best_category(topic, recs)                 # 不是議題 → 也許是個**大類**（如「靈感」）→ 列那大類底下的
            if cat:
                return _records_under_category(ctx, cat, recs)
            return f"找不到跟「{topic}」對得起來的主題。"
        if getattr(ctx, "state", None) is not None:           # 記住焦點主線：下一句「那條線」就接得回
            ctx.state.focus = dict(getattr(ctx.state, "focus", None) or {}, topic=label, ts=ctx.now.timestamp())
        chosen = [r for r in recs if r.get("topicLabel") == label]
        head = f"〔{label}〕"
    else:
        chosen = list(recs)
        head = "全部記寫"
    chosen = [r for r in chosen if analyzer.parse_ts(r.get("ts"))]
    chosen.sort(key=lambda r: analyzer.parse_ts(r.get("ts")))   # 由舊到新
    if not chosen:
        return "目前沒有相符的記寫。"
    total, cap = len(chosen), 40
    if earliest_only:                       # 「第一筆/最早」＝距今最遠那筆
        chosen, suffix = chosen[:1], "（第一筆）"
    elif latest_only:                       # 「最近一次/最後一筆/上次」＝距今最近那筆
        chosen, suffix = chosen[-1:], "（最近一筆）"
    elif total > cap:                       # 清單太長 → 顯示「最近 cap 筆」（不是最舊的，免得永遠到不了最新）
        chosen, suffix = chosen[-cap:], f"，共 {total} 筆（僅顯示最近 {cap} 筆，較早的 {total - cap} 筆略過）"
    else:
        suffix = f"，共 {total} 筆"
    lines = [f"📂 {head}{suffix}："]
    for r in chosen:
        ts = analyzer.parse_ts(r.get("ts"))
        tag = _TYPE_TAG.get(r.get("type"), "")
        txt = " ".join((r.get("text") or "").split())[:120]
        lines.append(f"・{_ts_label(ctx, ts)} {tag}{txt}".rstrip())
    return "\n".join(lines)


def recent_filings(ctx, **_):
    """最近被背景歸戶的記寫，依 大類｜議題 分組、標目前狀態——回答「我最近的記寫歸到哪了／這些歸到哪類」。
    （歸戶由 LINE 背景算，剛寫的可能還在分類中。）事實取自 snap.filed_records，不捏造。"""
    from . import thresholds as T
    filed = [f for f in (getattr(ctx.snap, "filed_records", None) or []) if f.get("ts")]
    if not filed:
        return "目前還沒有被背景歸戶的記寫（剛寫的可能還在分類中）。"
    recent = sorted(filed, key=lambda f: f["ts"], reverse=True)[:30]
    groups = {}
    for f in recent:
        g = groups.setdefault(f["key"], {"count": 0, "status": f.get("status"),
                                         "category": f.get("category"), "topicLabel": f.get("topicLabel")})
        g["count"] += 1
    lines = [f"📝 最近歸戶（依 大類｜議題，取最近 {len(recent)} 則）："]
    for _k, g in sorted(groups.items(), key=lambda kv: -kv[1]["count"]):
        icon = T.STATE_ICON.get(g["status"], "🌱")
        name = f"{g['category']}｜{g['topicLabel']}" if g.get("category") else g.get("topicLabel")
        suffix = f" ×{g['count']}" if g["count"] > 1 else ""
        lines.append(f"・{icon} 〈{name}〉{suffix}")
    return "\n".join(lines)


def topic_time_spans(ctx, order="longest", top=5, **_):
    spans = {}
    for r in _records(ctx):
        lab = r.get("topicLabel")
        ts = analyzer.parse_ts(r.get("ts"))
        if not lab or not ts:
            continue
        e = spans.get(lab)
        if not e:
            spans[lab] = [ts, ts, 1]
        else:
            e[0] = min(e[0], ts)
            e[1] = max(e[1], ts)
            e[2] += 1
    if not spans:
        return "目前還沒有可比較跨度的主題。"
    ranked = sorted(spans.items(), key=lambda kv: (kv[1][1] - kv[1][0]), reverse=(order != "shortest"))
    best = ranked[0][0]
    lines = [f"📏 橫跨時間{'最短' if order == 'shortest' else '最久'}的主題是〈{best}〉。\n"
             f"各主題跨度（第一筆→最後一筆，依真實記寫時間）："]
    for lab, (a, b, n) in ranked[:top]:
        lines.append(f"・〈{lab}〉{a.astimezone(ctx.tz).strftime('%Y/%m/%d')} → "
                     f"{b.astimezone(ctx.tz).strftime('%Y/%m/%d')}，跨度 {(b - a).days} 天（{n} 筆）")
    return "\n".join(lines)


def topic_span(ctx, topic=None, **_):
    """某條線（或全部記寫）的時間跨度：第一筆 → 最後一筆＝橫跨幾天、共幾筆，並附第一筆距今多久。
    回答「〔X〕第一筆到最新一筆橫跨幾天／這條線持續多久／從第一筆到現在多久／是不是橫跨 N 天」。
    跨度天數一律由真實時間戳相減（root：被問『是不是橫跨 10 天』時 LLM 自推說錯成 10，實際 12——
    這裡程式算好，LLM 照講即可、不再幻覺天數）。topic 留空＝全部記寫的跨度。"""
    recs = _records(ctx)
    if topic:
        topic = _deictic_topic_target(ctx, topic) or topic   # 「那條線/它」→ 綁回剛提到的主線
        label = best_topic(topic, recs)
        if label:
            chosen, head = [r for r in recs if r.get("topicLabel") == label], f"〔{label}〕"
        else:
            cat = best_category(topic, recs)                 # 不是議題 → 也許是個大類（如「靈感」）
            if not cat:
                return f"找不到跟「{topic}」對得起來的主題。"
            chosen, head, label = [r for r in recs if r.get("category") == cat], f"大類〔{cat}〕", cat
        if getattr(ctx, "state", None) is not None:           # 記住焦點主線：下一句「那條線」接得回
            ctx.state.focus = dict(getattr(ctx.state, "focus", None) or {}, topic=label, ts=ctx.now.timestamp())
    else:
        chosen, head = list(recs), "全部記寫"
    tss = sorted(t for t in (analyzer.parse_ts(r.get("ts")) for r in chosen) if t)
    if not tss:
        return f"{head}目前沒有可算跨度的記寫。"
    a, b = tss[0], tss[-1]
    la, lb = a.astimezone(ctx.tz), b.astimezone(ctx.tz)
    if len(tss) == 1:
        return f"📏 {head}目前只有 1 筆（{la.strftime('%Y/%m/%d')}，{temporal.human_gap(ctx.now - a)}前），還談不上跨度。"
    return (f"📏 {head}：第一筆 {la.strftime('%Y/%m/%d')} → 最後一筆 {lb.strftime('%Y/%m/%d')}，"
            f"橫跨 {(b - a).days} 天（共 {len(tss)} 筆）；第一筆距今 {temporal.human_gap(ctx.now - a)}。")


def days_since(ctx, date="", **_):
    """從某個日期到現在是幾天（純日期相減）。回答「6/13 到現在幾天了／X 號到今天差幾天／距離 6 月 13 號多久」。
    日期一律由程式解析＋相減（root：LLM 自推日期差會算錯）。解析不到給可照做的提示。"""
    local_now = ctx.now.astimezone(ctx.tz)
    d = _parse_date_loose(date, local_now)
    if not d:
        return f"看不懂日期「{date}」，可以說 6/13、6月13日、2026/06/13 這種。"
    diff = (local_now.date() - d).days
    if diff < 0:
        return f"⏳ {d.strftime('%Y/%m/%d')} 還沒到（在 {-diff} 天後）。"
    if diff == 0:
        return f"⏳ {d.strftime('%Y/%m/%d')} 就是今天。"
    return f"⏳ {d.strftime('%Y/%m/%d')} 到現在（{local_now.strftime('%Y/%m/%d')}）是 {diff} 天。"


def records_in_time_range(ctx, range="", **_):
    asked_recent = temporal.is_just_now(range or "") or temporal.is_fresh_deixis(range or "")  # 使用者問的是「剛剛/最新」嗎
    q = "剛剛" if temporal.is_fresh_deixis(range or "") else (range or "")   # 「新東西/最新」＝最近一段新記寫
    anchor = getattr(ctx.state, "last_range", None) if getattr(ctx, "state", None) else None
    resolved = temporal.resolve_range(q, _records(ctx), ctx.now, ctx.tz, anchor=anchor)
    if not resolved:
        return f"看不懂時間範圍「{range}」，可以說剛剛／今天／昨天／上週／上個月／N 天前。"
    start, end, label, hits = resolved
    if getattr(ctx, "state", None) is not None:
        ctx.state.last_range = (start, end, label)        # 記住當錨：下一句「那時候」就接得回這段
    # 問「剛剛/最新」但最近一批其實不是剛剛（label 非「剛剛…」）→ 先誠實點破，免得把昨晚的當「剛剛」回（時間感不準）
    note = (f"（你問「剛剛/最新」，但這會兒其實沒有剛落進來的——最近一次是{label}）\n"
            if asked_recent and not label.startswith("剛剛") else "")
    if not hits:
        return f"{note}📂 {label}：那段你沒有記寫。"
    lines = [f"{note}📂 {label}，共 {len(hits)} 筆："]
    for r in hits[:40]:
        ts = analyzer.parse_ts(r.get("ts"))
        tag = _TYPE_TAG.get(r.get("type"), "")
        lab = r.get("topicLabel")
        txt = " ".join((r.get("text") or "").split())[:120]
        lines.append(f"・{_ts_label(ctx, ts, '%m/%d %H:%M')} "
                     f"{('〔' + lab + '〕') if lab else ''}{tag}{txt}".rstrip())
    return "\n".join(lines)


def api_cost(ctx, **_):
    m = ctx.meter
    if not m:
        return "（這台沒啟用花費追蹤。）"
    s = getattr(ctx, "state", None)
    rate = m.usd_twd or 1.0
    total_usd = (getattr(s, "cost_total_usd", 0.0) or 0.0) if s else 0.0          # 累計總估（跨重啟，永不歸零）
    day_usd = (getattr(s, "cost_since_digest_usd", 0.0) or 0.0) if s else 0.0     # 今天（自上次摘要）
    month_usd = (getattr(s, "cost_month_usd", 0.0) or 0.0) if s else 0.0          # 本月（跨月歸零）
    # 換月後、本月還沒有任何呼叫 → 別把上個月的數字當「本月」報（能判月就判；判不出就信存的值）。
    now, tz = getattr(ctx, "now", None), getattr(ctx, "tz", None)
    if s and now is not None and tz is not None:
        try:
            if getattr(s, "cost_month_key", None) != now.astimezone(tz).strftime("%Y-%m"):
                month_usd = 0.0
        except Exception:
            pass
    win_twd = m.window_twd()

    def _line(label, usd):                                # NT$ ＋（US$）並列＝直接對得上 Google 後台的美金數字
        return f"・{label}：約 NT${usd * rate:.1f}（US${usd:.2f}）"
    return ("💸 Gemini API 花費（依 token 估算、只計這台追蹤起的用量，非 Google 實際帳單）\n"
            + _line("本月以來", month_usd) + "\n"
            + _line("累計總估", total_usd) + "\n"
            + _line("今天（自上次摘要）", day_usd) + "\n"
            + f"・最近 {int(m.window_sec / 60)} 分鐘：約 NT${win_twd:.1f}（US${win_twd / rate:.2f}）")


# ── Gemini function declarations（LLM 看這個決定要叫哪個工具）─────────────
# 註：get_current_time 與 overall_stats 刻意**不**在此列——鐘錶問句、整體數字問句都改由 handle_message 的
# is_clock_question / is_stats_question fast-path **確定性**處理。放進 LLM 工具表會讓含時間詞的閒聊被抓去回時鐘、
# 讓任何漏接落 function-calling 的訊息被誤抓去吐 📊 報表（結構性誤吐，補了四次都還在）——拔掉可達性才是根治。
TOOL_DECLS = [
    {"name": "list_funnel_topics",
     "description": "列出某個漏斗階段的『主題清單』（不是數字）。問『我有哪些零散的念頭／哪些還沒成形／"
                    "進行中的是哪些』→ stage=scattered；『哪些快成形了／候選歷程』→ stage=context；"
                    "『哪些是學習歷程／已成形的』→ stage=journey；"
                    "『哪些卡住了／差一步／快升格／我該補什麼／下一步該做什麼／有哪些可以升格』→ stage=stuck。"
                    "凡是要『列出某階段有哪些主題』就用這個——別用 overall_stats（那只給數字）。",
     "parameters": {"type": "object", "properties": {
         "stage": {"type": "string", "enum": ["scattered", "context", "journey", "stuck"],
                   "description": "零散/進行中=scattered、快成形/候選歷程=context、學習歷程/已成形=journey、卡住/差一步/該補什麼=stuck"}}}},
    {"name": "recent_filings",
     "description": "回答『我最近的記寫歸到哪了／這些歸到哪一類|議題／最近歸戶到哪』——列出最近被背景歸戶的"
                    "記寫，依 大類｜議題 分組、標目前狀態（🌱進行中/🌿候選/🌳學習歷程）。",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "list_topic_records",
     "description": "列出某個主題（或全部）的記寫**內容**，含正確時間戳。問「某主題的紀錄/何時/完整列出/寫了什麼」用這個；"
                    "**topic 也可以是一個大類**（如『靈感／生活／研究／閱讀』那幾則說了什麼）——會列那大類底下各議題的內容；"
                    "問「第一筆/最早（那筆寫什麼）」設 earliest_only=true；"
                    "問「最近一次/最後一筆/上次/最近寫了什麼」設 latest_only=true（只回距今最後那筆）。"
                    "但問的是『第一筆到最新一筆**橫跨幾天**／持續多久／是不是橫跨 N 天』＝要**跨度**，請改用 topic_span（不是這個）。",
     "parameters": {"type": "object", "properties": {
         "topic": {"type": "string", "description": "主題或大類關鍵字；留空＝全部記寫"},
         "earliest_only": {"type": "boolean", "description": "只要最早（第一）一筆時設 true"},
         "latest_only": {"type": "boolean", "description": "只要最近（最後）一筆時設 true——「最近一次/最後一筆/上次寫了什麼」用這個"}}}},
    {"name": "topic_span",
     "description": "算某條線（或全部記寫）的**時間跨度**：第一筆→最後一筆＝橫跨幾天、共幾筆。"
                    "問「〔某主題〕第一筆到最新一筆橫跨幾天／這條線持續多久／從第一筆到現在多久／是不是橫跨 N 天」"
                    "用這個——別用 list_topic_records（那是列每一筆內容，不是算跨度）。topic 留空＝全部記寫的跨度。",
     "parameters": {"type": "object", "properties": {
         "topic": {"type": "string", "description": "主題或大類關鍵字；留空＝全部記寫的跨度"}}}},
    {"name": "days_since",
     "description": "算某個日期到現在是幾天（純日期相減）。問「6/13 到現在幾天了／X 號到今天差幾天／距離 6 月 13 號多久」用這個。",
     "parameters": {"type": "object", "properties": {
         "date": {"type": "string", "description": "日期，如 6/13、6月13日、2026/06/13"}}}},
    {"name": "topic_time_spans",
     "description": "各主題的時間跨度**排名**（第一筆→最後一筆，用真實時間算）。回答「**哪個**主題橫跨時間最久/最短」"
                    "（要比較多條、找出最久那條時用；只問單一條的跨度用 topic_span）。",
     "parameters": {"type": "object", "properties": {
         "order": {"type": "string", "enum": ["longest", "shortest"], "description": "預設 longest"}}}},
    {"name": "records_in_time_range",
     "description": "列出某時間範圍內的記寫。問「剛剛/最近三小時/今天/昨天/上週/上個月/N天前 我寫了什麼」用這個。"
                    "「剛剛」「剛才」原樣傳 range，會抓最近一段連續記寫（不是整天）。",
     "parameters": {"type": "object", "properties": {
         "range": {"type": "string", "description": "時間範圍詞，如 剛剛/剛才/3小時前/最近/今天/昨天/上週/上個月/3天前/5天內"
                                                    "（『剛剛』『剛才』請原樣傳，別改寫成『最近』）"}}}},
    {"name": "api_cost",
     "description": "回報這個 bot 的 Gemini API 估算花費（本次啟動以來/過去一天/最近視窗）。",
     "parameters": {"type": "object", "properties": {}}},
]

# 「引述記寫資料（證據）」的工具——回完資料後，緊接一則有時間感的人話。
# 純查詢（現在幾點/狀態/花費）不算引述證據，只回資料、不接人話。
EVIDENCE_TOOLS = {"list_topic_records", "topic_time_spans", "topic_span", "records_in_time_range",
                  "list_funnel_topics", "recent_filings"}

# 🚪 非證據（純查詢）工具表：問句**不是在查記寫資料**時，coach.ask 只給 LLM 這幾個，把 Drive 證據工具拔出可達性
# （同 get_current_time/overall_stats 移出 TOOL_DECLS 的根治法——拔可達性才是根治，非二元切斷工具路徑）。
# 單一真相：自動排除 EVIDENCE_TOOLS，日後增減證據工具此表跟著同步（目前剩 days_since、api_cost）。
NON_EVIDENCE_TOOL_DECLS = [d for d in TOOL_DECLS if d["name"] not in EVIDENCE_TOOLS]

_DISPATCH = {
    "get_current_time": get_current_time,
    "overall_stats": overall_stats,
    "list_funnel_topics": list_funnel_topics,
    "recent_filings": recent_filings,
    "list_topic_records": list_topic_records,
    "topic_time_spans": topic_time_spans,
    "topic_span": topic_span,
    "days_since": days_since,
    "records_in_time_range": records_in_time_range,
    "api_cost": api_cost,
}


def dispatch(name, args, ctx):
    """跑 LLM 選中的工具。未知工具或出錯回 None（呼叫端退回一般對話）。"""
    fn = _DISPATCH.get(name)
    if not fn:
        return None
    try:
        return fn(ctx, **(args or {}))
    except Exception as e:
        print(f"[datatools] {name} 失敗：{e}")
        return None
