"""意向向量與型態（§3.4）。

四維意向取自 journey.gapAnalysis.levels；若資料沒有 gapAnalysis，退而用 journey.markers
的型別推得（概念重述→conceptDepth、跨主題整合/連結→crossTopic、行動指向→actionOrient、
後設反思→metaReflection）。
"""

import math

AXES = ["actionOrient", "conceptDepth", "crossTopic", "metaReflection"]
_MARKER_AXIS = {
    "概念重述": "conceptDepth",
    "跨主題整合": "crossTopic",
    "跨主題連結": "crossTopic",
    "行動指向": "actionOrient",
    "後設反思": "metaReflection",
}


def levels_from_journey(journey):
    ga = (journey or {}).get("gapAnalysis") or {}
    lv = ga.get("levels")
    if isinstance(lv, dict) and lv:
        return {k: float(lv.get(k, 0.0)) for k in AXES}
    # 退路：用 markers 推
    out = {k: 0.0 for k in AXES}
    for m in (journey or {}).get("markers") or []:
        ax = _MARKER_AXIS.get(m.get("type"))
        if ax:
            out[ax] = max(out[ax], float(m.get("confidence", 1.0) or 1.0))
    return out


def disposition(levels):
    a, c = levels.get("actionOrient", 0), levels.get("conceptDepth", 0)
    m = levels.get("metaReflection", 0)
    if a >= 0.6 and c >= 0.6:
        return "探究型", "既趨行動又趨理解"
    if a >= 0.6 and m < 0.3:
        return "處理型", "趨行動、低反思"
    if m >= 0.6 and a < 0.3:
        return "咀嚼型", "趨反思、不趨行動"
    if a < 0.3 and c < 0.3 and m < 0.3:
        return "記錄型", "方向未成形"
    return "混合型", "方向游移"


def magnitude(levels):
    return round(math.sqrt(sum(float(levels.get(k, 0.0)) ** 2 for k in AXES)), 3)
