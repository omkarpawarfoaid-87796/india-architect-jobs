import argparse
import hashlib
import html
import json
import os
import re
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone, timedelta
from urllib.parse import urljoin, urlparse, urldefrag, quote, unquote
from zoneinfo import ZoneInfo
import xml.etree.ElementTree as ET

import requests
import yaml
from bs4 import BeautifulSoup, MarkupResemblesLocatorWarning
from bs4.element import NavigableString, Tag
from dateutil import parser as dateparser

warnings.filterwarnings("ignore", category=MarkupResemblesLocatorWarning)

IST = ZoneInfo("Asia/Kolkata")
USER_AGENT = (
    "ArchitectJobsCollector/4.4.6 "
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

# Exact website/import schema supplied by the web developer.
# This is the ONLY schema written to the public/export Google Sheet tab.
WEBSITE_HEADERS = [
    "external_id",
    "title",
    "description",
    "status",
    "employer_author",
    "employer_email",
    "employer_name",
    "expiry_date",
    "application_deadline_date",
    "featured",
    "urgent",
    "filled",
    "apply_type",
    "apply_url",
    "apply_email",
    "phone",
    "salary",
    "max_salary",
    "salary_type",
    "address",
    "location",
    "category",
    "type",
    "tag",
    "experience",
    "gender",
    "industry",
    "qualification",
    "career_level",
    "video_url",
    "logo_url",
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
    (
        "Visualization",
        [
            "3d visualizer",
            "3d visualiser",
            "visualizer",
            "visualiser",
            "visualisation",
            "visualization",
            "3d render artist",
            "render artist",
            "rendering artist",
            "architectural renderer",
            "architectural rendering",
        ],
    ),
    ("Architecture", ["architect", "architecture", "architectural"]),
]

JOB_LINK_HINTS = (
    "job", "jobs", "career", "careers", "opening", "openings", "vacancy", "vacancies",
    "position", "positions", "opportunity", "opportunities", "recruit"
)

# Clean display names for seed employers whose metadata/domain names are compressed.
# Unknown employers still fall back to their own Organization metadata or domain name.
COMPANY_NAME_OVERRIDES = {
    "abhikalpan.in": "Abhikalpan",
    "uha.global": "UHA",
    "hingooarchitects.com": "Hingoo Architects",
    "daisaria.com": "Daisaria Associates",
    "p-cdesigns.com": "P-C Designs",
    "morphogenesis.org": "Morphogenesis",
    "dhapl.in": "DHAPL",
    "shreedesigns.in": "Shree Designs",
    "studio-cplusc.com": "Studio C+P",
    "apices.in": "Apices",
    "hafeezcontractor.com": "Architect Hafeez Contractor",
    "architecturebrio.com": "Architecture BRIO",
    "studiolotus.in": "Studio Lotus",
    "abindesignstudio.com": "Abin Design Studio",
}



def now_ist():
    return datetime.now(IST)


def now_iso():
    return now_ist().isoformat(timespec="seconds")



def _repair_mojibake(value):
    """Repair common UTF-8 text that was accidentally decoded as Windows-1252."""
    if not isinstance(value, str) or not value:
        return value
    out = value
    suspicious = ("â€", "â€™", "â€œ", "â€�", "â€“", "â€”", "Ã", "Â", "ðŸ")
    for _ in range(2):
        if not any(marker in out for marker in suspicious):
            break
        try:
            repaired = out.encode("cp1252").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            break
        if repaired == out:
            break
        out = repaired
    replacements = {
        "â€™": "’", "â€˜": "‘", "â€œ": "“", "â€�": "”",
        "â€“": "–", "â€”": "—", "Â·": "·", "Â": "",
    }
    for bad, good in replacements.items():
        out = out.replace(bad, good)
    return out


def clean_text(value):
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(x for x in (clean_text(v) for v in value) if x)
    if isinstance(value, dict):
        return clean_text(value.get("name") or value.get("value") or "")
    raw = _repair_mojibake(str(value))
    cleaned = BeautifulSoup(raw, "html.parser").get_text(" ", strip=True)
    cleaned = _repair_mojibake(html.unescape(cleaned))
    return re.sub(r"\s+", " ", cleaned.replace("\xa0", " ")).strip()


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


def company_name_from_domain(url):
    """
    Create a readable fallback company name from a public website domain.

    This is used only when seeding the Sources registry before a company page
    has been inspected. Discovery can later replace/augment this with the
    actual site name.

    Examples:
      hingooarchitects.com   -> Hingoo Architects
      shreedesigns.in        -> Shree Designs
      hafeezcontractor.com   -> Hafeez Contractor
    """
    host = domain(url).lower()
    if not host:
        return ""

    host = re.sub(r"^www\.", "", host)

    # Drop common public suffixes. The exact company name is only a fallback,
    # so we intentionally keep this dependency-free.
    stem = host.split(".")[0]
    stem = re.sub(r"[-_]+", " ", stem).strip()

    # Split common architecture/design/business suffix words that are usually
    # concatenated in domains.
    suffixes = [
        "architects",
        "architecture",
        "architect",
        "contractor",
        "contractors",
        "designs",
        "design",
        "studio",
        "studios",
        "associates",
        "consultants",
        "consulting",
        "interiors",
        "interior",
        "landscape",
        "planning",
        "planners",
    ]

    # Repeat because a domain can contain more than one recognizable token.
    for _ in range(3):
        previous = stem
        for word in suffixes:
            stem = re.sub(
                rf"(?i)([a-z0-9])({re.escape(word)})$",
                r"\1 \2",
                stem,
            )
        if stem == previous:
            break

    stem = re.sub(r"\s+", " ", stem).strip()
    if not stem:
        return host

    # Keep common acronyms readable.
    words = []
    for word in stem.split():
        if len(word) <= 4 and word.isupper():
            words.append(word)
        else:
            words.append(word[:1].upper() + word[1:])
    return " ".join(words)


CDN_MIRROR_SUFFIXES = (
    "global.ssl.fastly.net",
    "fastly.net",
    "cloudfront.net",
    "akamaized.net",
    "azureedge.net",
    "netlify.app",
    "workers.dev",
    "pages.dev",
    "vercel.app",
)


def hostname_aliases(host):
    """Generate hostname variants to catch CDN/proxy aliases of blocked sites."""
    host = (host or "").lower().strip(".")
    if not host:
        return set()
    aliases = {host}
    aliases.add(host.replace("-", "."))
    aliases.add(host.replace("_", "."))
    aliases.add(host.replace("-", ".").replace("_", "."))
    return {a.strip(".") for a in aliases if a}


def blocked_domain_reason(url, cfg):
    """Return a readable block reason, including CDN/mirror aliases."""
    d = domain(url)
    if not d:
        return ""

    blocked = set(clean_text(x).lower().removeprefix("www.") for x in cfg.get("blocked_domains", []))
    blocked |= set(clean_text(x).lower().removeprefix("www.") for x in cfg.get("platform_blocked_domains", []))
    blocked = {x for x in blocked if x}

    aliases = hostname_aliases(d)
    for candidate in aliases:
        stripped = candidate.removeprefix("www.")
        for blocked_domain in blocked:
            bd = blocked_domain.removeprefix("www.")
            if stripped == bd or stripped.endswith("." + bd):
                return f"Blocked platform/domain: {blocked_domain}"

            if any(stripped.endswith("." + suffix) or stripped == suffix for suffix in CDN_MIRROR_SUFFIXES):
                if bd in stripped:
                    return f"Blocked CDN/mirror alias of: {blocked_domain}"

    return ""


def is_blocked_domain(url, cfg):
    return bool(blocked_domain_reason(url, cfg))


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
            timeout=int(cfg.get("request_timeout_seconds", 8)),
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



def _email_priority(email):
    local = (email or "").split("@", 1)[0].lower()
    if any(k in local for k in ("careers", "career", "jobs", "recruit", "talent", "hiring")):
        return 0
    if local in {"hr", "humanresources", "human.resources"} or local.startswith("hr.") or local.startswith("hr-"):
        return 1
    if any(k in local for k in ("people", "team")):
        return 2
    if any(k in local for k in ("hello", "contact", "info", "office")):
        return 5
    if any(k in local for k in ("press", "media", "exec", "admin")):
        return 9
    return 6


def _best_email(emails):
    unique = []
    seen = set()
    for email in emails or []:
        email = (email or "").strip().lower()
        if not email or email in seen:
            continue
        seen.add(email)
        unique.append(email)
    if not unique:
        return ""
    unique.sort(key=lambda e: (_email_priority(e), len(e), e))
    return unique[0]


def extract_public_contacts(text):
    text = clean_text(text or "")
    emails = re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)
    emails = [
        e for e in emails
        if not e.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".svg"))
    ]

    compact = re.sub(r"[\s().-]+", "", text)
    phones = re.findall(r"(?:\+91)?[6-9]\d{9}", compact)
    phone = phones[0] if phones else ""
    return _best_email(emails), phone


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
    """Return employment type only when the source states it as a job type.

    This intentionally avoids treating phrases such as "previous internship
    experience" or a company name containing "Contractor" as employment type.
    """
    value = clean_text(text or "")
    low = value.lower()

    labels = re.findall(
        r"(?:job|employment|position)\s*type\s*[:\-]?\s*([^.;|]{1,80})",
        value,
        flags=re.I,
    )
    search_space = " ".join(labels) if labels else ""

    # Strong explicit phrases can be accepted even without a label.
    explicit = []
    if re.search(r"\bfull[-\s]?time\s+(?:role|position|job|opening)\b", low):
        explicit.append("Full Time")
    if re.search(r"\bpart[-\s]?time\s+(?:role|position|job|opening)\b", low):
        explicit.append("Part Time")
    if re.search(r"\b(?:contract|contractual|freelance)\s+(?:role|position|job|opening)\b", low):
        explicit.append("Contract")
    if re.search(r"\bintern(?:ship)?\s+(?:role|position|job|opening)\b", low):
        explicit.append("Internship")

    found = list(explicit)
    target = search_space.lower()
    if target:
        mapping = [
            ("Internship", r"\bintern(?:ship)?\b"),
            ("Part Time", r"\bpart[-\s]?time\b"),
            ("Contract", r"\b(?:contract|contractual|freelance)\b"),
            ("Full Time", r"\b(?:full[-\s]?time|permanent)\b"),
        ]
        for label, pattern in mapping:
            if re.search(pattern, target, re.I) and label not in found:
                found.append(label)
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


def _image_candidate_url(tag, page_url):
    """Return the best URL exposed by an <img> element."""
    srcset = tag.get("srcset") or tag.get("data-srcset") or ""
    if srcset:
        items = []
        for part in srcset.split(","):
            bits = part.strip().split()
            if not bits:
                continue
            url = bits[0]
            weight = 0
            if len(bits) > 1:
                m = re.match(r"([\d.]+)(w|x)$", bits[1], re.I)
                if m:
                    weight = float(m.group(1)) * (1000 if m.group(2).lower() == "x" else 1)
            items.append((weight, url))
        if items:
            items.sort(key=lambda x: x[0])
            return canonical_url(urljoin(page_url, items[-1][1]))

    for attr in ("data-src", "data-lazy-src", "data-original", "src"):
        value = tag.get(attr)
        if value and not str(value).lower().startswith("data:"):
            return canonical_url(urljoin(page_url, str(value)))
    return ""


def _upgrade_logo_url(url):
    """Remove obvious thumbnail transforms when the original asset URL is recoverable."""
    if not url:
        return ""
    # Zyro/Cloudflare image transform used by one of the seed sites. A transformed
    # w=16,h=16 URL is useless on a job card; the underlying path is higher quality.
    url = re.sub(r"/cdn-cgi/image/[^/]+/", "/", url, flags=re.I)
    return canonical_url(url)


def _logo_score(url, label_text="", in_header=False, width=None, height=None, base=0):
    low_url = (url or "").lower()
    low_label = (label_text or "").lower()
    score = base
    if "logo" in low_url:
        score += 22
    if any(k in low_label for k in ("logo", "brand", "company")):
        score += 35
    if in_header:
        score += 18
    if low_url.endswith(".svg"):
        score += 12
    if "favicon" in low_url or "apple-touch-icon" in low_url:
        score -= 55
    if re.search(r"(?:^|[,?&_/])w=?(?:16|24|32)(?:[,?&_/]|$)", low_url):
        score -= 45
    if re.search(r"(?:^|[,?&_/])h=?(?:16|24|32)(?:[,?&_/]|$)", low_url):
        score -= 45
    try:
        if width and int(re.sub(r"\D", "", str(width)) or 0) <= 40:
            score -= 30
        if height and int(re.sub(r"\D", "", str(height)) or 0) <= 40:
            score -= 30
    except Exception:
        pass
    return score



def extract_logo(soup, page_url):
    """Prefer a genuine company logo and never use a generic hero/building image."""
    strong = []
    fallback_icons = []

    for obj in jsonld_objects(soup):
        if type_contains(obj, "Organization"):
            logo = obj.get("logo")
            if isinstance(logo, dict):
                logo = logo.get("url") or logo.get("contentUrl")
            if logo:
                u = canonical_url(urljoin(page_url, str(logo)))
                strong.append((_logo_score(u, "organization logo", base=35), u))

    for img in soup.find_all("img"):
        u = _image_candidate_url(img, page_url)
        if not u:
            continue
        classes = " ".join(img.get("class") or [])
        label = " ".join(
            str(x) for x in (img.get("alt", ""), img.get("title", ""), img.get("id", ""), classes) if x
        )
        low_url = u.lower()
        low_label = label.lower()
        in_header = bool(img.find_parent(["header", "nav"]))
        logo_like = (
            "logo" in low_url
            or any(k in low_label for k in ("logo", "brand", "company logo"))
        )
        if not logo_like:
            # Do not let architectural/project hero photography become the company logo.
            continue
        score = _logo_score(
            u, label, in_header=in_header,
            width=img.get("width"), height=img.get("height"), base=15,
        )
        strong.append((score, u))

    for link in soup.find_all("link"):
        rel = link.get("rel") or []
        rel_text = " ".join(rel if isinstance(rel, list) else [str(rel)]).lower()
        if "icon" in rel_text and link.get("href"):
            u = canonical_url(urljoin(page_url, link["href"]))
            fallback_icons.append(u)

    if strong:
        strong.sort(key=lambda x: x[0], reverse=True)
        return _upgrade_logo_url(strong[0][1])
    return _upgrade_logo_url(fallback_icons[0]) if fallback_icons else ""


