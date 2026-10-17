"""Watch the gov.uk Skilled Worker pages, so salary minimums in config.yml don't go stale unnoticed."""
from __future__ import annotations

from .util import iso, parse_date

RULE_PAGES = [
    ("Skilled Worker visa: going rates for eligible occupations",
     "government/publications/skilled-worker-visa-going-rates-for-eligible-occupations"),
    ("Skilled Worker visa: temporary shortage list",
     "government/publications/skilled-worker-visa-temporary-shortage-list"),
    ("Skilled Worker visa (guide)", "skilled-worker-visa"),
]


def check_rule_pages(session, checked_on, timeout=20):
    """Compare each page's last update with the date the salary minimums were last checked."""
    checked = parse_date(checked_on)
    pages, errors = [], []
    for title, slug in RULE_PAGES:
        url = f"https://www.gov.uk/{slug}"
        try:
            r = session.get(f"https://www.gov.uk/api/content/{slug}", timeout=timeout)
            r.raise_for_status()
            data = r.json()
            updated = parse_date(data.get("public_updated_at") or data.get("updated_at"))
            pages.append({
                "title": data.get("title") or title,
                "url": url,
                "updated": iso(updated),
                "changed": bool(updated and checked and updated > checked),
            })
        except Exception as exc:
            errors.append(f"{title}: {type(exc).__name__}")
    return {
        "salary_rules_checked": iso(checked),
        "pages": pages,
        "changed": [p for p in pages if p["changed"]],
        "errors": errors,
    }
