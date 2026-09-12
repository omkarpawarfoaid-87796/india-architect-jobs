# Architect Job Collector V6.1 — Single Sheet Quantity Mode

This version fixes the low-quantity issue from V6.

## Output

Only one Google Sheet tab is used for website output:

- `Sheet1`

Old pipeline tabs are deleted automatically after a successful run:

- `_CollectorMeta`
- `Sources`
- `CandidateJobs`
- `AutomationOutput`
- `RejectedJobs`

## What changed from V6

V6 was too strict. It used LinkedIn/job boards as signals but published only jobs resolved to official employer pages. That kept the output clean but low.

V6.1 keeps one-sheet output, but adds Quantity Mode:

- Company career pages are still scanned.
- Public web searches are used.
- LinkedIn stays signal-only and is never published as final `apply_url`.
- Public job-board detail pages are allowed as final `apply_url` when they look like a specific real job detail page.
- Search/list/category/service/content pages are rejected.

## Website rule

The website developer should use only `Sheet1`.

`external_id` stays blank because the website developer generates IDs.

## Workflow

Run only:

`Single Sheet Job Finder V6.1 Quantity`

Expected runtime: 5–25 minutes depending on search results.
