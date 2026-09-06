"""極簡 Gemini generateContent client（沿用 LINE bot 的 GEMINI_API_KEY）。

只用 REST、不裝 SDK。預設模型對齊 src/Config.gs 的 MODELS.GENERATION（gemini-2.5-flash）。
"""

import base64

import requests

_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

_HARD_ENDERS = "。！？!?"            # 硬句尾：一定是句界
_ELLIPSIS = "…"                     # 省略號：只在「句末（後接空白/結束）」算句界；句中停頓不算
_SENT_ENDERS = _HARD_ENDERS + _ELLIPSIS   # 截斷時退回到「真句界」為止（永不吐半句）
_CLOSERS = "」』）)】〕]\"’”"           # 收尾引號/括號（併入句尾）
# 句界判定與 persona._boundary_end 同義（兩處各自保有以維持 gemini 低層獨立、不反向依賴 persona）。


class GeminiError(Exception):
    pass


def _trim_trailing_partial(text):
    """輸出被 maxOutputTokens 截在半句時 → 退回最後一個**完整句**句尾（含收尾引號/括號），永不吐半句。
    本函式只在**已知被截斷**時呼叫，故對省略號從嚴：句中停頓（『可能跟你…的狀況』被截）**或**結尾剛好停在
    省略號（『可能跟你…』＝截斷點、非刻意收尾）都**不算句界、會一起退掉**、不留斷尾——只有硬句尾（。！？）
    或「省略號後接空白（證明它是句末、模型又起了新句）」才算。整段找不到完整句就原樣回（極少見單一長 run-on）。"""
    last, i, n = 0, 0, len(text)
    while i < n:
        if text[i] in _SENT_ENDERS:
            j = i
            while j < n and text[j] in _SENT_ENDERS:        # 收整段連續句尾標點（…／。／！？）
                j += 1
            run_has_hard = any(c in _HARD_ENDERS for c in text[i:j])
            while j < n and text[j] in _CLOSERS:            # 併收尾引號/括號
                j += 1
            if run_has_hard or (j < n and text[j].isspace()):  # 硬句尾、或省略號後接空白＝真句界；結尾省略號＝截斷點不算
                last = j
            i = j
        else:
            i += 1
    return text[:last].rstrip() if last else text


def _gen_config(model, temperature, max_tokens):
    cfg = {"temperature": temperature, "maxOutputTokens": max_tokens}
    # gemini-2.5 系列是「思考型」模型：預設會先花一段「內部思考」再輸出，而思考會**吃掉
    # 輸出額度**，導致可見回覆被截在半句。對話/反思不需要長鏈推理 → 關閉思考
    # （thinkingBudget=0；也更快更省）。對齊 LINE bot 依模型世代挑思考參數的做法。
    if "2.5" in (model or ""):
        cfg["thinkingConfig"] = {"thinkingBudget": 0}
    return cfg


def _extract_text(data):
    cands = data.get("candidates") or []
    if not cands:
        raise GeminiError("no candidates: " + str(data)[:200])
    cand = cands[0]
    parts = (cand.get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts).strip()
    if not text:
        raise GeminiError(f"empty text (finishReason={cand.get('finishReason')}): " + str(data)[:200])
    if (cand.get("finishReason") or "").upper() == "MAX_TOKENS":   # 額度用盡被截在半句 → 退回完整句尾
        text = _trim_trailing_partial(text) or text
    return text


def generate_chat(api_key, model, system_instruction, contents,
                  temperature=0.85, max_tokens=900, timeout=40, on_usage=None):
    """contents = [{'role':'user'|'model','parts':[{'text':...}]}, ...]。回傳純文字。

    on_usage(prompt_tokens, output_tokens, model)：若提供，會在回應後回報 token 用量（給成本守門）。
    """
    from .dialogue_style import finalize
    system_instruction = finalize(system_instruction)
    url = _ENDPOINT.format(model=model) + f"?key={api_key}"
    body = {
        "system_instruction": {"parts": [{"text": system_instruction}]},
        "contents": contents,
        "generationConfig": _gen_config(model, temperature, max_tokens),
    }
    try:
        resp = requests.post(url, json=body, timeout=timeout)
    except requests.RequestException as e:
        raise GeminiError(f"network: {e}")
    if resp.status_code != 200:
        raise GeminiError(f"{resp.status_code} {resp.text[:300]}")
    data = resp.json()
    if on_usage:
        um = data.get("usageMetadata") or {}
        pt = um.get("promptTokenCount") or 0
        ct = um.get("candidatesTokenCount")
        if ct is None:
            ct = max(0, (um.get("totalTokenCount") or 0) - pt)
        try:
            on_usage(pt, ct, model)
        except Exception:
            pass
    return _extract_text(data)


def generate_with_tools(api_key, model, system_instruction, contents, tool_decls,
                        temperature=0.6, max_tokens=800, timeout=40, on_usage=None):
    """帶 function-calling。回傳 {'function_call': {name,args}|None, 'text': str|None}。

    模型若需要事實 → 回 functionCall（呼叫端執行確定性工具）；否則回一般文字（聊天）。
    """
    from .dialogue_style import finalize
    system_instruction = finalize(system_instruction)
    url = _ENDPOINT.format(model=model) + f"?key={api_key}"
    body = {
        "system_instruction": {"parts": [{"text": system_instruction}]},
        "contents": contents,
        "tools": [{"function_declarations": tool_decls}],
        "generationConfig": _gen_config(model, temperature, max_tokens),
    }
    try:
        resp = requests.post(url, json=body, timeout=timeout)
    except requests.RequestException as e:
        raise GeminiError(f"network: {e}")
    if resp.status_code != 200:
        raise GeminiError(f"{resp.status_code} {resp.text[:300]}")
    data = resp.json()
    if on_usage:
        um = data.get("usageMetadata") or {}
        pt = um.get("promptTokenCount") or 0
        ct = um.get("candidatesTokenCount")
        if ct is None:
            ct = max(0, (um.get("totalTokenCount") or 0) - pt)
        try:
            on_usage(pt, ct, model)
        except Exception:
            pass
    cands = data.get("candidates") or []
    if not cands:
        raise GeminiError("no candidates: " + str(data)[:200])
    parts = (cands[0].get("content") or {}).get("parts") or []
    for p in parts:
        fc = p.get("functionCall")
        if fc:
            return {"function_call": {"name": fc.get("name"), "args": fc.get("args") or {}}, "text": None}
    text = "".join(p.get("text", "") for p in parts).strip()
    return {"function_call": None, "text": text or None}


