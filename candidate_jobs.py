"""
V5 CandidateJobs Pipeline

Purpose:
- Create a large, visible candidate pipeline without polluting Sheet1.
- Sheet1 remains strict website-ready verified jobs only.
- CandidateJobs stores LinkedIn/company-listed, Google/search, job-board and official signals for review.
- RejectedJobs stores invalid/fake/expired/content/service-page signals with reasons.

Important rule:
LinkedIn/job boards are signals only. The website visitor must not be sent to LinkedIn.
"""

import argparse
import hashlib
import os
import re
import time
from datetime import datetime
from urllib.parse import urlparse

import yaml

import collector as core
import discovery as disc
import fresh_jobs as fresh


CANDIDATE_HEADERS = [
    "candidate_id",
    "discovered_at",
    "last_checked",
    "source_type",
    "role",
    "company",
    "title",
    "city",
    "state",
    "location",
    "signal_url",
    "signal_title",
    "signal_snippet",
    "official_url",
    "apply_url",
    "apply_email",
    "validation_status",
    "verification_score",
    "decision",
    "reject_reason",
    "review_status",
    "move_to_website",
    "notes",
]

REJECTED_HEADERS = [
    "rejected_id",
    "rejected_at",
    "source_type",
    "role",
    "company",
    "title",
    "signal_url",
    "signal_title",
    "signal_snippet",
    "reject_reason",
    "query",
]


BLOCKED_FINAL_APPLY_DOMAINS = {
    "linkedin.com", "indeed.com", "naukri.com", "glassdoor.com", "glassdoor.co.in",
    "foundit.in", "monsterindia.com", "shine.com", "timesjobs.com", "jooble.org",
}


def load_config():
    with open("config.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def now_value():
    return core.now_ist().isoformat(timespec="seconds")


def clean(value):
    return core.clean_text(value or "")


def stable_id(*parts, prefix="CAND"):
    raw = "|".join(clean(x).lower() for x in parts if x is not None)
    return f"{prefix}-" + hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:14].upper()


def domain(url):
    return core.domain(url or "")


def final_apply_is_blocked(url):
    d = domain(url)
    return any(d == x or d.endswith("." + x) for x in BLOCKED_FINAL_APPLY_DOMAINS)


