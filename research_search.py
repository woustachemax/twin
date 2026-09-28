"""SEC filing lookup via EDGAR (https://www.sec.gov). Public filings only: no brokerage
account linking, no portfolio tracking, no anything tied to the user's own finances.

Resolves a company name/ticker to a CIK via SEC's free company_tickers.json, then reads
that company's recent filings from EDGAR's public submissions API and fetches the actual
filing document text. No API key is needed, but SEC requires a descriptive User-Agent with
a real contact on every request, and a self-imposed rate limit under 10 requests/second.

The contact comes from SEC_EDGAR_CONTACT in the environment (or .env), or failing that,
config["edgar_contact"] (set once via buddy.py's onboarding prompt or /setup edgar). There
is deliberately no hardcoded fallback contact: a baked-in address would mean every
unconfigured install's EDGAR traffic gets attributed to whoever's email shipped in the
source, which is both wrong and a fast way to get that address rate-limited or blocked.
"""
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

from llm_providers import ssl_context

SEC_CONTACT_ENV = "SEC_EDGAR_CONTACT"
CONFIG_PATH = Path(os.environ.get("TWIN_CONFIG_PATH", "~/.twin/config.json")).expanduser()
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"
REQUEST_TIMEOUT = 25
MIN_REQUEST_INTERVAL = 0.15  # stays well under SEC's 10 requests/second limit
MAX_EXCERPT_CHARS = 16000

FORM_TOKEN_SRC = (
    r"10-?k(?:/a)?|10-?q|8-?k|def\s*14a|s-?1"
    r"|annual\s+report|quarterly\s+report|current\s+report|proxy\s+statement|proxy"
)
FORM_CANON = {
    "10k": "10-K", "10k/a": "10-K/A", "10q": "10-Q", "8k": "8-K",
    "def14a": "DEF 14A", "s1": "S-1",
    "annualreport": "10-K", "quarterlyreport": "10-Q", "currentreport": "8-K",
    "proxystatement": "DEF 14A", "proxy": "DEF 14A",
}
COMPANY_CHARS = r"[a-z0-9][a-z0-9 .,&'-]*?"
LEADIN_RE = re.compile(
    r"^(?:what(?:'s|s)?\s+(?:changed|new|different)\s+in\s+"
    r"|what(?:'s|s)?\s+(?:in|inside)\s+"
    r"|summar(?:ize|ise|y\s+of)\s+"
    r"|tell\s+me\s+about\s+"
    r"|give\s+me\s+a\s+summary\s+of\s+)",
    re.I,
)
RECENCY_RE = re.compile(r"\b(?:last|latest|most\s+recent|newest|recent)\b", re.I)
TRAILING_FILING_RE = re.compile(r"\s+filings?\s*$", re.I)
PATTERN_COMPANY_FIRST = re.compile(
    rf"^(?P<company>{COMPANY_CHARS})(?:'s|s'|\s+its)?\s+(?P<form>{FORM_TOKEN_SRC})\s*$", re.I,
)
PATTERN_FORM_FIRST = re.compile(
    rf"^(?P<form>{FORM_TOKEN_SRC})\s+(?:for|of|from)\s+(?P<company>{COMPANY_CHARS})\s*$", re.I,
)
NOT_A_COMPANY = ("me", "you", "us", "it", "this", "that", "them")

# A few well-known cases where the everyday name isn't a token of the SEC-registered title.
ALIASES = {"google": "alphabet", "facebook": "meta platforms"}

# Section a "what changed" / "summarize" question actually wants, per form type. Searched as
# plain text after HTML tags are stripped, so these match the filing's own item headers.
ITEM_HEADERS = {
    "10-K": (r"item\s*7\.?\s*management", r"item\s*7\b", r"item\s*1\.?\s*business"),
    "10-K/A": (r"item\s*7\.?\s*management", r"item\s*7\b", r"item\s*1\.?\s*business"),
    "10-Q": (r"item\s*2\.?\s*management", r"item\s*2\b", r"item\s*1\.?\s*financial"),
}

SKIP_TAGS = {"script", "style"}
NEWLINE_TAGS = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6"}

_ticker_cache = None
_ticker_cache_lock = threading.Lock()
_rate_lock = threading.Lock()
_last_request_at = 0.0

