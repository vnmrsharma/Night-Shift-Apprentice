"""Seed corpus loader: the whole de-identified dataset as typed units with literal provenance.
Used for retrieval, evaluation and corroboration; only the curated rules in care_map/ are executable guardrails."""
import json
from dataclasses import dataclass, field
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data" / "dementia_care_knowledge_deidentified.json"
EXTRACTION_VERSION = "ingest-1"


@dataclass(frozen=True)
class Span:
    session: str
    unit_id: str
    turn_ids: tuple
    start: str
    end: str
    speaker: str
    text: str            # literal turn text (English translation where the source was French)


@dataclass
class Unit:
    unit_id: str
    session: str
    domain: str
    subtopic: str
    source_type: str
    priority: str
    answer: str
    rationale: str
    branches: list[str]          # each practice rule stays a separate conditional branch
    caveat: str                  # evidence caveat, NOT an exception
    spans: list[Span] = field(default_factory=list)
    gold_eligible: bool = True   # interviewer hypotheses are never clinical gold


def load_units(path: Path = DATA) -> list[Unit]:
    d = json.loads(Path(path).read_text())
    units = []
    for s in d["sessions"]:
        turns = {t["turn_id"]: t for t in s["dialogue"]}
        for u in s["knowledge_units"]:
            spans = [Span(s["session_id"], u["unit_id"], (i,), turns[i]["start"], turns[i]["end"], turns[i]["speaker"],
                          turns[i]["english_translation"] or turns[i]["text"])
                     for i in u.get("dialogue_turn_ids", []) if i in turns]
            units.append(Unit(u["unit_id"], s["session_id"], u["domain"], u["subtopic"], u["source_type"], u.get("apprentice_priority", ""),
                              u.get("expert_answer", ""), u.get("clinical_rationale_why", ""), list(u.get("practice_rules", [])),
                              u.get("cautions_or_corrections", ""), spans, gold_eligible="hypothesis" not in u["source_type"].lower()))
    return units


if __name__ == "__main__":
    us = load_units()
    print(len(us), "units;", sum(u.gold_eligible for u in us), "gold-eligible;", sum(bool(u.branches) for u in us), "with rule branches")
