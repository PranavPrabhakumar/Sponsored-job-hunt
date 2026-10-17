"""Job sources: Adzuna and Reed. Each returns listings in one common shape."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from .util import annual_salary, clean_text, iso, one_line, parse_date, to_number, week_key

ADZUNA_URL = "https://api.adzuna.com/v1/api/jobs/gb/search/{page}"
REED_SEARCH_URL = "https://www.reed.co.uk/api/1.0/search"
REED_JOB_URL = "https://www.reed.co.uk/api/1.0/jobs/{id}"
DESC_LIMIT = 6000


@dataclass
class SourceResult:
    name: str
    jobs: list = field(default_factory=list)
    calls: int = 0
    errors: list = field(default_factory=list)
    skipped: str | None = None

    def status(self):
        out = {"calls": self.calls, "found": len(self.jobs)}
        if self.skipped:
            out["skipped"] = self.skipped
        if self.errors:
            out["error"] = self.errors[0]
        out["ok"] = not self.errors and not self.skipped
        return out


def all_searches(cfg):
    out = []
    for t in cfg.get("targets") or []:
        for s in t.get("searches") or []:
            if str(s).strip():
                out.append((t.get("name") or "Target", str(s).strip().lower()))
    return out


def _http_error(exc) -> str:
    resp = getattr(exc, "response", None)
    if resp is not None:
        code = resp.status_code
        if code in (401, 403):
            return f"HTTP {code}: the API key was refused. Check the key in the repository secrets."
        if code == 429:
            return "HTTP 429: too many requests. The next run will try again."
        return f"HTTP {code}"
    return type(exc).__name__ + (f": {exc}" if str(exc) else "")


# ---------------------------------------------------------------- Adzuna

def adzuna_budget(cfg_src, state, today):
    """How many Adzuna calls this run may make without passing the free-key limits."""
    u = state.setdefault("adzuna_usage", {})
    day, week, month = today.isoformat(), week_key(today), today.strftime("%Y-%m")
    if u.get("day") != day:
        u["day"], u["day_calls"] = day, 0
    if u.get("week") != week:
        u["week"], u["week_calls"] = week, 0
    if u.get("month") != month:
        u["month"], u["month_calls"] = month, 0
    left = [
        int(cfg_src.get("calls_per_run", 6)),
        int(cfg_src.get("max_calls_per_day", 120)) - u["day_calls"],
        int(cfg_src.get("max_calls_per_week", 900)) - u["week_calls"],
        int(cfg_src.get("max_calls_per_month", 2400)) - u["month_calls"],
    ]
    return max(0, min(left))


def _count_adzuna(state, n=1):
    u = state["adzuna_usage"]
    for k in ("day_calls", "week_calls", "month_calls"):
        u[k] = u.get(k, 0) + n


def norm_adzuna(item, target, search):
    company = one_line((item.get("company") or {}).get("display_name"))
    loc = item.get("location") or {}
    area = loc.get("area") or []
    location = one_line(loc.get("display_name")) or ", ".join(area[1:3])
    predicted = str(item.get("salary_is_predicted", "0")).strip() in ("1", "true", "True")
    smin, smax = to_number(item.get("salary_min")), to_number(item.get("salary_max"))
    return {
        "id": f"adzuna:{item.get('id')}",
        "source": "Adzuna",
        "title": one_line(item.get("title")),
        "company": company,
        "location": location,
        "salary_min": None if predicted else annual_salary(smin),
        "salary_max": None if predicted else annual_salary(smax),
        "salary_predicted": predicted,
        "posted": iso(parse_date(item.get("created"))),
        "closes": None,
        "url": item.get("redirect_url") or "",
        "description": clean_text(item.get("description"), DESC_LIMIT),
        "contract": (item.get("contract_type") or None),
        "hours": (item.get("contract_time") or None),
        "target_hint": target,
        "search": search,
        "full_text": False,
    }


def fetch_adzuna(session, cfg, state, today, app_id, app_key, sleep=1.0):
    src = (cfg.get("sources") or {}).get("adzuna") or {}
    res = SourceResult("Adzuna")
    if not src.get("enabled", True):
        res.skipped = "turned off in config.yml"
        return res
    if not (app_id and app_key):
        res.skipped = "no API key yet (add ADZUNA_APP_ID and ADZUNA_APP_KEY to the repository secrets)"
        return res
    searches = all_searches(cfg)
    if not searches:
        res.skipped = "no searches in config.yml"
        return res
    allowed = adzuna_budget(src, state, today)
    if allowed <= 0:
        res.skipped = "free-key limit reached for now; it resumes automatically"
        return res
    start = int(state.get("adzuna_cursor", 0)) % len(searches)
    batch = [searches[(start + i) % len(searches)] for i in range(min(allowed, len(searches)))]
    state["adzuna_cursor"] = (start + len(batch)) % len(searches)
    for target, search in batch:
        params = {
            "app_id": app_id, "app_key": app_key,
            "results_per_page": int(src.get("results_per_page", 50)),
            "title_only": search,
            "max_days_old": int(cfg.get("max_days_old", 3)),
            "sort_by": "date",
            "content-type": "application/json",
        }
        try:
            r = session.get(ADZUNA_URL.format(page=1), params=params, timeout=30)
            res.calls += 1
            _count_adzuna(state)
            r.raise_for_status()
            for item in (r.json() or {}).get("results") or []:
                job = norm_adzuna(item, target, search)
                if job["title"] and job["url"]:
                    res.jobs.append(job)
        except Exception as exc:  # keep going with the other searches
            res.errors.append(f"'{search}': {_http_error(exc)}")
            if getattr(getattr(exc, "response", None), "status_code", None) in (401, 403):
                break
        if sleep:
            time.sleep(sleep)
    return res


# ---------------------------------------------------------------- Reed

def norm_reed(item, target, search):
    smin, smax = item.get("minimumSalary"), item.get("maximumSalary")
    return {
        "id": f"reed:{item.get('jobId')}",
        "source": "Reed",
        "reed_id": item.get("jobId"),
        "title": one_line(item.get("jobTitle")),
        "company": one_line(item.get("employerName")),
        "location": one_line(item.get("locationName")),
        "salary_min": annual_salary(smin),
        "salary_max": annual_salary(smax),
        "salary_predicted": False,
        "posted": iso(parse_date(item.get("date"))),
        "closes": iso(parse_date(item.get("expirationDate"))),
        "url": item.get("jobUrl") or (f"https://www.reed.co.uk/jobs/{item.get('jobId')}" if item.get("jobId") else ""),
        "description": clean_text(item.get("jobDescription"), DESC_LIMIT),
        "contract": None,
        "hours": None,
        "target_hint": target,
        "search": search,
        "full_text": False,
    }


def apply_reed_details(job, d):
    """Merge the full Reed advert into a listing."""
    if not d:
        return job
    desc = clean_text(d.get("jobDescription"), DESC_LIMIT)
    if desc:
        job["description"] = desc
        job["full_text"] = True
    ymin = d.get("yearlyMinimumSalary")
    ymax = d.get("yearlyMaximumSalary")
    if to_number(ymin) or to_number(ymax):
        job["salary_min"] = annual_salary(ymin)
        job["salary_max"] = annual_salary(ymax)
    ct = str(d.get("contractType") or "").lower()
    if ct:
        job["contract"] = "permanent" if "perm" in ct else ("contract" if "contract" in ct else ("temporary" if "temp" in ct else ct))
    if d.get("partTime") is True and d.get("fullTime") is not True:
        job["hours"] = "part_time"
    elif d.get("fullTime") is True:
        job["hours"] = "full_time"
    closes = parse_date(d.get("expirationDate"))
    if closes:
        job["closes"] = closes.isoformat()
    ext = d.get("externalUrl")
    if ext and str(ext).startswith("http"):
        job["apply_url"] = ext
    return job


def fetch_reed(session, cfg, api_key, sleep=0.5):
    src = (cfg.get("sources") or {}).get("reed") or {}
    res = SourceResult("Reed")
    if not src.get("enabled", True):
        res.skipped = "turned off in config.yml"
        return res
    if not api_key:
        res.skipped = "no API key yet (add REED_API_KEY to the repository secrets)"
        return res
    for target, search in all_searches(cfg):
        params = {"keywords": search, "resultsToTake": int(src.get("results_to_take", 100))}
        if src.get("direct_employers_only", True):
            params["postedByDirectEmployer"] = "true"
        try:
            r = session.get(REED_SEARCH_URL, params=params, auth=(api_key, ""), timeout=30)
            res.calls += 1
            r.raise_for_status()
            for item in (r.json() or {}).get("results") or []:
                job = norm_reed(item, target, search)
                if job["title"] and job["url"]:
                    res.jobs.append(job)
        except Exception as exc:
            res.errors.append(f"'{search}': {_http_error(exc)}")
            if getattr(getattr(exc, "response", None), "status_code", None) in (401, 403):
                break
        if sleep:
            time.sleep(sleep)
    return res


def reed_details(session, api_key, reed_id):
    r = session.get(REED_JOB_URL.format(id=reed_id), auth=(api_key, ""), timeout=30)
    r.raise_for_status()
    return r.json() or {}
