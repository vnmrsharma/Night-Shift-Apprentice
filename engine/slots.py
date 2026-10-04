"""Slot-level uncertainty, risk-weighted question selection, Socratic probes and batched gap scan.

Honest naming: this is risk-weighted expected uncertainty reduction over slot states, not calibrated
information gain. States: missing -> hypothesized (from transcripts) -> expert_stated (live) -> confirmed
(teach-back); conflicted when a live action contradicts a learned rule."""
from copy import deepcopy

STATE_U = {"missing": 1.0, "hypothesized": 0.7, "expert_stated": 0.3, "confirmed": 0.0, "conflicted": 0.9}
# expected uncertainty removed by one expert answer on a slot in this state
DELTA = {"missing": 0.7, "hypothesized": 0.4, "expert_stated": 0.3, "confirmed": 0.0, "conflicted": 0.6}
SLOT_W = {"guardrails": 1.2, "exceptions": 1.1, "escalation": 1.0, "rationale": 0.8, "action": 0.6, "context": 0.4}
SLOTS = ["context", "action", "rationale", "exceptions", "guardrails", "escalation"]

PROBES = {
    "rationale": ["Why that step?"],
    "exceptions": ["When would you NOT do that?", "What would make you change that decision?"],
    "guardrails": ["Is there a limit here? What would make you stop and not go ahead?"],
    "escalation": ["When would you hand this to someone else, and who?"],
    "action": ["What do you usually do next?"],
    "context": ["In which situations does this apply?"],
}


def eff(rule: dict, state: str) -> str:
    """Effective state: a rule the expert reviewed and confirmed counts its transcript hypotheses as confirmed."""
    return "confirmed" if rule.get("reviewed") == "confirmed" and state in ("hypothesized", "expert_stated") else state


def slot_state(rule: dict, slot: str) -> str:
    v = rule["slots"][slot]
    if isinstance(v, list):
        if not v:
            return "missing"
        states = [eff(rule, i["state"]) for i in v]
        if "conflicted" in states:
            return "conflicted"
        order = ["missing", "hypothesized", "expert_stated", "confirmed"]
        return max(states, key=order.index)  # slot counts as addressed once the expert has stated any item; items keep their own state
    return eff(rule, v["state"])


def uncertainty(rule: dict) -> float:
    return round(sum(STATE_U[slot_state(rule, s)] * SLOT_W[s] for s in SLOTS) / sum(SLOT_W.values()), 3)


def rung(rule: dict, slot: str, asked: list[dict]) -> int:
    """How many probes on this (rule, slot) already asked: climbs the Socratic ladder."""
    return sum(1 for a in asked if a["rule_id"] == rule["id"] and a["slot"] == slot)


def watches(rule: dict, field: str | None, value) -> bool:
    """True when what the expert just did is something this rule is about: a value in its predicate or in its `watch` list."""
    vals = value if isinstance(value, list) else [value]
    for c in rule["predicate"]["all"]:
        if c["field"] == field and any(v in (c["value"] if isinstance(c["value"], list) else [c["value"]]) for v in vals):
            return True
    return any(v in rule.get("watch", {}).get(field, []) for v in vals)


def candidates(rules: list[dict], asked: list[dict], event_field: str | None = None, touched: set | None = None,
               guardrail_asked: bool = False, incident: str | None = None, event_value=None) -> list[dict]:
    out = []
    for r in rules:
        if r.get("learned"):          # learned cards are reviewed in the Map and warn in Teach; they never add live questions
            continue
        if incident and r.get("applies_to") and incident not in r["applies_to"]:
            continue                  # e.g. the refusal rules do not compete for a question about a wandering incident
        rel = None
        if event_field is not None:
            rel = 1.0 if event_field in r["triggers"] else None   # live: must be about the visible event
            if rel and event_value is not None and watches(r, event_field, event_value):
                rel = 1.8                                          # prefer the rule that is actually about what the expert just did
        else:
            rel = 0.8 if (touched is None or r["id"] in touched) else 0.5  # debrief
        if rel is None:
            continue
        for s in SLOTS:
            st = slot_state(r, s)
            if STATE_U[st] <= 0.0 or st == "confirmed":
                continue
            n = rung(r, s, asked)
            if n >= len(PROBES[s]):      # ladder exhausted for this slot
                continue
            boost = 1.5 if (s in ("guardrails", "escalation") and not guardrail_asked) else 1.0
            score = r["risk"] * SLOT_W[s] * rel * DELTA[st] * boost * (0.85 ** n)
            out.append({"rule_id": r["id"], "slot": s, "rung": n, "score": round(score, 3),
                        "state": st, "risk": r["risk"], "title": r["title"]})
    return sorted(out, key=lambda c: -c["score"])


def phrase(rule: dict, slot: str, n: int, ev_text: str | None) -> str:
    """Every live question names the visible screen event; debrief questions name the rule instead."""
    probe = PROBES[slot][n]
    lead = f"I saw you {ev_text}. " if ev_text else f"About '{rule['title'].lower()}': "
    return lead + probe


def gap_scan(rules: list[dict], asked: list[dict], touched: set | None = None, k: int = 3, incident: str | None = None) -> list[dict]:
    """One batched pass over the whole map: ranked top-k (rule, slot) gaps not yet asked."""
    cs = candidates(rules, asked, None, touched, incident=incident)
    seen, out = set(), []
    for c in cs:
        key = (c["rule_id"], c["slot"])
        if key in seen:
            continue
        seen.add(key); out.append(c)
        if len(out) == k:
            break
    return out


def apply_state(rule: dict, slot: str, new_state: str):
    v = rule["slots"][slot]
    for it in (v if isinstance(v, list) else [v]):
        it["state"] = new_state


def snapshot_states(rules: list[dict]) -> dict:
    return {r["id"]: {s: slot_state(r, s) for s in SLOTS} for r in rules}
