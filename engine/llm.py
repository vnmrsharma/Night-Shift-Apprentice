"""Thin Anthropic wrapper: usage ledger (credit tracking), cache, and graceful fallback."""
import json, os, threading, time, hashlib
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
load_dotenv(ROOT / ".env.local", override=True)
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)
LEDGER = DATA / "usage.json"
CACHE = DATA / "llm_cache.json"

FAST = "claude-haiku-4-5-20251001"
SMART = "claude-sonnet-5-5"
# Estimated USD per million tokens (input, output). Estimates only; check console.anthropic.com for real balance.
PRICE = {FAST: (1.0, 5.0), SMART: (3.0, 15.0)}


_LOCK = threading.Lock()


class LLMUnavailable(Exception):
    pass


def _load(p, default):
    try:
        return json.loads(p.read_text())
    except Exception:
        return default


def usage_summary():
    led = _load(LEDGER, {"calls": []})
    tot_in = sum(c["in"] for c in led["calls"]); tot_out = sum(c["out"] for c in led["calls"])
    return {"calls": len(led["calls"]), "input_tokens": tot_in, "output_tokens": tot_out,
            "est_usd": round(sum(c["usd"] for c in led["calls"]), 4), "credit_budget_usd": 25.0}


def complete(system: str, user: str, model: str = FAST, max_tokens: int = 800, cache: bool = True) -> str:
    key = hashlib.sha256(f"{model}|{system}|{user}".encode()).hexdigest()
    c = _load(CACHE, {})
    if cache and key in c:
        return c[key]
    if not os.getenv("ANTHROPIC_API_KEY") or os.getenv("APPRENTICE_OFFLINE") == "1":
        raise LLMUnavailable("no key or offline mode")
    import anthropic
    try:
        cl = anthropic.Anthropic(timeout=25.0, max_retries=1)
        r = cl.messages.create(model=model, max_tokens=max_tokens, system=system,
                               messages=[{"role": "user", "content": user}])
    except Exception as e:  # network, rate limit, auth
        raise LLMUnavailable(str(e))
    text = "".join(b.text for b in r.content if getattr(b, "type", "") == "text")
    pin, pout = PRICE.get(model, (3.0, 15.0))
    usd = r.usage.input_tokens * pin / 1e6 + r.usage.output_tokens * pout / 1e6
    with _LOCK:
        led = _load(LEDGER, {"calls": []})
        led["calls"].append({"t": time.time(), "model": model, "in": r.usage.input_tokens, "out": r.usage.output_tokens, "usd": usd})
        LEDGER.write_text(json.dumps(led))
        if cache:
            c = _load(CACHE, {})
            c[key] = text
            CACHE.write_text(json.dumps(c))
    return text


def complete_json(system: str, user: str, model: str = FAST, max_tokens: int = 800):
    t = complete(system + "\nReturn ONLY valid minified JSON, no prose, no code fences.", user, model, max_tokens)
    t = t.strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t[t.find("{"):]
    s, e = t.find("{"), t.rfind("}")
    return json.loads(t[s:e + 1])


def vision(data_url: str, prompt: str, model: str = FAST, max_tokens: int = 120) -> str:
    """One image + prompt. Not cached (frames are unique). Raises LLMUnavailable on any failure."""
    if not os.getenv("ANTHROPIC_API_KEY") or os.getenv("APPRENTICE_OFFLINE") == "1":
        raise LLMUnavailable("no key or offline mode")
    head, _, b64 = data_url.partition(",")
    media = head.split(";")[0].replace("data:", "") or "image/jpeg"
    import anthropic
    try:
        r = anthropic.Anthropic(timeout=25.0, max_retries=1).messages.create(
            model=model, max_tokens=max_tokens,
            messages=[{"role": "user", "content": [{"type": "image", "source": {"type": "base64", "media_type": media, "data": b64}},
                                                     {"type": "text", "text": prompt}]}])
    except Exception as e:
        raise LLMUnavailable(str(e))
    pin, pout = PRICE.get(model, (3.0, 15.0))
    with _LOCK:
        led = _load(LEDGER, {"calls": []})
        led["calls"].append({"t": time.time(), "model": model, "in": r.usage.input_tokens, "out": r.usage.output_tokens,
                             "usd": r.usage.input_tokens * pin / 1e6 + r.usage.output_tokens * pout / 1e6, "kind": "vision"})
        LEDGER.write_text(json.dumps(led))
    return "".join(b.text for b in r.content if getattr(b, "type", "") == "text").strip()
