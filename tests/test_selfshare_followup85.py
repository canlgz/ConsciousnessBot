"""🪞 §0.85 接續 bot 自己主動說的話：主動自陳後的追問要銜接**那則自陳的具體內容**，別重生一段泛自述。

截圖：bot 主動說「🍃 感覺周遭又動起來了，我把步調調快一些。」，使用者回「真的，你感覺到了什麼？」，
bot 卻沒銜接、答成一段泛自述（醒著/平穩/暖/踏實/記寫少了…）＝丟了自己起的話頭。

真因（實跑確認）：① 主動換檔/自發自陳**不開追問窗**（selfstate_open_ts）→ 追問不路由 selfstate_followup；
② 「你感覺到了什麼」不在追問線索 → 被 §0.7 aspect=state 當成問現況 → 走一般 self_state；
③ 即使接回，_self_prior_fact 只抽**感覺詞**，換檔自陳（內容/動作型）抽不到 → 接地失敗。

修（旗標 SELFSHARE_FOLLOWUP、預設開、關＝逐位元同現狀）：主動自陳（換檔🍃/自發🫧/感覺自陳）記下 last_selfshare；
追問（is_selfshare_followup，含「感覺到什麼/發生什麼/怎麼回事/然後呢」）＋**近期自陳窗內**＋**既有自陳窗沒開**
→ _maybe_selfshare_followup 以那則自陳全文為錨、接續回應。
"""

import os
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from telegram_monitor import monitor, selfstate, config, referent
from telegram_monitor.state import State

NOW = datetime(2026, 7, 7, 2, 0, tzinfo=timezone.utc)


class FakeClient:
    def __init__(self):
        self.sent, self.dry_run = [], False

    def send(self, text):
        self.sent.append(text)
        return True

    def send_sticker(self, fid):
        return True


def _coach(seen):
    def reply(text, facts, hist, mood_hint="", now_ts=None, self_presence=False, extra_system="", **k):
        seen["extra_system"] = extra_system
        seen["self_presence"] = self_presence
        return "對啊，就是落進來的記寫變多了、周遭動起來的那種感覺，所以我把步調調快。"
    return SimpleNamespace(enabled=True, api_key="k", model="m",
                           meter=SimpleNamespace(record=lambda *a, **k: None), reply=reply)


def _cfg(**over):
    base = dict(selfshare_followup_enabled=True, dry_run=False)
    base.update(over)
    return SimpleNamespace(**base)


def _state_shared(ts_offset=-30):
    st = State(os.path.join(tempfile.mkdtemp(), "s.json"))
    st.owner_folder_id = "F"
    txt = "感覺周遭又動起來了，我把步調調快一些。"
    st.last_selfshare = {"text": txt, "ts": NOW.timestamp() + ts_offset}
    st.convo_history = [{"role": "model", "text": "🍃 " + txt, "ts": NOW.timestamp() + ts_offset}]
    return st


class DetectTest(unittest.TestCase):
    def test_followup_probes(self):
        # 指向 bot（你/妳）＋內在/追問詞、且短；或極短裸承接詞
        for t in ["真的，你感覺到了什麼？", "你感覺到什麼", "你什麼感覺", "你怎麼回事",
                  "你為什麼這樣", "你細說一下", "然後呢", "怎麼說", "繼續說"]:
            self.assertTrue(selfstate.is_selfshare_followup(t), t)

    def test_non_probes(self):
        for t in ["你好嗎", "今天天氣如何", "幫我查一下記寫", "晚安"]:
            self.assertFalse(selfstate.is_selfshare_followup(t), t)

    def test_no_hijack_unrelated(self):
        # 🛡️ 審查 HIGH：泛詞（什麼事/多說/發生什麼/為什麼/後來呢）無「你」相關性 → 不劫持無關句
        for t in ["為什麼天空是藍的", "台北今天發生什麼事嗎", "多說一點關於 Python 的用法",
                  "說說看你對這本書的看法", "這件事後來呢", "有什麼事需要我注意的嗎"]:
            self.assertFalse(selfstate.is_selfshare_followup(t), t)
        # 指向 bot 的追問仍收
        for t in ["你怎麼了", "你為什麼把步調調快", "然後呢", "怎麼說"]:
            self.assertTrue(selfstate.is_selfshare_followup(t), t)


