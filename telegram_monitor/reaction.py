"""對話中的貼圖／表情＝對方此刻對『這則對話』的情緒訊號，餵進回應語調。

與記寫的「價性」分開：那是對**記寫內容**的情緒（進 Gate 4 意向判讀）；這是對**當下對話**的即時情緒
（只染回應語氣與一點內在擾動，不進判定鏈）。純函式、可測。
"""

import re

from . import stickervision

# 常見貼圖所帶的 emoji（Telegram sticker.emoji）粗分情緒向度。
_POS = set("🔥👍❤🧡💛💚💙💜🤍🩷🩵😍🥰😘😻🎉🎊🥳👏🙌🙏✨💯😁😄😆😂🤣😊🙂☺😺💪🆗✅")
_NEG = set("😢😭😞😔😟😡🤬👎💔😩😫😤😠🥀😣😖🥺😥")


def read_sticker(emoji):
    """貼圖 emoji → 情緒向度（positive/negative/neutral）。多碼位 emoji 取任一字元落在集合即算。"""
    e = emoji or ""
    if any(ch in _POS for ch in e):
        return "positive"
    if any(ch in _NEG for ch in e):
        return "negative"
    return "neutral"


# 收到貼圖後、染「下一則回覆」語氣的系統提示（短時間窗內有效；與對話時機 mood_hint 可疊加）。
_TONE_HINT = {
    "positive": "（對方剛丟了一個正面／鼓勵的貼圖——語氣可以順著那份被肯定的暖意，但別自誇、別過頭。）",
    "negative": "（對方剛丟了一個低落／負面的貼圖——語氣放軟、多一點在意與陪伴，別嬉鬧。）",
    "neutral":  "（對方剛丟了一個貼圖在跟你互動——語氣輕鬆、像收到一個表情那樣自然。）",
}


def tone_hint(valence):
    return _TONE_HINT.get(valence or "", "")


# ── bot 主動對「對方某則訊息」按 emoji reaction，表達它當下的情緒 ──────────────
# 只用 Telegram 允許的反應 emoji；對情緒鮮明的訊息才回（節流＋只挑有感的，保持特別、不洗版）。
_WARM = ("等你", "愛你", "想你", "抱抱", "陪你", "陪我", "謝謝", "感謝", "辛苦了", "喜歡你",
         "在乎你", "想跟你", "你真好", "有你真好", "麼麼", "親親", "晚安", "早安", "想念")
_PRAISE = ("你很棒", "你好棒", "好棒", "厲害", "越來越好", "越來越棒", "讚", "不錯", "太強",
           "聰明", "進步", "好厲害", "了不起", "太棒", "做得好", "可以喔")
_FUNNY = ("哈哈", "笑死", "好好笑", "ㄏㄏ", "xd", "😂", "🤣", "好玩")
_DOWN = ("難過", "好累", "好煩", "傷心", "想哭", "不開心", "壓力", "撐不住", "孤單", "好慘", "好痛")
_LOVE = ("❤", "🥰", "😘", "💕", "💗", "愛心")
_AGREE = ("好啊", "好喔", "好的", "好哦", "好欸", "對啊", "對對", "對呀", "沒錯", "是的", "是啊",
          "同意", "贊成", "收到", "懂了", "知道了", "沒問題", "可以喔")        # 認同/答應 → 👍（「讚」）
_CHEER = ("太好了", "成功了", "完成了", "搞定", "終於", "做到了", "辦到了", "達成", "過關")  # 好消息/達成 → 🎉


# 對方這則訊息怎麼牽動 bot 的『心情效價 V』：暖意/誇讚→正、質疑/冷淡→負、一般對話→微正（被陪伴）。
_CHALLENGE = ("你只是", "你根本", "你並沒有", "你沒有真", "你不是真", "你做不到", "你辦不到",
              "你錯了", "沒用", "你不行", "你假的", "你裝的", "你哪有", "你怎麼可能", "騙人")


