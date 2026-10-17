"""Search settings saved from the dashboard (settings.json), laid over the defaults in config.yml."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .util import load_json

APP_KEYS = ("targets", "max_years_experience", "locations", "include_remote", "exclude_title_words",
            "reject_if_required", "quiet_hours")
MAX_TARGETS = 8
MAX_SEARCHES = 30


def _text(v, limit=60):
    s = re.sub(r"\s+", " ", str(v or "")).strip()
    return s[:limit]


def _words(v, limit_items, limit_len=60):
    if isinstance(v, str):
        v = v.split(",")
    out = []
    for x in v if isinstance(v, list) else []:
        t = _text(x, limit_len)
        if t and t.lower() not in (o.lower() for o in out):
            out.append(t)
        if len(out) >= limit_items:
            break
    return out


def _hm(v, default):
    s = str(v or "")
    return s if re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", s) else default


def sanitize(raw):
    """Keep only valid, sensible values from settings.json. Returns a dict of the keys that are usable."""
    if not isinstance(raw, dict):
        return {}
    out = {}
    targets, total = [], 0
    for t in raw.get("targets") or []:
        if not isinstance(t, dict):
            continue
        name = _text(t.get("name"))
        searches = [s.lower() for s in _words(t.get("searches"), 10)]
        searches = searches[: max(0, MAX_SEARCHES - total)]
        if not name or not searches:
            continue
        try:
            minimum = int(float(t.get("min_salary") or 0))
        except (TypeError, ValueError):
            minimum = 0
        minimum = max(0, min(minimum, 250000))
        targets.append({"name": name, "searches": searches, "min_salary": minimum,
                        "salary_note": _text(t.get("salary_note"), 120)})
        total += len(searches)
        if len(targets) >= MAX_TARGETS or total >= MAX_SEARCHES:
            break
    if targets:
        out["targets"] = targets
    if "max_years_experience" in raw:
        try:
            out["max_years_experience"] = max(0, min(15, int(raw["max_years_experience"])))
        except (TypeError, ValueError):
            pass
    if "locations" in raw:
        out["locations"] = _words(raw.get("locations"), 20)
    if "include_remote" in raw:
        out["include_remote"] = bool(raw.get("include_remote"))
    if "exclude_title_words" in raw:
        out["exclude_title_words"] = [w.lower() for w in _words(raw.get("exclude_title_words"), 80, 40)]
    if "reject_if_required" in raw:
        out["reject_if_required"] = [w.lower() for w in _words(raw.get("reject_if_required"), 50, 40)]
    if isinstance(raw.get("quiet_hours"), dict):
        q = raw["quiet_hours"]
        out["quiet_hours"] = {"from": _hm(q.get("from"), "22:30"), "to": _hm(q.get("to"), "07:00")}
    return out


def apply(root, cfg):
    """Merge settings.json into the config. Returns (merged_config, info)."""
    raw = load_json(Path(root) / "settings.json", None)
    if raw is None:
        return cfg, {"source": "config.yml"}
    clean = sanitize(raw)
    merged = dict(cfg)
    merged.update(clean)
    return merged, {"source": "settings.json", "saved_at": str(raw.get("saved_at") or "")[:40] if isinstance(raw, dict) else "",
                    "ignored": sorted(set(raw) - set(clean) - {"saved_at", "version"}) if isinstance(raw, dict) else []}


def effective(cfg):
    """The settings the dashboard shows and edits."""
    return {
        "targets": [{"name": t.get("name"), "searches": t.get("searches") or [], "min_salary": t.get("min_salary") or 0,
                     "salary_note": t.get("salary_note") or ""} for t in cfg.get("targets") or []],
        "max_years_experience": cfg.get("max_years_experience", 2),
        "locations": cfg.get("locations") or [],
        "include_remote": cfg.get("include_remote", True),
        "exclude_title_words": cfg.get("exclude_title_words") or [],
        "reject_if_required": cfg.get("reject_if_required") or [],
        "quiet_hours": cfg.get("quiet_hours") or {"from": "22:30", "to": "07:00"},
    }


def fingerprint(cfg):
    """Changes whenever a setting that decides which roles are kept changes."""
    keys = ("targets", "max_years_experience", "locations", "include_remote", "exclude_title_words",
            "reject_if_required", "min_title_match", "max_listing_age_days", "show_possible_sponsors")
    blob = json.dumps({k: cfg.get(k) for k in keys}, sort_keys=True, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]
