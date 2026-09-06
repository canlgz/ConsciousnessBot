"""序參數計算（§3）——純 Python（不依賴 numpy/scipy，214 筆規模 O(n²) 足夠、零安裝負擔）。

三個序參數刻畫「結構有沒有成形」：
  Ω_recur — RQA 時序決定性（軌跡有沒有彎回成形）＋虛無檢定 z
  Ω_perc  — 滲流（操作門檻 τ* 下整合是否連成巨型分量）
  Ω_dxi   — 分化×整合（是否進入臨界帶）

所有向量先單位正規化，使內積＝餘弦。
"""

import math
import random
from collections import Counter


def unit(v):
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _eucl(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def centroid(vectors):
    """成員（單位向量）平均後再單位化＝群心。"""
    if not vectors:
        return None
    dim = len(vectors[0])
    acc = [0.0] * dim
    for v in vectors:
        for i, x in enumerate(v):
            acc[i] += x
    return unit(acc)


# ── Ω_recur：RQA 時序決定性 + 虛無檢定 ───────────────────────────────
def _det(order, D, eps, lmin):
    m = len(order)
    R = [[1 if (i != j and D[order[i]][order[j]] <= eps) else 0 for j in range(m)] for i in range(m)]
    total = sum(R[i][j] for i in range(m) for j in range(i + 1, m))
    if total == 0:
        return 0.0
    on_lines = 0
    for k in range(1, m):                 # 對角線偏移
        run = 0
        i = 0
        while i + k < m:
            if R[i][i + k]:
                run += 1
            else:
                if run >= lmin:
                    on_lines += run
                run = 0
            i += 1
        if run >= lmin:
            on_lines += run
    return on_lines / total


def omega_recur(vectors, RR=0.10, lmin=2, n_shuffle=500, z_star=2.0, seed=0):
    """vectors：依時間排序的單位向量序列。回傳 DET / z / significant。"""
    m = len(vectors)
    if m < 4:
        return {"DET": 0.0, "null_mean": 0.0, "z": 0.0, "significant": False, "n": m}
    D = [[0.0] * m for _ in range(m)]
    for i in range(m):
        for j in range(i + 1, m):
            d = _eucl(vectors[i], vectors[j])
            D[i][j] = D[j][i] = d
    ut = sorted(D[i][j] for i in range(m) for j in range(i + 1, m))
    eps = ut[max(0, min(len(ut) - 1, int(RR * len(ut))))]
    det = _det(list(range(m)), D, eps, lmin)
    rng = random.Random(seed)
    nulls = []
    base = list(range(m))
    for _ in range(n_shuffle):
        rng.shuffle(base)
        nulls.append(_det(base, D, eps, lmin))
    mean = sum(nulls) / len(nulls)
    var = sum((x - mean) ** 2 for x in nulls) / len(nulls)
    std = math.sqrt(var) or 1e-9
    z = (det - mean) / std
    return {"DET": round(det, 4), "null_mean": round(mean, 4), "z": round(z, 3),
            "significant": z > z_star, "n": m}


# ── Ω_perc：滲流（union-find）────────────────────────────────────────
def omega_perc(all_vectors, region_indices, tau_star=0.78):
    """all_vectors：全記憶層單位向量；region_indices：區域 R 在其中的索引。"""
    n = len(all_vectors)
    if n < 2:
        return {"lcc": 0.0, "region_in_giant": False, "n": n}
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(n):
        vi = all_vectors[i]
        for j in range(i + 1, n):
            if dot(vi, all_vectors[j]) >= tau_star:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[ri] = rj
    comp = Counter(find(i) for i in range(n))
    giant_root, giant_size = comp.most_common(1)[0]
    region = list(region_indices)
    in_giant = sum(1 for idx in region if 0 <= idx < n and find(idx) == giant_root)
    return {"lcc": round(giant_size / n, 4),
            "region_in_giant": bool(region) and in_giant > len(region) / 2,
            "n": n}


def percolation_transition(all_vectors, taus=None):
    """診斷用：由高到低掃 τ，回傳 LCC 單步躍升 > 0.25 的相變點 τ。"""
    if taus is None:
        taus = [round(0.95 - 0.05 * k, 2) for k in range(14)]   # 0.95→0.30
    prev = None
    transitions = []
    for tau in taus:
        lcc = omega_perc(all_vectors, [], tau)["lcc"]
        if prev is not None and lcc - prev > 0.25:
            transitions.append(tau)
        prev = lcc
    return transitions


# ── 適應性門檻（一次 O(n²)：邊排序＋union-find，不重算）──────────────────
def pairwise_edges(vectors):
    """回傳 (依 cos 由高到低排序的邊 [(w,i,j)], 所有 cos 值 list)。"""
    n = len(vectors)
    edges, coss = [], []
    for i in range(n):
        vi = vectors[i]
        for j in range(i + 1, n):
            w = dot(vi, vectors[j])
            edges.append((w, i, j))
            coss.append(w)
    edges.sort(key=lambda e: -e[0])
    return edges, coss


def adaptive_tau_from_edges(edges, n, default, jump=0.25):
    """這個語料「連成一塊」的門檻＝由高 cos 往低加邊時，巨型分量第一次大躍升處的 cos。"""
    if n < 4 or not edges:
        return default
    parent = list(range(n))
    size = [1] * n

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    giant, prev = 1, 1.0 / n
    for w, i, j in edges:
        ri, rj = find(i), find(j)
        if ri != rj:
            if size[ri] < size[rj]:
                ri, rj = rj, ri
            parent[rj] = ri
            size[ri] += size[rj]
            giant = max(giant, size[ri])
        lcc = giant / n
        if lcc - prev > jump:
            return round(w, 4)
        prev = lcc
    return default


def baseline_from_cos(coss, q=0.5):
    """語料的相似度基線（預設中位數對相似度）——相對門檻用。"""
    if not coss:
        return 0.5
    s = sorted(coss)
    return round(s[min(len(s) - 1, int(q * len(s)))], 4)


def mean_std(xs):
    """語料相似度分布的 μ、σ——通盤適應門檻 τ = μ + k·σ 用。"""
    if not xs:
        return 0.0, 0.0
    m = sum(xs) / len(xs)
    v = sum((x - m) ** 2 for x in xs) / len(xs)
    return m, math.sqrt(v)


def perc_from_edges(edges, n, region_indices, tau_star):
    """用已排序的邊在 τ* 下建圖（邊由高到低，低於 τ* 即停）。"""
    parent = list(range(n))
    size = [1] * n

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for w, i, j in edges:
        if w < tau_star:
            break
        ri, rj = find(i), find(j)
        if ri != rj:
            if size[ri] < size[rj]:
                ri, rj = rj, ri
            parent[rj] = ri
            size[ri] += size[rj]
    comp = Counter(find(i) for i in range(n))
    giant_root, giant_size = comp.most_common(1)[0]
    region = list(region_indices)
    in_giant = sum(1 for idx in region if 0 <= idx < n and find(idx) == giant_root)
    return {"lcc": round(giant_size / n, 4),
            "region_in_giant": bool(region) and in_giant > len(region) / 2, "n": n}


# ── Ω_dxi：分化×整合 ─────────────────────────────────────────────────
def omega_dxi(centroids, intra_cohesion, diff_min=0.0, int_min=0.5):
    """centroids：{label: 單位群心}；intra_cohesion：{label: 群內平均對群心餘弦}。"""
    labels = list(centroids)
    if len(labels) < 2:
        return {"integration": 0.0, "differentiation": 0.0, "in_critical_band": False, "topics": len(labels)}
    pair_cos = []
    for i in range(len(labels)):
        for j in range(i + 1, len(labels)):
            pair_cos.append(dot(centroids[labels[i]], centroids[labels[j]]))
    integration = sum(pair_cos) / len(pair_cos)
    diffs = []
    for i, li in enumerate(labels):
        nearest = max(dot(centroids[li], centroids[lj]) for j, lj in enumerate(labels) if j != i)
        diffs.append(intra_cohesion.get(li, 0.0) - nearest)
    differentiation = sum(diffs) / len(diffs)
    return {"integration": round(integration, 4), "differentiation": round(differentiation, 4),
            "in_critical_band": differentiation > diff_min and integration > int_min,
            "topics": len(labels)}