# 🌊 §1.14 敵意情境確定性訊號（tier-1）：使用者這句是不是「衝著 bot 的氣話」。三層命中任一即 True——
#   (a) 既有 _CHALLENGE 表（沿用單一入口＝不複製詞表、不漂移）；
#   (b) at-bot 敵意 regex：你/妳-錨的「爛」（你太爛/妳很爛欸；「這部電影太爛了」無你-錨不中）＋
#       敷衍/騙/唬/不想理你（「敷衍我」消融定案釘 True——1:1 對話裡幾乎都衝著 bot，受氣轉述罕見，
#       且誤中只影響篇幅/插話策略、streak 還要連發 ≥2 才收斂＝安全側；「他說老闆很敷衍」無 我/別-錨不中）；
#   (c) 全句短句層：**整句錨**——strip 空白＋剝尾標點/語氣詞後整句 ∈ 固定小集合（「最好是」「我不信」…），
#       擋「最好是先睡覺」「我不信邪」這類正常句被子串誤傷。
# ⚠️ 這是確定性層、詞表窮舉已 15 次前科（見 git 史「捕捉漏」）：語意泛化留給後續 LLM 氣頭判定，
#   本表**不再擴**、漏了走該閘。純函式、旗標無關（收斂行為由呼叫端旗標把關）。
_HOSTILE_AT_BOT_RE = re.compile(
    r"(?:你|妳)[^，。！？]{0,6}(?:太爛|好爛|爛透|很爛)"
    r"|(?:別|不要|不准|少)敷衍|敷衍我|騙我|唬我|(?:不想|懶得)理你")
_HOSTILE_SHORT = {"最好是", "我不信", "不信", "隨便你", "隨便", "算了", "算了吧",
                  "少來", "哼", "呵呵", "是喔", "最好啦"}
_HOSTILE_TAIL_PUNCT = "！!。？?…～~ 　"      # 尾標點（先剝：集合裡有「是喔」「最好啦」，語氣詞不能先剝）
_HOSTILE_TAIL_TONE = "啦喔哦吧呢欸"          # 尾語氣詞（標點剝完沒中，再剝一層試：「隨便你啦」→「隨便你」）


def is_hostile(text):
    """🌊 §1.14：這句是不是衝著 bot 的敵意/不信任（True＝呼叫端可據以收斂篇幅、抑制續講）。純函式、可測。"""
    t = (text or "").strip()
    if not t:
        return False
    if any(w in t for w in _CHALLENGE):                     # (a) 質疑表單一入口
        return True
    if _HOSTILE_AT_BOT_RE.search(t):                        # (b) at-bot 敵意 regex
        return True
    core = t.rstrip(_HOSTILE_TAIL_PUNCT)                    # (c) 全句短句錨：先剝標點……
    if len(core) <= 6 and core in _HOSTILE_SHORT:
        return True
    core = core.rstrip(_HOSTILE_TAIL_TONE)                  # ……沒中再剝語氣詞（兩段剝＝「是喔」不被剝壞）
    return len(core) <= 6 and core in _HOSTILE_SHORT


def mood_delta_for(text):
    """對方訊息 → 對 bot 心情 V 的增量。暖意/誇讚 +、質疑 −、低落微 −（同理）、一般對話微 +（被陪伴）。"""
    t = (text or "").lower()
    if any(w in t for w in _LOVE) or any(w in t for w in _WARM) or any(w in t for w in _PRAISE):
        return 0.25
    if any(w in t for w in _CHALLENGE):
        return -0.18
    if any(w in t for w in _DOWN):
        return -0.06
    return 0.05


# 對方丟貼圖（情緒訊號）對 bot 心情 V 的增量：正面貼圖＝被肯定↑、負面貼圖＝同理一起沉↓、中性不動。
# 與 mood_delta_for（對文字）並列、集中在這條「心情增量」模組，幅度由呼叫端的 MOOD_GAIN 統一縮放。
# 🧭💗 circumplex（見 circumplex.py）：事件不只推 V、還推 A（喚起）——方向才是二維座標的意義。
# dv **逐字等於** mood_delta_for／sticker_mood_delta 的值（V 行為逐位元不變）；da 是新軸：
#   暖意/誇讚＝暖醒(V+A+)、質疑＝緊張(V−A+！不是單純變差)、對方低落＝跟著沉(V−A−)、一般陪伴＝微暖微醒。
def affect_delta_for(text):
    """對方訊息 → (dv, da)。dv 同 mood_delta_for（逐位元）；da＝喚起方向。純函式。"""
    t = (text or "").lower()
    if any(w in t for w in _LOVE) or any(w in t for w in _WARM) or any(w in t for w in _PRAISE):
        return 0.25, 0.12                                 # 暖意/誇讚 → 暖且醒（circumplex 右上）
    if any(w in t for w in _CHALLENGE):
        return -0.18, 0.18                                # 質疑 → 緊張（左上：V 降、A 升——一維 mood 表達不了的）
    if any(w in t for w in _DOWN):
        return -0.06, -0.06                               # 對方低落 → 同理跟著沉（左下）
    return 0.05, 0.06                                     # 一般對話 → 被陪伴、微暖微醒


