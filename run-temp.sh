#!/usr/bin/env bash
# 臨時啟動腳本（給沒有 GitHub 憑證、或不想被帳密卡住的機器用）。
# 與 run.sh 的差別：
#   ① git 永遠不會跳 Username/Password、不會卡住（GIT_TERMINAL_PROMPT=0）。
#   ② 抓得到就**強制對齊** origin（git reset --hard）＝這台永遠跟線上一致；抓不到就用本機現有程式碼跑。
# 注意：強制對齊會覆蓋本機「對程式碼的改動」；但 .env／state.json 是 gitignore、不受影響。
# 用法：cd 到 telegram-monitor 目錄後 →  ./run-temp.sh      （Ctrl-C 結束）
#       乾跑驗證 →  ./run-temp.sh --once --dry-run
# 註：不用 set -u（避免變數未綁定就整支中止）；指令各自做好防呆。

[ -d telegram_monitor ] || { echo "❌ 請先 cd 到 telegram-monitor 目錄再執行"; exit 1; }

export GIT_TERMINAL_PROMPT=0          # 關鍵：git 不再跳 Username/Password、不會卡住
export GIT_ASKPASS=true               # 雙保險：就算有 credential helper 也不互動問

echo "▶ 嘗試更新（沒有憑證就跳過、不會問帳密）…"
if git fetch --prune origin >/dev/null 2>&1; then
  target="${RUN_BRANCH:-}"
  if [ -z "$target" ]; then
    target="$(git for-each-ref --sort=-committerdate --format='%(refname:short)' 'refs/remotes/origin/claude/*' 2>/dev/null | head -1 | sed 's#^origin/##')"
  fi
  if [ -n "$target" ] && git rev-parse --verify "origin/$target" >/dev/null 2>&1; then
    echo "▶ 強制對齊到 origin/$target（本機程式碼改動會被覆蓋；.env／state.json 不受影響）"
    git checkout -B "$target" "origin/$target" >/dev/null 2>&1 || git checkout -q "$target" >/dev/null 2>&1 || true
    git reset --hard "origin/$target" >/dev/null 2>&1 || echo "  ⚠️ 對齊失敗——用目前程式碼繼續"
  fi
else
  echo "  ⚠️ 連不上/沒有 GitHub 憑證——略過更新、用本機現有程式碼繼續"
fi
echo "  ↳ 目前：$(git rev-parse --abbrev-ref HEAD 2>/dev/null) @ $(git rev-parse --short HEAD 2>/dev/null)"

[ -d .venv ] || python3 -m venv .venv
echo "▶ 確認套件…"
./.venv/bin/pip install -q --disable-pip-version-check -r requirements.txt

if [ ! -f .env ]; then
  cp .env.example .env
  echo "❗ 已建立 .env 範本，請先填金鑰：nano .env  然後再執行一次"; exit 1
fi

echo "▶ 啟動（Ctrl-C 結束）…"
exec ./.venv/bin/python -m telegram_monitor "$@"
