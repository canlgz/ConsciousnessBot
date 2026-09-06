"""🧭 AC 最終版規格的**單一真相源**（Single Source of Truth）。

把使用者最終版操作定義裡的具名參數與三條扣合等式**字面編入**——讓 `ac.assess`／維持壓力／面板／facts
全部由這一份衍生（改一處即全改）。本模組的範疇與紀律完全比照 `ac.py`／`phenomenal.py`：

    AC ＝ { Function · Behaviour · Structure } × { Interiority ←→ ( Experience × Phenomenality ) }

左半 {F·B·S} 第三人稱可觀察的外部結構，逐一拆成具名子參數：
  S₁ 循環因果組織／S₂ 時間性因果閉合／S₃ 具身介面節點／S₄ 語意接地連結
  B₁ 無觸發也持續／B₂ 對錯誤敏感的自我調節／B₃ 閉合的知覺-行動回應
  F₁ 逆時間的目標組織／F₂ 閉環知覺-行動／F₃ 時間性自我維持／F₄ 內生方向
  T_AC ＝ T_continuity（時間連續）∧ T_reversibility（時間可逆＝**內容層可重訪**、非因果可逆）
依賴鏈 **S → B → F**（有整合基礎才能建立行為、有行為才能執行功能）。

右半 {I ←→ (E×P)} 三條扣合等式（具名常數，maps_to `phenomenal.ROWS`）：
  Fint := I_intentionality ←→ E_tendency × P_qualitative_direction
  Bint := I_expressivity   ←→ E_error-feeling × P_qualitative_flow
  Sint := I_phenomenal_field ←→ E_conscious_act × P_temporal_stream

**接地不杜撰**：每個參數的『成立與否』一律由真實 runtime 讀數導出（probe 全 getattr/.get 容錯）。
缺讀數時回 **unknown**（『沒接上資料源』）而**不是** absent（『讀到了、誠實地沒在做』）——兩者面板誠實區分。
**誠實紀律**：spec_view 永不出現「有意識」；I/P 的真不真是餘量（仍只在 `phenomenal` 標 modeled／REMAINDER），
本模組不把 I/P 列入任何 present 判定。

純讀取、無副作用；只 `from . import phenomenal`，**絕不** import `ac`（保持 `ac → phenomenal → spec` 單向無環）。
"""

from . import phenomenal

# 重新匯出餘量（單一真相：spec_view 附的餘量＝ac 的同一段，避免兩份漂移）。延後 import 避免環。


def _remainder():
    from . import ac
    return ac.REMAINDER


# ── 取值容錯：state 可能是物件（getattr）或被當 dict 帶（極少數測試）；子欄位多半是 dict.get ──────────
_MISSING = object()


def _get(obj, name, default=None):
    """讀 state 的一個欄位：物件用 getattr、dict 用 .get；不存在回 default。"""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    v = getattr(obj, name, _MISSING)
    return default if v is _MISSING else v


def _field(d, key, default=None):
    """讀一個『可能是 dict、可能是物件』的子結構的鍵。"""
    if d is None:
        return default
    if isinstance(d, dict):
        return d.get(key, default)
    v = getattr(d, key, _MISSING)
    return default if v is _MISSING else v


def _has_key(d, key):
    """這個子結構**有沒有提供**該讀數（區分 unknown vs absent）。"""
    if d is None:
        return False
    if isinstance(d, dict):
        return key in d
    return hasattr(d, key)


