"""時間感引擎：把記憶層的時間訊號抽成「時間上下文」，餵給教練，讓它的回應、引用、
回想都帶時間脈絡。純函式、無外部依賴（只用 ts）。

時間感的幾個維度：
  現在錨點（now/星期/時段）、新近與斷裂（多久沒寫、各脈絡多久沒碰）、節奏（慣常時段/平日週末）、
  跨度與冷卻（脈絡陪你多久、哪些在冷卻）、週期回顧（一週/一個月前的今天）、
  時間範圍回想（「上週」「三天前」→ 取那段的記寫）。
"""

import os
import re
from datetime import datetime, timedelta, timezone

from . import analyzer

WEEKDAYS = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"]
COOL_DAYS = 7          # 多久沒碰算「冷卻中」
DROUGHT_DAYS = 3       # 多久沒寫算「斷了」
BURST_WINDOW_MIN = 30  # 近此分鐘內多筆＝爆量
BURST_MIN_RECORDS = 4
# 「剛剛/剛才」＝最近一段連續記寫：停筆超過這麼久就算換一段（鏡射 LINE 端 30 分的敘事片段）。
RECENT_SESSION_GAP_MIN = 30
# 「剛剛」的語感上限：那段記寫**結束點離現在**超過這麼久，就不再叫「剛剛」，改用時段相對描述
# （今天早上／昨晚／前天午後…）。≤ 此值才算「剛剛」。使用者語感：剛剛≈往前推幾小時內，久了會說昨晚/今天早上。
JUST_NOW_RECENT_HOURS = 6
# 認得「剛剛」這種「就在剛才那批」的問法——不是整天，而是最近這一段連續記寫。
JUST_NOW_RE = re.compile(r"剛剛|剛才|方才|才剛|剛寫|適才")
# 指代型時間（本身沒有絕對錨）：那時候/當時/那次… → 接「對話裡剛確立的時間」，沒有就退「最後一次記寫那段」。
ANAPHORA_RE = re.compile(r"那時候|那個時候|那時|那陣子|那會兒|那一刻|當時|那次|那段時間|那段期間")
# 「新東西/最新/剛進來…」＝指最近一段新記寫（＝剛剛那批）；bot 常說「剛剛有新東西進來」，使用者接著問就是這個。
FRESH_DEIXIS_RE = re.compile(r"新東西|新進來|剛進來|剛落進來|最新|新記寫|新的東西|剛剛那批|剛那批|最近(那批|那段|寫的)")


def day_part(hour):
    if hour < 5:
        return "深夜"
    if hour < 8:
        return "清晨"
    if hour < 11:
        return "早上"
    if hour < 14:
        return "中午"
    if hour < 17:
        return "午後"
    if hour < 19:
        return "傍晚"
    if hour < 23:
        return "晚上"
    return "深夜"


def human_gap(delta):
    secs = max(0, delta.total_seconds())
    if secs < 3600:
        return f"{int(secs // 60)} 分鐘"
    if secs < 86400:
        return f"{int(secs // 3600)} 小時"
    return f"{int(secs // 86400)} 天"


# 口語相對時間：承接前文的語氣由它決定（單一防線，集中於此；不在各呼叫點各自包裝）。
#   <60s → 「剛剛」（緊接著直接接回，不外露「0 分鐘」）；
#   <1h  → 「約 N 分鐘前」；<1天 → 「約 N 小時前」；否則 → 「約 N 天前」。
# 並映射「承接框架」：剛剛＝緊接、小時～天＝回到之前、很久（≥3天）＝久違感。
JUST_NOW_GAP_SEC = 60       # 此秒數內一律稱「剛剛」（不報秒數、不外露「0 分鐘」）
LONG_AGO_DAYS = 3           # 隔這麼多天以上 → 久違感框架


def spoken_gap(secs):
    """把『幾秒前說的』翻成口語相對時間字串（程式算好餵 LLM，LLM 不自算、不報精確秒數）。
    <60s→『剛剛』、<1h→『約 N 分鐘前』、<1天→『約 N 小時前』、否則→『約 N 天前』。純函式。"""
    secs = max(0.0, float(secs))
    if secs < JUST_NOW_GAP_SEC:
        return "剛剛"
    if secs < 3600:
        return f"約 {int(secs // 60)} 分鐘前"
    if secs < 86400:
        return f"約 {int(secs // 3600)} 小時前"
    return f"約 {int(secs // 86400)} 天前"


def spoken_frame(secs):
    """承接框架（語氣選擇）：'just'（剛剛＝緊接著直接接回）｜'earlier'（隔了一陣＝回到之前那個）｜
    'long'（很久＝可帶久違感不誇飾）。純函式，配合 spoken_gap 一起餵 LLM 選承接語氣。"""
    secs = max(0.0, float(secs))
    if secs < JUST_NOW_GAP_SEC:
        return "just"
    if secs >= LONG_AGO_DAYS * 86400:
        return "long"
    return "earlier"


