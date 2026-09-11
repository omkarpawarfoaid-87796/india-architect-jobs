# Architect Job Collector V2

V2 is designed for the India architecture/design job website workflow.

## Hard freshness rule

- Dated vacancies posted **before 2026-09-01 are rejected**.
- September 2026, October 2026 and later jobs can be accepted only while applications are open.
- A closed/expired signal always overrides a recent date.
- Undated vacancies are accepted only when the page clearly shows a live opening AND a public application method.
- LinkedIn and configured login-gated URLs are rejected.

## What V2 adds over V1

1. September 1, 2026 freshness cutoff.
2. Application deadline detection.
3. Closed/expired vacancy detection.
4. HTML extraction in addition to Schema.org JobPosting JSON-LD.
5. Job-detail link discovery from career pages.
6. Relevant job URL discovery from public sitemaps.
7. Public apply-route detection: apply link, public form or public email.
8. Better India city/state normalization.
9. Architecture role filtering and IT/software-architect rejection.
10. Existing-job revalidation; stale/closed rows are kept historically but marked Closed.
11. Existing V1 Google Sheet is migrated automatically by appending the new V2 columns.
12. Built-in self-test before every GitHub Actions run.

## New V2 columns

V2 preserves your old columns and adds fields such as:

- Job Category
- Application Deadline
- Freshness
- Application Status
- Application Method
- Verified At
- Closed Reason

## Upgrade from V1

Your existing GitHub secrets do not change:

- `GOOGLE_SHEET_ID`
- `GOOGLE_SERVICE_ACCOUNT_JSON`

Keep the Google Sheet tab named `Jobs`.

Replace these repository files with the V2 copies:

- `collector.py`
- `config.yaml`
- `requirements.txt`
- `.github/workflows/hourly.yml`

Then go to GitHub -> Actions -> **Hourly Architect Job Collector V2** -> **Run workflow**.

The first V2 run will also review old active records. For example, the 2020 Shree Designs result from V1 should be marked `Closed` because its posted date is older than the 2026-09-01 cutoff.

## Expected GitHub log

A normal run will show messages like:

```
SELF TEST PASSED: cutoff, fresh, undated-live and closed-job rules are working.
SOURCE OK | https://example.com/careers | jobs=2
...
V2 cutoff date: 2026-09-01
Qualified OPEN Indian architecture jobs this run: 8
Google Sheet: 5 new, 3 refreshed, 1 closed/stale
```

## Important

The collector uses only publicly accessible pages. It does not bypass authentication, CAPTCHA, paywalls or access controls.
