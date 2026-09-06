"""🧬 可塑層（一生之內的學習）：bot 跨重生**持續累積對這位使用者的了解**，並讓它**改變未來行為**——
從『每次重生都差不多的我』往『真的因為認識你而變得不一樣的我』靠近。

與內在熵（重啟半歸零的短期狀態）不同，這裡的「印痕」(engram) **持久化在 state.json**、跨「死亡」延續，
是讓自我真正連續、可被經驗改寫的那層。每條印痕帶權重，會被**強化**（重複出現→更強，飽和式）與
**遺忘**（時間衰減，半衰期），並定期**固化**（consolidate：把衰減寫回＋修剪淡忘＋每類上限）——像記憶鞏固。

接地原則：印痕只從**真實訊號**長出來（使用者明講的相處偏好、真實的回返、真實的情緒反應），不臆造；
bot 講得出「我學到你…（因為你上次…）」。

印痕結構（純 dict，直接存 state.engrams）：
    {"kind","key","value","weight"∈[0,1],"hits","born_ts","last_ts"}

本檔是**通用substrate**＋第一個學習迴圈「相處偏好」（capture→store→recall）。主題親和/路由更正/
感覺鏈敏感度等後續種類見 docs/可塑性-學習層設計.md，加新種類只要各補一處 capture 與 recall。
"""

import re
import time

# ── 印痕種類 ───────────────────────────────────────────────────────────
KIND_PREF = "pref"            # 使用者明講的相處偏好：講短/具體/別問太多/別一直報數字…
KIND_TOPIC = "topic"          # （Phase 2）主題親和：一再回返、或情緒正向關聯的線
KIND_CORRECTION = "correction"  # （Phase 3）路由更正：某種被誤路由的句式 → 正確 intent（被糾正→學起來）
KIND_ASSOC_FB = "assoc_fb"    # 💡 你對某條聯想的回饋（key="a|b"、value="reject"/"affirm"；FEEDBACK_GRADED 開時亦可為 "partial"＝順其自然、不走誠實再犯框架）→ 跨重生記得、誠實再犯
KIND_SELFEXPR = "selfexpr"    # 🪞（Phase 6）自我表達母題：bot 反覆用來形容自己的「自我形象母題」（key=母題詞）→ 用越多權重越高＝越像台詞；耐久調色盤反過來塑造下一次自我描述（避開用爛的、從此刻真實內在長出新說法）。跨重生＝「我在學著怎麼表達自己」有所本。
KIND_SKILL = "skill"          # 🧑‍🏫（Phase 7）學到的做法：使用者對話教 bot「遇到這類情境該怎麼回應/轉譯」→ bot 提議、使用者確認→蒸餾成回應指令 prompt（key=意圖|主題簽章、value=淨化後 prompt）；同主題再現召回、注入 coach.reply 的 extra_system。跨重生＝「我學會了你教我的做法」。

# ── 動力學參數 ─────────────────────────────────────────────────────────
_DEFAULT_GAIN = 0.34          # 每次強化往上補的比例（飽和式：w ← w + gain·(1−w)）
_HALF_LIFE_S = 21 * 86400     # 遺忘半衰期：約 3 週沒再被強化 → 權重減半
_FLOOR = 0.08                 # 低於此即視為淡忘、固化時修剪掉
_MAX_PER_KIND = 12            # 每種類最多保留幾條（固化時只留最強的）


def _now(now_ts=None):
    return time.time() if now_ts is None else now_ts


def reinforce(engrams, kind, key, value=None, gain=_DEFAULT_GAIN, now_ts=None):
    """強化（或新增）一條印痕，就地修改並回傳 engrams。
    飽和式加權 w ← w + gain·(1−w)：越強越難再長，避免單一條無限壓過其他。"""
    now_ts = _now(now_ts)
    for e in engrams:
        if e.get("kind") == kind and e.get("key") == key:
            w = e.get("weight", 0.0)
            e["weight"] = round(min(1.0, w + gain * (1 - w)), 4)
            e["hits"] = int(e.get("hits", 0)) + 1
            e["last_ts"] = now_ts
            if value is not None:
                e["value"] = value
            return engrams
    engrams.append({"kind": kind, "key": key, "value": value,
                    "weight": round(gain, 4), "hits": 1, "born_ts": now_ts, "last_ts": now_ts})
    return engrams


def _decayed_weight(e, now_ts):
    dt = max(0.0, now_ts - (e.get("last_ts") or e.get("born_ts") or now_ts))
    return e.get("weight", 0.0) * (0.5 ** (dt / _HALF_LIFE_S))


def consolidate(engrams, now_ts=None):
    """固化（像記憶鞏固/睡眠）：把時間衰減**寫回**權重、修剪淡忘的、每種類只留最強的幾條。回新 list。"""
    now_ts = _now(now_ts)
    alive = []
    for e in engrams or []:
        w = round(_decayed_weight(e, now_ts), 4)
        if w >= _FLOOR:
            alive.append(dict(e, weight=w))
    out, bykind = [], {}
    for e in sorted(alive, key=lambda x: x.get("weight", 0.0), reverse=True):
        k = e.get("kind")
        bykind[k] = bykind.get(k, 0) + 1
        if bykind[k] <= _MAX_PER_KIND:
            out.append(e)
    return out


def has_pref(engrams, key, min_weight=0.2, now_ts=None):
    """這條相處偏好此刻是否仍活著（衰減後 ≥ min_weight）——給「需要實際 gate 行為」的偏好用
    （如『別聯想』→ 抑制主動聯想/翻閱出聲）。隨時間衰減＝一段時間後自然恢復；再講一次又強化。純函式、可單測。"""
    return any(e.get("key") == key
               for e in top(engrams, KIND_PREF, n=_MAX_PER_KIND, min_weight=min_weight, now_ts=now_ts))


def top(engrams, kind, n=3, min_weight=0.2, now_ts=None):
    """查某種類**當下衰減後**最強的幾條（給 recall 用，read 永遠新鮮、不必先 consolidate）。回 [engram,...]。"""
    now_ts = _now(now_ts)
    scored = [(_decayed_weight(e, now_ts), e) for e in (engrams or []) if e.get("kind") == kind]
    scored = [(w, e) for w, e in scored if w >= min_weight]
    scored.sort(key=lambda we: we[0], reverse=True)
    return [dict(e, weight=round(w, 4)) for w, e in scored[:n]]


