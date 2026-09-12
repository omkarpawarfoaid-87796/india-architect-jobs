"""
Single Sheet Job Finder V6

Goal:
- One Google Sheet output only: Sheet1
- No CandidateJobs, RejectedJobs, Sources, AutomationOutput, or _CollectorMeta required
- Search wider, but publish only verified website-ready jobs
- LinkedIn/job boards are signals only; final apply URL is never LinkedIn/login-gated

This script uses collector.py as a parsing/validation library, but it does NOT call
collector.write_sheet() and does NOT use the old meta/source/report tabs.
"""

import argparse
import hashlib
import html
import json
import os
import re
import time
from datetime import date, timedelta
from urllib.parse import quote, urljoin, urlparse, parse_qs, unquote
import xml.etree.ElementTree as ET

import requests
import yaml
from bs4 import BeautifulSoup

import collector as core

VERSION = "V6.2-CLEAN-QUANTITY"
ONE_SHEET_TAB = "Sheet1"

# Final website schema. Do not add or remove columns without developer approval.
WEBSITE_HEADERS = core.WEBSITE_HEADERS

LINKEDIN_DOMAINS = {"linkedin.com", "www.linkedin.com"}

PUBLIC_JOB_BOARD_DOMAINS = {
    "indeed.com", "in.indeed.com", "www.indeed.com",
    "naukri.com", "www.naukri.com",
    "foundit.in", "www.foundit.in",
    "glassdoor.com", "glassdoor.co.in", "www.glassdoor.co.in",
    "shine.com", "www.shine.com",
    "timesjobs.com", "www.timesjobs.com",
    "internshala.com", "www.internshala.com",
}

# LinkedIn stays signal-only. Other job boards can be published in Quantity Mode
# only when the URL looks like a specific job detail page, not a search/list page.
SIGNAL_ONLY_DOMAINS = LINKEDIN_DOMAINS | PUBLIC_JOB_BOARD_DOMAINS | {
    "hirist.tech", "www.hirist.tech",
}

CONTENT_OR_DIRECTORY_DOMAINS = {
    "archdaily.com", "www.archdaily.com",
    "architecturelab.net", "www.architecturelab.net",
    "aia.org", "www.aia.org",
    "houzz.com", "www.houzz.com",
    "yellowpages.com", "www.yellowpages.com",
    "superpages.com", "www.superpages.com",
    "interiordesign.net", "www.interiordesign.net",
    "decorilla.com", "www.decorilla.com",
    "dezeen.com", "www.dezeen.com",
    "designboom.com", "www.designboom.com",
    "architizer.com", "www.architizer.com",
    "web.archive.org", "archive.org",
    # V6.2: block educational/content/product/reference pages that V6.1 wrongly accepted.
    "dictionary.cambridge.org", "www.dictionary.com", "dictionary.com", "merriam-webster.com", "www.merriam-webster.com",
    "britannica.com", "www.britannica.com",
    "wikipedia.org", "en.wikipedia.org", "en.m.wikipedia.org", "m.wikipedia.org",
    "architecturaldigest.com", "www.architecturaldigest.com",
    "autodesk.com", "www.autodesk.com", "boards.autodesk.com", "dotcom-publish-iac-default.efddotcom-stg.autodesk.com",
    "bigrentz.com", "www.bigrentz.com", "bimobject.com", "www.bimobject.com",
    "bim.com", "www.bim.com", "trimble.com", "www.trimble.com",
    "bimcollab.com", "www.bimcollab.com", "engineeringcivil.org", "www.engineeringcivil.org",
    "archilabs.ai", "www.archilabs.ai", "revitgods.com", "www.revitgods.com",
    "loharchitects.com", "www.loharchitects.com", "spfa.com", "www.spfa.com",
    "architecturaldesigns.com", "www.architecturaldesigns.com",
}

FINAL_BLOCKED_DOMAINS = LINKEDIN_DOMAINS | CONTENT_OR_DIRECTORY_DOMAINS

ROLE_KEYWORDS = (
    "architect", "architecture", "architectural", "interior designer",
    "interior architect", "urban designer", "urban planner", "landscape architect",
    "bim", "revit", "visualizer", "visualiser", "3d render", "3d artist",
    "draftsman", "draughtsman", "autocad", "site architect", "design manager",
)

SOFTWARE_EXCLUDES = (
    "software architect", "solution architect", "solutions architect", "cloud architect",
    "enterprise architect", "data architect", "security architect", "network architect",
    "technical architect", "platform architect", "aws architect", "azure architect",
    "java architect", ".net architect", "salesforce architect",
)

SERVICE_PAGE_PREFIXES = (
    "interior designers in ", "best interior designers in ",
    "home interior designers in ", "modular kitchen designs in ",
    "wardrobe designs in ", "bedroom designs in ", "living room designs in ",
)

GENERIC_BUCKET_TITLES = {
    "australia & new zealand", "early careers", "graduate careers",
    "india early careers", "students and graduates", "career areas",
    "search jobs", "job search", "careers", "current openings", "open positions",
}

