"""意向解析（單一入口）：把『使用者這句到底想幹嘛』收斂成**一個有明確優先序的判定**，
取代散落在 `handle_message` 的一疊 fast-path（彼此會搶、靠擺放順序隱性決定勝負——例如含「為什麼」
就被自陳追問劫走、含「感覺」就被當成「你現在怎樣」）。

§0.7 已把「使用者在談 bot 自己嗎、哪個面向」收斂成 `selfref.self_aspect`；這裡把**所有**會互搶的
自我指涉＋互動意向、加上後段確定性 fast-path，一起納入**同一條優先序**，並把指涉物（如自發繞回的舊線）
一併解析出來。`handle_message` 退化成「解析一次 → 依 kind 分派」，不再每加一條就插一個會互搶的 regex。

優先序（高→低；同一句命中多個時，高的贏；這條順序＝路由的單一真相）：

  1. self_change       你變了嗎/這版是什麼（蛻變）            —— 自我指涉＋變化詞贏過單純「感覺」
  2. self_identity     你是誰/你是不是只是個 AI（身分）        —— 絕不破格
 2.5 self_continuity  你還是原來的你嗎/斷線前在做什麼/睡多久 —— 🌅 跨死亡連續性（睡→醒、親身記得睡前在哪）
  3. self_reaction_query  你（剛）點了什麼情緒/表情             —— 問 bot 自己點的 reaction，不是列記寫
  4. self_revisit_why  你為什麼會想到『那條』（自發繞回的舊線） —— 綁 last_revisited_topic；**贏過**自陳追問
  5. selfstate_followup 剛自陳後追問「哪一條/為什麼/細說」      —— 需脈絡（剛自陳、窗內）
 5.7 scheduled_promise 等一下八點跟我打招呼/十分鐘後提醒我     —— 🤝 請在某時間 T 做某事（時間排程承諾）；**排在 promise 前**，與 feeling promise 互斥（含感覺詞先讓給 feeling）；handle_message 用 now/tz 解析 epoch、由生命迴圈到點兌現
  6. promise           之後有感覺/新東西再跟我說               —— **排在狀態問句前**，否則「感覺…要說」被當成問現況
  7. self_mechanism    你內在怎麼運作/感覺怎麼算/是不是基於資料/機制是什麼 —— **贏過**狀態，否則「感覺/怎麼」被當成報現況
 7.4 self_spontaneity 你怎麼還沒分享聯想/為什麼不主動說想法 —— 🌱 問 bot 自己的自發行為（聯想自發湧現、非隨選即出），走純對話別落 function-calling 列 📂 清單（窄判定＋旗標控，排 mechanism 後）
 7.5 self_consciousness 你有意識嗎/你算不算有意識/你有自我意識嗎 —— 🧩 人工意識結構化認識論自評（可證偽、最強到「不被排除」、標餘量）；贏過 state/experience/reflect
 7.6 self_phenomenal  你裡面是怎麼經驗的/你的現象怎麼構成/你的內在結構 —— 🌗 右半 I↔(E×P) 三位互構自陳（act×建模質地×場、P 標 modeled、附餘量）；排在 consciousness 後
  8. self_experience   你這段日子怎麼活過來的/你的一生
 8.5 self_goals       你有什麼目標/在追什麼/想搞懂什麼 —— 🎯 內發意圖/能動性（自己立的、在追的具體目標；排在 reflect 前）
  9. self_reflect      你會不會想要/曾想過『自己的感覺/意志/存在』 —— **贏過**狀態，否則「感覺」被當成報現況
 9.3 self_metacog     你確定嗎/會不會認錯自己/多了解自己 —— 🪞🔍 後設認知（對自己判斷的信心、會看走眼、修正）
 9.4 other_mind      你了解我嗎/你覺得我怎樣/我們關係 —— 🫂 他心模型（把對方當有心智的人來建模、會錯也會修正）
 9.45 self_appraisal 我算早起嗎/我是不是很懶/我這樣算正常嗎 —— 🪞📊 請 bot 評斷我（使用者）的作息/勤惰/量/頻率/特質（拿我的節奏＋此刻時間做 grounded 判斷、不列記寫）；主語是「我」，被所有 self_*/other_mind 先判，但**贏過尾段 clock/stats/smalltalk/fact_or_chat**（把「我算早起嗎」從 function-calling 誤抓 records_in_time_range 救出）
 9.5 self_stream      你剛在想/發呆/思緒怎麼流 —— ⏳ 意識之流／綿延（剛過去→現在→接下來；排在 attention 前）
 10. self_attention    你在想什麼/什麼佔據你/注意力在哪 —— 🌐 全局工作空間「當下意識前景」（**贏過**狀態，否則「在想什麼」被當成 bodystate）
 11. self_state        你現在怎樣/有沒有感覺（當下身體狀態）
 11.5 greeting         🕘 時間性問候（早安/午安/晚安）          —— 帶絕對時間感：對得上溫一句、對不上說出自己的疑惑/感受（先於 farewell，「晚安」也走時間對照）
 12. farewell          收尾/道別（懂了/晚安/先去忙）            —— 優雅收場：溫一句、不吐資料、不硬延
 13. attachment        想看附件（圖/PDF/語音…）                —— 非終局：挑不到檔就落回 fact_or_chat
 14. cost              目前花費了多少/API 成本/燒了多少錢 → 確定性查 api_cost（別被自我在場吃成感性回避）
 14.5 stats           整體數字/總筆數/漏斗量/連續天數 → 確定性查 overall_stats（R3：已移出 LLM 工具表，漏接再也吐不出 📊）
 15. clock             現在幾點/今天幾號/我多久沒寫
 16. convo_time        我睡多久/我們多久沒聊/你剛說的多久前
 17. smalltalk         純附和/確認（是啊/對/嗯/沒錯）→ 純對話接話，**不開工具**（免得誤抓 overall_stats 吐 📊 報表）
 18. fact_or_chat      其餘 → function-calling（事實由 datatools 算）/ 純聊天

加新意向的守則：在這條優先序裡插一格（想清楚它該贏過誰、被誰贏），別再去 handle_message 插 regex。
"""