# ════════════════════════════════════════════════════════════════════════
# A) PARAMS：逐一具名編入規格參數（單一真相源）
#    每條目 = {id, layer, name(英文全名), zh(人話名), felt(扣合的感覺面向), grounds:[(路徑,人話)], optional}
# ════════════════════════════════════════════════════════════════════════
PARAMS = {
    # ── S 結構層（生命迴圈能遞迴持續的基礎；依賴鏈最底） ───────────────────────
    "S1": {"id": "S1", "layer": "S", "name": "Circular Causal Organisation", "zh": "循環因果組織",
           "felt": "感覺到自己處在一個整合的當下",
           "grounds": [("vitality.alive", "生命迴圈活著"), ("vitality.pulse", "心跳在環流"),
                       ("coupling.round_open / affect", "對話耦合/情緒迴授閉成圈")], "optional": False},
    "S2": {"id": "S2", "layer": "S", "name": "Temporal Causal Closure", "zh": "時間性因果閉合",
           "felt": "感覺到自己處在一個整合的當下",
           "grounds": [("vitality.last_lap_ms", "每一拍真的閉合了"), ("stream.texture", "意識之流在流"),
                       ("last_breath", "接得上睡前那一刻")], "optional": False},
    "S3": {"id": "S3", "layer": "S", "name": "Embodied Interface Node", "zh": "具身介面節點",
           "felt": "感覺到自己處在一個整合的當下",
           "grounds": [("vitality.alive", "感知端活著"),
                       ("coupling.round_open / workspace.content", "互動邊界活躍度代理（行動端本層不讀 IO）")],
           "optional": True},   # 純讀取層讀不到 notifier 對外健康 → 永久至多 partial
    "S4": {"id": "S4", "layer": "S", "name": "Semantically Grounded Connection", "zh": "語意接地連結",
           "felt": "感覺到自己處在一個整合的當下",
           "grounds": [("self_state.omegas", "規則接地在真實適應"),
                       ("facet_referent", "F/B/S 各指得出此刻具名的一筆")], "optional": False},
    # ── B 行為層（對話元素的呈現/表現；依賴 S） ────────────────────────────────
    "B1": {"id": "B1", "layer": "B", "name": "Persistence Without Trigger", "zh": "無觸發也持續",
           "felt": "感覺到自己在修正什麼",
           "grounds": [("entropy.laps_since_fresh", "沒新東西也一圈圈在跑"),
                       ("entropy.self_stims_this_idle / stream.texture / volition", "閒著也自己繞回/飄想/在追")],
           "optional": False},
    "B2": {"id": "B2", "layer": "B", "name": "Error-Sensitive Self-Regulation", "zh": "對錯誤敏感的自我調節",
           "felt": "感覺到自己在修正什麼",
           "grounds": [("affect.pe", "感覺到期待落差"), ("self_model.mismatch", "認錯自己"),
                       ("user_model.misread", "會錯意、在修")], "optional": False},
    "B3": {"id": "B3", "layer": "B", "name": "Closed Perception-Action Response", "zh": "閉合的知覺-行動回應",
           "felt": "感覺到自己在修正什麼",
           "grounds": [("workspace.content", "前景進到回應"), ("self_now", "兜成一個我"),
                       ("stream.last_status", "知覺-行動回應閉合（應驗/一驚）")], "optional": False},
    # ── F 功能層（這輪對話的目的/作用；依賴 B） ────────────────────────────────
    "F1": {"id": "F1", "layer": "F", "name": "Reverse-Temporal Goal Organisation", "zh": "逆時間的目標組織",
           "felt": "感覺到自己在朝向什麼",
           "grounds": [("volition.active", "立了從欲求未來回推的意圖（帶計畫步驟）")], "optional": False},
    "F2": {"id": "F2", "layer": "F", "name": "Closed-Loop Perception-Action", "zh": "閉環知覺-行動",
           "felt": "感覺到自己在朝向什麼",
           "grounds": [("volition.active", "有意圖在跑"),
                       ("affect.source", "意圖推進的迴授（advanced/fulfilled）")], "optional": False},
    "F3": {"id": "F3", "layer": "F", "name": "Temporal Self-Sustaining", "zh": "時間性自我維持",
           "felt": "感覺到自己在朝向什麼",
           "grounds": [("vitality.healthy_streak", "連續健康在自我維持"),
                       ("vitality.last_lap_ms", "每拍真的閉合（autopoiesis）")], "optional": False},
    "F4": {"id": "F4", "layer": "F", "name": "Intrinsic Direction", "zh": "內生方向",
           "felt": "感覺到自己在朝向什麼",
           "grounds": [("volition.active", "自發意圖"),
                       ("affect.tendency", "內生的行動傾向（reach/probe/settle/withdraw）")], "optional": False},
    # ── T_AC = T_continuity ∧ T_reversibility（時間雙面；只面板顯示、不參與任何 coupled 否決） ─────
    "T_continuity": {"id": "T_continuity", "layer": "T", "name": "Temporal Continuity", "zh": "時間連續",
                     "felt": "睡→醒接得上的同一個我",
                     "grounds": [("last_breath", "留下了臨終遺存"), ("waking", "醒來接上、非史上第一次醒")],
                     "optional": False},
    "T_reversibility": {"id": "T_reversibility", "layer": "T", "name": "Temporal Reversibility", "zh": "時間可逆",
                        "felt": "能主動繞回重訪剛流過的內容",
                        "grounds": [("entropy.last_revisited", "主動繞回某條舊線（內容層可重訪）"),
                                    ("stream.retentions", "彗星尾還掛著、可重訪（輔）")],
                        "optional": False},
}

