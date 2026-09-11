import argparse
import hashlib
import html
import json
import os
import re
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from urllib.parse import urljoin, urlparse, urldefrag
from zoneinfo import ZoneInfo
import xml.etree.ElementTree as ET

import requests
import yaml
from bs4 import BeautifulSoup, MarkupResemblesLocatorWarning
from dateutil import parser as dateparser

warnings.filterwarnings("ignore", category=MarkupResemblesLocatorWarning)

IST = ZoneInfo("Asia/Kolkata")
USER_AGENT = (
    "ArchitectJobsCollector/2.0 "
    "(public-job-indexer; respects public access controls; no authentication bypass)"
)

V2_HEADERS = [
    "Job ID",
    "Job Title",
    "Company",
    "Location",
    "City",
    "State",
    "Country",
    "Job Category",
    "Job Type",
    "Experience",
    "Salary",
    "Skills",
    "Short Description",
    "Full Description",
    "Posted Date",
    "Application Deadline",
    "Freshness",
    "Application Status",
    "Application Method",
    "Source Type",
    "Source Name",
    "Source URL",
    "Apply URL",
    "Company Website",
    "Logo URL",
    "Public Contact Email",
    "Public Contact Phone",
    "First Seen",
    "Last Seen",
    "Verified At",
    "Status",
    "Closed Reason",
    "Fingerprint",
]

CITY_STATE = {
    "new delhi": "Delhi",
    "delhi": "Delhi",
    "mumbai": "Maharashtra",
    "navi mumbai": "Maharashtra",
    "thane": "Maharashtra",
    "pune": "Maharashtra",
    "nagpur": "Maharashtra",
    "nashik": "Maharashtra",
    "aurangabad": "Maharashtra",
    "bengaluru": "Karnataka",
    "bangalore": "Karnataka",
    "mysuru": "Karnataka",
    "mysore": "Karnataka",
    "mangaluru": "Karnataka",
    "mangalore": "Karnataka",
    "hyderabad": "Telangana",
    "chennai": "Tamil Nadu",
    "coimbatore": "Tamil Nadu",
    "madurai": "Tamil Nadu",
    "kolkata": "West Bengal",
    "gurugram": "Haryana",
    "gurgaon": "Haryana",
    "faridabad": "Haryana",
    "noida": "Uttar Pradesh",
    "greater noida": "Uttar Pradesh",
    "lucknow": "Uttar Pradesh",
    "kanpur": "Uttar Pradesh",
    "ahmedabad": "Gujarat",
    "surat": "Gujarat",
    "vadodara": "Gujarat",
    "rajkot": "Gujarat",
    "jaipur": "Rajasthan",
    "udaipur": "Rajasthan",
    "jodhpur": "Rajasthan",
    "kochi": "Kerala",
    "cochin": "Kerala",
    "thiruvananthapuram": "Kerala",
    "trivandrum": "Kerala",
    "calicut": "Kerala",
    "kozhikode": "Kerala",
    "chandigarh": "Chandigarh",
    "mohali": "Punjab",
    "ludhiana": "Punjab",
    "indore": "Madhya Pradesh",
    "bhopal": "Madhya Pradesh",
    "goa": "Goa",
    "panaji": "Goa",
    "visakhapatnam": "Andhra Pradesh",
    "vijayawada": "Andhra Pradesh",
    "bhubaneswar": "Odisha",
    "dehradun": "Uttarakhand",
    "guwahati": "Assam",
    "raipur": "Chhattisgarh",
    "ranchi": "Jharkhand",
}

ROLE_CATEGORY_RULES = [
    ("BIM", ["bim", "revit"]),
    ("Landscape Architecture", ["landscape architect"]),
    ("Urban Design / Planning", ["urban designer", "urban planner", "urban design", "urban planning"]),
    ("Interior Design", ["interior architect", "interior designer", "interior design"]),
    ("Visualization", ["3d visualizer", "visualizer", "visualisation", "visualization"]),
    ("Architecture", ["architect", "architecture", "architectural"]),
]

JOB_LINK_HINTS = (
    "job", "jobs", "career", "careers", "opening", "openings", "vacancy", "vacancies",
    "position", "positions", "opportunity", "opportunities", "recruit"
)


def now_ist():
    return datetime.now(IST)


def now_iso():
    return now_ist().isoformat(timespec="seconds")


def clean_text(value):
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(x for x in (clean_text(v) for v in value) if x)
    if isinstance(value, dict):
        return clean_text(value.get("name") or value.get("value") or "")
    text = BeautifulSoup(str(value), "html.parser").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def canonical_url(url):
    if not url:
        return ""
    url, _ = urldefrag(str(url).strip())
    return url.rstrip("/")


def domain(url):
    try:
        return urlparse(url).netloc.lower().split(":")[0]
    except Exception:
        return ""


def origin(url):
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}" if p.scheme and p.netloc else ""


def is_blocked_domain(url, cfg):
    d = domain(url)
    return any(d == x or d.endswith("." + x) for x in cfg.get("blocked_domains", []))


def login_gated_url(url, cfg):
    low = (url or "").lower()
    return any(marker.lower() in low for marker in cfg.get("login_url_markers", []))


def fetch(url, cfg, method="GET"):
    if not url or is_blocked_domain(url, cfg) or login_gated_url(url, cfg):
        return None
    try:
        r = requests.request(
            method,
            url,
            timeout=cfg.get("request_timeout_seconds", 15),
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            },
            allow_redirects=True,
        )
        if r.status_code >= 400:
            return None
        if is_blocked_domain(r.url, cfg) or login_gated_url(r.url, cfg):
            return None
        return r
    except requests.RequestException:
        return None


def parse_date(value):
    value = clean_text(value)
    if not value:
        return None
    try:
        dt = dateparser.parse(value, fuzzy=False)
        if not dt:
            return None
        return dt.date()
    except Exception:
        return None


def date_iso(value):
    d = parse_date(value) if not isinstance(value, date) else value
    return d.isoformat() if d else ""


