"""🧭 對話意圖湧現（從對方一連串對話行為**累積、浮現**一個「他這是在幹嘛」的判讀，並偵測違常）。

設計（使用者明訂）：意圖不是貼標籤分類出來的，而是**從行為累積後湧現**——像真人看到對方連發五次「早安」，
會自然讀出「這是刻意的/在逗我/在試我」，而不是傻傻把每一次都當全新招呼熱情回。本模組把這條做成**純函式**訊號：

- `intent_log`（持久化在 `othermind.user_model`，跨重生）＝對方近 N 則的意圖履歷 [{kind, ts, sig}]。
- `repetition_run`＝在滑窗內數「同類＋近似」的連發（把『連發五次早安』從靠 LLM 事後看 history 變成**程式當下算得出**）。
- `anomaly_score`＝違常程度 [0,1]，**乘上 rapport/warmth 反向阻尼**（熟人熱情連發被壓低分，不誤判成測試）。
- `classify_repetition`＝testing｜mechanical｜seeking_confirmation｜None（違常的「種類」，決定回應分流）。
- `read`＝把上面收成一個**意圖向量 IntentReading**（kind/degree/anomaly/style/level/should_question），驅動：
  (B) 適性語氣（經 persona hint 注入 grounding／greeting，**溫暖底；只在明顯 testing 才升級為好奇反問確認**）；
  (A) 主動結尾（`close_decision`，餵 monitor._maybe_close_round）。

家規：全純函式、無 IO/LLM（只吃 text/ts/kind/coupling/user_model 快照），唯一副作用集中在薄包裝 `observe`；
旗標 `DIALOGUE_INTENT_ENABLED` 關時 observe/read 首行 return＝逐位元同現狀；接地不杜撰；數字一律不外露（由 persona hint
要 LLM 用「好像一直/又一次」這種人話質感講、標推測）。
"""

import re

_PUNCT_RE = re.compile(r"[\s\W_]+", re.UNICODE)   # 去標點/空白/emoji，保留 CJK 與英數（CJK 在 Unicode 屬 \w）

_SOCIAL_KINDS = ("greeting", "smalltalk", "farewell", "backchannel")   # 短社交語：低投入連發＝多半在試/逗


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _msg_sig(text, cap=16):
    """訊息的輕量正規化指紋（小寫、去標點/空白/emoji、截斷）——用來判『近似重複』而非精確相等。純函式。"""
    t = _PUNCT_RE.sub("", (text or "").strip().lower())
    return t[:cap]


def _similar(a, b, threshold=0.85):
    """兩個指紋是否『近似同一句』。完全相同＝True；否則看字元集合重疊率（取較短者為分母）並要求長度比夠近——
    使『早安』×5 互判相似、『早安』vs『早安啊』相似、但『早安』vs『早安你好嗎最近如何』(長度差太多)與
    『早安』vs『午安』(重疊低) 皆**不**相似（守連發 vs 正常邊界）。純函式。"""
    if not a or not b:
        return False
    if a == b:
        return True
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return False
    overlap = len(sa & sb) / min(len(sa), len(sb))
    lenratio = min(len(a), len(b)) / max(len(a), len(b))
    return overlap >= threshold and lenratio > 0.5   # 嚴格 >：單字 vs 雙字(『早』vs『早安』, ratio=0.5)不算同形式


def repetition_run(intent_log, now_ts, window_sec, sim_threshold):
    """從履歷尾端往回，數『在滑窗內、與最新一則同 kind 且 sig 近似』的**連發串**。
    回 {count, kind, span_sec, density(每分鐘次數)}。空履歷→count 0。純函式、可單測。"""
    log = list(intent_log or [])
    if not log:
        return {"count": 0, "kind": None, "span_sec": 0.0, "density": 0.0}
    latest = log[-1]
    lk, lsig = latest.get("kind"), latest.get("sig", "")
    run = []
    for e in reversed(log):
        if (now_ts - (e.get("ts") or 0)) > window_sec:        # 出窗即斷（時間制）
            break
        if e.get("kind") != lk:                               # 換了意圖種類即斷
            break
        if not _similar(e.get("sig", ""), lsig, sim_threshold):
            break
        run.append(e)
    count = len(run)
    span = abs((run[0].get("ts") or 0) - (run[-1].get("ts") or 0)) if count >= 2 else 0.0
    density = (count / max(1.0, span / 60.0)) if count >= 2 else float(count)
    return {"count": count, "kind": lk, "span_sec": round(span, 1), "density": round(density, 3)}


