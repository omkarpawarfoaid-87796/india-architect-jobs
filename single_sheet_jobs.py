"""
Single Sheet Job Finder V8.2

Goal:
- One Google Sheet output only: Sheet1
- No CandidateJobs, RejectedJobs, Sources, AutomationOutput, or _CollectorMeta required
- Search wider and publish real job-detail apply links from many sources
- LinkedIn/job boards are allowed only when they are specific job detail pages, never search/login pages

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

VERSION = "V8.2-FINAL-WEBSITE-QUALITY"
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
    # V7: block educational/content/product/reference pages that V7 wrongly accepted.
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

FINAL_BLOCKED_DOMAINS = CONTENT_OR_DIRECTORY_DOMAINS

ROLE_KEYWORDS = (
    "architect", "architecture", "architectural", "interior designer",
    "interior architect", "urban designer", "urban planner", "landscape architect",
    "bim", "revit", "visualizer", "visualiser", "3d render", "3d artist",
    "draftsman", "draughtsman", "draftsperson", "draftsmen", "draftsmem", "autocad", "site architect", "facade architect", "design manager",
)

SOFTWARE_EXCLUDES = (
    "software architect", "solution architect", "solutions architect", "cloud architect",
    "enterprise architect", "data architect", "security architect", "network architect",
    "technical architect", "platform architect", "aws architect", "azure architect",
    "java architect", ".net architect", "salesforce architect",
    "application architect", "systems architect", "infrastructure architect",
    "software development", "engineer software", "software engineer", "software developer",
    "technology systems", "information technology", "it architect", "devops",
    "backend developer", "frontend developer", "full stack", "programmer",
)

# V8.2: keep quantity, but remove IT/software roles that only contain words
# like "architecture" in the description. These must not enter an architecture
# jobs website.
BAD_ROLE_TITLE_TERMS = (
    "software development", "engineer software", "software engineer", "software developer",
    "developer", "programmer", "cloud", "data engineer", "data architect",
    "network", "security", "devops", "technology systems", "technical consultant",
    "backend", "frontend", "full stack", "information technology",
)

GOOD_ROLE_TITLE_TERMS = (
    "architect", "architectural", "architecture", "interior designer", "interior architect",
    "designer", "bim", "revit", "visualizer", "visualiser", "3d",
    "render", "draft", "draught", "autocad", "facade", "landscape",
    "urban", "parametric designer", "cad",
)

GENERIC_TITLES_ALLOWED_WITH_DESC = {
    "senior associate", "associate", "design associate", "graduate engineer trainee architecture",
    "draftsmen", "draftsman", "draughtsman",
}

MAX_REAL_EXPERIENCE_YEARS = 35

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
    # For job detail URLs the URL pattern itself is strong evidence, but still require role relevance elsewhere.
    if is_linkedin_url(url) and is_specific_linkedin_job_url(url):
        return True
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
    # V7: LinkedIn can be used as a final apply link only if it is a specific job detail URL.
    # Never allow LinkedIn search/company/login pages.
    if is_linkedin_url(url):
        return not bool(cfg and cfg.get("allow_linkedin_job_apply_url", True)) or not is_specific_linkedin_job_url(url)
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



def contains_bad_software_role(title="", description=""):
    """Reject software/IT jobs even when the snippet mentions design architecture."""
    title_l = clean(title).lower()
    desc_l = clean(description).lower()
    all_l = f"{title_l} {desc_l}"
    if any(x in title_l for x in BAD_ROLE_TITLE_TERMS):
        return True
    if any(x in all_l for x in SOFTWARE_EXCLUDES):
        return True
    # Generic engineer/developer titles are not built-environment unless the title itself
    # clearly says architect/interior/BIM/CAD/drafting/landscape/urban/facade.
    if re.search(r"\b(engineer|developer|programmer)\b", title_l):
        if not any(x in title_l for x in ("bim", "revit", "architect", "architectural", "architecture", "interior", "draft", "draught", "facade", "landscape", "urban", "cad", "autocad")):
            return True
    return False


def title_has_built_environment_role(title="", description=""):
    title_l = clean(title).lower()
    desc_l = clean(description).lower()
    if contains_bad_software_role(title, description):
        return False
    if any(x in title_l for x in GOOD_ROLE_TITLE_TERMS):
        return True
    # Allow a small set of generic job-board titles only when the snippet itself
    # has strong built-environment evidence.
    if title_l in GENERIC_TITLES_ALLOWED_WITH_DESC:
        return any(x in desc_l for x in ("architect", "architecture", "interior", "bim", "revit", "draft", "draught", "autocad"))
    return False


def extract_title_from_snippet(snippet=""):
    """Recover a proper role when LinkedIn/search title is generic (e.g. Senior Associate)."""
    text = clean(snippet)
    patterns = [
        r"Job\s*Title\s*[:\-–—]\s*([^\.\n\r]{4,90})",
        r"Designation\s*[:\-–—]\s*([^\.\n\r]{4,90})",
        r"Position\s*[:\-–—]\s*([^\.\n\r]{4,90})",
        r"Role\s*[:\-–—]\s*([^\.\n\r]{4,90})",
    ]
    for pat in patterns:
        m = re.search(pat, text, flags=re.I)
        if not m:
            continue
        candidate = clean(m.group(1))
        candidate = re.split(r"(?:Location|Experience|Candidate|Responsibilities|Requirements)\s*[:\-–—]", candidate, flags=re.I)[0]
        candidate = clean(candidate.strip(" -|,–—"))
        # If the source writes "Senior Associate - Draughtsman", keep the design part too.
        if title_has_built_environment_role(candidate, snippet):
            return candidate
    return ""


def years_in_text(value=""):
    return [int(x) for x in re.findall(r"\b(\d{1,2})\s*\+?\s*(?:years?|yrs?)\b", clean(value), flags=re.I)]

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

    if contains_bad_software_role(title, description):
        return "Software/IT/software-development role, not built-environment"
    return ""


def is_specific_linkedin_job_url(url):
    u = (url or "").lower()
    d = host(u)
    path = urlparse(u).path.lower()
    if not (d == "linkedin.com" or d.endswith(".linkedin.com")):
        return False
    # Accept only specific LinkedIn job pages like /jobs/view/1234567890.
    # Reject /jobs/search, /company, /login, /feed, etc.
    return "/jobs/view/" in path and not any(x in u for x in ("/jobs/search", "/login", "/signin", "trk=public_jobs_jobs-search"))


def is_specific_public_job_detail_url(url):
    return (is_linkedin_url(url) and is_specific_linkedin_job_url(url)) or (is_public_job_board_url(url) and is_specific_job_board_job_url(url))

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
        if not is_specific_public_job_detail_url(url):
            return True
    if any(x in u for x in ("/jobs-in-", "/job-search", "?k=", "?q=", "/jobs?q", "/search")):
        return True
    return False


def role_relevant(title, description=""):
    return title_has_built_environment_role(title, description)


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
    return record_identity_key(record)


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
    # V7: quantity mode must still look like a real vacancy, not only contain architecture words.
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
    exp_years = years_in_text(record.get("experience"))
    if exp_years and max(exp_years) > MAX_REAL_EXPERIENCE_YEARS:
        return False, "Invalid experience value from company-history text"
    if not public_apply_ok(record, cfg):
        return False, "No public official apply method"
    if url and is_linkedin_url(url) and not (cfg.get("allow_linkedin_job_apply_url", True) and is_specific_linkedin_job_url(url)):
        return False, "LinkedIn URL is not a specific job detail page"
    if url and is_signal_only_url(url) and not (is_public_job_board_url(url) or is_linkedin_url(url)):
        return False, "Final apply URL is signal-only job board"
    if url and is_public_job_board_url(url) and not is_specific_job_board_job_url(url):
        return False, "Job-board URL is not a specific job detail page"
    return True, "OK"


def normalize_existing_row(record, cfg):
    # Ensure every output row has exactly the web schema and external_id stays blank.
    out = {h: clean(record.get(h, "")) for h in WEBSITE_HEADERS}
    out["external_id"] = ""
    out["apply_url"] = clean_apply_url(out.get("apply_url"))
    fixed_title, fixed_company = clean_title_company_pair(out.get("title"), out.get("employer_name"), out.get("description"), out.get("apply_url"))
    if fixed_title:
        out["title"] = fixed_title
    if fixed_company:
        out["employer_name"] = fixed_company
        out["employer_author"] = fixed_company
    if not out.get("expiry_date"):
        out["expiry_date"] = ddmmyyyy(now_ist_date() + timedelta(days=int(cfg.get("website_rolling_expiry_days", 7))))
    return out


def website_record_from_job(job, cfg):
    # Reuse the validated V4 mapper, but do not write meta/source tabs.
    internal = core.job_to_record(job)
    web = core.website_record_from_meta(internal, cfg)
    web["external_id"] = ""

    # V7: LinkedIn is allowed only if it is a specific public job detail page.
    if is_linkedin_url(web.get("apply_url")) and not (cfg.get("allow_linkedin_job_apply_url", True) and is_specific_linkedin_job_url(web.get("apply_url"))):
        return None, "LinkedIn URL is not a specific job detail page"
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


def decode_search_redirect(url):
    """Decode common search-engine redirect URLs into the final target URL."""
    u = clean(url)
    if not u:
        return ""
    parsed = urlparse(u)
    qs = parse_qs(parsed.query)
    for key in ("u", "url", "uddg", "target", "r"):
        vals = qs.get(key)
        if vals:
            cand = unquote(vals[0])
            if cand.startswith("http"):
                return norm_url(cand)
    return norm_url(u)


def bing_html(query, cfg):
    """Extra search channel. RSS can miss job-detail URLs, so V8 also parses Bing HTML."""
    url = "https://www.bing.com/search?q=" + quote(query) + "&count=" + str(int(cfg.get("v8_results_per_query", 20)))
    r = fetch_url(url, cfg)
    if not r:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    out = []
    for li in soup.select("li.b_algo"):
        a = li.find("a", href=True)
        if not a:
            continue
        target = decode_search_redirect(a.get("href"))
        title = clean(a.get_text(" "))
        snip_el = li.select_one(".b_caption p") or li.find("p")
        snippet = clean(snip_el.get_text(" ") if snip_el else "")
        if target:
            out.append({"title": title, "url": target, "snippet": snippet, "query": query})
        if len(out) >= int(cfg.get("v8_results_per_query", 20)):
            break
    return out


def duckduckgo_html(query, cfg):
    """Backup search channel for public job-board result links."""
    url = "https://html.duckduckgo.com/html/?q=" + quote(query)
    r = fetch_url(url, cfg)
    if not r:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    out = []
    for res in soup.select(".result"):
        a = res.select_one("a.result__a") or res.find("a", href=True)
        if not a:
            continue
        target = decode_search_redirect(a.get("href"))
        title = clean(a.get_text(" "))
        snip_el = res.select_one(".result__snippet")
        snippet = clean(snip_el.get_text(" ") if snip_el else "")
        if target:
            out.append({"title": title, "url": target, "snippet": snippet, "query": query})
        if len(out) >= int(cfg.get("v8_results_per_query", 20)):
            break
    return out


def extract_urls_from_text_blob(text_blob, base_url=""):
    urls = set()
    blob = html.unescape(text_blob or "")
    # normal href/src links
    for m in re.finditer(r"""(?:href|src)=[\"']([^\"']+)[\"']""", blob, flags=re.I):
        urls.add(urljoin(base_url, html.unescape(m.group(1))))
    # raw absolute URLs in JSON/scripts
    for m in re.finditer(r"""https?:\\?/\\?/[^\s\"'<>\\]+""", blob, flags=re.I):
        u = m.group(0).replace('\\/', '/')
        urls.add(html.unescape(u))
    return [decode_search_redirect(u) for u in urls]


