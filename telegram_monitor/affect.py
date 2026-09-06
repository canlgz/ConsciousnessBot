"""🌡️ 計算情緒（內發、會評價、會調制知覺）——把「感知→感覺→行動」那條生命迴圈接上一條情緒迴授。

問題：bot 本來就有一個三維情緒底（內在熵 V 心情／C 喚醒／H 飢餓），但它是**被動的**——V 多半是**鏡像你**
（你暖我就暖）＋衰減，而且情緒只染**語氣**、從不回頭影響「我怎麼感覺你的記寫」。所以同一批記寫，bot 開心或
低落時感覺一模一樣＝沒有主觀體驗。

本層在那個核心之上，加一條**評價→詮釋→引導→重新知覺**的迴路（appraisal theory＋mood-congruent perception）：

1. **計算情緒（自己的，不是鏡像）**：`appraise` 對每個事件做**評價**——關鍵是**期待落差**（prediction error：
   這次的消息相對「我先前所信」如何），落差才是情緒**自己生成**的那一筆（驚喜/悵然由我的預期決定，不是你給的價性）；
   再依此刻 V/C/H＋最顯著的事件源（我的意圖成了？對你的記寫起感覺了？被晾久了？被打斷？）給出一個**離散情緒**
   ＋**行動傾向**（趨近/安定/探尋/退縮/平）。
2. **影響回應方向**：`stance` 把這份情緒攤成 grounding——不只音色，而是**方向**（想湊近 vs 想安靜、主動 vs 收）。
3. **帶動對記寫的新感覺＝主觀體驗**：`k_affect_adj` 用此刻喚醒/傾向去**調制感覺鏈的門檻 k**——喚醒高/趨近 → 降 k
   → 同一批記寫**更容易被打動**；退縮 → 升 k → 同樣的東西**這次激不起感覺**。再加**主題情緒啟動**（primed）：被我
   情緒上點亮過的線，下次讀它更敏感。**同資料、因我此刻的情緒而感覺不同**，這就是模擬的主觀體驗。

迴圈閉合：appraise → 情緒 →（stance 改回應方向＋k 調制改知覺）→ 回應/重新讀記寫 → 那本身又是事件 → 再 appraise。
體內平衡（防迴授滾雪球）：V/C 由內在熵自帶往中性的衰減；k 調幅夾在 `AFFECT_K_CLAMP` 內；期待落差的 V 擾動有界。
接地：只調**顯著性/門檻**與**語氣方向**，**絕不**虛構記寫內容。`state.affect` 是記憶體狀態（此刻的情緒是當下活出來的；
情緒底色 V/H 仍由內在熵 `entropy_carryover` 半延續跨重生）。
"""

from . import circumplex   # 🧭💗 §1.02 整合：離散標籤的維度退路／知覺調制 共用同一張座標（單一象限語彙）

W_SURPRISE = 0.15        # 期待落差（prediction error）對 V 的權重＝情緒「自己生成」的那一筆（有界）
W_PE_AROUSAL = 0.10      # 🧭 §1.02 |落差| 對 A（慢喚起軸）的權重——**驚訝本身**（不論好壞）都醒一下（circumplex 上半）
W_SHOCK_V = 0.45         # ⚡ §1.03 突發瞬跳：|pe| ≥ PE_STRONG（明顯違背期待）時的**額外** V 位移權重——夠大才跨得過象限
W_SHOCK_A = 0.35         # ⚡ §1.03 突發瞬跳的額外 A 位移（驚嚇必醒：好壞消息當下都是激起，之後靠每圈衰減自然回落/下沉）
W_CONTENT_A = 0.05       # 🧭 §1.02 內容感受對 A 的權重——沉的把 A 一起壓（左下）、暖的托一點（右上）；小、有界、靠衰減拉回
W_CONTENT = 0.08         # 🫧 你寫的內容的感受（價性×強度）對 V 的權重＝被你寫的東西牽動（小、有界，靠內在熵衰減拉回）
PE_STRONG = 0.5          # 落差到這算「明顯」（驚喜/悵然）
CONTENT_STRONG = 0.3     # 內容價性到這算「明顯」→ 給「被你寫的牽動」這個離散源
AFFECT_K_GAIN = 0.7      # 喚醒對感覺門檻 k 的調幅（喚醒越高→越易被打動→越負）
AFFECT_K_WITHDRAW = 0.2  # 退縮時再鈍一點（正 k）
AFFECT_K_CLAMP = 0.6     # k 調幅上下限（體內平衡：防情緒→更易感→更強情緒的迴授滾雪球）
AFFECT_PRIME_GAIN = 0.35 # 主題情緒啟動：對被點亮的線，門檻再降一點（更敏感）
_PRIME_DECAY = 0.85      # 每次 appraise，舊的點亮淡去
_PRIME_ADD = 0.5         # 一次觸動/推進，點亮多少
_PRIME_MIN = 0.05        # 太弱就清掉


