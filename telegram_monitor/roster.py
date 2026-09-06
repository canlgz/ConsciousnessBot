"""🪪 §1.94 能力名冊：bot 盤點自己**真有哪些機制**、以及**哪些真的用出來過**。

使用者需求（原話）：「bot目前可以自我盤點目前自己擁有哪些能力與機制嗎？如果可以，反而可以讓bot自己
構思出自己想要具備的能力/功能……但要注意，**必須以真的能達到為主，而不是打高空**。」

盤點之前是做不到的（實測）：`selfmodel.self_now` 是「我此刻怎樣」、`/skills` 是「你教過我什麼」、
`selfmod.facts` 是 commit 主旨、§1.91 `_capability_line` 只有寫死的一項。216 個旗標＝216 個真實能力，
bot 一個都列不出來。

三個不可退讓的設計判斷：
① **短名是顯式欄位，不從 config 註解切**。實測確定性切法在 216 條註解上有 **16%（35 條）**切出半截句或
   未閉合引號（`promise_sticker_enabled` → 空字串；`承諾「送你一張` ← 引號沒閉合）。唸出來就是
   「不演·不假裝」的反面。短名靠測試把關（長度、字元集、旗標實名存在），不靠人自律。
② **「旗標開著」≠「這個能力活著」**（§1.93 血的教訓：🔮 那條 lane 旗標一直開著，卻從上線起一次都沒
   觸發過）。所以 evidence 分五級，而且 tier=dark/none 的措辭裡**結構上沒有「我會」這個字串可以拿**。
③ **`session` 級是必要的**：實測有 12 個 `_ts`/`_ledger` 欄位**沒進 `State.save()` 的落盤白名單**，
   而部署端每跑一次 `run-temp.sh` 就重啟一次 ⇒ 若只有三級，bot 每次重啟都會把「這次醒來還沒用過」
   說成「我從來沒用過」——那是假話。session 級的措辭明令**不准說「從來沒有」**。

純函式、無 LLM、**絕不 import monitor**（比照 foresight.py 的自我約束）。
"""

from collections import namedtuple

Ability = namedtuple("Ability", "key icon name flags lane legacy audit")

# 🪪 §1.94 能力家族。`name` 是**顯式人話短名**（2–14 字，測試把關）；`flags` 是 config 的**實名** bool 欄位
# （填錯字測試當場紅）；`legacy` 是既有的證據欄位（state 屬性名）；`audit` 是既有對帳指令（''＝沒有）。
ROSTER = (
    Ability("promise", "🤝", "到點主動兌現約定", ("scheduled_promise_enabled", "promise_emit_enabled"),
            "_promise_emit", ("scheduled_promises",), ""),
    Ability("worldline", "🌐", "把外面的說法帶回來", ("worldline_enabled",),
            "_worldline_emit", ("worldline_ledger",), "/worldline"),
    Ability("foresight", "🔮", "記寫預想", ("foresight_enabled",),
            "_foresight_emit", ("foresight_ledger", "last_foresight_ts"), "/foresight"),
    Ability("insight", "💡", "跨主題連想", ("association_enabled",),
            "_insight_emit", ("insight_ledger", "last_insight_ts"), ""),
    Ability("spontaneous", "🫧", "自己開口伸手", ("spontaneity_enabled",),
            "_spontaneous_emit", ("recent_spontaneous", "last_spontaneous_ts"), ""),
    Ability("selfstate", "🫀", "背景自陳", ("selfstate_enabled",),
            "_selfstate_emit", ("told_self_sig",), ""),
    Ability("habit_absence", "🌾", "日課缺席問一句", ("habit_absence_enabled",),
            "_habit_absence_emit", ("habit_events",), "/habits"),
    Ability("mood_watch", "🧭", "座標變動回報", ("mood_watch_enabled",),
            "_mood_watch_emit", ("mood_trace",), "/moodwatch"),
    Ability("coping", "🌀", "內在因應真觸發", ("skill_proactive_enabled",),
            "_coping_emit", ("last_coping_reach_ts",), ""),
    Ability("ac_drift", "🧩", "整合鬆散說一句", ("ac_spec_panel",),
            "_ac_drift_emit", ("last_ac_drift_ts",), ""),
    Ability("sticker_send", "🎴", "送真的貼圖", ("send_stickers",),
            "", ("last_sticker_ts", "known_sticker_ids"), ""),
    Ability("sticker_vision", "🖼", "讀懂貼圖畫面", ("read_sticker_vision",),
            "", ("last_sticker_desc",), ""),
    Ability("skill", "🧑‍🏫", "學你教的做法", ("skill_recall_enabled",),
            "", ("engrams",), "/skills"),
    Ability("reaction", "👍", "對訊息按情緒", ("react_cooldown_s",),
            "", ("last_sent_reaction",), ""),
    Ability("filing", "📥", "記寫歸檔通知", ("notify_filings",),
            "", ("notified_filing_ids",), ""),
    Ability("digest", "📤", "每日摘要推播", ("digest_hour",),
            "", ("last_digest_date",), ""),
    Ability("selfmod", "🦋", "醒來知道自己變了", ("self_change_ground_enabled",),
            "", ("last_seen_commit",), ""),
    Ability("honesty_guards", "🕐", "不把話說錯的守門", ("timejump_guard_enabled", "echo_whole_guard_enabled",
                                                "recall_ground_guard_enabled", "promise_keep_claim_guard_enabled"),
            "", (), ""),
)