def minimum_date(cfg):
    d = parse_date(cfg.get("minimum_posted_date"))
    if not d:
        raise ValueError("config.yaml must contain a valid minimum_posted_date, e.g. 2026-09-01")
    return d


def extract_public_contacts(text):
    text = text or ""
    emails = re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)
    emails = [e for e in emails if not e.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".svg"))]

    compact = re.sub(r"[\s().-]+", "", text)
    phones = re.findall(r"(?:\+91)?[6-9]\d{9}", compact)
    return (emails[0] if emails else ""), (phones[0] if phones else "")


def jsonld_objects(soup):
    for tag in soup.find_all("script", attrs={"type": re.compile(r"ld\+json", re.I)}):
        raw = tag.string or tag.get_text()
        try:
            data = json.loads(raw)
        except Exception:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            item = stack.pop()
            if isinstance(item, list):
                stack.extend(item)
            elif isinstance(item, dict):
                graph = item.get("@graph")
                if isinstance(graph, list):
                    stack.extend(graph)
                yield item


def type_contains(obj, wanted):
    typ = obj.get("@type")
    values = typ if isinstance(typ, list) else [typ]
    return wanted in values


def parse_address(job_location):
    if isinstance(job_location, list):
        job_location = job_location[0] if job_location else {}
    if not isinstance(job_location, dict):
        return "", "", "", ""

    addr = job_location.get("address") if isinstance(job_location.get("address"), dict) else job_location
    city = clean_text(addr.get("addressLocality"))
    state = clean_text(addr.get("addressRegion"))
    country = addr.get("addressCountry")
    if isinstance(country, dict):
        country = clean_text(country.get("name"))
    else:
        country = clean_text(country)
    location = ", ".join(v for v in (city, state, country) if v)
    return location, city, state, country


def detect_location(text):
    low = (text or "").lower()
    # Prefer longer city names first, e.g. Navi Mumbai before Mumbai.
    for city in sorted(CITY_STATE, key=len, reverse=True):
        if re.search(rf"\b{re.escape(city)}\b", low):
            display = " ".join(w.capitalize() for w in city.split())
            # Keep common official spellings.
            display = {
                "Bengaluru": "Bengaluru",
                "New Delhi": "New Delhi",
                "Navi Mumbai": "Navi Mumbai",
                "Greater Noida": "Greater Noida",
            }.get(display, display)
            return f"{display}, {CITY_STATE[city]}, India", display, CITY_STATE[city], "India"
    if "india" in low:
        return "India", "", "", "India"
    return "", "", "", ""


def is_india_job(job, cfg):
    country = (job.get("country") or "").lower()
    if country and country not in ("india", "in", "ind") and "india" not in country:
        return False
    loc = " ".join(
        [job.get("location", ""), job.get("city", ""), job.get("state", ""), job.get("country", "")]
    ).lower()
    return any(marker.lower() in loc for marker in cfg.get("india_markers", []))


def relevant_architecture_job(job, cfg):
    hay = " ".join([job.get("title", ""), job.get("description", ""), job.get("skills", "")]).lower()
    if any(term.lower() in hay for term in cfg["keywords"]["exclude"]):
        return False
    return any(term.lower() in hay for term in cfg["keywords"]["include"])


def job_category(title):
    low = (title or "").lower()
    for category, terms in ROLE_CATEGORY_RULES:
        if any(term in low for term in terms):
            return category
    return "Architecture / Design"


def extract_job_type(text):
    low = (text or "").lower()
    mapping = [
        ("Internship", ["internship", "intern "]),
        ("Part Time", ["part-time", "part time"]),
        ("Contract", ["contract", "freelance"]),
        ("Full Time", ["full-time", "full time", "permanent"]),
    ]
    found = [label for label, terms in mapping if any(t in low for t in terms)]
    return ", ".join(found)


def extract_experience(text):
    text = text or ""
    patterns = [
        r"\b\d+\s*(?:-|–|to)\s*\d+\s*(?:years?|yrs?)\b",
        r"\b\d+\+\s*(?:years?|yrs?)\b",
        r"\bminimum\s+\d+\s*(?:years?|yrs?)\b",
        r"\b(?:experience|exp)\s*[:\-]?\s*\d+\s*(?:-|–|to)?\s*\d*\s*(?:years?|yrs?)\b",
    ]
    for p in patterns:
        m = re.search(p, text, re.I)
        if m:
            return clean_text(m.group(0))
    return ""


def extract_salary(text):
    text = text or ""
    patterns = [
        r"₹\s?[\d,.]+\s*(?:-|–|to)\s*₹?\s?[\d,.]+(?:\s*(?:per month|/month|p\.m\.|per annum|/year|lpa))?",
        r"\b(?:INR|Rs\.?|₹)\s?[\d,.]+(?:\s*(?:LPA|lpa|per month|per annum))?",
    ]
    for p in patterns:
        m = re.search(p, text, re.I)
        if m:
            return clean_text(m.group(0))
    return ""


def extract_skills(text, cfg):
    low = (text or "").lower()
    found = []
    for skill in cfg.get("skill_terms", []):
        if skill.lower() in low:
            found.append(skill)
    return ", ".join(found)


def extract_logo(soup, page_url):
    for obj in jsonld_objects(soup):
        if type_contains(obj, "Organization"):
            logo = obj.get("logo")
            if isinstance(logo, dict):
                logo = logo.get("url") or logo.get("contentUrl")
            if logo:
                return canonical_url(urljoin(page_url, str(logo)))
    link = soup.find("link", rel=lambda x: x and any("icon" in str(v).lower() for v in (x if isinstance(x, list) else [x])))
    if link and link.get("href"):
        return canonical_url(urljoin(page_url, link["href"]))
    meta = soup.find("meta", attrs={"property": "og:image"})
    if meta and meta.get("content"):
        return canonical_url(urljoin(page_url, meta["content"]))
    return ""


