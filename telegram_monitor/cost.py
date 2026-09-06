"""Gemini 用量/花費的粗估與「短時間花太多」守門。

費用＝回應 usageMetadata 的 token 數 × 可設定單價（USD/1M）× 匯率。這是**估算**、非
Google 帳單本身；單價請對照你方案的實際定價（.env 可調）。維持一個滾動視窗，視窗內估計
花費超過台幣門檻就觸發一次提醒（自帶冷卻，不洗版）。
"""

import time


class CostMeter:
    def __init__(self, in_per_m, out_per_m, usd_twd, window_min, threshold_twd, cooldown_min):
        self.in_per_m = float(in_per_m)
        self.out_per_m = float(out_per_m)
        self.usd_twd = float(usd_twd)
        self.window_sec = float(window_min) * 60
        self.threshold_twd = float(threshold_twd)
        self.cooldown_sec = float(cooldown_min) * 60
        self.events = []        # [(ts, cost_usd)]
        self.session_usd = 0.0  # 本次啟動以來累計
        self.last_alert = None  # 上次提醒時間（None＝從未）
        self.on_cost = None     # 可選：每次記帳回呼 on_cost(cost_usd)（給「每日累計」用）

    @property
    def enabled(self):
        return self.threshold_twd > 0

    def record(self, prompt_tokens, output_tokens, model=None, now=None):
        """記一次呼叫的 token，回傳該次估計花費（USD）。"""
        now = time.time() if now is None else now
        cost = ((prompt_tokens or 0) * self.in_per_m + (output_tokens or 0) * self.out_per_m) / 1_000_000.0
        self.events.append((now, cost))
        self.session_usd += cost
        if self.on_cost:
            try:
                self.on_cost(cost)
            except Exception:
                pass
        return cost

    def _prune(self, now):
        cutoff = now - self.window_sec
        self.events = [(t, c) for (t, c) in self.events if t >= cutoff]

    def window_twd(self, now=None):
        now = time.time() if now is None else now
        self._prune(now)
        return sum(c for _, c in self.events) * self.usd_twd

    def check_alert(self, now=None):
        """視窗內估計花費達門檻、且過了冷卻 → 回傳 dict 供推播；否則 None。"""
        if not self.enabled:
            return None
        now = time.time() if now is None else now
        twd = self.window_twd(now)
        cooldown_ok = self.last_alert is None or (now - self.last_alert) >= self.cooldown_sec
        if twd >= self.threshold_twd and cooldown_ok:
            self.last_alert = now
            return {
                "window_twd": twd,
                "calls": len(self.events),
                "window_min": self.window_sec / 60,
                "threshold_twd": self.threshold_twd,
                "session_twd": self.session_usd * self.usd_twd,
            }
        return None
