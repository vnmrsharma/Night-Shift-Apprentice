"""The learned map: cards the apprentice extracted from ALL interview units (engine.learner), plus human review.
An accepted card with a validated predicate becomes a WARN-level rule in Teach (never a blocker, never asked about in Capture)."""
import json, os
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"


def learned_path() -> Path:
    return Path(os.getenv("APPRENTICE_LEARNED_FILE", DATA / "learned_map.json"))


def load() -> dict:
    try:
        return json.loads(learned_path().read_text())
    except Exception:
        return {"cards": [], "agenda": []}


def card(card_id: str) -> dict | None:
    return next((c for c in load()["cards"] if c["id"] == card_id), None)


def to_rule(c: dict) -> dict | None:
    """Convert a reviewed card into an executable warn rule, or None if it has no validated predicate."""
    p = c.get("proposed_predicate")
    if not p:
        return None
    src = [{"unit_id": e["unit_id"], "session": e["session"], "turn_ids": [e["turn_id"]], "kind": "verbatim", "quote_turn": e["turn_id"], "quote_span": e["span"],
            "source_type": (c.get("source_types") or ["Expert statement"])[0]} for e in c["evidence"]]
    if not src:
        src = [{"unit_id": c["units"][0], "session": c["sessions"][0], "turn_ids": [], "kind": "summary", "quote_turn": None, "quote_span": c["action"] or c["title"],
                "source_type": (c.get("source_types") or ["Expert statement"])[0]}]
    item = lambda t: {"text": t, "state": "hypothesized"}
    return {"id": c["id"], "title": c["title"], "risk": 3 if c["safety_critical"] else 2, "learned": True,
            "triggers": sorted({cl["field"] for cl in p["all"]}),
            "slots": {"context": item(c["context"]), "action": item(c["action"]), "rationale": item(c["rationale"]),
                      "exceptions": [item(t) for t in c["exceptions"]], "guardrails": [item(t) for t in c["guardrails"]],
                      "escalation": {"text": "; ".join(c["escalation"]), "state": "hypothesized" if c["escalation"] else "missing"}},
            "predicate": {"severity": "warn", "message": f"Learned from the interviews: {c['title']}", "all": p["all"]},
            "sources": src, "caution": c.get("caveat", "")}
