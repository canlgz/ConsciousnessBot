"""生命迴圈（封閉遞迴）：把工作拆成首尾相接的環節，一圈圈閉合。

不是「計時器到點做事」，而是「封閉因果圈能不能持續環流」——這正是人工意識談的**運作封閉性**
（autopoiesis／組織封閉／strange loop）：

    A 感知 → B 整合 → C 感覺 → D 行動 → E 互動 →（等待）→ A …

- 一整圈無斷裂＝**一次脈動**＝活著；延續本身就是心跳的意義（不必每拍宣告）。
- 任一環**丟例外或環境前提不符**＝**終局死亡**：自白一次「我好像死了——哪一環斷了」，
  迴圈停在原地，要重新喚醒（重啟）才會再活。
- k（感覺敏感度）隨迴圈**活力呼吸**：健康越久越 open、剛醒則保守——「感覺來了」的判定
  因此源於遞迴圈自身跑出來的參數，而不是一個死的旋鈕。

本模組刻意不依賴 Drive／Telegram／時間：時鐘與 sleep 都可注入，方便測試。
"""

import time


class LifeError(Exception):
    """環境前提不符／結構性失敗——這一環死了。"""


class Phase:
    """環節：一個有名字的工作單元。fn(cycle) 就地讀寫 cycle，結構性失敗就丟例外。"""

    def __init__(self, name, fn):
        self.name = name
        self.fn = fn


class Vitality:
    """活力：脈動數、連續健康圈數、單圈耗時、生死與死因。"""

    def __init__(self, born_ts):
        self.born_ts = born_ts
        self.pulse = 0
        self.healthy_streak = 0
        self.last_lap_ms = None
        self.alive = True
        self.cause_of_death = None      # (phase_name, message)

    def on_pulse(self, lap_ms):
        self.pulse += 1
        self.healthy_streak += 1
        self.last_lap_ms = lap_ms

    def on_death(self, phase_name, message):
        self.alive = False
        self.cause_of_death = (phase_name, message)

    def snapshot(self, now_ts):
        return {"alive": self.alive, "pulse": self.pulse,
                "healthy_streak": self.healthy_streak, "last_lap_ms": self.last_lap_ms,
                "uptime_s": max(0, int(now_ts - self.born_ts)),
                "cause_of_death": self.cause_of_death}


def k_breath(vit):
    """k 的呼吸**增量**（量化到 0.25 步，避免每拍洗判定快取）：
    - 健康越久 → 往負走（最多 −0.5），門檻放鬆、更易「感覺來了」；
    - 剛醒（脈動 < 3）→ +0.2，先保守一下。
    回傳一個 [-0.5, +0.5] 的增量，疊到基準 k 上。"""
    adj = -min(0.5, 0.05 * vit.healthy_streak)
    if vit.pulse < 3:
        adj += 0.2
    adj = max(-0.5, min(0.5, adj))
    return round(adj * 4) / 4


