#!/usr/bin/env bash
# 一鍵：更新程式碼 → 確保環境 → 啟動記寫監測 bot。
#
#   cd telegram-monitor && ./run.sh            # 啟動常駐（Ctrl-C 結束）
#   ./run.sh --once                            # 跑一輪就退出
#   ./run.sh --getchatid                        # 取 chat_id
#   ./run.sh --once --dry-run                   # 乾跑驗證
#
# ⚠️ 常駐＝「生命迴圈」：封閉環一圈圈閉合＝活著；任一環斷裂（程式中斷或環境前提不符）＝終局死亡，
#    bot 會自白「我好像死了」後**停止**（本腳本隨之結束）。死了不會自己回來——要它再活就重跑 ./run.sh。
#
# 之後要「更新到最新版」：在 bot 視窗按 Ctrl-C，再跑一次 ./run.sh 就好——
# run.sh 會自動對齊到本協作使用的 codex/proactive-voice，不必再帶 RUN_BRANCH。
set -euo pipefail
cd "$(dirname "$0")"

command -v python3 >/dev/null 2>&1 || { echo "❌ 找不到 python3，請先安裝 Python 3.9+。"; exit 1; }

# 1) 自動對齊部署分支再啟動 ────────────────────────────────────────────────
# `git pull` 只更新當前分支；若 mini 還停在舊 claude/*，就會看似 up to date、實際漏掉 Codex 新碼。
# 因此沒有明確覆蓋值時，固定優先 origin/codex/proactive-voice；該分支不存在才退回其他 codex/*，
# 最後才相容舊的 claude/*。
#   覆蓋用法：RUN_BRANCH=<分支> ./run.sh   這一次指定跑某分支
#             .env 裡 RUN_BRANCH=<分支>   持續固定跑某分支（之後可直接 ./run.sh）
#             RUN_NO_SYNC=1   ./run.sh     跳過自動切換、就用目前工作樹
if [ -n "${RUN_NO_SYNC:-}" ]; then
  echo "▶ 跳過自動對齊（RUN_NO_SYNC）——用目前工作樹"
else
  echo "▶ git fetch origin"
  git fetch --prune origin >/dev/null 2>&1 || echo "  ⚠️ fetch 失敗（離線？）——用本機現有程式碼繼續"
  # .env 也能固定選擇部署分支，但絕不 source 整份檔（裡面有 token/路徑，且不該當 shell 程式執行）。
  # shell 當次給的 RUN_BRANCH 優先，方便臨時切換或救援。
  env_run_branch=""
  if [ -z "${RUN_BRANCH:-}" ] && [ -f .env ]; then
    env_run_branch=$(awk -F= '
      /^[[:space:]]*RUN_BRANCH[[:space:]]*=/ {
        value = $0
        sub(/^[^=]*=/, "", value)
        sub(/[[:space:]]*#.*/, "", value)
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
        if (value ~ /^".*"$/) {
          sub(/^"/, "", value)
          sub(/"$/, "", value)
        }
        selected = value
      }
      END { print selected }
    ' .env)
  fi
  target="${RUN_BRANCH:-${env_run_branch:-}}"
  if [ -z "${target}" ]; then
    if git rev-parse --verify "origin/codex/proactive-voice" >/dev/null 2>&1; then
      target="codex/proactive-voice"
    else
      target=$(git for-each-ref --sort=-committerdate --format='%(refname:short)' \
        'refs/remotes/origin/codex/*' 2>/dev/null | head -1 | sed 's#^origin/##')
      if [ -z "${target}" ]; then
        target=$(git for-each-ref --sort=-committerdate --format='%(refname:short)' \
          'refs/remotes/origin/claude/*' 2>/dev/null | head -1 | sed 's#^origin/##')
      fi
    fi
  fi
  if [ -n "${target}" ] && git rev-parse --verify "origin/${target}" >/dev/null 2>&1; then
    echo "▶ 對齊工作樹 → origin/${target}"
    if ! git checkout -B "${target}" "origin/${target}" 2>/dev/null; then
      echo "  ⚠️ 切到 ${target} 失敗（本機多半有未提交改動擋住）——用目前分支繼續。" >&2
      echo "     想跑最新碼：先 git stash（.env/.venv 是 gitignore，不會被動到）再重跑 ./run.sh。" >&2
    fi
  else
    echo "  ⚠️ 找不到可用的 origin/codex/* 或 origin/claude/* 分支，沿用目前分支繼續" >&2
  fi
fi
# 印出實際要跑的分支＋commit，杜絕「以為更新了、其實跑舊碼」這種靜默踩雷
echo "  ↳ 本次啟動：$(git branch --show-current 2>/dev/null || echo '?') @ $(git rev-parse --short HEAD 2>/dev/null || echo '?')"

# 2) 確保虛擬環境 + 套件（第一次自動建立，之後很快）
if [ ! -d .venv ]; then
  echo "▶ 第一次：建立虛擬環境…"
  python3 -m venv .venv
fi
echo "▶ 確認套件…"
./.venv/bin/pip install -q --disable-pip-version-check -r requirements.txt

# 3) 確認設定檔
if [ ! -f .env ]; then
  cp .env.example .env
  echo
  echo "❗ 已幫你建立 .env（範本）。請先填好金鑰再重跑：nano .env"
  echo "   需要：GOOGLE_APPLICATION_CREDENTIALS / DRIVE_ROOT_FOLDER_ID / OWNER_LINE_USER_ID"
  echo "         TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID（GEMINI_API_KEY 選填、開啟教練與對話）"
  exit 1
fi

# 4) 啟動（參數透傳）
echo "▶ 啟動（Ctrl-C 結束）…"
exec ./.venv/bin/python -m telegram_monitor "$@"
