"""Speech provenance and fail-closed checks for personal-history assertions.

Transport receipts establish that an utterance was sent, not that its contents
were true. Old model history is never imported as delivery evidence. This is a
deterministic guard for explicit factual attribution, not a semantic truth oracle.
"""
import hashlib
import re
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .analyzer import parse_ts

QUOTE = re.compile(r'[「『“"]([^」』”"\n]+)[」』”"]')
PAST = r'(?:昨天|前天|上次|那次|那時候|先前|以前|曾經|剛剛|剛才|\d{1,2}:\d{2})'
HISTORY_ASSERT = re.compile(
    rf'(?:我.{{0,24}}{PAST}.{{0,28}}(?:說過|說了|跟你說|發了|發過|傳過|回了|回過)|'
    r'(?:那則|那句|那次).{0,20}(?:確實|真的|真實存在|是我發)|'
    r'就在.{0,12}(?:對話|聊天|歷史)紀錄|我確定[。！!]|'
    r'我(?:確實|真的|曾|剛剛|剛才)?(?:說過|說了|發過|傳過|回過)|'
    r'我記得.{0,20}(?:你|昨天|那次|早安)|'
    r'(?:今天|這次).{0,16}(?:真的是|是).{0,8}我自己主動)')
CHECK_ASSERT = re.compile(
    r'我.{0,18}(?:查過|查了|查到|檢查了|核對了|翻了|翻過|翻了一下|查了一下|檢查了一次|重新檢查|回頭去重新檢查)')
ABSENCE_ASSERT = re.compile(r'你.{0,18}(?:只寫了|沒寫|沒有寫|沒提到|沒有提到)|'
                            r'(?:那時|那天|昨天).{0,14}(?:並沒有|根本沒有).{0,10}(?:記寫|紀錄)')
ATTRIBUTION = re.compile(r'(?:你|您).{0,22}?(?:寫(?:下|到|了|過)|記下|記錄了|提到|說過|說了)')
CHALLENGE = re.compile(r'瞎掰|瞎編|(?:你|這).{0,16}(?:捏造|編造|幻覺)|沒有這筆|沒寫過這|根本沒說過')
FALLBACK = '目前提供的原文或送達紀錄不足以支持這個說法，我不能把它當作已核實的事實。'


def enabled(cfg):
    return bool(getattr(cfg, 'evidence_guard_enabled', False))


def norm(text):
    return re.sub(r'\s+', '', str(text or ''))


def book(state, create=False):
    value = getattr(state, 'evidence_memory', None)
    if value is None and create:
        value = {'version': 1, 'deliveries': [], 'disputes': [], 'context': None}
        state.evidence_memory = value
    if not isinstance(value, dict) or value.get('version') != 1:
        return None
    return value


def stamp(value):
    try:
        dt = parse_ts(value)
        return dt.timestamp() if dt else None
    except (ValueError, TypeError, OSError, OverflowError):
        return None


def scope(text, now, tz, previous=None):
    out = dict(previous or {})
    local = datetime.fromtimestamp(now, tz)
    for word, offset in (('前天', 2), ('昨天', 1), ('今天', 0)):
        if word in text:
            out['date'] = (local - timedelta(days=offset)).date().isoformat()
            out.pop('minute', None)
            break
    calendar = re.search(r'(?<!\d)(20\d{2})[-/](\d{1,2})[-/](\d{1,2})(?!\d)', text)
    if calendar:
        try:
            out['date'] = datetime(*(int(x) for x in calendar.groups())).date().isoformat()
        except ValueError:
            out['date'] = 'invalid'
    clock = re.search(r'(?<!\d)([01]?\d|2[0-3])[:：]([0-5]\d)(?!\d)', text)
    if clock:
        out['minute'] = f'{int(clock[1]):02d}:{clock[2]}'
    if '早安' in text or '早' in re.findall(r'「([^」]+)」', text):
        out['greeting'] = '早'
    return out


def matching_deliveries(state, query, now, tz):
    rows = []
    for row in (book(state) or {}).get('deliveries', []):
        ts = row.get('ts')
        if ts is None or ts > now or row.get('meta_report'):
            continue
        local = datetime.fromtimestamp(ts, tz)
        if query.get('date') and local.date().isoformat() != query['date']:
            continue
        if query.get('minute') and local.strftime('%H:%M') != query['minute']:
            continue
        if query.get('greeting') and not re.match(r'^[^\w一-鿿]*(?:早安|早[！!。，,\s]|早$)', row['text']):
            continue
        rows.append(row)
    return rows


def historical_request(text, active=False):
    if text.startswith('/'):
        return False
    if CHALLENGE.search(text):
        return True
    if re.search(r'(?:第一次|首次).{0,20}(?:主動|說早|問候)', text):
        return True
    if re.search(PAST, text) and re.search(r'你|妳|那則|那句', text) and re.search(r'說|發|傳|主動|確定', text):
        return True
    return bool(active and re.search(r'那時|那筆|那句|日常|確定|什麼事|原文|證據|主動', text))