def _normalise_company_display(name):
    name = clean_text(name)
    if not name:
        return ""
    # Gentle cleanup only; do not invent words for unknown employers.
    name = re.sub(r"\s+", " ", name).strip(" -|,")
    return name


def extract_company_name(soup, page_url):
    host = domain(page_url).lower()
    host_no_www = host[4:] if host.startswith("www.") else host
    if host_no_www in COMPANY_NAME_OVERRIDES:
        return COMPANY_NAME_OVERRIDES[host_no_www]

    for obj in jsonld_objects(soup):
        if type_contains(obj, "Organization") and obj.get("name"):
            return _normalise_company_display(obj.get("name"))
    meta = soup.find("meta", attrs={"property": "og:site_name"})
    if meta and meta.get("content"):
        return _normalise_company_display(meta["content"])
    d = host_no_www.split(".")
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
    text = re.sub(r"\s+", " ", clean_text(text or " "))
    # Longer labels first so "deadline for applications" wins over "deadline".
    labels = sorted(labels, key=len, reverse=True)
    label_pattern = "|".join(re.escape(x) for x in labels)
    month = (
        r"Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|"
        r"Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|"
        r"Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?"
    )
    day = r"\d{1,2}\s*(?:st|nd|rd|th)?"
    date_patterns = [
        r"\d{4}-\d{1,2}-\d{1,2}",
        r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}",
        rf"{day}\s+(?:{month})\s+\d{{4}}",
        rf"(?:{month})\s+{day},?\s+\d{{4}}",
    ]
    dp = "(?:" + "|".join(date_patterns) + ")"
    m = re.search(rf"(?:{label_pattern})\s*[:\-]?\s*({dp})", text, re.I)
    if not m:
        return None
    raw = re.sub(r"(?<=\d)\s*(?:st|nd|rd|th)\b", "", m.group(1), flags=re.I)
    # dateutil understands Sep/Sept/September; normalize the odd abbreviation too.
    raw = re.sub(r"\bSept\b", "Sep", raw, flags=re.I)
    return parse_date(raw)


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
        [
            "deadline for applications",
            "deadline for application",
            "application deadline",
            "applications closing",
            "applications close",
            "last date to apply",
            "apply by",
            "last date",
            "closing date",
            "deadline",
        ],
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


GENERIC_CAREER_PATHS = {
    "career", "careers", "jobs", "job", "openings", "opening",
    "open-positions", "open-position", "opportunities", "opportunity",
    "join-us", "work-with-us", "vacancies", "vacancy",
}


def is_generic_careers_url(url):
    """True for a generic careers/jobs landing page rather than a role page."""
    if not url:
        return True
    path = (urlparse(url).path or "").strip("/").lower()
    if not path:
        return True
    parts = [p for p in path.split("/") if p]
    if not parts:
        return True
    last = parts[-1]
    return last in GENERIC_CAREER_PATHS


def _undated_detail_markers(description):
    """Count job-specific detail markers in the vacancy text."""
    low = (description or "").lower()
    markers = (
        "experience", "qualification", "qualifications", "requirements",
        "responsibilities", "responsibility", "skills", "skill",
        "b.arch", "bachelor", "degree", "diploma", "portfolio",
        "proficient", "proficiency", "years", "yrs",
        "job type", "location", "role", "duties",
    )
    return sum(1 for marker in markers if marker in low)


def validate_undated_live_evidence(job, cfg):
    """
    V2.3 false-positive guard for jobs with no posted date.

    A generic careers-page heading is NOT enough. An undated vacancy must show
    role-specific evidence such as a substantial description/requirements,
    experience/skills, a dedicated role page, a public form/email, or a future
    application deadline.
    """
    job = job or {}
    description = clean_text(job.get("description", ""))
    page_text = clean_text(job.get("page_text", ""))
    source_type = clean_text(job.get("source_type", "")).lower()
    source_url = canonical_url(job.get("source_url", ""))
    apply_url = canonical_url(job.get("apply_url", ""))
    method = clean_text(job.get("application_method", ""))
    experience = clean_text(job.get("experience", ""))
    skills = clean_text(job.get("skills", ""))
    deadline = job.get("deadline")
    if not isinstance(deadline, date):
        deadline = parse_date(deadline)

    has_apply = bool(apply_url or method)
    if not has_apply:
        return False, "Undated vacancy has no public application route", 0

    # A future application deadline is itself strong current-vacancy evidence.
    if deadline and deadline >= now_ist().date():
        return True, "", 10

    desc_len = len(description)
    marker_count = _undated_detail_markers(description)
    dedicated_source = bool(source_url) and not is_generic_careers_url(source_url)
    same_apply_page = bool(source_url and apply_url and source_url == apply_url)
    structured_source = any(
        token in source_type for token in ("json", "api", "ats", "lever", "greenhouse")
    )
    role_specific_open = page_has_open_signal(description, cfg)
    page_open = page_has_open_signal(page_text, cfg)

    score = 0
    if structured_source:
        score += 5
    if dedicated_source:
        score += 4
    if desc_len >= 120:
        score += 2
    if desc_len >= 300:
        score += 1
    if marker_count >= 1:
        score += 2
    if marker_count >= 2:
        score += 1
    if experience:
        score += 2
    if skills:
        score += 1
    if method == "Public Form":
        score += 2
    elif method == "Email":
        score += 1
    elif method == "Apply Link":
        score += 1
    if apply_url and source_url and apply_url != source_url and not apply_url.startswith("mailto:"):
        score += 2
    if role_specific_open:
        score += 1

    # Hard guard: a short title/heading on a generic careers page with only a
    # generic careers Apply link is not a verified vacancy.
    min_chars = int(cfg.get("undated_min_description_chars", 100))
    min_score = int(cfg.get("undated_min_evidence_score", 6))

    weak_generic = (
        is_generic_careers_url(source_url)
        and desc_len < min_chars
        and not experience
        and not skills
        and marker_count == 0
    )
    if weak_generic:
        return (
            False,
            "Insufficient evidence of current vacancy: generic careers-page heading only",
            score,
        )

    # Generic careers-page rows need actual role content, not just page-level
    # "Open Positions" text. Public Form is strong, but still requires job detail.
    if is_generic_careers_url(source_url) and not structured_source:
        has_role_detail = (
            desc_len >= min_chars
            and (marker_count >= 1 or bool(experience) or bool(skills))
        )
        if not has_role_detail:
            return (
                False,
                "Insufficient role-specific details for undated careers-page vacancy",
                score,
            )

        if method == "Apply Link" and same_apply_page and score < (min_score + 1):
            return (
                False,
                "Generic careers-page Apply link is not enough to verify an undated vacancy",
                score,
            )

    # Dedicated role pages are allowed with slightly lower evidence because the
    # dedicated URL itself is strong. Generic pages use the normal threshold.
    threshold = max(4, min_score - 1) if dedicated_source else min_score
    if score < threshold:
        return (
            False,
            f"Insufficient live-vacancy evidence for undated job (score {score}/{threshold})",
            score,
        )

    if not (page_open or role_specific_open or structured_source or dedicated_source):
        return False, "No current-opening signal for undated vacancy", score

    return True, "", score



def find_apply_route(soup, page_url, page_text, cfg):
    """Find the best public application route without requiring authentication."""
    web_candidates = []
    mail_candidates = []

    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        label = clean_text(a.get_text(" ", strip=True)).lower()
        parent_text = clean_text(a.parent.get_text(" ", strip=True) if a.parent else "").lower()
        if href.lower().startswith("mailto:"):
            email = href.split(":", 1)[1].split("?", 1)[0].strip()
            if email:
                mail_candidates.append(email)
            continue
        if any(k in label for k in ("apply", "submit application", "send cv", "send resume")) or (
            "apply" in href.lower() and "apply" in parent_text
        ):
            full = canonical_url(urljoin(page_url, href))
            if full and not is_blocked_domain(full, cfg) and not login_gated_url(full, cfg):
                web_candidates.append(full)

    # A dedicated Apply link is the best UX when it is public.
    if web_candidates:
        return web_candidates[0], "Apply Link", ""

    # A public form is preferable to a generic email address.
    if soup.find("form") and page_has_open_signal(page_text, cfg):
        return canonical_url(page_url), "Public Form", ""

    text_email, _ = extract_public_contacts(page_text)
    if text_email:
        mail_candidates.append(text_email)
    email = _best_email(mail_candidates)
    if email and page_has_open_signal(page_text, cfg):
        return f"mailto:{email}", "Email", email

    return "", "", email


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


def validate_freshness(
    posted_date,
    deadline,
    page_text,
    apply_url,
    application_method,
    cfg,
    job=None,
):
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

    # No usable posted date: V2.3 requires strong role-specific live evidence.
    if cfg.get("accept_undated_if_live_verified", True):
        candidate = dict(job or {})
        candidate.setdefault("page_text", page_text)
        candidate.setdefault("apply_url", apply_url)
        candidate.setdefault("application_method", application_method)
        candidate.setdefault("deadline", deadline)

        strong, strong_reason, score = validate_undated_live_evidence(candidate, cfg)
        has_open = page_has_open_signal(page_text, cfg)
        has_apply = bool(apply_url or application_method)

        if has_open and has_apply and strong:
            return True, f"Live Verified (No Posted Date, Score {score})", "", None

        if not strong:
            return False, "Unverified", strong_reason, ""

    return False, "Unverified", "No acceptable posted date and insufficient live-opening evidence", ""



def _natural_company(value):
    return re.sub(r"[^a-z0-9]+", " ", clean_text(value).lower()).strip()


def _natural_title(value):
    title = clean_text(value).lower()
    # Remove parenthetical/trailing experience annotations so careers-list and
    # detail-page versions of the same vacancy collapse to one job.
    title = re.sub(
        r"\([^)]*(?:\d+\s*(?:-|–|to)\s*\d+|\d+\+?|minimum\s+\d+)\s*(?:years?|yrs?)[^)]*\)",
        " ", title, flags=re.I,
    )
    title = re.sub(
        r"\b(?:\d+\s*(?:-|–|to)\s*\d+|\d+\+?|minimum\s+\d+)\s*(?:years?|yrs?)\s*(?:of\s+experience)?\b",
        " ", title, flags=re.I,
    )
    title = re.sub(r"\bof\s+experience\b", " ", title, flags=re.I)
    title = re.sub(r"[^a-z0-9+]+", " ", title)
    return re.sub(r"\s+", " ", title).strip()


def natural_job_key(job):
    company = _natural_company(job.get("company") or job.get("Company"))
    title = _natural_title(job.get("title") or job.get("Job Title"))
    city = re.sub(r"[^a-z0-9]+", " ", clean_text(job.get("city") or job.get("City")).lower()).strip()
    return "|".join((company, title, city))


def fingerprint(job):
    # V2.3 fingerprints represent the vacancy itself, not the page URL. This
    # prevents a careers-list row and a detail-page row becoming duplicates.
    base = natural_job_key(job)
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:24]


def job_id(fp):
    return hashlib.sha1(fp.encode("utf-8")).hexdigest()[:16]



SERVICE_LANDING_TITLE_PATTERNS = (
    r"^(?:best\s+)?(?:home\s+)?interior\s+designers?\s+in\b",
    r"^(?:top\s+)?(?:home\s+)?interior\s+designers?\s+in\b",
    r"^(?:best\s+)?(?:home\s+)?interior\s+designer\s+near\s+me\b",
    r"^(?:best\s+)?interior\s+design\s+(?:company|companies|services?)\s+in\b",
    r"^home\s+interior\s+designers?\s+in\b",
    r"^home\s+interiors?\s+in\b",
    r"^modular\s+kitchen\s+designs?\s+in\b",
    r"^wardrobe\s+designs?\s+in\b",
    r"^bedroom\s+designs?\s+in\b",
    r"^living\s+room\s+designs?\s+in\b",
    r"^bathroom\s+designs?\s+in\b",
    r"^space\s+saving\s+furniture\s+designs?\s+in\b",
    r"^home\s+office\s+designs?\s+in\b",
    r"^interior\s+decorators?\s+in\b",
    r"^interior\s+design\s+ideas?\b",
    r"^architects?\s+in\b",
    r"^architecture\s+firms?\s+in\b",
)

NON_JOB_TITLE_PATTERNS = (
    r"^(?:australia\s*&\s*new\s*zealand|americas|middle\s+east|asia|uk\s*&\s*ireland)\b",
    r"^(?:india|asia|global|international)\s+(?:early\s+careers?|graduate\s+careers?|opportunities?)\b",
    r"\b(?:graduate|graduates|early\s+careers?|student\s+opportunities)\b",
    r"^(?:find|search)\s+(?:your\s+)?(?:next\s+)?opportunit",
    r"^(?:careers?|jobs?|openings?|current\s+openings?)$",
    r"^(?:about|contact|locations?|store\s+locator|design\s+gallery)$",
)

REAL_ROLE_TITLE_TERMS = (
    "architect", "architecture", "architectural",
    "interior designer", "designer", "design manager", "design lead", "design head",
    "landscape", "urban", "planner", "bim", "revit", "cad", "autocad",
    "draftsman", "draughtsman", "drafting", "visualizer", "visualiser",
    "render", "3d", "artist", "modeler", "modeller", "site supervisor",
)

SERVICE_PAGE_URL_BITS = (
    "/interior-designers-in-", "/interior-designers/", "/interior-designers-in/",
    "/interior-designers-in", "/best-interior-designers-in",
    "/modular-kitchen-designs-in-", "/wardrobe-designs-in-",
    "/home-interiors-in-", "/interior-design-in-", "/cities/",
    "/home-interior-designers-in-",
)