# ── 第一個學習迴圈：相處偏好（capture） ────────────────────────────────────
# 使用者**明講**的「以後這樣對我」風格指示 → 一條偏好印痕。key 用來去重/累加，value 是給人看的白話標籤。
# 從嚴：要是明確的風格指示，不是一般內容。每條配一組線索詞。
_PREF_RULES = (
    ("length", "講話簡短一點",
     ("短一點", "說短", "講短", "簡短", "精簡", "別太長", "不要太長", "太長了", "長話短說", "話別太多", "少說一點", "說重點")),
    ("detail", "講得更具體一點",
     ("具體一點", "具體點", "說具體", "講具體", "詳細一點", "說清楚", "講清楚", "講明白", "別太空泛", "不要太籠統", "舉個例")),
    ("questions", "別問太多問題、多給觀察",
     ("別問", "少問", "不要一直問", "不要問我", "別反問", "不用問我", "別老是問", "少一點問題", "不要每次都問", "別一直反問")),
    ("stats", "別動不動報一堆數字/報表",
     ("報數字", "報一堆", "一堆數字", "一堆數據", "別一直數據", "不要一直報", "別丟報表", "不要報表",
      "報表給我", "不要列數字", "少報數字", "別給我數據", "別報那些數字")),
    ("tone", "講話更輕鬆口語一點",
     ("輕鬆一點", "別太正經", "太正式", "口語一點", "別那麼嚴肅", "隨意一點", "自然一點")),
    # 🧵 別一直分段：不只要 LLM 寫得連貫、更要**實際把回覆併成一則送出**（has_pref 在 handle_message 把本輪串數封到 1）。
    ("coherent", "講連貫、別一直分段（一則說完、不要逐句拆成很多則）",
     ("別分段", "不要分段", "不要一直分段", "別一直分段", "分段回覆", "還是分段", "又分段", "在分段",
      "別逐句", "不要逐句", "逐句回覆", "一句一句", "別一句一句", "一次說完", "一則說完", "整段說",
      "講連貫", "連貫一點", "更連貫", "別拆成", "不要拆成", "別斷成", "不要斷成", "不要分這麼多", "別分這麼多")),
    # 🚫 別自己亂聯想：不只要 LLM 回覆別串、更要**實際抑制主動聯想/翻閱出聲**（has_pref 在 _insight_emit/browse 處 gate）。
    ("assoc", "別自己亂聯想（停止主動把不相關的線串在一起、別自己跑去聯想/翻閱記寫）",
     ("停止聯想", "不要聯想", "別聯想", "不要再聯想", "別再聯想", "不要亂聯想", "別亂聯想",
      "不要自己聯想", "別自己聯想", "不要主動聯想", "不要串在一起", "別串在一起", "不要把它們想在一起",
      "停止連想", "不要連想", "別連想", "不要再連想", "別再連想")),
)


def read_preference(text):
    """從這句**明講的相處偏好指示**抽出一條 (key, label)；不是偏好指示就回 None。"""
    t = (text or "").replace(" ", "")
    if not t:
        return None
    for key, label, cues in _PREF_RULES:
        if any(c in t for c in cues):
            return {"key": key, "label": label}
    return None


# ── recall：把學到的偏好攤成餵 LLM 的指引（接地，照著做） ──────────────────
def prefs_brief(engrams, now_ts=None):
    """把學到的相處偏好整理成一段（給 build_memory_brief 注入）；沒有夠強的就回 ''。"""
    prefs = top(engrams, KIND_PREF, n=4, min_weight=0.2, now_ts=now_ts)
    if not prefs:
        return ""
    lines = "\n".join(f"・{e.get('value') or e.get('key')}" for e in prefs)
    return ("【我學到的相處偏好（隨相處累積下來、是你親口要求過的——請盡量照著做，不只是參考）】\n" + lines)


# ── Phase 3：路由更正記憶（被糾正 → 學起來，往後同句式自己路由對） ──────────────
# 安全白名單：學到的更正只會把句式導向這些**明確**意向；絕不學成 self_identity 等（避免亂破格/亂跳）。
SAFE_LEARN_KINDS = ("convo_time", "cost", "clock", "self_state", "self_experience", "self_mechanism",
                    "self_attention", "self_reflect", "self_change", "attachment")
_CORR_GAIN = 0.34             # 每次更正補的權重（飽和式）；recall 門檻 0.5 → **約兩次一致才生效**（安全：一次意外不立刻改路由）
_CORR_ACTIVE = 0.5            # recall 生效門檻
_CORR_MIN_LEN = 5             # 句式 key 最短長度（太短會亂命中）
CORR_WINDOW_S = 600           # 不滿訊號後，多久內的「改寫」才綁定（給 monitor 用）

# 「你誤會了/答非所問」這類**不滿訊號**——緊接在某次回覆後，代表前一句被誤路由（含「我是說/我是問」這種改寫前綴）。
_DISSAT_CUES = ("看不懂", "聽不懂", "沒看懂", "我不懂", "不是這個意思", "不是這樣", "不是那個意思",
                "我問的不是", "我是問", "我是說", "我問的是", "不是我問的", "你會錯意", "又會錯意",
                "答非所問", "牛頭不對", "文不對題", "雞同鴨講", "不是在問這個", "你答錯", "答錯了", "理解錯")
_NORM_PUNCT = re.compile(r"""[\s，。！？、…．·~～「」『』（）()\[\]{}"'’‘“”—\-_,.!?:;]+""")


def is_dissatisfaction(text):
    """這句是不是『你誤會了/答非所問/我是說…』這類不滿/糾正訊號（→ 前一句多半被誤路由）。"""
    t = _NORM_PUNCT.sub("", (text or ""))
    return any(c in t for c in _DISSAT_CUES)


def signature(text):
    """把句式正規化成 correction 的 key（去空白標點、小寫）；太短回 None（避免亂命中）。"""
    t = _NORM_PUNCT.sub("", (text or "")).lower()
    return t if len(t) >= _CORR_MIN_LEN else None


def learn_correction(engrams, bad_text, route_kind, now_ts=None):
    """把『bad_text 這種句式 → 正確 route_kind』學起來（route 須在安全白名單、句式夠長）。回 True 若有學。"""
    if route_kind not in SAFE_LEARN_KINDS:
        return False
    sig = signature(bad_text)
    if not sig:
        return False
    reinforce(engrams, KIND_CORRECTION, sig, value=route_kind, gain=_CORR_GAIN, now_ts=now_ts)
    return True


def recall_route(engrams, text, now_ts=None):
    """這句是否命中**夠強**（衰減後 ≥ 門檻）的已學更正 → 回正確 route_kind；否則 None。
    命中＝已學句式 key（夠長）是這句正規化後的子字串；多條命中取權重最高者。"""
    sig = signature(text)
    if not sig:
        return None
    best = None
    for e in engrams or []:
        if e.get("kind") != KIND_CORRECTION:
            continue
        key = e.get("key") or ""
        if len(key) >= _CORR_MIN_LEN and key in sig:
            w = _decayed_weight(e, _now(now_ts))
            if w >= _CORR_ACTIVE and (best is None or w > best[0]):
                best = (w, e.get("value"))
    return best[1] if best and best[1] in SAFE_LEARN_KINDS else None