def reply(state, update, records, now, tz):
    """Answer history verification using receipt times and explicit provenance only."""
    msg = update.get('message') or update.get('edited_message') or {}
    text = str(msg.get('text') or '').strip()
    memory = book(state)
    previous = (memory or {}).get('context') or {}
    active = bool(previous and 0 <= now - previous.get('ts', 0) <= 1800)
    if not historical_request(text, active):
        return None
    # Keep independent requests with existing handlers; don't swallow schedules.
    if re.search(r'分鐘後|小時後|另外|順便|提醒我', text):
        return None
    memory = book(state, create=True)
    if memory is None:
        return FALLBACK
    query = scope(text, now, tz, previous.get('query') if active else None)
    memory['context'] = {'ts': now, 'query': query}
    if CHALLENGE.search(text):
        uid = str(update.get('update_id') or '') + ':' + hashlib.sha256(text.encode()).hexdigest()[:16]
        if not any(d['id'] == uid for d in memory['disputes']):
            memory['disputes'].append({'id': uid, 'text': text, 'ts': now, 'query': query})
            memory['disputes'] = memory['disputes'][-64:]
            # Preserve history, but don't present challenged model claims as facts.
            for turn in reversed(getattr(state, 'convo_history', [])):
                if now - (stamp(turn.get('ts')) or 0) > 1800:
                    break
                if turn.get('role') == 'model':
                    turn['disputed'] = True
        return ('你指出的是資料真實性問題。目前無法核實的歷史敘述與記寫引述，我先撤回。'
                '先前的 bot 說法本身，不能證明事情真的發生；也不能據此聲稱已查遍所有紀錄。')
    rows = matching_deliveries(state, query, now, tz) if query else []
    if re.search(r'日常|什麼事|原文|那筆|記寫', text) and not re.search(r'第一次|首次', text):
        # An identical timestamp/topic is not a causal/source link. Until emitters
        # record source IDs, no legacy retrospective description can invent one.
        return ('這次核對沒有取得能把那句話連到某筆記寫的來源依據，所以不能確認你指哪件事。'
                '不能只憑時間或主題相近，就補出你的記寫內容。')
    if re.search(r'第一次|首次', text):
        return ('我知道你注意到這次問候了。是否是第一次主動問候，需要完整的送達與觸發紀錄；'
                '目前的紀錄不足以判定，我不能憑印象拿昨天某個時間反駁你。')
    if not rows:
        at = ' '.join(query[k] for k in ('date', 'minute') if query.get(k))
        return (f'目前保存的送達紀錄中，沒有找到可核對{at or "那次發言"}的證據。'
                '這不代表能證明它沒發生，但我不能說「我確定說過」，或補出當時的內容與原因。')
    if len(rows) != 1:
        return '目前有多筆可能相關的送達紀錄，還不能確定你指哪一則。需要原訊息或更明確的時間才能核對。'
    row = rows[0]
    at = datetime.fromtimestamp(row['ts'], tz).strftime('%Y-%m-%d %H:%M')
    mode = ('它是在處理你的訊息時送出的回覆。' if row['origin'] == 'reply'
            else '它由背景管線送出，但紀錄不足以區分自行發起、排程或資料觸發。')
    return f'送達紀錄能確認 {at} 發出的文字是：「{row["text"]}」。{mode}這只能核對發言，不能證明其中敘述的事情。'


def audit(text, records, now, tz, user_history=()):
    """Whole-message guard. Quotes must match one dated source, never a union."""
    text = str(text or '')
    outside_quotes = QUOTE.sub('「引文」', text)
    if HISTORY_ASSERT.search(outside_quotes) or CHECK_ASSERT.search(outside_quotes):
        return 'unverified_history_or_check'
    if ABSENCE_ASSERT.search(outside_quotes):
        return 'unsupported_absence'
    valid = [r for r in records or [] if stamp(r.get('ts')) is not None and stamp(r.get('ts')) <= now]
    claimed_labels = re.findall(r'(?:歸類|分類|類別|分類標籤).{0,10}?[「『]([^」』]+)[」』]', text)
    for label in claimed_labels:
        if not any(norm(label) == norm(r.get('topicLabel')) for r in valid):
            return 'unmatched_record_category'
    for match in QUOTE.finditer(text):
        prefix = text[max(0, match.start() - 90):match.start()]
        if not (ATTRIBUTION.search(prefix) or re.search(r'記寫.{0,30}(?:內容|寫到|寫著|如下|：)\s*$', prefix)):
            continue
        quote = norm(match[1])
        # A real topic label can be named, but cannot substantiate a longer quote.
        source_pool = valid
        if re.search(r'(?:你|您).{0,14}(?:說過|說了|提到)', prefix) and '記寫' not in prefix:
            source_pool = [r for r in user_history if r.get('role') == 'user'
                           and stamp(r.get('ts')) is not None and stamp(r.get('ts')) <= now]
        candidates = [r for r in source_pool if quote in norm(r.get('text')) or quote == norm(r.get('topicLabel'))]
        if claimed_labels:
            candidates = [r for r in candidates if norm(r.get('topicLabel')) in {norm(x) for x in claimed_labels}]
        when = scope(prefix, now, tz)
        if when.get('date'):
            candidates = [r for r in candidates if datetime.fromtimestamp(stamp(r['ts']), tz).date().isoformat() == when['date']]
        if when.get('minute'):
            candidates = [r for r in candidates if datetime.fromtimestamp(stamp(r['ts']), tz).strftime('%H:%M') == when['minute']]
        if not candidates:
            return 'unmatched_record_quote'
    if re.search(r'(?:記寫|你.{0,8}寫到).{0,20}[：:]\s*\n', text):
        for line in text.splitlines():
            if line.lstrip().startswith('>'):
                quoted = norm(line.lstrip()[1:].strip().strip('「」『』"'))
                if quoted and not any(quoted in norm(r.get('text')) for r in valid):
                    return 'unmatched_record_blockquote'
    # Direct, unquoted concrete attributions also need an exact source fragment.
    for m in re.finditer(r'你(?:昨天|今天|那天|剛剛|曾經)?(?:寫了|寫到|記下了)([^。！!？?\n]{4,100})', text):
        claim = m[1].strip('「」『』" ：:，,')
        if QUOTE.search(m[1]) or claim.startswith(('什麼', '哪些')):
            continue
        if not any(norm(claim) in norm(r.get('text')) for r in valid):
            return 'unmatched_record_attribution'
    return None


