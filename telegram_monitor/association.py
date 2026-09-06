"""💡 聯想湧現（Associative Emergence / Aha-Moment）——跨主題、資料接地的**生成性**連結。

與既有四者刻意區隔：
- contentfeel（🫧 對你**記寫內容**的感覺，主題**內**）、determination Gate 4（主題**內**的意向湧現）、
  experience（🌀 bot **自身狀態**的吸子）、volition（🎯 自發**目標**）。
- 本層是**不同主題**記寫在語意向量上的**交互碰撞**：縱觀整片記寫脈絡，據真實片段**冒出一個使用者從沒寫過的
  火花/構想**（一個假設），bot 以 Aha 的口吻說一句。

**三種創意操作（axis 1，由資料結構自選）＋四種靈感類型（axis 2，由訊號分類）**：
- 操作：**融入 blend**（兩主題越靠越近＝併成新東西）／**跳躍 leap**（兩主題很遠，卻在滲流圖經一筆**踏腳石**真實
  記寫接上＝隱藏的接點）／**衝突 conflict**（兩主題相近、價性卻相反＝張力暗示新點子）。
- 類型：問題導向（端點是 concerns 痛點）／頓悟直覺（閒置自我刺激繞回）／生活觀察（跨大類借鑑）／情感共鳴（強價性）。

**兩段、LLM 只在後段介入**：
1. **累積／沉澱（本檔，純計算、全程無 LLM）＝湧現的條件本身**：每圈把帶 embedding 的記寫（片段）依主題凝成質心
   → 三種偵測器各產出帶 `kind` 的候選 →（每個 (pair,kind) 橋）方向 EMA、強度隨支撐成長、過時半衰減；強度＋支撐
   ＋連續確認＋本圈被觸發＝湧現條件（一圈只發跨所有 kind 最強的那條）。
2. **Aha 表達（不在本檔）**：湧現事件交 `coach.voice_insight` 渲染（據真實片段提一個火花、明標假設、不杜撰）。

純記憶體＋小摘要跨重生（鏡射 experience.Experience）；純函式、可測；不碰 LLM、不碰 I/O。
向量數學一律重用 `order_params`（unit/dot/centroid/mean_std，餘弦＝單位向量內積）。

**漣漪式湧現（內外搭配，association_ripple_*）**：observe 額外吃一組 `ripple_topics`（此刻「活著」的真實主題標籤）
＝外部對話與自我遞迴的**弱觸發源**——「一顆石頭丟進湖面產生漣漪」：
- 外石＝使用者最近窗內**對話**提到、且**存在於 cents**的真實主題（`conversation_ripple_topics`：標籤完整子字串命中對話文字為主、
  `datatools.best_topic` 在高門檻時補強，最終一律 ∩ cents）。
- 遞迴石＝最近**自己湧現過的聯想**端點（`recursive_ripple_topics`：拆 recent_insights 的 pair、∩ cents）＝上一個聯想擾動下一個＝思緒鏈。
接地不變式：漣漪只**活化既有真實橋**（端點本就 ∈ cents），絕不造對話節點/主題；湧現內容仍引用真實記寫錨點；
漣漪 gain（`_RIPPLE_GAIN`）必 < new_record gain（`_STRENGTH_GAIN`）＝對話一句是擾動、非新資料；
**加性可關**：`ripple_topics=None` → 與現狀逐位元相同；漣漪觸發只在本圈未被 new_record/revisit 觸發時施加（嚴格 elif）。
"""

import math
from collections import Counter

from . import datatools
from . import order_params as op
from . import plasticity
from . import valence as val

# ── 累積/沉澱參數（realistic 預設；觀察模式 easy 在呼叫端覆寫成寬鬆值）──────────────
_MIN_TOPIC_RECS = 4        # 一個主題至少幾筆帶 embedding 的記寫，其質心才算數
_MIN_TOPICS = 2            # 至少兩個不同主題才談得上「跨主題」
_MIN_PAIR_GAP = 0.04       # 兩質心至少差這麼多 cos（cos ≤ 1−gap）才算「不同主題」（排除同義改名幾乎重合）
_COS_FLOOR = 0.15          # 橋的**絕對**正向靠近地板：cos 必須 ≥ max(τ, _COS_FLOOR)（主題少時 σ→0、μ 退化，仍不讓正交主題成橋）
_K_FLOOR = 0.0             # 自適應門檻 τ=μ+max(k,_K_FLOOR)·σ 的 k 地板（k 可被 affect 拉到負，不讓門檻退化）
_DIR_EMA = 0.2             # 橋方向 EMA（新支撐慢慢把橋方向往現況拉；比照 experience._ema a=0.25）
_HALFLIFE_LAPS = 120       # 沒有新支撐時橋強度的半衰期（圈）→ 過時的橋自然淡出
_DECAY_PER_LAP = 0.5 ** (1.0 / _HALFLIFE_LAPS)
_STRENGTH_GAIN = 0.34      # 每有一筆新記寫落在橋上 → 強度往上推（約三筆撐起一條橋）
_RIPPLE_GAIN = 0.17        # 漣漪觸發＝對話/前一聯想的**弱擾動**（≈_STRENGTH_GAIN 之半）；必 < _STRENGTH_GAIN——
                           # 漣漪只調「哪條橋、何時」湧現，不憑空造洞見；對話一句不等於一筆新資料
_STRENGTH_EMERGE = 0.66    # 強度過此才夠格湧現（≈ 兩三次支撐沉澱）
_STRENGTH_FLOOR = 0.04     # 強度低於此（且未湧現過）→ 汰除
_MIN_SUPPORT = 3           # 至少這麼多筆片段沉澱過，才算「累積夠」（確保是跨時間沉澱、非單拍）
_CONFIRM_LAPS = 4          # 強度連續站穩這麼多拍才算湧現（防抖）
_BRIDGES_CAP = 64          # 最多同時追蹤幾條橋（環形上限、汰弱）

# 觀察模式（easy）的寬鬆值：讓 Aha 幾圈內就湧現，供人親眼觀察
_EASY_GAIN = 1.0
_EASY_STRENGTH_EMERGE = 0.05
_EASY_TAU_FLOOR = 0.1      # easy：τ 放寬為 max(_EASY_TAU_FLOOR, μ)，正向靠近即算候選

# ── 三種創意操作（axis 1）的偵測門檻（realistic 預設＋easy 對照）───────────────────
# 跳躍 leap：兩主題質心**遠**（cos 在 blend 帶之下），卻有一筆第三條線的真實記寫剛好夾在中間把它們接起來
_LEAP_COS_FLOOR = 0.0      # 遠帶下界：cos ≥ 此（排除反平行噪音），且 < blend τ
_LEAP_STONE_FLOOR = 0.35   # 踏腳石：那筆真實記寫對兩質心的 min(餘弦) 要 ≥ 此，才算真的「中間搭得上」、非雜訊
_EASY_LEAP_COS_FLOOR = -0.1
_EASY_LEAP_STONE_FLOOR = 0.15
# 衝突 conflict：兩主題**相近**（cos 高 或 同大類）但平均價性**正負相反**且夠強夠多支撐
_CONF_MIN_SUPPORT = 2      # 每側至少這麼多筆有情緒登錄的記寫（非單顆貼圖抖動）
_CONF_MAG = 0.34           # 每側 |平均價性| 要 ≥ 此（暖/沉是真的，不是微弱雜訊）
_EASY_CONF_MIN_SUPPORT = 1
_EASY_CONF_MAG = 0.1
# 情感共鳴型（axis 2）：火花核心 |價性| ≥ 此才貼「emotion」（magnitude 也讓它在分數上勝出＝資料自選）
_ITYPE_EMO_MAG = 0.5


def _has_emb(r):
    e = r.get("embedding")
    return isinstance(e, (list, tuple)) and len(e) >= 8


