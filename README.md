# India Architect Job Collector

Free-first hourly collector for **public Indian architecture jobs**.

## What it does

- Runs automatically every hour using GitHub Actions.
- Uses public employer career pages and public ATS job feeds.
- Extracts structured `JobPosting` data from Schema.org JSON-LD.
- Supports public Lever and Greenhouse job boards.
- Keeps only architecture / interior / urban / landscape / BIM-type roles.
- Excludes software/cloud/solution/data/etc. "architect" roles.
- Keeps only jobs whose location appears to be in India.
- Rejects LinkedIn.
- Rejects final application pages that appear login-gated.
- Deduplicates jobs.
- Refreshes existing rows and adds new rows to Google Sheets.
- Extracts only publicly displayed contact email/phone where available.

## Sheet columns

Job ID, Job Title, Company, Location, City, State, Country, Job Type,
Experience, Salary, Skills, Short Description, Full Description, Posted Date,
Valid Through, Source Type, Source Name, Source URL, Apply URL, Company Website,
Logo URL, Public Contact Email, Public Contact Phone, First Seen, Last Seen,
Status, Fingerprint.

## Setup

### 1. Create the Google Sheet

Create a blank Google Sheet. Rename the first tab to `Jobs`.
Copy the spreadsheet ID from its URL.

### 2. Create a Google Cloud service account

1. Create a Google Cloud project.
2. Enable **Google Sheets API**.
3. Create a service account and download its JSON key.
4. Copy the service-account email address.
5. Share your Google Sheet with that email as **Editor**.

For minimum exposure, give this service account access only to the job sheet.

### 3. Create the GitHub repository

Upload these files to a repository.

A public repository gets standard GitHub-hosted Actions without billable
minutes. If you prefer a private GitHub Free repository, keep an eye on the
monthly included Actions-minute quota.

### 4. Add GitHub Actions secrets

Repository -> Settings -> Secrets and variables -> Actions -> New repository secret.

Add:

- `GOOGLE_SHEET_ID` = the spreadsheet ID.
- `GOOGLE_SERVICE_ACCOUNT_JSON` = the complete contents of the service-account JSON key.

Never commit the JSON key into the repository.

### 5. Add job sources

Edit `config.yaml`.

Add employer careers pages:

```yaml
career_pages:
  - https://company.example/careers
  - https://another.example/jobs
```

If an employer uses Lever:

```yaml
lever_sites:
  - employer-board-name
```

If it uses Greenhouse:

```yaml
greenhouse_boards:
  - employer-board-token
```

The strongest scalable approach is to keep expanding this source registry as
the discovery process finds new architecture employers.

### 6. Test manually

GitHub -> Actions -> Hourly Architect Job Collector -> Run workflow.

Or locally:

```bash
pip install -r requirements.txt
DRY_RUN=true python collector.py
```

### 7. Hourly schedule

The workflow runs at minute 17 of every hour in `Asia/Kolkata`.

Why minute 17 instead of exactly `00`? Scheduled jobs are often busier at the
top of the hour. Running at an off-minute tends to be more reliable.

## Important source policy

Use only pages that are publicly accessible. Do not bypass authentication,
CAPTCHA, paywalls, access controls, or anti-bot restrictions.

The system intentionally excludes LinkedIn. If another source requires users
to create an account or sign in before applying, add it to `blocked_domains`
or its login path to `login_url_markers`.

## Scaling plan

V1: employer sites + Lever + Greenhouse + JSON-LD.

V2: add adapters for other public ATS systems (e.g. SmartRecruiters, Workable,
Ashby and employer-specific APIs where their public job endpoints permit it).

V3: add a separate discovery job that identifies new Indian architecture firms
and their public careers pages, verifies the domain, and adds approved sources
to the source registry.

Do not use a generic "scrape the whole web" loop as the only source. It is less
stable, harder to deduplicate and more likely to hit anti-bot/login walls.
# india-architect-jobs