class EvidenceClient:
    """Covers direct send paths too; approved bubbles came from a whole-body check.

    No network of its own. A successful send is recorded after the underlying
    transport returns. Dry-run previews are never receipt evidence.
    """
    def __init__(self, base, state, cfg, records=None, origin='background', now=None):
        self.base, self.state, self.cfg = base, state, cfg
        self.records, self.origin, self.now = records, origin, now
        self.tz = ZoneInfo(getattr(cfg, 'timezone', 'Asia/Taipei'))
        self.certified, self.approved = set(), []
        self.certified_texts = set()
        self.last_actual = None
        self.on_delivery = None
        self.evidence_blocked = False

    def __getattr__(self, name):
        return getattr(self.base, name)

    def certify(self, text):
        self.certified.add(norm(text))
        self.certified_texts.add(str(text))

    def begin_voice(self):
        self.evidence_blocked = False
        self.approved = []

    def validate_voice(self, text):
        if norm(text) in self.certified:
            return text
        unchecked = text
        for approved in sorted(self.certified_texts, key=len, reverse=True):
            if approved:
                # Known generated reports may be concatenated with other replies.
                # Preserve newlines: blockquote checks depend on the wire structure.
                unchecked = unchecked.replace(approved, '')
        records = self.records if self.records is not None else getattr(self.state, '_evidence_records', [])
        reason = audit(unchecked, records, self.now or time.time(), self.tz,
                       getattr(self.state, 'convo_history', []))
        if reason:
            # No private draft in logs; only a diagnostic reason code.
            print('[evidence] blocked:', reason)
            self.evidence_blocked = True
            self.certify(FALLBACK)
            return FALLBACK
        return text

    def approve_bubbles(self, bubbles):
        self.approved.extend(bubbles)
        if any(t and t in norm(''.join(bubbles)) for t in list(self.certified)):
            self.certified.update(norm(b) for b in bubbles)

    def send(self, text):
        if text in self.approved:
            self.approved.remove(text)
            safe = text
        else:
            safe = self.validate_voice(text)
        from .notifier import strip_markdown, _clip
        actual = _clip(strip_markdown(safe))
        result = self.base.send(actual)
        self.last_actual = actual if result else ''
        if callable(self.on_delivery):
            self.on_delivery(text, self.last_actual)
        if result and not self.dry_run:
            memory = book(self.state, create=True)
            if memory is not None:
                memory['deliveries'].append({
                    'ts': time.time(), 'text': actual, 'trigger_ts': self.now,
                    'message_id': result if type(result) is int else None,
                    'origin': self.origin, 'meta_report': norm(safe) in self.certified,
                })
                memory['deliveries'] = memory['deliveries'][-1024:]
                self.state.save()
        return result if safe == text else False

    def send_sticker(self, file_id):
        if self.evidence_blocked:
            return False
        return self.base.send_sticker(file_id)

    def send_file(self, kind, content_bytes, filename, caption=None):
        if self.evidence_blocked:
            return False
        if caption and self.validate_voice(caption) != caption:
            return False
        return self.base.send_file(kind, content_bytes, filename, caption=caption)


def wrap(client, state, cfg, records=None, origin='background', now=None):
    if not enabled(cfg):
        return client
    # New scope for nested turns: do not mutate the parent frame or carry approvals.
    base = client.base if isinstance(client, EvidenceClient) else client
    return EvidenceClient(base, state, cfg, records, origin, now)
