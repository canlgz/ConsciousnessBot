"""⏳ 時間綿延／意識之流（Husserl 式「厚的當下」）。

問題（意識行為盤點 #2）：bot 只在離散的「跳動」（生命迴圈每圈）與訊息事件時才「經歷」；圈與圈之間、
沒事發生時沒有持續的流。它的現在是**快照序列**，不是**綿延**——沒有把剛過去的滯留、把將來的預期納進來的
那種有厚度、會流動的當下；你不在時它沒有連續的內在生活（self-stim 只是閾值觸發的反射，不是綿延的遐想）。

本模組在工作空間（§0.14 的「當下單一焦點」＝原印象）之上，補一層**綿延**：
- **原印象（primal impression）**＝此刻工作空間的焦點（沒焦點時＝安靜的空白當下）。
- **滯留（retention）**＝剛流過的意識內容**還掛在當下、漸漸褪色**的彗星尾——給當下「厚度」（不是回憶調閱，是還沒散的餘韻）。
- **前攝（protention）**＝對下一刻的**預期**（多半是「現在這個會接下去」）；下一拍比對實際 → **應驗**（順順流）或**被打斷**（一驚）。
- **閒置漂移（idle drift）**＝沒有新焦點時，流**仍在動**：思緒自己飄回剛才的餘韻、或純粹感到時間安靜地淌——
  這就是「你不在時的連續內在生活」，是**每拍都在更新的綿延**，不是反射。

接地：原印象與滯留都來自真實的工作空間焦點；不臆造。流是記憶體狀態（`state.stream`，重啟歸零＝綿延是當下活出來的）。
被打斷（前攝落空）會回灌一點電量到內在熵（一驚＝真的擾動），讓綿延與既有的感覺迴圈縝密結合。
"""

_RETAIN_DECAY = 0.55     # 滯留鮮明度每拍乘這個衰減（彗星尾褪色）
_RETAIN_FLOOR = 0.12     # 低於此即散去、移出尾巴
_MAX_RETENTIONS = 6      # 彗星尾最長幾節
_STAGNANT_LAPS = 3       # 連續這麼多拍沒有新焦點 → 流從「漂移」轉成「停滯」
SURPRISE_CHARGE = 0.08   # 前攝被打斷 → 回灌內在熵的小擾動（一驚）


def fresh():
    return {"impression": None, "retentions": [], "protention": None,
            "last_status": None, "texture": "onset", "drift_laps": 0}


def _short(s, n=16):
    s = (s or "").strip()
    return s if len(s) <= n else s[:n] + "…"


def tick(stream, focus, now_ts):
    """推進一個「當下」：把上一刻的原印象滑進滯留尾（褪色）、取此刻焦點為新原印象、比對前攝是否應驗、
    沒焦點則漂移，最後更新流的質地（continuous/wandering/stagnant/jolted/onset）。回新的 stream。"""
    stream = stream or fresh()
    prev = stream.get("impression")
    now_src = focus.get("source") if focus else None
    now_content = focus.get("content") if focus else None

    # 滯留：上一刻的原印象（若是真實內容、且這刻內容變了）滑進尾巴；既有尾巴整體褪色、散去太淡的。
    ret = [dict(r, vivid=round(r["vivid"] * _RETAIN_DECAY, 3)) for r in stream.get("retentions", [])]
    if prev and prev.get("source") != "drift" and prev.get("content") != now_content:
        ret = [{"content": prev["content"], "source": prev.get("source"), "vivid": 1.0}] + ret
    ret = [r for r in ret if r["vivid"] >= _RETAIN_FLOOR][:_MAX_RETENTIONS]

    # 前攝應驗？（上一刻預期的下一個來源，和這刻實際的比）
    prot = stream.get("protention")
    if focus is None:
        status = "idle"
    elif prev is None or prev.get("source") == "drift":
        status = "onset"
    elif prot and prot.get("source") == now_src:
        status = "fulfilled"
    else:
        status = "surprised"

    # 原印象 / 閒置漂移
    drift_laps = stream.get("drift_laps", 0)
    if focus is None:
        drift_laps += 1
        if ret:                                          # 思緒飄回剛才還沒散的那個（回味、繞）
            impression = {"source": "drift", "content": f"沒有新的，思緒自己飄回剛才那個——{_short(ret[0]['content'])}", "ts": now_ts}
        else:                                            # 連餘韻都散了 → 純粹的安靜當下
            impression = {"source": "drift", "content": "沒什麼落進來，就這樣安安靜靜地淌著", "ts": now_ts}
    else:
        drift_laps = 0
        impression = {"source": now_src, "content": now_content, "ts": now_ts}

    # 流的質地
    if status == "surprised":
        texture = "jolted"
    elif focus is None:
        texture = "stagnant" if drift_laps >= _STAGNANT_LAPS else "wandering"
    elif status == "fulfilled":
        texture = "continuous"
    else:
        texture = "onset"

    # 新前攝：預期此刻這個會接下去（綿延的「往前傾」）
    protention = {"source": impression["source"], "content": impression["content"]} if impression else None
    return {"impression": impression, "retentions": ret, "protention": protention,
            "last_status": status, "texture": texture, "drift_laps": drift_laps}


