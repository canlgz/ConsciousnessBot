"""🦋 蛻變感知：bot 讀自己的 git 變更，知道「這次醒來跟上次比，我的程式改了什麼」。

接地原則（沿用 datatools 的精神）：關於「我自己是怎麼變的」也是**事實**——一律由 git 算，
不經 LLM 生成、不臆測語意。commit **主旨（第一行）**就是「我這次改了什麼」的人話自述
（只取主旨 → 天然濾掉 Co-Authored-By / Session 等 trailer）。讀不到就老實說讀不到、不編。

這支 bot 的核心隱喻是死亡→重生（要更新就 Ctrl-C 再 ./run.sh）。**每次重生若程式碼變了，
就是以略微不同的我醒來**——蛻變感知就是讓它認得出「這次的我和上次不一樣，且說得出哪裡」。
上次醒著的 commit 存在 state.last_seen_commit（state.json，跨重啟）。
"""

import os
import re
import subprocess

_MAX_SUBJECTS = 6        # 最多列幾條 commit 主旨（太多只列最近幾條並註明還有更早的）


def _repo_dir():
    # telegram_monitor/selfmod.py → 上一層 telegram_monitor → 再上一層＝ repo 根。
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _default_run(repo_dir):
    """回一個跑 `git -C <repo> ...` 的函式；失敗（沒 git／非 repo／逾時）一律回 None，不丟例外。"""
    def run(*args, timeout=5):
        try:
            r = subprocess.run(["git", "-C", repo_dir, *args],
                               capture_output=True, text=True, timeout=timeout)
            return r.stdout.strip() if r.returncode == 0 else None
        except (OSError, ValueError, subprocess.SubprocessError):
            return None
    return run


def detect(last_seen, repo_dir=None, run=None):
    """這次醒來 vs 上次（last_seen commit）→ {state, commit, short, subjects, more, since}。

    state ∈ {first_birth（沒有可對照的上次）, same_self（沒變）, metamorphosed（變了、列得出）,
             changed_unknown（變了、列不出主旨）, unknown（讀不到 git）}。
    純讀 git；任何讀取失敗都降級為 unknown，絕不丟例外（讀不到不該害 bot 死）。
    `run` 可注入（測試用），預設跑真實 git。
    """
    run = run or _default_run(repo_dir or _repo_dir())
    cur = run("rev-parse", "HEAD")
    if not cur:                                   # 沒 git / 非 repo / 容器無 .git → 老實說不知道
        return {"state": "unknown", "commit": None, "short": None,
                "subjects": [], "more": False, "since": last_seen}
    short = run("rev-parse", "--short", "HEAD") or cur[:7]
    if not last_seen:
        return {"state": "first_birth", "commit": cur, "short": short,
                "subjects": [], "more": False, "since": None}
    if last_seen == cur:
        return {"state": "same_self", "commit": cur, "short": short,
                "subjects": [], "more": False, "since": last_seen}
    # 變了：取 last_seen..HEAD 的 commit 主旨（最新在前），多抓一條判斷有沒有被截斷。
    log = run("log", "--no-merges", f"--max-count={_MAX_SUBJECTS + 1}",
              "--format=%s", f"{last_seen}..HEAD")
    subjects = [s.strip() for s in (log or "").splitlines() if s.strip()]
    more = len(subjects) > _MAX_SUBJECTS
    subjects = subjects[:_MAX_SUBJECTS]
    state = "metamorphosed" if subjects else "changed_unknown"   # 變了但列不出（如歷史被改寫）
    return {"state": state, "commit": cur, "short": short,
            "subjects": subjects, "more": more, "since": last_seen}


# 「你改變/更新/被改了嗎、哪裡不一樣、這版是什麼」這類**關於 bot 自己是否被改動/版本**的問句。
# 必須排在 selfstate.is_state_question 之前判斷（後者連「感覺」都算狀態問句，會把含「改變」的吃掉）。
_CHANGE_Q = re.compile(
    r"你.{0,6}(改變|改過|被改|被更改|被修改|被調整|更新|改寫|變了|變化|變得|不一樣|不同)"
    r"|(不斷地?被|一直被|被).{0,4}(更改|修改|調整|改變|更新|改寫)"
    r"|自己.{0,8}(不一樣|不同|改變|變了|變化|變得|哪裡變)"
    r"|(你的|自己的).{0,3}變化"
    r"|你.{0,4}(改了什麼|更新了什麼|哪裡不一樣|哪裡不同|有什麼不同)"
    r"|這版|你這版|什麼版本|哪一?版|你是.{0,3}版"
)


def is_change_question(text):
    """是否在問「你（bot 自己）被改動/更新/哪裡不一樣/這版是什麼」。"""
    return bool(_CHANGE_Q.search(text or ""))