# 🤝 排程承諾的時間解析：把「八點／晚上八點／十分鐘後／明天X點」解析成**下一個該時刻的 epoch**。
# 純函式、無 IO/LLM、可單測。與 match_time_range（過去範圍）正交——這支只解析未來的單一鐘點/相對時刻。
# 規則（以針對性測試鎖死語意）：
#   ① 相對：「N 分鐘後／N 小時後／半小時後」＝now + 相對量。
#   ② 鐘點：「(晚上|下午|晚)X點(半)?」強制下午時段（X<12 → +12，取 20:00 等）；「(早上|上午|清晨)X點」強制上午；
#      純「X點」＝取今天 X:00 與今天 (X+12):00 中**第一個未來時刻**（如現在 19:00 說八點＝今天 20:00；
#      現在 21:00 說八點＝明天 08:00）；若兩個都已過 → 取明天較早那個。
#   ③「明天X點」＝明天該時刻（時段詞同樣決定上/下午）。
# 解析不到、或 tz/now 為 None → 回 None（呼叫端據此判定「不是排程承諾」並安全略過）。
_REL_AFTER_RE = re.compile(r"([0-9]+|[一二兩三四五六七八九十]+)\s*(?:個)?\s*(分鐘|分|小時|鐘頭|個鐘|天)\s*(?:之?後|後)")   # 🤝 §0.76 加「N天後」（兩天後提醒我＝+2 天）
# 🤝 §0.76 審計（confirmed HIGH）：「一個半小時後」的「半小時後」子串被吃成 +30 分（差一小時亂發）→ 半前不得緊接 數字/個；
# 「N個半小時後」另配專用樣式＝N*60+30。elided 形同步（lookbehind 類加 個）。
_HALF_HOUR_RE = re.compile(r"(?<![0-9一二兩三四五六七八九十個])半\s*(?:個)?\s*(?:小時|鐘頭)\s*(?:之?後|後)")
_HOUR_HALF_RE = re.compile(r"([0-9]+|[一二兩三四五六七八九十]+)\s*個半\s*(?:小時|鐘頭)\s*(?:之?後|後)")
# 🤝 §0.61 口語省略「後」（與 selfstate._SCHED_REL_ELIDED_RE 同步）：「30分鐘叫我起床」＝時距＋指向我動作緊隨
# （≤4字內）＝「…後」被省略。動作前瞻收窄＝「睡了30分鐘」不解析成時刻。裸「分」不收（歧義）。SCHED_REL_ELIDED=0 一鍵退。
# 對抗式審查（confirmed HIGH，與 selfstate 同步）：(?<![0-9一二兩三四五六七八九十了每]) 擋完成/慣常敘述（等了十分鐘才回我/每半小時提醒我一次）；
# 縫隙排除敘事連接詞 才/就/每 與換行——否則過去敘述會被解析成幽靈目標時刻、+N 分鐘亂發。
# §0.62（與 selfstate 同步）：時距與動作跨一個逗號＝常見睡覺-叫醒講法 → 縫隙 ≤6、不把逗號當硬邊界（仍擋句末/換行/才就每）。
# 審查（confirmed HIGH）：縫隙排作助動詞的裸「會」（第三方陳述 X會V我＝幽靈承諾），但放行填充詞等會/待會/一會/過會裡的「會」。
_REL_ELIDED_LOOKAHEAD = r"(?=(?:[^。！？!?\n\r才就每會]|[等待一過]會){0,6}?(?:叫|提醒|喊|敲|通知|聯繫|聯絡|找我|跟我|和我|告訴|回報|回我|回覆))"
_REL_ELIDED_RE = re.compile(r"(?<![0-9一二兩三四五六七八九十了每])([0-9]+|[一二兩三四五六七八九十]+)\s*(?:個)?\s*(分鐘|小時|鐘頭|個鐘)" + _REL_ELIDED_LOOKAHEAD)
_HALF_ELIDED_RE = re.compile(r"(?<![0-9一二兩三四五六七八九十了每個])半\s*(?:個)?\s*(?:小時|鐘頭)" + _REL_ELIDED_LOOKAHEAD)   # §0.76：加「個」擋「一個半小時」子串
# 鐘點：可選日詞 + 可選時段詞 + 數字（支援中文數字一~十二）+「點」+ 可選分鐘（半/三十分/X分）
# 🤝 §0.76 審計（confirmed CRITICAL）：日詞補 明早/明晚/後天（原本被靜默跳過→「明早8點」排成**今晚20:00**、
# 「後天8點」提早兩天）；分鐘群組補一般「X分」（原本只認 半/三十分→「7點15分」的 15 分被吞、排成 19:00）；
# 「分」後不得接「鐘」（免把「3點20分鐘後」的相對量吃進分鐘）。
_CLOCK_RE = re.compile(r"(明天|明日|明早|明晚|後天|今晚|今天晚上|今天)?\s*(晚上|下午|傍晚|晚|早上|上午|清晨|中午|凌晨|半夜)?\s*"
                       r"([0-9]{1,2}|[一二兩三四五六七八九十]+)\s*點\s*(半|三十分(?!鐘)|30分(?!鐘)|[0-9]{1,2}分(?!鐘)|[一二兩三四五六七八九十]+分(?!鐘))?")
# 🔢 數字鐘點 H:MM（1:30 / 3:00 / 15:30 / 3:30）——使用者常打數字而非中文（截圖：1:30/3:00/3:30 全沒被排程記下）。
# 前後不接半形數字/冒號/點，避免吃到日期(06/28 無冒號不中)或時間戳片段。時段詞(晚上/下午/早上)由各時刻前文判。
# 中間分隔符接受半形或全形冒號 [:：]（「問候我：21:20」全形冒號當時間冒號）；但**斷言只排半形**（不把 ： 放進排除類，
# 否則「：21」的全形冒號會誤觸後向斷言、漏掉第一筆 21:20、也吃不到「提醒我：8:55」——對抗式審查 high#1）。
_HHMM_RE = re.compile(r"(?<![\d:.])([0-2]?\d)[:：]([0-5]\d)(?![\d:.])")
# 🤝 §0.76 審計（confirmed HIGH）：「凌晨」原被放在下午詞表（凌晨1點→**13:00**、差 12 小時亂發）→ 移出、
# 與「半夜」同歸「小時段」（凌晨1點=01:00、半夜/凌晨12點=00:00）。「明晚」計下午、「明早」計上午（日詞段處理）。
_AFTERNOON_WORDS = ("晚上", "下午", "傍晚", "晚", "今晚", "今天晚上", "明晚")
_MORNING_WORDS = ("早上", "上午", "清晨", "明早")
_SMALLHOURS_WORDS = ("凌晨", "半夜")


