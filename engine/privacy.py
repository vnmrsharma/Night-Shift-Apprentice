"""Prospective privacy: regex redaction of free text + off-the-record tombstoning. No over-claims:
already transmitted provider data follows provider retention; fake data only in the demo."""
import re

PATTERNS = [
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"), "[EMAIL]"),
    (re.compile(r"\b(?:\+?\d[\s-]?){8,14}\d\b"), "[PHONE]"),
    (re.compile(r"\b(?:19|20)\d{2}[-/]\d{1,2}[-/]\d{1,2}\b|\b\d{1,2}[-/]\d{1,2}[-/](?:19|20)\d{2}\b"), "[DATE]"),
    (re.compile(r"\b(?:Mr|Mrs|Ms|Miss|Dr|Sister|Nurse)\.?\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?"), "[NAME]"),
    (re.compile(r"\b(?:resident|patient|lady|gentleman)\s+(?!R-\d)[A-Z][a-z]{2,}\b"), "resident [NAME]"),
]


def redact(text: str) -> tuple[str, int]:
    n = 0
    for pat, rep in PATTERNS:
        text, k = pat.subn(rep, text)
        n += k
    return text, n