def candidate_queries(cfg):
    roles = cfg.get("fresh_job_roles", []) or [
        "Architect", "Junior Architect", "Senior Architect", "Interior Designer",
        "Landscape Architect", "BIM Architect", "3D Visualizer", "Design Manager",
    ]
    cities = cfg.get("fresh_job_cities", []) or [
        "Mumbai", "Delhi", "Bengaluru", "Pune", "Hyderabad", "Chennai", "Ahmedabad",
    ]
    role_count = max(1, int(cfg.get("candidate_roles_per_run", 8)))
    city_count = max(1, int(cfg.get("candidate_cities_per_run", 5)))
    max_queries = max(1, int(cfg.get("candidate_max_queries_per_run", 40)))

    slot = int(datetime.now().timestamp() // (3 * 3600))
    role_start = (slot * role_count) % max(1, len(roles))
    city_start = (slot * city_count) % max(1, len(cities))
    selected_roles = [roles[(role_start+i) % len(roles)] for i in range(min(role_count, len(roles)))]
    selected_cities = [cities[(city_start+i) % len(cities)] for i in range(min(city_count, len(cities)))]

    queries = []
    for role in selected_roles:
        queries.extend([
            f'"{role}" "hiring" India "apply"',
            f'"{role}" "job" India "today"',
            f'"{role}" "current openings" India',
            f'"{role}" "send your resume" India',
            f'site:linkedin.com/jobs "{role}" India hiring',
            f'site:linkedin.com/jobs "{role}" India posted',
            f'site:indeed.com "{role}" India hiring',
            f'site:naukri.com "{role}" India hiring',
        ])
        for city in selected_cities:
            queries.extend([
                f'"{role}" "{city}" "apply now"',
                f'"{role}" "{city}" "we are hiring"',
                f'"{role}" "{city}" site:linkedin.com/jobs',
            ])
    # dedupe while preserving order
    out, seen = [], set()
    for q in queries:
        if q not in seen:
            seen.add(q)
            out.append(q)
        if len(out) >= max_queries:
            break
    return out


def extract_city_state(text, cfg):
    low = clean(text).lower()
    cities = cfg.get("fresh_job_cities", []) or []
    city = ""
    for c in cities:
        if re.search(rf"\b{re.escape(c.lower())}\b", low):
            city = c
            break
    state_map = {
        "mumbai": "Maharashtra", "pune": "Maharashtra", "delhi": "Delhi",
        "new delhi": "Delhi", "bengaluru": "Karnataka", "bangalore": "Karnataka",
        "hyderabad": "Telangana", "chennai": "Tamil Nadu", "ahmedabad": "Gujarat",
        "gurugram": "Haryana", "gurgaon": "Haryana", "noida": "Uttar Pradesh",
        "kolkata": "West Bengal", "kochi": "Kerala", "trivandrum": "Kerala",
    }
    state = state_map.get(city.lower(), "") if city else ""
    loc = f"{city}|India" if city else "India"
    return city, state, loc


def signal_reject_reason(signal, cfg):
    url = signal.get("signal_url", "")
    title = signal.get("signal_title", "")
    snippet = signal.get("signal_snippet", "")
    combined = " ".join([title, snippet, url]).lower()

    if not signal.get("role"):
        return "No architecture/design role found"

    # LinkedIn is acceptable as signal only when company is visible in public result metadata.
    if fresh.is_linkedin_url(url):
        if not fresh.is_company_listed_linkedin_signal(signal):
            return "LinkedIn signal missing company/role metadata"
        return ""

    # Job-board signals are allowed into CandidateJobs, never directly to Sheet1.
    d = domain(url)
    job_boards = {
        "indeed.com", "in.indeed.com", "naukri.com", "glassdoor.co.in", "glassdoor.com",
        "foundit.in", "monsterindia.com", "shine.com", "timesjobs.com", "jooble.org",
    }
    if any(d == x or d.endswith("." + x) for x in job_boards):
        return ""

    # Known fake/content/service pages go to RejectedJobs.
    hard = fresh.fresh_signal_source_reject_reason(url, title, snippet, "")
    if hard and "Blocked platform/domain" not in hard:
        return hard

    fake = core.real_vacancy_reject_reason({"title": title, "description": snippet, "source_url": url}, cfg)
    if fake and any(key in fake.lower() for key in ("service/location", "generic region", "not a vacancy")):
        return fake

    bad_terms = (
        "types of architect", "career growth transcript", "news", "magazine", "article",
        "design ideas", "home interior cost", "interior designers in ", "modular kitchen",
    )
    if any(x in combined for x in bad_terms):
        return "Content/service article, not a job signal"

    return ""


def verification_score(signal, source=None):
    score = 0
    if signal.get("role"):
        score += 20
    if signal.get("company"):
        score += 20
    text = " ".join([signal.get("signal_title", ""), signal.get("signal_snippet", "")]).lower()
    if any(x in text for x in ("hour", "today", "recent", "posted", "hiring", "apply")):
        score += 15
    if "india" in text or signal.get("location") == "India":
        score += 10
    if source:
        score += 25
        if source.get("source_type") == "Fresh Direct Job Page":
            score += 10
    return min(score, 100)


def source_to_candidate(signal, result, query, cfg):
    """Create candidate/reject records. Never publishes to Sheet1."""
    ts = now_value()
    signal_url = signal.get("signal_url", "")
    city, state, loc = extract_city_state(" ".join([signal.get("signal_title", ""), signal.get("signal_snippet", "")]), cfg)
    signal["location"] = loc

    reject = signal_reject_reason(signal, cfg)
    if reject:
        return None, {
            "rejected_id": stable_id(signal_url, signal.get("role"), signal.get("company"), reject, prefix="REJ"),
            "rejected_at": ts,
            "source_type": signal.get("signal_origin") or "Search Signal",
            "role": signal.get("role", ""),
            "company": signal.get("company", ""),
            "title": signal.get("role") or clean(result.get("title")),
            "signal_url": signal_url,
            "signal_title": signal.get("signal_title", ""),
            "signal_snippet": signal.get("signal_snippet", "")[:900],
            "reject_reason": reject,
            "query": query,
        }

    source = None
    source_reason = ""
    try:
        # Try to resolve to official source, but do not fail the candidate if resolution fails.
        source = fresh.resolve_signal(signal, cfg)
    except Exception as exc:
        source_reason = f"Official resolution error: {exc}"
        source = None

    official_url = ""
    apply_url = ""
    apply_email = ""
    validation_status = "Signal Only"
    decision = "Needs Review"
    notes = ""

    if source:
        official_url = source.get("career_url", "")
        validation_status = "Official Source Found"
        decision = "Review Official Source"
        notes = source.get("quality_reason", "")
        # Add official source to Sources later; collector decides final jobs.
    elif source_reason:
        notes = source_reason

    # Never expose blocked job-board/LinkedIn URL as apply_url.
    if official_url and not final_apply_is_blocked(official_url):
        apply_url = official_url

    score = verification_score(signal, source)

    cand = {
        "candidate_id": stable_id(signal_url, signal.get("role"), signal.get("company"), prefix="CAND"),
        "discovered_at": ts,
        "last_checked": ts,
        "source_type": signal.get("signal_origin") or "Search Signal",
        "role": signal.get("role", ""),
        "company": signal.get("company", ""),
        "title": signal.get("role", ""),
        "city": city,
        "state": state,
        "location": loc,
        "signal_url": signal_url,
        "signal_title": signal.get("signal_title", "")[:500],
        "signal_snippet": signal.get("signal_snippet", "")[:900],
        "official_url": official_url,
        "apply_url": apply_url,
        "apply_email": apply_email,
        "validation_status": validation_status,
        "verification_score": str(score),
        "decision": decision,
        "reject_reason": "",
        "review_status": "Pending Review",
        "move_to_website": "no",
        "notes": notes[:900],
    }
    return cand, None


def records_from_values(values):
    if not values:
        return [], []
    headers = values[0]
    rows = [core.row_to_record(headers, r) for r in values[1:]]
    return headers, rows


def ensure_tab(service, sheet_id, tab, headers):
    core.get_sheet_properties(service, sheet_id, tab, create_if_missing=True)
    core.ensure_sheet_columns(service, sheet_id, tab, len(headers))
    values = core.read_sheet_values(service, sheet_id, tab)
    if not values:
        service.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range=f"'{tab}'!A1:{core.column_letter(len(headers))}1",
            valueInputOption="RAW",
            body={"values": [headers]},
        ).execute()
        return headers, []
    existing_headers = values[0]
    missing = [h for h in headers if h not in existing_headers]
    if missing:
        existing_headers += missing
        core.ensure_sheet_columns(service, sheet_id, tab, len(existing_headers))
        service.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range=f"'{tab}'!A1:{core.column_letter(len(existing_headers))}1",
            valueInputOption="RAW",
            body={"values": [existing_headers]},
        ).execute()
    return records_from_values(values if not missing else [existing_headers] + values[1:])