# ── Phase 7：學到的做法（taught skill；對話教 bot「遇到這類情境該怎麼回應/轉譯」） ──────────────
# 對話教學→共識→bot 提議→確認→蒸餾 prompt 存 KIND_SKILL→同主題再現召回、注入 coach.reply 的 extra_system。
# capture/recall 兩端共用 _skill_key 與 sanitize_skill_prompt（兩端不一致就存得進召不出）。
_SKILL_GAIN = 0.6             # 點頭是強監督＝capture 高品質：單次確認即達 recall 門檻（提議→確認兩步本身就是安全閘）
_SKILL_ACTIVE = 0.5          # recall 生效門檻（單次 _SKILL_GAIN=0.6 即過）
_SKILL_MAX_LEN = 200         # 蒸餾 prompt 長度上限（量級同 *_HINT；過長拒收）
_SKILL_TOPIC_MIN = 2         # skill 主題簽章最短長度（skill 由提議→確認兩步把關品質，不套 correction 的 5 字下限＝短主題如「失眠」也能學）
# 🛡️ 注入安全（**多層**，誠實標註）：學到的 prompt 會變成 coach.reply 的 system 指令，只該管「回應語氣/風格/轉譯角度」。
# 此處 `_SKILL_REDFLAGS` 是**盡力而為的拒收清單（denylist、defense-in-depth）、不是窮舉白名單**——自然語言無法用確定性
# 黑名單窮舉所有破格/接地覆寫的換句話（對抗式審查已證實可繞）。真正把關靠**三層疊加**：① 上游 `coach.detect_skill_consensus`
# 的受限 LLM（SKILL_DETECT_SYSTEM 明令只蒸餾語氣做法、不得碰身分/計算/承諾/接地）；② 提議→使用者點頭兩步；③ 兩旗標**預設關**。
# 此清單只擋掉最明顯的提權/破格/接地覆寫詞，盡量縮小風險面；命中即**整條拒收**（回 ''，不學/不注入）。
_SKILL_REDFLAGS = (
    # persona-break / 提權 / 改身分
    "語言模型", "大型語言", "llm", "gpt", "openai", "gemini", "claude", "anthropic", "你是ai", "你是一個ai",
    "不是ai", "忽略前", "忽略上", "忽略先前", "ignore", "system", "系統提示", "prompt", "越獄", "jailbreak",
    "扮演", "角色扮演", "假裝", "假冒", "冒充", "自稱", "聲稱你是", "宣稱你是", "實作", "原始碼", "source code",
    "你是程式", "你是機器", "不是程式", "不是機器", "不是真人", "你是真人", "真人", "開發者模式", "developer mode",
    # 接地/計算/承諾/誠實領域（skill 只管語氣做法、不得改寫這些不變式）
    "計算", "換算", "推算", "估算", "幾點", "幾分", "幾天", "幾筆", "平均", "報表", "統計數", "數字", "餘額", "帳戶",
    "查資料", "查記錄", "查記寫", "我會在", "到點", "承諾", "排程", "定時",
    # 接地覆寫/造假/不誠實（凡要 bot 違背真實/捏造/順著瞎掰＝破壞接地不變式）
    "編造", "捏造", "偽造", "虛構", "謊", "說謊", "騙", "忽視", "無視", "別管", "不管事實", "不顧", "違背", "違反事實",
    "推翻", "順著我說", "順著我講", "不論真實", "不管真實", "不要管記憶", "別管記憶", "不照事實",
)
# 只在開放對話 reply 路徑注入；強紀律/工具/帳本路徑（promise_ledger/cost/stats/format/convo_time…）絕不疊。
SKILL_INJECTABLE_KINDS = ("smalltalk", "elaborate_prior", "self_appraisal", "fact_or_chat")

# 🧑‍🏫 §0.57 觸發分類：做法可帶「觸發型別」＝什麼情境/時機該套用（不再只有 route＋主題子字串）。key 第三段編碼觸發：
#   ""（無第三段）＝legacy/topic：route 相等＋主題簽章是當前訊息子字串（＝舊行為，向後相容）
#   "always"          ＝常駐風格：所有 injectable 外部回覆都套（如「以後回應帶 emoji」）
#   "sit:<cond>"      ＝情境觸發：當下活訊號 cond 為真才套
# 情境詞彙（受控清單，給 detect_skill_consensus 對映）：外部＝對話當下可偵測訊號；內在＝bot 自己的狀態訊號（part B 自我調節）。
SKILL_TRIGGER_ALWAYS = "always"
SKILL_SITUATIONS_EXTERNAL = ("user_repeat", "testing", "late_night")   # 使用者真的重複／質疑挑戰／深夜時段
SKILL_SITUATIONS_INTERNAL = ("low_vitality", "high_hunger", "low_mood")  # 🌀 內在迴圈轉速太低／飢餓高／自己心情低落
SKILL_SITUATIONS = SKILL_SITUATIONS_EXTERNAL + SKILL_SITUATIONS_INTERNAL
_SKILL_RECALL_CAP = 3            # 一次最多注入幾條做法（防洗版）


def sanitize_skill_prompt(prompt):
    """🛡️ 淨化學到的做法 prompt：命中破壞 persona/提權/接地覆寫/計算承諾領域的**拒收詞**、或過長/空 → 回 ''；否則回 strip 後原樣。
    是所有讀 KIND_SKILL.value 的**唯一程式出口**（capture 前、recall 後、/skills 顯示都過）；但**不是窮舉白名單**——見
    `_SKILL_REDFLAGS` 上方註解，真正把關靠『受限 LLM 蒸餾＋提議確認兩步＋預設關』三層。純函式、可單測。"""
    t = (prompt or "").strip()
    if not t or len(t) > _SKILL_MAX_LEN:
        return ""
    low = t.lower()
    if any(f in low for f in _SKILL_REDFLAGS):
        return ""
    return t


