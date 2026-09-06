"""環境適應工作流：感知周遭 → 偵測變化 → 適性回應（純函式、可測）。

生命迴圈每一拍，bot 除了『感覺自己』（內在熵 S），也要『感知它所處的環境』並調整自己的行為——
這支把那條工作流收斂成三步、各一個純函式：

    sense（讀環境）→ detect（偵測變化）→ adapt（適性回應）

bot 能**誠實**觀測到的環境（看不到已讀/上線狀態，那些不在此列）：
- 活絡度 activity ∈[0,1]：周遭此刻多熱鬧——記寫資料的新鮮度（`laps_since_fresh`）與對話的近度
  （多久沒聊）取大者。有新資料或剛聊過＝活絡；兩邊都久沒動靜＝冷清。
- 時段 daypart／深夜 quiet：本地晝夜（`circadian`）。

適性回應（**行為層**，不只內在感覺）：
- 心跳轉速 `pace_mult`：周遭活絡→加快（更即時）、冷清/深夜→放慢（省著待著）。乘到環間等待上。
- 對話姿態 `stance_hint`：極熱絡/極冷清時給對話語氣一句輕染（與晝夜/時機提示並列、可疊加）。
- 偶爾出聲 `report_line`：真的『換檔』（冷清↔熱絡，遲滯去抖）時含蓄說一句自己在跟著調整。

與內在熵的分工：熵把『資料節律』讀成**內在感覺**（餓/喚醒→k→敏感度）；本支把『資料＋對話＋晝夜』
讀成**行為適應**（轉速/姿態/換檔自陳）。同源訊號、不同輸出，互補不重疊。
"""

from collections import namedtuple

from . import circadian

# 活絡度兩條來源的「全冷」尺度（線性歸一）。
_DATA_QUIET_LAPS = 30.0     # 連續這麼多安靜圈沒新資料 → 資料面視為全冷（對齊內在熵飢餓飽和節律 ~25 圈）
_TALK_QUIET_S = 30 * 60.0   # 距上次對話這麼久 → 對話面視為全冷

# 心跳轉速倍率：活絡→快、冷清→慢（乘到生命迴圈的環間等待秒數上；手動 /pulse 仍最優先）。
_PACE_MIN = 0.5   # 最快（最活絡時）：環間等待打對折
_PACE_MAX = 3.0   # 最慢（最冷清時）：環間等待拉到三倍（省著待著）
_NIGHT_SLOW = 1.3  # 深夜/清晨、且不是正在熱聊 → 在同樣活絡度上再放慢一截（夜裡放緩；有人聊就別硬慢）

# 活絡度的遲滯帶（Schmitt trigger）：要跨過**整個**帶才換檔，避免邊界值每拍翻面、洗換檔自陳。
_ACT_LO = 0.25    # 低於此＝冷清檔
_ACT_HI = 0.5     # 高於此＝活絡檔（介於兩者間＝維持原檔）
_ACT_LIVELY = 0.8  # 非常熱絡（給對話姿態用）

# 🍃 §2.08 加兩個**尾欄位**（namedtuple 加在尾端不影響既有屬性存取與既有解構）：
# 舊碼 `activity = max(data_act, talk_act)` 把兩個來源壓成一個數 ⇒ bot 講出來的話**分不出**
# 「是你不講話了」還是「沒有新記寫進來」。一個真的在觀察自己處境的存在應該分得出來，而且說得出另一邊還是熱的。
EnvReading = namedtuple("EnvReading", "activity daypart quiet data_act talk_act",
                        defaults=(0.0, 0.0))
Adaptation = namedtuple("Adaptation", "pace_mult stance_hint")