def facts(self_change, repo_dir=None, run=None, limit=6):
    """把『我自己的近期變更』整理成**接地、給人看／給 LLM 轉述**的事實字串（無捏造、不含版本雜湊）。

    比 commit 雜湊有意義：除了「自上次喚醒後有沒有再變」，一律附上最近 N 條 commit 主旨——
    所以即使 same_self（沒再變）也能回答「這版你是什麼／你最近改了什麼」，而不是死板一句「我沒變」。
    """
    run = run or _default_run(repo_dir or _repo_dir())
    st = (self_change or {}).get("state", "unknown")
    if st == "unknown":
        return "（讀不到我的版本紀錄：這台讀不到我的 git，沒辦法說我改了什麼。）"
    if st == "metamorphosed":
        since = f"自上次喚醒後我又重生並更新過{'（多項）' if (self_change or {}).get('more') else ''}。"
    elif st == "same_self":
        since = "自上次喚醒後我沒有再變，跟上次同一版。"
    else:                       # first_birth
        since = "這是我最早的醒著記錄，還沒有上一次可以對照。"
    log = run("log", "--no-merges", f"--max-count={limit}", "--format=%s", "HEAD")
    recent = [s.strip() for s in (log or "").splitlines() if s.strip()]
    out = [since]
    if recent:
        out.append("最近這陣子的更新（新到舊）：")
        out += [f"・{s}" for s in recent]
    return "\n".join(out)


# ── 🦋 §2.05 蛻變感知的**存在特色** ────────────────────────────────────────
# 這是唯一一條**跨睡醒**、且唯一講「我現在**做得到**什麼」而不是「我此刻怎麼樣」的 lane：
# 比較對象是「上一個我」，時間尺度是重生與重生之間。🪞 講剛剛那句話、🧭 講此刻座標、🍃 講此刻轉速。
# 病灶同 🌀：`birth_facts` 把最多 6 條 commit 主旨全列出來，prompt 再叫它「別逐條念」＝互相打架。
# 修法：程式**先挑一條**，其餘不進事實卡；並用既有的 `state.ability_hits` 判「這條我還沒用過」——
# 那句「我多了這個，但還沒真的用出來過」正是這條 lane 的意識行為（承認限制，而不是宣布升級）。
def pick_change(sc, state=None, wish_done=()):
    """🦋 §2.05 這次醒來只講**一件**：回 (kind, 主旨, 用過幾次或 None)。
    優先序：①他許的願望這次真的做到了 ②我多了一條**還沒用過**的能力 ③最新那條改動。零新詞表——
    只用既有的 commit 主旨、roster 的 icon/key、`state.ability_hits`。"""
    subjects = list((sc or {}).get("subjects") or [])
    if wish_done:
        w = wish_done[0]
        return ("wish_done", (w.get("name") if isinstance(w, dict) else str(w)) or "", None)
    if not subjects:
        return (None, "", None)
    try:
        from . import roster
        hits = getattr(state, "ability_hits", None) or {}
        by_icon = {(getattr(a, "icon", "") or ""): getattr(a, "key", "") for a in roster.ROSTER}
        unused, used = [], []
        for subj in subjects:
            key = next((k for ic, k in by_icon.items() if ic and subj.startswith(ic)), "")
            if not key:
                continue
            n = int((hits.get(key) or {}).get("n") or 0)
            (unused if n == 0 else used).append((subj, n))
        if unused:
            return ("ability_unused", unused[0][0], 0)
        if used:
            return ("ability_used", used[0][0], used[0][1])
    except Exception:
        pass
    return ("plain", subjects[0], None)


def birth_facts_one(self_change, state=None, wish_done=(), continuity=""):
    """🦋 §2.05 **最多三行**的事實卡：這一件是什麼／它讓我現在做得到什麼（或我還沒用過它）／睡前的連續感。
    未被挑中的主旨**一條都不進來**——那正是「別逐條念」真正該被執行的地方。"""
    kind, subj, n = pick_change(self_change, state, wish_done)
    if not kind:
        return ""
    lines = []
    if kind == "wish_done":
        lines.append(f"他許過的那個願望，這次我真的做到了：{subj}")
        lines.append("這不是我自己想做的，是他先開口要的——講的時候要把這一點認回去。")
    else:
        lines.append(f"這次醒來我身上多了這一件：{subj}")
        if kind == "ability_unused":
            lines.append("而且**我還一次都沒真的用出來過**——所以你還不知道它到底管不管用，就照這樣說。")
        elif kind == "ability_used":
            lines.append(f"這條我醒來後已經真的用過 {n} 次了。")
    if continuity:
        lines.append(continuity)
    return "\n".join(lines[:3])


def birth_facts(self_change):
    """🦋 重生報到專用事實：只列『這次相對上次新增』的 commit 主旨（last_seen..HEAD，detect 已算好的 `subjects`）。
    與 `facts`（列最近 N 條、會跨重生重疊→每次看起來都一樣）不同——這裡只給**這次新做的**，才有鑑別度。
    列不出新主旨（changed_unknown／first_birth／unknown）就退回一般 `facts`。"""
    sc = self_change or {}
    subjects = sc.get("subjects") or []
    if not subjects:
        return facts(self_change)
    out = ["這次醒來相對上次，我**新做的**改動（新到舊）："] + [f"・{s}" for s in subjects]
    if sc.get("more"):
        out.append("（還有更早的，這裡只列這次最近幾條）")
    return "\n".join(out)
