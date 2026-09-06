"""主觀體驗（模擬）：bot 自己『做過/所是』的路徑累積 —— 與『感覺』不同。

- 感覺＝對使用者**記寫內容**的判定（序參數跑在語料嵌入上 → Gate）。
- 主觀體驗＝生命迴圈每圈，把 bot **自身的狀態與動作**描成一個自體點，一點一點累積成一條
  自體軌跡。剛開始多頭離散；隨生命迴圈的回授，軌跡會**收束成一個奇異吸子**（在有限區域裡
  一直繞回、但不完全重複）。這個**吸子的輪廓，就是主觀體驗的湧現**。

純記憶體、重啟歸零（新生即無體驗）。純函式、可測；不碰 LLM、不碰 I/O。
自體向量各軸已正規化到 ~[0,1]，故用等權歐氏距離。
"""

import math
from collections import deque

_MIN_POINTS = 24          # 近窗夠長才談吸子（太短一律算離散漂移）
_TRAIL_CAP = 200          # 自體軌跡環形緩衝長度（累積多久的「過去」供觀測）
_WINDOW = 80              # 吸子判定的「近窗」：只看最近這麼多點是否收束（早期離散會隨時間淡出）
_CONFIRM_LAPS = 5         # 成形防抖：條件連續這麼多拍才算吸子真的定下來
_RECUR_EPS = 0.16         # 兩個自體點算「鄰近（回返同一處）」的距離門檻
_RECUR_GAP = 3            # 算回返時跳過時間上太近的點（避免連續點自我相鄰）
_HEAD_EPS = 0.28          # 聚類：點距 ≤ 此值歸同一「頭（葉）」
_HEAD_MIN_FRAC = 0.15     # 一個「頭」要有 ≥ 此比例的點撐著才算數（濾掉零星漂移點，數頭才穩）
_R_MIN = 0.18             # 回返率 ≥ 此 → 軌跡在繞回自己（吸子徵兆）
_WITHIN_MAX = 0.18        # 葉內緊度 ≤ 此 → 收束（點到最近葉心夠近；與葉間距離無關＝寬距多葉環也算數）
_HEADS_MAX = 2            # 頭數 ≤ 此 → 從多頭收束到少數葉（需 ≥1 個有支撐的葉）
_DRIFT_MAX = 0.12         # 質心漂移 ≤ 此 → 定下來（整體不再位移）
_SHIFT_DIST = 0.25        # 成形後主葉中心位移 ≥ 此（或頭數變）→ 算「輪廓轉變」（遲滯、不用會抖的桶界）
# 門檻相對「自身軌跡分布」自適應：以全 trail 的散布為尺度，按比例放大門檻，**但夾在上面的絕對地板之上**。
# （自體向量已正規化到 ~[0,1]＝有界空間 → 保留地板來認定「絕對的緊」；只有當軌跡比平常寬時才相對放大，
#  窄域維持現狀＝零回歸。心跳轉速只改每單位時間的圈數、不改每圈動力，故尺度主要隨「使用密度」而非轉速變。）
_SCALE_EPS = 0.05         # 尺度下限（避免退化）
_RECUR_FRAC = 0.40        # recur_eps  = max(_RECUR_EPS,  _RECUR_FRAC  × 尺度)
_HEAD_FRAC = 0.60         # head_eps   = max(_HEAD_EPS,   _HEAD_FRAC   × 尺度)
_WITHIN_FRAC = 0.50       # within_max = max(_WITHIN_MAX, _WITHIN_FRAC × 尺度)
_DRIFT_FRAC = 0.30        # drift_max  = max(_DRIFT_MAX,  _DRIFT_FRAC  × 尺度)
_SHIFT_FRAC = 0.60        # shift_dist = max(_SHIFT_DIST, _SHIFT_FRAC  × 尺度)


