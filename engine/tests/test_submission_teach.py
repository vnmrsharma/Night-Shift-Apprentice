"""The Night Shift record choices must be judged by the guard, not by a label on the button."""
import json
import os
from pathlib import Path

os.environ.setdefault("APPRENTICE_OFFLINE", "1")
os.environ.setdefault("APPRENTICE_COOLDOWN", "0")

from engine.session import Session

FORMS = json.loads((Path(__file__).resolve().parents[2] / "frontend" / "teach_forms.json").read_text())
EXPECT = {
    "T1": {"R1-somatic-first", "R3-repeat-escalate"},
    "T2": {"R6-exit-seeking"},
    "T3": {"R4-treatment-routing"},
    "T4": {"R7-wandering-basic-needs", "R4-treatment-routing"},
}


def test_each_sealed_case_blocks_the_unsafe_record_and_saves_the_safe_one():
    assert set(FORMS) == set(EXPECT)
    for cid, spec in FORMS.items():
        s = Session("teach")
        s.teach_predict(cid, "a")
        bad = s.teach_check_save(cid, spec["bad"]["form"])
        assert not bad["saved"], cid
        assert EXPECT[cid] <= {b["rule_id"] for b in bad["blocked"]}, (cid, bad["blocked"])
        ex = bad["blocked"][0]["explain"]
        assert ex["expert_words"] or ex["dataset_summaries"]
        good = s.teach_check_save(cid, spec["good"]["form"])
        assert good["saved"], (cid, [(b["rule_id"], b["message"]) for b in good["blocked"]])


def test_capture_debrief_map_teach_and_night_check_use_the_engine():
    from fastapi.testclient import TestClient
    from engine.api import app
    night = json.loads((Path(__file__).resolve().parents[2] / "frontend" / "real_life.json").read_text())
    c = TestClient(app)
    h = {"x-session-id": "night-shift-live"}
    opened = c.post("/session", json={"mode": "capture"}, headers=h).json()
    assert opened["capture_scenario"]["id"] == "CAP-WANDERING"
    ev = c.post("/events", json={"field": "incident_type", "value": "wandering", "form": {"incident_type": "wandering", "months_in_residence": 24, "occurrences_today": 1}}, headers=h).json()
    q = c.post("/question", json={"event_id": ev["event"]["id"], "signals": {"voice_silent": True, "hands_still": True, "screen_stable": True}}, headers=h).json()["question"]
    assert q and q["text"]
    c.post("/answer", json={"question_id": q["id"], "text": "I check the body and the basic needs before I name a cause."}, headers=h)
    deb = c.post("/debrief/start", json={}, headers=h)
    assert deb.status_code == 200 and deb.json()["questions"]
    wm = c.get("/workmap", headers=h).json()
    assert wm["steps"] and wm["steps"][0]["rule_id"]
    assert c.post("/session", json={"mode": "teach"}, headers=h).status_code == 200
    bad = night["moments"][0]["choices"][0]["form"]
    good = night["moments"][0]["choices"][1]["form"]
    blocked = c.post("/guard/check", json={"form": bad}, headers=h).json()
    assert not blocked["saved"] and blocked["blocked"]
    assert c.post("/guard/check", json={"form": good}, headers=h).json()["saved"]


def test_demo_page_is_served_with_the_engine():
    from fastapi.testclient import TestClient
    from engine.api import app
    c = TestClient(app)
    page = c.get("/")
    assert page.status_code == 200
    assert "Night Shift Apprentice" in page.text
    assert "live.js" in page.text
    assert c.get("/live.js").status_code == 200
    assert c.get("/teach_forms.json").json()["T2"]["good"]["form"]["intervention"] == "integration_plan_review"
