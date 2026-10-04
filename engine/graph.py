"""The Care Graph: rules, their guardrails, hand-over routes, the form fields they test, the interview evidence behind them,
live screen moments, guideline context, and the cards learned from ALL interviews, with how they connect.
Pure read model over the session; every node and edge is derived from stored provenance, nothing is invented here."""
import re
from . import guard, learned, slots as S
from .confidence import facets
from .ingest import load_units
from .literature import cbt_check, context_for
from .evidence_refresh import public_context
from .related import related

ROUTES = {"nurse": "Nurse", "coordinating_physician": "Coordinating physician", "treating_doctor": "Treating doctor", "psychologist": "Psychologist", "team_meeting": "Team meeting"}
ROUTE_WORDS = [("treating doctor", "treating_doctor"), ("coordinating physician", "coordinating_physician"), ("physician", "coordinating_physician"), ("nurse", "nurse"),
               ("psychologist", "psychologist"), ("team meeting", "team_meeting"), ("staff meeting", "team_meeting")]
GENERIC = {("incident_type", "refusal_of_care")}      # shared by nearly every rule, so it says nothing about which rule a card re-derives


def _clause_keys(pred: dict) -> set:
    return {(c["field"], c["op"], str(c["value"])) for c in pred["all"]}


NEGATIVE = {"neq", "not_in", "not_contains"}


def _tests(pred: dict) -> set:
    """Informative tests of a predicate: (field, value, polarity). The incident type is compared separately, and numeric bounds keep their operator."""
    out = set()
    for c in pred["all"]:
        if c["field"] == "incident_type":
            continue
        for v in (c["value"] if isinstance(c["value"], list) else [c["value"]]):
            out.add((c["field"], str(v), c["op"] if c["op"] in ("gte", "lt") else c["op"] in NEGATIVE))
    return out


def _incidents(pred: dict) -> set:
    return {str(v) for c in pred["all"] if c["field"] == "incident_type" for v in (c["value"] if isinstance(c["value"], list) else [c["value"]])}


def rederives(card_pred: dict, rule: dict) -> bool:
    """A learned card re-derives a curated rule when it states the same informative test with the same polarity
    and its incident type, if it names one, is one the rule applies to."""
    inc, applies = _incidents(card_pred), rule.get("applies_to")
    if inc and applies and not inc & set(applies):
        return False
    return bool(_tests(card_pred) & _tests(rule["predicate"]))


def _short(t: str, n: int = 44) -> str:
    return t if len(t) <= n else t[: n - 1].rstrip() + "..."