SERVICE_TEXT_MARKERS = (
    "get free estimate", "design gallery", "store locator", "45-day delivery",
    "45 day delivery", "10-year warranty", "10 year warranty", "easy emis",
    "home interior cost", "modular kitchen cost", "book free design session",
    "experience centre", "visit our experience centre", "own a homelane franchise",
)


VACANCY_TERMS = (
    "apply now", "apply for this job", "apply for this role", "send your resume",
    "send resume", "send your cv", "send cv", "current opening", "current openings",
    "open position", "open positions", "job opening", "job openings", "vacancy",
    "vacancies", "we are hiring", "we're hiring", "hiring", "join our team",
    "careers@", "jobs@", "hr@", "requirements", "responsibilities", "experience",
    "years experience", "yrs experience", "qualification", "job description",
)

CONTENT_TITLE_MARKERS = (
    "definition", "meaning", "what is", "what are", "explained", "guide to",
    "homepage", "news", "designs and projects", "download", "objects", "free trial",
    "learn", "article", "blog", "magazine", "dictionary", "wikipedia",
)


def looks_like_reference_or_content_page(title='', description='', url=''):
    title_l = clean(title).lower()
    desc_l = clean(description).lower()
    url_l = clean(url).lower()
    if domain_matches(url, CONTENT_OR_DIRECTORY_DOMAINS):
        return True
    if title_l in {"architecture", "architect", "architectural", "autodesk revit", "3d modeling", "building information modeling", "bim objects"}:
        return True
    if any(m in title_l for m in CONTENT_TITLE_MARKERS):
        # Real vacancies rarely have these as the title. This catches dictionary/article/product pages.
        if not any(v in title_l for v in ("hiring", "job", "opening", "vacancy", "career")):
            return True
    if any(x in url_l for x in ("/wiki/", "/dictionary/", "/blog/", "/articles/", "/article/", "/topic/", "/solutions/", "/products/", "/free-trial", "/overview")):
        if not any(x in url_l for x in ("career", "careers", "jobs", "job-listings", "viewjob")):
            return True
    if any(x in desc_l for x in ("definition:", "learn more", "what is", "a practical guide", "explained")) and not any(v in desc_l for v in ("apply", "hiring", "send resume", "send your cv", "job description")):
        return True
    return False


def has_vacancy_evidence(title='', description='', url='', query=''):
    text = f"{clean(title)} {clean(description)} {clean(url)} {clean(query)}".lower()
    # For job-board detail URLs the URL pattern itself is strong evidence, but still require role relevance elsewhere.
    if is_public_job_board_url(url) and is_specific_job_board_job_url(url):
        return True
    score = sum(1 for t in VACANCY_TERMS if t in text)
    if "career" in (url or '').lower() or "jobs" in (url or '').lower() or "openings" in (url or '').lower():
        score += 1
    return score >= 2


def clean(value):
    return core.clean_text(value or "")


def norm_url(url):
    return core.canonical_url(url or "")


def host(url):
    return core.domain(url or "")


def domain_matches(url, domains):
    d = host(url)
    return any(d == x or d.endswith("." + x) for x in domains)


def is_linkedin_url(url):
    return domain_matches(url, LINKEDIN_DOMAINS)


def is_public_job_board_url(url):
    return domain_matches(url, PUBLIC_JOB_BOARD_DOMAINS)


def now_ist_date():
    return core.now_ist().date()


def ddmmyyyy(d):
    if isinstance(d, date):
        return d.strftime("%d-%m-%Y")
    parsed = core.parse_date(d)
    return parsed.strftime("%d-%m-%Y") if parsed else ""


def is_signal_only_url(url):
    d = host(url)
    return any(d == x or d.endswith("." + x) for x in SIGNAL_ONLY_DOMAINS)


def is_blocked_final_url(url, cfg=None):
    d = host(url)
    if not d:
        return True
    # LinkedIn is never a final apply URL.
    if is_linkedin_url(url):
        return True
    # Content/directories are always blocked.
    for x in CONTENT_OR_DIRECTORY_DOMAINS:
        if d == x or d.endswith("." + x):
            return True
    # Quantity mode: public job boards are allowed only as final URLs when they
    # are specific job detail pages, not search/listing/category pages.
    if is_public_job_board_url(url):
        return not bool(cfg and cfg.get("allow_public_job_board_apply_url", True)) or not is_specific_job_board_job_url(url)
    if cfg and core.is_blocked_domain(url, cfg):
        return True
    if any(x in url.lower() for x in ("/login", "/signin", "/sign-in", "account/login")):
        return True
    return False


