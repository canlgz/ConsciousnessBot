"""Telegram 推播（只送不收）＋訊息排版。

刻意用**純文字**（不設 parse_mode）：使用者自訂標題可能含各種符號，純文字最不會
炸格式，emoji 已足夠表達狀態（🌱🌿🌳 / ✅⏳⚠️）。
"""

import json
import re

import requests

from . import analyzer
from . import thresholds as T

TELEGRAM_LIMIT = 4096


def fmt_local(dt, tz):
    if not dt or tz is None:
        return "—"
    return dt.astimezone(tz).strftime("%m/%d %H:%M")


def _heartbeat_line(hb):
    status = hb["status"]
    age = hb.get("age_hours")
    age_s = f"{age:.1f}h" if isinstance(age, (int, float)) else "—"
    if status == "healthy":
        return f"✅ 背景正常運作（最近整理 {age_s} 前）"
    if status == "pending":
        return f"⏳ 有新資料，背景排隊整理中（{age_s}）"
    if status == "stalled":
        return (f"⚠️ 背景可能卡住：有新資料但 {age_s} 未整理"
                f"——檢查 Apps Script 的 backgroundSweep 觸發器是否還在跑")
    return "💤 目前沒有待整理的資料"


def _media_line(media):
    if not media:
        return "（無）"
    order = ["text", "image", "audio", "video", "file", "link", "sticker", "location"]
    parts = []
    for t in order:
        if media.get(t):
            parts.append(f"{analyzer.media_label(t)} {media[t]}")
    for t, n in media.items():
        if t not in order and n:
            parts.append(f"{analyzer.media_label(t)} {n}")
    return "・".join(parts)


def format_digest(snapshot, tz, now=None):
    s = snapshot.summary
    f = snapshot.funnel
    header_t = fmt_local(now, tz) if now else ""
    lines = [f"📊 記寫每日摘要　{header_t}".rstrip(), ""]

    today = s.get("today")
    if today is None:
        lines.append("今天：尚無可確認的日統計")
    elif today == 0:
        lines.append("今天：目前尚無新記寫")
    else:
        lines.append(f"今天：已記寫 {today} 則")
    lines.append(f"最近一筆：{fmt_local(s.get('last_write'), tz)}")
    lines.append(f"近 7 天：{s['last7d']} 則｜累計：{s['total']} 則")
    lines.append("")
    lines.append(f"整理概況：脈絡 {f['candidate']}・候選歷程 {f['context']}・學習歷程 {f['journey']}")
    # 每日摘要不是升格待辦清單；詳細門檻仍留在個別進展通知。
    hb = snapshot.heartbeat
    lines.append("背景整理：正常" if hb.get("status") == "healthy" else _heartbeat_line(hb))

    return _clip("\n".join(lines))


def format_events(events, tz):
    lines = ["🔔 記寫背景有新進展", ""]

    if events.get("heartbeat_alert"):
        lines.append("🫀 " + _heartbeat_line(events["heartbeat_alert"]))
        lines.append("")

    new_j = events.get("new_journeys") or []
    if new_j:
        lines.append("🌳 新學習歷程成形：")
        for j in new_j:
            lines.append(f"・〈{j.get('title') or j.get('label') or '(未命名)'}〉")
        lines.append("")

    new_c = events.get("new_contexts") or []
    if new_c:
        lines.append("🌿 新脈絡成形（候選歷程）：")
        for c in new_c:
            lines.append(f"・〈{analyzer.context_title(c)}〉")
        lines.append("")

    near = events.get("new_near") or []
    if near:
        lines.append("🌿 接近升格（差一步）：")
        for g in near:
            lines.append(f"・{g['line']}")
        lines.append("")

    return _clip("\n".join(lines).rstrip())


def format_filings(new_filings):
    """剛被背景歸戶的記寫，依 大類｜議題 分組、標目前狀態 icon。"""
    groups = {}
    for r in new_filings:
        g = groups.setdefault(r["key"], {
            "count": 0, "status": r["status"],
            "category": r.get("category"), "topicLabel": r.get("topicLabel"),
        })
        g["count"] += 1
    lines = [f"📝 剛歸戶（{len(new_filings)} 則記寫）", ""]
    for _key, g in sorted(groups.items(), key=lambda kv: -kv[1]["count"]):
        icon = T.STATE_ICON.get(g["status"], "🌱")
        label = T.STATE_LABEL.get(g["status"], "")
        name = f"{g['category']}｜{g['topicLabel']}" if g["category"] else g["topicLabel"]
        suffix = f" ×{g['count']}" if g["count"] > 1 else ""
        lines.append(f"・{icon} 〈{name}〉{suffix}　— {label}")
    return _clip("\n".join(lines))


def _clip(text):
    if len(text) <= TELEGRAM_LIMIT:
        return text
    return text[:TELEGRAM_LIMIT - 1] + "…"