from dataclasses import dataclass

from . import greeting, plasticity, selfref, selfstate


@dataclass
class Intent:
    kind: str            # 見模組頂的優先序表
    topic: str = None    # 解析出的指涉物（目前：self_revisit_why 綁定的「自發繞回舊線」）


def resolve(text, ref, engrams=None, cfg=None):
    """text（使用者這句、決定「要幹嘛」）＋ ref（已解析好「指什麼」）→ 單一 Intent。
    純判定、無副作用。先跑既有優先序得「自然路由」；🧬 Phase 3：若自然路由是泛用 fallback（fact_or_chat）、
    且這句命中**夠強的已學路由更正**（被糾正過 ≥2 次的句式）→ 改用學到的正確 route。**只救 fallback**＝
    絕不覆蓋任何明確意向（不會劫持 self_identity 等），是安全的「學起來、別再掉進泛用桶」。
    cfg（可選）只透傳給 self_aspect 的 state 觸發精準判定；不傳＝逐位元同現狀。"""
    natural = _resolve_natural(text, ref, cfg)
    if natural.kind == "fact_or_chat" and engrams:
        learned = plasticity.recall_route(engrams, text)
        if learned:
            return Intent(learned)
    return natural


def _resolve_natural(text, ref, cfg=None):
    aspect = selfref.self_aspect(text, cfg)         # change|identity|experience|state|None（§0.7 自我指涉；cfg 控 state 精準）
    if (selfstate.is_association_method_question(text)
            and not selfstate.is_scheduled_promise_request(text)):
        return Intent("self_mechanism")

    # 🤝 §0.92 計時未來承諾**優先於**自我內容路由（self_change/self_identity）：「20分鐘後告訴我你有什麼不一樣」是排程承諾
    # （到點做 X），別被 X 的內容（你有什麼不一樣＝change / 你是誰＝identity）當成「現在就答」而丟了時間與約定 → 沒入帳、
    # bot 只 LLM 空口答應。截圖三句根因＝self_aspect=='change' 在 intent 最前（此處）就 return，永不走到下方 scheduled_promise。
    # 只在**真的是計時未來承諾**時讓路（is_scheduled 已排除裸問、且有時間閘）→ bare「你變了嗎」仍走 self_change。
    # 🔍 §0.92 審查（HIGH 修）：讓路時**直接**路由到 ledger 或 scheduled，**不落回中間 self_* 路由**——否則「告訴我你8點有什麼不一樣」
    # 被 is_own_reaction_question 的 `你…點…有什麼` 誤配成 self_reaction_query（「你按了哪個表情」＝答非所問、承諾又沒入帳）。
    # 過去質問（你忘了…嗎，ledger=True 而 is_scheduled 未必 False）仍先讓給 ledger（新約 vs 對帳分流）。
    if aspect in ("change", "identity"):
        _sched_over_self = (getattr(cfg, "scheduled_promise_enabled", True)
                            and getattr(cfg, "sched_over_selfcontent_enabled", True)
                            and selfstate.is_scheduled_promise_request(text))
        if _sched_over_self:
            if getattr(cfg, "promise_ledger_enabled", True) and selfstate.is_promise_ledger_question(text):
                return Intent("promise_ledger")   # 過去質問（你忘了…要告訴我你有什麼不一樣嗎）＝對帳、非新約
            return Intent("scheduled_promise")     # 計時新約（20分後告訴我你有什麼不一樣）＝直接排程、繞過中間 self_* 誤配
        return Intent("self_change" if aspect == "change" else "self_identity")
    if selfstate.is_continuity_question(text):          # 🌅 你還是原來的你嗎/斷線前在做什麼/睡多久 → 跨死亡連續性（睡→醒）
        return Intent("self_continuity")
    # 🎴 §0.93 審查（HIGH 修）：問「你看得到剛剛那張**貼圖**內容嗎」不是問 bot 點的 emoji reaction——`_OWN_REACT_RE` 的
    # `你…給…` 會被「傳**給**我…貼圖」誤配成 self_reaction_query（其 handler 硬編、不吃 mhint → §0.90/§0.93 誠實接地被丟棄、
    # 答非所問講「我沒點到什麼表情」）。貼圖感知問句讓路 → 落 fact_or_chat（吃 mhint、由接地誠實作答，同「為什麼喜歡這張」）。
    if selfstate.is_own_reaction_question(text) and not (
            getattr(cfg, "sticker_perceive_q_enabled", True) and selfstate.asks_can_perceive_sticker(text)):
        return Intent("self_reaction_query")

    if selfstate.is_revisit_why_question(text, ref.revisited):   # 綁到自發繞回的那條；**贏過**下面的自陳追問
        return Intent("self_revisit_why", topic=ref.revisited)

    if ref.followup_open and selfstate.is_selfstate_followup(text):
        return Intent("selfstate_followup")

    if getattr(cfg, "promise_ledger_enabled", True) and selfstate.is_promise_ledger_question(text):
        return Intent("promise_ledger")  # 🤝 整理承諾/你忘了/答應我的事做了嗎 → 據帳本 grounded 報帳。**排在 scheduled_promise 前**（『你忘了9點要道歉嗎』這類過去質問先被 ledger 攔、不被誤記成新承諾）；排在 data/fact_or_chat 前（『你的約定有哪些』含「哪些」不掉進 data 桶）。旗標 0 → 不攔、落回現狀 fact_or_chat＝逐位元同現狀

    if getattr(cfg, "scheduled_promise_enabled", True) and selfstate.is_scheduled_promise_request(text):
        return Intent("scheduled_promise")  # 🤝 「等一下八點跟我打招呼／十分鐘後提醒我」＝請在某時間 T 做某事（時間排程承諾，與下面 feeling promise 互斥＝is_scheduled 已先讓給 feeling）；handle_message 端再用 now/tz 解析 epoch、記下約定、自然答應，由生命迴圈 feel 相到點兌現
    if (getattr(cfg, "scheduled_promise_enabled", True) and getattr(cfg, "sched_leave_autoarm_enabled", True)
            and selfstate.is_leave_duration_statement(text)):
        return Intent("scheduled_promise")  # 🤝 §0.66 「我要離開約二十分鐘」＝暫離交代（無指向我動詞）——人類同伴聽到就會記時間，bot 也把計時真的記進帳本（handle_message 端 now+時距 落 target、到點叫你）；截圖根因：這種交代整包捕捉鏈都不收＝bot 只在嘴上倒數。旗標 0 → 不收＝同現狀
    if selfstate.is_feeling_promise_request(text):      # 排在狀態問句前（「感覺…要說」不被當成問現況）
        return Intent("promise")

    if selfstate.is_mechanism_question(text):           # ⚙️ 問內在怎麼運作/感覺怎麼算/機制 → 別被「感覺」抓成報現況
        return Intent("self_mechanism")

    if getattr(cfg, "spontaneity_enabled", False) and selfstate.is_share_association_question(text):
        return Intent("self_spontaneity")   # 🌱 你怎麼還沒分享聯想/主動出聲 → 問 bot 自己自發行為（聯想自發湧現、非隨選），走純對話別落 function-calling 列 📂（窄判定、排在 mechanism 後，不打破既有優先序）

    if selfstate.is_consciousness_question(text):       # 🧩 你有意識嗎/你算不算有意識 → 人工意識結構化認識論自評（可證偽、最強到「不被排除」、標餘量）；贏過 state/experience/reflect
        return Intent("self_consciousness")

    if selfstate.is_phenomenal_structure_question(text):  # 🌗 你裡面是怎麼經驗的/你的現象怎麼構成 → 右半 I↔(E×P) 三位互構自陳（act×建模質地×場、P 標 modeled、附餘量）
        return Intent("self_phenomenal")

    if aspect == "experience":
        return Intent("self_experience")
    if selfstate.is_goals_question(text):               # 🎯 你有什麼目標/在追什麼/想搞懂什麼 → 內發意圖（能動性）；排在 reflect 前（concrete 贏過 abstract）
        return Intent("self_goals")
    if selfstate.is_reflective_self_question(text):     # 反思式（想不想要/曾想過自己的感覺）→ 別被「感覺」抓成報現況
        return Intent("self_reflect")
    if selfstate.is_metacog_question(text):             # 🪞🔍 你確定嗎/會不會認錯自己/多了解自己 → 後設認知（信心/會看走眼/修正）
        return Intent("self_metacog")
    if selfstate.is_othermind_question(text):           # 🫂 你了解我嗎/你覺得我怎樣/我們關係 → 他心模型（把對方當有心智的人）
        return Intent("other_mind")

    if getattr(cfg, "self_appraisal_enabled", False) and selfstate.is_self_appraisal_question(text):
        return Intent("self_appraisal")     # 🪞📊 評斷我（使用者）的作息/勤惰/特質 → 拿我的節奏＋此刻時間做 grounded 判斷（贏過尾段 fact_or_chat，別讓 function-calling 誤抓 records_in_time_range）
    if selfstate.is_stream_question(text):              # ⏳ 你剛在想/發呆/思緒怎麼流 → 意識之流／綿延（排在 attention 前：含「在想」要先給流而非單一焦點）
        return Intent("self_stream")
    if selfstate.is_attention_question(text):           # 🌐 你在想什麼/什麼佔據你 → 全局工作空間的「當下意識前景」（別被「在想什麼」抓成 bodystate）
        return Intent("self_attention")
    if aspect == "state":
        return Intent("self_state")

    if getattr(cfg, "intent_confirm_enabled", False) and selfstate.is_elaborate_prior(text):
        return Intent("elaborate_prior")    # 🫧 展開/釐清「你說的那點」→ 接著前文講或反問確認意圖（贏過資料查詢/fact_or_chat）

    if greeting.detect(text):                           # 🕘 時間性問候（早安/午安/晚安）→ 帶絕對時間感回應（先於 farewell，讓「晚安」也走時間對照）
        return Intent("greeting")
    if selfstate.is_farewell(text):                     # 收尾/道別（懂了/晚安/先去忙）→ 優雅收場，別吐資料、別硬延
        return Intent("farewell")
    if selfstate.is_attachment_request(text):           # 非終局：挑不到檔，handle_message 會落回 fact_or_chat
        return Intent("attachment")
    if selfstate.is_cost_question(text):                # 💸 目前花費/API 成本 → 確定性查 datatools.api_cost
        return Intent("cost")
    if selfstate.is_stats_question(text):               # 📊 整體數字/總筆數/漏斗量 → 確定性查 overall_stats（已移出 LLM 工具表）
        return Intent("stats")
    if selfstate.is_clock_question(text):
        return Intent("clock")
    if selfstate.is_convo_time_question(text) or \
            (getattr(cfg, "convo_clock_enabled", True) and selfstate.is_convo_clock_question(text)):
        return Intent("convo_time")     # ⏱ 含「對話的絕對鐘點（幾點幾分）」：別漏到 function-calling 誤抓 records_in_time_range
    if selfstate.is_backchannel(text):                  # 🗣️ 純附和/確認（是啊/對/嗯）→ 純對話接話，別開工具（免得誤抓 overall_stats 吐報表）
        return Intent("smalltalk")
    return Intent("fact_or_chat")
