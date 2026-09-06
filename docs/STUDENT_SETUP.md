# ConsciousnessBot：學生安裝與設定指南

這份指南讓你把 ConsciousnessBot 安裝在自己的電腦上。它是 **WriteToLearn 的旁路夥伴**：從 WriteToLearn 的 Google Drive 記憶層唯讀資料，整理成 Telegram 上的摘要、提醒與對話；它不會改寫你的 LINE bot、Apps Script 或 Drive 檔案。

> 先完成 [WriteToLearn 學生安裝](https://github.com/canlgz/WriteToLearn-Student-Setup)。本指南假設你已有可運作的 LINE WriteToLearn，且已能寫入一筆資料。

## 0. 安裝前確認

你需要：

- 一台可持續執行程式的個人電腦（macOS、Windows 或 Linux）；電腦關機或休眠時，bot 不會回覆或推播。
- Python 3.9 以上版：終端機輸入 `python3 --version` 確認。
- Git：輸入 `git --version` 確認。
- 一個 Telegram 帳號。
- 你自己的 WriteToLearn 設定值：`DRIVE_ROOT_FOLDER_ID` 與 `OWNER_LINE_USER_ID`。

## 1. 取得程式

在終端機執行：

```bash
git clone https://github.com/canlgz/ConsciousnessBot.git
cd ConsciousnessBot
cp .env.example .env
```

請用純文字編輯器開啟 `.env`。這個檔案有金鑰，已被 `.gitignore` 排除；**不要**把它上傳到 GitHub、傳到課程群組或貼在作業中。

## 2. 建立唯讀的 Google Drive 身分

ConsciousnessBot 用 Google service account 讀取資料。它不需要、也不應取得你的 Google 帳號密碼。

1. 開啟 [Google Cloud Console](https://console.cloud.google.com/)，建立一個專案或選取既有專案。
2. 在「API 和服務」啟用 **Google Drive API**。
3. 到「IAM 與管理」→「服務帳戶」，建立一個 service account。
4. 在該 service account 的「金鑰」頁面新增 **JSON** 金鑰並下載。把檔案放在你自己電腦的安全位置，例如 `~/Documents/keys/consciousnessbot-sa.json`。
5. 記下 service account 的 email（通常結尾是 `iam.gserviceaccount.com`）。
6. 到 Google Drive，找到 WriteToLearn 使用的根資料夾，將它分享給這個 email，權限選 **檢視者**。

在 `.env` 填入 JSON 金鑰的絕對路徑，例如：

```dotenv
GOOGLE_APPLICATION_CREDENTIALS=/Users/你的帳號/Documents/keys/consciousnessbot-sa.json
```

不要把 JSON 金鑰放在本專案資料夾，也不要填進 `GOOGLE_CREDENTIALS_JSON` 後提交。兩種方式只需選一種；初學者建議使用檔案路徑。

## 3. 從 WriteToLearn 取得兩個設定值

在你自己的 WriteToLearn Apps Script 專案中開啟「專案設定」→「指令碼屬性」，找到並複製：

```dotenv
DRIVE_ROOT_FOLDER_ID=...
OWNER_LINE_USER_ID=...
```

把它們填入 ConsciousnessBot 的 `.env`。兩者都不是 Telegram 資訊：

- `DRIVE_ROOT_FOLDER_ID` 是 WriteToLearn 儲存記寫資料的 Google Drive 根資料夾 ID。
- `OWNER_LINE_USER_ID` 是你的 LINE user ID，用來讓 bot 在根資料夾中辨識你的資料夾。

如果不確定資料夾是否分享正確，請確認 service account 是該根資料夾的「檢視者」，且你已經用 LINE 寫入至少一筆記寫。

## 4. 建立 Telegram bot 與取得 chat ID

1. 在 Telegram 搜尋 **@BotFather**，輸入 `/newbot`，依提示替 bot 命名。
2. 複製 BotFather 提供的 token，填到 `.env`：

   ```dotenv
   TELEGRAM_BOT_TOKEN=貼上你的token
   ```

3. 在 Telegram 開啟你剛建立的 bot，按「Start」或傳一則文字訊息。
4. 在專案資料夾執行：

   ```bash
   ./run.sh --getchatid
   ```

5. 輸出中的數字就是你的 `chat_id`，填入：

   ```dotenv
   TELEGRAM_CHAT_ID=你的chat_id
   ```

這個 bot 只會接受該 `chat_id` 的對話，避免陌生人使用你的資料。

## 5. 最小設定與第一次驗證

確認 `.env` 至少有這五項：

```dotenv
GOOGLE_APPLICATION_CREDENTIALS=/絕對路徑/service-account.json
DRIVE_ROOT_FOLDER_ID=...
OWNER_LINE_USER_ID=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

第一次請先做安全測試：

```bash
./run.sh --once --dry-run
```

它會自動建立 Python 虛擬環境並安裝套件；`--dry-run` 只在終端機印出結果，不會真的傳 Telegram 訊息。若出現「缺少必要設定」，請依錯誤名稱回到 `.env` 補齊。

測試成功後，啟動常駐服務：

```bash
./run.sh
```

保持這個終端機開啟。到 Telegram 對 bot 傳訊息，應會收到回應；停止時按 `Ctrl-C`。

## 6. 選配：開啟 Gemini 對話與教練語氣

不填 `GEMINI_API_KEY` 時，bot 仍可做結構化監測與摘要，但不會開啟生成式對話。若你要使用對話與蘇格拉底式提問，再填入：

```dotenv
GEMINI_API_KEY=你的Gemini_API_key
ENABLE_CHAT=1
LLM_VOICE=1
```

這會使用你的 API 額度；請先閱讀 `.env` 中的成本提醒參數。想維持純監測，把 `ENABLE_CHAT=0` 與 `LLM_VOICE=0`。

## 7. 常見問題

### `Permission denied` 或找不到 `./run.sh`

請確認終端機位於 `ConsciousnessBot` 資料夾；在 macOS/Linux 可執行 `chmod +x run.sh` 後重試。Windows 建議在 WSL 或 Git Bash 執行。

### Telegram 沒有回應

確認你先傳過訊息給 bot、`TELEGRAM_CHAT_ID` 是用 `--getchatid` 取得的數字，且程式仍在終端機執行。

### Drive 資料讀不到

依序檢查 JSON 金鑰路徑、Drive API 是否已啟用、根資料夾是否分享給 service account、`DRIVE_ROOT_FOLDER_ID` 與 `OWNER_LINE_USER_ID` 是否與 WriteToLearn 的指令碼屬性一致。

### 如何更新？

先在執行中的終端機按 `Ctrl-C`，再於專案目錄執行：

```bash
git pull --ff-only
./run.sh
```

請勿複製其他開發分支名稱或讓腳本自行切換分支；學生版只使用 `main`。
