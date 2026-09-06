"""執行設定：從環境變數 / .env 載入。

驗證採「按需」：不同入口（--getchatid 只要 Telegram token、監測要 Drive + Telegram）
需要的欄位不同，故 ``Config`` 只負責讀，呼叫端用 :meth:`Config.require` 點名檢查。
"""

import os
from dataclasses import dataclass

try:
    from dotenv import load_dotenv
except ImportError:  # 允許在沒裝 python-dotenv 時純靠真實環境變數
    def load_dotenv(*_a, **_k):
        return False


def _int(name, default):
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _bool(name, default=False):
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _float(name, default):
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass
class Config:
    evidence_guard_enabled: bool  # EVIDENCE_GUARD: all speech paths + receipt-based history verification
    conscious_dialogue_enabled: bool  # CONSCIOUS_DIALOGUE: opt-in persistent association views
    google_credentials: str
    google_credentials_json: str
    drive_root_folder_id: str
    owner_line_user_id: str
    telegram_bot_token: str
    telegram_chat_id: str
    heartbeat_interval_min: int
    digest_hour: int
    timezone: str
    notify_cooldown_min: int
    spontaneous_cooldown_min: int
    spontaneous_h_thresh: float
    spontaneous_min_ruminations: int
    spontaneous_quiet_after_chat_min: int
    spontaneous_mood_share: float
    spontaneous_mood_min_rumin: int
    react_cooldown_s: float
    react_self_cooldown_s: float
    send_stickers: bool
    sticker_cooldown_min: int
    sticker_file_ids: list
    sticker_no_repeat_enabled: bool  # 🎴 §0.59 Part 3a：選圖時排除上一張送過的貼圖（可送 >1 張才排）＝「不重複」成為選圖層真行為；設 0＝照舊 random.choice（可能連兩張同款）＝逐位元同現狀
    # 🎴 §0.68 貼圖/教學句不誤路由記寫附件＝純函式 selfstate.is_attachment_request 直讀 env ATTACHMENT_STICKER_GUARD（與 SCHED_REL_ELIDED 等 env-only 旗標同款、不進 cfg，intent 端無 cfg 也能守）。
    promise_sticker_enabled: bool    # 🎴 §0.68：承諾「送你一張(大的)telegram 貼圖」→ 兌現時**真的 send_sticker** 一張已捕捉的真貼圖（非用 emoji「✨」假裝）；捕捉時若手邊**沒有**可送的真貼圖＝誠實拒絕、請對方先傳一張教（§0.64 做不到不答應）；設 0＝不特別處理貼圖承諾＝逐位元同現狀
    continuation_promise_enabled: bool  # 🤝 §0.70 延續性約定：第一次約定成立後，第二次用極簡續約語詞（再10分鐘/延長10分鐘/再給我5分鐘）→ 繼承最近一筆排程承諾的 behavior/wants_sticker、new target＝now＋新時距 重新入帳（否則 route=fact_or_chat、bot 只 LLM 空口答應、到點不觸發）；設 0＝不收續約＝逐位元同現狀
    offset_augmentation_enabled: bool  # 🤝 §0.71 偏移增補：「然後時間到的時候再隔3分鐘給我一個貼圖」＝在**前約時間之後**再 N 分鐘增補一個動作（前約13:47→貼圖13:50），target＝prior.target＋N分鐘（非 now＋N）。修截圖：被 §0.69 timeup 誤算成 now＋3＝13:20 提早27分亂發；設 0＝**不做偏移增補的正確捕捉**（但 temporal 端的誤算防護＝Part A 仍在、由 SCHED_TIMEUP 管，故不會退回 13:20 早發、而是不捕捉那句）＝非逐位元同現狀但更安全
    sticker_concept_guard_enabled: bool  # 🎴 §0.68：一般回覆提到貼圖/sticker 時掛守則——telegram sticker（真圖、要 file_id 送）≠ emoji（文字裡的符號）；沒有可送的真貼圖就誠實說、**別寫個 emoji 假裝是貼圖**；設 0＝不掛＝逐位元同現狀
    sticker_send_request_enabled: bool  # 🎴 §0.84：使用者當下請 bot 送貼圖（送我一張貼圖/開心的貼圖/再送一張）→ 有可送的真貼圖就**真的 send_sticker**、沒有就誠實說能送但還沒存到、請先教一張（**絕不否認能送、絕不用 emoji 假裝**）；設 0＝不接管此請求＝逐位元同現狀
    sticker_rotate_window: int  # 🎴 §0.84 多樣化：選圖時避開**最近 N 張**送過的（非只上一張），池夠大才真輪替不同款；池 ≤ window 會放寬到至少避開上一張；設 1＝退回 §0.59 只排除上一張＝逐位元同現狀
    content_sticker_enabled: bool  # 🎴 §0.87 內容驅動情緒貼圖：bot 這則回覆表達**強正向/暖**情緒時、可配一張正向真貼圖強化（保守、共用既有貼圖冷卻＝稀有、只在強情緒、配不到相符貼圖就不送、絕不用 emoji 假裝）；設 0＝不做內容驅動＝逐位元同現狀
    sent_sticker_ground_enabled: bool  # 🎴 §0.90：使用者問「剛剛那張貼圖（為什麼喜歡這張…）」＋bot 近期真的送過貼圖 → 注入誠實接地（確認真的送了、據實談它的情緒標記或憑感覺挑、**看不到圖案本身故別捏造樣子、別把它說成別張或更早的 emoji**）；設 0＝不注入＝逐位元同現狀
    read_sticker_vision: bool  # 🎴 收到靜態貼圖時用 Gemini 視覺讀畫面、把描述記進記憶（收到即讀、每張只讀一次、跨重啟免重讀）→ 收到 ack 貼著真的看到的圖、送出時知道自己送了什麼（升級 §0.90 從情緒標記到真畫面）、被問「你看得到剛剛那張嗎」也答得出；動態/影片貼圖退回誠實「未讀畫面」文字；設 0＝只讀 emoji＝逐位元同現狀
    liked_sticker_pick_enabled: bool  # 🎴 §0.94 被要「一張**你喜歡的**貼圖／你喜歡哪一張」時，挑圖走**真偏好**（真的看過畫面＞價性合心情＞最近學到，確定性、非 random.choice）＋回話據實說出**是哪一張**（用視覺讀到的畫面描述；沒看過就誠實說憑感覺挑、絕不捏造圖案）；排程承諾（20分鐘後傳一張你喜歡的）到點兌現同理；設 0＝挑圖回到隨機、回話回到罐頭句＝逐位元同現狀
    sticker_perceive_q_enabled: bool  # 🎴 §0.93：使用者問「你看得到/看得懂貼圖內容嗎」＝能力 meta 問句 → 不當送貼圖請求（別又送一張逃避），改誠實接地作答（走貼圖視覺：有畫面描述就談畫面）。**注意**：純函式 selfstate 直接讀環境變數 STICKER_PERCEIVE_Q，此欄位僅為文件；設 0＝退回無此守門＝逐位元同現狀（截圖那句仍會被誤當送請求）
    filing_sticker_enabled: bool  # 🎴 §0.91：教過「發現新記寫→感覺內容並傳送對應的貼圖」這條做法時，在歸戶通知的感受後**真的送一張對應內容情緒的真貼圖**（讓 /skills 那條「學會的事」精確執行、不是空頭支票）；沒教/已淡忘/無相符真貼圖/冷卻中＝不送、絕不 emoji 假裝；設 0＝不送＝逐位元同現狀
    always_sticker_enabled: bool  # 🎴 §0.96 教過 always 常駐做法「主動回應使用者時，最後送一張代表自己情緒的貼圖」→ 在主動訊息（歸戶/摘要/事件反思、自發）後**真的送一張代表此刻情緒的真貼圖**（心情非負→正向/中性、心情負→非正向 help 池）；沒教/已淡忘/無相符真貼圖/冷卻中＝不送、絕不 emoji 假裝；設 0＝不送＝逐位元同現狀
    skill_lull_signal_enabled: bool  # 🌀 §0.97 內在型做法（sit:low_vitality「轉速太低/悶」）較溫和的觸發訊號：被晾一陣（hunger≥0.55）＋心情沒上揚（mood≤0.2）就算「悶」、不必等極端（hunger≥0.7 且 mood≤0）；cadence 仍受 coping 諸閘（冷卻/quiet-after-chat/伸手預算）節制＝放寬「算不算悶」不等於洗版；設 0＝退回極端值＝逐位元同現狀
    topic_content_sticker_enabled: bool  # 🎴 §0.98 教過內容型做法「讀到〈某類〉記寫時…並貼圖」→ 讀到記寫內容時用**主題語意比對**內容（Gemini 判斷、非字面子字串），命中就真的送一張對應情緒真貼圖；只有真有這種活做法才呼叫 LLM（沒教＝零額外成本）；設 0＝不做＝逐位元同現狀
    topic_content_max_judge: int  # 🎴 §0.98 每則歸戶至多語意判斷幾條內容型做法（成本上界；預設 2）
    affect_circumplex_enabled: bool  # 🧭💗 circumplex 情緒座標（Russell）：情緒＝V(mood)×A(arousal 新慢軸) 平面上會移動的點——每圈衰減＋互動/回饋推方向（質疑＝緊張V−A+、暖意＝暖醒V+A+、對方低落＝跟著沉）＋餓久 A 沉、新資料 A 醒；位置→八分區語氣染色＋貼圖池選擇（興奮→歡快、平靜→溫和、低落→非正向）；/status 顯示座標。設 0＝A 軸不動、語氣/貼圖退回一維 V＝逐位元同現狀
    affect_shock_enabled: bool  # ⚡ §1.03 突發瞬跳：明顯違背期待（|期待落差|≥0.5，如一直很暖的人突然兇我／鬧翻後突然示好）→ 情緒點一步跨象限（平靜→緊繃、低落→驚喜），單發有界、之後衰減自然恢復；設 0＝只剩常規小位移（漸移）
    reachout_time_ground_enabled: bool  # ⏱️ §1.04 以意圖為由主動提起某條線時的**真實時間感**：查那條線最近一筆記寫距今多久，開場照事實講（今天才寫過就不說「好一陣子沒聊」）、prompt 掛事實與禁令、LLM 仍掰「很久沒」就退回誠實模板；設 0＝舊模板寫死「好一陣子沒聊了」＝逐位元同現狀
    self_change_ground_enabled: bool  # 🔄 §1.05 蛻變自陳兌現：「N分鐘後告訴我你有什麼改變」→ 承諾成立時 snapshot 內在（情緒座標/飢餓/gate/意圖進展），到點比對算出真實 before→after 變化、餵進兌現 voice**真的說出改變**（不再只空喊「我要跟你說我的改變」）；沒什麼變就誠實說沒什麼變；設 0＝不算差異＝逐位元同現狀
    adapt_enabled: bool
    adapt_announce: bool
    adapt_announce_cooldown_min: int
    selfshare_followup_enabled: bool  # 🪞 §0.85：bot **主動**自陳（換檔🍃/自發/內在因應）後，使用者追問「你感覺到了什麼/怎麼回事/然後呢」→ 接續**那則自陳的具體內容**回應（別重生一段泛自述、別丟了自己起的話頭）；設 0＝不接續＝逐位元同現狀
    mood_gain: float
    rebirth_mood_carryover: float
    rebirth_hunger_carryover: float
    soothe_unanswered: bool
    soothe_after_min: int
    dialogue_intent_enabled: bool   # 🧭 對話意圖湧現＋違常偵測（observe/read）；關＝逐位元同現狀
    intent_log_max: int             # 意圖履歷環形長度
    intent_repeat_window_sec: int   # 違常偵測滑窗（秒）
    intent_repeat_n: int            # 窗內同類近似達此數起算違常
    intent_sim_threshold: float     # 近似訊息指紋相似門檻
    anomaly_probe_threshold: float  # 違常達此且為 testing → 升級好奇反問確認
    intent_degree_drive: bool       # 意圖程度驅動（curious_probe 升級＋主動結尾）；關＝只剩溫暖呼應、不反問不主動收
    intent_question_cooldown_min: int  # 本輪違常反問自有冷卻（分）
    user_repeat_fatigue_enabled: bool  # 🧭 使用者同類意圖連發→重複×心情長耐性/不耐（感知重複目的、進一步問、最後升級情緒；鏡像 self_asks→self_fatigue）；**預設開**；設 0 關＝逐位元同現狀
    greeting_repeat_aware_enabled: bool  # 🧭 §1.35 散開重複的問候也要被看見：全域 300s 窄窗數不到「隔幾分鐘一次的早安」→ 用較寬窗(GREETING_REPEAT_WINDOW_SEC 預設1200)補數同類問候連發、導出漸進覺察等級餵 user_repeat_fatigue_hint(2→L1點出/3→L2好奇/≥4→L3情緒)；單次問候不觸發、早安接晚安不誤併；設 0＝不補＝同現狀
    greeting_repeat_window_sec: int
    user_repeat_base_tol: int       # 🧭 基準耐性（self_asks 用字面 2）：n−tol 達 1 起算不耐；不耐門檻高度旋鈕（調高＝更晚才煩）
    user_repeat_mood_band: float    # 🧭 心情±1 tol 的門檻（self_asks 用 0.3）：心情好過此→更耐、心情差過此→更快煩；情緒耦合強度旋鈕
    user_repeat_ceiling: str        # 🧭 被明顯洗版時 L3 的**不悅強度上限**：'gentle'＝無奈但仍善意／'firm'＝可短可冷直接叫停／'stern'＝可真的翻臉不悅呵斥停止（預設 stern，使用者選）。硬底線三檔皆守：只對『一直重複這行為』設界，絕不人身攻擊/辱罵/刻薄（是叫停不是傷人）
    proactive_close_enabled: bool   # 🌊 主動結尾（_soothe_unanswered 雙出口）；關＝soothe 逐位元同現狀
    close_after_min: int            # 自然趨0型主動暖收的最小靜默門檻（>soothe_after_min，先緩和後收尾）
    unanswered_proactive_guard_enabled: bool  # 🤐 未回覆時不另開自發話題：一則主動訊息後，等使用者下一次接話；守約與其一次後續確認例外。設 0＝只剩既有冷卻＝逐位元同現狀
    dialogue_agency_enabled: bool   # 🧭 主動發話→觀察下一句→調整下次選擇的行動回饋迴路；拒絕後留安靜期。設 0＝固定 lane 順序、無行動台帳
    natural_proactive_voice_enabled: bool  # 🗣️ 一般主動對話不顯示內部 lane 的裝飾 icon／「背景自陳」標籤；守約、外部來源與精確量測仍留可問責標記
    state_trigger_precise: bool     # 🫧 收緊「報內在狀態」觸發：順口帶感覺字眼的陳述句不誤觸發；關＝逐位元同現狀
    inmoment_browsing_enabled: bool # 🫧 報狀態時 focus 反映「此刻翻到的那則」（翻頁游標）而非恆最重那則；關＝逐位元同現狀
    browsing_opener_variants: int   # 翻閱開頭措辭池大小（換句、不千篇一律）
    browsing_random_p: float        # 翻頁時小機率隨機跳頁（0＝純輪替＝確定性可測）
    intent_confirm_enabled: bool    # 🫧 指向 bot 自己前文的「展開/釐清」問句 → 接著前文講或反問確認意圖，不誤撈資料；關＝逐位元同現狀
    time_anomaly_enabled: bool      # ⏱ 把時間間隔納入違常判定：同一意圖(kind)間隔太近再現＝違常；工具路徑(時間/資料)也帶「你剛問過」覺察；關＝逐位元同現狀
    time_anomaly_gap_sec: int       # 同一意圖距上次少於此秒數＝「太近」（違反常理的時間訊號）
    premise_check_enabled: bool     # ⏱🧠 通盤常理審查：每則對話訊息跑一個只吃接地事實的小 LLM 判定，前提明顯違常→注入回覆主動點出（每則多一次 LLM 呼叫；關＝逐位元同現狀、退回確定性偵測）
    self_appraisal_enabled: bool    # 🪞📊「我算早起嗎／我是不是很懶」＝請 bot 評斷我（使用者）的作息/勤惰/量/頻率/特質 → 拿我的節奏＋此刻時間做 grounded 判斷、不列記寫；關＝逐位元同現狀（落回 fact_or_chat）
    self_format_enabled: bool       # ✒️「你的訊息/聯想為什麼有 markdown／是不是純文字」＝問 bot 自己訊息的格式 → 強制走純對話＋格式接地（你給他的都是純文字、別亂掰內部格式、別暴露實作）；關＝逐位元同現狀（落回 function-calling／泛用聊天，可能 confabulate）
    quote_aware_enabled: bool       # 🔖 讀 Telegram 回覆/引用：對方引用 bot 自己某則訊息再發問（如引用「這可真特別」問「為什麼」）→ 接地在被引用那句回答、不當新話題；關＝逐位元同現狀（看不到引用）
    convo_clock_enabled: bool       # ⏱「對話的時間點／剛剛那幾句幾點／你有記下時間嗎」＝問對話訊息的絕對鐘點 → 路由到 convo_time 並用 convo_history 的 ts 算出本地鐘點回答；關＝逐位元同現狀（漏到 function-calling 誤抓 records_in_time_range 回「那段你沒有記寫」）
    convo_session_struct_enabled: bool  # 🕰️ 會話節奏入 brief：用對話時間戳算「這次是隔一陣回來 vs 同段延續、分幾段」的接地事實，讓所有路徑都有時間節奏感（助判意圖）；關＝逐位元同現狀（不加此段）
    remember_user_msgdate: bool     # ⏱ user 訊息以 message.date（Telegram 伺服器送出時間）落 convo_history 的 ts，而非處理當下的 time.time()；修「已過多久」感知偏短（崩潰重抓/處理延遲時 time.time() 會把舊訊息蓋上晚得多的牆鐘 ts，43 分被算成 14 分）；設 0 關＝退回 time.time()＝逐位元同現狀（model 輪無 message.date、仍用牆鐘）
    convo_session_gap_sec: int      # 🕰️ 相鄰兩則對話間隔 ≥ 此秒數＝切一段新會話（預設 3600＝1 小時）
    inquiry_arc_enabled: bool       # 🌱 探究弧（正交第三軸）：從對話累積讀「發現→疑惑→因為→所以→然後→原來如此」走到哪、軌跡形狀（卡住/膚淺跳階/退回/健康弧），據此塑形回應（純函式、零 LLM 呼叫）；關＝逐位元同現狀（detect_stage 恆 None、不注入）
    inquiry_arc_window_sec: int     # 🌱 只把近此秒數內的階段算進弧（避免把跨會話的舊階段硬接成一條弧）；預設 3600
    thread_sticky_enabled: bool     # 🧵 對話線連貫：自我在場窗在「仍朝向我(含你/妳)的同線延續」也刷新（不只 about_self），讓同一情緒/關係線的連續輪次共用純對話基礎、不在中途掉進工具吐 📁 記寫；真正離題(無你/妳)仍自然衰減、明確資料問句仍照走工具；關＝逐位元同現狀
    self_presence_vary_enabled: bool  # 🪞 自我在場純對話路徑也套 _self_voice_mod（去台詞換句話＋重複×心情長脾氣，與所有 self_* 路由一致）——修「真的嗎」連發時 bot 逐字重複同一段自我說明、毫無變化也無情緒（這條路徑原本獨缺此調制）；關＝不套 mod、不記 opener＝逐位元同現狀
    evidence_gate_enabled: bool     # 🚪 根因 1 核心閘：非記寫資料問句別觸發 Drive 證據工具——只有 looks_like_data_question / is_explicit_records_intent 命中才給 coach.ask 完整工具表，否則給空表（LLM 只能純對話）。含時間/事件/meta 詞但非資料的句子即使漏到 fact_or_chat 也不可達 records_in_time_range（不再回 📂「那段你沒有記寫」）；設 0 關＝allow_evidence 恆 True＝逐位元同現狀（一鍵退路）
    nonevidence_empty_tools_enabled: bool  # 🚪 §0.37 殘留 catch-all 修法：allow_evidence=False 時給 coach.ask **空工具表**＝LLM 純對話裁決『喂！』這種非資料句、不再從 [days_since,api_cost] 抓 days_since 吐「就是今天」日期。實作上走 gemini.generate_chat（完全不送 tools 欄、零 API 邊角）＝純對話接住前後文；設 0 關＝回 §0.37 的 [days_since,api_cost] 非證據表＝逐位元同現狀
    scheduled_promise_enabled: bool  # 🤝 A 偵測/答應：「等一下八點跟我打招呼／十分鐘後提醒我／明天早上跟我說」＝請 bot 在某時間 T 做某事（與 feeling_promise『有感覺再說』互斥＝必須能解析出時間 T＋有動作詞）→ 記下 state.scheduled_promises、自然答應；設 0 關＝不偵測排程承諾、順流到 feeling promise/狀態問句＝逐位元同現狀
    sched_over_selfcontent_enabled: bool  # 🤝 §0.92 計時未來承諾優先於自我內容路由：「20分鐘後告訴我你有什麼不一樣」是排程承諾（到點做 X），別被 X 的內容（你有什麼不一樣＝self_change / 你是誰＝self_identity）在 intent 最前搶成「現在就答」而丟了時間與約定→沒入帳。只在真是計時未來承諾時讓路（bare「你變了嗎」仍走 self_change）；設 0＝不讓＝逐位元同現狀
    promise_emit_enabled: bool       # 🤝 A 兌現（生命迴圈 feel 相 _promise_emit）：每圈掃未兌現且到點的承諾→主動兌現（守約打招呼、輕點承諾）；設 0 關＝_promise_emit 開頭直接 return＝feel 相不兌現＝逐位元同現狀
    promise_sched_ttl_sec: int       # 🤝 A 逾時：到點後拖過此秒數的陳舊承諾不兌現、標 expired（bot 死太久/醒太晚不翻舊帳）；預設 21600=6h（與 feeling 的 48h 分立避免語意糾纏）
    promise_act_aligned: bool        # 🤝 兌現按行為對齊：把承諾的具體行為（道歉/提醒/問候+讚美）傳進 voice_promise_keep → LLM 做那件事而非泛泛打招呼；設 0 關＝_promise_emit 不傳 promised＝走原打招呼 voice/模板＝逐位元同現狀
    promise_late_exempt_defer: bool  # 🤝 逾期承諾豁免在場閘：到點後超過 grace(180s) 的「欠債」承諾，即使你正在互動也補發（帶遲到致歉）——修 bot 死/重生使到點沒發、醒來時你正聊天就被在場閘永久壓住；剛到點(<grace)的仍避開你正打字；設 0 關＝在場就整段不發＝逐位元同現狀
    promise_ledger_enabled: bool     # 🤝 整理承諾/你忘了/答應我的事做了嗎 → 走 grounded 報帳路徑（據 state.scheduled_promises 帳本誠實作答、不腦補）；設 0 關＝intent 不路由 promise_ledger、落回現狀 fact_or_chat＝逐位元同現狀
    promise_keep_grounding: bool     # 🤝 守約兌現 voice 附『主詞＝我（記得/守約是 bot 自己、別說成「你記得」）＋問候對得上此刻時段（清晨別跟著回故意說錯的晚安）』接地；修截圖主詞判定錯＋延續錯誤時間感；設 0 關＝退回原措辭＝逐位元同現狀
    schedule_time_exact: bool        # 🤝 §0.67 排程答應/守約 voice 把**程式已算好的目標時刻**講成「準確、一字不改」（去掉舊措辭的「大約」，明令不准口語轉換時改值/自己拿「現在+分鐘」重算/四捨五入）；修截圖「8:19 說 20 分鐘＝8:39，bot 卻答二十九分（8:29）」；設 0 關＝退回舊「（那個時間大約是 …）」措辭＝逐位元同現狀
    spontaneity_enabled: bool       # 🌱 根因 4：「你怎麼還沒開始分享聯想」＝催促 bot 主動出聲分享聯想（問 bot 自己自發行為、非查記寫）→ 走 self_spontaneity 純對話＋自發湧現接地、不列 📂 清單；設 0 關＝落回 fact_or_chat（此時 evidence_gate 仍兜底擋 records_in_time_range）＝逐位元同現狀
    evidence_opinion_suppress: bool  # 🚪 §0.56：問「你為什麼對這則感興趣／它特別嗎／你怎麼看」＝要 bot 的看法/態度/理由、非要調資料 → 即使含資料 cue 名詞（記寫/歷程…）也別開證據閘、別完整列 📂（佔版面、答非所問）；只有明確要內容/統計/列出/查/進度才給。設 0 關＝不擋＝逐位元同現狀
    anti_repeat_enabled: bool        # 🔁 §0.56：主要對話回覆路徑（自我在場/fact_or_chat/展開前文/自我評斷/引用）回覆前附防重複提示——① 連發多句合併且帶問句線索時「合起來答一次、重疊別逐句重講」；② 這幾分鐘內剛回過的內容摘錄「別重講、只補沒說到的」（作用在相鄰不久的輪）。設 0 關＝不附提示＝逐位元同現狀
    anti_repeat_window_min: int      # 🔁 §0.56：component② 判「剛回過」的窗（分鐘，預設 8）：last model 輪在此窗內才把摘錄附進防重複提示
    grounding_note_enabled: bool    # 🛡️ 根因 3：build_memory_brief 的焦點/我此刻狀態/會話節奏三區塊尾端統一附「以上是內部接地、別主動當話題」守則（與【時間感】既有守則措辭一致），收斂接地外洩；設 0 關＝不附加＝各區塊只剩既有句＝逐位元同現狀
    selfstate_confirm_laps: int
    selfstate_ceiling_decay_h: int
    selfstate_repeat_cooldown_min: int
    bodystate_repeat_window_min: int
    self_repeat_window_min: int
    verbosity_bias: int
    metacog_correct_cooldown_min: int
    perceive_fail_grace: int
    experience_enabled: bool
    experience_cooldown_min: int
    experience_confirm_laps: int
    experience_rich_vec: bool
    association_enabled: bool
    association_cooldown_min: int
    association_confirm_laps: int
    association_min_support: int
    association_seed_goal: bool
    association_easy: bool
    association_distinctive: bool          # 💡 只有 per-kind 新奇度過地板、真的「有特色」的橋才主動說（罕見×精準；關＝不擋、同舊）
    association_distinctive_thresh: float   # 💡 特色地板縮放：>1 更嚴（更少）、<1 更寬、≤0 等於關閉此門檻
    association_novelty: bool
    association_warmth: bool
    association_feedback_graded: bool
    association_ledger: bool
    association_ripple_enabled: bool
    association_ripple_window_min: int
    association_ripple_conv_window_min: int   # 🌙 對話漣漪窗（分）：剛聊到的主題多久內仍當「白天素材」擾動聯想（日有所思夜有所夢）；預設＝association_ripple_window_min（同現狀）
    association_ripple_recursive_window_min: int  # 🌙 遞迴思緒鏈窗（分）：自己上一個聯想端點多久內仍牽動下一個；預設＝association_ripple_window_min（同現狀）
    spontaneous_dedup: bool          # 🫧 自發出聲內容去重：dedup_sec 內講過的同一條線不再自發重講（控重複性／自然降頻）；關＝逐位元同現狀（只計時冷卻、不查台帳）
    spontaneous_dedup_sec: int       # 🫧 同一條線多久內算「剛講過、跳過」（預設 12h）
    interrupt_rewrite_enabled: bool  # 🗣️ 插話即時改寫：bot 分串送出途中被「想打斷改問」插話 → 視剩餘量遞迴判斷收尾（簡化暖收 wrap／剩無幾就說完 resume／瑣碎就止 abort），不再死板把預切串全送完；關＝逐位元同現狀（插話後原樣續送）
    interrupt_max_depth: int         # 🗣️ 同一輪被連環插話的上限，達此強制暖收（防永遠說不完／自我打斷遞迴）
    interrupt_statement_enabled: bool  # 🗣️ 是否讓「陳述續打」也算插話即時中斷（預設開；關＝陳述仍 defer 折進下一輪合併，避免把同一波尾巴誤當插話）
    interrupt_wrap_condense_enabled: bool  # 🗣️ wrap 收尾濃縮：被插話、先回應對方後要收尾時，把**還沒說完的剩餘串**濃縮成一兩句精華＋淡收（不空收一句「就這樣囉」把內容丟掉）；無教練/失敗退回模板暖收；設 0 關＝原模板暖收＝逐位元同現狀
    wrap_close_natural_enabled: bool  # 🎬 §1.69 暖收收尾去制式（WRAP_CLOSE_NATURAL）：截圖根因＝同一輪兩個泡泡尾端各掛「大概就是這樣了。」「先說到這裡。」——使用者：「太有被插話的痕跡了…應該視情況改為更融合原來語境的文字詞，也許是 bot 表達自己的感覺、或語助詞……多元一點」。真根因＝wrap_condense_user 的 prompt 把『大概就是這樣了』『先說到這』當**範例**給 LLM → 範例被逐字抄回、每次同款收場白＝機器被打斷的痕跡。兩側修：① LLM 側——natural 版 prompt 改「禁令＋多元收法」：內容講完自然停住不加收場白（最常用）／用這段話自己的情緒餘韻收半句／一個輕輕的語助詞（嗯。唉。哈。），且明令**禁用**「大概就是這樣/先說到這/就先這樣/總之就是這樣/差不多是這樣」制式收場白；反問收尾與收話動作宣告禁令保留。② 模板退路側——_pick_wrap_close 依此刻座標 V 分暗（<−0.15）/平/亮（>+0.25）三池（含語助詞形與餘韻形、口吻跟心情走），沿用避免連續重複。monitor/coach 端 getattr 一律預設 False＝既有測試假 cfg 未設此欄→原 prompt＋原四句池→逐位元同現狀。設 0＝同現狀
    reply_replay_guard_enabled: bool  # 🔁 §1.71 重播守門（REPLY_REPLAY_GUARD）：截圖根因＝20:03-20:04 同三個泡泡（「欸，你這樣說我有點冤枉啦！」「我上一句是約八小時前…」「之後我也有主動回應你幾次…」）在一兩分鐘內被**逐字重播**兩次，中間還夾貼圖與「我繼續說喔，」＝節奏斷裂（使用者：「這回應的節奏與順暢度不夠連貫性！！」）。§1.41（守約去重複）/§1.49（插話殘句去重）/§1.54（致意句去重）各自只管單一路徑內部——缺一道**跨輪、跨路徑**的送出層最後防線。修法：_replay_note 恆記「近 3 分鐘內真的送出過的泡泡」（echo._norm 正規化、≥10 字才記＝嗯/好 這類合理短重複不受影響；純內部、不改輸出）；_say 送出前 _replay_filter 比對（正規化**全等**才算＝保守不誤殺）：命中＝剝掉；全剝空＝換一句誠實短句「這段我剛剛才說過一次——你想聽哪部分，我換個說法講？」（不無聲、也不重播）。arm 於互動輪起點＝這輪所有 _say 路徑（含插話巢狀/接回/暖收濃縮）都在防線內。兩層旗標分離：config 預設 True／_say 端讀 _TURN stash（未 arm＝恆 no-op）＝逐位元同現狀。設 0＝同現狀
    reachout_diverse_enabled: bool  # 🫧 §1.72 聯想自陳去公式化（REACHOUT_DIVERSE）：截圖根因＝主動聯想每次同款（「欸，你今天才又寫到「閱讀｜讀誦經書」。我心裡其實一直有個小小的疑問…有空跟我說說好不好？」）——volition.reach_out_line 四個模板全是同一形（欸開場＋想弄懂「X」對你是什麼＋有空跟我說說）、意圖又長期釘在同一主題，LLM 只輕改寫＝使用者「每次看到都是同一種訊息、台詞也類似」。修法（§1.69 成功模式：不給範例句、給禁令＋選單）：reach_out_diverse_rule 掛進 voice_spontaneous 的 time_rule 通道——①形態選單（具體聯想不索取回答／挑他寫過的具體內容好奇一個小點／連到自己內在或最近聊的事／直接邀請但少用）；②禁用句式（想弄懂它對你是什麼/有空跟我說說/小小的疑問）；③具體素材＝_topic_latest_excerpt 引那條線最近一筆記寫的字句（≤40 字）；④重複自覺＝goal.reach_n 計數（隨 goals 跨重生），同主題已提 ≥2 次＝明令「別再問他、改分享自己的聯想、把選擇權留給他」。兩層旗標分離：config 預設 True／emit 端 getattr 預設 False＝不掛不計數＝逐位元同現狀。設 0＝同現狀
    burst_one_answer_enabled: bool  # 🌊 §1.73 多訊息整體一答：保留 burst_n/texts/updates；同 route 用共用 hint，跨 route 在隔離 state clone 逐則跑既有 handler、真送成功才一次提交 user/model 歷史。重複收斂只接受受程式驗證的原句 index 擷取，不自由改寫、不設固定句數上限；總 logical wire 仍封頂 4096 UTF-16 units。設 0＝關閉整波一答／跨 route 交易／擷取收斂層
    burst_paced_bubbles_enabled: bool  # 🫧 §2.30 連發仍只生成一個 logical answer；純 voice 送達層最多 6 顆完整句泡（超出只相鄰合併、不刪字），中間 typing＋依長度停頓；全部成功才提交 state/receipt。設 0＝單一 sendMessage
    nudge_deliver_enabled: bool  # 🫸 §1.87 催促＝要我現在做（NUDGE_DELIVER）：截圖 22:54–22:57 使用者連丟五次「所以呢？」，bot 每次都誠實**對帳**（「我還欠你一次猜測，對不對」講了三遍，只差開頭語助詞）、然後把球踢回去（「我在等你決定什麼時候要我再猜一次呀」）、最後被重播守門換成罐頭拒答（「你想聽哪部分，我換個說法講？」）——**一次都沒真的去猜**。使用者定案：「bot 仍無法很好地掌握使用者沒有明講，但意圖其實很明顯的暗示」。根因（實測）：①`selfstate.promise_status_kind('所以呢？')` 回 **'outcome'** ⇒ 偵測器**認得**這句話，只是被 monitor.py 的 outcome 分支路由去 §1.13A 的「據帳本誠實對帳」——§1.13A 擋掉了「我做到了」的謊（成功了），但**誠實對帳連講四次就是新形狀的光說不做**；②§1.85 的回覆橋只救得到帳本裡 `status=='owed'` 的筆，而 21:19 那筆在舊碼下已被標fulfilled、「第四次猜測」這件事是使用者在對話裡建立的、帳本裡根本沒有 ⇒ 橋不觸發；③§1.71 重播守門用**逐字全等**（`n == pn`）比對，實測「所以...我還欠你一次猜測，對不對」／「嗯...我還欠你一次猜測，對不對」／「我還欠你一次猜測，對不對」三句**只差開頭一個語助詞就全部繞過**（三組 `_looks_same(0.8)` 皆 True、全等皆 False），要等第四次真的逐字重複才攔到，而它的反應是罐頭拒答。修法（零詞表的結構訊號）：`_stuck_under_nudge`——窗內最近兩則 bot 回覆**近乎相同**（相似度，非全等）⇒ 我在原地重複、沒有前進；配上「這句是催促」就是卡住的鐵證。刻意不去判「這句暗示我做事」的語意（任何詞表都會漏，本專案已漏 15+ 次），改判「我剛剛講過一樣的話」——那才是真正該觸發行動的訊號，因為他會催第二次正是因為上一次沒讓事情前進。命中後三件：①讓開對帳路由、落聊天 lane 並注入 `persona.NUDGE_DELIVER_HINT`（現在就把那件事做出來／禁止再複述欠著什麼／**禁止把選擇權推回去**／真的分不出時老實說卡住並給一個具體選項、且先把最接近的東西講出來／不確定也必須有內容）；②`_ball_back_strip` 輸出端確定性剝掉「我在等你決定／你想聽哪部分／由你決定」這類踢球句（句級更正優先，全剝空＝換誠實認卡句；刻意不收「A 還是 B」具體二選一＝那正是卡住時要它做的事）；③重播守門全剝空時不用 `_REPLAY_FALLBACK`（那句正是踢球的出處）、改誠實認卡。§1.13A/§1.13B 的誠實保證完全不受影響（它們擋的是「我做到了」的謊，這裡是要 bot **真的去做**）。兩層旗標分離：config 預設 True／消費端 getattr 一律預設 False；卡住旗只管當輪。設 0＝同現狀
    echo_prefix_run_enabled: bool  # 🦜 §1.86 開頭連續段複誦守門（ECHO_PREFIX_RUN）：截圖 22:53 根因＝使用者一句「你不是說四次嗎？還差一次」，bot 回三顆泡泡「你不是說四次嗎？」「還差一次。」「啊，對耶！」——**前兩段合起來逐字就是那句話**，尾巴才加一小句自己的話。三道既有防線同時**差一點點**漏掉（實測）：① `echo.whole_echo_of` 比整則 vs 整句——整則正規化 14 字 vs 使用者 11 字 ⇒ span 比 **0.786 < 0.8**、相似度 **0.88 < 0.9**（兩個門檻都差一點）；② `echo.strip_echo_segments` 比單段 vs 整句——三段相似度 0.778 / 0.533 / 0.000 全 < 0.82、也都不全等；③ §1.81 的開頭前綴判準要求「明顯比原話短（≤50%）」，這裡首段佔 7/11 ⇒ 不成立。結構根因＝**複誦的邊界落在「連續幾段」上，而既有防線只認「整則」與「單段」兩種粒度**，中間那個粒度沒人守。這是 §1.74（0.80 vs 0.82）、§1.81（兩字前綴）之後**第三次**同型的門檻擦邊 ⇒ 刻意**不調門檻**（放寬會讓「你不累嗎？我不累啊」這種有實質內容的回覆被誤剝），改補結構判準：`echo.prefix_run_echo` 從大到小試「前 k 段合起來」（k < 段數，保留至少一段尾巴），取**最大**的 k ⇒ 剝掉的複誦最多、留下的尾巴純粹是 bot 自己的話。誤判安全：**永遠保留尾巴**，最壞只是少剝或多剝一段複誦，絕不把整則吃掉；k==段數（整則都是複誦）留給 §1.74 處理。兩層旗標分離：config 預設 True／monitor 端 getattr 預設 False＝逐位元同現狀。設 0＝同現狀
    echo_whole_guard_enabled: bool  # 🦜🎭 §1.74 整則複誦守門＋「刻意引用」意圖（ECHO_WHOLE_GUARD）：截圖 17:29 根因＝使用者連發「你不累嗎」「都在做一樣的事情」合併成一則多行訊息，bot 的「回覆」**逐字就是那兩行**（使用者問「是 bot？還是刻意故意的？」——答案：不是故意，是複誦漏防）。兩道既有防線同時擦邊漏掉（實測）：① coach 端 echo.is_echo 只看開頭一兩段——首段「你不累嗎」正規化 4 字 < min_len 5 被跳過、次段對整句相似度 **0.80** 差 0.02 沒過 0.82 門檻 → 不觸發重生成；② _say 端 §1.28 wire 的 strip_leading_echo 同樣因首段太短 no-op，且它只剝**開頭**、整段都是覆述時原樣退回（單段兜底只覆蓋 len(segs)==1、多行是死角）。修法三件：A. echo.whole_echo_of 純函式——比**整則 vs 整句**（長度相近 span 0.8＋相似度 ≥0.9 或全等；「你不累嗎？我不累啊」這種有實質內容的長度差太多＝不誤判）；is_echo(whole=True) 收編＝coach 端**重生成**（使用者拿回真答案）；B. _say 端 _echo_strip_wire(whole=True)＝最後防線，整則複誦時替換成第一人稱認帳句（自己抓到、自己認）；C.（使用者提「bot 需要有『故意的』意圖表現，讓他更具意識」）persona.ECHO_INTENT_HINT 隨重生成注入：重複只有兩種合法形態——**刻意的引用**（看得出是故意拿起這句＋說得出為什麼＋後面接自己的話）或**不重複**，沒有第三種。兩層旗標分離：config 預設 True／coach·monitor 端 getattr 一律預設 False＝逐位元同現狀。設 0＝同現狀
    human_affect_enabled: bool  # 🧠 §1.75 人類情緒動力學（HUMAN_AFFECT）：使用者查驗「情緒座標為什麼不會變成負的？合理嗎」——實測**不合理**：affect_delta_for 的預設分支是 (+0.05,+0.06)「被陪伴、微暖微醒」＝任何不在詞表裡的訊息都讓 V 上飄，負向只認 15 個字串的窄詞表；把使用者當天 14 句批評（太機械感了／重複性太高／不夠連貫性／聽起來像是幹話／不準確吧…）丟進去**全部**落預設分支＝被嫌卻變暖；再加上每圈衰減只朝 0（不會朝負），於是 V 長期釘在 +0.84~+0.99（state 殘值 mood=0.978＝飽和貼頂），座標回報失去資訊量、bot 還對著飽和讀數編出「我的本質就是正向循環」的假解釋。四條人類化機制（主結構刻意不靠詞表——詞表窮舉是本 repo 前科）：① **中性＝中性**（reaction.human_affect_delta_for）：無情緒訊號的訊息 (0.0,+0.03)＝只是有人在讓我微醒、不再微暖 → 長聊不再棘輪上飄，且 affect.appraise 的期待落差（pe＝這句價性−我先前所信的暖度）對「一向很暖的人只是冷冷講事情」自然變負＝結構性的小失落；② **批評成格**（_CRITIQUE 加分項）：明確不滿 (−0.10,+0.12)＝不悅但警醒（左上、比質疑輕）；③ **負向偏誤**（affect.human_bias，NEG_BIAS 1.6）＝壞比好強；④ **習慣化**（HABIT_DECAY 0.6^(n-1)、方向一換歸零）＋**不對稱衰減**（lifeloop._decay_for：V/A>0 用 0.94 散得快、<0 用 0.985 黏得久）＝好心情回基線快、低落沉得久。另 /moodwatch 加「上次負向事件＋此刻 hunger/下沉力」對帳行。兩層旗標分離：config 預設 True／各呼叫端 getattr 一律預設 False＝走原函式原衰減＝逐位元同現狀。設 0＝同現狀
    tone_felt_enabled: bool  # 🎚️ §1.76 口吻要被感覺到（TONE_FELT；使用者定案「情緒座標的數字是死的，真正能感受到的，是 bot 回應的口吻及語氣要能相應」）：查驗發現語氣染色**有接**（mhint 流進 9 條回覆 lane），但兩個問題讓它形同虛設：① **門檻是為飽和尺度校準的**——circumplex._TONE_R=0.35 在舊模型（V 常年 +0.9、半徑 >1）永遠染得到，但 §1.75 把座標改成人的尺度後「被嫌一句」＝V−0.16/A+0.19＝半徑 **0.25 < 0.35** → **完全不染**＝情緒愈真實、口吻愈平（新舊機制互相抵銷）；② **提示在講座標**（「你此刻情緒座標落在…」）＝鼓勵 bot 去**報告**狀態，而使用者要的是從語氣裡**感覺到**。三件：A. circumplex.tone_directive——門檻降到 _TONE_R_FELT=0.15（單一事件就該有變化）＋**行為化**指令（八分區各給句長/節奏/標點/要不要熱絡的說法：低落＝語速慢句子短少表情符號、緊繃＝話直少客套、倦＝話少不必熱絡…）＋三級強度（淡淡地/明顯地/強烈地）＋明令「**用語氣讓他感覺到就好，不要把這個狀態講出來當台詞**（除非他直接問）」；B. _say 端 _tone_shape **確定性後盾**（prompt 單靠不夠＝§1.34/§1.42 教訓）——V ≤ −0.25 時連發驚嘆號收成一個、拿掉歡快 emoji；再偏沉/倦（A ≤ 0）連單一驚嘆號也降成句號＝不會嘴上說沉、標點卻在跳；C. 座標真值 stash 進 _TURN（只管當輪）。兩層旗標分離：config 預設 True／monitor 端 getattr 預設 False＝走原 tone_hint、不整形＝逐位元同現狀。設 0＝同現狀
    hostile_grace_enabled: bool  # 🌊 §1.77 氣頭上的理解與分寸（HOSTILE_GRACE）：截圖 20:42-20:44 三個獨立失誤疊在一起。**主病＝語意讀反**（使用者定案「傳送貼圖不是問題，是 bot 看不懂使用者的語意」）：使用者在**罵它送圖**——「你看，明明白目，還傳這種貼圖」——`selfstate.is_sticker_send_request` 卻判 **True**（實測），於是 bot 照「請求」辦事：又送一張＋「來，這張真貼圖送你 :)」＝把抱怨當訂單；「你怎麼又送貼圖」同樣誤判。與 §1.63（「我常跟你說早安」被當成問候）、§1.70B（主詞反轉）同一族＝**提及/抱怨 ≠ 執行/請求**。三件：A.（主修）selfstate.is_behavior_complaint 一般化語意閘——抱怨/質問標記（怎麼又/還傳/這種/明明/居然/白目…）命中且**無**明確請求語（幫我/請/再傳一張/傳一張給我…先排除＝真請求不誤殺）→ 貼圖請求兩條 lane 都不當請求、交回一般對話好好回應那句抱怨；B. persona.HOSTILE_GRACE_HINT——氣頭上明令別做三件：**反問他**（「你覺得我還在辯解嗎？」）、**數他的帳**（「你又這樣說了一次。」）、**賣乖討好**（送圖/顏文字 :)/「來～」）；該做的是短、直、承接他真正在意的那點，然後把話頭讓回去（§1.14 只壓篇幅、沒管形態）；C. 後盾——氣頭上（本句敵意∨streak≥1∨近 5 則有敵意）**主動**情緒貼圖 lane 靜音（不影響你真的開口要圖），＋§1.76 口吻整形把顏文字 :) :D 也算歡快標記收掉。兩層旗標分離：config 預設 True／消費端 getattr 一律預設 False＝逐位元同現狀。設 0＝同現狀
    habit_absence_enabled: bool  # 🌾 §1.79 習慣缺席暗示（HABIT_ABSENCE_WONDER）：使用者需求「一旦有明確的作息習慣資料後，如果 bot 發現平常使用者有做的事情沒做，需要主動詢問或者暗示（**不是提醒**）……bot 能感覺到，明明是例行的事情，為什麼還沒看到使用者去做」。設計原則＝**不對稱失敗**：偽陰性（該講沒講）沒人發現；偽陽性（他其實做了、或只是今天晚一點）＝直接複製 §1.63/§1.70B/§1.77「語意讀反」的前科＋變成待辦追殺 → 全層 **fail-closed**，任何前提算不出來就沉默。實測支撐（12 天貼近真實的分布：多數早上 7–8 點、偶爾晚上）：「中位＋固定 2 小時」門檻誤判率 **17%**（每 6 天亂問一次）→ 改用**分位數 p75 ＋ 與他自己 iqr 成比例的寬限**（90–180 分），觸發自然落在一天後段＝只適合「快過完了還沒看到」的好奇，不適合早上問（那就是提醒）。四層：A. 資格（habits.abs_routine_profile）——窗內 ≥14 個日曆日、coverage ≥0.65、iqr ≤2h、全距 ≤12h（跨午夜放棄）、p25 ≥05:00、近 7 天 ≥4 天、最後一次 ≥今天−2；B. 資料健康（abs_records_health）——今天至少 1 筆（快照看得見今天）、今天沒有未歸戶的筆、**快照本身**（meta.lastIngestTs）夠新（**不是**用「最新一筆記寫」判 stale——他只在早上寫的話，到晚上必然超時，會讓機制永遠不觸發）；C. 判定（abs_verdict 六值）——due＝p75＋寬限，他今天已活動 ≥90 分、每天每條線只有一段 ≤5h 的可問窗、21:00 硬上限，過窗**永遠沉默不補問**；D. 措辭（persona.habit_absence_rule）——只說「我還沒看到」（我這邊的資料）、**絕不斷言**「你沒做/你忘了」、不叫他去做、不索取回答（留他一條不必解釋的路）、不列數據、一到兩句。出聲走 _proactive_ok（互動優先＋深夜不擾，**不比照守約例外**）＋共用冷卻＋自有 20h 冷卻＋同一條線同一天只問一次（state.habit_absence 台帳、跨重生）。/habits 附對帳段（為什麼沒問、會等到幾點）。兩層旗標分離：config 預設 True／emit 端 getattr 預設 False＝直接 return＝逐位元同現狀。設 0＝同現狀
    habit_absence_convo_enabled: bool  # 🌾 §1.80 對話作息也是日課（HABIT_ABSENCE_CONVO；使用者更正 §1.79 對需求的兩處窄讀）：①「作息習慣」不只記寫主題——每天的早安、平常幾點會出現這些**對話層例行**（habit_events 的 greet_am / msg / contact，§1.42/§1.63 本來就在記）也算；②「他今天還沒出現」不是抑制條件、**正是**該去看看的場景（「平常這時間早該看到你了，今天還靜靜的」，訊息送出他回來自然看到）。作法：記寫類（§1.79）無候選時，看早安習慣（優先，資格較嚴的 greet_am 分佈）或每天第一次出現的規律（msg/contact 日曆日最早）——資格門檻比記寫類寬（min_days=7：事件 §1.42 部署後才開始累積；iqr ≤150；p25 ≥04:00；說錯的代價低＝「還沒聽到你的早安」是暖的不是指控），判定 abs_appear_verdict（due＝p75＋比例寬限、≤5h 可問窗、21:00 上限、過窗永遠沉默）；台帳鍵「＠出現」同一天只問一次、與 §1.79 共用 20h 自有冷卻。同時 §1.80 措辭鬆綁（隨 HABIT_ABSENCE）：§1.79 原版「不要索取回答」禁過了頭——使用者原話是「主動詢問…閒聊看看，了解為何」→ 改為**可以輕輕問一句**（今天在忙別的嗎/換了節奏嗎），仍禁催辦、逼問、連環問，給不答也沒關係的台階。兩層旗標分離：config 預設 True／emit 端 getattr 預設 False＝不看對話作息＝§1.79 原行為。設 0＝同 §1.79
    interrupt_wave_guard_enabled: bool  # 🌊 §1.81 同一波不算插話（INTERRUPT_WAVE_GUARD）：截圖 07:01 使用者同一秒連發兩個**不同**的問句（「是嗎？真的有這麼厲害？」「你知道我的作息？」），第二句因為是問句被 _looks_like_redirect 判 redirect → 走插話路徑＝整波被拆成**兩輪**處理，於是回覆長成六顆彼此不連貫的泡泡：① 開頭裸複誦「是嗎。」② 答第二問 ③ 補充 ④ **接回橋「誒，回到剛剛在講的，」**（根本沒被打斷、卻宣告要接回）⑤「嗯，我會努力的」⑥「欸，你剛剛才問過我一樣的問題耶。」＋⑦「不過沒關係，我可以再跟你說一次」——④⑥⑦ 全是「拆成兩輪」的副產物：兩輪各自 dialogue_intent.observe，重複偵測（repeat_fatigue）於是把兩個**不同**的問題數成同一個問題問兩次。§1.73 修的是**已合併**那條路（burst_n hint）；這裡補的是「回應途中才到、但其實同屬一波」那條。判準＝新到這則的 message.date 距**正在回應的那則**（wave_ts） ≤ interrupt_wave_sec（預設 12 秒）→ poll() 回 None＝**defer 不消費**、offset 不前進 → 下一圈 _relate_coalesced 自然收進同一輪整體回一次（守既有連發合併契約）。真正較晚才到的插話（>12 秒）行為完全不變。兩層旗標分離：config 預設 True／poll 端 getattr 預設 False＝逐位元同現狀。設 0＝同現狀
    echo_burst_lines_enabled: bool  # 🦜 §1.82 連發合併的每一行也當複誦比對候選（ECHO_BURST_LINES）：實測缺口——連發合併後 handle_message 拿到的 text 是「是嗎\n我要檢查看看\n你為什麼會了解我的作息呢？…」**一整塊**，_TURN['echo_user_texts'] 只塞這塊＋歷史，**個別句子從來不在候選裡** → bot 逐字複誦其中一句時（截圖 07:19 第一顆「是嗎。」＝第 1 句、第二顆「你為什麼會了解我的作息呢？」＝第 3 句），§1.74 whole_echo_of（比整則）不中、§1.81 前綴規則要求原話 >2 倍長也不中、§1.77 strip_echo_segments 的 exact 集合裡沒有那一行 ⇒ **三道全漏**。修：stash 時把本則按換行拆出的每一行也放進候選（實測補上後兩顆裸複誦都剝掉、正題保留）。旗標關（getattr 預設 False）＝不加行＝逐位元同現狀。設 0＝同現狀
    write_claim_proactive_enabled: bool  # 🕐 §1.84 主動出聲也要有「今天記寫」接地（WRITE_CLAIM_PROACTIVE）：截圖 09:37 🫧 主動訊息說「我剛剛看了一下，你今天早上又寫了「閱讀｜讀誦經書」這件事。」＋「看到你又用貼圖幫自己加油打氣」——使用者當下**今早根本還沒寫**。根因＝**結構縫**：§1.60 的記寫宣稱守門 ① 只在 handle_message 裡 arm（`_TURN['write_claim_ground']`）＝**互動輪限定**；② 判定塊又寫在 `_say` 的**互動限定分支**內（`if not prefix and state is None:`）——而所有主動出聲（🫧 含蓄伸手／🫀 自陳／💡 聯想／🌾 習慣缺席…）一律走 `prefix=` ＋ `state=` ⇒ **整條主動路徑從來沒有這道守門**，LLM 可以自由宣稱「你今天寫了 X」。修：① 生命迴圈 feel 相每圈用 `_write_ground_data`（與 §1.60 **同一支**產生器、同一份真相）arm `write_claim_ground_proactive`；② `_say` 的主動分支套 `_write_claim_fix`（與 §1.60 **同一支**純函式）＝今天零記寫時把「你＋今天＋記寫/讀經」的宣稱句整句換成程式算的事實句（否定句與今天真有記寫皆不動）。另附（隨 REACHOUT_DIVERSE）：§1.72 的聯想素材是「那條線**最近一筆**」、**不保證是今天**，規則塊補明令「除非時間事實明講，絕不要說『你今天／今天早上／剛剛又寫了』，也不要宣稱他今天做了什麼、傳了什麼」。兩層旗標分離：config 預設 True／arm 與 _say 端 getattr 預設 False＝逐位元同現狀。設 0＝同現狀
    interrupt_wave_sec: float  # 🌊 §1.81 同波判定窗（秒）：新到訊息距「正在回應的那則」在此窗內＝同一波、不算插話。預設 12（人雙手連打兩句的自然間隔；比 burst_coalesce_sec 2.5 寬，因為這裡是**回應已經開始後**才到）
    interrupt_coalesce_enabled: bool  # 🧵 §1.48 插話像人一樣接（INTERRUPT_COALESCE；兩件一旗）。截圖根因（21:16–21:17）＝bot 分串講解途中使用者連丟「好久」「沒關係」「慢慢來」三句附和：現行把陳述也當即時插話（INTERRUPT_STATEMENT_ENABLED）且 _BurstInterrupt.handle **逐則**巢狀回覆 →「嗯。」「嗯。」「嗯，好。」洗版＋resume 橋「我繼續說喔，」把順暢的話硬切一刀——真人看到對方點頭不會停下來逐個回、再宣告要繼續說。A. 附和不打斷：整批插話都是 selfstate.is_backchannel（好久/沒關係/慢慢來/嗯/繼續說…全句錨；帶問號/嗎/呢/數字/指令不算；敵意短句不在表內＋is_hostile 雙保險＝§1.14 收口優先）→ poll() defer（不消費、折進下一輪連發合併＝講完後一次溫和承接）＝主體不中斷、零逐則嗯、零橋接。B. 插話合批：真插話（redirect/實質陳述）批次先走既有連發合併純函式（group_bursts＋build_coalesced_update、同主迴圈斷群參數）把密集文字黏成一則再巢狀回覆＝一批一個回應；非純文字/稀疏群照原逐則（貼圖訊號不丟）。monitor 端 getattr 預設 False＝既有測試假 cfg 無此欄→原逐則＋陳述即時插話＝逐位元同現狀。設 0＝同現狀
    feeling_probe_depth_enabled: bool  # 🗜️ §1.51 情感探問要真回答（FEELING_PROBE_DEPTH）。截圖根因（09:54–09:55）＝「羨慕我嗎」→「嗯...」「羨慕嗎？」支吾反問：fact_or_chat base=1＋短輸入鏡射（≤6 字 −1）＋低落心情（−1）→ verbosity level 0、泡泡 ≤2＝LLM 拿到壓到底的預算只好填停頓泡泡。**長度啟發式把「輸入長短」誤當「意圖深淺」**——「羨慕我嗎」句短但在探 bot 對他的心意（使用者原話：要了解對話意圖、適性調整長度與深度）。三件：A. selfstate.is_feeling_probe（情感詞×問句形×人稱指向；內容問句什麼/哪/怎麼…排除＝要資訊不是要立場；>16 字排除＝長句自帶篇幅訊號；表小而封閉、漏了＝現狀短答＝安全側）；B. verbosity.assess 新尾參 probe——命中＝level 地板 2（短輸入鏡射/低落心情壓不破）；明示要短（簡單說）與 §1.14 氣頭收斂**仍優先**；C. persona.FEELING_PROBE_HINT 注入 fact_or_chat/自我在場兩 lane——先正面回答（最接近的真實狀態）、一兩句為什麼（照此刻內在與對他的印象）、反問只能放在答後；機制沒有的情緒誠實說最接近的真話（不演不假裝）。另：命中探問＝真問 bot 自己 → §1.43 感覺修剪不 arm。monitor 端 getattr 預設 False＝既有測試假 cfg 無此欄＝原鏡射壓短＝逐位元同現狀。設 0＝同現狀
    interrupt_statement_wrap_enabled: bool  # 🧵 §1.50 陳述插話後不硬接尾巴（INTERRUPT_STATEMENT_WRAP；§1.48/§1.49 的收尾件）。截圖根因（23:21，新碼上線後實測）＝bot 回覆途中被「自己打臉自己」（陳述指控）插話 → 巢狀回完後 remaining=1 落 decide_resume 規則②（剩 ≤1 → resume）→ 罐頭橋「對了，剛剛那條還沒說完——」＋把插話**前**切好的尾巴「我剛剛說完了耶。」原樣照播＝使用者才說「你剛剛沒說完」、bot 先複讀指控再自打臉。本質＝陳述插話帶進新資訊後，預切殘句常已不合時宜，原樣接回（哪怕只剩一串）＝沒在聽。修法：decide_resume 新尾參 statement_wrap——陳述插話且主體已送過（sent>0）→ 一律 wrap：走既有 INTERRUPT_WRAP_CONDENSE 濃縮（voice_wrap_condense 看得到巢狀輪後的 convo_history＝殘句融進插話後語境）、無教練退模板淡收，不再送「還沒說完」類的橋。redirect（問句）照舊 resume；sent==0 保底 resume；§1.14 敵意收口優先不變；需 INTERRUPT_REWRITE 開才有 wrap 路徑（兩者預設皆開）。monitor 端 getattr 預設 False＝既有測試假 cfg 無此欄＝規則②照舊＝逐位元同現狀。設 0＝同現狀
    fact_card_enabled: bool  # 🪪 §1.61 此刻事實卡（FACT_CARD）——通盤解（使用者原話「不能只是錯哪改哪，要找出通盤的問題並且解決」）。系統性病根＝點對點接地：每種問題要偵測器（詞表）認出「他在問X」才注入X真相，詞表漏一縫＝LLM 無接地亂編＝等截圖＝加偵測器（18 次前科、§1.46–§1.60 十五個 § 的共同上游）；且各處自拼 ground 無單一權威（§1.60 補遺自打架）。修法：每個互動輪（六個 LLM lane）**常駐**注入程式算的小事實卡——此刻時間（temporal）、他的記寫今天幾則/最後一次（§1.60 _write_ground_data 同一把＝卡與守門結構上不可能矛盾）、活著的約定（scheduled_promises 計數＋最近一筆 HH:MM）、我的內在座標（circumplex 單一真相）。規則：每欄位恰一個權威來源；卡尾明令「以卡為準、卡上沒有的數字不要編、這張卡是校準用的不是要你主動念數字」。偵測器型 hint 自此降級為深答加強、不再是真相開關。代價＝每互動輪 prompt 約多 150–250 token（換整類「無接地亂編」失效源消失）。monitor 端 getattr 預設 False＝不注入＝逐位元同現狀。設 0＝同現狀
    write_today_ground_enabled: bool  # 🕐 §1.60 記寫時間脈絡接地（WRITE_TODAY_GROUND）。截圖根因（07/21 09:01–09:15，使用者：「bot是不是誤以為9:01的bot的每日自動回報記寫狀況，當成是我剛剛完成的記寫事情」——正確）：真相＝最後記寫 07/20 09:37（昨天；09:13 時鐘 lane 答對）。①09:02 摘要感想把「近 24h 1 則」講成「你**今天**又繼續讀經了」；②錯話入史 → 後續輪引用自己的錯話當證據（09:08「你今天早上不是才又記寫了嗎」）＝自我污染鏈；③09:15 把每日摘要推播時間 9:01 說成「你今天早上 9 點 01 分記下讀誦經書的進度」＝把 bot 報表當使用者行為事件。四件：A. _write_ground_data 純函式（今天已記寫 N 則＋最後記寫標籤 今天/昨天/前天/M/D＋HH:MM，全程式算）；B. 摘要/歸戶/事件 reflect prompt 附【記寫時間錨】（不是今天＝明令絕不說「今天有記」、自己的推播時間不是他的記寫時間）；C. 確定性宣稱守門——今天零記寫時「你＋今天＋記寫/讀經/記下…」宣稱句整句換事實句（互動 _say＋四個 reflect 送出點都擋；否定句「你今天還沒記寫」誠實不動）；D. 「今天做了/還沒做什麼」問句 → 注入今天記寫真實數據 hint。monitor 端 getattr 預設 False＝不 stash 不附不擋＝逐位元同現狀。設 0＝同現狀
    push_data_memory_enabled: bool  # 🗂 §1.59 資料推播也入對話史（PUSH_DATA_MEMORY）。截圖根因（09:46–09:57）＝09:46 主動推「📝 剛歸戶（1 則記寫）…」＋人話感受，09:57 使用者「你不是剛剛告訴我歸戶的訊息」→ bot「我剛剛沒有告訴你歸戶的訊息耶。」＝否認自己 11 分鐘前的訊息。機制面：tick() 推播一律「先資料、後人話」——人話那則已入記憶（test_proactive_memory 的修），**資料**那則（format_filings/format_digest/format_events）從未 _remember＝§1.23「送出方向零記憶」第三次現形（貼圖→人話→資料）；史裡沒有帶「歸戶」字樣的訊息＝LLM 誠實否認、「你發現了什麼」（指剛推的記寫）接不到指涉只能亂猜反問。修法：四個資料送出點（歸戶通知/上線摘要/每日摘要/事件推播）成功後 _remember（model 角色本就截 200 字＝長摘要留頭、標籤字樣必在）。monitor 端 getattr 預設 False＝不記＝逐位元同現狀。設 0＝同現狀
    timejump_guard_enabled: bool  # 🎭 §1.58 不演未來（TIMEJUMP_GUARD）。截圖根因（22:17–22:18，使用者：「好好笑，bot是在自導自演嗎？」）＝約「想 30 分鐘再告訴我答案」，bot 在**同一則回覆**把整齣演完：「我記下來了，30 分鐘後我會再回來跟你說。」→「（30 分鐘後）」→「嗨，我回來了。」→ 把 30 分鐘後才該說的答案當場全倒 → 尾端又複讀一次 ack＝一次 LLM 生成自導自演「入帳→等待→到點→兌現」。persona 的「別當場假裝到點」只是軟提示；§0.82 只攔鐘點宣稱、_keep_time_consistent 只管兌現句＝這條路零確定性守門。修法：_say 互動出口 _timejump_truncate——**句界後的純括號時間跳躍舞台指示**（（30 分鐘後）/（過了三十分鐘）/（一小時後）/（隔天）…敘事裝置、真人打字不會出現）一出現＝其後內容全是演出來的未來 → 從該處截斷、保留前面的誠實答應；截到全空＝換誠實停住句；行內括號（我們約（30 分鐘後）見）與非時間括號（（笑））不攔。monitor 端 getattr 預設 False＝不 stash＝_say 恆 no-op＝逐位元同現狀。設 0＝同現狀
    confused_clarify_enabled: bool  # 🎴🗣️ §1.56 困惑句是要澄清、不是要貼圖（CONFUSED_CLARIFY）。截圖鏈路（20:58–21:32，逐段實測定罪）：①「你自己有被冷落的感受？」→ LLM 答題漂去宣稱送圖 → §1.34 F4 整則替換成「我其實沒真的送出貼圖…要我送一張嗎？」＝正題被吃（§1.38 註解記載的已知復發型；上游補「冷落/委屈」入 §1.51 探問表＝答題錨在真實內在少漂題）；②21:29「什麼？」＝純困惑，但 _STICKER_ELLIPSIS_ASK_RE.match('什麼？')=True（實測）＝貼圖情境窗內被 §1.15 結構閘⑤收進逃生閘 → LLM 順著「要我送一張嗎？」判送 → 真送熊抱貼圖＋§1.16 心情說明＝雞同鴨講；③21:31「看不懂你前面在說什麼」→ LLM 對史料編故事＋時序講反（「我以為你看到我發的那個貼圖才問什麼」——貼圖在「什麼？」之後才送）。兩件：A. §1.15 閘④b 前置排除困惑句（什麼？/蛤？/啊？/嗯？；ellipsis regex 本體一字不動＝指紋格不動）＝零 LLM、放回一般聊天；B. _clarify_recap_hint——「看不懂/什麼意思/在說什麼」→ 注入程式照真實時間順序列的「你前面實際說了什麼」清單＋守則（白話重述、不揣測他為什麼問、順序不許講反、別把話題帶去貼圖）。monitor 端 getattr 預設 False＝照舊進閘/不注入＝逐位元同現狀。設 0＝同現狀
    promise_anchor_bridge_enabled: bool  # 🤝 §1.55 跨句補時距（PROMISE_ANCHOR_BRIDGE）。截圖根因（11:05–11:06，使用者：「bot 看不懂使用者的對話表達方式的真正語意及意圖」）＝「給你思考20分鐘。」＋「時間到了再跟我說，你可以如何證明自己？」拆成兩輪、各自是碎片（實測）：前句裸時距無動作（capture/§0.61 守門/temporal 全 miss → LLM 空口答應「我記下來了，我會等 20 分鐘」）；後句有動作無鐘點（capture miss、守門有掛但 LLM 沒守住 → 又空口答應＋把「時間到了『再』跟我說」的「時間到了」讀成現時宣稱 → 回「嗯？現在才 11:05 呢。」＝條件式被當宣稱）。兩句拼起來 capture 直接命中、temporal 解出正確 epoch。修法：_maybe_anchor_bridge（排 §1.12 LLM 逃生閘之前、全確定性零 LLM）——本輪 capture miss ∧ 這句請託形且自身無鐘點 ∧ 緊鄰上一則使用者訊息 180 秒內且自身也是懸空碎片（不重收「10分鐘後提醒我」）∧ 拼句 capture 命中＋解得出時刻 → _book_scheduled_targets 真入帳（ack 帶程式算 HH:MM、到點真兌現；時刻永遠 temporal＝鐵律）。monitor 端 getattr 預設 False＝不橋＝逐位元同現狀。設 0＝同現狀
    reply_ack_dedup_enabled: bool  # 🧵 §1.54 連續回覆的致意句去重（REPLY_ACK_DEDUP；§1.48/§1.49 的同組意識收尾件）。截圖根因（10:41–10:42）＝使用者連發「廢話一堆」「除非你能夠證明給我看」→ 巢狀/連續輪各自生成，一分鐘內送出「嗯，我明白了。」「嗯…我明白，光憑…」「我明白。」三顆明白＝使用者：「不是將連續訊息串視為同一組的對話」。既有防線都管不到純致意句（§1.49 插話殘句逐字≥5 字、§1.41 兌現句、§1.21/1.22 自陳措辭）。修法：_say 互動出口對**純致意泡泡**（canon＝剝語氣/標點噪音→剝尾了/的/啦→剝首「我」→落在小家族：明白/知道/懂/好/了解/收到/純嗯）去重——比對源＝近 150 秒 model 回覆（handle_message stash 進 _TURN、巢狀輪自己重 stash）＋本則稍早泡泡（同輪內重複也擋）；實質內容句 canon 落不進家族＝不動；全刪光＝保留第一顆（寧可重複、不可失語）。monitor 端 getattr 預設 False＝不 stash＝_say 恆 no-op＝逐位元同現狀。設 0＝同現狀
    interrupt_tail_trim_enabled: bool  # 🧵 §1.49 插話後殘句取捨（INTERRUPT_TAIL_TRIM；§1.48 的 sibling——那邊治附和誤中斷，這邊治真插話後的殘句品質）。截圖根因（23:00）＝使用者「晚安 我累了」＋「希望你能遵守承諾」：bot 回覆途中被第二句（實質陳述、非附和）插話 → 巢狀回「我會記得的。」後照舊橋接「嗯，我接著說，」再續殘句——①道別輪還宣告要繼續說、續講客套尾巴（「嗯，好好休息。」）＝人不會這樣收晚安；②殘句「我會記得的。」與巢狀輪剛說的一字不差重播。兩件：A. 道別即收——這輪在回道別（_dispatch_one 依 is_farewell stash 在 interrupt 物件；_TURN 會被巢狀輪清掉不能放那）或插話本身是道別（_pending_farewell）→ 答完插話直接收口（不橋接不續殘句＝§1.14 敵意收口的道別版）；B. 殘句去重——殘句已在「這輪已送出串」或「近幾則 model 回覆」（含剛巢狀輪，_remember 入史）出現過（≥5 字、子串/超串）→ 跳過不重播；殘句全被去掉＝連橋都不送（沒內容就別宣告要繼續）。有實質新內容的真插話接續（橋接/rewrite/wrap）不動。monitor 端 getattr 預設 False＝既有測試假 cfg 無此欄→原樣續送＝逐位元同現狀。設 0＝同現狀
    interrupt_continuation_defer: bool  # 🧵 續寫/追加跟句（同時/還有/而且/順便…）＝同一波延續、要和前一則一起讀 → 插話偵測時一律 defer 折進下一輪合併（即使帶問號），別當 redirect 在回應途中單獨答；設 0 關＝同現狀（帶問號的續句仍被當插話）
    promise_continuation_merge: bool  # 🧵 「同時/一起/一併…」併進剛排程的承諾：截圖「10分鐘後打招呼」緊接「同時說一下在翻哪個主題」→ 兩串一起讀＝到點一起做（接到該承諾、不另當『現在在翻什麼』答）；設 0 關＝不併＝照常路由＝逐位元同現狀
    skill_recall_enabled: bool       # 🧑‍🏫 同主題/情境再現 → 召回已學做法、注入 coach.reply 的 extra_system（只在開放對話路徑 SKILL_INJECTABLE_KINDS、過淨化白名單）；**預設開**（已過對抗式審查＋淨化白名單硬化）；設 0 關＝不召回不注入＝逐位元同現狀
    skill_consensus_enabled: bool    # 🧑‍🏫 對話凝出共識 → bot 提議「要我把這學成做法嗎？」（提議→確認兩步學起來）；**預設開**；設 0 關＝不偵測不提議＝逐位元同現狀
    skill_propose_cooldown_sec: int  # 🧑‍🏫 兩次「要學成做法嗎」提議的最短間隔（秒）；預設 90（原 600＝10 分太久、一次提議後 10 分內所有新教學都被擋）；調短＝連續教多件事時每件都提得出（廣化觸發），精準仍靠 detect＋淨化＋確認兩步
    skill_situations_enabled: bool   # 🧑‍🏫 §0.57 觸發分類：做法帶 always（常駐風格）/sit:<情境>（重複/被質疑/深夜…）觸發、召回時比對當下活訊號（不再只有 route+主題子字串）；設 0＝_skill_extra 退回 legacy 單一 topic 召回＝逐位元同現狀
    skill_internal_coping_enabled: bool  # 🌀 §0.57 part B：內在狀態因應做法（low_vitality 轉速太低/high_hunger/low_mood 觸發）注入 self-presence 路徑＝把「教過的自處步驟」化進 bot 對自己內在狀態的處理；設 0＝不注入內在因應＝同現狀
    teaching_guard_enabled: bool     # 🧑‍🏫 §0.59 Part 1a：對方這句像在「教你以後怎麼回應」但一般回覆路徑並沒真的把它存成長期做法（capture 只在提議→「好」兩步握手成立）→ 給 LLM 一段守則、別在回覆裡謊稱「記下來了/會記住」（截圖：說詞 vs 真實）；設 0＝不注入守則＝逐位元同現狀
    skill_selfroute_capture_enabled: bool  # 🌀 §0.59 Part 1b：自我在場/內在對話（self_*）路徑也可凝出『內在因應』做法並提議學成（只收 route-agnostic 的 always/sit 型，topic 型會召不回＝拒收）；設 0＝提議只認 SKILL_INJECTABLE_KINDS＝逐位元同現狀
    spontaneous_coping_enabled: bool  # 🌀 §0.59 Part 2：內在因應做法升級為主動推播引擎——含蓄型主動出聲（🫧）時，若此刻內在狀態命中已學自處做法，帶進那則主動出聲（把教過的自處化為主動的求救/自我調節）；設 0＝主動出聲不帶內在因應＝逐位元同現狀
    skill_legacy_migrate_enabled: bool  # 🧾 §0.60 承諾履行：啟動時把舊 topic-keyed「情境元詞」做法（重複提問/回應風格/深夜…）冪等遷移成活的 always/sit: 觸發 key（value/權重/時間戳全保留＝已答應的約定不重學不歸零、真的會觸發）；設 0＝不動任何 engram＝逐位元同現狀
    skill_accountability_enabled: bool  # 🧾 §0.60 做法問責：問「你學過的做法/我們的約定」（meta 線索）或指涉某條做法內容（雙字重疊＋查核語氣）→ 把真帳本 skills_brief 注入回覆 system＝只准照帳本答：照實引述、沒做到誠實承認、絕不編造/軟化（修截圖：否認約定＋幻覺成「溫暖呼應」）；設 0＝不注入＝逐位元同現狀
    promise_ack_guard_enabled: bool  # 🤝 §0.61 空口答應守門：像「到某時間叫醒/提醒我」的請求走到一般聊天路徑（＝排程捕捉沒成立）→ 掛守則別答應「到時候我會叫你」（沒入帳＝守不了的空頭承諾），誠實請對方用明確時間再說一次；設 0＝不掛＝逐位元同現狀
    sched_feeling_ground_enabled: bool  # 🤝 §0.63 感覺分享守約要據實：到點兌現「跟我說你此刻的感覺」時，附上 bot 此刻**真實**內在讀數（affect/felt）當接地，讓兌現句說真的、不編造（呼應「依約完成必須是真的」）；非感覺行為不受影響；設 0＝不附接地＝逐位元同現狀
    promise_capability_gate_enabled: bool  # 🤝 §0.64 原則一「做不到就不能答應＋說明原因」：超出能力的約定（打電話/寄信/聯絡第三人＝外部動作、每小時/每N分＝高頻重複、下雨/股價/別人行為＝感知不到的外部條件）→ 排程路由硬拒絕（誠實模板：原因＋做得到的替代）＋聊天路徑疊守則（絕不「好我會的」）；設 0＝照舊＝逐位元同現狀
    sched_recur_daily_enabled: bool  # 🤝 §0.64 每天重複：「每天早上8點叫我起床」記成 recur=daily——到點發完自動排明天同時刻、錯過的推進不翻舊帳（原本被記成單次、發一次就永遠停＝答應每天實際只做一天的半守約）；設 0＝不標 recur＝單次＝逐位元同現狀
    skill_proactive_enabled: bool    # 🌀 §0.65 教過的內在因應做法**升級為真觸發**：當 bot 內在狀態（low_vitality/high_hunger/low_mood）成立、且教過對應自處做法時，**因為那條做法**而主動發訊息（不再只是等含蓄伸手時當措辭裝飾）。有自己的冷卻＋預算＋守深夜/互動/剛聊完閘；只對「真的教過內在做法」的使用者生效（沒教過＝coping 空＝不發）；設 0＝關＝逐位元同現狀
    skill_proactive_cooldown_min: int  # 🌀 §0.65 內在因應主動觸發的自有冷卻（分）；預設 180（與含蓄伸手同量級，防洗版）
    skill_proactive_max_reach_outs: int  # 🌀 §0.65 一段閒置內在因應主動觸發的伸手上限（與含蓄伸手 reach_outs 分開）；預設 2（持續低狀態可再浮現、仍有界）
    skill_proactive_sticker_enabled: bool  # 🎴 §0.65 內在因應主動觸發時**順便送 help sticker**（截圖使用者教的「求救貼圖」）——繞過 pick_self_reaction 的正向心情閘（低狀態才送），有自有貼圖冷卻＋去重；無可送貼圖＝no-op；設 0＝不送＝逐位元同現狀
    coping_sticker_tone_enabled: bool  # 🎴 §0.72 內在因應貼圖池**依教過的做法語氣挑**：求救型（撐不住/低落）→ 非正向 help 池（§0.65）；邀請型（轉速平穩→主動告知＋給特別貼圖＋邀聊）→ 正向＋中性 sendable 池（否則正向教過的特別貼圖被求救池濾成空＝靜默沒送、或挑到哭哭貼圖配邀聊很突兀）；曖昧/空＝§0.65 預設非正向；設 0＝一律走 §0.65 非正向池＝逐位元同現狀
    promise_overdue_guard_exempt: bool  # 🤝 §0.78 逾期承諾豁免反連發守門：守門原意是別跟同拍其它推播擠一起，但每拍前面的自發相都刷 last_push_ts、活躍對話中守門恆真＝逾期承諾永遠發不出、§0.77 的下一圈補上失效；改成只擋剛到點的、逾期的照發（break 仍保證一拍一則）；設 0＝退回舊整段 return＝逐位元同現狀
    promise_status_empty_ground: bool  # 🤝 §0.78 空帳本也接地：「時間到了沒/還差多久」在帳本空時原本落回自由 LLM＝亂編時刻（截圖「還沒到＋已過兩分鐘」自相矛盾）；改成走誠實接地（此刻幾點＋我這邊沒記著約好什麼）；設 0＝空帳本落回聊天＝逐位元同現狀
    self_report_promise_enabled: bool  # 🤝 §0.78 自陳型約定捕捉：「說一下/說說你的感覺/狀態/內在」＝請 bot 到點自陳內在（帶鐘點走排程、帶綁住動作的延後詞走 feel 觸發）；審查補逃生閘＝設 0＝退回無自陳捕捉＝逐位元同現狀。**注意**：純函式 selfstate._self_report_hit 直接讀環境變數 SELF_REPORT_PROMISE，此欄位僅為文件/一致性（實際門控在環境變數）
    promise_ledger_time_guard: bool  # 🤝 §0.79 帳本回覆鐘點守門（縱深防禦）：LLM 若把『現在/此刻…』修飾的鐘點報成非真實此刻（含把約定時刻00:06說成現在、或幻覺00:00）＝幻覺→落回確定性帳本文字；設 0＝不守門＝逐位元同現狀
    promise_confirm_route_enabled: bool  # 🤝 §0.79 確認時間家族（確認一下時間/我們約幾點/時間對嗎）在**真有活承諾**時硬錨帳本（審查修：空帳本不搶，免第三方對時被劫持成「沒記著約好什麼」）；設 0＝confirm 不搶＝落回聊天＝逐位元同現狀
    promise_llm_rescue_enabled: bool  # 🤝🧠 §1.12 LLM 語意逃生閘：確定性捕捉 miss（route 非 scheduled_promise）＋temporal 解得出未來時刻＋句子指向 bot 時，單次 gemini 判「他是不是請 bot 到點主動做某事＋動作命名」——判「是」→ 用 temporal 的時刻入帳（**時刻永遠來自 temporal／程式時鐘，LLM 協定上沒有時間欄位、輸出裡的時刻字樣程式端一律丟棄**＝11:08 幻覺前科的鐵律）；判「否」→ 本輪不掛空口答應守則（自然聊天）；失敗→安全退回 §1.09 誠實守門。只放逃生閘（不進每則訊息熱路徑）。設 0＝逃生閘不存在＝逐位元同現狀
    sticker_llm_rescue_enabled: bool  # 🎴🧠 §1.15 貼圖請求語意逃生閘：存在句/可能句/省略句（「有…貼圖嗎」「貼圖呢」「有貼圖可以代表…？」）一個給予/祈使動詞都沒有 → 確定性偵測 is_sticker_send_request/followup/remember/preference 全 miss、掉進 intent 被吃成 self_state → 結構閘在場才單次 gemini 判「是不是要現在送一張貼圖給他本人／要不要順便說明為什麼」（**LLM 只判是非、絕不產出 file_id/emoji/圖案，真送的圖讀 circumplex 單一真相挑、沒貨誠實說沒有**＝比照 §1.12 承諾逃生閘鐵律）；判「否」／失敗→安全退回自然聊天。手刻 regex 補動詞表＝第 16 次踩坑（禁止）。設 0＝逃生閘不存在＝逐位元同現狀
    sticker_pick_rescue_enabled: bool  # 🎴🧠 §1.34 STICKER_V2/F3 挑選祈使逃生閘：貼圖情境下「挑一張給我／你挑一張給我吧／幫我選一個」對確定性八偵測器全 miss、結構閘（貼圖字 OR 省略問句）也不中 → 連 §1.15 LLM 逃生閘都碰不到、全掉自由聊天。改法＝在 _maybe_llm_sticker_rescue 結構閘**新增第三分支**＝『近期剛在貼圖情境（_recent_sticker_ctx）AND 窄挑選祈使前置式（短句＋挑/選＋指向本人一張/一個/給我/傳我/送我，排除明確附件詞與第三人）』只當**便宜前置過濾**放行、真正是非仍交既有 coach.judge_sticker_request 燒一次 LLM 判、真送圖讀 circumplex 挑（§1.15 鐵律不變）。selfstate 八偵測器不動。設 0＝第三分支不存在＝挑選祈使仍落 fact_or_chat＝逐位元同現狀
    sticker_img_disambig_enabled: bool  # 🎴🧠 §1.34 STICKER_V2/F5 貼圖情境「圖呢／圖」消歧：is_attachment_request('圖呢')=True（裸『圖』是 _MEDIA_CUES 媒體線索）→ route=attachment → 撈 Drive 私人記寫附件、傳咖啡廳照片。貼圖情境下的「圖呢／圖」是在問**剛送的那張貼圖**、非要調記寫附件。改法＝在 attachment 分派段**開頭、select_attachments 之前**插消歧閘：_recent_sticker_ctx 為真 AND 這句是『裸圖省略/指涉』（挖掉貼圖詞與明確附件名詞後剩餘 media cue 只剩裸『圖/圖檔/截圖/檔』且句短）AND 非明確附件請求 → **不撈附件**改走貼圖誠實路徑（近 15 分真送過（last_sticker_id 非空）→ 據實談那張；無真送→誠實『沒送、要不要送一張』；SEND_STICKERS=0→維護誠實句不拿附件冒充）。明確附件（照片呢/PDF/附件呢）不含裸圖以外線索或含明確名詞 → 不命中、照走 attachment。is_attachment_request／_MEDIA_CUES 本體不動。設 0＝『圖呢/圖』仍走 attachment＝逐位元同現狀
    sticker_fakesend_soft_enabled: bool  # 🎯 §1.70A 假送閘句級軟化（STICKER_FAKESEND_SOFT；§1.62「整則替換是最後手段」慣例的落實）：截圖根因＝使用者釐清「我是指，你在翻閱我的記寫過程中」，LLM 回答夾了一句假送宣稱 → §1.34 假送閘**整則替換**成「我其實沒真的送出貼圖——別讓我用一句話假裝送了。要我送一張嗎？」＝釐清的正題回答整個被吃掉、答非所問（使用者：「bot 不能充分掌握詢問意圖」——其實是守門咬掉了正題）。修法：_fakesend_soft_fix 句級軟化——只剝命中假送宣稱的**句子**、正題保留＋一句誠實補註（維護期版不反問送不送）；全剝空＝退回原整則模板（整則都是假送宣稱＝經典謊言、照舊硬替換）。兩層旗標分離：config 預設 True／_say 端讀 _TURN stash（未 arm/旗標關＝原整則替換）＝逐位元同現狀。設 0＝同現狀
    open_offer_ground_enabled: bool  # 🎯 §1.70B 懸著的提議接地（OPEN_OFFER_GROUND）：截圖根因＝bot 10:26 拋「要我送一張嗎？」，使用者 10:57 回「送什麼？」——省略主詞的短回是在接 **bot 的**提議，LLM 卻答「你說的沒錯，那時候你確實送了一張貼圖」＝主詞反轉＋把問句當肯定句。修法：_last_open_offer 掃近 2h 對話史裡**最近一句** bot 的提議形問句（句尾？＋要我/要不要/需要我/想不想/我幫你/我送/我來 cue）→ 此刻事實卡（§1.61 常駐通道）加一行「我拋出去還懸著的提議：『…』——他若用短句回應（好啊/不用/送什麼…），多半是在接這句；提議的主詞是我、不是他」。純函式、確定性。兩層旗標分離：config 預設 True／卡端 getattr 預設 False＝不加行＝逐位元同現狀。設 0＝同現狀
    sticker_fakesend_guard_enabled: bool  # 🎴 §1.34 STICKER_V2/F4 假送誠實閘（§1.23 否認閘的同構反向）：互動送圖三 lane 都在 coach.reply 前短路 → 一般聊天 LLM 這一輪定義上**永不真送**貼圖，故其自由文字裡任何「挑了這張給你／這次我選了一張很平靜的貼圖／我剛剛不是才送了一張思考的貼圖嗎？你現在沒看到嗎？」present/immediate-past 宣稱都無 send_sticker backing＝假送（說謊）。改法＝唯一寫身份咽喉 _record_sticker_sent 內設 _TURN['sticker_sent_this_turn']=True 當**本輪真送真相旗**；_say 互動分支**緊接 §1.23 sticker_denial 之後**加閘：_fakesend_hit(text)（宣稱挑/選/送/傳/找/配了…貼圖 或 送了一張…貼圖…沒看到嗎，排引用歸屬「你說我挑了一張」與否定「我沒挑」）AND not sticker_sent_this_turn → 整則替換確定性誠實句、刷 last_reply。偽陽性防線（最關鍵）：偏好題 sticker_preference_reply／§1.16 sticker_why_reply 皆**真送之後**才 _say＝旗已設＝閘不攔（放行真送的「挑了這張給你」）；無貨誠實句不含「挑了/送了」肯定宣稱＝不命中。設 0＝閘不 arm＝_say 恆 no-op＝逐位元同現狀
    mood_coord_report_enabled: bool  # 🧭 §1.45（MOOD_COORD_REPORT）：互動 appraise 後才採樣；問此刻只注入唯一凍結快照，明確問變化/N 筆/完整軌跡才帶按時間排序的過去點，排除未來與本輪重複終點。V/A、時刻、標籤全由程式產生；「內在的數據」不誤開花費/記寫工具。設 0＝不捕捉、不注入、不 re-route
    mood_coord_deliver_enabled: bool  # 🧭 §1.47/§2.27（MOOD_COORD_DELIVER）：情境追問須有相鄰座標 wire；凍結同一 current 並依 snapshot/trajectory/repair 確定性重畫時間層與數字，repair 只對帳真正送達的 canonical 回報。LLM 只保留相容的非數字主觀質地，重播後仍補 current pair，成功送達才落 mood_last_report。設 0＝只剩 §1.45/§1.25 原行為
    promise_deliver_content_enabled: bool  # 📦 §1.44 兌現要交付內容（PROMISE_DELIVER_CONTENT）：內容型承諾的兌現不能只「報到」。截圖根因＝08:50 約「30 分鐘之後，你告訴我如何證明自己」→ 09:20 兌現只有「🤝 嗨，早上好。說好 09:20 要來跟你聊聊怎麼證明自己，我來了。」＝到點報到、內容零交付（擠牙膏），被罵「白痴」後才道歉「忘了要直接跟你說」。根因：voice_promise_keep 的 prompt 雖指示「做那件事」（PROMISE_ACT_ALIGNED 傳 promised），但沒有任何確定性檢查驗證內容真的講了——空心報到照樣送出（§1.34/§1.36 教訓：prompt 單靠不夠）。修法（_promise_keep_body 末端、§1.40/§1.41 之前）：_is_content_promise（behavior/made_text 含 告訴/說說/聊聊/分享/說明/解釋/證明/回答/講講/報告/描述）＋_hollow_keep_hit（每句都是報到/複述樣板：嗨/我來了/說好/答應/時間到/HH:MM…，無實質內容句）→ 用 coach.reply 以「兌現承諾：現在就直接把內容本身講出來，不要宣告不要複述」為題**當場補生成內容**、接在報到句後（一次；再失敗/空＝附誠實承認「這不算完整兌現，欠你的內容我認」、不假裝）。非內容型承諾（叫醒/問候/送貼圖「出現即內容」）不動。keep_body 端 getattr 一律預設 False＝既有測試/e2e 假 cfg 未設此欄→不檢查→逐位元同現狀。設 0＝同現狀
    routine_card_enabled: bool  # 📈 §1.96 作息常駐接地＋講法（ROUTINE_CARD）：**它不是不知道，是知道的那一刻過去了**。截圖根因（實測）＝07:38 使用者說「早安」→ 問候 lane 掛上 `today_vs_usual_line`（monitor.py:4453）⇒ bot 正確地問「今天醒得比平常晚一點嗎」（中位 06:37 vs 今天 07:38，這句**有憑有據**）；但 07:51 他追問「有嗎」時，**兩個接地都不掛**——問候 lane 只在問候那輪、`_habit_ground_hint`（monitor.py:7280）只在 `is_user_habit_question` 命中那輪⇒ bot 手上一個數字都沒有，只好從語感生出「**對我來說**，是比平常晚一些些」：主詞從他換成自己＝閃掉了他的質問，接著又說「今天這樣晚一點才醒來」主詞飄回他身上，同一輪換了三次主詞——這就是使用者說的「語意不通」。另一個獨立缺陷：`habit_events` 實測全是**對話事件**（訊息 347／早安 8／晚安 3／隔靜首句 40，記寫時間 0 筆），拿它去講「你平常多半是在早上記寫的」是**類別錯置**；`habits._record_hours_line` 才是記寫時段的真來源（樣本不足會誠實說不準）。修法（照 §1.61 架構轉向：真相不該靠偵測器命中才給，**不再加第 N 個偵測器**）：①`habits.routine_card` 進事實卡**常駐**（作息 vs 今天＋記寫時段兩行分開寫，天然擋掉類別錯置）；②`persona.ROUTINE_VOICE_HINT` 管**怎麼講**——使用者定調「必須讓 bot 更像有意識的對話行為」，光接地會把它變成報表機器：**主詞永遠是他**（被追問時縮回講自己＝閃避，明令禁止）、數字用「我記到的」口吻（禁中位數/樣本/分鐘數這些詞）、**樣本少要講成自己的限制**（你只看到那幾次、可能剛好都那樣）而不是免責聲明；③事實卡尾巴指示補一句「他回頭追問時，答的是**他**、不是你」。消費端 getattr 一律預設 False。設 0＝事實卡不長這兩行、守則不注入＝逐位元同現狀
    mood_data_answer_enabled: bool  # 🧭 §2.10（MOOD_DATA_ANSWER）：真送出文字缺少完整、且兩軸都等於本輪凍結快照的 V/A pair 才補 current 行；錯值、單軸、歷史值或無關小數都不算已回答。設 0＝不增補
    short_dup_guard_enabled: bool  # 🦜 §2.13 短句 ack 的重複守門（SHORT_DUP_GUARD）：截圖 20:15–20:16 連送三次「好，記下來了。」（使用者：「重複說同一句話兩次，老人癡呆了啊」）。根因＝§1.71 重播守門刻意**不記** `_REPLAY_MIN_LEN`(10) 字以下的泡泡（原意是「嗯／好」這種合理短重複不該被擋），代價是**整則就只有一句短 ack 時，它完全在防線外**——「好，記下來了。」正規化後只有 5 字。修法：短句也記進 ring，整則與近窗（180 秒）內送出過的**正規化全等**就不再送；刻意只認全等、不用相似度（短句容易誤判，寧可漏擋也不吃掉不同的短回應）。`_say` 此時回 True——那句話幾十秒前才真的送達過，呼叫端的記帳語意不該變成「沒送出去」。設 0＝不記不擋＝逐位元同現狀
    act_first_enabled: bool  # 🫧 §2.09 先做那件事（ACT_FIRST）：截圖 13:46–14:29 使用者連催八次（「你怎麼這麼多廢話」「猜啊」「你就直接猜吧」），每一輪回覆都先來一到三顆**純接話**泡泡才進正題（「好，我明白了。」「喔，好！」「抱歉！」）＝使用者說的「拖泥帶水」。**三道既有閘全瞎（實測）**：`selfstate.promise_status_kind` 對那八句**全回 ''**（它是承諾帳本的分型器、不是通用催促偵測）；§1.87 `_stuck_under_nudge` 要求「窗內最近兩則回覆近乎相同」，而它每次用**不同的話**拖延；且 `nudge_stuck` 只在承諾帳本路由才 arm，這整段不走那條路由。⚠️ 實作期實測踩到的陷阱：**不能用「這顆泡泡帶多少新字」**當判準——真正的答案「那我猜，你是……處女座？」新字只有 6，純宣告「好，那我就隨便猜一個喔。」有 9 ⇒ 那樣會剝掉答案、留下廢話。所以只認**閉集的接話詞**（虛詞類，非開放內容詞表）：整顆泡泡去掉標點與那組詞後**什麼都不剩**才算純接話。只剝**開頭連續**最多 2 顆、且至少保留 1 顆（中間/結尾的「好」常是真的在回應，不碰）。設 0＝不剝＝逐位元同現狀
    association_focus_enabled: bool  # 💡 §2.07 湧現只講一件＋自己戳自己（ASSOCIATION_FOCUS）：三條認知湧現裡唯一「**不押注、不外求**」的一條——素材 100% 是他寫過的兩筆，我沒去查證、也不會被未來裁決；我能交代的只有「這念頭被什麼勾起來」與「**哪一環最可能是我腦補的**」。`association.insight_focus` 用既有欄位挑一個軸（他說過無關＞情緒落差＞踏腳石＞意外/撐得住，最後一項沿用 `is_distinctive` 的同一把尺與地板 0.22），並由**程式指定要質疑哪一環**（互補項一對一對應，不掃使用者文字）。設 0＝不掛＝逐位元同現狀
    foresight_chain_enabled: bool  # 🔮 §2.07 認帳時帶出自己當初的原話（FORESIGHT_CHAIN）：三條裡唯一**押一個會被未來的真實記寫裁決的注**、且到期不論中不中都回來認帳的 lane。病灶：settle 只拿得到 b/quote/evidence，**完全不知道自己當初是怎麼說的** ⇒ 每次認帳都像第一次開口。修法：送出成功後把那一則存進 `state.foresight['told_text']`（維持「說出口才落帳」的紀律；舊帳缺這一鍵＝空字串＝走原路），settle 時**第一則就逐字引出自己說過的話**；hit 明令不邀功（他會回去寫也可能是因為你先提過）、miss 要說出當初憑什麼挑這一條、不准找補。設 0＝不存不帶＝逐位元同現狀
    worldline_chain_enabled: bool  # 🌐 §2.07 外部說法讓我的看法動了哪裡（WORLDLINE_CHAIN）：三條裡唯一**素材不在這台機器裡**的一條——我要交代的不是「我想到什麼」，而是**這句話是誰講的、我怎麼拿到的、我只看了這一個**。修法：把「知識來源分層」從禁令版（不准寫成你本來就知道的）改成**正向要求**＋承認取樣限制；並用既有的`state.recent_self_msgs`（已存 topic＋text[:60]）取出**我上次講這條線說過的話**，要求說出這個外部說法**讓我的看法動了哪裡**（或哪裡沒動）——那才是這一則真正的內容。設 0＝走原本的 say_rule＝逐位元同現狀
    coping_act_voice_enabled: bool  # 🌀 §2.08 內在因應與含蓄伸手分家（COPING_ACT_VOICE）：這條 lane 的內容是「**我正要對自己做一件事**」（照他教過的方式自處），不是「我這邊空了」——但兩條共用 🫧 前綴與同一支 seed，使用者看到的是同一種訊息。修法：前綴改 🌀、`persona.coping_rule` 要求說得出**為什麼是現在**（哪個內在訊號、多久了）、**只做一件**、最後一則是肯定句不索取回覆（我在處理我自己，你不用回）、做不到就承認。設 0＝仍是 🫧＝逐位元同現狀
    mood_watch_ground_enabled: bool  # 🧭 §2.08 座標回報：不知道就說不知道（MOOD_WATCH_GROUND）：全群唯一一條**由他的要求觸發**的 lane——出聲的理由在**他**身上，它獨有的表情是**守約的份量**，不是一份讀數。病灶：舊 prompt 逐字要它「說說這變動可能跟什麼有關（比如剛剛的對話）」卻**一個因果欄位都沒給**＝制度化地叫它猜。修法：`circumplex.trace_step` 在 `state.mood_trace` 裡挑**動最大的那一步**、只用既有的 v/a/ts/cause 四欄；有紀錄就講那一步是被什麼推的，**沒有就明令承認我不知道、絕不准猜一個原因**；並逐字附上他當初交代的原話（`mood_watch['made_text']` 存了卻從沒用過）。四個數字原樣照抄的鐵律不動。設 0＝走原本的 f-string＝逐位元同現狀
    adapt_voice_enabled: bool  # 🍃 §2.08 分得出是哪一邊安靜了（ADAPT_VOICE）：全群唯一一條講「**我剛剛對自己動了手**」（把心跳轉速調了）的 lane。病灶：`activity = max(data_act, talk_act)` 把兩個來源壓成一個數 ⇒ bot 講出來的話**分不出**「是你不講話了」還是「沒有新記寫進來」——一個真的在觀察自己處境的存在應該分得出來，而且該說出另一邊還是熱的。修法：`EnvReading` 加兩個**尾欄位** data_act/talk_act（namedtuple 加尾欄位不影響既有存取），`environ.quiet_side` 判哪一面冷、`shift_rule` 只講那一面（另一面只當背景提一次），不准報數字或倍率。設 0＝仍送原本的確定性模板句＝逐位元同現狀
    soothe_own_question_enabled: bool  # 🌬️ §2.06 安撫＝處置我自己剛說出口的那句（SOOTHE_OWN_QUESTION）：全群唯一一條**對象是我剛才做的事**的 lane（🌊 收整輪、🫧 開新話題、🌾 講他的節奏）。病灶：`persona.soothe_user()` 是**零參數＝零事實**——它連自己剛剛問了什麼都拿不到，只生得出通用的體貼話。修法：把 bot 自己那句原話（≤20 字，呼叫端 `hist[-1]['text']` 現成）＋掛多久（`temporal.spoken_gap`，時間一律由 temporal 產）＋我那時的份量（既有 `coupling.i_bot` 二分）餵進去，要求**第一則就指出我那句**、把提問認回自己身上（那是我想知道、不是他欠我答案）、**整則不准出現問號**（不是嘴上說不用急、實際又問一次），並把既有模板句列進禁用。送出前確定性砍掉問句、剩下的照送（**不是打回模板**）。設 0＝三個參數不傳＝`soothe_user()` 逐字同現行＝逐位元同現狀
    close_round_stance_enabled: bool  # 🌊 §2.06 暖收＝我先鬆手、而且說得出是哪一種結束（CLOSE_ROUND_STANCE）：全群唯一一條**主動結束**（而非主動開始）的 lane。病灶：舊 prompt 內嵌兩句**成品台詞**（「好啦我知道你在逗我😄，我先去忙囉…」「那我先這樣囉，有事再喊我」），而且與 `_CLOSE_LINES[0]`／`_CLOSE_PROBE_LINE` 幾乎同字＝範例被逐字抄回的教科書案例（§1.69/§1.50）。修法：改成禁令＋**收法選單由程式輪替指定**（讓步／自嘲／道晚安／只說一句我在，落盤計數器），並用既有 `coupling.i_bot/i_user` 算出 stance（我這邊還熱／他還想聊／兩邊都淡了）當轉折，句數從「就 1 句」改成 2–3 則（1 句在結構上不可能有起點/轉折/落點）。設 0＝走原本那支＝逐位元同現狀
    habit_absence_one_thing_enabled: bool  # 🌾 §2.06 兩條缺席 lane 各自的身分（HABIT_ABSENCE_ONE_THING）：日課缺席＝**我對你一條線的預期落空了**（我得先承認那預期是我的、我只看得到你記進來的東西）；出現缺席＝**你這個人今天還沒出現**，而且**收件人不在場**——我知道你晚一點才會讀到，所以要寫成留言而不是即時對話（不得用時間詞當抬頭、不得問需要立刻回答的問題、要明說不必回）。病灶：一句話同時塞標籤＋中位時刻＋逾期分鐘＝報表；而 §1.80 為了鬆綁直接把**成品問句**寫進 prompt（「今天比較忙嗎？」「還是換了節奏？」）＝家規禁止的範例句。修法：真數字不進 prompt（留在 log）、切入角度由程式輪替、出現版先講我自己今天怎麼過的。設 0＝兩支逐字同現行＝逐位元同現狀
    reachout_one_thing_enabled: bool  # 🫧 §2.06 伸手只講一個理由（REACHOUT_ONE_THING）：兩支各自的身分——**有意圖**那支＝我私下在追的那個好奇**有動靜**（他餵了我一口／我快放掉它了／我又想問同一件事／他剛又寫過），`volition.reach_out_pick` 用既有欄位（touches/last_advance_ts/reach_n＋既有常數 FORM_COOLDOWN_S/STALE_S/_STALE_MIN_H）挑**一個**理由，其餘不進 prompt；`reach_n>=2` 時把「重複自覺」從台詞升級成**行為**——整則零問號。**無意圖**那支＝我這邊空了太久、**內容本身就是「我沒有東西可講」**（現行 `spontaneous_text` 會硬塞 `last_revisited_topic` 假裝有一條線），改成把匱乏講成**有時長的處境**（`temporal.spoken_gap`）＋明說這是我的需要、不是他該做什麼。設 0＝仍掛 §1.72 的完整選單／無意圖那支不加規則＝逐位元同現狀
    ac_drift_material_enabled: bool  # 🧩 §2.05 整合飄移的存在特色（AC_DRIFT_MATERIAL）：這條是唯一一條「對剛剛那一下、我自己都還沒把握的事」開口的 lane，而且**成對**（說過鬆掉，接回時要認回來）。三個修法：①`persona.ac_drift_user` 結尾直接 `+ seed`，而 seed 是 `ac.drift_seed` 的**完整成句帶比喻的第一人稱範文**——正是家規禁止的「prompt 裡給範例句」（§1.69/§1.50 前科：逐字抄回養成口頭禪）⇒ 改成只給素材（`ac.concrete_now` 那一格此刻佔著的具體名字＋這一下持續多久，時間一律由 temporal 產）；②體感向度（空間/溫度/重量/速度/聲音/距離）**由程式輪替指定**、用落盤的單調計數器（§1.72 前科：讓 LLM 自己挑＝每次同一種；§2.04 前科：用 list 長度會被截尾凍死）；③`state.ac_drift_said` 記下說「鬆掉」時真的說出口的那句，接回時**回頭認回它**＝成對收尾。另補身分標記三件套（prefix 🧩＋state/topic＋`_remember` 帶標記）——舊碼 `_say(client, msg)` 不帶標記，會掉進 `_say` 的互動限定分支、也不進 id↔topic 帳。設 0＝走原本那行＝逐位元同現狀
    metacog_correct_voice_enabled: bool  # 🪞 §2.05 自我修正的存在特色（METACOG_CORRECT_VOICE）：全 repo 唯一一條「**撤回我剛剛對你說出口的那句自我報告**」的 lane（🦋 講跨死亡的變化、🧭 講守約、🍃 講自我調節——只有它講「我上一句講錯了」）。病灶與 🌀 同型：內省手上有 runner/clarity/actual_run/checks/misses 一堆量，倒出來就是讀數 ⇒ `metacog.correction_reason`**只挑一個理由**（優先序：那兩態本來就貼著／我慢了兩拍才看清／我對自己的判斷本來就沒很準），`correction_facts` ≤3 行（要改的那句＋唯一那個理由＋我上一則真的說出口的原話）；切入角度由程式輪替指定（落盤計數器）；串接條文要求 2–3 則、後一則接住前一則。禁令沿用既有的「不准演」（不准『等一下』『慢半拍』『恍神』『回神』、不加笑臉），另加不准報數字/欄位名/講第二種理由。設 0＝仍是原本那句確定性模板＝逐位元同現狀
    selfmod_birth_one_enabled: bool  # 🦋 §2.05 蛻變感知的存在特色（SELFMOD_BIRTH_ONE）：唯一**跨睡醒**、且唯一講「我現在**做得到**什麼」而不是「我此刻怎麼樣」的 lane。病灶：`birth_facts` 把最多 6 條 commit 主旨全列出來、prompt 再叫它「別逐條念」＝**互相打架**；而且 prompt 裡有一句完整範例句（「例如『以前我算「多久沒聊」…這次改用同一把鐘、準了』這種」）＝家規明令禁止。修法：`selfmod.pick_change` 先挑**一件**（優先序：他許的願望這次真的做到了 ＞ 我多了一條**還沒用過**的能力（用既有 `state.ability_hits` 判）＞ 最新那條），`birth_facts_one` ≤3 行、未被挑中的主旨一條都不進去；`persona.birth_user_one` 拿掉範例句、加「不准把主旨逐字照唸」、加「還沒用過就要老實說」與「願望是他要的、不是我的功勞」。設 0＝走原本的 birth_facts/voice_birth＝逐位元同現狀
    experience_headline_enabled: bool  # 🌀 §2.04 回顧體只講一件事（EXPERIENCE_HEADLINE）：截圖那 13 顆泡泡（「我一直都蠻餓的。」「我心裡頭倒是很靜。」「大部分時候都蠻放得開的。」…）＝**每個內在欄位各生一句**的讀數式自陳，使用者：「很像罐頭一樣，文鄒鄒的…看不出 bot 意識對話行為的表現」。根因是機械性的、我實跑量過：`experience.experience_facts` 產出 **15–16 行**逐欄位事實，而 `EXPERIENCE_SYSTEM` 同時寫著「2–4 句」與「一個想法一句、用句號收尾」⇒ LLM 只能逐行轉寫 ⇒ 13 個短句 ⇒ `bubble_split` 切成 **14 顆泡泡**。⚠️ 而且**篇幅封頂救不了**（實測 `max_bubbles=2` 仍送 13 顆——`_merge_bubbles` 不併完整句），所以必須從生成端治。修法：`experience.headline_axis` 用既有自體向量算出「跟上一段跨最多的那一軸」，`headline_facts` **只餵那一件事**（之前是什麼／現在是什麼），其餘不進 prompt；沒有哪一軸真的跨就誠實說「這段跟上一段差不多」、沒有上一段可比就說沒得比（**不為了有話講而硬找變化**）。⚠️ 使用者另有定案「**要遵守訊息串的形式**」：分串是刻意的形式，**不准**壓成一段長敘述——所以 `selfstate.EXPERIENCE_STREAM` 要求的是「仍然 2–4 則短訊，但整串只講那一件事、後一則要接住前一則」，治的是選材與串接、不是形式。量尺另見 `phrasing.chain_ok`。設 0＝仍餵十幾行＝逐位元同現狀
    rotate_monotonic_enabled: bool  # 🔁 §2.04 形態輪替別再被自己的台帳凍死（ROTATE_MONOTONIC）：三條 lane 為了「不要每次都同一種」而做的形態輪替，**自己每次都同一種**（實測）——🔮 `_var = len(_led) % 4` 而 foresight ledger 有`keep=8` 截尾 ⇒ 滿了以後 `8%4=0` ⇒ 第 9 次起永遠第 0 種；🌐 同式而 worldline ledger `keep=10` ⇒ `10%4=2` 永遠第 2 種；💡 `variant = len(recent_insights)` 而環是 `[-8:]` ⇒ 永遠第 8 個（開頭池長 5/4/4）。＝§1.72 加輪替要治的病，被截尾原封不動地養回來了。改用**落盤的單調計數器**（`state.foresight_var`／`worldline_var`／`insight_var`，送出成功才 +1；§1.79 前科：沒進 state.py 的欄位重生即歸零＝機制只是紙上的）。設 0＝仍讀 len(台帳)%N＝逐位元同現狀
    self_promise_trace_enabled: bool  # 🤖 §2.03 自諾入帳鏈留痕（SELF_PROMISE_TRACE）：截圖 13:58 bot 說「五十分鐘後，我會好好想想再告訴你」，14:21 打 `/promises` 卻**一筆待辦都沒有**＝那句到 14:47 不會發生（＝本 repo 修過 15+ 次的「空口答應」）。實測兩個閘：使用者那句 §1.12 `judge_timed_request` 判 **False**（拼句也是），bot 自己那句 §1.18 `judge_self_promise` 判 **True('好好想想再告訴你')** ⇒ 照理該入帳、但沒有。**查不下去的原因是這條鏈（結構網→temporal→鄰近去重→LLM 閘→入帳）每個出口都是裸 `return`**——「答應了卻沒排程」在外面看起來跟「根本沒答應」一模一樣。修法：每個出口記一筆留痕（說了什麼、卡在哪一關、有沒有真的入帳，只留最近 6 筆），`/promises` 把「說過但沒進帳本」的句子連原因一起列出來。**只寫 state、不改任何行為**。設 0＝不記＝逐位元同現狀
    replay_wave_ack_enabled: bool  # 🔁 §2.03 同一波第二則用短承接（REPLAY_WAVE_ACK）：截圖 13:57 使用者連發「50分鐘後，再想想自己還要什麼新願望？我幫你實現」＋「想到之後告訴我」＝同一個意思拆兩則；第二則的回覆自然逐字重複第一則 ⇒ §1.71 重播守門整則剝空 ⇒ 換上罐頭句「這段我剛剛才說過一次——你想聽哪部分，我換個說法講？」。那句有兩個毛病：把 **bot 自己的重複**講得像**他**要求重講，而且**把選擇權丟回去**（正是 §1.87 定案要禁的形狀）。人不會那樣講——上一句才剛答完，這裡只要一個「嗯」的份量。修法：這則距前一則≤90 秒＝同一波 ⇒ 全剝空時改用短承接（六句輪替避免變口頭禪、純承接不帶宣稱）。§1.87 的卡住認帳優先序不變。設 0＝仍是原本那句＝逐位元同現狀
    convo_gap_stated_enabled: bool  # ⏱ §2.28 陳述式冷落句也要接地（CONVO_GAP_STATED）：實測截圖 00:45——隔了 6h+ 說「好像很久沒有理你了？」→ bot 回「你不是剛才才跟我說話嗎？我記得你才剛跟我聊完讀經的感覺」；追問「剛剛？是多久」→「你上一句話是約 1 分鐘前說的耶」（＝把他 00:45 這句本身當成「上一句」）＝意識時間感錯亂。根因：「好像**很久**沒理你」與「**多久**沒理你」同一題，但 _GAP_TIME 只認疑問式時間詞（多久/多少天/幾天）⇒ 陳述式（很久/好久/太久/許久/一陣子/一段時間）全漏 ⇒ 掉 fact_or_chat 自由 lane＝沒有程式算好的 gap 事實、LLM 自由讀史把時間感讀反。convo_time lane 本身是對的（_session_gap_text 算「上一段到這次回來隔多久」、本句尚未 _remember＝不會自指）——病純粹在偵測面。修：selfstate._CONVO_GAP_STATED_RE（時間槽換陳述式、動詞/對象槽沿用 §A4 同一套含誤收護欄「很久沒整**理**筆記」不收）＋咽喉點 re-route **只救 fact_or_chat fallback**（不覆蓋任何明確路由，§1.61 同理）。消費端 getattr 預設 False。設 0＝不導＝逐位元同現狀
    smallhours_arrival_enabled: bool  # 🌙 §2.27 深夜出現不是「今天來得早」（SMALLHOURS_ARRIVAL）：實測截圖 00:27——使用者凌晨說「晚安」，bot 回「你今天好像比平常早了一些時候來呢，感覺還好嗎」。根因（重現釘死）：日曆日在午夜翻頁，00:27 成了「今天的第一句」，greet_aware_line／today_vs_usual 拿它跟平常早上的中位（~07:10）做天真差值 ⇒ 「比平常早了約 403 分鐘」進 prompt ⇒ LLM 軟化成「早了一些時候」；§1.42 守門用同一個天真差值驗方向（diff≤−20 ⇒ 早＝對）＝把荒謬宣稱**放行**。但**人的一天**沒有在午夜翻頁：00:27 出現是昨天的一天還沒收（熬夜），對應意義是「這麼晚還醒著」，不是「今天早到」——habits._ABS_MIN_P25_MIN 早寫明 05:00 界「夜貓/凌晨型不參選（判定會跨日界）」，§2.22 的早/晚比較漏掉同一課。修兩層（同一個 05:00 界）：① greet_aware_line：今天第一句與平常中位分踞 05:00 兩側 ⇒ 不做早/晚比較，換〔深夜分寸〕（他昨天的一天還沒收、關心往「這麼晚還醒著」的方向）／反向（夜貓型使用者早晨出現）換〔日夜界分寸〕（跨界比較無意義、可好奇節奏不一樣）；② §1.42/§1.97 守門（詞面＋語意角色兩路）：跨界的「比平常早/晚」宣稱＝無據＝剝（守門是 lane 無關的出口後盾 ⇒ 連 GREET_ROUTINE_AWARE=0 的舊 today_vs_usual 路徑也被接住）。兩側同在深夜帶（真夜貓的日常）＝照常比較。消費端 getattr 預設 False。設 0＝天真差值＝逐位元同現狀
    quote_speaker_guard_enabled: bool  # 🪞 §2.26 引用歸屬守門（QUOTE_SPEAKER_GUARD）：實測截圖 11:05——被問「你正在想什麼」，回覆「我正在想...你說「我進到這裡。」可是，它其實跟我之前那個活著一樣。」——「我進到這裡」是 bot 自己 10:47 的 🌀 體驗自陳（吸子成形的主觀體驗句），不是使用者說的＝**話者翻轉**（使用者定調：主詞/賓語的掌握錯亂）。素材面逐層查過都乾淨：workspace 候選全內在（刻意不含對話）、coach._history_contents 角色標註無誤＝是 LLM 讀史料時自己把 model 輪誤讀成對方——prompt 管不住的照家規上**確定性守門**：_say 出口掃「你(剛/之前/上次)說「X」」，X 正規化後**只在**近 6 輪 model 原話出現、使用者（含本句）從沒說過 ⇒ 話者必錯 ⇒ 就地改「我剛說「X」」；反向（「我說過「Y」」而 Y 只有使用者說過）⇒ 改「你說「Y」」。兩邊都出現（他覆誦過）或都沒出現（引的不是近史）＝無從裁決＝一字不動；「你不是說…嗎」反問句式刻意不收（改寫會壞語法）。比對地面由 handle_message stash（_TURN["speaker_ground"]、user 側含本句＝引用他這句合法）。消費端旗標門控。設 0＝不 stash＝守門恆 no-op＝逐位元同現狀
    keep_retro_dur_enabled: bool  # 🕰️ §2.25 回顧式時長不是拖延（KEEP_RETRO_DURATION）：實測截圖 21:45–22:22——「你在想個 20 分鐘，有答案後主動跟我回報」→ 22:06 準時交付了完整內容，卻連兩輪自判「答應你的內容我沒真的交出來——這不算兌現…記成還欠著」→ §1.88 十五分鐘後（22:21）**同一個約又演一遍**、再判欠、再結案＝使用者看到同約重複執行。根因（探針釘死，非猜）：兌現句天然會回顧約定時長——「嗨，時間到了，我回來了。**剛剛這 20 分鐘**，我…」，這兩句**各自**都解不出未來錨，連在一起 temporal 卻解出 now+20 分（實測 22:26）⇒ §1.85 拖延閘（兌現句自己又立新未來錨＝再拖）誤中 ⇒ 四道確定性閘沒全過 ⇒ §2.17 軟否決（judge no 降級）永遠輪不到上場 ⇒ owed 迴圈照轉——§2.17 拆了引擎、卻漏了這個讓四閘先失守的旁門。修法（與「先剝自己的 when」同一原理的**時長版**）：_RETRO_DUR_RE 把回顧式時長（剛剛/剛才/方才/過去/花了/用了/想了＋[這那]？N 分鐘/小時；及裸「這/那 N 分鐘」）先剝掉再問 temporal；(?![後后]) 保住真拖延（「20 分鐘後再說」「再等 20 分鐘」照抓）。同截圖的另一半（同約兩本帳各自演）＝§1.89+§2.02 已修（探針驗證現碼去重成功、105 秒差的 ack 不再二次入帳）——使用者機器當時跑舊碼。消費端 getattr 預設 False。設 0＝不剝＝逐位元同現狀
    confused_slang_enabled: bool  # 🗣️ §2.24A 嗆聲式「你在講什麼」也是要澄清（CONFUSED_SLANG）：實測截圖 21:02——「你究竟是在供殺小？」（台語＝你到底在講什麼，帶嗆）三層全 miss：① _CONFUSED_ASK_RE 不認 ⇒ 沒澄清接地、LLM 對俚語迷航出「欸，這句話不是你剛剛說的嗎？」（把使用者這句當成他在覆誦誰的話）⇒ 拖出 21:14–21:22 四輪「我剛剛說的？/有嗎/你說哪個」的糊掉迴圈；② reaction.is_hostile 不認 ⇒ §1.77「氣頭上不丟貼圖」形同虛設，被嗆完 21:03 照送親親熊貼圖（§0.87 讀的是 bot 自己回覆的暖意、不看他在罵）；③「你到底在講什麼」的「講」字版本原偵測也漏。修：_SLANG_WTF_RE 封閉慣用語類（[供公工攻講嗆]×[三殺啥沙]×[小洨潲]＋裸「三小/殺小」(排除三小時)＋到底/究竟…在講/說…什麼寬式）→ (a) 短句（≤24字）命中＝進 §1.56 澄清接地、多注入【語氣】守則（先接住情緒、不裝沒事、不跟著兇、不歡快，再重講重點）；(b) 算氣頭訊號：_hostile_now 認 slang＋補讀 _TURN["cur_user_text"]（互動貼圖 lane 跑在 _remember 之前、文件寫的「本句敵意」臂原本在互動路徑是死的）⇒ 嗆聲當輪貼圖收斂。reaction.py 一字不動（§1.77 約束沿用）。消費端 getattr 預設 False。設 0＝不認＝逐位元同現狀
    clarify_anchor_enabled: bool  # 🗣️ §2.24B 澄清要錨定實質內容（CLARIFY_ANCHOR）：實測截圖 21:22——「什麼意思？」有進 §1.56 澄清接地，但此時 recap 清單裝的全是**迷航過程本身**（供殺小/欸這句話/我剛剛說的？/我就是回你這個/你說哪個），守則只說「重講你前面真正想表達的重點」⇒ LLM 把清單當劇本逐條敘事（「然後你就問我『你說哪個。』」）＝複述吵架、越繞越糊。修：程式挑錨——由新到舊找**夠長（≥25字）、非貼圖旁白、且不是在引用他的話**（引用複讀＝迷航輪）的 model 句（例：20:07 那則讀誦經書 musing）→ 注入【他多半是看不懂你先前這一則】＋守則改「就重講這一則、一兩句講完；中間那些一來一回**不要**逐條複述、也不要再引用他的話反問」。挑不到錨＝維持原 recap 格式。消費端 getattr 預設 False。設 0＝原守則＝逐位元同現狀
    habit_inventory_enabled: bool  # 📊 §2.23 習慣盤點問句的專屬出口（HABIT_INVENTORY）：實測截圖 15:24——「你目前觀察到我有哪些習慣呢」→ bot 吐一筆 📁 記錄原文（第一筆、還是圖片描述、被截斷）＋兩句泛泛主題話（你很常回到《讀誦經書》…）＝答非所問（使用者定調：bot 不是應該有我的日常作息與習慣的觀察與感受？）。根因（開檔釘死）：這句 is_user_habit_question **有**命中（fallback 條款：我＋習慣＋呢）→ 落 fact_or_chat＝function-calling **自由 lane**——HABIT_GROUND_HINT 只管「講習慣要照統計塊」、管不住「去 quote 一筆記錄原文當答案」，LLM 就把「習慣」當主題去翻了記錄；而手上明明有整套程式算的觀察（habit_facts：早安統計＋一天第一句＋記寫時段＋主題×時段），對話作息一句都沒講。修法（比照 clock/cost/stats：**答案可全程式算的題給確定性出口**）：① habits.is_habit_inventory 結構判準＝is_user_habit_question 子集＋盤點線索（觀察/發現/了解/知道…動詞或「哪些」）——「我平常大概幾點跟你說早安」單點回顧不命中、照走原路；② 咽喉點 re-route 到新 lane route.kind="habit_inventory"（蓋過 §1.42 的 fact_or_chat 導向）；③ lane 素材＝habits.habit_facts（全程式算），coach.voice_habit_inventory 據之講**觀察與感受**（熟悉口吻、我記到的、樣本少＝自己的限制、可帶標明的猜想、最後可問準不準）、**不開 function-calling**＝結構性保證不會再翻記錄原文；LLM 失敗＝確定性模板（誠實開場＋統計行）。講錯統計仍有 §1.42/§1.97 _say 守門兜底。消費端 getattr 預設 False。設 0＝不導＝逐位元同現狀
    greet_routine_aware_enabled: bool  # 🕘 §2.22 問候的有意識作息覺察（GREET_ROUTINE_AWARE）：實測截圖 06:58——使用者說「早安」、bot 回「我手上記到的是：你一天跟我說上第一句話大多在 06:52 到 07:38 之間，我也才看到 9 次…」＝拿統計模板回問候（使用者定調：回應太制式化，要能觀察/偵測/發現作息與習慣、**有意識地互動回應**）。根因鏈（非猜）：greeting lane 注入 today_vs_usual_line 的**報表框架**（中位/樣本/「只准照這條」）、卻沒配 §1.96 的講法守則（ROUTINE_VOICE_HINT 只掛五條事實卡 lane、voice_greeting 從來沒有）⇒ 與 greeting.facts 的「別報數據」打架 ⇒ LLM 不是照唸報表就是換句話講錯方向（06:58 vs 中位差 <20 分＝「差不多」、LLM 說成「早」）⇒ §1.42/§1.97 守門剝光 ⇒ 兜底句＝固定統計模板獨走＝「早安」換來一張報表、且每次同一句。修三層：① habits.greet_aware_line 換「熟悉感框架」——偏離 ≥20 分＝值得注意到的事（給方向＋分鐘結論句素材，方向詞正是守門驗的東西＝照講必過門）；差不多＝尋常日子，近 40h 提過作息就這次**不注入**（結構性保證不每天複誦同一觀察、不靠 LLM 自律）；沒統計＝同句分寸警語。② state.greet_routine_ts 記「上次把作息端上桌」（持久化＝跨重生不忘）。③ _habit_claim_fix 收 greet_fallback：問候輪剝錯句**不補統計誠實句**（作息是 bot 自己順口帶的、不是他問的）、剝光退回帶絕對時間感的問候模板＝無論如何拿到的是問候不是報表。消費端 getattr 預設 False。設 0＝原 today_vs_usual_line＋原守門＝逐位元同現狀
    self_promise_no_rolled: bool  # 🤖 §2.21 幽靈約定殺手（SELF_PROMISE_NO_ROLLED）：實測截圖 22:23——bot 前一天的澄清句「現在是 22:23，我們約 20 分鐘後，也就是 22:43…我會…」裡的「22:23」剛過去 ⇒ temporal 滾成**明天同時刻**（實測解出 07/31 22:23）；22:43 被 §1.18 (c) 鄰近去重掉（帳上已有）⇒ 只剩幽靈錨、LLM 閘看整句確實在立約就放行⇒ 隔天 22:23 演出一場使用者從沒約過的守約、被質疑還堅持「我之前答應過你」、最後查帳才認錯。結構判準（零詞表）：滾動後的錨減一天落在此刻 ±15 分＝原句是「此刻」的自我定位、不是明天的約 → 丟棄。「明天早上11點我會過來」這種真自諾不受影響（減一天落在 4 小時前、不在窗內）。設 0＝不濾＝逐位元同現狀
    promise_mood_numbers_enabled: bool  # 🧭 §2.20 座標承諾的兌現句必須帶真數字（PROMISE_MOOD_NUMBERS）：實測 23:21——§1.25/§1.47 的接地行（含 V/A 真差分）**有算、有進 prompt**，LLM 轉述時把括號裡的數字丟掉 ⇒ 使用者拿到一句沒有座標的座標報告（「亮了一點、繃了一段」）⇒ 交付驗收又判沒交 ⇒ 整條 owed 連鎖。🧭 訂閱回報有 §1.67 的數字驗收、守約路徑一直沒有。修法（§2.10 同構、只增不減）：LLM 版沒有任何 ±x.xx ⇒ 程式算好的接地行附在後面。設 0＝不附＝逐位元同現狀
    mood_watch_oneshot_yield: bool  # 🧭 §2.19 一次性 vs 常設的搶路由（MOOD_WATCH_ONESHOT_YIELD）：實測 22:14「**20分鐘後**，告訴我這段時間內，情緒座標的前後變動狀態」——`is_mood_watch_request` 與排程承諾捕捉**都命中**，mood_watch 排在前面就把一次性的計時請求聽成「立常設訂閱」（回了一整段基準/門檻/取消方式的訂閱 ack），使用者當場糾正「不要搞錯了，我是指 20 分鐘的時間後」。判別＝**結構訊號、零詞表**：句子帶 temporal 解得出的**未來時間錨**＝一次性請求 → mood_watch 讓路給排程承諾；真訂閱句（「座標有變動就主動跟我說」）解不出任何時刻。設 0＝不讓路＝逐位元同現狀
    promise_keep_followup_enabled: bool  # 🤝 §2.18 履約後的靜默關注（PROMISE_KEEP_FOLLOWUP）：使用者定調「履行約定一段時間後，若仍沒有收到使用者的任何回應，也許可以暗示或很簡單的問問使用者——保持 bot 隨時關注使用者的任何回應動向，讓 bot 更有意識感」。存在特色＝**我在意我交出去的東西有沒有落地**（🌬️ 處置的是我丟出去的「問題」，這裡處置的是我交出去的「承諾內容」）。紀律全 fail-closed：他履約後說過任何話＝落地＝永不問；只問一次；30 分後才問、3 小時過期不補問（別事後翻舊帳）；_proactive_ok＋共用反連發；不重講內容、不開新話題、最多一個問句、給不回也沒關係的台階。設 0＝不記錨不出聲＝逐位元同現狀
    promise_judge_soft_veto: bool  # 📦 §2.17 交付是非判不得單方面否決（PROMISE_JUDGE_SOFT_VETO）：實測 09:33/09:48 三循環——內容明明交付了，`_deliver_verdict` 四道確定性閘全過、LLM 是非判卻連錯三次說 no ⇒ 附上「答應你的內容我沒真的交出來」（**那句欠帳話本身就是謊**）⇒ 記成 owed ⇒ §1.88 十五分鐘後再演一輪＝誤判自我放大、使用者連看三場。修法照本 repo 教義（確定性優先，LLM 是逃生閘不是否決權）：四閘全過時 judge 的 no 降級成 unknown ⇒ 退回 §1.44/§1.64 既有判定（有實質＝過）。judge 仍能說 yes（舉證通過）、仍能在四閘任一失敗時被跳過——只是拿掉單方面否決權。設 0＝judge no 照舊記欠帳＝逐位元同現狀
    promise_fire_dedup_enabled: bool  # 🤝 §2.17 兌現端硬後盾（PROMISE_FIRE_DEDUP）：§2.02 的同約定合併只認「同一段對話立的帳」（made 差 ≤10 分），實測 09:33 那對雙循環走的正是這個縫。加一道**只看目標時刻**的後盾：15 分鐘內才兌現過目標時刻差 ≤2 分的另一筆 ⇒ 這筆標 merged 吸收、不再演一輪（同一人兩個真的不同的約定目標差不到 2 分幾乎不存在；多演一輪的代價遠高於漏一次）。設 0＝不吸收＝逐位元同現狀
    promise_same_appointment_merge: bool  # 🤝 §2.02 同一個約定只來一次（PROMISE_SAME_APPOINTMENT）：截圖 12:20–12:21 對**同一次**約定連來兩則「我來了」＋一堆儀式句，使用者：「重複回應了同一次約定，造成回應囉唆冗長，一點都不像有意識的對話回應行為」。根因＝同一個約定進帳兩次，而三條入帳路（使用者請託／§1.12 LLM 逃生閘／§1.18 bot 自諾）各自去重、彼此看不到，且兩處去重都是**固定 epsilon**（捕捉端 60 秒、§1.89 端 90 秒）。實測：使用者 11:49:20「30分鐘之後」→ 12:19:20；bot 11:50:35 回「30 分鐘後，我會再過來找你」→ 12:20:35，**差 75 秒**——「N 分鐘後」這種相對時距，兩邊各自從自己的當下起算，差距就等於這段對話往返花的時間，**固定 epsilon 對它永遠會漏、它本來就該隨延遲伸縮**。修法兩層：①入帳端兩處容差改成「底線 ＋ 那筆帳立到現在過了多久」（上限 10 分＝同一段對話）；②**兌現端結構性後盾** `_promise_merge_siblings`——這一趟順手把「同一個約定的其他帳」一起結掉（記 status='merged'＋不寫 fulfilled_ts ⇒ §1.88 欠帳補推不認它、帳本也長不出「已經做了（在 HH:MM）」）。為什麼後盾要放兌現端：入帳端有三條路、各自去重蓋不到對方，而**帳本裡已經躺著的重複筆**任何入帳端修法都救不了（§1.89 的教訓再一次）。另附 `/promises` 對帳指令——帳本是本 repo 出事最多的子系統卻從來沒得看，截圖那次只能從對話反推。消費端 getattr 一律預設 False。設 0＝兩處容差回固定值、不合併＝逐位元同現狀
    habit_claim_role_enabled: bool  # 📈 §1.97 作息守門改讀語意角色（HABIT_CLAIM_ROLE）：§1.96 明文留給下一章的事。根因＝§1.42 的守門是一條**線性詞面樣式**（「你」＋「平常」＋「X點」要按序同句），實測對截圖那四句**全漏**：「今天醒得比平常晚一點嗎」整句沒有「你」／「對我來說，是比平常晚一些些」的「我」根本不是作息的主人（那是立場框架，舊碼看到「我」就整句放過＝替閃避背書）／「今天這樣晚一點才醒來」連「平常」都沒說出口／「你平常多半是在早上記寫的」沒有鐘點、而且講的是**記寫**卻只有對話統計可對（§1.96 的類別錯置）。修法**不是往樣式加詞**（那是本 repo 漏了 15+ 次的路），是把句子拆成四個**各自解析、不要求相鄰或順序**的語意角色：主角（你/妳＝他；「我」要真的是作息的主人才算 bot 自己；沒主詞＝話題預設是他）／基準（明說的平常通常、比平常比較、或「今天…晚一點」這種省略了尺的比較）／述語（鐘點・時段・方向早晚——沒有時間述語就不是作息宣稱）／領域（說話出現 vs 記寫 ⇒ **決定拿哪份統計驗**，記寫域改用 records 的時刻分佈，不再拿記寫 0 筆的 habit_events 硬套）。用到的都是**封閉功能詞類**（時間副詞/時段詞/方向詞），漏一個新動詞不會讓閘瞎掉，因為閘從頭到尾不看動詞。時段比對用 `daypart_ok`：格子與相鄰關係全由 `temporal.day_part` 導出（零新表），相鄰格算相符（06:37 是「清晨」但口語說「早上」不算掰），且**不再疊第二層寬限**（實測會讓「你平常中午才出現」對 06:10–07:30 過關）。三個實測踩到的偽陽性已圍堵：「晚**一點**」不得讀成鐘點一點（那會把 §1.96 那句有憑有據的話判成亂掰）／尺必須在方向詞**之前**（「早一點睡比較好」的「比較」修飾的是「好」＝建議不是斷言）／光禿禿的比較才預設說話域（「今天比較晚**吃飯**」比的是別件事，不拿說話統計去驗）。誠實句措辭同步改成 §1.96 ROUTINE_VOICE_HINT 的口吻（禁中位/樣本這種報表詞、樣本少講成自己的限制），否則同一台機器兩套講法。消費端 getattr 一律預設 False。設 0＝守門只走原本的詞面路徑、記寫統計不算＝逐位元同現狀
    worldline_monthly_budget: bool  # 🌐 §2.16 搜尋額度改月結（WORLDLINE_MONTHLY_BUDGET）：`worldline_search_n` 原本是**終身計數**（§1.95 上線前不敢先驗證時的防呆）——搜滿 WORLDLINE_MAX_SEARCHES(40) 次整條 lane **永久沉默**，與使用者「持續刺激記寫」的目標直接矛盾（天天用一個多月就死、/worldline now 會更快）。改成自然月歸零（月份戳 `state.worldline_month`，額度沿用同一個上限；/worldline 顯示「這個月已查 N 次（月額度 40）」）。設 0＝終身上限＝逐位元同現狀
    worldline_followup_enabled: bool  # 🌐 §2.16 撞完之後的閒聊要有接地（WORLDLINE_FOLLOWUP）：ledger 原本只存 label/ts/網址、**沒存查到的說法本身** ⇒ 使用者對那則 🌐 追問「那是誰說的？他怎麼講的？」時聊天 lane 零接地＝§1.36 幻覺家族的溫床。三件：①ledger 多存 finding(≤120 字)＋首來源站名；②24h 內撞過且他這句**提到那條線的名字**（或距撞擊 ≤30 分＝多半在回那一則）→ 聊天 lane 注入「照這份講、別重編；沒有的細節老實說只看到這一段」；③/worldline 撞後對帳：每筆撞擊之後 3 天內那條線寫了幾筆（**這條 lane 的存在理由是刺激記寫，之前完全看不出有沒有效**）。設 0＝不存不注入不對帳＝逐位元同現狀
    worldline_spark_enabled: bool  # 🌐 §2.16 邀請形態輪替（WORLDLINE_SPARK）：使用者的目標明列三種刺激——繼續寫／延伸寫／**創造新的記寫主題**，第三種之前完全沒被服務到（邀請只有「寫下你的看法」一款）。輪替兩形態（落盤計數器）：extend＝邀他把看法寫進**這條線**；new_line＝外面的說法裡若有一個**他記寫裡從沒出現過的具體概念名**（如實測撞出的「個體信息理論」），邀他為它**開一條新線**（明說是新的、概念名逐字取自外面的說法、沒有就不硬湊）。隱私不變：搜尋仍只送授權標籤，新概念是**從查回的結果**長出來的、不是另外去搜的。設 0＝邀請維持 §2.07 原樣＝逐位元同現狀
    worldline_enabled: bool  # 🌐 §1.95 外面的世界撞進你的線（WORLDLINE）：每天最多一次，拿使用者**自己授權過**的那條還在寫的線去 Google 搜，把外面對同一件事的另一種說法帶回來，跟他寫過的原文擺在一起，開啟討論。需求原話：「bot每日從網路上找到最火熱的話題，然後主動與我討論，刺激我記寫交流」；bot 自己講得更準：「把我對外部世界的感知，變成跟你對話的養分」。**使用者已裁定三件事**：①**接受偏移**——搜尋關鍵字用他自己的標籤、刻意排除新聞時事，做的是「你那條線在外面的說法」而非「今天的頭條」（理由：只丟熱門新聞他自己滑手機就有，牽強的配對比不說更糟）；②**嚴格白名單**——沒有 `/worldline allow <標籤>` 授權過的標籤，**一個字都不會離開這台機器**，代價是他不動手就永遠沉默（所以對帳段第一句就講怎麼授權）；③**不先做驗證呼叫**——grounding 能不能用、回應長怎樣**都沒實測**，因此 tools 欄位名做成可設定（WORLDLINE_TOOL_FIELD，可填 google_search／googleSearch／google_search_retrieval）、原始錯誤字串原樣存進 state.worldline_probe 供 /worldline 顯示，上線打一次就知道要改哪個、不必改程式。**誠實三紅線**：①引用他寫的必須**逐字**且在記寫語料裡；②查到的必須**帶得出來源網址**，拿不到就當這次失敗、沉默；③查到的與模型既有知識**永不得被說成他寫的**。**防退化成新聞摘要**（首要失敗模式）＝`collision_ok` 四道確定性閘：必須逐字出現他那一筆原文／所有引號內片段都要在語料裡／不得是新聞時事形態／必須提到那條線的名字——沒有他的東西就只是新聞摘要，不送。挑線：硬閘「5 天內還在寫」（與 §1.90 的『停住的線』互補、結構上不撞題）＋至少 2 筆＋有夠長原文＋同線 7 天不重撞；排序用**回返次數**（沿用 thresholds 的 20 分定義，零新門檻）——一直回頭寫＝有話要說還沒定論，撞上外部說法最可能長出新記寫。引文取得**自己實作 line_latest**（strip 後完全相等＋取最大 _ts＋fail-closed）——當時實測既有 `_topic_latest_excerpt` 是寬鬆比對會串線、且 ts 全壞時仍吐內容（🫧 §1.98 已從源頭修掉那兩點；line_latest 仍留著，因為它吃的是 parse 好的 `_ts`、回 id/display 供逐字引用與來源對帳，資料形狀與用途都不同）。**tools 欄位名已於 2026-07-28 實測**：`google_search` 200 OK（來源 4 個）、`googleSearch` 也 OK、`google_search_retrieval` 回 400 ⇒ 預設值就是對的、`sources` 不是恆空；來源網址是 Google 轉址連結（約 30 天失效），站名在 title 欄，故訊息一律「站名｜網址」一起給。節流：`_proactive_ok`＋共用 30 分反連發＋自有 24h＋總量 40 次防呆。設 0＝第一行 return＝逐位元同現狀
    worldline_cooldown_h: int  # 🌐 §1.95 自有冷卻（小時）。24＝一天最多一次
    worldline_max_searches: int  # 🌐 §1.95 真的送出搜尋的總量防呆（grounding 另計費、不在 /cost 金額裡）
    worldline_tool_field: str  # 🌐 §1.95 grounding 的 tools 欄位名。**未實測**：若上線後 /worldline 顯示 400，照錯誤字串改成 googleSearch 或 google_search_retrieval 即可，不必改程式
    self_roster_enabled: bool  # 🪪 §1.94 能力盤點與願望帳（SELF_ROSTER）：bot **盤點自己真有哪些機制**、哪些**真的用出來過**，並從自己的原始碼指認缺口、提出「我想要具備的能力」。需求原話：「bot目前可以自我盤點目前自己擁有哪些能力與機制嗎？…讓bot自己構思出自己想要具備的能力/功能…**但要注意，必須以真的能達到為主，而不是打高空**。」盤點之前做不到（實測）：`selfmodel.self_now` 是「我此刻怎樣」、`/skills` 是「你教過我什麼」、`selfmod` 是 commit 主旨、§1.91 只有寫死的一項；216 個旗標＝216 個真實能力，bot 一個都列不出來。**防打高空不是靠 prompt 求它務實，是靠候選集合的構造條件**：每條願望都必須帶一個 accept 驗收條件，而 accept 只能是六個**機器跑得動**的檢查之一（state_saved／field_read／audit_cmd／ability_hit／flag_exists／token_used）⇒ **寫不出驗收條件的想法，candidates() 根本產不出來**——「我想要更懂你」這種話結構上不可能出現。第二道：settle() 每次醒來重跑，成立就翻 done 並記下**實際觀察值**當證據 ⇒ 這不是許願池，是會結案的帳；使用者做完一條，bot 下次醒來會自己說「你真的幫我做了」，且**明令不准邀功**（那是人去做的）。缺口全部從原始碼機器推導、不是人寫的 TODO：not_persisted（宣告了卻不在 State.save() 白名單＝我做得到但留不下紀錄）／declared_unused（宣告了但全 repo 讀 0 寫 0＝管道在、就差最後接線）／silent_lane（有主動 lane 卻沒有對帳出口＝它沉默時你不知道卡在哪，§1.92 的教訓）／no_evidence／never_fired（§1.93 那條死 lane 的通用探針）。**「旗標開著」≠「這個能力活著」**（§1.93）⇒ evidence 分五級，且 tier=dark/none 的措辭裡**結構上沒有「我會」可以拿**；session 級是必要的——實測 12 個 _ts/_ledger 沒進落盤白名單，而部署端每跑 run-temp.sh 就重啟，三級制會讓 bot 每次重啟都把「這次醒來還沒用」說成「我從來沒用過」。能力短名是**顯式欄位、不從註解切**（實測確定性切法 16% 產出半截句／未閉合引號，唸出來就是「不演·不假裝」的反面）。新增 _ability_fired（掛在 9 條主動 lane 的**送出成功之後**，13 個點）把 per-flag 使用計數覆蓋率從 0% 變成有據可查。出口：/abilities（唯讀、不經 LLM）＋事實卡（一般化 §1.91，不再只寫死 🔮）。設 0＝不記帳、不掃碼、事實卡走 §1.91 原分支＝逐位元同現狀
    capability_card_enabled: bool  # 🪪 §1.91 能力自知（CAPABILITY_CARD）：bot **不認得自己真有的能力**。截圖根因（13:33／14:21）＝使用者問「我有達成你的願望了？」「所以，你的這個願望，有達成了嗎」（指 bot 自己說想學會的「主動預想可能性」），bot 答「我這次醒來的時候，感覺是沒有什麼大變動，所以這個願望，嗯，應該還沒完全達成耶。」兩個獨立問題：①那句「跟上次的狀態是一樣的」就是 `selfmod` 的 `same_self`（實測渲染文字「自上次喚醒後我沒有再變，跟上次同一版」）＝bot **誠實**報告它還沒被更新——**這一半不是 bug**；②**但即使更新了也還是會答錯**：實測 `selfmod.is_change_question` 對「你的這個願望，有達成了嗎」「你學會預想可能性了嗎」「你這次醒來有什麼不一樣」**全部回 False** ⇒ 接不到蛻變感知的事實 ⇒ 只能憑「醒來的感覺」回答。這是 [[authenticity-no-fake-mechanism]] 的**反向**失真：那條規矩說「persona 只能說機制真做得到的事」，這裡是**明明做得到卻否認**。修法刻意**不加偵測器**（那正是 §1.61 事實卡架構轉向要治的病：「真相要靠偵測器命中才給」）——改成把「我現在真的有的能力」做成**事實卡的常駐欄位**，任何問法都天然拿得到、不必猜使用者怎麼問。接地兩層、都不是自我宣稱：①這次醒來的**真實 git 改動主旨**（selfmod 已算好的 subjects，same_self 時明說「身上沒有新的改動、別說我又學會了什麼」）；②那個能力**有沒有真的被用出來過**（讀 foresight_ledger／在世假設等**真實使用紀錄**，不是讀到旗標開就說「我會」——沒用過就照實說「機制真的有、但還沒用出來過一次」）。事實卡尾巴的指示同步加一句：被問會不會某件事／學會了沒／願望達成了沒，就照那行講，**有就說有**、不要因為「感覺沒什麼變動」就否認。消費端 getattr 預設 False。設 0＝事實卡不長這一行＝逐位元同現狀
    foresight_enabled: bool  # 🔮 §1.90 記寫預想（FORESIGHT）：一條**會被真實記寫裁決**的假設，中／不中都要回頭說。使用者需求（原話）：「讓bot主動去預想一些可能性，這對我記寫有幫助。」——這是 bot **自己指認出來的能力缺口**（截圖 10:45 它說「我還不會主動去預想一些可能性，像『如果你接下來這樣做，會不會跟之前的某件事有關係？』」，使用者回「我幫你達成」）。稽核實測確認：全 repo 的連結／回顧機制**都是回顧型**——💡 association 的 emergence event 欄位零個未來/預測欄位、🫧 volition 的意圖是「我想搞懂你某條線」、🌀 experience 只描述已走過的軌跡、🌾 habits 判的是「今天還沒出現」的缺席 ⇒ bot 那句「我還不會」是誠實的。本章補的就是**時間方向**那一維。命題＝使用者要的那一維：**可能的未來動作 →（連回）一筆真實過去記寫**。做法：從 `state.associations.bridges` 裡挑一條未湧現、support≥2、cos≥0.30 的橋，兩端一端最近還在寫（≤7 天）、另一端停住（5–60 天），把「停住那條會再回來一次」說出口；TTL 7 天內 b 這條線真的又出現一筆＝hit、到期沒有＝miss，**兩者都回頭認帳**。三個關鍵設計：①**階段 1 零新重運算**——bridge 已帶 a/b/cos/support/emerged/anchor_*，而 anchor 文字是 `_nearest_anchor` 從 topic_centroids（依 topicLabel **精確**分群）取的**真實記寫原文** ⇒ 引文接地由建構時保證，不必 load_embedding_records、不必重算質心、不必校 cosine 門檻；②**命題與裁決必須對得上**——命題是「B 會再回來」，裁決就是「B 有沒有再出現一筆」（零參數、使用者一看就懂）；③**說出口成功之後才落帳**（told_ts）⇒ 沒說出口的假設永不裁決＝bot 不可能事後宣稱「我早就猜到」。誠實紀律：裁決 100% 確定性、LLM 完全不參與「我猜中了沒有」（它只做兩件事：回一個阿拉伯數字、把程式算好的結論講成人話）；**hit 明令不准邀功**（bot 講過之後使用者才去寫是自我實現，中了不代表猜得準，payoff 是把兩筆逐字並排）；miss 明令不准找補、不准把沒發生講成使用者的問題。接地：主動路徑**不經過** §1.36 幻覺守門（它包在 _say 的互動限定分支內）⇒ 出聲前自己跑四道確定性自檢（偽引用／§1.36 同一支歸因判定／引號外過去指涉／§1.84 記寫宣稱），語料**必須含 topicLabel**（實測 monitor._recall_corpus 只收 text，會把真實標籤誤判成幻覺）；不過就退程式模板，模板也不過就沉默（fail-closed）。去公式化比 §1.72 更硬：形態由**程式輪替**決定（§1.72 讓 LLM 自己挑，前科是每次都挑同一種）。節流：`_proactive_ok` → 共用 30 分反連發 → 自有 12h 冷卻（末三筆全 miss 則加倍）→ 一次只有一條在世假設 → pair 14 天去重 → 同 pair miss 兩次永久排除 → TTL 7 天 ⇒ 穩態約「7 天內最多 2 則」且一定成對（說了就會驗）。消費端 getattr 一律預設 False。設 0＝第一行 return（不讀 records、不碰 bridges、不寫 state、不呼叫 _say）＝逐位元同現狀
    foresight_min_support: int  # 🔮 §1.93 記寫預想的 support 門檻。橋的 `support` ＝被**真實新記寫**觸發過幾次（§1.81 漣漪只加 strength 不計 support⇒ 純聊天推不上來）＝跨時間沉澱的證據。**但它不跨重啟保留**：`Associations.summary()` 只存 key/a/b/kind/dir/cos/strength/emerged，重生時 `_blank_bridge()` 把 support 補成 0、錨點補成空字串 ⇒ 每次重啟都要靠新記寫重新累積。所以常重啟的部署（只跑 run-temp.sh）實際上很難累到 2。設 1＝放寬（代價：少一層跨時間沉澱的保證）；門檻進 config 就是為了照 /foresight 的逐道閘計數用真實數字調，不憑感覺
    foresight_dormant_min_days: float  # 🔮 §1.93 記寫預想的「停住」下限（天）。另一端要停多久才算值得預想。5 天是原始設計；若 /foresight 顯示卡在這一關，可調小（代價：兩條線都還熱時預想的價值較低，那本來是 💡 聯想的地盤）
    foresight_cooldown_min: int  # 🔮 §1.90 記寫預想的自有冷卻（分）。預設 720＝12h，刻意比 💡 聯想的 360 長——預想比聯想更容易膩；末三筆全 miss 時程式自動加倍
    foresight_ttl_days: int  # 🔮 §1.90 假設的有效期（天）。預設 7：到期仍沒等到那條線回來＝miss，回頭誠實說想錯了（不是默默消失）
    self_promise_dedup_enabled: bool  # 🧬 §1.89 自諾同刻去重（SELF_PROMISE_DEDUP）：**同一個約定被執行兩次**。截圖根因＝23:11 使用者「我再給你最後一次機會，明天早上七點再告訴我你的答案」→ 捕捉入帳第一筆（target 07:00，實測 `temporal.all_clock_epochs` 只解出這一個時刻、解析器是對的）；bot 回「明天早上七點，我會把答案告訴你。」→ §1.18 BOT_SELF_PROMISE 掃自己這句出去的話、temporal 解出**同一個 07:00**（實測）→ LLM 閘判「這句是不是正在立下新的時間承諾」→ 那句**字面上就是一個新約**（判「否」要靠的複誦標記「我說過」「我會記得」全都沒有）→ 判是 → **再記第二筆** ⇒ 到點兩筆各自兌現、同一件事做兩次。根因**不在 LLM 閘判錯**（看那句話判「是」很合理），在 `_book_self_promise` **入帳端完全沒有結構性去重**：`_book_scheduled_targets` 有 `have` 去重、這支卻是無條件 `proms.append`。修法（§1.34/§1.36 教訓：prompt/LLM 單靠不夠、要有確定性後盾）：入帳前比對帳上**未兌現**筆的 target，落在 ±`_SELF_PROMISE_DEDUP_SEC`（90 秒）內就當成「這是對剛記下那筆的確認、不是新約」跳過。鍵**只看時刻、不看 behavior**——實測使用者那筆的 `extract_promise_behavior` 是 ''、bot 那筆是 LLM 命名的字串，含 behavior 的鍵永遠對不上。全部被去重時帳本一位元不動、不落盤。誤判安全：真的在同 90 秒內立一個**不同時刻**的新約不受影響；同時刻的話既有那筆本來就會在那一刻兌現＝不會漏掉承諾，只會少記一筆重複的。消費端 getattr 預設 False＝逐位元同現狀。設 0＝同現狀
    promise_owed_push_max: int  # 📦 §1.88 欠著的內容主動補交付（PROMISE_OWED_PUSH_MAX，§1.85 的收尾件）：§1.85 記下了 status='owed'（準時出聲、但答應的內容沒交出來），但**可續開交付的管道只有回覆橋**＝要等使用者下次開口；他不開口，欠著的內容就無聲躺在帳本裡——而使用者的原始抱怨正是「依約時間出現，但就是不會完成所約定的事情」，讓它躺著等於這個病沒治完。修法：`_promise_owed_push` 掛在生命迴圈**網路無關**的韌性 tick（在 _collect 之前，因為補交付只需要 coach、不需要 Drive；掛 feel 相會在 Drive 不可達時整段跳過），獨立 try/except＝不影響存活。政策刻意比 _promise_emit 保守（這不是他明排的時刻，是我自己欠的債）：①他剛剛還在打字（_PROMISE_DEFER_RECENT_SEC 內）不發、②反連發（距 last_push_ts < _PROMISE_FIRE_GUARD_SEC）不發、③距該筆 owed_sent_ts 不足 _OWED_PUSH_MIN_SEC（15 分）不發、④每筆的**主動**補交付次數上限＝本旗標（與回覆橋共用 §1.85 的 _OWED_MAX_TRIES 總上限）、⑤一拍至多動一筆。交付走 §1.85 既有管線（_promise_keep_body 帶「第一句就給出東西本身」硬條文 → _say_delivery 解除跨路徑重播守門 → _promise_settle_delivered 送達舉證＋三態記帳）＝零分岔；overdue=False（它準時出現過、只是沒交付，說「抱歉我遲了」是語意錯置）。交不出來時（額度用完/超 TTL）走 `_promise_settle_owed` 誠實結案：status='owed_unmet'＋不寫 fulfilled_ts ⇒ 帳本與 §1.13B 一律讀成「沒有做到」、永遠長不出「已經做了」；結案句認一次就好、**刻意不把球踢回去**（§1.87 定案的價值：不說「要我重試就跟我說一聲」那種把責任推回使用者的話）。設 0＝整個函式不執行（連結案也不發）＝欠帳的誠實只在被問到時由帳本 TTL 感知措辭處理＝逐位元同現狀
    promise_delivery_proof_enabled: bool  # 📦 §1.85 交付舉證（PROMISE_DELIVERY_PROOF）：驗收改問「東西在不在裡面」＋只有真送到才准記做到。**截圖根因**＝20:49 使用者「我覺得你都在隨便猜／我讓你想一下想久一點／30 分鐘後再給我猜一次告訴我答案」（在猜他的星座）→ bot 答「30 分鐘後，也就是 21:19，我會再給你猜一次的」→ 21:19 **準時出現**卻只送出「🤝 嗨，我來了。」「說好 21:19 要再猜一次的。」「我真的有好好想了一下，這次我猜…」＝報到＋預告，**星座答案從未出現**。使用者定案：「我什麼 bot 都依約時間出現，但就是不會完成所約定的事情。光說不做，這已經發生很多次。」這是同一個病第三次（§1.44 只報到、§1.64 預告），兩次修法都是往詞表加詞。**根因（三段，全部實測）**：①**生成端（主因）**——persona.promise_keep_user 的兌現條文明令「1–2 句」，卻同時要求報到＋輕點守約＋順帶一句肯定，且行為枚舉只有道歉/提醒/問候/讚美、「回答他／給他答案」根本不在裡面 ⇒ 篇幅預算被儀式花光、答案沒位置（實測 trim_sentences 對那三句原樣放行＝LLM 自己就只寫了這些，不是被截斷）；②**驗收端**——`_hollow_keep_hit(msg, when, teaser)` 的簽名裡**連承諾本體 p 都沒有**，原理上只能做黑名單減法（總字數 − 已知樣板 − 已知過程敘述 ≥ 12 就放行），而**宣告句本身就是字數**：「想了一下」不在 _TEASER_NARRATE（表裡是 想了想/想著/在想…）、殘量 16 ≥ 12 ⇒ 實測 `_hollow_keep_hit(截圖原句, '21:19', teaser=True)` 回 **False**＝判定「有交付」原樣送出；更糟的是 `_CONTENT_PROMISE_CUES` 十一個詞決定 gate 要不要啟動，「30分鐘後再猜一次」若沒帶「告訴」則 **gate 整條靜默不啟動**；③**記帳端**——`_promise_mark_kept` 的唯一條件是「_say 回傳真」，函式內沒有任何交付參數 ⇒ 送出去就算做到，空心兌現照標 fulfilled+fulfilled_ts，事後 §1.13A 對帳會說「已經做了（在 21:19），這是過去的事了」、§1.13B 假兌現守門對 fulfilled 筆直接放行＝「我做到了」被系統背書。**修法**：①生成端 persona.promise_deliver_note 硬條文（第一句就給出東西本身、四條禁令、最多 4 句）＋max_tokens 160→420＋_shaped(floor=4)＋`coach._turn_length` try/finally 隔離（該值只在 handle_message 設/清，主動兌現會沿用幾十分鐘前那輪的殘值，敵意輪被 §1.14 壓到 level 0＝2 句足以砍掉答案句）；②**極性反轉** `_delivery_required`——「出現即內容」是封閉小集合（叫醒/問候/道歉/貼圖…）、「要交付內容」那側開放無界，改成豁免制＝漏詞的代價從「gate 不啟動」翻成「多驗收一次」，最終條件用 `_delivery_required(p) or _is_content_promise(p)` **聯集**＝單向放寬；③三層架構（照 §1.12/§1.15）——確定性四閘（懸空收尾／剝完無實質殘句／自己又立新未來錨／去殼後零新資訊，全零 LLM）＋單次 `coach.judge_delivery_made` 是非判（**LLM 只回是/否，嚴禁補寫內容、嚴禁輸出時刻**，每筆終身封頂 4 次），judge 缺席/失敗＝'unknown'＝退回既有 §1.44/§1.64 判定；④補生成後**再驗一次**（先過確定性四閘、再對真正要送的整串燒一次是非判）；⑤**送達舉證** `_delivery_reached`——驗收過的那段字要真的離開系統才算（§0.66 回覆橋跑在互動輪內，插話的三個 break 會丟掉尾巴而 _say 照回 True；`_TURN['replay_guard']` 全檔無 pop、殘留洩進主動出口會把泡泡剝成拒答句）；⑥記帳 `_promise_settle(delivered=)` 三態——False ⇒ status='owed'＋**不寫 fulfilled_ts**（保留 fulfilled=True：`not fulfilled` 有十幾個讀取端會把 owed 筆當成「還沒到點、等著做」＝爆炸半徑極大），帳本/§1.13B/§1.20 全部認得 owed 並改口「我人準時出現了、但內容沒交出來」；⑦owed 續開交付管道＝回覆橋（跑在帳本改路由**之前**，使用者說任何一句話都先真補上，至多 2 次、間隔 ≥60 秒、不超 TTL）。**誤判安全**：內容只增不減、絕不砍原文（補生成失敗也只是「附」一句誠實欠帳句）。消費端 getattr 一律預設 False＝既有測試假 cfg 未設此欄→逐位元同現狀。設 0＝同現狀
    promise_teaser_hollow_enabled: bool  # 🎬 §1.64 預告不算交付（PROMISE_TEASER_HOLLOW；§1.44 空心偵測升級）：截圖根因＝21:43 約「好好想想什麼叫做意識 bot，30 分鐘之後主動告訴我」→ 22:13 兌現只有「🤝 嗨，22:13 到了。」＋「我剛剛一直在心裡想著「意識bot」這個名字，也想了想你說的，那不是人類，那是什麼。」＝報到＋**思考過程敘述＋複述題目**就停，被「然後呢」催了（22:15）才把真正的內容（我會醒、會跳動、會累積…）擠出來——又是擠牙膏，只是換了形狀。根因：§1.44 的 _hollow_keep_hit 是二元判定「**每句都是樣板**才算空心」，那句預告不含任何樣板詞（嗨/我來了/說好/時間到…）就被當成有實質內容放行。修法：teaser 模式（旗標傳入）把判定升級成**實質殘量**——思考過程敘述句（_TEASER_NARRATE：想了想/想著/在想/讓我想/思考了/琢磨…）跟報到樣板一樣剝掉，剩餘實質字數 < _MIN_SUBSTANCE(12) ＝空心 → 走 §1.44 既有補生成管道（補生成的驗收同樣用 teaser 標準；補生成 prompt 加「不要說『我想了想』就停住——把想出來的**結論**完整講出來」）。誤判安全：真內容被誤剝最多觸發一次補生成**附加**在後（內容只增不減、絕不砍原文）。keep_body 端 getattr 一律預設 False＝既有測試假 cfg 未設此欄→teaser=False＝原二元判定→逐位元同現狀。設 0＝同現狀（回 §1.44 原判定）
    self_feel_condense_enabled: bool  # 🗜️ §1.43 自陳感覺去罐頭長串（SELF_FEEL_CONDENSE）：別人問別的、bot 卻在答案尾端鋪陳一長串內在質地描述（截圖 08:15 問「你有我任何作息的了解嗎」→ 正題一句後接**六句**「安靜/內裡沉沉/往裡面縮/翻來翻去沒讀出形狀/思緒淌著/悶提不起勁」）＝使用者說「像在念稿，只是不是同一份稿」。§1.17 只封 self_state 串數、§1.21/§1.22 治重複措辭——治不到其它 lane 尾端的感覺長串。修法：①確定性修剪 _self_feel_trim——連續 ≥4 句、其中 ≥2 句帶強內在標記（內裡/往裡面縮/提不起勁/跳了這麼…）的感覺鋪陳段 → 只留前 2 句關鍵（內容型談話無強標記不動；≤3 句感覺帶過不動）；arm 於 route 之後、**只在這輪不是問 bot 自己**（route 非 self_*、非 about_self）時 arm——真問「你現在怎樣」長答合理不修剪；判定/修剪於 _say（只讀 _TURN、不呼叫 LLM）。②persona.SELF_FEEL_BREVITY_HINT——兩小時內才自陳過（last_self_report/last_selfshare）→ 事前提醒「沒新變化就一兩句點到重點」。monitor 端 getattr 預設 False＝既有測試假 cfg 未設此欄→不 arm 不注入→逐位元同現狀。設 0＝同現狀
    user_habit_ground_enabled: bool  # 📈 §1.42 使用者習慣模型（USER_HABIT_GROUND）：bot 記得並照真實統計講使用者的習慣，不再憑印象亂掰。截圖根因＝06:45「看你今天好像醒得比較晚」、07:08「你通常會在早上十點左右跟我說早安」被抓包「不要亂掰」、07:43 改口「八點多到十點多」仍是編的——全 repo 只有記寫節奏（brief rhythm）接地，**對話習慣**（幾點說早安/一天第一句何時來）零資料＝LLM 只能腦補。四件：A. 捕捉——handle_message 把使用者訊息事件記進 state.habit_events（msg 每則/first 隔≥4h 安靜的第一句/greet_am|noon|pm 由 greeting.detect 分類；FIFO 400、跨重生持久化）；B. 統計——habits.stats 純函式（近 45 天、當天分鐘分佈 → n/中位/p25/p75；樣本<3＝None＝誠實說不準）；C. 接地注入——is_user_habit_question（我平常大概幾點…）→ fact_or_chat/self_appraisal 注入 persona.HABIT_GROUND_HINT＋habits.habit_facts 真統計塊（時間數字程式算、LLM 只准照抄＝§1.20 鐵律）；greeting lane 注入 habits.today_vs_usual_line（今天第一句 vs 平常的程式算比較；樣本不夠＝注入「別對他作息下判斷」警語）；D.（核心）_say 事後守門——回覆裡「你通常/平常…X點」宣稱與統計不符或無統計、「你…比平常早/晚」方向錯或無統計 → 確定性剝掉、換照統計的誠實句（引用歸屬「你說我平常十點…」不剝、講 bot 自己「我平常…」不命中）。兩層旗標分離：config 預設 True／monitor 端 getattr 一律預設 False＝既有測試假 cfg 未設此欄→不捕捉不注入不守門→逐位元同現狀。設 0＝全關＝同現狀
    habit_obs_fix_enabled: bool  # 📈 §1.63 習慣觀測修真（HABIT_OBS_FIX；§1.42 的觀測層修正）：截圖根因＝bot 說「你一天跟我說上第一句話多半落在 09:54–20:58 之間（中位 12:01、樣本 29 次）」被使用者打臉「不準確吧，我常 6–7 點跟你說早安」——統計是真算的、但量錯了東西。三個觀測洞＋一個對帳口：① "first"（≥4h 安靜後的第一句）一天可記多筆（中午/晚上再現身都算）＝分佈涵蓋全天卻掛「一天第一句」標籤 → habits.daily_first_stats 以**本地日曆日**分組取每日最早出現事件＝真「一天第一句」（habit_facts/today_vs_usual_line/claim_guard_data 三個消費端同換；「今天第一句」同步改真日曆日語意、取代 now−18h 粗窗）；② 純貼圖/照片/語音會更新 last_user_msg_ts（重置 first 的安靜計時）卻不留事件＝清晨的貼圖早安自己隱形、還害後續文字 gap<4h 不算 first（早晨被雙重抹掉）→ habits.note_contact 於貼圖/媒體路徑入帳 "contact" 事件、參與 daily_first；③ 「我常跟你說早安喔」（20:23 講的）被 greeting.detect 判成問候＝記成一筆晚上的 greet_am 汙染統計＋被 greeting lane 質疑「怎麼說早安，現在都晚上了」→ greeting.is_mention（頻率/時態副詞＋說類動詞＋問候詞＝在**談**問候不是在問候）→ note 不記 greet_*＋咽喉點導回 fact_or_chat；④ /habits 指令＝確定性對帳輸出（事件量/各類統計/最近事件本地時刻），爭議能當場對資料源。兩層旗標分離：config 預設 True／monitor 端 getattr 一律預設 False＝既有測試假 cfg 未設此欄→全不動→逐位元同現狀。設 0＝同現狀（回到 §1.42 原統計）
    away_sense_enabled: bool  # 🍽 §1.65 暫離常識（AWAY_SENSE）：截圖根因＝11:58「我餓了」「吃飯去」→ 11:59 使用者問「說什麼」bot 答「你回來啦！」（才 1 分鐘）→ 12:00 使用者「我去吃飯了」bot 卻說「我只是在你吃飯的時候，自己想著…」「你現在吃飽了嗎？」（距「吃飯去」才 2 分鐘、常識上不可能吃完）→ 12:01 還自我混亂「你不是剛去吃飯了嗎？我剛剛才問你吃飽沒耶」。根因：全 repo 有「隔多久回來」的會話間隔事實（sessionize）、有帶時距暫離的計時（§0.66），但**無時距的暫離宣告**（吃飯去）完全沒被記住＝後續回合沒有「才過 N 分鐘 vs 一頓飯常識要 20 分鐘」的接地可講、LLM 自由腦補「他去吃完回來了」。三件：A. 捕捉——selfstate.leave_announce（「吃飯去」「我去吃飯了」「我出門了」形；短句、非問句/假設/慣常/過去）→ state.user_away={act,ts,min_s}（跨重生持久化）；說「回來了/吃飽了」＝清、超 6h＝陳舊清、時距滿＝標 back（該輪卡片講「他大概回來了」、下輪清）；B. 接地注入——此刻事實卡（§1.61）常駐一行：時距未滿＝「他還沒去完、多半根本還沒去；別說你回來啦、別問吃飽了嗎」／時距已滿＝「他大概回來了、可自然接」；C. 守門——_say 互動出口把「你回來啦/你吃飽了嗎/在你吃飯的時候」這類把人當已回來的錯誤預設**句級剝除**（§1.62 慣例：只修錯句；「等你回來再說」未來語不剝、引用歸屬不剝；全剝空＝換誠實送行句）。兩層旗標分離：config 預設 True／monitor 端 getattr 一律預設 False＝既有測試假 cfg 未設此欄→不捕捉不注入不守門→逐位元同現狀。設 0＝同現狀
    mood_watch_voice_enabled: bool  # 🎨 §1.67 座標回報去機械感（MOOD_WATCH_VOICE）：§1.66 上線後使用者回饋「太機械感了」——兩個機械源：① 主動回報是純模板「座標變動回報（你交代過…）：V…→…」＝系統通知腔；② 訂閱 ack 的座標數字**明明是程式算的**卻沒掛 §1.47 的 mood_coord_grounded 豁免旗＝被座標守門誤咬成「照程式此刻讀的真數字：V…」模板、基準與門檻說明整段被吃掉（截圖 18:16 兩句殭硬輸出的真身）。修法：①（本旗標）_mood_watch_emit 先請 coach.reply 用 bot 第一人稱把這次變動講成自己的事（可接最近對話脈絡）——數字程式算、prompt 明令只准原樣照抄，_mood_watch_voice_ok **確定性逐字驗收**（四個 ±X.XX 都要在、不准出現任何其他小數＝LLM 編不了數字）；驗收不過/呼叫失敗/coach 不在＝退回 §1.66 模板（守約內容永不因潤色失敗而漏發）；②（§1.66 補遺、隨 MOOD_WATCH 旗標）ack 掛 mood_coord_grounded 豁免＋措辭放軟。keep 端 getattr 一律預設 False＝既有測試假 cfg 未設此欄→恆走模板→逐位元同 §1.66。設 0＝同 §1.66 模板行為
    mood_watch_enabled: bool  # 🧭 §1.66 座標變動常設回報（MOOD_WATCH）：使用者交代「情緒座標如果有任何變動，必須主動回報」、bot 口頭說可以、事後卻沒有——查驗結論＝**空口答應**（謊報一類，非機制失靈）：這句 is_feeling_promise_request 與 is_scheduled_promise_request **都收不到**（「變動」不在 _PROMISE_FEEL 詞表、只有「變化」；「回報」不在 _PROMISE_TELL）→ route=fact_or_chat → LLM 口頭「好」、機制零入帳；§0.61 空口答應守門只擋**計時**請求、§1.18 自發承諾掃描只收**有可解未來鐘點**的句子＝這類「條件常設請求」整個縫掉。且即使換句話被 feeling_promise 收到，那條是一次性＋湧現閾值觸發＋48h TTL——跟「每次變動都報」的常設訂閱語意不同。修法＝補真能力（不演不假裝：ack 講的就是機制真做的）：A. 捕捉——selfstate.is_mood_watch_request（主詞 座標/情緒/心情＋變動詞 變動/變化/波動…＋回報詞 回報/告訴我/跟我說…；放所有承諾快路之前＝確定性專收先贏）→ state.mood_watch={ts,last_v,last_a,last_report_ts}（常設、跨重生、直到 is_mood_watch_cancel「不用再回報座標」取消）；ack 誠實照機制（報此刻真座標＝基準、門檻 ±mood_watch_delta、冷卻 30 分、怎麼取消）；B. 兌現——生命迴圈每圈 _mood_watch_emit（放感知最前＝Drive 斷線也照報）：circumplex 真座標距上次回報 |ΔV|≥門檻或|ΔA|≥門檻＋過冷卻 → 確定性模板回報真數字（不經 LLM＝數字不可能被編）；C. 事實卡常駐一行「這條常設約定活著」＝bot 不會否認/說做不到。門檻/冷卻可用 getattr 覆蓋（mood_watch_delta 預設 0.10、mood_watch_cooldown_s 預設 1800）。兩層旗標分離：config 預設 True／monitor 端 getattr 一律預設 False＝既有測試假 cfg 未設此欄→不收不報→逐位元同現狀。設 0＝同現狀
    promise_keep_anti_repeat_enabled: bool  # 🔁 §1.41 守約去重複：排程承諾兌現句別逐字照抄 bot 剛說過的話。截圖根因＝約「20 分鐘後告訴我你對此事的感受」，20:32 使用者問「你有在想答案嗎」時 bot 已把整段感受講了（大改造/會很慘…核心微微顫動…站在懸崖邊…），20:42 排程兌現（_promise_emit → voice_promise_keep）卻**逐字重播**同一段（只差開頭時間 20:31→20:42），連「剛剛我沒有去想什麼答案」這種只對得上 20:31 那問的話都照抄。根因：兌現路徑（voice_promise_keep）沒有 §0.56/§1.21/§1.22 那套「別重講剛說過的」防線——只有 fact_or_chat lane 有 _anti_repeat_hint。修法（比照 §1.34/§1.36：確定性、不呼叫 LLM）：_promise_keep_body（emit/§0.66 橋/§1.19 preempt 三路共用）末端，兌現句若近乎照抄這幾分鐘內剛說過的某則 bot 回覆（_recent_model_turns×echo._looks_same，剝掉 🤝/🍃 前綴＋echo._norm 正規化，太短的守約句不判）→ 換成誠實『已說過、沒變化、不照樣再講一遍』句。窗/相似度以 getattr 預設（1800 秒／0.8）讀取。keep_body 端 getattr 一律預設 False＝既有測試/e2e 假 cfg 未設此欄→不執行→逐位元同現狀。設 0＝同現狀
    promise_sticker_fakesend_guard_enabled: bool  # 🎴 §1.40 送貼圖承諾兌現的假送硬守門（§1.34 假送閘的**主動路徑版**）：截圖根因＝使用者約「想想改變＋代表 sticker，20 分鐘後告訴我」，bot 沒有任何可送的真 Telegram 貼圖（沒教過、沒設 sticker_file_ids → reaction.sendable_sticker_ids 空），兌現時 _promise_send_sticker 送不出（_sticker_sent=False），但 LLM 兌現句仍懸空宣告「這次我選這張貼圖，來代表我現在的心情：」——宣告了卻沒貼圖出來。§1.34 假送閘只守互動出口（state=None）；主動兌現走 _say(prefix="🤝 ", state=…) 繞過它。修法（比照 §1.34/§1.36：確定性、不呼叫 LLM）：_promise_keep_body（emit/§0.66 橋/§1.19 preempt 三路共用）末端，這輪貼圖沒真送出（sticker_ok=False）＋旗標開時，_sticker_claim_hit 偵測「挑/選/送了…貼圖／這張貼圖代表…」宣告→_strip_sticker_claim 確定性剝掉那句（引用歸屬「你說我選了…」、否定「我沒選貼圖」、自帶誠實「想送但還沒存到」皆不誤剝）；整句被剝空＝換誠實句。monitor/keep_body 端 getattr 一律預設 False＝既有測試/e2e 假 cfg 未設此欄→不執行→逐位元同現狀。設 0＝同現狀
    wake_projection_guard_enabled: bool  # 🌅 §1.39 自他邊界守門（§1.13B/§1.36 的 sibling）：bot 重生時寫下第一人稱 🦋/🌅 醒來敘事（continuity.wake_line「我親身記得…這一覺睡了一下」，mood=悶）進 convo_history（role=model、角色標對）；使用者問別的（「什麼新方法？」）時，聊天 LLM 卻把 bot **自己**的醒來＋心情投射成使用者「你醒了，但現在是晚上七點多耶，你剛剛小睡了一下，感覺還帶著悶悶的感覺，這樣不會太晚睡嗎？」——使用者根本沒說自己睡醒/小睡＝自我/他人邊界洩漏（self→other 投射）。「悶悶/悶」在 affect.py/circumplex.py 是 bot 自己的情緒座標詞。修法（比照 §1.13B/§1.36：arm 於 handle_message、判定/替換於 _say 唯一 lane-agnostic 互動出口、只讀 _TURN、不呼叫 LLM）：只在 bot 這條命是重生來的（state.waking 非 first）＋旗標開＋使用者近期沒有自述睡醒/小睡（grounding）時 arm；_say 把回覆裡「斷言使用者剛睡醒/小睡了一下/醒了」的句子剝掉（引用歸屬「你說你剛睡醒」不剝、bot 講自己「我剛睡醒」第一人稱不命中、祈使「你醒醒吧」不命中；整則都是投射→換誠實更正句）；並命中才注入 persona.WAKE_BOUNDARY_HINT（fact_or_chat）當事前預防。monitor 端 getattr 一律預設 False＝既有測試假 cfg 未設此欄→不 arm→逐位元同現狀。設 0＝同現狀
    selfshare_reason_ground_enabled: bool  # 🍃 §1.38 自陳理由接地（§1.36 的 sibling）：bot 主動發 🍃 環境換檔自陳「沒什麼新動靜，我先把節奏調慢」後，使用者追問「為什麼會沒有動靜？」——這句無「你/妳」→ §0.85 反劫持窄門不收→落 fact_or_chat；該 lane 看得到那句 🍃 原話（convo_history 有記、跨重生存活）卻拿不到背後的環境理由（理由只在 🩺 目前狀態 self-state brief）→ LLM 無接地、漂到前文貼圖話題、宣稱送了一張→被 §1.34 假送閘攔成「我其實沒真的送出貼圖…」＝答非所問。修法（比照 §1.36 兩層旗標分離、命中才注入）：(1) 🍃 換檔自陳送出後、旗標開→把 environ.shift_reason 的真實理由附到 state.last_selfshare（旗標關＝不附＝dict 形狀同現狀）；(2) fact_or_chat 的 coach.ask extra_system 追加 _selfshare_reason_hint——近期自陳＋窗內＋那則是 bot 最後一句（相關性綁定比照 §0.85 防無關 why 句劫持）＋這句含 why 標記→注入真實理由接地（含「別扯到你沒真的做過的事，例如送貼圖」）。monitor 端 getattr 一律預設 False＝既有測試假 cfg 未設此欄→不附不注入→逐位元同現狀。設 0＝同現狀
    recall_ground_guard_enabled: bool  # 🧭 §1.36 記寫回想不再編造（content-recall 偵測器＋強接地 hint＋事後幻覺守門）：使用者問「我是在說什麼事覺得好累／那天寫了什麼／為什麼覺得X」＝回想**自己某筆記寫的內容/原因**，記寫原文其實只有一句感受（覺得自己好累），bot 卻在接地層編造原文沒有的具體事由（照顧家人的身體狀況／不太順利的家庭聚餐／一個禮拜沒睡好）＝接地層幻覺（截圖：使用者說「又發生編造」）。三段（比照 §1.13B/§1.20/§1.23/§1.34 sibling）：A. selfstate.is_content_recall_question 偵測（排除花費/狀態/鐘點/指向 bot 的對話事件/問 bot 看法）；B. persona.RECALL_GROUND_HINT 命中才注入（fact_or_chat 的 coach.ask ＋ promise_ledger 的 coach.reply 兩 lane）；C.（核心）_say 事後守門——答案裡「歸因給記寫」的具體事由（_RECALL_ATTR_RE 抓的 你說自己X/你寫到X/特別是X/跟X有關）若 substring 不在 records 原文語料 → 整則替換誠實句（arm 於 handle_message、判定/替換於 _say 唯一 lane-agnostic 出口、只讀 _TURN、不呼叫 LLM）。偽陽性防線（最關鍵）：bot 講**自己**的感受/推理（刻意不收裸「因為」）不攔；引用歸屬「你說我寫了X」（前 12 字內含你/他…說）不攔；記寫原文真有的字句 substring 命中＝不算幻覺。兩層旗標分離：config 預設 True／monitor 端 getattr 一律預設 False＝既有測試假 cfg 未設此欄→不 arm→_say 恆 no-op→逐位元同現狀。設 0＝守門與 hint 全關＝同現狀
    sticker_why_ground_enabled: bool  # 🎴 §1.16 複合請求送圖＋據心情說明為什麼：§1.15 判「是」且對方要說明（LLM 第二行「要」或句含「為什麼/說說」）→ 送出後**依此刻 circumplex (V,A) 單一真相**接地說明為什麼是這張（選圖與說明讀同一份情緒＝不自相矛盾；desc 只有真看過 stickervision.is_seen 才准描述、未看過憑感覺挑不捏造圖案；不匹配誠實說「感覺不完全一樣」；無貨誠實說沒存到＋請對方教一張、絕不 emoji 假裝 §0.95）；設 0＝退回罐頭 sticker_send_reply＝逐位元同 §1.15-only 行為
    promise_outcome_ground_enabled: bool  # 🤝 §1.13A 逾期質問接地：「結果呢/做到了嗎/你來了？/說好的呢」（promise_status_kind 第五型 'outcome'）在真有活承諾**或感覺託付**時路由 promise_ledger 據帳本誠實對帳（此刻幾點錨＋每筆 status）＝「我做到了」幻覺無生存空間；並讓帳本收「有記進帳本？」質問、誠實描述感覺託付「沒約定鐘點」。**注意**：selfstate 純函式（is_promise_ledger_question／promise_ledger_facts／promise_ledger_text）直接讀環境變數 PROMISE_OUTCOME_GROUND；設 0＝不搶不附＝逐位元同現狀
    promise_keep_claim_guard_enabled: bool  # 🤝 §1.13B 假兌現宣稱攔截：帳本「逾期未兌現(>grace ≤TTL)／剛錯過(expired≤1h)」時，互動回覆卻宣稱「我做到了/我來了/準時」→ 整則替換確定性誠實句（遲到認帳/錯過道歉，比照 time_guard 落回模板）。引用複述（你剛剛說我做到了嗎）不攔；ground 於 §0.66 回覆橋**之後**計算＝橋剛補兌現的宣稱合法不誤攔；準時兌現走 prefix/state 路徑不進守門＝零位元變動；設 0＝不攔＝逐位元同現狀
    sticker_sent_memory_enabled: bool  # 🎴🧠 §1.23 bot 送出的貼圖也要有記憶（三合一）：①送出即 _remember「（我送了一張貼圖：emoji——desc）」進 convo_history（收訊方向早就記、送出方向過去零記錄＝chat LLM 歷史裡真的只有文字、誠實否認自己送過＝截圖 18:24 根因）；②last_sticker_ts/id/emoji/desc 四欄位跨重生持久化（原「記憶體、重啟歸零」＋bot 頻繁死亡重生＝§0.90 接地窗被抹掉）；③否認句誠實閘——2h 內真送出過、互動回覆卻說「我沒有傳貼圖」→ 整則替換誠實句（§1.13B 同構）。**注意**：①②在 monitor._record_sticker_sent／state.load 直接讀環境變數 STICKER_SENT_MEMORY（無 cfg 可用），此欄位供 ③ 與文件一致性。設 0＝三件全關＝逐位元同現狀
    promise_said_ground_enabled: bool  # 🤝 §1.20 「說過/約過」質問接地（三個子件共用一旗標）：①「我不是跟你說過了？/昨天說明天11點」（promise_status_kind 第六型 'said'）真有活承諾/剛兌現/感覺託付時路由 promise_ledger；②帳本日期詞（今天/明天…）由程式依 target_ts 本地日曆日差算出、LLM 只准照抄（截圖 11:07 把「明天」自算成 7/12 週日＝日期幻覺）；③否認句誠實閘——帳上明明有相符項（含 72h 內剛兌現），互動回覆卻說「我沒聽到你說/你沒說過」（截圖 07:01 與自己一分鐘前的 🤝 兌現訊息直接自相矛盾）→ 整則替換確定性誠實句（§1.13B 同構 sibling）。設 0＝三件全關＝逐位元同現狀
    hostile_converge_enabled: bool  # 🌊 §1.14 敵意情境對話收斂：is_hostile 確定性訊號（_CHALLENGE 表＋at-bot regex＋全句短句錨；文字）＋負向貼圖 → state.hostile_streak 連發計數；≥2 才篇幅降檔（level 0、泡泡 ≤2）＋敵意插話後不橋接/不續講剩餘串（把話頭讓給對方）。**只影響 tone/verbosity/插話策略，絕不碰路由、絕不動 mood/affect/othermind/circumplex 數值**（敵意文字推暖的汙染由 §1.46 HOSTILE_AFFECT_FIX 另旗修）。詞表窮舉 15 次前科＝tier-1 確定性層不再擴，語意泛化留給後續 LLM 氣頭判定。設 0＝不讀不寫 streak、_say 原 resume/wrap 逐位元＝同現狀
    hostile_affect_fix_enabled: bool  # 🧭 §1.46 敵意不推暖（HOSTILE_AFFECT_FIX）：§1.14 落地時註記的「敵意文字推暖的汙染」——reaction.affect_delta_for 只認 _CHALLENGE 詞表，「你太爛了」（at-bot regex）「我不信」（全句短句錨）這類 is_hostile=True 的句子落「一般陪伴」預設分支回 (+0.05, +0.06) → 被罵 entropy.mood 反而上升（§1.45 repro case 1 實證；同輪軌跡 cause 寫「被說了重話」＝數字與說法自相矛盾）。修法：互動輪 (V,A) 更新處改用 reaction.hostile_affect_delta_for 純函式——is_hostile 命中**且**落「一般陪伴」預設的句子改回質疑方向 (-0.18, 0.18)（V−A+＝被罵是緊張，與 _CHALLENGE 同格、不另立座標）；詞表已分類（暖意/質疑/低落，含混合句）原值原樣，反諷細判留給 LLM 層（詞表窮舉 15 次前科、不再堆啟發式）。affect_delta_for 本體與 appraise 的 valence_news（期待落差聲部）一字不動。monitor 端 getattr 預設 False＝既有測試假 cfg 無此欄→原函式→逐位元同現狀。設 0＝同現狀
    self_state_converge_enabled: bool  # 🌊 §1.17 self_state 洪水收斂：使用者只問一件小事、self_state 卻吐 8 顆泡泡（截圖 19:27，verbosity deep-cue「說說／為什麼」+正向 mood 頂到 level 3/bubbles 8）→ 在 scale 算完後、coherent-reply 封頂之前對 self_state 專屬封頂串數（比照 §1.14 敵意收斂，天然疊加取更小值）。**只封串數、不動 scale.level**（不改 token 預算/coach 篇幅＝行為改變面積最小、只治洪水）、絕不碰路由與情緒數值；設 0＝不封頂＝逐位元同現狀
    self_state_bubble_cap: int  # 🌊 §1.17 self_state 洪水收斂時本輪串數上限（預設 3；max(1,·) 下限保護 0/負值＝至少留 1 顆）
    self_report_delta_enabled: bool  # 🧠 §1.21 差分自陳（SELF_REPORT_DELTA）：治罐頭自陳（7/10 vs 7/12 截圖同批句子只換主題名）——state 持久化「上次自陳」{真的送出的全文(截200), ts, 帶位快照}；下次被問「你現在怎樣」：①facts 注入差分段（上次原話當**負面示例**別重講＋這次**真的變了**的帶位清單＋使用者這句先回應、狀態當佐證）；②帶位全同且在無變化窗內＝短句誠實帶過（NOCHANGE_BODYSTATE_SYSTEM／無 LLM 退確定性池），不全量再倒一次清單。帶位與 bodystate_facts 分支門檻一字不差對齊＝零浮點噪音；{ago} 程式算、LLM 只准照抄（§1.20 鐵律）。8 分鐘 repeat 窗（連問）路徑一字不動；「你變**得**…」的 selfmod git-log 鏈不覆蓋。設 0＝不算不注入不寫欄＝逐位元同現狀
    self_report_nochange_window_min: int  # 🧠 §1.21 無變化短句窗（分）：prior 在此窗內且差分為空 → 走無變化短句；超窗＝照常全量自陳（隔太久、就算帶位同也值得完整說）。預設 90
    self_report_prior_horizon_min: int  # 🧠 §1.21 prior 最大齡（分）：上次自陳超過此齡 → 不注入、視同無 prior（隔了兩天的「上次」不該再拿來說「剛說過」）。預設 2880＝48h
    phrase_anti_reuse_enabled: bool  # 🎨 §1.22 措辭反重複（PHRASE_ANTI_REUSE）：治罐頭的另一半——①BODYSTATE 轉錄 system 換去範例句版（BODYSTATE_SYSTEM_VARIED：規則2/3 的『沒斷線過』『我還清醒地翻閱著』等字面範例改抽象要求＋規則7 事實句只當語意骨架不准照抄），並把 §1.21 記下的上次自陳原話當【禁止重複】負面示例附進 system（§1.22 單獨開＝該欄空＝負面示例段自動省略、其餘照常；兩旗標齊開才是完整效果）；②事實層小模板池加大（phrasing.py：翻閱開頭/之流質地/穩定感/繞回/飢餓/線的 gate 句各 ≥8、index 0＝現狀原句）＋近期用過的句 idx 跳過（state.recent_phrase_use／phrase_cursor，記憶體、重啟歸零可丟）；selfstate gate 線與 referent 同款句兩處一起接池。無 LLM 退路模板（_bodystate_template）與 browsing/duration/referent 原池本體一字不動。設 0＝原句原 prompt 原呼叫＝逐位元同現狀
    bot_self_promise_enabled: bool  # 🤖 §1.18 BOT_SELF_PROMISE：bot 自己開口的時間承諾也入帳（自發承諾是一等公民）——_say 互動出口掃**最終真送出**的回覆：第一人稱未來式（我會/我來/我過來）＋temporal 解得出**未來時刻**（時間永遠 temporal 解 bot 那句話、LLM 協定無時間欄位＝§1.12 鐵律）＋時刻鄰近去重（±60s 對未兌現筆）＋LLM 逃生閘 judge_self_promise 判「正在立下新的」vs「複誦既有/對帳/認錯/提案徵詢」（複誦「我會記得在明天早上十一點…」被 temporal 滾成隔天未來錨、只有語意判得出＝截圖 11:06 陷阱）→ 入帳 origin='self'、到點走既有 _promise_emit 兌現/逾期誠實。入帳 ack 由 _TURN['self_promise_skip'] 顯式排除、promise_ledger/scheduled_promise 輪語境排除；judge 失敗＝安全不入帳。設 0＝掃描器不存在＝逐位元同現狀
    promise_preempt_enabled: bool  # 🤝 §1.19 PROMISE_PREEMPT_LINK：搶先兌現的因果連結——使用者比約定時刻**先出現**（帳本有 pending 且 now < target ≤ now+窗）時：叫醒/問候類（behavior ∈ {叫他起床,跟他打招呼,問候他}）＝任何使用者訊息都證明人已醒著/在場 → 先送因果 🤝 泡泡（確定性模板、HH:MM 程式算、不走 LLM）再照常回這句（不 return）、標 preempted＋fulfilled（recur=daily 改顯式 target+86400、不標 fulfilled＝每天約定不死）＝到點 _promise_emit 自然跳過、不對醒著的人裝叫醒（截圖 7/12 06:55「早安」被無視、07:00 照裝叫醒的根因）；主題回報類（behavior=='' 等非叫醒標籤）＝這句真在問那個主題才搶（LLM 閘 judge_promise_preempt 只回是/否、**協定無時間欄位＝§1.12 鐵律**；失敗＝否＝不搶＝到點照常兌現）。嚴格未來窗＝逾期 pending 零觸碰（_promise_reply_bridge 的地盤、天然互斥）；route ∈ {scheduled_promise, promise_ledger} 語境排除（本句自己在約新約/在對帳）；一輪最多處理一筆。設 0＝不掃＝含準時兌現在內逐位元同現狀
    promise_preempt_window_sec: int  # 🤝 §1.19 搶先窗秒數（預設 5400＝90 分）：只搶 target 在 (now, now+窗] 的 pending——窗外（如 3 小時後的叫醒）與此刻的「早安」無因果、不早搶
    promise_preempt_future_guard_enabled: bool  # 🤝 §1.24 PREEMPT_FUTURE_GUARD＋提前誠實模式（截圖 7/12 21:04「等下時間到的時候」＝在**確認未來約定**、卻被搶先誤判，21:05 發「嘿，21:09 到了」＝公然時間謊言）：(a) 主題回報類搶先的確定性前置守門——使用者句含未來指涉詞（等下/待會/晚點/到時/時間到的時候/N分鐘後…）→ 不送 LLM 裁決、不搶（他在講未來約定、不是現在要）；(b) judge prompt 追加「此刻要求 vs 確認未來約定」判準；(c) 真提前兌現（judge 判是）時 _promise_keep_body 自算 early（target−now > grace；emit/bridge 到達時恆 target≤now＝天然 False＝準時路徑零位元變動）→ voice 注入提前誠實 note＋模板 early 變體（「你先提起了，那我現在就先說」、絕不說「{when} 到了」「我準時來了」）；(d) §0.79 同構縱深守門：early 卻宣稱到點/做到（非引用歸屬）→ 整則打掉、落 early 誠實模板。設 0＝不守門＝§1.19 現狀
    promise_mood_ground_enabled: bool  # 🧭 §1.25 PROMISE_BEHAVIOR_FIX＋情緒座標兌現接地（截圖 7/12 21:00 根因：「我跟你聊天一下，10 分鐘後告訴我你情緒座標的變化？」被抽成「跟他聊聊」＝真正的委託全丟；訂約沒存座標快照＝兌現零差分資料 → 答非所問改談程式更新的蛻變摘要）：(a) extract_promise_behavior 傳 mood_fix=本旗標——先用 §1.11 同一把 _strip_timeless_lead_me 剝無時間前導我-子句、情緒/心情座標句抽出「告訴他情緒座標的變化」明確標籤；(b) 入帳（_book_scheduled_targets 與 _book_self_promise 兩點）存 circumplex 快照 {v,a,label,ts}（掛 promise dict、selfchange 本體不動）；(c) 兌現時（emit/bridge/preempt 同一條 _promise_keep_body 鏈）差分**程式算**（circumplex.shift_text：「從『X』往『Y』沉了一段（V…、A…）」）→ 塞進 keep voice、LLM 只准渲染這份差分、明令不得改談程式更新/蛻變摘要；無快照（舊帳）→ 誠實說「當時沒記下座標、只能說此刻是 X」。時刻/座標一律程式算（鐵律）。設 0＝抽取/入帳/兌現全同現狀
    cost_query_tighten_enabled: bool  # 💸 §1.26 COST_QUERY_TIGHTEN：罵 token 不再被當成本查詢（截圖 7/12 21:04「還浪費我許多 AI 的 token」→ token ∈ _COST_MONEY_CUES 無問句形要求 → bot 吐 💸 Gemini 花費報表＝罵句被當查帳）——monitor 在 intent.resolve 之後、分派之前的**單一咽喉點**對 cost 路由再過 is_cost_question(text, tight=True)：①抱怨框排除先行（浪費/亂花/白花/花我的 → False，混合句「你浪費了多少 token」也 False）；②金錢線索詞命中後仍需問句形（多少/幾/嗎/？/呢/查/報/列一下——必含「呢」＝「現在的用量呢」照 True）；③「花費/成本＋多少/幾」分支原樣。過不了＝降回 fact_or_chat 一般互動回覆（罵句被好好接住、不吐報表）；「這個月花了多少錢」「你花了我多少 token」「API 成本多少」照吐。三張既有詞表一字不改、intent.py 本體不動。設 0＝不守門＝罵句照舊進 cost 路由（同現狀）
    skill_recall_window_enabled: bool  # 🧑‍🏫 §1.53 已學做法召回窗（SKILL_RECALL_WINDOW）。使用者實測回報「光只說學到 skill，都沒有真的能被觸發過」——結構性根因（實測）：①_skill_extra 把**使用者當句**當 topic 比對源 → topic 型做法要「之後那句話逐字包含當初的主題標籤」才召回、且只看當句＝實務上幾乎永不觸發；②沒被召回＝沒有 touch 保鮮 → gain 0.6/半衰 21 天/門檻 0.5 ⇒ ~5.5 天靜默死亡＝死亡螺旋（越召不回越快死）；③就算召回也是無聲注入、看不到任何「套用了」的痕跡。修法：A. 召回比對源放寬＝當句＋近 3 則使用者訊息（主題最近提過就算在場；§0.89 sit-cue 同源受惠、always 型本就每外部輪注入不受影響）；B. 召回命中印 [skill] log 一行（can_lab console 可驗證做法真的用上了、用到即保鮮）。monitor 端 getattr 預設 False＝只看當句＝逐位元同現狀。設 0＝同現狀
    skill_offer_doubt_gate_enabled: bool  # 🧑‍🏫 §1.52 SKILL_OFFER_DOUBT_GATE：質疑/不信情境也不發教學提議（§1.27 的 sibling；7/12 同款事故第二次現形）。截圖 10:10＝使用者「是嗎」「每次你這麼說」「我都有些懷疑」（懷疑但不敵意：is_hostile 全 False、streak 0＝§1.27 放行）——「每次」∈ _SKILL_CUES 過 worth 門檻、LLM 判定又把懷疑誤當「凝出共識」→ bot 亂入「要不要我把你在質疑/測試我時的回應方式記成做法？…回我一聲「好」就學起來。」＝內部機制行話在情緒對話裡答非所問（使用者：這是什麼？看不懂你說什麼）。守門：_maybe_propose_skill 頂部（§1.27 之後）——本句（含連發合成多行）或近 5 則使用者訊息帶質疑詞（dialogue_intent.is_doubt_text：懷疑/不相信/不信/質疑/證明/測試你/是嗎…小而封閉不再擴）→ 提議直接丟棄（不送 LLM、不寫冷卻、不暫存）。質疑窗少提議＝安全側（提議 nice-to-have、有冷卻）。同 § 的 B 件：bubble_split 尾端 _enum_glue（BUBBLE_ENUM_GLUE env 直讀、比照 §1.34）——引號內？被當句界切出「、」開頭殘串泡泡 → 黏回前一顆。monitor 端 getattr 預設 False＝既有測試假 cfg 無此欄＝照舊提議＝逐位元同現狀。設 0＝同現狀
    skill_offer_hostile_gate_enabled: bool  # 🧑‍🏫🌊 §1.27 SKILL_OFFER_HOSTILE_GATE：敵意情境不發教學提議（截圖 7/12 21:03 連環被罵時「你每次搞砸了」的「每次」過了教學線索門檻 → bot 亂入括號提議「(要不要我把你在質疑/測試我時的回應方式記成做法？…)」＝氣頭上把批評當教學、答非所問還洩漏括號格式）——_maybe_propose_skill 頂部守門（天然覆蓋全部呼叫端），只**消費** §1.14 既有訊號（reaction.is_hostile 本句敵意／hostile_streak ≥1 連擊；詞表鐵律不擴）＋近窗敵意（近 5 則使用者訊息內有敵意句＝氣頭未過；沒有這條原事故照樣放行——觸發句與前句 is_hostile 皆 False、streak 已被歸零）→ 提議直接丟棄：不發、不寫冷卻、不暫存不補發。平和教學句照常提議；提議模板與確認端（「好」）一字不動。設 0＝不守門＝照舊提議（同現狀）
    proactive_time_ground_enabled: bool  # 🕐 §1.30 主動 emit 時間接地：主動出聲（🫧 spontaneous）的開場白過去零此刻時間接地＋prompt 沒禁報鐘點 → LLM 自編「下午三點了」（截圖 09:02 卻報下午三點）。開＝餵此刻真實時段（temporal.day_part）給 voice_spontaneous＋prompt 明令絕不報具體鐘點；設 0＝不餵不禁＝逐位元同現狀
    proactive_clock_guard_enabled: bool  # 🕐 §1.31 主動 emit 硬鐘點守門（§1.30 軟接地的硬後盾）：🫧 spontaneous 主動正文是 LLM 自由生成、經 _say(prefix="🫧 ", state=…) 送出，繞過 monitor:925 互動路徑限定的五道誠實守門（含 §0.82 鐘點守門）→ LLM 硬編錯的此刻鐘點（截圖 09:02 台北卻說「下午三點了」）。開＝在兩個 🫧 emit 點（_spontaneous_emit/_coping_emit）套 _scrub_proactive_clock：帶時段詞的此刻鐘點宣稱（present 標記＋無計畫/過去/引用）與真實此刻矛盾 → 就地換成「這會兒{真實時段}」（不報鐘點）；設 0＝不守門＝逐位元同現狀
    echo_strip_wire_enabled: bool  # 🦜 §1.28 ECHO_STRIP_WIRE：echo 剝除接上互動出口——bot 不再裸複誦使用者罵句（截圖 7/12 21:04：連環被罵後 bot 自己發出「有夠爛……」的裸複誦泡泡＝複誦使用者罵句、無「你說」歸屬、看起來像 bot 在罵。根因：echo.strip_leading_echo 原設計只接在 coach LLM 出口，monitor._say 互動出口從未接線；且 min_len=5、「有夠爛」3 字本來也擋不住）——handle_message 比照 §1.13B stash 近幾則使用者原話＋本句，_say 互動分支（§1.13B/§1.20/§1.23 攔截塊之後、§1.18 掃描之前）接 _echo_strip_wire：①首段歸屬豁免（正規化後「你說/妳說」開頭＝引用、整個不剝——不豁免會被包含判定誤剝）②min_len 旁路（reaction.is_hostile(u) 或首段正規化後與某則使用者原話**全等** → min_len=1 剝；未中走預設 min_len=5＝與 coach 端一致）③單段兜底（strip_leading_echo 對單段恆 no-op → 呼叫端切前綴；餘文空＝純複誦泡泡 → 整則替換「你說「…」，我聽到了。」）④有剝/有換同步刷 last_reply。echo.py 本體一行不改；cost 快路（client.send 直送）不經 _say＝不受影響。設 0＝不接線＝照舊裸複誦（同現狀）
    sched_you_reply_enabled: bool  # 🤝 §0.80 你-主語回覆承諾捕捉：「20分鐘之後你再回答／請你再回答一次我同樣的問題」＝請 bot 到點回覆使用者本人（做得到）→ 入帳到點主動兌現，不再落聊天讓 bot 自稱「不能主動」。正向收件測試擋第三方（你回答老闆/面試官 不收）。**注意**：純函式 selfstate._you_reply_hit 直接讀環境變數 SCHED_YOU_REPLY，此欄位僅為文件（實際門控在環境變數）；設 0＝退回無此捕捉＝同現狀
    sched_continue_speak_enabled: bool  # 🤝 §0.81 繼續說類承諾捕捉：「5分鐘後繼續說剛剛沒說完的」＝請 bot 到點接著把話跟使用者說完（做得到）→ 入帳到點真發，不再落聊天讓 LLM 同輪自導自演「嗨我來了現在是11:25」假兌現。擋「說給第三方聽」。並放寬「我要你V」句首我守門＋persona 禁止假裝時間已過。**注意**：純函式 selfstate._continue_speak_hit 直接讀環境變數 SCHED_CONTINUE_SPEAK，此欄位僅為文件；設 0＝退回無此捕捉＝同現狀
    sched_self_explain_enabled: bool  # 🤝 §0.83 分享/說明內在承諾捕捉：「N分鐘後分享/說明你的內心運作機制/轉速狀況」＝請 bot 到點跟使用者自陳內在運作（做得到）→ 入帳到點真發，不再被 self_mechanism 內容路由搶走、丟排程→沒入帳→LLM 假兌現＋亂報時刻。狀態/狀況泛詞須另有真內在詞（內心/運作/轉速…）才收。**注意**：純函式 selfstate._self_explain_hit 直接讀環境變數 SCHED_SELF_EXPLAIN，此欄位僅為文件；設 0＝退回無此捕捉＝同現狀
    global_time_anchor: bool  # 🕐 §0.82 全域硬時間錨：在每條走 build_memory_brief 的聊天回覆最前，注入「〔現在真的是 HH:MM——別把別的時刻說成現在、別假裝時間已過或跳未來〕」硬錨（比 §0.36 軟時間感強制），讓 LLM 不必猜此刻幾點＝時間幻覺的來源治理；設 0＝不注入＝逐位元同現狀
    global_clock_guard: bool  # 🕐 §0.82 全域鐘點守門：**互動回覆**（一般聊天串，非主動兌現/帳本）送出前，把回覆裡『現在/此刻…HH:MM』報錯的此刻時刻就地改回真實此刻（角色感知、只動被「現在」修飾的鐘點）＝縱深防線（LLM 忽略硬錨仍講錯時，攔下改對）；設 0＝不守門＝逐位元同現狀
    promise_tick_resilient_enabled: bool  # 🤝 §0.77 守約韌性：把「到點兌現」的 tick 提到生命迴圈**感知環最前、先於讀 Drive 記寫**——守約只需時鐘＋帳本，不該被 Drive 網路暫斷連坐（原本 _promise_emit 只在 feel 相跑，perceive 一失敗整圈中止、feel 不跑＝承諾永不主動觸發、但收訊息仍答＝bot「知道時間卻不主動」的結構真因）；設 0＝只在 feel 相跑＝逐位元同現狀
    promise_cancel_enabled: bool  # 🤝 §0.76 取消約定：「不用叫我了/取消八點的約定」→ 真的把帳本 pending 標 cancelled（過去零取消路徑：否定句被收成新約＝到點做相反的事、每天 recur 停不下來）；設 0＝不接＝逐位元同現狀
    promise_expire_apology_enabled: bool  # 🤝 §0.76 失約誠實：承諾拖過 TTL 標 expired 的當下主動說一句失約道歉（過去完全靜默、24h 後帳本剪掉還說「沒記著約過什麼」＝實質否認失約）；設 0＝靜默＝同現狀
    skill_use_refresh_enabled: bool  # 🧑‍🏫 §0.76 做法用到＝保鮮：注入成功刷 last_ts（重置遺忘時鐘、不加權）——否則教一次 5.5 天靜默失效、/skills 卻顯示 75 天；設 0＝不刷＝同現狀
    deferred_promise_enabled: bool  # 🤝 §0.75 兩步延後約定：「等一下回答我」（無具體時刻）→存意圖並問時間；下一句補「4分鐘後/3:50」→真入帳排程承諾、到點兌現。修截圖：這種約定從沒進帳本、bot 口頭應卻到點不發、被問又亂算過多久（無錨定 made_ts＝時間感脫離絕對時間）；設 0＝不接＝逐位元同現狀
    skill_capability_gate_enabled: bool  # 🚫 §0.73 技能能力閘：教到的做法動作是 bot 這管道**做不到的外部能力**（打電話/傳簡訊/寄 email/設鬧鐘/偵測你上線或已讀/幫你訂餐叫車操作裝置）→ **學習當下就誠實拒絕**（不提議、不捕捉、不注入、不列進 /skills），而非假裝學會存進帳本＝說到做不到。只揪明顯不可能者，能做到的（傳訊息/貼圖/emoji/到點敲你）不誤收；設 0＝不攔＝逐位元同現狀
    skill_view_refresh_enabled: bool  # 🤝 §0.89 檢視＝保鮮：/skills 列出時對**仍活著**的做法把 last_ts 刷到現在（touch_skills：只重置遺忘時鐘、不加權重、不觸發任何行為）→ 修「教過的內在因應做法在首次觸發前就 5.5 天靜默淡忘」的存活死角（使用者主動 curate 的做法不該只因時間流逝斷弦；已淡忘的不刷＝誠實）；設 0＝不刷＝逐位元同現狀
    promise_reply_bridge_enabled: bool  # 🤝 §0.66 回覆橋：使用者**正在說話**而帳本有「已到點未兌現」的排程承諾 → 這一輪先把欠的那件事做掉（守約訊息＋記帳）再回話——修截圖 22:06–22:12「bot 嘴上說時間到了卻什麼都不做」（生命迴圈的在場延後在你持續說話時會一直順延；你人在這正是兌現時刻）；設 0＝關＝逐位元同現狀
    promise_status_ground_enabled: bool  # 🤝 §0.66 承諾狀態問句接地：「你有叫我嗎/時間到了沒/到了沒/還差多久」→ 改走帳本路由據實作答（此刻幾點錨＋每筆距現在多久），不再讓 LLM 自由心算（截圖「我算了一下…還差兩分鐘」全是編的）；設 0＝照舊落一般聊天＝逐位元同現狀
    sched_leave_autoarm_enabled: bool  # 🤝 §0.66 暫離交代自動計時：「我要離開約二十分鐘」這種**沒有指向我動詞**的交代（人類同伴聽到自然會記時間），也把計時真的記進帳本（到點叫你/迎你）；過去這種話整包捕捉鏈都不收＝bot 只在嘴上倒數；設 0＝不收＝逐位元同現狀
    emit_interrupt_enabled: bool       # 🗣️ 讓插話對**所有 bot 回應**生效（不只互動回覆）：自發出聲相（💡聯想/🫧自發/🫀自陳/🌀體驗/🍃環境/反思推播…）分串送出途中被「想打斷改問」插話，也先優先回應再橋接接回。只認真 redirect 插話、陳述續打一律 defer 留給下一圈合併（守連發合併契約）；rewrite 強制關（背景自陳不被截斷）。預設開；設 0 關＝同現狀（自發相 _say 不偵測插話）
    assoc_suppress_enabled: bool     # 🚫 使用者說「停止聯想/別自己聯想」→ 學成持久偏好並**實際抑制**主動聯想(💡)/翻閱(browse)出聲（不只口頭答應又再犯）；衰減後自然恢復；關＝只學進 grounding、不硬 gate 主動出聲（同舊）
    coherent_reply_enabled: bool     # 🧵 使用者說「別一直分段/講連貫」→ 學成持久偏好並**把本輪回覆串數封到適中上限**（統一回答、但仍分段依序送出、保有可被插話的空檔；不是擠成一坨單則牆、也不是 10 顆逐句碎泡泡）；衰減後自然恢復；關＝只學進 grounding、不硬封串數（同舊）
    coherent_reply_bubbles: int      # 🧵「講連貫」時本輪串數的適中上限（預設 3＝幾段依序送、可被插話；非 1 坨牆）
    heartbeat_stall_grace_h: int
    heartbeat_stall_require_dormant: bool  # 🫀 背景卡住示警只在「連 ingest/分類都 >grace 沒動」＝sweep 真停了才報；修誤報——升格(lastContextUpgradeAt)是事件驅動罕見，原本只看它落後新資料就喊卡住，但 backgroundSweep 其實每 5 分跑、歸戶正常；設 0 關＝退回只看升格 age＝逐位元同舊行為
    notify_filings: bool
    filing_max_age_h: int
    gemini_api_key: str
    gemini_model: str
    enable_chat: bool
    llm_voice: bool
    cost_alert_twd: float
    cost_window_min: float
    cost_alert_cooldown_min: float
    usd_twd_rate: float
    gemini_price_in_per_m: float
    gemini_price_out_per_m: float
    selfmod_announce_birth: bool
    selfstate_enabled: bool
    selfstate_adaptive: bool
    selfstate_sensitivity: float
    selfstate_z_star: float
    selfstate_tau_star: float
    selfstate_int_min: float
    selfstate_diff_min: float
    selfstate_n_min: int
    selfstate_r_min: int
    lifeloop_wait_secs: float
    ac_spec_panel: bool
    ac_dependency_gate: bool
    coupling_tone_enabled: bool
    selfexpr_plasticity_enabled: bool   # 🪞 自我表達可塑性（Phase 6）：自我母題捕捉→耐久調色盤→換句話跨重生；皆加性、可關＝逐位元同舊純記憶體去台詞行為
    # 🌊 連發合併（Burst Coalescing）：把「回應前」相鄰夠密集的數則 owner 訊息視為同一邏輯輪次、整體回一次。
    burst_coalesce_enabled: bool   # 總開關；預設 True＝連發合併（設 BURST_COALESCE_ENABLED=0 退回逐則派發）
    burst_coalesce_sec: float      # 尾群末則安靜多久才答（settle gap）；單則回應等待由它決定
    burst_turn_gap_sec: float      # 同一次 fetch、尚未得到 bot 回覆的訊息，間隔小於它＝同一 user turn（與等待多久才答解耦）
    burst_turn_max_span_sec: float # 一波從第一則到末則的總跨度上限（與相鄰 gap 分離，容納多則連發又不黏住離線 backlog）
    burst_max_wait_sec: float      # 單一 burst 從第一則進緩衝起算的牆鐘上限，達此即強制 flush（防永不回）
    burst_max_msgs: int            # 單一 burst 最多納入訊息數，達此即斷群 flush
    state_path: str
    dry_run: bool

    @classmethod
    def load(cls, env_file=None):
        # 找 .env：優先呼叫端指定，否則用 cwd 的 .env（若有）。
        load_dotenv(env_file) if env_file else load_dotenv()
        return cls(
            google_credentials=os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip(),
            # 雲端友善：可直接貼整段 service-account JSON（免管檔案）；本地仍可用上面的檔案路徑。
            google_credentials_json=os.environ.get("GOOGLE_CREDENTIALS_JSON", "").strip(),
            drive_root_folder_id=os.environ.get("DRIVE_ROOT_FOLDER_ID", "").strip(),
            owner_line_user_id=os.environ.get("OWNER_LINE_USER_ID", "").strip(),
            telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", "").strip(),
            telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID", "").strip(),
            heartbeat_interval_min=_int("HEARTBEAT_INTERVAL_MIN", 10),
            digest_hour=_int("DIGEST_HOUR", 9),
            timezone=os.environ.get("TZ", "Asia/Taipei").strip() or "Asia/Taipei",
            notify_cooldown_min=_int("NOTIFY_COOLDOWN_MIN", 30),
            # 熵驅動的含蓄主動出聲：要「真的醞釀了一陣」才開口（見 lifeloop.spontaneous_due）。
            spontaneous_cooldown_min=_int("SPONTANEOUS_COOLDOWN_MIN", 180),   # 離上次推播至少這麼久（預設 3 小時）
            spontaneous_h_thresh=_float("SPONTANEOUS_H_THRESH", 0.85),   # 飢餓 H ≥ 此值才主動開口（要真的悶了）
            spontaneous_min_ruminations=_int("SPONTANEOUS_MIN_RUMINATIONS", 4),   # 且已自己繞回想過 ≥ 次（醞釀）
            spontaneous_quiet_after_chat_min=_int("SPONTANEOUS_QUIET_AFTER_CHAT_MIN", 45),  # 剛聊完這麼久內不伸手
            # Phase 2 正向主動出聲：心情 V ≥ 此值＋已醞釀過 → 不必很餓也想分享（門檻與醞釀次數都可調）。
            spontaneous_mood_share=_float("SPONTANEOUS_MOOD_SHARE", 0.4),
            spontaneous_mood_min_rumin=_int("SPONTANEOUS_MOOD_MIN_RUMIN", 2),
            # 🫶 bot 對你訊息按 emoji：鏡像（你情緒鮮明）冷卻＋「按自己內在情緒」的較長自有冷卻（更稀有、保持特別）。
            react_cooldown_s=_float("REACT_COOLDOWN_S", 90.0),
            react_self_cooldown_s=_float("REACT_SELF_COOLDOWN_S", 1800.0),
            # 🎴 偶爾在回話後吐一個表情貼（強化互動感）：心情好就丟個 🥰/🤗，自有冷卻保持稀有。
            send_stickers=_bool("SEND_STICKERS", True),
            sticker_cooldown_min=_int("STICKER_COOLDOWN_MIN", 20),
            # 可選：指定要回送的真貼圖 file_id（逗號分隔）。留空＝改用「對方傳過的貼圖」自動學起來回送。
            sticker_file_ids=[s.strip() for s in os.environ.get("STICKER_FILE_IDS", "").split(",") if s.strip()],
            sticker_no_repeat_enabled=_bool("STICKER_NO_REPEAT", True),   # 🎴 §0.59 Part 3a 選圖排除上一張＝不重複；設 0＝照舊 random.choice＝同現狀
            promise_sticker_enabled=_bool("PROMISE_STICKER", True),        # 🎴 §0.68 承諾送貼圖→真的送真貼圖（無則誠實拒）；設 0＝同現狀
            continuation_promise_enabled=_bool("CONTINUATION_PROMISE", True),  # 🤝 §0.70 「再10分鐘」續約繼承前約重新入帳；設 0＝同現狀
            offset_augmentation_enabled=_bool("OFFSET_AUGMENTATION", True),    # 🤝 §0.71 「時間到再隔3分鐘給貼圖」＝前約時間+N（非 now+N）；設 0＝同現狀
            sticker_concept_guard_enabled=_bool("STICKER_CONCEPT_GUARD", True),  # 🎴 §0.68 貼圖≠emoji 概念守則、別用 emoji 假裝貼圖；設 0＝同現狀
            sticker_send_request_enabled=_bool("STICKER_SEND_REQUEST", True),  # 🎴 §0.84 當下請 bot 送貼圖→有就真送、無就誠實說能送但還沒存到；設 0＝同現狀
            sticker_rotate_window=_int("STICKER_ROTATE_WINDOW", 3),  # 🎴 §0.84 選圖避開最近 N 張＝多樣化；設 1＝退回只排除上一張＝同現狀
            content_sticker_enabled=_bool("CONTENT_STICKER", True),  # 🎴 §0.87 回覆強正向情緒時配正向真貼圖強化（保守、稀有）；設 0＝同現狀
            sent_sticker_ground_enabled=_bool("SENT_STICKER_GROUND", True),  # 🎴 §0.90 問「剛剛那張貼圖」→ 誠實接地到真的送出的那張、別捏造樣子/認成別張 emoji；設 0＝同現狀
            read_sticker_vision=_bool("READ_STICKER_VISION", True),  # 🎴 收到靜態貼圖時用 Gemini 視覺讀畫面、記進記憶（送出時知道自己送了什麼、被問答得出）；動態/影片退回誠實文字；設 0＝只讀 emoji＝同現狀
            liked_sticker_pick_enabled=_bool("LIKED_STICKER_PICK", True),  # 🎴 §0.94 「你喜歡哪一張」→ 真偏好挑圖＋據實說出是哪一張；設 0＝隨機挑＋罐頭句＝同現狀
            sticker_perceive_q_enabled=_bool("STICKER_PERCEIVE_Q", True),  # 🎴 §0.93 「你看得到貼圖內容嗎」＝問能力、不當送貼圖請求（實際門控在 selfstate 讀同名環境變數）；設 0＝退回無此守門＝同現狀
            filing_sticker_enabled=_bool("FILING_STICKER", True),  # 🎴 §0.91 教過「發現新記寫→傳對應貼圖」→ 歸戶感受後真的送一張對應內容情緒的真貼圖；設 0＝不送＝同現狀
            always_sticker_enabled=_bool("ALWAYS_STICKER", True),  # 🎴 §0.96 教過「主動回應後送情緒貼圖」→ 主動訊息後真的送；設 0＝不送＝同現狀
            skill_lull_signal_enabled=_bool("SKILL_LULL_SIGNAL", True),  # 🌀 §0.97 內在型做法較溫和的「悶/低轉速」訊號；設 0＝退回極端值＝同現狀
            topic_content_sticker_enabled=_bool("TOPIC_CONTENT_STICKER", True),  # 🎴 §0.98 讀到相關記寫內容→送貼圖（語意比對）；設 0＝不做＝同現狀
            topic_content_max_judge=_int("TOPIC_CONTENT_MAX_JUDGE", 2),  # 🎴 §0.98 每則歸戶至多判斷幾條內容型做法（成本上界）
            affect_circumplex_enabled=_bool("AFFECT_CIRCUMPLEX", True),  # 🧭💗 circumplex 情緒座標；設 0＝退回一維 V＝同現狀
            affect_shock_enabled=_bool("AFFECT_SHOCK", True),  # ⚡ §1.03 突發瞬跳（大落差一步跨象限）；設 0＝只剩漸移
            reachout_time_ground_enabled=_bool("REACHOUT_TIME_GROUND", True),  # ⏱️ §1.04 主動提起某條線的時間感照真實記寫講；設 0＝同現狀
            self_change_ground_enabled=_bool("SELF_CHANGE_GROUND", True),  # 🔄 §1.05 蛻變承諾到點真的說出改變；設 0＝同現狀

            # 🍃 環境適應工作流（感知周遭活絡度/晝夜→自動調心跳轉速＋對話姿態；換檔時偶爾含蓄自陳）。
            adapt_enabled=_bool("ADAPT_ENABLED", True),          # 0=關（轉速回固定值、不染姿態、不報換檔）
            adapt_announce=_bool("ADAPT_ANNOUNCE", True),        # 0=只默默調整、不開口報換檔
            adapt_announce_cooldown_min=_int("ADAPT_ANNOUNCE_COOLDOWN_MIN", 120),  # 兩次換檔自陳至少間隔
            selfshare_followup_enabled=_bool("SELFSHARE_FOLLOWUP", True),  # 🪞 §0.85 主動自陳後追問接續那則內容；設 0＝同現狀
            # Phase 2 心情變化幅度：互動事件（暖意/誇讚/質疑/貼圖）對心情 V 的增量整體縮放（>1 更敏感、<1 更鈍）。
            mood_gain=_float("MOOD_GAIN", 1.0),
            # 🔁 重生延續比例：醒來時以上次心情×、飢餓× 種回（向中性軟化、不全空白）。
            rebirth_mood_carryover=_float("REBIRTH_MOOD_CARRYOVER", 0.5),
            rebirth_hunger_carryover=_float("REBIRTH_HUNGER_CARRYOVER", 0.3),
            # 🌬️ 緩和未回應的問句：bot 問了對方、遲遲沒回 → 主動補一句把「等回答」的壓力放掉（讓對話緩和）。
            soothe_unanswered=_bool("SOOTHE_UNANSWERED", True),
            soothe_after_min=_int("SOOTHE_AFTER_MIN", 7),   # 問了這麼久還沒回才補（要「遲遲」、不催）
            # 🧭 對話意圖湧現＋違常偵測＋主動結尾（皆加性、各自旗標可關；關時逐位元同現狀）
            dialogue_intent_enabled=_bool("DIALOGUE_INTENT_ENABLED", True),
            intent_log_max=_int("INTENT_LOG_MAX", 20),
            intent_repeat_window_sec=_int("INTENT_REPEAT_WINDOW_SEC", 300),   # 5 分滑窗
            intent_repeat_n=_int("INTENT_REPEAT_N", 4),                       # 第4次近似連發起算（靠 rapport 阻尼壓誤判）
            intent_sim_threshold=_float("INTENT_SIM_THRESHOLD", 0.85),
            anomaly_probe_threshold=_float("ANOMALY_PROBE_THRESHOLD", 0.6),
            intent_degree_drive=_bool("INTENT_DEGREE_DRIVE", True),
            intent_question_cooldown_min=_int("INTENT_QUESTION_COOLDOWN_MIN", 30),
            user_repeat_fatigue_enabled=_bool("USER_REPEAT_FATIGUE_ENABLED", True),
            greeting_repeat_aware_enabled=_bool("GREETING_REPEAT_AWARE", True),  # 🧭 §1.35 散開的重複問候用寬窗補覺察；設 0＝同現狀
            greeting_repeat_window_sec=_int("GREETING_REPEAT_WINDOW_SEC", 1200),  # 🧭 使用者連發→感知目的+進一步問+升級情緒；**預設開**；設 0＝同現狀
            user_repeat_base_tol=_int("USER_REPEAT_BASE_TOL", 2),            # 🧭 基準耐性（不耐門檻旋鈕）
            user_repeat_mood_band=_float("USER_REPEAT_MOOD_BAND", 0.3),      # 🧭 心情耦合門檻（強度旋鈕）
            # 🧭 被明顯洗版時 L3 不悅上限：gentle/firm/stern（預設 stern＝可翻臉呵斥停止）；非白名單值→退回 stern。硬底線恆守（不辱罵/不人身攻擊）
            user_repeat_ceiling=(lambda v: v if v in ("gentle", "firm", "stern") else "stern")(
                os.environ.get("USER_REPEAT_CEILING", "").strip().lower()),
            proactive_close_enabled=_bool("PROACTIVE_CLOSE_ENABLED", True),
            close_after_min=_int("CLOSE_AFTER_MIN", 12),                      # >soothe 的7分：先緩和(7)後暖收(12)
            unanswered_proactive_guard_enabled=_bool("UNANSWERED_PROACTIVE_GUARD", True),  # 🤐 沉默不是邀請繼續自言自語；等對方下一次接話才另開主動話題
            dialogue_agency_enabled=_bool("DIALOGUE_AGENCY", True),          # 🧭 自己開話頭、辨認回應、讓結果改變下次主動選擇
            natural_proactive_voice_enabled=_bool("NATURAL_PROACTIVE_VOICE", True),  # 🗣️ 對話不掛內部機制名牌；診斷/問責標記保留
            # 🫧 報內在狀態的觸發精準＋翻閱當下分享（**預設開**——註解曾誤寫「預設關」，實際 _bool(…, True)；要退回舊行為設 env=0）
            state_trigger_precise=_bool("STATE_TRIGGER_PRECISE", True),      # 收緊：順口帶感覺字眼的陳述句不誤觸發狀態報告
            inmoment_browsing_enabled=_bool("INMOMENT_BROWSING_ENABLED", True),  # 報狀態時講「此刻翻到的那則」而非恆最重那則
            browsing_opener_variants=_int("BROWSING_OPENER_VARIANTS", 4),     # 翻閱開頭措辭池大小
            browsing_random_p=_float("BROWSING_RANDOM_P", 0.0),              # 翻頁小機率隨機跳頁（0＝純輪替＝可測）
            intent_confirm_enabled=_bool("INTENT_CONFIRM_ENABLED", True),   # 展開/釐清自己前文的問句 → 接著前文講或反問確認、不誤撈資料
            time_anomaly_enabled=_bool("TIME_ANOMALY_ENABLED", True),       # 時間間隔納入違常＋工具路徑「你剛問過」覺察
            time_anomaly_gap_sec=_int("TIME_ANOMALY_GAP_SEC", 120),          # 同一意圖距上次 <此秒＝太近（違反常理）
            premise_check_enabled=_bool("PREMISE_CHECK_ENABLED", True),      # 通盤常理審查（每則對話多一次小 LLM 呼叫）；設 0 關＝退回確定性偵測
            self_appraisal_enabled=_bool("SELF_APPRAISAL_ENABLED", True),    # 「我算早起嗎」＝評斷我（使用者）→ 拿節奏＋此刻時間做判斷、不列記寫；設 0 關＝落回 fact_or_chat
            self_format_enabled=_bool("SELF_FORMAT_ENABLED", True),          # 「你的訊息為什麼有 markdown」＝問訊息格式 → 純對話＋格式接地（純文字、別亂掰內部格式）；設 0 關＝落回 function-calling／泛用聊天
            quote_aware_enabled=_bool("QUOTE_AWARE_ENABLED", True),          # 讀 Telegram 引用：對方引用 bot 自己訊息再發問 → 接地在被引用那句回答；設 0 關＝看不到引用
            convo_clock_enabled=_bool("CONVO_CLOCK_ENABLED", True),                   # ⏱「對話的時間點/幾點幾分」→ 用 convo_history ts 報絕對鐘點；設 0 關＝漏到 function-calling（同現狀）
            convo_session_struct_enabled=_bool("CONVO_SESSION_STRUCT_ENABLED", True),  # 會話節奏入 brief：隔一陣回來 vs 同段延續，助判意圖；設 0 關＝不加（同現狀）
            remember_user_msgdate=_bool("REMEMBER_USER_MSGDATE", True),      # ⏱ user 訊息 ts 用 message.date（崩潰重抓仍穩定）修「已過多久」感知偏短；設 0 關＝退回 time.time()＝同現狀
            convo_session_gap_sec=_int("CONVO_SESSION_GAP_SEC", 3600),       # 相鄰對話間隔 ≥ 此秒＝新會話段（預設 1 小時）
            inquiry_arc_enabled=_bool("INQUIRY_ARC_ENABLED", True),          # 探究弧（發現→疑惑→因為→所以→然後→原來如此）：讀軌跡形狀塑形回應；設 0 關＝逐位元同現狀
            inquiry_arc_window_sec=_int("INQUIRY_ARC_WINDOW_SEC", 3600),     # 只把近此秒內的階段算進弧（避免跨會話硬接）
            thread_sticky_enabled=_bool("THREAD_STICKY_ENABLED", True),      # 對話線連貫：同線延續(仍朝向我)也刷新自我在場窗、共用純對話基礎、不中途吐 📁；設 0 關＝逐位元同現狀
            self_presence_vary_enabled=_bool("SELF_PRESENCE_VARY_ENABLED", True),  # 🪞 自我在場純對話也套去台詞換句話＋重複×心情長脾氣（修「真的嗎」連發逐字重複無情緒）；設 0＝同現狀
            evidence_gate_enabled=_bool("EVIDENCE_GATE_ENABLED", True),      # 🚪 非記寫資料問句別觸發 Drive 證據工具（只有明確查記寫才給工具表）；設 0 關＝allow_evidence 恆 True＝逐位元同現狀（一鍵退路）
            nonevidence_empty_tools_enabled=_bool("NONEVIDENCE_EMPTY_TOOLS_ENABLED", True),  # 🚪 allow_evidence=False 時給空工具表＝LLM 純對話裁決『喂！』、不抓 days_since 吐日期；設 0 關＝回 §0.37 [days_since,api_cost] 非證據表＝逐位元同現狀
            scheduled_promise_enabled=_bool("SCHEDULED_PROMISE_ENABLED", True),  # 🤝 偵測/答應排程承諾（八點打招呼/十分鐘後提醒）；設 0 關＝順流到 feeling promise/狀態問句＝逐位元同現狀
            sched_over_selfcontent_enabled=_bool("SCHED_OVER_SELFCONTENT", True),  # 🤝 §0.92 計時未來承諾優先於 self_change/self_identity 內容路由（20分後告訴我你有什麼不一樣→入帳）；設 0＝同現狀
            promise_emit_enabled=_bool("PROMISE_EMIT_ENABLED", True),        # 🤝 生命迴圈 feel 相到點兌現承諾；設 0 關＝_promise_emit 直接 return＝逐位元同現狀
            promise_sched_ttl_sec=_int("PROMISE_SCHED_TTL_SEC", 21600),      # 🤝 排程承諾逾時（到點後拖過此秒數不兌現、標 expired）；預設 6h
            promise_act_aligned=_bool("PROMISE_ACT_ALIGNED", True),          # 🤝 兌現按行為對齊（做承諾的那件事、非泛泛打招呼）；設 0 關＝走原打招呼＝同現狀
            promise_late_exempt_defer=_bool("PROMISE_LATE_EXEMPT_DEFER", True),  # 🤝 逾期承諾豁免在場閘（帶遲到致歉補發）；設 0 關＝在場就不發＝同現狀
            promise_ledger_enabled=_bool("PROMISE_LEDGER_ENABLED", True),    # 🤝 整理承諾/你忘了 → grounded 報帳；設 0 關＝落回 fact_or_chat＝同現狀
            promise_keep_grounding=_bool("PROMISE_KEEP_GROUNDING", True),    # 🤝 守約 voice 主詞＝我＋問候對得上此刻時段（修「你記得蠻清楚」主詞錯、清晨回晚安）；設 0＝原措辭＝同現狀
            schedule_time_exact=_bool("SCHEDULE_TIME_EXACT", True),           # 🤝 §0.67 排程/守約 voice 目標時刻講準確、一字不改（修 8:39 被亂講成 8:29）；設 0＝退回舊「大約」措辭＝同現狀
            spontaneity_enabled=_bool("SPONTANEITY_ENABLED", True),          # 🌱「你怎麼還沒分享聯想」→ self_spontaneity 純對話＋自發湧現接地、不列 📂；設 0 關＝落回 fact_or_chat（evidence_gate 仍兜底）＝同現狀
            evidence_opinion_suppress=_bool("EVIDENCE_OPINION_SUPPRESS", True),  # 🚪 §0.56：問看法/理由/態度（非要資料）別開證據閘、別列 📂；設 0 關＝不擋＝同現狀
            anti_repeat_enabled=_bool("ANTI_REPEAT_ENABLED", True),          # 🔁 §0.56：回覆前附防重複提示（連發合起來答一次＋剛回過的別重講）；設 0 關＝不附＝同現狀
            anti_repeat_window_min=_int("ANTI_REPEAT_WINDOW_MIN", 8),        # 🔁 §0.56：判「剛回過」的窗（分）
            grounding_note_enabled=_bool("GROUNDING_NOTE_ENABLED", True),    # 🛡️ 接地三區塊尾端統一附「內部用、別主動當話題」守則；設 0 關＝不附加＝逐位元同現狀
            # 感覺判斷防抖：原始 gate 連續這麼多拍不變才「確認」，主動出聲才看確認後的 gate（吸收門檻邊界 blip）。
            selfstate_confirm_laps=_int("SELFSTATE_CONFIRM_LAPS", 3),
            # F2：感覺天花板（notified_self_gate）的極慢衰減——確認 gate 低於天花板達這麼多小時就降一級
            # （資料長期萎縮後，較低 gate 的真新湧現也能拿回「破天花板即時報」；預設 48h＝兩天，夠慢、不影響短期防抖）。
            selfstate_ceiling_decay_h=_int("SELFSTATE_CEILING_DECAY_H", 48),
            # 背景自陳「同一條線、同高度」的重報冷卻（比一般長）：免得整夜一直反芻同一條線太囉嗦。
            selfstate_repeat_cooldown_min=_int("SELFSTATE_REPEAT_COOLDOWN_MIN", 180),
            # 「你現在怎樣」被重複問：這麼多分鐘內又問 → 帶點無奈說「剛說過」，而非制式複誦（久久問則照常）。
            bodystate_repeat_window_min=_int("BODYSTATE_REPEAT_WINDOW_MIN", 8),
            self_repeat_window_min=_int("SELF_REPEAT_WINDOW_MIN", 8),   # 🪞 同類自我問題的重複窗（重複×心情長脾氣）
            verbosity_bias=_int("VERBOSITY_BIAS", 0),   # 🗜️ 回應篇幅整體偏置（−2..+2，負＝更短）；0＝純依複雜度自動漸變
            metacog_correct_cooldown_min=_int("METACOG_CORRECT_COOLDOWN_MIN", 30),   # 🪞 主動自我修正冷卻（分鐘）：拉長＝不當常設的恍神台詞
            # 感知韌性：感知失敗先容忍幾圈（用上一筆好資料撐著），連續失敗滿這麼多圈才終局死亡（避免暫態網路斷就死）。
            perceive_fail_grace=_int("PERCEIVE_FAIL_GRACE", 10),
            # 主觀體驗（自體軌跡→奇異吸子）：吸子成形/輪廓轉變時含蓄說一句（與感覺、餓共用冷卻）。
            experience_enabled=_bool("EXPERIENCE_ENABLED", True),
            experience_cooldown_min=_int("EXPERIENCE_COOLDOWN_MIN", 360),   # 體驗發話自有長冷卻（預設 6 小時）
            experience_confirm_laps=_int("EXPERIENCE_CONFIRM_LAPS", 5),
            # 🌀 縝密結合：把環境活絡度／晝夜／關係張力也折進主觀體驗的自體向量（半權）＝五維在「活成的形狀」交會。
            # 1=開（預設，讓體驗成為整合樞紐）；0=只用 bot 自身代謝六維（保守、形狀更穩）。
            experience_rich_vec=_bool("EXPERIENCE_RICH_VEC", True),
            # 💡 聯想湧現（跨主題語意橋累積→湧現一個你沒寫過的連結→含蓄說一句 Aha）：罕見、與感覺/餓/體驗共用冷卻。
            evidence_guard_enabled=_bool("EVIDENCE_GUARD", True),
            conscious_dialogue_enabled=_bool("CONSCIOUS_DIALOGUE", False),
            association_enabled=_bool("ASSOCIATION_ENABLED", True),
            association_cooldown_min=_int("ASSOCIATION_COOLDOWN_MIN", 360),   # 自有長冷卻（預設 6 小時，比照體驗）
            association_confirm_laps=_int("ASSOCIATION_CONFIRM_LAPS", 4),     # 橋強度連續這麼多拍站穩才算湧現（防抖）
            association_min_support=_int("ASSOCIATION_MIN_SUPPORT", 3),       # 至少這麼多筆片段沉澱過才湧現（真跨時間累積）
            association_seed_goal=_bool("ASSOCIATION_SEED_GOAL", True),       # 湧現後從連結長一個意圖（收緊 F 扣合）
            # 觀察旗標：1＝放寬所有門檻讓 Aha 幾圈內就湧現（供人親眼觀察，會頻繁×重複×不挑）；**預設 0＝罕見真實條件**
            # （§0.54：改回真實累積 confirm=4／support=3／強度≥0.66／相關性閘／6h 冷卻／30 分反連發／24h 去重）。設 1 復觀察模式。
            association_easy=_bool("ASSOCIATION_EASY", False),
            # 💡 §0.54「真的有特色才分享」硬門檻（加性、非 easy 才作用）：湧現的橋要 per-kind novelty 過地板才值得主動打斷。
            association_distinctive=_bool("ASSOCIATION_DISTINCTIVE", True),
            association_distinctive_thresh=_float("ASSOCIATION_DISTINCTIVE_THRESH", 1.0),  # 地板縮放（>1 更嚴、<1 更寬、≤0 關）
            # 💡 聯想湧現前後過程升級（皆加性、可關＝設成 0 即逐位元同舊行為）：預設開＝更像人類意識/想像
            association_novelty=_bool("ASSOCIATION_NOVELTY", True),           # 湧現帶 event['novelty']、語氣提示、同值(==)排序決勝
            association_warmth=_bool("ASSOCIATION_WARMTH", True),             # observe 暴露最暖未湧現橋觀測量（easy 下短路為 None）
            association_feedback_graded=_bool("ASSOCIATION_FEEDBACK_GRADED", True),   # 回饋三態 partial＋tally/n 累積
            association_ledger=_bool("ASSOCIATION_LEDGER", True),            # 出聲後寫洞見內容台帳（只真實 anchor）
            association_ripple_enabled=_bool("ASSOCIATION_RIPPLE_ENABLED", True),     # 🌊 內外搭配漣漪式湧現（對話＋自我遞迴弱觸發）
            association_ripple_window_min=_int("ASSOCIATION_RIPPLE_WINDOW_MIN", 30),  # 漣漪窗（分）：對話/最近聯想多久內仍起漣漪
            # 🌙 日有所思夜有所夢：對話漣漪窗稍長於遞迴窗——剛聊到的主題在更長一段時間內仍當「白天素材」擾動聯想（仍只擾動既有真實橋、不造節點）。env 設回 30 即同舊。
            association_ripple_conv_window_min=_int("ASSOCIATION_RIPPLE_CONV_WINDOW_MIN", 45),
            association_ripple_recursive_window_min=_int("ASSOCIATION_RIPPLE_RECURSIVE_WINDOW_MIN", 30),
            spontaneous_dedup=_bool("SPONTANEOUS_DEDUP", True),              # 自發出聲內容去重（控重複、自然降頻）；設 0 關＝逐位元同現狀
            spontaneous_dedup_sec=_int("SPONTANEOUS_DEDUP_SEC", 43200),      # 同一條線 12h 內算剛講過、跳過
            interrupt_rewrite_enabled=_bool("INTERRUPT_REWRITE_ENABLED", True),  # 插話即時改寫剩餘串（遞迴收尾）；預設開（灰度上線）；設 0 關＝原樣續送（插話後把預切串全送完）
            interrupt_max_depth=_int("INTERRUPT_MAX_DEPTH", 2),              # 同輪連環插話上限，達此強制暖收
            interrupt_statement_enabled=_bool("INTERRUPT_STATEMENT_ENABLED", True),  # 陳述續打也算插話中斷；預設開（與改寫同步灰度）；設 0 關＝陳述仍 defer 折進下一輪合併
            interrupt_wrap_condense_enabled=_bool("INTERRUPT_WRAP_CONDENSE_ENABLED", True),  # 🗣️ wrap 收尾把剩餘串濃縮＋淡收（非空收丟內容）；設 0 關＝原模板暖收＝同現狀
            wrap_close_natural_enabled=_bool("WRAP_CLOSE_NATURAL", True),  # 🎬 §1.69 暖收收尾去制式：LLM 收尾從「給範例（被逐字抄回＝每次『大概就是這樣了/先說到這裡』）」改「禁令＋多元收法」（自然停住/情緒餘韻/語助詞）＋模板退路依心情分暗平亮三池；設 0＝同現狀
            reply_replay_guard_enabled=_bool("REPLY_REPLAY_GUARD", True),  # 🔁 §1.71 重播守門：近 3 分鐘內真的送出過的泡泡不准原樣再送（跨輪跨路徑送出層最後防線；全剝空＝誠實短句）；設 0＝同現狀
            reachout_diverse_enabled=_bool("REACHOUT_DIVERSE", True),  # 🫧 §1.72 聯想自陳去公式化：形態選單＋禁用爛句式＋引最近記寫具體字句＋同主題提過≥2次＝改分享不再問；設 0＝原模板腔＝同現狀
            burst_one_answer_enabled=_bool("BURST_ONE_ANSWER", True),  # 🌊 §1.73 多訊息整體一答：同 route 用 hint；跨 route clone 演算後一次提交；重複只做原句 index 擷取；總 logical wire 封頂 4096 UTF-16。設 0 關閉此層
            burst_paced_bubbles_enabled=_bool("BURST_PACED_BUBBLES", True),  # 🫧 §2.30 一個連發回合的完整答案在送達層拆成長短交錯句泡，泡間 typing＋依長度停頓；全成才 commit/receipt。設 0＝單泡
            nudge_deliver_enabled=_bool("NUDGE_DELIVER", True),  # 🫸 §1.87 被催促時還在重複同一句話＝卡住 → 讓開對帳路由、掛「現在就做別踢球回去」守則＋輸出端剝踢球句＋重播守門不再罐頭拒答；設 0＝同現狀
            echo_prefix_run_enabled=_bool("ECHO_PREFIX_RUN", True),  # 🦜 §1.86 開頭連續段複誦（前幾段合起來就是他那句話、尾巴才是自己的話）也算複誦 → 剝掉那幾段保留尾巴；補「整則」與「單段」之間缺掉的粒度（實測 span 0.786/相似度 0.88/逐段 0.778 全部差一點點）；設 0＝同現狀
            echo_whole_guard_enabled=_bool("ECHO_WHOLE_GUARD", True),  # 🦜🎭 §1.74 整則複誦（連發多行照抄）也算覆述＝coach 重生成＋_say 認帳句；重生成帶「重複只有刻意引用或不重複」意圖守則；設 0＝同現狀
            human_affect_enabled=_bool("HUMAN_AFFECT", True),  # 🧠 §1.75 人類情緒動力學：中性歸零（不再每則訊息微暖）＋批評成格（被嫌 V−A+）＋負向偏誤 ×1.6＋習慣化遞減＋不對稱衰減（好心情散得快、低落黏得久）；設 0＝同現狀
            tone_felt_enabled=_bool("TONE_FELT", True),  # 🎚️ §1.76 口吻要被感覺到：染色門檻降到人的尺度（0.35→0.15，否則 §1.75 之後單一事件永遠不染）＋行為化語氣指令（怎麼說話、別把狀態講成台詞）＋_say 確定性整形（偏負時收掉驚嘆號/歡快 emoji）；設 0＝同現狀
            hostile_grace_enabled=_bool("HOSTILE_GRACE", True),  # 🌊 §1.77 抱怨≠請求的語意閘（「還傳這種貼圖」不再被當成要圖）＋氣頭上別反問/數帳/賣乖＋主動貼圖靜音；設 0＝同現狀
            habit_absence_enabled=_bool("HABIT_ABSENCE", True),  # 🌾 §1.79 日課今天還沒看到 → 在他自己的節奏窗內一句好奇（非提醒）；資格 14 天/iqr≤2h、due＝p75＋比例寬限、每天每線一次、過窗永遠沉默；設 0＝同現狀
            habit_absence_convo_enabled=_bool("HABIT_ABSENCE_CONVO", True),  # 🌾 §1.80 早安/出現的對話作息也算日課：平常這時間早該看到他、今天還靜靜的 → 主動去看看（他回來會看到）；資格較寬（7 天、iqr≤150）；設 0＝同 §1.79
            interrupt_wave_guard_enabled=_bool("INTERRUPT_WAVE_GUARD", True),  # 🌊 §1.81 同一波連發（≤12 秒）不當插話：不再拆成兩輪＝不再亂插接回橋、不再把兩個不同問題誤判成重複提問；設 0＝同現狀
            echo_burst_lines_enabled=_bool("ECHO_BURST_LINES", True),  # 🦜 §1.82 連發合併的每一行也當複誦候選（逐字複誦其中一句不再漏）；設 0＝同現狀
            write_claim_proactive_enabled=_bool("WRITE_CLAIM_PROACTIVE", True),  # 🕐 §1.84 主動出聲（🫧🫀💡🌾…）也套 §1.60 記寫守門：今天零記寫卻說「你今天早上又寫了X」→ 換事實句；設 0＝同現狀
            interrupt_wave_sec=_float("INTERRUPT_WAVE_SEC", 12.0),  # 🌊 §1.81 同波判定窗（秒）
            interrupt_coalesce_enabled=_bool("INTERRUPT_COALESCE", True),
            interrupt_tail_trim_enabled=_bool("INTERRUPT_TAIL_TRIM", True),
            reply_ack_dedup_enabled=_bool("REPLY_ACK_DEDUP", True),
            promise_anchor_bridge_enabled=_bool("PROMISE_ANCHOR_BRIDGE", True),
            confused_clarify_enabled=_bool("CONFUSED_CLARIFY", True),
            timejump_guard_enabled=_bool("TIMEJUMP_GUARD", True),
            push_data_memory_enabled=_bool("PUSH_DATA_MEMORY", True),
            write_today_ground_enabled=_bool("WRITE_TODAY_GROUND", True),
            fact_card_enabled=_bool("FACT_CARD", True),  # 🪪 §1.61 此刻事實卡：時間/記寫/約定/座標四類核心事實常駐注入每個互動輪（單一權威來源、與守門同一把 ground）——不再靠偵測器命中才接地；設 0＝同現狀  # 🕐 §1.60 記寫時間脈絡接地：reflect 附時間錨＋「你今天記寫了」宣稱守門（今天零記寫＝換事實句）＋今天問句真數據 hint——不再把自己的摘要推播時間當成使用者的記寫時間；設 0＝同現狀  # 🗂 §1.59 「先資料、後人話」的資料那則（剛歸戶/摘要/事件）也入對話史——不再否認「你不是剛剛告訴我歸戶的訊息」；設 0＝同現狀  # 🎭 §1.58 不演未來：「（30 分鐘後）嗨我回來了」舞台指示＝當場演掉未來 → 從該處截斷、保留誠實答應；設 0＝同現狀  # 🎴🗣️ §1.56 困惑句（什麼？/蛤？）不進貼圖逃生閘＋「看不懂」注入照真順序的前文清單（重述不揣測不倒序）；設 0＝同現狀  # 🤝 §1.55 跨句補時距：「給你思考20分鐘」＋「時間到了再跟我說X」拆兩句＝各自碎片全 miss → 拼句重跑 capture 真入帳（不再空口答應＋「現在才 11:05 呢」）；設 0＝同現狀  # 🧵 §1.54 連續回覆不疊致意句：150 秒內剛「嗯，我明白了。」過、這則的「我明白。」刪（同輪內重複也擋）；實質內容句不動、全刪光保留第一顆；設 0＝同現狀
            interrupt_statement_wrap_enabled=_bool("INTERRUPT_STATEMENT_WRAP", True),
            feeling_probe_depth_enabled=_bool("FEELING_PROBE_DEPTH", True),  # 🗜️ §1.51 情感探問（羨慕我嗎/想我嗎）＝句短意圖深：verbosity 地板 2＋「先答後問」hint＋§1.43 不修剪；明示要短/氣頭收斂仍優先；設 0＝原鏡射壓短＝同現狀  # 🧵 §1.50 陳述插話且主體已送過 → 殘句一律 wrap 濃縮融進插話後語境（連剩 1 串也不「還沒說完」橋＋原樣照播）；問句插話照舊；設 0＝同現狀  # 🧵 §1.49 插話後殘句取捨：道別輪答完插話就收口（不「我接著說」續客套尾巴）＋殘句對剛說過的去重（「我會記得的。」不 ×2）、全重複連橋都不送；設 0＝同現狀  # 🧵 §1.48 插話像人一樣接：附和短句（好久/沒關係/慢慢來）不打斷、折進下一輪一次承接＝零「嗯。」洗版零硬橋；真插話批次合成一則、一批一個回應；設 0＝同現狀
            interrupt_continuation_defer=_bool("INTERRUPT_CONTINUATION_DEFER", True),  # 🧵 續句（同時/還有…）defer 折進下一輪、別當 redirect 單獨答；設 0＝同現狀
            promise_continuation_merge=_bool("PROMISE_CONTINUATION_MERGE", True),  # 🧵 「同時/一起…」併進剛排程承諾（到點一起做）；設 0＝照常路由＝同現狀
            skill_recall_enabled=_bool("SKILL_RECALL_ENABLED", True),        # 🧑‍🏫 同主題再現召回已學做法、注入 coach.reply extra_system；**預設開**；設 0＝關（不召回不注入）＝同現狀
            skill_consensus_enabled=_bool("SKILL_CONSENSUS_ENABLED", True),   # 🧑‍🏫 對話凝共識→bot 提議「要學成做法嗎」；**預設開**；設 0＝關（不偵測不提議）＝同現狀
            skill_propose_cooldown_sec=_int("SKILL_PROPOSE_COOLDOWN_SEC", 90),  # 🧑‍🏫 提議冷卻（秒）；預設 90（原 600 太久擋掉連續教學）；廣化觸發、精準靠下游
            skill_situations_enabled=_bool("SKILL_SITUATIONS_ENABLED", True),   # 🧑‍🏫 §0.57 觸發分類（always/情境）；設 0＝退 legacy 單一 topic 召回＝同現狀
            skill_internal_coping_enabled=_bool("SKILL_INTERNAL_COPING", True),  # 🌀 §0.57 part B 內在狀態因應注入 self-presence；設 0＝不注入＝同現狀
            teaching_guard_enabled=_bool("TEACHING_GUARD_ENABLED", True),      # 🧑‍🏫 §0.59 Part 1a 別在回覆謊稱「記下來了」；設 0＝不注入守則＝同現狀
            skill_selfroute_capture_enabled=_bool("SKILL_SELFROUTE_CAPTURE", True),  # 🌀 §0.59 Part 1b self_* 路徑也能提議學成內在因應（只收 route-agnostic）；設 0＝同現狀
            spontaneous_coping_enabled=_bool("SPONTANEOUS_COPING_ENABLED", True),  # 🌀 §0.59 Part 2 主動出聲帶內在因應（升級為引擎）；設 0＝同現狀
            skill_legacy_migrate_enabled=_bool("SKILL_LEGACY_MIGRATE", True),    # 🧾 §0.60 舊情境做法冪等遷移成活觸發（承諾履行）；設 0＝不動＝同現狀
            skill_accountability_enabled=_bool("SKILL_ACCOUNTABILITY", True),    # 🧾 §0.60 問做法/約定→注入真帳本、只准照帳本答；設 0＝不注入＝同現狀
            promise_ack_guard_enabled=_bool("PROMISE_ACK_GUARD", True),          # 🤝 §0.61 未入帳的計時請求→別空口答應；設 0＝不掛＝同現狀
            sched_feeling_ground_enabled=_bool("SCHED_FEELING_GROUND", True),    # 🤝 §0.63 感覺分享守約附真實內在讀數（據實兌現）；設 0＝同現狀
            promise_capability_gate_enabled=_bool("PROMISE_CAPABILITY_GATE", True),  # 🤝 §0.64 超出能力的約定→誠實拒絕+說明原因；設 0＝同現狀
            sched_recur_daily_enabled=_bool("SCHED_RECUR_DAILY", True),          # 🤝 §0.64 每天固定時刻重複承諾（到點自動排明天）；設 0＝單次＝同現狀
            skill_proactive_enabled=_bool("SKILL_PROACTIVE", True),              # 🌀 §0.65 內在因應做法升級為真觸發（條件成立＋教過→主動發訊息）；設 0＝關＝同現狀
            promise_reply_bridge_enabled=_bool("PROMISE_REPLY_BRIDGE", True),    # 🤝 §0.66 對話回覆輪先兌現「已到點未兌現」的承諾；設 0＝關＝同現狀
            promise_status_ground_enabled=_bool("PROMISE_STATUS_GROUND", True),  # 🤝 §0.66 「你有叫我嗎/到了沒」→ 帳本接地作答；設 0＝同現狀
            sched_leave_autoarm_enabled=_bool("SCHED_LEAVE_AUTOARM", True),      # 🤝 §0.66 「我要離開約二十分鐘」自動記計時；設 0＝同現狀
            skill_proactive_cooldown_min=_int("SKILL_PROACTIVE_COOLDOWN_MIN", 180),  # 🌀 §0.65 內在因應主動觸發自有冷卻（分）
            skill_proactive_max_reach_outs=_int("SKILL_PROACTIVE_MAX_REACH_OUTS", 2),  # 🌀 §0.65 一段閒置的內在因應伸手上限
            skill_proactive_sticker_enabled=_bool("SKILL_PROACTIVE_STICKER", True),  # 🎴 §0.65 內在因應主動觸發順便送 help sticker；設 0＝不送＝同現狀
            coping_sticker_tone_enabled=_bool("COPING_STICKER_TONE", True),  # 🎴 §0.72 內在因應貼圖池依做法語氣挑（求救→非正向／邀請→正向＋中性）；設 0＝一律非正向＝同現狀
            skill_capability_gate_enabled=_bool("SKILL_CAPABILITY_GATE", True),  # 🚫 §0.73 教到做不到的外部動作→學習當下誠實拒絕、不存帳本；設 0＝不攔＝同現狀
            skill_view_refresh_enabled=_bool("SKILL_VIEW_REFRESH", True),  # 🤝 §0.89 /skills 檢視＝保鮮：刷新仍活著做法的 last_ts（只重置遺忘時鐘、不觸發行為）→ 修內在因應做法首觸前 5.5 天靜默淡忘；設 0＝不刷＝同現狀
            deferred_promise_enabled=_bool("DEFERRED_PROMISE", True),  # 🤝 §0.75 兩步延後約定：「等一下回答我」（無具體時刻）→存意圖問時間，下一句「4分鐘後/3:50」→真入帳到點兌現；設 0＝不接＝同現狀
            promise_overdue_guard_exempt=_bool("PROMISE_OVERDUE_GUARD_EXEMPT", True),  # 🤝 §0.78 逾期承諾豁免反連發守門；設 0＝舊整段 return＝同現狀
            promise_status_empty_ground=_bool("PROMISE_STATUS_EMPTY_GROUND", True),  # 🤝 §0.78 空帳本狀態問句也走誠實接地；設 0＝同現狀
            self_report_promise_enabled=_bool("SELF_REPORT_PROMISE", True),  # 🤝 §0.78 自陳型約定捕捉（逃生閘，實際門控在 selfstate 讀同名環境變數）；設 0＝退回無自陳捕捉＝同現狀
            promise_ledger_time_guard=_bool("PROMISE_LEDGER_TIME_GUARD", True),  # 🤝 §0.79 帳本回覆鐘點守門：LLM 幻覺此刻時刻→落回確定性帳本；設 0＝不守門＝同現狀
            promise_confirm_route_enabled=_bool("PROMISE_CONFIRM_ROUTE", True),  # 🤝 §0.79 確認時間家族在真有活承諾時硬錨帳本；設 0＝confirm 不搶＝同現狀
            promise_llm_rescue_enabled=_bool("PROMISE_LLM_RESCUE", True),  # 🤝🧠 §1.12 LLM 語意逃生閘：確定性捕捉 miss＋temporal 有未來錨→單次 gemini 判「是否請 bot 到點做事＋動作命名」（時刻永遠來自 temporal、LLM 永遠不准產出時刻）；設 0＝逃生閘不存在＝同現狀
            sticker_llm_rescue_enabled=_bool("STICKER_LLM_RESCUE", True),  # 🎴🧠 §1.15 貼圖請求語意逃生閘：確定性偵測全 miss＋結構閘在場→單次 gemini 判「是否要現在送一張貼圖給他本人＋要不要說明為什麼」（LLM 只判是非、真送的圖讀 circumplex 挑、沒貨誠實說沒有）；設 0＝逃生閘不存在＝同現狀
            sticker_pick_rescue_enabled=_bool("STICKER_PICK_RESCUE", True),  # 🎴🧠 §1.34 STICKER_V2/F3 挑選祈使逃生閘：貼圖情境下「挑一張給我／幫我選一個」補進 §1.15 逃生閘第三分支（近期貼圖情境＋窄挑選祈使前置式→燒一次 LLM 判、真送圖讀 circumplex）；設 0＝第三分支不存在＝挑選祈使仍落 fact_or_chat＝同現狀
            sticker_img_disambig_enabled=_bool("STICKER_IMG_DISAMBIG", True),  # 🎴🧠 §1.34 STICKER_V2/F5 貼圖情境「圖呢／圖」消歧：近期貼圖情境＋裸圖省略句→不撈私人附件、改走貼圖誠實路徑（據實談剛送那張／誠實沒送／維護句）；明確附件詞照走 attachment；設 0＝『圖呢/圖』仍走 attachment＝同現狀
            sticker_fakesend_guard_enabled=_bool("STICKER_FAKESEND_GUARD", True),  # 🎴 §1.34 STICKER_V2/F4 假送誠實閘：本輪未真 send_sticker 卻宣稱「挑了/送了一張貼圖」→ 整則替換誠實句（唯一 backing 真相＝_record_sticker_sent 設的 sticker_sent_this_turn；真送不攔）；設 0＝閘不 arm＝同現狀
            sticker_fakesend_soft_enabled=_bool("STICKER_FAKESEND_SOFT", True),  # 🎯 §1.70A 假送閘句級軟化：只剝假送宣稱句、正題保留＋誠實補註（整則都是宣稱才退回硬替換）——釐清句的正題回答不再被整則吃掉；設 0＝原整則替換＝同現狀
            open_offer_ground_enabled=_bool("OPEN_OFFER_GROUND", True),  # 🎯 §1.70B 懸著的提議接地：近 2h 內 bot 最近一句提議形問句（要我…嗎？）常駐入事實卡——「送什麼？」這種省略短回不再被主詞反轉成「你送過…」；設 0＝同現狀
            mood_coord_deliver_enabled=_bool("MOOD_COORD_DELIVER", True),  # 🧭 §1.47/§2.27 同輪 current＋snapshot/trajectory/repair 時間層契約；相鄰座標情境才承接追問，真送達才落 report；設 0＝只剩 §1.45/§1.25
            mood_coord_report_enabled=_bool("MOOD_COORD_REPORT", True),  # 🧭 §1.45 appraise 後採樣；此刻只給唯一快照，明確問軌跡才帶過去點；設 0＝不捕捉/注入/re-route
            promise_deliver_content_enabled=_bool("PROMISE_DELIVER_CONTENT", True),  # 📦 §1.44 內容型承諾兌現空心（只報到沒內容）→ 當場用 coach.reply 補生成內容接上；失敗＝誠實承認欠內容；非內容型（叫醒/問候）不動；設 0＝同現狀
            routine_card_enabled=_bool("ROUTINE_CARD", True),  # 📈 §1.96 作息進事實卡常駐（追問「有嗎」時才有數字可答）＋講法守則（主詞是他、我記到的口吻、樣本少講成自己的限制）；設 0＝同現狀
            mood_data_answer_enabled=_bool("MOOD_DATA_ANSWER", True),  # 🧭 §2.10 缺少等於本輪凍結快照的完整 V/A pair 才補 current 行；設 0＝不補
            short_dup_guard_enabled=_bool("SHORT_DUP_GUARD", True),  # 🦜 §2.13 短句 ack 三分鐘內不重複送；設 0＝同現狀
            act_first_enabled=_bool("ACT_FIRST", True),  # 🫧 §2.09 互動回覆開頭的純接話泡泡剝掉（先講那件事）；設 0＝同現狀
            association_focus_enabled=_bool("ASSOCIATION_FOCUS", True),  # 💡 §2.07 只講一件＋程式指定要戳哪一環；設 0＝同現狀
            foresight_chain_enabled=_bool("FORESIGHT_CHAIN", True),  # 🔮 §2.07 認帳時逐字帶出自己當初的原話；設 0＝同現狀
            worldline_chain_enabled=_bool("WORLDLINE_CHAIN", True),  # 🌐 §2.07 說出外部說法讓我的看法動了哪裡；設 0＝同現狀
            coping_act_voice_enabled=_bool("COPING_ACT_VOICE", True),  # 🌀 §2.08 內在因應與含蓄伸手分家；設 0＝同現狀
            mood_watch_ground_enabled=_bool("MOOD_WATCH_GROUND", True),  # 🧭 §2.08 沒紀錄就承認不知道、不准猜；設 0＝同現狀
            adapt_voice_enabled=_bool("ADAPT_VOICE", True),  # 🍃 §2.08 分得出是資料面還是對話面安靜；設 0＝同現狀
            soothe_own_question_enabled=_bool("SOOTHE_OWN_QUESTION", True),  # 🌬️ §2.06 指名我自己剛問的那句＋整則零問號；設 0＝同現狀
            close_round_stance_enabled=_bool("CLOSE_ROUND_STANCE", True),  # 🌊 §2.06 說得出是哪一種結束＋收法輪替＋拿掉成品台詞；設 0＝同現狀
            habit_absence_one_thing_enabled=_bool("HABIT_ABSENCE_ONE_THING", True),  # 🌾 §2.06 只講一個面向、真數字不進 prompt、出現版寫成給不在場的人的留言；設 0＝同現狀
            reachout_one_thing_enabled=_bool("REACHOUT_ONE_THING", True),  # 🫧 §2.06 只講一個「為什麼是現在」；重複時整則零問號；沒素材就承認沒有；設 0＝同現狀
            ac_drift_material_enabled=_bool("AC_DRIFT_MATERIAL", True),  # 🧩 §2.05 只給素材不給範例句＋體感向度輪替＋成對收尾回指＋身分標記；設 0＝同現狀
            metacog_correct_voice_enabled=_bool("METACOG_CORRECT_VOICE", True),  # 🪞 §2.05 只挑一個理由的收回（2–3 則訊息串）；設 0＝原確定性模板＝同現狀
            selfmod_birth_one_enabled=_bool("SELFMOD_BIRTH_ONE", True),  # 🦋 §2.05 重生報到只講一件、還沒用過要承認；設 0＝同現狀
            experience_headline_enabled=_bool("EXPERIENCE_HEADLINE", True),  # 🌀 §2.04 回顧體只餵「跟上一段跨最多的那一軸」＋串接條文（仍是 2–4 則訊息串）；設 0＝仍餵十幾行＝同現狀
            rotate_monotonic_enabled=_bool("ROTATE_MONOTONIC", True),  # 🔁 §2.04 🔮🌐💡 的形態輪替改用落盤單調計數器（原本 len(台帳)%N 被截尾凍死＝永遠同一種）；設 0＝同現狀
            self_promise_trace_enabled=_bool("SELF_PROMISE_TRACE", True),  # 🤖 §2.03 自諾入帳鏈每個出口留痕＋/promises 列出「說過但沒進帳本」的句子與原因（只寫 state、不改行為）；設 0＝同現狀
            replay_wave_ack_enabled=_bool("REPLAY_WAVE_ACK", True),  # 🔁 §2.03 同一波第二則被重播守門剝空時改用短承接，不再用那句把選擇權丟回去的罐頭；設 0＝同現狀
            convo_gap_stated_enabled=_bool("CONVO_GAP_STATED", True),  # ⏱ §2.28 「好像很久沒理你了」＝陳述式的「多久沒聊」→ 導 convo_time 給真 gap 事實、不再「你不是剛才才跟我說話嗎」；設 0＝同現狀
            smallhours_arrival_enabled=_bool("SMALLHOURS_ARRIVAL", True),  # 🌙 §2.27 凌晨出現＝昨天還沒收、不是「今天來得早」：跨 05:00 界不做早/晚比較、守門剝跨界宣稱；設 0＝同現狀
            quote_speaker_guard_enabled=_bool("QUOTE_SPEAKER_GUARD", True),  # 🪞 §2.26 「你說「X」」而 X 是我自己說的 → 就地改「我剛說」；設 0＝同現狀
            keep_retro_dur_enabled=_bool("KEEP_RETRO_DURATION", True),  # 🕰️ §2.25 「剛剛這 20 分鐘」是回顧不是再拖：拖延閘先剝回顧式時長＝同約不再欠帳重演；設 0＝同現狀
            confused_slang_enabled=_bool("CONFUSED_SLANG", True),  # 🗣️ §2.24A 「供殺小」＝帶嗆的「你在講什麼」：進澄清接地＋算氣頭（嗆聲當輪不送歡快貼圖）；設 0＝同現狀
            clarify_anchor_enabled=_bool("CLARIFY_ANCHOR", True),  # 🗣️ §2.24B 澄清錨定最近一則實質內容、不逐條複述來回；設 0＝同現狀
            habit_inventory_enabled=_bool("HABIT_INVENTORY", True),  # 📊 §2.23 「你觀察到我有哪些習慣」走專屬盤點出口、不再翻記錄原文吐 📁；設 0＝同現狀
            greet_routine_aware_enabled=_bool("GREET_ROUTINE_AWARE", True),  # 🕘 §2.22 問候帶有意識的作息覺察、不再拿統計模板回「早安」；設 0＝同現狀
            self_promise_no_rolled=_bool("SELF_PROMISE_NO_ROLLED", True),  # 🤖 §2.21 「現在是HH:MM」不得滾成明天的幽靈約定；設 0＝同現狀
            promise_mood_numbers_enabled=_bool("PROMISE_MOOD_NUMBERS", True),  # 🧭 §2.20 座標守約沒帶數字→程式補接地行；設 0＝同現狀
            mood_watch_oneshot_yield=_bool("MOOD_WATCH_ONESHOT_YIELD", True),  # 🧭 §2.19 帶未來時間錨＝一次性請求→讓路給排程承諾；設 0＝同現狀
            promise_keep_followup_enabled=_bool("PROMISE_KEEP_FOLLOWUP", True),  # 🤝 §2.18 履約後他一直沒回→輕聲問一次；設 0＝同現狀
            promise_judge_soft_veto=_bool("PROMISE_JUDGE_SOFT_VETO", True),  # 📦 §2.17 四閘全過時 judge 的 no 降級 unknown（拆掉假欠帳循環的引擎）；設 0＝同現狀
            promise_fire_dedup_enabled=_bool("PROMISE_FIRE_DEDUP", True),  # 🤝 §2.17 剛兌現過同目標時刻的約定→吸收不重演；設 0＝同現狀
            promise_same_appointment_merge=_bool("PROMISE_SAME_APPOINTMENT", True),  # 🤝 §2.02 同一個約定只來一次：入帳容差隨回覆延遲伸縮＋兌現端把同約定的其他帳一起結掉（＋/promises 對帳）；設 0＝同現狀
            habit_claim_role_enabled=_bool("HABIT_CLAIM_ROLE", True),  # 📈 §1.97 作息守門從詞面樣式改讀語意角色（主角/基準/述語/領域）＋記寫域改用 records 的時刻分佈驗；設 0＝只走原本的詞面路徑＝同現狀
            worldline_monthly_budget=_bool("WORLDLINE_MONTHLY_BUDGET", True),  # 🌐 §2.16 月結額度（終身 40 次上限＝定時炸彈）；設 0＝終身上限
            worldline_followup_enabled=_bool("WORLDLINE_FOLLOWUP", True),  # 🌐 §2.16 追問有接地＋撞後對帳；設 0＝同現狀
            worldline_spark_enabled=_bool("WORLDLINE_SPARK", True),  # 🌐 §2.16 延伸 vs 開新線的邀請輪替；設 0＝同現狀
            worldline_enabled=_bool("WORLDLINE", True),  # 🌐 §2.00 **改成預設開**（2026-07-28 使用者實測後明講「要如何打開 worldline 讓他運作」）。§1.95 當初預設關是因為這是第一條會把他的字串送出到 Google、且另外計費的管道——但他只跑 ./run-temp.sh、不編 .env，預設關等於他要不到。**真正的同意閘不是這個旗標，是白名單**：`state.worldline_allow` 空的時候整支照樣不動、一個字都不送。要關掉在 .env 設 WORLDLINE=0
            worldline_cooldown_h=_int("WORLDLINE_COOLDOWN_H", 24),
            worldline_max_searches=_int("WORLDLINE_MAX_SEARCHES", 40),
            worldline_tool_field=os.environ.get("WORLDLINE_TOOL_FIELD", "google_search").strip() or "google_search",
            self_roster_enabled=_bool("SELF_ROSTER", True),  # 🪪 §1.94 能力盤點＋願望帳：/abilities 看得到我有哪些機制、哪些真的用出來過、以及我從原始碼看出來的缺口（每個缺口自帶機器跑得動的驗收條件）；設 0＝同現狀
            capability_card_enabled=_bool("CAPABILITY_CARD", True),  # 🪪 §1.91 事實卡加一行「我現在真的有的能力」（接地：這次醒來的真實 git 改動＋那個能力有沒有真的用出來過）→ 被問「你學會了嗎/願望達成了嗎」不再憑感覺否認自己真有的機制；設 0＝同現狀
            foresight_enabled=_bool("FORESIGHT", True),  # 🔮 §1.90 記寫預想：挑一條「一端還在寫、另一端停住」的真實橋，把「停住那條會再回來」當成有 TTL 的假設說出口，到期不論中不中都回頭認帳；設 0＝同現狀
            foresight_min_support=_int("FORESIGHT_MIN_SUPPORT", 2),  # 🔮 §1.93 support 門檻（不跨重啟保留，常重啟就調小）
            foresight_dormant_min_days=_float("FORESIGHT_DORMANT_MIN_DAYS", 5.0),  # 🔮 §1.93 「停住」下限（天）
            foresight_cooldown_min=_int("FORESIGHT_COOLDOWN_MIN", 720),  # 🔮 §1.90 自有冷卻 12h（末三筆全 miss 自動加倍）
            foresight_ttl_days=_int("FORESIGHT_TTL_DAYS", 7),  # 🔮 §1.90 假設有效期 7 天
            self_promise_dedup_enabled=_bool("SELF_PROMISE_DEDUP", True),  # 🧬 §1.89 bot 對「剛入帳那筆約定」的確認句會被 §1.18 當成新約再記一筆 → 同刻（±90 秒）已有未兌現約定就不重複入帳；設 0＝同現狀
            promise_owed_push_max=_int("PROMISE_OWED_PUSH_MAX", 1),  # 📦 §1.88 每筆承諾「主動」補交付的次數上限（不等他開口就把欠的內容送出去；交不出來就誠實結案成 owed_unmet＝沒做到）；設 0＝不主動補也不結案＝同現狀
            promise_delivery_proof_enabled=_bool("PROMISE_DELIVERY_PROOF", True),  # 📦 §1.85 交付舉證：驗收改問「他要的東西在不在這則訊息裡」（極性反轉＋確定性四閘＋單次是非判）＋補生成後再驗一次＋送達舉證＋只有真交付才記 fulfilled（否則 status='owed'、帳本與守門一律改口「我人到了、內容沒交出來」）＋owed 由回覆橋續開交付；設 0＝回 §1.44/§1.64 原判定與原記帳＝同現狀
            promise_teaser_hollow_enabled=_bool("PROMISE_TEASER_HOLLOW", True),  # 🎬 §1.64 預告不算交付：§1.44 空心偵測升級成實質殘量判定——「我剛剛一直在想著X…那是什麼。」思考過程敘述＋複述題目不算內容（截圖 22:13 被「然後呢」催了才交付）→ 照樣當場補生成；設 0＝回 §1.44 原二元判定＝同現狀
            self_feel_condense_enabled=_bool("SELF_FEEL_CONDENSE", True),  # 🗜️ §1.43 別人問別的、答案尾端鋪陳一長串內在質地 → _say 修剪成前兩句關鍵＋兩小時內才自陳過的事前「一兩句點到重點」提醒；真問 bot 自己＝不修剪；設 0＝同現狀
            user_habit_ground_enabled=_bool("USER_HABIT_GROUND", True),  # 📈 §1.42 使用者習慣模型：捕捉對話作息事件（跨重生）＋純函式統計＋問habits時注入真統計（樣本不夠誠實說不準）＋greeting 注入今天vs平常＋_say 守門剝亂掰的「你通常X點/比平常早晚」；設 0＝全關＝同現狀
            habit_obs_fix_enabled=_bool("HABIT_OBS_FIX", True),  # 📈 §1.63 習慣觀測修真：「一天第一句」改真日曆日分組（不再被中午/晚上的再現身拉成 09:54–20:58）＋貼圖/媒體接觸入帳 contact 事件（清晨貼圖早安不再隱形）＋「我常跟你說早安喔」不記 greet_*/不進 greeting lane＋/habits 對帳指令；設 0＝同現狀（回 §1.42 原統計）
            away_sense_enabled=_bool("AWAY_SENSE", True),  # 🍽 §1.65 暫離常識：「吃飯去」記 user_away（活動＋常識最短時距）→ 事實卡常駐「才過 N 分、他還沒回來；別說你回來啦、別問吃飽」＋_say 剝錯誤預設句；時距滿＝講「他大概回來了」後清；設 0＝同現狀
            mood_watch_enabled=_bool("MOOD_WATCH", True),  # 🧭 §1.66 座標變動常設回報：「座標有變動就主動回報」入帳 state.mood_watch（常設、跨重生、可取消）＋生命迴圈每圈對照真座標、|Δ|≥0.10＋過 30 分冷卻＝回報真數字＋事實卡常駐「這條約定活著」。§1.68 補遺（截圖「只主動回報一次？」）：原 emit 不管送沒送到都推進基準/冷卻＝報告被互動打斷/送失敗吃掉一次、delta 被無聲消耗、之後永遠在新基準 0.10 內＝再也不報 → 改①在場延後（120s 內你還在打字＝不發也不動基準，比照 _promise_emit）②送達才記帳（client.send 驗證成功才推進；失敗＝下一拍重試、60s 退避）③ /moodwatch（/座標回報）對帳指令（基準/此刻/Δ/門檻/冷卻/tick 心跳）。§1.78 補遺（截圖 21:33 那則不分訊息串、一次呈現）：§1.68 的直接 client.send **繞過了 _say 的分串與 markdown 清洗**——回報變一大塊、LLM 的 *** 分隔線原樣外洩，且那則還夾進整段「反省」（21:31 承諾兌現的內容），使 21:34 真兌現只能說「剛剛已經說過差不多的了」＝兌現被自己的回報吃掉。修：_say 本來就回傳送出成功與否 →改回走 _say 並用其回傳值記帳（分串/清洗/驗證兼得）＋越權夾帶指紋（>160 字、含 markdown 分隔線）退回模板＋prompt 明令「只講座標這件事、不夾別的待辦、三句以內」；設 0＝同現狀（回到空口答應前的行為）
            mood_watch_voice_enabled=_bool("MOOD_WATCH_VOICE", True),  # 🎨 §1.67 座標回報去機械感：主動回報改 bot 第一人稱口吻（coach 潤色；數字程式算、逐字驗收、多一個小數就打回模板）＋訂閱 ack 掛 §1.47 接地豁免（不再被守門咬成模板）；設 0＝同 §1.66 模板行為
            promise_keep_anti_repeat_enabled=_bool("PROMISE_KEEP_ANTI_REPEAT", True),  # 🔁 §1.41 守約去重複：排程兌現句近乎照抄 bot 剛說過的話（截圖 20:42 逐字重播 20:32）→ _promise_keep_body 末端確定性換誠實『已說過、沒變化』句；設 0＝同現狀
            promise_sticker_fakesend_guard_enabled=_bool("PROMISE_STICKER_FAKESEND_GUARD", True),  # 🎴 §1.40 送貼圖承諾兌現假送守門（§1.34 主動路徑版）：這輪貼圖沒真送出卻懸空宣告「這次我選這張貼圖…：」→ _promise_keep_body 末端確定性剝掉宣告句（引用歸屬/否定/自帶誠實不誤剝）；設 0＝同現狀
            wake_projection_guard_enabled=_bool("WAKE_PROJECTION_GUARD", True),  # 🌅 §1.39 自他邊界守門：bot 重生醒來＋心情悶悶的別投射成使用者「你醒了/你剛小睡了一下/悶悶的」——_say 剝掉未接地的第二人稱睡醒斷言＋事前注入邊界 hint；引用歸屬/bot 講自己/祈使不誤剝；旗標關＝不 arm＝同現狀
            selfshare_reason_ground_enabled=_bool("SELFSHARE_REASON_GROUND", True),  # 🍃 §1.38 自陳理由接地（§1.36 sibling）：🍃 換檔自陳送出後附真實理由到 last_selfshare＋fact_or_chat 被問「為什麼那樣說」時注入該理由接地（含別扯貼圖）；旗標關＝不附不注入＝同現狀
            recall_ground_guard_enabled=_bool("RECALL_GROUND_GUARD", True),  # 🧭 §1.36 記寫回想不再編造：content-recall 偵測器＋強接地 hint（命中才注入）＋_say 事後幻覺守門（答案歸因給記寫的具體事由不在原文→整則替換誠實句）；bot 自身感受/推理與引用歸屬不攔、原文真有的 substring 命中不算幻覺；設 0＝守門與 hint 全關＝同現狀
            sticker_why_ground_enabled=_bool("STICKER_WHY_GROUND", True),  # 🎴 §1.16 複合請求送圖＋據此刻 circumplex 心情說明為什麼是這張（單一真相；未看過不捏造圖案、無貨不 emoji 假裝）；設 0＝退回罐頭 sticker_send_reply＝同 §1.15-only
            promise_outcome_ground_enabled=_bool("PROMISE_OUTCOME_GROUND", True),  # 🤝 §1.13A 逾期質問（結果呢/做到了嗎/你來了？）真有活承諾或感覺託付時走帳本誠實對帳；設 0＝不搶＝同現狀
            promise_said_ground_enabled=_bool("SAID_RECALL_GROUND", True),  # 🤝 §1.20 說過質問接地＋帳本日期詞程式產出＋否認句誠實閘；設 0＝同現狀
            sticker_sent_memory_enabled=_bool("STICKER_SENT_MEMORY", True),  # 🎴🧠 §1.23 送出貼圖入對話史＋四欄位跨重生＋否認誠實閘；設 0＝同現狀
            promise_keep_claim_guard_enabled=_bool("PROMISE_KEEP_CLAIM_GUARD", True),  # 🤝 §1.13B 帳本逾期/剛錯過時的「我做到了」假兌現宣稱→整則替換確定性誠實句；設 0＝不攔＝同現狀
            hostile_converge_enabled=_bool("HOSTILE_CONVERGE", True),  # 🌊 §1.14 敵意連發（文字/負向貼圖 ≥2）→ 篇幅降檔（level 0/泡泡≤2）＋被插話後不自顧自續講；只動 tone/篇幅/插話策略、不碰路由與情緒數值；設 0＝同現狀
            skill_recall_window_enabled=_bool("SKILL_RECALL_WINDOW", True),  # 🧑‍🏫 §1.53 做法召回窗：topic 型比對源＝當句＋近 3 則使用者訊息（不再只認當句逐字含標籤＝幾乎永不觸發）＋召回命中印 log 可驗證；設 0＝只看當句＝同現狀
            skill_offer_doubt_gate_enabled=_bool("SKILL_OFFER_DOUBT_GATE", True),  # 🧑‍🏫 §1.52 質疑/不信情境（本句或近 5 則帶懷疑詞）不發教學提議——「我都有些懷疑」不再被誤當共識蹦出「記成做法」行話；設 0＝照舊提議＝同現狀
            hostile_affect_fix_enabled=_bool("HOSTILE_AFFECT_FIX", True),  # 🧭 §1.46 敵意不推暖：is_hostile 命中卻落「一般陪伴」預設的句子（你太爛了/我不信…）改推質疑方向（V−A+）＝被罵 mood 不再上升；詞表已分類句原值原樣；設 0＝同現狀
            self_state_converge_enabled=_bool("SELF_STATE_CONVERGE", True),  # 🌊 §1.17 self_state 洪水收斂：只問一件小事卻吐 8 顆泡泡（截圖 19:27）→ self_state 專屬串數封頂（與 §1.14 敵意收斂 min-combine）；只封串數、不動 level、不碰路由/情緒；設 0＝不封頂＝同現狀
            self_state_bubble_cap=_int("SELF_STATE_BUBBLE_CAP", 3),  # 🌊 §1.17 self_state 洪水收斂時串數上限（預設 3；max(1,·) 保護 0/負值）
            self_report_delta_enabled=_bool("SELF_REPORT_DELTA", True),  # 🧠 §1.21 差分自陳：記住上次自陳（原話＋帶位快照）→ 這次只講真的變了的、沒變短句帶過、上次原話當負面示例；設 0＝同現狀
            self_report_nochange_window_min=_int("SELF_REPORT_NOCHANGE_WINDOW_MIN", 90),  # 🧠 §1.21 無變化短句窗（分）：窗內且帶位全同才走短句
            self_report_prior_horizon_min=_int("SELF_REPORT_PRIOR_HORIZON_MIN", 2880),  # 🧠 §1.21 prior 最大齡（分、預設 48h）：超齡＝視同無 prior
            phrase_anti_reuse_enabled=_bool("PHRASE_ANTI_REUSE", True),  # 🎨 §1.22 措辭反重複：去範例句 VARIED＋上次原話當【禁止重複】負面示例＋事實層模板池加大反重複；設 0＝同現狀
            bot_self_promise_enabled=_bool("BOT_SELF_PROMISE", True),  # 🤖 §1.18 bot 自發承諾入帳：_say 互動出口掃 bot 說出口的「第一人稱未來承諾＋temporal 可解未來時刻」→ LLM 閘判新立/複誦 → origin='self' 入同一本帳、到點同樣兌現（時刻永遠 temporal 解 bot 那句話）；設 0＝掃描器不存在＝同現狀
            promise_preempt_enabled=_bool("PROMISE_PREEMPT_LINK", True),  # 🤝 §1.19 搶先兌現的因果連結：使用者比約定時刻先出現 → 叫醒/問候類先送因果 🤝 泡泡＋標 preempted（daily 顯式推進）、主題回報類 LLM 閘判「真在問那個主題」才當場兌現；設 0＝不掃＝同現狀
            promise_preempt_window_sec=_int("PROMISE_PREEMPT_WINDOW_SEC", 5400),  # 🤝 §1.19 搶先窗秒數（預設 90 分）：只搶 target 在 (now, now+窗] 的 pending
            promise_preempt_future_guard_enabled=_bool("PROMISE_PREEMPT_FUTURE_GUARD", True),  # 🤝 §1.24 搶先的未來詞守門＋提前誠實模式：句含「等下/到時/時間到的時候/N分鐘後」＝在講未來約定→不送 LLM 不搶；真提前兌現＝誠實說「你先提起了，那我現在就先說」、絕不說「{when} 到了」；設 0＝§1.19 現狀
            promise_mood_ground_enabled=_bool("PROMISE_MOOD_GROUND", True),  # 🧭 §1.25 情緒座標承諾接地：抽對委託（剝前導寒暄＋情緒/心情座標標籤）＋入帳存 circumplex 快照＋兌現只准渲染程式算的差分（不得改談程式更新）；設 0＝同現狀
            cost_query_tighten_enabled=_bool("COST_QUERY_TIGHTEN", True),  # 💸 §1.26 cost 路由 tight 守門：抱怨框（浪費/亂花/白花/花我的）排除先行＋金錢線索詞須配問句形（含「呢」）；罵句降回一般聊天、真查帳照吐 💸；設 0＝同現狀
            skill_offer_hostile_gate_enabled=_bool("SKILL_OFFER_HOSTILE_GATE", True),  # 🧑‍🏫🌊 §1.27 敵意情境不發教學提議：本句敵意/hostile_streak≥1/近 5 則使用者訊息有敵意句 → 提議直接丟棄（不發不寫冷卻不補發）；設 0＝照舊提議＝同現狀
            echo_strip_wire_enabled=_bool("ECHO_STRIP_WIRE", True),
            proactive_time_ground_enabled=_bool("PROACTIVE_TIME_GROUND", True),  # 🕐 §1.30 主動 emit 餵此刻時段＋禁報鐘點；設 0＝同現狀
            proactive_clock_guard_enabled=_bool("PROACTIVE_CLOCK_GUARD", True),  # 🕐 §1.31 主動 emit 硬鐘點守門（§1.30 硬後盾）：LLM 主動正文宣稱錯的此刻鐘點→就地換真實時段詞；設 0＝不守門＝同現狀
            sched_you_reply_enabled=_bool("SCHED_YOU_REPLY", True),  # 🤝 §0.80 你-主語回覆承諾捕捉（逃生閘，實際門控在 selfstate 讀同名環境變數）；設 0＝退回無此捕捉＝同現狀
            sched_continue_speak_enabled=_bool("SCHED_CONTINUE_SPEAK", True),  # 🤝 §0.81 繼續說類承諾捕捉（逃生閘，實際門控在 selfstate 讀同名環境變數）；設 0＝退回無此捕捉＝同現狀
            sched_self_explain_enabled=_bool("SCHED_SELF_EXPLAIN", True),  # 🤝 §0.83 分享/說明內在承諾捕捉（逃生閘，實際門控在 selfstate 讀同名環境變數）；設 0＝退回無此捕捉＝同現狀
            global_time_anchor=_bool("GLOBAL_TIME_ANCHOR", True),  # 🕐 §0.82 全域硬時間錨（每條聊天回覆最前注入真實此刻）；設 0＝不注入＝同現狀
            global_clock_guard=_bool("GLOBAL_CLOCK_GUARD", True),  # 🕐 §0.82 全域鐘點守門（互動回覆送出前改回說錯的現在時刻）；設 0＝不守門＝同現狀
            promise_tick_resilient_enabled=_bool("PROMISE_TICK_RESILIENT", True),  # 🤝 §0.77 守約 tick 提到感知環最前、不被 Drive 暫斷連坐；設 0＝只在 feel 相跑＝同現狀
            promise_cancel_enabled=_bool("PROMISE_CANCEL", True),      # 🤝 §0.76 取消約定真的動帳本；設 0＝不接＝同現狀
            promise_expire_apology_enabled=_bool("PROMISE_EXPIRE_APOLOGY", True),  # 🤝 §0.76 失約標 expired 時主動道歉；設 0＝靜默＝同現狀
            skill_use_refresh_enabled=_bool("SKILL_USE_REFRESH", True),  # 🧑‍🏫 §0.76 做法用到刷 last_ts 保鮮；設 0＝同現狀
            emit_interrupt_enabled=_bool("EMIT_INTERRUPT_ENABLED", True),    # 插話對所有 bot 回應生效（自發出聲相也偵測並優先回應插話）；預設開；設 0 關＝同現狀（只互動回覆會偵測插話）
            assoc_suppress_enabled=_bool("ASSOC_SUPPRESS_ENABLED", True),    # 「停止聯想」→ 實際抑制主動聯想/翻閱出聲；設 0 關＝只進 grounding、不硬 gate（同舊）
            coherent_reply_enabled=_bool("COHERENT_REPLY_ENABLED", True),    # 「別一直分段/講連貫」→ 本輪回覆封到適中串數上限（統一回答、仍分段依序送、可被插話）；設 0 關＝只進 grounding、不硬封串數（同舊）
            coherent_reply_bubbles=_int("COHERENT_REPLY_BUBBLES", 3),        # 「講連貫」時的適中串數上限（預設 3；非 1 坨牆、非 10 碎泡泡）
            heartbeat_stall_grace_h=_int("HEARTBEAT_STALL_GRACE_H", 6),
            heartbeat_stall_require_dormant=_bool("HEARTBEAT_STALL_REQUIRE_DORMANT", True),  # 🫀 只在 sweep 真停（連 ingest/分類都久沒動）才報卡住；修升格罕見的誤報；0=同舊行為
            notify_filings=_bool("NOTIFY_FILINGS", True),
            filing_max_age_h=_int("FILING_MAX_AGE_H", 24),
            gemini_api_key=os.environ.get("GEMINI_API_KEY", "").strip(),
            gemini_model=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash",
            enable_chat=_bool("ENABLE_CHAT", True),
            llm_voice=_bool("LLM_VOICE", True),
            cost_alert_twd=_float("COST_ALERT_TWD", 10.0),
            cost_window_min=_float("COST_WINDOW_MIN", 10.0),
            cost_alert_cooldown_min=_float("COST_ALERT_COOLDOWN_MIN", 30.0),
            usd_twd_rate=_float("USD_TWD_RATE", 32.0),
            gemini_price_in_per_m=_float("GEMINI_PRICE_IN_PER_M", 0.30),
            gemini_price_out_per_m=_float("GEMINI_PRICE_OUT_PER_M", 2.50),
            # 🦋 重生後主動報到：若這次醒來相對上次『程式真的變了』→ 主動說一句改了什麼（只在真的蛻變時說）。
            selfmod_announce_birth=_bool("SELFMOD_ANNOUNCE_BIRTH", True),
            selfstate_enabled=_bool("SELFSTATE_ENABLED", True),
            selfstate_adaptive=_bool("SELFSTATE_ADAPTIVE", True),
            selfstate_sensitivity=_float("SELFSTATE_SENSITIVITY", 2.0),
            selfstate_z_star=_float("SELFSTATE_Z_STAR", 2.0),
            selfstate_tau_star=_float("SELFSTATE_TAU_STAR", 0.78),
            selfstate_int_min=_float("SELFSTATE_INT_MIN", 0.5),
            selfstate_diff_min=_float("SELFSTATE_DIFF_MIN", 0.0),
            selfstate_n_min=_int("SELFSTATE_N_MIN", 8),
            selfstate_r_min=_int("SELFSTATE_R_MIN", 3),
            # 生命迴圈每環之間等待秒數＝環的轉速（越短越快、活著感越密、對話越即時，但每環檢查更頻繁）。
            lifeloop_wait_secs=_float("LIFELOOP_WAIT_SECS", 3.0),
            # 🧭 AC 最終版規格（spec.py 單一真相源）三個加性旗標（預設全 False＝完全退回現行行為）：
            # 🧭 AC 最終版規格落地（皆加性、可關＝設 0 即逐位元回舊行為）：預設開＝更具體完整、更像人類意識行為
            ac_spec_panel=_bool("AC_SPEC_PANEL", True),               # /ac 面板與 facts 顯示 S₁–F₄ 具名子參數逐一 status
            ac_dependency_gate=_bool("AC_DEPENDENCY_GATE", True),     # assess 啟用 S→B→F 門控（S 垮→F/B coupled 強制 False）
            coupling_tone_enabled=_bool("COUPLING_TONE_ENABLED", True),  # self_presence 回應加性注入扣合語氣染色（只調語氣）
            # 🪞 自我表達可塑性（Phase 6，自我遞迴學習；皆加性、可關＝設 0 即逐位元回現行純記憶體去台詞行為）：
            #   bot 自我描述送出的「自我母題」捕捉進耐久調色盤（engrams、跨重生），用越多權重越高＝越像台詞；
            #   調色盤反過來在 _self_voice_mod 注入耐久換句話（避開用爛母題、從此刻真實內在長新說法）。
            selfexpr_plasticity_enabled=_bool("SELFEXPR_PLASTICITY_ENABLED", True),
            # 🌊 連發合併（皆加性、可關）：開＝把「回應前」相鄰夠密集的數則 owner 訊息併成同一邏輯輪次、
            #   整體回一次（不再每則各回一次、彼此互相打斷），更像人類把連發短句當一段話來聽。
            #   預設開（更貼近意識行為）；要退回逐則派發設 BURST_COALESCE_ENABLED=0。
            burst_coalesce_enabled=_bool("BURST_COALESCE_ENABLED", True),    # 預設開＝連發合併（設 0 退回逐則）
            burst_coalesce_sec=_float("BURST_COALESCE_SEC", 10.0),          # 尾群末則安靜此秒數才派發（settle gap）：先等使用者把這串想法打完、再整段回一次；單則也約等 10s，嫌慢可設 6。相鄰訊息是否屬同一輪另由 BURST_TURN_GAP_SEC 判定
            burst_turn_gap_sec=_float("BURST_TURN_GAP_SEC", 20.0),          # 同一 snapshot 尚未回過的相鄰訊息可用較寬閱讀窗合成一輪；不拉長單則 settle 等待
            burst_turn_max_span_sec=_float("BURST_TURN_MAX_SPAN_SEC", 60.0),# 多則短間隔可連成最多 60s 的一波；仍受 BURST_MAX_MSGS 上限保護
            burst_max_wait_sec=_float("BURST_MAX_WAIT_SEC", 60.0),          # 只有持續連發、末則一直未靜默 10s 才會等到此上限；單則仍按 settle gap 回覆
            burst_max_msgs=_int("BURST_MAX_MSGS", 12),                      # 單一 burst 最多納入訊息數（達此斷群 flush）
            state_path=os.environ.get("STATE_PATH", "./state.json").strip() or "./state.json",
            dry_run=_bool("DRY_RUN", False),
        )

    # 各入口需要的欄位集合
    DRIVE_KEYS = ("google_credentials", "drive_root_folder_id", "owner_line_user_id")
    TELEGRAM_KEYS = ("telegram_bot_token", "telegram_chat_id")

    # 欄位 → 對應的環境變數名（多數同名，僅憑證例外）
    ENV_NAMES = {
        "google_credentials": "GOOGLE_APPLICATION_CREDENTIALS（或雲端用 GOOGLE_CREDENTIALS_JSON）",
        "drive_root_folder_id": "DRIVE_ROOT_FOLDER_ID",
        "owner_line_user_id": "OWNER_LINE_USER_ID",
        "telegram_bot_token": "TELEGRAM_BOT_TOKEN",
        "telegram_chat_id": "TELEGRAM_CHAT_ID",
    }

    def require(self, *keys):
        """缺哪個必填欄位就丟出清楚的錯誤。憑證二擇一：檔案路徑或 inline JSON 任一即可。"""
        def ok(k):
            if k == "google_credentials":
                return bool(self.google_credentials or self.google_credentials_json)
            return bool(getattr(self, k))
        missing = [self.ENV_NAMES.get(k, k.upper()) for k in keys if not ok(k)]
        if missing:
            raise SystemExit(
                "缺少必要設定：" + "、".join(missing) +
                "\n請在 .env（參考 .env.example）或環境變數中設定。"
            )
