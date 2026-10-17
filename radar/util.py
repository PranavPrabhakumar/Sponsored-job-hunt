"""Small helpers shared by the pipeline: text cleaning, dates, HTTP and JSON files."""
from __future__ import annotations

import html
import json
import os
import re
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

UK = ZoneInfo("Europe/London")
USER_AGENT = "sponsor-radar/1.0 (personal job search; +https://github.com)"

_TAG = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"[ \t\r\f\v]+")


def clean_text(value, limit: int | None = None) -> str:
    """Strip HTML, decode entities and squeeze whitespace, keeping line breaks."""
    if value is None:
        return ""
    s = str(value)
    s = re.sub(r"(?i)<\s*(br|/p|/li|/div|/h\d|/tr)\s*/?>", "\n", s)
    s = re.sub(r"(?i)<\s*li[^>]*>", "\n• ", s)
    s = _TAG.sub(" ", s)
    s = html.unescape(s).replace(" ", " ").replace("​", "")
    s = _SPACES.sub(" ", s)
    s = re.sub(r" *\n[ \n]*", "\n", s).strip()
    if limit and len(s) > limit:
        s = s[:limit].rsplit(" ", 1)[0].rstrip(" ,.;:") + "…"
    return s


def one_line(value) -> str:
    return re.sub(r"\s+", " ", clean_text(value)).strip()


def fold(s: str) -> str:
    """Lower-case ASCII version of a string (accents removed)."""
    return unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()


def parse_date(value) -> date | None:
    """Parse ISO dates/datetimes and UK dd/mm/yyyy dates. Returns None when unclear."""
    if not value:
        return None
    s = str(value).strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        try:
            return date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return None
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", s)
    if m:
        try:
            return date(int(m[3]), int(m[2]), int(m[1]))
        except ValueError:
            return None
    return None


def iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def to_number(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        n = float(str(value).replace(",", "").replace("£", "").strip())
    except ValueError:
        return None
    return n if n > 0 else None


def annual_salary(value) -> float | None:
    """Turn an hourly or daily rate into a yearly figure; leave yearly salaries alone."""
    n = to_number(value)
    if n is None:
        return None
    if n < 100:          # hourly rate: 37.5 hours x 52 weeks
        return round(n * 1950)
    if n < 1500:         # daily rate: about 230 working days
        return round(n * 230)
    return round(n)


def in_quiet_hours(now: datetime, start: str, end: str) -> bool:
    """True when the UK time `now` falls in [start, end), wrapping past midnight."""
    def minutes(hm):
        h, m = (int(x) for x in str(hm).split(":"))
        return h * 60 + m
    try:
        a, b = minutes(start), minutes(end)
    except Exception:
        return False
    t = now.hour * 60 + now.minute
    if a == b:
        return False
    return a <= t < b if a < b else (t >= a or t < b)


def make_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(total=3, connect=3, read=2, backoff_factor=1.5,
                  status_forcelist=[429, 500, 502, 503, 504], allowed_methods=["GET"],
                  respect_retry_after_header=True)
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.headers["User-Agent"] = USER_AGENT
    return s


def load_json(path, default):
    p = Path(path)
    if not p.exists():
        return default
    try:
        with p.open(encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def save_json(path, data, compact=False):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        if compact:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        else:
            json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=False)
        f.write("\n")
    os.replace(tmp, p)


def week_key(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def days_between(a: date | None, b: date | None) -> int | None:
    if not a or not b:
        return None
    return (b - a).days


def uk_now(override: str | None = None) -> datetime:
    """Current UK time, or a fixed time from RADAR_NOW (used by tests)."""
    override = override or os.environ.get("RADAR_NOW")
    if override:
        dt = datetime.fromisoformat(override)
        return dt.astimezone(UK) if dt.tzinfo else dt.replace(tzinfo=UK)
    return datetime.now(UK)


__all__ = [n for n in dir() if not n.startswith("_")] + ["timedelta"]