def build(session) -> dict:
    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    def node(nid, typ, label, status="", detail=None, **extra):
        if nid not in nodes:
            nodes[nid] = {"id": nid, "type": typ, "label": label, "status": status, "detail": detail or {}, **extra}
        return nid

    def edge(a, b, typ, label=""):
        edges.append({"source": a, "target": b, "type": typ, "label": label})

    units = {u.unit_id: u for u in load_units()}
    reviews = session.reviews

    def unit_node(uid):
        if uid == "MICRO-CASE-WANDERING":      # the startup team's composite case, drawn as its own kind of evidence
            return node(f"unit:{uid}", "unit", "Composite case", detail={"unit": uid, "session": "COMPOSITE", "source_type": "Composite case (startup team, invented details)",
                                                                       "subtopic": "Afternoon wandering micro case: not an interview", "branches": []})
        u = units.get(uid)
        return node(f"unit:{uid}", "unit", f"{uid}", detail={"unit": uid, "session": u.session if u else "", "subtopic": u.subtopic if u else "", "source_type": u.source_type if u else "",
                                                         "branches": u.branches[:3] if u else []})

    def route_node(key):
        return node(f"route:{key}", "route", ROUTES[key])

    def field_node(name):
        return node(f"field:{name}", "field", name.replace("_", " "))

    for r in session.rules:
        live = session._live_moments(r)
        tb = session.teachback.get(r["id"], "none")
        if r.get("rejected"):
            status = "rejected"
        elif r.get("reviewed") == "confirmed" or tb in ("confirmed", "corrected"):
            status = "confirmed"
        elif live:
            status = "live"
        else:
            status = "hypothesis"
        fct = facets(r, len(live), tb, r.get("reviewed", ""))
        rid = node(f"rule:{r['id']}", "rule", r["title"], status, {
            "rule_id": r["id"], "title": r["title"], "risk": r["risk"], "severity": r["predicate"]["severity"], "predicate": guard.render(r["predicate"]),
            "context": r["slots"]["context"]["text"], "action": r["slots"]["action"].get("live_text") or r["slots"]["action"]["text"], "rationale": r["slots"]["rationale"]["text"],
            "escalation": r["slots"]["escalation"].get("live_text") or r["slots"]["escalation"]["text"], "caution": r.get("caution", ""),
            "reviewed": r.get("reviewed", ""), "review_note": r.get("review_note", ""), "confidence": fct["label"], "evidence_strength": fct["evidence_strength"],
            "cbt": cbt_check(r), "learned": bool(r.get("learned"))}, risk=r["risk"], learned=bool(r.get("learned")))

        for c in r["predicate"]["all"]:                                         # the form fields this rule tests
            f = field_node(c["field"])
            edge(rid, f, "tests", f"{c['op'].replace('_', ' ')} {c['value']}")
        for g in r["slots"]["guardrails"]:                                     # guardrails
            gid = node(f"guardrail:{r['id']}:{abs(hash(g['text'])) % 10**6}", "guardrail", _short(g["text"]), S.eff(r, g["state"]),
                       {"text": g["text"], "state": S.eff(r, g["state"]), "rule_id": r["id"]})
            edge(rid, gid, "guardrail")
        for e in r["slots"]["exceptions"]:
            if e.get("live") or e.get("review"):
                xid = node(f"exception:{r['id']}:{abs(hash(e['text'])) % 10**6}", "exception", _short(e["text"]), S.eff(r, e["state"]), {"text": e["text"], "state": e["state"], "rule_id": r["id"]})
                edge(rid, xid, "exception")
        text = (r["slots"]["escalation"].get("live_text") or r["slots"]["escalation"]["text"] or "").lower()   # hand-over routes
        chain = []
        for word, key in ROUTE_WORDS:
            if word in text and key not in chain:
                chain.append(key)
        for c in r["predicate"]["all"]:
            if c["field"] == "escalate_to":
                for v in (c["value"] if isinstance(c["value"], list) else [c["value"]]):
                    if v in ROUTES and v not in chain:
                        chain.append(v)
        for key in chain:
            edge(rid, route_node(key), "routes_to", "hands over to")
        if "->" in text:                                                         # explicit ordered route, for example nurse -> physician -> treating doctor
            seq = [k for part in text.split("->") for w, k in ROUTE_WORDS if w in part][:4]
            for a, b in zip(seq, seq[1:]):
                edge(route_node(a), route_node(b), "then", "then")
        for s in r["sources"]:                                                  # interview evidence
            uid = unit_node(s["unit_id"]); edge(rid, uid, "cites", {"verbatim": "verbatim", "composite": "composite case"}.get(s["kind"], "dataset summary"))
            nodes[uid]["detail"].setdefault("quotes", [])
            if s["kind"] == "verbatim" and s["quote_span"] not in nodes[uid]["detail"]["quotes"]:
                nodes[uid]["detail"]["quotes"].append(s["quote_span"])
        if not r.get("learned"):
            for rel in related(r):                                              # automatically retrieved related units
                uid = unit_node(rel["unit_id"]); nodes[uid]["type"] = "related" if nodes[uid]["type"] == "unit" and not any(
                    e["source"] != rid and e["target"] == uid and e["type"] == "cites" for e in edges) else nodes[uid]["type"]
                edge(rid, uid, "related", "related")
            for gl in context_for(r):                                           # guideline context
                gid = node(f"guideline:{gl['page']}", "guideline", f"Guideline p.{gl['page']}", detail={**gl})
                edge(rid, gid, "context", "guideline context")
            for pc in public_context(r):                                          # cached public-web guidance (BrightData), when refreshed
                pid = node(f"public:{abs(hash(pc['url'] + pc['excerpt'])) % 10**6}", "guideline", "Public guidance", detail={**pc, "page": "web", "source": f"{pc['provider']}, {pc['url']}, fetched {pc['fetched_at']}"})
                edge(rid, pid, "context", "public guidance")
        for m in live:                                                           # live screen moments
            mid = node(f"moment:{m['event_id']}", "moment", f"{m['ts']:.0f}s live", "live", {"quote": m["quote"], "ts": m["ts"], "slot": m["slot"], "frame": m["frame"], "event_id": m["event_id"]})
            edge(rid, mid, "seen_live", "seen live")

    lm = learned.load()["cards"]                                                 # everything learned from all interviews
    active_rules = [r for r in session.rules if not r.get("learned")]
    rederived = []
    for c in lm:
        rv = reviews.get(c["id"], {}); ok_rv = rv and (not rv.get("title") or rv["title"] == c["title"])
        st = ("accepted" if rv.get("decision") == "confirm" else "rejected" if rv.get("decision") == "reject" else "unreviewed") if ok_rv else "unreviewed"
        cid = node(f"learned:{c['id']}", "learned", _short(c["title"], 40), st, {
            "card_id": c["id"], "title": c["title"], "kind": c["kind"], "context": c["context"], "action": c["action"], "rationale": c["rationale"], "exceptions": c["exceptions"],
            "guardrails": c["guardrails"], "escalation": c["escalation"], "sessions": c["sessions"], "safety_critical": c["safety_critical"], "corroboration": c["corroboration"],
            "evidence": c["evidence"][:3], "evidence_status": c["evidence_status"], "predicate": guard.render(c["proposed_predicate"]) if c.get("proposed_predicate") else "",
            "review": rv.get("decision", "") if ok_rv else ""}, kind=c["kind"], safety=c["safety_critical"])
        for uid in c["units"]:
            edge(cid, unit_node(uid), "learned_from", "learned from")
        if c.get("proposed_predicate"):
            for r in active_rules:
                if rederives(c["proposed_predicate"], r):
                    edge(cid, f"rule:{r['id']}", "re_derives", "re-derives"); rederived.append((c["id"], r["id"]))

    types: dict[str, int] = {}
    for n in nodes.values():
        types[n["type"]] = types.get(n["type"], 0) + 1
    return {"nodes": list(nodes.values()), "edges": edges, "stats": {"nodes": len(nodes), "edges": len(edges), "by_type": types,
            "rules_rederived_by_learner": sorted({r for _, r in rederived}), "map_version": session.map_version()}}
