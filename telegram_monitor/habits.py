"""📈 §1.42 使用者習慣模型（USER_HABIT_GROUND）：把使用者的**對話作息**記成可統計的事件，讓 bot 講他的習慣時
有真資料可照——不再憑印象亂掰（截圖：「你通常會在早上十點左右跟我說早安」「有時八點多有時十點多」全是編的，
因為全 repo 只有記寫節奏接地、對話習慣零資料）。

事件（state.habit_events，跨重生持久化、FIFO 上限 _CAP）：
  - "msg"      每則使用者文字訊息
  - "first"    一段安靜（≥ _FIRST_GAP_S）後的第一句＝「今天/這段的第一次出現」
  - "contact"  📈 §1.63 非文字接觸（純貼圖/照片/語音…）——它們一樣是「出現」，不入帳會把早晨抹掉（見下）
  - "greet_am"/"greet_noon"/"greet_pm"  時間性問候（greeting.detect 分類）
  - "greet_pm_hello"/"greet_pm_bye"      晚間到場問候／睡前收尾（避免把晚上好和晚安混成同一作息）

統計（stats）＝純函式：近 _WINDOW_DAYS 天內某類事件的「當天第幾分鐘」分佈 →（樣本數, 中位, p25, p75）；
樣本 < _MIN_N＝None（誠實說不準，絕不硬給）。所有輸出時間都是**程式算的本地時刻**（LLM 只准照抄＝§1.20 鐵律）。
純函式、不碰 LLM/IO；捕捉由 monitor 依旗標呼叫（旗標關＝不捕捉不注入不守門＝逐位元同現狀）。

📈 §1.63 習慣觀測修真（HABIT_OBS_FIX；截圖「09:54–20:58、中位 12:01」被使用者打臉「我常 6–7 點說早安」）——
§1.42 的三個觀測洞：
  ① "first"（≥4h 安靜後的第一句）一天可記多筆（中午/晚上再現身都算）＝分佈涵蓋全天，卻掛「一天第一句」的標籤
     → daily_first_stats：以**本地日曆日**分組、取每日最早出現事件＝真「一天第一句」。
  ② 純貼圖/照片/語音會更新 last_user_msg_ts（重置 ① 的安靜計時）卻**不留事件**＝清晨的貼圖早安自己隱形、
     還害後續文字 gap<4h 不算 first（早晨被雙重抹掉）→ note_contact 入帳 "contact" 事件。
  ③ 「我常跟你說早安喔」（20:23 講的）被 greeting.detect 判成問候→記成一筆晚上的 greet_am 汙染統計
     → greeting.is_mention：頻率/時態副詞＋說/道/講＋問候詞＝在**談**問候、不是在問候，不記。
"""

import re
from datetime import datetime, timezone

from . import analyzer, greeting, temporal

_CAP = 400                 # habit_events FIFO 上限（約可覆蓋數月的日常對話量）
_FIRST_GAP_S = 4 * 3600    # 隔多久的安靜後，這句算「這段的第一句」（一天多段也各記一次、取樣自然偏向早上第一次）
_WINDOW_DAYS = 45          # 統計窗：只看近 45 天（作息會漂移，太舊的別拿來說「你平常」）
_MIN_N = 3                 # 至少 3 個樣本才敢說「你平常」；不夠＝誠實說不準

_GREET_KIND = {"morning": "greet_am", "noon": "greet_noon", "night": "greet_pm"}
# 晚間的到場/離場與跨午夜是不同 speech act；現階段不做 night routine 比較，避免線性分鐘把 23:50→00:10 算反。
_GREET_ROUTINE_KIND = {"morning": "greet_am", "noon": "greet_noon"}
_KIND_GREET = {"greet_am": "morning", "greet_noon": "noon", "greet_pm": "night",
               "greet_pm_hello": "night", "greet_pm_bye": "night"}
_KIND_LABEL = {"greet_am": "跟我說早安", "greet_noon": "跟我說午安", "first": "一天跟我說上第一句話",
               "greet_pm": "跟我道晚安", "greet_pm_hello": "跟我說晚上好", "greet_pm_bye": "跟我道晚安"}
_CONTACT_KINDS = ("msg", "contact")   # 📈 §1.63 「出現」事件＝文字＋非文字接觸（daily_first 的資料源）


def note(state, text, ts, prev_ts=0, mention_guard=False):
    """把這則使用者訊息記進習慣事件（msg 必記；隔 ≥4h 安靜的第一句加記 first；問候語加記 greet_*）。
    prev_ts＝**更新前**的 last_user_msg_ts（呼叫端必須在更新它之前呼叫，gap 才算得出）。
    mention_guard（§1.63、旗標傳入；False＝同現狀）＝「我常跟你說早安喔」這種**談**問候的陳述不記 greet_*。"""
    ev = getattr(state, "habit_events", None)
    if ev is None:
        ev = state.habit_events = []
    ev.append({"k": "msg", "ts": ts})
    if not prev_ts or (ts - prev_ts) >= _FIRST_GAP_S:
        ev.append({"k": "first", "ts": ts})
    # 「我平常幾點跟你說早安？」是在**問**問候習慣、不是在問候（greeting.detect 對 ≤12 字含「早安」會誤判 morning）
    # → 別把問句記成 greet_* 事件汙染統計。§1.63 再堵陳述形：「我常跟你說早安喔」是在**談**問候習慣，
    # 20:23 講的這句被記成 greet_am 會把早安統計往晚上拉（使用者越討論、資料越髒）。
    gk = _GREET_KIND.get(greeting.detect(text) or "")
    if gk and not is_user_habit_question(text) and not (mention_guard and greeting.is_mention(text)):
        ev.append({"k": gk, "ts": ts})
        if gk == "greet_pm":
            ev.append({"k": "greet_pm_bye" if greeting.is_goodnight(text) else "greet_pm_hello", "ts": ts})
    if len(ev) > _CAP:
        del ev[: len(ev) - _CAP]


def note_contact(state, ts):
    """📈 §1.63 非文字接觸（純貼圖/照片/語音/檔案…）入帳 "contact" 事件：這些路徑本來就會更新
    last_user_msg_ts（＝重置 "first" 的 4h 安靜計時）卻不留事件——清晨用貼圖說早安的那次「出現」
    自己隱形、還害後續第一句文字 gap<4h 不算 first＝早晨被統計雙重抹掉。呼叫端依旗標呼叫。"""
    ev = getattr(state, "habit_events", None)
    if ev is None:
        ev = state.habit_events = []
    ev.append({"k": "contact", "ts": ts})
    if len(ev) > _CAP:
        del ev[: len(ev) - _CAP]


def _minutes_local(ts, tz):
    dt = datetime.fromtimestamp(ts, timezone.utc)
    if tz is not None:
        dt = dt.astimezone(tz)
    return dt.hour * 60 + dt.minute


def hhmm(minutes):
    return f"{int(minutes) // 60:02d}:{int(minutes) % 60:02d}"


