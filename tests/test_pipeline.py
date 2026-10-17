import json
import shutil
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

import yaml  # noqa: E402

from radar import rules  # noqa: E402
from radar.main import run  # noqa: E402
from radar.notify import build_message, dashboard_url  # noqa: E402
from radar.scoring import SkillExtractor, TargetClassifier, sponsor_evidence  # noqa: E402
from radar.sources import adzuna_budget, all_searches  # noqa: E402
from radar.sponsors import SponsorIndex, clean_name, read_csv_text  # noqa: E402
from radar.util import UK, annual_salary, clean_text, in_quiet_hours, parse_date  # noqa: E402

import fakes  # noqa: E402

CFG = yaml.safe_load((ROOT / "config.yml").read_text())


def index():
    return SponsorIndex(read_csv_text(fakes.register_csv(filler=50)))


class SponsorMatching(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.idx = index()

    def test_clean_name(self):
        self.assertEqual(clean_name("Barclays Bank UK PLC"), "barclays bank")
        self.assertEqual(clean_name("The Co-operative Bank p.l.c."), "operative bank p l c")
        self.assertEqual(clean_name("Marks & Spencer Group plc"), "marks spencer")

    def test_exact(self):
        m = self.idx.match("Monzo Bank Ltd")
        self.assertEqual(m.kind, "exact")
        self.assertEqual(m.sponsor.rating, "A")

    def test_prefix_brand_name(self):
        m = self.idx.match("Barclays")
        self.assertEqual(m.kind, "prefix")
        self.assertEqual(m.sponsor.name, "Barclays Bank UK PLC")  # the larger licence wins
        self.assertTrue(m.sponsor.large())

    def test_parent_name(self):
        m = self.idx.match("Aviva Insurance")
        self.assertEqual(m.kind, "prefix")
        self.assertEqual(m.sponsor.name, "Aviva Plc")

    def test_trading_name(self):
        m = self.idx.match("Brightdata Insights")
        self.assertEqual(m.kind, "exact")
        self.assertEqual(m.sponsor.rating, "B")

    def test_similar(self):
        m = self.idx.match("Nationwide Building Soc")
        self.assertEqual(m.kind, "similar")
        self.assertGreaterEqual(m.similarity, 0.82)

    def test_no_match_and_route_filter(self):
        self.assertEqual(self.idx.match("Acme Widgets Ltd").kind, "none")
        self.assertEqual(self.idx.match("Charity Only Org").kind, "none")  # not a Skilled Worker sponsor
        self.assertEqual(self.idx.match("Data").kind, "none")             # too generic to trust

    def test_evidence_levels(self):
        likely = sponsor_evidence(self.idx.match("Barclays").as_dict(), None)
        self.assertEqual(likely[1], "Likely")
        possible = sponsor_evidence(self.idx.match("Monzo Bank").as_dict(), None)
        self.assertEqual(possible[1], "Possible")
        mention = sponsor_evidence({"match": "none"}, "visa sponsorship is available")
        self.assertEqual(mention[1], "Likely")
        self.assertIsNone(sponsor_evidence({"match": "none"}, None)[1])


class Rules(unittest.TestCase):
    def test_negative(self):
        for t in ["We are unable to offer visa sponsorship.", "Unfortunately we cannot sponsor visas",
                  "No sponsorship available", "Sponsorship is not available for this role",
                  "You must have the unrestricted right to work in the UK",
                  "Applicants must be able to work without the need for sponsorship",
                  "Candidates must not require sponsorship now or in the future",
                  "You must have full right to work in the UK"]:
            self.assertTrue(rules.sponsorship_negative(t), t)
        for t in ["We welcome applicants with or without sponsorship", "Visa sponsorship is available",
                  "You must have the right to work in the UK"]:
            self.assertFalse(rules.sponsorship_negative(t), t)

    def test_positive(self):
        for t in ["Visa sponsorship is available for the right candidate", "We can offer Skilled Worker visa sponsorship",
                  "We are able to sponsor your visa", "Certificate of sponsorship provided", "Visa sponsorship: yes"]:
            self.assertTrue(rules.sponsorship_positive(t), t)
        self.assertFalse(rules.sponsorship_positive("We cannot offer visa sponsorship"))

    def test_clearance_and_residency(self):
        self.assertTrue(rules.needs_clearance("Must be SC cleared"))
        self.assertTrue(rules.needs_clearance("Eligible for security clearance"))
        self.assertFalse(rules.needs_clearance("Clear communication skills"))
        self.assertTrue(rules.residency_requirement("You must have 5 years UK residency"))

    def test_years(self):
        self.assertEqual(rules.years_required("Minimum 3 years experience in SQL"), 3)
        self.assertEqual(rules.years_required("2-3 years' experience with Python"), 2)
        self.assertEqual(rules.years_required("three years of commercial experience"), 3)
        self.assertIsNone(rules.years_required("5 years experience would be desirable"))
        self.assertIsNone(rules.years_required("Graduate role, training provided"))

    def test_contract_and_required(self):
        self.assertTrue(rules.short_contract("This is a 6 month fixed-term contract"))
        self.assertTrue(rules.short_contract("Maternity cover role"))
        self.assertFalse(rules.short_contract("12 month contract with a view to extend"))
        self.assertFalse(rules.short_contract("6 months probation"))
        self.assertEqual(rules.required_missing("ACCA qualified essential", ["acca"]), "acca")
        self.assertIsNone(rules.required_missing("ACCA or working towards it is desirable", ["acca"]))
        self.assertIsNone(rules.required_missing("Experience with dbt is a nice to have", ["dbt"]))

    def test_title_and_agency(self):
        self.assertEqual(rules.title_excluded("Senior Data Analyst", CFG["exclude_title_words"]), "senior")
        self.assertEqual(rules.title_excluded("Data Analyst - Part Time", CFG["exclude_title_words"]), "part time")
        self.assertIsNone(rules.title_excluded("Junior Data Analyst", CFG["exclude_title_words"]))
        self.assertTrue(rules.is_agency("eFinancialCareers"))
        self.assertTrue(rules.is_agency("Totaljobs"))
        self.assertEqual(rules.employer_in_title("Enterprise Risk Management Analyst - Border to Coast Pensions Partnership"), "Border to Coast Pensions Partnership")
        self.assertIsNone(rules.employer_in_title("Data Analyst - London"))
        self.assertIsNone(rules.employer_in_title("Data Analyst - 12 month contract"))
        self.assertIsNone(rules.employer_in_title("Data Analyst"))
        self.assertEqual(rules.title_excluded("QA Analyst Risk IT", CFG["exclude_title_words"]), "qa")
        self.assertEqual(rules.title_excluded("Operational Risk Analyst AVP", CFG["exclude_title_words"]), "avp")
        self.assertIsNone(rules.title_excluded("Business Intelligence Analyst", CFG["exclude_title_words"]))
        self.assertTrue(rules.is_agency("Hays"))
        self.assertTrue(rules.is_agency("Spectrum IT Recruitment Ltd"))
        self.assertFalse(rules.is_agency("Monzo Bank"))

    def test_salary(self):
        self.assertEqual(rules.check_salary({"salary_min": 34000, "salary_max": 38000}, 33400), (True, None))
        self.assertFalse(rules.check_salary({"salary_min": 30000, "salary_max": 36000}, 33400)[0])
        ok, flag = rules.check_salary({"salary_min": None, "salary_max": None}, 33400)
        self.assertIsNone(ok)
        self.assertIn("£33,400", flag)
        self.assertEqual(annual_salary(20), 39000)
        self.assertEqual(annual_salary(200), 46000)
        self.assertEqual(annual_salary("35,000"), 35000)


class Models(unittest.TestCase):
    def test_classifier(self):
        clf = TargetClassifier(CFG["targets"])
        fam, sim = clf.classify("Junior Data Analyst")
        self.assertEqual(fam, "Data & insight analyst")
        self.assertGreater(sim, 0.9)
        self.assertEqual(clf.classify("Credit Risk Analyst")[0], "Risk, credit & fraud analyst")
        self.assertEqual(clf.classify("Graduate Data Scientist (Python)")[0], "Junior data scientist")
        self.assertEqual(clf.classify("BI & Reporting Analyst - London")[0], "Data & insight analyst")
        self.assertLess(clf.classify("Sales Assistant")[1], CFG["min_title_match"])
        self.assertGreaterEqual(clf.classify("Financial Crime Analyst")[1], CFG["min_title_match"])
        # The gate: "analyst" alone isn't enough
        for t in ["MES Business Analyst", "Cyber Business Analyst", "Business Analyst", "HR Analyst"]:
            self.assertIsNone(clf.classify(t)[0], t)
        self.assertEqual(clf.classify("Enterprise Risk Management Analyst - Border to Coast")[0], "Risk, credit & fraud analyst")
        self.assertEqual(clf.classify("Business Intelligence Analyst")[0], "Data & insight analyst")
        self.assertEqual(clf.classify("Customer Insights Analyst")[0], "Data & insight analyst")

    def test_skills(self):
        sk = SkillExtractor(ROOT / "docs/skills.json")
        found = sk.find("Python, R and SQL with Power BI dashboards; R&D background")
        for s in ["Python", "R", "SQL", "Power BI", "Dashboards"]:
            self.assertIn(s, found)
        self.assertNotIn("R", sk.find("Our R&D team"))
        self.assertNotIn("SAS", sk.find("We use Python. Sas is not needed"))


class Util(unittest.TestCase):
    def test_text_and_dates(self):
        self.assertEqual(clean_text("<p>Hello&nbsp;<b>world</b></p><ul><li>One</li></ul>"), "Hello world\n• One")
        self.assertEqual(parse_date("16/10/2026"), date(2026, 10, 16))
        self.assertEqual(parse_date("2026-10-16T09:00:00Z"), date(2026, 10, 16))
        self.assertIsNone(parse_date("soon"))

    def test_quiet_hours(self):
        at = lambda h, m: datetime(2026, 10, 17, h, m, tzinfo=UK)
        self.assertTrue(in_quiet_hours(at(23, 0), "22:30", "07:00"))
        self.assertTrue(in_quiet_hours(at(3, 0), "22:30", "07:00"))
        self.assertFalse(in_quiet_hours(at(7, 0), "22:30", "07:00"))
        self.assertFalse(in_quiet_hours(at(12, 0), "22:30", "07:00"))

    def test_adzuna_budget(self):
        src = CFG["sources"]["adzuna"]
        state = {}
        self.assertEqual(adzuna_budget(src, state, date(2026, 10, 17)), 6)
        state["adzuna_usage"].update(day_calls=118)
        self.assertEqual(adzuna_budget(src, state, date(2026, 10, 17)), 2)
        self.assertEqual(adzuna_budget(src, state, date(2026, 10, 18)), 6)  # new day resets
        state["adzuna_usage"].update(month_calls=2400)
        self.assertEqual(adzuna_budget(src, state, date(2026, 10, 18)), 0)
        self.assertEqual(len(all_searches(CFG)), 11)


class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        cfg = dict(CFG)
        cfg["sources"] = json.loads(json.dumps(CFG["sources"]))
        cfg["sources"]["adzuna"]["calls_per_run"] = 20
        cfg["sources"]["adzuna"]["enabled"] = True   # tested even while switched off in config.yml
        (self.dir / "config.yml").write_text(yaml.safe_dump(cfg))
        (self.dir / "docs").mkdir()
        shutil.copy(ROOT / "docs/skills.json", self.dir / "docs/skills.json")
        self.env = {"ADZUNA_APP_ID": "id", "ADZUNA_APP_KEY": "key", "REED_API_KEY": "reed",
                    "GITHUB_TOKEN": "t", "GITHUB_REPOSITORY": "Someone/sponsor-radar", "GITHUB_REPOSITORY_OWNER": "Someone",
                    "NTFY_TOPIC": "my-topic"}

    def tearDown(self):
        shutil.rmtree(self.dir)

    def read(self, rel):
        return json.loads((self.dir / rel).read_text())

    def test_full_run(self):
        gh, pushes = fakes.GitHubFake(), []
        sess = fakes.make_session(github=gh, ntfy=pushes)
        now = datetime(2026, 10, 17, 9, 17, tzinfo=UK)
        code = run(self.dir, env=self.env, now=now, session=sess, sleep=False)
        self.assertEqual(code, 0)
        jobs = {j["id"]: j for j in self.read("docs/data/jobs.json")["jobs"]}
        meta = self.read("docs/data/meta.json")

        # Kept
        self.assertIn("adzuna:101", jobs)            # Barclays, sponsorship mentioned
        self.assertEqual(jobs["adzuna:101"]["sponsor"]["level"], "Likely")
        self.assertIn("SQL", jobs["adzuna:101"]["skills"])
        self.assertIn("adzuna:108", jobs)            # trading-name sponsor, salary not stated
        self.assertIsNone(jobs["adzuna:108"]["salary"]["ok"])
        self.assertTrue(any("not stated" in f for f in jobs["adzuna:108"]["flags"]))
        self.assertIn("adzuna:202", jobs)            # "with or without sponsorship" is fine
        self.assertIn("reed:9001", jobs)             # full Reed advert fetched, sponsorship offered
        self.assertTrue(jobs["reed:9001"]["full_text"])
        self.assertEqual(jobs["reed:9001"]["url"], "https://careers.example.com/9001")
        self.assertEqual(jobs["reed:9001"]["closes"], "2026-11-25")
        # A job board's advert is matched on the employer named in the title
        self.assertIn("reed:9201", jobs)
        self.assertEqual(jobs["reed:9201"]["company"], "Border to Coast Pensions Partnership (via eFinancialCareers)")
        self.assertEqual(jobs["reed:9201"]["sponsor"]["register_name"], "Border to Coast Pensions Partnership Limited")
        self.assertNotIn("reed:9202", jobs)   # "Business Analyst" isn't one of the targets
        self.assertNotIn("reed:9203", jobs)   # posted more than 21 days ago
        self.assertNotIn("reed:9204", jobs)   # "Power BI" in the title is a skill, not the employer

        # Rejected, for the right reasons
        for jid in ["adzuna:102", "adzuna:103", "adzuna:104", "adzuna:105", "adzuna:106", "adzuna:107",
                    "adzuna:201", "reed:9002", "reed:9101", "reed:9102"]:
            self.assertNotIn(jid, jobs, jid)
        rej = meta["rejected"]
        self.assertEqual(rej.get("advert says no sponsorship"), 1)
        self.assertEqual(rej.get("salary below the visa minimum"), 1)
        self.assertEqual(rej.get("asks for more experience"), 1)
        self.assertEqual(rej.get("short fixed-term contract"), 1)
        self.assertGreaterEqual(rej.get("employer not on the sponsor register", 0), 2)
        self.assertEqual(rej.get("recruitment agency, employer not named"), 2)  # Hays, and the "Power BI" job-board advert
        self.assertEqual(rej.get("part-time"), 1)
        self.assertEqual(meta["counts"]["kept"], len(jobs))
        self.assertTrue(meta["sources"]["register"]["ok"])
        self.assertTrue(meta["sources"]["adzuna"]["ok"])

        # Alerts went out on both channels
        self.assertEqual(len(gh.issues), 1)
        self.assertEqual(len(gh.comments), 1)
        self.assertIn("@Someone", gh.comments[0]["body"])
        self.assertIn("Junior Data Analyst", gh.comments[0]["body"])
        self.assertEqual(len(pushes), 1)
        self.assertEqual(pushes[0][1]["headers"]["Click"], "https://someone.github.io/sponsor-radar/")

        # A second run finds nothing new and doesn't duplicate or re-alert
        sess2 = fakes.make_session(github=gh, ntfy=pushes)
        run(self.dir, env=self.env, now=datetime(2026, 10, 17, 11, 17, tzinfo=UK), session=sess2, sleep=False)
        jobs2 = self.read("docs/data/jobs.json")["jobs"]
        self.assertEqual(len(jobs2), len(jobs))
        self.assertEqual(self.read("docs/data/meta.json")["counts"]["checked"], 0)
        self.assertEqual(len(gh.comments), 1)
        details_calls = [c for c in sess2.calls if "/api/1.0/jobs/" in c[1]]
        self.assertEqual(details_calls, [])

    def test_new_rules_remove_existing_roles(self):
        run(self.dir, env=self.env, now=datetime(2026, 10, 17, 9, 17, tzinfo=UK), session=fakes.make_session(), sleep=False, notify_on=False)
        before = {j["id"] for j in self.read("docs/data/jobs.json")["jobs"]}
        self.assertIn("reed:9001", before)
        cfg = yaml.safe_load((self.dir / "config.yml").read_text())
        cfg["exclude_title_words"].append("credit")
        (self.dir / "config.yml").write_text(yaml.safe_dump(cfg))
        run(self.dir, env=self.env, now=datetime(2026, 10, 17, 11, 17, tzinfo=UK), session=fakes.make_session(adzuna={}, reed={}),
            sleep=False, notify_on=False)
        after = {j["id"] for j in self.read("docs/data/jobs.json")["jobs"]}
        self.assertNotIn("reed:9001", after)
        self.assertEqual(self.read("docs/data/meta.json")["counts"]["removed_by_new_rules"], 1)

    def test_settings_from_the_app(self):
        env = dict(self.env)
        when = datetime(2026, 10, 17, 9, 17, tzinfo=UK)
        (self.dir / "settings.json").write_text(json.dumps({"locations": ["Leeds", "Glasgow"], "include_remote": False, "saved_at": "2026-10-17T09:00:00Z"}))
        run(self.dir, env=env, now=when, session=fakes.make_session(), sleep=False, notify_on=False)
        jobs = {j["id"]: j for j in self.read("docs/data/jobs.json")["jobs"]}
        self.assertTrue(jobs)
        self.assertTrue(all(any(p in j["location"] for p in ("Leeds", "Glasgow")) for j in jobs.values()), [j["location"] for j in jobs.values()])
        meta = self.read("docs/data/meta.json")
        self.assertGreater(meta["rejected"].get("outside your chosen places", 0), 0)
        self.assertEqual(meta["settings"]["source"], "settings.json")
        published = self.read("docs/data/settings.json")
        self.assertEqual(published["locations"], ["Leeds", "Glasgow"])
        self.assertEqual(len(published["targets"]), 3)
        # Widening the places re-checks listings that were turned away, without re-adding the ones kept
        (self.dir / "settings.json").write_text(json.dumps({"locations": [], "saved_at": "2026-10-17T10:00:00Z"}))
        run(self.dir, env=env, now=datetime(2026, 10, 17, 11, 17, tzinfo=UK), session=fakes.make_session(), sleep=False, notify_on=False)
        jobs2 = {j["id"] for j in self.read("docs/data/jobs.json")["jobs"]}
        self.assertTrue(set(jobs) < jobs2, (set(jobs), jobs2))
        self.assertIn("adzuna:101", jobs2)        # London, kept now
        self.assertTrue(self.read("docs/data/meta.json")["settings"]["changed_this_run"])
        # Narrowing the places removes roles already shown
        (self.dir / "settings.json").write_text(json.dumps({"locations": ["Bristol"], "include_remote": False}))
        run(self.dir, env=env, now=datetime(2026, 10, 17, 13, 17, tzinfo=UK), session=fakes.make_session(adzuna={}, reed={}), sleep=False, notify_on=False)
        left = self.read("docs/data/jobs.json")["jobs"]
        self.assertTrue(all("Bristol" in j["location"] for j in left), [j["location"] for j in left])

    def test_quiet_hours_hold_then_morning(self):
        gh = fakes.GitHubFake()
        run(self.dir, env=self.env, now=datetime(2026, 10, 17, 2, 17, tzinfo=UK),
            session=fakes.make_session(github=gh), sleep=False)
        state = self.read("data/state.json")
        self.assertGreater(len(state["pending_alerts"]), 0)
        self.assertEqual(gh.comments, [])
        run(self.dir, env=self.env, now=datetime(2026, 10, 17, 7, 17, tzinfo=UK),
            session=fakes.make_session(adzuna={}, reed={}, github=gh), sleep=False)
        self.assertEqual(len(gh.comments), 1)
        self.assertIn("overnight", gh.comments[0]["body"])
        self.assertEqual(self.read("data/state.json")["pending_alerts"], [])

    def test_register_down_and_missing_keys(self):
        env = dict(self.env)
        env.pop("REED_API_KEY")
        code = run(self.dir, env=env, now=datetime(2026, 10, 17, 9, 17, tzinfo=UK),
                   session=fakes.make_session(register_ok=False), sleep=False, notify_on=False)
        self.assertEqual(code, 0)
        meta = self.read("docs/data/meta.json")
        self.assertFalse(meta["sources"]["register"]["ok"])
        self.assertIn("REED_API_KEY", meta["sources"]["reed"]["skipped"])
        jobs = {j["id"] for j in self.read("docs/data/jobs.json")["jobs"]}
        self.assertEqual(jobs, {"adzuna:101"})       # only the advert that offers sponsorship itself
        seen = self.read("data/state.json")["seen"]
        self.assertNotIn("adzuna:108", seen)          # judged again once the register is back

    def test_bad_key_fails_run_and_rule_change_alert(self):
        env = dict(self.env)
        env.pop("REED_API_KEY")
        gh = fakes.GitHubFake()
        sess = fakes.make_session(adzuna_status=401, github=gh,
                                  slug_updated={"going-rates": "2026-11-02T10:00:00Z"})
        code = run(self.dir, env=env, now=datetime(2026, 11, 3, 9, 17, tzinfo=UK), session=sess, sleep=False)
        self.assertEqual(code, 2)
        meta = self.read("docs/data/meta.json")
        self.assertIn("refused", meta["sources"]["adzuna"]["error"])
        self.assertEqual(len(meta["rules"]["changed"]), 1)
        self.assertEqual(len(gh.comments), 1)
        self.assertIn("going rates", gh.comments[0]["body"].lower().replace("-", " "))
        # The same change isn't announced twice
        run(self.dir, env=env, now=datetime(2026, 11, 3, 11, 17, tzinfo=UK),
            session=fakes.make_session(adzuna_status=401, github=gh, slug_updated={"going-rates": "2026-11-02T10:00:00Z"}), sleep=False)
        self.assertEqual(len(gh.comments), 1)


class AppSettings(unittest.TestCase):
    def test_sanitize(self):
        from radar import settings as st
        out = st.sanitize({
            "targets": [{"name": "  Pricing analyst ", "searches": "Pricing Analyst, pricing analyst , ,actuarial analyst", "min_salary": "34000"},
                        {"name": "", "searches": ["x"]}, {"name": "No searches", "searches": []}, "junk"],
            "max_years_experience": "3", "locations": "Leeds, Manchester ,", "include_remote": 0,
            "exclude_title_words": ["Senior", "senior", "Manager"], "quiet_hours": {"from": "23:00", "to": "7am"},
            "unknown": 1})
        self.assertEqual(out["targets"], [{"name": "Pricing analyst", "searches": ["pricing analyst", "actuarial analyst"], "min_salary": 34000, "salary_note": ""}])
        self.assertEqual(out["max_years_experience"], 3)
        self.assertEqual(out["locations"], ["Leeds", "Manchester"])
        self.assertIs(out["include_remote"], False)
        self.assertEqual(out["exclude_title_words"], ["senior", "manager"])
        self.assertEqual(out["quiet_hours"], {"from": "23:00", "to": "07:00"})
        self.assertNotIn("unknown", out)
        self.assertEqual(st.sanitize({"targets": []}), {})
        self.assertEqual(st.sanitize("nonsense"), {})


class Messages(unittest.TestCase):
    def test_message(self):
        job = {"title": "Data Analyst", "url": "https://x", "company": "Barclays", "location": "London",
               "salary": {"min": 36000, "max": 40000}, "closes": None,
               "sponsor": {"level": "Likely", "reasons": ["Licensed Skilled Worker sponsor: Barclays Bank UK PLC (A-rated)"]}}
        text = build_message([job], [], "https://me.github.io/sponsor-radar/", "me")
        self.assertIn("**1 new sponsored role** · @me", text)
        self.assertIn("£36,000–£40,000", text)
        self.assertIn("no closing date given", text)
        self.assertEqual(dashboard_url("Pranav/sponsor-radar"), "https://pranav.github.io/sponsor-radar/")


if __name__ == "__main__":
    unittest.main()
