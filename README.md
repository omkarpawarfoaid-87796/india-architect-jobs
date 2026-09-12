# Architect Job Collector V8.2 — Final Website Quality

V8.2 keeps the V8 quantity approach and V8.1 cleanup, but adds final website-quality filters.

## Output

Only one Google Sheet tab is used:

- `Sheet1`

Old tabs from previous versions can be deleted by the script if configured.

## What V8.2 does

- Collects architecture / interior / BIM / visualization / drafting jobs from public sources.
- Allows specific LinkedIn `/jobs/view/` links as `apply_url` for quantity.
- Allows specific public job-board detail URLs from Naukri, Indeed, Foundit, Shine, TimesJobs and Glassdoor.
- Keeps official career-page jobs from architecture/design firms.
- Cleans LinkedIn tracking parameters.
- Deduplicates by LinkedIn job ID / job-board detail URL / title-company-location.
- Cleans employer names and removes city suffixes such as `— Mumbai`.
- Rejects software / IT / cloud / solution / data architect roles.
- Rejects fake content pages such as Wikipedia, dictionary pages, BIM articles and Autodesk product pages.
- Removes invalid experience values such as `60 years` when that is company history, not candidate requirement.

## Run

GitHub Actions workflow:

`Single Sheet Job Finder V8.2 Final Website Quality`

Required secrets:

- `GOOGLE_SHEET_ID`
- `GOOGLE_SERVICE_ACCOUNT_JSON`