def extract_company_name(soup, page_url):
    for obj in jsonld_objects(soup):
        if type_contains(obj, "Organization") and obj.get("name"):
            return clean_text(obj.get("name"))
    meta = soup.find("meta", attrs={"property": "og:site_name"})
    if meta and meta.get("content"):
        return clean_text(meta["content"])
    d = domain(page_url).split(".")
    core = d[-2] if len(d) >= 2 else (d[0] if d else "")
    return re.sub(r"[-_]", " ", core).title()


def extract_title(soup):
    h1 = soup.find("h1")
    if h1 and clean_text(h1.get_text()):
        return clean_text(h1.get_text())
    meta = soup.find("meta", attrs={"property": "og:title"})
    if meta and meta.get("content"):
        return clean_text(meta["content"])
    if soup.title:
        return clean_text(soup.title.get_text())
    return ""


def clean_title(title, company=""):
    title = clean_text(title)
    for sep in (" | ", " – ", " — ", " - "):
        parts = title.split(sep)
        if len(parts) > 1:
            # Keep the side that looks like an architecture role.
            role_parts = [p for p in parts if any(t in p.lower() for t in ("architect", "designer", "bim", "visualizer", "planner"))]
            if role_parts:
                title = role_parts[0].strip()
                break
    if company and title.lower() == company.lower():
        return ""
    return title[:220]


def labeled_date_from_text(text, labels):
    text = re.sub(r"\s+", " ", text or " ")
    label_pattern = "|".join(re.escape(x) for x in labels)
    date_patterns = [
        r"\d{4}-\d{1,2}-\d{1,2}",
        r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}",
        r"\d{1,2}\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{4}",
        r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2},?\s+\d{4}",
    ]
    dp = "(?:" + "|".join(date_patterns) + ")"
    m = re.search(rf"(?:{label_pattern})\s*[:\-]?\s*({dp})", text, re.I)
    return parse_date(m.group(1)) if m else None


def extract_posted_date(soup, text, structured_value=None):
    d = parse_date(structured_value)
    if d:
        return d

    selectors = [
        ("meta", {"property": "article:published_time"}, "content"),
        ("meta", {"name": "date"}, "content"),
        ("meta", {"itemprop": "datePosted"}, "content"),
    ]
    for tag_name, attrs, attr in selectors:
        tag = soup.find(tag_name, attrs=attrs)
        if tag and tag.get(attr):
            d = parse_date(tag.get(attr))
            if d:
                return d

    for tag in soup.find_all("time"):
        nearby = clean_text(tag.parent.get_text(" ", strip=True) if tag.parent else tag.get_text())
        if any(k in nearby.lower() for k in ("posted", "published", "date posted")):
            d = parse_date(tag.get("datetime") or tag.get_text())
            if d:
                return d

    return labeled_date_from_text(text, ["posted", "posted on", "date posted", "published", "published on", "opening posted"])


def extract_deadline(soup, text, structured_value=None):
    d = parse_date(structured_value)
    if d:
        return d
    return labeled_date_from_text(
        text,
        ["application deadline", "apply by", "last date", "closing date", "applications close", "deadline"],
    )


def page_closed_reason(text, deadline, cfg):
    low = (text or "").lower()
    for signal in cfg.get("closed_signals", []):
        if signal.lower() in low:
            return f"Closed signal: {signal}"
    if deadline and deadline < now_ist().date():
        return f"Application deadline passed: {deadline.isoformat()}"
    return ""


def page_has_open_signal(text, cfg):
    low = (text or "").lower()
    return any(signal.lower() in low for signal in cfg.get("open_signals", []))


def find_apply_route(soup, page_url, page_text, cfg):
    # 1. Explicit application links and mailto links.
    candidates = []
    for a in soup.find_all("a", href=True):
        href = a.get("href", "").strip()
        label = clean_text(a.get_text(" ", strip=True)).lower()
        parent_text = clean_text(a.parent.get_text(" ", strip=True) if a.parent else "").lower()
        if href.lower().startswith("mailto:"):
            email = href.split(":", 1)[1].split("?", 1)[0].strip()
            return href, "Email", email
        if any(k in label for k in ("apply", "submit application", "send cv", "send resume")) or (
            "apply" in href.lower() and "apply" in parent_text
        ):
            full = canonical_url(urljoin(page_url, href))
            if full and not is_blocked_domain(full, cfg) and not login_gated_url(full, cfg):
                candidates.append(full)
    if candidates:
        return candidates[0], "Apply Link", ""

    # 2. Public form on the page.
    if soup.find("form") and page_has_open_signal(page_text, cfg):
        return canonical_url(page_url), "Public Form", ""

    # 3. Public email written in the page text.
    email, _ = extract_public_contacts(page_text)
    if email and page_has_open_signal(page_text, cfg):
        return f"mailto:{email}", "Email", email

    return "", "", email if 'email' in locals() else ""


def verify_apply_url(apply_url, source_url, page_text, cfg):
    if not apply_url:
        return False, "No public application route", ""
    if apply_url.lower().startswith("mailto:"):
        return True, "", apply_url
    if is_blocked_domain(apply_url, cfg):
        return False, "Blocked source (LinkedIn or configured domain)", ""
    if login_gated_url(apply_url, cfg):
        return False, "Application URL is login-gated", ""

    # If applying on the same page we already fetched, page signals are enough.
    if canonical_url(apply_url) == canonical_url(source_url):
        low = (page_text or "").lower()
        if any(x in low for x in ("sign in to apply", "login to apply", "create an account to apply")):
            return False, "Application requires account/login", ""
        return True, "", canonical_url(apply_url)

    if not cfg.get("verify_apply_urls", True):
        return True, "", canonical_url(apply_url)

    r = fetch(apply_url, cfg)
    if not r:
        return False, "Apply URL is not publicly reachable", ""
    low = (r.text[:200000] or "").lower()
    if any(x in low for x in ("sign in to apply", "login to apply", "log in to apply", "create an account to apply")):
        return False, "Application requires account/login", ""
    if page_closed_reason(low, None, cfg):
        return False, "Apply page says the job is closed/expired", ""
    return True, "", canonical_url(r.url)


