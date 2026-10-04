"""Literature context from the supplied Alzheimer's Association Dementia Care Practice Recommendations (2018).
Literature supports context and safety. It is never an expert quote and never proof that a rule is effective (CBT safeguard 3:
evidence does not transfer between conditions and populations), so every excerpt is labelled and short."""
import re
import subprocess
from functools import lru_cache
from pathlib import Path
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

SOURCE = Path(__file__).resolve().parent.parent / "data" / "source" / "alzheimers-dementia-care-practice-recommendations.pdf"
CITATION = "Alzheimer's Association Dementia Care Practice Recommendations (The Gerontologist, 2018)"
STOP = {"dementia", "persons", "person", "care", "living", "with", "should", "recommend", "recommendations", "evidence", "studies", "study"}


def _pages(path: Path) -> list[str] | None:
    """Page text, or None when pdftotext is missing or the PDF cannot be read. Guideline excerpts are optional context."""
    try:
        proc = subprocess.run(["pdftotext", str(path), "-"], capture_output=True, text=True, timeout=120)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.split("\f")


def _is_reference_page(t: str) -> bool:
    return len(re.findall(r"doi:|https?://|\bpp?\. \d|\(\d{4}\)\.", t)) > 12


@lru_cache(maxsize=1)
def _index():
    if not SOURCE.exists():
        return None
    pages = _pages(SOURCE)
    if not pages:
        return None
    chunks = []
    for n, raw in enumerate(pages, start=1):
        t = re.sub(r"\s+", " ", raw).strip()
        if n < 6 or len(t) < 400 or _is_reference_page(t):
            continue
        sents = re.split(r"(?<=[.!?])\s+(?=[A-Z])", t)
        for i in range(0, len(sents) - 1, 2):
            c = " ".join(sents[i:i + 2])
            if 18 <= len(c.split()) <= 90 and not re.search(r"\bet al\.|\(\d{4}\)", c[:60]):
                chunks.append({"page": n, "text": c})
    if not chunks:
        return None
    v = TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True, stop_words=list(STOP) + ["the", "of", "and", "to", "in", "a", "is", "for", "on", "that", "are", "as", "be", "by", "or", "with", "it", "an"]).fit([c["text"] for c in chunks])
    return chunks, v, v.transform([c["text"] for c in chunks])


def _excerpt(text: str, limit: int = 38) -> str:
    w = text.split()
    return " ".join(w[:limit]) + (" ..." if len(w) > limit else "")


def context_for(rule: dict, k: int = 2, min_score: float = 0.12) -> list[dict]:
    idx = _index()
    if idx is None:
        return []
    chunks, v, M = idx
    q = rule.get("literature_query") or " ".join([rule["title"], rule["slots"]["action"]["text"]])   # hand-written topic query per curated rule
    sims = np.asarray((M @ v.transform([q]).T).todense()).ravel()
    out, seen_pages, seen_text = [], set(), set()
    for i in np.argsort(-sims):
        if sims[i] < min_score or len(out) == k:
            break
        if chunks[i]["page"] in seen_pages or chunks[i]["text"][:80] in seen_text:   # one passage per page; the guideline repeats its summaries
            continue
        seen_pages.add(chunks[i]["page"]); seen_text.add(chunks[i]["text"][:80])
        out.append({"source": CITATION, "page": chunks[i]["page"], "excerpt": _excerpt(chunks[i]["text"]), "score": round(float(sims[i]), 2)})
    return out


SAFEGUARDS = {
    "delivery_not_effectiveness": "Delivering this step correctly does not show it worked: response and outcome must be measured separately.",
    "evidence_does_not_transfer": "No efficacy evidence for this rule in this setting was supplied. Expert practice, not validated effectiveness.",
}


def cbt_check(rule: dict) -> dict:
    """The supplied CBT checklist as a rubric: formulation, targeted intervention, measurement, empirical validation."""
    s = rule["slots"]
    return {"formulation": bool(s["context"]["text"] and s["rationale"]["text"]), "targeted_intervention": bool(s["action"]["text"]),
            "measurement": False, "empirical_validation": False, "safeguards": SAFEGUARDS}
