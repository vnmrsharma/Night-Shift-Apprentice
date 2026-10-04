"""Public-web evidence refresh via BrightData Web Unlocker (extension: never a live-demo dependency).
Fetches a short list of public guidance pages once, caches readable text with URL and date, and offers passages per rule.
  BRIGHTDATA_API_KEY and BRIGHTDATA_UNLOCKER_ZONE in .env, then: python -m engine.evidence_refresh"""
import json, os, re, time
from html.parser import HTMLParser
from pathlib import Path

import httpx
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

DATA = Path(__file__).resolve().parent.parent / "data"
CACHE = DATA / "evidence_cache.json"
URLS = [
    "https://www.alz.org/help-support/caregiving/stages-behaviors/aggression-anger",
    "https://www.alz.org/help-support/caregiving/stages-behaviors/wandering",
    "https://www.alzheimers.org.uk/get-support/daily-living/aggressive-behaviour",
]


class _Text(HTMLParser):
    SKIP = {"script", "style", "nav", "footer", "header", "noscript", "svg", "form"}

    def __init__(self):
        super().__init__(); self.parts: list[str] = []; self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP: self._skip += 1

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip: self._skip -= 1

    def handle_data(self, data):
        if not self._skip and data.strip(): self.parts.append(data.strip())


def extract_text(html: str) -> str:
    p = _Text(); p.feed(html)
    return re.sub(r"\s+", " ", " ".join(p.parts))


def passages(text: str, size: int = 2) -> list[str]:
    sents = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text)
    return [" ".join(sents[i:i + size]) for i in range(0, len(sents), size) if 15 <= len(" ".join(sents[i:i + size]).split()) <= 80]


def fetch(url: str, zone: str, key: str, timeout: float = 45.0) -> str:
    r = httpx.post("https://api.brightdata.com/request", headers={"Authorization": f"Bearer {key}"}, timeout=timeout,
                   json={"zone": zone, "url": url, "format": "raw"})
    r.raise_for_status()
    return r.text


def refresh(urls=URLS) -> dict:
    key, zone = os.getenv("BRIGHTDATA_API_KEY", ""), os.getenv("BRIGHTDATA_UNLOCKER_ZONE", "")
    if not key or not zone:
        raise SystemExit("Set BRIGHTDATA_API_KEY and BRIGHTDATA_UNLOCKER_ZONE (a Web Unlocker zone created in the BrightData control panel).")
    out = {"fetched_at": time.strftime("%Y-%m-%d"), "provider": "BrightData Web Unlocker", "pages": []}
    for u in urls:
        try:
            t = extract_text(fetch(u, zone, key)); out["pages"].append({"url": u, "ok": True, "passages": passages(t)[:80]})
        except Exception as e:                      # record the failure; never invent content
            out["pages"].append({"url": u, "ok": False, "error": str(e)[:120], "passages": []})
    CACHE.write_text(json.dumps(out, indent=1)); return out


def public_context(rule: dict, k: int = 1, min_score: float = 0.12, cache: Path = CACHE) -> list[dict]:
    """Best cached public passages for a rule (empty when the cache is absent)."""
    try:
        d = json.loads(cache.read_text())
    except Exception:
        return []
    items = [(p["url"], s) for p in d["pages"] if p["ok"] for s in p["passages"]]
    if not items:
        return []
    v = TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True, stop_words="english").fit([s for _, s in items])
    q = rule.get("literature_query") or rule["title"]
    sims = np.asarray((v.transform([s for _, s in items]) @ v.transform([q]).T).todense()).ravel()
    return [{"url": items[i][0], "excerpt": " ".join(items[i][1].split()[:38]), "fetched_at": d["fetched_at"], "provider": d["provider"], "score": round(float(sims[i]), 2)}
            for i in np.argsort(-sims)[:k] if sims[i] >= min_score]


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    print(json.dumps({"pages": [{k: p[k] for k in ("url", "ok")} | {"passages": len(p["passages"])} for p in refresh()["pages"]]}, indent=1))