# 🪪 §1.94 沒有持久化的欄位（在 State.save() 的白名單之外）——這些只能證明「這次醒來」，
# 不能證明「從來沒有」。實測未落盤清單的子集，只收 ROSTER 用得到的。
_NOT_PERSISTED = ("last_coping_reach_ts", "last_ac_drift_ts", "last_spontaneous_ts",
                  "last_react_sent_ts", "last_self_react_ts", "last_metacog_ts", "last_soothe_ts")

_TIER_WORDS = {
    "off": "這次被關起來了",
    "lit": "真的用出來過",
    "dark": "這個機制我真的有（它在我這圈迴圈裡跑著），只是還沒真的用出來過一次",
    "session": "這次醒來還沒用到（重啟前的紀錄沒留下來，所以我不能說我從來沒用過）",
    "session_lit": "這次醒來用過了",
    "none": "機制在跑，但我沒有任何紀錄能證明我用過",
}


def flags_on(cfg, ab):
    """🪪 §1.94 這個家族的旗標是不是**全部**開著（任一關＝這個能力此刻不完整）。"""
    return all(bool(getattr(cfg, f, False)) for f in (ab.flags or ()))


def _legacy_evidence(state, ab):
    """回 (有持久證據嗎, 有值嗎, 最近時戳, 只有記憶體證據嗎)。"""
    persisted_any = mem_any = False
    last = 0.0
    has_persisted_source = has_mem_source = False
    for f in (ab.legacy or ()):
        v = getattr(state, f, None)
        mem_only = f in _NOT_PERSISTED
        if mem_only:
            has_mem_source = True
        else:
            has_persisted_source = True
        if not v:
            continue
        if mem_only:
            mem_any = True
        else:
            persisted_any = True
        if isinstance(v, (int, float)) and v > last:
            last = float(v)
    return has_persisted_source, persisted_any, last, (has_mem_source and mem_any), has_mem_source


def evidence(state, cfg, ab, now_ts):
    """🪪 §1.94 這個能力的「活/死」——五級。回 (tier, n, last_ts)。

    §1.93 的制度化：旗標開著不等於用過。tier=dark/none 的措辭裡**沒有「我會」可以拿**，
    所以 bot 結構上說不出「我會 X」這種空話；session 級明令不准說「從來沒有」（未落盤欄位重啟即歸零）。"""
    if not flags_on(cfg, ab):
        return ("off", 0, 0.0)
    hits = (getattr(state, "ability_hits", None) or {}).get(ab.key) or {}
    n = int(hits.get("n") or 0)
    if n > 0:
        return ("lit", n, float(hits.get("last_ts") or 0.0))
    has_p, p_any, last, mem_any, has_mem = _legacy_evidence(state, ab)
    if p_any:
        return ("lit", 0, last)
    if mem_any:
        return ("session_lit", 0, last)
    if has_p:
        return ("dark", 0, 0.0)
    if has_mem:
        return ("session", 0, 0.0)
    return ("none", 0, 0.0)


