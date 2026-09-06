"""沉默的保守解讀：可能意圖與送訊息的授權分開；不把猜測寫成人物事實。"""
import re

BOUNDARY = re.compile(r"不要再問|別再問|不用再問|停止追問|跳針|別重複|不要重複")
DEPARTURE = re.compile(r"晚安|先忙|去忙|先睡|去睡|累了|先離開|晚點聊|明天聊")
WAITING = re.compile(r"查好.*告訴我|查完.*告訴我|做好.*通知我|完成.*告訴我|等你.*結果")


def interpret(history, now_ts, after_sec=420, last_contact_ts=0):
    """只有 bot 最後說話且沒有較新的聯絡才是待解讀的沉默；返回後自然重算。"""
    hist = history or []
    if not hist or hist[-1].get('role') != 'model':
        return None
    last = hist[-1]
    try:
        at = float(last.get('ts') or 0)
        elapsed = float(now_ts) - at
        if at <= 0 or elapsed < after_sec or float(last_contact_ts or 0) > at:
            return None
    except (ValueError, TypeError):
        return None
    user = next((e for e in reversed(hist[:-1]) if e.get('role') == 'user'), {})
    text = user.get('text') or ''
    if BOUNDARY.search(text):
        kind, possibilities = 'boundary', ['對方已要求停止或指出重複']
    elif DEPARTURE.search(text):
        kind, possibilities = 'away', ['可能正在休息或處理其他事情']
    elif WAITING.search(text):
        kind, possibilities = 'awaiting_result', ['可能在等已交付工作的結果']
    else:
        kind, possibilities = 'unknown', ['可能在思考', '可能暫離', '可能不想接這個問題']
    return {'kind': kind, 'possibilities': possibilities, 'evidence': text[:160],
            'last_bot_ts': at, 'elapsed_sec': elapsed,
            'action': 'await_task_result' if kind == 'awaiting_result' else 'wait',
            'send_followup': False}


def is_stop_feedback(text):
    # 複合要求交給正常路由，不能為了收尾吞掉後面的工作。
    return bool(re.fullmatch(r"(?:你)?(?:幹嘛|怎麼|為什麼)?(?:又|在|一直|再)?跳針(?:了)?[？?！!。\s]*|"
                             r"(?:請)?(?:你)?(?:不要|不用|別)再問(?:了)?[？?！!。\s]*", (text or '').strip()))
