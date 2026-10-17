"""Browser tests for the dashboard (Playwright + Chromium). Run: python3 tests/test_dashboard.py"""
import base64
import functools
import http.server
import io
import json
import shutil
import sys
import tempfile
import threading
from pathlib import Path

import pypdf
from playwright.sync_api import sync_playwright

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import build_site_fixture  # noqa: E402

RAW = (HERE / "fixtures/sample-cv.txt").read_text()
results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def serve(root):
    handler = functools.partial(QuietHandler, directory=str(root))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}/"


def new_page(browser, url, width=1100, scheme="light", storage=None):
    ctx = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme, accept_downloads=True)
    if storage is not None:
        ctx.add_init_script(f"try {{ if (!sessionStorage.getItem('__seed')) {{ localStorage.setItem('sponsorRadar.v1', {json.dumps(json.dumps(storage))}); sessionStorage.setItem('__seed','1'); }} }} catch (e) {{}}")
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "Failed to load resource" not in m.text else None)
    page.route("https://fonts.googleapis.com/**", lambda r: r.abort())
    page.route("https://fonts.gstatic.com/**", lambda r: r.abort())
    page.goto(url)
    page.wait_for_selector("html[data-ready='1']", timeout=15000)
    return ctx, page, errors


def store(page):
    return page.evaluate("JSON.parse(localStorage.getItem('sponsorRadar.v1') || '{}')")


def pdf_from_download(dl):
    raw = Path(dl.path()).read_bytes()
    r = pypdf.PdfReader(io.BytesIO(raw))
    return r, "\n".join(p.extract_text() for p in r.pages)