SERVICE_PAGE_NOISE_TERMS = (
    "get free estimate", "book free design session", "book virtual meeting", "design gallery",
    "store locator", "customer stories", "modular kitchen cost",
    "home interior cost", "wardrobe designs", "modular kitchen designs",
    "space saving furniture", "45-day delivery", "45 day delivery",
    "10-year warranty", "10 year warranty", "easy emis", "emi options",
    "experience centre", "experience center", "home interiors across india",
    "book your order", "finalise your design", "send designs to factory",
    "visit our", "schedule visit", "floor plan", "cost calculator", "customer support",
    "refer and earn", "home interiors", "modular kitchens", "popular services",
    "popular blogs", "sitemap interior design", "experience centres", "experience centers",
)


def _matches_any_pattern(value, patterns):
    low = clean_text(value or "").lower()
    return any(re.search(pattern, low, re.I) for pattern in patterns)


def _service_noise_score(text):
    low = clean_text(text or "").lower()
    return sum(1 for term in SERVICE_PAGE_NOISE_TERMS if term in low)


def _title_has_real_role(title):
    low = clean_text(title or "").lower()
    return any(term in low for term in REAL_ROLE_TITLE_TERMS)


def real_vacancy_reject_reason(job, cfg=None):
    """
    V4.4.6 quality lock.

    Reject service/location landing pages and generic region/career collection
    pages before they can become website jobs. This specifically prevents rows
    like HomeLane "Interior Designers in Ahmedabad" and AECOM
    "Australia & New Zealand" from entering Sheet1.
    """
    title = clean_text(job.get("title") or "")
    desc = clean_text(job.get("description") or job.get("page_text") or "")
    url = canonical_url(job.get("source_url") or job.get("apply_url") or "")
    low_url = url.lower()

    if not title:
        return "Missing job title"

    if _matches_any_pattern(title, SERVICE_LANDING_TITLE_PATTERNS):
        return "Service/location landing page title, not a vacancy"

    if _matches_any_pattern(title, NON_JOB_TITLE_PATTERNS):
        return "Generic region/career collection title, not a vacancy"

    if not _title_has_real_role(title):
        return "Title is not a real architecture/design job role"

    url_service = any(bit in low_url for bit in SERVICE_PAGE_URL_BITS)
    noise_score = _service_noise_score(desc)

    if url_service and noise_score >= 3:
        return f"Service/location marketing page, not a job post (noise {noise_score})"

    if noise_score >= int((cfg or {}).get("service_page_noise_reject_score", 8)):
        # Allow genuine careers pages with multiple role headings to pass to the
        # inline parser, but reject single-page HTML jobs created from marketing pages.
        source_type = clean_text(job.get("source_type") or "").lower()
        if source_type in {"html", "json-ld", "fresh direct job page"}:
            return f"Marketing/service page text, not a vacancy (noise {noise_score})"

    # AECOM/ATS collection pages may mention India in navigation; accept only if
    # the actual title is a role, not an area/program bucket.
    if re.search(r"\b(?:australia|new\s+zealand|americas|middle\s+east|graduate\s+careers?|early\s+careers?)\b", title, re.I):
        return "ATS region/program page, not an India job posting"

    return ""



def meta_record_vacancy_reject_reason(record, cfg=None):
    """
    V4.4.6 hard cleanup for old rows already stored in _CollectorMeta.

    Earlier versions may have written service/location pages (for example
    HomeLane city pages) or ATS region bucket pages (for example AECOM
    Australia & New Zealand). This maps old meta rows back into the normal
    validator so they can be closed and removed from Sheet1.
    """
    job = {
        "title": record.get("Job Title") or record.get("title") or "",
        "company": record.get("Company") or record.get("employer_name") or "",
        "location": record.get("Location") or record.get("address") or "",
        "city": record.get("City") or "",
        "state": record.get("State") or "",
        "country": record.get("Country") or "India",
        "description": (
            record.get("Full Description")
            or record.get("Short Description")
            or record.get("description")
            or ""
        ),
        "source_url": record.get("Source URL") or record.get("apply_url") or "",
        "apply_url": record.get("Apply URL") or record.get("apply_url") or "",
        "source_type": record.get("Source Type") or "",
        "page_text": (
            record.get("Full Description")
            or record.get("Short Description")
            or record.get("description")
            or ""
        ),
    }

    reason = real_vacancy_reject_reason(job, cfg or {})
    if reason:
        return f"V4.4.6 hard cleanup: {reason}"

    company = clean_text(job["company"]).lower()
    title = clean_text(job["title"]).lower()
    desc = clean_text(job["description"]).lower()
    url = canonical_url(job["source_url"] or job["apply_url"]).lower()

    if "homelane" in company and (
        _matches_any_pattern(title, SERVICE_LANDING_TITLE_PATTERNS)
        or any(bit in url for bit in SERVICE_PAGE_URL_BITS)
        or _service_noise_score(desc) >= 5
    ):
        return "V4.4.6 hard cleanup: HomeLane city/service landing page, not a vacancy"

    if "aecom" in company and re.search(
        r"\b(?:australia|new zealand|anz|americas|middle east|early careers|graduate careers)\b",
        f"{title} {url}",
        re.I,
    ):
        return "V4.4.6 hard cleanup: AECOM region/program bucket page, not an India job posting"

    return ""


def source_page_hard_reject_reason(url, title="", page_text="", cfg=None):
    """
    V4.4.6 source-level cleanup.

    Reject sources that are actually service/location pages before they keep
    recreating fake jobs on every hourly run.
    """
    candidate = {
        "title": title or "",
        "description": page_text or "",
        "source_url": url or "",
        "apply_url": url or "",
        "source_type": "Source Page",
    }
    reason = real_vacancy_reject_reason(candidate, cfg or {})
    low_reason = reason.lower()
    if reason and any(
        key in low_reason
        for key in (
            "service/location",
            "marketing/service",
            "region/career",
            "ats region/program",
        )
    ):
        return f"V4.4.6 source cleanup: {reason}"

    low_url = canonical_url(url or "").lower()
    # V4.4.6: block known non-job/service landing URLs before fetching/parsing huge pages.
    if "homelane.com" in low_url and any(bit in low_url for bit in (
        "/interior-designers", "/interior-designers-in",
        "/modular-kitchen", "/wardrobe", "/home-interior",
    )):
        return "V4.4.6 source cleanup: HomeLane service/location page, not a hiring source"
    if "careers.smartrecruiters.com/aecom2/anz" in low_url or "anz---early-careers" in low_url:
        return "V4.4.6 source cleanup: AECOM ANZ early-careers bucket, not an India job source"
    low_text = clean_text(page_text or "").lower()
    low_title = clean_text(title or "").lower()
    if any(bit in low_url for bit in SERVICE_PAGE_URL_BITS) and _service_noise_score(low_text) >= 3:
        return "V4.4.6 source cleanup: service/location marketing page, not a hiring source"
    if re.search(r"\b(?:australia|new zealand|anz|graduate careers|early careers)\b", f"{low_title} {low_url}"):
        return "V4.4.6 source cleanup: ATS region/program bucket, not an India job source"
    return ""



def normalize_job(job, cfg):
    job["title"] = clean_title(job.get("title", ""), job.get("company", ""))
    if not job.get("title"):
        return None

    job["description"] = clean_text(job.get("description"))

    # V4.4.6: reject marketing/service/location pages and generic ATS buckets
    # before keyword/location checks. This keeps Sheet1 website-ready.
    reject_reason = real_vacancy_reject_reason(job, cfg)
    if reject_reason:
        return None
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
        job=job,
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



def _looks_like_job_heading(tag, company, cfg):
    """Recognize any likely job-role heading, even if that role is not architecture.

    We use this as a section boundary. Example: an Architectural Draftsman block
    must stop before a Project Engineer heading even though Project Engineer is
    not a target role for this architecture collector.
    """
    if not isinstance(tag, Tag) or tag.name not in {"h1", "h2", "h3", "h4", "h5", "h6"}:
        return False
    raw = clean_text(tag.get_text(" ", strip=True))
    if not raw or len(raw) > 180:
        return False
    low = raw.lower().strip(" :-|")
    generic = {
        "application", "apply", "open positions", "open position", "current openings",
        "current opening", "requirements", "responsibilities", "qualification",
        "qualifications", "benefits", "about us", "careers", "career", "contact",
        "get in touch", "documents", "explore our office culture",
    }
    if low in generic:
        return False
    role_terms = (
        "architect", "designer", "draftsman", "draughtsman", "engineer", "coordinator",
        "manager", "officer", "visualizer", "visualiser", "planner", "intern",
        "surveyor", "modeler", "modeller", "artist", "lead", "head",
    )
    return any(re.search(rf"\b{re.escape(term)}\b", low) for term in role_terms)


def _is_role_heading(tag, company, cfg):
    if not _looks_like_job_heading(tag, company, cfg):
        return False
    candidate = clean_title(tag.get_text(" ", strip=True), company)
    return bool(candidate) and relevant_architecture_job(
        {"title": candidate, "description": "", "skills": ""}, cfg
    )



def _job_section_text_from_heading(heading, company, cfg, max_chars=7000):
    """Collect this vacancy only, stopping at the next likely job heading/form."""
    parts = []
    seen = set()
    title = clean_title(heading.get_text(" ", strip=True), company)
    if title:
        parts.append(title)
        seen.add(title.lower())

    for node in heading.next_elements:
        if node is heading:
            continue
        if isinstance(node, Tag):
            if node.name in {"script", "style", "noscript", "svg"}:
                continue
            if node.name == "form":
                break
            if node is not heading and _looks_like_job_heading(node, company, cfg):
                break
            continue
        if not isinstance(node, NavigableString):
            continue
        parent = node.parent
        if parent and getattr(parent, "name", None) in {"script", "style", "noscript", "svg"}:
            continue
        value = clean_text(str(node))
        if not value:
            continue
        # Stop before a form/application UI even when it is not marked as a heading.
        low = value.lower().strip(" :-|")
        if low in {"application", "application form", "apply now", "documents"} and len(parts) > 1:
            break
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        parts.append(value)
        if sum(len(p) + 1 for p in parts) >= max_chars:
            break

    return clean_text(" ".join(parts))[:max_chars]


def _validation_text_for_inline_job(block_text, page_text, apply_url, method, cfg):
    """Keep job-specific closed/deadline text, plus only global live-apply evidence."""
    low_page = (page_text or "").lower()
    open_evidence = [
        signal for signal in cfg.get("open_signals", [])
        if signal.lower() in low_page
    ][:6]
    return clean_text(" ".join([
        block_text,
        " ".join(open_evidence),
        method or "",
        apply_url or "",
    ]))


def inline_jobs_from_career_page(page_url, response_text, cfg):
    soup = BeautifulSoup(response_text, "html.parser")
    page_text = best_main_text(soup)
    company = extract_company_name(soup, page_url)
    logo = extract_logo(soup, page_url)
    results = []
    seen_titles = set()

    # A page-level apply route (form or HR email) can legitimately apply to every
    # role on a careers page. We use it only as fallback if the role block has none.
    page_apply_url, page_method, page_apply_email = find_apply_route(
        soup, page_url, page_text, cfg
    )
    page_email, page_phone = extract_public_contacts(page_text)

    for heading in soup.find_all(["h2", "h3", "h4", "h5", "h6"]):
        title = clean_title(heading.get_text(" ", strip=True), company)
        if not relevant_architecture_job(
            {"title": title, "description": "", "skills": ""}, cfg
        ):
            continue

        title_key = re.sub(r"\s+", " ", title.lower()).strip()
        if not title_key or title_key in seen_titles:
            continue
        seen_titles.add(title_key)

        # If the heading itself is a link to a detail page, parse_page_for_jobs()
        # will handle that page separately; avoid a duplicate careers-page row.
        linked = heading.find("a", href=True) or heading.find_parent("a", href=True)
        if linked:
            href = canonical_url(urljoin(page_url, linked.get("href", "")))
            if href and canonical_url(href) != canonical_url(page_url):
                continue

        block_text = _job_section_text_from_heading(heading, company, cfg)
        if len(block_text) < len(title) + 8:
            continue

        # Search the nearest semantic container for a role-specific apply control.
        container = heading.find_parent(["article", "li", "section"]) or heading.parent or soup
        apply_url, method, apply_email = find_apply_route(
            container, page_url, block_text, cfg
        )
        if not apply_url:
            apply_url, method, apply_email = (
                page_apply_url, page_method, page_apply_email
            )
        if not apply_url:
            continue

        # Location may be stated once for the whole careers page, so block first,
        # then fall back to the page context.
        location, city, state, country = detect_location(block_text)
        if not location:
            location, city, state, country = detect_location(page_text[:12000])

        block_email, block_phone = extract_public_contacts(block_text)
        validation_text = _validation_text_for_inline_job(
            block_text, page_text, apply_url, method, cfg
        )

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
            "contact_email": block_email or apply_email or page_email,
            "contact_phone": block_phone or page_phone,
            "page_text": validation_text,
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



ATS_HOST_PATTERNS = {
    "Lever": ("jobs.lever.co", "jobs.eu.lever.co"),
    "Greenhouse": ("boards.greenhouse.io", "job-boards.greenhouse.io", "boards.eu.greenhouse.io"),
    "Ashby": ("jobs.ashbyhq.com",),
    "SmartRecruiters": ("careers.smartrecruiters.com", "jobs.smartrecruiters.com"),
    "Workday": ("myworkdayjobs.com",),
    "Workable": ("apply.workable.com",),
    "Zoho Recruit": ("jobs.zohorecruit.com", "careers.zohorecruit.com"),
    "Breezy": ("breezy.hr", "jobs.breezy.hr"),
    "Teamtailor": ("teamtailor.com", "careers.teamtailor.com"),
    "Jobvite": ("jobs.jobvite.com", "jobvite.com"),
    "iCIMS": ("icims.com", "careers.icims.com"),
    "Recruitee": ("recruitee.com", "jobs.recruitee.com"),
    "BambooHR": ("bamboohr.com", "applytojob.com"),
}