def _clip(s, n=80):
    t = " ".join((s or "").split())
    return (t[:n] + "…") if len(t) > n else t


# ── 純計算：片段 → 每主題質心 ──────────────────────────────────────────────
def topic_centroids(full, min_recs=_MIN_TOPIC_RECS):
    """把帶 embedding 的記寫（片段）依 topicLabel 分群、凝成質心（鏡射 determination 的 by_topic）。
    回 {label: {"centroid": unit_vec, "members": [(unit_vec, text, ts, valence, category)…], "intra": 群內凝聚}}；
    member 多帶 valence（衝突偵測用，None＝無情緒登錄）與 category（生活觀察跨大類用）；筆數不足的主題丟掉。"""
    by_topic = {}
    for r in (full or []):
        lab = r.get("topicLabel")
        if not lab or not _has_emb(r):
            continue
        by_topic.setdefault(lab, []).append(
            (op.unit(r["embedding"]), r.get("text") or "", r.get("ts"), val.valence_of(r), r.get("category")))
    cents = {}
    for lab, members in by_topic.items():
        if len(members) < min_recs:
            continue
        vs = [m[0] for m in members]
        c = op.centroid(vs)
        if c is None:
            continue
        cents[lab] = {"centroid": c, "members": members,
                      "intra": round(sum(op.dot(v, c) for v in vs) / len(vs), 4)}
    return cents


def candidate_bridges(cents, k, min_pair_gap=_MIN_PAIR_GAP, easy=False):
    """跨『不同』主題對的橋候選：對所有 i<j 算質心餘弦 op.dot；用跨對 cos 分布的 μ,σ（op.mean_std）算自適應門檻
    τ=μ+max(k,_K_FLOOR)·σ（easy→放寬為 max(_EASY_TAU_FLOOR,μ)）；回 τ≤cos≤1−gap 的對 [(a,b,cos,mid_dir)]。
    mid_dir＝兩質心**中點方向** op.unit([c_a+c_b])＝兩主題交會的新區域（方向只能來自真實質心，不杜撰）。"""
    labels = sorted(cents)
    if len(labels) < _MIN_TOPICS:
        return []
    coss = []
    for i in range(len(labels)):
        for j in range(i + 1, len(labels)):
            coss.append(op.dot(cents[labels[i]]["centroid"], cents[labels[j]]["centroid"]))
    mu, sigma = op.mean_std(coss)
    tau = max(_EASY_TAU_FLOOR, mu) if easy else max(_COS_FLOOR, mu + max(k, _K_FLOOR) * sigma)
    out = []
    for i in range(len(labels)):
        ci = cents[labels[i]]["centroid"]
        for j in range(i + 1, len(labels)):
            cj = cents[labels[j]]["centroid"]
            cos = op.dot(ci, cj)
            if cos >= tau and cos <= 1.0 - min_pair_gap:
                mid = op.unit([x + y for x, y in zip(ci, cj)])
                out.append((labels[i], labels[j], round(cos, 4), mid))
    return out


def _bridge_key(a, b, kind="blend"):
    """橋鍵帶 kind 前綴 → 同一對主題的 blend/leap/conflict 各自獨立累積。預設 blend＝與既有呼叫 byte 相容。"""
    return kind + "::" + "|".join(sorted((a, b)))


def _nearest_anchor(members, direction):
    """某主題裡最靠近橋方向（op.dot 最大）的那一筆真實記寫 → 當錨（給 LLM 只連這條真實的線）。"""
    best, best_d = None, -2.0
    for vec, text, ts, *_ in members:                      # member 可能多帶 valence/category，這裡只用前三
        d = op.dot(vec, direction)
        if d > best_d:
            best, best_d = (text, ts), d
    if not best:
        return {"text": "", "ts": None}
    return {"text": _clip(best[0]), "ts": best[1]}


def _topic_category(entry):
    """這條主題此刻的代表大類（眾數）；都沒有回 None。"""
    cats = [m[4] for m in entry["members"] if len(m) > 4 and m[4]]
    return Counter(cats).most_common(1)[0][0] if cats else None


def _topic_valence(entry):
    """這條主題的情緒概況：平均價性、有情緒登錄的筆數、最暖一筆、最沉一筆（衝突偵測用）。"""
    warm, warm_v, heavy, heavy_v, vals = None, -2.0, None, 2.0, []
    for m in entry["members"]:
        v = m[3] if len(m) > 3 else None
        if v is None:
            continue
        vals.append(v)
        if v > warm_v:
            warm, warm_v = m, v
        if v < heavy_v:
            heavy, heavy_v = m, v

    def rec(m, v):
        return {"text": _clip(m[1]), "ts": m[2], "valence": round(v, 3)}
    return {"mean": (round(sum(vals) / len(vals), 3) if vals else None), "support": len(vals),
            "warm": (rec(warm, warm_v) if warm else None), "heavy": (rec(heavy, heavy_v) if heavy else None)}


def _ema(old, new, a=_DIR_EMA):
    return [(1 - a) * o + a * n for o, n in zip(old, new)]


def _blank_bridge():
    return {"key": "", "a": "", "b": "", "kind": "blend", "signal": {}, "itype": None,
            "dir": None, "cos": 0.0, "strength": 0.0, "support": 0,
            "anchor_a": {"text": "", "ts": None}, "anchor_b": {"text": "", "ts": None},
            "born_lap": 0, "last_support_lap": 0, "confirm_run": 0, "emerged": False, "emerged_ts": 0.0}


def _centroid_mu_sigma(cents, labels):
    coss = [op.dot(cents[labels[i]]["centroid"], cents[labels[j]]["centroid"])
            for i in range(len(labels)) for j in range(i + 1, len(labels))]
    return op.mean_std(coss)


def _flatten(cents):
    """把所有主題的真實記寫攤成一條 [(vec, text, ts, topic)]＝『縱觀整片記寫脈絡』的掃描底。"""
    flat = []
    for lab in sorted(cents):
        for m in cents[lab]["members"]:
            flat.append((m[0], m[1], m[2], lab))
    return flat


def _stepping_stone(flat, c_a, c_b, a, b, floor):
    """整片記寫裡最『居中』（對兩質心 min 餘弦最大）的一筆**第三條線**真實記寫＝把兩條遠線接起來的隱藏接點。
    min(餘弦) 須 ≥ floor（真的在中間、非雜訊）；找不到第三條線就退而取任何夠居中的一筆；都沒有回 None。"""
    best, best_s, fb, fb_s = None, floor, None, floor
    for vec, text, ts, topic in flat:
        s = min(op.dot(vec, c_a), op.dot(vec, c_b))
        if topic not in (a, b):
            if s >= best_s:
                best, best_s = {"vec": vec, "text": _clip(text), "ts": ts, "topic": topic}, s
        elif s >= fb_s:
            fb, fb_s = {"vec": vec, "text": _clip(text), "ts": ts, "topic": topic}, s
    return best or fb