_UNSET = object()  # distinguishes "haven't checked config.json yet" from "checked, no contact set"
_contact_cache = _UNSET
_contact_cache_lock = threading.Lock()


class ResearchSearchError(Exception):
    def __init__(self, kind, detail=""):
        super().__init__(detail or kind)
        self.kind = kind
        self.detail = detail


def _canon_form(token):
    t = token.strip().lower()
    if t in ("proxy statement", "proxy"):
        return "DEF 14A"
    compact = re.sub(r"[\s-]", "", t)
    return FORM_CANON.get(compact)


def parse_filing_query(text):
    """"[lead-in] X's [recency] FORM [filing]" or "FORM for/of X" -> {"company_text", "form_type"}."""
    text = text.replace("’", "'").strip()
    if not text:
        return None
    stripped = LEADIN_RE.sub("", text, count=1).strip()
    stripped = RECENCY_RE.sub("", stripped)
    stripped = TRAILING_FILING_RE.sub("", stripped)
    stripped = re.sub(r"\s+", " ", stripped).strip(" .,!?")
    if not stripped:
        return None
    for pattern in (PATTERN_COMPANY_FIRST, PATTERN_FORM_FIRST):
        match = pattern.match(stripped)
        if not match:
            continue
        company = " ".join(match.group("company").split())
        form = _canon_form(match.group("form"))
        if company and form and company.lower() not in NOT_A_COMPANY:
            return {"company_text": company, "form_type": form}
    return None


def _read_configured_contact():
    """Reads config["edgar_contact"] from the same config file buddy.py uses. Best-effort:
    any problem reading it just means "not configured", handled the same as a missing file."""
    try:
        with open(CONFIG_PATH) as f:
            config = json.load(f)
    except (OSError, ValueError):
        return None
    contact = config.get("edgar_contact") if isinstance(config, dict) else None
    return contact.strip() if isinstance(contact, str) and contact.strip() else None


def _configured_contact():
    """Cached after the first read, since a single filing lookup makes several sequential
    SEC requests (ticker resolution, submissions, document fetch), each checking this. Call
    invalidate_contact_cache() after writing a new edgar_contact so the change is picked up
    without restarting the app."""
    global _contact_cache
    with _contact_cache_lock:
        if _contact_cache is _UNSET:
            _contact_cache = _read_configured_contact()
        return _contact_cache


def invalidate_contact_cache():
    """Call this right after writing a new config["edgar_contact"] to disk (e.g. from
    EdgarContactPrompt), so the next lookup re-reads it instead of serving a stale value
    cached from before it was set."""
    global _contact_cache
    with _contact_cache_lock:
        _contact_cache = _UNSET


def _user_agent():
    contact = os.environ.get(SEC_CONTACT_ENV, "").strip() or _configured_contact()
    if not contact:
        raise ResearchSearchError(
            "contact_not_configured",
            "no SEC_EDGAR_CONTACT env var and no edgar_contact in config",
        )
    return f"Twin research-lookup {contact}"


def _throttle():
    global _last_request_at
    with _rate_lock:
        now = time.monotonic()
        wait = _last_request_at + MIN_REQUEST_INTERVAL - now
        if wait > 0:
            time.sleep(wait)
        _last_request_at = time.monotonic()


def _failure_kind(status):
    if status == 429:
        return "rate_limit"
    if status == 404:
        return "not_found"
    return "api_error"


def _fetch(url, decode_json):
    headers = {"User-Agent": _user_agent()}  # raises contact_not_configured before we throttle for nothing
    _throttle()
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT, context=ssl_context()) as response:
            raw = response.read()
    except urllib.error.HTTPError as e:
        raise ResearchSearchError(_failure_kind(e.code), f"HTTP {e.code} for {url}")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ResearchSearchError("offline", str(getattr(e, "reason", e)))
    text = raw.decode("utf-8", "replace")
    if not decode_json:
        return text
    try:
        return json.loads(text)
    except ValueError:
        raise ResearchSearchError("api_error", f"couldn't parse response from {url}")


def _load_tickers():
    global _ticker_cache
    with _ticker_cache_lock:
        if _ticker_cache is None:
            data = _fetch(TICKERS_URL, decode_json=True)
            entries = []
            for value in (data or {}).values():
                cik, ticker, title = value.get("cik_str"), value.get("ticker"), value.get("title")
                if cik is None or not ticker or not title:
                    continue
                entries.append({"cik": int(cik), "ticker": str(ticker).upper(), "title": str(title)})
            _ticker_cache = entries
        return _ticker_cache


