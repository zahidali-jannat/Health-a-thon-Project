"""Read structured values out of a report's text - deterministic, line by line.

Every value comes from a line of the document's own text: the line is kept as `source_text`, so a person can always
see where it came from. Nothing is inferred: a missing unit stays missing, an unreadable value is not produced, a date
is only what is printed. Names, units and ranges come from config/extraction_rules.json.
"""

import html
import json
import re
from dataclasses import dataclass, field
from datetime import date
from functools import cache

from ..settings import BACKEND_DIR

MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}


@cache
def rules() -> dict:
    data = json.loads((BACKEND_DIR / "config" / "extraction_rules.json").read_text(encoding="utf-8"))
    data.pop("_comment", None)
    for a in data["analytes"]:
        a["_names"] = [re.compile(n, re.I) for n in a["names"]]
        a["_exclude"] = [e.lower() for e in a.get("exclude", [])]
    spell = []
    for unit, forms in data["unit_spellings"].items():
        spell += [(re.compile(rf"^\s*({f})(?![a-z])", re.I), unit) for f in forms]
    data["_units"] = spell
    data["_qual"] = re.compile(r"\b(" + "|".join(data["qualitative_values"]) + r")(?![\w])", re.I) \
        if data["qualitative_values"] else None
    return data


def analyte(key: str) -> dict | None:
    return next((a for a in rules()["analytes"] if a["key"] == key), None)


@dataclass
class Found:
    kind: str                       # observation | medication | allergy
    name: str                       # as printed
    source_text: str
    page_no: int
    analyte: str | None = None
    value_text: str | None = None
    value_num: float | None = None
    unit: str | None = None         # standard spelling of the printed unit; None when none printed
    reference_range: str | None = None
    flags: list[str] = field(default_factory=list)


@dataclass
class DateRead:
    value: date | None
    text: str
    ambiguous: bool = False


NUMBER = re.compile(r"(?<![\w.])([<>]=?\s*)?(\d+(?:[.,]\d+)?)(?![\w.]*\d)")
RANGE = re.compile(r"(\d+(?:\.\d+)?\s*(?:-|–|to)\s*\d+(?:\.\d+)?|[<>≤≥]=?\s*\d+(?:\.\d+)?)", re.I)
PARENS = re.compile(r"\([^)]*\)|\[[^\]]*\]")
# Words that mean the number after them is a guide, not this patient's result ("Normal < 5.7", "Desirable < 200").
GUIDE = re.compile(r"\b(normal|non[- ]?diabetic|pre[- ]?diabet\w*|diabet\w*|desirable|borderline|optimal|near optimal|"
                   r"risk|reference|ref\.?|target|goal|interpretation|expected|range|cut[- ]?off)\b", re.I)


BLOCK_END = re.compile(r"</(tr|p|li|div|h[1-6]|table|thead|tbody)>|<br\s*/?>", re.I)
CELL_END = re.compile(r"</t[dh]>", re.I)