def settings_tests(browser, url):
    import base64 as b64
    calls = []
    state = {"sha": None, "put_status": 201, "repo_status": 200}

    def handler(route):
        req = route.request
        u = req.url
        calls.append((req.method, u, req.headers.get("authorization"), req.post_data))
        if req.method == "GET" and u.endswith("/repos/someone/sponsor-radar"):
            return route.fulfill(status=state["repo_status"], json={"full_name": "someone/sponsor-radar"} if state["repo_status"] == 200 else {"message": "Bad credentials"},
                                 headers={"access-control-allow-origin": "*"})
        if "/contents/settings.json" in u and req.method == "GET":
            if state["sha"]:
                return route.fulfill(status=200, json={"sha": state["sha"], "content": ""}, headers={"access-control-allow-origin": "*"})
            return route.fulfill(status=404, json={"message": "Not Found"}, headers={"access-control-allow-origin": "*"})
        if "/contents/settings.json" in u and req.method == "PUT":
            if state["put_status"] in (200, 201):
                state["sha"] = "sha-" + str(len(calls))
            return route.fulfill(status=state["put_status"], json={"content": {"sha": state["sha"]}}, headers={"access-control-allow-origin": "*"})
        if req.method == "OPTIONS":
            return route.fulfill(status=204, headers={"access-control-allow-origin": "*", "access-control-allow-headers": "*", "access-control-allow-methods": "GET,PUT"})
        return route.fulfill(status=404, json={})

    ctx, page, errs = new_page(browser, url)
    page.route("https://api.github.com/**", handler)
    names = page.eval_on_selector_all("#targetsEditor [data-k=name]", "els => els.map(e => e.value)")
    check("settings form shows the current role types", names == ["Data & insight analyst", "Risk, credit & fraud analyst", "Junior data scientist"], names)
    check("settings form: years and places", page.input_value("#s_years") == "2" and page.is_checked("#s_anywhere") and page.is_disabled("#s_places"))
    check("settings form: search count", "11 job titles searched" in page.inner_text("#searchCount"), page.inner_text("#searchCount"))
    page.click("#saveSearchBtn")
    check("save needs a GitHub key first", "Connect the app to GitHub first" in page.inner_text("#searchMsg") and page.locator("#ghPanel details[open]").count() == 1)
    page.fill("#ghToken", "not-a-key"); page.click("#ghPanel button:has-text('Connect')")
    check("bad key format is caught", "doesn't look like a GitHub key" in page.inner_text("#ghMsg"))
    state["repo_status"] = 401
    page.fill("#ghToken", "github_pat_" + "A" * 40); page.click("#ghPanel button:has-text('Connect')")
    page.wait_for_function("document.getElementById('ghMsg') && document.getElementById('ghMsg').textContent.includes('didn')")
    check("refused key isn't kept", page.evaluate("localStorage.getItem('sponsorRadar.github')") is None)
    state["repo_status"] = 200
    page.fill("#ghToken", "github_pat_" + "B" * 40); page.click("#ghPanel button:has-text('Connect')")
    page.wait_for_function("document.getElementById('ghPanel').innerText.includes('Connected to GitHub')")
    check("connect stores the key", page.evaluate("localStorage.getItem('sponsorRadar.github')") == "github_pat_" + "B" * 40)
    # validation
    page.fill("#t0_name", "")
    page.click("#saveSearchBtn")
    check("validation: role type needs a name", "Give role type 1 a name" in page.inner_text("#searchMsg"))
    page.fill("#t0_name", "Data & insight analyst")
    page.uncheck("#s_anywhere"); page.fill("#s_places", "")
    page.click("#saveSearchBtn")
    check("validation: places needed when not anywhere", "Add at least one town or city" in page.inner_text("#searchMsg"))
    page.fill("#s_places", "Leeds, Manchester")
    page.click("#addTargetBtn")
    page.fill("#t3_name", "Pricing analyst"); page.fill("#t3_searches", "Pricing Analyst, actuarial analyst"); page.fill("#t3_min", "34000")
    page.fill("#s_years", "3")
    page.click("#saveSearchBtn")
    page.wait_for_function("document.getElementById('searchMsg').textContent.startsWith('Saved')", timeout=5000)
    puts = [c for c in calls if c[0] == "PUT"]
    body = json.loads(puts[-1][3]) if puts else {}
    saved = json.loads(b64.b64decode(body.get("content", "")).decode("utf-8")) if body else {}
    check("saved over the GitHub API with the key", puts and puts[-1][2] == "Bearer github_pat_" + "B" * 40 and body.get("branch") == "main" and "sha" not in body)
    check("saved settings content", len(saved.get("targets", [])) == 4 and saved["targets"][3] == {"name": "Pricing analyst", "searches": ["pricing analyst", "actuarial analyst"], "min_salary": 34000}
          and saved["locations"] == ["Leeds", "Manchester"] and saved["max_years_experience"] == 3 and saved["include_remote"] is True, saved)
    check("form shows the pending save", "used from the next search" in page.inner_text("#searchSource"))
    page.click("#saveSearchBtn")
    page.wait_for_function("document.querySelectorAll('#searchMsg').length && document.getElementById('searchMsg').textContent.startsWith('Saved')", timeout=5000)
    body2 = json.loads([c for c in calls if c[0] == "PUT"][-1][3])
    check("second save sends the file's sha", body2.get("sha", "").startswith("sha-"))
    state["put_status"] = 403
    page.click("#saveSearchBtn")
    page.wait_for_function("document.getElementById('searchMsg').classList.contains('err')", timeout=5000)
    check("permission error is explained", "Contents set to Read and write" in page.inner_text("#searchMsg"))
    state["put_status"] = 401
    page.click("#saveSearchBtn")
    page.wait_for_function("document.getElementById('searchMsg').textContent.includes('expired')", timeout=5000)
    check("expired key is removed", page.evaluate("localStorage.getItem('sponsorRadar.github')") is None and "How to create the key" in page.inner_text("#ghPanel"))
    with page.expect_download() as d:
        page.click("#backupBtn")
    check("backup never contains the GitHub key", "github_pat_" not in Path(d.value.path()).read_text())
    page.reload(); page.wait_for_selector("html[data-ready='1']")
    check("pending settings survive a reload", len(page.eval_on_selector_all("#targetsEditor .target", "e => e")) == 4 and page.input_value("#s_places") == "Leeds, Manchester")
    check("no errors in settings flows", not errs, "; ".join(errs[:3]))
    ctx.close()