_CN_DIGIT = {"零": 0, "一": 1, "二": 2, "兩": 2, "三": 3, "四": 4, "五": 5,
             "六": 6, "七": 7, "八": 8, "九": 9}


def _cn_to_int(s):
    """把數字字串轉成 int（阿拉伯或中文，支援個位/十位如三十、二十、十五）；認不得回 None。"""
    if s.isdigit():
        return int(s)
    if "十" not in s:                       # 純個位中文（一~九）
        return _CN_DIGIT.get(s)
    # 含「十」：[X]十[Y]（十＝10、二十＝20、十五＝15、三十＝30）
    parts = s.split("十")
    tens = _CN_DIGIT.get(parts[0], 1) if parts[0] else 1
    ones = _CN_DIGIT.get(parts[1], 0) if len(parts) > 1 and parts[1] else 0
    return tens * 10 + ones


# 🤝 §0.69「N分鐘…時間到…做某事」＝到點觸發承諾（截圖「大概要20分鐘，時間到的時候請跟我聊天」「等你5分鐘，
# 時間到回應我」全落 fact_or_chat＝沒入帳）。「時間到」＝『這段時距走完那一刻』的觸發詞（等同「後」），時距不必
# 緊貼動作。取**時間到前方最近**的那個時距（修 msg2「我剛等你20分鐘…這次大概10分鐘，時間到」＝該用 10 不是 20）。
_TIMEUP_TRIGGER_RE = re.compile(r"時間到|時間一到|到時間|時候到|到點|到時候")
_BARE_DUR_RE = re.compile(
    r"(?<![0-9一二兩三四五六七八九十了每])(?:(?P<n>[0-9]+|[一二兩三四五六七八九十]+)\s*(?:個)?\s*(?P<u>分鐘|小時|鐘頭|個鐘)"
    r"|(?P<half>半)\s*(?:個)?\s*(?:小時|鐘頭))")
# 🤝 §0.71：時距前若帶**偏移標記**（在某錨點之後再 N 分鐘）＝偏移、**不是**從現在起算的 timer（截圖「時間到的時候
# 再隔3分鐘給貼圖」＝前約時間 13:47 後 3 分鐘＝13:50，被誤算成 now+3=13:20）。_timeup_epoch 的「觸發詞後方時距」
# fallback 遇偏移標記就不採（該由偏移增補 _maybe_offset_augmentation 接、算 prior+N）。
# 審查（confirmed MED）：這把（Part A、誤算防護）刻意是 selfstate._OFFSET_AUG_RE 的**超集**——多含 之後過/然後過 與
# 裸 隔/過（`過3分鐘`/`隔3分鐘`），寧可多擋（多擋只是不從 now 起算＝退一步不誤發）也不留 now+N 漏洞（曾漏「之後過」）。
_OFFSET_MARK_RE = re.compile(r"(?:再隔|再過|又隔|又過|之後再|然後再|之後過|然後過|隔|過)$")


def _timeup_epoch(t, now):
    """『N分鐘…時間到…』→ now＋時距（時間到＝到點觸發）；優先取時間到**前方最近**的時距（修 msg2 的 20 vs 10），
    前方沒有才退而取後方**最近**的（審查：「時間到叫我，我大概20分鐘」時距在觸發詞後、否則偵測收了卻解不出 epoch＝
    空承諾）。無時間到/無時距回 None。"""
    if os.getenv("SCHED_TIMEUP", "1") == "0":
        return None
    m = _TIMEUP_TRIGGER_RE.search(t)
    if not m:
        return None
    before = list(_BARE_DUR_RE.finditer(t[:m.start()]))
    after_seg = t[m.end():]
    after = list(_BARE_DUR_RE.finditer(after_seg))
    # 🤝 §0.71：後方時距若緊接偏移標記（…再隔3分鐘）＝偏移非 timer → 不採（留給偏移增補算 prior+N）
    if after and _OFFSET_MARK_RE.search(after_seg[:after[0].start()]):
        after = []
    d = before[-1] if before else (after[0] if after else None)   # 前方最近；無則後方最近（非偏移）
    if d is None:
        return None
    if d.group("half"):
        return (now + timedelta(minutes=30)).timestamp()
    n = _cn_to_int(d.group("n"))
    if n is None:
        return None
    delta = timedelta(hours=n) if d.group("u") in ("小時", "鐘頭", "個鐘") else timedelta(minutes=n)
    return (now + delta).timestamp()