def title_from_url_slug(url):
    """Build a readable fallback title/company from known job-board URL slugs."""
    u = unquote(url or "")
    d = host(u)
    path = urlparse(u).path.strip("/")
    slug = path.split("/")[-1]
    if "naukri.com" in d:
        slug = slug.replace("job-listings-", "")
    slug = re.sub(r"[-_](?:\d{4,}|[a-f0-9]{8,}).*$", "", slug, flags=re.I)
    words = [w for w in re.split(r"[-_]+", slug) if w and not w.isdigit()]
    cleaned = " ".join(words[:14]).strip().title()
    if not cleaned or len(cleaned) < 4:
        return "Architect Job", "Hiring Company"
    loc_words = {"mumbai","delhi","bangalore","bengaluru","hyderabad","pune","chennai","noida","gurugram","gurgaon","ahmedabad","india"}
    job_words = []
    company_words = []
    for w in words:
        if w.lower() in loc_words:
            break
        if len(job_words) < 5:
            job_words.append(w)
        elif len(company_words) < 5:
            company_words.append(w)
    title = " ".join(job_words).title() or cleaned
    company = " ".join(company_words).title() if company_words else extract_company_from_signal(cleaned, "") or "Hiring Company"
    return clean(title), clean(company)


def linkedin_job_id(url):
    """Return the numeric LinkedIn job id from a /jobs/view/ URL."""
    path = unquote(urlparse(url or "").path)
    m = re.search(r"/jobs/view/[^/?#]*?(\d{7,})", path, flags=re.I)
    return m.group(1) if m else ""


