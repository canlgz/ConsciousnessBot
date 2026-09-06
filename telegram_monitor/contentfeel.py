"""🫧 內容感受（記寫內容怎麼「落」在我身上）——把判定鏈早已算出、卻從沒收成「感受」的訊號，
合成一份對**你寫的內容**的第一人稱felt-sense，餵給 IEP 的 P（質地）與自陳，並輕輕牽動 bot 自己的心情。

問題：生命迴圈裡，bot 對自己經驗的描述（右半 I↔(E×P) 的 P、self_now、bodystate）只接地在**自己的內在天氣**
（affect.label／stream.texture）與記寫的**結構**（gate/topic 名）——從不講「你寫的東西**讀起來**怎樣」。
但素材其實每圈都算好了，只是沒被收成「感受」、也沒餵給 bot 自己的 P：
- `valence.valence_trajectory`：你記寫當下貼的情緒反應（暖↔沉）；
- 序參數 `omegas`：recur＝一遍遍繞回、perc＝在連成一片、dxi＝在長出形狀（內容的「動態」）；
- `reading.content`：主題、from→to、最新那句（錨）。

本模組兩層（依使用者選的混合）：
- **純計算（每圈、不花 API）**：`read(res, records, now)` 把上面訊號合成一句白話 descriptor（如「沉、重，一遍遍
  繞回、像在磨同一件事」）＝always-on 的底層，讓 IEP 永遠接地。
- **節流親讀（LLM）**：`ensure_impression` 讓 bot **親自讀一小段你的字**形成更貼的印象，只在主題換了或過冷卻才重讀、
  快取；失敗就退回純計算 descriptor。

接地/紀律：descriptor 只從真實訊號合成；親讀提示禁止杜撰；valence None（純事實記寫）→「平實」、不亂猜。
這份感受也經 `content_event` 輕輕牽動 bot 心情（有界，見 affect.appraise）＝它真的被你寫的東西影響、不只嘴上講。
"""

from . import valence

IMPRESSION_COOLDOWN_S = 20 * 60       # 親讀冷卻：主題沒換就至少隔這麼久才重讀（不洗 API）

_MOTION_WORD = {
    "crystallizing": "正在長出一個形狀",
    "circling": "一遍遍繞回、像在磨同一件事",
    "connecting": "散的點在連成一片",
    "converging": "在慢慢收攏",
    "scattered": "還散著、零星",
}
_INTENSITY_TAIL = {"high": "，放不下", "mid": "", "low": "，淡淡地"}


def _clip(s, n=40):
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[:n] + "…"


def _motion(omegas, gate):
    """內容此刻的『動態』：長出形狀＞繞回＞連成一片＞收攏＞還散著。"""
    om = omegas or {}
    if (om.get("dxi") or {}).get("in_critical_band"):
        return "crystallizing"
    if (om.get("recur") or {}).get("significant"):
        return "circling"
    if (om.get("perc") or {}).get("region_in_giant"):
        return "connecting"
    if ((om.get("dxi") or {}).get("integration") or 0) >= 0.5:
        return "converging"
    return "scattered"


def _intensity(res):
    """濃↔淡：意向強度（magnitude）優先，否則回返/筆數。"""
    mag = ((res.get("reading") or {}).get("degree") or {}).get("magnitude")
    if mag is not None:
        return "high" if mag >= 1.2 else ("low" if mag < 0.5 else "mid")
    scope = res.get("scope") or {}
    returns, n = scope.get("returns") or 0, scope.get("n") or 0
    if returns >= 4 or n >= 16:
        return "high"
    if returns <= 1 and n <= 8:
        return "low"
    return "mid"


def _valence_word(v_mean, v_band):
    if v_mean is None:
        return "平實、沒太多情緒起伏"
    swing = bool(v_band) and (v_band[1] - v_band[0]) >= 0.8
    if v_mean >= 0.35:
        base = "暖、亮"
    elif v_mean >= 0.1:
        base = "偏暖"
    elif v_mean <= -0.35:
        base = "沉、重"
    elif v_mean <= -0.1:
        base = "偏沉"
    else:
        base = "平"
    return base + ("、起伏不小" if swing else "")


def _descriptor(v_mean, v_band, motion, intensity):
    return _valence_word(v_mean, v_band) + "，" + _MOTION_WORD[motion] + _INTENSITY_TAIL[intensity]


