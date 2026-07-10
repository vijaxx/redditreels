#!/usr/bin/env python3
"""Anthropic-compatible shim so the pipeline can run on a free LLM provider
instead of a paid Claude key.

    from anthropic import Anthropic   ->   from llm import Anthropic

Mimics client.messages.create(...)/client.messages.stream(...) closely enough
that call sites don't change. Picks the first key it finds in credentials.json
-- gemini_api_key (Gemini 2.0 Flash), then groq_api_key (Llama 3.3 70B), then
falls back to a real Anthropic key if one's configured. Pure stdlib."""
import json, pathlib, urllib.request, urllib.error, time

CREDS = pathlib.Path.home() / "RedditReels/config/credentials.json"


def _cfg():
    try:
        return json.loads(CREDS.read_text())
    except Exception:
        return {}


OLLAMA_URL = "http://127.0.0.1:11434"
OLLAMA_MODEL = "qwen2.5:3b"


def _ollama_up():
    try:
        urllib.request.urlopen(f"{OLLAMA_URL}/api/version", timeout=3).read()
        return True
    except Exception:
        return False


def _provider():
    c = _cfg()
    # PRIORITY (2026-06-13): prefer a FREE CLOUD key when present — Groq llama-3.3-70b /
    # Gemini are FAR better than local qwen2.5:3b AND run off-device, so they OFFLOAD the
    # 8GB Mac (less RAM/heat — helps Mac health too). Fall back to free local Ollama if no
    # cloud key, then paid anthropic only as an absolute last resort.
    if c.get("groq_api_key"):
        return ("groq", c["groq_api_key"])
    if c.get("gemini_api_key"):
        return ("gemini", c["gemini_api_key"])
    if _ollama_up():
        return ("ollama", None)
    if c.get("anthropic_api_key"):
        return ("anthropic", c["anthropic_api_key"])
    return (None, None)