def leap_candidates(cents, k, *, easy=False):
    """跳躍 leap：兩主題質心**遠**（_LEAP_COS_FLOOR ≤ cos < blend τ），卻有一筆**第三條線的真實記寫**剛好夾在中間
    （對兩質心都夠近）把它們接起來 → 候選 (a,b,"leap",cos,dir,signal)。那筆踏腳石就是『縱觀脈絡』找到的隱藏接點，
    錨＝各主題裡最靠近踏腳石方向的真實記寫（接地、不杜撰）。"""
    labels = sorted(cents)
    if len(labels) < _MIN_TOPICS:
        return []
    mu, sigma = _centroid_mu_sigma(cents, labels)
    blend_tau = max(_EASY_TAU_FLOOR, mu) if easy else max(_COS_FLOOR, mu + max(k, _K_FLOOR) * sigma)
    far_lo = _EASY_LEAP_COS_FLOOR if easy else _LEAP_COS_FLOOR
    stone_floor = _EASY_LEAP_STONE_FLOOR if easy else _LEAP_STONE_FLOOR
    flat = _flatten(cents)
    out = []
    for i, a in enumerate(labels):
        ca = cents[a]["centroid"]
        for b in labels[i + 1:]:
            cb = cents[b]["centroid"]
            cos = op.dot(ca, cb)
            if not (far_lo <= cos < blend_tau):            # 必須真的遠（在 blend 帶之下）
                continue
            stone = _stepping_stone(flat, ca, cb, a, b, stone_floor)
            if stone is None:                              # 中間沒有真實記寫搭得上 → 不是 leap、只是兩條無關的線
                continue
            dir_ = op.unit([x + y for x, y in zip(ca, cb)])
            signal = {"stone": {"text": stone["text"], "ts": stone["ts"], "topic": stone["topic"]},
                      "anchor_a": _nearest_anchor(cents[a]["members"], stone["vec"]),
                      "anchor_b": _nearest_anchor(cents[b]["members"], stone["vec"])}
            out.append((a, b, "leap", round(cos, 4), dir_, signal))
    return out


def conflict_candidates(cents, k, *, easy=False):
    """衝突 conflict：兩主題**相近**（cos≥sim_τ 或同大類）但平均價性**正負相反**、各側夠強夠多支撐 → 候選
    (a,b,"conflict",cos,dir,signal)。錨＝暖那邊最暖一筆 vs 沉那邊最沉一筆真實記寫（張力就在這對比上）。"""
    labels = sorted(cents)
    if len(labels) < _MIN_TOPICS:
        return []
    mu, sigma = _centroid_mu_sigma(cents, labels)
    sim_tau = max(_EASY_TAU_FLOOR, mu) if easy else max(_COS_FLOOR, mu + max(k, _K_FLOOR) * sigma)
    min_supp = _EASY_CONF_MIN_SUPPORT if easy else _CONF_MIN_SUPPORT
    mag = _EASY_CONF_MAG if easy else _CONF_MAG
    tv = {lab: _topic_valence(cents[lab]) for lab in labels}
    cat = {lab: _topic_category(cents[lab]) for lab in labels}
    out = []
    for i, a in enumerate(labels):
        va = tv[a]
        if va["support"] < min_supp or va["mean"] is None:
            continue
        for b in labels[i + 1:]:
            vb = tv[b]
            if vb["support"] < min_supp or vb["mean"] is None:
                continue
            ma, mb = va["mean"], vb["mean"]
            if (ma > 0) == (mb > 0):                       # 同號 → 沒有衝突
                continue
            if not (abs(ma) >= mag and abs(mb) >= mag):    # 暖/沉都要夠真
                continue
            cos = op.dot(cents[a]["centroid"], cents[b]["centroid"])
            same_cat = bool(cat[a]) and cat[a] == cat[b]
            if not (cos >= sim_tau or same_cat):           # 必須相近（語意或同大類）
                continue
            warm_lab, heavy_lab = (a, b) if ma > 0 else (b, a)
            warm_rec = va["warm"] if ma > 0 else vb["warm"]
            heavy_rec = vb["heavy"] if ma > 0 else va["heavy"]
            dir_ = op.unit([x + y for x, y in zip(cents[a]["centroid"], cents[b]["centroid"])])
            signal = {"warm": {"topic": warm_lab, **warm_rec}, "heavy": {"topic": heavy_lab, **heavy_rec},
                      "anchor_a": (warm_rec if a == warm_lab else heavy_rec),
                      "anchor_b": (warm_rec if b == warm_lab else heavy_rec),
                      "valence_gap": round(abs(ma - mb), 3), "same_category": same_cat}
            out.append((a, b, "conflict", round(cos, 4), dir_, signal))
    return out


def _spark_valence_magnitude(signal, cents, a, b):
    """這個火花核心的情緒強度：衝突直接讀 warm/heavy 價性，否則取兩端點成員裡最大 |valence|。"""
    mags = [abs(node["valence"]) for node in ((signal or {}).get("warm"), (signal or {}).get("heavy"))
            if node and node.get("valence") is not None]
    if mags:
        return max(mags)
    for lab in (a, b):
        for m in (cents.get(lab) or {}).get("members", []):
            v = m[3] if len(m) > 3 else None
            if v is not None:
                mags.append(abs(v))
    return max(mags) if mags else 0.0


def classify_itype(a, b, kind, signal, why_now, concerns, cents):
    """貼一個靈感類型（axis 2，與操作半獨立）：依訊號評分、**強的優先**，平手照 precedence（problem＞epiphany＞life＞emotion）。
    沒有任何訊號 → 中性 'link'。'沒有一定、端看累積了哪些片段'＝由資料訊號自選。"""
    scores = {}
    if [c for c in (concerns or []) if c in (a, b)]:       # 問題導向：端點是活的痛點
        scores["problem"] = 3.0
    if (why_now or "").startswith("revisit:"):             # 頓悟直覺：閒置自我刺激繞回
        scores["epiphany"] = 2.5
    ca = _topic_category(cents[a]) if a in cents else None
    cb = _topic_category(cents[b]) if b in cents else None
    if ca and cb and ca != cb:                             # 生活觀察：跨大類借鑑
        scores["life"] = 2.0
    vmag = _spark_valence_magnitude(signal, cents, a, b)   # 情感共鳴：火花核心情緒強（magnitude 放大、可勝出）
    if vmag >= _ITYPE_EMO_MAG:
        scores["emotion"] = 1.5 + vmag
    if not scores:
        return "link"
    order = {"problem": 4, "epiphany": 3, "life": 2, "emotion": 1}
    return max(scores, key=lambda t: (scores[t], order[t]))


def warmth(br, strength_thr, confirm_laps=_CONFIRM_LAPS, min_support=_MIN_SUPPORT):
    """💡『快好了』暖度勢能（per-bridge 連續逼近觀測量，純函式）：把橋此刻離湧現的三個既有比例
    ——強度/門檻、連續確認/所需確認、支撐/所需支撐——各夾到 [0,1] 取**幾何平均**、量化到 0.05 步階回 0..1。
    三者皆達門檻＝1.0；任一未達＜1。鏡射 lifeloop.k_breath 的量化去抖風格（量化避免每圈微漂）。
    缺值／零門檻安全回 0.0。**註**：這是 per-bridge 的『逼近感』，不宣稱對應 experience 的全域收束 stage。"""
    if not br:
        return 0.0
    thr = strength_thr or 0.0
    cl = confirm_laps or 0
    ms = min_support or 0
    if thr <= 0 or cl <= 0 or ms <= 0:
        return 0.0
    rs = min(1.0, max(0.0, (br.get("strength") or 0.0) / thr))
    rc = min(1.0, max(0.0, (br.get("confirm_run") or 0) / cl))
    rsup = min(1.0, max(0.0, (br.get("support") or 0) / ms))
    geo = (rs * rc * rsup) ** (1.0 / 3.0)
    return round(round(geo / 0.05) * 0.05, 4)


_warmth = warmth   # 模組內別名：observe 的 warmth 參數會遮蔽同名函式，內部用此別名呼叫純函式