def ats_provider_from_url(url):
    """Return a supported/public ATS provider name or empty string."""
    d = domain(url)
    if not d:
        return ""
    for provider, hosts in ATS_HOST_PATTERNS.items():
        for host in hosts:
            if d == host or d.endswith("." + host):
                return provider
    if d.endswith(".freshteam.com") or ".freshteam.com" in d:
        return "Freshteam"
    return ""


def ats_identifier_from_url(url, provider=None):
    """Extract the public board/account identifier when the provider exposes one."""
    provider = provider or ats_provider_from_url(url)
    p = urlparse(url)
    parts = [x for x in p.path.split("/") if x]
    d = p.netloc.lower()

    if provider in ("Lever", "Greenhouse", "Ashby", "SmartRecruiters", "Workable"):
        return parts[0] if parts else ""

    if provider in ("Breezy", "Teamtailor", "Recruitee", "BambooHR"):
        if parts:
            return parts[0]
        root = d.split(".")[0]
        return root if root not in ("www", "jobs", "careers", "apply") else ""

    if provider in ("Jobvite", "iCIMS", "Zoho Recruit"):
        return parts[0] if parts else d

    if provider == "Freshteam":
        return d.split(".freshteam.com", 1)[0]

    if provider == "Workday":
        return d.split(".myworkdayjobs.com", 1)[0]

    return ""


def ats_board_root(url):
    """Normalize known ATS job/detail URLs to a reusable public board root."""
    provider = ats_provider_from_url(url)
    ident = ats_identifier_from_url(url, provider)
    p = urlparse(url)

    if not provider:
        return canonical_url(url)

    if provider == "Lever" and ident:
        host = "jobs.eu.lever.co" if "jobs.eu.lever.co" in p.netloc.lower() else "jobs.lever.co"
        return f"https://{host}/{ident}"

    if provider == "Greenhouse" and ident:
        return f"https://job-boards.greenhouse.io/{ident}"

    if provider == "Ashby" and ident:
        return f"https://jobs.ashbyhq.com/{ident}"

    if provider == "SmartRecruiters" and ident:
        return f"https://careers.smartrecruiters.com/{ident}"

    if provider == "Workable" and ident:
        return f"https://apply.workable.com/{ident}"

    if provider == "Freshteam":
        return f"https://{domain(url)}/jobs"

    return canonical_url(url)


def _clean_candidate_url(candidate):
    """Normalize URLs found inside scripts/config attributes."""
    value = clean_text(candidate or "")
    if not value:
        return ""
    value = html.unescape(value)
    value = value.replace("\\/", "/")
    value = value.strip(" \t\r\n'\"<>),;]}")
    for _ in range(2):
        decoded = unquote(value)
        if decoded == value:
            break
        value = decoded
    return canonical_url(value)


def detect_ats_links(page_url, response_text, cfg):
    """
    Discover public ATS board/job URLs embedded in a company careers page.

    V4.4.2 inspects anchors, iframes, script src, form actions, data attrs,
    meta refresh redirects, and raw/encoded JavaScript config URLs.
    """
    soup = BeautifulSoup(response_text or "", "html.parser")
    candidates = []

    attrs = ("href", "src", "action", "data-url", "data-src", "data-href", "data-apply-url")
    for tag in soup.find_all(True):
        for attr in attrs:
            value = tag.get(attr)
            if value:
                candidates.append(urljoin(page_url, value))

    for meta in soup.find_all("meta"):
        http_equiv = clean_text(meta.get("http-equiv", "")).lower()
        content = clean_text(meta.get("content", ""))
        if http_equiv == "refresh" and "url=" in content.lower():
            target = re.split(r"url\s*=", content, flags=re.I, maxsplit=1)[-1]
            candidates.append(urljoin(page_url, target))

    raw = response_text or ""
    normalized_raw = html.unescape(raw).replace("\\/", "/")
    raw_blobs = [raw, normalized_raw, unquote(normalized_raw)]

    for blob in raw_blobs:
        url_pattern = re.compile(r"https?://[^\s\"'<>\\)\]\}]+", re.I)
        encoded_pattern = re.compile(r"https?%3A%2F%2F[^\s\"'<>\\)\]\}]+", re.I)
        candidates.extend(url_pattern.findall(blob))
        candidates.extend(encoded_pattern.findall(blob))

    out = []
    seen = set()
    for candidate in candidates:
        candidate = _clean_candidate_url(candidate)
        if not candidate or is_blocked_domain(candidate, cfg):
            continue
        provider = ats_provider_from_url(candidate)
        if not provider:
            continue
        root = ats_board_root(candidate)
        key = (provider, root)
        if root and key not in seen:
            seen.add(key)
            out.append({
                "provider": provider,
                "url": root,
                "identifier": ats_identifier_from_url(root, provider),
            })
    return out



def _ats_job_base(
    *,
    title,
    company,
    location_text,
    description,
    job_type="",
    skills="",
    posted_date=None,
    deadline=None,
    source_type,
    source_name,
    source_url,
    apply_url,
    company_website="",
):
    location, city, state, country = detect_location(
        " ".join(x for x in (location_text, description) if x)
    )
    return {
        "title": clean_text(title),
        "company": clean_text(company),
        "location": location or clean_text(location_text),
        "city": city,
        "state": state,
        "country": country,
        "job_type": clean_text(job_type),
        "experience": "",
        "salary": "",
        "skills": clean_text(skills),
        "description": clean_text(description),
        "posted_date": posted_date,
        "deadline": deadline,
        "source_type": source_type,
        "source_name": source_name,
        "source_url": canonical_url(source_url),
        "apply_url": canonical_url(apply_url),
        "application_method": "Public ATS",
        "company_website": canonical_url(company_website),
        "logo_url": "",
        "contact_email": "",
        "contact_phone": "",
        "page_text": clean_text(description) + " apply now",
    }


def fetch_ashby(board, cfg):
    if not board:
        return []
    api = (
        "https://api.ashbyhq.com/posting-api/job-board/"
        f"{quote(board, safe='')}?includeCompensation=true"
    )
    r = fetch(api, cfg)
    if not r:
        return []
    try:
        rows = r.json().get("jobs", [])
    except Exception:
        return []

    out = []
    for x in rows:
        if x.get("isListed") is False:
            continue
        description = (
            x.get("descriptionPlain")
            or x.get("description")
            or x.get("descriptionHtml")
            or ""
        )
        compensation = x.get("compensation")
        salary = clean_text(compensation) if compensation else ""
        apply_url = (
            x.get("applyUrl")
            or x.get("jobUrl")
            or x.get("url")
            or f"https://jobs.ashbyhq.com/{board}"
        )
        job = _ats_job_base(
            title=x.get("title", ""),
            company=board.replace("-", " ").title(),
            location_text=x.get("location", ""),
            description=description,
            job_type=x.get("employmentType", ""),
            skills=" ".join(
                clean_text(x.get(k))
                for k in ("department", "team")
                if x.get(k)
            ),
            posted_date=parse_date(
                x.get("publishedAt")
                or x.get("published_at")
                or x.get("createdAt")
            ),
            source_type="Ashby API",
            source_name="Ashby",
            source_url=x.get("jobUrl") or apply_url,
            apply_url=apply_url,
        )
        job["salary"] = salary
        n = normalize_job(job, cfg)
        if n:
            out.append(n)
    return out


def _smartrecruiters_description(detail):
    job_ad = detail.get("jobAd") or {}
    sections = job_ad.get("sections") or {}
    pieces = []
    if isinstance(sections, dict):
        for value in sections.values():
            if isinstance(value, dict):
                pieces.append(value.get("text") or "")
            elif isinstance(value, str):
                pieces.append(value)
    return clean_text(" ".join(pieces))


def fetch_smartrecruiters(company, cfg):
    if not company:
        return []
    list_url = (
        "https://api.smartrecruiters.com/v1/companies/"
        f"{quote(company, safe='')}/postings?limit=100&offset=0"
    )
    r = fetch(list_url, cfg)
    if not r:
        return []
    try:
        rows = r.json().get("content", [])
    except Exception:
        return []

    out = []
    detail_limit = int(cfg.get("smartrecruiters_detail_limit", 40))
    checked = 0

    for x in rows:
        title = clean_text(x.get("name"))
        loc_obj = x.get("location") or {}
        loc_parts = [
            clean_text(loc_obj.get("city")),
            clean_text(loc_obj.get("region")),
            clean_text(loc_obj.get("country")),
        ]
        location_text = ", ".join(v for v in loc_parts if v)

        # Filter cheaply before fetching details.
        quick = {
            "title": title,
            "description": "",
            "skills": "",
        }
        if not relevant_architecture_job(quick, cfg):
            continue
        if not any(marker.lower() in location_text.lower() for marker in cfg.get("india_markers", [])):
            continue

        posting_id = clean_text(x.get("id"))
        if not posting_id or checked >= detail_limit:
            continue
        checked += 1

        detail_url = (
            "https://api.smartrecruiters.com/v1/companies/"
            f"{quote(company, safe='')}/postings/{quote(posting_id, safe='')}"
        )
        rr = fetch(detail_url, cfg)
        if not rr:
            continue
        try:
            detail = rr.json()
        except Exception:
            continue

        description = _smartrecruiters_description(detail)
        apply_url = (
            clean_text(detail.get("ref"))
            or clean_text(x.get("ref"))
            or f"https://jobs.smartrecruiters.com/{company}/{posting_id}"
        )
        employment = detail.get("typeOfEmployment") or x.get("typeOfEmployment") or {}
        job_type = clean_text(
            employment.get("label") if isinstance(employment, dict) else employment
        )

        job = _ats_job_base(
            title=title,
            company=company.replace("-", " ").title(),
            location_text=location_text,
            description=description,
            job_type=job_type,
            posted_date=parse_date(
                detail.get("releasedDate")
                or x.get("releasedDate")
                or detail.get("createdOn")
            ),
            source_type="SmartRecruiters API",
            source_name="SmartRecruiters",
            source_url=apply_url,
            apply_url=apply_url,
        )
        n = normalize_job(job, cfg)
        if n:
            out.append(n)

    return out


def extract_generic_ats_job_links(board_url, response_text, cfg):
    """Extract public job-detail links from unsupported ATS boards."""
    soup = BeautifulSoup(response_text or "", "html.parser")
    out = []
    seen = set()
    base_domain = domain(board_url)

    for a in soup.find_all("a", href=True):
        href = canonical_url(urljoin(board_url, a.get("href", "")))
        if not href or href in seen or is_blocked_domain(href, cfg):
            continue
        label = clean_text(a.get_text(" ", strip=True))
        context = f"{label} {href}".lower()

        # Stay on the ATS host for generic crawls.
        if domain(href) != base_domain:
            continue

        pathish = any(
            token in context
            for token in (
                "/job/", "/jobs/", "/jobdetails", "/job-detail",
                "/careers/", "/positions/", "/position/",
            )
        )
        roleish = relevant_architecture_job(
            {"title": label, "description": "", "skills": ""},
            cfg,
        )
        if pathish or roleish:
            seen.add(href)
            out.append((href, label))

        if len(out) >= int(cfg.get("max_ats_job_links_per_source", 60)):
            break

    return out


def scan_generic_ats_source(start_url, cfg):
    r = fetch(start_url, cfg)
    if not r:
        return [], {
            "source": start_url,
            "ok": False,
            "reason": "ATS board unreachable",
        }

    jobs = []
    soup = BeautifulSoup(r.text, "html.parser")
    page_text = best_main_text(soup)

    for obj in jsonld_objects(soup):
        j = job_from_jsonld(obj, r.url, soup, page_text, cfg)
        if j:
            jobs.append(j)

    links = extract_generic_ats_job_links(r.url, r.text, cfg)
    for url, hint in links:
        parsed, _ = parse_page_for_jobs(url, cfg, title_hint=hint)
        jobs.extend(parsed)
        time.sleep(float(cfg.get("request_delay_seconds", 0.05)))

    return jobs, {
        "source": start_url,
        "ok": True,
        "detail_pages_checked": len(links),
        "qualified_jobs": len(jobs),
    }


def scan_ats_source(start_url, cfg):
    provider = ats_provider_from_url(start_url)
    ident = ats_identifier_from_url(start_url, provider)

    if provider == "Lever":
        jobs = fetch_lever(ident, cfg)
        return jobs, {
            "source": start_url,
            "ok": True,
            "ats_provider": provider,
            "qualified_jobs": len(jobs),
        }

    if provider == "Greenhouse":
        jobs = fetch_greenhouse(ident, cfg)
        return jobs, {
            "source": start_url,
            "ok": True,
            "ats_provider": provider,
            "qualified_jobs": len(jobs),
        }

    if provider == "Ashby":
        jobs = fetch_ashby(ident, cfg)
        return jobs, {
            "source": start_url,
            "ok": True,
            "ats_provider": provider,
            "qualified_jobs": len(jobs),
        }

    if provider == "SmartRecruiters":
        jobs = fetch_smartrecruiters(ident, cfg)
        return jobs, {
            "source": start_url,
            "ok": True,
            "ats_provider": provider,
            "qualified_jobs": len(jobs),
        }

    # Workday / Workable / Freshteam / Zoho Recruit:
    # crawl only public board/detail pages, no authentication or private APIs.
    return scan_generic_ats_source(start_url, cfg)