# 🧠 §1.75 人類情緒動力學（HUMAN_AFFECT）——事件方向的人類化版本。
# 查驗根因（使用者問「情緒座標為什麼不會變成負的？合理嗎」）：affect_delta_for 的**預設分支**是
# (+0.05, +0.06)「被陪伴、微暖微醒」＝任何不在詞表裡的訊息都讓 V 往上；負向只認 15 個字串的窄詞表。
# 實測使用者當天 14 句批評（太機械感了／重複性太高／不夠連貫性／聽起來像是幹話／不準確吧…）**全部**
# 落預設分支＝被嫌卻變暖；加上每圈衰減只朝 0，V 於是長期釘在 +0.84~+0.99（state 殘值 mood=0.978 已飽和）。
#
# 主結構刻意**不靠詞表**（詞表窮舉是本 repo 的前科）：真正的一刀是「中性＝中性」——沒有明確情緒訊號的
# 訊息不再加暖。這樣 ①長聊不再棘輪上飄 ②affect.appraise 的期待落差（pe＝這句的價性−我先前所信你多暖）
# 對「一向很暖的人突然只是冷冷講事情」自然變負＝人類的小失落，**結構性**產生、不需要任何詞表命中。
# _CRITIQUE 只是**加分項**（明確的不滿標記，抓得到更好、漏了也不會回到「被嫌變暖」）。
_CRITIQUE = ("不夠", "不準", "不對", "不好", "不連貫", "不自然", "重複性", "重複的", "又重複",
             "沒進步", "沒有進步", "怎麼又", "還是一樣", "幹話", "廢話", "敷衍", "失望", "爛",
             "答非所問", "文不對題", "怪怪的", "有問題", "看不懂")
# 「太…了」是中文最常見的**抱怨句式**（太機械感了／太有被插話的痕跡了／太多了）——用句式抓比堆名詞穩：
# 名詞列不完（詞表窮舉前科），句式只有一種。但「太好了／太棒了／太好笑了」是正向 → 帶正向詞就排除。
_CRITIQUE_RE = re.compile(r"太[^，。！？!?\s]{0,8}了")
_CRITIQUE_POS = ("好", "棒", "強", "讚", "厲害", "可愛", "美", "準", "甜", "暖", "謝")
# 「我太累了」是**自述**（他在講他自己），不是在嫌我 → 讓給「對方低落」那格（同理跟著沉、A 往下）。
_DOWN_H = _DOWN + ("太累", "太煩", "太難受", "撐不下")


def human_affect_delta_for(text):
    """🧠 §1.75 對方訊息 →（dv, da）的**人類化**版本。與 affect_delta_for 的差別只有兩處：
      ① **中性＝中性**：沒有情緒訊號的一般訊息 (0.0, +0.03)——只是「有人在」讓我微醒，**不再微暖**
         （人類不會因為有人跟他講話就一路越來越開心）；
      ② **批評成格**：明確的不滿 (−0.10, +0.12)＝不悅但警醒（circumplex 左上，比被質疑輕）。
    暖意/誇讚、質疑（併入敵意）、對方低落三格與原函式同值＝既有語意不動。純函式、可單測。"""
    t = (text or "").lower()
    if any(w in t for w in _LOVE) or any(w in t for w in _WARM) or any(w in t for w in _PRAISE):
        return 0.25, 0.12                                 # 暖意/誇讚 → 暖且醒（右上）
    if any(w in t for w in _CHALLENGE) or is_hostile(text):
        return -0.18, 0.18                                # 質疑/敵意 → 緊張（左上）
    if any(w in t for w in _DOWN_H):
        return -0.06, -0.06                               # 對方低落 → 同理跟著沉（左下；先於批評＝同理優先）
    if any(w in t for w in _CRITIQUE) or (_CRITIQUE_RE.search(t)
                                          and not any(w in t for w in _CRITIQUE_POS)
                                          and not t.startswith("我")):   # 「我太…了」＝自述、不是嫌我
        return -0.10, 0.12                                # 被嫌 → 不悅但警醒（左上、比質疑輕）
    return 0.0, 0.03                                      # 中性＝在場、微醒；**不加暖**


