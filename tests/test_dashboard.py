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

        # 9. Empty site before the first run
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
