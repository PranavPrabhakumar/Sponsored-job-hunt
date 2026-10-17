"""The Home Office register of licensed sponsors: download it and match employers against it.

Matching works in three steps:
1. exact: the cleaned company name equals a cleaned register name;
2. prefix: the company name is the start of a register name ("Barclays" -> "Barclays Bank UK PLC");
3. similar: nearest neighbour on character 3-gram TF-IDF vectors, accepted above a cosine threshold.
"""
from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

from .util import fold

CONTENT_API = "https://www.gov.uk/api/content/government/publications/register-of-licensed-sponsors-workers"
PAGE_URL = "https://www.gov.uk/government/publications/register-of-licensed-sponsors-workers"
CSV_RE = re.compile(r"https://assets\.publishing\.service\.gov\.uk/[^\s\"'<>]+?\.csv", re.I)

DROP_WORDS = {
    "ltd", "limited", "plc", "llp", "llc", "lp", "inc", "incorporated", "co", "company", "corp",
    "corporation", "uk", "gb", "the", "and", "group", "holdings", "holding", "of", "international",
    "services", "service", "trading", "t", "a", "ta",
}
GENERIC = {
    "data", "digital", "global", "solutions", "consulting", "consultants", "tech", "technology",
    "technologies", "smart", "first", "new", "people", "health", "care", "capital", "partners",
    "management", "systems", "analytics", "insight", "insights", "bank", "finance", "financial",
    "insurance", "united", "national", "london", "british", "royal", "general", "city", "home",
}
SIMILAR_MIN = 0.82


def clean_name(name: str) -> str:
    s = fold(name).replace("&", " and ").replace("+", " and ")
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    words = [w for w in s.split() if w not in DROP_WORDS]
    return " ".join(words)


def split_trading_names(name: str) -> list[str]:
    """'ABC Ltd t/a XYZ' -> ['ABC Ltd', 'XYZ'] so both names can match."""
    parts = re.split(r"(?i)\s+(?:t/a|t\\a|trading as|ta)\s+", str(name))
    return [p.strip() for p in parts if p.strip()]


def rating_of(type_rating: str) -> str:
    s = (type_rating or "").lower()
    if "provisional" in s:
        return "Provisional"
    if re.search(r"\bb\b|b rating", s):
        return "B"
    if re.search(r"\ba\b|a rating", s):
        return "A"
    return ""


@dataclass
class Sponsor:
    name: str
    town: str = ""
    county: str = ""
    rating: str = ""
    routes: set = field(default_factory=set)
    skilled: bool = False

    def large(self) -> bool:
        # Organisations licensed for several routes (e.g. Global Business Mobility) are usually big employers.
        return len(self.routes) >= 2


@dataclass
class Match:
    kind: str                 # exact | prefix | similar | none
    sponsor: Sponsor | None = None
    similarity: float = 0.0

    def as_dict(self):
        if not self.sponsor:
            return {"match": "none"}
        return {
            "match": self.kind,
            "register_name": self.sponsor.name,
            "rating": self.sponsor.rating,
            "town": self.sponsor.town,
            "large": self.sponsor.large(),
            "similarity": round(self.similarity, 3),
        }