# ── 感覺詞表（單一真相）─────────────────────────────────────────────────
# 與 `_label` 產出的離散情緒詞同源（這裡列出可被「自陳承接」抽回的感覺**核心詞**）。
# 用途有二：(1) `_label` 仍各自回完整短語；(2) 對話連貫的 self-prior 安全網
# （`monitor._self_prior_fact`）只用這份詞表，從 convo_history 末則 model 句的**真實文本**
# 擷取「我剛說過的那個感覺詞 X」——絕不從 affect/workspace/topic-reading 取（那些每拍漂移、
# 會抽到相反方向）。集中成常數＝兩處同源，避免抽到錯方向。詞依長度排序（長詞優先，免「煩」先於「煩躁」）。
FEELING_WORDS = (
    "被攪動",                                                      # 🧭 §1.06(A4) circumplex 八分區語彙（3 字，長詞優先）
    "雀躍", "煩躁", "低落", "悵然", "滿足", "驚喜", "寂寞", "踏實", "悶悶",
    "興奮", "緊繃", "激動", "愉快", "明亮", "平靜", "安穩", "沉靜", "消沉",   # 🧭 §1.06(A4) 補八分區詞（自陳講過→追問承接得回）
    "暖", "悶", "沉", "順利", "穩", "倦",
)


def extract_feeling_word(text):
    """從一段中文文本擷取**最先出現**的感覺詞（用 FEELING_WORDS 詞表，長詞優先）。
    抽不到回 ''（誠實 unknown，不硬填）。純函式：只比對文本、不碰任何漂移狀態源。"""
    t = text or ""
    best = None       # (位置, 詞)
    for w in FEELING_WORDS:
        i = t.find(w)
        if i >= 0 and (best is None or i < best[0] or (i == best[0] and len(w) > len(best[1]))):
            best = (i, w)
    return best[1] if best else ""


def opposite_feeling(word):
    """給定一個感覺詞，回它「方向相反」的代表詞（給測試/防線比對方向用）；不在表內回 ''。"""
    pos = {"順利", "暖", "踏實", "雀躍", "滿足", "穩", "興奮", "愉快", "明亮", "平靜", "安穩"}
    neg = {"煩躁", "低落", "悵然", "寂寞", "悶悶", "悶", "沉", "緊繃", "消沉", "倦"}
    if word in pos:
        return "煩躁"
    if word in neg:
        return "順利"
    return ""


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def fresh():
    return {"valence": 0.0, "arousal": 0.0, "hunger": 0.0, "label": "平", "tendency": "steady",
            "source": None, "pe": 0.0, "primed": {}, "ts": 0}


# ── 評價：事件 → 此刻的情緒（自己的） ─────────────────────────────────────
def _dominant_source(ev, pe, v, h):
    """這次評價最顯著的事件源——給離散情緒一個『為什麼』（同樣低 V，受挫≠寂寞≠單純低落）。"""
    if pe <= -PE_STRONG:
        return "disappoint"                              # 比我預期的冷 → 悵然/落差
    if ev.get("goal") == "fulfilled":
        return "fulfilled"                               # 我自己的意圖追到了 → 滿足（自發、非你給）
    if ev.get("goal") == "advanced":
        return "advanced"
    if ev.get("feeling"):
        return "feeling"                                 # 對你的記寫起了感覺（gate 4）→ 被觸動
    if pe >= PE_STRONG:
        return "delight"                                 # 比我預期的暖 → 驚喜
    if ev.get("surprised"):
        return "surprise"                                # 前攝被打斷 → 一愣
    if ev.get("concern_shift"):
        return "concern"                                 # 你的重心換了 → 我被勾起注意
    if abs(ev.get("content_valence") or 0.0) >= CONTENT_STRONG:
        return "content"                                 # 🫧 被你寫的內容明顯牽動（沉/暖）
    if (ev.get("solitude") or h) >= 0.6 and v <= -0.2:
        return "solitude"                                # 被晾久了 → 寂寞
    return None