def clean_apply_url(url):
    """Remove search/tracking parameters so duplicates collapse into one clean apply link."""
    u = norm_url(url)
    if not u:
        return ""
    parsed = urlparse(u)
    d = host(u)
    path = parsed.path.rstrip("/")
    if is_linkedin_url(u):
        m = re.search(r"/jobs/view/([^/?#]+)", path, flags=re.I)
        if m:
            slug = unquote(m.group(1)).strip("/")
            # Use India LinkedIn host because these are India job links in our search.
            return f"https://in.linkedin.com/jobs/view/{quote(slug, safe='-%_+')}"
        return u.split("?", 1)[0].split("#", 1)[0]
    if "indeed.com" in d:
        qs = parse_qs(parsed.query)
        jk = (qs.get("jk") or [""])[0]
        if jk:
            base = f"{parsed.scheme or 'https'}://{parsed.netloc}{path or '/viewjob'}"
            return f"{base}?jk={quote(jk)}"
    if is_public_job_board_url(u):
        # Most board job-detail URLs keep the job id in the path; query params are tracking.
        return f"{parsed.scheme or 'https'}://{parsed.netloc}{path}"
    return u.split("#", 1)[0]


INDIA_CITY_PATTERN = r"(?:Mumbai|Navi Mumbai|Thane|Pune|New Delhi|Delhi|Gurugram|Gurgaon|Noida|Bengaluru|Bangalore|Hyderabad|Chennai|Ahmedabad|Kolkata|Jaipur|Kochi|Surat|Goa|India|Borivali|Worli|Wadala|Kurla|Tardeo|Defence Colony)"