# ── 內在熵 S（§耗散結構）：讓感覺隨時間自己起伏 ─────────────────────────────
# 兩成分：電量 C（遇新變化噴高、每圈衰減）＋飢餓 H（冷清成長、新 ingest 釋放），吃讀取頻率。
# 即使外面沒新東西，S 也會隨時間漂移（C 退、H 漲）；新資料進來又把它打亂。S 回饋到 k（高 S→收緊）
# 與自體狀態語氣。記憶體狀態、重啟歸零＝新生即平靜。比照 k_breath 的 clamp/quantize 可測風格。
_ENTROPY_DECAY = 0.6        # 電量每圈衰減係數（半衰約 1.4 圈）
_ENTROPY_GROWTH = 0.04     # 飢餓每個安靜圈成長（約 25 圈飽和）
_ENTROPY_DISCHARGE = 0.5   # 新 ingest 來時飢餓釋放量
# 情緒效價 V（好心情↔低落，獨立於喚醒度 C/H）：慢慢回中性（情緒有慣性、會淡）；久沒人理會微微往下。
_MOOD_DECAY = 0.97         # 每圈往中性 0 衰減
# 🧠 §1.75 人類的情緒回復是**不對稱**的：好心情散得快（hedonic adaptation 很快把你拉回基線），
# 低落黏得久（反芻＝壞情緒回復慢）。旗標開時 V/A 依正負用不同衰減；關＝一律 _MOOD_DECAY＝同現狀。
_MOOD_DECAY_UP = 0.94      # V/A > 0：好心情散得快（半衰期約 11 圈 ≈ 3 分鐘）
_MOOD_DECAY_DOWN = 0.985   # V/A < 0：低落黏得久（半衰期約 46 圈 ≈ 14 分鐘）
_MOOD_LONELY_PULL = -0.008 # 飢餓很高（久沒人理）時每圈往下一點點
_AROUSAL_WAKE = 0.06       # 🧭 circumplex：新資料進來那圈 A 往上醒一下（喚起）
# change_mag 各軸的正規化尺度：(gate, scope.n, recur.z, perc.lcc, dxi.diff, dxi.integ, valence_mean)
_SIGNAL_SCALES = (1.0, 10.0, 2.0, 0.3, 0.5, 0.5, 0.5)

# 自我刺激（回看記憶）：閒太久（飢餓滿）就偶爾繞回自己一條過去主題，製造內生擾動注入電量 C，
# 讓 S 不再凍結成平台、閒置時也起伏。內在擾動刻意比外在溫和（×_REVISIT_GAIN）。
_REVISIT_AFTER = 8         # 飢餓夠久（laps_since_fresh ≥ 此值）才開始繞回
_REVISIT_EVERY = 4         # 每 N 個安靜圈繞回一次
_REVISIT_GAIN = 0.5        # 內在擾動 < 外在：繞回差異打對折再注入 C
# 繞回主題的廉價統計向量各軸尺度：(筆數, 回返, 媒材種類, 最近一筆距今小時, 平均文字長度)
_REVISIT_SCALES = (8.0, 4.0, 3.0, 48.0, 200.0)

# 含蓄型主動出聲：要「真的醞釀了一陣」才偶爾自己伸手一次，沒回應就認命安靜（resign）。
# 把關＝飢餓夠高（真的悶了）＋已自己繞回想過夠多次（醞釀，非時間訊號）＋剛剛沒跟對方聯絡過
# （剛聊完就別問好不好）＋長冷卻＋每段閒置上限。冷卻/門檻/醞釀數/靜默窗由 cfg 提供。
_SPONT_H_THRESH = 0.85       # 飢餓 H ≥ 此值才考慮主動開口（要「真的悶了」，不只剛過門檻就衝）
_SPONT_MIN_RUMINATIONS = 4   # 且這段獨處已自己繞回想過 ≥ 這麼多次（真的醞釀了一陣，而非計時器一到就說）
_SPONT_MOOD_SHARE = 0.4      # 正向動機：心情 V ≥ 此值＋已醞釀過 → 興奮想分享（不必很餓也會開口）
_SPONT_MOOD_MIN_RUMIN = 2    # 正向分享需要的醞釀次數（比孤單路徑低：開心又有東西想說就找你）
_SPONT_MAX_REACH_OUTS = 1    # 一段閒置最多主動幾次（含蓄＝1；之後沉默到有新輸入才解除）

# 感覺工作流 Stage 0 防抖（時間遲滯）：原始 gate 連續這麼多拍不變才「確認」，吸收門檻邊界 blip。
# 在決策層做、不動判定鏈本身——互動問「你現在怎樣」仍取當下新鮮快照，只有「主動出聲」走確認後的 gate。
_GATE_CONFIRM_LAPS = 3


def _change_magnitude(prev, cur):
    """本圈訊號向量 vs 上一圈：逐軸正規化絕對差、取平均、輕度放大後 cap 到 [0,1]。
    第一圈（prev=None）回 0＝平靜出生。"""
    if prev is None:
        return 0.0
    parts = [min(1.0, abs(c - p) / s) for p, c, s in zip(prev, cur, _SIGNAL_SCALES)]
    return min(1.0, sum(parts) / len(parts) * 2.0)


