"""Alerts: a comment on a 'Job alerts' issue in the repository (GitHub emails and notifies you),
plus an optional push to the free ntfy app."""
from __future__ import annotations

API = "https://api.github.com"
ISSUE_TITLE = "Job alerts"


def dashboard_url(repo: str | None) -> str | None:
    if not repo or "/" not in repo:
        return None
    owner, name = repo.split("/", 1)
    return f"https://{owner.lower()}.github.io/{name}/"


def _money(v):
    return f"£{v:,.0f}" if isinstance(v, (int, float)) else None


def salary_line(job):
    s = job.get("salary") or {}
    lo, hi = _money(s.get("min")), _money(s.get("max"))
    if lo and hi and lo != hi:
        return f"{lo}–{hi}"
    if lo or hi:
        return lo or hi
    m = _money(s.get("minimum"))
    return f"salary not stated (needs {m}+)" if m else "salary not stated"


def job_markdown(job):
    sp = job.get("sponsor") or {}
    bits = [job.get("company") or "", job.get("location") or "", salary_line(job)]
    closes = job.get("closes")
    bits.append(f"closes {closes}" if closes else "no closing date given")
    evidence = (sp.get("reasons") or [""])[0]
    return (f"- **[{job.get('title')}]({job.get('url')})** · " + " · ".join(b for b in bits if b)
            + f"\n  Sponsor: {sp.get('level', '?')}" + (f". {evidence}" if evidence else ""))


def build_message(jobs, rule_changes, dashboard, owner, overnight=False):
    lines = []
    if jobs:
        head = f"**{len(jobs)} new sponsored role{'s' if len(jobs) != 1 else ''}**"
        if overnight:
            head += " (including ones found overnight)"
        lines.append(head + (f" · @{owner}" if owner else ""))
        lines += [job_markdown(j) for j in jobs]
    for page in rule_changes:
        lines.append(f"\ngov.uk updated **{page['title']}** on {page['updated']}. Check the salary minimums "
                     f"in config.yml against it, then update `salary_rules_checked`. {page['url']}"
                     + (f" · @{owner}" if owner and not jobs else ""))
    if dashboard:
        lines.append(f"\n[Open the dashboard]({dashboard}) to rank these against your CV and make tailored CVs and letters.")
    return "\n".join(lines)


def _gh(session, token, method, path, **kw):
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28"}
    r = session.request(method, API + path, headers=headers, timeout=30, **kw)
    r.raise_for_status()
    return r.json() if r.content else {}


def find_or_create_issue(session, token, repo, state, dashboard):
    num = state.get("alert_issue")
    if num:
        try:
            issue = _gh(session, token, "GET", f"/repos/{repo}/issues/{num}")
            if issue.get("state") == "open":
                return num
        except Exception:
            pass
    for issue in _gh(session, token, "GET", f"/repos/{repo}/issues", params={"state": "open", "per_page": 100}):
        if issue.get("title") == ISSUE_TITLE and not issue.get("pull_request"):
            state["alert_issue"] = issue["number"]
            return issue["number"]
    body = ("Sponsor Radar posts newly found sponsored roles here as comments, so GitHub notifies you by email "
            "and in the GitHub app. Close this issue to stop alerts; a new one is opened on the next find.")
    if dashboard:
        body += f"\n\nDashboard: {dashboard}"
    issue = _gh(session, token, "POST", f"/repos/{repo}/issues", json={"title": ISSUE_TITLE, "body": body})
    state["alert_issue"] = issue["number"]
    return issue["number"]


def post_github(session, token, repo, state, text, dashboard):
    num = find_or_create_issue(session, token, repo, state, dashboard)
    _gh(session, token, "POST", f"/repos/{repo}/issues/{num}/comments", json={"body": text})
    return num


def post_ntfy(session, topic, jobs, rule_changes, dashboard):
    if jobs:
        title = f"{len(jobs)} new sponsored role{'s' if len(jobs) != 1 else ''}"
        body = "\n".join(f"{j.get('title')} - {j.get('company')} ({j.get('location')})" for j in jobs[:8])
        if len(jobs) > 8:
            body += f"\n+{len(jobs) - 8} more"
    else:
        title = "Visa rules page updated"
        body = "\n".join(f"{p['title']} ({p['updated']})" for p in rule_changes)
    headers = {"Title": title.encode("ascii", "ignore").decode(), "Tags": "briefcase"}
    if dashboard:
        headers["Click"] = dashboard
    r = session.post(f"https://ntfy.sh/{topic}", data=body.encode("utf-8"), headers=headers, timeout=20)
    r.raise_for_status()


def send(session, env, state, jobs, rule_changes, overnight=False):
    """Send alerts through every channel that's set up. Returns a status dict for meta.json."""
    repo = env.get("GITHUB_REPOSITORY")
    owner = env.get("GITHUB_REPOSITORY_OWNER") or (repo.split("/")[0] if repo else None)
    dashboard = dashboard_url(repo)
    status = {"sent": len(jobs), "rule_changes": len(rule_changes), "channels": {}}
    if not jobs and not rule_changes:
        return status
    token = env.get("GITHUB_TOKEN")
    if token and repo:
        try:
            num = post_github(session, token, repo, state, build_message(jobs, rule_changes, dashboard, owner, overnight), dashboard)
            status["channels"]["github"] = {"ok": True, "issue": num}
        except Exception as exc:
            status["channels"]["github"] = {"ok": False, "error": type(exc).__name__ + ": " + str(exc)[:200]}
    topic = (env.get("NTFY_TOPIC") or "").strip()
    if topic:
        try:
            post_ntfy(session, topic, jobs, rule_changes, dashboard)
            status["channels"]["ntfy"] = {"ok": True}
        except Exception as exc:
            status["channels"]["ntfy"] = {"ok": False, "error": type(exc).__name__}
    return status
