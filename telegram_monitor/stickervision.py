"""🎴 貼圖視覺解讀的純判斷／整形小工具。

Telegram 貼圖分三型：靜態 .webp（可直接餵視覺）、動態 .tgs（Lottie，需算圖）、影片 .webm（需抽格）。
本層只做「能不能直接讀畫面」的判斷、inline mime 對應、與描述整形；**不碰網路、可測**。
真正的下載＋視覺呼叫在 monitor 的 `_describe_sticker`（IO）、成本記在 coach.read_sticker_image。
"""


def is_static(sticker):
    """這張貼圖是不是**靜態**（可直接餵視覺）：非動態且非影片。缺欄位＝當靜態（多數貼圖是靜態 webp）。"""
    s = sticker or {}
    return not s.get("is_animated") and not s.get("is_video")


def mime_for(sticker):
    """靜態貼圖的 inline mime（Telegram 靜態貼圖是 webp）。非靜態回 None（不送視覺）。"""
    return "image/webp" if is_static(sticker) else None


def clip(text, limit=40):
    """把視覺回來的描述整形成**一句**：收合換行/多空白、去除包住整句的引號、超長截到句界感的長度。空→None。"""
    t = " ".join((text or "").split()).strip()
    if len(t) >= 2 and t[0] in "「『\"'“" and t[-1] in "」』\"'”":
        t = t[1:-1].strip()
    if not t:
        return None
    return t if len(t) <= limit else t[:limit].rstrip() + "…"


UNSEEN_MARK = "未讀畫面"   # fallback_note 的誠實標記；is_seen 據此分辨「真的看過」vs「只是備援文字」


def fallback_note(sticker):
    """讀不到畫面（動態／影片／視覺關閉／下載或讀圖失敗）時的**誠實**文字備援：用 emoji＋貼圖包名拼一句粗描述，
    並明講『未讀畫面』——不假裝看到（符合這專案不演的調性）。"""
    s = sticker or {}
    kind = "動態貼圖" if s.get("is_animated") else ("影片貼圖" if s.get("is_video") else "貼圖")
    bits = []
    if s.get("emoji"):
        bits.append(f"帶 {s.get('emoji')}")
    if s.get("set_name"):
        bits.append(f"出自「{s.get('set_name')}」")
    tail = ("，" + "、".join(bits)) if bits else ""
    return f"（{kind}{tail}，{UNSEEN_MARK}）"


def is_seen(desc):
    """這句描述是不是**真的看過畫面**（視覺讀到的），而非 fallback_note 的「未讀畫面」誠實備援／空值。
    給「挑一張我喜歡的」偏好排序與「敢不敢據實描述圖案」共用——看過才談得出，沒看過就別瞎掰。純函式。"""
    d = (desc or "").strip()
    return bool(d) and UNSEEN_MARK not in d