# ── provider calls ───────────────────────────────────────────────
def _gemini(system, user, max_tokens, key):
    # 2026-06-30: gemini-2.0-flash returns 429/no-free-quota on current free-tier keys;
    # gemini-2.5-flash has free quota and works (verified). Override via cfg if ever needed.
    model = _cfg().get("gemini_model", "gemini-2.5-flash")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
    body = {
        "contents": [{"parts": [{"text": user}]}],
        "generationConfig": {
            "maxOutputTokens": max_tokens,
            "temperature": 1.0,
            # 2.5-flash is a 'thinking' model — without this it can spend the whole token
            # budget thinking and return a candidate with NO text parts. Budget 0 = no
            # thinking, so all output tokens become actual text.
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                  headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        d = json.loads(r.read())
    # robust extraction — an empty/over-budget candidate may lack 'parts'
    cands = d.get("candidates") or []
    parts = (cands[0].get("content") or {}).get("parts") if cands else None
    texts = [p.get("text", "") for p in (parts or []) if p.get("text")]
    if texts:
        return "".join(texts)
    fr = cands[0].get("finishReason") if cands else "no-candidates"
    raise RuntimeError(f"gemini: empty response (finishReason={fr})")


def _groq(system, user, max_tokens, key):
    url = "https://api.groq.com/openai/v1/chat/completions"
    msgs = []
    if system: msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": user})
    body = {"model": "llama-3.3-70b-versatile", "messages": msgs,
            "max_tokens": min(max_tokens, 8000), "temperature": 1.0}
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                  headers={"Content-Type": "application/json",
                                           "Authorization": f"Bearer {key}",
                                           # 2026-06-13: Groq's WAF 403s the default
                                           # Python-urllib UA — send a normal one.
                                           "User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        d = json.loads(r.read())
    return d["choices"][0]["message"]["content"]


def _anthropic(system, user, max_tokens, key, model):
    import anthropic
    client = anthropic.Anthropic(api_key=key)
    kw = {"model": model, "max_tokens": max_tokens,
          "messages": [{"role": "user", "content": user}]}
    if system: kw["system"] = system
    msg = client.messages.create(**kw)
    return msg.content[0].text


def _ollama(system, user, max_tokens, _key=None):
    body = {"model": OLLAMA_MODEL, "prompt": user, "stream": False,
            "options": {"num_predict": max_tokens, "temperature": 1.0}}
    if system:
        body["system"] = system
    req = urllib.request.Request(f"{OLLAMA_URL}/api/generate",
                                  data=json.dumps(body).encode(),
                                  headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        d = json.loads(r.read())
    return d.get("response", "")


def complete(system: str, user: str, max_tokens: int = 1500, model: str = None) -> str:
    """Unified free-first completion with patient backoff + FREE-provider fallback.

    2026-06-22 (token-cost guard): Groq's free-tier 429 is a PER-MINUTE rate window, not a
    daily quota at 4 fires/day — so we ride it out with escalating backoff (~20+30+45+60+90s
    ≈ 4 min of patience) instead of failing the fire after only 60s. If the primary FREE
    provider stays rate-limited, we fall through to ANOTHER FREE provider whose key exists
    (e.g. a free gemini_api_key). We deliberately NEVER auto-fall back to paid 'anthropic'
    here — a Groq rate-limit must not silently burn paid Claude tokens. Add a free Gemini
    key (https://aistudio.google.com/apikey) to config/credentials.json to give Groq a
    zero-cost safety net.
    """
    prov, key = _provider()
    if not prov:
        raise RuntimeError("No LLM available. Start Ollama (open -a Ollama) or add a free "
                           "gemini_api_key/groq_api_key to credentials.json.")

    # Build a FREE-only fallback chain: primary first, then any *other* free provider with a
    # key. 'anthropic' is excluded on purpose (paid → token-cost guard).
    c = _cfg()
    chain = [(prov, key)]
    if prov == "groq" and c.get("gemini_api_key"):
        chain.append(("gemini", c["gemini_api_key"]))
    elif prov == "gemini" and c.get("groq_api_key"):
        chain.append(("groq", c["groq_api_key"]))

    backoffs = [20, 30, 45, 60, 90]
    last_err = None
    for ci, (pv, pk) in enumerate(chain):
        has_fallback = ci < len(chain) - 1   # a free fallback provider waits after this one
        for attempt in range(len(backoffs) + 1):
            try:
                if pv == "ollama":   return _ollama(system, user, max_tokens)
                if pv == "gemini":   return _gemini(system, user, max_tokens, pk)
                if pv == "groq":     return _groq(system, user, max_tokens, pk)
                if pv == "anthropic":return _anthropic(system, user, max_tokens, pk,
                                                       model or "claude-haiku-4-5")
            except Exception as e:
                last_err = e
                _m = str(e)
                _is429 = ("429" in _m) or ("Too Many Requests" in _m) or ("rate limit" in _m.lower())
                # 2026-06-30: if a free fallback (e.g. Gemini) is available, switch to it
                # IMMEDIATELY on a 429 instead of burning the ~4-min backoff on the
                # rate-limited primary — that backoff was the whole cause of the slow fires.
                if _is429 and has_fallback:
                    break
                if attempt >= len(backoffs):
                    break  # this provider is exhausted → try next FREE provider in chain
                time.sleep(backoffs[attempt] if _is429 else 3)
    raise last_err or RuntimeError("LLM completion failed")


# ── Anthropic-compatible shim ───────────────────────────────────
# Lets existing code do `from llm import Anthropic` with zero other changes.
class _TextBlock:
    def __init__(self, text): self.text = text


class _Resp:
    def __init__(self, text): self.content = [_TextBlock(text)]


class _StreamCtx:
    """Mimics client.messages.stream(...) context manager with .text_stream."""
    def __init__(self, text): self._text = text
    def __enter__(self): return self
    def __exit__(self, *a): return False
    @property
    def text_stream(self):
        # yield in chunks to mimic streaming
        for i in range(0, len(self._text), 400):
            yield self._text[i:i+400]
    def get_final_message(self): return _Resp(self._text)


class _Messages:
    def create(self, model=None, max_tokens=1500, system=None, messages=None, **kw):
        user = ""
        if messages:
            user = "\n".join(m.get("content","") if isinstance(m.get("content"), str)
                             else "" for m in messages)
        return _Resp(complete(system or "", user, max_tokens, model))

    def stream(self, model=None, max_tokens=1500, system=None, messages=None, **kw):
        user = ""
        if messages:
            user = "\n".join(m.get("content","") if isinstance(m.get("content"), str)
                             else "" for m in messages)
        return _StreamCtx(complete(system or "", user, max_tokens, model))


class Anthropic:
    """Drop-in replacement: from llm import Anthropic"""
    def __init__(self, api_key=None, **kw):
        self.messages = _Messages()


if __name__ == "__main__":
    prov, key = _provider()
    print(f"Active provider: {prov or 'NONE — add a free key'}")
    if prov and prov != "anthropic":
        try:
            print("Test:", complete("You are terse.", "Say 'working' and nothing else.", 20)[:50])
        except Exception as e:
            print(f"Test failed: {e}")
