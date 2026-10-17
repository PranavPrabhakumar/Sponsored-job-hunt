"""Machine-learning and scoring parts of the pipeline.

- TargetClassifier: nearest-neighbour classifier on TF-IDF vectors (word 1-2 grams plus
  character 2-4 grams) that maps a job title to the closest target and gives a similarity.
- SkillExtractor: finds known skills (docs/skills.json) in an advert.
- sponsor_evidence: turns register matches and advert wording into a 0-1 evidence score.
- overall_score: blends relevance, sponsorship evidence, salary and freshness for ranking.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

from .util import fold

GRADE_WORDS = r"\b(?:junior|jnr|jr|graduate|grad|entry[- ]level|trainee|associate|assistant|i|ii|iii|level \d|band \w+|grade \w+|apprentice)\b"


def clean_title(title: str) -> str:
    t = fold(title)
    t = re.sub(r"\(.*?\)|\[.*?\]", " ", t)
    t = re.split(r"\s[-–|]\s|\s@\s", t)[0]
    t = re.sub(GRADE_WORDS, " ", t)
    t = t.replace("&", " and ")
    t = re.sub(r"[^a-z0-9+ ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


class TargetClassifier:
    def __init__(self, targets):
        self.labels, docs = [], []
        for t in targets or []:
            name = t.get("name") or "Target"
            for phrase in (t.get("searches") or []) + [name]:
                p = clean_title(phrase)
                if p:
                    self.labels.append(name)
                    docs.append(p)
        self.ready = bool(docs)
        if not self.ready:
            return
        self.word = TfidfVectorizer(analyzer="word", ngram_range=(1, 2), token_pattern=r"[a-z0-9+]+", sublinear_tf=True)
        self.char = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), sublinear_tf=True)
        self.Wd = self.word.fit_transform(docs)
        self.Wc = self.char.fit_transform(docs)

    def classify(self, title: str):
        """Return (target_name, similarity 0-1)."""
        if not self.ready:
            return None, 0.0
        t = clean_title(title)
        if not t:
            return None, 0.0
        sw = (self.Wd @ self.word.transform([t]).T).toarray().ravel()
        sc = (self.Wc @ self.char.transform([t]).T).toarray().ravel()
        sims = 0.5 * sw + 0.5 * sc
        i = int(np.argmax(sims))
        return self.labels[i], float(round(sims[i], 3))


class SkillExtractor:
    def __init__(self, path):
        self.skills = json.loads(Path(path).read_text(encoding="utf-8"))
        self.patterns = []
        for s in self.skills:
            pats = []
            for a in s.get("aliases") or []:
                a = fold(a).strip()
                if a:
                    pats.append(re.compile(r"(?<![a-z0-9])" + re.escape(a) + r"(?![a-z0-9])"))
            special = s.get("special")
            self.patterns.append((s["name"], pats, special))

    def find(self, text: str):
        low = fold(text)
        found = []
        for name, pats, special in self.patterns:
            hit = any(p.search(low) for p in pats)
            if not hit and special:
                # Short upper-case names (R, SAS) must appear on their own in the original text.
                hit = re.search(r"(?:^|[\s,(/;])" + re.escape(special) + r"(?=[\s,)/;.]|$)", text or "") is not None
            if hit:
                found.append(name)
        return found


def sponsor_evidence(match: dict, mention: str | None):
    """Score how likely the employer can sponsor this role. Returns (score, level, reasons)."""
    score, reasons = 0.0, []
    if mention:
        score += 0.55
        reasons.append(f'Advert mentions sponsorship ("{mention.strip()[:80]}")')
    kind = (match or {}).get("match", "none")
    rating = (match or {}).get("rating", "")
    if kind in ("exact", "prefix"):
        score += 0.4 if rating == "A" else 0.25
        reasons.append(f"Licensed Skilled Worker sponsor: {match.get('register_name')}" + (f" ({rating}-rated)" if rating else ""))
    elif kind == "similar":
        score += 0.2
        reasons.append(f"Probably on the sponsor register as {match.get('register_name')} (name match {match.get('similarity', 0):.0%})")
    if kind != "none" and match.get("large"):
        score += 0.15
        reasons.append("Licensed for several visa routes, which usually means a large sponsor")
    score = min(1.0, score)
    if score >= 0.55:
        level = "Likely"
    elif score >= 0.2:
        level = "Possible"
    else:
        level = None
    return round(score, 3), level, reasons


def overall_score(relevance: float, sponsor_score: float, salary_ok, days_old):
    salary = 1.0 if salary_ok is True else 0.5
    fresh = 1.0 if days_old is None else max(0.0, 1.0 - max(0, days_old) / 7.0)
    return round(0.4 * relevance + 0.35 * sponsor_score + 0.15 * salary + 0.1 * fresh, 3)
