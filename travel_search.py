"""Flight search via Duffel (https://duffel.com). Search only: no booking/checkout here.

Reads DUFFEL_API_KEY from the environment (or .env). A `duffel_test_...` key runs against Duffel's
free sandbox, which returns synthetic flights on a fake "Duffel Airways" rather than real fares; a
`duffel_live_...` key (after Duffel's account verification) returns real prices from real airlines.
Either way the request/response shape is identical.
"""
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

from llm_providers import ssl_context

DUFFEL_KEY_ENV = "DUFFEL_API_KEY"
DUFFEL_BASE_URL = "https://api.duffel.com"
DUFFEL_VERSION = "v2"
REQUEST_TIMEOUT = 25
MAX_RESULTS = 5

WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

FALLBACK_PLACES = {
    "goa": "GOI", "mumbai": "BOM", "bombay": "BOM", "delhi": "DEL", "new delhi": "DEL",
    "bengaluru": "BLR", "bangalore": "BLR", "chennai": "MAA", "hyderabad": "HYD", "kolkata": "CCU",
    "pune": "PNQ", "kochi": "COK", "cochin": "COK", "ahmedabad": "AMD", "jaipur": "JAI", "lucknow": "LKO",
    "chandigarh": "IXC", "goa mopa": "GOX",
    "london": "LON", "new york": "NYC", "dubai": "DXB", "singapore": "SIN", "bangkok": "BKK",
    "paris": "PAR", "tokyo": "TYO", "san francisco": "SFO", "los angeles": "LAX",
}

DATE_PHRASE_RE = re.compile(
    r"\b(?:on\s+)?(next weekend|this weekend|tomorrow|today"
    r"|(?:next|this|coming)\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
    r"|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    re.I,
)
ROUTE_RE = re.compile(
    r"\bflights?\s+(?:from\s+(?P<origin>[a-z][a-z .'-]*?)\s+)?to\s+(?P<destination>[a-z][a-z .'-]*?)\s*[.!?]*$",
    re.I,
)


class TravelSearchError(Exception):
    def __init__(self, kind, detail=""):
        super().__init__(detail or kind)
        self.kind = kind
        self.detail = detail


def api_key():
    return os.environ.get(DUFFEL_KEY_ENV, "").strip() or None


def parse_flight_query(text):
    """"flights [from X] to Y [date phrase]" -> {"origin_text", "destination_text", "date_phrase"} or None."""
    text = text.replace("’", "'").strip()
    date_match = DATE_PHRASE_RE.search(text)
    date_phrase = date_match.group(1).lower() if date_match else None
    route_text = (text[:date_match.start()] + text[date_match.end():]) if date_match else text
    match = ROUTE_RE.search(route_text.strip())
    if not match:
        return None
    destination = " ".join(match.group("destination").split())
    origin = " ".join(match.group("origin").split()) if match.group("origin") else None
    if not destination or destination.lower() in ("me", "you", "us"):
        return None
    return {"origin_text": origin, "destination_text": destination, "date_phrase": date_phrase}


def resolve_date(phrase, today=None):
    today = today or date.today()
    if not phrase:
        return today
    phrase = phrase.lower().strip()
    if phrase == "today":
        return today
    if phrase == "tomorrow":
        return today + timedelta(days=1)
    this_saturday = today + timedelta(days=(5 - today.weekday()) % 7)
    if phrase == "this weekend":
        return this_saturday
    if phrase == "next weekend":
        return this_saturday + timedelta(days=7)
    words = phrase.split()
    weekday_word = words[-1]
    if weekday_word in WEEKDAYS:
        target = today + timedelta(days=(WEEKDAYS.index(weekday_word) - today.weekday()) % 7)
        return target + timedelta(days=7) if words[0] == "next" else target
    return today


def _headers(key):
    return {
        "Authorization": f"Bearer {key}",
        "Duffel-Version": DUFFEL_VERSION,
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "Twin/0.1",
    }


