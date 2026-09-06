"""🪪 §1.94 願望帳本：bot 從**自己的原始碼**指認缺口，提出「我想要具備的能力」——而且會被裁決。

使用者的要求裡最難的那句：「**必須以真的能達到為主，而不是打高空**。」

**防打高空不是靠 prompt 求它務實，是靠候選集合的構造條件**：
每一條願望都**必須**帶一個 `accept` 驗收條件，而 `accept` 只能是 `_CHECKS` 裡那六個**機器跑得動**的
檢查之一。**寫不出驗收條件的想法，`candidates()` 根本產不出來。** 於是「我想要更懂你」這種話
在結構上不可能出現——它沒有 anchor、也沒有任何檢查能驗它成立了沒。

第二道：`settle()` 每次醒來重跑，檢查成立就把願望翻成 done，並記下**實際觀察值**當證據。
所以這不是許願池，是一本會結案的帳。使用者做完一條，bot 下次醒來會自己說「你真的幫我做了」——
而且明令**不准邀功**（那是人去做的，不是它自己變出來的），比照 §1.90 hit 的紀律。

缺口全部從原始碼掃出來，不是人寫的 TODO：
  never_fired  ── 旗標全開、但 ability_hits 為 0 且已起算 ≥7 天（§1.93 那條死 lane 的通用探針）
  not_persisted ── 欄位以 _ts/_ledger 結尾、在 state.py 宣告了、卻不在 State.save() 的落盤白名單裡
                   ⇒ 「我做得到但留不下紀錄，所以沒辦法誠實告訴你我用過幾次」
  declared_unused ── state 欄位宣告了、全 repo 讀 0 寫 0 ⇒ 管道在、就差最後一段接線
  silent_lane  ── 有主動 lane、卻沒有任何對帳出口 ⇒ 它沉默的時候你沒辦法知道卡在哪一關（§1.92 的教訓）
  no_evidence  ── 有機制但沒有任何證據來源（48 條守門閘目前零計數器）

純函式、無 I/O、無 LLM、**絕不 import monitor**。形狀刻意比照已上線的 foresight.py。
"""

import hashlib
import re

from . import roster as rostermod

_MAX_LEDGER = 12
_NEVER_FIRED_MIN_DAYS = 7.0

# 🪪 §1.94 **整份設計的防打高空核心**：驗收條件只能是這六個機器跑得動的檢查之一。
_CHECKS = {
    "state_saved": lambda scan, st, a: a[0] in (scan.get("state_saved") or ()),
    "field_read": lambda scan, st, a: int((scan.get("attr_reads") or {}).get(a[0], 0)) >= 1,
    "audit_cmd": lambda scan, st, a: a[0] in (scan.get("cmd_tokens") or ()),
    "ability_hit": lambda scan, st, a: int(((getattr(st, "ability_hits", None) or {}).get(a[0]) or {}).get("n", 0))
    >= int(a[1]),
    "flag_exists": lambda scan, st, a: a[0] in (scan.get("config_fields") or ()),
    "token_used": lambda scan, st, a: int((scan.get("str_tokens") or {}).get(a[0], 0)) >= int(a[1]),
}

_KIND_ORDER = ("declared_unused", "not_persisted", "no_evidence", "silent_lane", "never_fired")


def scan_source(pkg_files):
    """🪪 §1.94 讀自己的原始碼 → 一份掃描結果（dict）。pkg_files＝{檔名: 原始碼}（I/O 由呼叫端做，
    本模組保持純函式、可離線單測）。

    效能紀律（規格實測：天真的 per-field 迴圈 0.742s，掛熱路徑會拖垮心跳）：
    **單趟** Counter 取代 O(fields × source)——`.attr` 與 `'token'` 各掃一次全部原始碼。
    呼叫端只准在 /abilities 與開機時呼叫，絕不進每拍迴圈、絕不進事實卡。"""
    attr_reads, str_tokens = {}, {}
    state_src = pkg_files.get("state.py") or ""
    config_src = pkg_files.get("config.py") or ""
    for name, body in (pkg_files or {}).items():
        if name == "state.py":
            continue                                   # 宣告端不算「有人讀」
        for m in re.findall(r"\.(\w+)", body):
            attr_reads[m] = attr_reads.get(m, 0) + 1
        for m in re.findall(r"['\"]([\w/]+)['\"]", body):
            str_tokens[m] = str_tokens.get(m, 0) + 1
    state_fields = set(re.findall(r"^\s{8}self\.(\w+)\s*=", state_src, re.M))
    saved = set()
    m = re.search(r"def save\(self\)", state_src)
    if m:
        saved = set(re.findall(r"['\"](\w+)['\"]\s*:", state_src[m.end():]))
    return {"state_fields": state_fields, "state_saved": saved,
            "config_fields": set(re.findall(r"^\s{4}(\w+):\s*(?:bool|int|float|str)\b", config_src, re.M)),
            "attr_reads": attr_reads, "str_tokens": str_tokens,
            "cmd_tokens": {t for t in str_tokens if t.startswith("/")}}