def _relative_epoch(t, now):
    """『十分鐘後 / 兩小時後 / 半小時後 / 一個半小時後 / 兩天後』→ epoch；§0.61 亦收省略「後」的『30分鐘叫我』（動作緊隨）；
    §0.69 亦收『N分鐘…時間到…』（時間到＝到點觸發，時距不必緊貼動作）；無相對量回 None。"""
    mh = _HOUR_HALF_RE.search(t)                          # 🤝 §0.76：N個半小時後＝N*60+30（先於半小時，免被子串吃成 30 分）
    if mh:
        n = _cn_to_int(mh.group(1))
        if n is not None:
            return (now + timedelta(minutes=n * 60 + 30)).timestamp()
    if _HALF_HOUR_RE.search(t):
        return (now + timedelta(minutes=30)).timestamp()
    mr = _REL_AFTER_RE.search(t)
    if not mr and os.getenv("SCHED_REL_ELIDED", "1") != "0":   # 🤝 §0.61 省略「後」形（後綴有指向我動作才算）
        if _HALF_ELIDED_RE.search(t):
            return (now + timedelta(minutes=30)).timestamp()
        mr = _REL_ELIDED_RE.search(t)
    if mr:
        n = _cn_to_int(mr.group(1))
        if n is None:
            return None
        unit = mr.group(2)
        delta = (timedelta(days=n) if unit == "天"        # 🤝 §0.76：兩天後提醒我＝+2 天（原本整包漏收＝無守門空口答應）
                 else timedelta(hours=n) if unit in ("小時", "鐘頭", "個鐘") else timedelta(minutes=n))
        return (now + delta).timestamp()
    return _timeup_epoch(t, now)                          # 🤝 §0.69 「N分鐘…時間到」到點觸發（時距不必緊貼動作）


def _hm_epoch(hour, minute, before, local, force_day=None):
    """給 (時,分)＋該時刻**前文片段**(判時段/日詞) → 下一個該時刻 epoch。共用於 中文X點 與 數字H:MM。
    純鐘點（無時段詞、12h 制如『八點』『3:30』）取今天 H 與 H+12 第一個未來；24h（hour>12 如 15:30）只一個候選。
    🤝 §0.76 審計修（confirmed CRITICAL×3＋HIGH×2＋MED）：明早/明晚/後天 日詞；凌晨/半夜＝小時段（凌晨1點=01:00、
    半夜12點=午夜，非 13:00/正午）；晚上12點=午夜；裸 12點 取 {午夜,正午} 第一個未來；中午一點=13:00（非被 noon 蓋成 12:00）。
    force_day（§0.76 審查）：「N天後早上8點」組合形＝固定排在 +N 天的該時刻（不做未來滾動）。"""
    if hour is None or hour > 23 or minute > 59:
        return None
    day2 = "後天" in before
    tomorrow = any(w in before for w in ("明天", "明日", "明早", "明晚"))
    afternoon = any(w in before for w in _AFTERNOON_WORDS)
    morning = any(w in before for w in _MORNING_WORDS)
    small = any(w in before for w in _SMALLHOURS_WORDS)
    noon = "中午" in before

    def at(d, h):
        return local.replace(hour=h % 24, minute=minute, second=0, microsecond=0) + timedelta(days=d)

    def part_hour():
        """依時段詞把 12h 制鐘點定成 24h 制；無時段詞回 None（走純鐘點雙候選）。"""
        if small:                            # 凌晨1點=01:00；凌晨/半夜12點=00:00（午夜）
            return 0 if hour == 12 else hour
        if noon:                             # 中午12點=12:00；中午一點/兩點=13/14（≤3 視為午後續數）
            return 12 if hour == 12 else (hour + 12 if hour < 4 else hour)
        if afternoon:                        # 晚上八點=20:00；晚上12點=午夜（隔日 0 點）
            return 0 if hour == 12 else (hour + 12 if hour < 12 else hour)
        if morning:                          # 早上八點=8；早上12點=0（午夜）
            return 0 if hour == 12 else hour
        return None

    h = part_hour()
    if force_day is not None:                # N天後X點＝固定 +N 天（組合形，§0.76 審查）
        return at(force_day, h if h is not None else hour).timestamp()
    if day2:                                 # 後天X點＝固定 +2 天
        return at(2, h if h is not None else hour).timestamp()
    if tomorrow:                             # 明天/明早/明晚X點＝固定 +1 天
        return at(1, h if h is not None else hour).timestamp()
    if h is not None:                        # 帶時段詞：今天該時刻已過 → 明天同時刻
        c = at(0, h)
        return (c if c > local else at(1, h)).timestamp()
    # 純鐘點：12點 取 {午夜,正午}、12h 制取 {H, H+12}、24h（hour>12）單候選；皆取第一個未來；皆過→明天較早
    cset = {0, 12} if hour == 12 else ({hour % 24} if hour > 12 else {hour, hour + 12})
    fut = sorted(c for c in (at(0, hh) for hh in cset) if c > local)
    if fut:
        return fut[0].timestamp()
    return at(1, min(cset)).timestamp()