def hard_reject_reason(title="", description="", url="", employer=""):
    title_l = clean(title).lower()
    desc_l = clean(description).lower()
    url_l = clean(url).lower()
    employer_l = clean(employer).lower()

    if looks_like_reference_or_content_page(title, description, url):
        return "Reference/content/product page, not a job vacancy"

    if any(title_l.startswith(prefix) for prefix in SERVICE_PAGE_PREFIXES):
        return "Service/location landing page, not a job vacancy"
    if title_l in GENERIC_BUCKET_TITLES:
        return "Generic career/search bucket page, not a specific vacancy"
    if "australia & new zealand" in title_l or "anz---early-careers" in url_l:
        return "Region/career bucket page, not an India job vacancy"

    service_hits = sum(1 for x in SERVICE_TEXT_MARKERS if x in title_l or x in desc_l or x in url_l)
    if service_hits >= 2:
        return "Marketing/service landing page, not a vacancy"
    if "homelane" in employer_l or "homelane.com" in url_l:
        if service_hits >= 1 or "/interior-designers" in url_l or "/modular-kitchen" in url_l or "/wardrobe" in url_l:
            return "HomeLane service page, not a vacancy"

    text = f"{title_l} {desc_l}"
    if any(x in text for x in SOFTWARE_EXCLUDES):
        return "Software/IT architect role, not built-environment"
    return ""


def is_specific_job_board_job_url(url):
    u = (url or "").lower()
    d = host(u)
    path = urlparse(u).path.lower()
    if "naukri.com" in d:
        return "job-listings" in path or "/job-listings-" in path
    if "indeed.com" in d:
        return "/viewjob" in path or "jk=" in u
    if "foundit.in" in d:
        return "/job/" in path or "/jobs/" in path
    if "glassdoor" in d:
        return "job-listing" in path or "/job/" in path
    if "shine.com" in d:
        return "/jobs/" in path and "search" not in path
    if "timesjobs.com" in d:
        return "job-detail" in path or "jobdetailview" in u
    if "internshala.com" in d:
        return "/job/detail/" in path
    return False


def is_generic_job_search_page(title="", url="", snippet=""):
    text = f"{clean(title)} {clean(snippet)}".lower()
    u = (url or "").lower()
    generic_markers = (
        "jobs in ", "job vacancies", "latest jobs", "job openings in",
        "search results", "all jobs", "career opportunities in",
        "vacancies in ", "hiring now", "job listings",
    )
    if any(x in text for x in generic_markers):
        # Direct job detail URLs can still pass; search/list pages cannot.
        if not is_specific_job_board_job_url(url):
            return True
    if any(x in u for x in ("/jobs-in-", "/job-search", "?k=", "?q=", "/jobs?q", "/search")):
        return True
    return False


def role_relevant(title, description=""):
    text = f"{clean(title)} {clean(description)}".lower()
    if any(x in text for x in SOFTWARE_EXCLUDES):
        return False
    return any(x in text for x in ROLE_KEYWORDS)


def has_india_location(text):
    text_l = clean(text).lower()
    return any(x in text_l for x in core.load_config().get("india_markers", [])) or "india" in text_l


def public_apply_ok(record, cfg=None):
    apply_type = clean(record.get("apply_type")).lower()
    apply_url = norm_url(record.get("apply_url"))
    apply_email = clean(record.get("apply_email") or record.get("employer_email"))

    if apply_email and "@" in apply_email:
        return True
    if apply_type == "email" and apply_email:
        return True
    if apply_url and not is_blocked_final_url(apply_url, cfg):
        return True
    return False