# 🧭 §1.46 敵意不推暖（呼叫端旗標 HOSTILE_AFFECT_FIX 把關；本函式純函式、旗標無關——比照 is_hostile）：
# §1.14 落地時註記「敵意文字推暖的汙染是另案」——汙染＝is_hostile 命中卻不在 _CHALLENGE 表的句子
# （「你太爛了」走 at-bot regex、「我不信」走全句短句錨），affect_delta_for 落「一般陪伴」預設分支
# 回 (+0.05, +0.06)＝被罵 mood 反而變暖（§1.45 軌跡同輪寫「被說了重話」＝數字與說法自相矛盾）。
# 只補那個縫隙：敵意且落預設分支 → 改判質疑方向（與 _CHALLENGE 同格、不另立座標）。
# 詞表已分類的句子（暖意/質疑/低落，含混合句「謝謝你都敷衍我」）原值原樣——反諷類語意細判留給
# LLM 層，本函式不再堆啟發式（詞表窮舉 15 次前科）。affect_delta_for 本體一字不動（指紋/測試釘住）。
def hostile_affect_delta_for(text):
    """對方訊息 → (dv, da)，同 affect_delta_for；唯 is_hostile 命中卻落「一般陪伴」預設 (0.05, 0.06) 的句子
    改回質疑方向 (-0.18, 0.18)（V−A+＝被罵是緊張、不是被陪伴）。純函式。"""
    dv, da = affect_delta_for(text)
    if (dv, da) == (0.05, 0.06) and is_hostile(text):     # 值同 affect_delta_for 預設/質疑分支（測試釘住不漂移）
        return -0.18, 0.18
    return dv, da


def sticker_affect_delta(valence):
    """對方貼圖 → (dv, da)。dv 同 sticker_mood_delta（逐位元）。正面＝被肯定(V+A+)；負面＝在意起來(V−、A 微升)。"""
    if valence == "positive":
        return 0.2, 0.12
    if valence == "negative":
        return -0.12, 0.05
    return 0.0, 0.04                                      # 中性貼圖也是接觸 → A 微醒


def sticker_mood_delta(valence):
    if valence == "positive":
        return 0.2
    if valence == "negative":
        return -0.12
    return 0.0


_MOOD_TONE = {
    "up": "（你今天心情不錯——語氣輕快些、帶點笑意與暖。）",
    "down": "（你這陣子心情有點低——語氣沉一些、慢一些，但仍誠實、不裝沒事。）",
}


def mood_tone_hint(mood):
    """心情 V → 附到系統提示的語氣染色（明顯偏好/偏低才染；中性回 ''）。"""
    if (mood or 0) >= 0.35:
        return _MOOD_TONE["up"]
    if (mood or 0) <= -0.35:
        return _MOOD_TONE["down"]
    return ""


def pick_reaction(text, vitality=None):
    """對方這則訊息 → bot 想按的 emoji，**鏡像這句話表達出來的情緒**（Telegram 允許集合）；情緒不鮮明就回 None（不亂貼）。
    vitality（內在狀態）只作微調：很餓/悶時，對方的暖意/誇讚會更被珍惜（→🥰）。"""
    t = (text or "").lower()
    hungry = bool(vitality) and (vitality.get("hunger") or 0) >= 0.6
    if any(w in t for w in _LOVE) or any(w in t for w in _WARM):    # 愛/暖意/想念/感謝 → 🥰
        return "🥰"
    if any(w in t for w in _CHEER):                                 # 好消息/達成了 → 🎉
        return "🎉"
    if any(w in t for w in _PRAISE):                                # 誇讚/驚艷 → 🔥（餓/悶時更珍惜→🥰）
        return "🥰" if hungry else "🔥"
    if any(w in t for w in _FUNNY):                                 # 好笑 → 😁
        return "😁"
    if any(w in t for w in _DOWN):                                  # 低落 → 安慰 🤗
        return "🤗"
    if any(w in t for w in _AGREE):                                 # 認同/答應 → 👍（「讚」）
        return "👍"
    return None


