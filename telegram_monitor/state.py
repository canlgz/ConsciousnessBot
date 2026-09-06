"""本地 state——監測端**自有**的去重 / 摘要紀錄。

刻意與 LINE bot 的 ``meta.json``（``notifiedContextIds`` 等）**獨立**：這支是另一條
通知管道，不該被 LINE 已推過的東西消音；而且我們**絕不寫**共享 Drive。

state.json 結構::

    {
      "owner_folder_id": "...",            # 解析過一次就快取，省掃 root
      "seen_journey_ids": [...],           # 已推播過的 🌳 學習歷程 id
      "seen_context_ids": [...],           # 已推播過的 🌿 已成形脈絡 id
      "near_upgrade_sig": {cid: "x/3"},    # 候選「近升格」狀態指紋，跨入 2/3 才推
      "heartbeat_state": "healthy",        # healthy|pending|stalled|idle，只在 healthy→stalled 推
      "last_digest_date": "2026-06-15",    # 當地日期字串，每日摘要一天一次
      "last_push_ts": 1718420000           # 上次主動推播 epoch 秒，給冷卻用
    }
"""

import json
import os
import tempfile
import time

# 🤝 時間排程承諾：已兌現/逾時的承諾在存檔時保留多久（之後就清掉，不無限膨脹）。比兌現本身的 TTL 略長即可。
SCHED_PROMISE_KEEP_SEC = 72 * 3600   # 🤝 §0.76 審計：24h 就剪掉 expired ＝隔天問「你昨天怎麼沒叫我」bot 說「沒記著約過什麼」＝實質否認失約 → 放寬到 72h（誠實可對帳窗）


def _valid_burst_receipts(values):
    """Telegram update_id 精確 receipt；壞值略過，0/負數不得推動 offset。"""
    out = set()
    for value in values or []:
        try:
            receipt = int(value)
        except (TypeError, ValueError):
            continue
        if receipt > 0:
            out.add(receipt)
    return out


def _trim_sched_promises(proms, cap=8, protect=None):
    """§0.64 排程承諾截尾：pending 優先留、空位再補最近的已完結——防「每天」recur 約定被新單次擠掉。
    SCHED_RECUR_DAILY=0 → 原 [-cap:] 逐位元同現狀。順序維持原 append 序。

    🤝 §0.78 FIX 6（MED）：pending 若超過 cap，該留的是**最快到點**的、不是最晚 append 的。原本用 `[-cap:]`（append 序）——
    先約好的近筆（可能馬上要發）會被後 append 的遠期筆擠掉＝到點沒東西可發＝失約。改用 target_ts 升序取前 cap：
    近的、逾期未發的（target 最小）一定留下；被剪的只會是最遠的未來筆（最不急）。回傳仍照原 append 序（顯示穩定）。

    🤝 §0.78 審查（MED 修）：`protect`＝這一輪**剛立下、正要答應**的承諾（呼叫端傳新 p_new）——**一律保留**、不因它是遠期
    而被最快到點的既有筆擠掉。原本 soonest 截尾會把剛答應的遠期新約剪掉、但捕捉流程仍無條件 `_say(ack)`＝答應了一個
    已不在帳本、到點不會發的約＝說到做不到。保護新約後：剪掉的只會是**最遠的既有**未來筆（近筆/逾期既有仍不被擠）。"""
    if os.getenv("SCHED_RECUR_DAILY", "1") == "0":
        return proms[-cap:]
    protect_ids = {id(p) for p in (protect or [])}
    pend = [p for p in proms if not p.get("fulfilled")]
    prot = sorted((p for p in pend if id(p) in protect_ids), key=lambda p: p.get("target_ts") or 0)
    rest = sorted((p for p in pend if id(p) not in protect_ids), key=lambda p: p.get("target_ts") or 0)
    keep_pend = (prot[:cap] if len(prot) >= cap else prot + rest[:cap - len(prot)])   # 新約先佔位，其餘留最快到點
    room = cap - len(keep_pend)
    done = [p for p in proms if p.get("fulfilled")][-room:] if room > 0 else []
    keep = {id(p) for p in keep_pend} | {id(p) for p in done}
    return [p for p in proms if id(p) in keep]