def scan_career_source(start_url, cfg):
    # V4.4.6: fast URL-only source rejection prevents huge service pages from stalling hourly runs.
    fast_reject = source_page_hard_reject_reason(start_url, "", "", cfg)
    if fast_reject:
        return [], {
            "source": start_url,
            "ok": False,
            "reason": fast_reject,
            "hard_reject_source": True,
            "qualified_jobs": 0,
        }

    # V4.4: known public ATS boards use provider-specific/public parsing.
    if ats_provider_from_url(start_url):
        return scan_ats_source(start_url, cfg)

    source_jobs = []
    r = fetch(start_url, cfg)
    if not r:
        return source_jobs, {"source": start_url, "ok": False, "reason": "Source unreachable"}

    soup = BeautifulSoup(r.text, "html.parser")
    page_text = best_main_text(soup)

    page_title = clean_text(soup.title.get_text(" ", strip=True) if soup.title else "")
    source_reject = source_page_hard_reject_reason(r.url, page_title, page_text, cfg)
    if source_reject:
        return source_jobs, {
            "source": start_url,
            "ok": False,
            "reason": source_reject,
            "hard_reject_source": True,
            "qualified_jobs": 0,
        }

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

    max_pages = int(cfg.get("max_pages_per_source", 8))
    if max_pages > 0:
        combined = combined[:max_pages]

    for url, hint in combined:
        jobs, _ = parse_page_for_jobs(url, cfg, title_hint=hint)
        source_jobs.extend(jobs)
        time.sleep(float(cfg.get("request_delay_seconds", 0.05)))

    ats_links = detect_ats_links(r.url, r.text, cfg)

    return source_jobs, {
        "source": start_url,
        "ok": True,
        "detail_pages_checked": len(combined),
        "qualified_jobs": len(source_jobs),
        "ats_boards_found": len(ats_links),
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



def _job_quality_score(job):
    score = 0
    source = (job.get("source_type") or "").lower()
    if "ats" in source or "api" in source or "json" in source:
        score += 12
    elif source == "html":
        score += 10
    elif "inline" in source:
        score += 3

    src = canonical_url(job.get("source_url"))
    path = urlparse(src).path.lower() if src else ""
    if path and path.rstrip("/") not in {"/career", "/careers", "/jobs", "/openings"}:
        score += 8
    if job.get("experience"):
        score += 3
    if job.get("skills"):
        score += 2
    if job.get("deadline"):
        score += 3
    if job.get("posted_date"):
        score += 3
    if job.get("logo_url") and "logo" in job.get("logo_url", "").lower():
        score += 3
    if job.get("contact_email"):
        score += max(0, 4 - _email_priority(job.get("contact_email")))
    desc_len = len(job.get("description") or "")
    score += min(desc_len // 350, 5)
    return score


def _merge_job_records(preferred, other):
    out = dict(preferred)
    for key in (
        "experience", "salary", "skills", "posted_date", "deadline", "logo_url",
        "contact_email", "contact_phone", "company_website", "apply_url",
        "application_method", "location", "city", "state", "country",
    ):
        if not out.get(key) and other.get(key):
            out[key] = other[key]
    # Prefer the dedicated/richer description, never concatenate pages together.
    if len(other.get("description") or "") > len(out.get("description") or "") and _job_quality_score(other) > _job_quality_score(preferred):
        out["description"] = other["description"]
    out["fingerprint"] = fingerprint(out)
    return out


def dedupe(jobs):
    groups = {}
    for job in jobs:
        key = natural_job_key(job)
        if not key.strip("|"):
            continue
        if key not in groups:
            groups[key] = job
            continue
        current = groups[key]
        if _job_quality_score(job) > _job_quality_score(current):
            groups[key] = _merge_job_records(job, current)
        else:
            groups[key] = _merge_job_records(current, job)

    result = []
    for job in groups.values():
        job["fingerprint"] = fingerprint(job)
        result.append(job)
    return result


def scan_all_sources(cfg):
    jobs = []
    reports = []

    for site in cfg.get("lever_sites", []):
        jobs.extend(fetch_lever(site, cfg))
    for board in cfg.get("greenhouse_boards", []):
        jobs.extend(fetch_greenhouse(board, cfg))

    sources = list(cfg.get("career_pages", []))
    max_sources = int(cfg.get("max_sources_per_run", 30))
    if max_sources > 0:
        sources = sources[:max_sources]

    workers = max(1, min(int(cfg.get("source_workers", 4)), 8))
    per_future_timeout = int(cfg.get("source_future_timeout_seconds", 45))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(scan_career_source, source, cfg): source for source in sources}
        for fut in as_completed(futures):
            source = futures[fut]
            try:
                source_jobs, report = fut.result(timeout=per_future_timeout)
                jobs.extend(source_jobs)
                reports.append(report)
                print(f"SOURCE {'OK' if report['ok'] else 'FAIL'} | {source} | jobs={report.get('qualified_jobs', 0)}")
            except Exception as e:
                reports.append({"source": source, "ok": False, "reason": f"V4.4.6 skipped source safely: {e}"})
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


def get_sheet_properties(service, spreadsheet_id, tab, create_if_missing=True):
    """Return Google Sheet tab properties; create the tab if it does not exist."""
    metadata = service.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        fields="sheets(properties(sheetId,title,gridProperties(rowCount,columnCount)))",
    ).execute()

    for sheet in metadata.get("sheets", []):
        props = sheet.get("properties", {})
        if props.get("title") == tab:
            return props

    if not create_if_missing:
        raise RuntimeError(f"Google Sheet tab not found: {tab}")

    # Create a sufficiently wide tab so future V2 fields do not immediately
    # run into the default Google Sheets column limit.
    response = service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={
            "requests": [
                {
                    "addSheet": {
                        "properties": {
                            "title": tab,
                            "gridProperties": {
                                "rowCount": 1000,
                                "columnCount": max(40, len(V2_HEADERS)),
                            },
                        }
                    }
                }
            ]
        },
    ).execute()

    try:
        return response["replies"][0]["addSheet"]["properties"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Unable to create Google Sheet tab: {tab}") from exc


def ensure_sheet_columns(service, spreadsheet_id, tab, required_columns):
    """Expand the worksheet when more columns are required."""
    props = get_sheet_properties(
        service,
        spreadsheet_id,
        tab,
        create_if_missing=True,
    )

    current_columns = int(
        props.get("gridProperties", {}).get("columnCount", 0) or 0
    )

    if current_columns >= required_columns:
        return current_columns

    columns_to_add = required_columns - current_columns

    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={
            "requests": [
                {
                    "appendDimension": {
                        "sheetId": props["sheetId"],
                        "dimension": "COLUMNS",
                        "length": columns_to_add,
                    }
                }
            ]
        },
    ).execute()

    print(
        f"Expanded Google Sheet '{tab}' from "
        f"{current_columns} to {required_columns} columns"
    )
    return required_columns


def read_sheet_values(service, sheet_id, tab):
    """Read only the columns that physically exist in the worksheet."""
    props = get_sheet_properties(
        service,
        sheet_id,
        tab,
        create_if_missing=True,
    )
    column_count = int(
        props.get("gridProperties", {}).get("columnCount", 1) or 1
    )
    end_col = column_letter(max(1, column_count))

    return (
        service.spreadsheets()
        .values()
        .get(
            spreadsheetId=sheet_id,
            range=f"'{tab}'!A:{end_col}",
        )
        .execute()
        .get("values", [])
    )


def ensure_headers(service, sheet_id, tab):
    """
    Keep existing columns, append missing V2 columns and automatically
    expand the Google Sheet before writing beyond its current grid size.
    """
    # Ensure the tab exists before trying to read it.
    get_sheet_properties(
        service,
        sheet_id,
        tab,
        create_if_missing=True,
    )

    values = read_sheet_values(service, sheet_id, tab)

    # Empty worksheet: create all V2 headers.
    if not values:
        ensure_sheet_columns(
            service,
            sheet_id,
            tab,
            len(V2_HEADERS),
        )

        service.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range=f"'{tab}'!A1:{column_letter(len(V2_HEADERS))}1",
            valueInputOption="RAW",
            body={"values": [V2_HEADERS]},
        ).execute()

        print(
            f"Created {len(V2_HEADERS)} V2 headers "
            f"in Google Sheet tab '{tab}'"
        )
        return V2_HEADERS[:]

    headers = values[0][:]

    # Keep legacy V1 columns, but append every V2 column that is missing.
    missing = [header for header in V2_HEADERS if header not in headers]

    if missing:
        required_columns = len(headers) + len(missing)

        # This is the important V2 fix: Google Sheets starts with a finite
        # column grid. Expand it BEFORE writing headers such as AB:AH.
        ensure_sheet_columns(
            service,
            sheet_id,
            tab,
            required_columns,
        )

        start_col_number = len(headers) + 1
        end_col_number = required_columns

        start_col = column_letter(start_col_number)
        end_col = column_letter(end_col_number)

        service.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range=f"'{tab}'!{start_col}1:{end_col}1",
            valueInputOption="RAW",
            body={"values": [missing]},
        ).execute()

        headers.extend(missing)

        print(
            f"Added {len(missing)} missing V2 columns "
            f"to Google Sheet tab '{tab}'"
        )

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



def format_website_date(value):
    """Format a source date as DD-MM-YYYY without inventing a date."""
    parsed = value if isinstance(value, date) else parse_date(value)
    return parsed.strftime("%d-%m-%Y") if parsed else ""


def external_id_from_meta(record):
    """Stable external ID derived from the collector's persistent Job ID."""
    raw = clean_text(record.get("Job ID") or "")
    if not raw:
        fp = clean_text(record.get("Fingerprint") or "")
        raw = job_id(fp) if fp else hashlib.sha1(
            natural_job_key(record).encode("utf-8")
        ).hexdigest()[:16]
    return f"JOB-{raw.upper()}"


def normalize_pipe_tags(value):
    """Convert comma/semicolon skill lists to the pipe format requested by the site."""
    items = []
    for item in re.split(r"[,;|]+", clean_text(value or "")):
        item = item.strip()
        if item and item.lower() not in {x.lower() for x in items}:
            items.append(item)
    return "|".join(items)


def extract_qualification_for_export(text):
    """Extract qualification without truncating abbreviations such as B.Arch."""
    value = clean_text(text or "")
    if not value:
        return ""

    specific_patterns = [
        r"(Bachelor'?s degree in Architecture\s*\(B\.?\s*Arch\))",
        r"(Bachelor'?s degree in Architecture)",
        r"(Master'?s degree in Architecture\s*\(M\.?\s*Arch\))",
        r"(Master'?s degree in Architecture)",
        r"(B\.?\s*Arch(?:itecture)?)",
        r"(M\.?\s*Arch(?:itecture)?)",
        r"(Diploma or degree in Architectural Drafting)",
        r"(Diploma in Architectural Drafting)",
        r"(Diploma in Architecture)",
        r"(Bachelor'?s degree in Civil Engineering(?: or equivalent)?)",
        r"(Bachelor Degree in Architecture)",
        r"(Bachelor Degree)",
        r"(Diploma Degree)",
    ]
    for pattern in specific_patterns:
        m = re.search(pattern, value, re.I)
        if m:
            result = clean_text(m.group(1))
            result = re.sub(r"\bB\.\s*Arch\b", "B.Arch", result, flags=re.I)
            result = re.sub(r"\bM\.\s*Arch\b", "M.Arch", result, flags=re.I)
            return result

    m = re.search(
        r"(?:qualification|education(?: requirement)?)\s*[:\-]\s*"
        r"(.{3,160}?)"
        r"(?=\s+(?:experience|responsibilit(?:y|ies)|requirements?|skills?|"
        r"application|job type|location|deadline|how to apply)\s*[:\-]?|$)",
        value,
        re.I,
    )
    if m:
        return clean_text(m.group(1)).strip(" -|;,")

    return ""

def extract_gender_for_export(text):
    """Only emit gender when it is explicitly stated by the vacancy."""
    value = clean_text(text or "")
    m = re.search(r"\bgender\s*[:\-]\s*(male|female|both|any)\b", value, re.I)
    if not m:
        return ""
    word = m.group(1).lower()
    if word in ("both", "any"):
        return "Both"
    return word.title()


def career_level_for_export(title, experience):
    low = f"{clean_text(title)} {clean_text(experience)}".lower()
    if any(x in low for x in ("intern", "trainee")):
        return "Entry Level"
    if any(x in low for x in ("junior", "0-2 year", "0–2 year", "1-3 year", "1–3 year")):
        return "Entry Level"
    if any(x in low for x in ("senior", "lead", "principal", "associate", "director", "head")):
        return "Senior Level"

    nums = [int(x) for x in re.findall(r"\b(\d+)\b", clean_text(experience))]
    if nums:
        minimum = min(nums)
        if minimum >= 7:
            return "Senior Level"
        if minimum >= 3:
            return "Mid Level"
        return "Entry Level"
    return ""


def industry_for_export(category):
    cat = clean_text(category)
    if cat == "Interior Design":
        return "Interior Design"
    if cat == "Landscape Architecture":
        return "Landscape Architecture"
    if cat == "Urban Design / Planning":
        return "Urban Design / Planning"
    if cat == "Visualization":
        return "Architecture Visualization"
    if cat == "BIM":
        return "Architecture"
    return "Architecture"


def urgent_for_export(text):
    low = clean_text(text or "").lower()
    urgent_phrases = (
        "urgent hiring",
        "urgently hiring",
        "urgent requirement",
        "immediate requirement",
        "immediate joining",
        "join immediately",
        "immediate opening",
    )
    return "yes" if any(p in low for p in urgent_phrases) else "no"


def parse_salary_for_export(value):
    """
    Convert common Indian salary strings into salary/max_salary/salary_type.
    Values are left blank when the source does not provide a trustworthy number.
    """
    text = clean_text(value or "")
    if not text:
        return "", "", ""

    low = text.lower()
    salary_type = ""
    if any(x in low for x in ("per month", "/month", "monthly", "p.m.")):
        salary_type = "Monthly"
    elif any(x in low for x in ("per annum", "/year", "yearly", "annual", "lpa")):
        salary_type = "Yearly"
    elif any(x in low for x in ("per hour", "/hour", "hourly")):
        salary_type = "Hourly"

    multiplier = 100000 if "lpa" in low else 1
    numbers = []
    for token in re.findall(r"\d+(?:,\d{2,3})*(?:\.\d+)?", text):
        try:
            numbers.append(float(token.replace(",", "")) * multiplier)
        except ValueError:
            pass

    def display_num(n):
        if not n:
            return ""
        return str(int(n)) if float(n).is_integer() else str(round(n, 2))

    if len(numbers) >= 2:
        return display_num(numbers[0]), display_num(numbers[1]), salary_type
    if len(numbers) == 1:
        return display_num(numbers[0]), "", salary_type
    return "", "", salary_type



