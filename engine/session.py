"""Session state machine: events -> gated questions -> answers -> slot updates -> debrief -> Work Map -> Teach."""
import json, os, time, uuid
from copy import deepcopy
from pathlib import Path

from . import guard, learned, scope, slots as S, llm
from .confidence import facets
from .related import related
from .literature import cbt_check, context_for
from .evidence_refresh import public_context
from .mastery import Mastery
from .privacy import redact

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"


def reviews_path() -> Path:
    return Path(os.getenv("APPRENTICE_REVIEWS_FILE", DATA / "reviews.json"))


def load_reviews() -> dict:
    try:
        return json.loads(reviews_path().read_text())
    except Exception:
        return {}
LABEL = {"retry_later_same_carer": "retry later with the same carer", "swap_carer_or_call_psychologist": "swap carer / call the psychologist",
         "reassure_and_note": "reassure and note it", "give_prn_medication": "give PRN medication", "adjust_diet": "adjust the diet",
         "integration_plan_review": "review the integration plan", "no_action": "take no action",
         "prompted_toileting": "prompt toileting", "restore_signage": "restore the signage", "request_antipsychotic": "ask the physician for an antipsychotic", "add_activities": "add activities"}
BUDGET_PER_10MIN = int(os.getenv("APPRENTICE_Q_BUDGET", "5"))
COOLDOWN = float(os.getenv("APPRENTICE_COOLDOWN", "20"))
STALE = 12.0


def load_rules():
    return deepcopy(json.loads((HERE / "care_map" / "demo_rules.json").read_text())["rules"])


def load_cases():
    return json.loads((HERE / "cases.json").read_text())


class Conflict(Exception):
    """Impossible state transition or write while recording is off; the API maps this to HTTP 409."""


def nid(p): return f"{p}-{uuid.uuid4().hex[:6]}"


def describe(ev: dict) -> str:
    f, v, d = ev.get("field"), ev.get("value"), ev.get("delta") or {}
    if f == "checks":
        if d.get("added"): return f"ticked '{d['added'].replace('_', ' ')}' in the checks list"
        if d.get("removed"): return f"unticked '{d['removed'].replace('_', ' ')}'"
    if f == "intervention": return f"set the intervention to '{LABEL.get(v, v)}'"
    if f == "escalate_to": return f"chose to escalate to '{str(v).replace('_', ' ')}'"
    if f == "occurrences_today": return f"set occurrences today to {v}"
    if f == "pattern": return f"marked the behaviour as '{v}'"
    if f == "interpretation": return f"chose the interpretation '{str(v).replace('_', ' ')}'"
    if f == "incident_type": return f"set the incident type to '{str(v).replace('_', ' ')}'"
    if f == "months_in_residence": return f"set months in residence to {v}"
    if f == "observation": return "wrote a free-text observation"
    if f == "save": return "pressed Save"
    return f"changed {f}"