def title_case_soft(value):
    value = clean(value)
    if not value:
        return value
    # Keep existing all-caps abbreviations but title-case obvious slug-like lowercase strings.
    if value.islower() or "-" in value:
        value = value.replace("-", " ").title()
    value = re.sub(r"\bBim\b", "BIM", value)
    value = re.sub(r"\bCad\b", "CAD", value)
    value = re.sub(r"\bAutocad\b", "AutoCAD", value)
    value = re.sub(r"\bLlp\b", "LLP", value)
    value = re.sub(r"\bPvt\b", "Pvt", value)
    return clean(value)


def slug_title_company(url):
    """Parse useful title/company from job-board slugs like role-at-company-446123."""
    path = unquote(urlparse(url or "").path)
    m = re.search(r"/jobs/view/([^/?#]+)", path, flags=re.I)
    slug = m.group(1) if m else path.strip("/").split("/")[-1]
    slug = re.sub(r"[-_](?:\d{7,}|[a-f0-9]{8,}).*$", "", slug, flags=re.I)
    if not slug:
        return "", ""
    slug = slug.replace("%E2%80%93", "-")
    if "-at-" in slug:
        role, company = slug.split("-at-", 1)
        return title_case_soft(role), title_case_soft(company)
    return title_case_soft(slug), ""


def strip_location_tail(value):
    value = clean(value)
    # Remove trailing LinkedIn location tail: "in Mumbai, Maharashtra, India".
    value = re.sub(rf"\s+in\s+{INDIA_CITY_PATTERN}(?:\s+Metropolitan\s+Region)?(?:,\s*[^,]+)*\s*$", "", value, flags=re.I)
    value = re.sub(r"\s+Metropolitan\s+Region\s*$", "", value, flags=re.I)
    value = re.sub(r",\s*(Maharashtra|Delhi|Karnataka|Telangana|Tamil Nadu|Gujarat|India)\s*,?\s*$", "", value, flags=re.I)
    return clean(value)


def clean_company_name(value, title="", url=""):
    value = clean(html.unescape(value or ""))
    value = re.sub(r"\s*\|\s*LinkedIn.*$", "", value, flags=re.I)
    # V8.2: employer_name should be company only; city already exists in location/address.
    value = re.sub(rf"\s+[—–-]\s+{INDIA_CITY_PATTERN}\s*$", "", value, flags=re.I)
    value = strip_location_tail(value)
    # "ABC hiring Junior Architect ..." -> "ABC"
    m = re.match(r"^(.+?)\s+hiring\s+.+$", value, flags=re.I)
    if m:
        value = m.group(1)
    # "BIM at Jacobs" / "Life Science & Tech - GFS at Burns & McDonnell India" -> company after last at
    m = re.search(r"\bat\s+([A-Za-z0-9&.,'()+/ ®\- ]{2,90})$", value, flags=re.I)
    if m and any(role in value.lower() for role in ("architect", "designer", "bim", "draft", "facade", "real estate", "life science")):
        value = m.group(1)
    # If company is only a location, try URL slug company.
    if re.fullmatch(rf"{INDIA_CITY_PATTERN}(?:,\s*[^,]+)*", value, flags=re.I) or value.lower() in {"design", "real estate", "architecture", "interior design", "bim"}:
        _, slug_company = slug_title_company(url)
        if slug_company:
            value = slug_company
    value = re.sub(r"\b(hiring|job|jobs|apply|career|careers)\b", "", value, flags=re.I)
    value = strip_location_tail(value).strip(" -|,–—")
    return title_case_soft(value[:90]) or "Hiring Company"


