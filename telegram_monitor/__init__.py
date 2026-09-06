"""Telegram 記寫背景監測 bot（心跳＋主動推播）.

獨立的唯讀消費者：以 LINE bot 的 Google Drive 資料夾為記憶層來源，定期偵測
「記寫背景狀態」，必要時主動 push 到 Telegram。不修改 src/、不寫共享 Drive。
"""

__version__ = "0.1.0"