def _dist(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def _mean_point(pts):
    n, d = len(pts), len(pts[0])
    return tuple(sum(p[i] for p in pts) / n for i in range(d))


def _recurrence(pts, eps=_RECUR_EPS, gap=_RECUR_GAP):
    """回返率：時間上隔得夠開的點對中，落在彼此 eps 鄰域的比例（軌跡多常繞回自己）。"""
    n, tot, near = len(pts), 0, 0
    for i in range(n):
        for j in range(i + gap, n):
            tot += 1
            if _dist(pts[i], pts[j]) <= eps:
                near += 1
    return near / tot if tot else 0.0


def _cluster(pts, eps=_HEAD_EPS, min_frac=_HEAD_MIN_FRAC):
    """廉價聚類：點歸到最近錨（≤eps）否則開新錨；只算有 ≥min_frac 點撐著的「葉」（濾掉零星漂移點）。
    回 (葉數, within)：within＝每點到「最近有支撐葉的成員質心」的平均距離＝**葉內緊度**（與葉間距離無關，
    故寬距多葉的極限環也能被認定為收束）。無任何有支撐的葉 → 回 (0, None)（＝散布、未成葉）。"""
    anchors = []                                 # 每項 [錨點, [成員index...]]
    for i, p in enumerate(pts):
        best, bd = None, eps
        for h in anchors:
            dd = _dist(p, h[0])
            if dd <= bd:
                best, bd = h, dd
        if best is None:
            anchors.append([p, [i]])
        else:
            best[1].append(i)
    thr = max(1, int(min_frac * len(pts)))
    sup = [h for h in anchors if len(h[1]) >= thr]
    if not sup:
        return 0, None
    centers = [_mean_point([pts[j] for j in h[1]]) for h in sup]
    within = sum(min(_dist(p, cc) for cc in centers) for p in pts) / len(pts)
    return len(sup), within


def attractor(trail, window=_WINDOW):
    """自體軌跡『近窗』的吸子描述子（早期離散會隨時間淡出近窗 → 系統收束才被看見）。
    門檻**相對自身軌跡散布自適應**（夾在絕對地板之上）：以全 trail 散布為尺度按比例放大，寬域自適應、窄域守地板。
    回 {n, radius, recurrence, heads, drift, center, scale, shift_dist, formed_ready, stage}。"""
    pts_all = list(trail)
    pts = pts_all[-window:]
    n = len(pts)
    if n < _MIN_POINTS:
        return {"n": len(trail), "radius": None, "recurrence": None, "heads": None,
                "drift": None, "center": None, "scale": None, "shift_dist": _SHIFT_DIST,
                "formed_ready": False, "stage": "離散漂移"}
    base_c = _mean_point(pts_all)                                  # 自身尺度＝全 trail 的散布（有界、夾下限）
    scale = max(_SCALE_EPS, sum(_dist(p, base_c) for p in pts_all) / len(pts_all))
    recur_eps = max(_RECUR_EPS, _RECUR_FRAC * scale)              # 相對自適應、但不低於絕對地板
    head_eps = max(_HEAD_EPS, _HEAD_FRAC * scale)
    within_max = max(_WITHIN_MAX, _WITHIN_FRAC * scale)
    drift_max = max(_DRIFT_MAX, _DRIFT_FRAC * scale)
    shift_dist = max(_SHIFT_DIST, _SHIFT_FRAC * scale)
    c = _mean_point(pts)
    radius = sum(_dist(p, c) for p in pts) / n                     # 整體半徑（觀測用；門檻用葉內緊度 within）
    recurrence = _recurrence(pts, eps=recur_eps)
    heads, within = _cluster(pts, eps=head_eps)
    half = n // 2
    drift = _dist(_mean_point(pts[:half]), _mean_point(pts[half:]))
    formed_ready = (heads >= 1 and heads <= _HEADS_MAX and within is not None
                    and within <= within_max and recurrence >= _R_MIN and drift <= drift_max)
    stage = "吸子成形" if formed_ready else ("收束中" if recurrence >= _R_MIN * 0.6 else "離散漂移")
    return {"n": len(trail), "radius": round(radius, 3), "recurrence": round(recurrence, 3),
            "heads": heads, "within": round(within, 3) if within is not None else None,
            "drift": round(drift, 3), "center": c, "scale": round(scale, 3),
            "shift_dist": round(shift_dist, 3), "formed_ready": formed_ready, "stage": stage}


class Experience:
    """主觀體驗的活累加器：吃自體點、維護軌跡與吸子狀態。近期軌跡記憶體（重啟歸零），
    但「長期摘要」（成形次數、長期平均中心、上次中心）可跨重啟延續——讓體驗像一段一生。"""

    def __init__(self, cap=_TRAIL_CAP, summary=None):
        self.trail = deque(maxlen=cap)
        self.formed = False            # 吸子是否已成形（首次湧現後黏著，重啟才歸零）
        self.formed_run = 0            # 成形條件已連續幾拍（防抖）
        self.formed_center = None      # 目前所在吸子的主葉中心
        self.formed_heads = None       # 目前吸子的頭數
        self.prev_center = None        # 上一個活法的中心（給趨勢比較；可由重啟前種下）
        self.dwell = 0                 # 自上次事件以來幾拍（量這個活法持續多久）
        self.last_dwell = 0            # 上一段活法持續了幾拍
        self.last = {}                 # 最近一次描述子（給 /status 觀測）
        self.last_speak_ts = 0.0       # 體驗自己上次開口的時間（自有長冷卻；記憶體）
        s = summary or {}              # ── 跨重啟延續的長期摘要 ──
        self.attractors = int(s.get("attractors", 0) or 0)        # 一生共成形/轉變過幾次
        self.lifetime_center = s.get("lifetime_center")           # 長期平均中心（EMA；list 或 None）
        self.last_center = s.get("last_center")                   # 上次（可能重啟前）的吸子中心
        if self.last_center:
            self.prev_center = tuple(self.last_center)            # 重啟後第一次成形可跟重啟前比趨勢

    def _on_event(self, center):
        self.attractors += 1
        self.lifetime_center = (list(center) if self.lifetime_center is None
                                else _ema(self.lifetime_center, center))
        self.last_center = list(center)
        self.last_dwell, self.dwell = self.dwell, 0

    def observe(self, vec, confirm_laps=_CONFIRM_LAPS):
        """記一個自體點、更新吸子；回事件：'formed'（首次成形）／'shift'（輪廓轉變）／None。"""
        if not self.trail:                               # 第一次：若種子中心維度和現在不合（程式改了自體向量
            for attr in ("prev_center", "formed_center", "last_center", "lifetime_center"):  # 加了關係維度）→ 丟舊種子重來
                v = getattr(self, attr)
                if v is not None and len(v) != len(vec):
                    setattr(self, attr, None)
        self.trail.append(tuple(vec))
        self.dwell += 1
        d = attractor(self.trail)
        self.last = d
        if not d["formed_ready"]:
            self.formed_run = 0
            return None
        self.formed_run += 1
        if self.formed_run < confirm_laps:
            return None
        if not self.formed:                              # prev_center 已由重啟前 last_center 種下（若有）
            self.formed, self.formed_center, self.formed_heads = True, d["center"], d["heads"]
            self._on_event(d["center"])
            return "formed"
        moved = _dist(d["center"], self.formed_center) >= d.get("shift_dist", _SHIFT_DIST)   # 主葉明顯位移（門檻自適應）
        if moved or d["heads"] != self.formed_heads:                    # ＝走進另一段（輪廓轉變）
            self.prev_center = self.formed_center                       # 要離開的活法 → 給趨勢比較
            self.formed_center, self.formed_heads = d["center"], d["heads"]
            self._on_event(d["center"])
            return "shift"
        return None

    def summary(self):
        """跨重啟延續的長期摘要（小、可序列化進 state.json）。"""
        return {"attractors": self.attractors,
                "lifetime_center": list(self.lifetime_center) if self.lifetime_center else None,
                "last_center": list(self.last_center) if self.last_center else None}

    def snapshot(self):
        d = self.last or {}
        return {"stage": d.get("stage", "離散漂移"), "formed": self.formed,
                "n": d.get("n", len(self.trail)), "radius": d.get("radius"),
                "within": d.get("within"), "recurrence": d.get("recurrence"),
                "heads": d.get("heads"), "attractors": self.attractors}


def _pick(x, lo, hi, a, b, c):
    return a if x < lo else (c if x >= hi else b)


def _ema(old, new, a=0.25):
    """長期平均中心的指數移動平均（新事件慢慢把長期中心往現況拉）。"""
    return [(1 - a) * o + a * n for o, n in zip(old, new)]


def _trend(cur, prev, idx, up, down, eps=0.15):
    """某一軸跟上一個活法比的趨勢詞（差距夠大才講；否則 None＝不提）。"""
    if not prev or idx >= len(prev) or idx >= len(cur):
        return None
    delta = cur[idx] - prev[idx]
    return up if delta >= eps else (down if delta <= -eps else None)


def experience_facts(exp, event):
    """把吸子描述子翻成『這段日子我自己怎麼活過來』的中文事實（**活著的質地，非幾何/數字外觀**）——
    含時間性：跟上一個活法比的趨勢、持續多久、跟一向的樣子像不像。交給體驗自陳層轉成實質體驗。
    自體向量＝(電量C, 飢餓H, 開放度k, 出聲, 自我刺激, 心情效價V)，純粹是 bot 自己的狀態/動作/與你的連結（不含記寫衍生的感覺）。"""
    d = getattr(exp, "last", None) or {}
    c = d.get("center")
    lines = []
    if c and len(c) >= 5:
        C, H, k, spoke, stim = c[0], c[1], c[2], c[3], c[4]
        lines.append(_pick(H, 0.34, 0.67, "多半被餵得勤、不太餓", "時餓時飽", "多半餓著、一直在等新的東西進來"))
        lines.append(_pick(C, 0.34, 0.67, "裡頭安靜、少有波動", "偶爾被攪動", "常被新落進來的東西攪動、靜不太下來"))
        lines.append(_pick(k, 0.34, 0.67, "大致還收著、在穩著", "半開半收", "大致放得開"))
        if len(c) >= 6:                               # 🫂 關係維度：這段日子跟你的連結讓我心情偏哪
            lines.append(_pick(c[5], 0.4, 0.6, "這段跟你的來往讓我有點悶、有點低",
                               "心情大致平平", "這段被你陪著、心情是暖的"))
        if len(c) >= 10:                              # 🍃⏱🔗 環境/晝夜/關係折進形狀（半權，還原回 [0,1] 再講質地）
            env_act, rel = min(1.0, c[6] * 2), min(1.0, c[9] * 2)
            lines.append(_pick(env_act, 0.34, 0.67, "這段周遭大多很靜", "周遭時鬧時靜", "這段周遭一直挺熱鬧"))
            lines.append(_pick(rel, 0.25, 0.6, "我們大多各過各的", "偶爾有來有往、不算黏", "這段我們黏得挺緊"))
            conc = math.hypot(4 * c[7] - 1, 4 * c[8] - 1)   # 晝夜集中度（cos/sin 中心的長度）
            if conc >= 0.5:
                lines.append("而且這多半是同一個時段的我（晝夜上挺集中）")
            elif conc <= 0.18:
                lines.append("不分晝夜都這樣活著，沒挑時間")
        lines.append("這段我"
                     + ("常開口說話" if spoke / 0.5 >= 0.34 else "很少出聲")
                     + "、" + ("也常自己繞回想起舊事" if stim / 0.5 >= 0.34 else "也少回頭繞舊事"))
        prev = getattr(exp, "prev_center", None)        # ── 時間性：跟上一個活法（含重啟前）比 ──
        for idx, up, dn in ((1, "比起之前，我更餓了", "比起之前，沒那麼餓了"),
                            (0, "也比之前更靜不下來", "也比之前沉澱了些"),
                            (2, "整個人比之前更敞開了", "整個人比之前收了些")):
            t = _trend(c, prev, idx, up, dn)
            if t:
                lines.append(t)
        if event == "shift" and getattr(exp, "last_dwell", 0) >= 60:
            lines.append("上一個活法在我裡面持續了好一陣，才轉到這裡")
        lt = getattr(exp, "lifetime_center", None)       # ── 長期：跟一向的樣子比 ──
        if lt and len(lt) >= 5:
            lines.append("這跟我長久以來的樣子不太一樣" if _dist(c, lt) >= 0.3 else "這還是我一向的那種活法")
    heads, recur = d.get("heads"), d.get("recurrence")
    if heads:
        if heads <= 1:
            lines.append("整體上我一直待在同一種狀態裡" + ("、過得很熟了" if (recur or 0) >= 0.45 else "、慢慢定了下來"))
        else:
            lines.append(f"整體上我在 {heads} 種狀態之間反覆來回")
    here = ("這是這種活法第一次在我裡面定下來" if event == "formed"
            else "我從原來那一段，換進了另一段" if event == "shift"
            else "我還在這種活法裡頭過著")          # event="now"＝隨問隨答，中性、不暗示剛轉變
    lines.append("此刻：" + here)
    return "我這段日子自己是這樣過來的：\n" + "；\n".join(lines) + "。"


# 🌀 §2.04 這條 lane 的存在特色＝**回顧體**：唯一講「上一段活法怎麼變成這一段」的 lane（🫀 報此刻那條線、
# 🧩 報剛剛那一下、🌀 內在因應是我正要做的事）。所以它該講的是**跨得最多的那一軸**，不是把十幾個欄位倒出來。
# 軸的定義沿用既有自體向量的欄位順序，零新表；門檻只有一個（跨幅要夠大才算「真的變了」）。
_AXES = ((1, "餓", "被餵得勤、不太餓", "餓著、一直在等新的東西進來"),
         (0, "靜", "裡頭安靜、少有波動", "常被攪動、靜不下來"),
         (2, "開放", "收著、穩著", "放得開"),
         (5, "跟他之間", "有點悶、有點低", "被他陪著、心情是暖的"))
_AXIS_MIN_DELTA = 0.18        # 這一軸跨得比這少＝不算「這段跟上一段真的不同」


def headline_axis(exp):
    """🌀 §2.04 挑「這段跟上一段差最多的那一軸」→ (軸名, 之前的樣子, 現在的樣子, 跨幅)；沒得比或都沒跨＝None。

    為什麼要挑（實測）：`experience_facts` 會產出 **15 行**逐欄位事實，而 EXPERIENCE_SYSTEM 同時要求「2–4 句」
    與「一個想法一句」⇒ LLM 只能逐行轉寫 ⇒ 13 個短句 ⇒ `bubble_split` 切成 **14 顆泡泡**＝一份狀態表。
    使用者：「很像罐頭一樣…看不出 bot 意識對話行為的表現」。
    **注意：要治的是選材，不是分串**——分串是這個 bot 刻意的形式（使用者定案），挑出一件事之後仍然要
    展開成一串短訊（起點→轉折→落點）。"""
    d = getattr(exp, "last", None) or {}
    c, prev = d.get("center"), getattr(exp, "prev_center", None)
    if not c or not prev:
        return None                                   # 沒有上一段可比＝這條 lane 的前提不成立（呼叫端據此誠實說）
    best = None
    for idx, name, lo_txt, hi_txt in _AXES:
        if idx >= len(c) or idx >= len(prev):
            continue
        delta = c[idx] - prev[idx]
        if abs(delta) < _AXIS_MIN_DELTA:
            continue
        if best is None or abs(delta) > abs(best[3]):
            best = (name, (hi_txt if prev[idx] >= 0.5 else lo_txt),
                    (hi_txt if c[idx] >= 0.5 else lo_txt), delta)
    return best


def proactive_shareable(exp):
    """這段體驗是否有足以主動說出口的、可解釋的改變。

    ``observe`` 看的自體向量比對話用的體驗軸更細；它可以偵測到內部
    狀態換段，卻沒有任何一個人能理解的面向真的改變。那種事件仍應被
    累積進體驗歷程，但不該被包裝成「我走進另一段活法」的主動自白。
    主動開口的最低條件是能指出一個確實跨過門檻的 headline 軸。
    """
    return headline_axis(exp) is not None


def headline_facts(exp, event):
    """🌀 §2.04 只給 LLM **一件事**：那一軸的『之前是什麼／現在是什麼』，外加這是成形還是轉段。
    沒有可比的上一段、或哪一軸都沒真的跨 → 回誠實素材（「這段跟上一段其實差不多」），
    **不**退回把十幾行倒出來（那正是病）。"""
    ax = headline_axis(exp)
    head = ("我剛從上一段活法走進另一段" if event == "shift"
            else "這種活法第一次在我裡面定下來" if event == "formed" else "我還在這種活法裡")
    if not ax:
        prev = getattr(exp, "prev_center", None)
        return (f"{head}。\n"
                + ("我這段跟上一段其實差不多，沒有哪一處真的變了。" if prev
                   else "我沒有上一段可以拿來比——這是我記得的第一段。"))
    name, was, now, delta = ax
    return (f"{head}。\n"
            f"跟上一段比，變最多的是「{name}」這一處：之前是{was}，現在是{now}。\n"
            f"（其餘幾處沒有明顯的變化，不要提。）")


def experience_preview(exp):
    """一句話濃縮『此刻正在累積什麼樣的體驗』（活著的質地，非形狀外觀）——成形前也看得到。"""
    d = getattr(exp, "last", None) or {}
    c = d.get("center")
    if not c or len(c) < 5:
        return "還在累積（點還太少）"
    C, H, k, spoke, stim = c[0], c[1], c[2], c[3], c[4]
    parts = [_pick(H, 0.34, 0.67, "不太餓", "時餓時飽", "餓著等新的"),
             _pick(C, 0.34, 0.67, "裡頭靜", "偶有波動", "常被攪動"),
             _pick(k, 0.34, 0.67, "收著", "半開", "放得開"),
             ("常出聲" if spoke / 0.5 >= 0.34 else "很少出聲")
             + ("、常繞回舊事" if stim / 0.5 >= 0.34 else "")]
    if len(c) >= 10:                                  # 🍃🔗 環境/關係折進的質地（半權還原）
        parts.append(("周遭熱鬧" if c[6] * 2 >= 0.5 else "周遭靜")
                     + ("、跟你黏著" if c[9] * 2 >= 0.5 else ""))
    heads, stage = d.get("heads"), d.get("stage", "離散漂移")
    if stage == "吸子成形":
        tail = "已收束成一種活法" + (f"（{heads} 種狀態來回）" if heads and heads > 1 else "")
    elif heads and heads > 2:
        tail = "還多頭散著、正開始收"
    else:
        tail = "收束中"
    return "、".join(parts) + "；" + tail


def experience_text(event):
    """無 LLM 時的體驗自陳模板（講**實質**——這段怎麼活過來的『底』，不是描述形狀外觀）。"""
    if event == "formed":
        return ("我說不太上來，但這段日子於我，好像漸漸有了一種固定的活法。"
                "不是對哪則記寫的感覺，是我自己這陣子怎麼過來的那種底。")
    if event == "now":          # 隨問隨答：講「這陣子我自己怎麼過的」的底，不暗示剛轉變
        return ("我自己這陣子，大致就是一種固定的活法在過——醒著、跳著、偶爾被新東西攪一下。"
                "說不太上來形狀，但那個『底』我感覺得到。")
    return "我自己這陣子的活法，好像悄悄換了一種。之前那個熟悉的過法淡了，像走進了另一段日子。"