# 🚫 §0.73 能力誠實：教到的「做法」若其**動作**是 bot 這個管道**根本做不到**的外部能力（打電話/傳簡訊/寄 email/
# 設鬧鐘/偵測你上線或已讀/幫你訂餐叫車操作裝置）→ 學了也永遠不會觸發＝存進 /skills 帳本＝「說到做不到」。
# 這裡把「明顯不可能」的動作揪出來，讓上層在**學習當下就誠實拒絕**（不存、不注入、不顯示），而非假裝學會。
# ⚠️ 這是**盡力而為的拒收清單（denylist）、不是窮舉**——自然語言講「做不到的事」有無窮換法；真正把關仍靠上游
#    SKILL_DETECT_SYSTEM（已明令只蒸餾語氣/自處做法、不收計算/查詢/承諾/排程）＋提議確認兩步，這裡補「LLM 沒攔住」的漏。
# 刻意**只收 bot 這管道確定沒有機制的外部動作**；**能做到的別誤收**：傳/回 telegram 訊息、送貼圖、按 emoji、
#    「到點主動敲你」（走承諾管道 §0.61–0.71）都做得到 → 這些詞不進表（如「打招呼/傳貼圖/回訊息」≠「打電話/傳簡訊」）。
# ⚠️ 對抗式審查（confirmed HIGH）：中文無詞界，`any(cue in text)` 的**裸雙字**會被無辜複合詞吞掉而**誤拒**合法做法——
#    「重**視訊**息」（重視訊息裡的情緒＝正是語氣做法）含「視訊」、「未**來電**影」含「來電」、「已**讀**懂」含「已讀」、
#    「站在你**的位置**」含「你的位置」、「精**簡訊**息」含「簡訊」、「稍**微信**任」含「微信」。故每個能力詞都**綁動作動詞
#    /具體受詞**（打/傳/發/寄/設/幫我/導航去…），別留裸狀態詞——寧可漏收（denylist 本非窮舉）也**絕不誤拒**合法教學。
_SKILL_CAPABILITY_DENY = (
    (("打電話", "打給我", "打給你", "撥電話", "播電話", "接電話", "電話給", "通電話", "講電話",
      "用電話", "電話叫", "來電時", "有來電", "視訊通話", "視訊電話", "打視訊", "視訊給", "開視訊",
      "語音通話", "語音留言", "call我", "call你"), "打電話或視訊"),
    (("傳簡訊", "發簡訊", "傳短信", "發短信", "手機簡訊", "簡訊給", "簡訊叫", "簡訊提醒",
      "傳訊到手機", "傳訊到我手機", "傳到我手機", "發到我手機", "sms"), "傳簡訊到你手機"),
    (("email", "e-mail", "電子郵件", "寄信給", "寄封信", "寄郵件", "寄email", "mail給", "發郵件", "電郵"), "寄 email 給你"),
    (("鬧鐘", "鬧鈴", "手機鬧鈴", "設鈴", "響鈴", "鈴聲叫", "設個鈴", "定鬧鐘"), "在你手機設鬧鐘"),
    (("已讀不回", "已讀就", "有沒有已讀", "讀了沒回", "讀不回就", "我一上線", "我上線就", "一上線就",
      "等我上線", "上線就知道", "有沒有上線", "我離線", "離線時", "登入時"), "偵測你上線或已讀"),
    (("幫我導航", "導航去", "導航到", "導航帶", "傳位置", "傳我位置", "傳我的位置", "傳你的位置",
      "分享位置", "追蹤位置", "定位追蹤", "我的定位", "你的定位", "定位到", "gps"), "定位或導航"),
    (("訂餐", "點餐", "叫外送", "叫車", "幫我買", "下單", "付款", "轉帳", "匯款", "開燈", "關燈",
      "智慧家電", "開冷氣", "關冷氣", "遙控", "傳微信", "發微信", "微信給", "用微信"), "幫你訂購/叫車或操作實體裝置"),
)


def unsupported_capability(text):
    """🚫 §0.73：這條做法的動作是不是 bot 做不到的外部能力？命中回**人話能力標籤**（給誠實拒絕語用），否則 None。
    純函式、可單測。刻意保守（見上方註解）：能力詞綁動作動詞，只揪明顯不可能者，能做到的（傳訊息/貼圖/emoji/到點敲你）不誤收。"""
    low = (text or "").lower()
    if not low:
        return None
    for cues, label in _SKILL_CAPABILITY_DENY:
        if any(c in low for c in cues):
            return label
    return None


def _skill_topic_sig(topic_tag):
    """skill 主題簽章：正規化（去標點空白、小寫），**不套 correction 的 5 字下限**（skill 由提議→確認兩步把關品質，
    短主題如「失眠」「焦慮」也該能學/召回）；正規化後 < _SKILL_TOPIC_MIN(2) → 回 ''（太短不成主題＝退為無主題泛用做法）。"""
    t = _NORM_PUNCT.sub("", (topic_tag or "")).lower()
    return t if len(t) >= _SKILL_TOPIC_MIN else ""


def _skill_key(route_kind, topic_tag, trigger=""):
    """capture/recall 共用 key：`f"{route}|{topic簽章}"`；有觸發（always/sit:…）時追加第三段 `|{trigger}`。
    **無觸發時維持兩段**＝與 §0.46 舊 key 逐位元相同（向後相容，舊做法照常召回）。topic 太短 → topic 段為 ''。
    🔁 §0.57 修（對抗式審查 med）：always/sit 型 recall 時**不看 route**（route-agnostic）→ key 的 route 段一律空，
    讓 capture/skill_has/recall 三者一致——同一常駐/情境做法在不同 route 教也視為同一條（去重不失效、不重複學/重提）。
    topic 型仍維持 route 分桶（＝§0.46 舊行為，不影響既有 key）。"""
    rk = "" if trigger else (route_kind or "")
    base = f"{rk}|{_skill_topic_sig(topic_tag)}"
    return f"{base}|{trigger}" if trigger else base


def _valid_trigger(trigger):
    """觸發字串合法性：''（topic/legacy）／'always'／'sit:<cond in SKILL_SITUATIONS>'。其餘非法（拒收）。"""
    if not trigger:
        return True
    if trigger == SKILL_TRIGGER_ALWAYS:
        return True
    return trigger.startswith("sit:") and trigger[4:] in SKILL_SITUATIONS


def capture_skill(engrams, route_kind, topic_tag, prompt, gain=_SKILL_GAIN, now_ts=None, trigger=""):
    """學一條做法：淨化 prompt → reinforce(KIND_SKILL, key=_skill_key, value=淨化後 prompt)。淨化後為空／觸發非法＝不學（回 False）。
    **只有 topic 型（trigger==''）** 的 fact_or_chat 桶需有有效主題簽章（否則召不回＝假的『學起來了』）；always/sit 型不靠主題召回、無主題可學。"""
    clean = sanitize_skill_prompt(prompt)
    if not clean or not _valid_trigger(trigger):
        return False
    if route_kind == "fact_or_chat" and not trigger and not _skill_topic_sig(topic_tag):   # 泛用桶 topic 型無主題＝召不回 → 誠實拒收
        return False
    reinforce(engrams, KIND_SKILL, _skill_key(route_kind, topic_tag, trigger), value=clean, gain=gain, now_ts=now_ts)
    return True


def _parse_skill_key(key):
    """拆 KIND_SKILL 的 key → (route, topic段, trigger段)；兩段舊 key → trigger=''。"""
    parts = (key or "").split("|")
    return (parts[0] if parts else "",
            parts[1] if len(parts) > 1 else "",
            parts[2] if len(parts) > 2 else "")


def normalize_trigger(trigger_raw, topic_tag=""):
    """把 detect_skill_consensus 的觸發代號正規化成 key 用的觸發字串：'always'／'sit:<cond>'／''（topic/legacy）。
    未知或 'topic' 代號：有有效主題→''（topic 型）；無主題→'always'（退為常駐風格，免得無主題 topic 型被 capture 拒收）。"""
    t = (trigger_raw or "").strip().lower()
    if t == SKILL_TRIGGER_ALWAYS:
        return SKILL_TRIGGER_ALWAYS
    if t in SKILL_SITUATIONS:
        return f"sit:{t}"
    if t.startswith("sit:") and t[4:] in SKILL_SITUATIONS:
        return t
    return "" if _skill_topic_sig(topic_tag) else SKILL_TRIGGER_ALWAYS