def web_key(record):
    parts = [
        clean(record.get("title")).lower(),
        clean(record.get("employer_name")).lower(),
        clean(record.get("location")).lower(),
        norm_url(record.get("apply_url")).lower(),
        clean(record.get("apply_email")).lower(),
    ]
    raw = "|".join(parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def website_record_valid(record, cfg):
    if clean(record.get("status")).lower() != "publish":
        return False, "Not publish status"
    if clean(record.get("filled")).lower() == "yes":
        return False, "Filled job"
    title = clean(record.get("title"))
    description = clean(record.get("description"))
    employer = clean(record.get("employer_name"))
    url = clean(record.get("apply_url"))
    reason = hard_reject_reason(title, description, url, employer)
    if reason:
        return False, reason
    if not role_relevant(title, description):
        return False, "Role not relevant"
    # V6.2: quantity mode must still look like a real vacancy, not only contain architecture words.
    # Keep email/official career-page rows that clearly list responsibilities/experience/qualification.
    if not has_vacancy_evidence(title, description, url, ""):
        return False, "No clear hiring/apply/vacancy evidence"
    location_text = " ".join([
        clean(record.get("address")), clean(record.get("location")), description, employer
    ])
    if not has_india_location(location_text):
        return False, "Not India-based"
    expiry = core.parse_date(record.get("expiry_date"))
    if expiry and expiry < now_ist_date():
        return False, "Expired"
    if not public_apply_ok(record, cfg):
        return False, "No public official apply method"
    if url and is_linkedin_url(url):
        return False, "Final apply URL is LinkedIn"
    if url and is_signal_only_url(url) and not is_public_job_board_url(url):
        return False, "Final apply URL is signal-only job board"
    if url and is_public_job_board_url(url) and not is_specific_job_board_job_url(url):
        return False, "Job-board URL is not a specific job detail page"
    return True, "OK"


def normalize_existing_row(record, cfg):
    # Ensure every output row has exactly the web schema and external_id stays blank.
    out = {h: clean(record.get(h, "")) for h in WEBSITE_HEADERS}
    out["external_id"] = ""
    if not out.get("expiry_date"):
        out["expiry_date"] = ddmmyyyy(now_ist_date() + timedelta(days=int(cfg.get("website_rolling_expiry_days", 7))))
    return out


def website_record_from_job(job, cfg):
    # Reuse the validated V4 mapper, but do not write meta/source tabs.
    internal = core.job_to_record(job)
    web = core.website_record_from_meta(internal, cfg)
    web["external_id"] = ""

    # Safety: never publish LinkedIn. Public job boards are allowed in V6.1
    # Quantity Mode only if the URL is a specific public job detail page.
    if is_linkedin_url(web.get("apply_url")):
        return None, "LinkedIn final URL blocked"
    if is_public_job_board_url(web.get("apply_url")) and not is_specific_job_board_job_url(web.get("apply_url")):
        return None, "Generic job-board URL blocked"
    valid, reason = website_record_valid(web, cfg)
    if not valid:
        return None, reason
    return web, "OK"


def read_sheet1(service, sheet_id, tab):
    values = core.read_sheet_values(service, sheet_id, tab)
    if not values:
        return []
    headers = values[0]
    return [core.row_to_record(headers, row) for row in values[1:]]


def ensure_sheet1(service, sheet_id, tab):
    core.get_sheet_properties(service, sheet_id, tab, create_if_missing=True)
    core.ensure_sheet_columns(service, sheet_id, tab, len(WEBSITE_HEADERS))


def write_sheet1_only(service, sheet_id, tab, records):
    ensure_sheet1(service, sheet_id, tab)
    end_col = core.column_letter(len(WEBSITE_HEADERS))
    service.spreadsheets().values().clear(
        spreadsheetId=sheet_id,
        range=f"'{tab}'!A:ZZ",
        body={},
    ).execute()
    values = [WEBSITE_HEADERS]
    for rec in records:
        rec = {h: rec.get(h, "") for h in WEBSITE_HEADERS}
        rec["external_id"] = ""
        values.append([rec.get(h, "") for h in WEBSITE_HEADERS])
    service.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range=f"'{tab}'!A1:{end_col}{len(values)}",
        valueInputOption="RAW",
        body={"values": values},
    ).execute()


def delete_old_pipeline_tabs(service, sheet_id, cfg):
    if not bool(cfg.get("delete_old_pipeline_tabs", False)):
        return 0
    targets = set(cfg.get("old_pipeline_tabs_to_delete", []))
    if not targets:
        targets = {"CandidateJobs", "RejectedJobs", "AutomationOutput", "Sources", "_CollectorMeta"}
    try:
        metadata = service.spreadsheets().get(
            spreadsheetId=sheet_id,
            fields="sheets(properties(sheetId,title))",
        ).execute()
        requests = []
        for sheet in metadata.get("sheets", []):
            props = sheet.get("properties", {})
            title = props.get("title")
            sid = props.get("sheetId")
            if title in targets and title != ONE_SHEET_TAB:
                requests.append({"deleteSheet": {"sheetId": sid}})
        if requests:
            service.spreadsheets().batchUpdate(
                spreadsheetId=sheet_id,
                body={"requests": requests},
            ).execute()
        return len(requests)
    except Exception as e:
        print(f"V6 WARNING | could not delete old extra tabs safely: {e}")
        return 0


def fetch_url(url, cfg):
    try:
        r = requests.get(
            url,
            timeout=int(cfg.get("request_timeout_seconds", 10)),
            headers={"User-Agent": core.USER_AGENT},
            allow_redirects=True,
        )
        if r.status_code >= 400:
            return None
        return r
    except Exception:
        return None


def bing_rss(query, cfg):
    url = "https://www.bing.com/search?q=" + quote(query) + "&format=rss"
    r = fetch_url(url, cfg)
    if not r:
        return []
    out = []
    try:
        root = ET.fromstring(r.text)
    except Exception:
        return []
    for item in root.findall(".//item"):
        title = clean(item.findtext("title"))
        link = clean(item.findtext("link"))
        desc = clean(item.findtext("description"))
        if link:
            out.append({"title": title, "url": norm_url(link), "snippet": desc, "query": query})
        if len(out) >= int(cfg.get("v6_results_per_query", 8)):
            break
    return out


def career_like_url(url):
    u = (url or "").lower()
    return any(x in u for x in ("career", "careers", "jobs", "job", "openings", "hiring", "join-us", "joinus", "vacancy"))


def extract_company_from_signal(title, snippet=""):
    text = clean(title)
    # Common patterns: "Junior Architect - ABC - LinkedIn", "ABC hiring Junior Architect"
    parts = [p.strip() for p in re.split(r"\s[-|–—]\s", text) if p.strip()]
    bad = {"linkedin", "jobs", "job", "naukri", "indeed", "glassdoor"}
    for p in reversed(parts):
        p_clean = re.sub(r"\b(India|LinkedIn|Naukri|Indeed|Jobs?)\b", "", p, flags=re.I).strip()
        if p_clean and p_clean.lower() not in bad and len(p_clean) >= 3:
            return p_clean[:80]
    m = re.search(r"at\s+([A-Z][A-Za-z0-9&.,' ]{2,80})", text)
    if m:
        return clean(m.group(1))
    return ""


