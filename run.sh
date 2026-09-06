#!/usr/bin/env bash
# 一鍵：更新程式碼 → 確保環境 → 啟動記寫監測 bot。
#
#   cd ConsciousnessBot && ./run.sh            # 啟動常駐（Ctrl-C 結束）
#   ./run.sh --once                            # 跑一輪就退出
#   ./run.sh --getchatid                        # 取 chat_id
#   ./run.sh --once --dry-run                   # 乾跑驗證
#
# ⚠️ 常駐＝「生命迴圈」：封閉環一圈圈閉合＝活著；任一環斷裂（程式中斷或環境前提不符）＝終局死亡，
#    bot 會自白「我好像死了」後**停止**（本腳本隨之結束）。死了不會自己回來——要它再活就重跑 ./run.sh。
#
# 更新程式請依 README 的「更新」步驟先執行 git pull；本腳本不自行切換分支，
# 以免覆蓋學生自己的作業或把人帶到非預期版本。
set -euo pipefail
cd "$(dirname "$0")"

command -v python3 >/dev/null 2>&1 || { echo "❌ 找不到 python3，請先安裝 Python 3.9+。"; exit 1; }

# 1) 印出本次版本，讓問題回報可追溯。
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