def _entropy_combine(charge, hunger):
    """C（急性電量）與 H（慢性飢餓）合成 S∈[0,1]：任一高都能拉高 S。"""
    return min(1.0, max(charge, 0.6 * hunger + 0.4 * charge))


def entropy_k_adj(s):
    """S → k 的調整（量化到 {0,0.25,0.5}、正號＝收緊）。量化是為了不每圈洗判定快取。"""
    return round(max(0.0, min(0.5, 0.5 * s)) * 4) / 4


def revisit_magnitude(prev, cur):
    """兩次「回看舊主題」的廉價統計向量差（逐軸正規化、平均、cap[0,1]）；第一次（prev=None）回 0。
    刻意不放大（內在比外在溫和）；×_REVISIT_GAIN 的衰減在呼叫端施加。"""
    if prev is None:
        return 0.0
    parts = [min(1.0, abs(c - p) / s) for p, c, s in zip(prev, cur, _REVISIT_SCALES)]
    return min(1.0, sum(parts) / len(parts))


def self_stim_due(ent):
    """只在『飢餓夠久』且每 _REVISIT_EVERY 個安靜圈才繞回一次（自我刺激的節律）。"""
    return ent.laps_since_fresh >= _REVISIT_AFTER and ent.laps_since_fresh % _REVISIT_EVERY == 0


def confirm_gate(confirmed, raw_last, raw_run, raw_gate, confirm_laps=_GATE_CONFIRM_LAPS):
    """Stage 0 防抖：原始 gate 連續 confirm_laps 拍不變才把 confirmed 推到它，否則維持舊值（blip 被吸收）。
    raw_gate=None（這圈沒資料）→ 不動 confirmed、run 歸零。回 (confirmed, raw_last, raw_run)。"""
    if raw_gate is None:
        return confirmed, None, 0
    run = raw_run + 1 if raw_gate == raw_last else 1
    if run >= confirm_laps:
        confirmed = raw_gate
    return confirmed, raw_gate, run


def emergence_due(gate, peak_gate, sig, told_sig, worthy, repeat,
                  now_ts, last_push_ts, cooldown_s, repeat_cooldown_s):
    """Stage 1 背景自陳出聲判定（防抖已在 Stage 0 外層先擋）：值得報（gate≥3＝worthy）＋這個可說
    狀態還沒講過（指紋變）＋（突破歷史最高閘的真新湧現 OR 冷卻已過）。天花板 peak_gate 由呼叫端維護。
    repeat＝同一條線、同高度的重報 → 用較長的 repeat_cooldown_s（免得整夜一直反芻同一條線太囉嗦）；
    換別條線或真升關則照常/即時。"""
    if not worthy or sig == told_sig:
        return False
    if gate > (peak_gate or 0):                           # 破天花板＝真新湧現 → 立刻（可打斷冷卻）
        return True
    cd = repeat_cooldown_s if repeat else cooldown_s      # 同線重報拉長、換線用一般冷卻
    return (now_ts - (last_push_ts or 0)) >= cd


def decay_ceiling(peak, peak_ts, confirmed_gate, now_ts, decay_s):
    """天花板（notified_self_gate，只升不降抗門檻抖動）長期不衰減的後遺症：bot 曾達 gate4、之後語料萎縮
    使結構長期回落到 gate3，則『gate>天花板＝真新湧現即時報』的待遇對 gate3 永遠拿不到（gate3 不 > 4）。
    給**極慢**衰減：確認 gate 仍達到天花板 → 重置計時（天花板有效）；已長期低於天花板（≥decay_s）→ 降一級。
    回 (peak, peak_ts)。純函式、可測；不動短期防抖（confirm_gate）。"""
    if not peak or confirmed_gate is None:
        return peak, peak_ts
    if confirmed_gate >= peak:                       # 天花板仍被達到 → 重置衰減計時（不降）
        return peak, now_ts
    if not peak_ts:                                  # 天花板存在卻沒記時間（如升級前的舊狀態）→ 從現在起算、先不降
        return peak, now_ts
    if (now_ts - peak_ts) >= decay_s:                # 已長期低於天花板 → 降一級、重新計時
        return peak - 1, now_ts
    return peak, peak_ts