class SponsorIndex:
    def __init__(self, rows):
        by_key: dict[str, Sponsor] = {}
        for row in rows:
            r = {str(k).strip().lower(): (v or "").strip() for k, v in row.items() if k}
            name = r.get("organisation name") or r.get("organisation") or r.get("name") or ""
            route = r.get("route", "")
            if not name:
                continue
            for alias in split_trading_names(name):
                key = clean_name(alias)
                if not key:
                    continue
                sp = by_key.get(key)
                if sp is None:
                    sp = by_key[key] = Sponsor(name=name, town=r.get("town/city", ""), county=r.get("county", ""))
                if route:
                    sp.routes.add(route)
                if "skilled worker" in route.lower():
                    sp.rating = sp.rating or rating_of(r.get("type & rating", ""))
                    sp.skilled = True
        # Keep organisations licensed for the Skilled Worker route.
        self.sponsors = {k: v for k, v in by_key.items() if getattr(v, "skilled", False)}
        self.keys = sorted(self.sponsors)
        self.by_first: dict[str, list[str]] = {}
        for k in self.keys:
            self.by_first.setdefault(k.split()[0], []).append(k)
        self._vec = None
        self._matrix = None
        self._cache: dict[str, Match] = {}

    def __len__(self):
        return len(self.sponsors)

    def _ensure_vectors(self):
        if self._vec is None and self.keys:
            self._vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 3), sublinear_tf=True, dtype=np.float32)
            self._matrix = self._vec.fit_transform(self.keys)

    def _best(self, keys):
        def rank(k):
            sp = self.sponsors[k]
            return (sp.rating == "A", len(sp.routes), -len(k))
        return max(keys, key=rank)

    def match(self, company: str) -> Match:
        key = clean_name(company)
        if not key:
            return Match("none")
        if key in self._cache:
            return self._cache[key]
        result = self._match(key)
        self._cache[key] = result
        return result

    def _match(self, key: str) -> Match:
        if key in self.sponsors:
            return Match("exact", self.sponsors[key], 1.0)
        words = key.split()
        distinctive = not (len(words) == 1 and (words[0] in GENERIC or len(words[0]) < 4))
        if distinctive:
            starts = [k for k in self.by_first.get(words[0], []) if k.startswith(key + " ")]
            if starts:
                return Match("prefix", self.sponsors[self._best(starts)], 0.95)
        # The company name may carry an extra word ("Aviva Insurance" vs the register's "Aviva").
        parents = [k for k in self.by_first.get(words[0], [])
                   if key.startswith(k + " ") and (len(k.split()) > 1 or (k not in GENERIC and len(k) >= 5))]
        if parents:
            return Match("prefix", self.sponsors[max(parents, key=len)], 0.9)
        self._ensure_vectors()
        if self._matrix is None:
            return Match("none")
        q = self._vec.transform([key])
        if q.nnz == 0:
            return Match("none")
        sims = (self._matrix @ q.T).toarray().ravel()
        i = int(sims.argmax())
        best = float(sims[i])
        if best >= SIMILAR_MIN:
            k = self.keys[i]
            # Guard against matches that only share a generic word.
            shared = set(k.split()) & set(words)
            if shared - GENERIC:
                return Match("similar", self.sponsors[k], best)
        return Match("none", None, best)


def find_csv_url(session, timeout=30):
    """Return (csv_url, last_updated) for the current register file."""
    updated = None
    try:
        r = session.get(CONTENT_API, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        updated = data.get("public_updated_at") or data.get("updated_at")
        for a in (data.get("details") or {}).get("attachments") or []:
            url = (a or {}).get("url") or ""
            if url.lower().endswith(".csv"):
                return url, updated
        found = CSV_RE.findall(json.dumps(data))
        if found:
            return found[0], updated
    except Exception:
        pass
    r = session.get(PAGE_URL, timeout=timeout)
    r.raise_for_status()
    found = CSV_RE.findall(r.text)
    if not found:
        raise RuntimeError("couldn't find the register CSV on gov.uk")
    return found[0], updated


def read_csv_text(raw: bytes):
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    return list(csv.DictReader(io.StringIO(text)))


def load_register(session, timeout=90):
    """Download the register and build the index. Returns (index, info)."""
    url, updated = find_csv_url(session)
    r = session.get(url, timeout=timeout)
    r.raise_for_status()
    rows = read_csv_text(r.content)
    if len(rows) < 1000:
        raise RuntimeError(f"the register file looks too small ({len(rows)} rows)")
    index = SponsorIndex(rows)
    m = re.search(r"(\d{4}-\d{2}-\d{2})", url)
    info = {"ok": True, "url": url, "file_date": m.group(1) if m else None, "updated": updated,
            "rows": len(rows), "skilled_worker_sponsors": len(index)}
    return index, info