# 訊息一律以**純文字**送出（不設 parse_mode），但 LLM 有時會吐 markdown
# （**粗體**、`程式`、# 標題、- 項目…），在 Telegram 會原樣顯示成字面星號/反引號，看起來像壞掉。
# 這裡把**成對**的強調記號去掉、只留文字；行首標題/項目記號也清掉。刻意保守：
#   - 只動成對且邊界乾淨（前後非字元、內側不貼空白）的 *斜體*／_斜體_，孤立或夾在字裡的符號不碰
#     → 「3 * 4」「a_b_c」「__init__」「2*3」皆不受影響
#   - 不處理 __粗體__（與 dunder 難分，且 LLM 幾乎只吐 **）以免誤傷識別字
# markdown 在本 bot 永不需要被「渲染」，故一律清除、不設開關。
_MD_CODE = re.compile(r"`([^`\n]+?)`")                                            # `行內程式`
_MD_STRIKE = re.compile(r"~~(.+?)~~")                                             # ~~刪除線~~
_MD_BOLD = re.compile(r"\*\*(.+?)\*\*")                                           # **粗體**
_MD_ITALIC_A = re.compile(r"(?<![\*\w])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\*\w])")    # *斜體*
_MD_ITALIC_U = re.compile(r"(?<![_\w])_(?!\s)([^_\n]+?)(?<!\s)_(?![_\w])")        # _斜體_
_MD_HEAD = re.compile(r"(?m)^[ \t]*#{1,6}[ \t]+")                                 # # 標題
_MD_BULLET = re.compile(r"(?m)^([ \t]*)[-*+][ \t]+")                              # - / * / + 項目


def strip_markdown(text):
    """把 LLM 偶爾吐出的 markdown 記號清成純文字（見上方說明）。沒有任何記號字元就原樣返回。"""
    if not text or not any(c in text for c in "*_`~#-"):
        return text
    out = _MD_CODE.sub(r"\1", text)
    out = _MD_STRIKE.sub(r"\1", out)
    out = _MD_BOLD.sub(r"\1", out)
    out = _MD_ITALIC_A.sub(r"\1", out)
    out = _MD_ITALIC_U.sub(r"\1", out)
    out = _MD_HEAD.sub("", out)
    out = _MD_BULLET.sub(r"\1", out)
    return out