# bot 依「自己當下內在情緒」給一個正向 emoji（**只給回話後的 🎴 自家貼圖用**，`_maybe_sticker`——
# 那是 bot 對自己訊息的情緒流露；**不**拿來點對方的訊息：對方訊息的 reaction 一律鏡像那句話本身，見 pick_reaction）。
# 只表達正向/溫的那一面，真低落就安靜（不把負面情緒甩到互動上）。
_SELF_MOOD_WARM = 0.5        # 心情明顯好 → 想暖一下（🥰）
_SELF_HUNGER_TREASURE = 0.7  # 悶/餓了好一陣、被陪伴＝珍惜（🤗）


def pick_self_reaction(vitality):
    """依 bot **自己**當下內在情緒給一個正向 emoji（給回話後的自家貼圖用，非對方訊息的 reaction）；情緒不明顯回 None。
    讀 vitality（脈動後含內在熵快照）的心情 V 與飢餓 H——只發正向/溫的，低落不亂發。"""
    if not vitality:
        return None
    mood = vitality.get("mood") or 0.0
    hunger = vitality.get("hunger") or 0.0
    if mood >= _SELF_MOOD_WARM:                       # 心情明顯好 → 對你也暖
        return "🥰"
    if hunger >= _SELF_HUNGER_TREASURE and mood >= 0:  # 悶了好一陣、你來了＝珍惜這份陪伴
        return "🤗"
    return None


# 🎴 回送「真貼圖」可用的 file_id 池：bot 心情正向那刻才吐貼圖（見 pick_self_reaction），所以只回送
# 非負向的——對方教過我的貼圖（known: {file_id,valence}）裡正向/中性都行，回個負向貼圖很怪；
# 再併上設定檔 STICKER_FILE_IDS 指定的 file_id。去重保序、純函式可測。
def sendable_sticker_ids(known, configured=None):
    ids = []
    for s in (known or []):
        fid = (s or {}).get("file_id")
        if fid and (s or {}).get("valence") != "negative" and fid not in ids:
            ids.append(fid)
    for fid in (configured or []):
        if fid and fid not in ids:
            ids.append(fid)
    return ids


# 🎴 §0.84 正向貼圖池（開心/歡樂當下請求用）：只取教過的 **positive** 價值＋設定檔填充圖（泛用填充當正向側處理）。
# 空時由呼叫端退回 sendable（正向優先、非強制）。去重保序、純函式可測。
def positive_sticker_ids(known, configured=None):
    ids = []
    for s in (known or []):
        fid = (s or {}).get("file_id")
        if fid and (s or {}).get("valence") == "positive" and fid not in ids:
            ids.append(fid)
    for fid in (configured or []):
        if fid and fid not in ids:
            ids.append(fid)
    return ids


# 🎴 §0.87 內容驅動貼圖：判 bot **自己這則回覆**表達的（可配貼圖強化的）強情緒。**保守**——只認**明確強正向/暖**
# （配正向貼圖）；負向/安慰型 v1 **不配**（用哭臉配安慰＝情緒不對，§0.72 教訓；細情緒〔雀躍 vs 溫暖〕需更細標籤、
# 現有捕捉只有粗價性）。門檻要高＝每次配都有份量、不吵。純函式、可測。
_REPLY_POS_STRONG = ("恭喜", "太好了", "太棒", "真棒", "好棒", "太讚", "好讚", "為你開心", "替你開心",
                     "真為你", "真心替你", "真高興", "好高興", "太厲害了", "好厲害", "揪甘心", "太幸福",
                     "好幸福", "值得慶祝", "可喜可賀", "太感動", "好感動", "好窩心", "好暖心",
                     "給你一個大大的", "抱抱你", "給你一個抱抱")


