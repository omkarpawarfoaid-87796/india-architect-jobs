import argparse
import hashlib
import html
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, date
from urllib.parse import quote_plus, urljoin, urlparse, parse_qs, unquote

import requests
import yaml
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

import collector as core


DISCOVERY_HEADERS = core.SOURCE_HEADERS

DISCOVERY_BLOCKED_DOMAINS = {
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
}

CAREER_WORDS = (
    "career",
    "careers",
    "job",
    "jobs",
    "opening",
    "openings",
    "join-us",
    "join us",
    "work-with-us",
    "work with us",
    "opportunities",
    "vacancies",
)

COMPANY_RELEVANCE_TERMS = (
    "architecture",
    "architects",
    "architectural",
    "interior design",
    "design studio",
    "landscape architecture",
    "urban design",
    "urban planning",
    "masterplanning",
    "bim",
    "built environment",
    "aec",
)

PORTAL_TERMS = (
    "job portal",
    "search jobs",
    "jobs in india",
    "job vacancies",
    "recruitment portal",
)


def clean(value):
    return core.clean_text(value)


def canonical(url):
    return core.canonical_url(url)


def host(url):
    return core.domain(url)


def origin(url):
    return core.origin(url)


def blocked(url):
    d = host(url)
    if not d:
        return True
    if any(d == x or d.endswith("." + x) for x in DISCOVERY_BLOCKED_DOMAINS):
        return True
    return core.is_blocked_domain(url, load_config())


