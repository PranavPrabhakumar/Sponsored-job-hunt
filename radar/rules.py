"""Rule checks on a listing's text: sponsorship wording, clearance, seniority, experience, salary, contract."""
from __future__ import annotations

import re
from datetime import date

from .util import fold, parse_date

_NEG = [
    r"(?:unable|not able|cannot|can ?not|can't|won't|will not|do not|don't|does not|doesn't|are not able|is not able|not in a position)\s+(?:to\s+)?(?:currently\s+)?(?:offer|provide|support|consider|accept|give)?\s*(?:any\s+)?(?:visa\s+|work\s+|skilled worker\s+|tier 2\s+)?(?:sponsorship|sponsor)",
    r"\bno\s+(?:visa\s+|work\s+|skilled worker\s+)?sponsorship\b",
    r"sponsorship\s+(?:is\s+|will\s+|can\s+)?(?:not|n't)\s+(?:be\s+)?(?:available|offered|provided|possible|considered|an option)",
    r"(?<!or )without\s+(?:the\s+)?(?:need|requirement|requiring)\s+(?:for\s+|of\s+)?(?:any\s+)?(?:visa\s+|work\s+|current or future\s+)?sponsorship",
    r"(?<!or )without\s+(?:visa\s+)?sponsorship",
    r"(?:not|n't|never)\s+(?:currently\s+)?(?:require|need)\s+(?:visa\s+|work\s+|current or future\s+|any\s+)?sponsorship",
    r"(?:not|n't)\s+(?:able|eligible)\s+to\s+sponsor",
    r"unrestricted\s+(?:right to work|work rights|right to live and work|uk work)",
    r"right to (?:live and )?work in the uk without",
    r"full (?:and unrestricted )?right to (?:live and )?work",
    r"indefinite leave to remain\s+(?:is\s+)?(?:required|essential)",
    r"(?:british|uk)\s+(?:citizens?|nationals?)\s+only",
    r"settled (?:or pre-settled )?status\s+(?:is\s+)?(?:required|essential)",
]
_POS = [
    r"visa sponsorship\s+(?:is\s+|will be\s+|can be\s+|may be\s+)?(?:available|offered|provided|considered|on offer)",
    r"sponsorship\s+(?:is\s+|will be\s+|can be\s+|may be\s+)?(?:available|offered|provided|considered)",
    r"(?:we|the company|the bank|the firm|our client)\s+(?:can|could|will|are able to|is able to|do|does)\s+(?:offer\s+|provide\s+)?(?:visa\s+)?sponsor",
    r"skilled worker\s+(?:visa\s+)?sponsorship",
    r"open to (?:candidates|applicants|applications|people)[^.]{0,50}(?:sponsorship|visa)",
    r"certificate of sponsorship",
    r"(?:offer|provide|support)s?\s+(?:skilled worker\s+|tier 2\s+|uk\s+)?visa sponsorship",
    r"sponsor\s+(?:your|a|the)\s+(?:skilled worker\s+|work\s+)?visa",
    r"visa sponsorship\s*(?::|-)\s*(?:yes|available)",
]
_CLEARANCE = [
    r"\bsc[- ]?clear(?:ed|ance)\b", r"\bdv[- ]?clear(?:ed|ance)\b", r"security clearance", r"developed vetting",
    r"national security vetting", r"\bnppv\d?\b", r"eligible (?:for|to obtain|to gain) (?:sc|dv|security)",
    r"\bctc[- ]?clear", r"\bsc\s+(?:level\s+)?(?:clearance|vetting)",
]
_RESIDENCY = [
    r"(?:5|five|3|three|10|ten)\s+years?'?\s+(?:continuous\s+)?(?:uk\s+)?residen", r"resided in the uk for (?:at least |the )?(?:last |past )?(?:5|five|3|three|10|ten)",
    r"lived in the uk for (?:at least |the )?(?:last |past )?(?:5|five|3|three|10|ten)",
]
_PHD = [r"(?:phd|doctorate)\s+(?:is\s+)?(?:required|essential|mandatory)", r"must (?:have|hold) a (?:phd|doctorate)"]
_AGENCY_WORDS = re.compile(r"\b(?:recruitment|recruiting|recruiters?|resourcing|staffing|personnel|headhunt\w*|search and selection|executive search|talent acquisition partners?)\b", re.I)
_KNOWN_AGENCIES = {
    "hays", "michael page", "page personnel", "robert half", "robert walters", "harnham", "randstad", "adecco",
    "reed", "reed specialist recruitment", "manpower", "kforce", "spencer ogden", "hamilton barnes", "nigel frank",
    "jefferson frank", "understanding recruitment", "venturi", "oscar technology", "la fosse", "investigo",
    "goodman masson", "morgan mckinley", "huxley", "sanderson", "computer futures", "progressive recruitment",
    "salt", "searchability", "nicholson glover", "pontoon", "tiro partners", "intec select", "datatech analytics",
    "eden smith", "client server", "opus recruitment solutions", "lorien", "hydrogen group", "cpl", "green park",
    # job boards that post other employers' adverts
    "efinancialcareers", "totaljobs", "cv library", "cwjobs", "jobsite", "indeed", "glassdoor", "linkedin",
    "guardian jobs", "jobs ac uk", "monster", "adzuna", "gumtree", "jobserve", "nhs jobs", "milkround", "prospects",
    "bright network", "gradcracker", "targetjobs", "civil service jobs",
}
_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
_YEARS = re.compile(
    r"(?:(?:minimum|min\.?|at least|over|more than|a minimum of)\s+(?:of\s+)?)?"
    r"\b(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)\s*(?:\+|plus)?\s*"
    r"(?:(?:-|–|to)\s*(?:\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)\s*\+?\s*)?"
    r"years?(?:'|’)?\s+(?:of\s+)?(?:[a-z/&,\-\s]{0,40}?)?(?:experience|exp\b)", re.I)