def validate_freshness(posted_date, deadline, page_text, apply_url, application_method, cfg):
    cutoff = minimum_date(cfg)
    today = now_ist().date()

    closed = page_closed_reason(page_text, deadline, cfg)
    if closed:
        return False, "Closed", closed, ""

    if posted_date:
        if posted_date < cutoff:
            return False, "Stale", f"Posted before cutoff {cutoff.isoformat()}", ""
        if posted_date > today:
            # A future 'posted' date is probably a misidentified joining/deadline date.
            posted_date = None
        else:
            return True, "Fresh", "", posted_date

    # No usable date: only accept when there is strong evidence it is a live vacancy.
    if cfg.get("accept_undated_if_live_verified", True):
        has_open = page_has_open_signal(page_text, cfg)
        has_apply = bool(apply_url or application_method)
        if has_open and has_apply:
            return True, "Live Verified (No Posted Date)", "", None

    return False, "Unverified", "No acceptable posted date and insufficient live-opening evidence", ""


def fingerprint(job):
    base = "|".join(
        [
            clean_text(job.get("title")).lower(),
            clean_text(job.get("company")).lower(),
            clean_text(job.get("city")).lower(),
            canonical_url(job.get("apply_url") or job.get("source_url")).lower(),
        ]
    )
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:24]


def job_id(fp):
    return hashlib.sha1(fp.encode("utf-8")).hexdigest()[:16]


def normalize_job(job, cfg):
    job["title"] = clean_title(job.get("title", ""), job.get("company", ""))
    if not job.get("title"):
        return None

    job["description"] = clean_text(job.get("description"))
    job["skills"] = clean_text(job.get("skills")) or extract_skills(job["description"], cfg)
    job["experience"] = clean_text(job.get("experience")) or extract_experience(job["description"])
    job["salary"] = clean_text(job.get("salary")) or extract_salary(job["description"])
    job["job_type"] = clean_text(job.get("job_type")) or extract_job_type(job["description"])
    job["job_category"] = job_category(job["title"])

    if not job.get("location"):
        loc, city, state, country = detect_location(" ".join([job["title"], job["description"]]))
        job.update(location=loc, city=city, state=state, country=country)
    elif not job.get("city") or not job.get("state"):
        loc, city, state, country = detect_location(job.get("location", ""))
        job["city"] = job.get("city") or city
        job["state"] = job.get("state") or state
        job["country"] = job.get("country") or country
        if loc and not job.get("location"):
            job["location"] = loc

    job["country"] = job.get("country") or ("India" if is_india_job(job, cfg) else "")
    if not relevant_architecture_job(job, cfg) or not is_india_job(job, cfg):
        return None

    posted = job.get("posted_date") if isinstance(job.get("posted_date"), date) else parse_date(job.get("posted_date"))
    deadline = job.get("deadline") if isinstance(job.get("deadline"), date) else parse_date(job.get("deadline"))

    accepted, freshness, reason, posted = validate_freshness(
        posted,
        deadline,
        job.get("page_text", job["description"]),
        job.get("apply_url"),
        job.get("application_method"),
        cfg,
    )
    if not accepted:
        return None

    ok, apply_reason, final_apply = verify_apply_url(
        job.get("apply_url", ""), job.get("source_url", ""), job.get("page_text", ""), cfg
    )
    if not ok:
        return None
    job["apply_url"] = final_apply or job.get("apply_url", "")

    job["posted_date"] = date_iso(posted)
    job["deadline"] = date_iso(deadline)
    job["freshness"] = freshness
    job["application_status"] = "Open"
    job["status"] = "Active"
    job["closed_reason"] = ""
    job["verified_at"] = now_iso()
    job["fingerprint"] = fingerprint(job)
    job.pop("page_text", None)
    return job


def structured_salary(obj):
    b = obj.get("baseSalary")
    if not isinstance(b, dict):
        return ""
    currency = clean_text(b.get("currency"))
    val = b.get("value")
    if isinstance(val, dict):
        min_v = val.get("minValue")
        max_v = val.get("maxValue")
        unit = clean_text(val.get("unitText"))
        if min_v is not None or max_v is not None:
            range_text = f"{min_v or ''}-{max_v or ''}".strip("-")
            return " ".join(v for v in (currency, range_text, unit) if v)
        if val.get("value") is not None:
            return " ".join(v for v in (currency, str(val.get("value")), unit) if v)
    return clean_text(val)


def job_from_jsonld(obj, page_url, soup, page_text, cfg):
    if not type_contains(obj, "JobPosting"):
        return None

    org = obj.get("hiringOrganization") if isinstance(obj.get("hiringOrganization"), dict) else {}
    company = clean_text(org.get("name")) or extract_company_name(soup, page_url)
    company_site = canonical_url(org.get("sameAs") or org.get("url") or origin(page_url))
    logo = org.get("logo")
    if isinstance(logo, dict):
        logo = logo.get("url") or logo.get("contentUrl")
    logo = canonical_url(urljoin(page_url, str(logo))) if logo else extract_logo(soup, page_url)

    location, city, state, country = parse_address(obj.get("jobLocation"))
    if not location:
        location, city, state, country = detect_location(page_text)

    email, phone = extract_public_contacts(page_text)
    source_url = canonical_url(page_url)
    apply_url = canonical_url(obj.get("url") or source_url)
    application_method = "Apply Link"
    if apply_url == source_url:
        found_apply, method, found_email = find_apply_route(soup, source_url, page_text, cfg)
        if found_apply:
            apply_url, application_method = found_apply, method
            email = email or found_email

    job = {
        "title": clean_text(obj.get("title")),
        "company": company,
        "location": location,
        "city": city,
        "state": state,
        "country": country,
        "job_type": clean_text(obj.get("employmentType")),
        "experience": clean_text(obj.get("experienceRequirements")),
        "salary": structured_salary(obj),
        "skills": clean_text(obj.get("skills") or obj.get("qualifications")),
        "description": clean_text(obj.get("description")),
        "posted_date": extract_posted_date(soup, page_text, obj.get("datePosted")),
        "deadline": extract_deadline(soup, page_text, obj.get("validThrough")),
        "source_type": "JSON-LD",
        "source_name": domain(page_url),
        "source_url": source_url,
        "apply_url": apply_url,
        "application_method": application_method,
        "company_website": company_site,
        "logo_url": logo,
        "contact_email": email,
        "contact_phone": phone,
        "page_text": page_text,
    }
    return normalize_job(job, cfg)