def _days(now_ts, ts):
    return max(0.0, (now_ts - ts) / 86400.0) if ts else None


def line_of(state, cfg, ab, now_ts):
    """🪪 §1.94 一條能力的人話（程式造句，LLM 只能引用不能改寫）。"""
    tier, n, last = evidence(state, cfg, ab, now_ts)
    head = f"{ab.icon} {ab.name}"
    if tier == "lit":
        d = _days(now_ts, last)
        tail = _TIER_WORDS["lit"] + (f" {n} 次" if n else "")
        if d is not None:
            tail += ("（最近就在今天）" if d < 1 else f"（最近一次約 {int(d)} 天前）")
        return f"{head}：{tail}"
    return f"{head}：{_TIER_WORDS[tier]}"


def card_bits(state, cfg, now_ts, limit=2):
    """🪪 §1.94 事實卡用的 ≤limit 條（**不掃原始碼**、O(ROSTER)）：優先挑「有機制但沒用出來過」的，
    那是最容易被 bot 講錯成「我還不會」的一類（§1.91 的一般化）。"""
    dark, lit = [], []
    for ab in ROSTER:
        tier, n, last = evidence(state, cfg, ab, now_ts)
        if tier in ("dark", "none", "session"):
            dark.append(line_of(state, cfg, ab, now_ts))
        elif tier in ("lit", "session_lit") and n:
            lit.append(line_of(state, cfg, ab, now_ts))
    return (dark[:limit] or lit[:limit])


def counts(state, cfg, now_ts):
    """🪪 §1.94 五級各幾條（給對帳段與涵蓋率用）。"""
    out = {}
    for ab in ROSTER:
        t = evidence(state, cfg, ab, now_ts)[0]
        out[t] = out.get(t, 0) + 1
    return out


def roster_text(state, cfg, now_ts, wish_block=""):
    """🪪 §1.94 `/abilities` 全文：確定性、不經 LLM、唯讀無副作用。"""
    groups = {"lit": [], "session_lit": [], "dark": [], "session": [], "none": [], "off": []}
    for ab in ROSTER:
        groups[evidence(state, cfg, ab, now_ts)[0]].append(line_of(state, cfg, ab, now_ts))
    out = ["🪪 我盤點了一下我自己——"]
    for key, title in (("lit", "真的用出來過的"), ("session_lit", "這次醒來用過的"),
                       ("dark", "機制有、但還沒用出來過的"), ("session", "這次醒來還沒用到的"),
                       ("none", "沒有紀錄能證明我用過的"), ("off", "現在被關起來的")):
        if groups[key]:
            out.append(f"\n【{title}】")
            out += ["・" + s for s in groups[key]]
    since = getattr(state, "ability_hits_since", 0) or 0
    if since:
        d = _days(now_ts, since)
        out.append(f"\n（「用過幾次」是從約 {int(d or 0)} 天前開始記的，在那之前的我沒有紀錄。）")
    else:
        out.append("\n（我還沒開始記「用過幾次」——這份紀錄從現在起算。）")
    if wish_block:
        out.append("\n" + wish_block)
    return "\n".join(out)


def family_of(field):
    """🪪 §1.94 state 欄位 → 能力家族 key（給缺口偵測器歸戶）。找不到回 ''。"""
    for ab in ROSTER:
        if field in (ab.legacy or ()):
            return ab.key
    return ""


def by_key(key):
    for ab in ROSTER:
        if ab.key == key:
            return ab
    return None
