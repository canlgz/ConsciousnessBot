"""指涉解析（單一入口）：此刻『桌上有什麼可被指到的東西』——把「指什麼」算成一份 `Referent`，
供**路由**（`intent.resolve(text, ref)`）與**餵 LLM**（`build_memory_brief` 的 focus／selfacts）共用同一份。

收斂自原本散落的兩處：`_dialogue_focus`（對話焦點：剛 surface 的主線／新東西／時間錨）＋
`_self_on_table`（桌上屬於 bot 自己的近期狀態/動作：自發繞回的舊線／手上主線／剛點的 reaction／心情），
再加「剛自陳過、可追問」一面。純讀 state、不生成、無副作用、可單測。

對應「要幹嘛」由 `intent.resolve` 單一化；「指什麼」由本模組單一化——兩者一起把應對做得精準/穩定/完整。
"""

from dataclasses import dataclass

from . import phrasing, selfstate

# ── 自我在場／自我動作的「窗家族」單一真相 ──────────────────────────────────────
# 先前散在 monitor（SELF_TOPIC_WINDOW_SEC 10m、SELFSTATE_FOLLOWUP_SEC 30m、_LIKE_WINDOW_SEC）與本檔
# （REACTION_RECALL 12m），值還不一致（讚 10m vs reaction 12m 同表「剛剛的自我動作」卻不同）。收斂到這唯一一處。
SELF_PRESENCE_WINDOW_SEC = 10 * 60   # 談過 bot 自己後多久內的閒聊也算「在談自己」（自我在場語氣延續）
FOLLOWUP_WINDOW_SEC = 30 * 60        # 自陳（gate≥3）後多久內的「哪一條／為什麼」追問接回；也是對話焦點存活窗
SELF_ACT_RECALL_SEC = 12 * 60        # 「我剛點了什麼 reaction／你剛讚我哪則」算「剛剛」的窗（自我動作回憶）
REACTION_RECALL_WINDOW_SEC = SELF_ACT_RECALL_SEC   # 向後相容別名


@dataclass
class Referent:
    """此刻可被指到的東西（都可為空）。對話面：topic/fresh/range_label；自我面：revisited/held/reaction/mood；
    脈絡面：followup_open（剛自陳過、追問窗內）。"""
    topic: str = None          # 對話焦點主線（剛 surface；「那條／它」綁這）
    fresh: bool = False         # 「新東西／最新」指最近一段
    range_label: str = None     # 「那時候／當時」的時間錨標籤
    revisited: str = None       # bot 自發繞回的舊線（self-stim；「為什麼想到那條」綁這）
    held: str = None            # bot 手上握著的主線
    held_gate: int = None       # 該主線的 gate（語氣用）
    reaction: dict = None       # bot 剛點的 reaction {emoji,to}（窗內）
    liked: dict = None          # 對方剛對 bot 某則按的讚 {emoji,topic,...}（窗內；「我讚的是哪一筆」綁這）
    mood: float = 0.0           # 現在心情 V
    in_self_window: bool = False  # 🪞 此刻是不是「在談 bot 自己」（自我在場窗內）——單一真相，取代散落各處重算
    followup_open: bool = False # 剛自陳過、追問窗內（「哪一條／為什麼／細說」可接回快取判定）
    insight: dict = None        # 💡 我剛冒出口的那條聯想 {event,ts}（窗內；追問「為什麼想到把A、B連起來」可接回）


