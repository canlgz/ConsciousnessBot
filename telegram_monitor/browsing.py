"""🫧 翻閱當下：報「自己對記寫的感覺」時，要講哪條線從『恆最重那則』改成『此刻剛好翻到的那則』。

像 bot 的生命迴圈一直在一頁頁翻記寫，使用者一問，剛好翻到第幾頁就分享那則——有隨機性、也有當下意識感，
打破「永遠聚焦最重那則」的千篇一律。純函式（pick／opener_variant），無 IO／LLM、可單測；
被 monitor 在 self_state 一般狀態分支於 inmoment_browsing_enabled 開時呼叫。預設關＝逐位元同現狀。
"""

# 翻閱開頭措辭池（換句、不千篇一律；輸出**不含數字**，守不報數字家規）
_OPENERS = (
    "我正一頁頁翻著，這會兒手上是",
    "剛好繞到",
    "翻著翻著，這會兒停在",
    "此刻翻到的是",
)


def pick(topics, cursor, random_p=0.0, rng=None):
    """在真實主題清單上選『此刻翻到的那條』：輪替為主（cursor 取模），random_p>0 且有注入 rng 時以小機率隨機跳頁。
    回 (topic, next_cursor)。空清單回 (None, cursor)。純函式——random_p=0＝確定性純輪替；rng 注入＝可測。"""
    ts = [t for t in (topics or []) if t]
    if not ts:
        return (None, cursor)
    c = int(cursor or 0)
    idx = c % len(ts)
    if random_p and rng is not None and rng.random() < random_p:
        idx = rng.randrange(len(ts))            # 小機率隨機跳頁（『翻著翻著跳了一頁』的驚喜）
    return (ts[idx], c + 1)


def opener_variant(topic, gate, lap, n=4):
    """在 n 種翻閱開頭措辭間以確定性方式輪替（同 (topic,gate,lap) 穩定、跨輸入會變、輸出不含數字）。
    用字元碼總和而非 hash()＝不受 PYTHONHASHSEED 影響、跨程序可重現、測試穩定。"""
    n = max(1, min(int(n or 1), len(_OPENERS)))
    base = sum(ord(c) for c in str(topic)) + int(gate or 0) + int(lap or 0)
    return _OPENERS[base % n]