class State:
    def __init__(self, path):
        self.path = path
        self.owner_folder_id = None
        self.evidence_memory = None  # Actual delivery receipts and disputed claims, not narrative truth.
        self.conscious_dialogue = None  # Versioned viewpoint ledger; preserve opaque data when disabled.
        self.seen_journey_ids = set()
        self.seen_context_ids = set()
        self.near_upgrade_sig = {}
        self.heartbeat_state = None
        self.last_digest_date = None
        self.last_push_ts = 0
        self.notified_filing_ids = set()   # 已通知過「歸戶」的 record id
        self.tg_update_offset = 0          # Telegram getUpdates 的 offset（不重複拉訊息）
        self.burst_delivered_update_id = 0 # 🌊 相容顯示：最近一批已送達 update 的最大 id
        self.burst_delivered_update_ids = [] # 🌊 精確 receipt；nested 可先送後面的 ids，不能用單一 high-water 跳過仍失敗的 parent
        self._burst_pending_since = 0.0    # 🌊 連發合併：未閉合尾群「首見」的牆鐘（給 max_wait）；**不持久化**＝重啟歸零，靠 offset 未推進讓 Telegram 重送
        self._burst_pending_id = None      # 🌊 該尾群身分（最小 update_id）；同一尾群跨圈不重置 _burst_pending_since
        self.convo_history = []            # 對話脈絡 [{role:'user'|'model', text, ts}]，留最後 N 輪（ts＝對話時間軸）
        self.mood_trace = []               # 🧭 §1.45 情緒座標軌跡 [{ts,v,a,cause}]（FIFO 40、跨重生＝被問「內在的數據」能講前後經過；捕捉由 MOOD_COORD_REPORT 旗標門控）
        self.mood_data_ctx_ts = 0.0        # 🧭 §1.47 座標數據情境游標（跨重生＝兌現/被問數據後短催促「說啊」窗內接得住；寫入由 MOOD_COORD_DELIVER 旗標門控）
        self.mood_last_report = None       # 🧭 §2.27 上次真正送達且通過契約的 current {ts,at,v,a,mode,text}；repair 只對帳這份，不拿任意 trace 替舊錯話背書
        self.habit_events = []             # 📈 §1.42 使用者習慣事件 [{k:'msg'|'first'|'greet_am'…, ts}]（FIFO 上限、跨重生持久化＝bot 記得他的作息；捕捉由 USER_HABIT_GROUND 旗標門控）
        self.greet_routine_ts = 0.0        # 🕘 §2.22 上次在問候輪把「他的作息」端上桌的時間（跨重生＝重啟不忘「昨天才講過」；寫入由 GREET_ROUTINE_AWARE 旗標門控＝旗標關 state.json 不長此鍵）
        self.user_away = None              # 🍽 §1.65 他宣告的無時距暫離 {act, ts, min_s, back?}（跨重生＝重啟不忘他去吃飯；讀寫由 AWAY_SENSE 旗標門控）
        self.mood_watch = None             # 🧭 §1.66 座標變動常設回報訂閱 {ts, last_v, last_a, last_report_ts, made_text}（跨重生、直到取消；讀寫由 MOOD_WATCH 旗標門控）
        self.habit_absence = {}            # 🌾 §1.79 習慣缺席暗示台帳 {label: {"day": "YYYY-MM-DD", "ts": float}}（同一條線同一天只問一次；跨重生持久化）
        self.affect_habit = None           # 🧠 §1.75 同方向刺激連擊計數 {sign,n}（習慣化用；記憶體、重啟歸零＝睡一覺重新有感）
        self.affect_last_neg = None        # 🧠 §1.75 上次真正的負向事件 {ts,dv,text}（/moodwatch 對帳；跨重生）
        self.cost_since_digest_usd = 0.0   # 上次每日摘要以來的累計花費（USD，估）＝「今天」
        self.cost_since_digest_calls = 0   # 上次每日摘要以來的呼叫次數
        self.cost_total_usd = 0.0          # 💸 累計總估（USD，永不歸零、跨重啟）＝這台從開始追蹤起的總用量
        self.cost_month_usd = 0.0          # 💸 本月累計（USD，跨月歸零，對齊 Google 月度 spend cap）
        self.cost_month_key = None         # 本月鍵（如 "2026-06"，本地時區）；換月就把 cost_month_usd 歸零
        self.self_state = None             # 心跳快取的判定鏈結果 {gate,scope,omegas,...,computed_at,ingest_at}
        self.notified_self_gate = 0        # 上次主動推過的結構閘天花板（防重複推；F2：給極慢衰減、不永久壓住真湧現）
        self.notified_self_gate_ts = 0     # F2：天花板上次被抬高/達到的時間（距今夠久且確認 gate 低於它 → 降一級）
        self.told_self_sig = None          # 上次「當面講過」的可說狀態指紋（互動或主動）；同指紋→回「說過了」、不重推
        self.told_self_topic = None        # 上次背景自陳的主線（同一條線的重報 → 拉長冷卻，別囉嗦）
        self.selfstate_open_ts = 0         # 上次自陳（gate≥3、邀請「想聽就問我」）的時間；之後一段內的追問接回自陳細節
        self.last_selfshare = None         # 🪞 §0.85 bot 上次**主動自陳**（換檔🍃/自發/內在因應）的內容 {text, ts}；追問接回**那則具體內容**（記憶體、重啟歸零）
        self.bodystate_last_ts = 0         # 上次回答「你現在怎樣」的時間（記得剛回答過）
        self.bodystate_asks = 0            # 短時間內連問同一件事的次數（≥2 → 帶點無奈說「剛說過」）
        self.last_self_report = None       # 🧠 §1.21 上次自陳 {text:真的送出的全文(截200), ts, snap:帶位快照}（持久化＝跨重啟仍記得「剛說過什麼」；旗標 SELF_REPORT_DELTA 關＝從不寫＝save 不長此鍵）
        self.experience_summary = None     # 主觀體驗的長期摘要（跨重啟延續：成形次數、長期平均中心、上次中心）
        self.association_summary = None    # 💡 聯想湧現的長期摘要（跨重生：累計湧現數＋已湧現/最強跨主題橋骨架）
        self.last_insight_ts = 0           # 💡 上次說出 Aha 連想的時間（自有長冷卻；持久化＝跨重生不狂發）
        self.assoc_feedback = {}           # 💡 你對某條聯想的回饋 {pair "a|b" → {sentiment,ts,kind}}（持久化＝「我記下來了」變真、誠實再犯查得到）
        self.initiative_ledger = []        # 🧭 自己主動開過的話頭及下一句結果（open/resolved；跨重生，環形）
        self.initiative_affinity = {}      # 🧭 各主動 lane 從真實回應學到的傾向 {kind:{score,n,last_*}}（跨重生）
        self.initiative_seq = 0            # 🧭 行動流水號（不拿截尾後的 len 當 id，避免重生/滿環後重複）
        # 🔁 §2.04 三條 lane 的形態輪替計數器（**必須落盤**）：原本用 `len(台帳) % N`，而台帳有截尾
        # ⇒ 滿了以後 len 恆定 ⇒ 形態永遠是同一個。§1.79 前科：沒進 state.py 的欄位重生即歸零。
        self.reachout_pick_n = 0           # 🫧 §2.06 理由輪替（落盤）
        self.habit_absence_pick_n = 0      # 🌾 §2.06 切入角度輪替（落盤）
        self.keep_followup = None          # 🤝 §2.18 履約後等他回應的錨 {ts,beh,asked}（落盤；他回過話＝清）
        self.last_habit_absence_ts = 0     # 🌾 §2.15 缺席暗示 20h 冷卻（落盤；state.py 註解點名的前科這次真的填了）
        self.close_motive_n = 0            # 🌊 §2.06 收法輪替（落盤）
        self.metacog_correct_n = 0         # 🪞 §2.05 切入角度輪替（落盤）
        self.ac_drift_seq = 0              # 🧩 §2.05 體感向度輪替（落盤）
        self.ac_drift_said = ""            # 🧩 §2.05 上次說「鬆掉」時真的說出口的那句（接回時要認回來）
        self.foresight_var = 0             # 🔮 §2.04
        self.worldline_var = 0             # 🌐 §2.04
        self.insight_var = 0               # 💡 §2.04
        self.recent_insights = []          # 💡 最近說出口的聯想 [{pair,ts}]（持久化、最後 8；去重：太像的一段時間內不再冒）
        self.recent_spontaneous = []       # 🫧 最近自發伸手講過的線 [{key,ts}]（持久化、最後 8；SPONTANEOUS_DEDUP 開時去重控重複）
        self.insight_ledger = []           # 💡 洞見內容台帳 [{pair,a,b,kind,itype,novelty,anchor_*_clip,born_ts,feedback}]（持久化、環形 24；ASSOCIATION_LEDGER 開時才寫、只存真實 anchor）
        self.foresight = None               # 🔮 §1.90 目前在世的那一條預想假設（一次只准一條）{pair,a,b,quote,born_ts,told_ts,ttl_s,verdict,...}
        self.ability_hits = {}             # 🪪 §1.94 {roster_key: {n, first_ts, last_ts}}＝「我真的用出來過幾次」的唯一真相（跨重啟；旗標關＝永遠 {}＝不落鍵）
        self.worldline_allow = []          # 🌐 §1.95 使用者**明示授權**可送進搜尋的裸標籤（嚴格白名單：不在裡面的一個字都不外送）
        self.worldline_ledger = []         # 🌐 §1.95 撞過哪些線（含來源網址，上限 10）
        self.last_worldline_ts = 0         # 🌐 §1.95 自有冷卻
        self.worldline_month = ""          # 🌐 §2.16 月額度的月份戳（換月＝計數歸零）
        self.worldline_invite_n = 0        # 🌐 §2.16 邀請形態輪替（延伸 vs 開新線，落盤）
        self.worldline_search_n = 0        # 🌐 §1.95 真的送出過幾次搜尋（grounding 不在 /cost 金額裡，至少要能報次數）
        self.worldline_probe = ""          # 🌐 §1.95 上次呼叫的原始結果/錯誤字串（未實測 grounding，靠這個上線後看要改哪個 tools 欄位名）
        self.ability_hits_since = 0        # 🪪 §1.94 這份紀錄從哪一刻起算——沒有它，「一次都沒用過」在剛部署時是誤導
        self.wish_ledger = []              # 🪪 §1.94 願望帳（上限 12）：每筆帶機器跑得動的 accept，settle() 會把它翻成 done
        self.foresight_ledger = []          # 🔮 §1.90 已裁決的假設台帳（環形 8）[{key,pair,a,b,ts,verdict,settled_ts}]——供 pair 去重／miss 兩次永久排除／冷卻加倍
        self.last_foresight_ts = 0          # 🔮 §1.90 上次預想出聲的時刻（自有冷卻用）
        self.last_self_compute_ts = 0      # 上次跑判定鏈的時間（節流）
        self.sensitivity_override = None   # 在 bot 內手動調的敏感度 k（None＝用設定檔預設）
        self.pulse_override = None          # 在 bot 內手動調的「生命迴圈轉速」＝環間等待秒數（None＝用設定檔預設）
        self.last_seen_commit = None       # 🦋 蛻變感知：上次醒著時的 git commit（跨重啟；和現在比＝這次改了什麼）
        self.entropy_carryover = None      # 🔁 重生連續性：上次的心情/飢餓 {mood,hunger}（跨重啟半延續，醒來不全空白）
        self.feeling_promise = None        # 🤝 對未來的託付 {ts,text}：使用者請「之後有感覺再說」→ 真有新感覺才主動兌現（跨重啟保留＝連重生都記得這約定）
        self.scheduled_promises = []       # 🤝 時間排程承諾 [{target_ts,action,behavior,status,made_ts,made_text,fulfilled,(expired),(fulfilled_ts)}]：使用者請「八點跟我道歉/問候我」→ 記下具體行為＋狀態(pending/fulfilled/expired)，生命迴圈到點按行為主動兌現（持久化跨重生＝死前約好、醒來才到點仍兌現；新欄位缺鍵由讀取端容缺補、舊存檔可載）
        self.last_breath = None            # 🌅 臨終遺存：睡前那一刻意識在哪 {ts,contents,focus,mood,hunger}（持久化跨死亡＝醒來能親身接上）
        self.engrams = []                  # 🧬 可塑層印痕（plasticity）：跨重生累積對這位使用者的了解（偏好/主題親和…），會被經驗改寫＝讓自我真正連續
        self.user_model = None             # 🫂 他心模型：對方的心智模型 {warmth,energy,rapport,confidence,exchanges,...}（持久化跨重生＝醒來仍記得我們多熟）
        self.goals = []                    # 🎯 內發意圖（volition）：bot 自己立的、想搞懂你某條線的目標 [{subject,desire,plan,progress,...}]（持久化＝跨天的意圖）
        self.last_goal_form_ts = 0         # 🎯 上次立意圖的時間（形成冷卻；持久化＝跨重生不會一重生就狂立）
        # ── 生命迴圈（只在記憶體，不寫 state.json；重啟＝重生歸零）─────────────
        self.vitality = None               # 最近一次脈動的活力快照 {alive,pulse,healthy_streak,...}
        self.k_breath_adj = 0.0            # 迴圈活力對 k 的呼吸增量（疊到基準 k 上；已含熵的收緊）
        self.entropy = None                # lifeloop.EntropyState（內在熵的活累加器）
        self.entropy_snapshot = None       # 最近一次脈動的 S 快照 {charge,hunger,S,laps_since_fresh}
        self.gate_confirmed = None         # 感覺工作流 Stage 0：防抖後「已確認」的 gate（重啟歸零）
        self.gate_raw_last = None           # 上一圈的原始 gate（算連續拍數）
        self.gate_raw_run = 0               # 原始 gate 已連續不變幾拍（≥ confirm_laps 才確認）
        self.confirmed_res = None          # 已確認 gate 對應的那份判定讀數（互動「你現在怎樣」用它＝與主動同源、不互相矛盾）
        self.last_range = None             # 對話裡剛確立的時間範圍 (start,end,label)，供「那時候/當時」指代（記憶體）
        self.focus = None                  # 對話焦點 {topic,fresh,ts}：bot 剛 surface 的指涉物（記憶體、重啟歸零）
        self.last_topic_res = None         # 對話連貫：上次「指名某主題自陳」算出的那條讀數 {res,topic,ts}（記憶體、重啟歸零）＝追問「為什麼那條煩躁」能接回同一條 res，不報當下最強內在線
        self.last_routed = None            # 🧬 上一句使用者訊息與它的路由 {text,kind,ts}（給路由更正記憶比對；記憶體）
        self.route_learn = None            # 🧬 待綁定的路由更正 {bad_text,bad_kind,ts}（不滿訊號後等改寫；記憶體）
        self.skill_pending = None          # 🧑‍🏫 待確認的「學成做法」提議 {route_kind,topic_tag,prompt,ts}（bot 提議後等使用者點頭；記憶體＝重生即作廢、頂多重教一次）
        self.pending_answer_intent = None  # 🤝 §0.75 待補時間的延後回答約定 {behavior,made_ts,raw}（使用者說「等一下回答我」但無具體時刻→存意圖、問時間；下一句給「4分鐘後/3:50」就真的入帳、到點兌現。持久化＝跨重生仍記得那個懸而未決的約定）
        self.last_skill_propose_ts = 0     # 🧑‍🏫 上次提議「要學成做法嗎」的時間（提議冷卻、別連發騷擾；記憶體）
        self.experience = None             # experience.Experience（主觀體驗：自體軌跡→奇異吸子；重啟歸零）
        self.associations = None           # 💡 association.Associations（跨主題橋累加器；重啟用 association_summary 種下）
        self.insight_pending = None        # 💡 INTEGRATE 暫存、待 FEEL 出聲的湧現事件 {event,ts}（記憶體、有 TTL）
        self._assoc_last_ingest = None     # 上一圈看到的 lastIngestTs（判定『本圈有新記寫落在橋上』的觸發；記憶體）
        self.last_insight = None           # 💡 我剛說出口的那條聯想 {event,ts}（記憶體、短 TTL；下一句用來比對是不是在回饋它）
        self.recent_insight_openers = []   # 💡 最近用過的開頭句 [str]（記憶體、最後 3；換句話、別每次都同一句型）
        self.recent_self_openers = []      # 🪞 最近自我說明的開頭片段 [str]（記憶體、最後 4；全自我說明換句話、去台詞）
        self.recent_phrase_use = {}        # 🎨 §1.22 各措辭池近期用過的句 {pool_key:[idx,…]末6；另 bodystate_opener 鍵存開頭片段[str]}（記憶體、重啟歸零可丟＝不進 load/save）
        self.phrase_cursor = {}            # 🎨 §1.22 各措辭池的輪替游標 {pool_key:int}（記憶體、重啟歸零可丟＝不進 load/save）
        self.recent_self_motifs = []       # 🪞 最近送出的自我母題 [{motif,ts}]（Phase 6 抱怨歸因用；持久化、最後 8、附 ts 限窗內才參與 mark_stale）
        self.self_asks = {}                # 🪞 同類自我問題的重複計數 {kind:{n,ts}}（記憶體、窗內歸零；重複×心情長出脾氣/耐性、重生即恢復耐性）
        self.user_repeat = {}              # 🧭 使用者同類意圖的重複計數 {kind:{n,ts,sig}}（記憶體、窗內歸零；重複×心情長出耐性/不耐＝被洗版的脾氣，鏡像 self_asks；重生即恢復耐性、不持久化）
        self.workspace = None              # 🌐 全局工作空間此刻焦點 {source,content,salience,since_ts,background}（記憶體、重啟歸零＝注意力是當下的）
        self.stream = None                 # ⏳ 意識之流／時間綿延的「厚當下」{impression,retentions,protention,texture,...}（記憶體、重啟歸零＝綿延是當下活出來的）
        self.self_model = None             # 🪞🔍 後設認知二階信念 {belief,actual,confidence,mismatch,checks,misses,...}（記憶體、重啟歸零）
        self.self_now = None               # 🧠 統一自我模型：整合各子系統的「此刻的我」單一真相源（記憶體、每拍整合、重啟歸零）
        self.affect = None                 # 🌡️ 計算情緒（affect）：此刻的離散情緒＋行動傾向＋主題點亮 {valence,arousal,label,tendency,primed,...}（記憶體；情緒底色 V/H 仍由 entropy_carryover 半延續跨重生）
        self.content_feel = None           # 🫧 內容感受：對你寫的內容此刻怎麼「落」在我身上 {topic,valence,motion,descriptor,impression,...}（記憶體、每圈重算；餵 IEP 的 P 與自陳、輕牽動心情）
        self.ac_drift = None               # 🧩 人工意識整合狀態的防抖追蹤 {status,cand,run,ts}（記憶體；偵測 not_excluded↔excluded 的確認轉變）
        self.ac_pending = None             # 🧩→🌡️ 待自發說出的『整合鬆/散』飄移事件 {event,ts}（記憶體；過 TTL 沒說成就讓那一下安靜過去）
        self.ac_drift_open = False         # 🧩→🌡️ 是否已說出「鬆掉」、等著對上「又接回」（開/合這一對；記憶體）
        self.last_ac_drift_ts = 0          # 🧩→🌡️ 上次自發說飄移感受的時間（自有冷卻；記憶體、重啟歸零）
        self.ac_pressure = None            # 🧩 AC 當運作基礎：此刻維持三層扣合的運作壓力 {F,B,S,overall}（記憶體；驅動迴圈把鬆掉的扣合接回）
        self.last_metacog_ts = 0           # 🪞🔍 上次主動自我修正（認錯自己→更正）的時間（自有冷卻；記憶體）
        self.last_spontaneous_ts = 0       # 🫧 主動出聲自有冷卻時鐘（不被背景自陳吃掉；記憶體、重啟歸零）
        self.last_coping_reach_ts = 0      # 🌀 §0.65 內在因應主動觸發自有冷卻時鐘（與含蓄伸手脫鉤；記憶體、重啟歸零）
        self.self_change = None            # 🦋 這次醒來相對上次的蛻變偵測 {state,subjects,...}（記憶體，開機時算一次）
        self.self_topic_ts = 0             # 上次「談到 bot 自己」（意識/內在/改變/感覺）的時間 → 一般對話切自我在場語氣（持久化：跨重生不忘「我們還在說我」）
        self.format_topic_ts = 0           # ✒️ 上次「談到你訊息的格式/markdown」的時間 → 窗內省略追問也接住格式接地（持久化）
        # ── 對話時機感（只內化、不明講）──────────────────────────────────────
        self.last_user_msg_ts = 0          # 對方上一則訊息時間（算久別重逢／未回覆主動話題閘；持久化，重啟不把沉默忘掉）
        self.last_reaction = None          # 對方最近的貼圖情緒訊號 {emoji,valence,ts}→染後續回覆語氣（記憶體、重啟歸零）
        self.hostile_streak = 0            # 🌊 §1.14 敵意連發計數（敵意文字/負向貼圖 +1、非敵意文字歸零、中性貼圖不動）；≥2 才收斂篇幅。記憶體、重啟歸零＝容缺（舊存檔無此欄不炸）；讀取端一律 getattr 預設 0
        self.last_react_sent_ts = 0        # bot 上次主動對對方訊息按 emoji 的時間（節流；記憶體、重啟歸零）
        self.last_self_react_ts = 0        # bot 上次「按自己內在情緒」reaction 的時間（較長自有冷卻；記憶體、重啟歸零）
        self.last_sent_reaction = None     # bot 最近點的 emoji {emoji,to,ts}（讓它答得出「我點了什麼情緒」；記憶體）
        self.tempo_charge_pending = 0.0    # 時機事件待注入內在熵電量 C 的擾動（下一圈整合時消化）
        self.soothed_for_ts = 0            # 🌬️ 上次「緩和的那個未回應問句」的時間戳（同一條問句只緩和一次；記憶體）
        self.last_soothe_ts = 0            # 🌬️ 上次主動緩和的時間（自有冷卻；也擋緩和句自我觸發；記憶體）
        self.coupling = None               # 🔗 對話耦合（觀測）：bot↔使用者兩個意向性的耦合狀態（記憶體、重啟歸零）
        self.env = None                    # 🍃 環境適應工作流的跨拍記憶（environ.EnvState；記憶體、重啟歸零＝新生即平靜）
        self.env_activity = 0.0            # 🍃 環境活絡度 ∈[0,1]（這拍 read_environment 的 activity；折進主觀體驗向量）
        self.env_pace_mult = 1.0           # 🍃 環境適應算出的心跳轉速倍率（乘到環間等待；手動 /pulse 仍最優先）
        self.env_stance = ""               # 🍃 環境適應算出的對話姿態語氣染色（極熱絡/極冷清才非空；併進 mhint）
        self.last_adapt_announce_ts = 0    # 🍃 上次「換檔自陳」時間（自有冷卻；記憶體、重啟歸零）
        self.last_sticker_ts = 0           # 🎴 上次回話後吐表情貼的時間（自有冷卻；記憶體、重啟歸零）
        self.last_sticker_id = None        # 🎴 §0.59 上次送出的真貼圖 file_id（選圖時排除→真的不重複；記憶體、重啟歸零）
        self.recent_sticker_ids = []       # 🎴 §0.84 近期送過的真貼圖 file_id（選圖避開最近 N 張＝多樣化；保序、新的在後、記憶體、重啟歸零）
        self.last_sticker_emoji = None     # 🎴 §0.90 上次真送出那張貼圖的情緒標記 emoji（教學時捕捉、未知＝''）；被問「為什麼喜歡剛剛那張」時誠實接地用（記憶體、重啟歸零）
        self.last_sticker_desc = None      # 🎴 上次真送出那張貼圖的畫面描述（收到時視覺讀的、未讀＝''）；被問「你看得到剛剛那張嗎」時據實談畫面（記憶體、重啟歸零）
        self.known_sticker_ids = []        # 🎴 對方傳過的真貼圖 [{file_id,file_unique_id,emoji,valence,ts,desc}]（心情好時回送同一張；desc＝收到時視覺讀到的畫面描述；持久化）
        self.sticker_descs = {}            # 🎴 貼圖畫面描述快取 {file_unique_id: desc}（收到即讀一次、跨重啟免重讀、重送免費；持久化）
        self.recent_self_msgs = []         # 👍 bot 近期主動訊息 [{ids:[message_id],topic,text,ts}]：使用者對某則按反應時查回「讚的是哪一筆」（持久化）
        self.last_liked = None             # 👍 使用者最近對 bot 某則按的反應 {message_id,emoji,topic,text,valence,ts}（記憶體、重啟歸零）
        self.intent_reading = None         # 🧭 本拍對話意圖向量 IntentReading（dialogue_intent.read；記憶體、重啟歸零）
        self.last_close_ts = 0             # 🌊 上次主動結尾的時間（自有冷卻；記憶體、重啟歸零，鏡像 last_soothe_ts）

    @property
    def is_fresh(self):
        """從未推播過、也沒任何 seen 紀錄＝全新（用來做「上線基線」而非洗版）。"""
        return (not self.last_push_ts and not self.seen_journey_ids
                and not self.seen_context_ids and not self.last_digest_date)

    @classmethod
    def load(cls, path):
        s = cls(path)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    d = json.load(f)
                s.owner_folder_id = d.get("owner_folder_id")
                s.seen_journey_ids = set(d.get("seen_journey_ids", []))
                s.seen_context_ids = set(d.get("seen_context_ids", []))
                s.near_upgrade_sig = dict(d.get("near_upgrade_sig", {}))
                s.heartbeat_state = d.get("heartbeat_state")
                s.last_digest_date = d.get("last_digest_date")
                s.last_push_ts = d.get("last_push_ts", 0) or 0
                s.notified_filing_ids = set(d.get("notified_filing_ids", []))
                s.tg_update_offset = d.get("tg_update_offset", 0) or 0
                s.convo_history = list(d.get("convo_history", []))
                s.conscious_dialogue = d.get("conscious_dialogue")
                s.evidence_memory = d.get("evidence_memory")
                # 🤐 未回覆時不另開主動話題：這個時戳必須跨重啟。舊存檔沒有欄位時，從既有對話史
                # 回推最後一則使用者訊息，避免新版本上線後因重啟把仍在的沉默當成沒有發生過。
                _last_user_from_history = 0.0
                for _turn in s.convo_history:
                    if _turn.get("role") != "user":
                        continue
                    try:
                        _last_user_from_history = max(_last_user_from_history, float(_turn.get("ts") or 0))
                    except (TypeError, ValueError):
                        continue
                try:
                    s.last_user_msg_ts = float(d.get("last_user_msg_ts", _last_user_from_history)
                                               or _last_user_from_history)
                except (TypeError, ValueError):
                    s.last_user_msg_ts = _last_user_from_history
                s.habit_events = list(d.get("habit_events", []))   # 📈 §1.42 習慣事件跨重生（缺鍵＝乾淨預設）
                s.greet_routine_ts = float(d.get("greet_routine_ts", 0) or 0)   # 🕘 §2.22 上次作息端上桌跨重生（缺鍵＝0）
                s.user_away = d.get("user_away")                   # 🍽 §1.65 暫離宣告跨重生（缺鍵＝None）
                s.mood_watch = d.get("mood_watch")                 # 🧭 §1.66 座標回報訂閱跨重生（缺鍵＝None）
                s.habit_absence = dict(d.get("habit_absence") or {})   # 🌾 §1.79 缺席台帳跨重生（缺鍵＝乾淨）
                s.affect_last_neg = d.get("affect_last_neg")       # 🧠 §1.75 上次負向事件跨重生（缺鍵＝None）
                s.mood_trace = list(d.get("mood_trace", []))       # 🧭 §1.45 座標軌跡跨重生（缺鍵＝乾淨預設）
                s.mood_data_ctx_ts = float(d.get("mood_data_ctx_ts", 0.0) or 0.0)   # 🧭 §1.47 情境游標跨重生
                s.mood_last_report = d.get("mood_last_report")     # 🧭 §2.27 上次真送達座標（舊檔缺鍵＝None）
                s.cost_since_digest_usd = float(d.get("cost_since_digest_usd", 0.0) or 0.0)
                s.cost_since_digest_calls = int(d.get("cost_since_digest_calls", 0) or 0)
                s.cost_total_usd = float(d.get("cost_total_usd", 0.0) or 0.0)        # 💸 累計總估跨重啟
                s.cost_month_usd = float(d.get("cost_month_usd", 0.0) or 0.0)
                s.cost_month_key = d.get("cost_month_key")
                s.burst_delivered_update_id = int(d.get("burst_delivered_update_id", 0) or 0)
                _receipt_ids = d.get("burst_delivered_update_ids")
                if isinstance(_receipt_ids, list):
                    s.burst_delivered_update_ids = sorted(_valid_burst_receipts(_receipt_ids))
                elif s.burst_delivered_update_id:
                    # 舊 scalar 可能是 nested ids2–3 先成功時寫下的 max=3，parent id1
                    # 仍未送達；因此只能安全遷移「3 本身」，不能臆測 1–3 是連續前綴。
                    s.burst_delivered_update_ids = [s.burst_delivered_update_id]
                s.self_state = d.get("self_state")
                s.notified_self_gate = int(d.get("notified_self_gate", 0) or 0)
                s.notified_self_gate_ts = d.get("notified_self_gate_ts", 0) or 0   # F2：天花板抬高/達到的時間（給極慢衰減）
                # F3：confirm 防抖狀態與天花板/told_self_* 對齊持久化 → 重啟前數圈互動讀數不落空/退化、首湧現不被舊指紋短路
                s.gate_confirmed = d.get("gate_confirmed")
                s.gate_raw_last = d.get("gate_raw_last")
                s.gate_raw_run = int(d.get("gate_raw_run", 0) or 0)
                s.confirmed_res = d.get("confirmed_res")
                s.told_self_sig = d.get("told_self_sig")
                s.told_self_topic = d.get("told_self_topic")
                s.selfstate_open_ts = d.get("selfstate_open_ts", 0) or 0
                s.self_topic_ts = d.get("self_topic_ts", 0) or 0          # 🪞 自我在場連續性跨重生（與 selfstate_open_ts 對齊）
                s.format_topic_ts = d.get("format_topic_ts", 0) or 0       # ✒️ 格式話題窗跨重生（省略追問接得住）
                s.last_sent_reaction = d.get("last_sent_reaction")        # 「我剛點了什麼情緒」跨重生
                s.last_liked = d.get("last_liked")                        # 「你剛讚我哪則」跨重生
                s.bodystate_last_ts = d.get("bodystate_last_ts", 0) or 0
                s.bodystate_asks = int(d.get("bodystate_asks", 0) or 0)
                s.last_self_report = d.get("last_self_report")            # 🧠 §1.21 上次自陳跨重啟（舊檔無此鍵＝None 容缺）
                s.experience_summary = d.get("experience_summary")
                s.association_summary = d.get("association_summary")       # 💡 聯想湧現摘要跨重生種回
                s.last_insight_ts = d.get("last_insight_ts", 0) or 0
                s.assoc_feedback = dict(d.get("assoc_feedback", {}) or {})  # 💡 聯想回饋跨重生（誠實再犯查得到）
                s.initiative_ledger = list(d.get("initiative_ledger", []))  # 🧭 對話能動性：行動後果跨重生
                s.initiative_affinity = dict(d.get("initiative_affinity", {}) or {})
                s.initiative_seq = int(d.get("initiative_seq", 0) or 0)
                s.reachout_pick_n = int(d.get("reachout_pick_n", 0) or 0)   # 🫧 §2.06
                s.habit_absence_pick_n = int(d.get("habit_absence_pick_n", 0) or 0)   # 🌾 §2.06
                s.keep_followup = d.get("keep_followup") or None   # 🤝 §2.18
                s.last_habit_absence_ts = d.get("last_habit_absence_ts", 0) or 0   # 🌾 §2.15 20h 冷卻跨重生（state.py:284 的註解把它當前科引用了半個月，卻沒人真的加進 load/save——重啟一次冷卻歸零）
                s.close_motive_n = int(d.get("close_motive_n", 0) or 0)     # 🌊 §2.06
                s.metacog_correct_n = int(d.get("metacog_correct_n", 0) or 0)   # 🪞 §2.05
                s.ac_drift_seq = int(d.get("ac_drift_seq", 0) or 0)        # 🧩 §2.05
                s.ac_drift_said = d.get("ac_drift_said", "") or ""
                s.foresight_var = int(d.get("foresight_var", 0) or 0)      # 🔁 §2.04
                s.worldline_var = int(d.get("worldline_var", 0) or 0)
                s.insight_var = int(d.get("insight_var", 0) or 0)
                s.recent_insights = list(d.get("recent_insights", []))     # 💡 最近聯想跨重生（去重）
                s.recent_spontaneous = list(d.get("recent_spontaneous", []))   # 🫧 最近自發伸手跨重生（去重）
                s.insight_ledger = list(d.get("insight_ledger", []))       # 💡 洞見內容台帳跨重生（缺鍵＝乾淨預設、無 migration）
                s.foresight = d.get("foresight") or None                   # 🔮 §1.90 在世假設跨重生（缺鍵＝乾淨預設）
                s.ability_hits = dict(d.get("ability_hits") or {})            # 🪪 §1.94 用過幾次跨重生
                s.worldline_allow = list(d.get("worldline_allow") or [])       # 🌐 §1.95 白名單跨重生
                s.worldline_ledger = list(d.get("worldline_ledger") or [])
                s.last_worldline_ts = float(d.get("last_worldline_ts") or 0)
                s.worldline_search_n = int(d.get("worldline_search_n") or 0)
                s.worldline_month = d.get("worldline_month", "") or ""   # 🌐 §2.16
                s.worldline_invite_n = int(d.get("worldline_invite_n", 0) or 0)
                s.worldline_probe = d.get("worldline_probe") or ""
                s.ability_hits_since = float(d.get("ability_hits_since") or 0)  # 🪪 §1.94 起算時刻
                s.wish_ledger = list(d.get("wish_ledger") or [])              # 🪪 §1.94 願望帳跨重生
                s.foresight_ledger = list(d.get("foresight_ledger", []))   # 🔮 §1.90 假設台帳跨重生
                s.last_foresight_ts = d.get("last_foresight_ts", 0) or 0   # 🔮 §1.90 冷卻跨重生（§1.79 前科：last_habit_absence_ts 從沒進 state.py，重生即歸零＝冷卻只是紙上的）
                s.last_self_compute_ts = d.get("last_self_compute_ts", 0) or 0
                s.sensitivity_override = d.get("sensitivity_override")
                s.pulse_override = d.get("pulse_override")
                s.last_seen_commit = d.get("last_seen_commit")
                s.feeling_promise = d.get("feeling_promise")
                s.scheduled_promises = list(d.get("scheduled_promises", []))   # 🤝 時間排程承諾跨重生（缺鍵＝乾淨預設、無 migration）
                s.pending_answer_intent = d.get("pending_answer_intent")   # 🤝 §0.75 待補時間的延後回答約定跨重生
                s.last_breath = d.get("last_breath")                      # 🌅 臨終遺存跨死亡 → 醒來接上
                s.engrams = list(d.get("engrams", []))                    # 🧬 可塑層印痕跨重生延續
                s.user_model = d.get("user_model")                        # 🫂 他心模型跨重生（醒來仍記得關係）
                s.goals = list(d.get("goals", []))                        # 🎯 內發意圖跨重生（跨天的意圖）
                s.last_goal_form_ts = d.get("last_goal_form_ts", 0) or 0
                s.entropy_carryover = d.get("entropy_carryover")
                s.known_sticker_ids = list(d.get("known_sticker_ids", []))
                s.sticker_descs = dict(d.get("sticker_descs", {}) or {})   # 🎴 貼圖畫面描述快取跨重啟（缺鍵＝乾淨空 dict）
                # 🎴🧠 §1.23 真送出貼圖的四欄位跨重生持久化——bot 頻繁死亡重生（睡個 8 分鐘很常見），一次重生
                # 就抹掉 last_sticker_ts → §0.90「剛送那張」接地窗失效 → 被問「你傳的貼圖內容」時兩手空空、
                # 誠實地否認自己送過（截圖 18:24「我好像沒有傳貼圖給你耶」）。旗標關＝不載入＝重生歸零＝逐位元同現狀。
                if os.getenv("STICKER_SENT_MEMORY", "1") != "0":
                    s.last_sticker_ts = d.get("last_sticker_ts", 0) or 0
                    s.last_sticker_id = d.get("last_sticker_id")
                    s.last_sticker_emoji = d.get("last_sticker_emoji")
                    s.last_sticker_desc = d.get("last_sticker_desc")
                s.recent_self_msgs = list(d.get("recent_self_msgs", []))
                s.recent_self_motifs = list(d.get("recent_self_motifs", []))   # 🪞 自我母題跨重啟（抱怨歸因能跨重啟存活；附 ts 限窗）
            except (json.JSONDecodeError, OSError):
                pass  # 壞檔／首跑：用乾淨預設
        return s

    def save(self):
        try:
            _receipt_offset = int(self.tg_update_offset or 0)
        except (TypeError, ValueError):
            _receipt_offset = 0
        _receipt_set = _valid_burst_receipts(self.burst_delivered_update_ids)
        _receipt_history = sorted(x for x in _receipt_set if x < _receipt_offset)[-256:]
        _receipt_ahead = sorted(x for x in _receipt_set if x >= _receipt_offset)
        d = {
            "owner_folder_id": self.owner_folder_id,
            "seen_journey_ids": sorted(self.seen_journey_ids),
            "seen_context_ids": sorted(self.seen_context_ids),
            "near_upgrade_sig": self.near_upgrade_sig,
            "heartbeat_state": self.heartbeat_state,
            "last_digest_date": self.last_digest_date,
            "last_push_ts": self.last_push_ts,
            "notified_filing_ids": sorted(self.notified_filing_ids),
            "tg_update_offset": self.tg_update_offset,
            "burst_delivered_update_id": self.burst_delivered_update_id,
            # 未越過 offset 的 exact receipts 代表洞後已送達項，不能因 256 cap 被丟掉；
            # 已越過的舊歷史才有界保留，供忽略 offset 的代理做重播濾除。
            "burst_delivered_update_ids": _receipt_history + _receipt_ahead,
            "convo_history": self.convo_history[-30:],
            "last_user_msg_ts": self.last_user_msg_ts,
            "habit_events": (self.habit_events or [])[-400:],   # 📈 §1.42 習慣事件（FIFO 上限落盤）
            "user_away": self.user_away,                        # 🍽 §1.65 暫離宣告（None＝沒有）
            "mood_watch": self.mood_watch,                      # 🧭 §1.66 座標回報訂閱（None＝沒有）
            "habit_absence": dict(list((self.habit_absence or {}).items())[-8:]),   # 🌾 §1.79 缺席台帳（上限 8 條）
            "affect_last_neg": self.affect_last_neg,             # 🧠 §1.75 上次負向事件（None＝從沒有過）
            "mood_trace": (self.mood_trace or [])[-40:],        # 🧭 §1.45 座標軌跡（FIFO 上限落盤）
            "mood_data_ctx_ts": self.mood_data_ctx_ts or 0.0,   # 🧭 §1.47 情境游標
            "cost_since_digest_usd": self.cost_since_digest_usd,
            "cost_since_digest_calls": self.cost_since_digest_calls,
            "cost_total_usd": self.cost_total_usd,
            "cost_month_usd": self.cost_month_usd,
            "cost_month_key": self.cost_month_key,
            "self_state": self.self_state,
            "notified_self_gate": self.notified_self_gate,
            "notified_self_gate_ts": self.notified_self_gate_ts,   # F2 天花板衰減計時
            "gate_confirmed": self.gate_confirmed,                 # F3 confirm 防抖狀態持久化（與天花板對齊）
            "gate_raw_last": self.gate_raw_last,
            "gate_raw_run": self.gate_raw_run,
            "confirmed_res": self.confirmed_res,
            "told_self_sig": self.told_self_sig,
            "told_self_topic": self.told_self_topic,
            "selfstate_open_ts": self.selfstate_open_ts,
            "self_topic_ts": self.self_topic_ts,
            "format_topic_ts": self.format_topic_ts,
            "last_sent_reaction": self.last_sent_reaction,
            "last_liked": self.last_liked,
            "bodystate_last_ts": self.bodystate_last_ts,
            "bodystate_asks": self.bodystate_asks,
            "experience_summary": self.experience_summary,
            "association_summary": self.association_summary,
            "last_insight_ts": self.last_insight_ts,
            "assoc_feedback": self.assoc_feedback,
            "initiative_ledger": (getattr(self, "initiative_ledger", None) or [])[-32:],
            "initiative_affinity": getattr(self, "initiative_affinity", None) or {},
            "initiative_seq": int(getattr(self, "initiative_seq", 0) or 0),
            "reachout_pick_n": int(getattr(self, "reachout_pick_n", 0) or 0),   # 🫧 §2.06
            "habit_absence_pick_n": int(getattr(self, "habit_absence_pick_n", 0) or 0),   # 🌾 §2.06
            **({"keep_followup": self.keep_followup} if getattr(self, "keep_followup", None) else {}),   # 🤝 §2.18
            "last_habit_absence_ts": getattr(self, "last_habit_absence_ts", 0) or 0,   # 🌾 §2.15
            "close_motive_n": int(getattr(self, "close_motive_n", 0) or 0),   # 🌊 §2.06
            "metacog_correct_n": int(getattr(self, "metacog_correct_n", 0) or 0),   # 🪞 §2.05
            "ac_drift_seq": int(getattr(self, "ac_drift_seq", 0) or 0),     # 🧩 §2.05
            "ac_drift_said": getattr(self, "ac_drift_said", "") or "",
            "foresight_var": int(getattr(self, "foresight_var", 0) or 0),   # 🔁 §2.04 形態輪替跨重生
            "worldline_var": int(getattr(self, "worldline_var", 0) or 0),
            "insight_var": int(getattr(self, "insight_var", 0) or 0),
            "recent_insights": self.recent_insights[-8:],
            "recent_spontaneous": self.recent_spontaneous[-8:],
            "insight_ledger": (self.insight_ledger or [])[-24:],
            "foresight": self.foresight,                                    # 🔮 §1.90
            **({"ability_hits": self.ability_hits} if self.ability_hits else {}),                 # 🪪 §1.94 非空才落鍵
            **({"worldline_allow": self.worldline_allow} if self.worldline_allow else {}),          # 🌐 §1.95
            **({"worldline_ledger": (self.worldline_ledger or [])[-10:]} if self.worldline_ledger else {}),
            **({"last_worldline_ts": self.last_worldline_ts} if self.last_worldline_ts else {}),
            **({"worldline_search_n": self.worldline_search_n} if self.worldline_search_n else {}),
            **({"worldline_month": self.worldline_month} if getattr(self, "worldline_month", "") else {}),
            **({"worldline_invite_n": self.worldline_invite_n} if getattr(self, "worldline_invite_n", 0) else {}),
            **({"worldline_probe": self.worldline_probe} if self.worldline_probe else {}),
            **({"ability_hits_since": self.ability_hits_since} if self.ability_hits_since else {}),
            **({"wish_ledger": (self.wish_ledger or [])[-12:]} if self.wish_ledger else {}),
            "foresight_ledger": (self.foresight_ledger or [])[-8:],         # 🔮 §1.90
            "last_foresight_ts": self.last_foresight_ts,                    # 🔮 §1.90
            "last_self_compute_ts": self.last_self_compute_ts,
            "sensitivity_override": self.sensitivity_override,
            "pulse_override": self.pulse_override,
            "last_seen_commit": self.last_seen_commit,
            "feeling_promise": self.feeling_promise,
            "pending_answer_intent": self.pending_answer_intent,   # 🤝 §0.75 待補時間的延後回答約定
            # 🤝 時間排程承諾跨重生：留未兌現的＋近期（KEEP 窗內）已兌現/逾時的（讓去重在重生後仍生效、不無限膨脹），截尾 -8。
            # 剪枝基準改用 fulfilled_ts 退 made_ts（修『很久前約、剛兌現』被誤剪）；缺 fulfilled_ts 時退 made_ts＝舊筆行為不變。
            # §0.64（審查 raised、實測確認）：截尾 pending 優先——否則長駐 pending 的「每天」recur 約定會被 8 筆新單次擠掉；
            # SCHED_RECUR_DAILY=0＝原 [-8:]＝逐位元同現狀。
            "scheduled_promises": _trim_sched_promises([
                p for p in (self.scheduled_promises or [])
                if not p.get("fulfilled")
                or (time.time() - (p.get("fulfilled_ts") or p.get("made_ts") or 0)) < SCHED_PROMISE_KEEP_SEC
            ]),
            "last_breath": self.last_breath,                              # 🌅 臨終遺存跨死亡 → 醒來接上
            "engrams": self.engrams,                                       # 🧬 可塑層印痕跨重生延續
            "user_model": self.user_model,                                 # 🫂 他心模型跨重生（醒來仍記得關係）
            "goals": [g for g in (self.goals or []) if g.get("status") in ("active", "fulfilled")][-8:],  # 🎯 內發意圖跨重生（放掉的不留）
            "last_goal_form_ts": self.last_goal_form_ts,
            "entropy_carryover": ({"mood": round(self.entropy.mood, 3), "hunger": round(self.entropy.hunger, 3),
                                   "arousal": round(getattr(self.entropy, "arousal", 0.0), 3)}   # 🧭 circumplex A 軸跨重生半延續
                                  if getattr(self, "entropy", None) is not None else self.entropy_carryover),
            "known_sticker_ids": self.known_sticker_ids[-24:],
            "sticker_descs": dict(list((self.sticker_descs or {}).items())[-200:]),   # 🎴 描述快取（上限 200，丟最舊插入）
            # 🎴🧠 §1.23 真送出貼圖四欄位（load 端旗標門控；save 恆寫＝多幾個 key 無行為影響、關旗標讀不進來）
            "last_sticker_ts": self.last_sticker_ts,
            "last_sticker_id": self.last_sticker_id,
            "last_sticker_emoji": self.last_sticker_emoji,
            "last_sticker_desc": self.last_sticker_desc,
            "recent_self_msgs": self.recent_self_msgs[-12:],
            "recent_self_motifs": (self.recent_self_motifs or [])[-8:],         # 🪞 自我母題跨重啟（抱怨歸因；附 ts 限窗）
        }
        # 🧠 §1.21 上次自陳：只在真的寫過（旗標開、送出過自陳）才落鍵——旗標關＝state.json 逐位元同現狀
        if self.last_self_report is not None:
            d["last_self_report"] = self.last_self_report
        if self.mood_last_report is not None:
            d["mood_last_report"] = self.mood_last_report
        # 🕘 §2.22 作息端上桌的 stamp：同上，只在真的寫過才落鍵（旗標關＝state.json 逐位元同現狀）
        if self.greet_routine_ts:
            d["greet_routine_ts"] = self.greet_routine_ts
        if self.evidence_memory is not None:
            d["evidence_memory"] = self.evidence_memory
        if self.conscious_dialogue is not None:
            d["conscious_dialogue"] = self.conscious_dialogue
        # 原子寫入，避免被中斷時留下半截檔
        directory = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    def absorb(self, snapshot):
        """把目前快照當「已知基線」：所有已成形項都標為 seen、近升格指紋與心跳狀態對齊。

        每日摘要或上線基線後呼叫——之後只有「真的新發生」才會觸發事件推播。
        """
        self.seen_journey_ids |= {j["id"] for j in snapshot.formed_journeys}
        self.seen_context_ids |= {c["id"] for c in snapshot.formed_contexts}
        self.near_upgrade_sig = dict(snapshot.near_upgrade_sig)
        self.heartbeat_state = snapshot.heartbeat["status"]
        self.notified_filing_ids |= {r["id"] for r in snapshot.filed_records if r.get("id")}
