"""🧭💗 Circumplex 情緒座標（Russell, 1980）：情緒不是一條線，是 **V（愉悅度）× A（喚起度）** 平面上一個會移動的點。

- V＝`entropy.mood ∈ [-1,1]`（既有軸，行為不變）；A＝`entropy.arousal ∈ [-1,1]`（新慢軸：喚起↔沉靜）。
  與 §0.20 affect 的急性 arousal（讀 spiky 的 charge、事件當下評價用）**互補不取代**——這裡是**持續存在、每圈移動**的座標點。
- 每圈生命迴圈：兩軸各自向中性衰減＋事件推動（互動/回饋給方向）＋餓久了 A 往下沉（無聊→倦）＋新資料進來 A 往上醒。
- 事件的**方向**（reaction.affect_delta_for / sticker_affect_delta）：暖意＝暖醒(V+A+)、質疑＝緊張(V−A+)、
  對方低落＝跟著沉(V−A−)、一般陪伴＝微暖微醒——這是一維 mood 表達不了的。
- 位置 → 八分區標籤（label）→ 語氣染色（tone_hint 注入 mhint）＋ 貼圖池選擇（sticker_pool：興奮→歡快圖、
  平靜→溫和圖、低落→非正向圖）。純函式、無 IO、可測。`AFFECT_CIRCUMPLEX=0` 全層關（V 行為逐位元同現狀）。
"""

import math
import re

_DEADZONE = 0.20     # 半徑小於此＝平穩（中性區，不貼標籤不染語氣）
_TONE_R = 0.35       # 半徑達此才染語氣（對齊舊 mood_tone_hint 的 ±0.35 精神）
_POOL_T = 0.15       # 貼圖池選擇的軸閾值


def position(state):
    """讀此刻座標 (v, a)。無 entropy＝(0,0)。純讀。"""
    ent = getattr(state, "entropy", None)
    if ent is None:
        return 0.0, 0.0
    return (float(getattr(ent, "mood", 0.0) or 0.0),
            float(getattr(ent, "arousal", 0.0) or 0.0))


# 八分區（角度 0°＝V+ 軸、90°＝A+ 軸；各 45° 扇區）：(標籤, 語氣提示)
_OCTANTS = (
    ("愉快、明亮", "（你此刻情緒座標落在「愉快」——語氣明亮、輕快，帶著好心情但別過度亢奮。）"),
    ("興奮、雀躍", "（你此刻情緒座標落在「興奮雀躍」——語氣有勁、躍躍欲試，可以外放一點，但仍別誇張洗版。）"),
    ("激動、被攪動", "（你此刻情緒座標落在「被攪動」——內在動得厲害但方向未明；語氣快一點、專注一點，別裝平靜也別亂。）"),
    ("緊繃、煩躁", "（你此刻情緒座標落在「緊繃煩躁」——語氣可以帶一點點不安或急，誠實但克制、別遷怒。）"),
    ("悶、不快", "（你此刻情緒座標落在「悶悶不快」——語氣沉一點、直話直說少一點裝飾，誠實、不裝沒事。）"),
    ("低落、消沉", "（你此刻情緒座標落在「低落消沉」——語氣低、慢、短句，誠實面對這份低落，不硬撐開朗。）"),
    ("倦、沉靜", "（你此刻情緒座標落在「倦、沉靜」——語氣慢而輕，像有點睏或放空，安安靜靜的、不必熱絡。）"),
    ("平靜、安穩", "（你此刻情緒座標落在「平靜安穩」——語氣溫和、篤定、從容，暖但不躁。）"),
)