def generate(api_key, model, system_instruction, user_text,
             temperature=0.85, max_tokens=900, timeout=40, on_usage=None):
    """單輪：一段 user 文字。"""
    contents = [{"role": "user", "parts": [{"text": user_text}]}]
    return generate_chat(api_key, model, system_instruction, contents,
                         temperature=temperature, max_tokens=max_tokens,
                         timeout=timeout, on_usage=on_usage)


def generate_vision(api_key, model, system_instruction, prompt_text, image_bytes,
                    mime_type="image/webp", temperature=0.4, max_tokens=160,
                    timeout=40, on_usage=None):
    """單輪視覺：一段提示＋一張內嵌圖（inline base64）→ 純文字。給貼圖語意解讀用
    （模型看圖、回一句它畫了什麼／傳達什麼情緒）。gemini-2.5-flash 本身多模態，沿用同一 endpoint／key。
    inline_data 用 v1beta 接受的 snake_case 欄位（與本檔既有 system_instruction 寫法一致）。"""
    b64 = base64.b64encode(image_bytes or b"").decode("ascii")
    contents = [{"role": "user", "parts": [
        {"text": prompt_text},
        {"inline_data": {"mime_type": mime_type, "data": b64}},
    ]}]
    return generate_chat(api_key, model, system_instruction, contents,
                         temperature=temperature, max_tokens=max_tokens,
                         timeout=timeout, on_usage=on_usage)


def extract_grounding(data):
    """🌐 §1.95 從回應裡取 (文字, [(標題, 網址)], [模型實際搜過的字串])。**防禦性**：欄位缺失／型別不符／
    空陣列都不能炸——grounding 的回應形狀我們沒有實測過（使用者選擇不先做驗證呼叫），所以一律用 .get 走。
    **拿不到任何來源網址就回空 sources**——呼叫端會把「沒有來源」當成這次失敗（沒來源不准說「我查到」）。"""
    text = _extract_text(data)
    srcs, queries = [], []
    try:
        gm = ((data.get("candidates") or [{}])[0] or {}).get("groundingMetadata") or {}
        for ch in (gm.get("groundingChunks") or []):
            web = (ch or {}).get("web") or {}
            uri = (web.get("uri") or "").strip()
            if uri:
                srcs.append(((web.get("title") or "").strip() or uri, uri))
        queries = [q for q in (gm.get("webSearchQueries") or []) if isinstance(q, str) and q.strip()]
    except Exception:
        pass
    return text, srcs, queries


def generate_grounded(api_key, model, system_instruction, user_text, tool_field="google_search",
                      temperature=0.6, max_tokens=700, timeout=60, on_usage=None):
    """🌐 §1.95 帶 Google Search grounding 的單輪生成 → (文字, sources, queries)。

    ✅ **已實測**（2026-07-28，gemini-2.5-flash 真打 API，中性查詢）：
      `google_search` → 200，`groundingChunks` 4 筆、`webSearchQueries` 4 條（＝預設值就是對的）；
      `googleSearch` → 也 200（API 兩種寫法都收）；
      `google_search_retrieval` → 400「google_search_retrieval is not supported. Please use google_search tool instead.」
    來源網址是 **Google 的轉址連結**（`vertexaisearch…/grounding-api-redirect/…`，約 30 天後失效），
    站名只在 `web.title`（例：`cozyhousecoffee.com`）⇒ 呼叫端要把**站名與網址一起**給人看。
    - `tool_field` 仍保留可設定（由 config 的 WORLDLINE_TOOL_FIELD 給值），API 若哪天改名，看 /worldline
      印出的**原始錯誤字串**就知道要改哪個，不必改程式。
    - **刻意不套 `_gen_config`**：它對 2.5 系列硬塞 `thinkingConfig.thinkingBudget=0`，而那與 search 工具
      往返併用完全沒實測過；這裡只給 temperature/maxOutputTokens，把未知數降到最低。
    - 任何非 200 一律把 **resp.text 前 300 字原樣**包進 GeminiError ⇒ 對帳段看得到真正的錯誤訊息。"""
    url = _ENDPOINT.format(model=model) + f"?key={api_key}"
    body = {
        "system_instruction": {"parts": [{"text": system_instruction}]},
        "contents": [{"role": "user", "parts": [{"text": user_text}]}],
        "tools": [{tool_field: {}}],
        "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
    }
    try:
        resp = requests.post(url, json=body, timeout=timeout)
    except requests.RequestException as e:
        raise GeminiError(f"network: {e}")
    if resp.status_code != 200:
        raise GeminiError(f"{resp.status_code} {resp.text[:300]}")
    data = resp.json()
    if on_usage:
        um = data.get("usageMetadata") or {}
        pt = um.get("promptTokenCount") or 0
        ct = um.get("candidatesTokenCount")
        if ct is None:
            ct = max(0, (um.get("totalTokenCount") or 0) - pt)
        try:
            on_usage(pt, ct, model)
        except Exception:
            pass
    return extract_grounding(data)