def resolve_company(text):
    """Company name or ticker -> {"cik", "ticker", "title"}. Raises ResearchSearchError("not_found") if no match."""
    key = re.sub(r"[^a-z0-9. ]", "", text.lower()).strip()
    key = re.sub(r"\s+", " ", key)
    key = ALIASES.get(key, key)
    entries = _load_tickers()
    key_upper = key.upper()
    for entry in entries:
        if entry["ticker"] == key_upper:
            return entry
    for entry in entries:
        if entry["title"].lower() == key:
            return entry
    starts = [e for e in entries if e["title"].lower().startswith(key)]
    if starts:
        return min(starts, key=lambda e: len(e["title"]))
    word_re = re.compile(rf"\b{re.escape(key)}\b")
    contains = [e for e in entries if word_re.search(e["title"].lower())]
    if contains:
        return min(contains, key=lambda e: len(e["title"]))
    raise ResearchSearchError("not_found", text)


def latest_filing(cik, form_type):
    """Most recent filing of form_type for cik -> dict with form/filingDate/accessionNumber/primaryDocument.

    Only searches EDGAR's "recent" filings window (roughly the last year or more of activity).
    A real company with no filing of this type in that window raises "no_filings" even if an
    older one exists further back in its history.
    """
    data = _fetch(SUBMISSIONS_URL.format(cik=cik), decode_json=True)
    recent = ((data or {}).get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    dates = recent.get("filingDate") or []
    report_dates = recent.get("reportDate") or []
    accessions = recent.get("accessionNumber") or []
    primary_docs = recent.get("primaryDocument") or []
    for i, form in enumerate(forms):
        if form.upper() == form_type:
            return {
                "form": form,
                "filingDate": dates[i] if i < len(dates) else "",
                "reportDate": report_dates[i] if i < len(report_dates) else "",
                "accessionNumber": accessions[i] if i < len(accessions) else "",
                "primaryDocument": primary_docs[i] if i < len(primary_docs) else "",
            }
    raise ResearchSearchError("no_filings", form_type)


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.chunks = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self._skip += 1
        elif tag in NEWLINE_TAGS:
            self.chunks.append("\n")

    def handle_startendtag(self, tag, attrs):
        if tag in NEWLINE_TAGS:
            self.chunks.append("\n")

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS and self._skip > 0:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip and data.strip():
            self.chunks.append(data)


def _html_to_text(html):
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        pass
    text = "".join(parser.chunks)
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def _select_excerpt(text, form, max_chars):
    """Jump to the section that actually matters for "what changed"/"summarize" (MD&A for
    10-Ks/10-Qs), rather than just the cover page. Filings list their own table of contents
    before the real section, so the LAST match of a header (not the first) is the real one."""
    patterns = ITEM_HEADERS.get(form.upper())
    if patterns:
        lower = text.lower()
        for pattern in patterns:
            matches = list(re.finditer(pattern, lower))
            if matches:
                start = matches[-1].start()
                return text[start:start + max_chars].strip()
    return text[:max_chars].strip()


def fetch_filing_excerpt(cik, filing, max_chars=MAX_EXCERPT_CHARS):
    primary_doc, accession = filing.get("primaryDocument"), filing.get("accessionNumber")
    if not primary_doc or not accession:
        raise ResearchSearchError("no_filings", "filing has no readable document attached")
    url = f"{ARCHIVES_BASE}/{int(cik)}/{accession.replace('-', '')}/{primary_doc}"
    html = _fetch(url, decode_json=False)
    text = _html_to_text(html)
    if not text:
        raise ResearchSearchError("api_error", "the filing document came back empty")
    return _select_excerpt(text, filing.get("form", ""), max_chars)


def format_filing_context(company, filing, excerpt):
    """Plain-text block for the model's system prompt. Every line is real, already-fetched filing text."""
    header = f"📄 {company['title']} ({company['ticker']}) — {filing['form']} filed {filing['filingDate']}"
    if filing.get("reportDate"):
        header += f", for the period ending {filing['reportDate']}"
    lines = [header]
    for para in excerpt.split("\n"):
        para = para.strip()
        if para:
            lines.append(f"📄 {para}")
    return "\n".join(lines)