# ── 新奇度（per-bridge 驚奇純量，同 kind 可比、跨 kind 不宣稱公平）─────────────────────
def _novelty(kind, cos, signal=None, *, blend_tau=None):
    """💡 這條橋『有多意外』的 0..1 純量（純函式，由真實累積量導出）：
    - leap：兩主題越遠（cos 越低於 blend τ）越意外＝(blend_tau−cos) 對 blend_tau 正規化；缺 τ 時退化以 τ=1.0 正規化（leap 候選必有 ≥2 主題、實際路徑不會缺）。
    - conflict：價差越大越意外＝min(1, valence_gap/2)。
    - blend：越靠近反而越『順理成章』＝max(0, 1−cos)。
    **同 kind 內可比、跨 kind 不宣稱公平**（不同 kind 的 novelty 不放進同一個排序主序）。"""
    sig = signal or {}
    if kind == "leap":
        tau = blend_tau if blend_tau is not None else 1.0
        if tau <= 0:
            return 0.0
        return round(min(1.0, max(0.0, (tau - (cos or 0.0)) / tau)), 4)
    if kind == "conflict":
        gap = abs(sig.get("valence_gap") or 0.0)
        return round(min(1.0, max(0.0, gap / 2.0)), 4)
    return round(min(1.0, max(0.0, 1.0 - (cos or 0.0))), 4)


# 💡 §0.54「真的有特色才分享」硬門檻（純函式，_insight_emit 前置把關）：一條湧現的橋夠不夠意外到值得主動打斷。
# per-kind 地板——novelty 同 kind 內可比、**跨 kind 不宣稱公平**（見 _novelty），故各 kind 用自己那把尺、不混排：
#   leap ＝(τ−cos)/τ：本就以 τ 正規化、跨 τ 都能到 (0,1] 全幅 → 直接用**絕對地板 0.34**（≳1/3 τ 距離才算真跳接）
#   conflict＝valence_gap/2：亦全幅 → 絕對地板 0.34（價差 gap≳0.68 才算真張力）
#   blend＝1−cos：**天花板隨 τ 浮動**（blend 候選必 cos≥τ，故 novelty≤1−τ；τ>0.70 時任何 blend 都 <0.30，用絕對地板會把
#     整個 blend kind 封殺＝過度封口）。改**相對 τ 正規化**（同 leap 精神、τ 不變式）：pos=(1−cos)/(1−τ)=novelty/(1−τ)∈[0,1]，
#     越高＝這條 blend 在「它可能的意外範圍」裡越靠遠端＝相對使用者自己的主題離散度越有特色 → 地板 0.45（保住相對較遠的那半）。
#     沒帶 blend_tau（novelty 關/舊事件）→ 退回一個**保守低**絕對地板 0.22（只濾掉幾乎同義的 blend，避免過度封口）。
_DISTINCT_FLOOR = {"blend": 0.45, "leap": 0.34, "conflict": 0.34}
_DISTINCT_FLOOR_DEFAULT = 0.34
_BLEND_ABS_FALLBACK = 0.22        # blend 無 τ 可正規化時的保守絕對地板（寧鬆勿過度封口）


def is_distinctive(event, scale=1.0):
    """💡 這條湧現的橋『夠不夠有特色到值得主動打斷對方』？回 True/False（純函式）。
    以 event['novelty']（emergence 時由 _novelty 依 kind 算好）比對**該 kind 的地板×scale**。
    blend 特別：novelty 天花板隨 τ 浮動，故改用**相對 τ 正規化位置** pos=novelty/(1−blend_tau) 比對地板（τ 不變式、
    不會因 τ 高而把整個 blend 封殺）；event 需帶 'blend_tau'（emergence 時 stamp）——沒帶則退保守絕對地板。
    **沒有 novelty 鍵**（ASSOCIATION_NOVELTY=0、無從判定）→ 回 True（誠實退讓、不無故封口、不 silence sparks）。
    scale：門檻縮放（>1 更嚴、<1 更寬、≤0 或非有限值＝關閉此門檻＝放行；守門失敗一律偏放行）。"""
    if not event:
        return True
    nov = event.get("novelty")
    if nov is None:                                # 沒算 novelty → 無從評斷「有沒有特色」→ 放行（不因此封口）
        return True
    try:
        s = float(scale)
    except (TypeError, ValueError):
        s = 1.0
    if not math.isfinite(s) or s <= 0:             # 縮放非有限或≤0＝明確關閉此門檻（fail-open、不 silence）
        return True
    try:
        nv = float(nov)
    except (TypeError, ValueError):
        return True                                # novelty 壞值 → 不擋（守門失敗偏放行）
    if not math.isfinite(nv):
        return True
    kind = event.get("kind", "blend")
    if kind == "blend":                            # 相對 τ 正規化（天花板隨 τ 浮動 → 不用會封殺整 kind 的絕對地板）
        tau = event.get("blend_tau")
        try:
            tau = float(tau) if tau is not None else None
        except (TypeError, ValueError):
            tau = None
        if tau is not None and math.isfinite(tau) and tau < 1.0:
            span = 1.0 - tau                       # blend novelty 的實際天花板
            pos = (nv / span) if span > 1e-9 else 1.0
            return pos >= _DISTINCT_FLOOR["blend"] * s
        return nv >= _BLEND_ABS_FALLBACK * s       # 無 τ → 保守絕對地板（避免過度封口）
    floor = _DISTINCT_FLOOR.get(kind, _DISTINCT_FLOOR_DEFAULT) * s
    return nv >= floor