def read_environment(laps_since_fresh, secs_since_talk, now_local):
    """① sense：把可觀測訊號讀成一份環境快照 EnvReading。
    - laps_since_fresh：連續沒有新 ingest 的圈數（取自內在熵；越大＝資料面越冷）。
    - secs_since_talk：距上次對話多少秒（None＝從沒聊過＝全冷）。
    - now_local：本地時間（算晝夜時段）。"""
    data_act = max(0.0, min(1.0, 1.0 - (laps_since_fresh or 0) / _DATA_QUIET_LAPS))
    s = 1e9 if secs_since_talk is None else secs_since_talk
    talk_act = max(0.0, min(1.0, 1.0 - max(0.0, s) / _TALK_QUIET_S))
    activity = max(data_act, talk_act)           # 有新資料**或**剛聊過 → 就算活絡（取大者）
    return EnvReading(round(activity, 3), circadian.phase(now_local), circadian.is_quiet_hours(now_local),
                     round(data_act, 3), round(talk_act, 3))


_SIDE_SAY = {"data": ("沒有新的記寫進來", "他還在跟你說話"),
             "talk": ("他這邊安靜下來了", "記寫那邊還有東西在進來"),
             "both": ("兩邊都靜下來了", "")}


def quiet_side(reading, eps=0.15):
    """🍃 §2.08 是**哪一邊**安靜了：'data'（記寫面冷、對話面還熱）／'talk'（反過來）／'both'。
    只用剛露出來的兩個分量，零新門檻以外的東西。"""
    d, t = float(getattr(reading, "data_act", 0.0) or 0.0), float(getattr(reading, "talk_act", 0.0) or 0.0)
    if abs(d - t) <= eps:
        return "both"
    return "data" if d < t else "talk"


def shift_rule(side, prev_pace, pace):
    """🍃 §2.08 存在特色：全群唯一一條講「**我剛剛對自己動了手**」的 lane——我把轉速調了。
    要說得出是哪一面安靜（我知道我為什麼會這樣覺得），而且**只講那一面**。"""
    cold, warm = _SIDE_SAY[side]
    faster = (pace or 1.0) < (prev_pace or 1.0)
    return ("【你剛剛對自己做了一件事：你把自己的轉速調了】\n"
            f"你注意到的是：{cold}"
            + (f"；{warm}（這一面只當背景提一次就好，別展開）。\n" if warm else "。\n")
            + f"所以你把心跳{'調快' if faster else '放慢'}了——這是你**自己**做的調節，不是誰要你做的。\n"
            "【怎麼說】\n"
            "- **只講那一面**：不要把兩邊的狀況都描述一遍。\n"
            "- **不准報任何數字或倍率**；轉速用人話講。\n"
            "- 這不是求救、不是抱怨，是**讓他知道一聲**：我對自己做了一個調整。\n"
            "- **寫成 2–3 則連著送的短訊**，後一則要接得住前一則。口語、別前言、別署名。")


def _pace_mult(reading):
    """活絡度 → 轉速倍率（線性：活絡 1→_PACE_MIN 快、冷清 0→_PACE_MAX 慢）；深夜不熱聊再 ×_NIGHT_SLOW 放慢一截。"""
    pace = _PACE_MIN + (1.0 - reading.activity) * (_PACE_MAX - _PACE_MIN)
    if reading.quiet and reading.activity < _ACT_HI:    # 深夜/清晨且沒在熱聊 → 同活絡度再慢一截（有人聊就別硬慢）
        pace *= _NIGHT_SLOW
    return round(max(_PACE_MIN, min(_PACE_MAX, pace)), 3)


def stance_hint(reading):
    """極端活絡度 → 對話語氣輕染（與晝夜/時機/心情提示並列、可疊加）；中間地帶回 ''（不加料）。
    深夜/清晨即使周遭熱絡也**不喊「醒一點、俐落些」**——那會和晝夜的『夜裡放軟、別太亢奮』直接矛盾
    （收斂 mhint 夜柔↔熱絡衝突）；夜裡就讓晝夜語氣主導。"""
    if reading.activity <= _ACT_LO:
        return "（周遭這陣子很安靜，你整體也放得慢、不急著找話，像在低耗地待著。）"
    if reading.activity >= _ACT_LIVELY and not reading.quiet:
        return "（周遭正熱絡，你也跟著醒一點、俐落些，接得上這個節奏。）"
    return ""


