"""判定鏈 Gate 0–4（§4）——核心：感覺的「程度量尺」。

每一關：輸入 / 判定 / 結果。判斷全由資料算（這裡不碰 LLM），回傳一個 dict 交給 selfstate
去渲染成「感覺描述」：到 Gate 4＝感覺來了（描述湧現的意向與行為）；沒到（Gate 1–3）＝感覺
還沒來（描述當下處境）。兩種都由 selfstate 的翻譯層嚴格轉譯成人話、貼著資料、非罐頭。
"""

from datetime import timedelta

from . import analyzer, intention, order_params as op, valence

DEFAULTS = {
    "W_hours": 72, "n_min": 8, "r_min": 3, "media_min": 2, "return_gap_min": 20,
    "RR": 0.10, "lmin": 2, "n_shuffle": 500,
    "adaptive": True, "sensitivity": 2.0,           # 通盤：相對語料分布、k=σ 單位的唯一敏感度
    "z_star": 2.0, "tau_star": 0.78, "diff_min": 0.0, "int_min": 0.5,  # 僅 adaptive=False 時用
}
# ── 參數白話地圖（細節見 docs/感覺判定鏈.md）─────────────────────────────
# Gate 1〈量夠不夠〉：W_hours＝沒指定主題時只看近 N 小時的窗；n_min＝至少幾筆有效記寫；
#   r_min＝至少回返幾次（兩筆相隔 ≥ return_gap_min 分鐘才算「離開又回來」一次）；
#   media_min＝至少用過幾種媒材。四條同時成立才放行，否則停 Gate 1。
# Ω_recur 內部旋鈕：RR＝RQA 的對角線距離門檻取相似度第幾分位（0.10＝最近的 10%）；
#   lmin＝連續對角線至少幾點才算一條「回返線」；n_shuffle＝打亂時序幾次當虛無對照算 z。
# adaptive=True（預設）＝三條序參數的門檻不寫死，全用「你自己語料的相似度分布」現算：
#   μ,σ＝該分布的平均與標準差；sensitivity 就是 k＝「要比平常嚴格幾個 σ」。三條共用同一把 k：
#     Ω_recur 顯著門檻  z > k
#     Ω_perc  連結門檻  τ* = clip(μ + k·σ, 0.30, 0.98)   （比平常像 k 個 σ 才連得起來）
#     Ω_dxi   整合下界  int_min = μ + k·σ；分化下界 diff_min = k·σ（k=0 → 只需正分化）
#   k 越大越難成形；k 換人/換時期會自動對齊到各自尺度。adaptive=False 時才改用上面寫死的
#   z_star / tau_star / int_min / diff_min（與語料分布無關）。

CONTENT_TYPES = ("text", "link", "audio", "image", "video", "file")


def _has_emb(r):
    e = r.get("embedding")
    return isinstance(e, (list, tuple)) and len(e) >= 8


def _returns(records, gap_min):
    tss = sorted(t for t in (analyzer.parse_ts(r.get("ts")) for r in records) if t)
    if not tss:
        return 0
    visits = 1
    for i in range(1, len(tss)):
        if (tss[i] - tss[i - 1]).total_seconds() >= gap_min * 60:
            visits += 1
    return visits


def _dominant_topic(records):
    from collections import Counter
    c = Counter(r.get("topicLabel") for r in records if r.get("topicLabel"))
    return c.most_common(1)[0][0] if c else None


