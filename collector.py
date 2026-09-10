import os
import re
import json
import time
import hashlib
import html
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse, urldefrag

import requests
import yaml
from bs4 import BeautifulSoup
from dateutil import parser as dateparser
from google.oauth2 import service_account
from googleapiclient.discovery import build


USER_AGENT = (
    "ArchitectJobsCollector/1.0 "
    "(public-job-indexer; contact: set-your-email-in-repo)"
)

SHEET_HEADERS = [
    "Job ID",
    "Job Title",
    "Company",
    "Location",
    "City",
    "State",
    "Country",
    "Job Type",
    "Experience",
    "Salary",
    "Skills",
    "Short Description",
    "Full Description",
    "Posted Date",
    "Valid Through",
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
    "Status",
    "Fingerprint",
]


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean_text(v):
    if v is None:
        return ""
    if isinstance(v, list):
        return ", ".join(clean_text(x) for x in v if clean_text(x))
    if isinstance(v, dict):
        return clean_text(v.get("name") or v.get("value") or "")
    v = BeautifulSoup(str(v), "html.parser").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", html.unescape(v)).strip()


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


def is_blocked_domain(url, cfg):
    d = domain(url)
    return any(d == x or d.endswith("." + x) for x in cfg["blocked_domains"])


def login_gated_url(url, cfg):
    low = (url or "").lower()
    return any(marker in low for marker in cfg["login_url_markers"])