def last_same_kind_gap(intent_log, kind, now_ts):
    """同一意圖(kind)『上一次出現』距今幾秒（不含此刻這筆＝只看 ts<now_ts）。沒有前一筆回 None。
    這是『時間納入違常』的核心訊號：**同樣的意圖、間隔太近連問**本身就違反常理——且只看 kind＋時間、
    不靠字面相似，故「現在幾點」與「現在的時間是」同屬 clock 也算得到。純函式、可單測。"""
    prior = [(e.get("ts") or 0) for e in (intent_log or [])
             if e.get("kind") == kind and (e.get("ts") or 0) < now_ts]
    return (now_ts - max(prior)) if prior else None


# ⏱ 餐別 premise 對不對得上此刻時段（用 temporal.day_part 的字面）：晚上問早餐＝時間上違反常理。
# 不同於『同一意圖連問』的時間違常，這是『問句前提 vs 現實世界時間』的常識違和——bot 有時鐘卻常漏掉。
_MEALS = (
    ("早餐", {"清晨", "早上"}, "早上"),
    ("breakfast", {"清晨", "早上"}, "早上"),
    ("午餐", {"中午", "午後"}, "中午"),
    ("中餐", {"中午", "午後"}, "中午"),
    ("lunch", {"中午", "午後"}, "中午"),
    ("晚餐", {"傍晚", "晚上"}, "傍晚到晚上"),
    ("晚飯", {"傍晚", "晚上"}, "傍晚到晚上"),
    ("dinner", {"傍晚", "晚上"}, "傍晚到晚上"),
    ("宵夜", {"晚上", "深夜"}, "深夜"),
    ("消夜", {"晚上", "深夜"}, "深夜"),
)


# 便宜前置門檻：這句『看起來可能有可檢查的前提』才值得送 LLM 通盤常理審查（省掉一堆必然 OK 的呼叫）。
# ＝是問句、或提到時間/活動領域（餐/睡/上下班/問候…）。**這只是『要不要花這次呼叫』的成本閘、不做違常判斷本身**，
# 故寬鬆認定（寧可多送、少漏）；純陳述/附和又無這些線索＝跳過。純函式、可單測。
_PREMISE_Q = ("?", "？", "嗎", "呢", "幾", "如何", "什麼", "有沒有", "是不是", "要不要", "了沒", "了嗎", "怎麼")
_PREMISE_TOPIC = ("餐", "宵夜", "消夜", "吃飯", "用餐", "睡", "起床", "醒來",
                  "上班", "下班", "上課", "放學", "通勤", "出門", "回家", "早安", "午安", "晚安")


def worth_premise_check(text):
    """這句值不值得送通盤常理審查（成本前置門檻）：是問句、或提到時段/活動領域＝可能有對得上不上的前提 → True。
    純陳述/附和又無線索、或太短 → False（跳過、省一次 LLM 呼叫）。寬鬆＝寧可多送、漏判由確定性保底兜。純函式。"""
    t = (text or "").strip()
    if len(t) < 3:
        return False
    return any(q in t for q in _PREMISE_Q) or any(w in t for w in _PREMISE_TOPIC)


# 🧑‍🏫 對話教學共識的便宜前置門檻：這句帶「對未來生效的做法語氣」才值得送共識偵測 LLM（省掉絕大多數無關閒聊的呼叫）。
# ＝出現「以後/下次/這種時候/這類/每次/記得/希望你…」這類**把當下談的方式延伸到未來**的線索。從嚴（寧可漏、別亂提議）。
_SKILL_CUES = ("以後", "下次", "之後遇到", "這種時候", "這類", "這種情況", "每次", "每當", "往後",
               "記得要", "記得在", "希望你以後", "希望你之後", "希望你能", "你可以以後", "下回", "以後遇到",
               "學起來", "學會", "記住這", "記下來", "養成習慣", "形成默契",
               # 🧑‍🏫 更廣的「教你未來這樣做」線索（保有精準靠下游 detect＋淨化＋提議確認；此處只放寬成本閘）
               "我希望你這樣", "希望你這樣", "我要你", "要你這樣", "你要記得", "從今以後", "從現在起", "以後就",
               "遇到這種", "這種事", "這種情形", "希望你都", "希望你在", "希望你記得", "麻煩你以後", "拜託你以後",
               "我希望你以後", "我希望你之後", "我希望你能", "希望你可以", "以後都", "之後都", "每一次",
               # 🧑‍🏫 §0.59：更明確的**教學動詞/祈使**線索——截圖「我教你，如果…記得主動告訴我，叫我說笑話」整串線索全落空
               # （舊表只有「記得要/記得在」漏了裸「記得」、也沒有「我教你/叫我」）。廣化成本閘，精準仍靠下游 detect＋淨化＋提議確認。
               "我教", "教你", "教過你", "記得", "叫我", "要我", "請我")