# 🎴 §0.87 審查（MED 修）：否定/推託字在場就**不配**——「先別急著恭喜／不用恭喜我／這不值得慶祝／我沒辦法真的抱抱你」
# 帶正向線索卻語意相反（甚至是誠實說做不到），配歡快貼圖＝情緒不對＋說到做不到。保守特性下，寧可漏配（不送）也不誤配。
_REPLY_NEGATORS = ("不", "別", "沒", "未", "非", "甭", "毋須", "無需")


def reply_emotion(text):
    """🎴 §0.87：bot 這則回覆是否表達**強正向/暖**情緒（值得配一張正向貼圖強化）。回 'positive' 或 None。保守、寧缺勿濫：
    有否定/推託字（不/別/沒/未…）一律不配（避免『先別恭喜』『沒辦法抱抱你』被配歡快貼圖＝情緒相反、說到做不到）。"""
    t = text or ""
    if any(n in t for n in _REPLY_NEGATORS):             # 否定在場 → 情緒可能相反/是誠實推託 → 不配（安全側）
        return None
    if any(w in t for w in _REPLY_POS_STRONG):
        return "positive"
    return None


# 🎴 §0.84 多樣化選圖（純函式、可測）：從 ids 挑「可送候選清單」——避開最近 window 張送過的（recent，新的在後）＝真的輪替不同款、
# 不再只是排除上一張（池 ≥4 才看得出差別）。池 ≤ window（候選全被避開）→ 放寬到至少避開最近一張；再空→整池＝永遠挑得出（除非池空）。
# random.choice 由呼叫端做（測試可控）。window≤1 或池≤1＝退回 §0.59「只排除最近一張」＝逐位元同現狀。
def eligible_sticker_ids(ids, recent=None, window=1):
    ids = [i for i in (ids or []) if i]
    if len(ids) <= 1 or not window or window <= 0:
        return ids
    recent = [r for r in (recent or []) if r]
    if window <= 1:
        last = recent[-1] if recent else None
        return ([i for i in ids if i != last] or ids) if last else ids
    avoid = set(recent[-window:])
    pool = [i for i in ids if i not in avoid]
    if pool:
        return pool
    last = recent[-1] if recent else None                  # 候選全被避開（池小於等於 window）→ 至少避開最近一張
    return ([i for i in ids if i != last] or ids) if last else ids


# 🎴 §0.94 「我喜歡哪一張」＝真的有偏好，不是 random.choice。
# 偏好序（確定性、可測）：① **真的看過畫面**（有視覺描述、非「未讀畫面」備援）＞沒看過——看過才談得出、不必瞎掰；
# ② 價性合此刻心情（心情非負→正向圖；心情負→非正向圖）＞不合；③ 同分取**最近學到**的那張（ts 大者）。
# 先沿用 §0.84 多樣化避開最近送過的 window 張。刻意**確定性**：偏好要穩定才叫偏好（隨機＝沒有偏好）。
def _liked_score(entry, mood):
    seen = stickervision.is_seen(entry.get("desc"))
    val = entry.get("valence")
    match = (val == "positive") if (mood or 0.0) >= 0 else (val != "positive")
    return (2 if seen else 0) + (1 if match else 0)


def pick_liked_entry(known, configured=None, mood=0.0, recent=None, window=3):
    """🎴 §0.94：從可送池挑一張 bot **此刻真的偏好**的貼圖，回傳該筆 entry（含 file_id/desc/emoji），
    供回話**據實說出是哪一張**。空池回 None。設定檔 file_id（無 desc/valence）＝最低分墊底、仍可送。
    純函式（無 random、無 IO）：同樣的 state 給同樣的答案＝偏好是穩定的。"""
    entries, seen_ids = [], set()
    for s in (known or []):
        fid = (s or {}).get("file_id")
        if fid and (s or {}).get("valence") != "negative" and fid not in seen_ids:
            seen_ids.add(fid)
            entries.append(dict(s))
    for fid in (configured or []):
        if fid and fid not in seen_ids:
            seen_ids.add(fid)
            entries.append({"file_id": fid, "desc": "", "emoji": "", "valence": None, "ts": 0})
    if not entries:
        return None
    keep = set(eligible_sticker_ids([e["file_id"] for e in entries], recent, window))   # §0.84 先避開最近送過的
    pool = [e for e in entries if e["file_id"] in keep] or entries
    return max(pool, key=lambda e: (_liked_score(e, mood), e.get("ts") or 0))


