# Architect Job Collector V7 — Apply Link Mode

V7 is built for the user's updated requirement:

- One Google Sheet output only: `Sheet1`
- Quantity is important
- Jobs may be discovered from many sources: LinkedIn, Naukri, Indeed, Foundit, Shine, TimesJobs, Glassdoor, Internshala, official career pages, and public web search results
- Final output must contain a real apply link where possible
- Only real job-detail URLs are allowed
- Search pages, dictionary pages, Wikipedia, BIM articles, product pages, service/location pages, directory pages and fake rows are rejected

## Output

The website developer should use only `Sheet1`.

V7 deletes old pipeline tabs when it runs successfully:

- `_CollectorMeta`
- `Sources`
- `CandidateJobs`
- `AutomationOutput`
- `RejectedJobs`

## Apply-link policy

Allowed as final `apply_url`:

- Official company career/job pages
- Public ATS job pages
- Specific Naukri job-listing pages
- Specific Indeed viewjob pages
- Specific Foundit job pages
- Specific Shine job pages
- Specific TimesJobs JobDetailView pages
- Specific Glassdoor job-listing pages
- Specific LinkedIn `/jobs/view/` pages

Rejected as final `apply_url`:

- LinkedIn search pages
- Generic job-board search pages
- Login pages
- Company pages without job detail
- Articles/content pages
- Product pages
- Dictionary/Wikipedia pages
- Service/city landing pages

## Workflow

GitHub Actions workflow name:

`Single Sheet Job Finder V7 Apply Link Mode`

Run this workflow manually after uploading the files.