EXPORT_FOOTER_MARKERS = (
    "OPEN POSITIONS",
    "GET IN TOUCH",
    "EXPLORE OUR OFFICE CULTURE",
    "LEARN MORE",
    "FOR WORK ENQUIRIES",
    "FOR PRESS ENQUIRIES",
    "TERMS DISCLAIMER",
)

NAV_PHRASES = (
    "Home About Services Projects Careers Contact",
    "Home About Projects Careers Contact",
    "Home About Services Careers Contact",
    "About Services Projects Careers Contact",
)


def clean_export_description(value, title="", company=""):
    """Remove obvious website navigation/header/footer debris conservatively."""
    value = clean_text(value or "")
    if not value:
        return ""

    for nav in NAV_PHRASES:
        value = re.sub(re.escape(nav), " ", value, flags=re.I)

    company_name = clean_text(company or "")
    if company_name:
        value = re.sub(
            rf"^(?:\s*{re.escape(company_name)}\s*)+",
            "",
            value,
            flags=re.I,
        )

    # Remove a leading all-uppercase brand header when it is followed by normal prose.
    value = re.sub(
        r"^[A-Z][A-Z0-9&.'’+\-\s]{5,90}(?=\s+[A-Z][a-z])",
        "",
        value,
    )

    value = re.sub(r"\s+", " ", value).strip()

    upper = value.upper()
    cut_positions = []
    for footer in EXPORT_FOOTER_MARKERS:
        pos = upper.find(footer)
        if pos >= 120:
            cut_positions.append(pos)
    if cut_positions:
        value = value[:min(cut_positions)].strip()

    clean_title = clean_text(title or "")
    if clean_title:
        value = re.sub(
            rf"^\s*{re.escape(clean_title)}\s*[:\-]?\s*",
            "",
            value,
            flags=re.I,
        ).strip()

    return re.sub(r"\s+", " ", value).strip(" -|")


def export_category(title, stored_category):
    inferred = job_category(title)
    if inferred and inferred != "Architecture / Design":
        return inferred
    stored = clean_text(stored_category or "")
    return stored or "Architecture"


def rolling_expiry_date(real_deadline, cfg):
    """Use real deadline when present; otherwise give verified jobs a short rolling expiry."""
    deadline = real_deadline if isinstance(real_deadline, date) else parse_date(real_deadline)
    if deadline:
        return deadline
    days = int(cfg.get("website_rolling_expiry_days", 7))
    days = max(1, min(days, 30))
    return now_ist().date() + timedelta(days=days)




def normalized_logo_500_url(raw_url, cfg):
    """
    Return a 500x500 contained logo URL without stretching the logo.

    V4 uses wsrv.nl's public image-resize endpoint by default:
      - width 500
      - height 500
      - fit=contain
      - transparent letterbox background
      - PNG output

    Set logo_500_proxy_enabled: false to keep the original source URL.
    """
    raw_url = canonical_url(raw_url)
    if not raw_url:
        return ""

    if not cfg.get("logo_500_proxy_enabled", True):
        return raw_url

    # Avoid nesting the proxy if the sheet row is re-exported.
    if "wsrv.nl/?" in raw_url.lower():
        return raw_url

    base = clean_text(cfg.get("logo_proxy_base_url", "https://wsrv.nl/"))
    if not base:
        return raw_url

    size = int(cfg.get("logo_size_px", 500))
    size = max(100, min(size, 1000))
    transparent = clean_text(cfg.get("logo_transparent_background", "00FFFFFF")) or "00FFFFFF"

    separator = "&" if "?" in base else "?"
    return (
        f"{base}{separator}"
        f"url={quote(raw_url, safe='')}"
        f"&w={size}&h={size}"
        f"&fit=contain"
        f"&cbg={transparent}"
        f"&output=png"
    )



def website_record_from_meta(record, cfg):
    """Map one verified internal collector row to the developer's exact schema."""
    title = clean_text(record.get("Job Title") or "")
    company = clean_text(record.get("Company") or "")
    raw_description = clean_text(
        record.get("Full Description") or record.get("Short Description") or ""
    )
    max_desc = int(cfg.get("max_description_chars", 1800))
    if max_desc > 0 and len(raw_description) > max_desc:
        raw_description = raw_description[:max_desc].rsplit(" ", 1)[0] + "..."
    description = clean_export_description(
        raw_description,
        title=title,
        company=company,
    )

    email = clean_text(record.get("Public Contact Email") or "")
    application_method = clean_text(record.get("Application Method") or "")
    raw_apply_url = clean_text(record.get("Apply URL") or "")

    is_email_apply = (
        application_method.lower() == "email"
        or raw_apply_url.lower().startswith("mailto:")
    )
    apply_type = "email" if is_email_apply else "external"

    apply_email = email
    if raw_apply_url.lower().startswith("mailto:"):
        apply_email = raw_apply_url.split(":", 1)[1].split("?", 1)[0].strip()

    apply_url = "" if is_email_apply else raw_apply_url

    real_deadline = (
        record.get("Application Deadline")
        or record.get("Valid Through")
        or ""
    )
    website_expiry = rolling_expiry_date(real_deadline, cfg)

    salary, max_salary, salary_type = parse_salary_for_export(
        record.get("Salary") or ""
    )

    city = clean_text(record.get("City") or "")
    state = clean_text(record.get("State") or "")
    country = clean_text(record.get("Country") or "India") or "India"
    address = clean_text(record.get("Location") or "")
    if not address:
        address = ", ".join(x for x in (city, state, country) if x)

    compact_location = "|".join(x for x in (city or state, country) if x)

    category = export_category(title, record.get("Job Category") or "")
    experience = clean_text(record.get("Experience") or "")

    employer_author_mode = clean_text(
        cfg.get("employer_author_mode", "company_name")
    ).lower()
    employer_author = company
    if employer_author_mode == "email" and email:
        employer_author = email

    return {
        "external_id": "",
        "title": title,
        "description": description,
        "status": "publish",
        "employer_author": employer_author,
        "employer_email": email,
        "employer_name": company,
        "expiry_date": format_website_date(website_expiry),
        "application_deadline_date": format_website_date(real_deadline),
        "featured": "no",
        "urgent": urgent_for_export(f"{title} {description}"),
        "filled": "no",
        "apply_type": apply_type,
        "apply_url": apply_url,
        "apply_email": apply_email,
        "phone": clean_text(record.get("Public Contact Phone") or ""),
        "salary": salary,
        "max_salary": max_salary,
        "salary_type": salary_type,
        "address": address,
        "location": compact_location,
        "category": category,
        "type": clean_text(record.get("Job Type") or ""),
        "tag": normalize_pipe_tags(record.get("Skills") or ""),
        "experience": experience,
        "gender": extract_gender_for_export(description),
        "industry": industry_for_export(category),
        "qualification": extract_qualification_for_export(description),
        "career_level": career_level_for_export(title, experience),
        "video_url": "",
        "logo_url": normalized_logo_500_url(record.get("Logo URL") or "", cfg),
    }

def clear_sheet_tab(service, spreadsheet_id, tab):
    """Clear all values from a worksheet while preserving the tab itself."""
    props = get_sheet_properties(
        service,
        spreadsheet_id,
        tab,
        create_if_missing=True,
    )
    columns = int(props.get("gridProperties", {}).get("columnCount", 1) or 1)
    end_col = column_letter(max(columns, len(WEBSITE_HEADERS)))
    service.spreadsheets().values().clear(
        spreadsheetId=spreadsheet_id,
        range=f"'{tab}'!A:{end_col}",
        body={},
    ).execute()


def sync_website_export_sheet(service, spreadsheet_id, meta_tab, export_tab, cfg):
    """
    Rebuild the website-facing sheet from scratch on every run.

    This intentionally removes old V2/V2.3 headers and rows from the export tab.
    Only currently verified ACTIVE + OPEN jobs are written back.
    """
    meta_values = read_sheet_values(service, spreadsheet_id, meta_tab)
    if not meta_values:
        active_records = []
    else:
        meta_headers = meta_values[0]
        active_records = []
        for row in meta_values[1:]:
            rec = row_to_record(meta_headers, row)
            if (rec.get("Status") or "").strip().lower() != "active":
                continue
            if (rec.get("Application Status") or "").strip().lower() != "open":
                continue
            # V4.4.6: never export old fake rows even before their meta row is
            # closed by revalidation. Sheet1 must remain website-ready.
            if meta_record_vacancy_reject_reason(rec, cfg):
                continue
            active_records.append(rec)

    website_rows = [
        website_record_from_meta(rec, cfg)
        for rec in active_records
    ]

    # Stable sorting makes Google Sheet diffs easier to inspect.
    website_rows.sort(
        key=lambda r: (
            r.get("employer_name", "").lower(),
            r.get("title", "").lower(),
            r.get("external_id", ""),
        )
    )

    get_sheet_properties(
        service,
        spreadsheet_id,
        export_tab,
        create_if_missing=True,
    )
    ensure_sheet_columns(
        service,
        spreadsheet_id,
        export_tab,
        len(WEBSITE_HEADERS),
    )

    # IMPORTANT: this is the requested clean-slate behaviour.
    clear_sheet_tab(service, spreadsheet_id, export_tab)

    rows = [
        [record.get(header, "") for header in WEBSITE_HEADERS]
        for record in website_rows
    ]
    payload = [WEBSITE_HEADERS] + rows

    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"'{export_tab}'!A1:{column_letter(len(WEBSITE_HEADERS))}{len(payload)}",
        valueInputOption="RAW",
        body={"values": payload},
    ).execute()

    print(
        f"Website export sheet '{export_tab}' rebuilt: "
        f"{len(website_rows)} currently open jobs"
    )
    return len(website_rows)



def parse_existing_date(value):
    return parse_date(value)



def revalidate_existing_record(record, cfg):
    hard_reason = meta_record_vacancy_reject_reason(record, cfg)
    if hard_reason:
        return False, hard_reason

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
        # For email-only rows, re-check the source page so an old deadline on
        # that page can still close the vacancy.
        url = record.get("Source URL") or ""
        if not url:
            return True, ""
    if is_blocked_domain(url, cfg) or login_gated_url(url, cfg):
        return False, "Blocked or login-gated application URL"

    r = fetch(url, cfg)
    if not r:
        return False, "Application page is no longer publicly reachable"
    soup = BeautifulSoup(r.text, "html.parser")
    text = best_main_text(soup)

    live_deadline = extract_deadline(soup, text)
    if live_deadline and live_deadline < now_ist().date():
        return False, f"Application deadline passed: {live_deadline.isoformat()}"
    live_posted = extract_posted_date(soup, text)
    if live_posted and live_posted < cutoff:
        return False, f"Posted before cutoff {cutoff.isoformat()}"

    reason = page_closed_reason(text, live_deadline or deadline, cfg)
    if reason:
        return False, reason

    # V2.3: previously accepted undated generic careers-page headings must
    # prove that they are real, role-specific vacancies.
    if not (posted or live_posted):
        pseudo_job = {
            "title": record.get("Job Title", ""),
            "description": (
                record.get("Full Description")
                or record.get("Short Description")
                or ""
            ),
            "experience": record.get("Experience", ""),
            "skills": record.get("Skills", ""),
            "source_type": record.get("Source Type", ""),
            "source_url": record.get("Source URL", ""),
            "apply_url": record.get("Apply URL", ""),
            "application_method": record.get("Application Method", ""),
            "deadline": live_deadline or deadline,
            "page_text": text,
        }
        strong, strong_reason, _ = validate_undated_live_evidence(pseudo_job, cfg)
        if not strong:
            return False, strong_reason

    title = (record.get("Job Title") or "").strip()
    source_url = canonical_url(record.get("Source URL") or "")
    if title and canonical_url(url) == source_url and len(title) < 180:
        title_core = _natural_title(title)
        page_low = re.sub(r"\s+", " ", text.lower())
        significant = [
            w for w in title_core.split()
            if len(w) >= 4 and w not in ("career", "careers", "designs", "architecture")
        ]
        if significant and not all(w in page_low for w in significant[:3]):
            return False, "Role title is no longer visible on the source page"
    return True, ""




def close_hard_fake_meta_rows(existing_entries, headers, queue_update):
    """V4.4.6: close old fake rows without doing slow live revalidation."""
    changed = 0
    for row_num, old_row, rec in existing_entries:
        if (rec.get("Status") or "").lower() != "active":
            continue
        reason = meta_record_vacancy_reject_reason(rec, None)
        if not reason:
            continue
        patch = {
            "Verified At": now_iso(),
            "Application Status": "Closed",
            "Status": "Closed",
            "Closed Reason": reason,
        }
        queue_update(row_num, record_to_row(headers, patch, old_row))
        changed += 1
    if changed:
        print(f"V4.4.6 HARD CLEANUP | closed old fake meta rows: {changed}")
    return changed