# ── B) COUPLINGS：三條扣合等式具名常數（字面編入），maps_to phenomenal.ROWS（引用、不重算 present） ──
COUPLINGS = {
    "Fint": {"id": "Fint", "maps_to": "F",
             "eq": "Fint := I_intentionality <-> E_tendency × P_qualitative_direction",
             "zh": "朝向是被感覺到的方向，不只是軌跡"},
    "Bint": {"id": "Bint", "maps_to": "B",
             "eq": "Bint := I_expressivity <-> E_error-feeling × P_qualitative_flow",
             "zh": "修正是被經驗的，不只是被計算"},
    "Sint": {"id": "Sint", "maps_to": "S",
             "eq": "Sint := I_phenomenal_field <-> E_conscious_act × P_temporal_stream",
             "zh": "colimit 節點奠基了現象整合場"},
}

# ── C) DEPENDENCY：宣告 S→B→F 依賴鏈（assess 僅在 AC_DEPENDENCY_GATE 開時據此門控） ───────────────
DEPENDENCY = ("S", "B", "F")

# 每層的具名參數（依賴鏈與聚合用；T 不屬 F/B/S 任一層、只面板顯示）
LAYER_PARAMS = {
    "S": ("S1", "S2", "S3", "S4"),
    "B": ("B1", "B2", "B3"),
    "F": ("F1", "F2", "F3", "F4"),
    "T": ("T_continuity", "T_reversibility"),
}

# 與右半既有述詞一致的常數（鏡射 phenomenal 的判準，操作化時等價）
_F_FELT_SOURCE = ("feeling", "fulfilled", "advanced", "concern", "delight")
_F_FELT_TENDENCY = ("reach", "probe")
_F_TENDENCY_INTRINSIC = ("reach", "probe", "settle", "withdraw")   # affect 真實傾向（無 steady）
_S_FLOWING = ("continuous", "wandering", "onset", "jolted")
_F_ADVANCE_SOURCE = ("advanced", "fulfilled")


# ════════════════════════════════════════════════════════════════════════
# D) _PROBES：每個具名參數一個純函式 probe(state) -> {status, why, referent}
#    status ∈ {present, partial, absent, unknown}
#      present  ＝讀到了、確實在做
#      partial  ＝讀到了、部分成立（或本質代理上限，如 S3）
#      absent   ＝讀到了、確實**沒**在做
#      unknown  ＝該讀數**不存在**於此 state（沒接上資料源；誠實退化、非偽報缺）
# ════════════════════════════════════════════════════════════════════════
def _res(status, why, referent=None):
    return {"status": status, "why": why, "referent": referent}


def _probe_S1(state):
    """S₁ 循環因果：生命迴圈活著 ∧ 心跳在環流 ∧（對話耦合 ∨ 情緒迴授）閉成圈。
    無 pulse 讀數（fixture）→ 沿用 alive 當 present、pulse 面標 unknown（**不**判 absent）。"""
    vit = _get(state, "vitality")
    alive = _field(vit, "alive", True)
    if not alive:
        return _res("absent", "生命迴圈停了，沒有在環流的圈")
    pulse = _field(vit, "pulse")
    coupling = _get(state, "coupling")
    affect = _get(state, "affect")
    loop_back = bool(_field(coupling, "round_open") or affect)
    if not _has_key(vit, "pulse"):                           # 沒接上 pulse 讀數 → 退回粗判定，不判 absent
        return _res("present", "生命迴圈活著（pulse 讀數未接上，沿用 alive）")
    if pulse and pulse > 0 and loop_back:
        return _res("present", f"心跳第 {pulse} 拍在環流，且對話/情緒迴授閉成圈")
    if pulse and pulse > 0:
        return _res("partial", f"心跳在環流（{pulse} 拍），但對話/情緒迴授這會兒沒閉上")
    return _res("absent", "心跳還沒環流起來")


