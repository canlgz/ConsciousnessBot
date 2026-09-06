"""Service-account Drive 唯讀讀取（記憶層來源）。

職責：
  * 在 ``DRIVE_ROOT_FOLDER_ID`` 底下認出「擁有者本人」的資料夾——資料夾是用顯示名稱
    命名的，穩定鍵在每個資料夾的 ``meta.json`` 內（``type``/``id``）。鏡射
    ``src/ChatScope.gs:100`` ``findFolderByMeta_``。
  * 依 ``modifiedTime`` 條件式下載五個檔，未變動就用記憶體快取（省頻寬，
    ``embeddings.jsonl`` 可能很大）。
  * 解析 jsonl；解析 ``embeddings.jsonl`` 時**丟掉 ``embedding`` 陣列**（監測不需向量）。
"""

import json

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
FOLDER_MIME = "application/vnd.google-apps.folder"

META_FILE = "meta.json"
CONTEXTS_FILE = "contexts.jsonl"
JOURNEYS_FILE = "journeys.jsonl"
EXPLORATIONS_FILE = "explorations.jsonl"
EMBEDDINGS_FILE = "embeddings.jsonl"

# embeddings.jsonl 解析後保留這些欄位（向量丟掉、原文 text 留著供對話紮根、
# fileId 取回附件、urlPreview 讓教練看得到外部連結的標題/網址）。
_RECORD_KEEP = ("id", "ts", "type", "category", "topicLabel", "explorationId",
                "text", "fileId", "urlPreview")


class DriveReader:
    def __init__(self, credentials_path, root_folder_id, owner_user_id, credentials_json=""):
        # 延遲匯入 google 套件：讓本模組在沒裝 google 的環境（如測試）也能 import。
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        if credentials_json:                       # 雲端：直接吃整段 JSON（免在主機上放檔案）
            creds = service_account.Credentials.from_service_account_info(
                json.loads(credentials_json), scopes=SCOPES)
        else:                                      # 本地：吃 service-account JSON 檔案路徑
            creds = service_account.Credentials.from_service_account_file(
                credentials_path, scopes=SCOPES)
        self.svc = build("drive", "v3", credentials=creds, cache_discovery=False)
        self.root_folder_id = root_folder_id
        self.owner_user_id = owner_user_id
        # 記憶體快取：name -> (modifiedTime, parsed)。常駐行程跨 tick 重用。
        self._cache = {}

    # ── 低階 Drive 呼叫 ──────────────────────────────────────────────
    def _list_children(self, folder_id):
        """列資料夾下所有未刪除子項（含分頁）。回傳 list of file resource。"""
        out, token = [], None
        while True:
            resp = self.svc.files().list(
                q=f"'{folder_id}' in parents and trashed=false",
                fields="nextPageToken, files(id,name,mimeType,modifiedTime)",
                pageSize=1000, pageToken=token,
                supportsAllDrives=True, includeItemsFromAllDrives=True,
            ).execute()
            out.extend(resp.get("files", []))
            token = resp.get("nextPageToken")
            if not token:
                break
        return out

    def _download_text(self, file_id):
        data = self.svc.files().get_media(
            fileId=file_id, supportsAllDrives=True).execute()
        if isinstance(data, bytes):
            return data.decode("utf-8", errors="replace")
        return str(data)

    def download_bytes(self, file_id):
        """下載任意檔案的原始位元組（圖/PDF/語音…附件用）。"""
        return self.svc.files().get_media(fileId=file_id, supportsAllDrives=True).execute()

    def file_meta(self, file_id):
        """取檔名/mime/大小，給呈現附件用。"""
        return self.svc.files().get(
            fileId=file_id, fields="name,mimeType,size", supportsAllDrives=True).execute()

    def load_embedding_records(self, owner_folder_id):
        """載入完整記錄（**保留 embedding 與 reactions**）——給意識判定鏈用。
        較重，只在狀態問句／心跳跑鏈時呼叫；依 modifiedTime 快取。"""
        children = {f["name"]: f for f in self._list_children(owner_folder_id)}
        fres = children.get(EMBEDDINGS_FILE)
        if not fres:
            return []
        key = EMBEDDINGS_FILE + ":full"
        cached = self._cache.get(key)
        if cached and cached[0] == fres.get("modifiedTime"):
            return cached[1]
        out = []
        for line in (self._download_text(fres["id"]) or "").split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        self._cache[key] = (fres.get("modifiedTime"), out)
        return out

    # ── owner 資料夾解析 ─────────────────────────────────────────────
    def resolve_owner_folder(self):
        """掃 root 子資料夾、比對 meta.json 的 type=='user' 且 id==OWNER。回傳 folderId 或 None。"""
        for child in self._list_children(self.root_folder_id):
            if child.get("mimeType") != FOLDER_MIME:
                continue
            meta = self._find_and_parse_meta(child["id"])
            if meta and meta.get("type") == "user" and meta.get("id") == self.owner_user_id:
                return child["id"]
        return None

    def _find_and_parse_meta(self, folder_id):
        for f in self._list_children(folder_id):
            if f.get("name") == META_FILE:
                try:
                    return json.loads(self._download_text(f["id"]) or "{}")
                except json.JSONDecodeError:
                    return None
        return None

    # ── 條件式抓取 + 解析 ────────────────────────────────────────────
    def load_owner_data(self, owner_folder_id):
        """讀齊五個檔，回傳 dict(meta, contexts, journeys, explorations, records)。

        依 ``modifiedTime`` 與記憶體快取比對，未變動的檔不重新下載。
        """
        children = {f["name"]: f for f in self._list_children(owner_folder_id)}

        meta = self._fetch_parsed(children.get(META_FILE), self._parse_json_obj, default={})
        contexts = self._fetch_parsed(children.get(CONTEXTS_FILE), self._parse_jsonl, default=[])
        journeys = self._fetch_parsed(children.get(JOURNEYS_FILE), self._parse_jsonl, default=[])
        explorations = self._fetch_parsed(children.get(EXPLORATIONS_FILE), self._parse_jsonl, default=[])
        records = self._fetch_parsed(children.get(EMBEDDINGS_FILE), self._parse_records, default=[])

        return {
            "meta": meta,
            "contexts": contexts,
            "journeys": journeys,
            "explorations": explorations,
            "records": records,
        }

    def _fetch_parsed(self, file_res, parser, default):
        if not file_res:
            return default
        name = file_res["name"]
        mtime = file_res.get("modifiedTime")
        cached = self._cache.get(name)
        if cached and cached[0] == mtime:
            return cached[1]
        parsed = parser(self._download_text(file_res["id"]))
        self._cache[name] = (mtime, parsed)
        return parsed

    # ── 解析器（容錯：壞行跳過，比照 loadJsonl_）──────────────────────
    @staticmethod
    def _parse_json_obj(text):
        try:
            return json.loads(text or "{}")
        except json.JSONDecodeError:
            return {}

    @staticmethod
    def _parse_jsonl(text):
        out = []
        for line in (text or "").split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    @staticmethod
    def _parse_records(text):
        out = []
        for line in (text or "").split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            out.append({k: obj[k] for k in _RECORD_KEEP if k in obj})
        return out