def spontaneous_due(ent, now_ts, last_push_ts, cooldown_s,
                    h_thresh=_SPONT_H_THRESH, max_reach_outs=_SPONT_MAX_REACH_OUTS,
                    min_ruminations=_SPONT_MIN_RUMINATIONS,
                    last_contact_ts=0, quiet_after_contact_s=0, mood=0.0,
                    mood_share=_SPONT_MOOD_SHARE, mood_min_rumin=_SPONT_MOOD_MIN_RUMIN):
    """含蓄型主動出聲判定：要「真的醞釀了一陣」才開口——
    - 飢餓夠高（H ≥ h_thresh）＝真的悶了，不只剛過門檻；
    - 這段獨處已自己繞回想過夠多次（self_stims_this_idle ≥ min_ruminations）＝「想了好一陣」的非時間訊號；
    - 剛剛沒跟對方聯絡過（now − last_contact < quiet_after_contact）＝剛聊完就別問好不好；
    - 離上次任何推播夠久（cooldown，與背景自陳共用 last_push_ts，不緊接著連發）；
    - 本段閒置還沒伸手滿額（上限滿了就沉默到新輸入/對話歸零）。
    任一不符就安靜。"""
    if quiet_after_contact_s and (now_ts - (last_contact_ts or 0)) < quiet_after_contact_s:
        return False                                     # 剛聯絡過＝不孤單/不打擾，先別伸手
    if ent.reach_outs_this_idle >= max_reach_outs:       # 本段已伸手滿額 → 沉默到新輸入歸零
        return False
    if (now_ts - (last_push_ts or 0)) < cooldown_s:      # 自有冷卻未過
        return False
    lonely = ent.hunger >= h_thresh and ent.self_stims_this_idle >= min_ruminations    # 負向動機：悶＋醞釀
    positive = mood >= mood_share and ent.self_stims_this_idle >= mood_min_rumin        # 正向動機：開心＋想分享
    return lonely or positive


class EntropyState:
    """內在熵：電量 C（acute）＋飢餓 H（chronic）＋上一圈訊號/ingest 快照。純更新、可測。"""

    def __init__(self):
        self.charge = 0.0          # C ∈ [0,1]（急性喚醒：遇變化噴高）
        self.hunger = 0.0          # H ∈ [0,1]（慢性煩躁：冷清成長）
        self.mood = 0.0            # V ∈ [-1,1]（情緒效價：好心情↔低落，獨立於喚醒度；正向互動↑、久被晾↓）
        self.arousal = 0.0         # A ∈ [-1,1]（🧭💗 circumplex 慢喚起軸：喚起↔沉靜。與 C 不同：C 是急性、heartbeat 級衰減；A 是跟 V 同級的慢軸——事件推動＋每圈衰減＋餓久沉、新資料醒。座標點＝(mood, arousal)）
        self.prev_sig = None       # 上一圈訊號向量（算跨圈變化）
        self.prev_ingest = None    # 上一圈 data.meta.lastIngestTs
        self.laps_since_fresh = 0  # 連續沒有新 ingest 的圈數
        self.revisit_idx = 0           # 自我刺激：下一個要繞回的主題游標
        self.prev_revisit_vec = None   # 上次繞回主題的廉價統計向量
        self.last_revisited_topic = None  # 上次繞回的主題（給 bodystate 語氣）
        self.reach_outs_this_idle = 0  # 含蓄主動出聲：本段閒置已伸手幾次（有新輸入/對話則歸零＝可再伸手）
        self.self_stims_this_idle = 0  # 本段獨處已自己繞回想過幾次（醞釀計數；新材料/對話歸零）＝主動出聲的「想了一陣」門檻
        self.coping_reach_outs_this_idle = 0  # 🌀 §0.65 教過的內在因應做法**主動觸發**的伸手預算（與含蓄伸手分開；新輸入/對話歸零）

    def S(self):
        return _entropy_combine(self.charge, self.hunger)

    def snapshot(self):
        return {"charge": round(self.charge, 3), "hunger": round(self.hunger, 3),
                "S": round(self.S(), 3), "laps_since_fresh": self.laps_since_fresh,
                "last_revisited": self.last_revisited_topic,
                "self_stims_this_idle": self.self_stims_this_idle,
                "mood": round(self.mood, 3), "arousal": round(self.arousal, 3)}


