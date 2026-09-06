# 上游版本與單向同步規則

這個公開儲存庫是供學生安裝的穩定版；它不攜帶開發協作的完整歷史。

## 本版來源

- 上游開發儲存庫：[canlgz/telegram-monitor](https://github.com/canlgz/telegram-monitor)
- 上游分支：`codex/conscious-dialogue-core`
- 本次程式快照：`adbdfe2be7d01cc0bb9b3314766fc955264574ad`
- 快照日期：2026-09-06

公開版另加入學生安裝文件與安全啟動調整；這些變更只屬於 `ConsciousnessBot`，不回寫上游開發分支。

## 後續更新規則

資料流只有一個方向：

```text
telegram-monitor / codex/conscious-dialogue-core
                    ↓
            ConsciousnessBot / main
```

當要更新時，先指定一個上游 commit，再把該 commit 的程式內容帶入學生版，保留或更新學生文件與安全檢查；確認通過後才推送 `ConsciousnessBot/main`。不從學生版回寫到上游，也不讓 `run.sh` 自行切換開發分支。

可用這句話提出更新：

> 同步 ConsciousnessBot：來源是 telegram-monitor 的 `<commit>`。

這樣每次公開版都會記載明確來源，不會混淆「開發中最新狀態」和「已整理給學生的版本」。