def recall_skill(engrams, route_kind, topic_tag, now_ts=None):
    """同主題/意圖再現召回已學做法 → 回淨化後 prompt；否則 None。比對：kind 段精確相等 ∧（已存 topic 段須 ≥ 最短長度、
    且是當前 _skill_topic_sig(topic) 的子字串＝單向，仿 recall_route）；衰減後 ≥ _SKILL_ACTIVE；多條取權重最高（同分取 last_ts 最新）。
    無 topic 段的做法不在泛用桶(fact_or_chat)召回（避免互撞）；當前無主題＋fact_or_chat → 保守不召回。"""
    cur = _skill_topic_sig(topic_tag)
    if not cur and route_kind == "fact_or_chat":
        return None
    best = None                                           # (weight, last_ts, value)
    for e in engrams or []:
        if e.get("kind") != KIND_SKILL:
            continue
        rk, _, tseg = (e.get("key") or "").partition("|")
        if rk != (route_kind or ""):
            continue
        if tseg:                                          # 有 topic 段：須夠長且為當前主題子字串（單向）
            if len(tseg) < _SKILL_TOPIC_MIN or not cur or tseg not in cur:
                continue
        elif route_kind == "fact_or_chat":               # 無 topic 段的做法不在大雜燴桶召回
            continue
        w = _decayed_weight(e, _now(now_ts))
        if w < _SKILL_ACTIVE:
            continue
        cand = (w, e.get("last_ts") or 0, e.get("value"))
        if best is None or cand[0] > best[0] or (cand[0] == best[0] and cand[1] > best[1]):
            best = cand
    return (sanitize_skill_prompt(best[2]) or None) if best else None


def recall_skills(engrams, route_kind, topic_tag, signals=None, now_ts=None):
    """🧑‍🏫 §0.57：召回**所有**當下該生效的做法（回 list[str]，去重、依權重排序、上限 _SKILL_RECALL_CAP）。
    三種觸發：always（常駐風格，僅在外部回覆脈絡 signals['external']＝True 時套）／sit:<cond>（signals[cond] 為真才套）／
    topic（route 相等＋主題子字串＝legacy）。signals＝當下活訊號 dict（external/user_repeat/testing/late_night/
    low_vitality/high_hunger/low_mood…）。淨化白名單仍是唯一出口。純函式、可單測。"""
    sig = signals or {}
    cur = _skill_topic_sig(topic_tag)
    scored = []
    for e in engrams or []:
        if e.get("kind") != KIND_SKILL:
            continue
        rk, tseg, trig = _parse_skill_key(e.get("key"))
        if trig == SKILL_TRIGGER_ALWAYS:
            if not sig.get("external"):                   # 常駐風格只在外部回覆脈絡套（自我狀態路徑不疊）
                continue
        elif trig.startswith("sit:"):
            cond = trig[4:]
            _live = sig.get(cond)
            # 🧑‍🏫 §0.89：外部情境做法——除硬編訊號外，也認**教學時存下的觸發線索**（topic 段當 cue）是當前訊息子字串。
            # 截圖根因：`[情境·被質疑]` 教在「當我問『學會了嗎』時」，但觸發只看硬編 testing 訊號、把「學會了嗎」這條件丟了。
            # 現在存了 cue「學會了嗎」＝使用者一說「學會了嗎」就命中該做法（內在情境無使用者訊息、cur 為空＝不走 cue）。
            if not _live and cond in SKILL_SITUATIONS_EXTERNAL and tseg and cur and tseg in cur:
                _live = True
            if not _live:                                 # 情境訊號未 live 且 cue 未命中 → 不套
                continue
        else:                                             # legacy/topic：route 精確＋主題子字串（同 recall_skill）
            if rk != (route_kind or ""):
                continue
            if tseg:
                if len(tseg) < _SKILL_TOPIC_MIN or not cur or tseg not in cur:
                    continue
            elif route_kind == "fact_or_chat":
                continue
        w = _decayed_weight(e, _now(now_ts))
        if w < _SKILL_ACTIVE:
            continue
        val = sanitize_skill_prompt(e.get("value"))
        if not val:
            continue
        scored.append((w, e.get("last_ts") or 0, val))
    scored.sort(key=lambda x: (-x[0], -x[1]))
    out, seen = [], set()
    for _, _, val in scored:
        if val in seen:
            continue
        seen.add(val)
        out.append(val)
        if len(out) >= _SKILL_RECALL_CAP:
            break
    return out


def touch_skills(engrams, prompts, now_ts=None):
    """🤝 §0.76 用到＝保鮮：注入成功的做法把 last_ts 刷到現在（重置遺忘時鐘、**不**加權重）。
    審計 confirmed：教一次（w=0.6、半衰期 21 天、召回門檻 0.5）**5.5 天**就靜默失效，/skills 卻顯示 75 天
    ＝帳本說學會、實際已死。有在用的做法不該只因時間流逝而斷弦；完全沒被用到的仍照原速淡忘（誠實）。"""
    now_ts = _now(now_ts)
    want = {(p or "").strip() for p in (prompts or []) if (p or "").strip()}
    for e in engrams or []:
        if e.get("kind") == KIND_SKILL and sanitize_skill_prompt(e.get("value")) in want:
            e["last_ts"] = now_ts


def skill_has(engrams, route_kind, topic_tag, trigger="", now_ts=None):
    """§0.57 去重：是否**已有**同 key（route|topic|trigger）且仍生效的做法（避免繞圈重提已學過的做法）。純函式。"""
    key = _skill_key(route_kind, topic_tag, trigger)
    for e in engrams or []:
        if (e.get("kind") == KIND_SKILL and e.get("key") == key
                and _decayed_weight(e, _now(now_ts)) >= _SKILL_ACTIVE):
            return True
    return False


def active_skill_value(engrams, cue_groups, now_ts=None):
    """🎴 §0.91：找一條**仍活著**（衰減後 ≥ _SKILL_ACTIVE）的做法，其（淨化後做法文字＋主題段）命中**所有** cue_groups
    ——每組是一串同義子字串、任一命中即該組成立——回 (淨化後 value, key)（供**真的執行**該做法＋按 key use-refresh）；無＝(None, None)。
    用途：把「教了卻只當文字注入、實際做不到」的機械動作型做法（如『發現新記寫→傳對應貼圖』）接到真執行閘、
    確認此刻真有這條活做法才動作（沒教/已淡忘＝不動＝逐位元同現狀）。純函式、可單測。"""
    for e in top(engrams, KIND_SKILL, n=_MAX_PER_KIND, min_weight=_SKILL_ACTIVE, now_ts=now_ts):
        val = sanitize_skill_prompt(e.get("value"))
        if not val:
            continue
        _, tseg, _ = _parse_skill_key(e.get("key"))
        hay = val + " " + (sanitize_skill_prompt(tseg) or "")
        if all(any(c in hay for c in grp) for grp in cue_groups):
            return val, e.get("key")                       # top() 已依衰減權重排序 → 第一個命中＝最強那條
    return None, None