# ── 累加器（鏡射 experience.Experience：記憶體活態＋小摘要跨重生）──────────────────
class Associations:
    """跨主題橋的活累加器：每圈沉澱、半衰減、防抖確認，湧現時回事件。
    近期橋在記憶體（重啟歸零），但『已湧現/最強橋的骨架＋累計湧現數』可由 summary 跨重生種回。"""

    def __init__(self, summary=None):
        self.bridges = {}          # key -> bridge dict（key 帶 kind 前綴：同對主題的 blend/leap/conflict 各一條）
        self.lap = 0               # 累加器自己的拍數（半衰減/防抖的時間軸）
        self.emerged_total = 0     # 一生湧現過幾條（跨重生）
        self.last = {}             # 最近一次觀測（給 /status）
        s = summary or {}
        self.emerged_total = int(s.get("emerged_total", 0) or 0)
        for b in (s.get("bridges") or []):
            if b.get("key"):
                self.bridges[b["key"]] = {**_blank_bridge(), **b, "confirm_run": 0}

    def observe(self, cents, k, ingest_changed, revisit_topic, now_ts, *,
                confirm_laps=_CONFIRM_LAPS, min_support=_MIN_SUPPORT, easy=False, concerns=None,
                novelty=False, warmth=False, ripple_topics=None, ripple_kinds=None):
        """每圈跑：① 半衰減所有橋；② 三種偵測器（blend/leap/conflict）各產候選 → 更新 (pair,kind) 橋 dir(EMA)/cos/anchors；
        ③ 對**被本圈觸發**的橋推高 strength、support++；④ 防抖：strength 過門檻且連續 confirm_laps、support≥min_support、
        未 emerged、且本圈被觸發 → 回一個 emergence event（**跨所有 kind 最強的那條**，一圈最多一條）；否則 None。
        觸發＝新記寫（ingest 變）或自我刺激繞回橋的某端。湧現時順手貼上靈感類型 itype。
        novelty=True（加性）：event 多帶 round(novelty,3)；④ 排序鍵在 strength **完全相等(==)** 時才以 novelty 高者決勝
        （主序仍是**未取整** -strength，不顛覆現狀）。warmth=True（加性、easy 下短路為 None）：末把最暖未湧現橋寫 self.last['warmest']。
        ripple_topics（加性、**最後參數**、預設 None＝逐位元同現狀）：此刻「活著」的真實主題標籤集合（對話＋自我遞迴）。
        **僅在本圈該橋未被 new_record/revisit 觸發（嚴格 elif）且其端點 ∈ ripple_topics** 時，施一個**較弱的漣漪觸發**：
        strength += _RIPPLE_GAIN(< _STRENGTH_GAIN)、_trig_why='ripple:insight'/'ripple:conv'（同一條橋一圈只加一次，不因 a、b
        都在集合而加兩次）。**漣漪只調 strength（時機/醒目），不計 support**——support 是「真實片段跨時間沉澱」的閘，仍只由
        new_record／revisit 累積，故漣漪能讓**已有真實累積**的潛在連結浮上來/重新計時，但**不能獨力把無真實支撐的橋推到湧現**
        （嚴謹接地）。ripple_kinds={label:'conv'|'insight'}（可選）只用來標 _trig_why 來源（insight 端點→'ripple:insight'，否則
        'ripple:conv'）。石頭落湖→漣漪擴散到既有真實橋→改變哪條真實橋成長/湧現的時機，起伏的仍是湖水（資料）。"""
        self.lap += 1
        ripple_set = set(ripple_topics or ())                  # None → 空 set → 漣漪分支恆 False → 逐位元同現狀
        insight_src = {lab for lab, src in (ripple_kinds or {}).items() if src == "insight"}
        for br in self.bridges.values():                       # ① 半衰減（過時的橋自然淡出）
            br["strength"] *= _DECAY_PER_LAP
        gain = _EASY_GAIN if easy else _STRENGTH_GAIN
        strength_thr = _EASY_STRENGTH_EMERGE if easy else _STRENGTH_EMERGE
        cands = [(a, b, "blend", cos, mid, {}) for (a, b, cos, mid) in     # ② 三種操作合併成帶 kind 的候選
                 candidate_bridges(cents, k, min_pair_gap=(0.0 if easy else _MIN_PAIR_GAP), easy=easy)]
        cands += leap_candidates(cents, k, easy=easy)
        cands += conflict_candidates(cents, k, easy=easy)
        blend_tau = None                                       # leap novelty 用：當圈 blend 帶 τ（同 candidate_bridges 的算法）
        if novelty:
            labels = sorted(cents)
            if len(labels) >= _MIN_TOPICS:
                mu, sigma = _centroid_mu_sigma(cents, labels)
                blend_tau = (max(_EASY_TAU_FLOOR, mu) if easy
                             else max(_COS_FLOOR, mu + max(k, _K_FLOOR) * sigma))
        updated = []
        for a, b_lbl, kind, cos, mid, signal in cands:
            key = _bridge_key(a, b_lbl, kind)
            br = self.bridges.get(key)
            if br is None:
                br = _blank_bridge()
                br.update({"key": key, "a": a, "b": b_lbl, "kind": kind, "dir": list(mid),
                           "born_lap": self.lap, "last_support_lap": self.lap})
                self.bridges[key] = br
            else:
                br["dir"] = op.unit(_ema(br["dir"], mid)) if br.get("dir") else list(mid)
                br["a"], br["b"], br["kind"] = a, b_lbl, kind
            br["cos"], br["signal"] = cos, signal
            br["anchor_a"] = signal.get("anchor_a") or _nearest_anchor(cents[a]["members"], br["dir"])
            br["anchor_b"] = signal.get("anchor_b") or _nearest_anchor(cents[b_lbl]["members"], br["dir"])
            trig_why = ("new_record" if ingest_changed
                        else (f"revisit:{revisit_topic}" if revisit_topic in (a, b_lbl) else None))
            br["_trig_why"] = trig_why
            if trig_why:                                       # ③ 被觸發 → 沉澱一筆支撐
                br["strength"] = min(1.0, br["strength"] + gain)
                br["support"] += 1
                br["last_support_lap"] = self.lap
            elif ripple_set and (a in ripple_set or b_lbl in ripple_set):
                # 🌊 漣漪觸發（嚴格 elif：只在本圈未被 new_record/revisit 觸發時才施）：對話/前一聯想的弱擾動。
                # **只調 strength（時機/醒目），不計 support**——support 是「真實片段跨時間沉澱」的閘，仍只由
                # new_record／自我刺激 revisit 累積。＝漣漪能讓**已有真實累積**的潛在連結浮上來、重新計時/挑選，
                # 但聊天/遞迴的注意力本身不算沉澱、**不能獨力把一條無真實支撐的橋推到湧現**（owner 定奪：嚴謹接地，
                # 「石頭只決定漣漪去哪，起伏的仍是湖水＝資料」）。同一條橋一圈只加一次（or 短路、不對 a、b 各加）；
                # gain < new_record＝對話一句非新資料。
                br["strength"] = min(1.0, br["strength"] + _RIPPLE_GAIN)
                br["_trig_why"] = ("ripple:insight" if (a in insight_src or b_lbl in insight_src)
                                   else "ripple:conv")
            br["confirm_run"] = (br["confirm_run"] + 1) if br["strength"] >= strength_thr else 0
            updated.append(br)
        emerged = None                                         # ④ 湧現判定（跨 kind 最強且合格的那條）
        # 主序＝**未取整** -strength（不顛覆現狀）；novelty=True 時僅在 strength **完全相等(==)** 才以 novelty 高者決勝。
        def _sort_key(b):
            if not novelty:
                return -b["strength"]
            return (-b["strength"], -_novelty(b.get("kind", "blend"), b.get("cos", 0.0),
                                              b.get("signal"), blend_tau=blend_tau))
        for br in sorted(updated, key=_sort_key):
            if (not br["emerged"] and br.get("_trig_why")
                    and br["strength"] >= strength_thr
                    and br["support"] >= min_support
                    and br["confirm_run"] >= confirm_laps):
                br["emerged"], br["emerged_ts"] = True, now_ts
                br["itype"] = classify_itype(br["a"], br["b"], br["kind"], br.get("signal") or {},
                                             br["_trig_why"], concerns, cents)
                self.emerged_total += 1
                emerged = self._emergence_event(br, br["_trig_why"], novelty=novelty, blend_tau=blend_tau)
                break
        self._prune()
        warmest = None                                          # 💡 最暖未湧現橋觀測量（加性、easy 短路；不入 summary/state）
        if warmth and not easy:
            cand = max((b for b in updated if not b["emerged"]),
                       key=lambda b: _warmth(b, strength_thr, confirm_laps, min_support),
                       default=None)
            if cand is not None:
                w = _warmth(cand, strength_thr, confirm_laps, min_support)
                warmest = {"pair": pair_key(cand), "kind": cand.get("kind", "blend"), "warmth": w}
        self.last = {"bridges": len(self.bridges),
                     "strongest": round(max((b["strength"] for b in self.bridges.values()), default=0.0), 3),
                     "emerged_total": self.emerged_total,
                     "warmest": warmest,
                     "why_now": emerged["why_now"] if emerged else None}
        return emerged

    def _emergence_event(self, br, why_now, *, novelty=False, blend_tau=None):
        ev = {"a": br["a"], "b": br["b"], "kind": br.get("kind", "blend"),
                "anchor_a": dict(br["anchor_a"]), "anchor_b": dict(br["anchor_b"]),
                "cos": round(br["cos"], 3), "strength": round(br["strength"], 3),
                "support": br["support"], "why_now": why_now,
                "signal": dict(br.get("signal") or {}), "itype": br.get("itype")}
        if novelty:                                            # 加性新鍵；novelty=False 時逐位元同現狀（不加鍵）
            ev["novelty"] = round(_novelty(br.get("kind", "blend"), br.get("cos", 0.0),
                                           br.get("signal"), blend_tau=blend_tau), 3)
            # 💡 §0.54：blend 的特色門檻需相對 τ 正規化（novelty 天花板＝1−τ），故 stamp 當圈 blend_tau 供 is_distinctive 用。
            if br.get("kind", "blend") == "blend" and blend_tau is not None:
                ev["blend_tau"] = round(blend_tau, 3)
        return ev

    def _prune(self):
        dead = [k for k, b in self.bridges.items() if b["strength"] < _STRENGTH_FLOOR and not b["emerged"]]
        for k in dead:
            del self.bridges[k]
        if len(self.bridges) > _BRIDGES_CAP:
            keep = sorted(self.bridges.values(), key=lambda b: -b["strength"])[:_BRIDGES_CAP]
            self.bridges = {b["key"]: b for b in keep}

    def summary(self):
        """跨重生延續（小、可序列化進 state.json）：累計湧現數＋已湧現/最強橋骨架（≤8 條）。"""
        keep = sorted(self.bridges.values(), key=lambda b: -b["strength"])[:8]
        return {"emerged_total": self.emerged_total,
                "bridges": [{kk: b.get(kk) for kk in ("key", "a", "b", "kind", "dir", "cos", "strength", "emerged")}
                            for b in keep]}

    def snapshot(self):
        """/status 觀測：橋數、最強橋、累計湧現數、最強三條（含 kind）。"""
        top = sorted(((b["a"], b["b"], b.get("kind", "blend"), round(b["strength"], 2))
                      for b in self.bridges.values()), key=lambda t: -t[3])[:3]
        return {"bridges": len(self.bridges), "emerged_total": self.emerged_total,
                "strongest": round(max((b["strength"] for b in self.bridges.values()), default=0.0), 3),
                "top": top, "warmest": (self.last or {}).get("warmest")}