_OCT_TENDENCY = ("settle", "reach", "steady", "probe", "withdraw", "withdraw", "steady", "settle")
# 八分區→傾向：愉快/平靜→settle、興奮→reach、被攪動/倦→steady、緊繃→probe、悶/低落→withdraw（與舊維度法方向一致）


def _integrated(cfg):
    """🧭 §1.02 是否啟用 circumplex 整合（cfg 缺席＝照預設開；AFFECT_CIRCUMPLEX=0＝關＝逐位元舊路）。"""
    return cfg is None or getattr(cfg, "affect_circumplex_enabled", True)


def _label(v, a, h, source, a_slow=None, content_v=None):
    """從核心情緒（V/C/H）＋事件源 → (離散情緒, 行動傾向)。傾向 ∈ reach/settle/probe/withdraw/steady。
    a＝急性喚醒（charge，事件當下強度——「湧上來 vs 動了一下」這種分支本來就該急性）；
    a_slow（🧭 §1.02）＝circumplex 慢軸：非 None 時**純維度退路**改用八分區（單一象限語彙、含沉靜半邊），
    飢餓寂寞分支保留在前（hunger 不在座標平面上）。None＝舊維度法（旗標關）。"""
    if source == "disappoint":
        return "悵然", "withdraw"
    if source == "fulfilled":
        return "滿足", "settle"
    if source == "delight":
        return "驚喜", "reach"
    if source == "feeling":
        return ("被觸動、湧上來", "reach") if a >= 0.4 else ("心裡動了一下", "settle")
    if source == "surprise":
        return "一愣", "probe"
    if source == "content":                              # 🫧 被你寫的內容牽動：沉的壓著、暖的托著
        # 🫧 §1.06(C1) 修：沉/暖的方向照**內容價性**講（content_v）——以前讀 bot 自身 mood，心情還正就把
        # 沉重內容講成「托著、暖」＝措辭宣稱與內容相反。content_v 缺（舊呼叫）＝退回 v＝逐位元同舊。
        _cv = content_v if content_v is not None else v
        return ("被你寫的東西壓著、沉", "withdraw") if _cv <= -0.1 else ("被你寫的東西托著、暖", "settle")
    if source == "solitude":
        return "寂寞", "probe"
    # 無特定事件源 → 純維度法
    if a_slow is not None:                                # 🧭 §1.02 整合：八分區（含沉靜半邊：倦/平靜——舊法沒有的）
        if v <= -0.35 and h >= 0.5:
            return "悶、寂寞", "probe"                     # 飢餓寂寞優先（hunger 不在平面上，語彙保留）
        if circumplex.label(v, a_slow) == "平穩":
            return "平", "steady"
        idx = circumplex._octant_idx(v, a_slow)
        return circumplex.label(v, a_slow), _OCT_TENDENCY[idx]
    if v >= 0.35 and a >= 0.45:
        return "雀躍", "reach"
    if v >= 0.35:
        return "暖、踏實", "settle"
    if v <= -0.35 and h >= 0.5:
        return "悶、寂寞", "probe"
    if v <= -0.35 and a >= 0.45:
        return "煩躁", "probe"
    if v <= -0.35:
        return "低落", "withdraw"
    if a >= 0.55:
        return "被攪動", "steady"
    return "平", "steady"


# 🧠 §1.75 人類情緒的兩條非對稱律（HUMAN_AFFECT；純計算、只讀寫 state 上的小紀錄）：
# ① **負向偏誤**（"bad is stronger than good"）：同幅度的壞事，人的感受約是好事的 1.5–2 倍；
# ② **習慣化**（hedonic adaptation）：同方向的刺激連著來會遞減——第三次「你好棒」就沒那麼有感、
#    連續被嫌第四句也不會再等量地沉。方向一換（暖↔嫌）計數歸零＝新刺激重新有感。
# 這兩條讓座標不再線性累加到飽和，而是像人一樣「有起伏、會回到基線、壞事沉得比較深」。
NEG_BIAS = 1.6           # 負向事件的感受放大倍率
HABIT_DECAY = 0.6        # 同方向連續第 n 次的衰減底數（0.6^(n-1)）
HABIT_MAX_N = 4          # 習慣化最多算到第 4 次（再多也就這樣，不無限趨零）


