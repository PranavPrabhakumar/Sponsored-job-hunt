"""One run of Sponsor Radar: fetch new listings, judge them, update the dashboard data, send alerts."""
from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter
from pathlib import Path

import yaml

from . import notify, rules
from .govuk import check_rule_pages
from .scoring import SkillExtractor, TargetClassifier, clean_title, overall_score, sponsor_evidence
from .sources import apply_reed_details, fetch_adzuna, fetch_reed, reed_details
from .sponsors import clean_name, load_register
from .util import clean_text, fold, in_quiet_hours, load_json, make_session, parse_date, save_json, timedelta, uk_now

DESC_KEEP = 2000


def dedupe_key(job) -> str:
    place = re.sub(r"[^a-z ]+", " ", fold(job.get("location", ""))).split()
    return f"{clean_name(job.get('company', ''))}|{clean_title(job.get('title', ''))}|{place[0] if place else ''}"


def salary_text(job):
    lo, hi = job.get("salary_min"), job.get("salary_max")
    f = lambda v: f"£{v:,.0f}"
    if lo and hi and lo != hi:
        return f"{f(lo)} – {f(hi)}"
    if lo and hi:
        return f(lo)
    if lo:
        return f"From {f(lo)}"
    if hi:
        return f"Up to {f(hi)}"
    return "Not stated"


class Judge:
    """Applies the rules to one listing and builds the dashboard record for the ones that pass."""

    def __init__(self, cfg, index, classifier, skills, session, env, today, now, sleep=True):
        self.cfg, self.index, self.clf, self.skills = cfg, index, classifier, skills
        self.session, self.env, self.today, self.now = session, env, today, now
        self.targets = {t.get("name"): t for t in cfg.get("targets") or []}
        reed = (cfg.get("sources") or {}).get("reed") or {}
        self.details_left = int(reed.get("max_details_per_run", 60))
        self.details_calls = 0
        self.sleep = sleep

    def _maybe_details(self, job, worth_it):
        if job["source"] != "Reed" or job.get("full_text") or not worth_it or self.details_left <= 0:
            return
        key = self.env.get("REED_API_KEY")
        if not key or not job.get("reed_id"):
            return
        try:
            self.details_left -= 1
            self.details_calls += 1
            apply_reed_details(job, reed_details(self.session, key, job["reed_id"]))
        except Exception:
            pass  # judge on the snippet instead

    def judge(self, job):
        """Return (record, None) when kept, or (None, reason) when rejected.
        Returns (None, None) when the listing can't be judged yet (try again next run)."""
        cfg = self.cfg
        title = job.get("title", "")
        posted = parse_date(job.get("posted"))
        days_old = (self.today - posted).days if posted else None
        if days_old is not None and days_old > int(cfg.get("max_listing_age_days", 21)):
            return None, "older listing"
        word = rules.title_excluded(title, cfg.get("exclude_title_words"))
        if word:
            return None, f"title has '{word}'"
        target, rel = self.clf.classify(title)
        if not target or rel < float(cfg.get("min_title_match", 0.45)):
            return None, "title doesn't match your targets"

        company = job.get("company", "")
        via = None
        if rules.is_agency(company):
            named = rules.employer_in_title(title)
            if named and self.index and self.index.match(named).kind != "none":
                via, company = company, named
                job["company"] = f"{named} (via {via})"
        match = self.index.match(company).as_dict() if self.index else {"match": "unknown"}
        snippet = f"{title}\n{job.get('description', '')}"
        hint = rules.sponsorship_positive(snippet)
        self._maybe_details(job, worth_it=match.get("match") not in ("none",) or bool(hint))
        text = f"{title}\n{job.get('description', '')}"

        if rules.sponsorship_negative(text):
            return None, "advert says no sponsorship"
        mention = rules.sponsorship_positive(text)
        agency = rules.is_agency(company)
        if agency and not mention:
            return None, "recruitment agency, employer not named"
        if rules.needs_clearance(text):
            return None, "needs security clearance"
        if rules.residency_requirement(text):
            return None, "UK residency requirement"
        if rules._find(rules._PHD, text):
            return None, "needs a PhD"
        need = rules.required_missing(text, cfg.get("reject_if_required"))
        if need:
            return None, f"requires {need}"
        years = rules.years_required(text)
        allowance = int(cfg.get("max_years_experience", 2)) + (1 if rules._JUNIOR.search(title) else 0)
        if years and years > allowance:
            return None, "asks for more experience"
        if job.get("hours") == "part_time" or re.search(r"\bpart[- ]time\b", title, re.I) or (
                re.search(r"\bpart[- ]time\b", text, re.I) and not re.search(r"\bfull[- ]time\b", text, re.I)):
            return None, "part-time"
        if rules.short_contract(text):
            return None, "short fixed-term contract"
        to_close = rules.days_to_close(job, self.today)
        if to_close is not None and to_close < 2:
            return None, "closes within 2 days"

        t = self.targets.get(target) or {}
        minimum = float(t.get("min_salary") or 0)
        ok, salary_flag = rules.check_salary(job, minimum)
        if ok is False:
            return None, "salary below the visa minimum"

        if not self.index and not mention:
            return None, None  # register unavailable this run: judge again next time
        score_s, level, reasons = sponsor_evidence(match, mention)
        if not level:
            return None, "employer not on the sponsor register"
        if level == "Possible" and not cfg.get("show_possible_sponsors", True):
            return None, "sponsor match uncertain"

        flags = []
        if salary_flag:
            flags.append(salary_flag)
        if job.get("contract") in ("contract", "temporary"):
            flags.append("Contract or fixed-term: check it lasts at least 12 months")
        if not job.get("full_text") and job["source"] == "Adzuna":
            flags.append("Only a summary of the advert was checked: read it for any sponsorship wording")
        if agency:
            flags.append("Posted by a recruitment agency")
        if via:
            flags.append(f"Posted on {via}; the employer is named in the title")
        if years:
            flags.append(f"Asks for {years} year{'s' if years != 1 else ''} of experience")

        description = job.get("description", "")
        record = {
            "id": job["id"],
            "key": dedupe_key(job),
            "title": title,
            "company": job.get("company", ""),
            "location": job.get("location", ""),
            "source": job["source"],
            "url": job.get("apply_url") or job.get("url"),
            "links": [{"source": job["source"], "url": job.get("url")}],
            "salary": {"min": job.get("salary_min"), "max": job.get("salary_max"), "ok": ok,
                       "minimum": minimum, "text": salary_text(job), "note": t.get("salary_note")},
            "posted": job.get("posted"),
            "closes": job.get("closes"),
            "first_seen": self.now.isoformat(timespec="minutes"),
            "last_seen": self.today.isoformat(),
            "family": target,
            "relevance": rel,
            "sponsor": {"level": level, "score": score_s, "reasons": reasons, "mention": mention, **match},
            "flags": flags,
            "score": overall_score(rel, score_s, ok, days_old),
            "contract": job.get("contract"),
            "hours": job.get("hours"),
            "desc": clean_text(description, DESC_KEEP),
            "skills": self.skills.find(f"{title}\n{description}"),
            "full_text": bool(job.get("full_text")),
        }
        return record, None


