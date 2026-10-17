"""Build a local copy of the dashboard with sample data (from a fake pipeline run plus generated roles)."""
import json
import random
import shutil
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

import yaml  # noqa: E402

import fakes  # noqa: E402
from radar.main import run  # noqa: E402
from radar.util import UK  # noqa: E402

TITLES = {
    "Data & insight analyst": ["Data Analyst", "Junior Data Analyst", "BI Analyst", "Insight Analyst", "Reporting Analyst", "Graduate Data Analyst", "Customer Insight Analyst"],
    "Risk, credit & fraud analyst": ["Credit Risk Analyst", "Fraud Analyst", "Risk Analyst", "Financial Crime Analyst", "Graduate Risk Analyst"],
    "Junior data scientist": ["Data Scientist", "Junior Data Scientist", "Graduate Data Scientist"],
}
SNIPPETS = {
    "Data & insight analyst": ["Build Power BI dashboards and write SQL to report KPIs to stakeholders.",
                               "Use Python and Excel to clean data and present insight to managers.",
                               "Own weekly reporting in Tableau; strong SQL needed. Visa sponsorship is available.",
                               "Analyse customer data with SQL and Google Analytics; A/B testing experience a plus."],
    "Risk, credit & fraud analyst": ["Credit risk modelling in Python and SQL; IFRS 9 and Basel knowledge desirable.",
                                     "Fraud detection analytics with SQL; build rules and monitor alerts.",
                                     "AML and KYC investigations, financial crime reporting, Excel.",
                                     "Model validation for credit scoring scorecards; Python, SAS useful."],
    "Junior data scientist": ["Machine learning with Python, scikit-learn and XGBoost; deploy models with the team.",
                              "Predictive modelling and statistics in Python; insurance pricing models.",
                              "NLP and classification projects; Python, SQL, Git."],
}
EMPLOYERS = ["Northbridge Bank", "Harbour Insurance", "Kestrel Payments", "Lumen Analytics", "Oakfield Building Society",
             "Granite Lending", "Mosaic Retail", "Beacon Health Data", "Riverstone Capital", "Atlas Telecom",
             "Cobalt Energy", "Summit Logistics", "Juniper Pensions", "Fernhill Software", "Pioneer Mutual"]
PLACES = ["London", "Manchester", "Leeds", "Birmingham", "Glasgow", "Edinburgh", "Bristol", "Cardiff", "Remote", "Norwich"]


def synthetic(n, now):
    rnd = random.Random(7)
    out = []
    for i in range(n):
        fam = rnd.choice(list(TITLES))
        title = rnd.choice(TITLES[fam])
        snippet = rnd.choice(SNIPPETS[fam])
        company = rnd.choice(EMPLOYERS)
        lo = rnd.choice([None, 34000, 36000, 38000, 40000, 45000])
        hi = lo + rnd.choice([2000, 4000, 6000]) if lo else None
        minimum = {"Data & insight analyst": 33400, "Risk, credit & fraud analyst": 35140, "Junior data scientist": 38570}[fam]
        ok = None if lo is None else lo >= minimum
        if ok is False:
            lo, hi, ok = minimum + 600, minimum + 4600, True
        first = now - timedelta(hours=rnd.randint(0, 24 * 20))
        closes = (now + timedelta(days=rnd.randint(1, 40))).date().isoformat() if rnd.random() < 0.7 else None
        level = "Likely" if rnd.random() < 0.55 else "Possible"
        sscore = round(rnd.uniform(0.55, 1.0) if level == "Likely" else rnd.uniform(0.2, 0.5), 3)
        rel = round(rnd.uniform(0.6, 1.0), 3)
        skills = [s for s in ["SQL", "Python", "Power BI", "Tableau", "Excel", "Machine learning", "Credit risk", "Fraud", "AML", "KYC",
                              "scikit-learn", "XGBoost", "Statistics", "Stakeholder management", "Dashboards", "SAS", "Git", "IFRS 9", "Basel", "A/B testing"]
                  if s.lower() in snippet.lower() or (s == "Machine learning" and "machine learning" in snippet.lower())
                  or (s == "Dashboards" and "dashboard" in snippet.lower()) or (s == "Stakeholder management" and "stakeholder" in snippet.lower())]
        src = rnd.choice(["Adzuna", "Reed"])
        out.append({
            "id": f"{src.lower()}:s{i}", "key": f"k{i}", "title": title, "company": company, "location": rnd.choice(PLACES), "source": src,
            "url": f"https://example.com/apply/{i}", "links": [{"source": src, "url": f"https://example.com/listing/{i}"}],
            "salary": {"min": lo, "max": hi, "ok": ok, "minimum": minimum, "text": f"£{lo:,} – £{hi:,}" if lo else "Not stated", "note": ""},
            "posted": (first - timedelta(days=1)).date().isoformat(), "closes": closes,
            "first_seen": first.isoformat(timespec="minutes"), "last_seen": now.date().isoformat(), "family": fam, "relevance": rel,
            "sponsor": {"level": level, "score": sscore, "reasons": [f"Licensed Skilled Worker sponsor: {company} Ltd (A-rated)"],
                        "match": "exact", "register_name": company + " Ltd", "rating": "A"},
            "flags": [] if ok else [f"Salary not stated: confirm it's at least £{minimum:,}"],
            "score": round(0.4 * rel + 0.35 * sscore + 0.15 * (1 if ok else 0.5) + 0.1, 3), "contract": "permanent", "hours": "full_time",
            "desc": snippet + " " + "We are a growing team in the UK. " * 3, "skills": skills, "full_text": src == "Reed",
        })
    return out


def build(dest, extra=110, drop=None):
    dest = Path(dest)
    tmp = Path(tempfile.mkdtemp())
    cfg = yaml.safe_load((ROOT / "config.yml").read_text())
    cfg["sources"]["adzuna"]["calls_per_run"] = 20
    (tmp / "config.yml").write_text(yaml.safe_dump(cfg))
    (tmp / "docs").mkdir()
    shutil.copy(ROOT / "docs/skills.json", tmp / "docs/skills.json")
    now = datetime.now(UK).replace(second=0, microsecond=0)
    env = {"ADZUNA_APP_ID": "id", "ADZUNA_APP_KEY": "key", "GITHUB_REPOSITORY": "someone/sponsor-radar", "GITHUB_RUN_ID": "123"}
    run(tmp, env=env, now=now, session=fakes.make_session(), sleep=False, notify_on=False)
    data = json.loads((tmp / "docs/data/jobs.json").read_text())
    meta = json.loads((tmp / "docs/data/meta.json").read_text())
    jobs = data["jobs"] + synthetic(extra, now)
    if drop:
        jobs = [j for j in jobs if j["id"] not in drop]
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(ROOT / "docs", dest, ignore=shutil.ignore_patterns("data"))
    (dest / "data").mkdir()
    (dest / "data/jobs.json").write_text(json.dumps({"generated_at": data["generated_at"], "jobs": jobs}))
    meta["counts"]["showing"] = len(jobs)
    (dest / "data/meta.json").write_text(json.dumps(meta))
    shutil.rmtree(tmp)
    return len(jobs)


if __name__ == "__main__":
    print(build(sys.argv[1] if len(sys.argv) > 1 else "/tmp/radar-site"))