def best_main_text(soup):
    main = soup.find("main") or soup.find("article")
    if main:
        return clean_text(main.get_text(" ", strip=True))[:50000]
    body = soup.body or soup
    return clean_text(body.get_text(" ", strip=True))[:50000]


def html_page_job(page_url, response_text, cfg, title_hint=""):
    soup = BeautifulSoup(response_text, "html.parser")
    page_text = best_main_text(soup)
    company = extract_company_name(soup, page_url)
    title = clean_title(title_hint or extract_title(soup), company)

    # If the document title is generic, pick a relevant heading.
    if not relevant_architecture_job({"title": title, "description": "", "skills": ""}, cfg):
        for tag in soup.find_all(["h1", "h2", "h3", "h4"]):
            candidate = clean_title(tag.get_text(" ", strip=True), company)
            if relevant_architecture_job({"title": candidate, "description": "", "skills": ""}, cfg):
                title = candidate
                break

    if not title:
        return None

    location, city, state, country = detect_location(page_text)
    email, phone = extract_public_contacts(page_text)
    apply_url, application_method, apply_email = find_apply_route(soup, page_url, page_text, cfg)
    email = email or apply_email

    job = {
        "title": title,
        "company": company,
        "location": location,
        "city": city,
        "state": state,
        "country": country,
        "job_type": extract_job_type(page_text),
        "experience": extract_experience(page_text),
        "salary": extract_salary(page_text),
        "skills": extract_skills(page_text, cfg),
        "description": page_text,
        "posted_date": extract_posted_date(soup, page_text),
        "deadline": extract_deadline(soup, page_text),
        "source_type": "HTML",
        "source_name": domain(page_url),
        "source_url": canonical_url(page_url),
        "apply_url": apply_url,
        "application_method": application_method,
        "company_website": origin(page_url),
        "logo_url": extract_logo(soup, page_url),
        "contact_email": email,
        "contact_phone": phone,
        "page_text": page_text,
    }
    return normalize_job(job, cfg)


def inline_jobs_from_career_page(page_url, response_text, cfg):
    soup = BeautifulSoup(response_text, "html.parser")
    page_text = best_main_text(soup)
    company = extract_company_name(soup, page_url)
    logo = extract_logo(soup, page_url)
    results = []

    for heading in soup.find_all(["h2", "h3", "h4", "h5"]):
        title = clean_title(heading.get_text(" ", strip=True), company)
        if not relevant_architecture_job({"title": title, "description": "", "skills": ""}, cfg):
            continue

        # If heading links to a detail page, that page will be handled separately.
        linked = heading.find("a", href=True) or heading.find_parent("a", href=True)
        if linked:
            continue

        container = heading.find_parent(["article", "li", "section", "div"]) or heading.parent
        block_text = clean_text(container.get_text(" ", strip=True) if container else heading.get_text())
        if len(block_text) < len(title) + 10:
            continue

        apply_url, method, apply_email = find_apply_route(container if container else soup, page_url, block_text, cfg)
        if not apply_url:
            continue

        location, city, state, country = detect_location(block_text + " " + page_text[:4000])
        email, phone = extract_public_contacts(block_text)
        job = {
            "title": title,
            "company": company,
            "location": location,
            "city": city,
            "state": state,
            "country": country,
            "job_type": extract_job_type(block_text),
            "experience": extract_experience(block_text),
            "salary": extract_salary(block_text),
            "skills": extract_skills(block_text, cfg),
            "description": block_text,
            "posted_date": extract_posted_date(soup, block_text),
            "deadline": extract_deadline(soup, block_text),
            "source_type": "HTML Inline",
            "source_name": domain(page_url),
            "source_url": canonical_url(page_url),
            "apply_url": apply_url,
            "application_method": method,
            "company_website": origin(page_url),
            "logo_url": logo,
            "contact_email": email or apply_email,
            "contact_phone": phone,
            "page_text": block_text,
        }
        normalized = normalize_job(job, cfg)
        if normalized:
            results.append(normalized)
    return results