def all_clock_epochs(text, now, tz):
    """文中**所有**可解析的排程時刻 epoch（去重、由早到晚排序）。支援 中文X點/X點半、數字 H:MM、相對量。
    給「3:00、3:30 都跟我打招呼」這種一句多時刻記成多筆承諾。tz/now None → []。"""
    if not text or now is None or tz is None:
        return []
    # 🤝🔢 SCHED_TIME_SPACE_FIX（預設開）：空白分隔的多時刻 H:MM（截圖「21:20 21:30 21:43」到點全沒發的時間層根因）。
    # 兩前處理變數並存（純加性）：相對量解析用 strip 版（『十 分鐘 後』不能被頓號斷成『十、分鐘、後』而漏匹配，
    # 對抗式審查 med）；H:MM/中文鐘點用「空白→頓號」版（走已驗證可行的頓號分隔，使 _HHMM_RE 後向斷言不被緊鄰數字破壞）。
    # 旗標 0 → 兩者都回退原 `text.replace(' ','')`＝逐位元同現狀。
    if os.getenv("SCHED_TIME_SPACE_FIX", "1") != "0":
        t_rel = text.replace(" ", "")           # 相對量：吃掉空白（『十 分鐘 後』→『十分鐘後』）
        # 🤝 §0.61 修（同 selfstate 黏回）：空白→頓號會把「8 點」拆成「8、點」＝中文鐘點樣式對不上（偵測過了、
        # 這裡解析 0 筆 → 承諾沒記下）。先黏回「數字＋空白＋點/時間單位」，再頓號分隔（多時刻列表不受影響）。
        # 對抗式審查（confirmed MED）補強：時段/日詞與尾隨「半」也要黏——否則「明天 8 點」變「明天、8點」＝
        # _CLOCK_RE 的 (明天)?(下午)? 群組跨不過頓號 → 解析成**今天 20:00**（時間錯置比漏記更糟）；「三 點 半」同理。
        glued = re.sub(r"([0-9一二兩三四五六七八九十])\s+(?=個?\s*(?:分鐘|分|小時|鐘頭|個鐘|點|天))", r"\1", text)
        glued = re.sub(r"(明天|明日|明早|明晚|後天|今晚|今天晚上|今天|晚上|下午|傍晚|早上|上午|清晨|中午|凌晨|半夜)\s+"
                       r"(?=(?:晚上|下午|傍晚|早上|上午|清晨|中午|凌晨|半夜)|[0-9一二兩三四五六七八九十])", r"\1", glued)
        glued = re.sub(r"點\s+(?=半|三十分|30分|[0-9一二兩三四五六七八九十])", "點", glued)
        t_clk = re.sub(r"\s+", "、", glued)       # H:MM/中文鐘點：空白→頓號（分隔多時刻）
    else:
        t_rel = t_clk = text.replace(" ", "")    # 逐位元退路
    local = now.astimezone(tz)
    eps = []
    r = _relative_epoch(t_rel, now)
    # 🤝 §0.76 審查（confirmed MED）：「兩天後早上8點提醒我」＝**組合**（+2 天的 08:00）——原本 rel(+2d 同時刻) 與
    # clock(明天邏輯 8 點) 各記一筆＝兩個都錯還雙發。有 N天後＋句中有鐘點 → 鐘點固定排在 +N 天、rel 那筆不記。
    _mdays = _REL_AFTER_RE.search(t_rel)
    rel_days = _cn_to_int(_mdays.group(1)) if (_mdays and _mdays.group(2) == "天") else None
    has_clock = bool(_CLOCK_RE.search(t_clk)) or bool(_HHMM_RE.search(t_clk))
    if r and not (rel_days and has_clock):
        eps.append(r)

    def _day_blocked(start):
        """解析不了的日子詞（週X/日期）在鐘點**同一子句**前方 → 這個鐘點不排（§0.76；審查：窗跨句界會誤殺
        「星期五見！今晚8點提醒我」→ 窗在最近的句讀處截斷）。"""
        win = t_clk[max(0, start - 8):start]
        for pc in "。！？!?，,、；;":
            cut = win.rfind(pc)
            if cut >= 0:
                win = win[cut + 1:]
        return bool(_UNSUPPORTED_DAY_RE.search(win))

    for m in _HHMM_RE.finditer(t_clk):       # 數字 H:MM（可多個）；前文 4 字判時段/明天
        before = t_clk[max(0, m.start() - 4):m.start()]
        if _day_blocked(m.start()):          # §0.76：星期五/7月X號 前綴＝解析不了的日子 → 不排（守門誠實問）
            continue
        e = _hm_epoch(int(m.group(1)), int(m.group(2)), before, local, force_day=rel_days)
        if e:
            eps.append(e)
    for m in _CLOCK_RE.finditer(t_clk):      # 中文 X點/X點分（可多個）
        day_word, part_word, num_s, minute_s = m.group(1) or "", m.group(2) or "", m.group(3), m.group(4) or ""
        # 🤝 §0.76 審計（confirmed CRITICAL）：**不能對著被丟掉的日子詞排時間**——「星期五晚上8點」原本忽略星期五、
        # 照排**今晚**20:00（錯日比漏記更糟）。鐘點前綴帶解析不了的日詞（同子句內）→ 這個鐘點不排
        # → 捕捉解不出 target → 落 PROMISE_ACK_GUARD 誠實請對方換講法（守門詞表已同步補星期/週/號）。
        # 自帶日詞（今晚8點）＝日子明確、不受前方髒日詞影響（審查：別誤殺「星期五要開會。明天早上8點叫我」）。
        if not day_word and _day_blocked(m.start()):
            continue
        # 🤝 §0.76 審計（confirmed HIGH）：「溫柔**一點**/快**一點**」的程度副詞被吃成 1 點鐘 → 幻影 13:00 亂發。
        # 裸「一點」（無日詞/時段/分鐘）且前一字是程度形容詞 → 不是鐘點；「**晚**一點叫我」的 晚 被吃成時段詞
        # （審查 LOW-MED：晚一點＝待會，不是凌晨 1 點）→ part=晚＋hour=1＋無分鐘 也不排。
        if num_s == "一" and not day_word and not minute_s \
                and (part_word == "晚"
                     or (not part_word and t_clk[max(0, m.start() - 1):m.start()] in _DEGREE_ADVERB_PRE)):
            continue
        minute = 30 if minute_s in ("半", "三十分", "30分") else (_cn_to_int(minute_s.rstrip("分")) or 0) if minute_s else 0
        e = _hm_epoch(_cn_to_int(num_s), minute, day_word + part_word, local, force_day=rel_days)
        if e:
            eps.append(e)
    return sorted(set(eps))