# ── 🌊 漣漪來源（純函式、全程無 LLM、接地：只回 cents 子集，絕不造主題/節點）─────────────────
def conversation_ripple_topics(convo_history, cent_labels, now_ts, window_sec, recorded=None):
    """外石：使用者最近窗內**對話**提到、且**存在於 cents** 的真實主題標籤集合（cent_labels 子集）。
    主映射＝**標籤完整子字串命中對話文字**（`label in text`，便宜、誠實退化）；
    recorded 非 None 時用 `datatools.best_topic` 補強，但門檻拉高（`max(3, len(label))`）避免兩字巧合誤射、且結果一律 ∩ cent_labels。
    對話提到但 cents 沒有的主題 → 不漣漪（誠實退化、絕不無中生有造節點）。空對話/全在窗外 → set()。"""
    labels = set(cent_labels or ())
    if not labels or not convo_history:
        return set()
    hits = set()
    for turn in convo_history:
        if turn.get("role") != "user":                               # 💡 §1.06(C2) 只認**使用者**說的：bot 自己的話不算「你剛聊到」
            continue                                                 #（否則湧現後宣稱「被你剛聊到的牽動」但其實是 bot 自己提的＝掰）
        ts = turn.get("ts") or 0
        if window_sec is not None and (now_ts - ts) > window_sec:   # 尊重窗（窗外的對話不再起漣漪）
            continue
        txt = turn.get("text") or ""
        if not txt:
            continue
        hits |= {lab for lab in labels if lab and lab in txt}        # 主源：標籤完整出現在對話文字
        if recorded:                                                 # 補強：best_topic（高門檻防誤命中），再 ∩ cents
            bt = datatools.best_topic(txt, recorded, min_score=max(3, _best_topic_min_len(labels)))
            if bt in labels:
                hits.add(bt)
    return hits & labels                                             # 雙保險：保證 ⊆ cent_labels


def _best_topic_min_len(labels):
    """best_topic 補強的最低 LCS 門檻：要求達某個短標籤全長（避免兩字巧合誤射不相干真實主題）。"""
    lens = [len(lab) for lab in labels if lab]
    return min(lens) if lens else 3


def recursive_ripple_topics(recent_insights, cent_labels, now_ts, window_sec, last_insight=None):
    """遞迴石（思緒鏈）：最近窗內**自己湧現過的聯想**端點（recent_insights 的 pair 拆 a|b）∩ cents＝
    上一個/最近幾個聯想擾動下一個。以 recent_insights 為**主源**（持久化、回饋後不清、跨重生＝鏈不易斷）；
    last_insight 為**可選次要**（剛說出口那條的即時餘波，回饋後會被清空，僅作額外加成）。一律 ∩ cent_labels。
    對話/遞迴提到但 cents 沒有的主題（如已掉出湖面的舊主題）→ 不漣漪（語意誠實：主題離開資料就不該再漣漪）。"""
    labels = set(cent_labels or ())
    if not labels:
        return set()
    hits = set()
    for r in (recent_insights or []):
        ts = r.get("ts") or 0
        if window_sec is not None and (now_ts - ts) > window_sec:
            continue
        for lab in (r.get("pair") or "").split("|"):
            if lab in labels:
                hits.add(lab)
    if last_insight:                                                # 可選次要：剛說出口那條的端點（窗內）
        ts = last_insight.get("ts") or 0
        ev = last_insight.get("event") or {}
        if window_sec is None or (now_ts - ts) <= window_sec:
            for lab in (ev.get("a"), ev.get("b")):
                if lab in labels:
                    hits.add(lab)
    return hits & labels


# ── 自發出聲去重台帳（控重複性／自然降頻；鏡像 recent_insights 的 pair 級去重，但對「自發伸手講的那條線」）──
def was_recent(dedup_key, ledger, now_ts, dedup_s):
    """這條線（dedup_key）是否在 dedup_s 秒內**自發講過**（查 ledger=[{key,ts}]）。純函式、可單測。
    空 key／空台帳／dedup_s≤0 → False（不擋）。讓 🫧 自發出聲不一直重講同一條（💡 路徑早有 pair 級去重，本函式補 🫧 路徑只有計時冷卻、無內容去重的洞）。"""
    k = (dedup_key or "").strip()
    if not k or dedup_s <= 0:
        return False
    for e in (ledger or []):
        if e.get("key") == k and (now_ts - (e.get("ts") or 0)) < dedup_s:
            return True
    return False


def push_recent(dedup_key, ledger, now_ts, cap=8):
    """把剛自發講過的線記進去重台帳（去同 key 後追加、保最後 cap 筆）。純函式回新清單；空 key → 原樣。"""
    k = (dedup_key or "").strip()
    if not k:
        return list(ledger or [])
    ring = [e for e in (ledger or []) if e.get("key") != k]
    return (ring + [{"key": k, "ts": now_ts}])[-cap:]


# ── 相關性閘 / 出聲接地文字（給 monitor／coach 用）──────────────────────────────
def is_relevant_ev(event, foreground_topic, concern_topics):
    """湧現要『扣得上此刻前景/在意/剛繞回』才出聲（否則隨機聯想、不收緊耦合）。"""
    pool = {t for t in ([foreground_topic] + list(concern_topics or [])) if t}
    return event["a"] in pool or event["b"] in pool


def pair_key(event):
    """一條聯想的去重/回饋鍵＝兩端點排序（不分 kind、不分方向）→ 同一對主題只記一份回饋、去重看同一鍵。"""
    return "|".join(sorted((event.get("a") or "", event.get("b") or "")))


# 你對某條聯想的回饋線索（reject 優先；單講「有趣/好玩」不算 affirm——「無關但很有趣」＝reject）
_FB_REJECT = ("無關", "沒關聯", "沒有關聯", "沒有關係", "沒關係", "不相關", "不太相關", "扯不上", "八竿子",
              "想太多", "牽強", "沒這回事", "不見得", "哪有關", "沒什麼關係", "不太對", "不對吧")
_FB_AFFIRM = ("有道理", "有關聯", "有關係", "說得對", "對欸", "沒錯", "確實有", "真的有關", "說中", "被你說中",
              "想過", "有點道理", "蠻準", "很準", "good", "讚的連結")


def read_feedback(text):
    """這句是不是在回饋我剛冒的那條聯想？回 'reject'｜'affirm'｜None。reject 線索（含 plasticity 的不滿訊號）**優先**。"""
    t = text or ""
    if not t:
        return None
    if any(c in t for c in _FB_REJECT) or plasticity.is_dissatisfaction(t):
        return "reject"
    if any(c in t for c in _FB_AFFIRM):
        return "affirm"
    return None


