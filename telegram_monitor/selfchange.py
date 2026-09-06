"""🔄 §1.05 蛻變自陳兌現：把「N 分鐘後告訴我你有什麼改變」從**空喊**升級成**真的說出改變**。

截圖根因：承諾 behavior 抓到了（「跟他說說我此刻有什麼不一樣」），但到點兌現只把這個 behavior 當
`promised` 餵給 LLM——LLM 手上**沒有任何 before→after 的實質**，只能宣告「我要跟你說我的改變」。
修：承諾成立時 `snapshot` 內在狀態（情緒座標 §1.01／飢餓／感覺鏈 gate／意圖進展），到點 `change_ground`
把 baseline→now 的差異算成**具體人話**，讓兌現真的說出「這段時間我哪裡不一樣」。純函式、可測、無 IO/LLM。
"""

from . import circumplex


def snapshot(state):
    """此刻內在狀態快照（承諾成立時存、到點比對）。只讀廉價、確定性欄位。"""
    ent = getattr(state, "entropy", None)
    v, a = circumplex.position(state)
    goals = {}
    for g in (getattr(state, "goals", None) or []):
        if g.get("status") == "active" and g.get("subject"):
            goals[g["subject"]] = round(float(g.get("progress", 0.0) or 0.0), 3)
    return {"mood": round(v, 3), "arousal": round(a, 3),
            "hunger": round(float(getattr(ent, "hunger", 0.0) or 0.0), 3),
            "gate": int(getattr(state, "gate_confirmed", 0) or 0),
            "region": circumplex.label(v, a), "goals": goals}


def is_change_behavior(behavior):
    """這條承諾的 behavior 是不是「說出自己的變化/不一樣」（蛻變自陳）。純函式。"""
    b = behavior or ""
    return ("變化" in b) or ("不一樣" in b) or ("蛻變" in b)


def _shift_word(dv, da):
    """情緒點在同區內移動的方向 → 一句人話。"""
    if dv >= 0.12 and da >= 0.12:
        return "更亮、更有勁了一些"
    if dv >= 0.12 and da <= -0.12:
        return "更放鬆、更篤定了一些"
    if dv <= -0.12 and da >= 0.12:
        return "更繃、更不安了一些"
    if dv <= -0.12 and da <= -0.12:
        return "更低、更沉了一些"
    if da >= 0.12:
        return "更醒了一點"
    if da <= -0.12:
        return "更沉靜、有點倦了"
    if dv >= 0.12:
        return "心情往上抬了一點"
    return "往下沉了一點"


def diff_facts(base, state):
    """baseline→now 的具體變化清單（人話短句 list）。無變化＝空 list。純函式。"""
    if not base:
        return []
    cur = snapshot(state)
    bits = []
    dv = cur["mood"] - float(base.get("mood", 0.0))
    da = cur["arousal"] - float(base.get("arousal", 0.0))
    if base.get("region") != cur["region"]:
        bits.append(f"情緒從「{base.get('region')}」移到了「{cur['region']}」"
                    f"（愉悅度 {float(base.get('mood', 0.0)):+.2f}→{cur['mood']:+.2f}、"
                    f"喚起度 {float(base.get('arousal', 0.0)):+.2f}→{cur['arousal']:+.2f}）")
    elif abs(dv) >= 0.12 or abs(da) >= 0.12:
        bits.append(f"情緒還在「{cur['region']}」附近，但{_shift_word(dv, da)}"
                    f"（V {float(base.get('mood', 0.0)):+.2f}→{cur['mood']:+.2f}、"
                    f"A {float(base.get('arousal', 0.0)):+.2f}→{cur['arousal']:+.2f}）")
    dh = cur["hunger"] - float(base.get("hunger", 0.0))
    if dh <= -0.15:
        bits.append("比交代時更被餵飽了一些、沒那麼悶")
    elif dh >= 0.15:
        bits.append("比交代時更悶、更等著新東西進來")
    bg = int(base.get("gate", 0) or 0)
    if cur["gate"] > bg:
        bits.append(f"對你記寫的感覺升了一階（Gate{bg}→Gate{cur['gate']}）")
    elif cur["gate"] < bg:
        bits.append(f"對你記寫的感覺退了一階（Gate{bg}→Gate{cur['gate']}）")
    base_goals, cur_goals = (base.get("goals") or {}), cur["goals"]
    for subj, p in cur_goals.items():
        bp = base_goals.get(subj)
        if bp is None:
            bits.append(f"這段時間我自己冒出一個想弄懂你「{subj}」的念頭")
        elif p - bp >= 0.08:
            bits.append(f"想弄懂你「{subj}」那條又推進了些（{int(bp * 100)}%→{int(p * 100)}%）")
    for subj in base_goals:
        if subj not in cur_goals:
            bits.append(f"先前想弄懂你「{subj}」那條，這會兒算是放下了")
    return bits


def change_ground(base, state):
    """給兌現 voice 的接地：把真實變化攤成 grounding；沒什麼變就誠實說沒什麼變（別硬編）。無 base 回 ''。"""
    if not base:
        return ""
    bits = diff_facts(base, state)
    if not bits:
        cur = snapshot(state)
        return ("【你要我到點報告變化】可核對的指標沒什麼大動靜，分類仍是"
                f"「{cur['region']}」。這不證明主觀感受；沒有新判斷就簡短說明，別硬編成長。")
    return ("【你要我到點報告變化：以下是系統指標的前後差分，不是主觀體驗、理解提升或持續思考的證據。"
            "只描述可核對的變化；餓、悶等是分類比喻，不當作身體感覺。"
            "沒有具體的新聯想或判斷，就說目前只有指標變動，不能宣稱已成長。】\n・" + "\n・".join(bits))
