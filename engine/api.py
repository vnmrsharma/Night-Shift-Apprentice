"""FastAPI surface. From the repo root: uvicorn engine.api:app --port 8000
The Night Shift page is served at /. API routes are registered first, so they are not shadowed."""
import asyncio, contextvars, copy, json, os
from collections import OrderedDict
from pathlib import Path
import base64
from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

from . import export as exporter, graph as care_graph, learned, llm, scope
from .session import Conflict, Session

app = FastAPI(title="Apprentice engine")
# Browser origins allowed to call the engine. Production sets APPRENTICE_ALLOWED_ORIGINS="https://your-app.vercel.app" (comma separated).
ALLOWED = [o.strip() for o in os.getenv("APPRENTICE_ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",") if o.strip()]
# Optional pattern for hosted preview/production URLs of OUR Vercel project(s); a bare "*.vercel.app" would let any site spend our API credits.
ORIGIN_REGEX = os.getenv("APPRENTICE_ALLOWED_ORIGIN_REGEX") or None
app.add_middleware(CORSMiddleware, allow_origins=ALLOWED, allow_origin_regex=ORIGIN_REGEX, allow_methods=["*"], allow_headers=["*"])
SESSIONS: "OrderedDict[str, Session]" = OrderedDict()   # one Session per browser (X-Session-Id), oldest evicted
CURRENT = contextvars.ContextVar("sid", default="default")
MAX_SESSIONS = 60            # in-memory; frames per session are capped below, so worst case stays well inside a 512 MB host
LOCK = asyncio.Lock()


@app.middleware("http")
async def serialise(request, call_next):
    sid = (request.headers.get("x-session-id") or request.query_params.get("sid") or "default")[:64]
    CURRENT.set(sid)
    async with LOCK:
        return await call_next(request)


@app.exception_handler(Conflict)
async def conflict(_, exc: Conflict):
    return JSONResponse({"detail": str(exc)}, status_code=409)
DATA = Path(__file__).resolve().parent.parent / "data"


def S() -> Session:
    sid = CURRENT.get()
    if sid not in SESSIONS:
        SESSIONS[sid] = Session("capture")
        while len(SESSIONS) > MAX_SESSIONS: SESSIONS.popitem(last=False)
    SESSIONS.move_to_end(sid)
    return SESSIONS[sid]


class NewSession(BaseModel): mode: Literal["capture", "teach"] = "capture"
Interp = Literal["none", "behavioural_agitation", "physical_cause_suspected", "environmental", "unmet_basic_need", "sundowning", "boredom", "unknown"]
Check = Literal["pain", "footwear_skin", "hunger_thirst", "hearing_vision_aids", "noise_environment", "toileting", "signage_routine"]
Interv = Literal["no_action", "retry_later_same_carer", "swap_carer_or_call_psychologist", "reassure_and_note", "give_prn_medication", "request_antipsychotic", "adjust_diet", "integration_plan_review", "prompted_toileting", "restore_signage", "add_activities"]
Escal = Literal["none", "nurse", "psychologist", "team_meeting", "coordinating_physician"]


class FormIn(BaseModel):
    """The sandbox form, validated at the boundary (the guard evaluates this exact shape)."""
    model_config = ConfigDict(extra="forbid")
    incident_type: Literal["refusal_of_care", "exit_seeking", "wandering", "medication_request", "other"] = "refusal_of_care"
    observation: str = Field("", max_length=1000)
    interpretation: Interp = "none"
    checks: list[Check] = Field(default_factory=list, max_length=7)
    occurrences_today: int = Field(1, ge=0, le=50)
    months_in_residence: int = Field(0, ge=0, le=600)
    pattern: Literal["", "new", "habitual", "unsure"] = ""
    intervention: Interv = "no_action"
    escalate_to: Escal = "none"


class Ev(BaseModel):
    field: Literal["incident_type", "observation", "interpretation", "checks", "occurrences_today", "months_in_residence", "pattern", "intervention", "escalate_to", "save"]
    value: object = None; delta: dict | None = None; form: FormIn | None = None
class Frame(BaseModel): event_id: str = Field(max_length=40); data_url: str = Field(max_length=2_000_000, pattern=r"^data:image/(jpeg|png);base64,")
class Ask(BaseModel): event_id: str; signals: dict
class Ans(BaseModel): question_id: str = Field(max_length=40); text: str = Field(min_length=1, max_length=2000); ts: float | None = None
class Conf(BaseModel): rule_id: str = Field(max_length=60); ok: bool; correction: str | None = Field(default=None, max_length=1000)
class Off(BaseModel): since_ts: float = Field(ge=0)
class Pred(BaseModel): case_id: str = Field(max_length=10); option: Literal["a", "b", "c", "d"]
class Save(BaseModel): case_id: str = Field(max_length=10); form: FormIn
class GuardCheck(BaseModel): form: FormIn
class Rec(BaseModel): on: bool
class Rev(BaseModel): rule_id: str = Field(max_length=60); decision: Literal["confirm", "reject", "reset"]; note: str = Field("", max_length=600)


@app.get("/health")
def health(): return {"ok": True, "session": S().id, "degraded": S().degraded, "usage": llm.usage_summary()}

@app.post("/session")
def new(b: NewSession):
    if b.mode == "teach":   # keep what the apprentice learned in capture
        old = S(); s = Session("teach")
        s.rules, s.teachback, s.frames, s.drift_log = copy.deepcopy(old.rules), dict(old.teachback), dict(old.frames), copy.deepcopy(old.drift_log)
        s.events, s.questions, s.answers = copy.deepcopy(old.events), copy.deepcopy(old.questions), copy.deepcopy(old.answers)
        s.freeze()
        SESSIONS[CURRENT.get()] = s
    else:
        SESSIONS[CURRENT.get()] = Session(b.mode)
    return {"id": S().id, "mode": S().mode, "capture_scenario": S().cases["capture_scenario"]}

@app.get("/cases")
def cases(): return {"capture": S().cases["capture_scenario"], "teach": [{"id": c["id"], "title": c["title"], "resident": c["resident"]} for c in S().cases["teach_cases"]]}

@app.post("/events")
def events(e: Ev):
    r = S().add_event(e.model_dump())
    if e.field == "observation":   # fail-closed clinical language check, visible to the expert while they type
        r["scope"] = scope.classify(str(e.value or ""))
    return r

@app.post("/recording")
def recording(r: Rec): return S().set_recording(r.on)

@app.post("/frames")
def frames(f: Frame): return {"ok": True, "vision": S().add_frame(f.event_id, f.data_url)}

@app.get("/frame/{event_id}")
def frame(event_id: str):
    d = S().frames.get(event_id)
    if not d: raise HTTPException(404, "no frame")
    head, _, b64 = d.partition(",")
    return Response(base64.b64decode(b64), media_type="image/jpeg")

@app.post("/question")
def question(a: Ask): return S().propose_question(a.event_id, a.signals)

@app.post("/answer")
def answer(a: Ans):
    try: return S().answer(a.question_id, a.text, a.ts)
    except StopIteration: raise HTTPException(404, "unknown question")

@app.post("/debrief/start")
def debrief_start(): return S().debrief_start()
@app.get("/debrief/status")
def debrief_status(): return S().debrief_status()
@app.get("/debrief/teachback")
def teachback(): return S().teachback_text()
@app.post("/debrief/confirm")
def confirm(c: Conf): return S().confirm(c.rule_id, c.ok, c.correction)

@app.get("/workmap")
def workmap(): return S().workmap()
@app.post("/off-record")
def off(o: Off): return S().off_record(o.since_ts)

@app.post("/teach/open")
def t_open(b: dict):
    try: return S().teach_open(b["case_id"])
    except (StopIteration, KeyError): raise HTTPException(404, "unknown case")
@app.post("/teach/predict")
def t_pred(p: Pred):
    try: return S().teach_predict(p.case_id, p.option)
    except StopIteration: raise HTTPException(404, "unknown case")
@app.post("/teach/check-save")
def t_save(p: Save):
    try: return S().teach_check_save(p.case_id, p.form.model_dump())
    except StopIteration: raise HTTPException(404, "unknown case")
@app.get("/mastery")
def mastery(): return {"rows": S().mastery.summary()}

@app.post("/guard/check")
def guard_check(p: GuardCheck):
    """Score a record against the live work map without consuming a teach case or changing mastery."""
    from . import guard
    form = p.form.model_dump()
    fired = guard.check_form(S().rules, form)
    blocked, warnings = [], []
    for f in fired:
        if f["severity"] == "block":
            try:
                ex = S()._explain(S().rule(f["rule_id"]))
            except StopIteration:
                ex = {"expert_words": [], "dataset_summaries": []}
            blocked.append({**f, "explain": ex, "guardrail_id": f["rule_id"]})
        else:
            warnings.append({"rule_id": f["rule_id"], "message": f["message"], "trace": f["trace"]})
    sc = scope.classify(form.get("observation", ""))
    if sc["escalate"] and form.get("escalate_to") not in scope.HUMAN_ROUTES:
        blocked.append({"rule_id": "SCOPE-clinical-escalation", "guardrail_id": "SCOPE-clinical-escalation", "title": "A clinical concern must reach a qualified human",
                        "severity": "block", "message": sc["reason"], "trace": [], "explain": {"expert_words": [], "dataset_summaries": []}})
    return {"saved": not blocked, "blocked": blocked, "warnings": warnings}

@app.post("/teach/brief")
def t_brief(b: dict):
    """Expert-reasoning brief for the tutor agent (pushed as a contextual update)."""
    c = next(x for x in S().cases["teach_cases"] if x["id"] == b["case_id"])
    lines = []
    for rid in c["rule_ids"]:
        ex = S()._explain(S().rule(rid))
        quotes = " | ".join(f"\"{w['quote']}\"" for w in ex["expert_words"])
        notes = " | ".join(d["summary"] for d in ex["dataset_summaries"])
        lines.append(f"Rule {rid}: {ex['title']}. Verbatim expert words: {quotes or 'none'}." + (f" Dataset summary (not a quotation): {notes}." if notes else ""))
    return {"brief": "\n".join(lines)}

@app.post("/review")
def review(r: Rev): return S().review(r.rule_id, r.decision, r.note)

@app.get("/graph")
def graph(): return care_graph.build(S())

@app.get("/export")
def export_guardrails():
    return Response(exporter.build(S().rules, S().teachback, S().map_version()), media_type="text/markdown; charset=utf-8",
                    headers={"content-disposition": 'attachment; filename="apprentice-guardrails.md"'})

@app.get("/learned")
def learned_map():
    """Everything the apprentice learned from all interview units, with each card's review state."""
    d = learned.load(); revs = S().reviews
    rep_path = DATA / "learner_report.json"
    def rv(c):   # a review counts only if it still points at the same card title
        r = revs.get(c["id"], {})
        return r if r and (not r.get("title") or r["title"] == c["title"]) else {}
    return {"cards": [{**c, "review": rv(c).get("decision", ""), "review_note": rv(c).get("note", ""),
                       "enforced": rv(c).get("decision") == "confirm" and bool(c.get("proposed_predicate"))} for c in d["cards"]],
            "agenda": d.get("agenda", []), "report": json.loads(rep_path.read_text()) if rep_path.exists() else {}}

@app.get("/rules")
def rules(): return {"rules": S().rules}
@app.get("/usage")
def usage(): return llm.usage_summary()
if "dp" in os.getenv("APPRENTICE_EXTENSIONS", ""):   # deferred extension: never part of the live path
    from . import federated

    @app.get("/dp-sweep")
    def dp(): return {"note": "Simulation only; org = privacy unit", "sweep": federated.sweep()}
@app.get("/eval")
def ev():
    p = DATA / "eval_results.json"
    return json.loads(p.read_text()) if p.exists() else {"error": "run python -m engine.eval_heldout"}

@app.get("/voice/session")
def voice_session(role: str = "interviewer"):
    """Short-lived ElevenLabs agent URL. The API key stays on the server."""
    import httpx
    chosen = "tutor" if role == "tutor" else "interviewer"
    agent = os.getenv("ELEVENLABS_TUTOR_AGENT_ID" if chosen == "tutor" else "ELEVENLABS_INTERVIEWER_AGENT_ID", "")
    key = os.getenv("ELEVENLABS_API_KEY", "")
    if not agent or not key:
        raise HTTPException(503, "voice not configured")
    try:
        res = httpx.get(
            "https://api.elevenlabs.io/v1/convai/conversation/get-signed-url",
            params={"agent_id": agent},
            headers={"xi-api-key": key},
            timeout=8,
        )
    except httpx.HTTPError:
        raise HTTPException(504, "voice provider unreachable")
    if res.status_code >= 300:
        raise HTTPException(502, "voice provider error")
    url = (res.json() or {}).get("signed_url")
    if not url:
        raise HTTPException(502, "voice provider error")
    return {"role": chosen, "signed_url": url}

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
if FRONTEND.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