# 🧑‍🏫 §1.52 質疑/不信線索（教學提議的情境守門用）：對 bot 的自述/能力/誠實表示懷疑的句子——這種輪次把
# 「懷疑」蒸餾成做法＝答非所問（§1.27 只堵敵意；「懷疑但不敵意」是縫：截圖 10:10「每次你這麼說/我都有些
# 懷疑」is_hostile 全 False、streak 0 照樣放行）。表小而封閉、不再擴（詞表窮舉前科）：漏了＝照舊提議
# （有冷卻、可拒絕）＝安全側。
_DOUBT_CUES = ("懷疑", "不相信", "不太相信", "不信", "半信半疑", "信不過", "存疑", "質疑", "打臉",
               "騙", "唬", "證明", "測試你", "考你", "真的假的", "是嗎")


def is_doubt_text(text):
    """🧑‍🏫 §1.52：這句是不是在對 bot 表示懷疑/不信（教學提議的情境守門）。純函式、可單測。"""
    t = (text or "").strip()
    return bool(t) and any(c in t for c in _DOUBT_CUES)


def worth_skill_consensus(text):
    """這句值不值得送『對話教學共識偵測』（成本前置門檻）：帶「以後/下次/這種時候/記得/希望你…」這類把當下做法
    延伸到未來的線索 → True；否則 False（跳過、省一次 LLM 呼叫）。從嚴＝寧可漏判（不亂提議騷擾）。純函式、可單測。"""
    t = (text or "").strip()
    if len(t) < 4:
        return False
    return any(c in t for c in _SKILL_CUES)


def meal_premise_mismatch(text, day_part):
    """使用者問的『某一餐』對不對得上此刻時段（day_part＝temporal.day_part(hour) 的字面）。
    對不上＝時間上的違反常理（如晚上問早餐）→ 回 {'meal','expect_word','now_part'}；對得上/無餐別→ None。純函式、可單測。"""
    t = (text or "").lower()
    for word, ok, expect_word in _MEALS:
        if word in t and day_part and day_part not in ok:
            return {"meal": word, "expect_word": expect_word, "now_part": day_part}
    return None


def anomaly_score(run, coupling, user_model, repeat_n=4):
    """連發違常程度 [0,1]＝(連發超基準 × 密集度) × rapport/warmth 反向阻尼。
    未達 repeat_n 連發→0。阻尼讓**熟人熱情連發**分數被壓低（不誤判成測試）；阻尼下限 0.35＝**最熟最暖的關係**
    其連發分數封頂約 0.35（< 預設 probe 門檻 0.6）＝**對最信任的人給最大寬容、不對他反問**（刻意的設計取捨，
    非「極端重複一定會反問」）。一般熟人(中等 rapport/warmth)阻尼較輕、仍會浮現。純函式。"""
    count = run.get("count", 0)
    if count < repeat_n:
        return 0.0
    over = _clamp((count - repeat_n + 1) / 3.0, 0.0, 1.0)     # 4→.33 5→.67 6+→1
    dens = _clamp(run.get("density", 0.0) / 1.0, 0.0, 1.0)    # ≥1次/分 飽和
    base = _clamp(0.55 * over + 0.45 * dens, 0.0, 1.0)
    um = user_model or {}
    rapport = max(0.0, float(um.get("rapport", 0.0) or 0.0))
    warmth = max(0.0, float(um.get("warmth", 0.0) or 0.0))
    damp = max(0.35, 1.0 - 0.6 * rapport - 0.3 * warmth)      # 熟＋暖 → 阻尼；下限 0.35 不歸零
    return round(_clamp(base * damp, 0.0, 1.0), 3)


