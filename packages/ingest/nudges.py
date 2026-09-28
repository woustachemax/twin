import hashlib
import re
from datetime import date, datetime, timedelta

SOON_HOURS = 24
MESSAGE_WINDOW_HOURS = 72

TOPICS = {
    "travel": re.compile(
        r"\b(?:flights?|fly|flying|trips?|travel(?:ling|ing)?|hotels?|airport|train|bus|booking|itinerary|passport|visa|"
        r"boarding|check-?in|vacation|holiday|hostel|airbnb|cab)\b", re.I),
    "appointment": re.compile(
        r"\b(?:doctor|dr|dentist|appointment|clinic|check-?up|hospital|therapy|therapist|prescription|vaccin\w*)\b", re.I),
    "meeting": re.compile(
        r"\b(?:meeting|call|stand-?up|sync|interview|presentation|demo|review|deck|slides|agenda|1:1|workshop|"
        r"class|exam|lecture)\b", re.I),
    "social": re.compile(
        r"\b(?:dinner|lunch|brunch|party|birthday|wedding|gift|cake|reservation|drinks|concert|movie)\b", re.I),
}

CUES = (
    ("change", re.compile(r"\b(?:cancel(?:l?ed)?|resched\w*|postpon\w*|delay(?:ed)?|moved|running late|no longer)\b", re.I)),
    ("payment", re.compile(
        r"\b(?:deposit|pay|paid|payment|advance|fees?|invoice|owe|dues?|transfer|split|venmo|upi|rent)\b", re.I)),
    ("bring", re.compile(r"\b(?:bring|carry|pack|packing|documents?|tickets?|passport|charger|forms?)\b", re.I)),
    ("reminder", re.compile(
        r"\b(?:don'?t forget|do not forget|remember|remind(?:er)?|make sure|need to|have to|must)\b", re.I)),
)
CUE_PHRASES = {
    "change": "a possible change of plans",
    "payment": "something about a payment",
    "bring": "something to bring or prepare",
    "reminder": "a reminder",
}
NUDGE_TITLE_LIMIT = 60


def topics_in(text):
    return {name for name, pattern in TOPICS.items() if pattern.search(text)}


def cue_in(text):
    for name, pattern in CUES:
        if pattern.search(text):
            return name
    return None


def is_soon(event, now):
    if event["all_day"]:
        return event["start"].date() in (now.date(), now.date() + timedelta(days=1))
    return now <= event["start"] <= now + timedelta(hours=SOON_HOURS)


def when_phrase(event, now):
    if event["all_day"]:
        return "today" if event["start"].date() == now.date() else "tomorrow"
    hours = (event["start"] - now).total_seconds() / 3600
    if hours < 1:
        return "in under an hour"
    if event["start"].date() == now.date():
        return "later today"
    return "tomorrow"


def nudge_key(event, cue):
    raw = f"{event['title']}|{event['start'].isoformat()}|{cue}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def find_nudges(events, messages, now=None, skip=None, seen=()):
    """Match upcoming events to recent messages, entirely in memory.

    Message text is only ever compared against keyword patterns. What comes back holds the
    event (shown locally), a vague cue category, and a dedupe key, never any message text.
    """
    now = now or datetime.now()
    cutoff = now - timedelta(hours=MESSAGE_WINDOW_HOURS)
    soon = sorted((e for e in events or [] if is_soon(e, now)), key=lambda e: e["start"])
    usable = []
    for message in messages or []:
        text, stamp = message.get("text"), message.get("timestamp")
        if not text or (stamp is not None and stamp < cutoff) or (skip is not None and skip(text)):
            continue
        cue = cue_in(text)
        if cue:
            usable.append((topics_in(text), cue))
    nudges = []
    for event in soon:
        event_topics = topics_in(event["title"])
        if not event_topics:
            continue
        cues = {cue for topics, cue in usable if topics & event_topics}
        for cue, _ in CUES:
            if cue not in cues:
                continue
            key = nudge_key(event, cue)
            if key not in seen:
                nudges.append({"event": event, "cue": cue, "key": key, "when": when_phrase(event, now)})
            break
    return nudges


def nudge_text(nudge, clean_title):
    return f"{clean_title[:NUDGE_TITLE_LIMIT]} is {nudge['when']}, and a recent message mentions {CUE_PHRASES[nudge['cue']]}."