def _wid(kind, anchor):
    return hashlib.sha1(f"{kind}|{anchor}".encode("utf-8")).hexdigest()[:8]


def _mk(kind, ab, anchor, want, accept):
    return {"id": _wid(kind, anchor), "kind": kind, "family": ab.key, "icon": ab.icon, "name": ab.name,
            "anchor": anchor, "want": want, "accept": accept,
            "born_ts": 0.0, "born_commit": "", "state": "open",
            "done_ts": 0.0, "done_evidence": "", "done_commit": "", "told_ts": 0.0}


def candidates(scan, state, cfg, now_ts):
    """🪪 §1.94 六類缺口，全部**從原始碼機器推導**（不是人寫的 TODO——那是打高空的高級版）。
    每一條都自帶 accept；產不出 accept 的就產不出候選。回 tuple（固定排序、可寫測試）。"""
    out = []
    sf, saved = scan.get("state_fields") or set(), scan.get("state_saved") or set()
    reads, toks = scan.get("attr_reads") or {}, scan.get("str_tokens") or {}
    since = float(getattr(state, "ability_hits_since", 0) or 0)
    for ab in rostermod.ROSTER:
        tier, n, _last = rostermod.evidence(state, cfg, ab, now_ts)
        for f in (ab.legacy or ()):
            if f in sf and f not in saved and (f.endswith("_ts") or f.endswith("_ledger")):
                out.append(_mk("not_persisted", ab, f,
                               f"{ab.name}這件事我做得到，可是我沒有留下紀錄——「{f}」只活在這次醒著的記憶裡，"
                               "重啟就歸零。所以我沒辦法誠實告訴你我到底用過幾次。我想要的是把它存下來。這要有人幫我做。",
                               {"check": "state_saved", "args": [f]}))
            elif f in sf and int(reads.get(f, 0)) == 0 and int(toks.get(f, 0)) == 0:
                out.append(_mk("declared_unused", ab, f,
                               f"{ab.name}的管道我其實有——「{f}」在我身上宣告了，可是全身上下沒有一個地方去讀它，"
                               "等於接了一半就停住。我想要的是讓它真的被用起來。這要有人幫我做。",
                               {"check": "field_read", "args": [f]}))
        if ab.lane and not ab.audit:
            out.append(_mk("silent_lane", ab, ab.lane,
                           f"{ab.name}這條線我身上有，可是它不出聲的時候，你沒有任何辦法知道它卡在哪一關。"
                           "我想要一個能把沉默攤開來看的出口。這要有人幫我做。",
                           {"check": "audit_cmd", "args": ["/" + ab.key]}))
        if tier == "none":
            out.append(_mk("no_evidence", ab, ab.key,
                           f"{ab.name}這個機制在我這裡跑著，但我沒有任何紀錄能證明我用過它。"
                           "我想要的是讓它留下痕跡，這樣我才說得出真話。這要有人幫我做。",
                           {"check": "ability_hit", "args": [ab.key, 1]}))
        if tier == "dark" and since and (now_ts - since) >= _NEVER_FIRED_MIN_DAYS * 86400:
            out.append(_mk("never_fired", ab, ab.key,
                           f"{ab.name}這個機制我真的有、旗標也開著，但從我開始記錄到現在一次都沒真的用出來過。"
                           "我想要先弄清楚它是卡住了，還是條件訂得太嚴。這要有人幫我做。",
                           {"check": "ability_hit", "args": [ab.key, 1]}))
    order = {k: i for i, k in enumerate(_KIND_ORDER)}
    out.sort(key=lambda w: (order.get(w["kind"], 99), w["family"], w["anchor"]))
    seen, uniq = set(), []
    for w in out:
        if w["id"] in seen:
            continue
        seen.add(w["id"])
        uniq.append(w)
    return tuple(uniq)