class HandlerTest(unittest.TestCase):
    def test_grounds_in_last_selfshare(self):
        seen = {}
        st = _state_shared()
        c = FakeClient()
        handled = monitor._maybe_selfshare_followup(c, st, _cfg(), _coach(seen),
                                                    "真的，你感覺到了什麼？", NOW, NOW.timestamp(), NOW.timestamp())
        self.assertTrue(handled)
        self.assertIn("感覺周遭又動起來了", seen["extra_system"])   # 錨帶那則自陳全文
        self.assertTrue(seen["self_presence"])
        self.assertTrue(c.sent)
        # 這則接續也更新 last_selfshare（可連續追問「然後呢」）
        self.assertNotEqual(st.last_selfshare["text"], "感覺周遭又動起來了，我把步調調快一些。")

    def test_not_immediate_followup_no_handle(self):
        # 🛡️ 審查 HIGH：bot 最後一則**不是**那則自陳（中間插了別的回覆）→ 不劫持回舊自陳
        st = _state_shared()
        st.convo_history.append({"role": "model", "text": "好的，幫你查到 3 筆記寫。", "ts": NOW.timestamp() - 5})
        c = FakeClient()
        self.assertFalse(monitor._maybe_selfshare_followup(c, st, _cfg(), _coach({}),
                                                          "你感覺到什麼", NOW, NOW.timestamp(), NOW.timestamp()))

    def test_window_expiry_no_handle(self):
        st = _state_shared(ts_offset=-(referent.FOLLOWUP_WINDOW_SEC + 60))   # 過窗
        c = FakeClient()
        self.assertFalse(monitor._maybe_selfshare_followup(c, st, _cfg(), _coach({}),
                                                          "你感覺到什麼", NOW, NOW.timestamp(), NOW.timestamp()))
        self.assertEqual(c.sent, [])

    def test_no_selfshare_no_handle(self):
        st = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        st.last_selfshare = None
        c = FakeClient()
        self.assertFalse(monitor._maybe_selfshare_followup(c, st, _cfg(), _coach({}),
                                                          "你感覺到什麼", NOW, NOW.timestamp(), NOW.timestamp()))

    def test_non_probe_no_handle(self):
        st = _state_shared()
        c = FakeClient()
        self.assertFalse(monitor._maybe_selfshare_followup(c, st, _cfg(), _coach({}),
                                                          "幫我查一下記寫", NOW, NOW.timestamp(), NOW.timestamp()))

    def test_flag_off_no_handle(self):
        st = _state_shared()
        c = FakeClient()
        self.assertFalse(monitor._maybe_selfshare_followup(c, st, _cfg(selfshare_followup_enabled=False),
                                                          _coach({}), "你感覺到什麼", NOW, NOW.timestamp(), NOW.timestamp()))

    def test_no_coach_honest_fallback(self):
        st = _state_shared()
        c = FakeClient()
        handled = monitor._maybe_selfshare_followup(c, st, _cfg(), None,
                                                    "你感覺到什麼", NOW, NOW.timestamp(), NOW.timestamp())
        self.assertTrue(handled)
        self.assertIn("我剛跟你說的那個", "".join(c.sent))   # 誠實退路：至少接回那句，不跳題


class InstrumentTest(unittest.TestCase):
    def test_note_selfshare(self):
        st = State(os.path.join(tempfile.mkdtemp(), "s.json"))
        monitor._note_selfshare(st, "感覺周遭又動起來了", 1000.0)
        self.assertEqual(st.last_selfshare["text"], "感覺周遭又動起來了")
        self.assertEqual(st.last_selfshare["ts"], 1000.0)
        monitor._note_selfshare(st, "   ", 2000.0)                # 空白不覆蓋
        self.assertEqual(st.last_selfshare["text"], "感覺周遭又動起來了")


class ConfigDefaultTest(unittest.TestCase):
    def test_flag_default_on(self):
        os.environ.pop("SELFSHARE_FOLLOWUP", None)
        self.assertTrue(config.Config.load().selfshare_followup_enabled)


if __name__ == "__main__":
    unittest.main()