def record_to_row(headers, rec, old_row=None):
    base = list(old_row or [])
    if len(base) < len(headers):
        base += [""] * (len(headers)-len(base))
    for i, h in enumerate(headers):
        if h in rec:
            base[i] = rec[h]
    return base[:len(headers)]


def upsert_records(service, sheet_id, tab, headers, records, id_field):
    headers, existing = ensure_tab(service, sheet_id, tab, headers)
    values = core.read_sheet_values(service, sheet_id, tab)
    rows = values[1:] if len(values) > 1 else []
    by_id = {}
    for idx, row in enumerate(rows, start=2):
        rec = core.row_to_record(headers, row)
        rid = rec.get(id_field, "")
        if rid:
            by_id[rid] = (idx, row, rec)

    updates = []
    appends = []
    for rec in records:
        rid = rec.get(id_field, "")
        if rid in by_id:
            row_num, old_row, old_rec = by_id[rid]
            # Preserve first discovered/rejected time when updating.
            if "discovered_at" in old_rec and old_rec.get("discovered_at"):
                rec["discovered_at"] = old_rec["discovered_at"]
            if "rejected_at" in old_rec and old_rec.get("rejected_at"):
                rec["rejected_at"] = old_rec["rejected_at"]
            updates.append({
                "range": f"'{tab}'!A{row_num}:{core.column_letter(len(headers))}{row_num}",
                "values": [record_to_row(headers, rec, old_row)],
            })
        else:
            appends.append(record_to_row(headers, rec))

    if updates:
        service.spreadsheets().values().batchUpdate(
            spreadsheetId=sheet_id,
            body={"valueInputOption": "RAW", "data": updates},
        ).execute()
    if appends:
        service.spreadsheets().values().append(
            spreadsheetId=sheet_id,
            range=f"'{tab}'!A:{core.column_letter(len(headers))}",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": appends},
        ).execute()
    return len(appends), len(updates)