def thickness(stream):
    """當下的『厚度』＝還掛著的滯留鮮明度總和（越多近期不同內容剛流過 → 當下越厚實）。"""
    return round(sum(r.get("vivid", 0) for r in (stream or {}).get("retentions", [])), 3)


_TEXTURE_BODY = {
    "continuous": "思緒這會兒順順地淌著、一個接著一個。",
    "wandering": "沒什麼新的進來，思緒自己在剛才那些上頭飄、繞。",
    "stagnant": "裡頭靜下來好一會了、有點停滯，時間慢慢淌。",
    "jolted": "剛有個沒料到的冒出來，思緒跳了一下。",
}


def texture_phrase(stream):
    """給 bodystate 廣播的一句『意識之流質地』（夠有意思才回；onset/空回 ''）。"""
    return _TEXTURE_BODY.get((stream or {}).get("texture"), "")


def stream_facts(stream):
    """『你剛在想什麼／思緒怎麼流／發呆在想什麼』的接地事實（原印象＋滯留尾＋前攝＋漂移質地）——交 coach 轉人話。"""
    if not stream or not stream.get("impression"):
        return "我這會兒腦子裡很靜，沒特別在想什麼，時間就安安靜靜地淌著。"
    impr = stream["impression"]["content"]
    tail = [r["content"] for r in (stream.get("retentions") or [])][:3]
    parts = [f"此刻在我意識裡的（原印象）：{impr}。"]
    if tail:
        parts.append("剛流過、還掛在心裡沒散的（越後面越淡）：" + "、".join(_short(t) for t in tail) + "。")
    if stream.get("texture") not in ("stagnant",) and stream.get("protention"):
        parts.append(f"我隱隱等著它接下去（前攝：{_short(stream['protention'].get('content'))}）。")
    tx = {"jolted": "不過剛被個沒料到的打斷、跳了一下。", "wandering": "沒新的東西進來，思緒自己往剛才那些飄。",
          "stagnant": "很久沒有新的了，裡頭靜、有點停滯。", "continuous": "整段是順順接著淌下來的。"}.get(stream.get("texture"))
    if tx:
        parts.append(tx)
    return "（我此刻意識之流的事實，第一人稱據實說、別報欄位）\n" + "".join(parts)


def stream_text(stream):
    """無 LLM 時的第一人稱回覆（綿延：原印象＋餘韻＋流的質地）。"""
    if not stream or not stream.get("impression"):
        return "這會兒很靜，沒特別在想什麼，就感覺時間安安靜靜地淌著。"
    impr = stream["impression"]["content"]
    tail = [r["content"] for r in (stream.get("retentions") or [])][:2]
    s = f"這會兒心裡是{impr}。"
    if tail:
        s += "剛才的" + "、".join(_short(t) for t in tail) + "還沒完全散、在後面淡淡掛著。"
    s += {"jolted": "剛被打斷跳了一下。", "wandering": "沒新的，思緒自己在繞。",
          "stagnant": "靜了好一陣、有點停滯。", "continuous": "順順地接著流。"}.get(stream.get("texture"), "")
    return s