# 💡 中間態「部分對／方向對但細節不準／沒想過倒有點啟發」的線索（從嚴；刻意不與 _FB_REJECT 重疊）
_FB_PARTIAL = ("方向對", "方向是對", "大方向", "部分對", "有點對", "沒想過", "有啟發", "有點啟發",
               "倒是有意思", "細節不準", "細節不對", "雖不中")


def grade_feedback(text):
    """💡 三態回饋分級（新增純函式，**不影響** read_feedback 的二元語意）：回 {'sentiment','score'}：
    reject（含 plasticity 不滿訊號，**優先**）→ partial（方向對但不全中）→ affirm → 無線索回 sentiment=None。
    reject 詞優先於 partial（維持 reject 護欄）；partial 詞表從嚴、刻意不與 reject 重疊。score：reject=−1/partial=0.5/affirm=1/None=0。"""
    t = text or ""
    if not t:
        return {"sentiment": None, "score": 0.0}
    if any(c in t for c in _FB_REJECT) or plasticity.is_dissatisfaction(t):
        return {"sentiment": "reject", "score": -1.0}
    if any(c in t for c in _FB_PARTIAL):
        return {"sentiment": "partial", "score": 0.5}
    if any(c in t for c in _FB_AFFIRM):
        return {"sentiment": "affirm", "score": 1.0}
    return {"sentiment": None, "score": 0.0}


def feedback_brief(event, sentiment):
    """剛收到回饋當下，交給聊天 LLM 的**據實事實**：讓「我記下來了」為真、且不過度承諾（誠實第一）。沒有回 ''。"""
    if not event or sentiment not in ("reject", "affirm", "partial"):
        return ""
    a, b = event.get("a"), event.get("b")
    if sentiment == "partial":                                 # 中間態：方向對但不全中 → 收下、別誇張、誠實標只對一半
        return ("【你剛對我那條靈感的回饋】\n"
                f"你覺得我把『「{a}」和「{b}」連起來』那條**方向對、但細節沒全中**。據實回："
                "謝謝你講、我**記下來了**、抓到大方向就好，別把它說得比實際更準。")
    if sentiment == "reject":
        return ("【你剛對我那條靈感的回饋（據實回、別空口保證）】\n"
                f"你剛對我說的『把「{a}」和「{b}」連起來』那條說了**無關**。請據實回：謝謝你講、我**記下來了**；"
                "但老實說我就是會冒這種連結，**不保證**以後一定不會再想到（別裝、別硬辯、別過度承諾）。")
    return ("【你剛對我那條靈感的回饋】\n"
            f"你剛覺得我把『「{a}」和「{b}」連起來』那條**有道理/有意思**。收下這份肯定就好、別誇張；"
            "之後想到這兩條的別的交點，我會更有底氣提。")


# 換句話：每種操作備幾種開頭、輪替（不再每句都「欸——我突然…」）
_OPENERS = {
    "blend": ("欸——我突然把", "等等，我發現", "哦，我這邊好像把", "說起來，我把", "突然覺得，"),
    "leap": ("欸——我居然把", "怪了，我把", "說來跳，我把", "我繞著繞著、把"),
    "conflict": ("欸——", "有意思，", "我注意到，", "說來矛盾，"),
}


def _opener(kind, variant):
    opts = _OPENERS.get(kind) or _OPENERS["blend"]
    return opts[int(variant or 0) % len(opts)]


# 💡 新奇度語氣提示（ASSOCIATION_NOVELTY 開時貼在事實末尾、引導語氣，仍只引既有片段、仍標假設、不改判定）
_NOVELTY_HI = 0.5
_NOVELTY_HINT_HIGH = ("（這條連結對我自己都挺意外的——可以用更驚訝、像剛被自己嚇到的口吻，"
                      "但仍然明說這只是我冒出來的假設、可能不準。）")
_NOVELTY_HINT_LOW = "（這條算比較順理成章——平實一點說就好，別硬裝驚奇；一樣標明是假設、可能不準。）"


# 靈感類型對渲染的語氣提示（貼在接地事實末尾，引導 LLM 的方向，不改判定）
_ITYPE_HINT = {
    "problem": "（這碰到你正卡著、放不下的那條——往「這會不會是個破口」的方向提。）",
    "epiphany": "（這是我自己閒著繞回去時忽然閃過的——用「靈光一閃」那種口吻。）",
    "life": "（這是把你兩個不同領域/大類的東西借過來看——往跨界靈感的方向提。）",
    "emotion": "（核心是一筆情緒很重的記寫——讓那份感受帶出這個念頭。）",
}


def _blend_why_phrase(why_now):
    """blend 接地事實裡『為何此刻接起來』的措辭。new_record/None/revisit 維持原語意（逐位元同現狀）；
    ripple:* 是加性新分支——被剛聊到/上一個念頭牽動（仍只是擾動、不改內容）。"""
    if why_now == "new_record":
        return "他剛又寫了東西、把它觸發起來"
    if (why_now or "").startswith("ripple:"):
        return ("被你剛聊到的牽動、把它接起來" if why_now == "ripple:conv"
                else "被我上一個念頭牽動、繞著繞著把它接起來")
    return "我自己繞回去想、把它接起來"


def _blend_trg_phrase(why_now):
    """insight_text（無 LLM 模板）blend 的觸發措辭；同上：原值逐位元同現狀，ripple:* 為加性新分支。"""
    if why_now == "new_record":
        return "你剛又寫了東西"
    if (why_now or "").startswith("ripple:"):
        return "被剛聊到/我上一個念頭牽動" if why_now == "ripple:conv" else "被我上一個念頭牽動"
    return "我自己繞回去想"


# 💡 §2.07 跨主題湧現的**存在特色**：三條裡唯一「**不押注、不外求**」的一條——素材 100% 是他自己寫過的兩筆，
# 我沒去查證、也不打算讓未來裁決它；我能交代的只有「這個念頭是被什麼勾起來的」和「**哪一環最可能是我腦補的**」。
# （🔮 押一個會被真實記寫裁決的注、🌐 帶回一個不屬於我也不屬於他的第三方說法。三者互換讀不通。）
# 病灶同族：現行 facts 是 3 行清單 → 3 句逐行轉寫。⇒ 先挑**一個軸**，並由程式指定**要質疑哪一環**。
_INSIGHT_WEAK = {"novelty": "你覺得意外，可是它其實只沉澱過幾次——這份意外可能是我自己放大的",
                 "support": "它撐得住，可是那也可能只是因為這兩條線本來就常一起出現",
                 "gap": "那兩筆的情緒落差是**我算出來的**，不是他說出口的",
                 "stone": "中間那一筆是**我挑的**踏腳石，他不見得是那樣連過去的",
                 "recur": "他上次就說過這兩條無關，是我又把它們想在一起了"}


def insight_focus(event, prior=None):
    """💡 §2.07 這次只講哪一件（axis）＋要自己戳自己哪一環（weak）。只用既有欄位，零新詞表、零新門檻。"""
    ev = event or {}
    kind = ev.get("kind") or "blend"
    if prior == "reject":
        axis = "recur"                                     # 誠實紅線最優先：他說過無關，我又想在一起了
    elif kind == "conflict":
        axis = "gap"                                       # valence_gap 是這個 kind 獨有的量
    elif kind == "leap" and ((ev.get("signal") or {}).get("stone") or {}).get("text"):
        axis = "stone"
    else:
        _sig = ev.get("signal") or {}
        _nov, _tau = float(_sig.get("novelty") or 0.0), float(ev.get("blend_tau") or 0.0)
        _pos = _nov / max(1e-6, 1.0 - _tau) if _tau < 1.0 else 0.0
        axis = "novelty" if _pos >= 0.22 else "support"    # 沿用 is_distinctive 的同一把尺與保守地板
    return {"axis": axis, "weak": _INSIGHT_WEAK[axis]}


