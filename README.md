# Architect Job Collector V6.2 — Clean Quantity Mode

Single Google Sheet output only: `Sheet1`.

V6.2 fixes the V6.1 quantity issue where dictionary/article/BIM/product/reference pages were incorrectly added as jobs.

## Rules

- Use only `Sheet1` for the website.
- Do not create CandidateJobs, RejectedJobs, Sources, AutomationOutput, or _CollectorMeta.
- Delete old extra tabs after a successful run.
- LinkedIn is never a final apply URL.
- Public job board URLs are allowed only when they are specific job detail URLs.
- Public web URLs are allowed only when they are career/job/opening URLs with clear vacancy evidence.
- Reference/content/product pages are rejected.

## Main command

```bash
python single_sheet_jobs.py --self-test
python single_sheet_jobs.py
```