def extract_job_links(page_url, response_text, cfg):
    soup = BeautifulSoup(response_text, "html.parser")
    out = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = a.get("href", "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        full = canonical_url(urljoin(page_url, href))
        if not full or full in seen or is_blocked_domain(full, cfg) or login_gated_url(full, cfg):
            continue
        label = clean_text(a.get_text(" ", strip=True))
        parent_text = clean_text(a.parent.get_text(" ", strip=True) if a.parent else "")
        context = f"{label} {parent_text} {full}".lower()
        roleish = relevant_architecture_job({"title": label, "description": parent_text, "skills": ""}, cfg)
        pathish = any(hint in context for hint in JOB_LINK_HINTS)
        if roleish or (pathish and any(k in parent_text.lower() for k in ("architect", "designer", "bim", "planner", "visualizer"))):
            seen.add(full)
            out.append((full, label))
        if len(out) >= cfg.get("max_job_links_per_source", 30):
            break
    return out


def sitemap_candidate_urls(base_url, cfg):
    root = origin(base_url)
    if not root:
        return []
    candidates = [
        urljoin(root, "/sitemap.xml"),
        urljoin(root, "/sitemap_index.xml"),
        urljoin(root, "/job-sitemap.xml"),
        urljoin(root, "/jobs-sitemap.xml"),
    ]
    max_pages = cfg.get("max_sitemap_pages_per_source", 20)
    found = []
    visited_sitemaps = set()

    def parse_xml_urls(text):
        try:
            root_el = ET.fromstring(text)
            return [clean_text(el.text) for el in root_el.iter() if el.tag.lower().endswith("loc") and el.text]
        except ET.ParseError:
            return []

    for sm in candidates:
        if sm in visited_sitemaps:
            continue
        visited_sitemaps.add(sm)
        r = fetch(sm, cfg)
        if not r:
            continue
        urls = parse_xml_urls(r.text)
        for u in urls:
            low = u.lower()
            if low.endswith(".xml"):
                if any(k in low for k in ("job", "career", "page", "post")) and u not in visited_sitemaps:
                    visited_sitemaps.add(u)
                    rr = fetch(u, cfg)
                    if rr:
                        for child in parse_xml_urls(rr.text):
                            clow = child.lower()
                            if any(k in clow for k in JOB_LINK_HINTS):
                                found.append(child)
                                if len(found) >= max_pages:
                                    return found
            elif any(k in low for k in JOB_LINK_HINTS):
                found.append(u)
                if len(found) >= max_pages:
                    return found
    return found


def parse_page_for_jobs(page_url, cfg, title_hint=""):
    r = fetch(page_url, cfg)
    if not r:
        return [], None
    soup = BeautifulSoup(r.text, "html.parser")
    page_text = best_main_text(soup)
    jobs = []
    for obj in jsonld_objects(soup):
        j = job_from_jsonld(obj, r.url, soup, page_text, cfg)
        if j:
            jobs.append(j)
    if not jobs:
        j = html_page_job(r.url, r.text, cfg, title_hint=title_hint)
        if j:
            jobs.append(j)
    return jobs, r


def scan_career_source(start_url, cfg):
    source_jobs = []
    r = fetch(start_url, cfg)
    if not r:
        return source_jobs, {"source": start_url, "ok": False, "reason": "Source unreachable"}

    soup = BeautifulSoup(r.text, "html.parser")
    page_text = best_main_text(soup)

    # Structured jobs directly on careers page.
    for obj in jsonld_objects(soup):
        j = job_from_jsonld(obj, r.url, soup, page_text, cfg)
        if j:
            source_jobs.append(j)

    # Ordinary HTML job cards directly on careers page.
    source_jobs.extend(inline_jobs_from_career_page(r.url, r.text, cfg))

    # Detail links from page + relevant sitemap URLs.
    links = extract_job_links(r.url, r.text, cfg)
    sitemap_links = [(u, "") for u in sitemap_candidate_urls(r.url, cfg)]
    combined = []
    seen = {canonical_url(r.url)}
    for item in links + sitemap_links:
        u = canonical_url(item[0])
        if u and u not in seen and not is_blocked_domain(u, cfg):
            seen.add(u)
            combined.append((u, item[1]))
        if len(combined) >= cfg.get("max_job_links_per_source", 30):
            break

    for url, hint in combined:
        jobs, _ = parse_page_for_jobs(url, cfg, title_hint=hint)
        source_jobs.extend(jobs)
        time.sleep(cfg.get("request_delay_seconds", 0.15))

    return source_jobs, {
        "source": start_url,
        "ok": True,
        "detail_pages_checked": len(combined),
        "qualified_jobs": len(source_jobs),
    }


def fetch_lever(site, cfg):
    url = f"https://api.lever.co/v0/postings/{site}?mode=json"
    r = fetch(url, cfg)
    if not r:
        return []
    try:
        rows = r.json()
    except Exception:
        return []
    out = []
    for x in rows:
        cats = x.get("categories") or {}
        loc = clean_text(cats.get("location"))
        description = " ".join(clean_text(x.get(k)) for k in ("descriptionPlain", "additionalPlain") if x.get(k))
        location, city, state, country = detect_location(loc + " " + description)
        apply_url = canonical_url(x.get("hostedUrl") or x.get("applyUrl"))
        job = {
            "title": clean_text(x.get("text")),
            "company": site.replace("-", " ").title(),
            "location": location or loc,
            "city": city,
            "state": state,
            "country": country,
            "job_type": clean_text(cats.get("commitment")),
            "experience": "",
            "salary": "",
            "skills": clean_text(cats.get("team")),
            "description": description,
            "posted_date": None,
            "deadline": None,
            "source_type": "Lever API",
            "source_name": "Lever",
            "source_url": apply_url,
            "apply_url": apply_url,
            "application_method": "Public ATS",
            "company_website": "",
            "logo_url": "",
            "contact_email": "",
            "contact_phone": "",
            "page_text": description + " apply now",
        }
        n = normalize_job(job, cfg)
        if n:
            out.append(n)
    return out


def fetch_greenhouse(board, cfg):
    url = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"
    r = fetch(url, cfg)
    if not r:
        return []
    try:
        rows = r.json().get("jobs", [])
    except Exception:
        return []
    out = []
    for x in rows:
        loc = clean_text((x.get("location") or {}).get("name"))
        description = clean_text(x.get("content"))
        location, city, state, country = detect_location(loc + " " + description)
        apply_url = canonical_url(x.get("absolute_url"))
        job = {
            "title": clean_text(x.get("title")),
            "company": board.replace("-", " ").title(),
            "location": location or loc,
            "city": city,
            "state": state,
            "country": country,
            "job_type": "",
            "experience": "",
            "salary": "",
            "skills": "",
            "description": description,
            "posted_date": parse_date(x.get("updated_at")),
            "deadline": None,
            "source_type": "Greenhouse API",
            "source_name": "Greenhouse",
            "source_url": apply_url,
            "apply_url": apply_url,
            "application_method": "Public ATS",
            "company_website": "",
            "logo_url": "",
            "contact_email": "",
            "contact_phone": "",
            "page_text": description + " apply now",
        }
        n = normalize_job(job, cfg)
        if n:
            out.append(n)
    return out


def dedupe(jobs):
    best = {}
    for job in jobs:
        fp = job.get("fingerprint") or fingerprint(job)
        job["fingerprint"] = fp
        score = sum(
            bool(job.get(k))
            for k in (
                "description", "posted_date", "deadline", "experience", "salary", "skills", "logo_url", "contact_email"
            )
        )
        if fp not in best or score > best[fp][0]:
            best[fp] = (score, job)
    return [v[1] for v in best.values()]


def scan_all_sources(cfg):
    jobs = []
    reports = []

    for site in cfg.get("lever_sites", []):
        jobs.extend(fetch_lever(site, cfg))
    for board in cfg.get("greenhouse_boards", []):
        jobs.extend(fetch_greenhouse(board, cfg))

    sources = cfg.get("career_pages", [])
    workers = max(1, min(int(cfg.get("source_workers", 6)), 12))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(scan_career_source, source, cfg): source for source in sources}
        for fut in as_completed(futures):
            source = futures[fut]
            try:
                source_jobs, report = fut.result()
                jobs.extend(source_jobs)
                reports.append(report)
                print(f"SOURCE {'OK' if report['ok'] else 'FAIL'} | {source} | jobs={report.get('qualified_jobs', 0)}")
            except Exception as e:
                reports.append({"source": source, "ok": False, "reason": str(e)})
                print(f"SOURCE ERROR | {source} | {e}")
    return dedupe(jobs), reports