def clean_job_title(value, employer="", snippet="", url=""):
    raw = clean(html.unescape(value or ""))
    raw = re.sub(r"\s*\|\s*LinkedIn.*$", "", raw, flags=re.I)
    raw = re.sub(r"\s*[-–—]\s*(LinkedIn|Naukri\.com|Indeed|Glassdoor|Foundit|Shine|TimesJobs|Internshala).*$", "", raw, flags=re.I)
    raw = strip_location_tail(raw)

    # "Apply for Junior Architect at ABC in Mumbai" -> "Junior Architect"
    m = re.match(r"^Apply\s+for\s+(.+?)\s+at\s+.+$", raw, flags=re.I)
    if m:
        raw = m.group(1)
    # "ABC hiring Junior Architect in Mumbai" -> "Junior Architect"
    m = re.match(rf"^.+?\s+hiring\s+(.+?)(?:\s+in\s+{INDIA_CITY_PATTERN}.*)?$", raw, flags=re.I)
    if m:
        raw = m.group(1)
    # "Junior Architect at ABC" -> "Junior Architect"
    m = re.match(r"^(.+?)\s+at\s+.+$", raw, flags=re.I)
    if m:
        raw = m.group(1)
    # For titles like "Deputy Architect - BIM at Jacobs" keep the role portion.
    if " - " in raw or " – " in raw or " — " in raw:
        first = re.split(r"\s[-–—]\s", raw)[0]
        if role_relevant(first, snippet):
            raw = first
    # V8.2: recover better title from snippet when search result title is generic.
    if raw.lower() in GENERIC_TITLES_ALLOWED_WITH_DESC or not role_relevant(raw, snippet) or len(raw) > 80:
        snippet_title = extract_title_from_snippet(snippet)
        if snippet_title and role_relevant(snippet_title, snippet):
            raw = snippet_title
    # Fallback from slug if title still looks generic/bad.
    if not role_relevant(raw, snippet) or len(raw) > 80:
        slug_title, _ = slug_title_company(url)
        if slug_title and role_relevant(slug_title, snippet):
            raw = slug_title
    raw = re.sub(r"\b(hiring|apply for|job opening|opening)\b", "", raw, flags=re.I)
    raw = strip_location_tail(raw).strip(" -|,–—")
    return title_case_soft(raw[:90])


def clean_title_company_pair(title, employer, snippet="", url=""):
    raw = clean(html.unescape(title or ""))
    emp = clean(html.unescape(employer or ""))

    # Prefer exact LinkedIn-style patterns from title.
    m = re.match(rf"^(.+?)\s+hiring\s+(.+?)(?:\s+in\s+{INDIA_CITY_PATTERN}.*)?$", raw, flags=re.I)
    if m:
        emp = m.group(1)
        title = m.group(2)
    else:
        m = re.match(r"^Apply\s+for\s+(.+?)\s+at\s+(.+?)(?:\s+in\s+.+)?$", raw, flags=re.I)
        if m:
            title, emp = m.group(1), m.group(2)
        else:
            m = re.match(r"^(.+?)\s+at\s+(.+?)(?:\s+in\s+.+)?$", raw, flags=re.I)
            if m:
                title, emp = m.group(1), m.group(2)
            elif " at " in raw.lower():
                # "Associate Senior Architect - Life Science & Tech - GFS at Burns & McDonnell India"
                before, after = re.split(r"\s+at\s+", raw, maxsplit=1, flags=re.I)
                title, emp = before, after

    # Slug fallback can recover company when the search title is messy.
    slug_title, slug_company = slug_title_company(url)
    if (not emp or re.search(r"\bhiring\b|,\s*$", emp, re.I) or emp.lower() in {"design", "real estate", "architecture", "interior design", "bim"}) and slug_company:
        emp = slug_company
    if (not title or len(title) > 95 or not role_relevant(title, snippet)) and slug_title:
        title = slug_title

    title = clean_job_title(title, emp, snippet, url)
    emp = clean_company_name(emp, title, url)
    return title, emp