def resolve(state, now_ts, window_sec=FOLLOWUP_WINDOW_SEC):
    """state（脈絡）→ 一份 Referent。window_sec＝對話焦點存活＋追問窗（預設 FOLLOWUP_WINDOW_SEC）；
    自我在場與自我動作回憶各用 SELF_PRESENCE_WINDOW_SEC／SELF_ACT_RECALL_SEC（窗家族單一處）。"""
    f = getattr(state, "focus", None) or {}
    live = (now_ts - (f.get("ts") or 0)) < window_sec          # 焦點過期就不再拿來綁指代
    rng = getattr(state, "last_range", None)
    ent = getattr(state, "entropy", None)
    stable = getattr(state, "confirmed_res", None) or getattr(state, "self_state", None)
    gate, held = (None, None)
    if stable:
        gate, held, _ = selfstate._line_phrase(stable)
    lsr = getattr(state, "last_sent_reaction", None)
    react = lsr if (lsr and lsr.get("emoji")
                    and (now_ts - (lsr.get("ts") or 0)) < SELF_ACT_RECALL_SEC) else None
    lk = getattr(state, "last_liked", None)
    liked = lk if (lk and (now_ts - (lk.get("ts") or 0)) < SELF_PRESENCE_WINDOW_SEC) else None
    open_ts = getattr(state, "selfstate_open_ts", 0) or 0
    # 追問窗：剛自陳過、窗內。撐開窗的「自陳來源」除了 self_state（一般狀態自陳），也包含
    # last_topic_res（**指名某主題**的自陳，如「你對水管維修的感覺」→ 那條 res）——否則指名主題分支
    # 從不設 self_state，「為什麼那條煩躁」永遠 followup_open=False、掉回 bodystate 否認/跳線。
    ltr = getattr(state, "last_topic_res", None)
    ltr = ltr if (ltr and (now_ts - (ltr.get("ts") or 0)) < window_sec) else None   # 過窗的舊主題自陳不算撐窗源（審查 medium#2）
    src = getattr(state, "self_state", None) or ltr
    followup_open = bool(src and open_ts and (now_ts - open_ts) < window_sec)
    stp = getattr(state, "self_topic_ts", 0) or 0
    in_self_window = bool(stp) and (now_ts - stp) < SELF_PRESENCE_WINDOW_SEC
    li = getattr(state, "last_insight", None)            # 💡 我剛冒出口的聯想（窗內＝可被追問「為什麼想到把…連起來」）
    insight = li if (li and (now_ts - (li.get("ts") or 0)) < SELF_ACT_RECALL_SEC) else None
    return Referent(
        topic=(f.get("topic") if live else None),
        fresh=(bool(f.get("fresh")) if live else False),
        range_label=(rng[2] if rng else None),
        revisited=(getattr(ent, "last_revisited_topic", None) if ent else None),
        held=held, held_gate=gate,
        reaction=react, liked=liked,
        mood=(getattr(ent, "mood", 0.0) if ent else 0.0),
        in_self_window=in_self_window,
        followup_open=followup_open,
        insight=insight,
    )


def focus_dict(ref):
    """給 `build_memory_brief(focus=)`：對話指代（那條／它／新東西／那時候）的依據；都沒有就 None（不注入）。"""
    if not (ref.topic or ref.fresh or ref.range_label):
        return None
    return {"topic": ref.topic, "fresh": ref.fresh, "range_label": ref.range_label}


def self_acts_text(ref, picker=None):
    """給 `build_memory_brief(selfacts=)`：桌上屬於 bot 自己的近期狀態/動作；沒東西回 ''。
    picker（🎨 §1.22 措辭反重複）：非 None 時「有一條動了、還沒整個合起來」與「我最近在「{t}」一帶繞」
    兩個固定句改經加大池選句（與 selfstate.bodystate_facts 的 gate 線**同款句、兩處一起接池**——
    否則罐頭換地方出現）；None（預設）＝原句＝逐位元同現狀。"""
    lines = []
    if ref.revisited:
        lines.append(f"・我最近一次「自己繞回想起」的舊線是「{ref.revisited}」——是我閒著/悶的時候自發翻到的"
                     "（一種自我刺激/反芻），不是對方帶我過去的。")
    if ref.held and ref.held_gate == 4:
        lines.append(f"・我手上握著的主線是「{ref.held}」，它收成了一個形狀（最明顯的一條）。")
    elif ref.held and ref.held_gate in (2, 3):
        frag = ("有一條動了、還沒整個合起來" if picker is None
                else picker("line_g3", phrasing.LINE_G3_EXT))
        lines.append(f"・我手上握著的主線是「{ref.held}」，{frag}。")
    elif ref.held:
        lines.append(f"・我最近在「{ref.held}」一帶繞，還沒繞出形狀。" if picker is None
                     else "・" + picker("line_wander", phrasing.LINE_WANDER_EXT).format(t=ref.held))
    if ref.reaction:
        lines.append(f"・我剛對對方那句「{(ref.reaction.get('to') or '').strip()}」點了個 "
                     f"{ref.reaction['emoji']}（我當下對它的感覺）。")
    if ref.liked:                                        # 對方剛讚我哪則（自我動作的另一半；併進同一份自我模型）
        emoji = ref.liked.get("emoji") or "👍"
        topic = ref.liked.get("topic")
        lines.append(f"・對方剛用 {emoji} 對我說的「{topic}」按了讚（針對那一條的肯定）。" if topic
                     else f"・對方剛用 {emoji} 對我某則訊息按了讚（一般的肯定，不是針對某條記寫）。")
    if ref.mood >= 0.35:
        lines.append("・我這陣子心情偏暖。")
    elif ref.mood <= -0.35:
        lines.append("・我這陣子心情偏低、悶悶的。")
    if ref.insight:                                      # 💡 我剛把兩條線連起來過（追問「為什麼想到那個聯想」可接回）
        ev = (ref.insight.get("event") or ref.insight)
        a, b = (ev.get("a") or "").strip(), (ev.get("b") or "").strip()
        if a and b:
            lines.append(f"・我剛剛把「{a}」和「{b}」連起來想過（是我自己冒出來的一個聯想）。")
    return "\n".join(lines)