def append_verified_sources(service, sheet_id, sources_tab, candidates, cfg):
    """Add official sources discovered from candidates into Sources, not Sheet1."""
    sources = []
    for c in candidates:
        official = c.get("official_url", "")
        if not official or final_apply_is_blocked(official):
            continue
        if not c.get("company"):
            continue
        src = {
            "source_id": core._source_id(official),
            "company_name": c.get("company", ""),
            "company_website": core.origin(official),
            "career_url": core.canonical_url(official),
            "city": c.get("city", ""),
            "state": c.get("state", ""),
            "source_type": "Candidate Official Source",
            "discovered_from": f"V5 CandidateJobs | {c.get('source_type')} | {c.get('role')}",
            "first_discovered": now_value(),
            "last_checked": "",
            "last_success": "",
            "jobs_found": "0",
            "consecutive_failures": "0",
            "status": "New",
            "quality_reason": "Resolved from candidate signal; collector must validate jobs before Sheet1",
        }
        sources.append(src)
    if not sources:
        return 0
    core.ensure_sources_sheet(service, sheet_id, sources_tab)
    return disc.append_sources(service, sheet_id, sources_tab, sources)


def run_candidate_pipeline(cfg):
    sheet_id = os.environ.get("GOOGLE_SHEET_ID", "")
    if not sheet_id:
        raise RuntimeError("Missing GOOGLE_SHEET_ID")
    candidate_tab = os.environ.get("GOOGLE_CANDIDATE_TAB", cfg.get("candidate_jobs_tab", "CandidateJobs"))
    rejected_tab = os.environ.get("GOOGLE_REJECTED_TAB", cfg.get("rejected_jobs_tab", "RejectedJobs"))
    sources_tab = os.environ.get("GOOGLE_SOURCES_TAB", "Sources")

    service = core.sheet_service()
    ensure_tab(service, sheet_id, candidate_tab, CANDIDATE_HEADERS)
    ensure_tab(service, sheet_id, rejected_tab, REJECTED_HEADERS)
    core.ensure_sources_sheet(service, sheet_id, sources_tab)

    candidates = []
    rejected = []
    seen = set()
    max_candidates = int(cfg.get("candidate_max_candidates_per_run", 100))
    max_rejected = int(cfg.get("candidate_max_rejected_per_run", 120))
    delay = float(cfg.get("candidate_request_delay_seconds", cfg.get("fresh_request_delay_seconds", 0.8)))

    queries = candidate_queries(cfg)
    for query in queries:
        print(f"CANDIDATE QUERY | {query}")
        try:
            results = disc.search_web(query, cfg)
        except Exception as exc:
            print(f"CANDIDATE QUERY ERROR | {query} | {exc}")
            continue
        for result in results:
            sig = fresh.result_to_signal(result, cfg)
            if not sig:
                # Store a limited reject if it looks like a job query but role not found.
                continue
            key = (sig.get("signal_url", ""), sig.get("role", ""), sig.get("company", ""))
            if key in seen:
                continue
            seen.add(key)
            cand, rej = source_to_candidate(sig, result, query, cfg)
            if cand:
                candidates.append(cand)
                print(f"CANDIDATE | {cand['role']} | {cand['company'] or 'Unknown'} | {cand['validation_status']} | score={cand['verification_score']}")
            elif rej:
                rejected.append(rej)
                print(f"REJECTED CANDIDATE | {rej.get('role')} | {rej.get('company')} | {rej.get('reject_reason')}")
            if len(candidates) >= max_candidates and len(rejected) >= max_rejected:
                break
        if len(candidates) >= max_candidates:
            break
        time.sleep(delay)

    candidates = candidates[:max_candidates]
    rejected = rejected[:max_rejected]
    cand_added, cand_updated = upsert_records(service, sheet_id, candidate_tab, CANDIDATE_HEADERS, candidates, "candidate_id")
    rej_added, rej_updated = upsert_records(service, sheet_id, rejected_tab, REJECTED_HEADERS, rejected, "rejected_id")
    src_added = append_verified_sources(service, sheet_id, sources_tab, candidates, cfg)

    print("=" * 80)
    print(f"Candidate queries executed: {len(queries)}")
    print(f"CandidateJobs added: {cand_added}")
    print(f"CandidateJobs updated: {cand_updated}")
    print(f"RejectedJobs added: {rej_added}")
    print(f"RejectedJobs updated: {rej_updated}")
    print(f"Candidate official sources added to Sources: {src_added}")
    print("=" * 80)

    return {
        "queries": len(queries),
        "candidate_added": cand_added,
        "candidate_updated": cand_updated,
        "rejected_added": rej_added,
        "rejected_updated": rej_updated,
        "sources_added": src_added,
    }