# 🤝 §0.76：解析不了的日子限定詞（週幾/日期）——出現在鐘點**緊前方**時，那個鐘點不得照排今天（錯日）。
# 與 selfstate._TIMED_REQ_TIMEY 的守門詞表同步（這裡擋排程、那裡讓守門接住＝誠實請對方講明確時間）。
_UNSUPPORTED_DAY_RE = re.compile(r"(?:下+個?)?(?:週|周|星期|禮拜)[一二三四五六日天]|[0-9]{1,2}月[0-9]{1,2}[日號]|[0-9]{1,2}/[0-9]{1,2}")
# 程度副詞（…一點）：前一字是這些字＝「更X一點」的程度、不是 1 點鐘。
_DEGREE_ADVERB_PRE = set("柔軟快慢早晚多少好大小輕重穩靜暖甜兇狠嚴鬆緊高低長短近遠")


def next_clock_epoch(text, now, tz):
    """把『八點／晚上八點／十分鐘後／明天X點／3:30／15:30』解析成**下一個該時刻**的 epoch（秒）；解析不到回 None。
    純『八點』取今天 08:00／20:00 中第一個未來時刻；『晚上/下午X點』強制下午；『明天X點』固定明天；
    數字 H:MM 同規則（3:30 取今天 03:30/15:30 第一個未來、15:30 為 24h 制）。多時刻時回最早一個（多筆記下走 all_clock_epochs）。"""
    eps = all_clock_epochs(text, now, tz)
    return eps[0] if eps else None


def _longest_streak(dates):
    if not dates:
        return 0
    s = sorted(set(dates))
    best = cur = 1
    for i in range(1, len(s)):
        gap = (s[i] - s[i - 1]).days
        if gap == 1:
            cur += 1
            best = max(best, cur)
        elif gap > 1:
            cur = 1
    return best


def build(data, snapshot, tz, now=None):
    """回傳時間上下文 dict（給 build_memory_brief 用）。"""
    now = now or datetime.now(timezone.utc)
    records = data.get("records") or []
    rec_ts = sorted(t for t in (analyzer.parse_ts(r.get("ts")) for r in records) if t)
    local_now = now.astimezone(tz)

    last_write = rec_ts[-1] if rec_ts else None
    gap = human_gap(now - last_write) if last_write else None
    drought = bool(last_write and (now - last_write) >= timedelta(days=DROUGHT_DAYS))
    burst = sum(1 for t in rec_ts if t >= now - timedelta(minutes=BURST_WINDOW_MIN)) >= BURST_MIN_RECORDS

    # 節奏：慣常時段 + 平日/週末
    part_count, weekday_n, weekend_n = {}, 0, 0
    dates = []
    for t in rec_ts:
        lt = t.astimezone(tz)
        dates.append(lt.date())
        part_count[day_part(lt.hour)] = part_count.get(day_part(lt.hour), 0) + 1
        if lt.weekday() >= 5:
            weekend_n += 1
        else:
            weekday_n += 1
    rhythm_part = max(part_count, key=part_count.get) if part_count else ""
    if weekday_n or weekend_n:
        rhythm_weekday = ("週末居多" if weekend_n > weekday_n * 1.4
                          else "平日居多" if weekday_n > weekend_n * 1.4 else "平日週末都有")
    else:
        rhythm_weekday = ""
    longest = _longest_streak(dates)

    # 冷卻中的脈絡（still active 但久未碰）
    cooling = []
    for c in (data.get("contexts") or []):
        if c.get("status") not in ("context", "candidate"):
            continue
        lt = analyzer.parse_ts(c.get("lastTs"))
        if not lt:
            continue
        days = (now - lt).days
        if days >= COOL_DAYS:
            cooling.append((days, c))
    cooling.sort(key=lambda x: -x[0])
    cooling_lines = [f"〈{analyzer.context_title(c)}〉{d} 天沒回來" for d, c in cooling[:6]]

    # 週期回顧：一週前 / 一個月前的「今天」寫了什麼
    on_this_day = []
    for offset, label in ((7, "一週前的今天"), (30, "一個月前的今天")):
        day = (local_now - timedelta(days=offset)).date()
        hits = [r for r in records if (analyzer.parse_ts(r.get("ts")) or local_now).astimezone(tz).date() == day]
        if hits:
            sample = hits[0]
            tag = sample.get("topicLabel") or " ".join((sample.get("text") or "").split())[:24]
            on_this_day.append(f"{label}（{day.strftime('%m/%d')}）你在寫〔{tag}〕")

    return {
        "now": now,
        "now_local": (f"{local_now.strftime('%Y/%m/%d')}（{WEEKDAYS[local_now.weekday()]}）"
                      f"{local_now.strftime('%H:%M')}（{day_part(local_now.hour)}）"),
        "last_write_gap": gap,
        "drought": drought,
        "burst": burst,
        "streak": snapshot.summary.get("streak", 0),
        "longest_streak": longest,
        "rhythm_part": rhythm_part,
        "rhythm_weekday": rhythm_weekday,
        "cooling": cooling_lines,
        "on_this_day": on_this_day,
    }