_SOFT = re.compile(r"desirable|preferred|nice to have|bonus|advantage|ideally|beneficial", re.I)
_JUNIOR = re.compile(r"\b(?:junior|graduate|grad|entry[- ]level|trainee|associate|assistant|apprentice)\b", re.I)


def _find(patterns, text):
    for p in patterns:
        m = re.search(p, text, re.I)
        if m:
            return m.group(0)
    return None


def sponsorship_negative(text):
    return _find(_NEG, text)


def sponsorship_positive(text):
    if sponsorship_negative(text):
        return None
    return _find(_POS, text)


def needs_clearance(text):
    return _find(_CLEARANCE, text)


def residency_requirement(text):
    return _find(_RESIDENCY, text)


def is_agency(company):
    name = fold(company).strip()
    name_clean = re.sub(r"\b(ltd|limited|plc|llp|uk|group|the)\b", " ", name)
    name_clean = re.sub(r"[^a-z0-9 ]+", " ", name_clean)
    name_clean = re.sub(r"\s+", " ", name_clean).strip()
    return bool(_AGENCY_WORDS.search(name)) or name_clean in _KNOWN_AGENCIES


def employer_in_title(title):
    """Job boards often put the real employer after a dash: 'Risk Analyst - Border to Coast Pensions'."""
    parts = re.split(r"\s+[-–|]\s+|\s+@\s+", str(title or ""))
    if len(parts) < 2:
        return None
    cand = parts[-1].strip()
    words = cand.split()
    if not 1 <= len(words) <= 8 or not cand[:1].isupper():
        return None
    if re.search(r"(?i)\b(remote|hybrid|london|uk|united kingdom|england|scotland|wales|permanent|contract|full[- ]time|part[- ]time|graduate|junior|senior|£|\d)", cand):
        return None
    return cand


def years_required(text):
    """Largest 'N years experience' requirement that isn't marked as desirable."""
    best = None
    for m in _YEARS.finditer(text):
        n = m.group(1).lower()
        n = _NUM.get(n, None) if not n.isdigit() else int(n)
        if n is None or n > 20:
            continue
        tail = text[m.end(): m.end() + 70]
        sentence_tail = re.split(r"[.;\n]", tail, maxsplit=1)[0]
        if _SOFT.search(sentence_tail) or _SOFT.search(text[max(0, m.start() - 40): m.start()]):
            continue
        best = n if best is None else max(best, n)
    return best


def title_excluded(title, words):
    t = " " + re.sub(r"[^a-z0-9+ ]+", " ", fold(title)) + " "
    for w in words or []:
        w = re.sub(r"[^a-z0-9+ ]+", " ", fold(w)).strip()
        if w and f" {w} " in t:
            return w
    return None


def short_contract(text):
    m = re.search(r"\b([1-9]|1[01])[- ]?(?:months?|mths?)\b(?:[^.]{0,30})?(?:contract|fixed[- ]term|ftc|temporary|maternity|secondment)", text, re.I)
    if m:
        return m.group(0)
    m = re.search(r"(?:contract|fixed[- ]term|ftc)[^.]{0,30}\b([1-9]|1[01])[- ]?(?:months?|mths?)\b", text, re.I)
    if m:
        return m.group(0)
    if re.search(r"\b(?:maternity|paternity|parental leave) cover\b|\btemporary role\b|\btemp role\b", text, re.I):
        return "temporary cover"
    return None


def required_missing(text, items):
    """Return an item from `items` that the ad says is required (e.g. 'ACCA qualified')."""
    low = fold(text)
    for item in items or []:
        w = fold(item).strip()
        if not w:
            continue
        for m in re.finditer(r"(?<![a-z0-9])" + re.escape(w) + r"(?![a-z0-9])", low):
            window = low[max(0, m.start() - 90): m.end() + 90]
            if re.search(r"required|essential|must have|must hold|qualified|mandatory|you will need|you'll need", window) \
                    and not re.search(r"desirable|preferred|nice to have|advantage|bonus|or working towards|part[- ]qualified|studying", window):
                return item
    return None


def check_salary(job, minimum):
    """Returns (ok, flag). ok is True (meets), False (below) or None (not stated)."""
    lo, hi = job.get("salary_min"), job.get("salary_max")
    if lo is None and hi is None:
        return None, f"Salary not stated: confirm it's at least £{minimum:,.0f}"
    floor = lo if lo is not None else hi
    if floor < minimum:
        return False, f"Salary below the £{minimum:,.0f} visa minimum"
    return True, None


def days_to_close(job, today: date):
    d = parse_date(job.get("closes"))
    return (d - today).days if d else None