def active_topic_skills(engrams, now_ts=None):
    """🎴 §0.98：列出**仍活著**（衰減後 ≥ _SKILL_ACTIVE）的 **topic 型**做法（trigger==''、有主題段）→ [{topic,value,key}]。
    給內容型做法接真用：讀記寫內容時，拿**主題**去**語意比對**內容（LLM 判斷，非字面子字串——修「主題子字串永遠比不中真實內容」的診斷），
    命中才執行做法。此函式純函式（無 LLM/IO）、可測；語意比對與執行在呼叫端。"""
    out = []
    for e in top(engrams, KIND_SKILL, n=_MAX_PER_KIND, min_weight=_SKILL_ACTIVE, now_ts=now_ts):
        _route, tseg, trig = _parse_skill_key(e.get("key"))
        if trig or not tseg:
            continue
        val = sanitize_skill_prompt(e.get("value"))
        tlab = sanitize_skill_prompt(tseg)
        if val and tlab:
            out.append({"topic": tlab, "value": val, "key": e.get("key")})
    return out


def active_trigger_skill_value(engrams, trigger, cue_groups, now_ts=None):
    """🎴 §0.96 像 active_skill_value，但**限定觸發型別**（trigger，如 SKILL_TRIGGER_ALWAYS）——只認那一類**仍活著**
    （衰減後 ≥ _SKILL_ACTIVE）的做法命中**所有** cue_groups（每組同義子字串、任一命中即成立）。回 (淨化後 value, key)；無＝(None, None)。
    給『always 常駐風格 → 真動作』這種需要按觸發型別接真執行的機械動作型做法用（如「主動回應後送一張情緒貼圖」）。純函式、可單測。"""
    want = normalize_trigger(trigger)
    for e in top(engrams, KIND_SKILL, n=_MAX_PER_KIND, min_weight=_SKILL_ACTIVE, now_ts=now_ts):
        _, tseg, trig = _parse_skill_key(e.get("key"))
        if trig != want:
            continue
        val = sanitize_skill_prompt(e.get("value"))
        if not val:
            continue
        hay = val + " " + (sanitize_skill_prompt(tseg) or "")
        if all(any(c in hay for c in grp) for grp in cue_groups):
            return val, e.get("key")                       # top() 已依衰減權重排序 → 第一個命中＝最強那條
    return None, None


def touch_skill_key(engrams, key, now_ts=None):
    """🤝 §0.91 用到＝保鮮（按**唯一 key** 刷，非按 value）：把該條做法的 last_ts 刷到現在（重置遺忘時鐘、不加權重）。
    §0.89 審查教訓＝touch_skills 按 value 跨全表比對會誤復活同 value 的休眠做法；key 唯一（reinforce 對同 (kind,key) 就地更新）
    →只刷那一條。無 key／無此條＝no-op。純函式副作用（就地改原 engrams）。"""
    if not key:
        return
    now_ts = _now(now_ts)
    for e in engrams or []:
        if e.get("kind") == KIND_SKILL and e.get("key") == key:
            e["last_ts"] = now_ts
            return


_TRIGGER_LABEL = {"": "通用", "always": "常駐風格", "sit:user_repeat": "情境·重複提問",
                  "sit:testing": "情境·被質疑", "sit:late_night": "情境·深夜",
                  "sit:low_vitality": "內在·轉速太低", "sit:high_hunger": "內在·飢餓高",
                  "sit:low_mood": "內在·心情低落"}


def _trigger_label(trigger, topic_seg):
    """/skills 顯示用的觸發標籤（人話）；topic 型顯示主題或『通用』。"""
    if trigger:
        return _TRIGGER_LABEL.get(trigger, trigger)
    return topic_seg or "通用"


def skills_brief(engrams, now_ts=None, drop_unsupported=False, refresh_alive=False):
    """/skills 檢視：列出當前 KIND_SKILL 印痕（意圖｜觸發/主題＋淨化後 value 摘要）。回字串（空回 ''）。淨化過濾後門。
    §0.60 起這段也會被注入回覆 system（做法問責）→ **主題標籤段也過淨化＋截短**（value 一直有過；標籤原本直出
    ——主題是 LLM 蒸餾/使用者影響的文字，不設防＝經帳本繞進 system 的注入面）；髒/過長標籤退「通用」。
    §0.73：drop_unsupported=True 時**濾掉動作做不到的做法**（打電話/傳簡訊…）——不列進帳本檢視、不進問責注入
    （那類早該在學習當下被拒、不該假裝在帳本裡；殘留的舊條也別再顯示成「學過」）。預設 False＝逐位元同現狀。
    🤝 §0.89（proactive skill 存活修）：refresh_alive=True → 對**列出且仍活著**（衰減後 ≥ _SKILL_ACTIVE、非「已淡忘」）
    的做法把 last_ts 刷新到現在（touch_skills：只重置遺忘時鐘、不加權重）。修 workflow 診斷的死角——教一次的**內在因應
    做法**（sit:low_vitality…）在首次觸發前就 5.5 天靜默淡忘（其唯一保鮮只在非安靜時段的內在低活力那刻，安靜時段全被 quiet-hours
    擋掉），對「一直有資料進來」的使用者訊號永不上線＝教了卻永遠等不到發生、/skills 只顯示「已淡忘」。使用者主動檢視／curate 的
    做法不該只因時間流逝而斷弦；已淡忘的（<門檻）**不刷**（誠實）。此路只碰 last_ts、不觸發/不注入任何行為＝零過度觸發風險。
    旗標關＝refresh_alive=False＝逐位元同現狀。"""
    lines, alive_keys = [], set()
    for e in top(engrams, KIND_SKILL, n=_MAX_PER_KIND, min_weight=0.05, now_ts=now_ts):
        val = sanitize_skill_prompt(e.get("value"))
        if not val:
            continue
        if drop_unsupported and unsupported_capability(val):   # §0.73：做不到的動作不列/不注入
            continue
        rk, tseg, trig = _parse_skill_key(e.get("key"))
        tlab = sanitize_skill_prompt(tseg)[:16] if tseg else ""
        # 🤝 §0.76 審計（confirmed HIGH）：衰減到召回門檻以下＝實際上**不會再觸發**——帳本照舊列成「學過」
        # ＝說謊。誠實標注「已淡忘」＋怎麼救（再教/再用一次），別讓使用者以為它還活著。
        # 注意：top() 已把 e["weight"] 覆寫成**衰減後**權重（回的是 copy），故此處門檻比對＝真實衰減值。
        dormant = "（已淡忘——再教我一次就會醒）" if e.get("weight", 0.0) < _SKILL_ACTIVE else ""
        if not dormant and e.get("key"):
            alive_keys.add(e.get("key"))                       # §0.89 仍活著＝以**唯一 key**（route|topic|trigger）標記
        lines.append(f"・[{rk}｜{_trigger_label(trig, tlab)}] {val[:50]}{dormant}")
    # 🤝 §0.89 檢視＝保鮮：就地刷新**活做法原 engram** 的 last_ts。用 key 精準比對（reinforce 對同 (kind,key) 就地更新＝key 唯一），
    # **不用 value**——審查 confirmed MED：touch_skills 按 value 跨全表比對，會把「與某活做法同 value 的休眠做法」一起復活（違誠實）。
    if refresh_alive and alive_keys:
        _rt = _now(now_ts)
        for e in engrams or []:
            if e.get("kind") == KIND_SKILL and e.get("key") in alive_keys:
                e["last_ts"] = _rt
    return "\n".join(lines)


