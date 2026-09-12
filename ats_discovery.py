"""
V5 Public ATS Discovery

Reads trusted/active employer Sources, scans their public careers pages for
public ATS links, and adds those boards back into Sources.

No authentication, browser login, private APIs or LinkedIn scraping is used.
"""

import argparse
import os
import re
from datetime import datetime

import yaml

import collector as core
import discovery as disc


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



ATS_SEARCH_PATTERNS = (
    "jobs.lever.co",
    "greenhouse.io",
    "ashbyhq.com",
    "smartrecruiters.com",
    "myworkdayjobs.com",
    "apply.workable.com",
    "freshteam.com",
    "zohorecruit.com",
    "breezy.hr",
    "teamtailor.com",
    "jobvite.com",
    "icims.com",
    "recruitee.com",
    "bamboohr.com",
    "applytojob.com",
)


def parent_company_tokens(parent):
    company = core.clean_text(parent.get("company_name") or "")
    website = core.clean_text(parent.get("company_website") or "")
    stem = core.company_name_from_domain(website) if website else ""
    tokens = []
    for value in (company, stem):
        value = core.clean_text(value)
        if value and value.lower() not in {x.lower() for x in tokens}:
            tokens.append(value)
    return tokens


def ats_result_matches_parent(result, parent):
    text = " ".join([
        core.clean_text(result.get("title")),
        core.clean_text(result.get("snippet")),
        core.clean_text(result.get("url")),
    ]).lower()

    for token in parent_company_tokens(parent):
        token_low = token.lower()
        compact = re.sub(r"[^a-z0-9]", "", token_low)
        text_compact = re.sub(r"[^a-z0-9]", "", text)
        if token_low and token_low in text:
            return True
        if compact and len(compact) >= 5 and compact in text_compact:
            return True
    return False


def search_hidden_ats_boards(parent, cfg):
    """
    V4.4.2: find hidden ATS boards only for already trusted employer names.
    """
    queries_per_source = int(cfg.get("ats_search_queries_per_source", 6))
    results_per_source = int(cfg.get("ats_search_results_per_source", 8))

    found = []
    seen = set()
    companies = parent_company_tokens(parent)
    if not companies:
        return found

    patterns = list(cfg.get("ats_search_patterns", [])) or list(ATS_SEARCH_PATTERNS)
    queries = []
    for company in companies[:2]:
        for pattern in patterns:
            queries.append(f'"{company}" "{pattern}"')
            if len(queries) >= queries_per_source:
                break
        if len(queries) >= queries_per_source:
            break

    for query in queries:
        for result in disc.search_web(query, cfg)[:results_per_source]:
            url = core.canonical_url(result.get("url") or "")
            if not url or core.is_blocked_domain(url, cfg):
                continue
            provider = core.ats_provider_from_url(url)
            if not provider:
                continue
            if not ats_result_matches_parent(result, parent):
                continue

            root = core.ats_board_root(url)
            if not root:
                continue
            key = (provider, root)
            if key in seen:
                continue
            seen.add(key)
            found.append({
                "provider": provider,
                "url": root,
                "identifier": core.ats_identifier_from_url(root, provider),
            })
            print(f"ATS SEARCH HIT | {query} | {provider} | {root}")

    return found



def scan_parent_for_ats(parent, cfg):
    url = core.canonical_url(parent.get("career_url") or "")
    if not url or core.ats_provider_from_url(url):
        return []

    found = []
    seen = set()

    r = core.fetch(url, cfg)
    if r:
        for ats in core.detect_ats_links(r.url, r.text, cfg):
            key = (ats["provider"], core.canonical_url(ats["url"]))
            if key not in seen:
                seen.add(key)
                found.append(ats)

    for ats in search_hidden_ats_boards(parent, cfg):
        key = (ats["provider"], core.canonical_url(ats["url"]))
        if key not in seen:
            seen.add(key)
            found.append(ats)

    return found


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

    assert ats_result_matches_parent(
        {
            "title": "Example Architects Careers",
            "snippet": "Example Architects jobs on Lever",
            "url": "https://jobs.lever.co/examplearchitects",
        },
        parent,
    )

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
