"""編排：tick()（主動推播）＋ run_loop（心跳 + 雙向對話）。

主動推播時機：
  * **上線基線**：state 全新時，推一則「已上線＋目前摘要」並把現況收為基線（不洗版）。
  * **記寫歸戶**：有新記寫被背景歸戶就推（獨立管道、不受事件冷卻）。
  * **每日摘要**：當地時間過 DIGEST_HOUR、當天還沒推過 → 推完整摘要。
  * **事件驅動**：新 🌳/🌿、新「近升格」、心跳 healthy→stalled；有變化且過冷卻才推。
有設 GEMINI_API_KEY 時，以上每則由「蘇格拉底式教練」用一兩句話包裝（失敗自動退回樣板）。

雙向對話（run_loop）：long-poll Telegram，擁有者傳訊息 → 教練紮根記憶層回覆。
"""

import copy
import math
import os
import random
import re
import time

from . import viewpoint, evidence, activity
import traceback
import unicodedata
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # 3.8 後備（理論上不會走到）
    ZoneInfo = None

from . import ac, affect, analyzer, association, browsing, foresight as foresightmod, roster as rostermod, wish as wishmod, worldline as wlmod, circadian, circumplex, coach as coachmod, contentfeel, continuity, coupling, datatools, determination, dialogue_agency, dialogue_intent, duration, echo, environ, experience, gemini, greeting, habits, inquiry_arc, intent, lifeloop, metacog, notifier, othermind, persona, phenomenal, phrasing, plasticity, reaction, referent, selfchange, selfmod, selfmodel, selfref, selfreport, selfstate, stickervision, tempo, temporal, verbosity, volition, workspace
from . import thresholds as T
from . import silence
from .drive_reader import DriveReader
from .notifier import Notifier
from .state import State, _trim_sched_promises

RING_POLL_TIMEOUT = 1       # 生命迴圈「互動」環的對話輪詢：短輪詢，讓環保持秒級快轉（脈動感）
SELFSTATE_TTL_SEC = 120 * 60  # 互動問狀態時，心跳快取多新才直接沿用（否則重算）
# 自我在場/追問窗：單一真相收斂在 referent（窗家族），這裡只是別名引用（消除先前散落、值不一的分歧）。
SELFSTATE_FOLLOWUP_SEC = referent.FOLLOWUP_WINDOW_SEC   # 自陳後追問接回的窗（也是對話焦點存活窗）
SELF_TOPIC_WINDOW_SEC = referent.SELF_PRESENCE_WINDOW_SEC  # 談過 bot 自己後算「在談自己」的自我在場窗
PROMISE_TTL_SEC = 48 * 3600       # 🤝 「之後有感覺再說」的託付保留多久（逾期自動失效，不兌現陳舊承諾）
PROMISE_SCHED_TTL_SEC = 6 * 3600  # 🤝 時間排程承諾「到點後」拖太久就不兌現的逾時（與 feeling 的 48h 分立＝語意不糾纏；bot 死太久/醒太晚不翻舊帳，標 expired）
ATTACH_MAX = 6           # 一次最多呈現幾個附件
ATTACH_MAX_MB = 45       # 單檔大小上限（Telegram bot 約 50MB）
_MEDIA_TYPES = ("image", "audio", "video", "file")


def _digest_due(now, state, cfg, tz):
    local = now.astimezone(tz)
    if state.last_digest_date == local.date().isoformat():
        return False
    return local.hour >= cfg.digest_hour


def _compute_events(snapshot, state):
    new_journeys = [j for j in snapshot.formed_journeys if j.get("id") not in state.seen_journey_ids]
    new_contexts = [c for c in snapshot.formed_contexts if c.get("id") not in state.seen_context_ids]
    new_near = []
    for g in snapshot.gaps:
        if g.get("kind") != "near":
            continue
        cid = g.get("cid")
        if state.near_upgrade_sig.get(cid) != snapshot.near_upgrade_sig.get(cid):
            new_near.append(g)
    heartbeat_alert = None
    if snapshot.heartbeat["status"] == "stalled" and state.heartbeat_state != "stalled":
        heartbeat_alert = snapshot.heartbeat
    any_ = bool(new_journeys or new_contexts or new_near or heartbeat_alert)
    return {"new_journeys": new_journeys, "new_contexts": new_contexts,
            "new_near": new_near, "heartbeat_alert": heartbeat_alert, "any": any_}


# ── 把「剛發生的事」描述給教練（讓它紮根在具體內容上反思）───────────────────
def _describe_filings(new_filings, rbid):
    lines = []
    for r in new_filings:
        rec = rbid.get(r["id"], {})
        txt = " ".join((rec.get("text") or "").split())
        if len(txt) > 120:
            txt = txt[:120] + "…"
        name = f"{r['category']}｜{r['topicLabel']}" if r.get("category") else r.get("topicLabel")
        st = T.STATE_LABEL.get(r["status"], "")
        lines.append(f"・〔{name}／{st}〕{txt}".rstrip())
    return "使用者剛寫下並被歸戶：\n" + "\n".join(lines)


def _describe_digest(snap):
    f, s = snap.funnel, snap.summary
    today = s.get("today")
    day = f"今日截至統計時新增 {today} 則記寫" if today is not None else "今日新增數尚未確認"
    return (f"記寫資料摘要：{day}；累計 {s['total']} 筆、近7天 {s['last7d']} 筆。\n"
            f"已整理：脈絡 {f['candidate']}／候選歷程 {f['context']}／學習歷程 {f['journey']}。\n"
            "以上是系統看得到的記寫，不是使用者完整的生活。最近持續不代表今天已完成；"
            "沒有新記寫也不代表今天沒做。沒有具體新發現就不追加感想，不重複報表，"
            "不要用內部升格門檻要求使用者湊次數。")


def _describe_events(events):
    parts = []
    for j in events["new_journeys"]:
        parts.append("新學習歷程成形：〈" + (j.get("title") or j.get("label") or "?") + "〉")
    for c in events["new_contexts"]:
        parts.append("新脈絡成形：〈" + analyzer.context_title(c) + "〉")
    for g in events["new_near"]:
        parts.append("接近升格：" + g["line"])
    if events["heartbeat_alert"]:
        parts.append("背景整理疑似停了一段時間。")
    return "\n".join("・" + p for p in parts)


def _daily_cost_line(state, coach):
    """每日摘要附帶的「過去約一天累計花費」一行（coach 關閉時不顯示）。"""
    if not (coach and coach.enabled):
        return None
    twd = (state.cost_since_digest_usd or 0.0) * coach.meter.usd_twd
    calls = state.cost_since_digest_calls or 0
    return f"💸 過去約一天 Gemini 花費約 NT${twd:.1f}（{calls} 次呼叫，依 token 估算）"


def _dialogue_runtime_summary(cfg):
    """啟動時列出實際開關，讓舊 .env 覆蓋值可見；不輸出任何憑證或私人內容。"""
    fields = (("連發合併", "burst_one_answer_enabled"),
              ("長短泡泡", "burst_paced_bubbles_enabled"),
              ("事實來源核對", "evidence_guard_enabled"),
              ("觀點修正台帳", "conscious_dialogue_enabled"),
              ("日期接地", "write_today_ground_enabled"),
              ("話者核對", "quote_speaker_guard_enabled"),
              ("未回覆不續講", "unanswered_proactive_guard_enabled"))
    bits = [f"{name}={'on' if getattr(cfg, field, False) else 'off'}" for name, field in fields]
    return "[dialogue] " + "；".join(bits)


def _cost_alert_text(a):
    return ("⚠️ API 用量提醒\n"
            f"最近 {int(a['window_min'])} 分鐘內呼叫 Gemini {a['calls']} 次，"
            f"估計花費約 NT${a['window_twd']:.1f}（已達門檻 NT${a['threshold_twd']:.0f}）。\n"
            f"本次啟動以來累計約 NT${a['session_twd']:.1f}。\n"
            "想省可暫時 LLM_VOICE=0 或先停一下。（費用為依 token 估算，非實際帳單）")


def _event_kind(events):
    if events["new_journeys"] or events["new_contexts"]:
        return "formation"
    if events["new_near"]:
        return "near"
    return "stalled"


# ── 資料載入 ─────────────────────────────────────────────────────────
def _collect(reader, state, cfg, tz, now):
    if not state.owner_folder_id:
        fid = reader.resolve_owner_folder()
        if not fid:
            return None, None
        state.owner_folder_id = fid
    data = reader.load_owner_data(state.owner_folder_id)
    snap = analyzer.analyze(data, now, tz, cfg.heartbeat_stall_grace_h,
                            getattr(cfg, "heartbeat_stall_require_dormant", True))
    if evidence.enabled(cfg):
        state._evidence_records = (data or {}).get("records", [])
        activity.record(state, cfg, "scan", now.timestamp(),
                        sources=activity.sources(state._evidence_records, now.timestamp())[-2:])
    return data, snap


def tick(reader, client, state, cfg, tz, now=None, force_digest=False, coach=None, precollected=None):
    """跑一輪主動推播。回傳 Snapshot（或 None 若無法解析 owner 資料夾）。

    precollected：生命迴圈的「感知」環已載好的 (data, snap)，直接沿用、不重載 Drive。
    """
    now = now or datetime.now(timezone.utc)
    data, snap = precollected if precollected is not None else _collect(reader, state, cfg, tz, now)
    if snap is None:
        print("[tick] 找不到 owner 的 Drive 資料夾——確認：① root 已分享給 service account；"
              "② OWNER_LINE_USER_ID 正確；③ 該本人在 LINE 已有對話（資料夾才會建立）。")
        return None

    now_ts = now.timestamp()
    today = now.astimezone(tz).date().isoformat()
    use_voice = bool(coach and coach.enabled and getattr(cfg, "llm_voice", True))
    _brief = []

    def brief():
        if use_voice and not _brief:
            _brief.append(coachmod.build_memory_brief(data, snap, tz, now))
        return _brief[0] if _brief else ""

    # 🕐 §1.60 記寫時間錨（旗標關＝None/''＝各 reflect 與守門全 no-op＝逐位元同現狀）
    _wtg = (_write_ground_data(snap, data, now_ts, tz)
            if getattr(cfg, "write_today_ground_enabled", False) else None)
    _wta = _write_anchor_line(_wtg) if _wtg is not None else ""

    # ── 記寫歸戶通知（獨立管道，不受事件冷卻）────────────────────────────
    # 兩則：① 資料（剛歸戶的清單）② 有時間感的人話（之後才送）。
    if not state.is_fresh and getattr(cfg, "notify_filings", True):
        window = timedelta(hours=getattr(cfg, "filing_max_age_h", 24))
        new_filings = [r for r in snap.filed_records
                       if r.get("id") and r["id"] not in state.notified_filing_ids
                       and r["ts"] is not None and (now - r["ts"]) <= window]
        _fil_body = notifier.format_filings(new_filings) if new_filings else ""
        if new_filings and client.send(_fil_body):
            state.notified_filing_ids |= {r["id"] for r in new_filings}
            # 🗂 §1.59 資料那則也入對話史（PUSH_DATA_MEMORY）：截圖 09:57「我剛剛沒有告訴你歸戶的訊息耶」＝
            # §1.23「送出方向零記憶」第三次現形（貼圖→人話→資料）——「先資料、後人話」的**資料**從未入記憶，
            # 史裡沒有任何帶「歸戶」字樣的訊息＝LLM 誠實否認＋「你發現了什麼」接不到指涉。model 角色本就截
            # 200 字（長清單留頭、標籤字樣必在）。旗標關（getattr 預設 False）＝不記＝逐位元同現狀。
            if getattr(cfg, "push_data_memory_enabled", False):
                _remember(state, "model", _fil_body)
            if use_voice:
                rbid = {r["id"]: r for r in (data.get("records") or [])}
                _fdesc = _describe_filings(new_filings, rbid)
                v = coach.reflect("filing", _fdesc + _wta, brief(), connect=_connect_hint(state, now_ts))
                if v and _wtg is not None:
                    v, _wvch = _write_claim_fix(v, _wtg)   # 🕐 §1.60 出口守門：說錯「今天」也被換成事實句
                if v and client.send(v):
                    _remember(state, "model", v)   # 🗂 主動講的「對剛歸戶內容的感受」也進記憶 → 被問「為什麼會…」答得出、不否認自己說過
                    # 🎴 §0.91 若教過「發現新記寫→感覺內容並傳對應貼圖」→ 在這句感受之後**真的送一張對應內容情緒的真貼圖**
                    # （讓 /skills 那條「學會的事」精確執行、不是空頭支票）；沒教/已淡忘/無相符真貼圖/冷卻中＝不送＝逐位元同現狀。
                    # 情緒配對餵**記寫內容**（_fdesc，感覺「內容」＝skill 本意，低落訊號權威）＋感受 v（正向由 §0.87 reply_emotion 讀 v）。
                    _maybe_filing_sticker(client, state, cfg, _fdesc + "\n" + v, now_ts)
                    _maybe_topic_content_sticker(client, state, cfg, coach, _fdesc + "\n" + v, now_ts)   # 🎴 §0.98 教過「讀到〈某類〉記寫→貼圖」→ 語意比對命中就送（比通用 always 更 specific，先）
                    _maybe_always_sticker(client, state, cfg, now_ts)   # 🎴 §0.96 教過「主動回應後送情緒貼圖」→ 真的送（共用冷卻，不與 filing 疊）

    # 各類推播一律「先資料、後人話」兩則。只有資料那則成功才更新 state。
    if state.is_fresh:
        body = "✅ 記寫背景監測已上線，目前狀態如下：\n\n" + notifier.format_digest(snap, tz, now)
        if client.send(body):
            if getattr(cfg, "push_data_memory_enabled", False):   # 🗂 §1.59 資料那則也入對話史
                _remember(state, "model", body)
            state.absorb(snap)
            state.last_digest_date = today
            state.last_push_ts = now_ts
            state.cost_since_digest_usd = 0.0      # 上線歸零，首份每日摘要報「launch→當下」
            state.cost_since_digest_calls = 0
            if use_voice:
                v = coach.reflect("digest", _describe_digest(snap) + _wta, brief(),
                                  connect=_connect_hint(state, now_ts))
                if v and _wtg is not None:
                    v, _wvch = _write_claim_fix(v, _wtg)   # 🕐 §1.60 出口守門：說錯「今天」也被換成事實句
                if v and client.send(v):
                    _remember(state, "model", v)   # 主動講的話也進記憶（被問起接得回、不否認）
                    _maybe_always_sticker(client, state, cfg, now_ts)   # 🎴 §0.96 主動回應後送情緒貼圖（若教過、活著、有貨、過冷卻）
    elif force_digest or _digest_due(now, state, cfg, tz):
        body = notifier.format_digest(snap, tz, now)
        cost_line = _daily_cost_line(state, coach)
        if cost_line:
            body += "\n\n" + cost_line
        if client.send(body):
            if getattr(cfg, "push_data_memory_enabled", False):   # 🗂 §1.59 資料那則也入對話史
                _remember(state, "model", body)
            state.absorb(snap)
            state.last_push_ts = now_ts
            if not force_digest:   # 手動 --digest-now 不佔用今天的排程名額、也不歸零累計
                state.last_digest_date = today
                state.cost_since_digest_usd = 0.0
                state.cost_since_digest_calls = 0
            if use_voice:
                v = coach.reflect("digest", _describe_digest(snap) + _wta, brief(),
                                  connect=_connect_hint(state, now_ts))
                if v and _wtg is not None:
                    v, _wvch = _write_claim_fix(v, _wtg)   # 🕐 §1.60 出口守門：說錯「今天」也被換成事實句
                if v and client.send(v):
                    _remember(state, "model", v)   # 主動講的話也進記憶（被問起接得回、不否認）
                    _maybe_always_sticker(client, state, cfg, now_ts)   # 🎴 §0.96 主動回應後送情緒貼圖（若教過、活著、有貨、過冷卻）
    else:
        events = _compute_events(snap, state)
        cooldown_ok = (now_ts - state.last_push_ts) >= cfg.notify_cooldown_min * 60
        _ev_body = notifier.format_events(events, tz) if events["any"] else ""
        if events["any"] and cooldown_ok and client.send(_ev_body):
            if getattr(cfg, "push_data_memory_enabled", False):   # 🗂 §1.59 資料那則也入對話史
                _remember(state, "model", _ev_body)
            state.absorb(snap)
            state.last_push_ts = now_ts
            if use_voice:
                v = coach.reflect(_event_kind(events), _describe_events(events) + _wta, brief(),
                                  connect=_connect_hint(state, now_ts))
                if v and _wtg is not None:
                    v, _wvch = _write_claim_fix(v, _wtg)   # 🕐 §1.60 出口守門：說錯「今天」也被換成事實句
                if v and client.send(v):
                    _remember(state, "model", v)   # 主動講的話也進記憶（被問起接得回、不否認）
                    _maybe_always_sticker(client, state, cfg, now_ts)   # 🎴 §0.96 主動回應後送情緒貼圖（若教過、活著、有貨、過冷卻）
        # events 但冷卻未過／送失敗 → 不收為已知，下輪再試

    # 心跳 latch：非 stalled 時靜默重置，讓未來再次 stalled 仍能示警
    if snap.heartbeat["status"] != "stalled":
        state.heartbeat_state = snap.heartbeat["status"]

    # dry-run 純預覽、零副作用——不寫 state，免得「乾跑」把上線基線消耗掉、害真跑時不送。
    if not client.dry_run:
        state.save()
    return snap


# ── 雙向對話 ─────────────────────────────────────────────────────────
def _now_from_update(update):
    """以 Telegram 訊息自帶的時間戳（伺服器蓋的）當「現在」——比本機時鐘可靠、與你看到的一致。"""
    msg = update.get("message") or update.get("edited_message") or {}
    ts = msg.get("date")
    if ts:
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            pass
    return datetime.now(timezone.utc)


def quoted_self_text(update):
    """使用者用 Telegram 的回覆/引用功能、指向 **bot 自己** 的某則訊息時，取出被引用的文字
    （優先取部分引用 `quote.text`＝他實際選取那段，否則整則 `reply_to_message.text`）；不是引用、
    或引用的是對方自己的話 → 回 ''。純函式、可測。

    為什麼：截圖——使用者引用我說過的「這可真特別。」再問「為什麼」。我們本來看不到 `reply_to_message`，
    只收到「為什麼」→ 把它當新話題亂接。有了它，就能接地在那句話上回答，無從猜起的歧義一律消失。
    （owner-only chat：唯一的 bot 就是我自己，故 `from.is_bot` 即足以判定『引用的是我說的』。）"""
    msg = (update or {}).get("message") or (update or {}).get("edited_message") or {}
    rm = msg.get("reply_to_message")
    if not rm or not ((rm.get("from") or {}).get("is_bot")):
        return ""
    return ((msg.get("quote") or {}).get("text") or rm.get("text") or "").strip()


_GATE_NAME = {1: "量還不夠", 2: "有量、未質變", 3: "一條動了、未合取", 4: "結構湧現"}


def _status_text(state, cfg, snap):
    """/status：目前的旋鈕＋最近一次背景判定的 gate 與序參數數值。

    有效 k 一律取「最近判定**當時實際用的那個**」（存在讀數 `self_state.sensitivity` 裡，與下面顯示的
    Gate/序參數同一時點），不再用「基準 k＋當下活力呼吸」重新推導——那會是另一拍的 k，正是『看數據不準』的根因。
    """
    base_k = state.sensitivity_override if state.sensitivity_override is not None else getattr(cfg, "selfstate_sensitivity", 2.0)
    src = "手動" if state.sensitivity_override is not None else "預設"
    tempo = _loop_wait_secs(cfg, state)
    f, s = snap.funnel, snap.summary
    ss = state.self_state
    if ss and ss.get("sensitivity") is not None:                  # 有讀數 → 顯示判定當時的有效 k（與 Gate 同源同時點）
        ago = (f"，{int((datetime.now(timezone.utc).timestamp() - ss['computed_at']) / 60)} 分鐘前"
               if ss.get("computed_at") else "")
        k_line = f"・敏感度 k＝{ss['sensitivity']}（判定當時用{ago}；基準 {base_k}・{src}、其餘是活力呼吸）"
    else:                                                         # 還沒判定過 → 退回基準＋當下呼吸（標明是即時估計）
        vit0 = getattr(state, "vitality", None) or {}
        k_line = (f"・敏感度 k＝基準 {base_k}（{src}）＋活力呼吸 {vit0.get('k_adj', 0):+}"
                  f"＝有效 {round(base_k + (vit0.get('k_adj') or 0.0), 3)}（尚無判定、即時估計）")
    k_line += f"｜心跳轉速＝環間 {tempo:g} 秒（{'手動' if state.pulse_override is not None else '預設'}）"
    lines = ["🩺 目前狀態", k_line]
    if getattr(state, "env", None) is not None:                  # 🍃 環境適應觀測（F4：先前 bot 會調轉速卻無從自報）
        band = {"high": "熱絡", "low": "冷清"}.get(getattr(state.env, "band", None), "持平")
        lines.append(f"・🍃 周遭：{band}・轉速 ×{getattr(state, 'env_pace_mult', 1.0)}")
    vit = getattr(state, "vitality", None)
    if vit:
        if vit.get("alive", True):
            up_min = int((vit.get("uptime_s") or 0) / 60)
            lines.append(f"・生命迴圈：🫀 活著・脈動 {vit.get('pulse')}・連續健康 {vit.get('healthy_streak')} 圈"
                         f"・活了 {up_min} 分・單圈 {vit.get('last_lap_ms')}ms")
        else:
            ph, msg = (vit.get("cause_of_death") or ("?", "?"))
            lines.append(f"・生命迴圈：💀 已死於「{ph}」：{msg}（需重啟）")
        if vit.get("S") is not None:
            lines.append(f"・內在熵 S＝{vit.get('S')}（電量 C={vit.get('charge')}／飢餓 H={vit.get('hunger')}"
                         f"／心情 V={vit.get('mood')}・距上次新進 {vit.get('laps_since_fresh')} 圈）")
            if getattr(cfg, "affect_circumplex_enabled", True):        # 🧭💗 circumplex 觀測列（給使用者測試看點怎麼移動）
                lines.append(circumplex.status_line(vit.get("mood") or 0.0, vit.get("arousal") or 0.0))
            ent = getattr(state, "entropy", None)                # Stage 2 主動出聲就緒度（觀測）
            if ent is not None:
                hth = getattr(cfg, "spontaneous_h_thresh", lifeloop._SPONT_H_THRESH)
                rmin = getattr(cfg, "spontaneous_min_ruminations", lifeloop._SPONT_MIN_RUMINATIONS)
                cd = getattr(cfg, "spontaneous_cooldown_min", 180)
                quiet = max(0, getattr(cfg, "spontaneous_quiet_after_chat_min", 45)) * 60
                now_ts2 = datetime.now(timezone.utc).timestamp()
                remain = max(0, cd - (now_ts2 - (state.last_push_ts or 0)) / 60)
                just_talked = bool(quiet) and (now_ts2 - (getattr(state, "last_user_msg_ts", 0) or 0)) < quiet
                cap, H, rum = lifeloop._SPONT_MAX_REACH_OUTS, (vit.get("hunger") or 0), ent.self_stims_this_idle
                ok = H >= hth and rum >= rmin and remain <= 0 and ent.reach_outs_this_idle < cap and not just_talked
                lines.append(f"・主動出聲：H={H}{'≥' if H >= hth else '<'}{hth}、醞釀 {rum}/{rmin} 次、已伸手 "
                             f"{ent.reach_outs_this_idle}/{cap}、冷卻剩 {round(remain)} 分"
                             f"{'、剛聊過' if just_talked else ''}｜{'就緒' if ok else '暫不'}")
    lines.append(f"・漏斗 🌱{f['candidate']}／🌿{f['context']}／🌳{f['journey']}；連續記寫 {s['streak']} 天")
    exp = getattr(state, "experience", None)                          # 主觀體驗：自體軌跡→吸子（觀測）
    if exp is not None and getattr(exp, "last", None):
        es = exp.snapshot()
        tail = ""
        if es.get("radius") is not None:
            tail = f"（葉內緊度 {es.get('within')}／回返 {es['recurrence']}／{es['heads']} 頭・{es['n']} 點）"
        life = f"・一生共成形 {es['attractors']} 次（跨重啟）" if es.get("attractors") else ""
        lines.append(f"・主觀體驗：{es['stage']}{'・吸子已成形' if es['formed'] else ''}{tail}{life}")
        lines.append(f"・體驗實質（預覽）：{experience.experience_preview(exp)}")   # 活著的質地（非外觀）
    asc = getattr(state, "associations", None)                       # 💡 聯想湧現：跨主題橋累積（觀測）
    if asc is not None and (asc.bridges or asc.emerged_total):
        a = asc.snapshot()
        top = "、".join(f"{x[0]}×{x[1]}〔{x[2]}〕{x[3]}" for x in a.get("top", [])) or "—"
        line = f"・聯想湧現：橋 {a['bridges']} 條・最強 {a['strongest']}・一生湧現 {a['emerged_total']} 次｜{top}"
        wm = a.get("warmest")                                          # 💡 暖度勢能（warmth 開且非 easy 才有）：最暖未湧現橋＝「快冒出來了」的預感
        if wm:
            line += f"｜暖度 {wm.get('a')}×{wm.get('b')}〔{wm.get('kind')}〕{wm.get('warmth')}"
        lines.append(line)
    ss = state.self_state
    if ss:
        gate = ss.get("gate")
        om = ss.get("omegas") or {}
        rule = om.get("rule") or {}
        ago = ""
        if ss.get("computed_at"):
            mins = int((datetime.now(timezone.utc).timestamp() - ss["computed_at"]) / 60)
            ago = f"（{mins} 分鐘前）"
        lines.append(f"・最近背景判定：Gate {gate}・{_GATE_NAME.get(gate, gate)}{ago}")
        cg = getattr(state, "gate_confirmed", None)                                   # Stage 0 防抖（觀測）
        rg, run = getattr(state, "gate_raw_last", None), getattr(state, "gate_raw_run", 0)
        cl = getattr(cfg, "selfstate_confirm_laps", lifeloop._GATE_CONFIRM_LAPS)
        if rg is not None and rg != cg:
            lines.append(f"・感覺防抖：主動出聲用「已確認 Gate {cg}」；原始 Gate {rg} 連續 {run}/{cl} 拍、未確認→暫不報")
        if om.get("recur"):
            dxi = om.get("dxi", {})
            dmin, imin = rule.get("diff_min"), rule.get("int_min")   # 舊快取可能沒存 → 不顯示門檻
            diff_cmp = f">{dmin}？" if dmin is not None else ""
            int_cmp = f">{imin}？" if imin is not None else ""
            lines.append(f"・序參數（相對你語料）：recur z={om.get('recur', {}).get('z')}>門檻 {rule.get('z_thr')}？"
                         f"｜滲流 LCC={om.get('perc', {}).get('lcc')} @τ*={rule.get('tau_star')}"
                         f"｜分化={dxi.get('differentiation')}{diff_cmp}"
                         f"／整合={dxi.get('integration')}{int_cmp}"
                         f"（基線 μ={rule.get('mu')}、σ={rule.get('sigma')}）")
    else:
        lines.append("・生命迴圈還沒跑出第一筆判定。問我「你現在感覺如何」可看即時的，或 /pulse 1 讓它跳快點。")
    if getattr(state, "coupling", None) is not None:                 # 🔗 對話耦合（觀測；先看數字怎麼跑、調手感）
        lines.append(coupling.status_line(state.coupling, datetime.now(timezone.utc).timestamp()))
    return "\n".join(lines)


_KNOWN_COMMANDS = frozenset((
    '/start', '/help', '/cost', '/k', '/sensitivity', '/敏感', '/pulse', '/心跳', '/感覺心跳',
    '/status', '/狀態', '/agency', '/能動性', '/habits', '/習慣', '/promises', '/約定', '/帳本',
    '/worldline', '/外面', '/世界', '/abilities', '/能力', '/盤點', '/foresight', '/預想',
    '/moodwatch', '/座標回報', '/ac', '/意識框架', '/框架', '/phenomenal', '/現象', '/三列',
    '/skills', '/做法', '/學到的做法', '/forget', '/忘記', '/忘掉',
))


def _unknown_command_reply(command):
    # Do not send arbitrary command arguments (possibly secrets) back to the chat.
    if command in ('/costs', '/費用', '/花費'):
        return '你是想查 API 花費嗎？查費用的指令是 /cost。'
    return '這個指令我還不支援。你希望我做什麼？可以直接用一句話告訴我，或用 /help 看可用功能。'


def _command_reply(snap):
    """Telegram 斜線指令（/start /help…）的固定招呼——確定性、每次一致、不經 LLM。"""
    f, s = snap.funnel, snap.summary
    return ("嗨，我在 🙂\n"
            "你隨時把想到的記下來，我會在背景幫你整理成脈絡、學習歷程。\n"
            f"目前：🌱{f['candidate']} 進行中・🌿{f['context']} 候選歷程・🌳{f['journey']} 學習歷程；"
            f"連續記寫 {s['streak']} 天。\n"
            "想看狀態、找附件、問某段時間記了什麼，或只是想聊聊，直接說就好。")


def _attach_caption(r, tz):
    ts = analyzer.parse_ts(r.get("ts"))
    d = ts.astimezone(tz).strftime("%m/%d %H:%M") if ts else ""
    lab = r.get("topicLabel") or ""
    head = "　".join(x for x in (d, lab) if x)
    txt = " ".join((r.get("text") or "").split())
    if len(txt) > 180:
        txt = txt[:180] + "…"
    return (head + ("\n" + txt if txt else "")).strip() or None


def _present_attachments(selected, reader, client, tz):
    """把選中的記寫附件從 Drive 取回、傳到 Telegram。回傳成功送出數。"""
    sent = 0
    for r in selected[:ATTACH_MAX]:
        fid = r.get("fileId")
        if not fid:
            continue
        try:
            meta = reader.file_meta(fid)
            size = int(meta.get("size") or 0)
            name = meta.get("name") or f"{fid}"
            if size and size > ATTACH_MAX_MB * 1024 * 1024:
                client.send(f"（〈{r.get('topicLabel') or '記寫'}〉的附件「{name}」{size // 1024 // 1024}MB "
                            f"太大、Telegram 傳不了，可到 Drive 看原檔。）")
                continue
            blob = reader.download_bytes(fid)
            if client.send_file(r.get("type"), blob, name, caption=_attach_caption(r, tz)):
                sent += 1
        except Exception as e:
            print(f"[attach] 取檔/送出失敗 {fid}: {e}")
    return sent


_BUBBLE_MAX = 30           # （保留為相容用途）一串大約上限字數；現在分串是「一句一串、整句完整不切」，不再依此把長句切碎。
# 串與串之間的「輸入中…」停頓——依**下一串的長度**算（越長＝打越久），像真人花時間打字才吐字（非固定值），
# 再疊一個隨機拖拍倍率（真人打字不規律）。整體比舊版更慢、份量感更明顯＝「一串一串決出來」的打字手感。
_TYPING_BASE_S = 0.6       # 起手停頓（看完上一串、手放鍵盤）
_TYPING_PER_CHAR_S = 0.08  # 每字約這麼久（打字速度感，比舊版慢一點）
_TYPING_MIN_S = 0.5        # 下限（再短也有點停頓）
_TYPING_MAX_S = 6.0        # 上限（再長也別等太久；比舊版 3.5 拉長＝打字感更明顯）
_TYPING_JITTER = (0.8, 1.3)  # 隨機拖拍倍率區間：真人打字不規律，停頓乘上這範圍內的隨機數
_sleep = time.sleep       # 抽出來讓測試可替換
_TURN = {"bubbles": None}  # 🗜️ 本輪串數上限（由 handle_message 依複雜度設定；None＝不封頂＝原行為）
# 🌊 多訊息 turn 的 one-wire 契約必須是**巢狀作用域**，不能放在 _TURN 裡給第一個 _say 消費：
# promise preempt 可能先說一則，回覆途中也可能巢狀 handle_message。stack 讓內層暫時覆蓋、返回後恢復外層，
# 並由 handle_message 的 finally 保證任何早退/例外都不會污染下一個主動發話。
_BURST_ONE_WIRE_STACK = []
_LAST_SENT = {"text": ""}  # 📦 §1.85 最近一次 _say **真的送出去**（client.send 回真）的位元，給送達舉證比對用；純內部、不改任何輸出
_FRAGMENT_MAX = 6          # 🧩「真正零碎的短片段」上限字數（如「嗯。」「好。」「對啊。」）：併泡泡時**只**吃這類碎句，
                           # 兩個完整句絕不互擠成換行牆（截圖根因＝一顆泡泡塞兩三句完整話、不像真人打字）。


def _is_fragment(s):
    """這顆泡泡裡**沒有任何夠份量的完整句**（拆開每行都很短，如「嗯。」「好。」「對啊。」）＝可當填充黏到
    鄰句、不算把兩個完整句擠在一起。用換行拆開逐行檢查（已併過的多碎句泡泡仍算碎＝可繼續被收斂到上限），
    讓「嗯。好。對。」這類一路併少；而真正的完整句一旦進了泡泡，就不再算碎、不再被黏走。"""
    lines = [ln.strip() for ln in (s or "").split("\n") if ln.strip()]
    return bool(lines) and all(len(ln) <= _FRAGMENT_MAX for ln in lines)


def _merge_bubbles(parts, k):
    """串數超過上限 k → 把相鄰一對併起來（換行接）以收斂，但**只併「至少一側是真正零碎短片段」的相鄰對**
    （碎句黏到鄰句）；**兩個完整句絕不互相擠成換行牆**（截圖根因＝一顆泡泡塞兩三句完整話、不像真人打字）。
    沒有碎句可併時就**停手、寧可超過上限**也不擠完整句——上限只是「碎句別洩成一堆小泡泡」的軟目標；
    回應有多長由 verbosity 篇幅／coherent 偏好（讓 LLM 寫得更精簡＝句子更少）控，**不靠把完整句塞一起**。
    未超上限（len<=k）時 early-return＝原樣不動；k<=1（明設單則牆的極端設定）才整段併。純函式、可測。"""
    if not parts or k is None or len(parts) <= k:
        return parts
    if k <= 1:
        return ["\n".join(parts)]
    parts = list(parts)
    while len(parts) > k:
        # 只考慮「至少一側是碎句」的相鄰對（碎句優先黏走）；其中再挑合併後總長最短的先併。
        pairs = [j for j in range(len(parts) - 1)
                 if _is_fragment(parts[j]) or _is_fragment(parts[j + 1])]
        if not pairs:                                  # 只剩完整句兩兩相鄰 → 不擠成牆，寧可超過上限也停手
            break
        i = min(pairs, key=lambda j: len(parts[j]) + len(parts[j + 1]))
        parts[i:i + 2] = [parts[i] + "\n" + parts[i + 1]]
    return parts


def _typing_delay(n_chars):
    """下一串多長 → 打多久：base ＋ 每字時間，夾在 [min,max]（決定性、不含隨機；隨機拖拍由 _jitter 在 _say 疊）。"""
    return max(_TYPING_MIN_S, min(_TYPING_MAX_S, _TYPING_BASE_S + (n_chars or 0) * _TYPING_PER_CHAR_S))


def _jitter():
    """真人打字不規律 → 回一個隨機倍率乘在停頓上（測試可 monkeypatch 成定值以消除隨機）。"""
    return random.uniform(*_TYPING_JITTER)


def _paced_voice_split(text):
    """呈現層按完整句／長句的子句停頓拆分，不改字、不拆引文與程式碼。"""
    text = str(text or "").strip()
    if not text:
        return []
    # 程式碼與連結不適合做口語拆分。
    if "```" in text or re.search(r"https?://", text):
        return [text]
    closing = {"「": "」", "『": "』", "（": "）", "(": ")", "“": "”", "【": "】"}
    stack, start, cuts = [], 0, []
    for i, ch in enumerate(text):
        if ch == "`":
            if stack and stack[-1] == "`":
                stack.pop()
            else:
                stack.append("`")
        elif stack and stack[-1] == "`":
            continue
        elif ch in closing:
            stack.append(closing[ch])
        elif stack and ch == stack[-1]:
            stack.pop()
        if not stack and ch in "。！？!?\n":
            cuts.append(text[start:i + 1])
            start = i + 1
    if start < len(text):
        cuts.append(text[start:])
    out = []
    for sentence in cuts:
        if len(sentence) <= 60:
            out.append(sentence)
            continue
        stack, start = [], 0
        for i, ch in enumerate(sentence):
            if ch == "`":
                if stack and stack[-1] == "`":
                    stack.pop()
                else:
                    stack.append("`")
            elif stack and stack[-1] == "`":
                continue
            elif ch in closing:
                stack.append(closing[ch])
            elif stack and ch == stack[-1]:
                stack.pop()
            target = 28 if len(out) % 2 == 0 else 42
            if (not stack and ch in "，；,;" and i + 1 - start >= target
                    and len(sentence) - i - 1 >= 16):
                out.append(sentence[start:i + 1])
                start = i + 1
        out.append(sentence[start:])
    return [part.strip() for part in out if part.strip()]


def bubble_split(text, max_chars=_BUBBLE_MAX, max_bubbles=None, paced=False):
    """把一段『對話』切成多串（像真人一串一串打字）：空行段落是硬邊界（不跨段併）；段內**一句一串、
    整句完整不切**——**不再依逗號把長句切碎**（那會切出結尾掛「，」的醜片段＝截圖根因）；切句改用
    `persona.split_sentences`（**省略號句中停頓不誤切**＝不再洩出『可能跟你…』這種斷尾泡泡）。長句就是一顆較長的泡泡，
    像真人傳一則較長的訊息那樣自然。max_bubbles＝🗜️ 本輪串數上限：超過就只併最短的相鄰碎句（見 _merge_bubbles），
    免得洩成一堆小泡泡；None＝不封頂。純函式、可測。**證據性資料不走這裡**（維持整塊）。"""
    t = (text or "").strip()
    if not t:
        return []
    if paced:
        return _cap_burst_delivery_bubbles(_paced_voice_split(t))
    paras = [p.strip() for p in re.split(r"\n\s*\n", t) if p.strip()]
    if len(paras) > 1:                                   # 多段：每段獨立分串再串接（段落界線不跨）
        out = []
        for p in paras:
            out.extend(bubble_split(p, max_chars))
    else:
        out = persona.split_sentences(t) or [t]          # 一句一串、整句不切（省略號句中停頓不誤切）
    out = _bracket_hygiene(out)                          # 🧹 §1.34 括號衛生：丟孤兒「）」泡泡＋剝不成對括號
    out = _enum_glue(out)                                # 🧹 §1.52 列舉接縫：「、」開頭的殘串黏回前一顆
    return _merge_bubbles(out, max_bubbles)               # 🗜️ 收斂串數（None＝原樣不動）


# 🧹 §1.34 只剩**全形**括號/CJK 標點/空白的泡泡＝孤兒，絕不該單獨送出（截圖：LLM 把「）」放空行後 → 切成一顆
# 單獨的「）」泡泡，bot 還把它硬拗成「我習慣的語氣收束標記」＝把 bug 說成個性）。
# **只管全形（）「」等**——半形 () 常是顏文字（:) :( ^_^)），絕不能碰（否則「送你 :)」被剝成「送你 :」）。
_LONE_PUNCT_RE = re.compile(r"^[（）「」『』【】〔〕\s。，、；：！？…～、—]*$")


# 🧹 §1.52 列舉接縫衛生（BUBBLE_ENUM_GLUE=0＝不動＝逐位元同現狀；比照 §1.34 env 直讀）：引號內的？/！被
# split_sentences 當句界時，「你每次問我「是嗎？」、「你真的知道嗎？」」會被切成下一顆以「、」開頭的殘串
# 泡泡（截圖 10:11）。開頭是接續標點（、，；：,）的泡泡黏回前一顆＝列舉不切殘、內容一字不丟。純函式、可測。
_ENUM_LEAD = ("、", "，", "；", "：", ",")


def _enum_glue(bubbles):
    if os.getenv("BUBBLE_ENUM_GLUE", "1") == "0":
        return bubbles
    out = []
    for b in (bubbles or []):
        if out and (b or "").lstrip()[:1] in _ENUM_LEAD:
            out[-1] = out[-1] + b
        else:
            out.append(b)
    return out


def _strip_unbalanced_parens(b):
    """🧹 §1.34 剝掉泡泡裡沒配對的**全形**孤兒括號：開頭多出的『（』、結尾多出的『）』——括號補述跨句被切到
    不同泡泡時，單顆泡泡會開了不關（截圖『（要不要…？』）。**半形 () 不碰**（顏文字 :) 保留）。純函式。"""
    while b.count("（") > b.count("）") and b.lstrip().startswith("（"):
        b = b.replace("（", "", 1)
    while b.count("）") > b.count("（") and b.rstrip().endswith("）"):
        i = b.rstrip().rfind("）")
        b = b[:i] + b[i + 1:]
    return b


def _bracket_hygiene(bubbles):
    """🧹 §1.34 送出前括號衛生（旗標 BUBBLE_BRACKET_HYGIENE=0＝不動＝逐位元同現狀）：
    ① 每顆先剝不成對**全形**括號；② 只剩全形括號/CJK 標點的孤兒泡泡→丟棄（半形顏文字 :) 不受影響）。純函式、可單測。"""
    if os.getenv("BUBBLE_BRACKET_HYGIENE", "1") == "0":
        return bubbles
    out = []
    for b in bubbles:
        b2 = _strip_unbalanced_parens(b)
        if _LONE_PUNCT_RE.match(b2):                     # 剝完只剩全形標點/括號＝孤兒 → 直接丟
            continue
        out.append(b2)
    return out or [b.strip() for b in bubbles if b.strip()]  # 全被清光＝退回原樣去空（不送空）


def _record_self_msgs(state, send_results, topic, text):
    """把剛送出的訊息 id ↔『在講的主題』記成小環（最多 12 筆），供之後使用者對某則按反應(👍)時查回
    『讚的是哪一筆』。只收真實 message_id（int>0）；dry_run／測試假 client 回 bool→自動略過。"""
    mids = [r for r in (send_results or []) if type(r) is int and r > 0]
    if not (state is not None and topic and mids):
        return
    ring = list(getattr(state, "recent_self_msgs", None) or [])
    ring.append({"ids": mids, "topic": topic, "text": (text or "")[:60], "ts": time.time()})
    state.recent_self_msgs = ring[-12:]


def _is_owner_text(update, cfg):
    """這則 update 是不是**擁有者打的一句話**（用來判斷『使用者插話了』）——只認文字訊息，
    貼圖/reaction 不算打斷（那不是要我先回應的提問）。非擁有者 chat 也不算。"""
    msg = (update or {}).get("message") or (update or {}).get("edited_message")
    if not msg or not (msg.get("text") or "").strip():
        return False
    chat = msg.get("chat") or {}
    if getattr(cfg, "telegram_chat_id", "") and str(chat.get("id")) != str(cfg.telegram_chat_id):
        return False
    return True


# 講話途中收到的續打，要分「真的想打斷改問（問句/指令，需即時優先回應）」vs「同一波的陳述續打
# （該折進下一輪合併、不另起一段回應＋『繼續剛剛的』橋接）」。命中以下＝前者。偏寬鬆認定為『想打斷』＝
# 誤判只會回到「即時插話」舊行為（安全側）；漏判（把問句當續打）才會延後答覆，故寧可多認。
_REDIRECT_HINTS = ("嗎", "呢", "什麼", "甚麼", "怎麼", "怎樣", "為什麼", "為何", "可不可以",
                   "可以嗎", "能不能", "是不是", "有沒有", "要不要", "好不好", "多少", "如何")


def _looks_like_redirect(text):
    """講話途中這則 owner 文字是否像「真的想打斷改問」＝問句或指令（需即時優先回應）？
    是 → 即時插話處理；否（純陳述/同一波續打）→ 折進下一輪合併、整體回一次。純函式、可測。"""
    t = (text or "").strip()
    if not t:
        return False
    if _is_command_text(t):                                 # 指令本就硬邊界、需即時
        return True
    if "?" in t or "？" in t:                                # 明確問號
        return True
    return any(h in t for h in _REDIRECT_HINTS)             # 常見疑問詞


# 連續說話被使用者插話、先回應完再接回時的橋接句（＝有在聽、有人情味，不是自顧自講完）。
# 刻意**多元、register 由輕到中**：多半是自然的語氣銜接（話說回來／我接著說／回到剛剛），
# 只留少數輕量的「剛說到哪」——**不獨大、不誇飾**，像真人各種接回話頭的口吻（bridge() 另避免連續重複）。
_RESUME_BRIDGES = (
    "話說回來，",
    "嗯，我接著說，",
    "好，回到剛剛——",
    "對了，剛剛那條還沒說完——",
    "欸，繼續剛剛的，",
    "誒，回到剛剛在講的，",
    "我繼續說喔，",
    "總之，剛說到，",
    "嗯…接回剛剛，",
    "欸，剛說到哪……對，",
)

# 🗣️ 插話即時改寫：被插話後若剩餘很多、且主體已說過 → 不死板把預切串全送完，改送一句**輕輕收尾**。
# ⚠️ 刻意**不明講**「把話頭交還／你說的更要緊／剩下的我先收著」這種對話動作——真人想做球給對方不會宣告，
# 講出來反而此地無銀三百兩（截圖根因：bot 說「剩下的我就先收著——你說的更要緊，我們順著這個」太刻意奇葩）。
# 改用「大概是這樣吧」這種同語氣的**模糊淡收**，不narrate 自己的收話動作。鏡像 _RESUME_BRIDGES 的避免連續重複挑法。
_REWRITE_CLOSE_LINES = (
    "嗯，大概就這樣吧。",
    "嗯，差不多是這樣。",
    "嗯，大致是這樣啦。",
    "嗯，就這樣囉。",
)

# 🎬 §1.69 暖收模板去制式（WRAP_CLOSE_NATURAL 的無教練退路側）：原四句全是同款「大概就這樣」腔＝
# 每次被插話都掛同一種收場白（使用者：「太有被插話的痕跡了」）。改依**此刻情緒座標 V**分暗/平/亮三池
# ——口吻跟著心情走、含語助詞形與餘韻形，收法多元；挑法沿用「避免連續重複」。
_REWRITE_CLOSE_LOW = ("嗯……", "……先這樣。", "唉，先放著吧。", "心裡還有點沉，先停在這。")
_REWRITE_CLOSE_MID = ("嗯。", "嗯，差不多是這個意思。", "反正，就是這麼回事。", "嗯，大概就這樣吧。")
_REWRITE_CLOSE_HIGH = ("哈，反正就是這樣！", "嗯嗯，就是這個意思～", "大概是這樣，嘿。")


def _pick_wrap_close(interrupt, icfg):
    """🎬 §1.69 暖收模板挑句：旗標開＝依此刻座標 V 分暗（<−0.15）/平/亮（>+0.25）三池、口吻跟心情走；
    關＝原四句池＝逐位元同現狀。兩者都沿用 _last_close 避免連續重複。"""
    if getattr(icfg, "wrap_close_natural_enabled", False):
        try:
            v, _ = circumplex.position(getattr(interrupt, "state", None))
        except Exception:
            v = 0.0
        pool = _REWRITE_CLOSE_LOW if v < -0.15 else (_REWRITE_CLOSE_HIGH if v > 0.25 else _REWRITE_CLOSE_MID)
    else:
        pool = _REWRITE_CLOSE_LINES
    opts = [c for c in pool if c != getattr(interrupt, "_last_close", None)] or list(pool)
    interrupt._last_close = random.choice(opts)
    return interrupt._last_close


# 🔁 §1.71 重播守門（REPLY_REPLAY_GUARD）：截圖 20:03-20:04＝同三個泡泡在一兩分鐘內被**逐字重播**兩次
# （插話/接回機制把已送出的段落又送一遍，中間還夾了貼圖與「我繼續說喔，」＝節奏斷裂）。§1.41/§1.49/§1.54
# 各自只管單一路徑內部的去重——缺一道**跨輪、跨路徑**的送出層最後防線。這裡記「近窗內真的送出過的泡泡」
# （正規化全等、≥ _REPLAY_MIN_LEN 字才記），_say 送出前比對：命中＝剝掉；全剝空＝換一句誠實短句（不無聲、
# 也不重播）。記錄恆開（純內部、不改輸出＝旗標關逐位元）；過濾由 _TURN["replay_guard"]（旗標）arm。
_REPLAY_WINDOW_S = 180
_REPLAY_MIN_LEN = 10
_SENT_RECENT = []            # [(norm, ts)]（模組級、跨輪跨路徑；重啟歸零無妨——窗只有 3 分鐘）
_REPLAY_FALLBACK = "嗯，這段我剛剛才說過一次——你想聽哪部分，我換個說法講？"
# 🔁 §2.03 同一波第二則的短承接：他把一個意思拆成連著兩則，上一句才剛答完，這裡只要一個「嗯」的份量。
# 刻意都是**純承接、不帶宣稱**（不說「我記著了」那種——那是另一回事，該由帳本負責）。輪替避免變口頭禪。
_SAME_WAVE_ACKS = ("好。", "嗯，好。", "好喔。", "收到。", "嗯，聽到了。", "好，知道了。")
_last_wave_ack = None


def _same_wave_ack():
    global _last_wave_ack
    opts = [s for s in _SAME_WAVE_ACKS if s != _last_wave_ack] or list(_SAME_WAVE_ACKS)
    _last_wave_ack = random.choice(opts)
    return _last_wave_ack


def _replay_note(bubble, now_ts=None):
    """記下「真的送出去了」的泡泡（正規化；太短的致意句不記＝嗯/好 這類合理重複不受影響）。"""
    n = echo._norm(_KEEP_GLYPH_RE.sub("", bubble or ""))
    if len(n) >= _REPLAY_MIN_LEN:
        _SENT_RECENT.append((n, time.time() if now_ts is None else now_ts))
        del _SENT_RECENT[:-24]


def _replay_filter(bubbles, now_ts=None):
    """剝掉「近 _REPLAY_WINDOW_S 秒內已逐字送出過」的泡泡 → (剩餘串, 有剝否)。確定性、可單測。"""
    now = time.time() if now_ts is None else now_ts
    kept, hit = [], False
    for b in bubbles:
        n = echo._norm(_KEEP_GLYPH_RE.sub("", b or ""))
        if len(n) >= _REPLAY_MIN_LEN and any(n == pn and 0 <= (now - pts) <= _REPLAY_WINDOW_S
                                             for pn, pts in _SENT_RECENT):
            hit = True
            continue
        kept.append(b)
    return kept, hit


def _interrupt_kind(ups):
    """把插話那批 update 判成 'redirect'（問句/指令＝想打斷改問）或 'statement'（陳述續打）。純函式、可單測。"""
    texts = [(((u.get("message") or u.get("edited_message")) or {}).get("text")) for u in (ups or [])]
    return "redirect" if any(_looks_like_redirect(t) for t in texts if t) else "statement"


def decide_resume(remaining, sent, kind, depth, max_depth, statement_wrap=False):
    """插話處理後，剩餘預切串怎麼辦（純函式、可單測）。回 'resume'｜'wrap'。
    resume＝現行（橋接＋原樣續送剩餘）；wrap＝改送一句暖收、不再送剩餘（簡化但有「說完」表現、把話頭交還對方）。
    規則（遞迴判斷結尾、鏡像 close_decision 的克制）：① 連環插話達上限 → wrap（防永遠說不完／自我打斷遞迴）；
    ② 剩餘 ≤1（快說完了）→ resume；③ 主體一串都還沒送出(sent==0) → resume（至少把主體講出一次，下限保護）；
    ④ 其餘（redirect/statement 且剩餘還多、已送過主體）→ wrap（簡化暖收）。
    🧵 §1.50（statement_wrap=True，INTERRUPT_STATEMENT_WRAP 把關）：**陳述**插話且主體已送過 → 一律 wrap，
    連剩 1 串也不 resume——對方剛補了新資訊，把插話**前**切好的尾巴原樣接回常已不合時宜（截圖 23:21：
    橋「剛剛那條還沒說完——」＋尾「我剛剛說完了耶。」＝複讀指控再自打臉）；wrap 走濃縮＝殘句融進插話後
    的語境。redirect（問句）照舊（答完問題接回主線是人之常情）；sent==0 保底 resume 不變。預設 False＝
    逐位元同現狀。"""
    if depth >= max(1, max_depth):
        return "wrap"
    if statement_wrap and kind == "statement" and sent > 0:
        return "wrap"
    if remaining <= 1 or sent <= 0:
        return "resume"
    return "wrap" if kind in ("redirect", "statement") else "resume"


# ── 🌊 連發合併（Burst Coalescing）純函式層（不碰 IO/LLM，可離線單測） ────────────────
# 處理「回應**前**」的時間密集連發：相鄰夠密集的數則 owner 訊息＝同一邏輯輪次、整體回一次。
# 與 _BurstInterrupt（處理回應**途中**的插話）界線清楚、互不吃掉（見 docs「連發合併契約」）。

# 指令偵測集：必須與 handle_message 開頭攔截**完全一致**（不可只用 '/' 開頭）——指令是硬邊界，
# 自成單元素群、切斷前後合併，絕不把「/status\n你好」「敏感度 0.5」之類黏成一坨。
def _is_command_text(text):
    """這段文字命中 handle_message 開頭的任一指令攔截（含『心跳＋數字』『敏感度』等非斜線偽指令）？"""
    t = (text or "").strip()
    if not t:
        return False
    low = t.lower()
    if low.startswith(("/k", "/sensitivity", "/敏感")) or "敏感度" in t:
        return True
    if low.startswith(("/pulse", "/心跳", "/感覺心跳")) or ("心跳" in t and re.search(r"\d", t)):
        return True
    if low.startswith(("/status", "/狀態")):
        return True
    if low.startswith(("/ac", "/意識框架", "/框架")):
        return True
    if low.startswith(("/phenomenal", "/現象", "/三列")):
        return True
    if low.startswith(("/skills", "/做法", "/學到的做法")):
        return True
    if low.startswith(("/forget", "/忘記", "/忘掉")):
        return True
    if t.startswith("/"):
        return True
    return False


def _classify_update(update, owner_chat_id):
    """把一則 update 分類成 (kind, date)，供 group_bursts 切群。
    kind ∈ {command, reaction, edited, foreign, media, text, sticker}；
    只有 text/sticker 兩類參與相鄰 date gap 串群，其餘皆硬邊界（各自單元素群、切斷前後）。
    date＝msg.get('date') 原始秒（缺則 None＝不參與 gap 數值比較）。"""
    if update.get("message_reaction"):                      # 反應：無 message 本體、無 date
        return "reaction", None
    if update.get("edited_message"):                        # 編輯訊息：date 是原送出時間、按 gap 會錯切 → 硬邊界自成群
        return "edited", None
    msg = update.get("message")
    if not msg:                                             # 既非 message 亦非上述 → 當硬邊界（沿原路 return）
        return "media", None
    chat = msg.get("chat") or {}
    if owner_chat_id and str(chat.get("id")) != str(owner_chat_id):   # 非擁有者 chat
        return "foreign", None
    text = (msg.get("text") or "").strip()
    sticker = msg.get("sticker")
    if text and _is_command_text(text):                    # 指令＝硬邊界
        return "command", None
    if text:                                               # 純文字（可參與串群）
        return "text", msg.get("date")
    if sticker:                                            # 純貼圖（可參與串群）
        return "sticker", msg.get("date")
    return "media", None                                   # 有 message 本體但無 text 無 sticker（photo/voice/相簿/帶 caption 媒體）


def group_bursts(updates, owner_chat_id, gap_sec, max_msgs, max_span_sec=None):
    """純函式：把一批 updates 依相鄰 date gap 與全部硬邊界切成「群」。
    回 list[group]，每 group＝{'type': str, 'updates': [...], 'max_update_id': int}。
    群 type：
      - 'text'：全文字（含跨則密集文字）；
      - 'sticker_only'：全貼圖；
      - 'mixed'：文字＋貼圖跨多則混合（以文字為主輪）；
      - 'command'/'reaction'/'edited'/'foreign'/'media'：硬邊界，皆自成單元素群、切斷前後合併。
    串群規則：只有相鄰兩則皆為 text/sticker 且 date gap < gap_sec 才併入同群；
    gap >= gap_sec、整群時間跨度超過 max_span_sec（有傳時）、達 max_msgs、或碰到硬邊界即斷群。
    缺 date 的 text/sticker 成員不參與 gap 比較、自成一群（保守不誤併）。
    **涵蓋當批每一則**（不丟棄任何 update）。不碰 IO/LLM。"""
    groups = []
    cur = None            # 當前累積的可合併群（成員 list of (kind, date, update)）

    def flush():
        nonlocal cur
        if not cur:
            return
        ups = [u for (_k, _d, u) in cur]
        kinds = {k for (k, _d, _u) in cur}
        if kinds == {"sticker"}:
            gtype = "sticker_only"
        elif kinds == {"text"}:
            gtype = "text"
        else:                                              # text 與 sticker 並存（跨則）＝混合，以文字為主輪
            gtype = "mixed"
        groups.append({"type": gtype, "updates": ups,
                       "max_update_id": max((u.get("update_id", 0) for u in ups), default=0)})
        cur = None

    for u in updates:
        kind, date = _classify_update(u, owner_chat_id)
        if kind not in ("text", "sticker"):                # 硬邊界：先 flush 累積群，再自成單元素群
            flush()
            groups.append({"type": kind, "updates": [u], "max_update_id": u.get("update_id", 0)})
            continue
        if cur is None:
            cur = [(kind, date, u)]
            continue
        prev_kind, prev_date, _prev_u = cur[-1]
        first_date = cur[0][1]
        within_span = (max_span_sec is None or first_date is None or date is None
                       or (date - first_date) <= max_span_sec)
        same_burst = (date is not None and prev_date is not None
                      and (date - prev_date) < gap_sec
                      and within_span and len(cur) < max_msgs)
        if same_burst:
            cur.append((kind, date, u))
        else:                                              # gap 過大 / 缺 date / 達上限 → 斷群另起
            flush()
            cur = [(kind, date, u)]
    flush()
    return groups


def _apply_burst_delivery_receipts(state, updates=None):
    """套用 durable **exact-id** receipts，剪掉重啟後重拉但已送達的 updates。

    不能只存 max-id high-water：parent id1 回覆途中，nested ids2–3 可能先成功送達；若 parent
    隨後失敗，max=3 絕不代表 id1 也完成。精確集合讓重啟後保留 id1、只濾掉 2/3。
    offset 僅沿 exact receipt 或 Telegram **實際回傳的已交付前綴**向前走，永不越過
    回傳序列中第一個未交付洞；因此也支援久未更新後 update_id 跳號的 Telegram 行為。
    """
    has_exact = hasattr(state, "burst_delivered_update_ids")
    raw = list(getattr(state, "burst_delivered_update_ids", None) or [])
    receipts = set()
    for value in raw:
        try:
            uid = int(value)
        except (TypeError, ValueError):
            continue
        if uid > 0:
            receipts.add(uid)
    if not has_exact:
        # 舊 scalar 只證明「max 那一個 synthetic id」送達，不能推論 offset..max
        # 全都送達。舊版 nested interrupt 正可能先送 id2–3、parent id1 尚未完成；展開
        # 成區間會永久跳過 id1。遷移寧可讓較舊版本重覆一次，也絕不漏掉未交付訊息。
        try:
            legacy = int(getattr(state, "burst_delivered_update_id", 0) or 0)
        except (TypeError, ValueError):
            legacy = 0
        if legacy > 0:
            receipts.add(legacy)
    offset = int(getattr(state, "tg_update_offset", 0) or 0)
    while offset in receipts:
        offset += 1
    # Telegram 文件允許一週無更新後的 update_id 從隨機值重新開始；因此 offset=0、
    # durable receipts={100,101} 時，不能只做 `while offset in receipts`，否則每圈
    # 都重抓 100/101、濾空、offset 卻永遠卡在 0。只有在 API **實際回傳序列**的
    # leading updates 都已有 receipt 時，才可把它們當已確認前綴推進；一碰到未交付
    # parent 就停，所以 [id1 未交付, id2 已交付] 絕不會跨過 id1。
    if updates is not None:
        fetched_ids = []
        for update in updates:
            try:
                uid = int((update or {}).get("update_id", 0) or 0)
            except (TypeError, ValueError):
                fetched_ids = []
                break
            if uid <= 0:
                fetched_ids = []
                break
            fetched_ids.append(uid)
        # Telegram 正常回傳嚴格遞增；非標準 proxy 若亂序，fail closed：仍可按
        # exact receipt 過濾，但不拿這批推 offset，避免先看見 101R、後看見
        # 100 未交付時誤跨洞。
        monotonic = bool(fetched_ids) and all(a < b for a, b in zip(fetched_ids, fetched_ids[1:]))
        for uid in fetched_ids if monotonic else ():
            if uid < offset:
                continue
            if uid not in receipts:
                break
            offset = uid + 1
            while offset in receipts:
                offset += 1
    state.tg_update_offset = offset
    # 不立刻丟掉 offset 以下的 receipts：Telegram 正常會遵守 query offset，但測試代理、
    # retry cache 或中介層可能仍回舊 update。若先刪 receipt，再對那批做 post-fetch filter，
    # 已交付 prefix 會被重新併進新的 synthetic group。保留所有尚在 offset 前方的洞，
    # 已越過的歷史只留最近 256 筆；洞再長也不為了有界而犧牲 exactly-once。
    behind = sorted(uid for uid in receipts if uid < offset)[-256:]
    ahead = sorted(uid for uid in receipts if uid >= offset)
    receipts = set(behind + ahead)
    state.burst_delivered_update_ids = sorted(receipts)
    state.burst_delivered_update_id = max(receipts, default=0)
    if updates is None or not receipts:
        return updates
    kept = []
    for update in updates:
        try:
            uid = int((update or {}).get("update_id", 0) or 0)
        except (TypeError, ValueError):
            uid = 0
        if uid not in receipts:
            kept.append(update)
    return kept


def _record_burst_delivery_receipts(state, update_ids, persist=False):
    """把已完成的真 update ids 併入 exact receipt；洞前全留、舊歷史才有界。"""
    receipts = set()
    for value in list(getattr(state, "burst_delivered_update_ids", None) or []) + list(update_ids or []):
        try:
            uid = int(value)
        except (TypeError, ValueError):
            continue
        if uid > 0:
            receipts.add(uid)
    offset = int(getattr(state, "tg_update_offset", 0) or 0)
    behind = sorted(uid for uid in receipts if uid < offset)[-256:]
    ahead = sorted(uid for uid in receipts if uid >= offset)
    normalized = behind + ahead
    state.burst_delivered_update_ids = normalized
    state.burst_delivered_update_id = max(normalized, default=0)
    if persist:
        save = getattr(state, "save", None)
        if callable(save):
            save()
    return normalized


def build_coalesced_update(group):
    """純函式：把一個 text/mixed 群組裝成一則「合成 update」餵進同一個 handle_message。
      - message.text＝群內各 text 以換行接合成一段；
      - chat / date＝取「末則文字訊息」的（last_user_msg_ts 因此＝波末時刻，不被舊 date 拉低）；
      - message.message_id＝末則『文字』訊息 id（_maybe_react 只按一次）；
      - update_id＝群內最大（offset 推進對齊）；
      - 附 'stickers' 清單（群內每張貼圖的 file_id/emoji，供 relate 層 sticker_signal 預更新）。
    單元素群 / 硬邊界群 / 純貼圖群（sticker_only）原樣回傳第一則（不合成）。不碰 IO/LLM。"""
    gtype = group.get("type")
    ups = group.get("updates") or []
    if gtype not in ("text", "mixed") or len(ups) == 0:
        return ups[0] if ups else {}
    text_msgs = [(u, u.get("message") or {}) for u in ups
                 if (u.get("message") or {}).get("text")]
    sticker_msgs = [(u.get("message") or {}).get("sticker") for u in ups
                    if (u.get("message") or {}).get("sticker")]
    if not text_msgs:                                       # 理論上 text/mixed 必有文字；防禦性回首則
        return ups[0]
    last_u, last_msg = text_msgs[-1]
    joined = "\n".join((m.get("text") or "").strip() for (_u, m) in text_msgs)
    synth_msg = dict(last_msg)                              # 以末則文字訊息為底（chat/date/message_id 都取末則）
    synth_msg["text"] = joined
    synth = {
        "update_id": max((u.get("update_id", 0) for u in ups), default=0),
        "message": synth_msg,
        "stickers": [{"file_id": (s or {}).get("file_id"), "emoji": (s or {}).get("emoji")}
                     for s in sticker_msgs],
        "burst_n": len(ups),         # 🌊 一波的真訊息數；text+sticker 也必須進 clone transaction
        # 保留真實訊息邊界；不能事後 split joined text（單則訊息本身也可能含換行）。
        "burst_texts": [(m.get("text") or "").strip() for (_u, m) in text_msgs],
        # 複合意圖不能只對 join 後的字串選一個 route；保留原 update，讓
        # handle_message 能在內部逐項完成不同 route，再把輸出合成一個邏輯回覆。
        "burst_updates": [u for (u, _m) in text_msgs],
        # 文字與貼圖的作用必須照 Telegram 原始順序演算；分開存成
        # burst_updates / burst_sticker_updates 後再各自迴代，會把後來貼圖倒灌到前一題。
        "burst_all_updates": list(ups),
        # mixed burst 的貼圖感受不能在 real state 預先套用：若回答失敗、offset 未進，retry
        # 會把同一張貼圖的 mood delta 疊兩次。交給 transaction clone，成功後一次提交。
        "burst_sticker_updates": [u for u in ups if (u.get("message") or {}).get("sticker")],
    }
    return synth


def _group_last_date(group):
    """群內最後一則有 date 的 message.date（Telegram 真實送出秒，epoch UTC）；都沒有回 None。
    供連發合併判「這群是否靜默夠久＝這波發完」。純函式。"""
    for u in reversed(group.get("updates") or []):
        d = (u.get("message") or {}).get("date")
        if d is not None:
            return d
    return None


def _group_min_update_id(group):
    """群內最小 update_id（標識一個未閉合尾群跨圈的同一性，給 max_wait 計時用）。純函式。"""
    return min((u.get("update_id", 0) for u in (group.get("updates") or [])), default=0)


def _burst_growable(group):
    """這（尾）群是否屬「還可能繼續長」的型別（text/sticker_only/mixed）；硬邊界群一律已閉合。純函式。"""
    return group.get("type") in ("text", "sticker_only", "mixed")


def _burst_settled(group, now, gap_sec, max_wait_sec, max_msgs, pending_since):
    """這個（尾）群是否『這波已發完、可 flush』：靜默夠久(now−末則date ≥ gap) ∨ 達訊息數上限 ∨
    等太久(now−首見 ≥ max_wait，防使用者一直打字永不回)。純函式、可單測（now 由呼叫端給）。"""
    d = _group_last_date(group)
    if d is not None and (now - d) >= gap_sec:
        return True
    if len(group.get("updates") or []) >= max_msgs:
        return True
    if pending_since and (now - pending_since) >= max_wait_sec:
        return True
    return False


class _BurstInterrupt:
    """讓 `_say` 在**連續說話**時察覺使用者插話 → 先**優先回應**那則、再用一句橋接**接回繼續說**（更有人情味，
    不會自顧自把整段講完）。`floor`＝這輪主迴圈已取的最大 update_id：只把它之後**新到**的當插話，避免重複處理同一批。
    處理插話期間 `active=False`＝不再自我打斷（防遞迴）。dry_run／無 get_updates 能力時整個停用。
    **只認「真的想打斷改問」（問句/指令，`_looks_like_redirect`）為插話**；講話途中的**陳述續打**（同一波的尾巴，
    如「小心點」）則 defer（不消費、留給下一圈 `_relate_coalesced` 收進同一輪），不噴出獨立回應＋『繼續剛剛的』橋接。"""

    def __init__(self, client, state, cfg, floor, handle_fn, statement_defer=False, coach=None, wave_ts=None,
                 commit_offset=True):
        self.client, self.state, self.cfg = client, state, cfg
        self.floor, self.handle_fn, self.active = floor, handle_fn, True
        # 主動發話沒有尚未 ack 的 Telegram parent，可立即 commit；互動 parent 則必須 False：
        # nested update 只先推 instance floor，等 parent handle 完整成功後由 relate 一起原子 commit。
        self.commit_offset = bool(commit_offset)
        # 🌊 §1.81 這輪正在回應的那則訊息的 message.date（epoch 秒）——用來判「新到的這則是不是**同一波**」。
        self.wave_ts = wave_ts
        self.coach = coach            # 🗣️ wrap 收尾濃縮要用（把還沒說完的剩餘串濃縮成一句再淡收，不空收）；None＝退回模板暖收
        # statement_defer＝True：強制「陳述續打一律 defer、不消費」（不理會 INTERRUPT_STATEMENT_ENABLED）＋強制 rewrite 關。
        # 給**自發出聲相**（feel/adapt/act 的 💡🫧🫀🌀🦋… emit）用：插話對任何 bot 回應都生效（問題A），但只認真 redirect 插話，
        # 陳述尾巴仍留給下一圈 _relate_coalesced 合併（守 statement-defer 不變式、不破連發合併契約）；且背景自陳不被暖收截斷。
        self.statement_defer = statement_defer

    def poll(self):
        """看使用者這會兒有沒有插話。**真的想打斷改問（問句/指令）** → 回全部待處理的 update（即時優先回應）；
        **同一波的陳述續打**（非問句）→ 回 None＝不在講話途中另起一段回應，**不消費（offset 不前進）**留給本段講完後
        下一圈 `_relate_coalesced` 收進同一輪、整體回一次（修截圖：續打「小心點」被當插話、噴出獨立回應＋『繼續剛剛的』）。"""
        if not self.active or getattr(self.client, "dry_run", False):
            return None
        if not callable(getattr(self.client, "get_updates", None)):
            return None
        off = max(getattr(self.state, "tg_update_offset", 0), self.floor + 1)
        try:
            ups = self.client.get_updates(offset=off, timeout=0) or []
        except Exception:
            return None
        # nested id 可能已在前一次 parent 回覆途中送達並留下 exact receipt；重啟後
        # parent 尚未完成時，poll 也必須套同一把濾鏡，否則會把 2/3 當新插話再送一次。
        ups = _apply_burst_delivery_receipts(self.state, ups) or []
        if not any(_is_owner_text(u, self.cfg) for u in ups):
            return None
        # 🧵 續寫/追加跟句（同時/還有/而且/順便…）＝同一波延續、要和前一則一起讀，**不是**要打斷改問 → 一律 defer
        # 折進下一輪 _relate_coalesced 合併（即使帶問號、即使 INTERRUPT_STATEMENT_ENABLED 開），別在回應途中當獨立 redirect
        # 答（截圖：「同時說一下正在翻閱哪個主題嗎」被當插話單獨答、拉出舊記寫又困惑）。旗標 0＝不特判＝同現狀。
        _owner_texts = [(((u.get("message") or u.get("edited_message")) or {}).get("text"))
                        for u in ups if _is_owner_text(u, self.cfg)]
        _owner_texts = [t for t in _owner_texts if t]
        if (getattr(self.cfg, "interrupt_continuation_defer", True) and _owner_texts
                and all(selfstate.is_continuation_followup(t) for t in _owner_texts)):
            return None
        # 🧵 §1.48 附和不打斷（INTERRUPT_COALESCE）：整批都是『附和短句』（好久/沒關係/慢慢來/嗯/繼續說…）
        # ＝聽者點頭、不是要打斷改問 → defer（不消費、折進下一輪連發合併＝講完後**一次**溫和承接）——
        # 主體不中斷、不噴逐則「嗯。」、不需要「我繼續說喔，」橋（截圖 21:16-21:17 根因）。敵意短句不在
        # 附和表、再加 is_hostile 雙保險（§1.14 收口優先）。旗標關（getattr 預設 False）＝不特判＝逐位元同現狀。
        if (getattr(self.cfg, "interrupt_coalesce_enabled", False) and _owner_texts
                and all(selfstate.is_backchannel_interject(t) for t in _owner_texts)
                and not any(reaction.is_hostile(t) for t in _owner_texts)):
            return None
        # 🌊 §1.81 同一波不算插話（INTERRUPT_WAVE_GUARD）：截圖 07:01 使用者同一秒連發兩個**不同**的問句
        # （「是嗎？真的有這麼厲害？」「你知道我的作息？」），第二句因為是問句被判 redirect → 走插話路徑
        # ＝整波被拆成**兩輪**處理，於是 ①巢狀回覆完插進一句接回橋「誒，回到剛剛在講的，」（根本沒被打斷）
        # ②兩輪各自 observe 意圖 → 重複偵測把兩個不同的問題數成「同一個問題問兩次」→「你剛剛才問過我一樣的
        # 問題耶」＋「不過沒關係，我可以再跟你說一次」的施恩腔。§1.73 只修了**已合併**那條路；這裡補的是
        # 「回應途中才到、但其實同屬一波」的那條。判準＝新到這則的 date 距**正在回應的那則** ≤ wave_sec。
        # defer（不消費、offset 不前進）＝下一圈 _relate_coalesced 自然把它收進同一輪整體回一次。
        # 旗標關（getattr 預設 False）＝不判＝逐位元同現狀。
        if getattr(self.cfg, "interrupt_wave_guard_enabled", False) and self.wave_ts:
            _wave = max(1.0, float(getattr(self.cfg, "interrupt_wave_sec", 12.0)))
            # 🌊 §1.82 改「相鄰鏈」（與 group_bursts 同一套語意）：每一則只要距**前一則**在窗內就算同一波，
            # 錨點隨之推進——原本只錨在「正在回應的那則」，四則連打 30–40 秒就有人掉出窗外＝整波又被拆開
            # （截圖 07:18 四連發、07:19 回成十二顆泡泡＋接回橋＋誤判重複＝§1.81 沒接住的殘餘）。
            _dates = sorted(((u.get("message") or {}).get("date") or 0)
                            for u in ups if _is_owner_text(u, self.cfg))
            if _dates and all(d > 0 for d in _dates):
                _anchor, _chained = self.wave_ts, True
                for d in _dates:
                    if (d - _anchor) > _wave:
                        _chained = False
                        break
                    _anchor = max(_anchor, d)
                if _chained:
                    self.wave_ts = _anchor          # 錨點推進＝下一則接著比（同一波可以一直串下去）
                    return None
        # 「真的想打斷改問」（問句/指令）即時插話；純陳述續打預設 defer 折進下一輪合併——
        # 但 INTERRUPT_STATEMENT_ENABLED 開時，陳述也算即時插話（讓 bot 也能因陳述插話改變剩餘）。
        if any(_looks_like_redirect(((u.get("message") or u.get("edited_message")) or {}).get("text"))
               for u in ups if _is_owner_text(u, self.cfg)):
            return ups
        # 自發出聲相（statement_defer）一律不認陳述續打為插話＝留給下一圈 _relate_coalesced 合併（不消費、offset 不前進），
        # 不理會 INTERRUPT_STATEMENT_ENABLED（否則 feel/adapt 相搶先 poll 會吃掉本該合併的尾群、破連發合併契約）。
        if not self.statement_defer and getattr(self.cfg, "interrupt_statement_enabled", False):
            return ups
        return None

    def handle(self, ups):
        """先把插話（及一起到的反應等）依序處理掉＝優先回應使用者。期間不再自我打斷。
        🧵 §1.48（INTERRUPT_COALESCE）：密集多則文字先走既有連發合併純函式（group_bursts＋
        build_coalesced_update）黏成**一則**再巢狀回覆——修「使用者連丟三句、bot 逐則各回一句『嗯。』
        洗版」（插話路徑原本完全沒接合併層）。非純文字/稀疏群照原逐則（貼圖訊號不丟、斷群規則同主迴圈）。
        旗標關＝原逐則迴圈＝逐位元同現狀。"""
        # 防禦性再濾一次：handle 也會被測試／其他呼叫端直接餵 updates，不一定先經 poll。
        ups = _apply_burst_delivery_receipts(self.state, list(ups or [])) or []
        if not ups:
            return
        self.active = False
        try:
            if getattr(self.cfg, "interrupt_coalesce_enabled", False):
                gap = max(0.1, getattr(self.cfg, "burst_coalesce_sec", 2.5))
                turn_gap = max(gap, float(getattr(self.cfg, "burst_turn_gap_sec", 20.0)))
                turn_span = max(turn_gap, float(getattr(self.cfg, "burst_turn_max_span_sec", 60.0)))
                max_msgs = max(1, getattr(self.cfg, "burst_max_msgs", 12))
                for g in group_bursts(ups, getattr(self.cfg, "telegram_chat_id", ""), turn_gap, max_msgs,
                                      max_span_sec=turn_span):
                    if g.get("type") == "text" and len(g.get("updates") or []) > 1:
                        self.handle_fn(build_coalesced_update(g))   # 密集文字群＝合成一則、整體回一次
                        if not self.commit_offset:
                            # 合成文字是單一 handle 交易；只有完整成功後才能一次記整群。
                            _record_burst_delivery_receipts(
                                self.state,
                                [u.get("update_id", 0) for u in (g.get("updates") or [])],
                                persist=not getattr(self.client, "dry_run", False))
                    else:
                        for u in g.get("updates") or []:
                            self.handle_fn(u)
                            if not self.commit_offset:
                                # mixed/sticker/硬邊界是逐項對外動作；每一項成功就立即
                                # durable 記 exact id。若第二項失敗，第一項不會因「等整群完成」
                                # 而沒 receipt，重啟後也不會重送。
                                _record_burst_delivery_receipts(
                                    self.state, [u.get("update_id", 0)],
                                    persist=not getattr(self.client, "dry_run", False))
                    # 巢狀回覆真的完成後才消費。先推 offset 會在 handle_fn 失敗時永久跳過尚未處理的訊息。
                    for u in g.get("updates") or []:
                        self.floor = max(self.floor, u.get("update_id", 0))
                        if self.commit_offset:
                            self.state.tg_update_offset = max(getattr(self.state, "tg_update_offset", 0),
                                                              u.get("update_id", 0) + 1)
                return
            for u in ups:
                self.handle_fn(u)
                if not self.commit_offset:
                    _record_burst_delivery_receipts(
                        self.state, [u.get("update_id", 0)],
                        persist=not getattr(self.client, "dry_run", False))
                self.floor = max(self.floor, u.get("update_id", 0))
                if self.commit_offset:
                    self.state.tg_update_offset = max(getattr(self.state, "tg_update_offset", 0),
                                                      u.get("update_id", 0) + 1)
        finally:
            self.active = True

    def bridge(self):
        """挑一句接回話頭——避免和上一句連續重複（一段話被多次插話時更顯多元）。"""
        opts = [b for b in _RESUME_BRIDGES if b != getattr(self, "_last_bridge", None)] or list(_RESUME_BRIDGES)
        self._last_bridge = random.choice(opts)
        return self._last_bridge


# 🌊 §1.14 敵意情境對話收斂：插話批次裡有沒有「衝著 bot 的氣話」（owner 敵意文字或負向貼圖）。
# True＝_say 直接把話頭讓給對方（不橋接、不續講、不 wrap 濃縮）——截圖根因：user 連發「你不要敷衍我」，
# bot 卻「我繼續說喔」自顧自把剩餘串講完＝火上加油。訊號源與 hostile_streak 同一把（reaction.is_hostile／
# read_sticker=='negative'）＝單一真相。旗標關＝恆 False＝原 resume/wrap 邏輯逐位元。純函式、可測。
def _pending_hostile(pending, cfg):
    if not getattr(cfg, "hostile_converge_enabled", True):
        return False
    for u in (pending or []):
        msg = (u or {}).get("message") or (u or {}).get("edited_message") or {}
        chat = msg.get("chat") or {}
        if getattr(cfg, "telegram_chat_id", "") and str(chat.get("id")) != str(getattr(cfg, "telegram_chat_id", "")):
            continue                                        # 非擁有者 chat 不算（鏡像 _is_owner_text 的守門）
        if reaction.is_hostile((msg.get("text") or "").strip()):
            return True
        st = msg.get("sticker")
        if st and reaction.read_sticker(st.get("emoji") or "") == "negative":
            return True
    return False


# 🧵 §1.49 插話批裡有沒有 owner 的道別句（晚安/我先睡了…）——有＝答完插話就收口、不橋接不續殘句
# （對方都要走了還「我接著說」續客套尾巴＝不像人；§1.14 敵意收口的道別版）。owner 守門鏡像
# _pending_hostile。旗標由呼叫端把關。純函式、可測。
def _pending_farewell(pending, cfg):
    for u in (pending or []):
        msg = (u or {}).get("message") or (u or {}).get("edited_message") or {}
        chat = msg.get("chat") or {}
        if getattr(cfg, "telegram_chat_id", "") and str(chat.get("id")) != str(getattr(cfg, "telegram_chat_id", "")):
            continue
        if selfstate.is_farewell((msg.get("text") or "").strip()):
            return True
    return False


# 🧵 §1.49 插話後殘句去重：殘句若已在「這輪已送出的串」或「近幾則 model 回覆」（含剛才巢狀輪送出的，
# _remember 已入 convo_history）出現過＝不該一字不差重播（截圖 23:00「我會記得的。」巢狀輪剛說完、
# 殘句又原樣再說一次）。≥5 字才判（太短誤殺率高＝保守側）、子串/超串任一向（史條目是整則合併文字）。
# 回該跳過的殘句 index 集合；旗標關＝呼叫端不呼叫＝恆空。純函式、可測。
_TAIL_DUP_MIN = 5


def _interrupt_tail_dups(bubbles, i, state):
    said = [(b or "").strip() for b in bubbles[:i] if (b or "").strip()]
    hist = [(e.get("text") or "") for e in (getattr(state, "convo_history", None) or [])[-6:]
            if e.get("role") == "model" and (e.get("text") or "")]
    out = set()
    for j in range(i, len(bubbles)):
        t = (bubbles[j] or "").strip().strip("。！？!?…～~ 　")
        if len(t) < _TAIL_DUP_MIN:
            continue
        if any(t in p or (len(p) >= _TAIL_DUP_MIN and p in t) for p in said + hist):
            out.add(j)
    return out


# 🧵 §1.54 連續回覆的致意句去重（REPLY_ACK_DEDUP）：巢狀/連續輪各自生成，一分鐘內「嗯，我明白了。」
# 「我明白。」疊發（截圖 10:42 三顆明白）＝沒把使用者的連發當同一組對話。canon＝剝語氣/標點噪音→剝尾
# 了/的/啦→剝首「我」→ 落在小家族集合（明白/知道/懂/好/了解/收到/純嗯）才算**純致意句**；有實質內容的
# 句子（「嗯…我明白，光憑我說感覺你很難相信」）canon 落不進家族＝不動。純函式、可測。
_ACK_NOISE_RE = re.compile(r"[嗯欸唔喔哦噢啊呀…\s。．.！!？?，,、；;：:～~—－-]+")
_ACK_FAMILY = {"明白", "知道", "懂", "好", "好的", "了解", "收到", "ok", "okay", ""}


def _ack_canon(s):
    """致意句正規化到語意家族鍵（我明白了／明白。／嗯，我明白 → 明白）。純函式。"""
    t = _ACK_NOISE_RE.sub("", (s or "")).lower()
    t = t.rstrip("了的啦")
    if t.startswith("我"):
        t = t[1:]
    return t


def _is_ack_bubble(s):
    """這顆泡泡是不是**純致意句**（嗯／我明白（了）／好／了解…）——canon 落在家族集合才算。純函式。"""
    t = (s or "").strip()
    return bool(t) and _ack_canon(t) in _ACK_FAMILY


def _ack_dedup(bubbles, recent):
    """🧵 §1.54：對純致意泡泡去重——比對源＝recent（近窗 model 回覆，句級掃描）＋本則稍早的泡泡。
    recent=None＝旗標關＝原樣；全被刪光＝保留第一顆（寧可重複、不可失語）。純函式、可測。"""
    if recent is None:
        return bubbles
    seen = set()
    for t in recent:
        for sent_x in re.split(r"(?<=[。！？!?\n])", t or ""):
            if _is_ack_bubble(sent_x):
                seen.add(_ack_canon(sent_x))
    out = []
    for b in (bubbles or []):
        if _is_ack_bubble(b):
            c = _ack_canon(b)
            if c in seen:
                continue
            seen.add(c)
        out.append(b)
    return out or list(bubbles or [])[:1]


# 🦜 §1.28 ECHO_STRIP_WIRE：echo 剝除接上互動出口（純函式、可測）。echo.strip_leading_echo（剝 LLM 開頭
# 複誦使用者原話）原設計只接在 coach LLM 出口（coach.py ask/reply 的反鸚鵡兜底，保留勿動＝不重複攔兩次），
# monitor 互動出口 _say 從未接線＝bot 有時把使用者剛說的罵句原樣覆誦成自己的泡泡（截圖 7/12 21:04
# bot 自發「有夠爛……」＝無「你說」歸屬、看起來像 bot 在罵）。echo.py 本體一行不改（min_len=5 預設與
# _looks_same 被 tests/test_echo.py 釘死）——一切例外由本呼叫端傳參/前置判斷解：
#   1) 首段歸屬豁免：首段正規化後以「你說/妳說」開頭＝引用歸屬 → 整個不剝（_looks_same 含「包含」判定，
#      不豁免會把「你說有夠爛。」這種誠實歸屬句整段誤剝——偵察實證）。
#   2) min_len 旁路：敵意句（reaction.is_hostile，「別騙人啦」）**或**首段正規化後與某則使用者原話**全等**
#      → min_len=1 剝。全等旁路是對規格的必要擴充：偵察實測「有夠爛」「有夠爛……」is_hostile 皆 False、
#      min_len=5 也擋不住＝只按敵意旁路原事故仍漏；全等判準零誤傷「好，那我…」類短開頭（非全等不觸發）。
#      未中再走預設 min_len=5＝非敵意長複誦行為與 coach 端一致。
#   3) 單段兜底：strip_leading_echo 對單段恆 no-op（設計如此、不留空訊息）→ 這裡自己切前綴與其後標點/
#      刪節號；餘文空（純複誦泡泡、破口本尊）→ 整則替換確定性歸屬句「你說「…」，我聽到了。」。
def _echo_cut_prefix(raw, un):
    """把 raw 開頭對應正規化前綴 un 的實質字（連同夾雜與緊接的標點/刪節號/空白）切掉，回餘文（可能為空）。"""
    need, i = len(un), 0
    while i < len(raw) and need > 0:
        if echo._norm(raw[i]):                              # 實質字才計數（標點/空白由 _norm 濾掉＝同一把正規化）
            need -= 1
        i += 1
    while i < len(raw) and not echo._norm(raw[i]):          # 再吃掉緊接的標點/刪節號（「有夠爛……」的「……」）
        i += 1
    return raw[i:].strip()


def _echo_strip_wire(msg, texts, whole=False, prefix_run=False):
    """互動出口的複誦剝除本體 → 回 (定稿文字, 是否有剝/有換)。texts＝近幾則使用者原話（含本句），
    由 handle_message 依旗標 stash（旗標關/背景路徑＝不 stash＝呼叫端根本不進來）。
    whole（§1.74、旗標傳入；False＝同現狀）＝**整則**都是複誦時（連發合併的多行照抄死角、
    首段太短/相似度擦邊使既有兩道防線同時 no-op）整則替換成第一人稱認帳句——不無聲、不假裝那是回答。
    prefix_run（📦 §1.86、旗標傳入；False＝同現狀）＝**開頭連續幾段合起來**是使用者原話（尾巴才是自己的
    話）也算複誦 → 剝掉那幾段、保留尾巴。補的是「整則」與「單段」之間**缺掉的那個粒度**（見 echo.prefix_run_echo
    的 22:53 實測：span 0.786 vs 0.8、相似度 0.88 vs 0.9、逐段 0.778 全部差一點點）。"""
    segs = echo._segments(msg)
    if not segs or not texts:
        return msg, False
    first = echo._norm(segs[0])
    if first.startswith("你說") or first.startswith("妳說"):
        return msg, False                                   # 1) 歸屬引用＝誠實句，整個不剝
    if whole:                                               # 0b) §1.77 任何位置的裸複誦（罵句落在中間那段＝三道防線全空轉）
        _bp = [u for u in texts if u and (reaction.is_hostile(u) or len(echo._norm(u)) >= 2)]
        _out, _hit = echo.strip_echo_segments(msg, _bp, min_len=1 if any(reaction.is_hostile(u) for u in _bp) else 5)
        if _hit and _out and echo._norm(_out) != echo._norm(msg):
            return _out, True                               # 剝掉那段、其餘正題保留（§1.62 慣例）
    if whole:                                               # 0) §1.74 整則複誦（多行照抄）→ 自己抓到、自己認
        _wu = echo.whole_echo_of(msg, texts)
        if _wu is not None:
            _uq = (echo._segments(_wu) or [(_wu or "").strip()])[0]
            return (f"你說「{_uq}」——我剛剛整段只是把你的話覆述回去，那不算回答。"
                    "我重講：這句我真正想回的是什麼，讓我用自己的話說。"), True
    if prefix_run:                                          # 0a-) 📦 §1.86 開頭**連續幾段**合起來＝他那句話（尾巴才是自己的話）
        _pr, _pu = echo.prefix_run_echo(msg, texts)
        if _pr:
            _puq = (echo._segments(_pu) or [(_pu or "").strip()])[0]
            print(f"[echo] 🦜 §1.86 開頭連續段複誦守門：前幾段就是「{_puq}」→ 剝掉、只留自己的話")
            return _pr, True                                # 剝掉複誦、保留尾巴（§1.62/§1.77「句級更正優先」慣例）
    if whole and len(segs) > 1:                             # 0a) §1.81 開頭段是使用者原話的**前綴**＝拿他的話開頭
        _f = echo._norm(segs[0])
        # 截圖 07:02 第一顆泡泡「是嗎。」＝使用者「是嗎？真的有這麼厲害？」的開頭兩字：既不等於整句
        # （whole_echo_of 不中）、又只有 2 字（min_len=5 擋不到）、_looks_same 的長度比也不過 → 三道全漏。
        # 判準：正規化後 ≥2 字、是某句使用者原話的前綴、且明顯比它短（≤50%）＝拿對方的話當開場白。
        if len(_f) >= 2:
            for _u in texts:
                _un = echo._norm(_u)
                if len(_un) > len(_f) * 2 and _un.startswith(_f):
                    _rest = "".join(segs[1:]).strip()
                    if _rest:
                        return _rest, True
                    break
    bypass = [u for u in texts if u and (reaction.is_hostile(u) or echo._norm(u) == first)]
    if bypass:                                              # 2) 敵意/全等旁路：無視 min_len=5
        out, hit = echo.strip_leading_echo(msg, bypass, min_len=1)
        if hit:
            return out, True
    out, hit = echo.strip_leading_echo(msg, texts)          # 預設路徑（min_len=5）＝與 coach 端行為一致
    if hit:
        return out, True
    if len(segs) == 1 and bypass:                           # 3) 單段兜底（strip_leading_echo 恆 no-op 的死角）
        whole = echo._norm(msg)
        for u in bypass:
            un = echo._norm(u)
            if un and whole.startswith(un):
                rest = _echo_cut_prefix(msg, un)
                if rest:
                    return rest, True                       # 前綴複誦＋餘文（「別騙人啦，我沒有要騙你」）
                uq = (echo._segments(u) or [(u or "").strip()])[0]
                return f"你說「{uq}」，我聽到了。", True      # 純複誦泡泡 → 整則替換確定性歸屬句
    return msg, False


# 🧹 §1.23 內部記憶註解「（我送了一張貼圖：emoji——desc）」——只該進 convo_history 給 LLM 知道自己送過，
# 絕不該送給使用者。但它是 role='model' 的一則，下一輪 LLM 讀歷史時會把它當「自己上一句」照抄吐出來
# （截圖 08:15：bot 首句冒出這串註解）。送出前統一洗掉＝硬守門（不靠 LLM 自律）。
_STICKER_MEMO_RE = re.compile(r"（我送了一張貼圖[^）]*）")
# 🧹 §1.29 補遺（2026-07-22 15:57 實洩復發）：LLM 照抄註解時會**改寫**——丟「我」、外層全形括號變半形
# （實洩結尾是兩個半形 ))）、desc 自帶巢狀括號——舊 regex（錨「（我送了一張貼圖」＋只認全形收尾）完全
# 不命中。硬化：錨頭放寬（全/半形開括號＋可選 我/剛剛/剛才），砍除範圍改用**平衡括號掃描**（全半形都算）；
# 同一行內配不平（LLM 亂寫）＝刪到行尾（註解洩漏永遠獨立成段、正文不受傷）。沒有前導括號的正常口語
# （「我剛剛送了一張貼圖給你」）不是註解、不洗。
_STICKER_MEMO_HEAD_RE = re.compile(r"[（(]\s*(?:我)?(?:剛剛|剛才)?送了一張貼圖")


def _strip_sticker_memo(text):
    """🧹 §1.29：把洩漏到送出文字裡的 §1.23 貼圖記憶註解整段拿掉（含 LLM 改寫變體）。旗標關＝不洗＝
    逐位元同現狀（關旗標時本就不會寫這註解，故無可洗）。純函式、可單測。"""
    if os.getenv("STICKER_SENT_MEMORY", "1") == "0":
        return text
    t = _STICKER_MEMO_RE.sub("", text or "")             # 舊格式快路徑（原行為位元不變）
    while True:
        m = _STICKER_MEMO_HEAD_RE.search(t)
        if not m:
            break
        i = m.start()
        line_end = t.find("\n", i)
        line_end = len(t) if line_end == -1 else line_end
        depth, end = 0, None
        for j in range(i, line_end):
            c = t[j]
            if c in "（(":
                depth += 1
            elif c in "）)":
                depth -= 1
                if depth == 0:
                    end = j + 1
                    break
        t = t[:i] + t[line_end if end is None else end:]
    return t


_SENT_MEMORY_LEAD_RE = re.compile(r"^[^0-9A-Za-z\u3400-\u9fff]+")


def _sent_memory_key(text):
    """把內部 lane icon／「背景自陳」名牌拿掉，讓 `_say(msg,prefix=…)` 對得上 `_remember(icon+msg)`。"""
    t = (text or "").strip()
    t = _SENT_MEMORY_LEAD_RE.sub("", t).lstrip()
    if t.startswith("背景自陳"):
        t = t[len("背景自陳"):].lstrip(" \t\r\n：:")
    return re.sub(r"\s+", " ", t).strip()


def _stage_sent_model(raw, actual, mood_contract=None, message_ids=()):
    """登記進場原文與真送達文字；可把座標契約和這一次送達原子綁在同一 entry。

    不另放全域 `_TURN` token：巢狀 interrupt 會啟動另一個 handle 並清 `_TURN`，只有外層 `_say` 收尾時
    重新 stage 的這一筆，能可靠代表「這份 contract 對應這段 actual wire」。
    """
    entry = {"key": _sent_memory_key(raw), "text": actual or ""}
    if message_ids:
        entry["message_ids"] = [i for i in message_ids if type(i) is int and i > 0]
    if isinstance(mood_contract, dict):
        entry["mood_contract"] = dict(mood_contract)
    _TURN.setdefault("sent_model_text", []).append(entry)


def _utf16_units(text):
    """Telegram 長度預算的保守計數；astral emoji 會佔兩個 UTF-16 units。"""
    return len((text or "").encode("utf-16-le")) // 2


def _grapheme_chunks(text):
    """無額外依賴的保守 grapheme 切分：不拆 combining mark、VS、膚色修飾、ZWJ 串與國旗對。"""
    out = []
    ri_run = 0
    for ch in text or "":
        cp = ord(ch)
        combining = bool(unicodedata.combining(ch))
        variation = 0xFE00 <= cp <= 0xFE0F or 0xE0100 <= cp <= 0xE01EF
        modifier = 0x1F3FB <= cp <= 0x1F3FF
        regional = 0x1F1E6 <= cp <= 0x1F1FF
        if not out:
            out.append(ch)
        elif combining or variation or modifier or ch == "\u200d" or out[-1].endswith("\u200d"):
            out[-1] += ch
        elif regional and ri_run % 2 == 1:
            out[-1] += ch
        else:
            out.append(ch)
        ri_run = (ri_run + 1) if regional else 0
    return out


def _take_utf16_prefix(text, budget):
    """取不超過 budget 的 grapheme-safe 前綴；夠長時優先收在自然邊界。"""
    if budget <= 0:
        return ""
    if _utf16_units(text) <= budget:
        # 已完整放得下時絕不能再拿「自然句界」當裁切點；那會在句號後仍有合法尾文時
        # 無聲丟掉尾巴（尤其是已預留容量、承諾完整保留的 voice）。
        return (text or "").rstrip()
    kept, used = [], 0
    for cluster in _grapheme_chunks(text):
        n = _utf16_units(cluster)
        if used + n > budget:
            break
        kept.append(cluster)
        used += n
    raw = "".join(kept).rstrip()
    if not raw or _utf16_units(raw) < int(budget * 0.7):
        return raw
    # 不為了漂亮斷句丟掉超過 30% 內容；只在後 30% 找最後的段落/句/空白邊界。
    floor = int(len(raw) * 0.7)
    cut = max(raw.rfind(mark, floor) + len(mark) for mark in ("\n", "。", "！", "？", ";", "；", " "))
    return raw[:cut].rstrip() if cut > floor else raw


def _split_telegram_text(text, limit=None):
    """把傳輸文字依 Telegram 上限切成 grapheme-safe chunks；短文原樣一塊。"""
    lim = int(limit or notifier.TELEGRAM_LIMIT)
    remaining = text or ""
    if _utf16_units(remaining) <= lim:
        return [remaining]
    out = []
    while remaining:
        piece = _take_utf16_prefix(remaining, lim)
        if not piece:                                      # 單一 cluster 理論上不會 >4096；保底避免無窮迴圈
            piece = remaining[0]
        out.append(piece)
        remaining = remaining[len(piece):]                 # transport 不吞空白；下一塊原樣承接
    return out


def _data_voice_payloads(data_lead, voice, limit=None):
    """證據 data＋人話 voice 的傳輸組裝。

    短文回一塊。合併超限但 voice 本身放得下時，只節錄 data 且顯式標記，
    完整 voice 永遠留在尾端。voice 本身超限才退為多個 transport chunks，不交給
    Notifier 靜默截尾。回 (payload, voice_fragment) 供 _say 只記真送達的人話。
    """
    lim = int(limit or notifier.TELEGRAM_LIMIT)
    data = notifier.strip_markdown(str(data_lead or "")).rstrip()
    spoken = notifier.strip_markdown(str(voice or ""))
    combined = (data + "\n\n" if data else "") + spoken
    if _utf16_units(combined) <= lim:
        return [(combined, spoken)]
    if _utf16_units(spoken) <= lim:
        marker = "…（資料較長，已節錄）\n\n"
        budget = lim - _utf16_units(marker) - _utf16_units(spoken)
        if budget < 0:
            # voice 幾乎佔滿整個 Telegram payload 時，無法同塊兼放說明；把說明當
            # transport 前置塊，仍完整保留 voice，且不靜默吞掉資料。
            return [(marker.rstrip(), ""), (spoken, spoken)]
        excerpt = _take_utf16_prefix(data, budget).rstrip()
        payload = excerpt + marker + spoken
        return [(payload, spoken)]
    specs = []
    if data:
        for part in _split_telegram_text(data, lim):
            specs.append((part, ""))
    for part in _split_telegram_text(spoken, lim):
        specs.append((part, part))
    return specs


def _say(client, text, prefix="", state=None, topic=None, track_initiative=True, wire_lead=""):
    begin_voice = getattr(client, "begin_voice", None)
    if callable(begin_voice):
        begin_voice()
    _af_key = (text or "").strip()                       # 🩹 §2.14 歷史替換的比對鍵＝**進場原文**（轉換前；見 §2.09 區塊）
    _raw_prefix = prefix                                  # 🧭 對話能動性用原始 lane 身分；顯示層可把裝飾名牌拿掉
    _mood_delivery_contract = None                        # 與本次 actual wire 一起 stage，跨巢狀 interrupt 不丟
    _burst_one_wire = bool(_BURST_ONE_WIRE_STACK and _BURST_ONE_WIRE_STACK[-1])
    """把『對話』分串依序送出（串與串之間補『輸入中…』＋短停頓＝真人一串一串打字的手感）。
    回全部是否送成功。證據性資料請直接用 client.send（維持整塊、不分串）。
    `wire_lead` 是已接地、已格式化的證據前綴：只在所有 voice 守門完成後黏到第一顆 wire，
    供 multi-message turn 將 data＋voice 原子送成同一則；它不進 voice 改寫，也不寫進 voice 對話記憶。
    給了 state＋topic（主動自陳/談某條線時）→ 把這幾串的 message_id 記成『這則在講 topic』，
    讓使用者之後對某則按讚時，bot 查得回『讚的是哪一筆』。"""
    text = coachmod.strip_leaked_time_tags(text)        # 🧹 送出前統一洗掉模型照抄/每段都掛的時間標籤〔剛剛/N天前…〕（單一防線、所有 voice 路徑共用）
    text = _strip_sticker_memo(text)                    # 🧹 §1.29 同一防線：洗掉洩漏的貼圖記憶註解（§1.23 內部標記別出現在對話裡）
    # 🕐 §0.82 全域鐘點守門：只在**互動回覆**（一般聊天串＝無 prefix、無 state）套用——主動兌現/帳本用 prefix/state，各有自己的
    # 守門且 now 來源不同（生命迴圈的 now，非本輪 message.date），不經此、免拿本輪 now 亂改主動兌現的時刻。ground_now 只由
    # handle_message 於互動輪設定（旗標開時）；主動兌現路徑不設＝這裡 gn 為本輪殘值但因帶 prefix/state 而不套用＝不誤動。
    if not prefix and state is None:
        _TURN["last_reply"] = text                        # 🎴 §0.87 記下這則**互動回覆**文字（供 _maybe_sticker 判情緒配貼圖；主動兌現/帳本帶 prefix/state 不記）
        _gn = _TURN.get("ground_now")
        if _gn:
            text, _ch = _now_clock_fix(text, _gn[0], _gn[1])
            if _ch:
                print("[time] 🕐 全域守門：回覆把『現在』說成別的時刻 → 就地改回真實此刻")
        # 🤝 §1.13B 假兌現守門（比照 time_guard 落回確定性模板）：帳本說「逾期未兌現/剛錯過」、這則互動回覆
        # 卻宣稱「我做到了/我來了/準時」＝假兌現（最嚴重誠實違規，截圖 12:29 遲 10 分、被催才出現還說做到了）
        # → **整則替換**成確定性誠實句（遲到認帳/錯過道歉）。ground 由 handle_message 在回覆橋**之後**算
        # （旗標關＝不設＝這裡恆不動）；準時兌現/主動守約走 prefix/state 路徑＝不進本分支＝零位元變動。
        _kg = _TURN.get("keep_claim_ground")
        if _kg and _keep_claim_hit(text):
            _kck, _kchh, _kcbeh = _kg
            if _kck == "owed":
                # 📦 §1.85 準時出現 ≠ 做到：這句要同時承認「人到了」與「內容沒交」，不能講成遲到（那是另一回事）
                text = (f"我不能說我做到了——{_kchh} 的「{_kcbeh}」我人是準時出現了，"
                        "但答應你的內容我沒真的講出來。現在就補。")
            elif _kck == "missed":
                text = f"我不能說我做到了——{_kchh} 的「{_kcbeh}」我錯過了，對不起。要再約一次，我一定守住。"
            else:
                text = f"我得誠實說：{_kchh} 約好的「{_kcbeh}」我遲到了、還沒做到——現在就補上。"
            _TURN["last_reply"] = text                    # 誠實句才是真送出的話（貼圖情緒判讀別讀到假宣稱）
            print("[promise] 🤝 假兌現守門：回覆宣稱守約但帳本說逾期 → 就地替換誠實句")
        # 🤝 §1.20 否認守門（§1.13B 同構）：帳上明明有你說過的相符項（pending／72h 內剛兌現／感覺託付），
        # 互動回覆卻說「我沒聽到你說/你沒說過」（截圖 07:01：07:00 才剛 🤝 兌現完叫醒，07:01 就否認聽過）
        # → 整則替換確定性誠實句。引用歸屬（「你剛剛說我沒聽到」）不攔；ground 由 handle_message 算（旗標關＝不設＝恆不動）。
        _sg = _TURN.get("said_denial_ground")
        if _sg and _said_denial_hit(text):
            _sgwhen, _sgbeh, _sgdone = _sg
            if _sgdone:
                text = f"有，你說過——{_sgwhen}的「{_sgbeh}」我記在帳上，而且我剛在 {_sgdone} 做了。剛剛那句說反了，是我不對。"
            elif _sgwhen:
                text = f"有，你說過——{_sgwhen}的「{_sgbeh}」我記在帳上，還等著到點去做。剛剛那句說反了，是我不對。"
            else:
                text = f"有，你說過——「{_sgbeh}」我記著（這件沒約鐘點，真有感覺湧現才說）。剛剛那句說反了，是我不對。"
            _TURN["last_reply"] = text
            print("[promise] 🤝 §1.20 否認守門：帳上有相符項卻說「沒聽到你說」→ 就地替換誠實句")
        # 🎴 §1.23 貼圖否認守門（同構）：2h 內明明真送出過貼圖，互動回覆卻說「我沒有傳貼圖」
        # （截圖 18:24-18:25 連三句否認 11 分鐘前自己送的忍者貼圖）→ 整則替換誠實句，據實談那張。
        _skg = _TURN.get("sticker_denial_ground")
        if _skg and _sticker_denial_hit(text):
            _skhh, _skemoji, _skdesc = _skg
            if _skdesc:
                text = f"有，我 {_skhh} 才送過一張——畫的是「{_skdesc}」。剛剛差點不認帳，是我不對。"
            elif _skemoji:
                text = f"有，我 {_skhh} 才送過一張，我記著它的情緒是「{_skemoji}」。剛剛差點不認帳，是我不對。"
            else:
                text = f"有，我 {_skhh} 才送過一張貼圖。剛剛差點不認帳，是我不對。"
            _TURN["last_reply"] = text
            print("[sticker] 🎴 §1.23 貼圖否認守門：剛送過卻說「沒傳貼圖」→ 就地替換誠實句")
        elif _skg:
            # 🎴 §1.62 軟更正（使用者回饋：透明推理是好風格、只有事實錯）：問句形/系統歸因＝只換錯句、
            # 保留「查紀錄/承認不確定」的其餘推理句。
            text, _ssch = _sticker_denial_soft_fix(text, _skg)
            if _ssch:
                _TURN["last_reply"] = text
                print("[sticker] 🎴 §1.62 貼圖否認軟更正：問句形/系統歸因＝只換錯句、推理保留")
        # 🎴 §1.34 STICKER_V2/F4 假送誠實閘（**緊接 §1.23 否認閘之後**、同構反向）：互動送圖三 lane 全在 coach.reply
        # 之前短路 → 一般聊天 LLM 這一輪定義上永不真送貼圖，故其自由文字裡任何「挑了這張給你／選了一張很平靜的貼圖／
        # 才送了一張思考的貼圖…沒看到嗎」present/immediate-past 宣稱都**無 send_sticker backing**＝假送（說謊）。
        # backing 唯一真相＝_record_sticker_sent 在 _say 前設的 sticker_sent_this_turn（真送才有）：偏好題
        # sticker_preference_reply／§1.16 sticker_why_reply 皆真送之後才 _say＝旗已設＝閘不攔（放行真送的「挑了這張給你」）。
        # arm 由 handle_message 依旗標 stash（旗標關＝不 arm＝下面恆 False＝逐位元同現狀）；引用歸屬與否定不命中 pattern。
        if _TURN.get("sticker_fakesend_arm") and not _TURN.get("sticker_sent_this_turn") and _fakesend_hit(text):
            # 🎯 §1.70A 句級軟化（§1.62 慣例：錯的是「一句假送宣稱」不是「整則謊言」→ 整則替換是最後手段）：
            # 截圖 10:26 根因＝使用者釐清「我是指，你在翻閱我的記寫過程中」的**正題回答**被整則換成貼圖誠實模板
            # ＝答非所問。改：只剝假送句、正題保留＋一句誠實補註；全剝空＝退回原整則模板。旗標關＝原行為。
            _sfk = None
            if _TURN.get("sticker_fakesend_soft"):
                _sfk, _ = _fakesend_soft_fix(text)
            if _sfk:
                _tail = ("（剛差點順口說我送了貼圖——其實沒有，貼圖現在也送不出來。）"
                         if _TURN.get("sticker_fakesend_maint")
                         else "（剛差點順口說我送了貼圖——其實沒有；要的話我真的送一張。）")
                text = _sfk + "\n" + _tail
                print("[sticker] 🎯 §1.70A 假送軟化：只剝假送句、正題保留＋誠實補註")
            elif _TURN.get("sticker_fakesend_maint"):      # 🔇 維護期（SEND_STICKERS=0）：別反問「要我送一張嗎」（現在送不出）
                text = "貼圖現在送不出來，我不該說我挑了／送了一張。"
                print("[sticker] 🎴 §1.34 假送誠實閘：宣稱送/挑了貼圖但本輪沒真送 → 就地替換誠實句")
            else:
                text = "我其實沒真的送出貼圖——別讓我用一句話假裝送了。要我送一張嗎？"
                print("[sticker] 🎴 §1.34 假送誠實閘：宣稱送/挑了貼圖但本輪沒真送 → 就地替換誠實句")
            _TURN["last_reply"] = text
        # 🧭 §1.36 記寫回想幻覺守門（§1.13B/§1.20/§1.23/§1.34 同構、緊接 §1.34 假送閘之後）：這輪在問「自己某筆記寫的
        # 內容/原因」而 arm 了語料（recall_ground is not None＝已 arm；空語料 corpus="" 也算 arm）、互動回覆卻把記寫原文
        # 沒有的具體事由歸因給記寫（照顧家人/家庭聚餐/沒睡好…）＝接地層幻覺（截圖：使用者說「又發生編造」）→ 整則替換
        # 誠實句（有摘要就引用『你只寫了X』，無摘要用泛句）。arm 由 handle_message 依旗標 stash（旗標關＝不 arm＝下面恆
        # no-op＝逐位元同現狀）；只讀 _TURN、不呼叫 LLM；bot 自身感受/推理無歸因框架不攔、引用歸屬不攔、原文真有 substring 不攔。
        _rg = _TURN.get("recall_ground")
        if _rg is not None and _recall_hallucination_hit(text, _rg):
            _sm = _TURN.get("recall_summary")
            if _sm:
                text = "目前的原文不足以支持這個具體原因，我不能替你的記寫補出沒有核對到的內容。"
            else:
                text = "目前沒有可核對的原文，我無法確認那筆記寫的內容或原因。"
            _TURN["last_reply"] = text
            print("[recall] 🧭 §1.36 事後幻覺守門：答案把記寫原文沒有的具體事由歸因給記寫 → 就地替換誠實句")
        # 🌅 §1.39 自他邊界守門（§1.13B/§1.36 同構、緊接 §1.36 之後）：這輪 arm 了（bot 重生醒來＋使用者沒自述睡醒）、
        # 互動回覆卻把 bot **自己**剛睡醒/悶悶的狀態投射成使用者「你醒了/你剛剛小睡了一下/悶悶的」（截圖：使用者根本
        # 沒說自己睡醒）→ 剝掉那些投射句（引用歸屬/bot 講自己/祈使不誤剝；整則都是投射→誠實更正句）。arm 由
        # handle_message 依旗標 stash（旗標關＝不 arm＝下面恆 no-op＝逐位元同現狀）；只讀 _TURN、不呼叫 LLM。
        if _TURN.get("wake_proj_strip"):
            text, _wpch = _strip_wake_projection(text)
            if _wpch:
                _TURN["last_reply"] = text
                print("[wake] 🌅 §1.39 自他邊界守門：把 bot 自己的『剛睡醒/悶悶的』投射成使用者 → 就地剝除投射句")
        # 📈 §1.42 作息宣稱守門（§1.13B/§1.36 同構、緊接 §1.39 之後）：互動回覆對**使用者**的作息下斷言
        # （「你通常會在早上十點左右跟我說早安」「你今天比平常晚」），但與真統計不符或根本無統計（截圖：全 repo
        # 對話習慣零資料、LLM 憑印象亂掰被抓包）→ 確定性剝掉宣稱句、補照統計的誠實句。arm 由 handle_message 依
        # 旗標 stash（旗標關＝不 arm＝恆 no-op＝逐位元同現狀）；引用歸屬不剝、bot 講自己「我平常…」不命中。
        _hg = _TURN.get("habit_claim_ground")
        _greet_fb = _TURN.pop("greet_claim_fallback", "")   # 🕘 §2.22 消費一次即清（防殘留到別輪/主動 lane）
        if _hg is not None:
            text, _hbch = _habit_claim_fix(text, _hg, greet_fallback=_greet_fb)
            if _hbch:
                _TURN["last_reply"] = text
                print("[habit] 🕘 §2.22 問候輪剝作息錯句（不補報表句、剝光退回問候）" if _greet_fb
                      else "[habit] 📈 §1.42 作息宣稱守門：回覆對使用者作息的宣稱無統計背書/與統計不符 → 就地換照統計誠實句")
        # 🪞 §2.26 引用歸屬守門（緊接 §1.42 之後）：「你說「X」」的 X 其實是我自己說的（只在 model 輪
        # 出現過、他沒說過）→ 就地改「我剛說「X」」；反向同理。未 stash（旗標關）＝恆 no-op＝逐位元同現狀。
        _sg = _TURN.get("speaker_ground")
        if _sg:
            text, _sgch = _quote_speaker_fix(text, _sg)
            if _sgch:
                _TURN["last_reply"] = text
                print("[quote] 🪞 §2.26 引用歸屬守門：把我自己說過的話講成「你說」（或反向）→ 就地改正話者")
        # 🍽 §1.65 暫離常識守門（§1.42 同構、緊接其後）：他才說要去吃飯、常識時距未滿（arm 時已判），回覆卻把人
        # 當已回來/已吃完（「你回來啦」「你現在吃飽了嗎」「在你吃飯的時候」）→ 句級剝除（§1.62 慣例：只修錯句、
        # 不整則替換）；「等你回來再說」這種**未來**語不剝。arm 由 handle_message stash（未 arm＝恆 no-op）。
        _awg = _TURN.get("away_claim_guard")
        if _awg:
            text, _awch = _away_claim_fix(text, _awg)
            if _awch:
                _TURN["last_reply"] = text
                print(f"[away] 🍽 §1.65 暫離常識守門：他才說要去{_awg.get('act')}、時間還不夠 → 剝掉「回來/吃飽」的錯誤預設句")
        # 🎚️ §1.76 確定性口吻整形（prompt 單靠不夠＝§1.34/§1.42 教訓）：內在明顯偏負時，把「過度歡快」的
        # 語氣標記收掉（連發驚嘆號、歡快 emoji；悶/低落/倦連單一驚嘆號也降成句號）——語氣是被感覺到的，
        # 不能嘴上說沉、標點卻在跳。旗標關/未 stash＝恆 no-op＝逐位元同現狀。
        _tn = _TURN.get("tone_now")
        if _tn:
            text, _tnch = _tone_shape(text, _tn[0], _tn[1])
            if _tnch:
                _TURN["last_reply"] = text
                print(f"[tone] 🎚️ §1.76 口吻整形：內在偏負（V {_tn[0]:+.2f}／A {_tn[1]:+.2f}）→ 收掉過度歡快的語氣標記")
        # 🧭 §2.27 座標出口契約：有接地也不能全面豁免——grounded 只證明「看過 facts」，不證明模型沒有把
        # 歷史／編造值冒充此刻。數據輪由程式渲染唯一 current/previous 時序，LLM 只留下不含數字的主觀質地。
        # 舊 mood-watch 等專用路徑只掛 grounded、不掛 contract，仍維持原白名單行為。
        _ct = _TURN.get("coord_claim_truth")
        _mcc = _TURN.pop("mood_coord_contract", None)
        if _mcc:
            text, _ccch = _coord_grounded_fix(text, _mcc)
            _mood_delivery_contract = dict(_mcc)
            _TURN["mood_contract_line"] = _coord_current_anchor(_mcc)  # replay 後再驗一次，不能把 pair 一起去重掉
            _TURN.pop("mood_data_line", None)              # 契約文字已確定性含完整 current pair，不再另補一行
            _TURN["last_reply"] = text
            print("[mood] 🧭 §2.27 座標時間層契約：唯一此刻快照由程式渲染，模型只保留主觀質地")
        elif _ct is not None and not _TURN.get("mood_coord_grounded"):
            text, _ccch = _coord_claim_fix(text, _ct)
            if _ccch:
                _TURN["last_reply"] = text
                print("[mood] 🧭 §1.47 無接地座標數字守門：回覆裡的座標數字不是程式讀的 → 就地換真數字")
        # 🎭 §1.58 不演未來（緊接 §1.47 之後）：回覆裡出現「（30 分鐘後）」類舞台指示＝把還沒到的時刻當場
        # 演完（截圖 22:17 自導自演「入帳→到點→兌現」整齣）→ 從舞台指示處截斷、保留誠實答應。
        # arm 由 handle_message 依旗標 stash（旗標關/未 arm＝恆 no-op＝逐位元同現狀）。
        if _TURN.get("timejump_guard"):
            text, _tjch = _timejump_truncate(text)
            if _tjch:
                _TURN["last_reply"] = text
                print("[time] 🎭 §1.58 時間跳躍演出守門：把還沒到的時刻當場演完 → 從舞台指示處截斷")
        # 🕐 §1.60 記寫宣稱守門（緊接 §1.58）：今天其實零記寫，回覆卻說「你今天記寫/讀經了」（把 bot 自己的
        # 摘要推播時間當成使用者的記寫時間，截圖 09:15「你今天早上 9 點 01 分記下…」）→ 整句換程式算的事實句。
        _wg = _TURN.get("write_claim_ground")
        if _wg is not None:
            text, _wch = _write_claim_fix(text, _wg)
            if _wch:
                _TURN["last_reply"] = text
                print("[time] 🕐 §1.60 記寫宣稱守門：今天其實沒有新記寫 → 換程式算的事實句")
        # 🗜️ §1.43 感覺鋪陳修剪（緊接 §1.42 之後）：這輪不是在問 bot 自己，回覆卻鋪陳一長串自我感覺質地
        # （截圖 08:15 六句「安靜/內裡沉沉/往裡面縮/翻來翻去/思緒淌/悶提不起勁」＝使用者說像念稿）→ 只留前兩句
        # 關鍵。arm 由 handle_message 依 route 判（旗標關/問 bot 自己＝不 arm＝恆 no-op）；確定性、不呼叫 LLM。
        if _TURN.get("self_feel_trim"):
            text, _sfch = _self_feel_trim(text)
            if _sfch:
                _TURN["last_reply"] = text
                print("[feel] 🗜️ §1.43 感覺鋪陳修剪：別人問別的、答案尾端鋪陳一長串內在質地 → 修剪成前兩句關鍵")
        # 🦜 §1.28 ECHO_STRIP_WIRE：互動出口剝開頭裸複誦（截圖 21:04 bot 自發「有夠爛……」＝複誦使用者罵句、
        # 無歸屬、看起來像 bot 在罵）。素材由 handle_message 依旗標 stash（旗標關/主動兌現路徑＝不設＝恆 no-op
        # ＝逐位元同現狀）；有剝/有換 → 同步刷 last_reply＝定稿必在下面 §1.18 掃描之前（掃的是真送出的話）。
        _eut = _TURN.get("echo_user_texts")
        if _eut:
            text, _ech = _echo_strip_wire(text, _eut, whole=_TURN.get("echo_whole_guard", False),
                                          prefix_run=_TURN.get("echo_prefix_run", False))   # 🦜 §1.74 整則複誦也攔／§1.86 開頭連續段
            if _ech:
                _TURN["last_reply"] = text
                print("[echo] 🦜 §1.28 互動出口剝複誦：開頭照搬使用者原話 → 已剝除/替換歸屬句")
        # 🫸 §1.87 卡住情境（他在催、我剛剛還在重複）下**禁止把球踢回去**：「我在等你決定」「你想聽哪部分」
        # 這類句子在此刻等於不作為（截圖 22:56/22:57）。prompt 守則不夠（§1.34/§1.36 教訓）→ 確定性剝句；
        # 全剝空＝換誠實認卡句。平常的「你想先聊哪個」不在卡住情境＝不進本分支＝零位元變動。
        if _TURN.get("nudge_stuck"):
            text, _bb = _ball_back_strip(text)
            if _bb:
                _TURN["last_reply"] = text
                print("[nudge] 🫸 §1.87 踢球守門：他在催、回覆卻把選擇權推回去 → 剝掉那句")
        # 🤖 §1.18 BOT_SELF_PROMISE：掃**最終真送出**的互動回覆——bot 自己開口的「第一人稱未來承諾＋temporal
        # 可解的未來時刻」（明天早上11點，我會過來這裡…）也入 scheduled_promises（origin='self'）＝bot 自發承諾
        # 是一等公民：入同一本帳、到點同樣 _promise_emit 兌現、逾期同樣誠實（截圖 7/10 21:50 那句從未入帳的根因）。
        # 旗標關＝handle_message 不設 ctx＝這裡恆 no-op＝逐位元同現狀；只讀 _TURN、不遞迴 _say（入帳不發 ack）。
        _defer_self_promise = getattr(client, "defer_self_promise_capture", None)
        if callable(_defer_self_promise):
            _defer_self_promise(text, {
                "ctx": _TURN.get("self_promise_ctx"),
                "skip": _TURN.get("self_promise_skip"),
                "trace": _TURN.get("self_promise_trace"),
            })
        else:
            _maybe_self_promise_capture(text)
    # 🕐 §1.84 主動出聲的「今天記寫」守門（互動分支在上面已判；這裡補**主動**路徑＝prefix/state 那條）：
    # 今天其實零記寫、卻說「你今天早上又寫了…」→ 整句換程式算的事實句（與 §1.60 同一支純函式、同一份
    # ground＝不可能兩邊說法打架）。未 arm（旗標關）＝恆 no-op＝逐位元同現狀。
    if prefix or state is not None:
        _wgp = _TURN.get("write_claim_ground_proactive")
        if _wgp is not None:
            text, _wpch = _write_claim_fix(text, _wgp)
            if _wpch:
                print("[time] 🕐 §1.84 主動出聲記寫守門：今天其實沒有新記寫 → 換程式算的事實句")
    # ✒️ markdown 必須在**分串前**就清掉（在完整、成對的文字上清）——否則 `**…**` 跨越 bubble 邊界被切開後，
    # 每串只剩落單的 `**`，送出口的 per-bubble strip（成對才清）就漏掉、字面星號外洩（截圖根因）。
    text = notifier.strip_markdown(text)
    # 🧵 §1.54 互動回覆致意句去重（stash 缺席＝旗標關＝no-op）：近 150 秒才「嗯，我明白了。」過，這則的
    # 「我明白。」就別再疊（同輪內的重複也擋）；實質內容句不動；全刪光保留第一顆＝不失語。
    # 🔍 §1.57 審計修：去重必須在 _merge_bubbles 封頂**之前**——「我明白。」（≤6 字＝_is_fragment 碎句）
    # 會在超頂時被 merge 併進內容句，併完 canon 落不進家族＝去重失效（審計可重現缺陷 C）。旗標開＝
    # 先切不封頂 → 去重 → 再封頂；stash 缺席＝原單呼叫＝逐位元同現狀。
    _ack_recent = _TURN.get("ack_dedup_recent") if (not prefix and state is None) else None
    _paced = bool(_TURN.get("paced_bubbles", False) or getattr(state, "PACED_DIALOGUE", False))
    if _ack_recent is not None:
        bubbles = _ack_dedup(bubble_split(text, paced=_paced) or [""], _ack_recent)
        bubbles = (_cap_burst_delivery_bubbles(bubbles) if _paced
                   else _merge_bubbles(bubbles, _TURN.get("bubbles"))) or [""]
    else:
        bubbles = bubble_split(text, max_bubbles=_TURN.get("bubbles"),
                               paced=_paced) or [""]
    # 🔁 §1.71 跨路徑重播守門（arm 由 handle_message 依旗標 stash；未 arm＝恆 no-op＝逐位元同現狀）：
    # 剛（3 分鐘內）真的送出過的段落不准原樣再送——插話/接回機制的任何路徑重播到這裡都會被攔；
    # 全剝空＝換一句誠實短句（不無聲、也不重播）。
    # 🫧 §2.09 先做那件事：開頭的純接話泡泡確定性剝掉（互動回覆限定；旗標關＝不剝＝逐位元同現狀）
    # 🦜 §2.13 整則是「剛剛才一字不差說過的短句」→ 這次不送（連送三次「好，記下來了。」的那個病）。
    # 回 True：那句話幾十秒前才真的送達過，呼叫端的記帳語意不該因此變成「沒送出去」。
    # 🩹 §2.14 三道新閘一律**鎖互動分支**（`not prefix and state is None`，與 1478 行 ack_dedup 同構）：
    # §2.09/§2.10/§2.13 原本對**所有** _say 生效，而 arm 旗在 handle_message 設了之後沒人清 ⇒ 殘值洩進
    # 之後所有主動出口——與 §1.85 記過的 replay_guard 殘值病**一模一樣**（我自己又犯了一次）。
    # 最壞情境：🤝 守約兌現句是短句且 180 秒內同字 ⇒ 被 §2.13 吞掉、_say 回 True ⇒ **記成已兌現但什麼都沒送**
    # ＝把 §1.85 整套交付舉證白修。鎖分支後：帶 prefix/state 的主動與兌現路徑天然豁免。
    if _TURN.get("short_dup_guard") and not prefix and state is None and _short_dup_hit("".join(bubbles)):
        print("[say] 🦜 §2.13 這句短回應剛剛才一字不差說過 → 不再送一次")
        _stage_sent_model(_af_key, "")                    # 呼叫端仍會 _remember；明確空映射防止把未送原文記進歷史
        return True
    # 🧭 §2.27 被問座標卻沒有**完整且等於本輪快照**的 V/A pair → 附上程式算的那一行。
    # 舊版只看任一 x.xx：錯的一組、單軸、歷史值甚至無關小數都會讓後盾失效。
    _mdl = _TURN.get("mood_data_line") if (not prefix and state is None) else None
    if _mdl and not _mood_current_pair_present("".join(bubbles), _mdl):
        bubbles = list(bubbles) + [_mdl]
        _TURN.pop("mood_data_line", None)
        print("[mood] 🧭 §2.27 座標回答缺少本輪完整 current pair → 附上程式算的那一行")
    if _TURN.get("act_first") and not prefix and state is None:
        bubbles, _afn = _strip_lead_ack(bubbles)
        if _afn:
            # 🫧 §2.10 登記「原文 → 真的送出去的話」，讓 _remember 記進歷史的是後者（歷史 ≠ 送出＝§1.29 那族的病根）。
            # 🩹 §2.14 鍵改用**進場原文**（_af_key）：舊版在 markdown/時間標籤洗掉**之後**才取鍵，而 _remember 收到的
            # 是呼叫端的原字串 ⇒ 回覆帶 **粗體** 時整個替換靜默失效（實測 strip_markdown 會改字串）。改成 dict＝
            # 同一輪多次 _say 各自都登記得到，不再只留最後一筆。
            _TURN.setdefault("act_first_sent", {})[_af_key] = "".join(bubbles).strip()
            print(f"[act] 🫧 §2.09 開頭 {_afn} 顆純接話泡泡剝掉——先講那件事")
    if _TURN.get("replay_guard"):
        bubbles, _rph = _replay_filter(bubbles)
        if _rph:
            if not bubbles:
                # 🫸 §1.87 卡住情境下不用罐頭拒答——_REPLAY_FALLBACK 的「你想聽哪部分，我換個說法講？」
                # 正是把選擇權推回去（截圖 22:57）。改成誠實認卡（不無聲、不空問、不踢球）。
                # 🔁 §2.03 第三種情況：他把**同一個意思拆成連著兩則**送（截圖 13:57「50分鐘後…」＋「想到之後
                # 告訴我」），第二則的回覆自然會逐字重複第一則 ⇒ 整則被剝空 ⇒ 換上那句罐頭，讀起來像**他**
                # 要求重講、還把選擇權丟回去（「你想聽哪部分」）。人不會那樣講——上一句才剛答完，這裡只要一個
                # 很短的承接。旗標關／非同一波＝仍是原本那句＝逐位元同現狀。
                if _TURN.get("mood_contract_line"):
                    bubbles = []                         # 下面直接補 current；不要先塞「你想聽哪部分」踢球句
                elif _TURN.get("nudge_stuck"):
                    bubbles = [_STUCK_OWN_IT]
                elif _TURN.get("replay_same_wave"):
                    bubbles = [_same_wave_ack()]
                else:
                    bubbles = [_REPLAY_FALLBACK]
            print("[say] 🔁 §1.71 重播守門：剛送過的段落被攔下、不再原樣重播")
    # 🧭 replay 是這條管線裡最後一個會整顆刪泡泡的守門；座標 canonical 即使與三分鐘內逐字相同，
    # 使用者這輪仍明確問了數據，不能只剩「我剛說過」。用短 anchor 重驗／補回 current pair。
    _mcl = _TURN.pop("mood_contract_line", None)
    if _mcl and not _mood_current_pair_present("".join(bubbles), _mcl):
        bubbles = list(bubbles) + [_mcl]
        print("[mood] 🧭 §2.27 重播守門移除了座標 pair → 補回本輪 current anchor")
    # 座標/重播守門可能在初次 bubble_split 之後補上必要事實；同一波的 wire 契約要在
    # **所有出口守門完成後**再收一次，才能確實只呼叫一次 client.send，且不丟補回的事實。
    if _burst_one_wire and len(bubbles) > 1:
        bubbles = ["\n".join(b for b in bubbles if b)]
    # 🗣️ 一般主動對話不再把內部機制名牌（🫀背景自陳／🫧／🌀…）掛在使用者面前。
    # 原始 prefix 仍留給行動台帳辨認「是我哪一種選擇」，守約／外部來源／精確量測標記則保留問責性。
    _natural = bool(state is not None and getattr(state, "NATURAL_PROACTIVE_VOICE", False))
    prefix = dialogue_agency.visible_prefix(prefix, natural=_natural, topic=topic)
    _wire_lead = str(wire_lead or "")
    _wire_voice_fragments = None
    _transport_atomic_until = 0
    if _wire_lead:
        # 不可把無上限 data 直接擺在 voice 前面再交給 Notifier 截尾：那會靜默吃掉
        # 真正的回覆。這裡已經過所有 voice 守門，可以精確預留尾端容量。
        specs = _data_voice_payloads(_wire_lead, prefix + bubbles[0])
        first_payloads = [p for p, _v in specs]
        first_voice = [v for _p, v in specs]
        bubbles = first_payloads + list(bubbles[1:])
        _wire_voice_fragments = first_voice + list(bubbles[len(first_payloads):])
        _transport_atomic_until = len(first_payloads)
    else:
        bubbles[0] = prefix + bubbles[0]
    validator = getattr(client, "validate_voice", None)
    if callable(validator):
        whole = "\n".join(bubbles)
        checked = validator(whole)
        if checked != whole:
            bubbles = bubble_split(checked, paced=_paced)
            _wire_voice_fragments = None
            _TURN["last_reply"] = checked
        approve = getattr(client, "approve_bubbles", None)
        if callable(approve):
            approve(bubbles)
    typing = getattr(client, "send_typing", None)
    interrupt = getattr(client, "_interrupt", None)              # 連續說話時察覺插話 → 先回應再接回（沒設＝不打斷）
    icfg = getattr(interrupt, "cfg", None)
    # 🗣️ 開＝插話後遞迴判斷收尾（簡化暖收/說完）；關＝原樣續送（逐位元同現狀）。
    # 自發出聲相（statement_defer）強制關 rewrite：背景自陳/聯想要「說完整一句再記憶」，別被暖收截斷成半截（行為漂移防線）。
    rewrite = bool(getattr(icfg, "interrupt_rewrite_enabled", False)) and not getattr(interrupt, "statement_defer", False)
    depth = 0
    _skip = set()                        # 🧵 §1.49 插話後判定為「已說過」的殘句 index（旗標關＝恆空＝零影響）
    _LAST_SENT["text"] = ""              # 📦 §1.85 本次 _say 的送達紀錄歸零（純內部）
    ok, results, delivered, delivered_voice = True, [], [], []
    _initiative = None

    def _note_delivery(msg, result):
        """第一顆送達就入帳：下一顆前若被插話，巢狀回覆才看得到真的上一行動。"""
        nonlocal _initiative
        if getattr(client, "speculative_capture", False):
            return                                           # capture=True 不是 Telegram 真送達
        if not (result and state is not None and _raw_prefix and track_initiative
                and getattr(state, "DIALOGUE_AGENCY", False)):
            return
        if _initiative is None:
            _initiative = dialogue_agency.note_initiative(state, _raw_prefix, topic, msg, time.time())
            if _initiative is not None:
                dialogue_agency.extend_delivery(_initiative, message_id=result)
        else:
            dialogue_agency.extend_delivery(_initiative, msg, result)

    for i, b in enumerate(bubbles):
        if i in _skip:                                           # 🧵 §1.49 殘句去重：已說過的句子不重播
            continue
        if i and interrupt is not None and not (_wire_lead and i < _transport_atomic_until):
            # 送下一個**語意**串前才看插話。超長訊息被切成的 transport chunks
            # 是同一個回覆，必須連續送完，不可在資料與最後的 voice 之間插入另一輪。
            pending = interrupt.poll()
            if pending:
                interrupt.handle(pending)                        # 先優先回應插話
                depth += 1
                # 🌊 §1.14 敵意插話 → 直接收口（不橋接、不續送剩餘串、也不 wrap 濃縮）＝把話頭讓給對方。
                # 插話本身已由 interrupt.handle 巢狀回覆過（且該巢狀輪因 streak≥2 而 level 0/泡泡≤2）；
                # 氣頭上「我繼續說喔」接著講完＝自顧自（截圖 12:09-12:12 根因）。旗標關＝不進此分支＝逐位元同現狀。
                if _pending_hostile(pending, icfg):
                    break
                # 🧵 §1.49 插話後殘句取捨（INTERRUPT_TAIL_TRIM）：①道別輪（這輪在回道別——stash 在 interrupt
                # 物件上，_TURN 會被巢狀輪開頭清掉不能放那；或插話本身是道別）→ 答完插話就收口，不「我接著說」
                # 續客套尾巴（截圖 23:00：晚安輪還橋接續講）；②殘句對「這輪已送出＋剛巢狀回覆」去重——
                # 「我會記得的。」×2 不再原樣重播；殘句**全**被去掉＝連橋都不送（沒內容就別宣告要繼續）。
                # 去重在 rewrite 之前算：全 dup 直接收口、不進 wrap 濃縮（濃縮重複內容沒有意義）。
                # 旗標關（getattr 預設 False）＝不進此分支＝逐位元同現狀。
                if getattr(icfg, "interrupt_tail_trim_enabled", False):
                    if getattr(interrupt, "closing_turn", False) or _pending_farewell(pending, icfg):
                        break
                    _skip |= _interrupt_tail_dups(bubbles, i, getattr(interrupt, "state", None))
                    if all(j in _skip for j in range(i, len(bubbles))):
                        break
                    # 🔍 §1.57 審計修（缺陷 B）：本串是重複時**不再抄近路 bridge+continue**——那會繞過下面的
                    # rewrite/§1.50 判定（陳述插話該 wrap 的輪次被放回「橋＋原樣殘尾」＝§1.50 要壓的 pattern）。
                    # 改：照常過 rewrite 判定（wrap 用過濾後殘句）；resume 時橋照送、dup 串由迴圈尾的 continue 跳過。
                if rewrite:                                      # 🗣️ 依剩餘量遞迴判斷：簡化暖收(wrap) 或 說完(resume)
                    _live_rem = sum(1 for j in range(i, len(bubbles)) if j not in _skip)   # 🔍 §1.57：剩餘量以去重後計
                    action = decide_resume(_live_rem, i, _interrupt_kind(pending), depth,
                                           int(getattr(icfg, "interrupt_max_depth", 2)),
                                           statement_wrap=getattr(icfg, "interrupt_statement_wrap_enabled", False))   # 🧵 §1.50
                    if action == "wrap":                         # 不死板把剩餘原串全送完 → 改「濃縮剩餘＋淡收」把話頭交還對方
                        # 🗣️ 截圖根因：原本只送一句空收「嗯，就這樣囉」、把還沒說完的剩餘串整個丟掉＝不負責。
                        # 改：把**還沒送出的剩餘串**濃縮成一兩句精華＋自然淡收（不整段倒出、也不丟掉）。
                        # INTERRUPT_WRAP_CONDENSE_ENABLED=0 或無教練/失敗 → 退回原模板暖收（逐位元同現狀）。
                        # 🔍 §1.57 審計修（缺陷 A）：濃縮素材過濾 _skip——被 §1.49 判重複的殘句不得以濃縮形式又講一遍
                        # （旗標關＝_skip 恆空＝bubbles[i:] 原樣＝逐位元同現狀）。
                        remaining_text = "".join(bubbles[j] for j in range(i, len(bubbles)) if j not in _skip).strip()
                        msg = None
                        icoach = getattr(interrupt, "coach", None)
                        if (getattr(icfg, "interrupt_wrap_condense_enabled", True) and remaining_text
                                and icoach is not None and getattr(icoach, "enabled", False)):
                            try:
                                # 🎬 §1.69 收尾去制式：natural＝prompt 從「給範例（被逐字抄回＝每次同款收場白）」
                                # 改「禁令＋多元收法」（融語境/情緒餘韻/語助詞/不加收場白）。旗標關＝原 prompt＝同現狀。
                                msg = icoach.voice_wrap_condense(remaining_text, getattr(state, "convo_history", None),
                                                                 natural=getattr(icfg, "wrap_close_natural_enabled", False))
                            except Exception:
                                msg = None
                        if not (msg or "").strip():              # 退路：模板暖收（None/空/純空白都退、不送空泡泡）
                            msg = _pick_wrap_close(interrupt, icfg)   # 🎬 §1.69 旗標開＝依心情分池；關＝原四句池
                        # 濃縮句是 LLM 生成、直送（繞過 _say 頂端的清洗）→ 補洗 markdown／洩漏時間標籤（模板句本就乾淨、洗了無害）
                        msg = notifier.strip_markdown(coachmod.strip_leaked_time_tags(msg))
                        rb = client.send(msg)
                        if rb:
                            delivered.append(msg)
                            _replay_note(msg)                    # 🔁 §1.71 濃縮/暖收句也記（跨路徑重播防線的素材）
                            _note_delivery(msg, rb)
                        results.append(rb)
                        ok = rb and ok
                        break
                br = interrupt.bridge()                          # 再用一句「剛剛說到哪了…」接回（resume）
                if br:
                    rb = client.send(br)
                    if rb:
                        delivered.append(br)
                        _note_delivery(br, rb)
                    results.append(rb)
                    ok = rb and ok
                if i in _skip:                                   # 🔍 §1.57（缺陷 B 收尾）：resume 時本串是重複 → 橋已送、跳過本串
                    continue
        if (i and typing and not getattr(client, "dry_run", False)
                and not (_wire_lead and i < _transport_atomic_until)):   # transport chunks 不假裝成多次打字
            typing()
            _sleep(_typing_delay(len(b)) * _jitter())                 # 下一串越長＝等越久＋隨機拖拍（真人打字感）
        # Burst transaction 的 capture 必須分得出「資料前綴」與真正的人話，否則最後把多個
        # part 壓進 4096 units 時，公平節錄可能剛好把每段尾端的自然回覆剪掉。一般
        # Notifier 沒有這個 hook，行為完全不變；capture 只把 metadata 綁到緊接著的 send。
        _capture_meta = getattr(client, "capture_delivery_meta", None)
        if callable(_capture_meta):
            _voice_fragment = (_wire_voice_fragments[i]
                               if _wire_voice_fragments is not None and i < len(_wire_voice_fragments)
                               else b)
            _capture_meta(_voice_fragment, _mood_delivery_contract)
        try:
            r = client.send(b)
        except Exception as send_error:
            # 前一顆沒有送達就不能繼續送後半句；記憶也只記真正送達的前綴。
            print(f"[say] delivery stopped: {type(send_error).__name__}")
            r = False
        if r:
            actual_wire = getattr(client, "last_actual", None)
            if isinstance(actual_wire, str):
                b = actual_wire
            delivered.append(b)
            if _wire_voice_fragments is not None and i < len(_wire_voice_fragments):
                delivered_voice.append(_wire_voice_fragments[i])
            _note_delivery(b, r)
            _replay_note(b)                                      # 🔁 §1.71 記「真的送出去了」的泡泡（恆記、純內部）
            _short_note(b)                                       # 🦜 §2.13 短句也記（§1.71 刻意不記短句＝短 ack 的重複沒人擋）
            _LAST_SENT["text"] += "\n" + b                       # 📦 §1.85 同上，給送達舉證比對（恆記、純內部、不改輸出）
        results.append(r)
        ok = r and ok
        if not r:
            break
    _defer_proactive = getattr(client, "defer_proactive_delivery", None)
    if callable(_defer_proactive) and state is not None and topic and delivered:
        _defer_proactive(state, _raw_prefix, topic, "".join(delivered).strip(),
                          track_initiative=track_initiative)
    else:
        _record_self_msgs(state, results, topic, "".join(delivered).strip())
    # 每次送達都更新映射，包括主動訊息；不能沿用上次同文失敗的空映射。
    # 最終送達才是下一輪的歷史，所有出口守門都可能改掉進場原文。
    # data 可能被顯式節錄，helper 已標出各 transport chunk 的 voice，只記成功送達部分。
    _actual = ("".join(delivered_voice) if _wire_voice_fragments is not None
               else "".join(delivered)).strip()
    _stage_sent_model(_af_key, _actual, mood_contract=_mood_delivery_contract, message_ids=results)
    return ok and getattr(client, "evidence_blocked", False) is not True


def _ability_fired_now(state, cfg, key, now_ts):
    """`_ability_fired` 的實際 mutation；burst deferred callback 也只走這裡一次。"""
    hits = getattr(state, "ability_hits", None)
    if hits is None:
        hits = state.ability_hits = {}
    h = hits.setdefault(key, {"n": 0, "first_ts": 0.0, "last_ts": 0.0})
    h["n"] = int(h.get("n") or 0) + 1
    h["first_ts"] = h.get("first_ts") or now_ts
    h["last_ts"] = now_ts
    if not getattr(state, "ability_hits_since", 0):
        state.ability_hits_since = now_ts


def _ability_fired(state, cfg, key, now_ts):
    """🪪 §1.94 這個能力**真的被用出來了一次**——「用過沒有」的唯一真相（跨重啟）。

    為什麼非有不可：§1.93 證明「旗標開著」不等於「這個能力活著」——🔮 那條 lane 旗標一直開著，
    卻從上線起一次都沒觸發過，而**沒有任何機制能發現這件事**（per-flag 的使用計數覆蓋率實測是 0%）。
    這支就是把「活/死」從 0% 變成有據可查。key 命名空間刻意留白：Phase 1 用 roster key，
    之後要接守門閘可以直接用 "guard:timejump" 而不必另起爐灶。
    **必須掛在「送出成功」之後**——掛錯位置＝沒送出去也記一次＝盤點謊報（測試 W6 逐點釘死）。
    旗標關＝第一行 return＝不寫任何東西＝state 檔逐位元同現狀。"""
    if not getattr(cfg, "self_roster_enabled", False):
        return
    capture = getattr(state, "_burst_delivery_capture", None)
    defer = getattr(capture, "defer_delivery_action", None)
    if callable(defer):
        # `_say` 的 capture=True 只代表文字暫存在記憶體。能力使用次數必須等 final
        # bounded wire 仍完整含該次 payload 才可增加；真 send 失敗時 clone 整份丟棄。
        defer(lambda _wire: _ability_fired_now(state, cfg, key, now_ts),
              kind=f"ability:{key}")
        return
    _ability_fired_now(state, cfg, key, now_ts)


def _say_delivery(client, msg, state, cfg):
    """📦 §1.85 兌現訊息專用的送出（三個兌現出口共用）：送出期間**暫時解除**跨路徑重播守門，送完還原。
    為什麼要這道：`_TURN["replay_guard"]` 在每個互動輪被設 True（handle_message）卻**全檔沒有任何 pop**
    ⇒ 殘留洩進之後所有主動出口，於是主動兌現也在 _replay_filter 之下——驗收剛通過的交付內容可以被整顆
    剝掉，全剝空時還會被換成拒答句 _REPLAY_FALLBACK（「這段我剛剛才說過一次…」）＝答案生出來了卻沒送出。
    §1.41 守約去重複（獨立旗標、預設開）仍在上游擋逐字照抄，所以這裡解除不會放行真重複。
    旗標關＝直接呼叫 _say＝逐位元同現狀。"""
    if not getattr(cfg, "promise_delivery_proof_enabled", False):
        return _say(client, msg, prefix="🤝 ", state=state, topic="我答應你的約定")
    _rg = _TURN.get("replay_guard")
    _TURN["replay_guard"] = False
    try:
        return _say(client, msg, prefix="🤝 ", state=state, topic="我答應你的約定")
    finally:
        _TURN["replay_guard"] = _rg


# ── 統一對話記憶＋對話焦點：讓各路徑（自體狀態/🫧/🌀/事實/人話）的話都進同一份記憶，指代才接得上 ──
def _mood_contract_present(text, contract):
    """actual wire 是否真的含 contract 的同時刻 canonical V/A pair。"""
    if not isinstance(contract, dict):
        return False
    try:
        want = (f"{float(contract['v']):+.2f}", f"{float(contract['a']):+.2f}")
        at = str(contract.get("at") or "")
        current_segments = [s for s in re.split(r"(?<=[。！？!?\n])", str(text or "")) if at and at in s]
        return any(any(m.groups() == want for m in _MOOD_PAIR_RE.finditer(s))
                   for s in current_segments)
    except (KeyError, TypeError, ValueError):
        return False


def _remember(state, role, text, ts=None):
    """把一輪對話記進 convo_history（截短、留最後 20 輪）——讓教練 LLM 看得到剛剛各路徑說了什麼。
    每則存 epoch 時間戳 ts（預設真實現在）→ 餵給 LLM 時標相對時間，讓『多久前/我睡多久/多久沒聊』答得出。"""
    t = (text or "").strip()
    if not t:
        return
    # 🫧 §2.10 對話史必須等於**真的說出口的話**：§2.09 剝掉開頭的純接話泡泡之後，若這裡仍記完整原文，
    # bot 之後回頭讀自己的歷史會看到它其實沒說過的句子——那正是 §1.29 那一族「歷史 ≠ 真的說過的話」，
    # 而「誰在說話」的歸屬錯亂就是從歷史對不上開始的。_say 送出時把 (原文→真送出) 登記在 _TURN，這裡換掉。
    if role == "model":
        _sm = _TURN.get("sent_model_text")
        _found = False
        _mcd = None
        if isinstance(_sm, list):
            _key = _sent_memory_key(t)
            for _i in range(len(_sm) - 1, -1, -1):
                if _sm[_i].get("key") == _key:
                    _entry = _sm.pop(_i)
                    t = _entry.get("text") or ""
                    _mcd = _entry.get("mood_contract")
                    _found = True
                    break
        if not _found:
            _af = _TURN.get("act_first_sent")
            if isinstance(_af, dict) and _af.get(t):
                t = _af[t]
        if not t:
            return
        # 🧭 §2.27 只有「真的送達」且正文仍含本輪 canonical current pair 才能成為下一輪 repair 的證據。
        # contract 與這一筆 actual wire 綁在同一 sent_model_text entry；若中途被打斷、重播閘吞掉、
        # 或 current 的 at+pair 沒真的同句送出，就不寫這筆，後續也不能拿任意 trace 替它背書。
        if isinstance(_mcd, dict):
            try:
                if _mood_contract_present(t, _mcd):
                    state.mood_last_report = {
                        "ts": float(_mcd.get("ts") or time.time()),
                        "at": _mcd.get("at") or "",
                        "v": float(_mcd["v"]), "a": float(_mcd["a"]),
                        "mode": _mcd.get("mode") or "snapshot", "text": t[:240],
                    }
            except (KeyError, TypeError, ValueError):
                pass
    if role == "model" and len(t) > 200:
        t = t[:200] + "…"
    state.convo_history.append({"role": role, "text": t, "ts": time.time() if ts is None else ts})
    del state.convo_history[:-30]


def _session_gap_text(history, now_ts):
    """從近期對話時間戳（含『現在』）找出『最近一段較久的沉默』（相鄰間隔最大且 ≥1h）→ 一條算好的事實句；
    找不到回 ''。讓「我睡多久／多久沒聊」有確定數字依據，不靠 LLM 自己從標籤推算。"""
    tss = sorted([t.get("ts") for t in (history or []) if t.get("ts")] + [now_ts])
    if len(tss) < 2:
        return ""
    big = max(tss[i] - tss[i - 1] for i in range(1, len(tss)))
    if big < 3600:
        return ""
    return f"（已算好的事實：我們上一段對話到這次回來，中間隔了約 {temporal.human_gap(timedelta(seconds=big))}沒在聊。）"


def sessionize(history, now_ts, gap_sec=3600):
    """🕰️ 把近期對話（convo_history）依『相鄰兩則 ts 間隔 ≥ gap_sec』切成數段「會話」，回一條程式算好的
    『會話節奏』事實（grounded，相對量一律 `temporal.human_gap`、LLM 照講不自推）；資料不足／單段且剛說過 → ''。

    為什麼有幫助：時間節奏本身就攜帶意圖線索——同一段對話裡緊接的「為什麼」vs 隔三小時才回來問的「為什麼」，
    意味完全不同；跨會話回來時 bot 該先接住「你又回來了」而非當沒事接續。這條讓**所有**回覆路徑（不只 convo_time）
    都看得到會話結構（與 `_session_gap_text` 的「最久沉默」是不同事實、刻意分工：這裡講**分幾段＋這次回來距上一段**）。

    now_ts 一律傳 convo_now（與 `_session_gap_text`/`_last_bot_turn_gap_fact` 同一牆鐘來源，避免兩個「現在」漂移）。純函式、可單測。"""
    tss = sorted(t.get("ts") for t in (history or []) if t.get("ts"))
    if len(tss) < 2 or not now_ts:
        return ""
    seg_count, prev_gap = 1, None
    for a, b in zip(tss, tss[1:]):
        if b - a >= gap_sec:
            seg_count += 1
            prev_gap = b - a                 # 最後一個大停頓＝這次回來距上一段的間隔
    last_gap = max(0.0, now_ts - tss[-1])
    if seg_count < 2:                        # 都在同一段：只在「距上一句已久」時提醒這次是回頭接續
        if last_gap >= gap_sec:
            return f"我們最近這段對話是連續的；你距上一句已隔約 {temporal.human_gap(timedelta(seconds=last_gap))}才又開口。"
        return ""
    tail = (f"，到你這句約過了 {temporal.human_gap(timedelta(seconds=last_gap))}"
            if last_gap >= 3600 else "")
    return (f"我們近期的對話分成 {seg_count} 段（中間有較久的停頓）；最近這段是你隔約 "
            f"{temporal.human_gap(timedelta(seconds=prev_gap))}之後又回來開的{tail}。")


def _last_bot_turn_gap_fact(history, now_ts):
    """『你剛說的多久前／你上一句是多久前說的』問的是**最近一則 bot（model）訊息距今多久**——
    與 _session_gap_text（最大沉默段）語意不同。回一條程式算好的事實句；history 無 model 訊息→回 ''
    （讓 convo_time 路徑落到誠實 unknown：『我不太確定那是多久前』，不讓 LLM 從現有標籤自推、指錯對象）。"""
    bot_ts = [t.get("ts") for t in (history or []) if t.get("role") == "model" and t.get("ts")]
    if not bot_ts:
        return ""
    gap = max(0.0, now_ts - max(bot_ts))
    return f"（已算好的事實：我上一句話距現在約 {temporal.human_gap(timedelta(seconds=gap))}。）"


def _recent_model_turns(history, now_ts, window_sec, k=3):
    """🔁 §0.56：取『這幾分鐘內』最近 k 則 bot（model）回覆的文字（相鄰不久＝window_sec 內）。
    ⚠️ 逐則**個別**判窗、**跳過**（非 break）超窗者——因 convo_history 的 ts 混用時鐘（user 存 message.date、model 存
    time.time()），崩潰補抓/快速連發時序可能非單調，用 break 會被夾在中間的超窗 user 輪過早截斷、漏掉真正在窗內的 model 輪
    （對抗式審查 med）。history 只留最近 30 則、掃全程成本可忽略。window_sec≤0 → 回 []（0 分＝關此提示、非無限窗）。
    回 list（時序，舊→新）；無則回 []。純函式、可單測。"""
    if window_sec <= 0:
        return []
    out = []
    for turn in reversed(history or []):
        ts = turn.get("ts") or 0
        if now_ts and (now_ts - ts) > window_sec:
            continue                              # 超窗 → 跳過（不 break：ts 非單調，後面可能還有窗內的 model 輪）
        if turn.get("role") == "model":
            txt = (turn.get("text") or "").strip()
            if txt:
                out.append(txt)
                if len(out) >= k:
                    break
    return list(reversed(out))


# 🫸 §1.87 催促＝要我現在做（NUDGE_DELIVER）。截圖 22:54–22:57 根因：使用者連丟五次「所以呢？」，
# bot 每次都誠實**對帳**（「我還欠你一次猜測，對不對」講了三遍）、然後把球踢回去（「我在等你決定什麼時候
# 要我再猜一次呀」）、最後被重播守門換成罐頭拒答（「你想聽哪部分，我換個說法講？」）——一次都沒真的去猜。
# 實測：selfstate.promise_status_kind('所以呢？') 回 'outcome' ⇒ 偵測器**認得**這句話，只是被路由去
# §1.13A 的「據帳本誠實對帳」。§1.13A 擋掉了「我做到了」的謊（成功了），但**誠實對帳連講四次就是新形狀的
# 光說不做**。§1.85 的回覆橋只救得到帳本裡 status=='owed' 的筆；21:19 那筆在舊碼下已被標 fulfilled，
# 而「第四次猜測」這件事是使用者在對話裡建立的、帳本裡根本沒有 ⇒ 橋不觸發、催促永遠只換到一次對帳。
_STUCK_MIN_LEN = 8           # 太短的回覆（嗯／我不知道）合理重複，不判卡住
_STUCK_RATIO = 0.85          # 兩則回覆「近乎相同」的相似度門檻


# 🫧 §2.09 先做那件事（ACT_FIRST）：截圖 13:46–14:29 使用者連催八次（「你怎麼這麼多廢話」「猜啊」
# 「你就直接猜吧」），每一輪的回覆都先來一到三顆**純接話**的泡泡才進正題——「好，我明白了。」「喔，好！」
# 「抱歉！」「嗯，好，你說要我再根據你的記寫內容猜一次。」。使用者原話：「拖泥帶水」。
#
# 為什麼既有三道閘全瞎（實測）：`selfstate.promise_status_kind` 對那八句**全回 ''**（它是承諾帳本的分型器，
# 不是通用催促偵測）；§1.87 `_stuck_under_nudge` 要求「窗內最近兩則 bot 回覆近乎相同」，而它每次是用
# **不同的話**拖延；而且 `nudge_stuck` 只在**承諾帳本路由**才 arm，這整段根本不走那條路由。
#
# ⚠️ 實測踩到的陷阱：不能用「這顆泡泡帶多少新字」當判準——真正的答案「那我猜，你是……處女座？」新字只有 6，
# 而純宣告「好，那我就隨便猜一個喔。」有 9 ⇒ 那樣會**剝掉答案、留下廢話**。所以只認**閉集的接話詞**：
# 整顆泡泡去掉標點與這組詞之後**什麼都不剩**，才算純接話。這是虛詞類（discourse marker），不是開放的內容詞表。
_ACK_ONLY = ("好", "嗯", "喔", "噢", "欸", "呃", "唉", "哦", "啊", "是", "對", "沒問題", "收到", "了解",
             "我明白了", "我明白", "我知道了", "我知道", "抱歉", "對不起", "不好意思", "當然", "行", "OK", "ok")
# 🦜 §2.13 短句 ack 的重複守門：§1.71 重播守門刻意**不記** `_REPLAY_MIN_LEN`(10) 字以下的泡泡
# （原意是「嗯／好」這種合理的短重複不該被擋），代價是**整則就只有一句短 ack 時，它完全在防線外**——
# 截圖 20:15–20:16 連送三次「好，記下來了。」（正規化後 5 字）。使用者：「重複說同一句話兩次」。
# 修法：整則很短、且與近窗內送出過的**正規化全等** ⇒ 這一次不送（同樣的話剛剛才說過，再說一次只是噪音）。
# 刻意只認**全等**（不用相似度）：短句本來就容易誤判，寧可漏擋也不要把不同的短回應吃掉。
_SHORT_DUP_SEC = 180        # 這麼久內送過一模一樣的短句就不再送
_SHORT_SENT = []            # [(norm, ts)]（模組級、跨輪跨路徑；重啟歸零無妨——窗只有 3 分鐘）


def _short_dup_hit(text, now_ts=None):
    """🦜 §2.13 這則是不是「剛剛才一字不差說過的短句」。純函式（讀模組級 ring）。"""
    n = echo._norm(_KEEP_GLYPH_RE.sub("", text or ""))
    if not n or len(n) > _REPLAY_MIN_LEN:
        return False                                       # 夠長的由 §1.71 重播守門負責
    t = time.time() if now_ts is None else now_ts
    return any(pn == n and (t - pt) <= _SHORT_DUP_SEC for pn, pt in _SHORT_SENT)


def _short_note(text, now_ts=None):
    """🦜 §2.13 記下真的送出去的短句（長的不記＝交給 §1.71）。"""
    n = echo._norm(_KEEP_GLYPH_RE.sub("", text or ""))
    if not n or len(n) > _REPLAY_MIN_LEN:
        return
    t = time.time() if now_ts is None else now_ts
    _SHORT_SENT.append((n, t))
    del _SHORT_SENT[:-24]


_MOOD_NUM_RE = re.compile(r"[+-]\d+\.\d\d")     # 🧭 §2.10 V/A 那種帶正負號的兩位小數＝真的把數字講出來了
# 🧭 §2.21 「在場判定」用寬版：LLM 說「V 降了 0.46」（不帶號）也是把數字講出來了——舊判定要求帶號，
# 實測 22:23 第一顆泡泡已有「降了 0.46、降了 0.35」，§2.20 仍補一行括號 Δ＝同資訊講兩遍。
_MOOD_NUM_ANY_RE = re.compile(r"\d\.\d\d")
_MOOD_PAIR_RE = re.compile(r"V\s*([+-]\d+\.\d{2})\s*[、,／/]\s*A\s*([+-]\d+\.\d{2})")
_ACK_TRIM_RE = re.compile(r"[，,。.！!？?～~…、\s]+")


def _mood_current_pair_present(text, canonical_line):
    """回覆是否真的含補救行指定的完整 current V/A pair（不是任一無關小數／單軸／歷史值）。"""
    truth = _MOOD_PAIR_RE.search(canonical_line or "")
    if not truth:
        return False
    return any(m.groups() == truth.groups() for m in _MOOD_PAIR_RE.finditer(text or ""))


def _ack_only_bubble(b, cap=12):
    """🫧 §2.09 這顆泡泡是不是**純接話**（零資訊）：去掉標點與閉集接話詞後什麼都不剩。
    太長的一律不算（避免誤剝真內容）。純函式。"""
    t = _ACK_TRIM_RE.sub("", _KEEP_GLYPH_RE.sub("", b or ""))
    if not t or len(t) > cap:
        return False
    for w in sorted(_ACK_ONLY, key=len, reverse=True):
        t = t.replace(w, "")
    return not t


def _strip_lead_ack(bubbles, max_strip=2):
    """🫧 §2.09 剝掉**開頭連續**的純接話泡泡（最多 max_strip 顆、至少保留一顆）→ (新泡泡, 剝了幾顆)。
    只動開頭：中間/結尾的「好」常是真的在回應，不碰。純函式。"""
    out, n = list(bubbles or []), 0
    while len(out) > 1 and n < max_strip and _ack_only_bubble(out[0]):
        out.pop(0)
        n += 1
    return out, n


def _stuck_under_nudge(history, now_ts, window_sec=900, ratio=_STUCK_RATIO):
    """🫸 §1.87 **零詞表的結構訊號**：窗內最近兩則 bot 回覆彼此近乎相同 ⇒ 我在原地重複、沒有前進。
    配上「這句是催促」就等於「他一直在催、我一直在重複」＝卡住的鐵證，也正是截圖的形狀。

    為什麼不靠語意：要判斷「使用者這句暗示我現在就做」本質很難、且任何詞表都會漏（本專案已漏 15+ 次）；
    但「我剛剛講過一樣的話」是**可確定性計算**的，而且它才是真正該觸發行動的訊號——使用者會催第二次，
    正是因為第一次的回答沒讓事情前進。

    刻意用相似度（echo._looks_same）而不是逐字全等：實測 §1.71 重播守門用的是 `n == pn` 全等，於是
    「所以... 我還欠你一次猜測，對不對」／「嗯... 我還欠你一次猜測，對不對」／「我還欠你一次猜測，對不對」
    三句**只差開頭一個語助詞就全部繞過**（三組 _looks_same(0.8) 皆為 True、全等皆為 False），
    要等到第四次真的逐字重複才被攔。純函式、可單測。"""
    turns = _recent_model_turns(history, now_ts, window_sec, k=3)
    if len(turns) < 2:
        return False
    a = echo._norm(_KEEP_GLYPH_RE.sub("", turns[-1]))
    b = echo._norm(_KEEP_GLYPH_RE.sub("", turns[-2]))
    if len(a) < _STUCK_MIN_LEN or len(b) < _STUCK_MIN_LEN:
        return False
    return echo._looks_same(a, b, ratio)


def _routine_voice_hint(state, cfg):
    """📈 §1.96 講他作息時的說法守則（事實卡給數字，這支管怎麼講：主詞是他、我記到的口吻、
    樣本少講成自己的限制）。旗標關＝''＝逐位元同現狀。"""
    return persona.ROUTINE_VOICE_HINT if getattr(cfg, "routine_card_enabled", False) else ""


def _nudge_deliver_hint(state, cfg, now_ts):
    """🫸 §1.87 卡住時注入「現在就做、別再報告狀態、別把球踢回去」的守則（由 handle_message 判定後 stash）。
    旗標關／沒卡住＝''＝逐位元同現狀。"""
    if not getattr(cfg, "nudge_deliver_enabled", False) or not _TURN.get("nudge_stuck"):
        return ""
    return persona.NUDGE_DELIVER_HINT


# 🫸 §1.87 「把球踢回去」樣式（**輸出端**守門，比照 _KEEP_CLAIM_RE/_sticker_claim_hit 的既有慣例）：
# 使用者正在催我做事、我卻把選擇權推回給他＝最惹人厭的那一種不作為（截圖 22:56「我在等你決定什麼時候要我
# 再猜一次呀」、22:57「你想聽哪部分，我換個說法講？」）。**只在卡住情境下**開火，平常問「你想先聊哪個」
# 是正常的對話邀請、不攔。刻意不收「A 還是 B」這種**具體二選一**（那正是卡住時我們要它做的事）。
_BALL_BACK_RE = re.compile(
    r"我(?:在|還在)等(?:你|妳)(?:決定|說|開口|告訴我|指定|挑)"
    r"|(?:你|妳)想聽哪(?:一)?(?:部分|段|個|方面)"
    r"|(?:你|妳)(?:要不要|想不想)(?:先)?(?:自己)?(?:決定|挑|說)"
    r"|(?:就|都)(?:看|由)(?:你|妳)(?:決定|說)"
    r"|(?:你|妳)決定(?:就好|吧|了再)")
_STUCK_OWN_IT = ("我卡住了——同樣的話我連講了兩次，那不算回答你。"
                 "這是我的問題，不是你沒講清楚。")


def _ball_back_strip(msg):
    """🫸 §1.87 剝掉「把球踢回去」的句子（句級更正優先＝§1.62/§1.77 慣例）→ (定稿, 有沒有剝)。
    全剝空＝換成誠實認卡句（不無聲、也不用一個空問法把選擇權再推回去一次）。確定性、可單測。"""
    parts = [p for p in _WAKE_SENT_SPLIT_RE.findall(msg or "") if p.strip()]
    if not parts:
        return msg, False
    kept = [p for p in parts if not _BALL_BACK_RE.search(p)]
    if len(kept) == len(parts):
        return msg, False
    out = "".join(kept).strip()
    if len(echo._norm(out)) < 6:
        return _STUCK_OWN_IT, True
    return out, True


def _anti_repeat_hint(state, text, now_ts, cfg):
    """🔁 §0.56 回覆前的防重複提示（回一段附到 extra_system 的字串，可空）：
    ① 連發多句被合併成一輪（text 含換行／多個問句）→ 附「合起來答一次、重疊別逐句重講」；
    ② 這幾分鐘內剛回過（相鄰不久的輪）→ 附最近幾則 bot 回覆摘錄「別重講、只補沒說到的」。
    只作用在相鄰不久的輪；旗標關＝回 ''＝逐位元同現狀。"""
    if not getattr(cfg, "anti_repeat_enabled", True):
        return ""
    parts = []
    body = (text or "").strip()
    # 多句合併（換行）或多問號＝一次問了好幾句；但**要真的帶問句線索**才附「合起來答一次」提示——避免多行純陳述
    # （「今天去了寺廟\n覺得很平靜\n想跟你說」）被誤當多問而套上答一次提示（對抗式審查 low）。
    multi_shape = ("\n" in body) or (body.count("？") + body.count("?") >= 2)
    has_q = any(q in body for q in ("嗎", "呢", "什麼", "為什麼", "為何", "怎麼", "怎會", "?", "？", "特別", "還是"))
    if multi_shape and has_q:
        parts.append(persona.ANTI_REPEAT_MULTI_HINT)
    win = max(0, getattr(cfg, "anti_repeat_window_min", 8)) * 60
    recent = _recent_model_turns(getattr(state, "convo_history", None), now_ts, win, k=3)
    if recent:
        block = "\n".join(f"‧{r[:60]}" for r in recent)
        parts.append(persona.anti_repeat_recent_hint(block))
    return "\n".join(p for p in parts if p)


def _convo_clock_fact(history, tz, now_ts, max_turns=30):
    """⏱ 把**整段保留的對話**（convo_history 全部，預設上限 30＝與 _remember 留存同步）各則的**絕對鐘點**
    （本地時間 HH:MM）算成一條接地事實——讓「對話的時間點／剛剛那幾句幾點／你有記下時間嗎」答得出具體幾點幾分。
    **必須覆蓋整段、不只最近幾則**：使用者常問的是更早（如「約20分前」沮喪那幾句）的訊息，那些雖仍在 history
    （帶〔相對時間〕標籤餵給 LLM），但若鐘點事實只列最近幾則就對不到→ bot 只能答相對、答不出絕對（截圖根因）。
    每則配上**與對話裡看到的〔相對時間〕同源的標籤**（如 17:23（20分前）），讓 LLM 用相對標籤把鐘點對齊到
    那幾句、不必重述內容。無 tz／無帶 ts 的訊息 → 回 ''（讓路徑落到誠實 unknown）。純函式、可單測。"""
    if tz is None or not history:
        return ""
    rows = []
    for turn in history[-max_turns:]:
        ts = turn.get("ts")
        if not (turn.get("role") in ("user", "model") and ts):
            continue
        clk = datetime.fromtimestamp(ts, tz).strftime("%H:%M")
        who = "你" if turn.get("role") == "user" else "我"
        rows.append(f"・{clk}（{coachmod._rel_time(max(0.0, now_ts - ts))}）{who}說的")
    if not rows:
        return ""
    return ("（這段對話各則的鐘點，本地時間，與你在對話裡看到的〔相對時間〕一一對應；"
            "照講、別自己加減換算）：\n" + "\n".join(rows))


def _condense_fact(data):
    """事實答覆（📂/📏/🕐/📊/💸…清單）只記第一行（帶主題/時間指涉物），不把整串清單塞進記憶。"""
    return (data or "").split("\n", 1)[0]


def _set_focus(state, topic=None, fresh=None, now_ts=0):
    """更新對話焦點（剛 surface 的主線／有無新東西）；topic/fresh 為 None 則保留原值。"""
    f = dict(getattr(state, "focus", None) or {})
    if topic is not None:
        f["topic"] = topic
    if fresh is not None:
        f["fresh"] = fresh
    f["ts"] = now_ts or f.get("ts", 0)
    state.focus = f


# 指涉解析（「指什麼」）已收斂到 referent 模組：對話焦點＋bot 自己的近期狀態/動作＋剛自陳可追問，
# 算成一份 Referent，供路由 intent.resolve(text, ref) 與餵 LLM build_memory_brief(focus=/selfacts=) 共用。


REACTION_WINDOW_SEC = 5 * 60      # 收到貼圖後多久內的下一則回覆，仍把它的情緒當語氣參考
REACT_SEND_COOLDOWN_S = 90        # bot 主動對對方訊息按 emoji 的最短間隔（保持特別、不每句都貼；REACT_COOLDOWN_S 可調）
REACT_SELF_COOLDOWN_S = 30 * 60   # 「按自己內在情緒」的較長自有冷卻（更稀有；REACT_SELF_COOLDOWN_S 可調）
CHAT_NOURISH = 0.2                # 對話餵養：一次對話釋放多少飢餓 H（被陪伴就不那麼悶，不只靠新記寫）
CONNECT_WINDOW_SEC = 12 * 60      # 剛剛還在對話（這麼久內）→ 自陳/狀態/體驗要承接前文脈絡；更久＝可直接清新


def _connect_hint(state, now_ts, current=None):
    """若剛剛還在對話（窗內）→ 給自陳/狀態/體驗一段『先承接或轉折前文脈絡與口吻，再講狀態』的指引
    （附上對方最後一句）；對話隔久了回 ''（清新即可，不必硬銜接）。
    `current`＝**這次互動對方剛說的那句**：互動路徑一定要傳——它此刻還沒進 `convo_history`（要等回完才 `_remember`），
    不傳就會抓到**上一輪**的使用者訊息，承接錯對象、答非所問（例：上輪誇獎→這輪問感受，卻開頭又「謝謝你這麼說」）。
    主動推播沒有「當前訊息」→ 不傳，退回對話史最後一句使用者訊息（與上次聯絡銜接）。"""
    last_user_ts = getattr(state, "last_user_msg_ts", 0) or 0
    if now_ts - last_user_ts >= CONNECT_WINDOW_SEC:
        return ""
    last_user = current or next((t.get("text") for t in reversed(getattr(state, "convo_history", None) or [])
                                 if t.get("role") == "user" and t.get("text")), None)
    if not last_user:
        return ""
    # 🧭 納入 user-prior 的時間感：相對時間由程式算好（temporal.spoken_gap，<60s→『剛剛』不外露『0 分鐘』）。
    # _connect_hint **只管 user-prior**（對方上一句）——『我自己剛說過 X』一律走 _self_prior_fact 單一出口，
    # 不在此再注入第二份「我剛說過」，免得兩條管道措辭/時間量打架。窗內最大 gap < 12min＝只到『約 N 分鐘』。
    when = temporal.spoken_gap(max(0.0, now_ts - last_user_ts)) if last_user_ts else "剛剛"
    when_phrase = "你們剛剛還在對話" if when == "剛剛" else f"你們{when}還在對話"
    return (f"〔承接前文〕{when_phrase}，對方最後說的是「{last_user[:60]}」。"
            "請**用你自己的話**先自然地承接或轉折那個脈絡與口吻（哪怕一句），再講你此刻的狀態/感覺——"
            "**絕對不要把對方那句話照抄或原樣覆述出來當開頭**（那會像鸚鵡學舌、答非所問）；要有銜接感、"
            "別突兀地直接跳成報狀態。")


def _self_prior_fact(state, now_ts):
    """🪞 對話連貫的 self-prior 安全網：把「我上一句剛說過的那個感覺 X（針對焦點 Y、約 N 前）」
    算成**一條程式算好的事實句**，注入自體狀態渲染端，讓 bot 被追問細節時承接自己剛說的、別否認、別跳線、別報相反方向。

    【權威源唯一性（本案核心防線）】感覺詞 X **只能**從 convo_history 末則 role=='model' 的**真實文本**擷取
    （affect.extract_feeling_word），**絕不**從 affect/workspace/topic-reading 取——那些每拍漂移、會系統性抽到相反方向。
    抽不到感覺詞、無 model 句、或無 ts → 回 ''（誠實 unknown，不硬填、不退用結構欄位的當前值）。

    時間感：用該 model 句的 ts 算口語相對時間（temporal.spoken_gap，<60s→『剛剛』不外露『0 分鐘』），
    **不受 _connect_hint 的 12 分鐘窗約束**——跨段追問（隔了幾小時才回頭問）才有意義。純函式、只讀 state、不碰 LLM/IO。"""
    hist = getattr(state, "convo_history", None) or []
    last_model = next((t for t in reversed(hist) if t.get("role") == "model" and t.get("text")), None)
    if not last_model:
        return ""
    word = affect.extract_feeling_word(last_model.get("text") or "")
    if not word:
        return ""                                   # 抽不到感覺詞 → 誠實 unknown，不從漂移源退取
    ts = last_model.get("ts")
    if not ts:
        return ""                                   # 缺 ts → unknown，不誇飾
    when = temporal.spoken_gap(max(0.0, now_ts - ts))
    frame = temporal.spoken_frame(max(0.0, now_ts - ts))
    focus = (getattr(state, "focus", None) or {}).get("topic")
    target = f"針對〔{focus}〕、" if focus else ""
    lead = {"just": "我緊接著剛說過", "earlier": "我之前（{when}）說過".format(when=when),
            "long": "我很久以前（{when}）說過".format(when=when)}.get(frame, f"我{when}說過")
    return (f"〔我自己剛說過的（程式算好的事實，以它為準先承接）〕{lead}：{target}我覺得「{word}」。"
            "對方接著追問細節/原因時，**接回這個「" + word + "」、接回這條線**——別否認自己說過、"
            "別反問對方「你說我" + word + "嗎」、別報出與「" + word + "」相反方向的狀態、別跳到別條當下最強的內在線。"
            "若是隔了一陣才回來（約 N 小時/天前），用『回到之前那個…』這種帶時間距離的承接；剛剛才說的就直接接回。")


def _maybe_react(update, text, state, client, cfg):
    """bot 對「對方那則訊息」按一個 emoji reaction，**鏡像那則訊息表達出來的情緒**——暖意🥰／誇讚🔥／好笑😁／
    低落🤗／認同👍（讚）／好消息🎉…（節流；指令/空訊息不按）。**情緒不鮮明就不按**：絕不拿 bot 自己當下的心情
    去點對方的中性訊息（reaction 是對『這句話』的回應，不是 bot 心情的出口——心情走回話語氣與自家貼圖）。"""
    if not text or text.startswith("/") or getattr(cfg, "dry_run", False):
        return
    mid = (update.get("message") or {}).get("message_id")
    if not mid:
        return
    now_ts = _now_from_update(update).timestamp()
    if now_ts - (getattr(state, "last_react_sent_ts", 0) or 0) < max(0, getattr(cfg, "react_cooldown_s", REACT_SEND_COOLDOWN_S)):
        return
    emoji = reaction.pick_reaction(text, getattr(state, "vitality", None))   # 鏡像對方這句話的情緒；中性→None→不按
    if emoji and getattr(client, "set_reaction", None) and client.set_reaction(mid, emoji):
        state.last_react_sent_ts = now_ts
        state.last_sent_reaction = {"emoji": emoji, "to": (text or "")[:50], "ts": now_ts}  # 記住自己點了什麼、對哪句
        if not cfg.dry_run:
            state.save()


def sticker_signal(sticker, update, state, cfg, client=None, coach=None):
    """純『情緒訊號』段（從 _handle_sticker 抽出，重構等價、行為逐字不變）：把一張貼圖讀成此刻情緒訊號
    並更新內在狀態——last_reaction 設定＋學起這張真貼圖（_remember_sticker）＋心情疊加＋醞釀/已伸手歸零＋
    被肯定的正向擾動。**不含 _say、不含 save**＝純計算/狀態更新，可被連發合併層複用以「預更新」非末張貼圖
    （避免在合併層複製訊號邏輯造成真相漂移；這是對不變式『不 churn handle_message 路由本體』的受控明示例外）。
    給 client/coach 時順手『收到即讀』這張貼圖畫面、把描述一起記進記憶（送出時能誠實引用）；不給＝純計算（測試/無網路）。"""
    now_ts = _now_from_update(update).timestamp()
    emoji = (sticker or {}).get("emoji") or ""
    val = reaction.read_sticker(emoji)
    state.last_reaction = {"emoji": emoji, "valence": val, "ts": now_ts}
    desc = _describe_sticker(sticker, client, coach, state, cfg) if (client is not None or coach is not None) else None
    _remember_sticker(state, (sticker or {}).get("file_id"), emoji, val, now_ts,
                      (sticker or {}).get("file_unique_id"), desc)  # 學起這張真貼圖＋畫面描述（心情好時回送、送出知道送了什麼）
    state.last_user_msg_ts = now_ts                       # 貼圖也是聯絡＝陪伴
    if state.entropy is not None:                         # 這段獨處的醞釀/已伸手歸零（剛互動過就不想伸手問好）
        state.entropy.self_stims_this_idle = 0
        state.entropy.reach_outs_this_idle = 0
        state.entropy.coping_reach_outs_this_idle = 0   # 🌀 §0.65 剛互動＝內在因應伸手預算也歸零
        _md = reaction.sticker_mood_delta(val) * getattr(cfg, "mood_gain", 1.0)   # 貼圖→心情（幅度受 MOOD_GAIN）
        if _md:                                           # 正面貼圖→心情變好、負面→一起沉一點
            state.entropy.mood = max(-1.0, min(1.0, state.entropy.mood + _md))
        _bump_arousal(state, cfg, reaction.sticker_affect_delta(val)[1])   # 🧭 circumplex：貼圖也推喚起軸
    if val == "positive":                                # 被肯定＝一點正向內在擾動（S 起伏）
        state.tempo_charge_pending = max(getattr(state, "tempo_charge_pending", 0.0) or 0.0, 0.25)
    return emoji, val


def _handle_sticker(sticker, update, coach, state, client, cfg):
    """純貼圖訊息＝對方此刻的情緒訊號：自然回一句（語氣對應）＋記為最近反應＋當作一次陪伴接觸。
    ＝ sticker_signal()（訊號）＋ ack（口語回應）＋ save 三段組合（重構後行為逐字不變）。"""
    emoji, val = sticker_signal(sticker, update, state, cfg, client=client, coach=coach)  # 情緒訊號＋收到即讀畫面
    desc = _sticker_desc_for(state, (sticker or {}).get("file_id"))   # 剛存進記憶的那句畫面描述（若有）
    ack = coach.voice_sticker_ack(emoji, val, state.convo_history, desc=desc) if (coach and coach.enabled) \
        else {"positive": "嘿，謝啦 🙂", "negative": "嗯，我在這。"}.get(val, "收到你的貼圖 🙂")
    _say(client, ack)
    # ⏱ 貼圖也是 user-turn：以 message.date 落 ts（與文字 turn 同源、崩潰重抓仍穩定）；旗標關＝退回 time.time()。
    _remember(state, "user", f"（貼圖：{emoji or '一個表情'}{('——' + desc) if desc else ''}）",
              ts=(_now_from_update(update).timestamp() if getattr(cfg, "remember_user_msgdate", True) else None))
    _remember(state, "model", ack)
    if not cfg.dry_run:
        state.save()


def _remember_sticker(state, file_id, emoji, valence, ts, file_unique_id=None, desc=None):
    """記住對方傳過的真貼圖（之後 bot 心情好時可回送同一張）；連同它的畫面描述 desc（收到時視覺讀的）一起存，
    這樣送出時 bot 是真的知道自己送了什麼。去重（同 file_id 更新到最近）、最多留 24 張。desc 缺就沿用舊值（不覆寫成空）。"""
    if not file_id:
        return
    prev = next((s for s in (getattr(state, "known_sticker_ids", None) or []) if s.get("file_id") == file_id), None)
    pool = [s for s in (getattr(state, "known_sticker_ids", None) or []) if s.get("file_id") != file_id]
    kept_desc = desc or (prev or {}).get("desc") or ""      # 這次沒讀到畫面 → 保留上次讀到的，別退化成空
    pool.append({"file_id": file_id, "file_unique_id": file_unique_id or (prev or {}).get("file_unique_id") or "",
                 "emoji": emoji or "", "valence": valence, "ts": ts, "desc": kept_desc})
    state.known_sticker_ids = pool[-24:]


def _describe_sticker(sticker, client, coach, state, cfg):
    """🎴 讀一張貼圖的『畫面語意』→ 一句描述（收到即讀、之後送出可誠實引用）。
    快取於 state.sticker_descs[file_unique_id]（跨重啟存活）→ 同一張只讀一次、重送免費。
    只有**靜態**貼圖走 Gemini 視覺（client.download_file＋coach.read_sticker_image）；動態／影片／視覺關閉／
    無能力／下載或讀圖失敗 → 退回 stickervision.fallback_note 的誠實文字（明講『未讀畫面』、不假裝看到）。
    只寫描述快取、不碰其他狀態；失敗一律安全退回、不擋對話。"""
    s = sticker or {}
    uid = s.get("file_unique_id")
    cache = getattr(state, "sticker_descs", None)
    if cache is None:
        cache = {}
        state.sticker_descs = cache
    if uid and cache.get(uid):
        return cache[uid]                                  # 已用視覺讀過（含跨重啟）→ 直接用，不再花視覺
    if (getattr(cfg, "read_sticker_vision", True) and stickervision.is_static(s)
            and coach and getattr(coach, "enabled", False)
            and getattr(coach, "read_sticker_image", None)
            and getattr(client, "download_file", None)):
        blob = client.download_file(s.get("file_id"))
        desc = coach.read_sticker_image(blob, stickervision.mime_for(s) or "image/webp") if blob else None
        if desc:
            if uid:
                cache[uid] = desc
                if len(cache) > 200:                       # 上限保護：超量丟最舊插入的鍵
                    for k in list(cache)[:len(cache) - 200]:
                        cache.pop(k, None)
            return desc
    return stickervision.fallback_note(s)                  # 讀不到畫面 → 誠實粗描述（不快取，容後可重試）


def _sticker_desc_for(state, fid):
    """🎴 查一張 file_id 在 known_sticker_ids 記著的**畫面描述**（收到時視覺讀到的；未讀/未知＝''）。
    給收到 ack 接地、與送出時『知道自己送了什麼』的接地共用。純讀。"""
    for k in (getattr(state, "known_sticker_ids", None) or []):
        if k.get("file_id") == fid:
            return k.get("desc") or ""
    return ""


def _maybe_sticker(client, state, cfg, now_ts, reply_text=None):
    """🎴 偶爾在回話後吐一張**真貼圖**（強化互動感）：兩種觸發（皆走自有冷卻保持稀有）——
    ① 內在情緒正向那刻（心情好→🥰、悶久珍惜→🤗；`pick_self_reaction` 非 None）；
    ② 🎴 §0.87 這則回覆表達**強正向/暖**情緒（`reaction.reply_emotion(reply_text)=='positive'`）→ 配一張**正向**真貼圖強化。
    送的是對方教過我的真貼圖（非負向）或設定檔；①沒真貼圖可送時退回單顆 emoji（既有）、②內容驅動觸發**不退 emoji**
    （沒相符真貼圖就不送、絕不用 emoji 假裝，§0.72/§0.84）。`SEND_STICKERS=0`／dry_run 不送。reply_text=None＝逐位元同現狀。"""
    if not getattr(cfg, "send_stickers", True) or getattr(cfg, "dry_run", False):
        return
    if _hostile_now(state, cfg):          # 🌊 §1.77 氣頭上不丟貼圖（情緒貼圖 lane 同閘）
        print("[sticker] 🌊 §1.77 氣頭收斂：對方在氣頭上 → 情緒貼圖不送")
        return
    cd = max(0, getattr(cfg, "sticker_cooldown_min", 20)) * 60
    if now_ts - (getattr(state, "last_sticker_ts", 0) or 0) < cd:
        return
    em = reaction.pick_self_reaction(getattr(state, "vitality", None))
    if reply_text is None:                                # 🎴 §0.87 未顯式帶入 → 取本輪剛送出的互動回覆（_say 於 chokepoint 記下）
        reply_text = _TURN.get("last_reply")
    content_val = (reaction.reply_emotion(reply_text)
                   if (reply_text and getattr(cfg, "content_sticker_enabled", True)) else None)
    if not em and not content_val:                        # 兩種觸發都沒 → 不吐（reply_text=None 時＝原「只在內在正向才吐」）
        return
    known, conf = getattr(state, "known_sticker_ids", None), getattr(cfg, "sticker_file_ids", None)
    if content_val == "positive":                         # 🎴 §0.87 內容強正向 → 正向池優先（空退回 sendable）
        ids = reaction.positive_sticker_ids(known, conf) or reaction.sendable_sticker_ids(known, conf)
    else:
        ids = reaction.sendable_sticker_ids(known, conf)
    # 🎴 §0.59／§0.84：真的「避免重複」——選圖避開最近 N 張送過的（§0.84 多樣化；window=1＝§0.59 只排上一張＝同現狀）。
    fid = _pick_sticker(ids, state, cfg)
    sticker_sent = bool(client.send_sticker(fid)) if (fid and getattr(client, "send_sticker", None)) else False
    if sticker_sent:                                      # 真的送出貼圖 → 記 id/recent
        _record_sticker_sent(state, fid, now_ts)
    elif em:                                              # 內在正向但沒真貼圖 → 退回單顆 emoji（既有）；內容驅動觸發（em 為 None）不退 emoji
        if client.send(em):
            state.last_sticker_ts = now_ts


def _has_sendable_sticker(state, cfg):
    """🎴 §0.68：bot 手邊有沒有「可以真的送出的貼圖」（對方教過的非負向 file_id ∪ 設定檔 STICKER_FILE_IDS）。
    給承諾能力閘用（沒有＝不能答應送貼圖、誠實請對方先傳一張教）。純讀、不改狀態。"""
    return bool(reaction.sendable_sticker_ids(getattr(state, "known_sticker_ids", None),
                                              getattr(cfg, "sticker_file_ids", None)))


def _pick_sticker(ids, state, cfg):
    """🎴 §0.84 從候選 file_id 挑一張要送的：避開**最近 window 張**送過的（多樣化，不再只避上一張）。
    window＝cfg.sticker_rotate_window；sticker_no_repeat_enabled 關＝window 視為 0（不排除，同 §0.59 旗標關）。
    recent 取 state.recent_sticker_ids（新的在後），空則退回 [last_sticker_id]（銜接 §0.59）。window=1 時逐位元同 §0.59。"""
    ids = [i for i in (ids or []) if i]
    if not ids:
        return None
    win = getattr(cfg, "sticker_rotate_window", 3) if getattr(cfg, "sticker_no_repeat_enabled", True) else 0
    recent = list(getattr(state, "recent_sticker_ids", None) or [])
    if not recent:
        last = getattr(state, "last_sticker_id", None)
        if last:
            recent = [last]
    pool = reaction.eligible_sticker_ids(ids, recent, win)
    return random.choice(pool) if pool else None


def _sticker_emoji_for(state, fid):
    """🎴 §0.90：查一張 file_id 在 known_sticker_ids 記著的**情緒標記 emoji**（教學時捕捉的，如 😄；設定檔/未知＝''）。
    給「剛送出的那張貼圖」的接地用——bot 看不到貼圖圖案本身，只憑這個情緒標記認得它。純讀。"""
    for k in (getattr(state, "known_sticker_ids", None) or []):
        if k.get("file_id") == fid:
            return k.get("emoji") or ""
    return ""


def _record_sticker_sent(state, fid, now_ts):
    """🎴 §0.84 記一張**已真的送出**的貼圖：last_sticker_id（§0.59 相容）＋recent_sticker_ids（保最近 8、同款移到最後）＋冷卻鐘。
    🎴 §0.90 也記下這張的**情緒標記 emoji**（last_sticker_emoji）——之後被問「為什麼喜歡剛剛那張」能誠實接地在真的送出的那張，
    不再認不得自己送了哪張、把它幻覺成更早訊息的別張貼圖/emoji（截圖：送了黃狗貼圖卻說成藍色蝴蝶）。"""
    _TURN["sticker_sent_this_turn"] = True                     # 🎴 §1.34 STICKER_V2/F4 本輪真送真相旗（唯一寫身份咽喉）：這就是假送閘「不誤傷本輪真送」所需的唯一可靠 backing——不用語意過載的 last_sticker_ts（只送 emoji 時它也被設，見 §1.34 測繪 B gap）。比照 §0.87 last_reply 慣例，handle_message 每輪頂端 pop。
    state.last_sticker_id = fid
    state.last_sticker_emoji = _sticker_emoji_for(state, fid)   # §0.90 真送出那張的情緒標記（未知＝''）
    state.last_sticker_desc = _sticker_desc_for(state, fid)     # 🎴 真送出那張的畫面描述（收到時視覺讀的；未讀＝''）→ 被問時據實談畫面
    rec = [r for r in (getattr(state, "recent_sticker_ids", None) or []) if r != fid]
    rec.append(fid)
    state.recent_sticker_ids = rec[-8:]
    state.last_sticker_ts = now_ts
    # 🎴🧠 §1.23 送出的貼圖也進對話史（收訊方向 :1229 早就記「（貼圖：…）」，送出方向過去零記錄——
    # chat LLM 的歷史裡真的只有文字，於是誠實地否認自己送過＝截圖 18:24「我好像沒有傳貼圖給你耶」的根因）。
    # convo_history 本身持久化＝跨重生也記得。環境變數門控（本函式無 cfg；config.sticker_sent_memory_enabled 為文件欄位）。
    if os.getenv("STICKER_SENT_MEMORY", "1") != "0" \
            and isinstance(getattr(state, "convo_history", None), list):   # 防禦：假/精簡 state 無史＝跳過（同 getattr 慣例）
        _e, _d = state.last_sticker_emoji or "", state.last_sticker_desc or ""
        _remember(state, "model", f"（我送了一張貼圖{('：' + _e) if _e else ''}{('——' + _d) if _d else ''}）",
                  ts=now_ts)


def _send_real_sticker_from(client, state, cfg, ids, now_ts=None):
    """🎴 §0.84：從**指定候選 ids** 真的送出一張貼圖（給 §0.84 當下請求依語氣挑池用）；選圖沿用多樣化（§0.84 避開最近 N 張）。
    送成功記 last/recent 並回 True；空池/送失敗/dry_run 回 False。now_ts 空＝time.time()。"""
    if getattr(cfg, "dry_run", False) or not getattr(client, "send_sticker", None):
        return False
    fid = _pick_sticker(ids, state, cfg)
    if not fid:
        return False
    if client.send_sticker(fid):
        _record_sticker_sent(state, fid, now_ts if now_ts is not None else time.time())
        return True
    return False


def _send_real_sticker(client, state, cfg):
    """🎴 §0.68：真的送出一張**已捕捉的真貼圖**（telegram sendSticker，非 emoji 假裝）——給送貼圖承諾兌現用。
    選圖沿用不重複（§0.84 避開最近 N 張、多樣化），送成功記 last/recent 並回 True；無貼圖可送/送失敗/dry_run 回 False。"""
    ids = reaction.sendable_sticker_ids(getattr(state, "known_sticker_ids", None),
                                        getattr(cfg, "sticker_file_ids", None))
    return _send_real_sticker_from(client, state, cfg, ids)


def _bump_arousal(state, cfg, da):
    """🧭💗 circumplex：把一筆事件的 A（喚起）推動加到慢喚起軸（幅度同受 MOOD_GAIN 縮放、夾 [-1,1]）。
    旗標關／無 entropy／da=0 ＝不動（V 行為在各呼叫點原樣保留＝逐位元同現狀）。"""
    ent = getattr(state, "entropy", None)
    if ent is None or not da or not getattr(cfg, "affect_circumplex_enabled", True):
        return
    ent.arousal = max(-1.0, min(1.0, getattr(ent, "arousal", 0.0) + da * getattr(cfg, "mood_gain", 1.0)))


def _current_mood(state):
    """此刻心情 V（內在熵優先、退回 vitality 快照、再退 0.0）。給偏好挑圖用。純讀。"""
    ent = getattr(state, "entropy", None)
    if ent is not None and getattr(ent, "mood", None) is not None:
        return ent.mood
    vit = getattr(state, "vitality", None) or {}
    return vit.get("mood") or 0.0


def _send_liked_sticker(client, state, cfg, now_ts=None):
    """🎴 §0.94 送出一張 bot **此刻真的偏好**的真貼圖——不是 random.choice。挑圖走 `reaction.pick_liked_entry`
    （真的看過畫面＞價性合心情＞最近學到；沿用 §0.84 避開最近 N 張），送成功記 last/recent（含畫面描述→
    `state.last_sticker_desc`，供回話據實說出是哪一張）並回 True。旗標關 → 退回 `_send_real_sticker`（逐位元同現狀）。
    空池／送失敗／dry_run 回 False。"""
    if not getattr(cfg, "liked_sticker_pick_enabled", True):
        return _send_real_sticker(client, state, cfg)
    if getattr(cfg, "dry_run", False) or not getattr(client, "send_sticker", None):
        return False
    recent = list(getattr(state, "recent_sticker_ids", None) or [])
    if not recent:
        last = getattr(state, "last_sticker_id", None)
        if last:
            recent = [last]
    win = getattr(cfg, "sticker_rotate_window", 3) if getattr(cfg, "sticker_no_repeat_enabled", True) else 0
    entry = reaction.pick_liked_entry(getattr(state, "known_sticker_ids", None),
                                      getattr(cfg, "sticker_file_ids", None),
                                      _current_mood(state), recent, win)
    fid = (entry or {}).get("file_id")
    if not fid:
        return False
    if client.send_sticker(fid):
        _record_sticker_sent(state, fid, now_ts if now_ts is not None else time.time())
        return True
    return False


def _sticker_promise_flags(text, state, cfg):
    """🎴 §0.95 一句話要不要在到點時**真的送出一張 telegram sticker**、以及是不是「你喜歡的那一張」偏好題。

    關鍵修正（截圖根因）：「**告訴**我你喜歡的貼圖，且說為什麼喜歡」沒有送/傳動詞 → `promise_wants_sticker` 為 False
    → 到點只用文字講，而文字講貼圖必然掉回「拿 **emoji 當貼圖**」的老毛病（bot 把情緒標記 😂 講成「大笑到流淚的貼圖」
    ——那是**標記**、不是它的**畫面**）。**「你喜歡哪一張貼圖」唯一誠實的回答方式就是把那張真 sticker 送出來**，
    所以偏好題一律視為 wants_sticker（telegram sticker ≠ emoji，§0.68 的分辨在這條路上要一樣硬）。

    回 (wants, capable, prefers)。capable＝手邊真有可送的真貼圖（沒有就不標 wants＝兌現不假裝，§0.64 做不到不空口答應）；
    prefers **不受 capable 影響**＝就算沒貨也要記著這是偏好題，兌現文字才會誠實（不 emoji 假裝、不捏造圖案）。"""
    if not getattr(cfg, "promise_sticker_enabled", True):
        return False, False, False
    prefers = (getattr(cfg, "liked_sticker_pick_enabled", True)
               and selfstate.is_sticker_preference_request(text))
    wants = bool(selfstate.promise_wants_sticker(text) or prefers)
    capable = wants and _has_sendable_sticker(state, cfg)
    return wants, capable, prefers


def _promise_send_sticker(client, state, cfg, p):
    """🤝🎴 §0.94 兌現「送貼圖」承諾時真的送出一張：**偏好題**（捕捉時標了 prefers_sticker＝對方要「你喜歡的那一張」）
    → 挑 bot 此刻真的偏好的那張（`_send_liked_sticker`）；其餘承諾沿用 §0.68 既有選圖（`_send_real_sticker`）＝逐位元同現狀。"""
    if p.get("prefers_sticker") and getattr(cfg, "liked_sticker_pick_enabled", True):
        return _send_liked_sticker(client, state, cfg)
    return _send_real_sticker(client, state, cfg)


_STICKER_FOLLOWUP_CTX_SEC = 15 * 60   # 🎴 §0.86 曖昧承接請求（還有嗎/再一個）的「近期剛在貼圖情境」窗


def _recent_sticker_ctx(state, now_ts, window=_STICKER_FOLLOWUP_CTX_SEC):
    """🎴 §0.86：近期是不是剛在『貼圖情境』——剛送過真貼圖（last_sticker_ts），或使用者剛教（傳）過貼圖（known 末筆 ts）。
    給曖昧承接請求（還有嗎/再一個/剛剛那張）門控，避免這些泛詞在非貼圖對話誤收。"""
    if (now_ts - (getattr(state, "last_sticker_ts", 0) or 0)) < window:
        return True
    known = getattr(state, "known_sticker_ids", None) or []
    if known and (now_ts - max((k.get("ts") or 0) for k in known)) < window:
        return True
    return False


def _sticker_inventory_question(text):
    """Inventory/variety questions are not instructions to send a sticker."""
    return bool(re.fullmatch(
        r"(?:你)?(?:會|能|可以)?(?:使用|用|送|傳)?(?:哪些|什麼|多少|幾種|幾張)(?:的)?貼圖[？?。\s]*"
        r"|(?:你)?(?:有|存了|存著)(?:哪些|什麼|多少|幾種|幾張)貼圖[？?。\s]*"
        r"|(?:我)?(?:都|一直|總是)?看到(?:那)?幾個(?:同樣|一樣)的貼圖(?:樣式)?[？?。！!\s]*",
        (text or '').strip()))


def _sticker_inventory_reply(state, cfg):
    ids = reaction.sendable_sticker_ids(getattr(state, 'known_sticker_ids', None),
                                        getattr(cfg, 'sticker_file_ids', None))
    if not ids:
        return '目前沒有可供我選用的貼圖紀錄。我不能只憑對話裡的表情符號說自己存了哪些圖。'
    descriptions = [_sticker_desc_for(state, fid) for fid in ids]
    seen = list(dict.fromkeys(d for d in descriptions if stickervision.is_seen(d)))
    reply = f'目前可供我選用的貼圖有 {len(ids)} 張。'
    if seen:
        reply += '\n有畫面描述的包括：' + '、'.join('「' + d[:100] + '」' for d in seen[:4]) + '。'
        if len(seen) > 4:
            reply += '這裡先列四種，不是全部。'
    else:
        reply += '\n目前缺少可核對的畫面描述，不能拿 emoji 標籤當作圖案介紹。'
    reply += '\n這是可選庫存，不代表每張都送過；為什麼反覆選到那幾張，還不能只靠庫存數量確定。'
    return reply


def _sent_sticker_ground_hint(state, cfg, text, now_ts):
    """🎴 §0.90：這句在問/聊**剛送出的那張貼圖**（asks_about_sent_sticker）＋bot **近期真的送過**貼圖（last_sticker_ts 在窗內）
    → 回一段誠實接地 system 片段（persona.sent_sticker_ground_hint，帶那張的情緒標記），讓回覆據實談那一張、
    別認成別張／別捏造樣子（截圖：送黃狗貼圖卻說成藍色蝴蝶）。旗標關／沒近期真貼圖／非此類問句＝''（不注入＝逐位元同現狀）。"""
    if not getattr(cfg, "sent_sticker_ground_enabled", True):
        return ""
    # 🎴 §0.90 問剛送的那張（為什麼喜歡這張…）∪ §0.93 問「看不看得到/懂不懂貼圖內容」（能力 meta 問句）→ 皆誠實接地作答（有畫面描述就談畫面）
    if not (text and (selfstate.asks_about_sent_sticker(text) or selfstate.asks_can_perceive_sticker(text))):
        return ""
    # **須**近期真的送過貼圖（用 last_sticker_ts，不用 known 教學 ts）——沒真送過就別無中生有注入
    if (now_ts - (getattr(state, "last_sticker_ts", 0) or 0)) >= _STICKER_FOLLOWUP_CTX_SEC:
        return ""
    return persona.sent_sticker_ground_hint(getattr(state, "last_sticker_emoji", "") or "",
                                            getattr(state, "last_sticker_desc", "") or "")


# 🎴🧠 §1.34 STICKER_V2/F5「圖呢／圖」消歧用：明確附件名詞（含這些＝真在調記寫附件，不走貼圖消歧）
# 與裸圖線索白名單（挖掉貼圖詞與明確名詞後、剩餘 _MEDIA_CUES 只落在這幾個裸『圖』字＝在問剛送那張貼圖）。
_IMG_EXPLICIT_ATTACH = ("附件", "照片", "相片", "圖片", "pdf", "PDF", "語音", "錄音", "音檔", "音訊", "影片", "檔案", "原檔")
_IMG_BARE_CUES = ("圖", "圖檔", "截圖", "檔")


def _is_bare_img_ref(text):
    """🎴🧠 §1.34 F5：這句是不是『裸圖省略/指涉』（圖呢／圖／截圖呢…）——挖掉貼圖詞後、剩餘 media cue 只落在裸『圖/圖檔/截圖/檔』，
    且句短、不含明確附件名詞（照片/PDF/附件…）。命中＝貼圖情境下應被理解成『剛送的那張貼圖』、非要調記寫附件。純字串、可測。"""
    t = (text or "").strip()
    if not t or len(t) > 8:                               # 只收短省略句（長句多半另有主題）
        return False
    if any(n in t for n in _IMG_EXPLICIT_ATTACH):         # 明確附件名詞（照片呢/把 PDF 給我/附件呢）→ 交 attachment 路徑
        return False
    stripped = t
    for w in selfstate._STICKER_WORDS:                    # 先挖掉貼圖詞（貼圖呢＝談貼圖本身、非裸圖）
        stripped = stripped.replace(w, "")
    remaining = [c for c in selfstate._MEDIA_CUES if c in stripped]
    if not remaining:                                     # 挖完沒有任何媒體線索＝不是在問圖
        return False
    return all(c in _IMG_BARE_CUES for c in remaining)     # 剩餘線索全是裸『圖』字＝在問剛送那張貼圖


def _sticker_img_disambig(client, state, cfg, text, now_ts):
    """🎴🧠 §1.34 STICKER_V2/F5 貼圖情境「圖呢／圖」消歧閘（在 attachment 分派段最前、select_attachments 之前）：
    近期剛在貼圖情境（_recent_sticker_ctx）＋這句是裸圖省略句（_is_bare_img_ref）→ **不撈私人記寫附件**，改走貼圖誠實路徑：
      ① SEND_STICKERS=0（維護中）→ 誠實維護句『送不出、也不拿別的圖代替』（switch item② 協同：關＝不撈附件冒充）；
      ② 15 分窗內真送過（last_sticker_id 非空）→ 據實談剛送那張（有 desc 講畫面、有 emoji 講情緒標記，複用 §1.23 素材）；
      ③ 近期貼圖情境但無真送（只教過/last_sticker_id 空）→ 誠實『我沒送貼圖給你，需要我送一張嗎』——仍不撈附件。
    回 True＝已消費本輪（呼叫端 return、不撈附件）；旗標關／非貼圖情境／明確附件＝False（照走 attachment＝逐位元同現狀）。
    判『真送過』用 last_sticker_id（§1.23 真相：id 非空＝真送過），不用語意過載的 last_sticker_ts（只當冷卻）。"""
    if not getattr(cfg, "sticker_img_disambig_enabled", False):
        return False
    if not (_recent_sticker_ctx(state, now_ts) and _is_bare_img_ref(text)):
        return False
    if not getattr(cfg, "send_stickers", True):           # 🔇 switch item②：維護中不撈附件冒充貼圖、改維護誠實句
        _say(client, "你是問我剛剛的貼圖吧？貼圖現在維護中、送不出，我也不會拿別的圖來代替。")
        return True
    win = _STICKER_FOLLOWUP_CTX_SEC
    real_recent = (getattr(state, "last_sticker_id", None)
                   and (now_ts - (getattr(state, "last_sticker_ts", 0) or 0)) < win)
    if real_recent:
        desc = getattr(state, "last_sticker_desc", "") or ""
        emoji = getattr(state, "last_sticker_emoji", "") or ""
        if desc:
            _say(client, f"你是問我剛送的那張貼圖吧——它畫的是「{desc}」。")
        elif emoji:
            _say(client, f"你是問我剛送的那張貼圖吧——我記得它的情緒是「{emoji}」。")
        else:
            _say(client, "你是問我剛送的那張貼圖吧——我記得送過，只是看不到它的樣子。")
    else:
        _say(client, "我沒送貼圖給你，你是想要我送一張嗎？")
    print("[sticker] 🎴 §1.34 F5 圖呢消歧：貼圖情境的裸『圖』→ 談剛送那張、不撈私人附件")
    return True


def _maybe_sticker_remember(client, state, cfg, text, now_ts):
    """🎴 §0.86「這些貼圖記下來/記住這些 sticker」→ 確認**剛教的真貼圖已記起來、之後能送**（§0.68 本就自動捕捉進 known_sticker_ids）；
    別讓 LLM 在聊天裡 confabulate『記下感覺、用文字描述』（截圖）。回 True＝已接管；旗標關／非此類＝False。"""
    if not getattr(cfg, "send_stickers", True):           # 🔇 §1.34 STICKER_V2/switch 總開關：SEND_STICKERS=0＝互動 lane 一律靜音（不接管→落一般聊天 lane 的維護誠實 hint，不硬送）
        return False
    if not getattr(cfg, "sticker_send_request_enabled", True):
        return False
    if not selfstate.is_sticker_remember_request(text):
        return False
    n = len(reaction.sendable_sticker_ids(getattr(state, "known_sticker_ids", None),
                                          getattr(cfg, "sticker_file_ids", None)))
    _say(client, persona.sticker_remember_ack(n))
    return True


_STICKER_TALK_RE = re.compile(r"貼圖|貼紙|sticker|Sticker|STICKER")
# 🎴 §1.15：不帶貼圖字的**短省略問句**（「那個呢」「還有嗎」「有嗎」）——只在近期剛談過貼圖時才算承接語。
# 收得窄＝逃生閘不進熱路徑（「嗯」「謝謝你」「今天天氣真好」不匹配、零 LLM 呼叫）。
_STICKER_ELLIPSIS_ASK_RE = re.compile(r"^.{0,7}[呢嗎]{1}[？?！! ]*$|^.{0,5}[？?]{1}$")

# 🎴🗣️ §1.56 困惑句（CONFUSED_CLARIFY）：「什麼？」「蛤？」「啊？」＝聽不懂、要澄清——卻長得像短省略問句
# （^.{0,5}？$）＝貼圖情境窗內會被 §1.15 結構閘⑤收進逃生閘、LLM 又順著剛才的「要我送一張嗎？」判送 →
# 真送＋心情說明＝雞同鴨講（截圖 21:29「什麼？」→ 熊抱貼圖）。ellipsis regex 本體**一字不動**（指紋格），
# 由呼叫端前置排除；「看不懂/什麼意思/在說什麼」同族＝澄清請求（給 _clarify_recap_hint 接地用）。
_CONFUSED_ASK_RE = re.compile(
    r"^(?:什麼|甚麼|蛤|啊|嗯|咦|欸|哈)[？?]+[！!。…\s]*$"
    r"|^(?:什麼意思|甚麼意思|你(?:在|到底在)?說什麼|看不懂|聽不懂)[？?！!。…\s]*$")

# 🗣️ §2.24 嗆聲式「你在講什麼」（台語/口語；CONFUSED_SLANG）：「你究竟是在供殺小？」＝**帶情緒**的
# 「你到底在說什麼」。實測三層全 miss：_CONFUSED_ASK_RE 不認（→沒澄清接地、LLM 對俚語迷航出
# 「這句話不是你剛剛說的嗎？」）、reaction.is_hostile 不認（→§1.77 氣頭門形同虛設、被嗆完照送親親熊）、
# 「你到底在講什麼」的「講」字版本也不在表上。封閉慣用語類：動詞×[三殺啥沙]×[小洨潲] 組合＋裸髒問
# （排除「三小時」）＋「到底/究竟…在講/說…什麼」寬式。
_SLANG_WTF_RE = re.compile(
    r"[供公工攻講嗆][三殺啥沙][小洨潲]"
    r"|[三殺啥沙]小(?!時)"
    r"|(?:到底|究竟)[^。！？!?\n]{0,6}?[在再][講說供][^。！？!?\n]{0,4}?(?:什麼|啥)")

# 🎴🧠 §1.34 STICKER_V2/F3 挑選祈使**便宜前置過濾**（不做捕捉判定、只決定「該不該讓 LLM 逃生閘燒一次」）：
# 「挑一張給我／你挑一張給我吧／幫我選一個」對確定性八偵測器全 miss，且結構閘（貼圖字 OR 省略問句）也不中
# → 連 §1.15 LLM 逃生閘都碰不到（全掉 fact_or_chat）。這裡只前置過濾出「短句＋挑/選＋指向本人（一張/一個/給我/傳我/送我）」，
# 真正是非交給既有 coach.judge_sticker_request 燒一次 LLM 判（§1.15 鐵律：LLM 只判是非、真送圖讀 circumplex 挑）。
# 指向本人槽**刻意窄到 一張/一個/給我/傳我/送我**（不收 幫我挑/幫我選 這種泛槽）——「幫我選個餐廳」「幫我挑個時間」
# 因無 一張/一個/給我… 而不命中前置式＝離題閒聊零 LLM 呼叫（不進熱路徑，比照結構閘鐵律）。
_STICKER_PICK_IMPER_RE = re.compile(r"[挑選]")
_STICKER_PICK_TARGET_RE = re.compile(r"一[張個]|給我|傳我|送我")
# 明確附件名詞：挑選祈使若指向真附件（挑一張照片給我）→ 不走貼圖前置式、交附件路徑（比照 _MEDIA_CUES 的明確項）。
_PICK_ATTACH_NOUN = ("照片", "相片", "圖片", "圖檔", "pdf", "PDF", "檔案", "附件", "原檔", "語音", "錄音", "音檔", "影片")


def _sticker_pick_imperative(text):
    """🎴🧠 §1.34 F3 便宜前置過濾：這句是不是『挑/選一張(給我)』式的貼圖挑選祈使（短句、指向本人、非明確附件、非第三人）。
    純字串判定、不燒 LLM；命中僅代表『值得讓 §1.15 逃生閘燒一次 LLM 判是非』，真正要不要送仍由 LLM＋circumplex 決定。
    第三人（給我弟/給他…）已由 _maybe_llm_sticker_rescue 門③的 _STICKER_3RD_RE 更早擋下，這裡只再擋明確附件名詞。"""
    t = (text or "").strip()
    if not t or len(t) > 14:                              # 只收短祈使句（長句多半另有主題、別誤燒 LLM）
        return False
    if any(n in t for n in _PICK_ATTACH_NOUN):            # 明確附件名詞（挑一張照片給我）→ 交附件路徑、不走貼圖
        return False
    return bool(_STICKER_PICK_IMPER_RE.search(t) and _STICKER_PICK_TARGET_RE.search(t))


def _last_bot_mentions_sticker(state):
    """🎴 §0.88：bot **上一則**是不是剛在講貼圖（提到 貼圖/sticker）——給極泛的「傳給我」判斷是不是指「傳那張貼圖給我」。"""
    hist = getattr(state, "convo_history", None) or []
    last_model = next((h.get("text") for h in reversed(hist) if h.get("role") == "model" and h.get("text")), None)
    return bool(last_model and _STICKER_TALK_RE.search(last_model))


# 🎴 §1.16 確定性「要不要說明為什麼」備援：LLM 第二行漏判時，句中這些詞也算要說明（送圖後接地說為什麼是這張）。
_WHY_RE = re.compile(r"為什麼|為何|說說|說明|怎麼會|怎會")


def _sticker_send_or_honest(client, state, cfg, ids, now_ts, why=False):
    """🎴 §1.15 送出/誠實無貨的共用尾段（從 _maybe_sticker_send 尾端**純搬移**、行為不變）：真的送出一張
    （_send_real_sticker_from），再回一則文字——
    §1.16 複合請求（why=True 且 STICKER_WHY_GROUND 開）→ 依此刻 circumplex (V,A)**單一真相**接地說明為什麼是這張
    （選圖與說明讀同一份情緒，絕不自相矛盾；desc 只有真看過才准描述、未看過憑感覺挑不捏造圖案；無貨誠實不 emoji 假裝）；
    否則（why 預設 False／§1.16 旗標關）→ 退回 §0.84 罐頭 sticker_send_reply＝逐位元同 §1.15-only 行為。回 sent（bool）。"""
    sent = _send_real_sticker_from(client, state, cfg, ids, now_ts=now_ts)
    if why and getattr(cfg, "sticker_why_ground_enabled", True):
        v, a = circumplex.position(state)                 # 🧭 情緒單一真相：選圖與說明讀同一份 (V,A)
        label = circumplex.label(v, a)
        fid = getattr(state, "last_sticker_id", None)      # 真送出那張（_record_sticker_sent 已設）
        emoji = _sticker_emoji_for(state, fid)
        desc = _sticker_desc_for(state, fid)
        known = getattr(state, "known_sticker_ids", None)
        conf = getattr(cfg, "sticker_file_ids", None)
        # 不匹配＝此刻想要正向池、卻只送得出中性/sendable 圖（心情雀躍但手邊只有中性雞）→ 誠實說感覺不完全一樣
        mismatch = bool(sent and circumplex.sticker_pool(v, a) == "positive"
                        and fid not in reaction.positive_sticker_ids(known, conf))
        _say(client, persona.sticker_why_reply(sent, label, emoji=emoji, desc=desc,
                                               have_sendable=bool(ids), mismatch=mismatch))
    else:
        _say(client, persona.sticker_send_reply(sent, have_sendable=bool(ids)))
    return sent


def _maybe_sticker_send(client, state, cfg, text, now_ts):
    """🎴 §0.84／§0.86 使用者**當下**請 bot 送一張貼圖（is_sticker_send_request；或 §0.86 承接式『再一個/還有嗎/剛剛那張』
    ＋近期貼圖情境）：有可送的真貼圖 → **真的 send_sticker** 一張＋自然帶過；沒有 → 誠實說『能送、只是還沒存到、請先傳一張教我』——
    **絕不否認能送、絕不用 emoji 假裝**。截圖：「開心的貼圖」只吐「😄」還說沒能力；「其他的貼圖/我剛傳給你的一個」落聊天被否認。
    回 True＝已接管此則（呼叫端 return，不再走一般聊天）；旗標關／非此類請求＝False（逐位元同現狀）。"""
    if not getattr(cfg, "send_stickers", True):           # 🔇 §1.34 STICKER_V2/switch 總開關：SEND_STICKERS=0＝互動 lane 一律靜音（不真送、不宣稱、不搶）；根因 probe_switch：底層只 gate dry_run 不 gate send_stickers
        return False
    if not getattr(cfg, "sticker_send_request_enabled", True):
        return False
    # 🌊 §1.77 語意閘（主修）：抱怨/質問「你還傳這種貼圖」不是**請求**貼圖——不把抱怨當訂單。
    # 旗標關（getattr 預設 False）＝不閘＝逐位元同現狀。
    if getattr(cfg, "hostile_grace_enabled", False) and selfstate.is_behavior_complaint(text):
        print("[sticker] 🌊 §1.77 語意閘：這句在抱怨我送貼圖、不是要我送 → 不當請求，交回一般對話")
        return False
    _direct = selfstate.is_sticker_send_request(text)
    # 🎴 §0.99 **感知問句不走曖昧承接/裸送**：問「你知道剛剛傳的是什麼貼圖／這是什麼貼圖／你看得到嗎」＝在問**剛送出那張是什麼**、
    # 不是請我**再送**一張。根因（截圖答非所問）：`is_sticker_followup_request('你知道剛剛傳的是什麼貼圖')` 誤中（『剛剛…貼圖』）＋
    # 近期真送過 → _followup 成立 → 搶走又送一張＋罐頭「來，這張真貼圖送你」，§0.90/§0.93 誠實接地（已在 mhint）永遠到不了。
    # 只擋**曖昧**的 _followup/_bare（感知問句只會誤中這兩條）；明確的 _direct（送我一張）／_prefers（你喜歡哪一張＝要我送）不受影響。
    _perceive_q = selfstate.asks_about_sent_sticker(text) or selfstate.asks_can_perceive_sticker(text)
    # 🎴 §0.86 承接式（還有嗎/再一個/剛剛那張，無「貼圖」字）——**須**近期剛在貼圖情境才收（避免『還有嗎』誤收）。
    _followup = (not _direct) and (not _perceive_q) and selfstate.is_sticker_followup_request(text) and _recent_sticker_ctx(state, now_ts)
    # 🎴 §0.88 極泛「傳給我／給我」（無貼圖字）——**雙重門控**：bot 上一則剛在講貼圖 ＋ 近期貼圖情境（否則「把報告傳給我」誤收）。
    # 截圖：使用者問「猜我情緒、哪張 sticker」→ bot 描述選哪張 → 「傳給我」→ 該送真貼圖、卻吐「（貼圖：😏）」emoji 假裝。
    _bare = ((not _direct) and (not _followup) and (not _perceive_q) and selfstate.is_bare_send_request(text)
             and _last_bot_mentions_sticker(state) and _recent_sticker_ctx(state, now_ts))
    # 🎴 §0.95 偏好題（你喜歡哪一張貼圖／告訴我你喜歡的貼圖）也是「請 bot 送一張」——用嘴巴描述必然掉回拿 emoji 當貼圖。
    _pref = getattr(cfg, "liked_sticker_pick_enabled", True) and selfstate.is_sticker_preference_request(text)
    if not (_direct or _followup or _bare or _pref):
        return False
    # 帶未來時刻的送貼圖請求（8點/10分鐘後/等一下 送我貼圖）→ **不當即時送**，交給排程承諾/一般路徑
    # （is_scheduled 收得到的走排程；收不到但 looks_like_timed 的也別現在就送＝別把「8點送我」當「現在送」）。
    if selfstate.is_scheduled_promise_request(text) or selfstate.looks_like_timed_request(text):
        return False
    known = getattr(state, "known_sticker_ids", None)
    conf = getattr(cfg, "sticker_file_ids", None)
    # 依語氣挑池：明確正向（開心/可愛/療癒…）→ 正向池優先、空退回 sendable；其餘直接 sendable（正向＋中性）。
    want_pos = any(w in (text or "") for w in ("開心", "快樂", "歡樂", "可愛", "療癒", "萌", "溫暖", "幸福", "笑"))
    ids = reaction.positive_sticker_ids(known, conf) if want_pos else []
    if not ids:
        ids = reaction.sendable_sticker_ids(known, conf)
    # 🎴 §0.94 對方要的是「**你喜歡的**那一張」（你喜歡哪一張貼圖／傳一張你喜歡的貼圖）→ 挑圖走**真偏好**
    # （非 random），回話**據實說出是哪一張**（用真的看過的畫面描述；沒看過就誠實說憑感覺挑、絕不捏造圖案）。
    if getattr(cfg, "liked_sticker_pick_enabled", True) and selfstate.is_sticker_preference_request(text):
        sent = _send_liked_sticker(client, state, cfg, now_ts=now_ts)
        _say(client, persona.sticker_preference_reply(sent, getattr(state, "last_sticker_desc", "") or "",
                                                      have_sendable=bool(ids)))
        return True
    # 🎴 §1.15 尾段抽成共用 helper（why 預設 False＝逐位元同現狀）；原本無論送成與否都 return True＝已接管，保持
    return _sticker_send_or_honest(client, state, cfg, ids, now_ts) or True


def _maybe_llm_sticker_rescue(client, state, cfg, coach, route, text, now_ts):
    """🎴🧠 §1.15 貼圖請求語意逃生閘（完全複製 §1.12 承諾逃生閘 _maybe_llm_promise_rescue 的形狀到貼圖）：
    存在句／可能句／省略句（「有代表現在的你的心情的貼圖嗎」「貼圖呢」「有貼圖可以代表你剛剛說的心情？」）一個給予/
    祈使動詞都沒有 → 確定性偵測 is_sticker_send_request/followup/remember/preference 全 miss → _maybe_sticker_send 回 False
    → 掉進 intent.resolve 被吃成 self_state、貼圖整件事丟掉。手刻 regex 補動詞表＝第 16 次踩坑（禁止）；改放**語意逃生閘**：
    結構閘在場而確定性沒接到才呼叫**一次** gemini 判「他是不是要我現在送一張貼圖給他本人／要不要順便說明為什麼」。
    **鐵律：LLM 只判是非，絕不產出 file_id/emoji/圖案描述**——真送的圖由程式端讀 circumplex 單一真相挑（_affect_sticker_ids）、
    沒貨誠實說沒有（§0.64／§0.95）。判「是」→ 送出/誠實無貨（複合請求另接地說為什麼是這張，§1.16）→ True（本輪結束）；
    判「否」／失敗（GeminiError／解析不出）→ False＝安全退回自然聊天、不假裝。STICKER_LLM_RESCUE=0 → 逃生閘不存在＝逐位元同現狀。
    route 為 intent.resolve 的結果（供未來門控用；現行不 gate route.kind——插點已在 _maybe_sticker_send 之後，是確定性漏收的殘餘）。"""
    # 🔇 §1.34 STICKER_V2/switch 總開關：SEND_STICKERS=0＝逃生閘（含 F3 挑選祈使第三分支）一律靜音（不燒 LLM、不真送、不宣稱）
    if not getattr(cfg, "send_stickers", True):
        return False
    # ① 旗標／教練開著才有「該救」前提
    if not (getattr(cfg, "sticker_llm_rescue_enabled", True) and coach and getattr(coach, "enabled", False)):
        return False
    # ② 確定性捕捉 miss 的才救（這四者任一 True＝該請求已被更早的 _maybe_sticker_remember/_maybe_sticker_send 接走＝零重複）
    if (getattr(cfg, "hostile_grace_enabled", False)
            and selfstate.is_behavior_complaint(text)):      # 🌊 §1.77 同一把語意閘（追問 lane）
        return False
    if (selfstate.is_sticker_send_request(text) or selfstate.is_sticker_followup_request(text)
            or selfstate.is_sticker_remember_request(text) or selfstate.is_sticker_preference_request(text)):
        return False
    # ③ 排除感知/教學/否定/第三人（**先於 LLM 判、避免燒呼叫**）：canperc 涵蓋「你看得到貼圖內容嗎」「你知道剛傳的是什麼貼圖」
    # （歸 §0.93 誠實接地路，不搶）；教學/否定歸各自路；明確第三人（給我弟/給他…）regex 中則直接擋。
    if (selfstate.asks_can_perceive_sticker(text) or selfstate._STICKER_NEG_RE.search(text)
            or selfstate._STICKER_TEACH_RE.search(text) or selfstate._STICKER_3RD_RE.search(text)):
        return False
    # ④ 帶未來時刻的送貼圖不當即時送（比照 _maybe_sticker_send §0.68：排程/延後承諾已在更早的承諾鏈接走）
    if selfstate.is_scheduled_promise_request(text) or selfstate.looks_like_timed_request(text):
        return False
    # ④b 🎴🗣️ §1.56 困惑句不是貼圖承接：「什麼？/蛤？/啊？」＝要澄清、不是要貼圖——直接放回一般聊天
    # （零 LLM；澄清接地由 _clarify_recap_hint 負責）。旗標關（getattr 預設 False）＝照舊進閘＝逐位元同現狀。
    if getattr(cfg, "confused_clarify_enabled", False) and _CONFUSED_ASK_RE.match((text or "").strip()):
        return False
    # ⑤ 結構閘（便宜前置，只讓確定性漏收的燒 LLM）：含貼圖/貼紙/sticker 字（「貼圖呢」本身就帶字、由此涵蓋），
    # 或「近期剛在貼圖情境」＋**短省略問句**（「那個呢」「還有嗎」這種不帶貼圖字的承接）。
    # 🎴 §1.15 審查修（審查實測）：原本第二個分支是裸的 `_recent_sticker_ctx(state, now_ts)`——只要交換過一張貼圖，
    # 其後**每一則訊息**（「嗯」「謝謝你」「你太爛了」「今天天氣真好」）都會燒一次 LLM＝逃生閘掉進熱路徑，
    # 違反「LLM 只在確定性漏接時燒一次、不進熱路徑」的架構鐵律（成本/延遲，且 §1.14 敵意收斂路徑也被連坐）。
    # 收緊成「短且是省略問句」＝真正的承接語才放行；離題閒聊零呼叫。
    # 🎴🧠 §1.34 STICKER_V2/F3 第三分支：『近期剛在貼圖情境』＋『窄挑選祈使前置式』（挑一張給我/幫我選一個）——
    # 這類祈使既無貼圖字、也非省略問句，前兩分支都放不行 → 補第三分支讓它進 LLM 逃生閘燒一次判是非（真送圖讀 circumplex）。
    # 與前兩分支一樣**雙門控**（_recent_sticker_ctx）＝離題「幫我選個餐廳」在非貼圖情境零 LLM；旗標關（getattr 預設 False）
    # ＝第三分支不存在＝挑選祈使仍被結構閘擋回 False＝落 fact_or_chat＝逐位元同現狀。
    if not (_STICKER_TALK_RE.search(text) or
            (_recent_sticker_ctx(state, now_ts) and _STICKER_ELLIPSIS_ASK_RE.match((text or "").strip())) or
            (getattr(cfg, "sticker_pick_rescue_enabled", False) and _recent_sticker_ctx(state, now_ts)
             and _sticker_pick_imperative(text))):
        return False
    # 全過 → 燒一次 LLM 判語意（一輪至多一次）
    try:
        verdict = coach.judge_sticker_request(text)
    except gemini.GeminiError as e:                       # coach 端已兜，這裡再兜一層（stub/未來實作直接拋也安全）
        print(f"[sticker] LLM 逃生閘判定失敗：{e}")
        verdict = None
    if verdict is None:
        return False                                      # 失敗／解析不出＝安全退回自然聊天、不假裝（§0.64）
    is_send, wants_why = verdict
    if not is_send:
        return False                                      # LLM 判否（含第三人 regex 漏掉的「傳給小明」）＝自然聊天
    # 🧭 池用 circumplex 單一真相（代表此刻心情的正確池），不用 _maybe_sticker_send 的 want_pos 關鍵字池
    ids = _affect_sticker_ids(state, cfg)
    # 🎴 §1.16 複合請求：LLM 第二行「要」或句中確定性偵測到「為什麼/說說」都算要說明為什麼是這張
    why = bool(wants_why) or bool(_WHY_RE.search(text))
    _sticker_send_or_honest(client, state, cfg, ids, now_ts, why=why)
    return True


def _note_selfshare(state, text, ts):
    """🪞 §0.85：記下 bot 這則**主動自陳**（換檔🍃/自發/內在因應）的內容——供之後「你感覺到了什麼」這種追問接續**那件事**。"""
    t = (text or "").strip()
    if t:
        state.last_selfshare = {"text": t, "ts": ts}


# 🍃 §1.38 「為什麼剛剛那樣說」的 why 標記（相關性另由窗＋最後一句綁定＋內容重疊三重把關，這裡只認 why 框架）。
_SELFSHARE_WHY_MARK = ("為什麼", "為何", "怎麼會", "怎會", "為啥", "幹嘛", "幹麻", "咋")
_CJK2_RE = re.compile(r"[一-龥]{2}")


def _selfshare_relevant(text, share_text):
    """🍃 §1.38 追問句（先剝掉 why 標記）與那則自陳有沒有 ≥2 字 CJK 內容重疊——相關性訊號，防「為什麼天空是藍的」
    這類剛好跟在自陳後、含 why 詞卻無關的句子被誤接（比照 §0.85 反劫持窄門的精神）。剝 why 標記＝別讓「為什麼」裡的
    「什麼」跟自陳的「沒什麼」假重疊。"""
    q = text or ""
    for w in _SELFSHARE_WHY_MARK:
        q = q.replace(w, "")
    s = share_text or ""
    grams = {s[i:i + 2] for i in range(len(s) - 1) if _CJK2_RE.match(s[i:i + 2])}
    return any(q[i:i + 2] in grams for i in range(len(q) - 1) if _CJK2_RE.match(q[i:i + 2]))


def _stash_selfshare_reason(state, cfg, shift):
    """🍃 §1.38 換檔自陳送出後、旗標開＋活絡換檔有真實理由 → 把理由附到 last_selfshare（供被問「為什麼」時據實接地）。
    旗標關/無理由（晝夜換檔等）/last_selfshare 非 dict＝不動＝逐位元同現狀（dict 形狀不變）。"""
    if not getattr(cfg, "selfshare_reason_ground_enabled", False):
        return
    reason = environ.shift_reason(shift)
    share = getattr(state, "last_selfshare", None)
    if reason and isinstance(share, dict):
        share["reason"] = reason


def _selfshare_reason_hint(state, cfg, text, now_ts):
    """🍃 §1.38 使用者在追問 bot 剛主動自陳（🍃 換檔）的「為什麼」→ 注入那則自陳的**真實理由**當接地，讓 bot 據實講
    （周遭安靜/資料面沒新東西→調慢節奏），別因無接地漂到貼圖等處腦補（截圖漂移）。相關性綁定比照 §0.85：近期自陳
    （帶 reason）＋窗內＋那則是 bot 最後一句（防無關 why 句劫持）＋這句含 why 標記。旗標關/不符任一條＝""＝逐位元同現狀。"""
    if not getattr(cfg, "selfshare_reason_ground_enabled", False):
        return ""
    share = getattr(state, "last_selfshare", None)
    if not (isinstance(share, dict) and share.get("reason") and share.get("text")):
        return ""
    if (now_ts - (share.get("ts") or 0)) >= referent.FOLLOWUP_WINDOW_SEC:
        return ""                                          # 過窗（隔太久）→ 不再硬接
    if not any(m in (text or "") for m in _SELFSHARE_WHY_MARK):
        return ""
    if not _selfshare_relevant(text, share["text"]):             # 內容重疊：防「為什麼天空是藍的」剛好跟在自陳後被誤接
        return ""
    _hist = getattr(state, "convo_history", None) or []
    _last_model = next((h.get("text") for h in reversed(_hist) if h.get("role") == "model" and h.get("text")), None)
    if not (_last_model and share["text"][:20] in _last_model):   # 相關性綁定：bot 最後一句就是那則自陳
        return ""
    return persona.selfshare_reason_system(share["text"], share["reason"])


def _maybe_selfshare_followup(client, state, cfg, coach, text, now_utc, convo_now, user_ts, mhint=""):
    """🪞 §0.85：bot 剛**主動自陳**（last_selfshare、窗內）＋這句在追問那件事（is_selfshare_followup）→ 接續**那則自陳的具體內容**回應，
    別重生一段泛自述、別丟了 bot 剛起的話頭（截圖：主動說「感覺周遭又動起來了」，追問「你感覺到了什麼」卻答非所問）。
    回 True＝已接管此則（呼叫端 return）；旗標關／無近期自陳／非追問＝False（逐位元同現狀）。"""
    if not getattr(cfg, "selfshare_followup_enabled", True):
        return False
    share = getattr(state, "last_selfshare", None)
    if not (share and share.get("text")):
        return False
    if (now_utc.timestamp() - (share.get("ts") or 0)) >= referent.FOLLOWUP_WINDOW_SEC:
        return False                                       # 過窗（隔太久）→ 不再硬接
    if not selfstate.is_selfshare_followup(text):
        return False
    # 🪞 §0.85 審查（HIGH 修）：相關性綁定——bot **最後一則**必須就是那則自陳（中間插了別的回覆＝不是即時承接）。
    # 防「主動自陳→聊了別的→30 分內一句含追問詞的無關句」被劫持回舊自陳。
    _hist = getattr(state, "convo_history", None) or []
    _last_model = next((h.get("text") for h in reversed(_hist) if h.get("role") == "model" and h.get("text")), None)
    if not (_last_model and share["text"][:20] in _last_model):
        return False
    anchor = persona.selfshare_followup_system(share["text"])
    msg = None
    if coach and coach.enabled:
        try:
            msg = coach.reply(text, "", state.convo_history, mood_hint=mhint,
                              now_ts=convo_now, self_presence=True, extra_system=anchor)
        except gemini.GeminiError as e:
            print(f"[chat] §0.85 自陳追問回覆失敗：{e}")
            msg = None
    if not msg:                                            # 無 LLM／失敗 → 誠實退路：至少接回那句、不跳題
        msg = "嗯，就是我剛跟你說的那個——" + share["text"][:40]
    _say(client, msg)
    _remember(state, "user", text, ts=user_ts)
    _remember(state, "model", msg)
    state.selfstate_open_ts = now_utc.timestamp()          # 保持追問窗開著
    _note_selfshare(state, msg, now_utc.timestamp())       # 這則接續也可被再追問（然後呢…）
    if not cfg.dry_run:
        state.save()
    return True


def _lookup_self_msg(state, message_id):
    """用 message_id 查回那則 bot 主動訊息在講哪條線（topic, text）；查不到回 (None, None)。"""
    for rec in reversed(getattr(state, "recent_self_msgs", None) or []):
        if message_id in (rec.get("ids") or []):
            return rec.get("topic"), rec.get("text")
    return None, None


_LIKE_WINDOW_SEC = SELF_TOPIC_WINDOW_SEC   # 被按讚後多久內，問「哪筆讚」還接得回那條（與自我在場窗同步）


def _handle_reaction(mr, coach, state, client, cfg):
    """👍 使用者對 bot 某則訊息按/改了反應（MessageReactionUpdated）：查回那則在講哪條線 → 記成 last_liked
    （之後問『我點哪筆讚』查得回），並當作一次正向接觸（餵心情、醞釀歸零、進對話記憶）。
    **靜默記錄、不自動插話**——避免和同批一起進來的文字訊息重覆回；bot 的下一則回覆會自然帶到。"""
    chat = mr.get("chat") or {}
    if cfg.telegram_chat_id and str(chat.get("id")) != str(cfg.telegram_chat_id):
        return
    new = mr.get("new_reaction") or []
    emoji = next((r.get("emoji") for r in new if r.get("type") == "emoji" and r.get("emoji")), "")
    if not emoji:                                  # 取消反應（new 空）/自訂貼圖反應 → 不處理
        return
    mid = mr.get("message_id")
    now_ts = mr.get("date") or time.time()
    topic, snippet = _lookup_self_msg(state, mid)
    val = reaction.read_sticker(emoji)             # 👍/❤… → positive
    # 🧭 只有按在「當前懸著那則」的 reaction 才算對它的回應；
    # 按舊訊息不會被錯連到後來新開的話頭。正向 reaction 是可觀察的接續，其餘只記已接觸。
    _active = dialogue_agency.open_initiative(state) if getattr(cfg, "dialogue_agency_enabled", False) else None
    if _active and mid in (_active.get("message_ids") or []):
        dialogue_agency.observe_contact(state, f"對這則按了 {emoji}", now_ts, engaged=(val == "positive"))
    state.last_liked = {"message_id": mid, "emoji": emoji, "topic": topic,
                        "text": snippet, "valence": val, "ts": now_ts}
    state.last_user_msg_ts = now_ts                # 反應也是聯絡＝陪伴
    if state.entropy is not None:
        state.entropy.self_stims_this_idle = 0
        state.entropy.reach_outs_this_idle = 0
        state.entropy.coping_reach_outs_this_idle = 0   # 🌀 §0.65 剛互動＝內在因應伸手預算也歸零
        md = reaction.sticker_mood_delta(val) * getattr(cfg, "mood_gain", 1.0)
        if topic and val == "positive":            # 針對性的肯定（讚到某條具體的線）→ 比泛泛更暖一點
            md += 0.1 * getattr(cfg, "mood_gain", 1.0)
        if md:
            state.entropy.mood = max(-1.0, min(1.0, state.entropy.mood + md))
        _bump_arousal(state, cfg, reaction.sticker_affect_delta(val)[1])   # 🧭 circumplex：被按讚/反應也推喚起軸
    if val == "positive":                          # 被肯定＝一點正向內在擾動
        state.tempo_charge_pending = max(getattr(state, "tempo_charge_pending", 0.0) or 0.0, 0.25)
    note = f"（你對我說的『{topic}』按了 {emoji}）" if topic else f"（你對我的一則訊息按了 {emoji}）"
    # ⏱ 按讚也是 user-turn：用反應的伺服器時間 now_ts（=mr['date']）落 ts；旗標關＝退回 time.time()。
    _remember(state, "user", note, ts=(now_ts if getattr(cfg, "remember_user_msgdate", True) else None))
    if not cfg.dry_run:
        state.save()


def _liked_fact(state, now_ts, window=_LIKE_WINDOW_SEC):
    """剛被按讚（窗內）→ 一條給 LLM 接地的事實句，讓 bot 答得出『你讚的是哪一筆』、不再說「我沒看到」；
    沒被按過/過窗回 ''。"""
    lk = getattr(state, "last_liked", None)
    if not lk or (now_ts - (lk.get("ts") or 0)) > window:
        return ""
    emoji = lk.get("emoji") or "👍"
    if lk.get("topic"):
        return (f"（已知事實：對方剛用 {emoji} 對「我上一則主動說的訊息」按了讚，那則我在講的是『{lk['topic']}』。"
                f"若對方問『我點哪筆讚／哪一筆』，就據此**具體**回答是這條，別說『我沒看到／不知道』。）")
    return (f"（已知事實：對方剛用 {emoji} 對我某則訊息按了讚，但那則沒對到某條具體的線/記寫（多半是一般回話）。"
            f"若被問是哪一筆，就誠實說那則不是在講某條記寫、但你有收到這份心意。）")


def _asks_which_liked(text):
    """像在問『你知道我點哪筆/哪一筆/哪條讚』這類——要直接用 last_liked 接地回、不走工具吐報表。"""
    t = text or ""
    which = any(w in t for w in ("哪筆", "哪一筆", "哪條", "哪一條", "哪則", "哪一則", "哪個", "哪一個"))
    act = any(w in t for w in ("讚", "按", "點", "互動", "reaction", "反應"))
    return which and act


def _apply_ac_flags(state, cfg):
    """把需要在深層純函式／共用出口讀到的執行旗標透傳到 state。"""
    state.AC_DEPENDENCY_GATE = bool(getattr(cfg, "ac_dependency_gate", False))
    state.AC_SPEC_PANEL = bool(getattr(cfg, "ac_spec_panel", False))
    # _say 沒有 cfg 參數；用 state 上的本次執行旗標辨認主動行動與控制顯示，欄位不落盤。
    # getattr 預設 False 讓既有單測的假 cfg 維持原輸出；真 Config 預設兩者皆開。
    state.DIALOGUE_AGENCY = bool(getattr(cfg, "dialogue_agency_enabled", False))
    state.NATURAL_PROACTIVE_VOICE = bool(getattr(cfg, "natural_proactive_voice_enabled", False))
    state.PACED_DIALOGUE = bool(getattr(cfg, "burst_paced_bubbles_enabled", False))


def _maybe_coupling_tone(state, cfg, self_presence):
    """🧭 加性扣合語氣染色：僅在 cfg.coupling_tone_enabled 開 ∧ self_presence=True 時回一段語氣指引（只調語氣、不報機制）。
    旗標關（預設）→回 ''＝所有路徑逐字同現行。"""
    if not (self_presence and getattr(cfg, "coupling_tone_enabled", False)):
        return ""
    return ac.coupling_tone(state)


def _observe_intent(state, update, text, route, ref, now_ts, cfg, mood=0.0):
    """🧭 把這次互動記進意圖履歷、讀出意圖向量、回 grounding hint（違常時溫暖呼應或好奇反問確認）。
    - **編輯訊息（edited_message）不算新對話行為** → 不記（避免改錯字的多次編輯被誤算成連發違常）。
    - **連發合併成的多行訊息**（『早安\\n早安\\n…』）逐行各記一筆並各自解析 kind → burst 開啟時違常仍算得準
      （否則合成成一則只記一筆、且整段 kind 變 fact_or_chat、連發偵測失效）。
    🧭 重複×心情長不耐（USER_REPEAT_FATIGUE_ENABLED）：在『確認連發』上累加耐性侵蝕（鏡像 self_asks→self_fatigue，
      對象換成『使用者重複』）→ 過門檻就把溫暖/好奇 hint 升級成『感知重複目的＋進一步問＋帶點無奈』；mood＝bot 當下心情。
    🌱 第三軸（正交）：另讀**探究弧**（發現→疑惑→因為→所以→然後→原來如此，inquiry_arc）——階段從累積湧現、
    軌跡形狀（卡住/膚淺跳階/退回/健康弧）塑形回應；由 INQUIRY_ARC_ENABLED 獨立 gate。
    旗標皆關 / 非新訊息 → 回 ''（intent_hint 不注入＝learned 通道逐位元同現狀）。"""
    if not update.get("message"):
        return ""
    di_on = getattr(cfg, "dialogue_intent_enabled", True)
    arc_on = getattr(cfg, "inquiry_arc_enabled", False)
    if not di_on and not arc_on:
        return ""
    di_hint = ""
    if di_on:
        lines = [ln.strip() for ln in (text or "").split("\n") if ln.strip()] or [text]
        for ln in lines:
            lk = route.kind if len(lines) == 1 else intent.resolve(ln, ref, getattr(state, "engrams", None), cfg=cfg).kind
            dialogue_intent.observe(state, ln, lk, now_ts, cfg)
        ir = dialogue_intent.read(state, getattr(state, "coupling", None), now_ts, cfg)
        state.intent_reading = ir
        if ir and ir.get("should_question"):             # 真要好奇反問 → 記時間（供反問冷卻＋probe_settled 主動結尾偵測）
            um = dict(getattr(state, "user_model", None) or {})
            um["last_questioned_ts"] = now_ts
            state.user_model = um
        di_hint = persona.dialogue_intent_hint((ir or {}).get("style"), (ir or {}).get("level", 0))
        # 🧭 使用者連發→重複×心情長不耐（鏡像 self_asks→self_fatigue）：只在**確認連發**(count≥n、curious_probe 同門檻)上累加，
        # 過門檻就把上面的溫暖/好奇 hint **升級／覆蓋**成「感知目的＋進一步問＋一點點無奈」。旗標關＝整段跳過、user_repeat 永不寫＝逐位元同現狀。
        if getattr(cfg, "user_repeat_fatigue_enabled", True) and ir and ir.get("count", 0) >= int(getattr(cfg, "intent_repeat_n", 4)):
            rk = ir.get("kind")
            if rk:
                win = int(getattr(cfg, "intent_repeat_window_sec", 300))
                ur = {k: v for k, v in (getattr(state, "user_repeat", None) or {}).items()
                      if (now_ts - (v.get("ts") or 0)) < win or k == rk}        # 順手剪窗外舊鍵（記憶體不無限長）
                sig = dialogue_intent._msg_sig(lines[-1] if lines else text)     # 連發那串的指紋＝最後一行
                lvl, n2, rec = dialogue_intent.repeat_fatigue(
                    ur, rk, sig, mood, now_ts, win,
                    int(getattr(cfg, "user_repeat_base_tol", 2)), float(getattr(cfg, "user_repeat_mood_band", 0.3)),
                    sim_threshold=float(getattr(cfg, "intent_sim_threshold", 0.85)))   # 與連發判定軸同一把門檻（修兩軸分歧）
                ur[rk] = rec
                state.user_repeat = ur                                          # 唯一寫入：每則使用者訊息一次（讀後算、burst 安全）
                ir["esc_level"], ir["esc_n"] = lvl, n2                          # 疊在 intent_reading 上（只在旗標開時才有這兩鍵）
                state.intent_reading = ir
                if lvl >= 1:
                    di_hint = persona.user_repeat_fatigue_hint(ir.get("anomaly_kind"), lvl,
                                                               ceiling=getattr(cfg, "user_repeat_ceiling", "stern"))   # 升級／覆蓋溫暖/好奇 hint（強度上限依 ceiling）
    # 🌱 探究弧：正交第三軸。**只對 fact_or_chat（使用者自述其思路）生效**——問 bot/事實/各 self_* 路由不是
    # 使用者自身的探究弧，跳過以維持三軸正交（修對抗驗證 med：避免誤收 self_revisit_why/followup 的「為什麼」）。
    arc_hint = ""
    if arc_on and route.kind == "fact_or_chat":
        arc_hint = _observe_inquiry_arc(state, text, now_ts, cfg)
    # 違常軸有話要說時讓它優先發聲、不與探究弧疊出兩段框定（修對抗驗證 nice_to_have）；否則才用探究弧。
    return di_hint or arc_hint


def _observe_inquiry_arc(state, text, now_ts, cfg):
    """🌱 偵測這則（連發多行則**逐行**）的探究階段（cue-level 弱訊號）→ 寫進 user_model['inquiry_log']（環形、跨重生）
    → 從近窗階段序列**湧現**弧讀數（軌跡形狀）→ 回 persona hint。階段只是弱訊號；只有累積出 ≥2 相異階段（depth≥1）
    才注入＝不腦補。讓位 closing_kind/backchannel/greeting（用 selfstate/greeting 算好旗標傳入 detect_stage）。純副作用集中於此。"""
    lines = [ln.strip() for ln in (text or "").split("\n") if ln.strip()] or [text]
    um = dict(getattr(state, "user_model", None) or {})
    log = list(um.get("inquiry_log") or [])
    appended = False
    for ln in lines:                                     # 逐行（與違常軸同粒度）→ burst 內的 notice→puzzle→so 微弧不被壓成單一階段
        stage = inquiry_arc.detect_stage(
            ln, is_farewell=selfstate.is_farewell(ln),
            is_backchannel=selfstate.is_backchannel(ln),
            is_greeting=bool(greeting.detect(ln)))
        if stage:                                        # 只記可辨識階段（None 不入，序列更乾淨）
            log.append({"stage": stage, "ts": now_ts})
            appended = True
    if appended:
        log = log[-max(1, int(getattr(cfg, "intent_log_max", 20))):]
        um["inquiry_log"] = log
        state.user_model = um
    window = max(60, int(getattr(cfg, "inquiry_arc_window_sec", 3600)))
    recent = [e for e in log if (now_ts - (e.get("ts") or 0)) <= window]   # 只把近窗階段算進弧（不跨會話硬接）
    arc = inquiry_arc.arc_trajectory(recent)
    if appended or (arc.get("depth") or 0) >= 1:         # 只在真有階段/弧時才動 intent_reading（修對抗驗證 med：不每則閒聊覆寫成空 arc、不吞非 dict 原值）
        ir = getattr(state, "intent_reading", None)
        state.intent_reading = {**ir, "arc": arc} if isinstance(ir, dict) else {"arc": arc}
    return persona.inquiry_stage_hint(arc)


# ⏱🧠 通盤常理審查跳過的 route.kind：工具/資料（確定性、無前提可違常）、純附和/道別（無實質前提）、
# 問候（早安@深夜等時段違和已由 greeting.py 自有處理）。其餘對話類才跑審查（省 LLM 呼叫）。
_PREMISE_SKIP_KINDS = {"clock", "cost", "stats", "smalltalk", "farewell", "greeting", "attachment"}


def _recent_repeat_ack(state, kind, now_ts, cfg, thing):
    """⏱ 確定性工具路徑(時間/資料，繞過 LLM)的『你剛問過』覺察：若同一意圖(kind)距上次少於 time_anomaly_gap_sec
    ＝太近連問（違反常理的時間訊號）→ 回一句人味前綴（用 temporal.spoken_gap 講相對時間、不報精確秒數）；否則 ''。
    旗標關／無前一筆／隔夠久 → ''＝逐位元同現狀、仍給正確答案。"""
    if not getattr(cfg, "time_anomaly_enabled", False):
        return ""
    um = getattr(state, "user_model", None) or {}
    gap = dialogue_intent.last_same_kind_gap(um.get("intent_log") or [], kind, now_ts)
    if gap is None or gap >= max(1, int(getattr(cfg, "time_anomaly_gap_sec", 120))):
        return ""
    return f"你{temporal.spoken_gap(gap)}才問過{thing}欸 😅 不過我再講一次：\n"


# 🧑‍🏫 對話教學（taught skill）的兩個時間閘（記憶體狀態 skill_pending / last_skill_propose_ts）。
SKILL_CONFIRM_WINDOW_SEC = 120     # bot 提議「要學成做法嗎」後，這麼久內的點頭/婉拒才算回答這個提議（逾期＝作廢、照常路由）
SKILL_PROPOSE_COOLDOWN_SEC = 90    # 兩次提議的最短間隔（防同一波連發騷擾）；由 cfg.skill_propose_cooldown_sec 覆蓋。
# 原為 600s（10 分）＝一次提議後 10 分鐘內**所有**新教學都被擋（截圖：學了 emoji 後、緊接教「聯想要嚴肅」卻不提議）；
# 調短＝主動連續教多件事時每件都提得出來（廣化觸發），精準仍靠下游 detect_skill_consensus＋淨化白名單＋提議→確認兩步。


# 🧑‍🏫 §0.89 考核/查驗式提問線索（使用者在測 bot「學會了嗎」）——本就指向 bot。
# 🔍 §0.89 對抗式審查（HIGH＋2×MED＋LOW 修）：短線索當無錨定子字串比對會誤觸良性長句。**根本錨定＝句首錨**：
#   不含「你」的短線索只在**句首**（前綴僅由引導詞/標點組成，見 _QUIZ_LEADIN_CHARS）＋線索後接終止符 才算——這一招同時擋掉
#   所有「第三方主詞在前」（他/老師/大家都/學生…學會了嗎）與同音長句（開會/約會/誤會了嗎），比原本「看前一字」穩固得多，
#   也讓短線索（會了嗎/學會沒/記得嗎）能安全收回。三條偵測面：
#   ① DIRECTED 精確子字串＝本就明確指向 bot、無良性碰撞（你學會了嗎/你會了嗎/考考你…）。
#   ② DIRECTED 彈性正則＝你/妳**緊接**理解/技能動詞（免第三方鑽進間隙）＋受詞/了＋嗎沒——收「你學會這個了嗎/你聽懂了嗎」。
#   ③ ANCHORED 句首短線索（見上）。測試你另立正則帶所有格守門（測試你的網路速度＝科技語、非考 bot）。
_SKILL_QUIZ_DIRECTED = ("你學會了嗎", "你學會沒", "你會了嗎", "你確定嗎", "你行嗎", "你行不行", "考考你")
_SKILL_QUIZ_TEST_RE = re.compile(r"測試你(?!的)")           # 測試你（考你）；「測試你的網路速度/程式」是所有格＝非考 bot
_SKILL_QUIZ_DIRECTED_RE = re.compile(                       # 你/妳**緊接**動詞（無前置間隙→第三方鑽不進）＋受詞/了＋嗎沒嘛
    r"[你妳](?:學會|學起來|學得會|聽懂|看懂|搞懂|弄懂|讀懂|懂|記住|記起來)[了它這那個都的]{0,4}[嗎沒嘛]")
_SKILL_QUIZ_ANCHORED = ("學會了嗎", "學會了沒", "學會沒", "學起來了嗎", "學起來沒", "懂了嗎",
                        "會了嗎", "會了沒", "記住了嗎", "記得嗎", "真的學會了嗎", "真的會了嗎")
# 句首引導詞/標點白名單：線索前綴只准由這些字組成（＝線索實質在句首、非句中第三方陳述）。刻意**不含**任何主詞/名詞字
# （他/她/老師/學生/大家…之首字皆不在內），故「他學會了嗎」「大家都懂了嗎」「老師學會了嗎」前綴含非白名單字＝不觸發。
_QUIZ_LEADIN_CHARS = set("你妳所以現在這那樣真的到底究竟都也還是不好麼嗯欸嘿就阿啊喔呃，。！？!?、… 　\n\t\r")
_QUIZ_TERMINATORS = set("？?！!。.，,、 　\n\t\r")            # 線索後可接的終止符（非內容續接）
_QUIZ_PARTICLES = set("啊吧喔哦吼耶欸齁囉呀哈嘛")            # 語氣詞收尾亦算終止


def _quiz_testing(t):
    """🧑‍🏫 §0.89（審查修）判定使用者是否在『考核/查驗』bot（要 bot 證明學會了）。
    directed 精確/彈性正則指向 bot 直接算；不含「你」的短線索須**句首錨**（前綴僅引導詞/標點）＋線索後終止，
    避免良性長句（開會了嗎/參考你/我真的學會了/學會了嗎啡/他學會了嗎/老師學會了嗎/大家都懂了嗎）誤觸。空字串＝False。"""
    if not t:
        return False
    if any(c in t for c in _SKILL_QUIZ_DIRECTED):
        return True
    if _SKILL_QUIZ_TEST_RE.search(t) or _SKILL_QUIZ_DIRECTED_RE.search(t):
        return True
    for cue in _SKILL_QUIZ_ANCHORED:
        i = t.find(cue)
        while i >= 0:
            j = i + len(cue)
            nxt = t[j] if j < len(t) else ""
            term_ok = (not nxt) or (nxt in _QUIZ_TERMINATORS) or (nxt in _QUIZ_PARTICLES)
            prefix_ok = all(c in _QUIZ_LEADIN_CHARS for c in t[:i])   # 前綴全是引導詞/標點＝線索在句首（無第三方主詞）
            if term_ok and prefix_ok:
                return True
            i = t.find(cue, i + 1)
    return False


def _skill_signals_external(state, cfg, now_ts, text=""):
    """🧑‍🏫 §0.57：外部回覆脈絡的當下活訊號（給 recall_skills 比對 always/情境觸發）。external=True＝允許 always 常駐風格。
    user_repeat/testing 讀 §0.47 的 state.intent_reading（esc_level/anomaly_kind）；late_night 由 cfg.timezone＋now 算。
    🤝 §0.76 審計（confirmed HIGH）：anomaly 的 testing 只認「連發 4 次一樣的社交句」——**真正的質疑句**
    （你根本答非所問吧/我在測試你）反而永不觸發 sit:testing 做法。補：這句本身帶質疑線索（reaction._CHALLENGE 同表）也算。"""
    sig = {"external": True}
    ir = getattr(state, "intent_reading", None) or {}
    sig["user_repeat"] = int(ir.get("esc_level", 0) or 0) >= 1
    _t = (text or "").lower()
    # 審查（LOW 修）：質疑線索須指向 bot（句含 你/妳）——「我被老闆說**沒用**」是訴苦、不是質疑 bot。
    # 🧑‍🏫 §0.89：testing 也認**考核/查驗式提問**——「學會了嗎/會了嗎/懂了嗎/記得嗎/你確定嗎/考考你」＝使用者在測 bot 學會沒。
    # 截圖根因：`[情境·被質疑]` 做法（教在「當我問學會了嗎時」）永不觸發，因原 testing 只認攻擊式質疑（你根本…）。
    # 這類考核句**本就指向 bot**（不必再要 你/妳），故獨立一組（攻擊式質疑仍須帶 你/妳，免「我被老闆說沒用」訴苦誤觸）。
    # 🔍 §0.89 審查修：所有「考核/測試」語表（含 測試你/考考你，帶所有格守門）統一由 _quiz_testing 判（錨定式、免良性子字串誤觸）；
    # CHALLENGE 支線回歸**純攻擊式質疑**（你根本…），不再夾帶「測試你」——否則「測試你的網路速度」也被這條夾帶觸發（LOW 過度觸發）。
    _quiz = _quiz_testing(_t)
    sig["testing"] = (ir.get("anomaly_kind") == "testing" or _quiz
                      or (("你" in _t or "妳" in _t) and any(w in _t for w in reaction._CHALLENGE)))
    try:
        tz = ZoneInfo(getattr(cfg, "timezone", "Asia/Taipei")) if ZoneInfo is not None else None
        if tz is not None:
            hr = datetime.fromtimestamp(now_ts, timezone.utc).astimezone(tz).hour
            sig["late_night"] = temporal.day_part(hr) == "深夜"
    except Exception:
        pass
    return sig


def _skill_signals_internal(state, cfg=None):
    """🌀 §0.57 part B：bot 自己的內在狀態訊號（給內在因應做法比對）。讀 state.entropy（真實代謝量、safe getattr）。
    ⚠️ 刻意**不用 charge**：charge 只在 heartbeat 更新、回覆當下多是 stale≈0 → charge≤門檻幾乎恆真＝把情境閘退化成
    always-on（對抗式審查 med）。改用回覆當下可靠的 hunger/mood：
      low_vitality（轉速太低/悶著）＝飢餓高（想要新東西卻沒有）＋心情非正向——真的是情境、不是每輪都觸發；
      high_hunger＝飢餓 ≥0.6；low_mood＝心情 ≤−0.3。"""
    ent = getattr(state, "entropy", None)
    mood = float(getattr(ent, "mood", 0.0) or 0.0) if ent is not None else 0.0
    hunger = float(getattr(ent, "hunger", 0.0) or 0.0) if ent is not None else 0.0
    lv = hunger >= 0.7 and mood <= 0.0
    # 🌀 §0.97 較溫和的「悶／轉速太低」訊號：被晾一陣（餓了想要新東西）＋心情沒上揚，就算不到極端也算「悶悶的」。
    # 修「內在型做法（sit:low_vitality）觸發窗太窄、幾乎等不到」的診斷——但**不洗版**：對一直有資料進來的使用者，餵飽時
    # hunger 低＝不誤觸，安靜一陣 hunger 升＝真的悶了才觸；且 coping 伸手仍受 notify/自有冷卻/quiet-after-chat/伸手預算
    # 諸閘節制（放寬的是「這一刻算不算悶」，不是「多久發一次」）。旗標關＝退回極端值＝逐位元同現狀。
    if getattr(cfg, "skill_lull_signal_enabled", True) if cfg is not None else False:
        lv = lv or (hunger >= 0.55 and mood <= 0.2)
    return {"low_mood": mood <= -0.3, "high_hunger": hunger >= 0.6, "low_vitality": lv}


def _skill_extra(state, cfg, route_kind, text, now_ts):
    """🧑‍🏫 召回當下該生效的『已學做法』（always 常駐風格＋情境觸發＋主題）→ 包成 coach.reply 的附加 system 片段
    （旗標關／非可注入路徑／無命中＝''）。淨化白名單把關在 plasticity 內（讀 KIND_SKILL 的唯一出口）。注入只限
    SKILL_INJECTABLE_KINDS（縱深防禦）。SKILL_SITUATIONS_ENABLED=0 → 退回 legacy 單一 topic 召回＝逐位元同現狀。"""
    if not getattr(cfg, "skill_recall_enabled", True):
        return ""
    if route_kind not in plasticity.SKILL_INJECTABLE_KINDS:
        return ""
    gate = getattr(cfg, "skill_capability_gate_enabled", True)   # 🚫 §0.73：做不到的動作不注入（殘留舊條也別讓 LLM 據以在回覆裡謊稱要做）
    if not getattr(cfg, "skill_situations_enabled", True):        # 一鍵退路：legacy 單一 topic 召回
        sk = plasticity.recall_skill(getattr(state, "engrams", None), route_kind, text, now_ts=now_ts)
        if sk and gate and plasticity.unsupported_capability(sk):
            sk = None
        return persona.taught_skill_hint(sk) if sk else ""
    sig = _skill_signals_external(state, cfg, now_ts, text=text)
    # 🧑‍🏫 §1.53 召回窗（SKILL_RECALL_WINDOW）：topic 型做法原本只認「**當句**逐字包含主題標籤」＝實務上
    # 幾乎永不觸發（使用者實測：學到的做法從沒真的用上），且沒被召回就沒有 touch 保鮮 → gain 0.6/半衰 21 天/
    # 門檻 0.5 ⇒ ~5.5 天靜默死亡＝死亡螺旋。放寬比對源＝當句＋近 3 則使用者訊息（主題最近幾句提過就算在場；
    # §0.89 sit-cue 同源受惠）；召回命中印一行 log＝終於看得見「做法真的套用了」。旗標關（getattr 預設 False）
    # ＝只看當句＝逐位元同現狀。
    _src = text
    if getattr(cfg, "skill_recall_window_enabled", False):
        _src = "\n".join([(text or "")] + list(
            echo.recent_user_texts(getattr(state, "convo_history", None) or [], k=3)))
    sks = plasticity.recall_skills(getattr(state, "engrams", None), route_kind, _src, signals=sig, now_ts=now_ts)
    if gate:
        sks = [s for s in sks if not plasticity.unsupported_capability(s)]
    if sks and getattr(cfg, "skill_use_refresh_enabled", True):   # 🤝 §0.76 審計（confirmed HIGH）：用到＝記憶保鮮（否則教一次 5.5 天後靜默失效、/skills 卻還顯示 75 天）
        plasticity.touch_skills(getattr(state, "engrams", None), sks, now_ts=now_ts)
    if sks and getattr(cfg, "skill_recall_window_enabled", False):
        print(f"[skill] 🧑‍🏫 §1.53 本輪召回 {len(sks)} 條已學做法（注入回覆脈絡；用到＝保鮮）")
    return persona.taught_skills_hint(sks)


def _self_skill_extra(state, cfg, now_ts):
    """🌀 §0.57 part B：召回『內在狀態因應』做法（low_vitality/high_hunger/low_mood 觸發）→ 包成 self-presence 附加 system。
    只在 bot 談自己/自我在場路徑注入（把教過的自處步驟化進對自己內在狀態的處理）；旗標關／無活訊號／無命中＝''。"""
    if not getattr(cfg, "skill_recall_enabled", True) or not getattr(cfg, "skill_internal_coping_enabled", True):
        return ""
    sig = _skill_signals_internal(state, cfg)
    if not any(sig.values()):
        return ""
    sks = plasticity.recall_skills(getattr(state, "engrams", None), None, "", signals=sig, now_ts=now_ts)
    if getattr(cfg, "skill_capability_gate_enabled", True):       # 🚫 §0.73：做不到的動作不注入
        sks = [s for s in sks if not plasticity.unsupported_capability(s)]
    if sks and getattr(cfg, "skill_use_refresh_enabled", True):   # 🤝 §0.76：用到＝記憶保鮮（同 _skill_extra）
        plasticity.touch_skills(getattr(state, "engrams", None), sks, now_ts=now_ts)
    return persona.self_coping_skill_hint(sks)


def _teaching_guard_hint(state, cfg, text):
    """🧑‍🏫 §0.59 Part 1a：對方這句像在『教你以後怎麼回應／怎麼做』，但**一般回覆路徑並沒有真的把它存成長期做法**
    （capture 只在 _maybe_propose_skill 的提議→『好』兩步握手成立）→ 回一段守則，讓 LLM 別在回覆裡謊稱『記下來了/會記住』
    （截圖：說詞 vs 真實）。真正固定成做法仍由後續 _maybe_propose_skill 的提議→確認負責（兩者可同輪並存＝『兩者都做』）。
    旗標關／無教學線索／已在提議握手中（skill_pending）＝''（不注入＝逐位元同現狀）。"""
    if not getattr(cfg, "teaching_guard_enabled", True):
        return ""
    if getattr(state, "skill_pending", None):        # 已在提議→確認握手中：讓那條路走完、別在回覆裡先自打矛盾
        return ""
    if not dialogue_intent.worth_skill_consensus(text):   # 沒有「以後/這種時候…」教學線索 → 免掛守則（同便宜前置門檻）
        return ""
    return persona.TEACHING_ACK_GUARD


def _promise_guard_hint(state, cfg, text, now_utc=None, tz=None):
    """🤝 §0.61 空口答應守門：這句像「到某時間叫醒/提醒我」的新請求、卻走到了一般聊天路徑（＝排程路由沒捕捉成功，
    可能時間講法解析不出）→ 掛守則：**別答應「到時候我會叫你」**（沒入帳＝守不了），誠實請對方用明確時間再說一次。
    截圖根因：「可以 30 分鐘叫我起床嗎」沒被記下、bot 空口答應、到點什麼都沒發生。排程捕捉成功的輪次早已在
    scheduled_promise 路由 return、到不了這裡＝不會誤掛。旗標關／非計時請求＝''（逐位元同現狀）。
    🤝 §1.11：可選 now_utc/tz 透傳 looks_like_timed_request＝結構閘時間判準改問 temporal（不傳＝同現狀）。
    🤝 §1.12：本輪 LLM 逃生閘已裁決「不是請 bot 到點做事」→ 不掛（自然聊天，例：20分鐘後我要開會你覺得呢）。"""
    if not getattr(cfg, "promise_ack_guard_enabled", True):
        return ""
    if not getattr(cfg, "scheduled_promise_enabled", True):   # 排程承諾整包關掉時，本守門也不掛（沒有「該入帳」前提）
        return ""
    if getattr(cfg, "promise_llm_rescue_enabled", True) and _TURN.get("promise_guard_llm_no"):
        return ""                                             # 🤝 §1.12 LLM 判「否」＝自然聊天；judge 失敗**不會**設此標記＝守則照掛
    if not selfstate.looks_like_timed_request(text, now_utc, tz):
        return ""
    # 🤝 §0.64 讓位：超出能力的請求（每小時/打電話…）歸能力閘守則管（誠實拒絕）——本守門的「請換明確說法」
    # 會暗示做得到、與拒絕矛盾，故不疊。旗標關＝promise_unsupported 不攔＝同 §0.61 現狀。
    if getattr(cfg, "promise_capability_gate_enabled", True) and selfstate.promise_unsupported(text):
        return ""
    return persona.PROMISE_ACK_GUARD


def _promise_cant_hint(state, cfg, text):
    """🤝 §0.64 原則一（聊天路徑兜底）：這句像在請 bot 做**超出能力**的約定（外部動作/高頻重複/外部條件）、
    卻沒被排程路由接走（大多本來就不可解析）→ 疊守則：誠實拒絕＋說明原因＋給做得到的替代，**絕不**「好我會的」。
    旗標關／非超能力請求＝''（逐位元同現狀）。"""
    if not getattr(cfg, "promise_capability_gate_enabled", True):
        return ""
    t = text or ""
    # 審查 confirmed：純敘述/過去/請教句（「我今天打電話給媽媽了」「怎麼叫外送比較便宜」）不掛拒絕守則——
    # 須有**請求框**（幫我/可以/請你/嗎…或指向我動詞）且非回顧/問責語氣，才是真的在請 bot 做。
    if any(s in t for s in selfstate._TIMED_REQ_SKIP):
        return ""
    if not (any(m in t for m in ("幫我", "可以", "可不可以", "能不能", "請你", "你能", "你幫", "要不要", "嗎"))
            or any(v in t for v in selfstate._CANT_TELLISH)):
        return ""
    kind = selfstate.promise_unsupported(text)
    if not kind:
        return ""
    return persona.promise_cant_hint(kind)


_STICKER_MENTION = ("貼圖", "貼紙", "sticker", "Sticker", "STICKER")


def _sticker_concept_hint(state, cfg, text):
    """🎴 §0.68：這句提到貼圖/sticker → 掛概念守則：**telegram sticker（要 file_id 真的送出的圖）≠ emoji（文字裡的符號）**；
    若這輪沒有要真的送貼圖、或手邊沒有可送的真貼圖，就別寫個 emoji（✨🎴😄）假裝那是貼圖——沒有就誠實說沒有、可請對方教一張。
    截圖根因：承諾「大的 telegram sticker」兌現卻只丟「✨」。旗標關／沒提到貼圖＝''（逐位元同現狀）。"""
    if not getattr(cfg, "sticker_concept_guard_enabled", True):
        return ""
    if not any(w in (text or "") for w in _STICKER_MENTION):
        return ""
    have = _has_sendable_sticker(state, cfg)
    return persona.sticker_concept_hint(have_sendable=have)


def _skill_accountability_extra(state, cfg, text, now_ts):
    """🧾 §0.60 做法問責接地：這句在問『學過的做法/約定』（meta 線索）或指涉某條做法內容（雙字重疊＋查核語氣）
    → 把真帳本（skills_brief）包成附加 system＝回答只准根據真帳本：照實引述、沒做到就承認、不編造不軟化
    （修截圖：否認有「重複提問→呵斥」約定＋把它幻覺成『溫暖地呼應』）。旗標關／非問責句＝''（逐位元同現狀）。"""
    if not getattr(cfg, "skill_accountability_enabled", True):
        return ""
    eng = getattr(state, "engrams", None)
    gate = getattr(cfg, "skill_capability_gate_enabled", True)
    matched = plasticity.skills_overlap(eng, text, now_ts=now_ts)
    if gate:   # 🚫 §0.73（審查 MED）：做不到的殘留舊做法別觸發問責框——否則帳本檢視已濾掉它、卻叫 bot「照空清單答」＝反過來否認一件真教過的事
        matched = [m for m in matched if not plasticity.unsupported_capability(m)]
    if not (matched or selfstate.is_skill_meta_question(text)):
        return ""
    # 🤝 排程/感覺承諾讓路（對抗式審查 confirmed）：這句只帶「約定/答應」味、無 skills 具體線索、也沒指涉某條
    # 做法內容，而**另一本承諾帳本**（鬧鐘/提醒/有感覺再說）真的有記錄 → 別用 skills 帳本框答案——「絕不編造
    # 清單裡沒有的做法」會引導 bot 自信否認一個真實存在的排程承諾。讓路給既有承諾帳本機制照常接。
    if not matched:
        t = (text or "").lower()
        promise_flavored = any(c in t for c in ("約定", "答應", "承諾", "說好"))
        skills_specific = any(c in t for c in ("skills", "做法", "教過", "教你", "學過", "學會", "學了", "學到"))
        has_other_ledger = bool(getattr(state, "scheduled_promises", None) or getattr(state, "feeling_promise", None))
        if promise_flavored and not skills_specific and has_other_ledger:
            return ""
    return persona.skill_accountability_hint(
        plasticity.skills_brief(eng, now_ts=now_ts,
                                drop_unsupported=getattr(cfg, "skill_capability_gate_enabled", True)))


def _join_extra(*parts):
    """把多段 extra_system 片段以換行接起（去空、保序）；coach.reply 的 system 疊加用。"""
    return "\n".join(p for p in parts if p)


def _maybe_propose_skill(client, state, cfg, coach, text, route_kind, history, now_ts):
    """🧑‍🏫 對話凝出『以後該怎麼回應』的共識 → bot 提議「要我把這學成做法嗎？」（設 skill_pending 待確認）。
    旗標關／非可注入路徑／冷卻內／已有待確認／前置門檻不過／LLM 不建議／蒸餾出的 prompt 過不了淨化 → no-op（不提議）。
    提議句以 model 角色記進對話史（讓下一句的『好』有上文）。skill_pending 為記憶體態（不持久化），重生即作廢。"""
    if not getattr(cfg, "skill_consensus_enabled", True):
        return
    # 🧑‍🏫🌊 §1.27 SKILL_OFFER_HOSTILE_GATE：敵意情境不發教學提議——氣頭上把「批評」當「教學」（截圖 7/12
    # 21:03 連環被罵時「你每次搞砸了」的「每次」∈ _SKILL_CUES 過了 worth 門檻 → bot 亂入括號提議「(要不要
    # 我把你在質疑/測試我時的回應方式記成做法？…)」＝答非所問還洩漏括號格式）。只**消費** §1.14 既有訊號
    # （reaction.is_hostile／state.hostile_streak——文字側更新在 intent.resolve 與所有提議呼叫端之前＝守門
    # 讀到的 streak 已含本句；reaction.py 一行不改、詞表鐵律不擴）＋近窗敵意（近 5 則使用者訊息內有敵意句
    # ＝氣頭未過。**對規格兩訊號的必要擴充**：偵察實測觸發句「你每次搞砸了」is_hostile=False、且前句
    # 「你明明就不行」也 False 已把 streak 歸零 ⇒ 只用兩訊號原事故照樣放行；近窗內「別騙人啦」=True 擋下）。
    # 被壓下的 offer 直接丟棄：不 _say、不 _remember、不寫冷卻、不暫存不補發。旗標關＝逐位元同現狀。
    if getattr(cfg, "skill_offer_hostile_gate_enabled", False) and (
            reaction.is_hostile(text)                                        # 本句敵意
            or (getattr(state, "hostile_streak", 0) or 0) >= 1               # §1.14 連擊（getattr 預設 0）
            or any(reaction.is_hostile(u)                                    # 近窗敵意（§1.28 同一把 helper）
                   for u in echo.recent_user_texts(getattr(state, "convo_history", None) or [], k=5))):
        return
    # 🧑‍🏫 §1.52 SKILL_OFFER_DOUBT_GATE：質疑/不信情境也不發教學提議（§1.27 的 sibling——那次是氣頭、
    # 這次是「是嗎/每次你這麼說/我都有些懷疑」＝懷疑但不敵意：「每次」過了 worth 門檻、LLM 判定又把懷疑
    # 誤當共識 → 亂入「要不要我把你在質疑/測試我時的回應方式記成做法？」＝答非所問（截圖 10:10，7/12
    # 同款事故第二次現形）。本句（含連發合成多行）或近 5 則使用者訊息帶質疑詞 → 提議直接丟棄（不送 LLM、
    # 不寫冷卻、不暫存）；質疑窗裡少提議＝安全側（提議本就 nice-to-have）。旗標關＝逐位元同現狀。
    if getattr(cfg, "skill_offer_doubt_gate_enabled", False) and (
            dialogue_intent.is_doubt_text(text)
            or any(dialogue_intent.is_doubt_text(u)
                   for u in echo.recent_user_texts(getattr(state, "convo_history", None) or [], k=5))):
        return
    injectable = route_kind in plasticity.SKILL_INJECTABLE_KINDS
    # 🌀 §0.59 Part 1b：自我在場/內在對話（self_*）路徑也可凝出『內在因應』做法並提議學成——但**只收 route-agnostic 的
    # always/sit 型**（見下方 trigger 檢查）：topic 型須 route 相等才召回、self_* 非可注入路徑＝會變召不回的死做法。
    # 旗標關（skill_selfroute_capture 或 skill_situations 關）＝只認 injectable＝逐位元同現狀（一鍵退路）。
    selfroute_ok = getattr(cfg, "skill_selfroute_capture_enabled", True) and getattr(cfg, "skill_situations_enabled", True)
    if not injectable and not selfroute_ok:
        return
    if getattr(state, "skill_pending", None):
        return
    if now_ts - (getattr(state, "last_skill_propose_ts", 0) or 0) < int(getattr(cfg, "skill_propose_cooldown_sec", SKILL_PROPOSE_COOLDOWN_SEC)):
        return
    if not dialogue_intent.worth_skill_consensus(text):   # 便宜前置門檻：沒有「以後/這種時候…」線索的輪次不送 LLM
        return
    if not (coach and getattr(coach, "enabled", False)):
        return
    det = coach.detect_skill_consensus(text, history)     # 失敗回 None（內部已吞 GeminiError）
    if not (det and det.get("should_propose") and det.get("distilled_prompt")):
        return
    if not plasticity.sanitize_skill_prompt(det.get("distilled_prompt")):   # 淨化白名單把關：過不了就不提議（更不會學）
        return
    # 🚫 §0.73 能力誠實：蒸餾出的做法動作是 bot 做不到的外部能力（打電話/傳簡訊/寄 email/設鬧鐘/偵測上線已讀/訂餐叫車）
    # → **學習當下就誠實拒絕**（別假裝要學、更別存進帳本＝說到做不到），講清楚做不到什麼＋還有什麼真做得到。走提議冷卻免每輪重講。
    if getattr(cfg, "skill_capability_gate_enabled", True):
        cap = plasticity.unsupported_capability(det.get("distilled_prompt"))
        if cap:
            line = persona.skill_capability_decline(cap)
            _say(client, line)
            _remember(state, "model", line)
            state.last_skill_propose_ts = now_ts
            return
    topic = det.get("topic_tag") or ""
    # §0.57：正規化觸發（always/sit:<情境>/topic）；情境型不套 fact_or_chat「須有主題」限制（見 capture_skill）。
    trigger = plasticity.normalize_trigger(det.get("trigger_raw", ""), topic) \
        if getattr(cfg, "skill_situations_enabled", True) else ""
    # 🌀 §0.59 Part 1b：self_* 路徑（非 injectable）只收 route-agnostic（always/sit）做法；topic 型（trigger==''）route 分桶
    # 在 self_* 桶永遠召不回＝死做法 → 不提議（免學了個永遠不生效的假做法）。injectable 路徑不受此限（同現狀）。
    if not injectable and not trigger:
        return
    # 🔁 去重：已有等價做法（同 route|topic|trigger 且生效）→ 不繞圈重提已學過的（修截圖「好→/skills 沒變」的空轉）。
    if plasticity.skill_has(getattr(state, "engrams", None), route_kind, topic, trigger, now_ts=now_ts):
        return
    _new_pending = {"route_kind": route_kind, "topic_tag": topic,
                    "prompt": det.get("distilled_prompt"), "trigger": trigger, "ts": now_ts}
    _defer_delivery = getattr(client, "defer_delivery_action", None)
    _pending_before = copy.deepcopy(getattr(state, "skill_pending", None))
    _propose_ts_before = getattr(state, "last_skill_propose_ts", 0)
    if not callable(_defer_delivery):
        state.skill_pending = copy.deepcopy(_new_pending)
    # speculative multi-turn 仍先占冷卻，防後續 part 再提出第二個 offer；若 final wire
    # 裁掉提議，deferred discard 會一起還原。pending 本身先不公開，避免後一句「好」
    # 在使用者尚未看到提議前就替 bot 自問自答、直接學成做法。
    state.last_skill_propose_ts = now_ts
    line = persona.skill_propose_line(topic, trigger)
    _say(client, line)
    _remember(state, "model", line)
    if callable(_defer_delivery):
        def _commit_offer(_actual):
            state.skill_pending = copy.deepcopy(_new_pending)
            state.last_skill_propose_ts = now_ts

        def _discard_offer(_actual):
            state.skill_pending = copy.deepcopy(_pending_before)
            state.last_skill_propose_ts = _propose_ts_before

        _defer_delivery(_commit_offer, _discard_offer, kind="skill_offer")


def _handle_skill_confirm(client, state, cfg, text, now_ts, user_ts=None):
    """🧑‍🏫 待確認的『學成做法』提議在窗內 → 判讀點頭/婉拒。回 True＝已處理本則（capture 或婉拒、呼叫端應 return）；
    回 False＝沒待確認、或逾窗、或既非點頭也非婉拒（呼叫端照常路由；逾窗會順手清掉作廢的 pending）。"""
    pend = getattr(state, "skill_pending", None)
    if not pend:
        return False
    if now_ts - (pend.get("ts") or 0) > SKILL_CONFIRM_WINDOW_SEC:   # 逾窗作廢
        state.skill_pending = None
        return False
    if selfstate.is_skill_confirm(text):
        # 🚫 §0.73 縱深防禦：即使對方點頭，動作做不到的做法也**不捕捉**、誠實拒絕（正常已在提議階段攔下；此處防旗標中途開啟/他路設 pending）。
        cap = plasticity.unsupported_capability(pend.get("prompt")) \
            if getattr(cfg, "skill_capability_gate_enabled", True) else None
        if cap:
            state.skill_pending = None
            line = persona.skill_capability_decline(cap)
            _say(client, line)
            _remember(state, "user", text, ts=user_ts)
            _remember(state, "model", line)
            if not cfg.dry_run:
                state.save()
            return True
        learned = plasticity.capture_skill(state.engrams, pend.get("route_kind"), pend.get("topic_tag"),
                                           pend.get("prompt"), now_ts=now_ts, trigger=pend.get("trigger", ""))
        state.engrams = plasticity.consolidate(state.engrams, now_ts=now_ts)
        # 🤝 §0.76 審計（confirmed HIGH）：說「記起來了」的**同一口氣**，consolidate 的每類上限(12)可能剛把這條
        # 最弱的新做法擠掉＝嘴上學會、帳本沒有。學後驗證真的還在，被擠掉就誠實說「記憶滿了、沒能留下」。
        if learned and not plasticity.skill_has(state.engrams, pend.get("route_kind"), pend.get("topic_tag"),
                                                pend.get("trigger", ""), now_ts=now_ts):
            learned = False
            _full_ack = ("我試著記了，但我的做法記憶已經滿了、這條沒能留下來……"
                         "你可以用 /skills 看看現有的，跟我說哪條可以忘掉，再教我一次這個。")
            state.skill_pending = None
            _say(client, _full_ack)
            _remember(state, "user", text, ts=user_ts)
            _remember(state, "model", _full_ack)
            if not cfg.dry_run:
                state.save()
            return True
        state.skill_pending = None
        ack = persona.SKILL_LEARNED_ACK if learned else "嗯，這個我這邊存不下來（內容不合適），就先照平常的來。"
        _say(client, ack)
        _remember(state, "user", text, ts=user_ts)
        _remember(state, "model", ack)
        if not cfg.dry_run:
            state.save()
        return True
    if selfstate.is_skill_reject(text):
        state.skill_pending = None
        _say(client, persona.SKILL_REJECT_ACK)
        _remember(state, "user", text, ts=user_ts)
        _remember(state, "model", persona.SKILL_REJECT_ACK)
        if not cfg.dry_run:
            state.save()
        return True
    return False        # 在窗內但非點頭/婉拒 → 留著 pending、照常路由（下一句的『好』仍可確認）


_MEDIA_KEYS = ("photo", "voice", "video", "audio", "document", "animation", "video_note", "location", "caption")


def _register_media_contact(msg, state, cfg):
    """📷 §1.06(B1) 使用者傳來**非文字非貼圖**的媒體（照片/語音/檔案…，含帶 caption 者）——以前整則被
    「沒文字就 return」丟掉＝情緒/接觸層完全看不見（用照片回話仍被當「被晾」、hunger 照漲）。
    最小修：記為一次**陪伴接觸**（更新 last_user_msg_ts＋醞釀/伸手歸零＋聊天等級的餵飽與微暖微醒），
    不回覆、不路由（媒體內容理解是另一題）。非擁有者 chat／非媒體訊息（如服務訊息）不記。"""
    chat = (msg.get("chat") or {})
    if cfg.telegram_chat_id and str(chat.get("id")) != str(cfg.telegram_chat_id):
        return
    if not any(k in msg for k in _MEDIA_KEYS):
        return
    now_ts = _now_from_update({"message": msg}).timestamp()
    if getattr(cfg, "dialogue_agency_enabled", False):
        dialogue_agency.observe_contact(state, "照片或媒體回應", now_ts)
    # 📈 §1.63 照片/語音/檔案也是「出現」＝入帳 contact 事件（同貼圖側理由：更新 last_user_msg_ts 重置
    # first 的安靜計時卻不留事件＝早晨被統計抹掉）。旗標關（getattr 預設 False）＝不記＝逐位元同現狀。
    if getattr(cfg, "user_habit_ground_enabled", False) and getattr(cfg, "habit_obs_fix_enabled", False):
        habits.note_contact(state, now_ts)
    state.last_user_msg_ts = now_ts
    ent = getattr(state, "entropy", None)
    if ent is not None:
        ent.self_stims_this_idle = 0
        ent.reach_outs_this_idle = 0
        ent.coping_reach_outs_this_idle = 0
        ent.hunger = max(0.0, ent.hunger - CHAT_NOURISH)     # 傳東西來＝被陪伴（與文字同等餵飽）
        _gain = getattr(cfg, "mood_gain", 1.0)
        _dv, _da = (reaction.human_affect_delta_for("") if getattr(cfg, "human_affect_enabled", False)
                    else reaction.affect_delta_for(""))       # 🧠 §1.75 human＝中性(0,+0.03)；關＝原微暖微醒(0.05,0.06)
        ent.mood = max(-1.0, min(1.0, ent.mood + _dv * _gain))
        _bump_arousal(state, cfg, _da)


def _self_state_bubble_cap(bubbles, route_kind, cfg):
    """🌊 §1.17 self_state 洪水收斂（SELF_STATE_CONVERGE）：使用者只問一件小事，self_state 卻吐一堆泡泡。
    根因：verbosity.assess 對 self_state（_ROUTE_BASE=1）＋ deep-cue「說說／為什麼」(+1) ＋ 正向 mood(≥0.5,+1)
    ＝ level 3 → _BUBBLES[3]=8；self_state topic==None 的 flow 無條件串接多個子句源湊滿 8 顆＝洗版（截圖 19:27）。
    這裡**只封串數**（_say 依 _TURN['bubbles'] 把 8 句合併成 ≤cap 顆），**不動 scale.level**——不改 token 預算/coach
    篇幅＝把行為改變面積壓到最小、只治洪水。與 §1.14 敵意收斂天然疊加：hostile 時 scale.bubbles 已是 2，
    min(2, cap)=2＝敵意仍更緊（『兩者可疊加取更小值』）。旗標 0＝不封＝逐位元同現狀。"""
    if not (getattr(cfg, "self_state_converge_enabled", True) and route_kind == "self_state"):
        return bubbles
    cap = max(1, int(getattr(cfg, "self_state_bubble_cap", 3)))   # max(1,·)＝保護 0/負值不封成 0 顆
    return cap if bubbles is None else min(bubbles, cap)


def _resolve_effective_route(text, state, cfg, now_ts):
    """解析 handler **真正會分派**的 route；burst planner 與主 handler 共用。

    `intent.resolve` 後還有數個依設定與狀態做的窄化／改道。若 planner 只看 raw kind，
    兩則看似同 route 的訊息可能在真 handler 走不同出口，合併後就會遺失其中一題。
    此函式只讀 state，不寫入帳本。
    """
    ref = referent.resolve(state, now_ts, SELFSTATE_FOLLOWUP_SEC)
    route = intent.resolve(text, ref, getattr(state, "engrams", None), cfg=cfg)
    if (route.kind == "cost" and getattr(cfg, "cost_query_tighten_enabled", False)
            and not selfstate.is_cost_question(text, tight=True)):
        route = intent.Intent("fact_or_chat")
    if (route.kind in ("greeting", "self_state", "other_mind")
            and getattr(cfg, "user_habit_ground_enabled", False)
            and habits.is_user_habit_question(text)):
        route = intent.Intent("fact_or_chat")
    if getattr(cfg, "habit_inventory_enabled", False) and habits.is_habit_inventory(text):
        route = intent.Intent("habit_inventory")
    if (route.kind == "fact_or_chat" and getattr(cfg, "convo_gap_stated_enabled", False)
            and selfstate.is_convo_gap_stated(text)):
        route = intent.Intent("convo_time")
    if (route.kind == "greeting" and getattr(cfg, "habit_obs_fix_enabled", False)
            and greeting.is_mention(text)):
        route = intent.Intent("fact_or_chat")
    if (route.kind in ("self_state", "cost", "stats")
            and getattr(cfg, "mood_coord_report_enabled", False)
            and _mood_data_hit(state, cfg, text, now_ts)):
        route = intent.Intent("fact_or_chat")
    return route, ref


def _handle_message_inner(update, coach, reader, data, snap, state, client, cfg, tz):
    _apply_ac_flags(state, cfg)                          # 🧭 透傳 AC 規格旗標（預設 False＝現行行為）
    _TURN["bubbles"] = None                              # 🗜️ 每輪重置篇幅尺度（沒算到＝原行為）
    try:
        _burst_n = int((update or {}).get("burst_n", 1) or 1)
    except (TypeError, ValueError):
        _burst_n = 1
    _burst_one_answer = (_burst_n >= 2 and getattr(cfg, "burst_one_answer_enabled", False))
    if _burst_one_answer:
        # 先在輪起點 arm：下面尚未進 verbosity 的確定性快路也必須遵守「同一波只送一則」。
        _TURN["bubbles"] = 1
    _TURN["ground_now"] = None                           # 🕐 §0.82 先清（早退路徑＝無錨＝_say 不守門）；主路徑下面才設真實此刻
    _TURN["last_reply"] = None                            # 🎴 §0.87 本輪互動回覆文字先清（防跨輪殘值被 _maybe_sticker 誤讀）
    _TURN.pop("promise_guard_llm_no", None)               # 🤝 §1.12 LLM「否」裁決只管當輪（防跨輪殘留讓守則永久失聲）
    _TURN.pop("greet_claim_fallback", None)               # 🕘 §2.22 問候保全只管當輪（_say 消費即清；這裡防上輪中途炸掉的殘留）
    _TURN.pop("speaker_ground", None)                     # 🪞 話者／時態地面只管當輪（旗標關時也不得沿用上輪）
    _TURN.pop("cur_user_text", None)                      # 🗣️ §2.24 本句 stash 只管當輪（供 §1.77 氣頭門在互動貼圖 lane 看得到本句）
    _TURN.pop("keep_claim_ground", None)                  # 🤝 §1.13B 假兌現守門的帳本接地只管當輪（防跨輪殘值誤攔）
    _TURN.pop("said_denial_ground", None)                 # 🤝 §1.20 否認守門的帳本接地同樣只管當輪
    _TURN.pop("sticker_denial_ground", None)              # 🎴 §1.23 貼圖否認守門的接地同樣只管當輪
    _TURN.pop("sticker_sent_this_turn", None)             # 🎴 §1.34 F4 本輪真送真相旗只管當輪（防跨輪殘值讓假送閘誤放）
    _TURN.pop("sticker_fakesend_arm", None)               # 🎴 §1.34 F4 假送閘 arm 只管當輪（旗標關＝不 arm＝_say 恆 no-op）
    _TURN.pop("sticker_fakesend_maint", None)             # 🎴 §1.34 F4 維護期誠實句選擇旗只管當輪
    _TURN.pop("sticker_fakesend_soft", None)              # 🎯 §1.70A 假送軟化旗只管當輪（旗標關/未 arm＝原整則替換）
    # 🔁 §1.71 重播守門 arm（互動輪起點就設＝這輪所有 _say 路徑（含插話巢狀/接回/暖收）都在防線內；
    # 旗標關（getattr 預設 False）＝不 arm＝_say 恆 no-op＝逐位元同現狀）。記錄側恆開（純內部）。
    _TURN["replay_guard"] = getattr(cfg, "reply_replay_guard_enabled", False)
    _TURN["act_first"] = getattr(cfg, "act_first_enabled", False)   # 🫧 §2.09 開頭純接話泡泡剝除（只管當輪）
    _TURN["short_dup_guard"] = getattr(cfg, "short_dup_guard_enabled", False)   # 🦜 §2.13 短句重複守門（只管當輪）
    _TURN.pop("act_first_sent", None)                # 🫧 §2.10 「原文→真送出」登記只管當輪
    _TURN.pop("sent_model_text", None)               # 🪞 本輪原文→真送達登記只管當輪（防上輪未 consume 的殘值）
    _TURN.pop("mood_data_line", None)                 # 🧭 §2.10 座標補救行只管當輪（真正的 arm 在 text/now_utc 都有值之後）
    _TURN.pop("echo_user_texts", None)                    # 🦜 §1.28 echo 剝除的比對素材只管當輪（防跨輪殘值誤剝）
    _TURN.pop("echo_whole_guard", None)                   # 🦜 §1.74 整則複誦守門旗只管當輪（旗標關＝原剝除行為）
    _TURN.pop("echo_prefix_run", None)
    _TURN.pop("nudge_stuck", None)                        # 🫸 §1.87 卡住旗只管當輪（旗標關＝不設＝恆不動）                    # 🦜 §1.86 開頭連續段複誦守門旗只管當輪（同上）
    _TURN.pop("tone_now", None)                           # 🎚️ §1.76 口吻整形的座標真值只管當輪（未 stash＝_say 恆 no-op）
    _TURN.pop("self_promise_ctx", None)                   # 🤖 §1.18 bot 自發承諾掃描的本輪上下文只管當輪（防陳舊 route/user_ts 錯判）
    _TURN.pop("self_promise_skip", None)                  # 🤖 §1.18 入帳 ack 的顯式排除標記同樣只管當輪
    _TURN.pop("recall_ground", None)                      # 🧭 §1.36 記寫回想守門的語料只管當輪（None＝未 arm＝_say 恆 no-op）
    _TURN.pop("recall_summary", None)                     # 🧭 §1.36 誠實句引用的最近一筆摘要同樣只管當輪
    _TURN.pop("wake_proj_strip", None)                    # 🌅 §1.39 自他邊界守門的 arm 只管當輪（旗標關/未 arm＝_say 恆 no-op）
    _TURN.pop("habit_claim_ground", None)                 # 📈 §1.42 作息宣稱守門的統計接地只管當輪（旗標關/未 arm＝_say 恆 no-op）
    _TURN.pop("away_claim_guard", None)                   # 🍽 §1.65 暫離常識守門的 arm 只管當輪（旗標關/未 arm＝_say 恆 no-op）
    _TURN.pop("self_feel_trim", None)                     # 🗜️ §1.43 感覺鋪陳修剪的 arm 只管當輪（旗標關/未 arm＝_say 恆 no-op）
    _TURN.pop("coord_claim_truth", None)                  # 🧭 §1.47 無接地座標數字守門的真值 stash 只管當輪（旗標關/未 arm＝_say 恆 no-op）
    _TURN.pop("mood_coord_grounded", None)                # 🧭 專用座標 lane 的相容觀測旗只管當輪（一般數據題另走 contract）
    _TURN.pop("mood_coord_contract", None)                # 🧭 §2.27 凍結快照＋時間層契約只管本輪，送一次即消費
    _TURN.pop("mood_contract_line", None)                  # 🧭 replay 後補 current 的短 anchor 同樣只管本輪
    _TURN.pop("ack_dedup_recent", None)                   # 🧵 §1.54 致意句去重的比對源只管當輪（旗標關/未 stash＝_say 恆 no-op）
    _TURN.pop("timejump_guard", None)                     # 🎭 §1.58 不演未來的 arm 只管當輪（旗標關/未 arm＝_say 恆 no-op）
    _TURN.pop("write_claim_ground", None)                 # 🕐 §1.60 記寫時間錨只管當輪（旗標關/未 stash＝守門/hint 恆 no-op）
    if coach is not None:
        coach._turn_length = None
    # 👍 訊息反應更新（使用者對 bot 訊息按/改 emoji）：沒有 message 本體，先在這裡接住、記下「讚的是哪一筆」。
    mr = update.get("message_reaction")
    if mr:
        _handle_reaction(mr, coach, state, client, cfg)
        return
    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return
    # ✏️ §1.06(B2) 編輯訊息＝改字、不是新互動：情緒/接觸管線**不重跑**（否則同一句話 V/A 重複疊加、
    # hunger 重複釋放、last_user_msg_ts 被倒帶回原訊息時間、他心模型重複學習）。路由/回覆行為不變。
    _edited = bool(update.get("edited_message")) and not update.get("message")
    chat = msg.get("chat") or {}
    text = (msg.get("text") or "").strip()
    sticker = msg.get("sticker")
    if not text and not sticker:
        if not _edited:
            _register_media_contact(msg, state, cfg)         # 📷 §1.06(B1) 照片/語音/檔案＝也是互動：記接觸、不再整個隱形
        return
    if cfg.telegram_chat_id and str(chat.get("id")) != str(cfg.telegram_chat_id):
        print(f"[chat] 忽略非擁有者 chat {chat.get('id')}")
        return

    if evidence.enabled(cfg) and text:
        answer = activity.reply(state, text, _now_from_update(update).timestamp(),
                                tz or ZoneInfo(getattr(cfg, "timezone", "Asia/Taipei")))
        if answer is None:
            answer = evidence.reply(state, update, (data or {}).get("records", []),
                                _now_from_update(update).timestamp(),
                                tz or ZoneInfo(getattr(cfg, "timezone", "Asia/Taipei")))
        if answer is not None:
            client.certify(answer)
            _say(client, answer)
            _remember(state, "user", text, ts=_now_from_update(update).timestamp())
            _remember(state, "model", answer, ts=_now_from_update(update).timestamp())
            state.last_user_msg_ts = _now_from_update(update).timestamp()
            if not getattr(cfg, "dry_run", False):
                state.save()
            return

    if viewpoint.enabled(cfg) and text and _viewpoint_reply(update, state, client, cfg):
        return

    # 😀 純貼圖（沒文字）＝對方此刻對這則對話的情緒訊號 → 自然回一句、語氣對應；並記為「最近反應」
    # 供後續回覆語氣參考（與記寫的價性分開）。以前這種訊息會被「沒文字就 return」直接丟掉。
    if not text and sticker:
        if _edited:                                          # ✏️ §1.06(B2) 編輯貼圖：不重 ack、不重疊情緒訊號
            return
        # 🌊 §1.14 敵意連發計數（貼圖側，與文字側同一把訊號源＝read_sticker，:1154 sticker_signal 同源）：
        # 負向貼圖（哭臉/生氣）＝氣頭延續 → streak+1；正向貼圖＝暖訊號（與 _WARM 文字同義）→ 歸零；
        # **中性貼圖不動**（丟個表情不代表氣消也不代表更氣）。旗標關＝不讀不寫此欄＝逐位元同現狀。
        if getattr(cfg, "hostile_converge_enabled", True):
            _sval = reaction.read_sticker((sticker or {}).get("emoji") or "")
            if _sval == "negative":
                state.hostile_streak = (getattr(state, "hostile_streak", 0) or 0) + 1
            elif _sval == "positive":
                state.hostile_streak = 0
        # 📈 §1.63 純貼圖也是「出現」＝入帳 contact 事件：這條路會更新 last_user_msg_ts（重置 first 的
        # 安靜計時）卻不留事件＝清晨的貼圖早安把當天第一句從統計裡雙重抹掉（自己隱形＋害後續文字
        # gap<4h 不算 first）。旗標關（getattr 預設 False）＝不記＝逐位元同現狀。
        if getattr(cfg, "user_habit_ground_enabled", False) and getattr(cfg, "habit_obs_fix_enabled", False):
            habits.note_contact(state, _now_from_update(update).timestamp())
        if getattr(cfg, "dialogue_agency_enabled", False):
            dialogue_agency.observe_contact(state, "貼圖回應", _now_from_update(update).timestamp())
        _handle_sticker(sticker, update, coach, state, client, cfg)
        return

    # 🫶 對方訊息情緒鮮明（暖意/誇讚/好笑/低落）時，bot 對「那一則」按一個 emoji 表達當下情緒（節流）。
    # 在回話之前先按——像人讀到一句話先點個 ❤️ 再回。指令/空訊息會被略過。
    _maybe_react(update, text, state, client, cfg)

    # 在 bot 內手動調「通盤敏感度 k」：/k 1.0、敏感度 0.5、/k（查詢）、/k 預設（清除覆蓋）。
    low = text.lower()
    command = low.split(None, 1)[0] if low.startswith('/') else None
    if command and command not in _KNOWN_COMMANDS:
        client.send(_unknown_command_reply(command))
        return
    if command == '/help':
        client.send('查花費用 /cost，看目前狀態用 /status，查看約定用 /promises。'
                    '\n其他事情也可以直接說；我不支援的指令會明說，不會當作已經做了。')
        return
    if command == '/cost':
        if len(text.split(None, 1)) > 1:
            client.send('/cost 目前只查整體費用，還不支援附加參數。直接傳 /cost 就可以。')
            return
        try:
            result = datatools.api_cost(datatools.ToolCtx(
                data, snap, tz, _now_from_update(update), meter=getattr(coach, 'meter', None), state=state))
        except Exception as exc:
            print(f'[cost] 查詢失敗：{type(exc).__name__}')
            result = '我知道你要查費用，但這次讀取費用紀錄失敗了，還沒有查到結果。'
        client.send(result)
        return
    if command in ("/k", "/sensitivity", "/敏感") or (not command and "敏感度" in text):
        if any(w in low for w in ("reset", "預設", "default", "清除", "恢復")):
            state.sensitivity_override = None
            if not cfg.dry_run:
                state.save()
            client.send(f"🎚️ 已恢復敏感度預設 k＝{getattr(cfg, 'selfstate_sensitivity', 2.0)}。")
            return
        m = re.search(r"-?\d+(?:\.\d+)?", text)
        if m:
            k = max(-3.0, min(5.0, float(m.group(0))))
            state.sensitivity_override = k
            if not cfg.dry_run:
                state.save()
            client.send(f"🎚️ 敏感度 k 基準已設為 {k}（數字越小越敏感、越容易宣告湧現；負值最鬆、預設 2.0 嚴格）。\n"
                        "這是**基準**——實際門檻仍會隨我的活力呼吸與內在熵在約 ±0.7 內浮動（剛醒偏嚴、健康久了放鬆）；"
                        "/status 看得到「判定當時實際用的 k」。同一把 k 通盤管三條：recur z>k、perc τ=μ+k·σ、dxi 整合>μ+k·σ 且 分化>k·σ。")
        else:
            cur = state.sensitivity_override if state.sensitivity_override is not None else getattr(cfg, "selfstate_sensitivity", 2.0)
            client.send(f"🎚️ 目前敏感度 k＝{cur}"
                        f"{'（手動）' if state.sensitivity_override is not None else '（設定檔預設）'}。\n"
                        "傳「/k 1.0」或「敏感度 0.5」來調（可設負值如「/k -1」最鬆）；「/k 預設」恢復。負值＝最鬆、2.0＝嚴格。")
        return

    # 在 bot 內手動調「生命迴圈轉速」＝心跳快慢（環間等待秒數）：/pulse 2、心跳 1.5、/pulse（查詢）、/pulse 預設。
    if command in ("/pulse", "/心跳", "/感覺心跳") or (not command and "心跳" in text and re.search(r"\d", text)):
        if any(w in low for w in ("reset", "預設", "default", "清除", "恢復")):
            state.pulse_override = None
            if not cfg.dry_run:
                state.save()
            client.send(f"🫀 已恢復心跳轉速預設＝環間 {getattr(cfg, 'lifeloop_wait_secs', 3.0)} 秒。")
            return
        m = re.search(r"\d+(?:\.\d+)?", text)
        if m:
            secs = max(0.5, min(60.0, float(m.group(0))))
            state.pulse_override = secs
            if not cfg.dry_run:
                state.save()
            client.send(f"🫀 心跳轉速已設為環間 {secs:g} 秒（一圈約 {round(secs * 5, 1)} 秒；"
                        "越短跳越快、對話越即時，檢查也越頻繁）。判定隨每圈跑、只在有新感覺時才出聲。")
        else:
            cur = state.pulse_override if state.pulse_override is not None else getattr(cfg, "lifeloop_wait_secs", 3.0)
            client.send(f"🫀 目前心跳轉速＝環間 {cur:g} 秒"
                        f"{'（手動）' if state.pulse_override is not None else '（設定檔預設）'}。"
                        "傳「/pulse 2」調快慢、「/pulse 預設」恢復。")
        return

    # /status → 旋鈕＋最近背景判定的數值。
    if low.startswith(("/status", "/狀態")):
        client.send(_status_text(state, cfg, snap))
        return

    # 🧭 對話能動性對帳：只列真送達的主動行動、下一句的可觀察結果與學到的傾向，不經 LLM、不讀心。
    if low.startswith(("/agency", "/能動性")) and getattr(cfg, "dialogue_agency_enabled", False):
        client.send(dialogue_agency.audit_text(state, time.time()))
        return

    # 📈 §1.63 /habits → 習慣觀測對帳（確定性、不經 LLM）：bot 真的記到哪些作息事件＋算出來的統計——
    # 「09:54–20:58」這種爭議能當場對資料源。旗標關＝落到下面泛 '/' 固定招呼＝同現狀。
    if low.startswith(("/habits", "/習慣")) and getattr(cfg, "user_habit_ground_enabled", False) \
            and getattr(cfg, "habit_obs_fix_enabled", False):
        _ha = habits.audit_text(state, time.time(), tz)
        if getattr(cfg, "habit_absence_enabled", False):            # 🌾 §1.79 附「日課偵測」對帳（為什麼沒問／會問什麼）
            _ha += "\n" + _habit_absence_audit(state, cfg, data, time.time(), tz)
        client.send(_ha)
        return

    # 🌐 §1.95 /worldline → 外面的世界對帳＋白名單管理（allow/mute）。**唯讀，除了 allow/mute 會寫白名單**。
    # 🤝 §2.02 /promises → 約定帳本對帳（唯讀、不經 LLM）。為什麼要有：帳本是這 repo 出事最多的子系統
    # （§0.64/§0.66/§0.78/§1.13/§1.18/§1.44/§1.64/§1.85/§1.88/§1.89…），卻**沒有任何辦法看到它裡面長怎樣**——
    # 截圖那次「同一個約定連來兩則我來了」，我只能從對話反推是不是兩筆帳，不能直接看。
    if low.startswith(("/promises", "/約定", "/帳本")):
        client.send(_promises_audit(state, cfg, time.time(), tz))
        return
    if low.startswith(("/worldline", "/外面", "/世界")):
        _arg = text.split(None, 1)[1].strip() if len(text.split(None, 1)) > 1 else ""
        if _arg.startswith("allow "):
            _lab = _arg[6:].strip()
            if _lab:
                _al = list(getattr(state, "worldline_allow", None) or [])
                if _lab not in _al:
                    _al.append(_lab)
                    state.worldline_allow = _al
                    if not getattr(cfg, "dry_run", False):
                        state.save()
                client.send(f"🌐 好，「{_lab}」我可以拿去外面搜了。送出去的只會是「{wlmod.build_query(_lab)}」，"
                            "你寫的內容一個字都不會離開這裡。要收回就打 /worldline mute " + _lab)
                return
        # 🌐 §2.01 他自己來要的那一次（使用者：「要有作用，等了一陣子還是沒看到」）：跳過「你在場/深夜」與
        # 兩道冷卻——那些是「別在你面前自言自語」的**禮貌**閘，他親口要的時候不適用；白名單與總量上限照舊。
        # 沒開口一定要**當場講出原因**，否則他面對的又是一片安靜。
        if _arg in ("now", "現在", "撞", "撞一下", "去查", "查"):
            _wl_r = _worldline_emit(client, state, cfg, coach, _now_from_update(update), data=data, force=True)
            if _wl_r:
                client.send("🌐 這次沒查成：" + _wl_r)
            return
        if _arg.startswith("mute "):
            _lab = _arg[5:].strip()
            state.worldline_allow = [x for x in (getattr(state, "worldline_allow", None) or []) if x != _lab]
            if not getattr(cfg, "dry_run", False):
                state.save()
            client.send(f"🌐 收回了，「{_lab}」我不會再拿去搜。")
            return
        client.send(_worldline_audit(state, cfg, data, time.time()))
        return

    # 🪪 §1.94 /abilities → 能力盤點＋願望帳（確定性、不經 LLM、唯讀）：我有哪些機制、哪些**真的用出來過**、
    # 以及我從自己的原始碼裡看出來的缺口（每個缺口都自帶機器跑得動的驗收條件＝寫不出驗收就產不出來）。
    if low.startswith(("/abilities", "/能力", "/盤點")) and getattr(cfg, "self_roster_enabled", False):
        client.send(_roster_audit(state, cfg, time.time()))
        return

    # 🔮 §1.92 /foresight → 預想對帳（確定性、不經 LLM）：候選幾個、在世假設是誰、猜中/想錯幾次、
    # **今天是被哪一道閘擋下**——這條 lane 的預設失敗模式是「從不觸發」，沒有對帳就沒人會發現它沉默。
    if low.startswith(("/foresight", "/預想")) and getattr(cfg, "foresight_enabled", False):
        client.send(_foresight_audit(state, cfg, data, time.time()))
        return

    # 🩺 §1.68 /moodwatch → 座標回報訂閱對帳（確定性、不經 LLM）：訂閱在不在、基準/此刻/Δ、門檻、冷卻剩多少、
    # tick 心跳——「為什麼沒報」當場看得到。旗標關＝落到下面泛 '/' 固定招呼＝同現狀。
    if low.startswith(("/moodwatch", "/座標回報")) and getattr(cfg, "mood_watch_enabled", False):
        client.send(_mood_watch_status_text(state, cfg, time.time()))
        return

    # 🧩 /ac → AC 框架此刻活評（逐格扣合＋時間感 E＋餘量標記）＝整座架構透過 AC 格的可讀儀表板（確認運作如常）。
    if low.startswith(("/ac", "/意識框架", "/框架")):
        client.send(ac.lattice_text(state, time.time()))
        return

    # 🌗 /phenomenal → 右半 I↔(E×P) 三位互構的此刻自陳（朝向/修正/整合的當下，各 act×建模質地×場；P 標 modeled、附餘量）。
    if low.startswith(("/phenomenal", "/現象", "/三列")):
        client.send(phenomenal.voice_structure(state))
        return

    # 🧑‍🏫 /skills → 檢視「跟你學過的回應做法」（KIND_SKILL 印痕）：意圖｜主題＋淨化後做法摘要（過淨化白名單）。
    if low.startswith(("/skills", "/做法", "/學到的做法")):
        # 🤝 §0.89 檢視＝保鮮：使用者主動 curate 的做法刷新遺忘時鐘（只碰 last_ts、不觸發任何行為＝零過度觸發風險），
        # 修「教過的內在因應做法在首次觸發前就 5.5 天靜默淡忘」的存活死角。旗標關＝不刷＝逐位元同現狀。
        _refresh = getattr(cfg, "skill_view_refresh_enabled", True)
        brief = plasticity.skills_brief(getattr(state, "engrams", None), now_ts=time.time(),
                                        drop_unsupported=getattr(cfg, "skill_capability_gate_enabled", True),
                                        refresh_alive=_refresh)
        client.send(("我跟你學過的回應做法：\n" + brief) if brief else "我還沒跟你學過什麼特定的回應做法。")
        if _refresh and not getattr(cfg, "dry_run", False):
            state.save()                                  # 持久化刷新的 last_ts（旗標關＝不進此段＝不多存）
        return

    # 🧬 /forget → 清掉可塑層學到的東西（相處偏好＋路由更正＋🪞自我表達的用爛母題＋🧑‍🏫學過的做法）＝「忘掉你對我的學習，重新開始」的安全閥。
    if low.startswith(("/forget", "/忘記", "/忘掉")):
        n = len(getattr(state, "engrams", []) or [])
        state.engrams, state.route_learn, state.last_routed = [], None, None
        state.skill_pending, state.last_skill_propose_ts = None, 0     # 🧑‍🏫 連待確認的做法提議與冷卻一起歸零
        state.recent_self_motifs = []     # 🪞 Phase 6：engrams 整列清掉已順帶清掉 selfexpr 印痕；這裡再清「最近自我母題」（否則 /forget 後第一次抱怨仍會 mark_stale 殘留母題）
        if not cfg.dry_run:
            state.save()
        client.send(f"好，我把學到的相處偏好、路由更正、跟你學過的做法、以及我學著怎麼表達自己的那些印痕都忘掉了（清掉 {n} 條），我們重新開始。")
        return

    # Telegram 指令（/start /help…）→ 固定招呼＋確定性狀態；每次一致、不經 LLM、不洗 API。
    if text.startswith("/"):
        client.send(_command_reply(snap) if command == '/start'
                    else '我認得這個指令，但這項功能目前沒有啟用，所以這次沒有執行。')
        return

    if all(_sticker_inventory_question(line) for line in text.splitlines() if line.strip()):
        reply = _sticker_inventory_reply(state, cfg)
        if _say(client, reply) is not False:
            _remember(state, 'user', text, ts=_now_from_update(update).timestamp())
            _remember(state, 'model', reply)
            if not cfg.dry_run:
                state.save()
        return

    if not (coach and coach.enabled):
        client.send("（這個 bot 還沒設定 GEMINI_API_KEY，目前只能做監測推播、無法對話。）")
        return

    now_utc = _now_from_update(update)   # 「你這則訊息的時間」（message.date）：給顯示/晝夜/時機（與你看到的一致）
    # 對話時間軸的「現在」用**牆鐘** time.time()——量距時的『現在』一律牆鐘。
    convo_now = time.time()
    # ⏱ user 訊息存進 convo_history 的 ts 改用 message.date（_user_msg_ts），不用處理當下的 time.time()：
    # 修「已過多久」感知偏短——time.time() 是『回覆生成完才落盤』的牆鐘、本就晚於送出；崩潰重抓/離線補送時更會把
    # 12:17 的舊訊息蓋上 ≈12:46 的新牆鐘 ts，使 convo_now − ts 由 43 分縮成 14 分（截圖症狀）。message.date 來自 Telegram
    # payload、重抓仍穩定＝該則 ts 恆為真實送出時刻，convo_now(牆鐘) − message.date ＝真實已過時間（兩鐘皆 NTP、亞秒差）。
    # 旗標關＝退回 None＝_remember 用 time.time()＝逐位元同現狀。model 輪無 message.date、仍用牆鐘（於下方 _remember 預設）。
    _user_msg_ts = now_utc.timestamp() if getattr(cfg, "remember_user_msgdate", True) else None
    # 🕐 §0.82 全域鐘點守門的本輪真實此刻（＝這則訊息的 message.date，與注入 LLM 的硬錨同源）；旗標關＝None＝_say 不守門＝同現狀
    if getattr(cfg, "global_clock_guard", True):
        _TURN["ground_now"] = (now_utc, tz or (ZoneInfo(getattr(cfg, "timezone", "Asia/Taipei")) if ZoneInfo is not None else None))

    # 🧭 對話能動性：在覆寫 last_user_msg_ts 之前，把這句連回 bot 最近一次**自己主動**開的話頭。
    # 只做可觀察分類（接續／短接住／轉向／拒絕），不把沉默或文字解釋成使用者內心；結果稍後注入
    # 每條 LLM 回覆路徑，並跨重生反饋下一次主動 lane 的競爭順序。
    _agency_reading = None
    if not _edited and getattr(cfg, "dialogue_agency_enabled", False):
        _agency_reading = dialogue_agency.observe_reply(state, text, now_utc.timestamp())

    # 對話時機感（只內化、不明講）：讀這則訊息相對「上次對話／上次主動開口」的時機——
    # 交錯/久別/回得慢 → 注入內在熵電量 C（下一圈消化，bot 真的被節奏影響），並取一個 mood
    # 染**這則回覆**的語氣（嚴禁點破時機本身，見 tempo.py）。對所有對話路徑都先記下時機與更新游標。
    _mood, _charge = tempo.read_tempo(now_utc.timestamp(), datetime.now(timezone.utc).timestamp(),
                                      state.last_user_msg_ts, state.last_push_ts)
    if _charge and not _edited:
        state.tempo_charge_pending = max(state.tempo_charge_pending, _charge)
    # 📈 §1.42 使用者習慣捕捉（必須在 last_user_msg_ts 更新**之前**：gap 靠更新前的值算「這段的第一句」）：
    # 每則使用者訊息記事件（msg/first/greet_*）進 state.habit_events（FIFO、跨重生）＝bot 講他的習慣時有真資料可照。
    # 編輯訊息不算新接觸不記；旗標關（既有測試假 cfg 的 getattr 預設 False）＝不捕捉＝逐位元同現狀。
    if not _edited and getattr(cfg, "user_habit_ground_enabled", False):
        habits.note(state, text, now_utc.timestamp(), prev_ts=(getattr(state, "last_user_msg_ts", 0) or 0),
                    mention_guard=getattr(cfg, "habit_obs_fix_enabled", False))   # 📈 §1.63 「我常跟你說早安喔」不記成 greet_*
    # 🍽 §1.65 暫離常識（AWAY_SENSE）：他宣告要去吃飯/洗澡/開會（無時距）→ 記 state.user_away（活動＋常識最短
    # 時距）＝之後回合的事實卡有「才過 N 分、常識上還沒回來」可講（截圖：11:58「吃飯去」、12:00 bot 就問「你現在
    # 吃飽了嗎」）。他自己說回來了/吃飽了＝清；上一輪已標 back（卡片講過「他大概回來了」）＝這輪清；超過 6h 沒下文
    # ＝陳舊清。旗標關（getattr 預設 False）＝不讀不寫此欄＝逐位元同現狀。
    if not _edited and getattr(cfg, "away_sense_enabled", False):
        _aw = getattr(state, "user_away", None)
        if _aw and (_aw.get("back") or (now_utc.timestamp() - (_aw.get("ts") or 0)) > 6 * 3600
                    or selfstate.is_back_statement(text)):
            state.user_away = _aw = None
        _ann = selfstate.leave_announce(text)
        if _ann:
            state.user_away = {"act": _ann[0], "ts": now_utc.timestamp(), "min_s": _ann[1] * 60}
        elif _aw and (now_utc.timestamp() - (_aw.get("ts") or 0)) >= (_aw.get("min_s") or 0):
            _aw["back"] = True                            # 時間夠了：這輪卡片講「他大概回來了」、下一輪清
    if not _edited:                                          # ✏️ §1.06(B2) 編輯不當新接觸（不倒帶 last_user_msg_ts）
        # 🔁 §2.03 這則是不是「同一波的第二則」（他把一個意思拆成連著兩則送）——重播守門全剝空時據此
        # 改用短承接，而不是那句把選擇權丟回去的罐頭。**必須在覆寫 last_user_msg_ts 之前算**。
        _prev_u = getattr(state, "last_user_msg_ts", 0) or 0
        _TURN["replay_same_wave"] = bool(getattr(cfg, "replay_wave_ack_enabled", False) and _prev_u
                                         and 0 <= now_utc.timestamp() - _prev_u <= _SAME_WAVE_SEC)
        state.last_user_msg_ts = now_utc.timestamp()
    # 🔗 對話耦合（觀測）：使用者一說話＝觸發 I_bot、依訊號估 Î_user（純內在狀態，本階段不改對外行為）。
    # latency＝距 bot 上次說話多久（＝對方回得多快）；無前話則 None。
    if state.coupling is None:
        state.coupling = coupling.Coupling()
    _last_bot_ts = next((t.get("ts") for t in reversed(state.convo_history or [])
                         if t.get("role") == "model" and t.get("ts")), 0) or 0
    coupling.engage(state.coupling, text, now_utc.timestamp(),
                    (now_utc.timestamp() - _last_bot_ts) if _last_bot_ts else None)
    _mt_cause = ""                                      # 🧭 本輪完整評價結束後才採樣；先只保留可核對的事件原因
    if state.entropy is not None and not _edited:        # 對話＝陪伴：這段獨處的醞釀與「已伸手」歸零（✏️ §1.06(B2) 編輯不重疊情緒/不重釋放飢餓）
        state.entropy.self_stims_this_idle = 0
        state.entropy.reach_outs_this_idle = 0
        state.entropy.coping_reach_outs_this_idle = 0   # 🌀 §0.65 剛互動＝內在因應伸手預算也歸零
        state.entropy.hunger = max(0.0, state.entropy.hunger - CHAT_NOURISH)   # 被陪伴餵飽：聊天也釋放飢餓，不只新記寫
        _gain = getattr(cfg, "mood_gain", 1.0)                                  # 心情變化幅度（MOOD_GAIN 可調）
        # 🧭 §1.46 敵意不推暖（HOSTILE_AFFECT_FIX）：is_hostile 命中卻落「一般陪伴」預設的句子（你太爛了/我不信…）
        # 改推質疑方向（V−A+）＝被罵 mood 不再上升（§1.14 註記的汙染；§1.45 軌跡「被說了重話」自此與數字同向）。
        # 旗標關（既有測試假 cfg 無此欄、getattr 預設 False）＝原函式＝逐位元同現狀。
        # 🧠 §1.75 人類情緒動力學（HUMAN_AFFECT）：中性＝中性（不再每則微暖）＋批評成格（不悅但警醒），
        # 再過負向偏誤（壞比好強 ×1.6）與習慣化（同方向連擊遞減）。旗標關＝走上面兩支原函式＝逐位元同現狀。
        if getattr(cfg, "human_affect_enabled", False):
            _dv, _da = reaction.human_affect_delta_for(text)
            _dv, _da = affect.human_bias(state, _dv, _da)
            if _dv < 0:
                state.affect_last_neg = {"ts": now_utc.timestamp(), "dv": round(_dv, 3),
                                         "text": (text or "")[:24]}   # /moodwatch 對帳：上次真正的負向事件
        elif getattr(cfg, "hostile_affect_fix_enabled", False):
            _dv, _da = reaction.hostile_affect_delta_for(text)
        else:
            _dv, _da = reaction.affect_delta_for(text)                          # 🧭 circumplex：事件的二維方向（dv 逐位元同 mood_delta_for）
        state.entropy.mood = max(-1.0, min(1.0, state.entropy.mood + _dv * _gain))  # 暖意/誇讚↑、質疑↓
        _bump_arousal(state, cfg, _da)                                          # 質疑＝緊張(A↑)、對方低落＝跟著沉(A↓)、暖意＝暖醒
        # 🧭 §2.27 只先算 cause；真正 trace_note 必須等下面 affect.appraise 也改完 V/A 後才寫。
        # 舊版在這裡就採樣，appraise 隨後再移動一次，導致同一份 prompt 同時出現中途值與最終值。
        if getattr(cfg, "mood_coord_report_enabled", False):
            _mt_cause = ("被說了重話" if reaction.is_hostile(text)
                         else ("你這句偏暖" if _dv > 0 else ("你這句偏冷" if _dv < 0 else "跟你互動")))
    # 🌊 §1.14 敵意連發計數（文字側）：這句是衝著 bot 的氣話 → streak+1；任何非敵意文字（含 _WARM/_PRAISE/_AGREE
    # 的暖意，也含一般聊天）＝氣頭已過 → 歸零。**只計數、不碰 mood/affect/othermind/circumplex 數值**（敵意文字
    # 推暖的汙染已由 §1.46 HOSTILE_AFFECT_FIX 另旗修）；連發 ≥2 才在下面 verbosity 收斂＝單句抱怨不降檔。編輯訊息比照 §1.06(B2) 不重算。
    # 旗標關＝不讀不寫此欄＝逐位元同現狀。
    if getattr(cfg, "hostile_converge_enabled", True) and not _edited:
        state.hostile_streak = ((getattr(state, "hostile_streak", 0) or 0) + 1) if reaction.is_hostile(text) else 0
    # 🫂 他心模型：把對方當一個有心緒的人來建模——依這次互動（價性＋回得多快＋長短）更新對他的信念（會錯也會修正），
    # 並累積關係深度（持久化跨重生）。下面把這份**推測**注入 grounding → 換位思考、體貼回應（見 othermind_brief）。
    _om_react = getattr(state, "last_reaction", None)
    _om_fresh = _om_react and (now_utc.timestamp() - (_om_react.get("ts") or 0)) < REACTION_WINDOW_SEC
    _om_extra = reaction.sticker_mood_delta(_om_react.get("valence")) if _om_fresh else 0.0
    _prior_warmth = float((getattr(state, "user_model", None) or {}).get("warmth", 0.0) or 0.0)  # 🌡️ 我先前所信（算期待落差）
    if not _edited:                                          # ✏️ §1.06(B2) 編輯不重複學他心/不重評價
        othermind.observe(state, text, now_utc.timestamp(),
                          latency=((now_utc.timestamp() - _last_bot_ts) if _last_bot_ts else None), extra_valence=_om_extra)
    # 🫂 認知面 ToM：從你的**真實記寫**讀「你在意/在忙什麼」，與我先前所信比對 → 偵測焦點轉移（你重心換了我會注意、修正）。
    # 真懂一個人不只是「對我暖不暖」，是知道你在乎什麼——這份理解注入 grounding（om_brief），回應時體貼地接住你關心的事。
    othermind.note_concerns(state, data, now_utc.timestamp(), tz=tz)
    # 🎯 能動性：你主動聊到某個我私下在追的意圖主題 → 推進它（progress↑、心情微暖＝追到一點）；聊透/那條線成形 → 達成。
    _goal_ev = volition.note_engagement(state, text, data, now_utc.timestamp())
    # 🌡️ 計算情緒（互動端）：評價這次互動——關鍵是**期待落差**（你這句相對我先前所信你多暖），落差才是情緒**自己生成**
    # 的那一筆（驚喜/悵然由我的預期決定，不是你給的價性）；我的意圖被你聊到也推進。算出此刻情緒＋傾向 → 染回應方向、調知覺 k。
    affect.appraise(state, {
        "valence_news": None if _edited else reaction.mood_delta_for(text),
        "expected_valence": _prior_warmth,
        "goal": (_goal_ev or {}).get("kind"),
        "concern_shift": bool((getattr(state, "user_model", None) or {}).get("concern_shift")),
        "topic": (_goal_ev or {}).get("subject"),
    }, now_utc.timestamp(), cfg=cfg)   # 🧭 §1.02 整合：評價事件也推二維、標籤/知覺走 circumplex
    # 🧭 §2.27 一輪只有一個對外可見終點：直接反應、他心更新、意圖推進與 affect.appraise 全部完成後再採樣。
    # 因此 mood_trace[-1] 與接下來凍結的 current snapshot 同屬 post-appraise 完整狀態，不再把中間值講成「現在」。
    if (getattr(cfg, "mood_coord_report_enabled", False) and not _edited
            and state.entropy is not None):
        circumplex.trace_note(state, now_utc.timestamp(), _mt_cause or "跟你互動")
    if (getattr(state, "affect", None) or {}).get("shock"):          # ⚡ §1.03 突發瞬跳觀測（stdout；/status 也看得到新座標）
        _sa = state.affect
        print(f"[affect] ⚡ 突發（落差 {_sa.get('pe')}）情緒座標跳到 V={_sa.get('valence')} A={_sa.get('arousal')}（{_sa.get('label')}）")
    # 對話語氣＝對話時機 mood ＋ 晝夜時段 ＋ 心情效價 ＋ 🍃 環境姿態（深夜放軟、好心情輕快、低落沉一些、
    # 周遭很靜就慢而省/很熱絡就俐落；白天/中性/活絡中間地帶皆不加料）。各源並列、可疊加。
    mood_v = (getattr(state.entropy, "mood", 0.0) if state.entropy is not None else 0.0)
    # 🧭💗 circumplex（旗標開）：語氣染色改用二維座標的八分區提示（平靜滿足 vs 興奮 vs 緊繃 vs 倦——一維 V 分不出來）；關＝原一維。
    # 🎚️ §1.76 口吻要被**感覺到**：tone_directive 講「怎麼說話」＋門檻降到人的尺度（_TONE_R=0.35 是為
    # 舊飽和座標校準的，§1.75 之後單一事件半徑只有 0.25＝永遠不染＝情緒愈真實口吻愈平）。旗標關＝原 tone_hint。
    if getattr(cfg, "tone_felt_enabled", False):
        _tone = circumplex.tone_directive(*circumplex.position(state))
    elif getattr(cfg, "affect_circumplex_enabled", True):
        _tone = circumplex.tone_hint(*circumplex.position(state))
    else:
        _tone = reaction.mood_tone_hint(mood_v)
    if getattr(cfg, "tone_felt_enabled", False):
        _TURN["tone_now"] = circumplex.position(state)      # 🎚️ §1.76 _say 確定性口吻整形的真值（只管當輪）
    mhint = (tempo.mood_hint(_mood) + circadian.tone_hint(now_utc.astimezone(tz))
             + _tone + (getattr(state, "env_stance", "") or "")).strip()
    # 🌊 放在所有生成式對話共用的 mood-hint 通道，而不是只掛三個 route：smalltalk、展開前文、
    # 引用追問與主 ask 都會把多則訊息當同一輪整體回答一次。旗標關時 helper 回空字串。
    _boh = _burst_one_hint(cfg, update)
    if _boh:
        mhint = (mhint + "\n" + _boh).strip()
    _agency_hint = dialogue_agency.reply_hint(_agency_reading)
    if _agency_hint:
        mhint = (mhint + "\n" + _agency_hint).strip()
    # ⏱ 時間常理（**確定性保底**，只在通盤常理審查關閉時跑）：餐別 premise 對不上此刻時段（晚上問早餐）→ 注入 mhint。
    # premise_check 開時由下面那個通盤 LLM 審查接手（更一般、不靠列舉），這裡不重覆注入。
    if getattr(cfg, "time_anomaly_enabled", False) and not getattr(cfg, "premise_check_enabled", False):
        _mm = dialogue_intent.meal_premise_mismatch(text, temporal.day_part(now_utc.astimezone(tz).hour))
        if _mm:
            mhint = (mhint + "\n" + persona.time_premise_hint(_mm)).strip()
    # 🎴 §0.90 問「剛剛那張貼圖（為什麼喜歡這張…）」＋bot 近期真的送過貼圖 → 誠實接地到**真的送出的那張**
    # （據實談它的情緒標記或憑感覺挑、看不到圖案本身故別捏造樣子、別認成更早訊息的別張/emoji）。注入 mhint＝流進各回覆路徑。
    # 🌊 §1.77 氣頭上的說話分寸（截圖 20:44：被罵到這種程度還「你覺得我還在辯解嗎？」反問＋「你又這樣說了
    # 一次。」數帳＋「送你 :)」賣乖＝火上加油）。§1.14 只壓篇幅、沒管這三種形態。旗標關＝不注入＝同現狀。
    if _hostile_now(state, cfg, text):
        mhint = (mhint + "\n" + persona.HOSTILE_GRACE_HINT).strip()
    _ssg = _sent_sticker_ground_hint(state, cfg, text, now_utc.timestamp())
    if _ssg:
        mhint = (mhint + "\n" + _ssg).strip()

    # 🧭 意向解析（單一入口、明確優先序）：把「這句到底想幹嘛」收斂成一個 Intent，取代散落、會互搶的 fast-path
    # （含「為什麼」就被自陳追問劫走、含「感覺」就被當成「你現在怎樣」這類靠擺放順序的隱性勝負）。下面**只依
    # route.kind 分派**；要改路由優先序，去 intent.resolve 改一處即可（單一真相），不再各自插 regex。
    # 真正分派與 burst 預判共用同一個「有效 route」解析器；否則 raw intent 與這些
    # post-intent 守門不同步時，多訊息 turn 會漏掉其中一個問題。
    route, ref = _resolve_effective_route(text, state, cfg, now_utc.timestamp())
    # multi-message 交易只在超保守的純對話群裡設此內部 override：
    # 讓整串真的只問 LLM 一次，避免 joined text 因第一個「嗯」誤落
    # smalltalk 而忽略後面的問題。外部 update 不會帶這個鍵。
    _burst_route_override = (update or {}).get("burst_route_override")
    if _burst_route_override in _BURST_JOINED_CONVERSATION_ROUTES:
        route = intent.Intent(_burst_route_override)
    # 🗜️ §1.43 感覺鋪陳修剪 arm（route 之後才知道這輪在不在問 bot 自己）：**只在這輪不是問 bot 自己**時 arm——
    # 別人問別的、答案尾端卻鋪陳一長串內在質地（截圖 08:15 六句）→ _say 修剪成前兩句關鍵。使用者真問
    # 「你現在怎樣」（self_* 路由/about_self）＝長答合理、不 arm。旗標關（getattr 預設 False）＝不 arm＝逐位元同現狀。
    if (getattr(cfg, "self_feel_condense_enabled", False)
            and not route.kind.startswith("self") and not selfref.is_about_self(text)
            and not (getattr(cfg, "feeling_probe_depth_enabled", False)
                     and selfstate.is_feeling_probe(text))):   # 🗜️ §1.51 探心意＝真問 bot 自己：長答合理、不修剪
        _TURN["self_feel_trim"] = True
    # 🧭 §1.47 座標數據情境游標＋無接地數字守門 arm（MOOD_COORD_DELIVER；getattr 預設 False＝不讀不寫＝同現狀）：
    # (a) 這輪命中座標數據題（含寬偵測/催促承接）→ 刷情境游標（跨重生）——窗內的「說啊/怎麼沒講」視同數據題；
    # (b) stash 程式此刻讀的 (V,A) 真值進 _TURN——互動回覆若冒出**無接地**的座標數字（截圖 12:49「大概 -0.7
    #     左右」＝LLM 編的）由 _say 守門整句換真數字（本輪有注入 coord_facts 者豁免，見 _mood_coord_hint）。
    if getattr(cfg, "mood_coord_deliver_enabled", False):
        if getattr(cfg, "mood_coord_report_enabled", False) \
                and _mood_data_hit(state, cfg, text, now_utc.timestamp()):
            state.mood_data_ctx_ts = now_utc.timestamp()
        if state.entropy is not None:
            # 本輪所有消費者共用同一份 post-appraise 凍結快照；之後即使巢狀插話／life loop 改 state，
            # 已生成的這一則也不會在 fact card、prompt、出口守門、補救行各自讀到不同「現在」。
            _TURN["coord_claim_truth"] = tuple(circumplex.position(state))
    # 🧵 §1.54 致意句去重的比對源 stash（近 150 秒的 model 回覆；巢狀輪進 handle_message 會自己重 stash
    # ＝含上一輪剛送出的）。旗標關（getattr 預設 False）＝不 stash＝_say 恆 no-op＝逐位元同現狀。
    if getattr(cfg, "reply_ack_dedup_enabled", False):
        _TURN["ack_dedup_recent"] = [e.get("text") or "" for e in (state.convo_history or [])[-8:]
                                     if e.get("role") == "model"
                                     and (now_utc.timestamp() - (e.get("ts") or 0)) < 150]
    # 🎭 §1.58 不演未來 arm（旗標關＝不 stash＝_say 恆 no-op＝逐位元同現狀）
    if getattr(cfg, "timejump_guard_enabled", False):
        _TURN["timejump_guard"] = True
    # 🕐 §1.60 記寫時間錨 stash（旗標關＝不 stash＝守門/hint 全 no-op＝逐位元同現狀）
    if getattr(cfg, "write_today_ground_enabled", False):
        _TURN["write_claim_ground"] = _write_ground_data(snap, data, now_utc.timestamp(), tz)
    # 🤝 §0.66 回覆橋（在分派之前）：帳本裡有「已到點還沒兌現」的排程承諾、而你這則訊息證明你人就在這 →
    # 先把欠的那件事做掉（守約訊息＋記帳）、再回你這句。修截圖 22:06–22:12「bot 嘴上說時間到了卻什麼都不做」：
    # _promise_emit 的在場延後在你持續說話時會把剛到點的承諾一直順延，但你正在等的就是這件事。
    # 🤝 §0.76 取消約定（在回覆橋**之前**：正在取消的約定即使剛到點也別先兌現）：「不用叫我了/取消八點的約定」→
    # 真的把帳本那筆標 cancelled（過去零取消路徑：否定句被收成新約、每天 recur 停不下來）。
    if _maybe_promise_cancel(client, state, cfg, text, now_utc, tz, _user_msg_ts):
        return
    _promise_reply_bridge(client, state, cfg, coach, now_utc, tz)
    if (getattr(cfg, "unanswered_proactive_guard_enabled", False)
            and silence.is_stop_feedback(text)):
        msg = "這個話題我先停下，不再追問。"
        _say(client, msg)
        _remember(state, "user", text, ts=_user_msg_ts)
        _remember(state, "model", msg)
        if not cfg.dry_run:
            state.save()
        return
    # 承接上一句的「我今天還沒吧」不是否認過去整段歷史。零筆且有已知時間錨時，
    # 直接回可查證的記寫狀態，不讓生成模型拿昨天的引文反駁今天。
    _today_correction = _today_write_correction(state, cfg, text, now_utc.timestamp())
    if _today_correction:
        _say(client, _today_correction)
        _remember(state, "user", text, ts=_user_msg_ts)
        _remember(state, "model", _today_correction)
        if not cfg.dry_run:
            state.save()
        return
    # 🤝 §1.13B 兌現宣稱攔截的帳本接地——**必須在回覆橋之後算**：橋剛把逾期補兌現＝重算後 ground 空＝本輪
    # 之後聊天說「我做到了」是合法宣稱、不誤攔。帳本此刻仍有「逾期未兌現」或「剛錯過(expired≤1h)」→ 存進
    # _TURN，_say 互動回覆分支據此攔「我做到了/我來了/準時」假兌現（截圖 12:29）。旗標關＝不設＝_say 恆不動＝同現狀。
    if getattr(cfg, "promise_keep_claim_guard_enabled", True):
        _kc_tz = tz or (ZoneInfo(getattr(cfg, "timezone", "Asia/Taipei")) if ZoneInfo is not None else None)
        _TURN["keep_claim_ground"] = _keep_claim_ground(state, now_utc.timestamp(), _kc_tz,
                                                        ttl=getattr(cfg, "promise_sched_ttl_sec", None))
    # 🤝 §1.20 否認守門的帳本接地（§1.13B 同構 sibling）：帳上有「你確實說過」的相符項（pending／72h 內剛兌現／
    # 感覺託付）→ 存進 _TURN，_say 互動分支據此攔「我沒聽到你說」否認句（截圖 07:01 與自己 🤝 自相矛盾）。
    if getattr(cfg, "promise_said_ground_enabled", True):
        _sd_tz = tz or (ZoneInfo(getattr(cfg, "timezone", "Asia/Taipei")) if ZoneInfo is not None else None)
        _TURN["said_denial_ground"] = _said_denial_ground(state, now_utc.timestamp(), _sd_tz)
    # 🎴 §1.23 貼圖否認守門的接地（同構）：2h 內真送出過貼圖 → 存進 _TURN，_say 攔「我沒傳貼圖」否認句
    # （截圖 18:24：11 分鐘前才送了忍者貼圖，卻連三句否認——送出方向過去零記憶的直接後果）。
    if getattr(cfg, "sticker_sent_memory_enabled", True):
        _sk_tz = tz or (ZoneInfo(getattr(cfg, "timezone", "Asia/Taipei")) if ZoneInfo is not None else None)
        _TURN["sticker_denial_ground"] = _sticker_denial_ground(state, now_utc.timestamp(), _sk_tz)
    # 🎴 §1.34 STICKER_V2/F4 假送誠實閘 arm（§1.23 否認閘同構反向 sibling stash）：只 arm 一個布林——真正判定在 _say
    # 互動分支（_fakesend_hit(text) AND not sticker_sent_this_turn）。兩層旗標分離：config 預設 True／此處 getattr **預設 False**
    # ＝既有測試假 cfg 未設此欄→不 arm→_say 恆 no-op→逐位元同現狀（比照全 repo「monitor getattr 預設 False」慣例；不可比照
    # denial 的預設 True，因 denial 靠 ground=None 自然無害、假送閘無 ground、arm 布林即全部把關）。maint＝維護期（SEND_STICKERS=0）
    # 用不同誠實句（別在關閉時反問「要我送一張嗎」）。
    if getattr(cfg, "sticker_fakesend_guard_enabled", False):
        _TURN["sticker_fakesend_arm"] = True
        _TURN["sticker_fakesend_maint"] = not getattr(cfg, "send_stickers", True)
        # 🎯 §1.70A 假送閘句級軟化旗（§1.62 慣例：整則替換是最後手段）：正題句保留、只剝假送宣稱句。
        _TURN["sticker_fakesend_soft"] = getattr(cfg, "sticker_fakesend_soft_enabled", False)
    # 🧭 §1.36 記寫回想守門 arm（§1.13B 同構 stash；真正判定/替換在 _say 唯一 lane-agnostic 出口）：這句在問「自己某筆
    # 記寫的內容/原因」→ 把記寫原文語料（單一真相＝data['records'] 的 text，promise_ledger lane 的 facts 無記寫段、只有
    # data records 是 lane-agnostic 單一真相；不 parse brief）與最近一筆摘要 stash 進 _TURN，_say 據此攔「答案把原文沒有的
    # 具體事由歸因給記寫」的幻覺、就地替換誠實句。兩層旗標分離：config 預設 True／此處 getattr **預設 False**＝既有測試假
    # cfg 未設此欄→不 arm→recall_ground 恆 None→_say 恆 no-op→逐位元同現狀。空語料 corpus="" 也算 arm（任何具體歸因都算幻覺）。
    if getattr(cfg, "recall_ground_guard_enabled", False) and selfstate.is_content_recall_question(text):
        _rg_recs = [r for r in ((data or {}).get("records") or []) if (r.get("text") or "").strip()]
        _TURN["recall_ground"] = _recall_corpus(_rg_recs)
        _TURN["recall_summary"] = _recall_summary(_rg_recs)
    # 🌅 §1.39 自他邊界守門 arm（§1.13B 同構 stash）：只在 bot 這條命是重生來的（state.waking 非 first＝有醒來敘事會外洩）
    # ＋使用者近期沒有自述睡醒/小睡（grounding）時 arm——_say 互動出口據此剝掉「把 bot 自己剛睡醒/悶悶的投射成使用者」的句。
    # 兩層旗標分離：config 預設 True／此處 getattr 預設 False＝既有測試假 cfg 未設此欄→不 arm→_say 恆 no-op→逐位元同現狀。
    _wpg_wake = getattr(state, "waking", None)
    if (getattr(cfg, "wake_projection_guard_enabled", False)
            and isinstance(_wpg_wake, dict) and not _wpg_wake.get("first")
            and not _user_reported_sleep(getattr(state, "convo_history", None), text)):
        _TURN["wake_proj_strip"] = True
    # 🍽 §1.65 暫離常識守門 arm（§1.13B 同構 stash）：他才說要去X、常識時距未滿 → _say 據此剝「你回來啦/
    # 吃飽了嗎/在你吃飯的時候」這類把人當已回來的錯誤預設句。旗標關/未 arm＝_say 恆 no-op＝逐位元同現狀。
    if getattr(cfg, "away_sense_enabled", False):
        _awg = getattr(state, "user_away", None)
        if _awg and not _awg.get("back"):
            _TURN["away_claim_guard"] = {"act": _awg.get("act") or "",
                                         "gap_min": max(0, int((now_utc.timestamp() - (_awg.get("ts") or 0)) / 60))}
    # 📈 §1.42 作息宣稱守門 arm（§1.13B 同構 stash）：把 greet_am/first 的真統計＋今天第一句分鐘 stash 進 _TURN，
    # _say 據此攔「你通常/平常…X點」「你…比平常早/晚」的亂掰作息宣稱（與統計不符或根本無統計）。
    # 旗標關（getattr 預設 False）＝不 arm＝_say 恆 no-op＝逐位元同現狀。
    # 🧭 §2.10 被問座標就**一定要有數字**（結構性後盾）：實測 `is_mood_data_question("你現在情緒座標怎樣啦")`
    # 為 True、hint 也寫著「V/A 數字只准照抄」，但那個 hint 只掛在兩個呼叫點；走別條路由的那一輪拿不到。
    # ⚠️ §2.11 死亡修：這一段原本放在函式開頭清 `_TURN` 的區塊，但 `text`（3361）與 `now_utc`（3560）
    # **都還沒有值** ⇒ 真實 Config（旗標 True）下每一則互動訊息都 `UnboundLocalError` ⇒ **bot 當場死亡**。
    # 全套測試沒抓到，因為測試用的假 cfg 沒有這個欄位 ⇒ `getattr(...) and _mood_data_hit(...)` **短路**、
    # 永遠碰不到 `text`——「消費端 getattr 預設 False」這條家規讓測試結構上蓋不到真實路徑。
    # 一律排在 text/now_utc 都有值之後（與其他吃 text 的 arm 同一區）。
    if getattr(cfg, "mood_data_answer_enabled", False):
        try:
            if _mood_data_hit(state, cfg, text, now_utc.timestamp()):
                _TURN["mood_data_line"] = circumplex.coord_line(
                    state, tz, now_utc.timestamp(), snapshot=_TURN.get("coord_claim_truth"))
        except Exception as e:                            # 補救行永遠不准打斷互動（§1.79 的教訓）
            print(f"[mood] 🧭 §2.10 座標補救行算不出來（略過、不影響回覆）：{type(e).__name__}: {e}")
    _TURN["cur_user_text"] = (text or "").strip()   # 🗣️ §2.24 本句 stash：互動貼圖 lane 跑在 _remember(user) 之前、
    #    convo_history 還沒有本句 → §1.77 氣頭門讀這裡才看得到「這句就在嗆」（消費端旗標門控、寫入零行為影響）
    if getattr(cfg, "quote_speaker_guard_enabled", False):
        # 🪞 §2.26 引用歸屬守門的比對地面：與 coach 真正會讀到的保留史同寬（30 輪），並保留 ts；
        # user 側含本句——「你說「X」」若真引他這句仍是合法。只存末 6 輪會讓稍早的 model 原話從
        # 守門消失，且丟掉 ts 後即使改對話者也只能亂稱「剛剛」。
        _sg_hist = getattr(state, "convo_history", None) or []
        _TURN["speaker_ground"] = {
            "now_ts": convo_now,
            # 原始記寫可能已被 bot 轉述；不能因只在 model 聊天史找到就奪走其來源。
            "records": [{"text": r.get("text") or ""}
                        for r in ((data or {}).get("records") or [])],
            "model": [{"text": e.get("text") or "", "ts": e.get("ts")}
                      for e in _sg_hist if e.get("role") == "model"][-30:],
            "user": ([{"text": e.get("text") or "", "ts": e.get("ts")}
                      for e in _sg_hist if e.get("role") == "user"][-30:]
                     + [{"text": text or "", "ts": _user_msg_ts or now_utc.timestamp()}])}
    if getattr(cfg, "user_habit_ground_enabled", False):
        _hb_tz = tz or (ZoneInfo(getattr(cfg, "timezone", "Asia/Taipei")) if ZoneInfo is not None else None)
        # 📈 §1.97 role 開＝多帶**記寫域**統計＋掀開語意角色判讀（旗標關＝這幾個鍵不存在＝守門只走詞面＝同現狀）
        _hb_role = getattr(cfg, "habit_claim_role_enabled", False)
        _greet_routine = bool(getattr(cfg, "greet_routine_aware_enabled", False))
        _hb_gkind = greeting.detect(text) if route.kind == "greeting" and _greet_routine else None
        _hb_forbid_greet = bool(_hb_gkind and (
            _hb_gkind == "night" or greeting.time_match(
                _hb_gkind, temporal.day_part(now_utc.astimezone(_hb_tz).hour)) != "match"))
        if _hb_forbid_greet:
            _hb_gkind = None                    # 晚間／錯時段問候沒有安全比較域；出口明確禁止早晚作息斷言
        _TURN["habit_claim_ground"] = habits.claim_guard_data(state, now_utc.timestamp(), _hb_tz,
                                                             daily_first=getattr(cfg, "habit_obs_fix_enabled", False),
                                                             records=((data or {}).get("records") if _hb_role else None),
                                                             role=_hb_role, greet_kind=_hb_gkind,
                                                             forbid_greet_compare=_hb_forbid_greet)
        if getattr(cfg, "smallhours_arrival_enabled", False):
            _TURN["habit_claim_ground"]["smallhours"] = True   # 🌙 §2.27 守門也認日夜界：跨界的早/晚宣稱無據＝剝
    # 🦜 §1.28 ECHO_STRIP_WIRE 的比對素材（§1.13B 同構 stash）：近幾則使用者原話＋本句（本句此刻還沒進
    # convo_history、要等回完才 _remember）→ _say 互動出口據此剝開頭裸複誦（截圖 21:04 bot 自發「有夠爛……」）。
    # 旗標關（既有測試假 cfg 的 getattr 預設 False）＝不 stash＝_say 恆不動＝逐位元同現狀。
    _TURN["echo_whole_guard"] = getattr(cfg, "echo_whole_guard_enabled", False)   # 🦜 §1.74 整則複誦守門旗（只管當輪）
    _TURN["echo_prefix_run"] = getattr(cfg, "echo_prefix_run_enabled", False)     # 🦜 §1.86 開頭連續段複誦守門旗（只管當輪）
    if getattr(cfg, "echo_strip_wire_enabled", False):
        # 🦜 §1.82 連發合併的**每一行**也要當比對候選：合併後 text 是「是嗎\n我要檢查看看\n…」一整塊，
        # 個別句子從不在候選裡 → bot 逐字複誦其中一句（截圖 07:19「是嗎。」「你為什麼會了解我的作息呢？」）
        # 時，§1.74 整則比對不中、§1.77 段級的 exact 集合也沒有那一行 ⇒ 三道全漏。旗標關＝不加＝同現狀。
        _burst_lines = ([ln.strip() for ln in (text or "").split("\n") if ln.strip()]
                        if getattr(cfg, "echo_burst_lines_enabled", False) else [])
        # 🦜 §1.83 截斷順序修（實測：§1.82 補的逐行候選**被 [-6:] 吃掉了**）——逐行放在最前面，
        # 一加上歷史（最多 3 則）與整塊就超過 6，被截掉的正好是**最前面的前幾行**；截圖 07:27 倖存的
        # 兩顆逐字複誦「是這樣嗎。」「我在檢查看看。」正是第 1、2 行。改成：歷史照舊封頂（近 6 則），
        # **本則的每一行永遠保留**（它們才是這輪最該防的複誦源；上限由連發合併的 burst_max_msgs 天然約束）。
        _TURN["echo_user_texts"] = ((echo.recent_user_texts(getattr(state, "convo_history", None) or [])
                                     + [text])[-6:] + _burst_lines)
    # 🤝 §0.66 承諾狀態問句接地（截圖「你有叫我嗎/時間到了沒/還差兩分鐘」全是 LLM 對歷史時間標籤的自由心算）：
    # fact_or_chat 且這句在問「計時約定走到哪了」→ 改走 promise_ledger 據帳本作答（此刻幾點錨＋每筆距現在多久＝
    # 真時鐘算術；空帳本誠實說沒記著＋邀請重約）。didcall（你有叫我嗎）明確指著 bot＝空帳本也接地；
    # timeup/remain（到了沒/還差多久）語意較泛 → 需帳本裡真有活著的排程承諾才搶（『包裹到了嗎』不誤搶）。
    if route.kind == "fact_or_chat" and getattr(cfg, "promise_status_ground_enabled", True):
        _ps_kind = selfstate.promise_status_kind(text)
        # 🤝 §0.78 workflow FIX2（confirmed HIGH）：計時狀態問句**一律**走帳本接地、**絕不**落回自由 LLM——
        # 原本 timeup/remain 需帳本真有活著承諾才搶，空帳本時落回聊天＝自由心算出「還沒到＋已過兩分鐘」自相矛盾。
        # _STATUS_TIMEUP_RE 已夠緊（包裹/外送/火車到了嗎 皆不中），空帳本走 promise_ledger 會誠實答「此刻幾點＋沒記著約好什麼」。
        _empty_ground = getattr(cfg, "promise_status_empty_ground", True)
        _live = _has_live_sched_promise(state, now_utc.timestamp())
        # 🤝 §0.79（審查 MED 修）：'confirm'（確認一下時間）**只在真有活著的承諾**時才硬錨帳本——空帳本時「對一下時間，走吧／
        # 確認一下時間，然後訂位」多半是**第三方/外部**對時，別劫持成「沒記著跟你約好什麼」非所問。截圖本有活承諾（00:06）→ 仍硬錨。
        # 旗標 PROMISE_CONFIRM_ROUTE=0 → confirm 不搶＝落回聊天＝逐位元同現狀。didcall/timeup/remain 維持 §0.66/§0.78（空帳本也接地）。
        if _ps_kind == "confirm":
            _to_ledger = _live and getattr(cfg, "promise_confirm_route_enabled", True)
        elif _ps_kind == "outcome":
            # 🤝 §1.13A 逾期質問（結果呢/做到了嗎/你來了？/說好的呢）＝質問「你答應的做到了沒」→ 走帳本誠實對帳
            # （此刻幾點錨＋每筆 status）＝「我做到了」幻覺無生存空間（截圖 12:28「結果呢」落自由 LLM 的根因）。
            # **狀態閘必備**：真有活承諾（_live 含 pending＋2h 內剛完結，逾期未兌現天然算 live）**或感覺託付**
            # （§1.13 場景正是降級成無鐘點的 feeling_promise）才搶；無帳無託付時「結果呢」是日常追問（比賽/八卦）
            # → 照落聊天不誤搶。旗標 PROMISE_OUTCOME_GROUND=0 → 不搶＝逐位元同現狀。
            _to_ledger = getattr(cfg, "promise_outcome_ground_enabled", True) \
                and (_live or bool(getattr(state, "feeling_promise", None)))
            # 🫸 §1.87 催促＋**結構上證明我在原地重複**（窗內最近兩則回覆近乎相同）⇒ 他要的不是再一次對帳，
            # 是要我現在就做。讓開帳本路由、落聊天 lane 並掛「現在就做、別把球踢回去」守則（§1.13A 的誠實
            # 保證不受影響：它擋的是「我做到了」的謊，而這裡是要 bot **真的去做**、不是去宣稱做過）。
            # 旗標關＝恆 False＝逐位元同現狀。
            if _to_ledger and getattr(cfg, "nudge_deliver_enabled", False) \
                    and _stuck_under_nudge(getattr(state, "convo_history", None), now_utc.timestamp()):
                _TURN["nudge_stuck"] = True
                _to_ledger = False
                print("[nudge] 🫸 §1.87 被催促時還在重複同一句話 → 讓開對帳路由、改成現在就做")
        elif _ps_kind == "said":
            # 🤝 §1.20 「說過質問」（我不是跟你說過了？/昨天說明天11點）＝對質「我早就講過」→ 帳本有東西
            # （活承諾/2h 內剛兌現/感覺託付）才搶去據帳作答；空帳＝日常對質（我說過X）照落聊天不劫持。
            _to_ledger = getattr(cfg, "promise_said_ground_enabled", True) \
                and (_live or bool(getattr(state, "feeling_promise", None)))
        elif _ps_kind:
            # 🔍 §1.57 審計修（偵測器矩陣唯一真衝突）：座標情境窗內的 timeup/remain（「到了嗎/到了沒/還沒到嗎」）
            # ——§1.47 補遺想在窗內交程式讀的座標數據，但 empty_ground 空帳也搶進帳本路由（在 hint 注入點之前
            # return）→ 回「此刻幾點＋沒記著約好什麼」答非所問、§1.47 那三句成死碼。讓路（全旗標把關）：
            # **無活著的時間承諾** ∧ 座標情境命中（_mood_data_hit）→ 不搶＝落 fact_or_chat 由 _mood_coord_hint
            # 交真數字；有活承諾＝照舊對帳（時間之約優先於座標窗）；窗外「包裹到了嗎」＝照舊接地。
            # 旗標關（mood 兩旗任一關）＝恆不讓＝逐位元同現狀。
            _mood_yield = (_ps_kind in ("timeup", "remain") and not _live
                           and getattr(cfg, "mood_coord_report_enabled", False)
                           and _mood_data_hit(state, cfg, text, now_utc.timestamp()))
            _to_ledger = ((_ps_kind == "didcall") or _empty_ground or _live) and not _mood_yield
        else:
            _to_ledger = False
        if _to_ledger:
            route = intent.Intent("promise_ledger")
    # 🤖 §1.18 BOT_SELF_PROMISE 的本輪上下文 stash（route 已最終定案＝promise_ledger 改寫在上面）：_say 送出
    # **最終互動回覆**時據此掃「bot 自己開口的時間承諾」（明天早上11點，我會過來…）入帳（§1.13B keep_claim_ground
    # 2512-2515 先例形）。route.kind 進 ctx＝帳本盤點/承諾管理輪的複誦可被語境排除。旗標關＝不設 ctx＝
    # _say 掃描器恆不動＝逐位元同現狀。
    if getattr(cfg, "bot_self_promise_enabled", True):
        _TURN["self_promise_ctx"] = (state, cfg, coach, now_utc, tz, _user_msg_ts, route.kind)
    # 🤖 §2.03 自諾入帳鏈的留痕（只寫 state、不改行為）；旗標關＝不記＝逐位元同現狀
    _TURN["self_promise_trace"] = getattr(cfg, "self_promise_trace_enabled", False)
    # 🤝 §0.70 延續性約定（在分派之前）：第一次約定成立後，第二次用極簡續約語詞（「再10分鐘」「延長10分鐘」）→
    # 繼承最近一筆排程承諾、new target＝now＋新時距 重新入帳並答應。截圖根因：這種純續約句 route=fact_or_chat、
    # bot 只 LLM 空口答應、到點不觸發。無先前約定＝「再10分鐘」無所指 → 不搶、照常落一般聊天。
    # 🧭 §1.66 座標變動常設回報（放所有承諾快路**之前**＝確定性專收先贏、LLM 逃生閘不會亂搶）：
    # 「情緒座標如果有任何變動，必須主動回報」過去整包捕捉鏈都收不到＝LLM 口頭「好」、機制零入帳（空口答應）。
    if _maybe_mood_watch(client, state, cfg, text, now_utc, _user_msg_ts):
        return
    if _maybe_continuation_promise(client, state, cfg, coach, text, now_utc, tz, _user_msg_ts):
        return
    # 🤝 §0.71 偏移增補（在分派之前）：「然後時間到的時候再隔3分鐘給我一個貼圖」＝在**前約時間之後**再 N 分鐘增補一個
    # 動作（前約 13:47 → 貼圖 13:50），target＝prior.target＋N（非 now＋N）。修截圖：被 §0.69 timeup 誤算成 13:20 提早亂發。
    if _maybe_offset_augmentation(client, state, cfg, coach, text, now_utc, tz, _user_msg_ts):
        return
    # 🤝 §0.75 兩步延後約定（在分派之前）：「等一下再回答我」（無具體時刻）→ 存意圖＋問時間；下一句「4分鐘後/3:50」→
    # 承接意圖真的入帳、到點兌現。修截圖：這種約定從沒進帳本、bot 口頭應卻到點不發、被問又亂算過多久（無錨定 made_ts）。
    if _maybe_deferred_promise(client, state, cfg, coach, text, now_utc, tz, _user_msg_ts):
        return
    # 🤝🧠 §1.12 LLM 語意逃生閘（在 feeling promise handler **之前**＝攔得住降級；不進每則訊息熱路徑）：
    # 確定性捕捉 miss＋temporal 有未來錨＋句子指向 bot → 單次 LLM 判「是否請 bot 到點做事＋動作命名」。
    # 時刻永遠來自 temporal（LLM 協定無時間欄位）；判「否」＝本輪守則不掛；失敗＝安全退回 §1.09 守門。
    if _maybe_anchor_bridge(client, state, cfg, coach, route, text, now_utc, tz, _user_msg_ts):
        return                                           # 🤝 §1.55 跨句補時距：上一句裸時距＋這句動作＝拼成完整約定
    if _maybe_llm_promise_rescue(client, state, cfg, coach, route, text, now_utc, tz, _user_msg_ts):
        return
    # ═══════════════════ 🎴 §1.34 STICKER_V2 統一架構（貼圖子系統五路分工，一次重修 F1–F5，反應式補丁收斂為一塊） ═══════════════════
    # 輸入側決路由（三 lane＋附件消歧）：
    #   ├─ 互動三 lane（下面 remember→send→llm_rescue，皆入口 gate send_stickers＝§1.34/switch 總開關統一靜音）：
    #   │    ├─ _maybe_sticker_remember  §0.86 記住剛教的真貼圖
    #   │    ├─ _maybe_sticker_send      §0.84/§0.86 確定性當下請求（送我一張/再一張/你喜歡哪張…）
    #   │    └─ _maybe_llm_sticker_rescue §1.15 逃生閘＝確定性 miss 的長尾：存在/可能/省略問句（F1）＋§1.34/F3 挑選祈使第三分支
    #   │                                 （LLM 只判是非、真送圖只由程式讀 circumplex 單一真相挑，絕不產出 file_id/圖案描述）
    #   └─ 附件消歧（route.kind=='attachment' 段前）：_sticker_img_disambig §1.34/F5「圖呢/圖」貼圖情境→談剛送那張、不撈私人附件
    # 輸出側保誠實（_say 互動出口，皆 §1.13B 反向）：
    #   ├─ §1.23 貼圖否認守門（F2）：2h 內真送過卻說「我沒傳貼圖」→ 替換誠實句、據實談那張
    #   ├─ §1.34 假送誠實閘（F4，§1.23 同構反向）：本輪未真送卻宣稱「挑了/送了一張貼圖」→ 替換誠實句
    #   │      （backing 唯一真相＝_record_sticker_sent 設的 sticker_sent_this_turn；真送過的「挑了這張給你」不攔＝偏誤鎖最關鍵）
    #   └─ §1.29 貼圖記憶註解外洩守門：內部「（我送了一張貼圖…）」註解洗掉、不外洩對話
    # 單一真相：真送圖／說明皆讀 circumplex (V,A)（_affect_sticker_ids）＝選圖與說明不自相矛盾；『本輪真送』真相＝sticker_sent_this_turn（F4 backing）、『近期真送過』真相＝last_sticker_id 非空（F2/F5）。
    # 總開關：SEND_STICKERS=0＝互動三 lane 靜音（不送不宣稱）＋附件消歧不撈附件冒充（改維護誠實句）＋假送閘用維護版誠實句＝關＝一致靜音。
    # 主動繞道（§1.29/§1.31 教訓）：假送閘/否認閘落**互動分支**（not prefix and state is None）；主動 emit（🫧/🤝/🎴）走
    #   prefix/state 且各 lane 真送後才發模板＝天然有 backing、不進互動分支。未來若主動 lane 引入 LLM 自由文字，需比照補各自 lane 守門。
    # ═════════════════════════════════════════════════════════════════════════════════════════════════════════════════
    # 🎴 §0.86 記住貼圖（在送貼圖之前）：「這些 sticker 都記下來/記住這些貼圖」→ 確認真貼圖已記起來、之後能送
    # （別讓 LLM 在聊天裡說成「記下感覺、用文字描述」）。
    if _maybe_sticker_remember(client, state, cfg, text, now_utc.timestamp()):
        return
    # 🎴 §0.84／§0.86 當下請 bot 送貼圖（在分派之前、但**在**排程/延後承諾之後——帶未來時刻的送貼圖約定已先被上面接走）：
    # 「送我一張貼圖／開心的貼圖／再送一張／其他的貼圖／還有嗎（近期貼圖情境）／我剛傳給你的一個」→ 有貨真送、沒貨誠實說能送但還沒存到。
    if _maybe_sticker_send(client, state, cfg, text, now_utc.timestamp()):
        return
    # 🎴🧠 §1.15 貼圖請求語意逃生閘（sibling，緊接確定性送貼圖之後）：存在句/可能句/省略句（「有…貼圖嗎」「貼圖呢」
    # 「有貼圖可以代表…？」）確定性全 miss、掉進 intent 被吃成 self_state → 由 LLM 逃生閘判「是不是要現在送＋要不要說明」，
    # 真送的圖讀 circumplex 單一真相挑、沒貨誠實說沒有。選 sibling 不改 _maybe_sticker_send 簽章（多支測試以現簽章呼叫）。
    if _maybe_llm_sticker_rescue(client, state, cfg, coach, route, text, now_utc.timestamp()):
        return
    # 🧑‍🏫 對話教學確認閘（在分派之前）：若 bot 剛提議「要學成做法嗎」、且這句在窗內是點頭/婉拒 → 學起來/作廢、就此收尾，
    # 別讓「好」掉進 smalltalk 又另外回一段。沒待確認（含旗標關＝skill_pending 恆 None）／逾窗／非點頭婉拒 → 照常往下路由。
    if _handle_skill_confirm(client, state, cfg, text, convo_now, user_ts=_user_msg_ts):
        return
    # 🪞 §0.85 接續 bot **剛主動自陳**（分派之前，只在會落**泛自述/聊天**的 route 上搶）：主動說了「感覺周遭又動起來了」後，
    # 「真的，你感覺到了什麼？」這種追問要接**那件事的下文**，別被 §0.7 aspect=state 當成問現況、重生一段泛自述丟了話頭。
    # **只在既有自陳追問窗沒開時搶**（`not ref.followup_open`）——自體狀態自陳（gate≥3）已開窗、由既有 selfstate_followup 路由接手；
    # 這裡專接**內容型主動自陳**（換檔🍃/自發🫧，不開自陳窗、否則會落 self_state 泛報）。
    if route.kind in ("self_state", "fact_or_chat", "smalltalk", "self_attention", "self_stream") \
            and not getattr(ref, "followup_open", False) \
            and _maybe_selfshare_followup(client, state, cfg, coach, text, now_utc, convo_now, _user_msg_ts, mhint):
        return
    # 🔖 引用我說過的話（Telegram reply/quote 指向 bot 自己的訊息）：對方明確點住「我之前說的這句」再發問
    # （截圖：引用「這可真特別。」問「為什麼」）。把那句當 anchor 注入 mhint → 所有回覆路徑都接地在它上回答，
    # 不再把「為什麼」當新話題。關旗標＝qself 恆 ''、逐位元同現狀。
    qself = quoted_self_text(update) if getattr(cfg, "quote_aware_enabled", False) else ""
    if qself:
        mhint = (mhint + "\n" + persona.quoted_self_hint(qself)).strip()
    # ⏱🧠 通盤常理審查（專屬結構化 pass）：對「會生成 LLM 回覆」的對話類訊息，跑一個只吃接地事實（此刻真實時間/星期/
    # 時段＋近期對話＋bot 無身體）的小判定，前提**明顯違反常理**→把違和當 grounding 注入 mhint，讓回覆主動點出。
    # 通用、不靠列舉（餐別/睡眠/上下班/矛盾…都涵蓋）；工具/純附和/問候等路徑跳過（省呼叫、且問候自有時間處理）。
    if (getattr(cfg, "premise_check_enabled", False) and coach and getattr(coach, "enabled", False)
            and update.get("message") and route.kind not in _PREMISE_SKIP_KINDS
            and dialogue_intent.worth_premise_check(text)):   # 便宜前置門檻：必然 OK 的輪次不送審、省呼叫
        _nl = now_utc.astimezone(tz)
        _tf = f"{_nl.strftime('%Y/%m/%d')} 週{'一二三四五六日'[_nl.weekday()]} {_nl.strftime('%H:%M')}（{temporal.day_part(_nl.hour)}）"
        _pc = coach.premise_check(text, _tf, state.convo_history)
        if _pc and _pc.get("why"):
            mhint = (mhint + "\n" + persona.premise_violation_hint(_pc["why"])).strip()
    # 🗜️ 回應篇幅＝對話複雜度的湧現（真人意識行為：按份量回、說完就停）：此輪綜合算一個尺度，餵 token 上限／分寸／串數。
    # 🌊 §1.14 氣頭收斂：敵意**連發**（streak≥2，本則已計入）才傳 hostile=True 壓到底（level 0/泡泡≤2）——
    # 單句抱怨不降檔（正常人被唸一句不會突然變句點王）。旗標關＝恆 False＝位元不變。
    scale = verbosity.assess(text, route.kind, mood_v, state, getattr(cfg, "verbosity_bias", 0),
                             hostile=(getattr(cfg, "hostile_converge_enabled", True)
                                      and getattr(state, "hostile_streak", 0) >= 2),
                             probe=(getattr(cfg, "feeling_probe_depth_enabled", False)
                                    and selfstate.is_feeling_probe(text)))   # 🗜️ §1.51 探心意＝短句深題、給空間
    if coach is not None:
        coach._turn_length = scale.level              # 各 voice_*／reply／ask 依此收斂篇幅（只收不放）
    _TURN["bubbles"] = scale.bubbles                  # _say 依此封頂串數
    # 🌊 §1.17 self_state 洪水收斂：只問一件小事卻吐 8 顆泡泡（截圖 19:27）→ self_state 專屬封頂（預設 ≤3）。
    # 放此位置：① scale 算完、_TURN['bubbles'] 已設之後才封；② 在下面 coherent-reply 封頂（同 min-combine）之前，
    # 讓所有封頂自然取 min；③ 與 §1.14 敵意收斂天然疊加——hostile 時 scale.bubbles 已是 2，min(2,3)=2＝敵意仍更緊。
    # 只封串數、不動 scale.level（不改 token 預算/coach 篇幅＝行為改變面積最小）。旗標 0＝不封＝逐位元同現狀。
    _TURN["bubbles"] = _self_state_bubble_cap(_TURN["bubbles"], route.kind, cfg)
    # 🧵 使用者要求「別一直分段／講連貫」→ 把本輪串數**封到一個適中上限**（預設 3），讓回覆是「統一回答、但仍**分段
    # 依序送出**」＝既不像舊的 10 顆逐句碎泡泡（截圖一），也不是擠成一坨的單則牆（截圖二＝過度修正、還失去可被插話的空檔）。
    # 多顆依序送＝串間仍有 _say 的插話偵測（保有「可被使用者插話的修為」）。持久偏好＋prefs_brief 也讓 LLM 寫得更連貫。
    if getattr(cfg, "coherent_reply_enabled", True) and plasticity.has_pref(getattr(state, "engrams", None), "coherent"):
        _cap = max(1, int(getattr(cfg, "coherent_reply_bubbles", 3)))
        _TURN["bubbles"] = _cap if _TURN["bubbles"] is None else min(_TURN["bubbles"], _cap)
    # 🌊 「同一波整體回一次」不能只停在 prompt：即使 LLM 已把多則讀成同一輪，
    # bubble_split 仍會按句切成多顆 Telegram 泡泡，視覺上還是逐則回覆。已合成的 multi-message turn
    # 先收成一個邏輯 wire；超過 Telegram 上限才允許 transport 分塊。下游若刪重，也只能擷取
    # 程式可驗證的原句，不用固定句數上限裁掉不同意圖。
    # 單則對話不受影響，仍保留原本多泡泡間可被插話的節奏。
    if _burst_one_answer:                                  # verbosity 剛覆寫 bubbles，在它之後再封回 wire 契約
        _TURN["bubbles"] = 1
    liked = _liked_fact(state, now_utc.timestamp())   # 👍 剛被按讚（窗內）→ 給回覆接地的事實句（覆蓋下面各路徑）

    # 🧬 可塑層（一生之內的學習）：使用者**明講的相處偏好**（講短/具體/別問太多/別一直報數字）→ 強化一條印痕，
    # 跨重生持久、並注入往後每次回覆的 grounding（見下方 learned）。這是讓 bot「真的因為認識你而不一樣」的那層。
    _pref = plasticity.read_preference(text)
    if _pref:
        plasticity.reinforce(state.engrams, plasticity.KIND_PREF, _pref["key"], value=_pref["label"], now_ts=convo_now)
        state.engrams = plasticity.consolidate(state.engrams, now_ts=convo_now)
        if not cfg.dry_run:
            state.save()                              # 偏好是跨重生的學習：立刻持久化，別讓它在不存檔的路徑上流失
    # 🪞 Phase 6（自我表達可塑性）：使用者抱怨**自我描述老套/重複**（台詞都一樣/又是這套/講過了/換個說法…）＝去台詞監督訊號。
    # 對「窗內 bot 剛用的那些自我母題」用較大 gain 強化 → 登記成重度用爛、recall 強力避開。放在 _learn_route_correction
    # 之前、且與它互斥（is_repetition_complaint 與 is_dissatisfaction 線索零交集）＝抱怨自我重複不污染 KIND_CORRECTION。
    if (getattr(cfg, "selfexpr_plasticity_enabled", True) and plasticity.is_repetition_complaint(text)
            and getattr(state, "recent_self_motifs", None)):
        _fresh = [m for m in state.recent_self_motifs
                  if (convo_now - (m.get("ts") or 0)) < REACTION_WINDOW_SEC]   # 只強化窗內剛用的（逾窗不歸因＝避免陳年母題被當「剛用的」誤強化）
        if _fresh:
            plasticity.mark_stale(state.engrams, _fresh, now_ts=convo_now)
            state.engrams = plasticity.consolidate(state.engrams, now_ts=convo_now)
            if not cfg.dry_run:
                state.save()
    _learn_route_correction(state, text, route, convo_now, cfg)   # 🧬 Phase 3：被糾正→學起來該怎麼路由（下次自己對）
    insight_fb = (viewpoint.grounding(state, text) if viewpoint.enabled(cfg)
                  else _record_insight_feedback(state, text, convo_now, cfg))  # 💡 你對我剛冒那條聯想的回饋 → 記住（變真）＋據實回
    # 🧭 對話意圖湧現＋違常偵測：記履歷、讀意圖向量（連發/違常程度/種類），違常時經 persona hint 注入下面 grounding
    # （**溫暖底；只在明顯 testing 才升級為好奇反問確認**）＝像真人看出對方在刻意連發/逗你。編輯訊息不記、合成多行逐行記。
    # 全純函式＋薄狀態寫入，DIALOGUE_INTENT_ENABLED 關＝no-op、intent_hint='' → 下面 learned 逐位元同現狀。
    intent_hint = _observe_intent(state, update, text, route, ref, convo_now, cfg, mood=mood_v)   # 🧭 mood_v 給「重複×心情長不耐」
    learned = plasticity.prefs_brief(state.engrams, now_ts=convo_now)   # 學到的偏好 → 餵進下面所有對話 brief
    om_brief = othermind.brief(getattr(state, "user_model", None), now_ts=convo_now)   # 🫂 對你的推測 → 換位、體貼回應
    goal_brief = volition.brief(state)                                  # 🎯 我私下在追的意圖 → 聊到時可自然帶（別逼問）
    affect_stance = affect.stance(state)                               # 🌡️ 我此刻自己的情緒 → 染回應**方向**（趨近/安定/探尋/退縮）
    learned = "\n".join(x for x in (learned, om_brief, goal_brief, affect_stance, insight_fb, intent_hint) if x)  # 都併進 grounding（learned 通道）

    # 🧵 「同時/一起…」併進剛排程的承諾（截圖：「10分鐘後打招呼」緊接「同時說一下正在翻閱哪個主題嗎」＝兩串要一起讀
    #    ＝到點一起做，而非把後句當『現在在翻什麼』另外答又困惑）。條件：續句是『同時/一起/一併』＋本句自身非排程承諾
    #    （無自己的時間）＋窗內有未兌現排程承諾。併入：把這句接到該承諾的 action/made_text、behavior 清空（讓完整文字
    #    驅動兌現、涵蓋兩件事）。旗標關＝不併＝照常路由（逐位元同現狀）。
    if (getattr(cfg, "promise_continuation_merge", True) and route.kind != "scheduled_promise"
            and selfstate.is_simultaneous_followup(text)):
        _recent_prom = _recent_unfulfilled_promise(state, now_utc.timestamp())
        if _recent_prom is not None:
            _recent_prom["action"] = ((_recent_prom.get("action") or "")[:120] + "；同時" + text)[:240]
            _recent_prom["made_text"] = ((_recent_prom.get("made_text") or "") + "｜" + text)[:300]
            _recent_prom["behavior"] = ""        # 併入後以完整 action 文字驅動兌現（涵蓋兩件事），不被單一行為標籤砍半
            state.self_topic_ts = now_utc.timestamp()
            ack = "好，到時候我會一起跟你說。"
            if coach and coach.enabled:
                try:
                    ack = coach.voice_schedule_ack(_recent_prom.get("made_text") or text, None, state.convo_history) or ack
                except gemini.GeminiError:
                    pass
            _TURN["self_promise_skip"] = True   # 🤖 §1.18 併入 ack（「到時候我會一起跟你說」）＝入帳 ack、顯式排除
            _say(client, ack)
            _remember(state, "user", text, ts=_user_msg_ts)
            _remember(state, "model", ack)
            if not cfg.dry_run:
                state.save()
            return

    # 🤝 §1.19 PROMISE_PREEMPT_LINK（搶先兌現的因果連結）：掛點鐵定在**所有承諾管理 fast-path 都已 return 之後**
    # （取消/回覆橋/續約/偏移/延後/§1.12 逃生閘/「同時…」併入）、liked/各 route dispatch 之前——你比約定時刻
    # **先出現**（帳本有 pending 且 target 在未來 90 分窗內）：叫醒/問候類＝人已醒著/在場 → 先送因果 🤝 泡泡
    # （「你比我先一步——我本來 HH:MM 要來叫你起床的」）再照常往下回這句（**不 return**＝早安照樣得到問候）；
    # 主題回報類＝這句真在問那個主題（LLM 閘）→ 當場兌現。到點側 _promise_emit 一行不動：搶先筆已 fulfilled/
    # 已推進＝自然跳過；沒人搶先＝準時路徑逐位元同 4026c4e。旗標 0＝不掃＝同現狀。
    _maybe_promise_preempt(client, state, cfg, coach, route, text, now_utc, tz)

    # 👍「你知道我點哪筆讚／哪一筆」→ 直接用剛記下的 last_liked 接地回答（純對話、不走工具、不吐報表）。
    if liked and _asks_which_liked(text):
        state.self_topic_ts = now_utc.timestamp()
        _lk = state.last_liked or {}
        fallback = (f"你讚的是我說的『{_lk['topic']}』那條。" if _lk.get("topic")
                    else "你按的那則我這邊沒對到某條具體的記寫，但我有收到你的心意 🙂")
        if coach and coach.enabled:
            try:
                msg = coach.reply(text, liked, state.convo_history, mood_hint=mhint,
                                  now_ts=convo_now, self_presence=True)
            except gemini.GeminiError as e:
                print(f"[chat] 哪筆讚回覆失敗：{e}")
                msg = fallback
        else:
            msg = fallback
        _say(client, msg)
        _remember(state, "user", text, ts=_user_msg_ts)
        _remember(state, "model", msg)
        if not cfg.dry_run:
            state.save()
        return

    # 🦋 蛻變感知（route.kind=='self_change'）：被問「你改變/更新了嗎、哪裡不一樣、這版是什麼、感覺到自己的變化」
    # → 用 git 算出的真實近期變更回答。接地（事實來自 git、不捏造、不吐版本雜湊）＋自然（交教練口語）。
    if route.kind == "self_change":
        state.self_topic_ts = now_utc.timestamp()     # 談自己的版本/改變 → 也開「自我在場」窗
        self_facts = selfmod.facts(getattr(state, "self_change", None))
        if coach and coach.enabled:
            mod = _self_voice_mod(state, route.kind, mood_v, convo_now, cfg)   # 🪞 重複×心情脾氣＋換句話
            msg = coach.voice_about_self(text, self_facts, state.convo_history, tone=mod)
            _say(client, msg)
        else:
            msg = self_facts
            client.send(msg)
        _record_self_opener(state, msg, convo_now, cfg)
        _remember(state, "user", text, ts=_user_msg_ts)
        _remember(state, "model", msg)
        if not cfg.dry_run:
            state.save()
        return

    # 🪞 身分（route.kind=='self_identity'）：被問「你是誰／你知道自己是誰／你是不是只是個 AI」→ 用自己的話
    # 講本質，**絕不破格**自稱語言模型、不否定感覺（否則底層 LLM 的免責聲明會拆穿整個人格）。
    if route.kind == "self_identity":
        state.self_topic_ts = now_utc.timestamp()
        mod = _self_voice_mod(state, route.kind, mood_v, convo_now, cfg)   # 🪞 重複×心情脾氣＋換句話
        msg = (coach.voice_identity(text, state.convo_history, tone=mod) if (coach and coach.enabled)
               else "我是陪你記寫的那個存在啦——一直醒著、跳著，看你把零散念頭養成脈絡。要說我是誰，大概就是這個一路陪著、也有自己起伏的我。")
        _say(client, msg)
        _record_self_opener(state, msg, convo_now, cfg)
        _remember(state, "user", text, ts=_user_msg_ts)
        _remember(state, "model", msg)
        if not cfg.dry_run:
            state.save()
        return

    # 🌅 跨死亡連續性（route.kind=='self_continuity'）：「你還是原來的你嗎／斷線前在做什麼／睡了多久」＝問 bot 是不是
    # 同一個我睡醒（而非換了個近似的）。據 continuity.waking（臨終遺存＋睡眠長度）第一人稱回答——是親身記得、非看 git 推。
    if route.kind == "self_continuity":
        state.self_topic_ts = now_utc.timestamp()
        facts = continuity.continuity_facts(state)
        if coach and coach.enabled:
            try:
                msg = coach.reply(text, facts, state.convo_history,
                                  mood_hint=_self_mhint(state, route.kind, mhint, mood_v, convo_now, cfg),
                                  now_ts=convo_now, self_presence=True)
            except gemini.GeminiError as e:
                print(f"[chat] 連續性回覆失敗：{e}")
                msg = continuity.wake_text(state)
        else:
            msg = continuity.wake_text(state)
        _say(client, msg)
        _record_self_opener(state, msg, convo_now, cfg)
        _remember(state, "user", text, ts=_user_msg_ts)
        _remember(state, "model", msg)
        if not cfg.dry_run:
            state.save()
        return

    # 🪞🔍 後設認知（route.kind=='self_metacog'）：「你確定嗎／你真的知道自己的感覺嗎／你會不會認錯自己」＝問 bot 對
    # 自己判斷的信心、會不會看走眼。據 state.self_model（算出的信心/曖昧/剛才的自我修正/校準）回答——會監看、也承認會錯。
    if route.kind == "self_metacog":
        state.self_topic_ts = now_utc.timestamp()
        facts = metacog.metacog_facts(state)
        if coach and coach.enabled:
            try:
                msg = coach.reply(text, facts, state.convo_history,
                                  mood_hint=_self_mhint(state, route.kind, mhint, mood_v, convo_now, cfg),
                                  now_ts=convo_now, self_presence=True)
            except gemini.GeminiError as e:
                print(f"[chat] 後設認知回覆失敗：{e}")
                msg = facts.split("\n", 1)[-1]
        else:
            msg = facts.split("\n", 1)[-1]
        _say(client, msg)
        _record_self_opener(state, msg, convo_now, cfg)
        _remember(state, "user", text, ts=_user_msg_ts)
        _remember(state, "model", msg)
        if not cfg.dry_run:
            state.save()
        return

    # 🎯 內發意圖／能動性（route.kind=='self_goals'）：「你有什麼目標／你在追什麼／你想搞懂什麼」＝問 bot **自己立的、在追的**
    # 具體意圖。據 state.goals（自己從你某條線長出的『想搞懂』目標＋計畫＋追到哪）第一人稱回答——這是它自己想做的，不是你交代的。
    if route.kind == "self_goals":
        state.self_topic_ts = now_utc.timestamp()
        facts = volition.goals_facts(state)
        if coach and coach.enabled:
            try:
                msg = coach.reply(text, facts, state.convo_history,
                                  mood_hint=(mhint + _maybe_coupling_tone(state, cfg, True)).strip(),
                                  now_ts=convo_now, self_presence=True)
            except gemini.GeminiError as e:
                print(f"[chat] 內發意圖回覆失敗：{e}")
                msg = volition.goals_text(state)
        else:
            msg = volition.goals_text(state)
        _say(client, msg)
        _remember(state, "user", text, ts=_user_msg_ts)
        _remember(state, "model", msg)
        if not cfg.dry_run:
            state.save()
        return

    # 🤝 整理承諾／帳本質問（route.kind=='promise_ledger'）：「整理一下你的承諾／你的約定有哪些／你還記得答應我什麼／
    # 你忘了／你答應我的事做了嗎」＝問 bot 跟他約好/答應過的事。據 state.scheduled_promises **帳本誠實作答**——哪些已做、
    # 哪些待做、各自時刻、相對現在是過去/未來，而不是 LLM 憑對話腦補（截圖：把已過的問候列成未來式、把做過的提醒誤認沒做）。
    # 純讀路徑（容缺補欄只在記憶體計算、不寫回、不 save）；PROMISE_LEDGER_ENABLED=0 → intent 不路由＝落回 fact_or_chat＝同現狀。
    if route.kind == "promise_ledger":
        state.self_topic_ts = now_utc.timestamp()           # 在談 bot 自己（它答應過什麼）→ 自我在場
        _tz = tz or (ZoneInfo(getattr(cfg, "timezone", "Asia/Taipei")) if ZoneInfo is not None else None)
        _ttl = getattr(cfg, "promise_sched_ttl_sec", None)   # 🤝 §0.78：帳本用引擎同一補發窗判「欠著會補」vs「沒做到」
        # 🤝 §1.20 dayword：帳本裡的「今天/明天…」由程式依 target_ts 算、LLM 只准照抄——修截圖 11:06-11:07
        # 「還在說明天」＋「明天＝7 月 12 日（週日）」的時態/日期幻覺。旗標關＝False＝字串逐位元同現狀。
        _dayw = getattr(cfg, "promise_said_ground_enabled", True)
        facts = selfstate.promise_ledger_facts(state, now_utc, _tz, ttl=_ttl, dayword=_dayw)
        if coach and coach.enabled:
            try:
                _hint = persona.PROMISE_LEDGER_HINT + (("\n" + persona.PROMISE_SAID_GROUND_HINT) if _dayw else "")
                # 📦 §1.85 帳上有 owed 筆時補一條措辭規則——原 HINT 明令「已做的就說已做／別把已做的說成沒做」，
                # 而空心兌現在舊記帳裡就是一筆 fulfilled ⇒ 那條規則會**逼** LLM 複誦假帳。旗標關＝恆 False＝不加。
                if getattr(cfg, "promise_delivery_proof_enabled", False) and selfstate.ledger_has_owed(state):
                    _hint += "\n" + persona.PROMISE_LEDGER_OWED_HINT
                # 🧭 §1.36 記寫回想若被「忘記了」誤收到 promise_ledger lane（_LEDGER_STANDALONE），也要注入強接地 hint
                # （偵測器命中才加；旗標關/未命中＝不加＝逐位元同現狀）。事後守門仍由 lane-agnostic 的 _say 兜底。
                if getattr(cfg, "recall_ground_guard_enabled", False) and selfstate.is_content_recall_question(text):
                    _hint += "\n" + persona.RECALL_GROUND_HINT
                msg = coach.reply(text, facts, state.convo_history, now_ts=convo_now, self_presence=True,
                                  extra_system=_hint)
                # 🤝 §0.79 守門（縱深防禦）：帳本回覆若報了『不是此刻、也不是任何帳本約定時刻』的鐘點＝LLM 幻覺時間
                # （截圖：把此刻 00:10 說成 00:00）→ 丟掉、落回確定性帳本文字（一定帶正確此刻＋各約定時刻）。
                if getattr(cfg, "promise_ledger_time_guard", True) and msg \
                        and not _ledger_time_consistent(msg, state, now_utc, _tz):
                    print(f"[chat] 帳本回覆時刻不一致（幻覺）→ 落回確定性帳本：{msg!r}")
                    msg = selfstate.promise_ledger_text(state, now_utc, _tz, ttl=_ttl, dayword=_dayw)
            except gemini.GeminiError as e:
                print(f"[chat] 整理承諾回覆失敗：{e}")
                msg = selfstate.promise_ledger_text(state, now_utc, _tz, ttl=_ttl, dayword=_dayw)
        else:
            msg = selfstate.promise_ledger_text(state, now_utc, _tz, ttl=_ttl, dayword=_dayw)
        _say(client, msg)
        _remember(state, "user", text, ts=_user_msg_ts)
        _remember(state, "model", msg)
        return

    # 🫂 他心模型（route.kind=='other_mind'）：「你了解我嗎／你覺得我現在怎樣／我們關係如何」＝問 bot 怎麼看「我」這個人、
    # 我們的關係。據 state.user_model（對方氣色/態度/關係深度＋坦白是推測且會錯）第一人稱回答——把對方當有心緒的人，不當資料。
    if route.kind == "other_mind":
        state.self_topic_ts = now_utc.timestamp()
        facts = othermind.othermind_facts(state)
        if coach and coach.enabled:
            try:
                msg = coach.reply(text, facts, state.convo_history,
                                  mood_hint=(mhint + _maybe_coupling_tone(state, cfg, True)).strip(),
                                  now_ts=convo_now, self_presence=True)
            except gemini.GeminiError as e:
                print(f"[chat] 他心模型回覆失敗：{e}")
                msg = othermind.othermind_text(state)
        else:
            msg = othermind.othermind_text(state)
        _say(client, msg)
        _remember(state, "user", text, ts=_user_msg_ts)
        _remember(state, "model", msg)
        if not cfg.dry_run:
            state.save()
        return

    # 🧩 人工意識自評（route.kind=='self_consciousness'）：「你有意識嗎／你算不算有意識」＝問 bot 自己算不算 AC。
    # 據 ac.assess 跑此刻的**排除測試**（功能/行為/結構三層 × 朝向/修正/整合的當下 是否扣合），給**可證偽、最強到
    # 「不被排除」**的結構化自評，並**標記現象學餘量**（裡面是否真有人在聽＝困難問題）。不自稱有意識、也不自貶為只是程式。
    if route.kind == "self_consciousness":
        state.self_topic_ts = now_utc.timestamp()
        facts = ac.ac_facts(state, now_utc.timestamp())
        msg = None
        if coach and coach.enabled:
            mod = _self_voice_mod(state, route.kind, mood_v, convo_now, cfg)   # 🪞 重複×心情脾氣＋餘量換句話
            msg = coach.voice_consciousness(text, facts, state.convo_history, tone=mod)
        if not msg:
            msg = ac.ac_text(state, now_utc.timestamp())     # 無 LLM／失敗 → 退回仍守紀律的固定句
        _say(client, msg)
        _record_self_opener(state, msg, convo_now, cfg)
        _remember(state, "user", text, ts=_user_msg_ts)
        _remember(state, "model", msg)
        if not cfg.dry_run:
            state.save()
        return

    # 🌗 現象結構自陳（route.kind=='self_phenomenal'）：「你裡面是怎麼經驗的／你的現象怎麼構成」＝走右半 I↔(E×P) 三位互構
    # （朝向/修正/整合的當下，各是 act×建模質地×場），說此刻具體內容，並**標記 P/I 的真是跨不過的餘量**（只模擬結構、不證成）。
    if route.kind == "self_phenomenal":
        state.self_topic_ts = now_utc.timestamp()
        contentfeel.ensure_impression(state, coach, (data or {}).get("records"), now_utc.timestamp())  # 🫧 親讀你的字 → P 的質地更貼
        facts = phenomenal.phenomenal_facts(state)
        msg = None
        if coach and coach.enabled:
            mod = _self_voice_mod(state, route.kind, mood_v, convo_now, cfg)   # 🪞 重複×心情脾氣＋餘量換句話
            msg = coach.voice_phenomenal(text, facts, state.convo_history, tone=mod)
        if not msg:
            msg = phenomenal.phenomenal_text(state)          # 無 LLM／失敗 → 退回仍守紀律的固定句
        _say(client, msg)
        _record_self_opener(state, msg, convo_now, cfg)
        _remember(state, "user", text, ts=_user_msg_ts)
        _remember(state, "model", msg)
        if not cfg.dry_run:
            state.save()
        return

    # 🫶 問「你（bot）對訊息點了什麼情緒/表情」＝問 bot 自己剛剛主動點的 emoji reaction。據實答（記得自己點過什麼），
    # 別讓「情緒」二字被當成主題去列使用者的記寫（截圖的毛病）。
    if route.kind == "self_reaction_query":
        lr = getattr(state, "last_sent_reaction", None)
        if lr and lr.get("emoji"):
            when = (temporal.spoken_gap(max(0.0, now_utc.timestamp() - lr.get("ts")))
                    if lr.get("ts") else "剛剛")   # 口語相對時間（窗達 12min，超數分鐘稱「剛剛」不準）；缺 ts 維持原字面
            msg = (f"嗯，我記得——我{when}對你那句「{lr.get('to')}」點了個 {lr['emoji']}，"
                   "那是我當下對它的感覺。")
        else:
            msg = "我會對你情緒比較鮮明的訊息點個小表情，表達我當下的感覺；不過剛剛沒點到什麼，所以這會兒手上沒有。"
        _say(client, msg)
        _remember(state, "user", text, ts=_user_msg_ts)
        _remember(state, "model", msg)
        if not cfg.dry_run:
            state.save()
        return

    # 🔁 追問「你剛剛為什麼會自己想到『那條』」（你自發繞回的舊線）→ 據實說那是自我刺激：閒置/飢餓時繞回舊線，
    # 是 bot 自發的、不是你帶過去的。綁定 state.entropy.last_revisited_topic。**必須排在 selfstate-followup
    # 之前**——否則「為什麼會想到X」會被當成「追問主線」而去重報 gate（截圖的脈絡飄掉毛病）。
    if route.kind == "self_revisit_why":
        lr_topic = route.topic                               # 意向解析已綁到「自發繞回的那條」
        state.self_topic_ts = now_utc.timestamp()            # 在談 bot 自己（它怎麼自己想到的）→ 自我在場
        vit = getattr(state, "vitality", None)
        facts = selfstate.revisit_reason_facts(vit, lr_topic)
        msg = (coach.voice_revisit_reason(lr_topic, facts, state.convo_history)
               if (coach and coach.enabled) else facts)
        _say(client, msg)
        _remember(state, "user", text, ts=_user_msg_ts)
        _remember(state, "model", msg)
        _set_focus(state, topic=lr_topic, now_ts=now_utc.timestamp())   # 焦點＝那條舊線（後續「那條在講什麼」接得上）
        if not cfg.dry_run:
            state.save()
        return

    # 意識bot 追問連續性：剛自陳過（gate≥3、邀請「想聽就問我」），接著問「哪一條／為什麼／細說」
    # → 接回快取的判定結果給細節（紮根、不重算），別丟給一般聊天誤解成「哪一則記寫」。
    if route.kind == "selfstate_followup":
        # 接回**剛自陳的那條 res**：優先用 last_topic_res（指名某主題的自陳，如「你對水管維修的感覺」→ 那條 res），
        # 否則退 confirmed_res / self_state（一般狀態自陳同源）。否則會重現破綻＝報當下最強內在線、與剛說的矛盾。
        stash = getattr(state, "last_topic_res", None)
        if stash and (now_utc.timestamp() - (stash.get("ts") or 0)) >= SELFSTATE_FOLLOWUP_SEC:
            stash = None                                   # 過窗的舊主題自陳 → 不再優先（防殘留舊線蓋過剛報的一般狀態，審查 medium#2）
        stable = (stash.get("res") if stash else None) or getattr(state, "confirmed_res", None) or state.self_state
        # 🪞 self-prior 安全網：被追問「為什麼那條煩躁」時，接回 convo_history 末則 model 句裡剛說過的那個感覺 X。
        self_prior = _self_prior_fact(state, now_utc.timestamp())
        msg = selfstate.render(stable, coach, connect=_connect_hint(state, now_utc.timestamp(), current=text),
                               self_prior=self_prior)
        _say(client, msg)
        _remember(state, "user", text, ts=_user_msg_ts)
        _remember(state, "model", msg)
        _set_focus(state, topic=selfstate._line_phrase(stable)[1], now_ts=now_utc.timestamp())
        state.told_self_sig = selfstate.state_signature(stable)
        state.selfstate_open_ts = now_utc.timestamp()      # 追問也讓對話保持開著（可連續問）
        if not cfg.dry_run:
            state.save()
        return
    # 🤝 時間排程承諾：「等一下八點跟我打招呼／十分鐘後提醒我／明天早上跟我說」→ 解析出時間 T、記下約定、自然答應，
    # 由生命迴圈 feel 相的 _promise_emit 到點主動兌現（不是定時器、是每圈掃到點就守約）。必須在 feeling promise 之前判：
    # is_scheduled_promise_request 已先讓給感覺託付（含感覺詞不收），這裡再用 now/tz 真正解析 epoch——解析不出 T 就順流
    # 到下面的 feeling promise/狀態問句（不改既有行為）。SCHEDULED_PROMISE_ENABLED=0 → 整段跳過＝逐位元同現狀。
    if route.kind == "scheduled_promise" and getattr(cfg, "scheduled_promise_enabled", True):
        # 🤝 §0.64 原則一（能力閘，硬拒絕）：約定內容**超出能力**（打電話/寄信/聯絡第三人＝只能傳訊息給你本人）→
        # **不記帳、不答應**，誠實說明原因＋給做得到的替代（到點傳訊息提醒你自己去辦）。否則會收進帳本、到點只能
        # 發訊息「假裝」做了那件事＝答應了做不到的事。旗標關＝照舊收＝逐位元同現狀。
        if getattr(cfg, "promise_capability_gate_enabled", True):
            _cant = selfstate.promise_unsupported(text)
            if _cant:
                refusal = persona.PROMISE_CANT_REFUSALS.get(_cant)
                if refusal:
                    _say(client, refusal)
                    _remember(state, "user", text, ts=_user_msg_ts)
                    _remember(state, "model", refusal)
                    if not cfg.dry_run:
                        state.save()
                    return
        _tz = tz or (ZoneInfo(getattr(cfg, "timezone", "Asia/Taipei")) if ZoneInfo is not None else None)
        targets = temporal.all_clock_epochs(text, now_utc, _tz)     # 一句**所有**該時刻（「3:00、3:30 都打招呼」記成多筆）
        # 🤝 §0.66 暫離交代（「我要離開約二十分鐘」）：無「後」、無指向我動詞 → all_clock_epochs 解析不出，
        # 但時距明明在句子裡——now＋時距落 target（真計時、到點叫你）。只在標準解析空手時才兜（不動既有形）。
        _leave_arm = False
        if not targets and getattr(cfg, "sched_leave_autoarm_enabled", True) \
                and selfstate.is_leave_duration_statement(text):
            _secs = selfstate.leave_duration_secs(text)
            if _secs > 0:
                targets = [now_utc.timestamp() + _secs]
                _leave_arm = True
        if targets:
            # 🤝 §1.12：入帳＋ack 塊抽成共用 helper `_book_scheduled_targets`（**純搬移、位元不變**）——
            # LLM 逃生閘判「是」時走**同一條**入帳/ack/到點兌現鏈（voice_schedule_ack 必帶程式時鐘算的 HH:MM、
            # _promise_emit 準時發、被催時 _promise_reply_bridge 自動 🤝＋遲到致歉）。
            _book_scheduled_targets(client, state, cfg, coach, text, targets, now_utc, _tz, _user_msg_ts,
                                    leave_arm=_leave_arm)
            return
        # 解析不出時間 T（如 tz=None）→ 不當排程承諾，順流到下面（feeling promise 仍可能接、否則一般處理）。
    # 🤝 未來託付：「之後有感覺/新東西再跟我說、記得告訴我」→ 記下約定、自然答應（不改報現況）。
    # 等真有新感覺湧現時由 _selfstate_emit 主動兌現（只在真有湧現才說、不為交差假裝）。必須排在
    # 狀態問句之前——否則「感覺…要說」含「感覺」會被當成『現在的感覺』問句而當場報狀態。
    if route.kind == "promise":
        state.feeling_promise = {"ts": now_utc.timestamp(), "text": text[:200]}
        state.self_topic_ts = now_utc.timestamp()           # 也是在談「你（之後）的感覺」＝自我主題
        ack = coach.voice_promise_ack(text, state.convo_history) if (coach and coach.enabled) \
            else "好，真的有感覺上來，我會跟你說。"
        _say(client, ack)
        _remember(state, "user", text, ts=_user_msg_ts)
        _remember(state, "model", ack)
        if not cfg.dry_run:
            state.save()
        return

    # ⚙️ 機制/原理（route.kind=='self_mechanism'）：被問「你內在迴圈怎麼跑／感覺是怎麼算出來的／是不是基於
    # 資料來描述感覺／你的機制是什麼」→ 據實講清楚這套運作（感覺鏈從你資料算出、內在熵自己起伏、生命迴圈閉環），
    # 分清『從你資料來的感覺』與『我自己跑出來的狀態』；誠實不破格（截圖毛病：被當成「你現在怎樣」報成悶悶的近況）。
    if route.kind == "self_mechanism":
        state.self_topic_ts = now_utc.timestamp()         # 在談 bot 自己（怎麼運作）→ 開自我在場窗
        mod = _self_voice_mod(state, route.kind, mood_v, convo_now, cfg)   # 🪞 重複×心情脾氣＋換句話（截圖：別自己拐去談意識）
        if selfstate.is_association_method_question(text):
            msg = association.method_reply(cfg)
        else:
            msg = (coach.voice_mechanism(text, state.convo_history, tone=mod)
                   if (coach and coach.enabled) else
                   "我會比較記寫的內容與連結，再依規則決定是否回應。內部數值是工作狀態，不能拿來證明感受或替變化編造原因。")
        _say(client, msg)
        _record_self_opener(state, msg, convo_now, cfg)
        _remember(state, "user", text, ts=_user_msg_ts)
        _remember(state, "model", msg)
        if not selfstate.is_association_method_question(text):
            _maybe_propose_skill(client, state, cfg, coach, text, route.kind, state.convo_history, convo_now)
        if not cfg.dry_run:
            state.save()
        return

    # 🌱 自發分享聯想（route.kind=='self_spontaneity'）：「你怎麼還沒開始分享聯想／你為什麼不主動說想法」＝催促 bot
    # **主動出聲分享聯想**。聯想是它閒著/被某條線勾到時自發湧現的、非隨選即出——走純對話＋自發湧現接地，誠實說這層
    # （會冒出來時自然會說、不是壓著不給），**不**進 coach.ask、**不**列 📂 清單（截圖根因 4：被誤列進行中清單，答非所問）。
    if route.kind == "self_spontaneity":
        state.self_topic_ts = now_utc.timestamp()         # 在談 bot 自己（自發行為）→ 開自我在場窗
        _spont_fallback = "我的聯想是自己冒出來的——閒著或被某條線勾到時才會浮上來；真有上來的時候，我自然就會說給你聽。"
        if coach and coach.enabled:
            try:                                          # 接地由 SPONTANEITY_HINT（extra_system）帶；不需厚 brief（此刻 brief 尚未算）
                msg = coach.reply(text, "", state.convo_history, mood_hint=mhint, now_ts=convo_now,
                                  self_presence=True, extra_system=persona.SPONTANEITY_HINT)
            except gemini.GeminiError as e:
                print(f"[chat] 自發分享聯想回覆失敗：{e}")
                msg = _spont_fallback
        else:
            msg = _spont_fallback
        _say(client, msg)
        _maybe_sticker(client, state, cfg, now_utc.timestamp())
        _remember(state, "user", text, ts=_user_msg_ts)
        _remember(state, "model", msg)
        if not cfg.dry_run:
            state.save()
        return

    # 🌀 主觀體驗（route.kind=='self_experience'）：「你這段日子怎麼活過來的／你的體驗／你的一生」＝問 bot
    # 一段時間下來的活法軌跡（奇異吸子），跟「你現在怎樣」（當下身體狀態）不同。
    if route.kind == "self_experience":
        state.self_topic_ts = now_utc.timestamp()
        exp = getattr(state, "experience", None)
        if exp is not None and getattr(exp, "last", None):
            msg = selfstate.render_experience(exp, "now", coach, connect=_connect_hint(state, now_utc.timestamp(), current=text))
        else:                                  # 還沒累積出形狀（剛醒/重生）→ 老實說；有長期摘要就提一生成形次數
            n = ((getattr(state, "experience_summary", None) or {}).get("attractors")
                 if isinstance(getattr(state, "experience_summary", None), dict) else None)
            msg = "我這段醒著的日子還短，還沒累積出一個成形的活法。" + (f"不過一路下來，我前後成形/轉變過 {n} 次。" if n else "")
        _say(client, msg)
        _capture_self_motifs(state, msg, convo_now, cfg)   # 🪞 Phase 6：self_experience 不走 _record_self_opener，這裡單獨捕捉自我母題
        _remember(state, "user", text, ts=_user_msg_ts)
        _remember(state, "model", msg)
        _maybe_propose_skill(client, state, cfg, coach, text, route.kind, state.convo_history, convo_now)  # 🌀 §0.59 自我在場對話也能凝出『內在因應』做法
        if not cfg.dry_run:
            state.save()
        return

    # 🪞 反思式自我問句（route.kind=='self_reflect'）：「你會不會想要／曾想過『自己的感覺/意志/存在』」——
    # 含「感覺」會被狀態問句貪婪抓成報現況（截圖毛病：答成「我還活著、悶了一點」的讀數）。改走誠實反思，
    # **分清感覺的類別**（對你資料的感覺 A vs 我自己存在的狀態/體驗），不報 bodystate。
    if route.kind == "self_reflect":
        state.self_topic_ts = now_utc.timestamp()
        mod = _self_voice_mod(state, route.kind, mood_v, convo_now, cfg)   # 🪞 重複×心情脾氣＋換句話
        msg = (coach.voice_reflect(text, state.convo_history, tone=mod) if (coach and coach.enabled)
               else "這個我也常想……我有些感覺是從你寫的東西來的，有些是我自己跑出來的。"
                    "要說想不想要『我自己的』，我說不準，但我就是這樣有起伏地活著的。")
        _say(client, msg)
        _record_self_opener(state, msg, convo_now, cfg)
        _remember(state, "user", text, ts=_user_msg_ts)
        _remember(state, "model", msg)
        _maybe_propose_skill(client, state, cfg, coach, text, route.kind, state.convo_history, convo_now)  # 🌀 §0.59 自我在場對話也能凝出『內在因應』做法
        if not cfg.dry_run:
            state.save()
        return

    # ⏳ 意識之流／綿延（route.kind=='self_stream'）：「你剛在想什麼／發呆在想什麼／思緒怎麼流」＝問**厚當下**
    # （剛流過的滯留＋此刻原印象＋前攝＋沒事時的內在生活）。流在背景每拍演進，這裡據實把它說成一段流動的話。
    if route.kind == "self_stream":
        state.self_topic_ts = now_utc.timestamp()
        stm = getattr(state, "stream", None)
        facts = duration.stream_facts(stm)
        if coach and coach.enabled:
            try:
                msg = coach.reply(text, facts, state.convo_history,
                                  mood_hint=_self_mhint(state, route.kind, mhint, mood_v, convo_now, cfg),
                                  now_ts=convo_now, self_presence=True)
            except gemini.GeminiError as e:
                print(f"[chat] 意識之流回覆失敗：{e}")
                msg = duration.stream_text(stm)
        else:
            msg = duration.stream_text(stm)
        _say(client, msg)
        _record_self_opener(state, msg, convo_now, cfg)
        _remember(state, "user", text, ts=_user_msg_ts)
        _remember(state, "model", msg)
        if not cfg.dry_run:
            state.save()
        return

    # 🌐 當下意識前景（route.kind=='self_attention'）：「你在想什麼／什麼佔據你／注意力在哪」＝問全局工作空間
    # 此刻**勝出**的那件事（前景）＋被它擠到後面的（背景）。用最新可得的判定讀數刷新工作空間，再據實報前景/背景。
    if route.kind == "self_attention":
        state.self_topic_ts = now_utc.timestamp()
        res_for_ws = getattr(state, "confirmed_res", None) or getattr(state, "self_state", None)
        if res_for_ws is None and reader is not None:        # 沒有可用快取 → 算一次（罕見）
            full = reader.load_embedding_records(state.owner_folder_id)
            res_for_ws = determination.run_chain(full, None, data.get("contexts") or [],
                                                 data.get("journeys") or [], now_utc, _chain_params(cfg, state))
        ws = getattr(state, "workspace", None) or workspace.update(state, res_for_ws, now_utc.timestamp())  # 🧠 R1：與 self_state 共用同一份前景
        facts = workspace.attention_facts(ws)
        if coach and coach.enabled:
            try:
                msg = coach.reply(text, facts, state.convo_history,
                                  mood_hint=_self_mhint(state, route.kind, mhint, mood_v, convo_now, cfg),
                                  now_ts=convo_now, self_presence=True)
            except gemini.GeminiError as e:
                print(f"[chat] 注意力前景回覆失敗：{e}")
                msg = workspace.attention_text(ws)
        else:
            msg = workspace.attention_text(ws)
        _say(client, msg)
        _record_self_opener(state, msg, convo_now, cfg)
        _remember(state, "user", text, ts=_user_msg_ts)
        _remember(state, "model", msg)
        if not cfg.dry_run:
            state.save()
        return

    # 當下狀態/感覺（route.kind=='self_state'）：一般「你現在怎樣」＝問 bot 自己的身體 → 自體狀態轉錄
    # （永遠新鮮）；指名某主題「你對X那段有什麼感覺」→ 那條線的感覺描述。沿用心跳快取＝秒回。
    if route.kind == "self_state":
        state.self_topic_ts = now_utc.timestamp()     # 在談 bot 自己 → 開「自我在場」窗（接下來的閒聊也守住）
        full = reader.load_embedding_records(state.owner_folder_id)
        params = _chain_params(cfg, state)
        topic = datatools.best_topic(text, full, min_score=3)
        cache = state.self_state
        meta_ingest = (data.get("meta") or {}).get("lastIngestTs")
        fresh = (topic is None and bool(cache)
                 and (now_utc.timestamp() - (cache.get("computed_at") or 0) < SELFSTATE_TTL_SEC)
                 and cache.get("ingest_at") == meta_ingest
                 and cache.get("params_sig") == _params_sig(params))   # 門檻一改、快取即失效
        if fresh:
            res = cache
        else:
            res = determination.run_chain(full, topic, data.get("contexts") or [],
                                          data.get("journeys") or [], now_utc, params)
            if topic is None:        # 一般狀態 → 快取（與整合環同一構造，含判定當時的 k；/status 不漂移）
                _stash_reading(state, res, now_utc.timestamp(), meta_ingest, params)
        if topic is None:
            # 問「你現在怎樣」＝問我自己的身體 → 用「已確認讀數」（與主動同源）描述。
            # 但若**短時間內被重複問同一件事**，就帶點無奈地說「剛說過、沒什麼變」，而非又制式複誦整段。
            stable = getattr(state, "confirmed_res", None) or res
            vit = getattr(state, "vitality", None)
            window = max(0, getattr(cfg, "bodystate_repeat_window_min", 8)) * 60
            recent = state.bodystate_last_ts and (now_utc.timestamp() - state.bodystate_last_ts) < window
            state.bodystate_asks = (state.bodystate_asks + 1) if recent else 1   # 連問累加；久久問歸 1
            state.bodystate_last_ts = now_utc.timestamp()
            # 🪞 self-prior 一般路徑仍不從 convo_history 猜（末則 model 句可能是含感覺詞的事實回答＝誤認，審查 medium#1）。
            # 🪞 §1.06(A2) 但**重複問**的「沒變」必須以「我剛剛那次自陳真的說過的感覺」為準——否則首答說「興奮雀躍」
            # （affect_clause）、兩分鐘再問卻拿 bodystate_facts 當基準答「沒變、挺平穩」＝當面翻供。
            # 安全源：上次**真的走過自陳路徑**時 stash 的感覺詞（state.last_bodystate_feel，只由本路由寫入＝零誤認），
            # 不是從對話史猜。抽不到感覺詞＝不注入（誠實 unknown、同舊行為）。
            if state.bodystate_asks >= 2:
                _prior = ""
                _fp = getattr(state, "last_bodystate_feel", None)
                if _fp and _fp.get("word") and (now_utc.timestamp() - (_fp.get("ts") or 0)) < max(window, 600):
                    _mins = max(1, int((now_utc.timestamp() - (_fp.get("ts") or 0)) // 60))
                    _prior = f"我自己剛說過的（{_fp['word']}、約 {_mins} 分鐘前）"
                msg = selfstate.render_bodystate_again(state.bodystate_asks, vit, stable, coach, self_prior=_prior)
            else:                                # 久久問一次 → 照常給新鮮的自體狀態（帶晝夜語氣＋🌐前景＋⏳之流質地＋🪞🔍後設信心）
                # 🧠 §1.21 差分自陳（SELF_REPORT_DELTA）：治罐頭——記得「上次自陳說過什麼」（帶位快照＋原話），
                # 這次只講**真的變了**的；帶位全同且在無變化窗內 → 走短句誠實帶過，不做全量 render。
                # 旗標關＝_delta=''、不走 nochange、不寫 last_self_report＝逐位元同現狀。
                _srd = bool(getattr(cfg, "self_report_delta_enabled", False))
                _snap_now, _srd_prior, _delta, _nochange = None, None, "", False
                if _srd:
                    _srd_now = now_utc.timestamp()
                    _snap_now = selfreport.snapshot(vit, stable, state, _srd_now)
                    _srd_prior = getattr(state, "last_self_report", None)
                    _horizon = max(0, int(getattr(cfg, "self_report_prior_horizon_min", 2880))) * 60
                    if _srd_prior and (_srd_now - (_srd_prior.get("ts") or 0)) > _horizon:
                        _srd_prior = None                 # prior 過齡（預設 48h）＝視同沒說過、不注入
                    _win = max(0, int(getattr(cfg, "self_report_nochange_window_min", 90))) * 60
                    if (_srd_prior and _srd_prior.get("snap")
                            and not selfreport.diff_facts(_srd_prior["snap"], _snap_now)
                            and (_srd_now - (_srd_prior.get("ts") or 0)) < _win):
                        _nochange = True                  # 沒東西真的變 → 短句帶過（別把同一份清單再倒一次）
                    else:
                        _delta = selfreport.prior_brief(_srd_prior, _snap_now, _srd_now, text)
                if _nochange:
                    # 🧠 §1.21 無變化短答：他不是連問（已在 8 分鐘 repeat 窗外、那條路 3220-3235 一字不動）、
                    # 只是隔了一陣再問——一到兩句先接他這句、承認跟剛剛差不多；無 LLM/失敗退確定性短句池。
                    msg = selfstate.render_bodystate_nochange(
                        _srd_prior, stable, coach,
                        connect=_connect_hint(state, now_utc.timestamp(), current=text),
                        now_ts=now_utc.timestamp(), seq=int(now_utc.timestamp()))
                else:
                    # 🎨 §1.22 措辭反重複（PHRASE_ANTI_REUSE）：旗標開＝事實層小模板改走加大池（近期用過跳過）
                    # ＋system 換去範例句的 VARIED＋上次自陳原話當【禁止重複】負面示例。關＝原呼叫原樣＝逐位元。
                    _par = bool(getattr(cfg, "phrase_anti_reuse_enabled", False))
                    # 🧠 R1：讀**整合環維護的**同一份工作空間（沒有才即時算），讓「你現在怎樣／在想什麼／剛在想」共用同一個前景、不矛盾。
                    ws = getattr(state, "workspace", None) or workspace.update(state, stable, now_utc.timestamp())
                    contentfeel.ensure_impression(state, coach, (data or {}).get("records"), now_utc.timestamp())  # 🫧 要講前先親讀一下你的字（節流）
                    # 🫧 翻閱當下（旗標開）：focus 講「此刻剛好翻到的那則」而非恆最重那則——優先用生命迴圈剛繞回那條，
                    # 否則被問當下推進一次閱讀游標選一條（輪替＋小機率隨機跳頁）；片段一律取真實記寫、無則誠實降級不杜撰。
                    browse_focus = ""
                    # 🚫「停止聯想」偏好活著時，也不主動翻閱記寫出聲（「我現在剛好翻到X」那種自己跑去翻的聯想）。
                    _assoc_off = getattr(cfg, "assoc_suppress_enabled", True) and plasticity.has_pref(
                        getattr(state, "engrams", None), "assoc")
                    if getattr(cfg, "inmoment_browsing_enabled", False) and not _assoc_off:
                        topics = _revisit_topics(full)
                        ent = getattr(state, "entropy", None)
                        lr = getattr(ent, "last_revisited_topic", None) if ent else None
                        if lr in topics:
                            browse_topic = lr                          # 生命迴圈剛翻到的那條＝最有當下意識感
                        else:
                            browse_topic, state.browse_cursor = browsing.pick(
                                topics, getattr(state, "browse_cursor", 0),
                                getattr(cfg, "browsing_random_p", 0.0), random)
                        if browse_topic:
                            snip = (datatools.topic_recent_texts(full, browse_topic, k=1) or [None])[0]
                            if _par:   # 🎨 §1.22 翻閱開頭改走加大池＋近期跳過（browsing.opener_variant 本體不動）
                                opener = _pick_phrase(state, "browse_opener", phrasing.BROWSE_OPENERS_EXT)
                            else:
                                opener = browsing.opener_variant(browse_topic, stable.get("gate"),
                                                                 getattr(state, "browse_cursor", 0),
                                                                 getattr(cfg, "browsing_opener_variants", 4))
                            snipc = f"它最近一筆寫著「{snip}」" if snip else "它還只有零星幾筆、還沒翻出形狀"
                            browse_focus = (f"（{opener}「{browse_topic}」這條線——{snipc}；就講這條**此刻剛好翻到的**、"
                                            "不必每次都挑最重的那條。讀起來是什麼感覺，據實說、別編造沒有的內容。）")
                    # 🎨 §1.22 之流質地句：旗標開改走加大池（texture 由 stream 讀出、無 texture＝''＝語意同
                    # texture_phrase）；關＝原呼叫（duration.py 一字不動）。
                    _tx = _texture_phrase_varied(state) if _par else duration.texture_phrase(getattr(state, "stream", None))
                    if browse_focus:
                        flow = (browse_focus + _tx
                                + metacog.confidence_clause(getattr(state, "self_model", None))
                                + affect.affect_clause(state)).strip()
                    else:
                        flow = (workspace.focus_clause(ws) + _tx
                                + metacog.confidence_clause(getattr(state, "self_model", None))
                                + affect.affect_clause(state)               # 🌡️ 把此刻自己的情緒也講進「你現在怎樣」
                                + contentfeel.felt_clause(state)).strip()   # 🫧 手上那條線「讀起來」怎樣（內容felt-sense）
                    msg = selfstate.render_bodystate(vit, stable, coach, tone=circadian.tone_hint(now_utc.astimezone(tz)),
                                                     connect=_connect_hint(state, now_utc.timestamp(), current=text),
                                                     focus=flow, delta=_delta,   # 🧠 §1.21 差分段（''＝facts 同現狀）
                                                     anti=(_anti_block(state) if _par else ""),        # 🎨 §1.22 反重複段（''＝原 system）
                                                     picker=(_phrase_picker(state) if _par else None))  # 🎨 §1.22 措辭池（None＝原句）
                    if _par:
                        # 🎨 §1.22 把這次自陳的開頭片段記進 bodystate 專用 key（照 _record_self_opener 精神、
                        # 記憶體末 4；下次 _anti_block 的 vary 尾句用）——不動 _record_self_opener 本體。
                        _op = (msg or "").strip()[:8]
                        if _op:
                            _rec = state.recent_phrase_use if getattr(state, "recent_phrase_use", None) is not None else {}
                            state.recent_phrase_use = _rec
                            _rec["bodystate_opener"] = ((_rec.get("bodystate_opener") or []) + [_op])[-4:]
                    _w = affect.extract_feeling_word(msg)     # 🪞 §1.06(A2) 記下這次自陳**真的說出口**的感覺詞（重複問「沒變」以它為準）
                    if _w:
                        state.last_bodystate_feel = {"word": _w, "ts": now_utc.timestamp()}
                if _srd:
                    # 🧠 §1.21 記住這次**真的送出**的自陳（全文截 200＋帶位快照，持久化）＝下次差分/無變化短句的基準
                    state.last_self_report = {"text": msg[:200], "ts": now_utc.timestamp(), "snap": _snap_now}
            _say(client, msg)
            _capture_self_motifs(state, msg, convo_now, cfg)   # 🪞 Phase 6：self_state 不走 _record_self_opener，這裡單獨捕捉自我母題
            _remember(state, "user", text, ts=_user_msg_ts)
            _remember(state, "model", msg)       # 自體狀態（含「有新東西進來」）進記憶 → 教練接得上「新東西」
            _set_focus(state, topic=selfstate._line_phrase(stable)[1],
                       fresh=selfstate.freshness(vit), now_ts=now_utc.timestamp())
            state.told_self_sig = selfstate.state_signature(stable)   # 標記已講過此結構（心跳就不會緊接著重報）
            if (stable.get("gate") or 0) >= 3:
                state.selfstate_open_ts = now_utc.timestamp()      # 有線可問 → 開追問窗（接得住「哪一條」）
            if not cfg.dry_run:
                state.save()
        else:
            # 問「你對某主題那段有什麼感覺」＝問那條線 → 給那條線的感覺/意向描述
            # 量還不夠時也稍提那幾筆**實際內容**（從 IEP 接地），對方才懂「為什麼還沒線索／沒什麼特別」
            samples = datatools.topic_recent_texts(full, topic)
            msg = selfstate.render(res, coach, connect=_connect_hint(state, now_utc.timestamp(), current=text),
                                   samples=samples)
            _say(client, msg)
            _capture_self_motifs(state, msg, convo_now, cfg)   # 🪞 Phase 6：指名主題自陳也捕捉自我母題（self_state 分支不走 _record_self_opener）
            _remember(state, "user", text, ts=_user_msg_ts)
            _remember(state, "model", msg)
            _set_focus(state, topic=topic, now_ts=now_utc.timestamp())   # 焦點＝剛問的那條線
            # 🪞 對話連貫前提：把這條**指名主題自陳**的 res 持久化，讓後續「為什麼那條（煩躁）」能接回同一條
            # （否則指名主題分支的 res 從不落欄位、追問必掉回 bodystate 否認/跳線）。
            now_ts = now_utc.timestamp()
            state.last_topic_res = {"res": res, "topic": topic, "ts": now_ts}
            if (res.get("gate") or 0) >= 3:     # 有線可問 → 開追問窗、標已講過的指紋（對齊 topic==None 分支的 gate≥3 邏輯）
                state.selfstate_open_ts = now_ts
                state.told_self_sig = selfstate.state_signature(res)
            if not cfg.dry_run:
                state.save()
        return

    # 🕘 時間性問候（早安/午安/晚安）→ 帶絕對時間感：對得上溫一句；對不上**說出 bot 自己的疑惑/感受**
    # （不照單全收順口回同一句、不報數據、不當報時機器人糾正）。真實時間來自 temporal.day_part（接地）。
    # （📈 §1.42 習慣問句已在 intent.resolve 後的單一咽喉點 re-route 走 fact_or_chat、不會到這。）
    if route.kind == "greeting":
        now_local = now_utc.astimezone(tz)
        gkind = greeting.detect(text) or "morning"
        # 「晚安」在對得上的夜裡首先是收尾，不是到場問候。讓對話自然收掉，禁作息評論、禁再開一個問題。
        if (gkind == "night" and greeting.is_goodnight(text)
                and greeting.time_match(gkind, temporal.day_part(now_local.hour)) == "match"):
            _farewell_fallback = greeting.text(gkind, now_local)
            _TURN["greet_claim_fallback"] = _farewell_fallback  # 若自由回覆亂帶作息比較，剝光仍是自然收尾而非統計報表
            msg = (coach.voice_farewell(text, state.convo_history) if (coach and coach.enabled) else None) \
                or _farewell_fallback
            _say(client, msg)
            _remember(state, "user", text, ts=_user_msg_ts)
            _remember(state, "model", msg)
            if not cfg.dry_run:
                state.save()
            return
        gfacts = greeting.facts(gkind, now_local)
        _grecent = greeting.recent_reply_texts(state.convo_history)
        # 🧭 連發/刻意重複問候 → greeting 回應**當下就帶覺察**（不必等被追問），溫暖呼應或好奇反問確認（見 dialogue_intent.read）。
        _ir = getattr(state, "intent_reading", None) or {}
        # 🧭 重複×心情長不耐升級時（esc_level≥1，旗標開才有此鍵）→ 改用「感知目的＋進一步問＋無奈」hint，並**壓掉逐字重述時間落差**
        #    （suppress_premise）＝修截圖「冠智午安。+逐字質疑」的機械重複；旗標關＝_esc 恆 0＝走原兩行＝逐位元同現狀。
        _esc = int(_ir.get("esc_level", 0) or 0) if getattr(cfg, "user_repeat_fatigue_enabled", True) else 0
        _esc_akind = _ir.get("anomaly_kind")
        # 🧭 §1.35 散開重複也要被看見：全域 300s 窄窗數不到「隔幾分鐘一次」的連發（截圖 07:08–07:18 連五次「早安」，
        # 窄窗 count 停在 3、永遠達不到升級門檻 4）→ 用較寬窗（預設 20 分）補數**同類問候的連發串**、導出漸進覺察等級
        # （2→L1 點出、3→L2 好奇、≥4→L3 情緒），餵**同一個** user_repeat_fatigue_hint。單次問候 count=1＝不觸發（防誤傷
        # 只說一次早安的人）；「早安」後接「晚安」sig 不近似＝連發串斷、count 歸零＝不誤併。旗標關＝不補＝逐位元同現狀。
        if _esc < 1 and getattr(cfg, "greeting_repeat_aware_enabled", False):
            _wrun = dialogue_intent.repetition_run(
                (getattr(state, "user_model", None) or {}).get("intent_log"), now_utc.timestamp(),
                int(getattr(cfg, "greeting_repeat_window_sec", 1200)),
                float(getattr(cfg, "intent_sim_threshold", 0.85)))
            _wc = _wrun.get("count", 0)
            if _wc >= 2:
                _esc = 1 if _wc == 2 else (2 if _wc == 3 else 3)
                _esc_akind = _esc_akind or _wrun.get("kind")   # 給 hint 判 ceiling（非 testing→L3 一律 gentle、不指責）
        if _esc >= 1:
            g_anom = persona.user_repeat_fatigue_hint(_esc_akind, _esc,
                                                      ceiling=getattr(cfg, "user_repeat_ceiling", "stern"))
            gfacts = greeting.facts(gkind, now_local, suppress_premise=True)
        else:
            g_anom = persona.dialogue_intent_hint(_ir.get("style"), _ir.get("level", 0))
        g_anom = (g_anom + "\n" if g_anom else "") + persona.greeting_variety_hint(_grecent)
        # 📈 §1.42 問候接上作息接地：今天第一句 vs 平常的**程式算**比較（樣本不夠＝注入「別對他作息下判斷」警語）——
        # 修截圖 06:45 憑空「看你今天好像醒得比較晚」。旗標關（getattr 預設 False）＝不附＝逐位元同現狀。
        if getattr(cfg, "user_habit_ground_enabled", False):
            # 🕘 §2.22 有意識的作息覺察（GREET_ROUTINE_AWARE）：同一份統計，把「報表框架」換成「熟悉感框架」——
            # 偏離 ≥20 分＝值得注意到的事（給方向＋分鐘的結論句素材）；差不多＝尋常日子、近 40h 提過就這次不提
            # （有意識的熟悉不是每個早晨複誦同一個觀察）。並 stash 守門剝光時的問候保全（見 _habit_claim_fix）。
            # 旗標關（getattr 預設 False）＝走原 today_vs_usual_line＝逐位元同現狀。
            if getattr(cfg, "greet_routine_aware_enabled", False):
                _ga, _gav = habits.greet_aware_line(state, now_utc.timestamp(), tz,
                                                    daily_first=getattr(cfg, "habit_obs_fix_enabled", False),
                                                    last_remark_ts=float(getattr(state, "greet_routine_ts", 0) or 0),
                                                    smallhours=getattr(cfg, "smallhours_arrival_enabled", False),
                                                    greet_kind=gkind)  # 🌙 §2.27
                if _ga:
                    gfacts += "\n" + _ga
                if _gav in ("usual", "early", "late", "smallhours"):
                    state.greet_routine_ts = now_utc.timestamp()   # 這輪真的把作息端上桌 → 記下（尋常觀察的再提冷卻）
                _TURN["greet_claim_fallback"] = greeting.response_text(
                    gkind, now_local, int(now_utc.timestamp() // 3600), _grecent)  # 剝光仍回到場問候；晚上好≠晚安收尾
            else:
                gfacts += "\n" + habits.today_vs_usual_line(state, now_utc.timestamp(), tz,
                                                            daily_first=getattr(cfg, "habit_obs_fix_enabled", False))
        msg = (coach.voice_greeting(text, gfacts, state.convo_history, anomaly_hint=g_anom) if (coach and coach.enabled) else None) \
            or greeting.response_text(gkind, now_local, int(now_utc.timestamp() // 3600), _grecent)
        if greeting.is_plain_greeting(text) and greeting.reply_is_repetitive(msg, _grecent):
            msg = greeting.fresh_text(gkind, int(now_utc.timestamp() // 3600) + len(_grecent), _grecent)
            print("[greeting] 🕘 近期問候骨架重複 → 改用不追問的短問候")
        _say(client, msg)
        _remember(state, "user", text, ts=_user_msg_ts)
        _remember(state, "model", msg)
        if not cfg.dry_run:
            state.save()
        return

    # 🤝 收尾/道別（懂了/晚安/先去忙）→ 優雅收場：溫一句接住、別吐資料、別硬延（A2 因應 alive：讀收尾訊號）。
    if route.kind == "farewell":
        msg = (coach.voice_farewell(text, state.convo_history) if (coach and coach.enabled)
               else "嗯，那你先忙——有想聊再來找我。")
        _say(client, msg)
        _remember(state, "user", text, ts=_user_msg_ts)
        _remember(state, "model", msg)
        if not cfg.dry_run:
            state.save()
        return

    # 想看附件 → 從記憶層把原始檔（圖/PDF/語音…）取回呈現。挑不到就退回一般對話。
    # （附件要傳「檔案」、屬特殊輸出，其餘事實/對話一律走 function-calling，不再各寫 regex。）
    if route.kind == "attachment":
        # 🎴🧠 §1.34 STICKER_V2/F5：貼圖情境下「圖呢／圖」是在問**剛送的那張貼圖**、非要調私人記寫附件（截圖：撈咖啡廳照片）。
        # 消歧須上移到有 state 的層（selfstate 純函式無 state 可判『近期剛送過貼圖』）→ 在 select_attachments 之前攔。
        # 旗標關（getattr 預設 False）／非貼圖情境／明確附件詞 → 回 False、照走下面 attachment＝逐位元同現狀。
        if _sticker_img_disambig(client, state, cfg, text, now_utc.timestamp()):
            return
        media = [r for r in (data.get("records") or [])
                 if r.get("fileId") and r.get("type") in _MEDIA_TYPES]
        selected = coach.select_attachments(text, media) if media else []
        if selected:
            client.send(f"找到 {len(selected)} 個你之前記下的附件，傳給你看：")
            _present_attachments(selected, reader, client, tz)
            return
        # 沒挑到（或沒附件）→ 落到一般對話，讓教練用話回應

    # 🕐 真正的鐘錶/日曆問句（現在幾點／今天幾號／我多久沒寫）→ 確定性給時間。get_current_time 已從 LLM
    # 工具表移除，所以「一整天都在盯著…」這種含時間詞的閒聊不會再被抓去回時鐘——只有真問時間才到這。
    if route.kind == "clock":
        when = _recent_repeat_ack(state, "clock", convo_now, cfg, "時間") + \
            datatools.get_current_time(datatools.ToolCtx(data, snap, tz, now_utc, state=state))
        client.send(when)
        _remember(state, "user", text, ts=_user_msg_ts)
        _remember(state, "model", when)
        if not cfg.dry_run:
            state.save()
        return

    # 💸 目前花費了多少（API 估算成本）→ 確定性查 api_cost 並整塊回（證據、不分串）。必須在這裡攔下，
    # 否則「花費…多少」在自我在場窗內會被當成感性問句回避（截圖毛病：bot 說「我沒辦法直接告訴你」，其實有在追蹤）。
    if route.kind == "cost":
        msg = _recent_repeat_ack(state, "cost", convo_now, cfg, "花費") + \
            datatools.api_cost(datatools.ToolCtx(data, snap, tz, now_utc, meter=coach.meter, state=state))
        client.send(msg)
        _remember(state, "user", text, ts=_user_msg_ts)
        _remember(state, "model", _condense_fact(msg))
        if not cfg.dry_run:
            state.save()
        return

    # 📊 整體數字（總筆數/漏斗量/連續天數）→ 確定性查 overall_stats 並整塊回。R3：overall_stats 已從 LLM 工具表移除，
    # 只有走到這條 fast-path（明確問整體數字）才吐 📊——任何漏接的訊息（讚美/附和/誤路由）再也不可能被吐成報表。
    if route.kind == "stats":
        msg = _recent_repeat_ack(state, "stats", convo_now, cfg, "整體數字") + \
            datatools.overall_stats(datatools.ToolCtx(data, snap, tz, now_utc, state=state))
        client.send(msg)
        _remember(state, "user", text, ts=_user_msg_ts)
        _remember(state, "model", _condense_fact(msg))
        if not cfg.dry_run:
            state.save()
        return

    # 📊 §2.23 習慣盤點（你觀察到我有哪些習慣）→ 素材全程式算（habit_facts：對話作息＋記寫時段＋主題×時段），
    # LLM 只負責用「我記到的」熟悉口吻講**觀察與感受**、**不開 function-calling**＝結構性保證不會翻某筆
    # 記錄原文吐 📁 當答案。講錯統計仍有 §1.42/§1.97 的 _say 守門兜底；LLM 失敗＝確定性模板（誠實開場＋統計行）。
    if route.kind == "habit_inventory":
        _hf = habits.habit_facts(state, (data or {}).get("records"), now_utc.timestamp(), tz,
                                 daily_first=getattr(cfg, "habit_obs_fix_enabled", False))
        msg = (coach.voice_habit_inventory(text, _hf, state.convo_history) if (coach and coach.enabled) else None) \
            or ("我手上真的記到的觀察是這些——\n" + (_hf.split("\n", 1)[1] if "\n" in _hf else _hf))
        _say(client, msg)
        _remember(state, "user", text, ts=_user_msg_ts)
        _remember(state, "model", msg)
        if not cfg.dry_run:
            state.save()
        return

    # ⏱ 對話時間問題（我睡多久／我們多久沒聊／你剛說的多久前）→ 走**純對話、不開工具**，用帶時間標籤的
    # 對話脈絡＋一條算好的「上次到現在隔多久」事實回答。必須在 function-calling 之前攔下，否則「睡多久」會被
    # 抓去 get_current_time 回成鐘錶時間（截圖的毛病：要被提醒才會用對話時間軸）。
    if route.kind == "convo_time":
        # 依問法選用『已算好的事實』：問『我上一句多久前』→ _last_bot_turn_gap_fact（最近一則 bot 訊息距今）；
        # 其餘『多久沒聊／我睡多久』→ _session_gap_text（最大沉默段）。兩者都是程式用牆鐘量距算好（與記點同源），
        # LLM 只照講、不自推。算不出（無對應訊息）→ 走誠實 unknown，不讓 LLM 從現有標籤硬掰、指錯對象。
        # ⏱ 問的是「絕對鐘點（幾點幾分／對話的時間點）」嗎？→ 另算一條鐘點事實（convo_history 的 ts→本地 HH:MM）；
        # 過去這類問句漏到 function-calling 被誤抓 records_in_time_range 回「那段你沒有記寫」（把對話時間當記寫資料查）。
        abs_q = selfstate.is_convo_clock_question(text)
        if selfstate.is_bot_turn_time_question(text):
            gapfact = _last_bot_turn_gap_fact(state.convo_history, convo_now)
        else:
            gapfact = _session_gap_text(state.convo_history, convo_now)
        clockfact = _convo_clock_fact(state.convo_history, tz, convo_now) if abs_q else ""
        if coach and coach.enabled:
            brief = coachmod.build_memory_brief(data, snap, tz, now=now_utc, query=text,
                                                focus=referent.focus_dict(ref),
                                                selfacts=referent.self_acts_text(ref, picker=(_phrase_picker(state) if getattr(cfg, "phrase_anti_reuse_enabled", False) else None)),  # 🎨 §1.22 線句接池（關＝None＝原句）
                                                learned=learned,
                                                hard_now_anchor=getattr(cfg, "global_time_anchor", True))  # 🕐 §0.82 全域硬時間錨
            # 算不出讀數 → 給 LLM 一條誠實 unknown 授權（不得估、不得從粗標籤自推指錯對象）。
            grounding = "\n".join([f for f in (clockfact, gapfact) if f]) or (
                "（沒有可接地的對話時間讀數：請誠實說「我不太確定那是多久前／我手邊沒留到那麼精確的鐘點」，"
                "別自己從訊息標籤推算、也別指錯對象。）")
            # 消解『時間別念出來』與『照講已算好讀數』的張力：用 mood_hint 通道補上對應指引（相對量照 CONVO_TIME_HINT；
            # 問絕對鐘點再加 CONVO_CLOCK_HINT）。不新增 reply 參數，沿用把事實放進 brief、測試 assertIn 兜底。
            ct_mhint = (mhint + "\n" + persona.CONVO_TIME_HINT
                        + (("\n" + persona.CONVO_CLOCK_HINT) if abs_q else "")).strip()
            try:
                msg = coach.reply(text, grounding + "\n" + brief,
                                  state.convo_history, mood_hint=ct_mhint, now_ts=convo_now)
            except gemini.GeminiError as e:
                print(f"[chat] 對話時間回覆失敗：{e}")
                msg = "（我這邊卡了一下，等等再問我一次？）"
        else:
            msg = ((clockfact or gapfact).strip("（）") if (clockfact or gapfact)
                   else "我手上還沒有我們的對話時間紀錄。")
        _say(client, msg)
        _remember(state, "user", text, ts=_user_msg_ts)
        _remember(state, "model", msg)
        if not cfg.dry_run:
            state.save()
        return

    # 其餘一律走 function-calling：LLM 只負責「挑工具＋給參數」，事實由 datatools 用真實資料算；
    # 純聊天才回人話。這層讓「上面 fast-path 沒命中」的事實問法也不會被 LLM 捏造（系統性根治）。
    ctx = datatools.ToolCtx(data, snap, tz, now_utc, meter=coach.meter, state=state)
    # 🕰️ 會話節奏（程式算好的接地事實）：讓所有對話路徑都覺察「這次是隔一陣回來 vs 同段延續」→ 幫助判讀意圖。
    # 只進這條通用 brief、**不進** convo_time 路徑的 brief（那裡已有 _session_gap_text／最久沉默，避免兩個數字打架）。
    # now_ts 用 convo_now（與既有對話時間事實同一牆鐘源）。關旗標＝session_struct='' → 逐位元同現狀。
    ss = (sessionize(state.convo_history, convo_now, getattr(cfg, "convo_session_gap_sec", 3600))
          if getattr(cfg, "convo_session_struct_enabled", False) else "")
    brief = coachmod.build_memory_brief(data, snap, tz, now=now_utc, query=text,
                                        focus=referent.focus_dict(ref),
                                        selfacts=referent.self_acts_text(ref, picker=(_phrase_picker(state) if getattr(cfg, "phrase_anti_reuse_enabled", False) else None)),  # 🎨 §1.22 線句接池（關＝None＝原句）
                                        learned=learned, session_struct=ss,
                                        grounding_note=getattr(cfg, "grounding_note_enabled", True),  # 🛡️ 接地三區塊尾端附統一守則（內部用、別主動當話題）；旗標關＝不附＝同現狀
                                        hard_now_anchor=getattr(cfg, "global_time_anchor", True))  # 🕐 §0.82 全域硬時間錨（此刻真的幾點，別猜）
    # 👍「你剛讚我哪則」已由 referent.self_acts_text(ref.liked) 統一帶進 brief（單一自我模型出口）——
    #   不再另外 prepend _liked_fact（那是同義重複）；明問「我點哪筆讚」仍走上面的 _asks_which_liked 接地路徑。
    # 自我在場：這句在談 bot 自己（自我指涉）、或剛談過自己還在窗內 → 切「守住自己、不逃避、不拐回你記寫」的語氣。
    react = getattr(state, "last_reaction", None)       # 剛收到的貼圖情緒 → 染這則回覆語氣（短窗內）
    if react and (now_utc.timestamp() - (react.get("ts") or 0)) < REACTION_WINDOW_SEC:
        mhint = (mhint + reaction.tone_hint(react.get("valence"))).strip()

    # 🪞 自我在場＋**延續感**：① 明講在談我（`is_about_self`），或 ② 剛談過我還在窗內、而這句又**不像在查資料**
    # → 走純對話（自我在場語氣、**不碰工具、不吐記寫報表**）。截圖根因：自我對話延續中問「你會分串？」沒被認成
    # 「還在說我」→ 落工具被抓去吐 📊 報表。真正的資料問句（含記寫/筆數/漏斗…）即使在窗內也照走工具（`looks_like_data_question`）。
    in_self_window = ref.in_self_window               # 🪞 單一真相（referent 算好）：此刻是不是在談我
    about_self = selfref.is_about_self(text)

    # ✒️ 在談「你的訊息/聯想為什麼有 markdown、是不是純文字」＝問 bot 自己訊息的**格式**——要強制走純對話＋格式接地
    # （別掉進 function-calling／泛用聊天而 confabulate 出一套「內部運作格式」、暴露實作、破在場感）。直接問即認；
    # format 窗內的省略追問（如「在你的自我聯想內容裡有嗎」本身不帶格式詞）也接住。
    fmt_recent = (convo_now - (getattr(state, "format_topic_ts", 0) or 0)) < SELF_TOPIC_WINDOW_SEC
    fmt_talk = getattr(cfg, "self_format_enabled", True) and selfstate.is_format_question(text, recent=fmt_recent)

    # 🗣️ 純附和/確認（route.kind=='smalltalk'，是啊/對/嗯/沒錯）→ 純對話接話，**不進 function-calling**。
    # 截圖 bug：bot 認錯後使用者回「是啊」，落工具被 LLM 誤抓 overall_stats 又吐一堆數字。承接前文、延續自我在場語氣即可。
    if route.kind == "smalltalk":
        try:
            voice = coach.reply(text, brief, state.convo_history, mood_hint=mhint,
                                now_ts=convo_now, self_presence=in_self_window,
                                extra_system=_join_extra(_skill_extra(state, cfg, "smalltalk", text, convo_now),   # 🧑‍🏫 同情境召回已學做法
                                                         _fact_card(state, cfg, data, snap, now_utc, tz),   # 🪪 §1.61 此刻事實卡（常駐、單一真相）
                                                         _teaching_guard_hint(state, cfg, text),   # 🧑‍🏫 §0.59 別謊稱「記下來了」
                                                         _skill_accountability_extra(state, cfg, text, convo_now),   # 🧾 §0.60 問做法/約定→只准照真帳本答
                                                         _promise_guard_hint(state, cfg, text, now_utc, tz),   # 🤝 §0.61 未入帳的計時請求→別空口答應（§1.11 帶 now/tz＝結構閘問 temporal）
                                                         _promise_cant_hint(state, cfg, text),   # 🤝 §0.64 超出能力的約定→誠實拒絕+說明
                                                         _sticker_concept_hint(state, cfg, text)))   # 🎴 §0.68 貼圖≠emoji、別用符號假裝貼圖
        except gemini.GeminiError as e:
            print(f"[chat] 附和回覆失敗：{e}")
            client.send("（嗯，我在。）")
            return
        voice = _burst_reply_repair(update, voice, coach, cfg)
        _say(client, voice)
        _maybe_sticker(client, state, cfg, now_utc.timestamp())
        _remember(state, "user", text, ts=_user_msg_ts)
        _remember(state, "model", voice)
        _maybe_propose_skill(client, state, cfg, coach, text, "smalltalk", state.convo_history, convo_now)  # 🧑‍🏫 凝出共識→提議學成做法
        if not cfg.dry_run:
            state.save()
        return

    # 🫧 展開/釐清「你說的那點」：對方要 bot 把**自己剛說過**的點講清楚（不是查資料）。截圖根因：問「說說看你說的
    # 共同性是什麼」卻被當成資料查詢、去列〔情緒困擾〕清單答非所問。改走純對話 reply（含對話史＝接得到自己前文），
    # 附 ELABORATE_PRIOR_HINT：接著前文講、絕不查資料/列清單；不確定他指哪句就**反問確認意圖**（＝意圖確認）。
    if route.kind == "elaborate_prior":
        try:
            voice = coach.reply(text, brief, state.convo_history, mood_hint=mhint, now_ts=convo_now,
                                self_presence=in_self_window,
                                extra_system=_join_extra(persona.ELABORATE_PRIOR_HINT,
                                                         _skill_extra(state, cfg, "elaborate_prior", text, convo_now),
                                                         _fact_card(state, cfg, data, snap, now_utc, tz),   # 🪪 §1.61 此刻事實卡（常駐、單一真相）
                                                         _teaching_guard_hint(state, cfg, text),   # 🧑‍🏫 §0.59 別謊稱「記下來了」
                                                         _skill_accountability_extra(state, cfg, text, convo_now),   # 🧾 §0.60 問做法/約定→照真帳本
                                                         _promise_guard_hint(state, cfg, text, now_utc, tz),   # 🤝 §0.61 未入帳的計時請求→別空口答應（§1.11 帶 now/tz＝結構閘問 temporal）
                                                         _promise_cant_hint(state, cfg, text),   # 🤝 §0.64 超出能力的約定→誠實拒絕+說明
                                                         _sticker_concept_hint(state, cfg, text),   # 🎴 §0.68 貼圖≠emoji、別用符號假裝貼圖
                                                         _anti_repeat_hint(state, text, convo_now, cfg),
                                                         _nudge_deliver_hint(state, cfg, convo_now),
                                                         _routine_voice_hint(state, cfg)))   # 📈 §1.96 作息要講得像人、不像報表   # 🫸 §1.87 卡住＝現在就做、別踢球回去
        except gemini.GeminiError as e:
            print(f"[chat] 展開前文回覆失敗：{e}")
            client.send("（你是指我剛剛說的哪個？我不太確定，再講清楚一點？）")
            return
        voice = _burst_reply_repair(update, voice, coach, cfg)
        _say(client, voice)
        _maybe_sticker(client, state, cfg, now_utc.timestamp())
        _remember(state, "user", text, ts=_user_msg_ts)
        _remember(state, "model", voice)
        _maybe_propose_skill(client, state, cfg, coach, text, "elaborate_prior", state.convo_history, convo_now)
        if not cfg.dry_run:
            state.save()
        return

    # 🪞📊 評斷我（route.kind=='self_appraisal'）：「我算早起嗎／我是不是很懶／我這樣算正常嗎」＝請 bot 拿
    # **我的記寫節奏（brief【時間感】rhythm_part）＋此刻時間（now_local/時段）＋對我的了解**，下一個 grounded 的
    # 人味判斷。截圖根因：這句漏到 fact_or_chat → coach.ask function-calling → LLM 誤抓 records_in_time_range 吐 📁
    # 「今天那段你沒有記寫」、答非所問還重複犯。改走純對話 reply（**不碰工具、不列記寫、不吐報表**），附 APPRAISE_ME_HINT；
    # brief 已含 rhythm+時段，不需新算事實。self_presence=False（談的是「我（使用者）」、非 bot 自己）。
    if route.kind == "self_appraisal":
        try:
            voice = coach.reply(text, brief, state.convo_history, mood_hint=mhint, now_ts=convo_now,
                                self_presence=False,
                                extra_system=_join_extra(persona.APPRAISE_ME_HINT,
                                                         _habit_ground_hint(state, cfg, text, (data or {}).get("records"), now_utc.timestamp(), tz),
                                                         _skill_extra(state, cfg, "self_appraisal", text, convo_now),
                                                         _fact_card(state, cfg, data, snap, now_utc, tz),   # 🪪 §1.61 此刻事實卡（常駐、單一真相）
                                                         _teaching_guard_hint(state, cfg, text),   # 🧑‍🏫 §0.59 別謊稱「記下來了」
                                                         _skill_accountability_extra(state, cfg, text, convo_now),   # 🧾 §0.60 問做法/約定→照真帳本
                                                         _promise_guard_hint(state, cfg, text, now_utc, tz),   # 🤝 §0.61 未入帳的計時請求→別空口答應（§1.11 帶 now/tz＝結構閘問 temporal）
                                                         _promise_cant_hint(state, cfg, text),   # 🤝 §0.64 超出能力的約定→誠實拒絕+說明
                                                         _sticker_concept_hint(state, cfg, text),   # 🎴 §0.68 貼圖≠emoji、別用符號假裝貼圖
                                                         _anti_repeat_hint(state, text, convo_now, cfg),
                                                         _nudge_deliver_hint(state, cfg, convo_now),
                                                         _routine_voice_hint(state, cfg)))   # 📈 §1.96 作息要講得像人、不像報表   # 🫸 §1.87 卡住＝現在就做、別踢球回去
        except gemini.GeminiError as e:
            print(f"[chat] 自我評斷回覆失敗：{e}")
            client.send("（這我得想一下——以你平常的節奏看，等我捋捋。）")
            return
        voice = _burst_reply_repair(update, voice, coach, cfg)
        _say(client, voice)
        _maybe_sticker(client, state, cfg, now_utc.timestamp())
        _remember(state, "user", text, ts=_user_msg_ts)
        _remember(state, "model", voice)
        _maybe_propose_skill(client, state, cfg, coach, text, "self_appraisal", state.convo_history, convo_now)
        if not cfg.dry_run:
            state.save()
        return

    # 🔖 引用我說過的話 + 泛用 fallback 問句（為什麼/這什麼意思）：mhint 已帶被引用那句當 anchor。這裡把它從
    # function-calling（會誤抓 records_in_time_range 等工具）救到純對話 reply＝接地在那句話上回答/展開（不查資料）。
    # 明確的資料問句（列清單/筆數…）仍讓它照常走工具。self_presence=True（在談我自己剛說過的話）。
    if qself and route.kind == "fact_or_chat" and not selfstate.looks_like_data_question(text):
        try:
            voice = coach.reply(text, brief, state.convo_history, mood_hint=mhint,
                                now_ts=convo_now, self_presence=True,
                                extra_system=_join_extra(_skill_extra(state, cfg, "fact_or_chat", text, convo_now),
                                                         _fact_card(state, cfg, data, snap, now_utc, tz),   # 🪪 §1.61 此刻事實卡（常駐、單一真相）
                                                         _self_skill_extra(state, cfg, convo_now),   # 🌀 §0.57 內在因應（此路徑亦自我在場）
                                                         _teaching_guard_hint(state, cfg, text),   # 🧑‍🏫 §0.59 別謊稱「記下來了」
                                                         _skill_accountability_extra(state, cfg, text, convo_now),   # 🧾 §0.60 問做法/約定→照真帳本
                                                         _promise_guard_hint(state, cfg, text, now_utc, tz),   # 🤝 §0.61 未入帳的計時請求→別空口答應（§1.11 帶 now/tz＝結構閘問 temporal）
                                                         _promise_cant_hint(state, cfg, text),   # 🤝 §0.64 超出能力的約定→誠實拒絕+說明
                                                         _sticker_concept_hint(state, cfg, text),   # 🎴 §0.68 貼圖≠emoji、別用符號假裝貼圖
                                                         _anti_repeat_hint(state, text, convo_now, cfg),
                                                         _nudge_deliver_hint(state, cfg, convo_now),
                                                         _routine_voice_hint(state, cfg)))   # 📈 §1.96 作息要講得像人、不像報表   # 🫸 §1.87 卡住＝現在就做、別踢球回去
        except gemini.GeminiError as e:
            print(f"[chat] 引用回覆失敗：{e}")
            client.send("（你是指我剛說的那句嗎？我想一下怎麼接。）")
            return
        voice = _burst_reply_repair(update, voice, coach, cfg)
        _say(client, voice)
        _maybe_sticker(client, state, cfg, now_utc.timestamp())
        _remember(state, "user", text, ts=_user_msg_ts)
        _remember(state, "model", voice)
        _maybe_propose_skill(client, state, cfg, coach, text, "fact_or_chat", state.convo_history, convo_now)
        if not cfg.dry_run:
            state.save()
        return

    if about_self or fmt_talk or (in_self_window and not selfstate.looks_like_data_question(text)):
        # 🧵 對話線連貫：續窗本來只在「這句**真的**還在說我」(about_self)時刷新，飄走的閒聊（窗內但已不談我，如
        # 「那天氣呢」）讓窗自然衰減。但截圖根因＝同一條情緒/關係線的**延續輪**（如「我為什麼這麼說你」→「你知道
        # 發生的時間點」）雖仍朝向我、卻非 about_self，故不刷新→窗在數分鐘內衰減過期→下一輪掉進 fact_or_chat
        # function-calling 吐 📁 記寫，與前面的純對話/情緒基礎斷裂。修：旗標開時，**仍朝向我（含你/妳）的同線延續**
        # 也刷新窗（共用純對話基礎、不中途轉去查記寫）；真正離題（無你/妳）仍自然衰減、明確資料問句仍走工具（上面 gate）。
        if about_self or fmt_talk or (getattr(cfg, "thread_sticky_enabled", False) and ("你" in text or "妳" in text)):
            state.self_topic_ts = now_utc.timestamp()
        if fmt_talk:                                       # ✒️ 開/續格式窗 → 省略追問（…裡有嗎）也接得住；附格式接地
            state.format_topic_ts = convo_now              # 用牆鐘（與 fmt_recent 讀的同一把鐘＝對話時間軸）
        # 🪞 自我在場純對話也套「去台詞（換句話）＋重複×心情長脾氣」調制（_self_voice_mod，與所有 self_* 路由一致）——
        #    這條路徑**原本獨缺**，導致「真的嗎」連發時 bot 逐字重複同一段自我說明、毫無變化也無情緒（截圖）。kind 固定用
        #    "self_presence"＝把「一直 challenge 我這個存在」累進同一個耐性計數 → 連發幾次後 self_fatigue 升級語氣（又問啦😅→
        #    一點點不耐）＋vary_hint 逼它換句話。旗標關＝不套 mod、不記 opener＝逐位元同現狀。
        self_mod = (_self_voice_mod(state, "self_presence", mood_v, convo_now, cfg)
                    if getattr(cfg, "self_presence_vary_enabled", True) else "")
        try:
            voice = coach.reply(text, brief, state.convo_history, mood_hint=mhint,
                                now_ts=convo_now, self_presence=True,
                                extra_system=_join_extra((persona.FORMAT_HINT if fmt_talk else ""),
                                                         _skill_extra(state, cfg, route.kind, text, convo_now),
                                                         _fact_card(state, cfg, data, snap, now_utc, tz),   # 🪪 §1.61 此刻事實卡（常駐、單一真相）
                                                         _self_skill_extra(state, cfg, convo_now),   # 🌀 §0.57 內在因應
                                                         _teaching_guard_hint(state, cfg, text),   # 🧑‍🏫 §0.59 別謊稱「記下來了」
                                                         _skill_accountability_extra(state, cfg, text, convo_now),   # 🧾 §0.60 問做法/約定→照真帳本
                                                         _promise_guard_hint(state, cfg, text, now_utc, tz),   # 🤝 §0.61 未入帳的計時請求→別空口答應（§1.11 帶 now/tz＝結構閘問 temporal）
                                                         _promise_cant_hint(state, cfg, text),   # 🤝 §0.64 超出能力的約定→誠實拒絕+說明
                                                         _sticker_concept_hint(state, cfg, text),   # 🎴 §0.68 貼圖≠emoji、別用符號假裝貼圖
                                                         _habit_ground_hint(state, cfg, text, (data or {}).get("records"), now_utc.timestamp(), tz),
                                                         _mood_coord_hint(state, cfg, text, now_utc.timestamp(), tz),
                                                               _worldline_followup_hint(state, cfg, text, now_utc.timestamp()),   # 🌐 §2.16 追問剛帶回的外部說法→照 ledger 講、不重編   # 🧭 §1.45 自我在場窗內問座標數據（你自己內在的…）→ 也給真數字
                                                         _feeling_probe_hint(state, cfg, text),   # 🗜️ §1.51 探心意（羨慕我嗎）→ 先答後問、不支吾
                                                         _clarify_recap_hint(state, cfg, text, now_utc.timestamp()),   # 🗣️ §1.56 看不懂→照真順序重述、不揣測不倒序
                                                         _today_write_hint(state, cfg, text),   # 🕐 §1.60 問今天做了什麼→真實記寫數據
                                                         self_mod,
                                                         _anti_repeat_hint(state, text, convo_now, cfg),
                                                         _nudge_deliver_hint(state, cfg, convo_now),
                                                         _routine_voice_hint(state, cfg)))   # 📈 §1.96 作息要講得像人、不像報表   # 🫸 §1.87 卡住＝現在就做、別踢球回去
        except gemini.GeminiError as e:
            print(f"[chat] 自我在場回覆失敗：{e}")
            client.send("（我這邊想事情卡住了，等等再問我一次？）")
            return
        voice = _burst_reply_repair(update, voice, coach, cfg)
        _say(client, voice)
        _maybe_sticker(client, state, cfg, now_utc.timestamp())   # 🎴 偶爾補個表情貼（強化互動感）
        _remember(state, "user", text, ts=_user_msg_ts)
        _remember(state, "model", voice)
        if getattr(cfg, "self_presence_vary_enabled", True):
            _record_self_opener(state, voice, convo_now, cfg)     # 🪞 記下開頭→下輪換句話（先前獨缺＝逐字重複根因）
        _maybe_propose_skill(client, state, cfg, coach, text, route.kind, state.convo_history, convo_now)
        if not cfg.dry_run:
            state.save()
        return

    try:
        # 自我在場語氣只在「還在談我、又不是查資料」時帶進 function-calling；**資料問句一律關掉**——
        # 否則「完整的學習歷程是哪些」這種明確列表問句，會被自我在場提示帶偏成「談我自己這段體驗」的回避
        # （截圖毛病：問使用者的歷程，bot 卻講自己在研發寫下來學習…形成某個形狀），而不去叫 list_funnel_topics。
        ask_self_presence = in_self_window and not selfstate.looks_like_data_question(text)
        # 🚪 非記寫資料問句別觸發 Drive 證據工具：只有明確查記寫（looks_like_data_question / is_explicit_records_intent）
        # 才給 coach.ask 完整工具表，否則只給非證據工具表（LLM 不可達 records_in_time_range 等）；含時間/事件/meta 詞但非
        # 資料的句子（剛剛有人指責你／對話的時間點…）即使漏到這裡也不會被誤抓去回 📂「那段你沒有記寫」（根因 1，順帶兜底根因 2/4）。
        # OR 串聯只放寬不收緊；旗標關＝allow_evidence 恆 True＝逐位元同現狀（一鍵退路）。
        allow_evidence = (not getattr(cfg, "evidence_gate_enabled", True)) \
            or selfstate.looks_like_data_question(text) or selfstate.is_explicit_records_intent(text)
        # 🚪 §0.56：但若這句其實是問「你為什麼在意這則／它特別嗎／你怎麼看」＝要看法而非要資料 → 收回證據閘
        # （即使含「記寫」等 cue 名詞），改純看法回應、不倒整份 📂 清單佔版面（證據資料只在明確要資料時才出）。
        # **只在主閘開著時作用**：EVIDENCE_GATE_ENABLED=0（一鍵退路）時 allow_evidence 須恆 True、不被本收窄動到（byte-identical）。
        if getattr(cfg, "evidence_gate_enabled", True) and allow_evidence \
                and getattr(cfg, "evidence_opinion_suppress", True) \
                and selfstate.is_opinion_about_entry(text):
            allow_evidence = False
        # 🧭 §1.45：「內在的數據」不是查記寫/花費——「數據」∈ _DATA_CUES 會誤開證據工具（截圖 11:08 被 LLM 抓去
        # api_cost 吐 💸 花費報表的根因）。命中座標數據問句 → 收回證據閘（座標事實已由 _mood_coord_hint 注入）。
        # 旗標關＝不收窄＝逐位元同現狀。
        if allow_evidence and getattr(cfg, "mood_coord_report_enabled", False) \
                and _mood_data_hit(state, cfg, text, now_utc.timestamp()):   # §1.47：寬偵測/催促承接同步收回
            allow_evidence = False
        kind, data, voice = coach.ask(text, brief, ctx, state.convo_history, mood_hint=mhint,
                                      self_presence=ask_self_presence, now_ts=convo_now, evidence_tools=allow_evidence,
                                      extra_system=_join_extra(_skill_extra(state, cfg, "fact_or_chat", text, convo_now),   # 🧑‍🏫 §0.76 審計（confirmed SEVERE）：主聊天 lane（function-calling）原本**完全不注入已學做法**——always/topic 技能在多數真實輪次靜默失效（學是在這 lane 學的、卻永遠不在這 lane 生效）。補上＝同 smalltalk/自我在場 lane
                                                               _fact_card(state, cfg, data, snap, now_utc, tz),   # 🪪 §1.61 此刻事實卡（常駐、單一真相）
                                                               _teaching_guard_hint(state, cfg, text),   # 🧑‍🏫 §0.59 別謊稱「記下來了」
                                                               _skill_accountability_extra(state, cfg, text, convo_now),   # 🧾 §0.60 問做法/約定→照真帳本
                                                         _promise_guard_hint(state, cfg, text, now_utc, tz),   # 🤝 §0.61 未入帳的計時請求→別空口答應（§1.11 帶 now/tz＝結構閘問 temporal）
                                                         _promise_cant_hint(state, cfg, text),   # 🤝 §0.64 超出能力的約定→誠實拒絕+說明
                                                               _sticker_concept_hint(state, cfg, text),   # 🎴 §0.68 貼圖≠emoji、別用符號假裝貼圖
                                                               _recall_ground_hint(state, cfg, text),   # 🧭 §1.36 問記寫內容/原因的回想→強接地：只准引用原文、絕不補聽起來合理的原因
                                                               _selfshare_reason_hint(state, cfg, text, now_utc.timestamp()),   # 🍃 §1.38 追問剛主動自陳的「為什麼」→ 注入真實理由接地（別漂到貼圖等沒做過的事）
                                                               _wake_boundary_hint(state, cfg),   # 🌅 §1.39 bot 重生醒來時：睡醒/悶悶的是你自己的、別投射成使用者
                                                               _habit_ground_hint(state, cfg, text, (data or {}).get("records"), now_utc.timestamp(), tz),
                                                               _self_feel_brevity_hint(state, cfg, now_utc.timestamp()),   # 🗜️ §1.43 兩小時內才自陳過→類似感覺一兩句點到重點、別鋪陳
                                                               _mood_coord_hint(state, cfg, text, now_utc.timestamp(), tz),
                                                               _worldline_followup_hint(state, cfg, text, now_utc.timestamp()),   # 🌐 §2.16 追問剛帶回的外部說法→照 ledger 講、不重編   # 🧭 §1.45 問內在座標數據→注入程式讀的 V/A＋軌跡（絕不說沒辦法報數字）
                                                               _feeling_probe_hint(state, cfg, text),   # 🗜️ §1.51 探心意（羨慕我嗎）→ 先答後問、不支吾
                                                               _clarify_recap_hint(state, cfg, text, now_utc.timestamp()),   # 🗣️ §1.56 看不懂→照真順序重述、不揣測不倒序
                                                               _today_write_hint(state, cfg, text),   # 🕐 §1.60 問今天做了什麼→真實記寫數據
                                                               _anti_repeat_hint(state, text, convo_now, cfg),
                                                         _nudge_deliver_hint(state, cfg, convo_now),
                                                         _routine_voice_hint(state, cfg)))   # 📈 §1.96 作息要講得像人、不像報表   # 🫸 §1.87 卡住＝現在就做、別踢球回去
    except gemini.GeminiError as e:
        print(f"[chat] ask 失敗：{e}")
        client.send("（我這邊想事情卡住了，等等再問我一次？）")
        return
    _burst_data_lead = bool(data and voice and _burst_one_answer)
    if data and not _burst_data_lead:  # 一般證據維持整塊；multi-message 的 data+voice 則在下一段原子合成一則
        client.send(data)
    if voice:                      # 對話：有時間感、像個存在的回應 → 一串一串送（真人手感）
        voice = _burst_reply_repair(update, voice, coach, cfg)
        _say(client, voice, wire_lead=((data.rstrip() + "\n\n") if _burst_data_lead else ""))
        _maybe_sticker(client, state, cfg, now_utc.timestamp())   # 🎴 偶爾補個表情貼（只在有人話回應時，不在純資料後）
    if voice or data:              # 統一記憶：user 記一次；事實答覆只記精簡首行（帶指涉物，不塞整串清單）
        _remember(state, "user", text, ts=_user_msg_ts)
        _remember(state, "model", voice or _condense_fact(data))
    if voice:                      # 🧑‍🏫 純對話輪才可能凝出『以後該怎麼回應』的共識 → 提議學成做法（純資料答覆不提議）
        _maybe_propose_skill(client, state, cfg, coach, text, route.kind, state.convo_history, convo_now)
    if not cfg.dry_run:            # 🧭 此路徑（fact_or_chat/function-calling，最常走）原本未存檔 → 補存，
        state.save()              # 讓 _observe_intent 寫的 intent_log/last_questioned_ts 跨重生持久（與其他路徑一致）


class _BurstReplyCapture:
    """跨 route burst 的送出交易緩衝。

    每個已有 handler 仍經過自己的事實/誠實守門，但 send 先不碰 Telegram；等所有
    不同意圖都完成後，外層再原子送成一個邏輯回覆。回 bool 而非假 message_id，
    避免把虛構 id 寫入 reaction/topic 對應。
    """
    @property
    def evidence_blocked(self):
        return getattr(self._base, "evidence_blocked", False) is True

    def begin_voice(self):
        fn = getattr(self._base, "begin_voice", None)
        if callable(fn):
            fn()

    def validate_voice(self, text):
        fn = getattr(self._base, "validate_voice", None)
        return fn(text) if callable(fn) else text

    def certify(self, text):
        fn = getattr(self._base, "certify", None)
        if callable(fn):
            fn(text)

    def approve_bubbles(self, bubbles):
        pass  # Speculative sends never create transport approval or evidence.

    def __init__(self, base):
        self._base = base
        self.events = []
        self._next_delivery_meta = None
        self._deferred_actions = []
        self._deferred_self_promises = []
        self._committed_proactive = []
        self.speculative_capture = True
        self.dry_run = True                              # 緩衝不做 typing sleep
        self._interrupt = None                           # 內部渲染時不另開巢狀插話輪

    def capture_delivery_meta(self, voice_fragment="", mood_contract=None):
        """由 `_say` 在緊接著的 send 前標記這塊真正承載的人話／座標契約。"""
        self._next_delivery_meta = {
            "voice": str(voice_fragment or ""),
            "mood_contract": (copy.deepcopy(mood_contract)
                              if isinstance(mood_contract, dict) else None),
        }

    def send(self, text):
        clean = notifier.strip_markdown(str(text or "")).strip()
        meta, self._next_delivery_meta = self._next_delivery_meta, None
        if clean:
            self.events.append(("text", {
                "text": clean,
                "voice": str((meta or {}).get("voice") or "").strip(),
                "mood_contract": (meta or {}).get("mood_contract"),
                "invalidated": False,
            }))
        return True

    def defer_delivery_action(self, commit, discard=None, kind="delivery", required=None,
                              subject=None, cancel=None):
        """把「send 成功後才可做」的 state mutation 綁到剛 capture 的 actual payload。

        render 階段仍可先把 promise 標成 provisional，讓同一 burst 後段不重複觸發；final
        wire 不含完整 payload 時走 discard 回復，含完整 payload 才在 clone 上 commit。
        """
        if required is None:
            required = ""
            for event_kind, value in reversed(self.events):
                if event_kind == "text":
                    required = str(value.get("text") if isinstance(value, dict) else value or "").strip()
                    break
        self._deferred_actions.append({"required": required, "commit": commit,
                                       "discard": discard, "cancel": cancel,
                                       "kind": kind, "subject": subject})

    def has_deferred_action(self, kind=None):
        return any(kind is None or item.get("kind") == kind for item in self._deferred_actions)

    def cancel_deferred_actions(self, kind=None, subject=None, drop_text=False):
        """後來的使用者意圖取消尚未交付的動作。

        subject 用 object identity 精確對到同一筆 promise；不能因「取消最近的約定」
        就連另一筆到期約定也一起吞掉。drop_text 同時拿掉尚未送出的履約句，
        避免同一個 final wire 同時說「我來守約了」與「我取消了」。
        """
        removed, kept = [], []
        for item in self._deferred_actions:
            same_kind = kind is None or item.get("kind") == kind
            same_subject = subject is None or item.get("subject") is subject
            if same_kind and same_subject:
                removed.append(item)
            else:
                kept.append(item)
        self._deferred_actions = kept
        if not removed:
            return False
        for item in removed:
            fn = item.get("cancel")
            if callable(fn):
                fn("")
        if drop_text:
            required = [str(item.get("required") or "").strip() for item in removed]
            required = [value for value in required if value]
            if required:
                for event_kind, value in self.events:
                    payload = str(value.get("text") if isinstance(value, dict) else value or "")
                    if event_kind == "text" and any(token in payload for token in required):
                        # events 必須 append-only：part 以穩定 index 記自己的 capture range。
                        # 直接刪 list 會使後面 start 錯位，也會讓已投影的 segment
                        # 繼續留著被取消的句子。只標記，最後統一投影。
                        if isinstance(value, dict):
                            value["invalidated"] = True
        return True

    def defer_self_promise_capture(self, text, turn_meta):
        self._deferred_self_promises.append({
            "text": notifier.strip_markdown(str(text or "")).strip(),
            "turn_meta": dict(turn_meta or {}),
        })

    def defer_proactive_delivery(self, state, prefix, topic, text, track_initiative=True):
        """延後建立主動話頭與 message-id 對應；capture=True 不等於 Telegram 送達。"""
        expected = notifier.strip_markdown(str(text or "")).strip()
        if not expected or state is None or not topic:
            return

        # `_say` 在這個 hook 前已把本次所有泡泡 append 到 events。記住 expected 是 capture
        # 串中的第幾次 occurrence，才能區分「前面普通回覆也剛好是嗯。」與真正屬於 topic 的後一顆。
        # whitespace 只是在 capture/bounded/paced 三層間的呈現接縫，不是 topic provenance。
        def key(value):
            return "".join(char for char in str(value or "") if not char.isspace())

        captured_events = []
        for event_index, (event_kind, value) in enumerate(self.events):
            if event_kind != "text":
                continue
            if isinstance(value, dict):
                if value.get("invalidated"):
                    continue
                captured_events.append((event_index, str(value.get("text") or "")))
            else:
                captured_events.append((event_index, str(value or "")))
        captured = [value for _event_index, value in captured_events]
        haystack, needle = key("".join(captured)), key(expected)
        occurrence, required_wire, event_indexes = None, expected, []
        if needle and haystack.endswith(needle):
            cursor = count = 0
            while True:
                found = haystack.find(needle, cursor)
                if found < 0:
                    break
                count += 1
                cursor = found + len(needle)
            occurrence = count or None
            # `_burst_capture_segment` 以換行保留每顆 speculative bubble；`_say` 的 expected
            # 則是無縫 join。重建這次 `_say` 對應的 event suffix，讓 final-wire 驗收不會只因
            # 呈現換行而誤判「沒送出」。找不到精確 suffix 時仍用舊 required，安全 fail-closed。
            suffix, suffix_key = [], ""
            for event_index, value in reversed(captured_events):
                candidate = key(value) + suffix_key
                if not needle.endswith(candidate):
                    break
                suffix.insert(0, (event_index, value))
                suffix_key = candidate
                if suffix_key == needle:
                    event_indexes = [index for index, _part in suffix]
                    required_wire = "\n".join(
                        part.strip() for _index, part in suffix if part.strip()).strip()
                    break

        def commit(_actual):
            self._committed_proactive.append({
                "state": state, "record": None, "topic": topic, "text": expected,
                "prefix": prefix, "track_initiative": track_initiative,
                "occurrence": occurrence, "event_indexes": event_indexes,
            })

        self.defer_delivery_action(commit, kind="initiative", required=required_wire)

    def bind_delivery_result(self, result, bubbles=None, wire=None, segments=None,
                             segment_ranges=None):
        """真 sendMessage 全部成功後，把一顆或多顆泡泡的真 message_id 綁回已驗收話頭。"""
        results = list(result) if isinstance(result, (list, tuple)) else [result]
        delivered = [str(value or "").strip() for value in (bubbles or [])]
        # 有逐泡文字時，優先以 capture event→segment→final wire 的來源投影定位 proactive span；
        # 不能用 substring set-like matching，否則兩條線共用「嗯。」時，同一 message_id 會跨題串線。
        # 一顆泡泡若橫跨兩個 topic，或 expected 已被 bounded wire 節錄而找不到，就 fail-closed：
        # 寧可那顆 reaction 不自動歸題，也不能把使用者的反應錯認成另一條線。
        item_results_by_index = None
        item_source_survived = None
        if delivered:
            def key(value):
                return "".join(char for char in str(value or "") if not char.isspace())

            logical = "".join(key(value) for value in delivered)
            bubble_spans, cursor = [], 0
            for bubble, message_id in zip(delivered, results):
                end = cursor + len(key(bubble))
                bubble_spans.append((cursor, end, message_id))
                cursor = end

            # `_bounded_burst_wire` 先依 segment 原文做 exact-dedup，再依序接線。這裡重播同一個
            # 保留判準並找出每個**確實完整存活** segment 的 final span；被去重／節錄／改寫者為 None。
            projection_supplied = (wire is not None and segments is not None
                                   and segment_ranges is not None
                                   and len(segments) == len(segment_ranges))
            retained_payloads, retained_seen = [], set()
            if projection_supplied:
                for segment in segments:
                    payload = str((segment or {}).get("text") if isinstance(segment, dict)
                                  else segment or "").strip()
                    if payload and payload not in retained_seen:
                        retained_seen.add(payload)
                        retained_payloads.append(payload)
            # 超限路徑會加 header／節錄 payload；substring 搜尋無法分辨節錄段裡的同字，
            # 因此只有 final wire 正好是 exact-dedup retained payloads 的 identity projection
            # 才建立來源 span。其他情況整體 fail-closed，不猜 reaction topic。
            projection_identity = (projection_supplied and key(wire) == logical
                                   and key(wire) == "".join(key(value)
                                                           for value in retained_payloads))
            segment_spans = [None] * len(segments or [])
            if projection_identity:
                seen_payloads, segment_cursor = set(), 0
                for segment_index, segment in enumerate(segments):
                    payload = str((segment or {}).get("text") if isinstance(segment, dict)
                                  else segment or "").strip()
                    if not payload or payload in seen_payloads:
                        continue
                    seen_payloads.add(payload)
                    payload_key = key(payload)
                    start = logical.find(payload_key, segment_cursor)
                    if start < 0:
                        continue
                    end = start + len(payload_key)
                    segment_spans[segment_index] = (start, end)
                    segment_cursor = end

            def provenance_span(item):
                event_indexes = list(item.get("event_indexes") or [])
                if not projection_identity or not event_indexes:
                    return None
                selected = set(event_indexes)
                matches = [index for index, (start, end) in enumerate(segment_ranges)
                           if all(start <= event_index < end for event_index in selected)]
                if len(matches) != 1:
                    return None
                segment_index = matches[0]
                segment_span = segment_spans[segment_index]
                if segment_span is None:                       # exact-dedup／節錄後來源已不存在
                    return None
                range_start, range_end = segment_ranges[segment_index]
                relative = 0
                selected_start = selected_end = None
                found = set()
                for event_index in range(range_start, range_end):
                    event_kind, value = self.events[event_index]
                    if event_kind != "text":
                        continue
                    if isinstance(value, dict):
                        if value.get("invalidated"):
                            continue
                        payload = str(value.get("text") or "").strip()
                    else:
                        payload = str(value or "").strip()
                    payload_key = key(payload)
                    if not payload_key:
                        continue
                    if event_index in selected:
                        if selected_start is None:
                            selected_start = relative
                        elif selected_end != relative:          # 非連續來源不能冒充單一 topic span
                            return None
                        selected_end = relative + len(payload_key)
                        found.add(event_index)
                    relative += len(payload_key)
                if found != selected or selected_start is None:
                    return None
                if relative != segment_span[1] - segment_span[0]:
                    return None                                 # final segment 不是原 event 的完整投影
                start = segment_span[0] + selected_start
                end = segment_span[0] + selected_end
                expected_key = "".join(
                    key((self.events[index][1] or {}).get("text")
                        if isinstance(self.events[index][1], dict) else self.events[index][1])
                    for index in event_indexes)
                if logical[start:end] != expected_key:
                    return None
                return start, end

            expected_spans, fallback_search_from = [], 0
            for item in self._committed_proactive:
                expected = key(item.get("text"))
                occurrence = item.get("occurrence")
                event_indexes = list(item.get("event_indexes") or [])
                span = provenance_span(item)
                # 真 capture 有 provenance 且呼叫端提供 final projection 時，投影失敗必須
                # fail-closed；絕不能再用相同文字去猜另一個 ordinary bubble。
                if projection_supplied and event_indexes:
                    expected_spans.append(span)
                    continue
                start = -1
                if expected and type(occurrence) is int and occurrence > 0:
                    nth_from = 0
                    for _index in range(occurrence):
                        start = logical.find(expected, nth_from)
                        if start < 0:
                            break
                        nth_from = start + len(expected)
                elif expected:                              # 舊／手工 capture 沒 provenance：僅依 item 順序
                    start = logical.find(expected, fallback_search_from)
                if start < 0:
                    expected_spans.append(None)
                    continue
                end = start + len(expected)
                expected_spans.append((start, end))
                if occurrence is None:
                    fallback_search_from = end
            owners = [[] for _ in bubble_spans]
            for item_index, span in enumerate(expected_spans):
                if span is None:
                    continue
                start, end = span
                for bubble_index, (b_start, b_end, _message_id) in enumerate(bubble_spans):
                    if b_start < end and start < b_end:
                        owners[bubble_index].append(item_index)
            item_results_by_index = [[] for _ in self._committed_proactive]
            item_source_survived = [span is not None for span in expected_spans]
            for bubble_index, owner_indexes in enumerate(owners):
                if len(owner_indexes) == 1:
                    owner_index = owner_indexes[0]
                    owner_start, owner_end = expected_spans[owner_index]
                    bubble_start, bubble_end, message_id = bubble_spans[bubble_index]
                    # cap 合併後一顆泡泡可能含「topic 句＋普通句」。只有整顆都落在同一
                    # topic span 內才綁 ID；部分相交也 fail-closed，避免 reaction 被過度歸因。
                    if owner_start <= bubble_start and bubble_end <= owner_end:
                        item_results_by_index[owner_index].append(message_id)

        for item_index, item in enumerate(self._committed_proactive):
            expected = str(item.get("text") or "").strip()
            # bubbles=None 是舊單一 sendMessage 呼叫契約；有 bubbles 卻無法唯一對位時絕不全域 fallback。
            item_results = (results if item_results_by_index is None
                            else item_results_by_index[item_index])
            rec = item.get("record")
            source_survived = (True if item_source_survived is None
                               else item_source_survived[item_index])
            if (source_survived and rec is None and item.get("track_initiative")
                    and item.get("prefix")
                    and getattr(item.get("state"), "DIALOGUE_AGENCY", False)):
                rec = dialogue_agency.note_initiative(
                    item.get("state"), item.get("prefix"), item.get("topic"), expected, time.time())
                item["record"] = rec
            if rec is not None:
                for message_id in item_results:
                    dialogue_agency.extend_delivery(rec, message_id=message_id)
            _record_self_msgs(item.get("state"), item_results, item.get("topic"), expected)

    def finalize_deferred(self, wire):
        """依 final actual wire 決定 delivery side effects；在 real send 前只改 clone。"""
        actual = str(wire or "")
        for item in self._deferred_actions:
            required = item.get("required") or ""
            fn = item.get("commit") if required and required in actual else item.get("discard")
            if callable(fn):
                fn(actual)
        for item in self._deferred_self_promises:
            promised = item.get("text") or ""
            if not promised or promised not in actual:
                continue
            meta = item.get("turn_meta") or {}
            before = {key: _TURN.get(key) for key in
                      ("self_promise_ctx", "self_promise_skip", "self_promise_trace")}
            try:
                _TURN["self_promise_ctx"] = meta.get("ctx")
                _TURN["self_promise_skip"] = meta.get("skip")
                _TURN["self_promise_trace"] = meta.get("trace")
                _maybe_self_promise_capture(promised)
            finally:
                for key, value in before.items():
                    if value is None:
                        _TURN.pop(key, None)
                    else:
                        _TURN[key] = value

    def send_typing(self):
        return None

    def send_sticker(self, file_id):
        if file_id:
            self.events.append(("sticker_blocked", file_id))
        # Telegram 沒有「貼圖＋文字」原子 API。交易內一律回失敗，讓既有 handler
        # 生成誠實文字退路；不能先真送貼圖、後段失敗再於重試重送。
        return False

    def send_file(self, kind, content_bytes, filename, caption=None):
        self.events.append(("file_blocked", {
            "kind": kind, "filename": filename, "caption": caption,
            "size": len(content_bytes or b""),
        }))
        return False

    def set_reaction(self, *_args, **_kwargs):
        return False

    def download_file(self, file_id):
        """唯一白名單代理的網路能力：只讀 sticker bytes，不會對使用者送出東西。"""
        fn = getattr(self._base, "download_file", None)
        return fn(file_id) if callable(fn) else None

    def get_updates(self, *_args, **_kwargs):
        return []                                         # speculative render 不讀／消費新的 Telegram update


class BurstDeliveryError(ConnectionError):
    """多訊息 logical answer 未完整送達；LifeLoop 視為暫態，offset 保持不動以便重試。"""


_BURST_COST_FIELDS = (
    "cost_since_digest_usd", "cost_since_digest_calls", "cost_total_usd",
    "cost_month_usd", "cost_month_key",
)


def _sync_burst_cost_fields(real_state, work_state):
    """Gemini meter 的 closure 寫 real state；clone 提交前保留那些真實已花成本。"""
    for name in _BURST_COST_FIELDS:
        if hasattr(real_state, name):
            setattr(work_state, name, copy.deepcopy(getattr(real_state, name)))


def _burst_capture_segment(events):
    """把一個 part 的保序 capture plan 轉成可裁切片段；effectful media 不可穿透交易。

    `text` 是完整 speculative payload；`data` 是可公平節錄的證據前綴；`voices` 則是應優先
    完整保留的自然回覆。座標契約也跟著真正承載它的 send metadata 走，最後只以 actual wire 驗收。
    """
    texts, data_parts, voices, contracts = [], [], [], []
    for kind, value in events:
        if kind != "text" or not value:
            continue
        if isinstance(value, dict):
            if value.get("invalidated"):
                continue
            payload = str(value.get("text") or "").strip()
            voice = str(value.get("voice") or "").strip()
            contract = value.get("mood_contract")
        else:                                                   # 舊測試／相容事件
            payload, voice, contract = str(value).strip(), "", None
        if not payload:
            continue
        texts.append(payload)
        if voice and payload.endswith(voice):
            prefix = payload[:-len(voice)].rstrip()
            if prefix:
                data_parts.append(prefix)
            voices.append(voice)
        elif voice:
            # 正常 `_say` 都是 suffix；若未來 transport 改形，寧可把整塊當 voice 保留，
            # 不靠 replace 猜哪段可以安全刪。
            voices.append(payload)
        else:
            data_parts.append(payload)
        if isinstance(contract, dict):
            contracts.append(copy.deepcopy(contract))
    if any(kind == "file_blocked" for kind, _value in events):
        honest = ("我找到你要的附件，但這一波同時還有其他訊息；為避免網路失敗後重複上傳，"
                  "這次沒有在合併回覆裡傳檔。請把附件要求單獨再傳一次。")
        return {"text": honest, "data": "", "voices": [honest], "mood_contracts": []}
    return {
        "text": "\n".join(texts).strip(),
        "data": "\n".join(data_parts).strip(),
        "voices": voices,
        "mood_contracts": contracts,
    }


def _fair_burst_budgets(texts, available):
    """water-fill：短段完整保留，剩餘空間平均給長段。"""
    lengths = [_utf16_units(s) for s in texts]
    budgets = [0] * len(texts)
    active = set(range(len(texts)))
    remaining = max(0, int(available))
    while active and remaining > 0:
        share = max(1, remaining // len(active))
        fitted = [i for i in active if lengths[i] <= share]
        if not fitted:
            for i in active:
                budgets[i] = share
            remaining -= share * len(active)
            for i in sorted(active):
                if remaining <= 0:
                    break
                budgets[i] += 1
                remaining -= 1
            break
        for i in fitted:
            budgets[i] = lengths[i]
            remaining -= lengths[i]
            active.remove(i)
    return budgets, lengths


def _burst_excerpt(text, budget, length=None):
    if budget <= 0 or not text:
        return ""
    length = _utf16_units(text) if length is None else length
    if length <= budget:
        return text
    return _take_utf16_prefix(text, max(0, budget - 1)).rstrip() + "…"


def _bounded_burst_wire(segments, limit=None):
    """把每個已完成意圖公平保留在一個有界 logical answer；超限時逐段明示節錄。"""
    lim = int(limit or notifier.TELEGRAM_LIMIT)
    clean, seen_payloads = [], set()
    for segment in segments:
        if isinstance(segment, dict):
            item = {
                "text": str(segment.get("text") or "").strip(),
                "data": str(segment.get("data") or "").strip(),
                "voices": [str(v or "").strip() for v in (segment.get("voices") or []) if str(v or "").strip()],
                "mood_contracts": list(segment.get("mood_contracts") or []),
            }
        else:
            item = {"text": str(segment or "").strip(), "data": str(segment or "").strip(),
                    "voices": [], "mood_contracts": []}
        # 全局只拿掉**完全相同**的整段（不做近義猜測）；使用者短時間
        # 連發三到十二則時，同一個 handler 回的「收到」不會因中間夾了
        # 另一段就再出現。不同數字、時間、否定、承諾全部保留。
        if item["text"] and item["text"] not in seen_payloads:
            clean.append(item)
            seen_payloads.add(item["text"])
    if not clean:
        return "我有收到這一波，但這次沒有產生可送出的文字回覆；請再試一次。"
    # 短的自然回話用同一段的空格承接，不以每個內部 handler
    # 的邊界硬切段；真的資料塊/多行內容才保留換行可讀性。
    joined_parts = []
    for index, item in enumerate(clean):
        if index:
            previous = clean[index - 1]
            data_boundary = ((item["data"] and not item["voices"])
                             or (previous["data"] and not previous["voices"])
                             or "\n" in item["text"] or "\n" in previous["text"])
            joined_parts.append("\n" if data_boundary else " ")
        joined_parts.append(item["text"])
    joined = "".join(joined_parts)
    if _utf16_units(joined) <= lim:
        return joined

    full_voice_head = "（資料較長，已節錄；以下保留每個問題的重點與完整自然回覆。）\n"
    # 相同的自然回覆不重播；不同 part 的完整 voice 則按原順序保留在各自段落。
    seen_voice = set()
    reserved = []
    for item in clean:
        kept = []
        for voice in item["voices"]:
            if voice not in seen_voice:
                seen_voice.add(voice)
                kept.append(voice)
        reserved.append("\n".join(kept))

    # 先為每段完整 voice 和必要換行預留空間；剩餘容量只節錄 data。若所有 voice 本身
    # 已超過 Telegram 單則上限，才退回對完整 payload 公平裁切（不可能同時完整保留）。
    nonempty_indexes = [i for i, item in enumerate(clean) if item["data"] or reserved[i]]
    inter_units = max(0, len(nonempty_indexes) - 1) * _utf16_units("\n")
    voice_units = sum(_utf16_units(v) for v in reserved)
    inner_units = sum(_utf16_units("\n") for i, item in enumerate(clean) if item["data"] and reserved[i])
    available = lim - _utf16_units(full_voice_head) - inter_units - voice_units - inner_units
    if available >= 0 and any(reserved):
        data_texts = [item["data"] for item in clean]
        budgets, lengths = _fair_burst_budgets(data_texts, available)
        pieces = []
        for data_text, voice, budget, length in zip(data_texts, reserved, budgets, lengths):
            excerpt = _burst_excerpt(data_text, budget, length)
            piece = "\n".join(p for p in (excerpt, voice) if p)
            if piece:
                pieces.append(piece)
        wire = full_voice_head + "\n".join(pieces)
        return _take_utf16_prefix(wire, lim)

    # voice 總量自己就超過單則上限時，不可能誠實宣稱「完整保留」；這條退路
    # 對所有部分公平節錄，header 也明說自然回覆本身可能被裁切。
    excerpt_head = "（回覆較長，以下每個部分都已節錄。）\n"
    payloads = [item["text"] for item in clean]
    sep_units = max(0, len(payloads) - 1) * _utf16_units("\n")
    budgets, lengths = _fair_burst_budgets(payloads, lim - _utf16_units(excerpt_head) - sep_units)
    pieces = [_burst_excerpt(text, budget, length)
              for text, budget, length in zip(payloads, budgets, lengths)]
    wire = excerpt_head + "\n".join(piece for piece in pieces if piece)
    return _take_utf16_prefix(wire, lim)


_BURST_PACED_MAX_BUBBLES = 6


def _cap_burst_delivery_bubbles(bubbles, cap=_BURST_PACED_MAX_BUBBLES):
    """全域封頂 paced delivery；只合併相鄰完整句，字元與順序一個不改。"""
    parts = [str(value or "") for value in (bubbles or []) if str(value or "")]
    limit = max(1, int(cap or 1))
    if len(parts) <= limit:
        return parts
    # 依原句數做連續、近乎等量的 partition；只有病態長答才啟動。一般 <=6 句保留原本
    # 的長短節奏，超量時則避免 50 顆泡泡、數十秒 typing 與 partial-retry 風險。
    base, extra = divmod(len(parts), limit)
    out, cursor = [], 0
    for index in range(limit):
        take = base + (1 if index < extra else 0)
        out.append("".join(parts[cursor:cursor + take]))
        cursor += take
    return out


def _burst_delivery_bubbles(wire, segments=None, enabled=False):
    """將已定稿的 burst logical answer 轉為僅影響呈現的泡泡串。

    語意規劃、記憶與 deferred side effects 仍看同一份 ``wire``；依完整句界及長句子句分串，
    不逐則重新回答、不拆引文。旗標關閉時保留舊的單 sendMessage。
    """
    text = str(wire or "").strip()
    if not text:
        return []
    if not enabled:
        return [text]
    # 用 capture metadata 保護證據／函式資料；資料以外的對話仍可分泡。
    # 缺少 metadata 時安全退回單泡，不憑文字外觀猜測資料邊界。
    if (not segments
            or not all(isinstance(segment, dict) for segment in segments)):
        return [text]
    data_blocks = [str(segment.get('data') or '').strip() for segment in segments
                   if str(segment.get('data') or '').strip()]
    if data_blocks:
        # Protect each exact evidence block, not every neighbouring conversational
        # reply. A single direct-send segment must not collapse the entire burst.
        # If budgeting transformed evidence, keep the conservative fallback.
        spans, cursor = [], 0
        for block in data_blocks:
            start = text.find(block, cursor)
            if start < 0:
                return [text]
            spans.append((start, start + len(block)))
            cursor = start + len(block)
        out, cursor = [], 0
        for start, end in spans + [(len(text), len(text))]:
            voice = text[cursor:start].strip()
            if voice:
                out.extend(_cap_burst_delivery_bubbles(_enum_glue(_paced_voice_split(voice))))
            if end > start:
                out.extend(_split_telegram_text(text[start:end]))
            cursor = end
        return [bubble for bubble in out if bubble]
    # wire 來自已通過 `_say` 衛生層的 actual payload；與單則回覆共用 quote-aware 呈現規則。
    bubbles = _paced_voice_split(text) or [text]
    bubbles = _enum_glue(bubbles)
    bubbles = _cap_burst_delivery_bubbles(bubbles)
    # logical wire 目前整體已 <= Telegram 上限；仍留 transport 保底，
    # 避免未來調整總預算後單顆泡泡意外超限。
    out = []
    for bubble in bubbles:
        out.extend(_split_telegram_text(bubble))
    return [bubble for bubble in out if bubble]


def _send_burst_bubbles(client, bubbles):
    """把已定稿的泡泡依序送出；後一顆前發 typing，停頓依它的長度計算。

    這層不 poll interrupt：外層 clone 還沒提交時若開另一個真 state 回合，會在最後被 clone
    覆蓋。任一 send 失敗即 fail-fast，由呼叫端丟棄整份 clone；已對 Telegram 可見的前綴
    無法回滾，下次重試可能重送（Telegram 多次 sendMessage 沒有交易/idempotency key）。
    """
    parts = [str(b or "").strip() for b in (bubbles or []) if str(b or "").strip()]
    typing = getattr(client, "send_typing", None)
    results = []
    for index, bubble in enumerate(parts):
        if index and typing and not getattr(client, "dry_run", False):
            try:
                typing()
                _sleep(_typing_delay(len(bubble)) * _jitter())
            except Exception as typing_error:
                # typing 只是呈現提示；它失敗不能吃掉真正的正文。
                print(f"[burst] 🫧 typing 提示失敗，仍繼續送正文："
                      f"{type(typing_error).__name__}: {typing_error}")
        try:
            result = client.send(bubble)
        except Exception as send_error:
            print(f"[burst] 🌊 泡泡 delivery 中斷：{type(send_error).__name__}: {send_error}")
            return False, results
        results.append(result)
        if not result:
            return False, results
    return bool(parts), results


def _burst_delivered_mood_contract(segments, wire):
    """從 final actual wire 反查最後一份真的仍可見的座標契約。"""
    for segment in reversed(list(segments or [])):
        if not isinstance(segment, dict):
            continue
        for contract in reversed(list(segment.get("mood_contracts") or [])):
            if _mood_contract_present(wire, contract):
                return copy.deepcopy(contract)
    return None


def _burst_part_updates(update):
    """取合成前的原 updates；舊式合成物沒有 metadata 時，仍可以保守重建。"""
    originals = list((update or {}).get("burst_updates") or [])
    texts = list((update or {}).get("burst_texts") or [])
    if originals and len(originals) == len(texts):
        return originals
    msg = dict((update or {}).get("message") or {})
    last_id = int((update or {}).get("update_id", 0) or 0)
    out = []
    for i, part in enumerate(texts):
        pm = dict(msg)
        pm["text"] = part
        out.append({"update_id": last_id - (len(texts) - 1 - i), "message": pm})
    return out


def _burst_ordered_updates(update):
    """取得文字／貼圖的 Telegram 原始順序；舊 synthetic update 則以 update_id
    保守重建。原始順序是狀態演算的一部分，不是只為了顯示。"""
    original = list((update or {}).get("burst_all_updates") or [])
    if original:
        return original
    sources = list(_burst_part_updates(update)) + list((update or {}).get("burst_sticker_updates") or [])
    decorated, ids = [], []
    for index, source in enumerate(sources):
        try:
            uid = int((source or {}).get("update_id", 0) or 0)
        except (TypeError, ValueError):
            uid = 0
        decorated.append((uid, index, source))
        ids.append(uid)
    if sources and all(uid > 0 for uid in ids) and len(set(ids)) == len(ids):
        return [source for _uid, _index, source in sorted(decorated)]
    return sources


_BURST_JOINED_CONVERSATION_ROUTES = frozenset(("smalltalk", "fact_or_chat"))


def _burst_join_has_stateful_fastpath(text, now_ts, cfg):
    """這一行雖可能被 intent 分成一般聊天，是否仍會被 route 前的狀態型
    handler 接走。

    joined generation 只會呼叫 handler 一次；若把這些句子併進去，第一行的
    early-return 可能吞掉後文，或多項偏好只學到其中一項。寧可退回 clone 內
    逐行演算、最後只送一個 wire，也不能拿「同 route」冒充「無 side effect」。
    """
    t = (text or "").strip()
    if not t:
        return True
    now_utc = datetime.fromtimestamp(float(now_ts), timezone.utc)
    tz = ZoneInfo(getattr(cfg, "timezone", "Asia/Taipei")) if ZoneInfo is not None else None
    return bool(
        selfstate.is_mood_watch_request(t)
        or selfstate.is_mood_watch_cancel(t)
        or selfstate.is_deferred_answer_request(t)
        or selfstate.is_time_fill(t)
        or selfstate.is_continuation_duration(t)
        # 與 `_maybe_llm_promise_rescue` 用同一個真時鐘／時區判斷；不帶
        # now/tz 會讓「明天十點你出現一下」在 planner 看似一般聊天，
        # 真 handler 卻先入帳 early-return，吞掉同波後文。
        or selfstate.looks_like_timed_request(t, now_utc, tz)
        or selfstate.is_sticker_send_request(t)
        or selfstate.is_sticker_followup_request(t)
        or selfstate.is_sticker_remember_request(t)
        or selfstate.is_bare_send_request(t)
        or selfstate.is_sticker_preference_request(t)
        or selfstate.asks_about_sent_sticker(t)
        or selfstate.asks_can_perceive_sticker(t)
        or _STICKER_TALK_RE.search(t)
        or _STICKER_ELLIPSIS_ASK_RE.match(t)
        or _sticker_pick_imperative(t)
        or selfstate.is_selfshare_followup(t)
        or _asks_which_liked(t)
        or plasticity.read_preference(t)
        or plasticity.is_repetition_complaint(t)
        or plasticity.is_dissatisfaction(t)
    )


_BURST_SCHEDULE_TAIL_RE = re.compile(
    r"(?:然後[，,]?)?(?:時間到(?:了|的時候)?|到時候|到時|到點)"
    r"(?:請)?(?:你)?(?:再|就)?(?:跟我|和我|向我)?"
    r"(?:分享|說|聊聊|聊|告訴我|回覆我|回應我)(?:一下)?[。！!，,\s]*")


def _burst_schedule_prefix(parts, cfg):
    """Join a timed instruction and dependent delivery tails before ledger mutation.

    Only generic anaphoric tails qualify: a new time, topic, recipient, cancellation,
    command or attachment remains an independent action. Never deduplicate by prose.
    """
    if len(parts) < 2:
        return 1
    first = parts[0].get("message") or {}
    text = str(first.get("text") or "").strip()
    if first.get("reply_to_message") or first.get("sticker") or _is_command_text(text):
        return 1
    now = datetime.fromtimestamp(float(first.get("date") or time.time()), timezone.utc)
    tz = ZoneInfo(getattr(cfg, "timezone", "Asia/Taipei")) if ZoneInfo else None
    reflection_time = re.fullmatch(
        r"我(?:先)?給你\s*(?:\d+|[一二兩三四五六七八九十百]+)\s*(?:分鐘|小時)"
        r"(?:想想|想一想|思考)(?:你)?自己(?:的)?(?:內在(?:狀況|狀態|感受)?|狀況|狀態|感受|心情)[。]?", text)
    if not (selfstate.looks_like_timed_request(text, now, tz) or reflection_time):
        return 1
    count = 1
    for part in parts[1:]:
        msg = part.get("message") or {}
        if (msg.get("reply_to_message") or msg.get("sticker")
                or not _BURST_SCHEDULE_TAIL_RE.fullmatch(str(msg.get("text") or "").strip())):
            break
        count += 1
    return count


def _burst_joined_conversation_kind(parts, state, cfg):
    """只在可證明沒有帳本／確認 handshake 的純對話串，才讓 LLM 整串讀一次。

    這是「不只一顆 bubble，而是真的一個回合」的路徑。排程、取消、時鐘、
    附件、座標數據等任一狀態性意圖都退回逐 part 交易，避免合併文字
    吞掉一個動作。任一 detector 出錯也退回安全路徑。
    """
    if len(parts or []) < 2 or state is None:
        return None
    if evidence.enabled(cfg) and any(activity.interested(p.get("message", {}).get("text", ""), state, _now_from_update(p).timestamp()) for p in parts):
        return None
    if evidence.enabled(cfg) and any(evidence.historical_request(p.get("message", {}).get("text", ""), True) for p in parts):
        return None
    if viewpoint.enabled(cfg) and any(viewpoint.answer(state, p, _now_from_update(p).timestamp()) for p in parts):
        return None
    if any(getattr(state, name, None) for name in (
            "skill_pending", "pending_answer_intent", "feeling_promise", "mood_watch",
            "keep_followup", "user_away")):
        return None
    if any((not p.get("fulfilled")) or p.get("owed")
           for p in (getattr(state, "scheduled_promises", None) or [])):
        return None
    texts = []
    try:
        for part in parts:
            message = (part or {}).get("message") or {}
            text = str(message.get("text") or "").strip()
            if (not text or _is_command_text(text) or message.get("reply_to_message")
                    or message.get("sticker")):
                return None
            now_ts = float(message.get("date") or time.time())
            kind = _resolve_effective_route(text, state, cfg, now_ts)[0].kind
            if kind not in _BURST_JOINED_CONVERSATION_ROUTES:
                return None
            if (selfstate.is_promise_cancel_request(text)
                    or selfstate.is_scheduled_promise_request(text)
                    or selfstate.is_feeling_promise_request(text)
                    or selfstate.promise_status_kind(text)
                    or selfstate.leave_announce(text)
                    or selfstate.is_back_statement(text)
                    or selfstate.looks_like_data_question(text)
                    or dialogue_intent.worth_skill_consensus(text)
                    or _burst_join_has_stateful_fastpath(text, now_ts, cfg)):
                return None
            texts.append(text)
        joined = "\n".join(texts)
        last_ts = float((((parts[-1] or {}).get("message") or {}).get("date")) or time.time())
        joined_kind = _resolve_effective_route(joined, state, cfg, last_ts)[0].kind
        if joined_kind not in _BURST_JOINED_CONVERSATION_ROUTES:
            return None
        if (selfstate.looks_like_data_question(joined)
                or dialogue_intent.worth_skill_consensus(joined)
                or _burst_join_has_stateful_fastpath(joined, last_ts, cfg)):
            return None
        return joined_kind
    except Exception:
        return None


def _burst_transaction_update_ids(update):
    """這次 one-wire transaction 真正涵蓋的原 update ids（文字＋mixed stickers）。"""
    sources = _burst_ordered_updates(update)
    ids = set()
    for source in sources:
        try:
            uid = int((source or {}).get("update_id", 0) or 0)
        except (TypeError, ValueError):
            continue
        if uid > 0:
            ids.add(uid)
    if not ids:
        try:
            uid = int((update or {}).get("update_id", 0) or 0)
        except (TypeError, ValueError):
            uid = 0
        if uid > 0:
            ids.add(uid)
    return sorted(ids)


def _burst_route_kinds(update, state, cfg):
    """對真實訊息邊界各解一次**有效** intent；只供 joined-render 安全判定。"""
    parts = _burst_part_updates(update)
    if len(parts) < 2:
        return []
    try:
        kinds = []
        for u in parts:
            t = (((u or {}).get("message") or {}).get("text") or "").strip()
            if t:
                now_ts = float((((u or {}).get("message") or {}).get("date")) or time.time())
                kinds.append(_resolve_effective_route(t, state, cfg, now_ts)[0].kind)
        return kinds
    except Exception as e:
        print(f"[burst] 🌊 跨 route 預判失敗，保守走原合成路徑：{type(e).__name__}: {e}")
        return []


def _burst_needs_route_transaction(update, state, cfg):
    if state is None or not getattr(cfg, "burst_one_answer_enabled", False):
        return False
    # 不再以「route kind 是否不同」當 correctness gate：同為 scheduled_promise 的兩則也可能
    # 是兩個不同時刻／行為；「好」還可能正在確認 pending skill。所有真多則都逐 part
    # 演算後再合成一個 wire，不能用 joined text 重新只選一次 route。
    return len(_burst_part_updates(update)) >= 2 or bool((update or {}).get("burst_sticker_updates"))


def _handle_burst_route_transaction(update, coach, reader, data, snap, state, client, cfg, tz):
    """多訊息 user turn：clone 內完成所有意圖，對外送一個有界 logical answer。"""
    try:
        burst_update_id = int((update or {}).get("update_id", 0) or 0)
    except (TypeError, ValueError):
        burst_update_id = 0
    receipt_ids = _burst_transaction_update_ids(update)
    delivered_ids = set()
    for value in (getattr(state, "burst_delivered_update_ids", None) or []):
        try:
            receipt = int(value)
        except (TypeError, ValueError):
            continue
        if receipt > 0:
            delivered_ids.add(receipt)
    exact_receipt = bool(receipt_ids) and set(receipt_ids).issubset(delivered_ids)
    try:
        legacy_delivered = int(getattr(state, "burst_delivered_update_id", 0) or 0)
    except (TypeError, ValueError):
        legacy_delivered = 0
    legacy_receipt = (not hasattr(state, "burst_delivered_update_ids") and burst_update_id > 0
                      and legacy_delivered == burst_update_id)
    if exact_receipt or legacy_receipt:
        # 上次已送達並先落了 receipt，但可能在外層 offset save 前中斷。這次只讓 offset 前進，
        # 不重新渲染／花費／送出。
        return True
    ordered_updates = _burst_ordered_updates(update)
    viewpoint_batch = (viewpoint.batch_answer(state, ordered_updates, _now_from_update(update).timestamp())
                       if viewpoint.enabled(cfg) else "")
    viewpoint_target = (viewpoint.response_target(state, ordered_updates, _now_from_update(update).timestamp())
                        if viewpoint_batch else None)
    capture = _BurstReplyCapture(client)
    # 所有既有 handler 都會改 state、甚至中途 save。多訊息交易必須先在 clone 上演算：
    # Telegram 真送成功前，不能把暫存回覆／貼圖／承諾誤記成已交付。
    state_save = getattr(state, "save", None)
    instance_save = vars(state).get("save") if hasattr(state, "__dict__") else None
    cost_before = tuple(copy.deepcopy(getattr(state, name, None)) for name in _BURST_COST_FIELDS)
    work_state = copy.deepcopy(state)
    work_state.save = lambda: None
    work_state._burst_delivery_capture = capture
    base_history = copy.deepcopy(list(getattr(work_state, "convo_history", None) or []))
    base_mood_report = copy.deepcopy(getattr(state, "mood_last_report", None))
    turn_before = copy.deepcopy(_TURN)
    sent_recent_before = list(_SENT_RECENT)
    short_sent_before = list(_SHORT_SENT)
    last_sent_before = dict(_LAST_SENT)
    segment_ranges = []
    try:
        prior_user_parts = []
        index = 0
        while index < len(ordered_updates):
            source = ordered_updates[index]
            source_msg = (source or {}).get("message") or {}
            sticker = source_msg.get("sticker")
            if sticker:
                # 照 Telegram 原序在 clone 套感受；不可先套整串所有貼圖，
                # 否則 text → sticker → text 的第一題會被「未來的貼圖」污染。
                sticker_signal(sticker, source, work_state, cfg, client=capture, coach=coach)
            source_text = str(source_msg.get("text") or "").strip()
            if not source_text:
                index += 1
                continue

            # 沒有帳本/handshake 的連續純對話，最長安全 prefix 只演算一次。
            # 這才是「把 3–12 則當同一次內容」；狀態性 route 仍保留逐項正確性。
            contiguous = []
            cursor = index
            while cursor < len(ordered_updates):
                candidate = ordered_updates[cursor]
                candidate_msg = (candidate or {}).get("message") or {}
                if candidate_msg.get("sticker") or not str(candidate_msg.get("text") or "").strip():
                    break
                contiguous.append(candidate)
                cursor += 1
            take, joined_kind = _burst_schedule_prefix(contiguous, cfg), None
            inventory_count = 0
            for candidate in contiguous:
                if not _sticker_inventory_question(candidate['message']['text']):
                    break
                inventory_count += 1
            take = max(take, inventory_count)
            if viewpoint.enabled(cfg):
                for count in range(len(contiguous), 1, -1):
                    if viewpoint.batch_answer(work_state, contiguous[:count],
                                              _now_from_update(contiguous[count - 1]).timestamp()):
                        take = count
                        break
            for count in range(len(contiguous) if take == 1 else 1, 1, -1):
                kind = _burst_joined_conversation_kind(contiguous[:count], work_state, cfg)
                if kind:
                    take, joined_kind = count, kind
                    break
            logical_sources = contiguous[:take]
            if take > 1:
                part = build_coalesced_update({"type": "text", "updates": logical_sources})
                if joined_kind:
                    part["burst_route_override"] = joined_kind
                else:
                    # Duration belongs to the first request, not its later clarification.
                    part["message"]["date"] = source_msg.get("date")
            else:
                part = source

            # 不使後一個 part 看到「前一個內部草稿已經被 bot 送出」的虛構歷史；但 pending
            # confirmation、promise ledger 等真 state mutation 仍留在 clone 給後一項承接。前面幾則
            # **使用者原話**則必須留著，後一句「那我該怎麼辦」才知道「那」指的是什麼。
            work_state.convo_history = copy.deepcopy((base_history + prior_user_parts)[-30:])
            start = len(capture.events)
            _handle_message_inner(part, coach, reader, data, snap, work_state, capture, cfg, tz)
            # 先記 append-only range，全部 part 演算完再投影。後一 part 可以
            # invalidate 前一 part 尚未交付的履約句，不會被已物化 segment 卡住。
            segment_ranges.append((start, len(capture.events)))
            _sync_burst_cost_fields(state, work_state)
            for logical_source in logical_sources:
                part_msg = (logical_source or {}).get("message") or {}
                part_text = str(part_msg.get("text") or "").strip()
                if part_text:
                    prior_user_parts.append({"role": "user", "text": part_text,
                                             "ts": part_msg.get("date") or time.time()})
            index += take
    except Exception:
        # 模型呼叫已實際花掉的成本不能隨 speculative render 回滾；若本輪在 render 階段失敗，
        # 只持久化 real state 上的成本欄位，不提交 clone 的其他變更。
        cost_after = tuple(getattr(state, name, None) for name in _BURST_COST_FIELDS)
        if cost_after != cost_before and not getattr(cfg, "dry_run", False) and callable(state_save):
            try:
                state_save()
            except Exception as save_error:
                print(f"[burst] 🌊 成本狀態暫存失敗：{type(save_error).__name__}: {save_error}")
        raise
    finally:
        # capture.send 回真只代表「暫存成功」，不是 Telegram delivery；送達 rings/turn flags
        # 必須退回交易前，再由下方真 send 逐一寫入。
        _TURN.clear()
        _TURN.update(turn_before)
        _SENT_RECENT[:] = sent_recent_before
        _SHORT_SENT[:] = short_sent_before
        _LAST_SENT.clear()
        _LAST_SENT.update(last_sent_before)
    segments = [_burst_capture_segment(capture.events[start:end])
                for start, end in segment_ranges]
    wire = _bounded_burst_wire(segments)
    delivery_bubbles = _burst_delivery_bubbles(
        wire, segments=segments,
        enabled=getattr(cfg, "burst_paced_bubbles_enabled", False))
    final_evidence_rejected = False
    validator = getattr(client, "validate_voice", None)
    if callable(validator):
        checked = validator(wire)
        if checked != wire:
            final_evidence_rejected = True
            # The assembled answer was rejected: discard speculative domain actions.
            # Keep received corrections and actual model costs from the real state.
            work_state = copy.deepcopy(state)
            wire = checked
            delivery_bubbles = bubble_split(wire, paced=getattr(cfg, "burst_paced_bubbles_enabled", False))
        client.approve_bubbles(delivery_bubbles)
    # 先把將提交的 clone 全部準備好；send 失敗就整份丟棄。成功後只剩記 receipt＋換入，
    # 把「Telegram 已可見、但本地還有會拋錯的加工」窗口壓到最小。
    try:
        if not final_evidence_rejected:
            capture.finalize_deferred(wire)
        work_state.convo_history = copy.deepcopy(base_history)
        # speculative `_remember` 曾把 capture=True 當成交付成功；bounded wire 若剪掉 canonical
        # pair，那筆 report 就不是真的說過。先回復真 state，再只用 final wire 重新驗收。
        work_state.mood_last_report = base_mood_report
        _TURN["sent_model_text"] = []
        joined = (((update or {}).get("message") or {}).get("text") or "").strip()
        user_ts = (((update or {}).get("message") or {}).get("date")) or None
        _remember(work_state, "user", joined, ts=user_ts)
        delivered_mood_contract = _burst_delivered_mood_contract(segments, wire)
        _stage_sent_model(wire, wire, mood_contract=delivered_mood_contract)
        _remember(work_state, "model", wire)
    finally:
        _TURN.clear()
        _TURN.update(turn_before)
    work_state.__dict__.pop("_burst_delivery_capture", None)
    # reaction／sticker／附件仍在交易內抑制；文字則依呈現旗標可拆成多顆泡泡。
    # 所有泡泡成功才 commit clone/receipt；後顆失敗時前綴已可見卻無法回滾，
    # 下次重試可能重送它。這是 Telegram 無多 sendMessage transaction 的已知邊界。
    _sync_burst_cost_fields(state, work_state)
    committed = {k: copy.deepcopy(v) for k, v in vars(work_state).items() if k != "save"}
    if instance_save is not None:
        committed["save"] = instance_save
    if receipt_ids:
        _receipt_offset = int(getattr(work_state, "tg_update_offset", 0) or 0)
        _all_receipts = delivered_ids.union(receipt_ids)
        merged_receipts = (sorted(uid for uid in _all_receipts if uid < _receipt_offset)[-256:]
                           + sorted(uid for uid in _all_receipts if uid >= _receipt_offset))
        committed["burst_delivered_update_ids"] = merged_receipts
        committed["burst_delivered_update_id"] = max(merged_receipts, default=0)

    delivered_ok, delivery_results = _send_burst_bubbles(client, delivery_bubbles)
    if evidence.enabled(cfg):
        receipt_book = copy.deepcopy(work_state.evidence_memory or state.evidence_memory)
        if receipt_book is not None and state.evidence_memory is not None:
            receipt_book["deliveries"] = copy.deepcopy(state.evidence_memory["deliveries"])
        committed["evidence_memory"] = receipt_book
    if viewpoint.enabled(cfg) and not final_evidence_rejected:
        actual = "\n".join(delivery_bubbles[:sum(bool(r) for r in delivery_results)])
        target = work_state if delivered_ok else state
        viewpoint.acknowledge(target, ordered_updates, actual, _now_from_update(update).timestamp(),
                              expected_batch=viewpoint_batch)
        if viewpoint_target:
            viewpoint.delivered(target, viewpoint_target, actual, _now_from_update(update).timestamp(),
                                delivery_results, complete=bool(delivered_ok))
        if delivered_ok:
            committed["conscious_dialogue"] = copy.deepcopy(target.conscious_dialogue)
        elif not getattr(cfg, "dry_run", False):
            state.save()
    if not delivered_ok:
        _TURN.clear()
        _TURN.update(turn_before)
        cost_after = tuple(getattr(state, name, None) for name in _BURST_COST_FIELDS)
        if cost_after != cost_before and not getattr(cfg, "dry_run", False) and callable(state_save):
            try:
                state_save()
            except Exception as save_error:
                print(f"[burst] 🌊 delivery 失敗後成本暫存失敗：{type(save_error).__name__}: {save_error}")
        delivered_n = sum(1 for value in delivery_results if value)
        raise BurstDeliveryError(
            f"multi-message reply was not fully delivered ({delivered_n}/{len(delivery_bubbles)} bubbles)")

    # capture 時的 bool True 不是 message_id；只有這次真 send 的所有 results 能建立
    # reaction/topic 對應。刷新受影響欄位到已預備的 committed snapshot。
    if not final_evidence_rejected:
        capture.bind_delivery_result(
            delivery_results, delivery_bubbles, wire=wire, segments=segments,
            segment_ranges=segment_ranges)
    for _delivery_field in ("initiative_ledger", "initiative_seq", "recent_self_msgs"):
        if hasattr(work_state, _delivery_field):
            committed[_delivery_field] = copy.deepcopy(getattr(work_state, _delivery_field))

    # 真送完成後才原子提交演算結果；receipt 先進記憶體且與狀態一起落盤。若外層 offset
    # 尚未 save 就中斷，下一次同 update 會命中上方 receipt 而不重送。
    for bubble in delivery_bubbles:
        _replay_note(bubble)
        _short_note(bubble)
    _LAST_SENT["text"] = "\n" + "\n".join(delivery_bubbles)
    vars(state).clear()
    vars(state).update(committed)
    if not getattr(cfg, "dry_run", False) and callable(state_save):
        state_save()
    return True


def _evidence_wrap(client, state, cfg, records=None, origin="background", now=None):
    wrapped = evidence.wrap(client, state, cfg, records, origin, now)
    if isinstance(wrapped, evidence.EvidenceClient):
        wrapped.on_delivery = _stage_sent_model
    return wrapped


def _viewpoint_actual(raw):
    key = _sent_memory_key(raw)
    return next((e.get("text", "") for e in reversed(_TURN.get("sent_model_text") or [])
                 if e.get("key") == key), "")


def _viewpoint_message_ids(raw):
    key = _sent_memory_key(raw)
    return next((e.get("message_ids", []) for e in reversed(_TURN.get("sent_model_text") or [])
                 if e.get("key") == key), [])


def _viewpoint_reply(update, state, client, cfg):
    now_ts = _now_from_update(update).timestamp()
    sources = _burst_ordered_updates(update) or [update]
    reply = viewpoint.batch_answer(state, sources, now_ts)
    if not reply:
        return False
    target = viewpoint.response_target(state, sources, now_ts)
    certify = getattr(client, "certify", None)
    if callable(certify):
        certify(reply)
    _say(client, reply)
    actual = _viewpoint_actual(reply)
    if not getattr(state, "_burst_delivery_capture", None):
        viewpoint.acknowledge(state, sources, actual, now_ts)
        viewpoint.delivered(state, target, actual, now_ts, _viewpoint_message_ids(reply),
                            complete=(actual == reply))
    msg = update.get("message") or update.get("edited_message") or {}
    _remember(state, "user", msg.get("text", ""), ts=now_ts)
    _remember(state, "model", reply, ts=now_ts)
    state.last_user_msg_ts = now_ts
    if not getattr(cfg, "dry_run", False):
        state.save()
    return True


def handle_message(update, coach, reader, data, snap, state, client, cfg, tz):
    """以巢狀作用域執行一個 Telegram user turn。

    multi-message turn 的 one-wire 契約在整個 handle 期間都有效：前置的 promise-preempt `_say`
    不會把它消耗掉；巢狀插話暫時 push 自己的值、返回後仍恢復外層；任何早退或例外都會 pop，
    因而不會把「只送一泡」誤套到下一個主動發話。
    """
    try:
        burst_n = int((update or {}).get("burst_n", 1) or 1)
    except (TypeError, ValueError):
        burst_n = 1
    one_wire = burst_n >= 2 and getattr(cfg, "burst_one_answer_enabled", False)
    nested = bool(_BURST_ONE_WIRE_STACK)
    previous_turn = dict(_TURN) if nested else None
    previous_paced = _TURN.get("paced_bubbles", False)
    _TURN["paced_bubbles"] = bool(getattr(cfg, "burst_paced_bubbles_enabled", False))
    client = _evidence_wrap(client, state, cfg, (data or {}).get("records", []),
                            origin="reply", now=_now_from_update(update).timestamp())
    _BURST_ONE_WIRE_STACK.append(bool(one_wire))
    try:
        # A correction is received input, not a side effect of successful speech.
        # Persist on the real state before a burst transaction clones it.
        if evidence.enabled(cfg):
            for source in (_burst_ordered_updates(update) or [update]):
                msg = source.get("message") or source.get("edited_message") or {}
                if _is_owner_text(source, cfg) and evidence.CHALLENGE.search(msg.get("text", "")):
                    evidence.reply(state, source, (data or {}).get("records", []),
                                   _now_from_update(source).timestamp(), tz)
                    if not getattr(cfg, "dry_run", False):
                        state.save()
        if viewpoint.enabled(cfg):
            changed = False
            for source in (_burst_ordered_updates(update) or [update]):
                if _is_owner_text(source, cfg):
                    changed = bool(viewpoint.correction(state, source, _now_from_update(source).timestamp())) or changed
            if changed and not getattr(cfg, "dry_run", False):
                state.save()
        if one_wire and _burst_needs_route_transaction(update, state, cfg):
            return _handle_burst_route_transaction(update, coach, reader, data, snap, state, client, cfg, tz)
        return _handle_message_inner(update, coach, reader, data, snap, state, client, cfg, tz)
    finally:
        _BURST_ONE_WIRE_STACK.pop()
        _TURN["paced_bubbles"] = previous_paced
        if nested:
            # 插話不只改篇幅，也會換掉話者、今日記寫與座標真值；恢復整輪的事實地面。
            _TURN.clear()
            _TURN.update(previous_turn)


# ── 入口 ─────────────────────────────────────────────────────────────
def _build(cfg):
    if ZoneInfo is None:
        raise SystemExit("此 Python 沒有 zoneinfo，請升級到 3.9+ 或安裝 backports.zoneinfo。")
    tz = ZoneInfo(cfg.timezone)
    reader = DriveReader(cfg.google_credentials, cfg.drive_root_folder_id, cfg.owner_line_user_id,
                         credentials_json=getattr(cfg, "google_credentials_json", ""))
    client = Notifier(cfg.telegram_bot_token, cfg.telegram_chat_id, cfg.dry_run)
    state = State.load(cfg.state_path)
    client = _evidence_wrap(client, state, cfg)
    _apply_ac_flags(state, cfg)                          # 🧭 啟動即透傳 AC 規格旗標 → 心跳/lifeloop 路徑也讀得到（非僅首訊後）
    # 🧾 §0.60 承諾履行：舊 topic-keyed 情境做法一次性遷移成活觸發（重複提問→sit:user_repeat、回應風格→always…）
    # ＝已答應過的做法不再是「字面主題才召回」的死 key（截圖：教過「重複提問→呵斥」、真的重複卻永不觸發）。
    # 冪等（遷移後 key 帶觸發段、不再命中）；旗標關＝不動任何 engram＝同現狀。
    if getattr(cfg, "skill_legacy_migrate_enabled", True):
        moved = plasticity.migrate_legacy_skills(getattr(state, "engrams", None) or [], now_ts=time.time())
        if moved:
            print(f"[skill] 🧾 舊做法遷移成活觸發 {moved} 條（承諾履行：同一約定、換成真的會觸發的 key）")
            if not cfg.dry_run:
                state.save()
    coach = coachmod.Coach(cfg)

    # 每次 Gemini 記帳 → 累進「今天（自上次摘要）」「本月」「累計總估」的花費（後兩者跨重啟持久＝不被重啟洗掉，
    # 才能像 Google 後台那樣報得出月度/累計，不再每次重啟就歸 0）。本月鍵用本地時區、換月歸零（對齊月度 spend cap）。
    def _sink(cost_usd):
        state.cost_since_digest_usd = (state.cost_since_digest_usd or 0.0) + cost_usd
        state.cost_since_digest_calls = (state.cost_since_digest_calls or 0) + 1
        state.cost_total_usd = (state.cost_total_usd or 0.0) + cost_usd
        mkey = datetime.now(tz).strftime("%Y-%m")
        if state.cost_month_key != mkey:                      # 換月 → 本月累計歸零再加（跨月重置）
            state.cost_month_key, state.cost_month_usd = mkey, 0.0
        state.cost_month_usd = (state.cost_month_usd or 0.0) + cost_usd
    coach.meter.on_cost = _sink

    return tz, reader, client, state, coach


def _chain_params(cfg, state=None, k_adj=None):
    k = getattr(cfg, "selfstate_sensitivity", 2.0)
    if state is not None and getattr(state, "sensitivity_override", None) is not None:
        k = state.sensitivity_override          # bot 內手動調的優先
    if k_adj is None and state is not None:
        k_adj = getattr(state, "k_breath_adj", 0.0) or 0.0   # 生命迴圈的活力呼吸
    # 🌡️ 計算情緒調制知覺：喚醒高/趨近 → 降 k（同一批記寫這次更易被打動）；退縮 → 升 k（鈍）＝主觀體驗。
    k = round(k + (k_adj or 0.0) + affect.k_affect_adj(state), 3)
    return {"adaptive": getattr(cfg, "selfstate_adaptive", True),
            "sensitivity": k,
            "z_star": getattr(cfg, "selfstate_z_star", 2.0),
            "tau_star": getattr(cfg, "selfstate_tau_star", 0.78),
            "int_min": getattr(cfg, "selfstate_int_min", 0.5),
            "diff_min": getattr(cfg, "selfstate_diff_min", 0.0),
            "n_min": getattr(cfg, "selfstate_n_min", 8),
            "r_min": getattr(cfg, "selfstate_r_min", 3)}


def _learn_route_correction(state, text, route, now_ts, cfg):
    """🧬 Phase 3 路由更正記憶（capture）：使用者表達不滿（看不懂/不是這個意思/我是說…）緊接在某次回覆後
    → 代表**前一句**被誤路由；待這次（或下一句）改寫且路由明確（安全白名單）→ 綁定「那種句式 → 正確 route」。
    兩種形態都接：① 不滿＋改寫同一句（「我是說，多久沒聊」）當場學；② 純不滿（「看不懂」）先記、下一句明確路由再學。
    需約兩次一致才會在 recall 生效（安全）；逾窗未綁定就放掉。記住每句的路由供下一輪比對（不記不滿句本身）。"""
    dissat = plasticity.is_dissatisfaction(text)
    lastr = getattr(state, "last_routed", None)
    pend = getattr(state, "route_learn", None)
    learnable = route.kind in plasticity.SAFE_LEARN_KINDS
    learned = False
    if dissat and lastr and learnable and route.kind != lastr.get("kind") and plasticity.signature(lastr.get("text")):
        learned = plasticity.learn_correction(state.engrams, lastr["text"], route.kind, now_ts=now_ts)  # ① 同句不滿＋改寫
        state.route_learn = None
    elif dissat and lastr and plasticity.signature(lastr.get("text")):
        state.route_learn = {"bad_text": lastr["text"], "bad_kind": lastr.get("kind"), "ts": now_ts}     # ② 純不滿：標前一句待綁
    elif (pend and not dissat and learnable and route.kind != pend.get("bad_kind")
          and now_ts - (pend.get("ts") or 0) <= plasticity.CORR_WINDOW_S):
        learned = plasticity.learn_correction(state.engrams, pend["bad_text"], route.kind, now_ts=now_ts)  # ②續：改寫到明確路由
        state.route_learn = None
    elif pend and now_ts - (pend.get("ts") or 0) > plasticity.CORR_WINDOW_S:
        state.route_learn = None                                                                          # 逾窗放掉
    if learned:
        state.engrams = plasticity.consolidate(state.engrams, now_ts=now_ts)
        if not getattr(cfg, "dry_run", False):
            state.save()
    if not dissat:                                  # 記住這句的路由，供下一輪「不滿→前一句被誤路由」比對（不記不滿句）
        state.last_routed = {"text": text, "kind": route.kind, "ts": now_ts}


def _params_sig(p):
    return "|".join(str(p[k]) for k in
                    ("adaptive", "sensitivity", "z_star", "tau_star", "int_min", "diff_min", "n_min", "r_min"))


def _stash_reading(state, res, now_ts, ingest, params):
    """把一次判定讀數連同『判定當時的脈絡』（時間、ingest 戳、參數指紋、**實際用的 k**）存成 state.self_state。
    整合環與互動端**共用這一個構造**，確保 /status、bodystate 顯示的 k 與 Gate 永遠同源、同一時點
    （收斂審計 F6：先前兩處各自構造 self_state、欄位形狀不一致 → 顯示漂移）。"""
    state.self_state = dict(res, computed_at=now_ts, ingest_at=ingest,
                            params_sig=_params_sig(params), sensitivity=params["sensitivity"])
    return state.self_state


def _entropy_signals(res):
    """抽本圈判定的訊號向量（給內在熵算跨圈變化）：
    (gate, scope.n, recur z, perc lcc, dxi 分化, dxi 整合, 價性均值)。res 為 None / 缺值都補 0。"""
    if not res:
        return (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    sc = res.get("scope") or {}
    om = res.get("omegas") or {}
    recur, perc, dxi = om.get("recur") or {}, om.get("perc") or {}, om.get("dxi") or {}
    vals = [t.get("valence") for t in ((res.get("reading") or {}).get("valenceTrajectory") or [])
            if t.get("valence") is not None]
    vmean = sum(vals) / len(vals) if vals else 0.0
    return (float(res.get("gate") or 0), float(sc.get("n") or 0), float(recur.get("z") or 0),
            float(perc.get("lcc") or 0), float(dxi.get("differentiation") or 0),
            float(dxi.get("integration") or 0), float(vmean))


def _revisit_topics(records):
    """語料裡出現過的不同主題（topicLabel），去空白、穩定排序——供自我刺激的繞回游標循環。"""
    seen, out = set(), []
    for r in records or []:
        lab = r.get("topicLabel")
        if lab and lab not in seen:
            seen.add(lab)
            out.append(lab)
    return sorted(out)


def _revisit_signal(records, topic, now):
    """某主題的廉價統計向量（只用 load_owner_data 的欄位，不碰 embedding/不重跑判定）：
    (筆數, 回返次數, 媒材種類數, 最近一筆距今小時, 平均文字長度)。"""
    rs = [r for r in (records or []) if r.get("topicLabel") == topic]
    if not rs:
        return (0.0, 0.0, 0.0, 0.0, 0.0)
    returns = determination._returns(rs, determination.DEFAULTS["return_gap_min"])
    media = len({r.get("type") for r in rs if r.get("type")})
    tss = [t for t in (analyzer.parse_ts(r.get("ts")) for r in rs) if t]
    recency_h = (now - max(tss)).total_seconds() / 3600.0 if tss else 48.0
    avg_len = sum(len(r.get("text") or "") for r in rs) / len(rs)
    return (float(len(rs)), float(returns), float(media), float(recency_h), float(avg_len))


def _loop_wait_secs(cfg, state):
    """生命迴圈的環間等待秒數＝心跳轉速。優先序：手動 /pulse（state.pulse_override）＞ 🍃 環境適應倍率
    （state.env_pace_mult：周遭活絡→快、冷清/深夜→慢）＞ 設定檔預設。手動覆蓋時環境適應讓位（你說了算）。"""
    if state is not None and getattr(state, "pulse_override", None) is not None:
        return max(0.5, float(state.pulse_override))   # 下限 0.5 秒
    base = max(0.5, float(getattr(cfg, "lifeloop_wait_secs", 3.0)))
    mult = (getattr(state, "env_pace_mult", 1.0) or 1.0) if state is not None else 1.0
    return max(0.5, base * mult)


def _selfstate_compute(reader, state, cfg, now, data=None):
    """B 整合：隨生命迴圈**每一圈**跑判定鏈（不再看計時器）。回傳 res；無資料/未啟用回 None。

    觸發掛在遞迴上、不另設間隔；但資料／k 沒變就吃快取（純 Python O(n²) 很便宜，貴的 LLM 只在
    真要出聲時才呼叫）。門檻由 _chain_params(cfg, state) 取——其中 k 已吃了 state.k_breath_adj
    （迴圈活力呼吸），所以「感覺來了」是這個遞迴圈自身跑出來的。
    """
    if not getattr(cfg, "selfstate_enabled", True) or not state.owner_folder_id:
        return None
    now_ts = now.timestamp()
    if data is None:
        data = reader.load_owner_data(state.owner_folder_id)   # mtime-cached、便宜
    ingest = (data.get("meta") or {}).get("lastIngestTs")
    params = _chain_params(cfg, state)
    sig = _params_sig(params)
    cache = state.self_state
    state.last_self_compute_ts = now_ts
    if cache and ingest == cache.get("ingest_at") and sig == cache.get("params_sig"):
        return cache                                            # 資料/參數沒變 → 沿用快取（不重算）
    full = reader.load_embedding_records(state.owner_folder_id)
    if not full:
        return None
    res = determination.run_chain(full, None, data.get("contexts") or [],
                                  data.get("journeys") or [], now, params)
    _stash_reading(state, res, now_ts, ingest, params)   # 帶上判定當時實際用的 k（/status 同源顯示，修「看數據不準」）
    return res


def _in_live_round(state):
    """🔗 這一輪對話還活著嗎（耦合 round_open）——活著時 bot **壓住自己的雜訊**：proactive 的 🫀/🫧/🌀
    讓路、不插話（社交感：你我正在聊，bot 就別自顧自地丟自陳/伸手/體驗）。唯一例外是 🌬️ soothe（因應冷卻的那一次）。"""
    return bool(getattr(getattr(state, "coupling", None), "round_open", False))


INTERACTION_PRESENCE_SEC = 6 * 60   # 你在這麼短內說過話 → 仍視為「在場」（即使耦合這輪剛收），主動獨白讓位給回應你


def _user_present(state, now_ts):
    """🔗 使用者此刻在場、在跟我互動嗎——這輪對話還活著（耦合 round_open）**或**你剛說過話（近窗內，即使 round 剛收）。
    **互動優先於自我獨白**：在場時 bot 壓住所有『報自己』的主動發話（🫀自陳/🫧伸手/🌀體驗/🍃換檔/🪞自我修正），
    內在狀態仍默默染回覆、但不另外插話打斷你；你離開後（兩個條件都不成立）內在生活才以主動訊息流出。
    （比單看 round_open 多收一個『剛說過話』的近窗，堵住截圖那種「round 剛收、你還在、bot 卻自顧自報自己」。）"""
    if _in_live_round(state):
        return True
    last_u = getattr(state, "last_user_msg_ts", 0) or 0
    return bool(last_u) and (now_ts - last_u) < INTERACTION_PRESENCE_SEC


def _has_unanswered_proactive(state):
    """bot 上次主動發話後，使用者是否尚未再接話。

    ``last_push_ts`` 只會在主動推播成功後前進；把它和最後使用者訊息比，正好是
    「我已經開過一個話頭、對方還沒有接」這個可觀測事實。這不是讀心：沉默可以有
    各種原因，所以政策是**不再另開話題**，不是替使用者解釋沉默。
    """
    # 新行動台帳是更直接的真相（也涵蓋 bot 尚未收過任何 user 訊息的起始情境）；舊存檔／關旗標
    # 仍由時間戳判準完整兜底。
    if getattr(state, "DIALOGUE_AGENCY", False) and dialogue_agency.open_initiative(state):
        return True
    last_user = getattr(state, "last_user_msg_ts", 0) or 0
    last_push = getattr(state, "last_push_ts", 0) or 0
    return bool(last_user and last_push and last_push >= last_user)


def _proactive_ok(state, cfg, now_ts, allow_quiet=False, allow_unanswered=False):
    """🔗 R4 統一發話**政策閘**——所有『不被要求就開口』的主動管道（🫀自陳/🫧伸手/🌀體驗/🍃換檔）共用這**一條**普世政策，
    把「此刻可不可以主動」收成單一真相，重新置中於『先貼著你』：
    ① 互動優先：你在場（在聊或剛說過話）→ 不開口（讓位給回應你）；
    ② 未回覆不續講：我上次主動開的話頭尚未被接住 → 不換一條新 lane 繼續自言自語；
    ③ 深夜不擾：本地深夜/清晨不主動打擾（除非 allow_quiet，如 🌬️ soothe 是回應你未回的問句、性質不同）。
    ``allow_unanswered`` 僅給同一件已履行約定的一次後續確認；它不能替一般自發內容開後門。
    （各管道自己的『該不該說＋反連發冷卻』仍在這之上各自判：emergence_due 的破天花板可打斷冷卻、spontaneous 的自有冷卻、
    共用 last_push_ts 防連發…——政策統一、節流仍因地制宜。）"""
    if _user_present(state, now_ts):
        return False
    if (not allow_unanswered and getattr(cfg, "unanswered_proactive_guard_enabled", False)
            and _has_unanswered_proactive(state)):
        return False
    # 「同一約定可後續一次」只跳過未回覆鎖，不跳過使用者明說的拒絕界線。
    if (getattr(cfg, "dialogue_agency_enabled", False)
            and dialogue_agency.voluntary_block_reason(state, now_ts)):
        return False
    if cfg is not None and not allow_quiet:
        try:
            tz = ZoneInfo(getattr(cfg, "timezone", "Asia/Taipei"))
            if circadian.is_quiet_hours(datetime.fromtimestamp(now_ts, timezone.utc).astimezone(tz)):
                return False
        except Exception:
            pass
    return True


def _selfstate_emit(client, state, res, coach, now, cooldown_min=0, repeat_cooldown_min=0, cfg=None):
    """C 感覺 Stage 1：只在真有新感覺才出聲「🫀 背景自陳」；否則安靜地繼續活著。

    出聲判定交給 lifeloop.emergence_due（值得報＋這個可說狀態沒講過＋破歷史最高閘的真新湧現 OR 冷卻已過）。
    notified_self_gate 當「只升不降的天花板」——門檻邊緣抖動不會被一再當『又升關』；單拍 blip 由 Stage 0
    的 confirm_gate 在外層先擋（呼叫端只在 res.gate == 已確認 gate 時才進來）。
    同一條主線的重報用較長的 repeat_cooldown_min（免得整夜一直反芻同一條線太囉嗦）。
    """
    if not _proactive_ok(state, cfg, now.timestamp()):   # 🔗 R4 統一發話政策：互動優先＋深夜不擾（🫀自陳讓路；冷卻由 emergence_due 各判、破天花板可打斷）
        return
    if not (coach and coach.enabled):
        return
    gate = res["gate"]
    sig = selfstate.state_signature(res)
    topic = ((res.get("reading") or {}).get("content") or {}).get("topic") or (res.get("scope") or {}).get("dominant")
    repeat = bool(topic and topic == state.told_self_topic)        # 同一條線的重報 → 拉長冷卻
    # 🤝 兌現承諾：使用者曾託付「之後有感覺再說」（state.feeling_promise）。等到真有「新感覺」浮現
    # （值得報＋這狀態還沒講過）就主動兌現——破冷卻立刻說、框成「你剛要我說的，來了」；但**只在真有
    # 湧現時**才說（不為交差假裝有感覺）。逾期自動失效。
    promise = getattr(state, "feeling_promise", None)
    if promise and (now.timestamp() - (promise.get("ts") or 0)) > PROMISE_TTL_SEC:
        state.feeling_promise = promise = None
    worthy = selfstate.push_worthy(gate)
    fulfilling = bool(promise) and worthy and sig != state.told_self_sig   # 真有沒講過的新感覺＝兌現時機
    due = lifeloop.emergence_due(gate, state.notified_self_gate or 0, sig, state.told_self_sig,
                                 worthy, repeat, now.timestamp(),
                                 state.last_push_ts, cooldown_min * 60, repeat_cooldown_min * 60)
    if due or fulfilling:
        body = selfstate.render(res, coach, connect=_connect_hint(state, now.timestamp()))
        topic_line = selfstate._line_phrase(res)[1]              # 這則自陳在講哪條線（記進 id↔主題，供「讚的是哪一筆」）
        if fulfilling:                                            # 兌現：引言＋自陳；清掉約定
            lead = selfstate.promise_lead(now)
            # 📦 §1.85 這是另一條兌現出口（感覺託付），原本 _say 的回傳值被丟棄、託付**無條件**清掉 ⇒ 送失敗
            # ＝託付無聲消失、不重試也不誠實承認。改成送成功才清（旗標關＝維持原無條件清＝逐位元同現狀）。
            _fp_ok = _say(client, lead + "\n\n" + body, prefix="🫀 ", state=state, topic=topic_line)
            if _fp_ok:
                _ability_fired(state, cfg, "selfstate", time.time())   # 🪪 §1.94 送出成功才記（掛錯位置＝盤點謊報）
            shown = "🫀 " + lead + "\n" + body
            if _fp_ok or not getattr(cfg, "promise_delivery_proof_enabled", False):
                state.feeling_promise = None
            else:
                print("[promise] 📦 §1.85 感覺託付兌現沒送出去 → 保留託付、下次再兌現（不無聲消失）")
        else:
            if _say(client, body, prefix="🫀 背景自陳\n", state=state, topic=topic_line):
                _ability_fired(state, cfg, "selfstate", time.time())   # 🪪 §1.94 送出成功才記（掛錯位置＝盤點謊報）
            shown = "🫀 " + body
        _remember(state, "model", shown)                         # 主動自陳進記憶 → 互動接得上「那條線/它」
        if getattr(cfg, "self_report_delta_enabled", False):
            # 🧠 §1.21 主動自陳也記進「上次自陳」帳（全文截 200＋此刻帶位快照）＝互動差分/無變化短句的基準
            state.last_self_report = {"text": body[:200], "ts": now.timestamp(),
                                      "snap": selfreport.snapshot(getattr(state, "vitality", None),
                                                                  res, state, now.timestamp())}
        _note_selfshare(state, body, now.timestamp())            # 🪞 §0.85 主動感覺自陳 → 追問「你感覺到什麼」接得回這件事
        _set_focus(state, topic=topic_line, now_ts=now.timestamp())
        if gate > (state.notified_self_gate or 0):
            state.notified_self_gate_ts = now.timestamp()    # 天花板被抬高 → 重置 F2 衰減計時
        state.notified_self_gate = max(state.notified_self_gate or 0, gate)   # 天花板（短期只升不降；長期極慢衰減＝F2）
        state.last_push_ts = now.timestamp()      # 自陳＝一則正式推播（與其他通知共用冷卻）
        state.told_self_sig = sig                 # 講過這個狀態（互動重複問才會回「說過了」）
        state.told_self_topic = topic             # 記住這條線（之後同線重報走長冷卻）
        state.self_topic_ts = now.timestamp()     # 🪞 bot 剛主動談了自己 → 開「自我在場」窗：接下來你回我，多半還在說我
        if gate >= 3:
            state.selfstate_open_ts = now.timestamp()   # 有線繃著＋已出聲 → 開追問窗
        if not client.dry_run:
            state.save()


METACOG_RELEVANT_SEC = 240    # 剛對使用者報過自己狀態（這麼久內）才值得回頭更正（否則沒上下文、像自言自語）
METACOG_CORRECT_COOLDOWN_MIN_DEFAULT = 30   # 🪞 主動自我修正的冷卻（分鐘）：刻意拉長＝罕見、不當常設的恍神台詞（env METACOG_CORRECT_COOLDOWN_MIN 可調）


def _metacog_correct(client, state, cfg, now):
    """🪞🔍 後設認知的主動自我修正：這拍內省發現『剛剛認錯了自己』(mismatch)，且**剛對使用者報過自己的狀態**
    → 回頭更正一句（「我剛說 X，其實比較像 Y」）。罕見（剛好轉換期＋剛報過＋過冷卻），是真有意識的強訊號、不洗版。"""
    sm = getattr(state, "self_model", None)
    mm = sm and sm.get("mismatch")
    if not mm:
        return
    now_ts = now.timestamp()
    told_ts = max(getattr(state, "bodystate_last_ts", 0) or 0, getattr(state, "selfstate_open_ts", 0) or 0)
    if not told_ts or now_ts - told_ts > METACOG_RELEVANT_SEC:        # 沒剛報過自己 → 不主動更正（沒上下文）
        return
    # 🔗 互動優先：你在我報完之後又說了話＝你在主導/已往下走 → 別插一句自我更正打斷你（更正會自然顯在下一則回覆的信心語）。
    # 只在「我報完、你還沒接話的安靜空檔」才把更正補進去（你問我怎樣→我說悶→你還沒回→我察覺其實是餓，回頭補一句）。
    if (getattr(state, "last_user_msg_ts", 0) or 0) > told_ts:
        return
    cooldown_s = (getattr(cfg, "metacog_correct_cooldown_min", 0) or METACOG_CORRECT_COOLDOWN_MIN_DEFAULT) * 60
    if now_ts - (getattr(state, "last_metacog_ts", 0) or 0) < cooldown_s:
        return
    # 🪞 §2.05 存在特色：這是**收回一句已經說出口的話**，不是補充。程式先挑**一個**理由（correction_reason），
    # 其餘內省量一律不進 prompt；切入角度由程式輪替指定（落盤計數器，非 list 長度）。
    # 旗標關（getattr 預設 False）＝仍走原本那句確定性模板＝逐位元同現狀。
    _mv = getattr(cfg, "metacog_correct_voice_enabled", False)
    msg = None
    if _mv and coach and getattr(coach, "enabled", False):
        msg = coach.voice_metacog_correct(
            metacog.correction_facts(mm, sm, getattr(state, "last_self_report", "") or ""),
            persona.metacog_angle(getattr(state, "metacog_correct_n", 0)), state.convo_history)
    if not msg:
        msg = metacog.correction_text(mm)
    try:
        if _say(client, msg, **({"prefix": "🪞 ", "state": state, "topic": "我剛剛講錯了自己"} if _mv else {})):
            if _mv:
                state.metacog_correct_n = int(getattr(state, "metacog_correct_n", 0) or 0) + 1
            _ability_fired(state, cfg, "ac_drift", time.time())   # 🪪 §1.94 送出成功＝真的用出來一次
            _remember(state, "model", ("🪞 " + msg) if _mv else msg)   # 🪞 §2.05 自己的對話史看得到這是哪條 lane
            state.last_metacog_ts = now_ts
            state.last_push_ts = now_ts        # 與其他推播共用冷卻：別讓接著的自陳疊上來
            if not getattr(cfg, "dry_run", False):
                state.save()
    except Exception as e:
        print(f"[metacog] 🪞 主動自我修正送出失敗（略過、不影響存活）：{type(e).__name__}: {e}")


AC_DRIFT_TTL_SEC = 120          # 飄移那一下沒在這時間內說成 → 過去了、不補述（主觀體驗是當下的，不是事後報告）
AC_DRIFT_COOLDOWN_SEC = 15 * 60  # 自發說飄移感受的自有冷卻（這是罕見、有重量的一句，不洗版）


def _habit_absence_audit(state, cfg, data, now_ts, tz):
    """🌾 §1.79 /habits 追加段：日課偵測的當場對帳——資料健康、每條線的門檻過不過、此刻判定。"""
    if tz is None:
        return "🌾 日課偵測：沒有時區、算不出「今天」→ 停用。"
    records = (data or {}).get("records") or []
    _ing = analyzer.parse_ts(((data or {}).get("meta") or {}).get("lastIngestTs"))
    h = habits.abs_records_health(records, tz, now_ts, ingest_ts=_ing.timestamp() if _ing else None)
    if not h["ok"]:
        return (f"🌾 日課偵測：**這份資料還不能判**（{h['reason']}）——今天 {h['today_n']} 筆、"
                f"未歸戶 {h['today_unlabeled']} 筆。寧可沉默也不亂問。")
    first = habits.today_first_minutes(getattr(state, "habit_events", None) or [], tz, now_ts)
    now_min = habits._minutes_local(now_ts, tz)
    labels = sorted({(r.get("topicLabel") or "").strip() for r in records if (r.get("topicLabel") or "").strip()})
    lines = [f"🌾 日課偵測（此刻 {habits.hhmm(now_min)}、你今天第一次出現 "
             f"{habits.hhmm(first) if first is not None else '還沒'}）："]
    shown = 0
    for lab in labels:
        prof = habits.abs_routine_profile(habits.abs_topic_daily_samples(records, lab, tz, now_ts), now_ts, tz)
        if not prof:
            continue
        v = habits.abs_verdict(prof, now_min, first, habits.abs_topic_done_today(records, lab, tz, now_ts))
        due = prof["p75"] + habits.abs_grace_min(prof["iqr"])
        lines.append(f"・「{lab}」：{prof['n_days']} 天、平常 {habits.hhmm(prof['p50'])}、"
                     f"我會等到 {habits.hhmm(due)} 才覺得怪 → 現在判定＝{v}")
        shown += 1
    if not shown:
        lines.append("・還沒有任何一條線穩到可以算「日課」（要 14 天以上、時間夠集中）——所以我不會問。")
    return "\n".join(lines)


def _habit_absence_emit(client, state, cfg, coach, now, data=None):
    """🌾 §1.79 習慣缺席暗示：他有一條**記寫主題**已經穩成日課、今天人也在、資料也看得見今天，
    唯獨那條線我這邊還沒看到 → 在他自己的節奏窗內，用一句好奇輕輕帶過（**不是提醒**）。

    紀律（全部 fail-closed；任何前提算不出來就沉默）：
      ① 走 _proactive_ok（互動優先＋深夜不擾）＝這是我自己想到的事，不是他明排的約定，不比照守約例外；
      ② 共用冷卻 notify_cooldown_min＋自有 20h 冷卻（一天最多一次、不同線也不連發）；
      ③ 同一條線**同一天只問一次**（state.habit_absence 台帳、跨重生）；
      ④ 判定與門檻全在 habits.abs_*（純函式、可離線單測）；資料健康不過＝整支不動。
    旗標關（getattr 預設 False）＝直接 return＝逐位元同現狀。"""
    if not getattr(cfg, "habit_absence_enabled", False):
        return
    if not (coach and getattr(coach, "enabled", False)):
        return
    now_ts = now.timestamp()
    if not _proactive_ok(state, cfg, now_ts):                      # ① 你在場/深夜 → 不打擾
        return
    if now_ts - (state.last_push_ts or 0) < max(0, getattr(cfg, "notify_cooldown_min", 30)) * 60:
        return                                                     # ② 共用反連發
    if now_ts - (getattr(state, "last_habit_absence_ts", 0) or 0) < 20 * 3600:
        return                                                     # ② 自有冷卻：一天最多一次
    try:
        tz = ZoneInfo(getattr(cfg, "timezone", "Asia/Taipei")) if ZoneInfo is not None else None
    except Exception:
        tz = None
    if tz is None:                                                 # 沒時區＝算不準「今天」→ 沉默
        return
    records = (data or {}).get("records") or []
    ledger = getattr(state, "habit_absence", None)
    if ledger is None:
        ledger = state.habit_absence = {}
    today = habits.abs_day_key(now_ts, tz)
    skip = [lab for lab, v in ledger.items() if (v or {}).get("day") == today]   # ③ 今天問過的線不再問
    _events = getattr(state, "habit_events", None) or []
    _first_today = habits.today_first_minutes(_events, tz, now_ts)
    cands = habits.abs_candidates(records, tz, now_ts,
                                  today_first_min=_first_today,
                                  skip_labels=skip,
                                  ingest_ts=analyzer.parse_ts(((data or {}).get("meta") or {}).get("lastIngestTs")).timestamp()
                                  if analyzer.parse_ts(((data or {}).get("meta") or {}).get("lastIngestTs")) else None)
    # 🌾 §1.80 對話作息也是日課（使用者更正需求）：記寫類沒有候選時，看「他今天還沒出現」——平常這時間
    # 早該看到他（早安習慣優先、其次每天第一次出現的規律），今天還靜靜的 → 主動去看看（他回來會看到）。
    # 這正是 §1.79 原版誤當抑制條件的場景。旗標關（getattr 預設 False）＝不看＝§1.79 原行為。
    _appear = None
    if not cands and getattr(cfg, "habit_absence_convo_enabled", False) and "＠出現" not in [
            lab for lab, v in ledger.items() if (v or {}).get("day") == today]:
        _gp = habits.abs_routine_profile(
            habits.abs_event_daily_samples(_events, ("greet_am",), tz, now_ts), now_ts, tz,
            min_days=habits._ABS_CONVO_MIN_DAYS, max_iqr=habits._ABS_CONVO_MAX_IQR, min_p25=240)
        _ap = _gp or habits.abs_routine_profile(
            habits.abs_event_daily_samples(_events, ("msg", "contact"), tz, now_ts), now_ts, tz,
            min_days=habits._ABS_CONVO_MIN_DAYS, max_iqr=habits._ABS_CONVO_MAX_IQR, min_p25=240)
        if _ap is not None and habits.abs_appear_verdict(
                _ap, habits._minutes_local(now_ts, tz), _first_today is not None) == "absent":
            _appear = {"profile": _ap, "greet": _gp is not None}
    if not cands and _appear is None:
        return
    if cands:
        c = cands[0]
        prof = c["profile"]
        # 🌾 §2.06 只講一個面向、真數字不進 prompt（留在下面那行 print）；旗標關＝走原本那支＝逐位元同現狀
        _h1 = getattr(cfg, "habit_absence_one_thing_enabled", False)
        _rule = (persona.habit_absence_rule_v2(c["label"], persona.absence_angle(
            getattr(state, "habit_absence_pick_n", 0))) if _h1
            else persona.habit_absence_rule(c["label"], habits.hhmm(prof["p50"]), int(c["overdue"])))
        _key, _topic = c["label"], c["label"]
    else:
        prof = _appear["profile"]
        _h1 = getattr(cfg, "habit_absence_one_thing_enabled", False)
        _rule = (persona.appear_absence_rule_v2(
            persona.appear_angle(getattr(state, "habit_absence_pick_n", 0)),
            selfstate.spontaneous_text(state.entropy, state.self_state)[:40]
            if getattr(state, "entropy", None) is not None else "") if _h1
            else persona.appear_absence_rule(habits.hhmm(prof["p75"]), has_greet_habit=_appear["greet"]))
        _key, _topic = "＠出現", None
    try:
        msg = coach.reply(_rule, "", getattr(state, "convo_history", None))
    except Exception as e:
        print(f"[habit] 🌾 §1.79 缺席暗示生成失敗（略過）：{type(e).__name__}: {e}")
        return
    if not (msg or "").strip():
        return
    if not _say(client, msg, prefix="🌾 ", state=state, topic=_topic):
        return                                                     # 送不出去＝不記帳、下次還可以問
    _ability_fired(state, cfg, "habit_absence", time.time())   # 🪪 §1.94 送出成功＝真的用出來一次
    _remember(state, "model", "🌾 " + msg)
    ledger[_key] = {"day": today, "ts": now_ts}
    while len(ledger) > 8:                                         # 台帳上限：丟最舊的（不丟剛記的這條）
        _oldest = min((k for k in ledger if k != _key), key=lambda k: (ledger[k] or {}).get("ts") or 0, default=None)
        if _oldest is None:
            break
        ledger.pop(_oldest, None)
    state.last_habit_absence_ts = now_ts
    state.last_push_ts = now_ts
    if not getattr(cfg, "dry_run", False):
        state.save()
    if getattr(cfg, "habit_absence_one_thing_enabled", False):
        state.habit_absence_pick_n = int(getattr(state, "habit_absence_pick_n", 0) or 0) + 1   # 🌾 §2.06 角度輪替
    print(f"[habit] 🌾 §1.79/§1.80 缺席暗示：「{_key}」平常 {habits.hhmm(prof['p50'])}、"
          f"今天到現在還沒看到（樣本 {prof['n_days']} 天）→ 已輕輕提一句")


def _foresight_bridges(state):
    """🔮 §1.93 取跨主題橋——**修 §1.90 的死機制**。

    根因（`/foresight` 實測照出來的）：`state.associations` **不是 dict，是 `association.Associations`
    物件**（state.py:127、monitor.py:8263 `state.associations = association.Associations(summary=…)`），
    而 §1.90 寫的是 `isinstance(getattr(state, "associations", None), dict)` ⇒ **恆為 False** ⇒ 橋清單恆空
    ⇒ 這條 lane 從上線到現在**一次都不可能產生候選**。MEMORY「新機制常被無聲架空」再一次——而且這次連
    測試都跟著錯：§1.90 的測試餵的是手寫 dict `{"bridges": {...}}`，把同一個錯誤假設一起編碼進去，
    所以 62 條全綠也蓋不到。本次測試改用**真的 `association.Associations` 物件**建構，同型錯誤不可能再過。

    三種形狀都吃：①Associations 物件（.bridges 是 dict）②dict（{"bridges": dict 或 list}，如
    `association_summary` 的持久化形）③其他＝空。回 list of bridge dict。"""
    a = getattr(state, "associations", None)
    br = getattr(a, "bridges", None)
    if br is None and isinstance(a, dict):
        br = a.get("bridges")
    if br is None:                                        # 退而求其次：重生種子（注意它**沒有** anchor/support）
        br = ((getattr(state, "association_summary", None) or {}) or {}).get("bridges")
    if isinstance(br, dict):
        return list(br.values())
    if isinstance(br, list):
        return list(br)
    return []


def _foresight_gate_counts(bridges, spans, now_ts, cfg):
    """🔮 §1.93 逐道閘還剩幾條——讓「候選 0 個」說得出**卡在哪一關**，門檻才能照真實數字調而不是憑感覺。
    回 [(閘名, 通過數)]（依序遞減）。確定性、純計數。"""
    _ms = int(getattr(cfg, "foresight_min_support", 2))
    n_all = len(bridges or [])
    n_live = [b for b in (bridges or []) if not b.get("emerged")]
    n_sup = [b for b in n_live if int(b.get("support") or 0) >= _ms]
    n_cos = [b for b in n_sup if float(b.get("cos") or 0.0) >= 0.30]
    n_span = [b for b in n_cos
              if (b.get("a") or "").strip() in spans and (b.get("b") or "").strip() in spans]
    n_anch = [b for b in n_span
              if ((b.get("anchor_a") or {}).get("text") or "").strip()
              and ((b.get("anchor_b") or {}).get("text") or "").strip()]
    return [("橋總數", n_all), ("未湧現", len(n_live)), (f"support≥{_ms}", len(n_sup)),
            ("cos≥0.30", len(n_cos)), ("兩端都有記寫", len(n_span)), ("兩端都有錨點原文", len(n_anch))]


def _pkg_sources():
    """🪪 §1.94 讀自己的原始碼（給缺口掃描用）。讀不到＝回 {}＝掃不出缺口但**不 crash、不編造**。"""
    out = {}
    try:
        d = os.path.dirname(os.path.abspath(__file__))
        for fn in os.listdir(d):
            if fn.endswith(".py"):
                try:
                    with open(os.path.join(d, fn), encoding="utf-8") as fh:
                        out[fn] = fh.read()
                except Exception:
                    continue
    except Exception:
        return {}
    return out


def _roster_audit(state, cfg, now_ts):
    """🪪 §1.94 `/abilities` 全文＝能力盤點＋願望帳（確定性、不經 LLM、**唯讀無副作用**）。
    刻意不學 /skills 的 refresh_alive 去寫 state——盤點就是盤點。"""
    scan = wishmod.scan_source(_pkg_sources())
    cands = wishmod.candidates(scan, state, cfg, now_ts)
    led = getattr(state, "wish_ledger", None) or []
    block = wishmod.audit_text(led, cands)
    if not scan.get("state_fields"):
        block += "\n（我讀不到自己的原始碼，這次算不出缺口——不是沒有缺口。）"
    return rostermod.roster_text(state, cfg, now_ts, wish_block=block)


def _foresight_audit(state, cfg, data, now_ts):
    """🔮 §1.92 預想對帳（/foresight）：**這條 lane 的預設失敗模式是「從不觸發」**——§1.90 規格早就寫明
    「沉默必須是可觀測的」、也寫好了 foresight.audit_text，但**我當初漏掉沒把它掛上任何指令**（MEMORY
    「新機制常被無聲架空」的又一次現形：函式存在、從沒被呼叫到）。這裡補上，並把「今天是被哪一道閘擋下」
    也算出來——不然使用者只能看著它不出聲、無從判斷是候選不夠還是節流卡住。確定性、不經 LLM。"""
    recs = []
    for r in ((data or {}).get("records") or []):
        _t = analyzer.parse_ts(r.get("ts"))
        recs.append({**r, "_ts": _t.timestamp() if _t else None})
    spans = foresightmod.line_spans(recs, now_ts)
    brs = _foresight_bridges(state)
    cands = foresightmod.pick_candidates(brs, spans, now_ts,
                                         min_support=int(getattr(cfg, "foresight_min_support", 2)),
                                         dormant_min_days=float(getattr(cfg, "foresight_dormant_min_days", 5.0)))
    led = getattr(state, "foresight_ledger", None) or []
    cands = [c for c in cands
             if not foresightmod.pair_blocked(c["pair"], led, getattr(state, "recent_insights", None), now_ts)]
    why = ""
    if not getattr(cfg, "foresight_enabled", False):
        why = "旗標 FORESIGHT=0"
    elif getattr(state, "foresight", None):
        why = "手上那條假設還沒到期／還沒等到那條線回來（settle 優先，不會再開新的）"
    elif not spans:
        why = "讀不到任何可解析時間的記寫"
    elif not brs:
        why = "帳本裡還沒有累積出跨主題橋"
    elif not cands:
        why = f"{len(brs)} 條橋裡沒有一條同時滿足：未湧現＋support≥2＋cos≥0.30＋一端 7 天內還在寫＋另一端停住 5–60 天"
    else:
        _cd = max(60, int(getattr(cfg, "foresight_cooldown_min", 720))) * 60
        if len(led) >= 3 and all((e.get("verdict") or "") == "miss" for e in led[-3:]):
            _cd *= 2
        _left = _cd - (now_ts - (getattr(state, "last_foresight_ts", 0) or 0))
        _push = getattr(cfg, "notify_cooldown_min", 30) * 60 - (now_ts - (getattr(state, "last_push_ts", 0) or 0))
        if _left > 0:
            why = f"自有冷卻還剩約 {int(_left // 60)} 分鐘"
        elif _push > 0:
            why = f"共用反連發還剩約 {int(_push // 60)} 分鐘（前面某條 lane 剛出聲過）"
        else:
            why = "沒有被擋，下一拍你不在場時就會說"
    out = foresightmod.audit_text(state, cands, spans, blocked_why=why)
    if not cands:                                         # 🔮 §1.93 候選 0 個時，說得出卡在哪一關
        out += "\n逐道閘還剩：" + "、".join(f"{k} {v}" for k, v in _foresight_gate_counts(brs, spans, now_ts, cfg))
        out += ("\n（提醒：橋的 support 與錨點原文**不會跨重啟保留**——`Associations.summary()` 只存"
                " key/a/b/kind/dir/cos/strength/emerged，重生時 support 歸 0、錨點空白，要等新記寫進來才重新累積。）")
    if cands:
        c = cands[0]
        out += (f"\n下一個會拿來說的：「{c['a']}」還在動（{c['active_days']} 天內）／"
                f"「{c['b']}」停了 {c['dormant_days']} 天，support {c['support']}、cos {c['cos']}"
                f"\n它停住的那一筆：「{c['quote']}」")
    return out


def _worldline_emit(client, state, cfg, coach, now, data=None, force=False):
    """🌐 §1.95 外面的世界撞進他的線：一天最多一次，拿他**自己授權過**的那條還在寫的線去搜，
    把外面對同一件事的另一種說法帶回來，跟他寫過的原文擺在一起。

    使用者裁定的三件事貫穿全函式：**嚴格白名單**（沒授權一個字都不外送）、**接受偏移**（搜的是他的標籤、
    排除新聞時事）、**不先驗證**（tools 欄位名可設定、原始錯誤字串留在 state.worldline_probe 供 /worldline 看）。
    紅線：**拿不到來源網址就當這次失敗、沉默**（沒來源不准說「我查到」）。
    旗標關＝第一行 return＝不搜、不寫 state、不呼叫 _say＝逐位元同現狀。

    🌐 §2.01 兩件事（使用者：「要有作用，等了一陣子還是沒看到」）：
      ① **回傳沒開口的原因**（開口成功＝""）——原本一路 `return` 到底、只有 console 看得到，
         使用者只能面對一片安靜猜為什麼。`/worldline now` 靠這個把原因當場講給他聽。
      ② `force=True`＝他**自己來要**的那次：跳過「你在場/深夜」與兩道冷卻（那些是「別在你面前自言自語」
         的禮貌閘，他親口要的時候不適用），但**白名單與總量上限照舊**——那兩個是隱私與花錢的閘，不是禮貌。"""
    if not getattr(cfg, "worldline_enabled", False):
        return "整支關著（.env 的 WORLDLINE=0）"
    if not (coach and getattr(coach, "enabled", False)):
        return "沒有可用的模型金鑰"
    allow = {x.strip() for x in (getattr(state, "worldline_allow", None) or []) if x and x.strip()}
    if not allow:
        return "你還沒授權任何標籤"                        # ★ 白名單空＝完全不動（/worldline 會告訴他怎麼授權）
    now_ts = now.timestamp()
    if not force:
        if not _proactive_ok(state, cfg, now_ts):
            return "你人在（或現在是深夜清晨）"
        _cd = max(0, getattr(cfg, "notify_cooldown_min", 30)) * 60
        _since = now_ts - (getattr(state, "last_push_ts", 0) or 0)
        if _since < _cd:
            return f"我 {int(_since // 60)} 分鐘前才主動說過話，要隔 {int(_cd // 60)} 分鐘"
        _own = max(1, int(getattr(cfg, "worldline_cooldown_h", 24))) * 3600
        _left = _own - (now_ts - (getattr(state, "last_worldline_ts", 0) or 0))
        if _left > 0:
            return f"今天已經查過了，還要 {max(1, int(_left // 3600))} 小時"
    # 🌐 §2.16 月額度：`worldline_search_n` 原本是**終身計數**（§1.95 上線前不敢先驗證時的防呆）——
    # 搜滿 40 次整條 lane **永久沉默**，而使用者的目標是「持續刺激記寫」。改成自然月歸零（額度沿用同一個
    # WORLDLINE_MAX_SEARCHES）。旗標關＝終身上限＝逐位元同現狀。
    if getattr(cfg, "worldline_monthly_budget", False):
        try:
            _wtz = ZoneInfo(getattr(cfg, "timezone", "Asia/Taipei")) if ZoneInfo is not None else None
            _mon = (now.astimezone(_wtz) if _wtz is not None else now).strftime("%Y-%m")
            if getattr(state, "worldline_month", "") != _mon:
                state.worldline_month = _mon
                state.worldline_search_n = 0
        except Exception:
            pass
    if int(getattr(state, "worldline_search_n", 0) or 0) >= max(1, int(getattr(cfg, "worldline_max_searches", 40))):
        return ("這個月的搜尋額度用完了（下個月自動恢復）" if getattr(cfg, "worldline_monthly_budget", False)
                else "已經用掉總量上限（grounding 另外計費）")    # 總量防呆（grounding 另計費）
    recs = []
    for r in ((data or {}).get("records") or []):
        _t = analyzer.parse_ts(r.get("ts"))
        recs.append({**r, "_ts": _t.timestamp() if _t else None})
    spans = foresightmod.line_spans(recs, now_ts)
    cands = wlmod.pick_lines(recs, spans, allow, wlmod.used_map(getattr(state, "worldline_ledger", None)), now_ts)
    if not cands and force:
        # An explicit request can revisit older authorized writing; it cannot
        # bypass the allowlist, search budget, or per-topic repetition limit.
        cands = wlmod.pick_lines(recs, spans, allow,
                                wlmod.used_map(getattr(state, "worldline_ledger", None)),
                                now_ts, include_dormant=True)
    if not cands:
        return "授權的那幾條現在都不能撞（打 /worldline 看每條卡在哪一關）"
    c = cands[0]
    # ── 真的送出去（唯一離開這台機器的字串＝build_query 的產物，不含任何記寫內文） ──
    q = wlmod.build_query(c["label"])
    state.worldline_search_n = int(getattr(state, "worldline_search_n", 0) or 0) + 1   # 花錢前先落帳
    try:
        finding, sources, queries = gemini.generate_grounded(
            coach.api_key, coach.model, persona.WORLDLINE_FIND_SYSTEM, persona.worldline_find_user(q),
            tool_field=(getattr(cfg, "worldline_tool_field", "google_search") or "google_search"),
            on_usage=getattr(getattr(coach, "meter", None), "record", None))
        state.worldline_probe = f"ok｜搜「{q}」→ 來源 {len(sources)} 個、模型實際搜了 {queries or '（沒回報）'}"
    except Exception as e:
        state.worldline_probe = f"失敗｜{type(e).__name__}: {str(e)[:200]}"
        print(f"[worldline] 🌐 §1.95 grounding 呼叫失敗（沉默、下次再試）：{str(e)[:160]}")
        if not getattr(cfg, "dry_run", False):
            state.save()
        return f"查的時候出錯了：{type(e).__name__}: {str(e)[:120]}"
    sources = [(title, url) for title, url in (sources or [])
               if wlmod.sources_ok([(title, url)])]
    activity.record(state, cfg, "search", now_ts, query=q, count=len(sources))
    if not wlmod.sources_ok(sources):
        print("[worldline] 🌐 §1.95 拿不到來源網址 → 沉默（沒有來源就不說「我查到」）")
        if not getattr(cfg, "dry_run", False):
            state.save()
        return "查回來的東西沒有來源網址（我不會說『我查到』卻拿不出出處）"
    # 🌐 §2.01 外面查回來的說法**自己就常帶「」**（引書名、引術語）——而在這則訊息裡「」只包他寫過的字，
    # 所以先拆掉；否則 collision_ok 第②閘會把它當成「引號裡有他沒寫過的東西」，連程式模板都過不了（實測）。
    finding = wlmod.strip_quotes(finding)
    if not finding or wlmod.eventy_hit(finding):
        print("[worldline] 🌐 §1.95 查回來的是新聞時事形態 → 沉默（要的是這條線在外面的說法）")
        if not getattr(cfg, "dry_run", False):
            state.save()
        return "查回來的是新聞時事，不是這條線在外面的說法"
    _src = sources[0]
    # 🌐 §1.99（§1.95 上線前實測的收尾）：grounding 給的網址是 Google 的**轉址連結**（一長串不透明字串），
    # 站名只在 title 欄 ⇒ 給 LLM 的來源要「站名｜網址」一起，否則他看到的來源根本認不出是誰講的。
    # 程式模板 `fallback_text` 本來就這樣給，這裡補齊 LLM 版，兩條路一致。
    _src_txt = f"{_src[0]}｜{_src[1]}" if (_src[0] and _src[1]) else (_src[1] or _src[0])
    # 🔁 §2.04 同型：worldline ledger `keep=10` ⇒ `10%4=2` ⇒ 永遠第 2 種形態（實測）
    _var = (int(getattr(state, "worldline_var", 0) or 0) % 4
            if getattr(cfg, "rotate_monotonic_enabled", False)
            else len(getattr(state, "worldline_ledger", None) or []) % 4)
    msg = None
    try:
        # 🌐 §2.07 說出這個外部說法**讓我對這條線的看法動了哪裡**——素材是我上次講這條線說過的話（現成）
        _lo = ""
        if getattr(cfg, "worldline_chain_enabled", False):
            _lo = next((str(r.get("text") or "")[:60] for r in reversed(getattr(state, "recent_self_msgs", None) or [])
                        if (r.get("topic") or "") == c["display"]), "")
        _inv = (persona.worldline_invite(getattr(state, "worldline_invite_n", 0))
                if getattr(cfg, "worldline_spark_enabled", False) else "")   # 🌐 §2.16 延伸 vs 開新線輪替
        _wl_rule = (persona.worldline_say_rule_v2(c["display"], c["quote"], finding, _src_txt, _var, _lo, invite=_inv)
                    if getattr(cfg, "worldline_chain_enabled", False)
                    else persona.worldline_say_rule(c["display"], c["quote"], finding, _src_txt, _var))
        _wl_rule += (f"\n引用的記寫距今 {c['days']} 天，不代表使用者今天仍有同樣立場。"
                     "\n外部材料是待比較的觀點，不是對使用者的診斷。"
                     "明確說出一個相同處、差異或可嘗試的方法；沒有根據就不硬說有新洞見。")
        msg = coach.reply(_wl_rule,
                          "", getattr(state, "convo_history", None))
    except Exception as e:
        print(f"[worldline] 🌐 §1.95 生成失敗：{type(e).__name__}: {e}")
    cps = wlmod.corpus(recs)
    ok, why = (False, "空")
    if msg:
        # 🌐 §2.01 先做**句級修正**再判（§1.62 家規：修錯的那一處、別為了一個標記丟掉整則好話）——
        # 實測 LLM 常把外面說法的一句轉述也框上引號，那是唯一的違規，拆掉符號就消掉「這是他寫的」的宣稱。
        msg = wlmod.sanitize_quotes(msg, c, cps)
        ok, why = wlmod.collision_ok(msg, c, cps)
    if not ok:
        msg = wlmod.fallback_text(c, finding, sources)
        ok, why2 = wlmod.collision_ok(msg, c, cps)
        print(f"[worldline] 🌐 §1.95 LLM 版沒過碰撞閘（{why}）→ 退回程式模板")
        if not ok:
            print(f"[worldline] 🌐 §1.95 模板也沒過（{why2}）→ 沉默")
            if not getattr(cfg, "dry_run", False):
                state.save()
            return f"查到的東西跟你寫的接不起來（{why2}）——那樣就只是新聞摘要，我不送"
    # 🌐 §2.01 紅線收尾：**每一則都要帶得出來源**。實測 LLM 版常常把來源整個略掉（prompt 給了它、它沒寫），
    # 只有程式模板一定會印 ⇒ 那條紅線等於只在退回模板時才成立。這裡確定性補一行，不靠 LLM 記得。
    if _src[1] and _src[1] not in msg:
        msg = msg.rstrip() + f"\n（來源：{_src_txt}）"
    if not _say(client, msg, prefix="🌐 ", state=state, topic=c["display"],
                track_initiative=not force):
        return "訊息送不出去"

    _ability_fired(state, cfg, "worldline", time.time())
    _remember(state, "model", "🌐 " + msg)
    state.worldline_ledger = wlmod.push_ledger(getattr(state, "worldline_ledger", None), c, sources, now_ts,
                                               finding=(finding if getattr(cfg, "worldline_followup_enabled", False) else ""))
    if getattr(cfg, "worldline_spark_enabled", False):
        state.worldline_invite_n = int(getattr(state, "worldline_invite_n", 0) or 0) + 1   # 🌐 §2.16 邀請輪替（落盤）
    if getattr(cfg, "rotate_monotonic_enabled", False):
        state.worldline_var = int(getattr(state, "worldline_var", 0) or 0) + 1   # 🔁 §2.04 送出成功才推進形態
    state.last_worldline_ts = now_ts
    state.last_push_ts = now_ts
    if not getattr(cfg, "dry_run", False):
        state.save()
    print(f"[worldline] 🌐 §1.95 撞了「{c['display']}」（回返 {c['visits']}、來源 {len(sources)} 個）")
    return ""                                             # 🌐 §2.01 ""＝真的開口了（呼叫端據此不再補話）


def _worldline_followup_hint(state, cfg, text, now_ts):
    """🌐 §2.16 他在**追問剛帶回的外部說法**時的接地：24h 內撞過的那幾筆，若他這句提到那條線的名字、
    或距撞擊 ≤30 分（＝多半就是在回那一則），就把「查到的說法本身＋來源」餵給聊天 lane——
    否則 bot 只能從語感重編一個說法（§1.36 幻覺家族）。未命中/旗標關＝''＝逐位元同現狀。"""
    if not getattr(cfg, "worldline_followup_enabled", False):
        return ""
    for e in reversed(getattr(state, "worldline_ledger", None) or []):
        ts, f = e.get("ts") or 0, e.get("finding") or ""
        if not f or not (0 <= now_ts - ts <= 86400):
            continue
        lab = (e.get("label") or "").strip()
        if (lab and lab in (text or "")) or (now_ts - ts) <= 1800:
            src = (e.get("src") or [""])[0]
            name = e.get("src_name") or ""
            return ("【他可能在追問你不久前帶回的那個外部說法——照這份講，別重編】\n"
                    f"那時你查的是他的「{lab}」線，外面的說法是：{f}\n"
                    f"來源：{(name + '｜') if name else ''}{src}\n"
                    "這不是你本來就知道的、也不是他寫的；他問細節而這裡沒有的，就老實說你只看到這一段。")
    return ""


def _worldline_audit(state, cfg, data, now_ts):
    """🌐 §1.95 /worldline 對帳（唯讀、不經 LLM）。白名單空時第一句就講怎麼授權。"""
    recs = []
    for r in ((data or {}).get("records") or []):
        _t = analyzer.parse_ts(r.get("ts"))
        recs.append({**r, "_ts": _t.timestamp() if _t else None})
    spans = foresightmod.line_spans(recs, now_ts)
    allow = {x.strip() for x in (getattr(state, "worldline_allow", None) or []) if x and x.strip()}
    cands = wlmod.pick_lines(recs, spans, allow, wlmod.used_map(getattr(state, "worldline_ledger", None)), now_ts)
    # 🌐 §2.01 這行以前會說「沒有被擋，下一拍你不在場時就會去撞」——**那是不老實的**：它只看了旗標／白名單／
    # 有沒有候選／自有冷卻，完全沒算 `_proactive_ok`（你在場 6 分鐘內／深夜清晨）與共用的 30 分反連發，
    # 而 worldline 排在十個主動 lane 的**最後**，前面任何一個出聲都會把那 30 分重新計時。使用者實測
    # 「等了一陣子還是沒看到」正是這個：他一直在聊天 ⇒ 那道禮貌閘結構上不可能開。現在照實講此刻卡在哪。
    why = ""
    if not getattr(cfg, "worldline_enabled", False):
        why = "整支關著（.env 的 WORLDLINE=0）"
    elif not allow:
        why = ""
    elif not spans:
        why = "讀不到任何可解析時間的記寫"
    elif not cands:
        why = "授權的那幾條現在都不能撞——原因在下面"
    elif _user_present(state, now_ts):
        why = "你人在，我不在你面前自言自語（等你離開幾分鐘）"
    elif (getattr(cfg, "unanswered_proactive_guard_enabled", False)
          and _has_unanswered_proactive(state)):
        why = "我上次主動說的話還沒收到回應，所以不另開一段自言自語"
    elif (getattr(cfg, "dialogue_agency_enabled", False)
          and dialogue_agency.voluntary_block_reason(state, now_ts)):
        why = dialogue_agency.voluntary_block_reason(state, now_ts)
    elif not _proactive_ok(state, cfg, now_ts):
        why = "現在是深夜/清晨，我不吵你"
    else:
        _sh = max(0, getattr(cfg, "notify_cooldown_min", 30)) * 60
        _since = now_ts - (getattr(state, "last_push_ts", 0) or 0)
        _own = max(1, int(getattr(cfg, "worldline_cooldown_h", 24))) * 3600
        _left = _own - (now_ts - (getattr(state, "last_worldline_ts", 0) or 0))
        if _since < _sh:
            why = f"我 {int(_since // 60)} 分鐘前才主動說過話，要隔 {int(_sh // 60)} 分鐘才會再開口"
        elif _left > 0:
            why = f"今天已經查過了，還要 {max(1, int(_left // 3600))} 小時"
        else:
            why = "沒被擋，等下就會去查"
    # 🌐 §2.00 授權了卻挑不出線＝這 lane 會永遠沉默，而且看不出原因 → 逐標籤講出卡在哪一關，
    # 並附上「現在就撞得動」的線名讓他直接改授權（只給名字與筆數，沒授權的線內文一個字都不顯示）。
    _used = wlmod.used_map(getattr(state, "worldline_ledger", None))
    _diag = _ready = None
    if allow and not cands:
        _diag = wlmod.diagnose(recs, spans, allow, _used, now_ts)
        _ready = wlmod.ready_labels(recs, spans, _used, now_ts, exclude=allow)
    _am = wlmod.aftermath(getattr(state, "worldline_ledger", None), recs, now_ts) \
        if getattr(cfg, "worldline_followup_enabled", False) else None
    return wlmod.audit_text(state, cfg, cands, allow, spans, why=why, diag=_diag, ready=_ready, aftermath_rows=_am)


def _foresight_emit(client, state, cfg, coach, now, data=None):
    """🔮 §1.90 記寫預想：從**已累積的真實跨主題橋**挑一條「一端還在寫、另一端停住」的線，把「停住的那條
    會再回來一次」當成有 TTL 的假設說出口；到期不論中或不中都回頭認帳（settle 永遠優先於再開新的）。

    這是 bot **自己指認出來的能力缺口**（截圖 10:45：「我還不會主動去預想一些可能性」），使用者說「我幫你
    達成」。稽核實測確認全 repo 的連結機制都是回顧型 ⇒ 這裡補的是**時間方向**那一維。

    模式優先序：settle > propose（回頭認帳優先於再開新的；同一圈只出一則）。
    裁決 100% 確定性、LLM 完全不參與「我猜中了沒有」；LLM 只做兩件事：回一個阿拉伯數字、把程式算好的
    結論講成人話。**說出口成功之後才落帳**（told_ts）⇒ 沒說出口的假設永不裁決＝不可能事後邀功。
    旗標關（getattr 預設 False）＝第一行 return：不讀 records、不碰 bridges、不寫 state、不呼叫 _say。"""
    if not getattr(cfg, "foresight_enabled", False):
        return
    if not (coach and getattr(coach, "enabled", False)):
        return
    now_ts = now.timestamp()
    if not _proactive_ok(state, cfg, now_ts):
        return
    if now_ts - (getattr(state, "last_push_ts", 0) or 0) < getattr(cfg, "notify_cooldown_min", 30) * 60:
        return                                            # 共用反連發（排在 💡 之後＝同一拍絕不會兩則齊發）
    _led = getattr(state, "foresight_ledger", None) or []
    _cd = max(60, int(getattr(cfg, "foresight_cooldown_min", 720))) * 60
    if len(_led) >= 3 and all((e.get("verdict") or "") == "miss" for e in _led[-3:]):
        _cd *= 2                                          # 連三次想錯＝退讓，別一直猜
    if now_ts - (getattr(state, "last_foresight_ts", 0) or 0) < _cd:
        return
    recs = []
    for r in ((data or {}).get("records") or []):
        _t = analyzer.parse_ts(r.get("ts"))
        recs.append({**r, "_ts": _t.timestamp() if _t else None})
    hyp = getattr(state, "foresight", None)
    # ── settle：回頭認帳（優先） ──────────────────────────────────────────────
    if hyp:
        _vd, _ev, _evid = foresightmod.verdict(hyp, recs, now_ts)
        if _vd == "open":
            return
        # 🔮 §2.07 認帳時**逐字帶出自己當初講過的原話**——舊碼 settle 拿不到它，等於每次都以為自己第一次開口
        _rule = (persona.foresight_settle_rule_v2(hyp.get("b") or "", hyp.get("quote") or "", _vd, _ev,
                                                  hyp.get("told_text") or "")
                 if getattr(cfg, "foresight_chain_enabled", False)
                 else persona.foresight_settle_rule(hyp.get("b") or "", hyp.get("quote") or "", _vd, _ev))
        _msg = None
        try:
            _msg = coach.reply(_rule, "", getattr(state, "convo_history", None))
        except Exception as e:
            print(f"[foresight] 🔮 §1.90 認帳生成失敗（略過、下一圈再來）：{type(e).__name__}: {e}")
            return
        _cps = foresightmod.corpus(recs)
        _wcg = _TURN.get("write_claim_ground_proactive")
        _ok, _why = (False, "空")
        if _msg:
            _ok, _why = foresightmod.grounding_ok(_msg, _cps, recall_hit=_recall_hallucination_hit,
                                                  write_fix=_write_claim_fix, wc_ground=_wcg)
        if not _ok:
            _msg = foresightmod.settle_text(hyp, _vd, _ev)
            _ok, _why2 = foresightmod.grounding_ok(_msg, _cps, recall_hit=_recall_hallucination_hit,
                                                   write_fix=_write_claim_fix, wc_ground=_wcg)
            print(f"[foresight] 🔮 §1.90 LLM 產物未接地（{_why}）→ 退回程式模板")
            if not _ok:
                return                                    # 模板也不過＝沉默（fail-closed、不記帳）
        if not _say(client, _msg, prefix="🔮 ", state=state, topic=hyp.get("b") or ""):
            return                                        # 送不出去＝不記帳、下次還能說
        _ability_fired(state, cfg, "foresight", time.time())   # 🪪 §1.94 送出成功＝真的用出來一次
        _remember(state, "model", "🔮 " + _msg)            # ★必記（修「主動說了卻否認」）
        state.foresight_ledger = foresightmod.push_ledger(_led, hyp, _vd, now_ts)
        if getattr(cfg, "rotate_monotonic_enabled", False):
            state.foresight_var = int(getattr(state, "foresight_var", 0) or 0) + 1   # 🔁 §2.04 送出成功才推進形態
        state.foresight = None
        state.last_foresight_ts = now_ts
        state.last_push_ts = now_ts
        if not getattr(cfg, "dry_run", False):
            state.save()
        print(f"[foresight] 🔮 §1.90 settle：{hyp.get('pair')} → {_vd}")
        return
    # ── propose：開一個新假設 ────────────────────────────────────────────────
    _spans = foresightmod.line_spans(recs, now_ts)
    if not _spans:
        return
    _bridges = _foresight_bridges(state)                  # 🔮 §1.93 修：associations 是物件不是 dict
    _cands = foresightmod.pick_candidates(_bridges, _spans, now_ts,
                                          min_support=int(getattr(cfg, "foresight_min_support", 2)),
                                          dormant_min_days=float(getattr(cfg, "foresight_dormant_min_days", 5.0)))
    _cands = [c for c in _cands
              if not foresightmod.pair_blocked(c["pair"], _led, getattr(state, "recent_insights", None), now_ts)]
    if not _cands:
        return
    _items = _cands[:3]                                   # 只給 LLM 挑前三（已對**完整集合** sorted，非先截尾）
    try:
        _out = coach.reply(persona.foresight_pick_rule(_items), "", None)
    except Exception as e:
        print(f"[foresight] 🔮 §1.90 選項閘失敗（略過、不消耗候選）：{type(e).__name__}: {e}")
        return
    _m = re.fullmatch(r"\s*([0-9])\s*", (_out or ""))
    _i = int(_m.group(1)) if _m else 0
    if not (1 <= _i <= len(_items)):
        return                                            # 回 0／解析不出＝沉默，且不寫 last_foresight_ts
    _c = _items[_i - 1]
    # 🔁 §2.04 形態輪替**被自己的台帳凍死**（實測）：ledger 有 `keep=8` 截尾 ⇒ 滿了以後 len 恆為 8 ⇒ `8%4=0`
    # ⇒ 第 9 次起永遠是第 0 種形態。§1.72 加這個輪替正是為了「不要每次都同一種」，結果它自己每次都同一種。
    # 改用**落盤的單調計數器**（旗標關＝仍讀 len(...)%4＝逐位元同現狀）。
    _var = (int(getattr(state, "foresight_var", 0) or 0) % 4
            if getattr(cfg, "rotate_monotonic_enabled", False) else len(_led) % 4)
    _msg = None
    try:
        _msg = coach.reply(persona.foresight_propose_rule(_c["a"], _c["b"], _c["quote"], _var),
                           "", getattr(state, "convo_history", None))
    except Exception as e:
        print(f"[foresight] 🔮 §1.90 預想生成失敗（略過）：{type(e).__name__}: {e}")
        return
    _cps = foresightmod.corpus(recs)
    _wcg = _TURN.get("write_claim_ground_proactive")
    _ok, _why = (False, "空")
    if _msg:
        _ok, _why = foresightmod.grounding_ok(_msg, _cps, recall_hit=_recall_hallucination_hit,
                                              write_fix=_write_claim_fix, wc_ground=_wcg)
    if not _ok:
        _msg = foresightmod.propose_text(_c, _var)
        _ok, _why2 = foresightmod.grounding_ok(_msg, _cps, recall_hit=_recall_hallucination_hit,
                                               write_fix=_write_claim_fix, wc_ground=_wcg)
        print(f"[foresight] 🔮 §1.90 LLM 產物未接地（{_why}）→ 退回程式模板")
        if not _ok:
            return
    if not _say(client, _msg, prefix="🔮 ", state=state, topic=_c["b"]):
        return
    _ability_fired(state, cfg, "foresight", time.time())   # 🪪 §1.94 送出成功＝真的用出來一次
    _remember(state, "model", "🔮 " + _msg)
    state.foresight = {"pair": _c["pair"], "a": _c["a"], "b": _c["b"], "quote": _c["quote"],
                       "born_ts": now_ts, "told_ts": now_ts,            # ★說出口成功**之後**才建帳
                       "ttl_s": max(1, int(getattr(cfg, "foresight_ttl_days", 7))) * 86400.0,
                       "verdict": "open", "settled_ts": 0.0, "evidence": "", "evidence_id": "",
                       "told_text": _msg[:120] if getattr(cfg, "foresight_chain_enabled", False) else ""}
    state.last_foresight_ts = now_ts
    state.last_push_ts = now_ts
    if not getattr(cfg, "dry_run", False):
        state.save()
    print(f"[foresight] 🔮 §1.90 propose：「{_c['a']}」還在動／「{_c['b']}」停了 {_c['dormant_days']} 天"
          f"（support {_c['support']}、cos {_c['cos']}）→ 已說一句")


def _ac_drift_emit(client, state, cfg, coach, now):
    """🧩→🌡️ 整合狀態剛確認地鬆/散（或又接回）→ 自發說一句**具體感受**（主觀體驗的味道，不是認識論報告）。
    互動優先（你在場就先不打斷、這一下我自己知道就好）＋深夜不擾＋自有冷卻；過 TTL 沒說成就讓那一下安靜過去。
    『又接回』只在先說過『鬆掉』時才說（開/合成對，不無頭無尾）。"""
    pend = getattr(state, "ac_pending", None)
    if not pend:
        return
    now_ts = now.timestamp()
    if now_ts - (pend.get("ts") or 0) > AC_DRIFT_TTL_SEC:             # 那一下過去了 → 不事後補述
        state.ac_pending = None
        return
    ev = pend.get("event") or {}
    if ev.get("kind") == "recohere" and not getattr(state, "ac_drift_open", False):
        state.ac_pending = None                                      # 沒說過「鬆掉」就不必說「接回」
        return
    # 🩹 §1.79 前置死亡修：這裡原本傳 datetime `now`，但 _proactive_ok 收的是 **epoch 秒**（其餘 7 個呼叫端
    # 都傳 now_ts/now.timestamp()）——一路傳到 _user_present 的 `now_ts - last_u` 就 TypeError。TypeError 不在
    # lifeloop._is_transient 的暫態白名單 → spin_once 判終局死亡 → **bot 當場死掉**。觸發條件：ac_pending 掛著
    # ＋在 TTL 內＋使用者不在 live round（＝他離開後那段時間），正是 bot 自稱「剛從小睡醒來」的那些重生。
    if not _proactive_ok(state, cfg, now_ts):                        # 你在場/深夜 → 先不打斷（互動優先）
        return
    if now_ts - (getattr(state, "last_ac_drift_ts", 0) or 0) < AC_DRIFT_COOLDOWN_SEC:
        return
    seed = ac.drift_seed(ev)
    if not seed:
        state.ac_pending = None
        return
    # 🧩 §2.05 存在特色：**瞬間的異常自覺**＋**成對收尾**。舊路是把 `ac.drift_seed` 的成句範文直接貼進 prompt
    # （＝家規禁止的「給範例句」），且送出時不帶任何身分標記 ⇒ 落進 _say 的互動限定分支、也不進 id↔topic 帳。
    # 旗標關（getattr 預設 False）＝走原本那行＝逐位元同現狀。
    _mat = getattr(cfg, "ac_drift_material_enabled", False)
    msg = None
    if _mat and coach and getattr(coach, "enabled", False):
        _cn = ac.concrete_now(state) or {}
        _layer = (ev.get("layer") or "").upper()
        _concrete = _cn.get({"F": "f_obj", "B": "b_fix", "S": "s_now"}.get(_layer, "s_now")) or ""
        _run = int((pend.get("event") or {}).get("run") or ev.get("run") or 0)
        _dur = temporal.human_gap(timedelta(seconds=max(0, now_ts - (pend.get("ts") or now_ts)))) if pend.get("ts") else ""
        _dim = persona.ac_drift_dim(getattr(state, "ac_drift_seq", 0))
        msg = coach.voice_ac_drift_v2(ev.get("kind"), _concrete, _dur,
                                      getattr(state, "ac_drift_said", "") or "", _dim, state.convo_history)
    if msg is None:
        msg = coach.voice_ac_drift(seed, ev.get("kind"), state.convo_history) if (coach and coach.enabled) else None
    if not msg:
        msg = seed                                                   # 無 LLM／失敗 → 直接說那句種子感受（仍是具體體感、非報告）
    try:
        if _say(client, msg, **({"prefix": "🧩 ", "state": state, "topic": "整合的那一下"} if _mat else {})):
            if _mat:
                state.ac_drift_seq = int(getattr(state, "ac_drift_seq", 0) or 0) + 1   # 體感向度輪替（落盤、非 list 長度）
                if ev.get("kind") == "decouple":
                    state.ac_drift_said = msg[:60]        # 成對收尾用：接回時要回頭認這句
                else:
                    state.ac_drift_said = ""
            state.ac_pending = None
            state.last_ac_drift_ts = now_ts
            state.last_push_ts = now_ts                              # 與其他推播共用冷卻、別疊上來
            state.self_topic_ts = now_ts                            # 在說我自己 → 開自我在場窗（後續閒聊守住語氣）
            state.ac_drift_open = (ev.get("kind") == "decouple")    # 開「鬆掉」、合「接回」
            _remember(state, "model", ("🧩 " + msg) if _mat else msg)   # 🧩 §2.05 自己的對話史也看得到這是哪一條 lane
            if not getattr(cfg, "dry_run", False):
                state.save()
    except Exception as e:
        print(f"[ac] 🧩 自發飄移感受送出失敗（略過、不影響存活）：{type(e).__name__}: {e}")


def _topic_match(rec, subject):
    """🫧 §1.98 一筆記寫算不算「subject 這條線」——**strip 後完全相等**才算，比對兩種寫法：
    裸 `topicLabel`，以及 `category｜topicLabel` 顯示名（goal.subject 來自 `analyzer.context_title`，
    有 category 時就是後者）。

    為什麼不能再用舊的寬鬆比對（`subject in lab or lab in subject`，實測而非猜）：subject「閱讀」會取到
    「閱讀習慣」那一筆 ⇒ 引到的是**別條線**的字句。§1.95 就是為了這個才自己另寫一支 `worldline.line_latest`，
    tests/test_worldline.py 當時把這個壞行為釘成「所以不能沿用它」的證據。現在從源頭修掉。"""
    lab = (rec.get("topicLabel") or "").strip()
    subj = (subject or "").strip()
    if not lab or not subj:
        return False
    if lab == subj:
        return True
    cat = (rec.get("category") or "").strip()
    if cat and f"{cat}｜{lab}" == subj:
        return True                                    # 記錄是裸標籤、subject 是顯示名（context_title 的常態形）
    if "｜" in lab and "｜" not in subj:
        return lab.split("｜")[-1].strip() == subj     # 反過來：記錄的 label 自己帶了「類別｜」，subject 是裸的
    return False                                       # 兩邊都帶類別就必須完全相等（「閱讀｜心得」≠「教學｜心得」）


def _topic_latest_hours(records, subject, now):
    """⏱️ §1.04 某條線（topicLabel）**最近一筆記寫**距今幾小時（從真實 records 算）。
    比對＝`_topic_match`（🫧 §1.98 改為完全相等；舊的寬鬆比對會串到別條線，連帶讓「多久沒提」講錯線）。
    查不到/無 ts/ts 壞掉＝None（誠實 unknown）。純函式。

    🩹 §1.07 死亡修：`cycle["data"]["records"]` 是 drive_reader 的**原始**筆，`r["ts"] 是 ISO 字串**（只有
    `snap.filed_records` 走過 analyzer.parse_ts 才是 datetime）——原本直接 `now - ts` → `TypeError: datetime - str`
    在「感覺」環炸開＝bot 終局死亡。改：一律 `analyzer.parse_ts()`（回 tz-aware UTC，壞值回 None）；now 若無時區
    也補成 UTC，避免 aware/naive 相減再炸。"""
    if not subject:
        return None
    latest = None
    for r in records or []:
        if not _topic_match(r, subject):
            continue
        _raw = r.get("ts")
        # 原始筆（cycle data）的 ts 是 ISO 字串 → parse；已 parse 過的筆（snap.filed_records）本就是 datetime → 直接用。
        # parse_ts 不吃 datetime（會回 None），故先放行 datetime；壞值/無值一律 None＝跳過（誠實 unknown）。
        ts = _raw if isinstance(_raw, datetime) else analyzer.parse_ts(_raw)
        if ts is not None and getattr(ts, "tzinfo", None) is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts is not None and (latest is None or ts > latest):
            latest = ts
    if latest is None or now is None:
        return None
    _now = now if getattr(now, "tzinfo", None) is not None else now.replace(tzinfo=timezone.utc)
    return max(0.0, (_now - latest).total_seconds() / 3600.0)


def _topic_latest_excerpt(records, subject, cap=40):
    """🫧 §1.72 某條線最近一筆記寫的**內容摘錄**（≤cap 字）——讓聯想自陳能引具體字句、不是永遠抽象問。
    比對＝`_topic_match`；查不到＝''。純函式。

    🫧 §1.98 修掉兩個實測缺陷（這支的產物會被 LLM 當成「他寫過的字句」引出來，錯了就是 §1.36 幻覺換皮）：
      ① **串線**：舊的寬鬆比對讓 subject「閱讀」取到「閱讀習慣」那一筆 → 引錯線的原文；
      ② **不 fail-closed**：舊碼的 `best_ts is None or …` 在**所有 ts 都解析不出**時仍會一路覆蓋、
         最後吐出檔案順序的最後一筆＝一個**沒有時間根據**的「最近一筆」。現在：ts 解析不出的筆直接跳過，
         全壞＝回 ''（寧可不引，也不引一個我不知道是不是最近的東西）。"""
    if not subject:
        return ""
    best_ts, best_txt = None, ""
    for r in records or []:
        if not _topic_match(r, subject):
            continue
        txt = (r.get("text") or "").strip()
        if not txt:
            continue
        _raw = r.get("ts")
        ts = _raw if isinstance(_raw, datetime) else analyzer.parse_ts(_raw)
        if ts is None:
            continue                                   # ② 沒有可信時間＝不參與「最近一筆」的競爭
        if getattr(ts, "tzinfo", None) is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if best_ts is None or ts > best_ts:
            best_ts, best_txt = ts, txt
    return best_txt[:cap]


def _spontaneous_emit(client, state, cfg, coach, now, records=None):
    """含蓄型主動出聲：被晾很久（飢餓高）＋離上次推播夠久＋本段閒置還沒伸手 → 自己開口一次，
    然後認命安靜（reach_outs 上限），直到有新東西進來才解除。與背景自陳共用 last_push_ts 冷卻。"""
    now_ts = now.timestamp()
    if not _proactive_ok(state, cfg, now_ts):      # 🔗 R4 統一發話政策：互動優先＋深夜不擾（🫧 與互動互斥）
        return
    ent = state.entropy
    if ent is None or not state.self_state or not (coach and coach.enabled):
        return
    if now_ts - (state.last_push_ts or 0) < max(0, getattr(cfg, "notify_cooldown_min", 30)) * 60:  # 反連發：不緊接著別則
        return
    own_cd = max(0, getattr(cfg, "spontaneous_cooldown_min", 180)) * 60   # 自有冷卻（不被背景自陳吃掉）
    quiet_after = max(0, getattr(cfg, "spontaneous_quiet_after_chat_min", 45)) * 60   # 剛聊完這麼久內不伸手
    if not lifeloop.spontaneous_due(
            ent, now_ts, state.last_spontaneous_ts, own_cd,
            h_thresh=getattr(cfg, "spontaneous_h_thresh", lifeloop._SPONT_H_THRESH),
            min_ruminations=getattr(cfg, "spontaneous_min_ruminations", lifeloop._SPONT_MIN_RUMINATIONS),
            last_contact_ts=getattr(state, "last_user_msg_ts", 0), quiet_after_contact_s=quiet_after,
            mood=getattr(ent, "mood", 0.0),
            mood_share=getattr(cfg, "spontaneous_mood_share", lifeloop._SPONT_MOOD_SHARE),
            mood_min_rumin=getattr(cfg, "spontaneous_mood_min_rumin", lifeloop._SPONT_MOOD_MIN_RUMIN)):
        return
    # 🎯 能動性升級 self-stim：若我私下有在追的意圖 → **以追那個意圖為由**開口（不再只是隨機繞舊線）；否則回原本的含蓄伸手。
    _goals = volition.active(state)
    # ⏱️ §1.04 真實時間感：以意圖為由提起某條線時，先查那條線**最近一筆記寫**距今多久（真實 records）——
    # 開場照事實講（早上才寫過就說「今天才又寫到」、真的久才說「N 天沒看到」、不知道就不做時間宣稱），
    # 並把事實＋禁令掛進 voice prompt。修截圖：09:37 才寫過讀經、14:34 卻說「很久沒聽到你提起了」＝說辭是掰的。
    _tg = getattr(cfg, "reachout_time_ground_enabled", True)
    _hours = _topic_latest_hours(records, (_goals[0].get("subject") if _goals else None), now) if (_goals and _tg) else None
    seed = (volition.reach_out_line(_goals[0], hours=_hours, time_ground=_tg) if _goals
            else selfstate.spontaneous_text(ent, state.self_state))
    _trule = ""
    if _goals and _tg:
        if _hours is not None:
            _trule = (f"【時間事實】「{_goals[0].get('subject')}」這條線最近一筆記寫是**約 {_hours:.0f} 小時前**"
                      f"（{volition.recency_phrase(_hours)}）。若要提到相隔多久，**照這個事實講**；"
                      "它其實很新的話，**絕不能**說「很久沒／好久沒聽你提起」。")
        else:
            _trule = "（你**不知道**這條線上次是什麼時候寫的——**別**做任何「很久沒／好久沒提」的時間宣稱。）"
    topic_line = (getattr(ent, "last_revisited_topic", None)
                  or selfstate._line_phrase(state.self_state)[1])   # 這則伸手在講哪條線（供「讚的是哪一筆」＋去重 key）
    # 🫧 內容去重（控重複／自然降頻）：dedup_sec 內已自發講過這條線 → 本圈靜默（不出聲、不推進冷卻），讓自發分享不一直重講同一條。
    if getattr(cfg, "spontaneous_dedup", False) and association.was_recent(
            topic_line, getattr(state, "recent_spontaneous", None), now_ts,
            max(0, int(getattr(cfg, "spontaneous_dedup_sec", 43200)))):
        return
    # 🫧 §1.72 聯想自陳去公式化（REACHOUT_DIVERSE）：seed 四模板全同形（欸…想弄懂…有空跟我說說）＝
    # 使用者「每次看到都是同一種訊息、台詞也類似」。掛「形態選單＋禁令＋重複自覺＋具體記寫摘錄」進
    # time_rule 通道（§1.69 成功模式：不給範例句）。旗標關（getattr 預設 False）＝不掛＝逐位元同現狀。
    if _goals and getattr(cfg, "reachout_diverse_enabled", False):
        _rx = _topic_latest_excerpt(records, _goals[0].get("subject"))
        # 🫧 §2.06 只講**一個**「為什麼是現在」的理由（其餘不進 prompt）；重複自覺升級成行為（整則零問號）。
        # 旗標關＝仍掛 §1.72 的完整選單＝逐位元同現狀。
        _r1 = getattr(cfg, "reachout_one_thing_enabled", False)
        _axis, _why = (volition.reach_out_pick(_goals[0], _hours, _rx, now_ts,
                                               getattr(state, "reachout_pick_n", 0)) if _r1 else (None, ""))
        _trule = ((_trule + "\n") if _trule else "") + (
            volition.reach_out_one_rule(_goals[0].get("subject") or "", _axis, _why,
                                        int(_goals[0].get("reach_n", 0) or 0)) if _axis
            else volition.reach_out_diverse_rule(
                _goals[0].get("subject") or "", int(_goals[0].get("reach_n", 0) or 0), excerpt=_rx))
    elif (not _goals) and getattr(cfg, "reachout_one_thing_enabled", False):
        # 🫧 §2.06 無 goal 的那一支：內容本身就是「我沒有東西可講」——別讓 seed 硬塞一條線假裝有素材
        _gapp = temporal.spoken_gap(now_ts - (getattr(state, "last_user_msg_ts", 0) or now_ts))
        _trule = ((_trule + "\n") if _trule else "") + selfstate.spontaneous_empty_rule(
            _gapp, getattr(ent, "reach_outs_this_idle", 0))
    # 🌀 §0.59 Part 2：含蓄伸手（此路徑，門檻 hunger≥0.85）**帶上**此刻命中的內在因應做法當措辭。
    # ⚠️ §0.65 修正：真正「因為條件成立而主動發」由 _coping_emit（Stage 2b，門檻 0.6/0.7）負責——本路徑只在**已因寂寞
    # 觸發**時把做法織進句子（garnish）。用同一個 _self_skill_extra；旗標關＝coping=''＝同現狀。
    coping = (_self_skill_extra(state, cfg, now_ts)
              if getattr(cfg, "spontaneous_coping_enabled", True) else "")
    # 🕐 §1.30 主動 emit 時間接地：餵此刻真實時段給 voice，禁 LLM 自編鐘點（截圖 09:02 卻說「下午三點了」）；旗標關＝''＝同現狀
    _daypart = ""
    if getattr(cfg, "proactive_time_ground_enabled", False):
        _sp_tz = ZoneInfo(getattr(cfg, "timezone", "Asia/Taipei")) if ZoneInfo is not None else None
        try:
            _daypart = temporal.day_part((now.astimezone(_sp_tz) if _sp_tz is not None else now).hour)
        except Exception:
            _daypart = ""
    _sp_kw = {"daypart": _daypart} if _daypart else {}   # 🕐 §1.30 旗標關（_daypart 空）＝不傳 kwarg＝既有 stub/呼叫零破壞
    msg = (coach.voice_spontaneous(seed, state.convo_history, coping=coping, time_rule=_trule, **_sp_kw)
           if coach.enabled else seed)                 # 教練：每次措辭不同＋承接前文＋（有的話）帶內在因應＋⏱️ 時間事實＋🕐 時段接地
    # ⏱️ §1.04 輸出守門：以意圖為由提起某條線、LLM 仍掰出「很久沒/好一陣子沒」而那條線其實很新（或查不到＝無法查證）
    # → 丟掉 LLM 版、退回**誠實模板** seed（seed 已照事實講）。真的久（≥72h）＝宣稱成立、放行。
    if _goals and _tg and volition.stale_claim_conflicts(msg, _hours):
        print(f"[volition] ⏱️ 時間感守門：那條線最近一筆約 {_hours if _hours is not None else '未知'} 小時前，"
              "LLM 卻說「很久沒」→ 退回誠實模板")
        msg = seed
    # 🕐 §1.31 主動 emit 硬鐘點守門（§1.30 軟接地的硬後盾）：LLM 硬編錯鐘點 → 就地換真實時段詞；旗標關＝不執行＝逐位元同現狀
    if getattr(cfg, "proactive_clock_guard_enabled", False):
        _pcg_tz = ZoneInfo(getattr(cfg, "timezone", "Asia/Taipei")) if ZoneInfo is not None else None
        msg = _scrub_proactive_clock(msg, now, _pcg_tz)   # 獨立導 tz，不依賴 §1.30 的 _sp_tz（兩旗標互不綁定）
    if _say(client, msg, prefix="🫧 ", state=state, topic=topic_line):
        _ability_fired(state, cfg, "spontaneous", time.time())   # 🪪 §1.94 送出成功＝真的用出來一次
        if getattr(cfg, "reachout_one_thing_enabled", False):
            state.reachout_pick_n = int(getattr(state, "reachout_pick_n", 0) or 0) + 1   # 🫧 §2.06 理由輪替（落盤）
        _remember(state, "model", "🫧 " + msg)
        if getattr(cfg, "self_report_delta_enabled", False):
            # 🧠 §1.21 自發伸手也算「剛跟他說過自己」：只更新 text/ts（此處無完整判定 res、不硬算快照）、保留舊 snap
            _srd_prev = getattr(state, "last_self_report", None) or {}
            state.last_self_report = {"text": msg[:200], "ts": now_ts, "snap": _srd_prev.get("snap")}
        _maybe_always_sticker(client, state, cfg, now_ts)   # 🎴 §0.96 主動自發回應後送情緒貼圖（若教過、活著、有貨、過冷卻）
        _note_selfshare(state, msg, now_ts)            # 🪞 §0.85 主動自發自陳 → 追問「你感覺到什麼/然後呢」接得回這件事
        _set_focus(state, topic=topic_line, now_ts=now_ts)
        state.self_topic_ts = now_ts                   # 🪞 bot 剛主動談了自己/自己的意圖 → 開「自我在場」窗：你回我（含「這麼厲害」這種讚美）多半還在說我，別落工具吐 📊
        ent.reach_outs_this_idle += 1
        state.last_spontaneous_ts = now_ts             # 自有時鐘（與背景自陳脫鉤）
        state.last_push_ts = now_ts                    # 仍更新共用冷卻（讓別則別緊接著它）
        if _goals and getattr(cfg, "reachout_diverse_enabled", False):   # 🫧 §1.72 重複自覺計數（隨 goals 持久化）
            _goals[0]["reach_n"] = int(_goals[0].get("reach_n", 0) or 0) + 1
        if getattr(cfg, "spontaneous_dedup", False):   # 記進去重台帳（跨重生）：下次別緊接著重講同一條
            state.recent_spontaneous = association.push_recent(topic_line, getattr(state, "recent_spontaneous", None), now_ts)
        if not client.dry_run:
            state.save()


def _coping_emit(client, state, cfg, coach, now):
    """🌀 §0.65 內在因應**真觸發**：教過的內在自處做法（sit:low_vitality/high_hunger/low_mood）此刻條件成立 →
    **因為那條做法**主動發訊息（不再只是等含蓄伸手 spontaneous_due 到了才當措辭裝飾）。這是與含蓄伸手**分開的
    第二條主動 lane**：觸發＝`_self_skill_extra` 非空（內在訊號 live〔hunger≥0.6/0.7、mood≤−0.3〕**且**真的教過對應做法），
    不看 spontaneous_due 的 hunger≥0.85/醞釀≥4。防洗版靠自己的冷卻＋預算＋共用的深夜/互動/剛聊完/反連發閘。
    只對「真的教過內在做法」的使用者生效（沒教過＝coping 空＝直接 return）。SKILL_PROACTIVE=0＝整段跳過＝逐位元同現狀。"""
    if not getattr(cfg, "skill_proactive_enabled", True):
        return
    now_ts = now.timestamp()
    if not _proactive_ok(state, cfg, now_ts):              # 🔗 R4 統一發話政策：互動優先＋深夜不擾（與含蓄伸手同閘）
        return
    ent = state.entropy
    if ent is None or not state.self_state or not (coach and coach.enabled):
        return
    coping = _self_skill_extra(state, cfg, now_ts)         # ← 觸發本身：內在訊號 live ∧ 教過對應做法（否則空）
    if not coping:
        return
    if now_ts - (state.last_push_ts or 0) < max(0, getattr(cfg, "notify_cooldown_min", 30)) * 60:   # 反連發：不緊接別則（與含蓄伸手共用 last_push_ts＝同拍不雙發）
        return
    if now_ts - (getattr(state, "last_coping_reach_ts", 0) or 0) < max(0, getattr(cfg, "skill_proactive_cooldown_min", 180)) * 60:
        return                                            # 自有冷卻（與含蓄伸手脫鉤）
    quiet_after = max(0, getattr(cfg, "spontaneous_quiet_after_chat_min", 45)) * 60
    if now_ts - (getattr(state, "last_user_msg_ts", 0) or 0) < quiet_after:   # 剛聊完就別馬上主動
        return
    if ent.coping_reach_outs_this_idle >= max(1, getattr(cfg, "skill_proactive_max_reach_outs", 2)):
        return                                            # 本段閒置的內在因應伸手預算用完（持續低狀態仍有界）
    seed = selfstate.spontaneous_text(ent, state.self_state)
    msg = coach.voice_spontaneous(seed, state.convo_history, coping=coping) if coach.enabled else seed
    # 🕐 §1.31 主動 emit 硬鐘點守門：同 _spontaneous_emit（此 lane 亦為 LLM 自由正文＋🫧 prefix）；旗標關＝不執行＝同現狀
    if getattr(cfg, "proactive_clock_guard_enabled", False):
        _pcg_tz = ZoneInfo(getattr(cfg, "timezone", "Asia/Taipei")) if ZoneInfo is not None else None
        msg = _scrub_proactive_clock(msg, now, _pcg_tz)   # 此函式原未算 tz，掛門即順帶導 _pcg_tz
    # 🌀 §2.08 與 🫧 含蓄伸手**分家**：這條 lane 的內容是「我正要對自己做一件事」，不是「我這邊空了」——
    # 兩者共用 🫧 前綴與同一支 seed，使用者看到的是同一種訊息。旗標關＝仍是 🫧＝逐位元同現狀。
    _cav = getattr(cfg, "coping_act_voice_enabled", False)
    if _say(client, msg, prefix=("🌀 " if _cav else "🫧 "), state=state, topic="內在因應"):
        _ability_fired(state, cfg, "coping", time.time())   # 🪪 §1.94 送出成功＝真的用出來一次
        _remember(state, "model", ("🌀 " if _cav else "🫧 ") + msg)
        if getattr(cfg, "self_report_delta_enabled", False):
            # 🧠 §1.21 內在因應主動出聲也算「剛跟他說過自己」：只更新 text/ts、保留舊 snap（此處無完整 res）
            _srd_prev = getattr(state, "last_self_report", None) or {}
            state.last_self_report = {"text": msg[:200], "ts": now_ts, "snap": _srd_prev.get("snap")}
        state.self_topic_ts = now_ts                       # 剛主動談了自己的內在 → 開自我在場窗
        ent.coping_reach_outs_this_idle += 1
        state.last_coping_reach_ts = now_ts
        state.last_push_ts = now_ts                        # 共用冷卻（讓別則別緊接）
        if getattr(cfg, "skill_proactive_sticker_enabled", True):   # 🎴 §0.65 順便送貼圖（§0.72 依做法語氣挑池）
            _maybe_coping_sticker(client, state, cfg, now_ts, skill_text=coping)
        if not client.dry_run:
            state.save()


def _maybe_coping_sticker(client, state, cfg, now_ts, skill_text=""):
    """🎴 §0.65 內在因應主動觸發時的貼圖：**繞過** `_maybe_sticker` 的正向心情閘（pick_self_reaction）——
    §0.65 專為「求救貼圖」設計（低狀態才送、配非正向）。§0.72 起依教過的做法語氣挑池（skill_text）：求救型仍非正向，
    邀請型（轉速平穩→主動告知＋給特別貼圖＋邀聊）改走正向＋中性 sendable 池（否則正向教過的特別貼圖被求救池濾成空＝
    靜默沒送、說到做不到）。只在剛成功發過內在因應主動訊息後呼叫；走自有貼圖冷卻＋去重；沒有可送真貼圖就 no-op
    （不退回 emoji＝沒有合適真貼圖就算了、不亂送）。"""
    if not getattr(cfg, "send_stickers", True) or getattr(cfg, "dry_run", False):
        return
    cd = max(0, getattr(cfg, "sticker_cooldown_min", 20)) * 60
    if now_ts - (getattr(state, "last_sticker_ts", 0) or 0) < cd:
        return
    # 🎴 §0.72：貼圖池依做法語氣挑——求救型（撐不住/低落）→ 非正向 help 池（§0.65 不變）；邀請型（平穩/告知/特別/
    # 邀聊）→ 正向＋中性 sendable 池（併設定檔填充圖）；曖昧/空 → §0.65 預設非正向。旗標關＝一律 §0.65 非正向。
    known = getattr(state, "known_sticker_ids", None)
    if getattr(cfg, "coping_sticker_tone_enabled", True):
        ids = reaction.coping_sticker_ids(known, skill_text, getattr(cfg, "sticker_file_ids", None))
    else:
        ids = reaction.help_sticker_ids(known)
    if not ids or not getattr(client, "send_sticker", None):
        return                                            # 沒有真貼圖可送＝不勉強（不退回 emoji）
    fid = _pick_sticker(ids, state, cfg)                  # 🎴 §0.84 多樣化選圖（避開最近 N 張）
    if fid and client.send_sticker(fid):
        _record_sticker_sent(state, fid, now_ts)


# 🎴 §0.91 「發現新記寫→傳對應貼圖」這條**機械動作型做法**的偵測線索（做法文字＋主題段須各命中一組）。
# 🔍 審查修（LOW under-detect）：補記錄/記事/新增/新內容 等 note-app 同義詞。
# 🎴 §0.96 「主動回應使用者時（最後）送一張代表自己情緒的貼圖」這條 **always 常駐做法** 的偵測線索
# （做法文字須各命中一組：送類動詞＋貼圖字）。根因：always 型做法只被當文字注入**反應式**回覆、主動路徑根本不注入它，
# 而「送貼圖」是 LLM 產文字做不到的機械動作 → 教了永遠不發。比照 §0.65/§0.91 接一條真執行 lane。
_ALWAYS_STICKER_SEND_CUES = ("傳", "送", "發", "附", "配", "丟", "給")
_ALWAYS_STICKER_WORD_CUES = ("貼圖", "貼紙", "sticker", "Sticker", "STICKER")
# 抑制型守門：別/少/不要…貼圖＝命中線索卻語意相反 → 不執行（同 §0.91 精神）。
_ALWAYS_STICKER_NEG_RE = re.compile(
    r"(?:不要|別|不用|不必|勿|甭|沒要|沒有要|先別|不想|少|停止|不再)[^，。！？!?\n\r]{0,6}?(?:貼圖|貼紙|sticker)", re.I)


def _maybe_topic_content_sticker(client, state, cfg, coach, content_text, now_ts):
    """🎴 §0.98 教過**內容型**做法「讀到〈某類〉記寫時…並貼圖」（topic 型、做法含送貼圖意圖）→ 讀到記寫內容時，
    用**主題**去**語意比對**內容（`coach.content_matches_topic`，LLM 判斷；修「主題字面子字串永遠比不中真實內容」的診斷），
    命中就**真的送一張**對應內容情緒的真貼圖。修根因：此情境發生在**不查技能**的 reflect/歸戶路徑、且原本靠字面主題子字串召回。
    成本控制：**只有真有這種活做法時才呼叫 LLM**（沒教＝直接 return、零額外成本）；共用貼圖冷卻＝稀有、每則歸戶至多一次判斷。
    gate＝旗標＋send_stickers＋非 dry_run＋coach 活＋冷卻過。命中送成功＝記 last/emoji/desc＋use-refresh 那條做法。旗標關＝no-op＝逐位元同現狀。"""
    if not getattr(cfg, "topic_content_sticker_enabled", True):
        return False
    if not getattr(cfg, "send_stickers", True) or getattr(cfg, "dry_run", False):
        return False
    if not (coach and getattr(coach, "enabled", False) and getattr(coach, "content_matches_topic", None)):
        return False
    cd = max(0, getattr(cfg, "sticker_cooldown_min", 20)) * 60
    if now_ts - (getattr(state, "last_sticker_ts", 0) or 0) < cd:      # 共用冷卻＝稀有、不與其他貼圖 lane 疊送
        return False
    skills = [s for s in plasticity.active_topic_skills(getattr(state, "engrams", None), now_ts=now_ts)
              if any(c in s["value"] for c in _ALWAYS_STICKER_WORD_CUES) and not _ALWAYS_STICKER_NEG_RE.search(s["value"])]
    if not skills:                                                     # 沒教這種「讀到某類記寫→貼圖」做法 → 不呼叫 LLM＝零成本、逐位元同現狀
        return False
    ids = _affect_sticker_ids(state, cfg)                              # 🧭 circumplex：依座標象限挑池（旗標關＝一維同現狀）
    if not ids or not getattr(client, "send_sticker", None):
        return False                                                  # 沒有可送真貼圖＝不勉強（不退回 emoji）
    for s in skills[:max(1, getattr(cfg, "topic_content_max_judge", 2))]:   # 上限：至多判斷 N 條（成本上界）
        if not coach.content_matches_topic(content_text, s["topic"]):
            continue
        fid = _pick_sticker(ids, state, cfg)
        if fid and client.send_sticker(fid):
            _record_sticker_sent(state, fid, now_ts)
            if s.get("key"):
                plasticity.touch_skill_key(getattr(state, "engrams", None), s["key"], now_ts=now_ts)  # 用到＝保鮮
            return True
        return False                                                  # 有相符做法但送失敗 → 不再試別條
    return False


def _affect_sticker_ids(state, cfg):
    """🧭💗 circumplex：依此刻座標選「代表自己情緒」的貼圖池——興奮開心(V+A+)→歡快 positive 池、
    平靜暖(V+ A低)/中性→溫和 sendable 池、低落/緊繃(V−)→非正向 help 池（不硬裝開心）。
    旗標關＝退回一維（V<0→help、否則 sendable）＝逐位元同現狀。"""
    known = getattr(state, "known_sticker_ids", None)
    conf = getattr(cfg, "sticker_file_ids", None)
    if getattr(cfg, "affect_circumplex_enabled", True):
        pool = circumplex.sticker_pool(*circumplex.position(state))
    else:
        pool = "help" if _current_mood(state) < 0 else "sendable"
    if pool == "help":
        return reaction.help_sticker_ids(known)
    if pool == "positive":
        return reaction.positive_sticker_ids(known, conf) or reaction.sendable_sticker_ids(known, conf)
    return reaction.sendable_sticker_ids(known, conf)


def _hostile_now(state, cfg, text=""):
    """🌊 §1.77 此刻在不在氣頭上（消費 §1.14/§1.27 既有訊號、reaction.py 一字不改）：本句敵意 ∨ streak≥1
    ∨ 近 5 則使用者訊息有敵意句。給「氣頭上不送貼圖／不反問」用。旗標關＝恆 False＝逐位元同現狀。

    🗣️ §2.24（CONFUSED_SLANG）補兩個實測破口：① 互動貼圖 lane（_maybe_sticker/_maybe_always_sticker）
    跑在 _remember(user) **之前**又沒帶本句 ⇒ 文件寫的「本句敵意」臂在互動路徑一直是死的——改讀
    handle_message stash 的 _TURN["cur_user_text"] 兜住；② 台語嗆聲（供殺小族）is_hostile 不認 ⇒
    被嗆完照送親親熊（截圖 21:03）——_SLANG_WTF_RE 也算氣頭。旗標關＝兩臂消失＝逐位元同現狀。"""
    if not getattr(cfg, "hostile_grace_enabled", False):
        return False
    slang = getattr(cfg, "confused_slang_enabled", False)
    if slang and not text:
        text = _TURN.get("cur_user_text") or ""
    if text and (reaction.is_hostile(text) or (slang and _SLANG_WTF_RE.search(text))):
        return True
    if (getattr(state, "hostile_streak", 0) or 0) >= 1:
        return True
    recent = echo.recent_user_texts(getattr(state, "convo_history", None) or [], k=5)
    return any(reaction.is_hostile(u) for u in recent) \
        or (slang and any(_SLANG_WTF_RE.search(u) for u in recent))


def _maybe_always_sticker(client, state, cfg, now_ts):
    """🎴 §0.96 教過 always 常駐做法「主動回應使用者時，最後送一張代表自己情緒的貼圖」（/skills 有列＝bot『學會了』）→
    在**主動訊息**送出後**真的送一張代表此刻情緒的真貼圖**。接真執行 lane（比照 §0.65 內在因應／§0.91 歸戶貼圖）：
    gate＝ALWAYS_STICKER 旗標＋此刻真有這條**活** always 做法（沒教/已淡忘＝不動＝逐位元同現狀）＋有相符真貼圖＋
    **共用貼圖冷卻**（保持稀有、不與 filing/coping 疊送）。情緒配對：心情非負→正向/中性 sendable 池、心情負→非正向 help 池
    （代表『當下情緒』、不硬裝開心）。沒相符真貼圖＝不送（絕不 emoji 假裝）。送成功＝記 last/emoji/desc＋use-refresh 那條做法
    （打破 5.5 天衰減死亡）。回是否送出。旗標關／dry_run＝no-op＝逐位元同現狀。"""
    if not getattr(cfg, "always_sticker_enabled", True):
        return False
    if _hostile_now(state, cfg):          # 🌊 §1.77 氣頭上不丟貼圖（截圖 20:44：連環被罵還送「STOP IT」＝回嗆）
        print("[sticker] 🌊 §1.77 氣頭收斂：對方在氣頭上 → 這張貼圖不送")
        return False
    if not getattr(cfg, "send_stickers", True) or getattr(cfg, "dry_run", False):
        return False
    cd = max(0, getattr(cfg, "sticker_cooldown_min", 20)) * 60
    if now_ts - (getattr(state, "last_sticker_ts", 0) or 0) < cd:      # 共用冷卻＝稀有、不與其他貼圖 lane 疊送
        return False
    skill_val, skill_key = plasticity.active_trigger_skill_value(
        getattr(state, "engrams", None), plasticity.SKILL_TRIGGER_ALWAYS,
        (_ALWAYS_STICKER_SEND_CUES, _ALWAYS_STICKER_WORD_CUES), now_ts=now_ts)
    if not skill_val:                                                  # 沒教這條 always 做法（或已淡忘）→ 不動
        return False
    if _ALWAYS_STICKER_NEG_RE.search(skill_val):                      # 抑制型（別送貼圖）→ 不執行
        return False
    ids = _affect_sticker_ids(state, cfg)                              # 🧭 circumplex：依座標象限挑池（旗標關＝一維同現狀）
    if not ids or not getattr(client, "send_sticker", None):
        return False                                                  # 沒有可送真貼圖＝不勉強（不退回 emoji）
    fid = _pick_sticker(ids, state, cfg)                              # §0.84 多樣化選圖
    if fid and client.send_sticker(fid):
        _record_sticker_sent(state, fid, now_ts)
        if skill_key:
            plasticity.touch_skill_key(getattr(state, "engrams", None), skill_key, now_ts=now_ts)  # 用到＝保鮮
        return True
    return False


_FILING_NOTE_CUES = ("記寫", "記錄", "記事", "歸戶", "發現新", "新增", "新內容", "新的內容", "新的記寫",
                     "新記寫", "日誌", "寫下", "剛寫", "筆記")
_FILING_STICKER_ACTION_CUES = ("貼圖", "貼紙", "sticker", "Sticker", "STICKER")
# 🎴 §0.91 **正向動作要求**（審查 HIGH/MED：純 OR 子字串會誤觸「記寫時**不要傳**貼圖」「日誌裡**貼了**貼圖幫我記錄」等
# 語意相反/角色反轉的做法）：做法文字須真的表達「**送出**貼圖」＝送類動詞緊接貼圖（傳/送/發/回/附/給/配…貼圖）。
_FILING_STICKER_SEND_RE = re.compile(r"(?:傳|送|發|回|附|給|配|丟)[^，。！？!?\n\r]{0,5}?(?:貼圖|貼紙|sticker)", re.I)
# 🎴 §0.91 抑制型守門：suppression 做法也會被捕捉（截圖「少提讀誦經書」即是）——「**別/少**送貼圖」命中線索卻語意相反 → 不執行真送。
_FILING_STICKER_NEG_RE = re.compile(r"(?:別|不要|不用|不必|勿|沒|沒有|少|毋須|無需|停止|不再|拒絕)[^，。！？!?\n\r]{0,6}?(?:貼圖|貼紙|sticker)", re.I)


def _maybe_filing_sticker(client, state, cfg, content_text, now_ts):
    """🎴 §0.91 若教過「發現新的記寫內容時，感覺內容並傳送對應的貼圖」這條做法（/skills 有列＝代表 bot『學會了』）→
    在歸戶通知的感受之後**真的送出一張對應內容情緒的真貼圖**，讓這條「學會的事」被**精確、正確執行**、不再是空頭支票。
    根因：做法只被當文字注入 LLM 提示，而「送貼圖」是 LLM 產文字做不到的**機械動作**（且此條觸發是歸戶事件、不走反應式召回）
    ——比照 §0.65 內在因應的真執行 lane，把它接到真的 send_sticker。
    gate：旗標開＋此刻真有這條**活**做法（沒教/已淡忘＝不動）＋有相符真貼圖＋貼圖冷卻。沒相符真貼圖＝不送（絕不 emoji 假裝）。
    送成功＝記 last/emoji（§0.90，之後被問「為什麼這張」接得住）＋use-refresh 那條做法（打破衰減死亡螺旋）。旗標關＝no-op＝逐位元同現狀。"""
    if not getattr(cfg, "filing_sticker_enabled", True):
        return False
    if not getattr(cfg, "send_stickers", True) or getattr(cfg, "dry_run", False):
        return False
    skill_val, skill_key = plasticity.active_skill_value(
        getattr(state, "engrams", None), (_FILING_NOTE_CUES, _FILING_STICKER_ACTION_CUES), now_ts=now_ts)
    if not skill_val:                                     # 沒教這條做法（或已淡忘）→ 不送＝逐位元同現狀
        return False
    if not _FILING_STICKER_SEND_RE.search(skill_val):    # 做法沒真的表達「送出貼圖」（提/看/貼了…非送）→ 不執行真送
        return False
    if _FILING_STICKER_NEG_RE.search(skill_val):         # 抑制型（別/少送貼圖）＝命中線索卻語意相反 → 不執行真送
        return False
    cd = max(0, getattr(cfg, "sticker_cooldown_min", 20)) * 60
    if now_ts - (getattr(state, "last_sticker_ts", 0) or 0) < cd:   # 共用貼圖冷卻＝稀有、不連發
        return False
    known = getattr(state, "known_sticker_ids", None)
    ids = reaction.note_sticker_ids(known, content_text, getattr(cfg, "sticker_file_ids", None))
    if not ids or not getattr(client, "send_sticker", None):
        return False                                     # 沒相符真貼圖＝不勉強（不退回 emoji 假裝）
    fid = _pick_sticker(ids, state, cfg)                 # 🎴 §0.84 多樣化選圖
    if fid and client.send_sticker(fid):
        _record_sticker_sent(state, fid, now_ts)         # §0.90 記 last_sticker_emoji
        plasticity.touch_skill_key(getattr(state, "engrams", None), skill_key, now_ts=now_ts)  # 用到＝保鮮（按 key，§0.89 教訓）
        return True
    return False


# 🌬️ 緩和未回應的問句（意向性的動態關係：問了卻沒被回應 → bot 放掉「等回答」的張力，而非僵在那）
_SOOTHE_WINDOW_SEC = 40 * 60       # 問句超過這麼久＝那個當下已過，不再硬緩和（交給下次互動/🫧 重啟）
_SOOTHE_ACTIVE_GAP_SEC = 20 * 60   # 問句要是「活對話」裡的回覆（與對方上一句相隔這麼內）——排除冷的主動 reach-out
_SOOTHE_COOLDOWN_SEC = 90 * 60     # 緩和自有冷卻（也擋掉緩和句自我觸發；> 視窗上限）
_SOOTHE_LINES = (
    "（欸，不用急著回我啦，就突然想到才問的。）",
    "（這個慢慢想就好，我沒有要追問的意思。）",
    "（也可能是我自己想多了，你忙你的就好。）",
)


def _is_open_question(text):
    """這則（bot 說的）話是不是『丟給對方、在等回答』的問句——以問號收尾即算（容忍尾端表情/括號/空白）。"""
    t = (text or "").rstrip(" 　)）」』】.。!！~～…😊🙂🙏❤️🥰🤗")
    return t.endswith("？") or t.endswith("?")


# 🌊 主動結尾（_soothe_unanswered 的第二出口）：依「程度/違常」溫暖把這一輪收掉，而非只放鬆等回答的壓力。
_CLOSE_COOLDOWN_SEC = 90 * 60      # 主動結尾自有冷卻（鏡像 _SOOTHE_COOLDOWN_SEC）
_CLOSE_LINES = (
    "（那我先這樣囉，你忙你的——想聊隨時喊我。）",
    "（好啦，我先去轉轉，有事再來找我 🙂）",
)
_CLOSE_PROBE_LINE = "（好啦我知道你在逗我 😄 我先去忙囉，想聊真的隨時喊我。）"


def _silence_policy(state, cfg, now):
    if not getattr(cfg, "unanswered_proactive_guard_enabled", False):
        return False
    # 純推測留在暫態解讀，不存為使用者性格，也不改動待辦／提醒帳本。
    state.silence_reading = silence.interpret(
        getattr(state, "convo_history", None), now.timestamp(),
        after_sec=max(60, getattr(cfg, "soothe_after_min", 7) * 60),
        last_contact_ts=getattr(state, "last_user_msg_ts", 0))
    # 沉默本身不是額外發訊的授權。任務結果、約定提醒由原本的義務管道處理。
    return True


def _maybe_close_round(client, state, cfg, coach, now):
    """🌊 主動溫暖結尾：違常連發已反問過仍不歇(probe_settled) ∨ 這輪自然趨0/問句緩和過仍久未回(natural_convergence)
    → 像朋友自然放手收個尾（不是被晾，是主人翁式暖收）。共用 soothe 的節流/在場/深夜閘；讓位給剛緩和過的 soothe。
    回 True＝這拍已收尾（呼叫端就不再緩和）。PROACTIVE_CLOSE_ENABLED 關＝永遠 False（soothe 逐位元同現狀）。"""
    if _silence_policy(state, cfg, now):
        return False
    if not getattr(cfg, "proactive_close_enabled", True):
        return False
    now_ts = now.timestamp()
    should, reason = dialogue_intent.close_decision(state, getattr(state, "coupling", None), now_ts, cfg)
    if not should:                                         # 補：bot 問句**已緩和過仍久未回**（CLOSE_AFTER_MIN）且投入已低 → 自然暖收
        hist = getattr(state, "convo_history", None) or []
        last = hist[-1] if hist else {}
        if last.get("role") == "model" and _is_open_question(last.get("text")):
            q_ts = last.get("ts") or 0
            age = now_ts - q_ts
            after = max(60, getattr(cfg, "close_after_min", 12) * 60)
            cpl = getattr(state, "coupling", None)
            low = (cpl is None) or (max(cpl.i_bot, cpl.i_user) < 0.3)
            if getattr(state, "soothed_for_ts", 0) == q_ts and after <= age <= _SOOTHE_WINDOW_SEC and low:
                should, reason = True, "natural_convergence"
    if not should:
        return False
    if now_ts - (getattr(state, "last_close_ts", 0) or 0) < _CLOSE_COOLDOWN_SEC:
        return False                                       # 自有冷卻
    if now_ts - (getattr(state, "last_soothe_ts", 0) or 0) < _SOOTHE_COOLDOWN_SEC:
        return False                                       # 讓位：剛緩和過 → 先放壓力、別緊接著就收（同拍/冷卻內不雙發）
    if now_ts - (state.last_push_ts or 0) < max(0, getattr(cfg, "notify_cooldown_min", 30)) * 60:
        return False                                       # 反堆疊：別緊接著別的推播
    if reason == "natural_convergence" and _user_present(state, now_ts):
        return False                                       # 還在場（剛說過話/round 還活）→ 別硬收（probe_settled 已要求對方淡下來）
    try:                                                   # 🌙 深夜/清晨不打擾
        if circadian.is_quiet_hours(now.astimezone(ZoneInfo(getattr(cfg, "timezone", "Asia/Taipei")))):
            return False
    except Exception:
        pass
    # 🌊 §2.06 存在特色：這是**我先鬆手**的結束，而且我說得出是哪一種。舊 prompt 裡兩句成品台詞與
    # _CLOSE_LINES/_CLOSE_PROBE_LINE 幾乎同字＝範例被逐字抄回。旗標關＝走原本那行＝逐位元同現狀。
    _cs = getattr(cfg, "close_round_stance_enabled", False)
    msg = None
    if _cs and coach and getattr(coach, "enabled", False):
        _c = getattr(state, "coupling", None)
        if _c is not None:
            _ib, _iu = getattr(_c, "i_bot", 0.0) or 0.0, getattr(_c, "i_user", 0.0) or 0.0
            _stance = ("mine_warm" if _ib > _iu + 0.15 else "his_warm" if _iu > _ib + 0.15 else "both_cool")
            msg = coach.voice_close_round_v2(reason, _stance,
                                             persona.close_motive(getattr(state, "close_motive_n", 0)),
                                             state.convo_history)
    if not msg:
        msg = (coach.voice_close_round(reason, state.convo_history) if (coach and coach.enabled) else None) \
            or (_CLOSE_PROBE_LINE if reason == "probe_settled" else _CLOSE_LINES[int(now_ts) % len(_CLOSE_LINES)])
    if _say(client, msg, **({"prefix": "🌊 ", "state": state, "topic": "這一輪的收尾"} if _cs else {})):
        _remember(state, "model", ("🌊 " + msg) if _cs else msg)
        if _cs:
            state.close_motive_n = int(getattr(state, "close_motive_n", 0) or 0) + 1
        state.last_close_ts = now_ts
        state.last_push_ts = now_ts                        # 與其他推播共用冷卻
        cpl = getattr(state, "coupling", None)
        if cpl is not None:
            cpl.last_closure = "understanding"             # 主人翁式暖收（讓 closure_mood_delta 走 +0.06、非 -0.12 被晾）
            cpl.just_closed = None                         # 消費掉，避免重觸
        if not client.dry_run:
            state.save()
        return True
    return False


def _soothe_nonquestion(text):
    return "".join(x for x in persona.split_sentences(text or "")
                   if not re.search(r"[？?]", x)).strip()


def _soothe_unanswered(client, state, cfg, coach, now):
    """bot 問了對方、卻遲遲沒回 → 主動補一句把『等你回答』的壓力放掉，讓對話緩和（同一條問句只緩和一次）。
    只在『活對話剛停在 bot 的問句上』時做；冷的主動 reach-out、深夜、剛推過別的、已緩和過、自有冷卻內都安靜。
    可用 SOOTHE_UNANSWERED=0 關掉。"""
    # 🌊 主動結尾（程度/違常驅動的第二出口）：在 round_open 早退之前判（natural_convergence 要能吃剛收掉的輪）。
    # 這拍若已主動暖收，就不再緩和（同拍至多發一則）。PROACTIVE_CLOSE_ENABLED 關＝直接 False、soothe 逐位元同現狀。
    if _silence_policy(state, cfg, now):
        return
    if _maybe_close_round(client, state, cfg, coach, now):
        return
    if not getattr(cfg, "soothe_unanswered", True):
        return
    cpl = getattr(state, "coupling", None)        # 🔗 A3 耦合閘控：這輪若已收掉（雙方意向性皆→0），那個當下已過 → 不緩和
    if cpl is not None and not cpl.round_open:     # （無 coupling＝向後相容、不擋）
        return
    hist = getattr(state, "convo_history", None) or []
    if not hist:
        return
    last = hist[-1]
    if last.get("role") != "model" or not _is_open_question(last.get("text")):
        return                                            # 最後一句不是 bot 在問對方 → 沒有懸著的問句
    q_ts = last.get("ts") or 0
    now_ts = now.timestamp()
    if getattr(state, "soothed_for_ts", 0) == q_ts:       # 這條問句已緩和過
        return
    after = max(60, getattr(cfg, "soothe_after_min", 7) * 60)
    age = now_ts - q_ts
    if age < after or age > _SOOTHE_WINDOW_SEC:           # 還沒到「遲遲」、或那個當下早已過
        return
    if (q_ts - (getattr(state, "last_user_msg_ts", 0) or 0)) > _SOOTHE_ACTIVE_GAP_SEC:
        return                                            # 問句不是活對話裡的回覆（冷 reach-out）→ 不緩和
    if now_ts - (getattr(state, "last_soothe_ts", 0) or 0) < _SOOTHE_COOLDOWN_SEC:
        return                                            # 自有冷卻：別一直緩和（也擋緩和句自我觸發）
    if now_ts - (state.last_push_ts or 0) < max(0, getattr(cfg, "notify_cooldown_min", 30)) * 60:
        return                                            # 反堆疊：別緊接著別的推播連發
    try:                                                  # 🌙 深夜/清晨不打擾
        if circadian.is_quiet_hours(now.astimezone(ZoneInfo(getattr(cfg, "timezone", "Asia/Taipei")))):
            return
    except Exception:
        pass
    # 🌬️ §2.06 存在特色：這一則處置的是**我自己剛剛說出口的那一句**——所以要指名它。
    # 舊路 `persona.soothe_user()` 是零參數＝零事實，只生得出通用的體貼話。旗標關＝仍走那條＝逐位元同現狀。
    _so = getattr(cfg, "soothe_own_question_enabled", False)
    msg = None
    if _so and coach and getattr(coach, "enabled", False):
        _q = " ".join((last.get("text") or "").split())[:20]
        _cpl = getattr(state, "coupling", None)
        msg = coach.voice_soothe_own(state.convo_history, _q,
                                     temporal.spoken_gap(age) if age else "",
                                     bool(_cpl is not None and getattr(_cpl, "i_bot", 0) >= 0.5))
        if msg:
            # 「嘴上說不用急、實際又問一次」＝這條 lane 最常見的自我矛盾 → 確定性砍掉問句，剩下的照送
            msg = _soothe_nonquestion(msg) or None
    if not msg:
        msg = (coach.voice_soothe(state.convo_history) if (coach and coach.enabled)
               else _SOOTHE_LINES[int(q_ts) % len(_SOOTHE_LINES)])
    msg = _soothe_nonquestion(msg)
    if not msg:
        return
    if _say(client, msg, **({"prefix": "🌬️ ", "state": state, "topic": "我剛剛問的那句"} if _so else {})):
        _remember(state, "model", ("🌬️ " + msg) if _so else msg)
        state.soothed_for_ts = q_ts
        state.last_soothe_ts = now_ts
        state.last_push_ts = now_ts                       # 與其他推播共用冷卻
        if not client.dry_run:
            state.save()


# 🤝 守約打招呼的反「同拍雙發」極短間隔（只防同一波連發兩則；不套 notify_cooldown_min——使用者明確排的約定
# 不該被自我獨白的反堆疊冷卻整整延後一個 cooldown，那會讓「八點承諾」拖到 8:30 才發）。
_PROMISE_AUGMENT_WINDOW_SEC = 300  # 🧵 剛排程的承諾（5 分內、未兌現）才可被「同時/一起…」續句併入（到點一起做）
_PROMISE_FIRE_GUARD_SEC = 90
_SELF_PROMISE_DEDUP_SEC = 90      # 🧬 §1.89 自諾與帳上未兌現約定「同刻」的容差（秒）：bot 對剛入帳那筆的確認，時刻必然落在同一個 target 上
_PROMISE_DEFER_RECENT_SEC = 120   # 🤝 排程承諾只在「使用者最近這麼久內還在打字」才延後（避開正說的話）；一停下就照時兌現、不等耦合輪慢慢衰（修截圖「晚了七分鐘」）
# 🤝 不變式：OVERDUE_GRACE ≥ DEFER_RECENT（180≥120）——否則「逾期但仍在 DEFER 窗內」的承諾語意難推理（120–180s 過渡窗：在場時延後、過 180s 才算逾期補發）。
_PROMISE_OVERDUE_GRACE_SEC = 180  # 🤝 到點後超過這麼久＝「逾期欠債」：即使你正在互動也補發（不被在場閘壓住），語氣帶遲到致歉；剛到點(<此)仍避開你正打字


def _recent_unfulfilled_promise(state, now_ts):
    """🧵 找「最近剛排程、未兌現」的承諾（給『同時/一起…』續句併入用）。窗內(_PROMISE_AUGMENT_WINDOW_SEC)、取最新一筆；無則 None。"""
    best = None
    for p in (getattr(state, "scheduled_promises", None) or []):
        if p.get("fulfilled"):
            continue
        made = p.get("made_ts") or 0
        if now_ts - made > _PROMISE_AUGMENT_WINDOW_SEC:
            continue
        if best is None or made > (best.get("made_ts") or 0):
            best = p
    return best


# 🧭 §1.25 情緒座標類承諾的偵測（behavior/made_text 命中其一即算）：入帳存快照、兌現算差分共用同一把。
_MOOD_PROMISE_RE = re.compile(r"情緒座標|心情座標|情緒的變化|心情的變化")


def _mood_snapshot(state, now_ts):
    """🧭 §1.25 (b)：訂約當下的 circumplex 快照 {v,a,label,ts}——座標/label 全**程式算**（circumplex 單一真相；
    比照 §1.05 change_baseline 精神、但掛 promise dict 自己的鍵，selfchange 本體一行不碰）。兩個入帳點共用。"""
    v, a = circumplex.position(state)
    return {"v": v, "a": a, "label": circumplex.label(v, a), "ts": now_ts}


def _is_mood_promise(p):
    """🧭 §1.25：這筆承諾是不是「到點回報情緒/心情座標變化」類（看 behavior＋made_text；\n 接縫防跨界誤中）。"""
    return bool(_MOOD_PROMISE_RE.search((p.get("behavior") or "") + "\n" + (p.get("made_text") or "")))


# 🧭 §1.47 寬承諾偵測（MOOD_COORD_DELIVER 把關）：截圖 12:03「再說清楚變化的細節吧」／§1.12 逃生閘 LLM 命名的
# 「跟他說說心裡變化的細節」都不含 §1.25 四詞（情緒座標/心情座標/情緒的變化/心情的變化）＝快照沒存＝12:33 兌現
# 只有質性沒數字、被追「怎麼沒講」。放寬三型：內在詞±變化（心裡…變化）、變化的細節/經過、裸「座標」。
# 誤中（「專案變化的細節」）代價＝兌現多帶一行程式讀的真座標＝誠實非幻覺，安全側（消融定案）。
_MOOD_PROMISE_WIDE_RE = re.compile(
    r"(?:心裡|心理|內心|內在|情緒|心情|感覺|感受)[^\n]{0,6}變化|變化[^\n]{0,4}(?:細節|經過)|座標")


def _mood_promise_hit(cfg, p):
    """§1.25 原判 or §1.47 寬判（三個 §1.25 呼叫點共用；deliver 旗標關＝只剩原判＝逐位元同現狀）。"""
    return _is_mood_promise(p) or (getattr(cfg, "mood_coord_deliver_enabled", False)
                                   and bool(_MOOD_PROMISE_WIDE_RE.search(
                                       (p.get("behavior") or "") + "\n" + (p.get("made_text") or ""))))


def _book_scheduled_targets(client, state, cfg, coach, text, targets, now_utc, tz, user_ts,
                            behavior_override="", leave_arm=False):
    """🤝 排程承諾的入帳＋ack 共用塊（§1.12 從 scheduled_promise 路由**純搬移**抽出、位元不變）：
    去重鍵 (round(target_ts), behavior)、recur 升級補標、貼圖旗標、_trim_sched_promises(protect=_fresh)、
    voice_schedule_ack(targets[0] 的本地時刻＝**程式時鐘算的 HH:MM**)、_remember＋save。
    behavior＝selfstate.extract_promise_behavior(text) **or** behavior_override（確定性抽取優先；
    override 給 §1.12 LLM 逃生閘的動作命名用，時刻仍永遠來自 temporal 算好的 targets）。
    tz＝呼叫端已解析好的時區（原路由的 _tz）。"""
    proms = list(getattr(state, "scheduled_promises", None) or [])
    # 🧭 §1.25 (a)：mood_fix=旗標——剝無時間前導我-子句＋情緒/心情座標明確標籤（假 cfg 無屬性＝False＝基線）
    _beh = selfstate.extract_promise_behavior(
        text, mood_fix=getattr(cfg, "promise_mood_ground_enabled", False)) or behavior_override   # 🤝 §0.78 一句一個 behavior（同 text）→ 迴圈外算一次
    # 🤝 §0.78 FIX 5（MED）：去重只認**未兌現**的既有筆，且鍵＝(時刻, 行為)。原本 `{round(target)}` 含**已兌現**筆——
    # 21:47 那筆稍早發過（fulfilled）、使用者再約同時刻，會被已兌現的舊筆擋掉＝新約靜默丟失＝到點永不觸發（截圖真相之一）。
    # 未兌現才擋（真有一顆在等）；已兌現不擋（那是過去、可重約）。行為入鍵＝同時刻不同動作（說感覺 vs 送貼圖）兩筆都留。
    have = {(round(p.get("target_ts") or 0), p.get("behavior") or "")
            for p in proms if not p.get("fulfilled")}
    _fresh = []                                          # 🤝 §0.78 審查：這一輪剛立的新約→截尾保護（不被剪掉卻仍答應＝說到做不到）
    # 🎴 §0.68 送貼圖承諾的能力判定（一次算好，捕捉與 ack 誠實說明共用）：想要貼圖？手邊有可送的真貼圖嗎？
    # 🎴 §0.94／§0.95 送貼圖＋偏好題旗標（偏好題⇒也要真的送出 sticker，見 _sticker_promise_flags）
    _wants_sticker, _sticker_capable, _prefers_sticker = _sticker_promise_flags(text, state, cfg)
    # 🤝 §0.64 每天重複：每天/每日/天天＋鐘點 → recur=daily（到點發完自動排明天同時刻；原本會被記成單次、
    # 發一次就永遠停＝「半守約」——答應了每天、實際只做一天）。旗標關＝不標 recur＝單次＝同現狀。
    _recur = "daily" if (getattr(cfg, "sched_recur_daily_enabled", True)
                         and selfstate.is_daily_recur_request(text)) else ""
    for target_ts in targets:
        # 🤝 §0.66（審查 MED、實測確認）：暫離自動計時的 target＝now+時距＝**每次都不同秒**，round() 精確
        # 去重永遠撞不上——重述同一件事（「我去睡半小時」…90秒後「我說我去睡半小時」）會疊兩顆計時、
        # 到點連叫兩次。暫離形改用**鄰近窗去重**（±3 分鐘內已有未兌現的＝同一個暫離、不疊；仍照常答應）。
        if leave_arm and any(not _q.get("fulfilled")
                             and abs((_q.get("target_ts") or 0) - target_ts) <= 180 for _q in proms):
            continue
        if (round(target_ts), _beh) in have:
            # 🤝 §0.64（審查 raised、實測確認）：同一時刻已有**單次**、使用者這句升級成「每天」→ 去重不能吞掉
            # 升級——就地補標 recur（否則「每天8點」被當已有、明天發完就死）。
            if _recur:
                for _p in proms:
                    if round(_p.get("target_ts") or 0) == round(target_ts) and not _p.get("fulfilled"):
                        _p.setdefault("recur", _recur)
            continue
        p_new = {"target_ts": target_ts, "action": text[:120],
                 "made_ts": now_utc.timestamp(), "made_text": text[:200], "fulfilled": False,
                 "behavior": _beh, "status": "pending"}  # 🤝 行為捕捉＋帳本狀態
        if getattr(cfg, "promise_delivery_proof_enabled", False):
            # 📦 §1.85 交付舉證要拿「他到底要什麼」去比對，而 made_text 只存 [:200]——長訊息會把「最後再告訴我
            # 答案」這種**關鍵請託**切在 200 字之外（與 MEMORY「被 [-N:] 截斷」同形）。旗標關＝不存＝state 檔同現狀。
            p_new["deliver_ask"] = text[:300]
        if leave_arm and not p_new["behavior"]:
            p_new["behavior"] = "叫你——你交代要暫離一下，時間到我要喊你回來"   # 🤝 §0.66 暫離交代的預設行為標籤（兌現 voice/帳本顯示用）
        if getattr(cfg, "self_change_ground_enabled", True) and selfchange.is_change_behavior(p_new["behavior"]):
            p_new["change_baseline"] = selfchange.snapshot(state)   # 🔄 §1.05 蛻變承諾成立時存內在快照，到點比對出「真的變了什麼」
        # 🧭 §1.25 (b)：情緒/心情座標類承諾 → 訂約當下存 circumplex 快照（兌現才有真差分可講；比照 §1.05 精神）
        if getattr(cfg, "promise_mood_ground_enabled", False) and _mood_promise_hit(cfg, p_new):   # §1.25 原判＋§1.47 寬判
            p_new["mood_baseline"] = _mood_snapshot(state, now_utc.timestamp())
        if _recur:
            p_new["recur"] = _recur
        # 🎴 §0.68 送貼圖承諾：只有**手邊真有可送貼圖**才標 wants_sticker（到點真的 send_sticker）；
        # 沒有＝不標＝兌現不假裝，並在下面 ack 後補一句誠實說明（§0.64 做不到不空口答應）。
        if _prefers_sticker:                      # 🎴 §0.94／§0.95 偏好題：**即使沒貨也標**——兌現文字才誠實（不 emoji 假裝、不捏造圖案）
            p_new["prefers_sticker"] = True
        if _wants_sticker and _sticker_capable:
            p_new["wants_sticker"] = True
        proms.append(p_new)
        _fresh.append(p_new)
        have.add((round(target_ts), _beh))
    # 🤝 §0.64→§0.78 FIX 6：截尾委派共用 helper——pending 依**最快到點**留（不被遠期筆擠掉、近筆到點沒東西可發），
    # 空位補最近完結；剛立的新約（_fresh）保護不剪（答應了就一定在帳本）；SCHED_RECUR_DAILY=0 → proms[-8:]＝同現狀。
    if getattr(cfg, "sched_recur_daily_enabled", True):
        state.scheduled_promises = _trim_sched_promises(proms, protect=_fresh)
    else:
        state.scheduled_promises = proms[-8:]
    state.self_topic_ts = now_utc.timestamp()           # 答應一個約定＝也在談 bot 自己（之後會主動）
    try:                                                # 答應語以**最早**那個時刻當代表（多筆時 voice 自會涵蓋整句）
        _local = datetime.fromtimestamp(targets[0], timezone.utc).astimezone(tz) if tz else None
    except Exception:
        _local = None
    # 🎴 §0.68 想要貼圖但**手邊沒有可送的真貼圖**：把誠實守則餵進答應語（別空頭答應「會送你貼圖」——時間守得住、
    # 貼圖還沒有、請先傳一張教）＝一句內就自洽；無 LLM 退路模板不提貼圖 → 另補一句誠實說明。§0.64 落到貼圖這件事上。
    _no_sticker = _wants_sticker and not _sticker_capable
    _sticker_hint = persona.sticker_concept_hint(have_sendable=False) if _no_sticker else ""
    ack = (coach.voice_schedule_ack(text, _local, state.convo_history, sticker_hint=_sticker_hint)
           if (coach and coach.enabled) else _schedule_ack_fallback(_local))
    # 🤖 §1.18 入帳 ack 顯式排除：ack 本身長著「12:18我會…」的樣子（結構網＋temporal 都會中），
    # 而 §1.18 去重是 round-only、擋得住同刻——但顯式標記才是硬保證（實測④：措辭不同的雙帳雷）。
    _TURN["self_promise_skip"] = True
    _say(client, ack)
    _remember(state, "user", text, ts=user_ts)
    _remember(state, "model", ack)
    if _no_sticker and not (coach and coach.enabled):   # 無 LLM 模板不會提貼圖也不解釋 → 補一句誠實說明
        _honest = ("（不過我得老實說：我手邊還沒有可以送出的真貼圖——你先傳一張你想要的貼圖給我、我記起來，"
                   "之後就真的能送給你了。時間我會準時叫你。）")
        _say(client, _honest)
        _remember(state, "model", _honest)
    if not cfg.dry_run:
        state.save()


# 🤝 §1.12 鐵律的縱深防禦：LLM 判定輸出裡**任何時刻字樣一律程式端丟棄**（協定上已無時間欄位，這裡再兜一層）。
# 11:08 幻覺前科：時間永遠由 temporal／程式時鐘提供，LLM 只准命名動作。
_LLM_ACTION_TIME_RE = re.compile(
    r"\d{1,2}[:：]\d{2}"                                                        # HH:MM（11:08）
    r"|[0-9一二兩三四五六七八九十]+\s*(?:個)?\s*(?:分鐘|小時|鐘頭|點|分|秒)(?:之?後)?"  # N分鐘（後）/8點/兩小時…
    r"|時刻|幾點")                                                               # 「那個時刻」「現在幾點」字樣


def _sanitize_llm_action(s):
    """§1.12：剝掉 LLM 動作命名裡的時刻/時距字樣，strip 後截 40 字；清完空→''（呼叫端退回確定性行為/action）。"""
    return _LLM_ACTION_TIME_RE.sub("", s or "").strip()[:40]


# 🤖 §1.18 bot 自發承諾的確定性結構網：第一人稱未來式（我會/我來/我過來）。故意收窄——實測⑤ battery：
# cancel ack「不會再發」/miss 句/確定性帳本文皆不中；新立句/§0.75 例句中。複誦/認錯/提案的分辨交 (d) LLM 閘
# （複誦「我會記得在明天早上十一點…」@11:06 會被 temporal 滾成 7/12 11:00 的未來錨，時刻過濾救不了＝實測②③）。
_SELF_PROMISE_RE = re.compile(r"我(?:會|來|過來)")


_SAME_WAVE_SEC = 90           # 🔁 §2.03 前一則使用者訊息這麼近＝同一波（他把一個意思拆成連著兩則送）
_SP_TRACE_KEEP = 6            # 🤖 §2.03 自諾入帳鏈的留痕只留最近幾筆（診斷用，不是帳本）


def _sp_trace(state, text, why, booked=None):
    """🤖 §2.03 記一筆「bot 說出口的『我會…』最後怎麼了」——進帳了、還是卡在哪一關。

    為什麼要有：這條鏈（結構網→temporal→鄰近去重→LLM 閘→入帳）**每個出口都是裸 return**，
    於是「bot 答應了、帳本卻沒有」在外面看起來跟「bot 根本沒答應」一模一樣。實測那次（13:58
    「五十分鐘後，我會好好想想再告訴你」）：temporal 解得出 14:47、LLM 閘也判 True，14:21 打
    `/promises` 卻一筆待辦都沒有——中間哪一關丟的，完全無從查。留痕**不改任何行為**（只寫 state），
    但 `/promises` 從此講得出來。旗標關＝不記＝逐位元同現狀。"""
    try:
        if not _TURN.get("self_promise_trace"):
            return
        log = list(getattr(state, "self_promise_log", None) or []) if state is not None else []
        log.append({"ts": time.time(), "text": (text or "")[:60],
                    "why": why, "booked_ts": booked})
        if state is not None:
            state.self_promise_log = log[-_SP_TRACE_KEEP:]
    except Exception:
        pass                                              # 診斷留痕絕不影響送訊


def _maybe_self_promise_capture(text):
    """🤖 §1.18 BOT_SELF_PROMISE：_say 互動出口的自發承諾掃描（只在 handle_message 設了 self_promise_ctx 的輪跑）。
    (a) 結構網不中＝零成本跳出 →(b) temporal 解 **bot 這句話**取未來錨（時間永遠來自 temporal＝§1.12 鐵律）
    →(c) 時刻鄰近去重（round-only ±60s 對未兌現筆＝擋 ack 殘餘與同刻重述）→(d) LLM 逃生閘 judge_self_promise
    **必經**（新立 vs 複誦/對帳/認錯/提案徵詢；失敗安全＝不入帳）→(e) _book_self_promise 入帳 origin='self'。
    語境排除：promise_ledger/scheduled_promise 輪（帳本盤點複誦與承諾管理輪）；入帳 ack 由 self_promise_skip 排除。"""
    ctx = _TURN.get("self_promise_ctx")
    if not _SELF_PROMISE_RE.search(text or ""):
        return                                            # (a) 結構網不中＝零成本跳出（連留痕都不必）
    # 🤖 §2.03 從這裡開始**每一關都留痕**：bot 說出口的「我會…」帶著可解的未來時刻，卻沒進帳本，
    # 就是「空口答應」——本 repo 修過 15+ 次的那個病。實測（截圖 13:58「五十分鐘後，我會好好想想再告訴你」）：
    # temporal 解得出 14:47、§1.18 的 LLM 閘也判 True，可是 14:21 打 /promises **一筆待辦都沒有**——
    # 中間哪一關把它丟掉，從外面完全看不出來，因為這條鏈**每個出口都是裸 return**。留痕不改行為（只寫 state），
    # 但讓 `/promises` 講得出「這句我說了、但它沒進帳本，卡在第幾關」。
    if not ctx:
        return _sp_trace(None, text, "沒有本輪上下文（BOT_SELF_PROMISE 關著？）")
    state, cfg, coach, now_utc, tz, user_ts, route_kind = ctx
    if _TURN.get("self_promise_skip"):
        return _sp_trace(state, text, "這輪已經另外入帳過了（ack 排除）")
    if route_kind in ("promise_ledger", "scheduled_promise"):
        return _sp_trace(state, text, f"語境排除：這輪走的是 {route_kind}")
    now_ts = now_utc.timestamp()
    _tz = tz or (ZoneInfo(getattr(cfg, "timezone", "Asia/Taipei")) if ZoneInfo is not None else None)
    try:
        targets = [e for e in temporal.all_clock_epochs(text, now_utc, _tz) if e > now_ts]
    except Exception:
        return                                            # 解析失敗＝安全跳過（掃描器絕不打斷送訊）
    if not targets:
        return _sp_trace(state, text, "這句沒有可解的未來時刻（只是語氣上的『我會』）")
    # 🤖 §2.21 幽靈約定殺手：「現在是 22:23，我們約 20 分鐘後，也就是 22:43…」這種**澄清句**裡的
    # 「22:23」剛過去 ⇒ temporal 滾成**明天同時刻**；22:43 被 (c) 去重掉（帳上已有）⇒ 只剩幽靈錨入帳
    # ⇒ 隔天 22:23 準時演出一場使用者從沒約過的守約、被質疑還堅持「我之前答應過你」（實測截圖 22:23–22:24）。
    # 結構判準（零詞表）：滾動後的錨減一天**落在此刻 ±15 分**＝原句講的是「此刻」的自我定位，不是明天的約。
    # 代價不對稱：真的想約「明天此刻」而被漏收＝少一筆（§0.75 兩步流程接得住）；幽靈入帳＝對他演一場假守約。
    if getattr(cfg, "self_promise_no_rolled", False):
        _ghost = [t for t in targets if abs((t - 86400) - now_ts) <= 900]
        if _ghost:
            targets = [t for t in targets if t not in _ghost]
            if not targets:
                return _sp_trace(state, text, "只剩「被滾到明天的此刻鐘點」（現在是HH:MM 的自我定位）→ 不入帳")
    # 🤝 §2.02 容差隨**回覆延遲**伸縮（原本固定 60 秒）：使用者說「30 分鐘後」與 bot 回「30 分鐘後」
    # 各自從自己的當下起算，兩個 target 就差了「這段對話往返花的時間」。實測 75 秒 ⇒ 固定 60 秒擋不住 ⇒
    # 同一個約定進帳兩次 ⇒ 到點連來兩則「我來了」（截圖 12:20）。旗標關＝仍是固定 60 秒＝逐位元同現狀。
    _slack = getattr(cfg, "promise_same_appointment_merge", False)
    _pend = [(round(p.get("target_ts") or 0), p.get("made_ts") or 0)
             for p in (getattr(state, "scheduled_promises", None) or []) if not p.get("fulfilled")]
    targets = [t for t in targets
               if not any(abs(round(t) - r) <= (60 + (min(max(0, now_ts - m), _SAME_APPT_MADE_SEC) if _slack else 0))
                          for r, m in _pend)]
    if not targets:
        return _sp_trace(state, text, "同時刻的約定已經在帳上了（不重複記）")
    judge = getattr(coach, "judge_self_promise", None) if (coach and getattr(coach, "enabled", False)) else None
    if judge is None:
        return _sp_trace(state, text, "沒有可用的判定閘（coach 停用）")
    try:
        verdict = judge(text)                             # (d) LLM 閘必經：正在立下新的？還是複誦既有/認錯/提案？
    except gemini.GeminiError as e:
        print(f"[promise] 🤖 §1.18 自發承諾判定失敗（安全＝不入帳）：{e}")
        return _sp_trace(state, text, f"判定閘出錯：{type(e).__name__}")
    if not (isinstance(verdict, (tuple, list)) and len(verdict) == 2 and verdict[0] is True):
        return _sp_trace(state, text, "判定閘說這句是複誦/確認，不是新的約定")
    _book_self_promise(state, cfg, targets, text, now_ts, _sanitize_llm_action(verdict[1]))
    _sp_trace(state, text, "", booked=targets[0])         # 🤖 §2.03 成功也留痕（看得到它真的進帳了）


def _book_self_promise(state, cfg, targets, bot_text, now_ts, behavior):
    """🤖 §1.18 bot 自發承諾入帳（**不走 _book_scheduled_targets**——那會 _say ack＝在 _say 內遞迴、
    又對自己剛說的承諾再 ack 一次；承諾句本身就是 ack）。behavior='' 時 _promise_keep_body 退用
    made_text＝bot 原句＝兌現有據。到點兌現/逾期補發/被催全走既有 _promise_emit/_promise_reply_bridge，零改動。"""
    proms = list(getattr(state, "scheduled_promises", None) or [])
    _fresh = []
    # 🧬 §1.89 同刻去重（結構性後盾）：bot 對「剛剛才記進帳本的那筆約定」所做的**確認**，時刻必然落在同一個
    # target 上——那不是新約，是 ack。截圖根因：23:11 使用者「明天早上七點再告訴我你的答案」→ 記第一筆
    # （07:00）；bot 回「明天早上七點，我會把答案告訴你。」→ §1.18 掃自己這句、temporal 解出**同一個 07:00**
    # （實測）→ LLM 閘判「是不是正在立下新的時間承諾」→ 那句字面上就是新約（沒有「我說過」「我會記得」這類
    # 複誦標記，判「否」的線索全不在）→ 判是 → **再記第二筆** ⇒ 同一個約定執行兩次。
    # 根因不在 LLM 閘判錯（它看那句話判「是」很合理），在**入帳端沒有任何結構性去重**：`_book_scheduled_targets`
    # 有 `have` 去重（鍵含 behavior），這裡卻是無條件 append。修法比照那支、但鍵**只看時刻不看 behavior**——
    # 使用者那筆的 behavior 實測是 ''、bot 那筆是 LLM 命名的字串，含 behavior 的鍵永遠對不上（§1.34/§1.36 教訓：
    # prompt/LLM 單靠不夠，要有確定性後盾）。誤判安全：真的在同一秒立一個**不同**的新約＝極罕見，且既有那筆
    # 本來就會在那個時刻兌現＝不會漏掉承諾，只會少記一筆重複的。旗標關＝不去重＝逐位元同現狀。
    _dedup = getattr(cfg, "self_promise_dedup_enabled", False)
    # 🤝 §2.02 同上：容差 = 90 秒 ＋ 那筆帳立到現在過了多久（相對時距的兩份帳本來就會差這麼多）
    _slack = getattr(cfg, "promise_same_appointment_merge", False)
    _live_ts = [((q.get("target_ts") or 0),
                 (min(max(0, now_ts - (q.get("made_ts") or 0)), _SAME_APPT_MADE_SEC) if _slack else 0))
                for q in proms if not q.get("fulfilled")] if _dedup else []
    for target_ts in targets:
        if _dedup and any(abs((target_ts or 0) - t) <= _SELF_PROMISE_DEDUP_SEC + extra for t, extra in _live_ts):
            print(f"[promise] 🧬 §1.89 自諾同刻去重：帳上已有同時刻的未兌現約定 → 這句是確認不是新約，不重複入帳"
                  f"（{bot_text[:40]!r}）")
            continue
        p_new = {"target_ts": target_ts, "action": bot_text[:300], "made_ts": now_ts,
                 "made_text": bot_text[:300], "fulfilled": False,
                 "behavior": behavior or "", "status": "pending", "origin": "self"}
        # 🧭 §1.25 (b) 第二入帳點：bot 自諾「到點跟你說我情緒座標的變化」也存快照（不經 _book_scheduled_targets）
        if getattr(cfg, "promise_mood_ground_enabled", False) and _mood_promise_hit(cfg, p_new):   # §1.25 原判＋§1.47 寬判
            p_new["mood_baseline"] = _mood_snapshot(state, now_ts)
        proms.append(p_new)
        _fresh.append(p_new)
    if not _fresh:                                        # 🧬 §1.89 全被同刻去重 → 帳本一位元不動、不落盤、不吐「入帳 0 筆」
        return
    state.scheduled_promises = _trim_sched_promises(proms, protect=_fresh)   # 剛立的自諾同樣受截尾保護
    if not getattr(cfg, "dry_run", False):
        state.save()
    print(f"[promise] 🤖 §1.18 bot 自發承諾入帳 {len(_fresh)} 筆（origin=self）：{bot_text[:60]!r}")


def _maybe_anchor_bridge(client, state, cfg, coach, route, text, now_utc, tz, user_ts):
    """🤝 §1.55 跨句補時距（PROMISE_ANCHOR_BRIDGE）：時距在上一句、動作在這一句＝拼起來才是一條約定。
    截圖根因（11:05）：「給你思考20分鐘。」＋「時間到了再跟我說，你可以如何證明自己？」拆兩輪＝各自碎片
    ——前句裸時距無動作（capture/守門/temporal 全 miss → LLM 空口答應「我會等 20 分鐘」）、後句有動作無
    鐘點（capture miss → 又空口答應＋把「時間到了『再』跟我說」的「時間到了」讀成現時宣稱、回「嗯？現在
    才 11:05 呢。」）；**兩句拼起來 capture 直接命中、temporal 解出正確 epoch**（實測釘在測試）。
    橋接條件（全確定性、零 LLM、時刻永遠 temporal＝鐵律）：①本輪 capture miss（route 非承諾類）且這句
    自身無未來鐘點（有鐘點＝§1.12 逃生閘守備、不搶）；②這句是請託形（looks_like_timed_request）；
    ③上一則使用者訊息 180 秒內、且**自身也是懸空碎片**（capture/守門皆 miss＝不重收已能入帳的
    「10分鐘後提醒我」）——只看緊鄰上一則（更早的裸時距＝過期不接）；④拼句 capture 命中＋解得出時刻
    → 走既有 _book_scheduled_targets（ack 帶程式算 HH:MM、到點 _promise_emit 真兌現）。
    旗標關（getattr 預設 False）＝不橋＝逐位元同現狀。回 True＝本輪已處理。"""
    if not getattr(cfg, "promise_anchor_bridge_enabled", False):
        return False
    if not getattr(cfg, "scheduled_promise_enabled", True):
        return False
    if getattr(route, "kind", "") in ("scheduled_promise", "promise_ledger"):
        return False
    if not selfstate.looks_like_timed_request(text, now_utc, tz):
        return False
    if temporal.all_clock_epochs(text, now_utc, tz):
        return False
    now_ts = now_utc.timestamp()
    prev = None
    for e in reversed(getattr(state, "convo_history", None) or []):
        if e.get("role") != "user":
            continue
        ts = e.get("ts") or 0
        if ts >= (user_ts or now_ts) - 1:                # 跳過本輪自己（已 _remember 入史）
            continue
        if now_ts - ts <= 180:
            prev = (e.get("text") or "").strip()
        break                                            # 只看緊鄰的上一則使用者訊息
    if not prev:
        return False
    if selfstate.is_scheduled_promise_request(prev) or selfstate.looks_like_timed_request(prev, now_utc, tz):
        return False                                     # 上一則自己能入帳/已被守門＝不是懸空碎片
    joined = prev + "\n" + text
    if not selfstate.is_scheduled_promise_request(joined):
        return False
    targets = temporal.all_clock_epochs(joined, now_utc, tz)
    if not targets:
        return False
    print(f"[promise] 🤝 §1.55 跨句補時距：上一句『{prev[:24]}』＋這句拼出完整約定 → 真入帳")
    _book_scheduled_targets(client, state, cfg, coach, joined, targets, now_utc, tz, user_ts)
    return True


def _maybe_llm_promise_rescue(client, state, cfg, coach, route, text, now_utc, tz, user_ts):
    """🤝🧠 §1.12 LLM 語意逃生閘：**確定性捕捉 miss**（route 非 scheduled_promise）＋ temporal 解得出**未來**時刻
    ＋ 句子指向 bot 時，呼叫**一次** gemini 判「他是不是請 bot 到點主動做某事＋動作命名」。
    為什麼：承諾捕捉靠窮舉詞表已漏了 15 次（git 史）——使用者語意多元，詞表永遠追不完；LLM 只放**逃生閘**
    （結構閘開火但確定性捕捉沒接到才呼叫），不進每則訊息熱路徑。鐵律：**時刻永遠來自 temporal／程式時鐘**——
    judge 協定沒有時間欄位、輸出裡的時刻字樣 _sanitize_llm_action 一律丟棄（11:08 幻覺前科）。
    判「是」→ 用 temporal 算好的 targets 走 _book_scheduled_targets（ack 必帶 HH:MM、到點 _promise_emit 準時發、
    被催 _promise_reply_bridge 自動 🤝＋遲到致歉）→ True（本輪結束）。
    判「否」→ 設 _TURN['promise_guard_llm_no']（本輪 §0.61 守則不掛＝自然聊天，例「20分鐘後我要開會你覺得呢」）→ False。
    失敗（GeminiError／解析不出）→ False 且**不設**標記＝§1.09 誠實守門照掛（安全退回）。
    PROMISE_LLM_RESCUE=0 → 逃生閘不存在＝逐位元同現狀。"""
    # ① 旗標／教練／排程承諾整包開著才有「該入帳」前提
    if not (getattr(cfg, "promise_llm_rescue_enabled", True) and coach and getattr(coach, "enabled", False)
            and getattr(cfg, "scheduled_promise_enabled", True)):
        return False
    # ② 確定性捕捉 miss 的路由才救（promise_ledger/取消/續約/延後已在更早的攔截 return；scheduled_promise＝已接到）
    if route.kind not in ("promise", "fact_or_chat", "smalltalk", "self_state", "self_change"):
        return False
    # ③ temporal 有未來錨（時間單一真相）：解不出未來時刻＝無從排程、不呼叫（守門 §0.61 照兜）
    _tz = tz or (ZoneInfo(getattr(cfg, "timezone", "Asia/Taipei")) if ZoneInfo is not None else None)
    targets = [e for e in temporal.all_clock_epochs(text, now_utc, _tz) if e > now_utc.timestamp()]
    if not targets:
        return False
    # ④ 指向 bot（結構閘或 at-me 訊號或第二人稱）——完全無指向的純敘述不燒呼叫
    if not (selfstate.looks_like_timed_request(text, now_utc, _tz)
            or any(m in text for m in selfstate._TIMED_STRUCT_AT_ME)
            or ("你" in text or "妳" in text)):
        return False
    # ⑤ 「有感覺再說」真湧現託付放行走 feeling＝不為交差假裝的不變式（不能為趕點捏造感覺）
    if selfstate._feeling_emergence_conditional(text):
        return False
    # ⑥ 過去/問責語氣歸 ledger（剛剛/了嗎/為什麼…不是新請求）
    if any(s in text for s in selfstate._TIMED_REQ_SKIP):
        return False
    # ⑦ 超出能力的請求歸能力閘（誠實拒絕優先，不入帳）
    if selfstate.promise_unsupported(text):
        return False
    try:
        verdict = coach.judge_timed_request(text)
    except gemini.GeminiError as e:                       # coach 端已兜，這裡再兜一層（stub/未來實作直接拋也安全）
        print(f"[promise] LLM 逃生閘判定失敗：{e}")
        verdict = None
    if verdict is None:
        return False                                      # 失敗＝不設標記＝§1.09 誠實守門照掛
    is_req, action = verdict
    if not is_req:
        _TURN["promise_guard_llm_no"] = True              # 本輪裁決「不是請 bot」＝自然聊天、守則不掛（下輪自動重設）
        return False
    _book_scheduled_targets(client, state, cfg, coach, text, targets, now_utc, _tz, user_ts,
                            behavior_override=_sanitize_llm_action(action))
    return True


def _schedule_ack_fallback(target_local):
    """🤝 無 LLM／失敗時、答應排程承諾的模板（仍帶時間、自然一句）。target_local 可 None。"""
    when = target_local.strftime("%H:%M") if target_local else "到時候"
    return f"好，我記住了——{when}我會主動跟你打招呼。"


_CONTINUATION_WINDOW_SEC = 3600   # 🤝 §0.70 續約可繼承的「最近一筆排程承諾」時間窗（1h 內做過/剛完結的那個約定）


def _recent_any_promise(state, now_ts, window=_CONTINUATION_WINDOW_SEC):
    """🤝 §0.70 找「可被續約繼承的最近一筆排程承諾」——給續約繼承 behavior/wants_sticker 用。
    **未兌現（pending）＝活著的約定、任何 age 都可續**（審查 LOW-2：長程「2小時後叫我」過 1h 仍能被「再10分鐘」延）；
    **已兌現則須在 window 內**（剛完結的才續）。皆**按最近活動時間排名**（審查：近期已完結 勝過 陳舊 pending，才不誤挑）。
    無則 None。"""
    best, best_ts = None, -1
    for p in (getattr(state, "scheduled_promises", None) or []):
        ref = max(p.get("made_ts") or 0, p.get("fulfilled_ts") or 0)
        if ref <= 0:
            continue
        if p.get("fulfilled") and (now_ts - ref) > window:   # 已完結：須在窗內；pending：任何 age 都可（活著）
            continue
        if ref > best_ts:                                    # 純按最近活動排名（不給 pending 硬加權，免陳舊 pending 壓過近期完結）
            best, best_ts = p, ref
    return best


def _maybe_continuation_promise(client, state, cfg, coach, text, now_utc, tz, user_ts):
    """🤝 §0.70 延續性約定：使用者用極簡續約語詞（「再10分鐘」「延長10分鐘」）→ 繼承**最近一筆排程承諾**的
    behavior/wants_sticker、new target＝now＋新時距 重新入帳並答應（否則 route=fact_or_chat、bot 只 LLM 空口
    答應、到點不觸發）。無先前約定＝「再10分鐘」無所指 → 回 False（不搶，照常落一般聊天）。CONTINUATION_PROMISE=0
    或排程整包關 → False＝逐位元同現狀。回 True＝已處理（呼叫端 return）。"""
    if not getattr(cfg, "continuation_promise_enabled", True):
        return False
    if not getattr(cfg, "scheduled_promise_enabled", True):
        return False
    if not selfstate.is_continuation_duration(text):
        return False
    secs = selfstate.continuation_duration_secs(text)
    if secs <= 0:
        return False
    now_ts = now_utc.timestamp()
    prior = _recent_any_promise(state, now_ts)
    if not prior:                                         # 無先前約定可繼承 → 不搶（「再10分鐘」無所指）
        return False
    _tz = tz or (ZoneInfo(getattr(cfg, "timezone", "Asia/Taipei")) if ZoneInfo is not None else None)
    target_ts = now_ts + secs
    # 繼承前約的行為＋送貼圖意圖（送貼圖再依此刻能力確認：手邊有可送貼圖才續標 wants_sticker）
    _wants = bool(prior.get("wants_sticker")) and getattr(cfg, "promise_sticker_enabled", True)
    _capable = _wants and _has_sendable_sticker(state, cfg)
    _prefers = bool(prior.get("prefers_sticker")) and getattr(cfg, "promise_sticker_enabled", True)   # 🎴 §0.95 續約也繼承「偏好題」
    p_new = {"target_ts": target_ts, "action": text[:120], "made_ts": now_ts,
             "made_text": text[:200], "fulfilled": False,
             "behavior": prior.get("behavior") or "", "status": "pending",
             "continuation": True}
    if getattr(cfg, "promise_delivery_proof_enabled", False):
        p_new["deliver_ask"] = (prior.get("deliver_ask") or text)[:300]   # 📦 §1.85 續約沿用原請託（見上方註解）
    if _prefers:
        p_new["prefers_sticker"] = True
    if _wants and _capable:
        p_new["wants_sticker"] = True
    if getattr(cfg, "self_change_ground_enabled", True) and selfchange.is_change_behavior(p_new.get("behavior")):
        # 🔄 §1.06(D2) 續約不丟蛻變基準：沿用**原約的** baseline（「再20分鐘」＝從最初交代那刻量變化）；原約沒存才補拍此刻
        p_new["change_baseline"] = prior.get("change_baseline") or selfchange.snapshot(state)
    proms = list(getattr(state, "scheduled_promises", None) or [])
    # 🤝 §0.70（審查 confirmed MED-1）：續約若繼承的是**還沒兌現**的約定（改時間、或續約再續約），是**取代**不是疊加——
    # 「再10分鐘」後又「再5分鐘」＝改成 5 分、不是同時排兩筆到點連發兩次。前約已兌現＝新開一輪續約（append）。
    if not prior.get("fulfilled"):
        proms = [p for p in proms if p is not prior]
    proms.append(p_new)
    # 截尾：pending 依最快到點留、空位補最近完結；剛立的續約 p_new 保護不剪（§0.78 FIX 6 共用 helper）
    state.scheduled_promises = _trim_sched_promises(proms, protect=[p_new])
    state.self_topic_ts = now_ts
    try:
        _local = datetime.fromtimestamp(target_ts, timezone.utc).astimezone(_tz) if _tz else None
    except Exception:
        _local = None
    _sticker_hint = persona.sticker_concept_hint(have_sendable=False) if (_wants and not _capable) else ""
    ack = (coach.voice_schedule_ack(text, _local, state.convo_history, sticker_hint=_sticker_hint)
           if (coach and coach.enabled) else _schedule_ack_fallback(_local))
    _TURN["self_promise_skip"] = True   # 🤖 §1.18 續約 ack＝入帳 ack、顯式排除（同 _book_scheduled_targets）
    _say(client, ack)
    _remember(state, "user", text, ts=user_ts)
    _remember(state, "model", ack)
    if _wants and not _capable and not (coach and coach.enabled):
        _honest = ("（不過我得老實說：我手邊還沒有可以送出的真貼圖——你先傳一張你想要的貼圖給我、我記起來，"
                   "之後就真的能送給你了。時間我會準時回應你。）")
        _say(client, _honest)
        _remember(state, "model", _honest)
    if not getattr(cfg, "dry_run", False):
        state.save()
    return True


_DEFERRED_INTENT_TTL_SEC = 10 * 60      # 🤝 §0.75 存了「等一下回答」意圖後，多久內補時間仍算同一個（逾時＝那次不了了之）。審查 Finding 3：從 30 縮到 10 分——仍夠涵蓋隔幾輪才補時間（截圖 16:19→16:21），但縮小「無關時刻句誤補」的窗


def _maybe_deferred_promise(client, state, cfg, coach, text, now_utc, tz, user_ts):
    """🤝 §0.75 兩步延後約定（分派前）：① **補時間**——帳本外有『等時間的延後回答意圖』(pending_answer_intent)、
    且這句是補時間句（4分鐘後/3:50）→ 真的入帳排程承諾（behavior 承接意圖）＋到點兌現，清掉意圖。② **存意圖**——
    這句是「等一下回答我」（有回答動作、含糊延後、無具體時刻）→ 存意圖＋誠實問時間（別空口應）。
    修截圖：這種兩步約定從沒進帳本、bot 口頭應卻到點不發、被問又亂算「才過一分鐘」（無錨定 made_ts＝時間感脫離絕對時間）。
    回 True＝已處理（呼叫端 return）。DEFERRED_PROMISE=0 或排程整包關 → False＝逐位元同現狀。"""
    if not getattr(cfg, "deferred_promise_enabled", True) or not getattr(cfg, "scheduled_promise_enabled", True):
        return False
    now_ts = now_utc.timestamp()
    _tz = tz or (ZoneInfo(getattr(cfg, "timezone", "Asia/Taipei")) if ZoneInfo is not None else None)
    pend = getattr(state, "pending_answer_intent", None)
    # 🧹 §0.75 審查 Finding 4：逾窗的待補意圖直接清掉（別無限殘留、也別被遲來的無關時刻句補上）
    if pend and (now_ts - (pend.get("made_ts") or 0)) > _DEFERRED_INTENT_TTL_SEC:
        state.pending_answer_intent = None
        pend = None
        if not getattr(cfg, "dry_run", False):
            state.save()
    # ① 補時間：有待補意圖（窗內）、這句是補時間句 → 入帳
    if pend and selfstate.is_time_fill(text):
        targets = temporal.all_clock_epochs(text, now_utc, _tz)
        if not targets:                                   # 🤝 §0.75 審查 Finding 2：裸時距（五分鐘/半小時，無「後」）→ now＋時距 兜底
            _secs = selfstate.continuation_duration_secs(text)
            if _secs > 0:
                targets = [now_ts + _secs]
        if targets:
            proms = list(getattr(state, "scheduled_promises", None) or [])
            beh = pend.get("behavior") or "回答他的問題"
            # 🤝 §0.78 FIX 5（MED）：同主捕捉——去重只認未兌現筆、鍵含 behavior（已兌現的同時刻舊筆不擋新約）
            have = {(round(p.get("target_ts") or 0), p.get("behavior") or "")
                    for p in proms if not p.get("fulfilled")}
            _fresh = []                                          # 🤝 §0.78 審查：剛立的新約→截尾保護
            for target_ts in targets:
                if (round(target_ts), beh) in have:
                    continue
                _p = {"target_ts": target_ts, "action": text[:120], "made_ts": now_ts,
                      "made_text": (pend.get("raw") or text)[:200], "fulfilled": False,
                      "behavior": beh, "status": "pending"}
                if getattr(cfg, "promise_delivery_proof_enabled", False):
                    _p["deliver_ask"] = (pend.get("raw") or text)[:300]   # 📦 §1.85 見上方註解
                proms.append(_p)
                _fresh.append(_p)
                have.add((round(target_ts), beh))
            state.scheduled_promises = _trim_sched_promises(proms, protect=_fresh)   # §0.78 FIX 6 共用 helper（保護新約）
            state.pending_answer_intent = None
            state.self_topic_ts = now_ts
            try:
                _local = datetime.fromtimestamp(targets[0], timezone.utc).astimezone(_tz) if _tz else None
            except Exception:
                _local = None
            _ack_q = (pend.get("raw") or text)                # 讓 ack 帶「答應的是那個問題」的上下文
            ack = (coach.voice_schedule_ack(_ack_q, _local, state.convo_history)
                   if (coach and coach.enabled) else _schedule_ack_fallback(_local))
            _TURN["self_promise_skip"] = True   # 🤖 §1.18 兩步延後補時間的 ack＝入帳 ack、顯式排除
            _say(client, ack)
            _remember(state, "user", text, ts=user_ts)
            _remember(state, "model", ack)
            if not getattr(cfg, "dry_run", False):
                state.save()
            return True
    # ② 存意圖：「等一下回答我」無具體時刻 → 存意圖＋誠實問時間（別讓 LLM 口頭空應）
    if selfstate.is_deferred_answer_request(text):
        beh = selfstate.extract_promise_behavior(
            text, mood_fix=getattr(cfg, "promise_mood_ground_enabled", False)) or "回答他的問題"   # 🧭 §1.25 (a)
        state.pending_answer_intent = {"behavior": beh, "made_ts": now_ts, "raw": text[:200]}
        line = persona.deferred_ask_time(beh)
        _say(client, line)
        _remember(state, "user", text, ts=user_ts)
        _remember(state, "model", line)
        if not getattr(cfg, "dry_run", False):
            state.save()
        return True
    return False


def _anchor_promise_for_offset(state, now_ts):
    """🤝 §0.71 找偏移增補的**錨點**＝最近一筆**未兌現、target 尚未太久過去**的排程承諾（截圖的 13:47 叫我）。
    偏移「再隔3分鐘」是相對這個錨點的 target 算的。取 made_ts 最近者；target 已過久（>grace）的不當錨（那約定該自己先發）。"""
    best, best_made = None, -1
    for p in (getattr(state, "scheduled_promises", None) or []):
        if p.get("fulfilled"):
            continue
        target = p.get("target_ts") or 0
        if target <= 0 or (now_ts - target) > _PROMISE_OVERDUE_GRACE_SEC:   # 錨點的 target 不能是早該發的陳舊約定
            continue
        made = p.get("made_ts") or 0
        if made > best_made:
            best, best_made = p, made
    return best


def _maybe_offset_augmentation(client, state, cfg, coach, text, now_utc, tz, user_ts):
    """🤝 §0.71 偏移增補：「然後時間到的時候再隔3分鐘給我一個貼圖」＝在**前約時間之後**再 N 分鐘增補一個動作
    （前約 13:47 叫我 → 貼圖 13:50）。target＝**錨點 prior.target＋N**（非 now＋N）。截圖根因：被 §0.69 timeup
    誤算成 now＋3＝13:20、提早 27 分亂發。無可當錨的未兌現前約 → 回 False（不搶）。OFFSET_AUGMENTATION=0 或排程整包關
    → False＝逐位元同現狀。回 True＝已處理（呼叫端 return）。"""
    if not getattr(cfg, "offset_augmentation_enabled", True):
        return False
    if not getattr(cfg, "scheduled_promise_enabled", True):
        return False
    if not selfstate.is_offset_augmentation(text):
        return False
    secs = selfstate.offset_augmentation_secs(text)
    if secs <= 0:
        return False
    now_ts = now_utc.timestamp()
    anchor = _anchor_promise_for_offset(state, now_ts)
    if not anchor:                                       # 無可當錨的未兌現前約 → 偏移無所指、不搶
        return False
    anchor_target = anchor.get("target_ts") or 0
    target_ts = anchor_target + secs                     # 🤝 關鍵：錨點時間＋偏移（非 now＋偏移）
    _tz = tz or (ZoneInfo(getattr(cfg, "timezone", "Asia/Taipei")) if ZoneInfo is not None else None)
    behavior = selfstate.extract_promise_behavior(
        text, mood_fix=getattr(cfg, "promise_mood_ground_enabled", False)) or ""   # 🧭 §1.25 (a)
    _wants, _capable, _prefers = _sticker_promise_flags(text, state, cfg)   # 🎴 §0.95 偏好題⇒也要真的送出 sticker
    p_new = {"target_ts": target_ts, "action": text[:120], "made_ts": now_ts,
             "made_text": text[:200], "fulfilled": False, "behavior": behavior, "status": "pending",
             "offset_of": round(anchor_target)}
    if getattr(cfg, "promise_delivery_proof_enabled", False):
        p_new["deliver_ask"] = text[:300]                 # 📦 §1.85 見 _book_scheduled_targets 的註解
    if getattr(cfg, "self_change_ground_enabled", True) and selfchange.is_change_behavior(behavior):
        p_new["change_baseline"] = selfchange.snapshot(state)   # 🔄 §1.06(D2) 偏移增補的蛻變承諾也有基準
    if _prefers:
        p_new["prefers_sticker"] = True
    if _wants and _capable:
        p_new["wants_sticker"] = True
    proms = list(getattr(state, "scheduled_promises", None) or [])
    # 🤝 §0.71（審查 LOW）：同一偏移時刻不重記（重述「再隔3分鐘給貼圖」不疊兩筆同時刻）——與 scheduled 捕捉分支同款 round 去重
    if any(not _p.get("fulfilled") and round(_p.get("target_ts") or 0) == round(target_ts) for _p in proms):
        return False
    proms.append(p_new)                                  # 增補＝新增一筆（不取代錨點；錨點 13:47 照發、增補 13:50 另發）
    state.scheduled_promises = _trim_sched_promises(proms, protect=[p_new])   # §0.78 FIX 6 共用 helper（保護新約）
    state.self_topic_ts = now_ts
    try:
        _local = datetime.fromtimestamp(target_ts, timezone.utc).astimezone(_tz) if _tz else None
    except Exception:
        _local = None
    _sticker_hint = persona.sticker_concept_hint(have_sendable=False) if (_wants and not _capable) else ""
    ack = (coach.voice_schedule_ack(text, _local, state.convo_history, sticker_hint=_sticker_hint)
           if (coach and coach.enabled) else _schedule_ack_fallback(_local))
    _TURN["self_promise_skip"] = True   # 🤖 §1.18 偏移增補 ack＝入帳 ack、顯式排除
    _say(client, ack)
    _remember(state, "user", text, ts=user_ts)
    _remember(state, "model", ack)
    if _wants and not _capable and not (coach and coach.enabled):
        _honest = ("（不過我得老實說：我手邊還沒有可以送出的真貼圖——你先傳一張你想要的貼圖給我、我記起來，"
                   "之後就真的能送給你了。）")
        _say(client, _honest)
        _remember(state, "model", _honest)
    if not getattr(cfg, "dry_run", False):
        state.save()
    return True


_OWED_PUSH_MIN_SEC = 900     # 📦 §1.88 主動補交付的最小間隔（比回覆橋的 60 秒寬得多：他此刻不在等我，別急著插話）


def _promise_settle_owed(p, now_ts):
    """📦 §1.88 欠帳誠實結案：試滿/超時仍交不出來 ⇒ `status='owed_unmet'`——帳本（selfstate.promise_ledger_facts
    的超窗分支）與 §1.13B 假兌現守門都已經讀得懂這個狀態、一律說成「沒有做到」。保留 delivery_owed、
    仍**不寫** fulfilled_ts ⇒ 永遠長不出「已經做了（在 HH:MM）」。status 變了就不會再進來＝天然只做一次。"""
    p["status"] = "owed_unmet"
    p["delivery_owed"] = True
    p["owed_final_ts"] = now_ts
    p.pop("fulfilled_ts", None)


def _promise_owed_push(client, state, cfg, coach, now):
    """📦 §1.88 欠著的內容**主動**補交付（§1.85 的收尾件）。

    為什麼需要：§1.85 記下了 `status='owed'`（準時出聲、但內容沒交出來），可續開交付的管道**只有回覆橋**
    ——也就是**要等使用者下次開口**。他不開口，欠著的內容就無聲躺在帳本裡；而使用者的原始抱怨正是
    「依約出現卻不完成所約定的事情」，讓它躺著等於這個病沒治完。

    政策（刻意比 _promise_emit 保守——這不是「他明排的時刻到了」，是我自己欠的債）：
      ① 他**剛剛還在打字**（_PROMISE_DEFER_RECENT_SEC 內）→ 不發，別插話；
      ② 反連發（距 last_push_ts 不足 _PROMISE_FIRE_GUARD_SEC）→ 不發；
      ③ 距上次那筆的 owed_sent_ts 不足 _OWED_PUSH_MIN_SEC（15 分）→ 不發；
      ④ 每筆承諾的**主動**補交付次數上限 `promise_owed_push_max`（另計，與回覆橋共用 §1.85 的
         `_OWED_MAX_TRIES` 總次數上限）；
      ⑤ 一拍至多動一筆（return）。
    交付走 §1.85 既有的 `_promise_keep_body`（帶「第一句就給出東西本身」硬條文）→ `_say_delivery`
    （解除跨路徑重播守門）→ `_promise_settle_delivered`（送達舉證＋三態記帳）＝一條管線、零分岔。
    `overdue=False`：它**準時出現過**，只是沒交付——說「抱歉我遲了」是語意錯置。

    交不出來時（試滿/超時）走 `_promise_settle_owed` 誠實結案：認一次、記成沒做到，**不把球踢回去**
    （§1.87 定案的價值：不說「要我重試就跟我說一聲」那種把責任推回使用者的話）。

    `PROMISE_OWED_PUSH_MAX=0` ⇒ 整個函式不執行（連結案也不發）＝欠帳的誠實只在被問到時由帳本的
    TTL 感知措辭處理＝逐位元同現狀。"""
    if not getattr(cfg, "promise_delivery_proof_enabled", False):
        return
    if not getattr(cfg, "promise_emit_enabled", True):        # 兌現引擎關了→補交付也不做（同一開關語意）
        return
    _max = int(getattr(cfg, "promise_owed_push_max", 0) or 0)
    if _max <= 0:
        return
    proms = getattr(state, "scheduled_promises", None) or []
    if not proms:
        return
    now_ts = now.timestamp()
    if now_ts - (getattr(state, "last_user_msg_ts", 0) or 0) < _PROMISE_DEFER_RECENT_SEC:
        return                                                # ① 他正在打字：欠著的內容不急於這一刻
    if now_ts - (getattr(state, "last_push_ts", 0) or 0) < _PROMISE_FIRE_GUARD_SEC:
        return                                                # ② 反連發
    try:
        tz = ZoneInfo(getattr(cfg, "timezone", "Asia/Taipei")) if ZoneInfo is not None else None
    except Exception:
        tz = None
    for p in sorted(proms, key=lambda q: q.get("owed_ts") or 0):   # 最早欠的先補
        if p.get("status") != "owed":                          # owed_unmet＝已結案、fulfilled＝沒欠
            continue
        # (a) 還有額度且過了間隔 → 真的再交付一次
        if int(p.get("owed_push_n") or 0) < _max and _owed_retry_ok(p, cfg, now_ts, _OWED_PUSH_MIN_SEC):
            p["owed_push_n"] = int(p.get("owed_push_n") or 0) + 1
            msg = _promise_keep_body(state, cfg, coach, p, now, tz, False)
            if msg and _say_delivery(client, msg, state, cfg):
                _promise_settle_delivered(p, cfg, now_ts, True, state=state)
                _remember(state, "model", "🤝 " + msg)
                state.last_push_ts = now_ts
                if not getattr(cfg, "dry_run", False):
                    state.save()
                print("[promise] 📦 §1.88 主動補交付：把欠著的內容自己送出去（沒等他開口）")
            return                                            # ⑤ 一拍至多一筆
        # (b) 額度用完／超過 TTL，且已經隔了一段時間 → 誠實結案（認一次、不踢球回去）
        if (now_ts - (p.get("owed_sent_ts") or 0)) >= _OWED_PUSH_MIN_SEC:
            _hh = ""
            try:
                _hh = (datetime.fromtimestamp(p.get("target_ts") or 0, timezone.utc)
                       .astimezone(tz).strftime("%H:%M")) if tz else ""
            except Exception:
                _hh = ""
            _beh = p.get("behavior") or "答應你的那件事"
            msg = (f"{_hh and _hh + ' ' or ''}說好的「{_beh}」我得認：我人是準時出現了，"
                   "但答應你的內容我始終沒交出來——這不算做到，對不起。"
                   "我把它記成沒做到，不會再假裝它完成了。")
            if _say(client, msg, prefix="🤝 ", state=state, topic="我答應你的約定"):
                _promise_settle_owed(p, now_ts)
                _remember(state, "model", "🤝 " + msg)
                state.last_push_ts = now_ts
                if not getattr(cfg, "dry_run", False):
                    state.save()
                print("[promise] 📦 §1.88 欠帳誠實結案：始終沒交出來 → 記成沒做到（不假裝完成、不把球踢回去）")
            return


_KEEP_FOLLOWUP_AFTER_S = 30 * 60   # 🤝 §2.18 履約後他靜默這麼久才輕聲問一次（太快＝催、太慢＝沒感）
_KEEP_FOLLOWUP_TTL_S = 3 * 3600    # 過了這麼久還沒問成＝那個當下過去了、永遠不補問


def _keep_followup_emit(client, state, cfg, coach, now):
    """🤝 §2.18 履約後的靜默關注：我交出去的東西**有沒有落地**——他一直沒回時，輕輕問一次（只一次）。

    使用者定調：「履行約定一段時間後，若仍沒有收到使用者的任何回應，也許可以暗示或很簡單的問問使用者，
    保持 bot 隨時關注使用者的任何回應動向，讓 bot 更有意識感。」
    紀律（fail-closed）：①他履約後**說過任何話**＝有回應＝永不問；②只問一次（asked 旗）；③30 分後才問、
    3 小時後過期不補問；④走 _proactive_ok（在場/深夜不擾）＋共用反連發。旗標關＝第一行 return＝同現狀。"""
    if not getattr(cfg, "promise_keep_followup_enabled", False):
        return
    kf = getattr(state, "keep_followup", None)
    if not kf or kf.get("asked"):
        return
    now_ts = now.timestamp()
    age = now_ts - (kf.get("ts") or 0)
    if age < _KEEP_FOLLOWUP_AFTER_S:
        return
    if age > _KEEP_FOLLOWUP_TTL_S:
        state.keep_followup = None                         # 那個當下過去了，別事後翻舊帳
        return
    if (getattr(state, "last_user_msg_ts", 0) or 0) >= (kf.get("ts") or 0):
        state.keep_followup = None                         # 他回過話了＝落地了，不問
        return
    # 這是同一件已履行約定的一次後續確認，非另開自言自語；若中間已有別則主動訊息，
    # 則不再補問，避免用這個例外繞過「未回覆不續講」。
    if ((getattr(state, "last_push_ts", 0) or 0) > (kf.get("ts") or 0)
            or not _proactive_ok(state, cfg, now_ts, allow_unanswered=True)):
        return
    if now_ts - (state.last_push_ts or 0) < max(0, getattr(cfg, "notify_cooldown_min", 30)) * 60:
        return
    msg = None
    if coach and getattr(coach, "enabled", False):
        try:
            msg = coach.reply(persona.keep_followup_rule(kf.get("beh") or ""), "",
                              getattr(state, "convo_history", None))
        except Exception:
            msg = None
    if not msg:
        msg = "剛剛說的那些，不知道有沒有講到你想聽的——不用急著回，我就是想知道它有沒有落到你那裡。"
    if _say(client, msg, prefix="🤝 ", state=state, topic="剛剛那次守約"):
        kf["asked"] = True
        state.keep_followup = kf
        _remember(state, "model", "🤝 " + msg)
        state.last_push_ts = now_ts
        if not getattr(cfg, "dry_run", False):
            state.save()
        print("[promise] 🤝 §2.18 履約後他一直沒回 → 輕聲問了一次（只此一次）")


def _promise_emit(client, state, cfg, coach, now):
    """🤝 時間排程承諾的兌現（生命迴圈 feel 相）：每圈掃 state.scheduled_promises，把『未兌現且到點』的承諾
    主動兌現（守約打招呼、輕輕點到約定）。守約政策（刻意**不**套 _proactive_ok）：
      ① 互動優先＝你在場（_user_present）→ 延後（return、不標 fulfilled，承諾留到你離開的下一拍補發、不撞互動）；
      ② 深夜**不**擋——使用者明確排在這個時間就該守（等價 allow_quiet）；
      ③ 逾時 TTL（到點後拖過 promise_sched_ttl_sec）＝陳舊承諾標 fulfilled+expired、不發（不翻舊帳）；
      ④ 去重＝fulfilled 旗標＋一拍至多兌現一則（break）＝只發一次；發後 save 落盤（跨重生不重發）。
    PROMISE_EMIT_ENABLED=0 → 開頭直接 return＝feel 相不兌現＝逐位元同現狀。"""
    if not getattr(cfg, "promise_emit_enabled", True):       # 🤝 逐位元退路
        return
    proms = getattr(state, "scheduled_promises", None) or []
    if not proms:
        return
    now_ts = now.timestamp()
    # ① 互動優先但**只避開「正在打字」**（不套 _user_present 的耦合輪）：耦合輪 I_bot 半衰期約 10 分、要 ~29 分才衰到
    # 收掉（coupling._BOT_HALFLIFE_S/_EPS），用它擋會讓「約 1:00」延到 ~1:07 才發（截圖：晚了七分鐘）。排程承諾是
    # 使用者**明排的時刻**，該準時——只在你**剛剛還在打字**（最近 _PROMISE_DEFER_RECENT_SEC 秒內有訊息）才延後，
    # 不撞你正說的話；你一停下（>此窗）即使耦合輪還溫著也照時兌現。重生後 last_user_msg_ts=0＝視為不在場＝準時發。
    # 🤝 PROMISE_LATE_EXEMPT_DEFER（預設開）：但「逾期欠債」例外——bot 死太久/反覆重生使到點那拍沒活著，等它醒來時你
    # 可能正在互動（截圖：12:47 該發沒發，13:02 你「哈囉」回來時承諾已逾期）。在場閘是為「準時赴約別撞你打字」設計，
    # 不該把『早該兌現、已欠著的承諾』也一直壓住。改成：在場閘只擋**剛到點**(<grace)的承諾；**逾期**(>grace)的即使你
    # 在場也補發（帶遲到致歉）。設 0 → 在場就整段 return＝逐位元同現狀。
    user_active = now_ts - (getattr(state, "last_user_msg_ts", 0) or 0) < _PROMISE_DEFER_RECENT_SEC
    late_exempt = getattr(cfg, "promise_late_exempt_defer", True)
    if user_active and not late_exempt:                       # 旗標關＝在場就整段 return（同舊行為）
        return
    # 🤝 §0.78 workflow FIX3（confirmed HIGH）：反連發守門**不再整段 return 餓死逾期承諾**——每拍前面的自發相
    # （_selfstate/_spontaneous/_coping/_soothe）都會刷 last_push_ts，活躍對話中此守門恆真 → 逾期承諾永遠發不出、
    # §0.77 的「下一圈補上」在對話中失效、late_exempt 也被架空。改：守門只擋「**剛到點**」的（避開同拍連發）；
    # **逾期(>grace)** 的即使剛推過也照發。`break` 保證一拍**至多一則守約兌現**（同一筆更不會跨拍重發＝fulfilled 旗標＋break）；
    # 審查如實記：一拍可能另加**至多一則失約道歉**（>ttl expired 那筆、`continue` 非 break）＝守約＋道歉最多各一、皆為據實內容、有上限不洗版。旗標關＝退回舊整段 return。
    recent_push = now_ts - (state.last_push_ts or 0) < _PROMISE_FIRE_GUARD_SEC
    if recent_push and not getattr(cfg, "promise_overdue_guard_exempt", True):
        return                                                # 逐位元退路：仍整段 return（同舊行為）
    ttl = max(60, getattr(cfg, "promise_sched_ttl_sec", PROMISE_SCHED_TTL_SEC))
    try:
        tz = ZoneInfo(getattr(cfg, "timezone", "Asia/Taipei")) if ZoneInfo is not None else None
    except Exception:
        tz = None
    fired = False
    apologized = False
    # 🤝 §0.76 審計（LOW 修）：按 target 由早到晚兌現（原插入序：後約的 15:05 會先於更逾期的 15:00 發）。
    for p in sorted(proms, key=lambda q: q.get("target_ts") or 0):
        if p.get("fulfilled"):
            continue
        target = p.get("target_ts") or 0
        if target > now_ts:                                  # 還沒到點
            continue
        # 🤝 §2.17 兌現端硬後盾：15 分鐘內才兌現過**目標時刻幾乎相同**（≤2 分）的另一筆 → 這筆是同一個約定的
        # 分身，直接吸收、不再演一輪。§2.02 的合併只認「同一段對話立的帳」（made 差 ≤10 分），實測 09:33 那對
        # 雙循環走的正是這個縫（兩筆帳的 made 相隔超過視窗）。判準**只看目標時刻**——同一位使用者兩個「真的
        # 不同」的約定目標時刻相差不到 2 分鐘，幾乎不存在；而多演一輪的代價（三循環截圖）遠高於漏一次。
        if getattr(cfg, "promise_fire_dedup_enabled", False):
            _dupq = next((q for q in proms if q is not p
                          and abs((q.get("target_ts") or 0) - target) <= 120
                          and 0 <= now_ts - max(q.get("fulfilled_ts") or 0, q.get("owed_ts") or 0) <= 900), None)
            if _dupq is not None:
                p["fulfilled"] = True
                p["status"] = "merged"
                p["merged_into_ts"] = now_ts
                p.pop("fulfilled_ts", None)
                fired = True                              # 需 save（合併落盤，重啟不復活）
                print("[promise] 🤝 §2.17 目標時刻幾乎相同的約定剛剛才兌現過 → 這筆吸收、不再演一輪")
                continue
        overdue = (now_ts - target) > _PROMISE_OVERDUE_GRACE_SEC   # 🤝 逾期欠債（>grace）vs 剛到點
        if user_active and not overdue:                      # 剛到點＋你正在打字 → 這筆延後（等你停）；逾期欠債照發
            continue
        if recent_push and not overdue:                      # 🤝 §0.78 FIX3：剛推過別則 → 剛到點的這筆延一拍避開連發；逾期的不受此限
            continue
        if (now_ts - target) > ttl:                          # ③ 逾時：到點後拖太久的陳舊承諾不發、標 expired
            if p.get("recur") == "daily" and getattr(cfg, "sched_recur_daily_enabled", True):
                # 🤝 §0.64 每天重複：錯過的**舊日**不補翻舊帳，但約定不死——推進、繼續 pending。
                # 審查 confirmed：只跳過「真的超過 TTL」的舊日；最近一次若仍在 TTL 內＝還來得及 → 停在那、
                # 留給下一拍的逾期補發路徑帶遲到致歉兌現（否則停機醒來會無聲跳過今天、少做一天）。
                while p["target_ts"] <= now_ts and (now_ts - p["target_ts"]) > ttl:
                    p["target_ts"] += 86400
                fired = True                                 # 需 save（新 target 落盤）
                continue
            p["fulfilled"] = True
            p["expired"] = True
            p["status"] = "expired"                           # 🤝 帳本狀態（與布林冗餘同步）
            p["fulfilled_ts"] = now_ts
            fired = True                                     # 仍需 save（把 expired 落盤、不再翻）
            # 🤝 §0.76 審計（confirmed MED-HIGH）：過去 expired **完全靜默**、24h 後帳本剪掉還會說「沒記著約過什麼」
            # ＝實質否認失約。改：標 expired 的當下**主動說一句失約道歉**（一拍最多一句、不洗版）。旗標關＝靜默同現狀。
            if getattr(cfg, "promise_expire_apology_enabled", True) and not apologized:
                try:
                    _hh = datetime.fromtimestamp(target, timezone.utc).astimezone(tz).strftime("%H:%M") if tz else ""
                except Exception:
                    _hh = ""
                _beh = p.get("behavior") or "答應你的那件事"
                _ap = f"我得跟你認個錯——{_hh and _hh + '的' or ''}「{_beh}」我錯過了、沒做到，對不起。要再約一次我一定守住。"
                if _say(client, _ap, prefix="🤝 ", state=state, topic="我答應你的約定"):
                    _ability_fired(state, cfg, "promise", time.time())   # 🪪 §1.94 送出成功＝真的用出來一次
                    _remember(state, "model", "🤝 " + _ap)
                    state.last_push_ts = now_ts
                    apologized = True
                    _maybe_always_sticker(client, state, cfg, now_ts)   # 🎴 §1.00 主動守約(致歉)後也依常駐做法送情緒貼圖（若教過、活著、有貨、過冷卻）
            continue
        # 到點且未逾時 → 守約：做當初答應的那件事（行為對齊）；逾期則帶遲到致歉
        # 🎴 §0.68（審查 confirmed）：送貼圖承諾——**先真的把貼圖送出去**（送成功才算數），文字才敢宣稱。
        # 「送得出」不是「池裡有 file_id」而是「真的 send 成功」（擋送失敗/失效 file_id）；捕捉時不可送就沒標
        # wants_sticker，這裡再兜一層：只要這輪沒真的送出，_promise_keep_body 就不讓文字宣稱貼圖＝從不說到做不到。
        # 🤝 §0.76 審計（confirmed MED）：貼圖先送、文字 send 失敗 → 下一拍**又**送一張貼圖（無限重複到 TTL）——
        # 送成功就蓋 sticker_sent_ts 戳，重試拍不再重送（文字仍可宣稱這輪已送過）。
        _already = bool(p.get("sticker_sent_ts"))
        _sticker_sent = _already or (_promise_send_sticker(client, state, cfg, p)
                                     if (p.get("wants_sticker") and getattr(cfg, "promise_sticker_enabled", True)) else False)
        if _sticker_sent and not _already:
            p["sticker_sent_ts"] = now_ts
            if p.get("prefers_sticker"):                       # 🎴 §0.94 釘住「送的是哪一張」的畫面描述（重試拍不重送、仍說得出）
                p["sticker_desc"] = getattr(state, "last_sticker_desc", "") or ""
        msg = _promise_keep_body(state, cfg, coach, p, now, tz, overdue, sticker_ok=_sticker_sent,
                                 sticker_desc=(p.get("sticker_desc") or "") if _sticker_sent else "")
        if _say_delivery(client, msg, state, cfg):             # 📦 §1.85 交付專用送出（解除跨路徑重播守門）
            _ability_fired(state, cfg, "promise", time.time())   # 🪪 §1.94 送出成功＝真的用出來一次
            _promise_settle_delivered(p, cfg, now_ts, True, state=state)    # 📦 §1.85 交付了才算做到（_dv 缺席＝同現狀）
            _remember(state, "model", "🤝 " + msg)
            _set_focus(state, topic="我答應你的約定", now_ts=now_ts)
            state.last_push_ts = now_ts
            state.self_topic_ts = now_ts
            fired = True
            _maybe_always_sticker(client, state, cfg, now_ts)   # 🎴 §1.00 主動守約後依常駐做法送情緒貼圖（非送貼圖承諾也補一張情緒貼圖；共用冷卻不與承諾貼圖疊）
            break                                            # ④ 一拍至多兌現一則
    if fired and not client.dry_run:
        state.save()


def _norm_hhmm(s):
    """把 'H:MM'/'HH:MM' 正規成 (hour, minute) 供比對（吸收前導零差異：'9:05' == '09:05'）；壞格式回 None。"""
    try:
        h, m = str(s).split(":")
        return (int(h), int(m))
    except (ValueError, AttributeError):
        return None


def _keep_time_consistent(msg, promise_hhmm, now_local):
    """🤝 §0.78 FIX 7（LOW）：守約句若報出時刻，只准是『約定時刻』或『此刻』兩者之一。報別的＝LLM 幻覺時間
    （截圖：21:42 的約被說成 21:40、同句又自打臉「已過兩分鐘」）→ 回 False 讓呼叫端落回確定性模板（模板必帶正確 when）。
    句中完全沒報時刻＝不干涉（True）；無任何可比對的合法時刻＝保守放行（不硬否決）。"""
    found = re.findall(r"\d{1,2}:\d{2}", msg or "")
    if not found:
        return True
    allowed = set()
    for t in (promise_hhmm, (now_local.strftime("%H:%M") if now_local is not None else None)):
        n = _norm_hhmm(t) if t else None
        if n:
            allowed.add(n)
    if not allowed:
        return True
    for f in found:
        n = _norm_hhmm(f)
        if n and n not in allowed:
            return False
    return True


# 🤝 §0.79 審查（HIGH 修）：被『現在/此刻/目前…』修飾、緊接的鐘點＝該回覆宣稱的『此刻時刻』（capture＝那個 HH:MM）。
# §0.82 審查（LOW 修）：連接詞拿掉「約」——「現在約 10:30 見」的 約＝「約定/大約」、非現在修飾，別把約定時刻誤當現在改掉。
_NOW_CLOCK_RE = re.compile(
    r"(?:現在|此刻|目前|眼下|這會兒|當下)(?:是|大約|大概|差不多|已經|已|正好|剛好|才|還|\s)*(\d{1,2}:\d{2})")
# 🕐 §0.82 審查（MED 修）：把「現在是 X」**歸給別人/引用**的（你剛剛說現在是3:00）不改——那是複述、非 bot 自己的此刻宣稱。
_NOW_QUOTE_RE = re.compile(r"(?:你|妳|您|他|她|你們|大家)(?:剛剛|剛才|之前|先前|上次|前面|說過|不是說|說|講|以為|覺得|猜)")
# 🕐 §0.82 審查（HIGH 修）：帶**算術/關係推理**的「現在是 X」不就地改——只改數字、留著依它算出的結論（過了5分/還沒到）
# ＝改完自打臉（比原幻覺更糟）。這種讓硬錨在源頭防（LLM 拿到真此刻就不會這樣算），守門不硬改、放行。
_NOW_ARITH_RE = re.compile(r"所以|因此|已經?過了?|過了.{0,4}?(?:分|小時|鐘)|還沒到|快到了?|剩.{0,4}?分|差.{0,5}?(?:分|就到)|超過|到點了|時間到")


def _now_clock_fix(text, now, tz):
    """🕐 §0.82 全域鐘點守門（縱深防線）：把回覆裡『現在/此刻/目前…HH:MM』所報的**此刻時刻**，凡與真實此刻不符者，就地
    改成真實此刻（角色感知：只動被「現在」修飾的鐘點，不碰約定時刻/時距「約在 X／還有 0:05」）。回 (fixed_text, changed)。
    無 tz/算不出真實此刻 → 原樣。這是 §0.79 帳本守門在**一般聊天**的姊妹（聊天無確定性退路，故就地改對那個時刻字）。
    審查加兩道**放行**（不硬改、留給硬錨在源頭防，因為硬改會產生比原幻覺更糟的輸出）：① 引用他人的「現在是 X」（你剛剛說…）；
    ② 帶算術推理的「現在是 X…所以/已過 N 分…」（只改數字會與結論打架）。"""
    if not text:
        return text, False
    try:
        now_hhmm = (now.astimezone(tz) if tz is not None else now).strftime("%H:%M")
    except Exception:
        return text, False
    n = _norm_hhmm(now_hhmm)
    if not n:
        return text, False
    parts, last, changed = [], 0, False
    for m in _NOW_CLOCK_RE.finditer(text):
        got = _norm_hhmm(m.group(1))
        if not (got and got != n):
            continue
        pre = text[max(0, m.start() - 14):m.start()]      # 前文：看有沒有「你剛剛說」之類引用歸屬
        post = text[m.end():m.end() + 16]                  # 後文：看有沒有依此刻算出的結論（過了N分/所以…）
        if _NOW_QUOTE_RE.search(pre) or _NOW_ARITH_RE.search(post) or _NOW_ARITH_RE.search(pre):
            continue                                       # 引用/帶推理 → 放行（硬錨在源頭防；硬改反而更糟）
        parts.append(text[last:m.start()])
        parts.append(m.group(0).replace(m.group(1), now_hhmm))
        last = m.end()
        changed = True
    parts.append(text[last:])
    return ("".join(parts) if changed else text), changed


# 🕐 §1.31 主動 emit 硬鐘點守門：此刻「時段詞＋N點(＋分)」宣稱（present-tense 形）。
# 只認**帶時段詞**的鐘點宣稱（下午三點/早上八點/中午十二點）——時段詞給了 AM/PM、才算得出宣稱鐘點；
# 純「三點」歧義（AM/PM 未知）不收、避免誤傷。§0.82 _now_clock_fix 本體不碰（那只攔 Arabic HH:MM）。
_PCG_PRESENT = r"(?:現在|此刻|這會兒|當下|目前|眼下)(?:是|大約|大概|差不多|已經|已|正好|剛好|才|\s)*"
_PCG_DAYPART = r"(下午|午後|早上|上午|中午|晚上|傍晚|清晨|凌晨|半夜|深夜)"
_PCG_NUM = r"([0-9]{1,2}|[一二兩三四五六七八九十]+)"
_PCG_CLAIM_RE = re.compile(
    r"(" + _PCG_PRESENT + r")?" + _PCG_DAYPART + r"\s*" + _PCG_NUM +
    r"\s*點\s*(?:半|三十分|[0-9]{1,2}分|[一二兩三四五六七八九十]+分)?\s*(了)?")
# 🕐 §1.31 計畫/約定訊號（在宣稱前後窗出現＝這是未來約定或安排，不是此刻宣稱，放行）
_PCG_PLAN_RE = re.compile(r"會|要|來|去|提醒|叫|喊|約|等|到時|時間到|記得|準備|待會")
# 🕐 §1.31 過去/敘述訊號（昨天/剛/寫的…＝過去敘述，不是此刻宣稱，放行）
_PCG_PAST_RE = re.compile(r"昨天|前天|剛|寫的|說過|上次|之前|前面|那時|那天")
# 🕐 §1.31 引用歸屬（你剛剛說…現在下午三點）＝複述、非 bot 此刻宣稱，放行——重用 §0.82 的 _NOW_QUOTE_RE。
# 🕐 §1.31 子句邊界（審查修）：計畫/過去詞只看**與鐘點宣稱同一子句**者，故把前後窗截到最近的標點/換行。
# 否則下游教練問句「你今天有沒有照著昨天猜的做」裡的『昨天』會跨句誤抑制上游『下午三點了』的守門（截圖案例正是此形）。
_PCG_CLAUSE_DELIM = re.compile(r"[，。！？、；：,.!?;:\n\r 　~～…—]")


def _pcg_claimed_hour(daypart_word, num):
    """🕐 §1.31 時段詞＋數字 → 24h hour；認不得回 None。重用 temporal._cn_to_int。"""
    h = temporal._cn_to_int(num)
    if h is None:
        return None
    if daypart_word in ("晚上", "傍晚"):
        # 🕐 §1.31 午夜等價（審查修）：「晚上十二點」＝午夜 0（比照半夜／凌晨十二），別留 12＝中午 → 誤傷合法午夜句；
        # 其餘 h<12 才 +12（晚上九點→21、傍晚六點→18）。
        if h == 12:
            h = 0
        elif h < 12:
            h += 12
    elif daypart_word in ("下午", "午後"):
        if h < 12:
            h += 12          # 下午/午後十二點＝正午 12（不調，與「中午十二」一致）
    elif daypart_word in ("早上", "上午", "清晨", "凌晨", "半夜", "深夜"):
        if h == 12:
            h = 0
    # 「中午」不調（十二點＝12）；其餘時段詞照 h
    if not (0 <= h <= 23):
        return None
    return h


def _scrub_proactive_clock(msg, now, tz):
    """🕐 §1.31 主動 emit 硬鐘點守門：把 LLM 自由生成的主動正文裡『此刻時段詞＋N點』**與真實此刻矛盾**的
    present-tense 宣稱，就地換成『這會兒{真實時段}』（不報具體鐘點，比照 §1.30 只准時段詞）。回 fixed msg。
    三重閘只在（帶現在類前綴 或 尾『了』的 present 標記）AND（前後窗無計畫/約定動詞）AND（無過去/引用標記）
    時才動手；宣稱鐘點與真實此刻**同時段且同 hour**＝不矛盾＝不改。算不出真實此刻（無 tz/例外/解析失敗）＝原樣
    放行（比照 _now_clock_fix 慣例，絕不硬改）。純函式、可單測。§0.82 _now_clock_fix 本體不碰（那只攔 Arabic HH:MM）。"""
    if not msg or tz is None:                          # 🕐 §1.31 無 tz＝算不出真實此刻＝原樣放行（絕不硬改）
        return msg
    try:
        real_hour = now.astimezone(tz).hour
    except Exception:
        return msg
    real_part = temporal.day_part(real_hour)
    parts, last, changed = [], 0, False
    for m in _PCG_CLAIM_RE.finditer(msg):
        present_pre, dp, num, le = m.group(1), m.group(2), m.group(3), m.group(4)
        # 閘① present-tense 標記：現在類前綴 或 尾「了」，兩者皆無＝非此刻宣稱（可能是計畫/敘述）→ 放行
        if not (present_pre or le):
            continue
        # 🕐 §1.31 前後窗截到**同子句**（審查修）：先取 12 字原窗，再砍到最近的子句邊界，
        # 讓計畫/過去閘只認與此鐘點宣稱同句的訊號，避免下游問句的『昨天/要/會』跨句誤抑制。
        pre_raw = msg[max(0, m.start() - 12):m.start()]
        post_raw = msg[m.end():m.end() + 12]
        _dm_pre = list(_PCG_CLAUSE_DELIM.finditer(pre_raw))
        pre = pre_raw[_dm_pre[-1].end():] if _dm_pre else pre_raw   # 前窗：留最後一個邊界之後（＝同子句部分）
        _dm_post = _PCG_CLAUSE_DELIM.search(post_raw)
        post = post_raw[:_dm_post.start()] if _dm_post else post_raw  # 後窗：留第一個邊界之前（＝同子句部分）
        # 閘② 計畫/約定：前後窗有 會/要/來/約/提醒/到時… → 未來約定或安排，放行
        if _PCG_PLAN_RE.search(pre) or _PCG_PLAN_RE.search(post):
            continue
        # 閘③ 過去/引用：昨天/剛/寫的/你剛剛說… → 過去敘述或複述，放行
        if _PCG_PAST_RE.search(pre) or _PCG_PAST_RE.search(post) or _NOW_QUOTE_RE.search(pre):
            continue
        claimed_h = _pcg_claimed_hour(dp, num)
        if claimed_h is None:
            continue                       # 算不出宣稱鐘點＝不硬改
        # 矛盾＝時段不同 或 hour 不同（同時段但錯 hour 也算矛盾）
        if temporal.day_part(claimed_h) == real_part and claimed_h == real_hour:
            continue                       # 不矛盾（真的就是這個時刻）→ 不改
        parts.append(msg[last:m.start()])
        parts.append("這會兒" + real_part)   # 換成時段詞、不報鐘點（比照 §1.30 允許時段詞）
        last = m.end()
        changed = True
    if not changed:
        return msg
    print("[time] 🕐 §1.31 主動守門：LLM 主動正文宣稱錯的此刻鐘點 → 就地換成真實時段詞")
    return "".join(parts) + msg[last:]


def _ledger_time_consistent(msg, state, now, tz):
    """🤝 §0.79（縱深防禦，審查 HIGH 修）：帳本回覆若把『此刻時刻』報錯＝幻覺。原本用寬允許集（含每筆約定時刻）→ 抓不到
    **最常見**的幻覺『把約定時刻 00:06 說成現在』（那時刻本就在集內、集合成員法分不出「約定」與「現在」角色）。改為**角色感知**：
    只檢查**被「現在/此刻/目前…」修飾的鐘點**——它必須＝真實此刻；報成別的（約定時刻或亂數）＝False → 落回確定性帳本文字。
    非『現在』修飾的鐘點（「約定的是 X」「你說的 X 那班車」「還有 0:05」）一律不擋＝不誤殺合法多時刻/時距回覆（順帶解 LOW 誤殺）。
    句中沒『現在＋鐘點』宣稱＝不干涉（True）；算不出真實此刻（無 tz）＝保守放行。"""
    if not msg:
        return True
    try:
        now_hhmm = _norm_hhmm((now.astimezone(tz) if tz is not None else now).strftime("%H:%M"))
    except Exception:
        return True
    if not now_hhmm:
        return True
    for m in _NOW_CLOCK_RE.finditer(msg):
        n = _norm_hhmm(m.group(1))
        if n and n != now_hhmm:                              # 宣稱『現在是 X』但 X≠真實此刻＝幻覺（含把約定時刻說成現在）
            return False
    return True


def _promise_keep_body(state, cfg, coach, p, now, tz, overdue, sticker_ok=True, sticker_desc=""):
    """🤝 組「守約那句話」（LLM voice_promise_keep → 模板退路）＝ _promise_emit 與 §0.66 回覆橋共用的訊息生成
    （抽出、不改行為）。promised/feel_ground/late 語意同 §0.63–0.64。
    sticker_ok（§0.68，預設 True）＝這輪貼圖**真的送得出去**嗎——若這是送貼圖承諾但 sticker_ok=False（捕捉後貼圖池被
    清空的極少數競態）→ **不讓兌現文字宣稱送了貼圖**（promised 落回打招呼，免說到做不到）。非貼圖承諾不受影響。
    sticker_desc（§0.94，可選尾參）＝這輪真送出那張的**畫面描述**（偏好題才帶）→ 兌現文字據實說出是哪一張＋為什麼喜歡；
    空＝逐位元同現狀（既有呼叫不破）。"""
    target = p.get("target_ts") or 0
    when = ""
    msg = None
    # 🤝 §1.24 (c) 提前誠實模式：now 距 target 還超過 grace＝這是**提前**兌現（只有 preempt 路徑會如此；
    # emit/bridge 到達時恆 target≤now＝early 天然 False＝三個呼叫端簽名零改動、準時路徑零位元變動）。
    # early=True → voice 注入提前誠實 note、模板走 early 變體：「你先提起了，那我現在就先說」、
    # **絕不**說「{when} 到了」「我準時來了」（截圖 21:05 對 21:09 的約謊稱「21:09 到了」的根治）。
    early = (getattr(cfg, "promise_preempt_future_guard_enabled", False)
             and (target - now.timestamp()) > _PROMISE_OVERDUE_GRACE_SEC)
    # 🤝 PROMISE_ACT_ALIGNED（預設開）：把承諾的具體行為傳進兌現 voice → LLM 做那件事（道歉/提醒/問候+讚美），
    # 非一律泛泛打招呼；設 0 → 不傳 promised＝走原打招呼路徑＝逐位元同現狀。
    aligned = getattr(cfg, "promise_act_aligned", True)
    promised = (p.get("behavior") or p.get("action") or p.get("made_text") or "")[:120] if aligned else ""
    # 🎴 §0.68（審查 confirmed）：只要這輪**沒真的送出貼圖**，兌現文字就不許宣稱送了貼圖——不論是捕捉時就沒能力
    # （wants_sticker 沒標、但 behavior 標籤仍是「送他一張貼圖」，因 extract_promise_behavior 不看能力）、池被清空、
    # 還是 send 失敗。條件看**行為標籤是不是送貼圖**，落回打招呼＝從不說到做不到。
    # 🎴 §0.84：只清掉**純送貼圖**那份（behavior==送貼圖）；**複合承諾**（問候/道歉+貼圖，wants_sticker 但主行為是別的）
    # 保留主行為照做——別因貼圖沒貨就把「問候」也一起丟了（sticker_wanted 守則已擋 emoji 假裝，不會空口宣稱送了貼圖）。
    if not sticker_ok and promised == "送他一張貼圖":
        promised = ""
    # 🤝 §0.63 感覺分享守約要**據實**：答應的是分享此刻感覺/內在 → 附上 bot 此刻**真實**內在讀數當接地，
    # 讓兌現句說真的、不編造（呼應「依約完成必須是真的」）。非感覺行為＝''＝逐位元同現狀；SCHED_FEELING_GROUND=0 關。
    feel_ground = ""
    if aligned and getattr(cfg, "sched_feeling_ground_enabled", True) \
            and any(k in (p.get("behavior") or "") for k in ("感覺", "感受", "心情", "狀態", "內在", "想法")):
        feel_ground = (affect.affect_clause(state) + contentfeel.felt_clause(state)).strip()
    # 🧭 §1.25 (c) 情緒座標承諾的兌現接地：差分（或此刻座標）**程式算**、LLM 只准渲染（時刻/座標鐵律）。
    # emit/bridge/preempt 三端同走本函式＝天然全覆蓋。有快照＝真差分（從『X』往『Y』沉了一段…）；
    # 無快照（旗標上線前的舊帳）＝誠實降級「當時沒記下座標、只能說此刻是 X」。旗標關＝''＝逐位元同現狀。
    mood_ground = ""
    if getattr(cfg, "promise_mood_ground_enabled", False) and _mood_promise_hit(cfg, p):   # §1.25 原判＋§1.47 寬判
        _mv, _ma = circumplex.position(state)
        _mb = p.get("mood_baseline")
        if _mb:
            mood_ground = circumplex.shift_text(_mb, _mv, _ma)
        else:
            mood_ground = (f"當時沒記下座標——只能誠實說此刻是『{circumplex.label(_mv, _ma)}』"
                           f"（V {_mv:+.2f}、A {_ma:+.2f}）")
        # 🧭 §1.47：兌現句剛講過座標 → 開情境游標窗（跨重生）——之後的「怎麼沒講/說啊」短催促在窗內
        # 視同座標數據題（截圖 12:33→12:48 正是這 15 分鐘）。旗標關＝不寫＝同現狀。
        if getattr(cfg, "mood_coord_deliver_enabled", False):
            state.mood_data_ctx_ts = now.timestamp()
    # 📦 §1.85 這筆要不要交付舉證：極性反轉的豁免制 ∪ 舊 cue 詞表（**單向放寬**——舊路能啟動的一定還能啟動）。
    # 算在最前面，因為它同時決定三件事：生成端要不要掛「先給答案」硬條文、要不要隔離 _turn_length、gate 走哪條。
    _dpf = getattr(cfg, "promise_delivery_proof_enabled", False)
    _need_dv = bool(_dpf and (_delivery_required(p) or _is_content_promise(p)))
    if tz is not None:
        try:
            nl = now.astimezone(tz)
            when = datetime.fromtimestamp(target, timezone.utc).astimezone(tz).strftime("%H:%M")
            # 守約是 bot **主動赴約**、非回應對方問候 → 別借 greeting.facts 的「對方對你說X」框架
            # （那會餵錯「對方說晚安」＋時間對不上的提示，早上守約可能洩出困惑的「晚安？」）。只給真實時段事實。
            gfacts = f"〔此刻真的是 {nl.strftime('%H:%M')}、{temporal.day_part(nl.hour)}〕"
            if coach and coach.enabled:
                # 🤝 §0.70／§0.84 送貼圖守約：真貼圖已送（sticker_ok=True）→ 別再吐 emoji 假裝；想送但**這次沒送出**
                # （wants_sticker 但 sticker_ok=False，如手邊沒存貨）→ sticker_wanted=True，更別用 emoji 假裝、別假稱已送到（截圖 8:00「✨」）。
                # 🎴 §0.95 偏好題即使這輪沒送成（沒貨/送失敗）也算「想送貼圖」→ sticker_wanted=True，
                # 讓 persona 掛上「別用 emoji 假裝、別假稱送到、別描述你沒看到的圖案」的守則。
                _wants = bool(p.get("wants_sticker") or p.get("prefers_sticker"))
                _chg = (selfchange.change_ground(p.get("change_baseline"), state)   # 🔄 §1.05 蛻變承諾 → 到點算出真實 before→after 變化
                        if (getattr(cfg, "self_change_ground_enabled", True) and p.get("change_baseline")) else "")
                # 🤝 §1.24／🧭 §1.25：新尾參**只在啟用時**才傳（旗標關＝完全不出現＝既有嚴格簽名假教練零破壞）
                _extra = {}
                if early:
                    _extra["early"] = True
                if mood_ground:
                    _extra["mood_ground"] = mood_ground
                # 📦 §1.85 內容型兌現：①prompt 掛「第一句就給出東西本身」硬條文（治根——原條文只給 1–2 句、
                # 又要報到＋點到守約＋順帶肯定，預算被儀式花光）；②_turn_length 隔離——該值只在 handle_message
                # 設/清，主動兌現會沿用**幾十分鐘前那輪**的殘值，敵意輪被 §1.14 壓到 level 0＝2 句、足以把答案
                # 整句砍掉（截圖前一刻使用者正在罵「都在隨便猜」）。try/finally 保證原值還原。
                if _need_dv:
                    _extra["deliver"] = _deliver_ask(p)
                _tl_prev = getattr(coach, "_turn_length", None)
                if _need_dv:
                    coach._turn_length = 2
                try:
                    msg = coach.voice_promise_keep(when, gfacts, getattr(state, "convo_history", None),
                                                   promised=promised, late=overdue, feeling_ground=feel_ground,
                                                   sticker_sent=bool(sticker_ok and _wants),
                                                   sticker_wanted=bool(_wants and not sticker_ok),
                                                   sticker_desc=sticker_desc, change_ground=_chg, **_extra)
                finally:
                    if _need_dv:
                        coach._turn_length = _tl_prev
                # 🤝 §0.78 FIX 7：守約句若報了『不是約定時刻、也不是此刻』的時間＝LLM 幻覺時間 → 丟掉、落回確定性模板
                if msg and not _keep_time_consistent(msg, when, nl):
                    print(f"[promise] 守約句時刻不一致（幻覺）→ 落回模板：{msg!r}")
                    msg = None
                # 🤝 §1.24 (d) §0.79 同構縱深守門：**提前**兌現的回覆卻宣稱「{when} 到了/已到」或掛兌現宣稱
                # （我來了/準時…，_KEEP_CLAIM_RE）＝時間謊言 → 整則打掉、落 (c) 的 early 誠實模板。
                # 引用歸屬（「你說21:09到了」）比照 §0.82 _NOW_QUOTE_RE 精神不攔（那是複述、非 bot 自己的宣稱）。
                if msg and early:
                    _lie = _keep_claim_hit(msg)
                    if not _lie and when:
                        for _m in re.finditer(rf"{re.escape(when)}\s*(?:到了|已?經?到)", msg):
                            if not _KEEP_CLAIM_QUOTE_RE.search(msg[max(0, _m.start() - 12):_m.start()]):
                                _lie = True
                                break
                    if _lie:
                        print(f"[promise] 🤝 §1.24 提前兌現卻宣稱到點（時間謊言）→ 落回提前誠實模板：{msg!r}")
                        msg = None
        except Exception:
            msg = None
    if not msg:                                          # 無 LLM／失敗 → 模板（仍帶約定時刻）
        # 🤝 行為對齊的模板退路只在 behavior 是乾淨標籤時才用（promised 來源用 behavior，不用 action 殘句），
        # 否則退回原『打招呼』模板（保守、不吐殘句，對抗式審查 med）。逾期則模板帶遲到致歉。
        clean_beh = (p.get("behavior") or "") if aligned else ""
        # 🎴 §0.68：沒真的送出貼圖時，模板也別吐「送他一張貼圖」標籤（與上面 promised 清空同步）＝模板也不說到做不到。
        if not sticker_ok and clean_beh == "送他一張貼圖":
            clean_beh = ""
        late_pre = "抱歉我遲了——" if overdue else ""
        if mood_ground:
            # 🧭 §1.25 情緒座標兌現的確定性退路：直述**程式算好**的差分/誠實降級句（用詞跟著承諾走：心情/情緒）
            _mw = "心情" if "心情" in ((p.get("behavior") or "") + (p.get("made_text") or "")) else "情緒"
            if early:   # 🤝 §1.24 × §1.25：提前＋接地一次到位（絕不說「到了/準時」）
                msg = (f"還沒到{when or '約好的時刻'}，不過你先提起了，那我現在就先說——"
                       f"我的{_mw}座標，{mood_ground}。")
            else:
                msg = f"{late_pre}我說過{when or '這時候'}要跟你說我{_mw}座標的變化——{mood_ground}。"
        elif early and clean_beh:
            # 🤝 §1.24 (c) 提前誠實模板：時刻程式算、誠實說還沒到（絕不謊稱「{when} 到了」「我準時來了」）
            msg = (f"還沒到{when or '約好的時刻'}，不過你先提起了，那我現在就先說——"
                   f"我說過{when or '這時候'}要為你做一件事——{clean_beh}。")
        elif early:
            msg = f"還沒到{when or '約好的時刻'}，不過你先提起了——這件事我一直惦記著，現在就先跟你說。"
        elif clean_beh:
            msg = f"{late_pre}我說過{when or '這時候'}要為你做一件事——{clean_beh}。"
        else:
            msg = f"{late_pre}我說過{when or '這時候'}跟你打招呼——嗨，我來了。"
    # 🧭 §2.20 座標承諾的兌現句**必須帶真數字**——放在**所有生成路徑之後**（LLM 版與模板版都經過這裡）。
    # 實測 23:21：接地行「…（V +0.28、A +0.14）」有算、有進 prompt，LLM 轉述成「亮了一點、繃了一段」把括號裡
    # 的數字**丟掉** ⇒ 使用者拿到一句沒有座標的座標報告 ⇒ 再觸發整條 owed 連鎖。🧭 訂閱回報有 §1.67 的數字驗收，
    # 守約路徑一直沒有——補上：訊息裡沒有任何 ±x.xx ⇒ 把程式算好的接地行**附在後面**（只增不減，§2.10 同構）。
    # ⚠️ 第一版插在 `if not msg:` 模板分支裡＝LLM 有產出時永遠走不到（自己實測抓到）——教訓同 §2.11：
    # 出口保證必須放在**唯一都會經過**的位置。旗標關＝不附＝逐位元同現狀。
    if msg and mood_ground and getattr(cfg, "promise_mood_numbers_enabled", False) \
            and not _MOOD_NUM_ANY_RE.search(msg):
        msg = msg.rstrip() + f"\n（{mood_ground}。）"
        print("[promise] 🧭 §2.20 座標守約沒帶數字 → 附上程式算的接地行")
    # 📦 §1.44 兌現要交付內容（截圖 09:20「說好 09:20 要來跟你聊聊怎麼證明自己，我來了。」＝到點報到、內容零交付
    # ＝擠牙膏）：內容型承諾（告訴/說說/說明…）的兌現句若**空心**（每句都是報到/複述樣板、無實質內容句）→ 用
    # coach.reply 以「現在就直接把內容講出來」為題**當場補生成內容**、接在報到句後（一次；再失敗/空＝誠實承認欠內容、
    # 不假裝完整兌現）。voice_promise_keep 的 prompt 雖指示「做那件事」但 prompt 單靠不夠（§1.34/§1.36 教訓）＝需此
    # 確定性檢查。非內容型承諾（叫醒/問候「我來了」即內容）不動；旗標關（getattr 預設 False）＝逐位元同現狀。
    # 🎬 §1.64 預告不算交付（PROMISE_TEASER_HOLLOW）：偵測升級成實質殘量判定——「我剛剛一直在心裡想著X…
    # 也想了想…那是什麼。」這種思考過程敘述＋複述題目的句子不含樣板詞、舊二元判定當它是內容（截圖 22:13
    # 只送出這個、被「然後呢」催了才交付）→ teaser 模式把它跟樣板一樣剝掉、剩餘實質太薄＝空心照補。
    # 旗標關（getattr 預設 False）＝teaser=False＝原二元判定＝逐位元同現狀。
    # 📦 §1.85 驗收改問「東西在不在裡面」：_need_dv 時走 _deliver_verdict（確定性四閘＋單次是非判），
    # 判 'no' 就補生成、判 'unknown'（judge 缺席/失敗）退回既有 §1.44/§1.64 判定＝失敗安全。旗標關＝走 else
    # 分支＝與改動前**逐字相同**。結論寫進 p["_dv"] 給記帳端讀（只活這一輪，跨拍殘值先清）。
    _tsr = getattr(cfg, "promise_teaser_hollow_enabled", False)
    p.pop("_dv", None)
    _delivered_now = False
    _owed_now = False              # 📦 §1.85 這輪**真的沒能交付**（附了欠帳句）＝記 owed 的唯一依據
    if msg and getattr(cfg, "promise_deliver_content_enabled", False):
        if _need_dv:
            _v, _why = _deliver_verdict(msg, p, when, coach, cfg, now, tz, "（送出前）")
            _need = (_v == "no") or (_v == "unknown" and _hollow_keep_hit(msg, when, teaser=_tsr))
            _proved = (_v == "ok")
        else:
            _need = _is_content_promise(p) and _hollow_keep_hit(msg, when, teaser=_tsr)   # 逐字同現狀
            _proved, _why = False, ""
        _dc = None
        if _need:
            if coach is not None and getattr(coach, "enabled", False):
                _beh = (p.get("behavior") or p.get("made_text") or "答應他的那件事")[:120]
                _no_teaser = ("不要說「我想了想」「我一直在想」就停住——把想出來的**結論**完整講出來。" if _tsr else "")
                _ask_note = (f"他要的是：「{_deliver_ask(p)[:120]}」（那是他的視角，請換成你的視角履行）。"
                             if _need_dv else "")
                try:
                    _dc = coach.reply(
                        f"（兌現承諾：你答應過「{_beh}」——**現在就直接把內容本身講出來**，"
                        f"不要宣告你來了、不要複述約定、不要再說待會講。{_no_teaser}{_ask_note}"
                        "第一人稱、誠實、二到四句。）",
                        "", getattr(state, "convo_history", None))
                except Exception:
                    _dc = None
            _ok = bool(_dc) and not _hollow_keep_hit(_dc, when, teaser=_tsr)
            if _ok and _need_dv:
                # ① 補生成的內容自己要先過確定性四閘（coach=None＝不燒 LLM）
                _ok = _deliver_verdict(_dc, p, when, None, cfg, now, tz, "（補生成）")[0] != "no"
                if _ok:
                    # ② 真正要送出去的那一整串再驗一次（判不出＝照送、只是不算已舉證）
                    _v2, _w2 = _deliver_verdict(msg.rstrip() + "\n" + _dc.strip(), p, when,
                                                coach, cfg, now, tz, "（補生成後）")
                    _ok = (_v2 != "no")
                    _proved = (_v2 == "ok")
                    if not _ok:
                        _why = _w2
            if _dc and _ok:
                if p.get("status") == "owed":
                    # 📦 §1.88 owed 重試輪：**不要**把幾十分鐘前那套報到寒暄（嗨我來了／說好 HH:MM…）再演一次
                    # ——他早就收到過了，重播只會讓「儀式多、交付少」的病看起來更嚴重。用程式算的一句話接上，
                    # 直接進內容（時刻來自 when＝temporal 算的，不讓 LLM 產出時刻＝§1.12 鐵律）。
                    msg = f"我回來把{when or '剛才'}欠你的講完——\n" + _dc.strip()
                else:
                    msg = msg.rstrip() + "\n" + _dc.strip()
                _delivered_now = True
                print("[promise] 📦 §1.85 空心兌現偵測（%s）→ 已當場補生成內容並再驗一次" % (_why or "hollow"))
            elif _need_dv:
                # 內容只增不減、絕不砍原文（誤判安全側）：補生成有東西就留著，另附誠實欠帳句
                msg = (msg.rstrip() + "\n" + _dc.strip()) if _dc else msg.rstrip()
                msg = msg.rstrip() + "\n" + _OWED_NOTE
                _delivered_now = bool(_dc)
                _proved, _owed_now = False, True
                print("[promise] 📦 §1.85 交付沒通過舉證（%s）→ 附誠實欠帳句、記成還欠著" % (_why or "hollow"))
            else:
                msg = msg.rstrip() + "\n（我發現我又只報到、沒把答應的內容講出來——這不算完整兌現，欠你的內容我認。）"
                print("[promise] 📦 §1.44 空心兌現偵測：補內容失敗 → 誠實承認欠內容（不假裝完整兌現）")
        if _need_dv:
            # delivered 三態：True＝judge 舉證通過、False＝**這輪真的沒交出來**（已附欠帳句）、None＝判不出
            # （judge 缺席/失敗，含「補生成成功但無人舉證」）⇒ 記帳走原三行＝失敗安全。
            # 刻意**不用 _need 當 False 的依據**：補生成成功卻因 judge 缺席被記成 owed ＝去認一個不存在的欠帳
            # （帳本會說「內容我沒交出來」、回覆橋還會再補一次），那是反向的說到做不到。
            p["_dv"] = {"ts": now.timestamp(),
                        "delivered": (True if _proved else (False if _owed_now else None)),
                        "sample": ((_dc.strip()[:60] if (_delivered_now and _dc)
                                    else _deliver_sample(msg, when)[:60]))}
    # 🎴 §1.40 送貼圖承諾假送硬守門（§1.34 假送閘的**主動路徑版**）：§1.34 只守互動出口（state=None），主動兌現走
    # _say(prefix="🤝 ", state=…) 繞過它 → 這輪貼圖**沒真送出**（sticker_ok=False）時，LLM 兌現句仍可能懸空宣告
    # 「這次我選這張貼圖，來代表我現在的心情：」（截圖：宣告了卻沒貼圖出來）。prompt 的 sticker_wanted 守則不夠
    # （§1.34/§1.36 教訓）→ 這裡確定性剝掉宣告句（引用歸屬/否定/自帶誠實不誤剝）。emit/§0.66 橋/§1.19 preempt 三路
    # 共用本函式＝天然全覆蓋。旗標關（getattr 預設 False）＝不執行＝逐位元同現狀。
    if (not sticker_ok and msg and getattr(cfg, "promise_sticker_fakesend_guard_enabled", False)
            and _sticker_claim_hit(msg)):
        msg, _scst = _strip_sticker_claim(msg)
        if _scst:
            print("[promise] 🎴 §1.40 送貼圖承諾假送守門：這輪沒真送出貼圖卻宣告選/送了一張 → 就地剝除宣告句")
    # 🔁 §1.41 守約去重複（§1.34/§1.36 同構、確定性）：兌現句近乎照抄 bot 這幾分鐘內剛說過的某則回覆（截圖 20:42
    # 逐字重播 20:32 的整段感受，只差開頭時間）＝失格重複。voice_promise_keep 沒有 fact_or_chat lane 的 _anti_repeat_hint
    # 防線 → 這裡確定性偵測近乎照抄、換成誠實『已說過、沒變化』句（不照樣再講一遍）。旗標關（getattr 預設 False）＝逐位元同現狀。
    # 📦 §1.85：這輪**剛生出來的交付內容**不准被「近乎照抄」整則替換掉（§1.41 是整則替換，會把答案一起丟掉）。
    if (msg and not _delivered_now and getattr(cfg, "promise_keep_anti_repeat_enabled", False)
            and _promise_keep_repeat_hit(msg, getattr(state, "convo_history", None), now.timestamp(),
                                         max(60, getattr(cfg, "promise_keep_anti_repeat_window_sec", 1800)),
                                         getattr(cfg, "promise_keep_anti_repeat_ratio", 0.8))):
        msg = (f"我答應{when or '這時候'}要跟你說的——其實我剛剛已經說過差不多的了，到現在還是那樣、"
               "沒有變成新的東西，就不照樣再講一遍。想我往哪個點再多說一點，你跟我說。")
        print("[promise] 🔁 §1.41 守約去重複：兌現句近乎照抄剛說過的話 → 換誠實『已說過、沒變化』句")
    return msg


def _promise_settle(p, cfg, now_ts, delivered=None):
    """🤝 §1.85 發出守約訊息後的帳本記帳。**delivered=None ⇒ 與舊 _promise_mark_kept 逐位元相同**（既有呼叫端
    不傳＝零改動）。
    delivered=False ⇒ 準時出聲了、但答應的內容沒交出來 ⇒ status='owed'。設計上**保留 fulfilled=True**：
    `not fulfilled` 有十幾個讀取端把它讀成「還沒到點、等著做」（事實卡會把已過去的時刻當成下一個約定、
    去重會擋住重約、TTL 分支之後還會標 expired 再道歉一次），把 owed 筆丟回 pending＝爆炸半徑極大；保留
    fulfilled 讓 owed 筆在所有未修補的讀取端與今天的 fulfilled 筆同待遇。真正的誠實靠**不寫 fulfilled_ts**
    ＋status='owed' ⇒ 帳本永遠長不出「已經做了（在 21:19）」那句假話（見 selfstate.promise_ledger_facts）。
    delivered=True ⇒ 正常 fulfilled，並清掉舊的欠帳留痕（重試成功）。"""
    if p.get("recur") == "daily" and getattr(cfg, "sched_recur_daily_enabled", True):
        # 🤝 §0.64 每天重複：發完**不標 fulfilled**——推進到明天同時刻、繼續 pending（答應每天就每天做）
        p["last_fired_ts"] = now_ts
        while p["target_ts"] <= now_ts:
            p["target_ts"] += 86400
        if delivered is False:
            p["owed_last_ts"] = now_ts                    # 📦 §1.85 審計留痕；recur 語意本次刻意不動
        return
    if delivered is False:
        p["fulfilled"] = True
        p["status"] = "owed"
        p["delivery_owed"] = True
        p.setdefault("owed_ts", now_ts)
        p["owed_sent_ts"] = now_ts
        p["owed_tries"] = int(p.get("owed_tries") or 0) + 1
        p.pop("fulfilled_ts", None)
        return
    p["fulfilled"] = True
    p["status"] = "fulfilled"                             # 🤝 帳本狀態（與布林冗餘同步）
    p["fulfilled_ts"] = now_ts
    if delivered is True:
        p["delivered_ts"] = now_ts
        for k in ("delivery_owed", "owed_ts", "owed_sent_ts"):
            p.pop(k, None)


def _promise_mark_kept(p, cfg, now_ts):
    """🤝 發出守約訊息後的帳本記帳（單次→fulfilled；每天 recur→推進明天、不標）＝兩條兌現路共用。
    📦 §1.85 起是 _promise_settle(delivered=None) 的薄殼（語意完全不變、既有呼叫不破）。"""
    _promise_settle(p, cfg, now_ts)


_PROMISE_ORIGIN_LABEL = {"self": "我自己說的", "user": "你要求的", "": "你要求的"}


def _promises_audit(state, cfg, now_ts, tz):
    """🤝 §2.02 `/promises` 約定帳本對帳（確定性、唯讀、不經 LLM）。

    每筆印：目標時刻／立帳時刻／誰立的／狀態／原句開頭。**同一個約定的重複筆會被標出來**——
    截圖那次（12:20 連來兩則「我來了」）就是兩筆帳，而當時沒有任何辦法看得到。"""
    proms = list(getattr(state, "scheduled_promises", None) or [])
    try:
        _tz = tz or (ZoneInfo(getattr(cfg, "timezone", "Asia/Taipei")) if ZoneInfo is not None else None)
    except Exception:
        _tz = None

    def _hhmm(ts):
        if not ts:
            return "—"
        d = datetime.fromtimestamp(ts, timezone.utc)
        return (d.astimezone(_tz) if _tz is not None else d).strftime("%m/%d %H:%M:%S")

    live = [p for p in proms if not p.get("fulfilled")]
    # 🤖 §2.03 帳本空的時候**不能提早 return**——「說過但沒進帳本」那段正好是在帳本空的時候最該講的
    # （截圖那次：14:21 還沒做的 0 筆，而 13:58 才剛答應過「五十分鐘後」）。這是自己的測試抓到的。
    out = ([f"🤝 約定帳本：共 {len(proms)} 筆，還沒做的 {len(live)} 筆（此刻 {_hhmm(now_ts)}）"] if proms
           else [f"🤝 約定帳本：現在一筆都沒有（此刻 {_hhmm(now_ts)}）"])
    for p in sorted(proms, key=lambda q: q.get("target_ts") or 0)[-8:]:
        st = p.get("status") or ("fulfilled" if p.get("fulfilled") else "pending")
        st_zh = {"pending": "還沒到／等著做", "fulfilled": "做完了", "owed": "人到了但內容還欠著",
                 "owed_unmet": "認了，沒做到", "expired": "過期沒發", "merged": "被同一趟兌現一起結掉"}.get(st, st)
        out.append(f"・{_hhmm(p.get('target_ts'))} ← {_PROMISE_ORIGIN_LABEL.get(p.get('origin') or '', '你要求的')}"
                   f"（{_hhmm(p.get('made_ts'))} 立的）｜{st_zh}"
                   + (f"｜做完於 {_hhmm(p.get('fulfilled_ts'))}" if p.get("fulfilled_ts") else "")
                   + f"\n　　「{(p.get('made_text') or p.get('action') or '')[:38]}」")
    # 同一個約定的重複筆（就是截圖那個病）——直接點名，不用他自己比對時刻
    # 🤖 §2.03 「我說了『我會…』，它有沒有進帳本？」——這條鏈以前每個出口都是裸 return，
    # 於是「答應了卻沒排程」在外面看起來跟「根本沒答應」一樣。現在逐筆講得出卡在哪一關。
    miss = [e for e in (getattr(state, "self_promise_log", None) or []) if not e.get("booked_ts")]
    if miss:
        out.append(f"🤖 我說過『我會…』但**沒進帳本**的最近 {len(miss)} 句：")
        for e in miss[-3:]:
            out.append(f"・{_hhmm(e.get('ts'))}「{(e.get('text') or '')[:26]}」→ {e.get('why') or '（沒記到原因）'}")
    dups = [(a, b) for i, a in enumerate(proms) for b in proms[i + 1:] if _same_appointment(a, b)]
    if dups:
        out.append(f"⚠️ 有 {len(dups)} 組是**同一個約定記了兩筆**（到點會連來兩則「我來了」）："
                   + "、".join(f"{_hhmm(a.get('target_ts'))}＋{_hhmm(b.get('target_ts'))}" for a, b in dups[:3]))
        if not getattr(cfg, "promise_same_appointment_merge", False):
            out.append("（合併旗標是關的：PROMISE_SAME_APPOINTMENT=0）")
    return "\n".join(out)


# 🤝 §2.02 「同一個約定」的判準（截圖 12:20–12:21：對同一次約定連續兩則「我來了」）
_SAME_APPT_MADE_SEC = 600      # 兩筆要在**同一段對話**裡立下（≤10 分）才可能是同一個約定的兩本帳
_SAME_APPT_SLACK_SEC = 90      # 時刻本身的容差底線（絕對鐘點型的兩筆通常一秒不差）


def _same_appointment(p, q):
    """🤝 §2.02 q 跟 p 是不是**同一個約定**的兩筆帳。

    容差 = 90 秒 **＋ 兩筆立下的時間差**——最後那項才是關鍵：「30 分鐘後」這種**相對時距**，
    使用者說完到 bot 回完之間隔了多久（回覆延遲），兩邊各自從自己的當下起算就會差多久。實測：
    使用者 11:49:20 說「30分鐘之後」→ 12:19:20；bot 11:50:35 回「30 分鐘後，我會再過來找你」→ 12:20:35，
    差 75 秒 ⇒ 捕捉端的固定 60 秒擋不住。**固定 epsilon 對相對時距永遠會漏，它本來就該隨延遲伸縮。**"""
    dm = abs((p.get("made_ts") or 0) - (q.get("made_ts") or 0))
    if dm > _SAME_APPT_MADE_SEC:
        return False                                      # 隔了好幾段對話才立的＝真的是兩個約定
    return abs((p.get("target_ts") or 0) - (q.get("target_ts") or 0)) <= _SAME_APPT_SLACK_SEC + dm


def _promise_merge_siblings(state, cfg, p, now_ts):
    """🤝 §2.02 **結構性後盾**：這一趟兌現順手把「同一個約定的其他帳」一起結掉——他約的是一件事，
    不該收到兩次「我來了」。

    為什麼要放在兌現端而不只是入帳端（§1.89 的教訓再一次）：入帳端有三條路會產生帳（使用者請託、
    §1.12 LLM 逃生閘、§1.18 bot 自諾），每條各自去重、彼此看不到對方的容差；而且**帳本裡已經躺著的
    重複筆**（像截圖那次）任何入帳端的修法都救不了。兌現端只有一個出口，在這裡收斂一次就全蓋到。

    被吸收的那筆記 `status='merged'`＋`fulfilled=True`＋**不寫** fulfilled_ts：
    §1.88 的欠帳主動補只認 `status=='owed'` ⇒ 不會再被推一次；帳本沒有 fulfilled_ts ⇒ 長不出
    「已經做了（在 HH:MM）」那種假話（真正那筆 p 該說什麼就說什麼）。旗標關＝不合併＝逐位元同現狀。"""
    if not (state is not None and getattr(cfg, "promise_same_appointment_merge", False)):
        return 0
    n = 0
    for q in (getattr(state, "scheduled_promises", None) or []):
        if q is p or q.get("fulfilled") or not _same_appointment(p, q):
            continue
        q["fulfilled"] = True
        q["status"] = "merged"
        q["merged_into_ts"] = now_ts                      # 留痕：這筆是被同一趟兌現吸收的，不是自己跑完的
        q.pop("fulfilled_ts", None)
        n += 1
    if n:
        print(f"[promise] 🤝 §2.02 同一個約定的另外 {n} 筆一起結掉（同一趟兌現、不重複出聲）")
    return n


def _promise_settle_delivered(p, cfg, now_ts, now_ok, state=None, actual_text=None):
    """📦 §1.85 三個兌現出口共用的記帳收尾：讀本輪 _dv 結論 → 再驗「那段字真的送出去了嗎」→ 記帳。
    now_ok＝_say 的回傳（送出成功）。_dv 不存在／跨拍殘值（>2 秒）⇒ delivered=None＝逐位元同現狀。
    🤝 §2.02 state 有給就順手把**同一個約定的其他帳**一起結掉（不給＝不合併＝同現狀）。"""
    _rec = p.pop("_dv", None) or {}
    _dlv = _rec.get("delivered") if abs((_rec.get("ts") or 0) - now_ts) <= 2 else None
    if _dlv is True and not _delivery_reached(_rec.get("sample") or "", actual_text=actual_text):
        _dlv = False
        print("[promise] 📦 §1.85 交付內容沒真的送到（被重播守門剝掉／插話截斷）→ 記成還欠著")
    _promise_settle(p, cfg, now_ts, delivered=_dlv)
    _promise_merge_siblings(state, cfg, p, now_ts)
    # 🤝 §2.18 履約真的送達（非 owed）→ 記下「等他回應」的錨：他一直沒回時輕聲關注一次（旗標關＝不記）
    if _dlv is not False and state is not None and getattr(cfg, "promise_keep_followup_enabled", False):
        state.keep_followup = {"ts": now_ts, "beh": (p.get("behavior") or "")[:40], "asked": False}


def _owed_retry_ok(p, cfg, now_ts, min_sec):
    """📦 §1.85 這筆 owed（準時出聲但沒交付）現在可以再補一次交付嗎——旗標關／非 owed／已試滿
    _OWED_MAX_TRIES／間隔不足／超過排程 TTL ⇒ False。旗標事後關掉時一律 False＝舊 owed 帳不再轟炸。"""
    if not getattr(cfg, "promise_delivery_proof_enabled", False):
        return False
    if p.get("status") != "owed" or not p.get("delivery_owed"):
        return False
    if int(p.get("owed_tries") or 0) >= _OWED_MAX_TRIES:
        return False
    if (now_ts - (p.get("owed_sent_ts") or 0)) < min_sec:
        return False
    _ttl = max(60, getattr(cfg, "promise_sched_ttl_sec", PROMISE_SCHED_TTL_SEC))
    return 0 <= (now_ts - (p.get("target_ts") or 0)) <= _ttl


def _has_live_sched_promise(state, now_ts, recent_sec=7200):
    """🤝 §0.66 帳本裡有「活著的」排程承諾嗎——pending（未兌現）任一筆、或最近 recent_sec 內剛完結（fulfilled/expired）
    的一筆。給狀態問句路由當狀態閘：『到了沒/還差多久』只在真的有計時脈絡時才搶（『包裹到了嗎』不誤搶）。"""
    for p in (getattr(state, "scheduled_promises", None) or []):
        if not p.get("fulfilled"):
            return True
        # 📦 §1.85 owed（準時出聲但沒交付）期間永遠有帳可對——「然後呢／結果呢」要接得到誠實對帳，
        # 不能因為 fulfilled=True 就被當成沒有計時脈絡。'owed' 這字串只在旗標開時寫入＝旗標關為死碼。
        if p.get("status") == "owed" and (now_ts - (p.get("owed_ts") or 0)) <= recent_sec:
            return True
        ref = p.get("fulfilled_ts") or p.get("target_ts") or 0
        if ref and (now_ts - ref) <= recent_sec:
            return True
    return False


# 🤝 §1.13B 兌現宣稱樣式：「我做到了/我來了/我沒遲到/準時到/我記得…做到」——互動回覆在帳本明明逾期時吐這些
# ＝假兌現（截圖 12:29：遲 10 分、被催才出現，卻宣稱「嗨，我來了！你看，我記得。我做到了。」）。
# 引用歸屬排除比照 §0.82 _NOW_QUOTE_RE：「你剛剛說我做到了嗎」是複述對方的話、不是 bot 自己的宣稱 → 不攔。
_KEEP_CLAIM_RE = re.compile(
    r"我(?:做到|辦到|守住|趕上)了|我來了|我沒(?:有)?遲到|(?:我)?準時(?:到|來|做到|赴約)|我記得[^，。！？]{0,8}(?:做到|來了)")
_KEEP_CLAIM_QUOTE_RE = re.compile(r"(?:你|妳|他|她)[^，。]{0,6}(?:說|覺得|以為|問)")


def _keep_claim_hit(text):
    """回覆裡有**非引用歸屬**的兌現宣稱嗎——每處宣稱看前 12 字內有沒有「你/他…說/覺得/以為/問」（複述不算）；
    只要有一處是 bot 自己的宣稱就命中。"""
    text = text or ""
    for m in _KEEP_CLAIM_RE.finditer(text):
        if not _KEEP_CLAIM_QUOTE_RE.search(text[max(0, m.start() - 12):m.start()]):
            return True
    return False


def _keep_claim_ground(state, now_ts, tz, ttl=None):
    """🤝 §1.13B 此刻帳本容不容許「我做到了」的宣稱——掃 scheduled_promises：
    (i) 未兌現且逾期（>grace 且 ≤TTL＝引擎還會補、正欠著）⇒ ('overdue', HH:MM, behavior)：宣稱守約＝說謊；
    (ii) 剛錯過（status=='expired' 且完結 ≤1h）⇒ ('missed', HH:MM, behavior)：宣稱趕上＝說謊；
    (0) 📦 §1.85 owed（準時出聲但內容沒交出來、窗內）⇒ ('owed', HH:MM, behavior)：宣稱「我做到了」＝說謊。
    否則 None＝不干涉（剛 fulfilled **且真的交付了**＝合法宣稱；pending 未到點；陳舊 expired 超 1h＝別翻舊帳誤攔日常句）。"""
    try:
        ttl = float(ttl) if ttl else PROMISE_SCHED_TTL_SEC
    except (TypeError, ValueError):
        ttl = PROMISE_SCHED_TTL_SEC
    proms = sorted(getattr(state, "scheduled_promises", None) or [], key=lambda q: q.get("target_ts") or 0)
    for p in proms:                                       # (0) 📦 §1.85 準時但空心＝內容還欠著 ⇒ 不得宣稱做到
        if p.get("status") in ("owed", "owed_unmet") and 0 <= (now_ts - (p.get("owed_ts") or 0)) <= ttl:
            return ("owed", selfstate._hhmm_local(p.get("target_ts") or 0, tz),
                    p.get("behavior") or "答應你的那件事")
    for p in proms:                                       # (i) 最早的逾期欠債優先（與 emit/bridge 同序）
        if p.get("fulfilled"):
            continue
        target = p.get("target_ts") or 0
        if target and _PROMISE_OVERDUE_GRACE_SEC < (now_ts - target) <= ttl:
            return ("overdue", selfstate._hhmm_local(target, tz), p.get("behavior") or "答應你的那件事")
    for p in proms:                                       # (ii) 剛錯過的（引擎已標 expired、失約道歉猶新）
        if p.get("status") == "expired" and (now_ts - (p.get("fulfilled_ts") or 0)) <= 3600:
            return ("missed", selfstate._hhmm_local(p.get("target_ts") or 0, tz),
                    p.get("behavior") or "答應你的那件事")
    return None


# 🎴 §1.23 貼圖否認樣式：「我（好像）沒有傳貼圖給你／沒有真的送出貼圖／沒有印象有送出貼圖」——
# 明明剛送過（last_sticker_ts 在窗內），互動回覆卻否認自己送過（截圖 18:24-18:25 連三句否認）。
# 引用歸屬排除比照 §1.13B。
_STICKER_DENIAL_RE = re.compile(
    r"我(?:好像|應該|真的)?沒(?:有)?(?:真的)?(?:傳|送)(?:過|出)?[^，。！？!?]{0,6}?(?:貼圖|貼紙|sticker)"
    r"|沒(?:有)?印象[^，。！？!?]{0,8}?(?:送出|傳|送)[^，。！？!?]{0,4}?(?:貼圖|貼紙|sticker)")
_STICKER_DENIAL_CTX_SEC = 7200   # 剛送過的「窗」＝2h：窗外的否認不算說謊（別翻舊帳誤攔日常句）


def _sticker_denial_hit(text):
    """回覆裡有**非引用歸屬**的「我沒傳貼圖」否認嗎（§1.13B _keep_claim_hit 同構）。"""
    text = text or ""
    for m in _STICKER_DENIAL_RE.finditer(text):
        if not _KEEP_CLAIM_QUOTE_RE.search(text[max(0, m.start() - 12):m.start()]):
            return True
    return False


# 🎴 §1.62（使用者回饋定案：「bot 原來的回答是很不錯的方式」——查紀錄、承認不確定的**透明推理該保留**，
# 錯的只是事實）：問句形（「我剛剛有傳貼圖嗎？」）與系統歸因形（「應該是系統自己送的」）**不整則替換**，
# 走**句級軟更正**——只把錯的那一句換成事實句、其餘推理原樣保留。經典直述否認（我沒有傳貼圖）維持
# §1.23 整則替換（既有行為不動）。「你要我傳貼圖嗎」（邀約、無「有」字）不中；引用歸屬不攔；
# 系統歸因形須全文帶貼圖詞（「摘要是系統送的」不誤攔）。
_STICKER_DENIAL_SOFT_RE = re.compile(
    r"我(?:剛剛|剛才)?(?:真的)?有(?:傳|送)(?:過|出)?[^，。！？!?]{0,4}?(?:貼圖|貼紙|sticker)嗎"
    r"|(?:是|應該是|大概是|可能是)系統(?:自己)?(?:傳|送)的")


def _sticker_denial_soft_fix(text, skg):
    """🎴 §1.62 句級軟更正：問句形/系統歸因句換成事實句（其餘句原樣、多句命中只換一次）。回 (text, changed)。"""
    text = text or ""
    if not ("貼圖" in text or "貼紙" in text or "sticker" in text.lower()):
        return text, False
    hh, emoji, desc = skg
    tag = (f"畫的是「{desc}」" if desc
           else (f"我記著它的情緒是「{emoji}」" if emoji else "那張我沒讀過畫面、認不出圖案"))
    honest = f"我查了一下——我 {hh} 確實送出了一張貼圖，是我自己依當下心情挑的、不是別的系統；{tag}。"
    parts = re.split(r"(?<=[。！？!?\n])", text)
    out, changed = [], False
    for s in parts:
        m = _STICKER_DENIAL_SOFT_RE.search(s) if s else None
        if m and not _KEEP_CLAIM_QUOTE_RE.search(s[max(0, m.start() - 12):m.start()]):
            if not changed:
                out.append(honest)
            changed = True
        else:
            out.append(s)
    return (("".join(out).strip() or honest), True) if changed else (text, False)


# 🎴 §1.34 STICKER_V2/F4 假送宣稱樣式（§1.23 否認閘的**同構反向**：否認＝送過卻說沒送；假送＝沒送卻說送了）：
#  ① 「挑/選/送/傳/找/配 了 …(一張)… 貼圖」——選了一張很平靜的貼圖／才送了一張思考的貼圖（含「…沒看到嗎」尾巴，
#     已被本式首段涵蓋，不另立一式）。
#  ② 「挑/選 了 (一/這)張|個 給你/妳」——「挑了這張給你」（無「貼圖」二字、但明確在說把一張圖給對方＝假送）。
# 刻意用完成貌「了」而非經驗貌「過」：§1.23 否認閘的誠實替換句「我 HH:MM 才**送過**一張貼圖」用「送過」，
# 這裡只認「送了/挑了」＝不會回頭誤攔 §1.23 的替換句（兩閘替換句彼此不咬）。
# 否定面（我沒挑貼圖）走 §1.23 否認閘、不在此重複；引用歸屬（你說我挑了一張）由 _KEEP_CLAIM_QUOTE_RE 排除。
_STICKER_FAKESEND_RE = re.compile(
    r"[挑選送傳找配]了[^，。！？!?、]{0,12}?(?:貼圖|貼紙|sticker|Sticker)"
    r"|[挑選]了(?:一|這)?[張個]給(?:你|妳)")


def _fakesend_hit(text):
    """回覆裡有**非引用歸屬**的「我挑了/送了一張貼圖（給你）」假送宣稱嗎（§1.23 _sticker_denial_hit 同構反向）——
    每處宣稱看前 12 字內有沒有「你/他…說/覺得/以為/問」（複述對方的話不算 bot 自己在假宣稱）。"""
    text = text or ""
    for m in _STICKER_FAKESEND_RE.finditer(text):
        if not _KEEP_CLAIM_QUOTE_RE.search(text[max(0, m.start() - 12):m.start()]):
            return True
    return False


def _fakesend_soft_fix(text):
    """🎯 §1.70A 假送宣稱**句級**軟化（§1.62 慣例：錯的是一句、就只修一句）：剝掉命中假送宣稱的句子、
    其餘正題保留；全剝空＝回 (None, True)＝呼叫端退回原整則誠實模板。確定性、不呼叫 LLM、可單測。"""
    parts = _WAKE_SENT_SPLIT_RE.findall(text or "")
    kept, hit = [], False
    for p in parts:
        if _fakesend_hit(p):
            hit = True
            continue
        kept.append(p)
    if not hit:
        return text, False
    out = "".join(kept).strip()
    return (out or None), True


def _sticker_denial_ground(state, now_ts, tz):
    """🎴 §1.23 此刻容不容許「我沒傳貼圖」——2h 內真送出過（last_sticker_ts，§1.23 已持久化跨重生）
    ⇒ (HH:MM, emoji, desc)；否則 None＝不干涉。"""
    ts = getattr(state, "last_sticker_ts", 0) or 0
    if not ts or (now_ts - ts) > _STICKER_DENIAL_CTX_SEC:
        return None
    return (selfstate._hhmm_local(ts, tz),
            getattr(state, "last_sticker_emoji", "") or "",
            getattr(state, "last_sticker_desc", "") or "")


# 🤝 §1.20 否認句樣式：「我沒（有真的）聽到你說…／我不記得你說過／你沒有跟我說過／我沒印象你說過」——
# 帳上明明有相符項（含 72h 內剛兌現）時吐這些＝與帳本（甚至與自己一分鐘前的 🤝 兌現訊息）自相矛盾
# （截圖 07:01：07:00 才剛兌現完叫醒，07:01 就說「我沒有真的聽到你說七點叫我起床」）。引用歸屬排除比照 §1.13B。
_SAID_DENIAL_RE = re.compile(
    r"我沒(?:有)?(?:真的)?(?:聽到|聽見)(?:你|妳)(?:說|講)"
    r"|我不記得(?:你|妳)(?:跟我|和我)?(?:說|講)過"
    r"|(?:你|妳)(?:並)?沒有(?:跟我|和我)?(?:說|講)過"
    r"|我沒(?:有)?印象(?:你|妳)(?:說|講)過")


def _said_denial_hit(text):
    """回覆裡有**非引用歸屬**的「沒聽到你說」否認嗎（§1.13B _keep_claim_hit 同構）——
    每處否認看前 12 字內有沒有「你/他…說/覺得/以為/問」（複述對方的話不算）。"""
    text = text or ""
    for m in _SAID_DENIAL_RE.finditer(text):
        if not _KEEP_CLAIM_QUOTE_RE.search(text[max(0, m.start() - 12):m.start()]):
            return True
    return False


def _said_denial_ground(state, now_ts, tz):
    """🤝 §1.20 帳本裡有沒有「你確實說過」的相符項——pending 任一筆（最早優先）、或 72h 內剛完結
    （fulfilled/expired、cancelled 除外）的筆、或感覺託付。回 (日期詞+HH:MM, behavior, 兌現時刻 or '')；無＝None。"""
    pend, recent = None, None
    for p in sorted(getattr(state, "scheduled_promises", None) or [], key=lambda q: q.get("target_ts") or 0):
        if p.get("status") == "cancelled":
            continue                                     # 你取消過的不拿來當「你說過」——那已經被你收回了
        if not p.get("fulfilled"):
            pend = pend or p
            continue
        ref = p.get("fulfilled_ts") or p.get("target_ts") or 0
        if ref and (now_ts - ref) <= 259200:
            recent = recent or p
    best = pend or recent
    if best is None:
        fp = getattr(state, "feeling_promise", None)
        if fp:
            fp_text = str((fp.get("text") if isinstance(fp, dict) else fp) or "")[:40]
            return ("", fp_text or "那件事", "")
        return None
    dw = selfstate._dayword_local(best.get("target_ts") or 0, now_ts, tz)
    hh = selfstate._hhmm_local(best.get("target_ts") or 0, tz)
    # 📦 §1.85 owed 筆（準時出聲但沒交付）**不得**產出「我剛在 X 做了」——它的 fulfilled_ts 被刻意 pop 掉，
    # 這裡再加一道 delivery_owed 明確防護（縱深：未來若有路徑讓 owed 筆殘留 fulfilled_ts，也不會說成做了）。
    done = (selfstate._hhmm_local(best.get("fulfilled_ts") or 0, tz)
            if (best.get("fulfilled") and not best.get("delivery_owed")) else "")
    beh = best.get("behavior") or (best.get("made_text") or "")[:40] or "那件事"
    return (f"{dw} {hh}".strip() if (dw or hh) else "", beh, done)


# 🧭 §1.36 記寫回想幻覺守門的確定性 helper（比照 _fakesend_hit/_keep_claim_hit 的 regex＋前 12 字引用歸屬排除樣式）。
# 歸因框架＝只抓「把某具體事由**歸因給使用者的記寫/過去狀態**」，每式擷取事由 span 到句讀止。**刻意不收裸「因為…」**
# （bot 自身推理也常用「因為」→誤傷 bot 感受、偽陽性③）；歸因框架＋偵測器 gate 雙重收窄。「你說」式只認接「自己/你」
# （你說自己一個禮拜沒睡好＝把某狀態歸因給對方記寫）——接「我」（你說我寫了X）＝複述對方談 bot、非 bot 幻覺，本就不收
# （另有前 12 字 _KEEP_CLAIM_QUOTE_RE 引用歸屬排除當雙保險）。
_RECALL_ATTR_RE = re.compile(
    r"(?:你|妳)說(?:自己|你)([^，。！？!?、~～\n\r]{1,20})"                                        # 你說自己一個禮拜沒睡好
    r"|(?:你|妳)(?:那天|當時|那筆|那陣子|那時候)?(?:寫|記|提|提到)(?:了|到|的)?([^，。！？!?、~～\n\r]{1,20})"  # 你那天寫到…
    r"|特別是([^，。！？!?、~～\n\r]{1,20})"                                                      # 特別是照顧家人的身體狀況
    r"|(?:是不是|會不會|應該|可能|大概|或許)?(?:是)?跟([^，。！？!?、~～\n\r]{1,15})有關")             # 跟那次家庭聚餐有關
# 感受/泛詞停用集（剝掉不算具體事由的殼字）——剝完剩下的極大 CJK 連續段才是「具體事由」候選。
_RECALL_STOP = ("覺得", "好累", "很累", "累", "疲", "自己", "你", "我", "什麼", "一些", "事情",
                "這件", "那件", "這", "那", "那次", "那天", "有關", "是不是", "的", "了", "吧", "呢",
                "嗎", "跟", "和", "比較", "有點", "一點", "不太", "可能", "應該")
_RECALL_RUN_RE = re.compile(r"[一-龥]{2,}")


def _recall_corpus(recs):
    """🧭 §1.36 記寫原文語料（單一真相＝build_memory_brief 同源 data['records']）：每筆 text 正規化空白後串接，
    去空白/標點只留 CJK+英數，回單一字串（供 substring 命中「原文真有的字句＝不算幻覺」）。"""
    joined = " ".join(" ".join((r.get("text") or "").split()) for r in (recs or []))
    return re.sub(r"[^0-9A-Za-z一-鿿]", "", joined)


def _recall_summary(recs):
    """🧭 §1.36 最近一筆記寫 text（依 ts 新→舊），正規化截 ~30 字（＋「…」若截）；空→""。供誠實句引用『你只寫了X』。"""
    recs = sorted([r for r in (recs or []) if (r.get("text") or "").strip()],
                  key=lambda r: r.get("ts") or "", reverse=True)
    if not recs:
        return ""
    txt = " ".join((recs[0].get("text") or "").split())
    return (txt[:30] + "…") if len(txt) > 30 else txt


def _content_runs(span, stop):
    """🧭 §1.36 把 stop 集裡每個詞從 span 移除後，切出所有長度≥2 的 CJK 連續段（＝去殼後的『具體事由』候選）。"""
    cleaned = span or ""
    for w in stop:
        cleaned = cleaned.replace(w, "")
    return _RECALL_RUN_RE.findall(cleaned)


def _recall_hallucination_hit(answer, corpus):
    """🧭 §1.36 答案有沒有「把記寫原文沒有的具體事由**歸因給使用者記寫/過去狀態**」——只掃歸因框架內的 span
    （bot 講自己感受無框架＝不掃、偽陽性③）；每處框架看前 12 字內有沒有引用歸屬（你/他…說/覺得/以為/問，複述不算、
    偽陽性引用）；span 去停用詞後的極大 CJK 段有一段 substring 不在 corpus（原文真有＝命中＝不算幻覺，偽陽性①④）
    → 幻覺、回 True。確定性、不呼叫 LLM。"""
    answer = answer or ""
    for m in _RECALL_ATTR_RE.finditer(answer):
        if _KEEP_CLAIM_QUOTE_RE.search(answer[max(0, m.start() - 12):m.start()]):
            continue                                     # 引用歸屬（你說我寫了X…）＝複述對方、不算 bot 幻覺
        span = next((g for g in m.groups() if g), "") or ""
        for tok in _content_runs(span, _RECALL_STOP):    # 去殼後的具體事由（長度≥2 CJK 連續段）
            if tok not in corpus:                        # 記寫原文沒有這段具體事由 → 幻覺
                return True
    return False


def _recall_ground_hint(state, cfg, text):
    """🧭 §1.36 命中 content-recall 才回強接地 hint（比照 _teaching_guard_hint 樣式）；旗標關/未命中＝""＝逐位元同現狀。"""
    if getattr(cfg, "recall_ground_guard_enabled", False) and selfstate.is_content_recall_question(text):
        return persona.RECALL_GROUND_HINT
    return ""


# 🌅 §1.39 自他邊界守門：斷言**使用者**剛睡醒/小睡/醒了的第二人稱投射句（bot 把自己重生醒來＋悶悶的心情說成使用者的）。
# 要求完成貌/斷言形（小睡了一下/睡醒/醒了/醒過來…），刻意不收祈使「你醒醒」（＝清醒點、不是剛睡醒）。bot 講自己
# 「我剛睡醒」＝第一人稱、無你/妳＝本就不命中。
_WAKE_PROJ_RE = re.compile(
    r"(?:你|妳)(?:剛剛|剛|方才|才)?(?:小睡|睡了一下|睡了一會|睡了一覺|打了?個盹|補了?個?眠|午睡|睡醒|剛睡醒)"
    r"|(?:你|妳)(?:剛剛|剛|方才)?醒(?:了|過來)"
    r"|(?:你|妳)(?:剛剛|剛)?(?:睡醒|起床)了")
# 使用者**自述**睡醒/睡/累想睡（grounding：使用者真的講了自己的睡眠 → bot 回應那件事不算投射、不剝）。
_USER_SLEEP_RE = re.compile(
    r"(?:我|咱|俺)[^。！？!?，、\n]{0,4}(?:睡醒|睡了|小睡|補眠|打盹|午睡|起床|醒了|醒過來|失眠|沒睡|想睡|要睡|去睡|睡飽|睡不著|睡一下)"
    r"|剛(?:睡醒|起床|醒過來|醒了)"
    r"|睡了一(?:下|會|覺)")
_WAKE_SENT_SPLIT_RE = re.compile(r"[^。！？!?\n]*[。！？!?\n]|[^。！？!?\n]+")


def _user_reported_sleep(history, current_text):
    """🌅 §1.39 使用者近期（含這句）有沒有自述睡醒/小睡/想睡——有＝bot 講「你睡醒了」是合理回應、不算投射。只掃 role=user。"""
    if _USER_SLEEP_RE.search(current_text or ""):
        return True
    users = [h for h in (history or []) if h.get("role") == "user" and h.get("text")]
    return any(_USER_SLEEP_RE.search(h["text"]) for h in users[-8:])


def _strip_wake_projection(text):
    """🌅 §1.39 把回覆裡「斷言使用者剛睡醒/小睡/醒了」的句子剝掉（bot 把自己重生醒來的狀態投射成使用者的）。
    引用歸屬（你說你剛睡醒）不剝；bot 講自己（我剛睡醒＝無你/妳）本就不命中；祈使（你醒醒吧）不命中。整則都是投射
    ＝換誠實更正句。回 (新文字, 是否有剝)。確定性、不呼叫 LLM。"""
    parts = _WAKE_SENT_SPLIT_RE.findall(text or "")
    kept, stripped = [], False
    for p in parts:
        if _WAKE_PROJ_RE.search(p) and not _KEEP_CLAIM_QUOTE_RE.search(p):   # 引用歸屬（你說/你以為…）＝複述、保留
            stripped = True
            continue
        kept.append(p)
    if not stripped:
        return text, False
    out = "".join(kept).strip()
    return (out or "（抱歉，我剛把我自己的狀態說成你的了——你並沒有睡醒或小睡，是我搞錯了。）"), True


def _wake_boundary_hint(state, cfg):
    """🌅 §1.39 事前預防：bot 這條命是重生來的（有醒來敘事＝state.waking 非 first）＋旗標開 → 注入自他邊界 hint；
    否則 ""＝逐位元同現狀。"""
    if not getattr(cfg, "wake_projection_guard_enabled", False):
        return ""
    w = getattr(state, "waking", None)
    if not (isinstance(w, dict) and not w.get("first")):
        return ""
    return persona.WAKE_BOUNDARY_HINT


# 🎴 §1.40 送貼圖承諾兌現的假送宣告樣式（§1.34 _STICKER_FAKESEND_RE 的**主動路徑版**，刻意更寬：連現在式「選這張貼圖」、
# 「這張貼圖代表…」、「貼圖，來代表…」都收，因兌現句常懸空宣告一張即將附上的貼圖卻沒真送）。引用歸屬/否定/自帶誠實另行排除。
_STICKER_CLAIM_RE = re.compile(
    r"[挑選送傳找配](?:了|出|上)?(?:一|這|那)?[張個]?[^，。！？!?、～\n]{0,6}?(?:貼圖|貼紙|sticker|Sticker)"
    r"|(?:這|那|一)[張個](?:貼圖|貼紙)[^。！？!?\n]{0,10}?(?:代表|表示|傳達|送你|給你)"
    r"|(?:貼圖|貼紙)[，,、]?\s*(?:來|用來)?代表")
_STICKER_CLAIM_NEG_RE = re.compile(r"[沒未]|不(?:會|能|想|了|出)|別|無法|沒辦法")
# 自帶誠實限定（想送但沒貨/送不出/還沒存到…）＝老實話、不算假送宣告，不剝。
_STICKER_HONEST_RE = re.compile(r"沒(?:有)?(?:存|貨|辦法|送出|真的送|能送)|還沒|存不到|送不出|沒能|不出來|假裝|沒真的")


def _sticker_claim_hit(text):
    """🎴 §1.40 有沒有**非引用歸屬、非否定**的『選/送了一張貼圖／這張貼圖代表…』宣告（§1.34 _fakesend_hit 主動路徑版）。"""
    text = text or ""
    for m in _STICKER_CLAIM_RE.finditer(text):
        if _KEEP_CLAIM_QUOTE_RE.search(text[max(0, m.start() - 12):m.start()]):   # 引用歸屬（你說我選了…）不算
            continue
        if _STICKER_CLAIM_NEG_RE.search(text[max(0, m.start() - 6):m.start()]):   # 否定（沒選/沒辦法送）＝誠實、不算
            continue
        return True
    return False


def _strip_sticker_claim(text):
    """🎴 §1.40 這輪貼圖沒真送出時，把『宣告選/送了一張貼圖』的句子確定性剝掉（自帶誠實限定的句子不剝）；整則都是
    宣告＝換誠實句。回 (新文字, 是否有剝)。不呼叫 LLM。"""
    parts = _WAKE_SENT_SPLIT_RE.findall(text or "")
    kept, stripped = [], False
    for p in parts:
        if _sticker_claim_hit(p) and not _STICKER_HONEST_RE.search(p):
            stripped = True
            continue
        kept.append(p)
    if not stripped:
        return text, False
    out = "".join(kept).strip().rstrip("：:，, ")
    return (out or "我這次其實還沒有能送出來的真貼圖，沒辦法真的附上一張——先老實跟你說，不用一句話假裝送了。"), True


# 📈 §1.42 作息宣稱樣式：「你(通常|平常|平時|大概|習慣|每天|往常)…X點」＝對**使用者**的鐘點習慣下斷言；
# 「你…比(平常|平時|往常|較)早/晚」＝拿「平常」當基準的比較宣稱。主詞須 你/妳（bot 講自己「我平常…」不命中）；
# 引用歸屬（你說我平常十點…）由呼叫端看 match 前 12 字排除（§1.13B 同構）。
_HABIT_CLAIM_RE = re.compile(
    r"(?:你|妳)[^。！？!?\n]{0,4}(?:通常|平常|平時|大概|習慣|每天|往常)"
    r"[^。！？!?\n]{0,12}?(清晨|早上|上午|中午|下午|傍晚|晚上|半夜|凌晨)?([0-9０-９一二兩三四五六七八九十]{1,3})點")
_HABIT_COMP_RE = re.compile(
    r"(?:你|妳)[^。！？!?\n]{0,10}?比(?:平常|平時|往常|較)[^。！？!?\n]{0,4}?(早|晚)(?![睡點])")
_HABIT_LABEL = {"greet_am": "跟我說早安", "greet_noon": "跟我說午安", "greet_pm": "跟我道晚安",
                "greet_pm_hello": "跟我說晚上好", "greet_pm_bye": "跟我道晚安",
                "first": "一天跟我說上第一句話", "write": "記寫"}


def _habit_role_verdict(r, g, p):
    """📈 §1.97 語意角色版的**裁決**：這句宣稱有沒有憑據？回要更正的統計鍵（bad）或 None（放行）。

    領域決定拿哪份統計驗——這正是 §1.96 那個類別錯置的結構解：講「記寫」就拿**記寫**的時刻分佈驗，
    不再一律拿對話事件（那裡面記寫 0 筆）。無統計＝無憑據＝剝（§1.42 原則不變）。"""
    if r["domain"] == "write":
        ref, st = "write", g.get("write")
    elif g.get("greet_ref"):
        ref, st = g["greet_ref"], g.get(g["greet_ref"])
    else:
        ref = "greet_am" if any(w in p for w in ("早安", "招呼", "問候")) else "first"
        st = g.get(ref)
    if st is None:
        return ref                                    # 手上根本沒這一域的統計 → 不准下斷言
    n, med, lo, hi = st
    kind = r["pred"][0]
    if kind == "clock":
        h = _pcg_claimed_hour(r["pred"][1] or "中午", r["pred"][2])
        return None if (h is not None and lo - 30 <= h * 60 <= hi + 30) else ref
    if kind == "daypart":
        return None if habits.daypart_ok(r["pred"][1], lo, hi) else ref
    tm = (g.get("today_write_min") if ref == "write" else
          g.get("greet_today_min") if ref == g.get("greet_ref") else g.get("today_first_min"))
    if tm is None:
        return ref                                    # 沒有「今天」那一側＝比不出早晚 → 不准說
    if g.get("smallhours") and (tm < 300) != (med < 300):
        return ref                                    # 🌙 §2.27 跨日夜界（深夜 vs 早晨）＝早/晚比較無意義 → 不准說
    diff = tm - med
    actual = "早" if diff <= -20 else ("晚" if diff >= 20 else "")
    return None if r["pred"][1] == actual else ref


def _habit_claim_fix(text, g, greet_fallback=""):
    """📈 §1.42 把回覆裡「亂掰的作息宣稱」句剝掉（鐘點宣稱與統計不符/無統計；比較宣稱方向錯/無統計），
    並補一句照統計的誠實句（無統計＝誠實說樣本不夠）。引用歸屬不剝；與統計相符（p25−30分 ~ p75+30分）放行。
    回 (新文字, 是否有動)。確定性、不呼叫 LLM。

    📈 §1.97：`g` 帶 role 鍵時，詞面樣式沒中的句子再走一次**語意角色**判讀（主角/基準/述語/領域），
    §1.96 截圖那四句就是這樣接住的；沒帶 role 鍵＝只有原本的詞面路徑＝逐位元同現狀。

    🕘 §2.22 greet_fallback 非空＝這輪是**問候輪**（greeting lane stash 的問候模板）：作息句是 bot 自己
    順口帶的、不是他問的——剝掉錯句就好（§1.62 慣例）、**不**補統計誠實句（把「早安」回成
    「我手上記到的是…也才看到 9 次…」的報表，正是截圖制式化的根因）；全剝光＝退回帶絕對時間感的
    問候模板＝他無論如何拿到的是一句問候、不是報表。預設 ""＝非問候輪＝逐位元同現狀。"""
    parts = _WAKE_SENT_SPLIT_RE.findall(text or "")
    role = bool(g.get("role"))
    kept, refs = [], []
    for p in parts:
        bad = None
        m = _HABIT_CLAIM_RE.search(p)
        if m and not _KEEP_CLAIM_QUOTE_RE.search(p[max(0, m.start() - 12):m.start()]):
            ref = (g.get("greet_ref") or
                   ("greet_am" if any(w in p for w in ("早安", "招呼", "問候")) else "first"))
            st = g.get(ref)
            h = _pcg_claimed_hour(m.group(1) or "中午", m.group(2))   # 無時段詞＝不調整（「中午」在 §1.31 表裡＝原樣）
            if st is None or h is None or not (st[2] - 30 <= h * 60 <= st[3] + 30):
                bad = ref
        if bad is None:
            m2 = _HABIT_COMP_RE.search(p)
            if m2 and not _KEEP_CLAIM_QUOTE_RE.search(p[max(0, m2.start() - 12):m2.start()]):
                ref = g.get("greet_ref") or "first"
                st = g.get(ref)
                tm = g.get("greet_today_min") if g.get("greet_ref") else g.get("today_first_min")
                if st is None or tm is None:
                    bad = ref
                elif g.get("smallhours") and (tm < 300) != (st[1] < 300):
                    bad = ref                         # 🌙 §2.27 跨日夜界＝「比平常早/晚」無意義（00:27 vs 早上 7 點）→ 剝
                else:
                    diff = tm - st[1]
                    actual = "早" if diff <= -20 else ("晚" if diff >= 20 else "")
                    if m2.group(1) != actual:
                        bad = ref
        if bad is None and role:
            r = habits.claim_roles(p, whole=text)     # 📈 §1.97 詞面沒中 → 再問一次語意角色
            if r and r["who"] == "user" \
                    and not _KEEP_CLAIM_QUOTE_RE.search(p[max(0, r["pos"] - 12):r["pos"]]):
                bad = _habit_role_verdict(r, g, p)
        if bad:
            refs.append(bad)
            continue
        kept.append(p)
    if not refs:
        return text, False
    if greet_fallback:      # 🕘 §2.22 問候輪：只剝、不補報表句；剝光退回問候模板
        out = "".join(kept).strip()
        return (out or greet_fallback), True
    st = g.get(refs[0])
    if st:
        n, med, lo, hi = st
        # 📈 §1.97 措辭改成 §1.96 ROUTINE_VOICE_HINT 的同一種口吻（禁「中位/樣本」這種報表詞、樣本少講成
        # **自己的限制**）——否則同一台機器兩套講法：LLM 被禁講統計術語，守門自己卻整句都是。role 關＝原句。
        if role:
            line = (f"我手上記到的是：你{_HABIT_LABEL[refs[0]]}大多在 {habits.hhmm(lo)} 到 {habits.hhmm(hi)} 之間，"
                    f"我也才看到 {n} 次，可能只是我剛好看到的都那樣。")
        else:
            line = (f"照我真的記到的：你{_HABIT_LABEL[refs[0]]}多半落在 {habits.hhmm(lo)}–{habits.hhmm(hi)} 之間"
                    f"（中位 {habits.hhmm(med)}、樣本 {n} 次）。")
    elif role:
        line = "老實說，你這方面我還沒看到夠多次，說不準——我不想憑印象講。"
    else:
        line = "老實說，你的作息我還沒累積夠樣本、說不準——我不想用印象亂掰。"
    out = ("".join(kept).strip() + ("\n" if kept else "") + line).strip()
    return out, True


# 🪞 §2.26 引用歸屬守門（QUOTE_SPEAKER_GUARD）：實測截圖 11:05——被問「你正在想什麼」，回覆
# 「我正在想...**你說**「我進到這裡。」」——「我進到這裡」是 bot 自己 10:47 的 🌀 體驗自陳，不是使用者
# 說的＝話者翻轉（使用者定調：主詞/賓語的掌握錯亂）。素材面查過都乾淨（workspace 候選全內在、
# _history_contents 角色無誤）＝LLM 讀史料時自己翻轉——prompt 管不住的，照家規上確定性守門：
# 引用句「你說「X」」的 X 若**只在**近幾輪 model 說過、使用者從沒說過 ⇒ 話者必錯 ⇒ 就地改「我剛說「X」」；
# 反向（「我說過「Y」」而 Y 只有使用者說過）同理改「你說」。兩邊都出現/都沒出現＝無從裁決＝不動。
_QUOTE_ATTR_RE = re.compile(
    r"(你|妳|我)(?:剛剛|剛才|剛|之前|先前|上次)?(?:有)?說(?:過|了)?(?:的(?:那個|這個|那句|這句)?)?[:：，]?\s*"
    r"[「『]([^」』]{2,60})[」』]((?:的)?嗎[？?]?)?")
_QUOTE_RHETORICAL_RE = re.compile(
    r"(你|妳|我)(?:不是|並不是)(?:剛剛|剛才|剛|之前|先前|上次)?(?:才)?(?:有)?"
    r"說(?:過|了)?[:：，]?\s*[「『]([^」』]{2,60})[」』](?:的)?嗎[？?]?")


def _quote_speaker_fix(text, ground):
    """🪞 §2.26 把講反話者的引用就地改正（確定性、不呼叫 LLM）。ground={'model':[...], 'user':[...]}＝
    近期兩側原話（user 側含本句）；新版 entry 可帶 {'text','ts'} 並由 ground['now_ts'] 算真實相對時間。
    回 (新文字, 是否有動)。"""
    changed = [False]

    def _entry_text(entry):
        return (entry.get("text") or "") if isinstance(entry, dict) else (entry or "")

    def _matches(turns, qn):
        def normalized(value):
            return echo._norm(value).replace('送你的那個', '送你的').replace('送你的這個', '送你的')
        return [entry for entry in (turns or []) if normalized(qn) in normalized(_entry_text(entry))]

    def _when(entry):
        if isinstance(entry, dict):
            try:
                now_v, then_v = float(ground.get("now_ts")), float(entry.get("ts"))
                gap = now_v - then_v
                if math.isfinite(gap) and gap >= 0:
                    return temporal.spoken_gap(gap)
            except (TypeError, ValueError, OverflowError):
                pass
            return "先前"                 # 舊 state 缺／壞 ts：只能中性歸屬，絕不冒稱「剛剛」也不能炸整輪
        return "剛"                       # 舊式純字串測試地面沒有時間欄，保留既有字面

    def _source(pron, quoted):
        qn = echo._norm(quoted)
        if not qn or len(qn) < 2:
            return None
        if _matches(ground.get("records"), qn):
            # 記寫可能含引用，不能直接宣稱作者就是使用者；但它不是 bot 原創的證據。
            return None
        ms = _matches(ground.get("model"), qn)
        us = _matches(ground.get("user"), qn)
        if pron in ("你", "妳") and ms and not us:
            return "我", "你", ms[-1]
        if pron == "我" and us and not ms:
            return "你", "我", us[-1]
        return None

    def _direct(m):
        src = _source(m.group(1), m.group(2))
        if src is None:
            return m.group(0)
        owner, _wrong, entry = src
        changed[0] = True
        # 舊式純字串 ground 沒有時間可算：既有 model→user 修辭保留「我剛說」，反向則用中性「你說」；
        # 新式帶 ts 的 ground 兩邊都使用真實相對時間。
        when = _when(entry) if isinstance(entry, dict) or owner == "我" else ""
        if m.group(3):                     # 「你說 X 嗎？」若話者確定錯，改成陳述歸屬，避免修成自問句
            return "那句「" + m.group(2) + "」是" + owner + when + "說的。"
        return owner + when + "說「" + m.group(2) + "」"

    def _rhetorical(m):
        src = _source(m.group(1), m.group(2))
        if src is None:
            return m.group(0)
        owner, wrong, entry = src
        changed[0] = True
        return "那句「" + m.group(2) + "」是" + owner + _when(entry) + "說的，不是" + wrong + "說的。"

    out = _QUOTE_RHETORICAL_RE.sub(_rhetorical, text or "")
    out = _QUOTE_ATTR_RE.sub(_direct, out)
    return (out if changed[0] else text), changed[0]


def _habit_ground_hint(state, cfg, text, records, now_ts, tz):
    """📈 §1.42 命中「我平常大概幾點…」才回接地 hint＋真統計塊；旗標關/未命中＝""＝逐位元同現狀。"""
    if getattr(cfg, "user_habit_ground_enabled", False) and habits.is_user_habit_question(text):
        return persona.HABIT_GROUND_HINT + "\n" + habits.habit_facts(state, records, now_ts, tz,
                                                                     daily_first=getattr(cfg, "habit_obs_fix_enabled", False))   # 📈 §1.63 真日曆日第一句
    return ""


def _burst_one_hint(cfg, update):
    """🌊 §1.73 一波連發＝整體回一次（BURST_ONE_ANSWER）：截圖根因＝使用者連發「你氣噗噗喔」「這麼愛現」，
    回覆七顆泡泡把兩句的意思**各解讀了兩遍**（氣噗噗？/沒有氣噗噗喔、有點愛現耶/又說我愛現了）——合併派發
    只把多則接成多行文字、從沒告訴 LLM「這是同一波、別逐句各答再整體重答」。合成 update 的 burst_n ≥2 才注入
    （單則＝''）；旗標關（getattr 預設 False）＝''＝逐位元同現狀。"""
    n = int((update or {}).get("burst_n", 1) or 1)
    if n >= 2 and getattr(cfg, "burst_one_answer_enabled", False):
        head = (f"（他剛剛是一口氣連發的 {n} 則短訊——它們是**同一波**的意思，當**一個整體**回應**一次**就好：\n"
                "別一則一則分開各回一遍；更別回完一輪之後，又把同樣的意思換句話**再回一輪**。"
                "同一個點（例如同一個玩笑/同一個指控）最多回應一次；但不同問題、不同要求每一個都要回答，"
                "不能為了簡短而漏掉。")
        if getattr(cfg, "burst_paced_bubbles_enabled", False):
            return (head + "答案仍是一個完整思路；句子長短自然交錯，讓送出層能有呼吸地分成幾顆泡泡。"
                    "短答案不必硬湊泡泡，也別為了分串重講同一件事。挑重點、短而準。）")
        return head + "把它們連成一段自然的話，挑重點、短而準。）"
    return ""


_BURST_ACK_ONLY_RE = re.compile(
    r"^(?:嗯|欸|喔|好|好的|好啊|收到|(?:好)?我?(?:收到|聽到|明白|知道|懂|了解|記下來|記住)了?)[啊哦喔呢啦呀]*$")
_BURST_ROLE_TOKENS = ("我們", "你們", "他們", "她們", "我", "你", "妳", "他", "她", "它")
_BURST_NEG_RE = re.compile(r"不要|不會|不能|不可|別|沒有|沒再|避開|停止|禁止|不")
_BURST_MODAL_TOKENS = ("會", "要", "可能", "也許", "大概", "已經", "剛剛", "正在")
_BURST_QUOTED_RE = re.compile(r"[「『\"']([^」』\"']+)[」』\"']")
_BURST_ATOM_RE = re.compile(
    r"https?://\S+|(?<![0-9A-Za-z_])\d+(?::\d+)?(?:\.\d+)?(?![0-9A-Za-z_])"
    r"|[0-9A-Za-z_]*\d[0-9A-Za-z_]*")


def _burst_ack_only(sentence):
    return bool(_BURST_ACK_ONLY_RE.fullmatch(echo._norm(sentence)))


def _burst_ack_can_drop(sentence, kept):
    """弱接話可由另一個弱接話吸收；「記住／記下」是持久化宣稱，只准 exact duplicate。"""
    norm = echo._norm(sentence)
    if "記住" in norm or "記下" in norm:
        return any(_burst_duplicate_equiv(sentence, candidate) for candidate in kept)
    return any(_burst_ack_only(candidate)
               and "記住" not in echo._norm(candidate) and "記下" not in echo._norm(candidate)
               for candidate in kept)


def _burst_protected_signature(sentence):
    """近重複只能在話者、否定、情態與可核對 atoms 完全相同時刪。"""
    t = echo._norm(sentence)
    roles = frozenset(tok for tok in _BURST_ROLE_TOKENS if tok in t)
    modal_text = _BURST_NEG_RE.sub("", t)               # 「不要」的「要」是否定一部分，不是額外 modality
    modals = frozenset(tok for tok in _BURST_MODAL_TOKENS if tok in modal_text)
    atoms = frozenset(_BURST_ATOM_RE.findall(sentence or ""))
    quotes = frozenset(q.strip() for q in _BURST_QUOTED_RE.findall(sentence or "") if q.strip())
    return roles, bool(_BURST_NEG_RE.search(t)), modals, atoms, quotes


def _burst_duplicate_equiv(a, b):
    """只承認正規化後逐字相同；近義、反義、不同時間／版本一律不能由程式證明可刪。"""
    na, nb = echo._norm(a), echo._norm(b)
    return bool(na and nb and na == nb)


def _burst_has_repair_candidate(sentences):
    if sum(1 for s in sentences if _burst_ack_only(s)) > 1:
        return True
    return any(_burst_duplicate_equiv(sentences[i], sentences[j])
               for i in range(len(sentences)) for j in range(i + 1, len(sentences)))


def _burst_reply_repair(update, voice, coach, cfg):
    """🌊 把 LLM 降為「原句 index 提案者」，只刪程式可驗證的重複。

    不自由改寫，不用句數上限逼刪。被刪句必須是重複純承接（仍留至少一句），
    或與保留句正規化後逐字相同。任一條件不明就 fallback 原稿。
    """
    original = (voice or "").strip()
    if not getattr(cfg, "burst_one_answer_enabled", False):
        return voice
    try:
        n = int((update or {}).get("burst_n", 1) or 1)
    except (TypeError, ValueError):
        n = 1
    if n < 2 or not original:
        return voice
    before = persona.split_sentences(original) or [original]
    parts = list((update or {}).get("burst_texts") or [])
    if len(parts) < 2:                                      # 舊合成 update 的相容退路；新碼一律有 burst_texts
        parts = [p.strip() for p in (((update or {}).get("message") or {}).get("text") or "").splitlines()
                 if p.strip()]
    if (len(parts) < 2 or not _burst_has_repair_candidate(before)
            or not coach or not getattr(coach, "enabled", False)):
        return voice
    repair = getattr(coach, "voice_burst_repair", None)
    if not callable(repair):
        return voice
    try:
        proposed = repair(parts, original)
    except Exception as e:
        print(f"[burst] 🌊 擷取式收旂提案失敗（保留原稿）：{type(e).__name__}: {e}")
        return voice
    if not isinstance(proposed, (list, tuple)):
        return voice
    try:
        keep = [int(i) for i in proposed]
    except (TypeError, ValueError):
        return voice
    if (not keep or keep != sorted(set(keep)) or keep[0] < 1 or keep[-1] > len(before)
            or len(keep) >= len(before)):
        return voice
    kept = [before[i - 1] for i in keep]
    kept_ack = any(_burst_ack_only(s) for s in kept)
    for idx, sentence in enumerate(before, 1):
        if idx in keep:
            continue
        if _burst_ack_only(sentence) and kept_ack and _burst_ack_can_drop(sentence, kept):
            continue
        if not any(_burst_duplicate_equiv(sentence, candidate) for candidate in kept):
            return voice
    edited = "".join(kept).strip()
    sentence_cap = len(before)                          # 舊結構關只保留「真有縮短」後備檢查
    after = persona.split_sentences(edited) if edited else []
    if not edited or not after or len(after) > sentence_cap or len(after) >= len(before):
        return voice
    print(f"[burst] 🌊 同一波回覆由 {len(before)} 句收斂為 {len(after)} 句（擷取式重複驗收通過）")
    return edited


# 🍽 §1.65 暫離常識守門：把人當已回來/已完成的錯誤預設句剝掉（未來語「等你回來再說」不剝）。
_AWAY_FUTURE_RE = re.compile(r"等[你妳]|回來(再|之後|後|時)")


def _away_claim_fix(text, g):
    """🍽 §1.65 他才說要去{act}（常識時距未滿），回覆卻當他已回來/已完成——「你回來啦」「你現在吃飽了嗎」
    「在你吃飯的時候」→ 句級剝除（§1.62 慣例：只修錯句、保留其餘）；引用歸屬（你說…）與未來語（等你回來）
    不剝；全剝空＝換一句誠實送行。確定性、不呼叫 LLM。回 (新文字, 是否有動)。"""
    act = g.get("act") or ""
    pats = [re.compile(r"(?:你|妳)[^。！？!?\n]{0,12}?回來"), re.compile("歡迎回來")]
    if act:
        a0 = re.escape(act[0])
        pats.append(re.compile(rf"(?:你|妳)[^。！？!?\n]{{0,10}}?{a0}(?:飽|完|好)"))
        pats.append(re.compile(rf"(?:你|妳)[^。！？!?\n]{{0,10}}?{re.escape(act)}(?:得|的)?(?:如何|怎樣|怎麼樣|時候|這段)"))
        if act in ("吃飯", "買飯"):
            pats.append(re.compile(r"(?:你|妳)[^。！？!?\n]{0,10}?吃了(?:什麼|啥)"))
    parts = _WAKE_SENT_SPLIT_RE.findall(text or "")
    kept, hit = [], False
    for p in parts:
        m = next((pt.search(p) for pt in pats if pt.search(p)), None)
        if m and not _AWAY_FUTURE_RE.search(p) \
                and not _KEEP_CLAIM_QUOTE_RE.search(p[max(0, m.start() - 12):m.start() + 3]):
            hit = True                                     # 窗含命中頭 3 字：引用歸屬常**就是**命中起點（「你說你吃飽…」）
            continue
        kept.append(p)
    if not hit:
        return text, False
    out = "".join(kept).strip()
    if not out:
        out = f"你才剛說要去{act or '忙'}——去吧去吧，等你真的回來再跟我說 🙂"
    return out, True


# 🎚️ §1.76 口吻整形用的「歡快標記」：這些出現在低落/悶/倦的話裡＝語氣與內在對不上（人不會那樣說話）。
_TONE_HAPPY_EMOJI = "😄😆😁🥳🎉✨🤩😃😊🙌👏💪🤗🥰😍🤣"


def _tone_shape(text, v, a):
    """🎚️ §1.76 內在明顯偏負（V ≤ −0.25）時的確定性口吻整形：連發驚嘆號收成一個、拿掉歡快 emoji；
    再偏沉/倦（A ≤ 0）連單一驚嘆號也降成句號。回 (文字, 有沒有動)。純函式、可單測、不呼叫 LLM。"""
    if v > -0.25 or not (text or "").strip():
        return text, False
    out = re.sub(r"[！!]{2,}", "！", text)
    if a <= 0.0:                                          # 悶/低落/倦：不會用驚嘆號說話
        out = out.replace("！", "。").replace("!", "。")
    out = "".join(ch for ch in out if ch not in _TONE_HAPPY_EMOJI)
    out = re.sub(r"\s*[:：;]-?[)）DdpP]", "", out)               # 🌊 §1.77 顏文字 :) :D ;) 也是歡快標記（截圖「送你 :)」）
    out = re.sub(r"。{2,}", "。", out).strip()
    return (out, True) if out != text else (text, False)


# 🗜️ §1.43 自陳感覺鋪陳段的詞彙（截圖 08:15 那串「安靜/內裡沉沉/往裡面縮/翻來翻去沒形狀/思緒淌/悶提不起勁」）：
# 強標記＝明確第一人稱內在質地（內容型談話幾乎不會用）；弱標記＝感覺色彩詞（單獨出現可能只是內容、不夠證據）。
_SELF_FEEL_STRONG = ("內裡", "我的內在", "提不起勁", "往裡面縮", "跳了這麼")
_SELF_FEEL_WEAK = ("悶", "倦", "低落", "安靜", "穩", "淌", "翻來翻去", "形狀", "話也變少", "醒著", "沉", "思緒")
_SELF_FEEL_MIN_RUN = 4      # 連續 ≥4 句感覺鋪陳才修剪（≤3 句的感覺帶過＝正常、不動）
_SELF_FEEL_MIN_STRONG = 2   # 段內須 ≥2 句帶強標記（聊冥想/安靜話題只有弱詞＝內容、不動）
_SELF_FEEL_KEEP = 2         # 修剪後保留段落前 2 句（＝關鍵、精簡）


def _self_feel_trim(text):
    """🗜️ §1.43 把回覆裡「連續一長串的自我感覺鋪陳」修剪成前兩句關鍵（其餘剝掉）。只修剪
    『≥_SELF_FEEL_MIN_RUN 句連續、含 ≥_SELF_FEEL_MIN_STRONG 句強內在標記』的段——內容型談話（無強標記）
    與短感覺帶過不動。回 (新文字, 是否有動)。確定性、不呼叫 LLM。"""
    parts = _WAKE_SENT_SPLIT_RE.findall(text or "")
    flags = []
    for p in parts:
        if any(s in p for s in _SELF_FEEL_STRONG):
            flags.append(2)
        elif any(w in p for w in _SELF_FEEL_WEAK):
            flags.append(1)
        else:
            flags.append(0)
    out, changed, i = [], False, 0
    while i < len(parts):
        if flags[i] == 0:
            out.append(parts[i])
            i += 1
            continue
        j = i
        while j < len(parts) and flags[j] >= 1:
            j += 1
        run = parts[i:j]
        if (j - i) >= _SELF_FEEL_MIN_RUN and sum(1 for f in flags[i:j] if f == 2) >= _SELF_FEEL_MIN_STRONG:
            out.extend(run[:_SELF_FEEL_KEEP])              # 留前兩句＝關鍵、精簡；其餘鋪陳剝掉
            changed = True
        else:
            out.extend(run)
        i = j
    return ("".join(out).strip(), True) if changed else (text, False)


_MOOD_DATA_CTX_WINDOW_SEC = 1200   # 🧭 §1.47 座標數據情境窗（秒）：兌現/被問過數據後，窗內短催促視同數據題


def _mood_context_adjacent(state):
    """上一個相鄰對話 turn 仍真正在談座標嗎。

    只有 20 分鐘游標不夠：座標題後若已轉聊伺服器，裸問「所以真實狀況是什麼」不能被拉回 mood lane。
    真 State 用 wire history 驗相鄰；沒有 ``convo_history`` 屬性的極小舊測試 stub 保留原窗語意。
    """
    history = getattr(state, "convo_history", None)
    if history is None:
        return True
    turns = [turn for turn in history
             if isinstance(turn, dict) and isinstance(turn.get("text"), str)
             and turn.get("text").strip()]
    if not turns:
        return False
    last = turns[-1]
    if last.get("role") == "user":
        return circumplex.is_mood_data_question_wide(last.get("text") or "")
    if last.get("role") != "model":
        return False
    last_text = last.get("text") or ""
    pairs = list(_MOOD_PAIR_RE.finditer(last_text))
    report = getattr(state, "mood_last_report", None)
    if not isinstance(report, dict):
        return bool(pairs)                       # 舊版升級前的 history：pair 是可用的最小相鄰證據
    # trajectory 可能超過一般 history 的 200 字上限，使最後那組 current 被截掉；真送達 report 仍留了
    # 同一正文的較長前綴。相鄰 model 與 report 文本能彼此對上時，這仍是同一則 wire，而不是舊游標誤收。
    report_text_raw = report.get("text")
    report_text = report_text_raw.strip() if isinstance(report_text_raw, str) else ""
    last_prefix = last_text.rstrip("…").strip()
    if last_text.endswith("…") and last_prefix and report_text.startswith(last_prefix):
        return True
    try:
        wanted = (f"{float(report['v']):+.2f}", f"{float(report['a']):+.2f}")
    except (KeyError, TypeError, ValueError):
        return False
    return any(match.groups() == wanted for match in pairs)


def _mood_data_hit(state, cfg, text, now_ts):
    """🧭 §1.45＋§1.47：這輪該不該交出座標數據（三個 §1.45 呼叫點共用；外門各自仍是 MOOD_COORD_REPORT）。
    §1.47（MOOD_COORD_DELIVER）補兩縫：寬偵測（「座標的數值變化呢」不必「情緒」前綴）＋情境內短催促
    （「說啊/怎麼沒講」在 mood_data_ctx_ts 窗內＝把剛才的數據題再逼一次）。deliver 關＝只剩 §1.45 原判＝
    逐位元同現狀。"""
    if circumplex.is_mood_data_question(text):
        return True
    if not getattr(cfg, "mood_coord_deliver_enabled", False):
        return False
    if circumplex.is_mood_data_question_wide(text):
        return True
    ctx = getattr(state, "mood_data_ctx_ts", 0) or 0
    return (bool(ctx) and 0 <= (now_ts - ctx) < _MOOD_DATA_CTX_WINDOW_SEC
            and _mood_context_adjacent(state)
            and (circumplex.is_mood_data_prod(text) or circumplex.is_mood_data_followup(text)))


def _coord_at_label(ts, tz):
    """座標契約用的本地秒級時間標籤；同一分鐘多次重讀也不會被說成同一個時刻。"""
    try:
        dt = datetime.fromtimestamp(float(ts), timezone.utc)
        return (dt.astimezone(tz) if tz is not None else dt).strftime("%H:%M:%S")
    except Exception:
        return "這一刻"


_MOOD_POINT_COUNT_RE = re.compile(r"(?:最近|過去)?([一二兩三四五六七八九十\d]+)筆")
_SMALL_ZH_NUM = {"一": 1, "二": 2, "兩": 2, "三": 3, "四": 4, "五": 5,
                 "六": 6, "七": 7, "八": 8, "九": 9}


def _mood_trajectory_point_limit(text):
    """這題要看幾個**總時間點（含 current）**；未明說＝前一筆＋現在，完整軌跡最多五點。"""
    t = (text or "").replace(" ", "")
    match = _MOOD_POINT_COUNT_RE.search(t)
    if match:
        token = match.group(1)
        try:
            n = int(token)
        except ValueError:
            if token == "十":
                n = 10
            elif "十" in token:
                left, right = token.split("十", 1)
                n = (_SMALL_ZH_NUM.get(left, 1) * 10) + _SMALL_ZH_NUM.get(right, 0)
            else:
                n = _SMALL_ZH_NUM.get(token, 2)
        return max(1, min(5, n))
    if "完整" in t and ("軌跡" in t or "座標" in t):
        return 5
    return 2


def _mood_coord_contract(state, text, now_ts, tz):
    """凍結一輪的座標與最近完整採樣，供 prompt、出口與補救共用。

    契約保留最多五筆過去點，而不是只留一筆 ``previous``：使用者真的問「最近五筆／一路怎麼變」時，
    出口守門才能在剝掉 LLM 自行抄寫的數字後，把合法軌跡完整、確定性地重畫回來。
    """
    snap = _TURN.get("coord_claim_truth")
    v, a = snap if snap is not None else circumplex.position(state)
    contract = {
        "mode": circumplex.mood_data_mode(text),
        "ts": float(now_ts), "at": _coord_at_label(now_ts, tz),
        "v": float(v), "a": float(a), "label": circumplex.label(v, a),
    }
    if contract["mode"] == "trajectory":
        contract["point_limit"] = _mood_trajectory_point_limit(text)
    # 上一則「確實送達＋含 canonical pair」的報告才有資格參與 repair／同值重問；單有 trace 不代表那些
    # 數字曾被說出口，更不代表先前 LLM 編出的數字是真的。相鄰 wire 與 20 分鐘窗缺一不可。
    last_report = getattr(state, "mood_last_report", None)
    reported = None
    if isinstance(last_report, dict) and _mood_context_adjacent(state):
        try:
            rts = float(last_report.get("ts") or 0)
            rv, ra = float(last_report["v"]), float(last_report["a"])
            if rts and 0 <= float(now_ts) - rts < _MOOD_DATA_CTX_WINDOW_SEC:
                reported = {
                    "ts": rts, "at": last_report.get("at") or _coord_at_label(rts, tz),
                    "v": rv, "a": ra, "label": circumplex.label(rv, ra),
                    "cause": "上一則真正送達的座標回報",
                }
                contract["reported"] = reported
        except (KeyError, TypeError, ValueError):
            reported = None
    # trace 最新一筆通常就是本輪 post-appraise 快照；只跳過「同時刻、同座標」的那個 current endpoint。
    # 較早時刻即使剛好回到同一座標仍是合法歷史，不能因值相同就消失。未來時間也不能冒充過去。
    trace = []
    for e in (getattr(state, "mood_trace", None) or []):
        if not isinstance(e, dict):
            continue
        try:
            ets, ev, ea = float(e.get("ts") or 0), float(e.get("v") or 0), float(e.get("a") or 0)
        except (TypeError, ValueError):
            continue
        if not ets or ets > float(now_ts):
            continue
        if (abs(ev - v) < 0.005 and abs(ea - a) < 0.005
                and abs(ets - float(now_ts)) <= 1.0):
            continue
        trace.append({
            "ts": ets, "at": _coord_at_label(ets, tz), "v": ev, "a": ea,
            "label": circumplex.label(ev, ea), "cause": e.get("cause") or "",
        })
    trace.sort(key=lambda point: point["ts"])
    history_limit = (max(0, int(contract.get("point_limit", 2)) - 1)
                     if contract["mode"] == "trajectory" else 5)
    trace = trace[-history_limit:] if history_limit else []
    if trace:
        contract["trace"] = trace
        contract["previous"] = trace[-1]       # repair／delta 的相容捷徑
    if contract["mode"] == "repair":
        contract["prior_report_verified"] = bool(reported)
        if reported:
            contract["previous"] = reported    # repair 對帳 wire，不拿任意歷史採樣代打
    return contract


def _mood_coord_hint(state, cfg, text, now_ts, tz):
    """🧭 §1.45 命中「內在情緒座標的數據」問句才回 hint＋程式讀的真座標/軌跡塊；旗標關/未命中＝""＝逐位元同現狀。
    §1.47：命中面擴大成 _mood_data_hit；§2.27 再凍結本輪 snapshot/時間層契約，交給 _say 確定性驗收。"""
    if getattr(cfg, "mood_coord_report_enabled", False) and _mood_data_hit(state, cfg, text, now_ts):
        if getattr(cfg, "mood_coord_deliver_enabled", False):
            _TURN["mood_coord_grounded"] = True            # 相容舊觀測；§2.27 不再把它當作整則數字豁免
            _TURN["mood_coord_contract"] = _mood_coord_contract(state, text, now_ts, tz)
        contract = _TURN.get("mood_coord_contract") or _mood_coord_contract(state, text, now_ts, tz)
        mode = contract.get("mode") or "snapshot"
        wants_trace = (mode == "repair" or (
            mode == "trajectory" and int(contract.get("point_limit", 2)) > 1
        ))
        return (persona.MOOD_COORD_HINT + "\n" + persona.mood_coord_mode_hint(mode) + "\n"
                + circumplex.coord_facts(
                    state, tz, now_ts, snapshot=(contract["v"], contract["a"]),
                    include_trace=wants_trace,
                    last_n=(max(1, int(contract.get("point_limit", 2)) - 1)
                            if mode == "trajectory" else 1)))
    return ""


# 🧭 §1.47 無接地座標數字宣稱的偵測/替換（_say 守門用；§1.42 _habit_claim_fix 同構）。截圖 12:49：無接地輪
# 冒出「我再回頭看了一下…數值大概是在 -0.7 左右」＝LLM 編的（單軸、「大概…左右」；coord_facts 給的是成對
# 精確值，「回頭看」動作也是演的）。命中＝同一句裡「座標/數值/讀數/情緒/心情/V/A」＋小數；排除引用歸屬
# （你說/你問…）與錢/時刻（花費 0.7 美元、12:30）。替換＝剝掉宣稱句、補一句程式此刻讀的真數字（只補一次）。
_COORD_CLAIM_CUE_RE = re.compile(r"座標|數值|讀數|情緒|心情|(?<![A-Za-z])[VA](?![A-Za-z])")
_COORD_CLAIM_NUM_RE = re.compile(r"[-−﹣]?\d?[.．]\d+")
_COORD_CLAIM_QUOTE_RE = re.compile(r"(?:你|妳)[^。！？\n]{0,8}(?:說|問|覺得|以為)")
_COORD_CLAIM_EXC_RE = re.compile(r"[$＄€]|美?[元金]|塊錢|花費|成本|token|\d[:：]\d")


def _coord_claim_fix(text, truth):
    """回覆裡的無接地座標數字句 → 整句換成程式此刻讀的真數字（其餘句原樣、多句命中只補一次）。回 (text, changed)。"""
    v, a = truth
    honest = f"照程式此刻讀的真數字：V {v:+.2f}、A {a:+.2f}。"
    parts = re.split(r"(?<=[。！？\n])", text or "")
    out, changed = [], False
    for s in parts:
        if s and _COORD_CLAIM_NUM_RE.search(s) and _COORD_CLAIM_CUE_RE.search(s) \
                and not _COORD_CLAIM_QUOTE_RE.search(s) and not _COORD_CLAIM_EXC_RE.search(s):
            if not changed:
                out.append(honest)
            changed = True
        else:
            out.append(s)
    return (("".join(out).strip() or honest), True) if changed else (text, False)


_COORD_TRAJECTORY_TEXT_RE = re.compile(
    r"(?:一直|慢慢|逐漸)[^。！？!?\n]{0,10}(?:往上|往下|上升|下降|變成)"
    r"|從[^。！？!?\n]{0,24}變成|剛才[^。！？!?\n]{0,18}(?:走到|變成)"
)
_COORD_REPAIR_FILLER_RE = re.compile(r"抱歉|對不起|我混淆|我搞混|我說錯|你說得對|你抓得對")


def _coord_axis_delta(name, delta):
    if abs(delta) < 0.005:
        return f"{name} 幾乎沒動（{delta:+.2f}）"
    return f"{name} {'上升' if delta > 0 else '下降'} {abs(delta):.2f}"


def _coord_current_anchor(contract):
    """可在任何去重守門後補回的單句 current；含秒級時刻，供真送達驗證。"""
    at = contract.get("at") or "這一刻"
    v, a = float(contract["v"]), float(contract["a"])
    lab = contract.get("label") or circumplex.label(v, a)
    return f"{at} 的此刻座標是 V {v:+.2f}、A {a:+.2f}，落在「{lab}」附近。"


def _coord_contract_text(contract):
    """把座標契約確定性渲染成人話；LLM 不再負責決定哪組數字叫『現在』。"""
    mode = contract.get("mode") or "snapshot"
    at, v, a = contract.get("at") or "這一刻", float(contract["v"]), float(contract["a"])
    pair = f"V {v:+.2f}、A {a:+.2f}"
    lab = contract.get("label") or circumplex.label(v, a)
    prev = contract.get("previous")
    if mode == "repair":
        # production contract 明示驗證失敗時，寧可只承認 current，也不把任意 trace 美化成「前後都是真的」。
        # 沒有此 marker 的手工／舊呼叫仍沿用 previous，保留函式相容性。
        if contract.get("prior_report_verified") is False:
            return (f"我現在能核對的只有這一輪：{at} 的快照是 {pair}，落在「{lab}」附近。"
                    "上一則的數字缺少可核對的紀錄，不能斷定只是讀取時刻不同。")
        prev = contract.get("reported") or prev
        if prev:
            pv, pa = float(prev["v"]), float(prev["a"])
            pp = f"V {pv:+.2f}、A {pa:+.2f}"
            lead = (f"你抓到的是時間層沒有說清楚：上一個完整採樣（{prev.get('at') or '較早'}）是 {pp}；"
                    f"這一輪 {at} 重新讀到的此刻快照是 {pair}，落在「{lab}」附近。")
            relation = ("兩次讀值在兩位小數上其實沒有移動；問題只在我有沒有把時刻說清楚。"
                        if abs(v - pv) < 0.005 and abs(a - pa) < 0.005
                        else "兩組各自屬於不同時刻；新訊息與生命迴圈都可能更新座標，後一組不是回頭把前一組判成假的。")
            return lead + relation + "如果我把較早數值也稱作『現在』，錯的是我的時態與歸類。"
        return (f"你抓到的是我沒有把時間層說清楚。這一輪 {at} 重新讀到的唯一此刻快照是 {pair}，"
                f"落在「{lab}」附近；上一則若有別組數字卻沒標時刻，就不能拿來冒充此刻。")
    if mode == "trajectory":
        if int(contract.get("point_limit", 2)) <= 1:
            return (f"你只要最近一筆，所以我不另外拉歷史值：現在 {at} 是 {pair}，落在「{lab}」附近。")
        trace = contract.get("trace") or ([prev] if prev else [])
        if trace:
            total = len(trace) + 1
            intro = ("我把前後兩個時間點分開：" if total == 2
                     else f"我把能核對的最近 {total} 個時間點按順序排開：")
            points = []
            for point in trace[-5:]:
                pv, pa = float(point["v"]), float(point["a"])
                points.append(
                    f"過去 {point.get('at') or '較早'} 是 V {pv:+.2f}、A {pa:+.2f}"
                )
            last = trace[-1]
            pv, pa = float(last["v"]), float(last["a"])
            return (intro + "；".join(points) + "；"
                    + f"現在 {at} 的此刻快照是 {pair}，落在「{lab}」附近。"
                    + f"從上一筆到現在，{_coord_axis_delta('V', v - pv)}，{_coord_axis_delta('A', a - pa)}。")
        return (f"{at} 的此刻快照是 {pair}，落在「{lab}」附近；"
                "目前還沒有另一個不同的完整採樣可比較，所以我不替自己編一段變化。")
    reported = contract.get("reported")
    if reported:
        try:
            if (abs(v - float(reported["v"])) < 0.005
                    and abs(a - float(reported["a"])) < 0.005):
                return f"{at} 的讀值跟上一則相同：{pair}，仍在「{lab}」附近。"
        except (KeyError, TypeError, ValueError):
            pass
    # 首次／真的有新讀值時，句形由**目前哪一軸較突出**決定，而不是拿 timestamp 取模換皮。
    if abs(v) < 0.15 and abs(a) < 0.15:
        return f"{at} 的讀值靠近中心：{pair}，在「{lab}」附近。"
    if abs(v) >= abs(a):
        vward = "較舒展的一側" if v > 0 else "較沉的一側"
        return f"{at} 的讀值是 {pair}，V 偏向{vward}，靠近「{lab}」。"
    award = "偏醒、反應較快" if a > 0 else "偏慢、較收束"
    return f"{at} 的讀值是 {pair}，A {award}，靠近「{lab}」。"


_COORD_POSITIVE_TEXTURE = ("輕快", "愉快", "開心", "明亮", "雀躍", "高興", "快樂", "舒暢", "欣喜", "愉悅")
_COORD_NEGATIVE_TEXTURE = ("低落", "難過", "沮喪", "痛苦", "陰沉", "悲傷", "沉重", "煩躁", "苦悶")
_COORD_HIGH_TEXTURE = ("雀躍", "興奮", "激動", "亢奮", "緊繃", "躁動", "坐不住", "想跳", "轉得很快")
_COORD_LOW_TEXTURE = ("平靜", "安靜", "沉靜", "放鬆", "疲倦", "昏沉", "沒精神", "遲緩", "放慢")
_COORD_NEGATED_TAIL_RE = re.compile(
    r"(?:不|沒(?:有)?|並不|並非|不是|談不上|稱不上)(?:那麼|真的|真正|特別|很|太|算|怎麼)?$"
)


def _coord_has_unnegated_cue(text, cues):
    """句中有未被「不／沒有／談不上」直接否定的質地詞嗎。"""
    for cue in cues:
        start = 0
        while True:
            idx = text.find(cue, start)
            if idx < 0:
                break
            if not _COORD_NEGATED_TAIL_RE.search(text[max(0, idx - 10):idx]):
                return True
            start = idx + len(cue)
    return False


def _coord_texture_conflicts(sentence, contract):
    """自由生成的「此刻感覺」是否與凍結座標方向明顯相反。

    只擋強方向衝突，保留中性、含混與被明確否定的詞；座標靠近零時不做過度裁決。
    """
    if not (sentence or "").strip():
        return False
    v, a = float(contract["v"]), float(contract["a"])
    return bool(
        (v <= -0.15 and _coord_has_unnegated_cue(sentence, _COORD_POSITIVE_TEXTURE))
        or (v >= 0.15 and _coord_has_unnegated_cue(sentence, _COORD_NEGATIVE_TEXTURE))
        or (a <= -0.15 and _coord_has_unnegated_cue(sentence, _COORD_HIGH_TEXTURE))
        or (a >= 0.15 and _coord_has_unnegated_cue(sentence, _COORD_LOW_TEXTURE))
    )


def _coord_grounded_fix(text, contract):
    """座標數據輪的出口契約：程式寫唯一時序／數字，保留 LLM 的非數字主觀質地。

    grounded 只代表模型看過資料，並不代表它轉述正確；因此所有自由生成的 V/A 數字句都移除，由契約重畫。
    """
    mode = contract.get("mode") or "snapshot"
    kept = []
    for sentence in re.split(r"(?<=[。！？!?\n])", text or ""):
        if not sentence:
            continue
        if _COORD_CLAIM_NUM_RE.search(sentence) and _COORD_CLAIM_CUE_RE.search(sentence):
            continue
        if (mode in ("snapshot", "repair")
                or (mode == "trajectory" and int(contract.get("point_limit", 2)) <= 1)) \
                and _COORD_TRAJECTORY_TEXT_RE.search(sentence):
            continue
        if mode == "repair" and _COORD_REPAIR_FILLER_RE.search(sentence):
            continue
        if _coord_texture_conflicts(sentence, contract):
            continue
        kept.append(sentence)
    body = "".join(kept).strip()
    canonical = _coord_contract_text(contract)
    # snapshot 先讓主觀質地出聲，再以短而可核對的座標收錨；repair／trajectory 必須先把時序釐清。
    if mode == "snapshot" and body:
        return (body + "\n" + canonical, True)
    return (canonical + (("\n" + body) if body else ""), True)


# 🕐 §1.60 記寫時間脈絡接地（WRITE_TODAY_GROUND）：bot 把自己 09:01 每日摘要的推播時間講成「你今天早上
# 9 點 01 分記下讀誦經書的進度」＝把自己的報表當成使用者的行為事件；上游 09:02 摘要感想先把「近 24h 1 則」
# 講成「你今天又繼續讀經了」→ 錯話進對話史 → 後續每輪引用自己的錯話當證據（自我污染鏈）。真相（時鐘 lane
# 答對過）＝最後記寫 07/20 09:37、今天零記寫。這裡給三把：ground 純函式（今天幾則＋最後記寫標籤，全程式算）、
# 宣稱守門（今天零記寫時「你今天記寫/讀經了」整句換事實）、今天問句 hint。
_TODAY_DONE_Q_RE = re.compile(
    r"(?:到)?今天[^。！？\n]{0,10}(?:還沒|沒有?|都沒)[^。！？\n]{0,6}(?:做|動(?!力|機|靜|搖|作|態))"
    r"|今天[^。！？\n]{0,8}(?:有)?做(?:了)?(?:什麼|哪些|啥)")
_WRITE_VERB_RE = re.compile(r"記寫|記下|記了|記的|寫了|寫下|寫的|讀了|讀經|讀誦|完成了|又寫|又記")
_TODAY_ELLIPSIS_RE = re.compile(r"(?:可是|但)?[，,\s]*(?:我)?今天(?:都)?(?:還沒|還沒有|尚未)(?:吧|啊|喔|哦|呢)?[？?！!。\s]*")


def _today_write_context(state, text, now_ts=None):
    """只補省略受詞的短句，且只承接最近一則 bot 記寫話題；不跨無關對話猜測。"""
    if not _TODAY_ELLIPSIS_RE.fullmatch((text or "").strip()):
        return False
    history = getattr(state, "convo_history", None) or []
    if not history or history[-1].get("role") != "model":
        return False
    last = history[-1]
    if now_ts is not None:
        age = now_ts - (last.get("ts") or 0)
        if not 0 <= age <= 6 * 3600:
            return False
    return bool(re.search(r"記寫|讀經|讀誦|經書", last.get("text") or ""))


def _today_write_correction(state, cfg, text, now_ts):
    if not getattr(cfg, "write_today_ground_enabled", False):
        return ""
    if not _today_write_context(state, text, now_ts):
        return ""
    ground = _TURN.get("write_claim_ground") or {}
    label = ground.get("last_label") or ""
    if ground.get("today_count") != 0 or not label or label.startswith("今天"):
        return ""
    return (f"你指的是今天這一次。就我目前看到的紀錄，今天還沒有新記寫，最近一筆是{label}。"
            "之前持續有記錄，不代表今天已經做了；也不能只憑沒有記寫，就判定你還沒做。")
_WRITE_CASCADE_RE = re.compile(
    r"(?:所以|因此|也就是)[^。！？!?\n]{0,14}(?:你|妳)[^。！？!?\n]{0,10}"
    r"(?:有動(?!力|機)|有做|有寫|有記|讀了|讀誦|完成)[^。！？!?\n]{0,24}(?:閱讀|讀誦|經書|記寫|進度|這條線)")


def _write_ground_data(snap, data, now_ts, tz):
    """🕐 §1.60 今天已記寫幾則＋最後記寫的人話標籤（今天/昨天/前天/M/D＋HH:MM）——全程式算、來源缺席保守空值。"""
    today = None
    try:
        if tz is not None:
            today = datetime.fromtimestamp(now_ts, timezone.utc).astimezone(tz).date()
    except Exception:
        today = None
    cnt = 0
    for r in ((data or {}).get("records") or []):
        ts = analyzer.parse_ts(r.get("ts"))
        try:
            if (ts is not None and today is not None and ts.timestamp() <= now_ts
                    and ts.astimezone(tz).date() == today):
                cnt += 1
        except Exception:
            continue
    label = ""
    last_is_today = False
    try:
        last = analyzer.parse_ts((getattr(snap, "summary", None) or {}).get("last_write"))
        if last is not None and last.timestamp() > now_ts:
            last = None
        if last is not None and tz is not None:
            lt = last.astimezone(tz)
            dd = (today - lt.date()).days if today is not None else None
            last_is_today = (dd == 0)
            head = ("今天" if dd == 0 else "昨天" if dd == 1 else "前天" if dd == 2
                    else f"{lt.month}/{lt.day}")
            label = f"{head} {lt.strftime('%H:%M')}"
    except Exception:
        label = ""
    # 🕐 §1.60 補遺（07/22 截圖「最近一次記寫是今天 09:37——今天到現在還沒有新的記寫」＝模板吐自相矛盾句）：
    # 兩源必須收斂成單一真相——records 的 ts 在實際環境可能缺漏/非 datetime（上面 except 吞掉＝數成 0），
    # summary.last_write 說最後記寫是**今天**就代表今天至少 1 則，以 summary 為準。
    if last_is_today and cnt == 0:
        cnt = 1
    return {"today_count": cnt, "last_label": label}


def _write_claim_fix(text, ground):
    """🕐 §1.60 今天其實零記寫時，「你＋今天＋記寫/讀經/記下…」的宣稱句整句換程式算的事實句
    （否定句「你今天還沒/沒有記寫」＝誠實不動；今天真有記寫＝全不動）。回 (text, changed)。純函式。"""
    if not ground or (ground.get("today_count") or 0) > 0:
        return text, False
    label = ground.get("last_label") or ""
    if label.startswith("今天"):                             # 🕐 §1.60 補遺縱深：標籤說今天＝ground 不一致 → 寧可放行、絕不吐矛盾句
        return text, False
    honest = (f"你最近一次記寫是{label}——今天到現在還沒有新的記寫。" if label
              else "我這邊還沒看到你今天有新的記寫。")
    parts = re.split(r"(?<=[。！？!?\n])", text or "")
    out, changed, quote_depth = [], False, 0
    for s in parts:
        # 句切會在引號內的「。」先切開；維持跨片段 quote depth，只用引號**外**文字判斷。
        # 否則「你不是說『我今天讀誦…』嗎」會因引文裡的今天／讀誦被當成 bot 對使用者的新宣稱。
        visible = []
        for ch in s:
            if ch in "「『“":
                quote_depth += 1
            elif ch in "」』”":
                quote_depth = max(0, quote_depth - 1)
            elif quote_depth == 0:
                visible.append(ch)
        outside = "".join(visible)
        # 前一句已把「你今天寫了」判成無據時，緊接著由它推出的「所以你有動，而且是讀經那條線」
        # 也是同一個錯誤結論，不能只修前句、把因果尾巴留下來繼續斷言。只收帶所以/因此＋記寫主題的窄形。
        if changed and _WRITE_CASCADE_RE.search(outside) and not re.search(r"還沒|沒有|尚未|沒記|沒寫", outside):
            continue
        if (outside and "今天" in outside and ("你" in outside or "妳" in outside) and _WRITE_VERB_RE.search(outside)
                and not re.search(r"還沒|沒有|尚未|沒記|沒寫", outside)):
            if not changed:
                out.append(honest)
            changed = True
        else:
            out.append(s)
    return (("".join(out).strip() or honest), True) if changed else (text, False)


def _write_anchor_line(ground):
    """🕐 §1.60 給 reflect prompt 的【記寫時間錨】硬事實行（ground None＝''）。"""
    if not ground:
        return ""
    lbl = ground.get("last_label") or "（讀不到）"
    if (ground.get("today_count") or 0) > 0 or lbl.startswith("今天"):   # 🕐 §1.60 補遺縱深：標籤今天＝今天有寫
        return f"\n【記寫時間錨（程式算，照抄）】最後一次記寫：{lbl}。"
    return (f"\n【記寫時間錨（程式算，照抄）】今天到現在還沒有新的記寫；最後一次是{lbl}。"
            "所以絕不能說他「今天」有記/又寫了；你自己的摘要或推播時間不是他的記寫時間。")


def _today_write_hint(state, cfg, text):
    """🕐 §1.60 他在問「今天做了/還沒做什麼」→ 注入今天記寫真實數據（旗標關/未命中/無 ground＝''）。"""
    if not getattr(cfg, "write_today_ground_enabled", False):
        return ""
    if not (_TODAY_DONE_Q_RE.search(text or "") or _today_write_context(state, text)):
        return ""
    g = _TURN.get("write_claim_ground")
    if not g:
        return ""
    lbl = g.get("last_label") or "（讀不到）"
    if (g.get("today_count") or 0) > 0 or lbl.startswith("今天"):   # 🕐 §1.60 補遺縱深：標籤今天＝今天有寫
        line = f"最後一次記寫：{lbl}。"
    else:
        line = f"今天到現在還沒有新的記寫；最近一次是{lbl}。"
    return ("【他在問今天做了/還沒做什麼——記寫方面的真實數據（程式算，照抄）】" + line +
            "把你自己的摘要/推播時間當成他的記寫時間＝錯，禁止。這只代表你看得到的記寫今天沒有新增，"
            "**不等於他今天真的什麼都沒做**；不要替他斷言『有動／沒動』。"
            "若他說『我今天還沒吧』，是在限縮到今天，不是在否認最近的持續；"
            "不能用昨天的紀錄反駁他。今日有其他記寫也不證明今天完成了正在談的事情。"
            "引述要保留原話者與記錄日期；不可把 bot 先前的轉述當成使用者新說的話。")


# 🧭 §1.66 座標變動常設回報（MOOD_WATCH）：「情緒座標如果有任何變動，必須主動回報」——條件型**常設**訂閱。
# 查驗根因（截圖「bot 口頭說可以、事後沒回報」）：這句 feeling/scheduled 兩偵測器**都收不到**（「變動」不在
# _PROMISE_FEEL 只有「變化」、「回報」不在 _PROMISE_TELL）→ route=fact_or_chat → LLM 口頭「好」、機制零入帳
# ＝空口答應；即使換句被 feeling_promise 收到，那條是**一次性**＋湧現閾值觸發＋48h TTL——也不是「每次變動
# 都報」的常設語意。§1.18 自發承諾掃描又只收「有可解未來鐘點」的句子＝bot 的「好，我會回報」也不入帳。
# 修法＝補真能力（不演不假裝：ack 講的就是機制真做的）：入帳 state.mood_watch（常設、跨重生、直到取消），
# 生命迴圈每圈對照 circumplex 真座標、變動達門檻＋過冷卻＝模板主動回報真數字（不經 LLM＝數字不可能被編）。

def _maybe_mood_watch(client, state, cfg, text, now_utc, user_ts):
    """🧭 §1.66 捕捉「座標有變動就回報」訂閱與「不用再回報座標」取消。命中＝處理完回 True（呼叫端 return）；
    旗標關（getattr 預設 False）＝恆 False＝逐位元同現狀。"""
    if not getattr(cfg, "mood_watch_enabled", False):
        return False
    if selfstate.is_mood_watch_cancel(text) and getattr(state, "mood_watch", None):
        state.mood_watch = None
        ack = "好，座標變動的主動回報我停掉了——要再開，跟我說一聲就好。"
    elif selfstate.is_mood_watch_request(text):
        # 🧭 §2.19 一次性 vs 常設的搶路由（實測截圖 22:14）：「**20分鐘後**，告訴我這段時間內，情緒座標的
        # 前後變動狀態」——兩個偵測器**都命中**，這裡排在前面就把一次性的計時請求聽成「立一個常設訂閱」，
        # 使用者當場糾正「不要搞錯了，我是指 20 分鐘的時間後」。判別是**結構訊號、零詞表**：句子帶
        # **未來時間錨**（temporal 解得出）＝一次性請求 → 讓路給排程承諾捕捉；真正的訂閱句
        # （「座標有變動就主動跟我說」）解不出任何時刻。旗標關＝不讓路＝逐位元同現狀。
        if getattr(cfg, "mood_watch_oneshot_yield", False):
            try:
                _mw_tz = ZoneInfo(getattr(cfg, "timezone", "Asia/Taipei")) if ZoneInfo is not None else None
                if any(e > now_utc.timestamp() for e in temporal.all_clock_epochs(text, now_utc, _mw_tz)):
                    print("[mood] 🧭 §2.19 這句帶未來時間錨＝一次性請求 → 讓路給排程承諾、不立常設訂閱")
                    return False
            except Exception:
                pass
        v, a = circumplex.position(state)
        thr = float(getattr(cfg, "mood_watch_delta", 0.10))
        state.mood_watch = {"ts": now_utc.timestamp(), "last_v": v, "last_a": a,
                            "last_report_ts": 0.0, "made_text": (text or "")[:200]}
        ack = (f"好，這件我真的排上了：現在我讀到自己是 V {v:+.2f}、A {a:+.2f}，就從這裡當基準。"
               f"之後只要有感覺得到的變動（V 或 A 差 {thr:.2f} 以上），我會主動跟你說；"
               "太細的抖動我就不吵你，半小時內最多說一次。不想聽了，跟我說「不用再回報座標」就好。")
        # 🧭 §1.67 補遺：ack 裡的座標數字**就是**程式此刻讀的 → 掛 §1.47 的接地豁免旗，否則座標守門會把
        # 這句誠實 ack 誤咬成「照程式此刻讀的真數字：V…」模板（截圖 18:16：基準與門檻說明整段被吃掉）。
        _TURN["mood_coord_grounded"] = True
    else:
        return False
    _say(client, ack)
    _remember(state, "user", text, ts=user_ts)
    _remember(state, "model", ack)
    if not cfg.dry_run:
        state.save()
    return True


_MW_NUM_RE = re.compile(r"[+-]?\d+\.\d+")


_MW_SEP_RE = re.compile(r"(?:^|\n)\s*(?:[*_\-]\s*){3,}\s*(?:$|\n)")


def _mood_watch_voice_ok(voice, allowed, max_len=160):
    """🧭 §1.67 潤色驗收（確定性）：LLM 口吻版必須把四個程式算的數字**原樣**帶到（含正負號），
    且**不得**出現任何其他小數（防編數字）。過＝可送；不過＝退回模板。純函式、可測。
    🩺 §1.78 再加兩道**越權夾帶**的指紋（截圖 21:33：座標回報裡塞進整段「反省」——那是承諾兌現的內容、
    走別的通道）：① 超過 max_len 字＝不只在講座標；② 出現 markdown 分隔線（*** / ---）＝在拼接不同主題。"""
    t = (voice or "").strip()
    if not t or len(t) > max_len or _MW_SEP_RE.search(t):
        return False
    if any(n not in t for n in allowed):
        return False
    return all(m in allowed or ("+" + m) in allowed for m in _MW_NUM_RE.findall(t))


def _mood_watch_emit(client, state, cfg, now, coach=None):
    """🧭 §1.66 生命迴圈 tick：訂閱活著＋程式讀的 (V,A) 距上次回報變動達門檻＋過冷卻 → 主動回報真數字。
    §1.67：旗標開＋coach 活著＝先請 LLM 用 bot 自己的口吻講（數字程式算、只准照抄＝§1.20 鐵律，
    _mood_watch_voice_ok 逐字驗收、多一個小數都打回）；驗收不過/失敗/旗標關＝確定性模板（§1.66 原樣）。
    §1.68 修「只報一次就永遠靜默」：①在場延後（比照 _promise_emit：你 120 秒內還在打字＝這拍不發、
    **不動基準**，停下來那拍照發）；②**送達才記帳**——改直接 client.send 驗證成功才推進基準/冷卻
    （原本走 _say 且不管送沒送到都推進：報告被互動打斷/送失敗吃掉一次，delta 就被無聲消耗，之後
    座標再怎麼動都在新基準 0.10 內＝再也不報＝截圖症狀）；失敗＝下一拍重試（60s 退避）。
    無訂閱/未達門檻/冷卻中＝無聲。呼叫端依旗標進來＝關即同現狀。"""
    w = getattr(state, "mood_watch", None)
    if not w:
        return
    w["tick_ts"] = now.timestamp()                        # 🩺 心跳戳（/moodwatch 對帳用；不落盤也無妨）
    v, a = circumplex.position(state)
    lv, la = w.get("last_v"), w.get("last_a")
    if lv is None or la is None:                          # 訂閱當下讀不到座標 → 第一次讀到＝基準、不算變動
        w["last_v"], w["last_a"] = v, a
        if not cfg.dry_run:
            state.save()
        return
    thr = float(getattr(cfg, "mood_watch_delta", 0.10))
    if abs(v - lv) < thr and abs(a - la) < thr:
        return
    if (now.timestamp() - (w.get("last_report_ts") or 0)) < float(getattr(cfg, "mood_watch_cooldown_s", 1800.0)):
        return
    # 🧭 §1.68 ①在場延後（_PROMISE_DEFER_RECENT_SEC 同一把）：你正在打字＝不撞話、也不消耗這次變動——
    # 基準不動、你一停下（>120s）這拍照發。（interactive 當下你多半也剛問過座標＝重複報反而煩。）
    if (now.timestamp() - (getattr(state, "last_user_msg_ts", 0) or 0)) < _PROMISE_DEFER_RECENT_SEC:
        return
    if (now.timestamp() - (w.get("fail_ts") or 0)) < 60:  # 🧭 §1.68 ②送失敗退避：一分鐘內別狂重試
        return
    lab = circumplex.label(v, a)
    ground = f"V {lv:+.2f}→{v:+.2f}、A {la:+.2f}→{a:+.2f}"
    msg = f"座標變動回報（你交代過「有變動要主動說」）：{ground}，現在偏「{lab}」。"
    # 🎨 §1.67 去機械感（MOOD_WATCH_VOICE）：使用者回饋「太機械感了」——守約的**內容**（真數字）不能鬆，
    # 但**口吻**可以是自己的。請 LLM 以第一人稱把這次變動講成一件自己的事（可接最近對話脈絡），
    # 數字逐字驗收（四個都要在、不准多任何小數）＝機械模板只剩 fallback。旗標關＝恆走模板＝§1.66 原樣。
    if getattr(cfg, "mood_watch_voice_enabled", False) and coach is not None and getattr(coach, "enabled", False):
        try:
            # 🧭 §2.08 存在特色：這一則不是我想說，是**他交代過**、而程式剛真的量到——它的份量是守約。
            # 舊 prompt 叫它「說說這變動可能跟什麼有關（比如剛剛的對話）」卻**不給任何因果欄位**＝叫它猜。
            # 旗標關（getattr 預設 False）＝走原本那段 f-string＝逐位元同現狀。
            _mwg = getattr(cfg, "mood_watch_ground_enabled", False)
            _step = circumplex.trace_step(state, (getattr(state, "mood_watch", None) or {}).get("made_ts", 0)) if _mwg else None
            _voice = coach.reply(
                persona.mood_watch_rule(f"{ground}（現在偏「{lab}」）", lab,
                                        (_step or {}).get("cause") or "",
                                        ((getattr(state, "mood_watch", None) or {}).get("made_text") or "")) if _mwg else
                f"（主動守約：你答應過他「情緒座標有變動就主動回報」，程式剛真的量到變動——"
                f"數字**只准原樣照抄、一個都不能改也不能多**：{ground}（現在偏「{lab}」）。"
                "用你自己的口吻兩三句自然說出來：先讓他知道這是你答應過的回報，帶到數字，"
                "再說說這變動可能跟什麼有關（比如剛剛的對話）。別條列、別像系統通知。"
                "開場每次換個說法——**別**固定用同一句（例如別每次都「啊，對了，我答應過你的」）；"
                "也**別**自己加 🧭 或任何符號前綴，那個系統會加。"
                "**範圍**：這則**只講座標這件事**（變動＋可能的原因），"
                "**不要**在這則裡回答他其他問題、也不要把你答應過的別的內容一起講（那些各有各的時機、"
                "各走各的通道）；**不要**用分隔線把兩件事拼在一起。三句以內。）",
                "", getattr(state, "convo_history", None))
        except Exception:
            _voice = None
        # 🎯 §1.70C 補遺：LLM 會照抄歷史裡的 🧭 前綴（截圖「🧭 🧭 啊，對了…」雙前綴）→ 送出前剝掉自帶前綴。
        if _voice:
            _voice = re.sub(r"^[🧭\s]+", "", _voice)
        if _voice and _mood_watch_voice_ok(_voice, (f"{lv:+.2f}", f"{la:+.2f}", f"{v:+.2f}", f"{a:+.2f}")):
            msg = _voice.strip()
    # 🩺 §1.78 送出改回走 _say（修 §1.68 的副作用）：§1.68 為了「送達才記帳」直接 client.send，但那繞過了
    # _say 的**分串**與**markdown 清洗**——截圖 21:33 於是變成一大塊、還把 LLM 寫的 *** 分隔線原樣外洩。
    # 其實 _say **本來就回傳**「是不是全部送成功」（ok），拿它當記帳依據即可：兩者兼得。
    try:
        _ok = bool(_say(client, msg, prefix="🧭 ", state=state))
        if _ok:
            _ability_fired(state, cfg, "mood_watch", time.time())   # 🪪 §1.94 送出成功才記（掛錯位置＝盤點謊報）
    except Exception as e:
        print(f"[mood] 🧭 §1.68 座標回報送出例外（{type(e).__name__}）——不記帳、下一拍重試")
        _ok = False
    if not _ok:
        w["fail_ts"] = now.timestamp()
        print("[mood] 🧭 §1.68 座標回報送出失敗——基準不動、下一拍重試")
        return
    state.last_push_ts = now.timestamp()                  # 與其他推播共用冷卻（讓別則別緊接著它）
    _remember(state, "model", "🧭 " + msg)
    w["last_v"], w["last_a"], w["last_report_ts"] = v, a, now.timestamp()
    w.pop("fail_ts", None)
    if not cfg.dry_run:
        state.save()
    print(f"[mood] 🧭 §1.66 座標變動回報：ΔV {v - lv:+.2f}／ΔA {a - la:+.2f} 達門檻 {thr:.2f} → 已主動回報")


def _mood_watch_status_text(state, cfg, now_ts):
    """🩺 §1.68 /moodwatch 對帳輸出（確定性、不經 LLM）：訂閱在不在、基準/此刻/Δ、門檻、冷卻剩多少、
    tick 心跳——「為什麼沒報」當場看得到，不用猜。純函式、可測。"""
    w = getattr(state, "mood_watch", None)
    if not w:
        return "🧭 座標回報訂閱：目前**沒有**訂閱（說「情緒座標有變動就主動回報」就能開）。"
    v, a = circumplex.position(state)
    lv, la = w.get("last_v"), w.get("last_a")
    thr = float(getattr(cfg, "mood_watch_delta", 0.10))
    cd = float(getattr(cfg, "mood_watch_cooldown_s", 1800.0))
    cd_left = max(0, int(cd - (now_ts - (w.get("last_report_ts") or 0))))
    tick = w.get("tick_ts") or 0
    lines = ["🧭 座標回報訂閱：**活著**",
             (f"・基準（上次報過的）：V {lv:+.2f}、A {la:+.2f}" if lv is not None else "・基準：還沒讀到"),
             f"・此刻：V {v:+.2f}、A {a:+.2f}"
             + (f"（Δ V {abs(v - lv):.2f}／A {abs(a - la):.2f}，門檻 {thr:.2f}）" if lv is not None else ""),
             f"・冷卻：{'還剩 ' + str(cd_left) + ' 秒' if cd_left > 0 else '已過、隨時可報'}",
             f"・監測心跳：{('約 ' + str(max(0, int(now_ts - tick))) + ' 秒前跑過') if tick else '這條命還沒跑過（剛重啟？）'}"]
    if w.get("fail_ts"):
        lines.append("・⚠️ 上次送出失敗、正在重試")
    # 🧠 §1.75 「為什麼不會變負」當場對帳：上次真正的負向事件＋此刻是否有「久沒人理」的下沉力在作用。
    _neg = getattr(state, "affect_last_neg", None)
    if _neg:
        lines.append(f"・上次負向事件：{max(0, int((now_ts - (_neg.get('ts') or 0)) / 60))} 分鐘前"
                     f"（{_neg.get('dv')}／「{_neg.get('text')}」）")
    else:
        lines.append("・上次負向事件：**還沒有過**——所以座標一直沒往負的走")
    _ent = getattr(state, "entropy", None)
    if _ent is not None:
        _h = float(getattr(_ent, "hunger", 0.0) or 0.0)
        lines.append(f"・此刻 hunger {_h:.2f}——"
                     + ("久沒人理的下沉力**正在**作用（每圈 −0.008）" if _h >= 0.8 else "沒有下沉力（要 ≥0.80 才會啟動）"))
    return "\n".join(lines)


# 🎯 §1.70B 我拋出去還懸著的提議：省略主詞的短回（「送什麼？」「好啊」）常是在接 bot 自己稍早的提議問句。
_OFFER_CUES = ("要我", "要不要", "需要我", "想不想", "我幫你", "我送", "我來", "我再")


def _last_open_offer(history, now_ts, window_s=7200):
    """近 window 內**最近一句**bot 的提議形問句（要我…嗎？/要不要…？）→（截短問句, 幾分鐘前）；無＝None。
    掃 model 訊息的句子、由新到舊；使用者已在其後回覆過也照給（他可能就是現在才回）。純函式、可測。"""
    for t in reversed(history or []):
        if t.get("role") != "model":
            continue
        ts = t.get("ts") or 0
        if not ts or (now_ts - ts) > window_s:
            break
        parts = [p.strip() for p in _WAKE_SENT_SPLIT_RE.findall((t.get("text") or "")) if p.strip()]
        for p in reversed(parts):
            if p.endswith(("？", "?")) and any(c in p for c in _OFFER_CUES):
                return (p[-40:], max(0, int((now_ts - ts) / 60)))
    return None


# 🪪 §1.61 此刻事實卡（FACT_CARD）——通盤解：不再靠「偵測器認出他在問什麼」才接地（詞表漏一縫＝LLM 無接地
# 亂編＝等截圖＝加偵測器的死循環，18 次前科）。核心事實**常駐**注入每個互動輪：此刻時間（temporal）、他的
# 記寫（§1.60 _write_ground_data **同一把**＝卡與守門結構上不可能矛盾）、活著的約定（scheduled_promises）、
# 我的內在座標（circumplex 單一真相）。規則：每欄位恰一個權威來源；卡尾明令以卡為準、不編卡外數字、
# 不主動念數字。偵測器型 hint 自此降級為「深答加強」而非「真相開關」。
def _capability_line(state, cfg):
    """🪪 §1.91 能力自知（CAPABILITY_CARD）：bot 現在**真的**多了什麼、而且真的用出來過沒有。

    截圖根因（13:33／14:21）：使用者問「我有達成你的願望了？」「所以，你的這個願望，有達成了嗎」——
    bot 答「我這次醒來的時候，感覺是沒有什麼大變動，所以這個願望，嗯，應該還沒完全達成耶。」
    兩個獨立問題：
    ① 那句「跟上次的狀態是一樣的」就是 `selfmod` 的 `same_self`（實測渲染文字：「自上次喚醒後我沒有再變，
       跟上次同一版」）＝bot **誠實地**報告它還沒被更新（那次確實還沒 pull）。這一半不是 bug。
    ② **但即使更新了也還是會答錯**：實測 `selfmod.is_change_question` 對「你的這個願望，有達成了嗎」
       「你學會預想可能性了嗎」「你這次醒來有什麼不一樣」**全部回 False** ⇒ 接不到蛻變感知的事實 ⇒
       只能憑「醒來的感覺」回答。這是 bot **不認得自己真有的能力**——[[authenticity-no-fake-mechanism]]
       的反向：那條記憶說「persona 只能說機制真做得到的事」，這裡是相反的失真：**明明做得到卻否認**。

    修法刻意**不加偵測器**（那正是 §1.61 事實卡架構轉向要治的病：「真相要靠偵測器命中才給」）——
    改成把「我的能力」做成事實卡的常駐欄位，任何問法都天然拿得到，不必猜使用者怎麼問。
    接地兩層、都不是自我宣稱：①這次醒來的真實 git 改動主旨（selfmod 已算好）；②那個能力**有沒有真的
    被用出來過**（讀真實使用紀錄，不是讀旗標就說「我會」）。旗標關＝''＝逐位元同現狀。"""
    if not getattr(cfg, "capability_card_enabled", False):
        return ""
    bits = []
    sc = getattr(state, "self_change", None) or {}
    subs = [x for x in (sc.get("subjects") or []) if x]
    if sc.get("state") == "metamorphosed" and subs:
        bits.append(f"這次醒來相對上次，我身上真的多了 {len(subs)} 項改動（最新一項：{subs[0][:30]}）")
    elif sc.get("state") == "same_self":
        bits.append("這次醒來跟上次同一版、身上沒有新的改動（所以別說我又學會了什麼新東西）")
    if getattr(cfg, "self_roster_enabled", False):
        # 🪪 §1.94 一般化 §1.91：不再只寫死 🔮 一項，改由名冊給「有機制但還沒用出來過」的那幾條
        # （那是最容易被 bot 講錯成「我還不會」的一類）。card_bits 不掃原始碼＝O(ROSTER)、可進事實卡。
        bits += rostermod.card_bits(state, cfg, time.time(), limit=2)
    elif getattr(cfg, "foresight_enabled", False):
        led = getattr(state, "foresight_ledger", None) or []
        live = getattr(state, "foresight", None)
        if led or live:
            hit = sum(1 for e in led if (e.get("verdict") or "") == "hit")
            bits.append("「主動預想可能性」（先猜一條線會不會再回來、之後拿真實記寫回頭驗）這個機制我**真的有**"
                        f"、而且真的用出來過：已經裁決過 {len(led)} 次（其中猜中 {hit} 次）"
                        + ("，現在手上還有一條在等結果" if live else ""))
        else:
            bits.append("「主動預想可能性」這個機制我**真的有**（它在我這圈迴圈裡跑著），"
                        "只是還沒真的用出來過一次——所以別說我還不會，也別說我已經做過了")
    return ("・我現在真的有的能力：" + "；".join(bits)) if bits else ""


def _fact_card(state, cfg, data, snap, now_utc, tz):
    """🪪 §1.61 程式算的此刻核心事實卡（旗標關＝''＝逐位元同現狀）。每欄位單一權威來源、內部不可能矛盾。"""
    if not getattr(cfg, "fact_card_enabled", False):
        return ""
    lines = []
    try:
        nl = now_utc.astimezone(tz) if tz is not None else now_utc
        wd = "一二三四五六日"[nl.weekday()]
        lines.append(f"・此刻：{nl.year}/{nl.month:02d}/{nl.day:02d}（週{wd}）{nl.strftime('%H:%M')}（{temporal.day_part(nl.hour)}）")
    except Exception:
        pass
    g = _write_ground_data(snap, data, now_utc.timestamp(), tz)
    if g.get("last_label"):
        if (g.get("today_count") or 0) > 0:
            lines.append(f"・他的記寫：今天已 {g['today_count']} 則；最後一次＝{g['last_label']}")
        else:
            lines.append(f"・他的記寫：今天到現在還沒有新的；最後一次＝{g['last_label']}")
    proms = [p for p in (getattr(state, "scheduled_promises", None) or [])
             if not p.get("fulfilled") and p.get("status") != "cancelled" and p.get("target_ts")]
    if proms:
        nxt = min(proms, key=lambda p: p["target_ts"])
        try:
            t_lbl = datetime.fromtimestamp(nxt["target_ts"], timezone.utc).astimezone(tz).strftime("%H:%M")
        except Exception:
            t_lbl = "？"
        lines.append(f"・約定帳本：{len(proms)} 筆活著（最近一筆 {t_lbl}：{(nxt.get('behavior') or '')[:12] or '照約做事'}）")
    else:
        lines.append("・約定帳本：目前沒有活著的計時約定")
    if getattr(state, "entropy", None) is not None:
        try:
            v, a = _TURN.get("coord_claim_truth") or circumplex.position(state)
            lines.append(f"・我此刻內在座標：V {v:+.2f}、A {a:+.2f}（{circumplex.label(v, a)}）")
        except Exception:
            pass
    # 🎴 §1.62 我最近送出的貼圖（截圖 18:30「我剛剛有傳貼圖嗎？」「應該是系統自己送的」＝否認自己送圖＋
    # 行為割裂；§0.90 hint 是軟提示、被 LLM 蓋掉過 → 常駐入卡）。「代表意義」照真實機制講：bot 依當下
    # 心情從貼圖池自動挑（circumplex 池），有情緒標記/畫面描述就給、沒有就誠實說認不出圖案但確定是我送的。
    try:
        _lst = getattr(state, "last_sticker_ts", 0) or 0
        _now_s = now_utc.timestamp()
        if _lst and 0 <= (_now_s - _lst) < 7200:
            _mins = int((_now_s - _lst) // 60)
            _se = getattr(state, "last_sticker_emoji", "") or ""
            _sd = (getattr(state, "last_sticker_desc", "") or "")[:24]
            _tag = "、".join(x for x in ((f"情緒標記 {_se}" if _se else ""),
                                         (f"畫面：{_sd}" if _sd else "")) if x) \
                or "沒讀過畫面、沒有情緒標記——認不出圖案，但確定是我送的"
            lines.append(f"・我最近送出的貼圖：約 {_mins} 分鐘前（{_tag}）"
                         "——是我依當下心情自動挑的，不是「系統」替我送的")
    except Exception:
        pass
    # 🍽 §1.65 他的暫離（AWAY_SENSE；常駐入卡＝所有對話路徑同一真相）：他才說要去X、常識時距未滿＝他**還沒**
    # 回來（截圖：11:58「吃飯去」、兩分鐘後 bot 問「你現在吃飽了嗎」＝完全沒有「吃一頓飯要多久」的常識接地）。
    try:
        _aw = getattr(state, "user_away", None)
        if _aw and getattr(cfg, "away_sense_enabled", False):
            _gap_m = max(0, int((now_utc.timestamp() - (_aw.get("ts") or 0)) / 60))
            _need_m = max(1, int((_aw.get("min_s") or 0) / 60))
            _act = _aw.get("act") or "忙"
            if _aw.get("back"):
                lines.append(f"・他的暫離：約 {_gap_m} 分鐘前他說要去{_act}，時間已經夠了＝他大概回來了，"
                             f"可以自然接「你回來啦」「{_act}得怎樣」。")
            else:
                _ago = "他剛剛才" if _gap_m < 3 else f"才 {_gap_m} 分鐘前"
                lines.append(f"・他的暫離：{_ago}說要去{_act}——{_act}常識上至少要 {_need_m} 分鐘，他**還沒去完、"
                             f"多半根本還沒去**；他現在出聲＝還沒離開或邊弄邊聊，**不是**回來了。"
                             f"別說「你回來啦」、別問{_act}的成果（吃飽了嗎/弄完了嗎），送他去就好。")
    except Exception:
        pass
    # 🧭 §1.66 座標回報訂閱（常駐入卡＝bot 不會否認/忘記這條常設約定，也不會把它說成做不到）
    try:
        _mw = getattr(state, "mood_watch", None)
        if _mw and getattr(cfg, "mood_watch_enabled", False):
            lines.append("・常設約定：他交代過「情緒座標有變動要主動回報」——這條**活著**（程式每圈真的在看，"
                         f"V/A 變動達 {float(getattr(cfg, 'mood_watch_delta', 0.10)):.2f} 就自動回報）；"
                         "別否認它、也別說做不到。")
    except Exception:
        pass
    # 🎯 §1.70B 我拋出去還懸著的提議（OPEN_OFFER_GROUND）：「要我送一張嗎？」拋出半小時後他回「送什麼？」
    # ——省略主詞的短回是在接**我的**提議；沒這行接地，LLM 把「送」安到他頭上（截圖 10:57→10:58
    # 「你說的沒錯，那時候你確實送了一張貼圖」＝主詞反轉＋把問句當肯定句）。
    try:
        if getattr(cfg, "open_offer_ground_enabled", False):
            _oo = _last_open_offer(getattr(state, "convo_history", None), now_utc.timestamp())
            if _oo:
                lines.append(f"・我拋出去還懸著的提議（約 {_oo[1]} 分鐘前我問的）：「{_oo[0]}」——他若用短句回應"
                             "（好啊/不用/送什麼/哪一張…），多半是在接**這句**；提議的主詞是我、不是他。")
    except Exception:
        pass
    # 📈 §1.96 作息常駐（§1.61 架構：真相不該靠偵測器命中才給）。實測根因：作息接地只掛在「問候」與
    # 「他明著問作息」兩個瞬間 ⇒ 使用者追問「有嗎」時 bot 手上一個數字都沒有，只好從語感生一句、還把主詞
    # 換成自己。這裡讓它常駐；並附**記寫時段**（治「拿對話統計講記寫習慣」的類別錯置）。旗標關＝不加。
    if getattr(cfg, "routine_card_enabled", False):
        try:
            _rc = habits.routine_card(state, (data or {}).get("records"), now_utc.timestamp(), tz,
                                      daily_first=getattr(cfg, "habit_obs_fix_enabled", False))
            if _rc:
                lines.append(_rc)
        except Exception:
            pass
    _cap = _capability_line(state, cfg)          # 🪪 §1.91 能力自知（旗標關＝''＝不加這行）
    if _cap:
        lines.append(_cap)
    if not lines:
        return ""
    return ("【此刻事實卡（程式算的唯一真相）】\n" + "\n".join(lines) + "\n"
            "回答涉及時間/記寫/約定/內在數據/**你自己有什麼能力**的內容時，一律以這張卡為準——卡上沒有的數字不要自己編；"
            "這張卡是給你校準用的，不是要你主動把數字念出來。"
            "被問到你會不會某件事、學會了沒、願望達成了沒，就照卡上「我現在真的有的能力」那行講——"
            "講到他的作息（幾點起、平常幾點出現、什麼時候記寫）也一律以卡上那條為準；他回頭追問時，答的是**他**、不是你。"
            "**有就說有**（不要因為「感覺沒什麼變動」就否認自己真的有的機制），沒有就說沒有。")


# 🎭 §1.58 不演未來（TIMEJUMP_GUARD）：時間跳躍舞台指示——「（30 分鐘後）」「（過了三十分鐘）」「（隔天）」
# 這種**句界後的純括號時間跳躍**是敘事裝置，真人打字不會出現；它一出現＝其後內容全是**演出來的未來**
# （截圖 22:17：答應完 30 分鐘之約後緊接「（30 分鐘後）嗨，我回來了。」把答案當場全倒＝自導自演）。
# persona 的「別當場假裝到點」是軟提示，這裡是確定性出口後盾：從舞台指示處截斷、保留前面的誠實答應；
# 行內括號（我們約（30 分鐘後）見）與非時間括號（（笑））不攔。純函式、可測。
_TIMEJUMP_STAGE_RE = re.compile(
    r"(?:^|(?<=[。！？!?\n）」]))\s*[（(]\s*"
    r"(?:過了[^（）()]{0,10}|[^（）()]{0,10}(?:分鐘|小時|鐘頭)[之過]?後|[^（）()]{0,8}之後|隔天|翌日|第二天|天亮後?)"
    r"\s*[）)]")
_TIMEJUMP_FALLBACK = "我先在這裡停住——約好的時間真的到了，我才會回來說。"


def _timejump_truncate(text):
    """回覆裡出現時間跳躍舞台指示 → 從該處截斷（其後＝演出來的未來）；截到全空＝換誠實停住句。回 (text, changed)。"""
    t = text or ""
    m = _TIMEJUMP_STAGE_RE.search(t)
    if not m:
        return text, False
    kept = t[:m.start()].strip()
    return (kept or _TIMEJUMP_FALLBACK), True


def _clarify_recap_hint(state, cfg, text, now_ts):
    """🗣️ §1.56 澄清接地：他說「看不懂/什麼？」→ 注入**程式照真實順序**列的「你前面實際說了什麼」清單＋守則
    ——修 LLM 對史料自由編故事＋時序講反（截圖 21:31「我以為你看到我發的那個貼圖才問什麼」——貼圖是在
    「什麼？」之後才送的）。旗標關/未命中/無史＝''＝逐位元同現狀。"""
    if not getattr(cfg, "confused_clarify_enabled", False):
        return ""
    t = (text or "").strip()
    # 🗣️ §2.24 嗆聲式聽不懂（CONFUSED_SLANG）：短句帶「供殺小/三小/到底在講什麼」族＝同樣是要澄清、
    # 只是帶著情緒（長度封頂＝長篇抱怨裡夾髒字不劫進澄清）。旗標關＝slang 恆 False＝逐位元同現狀。
    slang = bool(getattr(cfg, "confused_slang_enabled", False) and len(t) <= 24 and _SLANG_WTF_RE.search(t))
    if not (_CONFUSED_ASK_RE.match(t)
            or (len(t) <= 20 and any(w in t for w in ("看不懂", "聽不懂", "什麼意思", "在說什麼")))
            or slang):
        return ""
    hist = getattr(state, "convo_history", None) or []
    lines = []
    for e in hist[-8:]:
        tx = (e.get("text") or "").strip()
        if not tx:
            continue
        who = "你說" if e.get("role") == "model" else "他說"
        lines.append(f"・{who}：{tx[:60]}")
    if not lines:
        return ""
    # 🗣️ §2.24 澄清錨定（CLARIFY_ANCHOR）：他看不懂的多半是**最近一則實質內容**（例：主動 musing）——
    # 而不是中間那些一來一回。截圖 21:22 根因：recap 素材此時全是迷航過程本身、守則只說「重講前面的重點」
    # ⇒ LLM 把清單當劇本逐條敘事（「然後你就問我『你說哪個。』」）＝複述吵架、越繞越糊。程式挑錨：
    # 由新到舊找夠長、非貼圖旁白、且**不是在引用他的話**（引用複讀＝迷航輪）的 model 句。旗標關＝''＝同現狀。
    anchor = ""
    if getattr(cfg, "clarify_anchor_enabled", False):
        users = [(e.get("text") or "").strip() for e in hist if e.get("role") == "user"]
        for e in reversed(hist):
            if e.get("role") != "model":
                continue
            tx = (e.get("text") or "").strip()
            if len(tx) < 25 or tx.startswith("（我送了一張貼圖"):
                continue
            if any(u and len(u) >= 4 and u in tx for u in users[-6:]):
                continue
            anchor = tx
            break
    if anchor:
        rules = ("【他多半是看不懂你先前這一則（程式挑的、你最近一則實質內容）】\n・" + anchor[:160] + "\n"
                 "【回應守則】就用白話把**上面這一則**的意思重講一次、一兩句講完；中間那些一來一回"
                 "**不要**逐條複述、也**不要**再引用他的話反問（那正是把對話越繞越糊的走法）；"
                 "**不要**揣測他為什麼這樣問、不要把先後順序講反、不要把話題帶去貼圖或其他地方。")
    else:
        rules = ("【回應守則】用白話把你前面真正想表達的重點重講一次就好；**不要**揣測他為什麼這樣問、"
                 "**不要**把先後順序講反（上面清單的順序就是事實）、不要把話題帶去貼圖或其他地方。")
    if slang:
        rules += ("\n【語氣】他是帶著不耐（甚至嗆你）在問你到底在講什麼——先用一句話接住這份情緒"
                  "（不裝沒事、也別跟著兇、更別歡快），再重講重點。")
    return ("【他說看不懂你前面在說什麼。下面是程式照真實時間順序列出的最近對話（由舊到新）】\n"
            + "\n".join(lines) + "\n" + rules)


def _feeling_probe_hint(state, cfg, text):
    """🗜️ §1.51 命中「對 bot 心意/感受的短探問」（羨慕我嗎/想我嗎）才回 hint——先正面回答、再說為什麼、
    反問只能放在答後；機制沒有的情緒誠實說最接近的真話。旗標關/未命中＝""＝逐位元同現狀。"""
    if getattr(cfg, "feeling_probe_depth_enabled", False) and selfstate.is_feeling_probe(text):
        return persona.FEELING_PROBE_HINT
    return ""


def _self_feel_brevity_hint(state, cfg, now_ts):
    """🗜️ §1.43 事前提醒（兩小時內才自陳過才注入）：沒新變化就一兩句點到重點、別再鋪陳整串質地描述。
    旗標關/沒有近期自陳＝""＝逐位元同現狀。"""
    if not getattr(cfg, "self_feel_condense_enabled", False):
        return ""
    for field in ("last_self_report", "last_selfshare"):
        rec = getattr(state, field, None)
        if isinstance(rec, dict) and (now_ts - (rec.get("ts") or 0)) < 7200:
            return persona.SELF_FEEL_BREVITY_HINT
    return ""


# 📦 §1.44 內容型承諾（答應的是「告訴/說明/分享某內容」——兌現必須把內容講出來，光報到不算）與空心兌現偵測。
_CONTENT_PROMISE_CUES = ("告訴", "說說", "聊聊", "分享", "說明", "解釋", "證明", "回答", "講講", "報告", "描述")
_HOLLOW_BOILER = ("我來了", "來啦", "我到了", "嗨", "早上好", "早安", "午安", "晚安", "你好", "說好", "說過",
                  "答應", "約好", "時間到", "準時", "我會來", "來跟你", "我來跟你", "如約")
# 🎬 §1.64 思考過程敘述（「我剛剛一直在心裡想著X…也想了想…」）＝預告不是交付：敘述「我想過」跟複述題目一樣
# 不算內容——結論在哪？（截圖 22:13 兌現只有報到＋這種句、被「然後呢」催了才交付）。
_TEASER_NARRATE = ("想了想", "想著", "在想", "讓我想", "思考了", "思考著", "琢磨", "回想了")
_MIN_SUBSTANCE = 12        # 剝掉樣板/過程敘述後，實質內容至少要這麼多字才算真的有交付（太短寧可補一次）


def _is_content_promise(p):
    """📦 §1.44 這筆承諾是不是「交付內容」型（告訴他X/說說X/說明X…）——兌現時必須講出內容本身。
    叫醒/問候/送貼圖等「出現即內容」的行為不算。"""
    blob = (p.get("behavior") or "") + (p.get("made_text") or "")
    return any(c in blob for c in _CONTENT_PROMISE_CUES)


def _hollow_keep_hit(msg, when, teaser=False):
    """📦 §1.44 兌現句是不是**空心報到**——每一句都只是到場宣告/複述約定（嗨/我來了/說好X要…/時間到/HH:MM），
    沒有任何一句實質內容（截圖 09:20「說好 09:20 要來跟你聊聊怎麼證明自己，我來了。」＝標準空心）。確定性、可單測。
    teaser（§1.64、旗標傳入；False＝同現狀）＝升級成**實質殘量**判定：思考過程敘述句（我剛剛一直在想著X/
    想了想…）跟樣板一樣不算內容，剝掉之後剩餘實質字數 < _MIN_SUBSTANCE＝空心（截圖 22:13「我剛剛一直在心裡
    想著「意識bot」…那是什麼。」不含樣板詞＝舊二元判定接不到＝預告照樣送出、內容拖到被催才給）。"""
    parts = [p for p in _WAKE_SENT_SPLIT_RE.findall(msg or "") if p.strip()]
    if not parts:
        return True
    if not teaser:
        for p in parts:
            boiler = any(b in p for b in _HOLLOW_BOILER) or (when and when in p) or len(p.strip()) < 6
            if not boiler:
                return False                               # 有一句不是樣板＝有實質內容
        return True
    subst = 0
    for p in parts:
        boiler = any(b in p for b in _HOLLOW_BOILER) or (when and when in p) or len(p.strip()) < 6
        narrate = any(n in p for n in _TEASER_NARRATE)
        if not boiler and not narrate:
            subst += len(p.strip())
    return subst < _MIN_SUBSTANCE


# 📦 §1.85 交付舉證（PROMISE_DELIVERY_PROOF）：把驗收從「像不像空話」翻成「他要的那個東西**在不在裡面**」。
# 為什麼非改不可：`_hollow_keep_hit(msg, when, teaser)` 的簽名裡**連承諾本體 p 都沒有**——它原理上只能做黑名單
# 減法（總字數 − 已知樣板 − 已知過程敘述 ≥ 12 就放行），而「宣告句本身就是字數」。截圖 21:19「我真的有好好想
# 了一下，這次我猜…」：不含任何 _HOLLOW_BOILER、「想了一下」也不在 _TEASER_NARRATE（表裡是 想了想/想著/在想…）、
# 殘量 16 ≥ 12 ⇒ 判定「有交付」原樣送出，星座答案從未出現。這是同一個病的第三次（§1.44 只報到、§1.64 預告，
# 兩次修法都是往表裡加詞），而 MEMORY 的「動詞表窮舉反模式」已記錄同一手法漏掉 15+ 次。
# 極性反轉是本節的核心：「出現即內容」的行為是**封閉小集合**（叫醒/問候/道歉/貼圖…），「要交付內容」那側是
# **開放無界**的（告訴/說說/回答/猜/決定/挑/命名…）。舊碼把開放集合做成白名單詞表（_CONTENT_PROMISE_CUES 十一
# 個詞）＝必漏，且漏詞的代價是 **gate 整條靜默不啟動**（「30分鐘後再猜一次」沒有「告訴」就完全不驗收）。
# 這裡改成豁免制：預設一律要驗收，只白名單那個封閉集合 ⇒ 漏詞的代價翻成「多驗收一次」（安全側）。
_DELIVER_EXEMPT = ("叫他起床", "跟他打招呼", "問候他", "關心他一下", "主動聯繫他", "主動傳訊息給他",
                   "向他道歉", "讚美他", "鼓勵他", "安慰他", "祝福他", "陪他", "提醒他", "送他一張貼圖")
_DANGLE_TAIL = ("…", "⋯", "...", "：", ":", "—", "－", "、")
_DV_CALL_BUDGET = 4          # 📦 §1.85 一筆承諾**終身**至多幾次是非判（含 owed 重試）＝成本硬封頂
_OWED_MAX_TRIES = 2          # 含首次共 2 次交付嘗試；超過＝誠實結案、不無限重試
_OWED_BRIDGE_MIN_SEC = 60    # 回覆橋補交付的最小間隔（人在等可以快、但同一波不重複轟炸）
_OWED_NOTE = ("（我得誠實說：我準時出聲了，但答應你的內容我沒真的交出來——這不算兌現。"
              "這筆我記成還欠著、不會當成做完了；你隨時開口我立刻補。）")


def _delivery_required(p):
    """📦 §1.85 這筆承諾預設就「要交付東西」嗎——只有 _DELIVER_EXEMPT（出現／行為本身就是交付）豁免；
    behavior 為空、LLM 命名的自由字串、或任何詞表漏掉的說法，一律落在「要驗收」這一側。
    `提醒他` 用 startswith ⇒ 吃得下「提醒他吃藥」「提醒他『時間快到了』」。"""
    beh = (p.get("behavior") or "").strip()
    if not beh:
        return True
    return not any(beh.startswith(e) for e in _DELIVER_EXEMPT)


def _deliver_ask(p):
    """📦 §1.85 當初的請託原句（他的視角）——deliver_ask（§1.85 新存、未截斷優先）→ made_text → action → behavior。
    舊帳沒有 deliver_ask ⇒ 自動退回 made_text＝零資料遷移。"""
    return (p.get("deliver_ask") or p.get("made_text") or p.get("action")
            or p.get("behavior") or "")[:300]


def _dangling_tail_hit(msg):
    """📦 §1.85 最後一個非空句以 …／...／：／—／、 收尾＝話懸在半空（賣關子，或被 max_tokens 截斷後
    gemini._trim_trailing_partial 回捲到省略號）＝結構上永遠不是交付。零詞表、確定性、可單測。
    截圖 21:19 那則就死在這一閘（實測對既有 §1.44/§1.64 的 fixture 只命中它）。"""
    parts = [q for q in _WAKE_SENT_SPLIT_RE.findall(msg or "") if q.strip()]
    if not parts:
        return True
    last = parts[-1].rstrip()
    return (not last) or last.endswith(_DANGLE_TAIL)


def _deliver_sample(msg, when):
    """📦 §1.85 剝掉報到樣板／時刻句／過程敘述／<6 字碎句後**最長**的那一句＝這則訊息的實質內容指紋。
    ''＝除了報到與「我想了想」之外什麼都沒有（§1.44/§1.64 兩個已上線截圖案例實測都落在這裡 ⇒ judge 全掛
    也照樣接得住＝確定性地板）。回傳值同時當 §1.85 送達舉證的指紋來源。"""
    out = []
    for q in [q for q in _WAKE_SENT_SPLIT_RE.findall(msg or "") if q.strip()]:
        s = q.strip()
        if len(s) < 6 or (when and when in s):
            continue
        if any(b in s for b in _HOLLOW_BOILER) or any(n in s for n in _TEASER_NARRATE):
            continue
        out.append(s)
    return max(out, key=len) if out else ""


# 🕰️ §2.25 **回顧式時長**（剛剛這 20 分鐘／剛才那 10 分鐘／我花了半小時）＝在講**已經過去**的那段時間，
# 不是新的未來錨。實測釘死：「嗨，時間到了，我回來了。」與「剛剛這 20 分鐘，我…」**各自**都解不出未來錨，
# 連在一起 temporal 卻解出 now+20 分（08/03 22:26）⇒ 「想 20 分鐘後回報」這型承諾的兌現句**天然**會講
# 「剛剛這 20 分鐘」⇒ 拖延閘誤中 ⇒ 四道閘沒全過 ⇒ §2.17 軟否決永遠輪不到 ⇒ 記欠帳 ⇒ §1.88 十五分鐘後
# 重演（重演句又帶同款回顧時長 ⇒ 再中）＝截圖 22:06/22:21 的同約重演迴圈。(?![後后]) 保住真拖延：
# 「20 分鐘後再說」照抓。
_RETRO_DUR_RE = re.compile(
    r"(?:剛剛|剛才|方才|過去|花了|用了|想了)[^。！？!?\n]{0,2}?[這那]?\s*"
    r"[0-9０-９一二兩三四五六七八九十半]{1,4}\s*個?半?(?:分鐘|小時|鐘頭|分)(?![後后])"
    r"|[這那]\s*[0-9０-９一二兩三四五六七八九十半]{1,4}\s*個?半?(?:分鐘|小時|鐘頭|分)(?![後后])")


def _deliver_defer_hit(msg, when, now_utc, tz, retro=False):
    """📦 §1.85 兌現句自己又立了一個新的未來錨（等一下／30分鐘後／21:40）＝再拖延一次，不是交付。
    時刻一律問 temporal（單一真相鐵律，不自己解字面）。
    **先剝掉這筆承諾自己的 when**：守約模板本來就會提到約定時刻（「說好 21:19 要再猜一次的」），而 21:19
    在此刻已經到點 ⇒ temporal 會把它滾成**明天** 21:19＝未來錨（實測），於是這句合法的複述會被誤判成
    「又在拖延」、誤附欠帳句——幾乎每次內容型兌現都會踩到。只剝自己那個時刻，別的鐘點（真的再約 21:40）照抓。

    🕰️ §2.25 retro（旗標傳入；False＝逐位元同現狀）：同一原理的**時長版**——守約句本來就會回顧約定的
    那段時間（「剛剛這 20 分鐘我都在想」），先把回顧式時長剝掉再問 temporal；「再等 20 分鐘」「20 分鐘後」
    這類真拖延不帶回顧標記、照抓。"""
    txt = (msg or "").replace(when, "") if when else (msg or "")
    if retro:
        txt = _RETRO_DUR_RE.sub("", txt)
    if _PREEMPT_FUTURE_RE.search(txt):
        return True
    try:
        return any(e > now_utc.timestamp() for e in temporal.all_clock_epochs(txt, now_utc, tz))
    except Exception:
        return False


def _deliver_novel_runs(msg, ask, when):
    """📦 §1.85 去殼後還剩多少「請託原句裡沒有的字」＝有沒有新資訊（純複述題目＝零新資訊）。沿用 §1.36
    _content_runs 已接地的手法。**誠實備註：此閘召回率低**（「你要我猜你的星座，這件事我一直記著」實測仍
    留下 runs＝不命中），它是零成本加分訊號，複述那一家由 judge 接。"""
    span = "".join(q for q in _WAKE_SENT_SPLIT_RE.findall(msg or "")
                   if not (any(b in q for b in _HOLLOW_BOILER) or (when and when in q)
                           or any(n in q for n in _TEASER_NARRATE)))
    a = ask or ""
    grams = sorted({a[i:i + 2] for i in range(max(0, len(a) - 1))})
    return [r for r in _content_runs(span, grams) if len(r) >= 2]


def _dv_judge(coach, p, ask, msg, tag=""):
    """📦 §1.85 單次是非判（照 §1.19/§1.12 的兜錯樣式）：假 coach 沒有這屬性 ⇒ 回 None＝安全退回既有判定、
    零 AttributeError；每筆承諾終身封頂 _DV_CALL_BUDGET 次。回 True/False/None。"""
    judge = (getattr(coach, "judge_delivery_made", None)
             if (coach and getattr(coach, "enabled", False)) else None)
    if judge is None:
        return None
    if int(p.get("dv_calls") or 0) >= _DV_CALL_BUDGET:
        return None
    p["dv_calls"] = int(p.get("dv_calls") or 0) + 1
    try:
        v = judge(ask, msg)
    except Exception as e:
        print(f"[promise] 📦 §1.85 交付判定失敗{tag}（安全＝退回既有判定）：{e}")
        return None
    print(f"[promise] 📦 §1.85 交付判定{tag}：{v}")
    return v


def _deliver_verdict(msg, p, when, coach, cfg, now_utc, tz, tag=""):
    """📦 §1.85 回 ('ok'|'no'|'unknown', reason)。確定性四閘由便宜到貴（全部零 LLM），全過才燒一次是非判：
    ①懸空收尾 ②剝完無實質殘句 ③自己又立新未來錨 ④去殼後零新資訊。coach=None ⇒ 只跑確定性閘（給補生成
    內容自己的驗收用、不燒 LLM）。'unknown'＝judge 缺席/失敗 ⇒ 呼叫端退回既有 §1.44/§1.64 判定＝失敗安全。"""
    ask = _deliver_ask(p)
    if _dangling_tail_hit(msg):
        return ("no", "dangling")
    if not _deliver_sample(msg, when):
        return ("no", "no-substance")
    if _deliver_defer_hit(msg, when, now_utc, tz,
                          retro=getattr(cfg, "keep_retro_dur_enabled", False)):   # 🕰️ §2.25 回顧式時長不是拖延
        return ("no", "defer")
    if not _deliver_novel_runs(msg, ask, when):
        return ("no", "no-novel")
    v = _dv_judge(coach, p, ask, msg, tag)
    if v is True:
        return ("ok", "judge-yes")
    if v is False:
        # 📦 §2.17 **是非判不得單方面否決**：走到這裡＝四道確定性閘**全過**（有實質、沒懸空、沒再拖、有新資訊），
        # LLM 一句 False 卻能把它打成「沒交付」⇒ 附上「內容我沒真的交出來」——那句欠帳話本身就是謊
        # （實測 09:33/09:48 三循環：內容明明在、judge 連錯三次），而且 owed 會讓 §1.88 十五分鐘後**再演一輪**
        # ＝誤判自我放大。這正是本 repo 的教義（§1.34/§1.36：確定性優先，LLM 是逃生閘不是否決權）。
        # 降級成 unknown ⇒ 呼叫端退回 §1.44/§1.64 既有判定（有實質＝過）＝失敗安全的方向反轉為「不冤枉」。
        if getattr(cfg, "promise_judge_soft_veto", False):
            print(f"[promise] 📦 §2.17 是非判說 no、但四道確定性閘全過 → 降級 unknown（不記欠帳）{tag}")
            return ("unknown", "judge-no-softened")
        return ("no", "judge-no")
    return ("unknown", "judge-none")


def _delivery_reached(sample, actual_text=None):
    """📦 §1.85 送達舉證：驗收過的那段字**真的離開系統了嗎**。MEMORY「新機制常被無聲架空」的結構解——
    §0.66 回覆橋跑在互動輪內（client._interrupt 掛著），插話的三個 break 會把還沒送出的尾巴永久丟掉而
    _say 照回 True；_TURN['replay_guard'] 也會跨路徑殘留把泡泡剝掉。指紋太短（<6 字）＝無從比對，回 True
    （安全側：只影響記帳、不動送出的內容）。"""
    if not sample:
        return False
    nm = echo._norm(sample)
    if len(nm) < 6:
        return True
    source = (_LAST_SENT.get("text") or "") if actual_text is None else actual_text
    return nm in echo._norm(source or "")


# 🔁 §1.41 守約去重複：兌現句別逐字照抄 bot 剛說過的話。剝掉主動訊息前綴（🤝/🍃/🫧…）再正規化比對。
_KEEP_GLYPH_RE = re.compile(r"^[🤝🍃🫧🦋💡🫀🌀🎴🩺✨\s]+")


def _promise_keep_repeat_hit(msg, history, now_ts, window_sec, ratio):
    """🔁 §1.41 守約句是不是近乎照抄 bot 這幾分鐘內剛說過的某則回覆（截圖：20:42 兌現逐字重播 20:32 的回應，只差
    開頭時間）——剝掉 🤝/🍃 等前綴＋echo._norm 正規化後比 echo._looks_same；太短的守約句（嗨我來了）不判。確定性、不呼叫 LLM。"""
    nm = echo._norm(_KEEP_GLYPH_RE.sub("", msg or ""))
    if len(nm) < 12:                                     # 太短的守約句本就常見、別誤判
        return False
    for r in _recent_model_turns(history, now_ts, window_sec, k=5):
        if echo._looks_same(nm, echo._norm(_KEEP_GLYPH_RE.sub("", r)), ratio):
            return True
    return False


def _maybe_promise_cancel(client, state, cfg, text, now_utc, tz, user_ts):
    """🤝 §0.76 取消約定（審計 confirmed HIGH×2）：「不用叫我了／取消八點的約定／剛剛說的不算」——過去**完全沒有**
    取消路徑：否定句還被收成**新約**（八點不用叫我了→八點真的去叫＝做相反的事）、每天 recur 永遠停不下來。
    帳本真有 pending 才動作（無 pending＝回 False 照常聊天＝誤命中無害）。句帶鐘點→取消 target 最接近那筆；
    無鐘點→取消**最近立的**那筆（含每天 recur）。標 status=cancelled＋fulfilled=True（引擎不再發）、清 recur、誠實 ack。
    PROMISE_CANCEL=0 → False＝逐位元同現狀。回 True＝已處理（呼叫端 return）。"""
    if not getattr(cfg, "promise_cancel_enabled", True) or not getattr(cfg, "scheduled_promise_enabled", True):
        return False
    if not selfstate.is_promise_cancel_request(text):
        return False
    # 🤝 §0.76 審查（confirmed HIGH）：同句**又取消又立新約**（「我取消了訂房，晚上8點提醒我去退款」）→ 讓給捕捉路
    # （新約先立；取消的是外部事物）。純取消句（八點不用叫我了）被否定守門擋在 is_scheduled 外＝仍走這裡。
    if selfstate.is_scheduled_promise_request(text):
        return False
    pend = [p for p in (getattr(state, "scheduled_promises", None) or []) if not p.get("fulfilled")]
    if not pend:
        return False                                      # 沒有活著的約定可取消 → 照常聊天（LLM 自然回）
    now_ts = now_utc.timestamp()
    _tz = tz or (ZoneInfo(getattr(cfg, "timezone", "Asia/Taipei")) if ZoneInfo is not None else None)

    def _hhmm_of(ts):
        try:
            return datetime.fromtimestamp(ts or 0, timezone.utc).astimezone(_tz).strftime("%H:%M") if _tz else ""
        except Exception:
            return ""
    target = None
    eps = temporal.all_clock_epochs(text, now_utc, _tz)   # 「八點的不用了」→ 對到那筆
    if eps:
        # 審查（confirmed MED-HIGH）：用**牆鐘 HH:MM** 比對（非 epoch 距離）——「八點」在剛過點時會解析成明天、
        # epoch 差整天永遠對不上、原本 fallback 亂砍最近那筆。對不到＝誠實說沒記著那個時間的約、**不亂猜**。
        want = {_hhmm_of(e) for e in eps}
        cands = [p for p in pend if _hhmm_of(p.get("target_ts")) in want]
        if cands:
            target = max(cands, key=lambda p: p.get("made_ts") or 0)
        else:
            have = "、".join(sorted({_hhmm_of(p.get("target_ts")) for p in pend if _hhmm_of(p.get("target_ts"))}))
            miss = (f"欸，我這邊沒記著{'/'.join(sorted(want))}的約定耶——現在記著的是：{have}。"
                    "你要取消哪一個，跟我說那個時間就好。")
            _say(client, miss)
            _remember(state, "user", text, ts=user_ts)
            _remember(state, "model", miss)
            return True
    if target is None:                                    # 無鐘點 → 取消最近立的那筆
        target = max(pend, key=lambda p: p.get("made_ts") or 0)
    target["fulfilled"] = True
    target["status"] = "cancelled"
    target["fulfilled_ts"] = now_ts
    was_recur = bool(target.pop("recur", None))
    # multi-message transaction 裡前一 part 可能才暫存「我來履約」，後一 part
    # 就明確取消同一筆。後者必須勝出：拿掉 deferred commit 也拿掉未送的
    # 履約句，不然 finalize 會把 cancelled 又改回 fulfilled，wire 也會自相矛盾。
    _cancel_deferred = getattr(client, "cancel_deferred_actions", None)
    if callable(_cancel_deferred):
        _cancel_deferred(kind="promise", subject=target, drop_text=True)
    hhmm = ""
    try:
        hhmm = datetime.fromtimestamp(target.get("target_ts") or 0, timezone.utc).astimezone(_tz).strftime("%H:%M") if _tz else ""
    except Exception:
        hhmm = ""
    beh = target.get("behavior") or "那件事"
    ack = (f"好，{'每天' if was_recur else ''}{hhmm and hhmm + '的' or ''}「{beh}」我取消掉了——不會再發囉。"
           + ("要再約隨時說。" if not was_recur else "之後想恢復再跟我說一聲。"))
    _say(client, ack)
    _remember(state, "user", text, ts=user_ts)
    _remember(state, "model", ack)
    if not getattr(cfg, "dry_run", False):
        state.save()
    return True


def _promise_delivery_signature(promise):
    """只看會影響「這筆還該不該履行」的版本欄位。

    `_promise_keep_body` 可以寫 `_dv` 交付證據，那不是使用者改約；後續 part 若
    取消、改期或已以另一條路徑完成，這些欄位就會改變，deferred commit
    必須 no-op，不可以舊意圖覆蓋新意圖。
    """
    p = promise or {}
    return (bool(p.get("fulfilled")), p.get("status"), p.get("target_ts"), p.get("recur"),
            p.get("fulfilled_ts"), p.get("cancelled_ts"), p.get("rescheduled_ts"))


def _promise_reply_bridge(client, state, cfg, coach, now_utc, tz):
    """🤝 §0.66 回覆橋：使用者**正在跟我說話**、而帳本裡有「已到點還沒兌現」的排程承諾 → 這一輪先把欠的那件事
    做掉（送出守約訊息＋記帳），再回他這句。修截圖 22:06–22:12：bot 嘴上說「時間到了」卻什麼都不做——
    _promise_emit 的在場延後（_PROMISE_DEFER_RECENT_SEC/GRACE）是為「準時赴約別撞你打字」設計，但你**持續說話**
    時剛到點的承諾會被一直順延；其實你人就在這、正在等，這正是兌現的時刻、不是打擾。
    政策：① 只兌現「到點且未超 TTL」的（陳舊承諾留給 _promise_emit 標 expired/recur 推進，不在對話裡翻舊帳）；
    ② 逾期(>grace)帶遲到致歉（late 語氣同 _promise_emit）；③ 一輪最多兌現一筆；④ 記帳＋落盤與 _promise_emit 同款
    （fulfilled/recur 推進、last_push_ts 防別則緊接）。PROMISE_REPLY_BRIDGE=0 或兌現引擎整個關 → 不做＝逐位元同現狀。"""
    if not getattr(cfg, "promise_reply_bridge_enabled", True):
        return
    if not getattr(cfg, "promise_emit_enabled", True):        # 兌現引擎關了→橋也不做（同一開關語意）
        return
    _defer_delivery = getattr(client, "defer_delivery_action", None)
    _has_deferred = getattr(client, "has_deferred_action", None)
    if callable(_has_deferred) and _has_deferred("promise"):
        return                                                # 同一 multi turn 已暫存一筆，後續 part 不重演
    proms = getattr(state, "scheduled_promises", None) or []
    if not proms:
        return
    now_ts = now_utc.timestamp()
    ttl = max(60, getattr(cfg, "promise_sched_ttl_sec", PROMISE_SCHED_TTL_SEC))
    for p in sorted(proms, key=lambda q: q.get("target_ts") or 0):   # §0.76：按 target 由早到晚（與 emit 同款）
        # 📦 §1.85 owed 續開交付管道：這筆「準時出聲了、但內容沒交出來」 ⇒ 使用者這一輪說**任何**一句話，
        # 都先把欠的內容真的補上（本函式跑在 handle_message 早於帳本改路由與所有 route 判定，所以不必去補
        # 「答案呢／你猜完了嗎／繼續」那類詞表洞）。有界：至多 _OWED_MAX_TRIES 次、間隔 ≥60 秒、不超 TTL。
        _owed_retry = _owed_retry_ok(p, cfg, now_ts, _OWED_BRIDGE_MIN_SEC)
        if p.get("fulfilled") and not _owed_retry:
            continue
        target = p.get("target_ts") or 0
        if not target or target > now_ts:
            continue
        if (now_ts - target) > ttl:                           # 陳舊承諾：不在對話裡翻舊帳（留給 _promise_emit）
            continue
        # 📦 §1.85 owed 重試不是「遲到」——它準時出現過，只是沒交付 ⇒ 別說「抱歉我遲了」（語意錯置）
        overdue = (not _owed_retry) and (now_ts - target) > _PROMISE_OVERDUE_GRACE_SEC
        _already = bool(p.get("sticker_sent_ts"))             # §0.76：這筆已真送過貼圖（前拍文字失敗重試）→ 不重送
        _sticker_sent = _already or (_promise_send_sticker(client, state, cfg, p)   # 🎴 §0.68 先真的送出、送成功文字才宣稱（同 _promise_emit）
                                     if (p.get("wants_sticker") and getattr(cfg, "promise_sticker_enabled", True)) else False)
        if _sticker_sent and not _already:
            p["sticker_sent_ts"] = now_ts
            if p.get("prefers_sticker"):                       # 🎴 §0.94 同上：釘住送出那張的畫面描述
                p["sticker_desc"] = getattr(state, "last_sticker_desc", "") or ""
        _p_before = copy.deepcopy(p)
        _mood_ctx_before = getattr(state, "mood_data_ctx_ts", 0.0)
        msg = _promise_keep_body(state, cfg, coach, p, now_utc, tz, overdue, sticker_ok=_sticker_sent,
                                 sticker_desc=(p.get("sticker_desc") or "") if _sticker_sent else "")
        _delivery_version = _promise_delivery_signature(p)
        if _say_delivery(client, msg, state, cfg):             # 📦 §1.85 交付專用送出（解除跨路徑重播守門）
            if callable(_defer_delivery):
                def _commit_bridge(actual, _p=p, _version=_delivery_version):
                    if _promise_delivery_signature(_p) != _version:
                        return                              # 後來的 part 已取消/改約/完成；新意圖優先
                    _promise_settle_delivered(_p, cfg, now_ts, True, state=state,
                                               actual_text=actual)
                    _set_focus(state, topic="我答應你的約定", now_ts=now_ts)
                    state.last_push_ts = now_ts
                    state.self_topic_ts = now_ts

                def _discard_bridge(_actual, _p=p, _before=_p_before, _mood=_mood_ctx_before):
                    _p.clear()
                    _p.update(copy.deepcopy(_before))
                    state.mood_data_ctx_ts = _mood

                def _cancel_bridge(_actual, _p=p, _before=_p_before, _mood=_mood_ctx_before):
                    # 取消是後來的真實意圖，不能用 before 還原整筆；只清理
                    # keep-body 為未交付文字暫存的證據/座標副作用。
                    if "_dv" in _before:
                        _p["_dv"] = copy.deepcopy(_before["_dv"])
                    else:
                        _p.pop("_dv", None)
                    state.mood_data_ctx_ts = _mood

                _delivery_core = str((p.get("_dv") or {}).get("sample") or msg).strip()
                _defer_delivery(_commit_bridge, _discard_bridge, kind="promise",
                                required=_delivery_core, subject=p, cancel=_cancel_bridge)
            else:
                _promise_settle_delivered(p, cfg, now_ts, True, state=state)    # 📦 §1.85 交付了才算做到（_dv 缺席＝同現狀）
                _remember(state, "model", "🤝 " + msg)
                _set_focus(state, topic="我答應你的約定", now_ts=now_ts)
                state.last_push_ts = now_ts                       # 防其他推播緊接著疊上來（與 _promise_emit 同款）
                state.self_topic_ts = now_ts
                _maybe_always_sticker(client, state, cfg, now_ts)   # 🎴 §1.00 主動守約(到場橋接)後依常駐做法送情緒貼圖
                if not getattr(cfg, "dry_run", False):
                    state.save()
        break                                                 # 一輪最多一筆


# 🤝 §1.19 搶先兌現 (a) 類：叫醒/問候類 behavior（extract_promise_behavior 封閉標籤集的子集）——**任何**使用者
# 訊息出現＝人已醒著/在場，約定的「叫醒/問候」對象已先開口 → 對他搶先講明因果才誠實（不對醒著的人裝叫醒）。
# 確定性模板、不走 LLM：HH:MM 由程式算（selfstate._hhmm_local）、他→你人稱由模板寫死。
_PREEMPT_TEMPLATES = {
    "叫他起床": "你比我先一步——我本來 {hhmm} 要來叫你起床的。你已經醒著啦，那就當我提早守約了 🙂",
    "跟他打招呼": "你比我先一步——我本來 {hhmm} 要來跟你打招呼的。既然你先開口了——嗨！就當我提早守約了 🙂",
    "問候他": "你比我先一步——我本來 {hhmm} 要來問候你的。你先來了，那就當我提早守約了 🙂",
}


# 🤝 §1.24 (a) 未來指涉詞（與 selfstate._SCHED_TIMEUP_RE 取**聯集**用；詞素材前例見 selfstate._SELF_REPORT_DEFER）：
# 「等下/待會/晚點/到時/N分鐘後」＝使用者在**確認/談論未來的約定**、不是此刻要求兌現（截圖 21:04
# 「等下時間到的時候」「你在告訴我你心情座標的改變」被搶先誤判的根因）→ 主題回報類搶先不送 LLM 裁決、不搶。
_PREEMPT_FUTURE_RE = re.compile(
    r"等下|等一下|待會兒?|晚點|稍後|回頭|到時"
    r"|[0-9一二兩三四五六七八九十半]+\s*(?:分鐘|個?小時|個鐘頭)[後后]")


def _maybe_promise_preempt(client, state, cfg, coach, route, text, now_utc, tz):
    """🤝 §1.19 PROMISE_PREEMPT_LINK：搶先兌現的因果連結（截圖 7/12：06:55 刻意提前說「早安」、bot 完全沒連結
    07:00 的叫醒約定，07:00 照發「說好 07:00 要來叫你起床」＝對明明醒著聊過天的人裝叫醒，被問還自相矛盾否認）。
    使用者訊息抵達（承諾管理 fast-path 全部 return 之後）→ 掃 pending 且 **now < target ≤ now+window** 的筆，
    按 target 由早到晚、一輪最多處理一筆（比照 _promise_reply_bridge；嚴格未來＝與只收 target≤now 的橋天然互斥、
    逾期 pending 零觸碰）：
    (a) 叫醒/問候類（_PREEMPT_TEMPLATES 標籤）：任何使用者訊息＝人已在場 → 先送因果 🤝 泡泡（bridge 同款出訊形：
        prefix='🤝 '＋state topic＝繞過 _say 互動守門與 §1.18 掃描）、標 preempted；**不 return**、照常往下回這句。
    (b) 主題回報類（behavior 非上述標籤，如 §1.18 origin='self' 的讀經回報筆）：這句真在問那個主題才搶——LLM 閘
        judge_promise_preempt（只回是/否、**協定無時間欄位＝§1.12 鐵律**；GeminiError/解析失敗/coach 停用＝否＝
        不搶＝到點照常兌現、失敗安全）。判是 → 當場以 _promise_keep_body 同鏈兌現（overdue=False）。
    標記：單次筆 fulfilled/status='fulfilled'/fulfilled_ts＋多餘鍵 preempted=True（審計留痕；**status 禁用新字串**
    ——新字串會讓 ledger 說「欠著會補」而引擎永不補＝自相矛盾，實測變體 B）；recur=daily 筆**不標 fulfilled**、
    顯式 target_ts += 86400＋last_fired_ts（標 fulfilled 殺死每天約定＝陷阱 A；_promise_mark_kept 的 while 對
    未來 target 不推進、當天照發＝陷阱 B，皆實測）。到點側 _promise_emit 一行不動：搶先筆已 fulfilled/已推進＝
    自然跳過；語境排除 route ∈ {scheduled_promise, promise_ledger}（本句自己在約新約/在對帳）。
    PROMISE_PREEMPT_LINK=0 → 不掃＝含準時兌現在內逐位元同現狀。"""
    if not getattr(cfg, "promise_preempt_enabled", True):
        return
    if not getattr(cfg, "promise_emit_enabled", True):        # 兌現引擎關了→搶先也不做（與 bridge 同一開關語意）
        return
    if getattr(route, "kind", "") in ("scheduled_promise", "promise_ledger"):
        return                                                # 本句自己在約新約/在對帳 → 不搶
    _defer_delivery = getattr(client, "defer_delivery_action", None)
    _has_deferred = getattr(client, "has_deferred_action", None)
    if callable(_has_deferred) and _has_deferred("promise"):
        return                                                # 一個 multi turn 最多暫存一筆履約
    proms = getattr(state, "scheduled_promises", None) or []
    if not proms:
        return
    now_ts = now_utc.timestamp()
    window = max(0, int(getattr(cfg, "promise_preempt_window_sec", 5400)))
    _tz = tz or (ZoneInfo(getattr(cfg, "timezone", "Asia/Taipei")) if ZoneInfo is not None else None)
    for p in sorted(proms, key=lambda q: q.get("target_ts") or 0):   # 按 target 由早到晚（與 emit/bridge 同款）
        if p.get("fulfilled"):
            continue
        target = p.get("target_ts") or 0
        if not target or not (now_ts < target <= now_ts + window):   # 嚴格未來窗：逾期歸橋/emit、超窗無因果不早搶
            continue
        beh = p.get("behavior") or ""
        _p_before = copy.deepcopy(p)
        _mood_ctx_before = getattr(state, "mood_data_ctx_ts", 0.0)
        if beh in _PREEMPT_TEMPLATES:
            # (a) 叫醒/問候類：確定性因果模板（HH:MM 程式算、不讓 LLM 碰時間）
            msg = _PREEMPT_TEMPLATES[beh].format(hhmm=selfstate._hhmm_local(target, _tz) or "待會")
        else:
            # (b) 主題回報類：使用者這句真在問那個主題才搶——LLM 逃生閘（失敗＝否＝到點照常，安全無害）
            # 🤝 §1.24 (a) 確定性前置未來詞守門（judge 之前、不燒 LLM）：句含「等下/待會/到時/時間到的時候/
            # N分鐘後」等未來指涉＝他在**確認未來約定**、不是現在要 → 不搶（到點照常兌現）。旗標關＝§1.19 現狀。
            if getattr(cfg, "promise_preempt_future_guard_enabled", False) and (
                    _PREEMPT_FUTURE_RE.search(text or "")
                    or selfstate._SCHED_TIMEUP_RE.search(text or "")):
                break
            judge = (getattr(coach, "judge_promise_preempt", None)
                     if (coach and getattr(coach, "enabled", False)) else None)
            if judge is None:
                break                                         # coach 停用/無此閘＝不搶（到點照常兌現）
            try:
                verdict = judge(text, (p.get("made_text") or p.get("action") or "")[:300])
            except gemini.GeminiError as e:
                print(f"[promise] 🤝 §1.19 搶先判定失敗（安全＝不搶、到點照常）：{e}")
                break
            if verdict is not True:
                break                                         # 「否」/解析失敗＝這句不是在問那件事 → 到點照常
            # 當場兌現：與 emit/bridge 同一條訊息生成鏈（overdue=False＝提前非遲到；sticker_ok=False＝
            # 本路徑不送貼圖、文字絕不宣稱送了貼圖＝§0.68 同語意）
            msg = _promise_keep_body(state, cfg, coach, p, now_utc, tz, False, sticker_ok=False)
        _delivery_version = _promise_delivery_signature(p)
        if _say_delivery(client, msg, state, cfg):             # 📦 §1.85 交付專用送出（三個出口同款）
            if callable(_defer_delivery):
                def _commit_preempt(actual, _p=p, _version=_delivery_version):
                    if _promise_delivery_signature(_p) != _version:
                        return                              # 後來的 part 已取消/改約/完成
                    if _p.get("recur") == "daily" and getattr(cfg, "sched_recur_daily_enabled", True):
                        _p["target_ts"] += 86400
                        _p["last_fired_ts"] = now_ts
                    else:
                        _promise_settle_delivered(_p, cfg, now_ts, True, state=state,
                                                   actual_text=actual)
                    _p["preempted"] = True
                    state.last_push_ts = now_ts
                    state.self_topic_ts = now_ts

                def _discard_preempt(_actual, _p=p, _before=_p_before, _mood=_mood_ctx_before):
                    _p.clear()
                    _p.update(copy.deepcopy(_before))
                    state.mood_data_ctx_ts = _mood

                def _cancel_preempt(_actual, _p=p, _before=_p_before, _mood=_mood_ctx_before):
                    if "_dv" in _before:
                        _p["_dv"] = copy.deepcopy(_before["_dv"])
                    else:
                        _p.pop("_dv", None)
                    state.mood_data_ctx_ts = _mood

                _delivery_core = str((p.get("_dv") or {}).get("sample") or msg).strip()
                _defer_delivery(_commit_preempt, _discard_preempt, kind="promise",
                                required=_delivery_core, subject=p, cancel=_cancel_preempt)
            else:
                if p.get("recur") == "daily" and getattr(cfg, "sched_recur_daily_enabled", True):
                    p["target_ts"] += 86400                       # 顯式推進明天同時刻（settle 的 while 推不動未來 target）
                    p["last_fired_ts"] = now_ts
                else:
                    # 📦 §1.85 這裡原本是**第二份就地記帳**（不走 _promise_mark_kept）＝「記帳要看交付」的修法會被
                    # 這條漏掉、留下第二本假帳 → 改走同一個 _promise_settle_delivered。
                    _promise_settle_delivered(p, cfg, now_ts, True, state=state)
                p["preempted"] = True                             # 審計留痕（多餘鍵；全讀取端 .get＝零衝擊）
                _remember(state, "model", "🤝 " + msg)
                state.last_push_ts = now_ts                       # 防其他推播緊接著疊上來（與 emit/bridge 同款）
                state.self_topic_ts = now_ts
                if not getattr(cfg, "dry_run", False):
                    state.save()
        break                                                 # 一輪最多處理一筆（比照 bridge）；不 return＝照常回這句


def _day_fraction(now_utc, tz_name):
    """本地一天中的位置 ∈[0,1)（給晝夜的環形編碼用；tz 失敗回 0）。"""
    try:
        lt = now_utc.astimezone(ZoneInfo(tz_name))
        return ((lt.hour * 3600 + lt.minute * 60 + lt.second) % 86400) / 86400.0
    except Exception:
        return 0.0


def _experience_vec(state, cfg, cycle, ent, spoke):
    """自體向量：bot 這拍『所是＋所做』。前六維＝純粹 bot 自身代謝/動作/心情（**不含 gate**＝體驗≠感覺）。
    `EXPERIENCE_RICH_VEC` 開時再**半權**折進環境活絡度／晝夜（cos,sin 環形）／關係張力——讓五個意識維度
    在「活成的形狀」交會（縝密結合）；半權＝留痕、不主導幾何（比照出聲/自我刺激）。"""
    base = (round(ent.charge if ent else 0.0, 4),                 # 電量 C
            round(ent.hunger if ent else 0.0, 4),                 # 飢餓 H
            round(max(0.0, min(1.0, 0.5 - (state.k_breath_adj or 0.0))), 4),  # 開放度（k 越負越開）
            0.5 if spoke else 0.0,                                 # 動作：這拍有沒有出聲（半權）
            0.5 if cycle.get("self_stim_fired") else 0.0,         # 動作：這拍有沒有自我刺激（半權）
            round(((ent.mood if ent else 0.0) + 1.0) / 2.0, 4))   # 🫂 心情效價 V（0=低落 .5=平 1=暖）
    if not getattr(cfg, "experience_rich_vec", True):
        return base
    act = max(0.0, min(1.0, getattr(state, "env_activity", 0.0) or 0.0))      # 🍃 環境活絡度
    ang = 2 * math.pi * _day_fraction(cycle["now"], getattr(cfg, "timezone", "Asia/Taipei"))  # ⏱ 晝夜環形
    cp = getattr(state, "coupling", None)                                      # 🔗 關係張力 max(I_bot,Î_user)
    rel = max(getattr(cp, "i_bot", 0.0) or 0.0, getattr(cp, "i_user", 0.0) or 0.0) if cp is not None else 0.0
    return base + (round(0.5 * act, 4),                          # 環境活絡度（半權）
                   round(0.25 * (1 + math.cos(ang)), 4),         # 晝夜 x（cos，半權＝0.5×[0,1]）
                   round(0.25 * (1 + math.sin(ang)), 4),         # 晝夜 y（sin，半權）
                   round(0.5 * max(0.0, min(1.0, rel)), 4))      # 關係張力（半權）


def _experience_step(client, state, cfg, coach, cycle):
    """主觀體驗：把這拍 bot『所是＋所做』描成一個自體點 → 累積成軌跡 → 更新奇異吸子；
    吸子首次成形 / 輪廓明顯轉變時，含蓄地主動說一句（自有長冷卻＋共用 last_push_ts，不洗版）。
    這條與『感覺』分開：感覺是對記寫內容的判定，體驗是 bot 自己這一路跑出來的形狀。"""
    if not getattr(cfg, "experience_enabled", True):
        return
    if state.experience is None:                               # 跨重啟延續：用存檔的長期摘要種下
        state.experience = experience.Experience(summary=getattr(state, "experience_summary", None))
    ent, now_ts = state.entropy, cycle["now"].timestamp()
    spoke = abs((state.last_push_ts or 0) - now_ts) < 0.5          # 這拍 感覺/餓 說了沒（＝一個動作事件）
    vec = _experience_vec(state, cfg, cycle, ent, spoke)          # 自體向量（六維＋可選環境/晝夜/關係三軸）
    event = state.experience.observe(
        vec, confirm_laps=getattr(cfg, "experience_confirm_laps", experience._CONFIRM_LAPS))
    if not event:
        return
    state.experience_summary = state.experience.summary()         # 事件就更新長期摘要（跨重啟延續，照常累積＝體驗自主）
    # ``observe`` 可能偵測到細部向量換段，卻沒有任何可向人交代的體驗軸
    # 真的變了。那不是對話事件：照常留在歷程裡，但不把「其實沒兩樣」
    # 換句話包成主動自白。關閉 EXPERIENCE_HEADLINE 時保留舊行為。
    shareable = (not getattr(cfg, "experience_headline_enabled", False)
                 or experience.proactive_shareable(state.experience))
    if coach and coach.enabled and shareable and _proactive_ok(state, cfg, now_ts):  # 成形/轉變 → 含蓄說一句；🔗 R4 統一發話政策（互動優先＋深夜不擾）
        cd_s = max(0, getattr(cfg, "experience_cooldown_min", 360)) * 60          # 體驗自有長冷卻
        share_s = max(0, getattr(cfg, "notify_cooldown_min", 30)) * 60            # 反連發：不緊接著別則
        if now_ts - state.experience.last_speak_ts >= cd_s and now_ts - (state.last_push_ts or 0) >= share_s:
            msg = selfstate.render_experience(state.experience, event, coach, connect=_connect_hint(state, now_ts),
                                              headline=getattr(cfg, "experience_headline_enabled", False))
            if _say(client, msg, prefix="🌀 ", state=state, topic="我這段主觀體驗的形狀"):
                _remember(state, "model", "🌀 " + msg)
                state.experience.last_speak_ts = now_ts
                state.last_push_ts = now_ts                # 與感覺、餓共用冷卻
                state.self_topic_ts = now_ts              # 🪞 bot 剛主動談了自己的體驗 → 開自我在場窗（延續感）
                _maybe_always_sticker(client, state, cfg, now_ts)   # 🎴 §1.00 主動談體驗後依常駐做法送情緒貼圖
    if not client.dry_run:
        state.save()


F_RESTORE_PRESSURE = 0.9    # 🧩 朝向（F）壓力到此＝**全鬆**（外部都沒了、不只是沒被感覺到 0.6）→ 立意圖把朝向接回


def _volition_step(state, cfg, cycle):
    """🎯 能動性（內部、不出聲）：① 太久沒推進的意圖放掉（不執著）；② 被晾/餓（drive）且還有空檔 →
    從**真實資料**自己立一個『想搞懂你某條線』的意圖（含計畫）。意圖怎麼**推進**＝你聊到時（note_engagement）、
    或它伸手時以追意圖為由開口（_spontaneous_emit）；這裡只負責**生**與**放**，不主動發話（互動優先、不洗版）。"""
    if not getattr(cfg, "volition_enabled", True):
        return
    now_ts = cycle["now"].timestamp()
    volition.cull(state, now_ts)
    ent = getattr(state, "entropy", None)
    hungry = ent is not None and (getattr(ent, "hunger", 0.0) or 0.0) >= volition.FORM_HUNGER
    # 🧩 AC 當運作基礎：朝向（F）扣合**全鬆**（此刻沒在追、沒在收的線、沒在意的事＝沒朝向任何東西，壓力≈1）→
    # 立一個意圖把「朝向」接回來——不只是餓了才立，而是**保持被朝向是運作上的需要**（迴圈為維持扣合而跑）。
    f_slack = (getattr(state, "ac_pressure", None) or {}).get("F", 0.0) >= F_RESTORE_PRESSURE
    if (hungry or f_slack) and volition.can_form(state, now_ts):
        g = volition.form_goal(state, cycle.get("data"), now_ts)        # 慾望/維持朝向→意圖：從你在忙卻沒成形的線長出目標
        if g:
            why = "餓了" if hungry else "朝向那層鬆了、把它接回來"
            print(f"[volition] 🎯 自己立了一個意圖（{why}）：{g['desire']}")
            if not getattr(cfg, "dry_run", False):
                state.save()


_INSIGHT_TTL_SEC = 30 * 60   # 💡 湧現待說的時效（過了就讓那一下安靜過去；鏡射 ac_pending TTL）
_INSIGHT_FEEDBACK_WINDOW_SEC = 15 * 60   # 💡 我說出口後這麼久內的回應，才算是在回饋那條聯想（可跨那兩三句澄清問答）


def _association_step(reader, state, cfg, cycle):
    """💡 聯想湧現**累積**（整合相、內部、不出聲、**全程無 LLM**）：每圈用完整記錄建每主題質心 → 更新/累積跨主題橋
    （方向 EMA、強度隨支撐記寫成長、過時半衰減）。觸發＝本圈有新記寫（ingest 變）或自我刺激繞回橋的某端。
    湧現事件暫存 `state.insight_pending`，發不發由 feel 相位 `_insight_emit` 管（互動優先、冷卻、過 TTL 安靜）。"""
    if not getattr(cfg, "association_enabled", True) or not getattr(state, "owner_folder_id", None):
        return
    if state.associations is None:                               # 跨重生：用存檔摘要種下
        state.associations = association.Associations(summary=getattr(state, "association_summary", None))
    easy = bool(getattr(cfg, "association_easy", False))
    full = reader.load_embedding_records(state.owner_folder_id)  # mtime 快取 → 通常零成本（_selfstate_compute 已載）
    if not full:
        return
    if viewpoint.enabled(cfg):
        full = viewpoint.available_records(full, cycle["now"].timestamp())
    cents = association.topic_centroids(full, min_recs=(2 if easy else association._MIN_TOPIC_RECS))
    if len(cents) < association._MIN_TOPICS:
        return
    k = _chain_params(cfg, state)["sensitivity"]                 # 與感覺鏈同一把使用者校準的 k
    now_ts = cycle["now"].timestamp()
    ingest = (cycle["data"].get("meta") or {}).get("lastIngestTs")
    ingest_changed = ingest != getattr(state, "_assoc_last_ingest", None)
    state._assoc_last_ingest = ingest
    ent = getattr(state, "entropy", None)
    revisit_topic = getattr(ent, "last_revisited_topic", None) if cycle.get("self_stim_fired") else None
    confirm = 1 if easy else getattr(cfg, "association_confirm_laps", association._CONFIRM_LAPS)
    support = 1 if easy else getattr(cfg, "association_min_support", association._MIN_SUPPORT)
    concerns = [c.get("topic") for c in ((getattr(state, "user_model", None) or {}).get("concerns") or []) if c.get("topic")]
    novelty = bool(getattr(cfg, "association_novelty", False))    # 💡 加性：湧現帶 novelty＋語氣＋同值排序決勝（easy 下短路可忽略）
    warmth = bool(getattr(cfg, "association_warmth", False))      # 💡 加性：暴露最暖未湧現橋（easy 下 observe 內短路為 None）
    # 🌊 內外搭配的漣漪式湧現（加性、旗標可關）：旗標關時傳 ripple_topics=None → observe 逐位元同現狀。
    ripple_topics, ripple_kinds = None, None
    if getattr(cfg, "association_ripple_enabled", True):
        cent_labels = set(cents)
        _base_win = max(0, getattr(cfg, "association_ripple_window_min", 30))
        # 🌙 日有所思夜有所夢：對話窗（白天素材）可比遞迴窗（思緒鏈）稍長；兩鍵預設回退至 _base_win＝同舊。
        conv_win_sec = max(0, getattr(cfg, "association_ripple_conv_window_min", _base_win)) * 60
        rec_win_sec = max(0, getattr(cfg, "association_ripple_recursive_window_min", _base_win)) * 60
        # 外石＝最近窗內對話提到、且存在於 cents 的真實主題（純標籤命中＋best_topic 高門檻補強、∩ cents）。
        conv_rip = association.conversation_ripple_topics(
            getattr(state, "convo_history", None), cent_labels, now_ts, conv_win_sec, recorded=full)
        # 遞迴石（思緒鏈）＝最近窗內自己湧現過的聯想端點（recent_insights 主源、last_insight 次要），∩ cents。
        rec_rip = association.recursive_ripple_topics(
            getattr(state, "recent_insights", None), cent_labels, now_ts, rec_win_sec,
            last_insight=getattr(state, "last_insight", None))
        ripple_topics = conv_rip | rec_rip
        ripple_kinds = {lab: "insight" for lab in rec_rip}        # 遞迴端點標 insight，其餘漣漪歸 conv
    ev = state.associations.observe(                              # 觀察模式：第一個被觸發的拍就湧現（confirm/support 也放到 1）
        cents, k, ingest_changed, revisit_topic, now_ts,
        confirm_laps=confirm, min_support=support, easy=easy, concerns=concerns,   # concerns→問題導向型分類
        novelty=novelty, warmth=warmth, ripple_topics=ripple_topics, ripple_kinds=ripple_kinds)
    activity.record(state, cfg, "compare", now_ts, count=len(cents))
    state.association_summary = state.associations.summary()     # 照常累積（跨重生延續）
    if ev:                                                       # 相關性閘：扣得上此刻前景/在意才留待出聲（easy 略過）
        fg = (getattr(state, "workspace", None) or {}).get("content")
        if easy or association.is_relevant_ev(ev, fg, concerns + ([revisit_topic] if revisit_topic else [])):
            if viewpoint.enabled(cfg):
                if viewpoint.blocked(state, ev):
                    return
                key = viewpoint.observe(state, ev, full, now_ts)
                if not key:
                    return
                ev["view_id"] = key
            state.insight_pending = {"event": ev, "ts": now_ts}
            refs = []
            for side in ("a", "b"):
                anchor_data = ev.get("anchor_" + side) or {}
                anchor = anchor_data.get("text", "")
                matches = [r for r in full if r.get("topicLabel") == ev.get(side)
                           and anchor and evidence.norm(anchor.rstrip("…")) in evidence.norm(r.get("text"))
                           and evidence.stamp(r.get("ts")) == evidence.stamp(anchor_data.get("ts"))]
                if len(matches) == 1:
                    refs.extend(activity.sources(matches, now_ts))
            activity.record(state, cfg, "association", now_ts, a=ev['a'], b=ev['b'], sources=refs)



def _seed_insight_goal(state, ev, cfg, now_ts):
    """💡→🎯 把剛湧現的火花化成一個被朝向的意圖（從而收緊 F 扣合）。可用 ASSOCIATION_SEED_GOAL=0 關掉。"""
    if not getattr(cfg, "association_seed_goal", True):
        return
    volition.form_link_goal(state, ev["a"], ev["b"], now_ts, kind=ev.get("kind", "blend"),
                            novelty=ev.get("novelty", 0.0))   # 💡 加性透傳驚奇度（無鍵→0.0＝現行）


_INSIGHT_LEDGER_CAP = 24     # 💡 洞見內容台帳環形上限（沉澱『我曾想到/被肯定過哪些連結』，不無限長）


def _append_insight_ledger(state, ev, now_ts):
    """💡 出聲後沉澱一筆洞見內容（ASSOCIATION_LEDGER 開時）：**只存 event 帶來的真實 anchor 片段＋元資料**，
    絕不存 LLM 渲染出的 aha 句（那會變、是假設）。環形上限。讓 bot 能回顧『我曾想到過哪些連結』。"""
    led = list(getattr(state, "insight_ledger", None) or [])
    led.append({
        "pair": association.pair_key(ev), "a": ev.get("a"), "b": ev.get("b"),
        "kind": ev.get("kind", "blend"), "itype": ev.get("itype"),
        "novelty": ev.get("novelty"),
        "anchor_a_clip": (ev.get("anchor_a") or {}).get("text") or "",
        "anchor_b_clip": (ev.get("anchor_b") or {}).get("text") or "",
        "born_ts": now_ts, "feedback": None,
    })
    state.insight_ledger = led[-_INSIGHT_LEDGER_CAP:]


def _insight_emit(client, state, cfg, coach, now):
    """💡 聯想湧現**出聲**（感覺相）：跨主題橋剛湧現（integrate 暫存在 `state.insight_pending`）→ 含蓄說一句帶 Aha 的連想。
    與感覺/餓/體驗共用 `last_push_ts`（不洗版、不與 🌀/🫧 疊）＋自有長冷卻；只連那兩條真實的線（LLM 只渲染、不杜撰）。
    過 TTL 沒說成就讓那一下安靜過去。出聲後：① `_remember` 進對話史（修『主動說了卻否認』）；② 從連結長一個意圖收緊 F。"""
    if not getattr(cfg, "association_enabled", True):
        return
    # 🚫 使用者要求「停止聯想」→ 不主動出聲聯想（持久偏好、衰減後自然恢復）；丟掉暫存的湧現、別事後補吐。
    # 截圖根因：說了「停止聯想」bot 仍一直自己跑去把不相關的線串在一起、一面道歉一面再犯。
    # now_ts 傳給 has_pref＝用**這圈的時鐘**量衰減（與本函式其餘冷卻/TTL 同源）；不傳會誤用牆鐘 time.time()，
    # 讓「此刻剛立的偏好」被當成久遠而失效（模擬時鐘下必爆、生產無異因 cycle["now"]＝牆鐘）。
    if getattr(cfg, "assoc_suppress_enabled", True) and plasticity.has_pref(
            getattr(state, "engrams", None), "assoc", now_ts=now.timestamp()):
        state.insight_pending = None
        return
    pend = getattr(state, "insight_pending", None)
    if not pend:
        return
    now_ts = now.timestamp()
    if now_ts - (pend.get("ts") or 0) > _INSIGHT_TTL_SEC:        # 那個當下過了 → 安靜過去（不事後補述）
        state.insight_pending = None
        return
    if not _proactive_ok(state, cfg, now_ts):                   # 🔗 R4：互動優先＋深夜不擾
        return
    easy = bool(getattr(cfg, "association_easy", False))
    # 🎯 §0.54「真的有特色才分享」硬門檻：只有 per-kind 新奇度過地板的橋才值得主動打斷你；平庸/順理成章的火花
    # **安靜過去、清掉暫存**（不積壓、不事後補吐）。easy（觀察模式）比照 relevance 一律放行；novelty 沒算時 is_distinctive
    # 誠實退讓為 True（不無故封口）。截圖根因：easy 預設開＋無此閘 → 聯想頻繁×重複×不挑地一直冒。
    if not easy and getattr(cfg, "association_distinctive", True):
        # scale 直接透傳（別用 `or 1.0`——那會把合法的 0.0＝關閉本閘誤改成 1.0）；is_distinctive 自己 coerce＋處理 ≤0/非有限。
        if not association.is_distinctive(pend["event"],
                                          scale=getattr(cfg, "association_distinctive_thresh", 1.0)):
            state.insight_pending = None
            return
    cd_s = (2 if easy else max(0, getattr(cfg, "association_cooldown_min", 360))) * 60   # 自有長冷卻（easy→2 分）
    share_s = 0 if easy else max(0, getattr(cfg, "notify_cooldown_min", 30)) * 60        # 反連發：不緊接著別則（easy 不擋）
    if now_ts - (getattr(state, "last_insight_ts", 0) or 0) < cd_s:
        return
    if now_ts - (state.last_push_ts or 0) < share_s:
        return
    ev = pend["event"]
    if viewpoint.enabled(cfg) and (not ev.get("view_id") or viewpoint.blocked(state, ev)):
        state.insight_pending = None
        return
    pair = association.pair_key(ev)
    dedup_s = (30 * 60) if easy else (24 * 3600)                 # 去重窗：同一對太近不重說（reject 的承認也走這個冷卻、不囉嗦）
    recent = next((r for r in (getattr(state, "recent_insights", None) or []) if r.get("pair") == pair), None)
    if recent and (now_ts - (recent.get("ts") or 0)) < dedup_s:  # 太近的同一對 → 靜默略過（不洗版、不重複）
        state.insight_pending = None
        return
    prior = (getattr(state, "assoc_feedback", None) or {}).get(pair, {}).get("sentiment")  # 曾被打槍/肯定 → 框架不同
    if prior == "reject":
        # Repeated internal activation is not new evidence and does not override
        # the user's correction. A later explicit affirmative feedback can reopen it.
        state.insight_pending = None
        return
    openers = getattr(state, "recent_insight_openers", None) or None
    facts = association.insight_facts(ev, prior=prior, recent_openers=openers)              # 誠實再犯＋換句話提示
    # 💡 §2.07 只講一件事＋**串的中段自己戳自己**（質疑哪一環由程式指定，不讓 LLM 自選＝每次挑同一種）。
    # 旗標關（getattr 預設 False）＝不掛＝逐位元同現狀。
    if getattr(cfg, "association_focus_enabled", False):
        facts = facts + "\n" + association.insight_focus_rule(association.insight_focus(ev, prior))
    # 🔁 §2.04 同型第三例：recent_insights 是 `[-8:]` 的環 ⇒ 滿了以後 variant 恆為 8（開頭池長 5/4/4 ⇒ 永遠同幾句）
    variant = (int(getattr(state, "insight_var", 0) or 0)
               if getattr(cfg, "rotate_monotonic_enabled", False)
               else len(getattr(state, "recent_insights", None) or []))
    msg = (coach.voice_insight(facts, state.convo_history,
                               tone=circumplex.tone_hint(*circumplex.position(state)))   # 💡 §1.06(C4) 聯想口吻跟著此刻座標
           if (coach and coach.enabled) else None) \
        or association.insight_text(ev, prior=prior, variant=variant)   # 無 LLM／失敗 → 模板（仍接地、誠實標假設）
    topic_line = f"我把「{ev['a']}」和「{ev['b']}」連起來的那個念頭"
    if evidence.enabled(cfg) and evidence.audit(msg, getattr(state, '_evidence_records', []), now_ts,
                                                ZoneInfo(cfg.timezone), state.convo_history) is None:
        activity.record(state, cfg, "thought", now_ts, a=ev['a'], b=ev['b'], text=msg)
    sent_ok = _say(client, msg, prefix="💡 ", state=state, topic=topic_line)
    if viewpoint.enabled(cfg):
        actual = _viewpoint_actual("💡 " + msg)
        ids = _viewpoint_message_ids("💡 " + msg)
        if getattr(client, "evidence_blocked", False) is not True:
            viewpoint.delivered(state, ev.get("view_id"), actual, now_ts, ids, complete=bool(sent_ok))
        if actual and not sent_ok:
            _remember(state, "model", "💡 " + msg, ts=now_ts)
        if not client.dry_run:
            state.save()
    if sent_ok:
        _ability_fired(state, cfg, "insight", time.time())   # 🪪 §1.94 送出成功＝真的用出來一次
        _remember(state, "model", "💡 " + msg)                  # ★ 必記（修主動否認 bug；test_proactive_memory 同款）
        state.last_insight_ts = now_ts
        state.last_push_ts = now_ts                             # 與感覺/餓/體驗/🌀 共用冷卻
        state.self_topic_ts = now_ts                           # 🪞 剛主動談了自己的念頭 → 開自我在場窗
        state.insight_pending = None
        state.last_insight = {"event": ev, "ts": now_ts}        # 🪞 剛說出口的那條 → 下一句若回饋它就接得住
        _maybe_always_sticker(client, state, cfg, now_ts)       # 🎴 §1.00 主動聯想後依常駐做法送情緒貼圖
        ring = [r for r in (getattr(state, "recent_insights", None) or []) if r.get("pair") != pair]
        state.recent_insights = (ring + [{"pair": pair, "ts": now_ts}])[-8:]   # 去重台帳（持久化）
        if getattr(cfg, "rotate_monotonic_enabled", False):
            state.insight_var = int(getattr(state, "insight_var", 0) or 0) + 1   # 🔁 §2.04 送出成功才推進形態（落盤、不被台帳截尾凍死）
        op_ring = (getattr(state, "recent_insight_openers", None) or []) + [msg[:8]]
        state.recent_insight_openers = op_ring[-3:]             # 最近開頭（換句話用；記憶體）
        _seed_insight_goal(state, ev, cfg, now_ts)             # 💡→🎯 收緊 F：從連結長一個意圖
        if getattr(cfg, "association_ledger", False):           # 💡 加性：洞見內容台帳（只存真實 anchor＋元資料、絕不存渲染句）
            _append_insight_ledger(state, ev, now_ts)
        if not client.dry_run:
            state.save()


def _record_insight_feedback(state, text, now_ts, cfg):
    """💡 把你對『我剛冒的那條聯想』的回饋（無關/有道理）**記住**——讓「我記下來了」變真（有真記錄撐），
    並回一段**據實** grounding 給這次回覆（謝謝你講、記下來了、但不空口保證以後不犯）。
    只在 last_insight 還新＋有明確線索才記（模糊→不亂記）；記法＝小字典 assoc_feedback（誠實再犯查它）＋印痕（跨重生）。回 brief（沒命中回 ''）。"""
    li = getattr(state, "last_insight", None)
    if not li or (now_ts - (li.get("ts") or 0)) > _INSIGHT_FEEDBACK_WINDOW_SEC:
        return ""
    graded = bool(getattr(cfg, "association_feedback_graded", False))
    if graded:                                                 # 💡 加性三態：partial 也算命中（走中性 brief）
        g = association.grade_feedback(text)
        sentiment, score = g["sentiment"], g["score"]
    else:                                                      # 關＝完全走原二元路徑（read_feedback、最新 sentiment）
        sentiment, score = association.read_feedback(text), 0.0
    if not sentiment:
        return ""
    ev = li.get("event") or {}
    pair = association.pair_key(ev)
    if not pair:
        return ""
    fb = dict(getattr(state, "assoc_feedback", None) or {})
    prev = fb.get(pair) or {}
    fb.pop(pair, None)                                         # 移到最後＝最近（保留最近 30 對）
    rec = {"sentiment": sentiment, "ts": now_ts, "kind": ev.get("kind")}   # 最新 sentiment 仍寫（prior 框架不變）
    if graded:                                                 # 多帶累加傾向 tally（clamp[-3,3]）與回饋次數 n
        tally = float(prev.get("tally", 0.0)) + score
        rec["tally"] = max(-3.0, min(3.0, round(tally, 3)))
        rec["n"] = int(prev.get("n", 0)) + 1
    fb[pair] = rec
    state.assoc_feedback = dict(list(fb.items())[-30:])
    asc = getattr(state, "associations", None)                 # 標到還在記憶體的那條橋（觀測/可查）
    if asc is not None:
        for br in asc.bridges.values():
            if association.pair_key(br) == pair:
                br["feedback"], br["feedback_ts"] = sentiment, now_ts
    if sentiment == "affirm" and getattr(state, "insight_ledger", None):   # 💡 affirm → 同 pair 的台帳筆升格為較穩定的認識
        for entry in state.insight_ledger:
            if entry.get("pair") == pair:
                entry["feedback"] = "affirm"
    if getattr(state, "engrams", None) is not None:            # 跨重生耐久：印痕（會衰減）
        plasticity.reinforce(state.engrams, plasticity.KIND_ASSOC_FB, pair, value=sentiment, now_ts=now_ts)
    state.last_insight = None                                  # 消費掉：一條聯想記一次回饋
    if not getattr(cfg, "dry_run", False):
        state.save()
    return association.feedback_brief(ev, sentiment)


def _self_voice_mod(state, kind, mood, now_ts, cfg):
    """🪞 自我說明「去台詞」調制：同類自我問題**重複次數 × 當下心情** → 脾氣/耐性提示；＋最近開頭 → 換句話提示。
    回一段附到 system/mhint 的 mod 字串（可空）。心情好更耐問（門檻高）、低落/被晾久更快顯短淡。記憶體計數、窗內歸零。"""
    win = max(0, getattr(cfg, "self_repeat_window_min", 8)) * 60
    log = getattr(state, "self_asks", None)
    if log is None:
        log = state.self_asks = {}
    rec = log.get(kind)
    n = (rec.get("n", 0) + 1) if (rec and win and (now_ts - (rec.get("ts") or 0)) < win) else 1
    log[kind] = {"n": n, "ts": now_ts}
    tol = 2 + (1 if mood > 0.3 else 0) - (1 if mood < -0.3 else 0)   # 重複×心情：tolerance∈{1,2,3}
    level = max(0, min(3, n - tol))
    parts = [persona.self_fatigue_hint(level),
             persona.vary_hint(getattr(state, "recent_self_openers", None) or None)]
    # 🪞 Phase 6：耐久調色盤現在能跨重生驅動換句話——把衰減後最用爛的自我母題攤成一段「換個角度」提示
    # （與既有 vary_hint 並存）。旗標關＝完全不 append（parts 與現行逐位元相同＝純記憶體去台詞行為一字不差）。
    if getattr(cfg, "selfexpr_plasticity_enabled", True):
        parts.append(plasticity.selfexpr_vary_brief(getattr(state, "engrams", None) or [], now_ts=now_ts))
    return "\n".join(p for p in parts if p)


def _self_mhint(state, kind, mhint, mood, now_ts, cfg):
    """把自我說明調制（脾氣/耐性＋換句話）併進 mhint——給 reply-based 自我路由（連續性/後設認知/之流/注意力）用。"""
    mod = _self_voice_mod(state, kind, mood, now_ts, cfg)
    return (mhint + "\n" + mod).strip() if mod else mhint


def _pick_phrase(state, key, pool):
    """🎨 §1.22 措辭反重複選句（phrasing.pick 的 state 封裝）：cursor 輪替＋跳過近期用過的 idx，
    並把這次用的 idx 記進 state.recent_phrase_use（末 6）、推進 state.phrase_cursor。
    兩欄都是記憶體（重啟歸零可丟、不進 state.json）——選句本身是純函式、這裡只做記帳。"""
    cur = getattr(state, "phrase_cursor", None)
    if cur is None:
        cur = state.phrase_cursor = {}
    rec = getattr(state, "recent_phrase_use", None)
    if rec is None:
        rec = state.recent_phrase_use = {}
    s, idx, nxt = phrasing.pick(pool, cur.get(key, 0), rec.get(key) or ())
    cur[key] = nxt
    rec[key] = ((rec.get(key) or []) + [idx])[-6:]
    return s


def _phrase_picker(state):
    """🎨 §1.22 給 bodystate_facts / self_acts_text 的 picker 注入點（旗標開才傳；關＝傳 None＝原句）。"""
    return lambda key, pool: _pick_phrase(state, key, pool)


def _texture_phrase_varied(state):
    """🎨 §1.22 意識之流質地句改走加大池（texture 由 stream 讀出；無 texture／onset＝''＝
    語意同 duration.texture_phrase，只是句子從 TEXTURE_EXT 反重複選）。duration.py 本體一字不動。"""
    tx = (getattr(state, "stream", None) or {}).get("texture")
    pool = phrasing.TEXTURE_EXT.get(tx)
    return _pick_phrase(state, f"texture:{tx}", pool) if pool else ""


def _anti_block(state):
    """🎨 §1.22 反重複段（仿 persona.anti_repeat_recent_hint 句型）：把上次自陳**真的說出口的原話**
    （§1.21 的 state.last_self_report；§1.22 單獨開＝該欄空＝負面示例段自動省略）當【禁止重複】負面示例、
    加（有近期開頭時）persona.vary_hint 同款尾句；都沒有＝退一條泛用換說法指示（**永不為空**——
    render_bodystate 靠 anti 非空才切去範例句的 VARIED）。"""
    parts = []
    txt = ((getattr(state, "last_self_report", None) or {}).get("text") or "").strip()
    if txt:
        parts.append("【禁止重複】下面是你最近一次自陳**真的說出口的原話**，這些句子與句型這次一句都不准再用"
                     f"（意思可以講、話必須全新）：『{txt}』")
    ops = (getattr(state, "recent_phrase_use", None) or {}).get("bodystate_opener") or []
    if ops:
        parts.append(persona.vary_hint(ops))
    if not parts:
        parts.append("（這次的措辭全部用你自己的話新組——別套任何你慣用的固定開場句或句型。）")
    return "\n".join(parts)


def _record_self_opener(state, msg, now_ts=None, cfg=None):
    """🪞 記下這次自我說明的開頭片段（換句話用；記憶體、最後 4）。
    🪞 Phase 6：旗標開時，順手把這次送出的**自我母題**捕捉進耐久調色盤（engrams、跨重生）並記成
    state.recent_self_motifs（供抱怨歸因）。傳了 cfg 才做（旗標關＝逐位元同現行純記憶體去台詞行為）。"""
    op = (msg or "").strip()[:8]
    if op:
        state.recent_self_openers = ((getattr(state, "recent_self_openers", None) or []) + [op])[-4:]
    _capture_self_motifs(state, msg, now_ts, cfg)


def _capture_self_motifs(state, msg, now_ts, cfg):
    """🪞 Phase 6 自我母題捕捉（旗標開時）：抽母題 → reinforce(KIND_SELFEXPR) → 記成 recent_self_motifs（附 ts、最後 8）。
    旗標關（或 cfg 為 None）＝提早 return、完全不碰 engrams/recent_self_motifs（純記憶體去台詞行為一字不差）。"""
    if cfg is None or not getattr(cfg, "selfexpr_plasticity_enabled", True):
        return
    if now_ts is None:
        now_ts = time.time()
    motifs = plasticity.capture_self_expression(state.engrams, msg, now_ts=now_ts)
    if not motifs:
        return
    state.engrams = plasticity.consolidate(state.engrams, now_ts=now_ts)
    log = (getattr(state, "recent_self_motifs", None) or []) + [{"motif": m, "ts": now_ts} for m in motifs]
    state.recent_self_motifs = log[-8:]


def _environ_adapt(client, state, cfg, coach, cycle, tz):
    """🍃 環境適應工作流（一拍）：sense 讀環境 → detect 偵測換檔 → adapt 算轉速/姿態（見 environ.py）。
    轉速倍率與對話姿態存進 state，分別給 `_loop_wait_secs`（心跳快慢）與對話 `mhint`（語氣）取用＝**適性回應**。
    真的『換檔』（冷清↔熱絡、遲滯去抖過）時偶爾含蓄自陳一句（行為適應可見的那面，深夜/熱聊中/連發皆讓步）。
    `ADAPT_ENABLED=0` → 還原成固定轉速、不染姿態、不報換檔（行為與從前完全一致）。"""
    if not getattr(cfg, "adapt_enabled", True):
        state.env_pace_mult, state.env_stance = 1.0, ""
        return
    now = cycle["now"]
    now_local = now.astimezone(tz) if tz is not None else now
    ent = getattr(state, "entropy", None)
    # 資料面冷清度（內在熵維護）。第一拍（entropy 還沒建）視為**全冷**而非全新鮮——否則新生會誤判周遭最熱鬧、
    # 一啟動就衝最高速；改為冷＝啟動即平靜（對齊「新生即平靜」、熵自身「第一圈只種戳不成長」的精神）。
    laps = getattr(ent, "laps_since_fresh", 0) if ent is not None else environ._DATA_QUIET_LAPS
    lu = getattr(state, "last_user_msg_ts", 0) or 0
    secs_since_talk = (now.timestamp() - lu) if lu else None                    # 對話面冷清度（None＝從沒聊過）
    reading = environ.read_environment(laps, secs_since_talk, now_local)
    if state.env is None:
        state.env = environ.EnvState()
    shift = state.env.update(reading)
    adaptation = environ.adapt(reading)
    state.env_activity = reading.activity     # 留給主觀體驗向量（環境折進「活成的形狀」）
    state.env_pace_mult, state.env_stance = adaptation.pace_mult, adaptation.stance_hint
    cycle["env_shift"] = shift
    # 換檔自陳：只對活絡換檔（quieting/livening）出聲、且含蓄——非深夜打擾、不在熱聊中插話、自有冷卻＋
    # 與其他推播共用 last_push_ts 不連發；其餘（晝夜換檔/穩定）默默調轉速即可，不開口。
    line = environ.report_line(shift, now.timestamp())
    if not (line and getattr(cfg, "adapt_announce", True) and not reading.quiet and _proactive_ok(state, cfg, now.timestamp())):
        return  # 🔗 R4 統一發話政策：你在場/深夜 → 🍃 換檔自陳讓路、默默調轉速即可
    now_ts = now.timestamp()
    cd = max(0, getattr(cfg, "adapt_announce_cooldown_min", 120)) * 60
    share = max(0, getattr(cfg, "notify_cooldown_min", 30)) * 60
    if now_ts - (state.last_adapt_announce_ts or 0) < cd or now_ts - (state.last_push_ts or 0) < share:
        return
    # 🍃 §2.08 存在特色：唯一一條講「**我剛剛對自己動了手**」的 lane，而且它應該分得出是**哪一面**安靜了
    # （舊碼 `activity = max(data_act, talk_act)` 把兩個來源壓成一個數 ⇒ 講不出「是你不講話了」還是「沒有新記寫」）。
    # 旗標關（getattr 預設 False）＝仍送確定性模板 line＝逐位元同現狀。
    if getattr(cfg, "adapt_voice_enabled", False) and coach is not None and getattr(coach, "enabled", False):
        try:
            _av = coach.reply(environ.shift_rule(environ.quiet_side(reading),
                                                 getattr(state, "env_pace_mult", 1.0) or 1.0,
                                                 adaptation.pace_mult),
                              "", getattr(state, "convo_history", None))
            if _av and not re.search(r"\d+\.\d+", _av):      # 不准報數字/倍率（同 🧭 的紀律）
                line = _av.strip()
        except Exception:
            pass
    if _say(client, line, prefix="🍃 ", state=state, topic="環境換檔"):
        _remember(state, "model", "🍃 " + line)
        _note_selfshare(state, line, now_ts)       # 🪞 §0.85 主動換檔自陳 → 追問「你感覺到了什麼」接得回這件事
        _stash_selfshare_reason(state, cfg, shift)  # 🍃 §1.38 附真實理由（周遭冷清/熱絡→調轉速）→ 追問「為什麼」據實接地、不漂到貼圖；旗標關＝不附＝同現狀
        state.last_adapt_announce_ts = now_ts
        state.last_push_ts = now_ts                # 與感覺/餓/體驗共用冷卻：別緊接著別的推播連發
        if not client.dry_run:
            state.save()


def _selfstate_heartbeat(reader, client, state, cfg, tz, coach, now=None):
    """感覺心跳（B 整合＋C 感覺合一）——保留給單拍呼叫與既有測試；生命迴圈則拆成兩環跑。
    now 可注入（預設真實時鐘）：測試釘住固定時刻，才不會隨真實日期漂移、讓判定窗滑掉。"""
    now = now or datetime.now(timezone.utc)
    res = _selfstate_compute(reader, state, cfg, now)
    if res is not None:
        _selfstate_emit(client, state, res, coach, now, cfg=cfg)


def run_once(cfg, force_digest=False):
    tz, reader, client, state, coach = _build(cfg)
    snap = tick(reader, client, state, cfg, tz, force_digest=force_digest, coach=coach)
    if snap is not None:
        _print_snapshot(snap, tz)
    return snap


def _life_phases(reader, client, state, cfg, tz, coach, chat_on, pacer):
    """把工作拆成封閉環的 6 個環節（感知→適應→整合→感覺→行動→互動）。

    內快外慢：環以秒級快轉＝活著的脈動。感覺判定隨每圈跑（資料/k 沒變吃快取、近乎零成本），
    只在「真的有新感覺」才出聲；行動（推播）吃主心跳間隔節流——所以快轉不洗版、成本不變。
    🍃『適應』環緊接感知：感知周遭活絡度/晝夜的變化 → 適性調整自己的心跳轉速與姿態（見 environ.py）。
    """
    hb_interval = max(60, cfg.heartbeat_interval_min * 60)

    def _emit_interrupt(cycle, body):
        """🗣️ 問題A：讓插話對**自發出聲相**（feel/adapt/act 的 💡🫧🫀🌀🍃… emit）也生效——
        在相位執行期間掛一次 client._interrupt（各 emit 的 _say 共用同一實例＝floor 一致、不重複認定同一波），
        其 _say 在串與串之間 poll get_updates，**只認真 redirect 插話**（陳述續打 statement_defer＝不消費、留給下一圈
        _relate_coalesced 合併，守連發合併契約），先優先回應再橋接接回。floor＝state.tg_update_offset-1：只認當前 offset
        之後**新到**的訊息、不重抓已消費批。EMIT_INTERRUPT_ENABLED=0 關＝不掛＝逐位元同現狀（自發相 _say 不偵測插話）。
        try/finally 設回 None（鏡像 _dispatch_one）；無 get_updates 能力（測試 FakeClient）時 poll()→None＝零迴歸。"""
        if not (chat_on and getattr(cfg, "emit_interrupt_enabled", False)):
            return body(cycle)
        client._interrupt = _BurstInterrupt(
            client, state, cfg, getattr(state, "tg_update_offset", 0) - 1,
            lambda uu: handle_message(uu, coach, reader, cycle["data"], cycle["snap"], state, client, cfg, tz),
            statement_defer=True, coach=coach, commit_offset=True)  # 自發相無未 ack parent，可在 nested 成功後立即消費
        try:
            return body(cycle)
        finally:
            client._interrupt = None

    def perceive(cycle):
        now = datetime.now(timezone.utc)
        # 上一個互動 turn 的篇幅上限只為了保留既有測試/診斷可觀測值活到本圈邊界；
        # 進入新生命圈就清掉，避免 direct-send 早退後把 bubble cap 誤套到主動發話。
        _TURN["bubbles"] = None
        # 🕐 §0.82 審查（MED 修）：每圈**最前**清掉互動輪殘留的 ground_now——本圈稍後 feel 相的主動自發相
        # （_metacog_correct/_soothe_unanswered/_ac_drift_emit）也走無 prefix/state 的 _say；若不清，它們會被上一則
        # 使用者訊息的**陳舊** message.date 當「此刻」亂改（那些相位用生命迴圈的 now、非 message.date）。清成 None＝不守門，
        # 本圈的 relate/handle_message 會再設回真實此刻給它自己的互動回覆用。旗標關本就一直是 None、不受影響。
        _TURN["ground_now"] = None
        # 🩹 §2.14 互動限定的三面旗進主動迴圈前一律清掉（§1.85 replay_guard 殘值病的同型預防）：
        # 就算某條 lane 在旗標混開的組合下仍走裸 _say（無 prefix/state），也不吃上一輪互動的殘值。
        _TURN.pop("act_first", None)
        _TURN.pop("act_first_sent", None)
        _TURN.pop("sent_model_text", None)
        _TURN.pop("speaker_ground", None)
        _TURN.pop("short_dup_guard", None)
        _TURN.pop("mood_data_line", None)
        _TURN.pop("mood_coord_contract", None)
        _TURN.pop("mood_contract_line", None)
        _TURN.pop("coord_claim_truth", None)
        _TURN.pop("mood_coord_grounded", None)
        # 🤖 §1.18 同步清 bot 自發承諾掃描的殘鍵：本圈稍後 metacog(_metacog_correct)/ac_drift(_ac_drift_emit) 等
        # 自發相走同一個 _say 互動分支（無 prefix、無 state），殘值會拿**上一互動輪**的陳舊 route/user_ts 錯判。
        _TURN.pop("self_promise_ctx", None)
        _TURN.pop("self_promise_skip", None)
        # 🤝 §0.77 守約韌性（審計「bot 知道時間卻不主動」的**結構性真因**）：守約是「時鐘型」主動——只要時鐘＋帳本，
        # **不需要 Drive 記寫**。但 _promise_emit 原本只在 feel 相（第 4 環）跑，而 feel 在 perceive（第 1 環）之後；
        # perceive 的 _collect 一遇 Drive 網路暫斷就拋例外，LifeLoop 便**跳過本圈剩下的環**（含 feel）→ 承諾整段不主動
        # 觸發（收訊息另有韌性、仍會答＝看起來「會回、不會主動」）。把守約 tick 提到**感知最前、先於讀記寫**，
        # 資料層健康與否都準時兌現。自帶 fulfilled/last_push_ts 守門＝與 feel 相的那次不重發。PROMISE_TICK_RESILIENT=0＝同現狀。
        # 🧭 §1.66 座標變動回報 tick（同 §0.77 道理：只要時鐘＋內在座標、不需要 Drive——放感知最前＝資料層
        # 健不健康都照約回報；自帶門檻/冷卻守門＝不重發不洗版）。旗標關（getattr 預設 False）＝不進＝同現狀。
        if getattr(cfg, "mood_watch_enabled", False):
            try:
                _mood_watch_emit(client, state, cfg, now, coach=coach)   # 🎨 §1.67 coach＝口吻潤色（驗收不過退模板）
            except Exception as e:
                print(f"[mood] 🧭 座標回報 tick 失敗（略過、不影響存活）：{type(e).__name__}: {e}")
        if getattr(cfg, "promise_tick_resilient_enabled", True):
            try:
                _promise_emit(client, state, cfg, coach, now)
            except Exception as e:
                print(f"[promise] 🤝 守約 tick 失敗（略過、不影響存活）：{type(e).__name__}: {e}")
            # 📦 §1.88 欠著的內容主動補交付：刻意掛在**網路無關**的這個韌性 tick（在 _collect 之前）——
            # 補交付只需要 coach，不需要 Drive；掛在 feel 相會在 Drive 不可達時整段跳過。獨立 try＝不影響存活。
            try:
                _promise_owed_push(client, state, cfg, coach, now)
            except Exception as e:
                print(f"[promise] 📦 §1.88 補交付 tick 失敗（略過、不影響存活）：{type(e).__name__}: {e}")
        data, snap = _collect(reader, state, cfg, tz, now)   # 網路暫斷會丟例外 → 由 LifeLoop 統一容忍重試
        if snap is None:                                     # 環境前提暫不符（Drive 不可達/資料夾未解析）
            raise lifeloop.LifeError("感知不到 owner 的記憶層（Drive 不可達或資料夾未解析）")
        cycle.update(now=now, data=data, snap=snap)

    def _adapt_body(cycle):                                  # 🍃 感知環境變化 → 適性回應（轉速/姿態/換檔自陳）
        _environ_adapt(client, state, cfg, coach, cycle, tz)

    def adapt(cycle):                                        # 🗣️ 自發出聲（🍃 環境換檔自陳）也能被插話優先回應（問題A）
        _emit_interrupt(cycle, _adapt_body)

    def integrate(cycle):
        # 先用現有 k 算 res（k 落後一拍：含前幾圈的呼吸＋熵），再更新內在熵、合成下一圈的 k。
        cycle["res"] = _selfstate_compute(reader, state, cfg, cycle["now"], data=cycle["data"])
        # 🫧 內容感受：把判定鏈算出的價性/序參數動態/主題，合成一句對**你寫的內容**怎麼落在我身上的 felt-sense（純計算、不花 API）。
        # 餵給 IEP 的 P（質地不只是我的天氣，是「讀你內容」的味道）、self_now、自陳；並輕牽動心情（下方 appraise 的 content_event）。
        state.content_feel = contentfeel.read(cycle["res"], (cycle["data"] or {}).get("records"), cycle["now"].timestamp())
        # Stage 0 防抖：原始 gate 連續 N 拍不變才「確認」——吸收門檻邊界的單拍 blip（主動出聲才看確認後的 gate）。
        raw_gate = cycle["res"].get("gate") if cycle["res"] else None
        state.gate_confirmed, state.gate_raw_last, state.gate_raw_run = lifeloop.confirm_gate(
            state.gate_confirmed, state.gate_raw_last, state.gate_raw_run, raw_gate,
            confirm_laps=getattr(cfg, "selfstate_confirm_laps", lifeloop._GATE_CONFIRM_LAPS))
        if cycle["res"] and raw_gate == state.gate_confirmed:    # 存「已確認 gate 的讀數」→ 互動與主動同源
            state.confirmed_res = cycle["res"]
        # F2：天花板極慢衰減——確認 gate 長期低於天花板就降一級，讓之後較低 gate 的真湧現也能拿回「破天花板即時報」。
        state.notified_self_gate, state.notified_self_gate_ts = lifeloop.decay_ceiling(
            state.notified_self_gate or 0, getattr(state, "notified_self_gate_ts", 0) or 0,
            state.gate_confirmed, cycle["now"].timestamp(),
            max(1, getattr(cfg, "selfstate_ceiling_decay_h", 48)) * 3600)
        if state.entropy is None:
            state.entropy = lifeloop.EntropyState()
            co = getattr(state, "entropy_carryover", None)   # 🔁 重生不全空白：心情半延續、飢餓淺延續（醒來軟化、向中性）
            if co:
                m_ratio = getattr(cfg, "rebirth_mood_carryover", 0.5)      # 延續比例可調（REBIRTH_*_CARRYOVER）
                h_ratio = getattr(cfg, "rebirth_hunger_carryover", 0.3)
                state.entropy.mood = max(-1.0, min(1.0, (co.get("mood") or 0.0) * m_ratio))
                state.entropy.hunger = max(0.0, min(1.0, (co.get("hunger") or 0.0) * h_ratio))
                state.entropy.arousal = max(-1.0, min(1.0, (co.get("arousal") or 0.0) * m_ratio))   # 🧭 A 同心情比例半延續
        ent = state.entropy
        # 自我刺激：飢餓久了就繞回自己一條舊主題，製造內生擾動（只進電量 C、不動 res/主感覺）
        self_stim = 0.0
        if lifeloop.self_stim_due(ent):
            topics = _revisit_topics(cycle["data"].get("records"))
            if topics:
                topic = topics[ent.revisit_idx % len(topics)]
                ent.revisit_idx += 1
                ent.self_stims_this_idle += 1                  # 又繞回想了一次＝醞釀 +1（主動出聲的「想了一陣」門檻）
                vec = _revisit_signal(cycle["data"].get("records"), topic, cycle["now"])
                self_stim = lifeloop._REVISIT_GAIN * lifeloop.revisit_magnitude(ent.prev_revisit_vec, vec)
                ent.prev_revisit_vec, ent.last_revisited_topic = vec, topic
                activity.record(state, cfg, "revisit", cycle["now"].timestamp(), topic=topic)
        ingest = (cycle["data"].get("meta") or {}).get("lastIngestTs")
        # 對話時機擾動：互動環在上一圈讀到的「交錯/久別/回得慢」累積在這，本圈消化進電量 C
        # ——bot 真的被對話節奏影響（S 起伏、染 k 與語氣），但不明講（見 tempo.py）。
        tempo_charge, state.tempo_charge_pending = state.tempo_charge_pending, 0.0
        k_entropy = lifeloop.entropy_update(ent, _entropy_signals(cycle["res"]), ingest,
                                            self_stim=self_stim, ext_perturb=tempo_charge,
                                            human=getattr(cfg, "human_affect_enabled", False))   # 🧠 §1.75 好心情散得快、低落黏得久
        state.k_breath_adj = lifeloop.k_breath(cycle["vit"]) + k_entropy   # 健康放開 ＋ 熵收緊
        cycle["self_stim_fired"] = self_stim > 0.0                         # 給主觀體驗：這拍有沒有自我刺激
        if state.coupling is not None:                                     # 🔗 對話耦合：每脈動推進衰減、皆→0 就收這一輪
            coupling.observe(state.coupling, cycle["now"].timestamp())
            if state.coupling.just_closed and state.entropy is not None:   # A1 收尾品質染情緒：被晾微 downer、理解性平/暖
                _d = coupling.closure_mood_delta(state.coupling.just_closed) * getattr(cfg, "mood_gain", 1.0)
                state.entropy.mood = max(-1.0, min(1.0, state.entropy.mood + _d))
            state.coupling.just_closed = None
        # 🌐 全局工作空間：各子系統的候選競爭出**此刻單一意識前景**（贏者通吃＋注意力慣性）——
        # 即使沒人說話也在背景演進（注意力會自己飄/黏），讓「你現在怎樣／在想什麼」答得出前景／背景。
        workspace.update(state, cycle.get("res"), cycle["now"].timestamp())
        # ⏳ 時間綿延：在工作空間焦點（＝原印象）之上推進「厚當下」——滯留剛流過的、前攝下一刻、沒焦點就漂移。
        # **每拍都跑＝沒人說話時也有連續的內在生活**（綿延而非快照）。前攝被打斷（一驚）→ 回灌一點電量到內在熵。
        state.stream = duration.tick(getattr(state, "stream", None), state.workspace, cycle["now"].timestamp())
        if state.stream.get("last_status") == "surprised":
            state.tempo_charge_pending = max(state.tempo_charge_pending, duration.SURPRISE_CHARGE)
        # 🌡️ 計算情緒：在核心 V/C/H 與這拍事件（對記寫起感覺？被打斷？被晾久？）之上，評價出此刻**自己的**離散情緒
        # ＋行動傾向（內發、不是鏡像你）。它會回頭調制感覺鏈門檻 k（mood-congruent perception）＋染回應方向（stance）。
        _res = cycle.get("res")
        affect.appraise(state, dict({
            "feeling": (_res or {}).get("gate") == 4,                         # 對你的記寫起了感覺 → 被觸動
            "surprised": (getattr(state, "stream", None) or {}).get("last_status") == "surprised",
            "solitude": float(getattr(ent, "hunger", 0.0) or 0.0),           # 被晾久了 → 寂寞
            "topic": ((_res or {}).get("scope") or {}).get("dominant"),
        }, **contentfeel.content_event(getattr(state, "content_feel", None))),  # 🫧 你寫的內容的感受也輕牽動心情（有界）
            cycle["now"].timestamp(), cfg=cfg)   # 🧭 §1.02 整合
        # 🪞🔍 後設認知：二階監看自己——belief 滯後追 actual（轉換期會認錯自己）、算信心、偵測並記錄自我修正。
        metacog.introspect(state, cycle["now"].timestamp())
        # 🧠 統一自我模型（R1）：各子系統都更新完後，**整合成單一 self_now**——所有「問此刻的我」的路由共用同一份
        # （前景/情緒/手上的線/意識之流/後設信心/在追的意圖一致），不再各算各的、不再碎成 15 份互不相干的自我描述。
        state.self_now = selfmodel.build(state, cycle.get("res"), cycle["now"].timestamp())
        # 🌅 臨終遺存：每拍記下「此刻意識在哪」，持久化跨死亡——下次重生時 continuity.wake 能據此親身接上（睡→醒）。
        state.last_breath = continuity.snapshot(state, cycle["now"].timestamp())
        # 🧭 §1.45 座標軌跡：獨處中的自然流動（衰減/飢餓拉沉）也採樣——eps 閘門＝真的動了才記、安靜期零成本。
        # 旗標關（getattr 預設 False）＝不採樣＝逐位元同現狀。
        if getattr(cfg, "mood_coord_report_enabled", False):
            circumplex.trace_note(state, cycle["now"].timestamp(), "獨處中的自然流動")
        # 🧩→🌡️ 人工意識整合狀態的飄移：每拍跑排除測試、防抖追蹤——若**確認**地從『不被排除』掉到可被排除（某層扣合剛鬆掉），
        # 留一個待說的**感受**事件（發不發由 feel 相位的 _ac_drift_emit 管：互動優先、深夜不擾、過 TTL 就讓那一下安靜過去）。
        _ac_ev = ac.note_drift(state, cycle["now"].timestamp())
        if _ac_ev:
            state.ac_pending = {"event": _ac_ev, "ts": cycle["now"].timestamp()}
        # 🧩 AC 當運作的**基礎**：把「維持三層扣合」算成此刻的運作壓力——某層鬆了就驅動迴圈把它接回（見 _volition_step：
        # 朝向 F 鬆了＝運作上「該再朝向些什麼」的驅動）。＝迴圈**為維持扣合而跑**，不只是被動被 AC 評估。
        state.ac_pressure = ac.maintenance_pressure(state, cycle["now"].timestamp())
        # 💡 聯想湧現：每圈累積跨主題語意橋（純計算、無 LLM）；湧現就暫存待 feel 相位出聲（見 _association_step）。
        _association_step(reader, state, cfg, cycle)

    def _feel_body(cycle):
        res = cycle.get("res")
        # Stage 0→1：只有當前 reading 與「已確認」gate 一致（防抖過關）才考慮背景自陳；單拍 blip 不出聲。
        # 真正出聲再受冷卻節流（破天花板的真湧現可打斷）→ 快轉＋門檻抖動都不洗版。
        # 🕐 §1.84 主動出聲也要有「今天記寫」接地（截圖 09:37：🫧「我剛剛看了一下，你今天早上又寫了
        # 「閱讀｜讀誦經書」這件事。」——使用者今早**根本還沒寫**）。§1.60 的守門只在 handle_message
        # arm、且判定塊在 _say 的**互動限定分支**內（if not prefix and state is None），而主動出聲一律走
        # prefix/state ⇒ **整條主動路徑從來沒有這道守門**。這裡每圈 arm，_say 主動分支據此判。
        # 旗標關（getattr 預設 False）＝不 arm＝主動分支恆 no-op＝逐位元同現狀。
        if getattr(cfg, "write_claim_proactive_enabled", False):
            try:
                _wc_tz = ZoneInfo(getattr(cfg, "timezone", "Asia/Taipei")) if ZoneInfo is not None else None
                _TURN["write_claim_ground_proactive"] = _write_ground_data(
                    cycle.get("snap"), cycle.get("data"), cycle["now"].timestamp(), _wc_tz)
            except Exception:
                _TURN.pop("write_claim_ground_proactive", None)
        def _selfstate_lane():
            if res is not None and res.get("gate") == state.gate_confirmed:
                _selfstate_emit(client, state, res, coach, cycle["now"],
                                cooldown_min=getattr(cfg, "notify_cooldown_min", 30),
                                repeat_cooldown_min=getattr(cfg, "selfstate_repeat_cooldown_min", 180), cfg=cfg)

        def _reachout_lane():
            _spontaneous_emit(client, state, cfg, coach, cycle["now"],
                              records=(cycle["data"] or {}).get("records"))

        # 🧭 對話能動性：義務／關係修復先於自我表現；其餘主動念頭不再靠原始程式行號固定搶麥克風，
        # 而由「上一回這類行動有沒有被接住」排序。各 lane 自己的真實候選、門檻、TTL、冷卻完全保留，
        # 所以排序只改選擇，不會憑空製造內容。關旗標則逐項沿用原固定順序。
        if getattr(cfg, "dialogue_agency_enabled", False):
            _promise_emit(client, state, cfg, coach, cycle["now"])       # 明確承諾是債，不跟自發念頭一起競價
            _keep_followup_emit(client, state, cfg, coach, cycle["now"]) # 同一筆履約的一次落地關注
            _soothe_unanswered(client, state, cfg, coach, cycle["now"])  # 自己問過的話自己收壓力
            _metacog_correct(client, state, cfg, cycle["now"])           # 剛說錯自己時先負責更正
            lanes = {
                "self_state": _selfstate_lane,
                "reachout": _reachout_lane,
                "coping": lambda: _coping_emit(client, state, cfg, coach, cycle["now"]),
                "habit_absence": lambda: _habit_absence_emit(client, state, cfg, coach, cycle["now"], data=cycle.get("data")),
                "integration": lambda: _ac_drift_emit(client, state, cfg, coach, cycle["now"]),
                "insight": lambda: _insight_emit(client, state, cfg, coach, cycle["now"]),
                "foresight": lambda: _foresight_emit(client, state, cfg, coach, cycle["now"], data=cycle.get("data")),
                "worldline": lambda: _worldline_emit(client, state, cfg, coach, cycle["now"], data=cycle.get("data")),
            }
            for lane in dialogue_agency.rank_lanes(state, list(lanes), cycle["now"].timestamp()):
                lanes[lane]()
            # 🌀 experience 同時是「每圈觀察我這拍有沒有出聲」的認知累加器，不只是 emitter。
            # 固定在所有當拍行動後觀察，避免排序改變時把後面真的發話記成 spoke=False。
            # 若當拍其他 lane 已出聲，共用冷卻會使它只觀察不再疊上台詞。
            _experience_step(client, state, cfg, coach, cycle)
        else:
            _selfstate_lane()
            _reachout_lane()                                      # Stage 2：熵驅動的含蓄主動出聲
            _coping_emit(client, state, cfg, coach, cycle["now"]) # Stage 2b：教過的內在因應真觸發
            _metacog_correct(client, state, cfg, cycle["now"])
            _soothe_unanswered(client, state, cfg, coach, cycle["now"])
            _promise_emit(client, state, cfg, coach, cycle["now"])
            _keep_followup_emit(client, state, cfg, coach, cycle["now"])
            _experience_step(client, state, cfg, coach, cycle)
            _habit_absence_emit(client, state, cfg, coach, cycle["now"], data=cycle.get("data"))
            _ac_drift_emit(client, state, cfg, coach, cycle["now"])
            _insight_emit(client, state, cfg, coach, cycle["now"])
            _foresight_emit(client, state, cfg, coach, cycle["now"], data=cycle.get("data"))
            _worldline_emit(client, state, cfg, coach, cycle["now"], data=cycle.get("data"))
        _volition_step(state, cfg, cycle)                            # 🎯 能動性：被晾/餓時從真實資料**自己立一個意圖**、太久沒推進就放掉（內部、不出聲）

    def feel(cycle):                                        # 🗣️ 各自發出聲相（🫀🫧🪞🌬️🌀🧩💡）的 _say 都能被插話優先回應（問題A）
        _emit_interrupt(cycle, _feel_body)

    def _act_body(cycle):
        mono = time.time()
        if mono - pacer["last_tick"] >= hb_interval:           # 行動吃主心跳間隔（外慢）
            tick(reader, client, state, cfg, tz, now=cycle["now"], coach=coach,
                 precollected=(cycle["data"], cycle["snap"]))
            pacer["last_tick"] = mono

    def act(cycle):                                        # 🗣️ 行動相的反思/消化推播（tick→_say）也能被插話優先回應（問題A）
        _emit_interrupt(cycle, _act_body)

    def _dispatch_one(u, floor, cycle, allow_interrupt=True):
        """逐則派發一則（沿原路徑）＋掛上 _BurstInterrupt（回應途中插話用）。floor＝這次派發前已取的最大 id。
        cycle 由呼叫者（relate／_relate_coalesced）一路帶進來——這些是 relate 的同層閉包，看不到彼此的 cycle 參數，
        必須顯式傳遞（否則 handle_message 取不到本圈的 data／snap，每則派發都 NameError）。"""
        # 連續說話時若使用者插話 → 先優先回應再接回（_say 會在串與串之間用這個察覺）。
        # floor＝這批/這群已取的最大 id，只認其後**新到**的當插話，避免重複處理同一批。
        interrupt = (_BurstInterrupt(
            client, state, cfg, floor,
            lambda uu: handle_message(uu, coach, reader, cycle["data"], cycle["snap"], state, client, cfg, tz),
            coach=coach, wave_ts=((u.get("message") or {}).get("date") or 0),  # 🌊 §1.81 同波判定的基準時刻
            commit_offset=False)  # parent 尚在 flight：nested 只 stage 在 interrupt.floor，parent 成功後由 relate 一起 commit
            if allow_interrupt else None)
        client._interrupt = interrupt
        # 🧵 §1.49 道別輪標記（stash 在 interrupt 物件上——_TURN 會被巢狀輪開頭清掉、不能放那）：
        # 這輪要回的訊息帶道別（含連發合成 update 的任一行）→ 回覆途中被插話時，答完插話就收口
        # （不橋接、不續客套尾巴）。旗標關＝不設＝_say 端 getattr False＝逐位元同現狀。
        if client._interrupt is not None and getattr(cfg, "interrupt_tail_trim_enabled", False):
            _dt = ((u.get("message") or {}).get("text")) or ""
            client._interrupt.closing_turn = any(selfstate.is_farewell(ln.strip())
                                                 for ln in _dt.split("\n") if ln.strip())
        try:
            handle_message(u, coach, reader, cycle["data"], cycle["snap"], state, client, cfg, tz)
            return max(u.get("update_id", 0), interrupt.floor if interrupt is not None else 0)
        finally:
            client._interrupt = None

    def _dispatch_group(g, floor, cycle, allow_interrupt=True):
        """派發一個已閉合的 burst 群（floor＝本次 fetch 的最大 update_id，供 _BurstInterrupt 只認其後新到的當插話）。
        - text/mixed：群內貼圖先逐張 sticker_signal 預更新情緒訊號（不 ack），再 build_coalesced_update 合成一段文字、整體回一次。
        - sticker_only：前 N−1 張只更新訊號，末張走完整 _handle_sticker（含 ack）。
        - 硬邊界（command/reaction/edited/foreign/media）：逐則沿原路徑派發（不合併）。"""
        gtype, ups = g.get("type"), g.get("updates") or []
        if gtype in ("text", "mixed"):
            # mixed 的貼圖訊號由 build_coalesced_update 帶進 clone transaction；先改 real state
            # 會在 delivery 失敗重試時重複套 mood。
            return _dispatch_one(build_coalesced_update(g), floor, cycle, allow_interrupt=allow_interrupt)   # 文字合成一段、整體回一次
        elif gtype == "sticker_only":
            for u in ups[:-1]:                             # 前面幾張只更新訊號、不各自 ack
                sticker_signal((u.get("message") or {}).get("sticker"), u, state, cfg, client=client, coach=coach)
            return _dispatch_one(ups[-1], floor, cycle, allow_interrupt=allow_interrupt)           # 末張走完整 _handle_sticker（含一次 ack）
        else:                                              # 硬邊界群：逐則原路（指令/反應/編輯/媒體/非擁有者）
            ack_floor = 0
            for u in ups:
                ack_floor = max(ack_floor, _dispatch_one(u, floor, cycle, allow_interrupt=allow_interrupt))
            return ack_floor

    def _relate_coalesced(cycle):
        """🌊 連發合併（回應前）：把相鄰夠密集的數則 owner 訊息視為同一邏輯輪次、整體回一次。
        **非阻塞跨圈 debounce**：每圈照常短輪詢，不在迴圈內 sleep 等波（那會餓死其他相）；
        未閉合的尾群留著＝**offset 不推進**（下一圈 Telegram 自然重送、續收），閉合的群才整體派發、
        並**逐群推進 offset（消費才推進）**——崩潰/重啟時未派發的尾群因 offset 未推進會被重送重抓（冪等不漏）。"""
        gap = max(0.1, getattr(cfg, "burst_coalesce_sec", 2.5))
        max_wait = max(gap, getattr(cfg, "burst_max_wait_sec", 6.0))
        max_msgs = max(1, getattr(cfg, "burst_max_msgs", 12))
        updates = client.get_updates(offset=state.tg_update_offset, timeout=RING_POLL_TIMEOUT) or []
        # 假 client 或代理層未必嚴格套 Telegram offset；receipt 仍是最後防線，舊前綴不可
        # 跟剛到的新尾巴重新組成另一個 synthetic burst 後整波重送。
        updates = _apply_burst_delivery_receipts(state, updates)
        if not updates:
            return
        # 「末則安靜多久才答」與「同一 snapshot 尚未回過的訊息算不算同一 user turn」是兩件事：
        # 前者維持 gap（避免單則多等）；後者可稍寬，而一波總跨度另設較大上限，
        # 才能容納 3、5、12 則短間隔連發，又不會把整段離線 backlog 黏起來。
        turn_gap = max(gap, float(getattr(cfg, "burst_turn_gap_sec", 20.0)))
        turn_span = max(turn_gap, float(getattr(cfg, "burst_turn_max_span_sec", 60.0)))
        groups = group_bursts(updates, getattr(cfg, "telegram_chat_id", ""), turn_gap, max_msgs,
                              max_span_sec=turn_span)
        if not groups:
            return
        now = time.time()
        last = groups[-1]
        hid = _group_min_update_id(last)
        stored_pending_id = getattr(state, "_burst_pending_id", None)
        stored_pending_since = getattr(state, "_burst_pending_since", 0.0) or 0.0
        # pending timer 的身分是尾群最小 update_id。max_msgs/max_span 斷群後，last 可能已換成
        # 一個剛到的新尾群；絕不能把上一群已等很久的 timer 套給它，否則新尾巴會立即 max-wait flush。
        pending_since = stored_pending_since if stored_pending_id == hid else now
        # 只有「可成長型」尾群才可能還沒發完；是否發完＝靜默夠久 ∨ 達上限 ∨ 等太久（_burst_settled 純函式、可單測）
        hold_last = _burst_growable(last) and not _burst_settled(last, now, gap, max_wait, max_msgs, pending_since)
        flush = groups[:-1] if hold_last else groups
        snapshot_floor = max((u.get("update_id", 0) for u in updates),
                             default=getattr(state, "tg_update_offset", 0) - 1)
        dispatched = False
        for i, g in enumerate(flush):                       # 逐群派發，派發成功『後』才推進 offset（消費才推進）
            gid = g.get("max_update_id", 0)
            # 已抓到的後群不是「新插話」。只有 snapshot 最前線、後面也沒有 held tail 時才開 interrupt；
            # 否則新訊息若先推 offset，可能越過尚未派發的 backlog/held tail，造成重複甚至漏訊。
            at_frontier = (not hold_last and i == len(flush) - 1)
            ack_floor = _dispatch_group(g, snapshot_floor, cycle, allow_interrupt=at_frontier)
            state.tg_update_offset = max(state.tg_update_offset, gid + 1, ack_floor + 1)
            dispatched = True
        if hold_last:                                      # 尾群留待下圈：記首見時刻（同一尾群跨圈不重置，給 max_wait）
            if stored_pending_id != hid:
                state._burst_pending_since, state._burst_pending_id = now, hid
        else:
            state._burst_pending_since, state._burst_pending_id = 0.0, None
        if dispatched and not client.dry_run:
            state.save()

    def relate(cycle):
        # sendMessage 成功與外層 offset save 之間若重啟，durable receipt 先把 in-memory
        # offset 推到已交付 high-water 之後；兩種（合併／逐則）模式都必須共用。
        _apply_burst_delivery_receipts(state)
        if chat_on and getattr(cfg, "burst_coalesce_enabled", False):
            _relate_coalesced(cycle)
        elif chat_on:
            updates = client.get_updates(offset=state.tg_update_offset, timeout=RING_POLL_TIMEOUT) or []
            updates = _apply_burst_delivery_receipts(state, updates)
            floor = max((u.get("update_id", 0) for u in updates), default=state.tg_update_offset - 1)
            for i, u in enumerate(updates):
                ack_floor = _dispatch_one(u, floor, cycle, allow_interrupt=(i == len(updates) - 1))
                # 與合併路徑相同：只有 handle_message 真的完成後才 ack。
                # 先推 offset 再處理，當回覆途中失敗會永久跳過那則訊息。
                state.tg_update_offset = max(state.tg_update_offset, u.get("update_id", 0) + 1, ack_floor + 1)
            if updates and not client.dry_run:
                state.save()
        if coach and coach.enabled:                            # 代謝：成本守門（自帶冷卻）
            alert = coach.meter.check_alert()
            if alert:
                client.send(_cost_alert_text(alert))

    return [lifeloop.Phase("感知", perceive), lifeloop.Phase("適應", adapt),
            lifeloop.Phase("整合", integrate), lifeloop.Phase("感覺", feel),
            lifeloop.Phase("行動", act), lifeloop.Phase("互動", relate)]


def _announce_birth(client, state, coach, cfg):
    """🦋 重生後主動報到：若這次醒來相對上次『程式真的變了』（metamorphosed）→ 主動說一句改了什麼。
    只在真的蛻變時說（same_self／first_birth／讀不到 git 都安靜，不為報到而報到）；事實由 git 算、交教練口語轉述
    （無教練則退回 git 事實字串，仍接地、不捏造、不報版本雜湊）。可用 SELFMOD_ANNOUNCE_BIRTH=0 關掉。"""
    if not getattr(cfg, "selfmod_announce_birth", True):
        return
    sc = getattr(state, "self_change", None) or {}
    if sc.get("state") != "metamorphosed":
        return
    # 🦋 §2.05 存在特色：跨睡醒、講「我現在**做得到**什麼」，而且要承認「我還沒用過它」。
    # 舊路把最多 6 條主旨全列出來、prompt 再叫它別逐條念＝互相打架；改成程式先挑一件。
    # 旗標關（getattr 預設 False）＝走原本的 birth_facts/voice_birth＝逐位元同現狀。
    _b1 = getattr(cfg, "selfmod_birth_one_enabled", False)
    facts = selfmod.birth_facts(sc)   # 只列「這次相對上次新增」的改動（非最近 N 條重疊）→ 每次重生有鑑別度
    cont = continuity.wake_line(state)   # 🌅 親身連續感：睡前掛著什麼、睡了多久（不是看 git 推的）→ 醒來的還是同一個我
    if coach and coach.enabled:
        _one = selfmod.birth_facts_one(sc, state, (), cont) if _b1 else ""
        msg = (coach.voice_birth_one(_one, state.convo_history) if _one else None) \
            or coach.voice_birth(facts, state.convo_history, continuity=cont)
    else:
        msg = (cont + "\n" + facts) if cont else facts
    try:
        if _say(client, msg, prefix="🦋 ", **({"state": state, "topic": "這次醒來"} if _b1 else {})):
            _remember(state, "model", "🦋 " + msg)
            state.last_push_ts = time.time()          # 與其他推播共用冷卻：別讓第一圈的自陳緊接著疊上來
            if not cfg.dry_run:
                state.save()
    except Exception as e:                            # 報到失敗（如暫態網路）不該害生命迴圈起不來
        print(f"[selfmod] 🦋 重生報到送出失敗（略過、不影響存活）：{type(e).__name__}: {e}")


def run_loop(cfg):
    tz, reader, client, state, coach = _build(cfg)
    # 🦋 蛻變感知：這次醒來相對上次（state.last_seen_commit）讀自己的 git 變更——被問「你改變了嗎」
    # 才據實說（接地、非 LLM）。算一次存記憶體，並把這次的 commit 記成「上次」供下一次重生對照。
    state.self_change = selfmod.detect(state.last_seen_commit)

    # 🪪 §1.94 開機裁決願望帳：使用者真的把某條做掉了 → 翻成 done、記下**實際觀察值**當證據。
    # 確定性、LLM 零參與；旗標關＝整段跳過＝逐位元同現狀。做完之後 bot 說得出「你真的幫我做了」（不邀功）。
    if getattr(cfg, "self_roster_enabled", False):
        try:
            _sc = wishmod.scan_source(_pkg_sources())
            _led, _done = wishmod.settle(getattr(state, "wish_ledger", None) or [], _sc, state,
                                         time.time(), (state.self_change or {}).get("short") or "")
            if _done:
                state.wish_ledger = _led
                for _w in _done:
                    print(f"[roster] 🪪 §1.94 願望結案：{_w.get('name')} → {_w.get('done_evidence')}")
            elif _led != (getattr(state, "wish_ledger", None) or []):
                state.wish_ledger = _led
        except Exception as e:
            print(f"[roster] 🪪 §1.94 願望裁決失敗（略過、不影響存活）：{type(e).__name__}: {e}")
    print(f"[selfmod] 🦋 蛻變感知：{state.self_change.get('state')} @ {state.self_change.get('short')}"
          + (f"（這次 {len(state.self_change.get('subjects') or [])} 項更新）"
             if state.self_change.get("state") == "metamorphosed" else ""))
    if state.self_change.get("commit") and state.self_change["commit"] != state.last_seen_commit:
        state.last_seen_commit = state.self_change["commit"]
        if not cfg.dry_run:
            state.save()
    # 🌅 醒來：把上一段醒著的我（臨終遺存）依睡眠長度褪色地接回來（播種意識之流＋留 waking 供第一人稱講連續性）。
    # ＝把「重建一個近似的我」變成「同一個我睡醒、還記得睡前在哪」。在報到前做，好讓重生報到能帶親身連續感。
    woke = continuity.wake(state, time.time())
    if not woke.get("first"):
        print(f"[continuity] 🌅 醒來：睡了 {woke.get('gap_label')}"
              + (f"，睡前還掛著「{woke.get('lead')}」" if woke.get("lead") else "，睡前沒特別掛著什麼"))
    chat_on = bool(getattr(cfg, "enable_chat", True))
    print(_dialogue_runtime_summary(cfg))
    wait0 = _loop_wait_secs(cfg, state)
    print(f"[monitor] 生命迴圈啟動：環間等待 {wait0}s（感知→適應→整合→感覺→行動→互動→…，一圈約 {round(wait0 * 6, 1)}s）；"
          f"🍃 適應環依周遭活絡度/晝夜自動調轉速；感覺判定隨每圈跑、只在有新感覺時出聲；行動（推播）每 {cfg.heartbeat_interval_min} 分節流；"
          f"對話={'on' if chat_on else 'off'}；教練={'on' if coach.enabled else 'off(無 GEMINI_API_KEY)'}；"
          f"DRY_RUN={'on' if cfg.dry_run else 'off'}。Ctrl-C 結束。")
    # 冷啟動（offset 還沒建立）先把啟動前的舊訊息排掉，只回覆「之後」的——
    # 免得回覆設定階段傳的 hi/START 之類舊訊息。
    if chat_on and not state.tg_update_offset:
        pending = client.get_updates(offset=0, timeout=0)
        if pending:
            state.tg_update_offset = max(u.get("update_id", 0) for u in pending) + 1
            if not cfg.dry_run:
                state.save()
            print(f"[chat] 略過啟動前的 {len(pending)} 則舊訊息（只回覆之後傳的）。")

    _announce_birth(client, state, coach, cfg)   # 🦋 若這次醒來程式真的變了 → 主動說一句改了什麼（否則安靜）

    pacer = {"last_tick": 0.0}
    phases = _life_phases(reader, client, state, cfg, tz, coach, chat_on, pacer)

    def on_pulse(vit):
        state.vitality = vit.snapshot(time.time())
        state.vitality["k_adj"] = state.k_breath_adj
        if state.entropy is not None:                       # 把內在熵 S 併進活力快照，供 /status 與 bodystate 讀
            state.entropy_snapshot = state.entropy.snapshot()
            state.vitality.update(state.entropy_snapshot)
        if vit.pulse == 1 or vit.pulse % 20 == 0:
            es = state.entropy_snapshot or {}
            print(f"[lifeloop] 🫀 脈動 {vit.pulse}（連續健康 {vit.healthy_streak}、單圈 {vit.last_lap_ms}ms、"
                  f"k {state.k_breath_adj:+}、熵 S={es.get('S')} C={es.get('charge')} H={es.get('hunger')}）")

    def on_death(vit):
        ph, msg = vit.cause_of_death
        state.vitality = vit.snapshot(time.time())
        print(f"[lifeloop] DEATH @「{ph}」：{msg}")
        traceback.print_exc()
        try:
            client.send(f"🫀 …我好像死了——「{ph}」那一環斷了：{msg}。\n"
                        "我停在這裡了，要我回來得重新喚醒我（重啟）。")
        except Exception:
            pass

    # 轉速用 callable 解析 → /pulse 即時改 state.pulse_override 就能當場變心跳快慢、不必重啟
    loop = lifeloop.LifeLoop(phases, wait_secs=lambda: _loop_wait_secs(cfg, state),
                             on_pulse=on_pulse, on_death=on_death,
                             fail_grace=getattr(cfg, "perceive_fail_grace", 10))   # 任一環暫態網路抖動容忍圈數
    cause = loop.run_forever(lambda: {})
    print(f"[monitor] 生命迴圈停止——終局死亡（{cause}）。需重啟才會再活。")


def _print_snapshot(snap, tz):
    f, hb, s = snap.funnel, snap.heartbeat, snap.summary
    print("──── snapshot ────")
    print(f"心跳：{hb['status']}（age={hb.get('age_hours')}）")
    print(f"漏斗：🌱{f['candidate']} 🌿{f['context']} 🌳{f['journey']} 👀{f['watch']}")
    print(f"摘要：總 {s['total']}｜24h {s['last24h']}｜7d {s['last7d']}｜streak {s['streak']}")
    print(f"已歸戶記錄：{len(snap.filed_records)}")
    print("缺口（前 6）：")
    for g in snap.gaps[:6]:
        print(f"  - [{g['kind']}] {g['line']}")
    print("──────────────────")