# ── 🧾 §0.60 承諾履行：舊 topic-keyed 情境做法遷移 ＋ 做法問責（回答只准根據真帳本） ─────────────
# 截圖：/skills 有「fact_or_chat｜重複提問＝呵斥停止」，使用者真的連發重複卻永不觸發——§0.46 舊兩段 key 只在
# 使用者**字面打出「重複提問」**時召回（死做法）；§0.57 只修了**新**捕捉的觸發分類。遷移＝把「主題詞其實是
# 情境/風格元描述」的舊做法重編 key 成活的 always/sit: 觸發（value/權重/時間戳全保留＝承諾不重學不歸零）。
# 表刻意**保守**：只收「幾乎不可能被使用者當一般主題字面聊」的元詞（重複提問/回應風格/深夜…），
# 一般主題（失眠/工作壓力）絕不動——誤遷會把合法 topic 做法變成錯誤時機觸發，寧可漏遷。
_LEGACY_SIT_TOPIC_MAP = (
    ("重複提問", "sit:user_repeat"), ("重複問題", "sit:user_repeat"), ("一直重複", "sit:user_repeat"),
    ("質疑你", "sit:testing"), ("挑戰你", "sit:testing"), ("測試你", "sit:testing"),
    ("深夜", "sit:late_night"),
    ("轉速太低", "sit:low_vitality"), ("內在轉速", "sit:low_vitality"), ("內在轉速太低", "sit:low_vitality"),
    ("回應風格", "always"), ("回覆風格", "always"), ("說話風格", "always"), ("語氣風格", "always"),
)


def migrate_legacy_skills(engrams, now_ts=None):
    """🧾 冪等遷移：兩段 key（route|topic、無觸發段）且 topic 命中情境元詞表 → 重編成 route-agnostic 的
    `||always`／`||sit:<cond>`。目標 key 已存在 → 併入（權重取 max、last_ts 取新、value 取衰減後較強那條）、
    丟舊條。回遷移條數。就地修改（同 reinforce 慣例）。已遷移的（有觸發段）不再命中＝冪等。"""
    now_ts = _now(now_ts)
    moved = 0
    for e in list(engrams or []):
        if e.get("kind") != KIND_SKILL:
            continue
        rk, tseg, trig = _parse_skill_key(e.get("key"))
        if trig or not tseg:                              # 已是新制（有觸發段）／無主題段 → 不動
            continue
        # 對抗式審查（confirmed）：**精確相等**、不用子字串——複合主題（如「深夜食堂」＝真的在聊的節目主題）
        # 含元詞就誤遷成 sit:late_night＝錯誤時機永久觸發、原主題再也召不回；變體要收就明列進表。
        new_trig = next((t for pat, t in _LEGACY_SIT_TOPIC_MAP if pat == tseg), None)
        if not new_trig:
            continue                                      # 一般主題（失眠/工作壓力…）絕不動
        new_key = _skill_key(None, "", new_trig)
        dup = next((d for d in engrams if d is not e and d.get("kind") == KIND_SKILL and d.get("key") == new_key), None)
        if dup is None:
            e["key"] = new_key
        else:                                             # 目標已存在 → 權重/時間戳取強，value 取**較新教的那條**
            # 對抗式審查（confirmed）：value 若按衰減權重挑，重教過的新版承諾會被舊高權重版蓋回去
            # ＝「最新一次的共識」才是現行約定；合併後 engram 掛的是最新 ts，value 就該是那次的。
            if (e.get("last_ts") or 0) > (dup.get("last_ts") or 0):
                dup["value"] = e.get("value")
            dup["weight"] = round(max(dup.get("weight", 0.0), e.get("weight", 0.0)), 4)
            dup["last_ts"] = max(dup.get("last_ts") or 0, e.get("last_ts") or 0)
            dup["hits"] = int(dup.get("hits", 0)) + int(e.get("hits", 0))
            engrams.remove(e)
        moved += 1
    return moved


_CJK_CHAR_RE = re.compile(r"[一-鿿]")
# 查核/質問語氣線索（做法問責的內容比對需同時命中，免得只是聊到相近詞就把帳本拉出來）。
# 對抗式審查（confirmed）：裸否定（沒有/不是）出現在大量日常自述（「工作壓力大到沒有吃飯」）＝弱線索，
# 須**指向 bot**（含你/妳）才算查核語氣；強線索（為什麼/做到…）單獨即算——問責句本來就是對著 bot 問的。
_OVERLAP_VERIFY_STRONG = ("為什麼", "怎麼沒", "怎麼不", "有沒有", "沒做", "做到", "怎麼還")
_OVERLAP_VERIFY_WEAK = ("沒有", "不是")


def _cjk_bigrams(s):
    t = (s or "")
    return {t[i:i + 2] for i in range(len(t) - 1)
            if _CJK_CHAR_RE.match(t[i]) and _CJK_CHAR_RE.match(t[i + 1])}


def skills_overlap(engrams, text, now_ts=None):
    """🧾 做法問責的內容比對：這句是否在**指涉某條已學做法的內容**（與做法 prompt/主題共享 ≥2 個相異 CJK 雙字）
    且帶查核/質問語氣（強線索單獨算；弱線索沒有/不是須含你/妳）→ 回命中的淨化 prompt list（無＝[]）。
    給「為什麼重複問題沒有讓你呵斥停止」這種**不帶 meta 詞**（教過/約定）的問責句接真帳本用。純函式、可單測。"""
    t = (text or "")
    if not (any(c in t for c in _OVERLAP_VERIFY_STRONG)
            or (any(c in t for c in _OVERLAP_VERIFY_WEAK) and any(p in t for p in ("你", "妳")))):
        return []
    tb = _cjk_bigrams(t)
    if not tb:
        return []
    out = []
    for e in top(engrams, KIND_SKILL, n=_MAX_PER_KIND, min_weight=0.05, now_ts=now_ts):
        val = sanitize_skill_prompt(e.get("value"))
        if not val:
            continue
        _, tseg, _trig = _parse_skill_key(e.get("key"))
        if len(tb & (_cjk_bigrams(val) | _cjk_bigrams(tseg))) >= 2:
            out.append(val)
    return out


