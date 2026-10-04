"""Confidence as inspectable facets. No fake probability; the label follows explicit rules."""


def facets(rule: dict, live_support: int, teach_back: str, reviewed: str = "") -> dict:
    sessions = {s["session"] for s in rule["sources"] if s["kind"] != "composite"}   # a composite case is not an independent interview session
    kinds = {s["source_type"] for s in rule["sources"] if s["kind"] != "composite"}
    n = len(sessions) + (1 if live_support else 0)
    ev = "strong" if n >= 3 else "corroborated" if n == 2 else "single-source"
    cls = "expert statements" if kinds <= {"Expert statement"} else "expert statements + corrections"
    if live_support:
        cls += " + live capture"
    states = [rule["slots"][s]["state"] if not isinstance(rule["slots"][s], list)
              else (rule["slots"][s][0]["state"] if rule["slots"][s] else "missing") for s in rule["slots"]]
    label = "low"
    if live_support or len(sessions) >= 2:
        label = "medium"
    if live_support and teach_back == "confirmed" and len(sessions) >= 1:
        label = "high"
    if reviewed == "confirmed":          # a person with expertise read the card and confirmed it
        label = "high"
        cls += " + expert review"
    if reviewed == "rejected":
        label = "low"
    if rule.get("conflicted"):
        label = "low"
    return {"evidence_strength": ev, "evidence_count": n, "sessions": sorted(sessions), "source_class": cls,
            "teach_back": teach_back, "reviewed": reviewed or "not reviewed", "slot_states": states, "label": label,
            "caution": rule.get("caution", "")}
