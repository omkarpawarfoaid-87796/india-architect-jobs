# India Architect Jobs Automation V5

V5 changes the strategy from “force everything into Sheet1” to a professional two-layer pipeline.

## Main idea

- `Sheet1` = only verified website-ready jobs.
- `CandidateJobs` = high-volume job signals from LinkedIn/search/job boards/official pages for review.
- `RejectedJobs` = fake, content, service-page, expired or invalid signals.
- `AutomationOutput` = stakeholder dashboard summary.

## Why V5 is needed

V4.4.6 is stable and clean, but strict verification keeps Sheet1 around 4–7 jobs. V5 keeps Sheet1 clean while creating a larger visible candidate pipeline.

## New file

- `candidate_jobs.py`

## New Google Sheet tabs

- `CandidateJobs`
- `RejectedJobs`

## LinkedIn rule

LinkedIn is used only as a company-listed search signal. The website never sends users to LinkedIn.

LinkedIn signal flow:

1. Read public search result metadata only.
2. Extract role and company.
3. Try to resolve official company careers/apply page.
4. Save into CandidateJobs.
5. Only official/non-LinkedIn sources can become final website jobs.

## Workflows

1. `Hourly Architect Job Collector V5`
   - Updates only verified Sheet1.
2. `Fresh Architect Job Discovery V5`
   - Builds CandidateJobs, fresh sources, ATS sources, then refreshes Sheet1.
3. `Daily Architect Source Discovery V5`
   - Finds new official career sources.
4. `Candidate Job Pipeline V5`
   - Builds only CandidateJobs and RejectedJobs.

## First run order

1. Candidate Job Pipeline V5
2. Fresh Architect Job Discovery V5
3. Hourly Architect Job Collector V5

## What to show

Open `AutomationOutput` and show:

- Website-ready open jobs
- CandidateJobs total
- Pending review
- Official source found
- LinkedIn company-listed signals
- RejectedJobs total
- Rejected reason summary

## Important

Do not connect CandidateJobs directly to the website. Your web developer should import only Sheet1.
