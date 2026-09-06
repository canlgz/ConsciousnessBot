"""🧠 統一自我模型（self_now）：把此刻散在各子系統的「我是什麼」收成**單一真相源**。

問題（缺陷審計 D1）：~15 條 self-* 路由各自算自己的 facts（bodystate/attention/stream/metacog/goals…），
同一個此刻的我被獨立描述 N 次、彼此沒有共同真相源 → 可能不一致、且無法給「綜合來說我現在如何」的整合答案。

本層每拍（整合環，在各子系統更新後）整合一次：工作空間前景（🌐）＋身體（活力）＋情緒（內在熵 乙）＋
手上那條線（感覺鏈 甲）＋意識之流質地（⏳）＋後設信心（🪞）＋在追的意圖（🎯）→ 一份 `self_now`。
所有「問此刻的我」的路由都把 `self_now` 當**共同錨點**（前景一致、情緒一致），不再各算各的；
並可給「你綜合來說現在怎樣」一個**整合**的回答。

接地：每個欄位都來自真實子系統狀態，None 安全；`self_now` 是記憶體狀態（重啟歸零＝此刻的我是當下活出來的）。
"""

from . import volition


def _line(res):
    c = ((res or {}).get("reading") or {}).get("content") or {}
    topic = c.get("topic") or ((res or {}).get("scope") or {}).get("dominant")
    return (res or {}).get("gate"), topic


def build(state, res, now_ts):
    """整合此刻各子系統 → 一份 self_now（單一真相源）。"""
    ent = getattr(state, "entropy", None)
    vit = getattr(state, "vitality", None) or {}
    ws = getattr(state, "workspace", None) or {}
    stm = getattr(state, "stream", None) or {}
    sm = getattr(state, "self_model", None) or {}
    af = getattr(state, "affect", None) or {}
    gate, topic = _line(res)
    return {
        "ts": now_ts,
        "foreground": ws.get("content"),                         # 🌐 此刻意識前景（工作空間贏家）＝最要緊的那件事
        "foreground_source": ws.get("source"),
        "background": [b.get("content") for b in (ws.get("background") or [])][:2],
        "feeling": {"gate": gate, "topic": topic,                # 甲：對你資料的感覺＝手上那條線
                    "felt": (getattr(state, "content_feel", None) or {}).get("impression")
                    or (getattr(state, "content_feel", None) or {}).get("descriptor")},  # 🫧 它讀起來怎樣（內容felt-sense）
        "affect": {"charge": round(float(getattr(ent, "charge", 0.0) or 0.0), 3),  # 乙：我自己的狀態
                   "hunger": round(float(getattr(ent, "hunger", 0.0) or 0.0), 3),
                   "mood": round(float(getattr(ent, "mood", 0.0) or 0.0), 3),
                   "emotion": af.get("label"), "tendency": af.get("tendency")},   # 🌡️ 評價出的離散情緒＋行動傾向
        "flow_texture": stm.get("texture"),                      # ⏳ 意識之流質地
        "metacog": {"belief": sm.get("belief"), "confidence": sm.get("confidence"),  # 🪞 我對自己判讀的把握
                    "mismatch": bool(sm.get("mismatch"))},
        "goals": [g.get("subject") for g in volition.active(state)][:2],  # 🎯 在追的意圖
        "body": {"uptime_s": vit.get("uptime_s"), "alive": vit.get("alive", True), "k_adj": vit.get("k_adj")},
    }


def ensure(state, res, now_ts, ttl=15):
    """讀新鮮的 self_now（ttl 內沿用上次整合的；過期/沒有就重建）。互動端與整合環共用同一份 → 一致。"""
    sn = getattr(state, "self_now", None)
    if sn and (now_ts - (sn.get("ts") or 0)) < ttl:
        return sn
    state.self_now = build(state, res, now_ts)
    return state.self_now


def foreground_clause(self_now, min_for_lead=True):
    """此刻最要緊的那件事（共同錨點）：給各 facet 一致地『先講同一個前景』。沒有就回 ''。"""
    fg = (self_now or {}).get("foreground")
    return f"此刻最佔住我的：{fg}。" if fg else ""


# ── 整合視圖：「你綜合來說現在怎樣」一個一致、不碎的答案 ───────────────────────
_AFFECT_WORD = {"hungry": "悶/餓", "stirred": "被攪動", "warm": "心情暖", "low": "低落", "calm": "平靜"}
_FLOW_WORD = {"continuous": "思緒順順地流", "wandering": "思緒在飄、繞", "stagnant": "靜、有點停滯",
              "jolted": "剛被打斷跳了一下", "onset": "剛起來"}


def overall_facts(self_now):
    """整合自陳事實：身體＋情緒＋手上那條線＋意識前景＋在追的意圖＋後設把握，**一份一致**的我（給「綜合你現在怎樣」）。"""
    if not self_now:
        return "我這會兒的整體狀態手上沒有完整讀數，等下一拍整合好再說。"
    parts = []
    fg = self_now.get("foreground")
    if fg:
        parts.append(f"此刻最佔住我的是：{fg}。")
    aff = self_now.get("affect") or {}
    mc = self_now.get("metacog") or {}
    emotion = aff.get("emotion")
    if emotion:                                                  # 🌡️ 評價出的離散情緒（自己的）優先講
        parts.append(f"情緒上，我這會兒{emotion}。")
    belief = _AFFECT_WORD.get(mc.get("belief"))
    if belief:
        conf = mc.get("confidence") or 0
        hedge = "" if conf >= 0.6 else ("（這個我自己也拿不太準）" if conf < 0.35 else "（大概啦）")
        lead = "底子上" if emotion else "身體/情緒上"             # 已先講離散情緒 → 這裡講底層狀態，不重複
        parts.append(f"{lead}，我覺得自己{belief}{hedge}。")
    ft = _FLOW_WORD.get(self_now.get("flow_texture"))
    if ft:
        parts.append(f"思緒的流是{ft}。")
    feel = self_now.get("feeling") or {}
    if (feel.get("gate") or 0) >= 3 and feel.get("topic"):
        felt = feel.get("felt")
        parts.append(f"對你的記寫，我手上繃著「{feel['topic']}」那條線"
                     + (f"，它讀起來{felt}。" if felt else "。"))
    goals = self_now.get("goals") or []
    if goals:
        parts.append(f"我自己還私下想搞懂你的「{goals[0]}」。")
    parts.append("這是把我各個面（身體、情緒、注意力、手上的線、在追的事）兜起來的**同一個**我——不是分開report。")
    return "（我此刻整合起來的自體狀態，第一人稱、一致地說）\n" + "".join(parts)