def normalize_text(text: str) -> str:
    """OCR output to plain lines: an HTML table becomes one line per row with " | " between cells (Sarvam returns
    tables as HTML), entities are decoded (&lt; 5.7 -> < 5.7)."""
    text = CELL_END.sub(" | ", text or "")
    text = BLOCK_END.sub("\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return html.unescape(text)


def clean_line(line: str) -> str:
    """Table borders and repeated spaces out - a row reads the same whether it came as text, markdown or HTML."""
    line = re.sub(r"<[^>]+>", " ", line)
    line = line.replace("|", " ").replace("\t", " ").replace(" ", " ")
    return re.sub(r"\s{2,}", " ", line).strip()


def _unit_at(text: str) -> tuple[str | None, int]:
    for rx, unit in rules()["_units"]:
        m = rx.match(text)
        if m:
            return unit, m.end()
    return None, 0


def observations(text: str, page_no: int) -> list[Found]:
    out = []
    for raw in text.splitlines():
        line = clean_line(raw)
        if len(line) < 3:
            continue
        low = line.lower()
        for a in rules()["analytes"]:
            m = next((m for rx in a["_names"] if (m := rx.search(line))), None)
            if not m or any(x in low for x in a["_exclude"]):
                continue
            if GUIDE.search(line[:m.start()]):
                break                                     # "Desirable: Total cholesterol < 200" is a guide line
            rest = PARENS.sub(" ", line[m.end():])
            if a.get("qualitative"):
                q = rules()["_qual"].search(rest) if rules()["_qual"] else None
                if q and not GUIDE.search(rest[:q.start()]):
                    out.append(Found("observation", m.group(0), line, page_no, a["key"], value_text=q.group(1).strip()))
                break
            n = NUMBER.search(rest)
            if not n or GUIDE.search(rest[:n.start()]):
                break                                     # a heading, or a guide line: nothing to suggest
            raw_value = n.group(2).replace(",", ".")
            comparator = (n.group(1) or "").strip()
            after = rest[n.end():]
            unit, used = _unit_at(after)
            ref = RANGE.search(after[used:])
            f = Found("observation", m.group(0), line, page_no, a["key"], value_text=f"{comparator}{raw_value}",
                      value_num=None if comparator else float(raw_value), unit=unit,
                      reference_range=ref.group(1).strip() if ref else None)
            if comparator:
                f.flags.append("comparator")              # "<5" is not a number to graph
            out.append(f)
            break                                         # one analyte per line
    return out


MED = re.compile(r"^\s*(?:\d+[.)]\s*)?(tab|tablet|cap|capsule|inj|injection|syp|syrup)\.?\s+(.+)$", re.I)
DOSE = re.compile(r"(\d+(?:\.\d+)?)\s*(mg|mcg|g|ml|iu|units?)\b", re.I)
SCHEDULE = re.compile(r"\b\d\s*-\s*\d\s*-\s*\d\b|\b(od|bd|tds|tid|qid|hs|sos|daily|once|twice|x\s*\d+|for\s+\d+)\b|[,;(]", re.I)


def medications(text: str, page_no: int) -> list[Found]:
    """Printed prescription lines: "Tab Metformin 500 mg 1-0-1". The dose only when it is printed - never assumed."""
    out = []
    for raw in text.splitlines():
        line = clean_line(raw)
        m = MED.match(line)
        if not m:
            continue
        rest = m.group(2)
        dose = DOSE.search(rest)
        stop = min([x.start() for x in (dose, SCHEDULE.search(rest)) if x] or [len(rest)])
        name = rest[:stop].strip(" .-")
        if len(name) < 2 or not re.match(r"[A-Za-z]", name):
            continue
        out.append(Found("medication", f"{m.group(1).title()} {name}", line, page_no,
                         value_text=f"{dose.group(1)}{dose.group(2).lower()}" if dose else None))
    return out


ALLERGY = re.compile(r"\ballerg(?:y|ies|ic)\b\s*(?:to)?\s*[:\-]?\s*(.*)$", re.I)


def allergies(text: str, page_no: int) -> list[Found]:
    out = []
    for raw in text.splitlines():
        line = clean_line(raw)
        if re.search(r"\b(nkda|no known (drug )?allerg)", line, re.I):
            out.append(Found("allergy", "Allergies", line, page_no, value_text="None known"))
            continue
        m = ALLERGY.search(line)
        if m and len(m.group(1).strip(" .:-")) >= 3:
            out.append(Found("allergy", "Allergies", line, page_no, value_text=m.group(1).strip(" .:-")))
    return out


DMY = re.compile(r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})\b")
YMD = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
DMONY = re.compile(r"\b(\d{1,2})[\s\-]*([a-z]{3})[a-z]*[\s,\-]*(\d{2,4})\b", re.I)
DATE_LABELS = (("collect", 0), ("sample", 0), ("drawn", 0), ("report", 1), ("receiv", 1), ("regist", 2), ("date", 2))


def _year(y: str) -> int:
    n = int(y)
    return n + 2000 if n < 100 else n


def _date_in(line: str) -> DateRead | None:
    for rx, order in ((YMD, "ymd"), (DMONY, "dmony"), (DMY, "dmy")):
        m = rx.search(line)
        if not m:
            continue
        try:
            if order == "ymd":
                return DateRead(date(int(m.group(1)), int(m.group(2)), int(m.group(3))), m.group(0))
            if order == "dmony":
                mon = MONTHS.get(m.group(2).lower()[:3])
                if mon:
                    return DateRead(date(_year(m.group(3)), mon, int(m.group(1))), m.group(0))
                continue
            d, mth, y = int(m.group(1)), int(m.group(2)), _year(m.group(3))
            # Indian reports print day first. When both could be a month, say so instead of choosing silently.
            return DateRead(date(y, mth, d), m.group(0), ambiguous=d <= 12 and mth <= 12 and d != mth)
        except ValueError:
            return DateRead(None, m.group(0))
    return None


def report_date(pages: list[str]) -> DateRead | None:
    """The test date as printed: a collection / sample date first, then the report date, then any labelled date.
    Dates of birth are never taken."""
    best = None
    for text in pages:
        for raw in text.splitlines():
            line = clean_line(raw)
            low = line.lower()
            if re.search(r"\b(dob|birth|born|age)\b", low):
                continue
            rank = next((r for word, r in DATE_LABELS if word in low), None)
            if rank is None:
                continue
            d = _date_in(line)
            if d and (best is None or rank < best[0]):
                best = (rank, d)
    return best[1] if best else None


LAB_WORDS = re.compile(r"\b(lab|labs|laborator\w*|diagnostic\w*|patholog\w*|hospital|clinic|healthcare|imaging)\b", re.I)


def lab_name(first_page: str) -> str | None:
    for raw in first_page.splitlines()[:8]:
        line = clean_line(raw)
        if 3 <= len(line) <= 80 and LAB_WORDS.search(line) and not re.search(r"\d{3,}", line):
            return line
    return None


READABLE = re.compile(r"\(?[A-Za-z][A-Za-z0-9\-']{2,}[.,:;)]?|[<>≤≥]?=?\d+(?:[.,:/\-]\d+)*%?[.,;)]?|[A-Za-z]+/[A-Za-z0-9.²]+")


def readable_share(text: str) -> float:
    """Share of tokens that look like real words, numbers, dates or units - table borders and punctuation not counted.
    A printed report scores high; handwriting read by OCR comes out as fragments and scores low."""
    tokens = [t for line in text.splitlines() for t in clean_line(line).split() if re.search(r"[A-Za-z0-9]", t)]
    if not tokens:
        return 0.0
    return sum(1 for t in tokens if READABLE.fullmatch(t)) / len(tokens)