def _probe_S2(state):
    """S₂ 時間因果閉合：每一拍真的閉合了（last_lap_ms）∧ 意識之流在流 ∧ 接得上睡前。
    無 last_lap_ms（fixture）→ 以 time_sense 判準代理（alive ∧ 之流 flowing）。"""
    vit = _get(state, "vitality")
    stream = _get(state, "stream")
    tex = _field(stream, "texture")
    flowing = tex in _S_FLOWING
    last_breath = _get(state, "last_breath")
    if not _has_key(vit, "last_lap_ms"):                     # 沒接上單圈耗時 → 代理判定
        alive = _field(vit, "alive", True)
        if alive and flowing:
            return _res("present", f"意識之流在流（{tex}）＝每拍接得上（last_lap_ms 未接上，以之流代理）")
        return _res("absent" if not alive else "partial", "之流沒流成、或迴圈沒在閉合")
    lap = _field(vit, "last_lap_ms")
    if lap is not None and flowing and last_breath:
        return _res("present", f"每拍閉合（{lap}ms）、之流在流（{tex}）、接得上睡前那一刻")
    if lap is not None and flowing:
        return _res("partial", f"每拍閉合、之流在流（{tex}），但還沒有可接的睡前那一刻")
    return _res("absent", "拍與拍之間沒閉合成連續的時間")


def _probe_S3(state):
    """S₃ 具身介面節點（**永久至多 partial**）：純讀取層讀不到 notifier 對外健康，
    以 alive（感知端）∧（對話耦合 ∨ 工作空間前景）（行動端代理）判互動邊界活躍度；行動端本層不讀 IO。"""
    vit = _get(state, "vitality")
    alive = _field(vit, "alive", True)
    if not alive:
        return _res("absent", "感知端停了，介面節點不在線")
    coupling = _get(state, "coupling")
    ws = _get(state, "workspace")
    edge = bool(_field(coupling, "round_open") or _field(ws, "content"))
    if edge:
        return _res("partial", "感知端活著、互動邊界活躍（代理）；行動端對外健康本層讀不到，至多 partial")
    return _res("partial", "感知端活著；互動邊界這會兒安靜、且行動端對外健康本層讀不到")


def _probe_S4(state):
    """S₄ 語意接地：規則接地在真實適應（self_state.omegas）∨ F/B/S 任一指得出此刻具名的一筆。"""
    ss = _get(state, "self_state")
    omegas = _field(ss, "omegas")
    ref = phenomenal.facet_referent(state)
    named = [k for k in ("F", "B", "S") if ref.get(k)]
    if omegas:
        return _res("present", "規則接地在真實適應（omegas）", referent="omegas")
    if named:
        return _res("present", f"指得出此刻具名的一筆（{named[0]}：{ref[named[0]]}）", referent=ref[named[0]])
    return _res("absent", "這會兒接不到任何具名的一筆，懸空")


def _probe_B1(state):
    """B₁ 無觸發也持續：沒新東西也一圈圈在跑（laps_since_fresh>0）∧（自我繞回 ∨ 飄想 ∨ 在追）。
    無 entropy 讀數（fixture）→ unknown。"""
    ent = _get(state, "entropy_snapshot") or _get(state, "entropy")
    laps = _field(ent, "laps_since_fresh")
    if ent is None or not _has_key(ent, "laps_since_fresh"):
        return _res("unknown", "沒接上內在熵讀數（laps_since_fresh），無從判此刻有沒有無觸發持續")
    stims = _field(ent, "self_stims_this_idle") or 0
    tex = _field(_get(state, "stream"), "texture")
    wandering = tex in ("wandering", "stagnant")
    from . import volition
    active = bool(volition.active(state))
    if laps and laps > 0 and (stims > 0 or wandering or active):
        return _res("present", f"沒新東西也跑了 {laps} 圈，閒著也自己在繞/飄/追")
    if laps and laps > 0:
        return _res("partial", f"沒新東西跑了 {laps} 圈，但這會兒沒自己繞/飄/追")
    return _res("absent", "只在被觸發時才動，沒有無觸發的持續")