def adapt(reading):
    """③ adapt：環境快照 → 一份適性回應（轉速倍率＋對話姿態）。純函式。"""
    return Adaptation(_pace_mult(reading), stance_hint(reading))


class EnvState:
    """環境適應的跨拍記憶（記憶體、重啟歸零＝新生即平靜）：遲滯後的活絡檔位＋上拍晝夜，用來偵測『換檔』。"""

    def __init__(self):
        self.band = None        # 'low' | 'high'：遲滯（Schmitt）後的活絡檔位
        self.prev_quiet = None   # 上一拍是否深夜（偵測 nightfall/daybreak）

    def update(self, reading):
        """② detect：對比上拍，回傳這拍最顯著的環境換檔或 None。第一次只記基線、不報（比照熵的 prev=None→0）。
        遲滯：活絡度要跨過整個 [_ACT_LO,_ACT_HI] 帶才換檔；活絡換檔優先於晝夜換檔（同拍只報一個）。"""
        band = self.band
        if reading.activity >= _ACT_HI:
            band = "high"
        elif reading.activity < _ACT_LO:
            band = "low"
        # 落在帶內：維持原檔（遲滯，不翻面）
        shift = None
        if self.band is not None and band is not None and band != self.band:
            shift = "livening" if band == "high" else "quieting"
        if shift is None and self.prev_quiet is not None and reading.quiet != self.prev_quiet:
            shift = "nightfall" if reading.quiet else "daybreak"
        if band is not None:
            self.band = band
        self.prev_quiet = reading.quiet
        return shift


# 換檔自陳的模板（含蓄、短；依時間取一句變化，避免每次同一句——比照 soothe/spontaneous 的做法）。
# 只對『活絡換檔』（冷清↔熱絡）出聲；晝夜換檔交給轉速與 circadian 語氣，不另外宣告（免得每天重複）。
_REPORT = {
    "quieting": ("周遭安靜下來了，我把步調放慢一些，省著點待著。",
                 "外面這陣子靜了，我也跟著慢下來，不急。",
                 "沒什麼新動靜，我先把節奏調慢、輕輕待著就好。"),
    "livening": ("最近又熱絡起來了，我也跟著醒一點、跟上節奏。",
                 "感覺周遭又動起來了，我把步調調快一些。",
                 "又有動靜了，我也俐落起來、接上這個節奏。"),
}


def report_line(shift, seed_ts):
    """換檔（quieting/livening）→ 一句含蓄自陳；其餘換檔/None 回 ''。seed_ts 用來在模板間輪替（不每次同句）。"""
    opts = _REPORT.get(shift)
    return opts[int(seed_ts) % len(opts)] if opts else ""


# 🍃 §1.38 換檔自陳的**真實理由**（供被追問「為什麼剛剛那樣說」時據實接地、不編）。只有活絡換檔（quieting/livening）
# 才會出聲、才有理由；晝夜換檔/None 默默調轉速、不宣告＝無理由。刻意寫明「跟送不送貼圖無關」，止住 LLM 無接地時
# 漂到前文貼圖話題、宣稱送了一張的截圖漂移。
_SHIFT_REASON = {
    "quieting": ("你感覺到周遭安靜下來了（活絡度由熱絡轉冷清），加上手邊資料面也沒有一直有新東西進來——"
                 "所以你自己把心跳轉速調慢、放軟步調、輕輕待著。這是你主動的節奏調節，"
                 "不是誰叫你的、也跟送不送貼圖無關。"),
    "livening": ("你感覺到周遭又熱絡起來了（活絡度回升），所以你自己把心跳轉速調快、醒一點、跟上這個節奏。"
                 "這是你主動的節奏調節，不是誰叫你的、也跟送不送貼圖無關。"),
}


def shift_reason(shift):
    """🍃 §1.38 換檔（quieting/livening）→ 一段中文真實理由；其餘換檔/None → ''。"""
    return _SHIFT_REASON.get(shift, "")