def record_identity_key(record):
    """Dedupe strongly by source job id, then by clean title/company/location."""
    url = clean_apply_url(record.get("apply_url"))
    if url and is_linkedin_url(url):
        jid = linkedin_job_id(url)
        if jid:
            return f"linkedin:{jid}"
    if url and is_public_job_board_url(url):
        parsed = urlparse(url)
        return f"board:{host(url)}:{parsed.path.lower().rstrip('/')}:{parsed.query.lower()}"
    title = clean_job_title(record.get("title"), record.get("employer_name"), record.get("description"), url).lower()
    company = clean_company_name(record.get("employer_name"), title, url).lower()
    loc = clean(record.get("location") or record.get("address")).lower()
    email = clean(record.get("apply_email") or record.get("employer_email")).lower()
    return "text:" + hashlib.sha1(f"{title}|{company}|{loc}|{email}".encode("utf-8")).hexdigest()


def detail_hit_from_url(url, fallback_title="", fallback_snippet="", query="", cfg=None):
    """Fetch a job-detail page and create a search-hit-like object with better title/description."""
    cfg = cfg or {}
    title = clean(fallback_title)
    snippet = clean(fallback_snippet)
    r = fetch_url(url, cfg)
    if r and r.text:
        soup = BeautifulSoup(r.text, "html.parser")
        meta_title = ""
        for selector in ["meta[property='og:title']", "meta[name='twitter:title']"]:
            tag = soup.select_one(selector)
            if tag and tag.get("content"):
                meta_title = clean(tag.get("content")); break
        page_title = clean(soup.title.get_text(" ") if soup.title else "")
        if meta_title:
            title = meta_title
        elif page_title:
            title = page_title
        meta_desc = ""
        for selector in ["meta[property='og:description']", "meta[name='description']", "meta[name='twitter:description']"]:
            tag = soup.select_one(selector)
            if tag and tag.get("content"):
                meta_desc = clean(tag.get("content")); break
        body_text = clean(soup.get_text(" "))
        if meta_desc:
            snippet = meta_desc
        elif body_text:
            snippet = body_text[:700]
    if not title:
        t, c = title_from_url_slug(url)
        title = f"{t} at {c}"
    return {"title": title, "url": norm_url(url), "snippet": snippet, "query": query}


def direct_job_board_search_urls(cfg):
    roles = cfg.get("v8_roles") or [
        "Junior Architect", "Architect", "Senior Architect", "Project Architect", "Design Architect",
        "Interior Designer", "Interior Architect", "BIM Coordinator", "BIM Architect", "Revit Architect",
        "3D Visualizer", "3D Visualiser", "Architectural Draftsman", "Landscape Architect", "Urban Designer",
    ]
    cities = cfg.get("v8_cities") or ["Mumbai", "Delhi", "Bengaluru", "Hyderabad", "Pune", "Chennai", "Ahmedabad", "Noida", "Gurugram"]
    max_pairs = int(cfg.get("v8_max_direct_role_city_pairs", 55))
    pairs = []
    for role in roles:
        for city in cities:
            pairs.append((role, city))
            if len(pairs) >= max_pairs:
                break
        if len(pairs) >= max_pairs:
            break
    urls = []
    for role, city in pairs:
        q = quote(role)
        q_plus = quote(role.replace(" ", "+"))
        loc = quote(city)
        loc_plus = quote(city.replace(" ", "+"))
        urls.extend([
            (f"https://www.naukri.com/{role.lower().replace(' ', '-')}-jobs-in-{city.lower().replace(' ', '-')}", role, city),
            (f"https://in.indeed.com/jobs?q={q_plus}&l={loc_plus}&fromage=14", role, city),
            (f"https://www.linkedin.com/jobs/search?keywords={q}&location={loc}%2C%20India", role, city),
            (f"https://www.timesjobs.com/candidate/job-search.html?searchType=personalizedSearch&txtKeywords={q}&txtLocation={loc}", role, city),
        ])
    return urls[: int(cfg.get("v8_max_direct_search_pages", 180))]


