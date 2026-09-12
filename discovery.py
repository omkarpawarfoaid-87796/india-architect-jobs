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
    # V4.3 hard blocks: archives, directories, broad job/media/design platforms.
    "web.archive.org",
    "archive.org",
    "yellowpages.com",
    "superpages.com",
    "houzz.com",
    "interiordesign.net",
    "decorilla.com",
    "people.inc",
    "condenast.com",
    # V4.3.1 architecture media / marketplace platforms.
    "archdaily.com",
    "architizer.com",
    "dezeen.com",
    "designboom.com",
    # V4.4.4 article / institute / education / content pages that create false fresh signals.
    "nrd.adsttc.com",
    "adsttc.com",
    "aia.org",
    "architecturelab.net",
    "worldarchitecture.org",
    "archinect.com",
    "e-architect.com",
    "stirworld.com",
    "architectandinteriorsindia.com",
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

# V4.3 company-quality classification.
BUILT_ENVIRONMENT_IDENTITY_TERMS = (
    "architecture practice",
    "architectural practice",
    "architecture studio",
    "architects",
    "architectural design",
    "interior design studio",
    "interior design firm",
    "interior architecture",
    "landscape architecture",
    "landscape design",
    "urban design",
    "urban planning",
    "master planning",
    "masterplanning",
    "aec consultancy",
    "design consultancy",
    "built environment",
    "architecture and design",
    "architecture & design",
)

NEGATIVE_COMPANY_IDENTITY_TERMS = (
    "software company",
    "software platform",
    "saas",
    "cloud platform",
    "design software",
    "architecture software",
    "bim software",
    "collaboration platform",
    "publisher",
    "publishing company",
    "magazine",
    "media company",
    "news media",
    "job board",
    "job portal",
    "recruitment platform",
    "staffing company",
    "talent platform",
    "marketplace",
    "directory",
    "yellow pages",
    "digital publisher",
)

INDIA_LOCATION_TERMS = (
    "india",
    "mumbai",
    "navi mumbai",
    "thane",
    "pune",
    "new delhi",
    "delhi",
    "gurugram",
    "gurgaon",
    "noida",
    "bengaluru",
    "bangalore",
    "hyderabad",
    "chennai",
    "kolkata",
    "ahmedabad",
    "jaipur",
    "kochi",
    "cochin",
    "goa",
    "chandigarh",
    "indore",
    "surat",
    "coimbatore",
    "lucknow",
    "bhubaneswar",
    "nagpur",
    "vadodara",
    "dehradun",
    "guwahati",
)