def cv_reader_tests(browser, url):
    """The CV reader: headings that put the job title and organisation on one line, bullets, hyphens, older outlines."""
    ctx, page, errs = new_page(browser, url)
    outline = lambda text: page.evaluate("t => window.__radar.rawToOutline(t).outline", text)
    parsed = lambda text: page.evaluate("t => window.__radar.parseOutline(t)", text)
    job = "## Data Analytics Intern | Brightwave | Remote | 03/2025 – 05/2025"
    cases = [
        ("one line, dates after", "EXPERIENCE\nData Analytics Intern Brightwave (Remote) 03/2025 – 05/2025\n• Built dashboards\n", job),
        ("one line, dates on the next line", "EXPERIENCE\nData Analytics Intern Brightwave (Remote)\n03/2025 – 05/2025\n• Built dashboards\n", job),
        ("dates first", "EXPERIENCE\n03/2025 – 05/2025\nData Analytics Intern Brightwave (Remote)\n• Built dashboards\n", job),
        ("tabs", "EXPERIENCE\nData Analytics Intern\tBrightwave (Remote)\t03/2025 – 05/2025\n• Built dashboards\n", job),
        ("organisation first, separated", "EXPERIENCE\nBrightwave | Data Analytics Intern | Remote | 03/2025 – 05/2025\n• Built dashboards\n", job),
        ("organisation on the next line", "EXPERIENCE\nData Analytics Intern 03/2025 – 05/2025\nBrightwave (Remote)\n• Built dashboards\n", job),
        ("title in brackets kept, organisation after", "EXPERIENCE\nCustomer Service Assistant Tesco Stores Ltd (Part-time) 2021 – 2023\n• Served customers\n",
         "## Customer Service Assistant (Part-time) | Tesco Stores Ltd | 2021 – 2023"),
        ("head of", "EXPERIENCE\nHead of Data Brightwave 2021 – 2023\n• Led a team\n", "## Head of Data | Brightwave | 2021 – 2023"),
        ("company name first", "EXPERIENCE\nLloyds Banking Group Data Analyst 2021 – 2023\n• Built reports\n", "## Data Analyst | Lloyds Banking Group | 2021 – 2023"),
        ("a title alone stays a title", "EXPERIENCE\nPricing Analyst 2021 – 2023\n• Built models\n", "## Pricing Analyst | 2021 – 2023"),
        ("a title with its field stays a title", "EXPERIENCE\nAssociate Director Finance 2021 – 2023\n• Led budgets\n", "## Associate Director Finance | 2021 – 2023"),
        ("place on its own line", "EXPERIENCE\nData Analytics Intern 03/2025 – 05/2025\nBrightwave\nRemote\n• Built dashboards\n", job),
        ("degree and university", "EDUCATION\nMSc Business Analytics (Merit) University of Leicester 2023 – 2024\n",
         "## MSc Business Analytics (Merit) | University of Leicester | 2023 – 2024"),
        ("degree and college", "EDUCATION\nBE Electronics and Communication Engineering PSG College of Technology 2018 – 2022\n",
         "## BE Electronics and Communication Engineering | PSG College of Technology | 2018 – 2022"),
        ("university first", "EDUCATION\nUniversity of Leicester | MSc Business Analytics | 2023 – 2024\n", "## MSc Business Analytics | University of Leicester | 2023 – 2024"),
        ("named university", "EDUCATION\nMSc Data Science Queen Mary University of London 2023 – 2024\n", "## MSc Data Science | Queen Mary University of London | 2023 – 2024"),
    ]
    for name, text, want in cases:
        got = outline(text)
        check(f"CV reader: {name}", want + "\n" in got, got)
    got = outline("PROFILE\nUsing statistical models on real-\nworld problems, with short- and long-term goals.\n")
    check("CV reader: a word broken at a hyphen is joined", "on real-world problems, with short- and long-term goals." in got, got)
    got = outline("EXPERIENCE\nData Analytics Intern, Brightwave 2024 – 2025\n Built dashboards\n Cleaned data\n")
    check("CV reader: Word's Symbol bullets", "- Built dashboards\n- Cleaned data" in got, got)
    got = outline("EXPERIENCE\nData Analytics Intern, Brightwave\t2024 – 2025\n• Built dashboards for 40 managers\n"
                  "Business Analyst Intern Kestrel Data Solutions Pvt Ltd (Chennai, India)\n01/2023 – 06/2023\n• Built SQL reports\n")
    check("CV reader: a long heading after a point starts the next job",
          "- Built dashboards for 40 managers\n\n## Business Analyst Intern | Kestrel Data Solutions Pvt Ltd | Chennai, India | 01/2023 – 06/2023" in got, got)
    got = outline("EXPERIENCE\nData Analytics Intern, Brightwave 2024 – 2025\n• Built dashboards for 40 managers, cutting\nreporting time by half\n• Cleaned data\n")
    check("CV reader: a wrapped point is joined", "- Built dashboards for 40 managers, cutting reporting time by half\n- Cleaned data" in got, got)
    got = outline("EXPERIENCE\nData Analytics Intern 2024 – 2025\nRemote\n• Built dashboards\n")
    check("CV reader: a place without an organisation keeps its slot", "## Data Analytics Intern | | Remote | 2024 – 2025" in got, got)
    check("CV reader: that outline reads back", parsed(got)["experience"][0]["org"] == "" and parsed(got)["experience"][0]["place"] == "Remote")

    # Outlines saved by the earlier reader are repaired when read
    cv = parsed("# Profile\nWorking on real- world problems.\n\n# Experience\n## Data Analytics Intern Brightwave (Remote) | 03/2025 – 05/2025\n- Built dashboards\n"
                "## Business Analyst Intern Kestrel Data Solutions Pvt Ltd (Chennai, India | 01/2023 – 06/2023\n- Built reports\n\n"
                "# Education\n## MSc Business Analytics (Merit) University of Leicester | 2023 – 2024\n")
    e0, e1, d0 = cv["experience"][0], cv["experience"][1], cv["education"][0]
    check("saved outline: one-line heading split", (e0["title"], e0["org"], e0["place"]) == ("Data Analytics Intern", "Brightwave", "Remote"), e0)
    check("saved outline: unclosed bracket", (e1["title"], e1["org"], e1["place"]) == ("Business Analyst Intern", "Kestrel Data Solutions Pvt Ltd", "Chennai, India"), e1)
    check("saved outline: degree and university split", (d0["degree"], d0["school"]) == ("MSc Business Analytics (Merit)", "University of Leicester"), d0)
    check("saved outline: hyphen joined", cv["profile"] == "Working on real-world problems.", cv["profile"])

    # The preview names the jobs that still need fixing
    page.fill("#cvOutline", "# Experience\n## Data Analytics Intern Brightwave (Remote) | 03/2025 – 05/2025\n- Built dashboards\n## Freelance work | 2024\n- Built sites\n")
    page.click("#cvSave")
    prev = page.inner_text("#cvPreview")
    check("preview: split heading shown as title · organisation", "Data Analytics Intern · Brightwave" in prev and "Remote · 03/2025 – 05/2025" in prev, prev[:300])
    check("preview: names the job it can't split", "“Freelance work”" in prev and "## Job title | Organisation | Place | Dates" in prev and "organisation missing" in prev, prev[:400])
    check("preview: no warning for the split job", "Data Analytics Intern Brightwave" not in prev, prev[:400])

    # Uploads in common layouts
    want = {
        "layout-cv.pdf": True, "layout-cv.docx": True, "layout-cv-browser.pdf": True,
    }
    for name in want:
        page.evaluate("document.getElementById('cvOutline').value = ''")
        page.set_input_files("#cvFile", str(HERE / "fixtures" / name))
        page.wait_for_function("document.getElementById('cvOutline').value.includes('## ')", timeout=20000)
        out = page.input_value("#cvOutline")
        ok = all(x in out for x in [
            "## Data Analytics Intern | Brightwave | Remote | 03/2025 – 05/2025",
            "- Interpreted sales datasets, delivering insights that informed pricing decisions\n- Designed and deployed 8 interactive dashboards in Power BI",
            "## Business Analyst Intern | Kestrel Data Solutions Pvt Ltd | Chennai, India | 01/2023 – 06/2023",
            "- Built weekly SQL reports for 4 teams\n- Automated data cleaning in Python, saving 5 hours a week",
            "## MSc Business Analytics (Merit) | University of Leicester | 09/2023 – 09/2024",
            "## BE Electronics and Communication Engineering | PSG College of Technology | 06/2018 – 05/2022",
            "statistical models to real-world problems"])
        check(f"upload {name}: titles, organisations, points and hyphens", ok, out[:700])
    raw = page.input_value("#cvRaw")
    check("upload: no blank line between every line", "\n\n\n" not in raw and raw.count("\n\n") <= 2, repr(raw[:200]))
    check("CV reader: no errors", not errs, "; ".join(errs[:3]))
    ctx.close()