def stats(events, kind, tz, now_ts, days=_WINDOW_DAYS, min_n=_MIN_N, exclude_today=False):
    """近 days 天內 kind 事件的當天分鐘分佈 →（n, 中位, p25, p75）；樣本 < min_n＝None（說不準就不給數）。"""
    cutoff = now_ts - days * 86400
    today = _local_day_min(now_ts, tz)[0] if exclude_today else None
    mins = sorted(_minutes_local(e["ts"], tz) for e in (events or [])
                  if (e.get("k") == kind and (e.get("ts") or 0) >= cutoff
                      and (today is None or _local_day_min(e["ts"], tz)[0] != today)))
    n = len(mins)
    if n < min_n:
        return None
    return (n, mins[n // 2], mins[n // 4], mins[(3 * n) // 4])


def daily_kind_stats(events, kind, tz, now_ts, days=_WINDOW_DAYS, min_n=_MIN_N, exclude_today=False):
    """問候作息用：同一類問候每個本地日只取第一筆，樣本數代表**天數**而非連發次數。"""
    cutoff = now_ts - days * 86400
    today = _local_day_min(now_ts, tz)[0] if exclude_today else None
    firsts = {}
    for e in (events or []):
        ts = e.get("ts") or 0
        if e.get("k") != kind or ts < cutoff:
            continue
        day, minute = _local_day_min(ts, tz)
        if today is not None and day == today:
            continue
        gkind = _KIND_GREET.get(kind)
        if gkind and greeting.time_match(gkind, temporal.day_part(minute // 60)) != "match":
            continue                         # 夜裡玩笑說「早安」不污染真正的早安作息
        if day not in firsts or minute < firsts[day]:
            firsts[day] = minute
    mins = sorted(firsts.values())
    n = len(mins)
    if n < min_n:
        return None
    return (n, mins[n // 2], mins[n // 4], mins[(3 * n) // 4])


def _local_day_min(ts, tz):
    """(本地日曆日, 當天第幾分鐘)。"""
    dt = datetime.fromtimestamp(ts, timezone.utc)
    if tz is not None:
        dt = dt.astimezone(tz)
    return dt.date(), dt.hour * 60 + dt.minute


def daily_first_stats(events, tz, now_ts, days=_WINDOW_DAYS, min_n=_MIN_N, exclude_today=False):
    """📈 §1.63 真「一天第一句」：以**本地日曆日**分組、取每日最早的出現事件（msg/contact）→ 當天分鐘分佈
    →（天數, 中位, p25, p75）。舊 stats(ev,"first") 的樣本是「每段 ≥4h 安靜後的第一句」＝一天可多筆＝
    p25–p75 被中午/晚上的再現身拉成涵蓋全天的區間（截圖 09:54–20:58），卻掛著「一天第一句」的標籤。
    樣本（天數）< min_n＝None（誠實說不準）。"""
    cutoff = now_ts - days * 86400
    today = _local_day_min(now_ts, tz)[0] if exclude_today else None
    firsts = {}
    for e in (events or []):
        ts = e.get("ts") or 0
        if e.get("k") not in _CONTACT_KINDS or ts < cutoff:
            continue
        day, m = _local_day_min(ts, tz)
        if today is not None and day == today:
            continue
        if day not in firsts or m < firsts[day]:
            firsts[day] = m
    mins = sorted(firsts.values())
    n = len(mins)
    if n < min_n:
        return None
    return (n, mins[n // 2], mins[n // 4], mins[(3 * n) // 4])


def today_first_minutes(events, tz, now_ts):
    """📈 §1.63 今天（**本地日曆日**）第一次出現（msg/contact）的當天分鐘；今天還沒出現過＝None。
    取代舊的「now−18h 內最後一筆 first」（凌晨聊過會把昨晚算成今天、又漏掉貼圖出現）。"""
    today, _ = _local_day_min(now_ts, tz)
    best = None
    for e in (events or []):
        ts = e.get("ts") or 0
        if e.get("k") not in _CONTACT_KINDS or ts < now_ts - 2 * 86400:   # 近兩天已足以涵蓋任何時區的「今天」
            continue
        day, m = _local_day_min(ts, tz)
        if day == today and (best is None or m < best):
            best = m
    return best


# 問「我自己的習慣/作息」的偵測（主詞必須是我；「你平常幾點」是問 bot、不收；「現在幾點」無習慣框、不收）。
_HABIT_FRAME = ("我平常", "我通常", "我平時", "我大概都", "我都", "我的習慣", "我的作息", "我習慣", "我一般")
_HABIT_CUE = ("幾點", "什麼時候", "何時", "時間", "作息", "習慣", "早安", "晚安", "起床", "睡")
_FUTURE_MARK = ("明天", "後天", "等下", "待會", "晚點", "下週", "下周", "下個月")
# 泛型第二路：句含「我」＋習慣名詞＋問句形（「你知道我記寫裡的行為與習慣？」「你有我任何作息的了解嗎？」）——
# 但「你的習慣/你的作息/你平常…」＝問 bot 自己的習慣、先排除（截圖兩句都是問使用者、原第一路的窄框接不到）。
_HABIT_NOUN = ("習慣", "作息", "行為模式", "行為與習慣", "慣性", "規律")
_HABIT_QFORM = ("嗎", "？", "?", "呢", "知道", "了解", "清楚", "分析", "看得出", "說說", "講講", "描述", "歸納")
_HABIT_BOT_SUBJ = ("你的習慣", "你的作息", "妳的習慣", "妳的作息", "你平常", "你通常", "你平時", "妳平常", "你自己的習慣")


def is_user_habit_question(text):
    """這句在問**使用者自己的習慣/作息回顧**嗎（「我平常大概幾點跟你說早安」「你知道我記寫裡的行為與習慣？」）。
    純函式。未來安排（我明天八點要開會）不是習慣回顧、不收；問 bot 自己的習慣（你的習慣/你平常…）不收。"""
    t = (text or "").replace(" ", "")
    if not t:
        return False
    if any(f in t for f in _FUTURE_MARK):
        return False
    if any(b in t for b in _HABIT_BOT_SUBJ):
        return False
    if any(f in t for f in _HABIT_FRAME) and any(c in t for c in _HABIT_CUE):
        return True
    return ("我" in t) and any(n in t for n in _HABIT_NOUN) and any(q in t for q in _HABIT_QFORM)


# 📊 §2.23 習慣**盤點**問句的線索：問的是「你（bot）**觀察/發現/了解**到我有**哪些**習慣」＝驗收 bot 對他的
# 認識總覽——答案是「觀察與感受的盤點」、不是某個單點統計，更不是某筆記錄的原文。
_INV_CUE = ("觀察", "發現", "注意", "看出", "看見", "了解", "知道", "掌握", "記到", "學到", "歸納", "分析", "哪些")


def is_habit_inventory(text):
    """📊 §2.23 這句是**習慣盤點總覽**問句嗎（「你目前觀察到我有哪些習慣呢」「你對我的作息有什麼發現」
    「你了解我的習慣嗎」）？是 is_user_habit_question 的子集：多要求盤點線索（觀察動詞或「哪些」）——
    「我平常大概幾點跟你說早安」這種**單點**回顧不算（照走原 fact_or_chat＋統計注入）。純函式。"""
    t = (text or "").replace(" ", "")
    if not is_user_habit_question(t):
        return False
    return any(c in t for c in _INV_CUE)


def _stat_line(st, label):
    if st is None:
        return f"你{label}的時間：樣本還不夠（不足 {_MIN_N} 次），**說不準**——別掰一個時間出來。"
    n, med, lo, hi = st
    return f"你{label}，多半落在 {hhmm(lo)}–{hhmm(hi)} 之間（中位 {hhmm(med)}、樣本 {n} 次）。"


def _record_hours_line(records, tz, now_ts):
    """記寫時段分佈（資料源＝data['records'] 的 ts；<3 筆＝誠實）。"""
    from . import temporal
    cutoff = now_ts - _WINDOW_DAYS * 86400
    hours = []
    for r in (records or []):
        dt = analyzer.parse_ts(r.get("ts"))
        if dt is None or dt.timestamp() < cutoff:
            continue
        hours.append((dt.astimezone(tz) if tz is not None else dt).hour)
    if len(hours) < _MIN_N:
        return f"你的記寫時段：近期樣本不夠（{len(hours)} 筆），說不準。"
    buckets = {}
    for h in hours:
        buckets.setdefault(temporal.day_part(h), []).append(h)
    top = max(buckets.items(), key=lambda kv: len(kv[1]))
    return (f"你的記寫多落在{top[0]}（{min(top[1])}–{max(top[1])} 點，佔 {len(top[1])}/{len(hours)} 筆）。")


def _topic_habit_lines(records, tz, now_ts, top=3):
    """📈 記寫的**事後習慣分析**（主題×時段）：同一 topicLabel 在窗內 ≥ _MIN_N 筆＝重複出現的行為
    → 「你常記「讀誦經書」（5 筆，多在 7–8 點）」＝從記寫內容歸納出的習慣。不足＝誠實一行。"""
    cutoff = now_ts - _WINDOW_DAYS * 86400
    by_label = {}
    for r in (records or []):
        lab = (r.get("topicLabel") or "").strip()
        dt = analyzer.parse_ts(r.get("ts"))
        if not lab or dt is None or dt.timestamp() < cutoff:
            continue
        by_label.setdefault(lab, []).append((dt.astimezone(tz) if tz is not None else dt).hour)
    habitual = sorted(((lab, hs) for lab, hs in by_label.items() if len(hs) >= _MIN_N),
                      key=lambda kv: -len(kv[1]))[:top]
    if not habitual:
        return ["・主題性的固定習慣：還看不出來（近期沒有單一主題累積到 " + str(_MIN_N) + " 筆），說不準。"]
    return [f"・你常記「{lab}」（{len(hs)} 筆，多在 {min(hs)}–{max(hs)} 點）＝重複出現的行為。"
            for lab, hs in habitual]


# ── 🌾 §1.79 習慣缺席暗示（HABIT_ABSENCE_WONDER）的純函式層 ──────────────────────────────
# 使用者需求：「如果 bot 發現平常使用者有做的事情沒做，需要主動詢問或者暗示（**不是提醒**）……
# bot 能感覺到，明明是使用者習慣做的事情、例行的事情，為什麼還沒看到使用者去做。」
#
# 這條 lane 的失敗是**不對稱**的：偽陰性（該講沒講）＝沒人發現；偽陽性（他明明做了、或只是今天晚一點做，
# bot 卻說「我這邊還沒看到」）＝直接複製 §1.63/§1.70B/§1.77 那族「語意讀反」的前科，而且會變成待辦追殺。
# 所以整層設計成 **fail-closed**：任何一個前提算不出來就沉默。
#
# 實測支撐（設計時用貼近真實的分布試算 12 天樣本：多數日子早上 7–8 點、偶爾晚上六點多）：
#   ・「中位＋固定 2 小時」當門檻 → 誤判率 17%（每 6 天亂問一次）；
#   ・改用**分位數 p75 ＋ 與他自己節奏成比例的寬限** → 觸發自然落在一天的後段。
# 結論：這機制天生只適合「一天快過完了、還是沒看到」的那種好奇，不適合早上就問——後者正是**提醒**。
_ABS_MIN_DAYS = 14          # 窗內至少幾個不同日曆日有這條線（主動打擾的舉證責任 ≈ 回答問句 _MIN_N 的 5 倍）
_ABS_MIN_COVERAGE = 0.65    # n_days / 可觀測天數：排除「一週只做三天」
_ABS_MAX_IQR_MIN = 120      # p75−p25 上限：時間太散＝沒有「平常幾點」可言
_ABS_MAX_SPREAD_MIN = 720   # max−min >12h ⇒ 跨午夜，整條放棄（不做環形統計）
_ABS_MIN_P25_MIN = 300      # p25 ≥ 05:00：夜貓/凌晨型不參選（判定會跨日界）
_ABS_MIN_RECENT = 4         # 近 7 天（不含今天）至少 4 天有做＝這條線**現在**還活著
_ABS_MAX_LAST_GAP = 2       # 最後一次 ≥ 今天−2 天（已經在衰退就別戳）
_ABS_GRACE_FLOOR, _ABS_GRACE_CAP = 90, 180   # 寬限與他自己的 iqr 成比例（抓得緊的等 90 分就夠）
_ABS_WINDOW_MIN = 300       # 每天每條線只有一段最長 5h 的可問窗；過窗永遠沉默、不補問
_ABS_CUTOFF_MIN = 1260      # 21:00 硬上限（距深夜界留緩衝）
_ABS_AWAKE_MIN = 90         # 他今天已經活動 ≥90 分鐘才算「今天真的開始了」
_ABS_STALE_H = 6            # 記寫快照最新一筆距今 ≤6h，否則「今天還沒記」可能只是資料沒同步


def abs_day_key(ts, tz):
    """🌾 §1.79 本地日曆日鍵 'YYYY-MM-DD'；**tz 為 None 一律回 None**（不退化 UTC）。
    刻意與本檔其他函式不同：那些退化 UTC 只是為了輸出一句誠實話；這裡是要**主動開口**，
    時區差 8 小時會把「今天做了沒」整個翻反 → 寧可算不出、不要算錯。"""
    if tz is None or not ts:
        return None
    return datetime.fromtimestamp(ts, timezone.utc).astimezone(tz).strftime("%Y-%m-%d")


def _abs_rec_dt(r, tz):
    dt = analyzer.parse_ts(r.get("ts"))
    if dt is None:
        return None
    if getattr(dt, "tzinfo", None) is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz)


def abs_records_health(records, tz, now_ts, max_stale_h=_ABS_STALE_H, ingest_ts=None):
    """🌾 §1.79 這份記寫快照**能不能拿來判斷「今天還沒做」**（fail-closed 的守門）。
    ok 需同時成立：① 有筆數且至少一筆 ts 解析得出（全解析失敗＝上游資料形狀不對，會讓所有習慣看起來都缺席）；
    ② 今天已經有 ≥1 筆（快照確實看得見今天；Drive 讀取有快取、不是即時）；③ 今天沒有還沒歸戶（topicLabel 空）
    的筆（那筆很可能正是這條線）；④ **快照本身**夠新。

    ④ 的來源刻意是 **ingest_ts（data.meta.lastIngestTs＝快照抓取時刻）**，不是「最新一筆記寫的時刻」——
    實測踩到的坑：他若只在早上記寫，到晚上「最新一筆」必然超過 6 小時，用它判 stale 會讓這個機制**永遠不觸發**
    （而那正是最該觸發的時候）。沒有新記寫≠資料不新。ingest_ts 缺席時退回只靠 ①②③（today_n≥1 本身就是
    「這份快照看得見今天」的證據）。"""
    out = {"total": 0, "parsed": 0, "today_n": 0, "today_unlabeled": 0, "last_ts": None,
           "ok": False, "reason": ""}
    if tz is None:
        out["reason"] = "no_tz"
        return out
    today = abs_day_key(now_ts, tz)
    recs = records or []
    out["total"] = len(recs)
    for r in recs:
        dt = _abs_rec_dt(r, tz)
        if dt is None:
            continue
        out["parsed"] += 1
        ts = dt.timestamp()
        if out["last_ts"] is None or ts > out["last_ts"]:
            out["last_ts"] = ts
        if dt.strftime("%Y-%m-%d") == today:
            out["today_n"] += 1
            if not (r.get("topicLabel") or "").strip():
                out["today_unlabeled"] += 1
    if not out["total"] or not out["parsed"]:
        out["reason"] = "unparsable"
    elif out["today_n"] < 1:
        out["reason"] = "snapshot_missing_today"
    elif out["today_unlabeled"] > 0:
        out["reason"] = "pending_filing"
    elif ingest_ts and (now_ts - ingest_ts) > max_stale_h * 3600:
        out["reason"] = "stale_snapshot"                       # 快照本身太舊（不是「沒有新記寫」）
    else:
        out["ok"] = True
    return out


def abs_topic_daily_samples(records, label, tz, now_ts, days=_WINDOW_DAYS):
    """🌾 §1.79 這條線在窗內**每個日曆日最早那筆**的當天分鐘 [(date, minute)]（今天不進樣本）。
    label 用 .strip() 後**完全相等**分組（不用子字串比對——「閱讀」與「閱讀｜讀誦經書」會互相汙染）。
    任一筆 ts 解析不出 → 回 None（fail-closed）；tz None → None。"""
    if tz is None or not label:
        return None
    today = abs_day_key(now_ts, tz)
    cutoff = now_ts - (days + 1) * 86400
    firsts = {}
    for r in (records or []):
        if (r.get("topicLabel") or "").strip() != label:
            continue
        dt = _abs_rec_dt(r, tz)
        if dt is None:
            return None
        if dt.timestamp() < cutoff:
            continue
        key = dt.strftime("%Y-%m-%d")
        if key == today:
            continue
        m = dt.hour * 60 + dt.minute
        if key not in firsts or m < firsts[key][1]:
            firsts[key] = (dt.date(), m)
    return sorted(firsts.values())


def abs_routine_profile(samples, now_ts, tz, min_days=_ABS_MIN_DAYS, min_coverage=_ABS_MIN_COVERAGE,
                        max_iqr=_ABS_MAX_IQR_MIN, max_spread=_ABS_MAX_SPREAD_MIN,
                        min_p25=_ABS_MIN_P25_MIN, min_recent=_ABS_MIN_RECENT,
                        max_last_gap=_ABS_MAX_LAST_GAP):
    """🌾 §1.79 這條線**夠不夠格**被當成「他的日課」→ profile dict；任一條不過＝None（沉默）。
    百分位沿用本檔既有整數索引法（stats 同一套）。全部 AND：
      ① 天數 ≥ min_days ② coverage ≥ 0.65 ③ iqr ≤ 2h ④ 全距 ≤ 12h（>12h＝跨午夜，放棄）
      ⑤ p25 ≥ 05:00 ⑥ 近 7 天（不含今天）≥ min_recent 天有做 ⑦ 最後一次 ≥ 今天−max_last_gap 天。"""
    if not samples or tz is None:
        return None
    mins = sorted(m for _d, m in samples)
    n = len(mins)
    if n < min_days:
        return None
    p25, p50, p75 = mins[n // 4], mins[n // 2], mins[(3 * n) // 4]
    iqr = p75 - p25
    if iqr > max_iqr or (mins[-1] - mins[0]) > max_spread or p25 < min_p25:
        return None
    days_seen = sorted(d for d, _m in samples)
    today = datetime.fromtimestamp(now_ts, timezone.utc).astimezone(tz).date()
    eligible = max(1, min(_WINDOW_DAYS, (today - days_seen[0]).days))   # 分母＝實際可觀測天數（不含今天）
    coverage = n / eligible
    if coverage < min_coverage:
        return None
    recent = sum(1 for d in days_seen if 1 <= (today - d).days <= 7)
    if recent < min_recent or (today - days_seen[-1]).days > max_last_gap:
        return None
    return {"n_days": n, "coverage": round(coverage, 3), "p25": p25, "p50": p50, "p75": p75,
            "iqr": iqr, "recent": recent, "last_gap": (today - days_seen[-1]).days}


def abs_grace_min(iqr, floor=_ABS_GRACE_FLOOR, cap=_ABS_GRACE_CAP):
    """🌾 §1.79 寬限與**他自己的節奏**成比例：抓得緊的習慣（iqr 20 分）等 90 分就夠，鬆的等到上限。"""
    return max(floor, min(cap, int(iqr)))


def abs_topic_done_today(records, label, tz, now_ts):
    """🌾 §1.79 今天這條線有沒有出現（單一資料源＝記寫；**絕不**用「對話沒提到」推「沒做」）。算不出＝None。"""
    if tz is None or not label:
        return None
    today = abs_day_key(now_ts, tz)
    seen = False
    for r in (records or []):
        if (r.get("topicLabel") or "").strip() != label:
            continue
        dt = _abs_rec_dt(r, tz)
        if dt is None:
            return None
        if dt.strftime("%Y-%m-%d") == today:
            seen = True
    return seen


# 🌾 §1.80 對話作息也是日課（HABIT_ABSENCE_CONVO；使用者更正需求：「作息習慣」不只記寫主題——
# 每天的早安、平常幾點會出現，這些**對話層的例行**也算；而且「他今天還沒出現」不是抑制條件，
# **正是**該去看看的場景：「平常這時間早該看到你了，今天還靜靜的」）。資料源＝habit_events（§1.42/§1.63
# 就在記的 greet_am 與 msg/contact），資格門檻比記寫類寬（min_days=7：事件是 §1.42 部署後才開始累積；
# 且這句話說錯的代價低——「還沒聽到你的早安」是暖的、不是指控）。
_ABS_CONVO_MIN_DAYS = 7
_ABS_CONVO_MAX_IQR = 150


def abs_event_daily_samples(events, kinds, tz, now_ts, days=_WINDOW_DAYS):
    """🌾 §1.80 habit_events 裡某類事件**每個日曆日最早**的分鐘 [(date, minute)]（今天不進樣本）。
    kinds=("greet_am",)＝早安習慣；kinds=("msg","contact")＝每天第一次出現。tz None → None。"""
    if tz is None:
        return None
    today = abs_day_key(now_ts, tz)
    cutoff = now_ts - (days + 1) * 86400
    firsts = {}
    for e in (events or []):
        ts = e.get("ts") or 0
        if e.get("k") not in kinds or ts < cutoff:
            continue
        dt = datetime.fromtimestamp(ts, timezone.utc).astimezone(tz)
        key = dt.strftime("%Y-%m-%d")
        if key == today:
            continue
        m = dt.hour * 60 + dt.minute
        if key not in firsts or m < firsts[key][1]:
            firsts[key] = (dt.date(), m)
    return sorted(firsts.values())


def abs_appear_verdict(profile, now_min, appeared_today, grace_min=None,
                       window_min=_ABS_WINDOW_MIN, cutoff_min=_ABS_CUTOFF_MIN):
    """🌾 §1.80 出現/早安習慣的判定（與 abs_verdict 的關鍵差異：**他還沒出現正是觸發**、不是抑制）。
    四值：'unknown'|'done'|'too_early'|'too_late'|'absent'。訊息送出後他回來自然看得到。"""
    if not profile or appeared_today is None:
        return "unknown"
    if appeared_today:
        return "done"
    grace = abs_grace_min(profile["iqr"]) if grace_min is None else grace_min
    due = profile["p75"] + grace
    if due > cutoff_min:
        return "too_late"
    if now_min < due:
        return "too_early"
    if now_min > min(due + window_min, cutoff_min):
        return "too_late"
    return "absent"


def abs_verdict(profile, now_min, today_first_min, done_today, grace_min=None,
                window_min=_ABS_WINDOW_MIN, cutoff_min=_ABS_CUTOFF_MIN, awake_min=_ABS_AWAKE_MIN):
    """🌾 §1.79 六值判定：'unknown'|'done'|'not_present'|'too_early'|'too_late'|'absent'。純函式、可單測。
    due＝p75＋寬限；**每天每條線只有一段最長 window_min 的可問窗**，過窗就永遠沉默（不補問、不隔天翻舊帳）。"""
    if not profile or done_today is None:
        return "unknown"
    if done_today:
        return "done"
    if today_first_min is None:
        return "not_present"                                  # 他今天根本還沒出現＝人不在，不是習慣缺席
    grace = abs_grace_min(profile["iqr"]) if grace_min is None else grace_min
    due = profile["p75"] + grace
    if due > cutoff_min:
        return "too_late"                                     # 這條線本來就太晚＝永遠不問（不熬夜查勤）
    if now_min - today_first_min < awake_min:
        return "not_present"                                  # 他今天才剛開始活動＝還早，不是沒做
    if now_min < due:
        return "too_early"
    if now_min > min(due + window_min, cutoff_min):
        return "too_late"
    return "absent"


def abs_candidates(records, tz, now_ts, today_first_min, skip_labels=(), ingest_ts=None, **kw):
    """🌾 §1.79 掃出此刻真的「缺席」的線 → [{label, profile, due_min, overdue}]（只回 absent）。
    排序：overdue 小的優先＝最接近他平常的節奏＝像剛注意到，不像事後查勤。fail-closed：健康檢查不過＝空。"""
    health = abs_records_health(records, tz, now_ts, max_stale_h=kw.get("max_stale_h", _ABS_STALE_H),
                                ingest_ts=ingest_ts)
    if not health["ok"]:
        return []
    now_min = _minutes_local(now_ts, tz)
    out = []
    labels = {(r.get("topicLabel") or "").strip() for r in (records or [])}
    for label in sorted(l for l in labels if l and l not in set(skip_labels)):
        prof = abs_routine_profile(abs_topic_daily_samples(records, label, tz, now_ts), now_ts, tz)
        if not prof:
            continue
        v = abs_verdict(prof, now_min, today_first_min, abs_topic_done_today(records, label, tz, now_ts))
        if v != "absent":
            continue
        due = prof["p75"] + abs_grace_min(prof["iqr"])
        out.append({"label": label, "profile": prof, "due_min": due, "overdue": now_min - due})
    return sorted(out, key=lambda c: (c["overdue"], -c["profile"]["coverage"], c["label"]))


def habit_facts(state, records, now_ts, tz, daily_first=False):
    """【你（使用者）的習慣——程式從真實事件算的】接地事實塊：每行不是接地統計、就是誠實說不準。
    對話作息（早安/第一句）＋記寫時段＋記寫主題×時段的事後習慣分析。
    daily_first（§1.63、旗標傳入；False＝同現狀）＝「一天第一句」改用真的日曆日分組統計。"""
    ev = getattr(state, "habit_events", None) or []
    fst = daily_first_stats(ev, tz, now_ts) if daily_first else stats(ev, "first", tz, now_ts)
    lines = ["【他的習慣（我真的記到的事件算出來的；樣本不夠就說不準）】",
             "・" + _stat_line(daily_kind_stats(ev, "greet_am", tz, now_ts), _KIND_LABEL["greet_am"]),
             "・" + _stat_line(fst, _KIND_LABEL["first"]),
             "・" + _record_hours_line(records, tz, now_ts)]
    lines += _topic_habit_lines(records, tz, now_ts)
    return "\n".join(lines)


def today_vs_usual_line(state, now_ts, tz, daily_first=False):
    """greeting lane 用：「今天第一句 vs 平常」的程式算比較。樣本不夠＝回警語（別對他作息下判斷）；
    有統計＝接地比較句（早/晚/差不多，差 <20 分＝差不多）。
    daily_first（§1.63）＝統計與「今天第一句」都改日曆日語意（今天＝真的今天、第一句＝真的最早）。"""
    ev = getattr(state, "habit_events", None) or []
    if daily_first:
        st, tm = daily_first_stats(ev, tz, now_ts), today_first_minutes(ev, tz, now_ts)
    else:
        st = stats(ev, "first", tz, now_ts)
        day_start = now_ts - (now_ts % 86400)  # 粗切：找今天（本地日界差異由下面本地分鐘比較吸收）
        today = [e for e in ev if e.get("k") == "first" and (e.get("ts") or 0) >= now_ts - 18 * 3600]
        tm = _minutes_local(today[-1]["ts"], tz) if today else None
    if st is None or tm is None:
        return _NO_ROUTINE_STAT_LINE
    n, med, lo, hi = st
    if abs(tm - med) < 20:
        cmpw = "跟平常差不多"
    else:
        cmpw = f"比平常{'早' if tm < med else '晚'}了約 {abs(tm - med)} 分鐘"
    return (f"〔作息事實（程式算的，要提他的作息**只准照這條**）〕他今天第一句是 {hhmm(tm)}；"
            f"過去他一天的第一句多在 {hhmm(lo)}–{hhmm(hi)}（中位 {hhmm(med)}、樣本 {n} 次）＝今天{cmpw}。")


# 🕘 §2.22 沒統計時的分寸警語（today_vs_usual_line 與 greet_aware_line 共用＝逐字同一句）
_NO_ROUTINE_STAT_LINE = ("〔作息分寸〕你手上**沒有**他作息的可靠統計——**別**對他今天起得早/晚、平常幾點出現下任何判斷，"
                         "也別說「比平常早/晚」。")

# 🕘 §2.22 「差不多時間」這種**尋常**觀察的再提冷卻：介於 1~2 天（40h）＝隔天可以再提、
# 但不會連續兩個早晨複誦同一個觀察（有意識的熟悉不是每天報到都講一遍「你都這時候出現」）。
_GREET_REMARK_GAP_S = 40 * 3600
# 「今天早／晚了」也不能每次有偏差就盤問；同一天內只端一次作息觀察，讓下一句問候可以只是問候。
_GREET_DEVIATION_GAP_S = 18 * 3600

# 🌙 §2.27 深夜帶界線（分鐘，05:00）：日曆日在午夜翻頁，但**人的一天**沒有——00:27 出現是昨天的一天
# 還沒收（熬夜），不是「今天來得早」。與 _ABS_MIN_P25_MIN 同一個 05:00 判準（那裡早就寫明
# 「夜貓/凌晨型不參選（判定會跨日界）」——§2.22 的早/晚比較漏掉了同一課）。
_SMALL_HOURS_MIN = 300


def greet_aware_line(state, now_ts, tz, daily_first=False, last_remark_ts=0.0, remark_gap_s=_GREET_REMARK_GAP_S,
                     smallhours=False, greet_kind=None, deviation_gap_s=_GREET_DEVIATION_GAP_S):
    """🕘 §2.22 問候輪的**作息覺察**接地（today_vs_usual_line 的「熟悉感」版）：同一份統計、同一個 20 分判準，
    但把「報表框架」（中位/樣本/只准照這條）換成「你自己注意到的事」框架——報表框架餵進 voice_greeting
    正是截圖制式化的根因（greeting.facts 說「別報數據」、這行卻端上統計＝兩個 hint 打架，LLM 不是照唸
    報表就是換句話講錯方向、被 §1.42 守門剝光只剩統計模板）。

    ・沒統計 → 同一句分寸警語（與 today_vs_usual_line 逐字同句），verdict="none"。
    ・偏離 ≥20 分 → 值得**有意識地**說出來：給「比平常早/晚了約 N 分鐘」的結論句素材
      （方向詞正是 §1.42/§1.97 守門驗的東西＝照講必過門），邀請它順著問候自然帶一句，verdict="early"|"late"。
    ・差不多 → 尋常日子：近 remark_gap_s 內才提過作息 → ("", "quiet")＝這次別提、好好回應問候本身
      （結構性保證多樣、不靠 LLM 自律）；夠久沒提 → 熟悉感結論（差不多是你會出現的時間），verdict="usual"。

    回 (注入行, verdict)。純函式、不寫 state——「這輪真的把作息端上桌」的 stamp 由呼叫端記。"""
    ev = getattr(state, "habit_events", None) or []
    if greet_kind == "night":
        return "", "quiet"                 # 晚上好／晚安 speech act 與跨午夜暫不拿線性作息互比
    if greet_kind in _GREET_ROUTINE_KIND and greeting.time_match(
            greet_kind, temporal.day_part(_minutes_local(now_ts, tz) // 60)) != "match":
        return "", "quiet"                 # 故意在錯時段說問候，只處理絕對時間落差、不談「比平常早晚」
    if greet_kind in _GREET_ROUTINE_KIND:
        # 早安只和過去的早安比、晚間問候只和過去的晚間問候比；本輪已先入帳，基準必須排除今天。
        st = daily_kind_stats(ev, _GREET_ROUTINE_KIND[greet_kind], tz, now_ts, exclude_today=True)
        tm = _minutes_local(now_ts, tz)
        subject = {"morning": "這句早安", "noon": "這句午間問候", "night": "這句晚間問候"}[greet_kind]
    elif daily_first:
        st = daily_first_stats(ev, tz, now_ts, exclude_today=True)
        tm = today_first_minutes(ev, tz, now_ts)
        subject = "他今天第一次出現"
    else:
        st = stats(ev, "first", tz, now_ts, exclude_today=True)
        today = [e for e in ev if e.get("k") == "first" and (e.get("ts") or 0) >= now_ts - 18 * 3600]
        tm = _minutes_local(today[-1]["ts"], tz) if today else None
        subject = "他今天第一次出現"
    if st is None or tm is None:
        return _NO_ROUTINE_STAT_LINE, "none"
    n, med, lo, hi = st
    # 同一個人不會七小時內又拿同一種「你今天早/晚」觀察盤問一次；所有偏離型評論共用短冷卻。
    if last_remark_ts and now_ts - last_remark_ts < deviation_gap_s:
        return "", "quiet"
    # 🌙 §2.27（SMALLHOURS_ARRIVAL；smallhours=False＝逐位元同 §2.22）：今天第一句與平常中位分踞
    # 05:00 界兩側 ⇒ 「比平常早/晚 N 分」在人的一天裡沒有意義（00:27 vs 早上 7 點＝差 403 分的「早」＝
    # 實測截圖的荒謬句）。深夜側＝他昨天的一天還沒收——關心方向是「這麼晚還醒著」，不是「今天來得早」。
    if smallhours and (tm < _SMALL_HOURS_MIN) != (med < _SMALL_HOURS_MIN):
        if tm < _SMALL_HOURS_MIN:
            return ((f"〔深夜分寸（程式算的）〕他此刻 {hhmm(tm)}（深夜）出現——這不是「今天來得早」，"
                     "是他**昨天的一天還沒收**（他平常要到早上才第一次出現）。**別**說他今天比平常早或晚；"
                     "要關心就往「這麼晚還醒著、記得休息」的方向，順著他的話溫一句就好。"), "smallhours")
        return ((f"〔日夜界分寸（程式算的）〕他平常是深夜才出沒的，今天卻 {hhmm(tm)} 就出現——跨了日夜界，"
                 "「比平常早/晚幾分鐘」在這裡沒有意義、**別**這樣講；可以自然好奇一句今天節奏不太一樣。"),
                "smallhours")
    diff = tm - med
    if abs(diff) < 20:
        if last_remark_ts and now_ts - last_remark_ts < remark_gap_s:
            return "", "quiet"
        return ((f"〔你對他的熟悉（程式算的）〕{subject}在 {hhmm(tm)}——差不多就是你記得的時間。"
                 "不必每次都把作息說出口；這次可以只好好回應問候本身。若要提，只陳述觀察，不必追問原因；"
                 "**別**說他比平常早或晚，別報時間區間或次數，更別用中位數/樣本這種報表詞。"), "usual")
    word = "早" if diff < 0 else "晚"
    return ((f"〔你對他的熟悉（程式算的；方向與分鐘**只准照這條**）〕{subject}在 {hhmm(tm)}，"
             f"比平常{word}了約 {abs(diff)} 分鐘——這是你**自己注意到**的事。"
             "若順著問候說出來，這次可只陳述、不一定追問；只有真的有新的具體好奇才問，且別沿用上次問候的句型。"
             "別報時間區間或次數，別用中位數/樣本這種報表詞。"), "early" if diff < 0 else "late")


def record_stats(records, tz, now_ts, days=_WINDOW_DAYS, min_n=_MIN_N):
    """📈 §1.97 **記寫**時刻的當天分鐘分佈 →（n, 中位, p25, p75）；不足 min_n＝None。

    形狀刻意與 `stats()` 一模一樣，好讓守門對「說話」與「記寫」兩域用同一段判定碼。
    資料源是 `data['records']` 的 ts——**不是** `habit_events`（那裡面全是對話事件，§1.96 實測記寫 0 筆，
    拿它去驗「你平常多半是在早上記寫的」正是那次的類別錯置）。"""
    cutoff = now_ts - days * 86400
    mins = []
    for r in (records or []):
        dt = analyzer.parse_ts(r.get("ts"))
        if dt is None or dt.timestamp() < cutoff:
            continue
        mins.append(_minutes_local(dt.timestamp(), tz))
    mins.sort()
    n = len(mins)
    if n < min_n:
        return None
    return (n, mins[n // 2], mins[n // 4], mins[(3 * n) // 4])


def today_record_minutes(records, tz, now_ts):
    """📈 §1.97 今天（本地日曆日）**第一筆記寫**的當天分鐘；今天還沒記＝None。
    給「今天比平常晚記」這種方向宣稱當今天側的錨（記寫域不能拿說話的今天第一句來比）。"""
    today, _ = _local_day_min(now_ts, tz)
    best = None
    for r in (records or []):
        dt = analyzer.parse_ts(r.get("ts"))
        if dt is None or dt.timestamp() < now_ts - 2 * 86400:
            continue
        day, m = _local_day_min(dt.timestamp(), tz)
        if day == today and (best is None or m < best):
            best = m
    return best


def claim_guard_data(state, now_ts, tz, daily_first=False, records=None, role=False, greet_kind=None,
                     forbid_greet_compare=False):
    """_say 守門用的接地資料：greet_am/first 統計＋今天第一句的本地分鐘（無＝None）。
    daily_first（§1.63）＝first 統計與今天第一句改日曆日語意（守門從此對照真的「一天第一句」）。
    records/role（📈 §1.97）＝多帶**記寫域**的統計與今天第一筆，並掀開語意角色判讀；
    不傳＝這兩個鍵不存在＝守門走原本的詞面路徑＝逐位元同現狀。"""
    ev = getattr(state, "habit_events", None) or []
    if daily_first:
        out = {"greet_am": daily_kind_stats(ev, "greet_am", tz, now_ts),
               "first": daily_first_stats(ev, tz, now_ts),
               "today_first_min": today_first_minutes(ev, tz, now_ts)}
    else:
        today = [e for e in ev if e.get("k") == "first" and (e.get("ts") or 0) >= now_ts - 18 * 3600]
        out = {"greet_am": daily_kind_stats(ev, "greet_am", tz, now_ts),
               "first": stats(ev, "first", tz, now_ts),
               "today_first_min": _minutes_local(today[-1]["ts"], tz) if today else None}
    if greet_kind in _GREET_ROUTINE_KIND:
        ref = _GREET_ROUTINE_KIND[greet_kind]
        out["greet_ref"] = ref
        out[ref] = daily_kind_stats(ev, ref, tz, now_ts, exclude_today=True)
        out["greet_today_min"] = _minutes_local(now_ts, tz)
    elif forbid_greet_compare:
        # 晚安／錯時段問候目前沒有可安全線性比較的同 speech-act 基準；用明確空域讓出口守門剝掉
        # 「比平常早／晚」句，而不是偷偷退回 daily-first 的另一把尺。
        out["greet_ref"] = "greet_forbidden"
        out["greet_forbidden"] = None
        out["greet_today_min"] = _minutes_local(now_ts, tz)
    if role:
        out["role"] = True
        out["write"] = record_stats(records, tz, now_ts)
        out["today_write_min"] = today_record_minutes(records, tz, now_ts)
    return out


# ── 📈 §1.97 作息宣稱的**語意角色**判讀（HABIT_ROLE）────────────────────────────────
# §1.42 的守門靠一條線性詞面樣式：「你」＋「平常」＋「X點」三者要按順序出現在同一句。實測（§1.96 截圖那四句）
# 全漏：「今天醒得比平常晚一點嗎」整句沒有「你」、「對我來說，是比平常晚一些些」的「我」根本不是作息的主人、
# 「今天這樣晚一點才醒來」連「平常」都沒說出口、「你平常多半是在早上記寫的」沒有鐘點。
#
# 改法不是往那條樣式再加詞（那是這個 repo 漏了 15+ 次的路），是把一句話拆成**三個獨立的語意角色**再組合：
#   ・主角＝這條作息是**誰的**（你/妳＝他；「我」要真的是作息的主人才算 bot 自己——「對我來說」是**立場框架**、
#     不是主人；沒有主詞＝話題預設就是他，§1.96 的「主詞永遠是他」同一條理）
#   ・基準＝拿什麼當尺（明說的「平常/通常」；「比平常/比較」；或「今天…晚一點」這種**省略了尺**的比較）
#   ・述語＝時間本身（鐘點／時段／方向早晚），沒有時間述語就根本不是作息宣稱
#   ・領域＝說話出現，還是記寫（§1.96 的類別錯置在這裡變成**選哪份統計來驗**）
# 三個槽各自解析、彼此不要求相鄰或順序——這才是「詞面 → 語意角色」的實質差別。用到的詞都是**封閉的功能詞類**
# （時間副詞、時段詞、方向詞），不是開放的動詞/主題表；漏一個新動詞不會讓閘瞎掉，因為閘從頭到尾不看動詞。
_ROLE_GROUND = ("平常", "通常", "平時", "往常", "一向", "向來", "一般", "每天", "天天",
                "多半", "大多", "老是", "總是", "習慣", "大概")
# 立場框架：「我」在這些構式裡是**說話者的視角**，不是作息的主人（§1.96 那句「對我來說，是比平常晚一些些」
# 就是靠這條才判得出主角其實是他——舊碼看到「我」就整句放過，等於替閃避背書）。
_ROLE_STANCE = ("對我來說", "對我而言", "在我看來", "我覺得", "我感覺", "我猜", "我想",
                "我記得", "我看來", "我以為", "我是說", "我知道", "我發現", "我注意到")
_ROLE_DAYPART = {"清晨": "清晨", "早上": "早上", "上午": "早上", "中午": "中午", "下午": "午後",
                 "午後": "午後", "傍晚": "傍晚", "晚上": "晚上", "半夜": "深夜", "凌晨": "深夜", "深夜": "深夜"}
_ROLE_APPEAR = ("醒", "起床", "睡醒", "出現", "現身", "冒出", "說話", "講話", "找我", "來找",
                "開口", "第一句", "早安", "午安", "晚安", "招呼", "問候", "報到", "上線", "露面")
_ROLE_WRITE = ("記寫", "記錄", "書寫", "寫下", "記下", "筆記", "寫")
# 未來/祈使（「晚一點再看吧」「等等早一點睡」）＝這不是對他作息的斷言，是安排 → 不進守門
_ROLE_IRREALIS = ("等等", "等一下", "等下", "待會", "晚點", "明天", "後天", "下次", "改天",
                  "要不要", "記得", "吧", "再")
_ROLE_TODAY = ("今天", "今早", "今晚", "今日", "現在", "這樣")
_ROLE_CLOCK_RE = re.compile(r"(清晨|早上|上午|中午|下午|午後|傍晚|晚上|半夜|凌晨|深夜)?\s*"
                            r"([0-9０-９]{1,2}|[一二兩三四五六七八九十]+)\s*點")
_ROLE_DP_RE = re.compile(r"(清晨|早上|上午|中午|下午|午後|傍晚|晚上|半夜|凌晨|深夜)")
# 方向詞：後面接程度/收尾才算（排除「晚上/早上」的時段用法、「早睡/晚睡」的動詞用法、「早點/晚點」的祈使用法）
_ROLE_DIR_RE = re.compile(r"(早|晚)(?=[一些多了嗎呢啊，,。.！!？?\s]|$)")
# 有比較標記撐著時，方向詞後面接動詞也算（「比平常晚醒」「比平常晚起床」——上面那條的收尾表接不到動詞）。
# 沿用 §1.42 `_HABIT_COMP_RE` 的 (?![睡點]) 排除：早睡/晚睡是另一件事、早點/晚點是祈使。
_ROLE_DIR_COMP_RE = re.compile(r"比(?:平常|平時|往常|以往|之前|較)?[^，。！？!?\n]{0,4}?(早|晚)(?![睡點])")
_ROLE_COMP_RE = re.compile(r"比(?:平常|平時|往常|以往|之前|較)|比較")
# 方向詞後面剩下的東西：只剩程度詞/語助詞＝這是**光禿禿的比較**（比的只能是他的出現）；還剩實詞＝比的是別件事
_ROLE_TAIL_NOISE_RE = re.compile(r"[一些點多了嗎呢啊喔耶吧的是些\s，,。.！!？?～~…—、]")
_ROLE_HONEST_MARK = "我手上記到的是"      # 守門自己補的誠實句 → 絕不能再被自己判成宣稱（自我觸發）


def _daypart_ranges(word):
    """時段詞 → 分鐘區間（**由 `temporal.day_part` 自己導出**，零新表：時段分界只有一個真相）。
    深夜跨午夜＝回兩段。認不得的詞回 []。"""
    from . import temporal
    want = _ROLE_DAYPART.get((word or "").strip())
    if not want:
        return []
    hrs = [h for h in range(24) if temporal.day_part(h) == want]
    if not hrs:
        return []
    segs, cur = [], [hrs[0]]
    for h in hrs[1:]:
        if h == cur[-1] + 1:
            cur.append(h)
        else:
            segs.append(cur)
            cur = [h]
    segs.append(cur)
    return [(s[0] * 60, (s[-1] + 1) * 60) for s in segs]


def daypart_ok(word, lo_min, hi_min, tol=90):
    """📈 §1.97 宣稱的時段詞 vs 真統計的 p25–p75 窗：重疊、或**差距在 tol 分鐘內**算相符。

    為什麼要容忍：`day_part` 的分界比口語細（06:37 是「清晨」），口語說「早上六點多」並不算掰；
    要抓的是「明明清晨卻說深夜/中午」這種整段錯位。
    ⚠️ 為什麼用分鐘距離而不是「相鄰格」：實測踩到——格子寬窄不一，深夜(23–05)與清晨(05–08)算相鄰，
    於是「你平常多半是在**深夜**記寫的」對真統計 07:10–08:10 竟然過關（實際差兩小時）。改量分鐘就準了。
    跨午夜用 ±24h 位移一起比，環狀不會漏。"""
    segs = _daypart_ranges(word)
    if not segs:
        return True                       # 認不得的時段詞＝不下判斷（fail-open，不亂剝）
    lo, hi = min(lo_min, hi_min), max(lo_min, hi_min)
    best = None
    for a, b in segs:
        for shift in (-1440, 0, 1440):
            s, e = a + shift, b + shift
            gap = 0 if (s <= hi and lo <= e) else min(abs(s - hi), abs(lo - e))
            best = gap if best is None else min(best, gap)
    return best is not None and best <= tol


def _role_domain(sent, whole, pos):
    """述語附近的**領域錨**：離述語最近的「說話/出現」或「記寫」線索；本句沒有就往整則找
    （§1.96 那則就是——「對我來說，是比平常晚一些些」自己沒有錨，同一則的下一句「今天這樣晚一點才醒來」有）。"""
    for text, at in ((sent, pos), (whole, None)):
        if not text:
            continue
        best, bestd = None, None
        for dom, words in (("write", _ROLE_WRITE), ("appear", _ROLE_APPEAR)):
            for w in words:
                i = text.find(w)
                if i < 0:
                    continue
                d = abs(i - at) if at is not None else i
                if bestd is None or d < bestd:
                    best, bestd = dom, d
        if best:
            return best
    return None


def _role_who(sent, pos):
    """主角＝**這條作息的主人**。做法：先把立場框架（對我來說／我覺得…）挖掉，再找述語左邊最近的人稱代詞；
    左邊沒有就看右邊；都沒有＝沒說出主詞＝話題預設是他（§1.96「主詞永遠是他」的同一條理）。"""
    s = sent or ""
    for f in _ROLE_STANCE:
        while f in s:
            s = s.replace(f, "　" * len(f), 1)     # 等長挖空：保住 pos 的索引語意
    left = [(i, s[i]) for i in range(min(pos, len(s))) if s[i] in "你妳我"]
    if left:
        return "bot" if left[-1][1] == "我" else "user"
    right = [(i, s[i]) for i in range(min(pos, len(s)), len(s)) if s[i] in "你妳我"]
    if right:
        return "bot" if right[0][1] == "我" else "user"
    return "user"


def claim_roles(sent, whole=""):
    """📈 §1.97 把一句話讀成**語意角色**：回 {"who","ground","pred","domain","pos"}，不是作息宣稱＝None。

    `pred`＝("clock", 時段詞或None, 數字) ／ ("daypart", 時段詞) ／ ("dir", "早"|"晚")。
    `pos`＝述語起點（呼叫端據此看前 12 字排除引用歸屬，§1.13B 同構）。純函式、不碰 state。

    刻意的保守設計（避免偽陽性把真話剝掉）：
      ・沒有時間述語 → 不是作息宣稱（「你平常都這樣想」不進門）
      ・沒有基準 → 不是宣稱（「你八點跟我說早安」＝敘述今天，不是「平常」）
      ・鐘點/時段述語**沒有領域錨** → 交還給 §1.42 原本的詞面路徑判（本函式不新增這類命中，
        免得「你平常晚上都在忙吧」被拿去跟『第一句話』的統計對照、剝完還補一句不搭嘎的誠實話）
      ・方向述語（早/晚）沒有錨時預設「說話出現」域——這不是新猜測，是 §1.42 `_HABIT_COMP_RE`
        本來就有的語意（它對裸的「你比平常晚」一律拿 first 統計驗）
      ・未來/祈使句一律不進門
    """
    s = sent or ""
    if not s.strip() or _ROLE_HONEST_MARK in s:
        return None
    pred = pos = None
    for m in _ROLE_CLOCK_RE.finditer(s):
        # ⚠️ 實測踩到：「今天醒得比平常晚**一點**嗎」被讀成鐘點「一點」＝把 §1.96 那句有憑有據的話判成亂掰。
        # 裸的「一點」在中文裡幾乎都是程度詞（晚一點／差一點／有一點）；真要講凌晨一點會帶時段詞。
        if m.group(1) is None and m.group(2) in ("一", "1", "１"):
            continue
        pred, pos = ("clock", m.group(1), m.group(2)), m.start()
        break
    gwin, tail = s, ""
    if pred is None:
        m = _ROLE_DIR_COMP_RE.search(s) or _ROLE_DIR_RE.search(s)
        if m:
            tail = s[m.end():]
            # ⚠️ 尺要在方向詞**之前**才算數（結構條件，不是再加一張詞表）：真的比較是「比平常→晚」，
            # 而「早一點睡比較好」的「比較」修飾的是「好」、在方向詞之後＝那是建議不是斷言。實測踩到。
            pred, pos, gwin = ("dir", m.group(1)), m.start(), s[:m.end()]
        else:
            m = _ROLE_DP_RE.search(s)
            if not m:
                return None
            pred, pos = ("daypart", m.group(1)), m.start()
    if pred[0] == "dir" and any(w in s for w in _ROLE_IRREALIS):
        return None                                   # 「你平常晚一點再回我就好」＝安排，不是斷言
    if any(w in gwin for w in _ROLE_GROUND):
        ground = "habitual"
    elif _ROLE_COMP_RE.search(gwin):
        ground = "comparative"
    elif pred[0] == "dir" and any(w in gwin for w in _ROLE_TODAY):
        ground = "implicit"                           # 「今天這樣晚一點才醒來」＝尺沒說出口，但比的就是他平常
    else:
        return None
    domain = _role_domain(s, whole, pos)
    if domain is None:
        if pred[0] != "dir" or ground == "implicit":
            return None                               # 未錨定的鐘點/時段、與省略尺的比較 → 不新增命中
        if _ROLE_TAIL_NOISE_RE.sub("", tail).strip():
            return None                               # 「今天比較晚**吃飯**喔」＝比的是別件事，別拿說話統計去驗
        domain = "appear"                             # 光禿禿的「比平常晚」＝只可能在講他的出現（§1.42 原語意）
    return {"who": _role_who(s, pos), "ground": ground, "pred": pred, "domain": domain, "pos": pos}


_KIND_SHORT = {"msg": "訊息", "contact": "貼圖/媒體", "first": "隔靜首句",
               "greet_am": "早安", "greet_noon": "午安", "greet_pm": "晚安"}


def _local_stamp(ts, tz):
    dt = datetime.fromtimestamp(ts, timezone.utc)
    if tz is not None:
        dt = dt.astimezone(tz)
    return dt.strftime("%m/%d %H:%M")


def audit_text(state, now_ts, tz):
    """📈 §1.63 /habits 對帳輸出（確定性、不經 LLM）：事件量＋各類統計＋最近事件的本地時刻——
    讓使用者能直接檢查 bot 到底記到了什麼（「09:54–20:58」這種爭議能當場對資料源）。"""
    ev = getattr(state, "habit_events", None) or []
    cnt = {}
    for e in ev:
        cnt[e.get("k")] = cnt.get(e.get("k"), 0) + 1
    lines = [f"📈 習慣觀測對帳（事件 {len(ev)} 筆：訊息 {cnt.get('msg', 0)}、貼圖/媒體 {cnt.get('contact', 0)}、"
             f"隔靜首句 {cnt.get('first', 0)}、早安 {cnt.get('greet_am', 0)}、午安 {cnt.get('greet_noon', 0)}、"
             f"晚安 {cnt.get('greet_pm', 0)}）",
             "・" + _stat_line(daily_first_stats(ev, tz, now_ts), "一天（日曆日）第一次出現"),
             "・" + _stat_line(stats(ev, "greet_am", tz, now_ts), _KIND_LABEL["greet_am"]),
             "・" + _stat_line(stats(ev, "greet_pm", tz, now_ts), _KIND_LABEL["greet_pm"])]
    tail = ev[-10:]
    if tail:
        lines.append(f"最近 {len(tail)} 筆：" + "、".join(
            f"{_KIND_SHORT.get(e.get('k'), e.get('k'))}@{_local_stamp(e.get('ts') or 0, tz)}" for e in tail))
    else:
        lines.append("（還沒有任何事件——旗標剛開或事件被清過。）")
    return "\n".join(lines)


def routine_card(state, records, now_ts, tz, daily_first=True):
    """📈 §1.96 給 §1.61 事實卡的**常駐**作息事實（緊湊、≤3 行）。

    為什麼要常駐（截圖 07:38–07:51 的根因，實測）：作息接地原本只掛在兩個瞬間——問候那一輪
    （monitor.py:4453 `today_vs_usual_line`）與「他明著問作息」那一輪（monitor.py:7280
    `is_user_habit_question`）。使用者說「早安」→ 有接地，bot 於是正確地問「今天醒得比平常晚一點嗎」；
    但他追問「有嗎」時**兩個接地都不掛** ⇒ bot 手上一個數字都沒有，只好從語感生出
    「對我來說，是比平常晚一些些」（主詞還掉了）。**它不是不知道，是知道的那一刻過去了。**
    這正是 §1.61 事實卡要治的架構病：真相不該靠偵測器命中才給。

    第三行是**記寫時段**（`_record_hours_line`，資料源＝records 的 ts）——治另一個實測缺陷：
    `habit_events` 全是對話事件（訊息/早安/晚安），拿它去講「你平常多半是在早上記寫的」是**類別錯置**。
    樣本不足時該函式本來就會誠實說「說不準」。"""
    line = today_vs_usual_line(state, now_ts, tz, daily_first=daily_first)
    for tag in ("〔作息事實（程式算的，要提他的作息**只准照這條**）〕", "〔作息分寸〕"):
        line = line.replace(tag, "")
    out = ["・他跟你說話的作息：" + line.strip()]
    try:
        # 記寫時段**另立一行**——`habit_events` 全是對話事件，拿它講記寫是類別錯置（實測根因）
        rec = _record_hours_line(records, tz, now_ts).strip()
        out.append("・他記寫的時段（跟上面那條是兩回事）：" + rec.replace("你的記寫時段：", "").replace("你的記寫", "").strip())
    except Exception:
        pass
    return "\n".join(out)
