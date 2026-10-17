"""A fake HTTP session and sample data, so the pipeline can be tested without the internet."""
import csv
import io
import json
import re


class FakeResponse:
    def __init__(self, status=200, data=None, content=None, text=None):
        self.status_code = status
        self._data = data
        if content is None:
            if text is not None:
                content = text.encode("utf-8")
            elif data is not None:
                content = json.dumps(data).encode("utf-8")
            else:
                content = b""
        self.content = content
        self.text = content.decode("utf-8", "replace")

    def json(self):
        return self._data if self._data is not None else json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            err = requests.HTTPError(f"HTTP {self.status_code}")
            err.response = self
            raise err


class FakeSession:
    """Routes requests by URL pattern. `routes` maps a regex to a function(method, url, kw) -> FakeResponse."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        for pattern, fn in self.routes:
            if re.search(pattern, url):
                return fn(method, url, kw)
        return FakeResponse(404, {"error": "no route"})

    def get(self, url, **kw):
        return self.request("GET", url, **kw)

    def post(self, url, **kw):
        return self.request("POST", url, **kw)


REGISTER_ROWS = [
    ("Barclays Bank UK PLC", "London", "", "Worker (A rating)", "Skilled Worker"),
    ("Barclays Bank UK PLC", "London", "", "Worker (A rating)", "Global Business Mobility: Senior or Specialist Worker"),
    ("Barclays Execution Services Limited", "London", "", "Worker (A rating)", "Skilled Worker"),
    ("Monzo Bank Limited", "London", "", "Worker (A rating)", "Skilled Worker"),
    ("Aviva Plc", "London", "", "Worker (A rating)", "Skilled Worker"),
    ("Nationwide Building Society", "Swindon", "Wiltshire", "Worker (A rating)", "Skilled Worker"),
    ("Hargreaves Lansdown Asset Management Limited", "Bristol", "", "Worker (A rating)", "Skilled Worker"),
    ("Small Analytics Co Ltd T/A Brightdata Insights", "Leeds", "", "Worker (B rating)", "Skilled Worker"),
    ("Charity Only Org", "York", "", "Temporary Worker (A rating)", "Charity Worker"),
    ("Hays Specialist Recruitment Limited", "London", "", "Worker (A rating)", "Skilled Worker"),
]


def register_csv(filler=1200):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Organisation Name", "Town/City", "County", "Type & Rating", "Route"])
    for r in REGISTER_ROWS:
        w.writerow(r)
    for i in range(filler):
        w.writerow([f"Filler Widgets {i:04d} Ltd", "Town", "", "Worker (A rating)", "Skilled Worker"])
    return buf.getvalue().encode("utf-8-sig")


def adzuna_job(i, title, company, location="London", desc="", smin=None, smax=None, predicted="0",
               created="2026-10-16T09:00:00Z", contract_type="permanent", contract_time="full_time"):
    return {
        "__CLASS__": "Adzuna::API::Response::Job", "id": str(i), "title": title,
        "company": {"display_name": company}, "location": {"display_name": location, "area": ["UK", location]},
        "description": desc, "salary_min": smin, "salary_max": smax, "salary_is_predicted": predicted,
        "created": created, "redirect_url": f"https://www.adzuna.co.uk/jobs/land/ad/{i}",
        "contract_type": contract_type, "contract_time": contract_time,
        "category": {"label": "IT Jobs", "tag": "it-jobs"},
    }


def reed_job(i, title, company, location="Bristol", desc="", smin=None, smax=None, date="16/10/2026", expires="20/11/2026"):
    return {"jobId": i, "employerId": 1000 + i, "employerName": company, "jobTitle": title, "locationName": location,
            "minimumSalary": smin, "maximumSalary": smax, "currency": "GBP", "expirationDate": expires, "date": date,
            "jobDescription": desc, "applications": 3, "jobUrl": f"https://www.reed.co.uk/jobs/job/{i}"}


ADZUNA_RESULTS = {
    "data analyst": [
        adzuna_job(101, "Junior Data Analyst", "Barclays", "London",
                   "Join our team using SQL, Python and Power BI. Visa sponsorship is available for this role.", 36000, 40000),
        adzuna_job(102, "Senior Data Analyst", "Monzo Bank", "London", "Lead analytics.", 60000, 70000),
        adzuna_job(103, "Data Analyst", "Acme Widgets Ltd", "Leeds", "Analyse sales data in Excel.", 35000, 38000),
        adzuna_job(104, "Data Analyst", "Aviva Insurance", "Norwich",
                   "Unfortunately we are unable to offer visa sponsorship for this position.", 36000, 39000),
        adzuna_job(105, "Data Analyst (SC Cleared)", "Barclays", "London", "Must hold SC clearance.", 45000, 50000),
        adzuna_job(106, "Data Analyst", "Nationwide Building Soc", "Swindon",
                   "Use SQL and Tableau to build dashboards. 1-2 years experience.", 30000, 32000),
        adzuna_job(107, "Graduate Data Analyst", "Hays", "London", "Our client, a leading bank...", 34000, 36000),
        adzuna_job(108, "Data Analyst", "Brightdata Insights", "Leeds",
                   "Python, R and SQL. 2 years' experience required.", None, None),
    ],
    "risk analyst": [
        adzuna_job(201, "Credit Risk Analyst", "Hargreaves Lansdown", "Bristol",
                   "Credit risk modelling with Python and SQL. Minimum 5 years experience in credit risk.", 45000, 55000),
        adzuna_job(202, "Fraud Risk Analyst", "Monzo", "Cardiff",
                   "Fraud detection, SQL and stakeholder management. We welcome candidates with or without visa sponsorship needs.", 38000, 42000),
    ],
}

REED_RESULTS = {
    "credit analyst": [
        reed_job(9001, "Credit Analyst", "Barclays Execution Services", "Glasgow",
                 "Assess credit applications ... (snippet)", 36000, 40000),
        reed_job(9002, "Credit Analyst", "Aviva", "York", "Part time role", 20, 25),
    ],
    "data scientist": [
        reed_job(9101, "Graduate Data Scientist", "Monzo Bank", "London",
                 "Machine learning, Python, scikit-learn ... (snippet)", 45000, 50000),
        reed_job(9102, "Data Scientist", "Acme Widgets", "London", "ML role", 50000, 60000),
    ],
}

REED_DETAILS = {
    9001: {"jobId": 9001, "jobDescription": "<p>Assess credit applications using SQL and Excel.</p><ul><li>Credit risk analysis</li><li>Stakeholder reporting</li></ul><p>We can offer Skilled Worker visa sponsorship.</p>",
           "yearlyMinimumSalary": 36000, "yearlyMaximumSalary": 40000, "contractType": "Permanent", "fullTime": True,
           "partTime": False, "expirationDate": "25/11/2026", "externalUrl": "https://careers.example.com/9001"},
    9101: {"jobId": 9101, "jobDescription": "<p>Build machine learning models in Python with scikit-learn and XGBoost. "
           "This is a 6 month fixed-term contract.</p>", "yearlyMinimumSalary": 45000, "yearlyMaximumSalary": 50000,
           "contractType": "Contract", "fullTime": True, "partTime": False, "expirationDate": "30/11/2026"},
}


def govuk_content(slug_updated=None):
    def handler(method, url, kw):
        if "register-of-licensed-sponsors-workers" in url:
            return FakeResponse(200, {"title": "Register of licensed sponsors: workers",
                                      "public_updated_at": "2026-10-16T09:00:00Z",
                                      "details": {"attachments": [{"url": "https://assets.publishing.service.gov.uk/media/abc/2026-10-16_-_Worker_and_Temporary_Worker.csv", "content_type": "text/csv"}]}})
        for slug, when in (slug_updated or {}).items():
            if slug in url:
                return FakeResponse(200, {"title": slug, "public_updated_at": when})
        return FakeResponse(200, {"title": "page", "public_updated_at": "2026-07-22T00:00:00Z"})
    return handler


class GitHubFake:
    def __init__(self):
        self.issues = []
        self.comments = []

    def __call__(self, method, url, kw):
        m = re.search(r"/repos/[^/]+/[^/]+/issues(?:/(\d+))?(/comments)?$", url)
        if not m:
            return FakeResponse(404, {})
        num, comments = m.group(1), m.group(2)
        if method == "GET" and not num:
            return FakeResponse(200, [i for i in self.issues if i["state"] == "open"])
        if method == "GET" and num:
            for i in self.issues:
                if i["number"] == int(num):
                    return FakeResponse(200, i)
            return FakeResponse(404, {})
        if method == "POST" and not num:
            issue = {"number": len(self.issues) + 1, "title": kw["json"]["title"], "state": "open", "body": kw["json"]["body"]}
            self.issues.append(issue)
            return FakeResponse(201, issue)
        if method == "POST" and comments:
            self.comments.append({"issue": int(num), "body": kw["json"]["body"]})
            return FakeResponse(201, {"id": len(self.comments)})
        return FakeResponse(400, {})


def make_session(adzuna=None, reed=None, details=None, register_ok=True, slug_updated=None, github=None, ntfy=None, adzuna_status=200):
    adzuna = ADZUNA_RESULTS if adzuna is None else adzuna
    reed = REED_RESULTS if reed is None else reed
    details = REED_DETAILS if details is None else details

    def adz(method, url, kw):
        if adzuna_status != 200:
            return FakeResponse(adzuna_status, {"exception": "AUTH_FAIL"})
        q = (kw.get("params") or {}).get("title_only", "")
        return FakeResponse(200, {"results": adzuna.get(q, []), "count": len(adzuna.get(q, []))})

    def reed_search(method, url, kw):
        q = (kw.get("params") or {}).get("keywords", "")
        return FakeResponse(200, {"results": reed.get(q, []), "totalResults": len(reed.get(q, []))})

    def reed_detail(method, url, kw):
        jid = int(url.rstrip("/").split("/")[-1])
        return FakeResponse(200, details.get(jid, {"jobId": jid}))

    def csv_file(method, url, kw):
        if not register_ok:
            return FakeResponse(503, {})
        return FakeResponse(200, content=register_csv())

    def ntfy_post(method, url, kw):
        if ntfy is not None:
            ntfy.append((url, kw))
        return FakeResponse(200, {"id": "x"})

    routes = [
        (r"api\.adzuna\.com", adz),
        (r"reed\.co\.uk/api/1\.0/search", reed_search),
        (r"reed\.co\.uk/api/1\.0/jobs/", reed_detail),
        (r"assets\.publishing\.service\.gov\.uk/.+\.csv", csv_file),
        (r"www\.gov\.uk/", govuk_content(slug_updated)),
        (r"api\.github\.com", github or GitHubFake()),
        (r"ntfy\.sh", ntfy_post),
    ]
    return FakeSession(routes)