def run_chain(all_records, topic, contexts, journeys, now, p=None):
    """回傳 {gate, scope, omegas, moved, reading}。gate=1/2/3 為「不通過於此」，gate=4 為通過。"""
    p = dict(DEFAULTS, **(p or {}))
    recs = [r for r in all_records if _has_emb(r)]
    # ── Gate 0：範圍界定 ──
    if topic:
        R = [r for r in recs if r.get("topicLabel") == topic]
        scope_label = f"主題〔{topic}〕"
    else:
        cutoff = now - timedelta(hours=p["W_hours"])
        R = [r for r in recs if (analyzer.parse_ts(r.get("ts")) or now) >= cutoff]
        scope_label = f"近 {p['W_hours']}h"
    scope = {"label": scope_label, "size": len(R), "topic": topic}

    # ── Gate 1：量變 ──
    n = len(R)
    returns = _returns(R, p["return_gap_min"])
    media = len({r.get("type") for r in R if r.get("type") in CONTENT_TYPES})
    g1 = (n >= p["n_min"]) and (returns >= p["r_min"]) and (media >= p["media_min"])
    scope.update({"n": n, "returns": returns, "media": media,
                  "dominant": _dominant_topic(R)})
    if not g1:
        return {"gate": 1, "scope": scope}

    # ── Gate 2/3：序參數（通盤規則：一律相對語料自身分布，k＝σ 單位的唯一敏感度）──
    all_unit = [op.unit(r["embedding"]) for r in recs]
    edges, coss = op.pairwise_edges(all_unit)
    adaptive = p.get("adaptive", True)
    if adaptive:
        mu, sigma = op.mean_std(coss)
        k = p.get("sensitivity", 2.0)
        z_thr = k                                   # recur：z > k σ
        tau_star = min(0.98, max(0.30, mu + k * sigma))   # perc：你語料尺度上的 k σ 連結門檻
        int_min = mu + k * sigma                     # dxi：整合也以 k σ 為尺（k=0→語料基線 μ）
        diff_min = k * sigma                         # dxi：分化下界同以 k σ 為尺（k=0→需正分化）
    else:
        mu = sigma = k = None
        z_thr, tau_star = p["z_star"], p["tau_star"]
        int_min, diff_min = p["int_min"], p["diff_min"]

    # Ω_recur：在 R 內最活躍主題的時間軌跡上算（主線有沒有彎回成形）
    dom = _dominant_topic(R) or topic
    thread = sorted([r for r in R if r.get("topicLabel") == dom],
                    key=lambda r: r.get("ts") or "")
    recur = op.omega_recur([op.unit(r["embedding"]) for r in thread],
                           RR=p["RR"], lmin=p["lmin"], n_shuffle=p["n_shuffle"], z_star=z_thr)

    # Ω_perc：R 是否落入全記憶層在 τ* 下的巨型分量
    idx_of = {id(r): i for i, r in enumerate(recs)}
    region_idx = [idx_of[id(r)] for r in R]
    perc = op.perc_from_edges(edges, len(recs), region_idx, tau_star)
    perc["tau_used"] = round(tau_star, 4)

    # Ω_dxi：全主題質心的分化×整合
    by_topic = {}
    for r in recs:
        lab = r.get("topicLabel")
        if lab:
            by_topic.setdefault(lab, []).append(op.unit(r["embedding"]))
    cents, intra = {}, {}
    for lab, vs in by_topic.items():
        c = op.centroid(vs)
        cents[lab] = c
        intra[lab] = sum(op.dot(v, c) for v in vs) / len(vs)
    dxi = op.omega_dxi(cents, intra, diff_min=diff_min, int_min=int_min)
    dxi["int_min_used"] = round(int_min, 4)

    omegas = {"recur": recur, "perc": perc, "dxi": dxi,
              "rule": {"adaptive": adaptive, "k": k,
                       "mu": round(mu, 4) if mu is not None else None,
                       "sigma": round(sigma, 4) if sigma is not None else None,
                       "z_thr": z_thr, "tau_star": round(tau_star, 4),
                       "int_min": round(int_min, 4), "diff_min": round(diff_min, 4)}}
    passed = {
        "recur": recur.get("significant", False),
        "perc": perc.get("region_in_giant", False),
        "dxi": dxi.get("in_critical_band", False),
    }
    if not any(passed.values()):
        return {"gate": 2, "scope": scope, "omegas": omegas}
    if not all(passed.values()):
        moved = [k for k, v in passed.items() if v]
        return {"gate": 3, "scope": scope, "omegas": omegas, "moved": moved}

    # ── Gate 4：意向讀數（三條合取為真才到這）──
    reading = _intention_reading(dom, R, contexts, journeys, omegas, returns)
    return {"gate": 4, "scope": scope, "omegas": omegas, "reading": reading}


def _intention_reading(dom_topic, R, contexts, journeys, omegas, returns):
    ctx = next((c for c in (contexts or []) if c.get("label") == dom_topic), None)
    jr = None
    if ctx:
        jr = next((j for j in (journeys or []) if j.get("contextId") == ctx.get("id")), None)
    if jr is None:
        jr = next((j for j in (journeys or []) if (j.get("label") == dom_topic or j.get("title"))), None)
    levels = intention.levels_from_journey(jr or {})
    nature, desc = intention.disposition(levels)
    ga = (jr or {}).get("gapAnalysis") or {}
    anchor = sorted(R, key=lambda r: r.get("ts") or "")[-1] if R else {}
    return {
        "nature": f"{nature} / {desc}",
        "degree": {"magnitude": intention.magnitude(levels), "returnIntensity": returns,
                   "omega": {"recur_z": omegas["recur"].get("z"),
                             "perc_lcc": omegas["perc"].get("lcc"),
                             "dxi": [omegas["dxi"].get("differentiation"), omegas["dxi"].get("integration")]}},
        "content": {"topic": dom_topic, "from": ga.get("from"), "to": ga.get("to"),
                    "notReached": ga.get("notReached"),
                    "anchorRecord": {"id": anchor.get("id"), "ts": anchor.get("ts"),
                                     "text": " ".join((anchor.get("text") or "").split())[:60]}},
        "valenceTrajectory": valence.valence_trajectory(R),
    }