def classify_repetition(run, coupling, user_model, repeat_n=4):
    """違常連發的『種類』：投入仍高(Î_user≥.55)＝真的在求確認(seeking_confirmation)；否則短社交語連發＝在試/逗你(testing)；
    其餘＝機械式(mechanical)。未達門檻→None。純函式（只是讀現成訊號、不讀心）。"""
    if run.get("count", 0) < repeat_n:
        return None
    iu = (getattr(coupling, "i_user", 0.0) if coupling is not None else 0.0) or 0.0
    if iu >= 0.55:
        return "seeking_confirmation"
    if run.get("kind") in _SOCIAL_KINDS:
        return "testing"
    return "mechanical"


def intent_degree(coupling):
    """對話意圖『程度』＝沿用 coupling 現成活力純量 max(I_bot, Î_user)∈[0,1]（不另算）。純函式。"""
    if coupling is None:
        return 0.0
    return round(_clamp(max(getattr(coupling, "i_bot", 0.0) or 0.0,
                            getattr(coupling, "i_user", 0.0) or 0.0), 0.0, 1.0), 3)


def _level(anomaly, probe_thr):
    """把違常程度量化成 0..3（餵 persona hint 升級語氣；鏡像 verbosity/self_fatigue 的 level 慣例）。"""
    if anomaly < probe_thr * 0.7:
        return 0
    if anomaly < probe_thr:
        return 1
    if anomaly < 0.85:
        return 2
    return 3


def repeat_fatigue(user_repeat, kind, sig, mood, now_ts, window_sec, base_tol, mood_band, sim_threshold=0.85):
    """🧭 使用者同類意圖在窗內連發 × 當下心情 → 不耐等級 0..3（鏡像 self_asks 的 _self_voice_mod：tol=base±心情）。
    純函式：吃 user_repeat 快照與 now，回 (level:int, n:int, next_rec:dict)；呼叫端負責把 next_rec 寫回（仿 last_questioned_ts
    由呼叫端寫、不在 read() 裡動 state）。窗外或主題簽章不近似（換話題/答了別的）→ n 歸 1＝重新數，不把不相干的話累進不耐。
    sim_threshold＝主題近似門檻，須與 repetition_run/read() 的 intent_sim_threshold **同一把**（否則『連發判定軸』與
    『不耐累加軸』對「算不算同主題」會分歧、累加器在非預設門檻下永遠歸零＝升級不發）；預設 0.85＝沿用 _similar 預設。"""
    rec = (user_repeat or {}).get(kind)
    in_window = bool(rec and window_sec and (now_ts - (rec.get("ts") or 0)) < window_sec)
    same_topic = bool(rec and _similar(sig or "", rec.get("sig") or "", sim_threshold))   # sig 不近似＝換話題 → 不接續、歸零
    n = (rec.get("n", 0) + 1) if (in_window and same_topic) else 1
    tol = base_tol + (1 if mood > mood_band else 0) - (1 if mood < -mood_band else 0)   # 心情好→更耐、心情差→更快煩
    level = max(0, min(3, n - tol))
    return level, n, {"n": n, "ts": now_ts, "sig": sig or ""}


# ── 薄狀態寫入（唯一副作用）──────────────────────────────────────────
def observe(state, text, kind, now_ts, cfg):
    """把這次互動的意圖記進履歷 user_model['intent_log']（環形最後 INTENT_LOG_MAX，跨重生）。旗標關＝no-op。"""
    if not getattr(cfg, "dialogue_intent_enabled", True):
        return
    um = dict(getattr(state, "user_model", None) or {})
    log = list(um.get("intent_log") or [])
    log.append({"kind": kind, "ts": now_ts, "sig": _msg_sig(text)})
    cap = max(1, int(getattr(cfg, "intent_log_max", 20)))
    um["intent_log"] = log[-cap:]
    state.user_model = um