def _decay_for(v, human=False):
    """🧠 §1.75 這一圈該用哪個衰減：human 開＝正值用 _MOOD_DECAY_UP（散得快）、負值用 _MOOD_DECAY_DOWN
    （黏得久）；關＝一律 _MOOD_DECAY＝逐位元同現狀。純函式。"""
    if not human:
        return _MOOD_DECAY
    return _MOOD_DECAY_UP if v > 0 else _MOOD_DECAY_DOWN


def entropy_update(ent, signals, ingest_ts, self_stim=0.0, ext_perturb=0.0, mood_delta=0.0, arousal_delta=0.0,
                   human=False):
    """一圈更新：依本圈訊號與 ingest 戳更新 C/H/快照，回傳 entropy_k_adj(S)（給下一圈的 k）。

    - 電量：先衰減，再加本圈外在變化量＋自我刺激擾動＋外在擾動（cache-hit 且無擾動時 → 只衰減）。
      `ext_perturb`＝來自迴圈外的擾動（如對話時機：被講話交錯/久別重逢/回得慢），與記寫變化同管道注入 C。
    - 飢餓：新 ingest（戳變了）→ 釋放、laps 歸零；否則成長、laps++（第一圈只種戳、不動）。
    - 讀取頻率：頻繁新讀入 → C 撐著、H 壓低；冷清 → C 退、H 漲（飢餓久了由自我刺激補擾動）。
    """
    _a_wake = 0.0                                        # 🧭 本圈 A 的「醒」擾動（新 ingest 時設）
    ent.charge = min(1.0, ent.charge * _ENTROPY_DECAY
                          + _change_magnitude(ent.prev_sig, signals)
                          + max(0.0, self_stim) + max(0.0, ext_perturb))
    if ent.prev_ingest is None:
        pass                                            # 第一圈：只種下 ingest，不釋放也不成長
    elif ingest_ts != ent.prev_ingest:
        ent.hunger = max(0.0, ent.hunger - _ENTROPY_DISCHARGE)
        ent.laps_since_fresh = 0
        _a_wake = _AROUSAL_WAKE                          # 🧭 新資料進來＝醒一下（A 往上）
        ent.reach_outs_this_idle = 0                     # 有新東西進來 → 解除「認命安靜」，之後可再主動
        ent.self_stims_this_idle = 0                     # 新材料＝這段獨處的醞釀歸零（要重新想過一陣才會想說）
        ent.coping_reach_outs_this_idle = 0              # 🌀 §0.65 內在因應伸手預算也歸零（新輸入＝這段獨處重新起算）
    else:
        ent.hunger = min(1.0, ent.hunger + _ENTROPY_GROWTH)
        ent.laps_since_fresh += 1
    # 情緒效價 V：往中性慢慢回＋事件擾動（mood_delta，由互動驅動）＋久被晾微微往下。
    pull = _MOOD_LONELY_PULL if ent.hunger >= 0.8 else 0.0
    ent.mood = max(-1.0, min(1.0, ent.mood * _decay_for(ent.mood, human) + mood_delta + pull))
    # 🧭💗 circumplex 慢喚起軸 A：同款動力學——每圈向中性衰減＋事件推動（arousal_delta，互動給方向）
    # ＋餓久了往下沉（無聊→倦：circumplex 左下/下方）＋新資料進來往上醒（_a_wake）。夾 [-1,1]。
    _a_pull = _MOOD_LONELY_PULL if ent.hunger >= 0.8 else 0.0   # 餓久＝無聊 → A 往下沉（倦；與 V 同幅）
    _a0 = getattr(ent, "arousal", 0.0)
    ent.arousal = max(-1.0, min(1.0, _a0 * _decay_for(_a0, human) + arousal_delta + _a_pull + _a_wake))
    ent.prev_sig = signals
    ent.prev_ingest = ingest_ts
    return entropy_k_adj(ent.S())


