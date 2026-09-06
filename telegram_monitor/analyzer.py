"""把讀到的記憶層資料算成一個 ``Snapshot``——監測端的全部判讀都在這裡。

不需要 embedding、不需要 LLM：升格判準（returnVisits / mediaKinds / semanticDensity）
LINE 背景**已算好存進每條 context 的 ``criteria``**；缺 criteria 時才用成員 record 的
``ts``/``type`` 重算前兩項（密度仍取既有 ``criteria.semanticDensity``，不重算向量）。
"""

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from . import thresholds as T

_MEDIA_LABEL = {
    "text": "文字", "link": "連結", "audio": "語音",
    "image": "圖片", "video": "影片", "file": "檔案",
    "sticker": "貼圖", "location": "地點",
}


# ── 時間解析（吃 ISO 字串與 epoch 數字兩種）──────────────────────────
def parse_ts(v):
    if v is None:
        return None
    if isinstance(v, datetime):
        return (v.replace(tzinfo=timezone.utc) if v.tzinfo is None else v).astimezone(timezone.utc)
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        if v <= 0:
            return None
        secs = v / 1000.0 if v > 1e11 else float(v)
        try:
            return datetime.fromtimestamp(secs, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        s = s.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            # 退路：剝掉小數秒再試
            s2 = re.sub(r"\.\d+", "", s)
            try:
                dt = datetime.fromisoformat(s2)
            except ValueError:
                return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    return None


def _media_kinds_from_members(members):
    """成員 record 的 type 中屬於內容媒介的去重集合。"""
    kinds = set()
    for r in members:
        t = r.get("type")
        if t in T.MEDIA_TYPES:
            kinds.add(t)
    return kinds


def _returns_from_members(members):
    """從成員 ts 重算 (returnVisits, returnSpanHours)，鏡射 evaluateContextCriteria_。"""
    tss = sorted(t for t in (parse_ts(r.get("ts")) for r in members) if t)
    if not tss:
        return 0, 0.0
    visits = 1
    gap = timedelta(minutes=T.RETURN_GAP_MINUTES)
    for i in range(1, len(tss)):
        if tss[i] - tss[i - 1] >= gap:
            visits += 1
    span_h = (tss[-1] - tss[0]).total_seconds() / 3600.0
    return visits, span_h


def context_title(c):
    ut = (c.get("userTitle") or "").strip()
    if ut:
        return ut
    cat = (c.get("category") or "").strip()
    lab = (c.get("label") or "").strip() or "(未命名)"
    return f"{cat}｜{lab}" if cat else lab


def pick_journey_for_context(journeys, cid):
    """鏡射 src/ContextStore.gs:205 pickJourneyForContext_ 的優先序。"""
    matches = [j for j in journeys if j.get("contextId") == cid]
    if len(matches) <= 1:
        return matches[0] if matches else None

    def key(j):
        status_rank = 0 if j.get("status") == "journey" else 1
        markers = -len(j.get("markers") or [])
        updated = parse_ts(j.get("updatedAt")) or datetime.min.replace(tzinfo=timezone.utc)
        return (status_rank, markers, -updated.timestamp(), j.get("createdAt") or "")

    return sorted(matches, key=key)[0]


def _context_metrics(c, record_by_id):
    """回傳該脈絡的 (returnVisits, spanHours, mediaKinds, density, coreFrac, present_kinds, members)。"""
    crit = c.get("criteria") or {}
    members = [record_by_id[rid] for rid in (c.get("recordIds") or []) if rid in record_by_id]

    rv = crit.get("returnVisits")
    span = crit.get("returnSpanHours")
    if rv is None or span is None:
        rv, span = _returns_from_members(members)

    present = _media_kinds_from_members(members)
    mk = crit.get("mediaKinds")
    if mk is None:
        mk = len(present)

    density = crit.get("semanticDensity")
    core = crit.get("coreFrac")
    return rv, span, mk, density, core, present, members


def _gap_phrases(rv, span, mk, density, core, present):
    """「還缺什麼」逐條句子，鏡射 contextGapReport_（src/ContextUpgrade.gs:574）。"""
    out = []
    if not T.density_passes(density, core):
        if density is None:
            out.append("語意再聚焦：密度尚未算出（背景整理後會有）")
        elif density >= T.DENSITY_FLOOR_FOR_FOCUS and core is not None:
            out.append(
                f"語意再聚焦：密度 {density:.3f} 已近門檻，但成員還不夠都扣同一核心"
                f"（聚焦 {round(core * 100)}%，需 ≥{round(T.DENSITY_FOCUS_CORE_FRAC_MIN * 100)}%）"
            )
        else:
            out.append(f"語意再聚焦：群內相似度 {density:.3f}，需 ≥{T.SEMANTIC_DENSITY_MIN}（內容更集中在同一件事）")
    if rv < T.RETURN_VISITS_MIN:
        out.append(
            f"意向回返：目前 {rv} 次，還要在不同時段（間隔 ≥{T.RETURN_GAP_MINUTES} 分）"
            f"再回到這主題 {T.RETURN_VISITS_MIN - rv} 次"
        )
    elif span is not None and span < T.RETURN_SPAN_HOURS_MIN:
        out.append(f"意向回返：首末需橫跨 ≥{T.RETURN_SPAN_HOURS_MIN}h（目前僅 {span:.1f}h）")
    if mk < T.MEDIA_KINDS_MIN:
        have = "、".join(_MEDIA_LABEL.get(t, t) for t in sorted(present)) or "無"
        out.append(f"跨媒介：目前 {mk} 種（{have}），再加 {T.MEDIA_KINDS_MIN - mk} 種（語音／圖片／影片／檔案）")
    return out


@dataclass
class Snapshot:
    funnel: dict
    heartbeat: dict
    gaps: list
    summary: dict
    formed_journeys: list = field(default_factory=list)
    formed_contexts: list = field(default_factory=list)
    near_upgrade_sig: dict = field(default_factory=dict)
    filed_records: list = field(default_factory=list)


def analyze(data, now_utc=None, tz=None, stall_grace_h=6, stall_require_dormant=True):
    now_utc = now_utc or datetime.now(timezone.utc)
    contexts = data.get("contexts") or []
    journeys = data.get("journeys") or []
    explorations = data.get("explorations") or []
    records = data.get("records") or []
    meta = data.get("meta") or {}
    record_by_id = {r["id"]: r for r in records if r.get("id")}

    formed_journeys = [j for j in journeys if j.get("status") == "journey"]
    formed_contexts = [c for c in contexts if c.get("status") == "context"]
    candidates = [c for c in contexts if c.get("status") == "candidate"]
    watch = [j for j in journeys if j.get("status") == "watch"]

    funnel = {
        "candidate": len(candidates),
        "context": len(formed_contexts),
        "journey": len(formed_journeys),
        "watch": len(watch),
    }

    # ── 意向 ↔ 行為缺口 ──────────────────────────────────────────────
    gaps = []
    near_sig = {}
    jbc = {c.get("id"): pick_journey_for_context(journeys, c.get("id")) for c in contexts}

    for c in contexts:
        cid = c.get("id")
        status = c.get("status")
        rv, span, mk, density, core, present, members = _context_metrics(c, record_by_id)
        density_pass = T.density_passes(density, core)
        return_pass = rv >= T.RETURN_VISITS_MIN and (span is not None and span >= T.RETURN_SPAN_HOURS_MIN)
        media_pass = mk >= T.MEDIA_KINDS_MIN
        met = sum((density_pass, return_pass, media_pass))
        title = context_title(c)
        last_ts = parse_ts(c.get("lastTs"))

        if status == "context":
            jrn = jbc.get(cid)
            if not jrn or jrn.get("status") != "journey":
                gaps.append({
                    "kind": "watch", "leverage": 2, "cid": cid, "title": title,
                    "last_ts": last_ts,
                    "line": f"{T.STATE_ICON['journey']} 〈{title}〉已成形脈絡，但還沒抓到轉折"
                            f"——回去寫一句「所以我學到…／下一步…」就能升成學習歷程",
                })
            continue

        if status == "candidate":
            if met >= 2:
                near_sig[cid] = f"{met}/3"
                miss = _gap_phrases(rv, span, mk, density, core, present)
                intent = f"回返 {rv} 次（意向強），" if return_pass else ""
                gaps.append({
                    "kind": "near", "leverage": 3, "cid": cid, "title": title,
                    "last_ts": last_ts,
                    "line": f"{T.STATE_ICON['context']} 〈{title}〉{intent}只差：" + "；".join(miss),
                })
            elif rv <= 1 and len(members) >= 2:
                gaps.append({
                    "kind": "unreturned", "leverage": 1, "cid": cid, "title": title,
                    "last_ts": last_ts,
                    "line": f"{T.STATE_ICON['candidate']} 〈{title}〉只碰過 1 次（意向未回返）"
                            f"——想留住的記得回來補",
                })

    # exploration：宣告了卻記得少（意向宣告、行為偏薄）
    exp_record_counts = Counter(r.get("explorationId") for r in records if r.get("explorationId"))
    active_explorations = 0
    for e in explorations:
        if e.get("status") == "active":
            active_explorations += 1
        n = exp_record_counts.get(e.get("id"), len(e.get("recordIds") or []))
        if e.get("status") == "closed" and n < T.EXPLORATION_THIN_RECORDS:
            gaps.append({
                "kind": "exploration", "leverage": 1, "cid": e.get("id"),
                "title": e.get("label") or "(未命名探索)", "last_ts": parse_ts(e.get("endTs")),
                "line": f"🧭 探索〈{e.get('label') or '未命名'}〉只記了 {n} 筆"
                        f"——下次想深入可多留幾筆",
            })

    gaps.sort(key=lambda g: (-g["leverage"], -(g["last_ts"].timestamp() if g["last_ts"] else 0)))

    # ── 背景心跳健康 ─────────────────────────────────────────────────
    last_upgrade = parse_ts(meta.get("lastContextUpgradeAt"))
    last_ingest = parse_ts(meta.get("lastIngestTs"))
    last_classify = parse_ts(meta.get("lastClassifyAt"))
    newest_input = max([t for t in (last_ingest, last_classify) if t], default=None)
    # 🫀 背景「還活著」的最新跡象＝任一處理步驟（升格／ingest／分類-歸戶）最近一次活動。
    # 修誤報：原本只用 lastContextUpgradeAt（升格/整理）當基準判 stalled，但**升格是事件驅動、罕見**（脈絡累積夠
    # 才升格），而 **ingest／分類（歸戶）每次 sweep 都在跑**。只要使用者寫了新東西卻 >grace 沒升格，就誤報「背景卡住」
    # （實測：backgroundSweep 每 5 分鐘成功跑、歸戶也正常，卻一直 ⚠️）。改成：`stall_require_dormant`（預設開）下，
    # 只有「連 ingest／分類 都 >grace 沒動」＝sweep 真的停了才算 stalled；ingest／分類最近還在動＝背景活著、頂多 pending
    # （排隊升格），不報卡住。設 stall_require_dormant=False → 退回原「只看升格 age」＝逐位元同舊行為。
    last_active = max([t for t in (last_upgrade, last_ingest, last_classify) if t], default=None)

    if not records and last_ingest is None:
        heartbeat = {"status": "idle", "pending": False, "age_hours": None,
                     "last_upgrade": last_upgrade, "last_ingest": last_ingest}
    else:
        if last_upgrade is None:
            pending = newest_input is not None
            ref = newest_input
        else:
            pending = newest_input is not None and newest_input > last_upgrade
            ref = last_upgrade
        age_hours = (now_utc - ref).total_seconds() / 3600.0 if ref else None     # 顯示用：距上次「升格/整理」多久
        # stalled 用「背景活著跡象」age（last_active＝任一處理步驟最近活動）；旗標關＝退回只看升格的 age_hours（同舊）。
        stall_ref = last_active if (stall_require_dormant and last_active) else ref
        stall_age = (now_utc - stall_ref).total_seconds() / 3600.0 if stall_ref else None
        if pending and stall_age is not None and stall_age > stall_grace_h:
            status = "stalled"
        elif pending:
            status = "pending"
        else:
            status = "healthy"
        heartbeat = {"status": status, "pending": pending, "age_hours": age_hours,
                     "last_upgrade": last_upgrade, "last_ingest": last_ingest}

    # ── 整體記寫表現摘要 ─────────────────────────────────────────────
    rec_ts = sorted(t for t in (parse_ts(r.get("ts")) for r in records) if t and t <= now_utc)
    last24h = sum(1 for t in rec_ts if t >= now_utc - timedelta(hours=24))
    last7d = sum(1 for t in rec_ts if t >= now_utc - timedelta(days=7))
    media = Counter(r.get("type") for r in records if r.get("type"))
    streak = _streak(rec_ts, now_utc, tz)
    summary = {
        "total": len(records),
        # 日曆日不是滾動 24 小時；沒有時區時不冒充使用者的「今天」。
        "today": (sum(1 for t in rec_ts if t <= now_utc
                      and t.astimezone(tz).date() == now_utc.astimezone(tz).date())
                  if tz is not None else None),
        "last24h": last24h,
        "last7d": last7d,
        "streak": streak,
        "media": dict(media),
        "last_write": rec_ts[-1] if rec_ts else None,
        "last_organize": last_upgrade,
        "active_explorations": active_explorations,
    }

    # ── 記寫歸戶（每筆記寫被背景歸到哪條 大類｜議題、目前狀態）────────────
    # (category|label) → 狀態（journey>context>candidate；該脈絡若已有 journey 視為 journey）
    rank = {"candidate": 1, "context": 2, "journey": 3}
    status_by_key = {}
    for c in contexts:
        key = (c.get("category") or "") + "|" + (c.get("label") or "")
        st = c.get("status")
        jrn = jbc.get(c.get("id"))
        if jrn and jrn.get("status") == "journey":
            st = "journey"
        if key not in status_by_key or rank.get(st, 0) > rank.get(status_by_key[key], 0):
            status_by_key[key] = st
    filed_records = []
    for r in records:
        lab = r.get("topicLabel")
        if not lab:
            continue  # 還沒被背景分類（歸戶未定）→ 等下輪
        cat = r.get("category")
        key = (cat or "") + "|" + lab
        filed_records.append({
            "id": r.get("id"), "ts": parse_ts(r.get("ts")),
            "category": cat, "topicLabel": lab, "key": key,
            "status": status_by_key.get(key, "candidate"),
        })

    return Snapshot(
        funnel=funnel, heartbeat=heartbeat, gaps=gaps, summary=summary,
        formed_journeys=formed_journeys, formed_contexts=formed_contexts,
        near_upgrade_sig=near_sig, filed_records=filed_records,
    )


def _streak(rec_ts, now_utc, tz):
    """連續記寫天數（當地時區），以今天或昨天為錨往回數。"""
    if not rec_ts or tz is None:
        return 0
    days = {t.astimezone(tz).date() for t in rec_ts}
    today = now_utc.astimezone(tz).date()
    if today in days:
        anchor = today
    elif (today - timedelta(days=1)) in days:
        anchor = today - timedelta(days=1)
    else:
        return 0
    n = 0
    d = anchor
    while d in days:
        n += 1
        d -= timedelta(days=1)
    return n


def media_label(t):
    return _MEDIA_LABEL.get(t, t)