def official_sources_from_signal(result, cfg):
    company = extract_company_from_signal(result.get("title"), result.get("snippet"))
    if not company:
        return []
    queries = [
        f'"{company}" careers India architect',
        f'"{company}" "current openings" India architect',
        f'"{company}" "join our team" India architecture',
    ]
    urls = []
    seen = set()
    for q in queries[: int(cfg.get("v6_signal_resolve_queries", 2))]:
        for hit in bing_rss(q, cfg):
            u = norm_url(hit.get("url"))
            if not u or u in seen:
                continue
            if is_blocked_final_url(u, cfg):
                continue
            if not career_like_url(u):
                continue
            seen.add(u)
            urls.append(u)
            if len(urls) >= int(cfg.get("v6_official_sources_per_signal", 2)):
                return urls
    return urls


def city_from_text(text):
    t = clean(text).lower()
    cities = [
        "Mumbai", "Navi Mumbai", "Thane", "Pune", "New Delhi", "Delhi", "Gurugram", "Gurgaon", "Noida",
        "Bengaluru", "Bangalore", "Hyderabad", "Chennai", "Ahmedabad", "Kolkata", "Jaipur", "Kochi",
        "Goa", "Surat", "Coimbatore", "Lucknow", "Indore", "Nagpur", "Vadodara", "Chandigarh",
        "Bhubaneswar", "Dehradun", "Guwahati", "Mysuru", "Mangalore", "Rajkot",
    ]
    for city in cities:
        if city.lower() in t:
            return city
    return "India"


def strip_job_board_suffix(title):
    title = clean(html.unescape(title or ""))
    title = re.sub(r"\s*[-|–—]\s*(Naukri\.com|Indeed|LinkedIn|Glassdoor|Foundit|Shine|TimesJobs|Internshala).*$", "", title, flags=re.I)
    title = re.sub(r"\s*\|\s*(Naukri\.com|Indeed|LinkedIn|Glassdoor|Foundit|Shine|TimesJobs|Internshala).*$", "", title, flags=re.I)
    title = re.sub(r"\s+Jobs?\s+(in|at)\s+.*$", "", title, flags=re.I)
    return clean(title)


def parse_signal_job_title_company(title, snippet="", url=""):
    raw = clean(html.unescape(title or ""))
    raw = re.sub(r"\s*\|\s*.*$", "", raw)
    raw = re.sub(r"\s*[-–—]\s*(Naukri\.com|Indeed|LinkedIn|Glassdoor|Foundit|Shine|TimesJobs|Internshala).*$", "", raw, flags=re.I)
    parts = [clean(p) for p in re.split(r"\s[-–—]\s", raw) if clean(p)]
    company = ""
    job_title = strip_job_board_suffix(raw)
    if len(parts) >= 2:
        job_title = strip_job_board_suffix(parts[0])
        # choose a later part that looks like company, not location/experience
        for p in parts[1:3]:
            if not re.search(r"\b(year|yrs?|experience|mumbai|delhi|india|pune|bangalore|bengaluru|hyderabad|chennai)\b", p, re.I):
                company = re.sub(r"\b(Hiring|Recruitment|Jobs?)\b", "", p, flags=re.I).strip()
                break
    m = re.search(r"\bat\s+([A-Z][A-Za-z0-9&.,'() /-]{2,80})", raw)
    if m and not company:
        company = clean(m.group(1))
    if not company:
        company = extract_company_from_signal(raw, snippet)
    if not company:
        d = host(url)
        company = d.replace("www.", "").split(".")[0].title() if d else "Hiring Company"
    # Keep titles simple; reject huge titles later.
    job_title = re.sub(r"\bHiring For\b", "", job_title, flags=re.I).strip(" -|–—")
    return clean(job_title[:90]), clean(company[:90])


def infer_category(title, desc=""):
    text = f"{title} {desc}".lower()
    if any(x in text for x in ("bim", "revit")):
        return "BIM"
    if any(x in text for x in ("visualizer", "visualiser", "3d render", "3d artist", "3ds max", "lumion", "v-ray")):
        return "Visualization"
    if "interior" in text:
        return "Interior Design"
    if "landscape" in text:
        return "Landscape Architecture"
    if "urban" in text:
        return "Urban Design"
    if "draft" in text or "autocad" in text:
        return "Drafting"
    return "Architecture"


def infer_career_level(title, desc=""):
    text = f"{title} {desc}".lower()
    if any(x in text for x in ("senior", "lead", "manager", "head")):
        return "Senior Level"
    if any(x in text for x in ("junior", "fresher", "entry", "0-", "0 to", "1 year")):
        return "Entry Level"
    return "Mid Level"


