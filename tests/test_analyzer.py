"""analyzer 單元測試（不需要 Drive / 網路）。

跑法（在 telegram-monitor/ 目錄下）：
    python -m unittest discover -s tests
"""

import unittest
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from telegram_monitor import analyzer

TZ = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def base_records():
    # u1 在 24h 內、u2 在 26h 前（昨天）
    return [
        {"id": "u1", "ts": iso(NOW - timedelta(hours=2)), "type": "image"},
        {"id": "u2", "ts": iso(NOW - timedelta(hours=26)), "type": "text"},
    ]


class GapTest(unittest.TestCase):
    def _analyze(self, contexts, journeys=None, explorations=None,
                 records=None, meta=None):
        data = {
            "contexts": contexts,
            "journeys": journeys or [],
            "explorations": explorations or [],
            "records": records if records is not None else base_records(),
            "meta": meta or {},
        }
        return analyzer.analyze(data, NOW, TZ, stall_grace_h=6)

    def test_near_upgrade_candidate(self):
        # 密度過、回返過、跨媒介只 1 種 → met==2 → 近升格
        c = {
            "id": "cNear", "status": "candidate", "label": "形成性評量", "category": "教學",
            "recordIds": [], "lastTs": iso(NOW - timedelta(hours=1)),
            "criteria": {"semanticDensity": 0.72, "coreFrac": 0.9,
                         "returnVisits": 4, "returnSpanHours": 5, "mediaKinds": 1},
        }
        snap = self._analyze([c])
        near = [g for g in snap.gaps if g["kind"] == "near"]
        self.assertEqual(len(near), 1)
        self.assertIn("跨媒介", near[0]["line"])
        self.assertIn("回返 4 次", near[0]["line"])
        self.assertEqual(snap.near_upgrade_sig.get("cNear"), "2/3")
        self.assertEqual(snap.funnel["candidate"], 1)

    def test_watch_formed_context_without_marker(self):
        c = {
            "id": "cWatch", "status": "context", "label": "RAG", "category": "研究",
            "recordIds": [], "lastTs": iso(NOW - timedelta(hours=3)),
            "criteria": {"semanticDensity": 0.7, "coreFrac": 0.9,
                         "returnVisits": 4, "returnSpanHours": 6, "mediaKinds": 2,
                         "passed": True},
        }
        snap = self._analyze([c], journeys=[])  # 沒有對應 journey
        watch = [g for g in snap.gaps if g["kind"] == "watch"]
        self.assertEqual(len(watch), 1)
        self.assertIn("轉折", watch[0]["line"])
        self.assertEqual(snap.funnel["context"], 1)

    def test_unreturned_candidate(self):
        c = {
            "id": "cUnret", "status": "candidate", "label": "雜記", "category": "隨想",
            "recordIds": ["u1", "u2"], "lastTs": iso(NOW - timedelta(hours=2)),
            "criteria": {"semanticDensity": 0.3, "coreFrac": 0.4,
                         "returnVisits": 1, "returnSpanHours": 0, "mediaKinds": 1},
        }
        snap = self._analyze([c])
        unret = [g for g in snap.gaps if g["kind"] == "unreturned"]
        self.assertEqual(len(unret), 1)
        self.assertIn("未回返", unret[0]["line"])

    def test_gap_priority_order(self):
        near = {"id": "n", "status": "candidate", "label": "a", "recordIds": [],
                "lastTs": iso(NOW), "criteria": {"semanticDensity": 0.7, "coreFrac": 0.9,
                "returnVisits": 4, "returnSpanHours": 5, "mediaKinds": 1}}
        watch = {"id": "w", "status": "context", "label": "b", "recordIds": [],
                 "lastTs": iso(NOW), "criteria": {"semanticDensity": 0.7, "coreFrac": 0.9,
                 "returnVisits": 4, "returnSpanHours": 6, "mediaKinds": 2, "passed": True}}
        snap = self._analyze([watch, near], journeys=[])
        self.assertEqual(snap.gaps[0]["kind"], "near")  # near(3) 排在 watch(2) 前

    def test_recompute_from_members_when_no_criteria(self):
        # 沒 criteria：returnVisits/mediaKinds 由成員 ts/type 重算
        recs = [
            {"id": "m1", "ts": iso(NOW - timedelta(hours=3)), "type": "text"},
            {"id": "m2", "ts": iso(NOW - timedelta(hours=2)), "type": "image"},
            {"id": "m3", "ts": iso(NOW - timedelta(hours=1)), "type": "audio"},
        ]
        c = {"id": "cRe", "status": "candidate", "label": "x", "recordIds": ["m1", "m2", "m3"],
             "lastTs": iso(NOW - timedelta(hours=1))}
        snap = self._analyze([c], records=recs)
        # 3 筆間隔各 1h ≥20 分 → 3 次回返；跨 text/image/audio → 3 種媒介 → met 至少 2（回返+媒介）
        near = [g for g in snap.gaps if g["kind"] == "near"]
        self.assertEqual(len(near), 1)


