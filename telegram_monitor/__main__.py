"""進入點。

  python -m telegram_monitor                # 常駐心跳迴圈（預設）
  python -m telegram_monitor --once         # 跑一輪就退出（cron/驗證用）
  python -m telegram_monitor --once --dry-run   # 一輪、訊息只印不送
  python -m telegram_monitor --digest-now   # 立刻強推一則每日摘要
  python -m telegram_monitor --getchatid    # 讀 getUpdates 印出 chat_id（先對 bot 傳一則）
"""

import argparse
import warnings

# 靜音 macOS 內建 Python 3.9 + LibreSSL 的無害雜訊（不影響功能、也不蓋掉真正的錯誤）。
# 放在匯入 google / urllib3（在 main() 內延遲匯入）之前生效。
warnings.filterwarnings("ignore", message=r".*OpenSSL.*")
warnings.filterwarnings("ignore", message=r".*end of life.*")
warnings.filterwarnings("ignore", message=r".*non-supported Python version.*")

from .config import Config


def main(argv=None):
    p = argparse.ArgumentParser(prog="telegram_monitor", description="記寫背景監測 bot")
    p.add_argument("--once", action="store_true", help="跑一輪就退出")
    p.add_argument("--digest-now", action="store_true", help="立刻強推每日摘要")
    p.add_argument("--getchatid", action="store_true", help="讀 getUpdates 印出 chat_id")
    p.add_argument("--dry-run", action="store_true", help="訊息只印到 stdout、不送 Telegram")
    p.add_argument("--env", default=None, help="指定 .env 檔路徑")
    args = p.parse_args(argv)

    cfg = Config.load(args.env)
    if args.dry_run:
        cfg.dry_run = True

    if args.getchatid:
        cfg.require("telegram_bot_token")  # 查 chat_id 只需要 token（此時本來就還沒有 chat_id）
        from .notifier import Notifier
        ids = Notifier(cfg.telegram_bot_token, cfg.telegram_chat_id).get_updates_chat_ids()
        if not ids:
            print("沒抓到任何 chat_id。請先在 Telegram 對你的 bot 傳一則訊息，再跑一次。")
        else:
            print("偵測到的 chat_id：")
            for cid, who in ids.items():
                print(f"  {cid}   {who}")
        return 0

    # 監測需要 Drive + Telegram 全套
    cfg.require(*Config.DRIVE_KEYS, *Config.TELEGRAM_KEYS)
    from . import monitor

    if args.digest_now:
        monitor.run_once(cfg, force_digest=True)
    elif args.once:
        monitor.run_once(cfg)
    else:
        monitor.run_loop(cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
