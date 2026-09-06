"""對話耦合（觀測層）：把 bot 與使用者的對話讀成兩個耦合的「意向性」純量，掛在生命迴圈上。

構想（使用者提）：對話＝bot 與使用者各自意向性的動態耦合——
- ``I_bot∈[0,1]``：bot 的對話意向性，**由使用者觸發拉高**，沒被回就**隨時間遞減**（耐心，不是秒放棄）。
- ``Î_user∈[0,1]``：對使用者意向性的**估計**（bot 看不到對方腦袋，只能從可觀測訊號推：有沒有回、回得多快/
  多實、有沒有把球丟回來、有沒有收尾語）。沉默越久估計越往 0 掉。
- 這一輪對話「活著」⟺ ``max(I_bot, Î_user) > eps``；雙方皆 < eps ⟺ 這輪收掉（＝「遞迴檢視雙方皆 0」，
  其實就掛在生命迴圈每脈動重評）。收尾分品質：理解性（懂了/有道理）｜離開（晚安/先去忙）｜被晾（線還開著人卻消失）。

狀態接進 ``/status`` 觀測；**活著時 bot 壓住自己的 proactive 雜訊**（🫀/🫧/🌀 讓路，見 ``monitor._in_live_round``）。

遞迴回應的**「結束關鍵」**（使用者提）：
1. **已讀未回一定的時間**——註：Telegram bot **看不到『已讀』回執**，操作上＝「bot 說了、T 時間內沒回」；
   沉默後 I_bot 隨半衰期衰到 eps，那段時間就是「一定的時間」。
2. **使用者的再回應會增或減 bot 意向性，趨近 0 為止**——**開場**才高觸發；**進行中的再回應**依「更投入↑/
   更冷淡↓」調 I_bot（不再 max-slam），所以一連串敷衍/收尾的再回應會讓 I_bot **收斂到 0**、這輪自然收掉。

純函式、可單測、與迴圈轉速無關（時間制衰減）。情緒耦合與「收編 🌬️ soothe」仍待後續。
"""

from dataclasses import dataclass

from . import selfstate

# 動力學常數（先抓的預設，觀測後再調）——時間制（半衰期），與迴圈轉速無關。
_EPS = 0.12                 # 低於此＝視為 0
_BOT_TRIGGER = 0.9          # **開場**：使用者主動接觸 → I_bot 拉到這（被觸發，不看這則多短）
_BOT_HALFLIFE_S = 10 * 60   # 被晾時 I_bot 的半衰期（約 10 分；I_bot 0.9→eps 約半小時＝耐心後鬆手）
_USER_HALFLIFE_S = 4 * 60   # Î_user 衰得快些（bot 對「你還在」的相信，比它自己的意向性退得快）
# **進行中的再回應**：依「更投入↑/更冷淡↓」調 I_bot（不再 max-slam）→ 敷衍/收尾的再回應可讓 I_bot 趨近 0。
_REINVEST_NEUTRAL = 0.5     # 中性參與基準：再回應投入度高於此＝更投入(I_bot↑)、低於此＝更冷淡(I_bot↓)
_REINVEST_GAIN = 0.6        # 把（投入度−中性）放大成 I_bot 的增減量
_CLOSING_PULL = 0.3         # 收尾語（懂了/晚安）額外把 I_bot 往下拉（讀出對方要收）

# 收尾品質 → 心情（A1）：被晾＝失落微 downer；理解性收尾＝平/微暖；離開＝中性。base，呼叫端再 ×MOOD_GAIN。
_CLOSURE_MOOD = {"ignored": -0.12, "understanding": 0.06, "leaving": 0.0}


def closure_mood_delta(kind):
    """這一輪『怎麼收的』對心情 V 的 base 增量（呼叫端再 ×MOOD_GAIN）。"""
    return _CLOSURE_MOOD.get(kind, 0.0)


@dataclass
class Coupling:
    """一輪對話的耦合狀態（記憶體、重啟歸零）。"""
    i_bot: float = 0.0
    i_user: float = 0.0
    round_open: bool = False
    opened_ts: float = 0.0
    closed_ts: float = 0.0
    last_msg_ts: float = 0.0
    last_update_ts: float = 0.0
    pending_closing: str = None    # 本輪最近一次收尾訊號：'understanding'|'leaving'|None
    last_closure: str = None       # 上一輪怎麼收的：'understanding'|'leaving'|'ignored'|None
    just_closed: str = None        # 這次 observe 剛把一輪收掉的品質（給呼叫端染情緒一次後清掉；A1）

    def snapshot(self):
        return {"i_bot": round(self.i_bot, 3), "i_user": round(self.i_user, 3),
                "round_open": self.round_open, "last_closure": self.last_closure}