def extract_experience(text):
    t = clean(text)
    m = re.search(r"(\d+\s*(?:-|to|–)\s*\d+\s*(?:years?|yrs?))", t, re.I)
    if m:
        return clean(m.group(1))
    m = re.search(r"(minimum\s+\d+\s*(?:years?|yrs?))", t, re.I)
    if m:
        return clean(m.group(1))
    m = re.search(r"(\d+\+?\s*(?:years?|yrs?))", t, re.I)
    if m:
        return clean(m.group(1))
    return ""


def quantity_signal_to_record(hit, cfg):
    url = norm_url(hit.get("url"))
    title = clean(hit.get("title"))
    snippet = clean(BeautifulSoup(hit.get("snippet") or "", "html.parser").get_text(" "))
    query = clean(hit.get("query"))
    if not url or is_linkedin_url(url):
        return None, "LinkedIn URL blocked"
    if looks_like_reference_or_content_page(title, snippet, url):
        return None, "Reference/content/product page, not a job vacancy"
    if is_public_job_board_url(url) and not is_specific_job_board_job_url(url):
        return None, "Not a specific job-board detail URL"
    if not is_public_job_board_url(url):
        # Public web results are allowed only when they are career/job/opening URLs with vacancy evidence.
        if not career_like_url(url):
            return None, "Public web result is not a career/job URL"
        if not has_vacancy_evidence(title, snippet, url, query):
            return None, "No clear hiring/apply/vacancy evidence"
    if is_generic_job_search_page(title, url, snippet):
        return None, "Generic job search page"
    job_title, employer = parse_signal_job_title_company(title, snippet, url)
    if len(job_title) < 4 or len(job_title) > 95:
        return None, "Bad title length"
    if hard_reject_reason(job_title, snippet, url, employer):
        return None, hard_reject_reason(job_title, snippet, url, employer)
    if not role_relevant(job_title, snippet):
        return None, "Role not relevant"
    text_for_loc = " ".join([title, snippet, query, url])
    if not has_india_location(text_for_loc):
        return None, "Not India-based"
    city = city_from_text(text_for_loc)
    location = f"{city}|India" if city != "India" else "India"
    address = f"{city}, India" if city != "India" else "India"
    if is_blocked_final_url(url, cfg):
        return None, "Final URL blocked"
    desc = snippet or f"{job_title} opening at {employer}. Public job source detected by V6.2 Clean Quantity Mode."
    exp = extract_experience(f"{title} {snippet}")
    tag_terms = []
    for term in cfg.get("skill_terms", []):
        if term.lower() in f"{title} {snippet}".lower():
            tag_terms.append(term)
    rec = {h: "" for h in WEBSITE_HEADERS}
    rec.update({
        "external_id": "",
        "title": job_title,
        "description": desc[: int(cfg.get("max_description_chars", 1800))],
        "status": "publish",
        "employer_author": employer,
        "employer_email": "",
        "employer_name": employer,
        "expiry_date": ddmmyyyy(now_ist_date() + timedelta(days=int(cfg.get("website_rolling_expiry_days", 7)))),
        "application_deadline_date": "",
        "featured": "no",
        "urgent": "no",
        "filled": "no",
        "apply_type": "external",
        "apply_url": url,
        "apply_email": "",
        "phone": "",
        "address": address,
        "location": location,
        "category": infer_category(job_title, snippet),
        "tag": "|".join(tag_terms[:8]),
        "experience": exp,
        "industry": infer_category(job_title, snippet),
        "qualification": "",
        "career_level": infer_career_level(job_title, snippet),
        "logo_url": "",
    })
    ok, reason = website_record_valid(rec, cfg)
    if not ok:
        return None, reason
    return rec, "OK"


def collect_quantity_records(cfg):
    if not bool(cfg.get("v6_quantity_mode_enabled", True)):
        return []
    records = []
    seen_urls = set()
    queries = cfg.get("v6_quantity_search_queries") or cfg.get("v6_search_queries", [])
    max_queries = int(cfg.get("v6_quantity_max_queries_per_run", 80))
    target = int(cfg.get("v6_quantity_target_records_per_run", 75))
    print("=" * 80)
    print(f"V6.2 CLEAN QUANTITY MODE | queries={min(len(queries), max_queries)} target={target}")
    print("=" * 80)
    for query in queries[:max_queries]:
        print(f"V6.2 JOB SEARCH | {query}")
        for hit in bing_rss(query, cfg):
            u = norm_url(hit.get("url"))
            if not u or u in seen_urls:
                continue
            seen_urls.add(u)
            rec, reason = quantity_signal_to_record(hit, cfg)
            if rec:
                records.append(rec)
                print(f"V6.2 ACCEPT SIGNAL JOB | {rec.get('employer_name')} | {rec.get('title')} | {u}")
            else:
                print(f"V6.2 REJECT SIGNAL | {hit.get('title')} | {reason}")
            if len(records) >= target:
                return records
        time.sleep(float(cfg.get("v6_search_delay_seconds", 0.25)))
    return records