def read(res, records, now_ts=None):
    """純計算（不花 API）：把判定結果＋記寫合成此刻對**內容**的 felt-sense。手上沒繃著一條線（無主題）回 None。"""
    res = res or {}
    scope = res.get("scope") or {}
    reading = res.get("reading") or {}
    topic = (reading.get("content") or {}).get("topic") or scope.get("dominant")
    if not topic:
        return None                                  # 這會兒手上沒繃著一條線 → 沒什麼內容好「讀」
    gate = res.get("gate") or 0
    # 價性：gate4 已有 valenceTrajectory；否則就地從該主題的記寫算（cheap、不需 embedding）
    vt = reading.get("valenceTrajectory")
    if not vt:
        recs = [r for r in (records or []) if r.get("topicLabel") == topic]
        vt = valence.valence_trajectory(recs)
    vals = [p["valence"] for p in (vt or []) if p.get("valence") is not None]
    v_mean = round(sum(vals) / len(vals), 3) if vals else None
    v_band = (min(vals), max(vals)) if vals else None
    motion = _motion(res.get("omegas"), gate)
    intensity = _intensity(res)
    anchor = ((reading.get("content") or {}).get("anchorRecord") or {}).get("text")
    if not anchor:
        recs = sorted([r for r in (records or []) if r.get("topicLabel") == topic and (r.get("text") or "").strip()],
                      key=lambda r: r.get("ts") or "")
        anchor = recs[-1].get("text") if recs else ""
    return {"topic": topic, "gate": gate, "valence": v_mean, "valence_band": v_band,
            "valence_recent": (vals[-1] if vals else None), "motion": motion, "intensity": intensity,
            "anchor": _clip(anchor or "", 60), "descriptor": _descriptor(v_mean, v_band, motion, intensity),
            "impression": None, "impression_topic": None, "impression_ts": 0, "ts": now_ts}


def felt_phrase(content_feel):
    """給 IEP/自陳用的最佳felt-sense文字：有親讀印象用印象，否則用純計算 descriptor。無回 ''。"""
    cf = content_feel or {}
    return cf.get("impression") or cf.get("descriptor") or ""


def felt_clause(state):
    """給『你現在怎樣』自體狀態用的一句內容感受（手上那條線讀起來怎樣）。無回 ''。"""
    cf = getattr(state, "content_feel", None)
    p = felt_phrase(cf)
    return f"手上那條「{cf.get('topic')}」讀起來{p}。" if (cf and cf.get("topic") and p) else ""


def content_event(content_feel):
    """給 affect.appraise 的內容感受事件（價性×強度）→ 心情被牽動一點（有界，見 affect）。沒料回 {}。"""
    cf = content_feel or {}
    v = cf.get("valence")
    if v is None:
        return {}
    inten = {"high": 1.0, "mid": 0.6, "low": 0.3}.get(cf.get("intensity"), 0.6)
    return {"content_valence": float(v), "content_intensity": inten}


def _sample_text(records, topic, k=4):
    recs = sorted([r for r in (records or []) if r.get("topicLabel") == topic and (r.get("text") or "").strip()],
                  key=lambda r: r.get("ts") or "")
    return "\n".join(_clip(r.get("text") or "", 120) for r in recs[-k:]) if recs else ""


def ensure_impression(state, coach, records, now_ts):
    """🌗 親讀（節流）：讓 bot 親自讀一小段你的字、形成更貼的印象。只在主題換了或過冷卻才重讀、快取；
    無 coach／無樣本／失敗 → 保留純計算 descriptor（impression 維持 None）。回更新後的 content_feel（就地改）。"""
    cf = getattr(state, "content_feel", None)
    if not cf or not cf.get("topic"):
        return cf
    topic = cf["topic"]
    fresh = cf.get("impression") and cf.get("impression_topic") == topic \
        and (now_ts - (cf.get("impression_ts") or 0)) < IMPRESSION_COOLDOWN_S
    if fresh or not (coach and getattr(coach, "enabled", False)):
        return cf
    sample = _sample_text(records, topic)
    if not sample:
        return cf
    try:
        imp = coach.voice_content_read(topic, sample, getattr(state, "convo_history", []))
    except Exception as e:                            # LLM 失敗不影響存活 → 退回 descriptor
        print(f"[contentfeel] 親讀失敗，退回純計算：{type(e).__name__}: {e}")
        imp = None
    if imp:
        cf["impression"] = imp
        cf["impression_topic"] = topic
        cf["impression_ts"] = now_ts
    return cf