def insight_focus_rule(focus):
    """💡 §2.07 掛在事實後面的講法條文：只講一件、串的中段要自己戳自己。"""
    return ("【這次只講一件事】\n"
            "- 你手上的其他訊號**一律不要提**——把它們倒出來就變成一份清單，不是一個念頭。\n"
            f"- **串的中段必須是一句自我質疑，而且只准質疑這一點**：{focus['weak']}。\n"
            "- 那不是免責聲明，是你**自己**發現的裂縫——用你自己的話說，別寫成「僅供參考」那種語氣。\n"
            "- **寫成 2–4 則連著送的短訊**：念頭怎麼冒出來的 → 那句自我質疑 → 你現在還相信它幾分。"
            "**後一則要接得住前一則**，把順序對調應該要讀不通。")


def method_reply(cfg):
    """Current implementation, not an invented before/after or mood narrative."""
    if not getattr(cfg, "association_enabled", True):
        return "目前自動聯想沒有啟用，所以我不能說它正在產生新連結。"
    return (
        "你問的是怎麼聯想，不是剛剛想到什麼。\n"
        "目前我會先從記寫找相近的主題，也找能透過另一筆記寫接起的遠題，"
        "或內容相近、表達卻有差異的片段。這些先當候選，不直接當成結論。\n"
        "接著要說清楚原文之間具體怎麼相關；你已否定的配對，不再主動重提。"
        "反覆翻到同一段，也不代表多了一份證據。\n"
        "這是目前的方法與限制。若要比較修改前後，還需要對照當時的版本；"
        "光有這些規則，不能保證每次聯想都更好。")


def insight_facts(event, prior=None, recent_openers=None):
    """把 emergence event 翻成交給 LLM 渲染的**接地事實**：依操作（融入/跳躍/衝突）給不同的真實片段與框架，
    一律保留真實錨點＋誠實標『這是你冒出來的火花/假設、不是使用者寫過的事實』。LLM 只據這些片段提一個新點子。
    prior＝這對主題之前的回饋（'reject' 要誠實再犯、'affirm' 順著上次、'partial' 順其自然不加框架＝同無 prior）；recent_openers＝最近開頭（換句話、別重複句型）。"""
    kind = event.get("kind") or "blend"
    a, b = event["a"], event["b"]
    ta = event["anchor_a"].get("text") or "（沒抓到具體那筆）"
    tb = event["anchor_b"].get("text") or "（沒抓到具體那筆）"
    recur = ""
    if prior == "reject":                                  # 誠實再犯：不裝沒說過、不硬辯、不保證不再犯
        recur = (f"（★你之前說過「{a}」和「{b}」**無關**，可是我這邊又冒出來了——請**老實承認**："
                 f"『我知道你說過這倆無關，但我就是又把它們想在一起了』，別裝沒發生、別硬辯它有關、也別保證以後不再想到。）\n")
    elif prior == "affirm":
        recur = f"（你之前覺得「{a}」「{b}」這條有道理，這次又想到相關的，可以順著上次接、別重頭講。）\n"
    head = (recur + "（這是你**自己據他真實片段冒出來的一個火花/構想**——**不是**使用者寫過的事實，是一個**假設**。"
            "只准用下面這些真實片段、別補資料沒有的具體內容；可以提一個新點子，但要明說是你冒出來的、可能不準。）\n")
    if kind == "leap":
        stone = (event.get("signal") or {}).get("stone") or {}
        lines = [f"・遠的一條〔{a}〕他寫過：「{ta}」",
                 f"・遠的另一條〔{b}〕他寫過：「{tb}」"]
        if stone.get("text"):
            lines.append(f"・中間我繞過你寫的這一筆〔{stone.get('topic')}〕：「{stone['text']}」"
                         "——這兩件看起來離很遠，好像就是透過它接上的。")
        lines.append(f"・這兩條其實隔得遠（靠近程度才約 {event.get('cos')}），卻在你整片記寫裡連得起來；據此跳接一個新點子。")
    elif kind == "conflict":
        sig = event.get("signal") or {}
        warm, heavy = sig.get("warm") or {}, sig.get("heavy") or {}
        lines = [f"・暖的一邊〔{warm.get('topic', a)}〕他寫過：「{warm.get('text') or ta}」（當時是正向、暖的）",
                 f"・沉的一邊〔{heavy.get('topic', b)}〕他寫過：「{heavy.get('text') or tb}」（當時是負向、沉的）",
                 f"・這兩件其實很靠近，他的感覺卻相反（價差約 {sig.get('valence_gap')}）；這個張力讓你冒出一個假設。"]
    else:  # blend（含舊事件無 kind）
        why = _blend_why_phrase(event.get("why_now"))
        lines = [f"・線一〔{a}〕他寫過：「{ta}」",
                 f"・線二〔{b}〕他寫過：「{tb}」",
                 f"・這兩條在你這邊越靠越近（靠近程度約 {event.get('cos')}、沉澱了 {event.get('support')} 次支撐），"
                 f"{why}；據此它們好像正併成一塊新東西。"]
    hint = _ITYPE_HINT.get(event.get("itype"))
    nov_hint = ""                                              # 💡 新奇度語氣提示（非新事實、仍只引既有片段；無 novelty 鍵→逐字同現狀）
    if "novelty" in event:
        nov = event.get("novelty") or 0.0
        nov_hint = ("\n" + (_NOVELTY_HINT_HIGH if nov >= _NOVELTY_HI else _NOVELTY_HINT_LOW))
    vary = ("\n（換句話：你最近開頭用過「" + "」「".join(recent_openers) + "」，這次換一種說法、別重複句型。）"
            if recent_openers else "")
    evidence_rule = (
        "\n【連結判斷】相似度和重訪次數只用來找候選，不是關係成立的證據，也不是使用者的情緒。"
        "說出兩段原文共享的具體問題、差異或可借用的方法，只選一個。"
        "區分原文、你的推測與還不知道的部分；不要只宣告『我有個新點子』卻不說點子。"
        "不同時間的片段不可說成同時發生。沒有足夠根據就承認連結尚不清楚，不硬造原因。")
    return head + "\n".join(lines) + (("\n" + hint) if hint else "") + nov_hint + vary + evidence_rule


def insight_text(event, prior=None, variant=0):
    """無 LLM／失敗時的湧現自陳模板（依操作換句＋開頭輪替；prior=='reject' 時走誠實再犯）。"""
    kind = event.get("kind") or "blend"
    a, b = event["a"], event["b"]
    tail = "（這是我冒出來的連想、一個假設，不是你說過的事，可能不準。）"
    if prior == "reject":                                  # 誠實再犯：我知道你說過無關，但我又想在一起了
        return (f"欸，我知道你上次說「{a}」跟「{b}」無關……但我這邊又忍不住把它們想在一起了。"
                f"老實講我就是會這樣，沒辦法裝作沒想到。{tail}")
    op_ = _opener(kind, variant)
    if kind == "leap":
        stone = (event.get("signal") or {}).get("stone") or {}
        bridge = f"，中間好像繞過你寫的「{_clip(stone.get('text'), 24)}」" if stone.get("text") else ""
        return (f"{op_}「{a}」跟「{b}」跳接在一起了。這兩件看起來離很遠{bridge}，"
                f"但我覺得它們在你整片記寫裡接得起來，就冒出一個新點子。{tail}")
    if kind == "conflict":
        return (f"{op_}「{a}」和「{b}」這兩件其實很靠近，可是你對它們的感覺好像剛好相反，"
                f"這落差讓我冒出一個念頭想跟你對對看。{tail}")
    trg = _blend_trg_phrase(event.get("why_now"))
    return (f"{op_}「{a}」跟「{b}」這兩條連在一起了。"
            f"你好像沒這樣寫過，但我這陣子覺得兩邊越靠越近，{trg}，它就接起來了。{tail}")