def estimate_user_intent(text, latency_s=None):
    """從可觀測訊號估計 Î_user∈[0,1]：有回就有底；實質/反問↑、太短/收尾↓、回得快↑慢↓。
    刻意是**估計**（bot 看不到對方腦袋）——這是 bot 對另一個心智的模型，不是讀心。"""
    t = (text or "").strip()
    v = 0.6                                   # 有回應＝有一定意向（起點）
    n = len(t)
    if n >= 12:
        v += 0.2                              # 回得實
    elif n <= 3:
        v -= 0.25                             # 「嗯」「喔」「好」這種低投入
    if t.endswith(("?", "？")):
        v += 0.2                              # 把球丟回來＝再投資
    if selfstate.closing_kind(t):
        v -= 0.5                              # 收尾語＝主動歸零
    if latency_s is not None:
        if latency_s < 60:
            v += 0.1                          # 秒回＝熱
        elif latency_s > 30 * 60:
            v -= 0.2                          # 拖很久＝在降
    return max(0.0, min(1.0, v))


def _advance(c, now_ts):
    """把時間推進到 now_ts：活著的一輪，I_bot/Î_user 各依半衰期衰減（時間制、與轉速無關）。"""
    dt = max(0.0, now_ts - (c.last_update_ts or now_ts))
    if c.round_open and dt > 0:
        c.i_bot *= 0.5 ** (dt / _BOT_HALFLIFE_S)
        c.i_user *= 0.5 ** (dt / _USER_HALFLIFE_S)
    c.last_update_ts = now_ts


def _reinvest_delta(text, iu):
    """進行中的再回應對 bot 意向性的增減：投入（iu>中性）↑、冷淡（iu<中性）/收尾↓——讓一連串敷衍能收斂到 0。"""
    d = (iu - _REINVEST_NEUTRAL) * _REINVEST_GAIN
    if selfstate.closing_kind(text):
        d -= _CLOSING_PULL
    return d


def engage(c, text, now_ts, latency_s=None):
    """使用者說話＝一次互動。**開場**：bot 被觸發投入（高，不看這則多短）。**進行中的再回應**：依這則
    『更投入↑/更冷淡↓』調 I_bot（不再 max-slam）——所以一連串敷衍/收尾的再回應會讓 I_bot **收斂到 0**、這輪自然收掉。"""
    _advance(c, now_ts)
    iu = estimate_user_intent(text, latency_s)
    ck = selfstate.closing_kind(text)
    if not c.round_open:                      # 開新一輪：使用者主動接觸 → bot 被觸發投入
        c.round_open = True
        c.opened_ts = now_ts
        c.pending_closing = None
        c.i_bot = _BOT_TRIGGER
    else:                                     # 進行中：再回應讓 bot 意向性增或減（相對中性參與）→ 可趨近 0
        c.i_bot = max(0.0, min(1.0, c.i_bot + _reinvest_delta(text, iu)))
    c.i_user = iu
    c.pending_closing = ck or c.pending_closing
    c.last_msg_ts = now_ts
    return c


def observe(c, now_ts):
    """每脈動（或讀 /status 前）推進衰減；雙方皆 < eps → 收掉這一輪、記下收尾品質。回傳 c。"""
    _advance(c, now_ts)
    if c.round_open and max(c.i_bot, c.i_user) < _EPS:
        c.round_open = False
        c.closed_ts = now_ts
        c.last_closure = c.pending_closing or "ignored"   # 有收尾語＝理解/離開；否則＝被晾
        c.just_closed = c.last_closure                     # 給呼叫端染情緒一次（A1）；消費後清掉
        c.pending_closing = None
    return c


_CLOSURE_ZH = {"understanding": "理解性收尾", "leaving": "離開", "ignored": "被晾"}


def status_line(c, now_ts):
    """/status 的一行（先推進到現在再顯示）：I_bot／Î_user／這輪活著沒／上輪怎麼收的。"""
    observe(c, now_ts)
    if c.round_open:
        held = int((now_ts - (c.opened_ts or now_ts)) / 60)
        state = f"這輪活著（開 {held} 分）"
    else:
        state = "這輪已收"
    tail = f"｜上輪收尾：{_CLOSURE_ZH.get(c.last_closure, '—')}" if c.last_closure else ""
    return f"・對話耦合（觀測）：I_bot={round(c.i_bot, 2)}／Î_user={round(c.i_user, 2)}・{state}{tail}"