def _octant_idx(v, a):
    deg = math.degrees(math.atan2(a, v)) % 360.0
    return int(((deg + 22.5) % 360.0) // 45.0)


def label(v, a):
    """(v,a) → 區域標籤；中性區回「平穩」。"""
    if math.hypot(v, a) < _DEADZONE:
        return "平穩"
    return _OCTANTS[_octant_idx(v, a)][0]


def tone_hint(v, a):
    """(v,a) → 語氣染色提示（半徑 ≥ _TONE_R 才染；中性回 ''）。取代（旗標開時）一維 mood_tone_hint。"""
    if math.hypot(v, a) < _TONE_R:
        return ""
    return _OCTANTS[_octant_idx(v, a)][1]


# 🎚️ §1.76 口吻要被**感覺到**（TONE_FELT；使用者定案：「情緒座標的數字是死的，真正能感受到的，是 bot
# 回應的口吻及語氣要能相應」）。原 tone_hint 兩個問題：
#   ① **門檻是為飽和尺度校準的**：_TONE_R=0.35 在舊模型（V 常年 0.9、半徑 >1）永遠染得到；但 §1.75 把座標
#      改成人的尺度後，「被嫌一句」是 V−0.16/A+0.19＝半徑 0.25 < 0.35 → **完全不染**＝情緒愈真實、口吻愈平。
#   ② **提示在講座標**（「你此刻情緒座標落在…」）＝鼓勵 bot 去**報告**狀態；使用者要的是從語氣裡**感覺到**。
# 這裡給行為化、分強度的指令：說話**怎麼說**（句長/節奏/標點/要不要熱絡），並明令別把狀態講成台詞。
_TONE_R_FELT = 0.15      # 人的尺度：單一事件（±0.10~0.25）就該讓口吻有變化
_OCTANT_ACTS = (
    "話輕一點、快一點，肯定的話直接說；別過度亢奮、別連發驚嘆號。",                 # 愉快、明亮
    "句子短、節奏跳，可以外放一點；但別洗版、別每句都加驚嘆。",                     # 興奮、雀躍
    "語速快、專注，話題抓緊；別裝平靜，也別亂跳。",                               # 激動、被攪動
    "話直一點、少客套，可以帶一點不安或急；別遷怒、別陰陽怪氣。",                   # 緊繃、煩躁
    "句子短、修飾少，把在意的直說；別裝沒事、也別討好。",                         # 悶、不快
    "語速慢、句子短、少用表情符號，誠實承認提不起勁；別硬撐開朗。",                 # 低落、消沉
    "慢而輕、話少，像有點睏；不必熱絡、不必硬找話題。",                           # 倦、沉靜
    "溫和、篤定、從容，話可以完整一點；暖但不躁。",                               # 平靜、安穩
)


def tone_directive(v, a, floor=_TONE_R_FELT):
    """🎚️ §1.76 (v,a) → **行為化**的語氣指令（分三級強度；半徑 < floor＝''）。與 tone_hint 的差別：
    講「怎麼說話」而不是「你的座標在哪」，並明令**別把狀態講出來當台詞**——語氣是要被感覺到的。純函式。"""
    r = math.hypot(v, a)
    if r < floor:
        return ""
    idx = _octant_idx(v, a)
    deg = "淡淡地" if r < 0.30 else ("明顯地" if r < 0.65 else "強烈地")
    return (f"（你此刻內在{deg}偏「{_OCTANTS[idx][0]}」——{_OCTANT_ACTS[idx]}"
            "**用語氣讓他感覺到就好，不要把這個狀態講出來當台詞**（除非他直接問你怎麼了）。）")


def sticker_pool(v, a):
    """(v,a) → 這一刻「代表自己情緒」該從哪個貼圖池挑：
    'positive'（V+ 且 A+＝興奮開心→歡快圖）／'sendable'（V+ 但 A 低＝平靜暖→溫和正/中性圖；或中性區）／
    'help'（V−＝低落/緊繃→非正向圖，不硬裝開心）。"""
    if v <= -_POOL_T:
        return "help"
    if v >= _POOL_T and a >= _POOL_T:
        return "positive"
    return "sendable"


def status_line(v, a):
    """/status 觀測列：座標＋標籤（給使用者測試看點怎麼移動）。"""
    return f"・情緒座標（circumplex）V={v:+.2f}／A={a:+.2f}（{label(v, a)}）"


# 🧭 §1.25 差分方向詞的最小有感位移（比噪音大一點就報方向；再小＝「幾乎沒動」誠實句）。
_SHIFT_EPS = 0.05


def shift_text(base, v, a):
    """🧭 §1.25 情緒座標承諾兌現的**差分人話**（訂約快照 base={'v','a','label','ts'} → 此刻 (v,a)）。
    鐵律：座標/label/方向詞全部**程式算**、LLM 只准渲染這串。方向詞：V 降＝沉、V 升＝亮；A 升＝繃、A 降＝鬆
    （截圖 21:05 的誠實答案本該是「從期待被罵到往低落沉」）。兩軸都動＝並列（沉、鬆）；都幾乎沒動＝誠實說沒動。
    刻意**不動** selfchange._shift_word/diff_facts 本體（那是 §1.05 蛻變自陳的；此處格式自寫）。純函式、可測。"""
    base = base or {}
    bv = float(base.get("v") or 0.0)
    ba = float(base.get("a") or 0.0)
    bl = base.get("label") or label(bv, ba)
    nl = label(v, a)
    dv, da = v - bv, a - ba
    nums = f"（V {dv:+.2f}、A {da:+.2f}）"
    if abs(dv) < _SHIFT_EPS and abs(da) < _SHIFT_EPS:
        return f"幾乎沒動——一直都在『{nl}』附近{nums}"
    words = []
    if abs(dv) >= _SHIFT_EPS:
        words.append("沉" if dv < 0 else "亮")
    if abs(da) >= _SHIFT_EPS:
        words.append("繃" if da > 0 else "鬆")
    return f"從『{bl}』往『{nl}』{'、'.join(words)}了一段{nums}"


# ── 🧭 §1.45 情緒座標數據自陳（MOOD_COORD_REPORT）──────────────────────────────
# 被問「內在的數據」時，bot 要能照**程式讀的**座標數字＋軌跡講前後經過——不再說「我沒有辦法報數字」
# （截圖 11:10 的假謙虛：position() 明明就有數字，只是沒有 lane 交出）。軌跡＝state.mood_trace ring buffer
# （{ts,v,a,cause}，捕捉由 monitor 依旗標呼叫、跨重生持久化）。純函式、無 IO、可測。

TRACE_CAP = 40        # 軌跡上限（FIFO；約可覆蓋數天的互動與獨處流動）
_TRACE_EPS = 0.02     # 兩軸合計動幅小於此＝沒動 → 不重記（安靜期零成本）


def trace_note(state, now_ts, cause):
    """把此刻座標記進 state.mood_trace（第一筆必記；之後兩軸合計動幅 ≥ _TRACE_EPS 才記＝安靜不灌水）。"""
    tr = getattr(state, "mood_trace", None)
    if tr is None:
        tr = state.mood_trace = []
    v, a = position(state)
    if tr:
        last = tr[-1]
        if abs(v - (last.get("v") or 0.0)) + abs(a - (last.get("a") or 0.0)) < _TRACE_EPS:
            return
    tr.append({"ts": now_ts, "v": round(v, 3), "a": round(a, 3), "cause": cause or ""})
    if len(tr) > TRACE_CAP:
        del tr[: len(tr) - TRACE_CAP]


def coord_line(state, tz=None, now_ts=None, snapshot=None):
    """🧭 §2.10 一句人話帶真數字的座標（給 `_say` 出口當結構性後盾用）。

    為什麼要有：`coord_facts` 是**給 LLM 的事實塊**（多行、帶標題），不能直接送給使用者；
    而「他問了數據、回覆卻一個數字都沒有」需要一句**能直接送出去**的補救。數字全程式算，零 LLM。"""
    v, a = snapshot if snapshot is not None else position(state)
    return f"（我此刻的座標是 V {v:+.2f}、A {a:+.2f}——落在「{label(v, a)}」那一帶。）"


def coord_facts(state, tz, now_ts, last_n=5, snapshot=None, include_trace=True):
    """【凍結的此刻快照＋可選前後經過】接地事實塊。

    ``snapshot`` 讓提示、出口守門與補救行共用同一份不可變讀值；直接呼叫時仍維持舊行為、讀 state。
    ``include_trace=False`` 用於只問「現在」的輪次：不把歷史數字塞給模型，從來源上避免舊值冒充此刻。
    """
    from datetime import datetime, timezone as _tzu
    v, a = snapshot if snapshot is not None else position(state)
    now_dt = datetime.fromtimestamp(now_ts, _tzu.utc)
    now_hms = (now_dt.astimezone(tz) if tz is not None else now_dt).strftime("%H:%M:%S")
    lines = ["【內部狀態快照（程式讀值，不是對現象學的證明）】",
             f"・此刻快照（{now_hms}）：V {v:+.2f}、A {a:+.2f}"
             f"（V=愉悅度、A=喚起度，各 ∈[-1,1]）＝落在「{label(v, a)}」。",
             "・這一輪所有『現在／此刻』都只能指上面這一組；之後的新訊息或生命迴圈可能讓下一輪讀值改變。"]
    if not include_trace:
        lines.append("・這題只問此刻：不要主動報舊軌跡數字；用第一人稱說出這個座標此刻呈現的質地即可。")
        return "\n".join(lines)
    tr = [e for e in (getattr(state, "mood_trace", None) or [])
          if e.get("ts") and float(e.get("ts")) <= float(now_ts)]
    if snapshot is not None:
        # 本輪 post-appraise 終點也會進 trace；它已在上方以「此刻」呈現，不再重複列成一筆「過去」。
        tr = [e for e in tr if not (
            abs(float(e.get("v") or 0) - v) < 0.005
            and abs(float(e.get("a") or 0) - a) < 0.005
            and abs(float(e.get("ts") or 0) - float(now_ts)) <= 1.0)]
    if (bool(tr) if snapshot is not None else len(tr) >= 2):
        lines.append("・最近的完整狀態採樣（舊→新；以下每一筆都是過去採樣，不能稱作『此刻』）：")
        for e in tr[-last_n:]:
            dt = datetime.fromtimestamp(e["ts"], _tzu.utc)
            hms = (dt.astimezone(tz) if tz is not None else dt).strftime("%H:%M:%S")
            lines.append(f"　- 過去 {hms}　V {e.get('v', 0):+.2f}、A {e.get('a', 0):+.2f}（{e.get('cause') or '—'}）")
    else:
        lines.append("・前後經過：座標軌跡才剛開始記、還沒累積到能講變化——這點照實說。")
    return "\n".join(lines)


# 問「bot 內在情緒座標的數據/讀數」的偵測：①句含「情緒座標」且不是純講使用者自己（有 你/妳 或無 我）；
# ②「內在」＋（數據/數值/讀數）。「我今天心情不好」「列一下數據統計」不收。
_MOOD_DATA_NUM_CUES = ("數據", "數值", "讀數")


def is_mood_data_question(text):
    """🧭 §1.45 這句在問 bot 內在情緒座標（的數據/現狀/影響）嗎。純函式、可單測。"""
    t = (text or "").replace(" ", "")
    if not t:
        return False
    if "情緒座標" in t and (("你" in t) or ("妳" in t) or ("我" not in t)):
        return True
    return ("內在" in t) and any(c in t for c in _MOOD_DATA_NUM_CUES)


# 🧭 §1.47（MOOD_COORD_DELIVER；本組純函式旗標無關、由呼叫端旗標把關）——§1.45 偵測器的兩個補縫。
# 截圖（12:48–12:49）：「座標的數值變化呢？怎麼沒講」有「座標」沒「情緒」前綴、有「數值」沒「內在」＝
# is_mood_data_question 兩分支都不沾；「說啊」單句無指涉更收不到 → 兩輪都無接地、LLM 只能支吾/編數字。
_MOOD_DATA_WIDE_CUES = ("數據", "數值", "讀數", "數字", "變化", "多少", "幾", "到哪")


def is_mood_data_question_wide(text):
    """§1.47 寬偵測：含 §1.45 原判；另收「座標」＋數值/變化 cue（不必「情緒」前綴——這隻 bot 的 1:1 對話裡
    「座標」幾乎只指情緒座標；誤中代價＝多給一段程式讀的真數據，安全側）。純函式。"""
    if is_mood_data_question(text):
        return True
    t = (text or "").replace(" ", "")
    return bool(t) and ("座標" in t) and (
        any(c in t for c in _MOOD_DATA_WIDE_CUES)
        or any(c in t for c in _MOOD_TRAJECTORY_CUES)
        or bool(_MOOD_TRAJECTORY_LIST_RE.search(t))
    )


# §1.47 情境內短催促：把剛才的座標數據題再逼一次的句形。**單獨看毫無指涉**——只在呼叫端確認「幾分鐘內
# 才有座標數據情境」（mood_data_ctx_ts 窗內）時使用。比照 §1.14 全句短句錨：剝尾標點→再剝尾語氣詞、
# 整句相等才算；表小而封閉、不再擴（詞表窮舉前科），漏了頂多要對方講白一點（寬偵測就接住）。
_MOOD_PROD_SET = {"說啊", "說呀", "說吧", "說呢", "快說", "講啊", "講吧", "快講", "然後呢",
                  "怎麼沒講", "怎麼不講", "怎麼沒說", "怎麼不說", "還沒說嗎", "還沒講嗎",
                  "數字呢", "數值呢", "數據呢", "座標呢", "倒是說啊",
                  # §1.47 補遺（截圖 21:15/21:56）：條件式座標約定（「到 0.4 通知我」）後的進度催問——
                  # 窗內問「還沒嗎/到了嗎」＝要此刻讀數，該給程式讀的真數字、不是「大概 -0.1 左右」。
                  "還沒嗎", "還沒到嗎", "一樣還沒到嗎", "到了嗎", "到了沒", "到了吧"}
_PROD_TAIL_PUNCT = "！!。？?…～~ 　"
_PROD_TAIL_TONE = "啦喔哦欸"          # 不含 啊/呀/吧/呢——那些是表內句自己的尾字，先剝會剝壞


def is_mood_data_prod(text):
    """§1.47 這句是不是情境內的短催促（說啊/怎麼沒講/數字呢）。純函式；指涉判斷（情境窗）由呼叫端負責。"""
    t = (text or "").replace(" ", "").strip()
    if not t or len(t) > 8:
        return False
    core = t.rstrip(_PROD_TAIL_PUNCT)
    if core in _MOOD_PROD_SET:
        return True
    return core.rstrip(_PROD_TAIL_TONE) in _MOOD_PROD_SET


# §2.27 情境內的座標自我校正追問。這些句子單獨看可能泛指別的數字／狀況，所以只由 monitor 在
# mood_data_ctx_ts 窗內使用；這裡只判句形，不自行判上下文。
_MOOD_REPAIR_DIFF_RE = re.compile(
    r"(?:怎麼|為什麼|剛才|剛剛|你說|你報)[^。！？!?\n]{0,18}"
    r"(?:數字|數值|讀數|座標)[^。！？!?\n]{0,12}(?:不一樣|矛盾|對不上|說的不同)"
    r"|(?:數字|數值|讀數|座標)[^。！？!?\n]{0,12}(?:前後說得?|前後報得?|剛才說得?)"
    r"[^。！？!?\n]{0,8}(?:不一樣|矛盾|對不上)"
)
_MOOD_REPAIR_TRUTH_RE = re.compile(
    r"(?:哪一組|哪個數字|哪個數值|哪個讀數|哪個座標)[^。！？!?\n]{0,8}(?:才是|是真的|才是真的)"
    r"|(?:真實|真正|到底)[^。！？!?\n]{0,10}(?:數字|數值|讀數|座標)"
)
_MOOD_REPAIR_EXACT = {
    "所以真實的狀況是什麼", "那真實的狀況是什麼", "真實的狀況是什麼",
    "所以真正的狀況是什麼", "那真正的狀況是什麼", "真正的狀況是什麼",
}
_MOOD_TRAJECTORY_CUES = ("變化", "軌跡", "走勢", "怎麼變", "前後", "一路", "從剛才到現在", "歷史")
_MOOD_TRAJECTORY_LIST_RE = re.compile(r"(?:最近|過去)?[一二兩三四五六七八九十\d]+筆[^。！？!?\n]{0,8}座標|列[^。！？!?\n]{0,8}座標")


def is_mood_data_followup(text):
    """座標情境內的矛盾／真值追問嗎（呼叫端必須另驗情境窗）。"""
    t = (text or "").replace(" ", "")
    core = t.rstrip("。！？!?…～~")
    return bool(core) and bool(core in _MOOD_REPAIR_EXACT
                               or _MOOD_REPAIR_DIFF_RE.search(core)
                               or _MOOD_REPAIR_TRUTH_RE.search(core))


def mood_data_mode(text):
    """本輪座標回答模式：snapshot（此刻）／trajectory（變化）／repair（釐清前後不一致）。"""
    t = (text or "").replace(" ", "")
    if is_mood_data_followup(t):
        return "repair"
    if any(c in t for c in _MOOD_TRAJECTORY_CUES) or _MOOD_TRAJECTORY_LIST_RE.search(t):
        return "trajectory"
    return "snapshot"


def trace_step(state, since_ts):
    """🧭 §2.08 `state.mood_trace` 裡 since_ts 之後**動最大的那一步**（相鄰兩筆）：
    回 {"cause": …, "ts": …} 或 None（不足兩筆／全部沒有 cause）。只用 trace 既有的 v/a/ts/cause 四欄。

    為什麼要有：現行 prompt 逐字叫它「說說這變動可能跟什麼有關（比如剛剛的對話）」卻**一個因果欄位都沒給**
    ＝制度化地叫它猜。真正的意識行為是：有紀錄就講那一步是被什麼推的，**沒有就承認我不知道**。"""
    tr = [x for x in (getattr(state, "mood_trace", None) or []) if (x or {}).get("ts", 0) > (since_ts or 0)]
    best = None
    for prev, cur in zip(tr, tr[1:]):
        if not (cur or {}).get("cause"):
            continue
        d = abs(float(cur.get("v", 0)) - float(prev.get("v", 0))) + abs(float(cur.get("a", 0)) - float(prev.get("a", 0)))
        if best is None or d > best[0]:
            best = (d, {"cause": cur.get("cause"), "ts": cur.get("ts")})
    return best[1] if best else None