GENERIC_PLATFORM_NAME_TERMS = (
    "yellow pages",
    "what's nearby",
    "jobs",
    "careers in",
    "publisher",
    "magazine",
    "houzz",
    "superpages",
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
    if hard_block_reason(url) or core.is_blocked_domain(url, cfg):
        return None

    # V4.4.4: do not let city/service landing pages become Sources.
    title = clean(result.get("title") or "")
    snippet = clean(result.get("snippet") or "")
    reason = core.real_vacancy_reject_reason(
        {"title": title, "description": snippet, "source_url": url}, cfg
    )
    if reason and any(key in reason.lower() for key in ("service/location", "marketing/service", "region/career")):
        return None
    try:
        r = requests.get(
            url,
            timeout=int(cfg.get("request_timeout_seconds", 15)),
            headers={
                "User-Agent": (
                    "ArchitectJobsDiscovery/4.4.4.2 "
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



def hard_block_reason(url):
    d = host(url)
    if not d:
        return "Missing domain"

    try:
        cfg = load_config()
        reason = core.blocked_domain_reason(url, cfg)
        if reason:
            return reason
    except Exception:
        pass

    aliases = core.hostname_aliases(d)
    for blocked_domain in DISCOVERY_BLOCKED_DOMAINS:
        bd = blocked_domain.lower().removeprefix("www.")
        for candidate in aliases:
            stripped = candidate.removeprefix("www.")
            if stripped == bd or stripped.endswith("." + bd):
                return f"Blocked platform/domain: {blocked_domain}"
            if any(stripped.endswith("." + suffix) or stripped == suffix for suffix in core.CDN_MIRROR_SUFFIXES) and bd in stripped:
                return f"Blocked CDN/mirror alias of: {blocked_domain}"
    return ""


def india_relevance_score(url, page_text="", city_hint="", state_hint=""):
    """
    Require independent India evidence. Search-query city alone is NOT enough.

    Strong:
    - .in / .co.in domain
    - official page explicitly mentions India
    - official page explicitly mentions a known Indian city/state hint
    """
    score = 0
    d = host(url).lower()
    low = clean(page_text).lower()

    if d.endswith(".in") or d.endswith(".co.in"):
        score += 4

    if re.search(r"\bindia\b", low):
        score += 3

    for term in INDIA_LOCATION_TERMS:
        if re.search(rf"\b{re.escape(term)}\b", low):
            score += 2
            break

    city_hint = clean(city_hint).lower()
    state_hint = clean(state_hint).lower()
    if city_hint and re.search(rf"\b{re.escape(city_hint)}\b", low):
        score += 2
    if state_hint and re.search(rf"\b{re.escape(state_hint)}\b", low):
        score += 1

    return score


def company_identity_score(page_text, company_name=""):
    low = f"{clean(company_name)} {clean(page_text)}".lower()
    score = 0

    for term in BUILT_ENVIRONMENT_IDENTITY_TERMS:
        if term in low:
            score += 3

    # Weaker identity evidence.
    if re.search(r"\barchitects?\b", low):
        score += 2
    if "interior design" in low:
        score += 2
    if "landscape" in low and ("design" in low or "architecture" in low):
        score += 2
    if "urban design" in low or "urban planning" in low:
        score += 2

    for term in NEGATIVE_COMPANY_IDENTITY_TERMS:
        if term in low:
            score -= 5

    return score


def generic_platform_name(company_name):
    low = clean(company_name).lower()
    return any(term in low for term in GENERIC_PLATFORM_NAME_TERMS)


def trusted_design_employer_domain(url, cfg=None):
    """
    Some legitimate India design employers are design-led businesses rather
    than traditional architecture studios. V4.3.1 allows a small explicit
    config list to pass the employer-identity gate, while still requiring
    independent India evidence and a real public careers page.
    """
    cfg = cfg or {}
    d = host(url).lower()
    trusted = {
        clean(x).lower().removeprefix("www.")
        for x in cfg.get("trusted_design_employer_domains", [])
        if clean(x)
    }
    d = d.removeprefix("www.")
    return any(d == x or d.endswith("." + x) for x in trusted)


def official_company_quality(
    url,
    company_name,
    page_text,
    city_hint="",
    state_hint="",
    cfg=None,
):
    """
    Returns (accepted, reason, scores).

    V4.3.1 classification order:
    1. hard-block media/directories/job platforms;
    2. require independent India evidence;
    3. accept verified built-environment identity;
    4. or accept a small configured trusted design-employer list.
    """
    cfg = cfg or {}

    reason = hard_block_reason(url)
    if reason:
        return False, reason, {"india": 0, "identity": 0}

    if generic_platform_name(company_name):
        return False, "Generic directory/media/job-platform name", {
            "india": 0,
            "identity": 0,
        }

    india_score = india_relevance_score(
        url,
        page_text=page_text,
        city_hint=city_hint,
        state_hint=state_hint,
    )
    identity_score = company_identity_score(page_text, company_name)

    if india_score < 2:
        return False, f"Insufficient India evidence (score {india_score})", {
            "india": india_score,
            "identity": identity_score,
        }

    if trusted_design_employer_domain(url, cfg):
        return True, "", {
            "india": india_score,
            "identity": max(identity_score, 2),
        }

    if identity_score < 2:
        return False, (
            f"Not verified as a built-environment/design employer "
            f"(score {identity_score})"
        ), {
            "india": india_score,
            "identity": identity_score,
        }

    return True, "", {
        "india": india_score,
        "identity": identity_score,
    }


def homepage_identity_context(url, cfg):
    """
    Fetch official homepage for company identity/country evidence.
    Career pages alone may contain generic job text that creates false positives.
    """
    base = origin(url)
    r = fetch(base, cfg)
    if not r:
        return "", ""
    soup = BeautifulSoup(r.text, "html.parser")
    text = core.best_main_text(soup)
    name = company_name_from_page(soup, r.url)
    return name, text



def careerish_url(url):
    """
    V4.4.4: stricter than older versions.

    Accept real hiring paths such as /careers, /career, /jobs, /join-us,
    /work-with-us, /openings. Do not treat content paths such as
    /career-growth/transcript or /architect/types as career pages.
    """
    p = urlparse(url)
    path = (p.path or "").lower().strip("/")
    if not path:
        return False

    content_bad_segments = {
        "article", "articles", "news", "blog", "blogs", "stories",
        "story", "transcript", "types", "guide", "guides", "learn",
        "education", "career-growth", "resources", "resource",
        "magazine", "project", "projects", "portfolio",
    }
    segments = [s for s in re.split(r"[/_.]+", path.replace("-", "-")) if s]
    if any(seg in content_bad_segments for seg in segments):
        return False
    if "career-growth" in path or "architect/types" in path:
        return False

    exact_good = {
        "career", "careers", "jobs", "job", "join-us", "joinus",
        "work-with-us", "workwithus", "openings", "opening",
        "vacancies", "vacancy", "hiring", "apply", "current-openings",
        "opportunities",
    }
    normalized_segments = {s.replace("_", "-") for s in segments}
    if normalized_segments & exact_good:
        return True

    # Support career.html, careers.php, jobs.aspx etc.
    tail = segments[-1].replace("-", "").replace("_", "") if segments else ""
    if tail in {"career", "careers", "jobs", "job", "joinus", "workwithus", "openings", "vacancies", "hiring"}:
        return True

    # Direct job detail pages are allowed when they clearly contain job terms.
    if re.search(r"/(jobs?|careers?|openings?|positions?|vacanc(?:y|ies))/(?:[^/]+)", "/" + path):
        return True

    return False


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


def content_or_nonhiring_page_reason(url, page_text=""):
    """Reject content/institute/info pages that mention architects but are not hiring."""
    p = urlparse(url)
    path = (p.path or "").lower()
    d = host(url).lower()

    hard_content_domains = {
        "aia.org",
        "architecturelab.net",
        "nrd.adsttc.com",
        "adsttc.com",
        "worldarchitecture.org",
        "archinect.com",
        "e-architect.com",
        "stirworld.com",
        "architectandinteriorsindia.com",
    }
    if any(d == x or d.endswith("." + x) for x in hard_content_domains):
        return f"Blocked content/institute domain: {d}"

    bad_path_bits = (
        "/career-growth/", "/transcript", "/architect/types", "/article/",
        "/articles/", "/news/", "/blog/", "/blogs/", "/guide/",
        "/guides/", "/education/", "/resources/", "/project/", "/projects/",
    )
    if any(bit in path for bit in bad_path_bits):
        low = clean(page_text).lower()
        hiring_terms = (
            "apply now", "apply for this job", "submit application",
            "send your resume", "send your cv", "current openings",
            "open positions", "job opening", "job openings", "vacancy",
        )
        if not any(term in low for term in hiring_terms):
            return "Content/info page without hiring application signal"

    return ""



def validate_career_page(
    url,
    cfg,
    relevance_context="",
    city_hint="",
    state_hint="",
):
    r = fetch(url, cfg)
    if not r:
        return None

    block_reason = hard_block_reason(r.url)
    if block_reason:
        return None

    soup = BeautifulSoup(r.text, "html.parser")
    career_text = core.best_main_text(soup)

    content_reason = content_or_nonhiring_page_reason(r.url, career_text)
    if content_reason:
        return None

    # Resolve identity from the official homepage, not only from the career page.
    homepage_name, homepage_text = homepage_identity_context(r.url, cfg)
    company_name = homepage_name or company_name_from_page(soup, r.url)

    combined = " ".join(
        x for x in (
            relevance_context,
            homepage_text,
            career_text,
            clean(soup.title.get_text(" ", strip=True) if soup.title else ""),
        )
        if x
    )

    accepted, reason, scores = official_company_quality(
        r.url,
        company_name,
        combined,
        city_hint=city_hint,
        state_hint=state_hint,
        cfg=cfg,
    )
    if not accepted:
        return None

    low = career_text.lower()
    has_career_signal = (
        careerish_url(r.url)
        or any(x in low for x in (
            "careers",
            "career opportunities",
            "current openings",
            "open positions",
            "join our team",
            "we are hiring",
            "submit your application",
            "send your resume",
            "send your cv",
            "apply now",
        ))
    )
    if not has_career_signal:
        return None

    return {
        "career_url": canonical(r.url),
        "company_name": company_name,
        "company_website": origin(r.url),
        "page_text": career_text,
        "quality_reason": (
            (
                "Verified trusted India design employer "
                if trusted_design_employer_domain(r.url, cfg)
                else "Verified India built-environment employer "
            )
            + f"(India {scores['india']}, Identity {scores['identity']})"
        ),
    }


def discover_from_result(result, city, cfg):
    url = canonical(result.get("url"))
    if not url:
        return None
    if hard_block_reason(url) or core.is_blocked_domain(url, cfg):
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
        state = core.CITY_STATE.get(city.lower(), "")
        valid = validate_career_page(
            career_url,
            cfg,
            relevance_context=context,
            city_hint=city,
            state_hint=state,
        )
        if not valid:
            continue

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
            "quality_reason": valid.get("quality_reason", ""),
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



def reject_source_row(service, sheet_id, tab, headers, row_num, rec, reason):
    rec["status"] = "Rejected"
    rec["quality_reason"] = reason
    row_values = [rec.get(h, "") for h in headers]
    service.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range=f"'{tab}'!A{row_num}:{core.column_letter(len(headers))}{row_num}",
        valueInputOption="RAW",
        body={"values": [row_values]},
    ).execute()


def audit_existing_discovered_sources(service, sheet_id, tab, cfg):
    """
    Clean previously discovered junk without deleting history.

    Seed Website rows are preserved unless they are on a hard-blocked domain.
    Discovered Website / Fresh Job Signal rows must pass V4.3 official-employer
    and India quality verification.
    """
    headers = core.ensure_sources_sheet(service, sheet_id, tab)
    values = core.read_sheet_values(service, sheet_id, tab)
    if not values:
        return 0

    rejected = 0
    max_audit = int(cfg.get("source_quality_audit_batch_size", 60))
    audited = 0

    for row_num, row in enumerate(values[1:], start=2):
        if audited >= max_audit:
            break

        rec = core.row_to_record(headers, row)
        status = clean(rec.get("status") or "").lower()
        url = canonical(rec.get("career_url") or "")
        source_type = clean(rec.get("source_type") or "")

        # V4.3.1: previously rejected trusted design employers (e.g. Livspace)
        # get one more audit pass so they can be restored to Active.
        if status == "rejected" and not trusted_design_employer_domain(url, cfg):
            continue

        block_reason = hard_block_reason(url)
        if block_reason:
            reject_source_row(
                service, sheet_id, tab, headers, row_num, rec, block_reason
            )
            rejected += 1
            continue

        # Preserve manual seed list unless explicitly hard-blocked.
        if source_type == "Seed Website":
            continue

        if source_type not in ("Discovered Website", "Fresh Job Signal"):
            continue

        audited += 1
        city = clean(rec.get("city") or "")
        state = clean(rec.get("state") or "")

        valid = validate_career_page(
            url,
            cfg,
            city_hint=city,
            state_hint=state,
        )
        if not valid:
            reject_source_row(
                service,
                sheet_id,
                tab,
                headers,
                row_num,
                rec,
                "Failed V4.3 India/built-environment source-quality gate",
            )
            rejected += 1
        else:
            rec["quality_reason"] = valid.get("quality_reason", "")
            if status in ("warning", "new", "", "rejected"):
                # Trusted employers that were falsely rejected can be restored.
                if status != "warning":
                    rec["status"] = "Active"
            row_values = [rec.get(h, "") for h in headers]
            service.spreadsheets().values().update(
                spreadsheetId=sheet_id,
                range=f"'{tab}'!A{row_num}:{core.column_letter(len(headers))}{row_num}",
                valueInputOption="RAW",
                body={"values": [row_values]},
            ).execute()

    if rejected:
        print(f"SOURCE QUALITY AUDIT | rejected {rejected} existing sources")
    return rejected



def run_discovery(cfg):
    sheet_id = os.environ.get("GOOGLE_SHEET_ID", "")
    if not sheet_id:
        raise RuntimeError("Missing GOOGLE_SHEET_ID")

    tab = os.environ.get("GOOGLE_SOURCES_TAB", "Sources")
    svc = core.sheet_service()
    core.ensure_sources_sheet(svc, sheet_id, tab)
    core.seed_sources_registry(svc, sheet_id, tab, cfg)
    audit_existing_discovered_sources(svc, sheet_id, tab, cfg)

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

    # V4.3 quality regressions.
    assert hard_block_reason("https://www.yellowpages.com/search")
    assert hard_block_reason("https://web.archive.org/web/123/example.com")
    assert hard_block_reason("https://www.houzz.com/jobs")

    accepted, reason, scores = official_company_quality(
        "https://examplearchitects.in/careers",
        "Example Architects",
        "Example Architects is an architecture studio in Mumbai, India.",
        city_hint="Mumbai",
        state_hint="Maharashtra",
    )
    assert accepted is True
    assert scores["india"] >= 2 and scores["identity"] >= 2

    accepted, reason, _ = official_company_quality(
        "https://example-software.com/careers",
        "Example Software",
        "Cloud software platform and SaaS collaboration product.",
        city_hint="Mumbai",
    )
    assert accepted is False

    assert hard_block_reason("https://www.archdaily.com/opportunities")
    assert hard_block_reason("https://architizer.com/jobs")
    assert hard_block_reason("https://www-archdaily-com.global.ssl.fastly.net/opportunities")

    trusted_cfg = {
        "trusted_design_employer_domains": ["livspace.com"],
    }
    accepted, reason, scores = official_company_quality(
        "https://www.livspace.com/in/careers",
        "Livspace India",
        "Livspace India provides home interior design services across India.",
        city_hint="Mumbai",
        cfg=trusted_cfg,
    )
    assert accepted is True
    assert trusted_design_employer_domain(
        "https://www.livspace.com/in/careers",
        trusted_cfg,
    )

    assert hard_block_reason("https://nrd.adsttc.com/1184508/story")
    assert content_or_nonhiring_page_reason("https://www.aia.org/career-growth/transcript", "Architect career transcript")
    assert content_or_nonhiring_page_reason("https://www.architecturelab.net/architect/types", "Types of architect article")
    assert not careerish_url("https://www.aia.org/career-growth/transcript")
    assert not careerish_url("https://www.architecturelab.net/architect/types")
    assert careerish_url("https://examplearchitects.in/careers")
    assert careerish_url("https://examplearchitects.in/jobs/senior-architect")

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