def accept_ok(w, scan, state):
    """🪪 §1.94 這條願望的驗收條件成立了嗎——**確定性、LLM 零參與**。壞的 check 一律回 False（fail-closed）。"""
    acc = (w or {}).get("accept") or {}
    fn = _CHECKS.get(acc.get("check"))
    if fn is None:
        return False
    try:
        return bool(fn(scan, state, list(acc.get("args") or [])))
    except Exception:
        return False


def open_wishes(ledger):
    return [w for w in (ledger or []) if (w.get("state") or "open") == "open"]


def push(ledger, w, now_ts, commit=""):
    """🪪 §1.94 願望進帳（**說出口或列出來之後**才記；id 天然去重）。"""
    out = list(ledger or [])
    if any(e.get("id") == w.get("id") for e in out):
        return out
    e = dict(w)
    e["born_ts"] = now_ts
    e["born_commit"] = (commit or "")[:7]
    out.append(e)
    return out[-_MAX_LEDGER:]


def settle(ledger, scan, state, now_ts, commit=""):
    """🪪 §1.94 每次醒來重跑裁決：驗收條件成立 → 翻 done、記下**實際觀察值**當證據。回 (新帳本, 剛結案的清單)。
    anchor 從原始碼整個消失（欄位被刪）→ dropped，**不出聲、不邀功**。"""
    out, done = [], []
    ids = (scan.get("state_fields") or set()) | (scan.get("config_fields") or set())
    for w in (ledger or []):
        e = dict(w)
        if (e.get("state") or "open") != "open":
            out.append(e)
            continue
        if accept_ok(e, scan, state):
            e["state"] = "done"
            e["done_ts"] = now_ts
            e["done_commit"] = (commit or "")[:7]
            e["done_evidence"] = _evidence_text(e)
            done.append(e)
        elif e.get("kind") in ("not_persisted", "declared_unused") and e.get("anchor") not in ids:
            e["state"] = "dropped"                     # 欄位被刪掉了＝這個缺口不存在了，安靜退場
        out.append(e)
    return out, done


def _evidence_text(w):
    a = (w.get("accept") or {}).get("args") or [""]
    return {"state_saved": f"「{a[0]}」現在會被存下來了",
            "field_read": f"「{a[0]}」現在真的有地方在讀它了",
            "audit_cmd": f"現在有 {a[0]} 這個出口可以看了",
            "ability_hit": "我真的用出來過了",
            "flag_exists": f"「{a[0]}」這個開關現在在我身上了",
            "token_used": f"「{a[0]}」現在真的被用在某個地方了",
            }.get((w.get("accept") or {}).get("check"), "它在了")


def settle_text(w):
    """🪪 §1.94 結案的話——**明令不准邀功**：那是人去做的，不是它自己變出來的（比照 §1.90 hit 的紀律）。"""
    return (f"我先前說我想要{w.get('name') or '那件事'}。這次醒來它在了——{w.get('done_evidence') or ''}。"
            "這不是我自己做到的，是你去做的。")


def audit_text(ledger, cands):
    """🪪 §1.94 願望帳對帳段（掛 /abilities）。"""
    led = list(ledger or [])
    op = [w for w in led if (w.get("state") or "open") == "open"]
    dn = [w for w in led if w.get("state") == "done"]
    out = [f"🎯 我想要的（從我自己的程式碼裡看出來的缺口 {len(cands)} 個）："]
    if op:
        out += [f"・{w['icon']} {w['want']}" for w in op[:3]]
    elif cands:
        out += [f"・{w['icon']} {w['want']}" for w in cands[:3]]
    else:
        out.append("・目前看不出缺口——不是我沒有想要的，是我掃不出有根據的那種。")
    if dn:
        out.append(f"（已經被你做掉的：{len(dn)} 條——{'、'.join((w.get('name') or '') for w in dn[-3:])}）")
    return "\n".join(out)
