"""對話時機感：把「提問↔回覆之間的時間」讀成一個內在擾動＋語氣染色。

設計取向＝**只內化、不明講**：時機會真的影響 bot 的內在感覺（注入內在熵的電量 C、
讓 S 起伏、`/status` 看得到）與**下一則回覆的語氣**，但 bot **絕不**把時機本身講出來
（不會說「我們撞到了」「抱歉慢了」「好久不見」這類話）。

純函式、可測；不依賴 Drive/Telegram。只看時間差、不看內容。三種時機事件：
- `crossed`：bot 剛主動開口、和對方訊息撞在一起（秒級重疊）——雙方「講話交錯」的尷尬；
- `reunion`：對方久違才又開口——一陣久別重逢的暖；
- `slow`  ：對方訊息已在隊列等了一陣才被回——「我回得有點慢」的小在意。
同時只取一個最顯著的 mood（crossed > reunion > slow）。
"""

# 交錯判定窗（秒）：delta = 對方訊息時間 − bot 上次主動開口時間。
#   delta < 0：對方在 bot 開口前送出 → bot 講過去了（交錯）。
#   delta ≈ 0：幾乎同時（交錯）。
#   0 < delta ≤ _CROSS_FWD：對方在 bot 開口後極短時間就送出，來不及讀完才回 → 仍算撞上。
#   delta > _CROSS_FWD：對方多半讀了才回 → 正常回覆，不算交錯。
_CROSS_BACK = 8.0     # bot 在對方送出後最多這麼多秒內才開口（對方正打字就被講過去）→ 交錯
_CROSS_FWD = 2.0      # 對方在 bot 開口後這麼多秒內就送出（來不及讀）→ 仍算同時撞上
_REUNION_GAP_S = 6 * 3600.0   # 距上次對話 ≥ 這麼久才又開口 → 久別重逢
_SLOW_REPLY_S = 90.0          # 對方訊息已等 ≥ 這麼久才被處理 → 回得有點慢

# 各事件注入內在熵電量 C 的擾動量（溫和：比「新記寫」小、和自我刺激同量級）。
_CHARGE = {"crossed": 0.35, "reunion": 0.30, "slow": 0.15}


def read_tempo(user_msg_ts, now_ts, last_user_msg_ts=0.0, last_proactive_ts=0.0):
    """這則使用者訊息的時機 →（mood, charge）。

    - user_msg_ts：這則訊息送出的時間（epoch，取自 Telegram message.date）。
    - now_ts：現在處理它的時間（epoch）——與 user_msg_ts 的差＝它等了多久才被回。
    - last_user_msg_ts：對方上一則訊息時間（算久別重逢）。
    - last_proactive_ts：bot 上次「主動開口」時間（= state.last_push_ts；算交錯）。

    mood ∈ {None,'crossed','reunion','slow'}；同時只回一個最顯著的。charge 給呼叫端注入熵。
    """
    if last_proactive_ts:
        delta = user_msg_ts - last_proactive_ts
        if -_CROSS_BACK <= delta <= _CROSS_FWD:
            return "crossed", _CHARGE["crossed"]
    if last_user_msg_ts and (user_msg_ts - last_user_msg_ts) >= _REUNION_GAP_S:
        return "reunion", _CHARGE["reunion"]
    if (now_ts - user_msg_ts) >= _SLOW_REPLY_S:
        return "slow", _CHARGE["slow"]
    return None, 0.0


# 語氣染色提示（附加到系統提示）：可染情緒，但**嚴禁明說時機本身**——這是「只內化」的核心。
_MOOD_HINT = {
    "crossed": "〔對話時機・只內化不可明說〕你剛好和對方的訊息錯開、講話有點重疊了。"
               "自然地把注意力收回到對方這句、先好好回應，語氣可帶一絲不好意思；"
               "但**絕對不要明講**「我們撞到了／我剛在講別的／我們同時開口」這類話。",
    "reunion": "〔對話時機・只內化不可明說〕對方久違才又開口。"
               "語氣可以多一點點溫度與在意；但**不要明講**「好久不見／你終於回來／這麼久沒聊」這類話。",
    "slow":    "〔對話時機・只內化不可明說〕這則你回得有點慢。"
               "直接給重點、語氣俐落些；但**不要道歉、也不要提到延遲或久等**。",
}


def mood_hint(mood):
    """mood → 附加到系統提示的語氣指引（只染語氣、嚴禁明說時機本身）。無事件回 ''。"""
    return _MOOD_HINT.get(mood or "", "")
