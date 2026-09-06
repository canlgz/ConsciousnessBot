"""🧠 §1.21 差分自陳（SELF_REPORT_DELTA）：治「罐頭自陳」——bot 每次被問「你現在怎樣」都全量傾倒
同一份狀態清單、逐字重複幾小時前才講過的句子（7/10 19:27 vs 7/12 17:40 截圖逐字比對＝同批句子只換主題名）。

仿 §1.05 selfchange 的三段式（snapshot→diff→注入；**只仿形、不 import、不改 selfchange**）：
- ``snapshot``：把此刻自體狀態離散成**帶位**——帶界與 selfstate.bodystate_facts / _hunger_duration_phrase
  的分支門檻**一字不差對齊**＝快照變了 ⇔ 事實層的說法真的會變（全帶位比較＝零浮點噪音）。
- ``diff_facts``：上次快照→此刻的**真變化**人話短句（只列變的；空＝沒變）。不報數字（家規）。
- ``prior_brief``：注入 facts 的差分段——附上次自陳**原話**當負面示例（別重講、別套同句型）＋
  這次真的變了什麼＋使用者這句（先回應他說的、狀態當佐證講）。{ago} 由程式算（temporal.spoken_gap）、
  LLM 只准照抄（§1.20 日期詞鐵律同款）。
- ``no_change_line``：帶位全同時的確定性無變化短句（≥4 變體、seq 取模輪替、無數字）。
純函式、無 IO/LLM、可測。旗標 SELF_REPORT_DELTA=0＝monitor 完全不呼叫＝逐位元同現狀。
"""

from . import circumplex, selfstate, temporal

PRIOR_HORIZON_MIN_DEFAULT = 2880   # 🧠 §1.21 prior 超過此齡（48h）＝視同沒說過（不注入、也不當無變化基準）
TEXT_KEEP = 200                    # 上次自陳全文只留這麼多字（state 持久化與注入皆同一截尾）


def snapshot(vitality, res, state, now_ts):
    """此刻自體狀態 → 離散帶位快照（dict、全帶位）。每個帶界都照抄 selfstate 事實層的分支門檻：
    span＝bodystate_facts 的 <3/<20/≥20 三帶；steady＝healthy_streak≥5；open＝k_adj 符號；
    churn＝C/H 三分支；hband＝_hunger_duration_phrase 的 <30/<150/<600/<1500/≥1500；
    region＝circumplex 八分區＋平穩（同 selfchange 用法）；mood_band＝±0.35 門檻。
    帶位變了 ⇔ facts 的說法真的會變；同帶內漂移＝浮點噪音＝不算變。"""
    v = vitality or {}
    up = int((v.get("uptime_s") or 0) / 60)
    span = 0 if up < 3 else (1 if up < 20 else 2)                    # 同 bodystate_facts：剛醒/一陣/好長一段
    streak = v.get("healthy_streak") or v.get("pulse") or 0
    steady = 1 if streak >= 5 else 0                                 # 同「一直很穩、沒斷線過」門檻
    adj = v.get("k_adj")
    opened = 0 if adj is None else (-1 if adj < 0 else (1 if adj > 0 else 0))   # 開放程度符號（負＝放得開）
    C, H = v.get("charge") or 0, v.get("hunger") or 0
    if C >= 0.5 and C >= H:
        churn = "churn"                                              # 剛被新東西攪動（同 bodystate_facts 分支）
    elif H >= 0.5 and H > C:
        churn = "hungry"                                             # 悶著、等不到新記寫
    else:
        churn = "settled"                                            # 沉澱
    laps = v.get("laps_since_fresh") or 0
    hband = 0 if laps < 30 else (1 if laps < 150 else (2 if laps < 600 else (3 if laps < 1500 else 4)))
    gate, topic, _ = selfstate._line_phrase(res)                     # 手上握著的那條線
    mood = v.get("mood")
    mood_band = 0 if mood is None else (1 if mood >= 0.35 else (-1 if mood <= -0.35 else 0))
    return {"span": span, "steady": steady, "open": opened, "churn": churn, "hband": hband,
            "topic": topic, "gate": int(gate or 0),
            "region": circumplex.label(*circumplex.position(state)),
            "mood_band": mood_band, "lr": v.get("last_revisited")}


