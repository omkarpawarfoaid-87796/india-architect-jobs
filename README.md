# India Architect Jobs Collector V4.4.6

V4.4.6 is the timeout-safe cleanup build.

## Fixed

- Prevents hourly collector from running for 1+ hour.
- Blocks HomeLane service/city landing pages before fetching.
- Blocks AECOM Australia & New Zealand / early-career bucket pages.
- Limits detail pages per source.
- Lowers request timeout to 8 seconds.
- Reduces hourly workflow timeout to 20 minutes.
- Rebuilds Sheet1 clean from verified rows only.

## Replace these files

- collector.py
- discovery.py
- fresh_jobs.py
- ats_discovery.py
- report_output.py
- config.yaml
- .github/workflows/hourly.yml
- .github/workflows/discovery.yml
- .github/workflows/fresh_jobs.yml

Keep requirements.txt.

## Run first

1. Hourly Architect Job Collector V4.4.6
2. Fresh Architect Job Discovery V4.4.6
3. Hourly Architect Job Collector V4.4.6

Expected: Hourly must finish under 20 minutes and Sheet1 must not contain HomeLane city pages or AECOM ANZ page.