def discover_run_sources(cfg):
    sources = []
    seen = set()

    for u in cfg.get("career_pages", []):
        u = norm_url(u)
        if u and u not in seen and not is_blocked_final_url(u, cfg):
            seen.add(u)
            sources.append(u)

    queries = cfg.get("v6_search_queries", [])
    max_queries = int(cfg.get("v6_max_queries_per_run", 18))
    max_sources = int(cfg.get("v6_max_discovered_sources_per_run", 25))

    for query in queries[:max_queries]:
        print(f"V6 SEARCH | {query}")
        for hit in bing_rss(query, cfg):
            u = norm_url(hit.get("url"))
            if not u:
                continue

            if is_linkedin_url(u):
                # LinkedIn stays discovery signal only. Resolve to official sources.
                for official in official_sources_from_signal(hit, cfg):
                    if official not in seen and not is_blocked_final_url(official, cfg):
                        seen.add(official)
                        sources.append(official)
                        print(f"V6 SIGNAL RESOLVED | {hit.get('title')} -> {official}")
                continue
            if is_public_job_board_url(u):
                # Public job-board detail records are handled by Quantity Mode, not scanned as employer sources.
                continue

            if is_blocked_final_url(u, cfg):
                continue
            if career_like_url(u):
                if u not in seen:
                    seen.add(u)
                    sources.append(u)
                    print(f"V6 SOURCE | {u}")

            if len(sources) >= len(cfg.get("career_pages", [])) + max_sources:
                break
        if len(sources) >= len(cfg.get("career_pages", [])) + max_sources:
            break
        time.sleep(float(cfg.get("v6_search_delay_seconds", 0.4)))

    return sources


def jobs_from_sources(sources, cfg):
    cfg2 = dict(cfg)
    cfg2["career_pages"] = sources
    cfg2["max_sources_per_run"] = int(cfg.get("v6_max_sources_to_scan", len(sources)))
    cfg2["source_workers"] = int(cfg.get("source_workers", 4))
    cfg2["max_job_links_per_source"] = int(cfg.get("max_job_links_per_source", 8))
    cfg2["max_pages_per_source"] = int(cfg.get("max_pages_per_source", 8))
    jobs, reports = core.scan_all_sources(cfg2)
    return jobs, reports


def build_single_sheet_records(existing_records, jobs, cfg, extra_records=None):
    accepted = []
    rejected_count = 0
    existing_kept = 0
    new_added = 0
    quantity_added = 0
    by_key = {}

    for rec in existing_records:
        web = normalize_existing_row(rec, cfg)
        valid, reason = website_record_valid(web, cfg)
        if not valid:
            rejected_count += 1
            print(f"V6 CLEAN EXISTING | removed | {web.get('employer_name')} | {web.get('title')} | {reason}")
            continue
        key = web_key(web)
        by_key[key] = web
        existing_kept += 1

    for job in jobs:
        web, reason = website_record_from_job(job, cfg)
        if not web:
            rejected_count += 1
            print(f"V6 REJECT JOB | {job.get('company')} | {job.get('title')} | {reason}")
            continue
        key = web_key(web)
        if key not in by_key:
            new_added += 1
        by_key[key] = web

    for web in (extra_records or []):
        web = normalize_existing_row(web, cfg)
        valid, reason = website_record_valid(web, cfg)
        if not valid:
            rejected_count += 1
            print(f"V6.2 REJECT EXTRA | {web.get('employer_name')} | {web.get('title')} | {reason}")
            continue
        key = web_key(web)
        if key not in by_key:
            quantity_added += 1
        by_key[key] = web

    accepted = list(by_key.values())
    accepted.sort(key=lambda r: (clean(r.get("employer_name")).lower(), clean(r.get("title")).lower()))
    return accepted, {"existing_kept": existing_kept, "new_added": new_added, "quantity_added": quantity_added, "rejected_count": rejected_count}