def refresh(existing, job, today):
    """A listing we already show turned up again: keep it fresh and add any new link."""
    existing["last_seen"] = today.isoformat()
    links = existing.setdefault("links", [])
    if job.get("url") and all(l.get("url") != job["url"] for l in links):
        if all(l.get("source") != job["source"] for l in links):
            links.append({"source": job["source"], "url": job["url"]})
    if job.get("closes") and not existing.get("closes"):
        existing["closes"] = job["closes"]


def run(root=".", env=None, now=None, session=None, sleep=True, notify_on=True):
    root = Path(root)
    env = dict(os.environ if env is None else env)
    now = now or uk_now()
    today = now.date()
    cfg = yaml.safe_load((root / "config.yml").read_text(encoding="utf-8")) or {}
    state_path, jobs_path, meta_path = root / "data/state.json", root / "docs/data/jobs.json", root / "docs/data/meta.json"
    state = load_json(state_path, {})
    session = session or make_session()
    repo = env.get("GITHUB_REPOSITORY")
    meta = {
        "generated_at": now.isoformat(timespec="minutes"),
        "repo": repo,
        "run_url": (f"https://github.com/{repo}/actions/runs/{env['GITHUB_RUN_ID']}" if repo and env.get("GITHUB_RUN_ID") else None),
        "schedule": "every 2 hours",
        "sources": {},
        "targets": [{"name": t.get("name"), "min_salary": t.get("min_salary"), "salary_note": t.get("salary_note"),
                     "searches": t.get("searches")} for t in cfg.get("targets") or []],
        "salary_rules_checked": str(cfg.get("salary_rules_checked") or ""),
    }

    index = None
    try:
        index, info = load_register(session)
        meta["sources"]["register"] = info
    except Exception as exc:
        meta["sources"]["register"] = {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:160]}"}

    az = fetch_adzuna(session, cfg, state, today, env.get("ADZUNA_APP_ID"), env.get("ADZUNA_APP_KEY"), sleep=1.0 if sleep else 0)
    rd = fetch_reed(session, cfg, env.get("REED_API_KEY"), sleep=0.5 if sleep else 0)

    existing = load_json(jobs_path, {}).get("jobs") or []
    by_id = {j["id"]: j for j in existing if isinstance(j, dict) and j.get("id")}
    by_key = {(j.get("key") or dedupe_key(j)): j for j in by_id.values()}
    seen = state.setdefault("seen", {})
    judge = Judge(cfg, index, TargetClassifier(cfg.get("targets")), SkillExtractor(root / "docs/skills.json"),
                  session, env, today, now, sleep)
    rejected, new_records, checked = Counter(), [], 0

    unique = {}
    for job in az.jobs + rd.jobs:
        unique.setdefault(job["id"], job)
    for job in unique.values():
        key = dedupe_key(job)
        hit = by_id.get(job["id"]) or by_key.get(key)
        if hit:
            refresh(hit, job, today)
            continue
        if job["id"] in seen:
            continue
        checked += 1
        record, reason = judge.judge(job)
        if record:
            seen[job["id"]] = today.isoformat()
            by_id[record["id"]] = record
            by_key[record["key"]] = record
            new_records.append(record)
        elif reason:
            seen[job["id"]] = today.isoformat()
            rejected[reason] += 1

    keep_days = int(cfg.get("keep_days", 30))
    oldest = (now - timedelta(days=keep_days)).isoformat(timespec="minutes")
    final = []
    for j in by_id.values():
        closes = parse_date(j.get("closes"))
        if closes and closes < today:
            continue
        if (j.get("first_seen") or "") < oldest:
            continue
        final.append(j)
    final.sort(key=lambda j: (j.get("score") or 0, j.get("first_seen") or ""), reverse=True)
    cutoff = (today - timedelta(days=keep_days + 7)).isoformat()
    state["seen"] = {k: v for k, v in seen.items() if v >= cutoff}

    # Rule pages and alerts
    rules_info = {"pages": [], "changed": [], "errors": []}
    try:
        rules_info = check_rule_pages(session, cfg.get("salary_rules_checked"))
    except Exception as exc:
        rules_info["errors"].append(type(exc).__name__)
    meta["rules"] = rules_info
    alerted = state.setdefault("rules_alerted", {})
    new_rule_changes = [p for p in rules_info.get("changed", []) if alerted.get(p["url"]) != p["updated"]]

    alert_min = float(cfg.get("alert_min_score", 0.55))
    fresh_ids = [r["id"] for r in new_records if r["score"] >= alert_min]
    qh = cfg.get("quiet_hours") or {}
    quiet = in_quiet_hours(now, qh.get("from", "22:30"), qh.get("to", "07:00"))
    pending = [i for i in state.get("pending_alerts", []) if i in by_id]
    meta["alerts"] = {"quiet_hours": quiet}
    if quiet:
        state["pending_alerts"] = pending + [i for i in fresh_ids if i not in pending]
        meta["alerts"]["held"] = len(state["pending_alerts"])
    else:
        ids = pending + [i for i in fresh_ids if i not in pending]
        to_send = sorted((by_id[i] for i in ids if i in by_id), key=lambda j: j.get("score") or 0, reverse=True)[:15]
        state["pending_alerts"] = []
        if notify_on and (to_send or new_rule_changes):
            status = notify.send(session, env, state, to_send, new_rule_changes, overnight=bool(pending))
            meta["alerts"].update(status)
            if status.get("channels", {}).get("github", {}).get("ok") or status.get("channels", {}).get("ntfy", {}).get("ok"):
                for p in new_rule_changes:
                    alerted[p["url"]] = p["updated"]

    meta["sources"]["adzuna"] = az.status()
    meta["sources"]["reed"] = rd.status()
    meta["sources"]["reed"]["detail_calls"] = judge.details_calls
    meta["counts"] = {"checked": checked, "kept": len(new_records), "rejected": sum(rejected.values()), "showing": len(final)}
    meta["rejected"] = dict(rejected.most_common())
    meta["dashboard"] = notify.dashboard_url(repo)
    history = state.setdefault("history", [])
    history.append({"at": meta["generated_at"], "checked": checked, "kept": len(new_records)})
    state["history"] = history[-50:]
    meta["recent_runs"] = state["history"][-12:]

    save_json(jobs_path, {"generated_at": meta["generated_at"], "jobs": final}, compact=True)
    save_json(meta_path, meta)
    save_json(state_path, state)

    configured = [s for s in (az, rd) if not s.skipped]
    if configured and all(s.errors and not s.jobs for s in configured):
        return 2  # every source failed: mark the run as failed so GitHub emails you
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Run Sponsor Radar once.")
    ap.add_argument("--root", default=".", help="repository folder")
    ap.add_argument("--no-notify", action="store_true", help="don't send alerts")
    ap.add_argument("--now", help="pretend it's this UK time (ISO format), for testing")
    a = ap.parse_args(argv)
    now = uk_now(a.now) if a.now else None
    code = run(a.root, now=now, notify_on=not a.no_notify)
    meta = load_json(Path(a.root) / "docs/data/meta.json", {})
    c = meta.get("counts", {})
    print(f"Checked {c.get('checked', 0)} new listings, kept {c.get('kept', 0)}, showing {c.get('showing', 0)}.")
    for name, s in (meta.get("sources") or {}).items():
        if s.get("error") or s.get("skipped"):
            print(f"  {name}: {s.get('error') or s.get('skipped')}")
    return code


if __name__ == "__main__":
    sys.exit(main())
