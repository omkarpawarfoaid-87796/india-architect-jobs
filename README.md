# India Architect Jobs Collector V6 — Single Sheet Only

V6 follows the new requirement: output only to `Sheet1`.

## What changed from V5

V5 created multiple tabs: CandidateJobs, RejectedJobs, AutomationOutput, Sources, and _CollectorMeta.
V6 does not use those tabs.

The website developer should connect only to:

`Sheet1`

## V6 flow

1. Read existing `Sheet1`.
2. Remove fake, expired, duplicate, LinkedIn-final, and service-page rows.
3. Search multiple public sources:
   - company career pages
   - official employer websites
   - public ATS boards
   - Bing/Google-style public search signals
   - LinkedIn/job-board signals as discovery signals only
4. Resolve LinkedIn/job-board signals to official employer pages where possible.
5. Validate public apply method.
6. Rebuild only `Sheet1` with website-ready jobs.

## Important LinkedIn rule

LinkedIn can be used only to discover that a company has a job.
The final `apply_url` in Sheet1 will never be a LinkedIn URL.

Allowed final apply methods:

- official company careers page
- official job page
- public ATS apply page
- official application form
- employer email

## Sheet1 schema

V6 uses the exact developer schema:

external_id, title, description, status, employer_author, employer_email,
employer_name, expiry_date, application_deadline_date, featured, urgent,
filled, apply_type, apply_url, apply_email, phone, salary, max_salary,
salary_type, address, location, category, type, tag, experience, gender,
industry, qualification, career_level, video_url, logo_url

`external_id` always remains blank.

## Important repo cleanup

Before pushing V6, remove old workflow files from earlier versions:

- .github/workflows/hourly.yml
- .github/workflows/discovery.yml
- .github/workflows/fresh_jobs.yml
- .github/workflows/candidate_jobs.yml

Then add only:

- .github/workflows/single_sheet_jobs.yml

## Run order

Run only one workflow:

`Single Sheet Job Finder V6`

It runs hourly on schedule and can also be run manually.