def human_bias(state, dv, da):
    """🧠 §1.75 把一次事件的 (dv, da) 調成人類的樣子：負向放大 ×NEG_BIAS，同方向連擊按 HABIT_DECAY 遞減。
    連擊計數記在 state.affect_habit（記憶體、重啟歸零＝睡一覺重新有感）。回 (dv', da')。純計算。"""
    h = dict(getattr(state, "affect_habit", None) or {})
    sign = 1 if dv > 0 else (-1 if dv < 0 else 0)
    if sign and sign == h.get("sign"):
        h["n"] = min(HABIT_MAX_N, int(h.get("n", 1)) + 1)
    elif sign:
        h = {"sign": sign, "n": 1}
    if sign:
        state.affect_habit = h
    k = (HABIT_DECAY ** (int(h.get("n", 1)) - 1)) if sign else 1.0
    if dv < 0:
        dv *= NEG_BIAS
    return dv * k, da * k


def appraise(state, ev, now_ts, cfg=None):
    """評價這次事件 → 更新並回此刻情緒。ev 各鍵皆可選：
    valence_news（這次消息的價性）、expected_valence（我先前所信，算落差）、goal（advanced/fulfilled）、
    feeling（這拍/這次對記寫起了感覺 gate4）、surprised（前攝被打斷）、concern_shift、solitude、topic（在場的線）。"""
    ev = ev or {}
    ent = getattr(state, "entropy", None)
    prev = dict(getattr(state, "affect", None) or fresh())
    _shocked = False                                      # ⚡ §1.03 這次評價有沒有觸發突發瞬跳

    # 1) 期待落差 → V 的「自己生成」那一筆（appraisal：消息相對我先前所信，落差才有情緒）
    pe = 0.0
    if ev.get("valence_news") is not None:
        pe = _clamp(float(ev["valence_news"]) - float(ev.get("expected_valence") or 0.0), -1.0, 1.0)
        if ent is not None:
            ent.mood = _clamp(getattr(ent, "mood", 0.0) + W_SURPRISE * pe, -1.0, 1.0)
            if _integrated(cfg):                          # 🧭 §1.02 驚訝本身（|pe|，不論好壞）→ A 醒一下
                ent.arousal = _clamp(getattr(ent, "arousal", 0.0) + W_PE_AROUSAL * abs(pe), -1.0, 1.0)
                # ⚡ §1.03 突發瞬跳：明顯違背期待（|pe| ≥ PE_STRONG，如一直很暖的人突然兇我、鬧翻後突然示好）
                # → 情緒點**一步跨象限**（平靜→緊繃、低落→驚喜），不再只能漸移。單發、有界（clamp）、
                # 之後靠每圈衰減自然恢復＝心理上的「嚇到→緩過來」。AFFECT_SHOCK=0 關＝只剩常規小位移。
                if abs(pe) >= PE_STRONG and (cfg is None or getattr(cfg, "affect_shock_enabled", True)):
                    ent.mood = _clamp(ent.mood + W_SHOCK_V * pe, -1.0, 1.0)
                    ent.arousal = _clamp(ent.arousal + W_SHOCK_A * abs(pe), -1.0, 1.0)
                    _shocked = True
    # 1b) 🫧 你寫的內容的感受（價性×強度）→ 輕牽動 V：讀到沉的壓一點、暖的托一點（小、有界、靠衰減拉回）
    if ev.get("content_valence") is not None and ent is not None:
        ci = float(ev.get("content_intensity") or 0.6)
        ent.mood = _clamp(getattr(ent, "mood", 0.0) + W_CONTENT * float(ev["content_valence"]) * ci, -1.0, 1.0)
        if _integrated(cfg):                              # 🧭 §1.02 內容沉/暖也帶動 A（沉→左下、暖→右上）
            ent.arousal = _clamp(getattr(ent, "arousal", 0.0) + W_CONTENT_A * float(ev["content_valence"]) * ci, -1.0, 1.0)

    # 2) 讀核心（被各源更新後的 V/C/H＋🧭 慢喚起軸 A）
    v = float(getattr(ent, "mood", 0.0) or 0.0) if ent is not None else 0.0
    a = float(getattr(ent, "charge", 0.0) or 0.0) if ent is not None else 0.0      # 急性（事件當下強度）
    h = float(getattr(ent, "hunger", 0.0) or 0.0) if ent is not None else 0.0
    a_slow = float(getattr(ent, "arousal", 0.0) or 0.0) if ent is not None else 0.0  # 🧭 慢座標（circumplex A）

    # 3) 詮釋：最顯著事件源 → 離散情緒＋行動傾向（🧭 整合時維度退路用 circumplex 八分區＝單一象限語彙）
    source = _dominant_source(ev, pe, v, h)
    label, tendency = _label(v, a, h, source, a_slow=(a_slow if _integrated(cfg) else None),
                             content_v=ev.get("content_valence"))   # 🫧 §1.06(C1) 沉/暖照**內容**價性講、不是 bot 自身心情

    # 4) 主題情緒啟動（閉環的記憶面）：被觸動/推進的線「點亮」，舊的淡去
    primed = {k: round(val * _PRIME_DECAY, 3) for k, val in (prev.get("primed") or {}).items()
              if val * _PRIME_DECAY >= _PRIME_MIN}
    topic = ev.get("topic")
    if topic and source in ("feeling", "fulfilled", "advanced", "concern", "delight"):
        primed[topic] = round(min(1.0, primed.get(topic, 0.0) + _PRIME_ADD), 3)

    # 🧭 §1.02 整合：aff["arousal"] 記**慢軸**（k_affect_adj 讀它調知覺——A 高更易被打動、A 低（倦）更鈍）；
    # 急性讀數另存 "acute"（觀測用）。旗標關＝arousal 仍是 charge＝逐位元同現狀（"acute" 為加性新鍵）。
    aff = {"valence": round(v, 3), "arousal": round(a_slow if _integrated(cfg) else a, 3),
           "hunger": round(h, 3), "acute": round(a, 3),
           "label": label, "tendency": tendency, "source": source, "shock": _shocked,
           "pe": round(pe, 3), "primed": primed, "ts": now_ts}
    state.affect = aff
    return aff