class Notifier:
    def __init__(self, bot_token, chat_id, dry_run=False):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.dry_run = dry_run

    def send(self, text):
        # 統一收口：所有發話路徑（語音 _say／資料摘要／reflect 主動推播）都經過這裡，
        # 在最後送出前把 markdown 清成純文字（本 bot 不設 parse_mode，markdown 會原樣顯示）。
        text = strip_markdown(text)
        if self.dry_run:
            print("──── [DRY_RUN] Telegram 訊息 ────")
            print(text)
            print("────────────────────────────────")
            return True
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        try:
            resp = requests.post(url, json={
                "chat_id": self.chat_id,
                "text": _clip(text),
                "disable_web_page_preview": True,
            }, timeout=20)
            if resp.status_code != 200:
                print(f"[notifier] Telegram 回非 200：{resp.status_code} {resp.text[:300]}")
                return False
            # 回傳這則的 message_id（int，仍為 truthy＝成功）→ 讓呼叫端能把「訊息 id ↔ 在講哪條線」對起來，
            # 之後使用者對某則按反應(👍)時查得回『讚的是哪一筆』。解析不到就回 True（仍算成功、向後相容）。
            return (resp.json().get("result") or {}).get("message_id") or True
        except requests.RequestException as e:
            print(f"[notifier] 送出失敗：{e}")
            return False

    def send_typing(self):
        """送出「輸入中…」聊天動作（讓多串對話更像真人在打字）。dry_run／失敗皆靜默、不擋送訊息。"""
        if self.dry_run:
            return
        try:
            requests.post(f"https://api.telegram.org/bot{self.bot_token}/sendChatAction",
                          json={"chat_id": self.chat_id, "action": "typing"}, timeout=10)
        except requests.RequestException:
            pass

    def set_reaction(self, message_id, emoji):
        """對某則訊息按一個 emoji reaction（表達 bot 當下情緒）。emoji 須在 Telegram 允許的反應集合內。
        dry_run／失敗皆靜默回 False、不擋對話。"""
        if self.dry_run or not (self.chat_id and message_id and emoji):
            return False
        try:
            r = requests.post(f"https://api.telegram.org/bot{self.bot_token}/setMessageReaction",
                              json={"chat_id": self.chat_id, "message_id": message_id,
                                    "reaction": [{"type": "emoji", "emoji": emoji}]}, timeout=10)
            return bool(getattr(r, "ok", False))
        except requests.RequestException:
            return False

    def send_sticker(self, file_id):
        """送一張真貼圖（Telegram sendSticker，需 file_id；通常是對方傳過、被我們記住的那張）。
        dry_run／缺參數／失敗皆靜默回 False、不擋對話（呼叫端會退回單顆 emoji）。"""
        if self.dry_run or not (self.chat_id and file_id):
            return False
        try:
            r = requests.post(f"https://api.telegram.org/bot{self.bot_token}/sendSticker",
                              json={"chat_id": self.chat_id, "sticker": file_id}, timeout=15)
            if getattr(r, "status_code", 0) != 200:
                print(f"[notifier] sendSticker 非 200：{r.status_code} {r.text[:200]}")
                return False
            return True
        except requests.RequestException as e:
            print(f"[notifier] sendSticker 失敗：{e}")
            return False

    def download_file(self, file_id):
        """用 Telegram getFile 取檔路徑、再抓原始 bytes（給貼圖視覺解讀）。只讀不送 → dry_run 也照抓；
        缺 token/file_id、回應非 200、壞 JSON 或任何網路例外都靜默回 None、不擋對話（呼叫端會退回文字備援）。"""
        if not (self.bot_token and file_id):
            return None
        try:
            r = requests.get(f"https://api.telegram.org/bot{self.bot_token}/getFile",
                             params={"file_id": file_id}, timeout=15)
            if getattr(r, "status_code", 0) != 200:
                return None
            path = ((r.json() or {}).get("result") or {}).get("file_path") or ""
            if not path:
                return None
            fr = requests.get(f"https://api.telegram.org/file/bot{self.bot_token}/{path}", timeout=30)
            if getattr(fr, "status_code", 0) != 200:
                return None
            return fr.content or None
        except (requests.RequestException, ValueError):   # 網路失敗／r.json() 壞 → 當作讀不到
            return None

    _FILE_METHOD = {
        "image": ("sendPhoto", "photo"),
        "audio": ("sendAudio", "audio"),
        "video": ("sendVideo", "video"),
    }

    def send_file(self, kind, content_bytes, filename, caption=None):
        """把附件原始檔上傳到 Telegram；依類型挑 sendPhoto/Audio/Video，其餘走 sendDocument。"""
        method, field = self._FILE_METHOD.get(kind, ("sendDocument", "document"))
        if self.dry_run:
            print(f"──── [DRY_RUN] 送檔 {method}：{filename}（{len(content_bytes)} bytes）"
                  f" caption={caption!r} ────")
            return True
        url = f"https://api.telegram.org/bot{self.bot_token}/{method}"
        data = {"chat_id": self.chat_id}
        if caption:
            data["caption"] = caption[:1024]
        try:
            resp = requests.post(url, data=data, files={field: (filename, content_bytes)}, timeout=120)
            if resp.status_code != 200:
                print(f"[notifier] {method} 非 200：{resp.status_code} {resp.text[:200]}")
                if method != "sendDocument":   # 圖/音/影失敗（格式/大小）→ 退回當一般檔案送
                    return self.send_file("file", content_bytes, filename, caption)
                return False
            return True
        except requests.RequestException as e:
            print(f"[notifier] {method} 失敗：{e}")
            return False

    # allowed_updates 明確含 message_reaction → 才收得到「使用者對 bot 訊息按/改反應(👍 等)」的事件。
    # Telegram **預設不送** message_reaction（與 chat_member 同列），不指定就永遠收不到反應 → bot 不知道
    # 「你點哪筆讚」。私聊裡使用者對 bot 自己訊息的反應即會送進來（群組才需管理員）。
    _ALLOWED_UPDATES = ["message", "edited_message", "message_reaction"]

    def get_updates(self, offset=0, timeout=30):
        """Long-poll 拉新訊息（offset 之後）。回傳 update 物件 list（含 message / message_reaction）。"""
        url = f"https://api.telegram.org/bot{self.bot_token}/getUpdates"
        try:
            resp = requests.get(url, params={"offset": offset, "timeout": timeout,
                                             "allowed_updates": json.dumps(self._ALLOWED_UPDATES)},
                                timeout=timeout + 15)
            if resp.status_code != 200:
                print(f"[notifier] getUpdates 非 200：{resp.status_code} {resp.text[:200]}")
                return []
            return resp.json().get("result", [])
        except requests.RequestException as e:
            print(f"[notifier] getUpdates 失敗：{e}")
            return []

    def get_updates_chat_ids(self):
        """讀 getUpdates，回傳出現過的 chat_id 清單（協助設定）。"""
        url = f"https://api.telegram.org/bot{self.bot_token}/getUpdates"
        resp = requests.get(url, timeout=20)
        resp.raise_for_status()
        seen = {}
        for upd in resp.json().get("result", []):
            msg = upd.get("message") or upd.get("edited_message") or upd.get("channel_post") or {}
            chat = msg.get("chat") or {}
            if chat.get("id") is not None:
                who = chat.get("username") or chat.get("title") or chat.get("first_name") or ""
                seen[chat["id"]] = who
        return seen