def collect_direct_job_board_records(cfg):
    if not bool(cfg.get("v8_direct_job_board_harvest_enabled", True)):
        return []
    print("=" * 80)
    print("V8 DIRECT JOB-BOARD HARVEST | extracting specific apply links")
    print("=" * 80)
    target = int(cfg.get("v8_direct_target_records_per_run", 120))
    records = []
    seen_urls = set()
    queries = cfg.get("v8_apply_link_search_queries") or cfg.get("v6_quantity_search_queries", [])
    max_queries = int(cfg.get("v8_max_apply_link_queries", 90))
    for query in queries[:max_queries]:
        hits = []
        hits.extend(bing_rss(query, cfg))
        hits.extend(bing_html(query, cfg))
        hits.extend(duckduckgo_html(query, cfg))
        for hit in hits:
            u = norm_url(hit.get("url"))
            if not u or u in seen_urls:
                continue
            if not is_specific_public_job_detail_url(u):
                continue
            seen_urls.add(u)
            detail_hit = detail_hit_from_url(u, hit.get("title"), hit.get("snippet"), query, cfg)
            rec, reason = quantity_signal_to_record(detail_hit, cfg)
            if rec:
                records.append(rec)
                print(f"V8 ACCEPT APPLY LINK | {rec.get('title')} | {rec.get('employer_name')} | {u}")
            else:
                print(f"V8 REJECT APPLY LINK | {u} | {reason}")
            if len(records) >= target:
                return records
        time.sleep(float(cfg.get("v8_search_delay_seconds", 0.15)))

    for page_url, role, city in direct_job_board_search_urls(cfg):
        print(f"V8 DIRECT SEARCH PAGE | {role} | {city} | {page_url}")
        r = fetch_url(page_url, cfg)
        if not r or not r.text:
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        candidates = []
        for a in soup.find_all("a", href=True):
            u = decode_search_redirect(urljoin(page_url, a.get("href")))
            if is_specific_public_job_detail_url(u):
                candidates.append((u, clean(a.get_text(" "))))
        for u in extract_urls_from_text_blob(r.text, page_url):
            if is_specific_public_job_detail_url(u):
                candidates.append((u, ""))
        for u, anchor_title in candidates:
            if u in seen_urls:
                continue
            seen_urls.add(u)
            detail_hit = detail_hit_from_url(u, anchor_title or role, f"{role} job opening in {city}, India. Apply via public job link.", f"{role} {city}", cfg)
            rec, reason = quantity_signal_to_record(detail_hit, cfg)
            if rec:
                if rec.get("location") == "India" and city:
                    rec["location"] = f"{city}|India"
                    rec["address"] = f"{city}, India"
                records.append(rec)
                print(f"V8 ACCEPT DIRECT | {rec.get('title')} | {rec.get('employer_name')} | {u}")
            else:
                print(f"V8 REJECT DIRECT | {u} | {reason}")
            if len(records) >= target:
                return records
        time.sleep(float(cfg.get("v8_direct_page_delay_seconds", 0.25)))
    return records


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
    job_title, company = clean_title_company_pair(title, "", snippet, url)
    if not company or company == "Hiring Company":
        company = extract_company_from_signal(title, snippet)
    if not company or company == "Hiring Company":
        _, slug_company = slug_title_company(url)
        if slug_company:
            company = slug_company
    if not job_title:
        slug_title, _ = slug_title_company(url)
        job_title = slug_title or "Architect Job"
    return clean_job_title(job_title, company, snippet, url), clean_company_name(company, job_title, url)

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
    title_l = clean(title).lower()
    desc_l = clean(desc).lower()
    if re.search(r"(junior|jr\.?|fresher|entry|intern|trainee|graduate)", title_l) or re.search(r"0\s*(?:-|to|–)\s*2\s*(?:years?|yrs?)", desc_l):
        return "Entry Level"
    if re.search(r"(senior|sr\.?|lead|manager|head|principal|director)", title_l):
        return "Senior Level"
    if re.search(r"(5\+|6\+|7\+|8\+|9\+|10\+)\s*(?:years?|yrs?)", desc_l):
        return "Senior Level"
    return "Mid Level"


def extract_experience(text):
    t = clean(text)
    candidates = []
    for pat in [
        r"(\d+\s*(?:-|to|–)\s*\d+\s*(?:years?|yrs?))",
        r"(minimum\s+\d+\s*(?:years?|yrs?))",
        r"(\d+\+?\s*(?:years?|yrs?))",
    ]:
        for m in re.finditer(pat, t, re.I):
            value = clean(m.group(1))
            nums = years_in_text(value)
            if nums and max(nums) <= MAX_REAL_EXPERIENCE_YEARS:
                # Avoid company-history lines like "more than 60 years since...".
                window = t[max(0, m.start()-35):m.end()+45].lower()
                if "since" in window and "experience" not in window:
                    continue
                candidates.append(value)
    return candidates[0] if candidates else ""