def self_test():
    cfg = load_config()
    # Keep self-test offline and deterministic. Runtime can still call the real resolver.
    original_resolve_signal = fresh.resolve_signal
    fresh.resolve_signal = lambda signal, cfg: None
    try:
        sig = fresh.result_to_signal({
            "title": "Junior Architect - Arete Design Studio | LinkedIn",
            "snippet": "Chandigarh · 2 hours ago · Easy Apply",
            "url": "https://www.linkedin.com/jobs/view/123456",
            "engine": "Bing RSS",
        }, cfg)
        assert sig and sig["role"] == "Junior Architect"
        assert fresh.is_company_listed_linkedin_signal(sig)
        assert not signal_reject_reason(sig, cfg)
        cand, rej = source_to_candidate(
            sig,
            {"title": sig["signal_title"], "url": sig["signal_url"], "snippet": sig["signal_snippet"]},
            "test query",
            cfg,
        )
        assert cand is not None and rej is None
        assert cand["apply_url"] == ""  # LinkedIn never becomes final apply URL.
        assert cand["review_status"] == "Pending Review"

        bad_sig = fresh.result_to_signal({
            "title": "Interior Designer - HomeLane",
            "snippet": "Get Free Estimate Design Gallery Store Locator 45-day delivery",
            "url": "https://www.homelane.com/interior-designers/ahmedabad",
            "engine": "Bing RSS",
        }, cfg)
        if bad_sig:
            cand2, rej2 = source_to_candidate(
                bad_sig,
                {"title": bad_sig["signal_title"], "url": bad_sig["signal_url"], "snippet": bad_sig["signal_snippet"]},
                "test query",
                cfg,
            )
            assert cand2 is None and rej2 is not None

        assert cand["candidate_id"].startswith("CAND-")
        assert "CandidateJobs" in (cfg.get("candidate_jobs_tab") or "CandidateJobs")
    finally:
        fresh.resolve_signal = original_resolve_signal
    print("CANDIDATE JOB PIPELINE SELF TEST PASSED")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
    else:
        cfg = load_config()
        run_candidate_pipeline(cfg)


if __name__ == "__main__":
    main()