def main():
    tmp = Path(tempfile.mkdtemp())
    site = tmp / "site"
    n = build_site_fixture.build(site)
    httpd, url = serve(site)
    with sync_playwright() as p:
        browser = p.chromium.launch()

        # 1. First load
        ctx, page, errs = new_page(browser, url)
        check("loads without errors", not errs, "; ".join(errs[:3]))
        check("header shows the last search", "Last search" in page.inner_text("#runText") and f"{n} on the radar" in page.inner_text("#runText"), page.inner_text("#runText"))
        check("missing Reed key notice", "Reed isn't searched yet" in page.inner_text("#notices"))
        check("get started shown", page.is_visible("#start"))
        check("30 cards drawn", page.eval_on_selector_all("#cards .card", "e => e.length") == 30)
        chips = page.inner_text("#chips")
        check("chips count all roles", f"To review\n{n}" in chips or f"To review {n}" in chips.replace("\n", " "), chips)
        page.click("#more")
        check("show more", page.eval_on_selector_all("#cards .card", "e => e.length") == 60)
        top_title = page.inner_text("#cards .card h3")
        check("best match puts the strongest role first", top_title == "Junior Data Analyst", top_title)
        page.fill("#q", "Kestrel")
        page.wait_for_timeout(250)
        titles = page.eval_on_selector_all("#cards .card", "els => els.map(e => e.innerText)")
        check("search filters", titles and all("Kestrel" in t for t in titles), str(len(titles)))
        page.fill("#q", "")
        page.select_option("#sponsorFilter", "Likely")
        page.wait_for_timeout(150)
        check("likely-only filter", all("Likely sponsor" in t for t in page.eval_on_selector_all("#cards .card", "els => els.map(e => e.innerText)")))
        page.select_option("#sponsorFilter", "all")
        page.check("#salaryOnly")
        page.wait_for_timeout(150)
        check("salary filter", all("meets £" in t for t in page.eval_on_selector_all("#cards .card", "els => els.map(e => e.innerText)")))
        page.uncheck("#salaryOnly")
        page.select_option("#sort", "closing")
        page.wait_for_timeout(150)
        check("sort by closing date", "Closes" in page.inner_text("#cards .card"))
        page.select_option("#sort", "best")

        # 2. Statuses, undo, closed
        first = page.locator("#cards .card").first
        name = first.locator("h3").inner_text()
        first.locator("button", has_text="Not for me").click()
        page.wait_for_timeout(150)
        check("not for me moves the role", page.locator("#cards .card h3", has_text=name).count() == 0 or name not in page.inner_text("#cards .card h3"))
        page.locator("#toast button", has_text="Undo").click()
        page.wait_for_timeout(150)
        check("undo restores it", page.inner_text("#cards .card h3") == name)
        ids = page.evaluate("() => [...document.querySelectorAll('#cards .card')].slice(0, 8).map(c => c.querySelector('h3').innerText)")
        cards = page.locator("#cards .card")
        for i in range(3):
            cards.nth(0).locator("button", has_text="Interested").click()
            page.wait_for_timeout(80)
        for i in range(3):
            page.locator("#cards .card").nth(0).locator("button", has_text="Not for me").click()
            page.wait_for_timeout(80)
        s = store(page)
        labels = [v["s"] for v in s["statuses"].values()]
        check("statuses saved", labels.count("interested") == 3 and labels.count("notforme") == 3, labels)
        check("model trained after 3+3 choices", "Learned from 6 of your choices" in page.inner_text("#modelBody"), page.inner_text("#modelBody")[:200])
        check("taste meter appears", page.locator("#cards .card .meter", has_text="Your taste").count() > 0)
        page.click("#chips button:has-text('Interested')")
        check("interested chip lists them", page.eval_on_selector_all("#cards .card", "e => e.length") == 3)
        page.click("#chips button:has-text('Closed')")
        check("closed chip lists not-for-me", page.eval_on_selector_all("#cards .card", "e => e.length") == 3)
        page.click("#chips button:has-text('To review')")

        # 3. CV: paste, outline, save, fit
        page.fill("#cvRaw", RAW)
        page.click("#toOutline")
        outline = page.input_value("#cvOutline")
        check("outline built", "## Data Analyst Intern | Northgate Insights | Remote | May 2025 – Jun 2025" in outline and "## Junior Analyst | Leeds Community Trust | Leeds | Jul 2022 – Jul 2023" in outline, outline[:400])
        check("wrapped bullet joined", "cutting report preparation time by 6 hours a week" in outline)
        check("details filled from CV", page.input_value("#d_name") == "Alex Morgan" and page.input_value("#d_email") == "alex.morgan@example.com" and page.input_value("#d_home") == "Leeds")
        page.click("#cvSave")
        check("CV saved", "Saved" in page.inner_text("#cvMsg"))
        check("fit meter appears", page.locator("#cards .card .meter", has_text="fit with your CV").count() > 0)
        check("matched skills highlighted", page.locator("#cards .sk.have").count() > 0)
        check("CV preview", "Northgate Insights" in page.inner_text("#cvPreview") and "Python" in page.inner_text("#cvPreview"))
        page.click("#detailsForm button[type=submit]")
        check("details saved", page.inner_text("#detailsMsg") == "Saved" and store(page)["details"]["name"] == "Alex Morgan")
        check("get started done after CV, details and marks", page.is_hidden("#start"))

        # 4. CV & letter
        page.click("#chips button:has-text('Interested')")
        card = page.locator("#cards .card").first
        role = card.locator("h3").inner_text()
        company = card.locator(".card-top span").first.inner_text().split(" · ")[0]
        card.locator("button", has_text="CV & letter").click()
        page.wait_for_selector("#dlg[open] .sheet")
        sheet = page.inner_text("#dlg .sheet")
        cv_bullets = [l[2:].strip() for l in outline.splitlines() if l.startswith("- ")]
        shown = page.eval_on_selector_all("#dlg .sheet li", "els => els.map(e => e.innerText.trim())")
        check("CV uses only bullets from the CV", shown and all(b in cv_bullets for b in shown), [b for b in shown if b not in cv_bullets][:2])
        check("CV has name, contact and headline", "Alex Morgan" in sheet and "alex.morgan@example.com" in sheet and "·" in page.inner_text("#dlg .sheet .hl"))
        page.click("#dlgBody [role=tab]:has-text('Cover letter')")
        letter = page.inner_text("#dlg .sheet")
        check("letter names the role and company", role in letter and company in letter, letter[:300])
        check("letter ends with the sponsorship sentence", "I currently have permission to work in the UK and would require Skilled Worker visa sponsorship for this role going forward." in letter)
        check("letter has no unfilled placeholders", "{" not in letter)
        check("letter uses an MSc", "an MSc Data Science (Distinction) from the University of Leeds" in letter, letter[:400])
        para = page.locator("[data-path='letter.paragraphs.0']")
        para.click(); page.keyboard.press("Control+End"); page.keyboard.type(" Extra sentence."); page.locator("[data-path='letter.greeting']").click()
        page.wait_for_timeout(200)
        saved = [v for v in store(page)["docs"].values()]
        check("edit saved for this role", saved and saved[0]["letter"]["paragraphs"][0].endswith("Extra sentence."))
        with page.expect_download() as d1:
            page.click("text=Download CV (PDF)")
        r, text = pdf_from_download(d1.value)
        check("CV PDF file name", d1.value.suggested_filename == f"Alex Morgan CV - {company}.pdf", d1.value.suggested_filename)
        check("CV PDF is one page with real text", len(r.pages) == 1 and "Northgate Insights" in text and "Alex Morgan" in text, f"{len(r.pages)} pages")
        with page.expect_download() as d2:
            page.click("text=Download cover letter (PDF)")
        r2, text2 = pdf_from_download(d2.value)
        check("letter PDF one page, with the edit", len(r2.pages) == 1 and "Extra sentence." in text2 and "Yours faithfully" in text2)
        page.click("text=Rebuild from my CV")
        page.wait_for_timeout(150)
        check("rebuild drops the edit", "Extra sentence." not in page.inner_text("#dlg .sheet") and not store(page)["docs"])
        page.keyboard.press("Escape")
        check("Esc closes the dialog", not page.evaluate("document.getElementById('dlg').open"))
        check("no errors so far", not errs, "; ".join(errs[:3]))

        # 5. Backup, clear, restore
        page.click("#chips button:has-text('To review')")
        with page.expect_download() as d3:
            page.click("#backupBtn")
        backup = Path(d3.value.path()).read_text()
        page.click("#clearBtn"); page.click("#clearYes")
        check("clear wipes this browser", not store(page).get("statuses") and page.input_value("#cvOutline") == "")
        bk = tmp / "backup.json"; bk.write_text(backup)
        page.set_input_files("#restoreFile", str(bk))
        page.wait_for_timeout(300)
        check("restore brings everything back", len(store(page)["statuses"]) == 6 and "Northgate" in page.input_value("#cvOutline") and "Restored" in page.inner_text("#dataMsg"))
        st = store(page)
        ctx.close()

        # 6. A role that left the radar keeps its status
        gone_id = next(k for k, v in st["statuses"].items() if v["s"] == "interested")
        site2 = tmp / "site2"
        build_site_fixture.build(site2, drop={gone_id})
        httpd2, url2 = serve(site2)
        ctx, page, errs = new_page(browser, url2, storage=st)
        page.click("#chips button:has-text('Interested')")
        check("role no longer listed is kept under its status", "No longer on the radar" in page.inner_text("#cards") and page.eval_on_selector_all("#cards .card", "e => e.length") == 3)
        check("still 6 labelled examples", "Learned from 6 of your choices" in page.inner_text("#modelBody"))
        ctx.close(); httpd2.shutdown()

        # 7. Uploads
        ctx, page, errs = new_page(browser, url)
        page.set_input_files("#cvFile", str(HERE / "fixtures/sample-cv.pdf"))
        page.wait_for_function("document.getElementById('cvOutline').value.includes('## ')", timeout=20000)
        out_pdf = page.input_value("#cvOutline")
        check("PDF upload builds an outline", "Northgate Insights" in out_pdf and "# Experience" in out_pdf and "Leeds Community Trust" in out_pdf, out_pdf[:300])
        page.set_input_files("#cvFile", str(HERE / "fixtures/sample-cv.docx"))
        page.wait_for_function("document.getElementById('cvRaw').value.includes('Hindi')", timeout=20000)
        out_docx = page.input_value("#cvOutline")
        check("Word upload builds an outline", "## Data Analyst Intern | Northgate Insights | Remote | May 2025 – Jun 2025" in out_docx and "- Designed 12 interactive" in out_docx, out_docx[:400])
        check("no errors on uploads", not errs, "; ".join(errs[:3]))
        ctx.close()

        # 7b. The CV reader on common layouts
        cv_reader_tests(browser, url)

        # 8. Phone width and dark mode
        ctx, page, errs = new_page(browser, url, width=375, scheme="dark", storage=st)
        check("phone: no sideways scroll", page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), page.evaluate("[document.documentElement.scrollWidth, innerWidth]"))
        check("dark background", page.evaluate("getComputedStyle(document.body).backgroundColor") == "rgb(15, 23, 22)")
        page.screenshot(path=str(tmp / "phone.png"), full_page=False)
        page.locator("#cards .card").first.locator("button", has_text="CV & letter").click()
        page.wait_for_selector("#dlg[open] .sheet")
        check("phone: dialog fills the screen", page.evaluate("document.getElementById('dlg').getBoundingClientRect().width") >= 374)
        shutil.copy(tmp / "phone.png", "/tmp/claude-0/radar-phone.png")
        ctx.close()

        # 9. Search settings saved to GitHub (GitHub API mocked)
        settings_tests(browser, url)

        # 10. Empty site before the first run
        empty = tmp / "empty"
        shutil.copytree(site, empty, ignore=shutil.ignore_patterns("data"))
        httpd3, url3 = serve(empty)
        ctx, page, errs = new_page(browser, url3)
        check("before the first run: friendly notice", "No search has finished yet" in page.inner_text("#notices") and "after the first search" in page.inner_text("#cards"))
        check("before the first run: no errors", not errs, "; ".join(errs[:3]))
        ctx.close(); httpd3.shutdown()
        browser.close()
    httpd.shutdown()
    shutil.rmtree(tmp, ignore_errors=True)
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    for f in failed:
        print("FAILED:", f[0], f[2])
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