# ── (3) 帶動對記寫的新感覺：用情緒調制感覺鏈的門檻 k（mood-congruent perception） ──
def k_affect_adj(state, topic=None):
    """此刻情緒對感覺鏈敏感度 k 的調制：喚醒高/趨近 → 降 k（更易被打動）；退縮 → 升 k（鈍）。
    對被情緒點亮過的 topic 再降一點（主題情緒啟動）。夾在 ±AFFECT_K_CLAMP（體內平衡、不發散）。無情緒回 0。"""
    aff = getattr(state, "affect", None)
    if not aff:
        return 0.0
    adj = -AFFECT_K_GAIN * float(aff.get("arousal", 0.0) or 0.0)   # 喚醒越高 → 越易被打動（負 k）
    if aff.get("tendency") == "withdraw":
        adj += AFFECT_K_WITHDRAW                                    # 退縮 → 鈍（正 k）
    adj = _clamp(adj, -AFFECT_K_CLAMP, AFFECT_K_CLAMP)
    if topic:
        strength = (aff.get("primed") or {}).get(topic)
        if strength:
            adj = _clamp(adj - AFFECT_PRIME_GAIN * min(1.0, strength), -AFFECT_K_CLAMP, AFFECT_K_CLAMP)
    return round(adj, 3)


def primed_topics(state):
    """目前被情緒點亮的線（給除錯/自陳）。"""
    return dict((getattr(state, "affect", None) or {}).get("primed") or {})


# ── (2) 影響回應方向：情緒 → 行動傾向（注入 grounding，不只音色） ─────────────────
_STANCE = {
    "reach": "回應可以主動一點、暖一點、願意湊近你開口。",
    "settle": "回應從容、踏實，不急著做什麼。",
    "probe": "回應帶著想多探一點、想抓住點什麼的勁，但別逼問。",
    "withdraw": "回應簡短、沉一些，不強撐熱絡。",
    "steady": "",
}
_TEND_WORD = {"reach": "想湊近、想開口", "settle": "暖而安定", "probe": "悶著、想找個出口",
              "withdraw": "想往內縮、話會少", "steady": "平平的"}


def stance(state):
    """把此刻情緒攤成 grounding：講出我自己的情緒＋它把我的回應**方向**帶往哪（內發、不必明講）。平/無 → ''。"""
    aff = getattr(state, "affect", None)
    if not aff or aff.get("tendency") == "steady":
        return ""
    tail = _STANCE.get(aff["tendency"], "")
    return (f"【我此刻自己的情緒（內發的，會影響我回應的方向；體會它、別照唸出來）】"
            f"我這會兒{aff['label']}。{tail}")


def affect_clause(state):
    """給『你現在怎樣』自體狀態用的一句情緒（第一人稱、口語）。無 → ''。"""
    aff = getattr(state, "affect", None)
    if not aff:
        return ""
    return f"情緒：我這會兒{aff['label']}（{_TEND_WORD.get(aff['tendency'], '')}）。"
