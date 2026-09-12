"""
V4.4.3 Fresh Job Discovery

Purpose:
- Find newly surfaced architecture / built-environment job signals every few hours.
- Search public web results, including search-result snippets that may point to
  LinkedIn or job boards.
- NEVER fetch/login to LinkedIn and NEVER publish a LinkedIn-only job.
- Resolve the employer to an official/public company careers/job page first.
- Add verified public career sources into Sources.
- The workflow then runs collector.py immediately, so valid jobs can reach
  Sheet1 without waiting for the next hourly schedule.
"""

import argparse
import os
import re
import time
from datetime import datetime

import yaml

import collector as core
import discovery as disc


def load_config():
    with open("config.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def compact(value):
    return core.clean_text(value or "")


def blocked_signal_domain(url):
    """
    Compatibility-safe blocked-domain check.

    V4.4.1 does not assume discovery.py exposes DISCOVERY_BLOCKED_DOMAINS.
    It falls back to the known blocked domains and also asks collector.py's
    blocklist where possible.
    """
    d = core.domain(url)
    if not d:
        return False

    fallback = {
        "linkedin.com",
        "indeed.com",
        "in.indeed.com",
        "naukri.com",
        "glassdoor.co.in",
        "glassdoor.com",
        "foundit.in",
        "monsterindia.com",
        "shine.com",
        "timesjobs.com",
        "jooble.org",
        "ziprecruiter.com",
        "talent.com",
        "jobrapido.com",
        "simplyhired.co.in",
        "facebook.com",
        "instagram.com",
        "youtube.com",
        "x.com",
        "twitter.com",
        "pinterest.com",
        "web.archive.org",
        "archive.org",
        "yellowpages.com",
        "superpages.com",
        "houzz.com",
        "interiordesign.net",
        "decorilla.com",
        "people.inc",
        "condenast.com",
        "archdaily.com",
        "architizer.com",
        "dezeen.com",
        "designboom.com",
        "nrd.adsttc.com",
        "adsttc.com",
        "aia.org",
        "architecturelab.net",
        "worldarchitecture.org",
        "archinect.com",
        "e-architect.com",
        "stirworld.com",
        "architectandinteriorsindia.com",
    }

    discovery_blocked = getattr(disc, "DISCOVERY_BLOCKED_DOMAINS", set()) or set()
    blocked_domains = fallback | set(discovery_blocked)

    if any(d == x or d.endswith("." + x) for x in blocked_domains):
        return True

    try:
        cfg = load_config()
        if core.is_blocked_domain(url, cfg):
            return True
    except Exception:
        pass

    return False


def normalize_role_from_text(text, cfg):
    low = compact(text).lower()
    roles = cfg.get("fresh_job_roles", [])
    # Longest first prevents "Architect" from swallowing "Junior Architect".
    roles = sorted(roles, key=lambda x: len(x), reverse=True)
    for role in roles:
        if role.lower() in low:
            return role
    return ""


def strip_job_site_suffix(title):
    value = compact(title)
    suffixes = [
        " | LinkedIn",
        " - LinkedIn",
        " | Indeed",
        " - Indeed",
        " | Glassdoor",
        " - Glassdoor",
        " | Naukri",
        " - Naukri",
    ]
    for suffix in suffixes:
        if value.lower().endswith(suffix.lower()):
            value = value[: -len(suffix)].strip()
    return value


def extract_company_from_signal(title, snippet, role):
    """
    Best-effort extraction from public search-result metadata only.
    We do not fetch LinkedIn/job-board pages.
    """
    raw_title = strip_job_site_suffix(title)
    role_clean = compact(role)

    patterns = [
        # "Junior Architect - Arete Design Studio"
        rf"^{re.escape(role_clean)}\s*[-–—|]\s*(.+)$",
        # "Arete Design Studio hiring Junior Architect"
        rf"^(.+?)\s+(?:hiring|is hiring)\s+{re.escape(role_clean)}\b",
        # "Junior Architect at Arete Design Studio"
        rf"^{re.escape(role_clean)}\s+at\s+(.+)$",
        # "Junior Architect | Arete Design Studio"
        rf"^{re.escape(role_clean)}\s*\|\s*(.+)$",
    ]
    for pattern in patterns:
        m = re.search(pattern, raw_title, re.I)
        if m:
            company = compact(m.group(1))
            company = re.sub(
                r"\s+(?:jobs?|careers?|india|linkedin)$",
                "",
                company,
                flags=re.I,
            ).strip(" -|")
            if 2 <= len(company) <= 120:
                return company

    # Search snippets often start with the company name followed by separators.
    sn = compact(snippet)
    if role_clean:
        m = re.search(
            rf"([A-Z][A-Za-z0-9&.'’+\- ]{{2,80}}?)\s*[·|–—-]\s*"
            rf"(?:.*?){re.escape(role_clean)}",
            sn,
            re.I,
        )
        if m:
            company = compact(m.group(1)).strip(" -|")
            if 2 <= len(company) <= 120:
                return company

    return ""


def signal_is_recent(text):
    """
    Search engines expose recency inconsistently. This is a prioritization
    signal only; final acceptance still depends on the official page validator.
    """
    low = compact(text).lower()
    recent_patterns = [
        r"\b\d+\s*(?:minute|minutes|min|mins)\s+ago\b",
        r"\b\d+\s*(?:hour|hours|hr|hrs)\s+ago\b",
        r"\btoday\b",
        r"\bjust posted\b",
        r"\brecently posted\b",
        r"\bnew opening\b",
        r"\bnew job\b",
        r"\bhiring now\b",
    ]
    return any(re.search(p, low) for p in recent_patterns)


def fresh_queries(cfg):
    roles = cfg.get("fresh_job_roles", [])
    cities = cfg.get("fresh_job_cities", [])
    role_count = max(1, int(cfg.get("fresh_roles_per_run", 5)))
    city_count = max(1, int(cfg.get("fresh_cities_per_run", 3)))

    # Rotate by 3-hour window so repeated runs naturally cover the whole list.
    slot = int(datetime.now().timestamp() // (3 * 3600))
    role_start = (slot * role_count) % max(1, len(roles))
    city_start = (slot * city_count) % max(1, len(cities))

    selected_roles = [
        roles[(role_start + i) % len(roles)]
        for i in range(min(role_count, len(roles)))
    ] if roles else []

    selected_cities = [
        cities[(city_start + i) % len(cities)]
        for i in range(min(city_count, len(cities)))
    ] if cities else []

    queries = []
    for role in selected_roles:
        # India-wide catches remote/national openings.
        queries.append(f'"{role}" hiring India "hours ago"')
        queries.append(f'"{role}" jobs India "today"')
        queries.append(f'"{role}" "apply now" India')
        queries.append(f'"{role}" "current openings" India')
        queries.append(f'"{role}" "send your resume" India')

        # City-specific queries increase precision.
        for city in selected_cities:
            queries.append(f'"{role}" {city} India hiring')
            queries.append(f'"{role}" {city} "apply now"')

    max_queries = int(cfg.get("fresh_max_queries_per_run", 24))
    return queries[:max_queries]


def result_to_signal(result, cfg):
    text = " ".join([
        compact(result.get("title")),
        compact(result.get("snippet")),
    ])
    role = normalize_role_from_text(text, cfg)
    if not role:
        return None

    company = extract_company_from_signal(
        result.get("title", ""),
        result.get("snippet", ""),
        role,
    )

    return {
        "role": role,
        "company": company,
        "signal_url": core.canonical_url(result.get("url") or ""),
        "signal_title": compact(result.get("title")),
        "signal_snippet": compact(result.get("snippet")),
        "engine": compact(result.get("engine")),
        "recent_hint": signal_is_recent(text),
    }


def official_resolution_queries(signal):
    role = signal["role"]
    company = signal["company"]
    queries = []

    if company:
        queries.extend([
            f'"{company}" "{role}" careers',
            f'"{company}" "{role}" jobs',
            f'"{company}" careers India',
            f'"{company}" official website careers',
        ])

    # If company extraction failed, use the result title minus obvious job-board text.
    title = strip_job_site_suffix(signal.get("signal_title", ""))
    if title:
        queries.append(f'"{title}" official careers India')

    return queries[:5]


def fresh_signal_source_reject_reason(url, title="", snippet="", page_text=""):
    """
    V4.4.3 gate: fresh discovery must focus on hiring pages, not content pages.
    """
    url = core.canonical_url(url or "")
    if not url:
        return "Missing URL"
    if blocked_signal_domain(url):
        return "Blocked platform/domain"

    d = core.domain(url).lower()
    content_domains = {
        "nrd.adsttc.com", "adsttc.com", "aia.org", "architecturelab.net",
        "worldarchitecture.org", "archinect.com", "e-architect.com",
        "stirworld.com", "architectandinteriorsindia.com",
    }
    if any(d == x or d.endswith("." + x) for x in content_domains):
        return f"Content/institute/media domain: {d}"

    path = (core.urlparse(url).path or "").lower()
    combined = " ".join([compact(title), compact(snippet), compact(page_text)]).lower()

    bad_path_bits = (
        "/article/", "/articles/", "/news/", "/blog/", "/blogs/",
        "/transcript", "/career-growth/", "/architect/types", "/guide/",
        "/guides/", "/education/", "/resources/", "/project/", "/projects/",
    )
    hiring_terms = (
        "apply now", "apply for this job", "job opening", "job openings",
        "current opening", "current openings", "open position", "open positions",
        "vacancy", "vacancies", "we are hiring", "send your resume",
        "send your cv", "submit application", "submit your application",
        "careers", "join our team", "hiring",
    )
    if any(bit in path for bit in bad_path_bits) and not any(term in combined for term in hiring_terms[:13]):
        return "Article/content page without application signal"

    # If the URL path has no hiring terms, require the page/snippet to strongly
    # look like an application page before it can become a source.
    path_hiring = re.search(r"/(careers?|jobs?|openings?|positions?|vacanc(?:y|ies)|join-us|work-with-us|apply|hiring)(/|$|-|_)", path)
    if not path_hiring and not any(term in combined for term in hiring_terms):
        return "No hiring/apply/career signal"

    return ""


def source_from_direct_job_page(result, signal, cfg):
    """
    V4.4.3 volume improvement: if a public official result is itself a valid
    job page, add that exact page as a source. This lets new jobs enter Sheet1
    without waiting for a separate careers-page resolver.
    """
    url = core.canonical_url(result.get("url") or "")
    if not url or blocked_signal_domain(url) or core.is_blocked_domain(url, cfg):
        return None

    # Must look like a hiring/apply page from URL/search metadata first.
    reason = fresh_signal_source_reject_reason(
        url,
        result.get("title", ""),
        result.get("snippet", ""),
        "",
    )
    if reason and "No hiring" not in reason:
        return None

    jobs, response = core.parse_page_for_jobs(url, cfg, title_hint=signal.get("role", ""))
    if not response or not jobs:
        return None

    page_text = " ".join([j.get("description", "") for j in jobs])
    reason = fresh_signal_source_reject_reason(
        response.url,
        result.get("title", ""),
        result.get("snippet", ""),
        page_text,
    )
    if reason:
        return None

    # Use the first validated job to fill source metadata. The collector will
    # still re-scan and rebuild Sheet1 through the normal path.
    job = jobs[0]
    company = core.clean_text(job.get("company") or signal.get("company") or core.company_name_from_domain(response.url))
    city = core.clean_text(job.get("city") or "")
    state = core.clean_text(job.get("state") or "")

    return {
        "source_id": core._source_id(response.url),
        "company_name": company,
        "company_website": core.origin(response.url),
        "career_url": core.canonical_url(response.url),
        "city": city,
        "state": state,
        "source_type": "Fresh Direct Job Page",
        "discovered_from": (
            f"{signal.get('engine','Search')} direct job page | "
            f"{signal.get('role','')} | {signal.get('company','Unknown employer')}"
        ),
        "first_discovered": core.now_ist().isoformat(timespec="seconds"),
        "last_checked": "",
        "last_success": "",
        "jobs_found": str(len(jobs)),
        "consecutive_failures": "0",
        "status": "New",
        "quality_reason": f"Verified public direct job page with {len(jobs)} valid job(s)",
    }



def source_from_official_result(result, signal, cfg):
    url = core.canonical_url(result.get("url") or "")
    if not url:
        return None
    if blocked_signal_domain(url) or core.is_blocked_domain(url, cfg):
        return None

    # Reuse V4 company/career-page verification.
    candidate = disc.discover_from_result(
        {
            **result,
            "query": f"Fresh job: {signal['role']} | {signal.get('company','')}",
        },
        city="",
        cfg=cfg,
    )
    if not candidate:
        return None

    # V4.3: discovered company must independently pass India + employer quality.
    quality_valid = disc.validate_career_page(
        candidate["career_url"],
        cfg,
    )
    if not quality_valid:
        return None
    candidate["company_name"] = quality_valid["company_name"]
    candidate["company_website"] = quality_valid["company_website"]
    candidate["career_url"] = quality_valid["career_url"]
    candidate["quality_reason"] = quality_valid.get("quality_reason", "")

    # Verify the official career page text still has enough job relevance.
    response = disc.fetch(candidate["career_url"], cfg)
    if not response:
        return None

    from bs4 import BeautifulSoup
    soup = BeautifulSoup(response.text, "html.parser")
    page_text = core.best_main_text(soup)
    low = page_text.lower()

    reject_reason = fresh_signal_source_reject_reason(
        candidate["career_url"],
        result.get("title", ""),
        result.get("snippet", ""),
        page_text,
    )
    if reject_reason:
        return None

    # Role-specific match is preferred. Generic career pages are still allowed
    # because the hourly collector will parse/validate each actual vacancy.
    role_words = [
        w.lower()
        for w in re.findall(r"[A-Za-z0-9+]+", signal["role"])
        if len(w) >= 4
    ]
    role_match = (
        signal["role"].lower() in low
        or sum(1 for w in role_words if w in low) >= max(1, len(role_words) // 2)
    )
    career_signal = any(
        phrase in low
        for phrase in (
            "current openings",
            "open positions",
            "careers",
            "join our team",
            "we are hiring",
            "apply now",
            "submit your application",
            "send your resume",
            "send your cv",
        )
    )
    if not (role_match or career_signal):
        return None

    candidate["source_type"] = "Fresh Job Signal"
    candidate["discovered_from"] = (
        f"{signal.get('engine','Search')} fresh signal | "
        f"{signal['role']} | {signal.get('company','Unknown employer')}"
    )
    candidate["status"] = "New"
    return candidate


def resolve_signal(signal, cfg):
    # If search result itself is an official/non-blocked page, try it first.
    signal_url = signal.get("signal_url", "")
    if signal_url and not blocked_signal_domain(signal_url):
        signal_result = {
            "title": signal.get("signal_title", ""),
            "url": signal_url,
            "snippet": signal.get("signal_snippet", ""),
            "engine": signal.get("engine", ""),
        }
        direct_job_page = source_from_direct_job_page(signal_result, signal, cfg)
        if direct_job_page:
            return direct_job_page

        direct = source_from_official_result(signal_result, signal, cfg)
        if direct:
            return direct

    # For LinkedIn / job-board search signals, never fetch the blocked page.
    # Instead, resolve the company/role back to an official public source.
    for query in official_resolution_queries(signal):
        for result in disc.search_web(query, cfg):
            source = source_from_direct_job_page(result, signal, cfg)
            if source:
                return source
            source = source_from_official_result(result, signal, cfg)
            if source:
                return source
        time.sleep(float(cfg.get("fresh_request_delay_seconds", 0.8)))

    return None


def run_fresh_discovery(cfg):
    sheet_id = os.environ.get("GOOGLE_SHEET_ID", "")
    if not sheet_id:
        raise RuntimeError("Missing GOOGLE_SHEET_ID")

    tab = os.environ.get("GOOGLE_SOURCES_TAB", "Sources")
    service = core.sheet_service()
    core.ensure_sources_sheet(service, sheet_id, tab)
    core.seed_sources_registry(service, sheet_id, tab, cfg)
    disc.audit_existing_discovered_sources(service, sheet_id, tab, cfg)

    headers, existing_urls, _ = disc.existing_source_urls(
        service,
        sheet_id,
        tab,
    )

    found = []
    seen_signals = set()
    max_new = int(cfg.get("fresh_max_new_sources_per_run", 20))

    queries = fresh_queries(cfg)
    for query in queries:
        print(f"FRESH QUERY | {query}")
        for result in disc.search_web(query, cfg):
            signal = result_to_signal(result, cfg)
            if not signal:
                continue

            signal_key = (
                signal["role"].lower(),
                signal.get("company", "").lower(),
                signal.get("signal_url", ""),
            )
            if signal_key in seen_signals:
                continue
            seen_signals.add(signal_key)

            # Process recent-looking results first, but do not depend on the hint.
            source = resolve_signal(signal, cfg)
            if not source:
                continue

            url = core.canonical_url(source["career_url"])
            if not url or url in existing_urls:
                continue

            existing_urls.add(url)
            found.append(source)
            print(
                f"FRESH SOURCE | {source['company_name']} | "
                f"{signal['role']} | {url}"
            )
            if len(found) >= max_new:
                break

        if len(found) >= max_new:
            break
        time.sleep(float(cfg.get("fresh_request_delay_seconds", 0.8)))

    added = disc.append_sources(service, sheet_id, tab, found)
    print("=" * 80)
    print(f"Fresh queries executed: {len(queries)}")
    print(f"New official/public career sources added: {added}")
    print("=" * 80)
    return added


def self_test():
    cfg = load_config()

    linkedin_signal = result_to_signal(
        {
            "title": "Junior Architect - Arete Design Studio | LinkedIn",
            "snippet": "Chandigarh · 2 hours ago · Easy Apply",
            "url": "https://www.linkedin.com/jobs/view/123456",
            "engine": "Bing RSS",
        },
        cfg,
    )
    assert linkedin_signal is not None
    assert linkedin_signal["role"] == "Junior Architect"
    assert linkedin_signal["company"] == "Arete Design Studio"
    assert blocked_signal_domain(linkedin_signal["signal_url"])
    assert linkedin_signal["recent_hint"] is True

    # V4.4.1 regression: fresh discovery must remain functional even when an
    # older discovery.py lacks DISCOVERY_BLOCKED_DOMAINS.
    original_blocked = getattr(disc, "DISCOVERY_BLOCKED_DOMAINS", None)
    had_attr = hasattr(disc, "DISCOVERY_BLOCKED_DOMAINS")
    try:
        if had_attr:
            delattr(disc, "DISCOVERY_BLOCKED_DOMAINS")
        assert blocked_signal_domain("https://www.linkedin.com/jobs/view/123")
        assert blocked_signal_domain("https://www.archdaily.com/opportunities")
        assert blocked_signal_domain("https://www-archdaily-com.global.ssl.fastly.net/opportunities")
    finally:
        if had_attr:
            setattr(disc, "DISCOVERY_BLOCKED_DOMAINS", original_blocked)

    direct_signal = result_to_signal(
        {
            "title": "Senior Architect - Example Architects",
            "snippet": "We are hiring in Mumbai today.",
            "url": "https://example.com/careers/senior-architect",
            "engine": "DuckDuckGo",
        },
        cfg,
    )
    assert direct_signal["role"] == "Senior Architect"
    assert direct_signal["company"] == "Example Architects"

    queries = fresh_queries(cfg)
    assert queries
    assert any("India" in q for q in queries)

    assert disc.hard_block_reason("https://www.yellowpages.com/jobs")
    assert hasattr(disc, "official_company_quality")

    assert fresh_signal_source_reject_reason("https://www.aia.org/career-growth/transcript", "Architect transcript", "", "")
    assert fresh_signal_source_reject_reason("https://www.architecturelab.net/architect/types", "Types of architect", "", "")
    assert fresh_signal_source_reject_reason("https://nrd.adsttc.com/1184508/story", "Architect", "", "")
    assert not fresh_signal_source_reject_reason(
        "https://examplearchitects.in/jobs/senior-architect",
        "Senior Architect",
        "Apply now for this job in Mumbai, India",
        "Apply now Senior Architect Mumbai India",
    )

    print("FRESH JOB DISCOVERY SELF TEST PASSED")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        return

    cfg = load_config()
    run_fresh_discovery(cfg)


if __name__ == "__main__":
    main()