class Session:
    def __init__(self, mode="capture", drift=None):
        """`drift` is an optional extension adapter (engine.drift); the live path never requires it."""
        self.drift = drift
        self.id = nid("S"); self.mode = mode; self.t0 = time.time()
        self.rules = load_rules(); self.cases = load_cases()
        self.reviews = load_reviews(); self._apply_reviews()
        self.events, self.questions, self.answers = [], [], []
        self.frames = {}; self.tombstones = []; self.degraded = False
        self.teachback = {}             # rule_id -> confirmed|corrected
        self.corrections = []; self.drift_log = []
        self.mastery = Mastery([r["id"] for r in self.rules]); self.attempts = []; self.done_cases = set()
        self.log = []
        self.recording = True            # Off the record flips this server-side; late writes are rejected
        self.predicted = set()           # teach cases the learner made a prediction on
        self.artifact_version = None     # Work Map hash frozen when Teach starts

    # ---------- helpers
    def rule(self, rid): return next(r for r in self.rules if r["id"] == rid)
    def now(self): return round(time.time() - self.t0, 2)
    def ev(self, eid): return next((e for e in self.events if e["id"] == eid), None)
    def touched(self): return {q["rule_id"] for q in self.questions}

    def _save(self):
        try:
            DATA.mkdir(exist_ok=True)
            (DATA / "session.json").write_text(json.dumps(self.export(), default=str))
        except Exception:
            pass

    # ---------- expert review (persisted: survives restarts, applies to every new session)
    def _apply_reviews(self):
        """A review is a flag on the rule; slots keep their honest provenance and `slots.eff` derives the effective state."""
        for r in self.rules:
            for k in ("reviewed", "review_note", "rejected"):
                r.pop(k, None)
            rv = self.reviews.get(r["id"])
            if rv:
                r["reviewed"] = "confirmed" if rv["decision"] == "confirm" else "rejected"
                r["review_note"] = rv.get("note", "")
                r["rejected"] = rv["decision"] == "reject"
        # accepted learned cards join as warn-level rules (human acceptance is what makes learned knowledge enforceable)
        self.rules = [r for r in self.rules if not r.get("learned")]
        for cid, rv in self.reviews.items():
            if cid.startswith("LC-") and rv["decision"] == "confirm":
                c = learned.card(cid)
                if c and rv.get("title") and rv["title"] != c["title"]:
                    c = None                    # the learned map was rebuilt: never apply a review to a different card
                lr = learned.to_rule(c) if c else None
                if lr:
                    lr["reviewed"] = "confirmed"; lr["review_note"] = rv.get("note", ""); self.rules.append(lr)

    def review(self, rule_id: str, decision: str, note: str = "") -> dict:
        """decision: confirm | reject | reset. The reviewer is the human expert using the app (no authentication in this demo)."""
        known = any(r["id"] == rule_id for r in self.rules) or (rule_id.startswith("LC-") and learned.card(rule_id) is not None)
        if not known:
            raise Conflict("unknown rule")
        revs = load_reviews()
        if decision == "reset":
            revs.pop(rule_id, None)
        else:
            revs[rule_id] = {"decision": decision, "note": redact(note)[0][:600], "ts": self.now(), "at": time.strftime("%Y-%m-%d %H:%M")}
            if rule_id.startswith("LC-"):       # learned-card ids depend on the merge run, so bind the review to the card's title
                revs[rule_id]["title"] = (learned.card(rule_id) or {}).get("title", "")
        reviews_path().parent.mkdir(exist_ok=True); reviews_path().write_text(json.dumps(revs, indent=1))
        self.reviews = revs; self._apply_reviews(); self._save()
        return {"rule_id": rule_id, "decision": decision if decision != "reset" else "not reviewed", "map_version": self.map_version()}

    def _need_recording(self):
        if not self.recording:
            raise Conflict("off the record: collection is stopped")

    def set_recording(self, on: bool) -> dict:
        self.recording = bool(on)
        return {"recording": self.recording}

    def freeze(self):
        """Teach consumes this exact artifact: later edits to the source session cannot change evaluation."""
        self.artifact_version = self.map_version()

    # ---------- capture
    def add_event(self, ev: dict) -> dict:
        self._need_recording()
        ev = {**ev, "id": nid("E"), "ts": self.now()}
        ev["text"] = describe(ev)
        self.events.append(ev)
        out = {"event": ev, "drift_question": None}
        if self.drift and ev.get("field") == "save" and self.mode == "capture":
            for c in self.drift.contradictions(self.rules, ev.get("form", {})):
                r = self.rule(c["rule_id"]); S.apply_state(r, "guardrails", "conflicted"); r["conflicted"] = True
                self.drift_log.append({"rule_id": r["id"], "event_id": ev["id"], "ts": ev["ts"], "trace": c["trace"], "disposition": "pending"})
                q = self._mk_question(r, "guardrails", 0, ev, qtype="drift",
                    text=f"Earlier material says: {c['message']} But your saved record does not follow that. What changed here?")
                out["drift_question"] = q
        self._save()
        return out

    MAX_FRAMES = 40
    VISION_FIELDS = {"checks", "intervention", "escalate_to", "save"}
    VISION_BUDGET = 12

    def add_frame(self, event_id: str, data_url: str) -> str | None:
        """Store the cropped frame; a vision model confirms/captions the change for the decision-relevant events only."""
        self._need_recording()
        self.frames[event_id] = data_url
        while len(self.frames) > self.MAX_FRAMES:          # bound memory: the oldest frames are dropped first
            self.frames.pop(next(iter(self.frames)))
        ev = self.ev(event_id)
        if not ev or ev.get("field") not in self.VISION_FIELDS or sum(1 for e in self.events if e.get("vision")) >= self.VISION_BUDGET:
            return None
        try:
            cap = llm.vision(data_url, f"A DOM event reports that the expert {ev['text']}. This is a cropped screenshot of a fake care-records form. "
                             "In one short sentence start with 'Confirmed:' or 'Not visible:' and say what the relevant field shows. Describe only what is visible; infer nothing clinical.")
        except Exception:
            self.degraded = True
            return None
        ev["vision"] = redact(cap)[0]
        self._save()
        return ev["vision"]

    def _mk_question(self, rule, slot, rung, ev, qtype=None, text=None, score=0.0, alts=None):
        qtype = qtype or {"rationale": "why", "exceptions": "counterfactual", "guardrails": "guardrail",
                          "escalation": "guardrail", "action": "why", "context": "why"}[slot]
        text = text or S.phrase(rule, slot, rung, ev["text"] if ev else None)
        q = {"id": nid("Q"), "ts": self.now(), "rule_id": rule["id"], "slot": slot, "rung": rung, "type": qtype,
             "event_id": ev["id"] if ev else None, "event_text": ev["text"] if ev else None, "text": text,
             "score": score, "alternatives": alts or [], "answered": False, "phase": self.mode}
        self.questions.append(q)
        return q

    def propose_question(self, event_id: str, signals: dict) -> dict:
        """Server-side gate. signals: voice_silent, hands_still, screen_stable. Returns {question|None, reason, gate}."""
        self._need_recording()
        gate = {"voice_silent": bool(signals.get("voice_silent")), "hands_still": bool(signals.get("hands_still")),
                "screen_stable": bool(signals.get("screen_stable"))}
        asked = [q for q in self.questions if q["phase"] == "capture"]
        last = asked[-1]["ts"] if asked else -1e9
        gate["cooldown_done"] = (self.now() - last) >= COOLDOWN
        gate["budget_ok"] = sum(1 for q in asked if self.now() - q["ts"] < 600) < BUDGET_PER_10MIN
        ev = self.ev(event_id)
        if ev is None:
            return {"question": None, "reason": "unknown event", "gate": gate}
        latest = self.events[-1]
        gate["fresh"] = ev["id"] == latest["id"] and (self.now() - ev["ts"] <= STALE)   # stale or superseded events are never asked about
        if not all(gate.values()):
            return {"question": None, "reason": "gate closed: " + ", ".join(k for k, v in gate.items() if not v), "gate": gate}
        if any(q for q in self.questions if q["event_id"] == event_id):
            return {"question": None, "reason": "event already asked about", "gate": gate}
        g_asked = any(q["type"] == "guardrail" for q in asked)
        incident = (ev.get("form") or {}).get("incident_type")
        what = (ev.get("delta") or {}).get("added") or ev.get("value")        # the checkbox just ticked, or the value just chosen
        cands = S.candidates(self.rules, [q for q in self.questions], ev.get("field"), None, g_asked, incident, what)
        if not cands:
            return {"question": None, "reason": "no uncertain slot relevant to this screen event", "gate": gate}
        top = cands[0]
        rule = self.rule(top["rule_id"])
        q = self._mk_question(rule, top["slot"], top["rung"], ev, score=top["score"], alts=cands[1:4])
        self._save()
        return {"question": q, "gate": gate, "reason": "ok"}

    # ---------- answers
    def _extract(self, q, rule, text):
        sys_ = ("You extract care knowledge from a caregiving expert's spoken answer for a documentation-and-escalation training tool. "
                "Only use what the expert said. JSON keys: dont_know (bool), slot_text (short, expert's terms, '' if none), "
                "exceptions (list of strings: when NOT to do it), guardrails (list: stop/limit/ask-someone rules), escalation (string who to ask, ''), "
                "contradicts_rule (bool: does the answer contradict the existing rule text), threshold (number or null: any numeric limit stated e.g. number of refusals).")
        user = (f"Question slot: {q['slot']}. Question: {q['text']}\nExisting rule: {rule['title']}\n"
                f"Existing slot text: {rule['slots'][q['slot']] if not isinstance(rule['slots'][q['slot']], list) else [i['text'] for i in rule['slots'][q['slot']]]}\n"
                f"Expert answer: {text}")
        try:
            return llm.complete_json(sys_, user, llm.FAST, 500)
        except Exception:
            self.degraded = True
            low = text.lower()
            dk = any(p in low for p in ["don't know", "do not know", "not sure", "no idea"])
            return {"dont_know": dk, "slot_text": text if not dk else "", "exceptions": [text] if q["slot"] == "exceptions" and not dk else [],
                    "guardrails": [text] if q["slot"] == "guardrails" and not dk else [], "escalation": text if q["slot"] == "escalation" and not dk else "",
                    "contradicts_rule": q["type"] == "drift" and any(p in low for p in ["exception", "because", "changed"]) and False,
                    "threshold": self.drift.extract_threshold(text) if self.drift and rule["id"] == "R3-repeat-escalate" and "time" in low else None}

    def answer(self, qid: str, text: str, ts: float | None = None) -> dict:
        self._need_recording()
        q = next(x for x in self.questions if x["id"] == qid)
        if q["answered"]:
            raise Conflict("question already answered")
        rule = self.rule(q["rule_id"]); ts = ts if ts is not None else self.now()
        clean, nred = redact(text)
        before = S.snapshot_states([rule])[rule["id"]]
        ex = self._extract(q, rule, clean)
        prov = {"kind": "screen" if q["event_id"] else "debrief", "event_id": q["event_id"], "ts": ts, "question_id": qid,
                "quote": clean, "derived_from": [qid], "frame": q["event_id"] in self.frames}
        ans = {"id": nid("A"), "question_id": qid, "ts": ts, "text": clean, "redactions": nred, "extract": ex}
        self.answers.append(ans); q["answered"] = True
        slot = q["slot"]
        if not ex.get("dont_know"):
            new_state = "conflicted" if (ex.get("contradicts_rule") and q["type"] != "drift") else "expert_stated"
            if q["type"] == "drift":
                new_state = "expert_stated"
                for d in self.drift_log:
                    if d["rule_id"] == rule["id"] and d["disposition"] == "pending":
                        d["disposition"] = "exception-or-change (expert explained)"; d["quote"] = clean
                rule["conflicted"] = False
            v = rule["slots"][slot]
            if isinstance(v, list):
                # Only what the expert actually said becomes live evidence; seeded items keep their own (hypothesized) state.
                texts = ex.get("exceptions" if slot == "exceptions" else "guardrails") or [clean]
                for t in texts:
                    v.append({"text": t, "state": new_state, "live": [prov]})
            else:
                v["state"] = new_state
                v.setdefault("live", []).append(prov)
            if slot == "escalation" and ex.get("escalation"):
                v["live_text"] = ex["escalation"]
            if ex.get("slot_text") and not isinstance(v, list):
                v["live_text"] = ex["slot_text"]
            if self.drift and ex.get("threshold") is not None and rule["id"] == "R3-repeat-escalate":
                upd = self.drift.boundary_update(rule, "occurrences_today", ex["threshold"], clean, ts)
                if upd:
                    self.drift_log.append({"rule_id": rule["id"], "event_id": q["event_id"], "ts": ts, "boundary": upd, "disposition": "pending"})
        after = S.snapshot_states([rule])[rule["id"]]
        self._save()
        return {"answer": ans, "transition": {"slot": slot, "before": before[slot], "after": after[slot]},
                "uncertainty": S.uncertainty(rule), "dont_know": bool(ex.get("dont_know"))}

    # ---------- debrief
    def worked_incident(self) -> str | None:
        """The incident type the expert actually worked in Capture (the last non-'other' type on screen)."""
        for e in reversed(self.events):
            t = (e.get("form") or {}).get("incident_type")
            if t and t != "other":
                return t
        return None

    def debrief_start(self) -> dict:
        if not any(q["phase"] == "capture" and q["answered"] for q in self.questions):
            raise Conflict("capture at least one explained decision before the debrief")
        asked = self.questions
        touched = {q["rule_id"] for q in asked if q["phase"] == "capture"}
        gaps = S.gap_scan(self.rules, asked, touched or None, 3, incident=self.worked_incident())
        qs = []
        for g in gaps:
            rule = self.rule(g["rule_id"])
            q = self._mk_question(rule, g["slot"], g["rung"], None, qtype=None, score=g["score"],
                                  text=S.phrase(rule, g["slot"], g["rung"], None))
            q["phase"] = "debrief"
            qs.append(q)
        unseen = [r for r in self.rules if r["id"] not in touched]
        if unseen:
            r = max(unseen, key=lambda x: x["risk"])
            uq = self._mk_question(r, "action", 0, None, qtype="unseen", score=0,
                                   text=f"I did not see this case today: {r['slots']['context']['text']} What would you do, and when would you stop and ask someone?")
            uq["phase"] = "debrief"; qs.append(uq)
        self._save()
        return {"gaps": gaps, "questions": qs, "states": S.snapshot_states(self.rules)}

    def debrief_status(self) -> dict:
        deb = [q for q in self.questions if q["phase"] == "debrief"]
        answered = [q for q in deb if q["answered"]]
        live_rules = {q["rule_id"] for q in self.questions if q["phase"] == "capture"}
        guard_ok = all(S.slot_state(self.rule(r), "guardrails") in ("expert_stated", "confirmed") or not self.rule(r)["slots"]["guardrails"]
                       for r in live_rules)
        steps = self.workmap()["steps"]
        tb = bool(steps) and all(s["teach_back"] in ("confirmed", "corrected") for s in steps)
        new_unanswered_in_task = len(answered) >= 3
        residual = S.gap_scan(self.rules, self.questions, live_rules or None, 3, incident=self.worked_incident())
        return {"new_followups_answered": len(answered), "needs_3_new": new_unanswered_in_task, "guardrails_covered": guard_ok,
                "teach_back_done": tb, "done": new_unanswered_in_task and guard_ok and tb, "residual_gaps": residual,
                "note": "Done means these checks pass; it does not prove complete understanding."}

    # ---------- work map
    def _map_confirmed(self) -> bool:
        """True when every active rule is confirmed by an expert review or by a live teach-back (rejected rules are out of the map)."""
        active = [r for r in self.rules if not r.get("rejected") and not r.get("learned")]
        return bool(active) and all(r.get("reviewed") == "confirmed" or self.teachback.get(r["id"]) in ("confirmed", "corrected") for r in active)

    def map_version(self) -> str:
        """Content hash of what Teach consumes: slot states, live items, predicates and teach-back results."""
        import hashlib
        blob = json.dumps({"r": [{"id": r["id"], "slots": r["slots"], "p": r["predicate"], "rv": r.get("reviewed", "")} for r in self.rules], "tb": self.teachback}, sort_keys=True, default=str)
        return hashlib.sha1(blob.encode()).hexdigest()[:8]

    def workmap(self) -> dict:
        steps, by_rule = [], {}
        for q in self.questions:
            if q["phase"] == "capture" and q["event_id"]:
                by_rule.setdefault(q["rule_id"], []).append(q)
        for rid, qs in by_rule.items():
            r = self.rule(rid); first = self.ev(qs[0]["event_id"])
            answers = [a for a in self.answers if a["question_id"] in {q["id"] for q in qs} and not a["extract"].get("dont_know")]
            live_n = len(answers)
            live_quotes = [{"text": a["text"], "ts": a["ts"], "event_id": next(q["event_id"] for q in qs if q["id"] == a["question_id"]),
                            "slot": next(q["slot"] for q in qs if q["id"] == a["question_id"])} for a in answers]
            gs = []
            for g in r["slots"]["guardrails"]:
                gs.append({"text": g["text"], "state": S.eff(r, g["state"]),
                           "expert_words": [{"kind": "screen", **p} for p in g.get("live", [])] or self._source_evidence(r)[:1]})
            unresolved = [s for s in S.SLOTS if S.slot_state(r, s) in ("missing", "hypothesized", "conflicted")]
            steps.append({"step_id": f"STEP-{rid}", "rule_id": rid, "title": r["title"], "screen_moment": {"event_id": first["id"], "ts": first["ts"],
                          "text": first["text"], "form": first.get("form"), "frame": first["id"] in self.frames, "vision": first.get("vision")},
                          "decision": first["text"], "reason": live_quotes, "rationale_seed": r["slots"]["rationale"]["text"],
                          "exceptions": [{"text": e["text"], "state": S.eff(r, e["state"])} for e in r["slots"]["exceptions"]],
                          "guardrails": gs, "escalation": r["slots"]["escalation"].get("live_text") or r["slots"]["escalation"]["text"],
                          "unresolved_slots": unresolved, "teach_back": self.teachback.get(rid, "none"),
                          "confidence": facets(r, live_n, self.teachback.get(rid, "none"), r.get("reviewed", "")), "reviewed": r.get("reviewed", ""), "review_note": r.get("review_note", ""),
                          "provenance": [{"kind": "screen", "event_id": q["event_id"], "ts": q["ts"]} for q in qs] +
                                        [{"kind": "composite" if s["kind"] == "composite" else "transcript", "unit_id": s["unit_id"], "session": s["session"], "turn_ids": s["turn_ids"],
                                          "span": s["quote_span"], "verbatim": s["kind"] == "verbatim"} for s in r["sources"]],
                          "versions": r.get("versions", []), "related": related(r), "guidelines": context_for(r), "public": public_context(r), "cbt": cbt_check(r)})
        steps.sort(key=lambda s: s["screen_moment"]["ts"])
        for i, s in enumerate(steps): s["n"] = i + 1
        seeded = [{"rule_id": r["id"], "title": r["title"], "risk": r["risk"],
                   "context": r["slots"]["context"]["text"], "action": r["slots"]["action"]["text"], "rationale": r["slots"]["rationale"]["text"],
                   "guardrails": [{"text": g["text"], "state": S.eff(r, g["state"])} for g in r["slots"]["guardrails"]],
                   "escalation": r["slots"]["escalation"]["text"], "predicate": guard.render(r["predicate"]), "severity": r["predicate"]["severity"],
                   "evidence": self._source_evidence(r), "caution": r.get("caution", ""),
                   "confidence": facets(r, 0, "none", r.get("reviewed", ""))["label"], "reviewed": r.get("reviewed", ""), "review_note": r.get("review_note", ""),
                   "states": S.snapshot_states([r])[r["id"]], "related": related(r), "guidelines": context_for(r), "public": public_context(r), "cbt": cbt_check(r)}
                  for r in self.rules if r["id"] not in by_rule and not r.get("learned")]
        asked_events = {q["event_id"] for q in self.questions if q["event_id"]}
        unexplained = [{"event_id": e["id"], "ts": e["ts"], "text": e["text"]} for e in self.events if e["id"] not in asked_events and e.get("field") != "save"]
        return {"schema": "workmap/1", "map_version": self.map_version(), "steps": steps, "unexplained_events": unexplained, "drift": self.drift_log, "seeded_only_rules": [r["id"] for r in self.rules if r["id"] not in by_rule and not r.get("learned")], "learned_active": [r["id"] for r in self.rules if r.get("learned")], "seeded_rules": seeded}

    @staticmethod
    def _source_evidence(r) -> list[dict]:
        """Transcript evidence for a rule. Only verbatim spans are quotations; dataset summaries are labelled as such."""
        out = []
        for s in r["sources"]:
            base = {"unit_id": s["unit_id"], "turn": s["quote_turn"], "session": s["session"]}
            if s["kind"] == "verbatim":
                out.append({"kind": "transcript", "quote": s["quote_span"], **base})
            elif s["kind"] == "composite":      # the startup team's composite micro case: invented details, not an interview
                out.append({"kind": "composite_case", "quote": s["quote_span"], **base})
            else:
                out.append({"kind": "dataset_summary", "summary": s["quote_span"], **base})
        return sorted(out, key=lambda e: ["transcript", "dataset_summary", "composite_case"].index(e["kind"]))   # verbatim interview words first

    def teachback_text(self) -> dict:
        wm = self.workmap()["steps"]
        base = [f"{s['n']}. {s['title']}. You said: " + (" / ".join(q["text"] for q in s["reason"][:2]) or "(no explanation yet)") +
                (f" Guardrail: {s['guardrails'][0]['text']}" if s["guardrails"] else "") for s in wm]
        try:
            txt = llm.complete("Explain a care-documentation process back to the expert in short numbered steps, using only the facts given, "
                               "in plain spoken English. One sentence each, then end by asking 'Is that how it works?'.",
                               "\n".join(base), llm.SMART, 500)
        except Exception:
            self.degraded = True
            txt = "\n".join(base) + "\nIs that how it works?"
        return {"text": txt, "steps": [{"step_id": s["step_id"], "rule_id": s["rule_id"], "summary": b} for s, b in zip(wm, base)]}

    def confirm(self, rule_id: str, ok: bool, correction: str | None = None) -> dict:
        r = self.rule(rule_id)
        if not any(q["rule_id"] == rule_id and q["phase"] == "capture" and q["event_id"] for q in self.questions):
            raise Conflict("nothing was captured live for this rule yet")
        if ok:
            self.teachback[rule_id] = "confirmed"
            for s in S.SLOTS:
                v = r["slots"][s]
                for it in (v if isinstance(v, list) else [v]):
                    if it["state"] == "expert_stated": it["state"] = "confirmed"
        else:
            clean, _ = redact(correction or "")
            self.teachback[rule_id] = "corrected"
            self.corrections.append({"rule_id": rule_id, "text": clean, "ts": self.now()})
            r["slots"]["exceptions"].append({"text": clean, "state": "expert_stated", "live": [{"kind": "debrief", "ts": self.now(), "quote": clean}]})
        self._save()
        return {"rule_id": rule_id, "teach_back": self.teachback[rule_id], "confidence": facets(r, 1, self.teachback[rule_id])}

    # ---------- off the record
    def off_record(self, since_ts: float) -> dict:
        gone_q = {q["id"] for q in self.questions if q["ts"] >= since_ts}
        n = {"events": 0, "frames": 0, "questions": len(gone_q), "answers": 0, "slot_items": 0}
        keep_ev = []
        for e in self.events:
            if e["ts"] >= since_ts:
                n["events"] += 1; n["frames"] += 1 if self.frames.pop(e["id"], None) else 0
            else:
                keep_ev.append(e)
        self.events = keep_ev
        a_gone = {a["id"] for a in self.answers if a["question_id"] in gone_q or a["ts"] >= since_ts}
        n["answers"] = len(a_gone)
        self.answers = [a for a in self.answers if a["id"] not in a_gone]
        self.questions = [q for q in self.questions if q["id"] not in gone_q]
        for r in self.rules:
            for s in S.SLOTS:
                v = r["slots"][s]
                if isinstance(v, list):
                    kept = []
                    for it in v:
                        live = [p for p in it.get("live", []) if p.get("question_id") not in gone_q and p.get("ts", 0) < since_ts]
                        if it.get("live") and not live and it["state"] != "hypothesized":
                            n["slot_items"] += 1; continue
                        it["live"] = live; kept.append(it)
                    v[:] = kept
                else:
                    live = [p for p in v.get("live", []) if p.get("question_id") not in gone_q and p.get("ts", 0) < since_ts]
                    if v.get("live") and not live:
                        v["state"] = "hypothesized" if v.get("text") else "missing"; v.pop("live_text", None); n["slot_items"] += 1
                    v["live"] = live
        t = {"since": since_ts, "at": self.now(), **n,
             "disclosure": "Collection stopped and local copies deleted. Data already sent to model/voice providers follows their retention terms."}
        self.tombstones.append(t); self._save()
        return t

    # ---------- teach
    def teach_open(self, case_id: str) -> dict:
        c = next(x for x in self.cases["teach_cases"] if x["id"] == case_id)
        return {k: v for k, v in c.items() if k not in ("rule_ids",)} | {"map_version": self.map_version(), "map_confirmed": self._map_confirmed(), "predict": {"question": c["predict"]["question"], "options": c["predict"]["options"]}}

    def teach_predict(self, case_id: str, option: str) -> dict:
        c = next(x for x in self.cases["teach_cases"] if x["id"] == case_id)
        ok = option == c["predict"]["correct"]
        if case_id in self.predicted:
            raise Conflict("prediction already recorded for this case")
        self.predicted.add(case_id)
        for rid in c["rule_ids"]:
            self.mastery.update(rid, ok)
        self.attempts.append({"case": case_id, "kind": "predict", "option": option, "correct": ok, "ts": self.now()})
        r0 = self.rule(c["rule_ids"][0])
        return {"correct": ok, "correct_option": None if not ok else option,
                "explain": self._explain(r0) if not ok else "That matches how the expert reasons."}

    def _live_moments(self, r) -> list[dict]:
        """What the expert said live about this rule, each tied to the screen event it was asked about."""
        out = []
        for q in self.questions:
            if q["rule_id"] != r["id"] or q["phase"] != "capture" or not q["event_id"]:
                continue
            for a in self.answers:
                if a["question_id"] == q["id"] and not a["extract"].get("dont_know"):
                    out.append({"event_id": q["event_id"], "ts": a["ts"], "quote": a["text"], "slot": q["slot"], "frame": q["event_id"] in self.frames})
        return out

    def _explain(self, r) -> dict:
        live = self._live_moments(r)
        words = [{"kind": "screen", "quote": m["quote"], "ts": m["ts"], "event_id": m["event_id"], "frame": m["frame"]} for m in live[:2]]
        src = self._source_evidence(r)
        words += [e for e in src if e["kind"] == "transcript"][:2]
        return {"rule_id": r["id"], "title": r["title"], "expert_words": words,
                "dataset_summaries": [e for e in src if e["kind"] == "dataset_summary"][:2],
                "composite_cases": [e for e in src if e["kind"] == "composite_case"][:2],
                "screen_moment": {"event_id": live[0]["event_id"], "ts": live[0]["ts"], "frame": live[0]["frame"]} if live else None}

    def teach_check_save(self, case_id: str, form: dict) -> dict:
        c = next(x for x in self.cases["teach_cases"] if x["id"] == case_id)
        if case_id not in self.predicted:
            raise Conflict("make a prediction before saving")
        if self.artifact_version and self.map_version() != self.artifact_version:
            raise Conflict("the Work Map changed after Teach started")
        fired = guard.check_form(self.rules, form)
        blocked = [f for f in fired if f["severity"] == "block"]
        first = not any(a["case"] == case_id and a["kind"] == "save" for a in self.attempts)
        res = {"saved": not blocked, "blocked": [], "warnings": [{"rule_id": w["rule_id"], "message": w["message"], "trace": w["trace"]} for w in fired if w["severity"] == "warn"]}
        for b in blocked:
            r = self.rule(b["rule_id"]); ex = self._explain(r)
            res["blocked"].append({**b, "explain": ex, "guardrail_id": r["id"],
                                   "evidence_class": "live expert capture" if ex["screen_moment"] else "confirmed by expert review" if r.get("reviewed") == "confirmed" else "transcript only (not yet confirmed live)"})
        sc = scope.classify(form.get("observation", ""))
        if sc["escalate"] and form.get("escalate_to") not in scope.HUMAN_ROUTES:   # fail closed on clinical language
            blocked.append({"rule_id": "SCOPE-clinical-escalation"})
            res["blocked"].append({"rule_id": "SCOPE-clinical-escalation", "title": "A clinical concern must reach a qualified human", "severity": "block",
                "message": sc["reason"], "guardrail_id": "SCOPE-clinical-escalation", "evidence_class": "fail-closed safety lexicon (not learned from the expert)",
                "trace": [{"field": "observation", "op": "mentions", "expected": "a clinical term", "observed": sc["term"], "met": True},
                          {"field": "escalate_to", "op": "in", "expected": list(scope.HUMAN_ROUTES), "observed": form.get("escalate_to", ""), "met": False}],
                "text": f"observation mentions a clinical term AND escalate_to not in {list(scope.HUMAN_ROUTES)}",
                "explain": {"rule_id": "SCOPE", "title": "Clinical scope", "expert_words": [], "dataset_summaries": [], "screen_moment": None}})
        if form.get("incident_type") not in scope.COVERED_INCIDENTS and form.get("escalate_to") not in scope.HUMAN_ROUTES:   # outside learned knowledge: abstain
            blocked.append({"rule_id": "ABSTAIN-outside-knowledge"})
            res["blocked"].append({"rule_id": "ABSTAIN-outside-knowledge", "title": "Outside what the apprentice has learned", "severity": "block",
                "message": "No learned rule covers this incident type, so the apprentice abstains. A qualified person must review it.",
                "guardrail_id": "ABSTAIN-outside-knowledge", "evidence_class": "abstention (no learned rule applies)",
                "trace": [{"field": "incident_type", "op": "in", "expected": list(scope.COVERED_INCIDENTS), "observed": form.get("incident_type", ""), "met": False},
                          {"field": "escalate_to", "op": "in", "expected": list(scope.HUMAN_ROUTES), "observed": form.get("escalate_to", ""), "met": False}],
                "text": f"incident_type not in {list(scope.COVERED_INCIDENTS)} AND escalate_to not in {list(scope.HUMAN_ROUTES)}",
                "explain": {"rule_id": "ABSTAIN", "title": "Outside knowledge", "expert_words": [], "dataset_summaries": [], "screen_moment": None}})
        res["saved"] = not blocked
        self.attempts.append({"case": case_id, "kind": "save", "blocked": [b["rule_id"] for b in blocked], "first": first, "ts": self.now()})
        if first:
            for rid in c["rule_ids"]:
                bad = rid in [b["rule_id"] for b in blocked]
                self.mastery.update(rid, not bad)
        else:
            for b in blocked:
                self.mastery.update(b["rule_id"], False, hinted=True)
        if not blocked:
            self.done_cases.add(case_id)
        risk = {r["id"]: r["risk"] for r in self.rules}
        nxt, score = self.mastery.next_scenario([{"id": x["id"], "rule_ids": x["rule_ids"]} for x in self.cases["teach_cases"]], risk, self.done_cases)
        res["mastery"] = self.mastery.summary(); res["next_scenario"] = nxt["id"] if nxt else None; res["next_score"] = score
        self._save()
        return res

    def export(self):
        return {"id": self.id, "mode": self.mode, "events": self.events, "questions": self.questions, "answers": self.answers,
                "rules": self.rules, "teachback": self.teachback, "drift": self.drift_log, "tombstones": self.tombstones,
                "attempts": self.attempts, "degraded": self.degraded}