def load_config():
    with open("config.yaml", "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    cfg.setdefault("minimum_posted_date", "2026-09-01")
    cfg.setdefault("website_rolling_expiry_days", 7)
    cfg.setdefault("request_timeout_seconds", 10)
    cfg.setdefault("source_workers", 4)
    cfg.setdefault("max_job_links_per_source", 8)
    cfg.setdefault("max_pages_per_source", 8)
    return cfg


def run(dry_run=False):
    cfg = load_config()
    sources = discover_run_sources(cfg)
    print("=" * 80)
    print(f"V6 sources to scan this run: {len(sources)}")
    print("=" * 80)

    jobs, reports = jobs_from_sources(sources, cfg)
    print("=" * 80)
    print(f"V6 parsed/validated official jobs before Sheet1 merge: {len(jobs)}")
    print("=" * 80)

    quantity_records = collect_quantity_records(cfg)
    print("=" * 80)
    print(f"V6.2 clean quantity-mode public-source records before Sheet1 merge: {len(quantity_records)}")
    print("=" * 80)

    if dry_run:
        for j in jobs[:20]:
            print(json.dumps({"title": j.get("title"), "company": j.get("company"), "apply_url": j.get("apply_url")}, ensure_ascii=False))
        for r in quantity_records[:20]:
            print(json.dumps({"title": r.get("title"), "company": r.get("employer_name"), "apply_url": r.get("apply_url")}, ensure_ascii=False))
        return

    sheet_id = os.environ.get("GOOGLE_SHEET_ID", "")
    if not sheet_id:
        raise RuntimeError("Missing GOOGLE_SHEET_ID")
    tab = os.environ.get("GOOGLE_SHEET_TAB", ONE_SHEET_TAB) or ONE_SHEET_TAB
    if tab != ONE_SHEET_TAB:
        print(f"V6 NOTICE | forcing single output tab from {tab!r} to {ONE_SHEET_TAB!r}")
        tab = ONE_SHEET_TAB

    service = core.sheet_service()
    ensure_sheet1(service, sheet_id, tab)
    existing = read_sheet1(service, sheet_id, tab)
    records, stats = build_single_sheet_records(existing, jobs, cfg, extra_records=quantity_records)
    write_sheet1_only(service, sheet_id, tab, records)
    deleted = delete_old_pipeline_tabs(service, sheet_id, cfg)

    print("=" * 80)
    print("V6.2 SINGLE SHEET CLEAN QUANTITY COMPLETE")
    print(f"Sheet tab: {tab}")
    print(f"Existing kept: {stats['existing_kept']}")
    print(f"New/updated official accepted jobs: {stats['new_added']}")
    print(f"New/updated public-source quantity jobs: {stats.get('quantity_added', 0)}")
    print(f"Rejected/cleaned rows this run: {stats['rejected_count']}")
    print(f"Final Sheet1 website-ready jobs: {len(records)}")
    print(f"Old extra tabs deleted: {deleted}")
    print("=" * 80)


def self_test():
    cfg = {
        "website_rolling_expiry_days": 7,
        "india_markers": ["india", "mumbai", "delhi"],
    }
    bad = {
        "external_id": "ABC",
        "title": "Interior Designers in Ahmedabad",
        "description": "Get Free Estimate Design Gallery Store Locator 45-day delivery",
        "status": "publish",
        "filled": "no",
        "employer_name": "HomeLane",
        "apply_type": "external",
        "apply_url": "https://www.homelane.com/interior-designers/ahmedabad",
        "apply_email": "",
        "address": "Ahmedabad, India",
        "location": "Ahmedabad|India",
    }
    ok, reason = website_record_valid(bad, cfg)
    assert not ok and "service" in reason.lower()

    linkedin = dict(bad)
    linkedin.update({
        "title": "Junior Architect",
        "description": "We are hiring a Junior Architect in Mumbai India. Send resume.",
        "employer_name": "ABC Architects",
        "apply_url": "https://www.linkedin.com/jobs/view/123",
    })
    ok, reason = website_record_valid(linkedin, cfg)
    assert not ok

    content = dict(bad)
    content.update({
        "title": "ARCHITECTURAL Definition & Meaning",
        "description": "ARCHITECTURAL definition: of or relating to architecture. Learn more.",
        "employer_name": "Dictionary",
        "apply_url": "https://www.dictionary.com/browse/architectural",
        "address": "Mumbai, India",
        "location": "Mumbai|India",
    })
    ok, reason = website_record_valid(content, cfg)
    assert not ok and ("content" in reason.lower() or "reference" in reason.lower()), reason

    public_board = dict(linkedin)
    public_board.update({
        "apply_url": "https://www.naukri.com/job-listings-junior-architect-abc-architects-mumbai-1-to-3-years-123",
        "description": "Junior Architect opening in Mumbai India. Apply now. Experience 1 to 3 years.",
        "apply_type": "external",
        "filled": "no",
    })
    ok, reason = website_record_valid(public_board, {**cfg, "allow_public_job_board_apply_url": True})
    assert ok, reason

    good = dict(bad)
    good.update({
        "external_id": "SHOULD_CLEAR",
        "title": "Junior Architect",
        "description": "We are hiring a Junior Architect in Mumbai India. Experience 1-3 years. Apply now.",
        "employer_name": "ABC Architects",
        "employer_email": "careers@example.com",
        "apply_type": "email",
        "apply_url": "",
        "apply_email": "careers@example.com",
        "address": "Mumbai, Maharashtra, India",
        "location": "Mumbai|India",
        "category": "Architecture",
        "expiry_date": ddmmyyyy(now_ist_date() + timedelta(days=7)),
    })
    ok, reason = website_record_valid(good, cfg)
    assert ok, reason
    normalized = normalize_existing_row(good, cfg)
    assert normalized["external_id"] == ""

    rows, stats = build_single_sheet_records([bad, good, good], [], cfg)
    assert len(rows) == 1
    assert rows[0]["external_id"] == ""
    assert stats["rejected_count"] == 1

    print("V6.2 SELF TEST PASSED: single Sheet1 output, fake-row cleanup, LinkedIn blocked, public job-board detail URLs allowed for quantity mode, dedupe and blank external_id are working.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
