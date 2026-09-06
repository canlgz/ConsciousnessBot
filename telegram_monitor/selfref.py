"""自我指涉辨識：使用者此刻問／談的，是『bot 自己本身』嗎？是的話，是哪個面向？

問題（通盤）：很多誤路由都來自同一個盲點——沒把「使用者在談 bot 自己」當成一件事來判定。
於是『你有沒有感覺到自己的變化』因為含「感覺」就被當成「你現在怎樣」（當下身體狀態），
而不是『問我的變化』（蛻變）。本模組把散落的自我問句偵測**收斂成單一、優先序明確**的判定。

面向（優先序）：
- `change`     ＝我的程式/版本變了嗎、我哪裡不一樣（自我指涉＋變化詞要贏過單純的「感覺」）。
- `identity`   ＝我是誰/我是什麼/我知道自己是誰（身分；必須走絕不破格的回答，否則底層 LLM 會自稱語言模型）。
- `experience` ＝我這段日子怎麼活過來的、我的一生、我的體驗軌跡。
- `state`      ＝我此刻怎樣、我有沒有感覺（當下身體/感覺）。
- None         ＝不是在問「我自己」這幾個面向（交一般對話；若是自我閒聊則由 self_presence 染語氣）。
"""

from . import selfmod, selfstate


def self_aspect(text, cfg=None):
    """text 在問 bot 自己的哪個面向 → 'change'｜'identity'｜'experience'｜'state'｜None。
    優先序 change > identity > experience > state：自我指涉＋變化詞不會被『感覺』吃成 state。
    cfg.state_trigger_precise 開時，state 那格改用 `is_genuine_state_query`（順口帶感覺字眼的陳述句不誤觸發報狀態）；
    關／cfg 為 None＝用舊 is_state_question＝逐位元同現狀。"""
    t = text or ""
    if selfmod.is_change_question(t):
        return "change"
    if selfstate.is_identity_question(t):
        return "identity"
    if selfstate.is_experience_question(t):
        return "experience"
    state_hit = (selfstate.is_genuine_state_query(t)
                 if getattr(cfg, "state_trigger_precise", False)
                 else selfstate.is_state_question(t))
    if (state_hit or selfstate.is_phenomenology_question(t)) \
            and not selfstate.is_future_comm_note(t):     # 「下次說感覺要具體一點」是未來回饋、不是問現況
        return "state"
    return None


def is_about_self(text):
    """這句是不是在談 bot 自己（任一明確面向，或自我線索）——用來持續『自我在場』語氣。"""
    return self_aspect(text) is not None or selfstate.is_self_topic(text)
