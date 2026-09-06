"""價性抽取（§2.3）。

從 reactions[].summary 與 sticker 記錄的 text 抽「情緒登錄」，映射到 [-1,+1]。
這代表的是**記寫者當下對所記寫的情緒**（他貼的貼圖／reactions）；供判定鏈在 Gate 4 把它
讀進意向判讀裡（這條線一路上記寫者的情緒怎麼起伏），作為理解該意向的一部分。
"""

POS = ["棒", "好喔", "沒問題", "加油", "自信", "我能", "你行", "掌聲", "愛心", "明星",
       "拍手", "歡呼", "喝采", "慶祝", "恭喜", "懂了", "太棒", "讚", "開心", "喜悅"]
NEG = ["哭泣", "心碎", "憂鬱", "孤單", "悲傷", "失落", "低落", "難過", "失敗", "打擊",
       "焦慮", "不安", "拒絕", "否定", "糟糕", "崩潰", "無力", "疲憊"]
# 混合/自嘲（抱歉臉紅內疚偷笑吐舌呵呵）→ 視為輕負/中性
MIXED = ["抱歉", "臉紅", "內疚", "偷笑", "吐舌", "呵呵", "尷尬"]


def _affect_texts(record):
    out = []
    for r in (record.get("reactions") or []):
        s = r.get("summary")
        if s:
            out.append(s)
    if record.get("type") == "sticker":
        t = (record.get("text") or "").replace("[貼圖]", "").strip()
        if t:
            out.append(t)
    return out


def valence_of(record):
    """回傳該筆記寫登錄的價性 ∈ [-1,+1]，沒有情緒登錄回 None。"""
    texts = _affect_texts(record)
    if not texts:
        return None
    blob = " ".join(texts)
    pos = sum(1 for w in POS if w in blob)
    neg = sum(1 for w in NEG if w in blob)
    mixed = sum(1 for w in MIXED if w in blob)
    if pos == 0 and neg == 0:
        return -0.2 if mixed else None      # 純混合/自嘲＝輕負
    score = (pos - neg) / max(1, pos + neg)
    if mixed and score > 0:                  # 混合詞把正向往下拉一點
        score -= 0.2
    return max(-1.0, min(1.0, round(score, 3)))


def valence_trajectory(records):
    """依 ts 排序，回傳 [{ts, valence}]（只含有價性登錄的）。"""
    out = []
    for r in sorted(records, key=lambda r: r.get("ts") or ""):
        v = valence_of(r)
        if v is not None:
            out.append({"ts": r.get("ts"), "valence": v})
    return out