def brief_sections(tc):
    """把時間上下文渲染成【時間感】區塊的數行。"""
    lines = ["【時間感】"]
    head = f"・現在 {tc['now_local']}"
    if tc.get("last_write_gap"):
        head += f"；距上次記寫 {tc['last_write_gap']}" + ("（已斷一段）" if tc.get("drought") else "")
    lines.append(head)
    rhythm = []
    if tc.get("rhythm_part"):
        rhythm.append(f"你多在「{tc['rhythm_part']}」記寫")
    if tc.get("rhythm_weekday"):
        rhythm.append(tc["rhythm_weekday"])
    streak_s = f"目前連續 {tc['streak']} 天（最長 {tc['longest_streak']} 天）" if tc.get("streak") else ""
    if streak_s:
        rhythm.append(streak_s)
    if tc.get("burst"):
        rhythm.append("剛剛是一波密集記寫")
    if rhythm:
        lines.append("・節奏：" + "；".join(rhythm))
    if tc.get("cooling"):
        lines.append("・冷卻中：" + "、".join(tc["cooling"]))
    for r in tc.get("on_this_day", []):
        lines.append("・時光回顧：" + r)
    # 🛡️ 內部用、別主動說出口：時間感只拿來抓語感／判讀意圖，不是回應內容。除非對方**問起時間／你的記寫節奏**，
    # 否則**別主動拿「現在是晚上、跟你早上記寫差好多」「距上次記寫多久」這種時間差當開場或話題**——
    # 那會變成答非所問的時間旁白（截圖：問聯想卻被回「我現在是晚上，跟早上你記寫的時間差好多喔」）。
    lines.append("・（以上時間感是給你**內部**判讀語感用的，不是話題：除非對方問起時間/你的節奏，"
                 "否則別主動開場提「現在幾點/跟你記寫差多久/你多在早上寫」這類時間差。）")
    return lines


# ── 時間範圍回想：把「上週/三天前/上個月…」解析成日期區間 ──────────────
def _day_bounds(d, tz):
    start = datetime(d.year, d.month, d.day, tzinfo=tz)
    return start, start + timedelta(days=1)


def match_time_range(query, now, tz):
    """從問句解析時間範圍 → (start_utc, end_utc, label)；解析不到回 None。"""
    if not query:
        return None
    q = query
    local = now.astimezone(tz)
    today = local.date()

    def out(s, e, label):
        return (s.astimezone(timezone.utc), e.astimezone(timezone.utc), label)

    nowplus = local + timedelta(seconds=1)   # 含「現在」這一刻
    # 滾動視窗：最近 N 小時 / N 小時前 / N 小時內、N 分鐘
    m = re.search(r"(\d+)\s*(?:個)?\s*小時", q)
    if m:
        h = int(m.group(1))
        return out(local - timedelta(hours=h), nowplus, f"最近 {h} 小時")
    m = re.search(r"(\d+)\s*分鐘", q)
    if m:
        mins = int(m.group(1))
        return out(local - timedelta(minutes=mins), nowplus, f"最近 {mins} 分鐘")
    # N 天內 / 最近 N 天（滾動窗，與單日的「N 天前」不同）
    m = re.search(r"(?:最近|過去|近)\s*(\d+)\s*天", q) or re.search(r"(\d+)\s*天\s*(?:之?內)", q)
    if m:
        d = int(m.group(1))
        return out(local - timedelta(days=d), nowplus, f"最近 {d} 天")
    # 「最近」沒帶數字 → 近 24 小時
    if ("最近" in q or "近期" in q) and not re.search(r"\d", q):
        return out(local - timedelta(hours=24), nowplus, "最近一天")

    m = re.search(r"(\d+)\s*天前", q)
    if m:
        d = today - timedelta(days=int(m.group(1)))
        s, e = _day_bounds(d, tz)
        return out(s, e, f"{m.group(1)} 天前（{d.strftime('%m/%d')}）")
    if "前天" in q:
        d = today - timedelta(days=2); s, e = _day_bounds(d, tz)
        return out(s, e, f"前天（{d.strftime('%m/%d')}）")
    if "昨天" in q:
        d = today - timedelta(days=1); s, e = _day_bounds(d, tz)
        return out(s, e, f"昨天（{d.strftime('%m/%d')}）")
    if "今天" in q:
        s, e = _day_bounds(today, tz)
        return out(s, e, f"今天（{today.strftime('%m/%d')}）")
    if any(k in q for k in ("上週", "上禮拜", "上星期")):
        mon = today - timedelta(days=today.weekday() + 7)
        s = datetime(mon.year, mon.month, mon.day, tzinfo=tz)
        return out(s, s + timedelta(days=7), f"上週（{mon.strftime('%m/%d')}起）")
    if any(k in q for k in ("這週", "本週", "這禮拜", "本禮拜", "這星期")):
        mon = today - timedelta(days=today.weekday())
        s = datetime(mon.year, mon.month, mon.day, tzinfo=tz)
        return out(s, local + timedelta(seconds=1), f"這週（{mon.strftime('%m/%d')}起）")
    if "上個月" in q or "上月" in q:
        first_this = today.replace(day=1)
        last_prev = first_this - timedelta(days=1)
        s = datetime(last_prev.year, last_prev.month, 1, tzinfo=tz)
        e = datetime(first_this.year, first_this.month, 1, tzinfo=tz)
        return out(s, e, f"上個月（{last_prev.strftime('%Y/%m')}）")
    if "這個月" in q or "本月" in q:
        first = today.replace(day=1)
        s = datetime(first.year, first.month, 1, tzinfo=tz)
        return out(s, local + timedelta(seconds=1), f"這個月（{first.strftime('%Y/%m')}）")
    m = re.search(r"(\d+)\s*個?月前", q)
    if m:
        months = int(m.group(1))
        y, mo = today.year, today.month - months
        while mo <= 0:
            mo += 12; y -= 1
        s = datetime(y, mo, 1, tzinfo=tz)
        ny, nmo = (y + 1, 1) if mo == 12 else (y, mo + 1)
        return out(s, datetime(ny, nmo, 1, tzinfo=tz), f"{months} 個月前（{y}/{mo:02d}）")
    return None