def fetch(url, cfg, method="GET"):
    if not url or is_blocked_domain(url, cfg):
        return None
    try:
        r = requests.request(
            method,
            url,
            timeout=cfg["request_timeout_seconds"],
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/json;q=0.9,*/*;q=0.8"},
            allow_redirects=True,
        )
        if r.status_code >= 400:
            return None
        if is_blocked_domain(r.url, cfg) or login_gated_url(r.url, cfg):
            return None
        return r
    except requests.RequestException:
        return None


def maybe_public_apply_url(url, cfg):
    if not url or is_blocked_domain(url, cfg) or login_gated_url(url, cfg):
        return ""
    r = fetch(url, cfg, method="GET")
    if not r:
        return ""
    low = (r.text[:150000] or "").lower()
    # Conservative heuristic: pages whose main purpose is auth are rejected.
    auth_signals = [
        "sign in to continue",
        "log in to continue",
        "login to continue",
        "create an account to apply",
        "sign in to apply",
    ]
    if any(x in low for x in auth_signals):
        return ""
    return canonical_url(r.url)


def parse_address(addr):
    if not isinstance(addr, dict):
        return "", "", "", ""
    loc = addr.get("address") if isinstance(addr.get("address"), dict) else addr
    city = clean_text(loc.get("addressLocality", ""))
    state = clean_text(loc.get("addressRegion", ""))
    country = clean_text(loc.get("addressCountry", ""))
    if isinstance(loc.get("addressCountry"), dict):
        country = clean_text(loc["addressCountry"].get("name", ""))
    location = ", ".join(x for x in [city, state, country] if x)
    return location, city, state, country


def parse_employment_type(v):
    if isinstance(v, list):
        return ", ".join(clean_text(x) for x in v)
    return clean_text(v)


def extract_salary(obj):
    b = obj.get("baseSalary")
    if not isinstance(b, dict):
        return ""
    cur = clean_text(b.get("currency", ""))
    val = b.get("value", {})
    if isinstance(val, dict):
        minv = val.get("minValue")
        maxv = val.get("maxValue")
        unit = clean_text(val.get("unitText", ""))
        if minv is not None or maxv is not None:
            nums = f"{minv or ''}-{maxv or ''}".strip("-")
            return " ".join(x for x in [cur, nums, unit] if x)
        if val.get("value") is not None:
            return " ".join(x for x in [cur, str(val.get("value")), unit] if x)
    return clean_text(val)


def extract_company(obj):
    org = obj.get("hiringOrganization")
    if isinstance(org, dict):
        return (
            clean_text(org.get("name")),
            canonical_url(org.get("sameAs") or org.get("url") or ""),
            canonical_url(org.get("logo") or ""),
        )
    return clean_text(org), "", ""


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


def relevant_architecture_job(job, cfg):
    hay = " ".join(
        [
            job.get("title", ""),
            job.get("description", ""),
            job.get("skills", ""),
        ]
    ).lower()

    if any(x in hay for x in cfg["keywords"]["exclude"]):
        return False
    if not any(x in hay for x in cfg["keywords"]["include"]):
        return False

    loc = " ".join(
        [job.get("location", ""), job.get("city", ""), job.get("state", ""), job.get("country", "")]
    ).lower()
    country = (job.get("country") or "").lower()
    if country and "india" not in country and country not in ("in", "ind"):
        return False
    return any(marker in loc for marker in cfg["india_markers"])


def public_contacts_from_html(text):
    # Only contact details that are publicly displayed on the page are extracted.
    emails = re.findall(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', text or "")
    emails = [x for x in emails if not x.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))]
    phones = re.findall(r'(?:\+91[\s-]?)?[6-9]\d{9}', re.sub(r"[\s().-]+", "", text or ""))
    return (emails[0] if emails else ""), (phones[0] if phones else "")


def jsonld_objects(soup):
    for tag in soup.find_all("script", attrs={"type": re.compile("ld\+json", re.I)}):
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


def jsonld_job_to_record(obj, page_url, page_html, cfg):
    typ = obj.get("@type")
    types = typ if isinstance(typ, list) else [typ]
    if "JobPosting" not in types:
        return None

    title = clean_text(obj.get("title"))
    description = clean_text(obj.get("description"))
    company, company_site, logo = extract_company(obj)

    jl = obj.get("jobLocation")
    if isinstance(jl, list):
        jl = jl[0] if jl else {}
    location, city, state, country = parse_address(jl or {})

    apply = canonical_url(obj.get("url") or page_url)
    if is_blocked_domain(apply, cfg):
        return None

    email, phone = public_contacts_from_html(page_html)

    job = {
        "title": title,
        "company": company,
        "location": location,
        "city": city,
        "state": state,
        "country": country or ("India" if any(x in location.lower() for x in cfg["india_markers"]) else ""),
        "job_type": parse_employment_type(obj.get("employmentType")),
        "experience": clean_text(obj.get("experienceRequirements")),
        "salary": extract_salary(obj),
        "skills": clean_text(obj.get("skills") or obj.get("qualifications")),
        "description": description,
        "posted_date": clean_text(obj.get("datePosted")),
        "valid_through": clean_text(obj.get("validThrough")),
        "source_type": "JSON-LD",
        "source_name": domain(page_url),
        "source_url": canonical_url(page_url),
        "apply_url": apply,
        "company_website": company_site,
        "logo_url": logo,
        "contact_email": email,
        "contact_phone": phone,
    }
    return job if relevant_architecture_job(job, cfg) else None


def scrape_page_jobs(url, cfg):
    r = fetch(url, cfg)
    if not r:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    jobs = []
    for obj in jsonld_objects(soup):
        rec = jsonld_job_to_record(obj, r.url, r.text, cfg)
        if rec:
            jobs.append(rec)
    return jobs


def sitemap_urls(base_url, cfg):
    parsed = urlparse(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    candidates = [
        urljoin(origin, "/sitemap.xml"),
        urljoin(origin, "/sitemap_index.xml"),
        urljoin(origin, "/job-sitemap.xml"),
        urljoin(origin, "/jobs-sitemap.xml"),
    ]
    found = set()
    for sm in candidates:
        r = fetch(sm, cfg)
        if not r:
            continue
        soup = BeautifulSoup(r.text, "xml")
        for loc in soup.find_all("loc"):
            u = clean_text(loc.get_text())
            if not u:
                continue
            low = u.lower()
            if any(k in low for k in ("career", "job", "opening", "vacanc", "recruit")):
                found.add(u)
            # sitemap index: follow one layer of job/career-looking sub-sitemaps
            elif low.endswith(".xml") and any(k in low for k in ("post", "page")):
                rr = fetch(u, cfg)
                if rr:
                    ss = BeautifulSoup(rr.text, "xml")
                    for ll in ss.find_all("loc"):
                        uu = clean_text(ll.get_text())
                        if any(k in uu.lower() for k in ("career", "job", "opening", "vacanc", "recruit")):
                            found.add(uu)
            if len(found) >= cfg["max_pages_per_source"]:
                break
        if len(found) >= cfg["max_pages_per_source"]:
            break
    return list(found)[: cfg["max_pages_per_source"]]


def fetch_generic_sources(cfg):
    results = []
    for start_url in cfg.get("career_pages", []):
        urls = [start_url] + sitemap_urls(start_url, cfg)
        seen = set()
        for url in urls[: cfg["max_pages_per_source"]]:
            if url in seen:
                continue
            seen.add(url)
            results.extend(scrape_page_jobs(url, cfg))
            time.sleep(cfg["request_delay_seconds"])
    return results


def fetch_lever(site, cfg):
    url = f"https://api.lever.co/v0/postings/{site}?mode=json"
    r = fetch(url, cfg)
    if not r:
        return []
    try:
        data = r.json()
    except Exception:
        return []
    out = []
    for x in data:
        cats = x.get("categories") or {}
        loc = clean_text(cats.get("location"))
        title = clean_text(x.get("text"))
        desc = " ".join(
            clean_text(x.get(k)) for k in ("descriptionPlain", "additionalPlain") if x.get(k)
        )
        apply = canonical_url(x.get("hostedUrl") or x.get("applyUrl") or "")
        job = {
            "title": title,
            "company": clean_text(site).replace("-", " ").title(),
            "location": loc,
            "city": loc.split(",")[0].strip() if loc else "",
            "state": "",
            "country": "India" if any(m in loc.lower() for m in cfg["india_markers"]) else "",
            "job_type": clean_text(cats.get("commitment")),
            "experience": "",
            "salary": "",
            "skills": clean_text(cats.get("team")),
            "description": desc,
            "posted_date": "",
            "valid_through": "",
            "source_type": "Lever API",
            "source_name": "Lever",
            "source_url": apply,
            "apply_url": apply,
            "company_website": "",
            "logo_url": "",
            "contact_email": "",
            "contact_phone": "",
        }
        if relevant_architecture_job(job, cfg):
            out.append(job)
    return out


def fetch_greenhouse(board, cfg):
    url = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"
    r = fetch(url, cfg)
    if not r:
        return []
    try:
        data = r.json().get("jobs", [])
    except Exception:
        return []
    out = []
    for x in data:
        loc = clean_text((x.get("location") or {}).get("name"))
        title = clean_text(x.get("title"))
        desc = clean_text(x.get("content"))
        apply = canonical_url(x.get("absolute_url"))
        job = {
            "title": title,
            "company": clean_text(board).replace("-", " ").title(),
            "location": loc,
            "city": loc.split(",")[0].strip() if loc else "",
            "state": "",
            "country": "India" if any(m in loc.lower() for m in cfg["india_markers"]) else "",
            "job_type": "",
            "experience": "",
            "salary": "",
            "skills": "",
            "description": desc,
            "posted_date": clean_text(x.get("updated_at")),
            "valid_through": "",
            "source_type": "Greenhouse API",
            "source_name": "Greenhouse",
            "source_url": apply,
            "apply_url": apply,
            "company_website": "",
            "logo_url": "",
            "contact_email": "",
            "contact_phone": "",
        }
        if relevant_architecture_job(job, cfg):
            out.append(job)
    return out


def enrich_final_urls(jobs, cfg):
    out = []
    for job in jobs:
        u = job.get("apply_url") or job.get("source_url")
        if is_blocked_domain(u, cfg):
            continue
        # For official ATS/public employer pages we do a final public-access test.
        final = maybe_public_apply_url(u, cfg)
        if not final:
            continue
        job["apply_url"] = final
        out.append(job)
        time.sleep(cfg["request_delay_seconds"])
    return out


def dedupe(jobs):
    uniq = {}
    for j in jobs:
        j["source_url"] = canonical_url(j.get("source_url"))
        j["apply_url"] = canonical_url(j.get("apply_url"))
        fp = fingerprint(j)
        j["fingerprint"] = fp
        # Keep the richer copy if duplicate.
        score = sum(bool(j.get(k)) for k in ("description", "salary", "skills", "logo_url", "contact_email"))
        old = uniq.get(fp)
        if not old or score > old[0]:
            uniq[fp] = (score, j)
    return [x[1] for x in uniq.values()]


def sheet_service():
    raw = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
    info = json.loads(raw)
    creds = service_account.Credentials.from_service_account_info(
        info,
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def ensure_header(service, sheet_id, tab):
    result = service.spreadsheets().values().get(
        spreadsheetId=sheet_id, range=f"{tab}!1:1"
    ).execute()
    values = result.get("values", [])
    if not values:
        service.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range=f"{tab}!A1",
            valueInputOption="RAW",
            body={"values": [SHEET_HEADERS]},
        ).execute()


def read_existing(service, sheet_id, tab):
    result = service.spreadsheets().values().get(
        spreadsheetId=sheet_id,
        range=f"{tab}!A2:AA",
    ).execute()
    rows = result.get("values", [])
    by_fp = {}
    for idx, row in enumerate(rows, start=2):
        if len(row) >= len(SHEET_HEADERS):
            fp = row[SHEET_HEADERS.index("Fingerprint")]
            if fp:
                by_fp[fp] = (idx, row)
    return by_fp


def job_row(job, first_seen=None):
    t = now_iso()
    short = (job.get("description") or "")[:500]
    jid = hashlib.sha1(
        (job.get("fingerprint") or fingerprint(job)).encode("utf-8")
    ).hexdigest()[:16]
    return [
        jid,
        job.get("title", ""),
        job.get("company", ""),
        job.get("location", ""),
        job.get("city", ""),
        job.get("state", ""),
        job.get("country", "India"),
        job.get("job_type", ""),
        job.get("experience", ""),
        job.get("salary", ""),
        job.get("skills", ""),
        short,
        job.get("description", ""),
        job.get("posted_date", ""),
        job.get("valid_through", ""),
        job.get("source_type", ""),
        job.get("source_name", ""),
        job.get("source_url", ""),
        job.get("apply_url", ""),
        job.get("company_website", ""),
        job.get("logo_url", ""),
        job.get("contact_email", ""),
        job.get("contact_phone", ""),
        first_seen or t,
        t,
        "Active",
        job.get("fingerprint", ""),
    ]


def write_sheet(jobs):
    sheet_id = os.environ["GOOGLE_SHEET_ID"]
    tab = os.environ.get("GOOGLE_SHEET_TAB", "Jobs")
    svc = sheet_service()
    ensure_header(svc, sheet_id, tab)
    existing = read_existing(svc, sheet_id, tab)

    new_rows = []
    updates = []
    for job in jobs:
        fp = job["fingerprint"]
        if fp in existing:
            row_num, old = existing[fp]
            first_seen_idx = SHEET_HEADERS.index("First Seen")
            first_seen = old[first_seen_idx] if len(old) > first_seen_idx else now_iso()
            updates.append({
                "range": f"{tab}!A{row_num}:AA{row_num}",
                "values": [job_row(job, first_seen=first_seen)],
            })
        else:
            new_rows.append(job_row(job))

    if new_rows:
        svc.spreadsheets().values().append(
            spreadsheetId=sheet_id,
            range=f"{tab}!A:AA",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": new_rows},
        ).execute()

    if updates:
        svc.spreadsheets().values().batchUpdate(
            spreadsheetId=sheet_id,
            body={"valueInputOption": "RAW", "data": updates},
        ).execute()

    return len(new_rows), len(updates)


def load_config():
    with open("config.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def main():
    cfg = load_config()
    jobs = []

    for site in cfg.get("lever_sites", []):
        jobs.extend(fetch_lever(site, cfg))
    for board in cfg.get("greenhouse_boards", []):
        jobs.extend(fetch_greenhouse(board, cfg))
    jobs.extend(fetch_generic_sources(cfg))

    jobs = dedupe(jobs)
    jobs = enrich_final_urls(jobs, cfg)
    jobs = dedupe(jobs)

    print(f"Qualified public Indian architecture jobs this run: {len(jobs)}")

    if os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes"):
        print(json.dumps(jobs[:10], ensure_ascii=False, indent=2))
        return

    new_count, updated_count = write_sheet(jobs)
    print(f"Google Sheet: {new_count} new, {updated_count} refreshed")


if __name__ == "__main__":
    main()
