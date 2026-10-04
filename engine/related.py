"""Related expert knowledge: retrieve units from the WHOLE corpus that are similar to a rule but not already cited by it.
Retrieved automatically (TF-IDF), never curated or confirmed, so the UI labels them as such."""
from functools import lru_cache
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

from .ingest import load_units


@lru_cache(maxsize=1)
def _index():
    units = [u for u in load_units() if u.gold_eligible and (u.answer or u.branches)]
    docs = [f"{u.subtopic}. {u.answer} {' '.join(u.branches)}" for u in units]
    v = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), sublinear_tf=True).fit(docs)
    return units, v, v.transform(docs)


def related(rule: dict, k: int = 3, min_score: float = 0.12) -> list[dict]:
    try:
        units, v, M = _index()
    except FileNotFoundError:          # corpus not present on this machine
        return []
    cited = {s["unit_id"] for s in rule["sources"]}
    q = " ".join([rule["title"], rule["slots"]["context"]["text"], rule["slots"]["action"]["text"], rule["slots"]["rationale"]["text"]])
    sims = np.asarray((M @ v.transform([q]).T).todense()).ravel()
    out = []
    for i in np.argsort(-sims):
        u = units[i]
        if u.unit_id in cited or sims[i] < min_score or len(out) == k:
            continue
        out.append({"unit_id": u.unit_id, "session": u.session, "subtopic": u.subtopic, "source_type": u.source_type, "score": round(float(sims[i]), 2),
                    "branches": u.branches[:2], "caveat": u.caveat})
    return out
