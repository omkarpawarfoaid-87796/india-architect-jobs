"""
V4.4 Public ATS Discovery

Reads trusted/active employer Sources, scans their public careers pages for
public ATS links, and adds those boards back into Sources.

No authentication, browser login, private APIs or LinkedIn scraping is used.
"""

import argparse
import os
from datetime import datetime

import yaml

import collector as core


def load_config():
    with open("config.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def ats_source_row(parent, ats, stamp):
    provider = ats["provider"]
    url = core.canonical_url(ats["url"])
    ident = core.clean_text(ats.get("identifier"))

    company = core.clean_text(parent.get("company_name") or "")
    website = core.canonical_url(parent.get("company_website") or "")
    city = core.clean_text(parent.get("city") or "")
    state = core.clean_text(parent.get("state") or "")

    return {
        "source_id": core._source_id(url),
        "company_name": company or (ident.replace("-", " ").title() if ident else provider),
        "company_website": website,
        "career_url": url,
        "city": city,
        "state": state,
        "source_type": f"ATS {provider}",
        "discovered_from": (
            f"ATS detected from {core.clean_text(parent.get('career_url') or website)}"
        ),
        "first_discovered": stamp,
        "last_checked": "",
        "last_success": "",
        "jobs_found": "0",
        "consecutive_failures": "0",
        "status": "New",
        "quality_reason": f"Public {provider} board linked by trusted employer",
    }


def existing_sources(service, sheet_id, tab):
    headers = core.ensure_sources_sheet(service, sheet_id, tab)
    values = core.read_sheet_values(service, sheet_id, tab)
    rows = []
    urls = set()
    if values:
        for idx, row in enumerate(values[1:], start=2):
            rec = core.row_to_record(headers, row)
            rec["_row_num"] = idx
            rows.append(rec)
            url = core.canonical_url(rec.get("career_url") or "")
            if url:
                urls.add(url)
    return headers, rows, urls


def scan_parent_for_ats(parent, cfg):
    url = core.canonical_url(parent.get("career_url") or "")
    if not url or core.ats_provider_from_url(url):
        return []

    r = core.fetch(url, cfg)
    if not r:
        return []

    return core.detect_ats_links(r.url, r.text, cfg)


def append_rows(service, sheet_id, tab, headers, rows):
    if not rows:
        return 0
    payload = [[row.get(h, "") for h in headers] for row in rows]
    service.spreadsheets().values().append(
        spreadsheetId=sheet_id,
        range=f"'{tab}'!A:{core.column_letter(len(headers))}",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": payload},
    ).execute()
    return len(payload)


def run(cfg):
    sheet_id = os.environ.get("GOOGLE_SHEET_ID", "")
    if not sheet_id:
        raise RuntimeError("Missing GOOGLE_SHEET_ID")

    tab = os.environ.get("GOOGLE_SOURCES_TAB", "Sources")
    service = core.sheet_service()
    headers, rows, existing_urls = existing_sources(service, sheet_id, tab)

    max_parents = int(cfg.get("ats_discovery_sources_per_run", 80))
    max_new = int(cfg.get("ats_max_new_boards_per_run", 40))
    parents_checked = 0
    found = []
    stamp = core.now_ist().isoformat(timespec="seconds")

    for parent in rows:
        if parents_checked >= max_parents or len(found) >= max_new:
            break

        status = core.clean_text(parent.get("status") or "").lower()
        source_type = core.clean_text(parent.get("source_type") or "")

        # Only trusted/accepted employer pages should seed ATS boards.
        if status not in ("active", "warning", "new"):
            continue
        if source_type.startswith("ATS "):
            continue

        parent_url = core.canonical_url(parent.get("career_url") or "")
        if not parent_url:
            continue

        parents_checked += 1

        for ats in scan_parent_for_ats(parent, cfg):
            ats_url = core.canonical_url(ats["url"])
            if not ats_url or ats_url in existing_urls:
                continue

            existing_urls.add(ats_url)
            row = ats_source_row(parent, ats, stamp)
            found.append(row)
            print(
                f"ATS DISCOVERED | {row['company_name']} | "
                f"{ats['provider']} | {ats_url}"
            )
            if len(found) >= max_new:
                break

    added = append_rows(service, sheet_id, tab, headers, found)

    print("=" * 80)
    print(f"Trusted employer sources checked for ATS: {parents_checked}")
    print(f"New public ATS boards added: {added}")
    print("=" * 80)
    return added


def self_test():
    cfg = load_config()

    html = """
    <html><body>
      <iframe src="https://jobs.lever.co/acme"></iframe>
      <a href="https://job-boards.greenhouse.io/example">Careers</a>
      <script>
        window.jobs = "https://jobs.ashbyhq.com/sample";
      </script>
    </body></html>
    """
    detected = core.detect_ats_links(
        "https://examplearchitects.in/careers",
        html,
        cfg,
    )
    providers = {x["provider"] for x in detected}
    assert {"Lever", "Greenhouse", "Ashby"}.issubset(providers)

    parent = {
        "company_name": "Example Architects",
        "company_website": "https://examplearchitects.in",
        "career_url": "https://examplearchitects.in/careers",
        "city": "Mumbai",
        "state": "Maharashtra",
    }
    row = ats_source_row(
        parent,
        {
            "provider": "Lever",
            "url": "https://jobs.lever.co/acme",
            "identifier": "acme",
        },
        "2026-09-11T12:00:00+05:30",
    )
    assert row["source_type"] == "ATS Lever"
    assert row["company_name"] == "Example Architects"
    assert row["status"] == "New"

    print("ATS DISCOVERY SELF TEST PASSED")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        return

    run(load_config())


if __name__ == "__main__":
    main()