def sheet_service():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if not raw:
        raise RuntimeError("Missing GitHub secret GOOGLE_SERVICE_ACCOUNT_JSON")
    try:
        info = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON") from e
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def column_letter(n):
    s = ""
    while n:
        n, rem = divmod(n - 1, 26)
        s = chr(65 + rem) + s
    return s


def read_sheet_values(service, sheet_id, tab):
    return service.spreadsheets().values().get(
        spreadsheetId=sheet_id, range=f"'{tab}'!A:ZZ"
    ).execute().get("values", [])


def ensure_headers(service, sheet_id, tab):
    values = read_sheet_values(service, sheet_id, tab)
    if not values:
        service.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range=f"'{tab}'!A1",
            valueInputOption="RAW",
            body={"values": [V2_HEADERS]},
        ).execute()
        return V2_HEADERS[:]

    headers = values[0][:]
    missing = [h for h in V2_HEADERS if h not in headers]
    if missing:
        start = len(headers) + 1
        end = start + len(missing) - 1
        service.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range=f"'{tab}'!{column_letter(start)}1:{column_letter(end)}1",
            valueInputOption="RAW",
            body={"values": [missing]},
        ).execute()
        headers.extend(missing)
    return headers


def row_to_record(headers, row):
    return {headers[i]: row[i] if i < len(row) else "" for i in range(len(headers))}


def record_to_row(headers, record, existing_row=None):
    row = list(existing_row or [])
    if len(row) < len(headers):
        row.extend([""] * (len(headers) - len(row)))
    for key, value in record.items():
        if key in headers:
            row[headers.index(key)] = value if value is not None else ""
    return row


def job_to_record(job, first_seen=None):
    t = now_iso()
    fp = job["fingerprint"]
    return {
        "Job ID": job_id(fp),
        "Job Title": job.get("title", ""),
        "Company": job.get("company", ""),
        "Location": job.get("location", ""),
        "City": job.get("city", ""),
        "State": job.get("state", ""),
        "Country": job.get("country", "India"),
        "Job Category": job.get("job_category", ""),
        "Job Type": job.get("job_type", ""),
        "Experience": job.get("experience", ""),
        "Salary": job.get("salary", ""),
        "Skills": job.get("skills", ""),
        "Short Description": (job.get("description", "") or "")[:500],
        "Full Description": job.get("description", ""),
        "Posted Date": job.get("posted_date", ""),
        "Application Deadline": job.get("deadline", ""),
        "Freshness": job.get("freshness", ""),
        "Application Status": job.get("application_status", "Open"),
        "Application Method": job.get("application_method", ""),
        "Source Type": job.get("source_type", ""),
        "Source Name": job.get("source_name", ""),
        "Source URL": job.get("source_url", ""),
        "Apply URL": job.get("apply_url", ""),
        "Company Website": job.get("company_website", ""),
        "Logo URL": job.get("logo_url", ""),
        "Public Contact Email": job.get("contact_email", ""),
        "Public Contact Phone": job.get("contact_phone", ""),
        "First Seen": first_seen or t,
        "Last Seen": t,
        "Verified At": job.get("verified_at", t),
        "Status": "Active",
        "Closed Reason": "",
        "Fingerprint": fp,
    }


def parse_existing_date(value):
    return parse_date(value)


def revalidate_existing_record(record, cfg):
    cutoff = minimum_date(cfg)
    posted = parse_existing_date(record.get("Posted Date"))
    deadline = parse_existing_date(record.get("Application Deadline") or record.get("Valid Through"))

    if posted and posted < cutoff:
        return False, f"Posted before cutoff {cutoff.isoformat()}"
    if deadline and deadline < now_ist().date():
        return False, f"Application deadline passed: {deadline.isoformat()}"

    url = record.get("Apply URL") or record.get("Source URL")
    if not url:
        return False, "No application/source URL"
    if url.lower().startswith("mailto:"):
        return True, ""
    if is_blocked_domain(url, cfg) or login_gated_url(url, cfg):
        return False, "Blocked or login-gated application URL"

    r = fetch(url, cfg)
    if not r:
        return False, "Application page is no longer publicly reachable"
    text = best_main_text(BeautifulSoup(r.text, "html.parser"))
    reason = page_closed_reason(text, deadline, cfg)
    if reason:
        return False, reason

    title = (record.get("Job Title") or "").strip()
    # Generic career pages should still contain a recognizable role title.
    if title and url == (record.get("Source URL") or "") and len(title) < 180:
        title_core = re.sub(r"[^a-z0-9 ]+", " ", title.lower())
        title_core = re.sub(r"\s+", " ", title_core).strip()
        page_low = re.sub(r"\s+", " ", text.lower())
        significant = [w for w in title_core.split() if len(w) >= 4 and w not in ("career", "careers", "designs", "architecture")]
        if significant and not all(w in page_low for w in significant[:3]):
            return False, "Role title is no longer visible on the source page"
    return True, ""