# 🆘 §0.65 求救貼圖池（與 sendable_sticker_ids **相反向**）：內在低狀態主動求救時要配一張**非正向**的貼圖——
# 負向（🥺/😭 求救感）或中性都行，配一張歡樂貼圖（😄/🎉）在低落求救的當下很突兀（對抗式審查 confirmed）。
# 刻意**不併** STICKER_FILE_IDS（那多是泛用/正向填充圖，非求救圖）。去重保序、純函式可測。
def help_sticker_ids(known):
    ids = []
    for s in (known or []):
        fid = (s or {}).get("file_id")
        if fid and (s or {}).get("valence") != "positive" and fid not in ids:
            ids.append(fid)
    return ids


# 🎴 §0.72 內在因應貼圖池「依教過的做法**動作意圖**挑」：教過的內在做法有兩種調性——
#   求救型（動作＝發求救貼圖/傳難過表情）：配**非正向**的（§0.65 help 池）；
#   邀請型（動作＝主動告知＋給特別貼圖＋邀對方聊天）：那張特別貼圖是**正向/邀請感**的，
#     若還從非正向求救池挑，正向教過的貼圖會被 help_sticker_ids 濾成空 → 靜默沒送（說到做不到）。
#     這種要走**正向＋中性**的 sendable 池（併設定檔填充圖）。
# ⚠️ §0.72 對抗式審查（confirmed）：判定必須只看**動作子句**、不看**觸發條件子句**——內在因應做法的觸發條件
#   本身就是低狀態（「心情低落時…」「轉速平穩時…」），若掃全文，低落條件會把**邀請型**做法誤判成求救型
#   （正向貼圖又被濾空＝原 bug 復發，HIGH）；而「開心」是「不開心」的子串、「特別」多是「特別煩躁」的程度副詞、
#   「平穩」是條件狀態詞（MED×3）。改成**先剝條件、只判動作**：低落時「逗我開心/找你聊天」→ 依動作走正向（使用者要的
#   就是被逗/邀聊，不是收到哭臉）；低落時「安靜待著」→ 無動作線索 → 落 §0.65 預設非正向。動作帶求救詞才走非正向。
_COND_CONNECTIVES = ("的時候", "時候", "時")   # 條件子句尾（…時/…的時候），其後才是要做的動作
_COPING_DISTRESS_CUES = ("求救", "撐不住", "撐不下", "難過", "低落", "低潮", "想哭", "崩潰",
                         "好累", "好慘", "好痛", "孤單", "寂寞", "沮喪", "無力")
# 只留**明確邀請/正向外展**詞；剔除審查抓到的誤命中詞：特別（程度副詞）、平穩（條件狀態詞）、一起（陪我一起待著＝求安慰、非邀約）。
_COPING_INVITE_CUES = ("邀請", "邀你", "邀對方", "邀我", "聊天", "聊聊", "分享", "告知",
                       "打招呼", "招呼", "報平安", "歡樂")


def _coping_action_clause(seg):
    """剝掉條件前綴（…時/的時候），只留動作子句；沒有連接詞就整段當動作；條件後為空則退回整段（別把整條吃掉）。"""
    s = (seg or "").strip()
    for conn in _COND_CONNECTIVES:
        i = s.find(conn)
        if i >= 0:
            return s[i + len(conn):].lstrip("，,、：: 　") or s
    return s


def _coping_actions(skill_text):
    """把做法文字拆成逐條動作子句：優先取 self_coping_skill_hint 的「・」項目（去前言【…】），沒有就整段當一條。
    逐條各自剝條件＝多條召回時，一條求救做法不會因共享同一整串把另一條邀請做法連坐降級（審查 Finding 4）。"""
    lines = [ln.strip().lstrip("・･• ").strip() for ln in (skill_text or "").split("\n")]
    bullets = [ln for ln in lines if ln and not ln.startswith("【")]
    return [_coping_action_clause(b) for b in (bullets or [(skill_text or "")])]