def write_sheet(jobs, cfg):
    sheet_id = os.environ.get("GOOGLE_SHEET_ID", "")
    if not sheet_id:
        raise RuntimeError("Missing GitHub secret GOOGLE_SHEET_ID")

    # Keep collector-only fields away from the website import sheet.
    meta_tab = os.environ.get("GOOGLE_META_TAB", "_CollectorMeta")

    # Backward-compatible: your existing workflow currently sets
    # GOOGLE_SHEET_TAB=Sheet1. V3 uses that same tab as the website export tab,
    # so the first run automatically clears all old V2 data from Sheet1.
    export_tab = (
        os.environ.get("GOOGLE_EXPORT_TAB")
        or os.environ.get("GOOGLE_SHEET_TAB")
        or "Jobs"
    )

    svc = sheet_service()
    tab = meta_tab
    headers = ensure_headers(svc, sheet_id, tab)
    values = read_sheet_values(svc, sheet_id, tab)
    rows = values[1:] if len(values) > 1 else []

    existing_entries = []
    existing_by_fp = {}
    existing_by_key = {}
    for idx, row in enumerate(rows, start=2):
        rec = row_to_record(headers, row)
        fp = rec.get("Fingerprint") or ""
        key = natural_job_key(rec)
        entry = (idx, row, rec)
        existing_entries.append(entry)
        if fp:
            existing_by_fp[fp] = entry
        if key.strip("|"):
            existing_by_key.setdefault(key, []).append(entry)

    current_fps = {j["fingerprint"] for j in jobs}
    current_keys = {natural_job_key(j) for j in jobs}
    new_rows = []
    updates_by_row = {}
    new_count = 0
    updated_count = 0
    closed_count = 0
    touched_rows = set()

    def queue_update(row_num, values):
        updates_by_row[row_num] = {
            "range": f"'{tab}'!A{row_num}:{column_letter(len(headers))}{row_num}",
            "values": [values],
        }

    # V4.4.6: close obvious fake legacy rows before slow network revalidation.
    closed_count += close_hard_fake_meta_rows(existing_entries, headers, queue_update)

    # Upsert current V2.3 jobs. Match old V2/V2.1 rows by natural vacancy key
    # when their legacy fingerprint was URL-based.
    for job in jobs:
        fp = job["fingerprint"]
        key = natural_job_key(job)
        match = existing_by_fp.get(fp)
        if not match:
            candidates = existing_by_key.get(key, [])
            active = [e for e in candidates if (e[2].get("Status") or "").lower() == "active"]
            match = active[0] if active else (candidates[0] if candidates else None)

        if match:
            row_num, old_row, rec = match
            record = job_to_record(job, first_seen=rec.get("First Seen") or now_iso())
            merged = record_to_row(headers, record, old_row)
            queue_update(row_num, merged)
            touched_rows.add(row_num)
            updated_count += 1

            # Close legacy duplicate rows for the same vacancy.
            for dup in existing_by_key.get(key, []):
                dup_row_num, dup_old_row, dup_rec = dup
                if dup_row_num == row_num or (dup_rec.get("Status") or "").lower() != "active":
                    continue
                patch = {
                    "Verified At": now_iso(),
                    "Application Status": "Closed",
                    "Status": "Closed",
                    "Closed Reason": "Duplicate merged by V2.3",
                }
                queue_update(dup_row_num, record_to_row(headers, patch, dup_old_row))
                touched_rows.add(dup_row_num)
                closed_count += 1
        else:
            record = job_to_record(job)
            new_rows.append(record_to_row(headers, record))
            new_count += 1

    # Revalidate remaining active rows that were not rediscovered. This will
    # close legacy rows such as a 2023 vacancy whose deadline was previously missed.
    if cfg.get("revalidate_existing_jobs", True):
        limit = int(cfg.get("revalidate_limit_per_run", 20))
        checked = 0
        for row_num, old_row, rec in existing_entries:
            if checked >= limit:
                break
            if row_num in touched_rows or (rec.get("Status") or "").lower() != "active":
                continue
            key = natural_job_key(rec)
            if key in current_keys:
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
            queue_update(row_num, record_to_row(headers, patch, old_row))

    if new_rows:
        svc.spreadsheets().values().append(
            spreadsheetId=sheet_id,
            range=f"'{tab}'!A:{column_letter(len(headers))}",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": new_rows},
        ).execute()

    updates = list(updates_by_row.values())
    if updates:
        svc.spreadsheets().values().batchUpdate(
            spreadsheetId=sheet_id,
            body={"valueInputOption": "RAW", "data": updates},
        ).execute()

    export_count = sync_website_export_sheet(
        svc,
        sheet_id,
        meta_tab,
        export_tab,
        cfg,
    )

    print(
        f"Internal meta tab '{meta_tab}': "
        f"{new_count} new, {updated_count} refreshed, {closed_count} closed/stale"
    )
    return new_count, updated_count, closed_count, export_count



SOURCE_HEADERS = [
    "source_id",
    "company_name",
    "company_website",
    "career_url",
    "city",
    "state",
    "source_type",
    "discovered_from",
    "first_discovered",
    "last_checked",
    "last_success",
    "jobs_found",
    "consecutive_failures",
    "status",
    "quality_reason",
]


def _source_id(url):
    return "SRC-" + hashlib.sha1(
        canonical_url(url).lower().encode("utf-8")
    ).hexdigest()[:12].upper()


def ensure_sources_sheet(service, spreadsheet_id, tab):
    get_sheet_properties(service, spreadsheet_id, tab, create_if_missing=True)
    ensure_sheet_columns(service, spreadsheet_id, tab, len(SOURCE_HEADERS))
    values = read_sheet_values(service, spreadsheet_id, tab)
    if not values:
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"'{tab}'!A1:{column_letter(len(SOURCE_HEADERS))}1",
            valueInputOption="RAW",
            body={"values": [SOURCE_HEADERS]},
        ).execute()
        return SOURCE_HEADERS[:]
    headers = values[0][:]
    missing = [h for h in SOURCE_HEADERS if h not in headers]
    if missing:
        ensure_sheet_columns(
            service,
            spreadsheet_id,
            tab,
            len(headers) + len(missing),
        )
        start = len(headers) + 1
        end = len(headers) + len(missing)
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"'{tab}'!{column_letter(start)}1:{column_letter(end)}1",
            valueInputOption="RAW",
            body={"values": [missing]},
        ).execute()
        headers.extend(missing)
    return headers


def seed_sources_registry(service, spreadsheet_id, tab, cfg):
    """Put config seed sources into Sources once, without duplicating them."""
    headers = ensure_sources_sheet(service, spreadsheet_id, tab)
    values = read_sheet_values(service, spreadsheet_id, tab)
    existing = set()
    if values:
        for row in values[1:]:
            rec = row_to_record(headers, row)
            u = canonical_url(rec.get("career_url") or "")
            if u:
                existing.add(u)

    new_rows = []
    stamp = now_ist().isoformat(timespec="seconds")
    for url in cfg.get("career_pages", []):
        url = canonical_url(url)
        if not url or url in existing:
            continue
        host = domain(url).replace("www.", "")
        company = company_name_from_domain(url)
        row = {
            "source_id": _source_id(url),
            "company_name": company,
            "company_website": origin(url),
            "career_url": url,
            "city": "",
            "state": "",
            "source_type": "Seed Website",
            "discovered_from": "config.yaml seed",
            "first_discovered": stamp,
            "last_checked": "",
            "last_success": "",
            "jobs_found": "0",
            "consecutive_failures": "0",
            "status": "Active",
            "quality_reason": "",
        }
        new_rows.append([row.get(h, "") for h in headers])
        existing.add(url)

    if new_rows:
        service.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id,
            range=f"'{tab}'!A:{column_letter(len(headers))}",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": new_rows},
        ).execute()
        print(f"Sources registry seeded with {len(new_rows)} configured sources")


def load_dynamic_sources(cfg):
    """
    Load Active sources from the Sources tab and return a rotating hourly batch.

    Selection is based on oldest last_checked first so a large source library can
    be covered over time without making every hourly run too expensive.
    """
    sheet_id = os.environ.get("GOOGLE_SHEET_ID", "")
    raw_creds = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if not sheet_id or not raw_creds:
        return []

    tab = os.environ.get("GOOGLE_SOURCES_TAB", "Sources")
    svc = sheet_service()
    headers = ensure_sources_sheet(svc, sheet_id, tab)
    seed_sources_registry(svc, sheet_id, tab, cfg)
    values = read_sheet_values(svc, sheet_id, tab)

    rows = []
    for row in values[1:] if values else []:
        rec = row_to_record(headers, row)
        status = clean_text(rec.get("status") or "Active").lower()
        url = canonical_url(rec.get("career_url") or "")
        # V4.3: Rejected/Inactive sources never enter the hourly collector.
        if not url or status not in ("active", "warning", "new"):
            continue

        checked = parse_existing_date(rec.get("last_checked") or "")
        # Full timestamp sort key retained separately when parseable.
        raw_checked = clean_text(rec.get("last_checked") or "")
        try:
            checked_dt = dateparser.parse(raw_checked) if raw_checked else None
        except Exception:
            checked_dt = None

        jobs_found = 0
        try:
            jobs_found = int(float(rec.get("jobs_found") or 0))
        except Exception:
            pass

        rows.append(
            (
                checked_dt or datetime(1970, 1, 1, tzinfo=IST),
                -jobs_found,
                url,
            )
        )

    rows.sort(key=lambda x: (x[0], x[1], x[2]))
    limit = int(cfg.get("hourly_source_batch_size", 80))
    limit = max(1, min(limit, 500))
    return [u for _, _, u in rows[:limit]]


def merge_registry_sources(cfg):
    """Merge seed config sources with the due batch from Sources."""
    dynamic = load_dynamic_sources(cfg)
    merged = []
    seen = set()

    # Dynamic due sources first, then seeds as safety fallback.
    for url in dynamic + list(cfg.get("career_pages", [])):
        url = canonical_url(url)
        if url and url not in seen:
            seen.add(url)
            merged.append(url)

    # At scale, do not let seed fallback defeat batching.
    if dynamic:
        seed_set = {canonical_url(x) for x in cfg.get("career_pages", [])}
        due = dynamic[:]
        # Ensure current original seeds are always checked while the registry is small.
        if len(dynamic) <= int(cfg.get("hourly_source_batch_size", 80)):
            for u in seed_set:
                if u and u not in due:
                    due.append(u)
        merged = due

    cfg = dict(cfg)
    cfg["career_pages"] = merged
    return cfg