def quantity_signal_to_record(hit, cfg):
    url = clean_apply_url(hit.get("url"))
    title = clean(hit.get("title"))
    snippet = clean(BeautifulSoup(hit.get("snippet") or "", "html.parser").get_text(" "))
    query = clean(hit.get("query"))
    if not url:
        return None, "Missing URL"
    if is_linkedin_url(url) and not is_specific_linkedin_job_url(url):
        return None, "LinkedIn URL is not a specific job detail page"
    if looks_like_reference_or_content_page(title, snippet, url):
        return None, "Reference/content/product page, not a job vacancy"
    if is_public_job_board_url(url) and not is_specific_job_board_job_url(url):
        return None, "Not a specific job-board detail URL"
    if not (is_public_job_board_url(url) or is_linkedin_url(url)):
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
    desc = snippet or f"{job_title} opening at {employer}. Public job source detected by V8.2 Final Website Quality Mode."
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
    print(f"V8.2 FINAL WEBSITE QUALITY MODE | queries={min(len(queries), max_queries)} target={target}")
    print("=" * 80)
    for query in queries[:max_queries]:
        print(f"V8.2 JOB SEARCH | {query}")
        for hit in bing_rss(query, cfg):
            u = norm_url(hit.get("url"))
            if not u or u in seen_urls:
                continue
            seen_urls.add(u)
            rec, reason = quantity_signal_to_record(hit, cfg)
            if rec:
                records.append(rec)
                print(f"V8.2 ACCEPT SIGNAL JOB | {rec.get('employer_name')} | {rec.get('title')} | {u}")
            else:
                print(f"V8.2 REJECT SIGNAL | {hit.get('title')} | {reason}")
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
            print(f"V8.2 REJECT EXTRA | {web.get('employer_name')} | {web.get('title')} | {reason}")
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
    direct_records = collect_direct_job_board_records(cfg)
    merged_extra = []
    seen_extra = set()
    for r in (quantity_records + direct_records):
        k = web_key(r)
        if k not in seen_extra:
            seen_extra.add(k)
            merged_extra.append(r)
    quantity_records = merged_extra
    print("=" * 80)
    print(f"V8.2 final quality apply-link records before Sheet1 merge: {len(quantity_records)}")
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
    print("V8.2 SINGLE SHEET FINAL WEBSITE QUALITY COMPLETE")
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
    ok, reason = website_record_valid(linkedin, {**cfg, "allow_linkedin_job_apply_url": True})
    assert ok, reason

    messy_hit = {
        "title": "ANA hiring Interior Draftsmem in Mumbai, Maharashtra, India",
        "snippet": "Posted today. Company Description ANA is an interior design and architecture firm. Apply now.",
        "url": "https://in.linkedin.com/jobs/view/interior-draftsmem-4461315335?position=58&pageNum=0&trackingId=abc",
        "query": "Interior Designer Mumbai India LinkedIn",
    }
    rec, reason = quantity_signal_to_record(messy_hit, {**cfg, "allow_linkedin_job_apply_url": True})
    assert rec, reason
    assert rec["title"] == "Interior Draftsmem", rec["title"]
    assert rec["employer_name"] == "ANA", rec["employer_name"]
    assert rec["apply_url"] == "https://in.linkedin.com/jobs/view/interior-draftsmem-4461315335", rec["apply_url"]

    duplicate_hit = dict(messy_hit)
    duplicate_hit["url"] = "https://in.linkedin.com/jobs/view/interior-draftsmem-4461315335"
    rec2, reason = quantity_signal_to_record(duplicate_hit, {**cfg, "allow_linkedin_job_apply_url": True})
    assert rec2, reason
    assert web_key(rec) == web_key(rec2)

    linkedin_search = dict(linkedin)
    linkedin_search["apply_url"] = "https://www.linkedin.com/jobs/search?keywords=architect"
    ok, reason = website_record_valid(linkedin_search, {**cfg, "allow_linkedin_job_apply_url": True})
    assert not ok, reason

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

    print("V8.2 SELF TEST PASSED: single Sheet1 output, fake-row cleanup, specific LinkedIn/job-board detail URLs allowed, tracking parameters removed, titles/companies cleaned, dedupe and blank external_id are working.")


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
