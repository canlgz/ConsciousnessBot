"""Observed runtime actions, separate from sent messages and inferred feelings.

Events are written by the operation that happened, never reconstructed from model
history or a topic selected while answering. No extra model or network calls.
"""
import re
from datetime import datetime
from . import evidence, viewpoint, association

ASK = re.compile(r'(?:你|妳).{0,6}(?:剛剛|剛才|最近|現在|這段時間|方才)?(?:在|都在|忙著|忙)?(?:做什麼|做了什麼|忙什麼|看什麼|看了什麼|讀什麼|讀了什麼|想什麼|想到什麼|在幹嘛|幹嘛)')
FOLLOW = re.compile(r'^(?:那|所以|你)?(?:在)?(?:看哪|讀哪|比較什麼|哪兩|什麼想法|想到什麼|為什麼|怎麼想到|可以說具體|說具體|細說)')


def record(state, cfg, kind, ts, **details):
    if not evidence.enabled(cfg):
        return
    memory = evidence.book(state, create=True)
    if memory is None:
        return
    event = {'kind': kind, 'ts': float(ts), **details}
    events = memory.setdefault('activities', [])
    events.append(event)
    memory['activities'] = events[-128:]
    if not getattr(cfg, 'dry_run', False):
        state.save()


def sources(records, now):
    # Snapshots preserve exactly which source was available at execution time.
    return [{'id': str(r.get('id') or ''), 'ts': evidence.stamp(r.get('ts')), 'topic': r.get('topicLabel') or '',
             'text': str(r.get('text') or '')[:180]}
            for r in records or [] if evidence.stamp(r.get('ts')) is not None
            and evidence.stamp(r.get('ts')) <= now and r.get('text')]


def interested(text, state=None, now=0):
    if evidence.CHALLENGE.search(text) or str(text).startswith('/') or re.search(r'另外|順便|提醒|分鐘後|小時後|等一下|明天|昨天|前天|\d{1,2}[:：]\d{2}', text):
        return False
    if ASK.search(text):
        return True
    ctx = (evidence.book(state) or {}).get('activity_context') if state else None
    return bool(ctx and 0 <= now - ctx.get('ts', 0) < 600 and FOLLOW.search(text))


def describe(event, now, tz):
    age = now - event['ts']
    when = '剛剛' if age <= 300 else '最近一次是在' + datetime.fromtimestamp(event['ts'], tz).strftime('%m/%d %H:%M') + '，'
    kind = event['kind']
    if kind == 'scan':
        refs = event.get('sources') or []
        detail = f'這次載入的內容包括「{refs[-1]["text"]}」。' if refs else ''
        return f'{when}讀取了你的記寫，檢查內容有沒有更新。' + detail + '這一步是在整理資料，還不是形成新的想法。'
    if kind == 'compare':
        return f'{when}比較了{event["count"]}個主題的記寫，看看哪些內容可能接得上。這一輪還沒有留下可展開的新聯想。'
    if kind == 'revisit':
        return f'{when}重新看了「{event["topic"]}」這個主題的記寫分布。這次是背景輪替回到它，還沒有形成新的內容解讀。'
    if kind == 'association':
        refs = event.get('sources') or []
        detail = '；'.join(f'「{r["text"]}」' for r in refs[:2])
        return (f'{when}把「{event["a"]}」和「{event["b"]}」列為可以比較的一組。'
                + (f'用到的記寫片段是：{detail}。' if detail else '')
                + '目前只是候選連結，還不能說它們真的有關。')
    if kind == 'thought':
        return f'{when}整理了「{event["a"]}」和「{event["b"]}」的聯想。當時整理出這個還待核對的想法：\n\n{event["text"]}'
    if kind == 'search':
        return f'{when}搜尋了「{event["query"]}」的外部資料，取得{event["count"]}個來源連結。還不能只憑連結就認定摘要的每句話都成立。'
    return ''


def reply(state, text, now, tz):
    memory = evidence.book(state) or {}
    # Explain an actual delivered guard message; do not let the model invent why.
    explaining = ('原文或送達紀錄不足' in text or '不能把它當作已核實' in text)
    if explaining and any(evidence.FALLBACK in r.get('text', '') for r in memory.get('deliveries', [])):
        return '那是我把一段沒核對好的回答擋下時，直接丟給你的制式句，沒有回答到你的問題。它不代表我那段時間什麼都沒做，也不能用上次聊天的時間來推算我在做什麼。'
    if not interested(text, state, now):
        return None
    events = [e for e in memory.get('activities', []) if 0 <= now - e.get('ts', 0) <= 86400]
    ctx = memory.get('activity_context') or {}
    if not ASK.search(text) and ctx:
        events = [e for e in events if e.get('ts') == ctx.get('event_ts') and e.get('kind') == ctx.get('kind')]
    # Routine scans must not hide a substantive action from the last five minutes.
    recent = [e for e in events if now - e['ts'] <= 300 and e['kind'] != 'scan']
    detailed = [e for e in recent if e['kind'] not in ('scan', 'compare')]
    event = (detailed or recent or events or [None])[-1]
    if event is None:
        return '我現在在讀你這則訊息、準備回答。至於你問之前那一小段，我還沒留下足夠的活動細節，不能補說成正在讀某篇或想某件事。'
    memory['activity_context'] = {'ts': now, 'event_ts': event['ts'], 'kind': event['kind']}
    if event['kind'] in ('thought', 'association'):
        feedback = (getattr(state, 'assoc_feedback', None) or {}).get(association.pair_key(event), {})
        disputed = feedback.get('sentiment') == 'reject' or viewpoint.blocked(state, event) or any(
            0 <= d.get('ts', 0) - event['ts'] <= 1800 for d in memory.get('disputes', []))
        if disputed:
            return f'當時是在比較「{event["a"]}」和「{event["b"]}」。但這段想法後來受到你的質疑，我不會再把它當成成立的判斷。'
    if re.search(r'為什麼|怎麼想到', text):
        if event['kind'] == 'revisit':
            return f'這次是背景輪替回到「{event["topic"]}」，不是因為我辨認出了新的意義。我不能再替這個動作補一個情緒上的原因。'
        if event['kind'] in ('association', 'compare', 'thought'):
            return '這次是記寫主題比較流程挑出的候選。內容相近只是開始比較的理由，還需要看具體共同點與差異，才能判斷這個連結有沒有用。'
    return describe(event, now, tz)