def update_sources_from_reports(reports, cfg):
    """Write source health/job counts back to Sources after the hourly scan."""
    sheet_id = os.environ.get("GOOGLE_SHEET_ID", "")
    raw_creds = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if not sheet_id or not raw_creds or not reports:
        return

    tab = os.environ.get("GOOGLE_SOURCES_TAB", "Sources")
    svc = sheet_service()
    headers = ensure_sources_sheet(svc, sheet_id, tab)
    values = read_sheet_values(svc, sheet_id, tab)
    if not values:
        return

    by_url = {}
    for idx, row in enumerate(values[1:], start=2):
        rec = row_to_record(headers, row)
        url = canonical_url(rec.get("career_url") or "")
        if url:
            by_url[url] = (idx, rec)

    stamp = now_ist().isoformat(timespec="seconds")
    disable_after = int(cfg.get("source_disable_after_failures", 8))
    updates = []

    for report in reports:
        url = canonical_url(report.get("source") or "")
        if not url or url not in by_url:
            continue
        row_num, rec = by_url[url]
        rec["last_checked"] = stamp
        ok = bool(report.get("ok"))
        if ok:
            rec["last_success"] = stamp
            rec["jobs_found"] = str(int(report.get("qualified_jobs", 0) or 0))
            rec["consecutive_failures"] = "0"
            rec["status"] = "Active"
            rec["quality_reason"] = rec.get("quality_reason", "") or "Checked successfully"
        elif report.get("hard_reject_source"):
            rec["jobs_found"] = "0"
            rec["consecutive_failures"] = "0"
            rec["status"] = "Rejected"
            rec["quality_reason"] = clean_text(report.get("reason") or "V4.4.6 hard rejected source")
        else:
            try:
                failures = int(float(rec.get("consecutive_failures") or 0)) + 1
            except Exception:
                failures = 1
            rec["consecutive_failures"] = str(failures)
            rec["status"] = "Inactive" if failures >= disable_after else "Warning"
            rec["quality_reason"] = clean_text(report.get("reason") or rec.get("quality_reason") or "Temporary source check failure")

        row_values = [rec.get(h, "") for h in headers]
        updates.append(
            {
                "range": f"'{tab}'!A{row_num}:{column_letter(len(headers))}{row_num}",
                "values": [row_values],
            }
        )

    if updates:
        svc.spreadsheets().values().batchUpdate(
            spreadsheetId=sheet_id,
            body={"valueInputOption": "RAW", "data": updates},
        ).execute()
        print(f"Sources registry updated for {len(updates)} checked sources")



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
        "description": (
            "We are hiring a Junior Architect. Experience: 1-3 years. "
            "Qualification: Bachelor's degree in Architecture. Requirements include "
            "Revit and AutoCAD. Send your resume and portfolio to careers@example.com."
        ),
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

    # V2.3 regression: old application deadlines such as Shree Designs' 2023
    # deadline must be detected even with ordinal suffix + 'Sept'.
    d = labeled_date_from_text(
        "Deadline for Applications: 30th Sept 2023 How to apply: email us",
        ["deadline for applications", "application deadline", "deadline"],
    )
    assert d == date(2023, 9, 30), f"Expected 2023-09-30 deadline, got {d}"
    stale_deadline = dict(
        base,
        posted_date=None,
        deadline=d,
        page_text="We are hiring. Apply now. Deadline for Applications: 30th Sept 2023",
    )
    assert normalize_job(stale_deadline, cfg) is None, "Expired deadline must reject undated job"

    # V2.3 regression: a weak undated generic careers-page heading such as
    # "Junior Architect Site Supervisor Open Positions" must NOT be accepted.
    weak_generic = dict(
        base,
        title="Junior Architect",
        posted_date=None,
        deadline=None,
        source_type="HTML Inline",
        source_url="https://example.com/careers",
        apply_url="https://example.com/careers",
        application_method="Apply Link",
        description="Junior Architect Site Supervisor Open Positions",
        page_text="Open Positions Apply Now",
        experience="",
        skills="",
    )
    assert normalize_job(weak_generic, cfg) is None, (
        "Weak generic careers-page heading must be rejected"
    )

    # A real undated inline vacancy with role-specific experience/qualification
    # and a public form should remain accepted.
    strong_inline = dict(
        base,
        title="Junior Architect",
        posted_date=None,
        deadline=None,
        source_type="HTML Inline",
        source_url="https://example.com/careers",
        apply_url="https://example.com/careers",
        application_method="Public Form",
        description=(
            "Junior Architect Experience: 1-3 years in an architectural firm. "
            "Qualification: Bachelor's degree in Architecture (B.Arch). "
            "Submit your application using the form below."
        ),
        page_text="Current openings. Submit your application.",
        experience="1-3 years",
        skills="",
    )
    assert normalize_job(strong_inline, cfg) is not None, (
        "Substantive undated public-form vacancy should be accepted"
    )

    # A dedicated role page with substantial requirements is also valid even
    # when the employer does not publish a posting date.
    dedicated_undated = dict(
        base,
        title="Architect",
        posted_date=None,
        deadline=None,
        source_type="HTML",
        source_url="https://example.com/careers-architect",
        apply_url="mailto:careers@example.com",
        application_method="Email",
        description=(
            "Architect responsibilities include concept design, drawings and "
            "consultant coordination. Requirements: B.Arch and 3-5 years of "
            "experience. Send your resume and portfolio to careers@example.com."
        ),
        page_text="We are hiring. Send your resume to careers@example.com.",
        experience="3-5 years",
        skills="AutoCAD",
    )
    assert normalize_job(dedicated_undated, cfg) is not None, (
        "Dedicated undated role page with substantive evidence should be accepted"
    )

    # V2.3 regression: careers list + detail page must dedupe to one vacancy.
    a = dict(base, title="Junior Architect (0-2 Years of Experience)", source_type="HTML Inline", source_url="https://example.com/careers")
    b = dict(base, title="Junior Architect", source_type="HTML", source_url="https://example.com/careers-junior-architect")
    a["fingerprint"] = fingerprint(a)
    b["fingerprint"] = fingerprint(b)
    assert a["fingerprint"] == b["fingerprint"], "Natural vacancy fingerprint should ignore experience annotation/URL"
    assert len(dedupe([a, b])) == 1, "Duplicate list/detail vacancy should collapse to one row"

    # Employment type must not be polluted by phrases like previous internship.
    assert extract_job_type("CV detailing internship / previous experience. Job type: Full time") == "Full Time"

    # Careers/HR email should beat generic info/press addresses.
    email, _ = extract_public_contacts("info@example.com press@example.com hr@example.com careers@example.com")
    assert email == "careers@example.com", f"Expected careers email, got {email}"

    # Mojibake repair.
    assert "you’re" in clean_text("If youâ€™re interested").lower()

    export_sample = {
        "Job ID": "abc123",
        "Job Title": "Senior Architect",
        "Company": "Example Architects",
        "Location": "Mumbai, Maharashtra, India",
        "City": "Mumbai",
        "State": "Maharashtra",
        "Country": "India",
        "Job Category": "Architecture",
        "Job Type": "Full Time",
        "Experience": "7-10 years",
        "Salary": "₹80000 - ₹120000 per month",
        "Skills": "AutoCAD, Revit, Rhino",
        "Full Description": (
            "Qualification: Bachelor's degree in Architecture (B.Arch). "
            "Senior Architect role."
        ),
        "Application Deadline": "2026-10-31",
        "Application Status": "Open",
        "Application Method": "External",
        "Apply URL": "https://example.com/jobs/senior-architect",
        "Public Contact Email": "careers@example.com",
        "Public Contact Phone": "+91 90000 00000",
        "Logo URL": "https://example.com/logo.svg",
        "Status": "Active",
        "Fingerprint": "1234567890abcdef",
    }
    exported = website_record_from_meta(export_sample, cfg)
    assert list(exported.keys()) == WEBSITE_HEADERS
    assert exported["external_id"] == ""
    assert exported["status"] == "publish"
    assert exported["location"] == "Mumbai|India"
    assert exported["tag"] == "AutoCAD|Revit|Rhino"
    assert exported["salary"] == "80000"
    assert exported["max_salary"] == "120000"
    assert exported["salary_type"] == "Monthly"
    assert exported["application_deadline_date"] == "31-10-2026"
    assert exported["expiry_date"] == "31-10-2026"
    assert exported["career_level"] == "Senior Level"
    assert exported["qualification"] == "Bachelor's degree in Architecture (B.Arch)"

    undated_sample = dict(export_sample)
    undated_sample["Application Deadline"] = ""
    undated_sample["Job Title"] = "3D Render Artist (Architecture)"
    undated_sample["Job Category"] = "Architecture"
    undated_sample["Full Description"] = (
        "RASIK P HINGOO ASSOCIATES Home About Services Projects Careers Contact "
        "Develop detailed 3D models using SketchUp and 3ds Max. "
        "Requirements: strong architectural visualization portfolio. "
        "OPEN POSITIONS GET IN TOUCH +91-9000000000"
    )
    undated = website_record_from_meta(undated_sample, cfg)
    expected_expiry = now_ist().date() + timedelta(
        days=int(cfg.get("website_rolling_expiry_days", 7))
    )
    assert undated["expiry_date"] == expected_expiry.strftime("%d-%m-%Y")
    assert undated["application_deadline_date"] == ""
    assert undated["category"] == "Visualization"
    assert undated["industry"] == "Architecture Visualization"
    assert "Home About Services" not in undated["description"]
    assert "GET IN TOUCH" not in undated["description"]

    assert extract_qualification_for_export(
        "Qualification: Bachelor's degree in Architecture (B.Arch)"
    ) == "Bachelor's degree in Architecture (B.Arch)"

    logo = normalized_logo_500_url(
        "https://example.com/company-logo.svg",
        cfg,
    )
    assert "w=500" in logo and "h=500" in logo
    assert "fit=contain" in logo and "output=png" in logo

    assert _source_id("https://example.com/careers").startswith("SRC-")

    # V4.1 regression: Sources seeding must always have a company-name fallback.
    assert company_name_from_domain("https://hingooarchitects.com/careers") == "Hingoo Architects"
    assert company_name_from_domain("https://www.shreedesigns.in/careers/") == "Shree Designs"
    assert company_name_from_domain("https://www.hafeezcontractor.com/careers") == "Hafeez Contractor"
    assert exported["external_id"] == ""
    assert "quality_reason" in SOURCE_HEADERS

    assert ats_provider_from_url("https://jobs.lever.co/example") == "Lever"
    assert ats_identifier_from_url("https://jobs.lever.co/example/123", "Lever") == "example"
    assert ats_board_root("https://jobs.lever.co/example/123") == "https://jobs.lever.co/example"

    assert ats_provider_from_url(
        "https://job-boards.greenhouse.io/example/jobs/123"
    ) == "Greenhouse"
    assert ats_identifier_from_url(
        "https://job-boards.greenhouse.io/example/jobs/123",
        "Greenhouse",
    ) == "example"

    assert ats_provider_from_url("https://jobs.ashbyhq.com/example") == "Ashby"
    assert ats_provider_from_url(
        "https://careers.smartrecruiters.com/ExampleCompany"
    ) == "SmartRecruiters"
    assert ats_provider_from_url(
        "https://example.wd3.myworkdayjobs.com/Careers"
    ) == "Workday"
    assert ats_provider_from_url("https://example.teamtailor.com/jobs") == "Teamtailor"
    assert ats_provider_from_url("https://company.breezy.hr") == "Breezy"
    assert ats_provider_from_url("https://jobs.jobvite.com/company") == "Jobvite"
    assert ats_provider_from_url("https://company.icims.com/jobs") == "iCIMS"
    assert ats_provider_from_url("https://company.recruitee.com") == "Recruitee"

    sample_ats_html = r"""
    <html><head>
      <meta http-equiv="refresh" content="0; url=https://careers.smartrecruiters.com/ExampleCompany">
    </head><body>
      <iframe src="https://jobs.lever.co/example"></iframe>
      <a href="https://jobs.ashbyhq.com/another">Open jobs</a>
      <form action="https://apply.workable.com/sample"></form>
      <script>
        window.ats = "https:\/\/job-boards.greenhouse.io\/sample";
        window.alt = "https%3A%2F%2Fexample.teamtailor.com%2Fjobs";
      </script>
    </body></html>
    """
    ats = detect_ats_links("https://example.com/careers", sample_ats_html, cfg)
    providers = {x["provider"] for x in ats}
    assert {"Lever", "Ashby", "SmartRecruiters", "Workable", "Greenhouse", "Teamtailor"}.issubset(providers)

    alias_cfg = dict(cfg)
    alias_cfg["platform_blocked_domains"] = ["archdaily.com"]
    assert is_blocked_domain(
        "https://www-archdaily-com.global.ssl.fastly.net/opportunities",
        alias_cfg,
    )
    assert "CDN/mirror" in blocked_domain_reason(
        "https://www-archdaily-com.global.ssl.fastly.net/opportunities",
        alias_cfg,
    )

    alias_cfg = dict(cfg)
    alias_cfg["platform_blocked_domains"] = ["archdaily.com", "architecturelab.net", "aia.org", "nrd.adsttc.com"]
    assert is_blocked_domain("https://www-archdaily-com.global.ssl.fastly.net/opportunities", alias_cfg)
    assert is_blocked_domain("https://www.architecturelab.net/architect/types", alias_cfg)
    assert is_blocked_domain("https://www.aia.org/career-growth/transcript", alias_cfg)

    service_page_job = {
        "title": "Interior Designers in Ahmedabad",
        "company": "HomeLane",
        "location": "Ahmedabad, Gujarat, India",
        "city": "Ahmedabad",
        "state": "Gujarat",
        "country": "India",
        "description": "Get Free Estimate Design Gallery Store Locator Modular Kitchen Cost Home Interior Cost 45-day delivery 10-year warranty Easy EMIs Book Free Design Session",
        "source_url": "https://www.homelane.com/interior-designers-in-ahmedabad",
        "apply_url": "mailto:hello@homelane.com",
        "application_method": "Email",
        "page_text": "apply now send your resume careers@ HomeLane India",
    }
    assert real_vacancy_reject_reason(service_page_job, cfg)
    assert normalize_job(dict(service_page_job), cfg) is None
    assert meta_record_vacancy_reject_reason({
        "Job Title": "Interior Designers in Ahmedabad",
        "Company": "HomeLane",
        "Full Description": service_page_job["description"],
        "Source URL": service_page_job["source_url"],
        "Apply URL": "mailto:hello@homelane.com",
        "Status": "Active",
        "Application Status": "Open",
    }, cfg)
    assert source_page_hard_reject_reason(
        service_page_job["source_url"],
        service_page_job["title"],
        service_page_job["description"],
        cfg,
    )

    ats_bucket_job = {
        "title": "Australia & New Zealand",
        "company": "AECOM",
        "location": "India",
        "city": "",
        "state": "",
        "country": "India",
        "description": "Graduates and Early Careers India Apply now Search for careers Architecture",
        "source_url": "https://careers.smartrecruiters.com/AECOM2/anz---early-careers---opportunities",
        "apply_url": "https://careers.smartrecruiters.com/AECOM2/anz---early-careers---opportunities",
        "application_method": "Public ATS",
        "page_text": "Apply now India Architecture careers",
    }
    assert real_vacancy_reject_reason(ats_bucket_job, cfg)
    assert normalize_job(dict(ats_bucket_job), cfg) is None
    assert meta_record_vacancy_reject_reason({
        "Job Title": "Australia & New Zealand",
        "Company": "AECOM",
        "Full Description": ats_bucket_job["description"],
        "Source URL": ats_bucket_job["source_url"],
        "Apply URL": ats_bucket_job["apply_url"],
        "Status": "Active",
        "Application Status": "Open",
    }, cfg)

    real_job = {
        "title": "Senior Interior Designer",
        "company": "Example Design Studio",
        "location": "Mumbai, Maharashtra, India",
        "city": "Mumbai",
        "state": "Maharashtra",
        "country": "India",
        "description": "We are hiring a Senior Interior Designer. Apply now. Send your resume. Experience 5 years. AutoCAD SketchUp.",
        "source_url": "https://exampledesignstudio.in/careers/senior-interior-designer",
        "apply_url": "mailto:careers@exampledesignstudio.in",
        "application_method": "Email",
        "page_text": "We are hiring Senior Interior Designer Mumbai India apply now send your resume careers@exampledesignstudio.in",
    }
    assert not real_vacancy_reject_reason(real_job, cfg)


    # V4.4.6 timeout-safety self-test
    assert source_page_hard_reject_reason(
        "https://www.homelane.com/interior-designers/ahmedabad", "", "", cfg
    )
    assert source_page_hard_reject_reason(
        "https://careers.smartrecruiters.com/AECOM2/anz---early-careers---opportunities", "", "", cfg
    )
    print("SELF TEST PASSED: V4.4.6 validation, timeout-safe cleanup, fake-row removal and 500px logo rules are working.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        return

    cfg = load_config()

    # V4: pull the oldest-due public career pages from the Sources registry.
    if os.environ.get("DRY_RUN", "").lower() not in ("1", "true", "yes"):
        try:
            cfg = merge_registry_sources(cfg)
            print(f"V4 active source batch: {len(cfg.get('career_pages', []))} websites")
        except Exception as e:
            print(f"SOURCES WARNING | registry unavailable, using config seeds | {e}")

    jobs, reports = scan_all_sources(cfg)

    if os.environ.get("DRY_RUN", "").lower() not in ("1", "true", "yes"):
        try:
            update_sources_from_reports(reports, cfg)
        except Exception as e:
            print(f"SOURCES WARNING | could not update source health | {e}")

    print("=" * 80)
    print(f"V4.4.6 cutoff date: {minimum_date(cfg).isoformat()}")
    print(f"Sources attempted: {len(reports)}")
    print(f"Qualified OPEN Indian architecture jobs this run: {len(jobs)}")
    print("=" * 80)

    if os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes"):
        print(json.dumps(jobs[:20], ensure_ascii=False, indent=2))
        return

    new_count, updated_count, closed_count, export_count = write_sheet(jobs, cfg)
    print(
        f"Google Sheet complete: {export_count} open jobs in website schema "
        f"({new_count} new / {updated_count} refreshed / {closed_count} closed internally)"
    )


if __name__ == "__main__":
    main()
