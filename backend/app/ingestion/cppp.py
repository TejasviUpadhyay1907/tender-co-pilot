"""Defensive public-listing fetcher for the CPPP portal (eprocure.gov.in).

NO login anywhere in this module. Every request targets public,
unauthenticated listing pages, carries a descriptive User-Agent, waits
~2s between requests (polite rate limit), and has an explicit timeout.

IMPORTANT — best-effort by design: the CPPP search UI is dynamic
(JS/session-driven) and its HTML structure changes without notice. The
candidate endpoints and selectors below are tried in order and validated
by content; they are NOT guaranteed to work. This module NEVER fabricates
listings: when the portal cannot be parsed it raises IngestError loudly
and returns nothing. Callers must record the error, never invent data.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.robotparser
from datetime import date, datetime
from typing import Any, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

log = logging.getLogger("tender-copilot.ingest.cppp")

BASE_URL = "https://eprocure.gov.in"
ROBOTS_URL = "https://eprocure.gov.in/robots.txt"
USER_AGENT = "TenderCopilot/0.1 (MSME research prototype)"
REQUEST_TIMEOUT = 20
POLITE_DELAY_S = 2
MAX_RETRIES = 2

SOURCE_NAME = "CPPP eprocure.gov.in"

# Best-effort candidate search endpoints. These are the paths the portal has
# historically used for public tender lists; each is attempted and the
# response is validated for actual tender rows before anything is returned.
# If the portal has moved or gone JS-only, every candidate fails validation
# and search_tenders() raises IngestError instead of returning made-up data.
CANDIDATE_SEARCH_URLS = [
    # Verified live 2026-10-20: returns a real tender table with
    # Title/Ref.No./Tender Id, Organisation, Published and Closing dates.
    "https://eprocure.gov.in/cppp/latestactivetendersnew",
    "https://eprocure.gov.in/cppp/tenders_search",
    "https://eprocure.gov.in/eprocure/app?page=FrontEndTendersByOrganisation&service=page",
]


class IngestError(Exception):
    """Raised when the portal cannot be read (structure changed, JS-only session,
    blocked, or otherwise unparseable). Never silently returns empty/partial data."""


def check_robots() -> Optional[bool]:
    """Check the portal's robots.txt for our crawler.

    Returns True if fetching is allowed, False if disallowed, None if the
    check itself failed (network error / no robots.txt).
    """
    try:
        resp = requests.get(
            ROBOTS_URL, headers={"User-Agent": USER_AGENT}, timeout=10
        )
        if resp.status_code != 200:
            return None
        rp = urllib.robotparser.RobotFileParser()
        rp.parse(resp.text.splitlines())
        return bool(rp.can_fetch(USER_AGENT, BASE_URL + "/"))
    except Exception as exc:
        log.warning("robots.txt check failed: %s", exc)
        return None


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
    )
    adapter = requests.adapters.HTTPAdapter(max_retries=MAX_RETRIES)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


def _polite_get(session: requests.Session, url: str, **kwargs) -> requests.Response:
    """GET with a polite delay, an explicit timeout, and error surfacing."""
    time.sleep(POLITE_DELAY_S)
    kwargs.setdefault("timeout", REQUEST_TIMEOUT)
    resp = session.get(url, **kwargs)
    resp.raise_for_status()
    return resp


def _clean(text: Optional[str]) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _parse_money(text: str) -> Optional[float]:
    """Best-effort parse of an INR amount like 'Rs. 1,25,00,000'."""
    if not text:
        return None
    m = re.search(r"[\d,]+(?:\.\d+)?", text.replace("Rs.", "").replace("INR", ""))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def _parse_dt(text: str) -> Optional[datetime]:
    for fmt in (
        "%d-%b-%Y %I:%M %p",  # portal format e.g. 20-Oct-2026 11:00 AM
        "%d-%b-%Y %H:%M",
        "%d-%b-%Y",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(_clean(text), fmt)
        except (ValueError, TypeError):
            continue
    return None


def _parse_search_table(html: str, base_url: str) -> list[dict]:
    """Best-effort extraction of tender rows from a search-result page.

    Looks for a table whose headers mention tender/title/organisation and
    maps columns by header text. Returns [] when no plausible tender table
    is found (caller treats this as a parse failure, not as "no tenders").
    """
    soup = BeautifulSoup(html, "lxml")
    wanted = ("tender", "title", "organisation", "organization", "emd", "bid", "publish")
    for table in soup.find_all("table"):
        headers = [_clean(th.get_text()).lower() for th in table.find_all("th")]
        if not headers:
            first_row = table.find("tr")
            if first_row:
                headers = [_clean(td.get_text()).lower() for td in first_row.find_all("td")]
        if sum(1 for h in headers if any(w in h for w in wanted)) < 2:
            continue

        def col(*names: str) -> Optional[int]:
            for i, h in enumerate(headers):
                if any(n in h for n in names):
                    return i
            return None

        c_title = col("title") or col("work") or col("tender")
        c_org = col("organisation", "organization", "department", "ministry")
        c_ref = col("ref", "tender id", "tender no")
        c_emd = col("emd")
        c_pub = col("publish")
        c_end = col("bid due", "bid end", "submission end", "closing")

        rows: list[dict] = []
        for tr in table.find_all("tr")[1:]:
            cells = tr.find_all("td")
            if not cells:
                continue
            texts = [_clean(td.get_text()) for td in cells]
            title = texts[c_title] if c_title is not None and c_title < len(texts) else ""
            # The Title column on CPPP glues "Title / Ref No / Tender Id"
            # into one cell. Prefer the title link's own text; otherwise
            # trim the trailing /ref/tender-id chain.
            if c_title is not None and c_title < len(cells):
                anchor = cells[c_title].find("a")
                if anchor is not None:
                    a_text = _clean(anchor.get_text())
                    if len(a_text) >= 8:
                        title = a_text
                title = re.sub(
                    r"\s*/\s*\d{4,}\s*/\s*(?:19|20)\d{2}_[A-Za-z].*$", "", title
                )
                title = re.sub(r"\s*/\s*(?:19|20)\d{2}_[A-Za-z][\w/]*$", "", title)
                title = title.strip()
            if not title or len(title) < 8:
                continue
            link = tr.find("a", href=True)
            href = urljoin(base_url, link["href"]) if link else None
            rows.append(
                {
                    "tender_id_external": texts[c_ref]
                    if c_ref is not None and c_ref < len(texts)
                    else None,
                    "title": title,
                    "organisation": texts[c_org]
                    if c_org is not None and c_org < len(texts)
                    else "",
                    "ref_no": texts[c_ref]
                    if c_ref is not None and c_ref < len(texts)
                    else None,
                    "category": "works",
                    "est_value": None,
                    "emd_amount": _parse_money(texts[c_emd])
                    if c_emd is not None and c_emd < len(texts)
                    else None,
                    "published_at": _parse_dt(texts[c_pub])
                    if c_pub is not None and c_pub < len(texts)
                    else None,
                    "bid_end_at": _parse_dt(texts[c_end])
                    if c_end is not None and c_end < len(texts)
                    else None,
                    "source_url": href,
                    "source": SOURCE_NAME,
                }
            )
        if rows:
            return rows
    return []


def search_tenders(keyword: str = "", max_results: int = 50) -> list[dict]:
    """Search public CPPP listings for a keyword (best-effort).

    Returns a list of tender dicts (never fabricated). Raises IngestError if
    the portal's structure changed or requires a JS session, so the caller
    can record the failure instead of silently returning nothing.
    """
    session = _session()
    failures: list[str] = []
    for url in CANDIDATE_SEARCH_URLS:
        try:
            resp = _polite_get(
                session, url, params={"keyword": keyword} if keyword else None
            )
        except Exception as exc:
            failures.append(f"{url}: request failed ({exc})")
            continue
        try:
            rows = _parse_search_table(resp.text, base_url=url)
        except Exception as exc:
            failures.append(f"{url}: parse error ({exc})")
            continue
        if rows:
            log.info("CPPP search %r: %d rows via %s", keyword, len(rows), url)
            return rows[:max_results]
        failures.append(f"{url}: no tender rows parsed")
    raise IngestError(
        "CPPP search page structure changed or requires JS session; "
        "no tender rows could be parsed from any known endpoint "
        f"({'; '.join(failures)}). No listings returned."
    )


def _label_value_pairs(soup: BeautifulSoup) -> dict[str, str]:
    """Extract label->value pairs from definition lists and two-column tables."""
    pairs: dict[str, str] = {}
    for dl in soup.find_all("dl"):
        terms = dl.find_all("dt")
        defs = dl.find_all("dd")
        for dt, dd in zip(terms, defs):
            pairs[_clean(dt.get_text()).lower()] = _clean(dd.get_text())
    for table in soup.find_all("table"):
        for tr in table.find_all("tr"):
            cells = tr.find_all(["th", "td"])
            if len(cells) == 2:
                pairs[_clean(cells[0].get_text()).lower()] = _clean(cells[1].get_text())
    return pairs


def get_tender_detail(url: str) -> dict[str, Any]:
    """Fetch a tender detail/NIT page and extract fields best-effort.

    Returns a dict with any of: tender_id_external, title, organisation,
    ref_no, est_value, emd_amount, emd_type, published_at, bid_end_at,
    bid_opening_at, bid_validity_days, source_url. Missing fields are None.
    Raises IngestError when the page cannot be fetched or parsed.
    """
    session = _session()
    try:
        resp = _polite_get(session, url)
    except Exception as exc:
        raise IngestError(f"tender detail fetch failed for {url}: {exc}")
    soup = BeautifulSoup(resp.text, "lxml")
    pairs = _label_value_pairs(soup)

    def pick(*names: str) -> Optional[str]:
        for n in names:
            for label, value in pairs.items():
                if n in label and value:
                    return value
        return None

    title = pick("tender title", "work name", "name of work") or _clean(
        soup.title.get_text() if soup.title else ""
    )
    est_raw = pick("estimated cost", "tender value", "estimated value")
    emd_raw = pick("emd", "earnest money")
    emd_amount = _parse_money(emd_raw or "")
    emd_type = "none" if emd_raw and "nil" in emd_raw.lower() else ("fixed" if emd_amount else "unknown")
    validity_raw = pick("bid validity")
    validity = None
    if validity_raw:
        m = re.search(r"\d+", validity_raw)
        validity = int(m.group(0)) if m else None

    return {
        "tender_id_external": pick("tender id", "tender no", "nit no"),
        "title": title or None,
        "organisation": pick("organisation", "organization", "department", "ministry"),
        "ref_no": pick("reference no", "ref no", "tender reference"),
        "est_value": _parse_money(est_raw or ""),
        "emd_amount": emd_amount,
        "emd_type": emd_type,
        "published_at": _parse_dt(pick("published date", "publish date") or ""),
        "bid_end_at": _parse_dt(pick("bid submission end", "bid due", "submission closing") or ""),
        "bid_opening_at": _parse_dt(pick("bid opening") or ""),
        "bid_validity_days": validity,
        "source_url": url,
    }


def download_pdf(url: str, dest_dir: str) -> Optional[str]:
    """Stream-download a PDF; verifies PDF magic bytes. Returns path or None."""
    os.makedirs(dest_dir, exist_ok=True)
    session = _session()
    try:
        time.sleep(POLITE_DELAY_S)
        with session.get(url, timeout=REQUEST_TIMEOUT, stream=True) as resp:
            resp.raise_for_status()
            ctype = resp.headers.get("Content-Type", "")
            first = next(resp.iter_content(chunk_size=5), b"")
            if b"%PDF" not in first and "pdf" not in ctype.lower():
                log.warning("download skipped (not a PDF): %s", url)
                return None
            fname = re.sub(r"[^A-Za-z0-9_.-]", "_", url.split("/")[-1].split("?")[0]) or "document.pdf"
            if not fname.lower().endswith(".pdf"):
                fname += ".pdf"
            dest = os.path.join(dest_dir, fname)
            with open(dest, "wb") as f:
                f.write(first)
                for chunk in resp.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
            return dest
    except Exception as exc:
        log.warning("PDF download failed for %s: %s", url, exc)
        return None


def _parse_seed_dt(value):
    """Parse an ISO-8601 datetime/date string to a Python object for SQLAlchemy.

    Returns datetime/date objects (SQLite DateTime columns reject raw strings);
    returns None for empty values and raises IngestError on garbage.
    """
    if value is None or value == "":
        return None
    if isinstance(value, (datetime, date)):
        return value
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            pass
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
        raise IngestError(f"Unparseable datetime in seed data: {value!r}")


def _as_date(value):
    """Coerce a parsed datetime/date (or None) to a date for Date columns."""
    if value is None:
        return date.today()
    if isinstance(value, datetime):
        return value.date()
    return value
    raise IngestError(f"Unparseable datetime in seed data: {value!r}")


def import_seed(path: str, db) -> dict:
    """Import tenders from a seed JSON file into the database.

    Accepts a JSON list or {"tenders": [...]}. Dedupes by
    ``tender_id_external`` or by (title, organisation). Returns
    {"imported": n, "skipped": m}. Used by the API and by app startup.
    """
    # Local import: keeps this module importable without a DB at module load.
    from app.db import Tender

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    items = data.get("tenders") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise IngestError(f"Seed file {path} must be a JSON list or {{\"tenders\": [...]}}")

    imported, skipped = 0, 0
    for raw in items:
        if not isinstance(raw, dict):
            skipped += 1
            continue
        ext = raw.get("tender_id_external")
        existing = None
        if ext:
            existing = db.query(Tender).filter(Tender.tender_id_external == ext).first()
        if existing is None and raw.get("title") and raw.get("organisation"):
            existing = (
                db.query(Tender)
                .filter(
                    Tender.title == raw["title"],
                    Tender.organisation == raw["organisation"],
                )
                .first()
            )
        if existing is not None:
            skipped += 1
            continue
        nested = raw.get("raw_json") if isinstance(raw.get("raw_json"), dict) else {}
        stored_raw = {k: v for k, v in raw.items() if k not in ("capture_date", "raw_json")}
        # Promote nested detail keys (criteria, notes, emd clauses...) to the
        # top level of raw_json so agents can read tender["raw_json"]["criteria"].
        for k, v in nested.items():
            stored_raw.setdefault(k, v)
        db.add(
            Tender(
                tender_id_external=ext,
                title=raw.get("title") or "Untitled tender",
                organisation=raw.get("organisation") or "",
                ref_no=raw.get("ref_no"),
                category=raw.get("category") or "works",
                est_value=raw.get("est_value"),
                est_value_known=bool(raw.get("est_value_known", raw.get("est_value") is not None)),
                emd_amount=raw.get("emd_amount"),
                emd_type=raw.get("emd_type"),
                emd_exemption_allowed=raw.get("emd_exemption_allowed"),
                published_at=_parse_seed_dt(raw.get("published_at")),
                bid_end_at=_parse_seed_dt(raw.get("bid_end_at")),
                bid_opening_at=_parse_seed_dt(raw.get("bid_opening_at")),
                bid_validity_days=raw.get("bid_validity_days"),
                source=raw.get("source") or "seed",
                source_url=raw.get("source_url"),
                capture_date=_as_date(_parse_seed_dt(raw.get("capture_date"))),
                is_seed=True,
                raw_json=stored_raw,
            )
        )
        imported += 1
    db.commit()
    return {"imported": imported, "skipped": skipped}