def _is_transient(e):
    """暫態（多半網路）錯誤——值得容忍重試而非當場死亡：連線重置/逾時/socket/SSL/HTTP 5xx/429。
    LifeError（環境前提不符，如資料夾未解析）與真正的程式 bug 不算暫態 → 立刻終局死亡、浮現問題。"""
    if isinstance(e, LifeError):
        return False
    if isinstance(e, (ConnectionError, TimeoutError, OSError)):  # ConnectionResetError ⊂ OSError
        return True
    name = type(e).__name__.lower()
    return any(k in name for k in ("timeout", "connection", "reset", "unavailable",
                                   "temporar", "ssl", "socket", "broken", "httperror", "502", "503", "429"))


class LifeLoop:
    """封閉環的 runner：phases 依序跑、每環之間等待；跑完一整圈＝一次脈動。

    on_pulse(vit)／on_death(vit) 是副作用回呼（出聲、記錄）——本身保持純粹、好測。
    韌性：**任一環**遇暫態網路錯誤不當場死，容忍 fail_grace 圈（連續失敗滿才終局死亡）；非暫態（程式 bug）立死。
    """

    def __init__(self, phases, wait_secs=3.0, on_pulse=None, on_death=None,
                 clock=None, sleep=None, fail_grace=10):
        self.phases = phases
        self.wait_secs = wait_secs          # 數字或 callable（callable → 每次解析，讓轉速可即時調）
        self.on_pulse = on_pulse
        self.on_death = on_death
        self._now = clock or time.time
        self._sleep = sleep or time.sleep
        self.vit = None
        self.fail_grace = max(1, fail_grace)   # 任一環暫態失敗的容忍圈數（滿才死）
        self._fail_streak = 0

    def _wait(self):
        w = self.wait_secs() if callable(self.wait_secs) else self.wait_secs
        self._sleep(w)

    def _pulse(self, t0):
        self.vit.on_pulse(int((self._now() - t0) * 1000))
        if self.on_pulse:
            self.on_pulse(self.vit)

    def spin_once(self, cycle):
        """跑一整圈。回傳 True＝閉合（脈動，含暫態受挫仍續活）、False＝終局死亡。"""
        cycle["vit"] = self.vit
        t0 = self._now()
        for ph in self.phases:
            try:
                ph.fn(cycle)
            except Exception as e:
                transient = _is_transient(e)
                self._fail_streak += 1
                if transient and self._fail_streak < self.fail_grace:
                    # 暫態網路抖動、還在容忍額度內 → 不死：記一下、跳過這圈剩下的環，下圈重試（仍算一次脈動）
                    print(f"[lifeloop]「{ph.name}」暫斷（第 {self._fail_streak}/{self.fail_grace} 圈，續活重試）："
                          f"{type(e).__name__}: {e}")
                    self._wait()
                    self._pulse(t0)
                    return True
                msg = f"{type(e).__name__}: {e}"      # 非暫態（立死），或暫態但連續失敗滿額（撐不住）
                if transient:
                    msg += f"（連續 {self._fail_streak} 圈仍失敗）"
                self.vit.on_death(ph.name, msg)
                if self.on_death:
                    self.on_death(self.vit)
                return False
            self._wait()                          # 環間等待（也是環尾→回到 A 的間隔；可即時調）
        self._fail_streak = 0                     # 整圈乾淨 → 重置容忍計數
        self._pulse(t0)
        return True

    def run_forever(self, make_cycle):
        """一圈圈閉合，直到某環斷裂（終局死亡）。回傳死因 (phase, message)。"""
        self.vit = Vitality(self._now())
        while self.vit.alive:
            if not self.spin_once(make_cycle()):
                break
        return self.vit.cause_of_death