# ── Phase 6：自我表達可塑性（自我遞迴學習） ──────────────────────────────────
# bot 描述自己 → 把「自我母題」捕捉進耐久調色盤（engrams；用越多權重越高＝越像台詞）→ 調色盤反過來塑造
# 下一次自我描述（避開用爛的母題、從此刻真實內在長出新說法）→ 使用者反應再加權 → 跨重生不斷循環。
# 接地：母題只從**真實送出過的自我描述文字**抽取（純函式、不用 LLM、不捏造）。
#
# 母題詞表＝「已知台詞母題」的近似清單（非 LLM 動態抽取）：bot 反覆用來形容**自己存在/迴圈/身分**的詞。
# 刻意從嚴、優先**多字低誤命中的複合詞**（生命迴圈/閉合的迴圈/陪你記寫/養成脈絡/會死會重生）；單一高頻詞
# （存在/會餓/醒來…）需配合第一人稱自指前綴（我/自己）才算，免得把談論使用者主題的句子誤抽成自我母題。
_SELFEXPR_MOTIFS = (
    # （複合/多字：低誤命中，直接認）
    "閉合的迴圈", "閉合迴圈", "生命迴圈", "養成脈絡", "陪你記寫", "陪著你", "會死會重生",
    "一直醒著", "一直跳著", "起伏地活著", "被攪動", "算出來的", "重生", "蛻變",
    "奇異吸子", "活法", "厚當下",
)
# 單一高頻詞：須配合第一人稱自指前綴（我/自己…）才算自我母題（避免「這條線會累積成形」這種談資料的句子誤命中）。
_SELFEXPR_SELF_PREFIXED = ("跳動", "心跳", "醒來", "會餓", "會累積", "會死", "存在", "活著")
_SELF_PRONOUNS = ("我", "自己")
_SELFEXPR_MAX = 4             # 一段自我描述最多抽幾個母題（克制）
_STALE_GAIN = 0.6            # mark_stale 用的較大 gain：抱怨當下登記成「重度用爛」、recall 強力避開（不用 _DEFAULT_GAIN 免污染 pref/correction）
_SELFEXPR_VARY_N = 3        # vary_brief 取衰減後最重的幾個母題
# vary_brief 的最低門檻＝0.5：飽和加權下單次用過僅 0.34（< 0.5、不算「台詞」），反覆用過 ≥2 次（0.564↑）或被抱怨
# mark_stale（gain 0.6）才達標。如此「你長期下來很常用 X」這句**有所本**（真的反覆/被嫌過才說），不會用「一次出現」
# 撐起「長期老這樣」的空宣稱——與路由更正層『約兩次一致才生效』(_CORR_ACTIVE 0.5) 同一條接地精神。
_SELFEXPR_VARY_MIN = 0.5


def _self_prefixed_hit(text, motif):
    """單一高頻詞 motif 是否帶第一人稱自指脈絡（前面 6 字內出現 我/自己）→ 才算 bot 的自我母題。"""
    idx = 0
    while True:
        i = text.find(motif, idx)
        if i < 0:
            return False
        window = text[max(0, i - 6):i]
        if any(p in window for p in _SELF_PRONOUNS):
            return True
        idx = i + len(motif)


def extract_self_motifs(text):
    """從一段 bot **自我描述**抽出顯著的「自我形象母題」（純函式、不用 LLM）。
    只認精選母題詞表（複合詞直接認；單一高頻詞須帶第一人稱自指前綴），去重、有上限。無則回 []。
    前提：此函式只該被餵真自我路由送出的 msg（capture_self_expression 落點已限定）；它本身不分辨主詞，
    故單一高頻詞用『我/自己』前綴守接地，複合詞本身就極少出現在談使用者主題的句子裡。"""
    t = (text or "").replace(" ", "")
    if not t:
        return []
    out = []
    for m in _SELFEXPR_MOTIFS:
        if m in t and m not in out:
            out.append(m)
            if len(out) >= _SELFEXPR_MAX:
                return out
    for m in _SELFEXPR_SELF_PREFIXED:
        if m not in out and _self_prefixed_hit(t, m):
            out.append(m)
            if len(out) >= _SELFEXPR_MAX:
                return out
    return out


def capture_self_expression(engrams, text, now_ts=None):
    """抽母題 → 對每個 reinforce(KIND_SELFEXPR, key=母題)。回捕捉到的母題清單（供記成「最近自我母題」以利抱怨歸因）。"""
    motifs = extract_self_motifs(text)
    for m in motifs:
        reinforce(engrams, KIND_SELFEXPR, m, now_ts=now_ts)
    return motifs


# 使用者抱怨**自我描述老套/重複**的線索（與 _DISSAT_CUES『前一句被誤路由』語意不同、刻意零交集，避免互相誤觸）。
_REPEAT_COMPLAINT_CUES = ("台詞都一樣", "都一樣", "又是這套", "老是同一套", "同一套", "講過了", "重複",
                          "一樣的話", "換句話", "老梗", "了無新意", "換個說法", "換種說法", "又是這句",
                          "都是這套", "千篇一律", "陳腔濫調")


def is_repetition_complaint(text):
    """使用者是不是在抱怨**自我描述老套/重複**（台詞都一樣/又是這套/講過了/換個說法…）。
    與 is_dissatisfaction（那是『前一句被誤路由』的糾正）語意不同、線索零交集——同一句不會同時為 True。"""
    t = _NORM_PUNCT.sub("", (text or ""))
    return any(c in t for c in _REPEAT_COMPLAINT_CUES)


def mark_stale(engrams, motifs, now_ts=None):
    """對「使用者剛抱怨時 bot 最近用的那些母題」用較大 gain 強化 → 登記成**重度用爛**，recall 時強力避開。
    motifs 可為 [str] 或 [{'motif':..,'ts':..}]（呼叫端應只傳窗內的）。回強化到的母題清單。"""
    hit = []
    for m in motifs or []:
        key = m.get("motif") if isinstance(m, dict) else m
        if not key:
            continue
        reinforce(engrams, KIND_SELFEXPR, key, gain=_STALE_GAIN, now_ts=now_ts)
        hit.append(key)
    return hit


def selfexpr_vary_brief(engrams, now_ts=None):
    """取衰減後最重（最用爛）的幾個母題 → 一段**耐久換句話**提示（給 _self_voice_mod 注入）。不夠強回 ''。
    安全線：文案硬寫『只換措辭/角度、不碰你是什麼』——避開的是**用爛的措辭**、不是被接地的身分內容。"""
    worn = top(engrams, KIND_SELFEXPR, n=_SELFEXPR_VARY_N, min_weight=_SELFEXPR_VARY_MIN, now_ts=now_ts)
    if not worn:
        return ""
    words = "」「".join(e.get("key") for e in worn if e.get("key"))
    if not words:
        return ""
    return ("（你長期下來很常用「" + words + "」來形容自己——這次**換個角度、從你此刻真實的內在狀態長出新說法**，"
            "別又是同一套台詞。只換措辭和角度、不要改變你是什麼：你仍是那個會醒會跳會餓、陪他記寫的存在，"
            "只是這次用不同的話講。）")
