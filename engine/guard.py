"""Inspectable guardrail predicates evaluated over sandbox form fields (no black box)."""
from typing import Any


def _get(form: dict, field: str) -> Any:
    v = form.get(field)
    return v if v is not None else ("" if field != "checks" else [])


def eval_clause(form: dict, c: dict) -> dict:
    obs = _get(form, c["field"])
    op, val = c["op"], c["value"]
    if op == "eq": ok = obs == val
    elif op == "neq": ok = obs != val
    elif op == "in": ok = obs in val
    elif op == "not_in": ok = obs not in val
    elif op == "contains": ok = val in obs
    elif op == "not_contains": ok = val not in obs
    elif op == "gte": ok = float(obs or 0) >= float(val)
    elif op == "lt": ok = float(obs or 0) < float(val)
    else: raise ValueError(op)
    return {"field": c["field"], "op": op, "expected": val, "observed": obs, "met": bool(ok)}


def evaluate(rule: dict, form: dict) -> dict:
    p = rule["predicate"]
    trace = [eval_clause(form, c) for c in p["all"]]
    fired = all(t["met"] for t in trace)
    return {"rule_id": rule["id"], "title": rule["title"], "severity": p["severity"], "message": p["message"],
            "fired": fired, "trace": trace, "text": render(p)}


def render(p: dict) -> str:
    sym = {"eq": "==", "neq": "!=", "in": "in", "not_in": "not in", "contains": "contains",
           "not_contains": "does not contain", "gte": ">=", "lt": "<"}
    return " AND ".join(f"{c['field']} {sym[c['op']]} {c['value']!r}" for c in p["all"])


def check_form(rules: list[dict], form: dict, min_state: tuple = ("expert_stated", "confirmed", "hypothesized")) -> list[dict]:
    """All fired guardrails for a form. Rules without live support still fire (seeded from transcripts)."""
    return [r for r in (evaluate(x, form) for x in rules if not x.get("rejected")) if r["fired"]]   # a rule the expert rejected never fires