class FiledRecordsTest(unittest.TestCase):
    def _analyze(self, contexts, records):
        data = {"contexts": contexts, "journeys": [], "explorations": [],
                "records": records, "meta": {}}
        return analyzer.analyze(data, NOW, TZ, stall_grace_h=6)

    def test_filed_records_and_status(self):
        recs = [
            {"id": "f1", "ts": iso(NOW), "type": "text", "category": "教學", "topicLabel": "形成性評量"},
            {"id": "f2", "ts": iso(NOW), "type": "text"},  # 無 topicLabel → 排除（歸戶未定）
        ]
        ctx = {"id": "cF", "status": "context", "category": "教學", "label": "形成性評量",
               "recordIds": ["f1"], "lastTs": iso(NOW),
               "criteria": {"semanticDensity": 0.7, "coreFrac": 0.9, "returnVisits": 4,
                            "returnSpanHours": 6, "mediaKinds": 2, "passed": True}}
        snap = self._analyze([ctx], recs)
        ids = [r["id"] for r in snap.filed_records]
        self.assertEqual(ids, ["f1"])  # 只有已歸戶的入列
        self.assertEqual(snap.filed_records[0]["key"], "教學|形成性評量")
        self.assertEqual(snap.filed_records[0]["status"], "context")  # 帶該脈絡目前狀態

    def test_status_defaults_candidate_when_no_context(self):
        recs = [{"id": "x", "ts": iso(NOW), "type": "text", "category": "隨想", "topicLabel": "新念頭"}]
        snap = self._analyze([], recs)
        self.assertEqual(snap.filed_records[0]["status"], "candidate")


class HeartbeatTest(unittest.TestCase):
    def _hb(self, meta, require_dormant=True):
        data = {"contexts": [], "journeys": [], "explorations": [],
                "records": base_records(), "meta": meta}
        return analyzer.analyze(data, NOW, TZ, stall_grace_h=6,
                                stall_require_dormant=require_dormant).heartbeat

    def test_stalled_when_pending_and_old(self):
        # 真停：連 ingest 都 10h 沒動（升格 12h）＋有新資料 → stalled（fix 不影響此真實卡住）
        hb = self._hb({
            "lastContextUpgradeAt": iso(NOW - timedelta(hours=12)),
            "lastIngestTs": iso(NOW - timedelta(hours=10)),  # 升格後還有新資料、但 ingest 也老
        })
        self.assertEqual(hb["status"], "stalled")

    def test_fresh_classify_old_upgrade_not_stalled(self):
        # 🫀 截圖誤報根因：升格 8.6h 沒跑（事件驅動、罕見），但歸戶/分類剛剛還在跑（backgroundSweep 每 5 分鐘正常）
        # → 不該報「背景卡住」，背景活著、頂多 pending（排隊升格）。
        hb = self._hb({
            "lastContextUpgradeAt": iso(NOW - timedelta(hours=8.6)),
            "lastClassifyAt": iso(NOW - timedelta(minutes=3)),   # 歸戶剛跑
            "lastIngestTs": iso(NOW - timedelta(minutes=3)),
        })
        self.assertEqual(hb["status"], "pending")               # 不是 stalled

    def test_fresh_classify_old_upgrade_flag_off_is_stalled(self):
        # 旗標關＝退回只看升格 age＝逐位元同舊行為（重現截圖誤報，供一鍵回退驗證）
        hb = self._hb({
            "lastContextUpgradeAt": iso(NOW - timedelta(hours=8.6)),
            "lastClassifyAt": iso(NOW - timedelta(minutes=3)),
            "lastIngestTs": iso(NOW - timedelta(minutes=3)),
        }, require_dormant=False)
        self.assertEqual(hb["status"], "stalled")

    def test_healthy_when_no_pending(self):
        hb = self._hb({
            "lastContextUpgradeAt": iso(NOW - timedelta(hours=1)),
            "lastIngestTs": iso(NOW - timedelta(hours=2)),  # 整理在後、無待處理
        })
        self.assertEqual(hb["status"], "healthy")

    def test_pending_within_grace(self):
        hb = self._hb({
            "lastContextUpgradeAt": iso(NOW - timedelta(hours=2)),
            "lastIngestTs": iso(NOW - timedelta(hours=1)),  # 有新資料但才 2h（< 6h grace）
        })
        self.assertEqual(hb["status"], "pending")


class SummaryTest(unittest.TestCase):
    def test_counts_and_streak(self):
        data = {"contexts": [], "journeys": [], "explorations": [],
                "records": base_records(), "meta": {}}
        s = analyzer.analyze(data, NOW, TZ).summary
        self.assertEqual(s["total"], 2)
        self.assertEqual(s["last24h"], 1)   # 只有 u1 在 24h 內
        self.assertEqual(s["last7d"], 2)
        self.assertEqual(s["streak"], 2)    # 今天 + 昨天
        self.assertEqual(s["media"], {"image": 1, "text": 1})


class ParseTsTest(unittest.TestCase):
    def test_iso_and_epoch(self):
        self.assertIsNotNone(analyzer.parse_ts("2026-06-15T12:00:00.000Z"))
        self.assertIsNotNone(analyzer.parse_ts(1718452800000))  # epoch ms
        self.assertIsNone(analyzer.parse_ts(None))
        self.assertIsNone(analyzer.parse_ts(""))


if __name__ == "__main__":
    unittest.main()