# ── 讀（純：吃快照、不改 state）：意圖向量 IntentReading ───────────────
def read(state, coupling, now_ts, cfg):
    """把履歷＋耦合讀成一個意圖向量（回 dict 或 None）。**不改 state**（last_questioned_ts 由呼叫端在真的反問時寫）。
    style：'none'＝無感違常不注入；'warm'＝溫暖呼應這份重複（底色）；'curious_probe'＝好奇反問確認（**只在 testing
    ＋過門檻＋INTENT_DEGREE_DRIVE 開＋不在反問冷卻內**時升級＝使用者選的「溫暖底、測試才反問」）。"""
    if not getattr(cfg, "dialogue_intent_enabled", True):
        return None
    um = getattr(state, "user_model", None) or {}
    log = um.get("intent_log") or []
    window = int(getattr(cfg, "intent_repeat_window_sec", 300))
    sim = float(getattr(cfg, "intent_sim_threshold", 0.85))
    n = int(getattr(cfg, "intent_repeat_n", 4))
    probe_thr = float(getattr(cfg, "anomaly_probe_threshold", 0.6))
    drive = bool(getattr(cfg, "intent_degree_drive", True))
    run = repetition_run(log, now_ts, window, sim)
    anomaly = anomaly_score(run, coupling, um, n)
    akind = classify_repetition(run, coupling, um, n) if anomaly > 0 else None
    style, level, should_q = "none", 0, False
    if anomaly >= probe_thr * 0.7:                        # 有感的違常才出 hint
        level = _level(anomaly, probe_thr)
        if drive and anomaly >= probe_thr and akind == "testing":
            style, should_q = "curious_probe", True
        else:
            style = "warm"
    # ⏱ 時間納入違常：同一意圖(kind)間隔太近再現（即使字面不同、或未達連發數）＝違反常理的時間訊號 → 至少溫暖點出。
    # 旗標關＝整段跳過、byte-identical；不主動升級成反問（反問仍只由上面的 testing 路徑決定）。
    if getattr(cfg, "time_anomaly_enabled", False) and log:
        gap = last_same_kind_gap(log, log[-1].get("kind"), now_ts)
        if gap is not None and gap < max(1, int(getattr(cfg, "time_anomaly_gap_sec", 120))):
            if style == "none":
                style, level = "warm", max(level, 1)
            akind = akind or "rapid_repeat"
    if should_q:                                         # 反問自有冷卻：本輪剛反問過就降回溫暖、不重複追問
        last_q = um.get("last_questioned_ts", 0) or 0
        cd = max(0, int(getattr(cfg, "intent_question_cooldown_min", 30))) * 60
        if last_q and (now_ts - last_q) < cd:            # last_q==0＝從未反問 → 不算冷卻
            should_q, style = False, "warm"
    return {"kind": run.get("kind"), "count": run.get("count", 0), "degree": intent_degree(coupling),
            "anomaly": anomaly, "anomaly_kind": akind, "style": style, "level": level,
            "should_question": should_q, "ts": now_ts}


def close_decision(state, coupling, now_ts, cfg):
    """主動結尾的『意圖/違常』判定（餵 monitor._maybe_close_round；時間/問句條件由 monitor 補）。回 (should, reason)。
    - probe_settled：先前已對違常好奇反問過、現仍違常、且對方已淡下來（沉默夠久）→ 帶玩心暖收這一輪。
    - natural_convergence：耦合這拍剛把一輪收掉（雙方意向性趨 0）→ 把『被晾』改寫成主人翁式暖收。
    旗標 PROACTIVE_CLOSE_ENABLED 關＝(False, None)。純函式（只讀快照）。"""
    if not getattr(cfg, "proactive_close_enabled", True):
        return (False, None)
    um = getattr(state, "user_model", None) or {}
    last_q = um.get("last_questioned_ts", 0) or 0
    last_u = getattr(state, "last_user_msg_ts", 0) or 0
    window = int(getattr(cfg, "intent_repeat_window_sec", 300))
    if last_q and last_u and (now_ts - last_q) < window * 3:   # 近 ~15 分內反問過（last_u>0 防剛重生 last_user_msg_ts=0 誤判 quiet）
        # 用較寬的窗（window*3）重看那串連發是否仍在履歷裡——因「淡了(quiet)」的沉默通常已超過 5 分窄窗、
        # 會把連發都擠出窄窗，故 still-anomalous 改看寬窗（連發仍可見＝那波違常確實發生過、不是誤判）。
        run = repetition_run(um.get("intent_log") or [], now_ts, window * 3,
                             float(getattr(cfg, "intent_sim_threshold", 0.85)))
        still = anomaly_score(run, coupling, um, int(getattr(cfg, "intent_repeat_n", 4))) \
            >= float(getattr(cfg, "anomaly_probe_threshold", 0.6))
        quiet = (now_ts - last_u) >= max(60, int(getattr(cfg, "soothe_after_min", 7)) * 60)
        if still and quiet:
            return (True, "probe_settled")
    if coupling is not None and getattr(coupling, "just_closed", None):
        return (True, "natural_convergence")
    return (False, None)