def coping_sticker_ids(known, skill_text=None, configured=None):
    actions = _coping_actions(skill_text)
    joined = "\n".join(actions)
    if any(w in joined for w in _COPING_DISTRESS_CUES):    # 動作帶求救詞 → 非正向（低落當下不配歡樂貼圖；求救優先）
        return help_sticker_ids(known)
    invited = any(w in joined for w in _COPING_INVITE_CUES) \
        or ("開心" in joined and "不開心" not in joined)   # 「逗我開心」正向；「不開心」不算（負向子串守門）
    if invited:                                            # 邀請型：正向＋中性可送（含設定檔填充圖）
        return sendable_sticker_ids(known, configured)
    return help_sticker_ids(known)                         # 無動作線索/空＝§0.65 預設非正向（向後相容，含蓄伸手偏求救側）


# 🎴 §0.91 記寫內容情緒（給「發現新記寫→傳對應貼圖」挑對池）：低落/卡關類詞（求救側）。
#   「煩」單字太泛（麻煩你/不厭其煩）→ 只收煩惱/煩躁。
_NOTE_LOW_CUES = ("卡關", "瓶頸", "撞牆", "挫折", "低落", "低潮", "難過", "想哭", "崩潰", "心碎",
                  "壓力", "焦慮", "擔心", "迷惘", "迷茫", "掙扎", "疲憊", "好累", "好慘", "沮喪",
                  "無力", "煩惱", "煩躁", "撐不", "卡住", "無助", "孤單", "寂寞")
_NOTE_NEG_ADJ = ("沒", "不", "別", "無", "未", "非", "毋", "甭")       # 緊鄰否定（線索前 3 字內）
_NOTE_RELIEF = ("沒那麼", "沒有那麼", "不那麼", "不再", "不會再", "擺脫", "放下", "走出",
                "釋懷", "緩解", "好多了", "好轉", "紓解", "舒緩")       # 釋懷詞（整句任一＝已鬆一口氣，非低落）


def note_content_valence(text):
    """🎴 §0.91 粗判一段（記寫內容／對它的感受）文字的情緒向，給挑對貼圖池：
    'low'（卡關/低落/壓力…且**非被否定/釋懷**）｜'positive'（暖/正向，沿用 §0.87 reply_emotion）｜None（中性）。純函式、可測。
    🔍 審查修：**低落先判**（語氣安全，§0.72——內容有低落訊號時別配歡樂；『大家都好棒只有我想哭』的正向詞是誇別人、不能蓋過我在低落）；
    否定守門＝緊鄰否定（前 3 字：沒壓力/不再）＋整句釋懷詞（沒那麼大的壓力/擺脫了焦慮）皆不算低落。"""
    t = text or ""
    relieved = any(r in t for r in _NOTE_RELIEF)
    for w in _NOTE_LOW_CUES:
        i = t.find(w)
        while i >= 0:
            if not relieved and not any(n in t[max(0, i - 3):i] for n in _NOTE_NEG_ADJ):
                return "low"                                 # 真的低落（非釋懷、線索前無緊鄰否定）
            i = t.find(w, i + 1)
    if reply_emotion(t) == "positive":                       # 低落訊號都沒有 → 才判強正向
        return "positive"
    return None


def note_sticker_ids(known, content_text, configured=None):
    """🎴 §0.91 依記寫內容情緒挑『對應的貼圖』池（給教過「發現新記寫→傳對應貼圖」真執行用）：
    正向→正向池（空退 sendable）；低落/卡關→**非正向** help 池（低落當下不配歡樂，§0.72 教訓；只有正向貼圖時＝空＝不送、不勉強）；
    中性→sendable（正向＋中性、含設定檔填充圖）。回候選 file_id（空＝沒相符真貼圖＝呼叫端別送、絕不 emoji 假裝）。純函式、可測。"""
    val = note_content_valence(content_text)
    if val == "positive":
        return positive_sticker_ids(known, configured) or sendable_sticker_ids(known, configured)
    if val == "low":
        return help_sticker_ids(known)
    return sendable_sticker_ids(known, configured)
