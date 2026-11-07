"""Does the report match the test the patient chose? Deterministic, from the report's own text.

  match     the chosen test's values or words are in the report
  mismatch  they are not, AND the report clearly shows other tests (so it is not just unreadable)
  unknown   too little to say (unreadable, handwritten, no text, or "Other") - a person decides, as always

A mismatch never deletes anything: at upload the patient is asked to choose again (or send it anyway), and the
clinical team always sees it flagged for a manual check.
"""

import re
from dataclasses import dataclass, field

from . import parse

# The chosen test (report_tests key) -> values that belong to it, and words that name it on a report.
BELONGS = {
    "hba1c": ({"hba1c"}, r"\b(hb\s*a1c|a1c|glyc(at|osyl)ated)"),
    "fpg": ({"fpg"}, r"\bfasting\b|\bfbs\b|\bfpg\b"),
    "ppbs": ({"ppbs"}, r"post\s*-?\s*prandial|\bppbs\b|\bpp\b"),
    "ogtt": ({"ogtt", "fpg", "ppbs"}, r"\bogtt\b|tolerance"),
    "kft": ({"creatinine", "egfr", "uacr"}, r"\b(kidney|renal|kft|rft|creatinine|e\s*gfr|urea|bun|microalbumin)\b"),
    "lipid": ({"total_cholesterol", "ldl", "hdl", "vldl", "triglycerides"}, r"\b(lipid|cholesterol|triglycerides?)\b"),
    "urine_ketone": ({"urine_ketone"}, r"\bketones?\b"),
    "eye_foot": (set(), r"\b(retina\w*|fundus|fundoscop\w*|ophthalm\w*|eye\s+exam\w*|monofilament|foot\s+exam\w*|"
                        r"neuropath\w*|vibration|dorsalis)\b"),
}
# What a found value says the report is (the short names the team and patient know).
SHOWS = {"hba1c": "HbA1c", "fpg": "FPG", "ppbs": "PPBS", "rbs": "RBS", "ogtt": "OGTT", "creatinine": "KFT", "egfr": "KFT",
         "uacr": "KFT", "total_cholesterol": "Lipid", "ldl": "Lipid", "hdl": "Lipid", "vldl": "Lipid", "triglycerides": "Lipid",
         "urine_ketone": "Urine ketone", "hemoglobin": "Hemoglobin"}


@dataclass
class Match:
    result: str                                   # match | mismatch | unknown
    found: list[str] = field(default_factory=list)


def check(expected: str | None, document_kind: str, texts: list[str]) -> Match:
    text = "\n".join(texts)
    if not text.strip() or parse.readable_share(text) < parse.rules()["quality"]["min_readable_word_share"]:
        return Match("unknown")
    found = {f.analyte for p, t in enumerate(texts, 1) for f in parse.observations(t, p) if f.analyte}
    shows = sorted({SHOWS[a] for a in found if a in SHOWS})
    if document_kind == "prescription":
        medicines = any(parse.medications(t, 1) for t in texts)
        return Match("mismatch", shows) if len(found) >= 2 and not medicines else Match("match" if medicines else "unknown")
    if expected not in BELONGS:
        return Match("unknown")                   # "Other": nothing to compare with
    values, words = BELONGS[expected]
    if found & values or re.search(words, text, re.I):
        return Match("match")
    if not shows and any(parse.medications(t, 1) for t in texts):
        shows = ["Prescription"]
    return Match("mismatch", shows) if shows else Match("unknown")