def _error_message(text):
    try:
        data = json.loads(text)
    except ValueError:
        return text.strip()[:200]
    errors = data.get("errors") if isinstance(data, dict) else None
    if isinstance(errors, list) and errors:
        first = errors[0] if isinstance(errors[0], dict) else {}
        return str(first.get("message") or first.get("title") or "").strip()[:200]
    return ""


def _failure_kind(status, text):
    if status in (401, 403):
        return "auth"
    if status == 429:
        return "rate_limit"
    if status == 422:
        return "not_found"
    return "api_error"


def _request(method, path, key, params=None, body=None):
    url = DUFFEL_BASE_URL + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers=_headers(key))
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT, context=ssl_context()) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", "replace")
        raise TravelSearchError(_failure_kind(e.code, text), _error_message(text) or f"HTTP {e.code}")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise TravelSearchError("offline", str(getattr(e, "reason", e)))
    except ValueError:
        raise TravelSearchError("api_error", "the response couldn't be read")


def _get(path, params, key):
    return _request("GET", path, key, params=params)


def _post(path, body, key, params=None):
    return _request("POST", path, key, params=params, body=body)


def resolve_place(text):
    """City/airport name -> IATA code, or None if nothing recognizable was found."""
    key = re.sub(r"[^a-z ]", "", text.lower()).strip()
    key = re.sub(r"\s+", " ", key)
    if key in FALLBACK_PLACES:
        return FALLBACK_PLACES[key]
    duffel_key = api_key()
    if not duffel_key:
        return None
    try:
        data = _get("/places/suggestions", {"query": text}, duffel_key)
    except TravelSearchError:
        return None
    candidates = data.get("data") or []
    for preferred in ("city", "airport"):
        for place in candidates:
            if place.get("type") == preferred and place.get("iata_code"):
                return place["iata_code"]
    for place in candidates:
        if place.get("iata_code"):
            return place["iata_code"]
    return None


def _format_clock(iso_ts):
    try:
        return datetime.fromisoformat(iso_ts).strftime("%-I:%M %p")
    except (ValueError, TypeError):
        return "?"


def _summarize_offer(offer):
    slice0 = (offer.get("slices") or [{}])[0]
    segments = slice0.get("segments") or []
    first, last = (segments[0], segments[-1]) if segments else ({}, {})
    return {
        "airline": (first.get("marketing_carrier") or {}).get("name") or "Unknown airline",
        "depart": _format_clock(first.get("departing_at", "")),
        "arrive": _format_clock(last.get("arriving_at", "")),
        "stops": max(len(segments) - 1, 0),
        "price": offer.get("total_amount"),
        "currency": offer.get("total_currency"),
    }


def search_flights(origin_code, destination_code, depart_date, max_results=MAX_RESULTS):
    duffel_key = api_key()
    if not duffel_key:
        raise TravelSearchError("no_key")
    body = {
        "data": {
            "slices": [{
                "origin": origin_code,
                "destination": destination_code,
                "departure_date": depart_date.isoformat(),
            }],
            "passengers": [{"type": "adult"}],
            "cabin_class": "economy",
        },
    }
    data = _post("/air/offer_requests", body, duffel_key, params={"return_offers": "true"})
    offers = ((data.get("data") or {}).get("offers")) or []
    return [_summarize_offer(offer) for offer in offers[:max_results]]


def format_flight_options(options, origin_code, destination_code, depart_date):
    """Plain-text block for the model's system prompt. Every line is a real, already-fetched fact."""
    header = f"✈ {origin_code} to {destination_code}, {depart_date:%a, %b %d}:"
    if not options:
        return header + "\n✈ No flights were found for that route and date."
    lines = [header]
    for option in options:
        stops = "nonstop" if option["stops"] == 0 else f"{option['stops']} stop(s)"
        lines.append(
            f"✈ {option['airline']}: {option['depart']}–{option['arrive']}, "
            f"{option['currency']} {option['price']}, {stops}"
        )
    return "\n".join(lines)