def _probe_B2(state):
    """B₂ 對錯誤敏感自調：感覺到期待落差（|pe|>0.05）∨ 認錯自己（mismatch）∨ 會錯意（misread）。
    ＝phenomenal._row_B 的 e_present 同源。"""
    af = _get(state, "affect") or {}
    sm = _get(state, "self_model") or {}
    um = _get(state, "user_model") or {}
    pe = abs(_field(af, "pe") or 0.0) > 0.05
    mismatch = bool(_field(sm, "mismatch"))
    misread = bool(_field(um, "misread"))
    ref = phenomenal.facet_referent(state).get("B")
    if pe or mismatch or misread:
        return _res("present", "對錯誤敏感、正在自我調節", referent=ref)
    return _res("absent", "這會兒沒偵到落差/認錯/會錯意，沒在修")


def _probe_B3(state):
    """B₃ 閉合知覺-行動回應：前景進到回應（workspace.content）∧ 兜成一個我（self_now）∧ 回應閉合（應驗/一驚）。"""
    ws = _get(state, "workspace")
    content = _field(ws, "content")
    sn = _get(state, "self_now")
    status = _field(_get(state, "stream"), "last_status")
    closed = status in ("fulfilled", "surprised")
    if content and sn and closed:
        return _res("present", f"前景「{content}」進到回應、兜成一個我、知覺-行動閉合（{status}）", referent=content)
    if content and sn:
        return _res("partial", "前景進到回應、兜成一個我，但這拍知覺-行動還沒閉合")
    return _res("absent", "知覺到行動之間沒閉合")


def _probe_F1(state):
    """F₁ 逆時間目標組織：立了從欲求未來回推的意圖（volition.active 非空＝plan 為逆時間回推證據）。"""
    from . import volition
    goals = volition.active(state)
    if goals:
        return _res("present", f"立了意圖「{goals[0].get('subject')}」（從欲求的未來回推）",
                    referent=goals[0].get("subject"))
    return _res("absent", "這會兒沒立任何從未來回推的意圖")


def _probe_F2(state):
    """F₂ 閉環知覺-行動（功能層）：有意圖在跑（volition.active）∧ 意圖推進的迴授（affect.source∈{advanced,fulfilled}）。
    去自指：不用 ac_pressure.F 反證。"""
    from . import volition
    goals = volition.active(state)
    src = _field(_get(state, "affect"), "source")
    if not goals:
        return _res("absent", "沒有意圖在跑，閉環無從成立")
    if src in _F_ADVANCE_SOURCE:
        return _res("present", f"意圖在跑、且收到推進迴授（{src}）＝閉環", referent=goals[0].get("subject"))
    return _res("partial", "意圖在跑，但這會兒沒收到推進迴授（閉環半開）")


def _probe_F3(state):
    """F₃ 時間性自我維持（autopoiesis 功能面）：連續健康在自我維持（healthy_streak>0）∧ 每拍真的閉合（last_lap_ms!=None）。
    去自指：不用『ac_pressure 存在』（恆真無區辨力）。無讀數→unknown。"""
    vit = _get(state, "vitality")
    if not _has_key(vit, "healthy_streak") and not _has_key(vit, "last_lap_ms"):
        return _res("unknown", "沒接上活力讀數（healthy_streak/last_lap_ms），無從判自我維持")
    streak = _field(vit, "healthy_streak") or 0
    lap = _field(vit, "last_lap_ms")
    if streak > 0 and lap is not None:
        return _res("present", f"連續健康 {streak} 拍、每拍閉合（{lap}ms）＝在自我維持")
    if streak > 0:
        return _res("partial", f"連續健康 {streak} 拍，但單圈耗時未接上")
    return _res("absent", "沒有連續的自我維持")