def load_config():
    with open("config.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def fetch(url, cfg):
    if not url:
        return None
    d = host(url)
    if any(d == x or d.endswith("." + x) for x in DISCOVERY_BLOCKED_DOMAINS):
        return None
    if core.is_blocked_domain(url, cfg):
        return None
    try:
        r = requests.get(
            url,
            timeout=int(cfg.get("request_timeout_seconds", 15)),
            headers={
                "User-Agent": (
                    "ArchitectJobsDiscovery/4.1 "
                    "(public-source discovery; no authentication bypass)"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
            allow_redirects=True,
        )
        if r.status_code >= 400:
            return None
        if core.login_gated_url(r.url, cfg):
            return None
        return r
    except requests.RequestException:
        return None


def unwrap_ddg_url(url):
    if not url:
        return ""
    if "duckduckgo.com/l/?" in url:
        q = parse_qs(urlparse(url).query)
        target = q.get("uddg", [""])[0]
        if target:
            return unquote(target)
    return url


def search_bing_rss(query, cfg):
    url = "https://www.bing.com/search?format=rss&q=" + quote_plus(query)
    r = fetch(url, cfg)
    if not r:
        return []
    try:
        root = ET.fromstring(r.text)
    except ET.ParseError:
        return []

    out = []
    for item in root.findall(".//item"):
        title = clean(item.findtext("title") or "")
        link = canonical(item.findtext("link") or "")
        snippet = clean(item.findtext("description") or "")
        if link:
            out.append({"title": title, "url": link, "snippet": snippet, "engine": "Bing RSS"})
    return out


def search_duckduckgo(query, cfg):
    url = "https://html.duckduckgo.com/html/?q=" + quote_plus(query)
    r = fetch(url, cfg)
    if not r:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    out = []
    for result in soup.select(".result"):
        a = result.select_one("a.result__a")
        if not a:
            continue
        link = canonical(unwrap_ddg_url(urljoin(r.url, a.get("href") or "")))
        title = clean(a.get_text(" ", strip=True))
        sn = result.select_one(".result__snippet")
        snippet = clean(sn.get_text(" ", strip=True)) if sn else ""
        if link:
            out.append({"title": title, "url": link, "snippet": snippet, "engine": "DuckDuckGo"})
    return out


def search_web(query, cfg):
    results = []
    seen = set()
    for provider in (search_bing_rss, search_duckduckgo):
        for item in provider(query, cfg):
            u = canonical(item.get("url"))
            if not u or u in seen:
                continue
            seen.add(u)
            results.append(item)
            if len(results) >= int(cfg.get("discovery_results_per_query", 12)):
                return results
        if results:
            # Prefer one provider per query to keep free request volume low.
            break
    return results


def careerish_url(url):
    path = (urlparse(url).path or "").lower()
    return any(word.replace(" ", "-") in path or word.replace("-", "") in path.replace("-", "")
               for word in CAREER_WORDS)


def relevance_score(text):
    low = clean(text).lower()
    score = sum(2 for term in COMPANY_RELEVANCE_TERMS if term in low)
    score -= sum(3 for term in PORTAL_TERMS if term in low)
    return score


def company_name_from_page(soup, url):
    og = soup.find("meta", attrs={"property": "og:site_name"})
    if og and clean(og.get("content")):
        return clean(og.get("content"))

    h1 = soup.find("h1")
    if h1:
        value = clean(h1.get_text(" ", strip=True))
        if 2 <= len(value) <= 100 and "career" not in value.lower():
            return value

    if soup.title:
        title = clean(soup.title.get_text(" ", strip=True))
        if title:
            for sep in (" | ", " – ", " - ", " — "):
                if sep in title:
                    parts = [p.strip() for p in title.split(sep) if p.strip()]
                    noncareer = [
                        p for p in parts
                        if not any(w in p.lower() for w in ("career", "jobs", "openings"))
                    ]
                    if noncareer:
                        return noncareer[-1][:100]
            return title[:100]

    return core.company_name_from_domain(url)


def candidate_career_links(page_url, soup, cfg):
    out = []
    seen = set()

    if careerish_url(page_url):
        seen.add(canonical(page_url))
        out.append(canonical(page_url))

    for a in soup.find_all("a", href=True):
        label = clean(a.get_text(" ", strip=True)).lower()
        href = canonical(urljoin(page_url, a.get("href")))
        if not href or core.is_blocked_domain(href, cfg):
            continue
        hlow = href.lower()
        if any(w in label or w.replace(" ", "-") in hlow for w in CAREER_WORDS):
            if href not in seen:
                seen.add(href)
                out.append(href)

    # Probe common public paths only on the official domain.
    base = origin(page_url)
    for path in (
        "/careers",
        "/career",
        "/jobs",
        "/join-us",
        "/work-with-us",
        "/opportunities",
        "/openings",
    ):
        u = canonical(urljoin(base + "/", path.lstrip("/")))
        if u not in seen:
            seen.add(u)
            out.append(u)

    return out[: int(cfg.get("discovery_max_career_candidates_per_site", 12))]


def validate_career_page(url, cfg, relevance_context=""):
    r = fetch(url, cfg)
    if not r:
        return None
    if host(r.url) in DISCOVERY_BLOCKED_DOMAINS:
        return None

    soup = BeautifulSoup(r.text, "html.parser")
    text = core.best_main_text(soup)
    combined = f"{relevance_context} {text} {clean(soup.title.get_text(' ', strip=True) if soup.title else '')}"

    # Require built-environment/company relevance.
    if relevance_score(combined) < int(cfg.get("discovery_min_relevance_score", 2)):
        return None

    low = combined.lower()
    has_career_signal = (
        careerish_url(r.url)
        or any(x in low for x in (
            "careers", "career opportunities", "current openings", "open positions",
            "join our team", "we are hiring", "submit your application",
            "send your resume", "send your cv",
        ))
    )
    if not has_career_signal:
        return None

    return {
        "career_url": canonical(r.url),
        "company_name": company_name_from_page(soup, r.url),
        "company_website": origin(r.url),
        "page_text": text,
    }


def discover_from_result(result, city, cfg):
    url = canonical(result.get("url"))
    if not url:
        return None
    d = host(url)
    if any(d == x or d.endswith("." + x) for x in DISCOVERY_BLOCKED_DOMAINS):
        return None
    if core.is_blocked_domain(url, cfg):
        return None

    r = fetch(url, cfg)
    if not r:
        return None
    soup = BeautifulSoup(r.text, "html.parser")
    text = core.best_main_text(soup)
    context = f"{result.get('title','')} {result.get('snippet','')} {text}"

    # Skip obvious portals/aggregators.
    if relevance_score(context) < int(cfg.get("discovery_min_relevance_score", 2)):
        return None

    candidates = candidate_career_links(r.url, soup, cfg)
    for career_url in candidates:
        valid = validate_career_page(career_url, cfg, relevance_context=context)
        if not valid:
            continue

        state = core.CITY_STATE.get(city.lower(), "")
        return {
            "source_id": core._source_id(valid["career_url"]),
            "company_name": valid["company_name"],
            "company_website": valid["company_website"],
            "career_url": valid["career_url"],
            "city": city,
            "state": state,
            "source_type": "Discovered Website",
            "discovered_from": f"{result.get('engine','Search')} | {result.get('query','')}",
            "first_discovered": core.now_ist().isoformat(timespec="seconds"),
            "last_checked": "",
            "last_success": "",
            "jobs_found": "0",
            "consecutive_failures": "0",
            "status": "New",
        }
    return None


def selected_cities(cfg):
    cities = cfg.get("discovery_cities", [])
    if not cities:
        return []
    per_day = int(cfg.get("discovery_cities_per_day", 5))
    per_day = max(1, min(per_day, len(cities)))
    start = (date.today().toordinal() * per_day) % len(cities)
    return [cities[(start + i) % len(cities)] for i in range(per_day)]


def discovery_queries(city, cfg):
    templates = cfg.get("discovery_query_templates", [])
    return [template.format(city=city) for template in templates]


def existing_source_urls(service, sheet_id, tab):
    headers = core.ensure_sources_sheet(service, sheet_id, tab)
    values = core.read_sheet_values(service, sheet_id, tab)
    urls = set()
    domains = set()
    if values:
        for row in values[1:]:
            rec = core.row_to_record(headers, row)
            u = canonical(rec.get("career_url") or "")
            if u:
                urls.add(u)
                domains.add(host(u))
    return headers, urls, domains


def append_sources(service, sheet_id, tab, rows):
    if not rows:
        return 0
    headers = core.ensure_sources_sheet(service, sheet_id, tab)
    values = [[row.get(h, "") for h in headers] for row in rows]
    service.spreadsheets().values().append(
        spreadsheetId=sheet_id,
        range=f"'{tab}'!A:{core.column_letter(len(headers))}",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": values},
    ).execute()
    return len(values)


def run_discovery(cfg):
    sheet_id = os.environ.get("GOOGLE_SHEET_ID", "")
    if not sheet_id:
        raise RuntimeError("Missing GOOGLE_SHEET_ID")

    tab = os.environ.get("GOOGLE_SOURCES_TAB", "Sources")
    svc = core.sheet_service()
    core.ensure_sources_sheet(svc, sheet_id, tab)
    core.seed_sources_registry(svc, sheet_id, tab, cfg)

    headers, existing_urls, existing_domains = existing_source_urls(svc, sheet_id, tab)

    max_queries = int(cfg.get("discovery_max_queries_per_day", 24))
    max_new = int(cfg.get("discovery_max_new_sources_per_day", 30))
    query_count = 0
    found = []

    for city in selected_cities(cfg):
        for query in discovery_queries(city, cfg):
            if query_count >= max_queries or len(found) >= max_new:
                break
            query_count += 1
            print(f"DISCOVERY QUERY | {query}")
            results = search_web(query, cfg)

            for result in results:
                result["query"] = query
                source = discover_from_result(result, city, cfg)
                if not source:
                    continue

                u = canonical(source["career_url"])
                d = host(u)
                if u in existing_urls:
                    continue

                # One canonical careers source per exact URL. Multiple domains are
                # allowed because large firms sometimes use separate ATS domains.
                existing_urls.add(u)
                existing_domains.add(d)
                found.append(source)
                print(
                    f"DISCOVERED | {source['company_name']} | "
                    f"{source['career_url']} | {city}"
                )
                if len(found) >= max_new:
                    break

            time.sleep(float(cfg.get("discovery_request_delay_seconds", 1.2)))

        if query_count >= max_queries or len(found) >= max_new:
            break

    added = append_sources(svc, sheet_id, tab, found)
    print("=" * 80)
    print(f"Discovery queries: {query_count}")
    print(f"New verified public career sources added: {added}")
    print("=" * 80)


def self_test():
    cfg = load_config()

    sample_rss = """<?xml version="1.0"?>
    <rss><channel><item><title>Studio Careers</title>
    <link>https://example.com/careers</link>
    <description>Architecture studio careers in Mumbai</description>
    </item></channel></rss>"""
    root = ET.fromstring(sample_rss)
    assert root.find(".//item/link").text == "https://example.com/careers"

    sample_html = """
    <html><head><title>Careers | Example Architects</title></head>
    <body><h1>Careers</h1><p>We are an architecture and interior design studio.</p>
    <a href="/careers">Current Openings</a></body></html>
    """
    soup = BeautifulSoup(sample_html, "html.parser")
    links = candidate_career_links("https://example.com", soup, cfg)
    assert "https://example.com/careers" in links
    assert relevance_score("Architecture and interior design studio") >= 2
    assert core._source_id("https://example.com/careers").startswith("SRC-")

    print("DISCOVERY SELF TEST PASSED")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        return

    cfg = load_config()
    run_discovery(cfg)


if __name__ == "__main__":
    main()
