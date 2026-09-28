import re
from datetime import datetime

TOPICS = (
    ("payments", re.compile(
        r"\b(?:deposit|pay|paid|payment|advance|fees?|invoice|owe|dues?|transfer|split|venmo|upi|rent)\b", re.I)),
    ("travel", re.compile(
        r"\b(?:flights?|trips?|travel(?:ling|ing)?|hotels?|airport|train|booking|itinerary|passport|visa|boarding|"
        r"vacation|holiday|airbnb|cab)\b", re.I)),
    ("appointments", re.compile(
        r"\b(?:doctor|dentist|appointment|clinic|check-?up|hospital|therapy|prescription)\b", re.I)),
    ("work", re.compile(
        r"\b(?:meeting|stand-?up|sync|interview|presentation|demo|review|deck|slides|agenda|deadline|project|"
        r"client|report|standup)\b", re.I)),
    ("deliveries", re.compile(r"\b(?:deliver\w*|package|parcel|courier|shipped|order|arriv(?:ed|ing))\b", re.I)),
    ("plans", re.compile(
        r"\b(?:weekend|saturday|sunday|tonight|tomorrow|plans?|hang-?out|meet-?up|dinner|lunch|brunch|party|"
        r"birthday|wedding|drinks|movie|game)\b", re.I)),
)
URGENT_RE = re.compile(r"\b(?:urgent|asap|emergency|immediately|right now|important|call me)\b", re.I)
QUANTITIES = ((1, "one"), (2, "a couple of"), (5, "a few"))
MANY = "several"
NOUNS = {
    "payments": ("message about a payment", "messages about payments"),
    "travel": ("message about travel", "messages about travel"),
    "appointments": ("message about an appointment", "messages about appointments"),
    "work": ("message about work", "messages about work"),
    "deliveries": ("message about a delivery", "messages about deliveries"),
    "plans": ("message about plans", "messages about plans"),
    "other": ("other chat", "other chats"),
    "bank": ("bank alert", "bank alerts"),
}
NOTHING = "Nothing new in your Messages today."


def quantity(count):
    for limit, word in QUANTITIES:
        if count <= limit:
            return word
    return MANY


def phrase(topic, count):
    singular, plural = NOUNS[topic]
    return f"{quantity(count)} {singular if count == 1 else plural}"


def join(parts):
    if len(parts) <= 2:
        return " and ".join(parts)
    return ", ".join(parts[:-1]) + ", and " + parts[-1]


def summarize(messages, is_bank=None, today=None):
    """Count today's incoming messages by vague topic. Only counts leave this function, never text."""
    today = today or datetime.now().date()
    counts, urgent = {}, False
    for message in messages or []:
        text, stamp = message.get("text"), message.get("timestamp")
        if not text or stamp is None or stamp.date() != today or message.get("sender") == "Me":
            continue
        if is_bank is not None and is_bank(text):
            topic = "bank"
        else:
            topic = next((name for name, pattern in TOPICS if pattern.search(text)), "other")
            urgent = urgent or bool(URGENT_RE.search(text))
        counts[topic] = counts.get(topic, 0) + 1
    if not counts:
        return NOTHING
    order = [name for name, _ in TOPICS] + ["other", "bank"]
    parts = [phrase(topic, counts[topic]) for topic in order if topic in counts]
    tail = "One of them may be urgent." if urgent else "Nothing looks urgent."
    return f"Today you got {join(parts)}. {tail}"
