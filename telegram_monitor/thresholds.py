"""升格判準閾值——**鏡射** ``src/Config.gs`` 的 ``CONTEXT_CRITERIA``。

⚠️ 單一來源在 GAS 端。改動 GAS 那邊的常數時，**務必同步這裡**，否則監測端對
「還缺什麼／是否近升格」的判讀會與 LINE 上看到的不一致。

對應位置：``src/Config.gs:105`` (CONTEXT_CRITERIA)、``:148`` (JOURNEY_MARKERS)。
"""

# 主題群組 → 脈絡（層 3→4）三條件
RETURN_VISITS_MIN = 3          # 意向回返：不同時段回到同一關注點的次數下限
RETURN_GAP_MINUTES = 20        # 兩則間隔 ≥ 此值才算「新一次回返」
RETURN_SPAN_HOURS_MIN = 1      # 首末回返要橫跨的最小總時數
MEDIA_KINDS_MIN = 2            # 跨媒介協同：至少跨越的媒介種類數
SEMANTIC_DENSITY_MIN = 0.60    # 群內平均 cosine 下限

# 聚焦補償（邊緣密度）：密度落在 [floor, min) 但成員夠扣單一核心也算達標
DENSITY_FLOOR_FOR_FOCUS = 0.52
DENSITY_FOCUS_CORE_FRAC_MIN = 0.80

# 脈絡 → 學習歷程（層 4→5）：四種轉折標記，偵測到 ≥1 個即升格
JOURNEY_MARKERS = ("概念重述", "跨主題整合", "行動指向", "後設反思")

# 算「跨媒介種類」時納入的媒介型別（對齊 GAS 的 CONTEXT_MEDIA_TYPES）。
# sticker（情緒）、location（空間錨點）不算內容媒介。
MEDIA_TYPES = ("text", "link", "audio", "image", "video", "file")

# LINE 背景升格的最小節流間隔（小時）——決定心跳「stalled」判法的基準。
# 對應 src/Config.gs:158 CONTEXT_UPGRADE_MIN_INTERVAL_MS = 3h。
CONTEXT_UPGRADE_MIN_INTERVAL_H = 3

# exploration 內成不了 mini cluster 的最小筆數（對應 EXPLORATION_MINI_CLUSTER_MIN）。
# 拿來判「這場探索記得太少」。
EXPLORATION_THIN_RECORDS = 4

# 狀態詞彙 / icon（對齊 src/Focus.gs THEME_STATUS_ICON / stateBadge_）
STATE_ICON = {
    "journey": "🌳",     # 學習歷程（≥1 轉折）
    "context": "🌿",     # 候選歷程 / 已成形脈絡（過三條件）
    "candidate": "🌱",   # 進行中脈絡（累積中）
    "watch": "👀",       # 已成形但還沒抓到轉折
}
STATE_LABEL = {
    "journey": "學習歷程",
    "context": "候選歷程",
    "candidate": "進行中脈絡",
    "watch": "持續關注",
}


def density_passes(density, core_frac):
    """鏡射 densityConditionMet_：硬門檻 0.60，或聚焦補償（≥0.52 且 coreFrac≥0.80）。"""
    if density is None:
        return False
    if density >= SEMANTIC_DENSITY_MIN:
        return True
    if core_frac is not None and density >= DENSITY_FLOOR_FOR_FOCUS \
            and core_frac >= DENSITY_FOCUS_CORE_FRAC_MIN:
        return True
    return False