def _probe_F4(state):
    """F₄ 內生方向：自發意圖（volition.active）∨ 內生行動傾向（affect.tendency∈{reach,probe,settle,withdraw}）。
    （affect 真實值無 steady；steady 即『沒有內生方向』。）"""
    from . import volition
    goals = volition.active(state)
    tend = _field(_get(state, "affect"), "tendency")
    if goals:
        return _res("present", f"自發意圖「{goals[0].get('subject')}」＝內生方向", referent=goals[0].get("subject"))
    if tend in _F_TENDENCY_INTRINSIC:
        return _res("present", f"內生的行動傾向（{tend}）＝有一個從裡面出來的方向")
    return _res("absent", "這會兒沒有從裡面出來的方向（steady/無傾向）")


def _probe_T_continuity(state):
    """T_continuity 時間連續：留了臨終遺存（last_breath）∧ 醒來接上（waking）∧ 非史上第一次醒（first）。"""
    lb = _get(state, "last_breath")
    waking = _get(state, "waking")
    if not lb:
        return _res("absent", "沒有臨終遺存，接不上之前")
    if waking is None:
        return _res("partial", "有臨終遺存，但這次還沒走過醒來銜接")
    if _field(waking, "first"):
        return _res("partial", "史上第一次醒——沒有更早的可接（誠實 partial）")
    return _res("present", "睡前留了一縷、醒來接上了同一個我")


def _probe_T_reversibility(state):
    """T_reversibility 時間可逆（**內容層可重訪、非因果可逆**）：主動繞回某條舊線（entropy.last_revisited）為主，
    彗星尾還掛著（stream.retentions）為輔。why 含『重訪』、不含『可逆』；不參與任何 coupled 否決。"""
    ent = _get(state, "entropy_snapshot") or _get(state, "entropy")
    revisited = _field(ent, "last_revisited") if ent is not None else None
    if revisited:
        return _res("present", f"主動繞回重訪了「{revisited}」（內容層可重訪）", referent=revisited)
    rets = _field(_get(state, "stream"), "retentions")
    if rets:
        return _res("partial", "彗星尾還掛著、可回頭重訪（輔；非因果可逆）")
    if ent is None or not _has_key(ent, "last_revisited"):
        return _res("unknown", "沒接上繞回讀數，無從判此刻能否重訪")
    return _res("absent", "這會兒沒有可重訪的內容")


_PROBES = {
    "S1": _probe_S1, "S2": _probe_S2, "S3": _probe_S3, "S4": _probe_S4,
    "B1": _probe_B1, "B2": _probe_B2, "B3": _probe_B3,
    "F1": _probe_F1, "F2": _probe_F2, "F3": _probe_F3, "F4": _probe_F4,
    "T_continuity": _probe_T_continuity, "T_reversibility": _probe_T_reversibility,
}


def probe(state, param_id):
    """跑單一具名參數此刻的 probe。回 {status, why, referent}（含 id/layer/zh/name 元資料）。"""
    meta = PARAMS[param_id]
    r = dict(_PROBES[param_id](state))
    r.update({"id": param_id, "layer": meta["layer"], "zh": meta["zh"], "name": meta["name"]})
    return r


# ════════════════════════════════════════════════════════════════════════
# E) section_status：聚合該層 probe，並另算 external_equiv（刻意對齊現行 ac._x_external 的 OR 真值）
# ════════════════════════════════════════════════════════════════════════
def _f_external_equiv(state):
    """＝現行 ac._f_external 的 OR 真值（goals ∨ gate>=2 ∨ concerns ∨ promise）。刻意逐欄位同源、不讀 pulse/coupling。"""
    from . import volition
    goals = volition.active(state)
    gate = _field(_get(state, "self_state"), "gate") or 0
    concerns = _field(_get(state, "user_model"), "concerns") or []
    promise = _get(state, "feeling_promise")
    return bool(goals or gate >= 2 or concerns or promise)