def records_in_range(records, start_utc, end_utc):
    out = []
    for r in records:
        t = analyzer.parse_ts(r.get("ts"))
        if t and start_utc <= t < end_utc:
            out.append((t, r))
    out.sort(key=lambda x: x[0])
    return [r for _, r in out]


def is_just_now(query):
    """問的是「剛剛/剛才/方才」這種「就在剛才那批」嗎？"""
    return bool(query and JUST_NOW_RE.search(query))


def session_when_phrase(end_local, now_local):
    """這段記寫**結束點離現在多久**的人類語感前綴。
    在 `JUST_NOW_RECENT_HOURS` 內＝「剛剛這段」；久了就用**時段相對**（今天早上那段／昨晚那段／前天午後那段／N 天前那段），
    免得 12 小時前、甚至跨日的那批還被叫「剛剛」（語感不準）。"""
    if now_local - end_local < timedelta(hours=JUST_NOW_RECENT_HOURS):
        return "剛剛這段"
    part = day_part(end_local.hour)
    day_diff = (now_local.date() - end_local.date()).days
    if day_diff <= 0:
        return f"今天{part}那段"
    if day_diff == 1:
        return "昨晚那段" if part in ("晚上", "深夜") else f"昨天{part}那段"
    if day_diff == 2:
        return f"前天{part}那段"
    return f"{day_diff} 天前那段"


def recent_session(records, now, tz, gap_min=RECENT_SESSION_GAP_MIN):
    """最近一段連續記寫：從最後一筆往回，停筆 < gap_min 視為同一段。
    回傳 (start_utc, end_utc, label, hits)；完全沒有記寫回 None。標籤前綴依**離現在多久**自動選
    「剛剛這段」或時段相對（今天早上／昨晚／前天…那段），所以隔了很久回頭問也不會誤稱「剛剛」。

    比「最近一天」（整 24h）貼近「剛才那批」——錨在最後一筆而非 now，所以
    「寫完一陣子才回頭問」也抓得到那批，而不是被中間的空檔稀釋成一整天。
    """
    tss = sorted(t for t in (analyzer.parse_ts(r.get("ts")) for r in records) if t)
    if not tss:
        return None
    gap = timedelta(minutes=gap_min)
    start = tss[-1]
    for i in range(len(tss) - 1, 0, -1):
        if tss[i] - tss[i - 1] < gap:
            start = tss[i - 1]
        else:
            break
    end = tss[-1] + timedelta(seconds=1)   # 含最後一筆
    a, b = start.astimezone(tz), tss[-1].astimezone(tz)
    span = (f"{a.strftime('%m/%d %H:%M')}–{b.strftime('%H:%M')}"
            if a.date() == b.date()
            else f"{a.strftime('%m/%d %H:%M')}–{b.strftime('%m/%d %H:%M')}")
    label = f"{session_when_phrase(b, now.astimezone(tz))}（{span}）"
    s_utc, e_utc = start.astimezone(timezone.utc), end.astimezone(timezone.utc)
    return (s_utc, e_utc, label, records_in_range(records, s_utc, e_utc))


def is_anaphoric(query):
    """問的是「那時候/當時/那次…」這種指代型時間（沒有絕對錨）嗎？"""
    return bool(query and ANAPHORA_RE.search(query))


def is_fresh_deixis(query):
    """問的是「新東西/最新/剛進來…」這種「最近那批新記寫」嗎？（接到 recent_session）"""
    return bool(query and FRESH_DEIXIS_RE.search(query))


def resolve_anaphoric(records, now, tz, anchor=None):
    """指代型時間（那時候/當時…）：優先接 anchor＝對話裡剛確立的 (start_utc,end_utc,label)；
    沒有就退「你最後一次記寫那段」（recent_session）——正好對應「我多久沒寫」剛說過的那個時間。
    回 (start, end, label, hits) 或 None（完全沒有記寫）。"""
    if anchor:
        s, e, base = anchor
        return (s, e, base, records_in_range(records, s, e))      # 沿用既有錨的標籤、不再包一層
    sess = recent_session(records, now, tz)
    if not sess:
        return None
    s, e, _base, hits = sess
    a = s.astimezone(tz)
    return (s, e, f"你最後一次記寫那段（{a.strftime('%m/%d %H:%M')}起）", hits)


def resolve_range(query, records, now, tz, anchor=None):
    """把問句解析成時間範圍並取出該段記寫 → (start_utc, end_utc, label, hits)；解析不到回 None。

    單一入口：「剛剛/剛才」走「最近一段連續記寫」；其餘（今天/昨天/上週/N 天前/最近…）走 match_time_range；
    「那時候/當時…」這種指代則接 anchor（對話剛確立的時間）或退「最後一次記寫那段」。
    """
    if is_just_now(query):
        return recent_session(records, now, tz)
    rng = match_time_range(query, now, tz)
    if rng:
        start, end, label = rng
        return (start, end, label, records_in_range(records, start, end))
    if is_anaphoric(query):
        return resolve_anaphoric(records, now, tz, anchor=anchor)
    return None
