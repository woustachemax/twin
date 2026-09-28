"""Best-effort PII redaction for text before it's sent to any LLM provider.

Regex-based on purpose: it catches the common, mechanically-recognizable shapes (an email
has an @, a US SSN is digits split 3-2-4, a credit card number Luhn-checks) rather than
trying to understand meaning. It will miss things a human reader would catch instantly, and
it will occasionally flag something that isn't actually PII. That tradeoff is intentional for
v1: a light, dependency-free regex layer beats no coverage at all, without pulling in an NER
model. Categories covered: email addresses, phone numbers, government-ID-shaped numbers (US
SSN, Indian Aadhaar), credit card numbers (regex + Luhn checksum), and street addresses (a
best-effort "<number> <words> <Street/Ave/Road/...>" heuristic — it won't catch PO boxes,
apartment-only lines, or addresses in scripts/formats it wasn't written for).

Scope: this is meant to run on user-supplied and locally-ingested text (chat input, calendar
titles, screen descriptions) before those become part of a request to an LLM provider. It
deliberately does NOT run on externally-fetched public data (flight search results, SEC
filing excerpts) — those are already public facts about companies or flights, not personal
data about the user, and blanket-redacting them would corrupt real public figures (a filed
company's own listed phone number or address, a filing's real dollar amounts) for no privacy
benefit. That split mirrors buddy.py's existing FLIGHT_LINE_RE / FILING_LINE_RE scrub
exemptions, which this module doesn't touch.
"""
import re

EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

# Candidates: either a compact/optionally-separated 10-digit US/Canada-style number
# (optional country code, optional parens around area code), or 2+ digit groups joined by a
# REQUIRED space/dot/dash/plus (covers most other groupings in practice — UK's 020-7946-0958,
# India's +91 98765 43210 or 098765-43210, etc.). The digit-count check in _redact_phones()
# below is what actually decides "is this phone-shaped", not the grouping itself; a bare,
# unseparated run of digits deliberately isn't matched here so it isn't mislabeled "phone"
# instead of "card" — the fallback in buddy.py's own scrub() still redacts it generically.
PHONE_CANDIDATE_RE = re.compile(
    r"(?<!\d)(?:\+\d{1,3}[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)"
    r"|(?<!\d)\+?\(?\d{2,4}\)?(?:[\s.-]\d{2,5}){1,4}(?!\d)"
)

# US SSN (XXX-XX-XXXX) and Indian Aadhaar (12 digits, grouped 4-4-4).
GOVT_ID_RE = re.compile(
    r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)"
    r"|(?<!\d)\d{4}[\s-]\d{4}[\s-]\d{4}(?!\d)"
)

# Candidate digit runs for card numbers; separators allowed only BETWEEN digits (not
# trailing) so both "4111 1111 1111 1111" and "4111111111111111" match without eating a
# trailing space or dash that isn't part of the number. Luhn-checked below to cut false
# positives on arbitrary long numbers (order/tracking IDs, account numbers, etc).
CARD_CANDIDATE_RE = re.compile(r"(?<!\d)\d(?:[ -]?\d){12,18}(?!\d)")

_STREET_SUFFIXES = (
    "street", "st", "avenue", "ave", "road", "rd", "boulevard", "blvd", "lane", "ln",
    "drive", "dr", "court", "ct", "place", "pl", "way", "terrace", "ter", "circle", "cir",
    "highway", "hwy", "square", "sq", "parkway", "pkwy",
)
ADDRESS_RE = re.compile(
    r"\b\d{1,6}\s+[A-Za-z0-9.'-]+(?:\s+[A-Za-z0-9.'-]+){0,4}\s+(?:" + "|".join(_STREET_SUFFIXES) + r")\b\.?",
    re.I,
)


def _luhn_ok(digits):
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def _redact_credit_cards(text):
    count = 0

    def repl(match):
        nonlocal count
        digits = re.sub(r"[ -]", "", match.group(0))
        if 13 <= len(digits) <= 19 and _luhn_ok(digits):
            count += 1
            return "[card]"
        return match.group(0)

    return CARD_CANDIDATE_RE.sub(repl, text), count


def _redact_phones(text):
    count = 0

    def repl(match):
        nonlocal count
        digits = re.sub(r"\D", "", match.group(0))
        # Floor of 9 (not the more permissive 7) specifically to keep 8-digit MM-DD-YYYY /
        # YYYY-MM-DD dates from being mistaken for phone numbers; real phone numbers are
        # almost always 9+ digits once grouped with separators or a country code.
        if 9 <= len(digits) <= 15:
            count += 1
            return "[phone]"
        return match.group(0)

    return PHONE_CANDIDATE_RE.sub(repl, text), count


def redact(text):
    """Redact PII from text before it goes to an LLM provider.

    Returns (redacted_text, findings), where findings is a list of {"category", "count"}
    dicts for whatever was actually found, in the order checked. Pure and side-effect-free:
    calling this never talks to an LLM, which is what makes it safe to use for a dry-run
    preview as well as the real redaction step."""
    if not text:
        return text, []
    findings = []

    text, n = EMAIL_RE.subn("[email]", text)
    if n:
        findings.append({"category": "email", "count": n})

    text, n = _redact_credit_cards(text)
    if n:
        findings.append({"category": "credit_card", "count": n})

    text, n = GOVT_ID_RE.subn("[gov-id]", text)
    if n:
        findings.append({"category": "government_id", "count": n})

    text, n = _redact_phones(text)
    if n:
        findings.append({"category": "phone", "count": n})

    text, n = ADDRESS_RE.subn("[address]", text)
    if n:
        findings.append({"category": "address", "count": n})

    return text, findings


def summarize(redacted_text, findings):
    """Chat-friendly report of what redact() found, for a dry-run demo (no LLM call)."""
    if not findings:
        return "Nothing flagged. This text would go out unchanged:\n\n" + redacted_text
    lines = [f"- {f['count']}x {f['category'].replace('_', ' ')}" for f in findings]
    return "Would redact:\n" + "\n".join(lines) + "\n\nRedacted text:\n" + redacted_text