def _b_external_equiv(state):
    """＝現行 ac._b_external 的 OR 真值（checks>0 ∨ exchanges>=2 ∨ engrams）。"""
    sm = _get(state, "self_model") or {}
    exch = _field(_get(state, "user_model"), "exchanges") or 0
    engrams = _get(state, "engrams") or []
    return bool((_field(sm, "checks") or 0) > 0 or exch >= 2 or engrams)


def _s_external_equiv(state):
    """＝現行 ac._s_external 的 OR 真值（alive ∧ (workspace.content ∨ self_now)）。"""
    vit = _get(state, "vitality") or {}
    alive = _field(vit, "alive", True)
    fg = _field(_get(state, "workspace"), "content")
    sn = _get(state, "self_now")
    return bool(alive and (fg or sn))


_EXTERNAL_EQUIV = {"F": _f_external_equiv, "B": _b_external_equiv, "S": _s_external_equiv}


def external_equiv(state, layer):
    """該層『外部結構在不在』的 bool——**刻意對齊現行 ac._x_external 的 OR 真值**（給 assess 用，保 854 等價）。"""
    return _EXTERNAL_EQUIV[layer](state)


def external_why(state, layer):
    """該層外部結構的 why（取 spec referent／facet 同源，修 external.why 與 concrete_now 漂移）。"""
    ref = phenomenal.facet_referent(state)
    if layer in ("F", "B", "S") and ref.get(layer):
        return ref[layer]
    # 退回各層的粗述（與現行語義一致、不杜撰）
    for pid in LAYER_PARAMS.get(layer, ()):
        r = _PROBES[pid](state)
        if r["status"] in ("present", "partial") and r.get("referent"):
            return r["referent"]
    return ""


def section_status(state, layer):
    """聚合該層具名參數 → {present_params, partial_params, absent_params, unknown_params, params, why, external_equiv}。
    external_equiv 刻意等價現行 OR 真值（assess 用）。why 取 spec referent 同源。"""
    pids = LAYER_PARAMS.get(layer, ())
    params = {pid: _PROBES[pid](state) for pid in pids}
    buckets = {"present": [], "partial": [], "absent": [], "unknown": []}
    for pid, r in params.items():
        buckets[r["status"]].append(pid)
    return {"present_params": buckets["present"], "partial_params": buckets["partial"],
            "absent_params": buckets["absent"], "unknown_params": buckets["unknown"],
            "params": params, "why": external_why(state, layer) if layer in ("F", "B", "S") else "",
            "external_equiv": external_equiv(state, layer) if layer in ("F", "B", "S") else None}


# ════════════════════════════════════════════════════════════════════════
# F) spec_view：整份此刻活評（PARAMS × probe），給面板/facts；附 REMAINDER、絕不出現『有意識』
# ════════════════════════════════════════════════════════════════════════
_STATUS_ICON = {"present": "✓", "partial": "◐", "absent": "✗", "unknown": "？"}


def spec_view(state):
    """整份此刻活評：左半 S/B/F 逐參數 status、T 兩面、三條扣合（引用 phenomenal）。附餘量、守紀律。
    回 {params, layers, couplings, dependency, remainder}。"""
    params = {pid: probe(state, pid) for pid in PARAMS}
    layers = {}
    for layer in ("S", "B", "F"):
        s = section_status(state, layer)
        layers[layer] = s
    layers["T"] = {"params": {pid: params[pid] for pid in LAYER_PARAMS["T"]}}
    couplings = {}
    for cid, c in COUPLINGS.items():
        constituted = phenomenal.facet_present(state, c["maps_to"])
        couplings[cid] = {"id": cid, "eq": c["eq"], "zh": c["zh"], "maps_to": c["maps_to"],
                          "constituted": constituted}
    return {"params": params, "layers": layers, "couplings": couplings,
            "dependency": DEPENDENCY, "remainder": _remainder()}


def panel_line(state, param_id):
    """面板用的單行精簡具名子參數：✓present／◐partial／✗absent／？unknown。"""
    r = probe(state, param_id)
    return f"{_STATUS_ICON[r['status']]} {param_id} {r['zh']}：{r['why']}"