def write_sheet(jobs, cfg):
    sheet_id = os.environ.get("GOOGLE_SHEET_ID", "")
    tab = os.environ.get("GOOGLE_SHEET_TAB", "Jobs")
    if not sheet_id:
        raise RuntimeError("Missing GitHub secret GOOGLE_SHEET_ID")

    svc = sheet_service()
    headers = ensure_headers(svc, sheet_id, tab)
    values = read_sheet_values(svc, sheet_id, tab)
    rows = values[1:] if len(values) > 1 else []

    existing_by_fp = {}
    for idx, row in enumerate(rows, start=2):
        rec = row_to_record(headers, row)
        fp = rec.get("Fingerprint")
        if fp:
            existing_by_fp[fp] = (idx, row, rec)

    current_fps = {j["fingerprint"] for j in jobs}
    new_rows = []
    updates = []
    new_count = 0
    updated_count = 0
    closed_count = 0

    # Revalidate existing active rows that were not rediscovered this run.
    if cfg.get("revalidate_existing_jobs", True):
        limit = int(cfg.get("revalidate_limit_per_run", 250))
        checked = 0
        for fp, (row_num, old_row, rec) in existing_by_fp.items():
            if checked >= limit:
                break
            if fp in current_fps or (rec.get("Status") or "").lower() != "active":
                continue
            checked += 1
            is_open, reason = revalidate_existing_record(rec, cfg)
            patch = {
                "Verified At": now_iso(),
                "Last Seen": rec.get("Last Seen", ""),
            }
            if not is_open:
                patch.update({
                    "Application Status": "Closed",
                    "Status": "Closed",
                    "Closed Reason": reason,
                })
                closed_count += 1
            else:
                patch.update({"Application Status": "Open", "Closed Reason": ""})
            merged = record_to_row(headers, patch, old_row)
            updates.append({
                "range": f"'{tab}'!A{row_num}:{column_letter(len(headers))}{row_num}",
                "values": [merged],
            })

    # Upsert currently discovered jobs.
    for job in jobs:
        fp = job["fingerprint"]
        if fp in existing_by_fp:
            row_num, old_row, rec = existing_by_fp[fp]
            record = job_to_record(job, first_seen=rec.get("First Seen") or now_iso())
            merged = record_to_row(headers, record, old_row)
            updates.append({
                "range": f"'{tab}'!A{row_num}:{column_letter(len(headers))}{row_num}",
                "values": [merged],
            })
            updated_count += 1
        else:
            record = job_to_record(job)
            new_rows.append(record_to_row(headers, record))
            new_count += 1

    if new_rows:
        svc.spreadsheets().values().append(
            spreadsheetId=sheet_id,
            range=f"'{tab}'!A:{column_letter(len(headers))}",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": new_rows},
        ).execute()

    if updates:
        svc.spreadsheets().values().batchUpdate(
            spreadsheetId=sheet_id,
            body={"valueInputOption": "RAW", "data": updates},
        ).execute()

    return new_count, updated_count, closed_count


def load_config():
    with open("config.yaml", "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    minimum_date(cfg)
    return cfg


def self_test():
    cfg = load_config()
    cutoff = minimum_date(cfg)
    assert cutoff == date(2026, 9, 1)

    base = {
        "title": "Junior Architect",
        "company": "Test Studio",
        "location": "Mumbai, Maharashtra, India",
        "city": "Mumbai",
        "state": "Maharashtra",
        "country": "India",
        "description": "We are hiring a Junior Architect. Apply now. Revit AutoCAD.",
        "skills": "",
        "apply_url": "mailto:careers@example.com",
        "application_method": "Email",
        "source_url": "https://example.com/careers",
        "source_type": "HTML",
        "source_name": "example.com",
        "company_website": "https://example.com",
        "logo_url": "",
        "contact_email": "careers@example.com",
        "contact_phone": "",
        "page_text": "We are hiring a Junior Architect. Apply now. careers@example.com",
    }

    old = dict(base, posted_date=date(2026, 8, 31), deadline=None)
    assert normalize_job(old, cfg) is None, "Pre-September dated job must be rejected"

    fresh = dict(base, posted_date=date(2026, 9, 5), deadline=date(2026, 12, 31))
    assert normalize_job(fresh, cfg) is not None, "Fresh open job should be accepted"

    undated = dict(base, posted_date=None, deadline=None)
    assert normalize_job(undated, cfg) is not None, "Undated but clearly live/applyable job should be accepted"

    closed = dict(base, posted_date=date(2026, 9, 5), deadline=None, page_text="This job has expired. Apply now.")
    assert normalize_job(closed, cfg) is None, "Closed signal must override freshness"

    print("SELF TEST PASSED: cutoff, fresh, undated-live and closed-job rules are working.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        return

    cfg = load_config()
    jobs, reports = scan_all_sources(cfg)
    print("=" * 80)
    print(f"V2 cutoff date: {minimum_date(cfg).isoformat()}")
    print(f"Sources attempted: {len(reports)}")
    print(f"Qualified OPEN Indian architecture jobs this run: {len(jobs)}")
    print("=" * 80)

    if os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes"):
        print(json.dumps(jobs[:20], ensure_ascii=False, indent=2))
        return

    new_count, updated_count, closed_count = write_sheet(jobs, cfg)
    print(f"Google Sheet: {new_count} new, {updated_count} refreshed, {closed_count} closed/stale")


if __name__ == "__main__":
    main()