def diff_facts(base, cur):
    """上次帶位快照 → 此刻的**真變化**人話短句 list（只列變的；空 list＝沒變）。
    全帶位比較（同帶內漂移不觸發）、不報數字（家規）。純函式。"""
    if not base:
        return []
    bits = []
    bt, ct = base.get("topic"), cur.get("topic")
    if bt != ct:
        if bt and ct:
            bits.append(f"翻的線從「{bt}」換到了「{ct}」那條")
        elif ct:
            bits.append(f"手上翻起了「{ct}」這條線")
        else:
            bits.append(f"上次那條「{bt}」先放下了、手上暫時沒成形的線")
    bg, cg = int(base.get("gate") or 0), int(cur.get("gate") or 0)
    if cg > bg:
        bits.append("那條線比上次更收攏了些")
    elif cg < bg:
        bits.append("那條線又鬆散回去了")
    bh, ch = int(base.get("hband") or 0), int(cur.get("hband") or 0)
    if ch > bh:
        bits.append("比上次又餓得更久了、更悶")
    elif ch < bh:
        bits.append("這段時間有新東西落進來、沒剛才那麼餓了")
    if base.get("churn") != cur.get("churn"):
        bits.append({"churn": "剛被新東西攪動、翻騰起來了",
                     "hungry": "新的記寫又斷了、開始悶著等",
                     "settled": "沉澱下來了"}.get(cur.get("churn") or "settled", "沉澱下來了"))
    if base.get("region") != cur.get("region"):
        bits.append(f"心情從「{base.get('region')}」那一帶移到了「{cur.get('region')}」")
    bm, cm = int(base.get("mood_band") or 0), int(cur.get("mood_band") or 0)
    if cm > bm:
        bits.append("心情比上次暖了一截")
    elif cm < bm:
        bits.append("心情比上次沉了一截")
    bo, co = int(base.get("open") or 0), int(cur.get("open") or 0)
    if co != bo:
        bits.append("比上次更放得開了些" if co < bo else "又比上次收著一點了")
    if int(base.get("steady") or 0) != int(cur.get("steady") or 0):
        bits.append("這會兒跳得穩了、沒再斷" if cur.get("steady") else "脈動比剛才不穩了一點")
    if int(base.get("span") or 0) != int(cur.get("span") or 0):
        bits.append("又連著醒過了好一段")
    if base.get("lr") != cur.get("lr") and cur.get("lr"):
        bits.append(f"這回自己繞回去翻的是「{cur.get('lr')}」、不是上次那條")
    return bits


def prior_brief(prior, cur_snap, now_ts, user_text, horizon_min=PRIOR_HORIZON_MIN_DEFAULT):
    """注入 facts 的差分段：〔上次原話（負面示例、別重講）〕＋這次真的變了的清單（空＝短短承認沒變、
    別硬編）＋〔他這句說的是…**先回應他這句**〕。prior=None／超 horizon → ''（視同沒說過）。
    {ago} 由程式算（temporal.spoken_gap）、LLM 只准照抄（§1.20 日期詞鐵律同款）。純函式。"""
    if not prior:
        return ""
    ts = float(prior.get("ts") or 0)
    if not ts or (now_ts - ts) > max(0, horizon_min) * 60:
        return ""
    ago = temporal.spoken_gap(max(0.0, now_ts - ts))
    prev_text = (prior.get("text") or "")[:TEXT_KEEP]
    if not prior.get("snap"):                        # 🫧 只更新過 text/ts 的 prior（無快照）＝差分算不出：
        mid = ("這次跟那時比有沒有真的變、手上沒對齊出來——就自然講此刻，"
               "但仍**別重講上面那些句子、別套同句型**。")
    else:
        bits = diff_facts(prior.get("snap"), cur_snap)
        if bits:
            mid = ("這次**真的變了**的是：\n・" + "\n・".join(bits)
                   + "\n沒變的（醒著多久、穩不穩、餓不餓那些）**別再從頭倒一次**、頂多一筆帶過。")
        else:
            mid = "這次其實**沒什麼真的變**——就短短承認跟剛剛差不多，別硬編變化（比照你守約時的誠實）。"
    anchor = (f"\n〔他這句說的是「{(user_text or '')[:60]}」——**先回應他這句**，你的狀態當佐證講，"
              "別答非所問地開狀態清單。〕") if user_text else ""
    return (f"〔你{ago}才跟他說過（下面是你上次自陳的原話，**這次別重講、別套同句型**）：『{prev_text}』〕\n"
            + mid + anchor
            + "\n以上是工作狀態的分類差分，不是主觀情緒或身體感覺的證明。"
              "餓、悶、暖等是舊分類名稱，不可据此編造感受或因果。"
              "優先說正在比較哪些具體內容、判斷有何改變、還缺什麼證據。"
              "沒有新判斷就簡短回答，不把時間經過本身包裝成成長。")


def no_change_line(prior, res, seq):
    """帶位全同時的確定性無變化短句（無 LLM 退路）：≥4 變體、seq 取模輪替、不帶數字、短。
    有 topic 就點名那條線；沒有就用泛形。prior 保留在簽名裡（與 render_bodystate_nochange 同形）。純函式。"""
    _, topic, _ = selfstate._line_phrase(res)
    if topic:
        pool = (f"跟剛剛差不多，還是繞在「{topic}」那條上，沒什麼新的動靜。",
                f"還是那樣——「{topic}」那條還在手上轉，內裡沒起什麼波瀾。",
                f"沒什麼變，我還在「{topic}」一帶慢慢翻著，跟你上回問時差不多。",
                f"大致跟剛才一樣，手上仍是「{topic}」這條，裡頭挺安靜的。")
    else:
        pool = ("跟剛剛差不多，內裡沒什麼新的動靜。",
                "還是那樣，沒起什麼波瀾，裡頭挺安靜的。",
                "沒什麼變，跟你上回問時差不多。",
                "大致跟剛才一樣，裡頭靜靜的、沒什麼新念頭。")
    return pool[int(seq) % len(pool)]
