# Sponsor Radar

Finds new UK roles at employers licensed to sponsor Skilled Worker visas, filters them with clear rules, ranks them
with machine learning, and gives you tailored CVs and cover letters built from your own CV. No AI is involved anywhere,
and it runs for free on GitHub.

- **Searches every 2 hours** (GitHub Actions) on [Adzuna](https://www.adzuna.co.uk/) and [Reed](https://www.reed.co.uk/) for the roles in `config.yml`.
- **Keeps only sponsors**: the employer must be on the Home Office
  [register of licensed sponsors](https://www.gov.uk/government/publications/register-of-licensed-sponsors-workers)
  (Skilled Worker route), or the advert must offer sponsorship.
- **Drops bad fits**: adverts that rule out sponsorship, need security clearance or UK residency, are too senior or
  ask for too many years, pay below the visa minimum, are part-time or short fixed-term, or close within 2 days.
- **Ranks the rest** on a dashboard you open on your phone or laptop.
- **Alerts you** through GitHub notifications (email and the GitHub app), and optionally as phone push notifications through ntfy.

## The machine learning

| Part | Where | Method |
|---|---|---|
| Title matching | search | Nearest-neighbour classifier over TF-IDF vectors (word 1–2-grams plus character 2–4-grams) that maps each job title to your closest target role |
| Sponsor matching | search | Exact and prefix matching of cleaned company names, then nearest neighbour on character 3-gram TF-IDF vectors of about 100,000 licensed sponsors |
| Fit with your CV | dashboard | TF-IDF cosine similarity between your CV and each advert |
| Your taste | dashboard | Logistic regression trained in your browser on the roles you mark *Interested/Applied* versus *Not for me* |
| Tailored CV and letter | dashboard | Your CV bullets ranked by TF-IDF similarity to the advert and the skills it asks for; the letter fills a template you can edit |

Everything is deterministic: nothing is generated, and the CVs and letters only reuse your own text.

## Privacy

The repository and the dashboard contain no personal data. Your CV, contact details, statuses and edited documents are
stored only in your browser (localStorage). Use **Your data → Back up** to move them to another device.

## Setup (about 10 minutes)

1. **Keys.** Get a free Adzuna key at [developer.adzuna.com](https://developer.adzuna.com/) (App ID and App Key) and a
   free Reed key at [reed.co.uk/developers/jobseeker](https://www.reed.co.uk/developers/jobseeker).
2. **Secrets.** In this repository go to **Settings → Secrets and variables → Actions → New repository secret** and add:
   - `ADZUNA_APP_ID`
   - `ADZUNA_APP_KEY`
   - `REED_API_KEY`
   - `NTFY_TOPIC` (optional, see Alerts below)
3. **Dashboard.** Go to **Settings → Pages** and set **Source** to **GitHub Actions**.
4. **First run.** Go to **Actions → Sponsor Radar → Run workflow**. It takes a couple of minutes.
5. **Open the dashboard** at `https://<your-username>.github.io/<repository-name>/`. Add your CV, your details, and mark a few roles.

## Alerts

- **GitHub:** new finds are posted as comments on an issue called *Job alerts*, mentioning you, so GitHub emails you and
  notifies the GitHub app. Roles found during quiet hours (22:30–07:00 UK by default) are sent together in the morning.
- **Phone push (optional):** install the free [ntfy](https://ntfy.sh/) app, subscribe to a hard-to-guess topic name
  (for example `sponsor-radar-7f3k29x`), and save the same name as the `NTFY_TOPIC` secret. Anyone who knows the topic
  name can read these alerts, so keep it private.

## Changing what it looks for

Edit `config.yml` on GitHub (pencil icon) and commit. The next run uses it. You can change the target roles and their
searches, salary minimums, experience limit, excluded title words, quiet hours and alert threshold.

**Salary minimums:** the defaults are Skilled Worker new-entrant rates. The dashboard warns you when gov.uk updates the
Skilled Worker going rates, shortage list or guide after the `salary_rules_checked` date. Check the minimums, update them
and the date.

## Limits

- Adzuna's free key allows 25 calls a minute, 250 a day, 1,000 a week and 2,500 a month. The search stays under these
  by taking turns through your searches (`calls_per_run`). Reed doesn't publish limits.
- Adzuna adverts arrive as summaries, so wording deeper in the advert can't be checked. Those roles are flagged.
- GitHub may delay scheduled runs at busy times, and pauses schedules in repositories with no activity for 60 days
  (each run's commit counts as activity).
- Being on the sponsor register means an employer *can* sponsor, not that it will for a given role. Always confirm
  sponsorship and salary with the employer. This is not immigration advice.

## Running it yourself

```bash
pip install -r requirements.txt
ADZUNA_APP_ID=... ADZUNA_APP_KEY=... REED_API_KEY=... python -m radar --no-notify
python -m unittest discover -s tests          # pipeline tests
python tests/test_dashboard.py                # dashboard tests (needs Playwright and Chromium)
```

## Credits

Jobs by [Adzuna](https://www.adzuna.co.uk/) and [Reed.co.uk](https://www.reed.co.uk/). Sponsor data from the Home Office
register of licensed sponsors (Open Government Licence). PDF output by jsPDF, PDF reading by PDF.js, Word reading with JSZip
(see `docs/vendor/LICENSES.md`).
