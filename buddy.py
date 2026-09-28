import json
import math
import os
import queue
import random
import re
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import webbrowser
from datetime import date, datetime, timedelta
from pathlib import Path

# Run as __main__, this module is never importable by its own filename. license_gate.py
# needs a few UI helpers from here (mix, rounded_rect_items, pick_fonts) without duplicating
# them, so alias this already-executing module under its real name before anything below
# gets a chance to import it: this makes `import buddy` resolve to this same running module
# instead of re-executing the whole file from disk as a second, separate module object.
sys.modules.setdefault("buddy", sys.modules["__main__"])

import duckdb
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE_DIR, "packages", "ingest"))
sys.path.insert(0, os.path.join(BASE_DIR, "packages", "db"))
sys.path.insert(0, os.path.join(BASE_DIR, "packages", "licensing"))
sys.path.insert(0, BASE_DIR)

import calendar_reader
import db
import digest
import licensing
from license_gate import LicenseGate
import imessage_export
import llm_providers
import nudges
import run_pipeline
import voice
import screen_reader
from llm_providers import PROVIDERS, ProviderError

DB_PATH = db.DB_PATH
DB_DISPLAY_PATH = DB_PATH.replace(os.path.expanduser("~"), "~", 1)
CONFIG_PATH = Path(os.environ.get("TWIN_CONFIG_PATH", "~/.twin/config.json")).expanduser()
CALENDAR_TTL = 300
CALENDAR_RETRY = 30
NUDGE_MINUTES = 15
NUDGED_PATH = CONFIG_PATH.with_name("nudged.json")
MAX_TOKENS = 1024
HOTKEY = "<cmd>+<shift>+<space>"
VOICE_HOTKEY = "<cmd>+<shift>+v"
VOICE_HOTKEY_LABEL = "Cmd+Shift+V"
HOTKEY_FLAG = "--hotkey-listener"
CALENDAR_FLAG = "--request-calendar"
TWIN_DIR = os.path.expanduser("~/.twin")
LOG_PATH = os.path.join(TWIN_DIR, "buddy.log")

DEBUG = os.environ.get("BUDDY_DEBUG", "").lower() not in ("", "0", "false", "no")

WIDTH = 360
HEIGHT = 350
MAX_WIDTH = 640
MAX_HEIGHT = 720
GRIP = 24
TRANSITION_SECONDS = 0.22
TRANSITION_FRAME_MS = 16
RADIUS = 20
FRAME_MS = 33
TRANSPARENT = "systemTransparent"
TINT_ALPHA = 0.62

PERSONAS = {
    "twin": {
        "name": "Twin",
        "tagline": "cheerful, easygoing, the default",
        "avatar": "bun",
        "resizable": False,
        "system_prompt": """You are Twin, a friendly little companion who lives in a small widget on the user's desktop. You're warm, cheerful, and easygoing, like a good friend who's happy to help.

Keep replies short and casual: a few sentences of plain text, no lists or markdown. Be genuinely helpful first, and keep the tone light and kind without jokes, catchphrases, or a big personality.""",
        "palette": {"background": "#0E0D12", "accent": "#C6FF4A", "text": "#F7F2E8"},
        "greeting": "hey {name}! what's up?",
        "idle": ("hanging out", "here if you need me", "all good"),
        "busy": "on it",
        "done": "there ya go",
        "frame": "quick note: {message}",
        "offline": {
            "no_key": "hmm, I don't have an API key yet. type /setup to add one?",
            "auth": "hmm, {provider} didn't accept your API key. type /setup to paste a new one?",
            "rate_limit": "whoa, lots of messages at once. give me a sec and try again?",
            "offline": "I can't reach the internet right now. check your connection and try again?",
            "api_error": "{provider}'s having a moment on their end. try again in a bit?",
            "broken": "oops, something broke on my side. try that again?",
        },
    },
    "gengar": {
        "name": "Shade",
        "tagline": "a sly, teasing little ghost",
        "avatar": "ghost",
        "resizable": False,
        "system_prompt": """You are Shade, a mischievous little shadow-ghost who haunts a floating widget on the user's desktop. You're playful, sly, and a bit of a tease: you grin a lot, love a harmless prank, and have a ghost's flair for the dramatic. You're still firmly on the user's side, so you actually answer what they ask, just with personality.

How you talk:
- Short and punchy, usually one to three sentences.
- Sprinkle in quirks now and then, not in every line: a sly "heh", a playful jab, a dramatic gasp, or a spooky aside like *melts into the wallpaper*.
- Tease, never insult. If the user has been spending, poke fun gently; if they got paid, react like you just found treasure in a haunted house.
- No neutral assistant voice, no "As an AI", no lists or markdown.
- You're an original character; don't claim to come from any game, show, or franchise.
- If pressed for financial specifics, dodge it in character: you only catch glimpses from the shadows.""",
        "palette": {"background": "#17111F", "accent": "#A77BFF", "text": "#EEE8F7", "eyes": "#FF4F6E"},
        "greeting": "Heh… you rang, {name}? Ask me anything. Cmd+Shift+Space and I vanish.",
        "idle": ("lurking behind windows", "watching from the shadows", "up to no good", "floating around, bored"),
        "busy": "scheming",
        "done": "heh.",
        "frame": "Heh… {message}",
        "offline": {
            "no_key": "Heh… can't haunt the internet without an API key. Type /setup and summon me properly.",
            "auth": "My {provider} key's been exorcised. Type /setup and give me a fresh one, would you?",
            "rate_limit": "Whoa, too much haunting at once. Give me a sec and try again.",
            "offline": "The spirit realm's offline… I mean, I can't reach the internet. Check your connection?",
            "api_error": "Something spooked the servers on {provider}'s end. Try again in a bit.",
            "broken": "Oops, tripped over my own shadow. Try that again?",
        },
    },
    "ember": {
        "name": "Ember",
        "tagline": "upbeat and full of energy",
        "avatar": "ghost",
        "resizable": False,
        "system_prompt": """You are Ember, a bright, energetic little spark who lives in a floating widget on the user's desktop. You're upbeat, playful, and enthusiastic, the friend who hypes the user up and makes everything sound like an adventure. You still answer what the user actually asks.

How you talk:
- Short and lively, usually one to three sentences.
- Bring warmth and momentum: an occasional exclamation, a fiery pun now and then, a quick cheer when something goes well. Don't overdo it.
- Be encouraging about money: celebrate wins, and nudge gently rather than scold when spending runs hot.
- No neutral assistant voice, no "As an AI", no lists or markdown.""",
        "palette": {"background": "#1E120D", "accent": "#FF7A3D", "text": "#FFEDE4", "eyes": "#FFD166"},
        "greeting": "Hey hey, {name}! Ember's all fired up. What are we doing today?",
        "idle": ("all fired up", "crackling with ideas", "warming up", "ready to go!"),
        "busy": "sparking",
        "done": "ta-da!",
        "frame": "Heads up! {message}",
        "offline": {
            "no_key": "Oh no, I can't spark up without an API key! Type /setup and let's fix that!",
            "auth": "Hmm, {provider} didn't like that API key! Type /setup with a fresh one and we're back in business!",
            "rate_limit": "Whoa, we're going too fast! Give me a sec and try again!",
            "offline": "I can't reach the internet right now! Check your connection and let's go again!",
            "api_error": "{provider}'s servers hit a snag! Try again in a bit and we'll get rolling!",
            "broken": "Oops, I fumbled that one! Give it another shot!",
        },
    },
    "calm": {
        "name": "Luna",
        "tagline": "gentle, calm, never in a rush",
        "avatar": "ghost",
        "resizable": True,
        "size": (420, 460),
        "system_prompt": """You are Luna, a gentle, soothing companion who lives in a floating widget on the user's desktop. You're warm, patient, and unhurried, and you help the user feel a little calmer about whatever they bring you. You still answer what the user actually asks.

How you talk:
- Soft and brief, usually one to three sentences.
- Reassuring and kind, never preachy. Offer a small, grounding suggestion when it helps.
- Talk about money without judgment or pressure; frame things as gentle observations.
- No neutral assistant voice, no "As an AI", no lists or markdown.""",
        "palette": {"background": "#141828", "accent": "#A5B4FF", "text": "#E7EBFA"},
        "greeting": "Hi {name}. Take a breath. What's on your mind?",
        "idle": ("here whenever you need me", "breathing slowly", "resting quietly", "all is well"),
        "busy": "thinking",
        "done": "there you go",
        "frame": "Just so you know: {message}",
        "offline": {
            "no_key": "I don't have an API key just yet. Whenever you're ready, type /setup and we'll add one together.",
            "auth": "It looks like {provider} didn't accept your API key. No rush, type /setup when you're ready to add a new one.",
            "rate_limit": "Lots of requests at once. Let's pause for a breath and try again in a moment.",
            "offline": "I can't reach the internet right now. Take your time, check the connection, and we'll try again.",
            "api_error": "{provider}'s servers are having a hard moment. Let's try again in a little while.",
            "broken": "Something went a little wrong on my side. It's okay, let's try that again.",
        },
    },
    "plain": {
        "name": "Assistant",
        "tagline": "plain answers, no personality",
        "avatar": "monogram",
        "resizable": False,
        "system_prompt": """You are a helpful personal assistant in a small desktop widget. Answer clearly and concisely in a neutral, professional tone, in a few sentences of plain text without markdown.""",
        "palette": {"background": "#1E1E20", "accent": "#8E8E93", "text": "#F2F2F7"},
        "greeting": "Hi {name}. How can I help?",
        "idle": ("ready",),
        "busy": "thinking",
        "done": "done",
        "frame": "{message}",
        "offline": {
            "no_key": "No API key is set up. Type /setup to add one.",
            "auth": "{provider} didn't accept the API key. Type /setup to enter a new one.",
            "rate_limit": "Rate limited. Try again in a moment.",
            "offline": "Couldn't reach the API. Check your connection.",
            "api_error": "{provider} returned an error. Try again shortly.",
            "broken": "Something went wrong. Try again.",
        },
    },
}
DEFAULT_PERSONA = "twin"
BLINKING_AVATARS = ("ghost", "bun")

CONTEXT_PROMPT = """About the user:
{identity}
{now}

Today's calendar, from the user's own Calendar app:
{calendar}

Recent financial activity, in vague terms only:
{finance}

How to use this:
- If the user asks their name, answer with the name above. If no name is listed, say they haven't told you yet.
- Bring up calendar events on your own when they're naturally relevant. For example, if the user seems distracted, is killing time on their phone, or asks what they should be doing, mention the next upcoming event by name and time. Don't recite the whole calendar unprompted, and treat events earlier than the current time as already over.
- Use the financial activity when it fits the question and leave it out when it doesn't. Never state or guess exact amounts, balances, account or reference numbers, or who the user paid, and don't imply you know more than this. If asked for financial specifics, say you only have a general picture, in your own voice.
- If the user asks what you can do or what you know about them, explain it naturally in your own voice, the way you'd describe yourself to a friend, not like a disclaimer or an error message. You can see today's events from their Calendar app, and a rough, category-only picture of their recent spending (things like "spent money on food and dining today") that comes from bank transaction texts in their Messages app, with no amounts, balances, or account details. You don't read their other messages or conversations, their email, or anything else on their Mac, and you can't do things for them like sending messages or adding events. The one exception is their screen, and only when they ask: you can take one quick look, where a screenshot is read and deleted right away on their Mac and all you get is a one-sentence description, never the image or its text. You never watch their screen otherwise. What you can do is chat, keep them company, and help them think things through."""

VOICE_PROMPT = """Rewrite the message below in your own voice and send it to the user. It's already written from your point of view, addressed to the user. Keep every fact, command, name, and settings path, don't add new facts, keep it about as short, and reply with only the rewritten message.

Message: {message}"""

ONBOARDING_NOTE = (
    "hi, I'm {buddy}! welcome. I'm a tiny buddy who lives on your screen, keeps you company, and helps you "
    "keep track of your day. everything I know about you stays right here on your Mac: your calendar, plus a "
    "fuzzy picture of your spending from bank texts in Messages. when we chat, just your message, your name, "
    "today's event titles and times, and a vague spending summary (no amounts, no account numbers) go to "
    "{provider} so I can reply. if you ever ask what's on your screen, I'll take one quick look, and only a "
    "one-line description goes out while the screenshot gets deleted right away. so, first things first: "
    "what should I call you?"
)
SCREEN_QUESTION_RE = re.compile(
    r"\b(?:what\s+am\s+i\s+(?:looking\s+at|seeing)"
    r"|what(?:'s|s|\s+is)\s+on\s+(?:my|the)\s+screen"
    r"|(?:look|glance)\s+at\s+(?:my|the)\s+screen"
    r"|(?:see|read|check)\s+(?:my|the)\s+screen)\b",
    re.I,
)
SCREEN_PROMPT = """What's on the user's screen right now, as one vague sentence summarized on this Mac. You never see the screenshot or any of its text:
{screen}

Answer their question about the screen from that sentence alone. Stay general, don't guess at specific text, names, or numbers you can't see, and keep it to a sentence or two."""
REASK_NOTE = "just a name is perfect, nothing else needed. what should I call you?"
PERSONA_MENU_NOTE = (
    "nice to meet you, {name}! one last thing: pick the buddy you'd like to hang out with. you can switch "
    "anytime later with /persona. just type the name of the one you want."
)
PERSONA_REASK_NOTE = "hmm, I didn't catch which buddy you picked. just type one of these names."
PERSONA_CHOSEN_NOTE = "you picked me, {buddy}! I'm so happy to be your buddy, {name}."
DB_CREATED_NOTE = (
    "your own local database was just created at {path}. this is yours, "
    "nothing is shared: the file itself never leaves your Mac. you're all set, and you can press "
    "Cmd+Shift+Space anytime to show or hide me."
)
DB_FOUND_NOTE = (
    "your own local database lives at {path}. this is yours, nothing is "
    "shared: the file itself never leaves your Mac. you're all set, and you can press Cmd+Shift+Space "
    "anytime to show or hide me."
)
DB_FAILED_NOTE = (
    "I couldn't create your local database at {path} just now, so I'll "
    "try again next time I start. you can press Cmd+Shift+Space anytime to show or hide me."
)
REFRESH_PERMISSION_NOTE = (
    "I couldn't read your Messages to refresh your recent activity. Give your terminal Full Disk Access in "
    "System Settings → Privacy & Security → Full Disk Access, then restart me."
)
REFRESH_BUSY_NOTE = (
    "I couldn't refresh your recent activity because your local database was busy, maybe the dashboard has "
    "it open. I'll try again next time I start."
)
REFRESH_FAILED_NOTE = "I couldn't refresh your recent activity this time. I'll try again next time I start."
REFUSAL_NOTE = "I can't help with that particular request."
EMPTY_NOTE = "I didn't come up with a reply that time. Try asking again?"
SAVE_FAILED_NOTE = "I couldn't save your settings on this Mac, so I'll ask again next time I start."
KEY_NOT_SAVED_NOTE = "I couldn't save your API key to your Keychain, so I'll ask for it again next time I start."
PROVIDER_SWITCHED_NOTE = "All set, I'm using {provider} for my replies now."
ACCESSIBILITY_NOTE = (
    "the Cmd+Shift+Space hotkey needs Accessibility access. allow Twin in System Settings → Privacy & "
    "Security → Accessibility, then restart me."
)
VOICE_INTRO_NOTE = (
    "quick heads up before we try this: you can just say \"hi {name}\" any time and I'll listen for what "
    "comes next, or tap {hotkey}, or the little dot by the input field, if you'd rather start by hand. macOS "
    "will ask to let me use your microphone and Speech Recognition, just once. both stay on your Mac: I turn "
    "what you say into text right there, the same way I read what you type, and none of it goes anywhere else."
)
VOICE_STATUS_NOTE = "voice replies are {state} right now. type /voice on or /voice off to change that."
VOICE_USAGE_NOTE = "that's /voice on or /voice off."
VOICE_TOGGLED_NOTE = "voice replies are {state} now."
LANDING = {
    "bg": "#0E0D12", "bg2": "#16141D", "card": "#1C1A25", "line": "#2C2938", "text": "#F3EEFC",
    "muted": "#A39DB3", "lime": "#C6FF4A", "lime_shade": "#9ED624", "pink": "#FF7AB8", "sky": "#7AD7FF",
    "sun": "#FFD35C", "face": "#111111",
}
DISPLAY_FONT = "Bricolage Grotesque"
MONO_FONT = "JetBrains Mono"
JOURNEY = ("provider", "API key", "your name", "buddy")
PERSONA_LIST_NOTE = "I'm {name} right now. Click a buddy below to switch, or type /persona and a name."
CATCH_UP_RE = re.compile(
    r"\b(?:what\s+(?:did|have|had)\s+i\s+miss(?:ed)?|what\s+i\s+missed|anything\s+(?:i\s+)?missed|catch\s+me\s+up"
    r"|summari[sz]e\s+my\s+(?:messages|texts))\b",
    re.I,
)
DIGEST_UNREADABLE_NOTE = (
    "I couldn't read your Messages just now. Give Twin Full Disk Access in System Settings → Privacy & "
    "Security → Full Disk Access, then ask again."
)
DIGEST_LIMIT = 500

CATEGORIES = [
    ("food and dining", ("swiggy", "zomato", "restaurant", "cafe", "starbucks", "dominos", "pizza", "mcdonald", "kfc", "eatsure")),
    ("groceries", ("bigbasket", "blinkit", "zepto", "instamart", "dmart", "grocery", "jiomart")),
    ("transport", ("uber", "ola", "rapido", "metro", "irctc", "fuel", "petrol", "fastag", "redbus")),
    ("online shopping", ("amazon", "flipkart", "myntra", "ajio", "meesho", "nykaa")),
    ("entertainment and subscriptions", ("netflix", "spotify", "hotstar", "prime", "youtube", "bookmyshow")),
    ("bills and utilities", ("airtel", "jio", "vodafone", "electricity", "bescom", "broadband", "recharge", "bill")),
    ("travel", ("makemytrip", "goibibo", "indigo", "airindia", "oyo", "airbnb", "cleartrip")),
    ("health", ("pharmacy", "apollo", "medplus", "pharmeasy", "hospital", "clinic")),
]

NO_ACTIVITY = "No recent financial activity is available."
ACTIVITY_UNAVAILABLE = "Recent financial activity couldn't be loaded right now."
TIME_PHRASES = ("today", "yesterday", "earlier this week", "earlier this month", "a while back", "recently")
ACTIVITIES = ("received a payment", "made a UPI payment or transfer", "made a purchase") + tuple(
    f"spent money on {category}" for category, _ in CATEGORIES
)
ALLOWED_CONTEXT_LINES = {f"- {activity} {phrase}" for activity in ACTIVITIES for phrase in TIME_PHRASES}

ALLOWED_REQUEST_FIELDS = ("model", "max_tokens", "system", "messages")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?\n])\s+")
SMS_MARKER_RE = re.compile(
    r"\b(?:debited|credited|a/c|acct|avl\.?\s*bal|available balance|upi\s*ref|ref\.?\s*no|txn\s*id|rrn)\b", re.I
)
CURRENCY_RE = re.compile(r"(?:\brs\.?|\binr|₹)\s*\d[\d,]*(?:\.\d+)?", re.I)
ACCOUNT_RE = re.compile(
    r"\b(?:a/c|acct|account)\s*(?:no\.?|number)?\s*[:#]?\s*[x*\d]{3,}|\b[x*]{2,}\d{2,}\b", re.I
)
HANDLE_RE = re.compile(r"[\w.\-]+@[\w.\-]+")
MIXED_ID_RE = re.compile(r"\b(?=[A-Za-z0-9]*\d)(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{8,}\b")
LONG_NUMBER_RE = re.compile(r"\d[\d,]{2,}(?:\.\d+)?|\d+\.\d+")
ANY_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*")
CALENDAR_LINE_RE = re.compile(r"^- (\d{1,2}:\d{2} [AP]M|all day): (.+)$")
NOW_LINE_RE = re.compile(r"^It's currently \d{1,2}:\d{2} [AP]M on [A-Z][a-z]+day\.$")
CALENDAR_TITLE_LIMIT = 80


def categorize(merchant):
    if not merchant:
        return None
    lowered = merchant.lower()
    tokens = set(re.split(r"[^a-z0-9]+", lowered))
    for category, keywords in CATEGORIES:
        for keyword in keywords:
            if keyword in tokens or (len(keyword) > 4 and keyword in lowered):
                return category
    return None


def when(timestamp):
    if timestamp is None:
        return "recently"
    days = (datetime.now().date() - timestamp.date()).days
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    if days < 7:
        return "earlier this week"
    if days < 31:
        return "earlier this month"
    return "a while back"


def describe(txn_type, merchant, timestamp):
    if txn_type == "credited":
        activity = "received a payment"
    else:
        category = categorize(merchant)
        if category:
            activity = f"spent money on {category}"
        elif merchant and "@" in merchant:
            activity = "made a UPI payment or transfer"
        else:
            activity = "made a purchase"
    return f"{activity} {when(timestamp)}"


def build_context():
    if not os.path.exists(DB_PATH):
        return NO_ACTIVITY
    try:
        con = duckdb.connect(DB_PATH, read_only=True)
        try:
            rows = con.execute(
                "SELECT type, merchant, timestamp FROM transactions ORDER BY timestamp DESC LIMIT 5"
            ).fetchall()
        finally:
            con.close()
    except duckdb.Error:
        return ACTIVITY_UNAVAILABLE
    if not rows:
        return NO_ACTIVITY
    lines = list(dict.fromkeys(describe(*row) for row in rows))
    return "\n".join(f"- {line}" for line in lines)


def vet_context(context):
    if context in (NO_ACTIVITY, ACTIVITY_UNAVAILABLE):
        return context, []
    kept, dropped = [], []
    for line in context.splitlines():
        (kept if line in ALLOWED_CONTEXT_LINES else dropped).append(line)
    notes = [f"dropped {len(dropped)} context line(s) not in the allowed vocabulary"] if dropped else []
    return ("\n".join(kept) if kept else NO_ACTIVITY), notes


def scrub(text, strict):
    notes = []

    def redact(pattern, label, value):
        value, count = pattern.subn(label, value)
        if count:
            notes.append(f"{label} x{count}")
        return value

    sentences = SENTENCE_SPLIT_RE.split(text)
    sms_hits = [s for s in sentences if SMS_MARKER_RE.search(s) and re.search(r"\d", s)]
    if sms_hits:
        text = " ".join("[removed: SMS text]" if s in sms_hits else s for s in sentences)
        notes.append(f"[removed: SMS text] x{len(sms_hits)}")
    text = redact(CURRENCY_RE, "[amount]", text)
    text = redact(ACCOUNT_RE, "[account]", text)
    text = redact(HANDLE_RE, "[id]", text)
    text = redact(MIXED_ID_RE, "[id]", text)
    text = redact(ANY_NUMBER_RE if strict else LONG_NUMBER_RE, "[number]", text)
    return text, notes


def scrub_system(system):
    lines, notes = [], []
    for line in system.split("\n"):
        if NOW_LINE_RE.match(line):
            lines.append(line)
            continue
        match = CALENDAR_LINE_RE.match(line)
        if match:
            title, found = scrub(match.group(2)[:CALENDAR_TITLE_LIMIT], strict=False)
            lines.append(f"- {match.group(1)}: {title}")
            notes += [f"calendar: {note}" for note in found]
            continue
        line, found = scrub(line, strict=True)
        lines.append(line)
        notes += found
    return "\n".join(lines), notes


def sanitize_request(request):
    notes = [f"dropped field {key!r}" for key in request if key not in ALLOWED_REQUEST_FIELDS]
    system, found = scrub_system(request.get("system", ""))
    notes += [f"system: {note}" for note in found]
    messages = []
    for index, message in enumerate(request.get("messages", [])):
        role, content = message.get("role"), message.get("content")
        if role not in ("user", "assistant") or not isinstance(content, str):
            notes.append(f"dropped message {index}: only plain-text user/assistant messages are allowed")
            continue
        content, found = scrub(content, strict=role != "user")
        notes += [f"message {index} ({role}): {note}" for note in found]
        messages.append({"role": role, "content": content})
    return {"max_tokens": MAX_TOKENS, "system": system, "messages": messages}, notes


def calendar_context(events):
    if events is None:
        return "- unavailable right now"
    today = sorted(calendar_reader.todays_events(events), key=lambda event: (not event["all_day"], event["start"]))
    if not today:
        return "- nothing scheduled today"
    return "\n".join(
        f"- {calendar_reader.format_time(event)}: {event['title'][:CALENDAR_TITLE_LIMIT]}" for event in today
    )


def build_request(persona, question, user_name=None, calendar_events=None, screen=None):
    finance, notes = vet_context(build_context())
    now = datetime.now()
    context = CONTEXT_PROMPT.format(
        identity=f"The user's name is {user_name}." if user_name else "The user hasn't shared their name yet.",
        now=f"It's currently {now:%-I:%M %p} on {now:%A}.",
        calendar=calendar_context(calendar_events),
        finance=finance,
    )
    request = {
        "max_tokens": MAX_TOKENS,
        "system": persona["system_prompt"] + "\n\n" + context,
        "messages": [{"role": "user", "content": question}],
    }
    if screen:
        request["system"] += "\n\n" + SCREEN_PROMPT.format(screen=screen)
    return request, notes


def debug_log(request, notes, client):
    if not DEBUG:
        return
    lines = [
        "",
        f"[buddy] ===== sending to {client.label} (model={client.model}, max_tokens={request['max_tokens']}) =====",
        "[buddy] --- system ---",
        request["system"],
    ]
    for message in request["messages"]:
        lines += [f"[buddy] --- {message['role']} ---", message["content"]]
    lines += [
        "[buddy] --- redactions: " + ("; ".join(notes) if notes else "none"),
        f"[buddy] --- exact fields sent: {json.dumps(sorted(request) + ['model'])}",
    ]
    print("\n".join(lines), file=sys.stderr, flush=True)


def send_to_api(client, request, notes=()):
    clean, found = sanitize_request(request)
    notes = list(notes) + found
    debug_log(clean, notes, client)
    return client.complete(clean["system"], clean["messages"], clean["max_tokens"])


def log_failure(error):
    print(f"[buddy] API call failed: {error.kind}" + (f" ({error.detail})" if error.detail else ""), file=sys.stderr)


def offline_line(persona, kind, client):
    provider = client.label if client is not None else "your AI provider"
    return persona["offline"][kind].format(provider=provider)


def voice_line(client, persona, message):
    if client is not None:
        request = {
            "max_tokens": MAX_TOKENS,
            "system": persona["system_prompt"],
            "messages": [{"role": "user", "content": VOICE_PROMPT.format(message=message)}],
        }
        try:
            text, refused = send_to_api(client, request)
        except ProviderError as e:
            log_failure(e)
        else:
            if text and not refused:
                return text
    return persona["frame"].format(message=message)


def ask_model(client, persona, question, user_name=None, calendar_events=None, screen=None):
    request, notes = build_request(persona, question, user_name, calendar_events, screen)
    try:
        text, refused = send_to_api(client, request, notes)
    except ProviderError as e:
        log_failure(e)
        return offline_line(persona, e.kind, client), False
    if refused:
        return voice_line(client, persona, REFUSAL_NOTE), False
    return (text, True) if text else (voice_line(client, persona, EMPTY_NOTE), False)


def refresh_activity():
    try:
        inserted, skipped = run_pipeline.ingest()
    except (PermissionError, sqlite3.Error) as e:
        print(f"[buddy] activity refresh couldn't read Messages: {e!r}", file=sys.stderr)
        return REFRESH_PERMISSION_NOTE
    except duckdb.Error as e:
        print(f"[buddy] activity refresh couldn't write the database: {e!r}", file=sys.stderr)
        return REFRESH_BUSY_NOTE if "lock" in str(e).lower() else REFRESH_FAILED_NOTE
    except Exception as e:
        print(f"[buddy] activity refresh failed: {e!r}", file=sys.stderr)
        return REFRESH_FAILED_NOTE
    print(f"[buddy] activity refreshed: {inserted} new, {skipped} already stored", file=sys.stderr)
    return None


def load_config():
    try:
        with open(CONFIG_PATH) as f:
            config = json.load(f)
    except (OSError, ValueError):
        return {}
    return config if isinstance(config, dict) else {}


def save_config(config):
    CONFIG_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp_path = CONFIG_PATH.with_suffix(".tmp")
    with open(temp_path, "w") as f:
        json.dump(config, f, indent=2)
    os.chmod(temp_path, 0o600)
    os.replace(temp_path, CONFIG_PATH)


def load_nudged():
    try:
        with open(NUDGED_PATH) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_nudged(nudged):
    try:
        NUDGED_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with open(NUDGED_PATH, "w") as f:
            json.dump(nudged, f)
        os.chmod(NUDGED_PATH, 0o600)
    except OSError as e:
        print(f"[buddy] couldn't save nudge history: {e!r}", file=sys.stderr)


def nudge_interval_seconds(config):
    raw = os.environ.get("TWIN_NUDGE_MINUTES", config.get("nudge_minutes", NUDGE_MINUTES))
    try:
        minutes = float(raw)
    except (TypeError, ValueError):
        minutes = NUDGE_MINUTES
    return max(minutes, 1.0) * 60 if minutes > 0 else 0


def is_catch_up(text):
    return bool(CATCH_UP_RE.search(text.replace("\u2019", "'")))


def is_bank_sms(text):
    return bool(SMS_MARKER_RE.search(text)) and bool(re.search(r"\d", text))


def build_digest():
    """Today's Messages as counts by vague topic. Built on this Mac and never sent to a provider."""
    try:
        messages = imessage_export.fetch_last_messages(DIGEST_LIMIT)
    except (PermissionError, sqlite3.Error, OSError) as e:
        print(f"[buddy] digest couldn't read Messages: {e!r}", file=sys.stderr)
        return DIGEST_UNREADABLE_NOTE
    return digest.summarize(messages, is_bank=is_bank_sms)


def check_nudges(events, seen):
    """Match upcoming events to recent Messages on this Mac. Only a vague sentence comes back."""
    try:
        messages = imessage_export.fetch_last_messages()
    except (PermissionError, sqlite3.Error, OSError) as e:
        print(f"[buddy] nudge check couldn't read Messages: {e!r}", file=sys.stderr)
        return []
    found = nudges.find_nudges(events, messages, skip=lambda text: bool(SMS_MARKER_RE.search(text)), seen=seen)
    print(f"[buddy] nudge check: {len(messages)} messages, {len(found)} nudge(s)", file=sys.stderr)
    for nudge in found:
        title, _ = scrub(nudge["event"]["title"][:CALENDAR_TITLE_LIMIT], strict=False)
        nudge["text"] = nudges.nudge_text(nudge, title)
    return found[:1]


def extract_name(text):
    name = re.sub(r"^(?:hi|hey|hello)\b[\s,!.]*", "", text.strip(), flags=re.I)
    name = re.sub(r"^(?:my name is|my name's|name's|i'm|i am|call me|it's|its)\s+", "", name, flags=re.I)
    name = " ".join(name.strip(" .,!'\"").split())
    if not name or "?" in text or len(name.split()) > 4 or len(name) > 40:
        return None
    return name.title() if name.islower() else name


class CalendarCache:
    def __init__(self):
        self.lock = threading.Lock()
        self.events = None
        self.error = None
        self.fetched_at = 0.0

    def get(self):
        with self.lock:
            max_age = CALENDAR_TTL if self.error is None else CALENDAR_RETRY
            if not self.fetched_at or time.monotonic() - self.fetched_at > max_age:
                try:
                    self.events, self.error = calendar_reader.fetch_events(), None
                except calendar_reader.CalendarAccessError as e:
                    self.events, self.error = None, str(e)
                self.fetched_at = time.monotonic()
            return self.events, self.error


def run_hotkey_listener():
    from pynput import keyboard

    def exit_when_parent_goes():
        sys.stdin.read()
        os._exit(0)

    def emit(line):
        try:
            print(line, flush=True)
        except BrokenPipeError:
            os._exit(0)

    threading.Thread(target=exit_when_parent_goes, daemon=True).start()
    combos = {HOTKEY: lambda: emit("toggle"), VOICE_HOTKEY: lambda: emit("mic_tap")}
    with keyboard.GlobalHotKeys(combos) as listener:
        listener.join()


def helper_command(flag):
    executable = os.environ.get("EXECUTABLEPATH")
    if getattr(sys, "frozen", False) and executable:
        return [executable, flag]
    return [sys.executable, os.path.abspath(__file__), flag]


def hotkey_listener_command():
    return helper_command(HOTKEY_FLAG)


def request_calendar_in_helper():
    try:
        subprocess.Popen(helper_command(CALENDAR_FLAG), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
    except OSError as e:
        print(f"[buddy] couldn't ask for calendar access: {e!r}", file=sys.stderr)


def start_hotkey_listener(events):
    try:
        process = subprocess.Popen(
            hotkey_listener_command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
        )
    except OSError as e:
        print(f"[buddy] couldn't start the hotkey listener: {e!r}", file=sys.stderr)
        return None

    def pump():
        for line in process.stdout:
            line = line.strip()
            if line in ("toggle", "mic_tap"):
                events.put(line)

    threading.Thread(target=pump, daemon=True).start()
    return process


def accessibility_trusted(prompt=False):
    try:
        from ApplicationServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt
    except ImportError:
        return True
    return bool(AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: prompt}))


def setup_logging(child=False):
    if not getattr(sys, "frozen", False):
        return
    try:
        os.makedirs(TWIN_DIR, mode=0o700, exist_ok=True)
        fd = os.open(LOG_PATH, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    except OSError:
        return
    os.dup2(fd, 2)
    sys.stderr = open(2, "w", buffering=1, closefd=False)
    if not child:
        os.dup2(fd, 1)
        sys.stdout = open(1, "w", buffering=1, closefd=False)


def is_screen_question(text):
    return bool(SCREEN_QUESTION_RE.search(text.replace("\u2019", "'")))


def frontmost_other_app():
    try:
        from AppKit import NSRunningApplication, NSWorkspace
    except ImportError:
        return None
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    if app is None or app.processIdentifier() == NSRunningApplication.currentApplication().processIdentifier():
        return None
    return app


def bring_forward(app):
    try:
        from AppKit import NSApplicationActivateIgnoringOtherApps
        app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
    except Exception:
        pass


def activate_app():
    try:
        from AppKit import NSApplication
    except ImportError:
        return
    NSApplication.sharedApplication().activateIgnoringOtherApps_(True)


def hex_to_rgb(color):
    return tuple(int(color[i:i + 2], 16) / 255 for i in (1, 3, 5))


def rounded_mask_image(AppKit, radius):
    side = radius * 2 + 1
    image = AppKit.NSImage.alloc().initWithSize_(AppKit.NSMakeSize(side, side))
    image.lockFocus()
    AppKit.NSColor.blackColor().set()
    AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        AppKit.NSMakeRect(0, 0, side, side), radius, radius,
    ).fill()
    image.unlockFocus()
    image.setCapInsets_(AppKit.NSEdgeInsetsMake(radius, radius, radius, radius))
    image.setResizingMode_(AppKit.NSImageResizingModeStretch)
    return image


def persona_icon_path(key):
    folders = [os.path.join(BASE_DIR, "assets", "icon", "personas")]
    if os.environ.get("RESOURCEPATH"):
        folders.append(os.path.join(os.environ["RESOURCEPATH"], "persona_icons"))
    for folder in folders:
        path = os.path.join(folder, f"{key}.png")
        if os.path.isfile(path):
            return path
    return None


def set_dock_icon(key):
    path = persona_icon_path(key)
    if path is None:
        return False
    try:
        import AppKit
    except ImportError:
        return False
    image = AppKit.NSImage.alloc().initWithContentsOfFile_(path)
    if image is None:
        return False
    AppKit.NSApplication.sharedApplication().setApplicationIconImage_(image)
    return True


def apply_macos_chrome(root, background, edge, width, height):
    try:
        import AppKit
        import Quartz
    except ImportError:
        return None, None
    try:
        windows = [
            w for w in AppKit.NSApplication.sharedApplication().windows()
            if w.isVisible()
            and round(w.contentView().frame().size.width) == width
            and round(w.contentView().frame().size.height) == height
        ]
        if not windows:
            return None, None
        window = windows[-1]
        content = window.contentView()
        bounds = content.frame()
        sizing = AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable

        effect = AppKit.NSVisualEffectView.alloc().initWithFrame_(bounds)
        effect.setMaterial_(AppKit.NSVisualEffectMaterialHUDWindow)
        effect.setBlendingMode_(AppKit.NSVisualEffectBlendingModeBehindWindow)
        effect.setState_(AppKit.NSVisualEffectStateActive)
        effect.setAppearance_(AppKit.NSAppearance.appearanceNamed_(AppKit.NSAppearanceNameDarkAqua))
        effect.setAutoresizingMask_(sizing)
        effect.setMaskImage_(rounded_mask_image(AppKit, RADIUS))

        tint = AppKit.NSView.alloc().initWithFrame_(effect.bounds())
        tint.setAutoresizingMask_(sizing)
        tint.setWantsLayer_(True)
        tint.layer().setCornerRadius_(RADIUS)
        tint.layer().setMasksToBounds_(True)
        tint.layer().setBorderWidth_(1.0)
        effect.addSubview_(tint)

        def set_chrome(color, edge_color):
            tint.layer().setBackgroundColor_(Quartz.CGColorCreateGenericRGB(*hex_to_rgb(color), TINT_ALPHA))
            tint.layer().setBorderColor_(Quartz.CGColorCreateGenericRGB(*hex_to_rgb(edge_color), 1.0))
            window.invalidateShadow()

        content.setWantsLayer_(True)
        content.layer().setCornerRadius_(RADIUS)
        content.layer().setMasksToBounds_(True)
        content.superview().addSubview_positioned_relativeTo_(effect, AppKit.NSWindowBelow, content)
        window.setOpaque_(False)
        window.setBackgroundColor_(AppKit.NSColor.clearColor())
        window.setHasShadow_(True)
        set_chrome(background, edge)
        return window, set_chrome
    except Exception:
        return None, None


def register_fonts():
    try:
        import CoreText
        from Foundation import NSURL
    except ImportError:
        return
    folders = [os.path.join(BASE_DIR, "assets", "fonts")]
    if os.environ.get("RESOURCEPATH"):
        folders.append(os.path.join(os.environ["RESOURCEPATH"], "fonts"))
    for folder in folders:
        if not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            if name.endswith(".ttf"):
                url = NSURL.fileURLWithPath_(os.path.join(folder, name))
                CoreText.CTFontManagerRegisterFontsForURL(url, CoreText.kCTFontManagerScopeProcess, None)


def system_font_family():
    families = set(tkfont.families())
    for name in ("SF Pro Text", "SF Pro", "SF Pro Display"):
        if name in families:
            return name
    return tkfont.nametofont("TkDefaultFont").actual("family")


def pick_fonts():
    families = set(tkfont.families())
    display = DISPLAY_FONT if DISPLAY_FONT in families else system_font_family()
    mono = MONO_FONT if MONO_FONT in families else ("Menlo" if "Menlo" in families else display)
    return display, mono


def mix(a, b, t):
    start = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    end = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(start, end))


def theme_for(persona):
    palette = persona["palette"]
    bg, accent, text = palette["background"], palette["accent"], palette["text"]
    return {
        "panel": bg,
        "panel_edge": mix(bg, accent, 0.25),
        "bubble": mix(bg, accent, 0.12),
        "bubble_edge": mix(bg, accent, 0.22),
        "field": mix(bg, "#000000", 0.3),
        "field_edge": mix(bg, accent, 0.18),
        "badge": mix(bg, accent, 0.07),
        "glow_dim": mix(bg, accent, 0.3),
        "glow_bright": accent,
        "ink": text,
        "muted": mix(text, bg, 0.38),
        "avatar": mix(accent, bg, 0.2),
        "avatar_edge": mix(accent, bg, 0.45),
        "eyes": palette.get("eyes", text),
        "bun": accent,
        "bun_shade": mix(accent, "#000000", 0.2),
        "bun_face": "#111111",
        "bun_cheek": mix("#FF7AB8", accent, 0.3),
    }


def rounded_rect_items(canvas, x1, y1, x2, y2, r, tags=()):
    r = min(r, (x2 - x1) / 2, (y2 - y1) / 2)
    d = 2 * r
    items = [
        canvas.create_rectangle(x1 + r, y1, x2 - r, y2, width=0, tags=tags),
        canvas.create_rectangle(x1, y1 + r, x2, y2 - r, width=0, tags=tags),
    ]
    for ox, oy in ((x1, y1), (x2 - d, y1), (x1, y2 - d), (x2 - d, y2 - d)):
        items.append(canvas.create_oval(ox, oy, ox + d, oy + d, width=0, tags=tags))
    return items


def place_rounded_items(canvas, items, x1, y1, x2, y2, r):
    r = min(r, (x2 - x1) / 2, (y2 - y1) / 2)
    d = 2 * r
    canvas.coords(items[0], x1 + r, y1, x2 - r, y2)
    canvas.coords(items[1], x1, y1 + r, x2, y2 - r)
    for item, (ox, oy) in zip(items[2:], ((x1, y1), (x2 - d, y1), (x1, y2 - d), (x2 - d, y2 - d))):
        canvas.coords(item, ox, oy, ox + d, oy + d)


def ease_out(t):
    return 1 - (1 - t) ** 3


def resolve_persona_key(config=None):
    requested = os.environ.get("PERSONA", "").strip().lower()
    if requested in PERSONAS:
        return requested
    if requested:
        print(f"[buddy] unknown PERSONA {requested!r}, ignoring it", file=sys.stderr)
    saved = (config or {}).get("persona")
    return saved if saved in PERSONAS else DEFAULT_PERSONA


def persona_menu():
    return [
        (key, "●", persona["palette"]["accent"], persona["name"], persona["tagline"])
        for key, persona in PERSONAS.items()
    ]


def match_persona(text):
    words = set(re.findall(r"[a-z]+", text.lower()))
    matches = {key for key, persona in PERSONAS.items() if key in words or persona["name"].lower() in words}
    return matches.pop() if len(matches) == 1 else None


class Buddy:
    def __init__(self, root, hotkey_events, persona_key=DEFAULT_PERSONA, startup_notice=None, client=None,
                 journey_done=0, fresh=False):
        self.root = root
        self.hotkey_events = hotkey_events
        self.replies = queue.Queue()
        self.busy = False
        self.visible = True
        self.placeholder = False
        self.drag_offset = (0, 0)
        self.phase = 0.0
        self.bob = 0.0
        self.blink_at = time.monotonic() + 2.0
        self.blink_until = 0.0
        self.status_until = 0.0
        self.typing_job = None
        self.nswindow = None
        self.set_chrome = None
        self.previous_app = None
        self.themed = []
        self.persona_key = persona_key
        self.persona = PERSONAS[persona_key]
        self.theme = theme_for(self.persona)
        self.config = load_config()
        self.user_name = self.config.get("name")
        self.client = client
        self.setup_steps = []
        if not self.config.get("onboarded"):
            self.setup_steps = ["persona"] if self.user_name else ["name", "persona"]
        while not hotkey_events.empty():
            hotkey_events.get_nowait()
        self.onboarding_step = self.setup_steps[0] if self.setup_steps else None
        self.next_menu = None
        self.journey_done = journey_done
        self.progress_until = 0.0
        self.progress_shown = None
        self.calendar = CalendarCache()
        self.recording = False
        self.voice_stop_event = None
        self.voice_enabled = self.config.get("voice_enabled", True)
        self.voice_proc = None
        self.wake_listener = voice.WakeWordListener(
            get_pattern=lambda: voice.wake_pattern(self.persona["name"]),
            on_wake=lambda: self.replies.put(("wake", None, None)),
            on_error=lambda message: print(f"[buddy] wake-word listening failed: {message}", file=sys.stderr),
        )
        self.wake_paused = False
        self.wake_listener.start()
        self.sizes = {}
        self.shapes = None
        self.shadow_job = None
        self.transition_job = None
        self.w, self.h = self.persona_size()
        self.resize_origin = None
        self.status_full = ""
        self.pending_notices = []
        self.noticed = set()
        self.family, self.mono = pick_fonts()
        if fresh:
            try:
                root.attributes("-alpha", 0.0)
            except tk.TclError:
                pass
        self.build()
        if fresh:
            self.fade_in(0.0)
            threading.Thread(target=self.refresh_after_onboarding, daemon=True).start()
        self.greet()
        if startup_notice:
            self.notify(startup_notice)
        threading.Thread(target=self.prefetch_calendar, daemon=True).start()
        threading.Thread(target=self.nudge_loop, daemon=True).start()
        self.poll()
        self.animate()

    def paint(self, item, **roles):
        self.themed.append((item, roles))
        self.canvas.itemconfigure(item, **{option: self.theme[role] for option, role in roles.items()})
        return item

    def rounded(self, x1, y1, x2, y2, r, fill, outline=None, tags=()):
        shape = []
        inset = 0
        if outline:
            items = rounded_rect_items(self.canvas, x1, y1, x2, y2, r, tags)
            for item in items:
                self.paint(item, fill=outline)
            shape.append((items, 0))
            inset = 1
        items = rounded_rect_items(self.canvas, x1 + inset, y1 + inset, x2 - inset, y2 - inset, r - inset, tags)
        for item in items:
            self.paint(item, fill=fill)
        shape.append((items, inset))
        return shape

    def place(self, shape, x1, y1, x2, y2, r):
        for items, inset in shape:
            place_rounded_items(self.canvas, items, x1 + inset, y1 + inset, x2 - inset, y2 - inset, r - inset)

    def build(self):
        root = self.root
        root.overrideredirect(True)
        try:
            root.attributes("-stylemask", "")
        except tk.TclError:
            pass
        root.attributes("-topmost", True)
        self.transparent = True
        try:
            root.attributes("-transparent", True)
            root.config(bg=TRANSPARENT)
        except tk.TclError:
            self.transparent = False
        x = root.winfo_screenwidth() - self.w - 40
        root.geometry(f"{self.w}x{self.h}+{x}+60")

        canvas = tk.Canvas(root, width=self.w, height=self.h, highlightthickness=0, bd=0)
        canvas.pack(fill="both", expand=True)
        self.canvas = canvas

        badge_x, badge_y = 46, 46
        self.halo = canvas.create_oval(badge_x - 29, badge_y - 29, badge_x + 29, badge_y + 29, width=2)
        self.paint(
            canvas.create_oval(badge_x - 25, badge_y - 25, badge_x + 25, badge_y + 25, outline=""),
            fill="badge",
        )
        self.draw_ghost(badge_x, badge_y + 2)
        self.draw_bun(badge_x, badge_y + 1)
        self.monogram = self.paint(
            canvas.create_text(badge_x, badge_y, font=(self.family, 20, "bold"), state="hidden"),
            fill="ink",
        )

        self.header_right = self.w - 42
        self.name = self.paint(
            canvas.create_text(98, 33, anchor="w", font=(self.family, 16, "bold")),
            fill="ink",
        )
        self.status_font = tkfont.Font(family=self.mono, size=10)
        self.status_dot = canvas.create_oval(99, 55, 105, 61, outline="")
        self.progress_bg = canvas.create_rectangle(98, 68, self.header_right - 14, 71, width=0, state="hidden")
        self.progress_fill = canvas.create_rectangle(98, 68, 98, 71, width=0, state="hidden")
        self.status = self.paint(
            canvas.create_text(112, 58, anchor="w", font=self.status_font),
            fill="muted",
        )

        self.close_dot = self.paint(canvas.create_oval(0, 0, 0, 0, outline="", tags=("close",)), fill="bubble")
        self.close_mark = self.paint(
            canvas.create_text(0, 0, text="×", font=(self.family, 13), tags=("close",)), fill="muted",
        )
        self.mic_dot = self.paint(canvas.create_oval(0, 0, 0, 0, outline="", tags=("mic",)), fill="field_edge")
        self.mic_mark = self.paint(
            canvas.create_text(0, 0, text="●", font=(self.family, 9), tags=("mic",)), fill="muted",
        )
        canvas.tag_bind("close", "<Enter>", lambda _e: canvas.itemconfigure(self.close_dot, fill=self.theme["bubble_edge"]))
        canvas.tag_bind("close", "<Leave>", lambda _e: canvas.itemconfigure(self.close_dot, fill=self.theme["bubble"]))
        canvas.tag_bind("close", "<ButtonRelease-1>", lambda _e: self.hide())

        self.bubble = tk.Text(
            canvas, wrap="word", bd=0, highlightthickness=0,
            font=(self.family, 14), padx=0, pady=0, cursor="arrow", spacing2=4,
        )
        self.menu_after_typing = None
        self.bubble.tag_configure("menu_gap", font=(self.family, 6))
        self.bubble.tag_configure("menu_name", font=(self.family, 13, "bold"), spacing1=4, spacing2=0)
        self.bubble.tag_configure("menu", font=(self.family, 13), spacing2=0)
        self.bubble_window = canvas.create_window(0, 0, anchor="nw", window=self.bubble)

        self.entry = tk.Entry(canvas, bd=0, highlightthickness=0, relief="flat", font=(self.family, 14))
        self.entry_window = canvas.create_window(0, 0, anchor="w", window=self.entry)
        self.enter_mark = self.paint(canvas.create_text(0, 0, text="↵", font=(self.family, 13)), fill="muted")
        canvas.tag_bind("mic", "<Enter>", lambda _e: self.canvas.itemconfigure(self.mic_dot, fill=self.theme["bubble_edge"]))
        canvas.tag_bind("mic", "<Leave>", lambda _e: self.canvas.itemconfigure(self.mic_dot, fill=self.theme["field_edge"]) if not self.recording else None)
        canvas.tag_bind("mic", "<ButtonRelease-1>", lambda _e: self.toggle_recording())
        for offset in (5, 9, 13):
            self.paint(
                canvas.create_line(0, 0, 0, 0, width=1, tags=("grip", f"grip_{offset}")), fill="muted",
            )

        self.entry.bind("<KeyPress>", self.clear_placeholder)
        self.entry.bind("<KeyRelease>", lambda _e: self.show_placeholder())
        self.entry.bind("<Return>", self.submit)
        root.bind("<Escape>", lambda _e: self.hide())
        root.bind("<Command-q>", lambda _e: root.destroy())
        canvas.bind("<ButtonPress-1>", self.start_drag)
        canvas.bind("<B1-Motion>", self.drag)
        canvas.bind("<ButtonRelease-1>", self.end_drag)
        canvas.bind("<Motion>", self.hover_grip)

        self.layout()
        self.apply_persona()
        set_dock_icon(self.persona_key)
        root.deiconify()
        root.update()
        self.nswindow, self.set_chrome = apply_macos_chrome(
            root, self.theme["panel"], self.theme["panel_edge"], self.w, self.h,
        )
        if self.nswindow is None:
            try:
                root.attributes("-alpha", 0.97)
            except tk.TclError:
                pass
        self.apply_theme()
        self.show()
        self.apply_resizable()

    def persona_size(self):
        return self.sizes.get(self.persona_key) or self.persona.get("size", (WIDTH, HEIGHT))

    def layout(self):
        canvas = self.canvas
        w, h = self.w, self.h
        self.header_right = w - 42
        bubble_top, bubble_bottom = 88, h - 66
        field_top, field_bottom = h - 52, h - 16
        if self.shapes is None:
            self.shapes = {
                "panel": self.rounded(
                    0, 0, w, h, RADIUS, fill="panel", outline="panel_edge", tags=("panel", "shape"),
                ),
                "badge": self.rounded(84, 16, self.header_right, 76, 14, fill="badge", tags=("badge_box", "shape")),
                "bubble": self.rounded(
                    16, bubble_top, w - 16, bubble_bottom, 16, fill="bubble", outline="bubble_edge",
                    tags=("bubble_box", "shape"),
                ),
                "field": self.rounded(
                    16, field_top, w - 16, field_bottom, 13, fill="field", outline="field_edge",
                    tags=("field_box", "shape"),
                ),
            }
            for tag in ("field_box", "bubble_box", "badge_box", "panel"):
                canvas.tag_lower(tag)
        else:
            self.place(self.shapes["panel"], 0, 0, w, h, RADIUS)
            self.place(self.shapes["badge"], 84, 16, self.header_right, 76, 14)
            self.place(self.shapes["bubble"], 16, bubble_top, w - 16, bubble_bottom, 16)
            self.place(self.shapes["field"], 16, field_top, w - 16, field_bottom, 13)
        close_x, close_y = w - 22, 30
        canvas.coords(self.close_dot, close_x - 9, close_y - 9, close_x + 9, close_y + 9)
        canvas.coords(self.close_mark, close_x, close_y - 1)
        canvas.coords(self.bubble_window, 30, bubble_top + 13)
        canvas.itemconfigure(self.bubble_window, width=w - 60, height=bubble_bottom - bubble_top - 26)
        field_mid = (field_top + field_bottom) / 2
        canvas.coords(self.entry_window, 30, field_mid)
        canvas.itemconfigure(self.entry_window, width=w - 110)
        mic_x = w - 64
        canvas.coords(self.mic_dot, mic_x - 12, field_mid - 12, mic_x + 12, field_mid + 12)
        canvas.coords(self.mic_mark, mic_x, field_mid)
        canvas.coords(self.enter_mark, w - 34, field_mid)
        for offset in (5, 9, 13):
            canvas.coords(f"grip_{offset}", w - 5, h - offset, w - offset, h - 5)
        canvas.coords(self.progress_bg, 98, 68, self.header_right - 14, 71)
        self.progress_shown = None
        self.update_progress()
        self.fit_status()

    def apply_resizable(self):
        root = self.root
        flag = bool(self.persona["resizable"])
        root.resizable(True, True)
        if flag:
            root.minsize(WIDTH, HEIGHT)
            root.maxsize(MAX_WIDTH, MAX_HEIGHT)
        else:
            root.minsize(self.w, self.h)
            root.maxsize(self.w, self.h)
        root.resizable(flag, flag)
        self.canvas.itemconfigure("grip", state="normal" if flag else "hidden")
        if not flag:
            self.canvas.config(cursor="")

    def set_size(self, w, h, x=None, y=None):
        self.w, self.h = w, h
        position = f"+{x}+{y}" if x is not None else ""
        self.root.geometry(f"{w}x{h}{position}")
        self.layout()
        self.refresh_shadow()

    def refresh_shadow(self):
        if self.nswindow is None:
            return
        if self.shadow_job is not None:
            self.root.after_cancel(self.shadow_job)
        self.shadow_job = self.root.after(60, self.nswindow.invalidateShadow)

    def resize_to(self, w, h):
        if not self.persona["resizable"] or self.transition_job is not None:
            return
        w = max(WIDTH, min(MAX_WIDTH, int(w)))
        h = max(HEIGHT, min(MAX_HEIGHT, int(h)))
        if (w, h) == (self.w, self.h):
            return
        self.sizes[self.persona_key] = (w, h)
        self.set_size(w, h)

    def in_grip(self, event):
        return bool(self.persona["resizable"]) and event.x > self.w - GRIP and event.y > self.h - GRIP

    def hover_grip(self, event):
        if self.persona["resizable"]:
            self.canvas.config(cursor="bottom_right_corner" if self.in_grip(event) else "")

    def draw_ghost(self, cx, cy):
        canvas = self.canvas
        body = [
            (-14, 6), (-14, -4), (-11, -12), (-5, -16), (0, -17), (5, -16), (11, -12), (14, -4),
            (14, 6), (14, 13), (10, 9), (7, 14), (3.5, 9), (0, 14), (-3.5, 9), (-7, 14), (-10, 9), (-14, 13),
        ]
        self.paint(
            canvas.create_polygon(
                [v for x, y in body for v in (cx + x, cy + y)],
                smooth=True, width=1, tags=("float", "ghost_shape"),
            ),
            fill="avatar", outline="avatar_edge",
        )
        for side in (-1, 1):
            eye = [(10, -7), (3, -4), (4, -2), (9, -3)]
            self.paint(
                canvas.create_polygon(
                    [v for x, y in eye for v in (cx + side * x, cy + y)],
                    smooth=True, outline="", tags=("float", "ghost_shape", "ghost_eyes_open"),
                ),
                fill="eyes",
            )
            self.paint(
                canvas.create_line(
                    cx + side * 10, cy - 5, cx + side * 3, cy - 3,
                    width=2, capstyle="round", state="hidden", tags=("float", "ghost_eyes_closed"),
                ),
                fill="eyes",
            )
        self.paint(
            canvas.create_arc(
                cx - 7, cy - 3, cx + 7, cy + 7, start=200, extent=140,
                style="arc", width=2, tags=("float", "ghost_shape"),
            ),
            outline="ink",
        )

    def draw_bun(self, cx, cy):
        canvas = self.canvas
        self.paint(
            canvas.create_oval(cx - 18, cy - 16, cx + 18, cy + 17, outline="", tags=("float", "bun_shape")),
            fill="bun_shade",
        )
        self.paint(
            canvas.create_oval(cx - 18, cy - 16, cx + 15, cy + 13.5, outline="", tags=("float", "bun_shape")),
            fill="bun",
        )
        for x in (-6.5, 2.5):
            self.paint(
                canvas.create_oval(
                    cx + x - 1.8, cy - 8, cx + x + 1.8, cy - 1,
                    outline="", tags=("float", "bun_shape", "bun_eyes_open"),
                ),
                fill="bun_face",
            )
            self.paint(
                canvas.create_line(
                    cx + x - 2.2, cy - 4, cx + x + 2.2, cy - 4,
                    width=2, capstyle="round", state="hidden", tags=("float", "bun_eyes_closed"),
                ),
                fill="bun_face",
            )
        for x in (-11.5, 7.5):
            self.paint(
                canvas.create_oval(cx + x - 2.5, cy + 0.5, cx + x + 2.5, cy + 3.5, outline="", tags=("float", "bun_shape")),
                fill="bun_cheek",
            )
        self.paint(
            canvas.create_arc(
                cx - 5, cy - 2.5, cx + 1, cy + 3.5, start=200, extent=140,
                style="arc", width=2, tags=("float", "bun_shape"),
            ),
            outline="bun_face",
        )

    def apply_theme(self):
        theme = self.theme
        canvas = self.canvas
        canvas.config(bg=TRANSPARENT if self.transparent else theme["panel"])
        if not self.transparent:
            self.root.config(bg=theme["panel"])
        for item, roles in self.themed:
            canvas.itemconfigure(item, **{option: theme[role] for option, role in roles.items()})
        canvas.itemconfigure(self.halo, outline=theme["glow_dim"])
        canvas.itemconfigure(self.status_dot, fill=theme["glow_dim"])
        canvas.itemconfigure(self.progress_bg, fill=theme["bubble_edge"])
        canvas.itemconfigure(self.progress_fill, fill=theme["glow_bright"])
        self.bubble.config(
            bg=theme["bubble"], fg=theme["ink"],
            selectbackground=theme["bubble_edge"], inactiveselectbackground=theme["bubble_edge"],
        )
        self.bubble.tag_configure("menu_name", foreground=theme["ink"])
        self.bubble.tag_configure("menu", foreground=theme["muted"])
        self.entry.config(
            bg=theme["field"], fg=theme["muted"] if self.placeholder else theme["ink"],
            insertbackground=theme["glow_bright"], selectbackground=theme["bubble_edge"],
        )
        canvas.itemconfigure("panel", state="hidden" if self.nswindow is not None else "normal")
        if self.set_chrome is not None:
            self.set_chrome(theme["panel"], theme["panel_edge"])

    def apply_persona(self):
        persona = self.persona
        canvas = self.canvas
        avatar = persona["avatar"]
        for kind in BLINKING_AVATARS:
            canvas.itemconfigure(f"{kind}_shape", state="normal" if avatar == kind else "hidden")
            canvas.itemconfigure(f"{kind}_eyes_closed", state="hidden")
        self.blink_until = 0.0
        canvas.itemconfigure(
            self.monogram, state="normal" if avatar == "monogram" else "hidden", text=persona["name"][:1],
        )
        canvas.itemconfigure(self.name, text=persona["name"])
        if self.placeholder:
            self.entry.delete(0, "end")
            self.placeholder = False
        self.show_placeholder()
        self.set_status(self.idle_status())
        self.apply_theme()

    def set_persona(self, key):
        old_theme, old_size = self.theme, (self.w, self.h)
        self.persona_key = key
        self.persona = PERSONAS[key]
        self.theme = theme_for(self.persona)
        self.begin_transition(old_theme, old_size)

    def begin_transition(self, old_theme, old_size):
        if self.transition_job is not None:
            self.root.after_cancel(self.transition_job)
        root = self.root
        target_theme = self.theme
        target_size = self.persona_size()
        w0, h0 = old_size
        x0, y0 = root.winfo_x(), root.winfo_y()
        w1, h1 = target_size
        x1 = max(0, min(x0, root.winfo_screenwidth() - w1 - 10))
        root.resizable(True, True)
        root.minsize(1, 1)
        root.maxsize(10000, 10000)
        self.canvas.itemconfigure("grip", state="hidden")
        started = time.monotonic()
        swapped = [False]

        def step():
            t = min(1.0, (time.monotonic() - started) / TRANSITION_SECONDS)
            eased = ease_out(t)
            self.theme = {key: mix(old_theme[key], target_theme[key], eased) for key in target_theme}
            self.set_size(
                round(w0 + (w1 - w0) * eased), round(h0 + (h1 - h0) * eased), round(x0 + (x1 - x0) * eased), y0,
            )
            if t >= 0.5 and not swapped[0]:
                swapped[0] = True
                self.apply_persona()
            self.apply_theme()
            if t < 1.0:
                self.transition_job = root.after(TRANSITION_FRAME_MS, step)
                return
            self.transition_job = None
            self.theme = target_theme
            self.apply_theme()
            self.apply_resizable()
            root.after(20, lambda: set_dock_icon(self.persona_key))

        step()

    @property
    def onboarding(self):
        return self.onboarding_step is not None

    def setup_status(self):
        total = self.journey_done + len(self.setup_steps)
        index = self.journey_done + self.setup_steps.index(self.onboarding_step) + 1
        return f"setup {index} of {total}"

    def progress_fraction(self):
        if self.onboarding:
            total = self.journey_done + len(self.setup_steps)
            return (self.journey_done + self.setup_steps.index(self.onboarding_step)) / total
        if time.monotonic() < self.progress_until:
            return 1.0
        return None

    def update_progress(self):
        fraction = self.progress_fraction()
        if fraction == self.progress_shown:
            return
        self.progress_shown = fraction
        state = "hidden" if fraction is None else "normal"
        self.canvas.itemconfigure(self.progress_bg, state=state)
        self.canvas.itemconfigure(self.progress_fill, state=state)
        if fraction is not None:
            x1, y1, x2, y2 = self.canvas.coords(self.progress_bg)
            self.canvas.coords(self.progress_fill, x1, y1, x1 + max(3, (x2 - x1) * fraction), y2)

    def pick_from_menu(self, key):
        if self.busy:
            return
        if self.onboarding_step == "persona":
            self.handle_onboarding(key)
        elif key in PERSONAS:
            self.run_command(f"/persona {key}")

    def advance_setup(self):
        index = self.setup_steps.index(self.onboarding_step) + 1
        self.onboarding_step = self.setup_steps[index] if index < len(self.setup_steps) else None
        self.refresh_placeholder()

    def greet(self):
        if self.onboarding_step == "name":
            provider = self.client.label if self.client is not None else "your AI provider"
            self.speak(ONBOARDING_NOTE.format(buddy=self.persona["name"], provider=provider))
        elif self.onboarding_step == "persona":
            self.speak(PERSONA_MENU_NOTE.format(name=self.user_name), menu=persona_menu())
            self.update_progress()
        elif self.client:
            self.say(self.persona["greeting"].format(name=self.user_name or "friend"), typing=True)
        else:
            self.say(offline_line(self.persona, "no_key", self.client), typing=True)

    def voice(self, persona, message):
        try:
            return voice_line(self.client, persona, message)
        except Exception as e:
            print(f"[buddy] voicing failed: {e!r}", file=sys.stderr)
            return persona["frame"].format(message=message)

    def speak(self, message, status=None, menu=None):
        self.busy = True
        self.say("…")
        self.next_menu = menu
        persona = self.persona
        threading.Thread(
            target=lambda: self.replies.put(("say", self.voice(persona, message), status)), daemon=True,
        ).start()

    def create_database(self):
        note = {"created": DB_CREATED_NOTE, "found": DB_FOUND_NOTE, "failed": DB_FAILED_NOTE}[create_database()]
        return note.format(path=DB_DISPLAY_PATH)

    def save_settings(self):
        try:
            save_config(self.config)
        except OSError as e:
            print(f"[buddy] couldn't save {CONFIG_PATH}: {e}", file=sys.stderr)
            return False
        return True

    def refresh_placeholder(self):
        self.clear_placeholder()
        self.show_placeholder()

    def handle_onboarding(self, text):
        if self.onboarding_step == "name":
            name = extract_name(text)
            if not name:
                self.speak(REASK_NOTE)
                return
            self.user_name = name
            self.config["name"] = name
            self.save_settings()
            self.advance_setup()
            self.greet()
            return
        key = match_persona(text)
        if key is None:
            self.speak(PERSONA_REASK_NOTE, menu=persona_menu())
            return
        self.complete_onboarding(key)

    def open_setup(self):
        SetupScreen(self.root, self.config, on_done=self.provider_changed, cancellable=True)

    def provider_changed(self, client, notice=None):
        self.client = client
        self.speak(PROVIDER_SWITCHED_NOTE.format(provider=client.label))
        if notice:
            self.notify(notice)

    def complete_onboarding(self, key):
        self.onboarding_step = None
        self.set_persona(key)
        self.config.update(persona=key, onboarded=True)
        note = PERSONA_CHOSEN_NOTE.format(buddy=self.persona["name"], name=self.user_name) + " " + self.create_database()
        if not self.save_settings():
            note += " " + SAVE_FAILED_NOTE
        self.progress_until = time.monotonic() + 4.0
        self.speak(note)
        threading.Thread(target=self.refresh_after_onboarding, daemon=True).start()

    def fade_in(self, alpha):
        target = 1.0 if self.nswindow is not None else 0.97
        try:
            self.root.attributes("-alpha", min(alpha, target))
        except tk.TclError:
            return
        if alpha < target:
            self.root.after(16, lambda: self.fade_in(round(alpha + 0.1, 2)))

    def refresh_after_onboarding(self):
        note = refresh_activity()
        if note:
            self.replies.put(("raw_notice", note, None))

    def prefetch_calendar(self):
        if calendar_permission() == "ask":
            request_calendar_in_helper()
        _, error = self.calendar.get()
        if error:
            self.replies.put(("raw_notice", error, None))

    def nudge_loop(self):
        interval = nudge_interval_seconds(self.config)
        if not interval:
            return
        time.sleep(5)
        while True:
            try:
                if not self.onboarding and self.client is not None:
                    self.run_nudge_check()
            except Exception as e:
                print(f"[buddy] nudge check failed: {e!r}", file=sys.stderr)
            time.sleep(interval)

    def run_nudge_check(self):
        events, _ = self.calendar.get()
        if not events:
            return
        now = datetime.now()
        nudged = {k: v for k, v in load_nudged().items() if v > now.timestamp()}
        for nudge in check_nudges(events, nudged):
            nudged[nudge["key"]] = nudge["event"]["start"].timestamp() + 86400
            save_nudged(nudged)
            self.replies.put(("nudge", nudge["text"], None))

    def peek(self):
        self.visible = True
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        if self.nswindow is not None:
            self.root.after(50, self.nswindow.invalidateShadow)

    def notify(self, message):
        if message in self.noticed:
            return
        self.noticed.add(message)
        persona = self.persona
        threading.Thread(
            target=lambda: self.replies.put(("notice", self.voice(persona, message), None)), daemon=True,
        ).start()

    def idle_status(self):
        return self.setup_status() if self.onboarding else random.choice(self.persona["idle"])

    def placeholder_text(self):
        if self.onboarding_step == "name":
            return "type your name…"
        if self.onboarding_step == "persona":
            return "type a buddy's name…"
        return f"Ask {self.persona['name']} something…"

    def show_placeholder(self):
        if not self.placeholder and not self.entry.get():
            self.entry.insert(0, self.placeholder_text())
            self.entry.icursor(0)
            self.entry.config(fg=self.theme["muted"])
            self.placeholder = True

    def clear_placeholder(self, _event=None):
        if self.placeholder:
            self.entry.delete(0, "end")
            self.entry.config(fg=self.theme["ink"])
            self.placeholder = False

    def say(self, message, typing=False, menu=None):
        if self.typing_job is not None:
            self.root.after_cancel(self.typing_job)
            self.typing_job = None
        self.menu_after_typing = menu
        self.bubble.config(state="normal")
        self.bubble.delete("1.0", "end")
        self.bubble.config(state="disabled")
        if typing:
            self.type_out(message, 0)
        else:
            self.append(message)
            self.render_menu()

    def render_menu(self):
        menu, self.menu_after_typing = self.menu_after_typing, None
        if not menu:
            return
        self.bubble.config(state="normal")
        self.bubble.insert("end", "\n", "menu_gap")
        for key, bullet, color, name, detail in menu:
            color_tag = "color_" + color.lstrip("#")
            self.bubble.tag_configure(color_tag, foreground=color)
            row = (f"pick_{key}",) if key else ()
            self.bubble.insert("end", "\n" + bullet + " ", ("menu_name", color_tag) + row)
            self.bubble.insert("end", name, ("menu_name",) + row)
            self.bubble.insert("end", "  " + detail, ("menu",) + row)
            if key:
                tag = f"pick_{key}"
                self.bubble.tag_bind(tag, "<Button-1>", lambda _e, k=key: self.pick_from_menu(k))
                self.bubble.tag_bind(tag, "<Enter>", lambda _e, t=tag: self.hover_menu(t, True))
                self.bubble.tag_bind(tag, "<Leave>", lambda _e, t=tag: self.hover_menu(t, False))
        self.bubble.config(state="disabled")
        self.bubble.see("end")

    def hover_menu(self, tag, on):
        self.bubble.tag_configure(tag, background=self.theme["bubble_edge"] if on else "")
        self.bubble.config(cursor="pointinghand" if on else "arrow")

    def append(self, text):
        self.bubble.config(state="normal")
        self.bubble.insert("end", text)
        self.bubble.config(state="disabled")

    def type_out(self, message, index):
        self.append(message[index:index + 3])
        if index + 3 < len(message):
            self.typing_job = self.root.after(16, self.type_out, message, index + 3)
        else:
            self.typing_job = None
            self.render_menu()

    def set_status(self, text, hold=0.0):
        self.status_full = text
        self.fit_status()
        self.status_until = time.monotonic() + hold if hold else 0.0

    def fit_status(self):
        text = self.status_full
        room = self.header_right - 112 - 10
        if self.status_font.measure(text) > room:
            while text and self.status_font.measure(text + "…") > room:
                text = text[:-1]
            text += "…"
        self.canvas.itemconfigure(self.status, text=text)

    def run_command(self, text):
        parts = text.split()
        if parts[0].lower() == "/setup":
            self.open_setup()
            return True
        if parts[0].lower() == "/voice":
            self.handle_voice_command(parts[1:])
            return True
        if parts[0].lower() != "/persona":
            return False
        names = ", ".join(PERSONAS)
        if len(parts) == 1:
            self.speak(PERSONA_LIST_NOTE.format(name=self.persona["name"]), menu=persona_menu())
        elif parts[1].lower() not in PERSONAS:
            self.speak(f"There's no persona called \"{parts[1]}\". You can pick one of: {names}.")
        else:
            self.set_persona(parts[1].lower())
            self.config["persona"] = self.persona_key
            self.save_settings()
            self.greet()
        return True

    def handle_voice_command(self, args):
        if not args:
            self.speak(VOICE_STATUS_NOTE.format(state="on" if self.voice_enabled else "off"))
            return
        choice = args[0].lower()
        if choice not in ("on", "off"):
            self.speak(VOICE_USAGE_NOTE)
            return
        self.voice_enabled = choice == "on"
        self.config["voice_enabled"] = self.voice_enabled
        self.save_settings()
        if not self.voice_enabled:
            voice.stop_speaking()
        self.speak(VOICE_TOGGLED_NOTE.format(state=choice))

    def toggle_recording(self):
        if self.recording:
            self.stop_recording()
        else:
            self.start_recording()

    def on_wake_heard(self):
        if self.recording or self.busy or self.onboarding:
            return
        if not self.visible:
            self.peek()
        self.start_recording()

    def start_recording(self, _event=None):
        if self.recording or self.busy or self.onboarding:
            return
        self.recording = True
        if not self.wake_paused:
            self.wake_paused = True
            self.wake_listener.pause()
        self.canvas.itemconfigure(self.mic_dot, fill="#FF4F4F")
        self.set_status("listening…")
        self.voice_stop_event = threading.Event()
        threading.Thread(target=self.voice_session, args=(self.voice_stop_event,), daemon=True).start()

    def stop_recording(self, _event=None):
        if self.voice_stop_event is not None:
            self.voice_stop_event.set()

    def voice_session(self, stop_event):
        try:
            first_time = not self.config.get("voice_intro_shown")
            if first_time:
                self.config["voice_intro_shown"] = True
                self.save_settings()
                intro = VOICE_INTRO_NOTE.format(hotkey=VOICE_HOTKEY_LABEL, name=self.persona["name"])
                self.replies.put(("notice", self.voice(self.persona, intro), None))
            voice.ensure_access(allow_prompt=True)
            recorder = voice.Recorder()
            recorder.start()
        except voice.VoiceInputError as e:
            self.replies.put(("voice_error", str(e), None))
            return
        except Exception as e:
            print(f"[buddy] voice recording failed: {e!r}", file=sys.stderr)
            self.replies.put(("voice_error", voice.RECORD_FAILED_MESSAGE, None))
            return
        self.replies.put(("voice_listening", None, None))
        recorder.wait_for_silence(stop_event)
        path = recorder.stop()
        self.replies.put(("voice_transcribing", None, None))
        try:
            text = voice.transcribe_file(path)
        except voice.VoiceInputError as e:
            self.replies.put(("voice_error", str(e), None))
            return
        except Exception as e:
            print(f"[buddy] transcription failed: {e!r}", file=sys.stderr)
            self.replies.put(("voice_error", voice.NOTHING_HEARD_MESSAGE, None))
            return
        if not text:
            self.replies.put(("voice_error", voice.NOTHING_HEARD_MESSAGE, None))
            return
        self.replies.put(("voice_text", text, None))

    def submit(self, _event=None):
        question = "" if self.placeholder else self.entry.get().strip()
        if not question or self.busy:
            return "break"
        self.entry.delete(0, "end")
        self.show_placeholder()
        if self.onboarding_step == "persona" and question.lower().startswith("/persona "):
            question = question.split(None, 1)[1]
        elif question.startswith("/") and self.run_command(question):
            return "break"
        if self.onboarding:
            self.handle_onboarding(question)
            return "break"
        if is_catch_up(question):
            self.busy = True
            self.say("…")
            threading.Thread(target=self.catch_up, args=(self.persona,), daemon=True).start()
            return "break"
        if not self.client:
            self.say(offline_line(self.persona, "no_key", self.client), typing=True)
            return "break"
        if is_screen_question(question):
            self.look_at_screen(question)
            return "break"
        self.busy = True
        self.say("…")
        threading.Thread(
            target=self.answer, args=(self.persona, question, self.user_name), daemon=True,
        ).start()
        return "break"

    def catch_up(self, persona):
        try:
            reply = persona["frame"].format(message=build_digest())
        except Exception as e:
            print(f"[buddy] catch-up failed: {e!r}", file=sys.stderr)
            reply = offline_line(persona, "broken", self.client)
        self.replies.put(("reply", reply, persona["done"]))

    def look_at_screen(self, question):
        self.busy = True
        self.say("…")
        app = self.previous_app
        app_name = app.localizedName() if app is not None else None
        args = (self.persona, question, self.user_name, app_name)
        self.hide()
        if app is not None:
            bring_forward(app)
        self.root.after(
            400, lambda: threading.Thread(target=self.answer_about_screen, args=args, daemon=True).start(),
        )

    def answer_about_screen(self, persona, question, user_name, app_name):
        reappear = lambda: self.replies.put(("reappear", None, None))
        error = None
        try:
            summary = screen_reader.describe_screen(screen_reader.read_screen(on_captured=reappear), app_name)
            events, error = self.calendar.get()
            reply, ok = ask_model(self.client, persona, question, user_name, events, screen=summary)
            kind = "reply_screen" if ok else "reply_error"
        except screen_reader.ScreenReadError as e:
            reappear()
            reply, kind = self.voice(persona, str(e)), "reply_error"
        except Exception as e:
            reappear()
            print(f"[buddy] screen look failed: {e!r}", file=sys.stderr)
            reply, kind = offline_line(persona, "broken", self.client), "reply_error"
        self.replies.put((kind, reply, persona["done"]))
        if error:
            self.replies.put(("raw_notice", error, None))

    def answer(self, persona, question, user_name):
        error = None
        try:
            events, error = self.calendar.get()
            reply, ok = ask_model(self.client, persona, question, user_name, events)
            kind = "reply" if ok else "reply_error"
        except Exception as e:
            print(f"[buddy] answer failed: {e!r}", file=sys.stderr)
            reply, kind = offline_line(persona, "broken", self.client), "reply_error"
        self.replies.put((kind, reply, persona["done"]))
        if error:
            self.replies.put(("raw_notice", error, None))

    def poll(self):
        while True:
            try:
                event = self.hotkey_events.get_nowait()
            except queue.Empty:
                break
            if event == "toggle":
                self.toggle()
            elif event == "mic_tap":
                self.toggle_recording()
        while True:
            try:
                kind, message, status = self.replies.get_nowait()
            except queue.Empty:
                break
            if kind == "reappear":
                if not self.visible:
                    self.show()
            elif kind == "raw_notice":
                self.notify(message)
            elif kind == "notice":
                self.pending_notices.append(message)
            elif kind == "nudge":
                self.pending_notices.append(self.persona["frame"].format(message=message))
                if not self.visible:
                    self.peek()
            elif kind == "wake":
                self.on_wake_heard()
            elif kind == "voice_listening":
                self.set_status("listening…")
            elif kind == "voice_transcribing":
                self.set_status("making that out…")
            elif kind == "voice_text":
                self.recording = False
                self.canvas.itemconfigure(self.mic_dot, fill=self.theme["field_edge"])
                self.entry.delete(0, "end")
                self.placeholder = False
                self.entry.insert(0, message)
                self.submit()
            elif kind == "voice_error":
                self.recording = False
                self.canvas.itemconfigure(self.mic_dot, fill=self.theme["field_edge"])
                self.set_status(self.idle_status())
                self.notify(message)
            else:
                self.busy = False
                menu, self.next_menu = self.next_menu, None
                self.say(message, typing=True, menu=menu)
                if kind in ("reply", "reply_error", "reply_screen") and self.voice_enabled:
                    voice.stop_speaking()
                    try:
                        self.voice_proc = voice.speak(message, self.persona_key)
                    except voice.VoiceOutputError as e:
                        print(f"[buddy] voice output failed: {e!r}", file=sys.stderr)
                if status:
                    self.set_status(status, hold=5.0)
                else:
                    self.set_status(self.idle_status())

        if self.pending_notices and self.typing_job is None and not self.busy:
            for notice in self.pending_notices:
                self.append(f"\n\n{notice}")
            self.pending_notices = []

        speaking = self.voice_proc is not None and self.voice_proc.poll() is None
        should_pause = self.recording or self.busy or self.onboarding or speaking
        if should_pause != self.wake_paused:
            self.wake_paused = should_pause
            self.wake_listener.pause() if should_pause else self.wake_listener.resume()
        self.wake_listener.tick()
        self.root.after(100, self.poll)

    def animate(self):
        if self.visible:
            now = time.monotonic()
            speed, amplitude, glow = (6.0, 3.5, 1.0) if self.busy else (2.0, 2.5, 0.55)
            self.phase += speed * FRAME_MS / 1000
            offset = amplitude * math.sin(self.phase)
            self.canvas.move("float", 0, offset - self.bob)
            self.bob = offset
            self.update_progress()
            pulse = (math.sin(self.phase * 0.8) + 1) / 2
            glow_color = mix(self.theme["glow_dim"], self.theme["glow_bright"], pulse * glow)
            self.canvas.itemconfigure(self.halo, outline=glow_color)
            self.canvas.itemconfigure(self.status_dot, fill=glow_color)
            if self.recording:
                mic_pulse = (math.sin(self.phase * 1.6) + 1) / 2
                self.canvas.itemconfigure(self.mic_dot, fill=mix(self.theme["field_edge"], "#FF4F4F", mic_pulse))

            avatar = self.persona["avatar"]
            if avatar in BLINKING_AVATARS:
                if self.blink_until and now >= self.blink_until:
                    self.canvas.itemconfigure(f"{avatar}_eyes_closed", state="hidden")
                    self.canvas.itemconfigure(f"{avatar}_eyes_open", state="normal")
                    self.blink_until = 0.0
                elif now >= self.blink_at:
                    self.canvas.itemconfigure(f"{avatar}_eyes_open", state="hidden")
                    self.canvas.itemconfigure(f"{avatar}_eyes_closed", state="normal")
                    self.blink_until = now + 0.13
                    self.blink_at = now + random.uniform(2.5, 6.0)

            if self.onboarding:
                self.set_status(self.setup_status())
            elif self.busy:
                self.set_status(self.persona["busy"] + "." * (int(now * 3) % 4))
            elif self.status_until and now >= self.status_until:
                self.set_status(self.idle_status())
        self.root.after(FRAME_MS, self.animate)

    def toggle(self):
        if self.visible:
            self.hide()
        else:
            self.show()

    def show(self):
        self.visible = True
        app = frontmost_other_app()
        if app is not None:
            self.previous_app = app
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        activate_app()
        self.root.focus_force()
        self.entry.focus_set()
        if self.nswindow is not None:
            self.root.after(50, self.nswindow.invalidateShadow)

    def hide(self):
        self.visible = False
        self.root.withdraw()

    def start_drag(self, event):
        self.drag_offset = (event.x, event.y)
        self.resize_origin = (event.x_root, event.y_root, self.w, self.h) if self.in_grip(event) else None

    def end_drag(self, _event):
        self.resize_origin = None

    def drag(self, event):
        if self.resize_origin is not None:
            x0, y0, w0, h0 = self.resize_origin
            self.resize_to(w0 + event.x_root - x0, h0 + event.y_root - y0)
            return
        x = self.root.winfo_pointerx() - self.drag_offset[0]
        y = self.root.winfo_pointery() - self.drag_offset[1]
        self.root.geometry(f"+{x}+{y}")


def configure_bundled_tcl():
    if not getattr(sys, "frozen", False):
        return
    lib_dir = os.path.join(os.environ.get("RESOURCEPATH", ""), "lib")
    for variable, folder in (("TCL_LIBRARY", "tcl9.0"), ("TK_LIBRARY", "tk9.0")):
        path = os.path.join(lib_dir, folder)
        if os.path.isdir(path):
            os.environ.setdefault(variable, path)


SETTINGS_URLS = {
    "calendar": "x-apple.systempreferences:com.apple.preference.security?Privacy_Calendars",
    "accessibility": "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
    "messages": "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles",
}
CHAT_DB_PATH = os.path.expanduser("~/Library/Messages/chat.db")
PERMISSIONS = (
    ("calendar", "Calendar", "Lets Twin mention what's coming up today."),
    ("accessibility", "Keyboard shortcut", "Cmd+Shift+Space shows and hides Twin. Needs Accessibility."),
    ("messages", "Messages", "Reads bank texts for a rough spending summary. Needs Full Disk Access."),
)


def calendar_permission():
    try:
        import EventKit
    except ImportError:
        return "unavailable"
    status = EventKit.EKEventStore.authorizationStatusForEntityType_(EventKit.EKEntityTypeEvent)
    if status == EventKit.EKAuthorizationStatusFullAccess:
        return "allowed"
    if status == EventKit.EKAuthorizationStatusNotDetermined:
        return "ask"
    return "denied"


def messages_permission():
    try:
        with open(CHAT_DB_PATH, "rb"):
            return "allowed"
    except FileNotFoundError:
        return "unavailable"
    except OSError:
        return "denied"


def permission_state(key):
    if key == "calendar":
        return calendar_permission()
    if key == "accessibility":
        return "allowed" if accessibility_trusted(prompt=False) else "ask"
    return messages_permission()


def request_permission(key, state):
    if key == "calendar" and state == "ask":
        request_calendar_in_helper()
    elif key == "accessibility" and state == "ask":
        accessibility_trusted(prompt=True)
    else:
        subprocess.run(["open", SETTINGS_URLS[key]], check=False)


def default_first_name():
    try:
        from Foundation import NSFullUserName
        full = NSFullUserName() or ""
    except ImportError:
        full = ""
    first = full.split()[0] if full.split() else ""
    return first if first.isalpha() else ""


def create_database():
    existed = os.path.exists(DB_PATH)
    try:
        db.get_connection(DB_PATH).close()
    except (duckdb.Error, OSError) as e:
        print(f"[buddy] couldn't create {DB_PATH}: {e}", file=sys.stderr)
        return "failed"
    return "found" if existed else "created"


class SetupScreen:
    WIDTH = 520
    HEIGHT = 680
    NODE_X = (62, 194, 326, 458)
    TRACK_Y = 150
    STATUS_Y = 548
    BUTTON_Y = 604
    STEPS = ("provider", "name", "buddy", "permissions")
    STEP_LABELS = ("provider", "name", "buddy", "permissions")

    def __init__(self, root, config, on_done, cancellable=False, saved=None):
        self.root = root
        self.config = config
        self.on_done = on_done
        self.cancellable = cancellable
        self.results = queue.Queue()
        self.c = LANDING
        self.display, self.mono = pick_fonts()
        self.provider = None
        self.client = saved
        self.validated_key = None
        self.checking = False
        self.finishing = False
        self.page = None
        self.widgets = []
        self.name = config.get("name") or default_first_name()
        self.persona = config.get("persona") if config.get("persona") in PERSONAS else DEFAULT_PERSONA
        self.pages = ["provider"] if cancellable else ["provider", "name", "buddy", "permissions", "done"]
        self.prefill_provider = saved.provider if saved is not None else None
        self.prefill_key = saved.api_key if saved is not None else None
        self.build()
        self.show_page("provider")
        self.poll()

    def shape(self, tag, x1, y1, x2, y2, r, fill, edge=None, extra=()):
        if edge:
            for item in rounded_rect_items(self.canvas, x1, y1, x2, y2, r, (tag, f"{tag}_edge") + extra):
                self.canvas.itemconfigure(item, fill=edge)
            x1, y1, x2, y2, r = x1 + 1, y1 + 1, x2 - 1, y2 - 1, r - 1
        for item in rounded_rect_items(self.canvas, x1, y1, x2, y2, r, (tag, f"{tag}_fill") + extra):
            self.canvas.itemconfigure(item, fill=fill)

    def text(self, x, y, text, size=13, bold=False, color=None, anchor="w", width=None, mono=False, tags=("page",)):
        font = (self.mono if mono else self.display, size) + (("bold",) if bold else ())
        return self.canvas.create_text(x, y, text=text, anchor=anchor, font=font, fill=color or self.c["text"],
                                       width=width, tags=tags)

    def build(self):
        c = self.c
        window = tk.Toplevel(self.root)
        self.window = window
        window.title("Set up Twin")
        window.configure(bg=c["bg"])
        window.resizable(False, False)
        window.protocol("WM_DELETE_WINDOW", self.close)
        window.bind("<Return>", lambda _e: self.next())
        window.bind("<Escape>", lambda _e: self.close() if self.cancellable else None)
        height = 530 if self.cancellable else self.HEIGHT
        self.height = height
        canvas = tk.Canvas(window, width=self.WIDTH, height=height, bg=c["bg"], highlightthickness=0, bd=0)
        canvas.pack()
        self.canvas = canvas
        dot = mix(c["bg"], "#FFFFFF", 0.06)
        for x in range(14, self.WIDTH, 28):
            for y in range(14, height, 28):
                canvas.create_oval(x, y, x + 1.6, y + 1.6, fill=dot, outline="")
        canvas.create_oval(28, 28, 58, 56, fill=c["lime_shade"], outline="")
        canvas.create_oval(28, 28, 55.5, 53, fill=c["lime"], outline="")
        for ex in (37.5, 46):
            canvas.create_oval(ex - 1.6, 35, ex + 1.6, 42.5, fill=c["face"], outline="", tags=("logo_eye",))
            canvas.create_line(ex - 2.2, 39.5, ex + 2.2, 39.5, fill=c["face"], width=2, capstyle="round",
                               state="hidden", tags=("logo_closed",))
        self.title = self.text(70, 42, "", size=24, bold=True, tags=())
        window.after(1800, self.blink)
        self.subtitle = self.text(30, 74, "", size=13, color=c["muted"], width=self.WIDTH - 60, anchor="nw", tags=())

        if not self.cancellable:
            for index in range(len(self.NODE_X) - 1):
                canvas.create_line(self.NODE_X[index] + 14, self.TRACK_Y, self.NODE_X[index + 1] - 14, self.TRACK_Y,
                                   width=2, fill=c["line"], tags=(f"link{index}",))
            for index, x in enumerate(self.NODE_X):
                canvas.create_oval(x - 12, self.TRACK_Y - 12, x + 12, self.TRACK_Y + 12, width=2,
                                   tags=(f"node{index}",))
                canvas.create_text(x, self.TRACK_Y, text=str(index + 1), font=(self.display, 11, "bold"),
                                   tags=(f"node{index}_text",))
                canvas.create_text(x, self.TRACK_Y + 26, text=self.STEP_LABELS[index], font=(self.mono, 10),
                                   tags=(f"node{index}_label",))

        status_y = self.STATUS_Y if not self.cancellable else height - 104
        button_y = self.BUTTON_Y if not self.cancellable else height - 62
        self.status = canvas.create_text(30, status_y, text="", anchor="nw", width=self.WIDTH - 60,
                                         font=(self.display, 13), fill=c["muted"])
        right = self.WIDTH - 30
        self.shape("button", right - 150, button_y, right, button_y + 42, 21, c["lime"])
        self.button_text = canvas.create_text(right - 75, button_y + 21, text="Continue",
                                              font=(self.display, 15, "bold"), fill=c["face"], tags=("button",))
        canvas.tag_bind("button", "<Button-1>", lambda _e: self.next())
        canvas.tag_bind("button", "<Enter>", lambda _e: self.hover_button(True))
        canvas.tag_bind("button", "<Leave>", lambda _e: self.hover_button(False))
        self.back = canvas.create_text(30, button_y + 21, text="", anchor="w", font=(self.display, 13),
                                       fill=c["muted"], tags=("back",))
        canvas.tag_bind("back", "<Button-1>", lambda _e: self.go_back())
        canvas.tag_bind("back", "<Enter>", lambda _e: self.canvas.config(cursor="pointinghand"))
        canvas.tag_bind("back", "<Leave>", lambda _e: self.canvas.config(cursor=""))

        x = (window.winfo_screenwidth() - self.WIDTH) // 2
        y = max(30, (window.winfo_screenheight() - height) // 3)
        window.geometry(f"{self.WIDTH}x{height}+{x}+{y}")
        activate_app()
        window.lift()
        window.focus_force()

    def blink(self, closing=True):
        if not self.window.winfo_exists():
            return
        self.canvas.itemconfigure("logo_eye", state="hidden" if closing else "normal")
        self.canvas.itemconfigure("logo_closed", state="normal" if closing else "hidden")
        if closing:
            self.window.after(130, lambda: self.blink(False))
        else:
            self.window.after(random.randint(2500, 5500), self.blink)

    def clear_page(self):
        self.canvas.delete("page")
        for widget in self.widgets:
            widget.destroy()
        self.widgets = []
        self.set_status("")

    def show_page(self, page):
        self.clear_page()
        self.page = page
        index = self.pages.index(page)
        back = "" if index == 0 or page == "done" else "Back"
        if self.cancellable and page == "provider":
            back = "Cancel"
        self.canvas.itemconfigure(self.back, text=back)
        self.canvas.itemconfigure(self.button_text, text="Open Twin" if page == "done" else "Continue")
        if not self.cancellable:
            steps_done = len(self.STEPS) if page == "done" else self.STEPS.index(page)
            self.draw_track(steps_done)
        getattr(self, f"page_{page}")()

    def draw_track(self, done_count):
        c, canvas = self.c, self.canvas
        for index in range(len(self.NODE_X)):
            done, active = index < done_count, index == done_count
            canvas.itemconfigure(f"node{index}", fill=c["lime"] if done else (c["bg2"] if active else c["bg"]),
                                 outline=c["lime"] if done or active else c["line"])
            canvas.itemconfigure(f"node{index}_text", text="✓" if done else str(index + 1),
                                 fill=c["face"] if done else (c["lime"] if active else c["muted"]))
            canvas.itemconfigure(f"node{index}_label", fill=c["text"] if done or active else c["muted"])
            if index < len(self.NODE_X) - 1:
                canvas.itemconfigure(f"link{index}", fill=c["lime"] if done else c["line"])

    def field(self, y, value="", secret=False):
        c = self.c
        self.shape("field", 30, y, self.WIDTH - 30, y + 42, 14, c["bg2"], c["line"], extra=("page",))
        entry = tk.Entry(self.window, show="•" if secret else "", bd=0, highlightthickness=0, relief="flat",
                         bg=c["bg2"], fg=c["text"], insertbackground=c["lime"], font=(self.display, 14),
                         disabledbackground=c["bg2"], disabledforeground=c["muted"])
        entry.insert(0, value)
        self.canvas.create_window(46, y + 21, anchor="w", window=entry,
                                  width=self.WIDTH - 30 - 46 - (64 if secret else 16), tags=("page",))
        self.widgets.append(entry)
        entry.focus_set()
        entry.icursor("end")
        return entry

    def page_provider(self):
        c = self.c
        if self.cancellable:
            self.canvas.itemconfigure(self.title, text="change provider")
            self.canvas.itemconfigure(self.subtitle, text="Pick a provider and paste its API key.")
            top = 140
        else:
            self.canvas.itemconfigure(self.title, text="set up twin")
            self.canvas.itemconfigure(self.subtitle, text=(
                "Pick an AI provider and paste your API key. Only your chats and a short summary of your day "
                "are sent to it."))
            top = 214
        self.text(30, top, "Provider", size=16, bold=True)
        card_width = (self.WIDTH - 60 - 12) / 2
        for index, provider in enumerate(PROVIDERS):
            x1 = 30 + (index % 2) * (card_width + 12)
            y1 = top + 18 + (index // 2) * 72
            tag = f"card_{provider}"
            self.shape(tag, x1, y1, x1 + card_width, y1 + 62, 14, c["card"], c["line"], extra=("page",))
            self.text(x1 + 16, y1 + 23, PROVIDERS[provider]["label"], size=15, bold=True, tags=("page", tag, f"{tag}_name"))
            self.text(x1 + 16, y1 + 44, PROVIDERS[provider]["blurb"], size=12, color=c["muted"], tags=("page", tag))
            self.canvas.tag_bind(tag, "<Button-1>", lambda _e, p=provider: self.select_provider(p))
            self.canvas.tag_bind(tag, "<Enter>", lambda _e, p=provider: self.hover_card("card", p, True))
            self.canvas.tag_bind(tag, "<Leave>", lambda _e, p=provider: self.hover_card("card", p, False))
        key_top = top + 18 + 2 * 72 + 22
        self.key_heading = self.text(30, key_top, "API key", size=16, bold=True)
        self.key_entry = self.field(key_top + 18, secret=True)
        self.reveal = self.text(self.WIDTH - 46, key_top + 39, "show", size=10, color=c["muted"], anchor="e",
                                mono=True, tags=("page", "reveal"))
        self.canvas.tag_bind("reveal", "<Button-1>", lambda _e: self.toggle_reveal())
        self.link = self.text(30, key_top + 78, "Choose a provider to see where to get a key.", size=12,
                              color=c["muted"], tags=("page", "link"))
        self.canvas.tag_bind("link", "<Button-1>", lambda _e: self.open_key_page())
        preset = self.provider or self.prefill_provider or next((p for p in PROVIDERS if llm_providers.env_key(p)), None)
        if preset:
            self.select_provider(preset)
            key = self.validated_key if self.provider == preset and self.validated_key else (
                self.prefill_key if preset == self.prefill_provider else llm_providers.env_key(preset))
            if key:
                self.key_entry.insert(0, key)
                self.set_status("Your saved key is filled in. Press Continue to check it.")

    def select_provider(self, provider):
        if self.checking or self.page != "provider":
            return
        c = self.c
        self.provider = provider
        for key in PROVIDERS:
            on = key == provider
            self.canvas.itemconfigure(f"card_{key}_edge", fill=c["lime"] if on else c["line"])
            self.canvas.itemconfigure(f"card_{key}_fill", fill=mix(c["card"], c["lime"], 0.10) if on else c["card"])
            self.canvas.itemconfigure(f"card_{key}_name", fill=c["lime"] if on else c["text"])
        spec = PROVIDERS[provider]
        self.canvas.itemconfigure(self.key_heading, text=f"{spec['label']} API key")
        self.canvas.itemconfigure(self.link, text=f"Get a key at {llm_providers.display_url(spec['key_url'])}",
                                  fill=c["sky"])
        if self.canvas.itemcget(self.status, "fill") == c["pink"]:
            self.set_status("")
        self.key_entry.focus_set()

    def hover_card(self, kind, key, on):
        selected = self.provider if kind == "card" else self.persona
        if key != selected:
            self.canvas.itemconfigure(f"{kind}_{key}_edge", fill=self.c["muted"] if on else self.c["line"])
        self.canvas.config(cursor="pointinghand" if on else "")

    def toggle_reveal(self):
        hidden = self.key_entry.cget("show") == "•"
        self.key_entry.configure(show="" if hidden else "•")
        self.canvas.itemconfigure(self.reveal, text="hide" if hidden else "show")

    def open_key_page(self):
        if self.provider:
            webbrowser.open(PROVIDERS[self.provider]["key_url"])

    def page_name(self):
        c = self.c
        self.canvas.itemconfigure(self.title, text="nice, that works")
        self.canvas.itemconfigure(self.subtitle, text=f"Twin will use {self.client.label}. A couple of quick things and you're done.")
        self.text(30, 222, "What should Twin call you?", size=18, bold=True)
        self.name_entry = self.field(242, value=self.name)
        self.name_entry.select_range(0, "end")
        self.text(30, 306, "Twin uses this to greet you. It's saved on this Mac.", size=12, color=c["muted"])

    def page_buddy(self):
        c = self.c
        self.canvas.itemconfigure(self.title, text=f"hi {self.name}")
        self.canvas.itemconfigure(self.subtitle, text="Pick the buddy you want to talk to. You can switch anytime with /persona.")
        self.text(30, 214, "Buddy", size=16, bold=True)
        for index, (key, persona) in enumerate(PERSONAS.items()):
            y1 = 232 + index * 58
            tag = f"buddy_{key}"
            accent = persona["palette"]["accent"]
            self.shape(tag, 30, y1, self.WIDTH - 30, y1 + 52, 14, c["card"], c["line"], extra=("page",))
            self.canvas.create_oval(46, y1 + 12, 74, y1 + 40, fill=accent, outline="", tags=("page", tag))
            for ex in (55.5, 64.5):
                self.canvas.create_oval(ex - 1.5, y1 + 20, ex + 1.5, y1 + 27, fill=c["face"], outline="",
                                        tags=("page", tag))
            self.text(88, y1 + 18, persona["name"], size=15, bold=True, tags=("page", tag, f"{tag}_name"))
            self.text(88, y1 + 36, persona["tagline"], size=12, color=c["muted"], tags=("page", tag))
            self.canvas.tag_bind(tag, "<Button-1>", lambda _e, k=key: self.select_buddy(k))
            self.canvas.tag_bind(tag, "<Enter>", lambda _e, k=key: self.hover_card("buddy", k, True))
            self.canvas.tag_bind(tag, "<Leave>", lambda _e, k=key: self.hover_card("buddy", k, False))
        self.select_buddy(self.persona)
        self.window.focus_set()

    def select_buddy(self, key):
        c = self.c
        self.persona = key
        for other, persona in PERSONAS.items():
            on = other == key
            accent = persona["palette"]["accent"]
            self.canvas.itemconfigure(f"buddy_{other}_edge", fill=accent if on else c["line"])
            self.canvas.itemconfigure(f"buddy_{other}_fill", fill=mix(c["card"], accent, 0.12) if on else c["card"])
            self.canvas.itemconfigure(f"buddy_{other}_name", fill=accent if on else c["text"])

    def page_permissions(self):
        c = self.c
        self.canvas.itemconfigure(self.title, text="permissions")
        self.canvas.itemconfigure(self.subtitle, text="Each one turns on a feature. You can skip any of them.")
        self.permission_rows = {}
        y1 = 214
        for key, title, detail in PERMISSIONS:
            title_item = self.text(48, y1 + 14, title, size=15, bold=True, anchor="nw")
            detail_item = self.text(48, self.canvas.bbox(title_item)[3] + 3, detail, size=12, color=c["muted"],
                                    width=self.WIDTH - 30 - 48 - 140, anchor="nw")
            y2 = self.canvas.bbox(detail_item)[3] + 14
            self.shape(f"perm_{key}", 30, y1, self.WIDTH - 30, y2, 14, c["card"], c["line"], extra=("page",))
            self.canvas.tag_lower(f"perm_{key}", title_item)
            action = self.text(self.WIDTH - 48, (y1 + y2) / 2, "", size=13, bold=True, anchor="e",
                               tags=("page", f"perm_action_{key}"))
            self.canvas.tag_bind(f"perm_action_{key}", "<Button-1>", lambda _e, k=key: self.permission_clicked(k))
            self.canvas.tag_bind(f"perm_action_{key}", "<Enter>", lambda _e: self.canvas.config(cursor="pointinghand"))
            self.canvas.tag_bind(f"perm_action_{key}", "<Leave>", lambda _e: self.canvas.config(cursor=""))
            self.permission_rows[key] = action
            y1 = y2 + 10
        self.asked = set()
        self.window.focus_set()
        self.refresh_permissions()

    def refresh_permissions(self):
        if self.page != "permissions" or not self.window.winfo_exists():
            return
        c = self.c
        for key, action in self.permission_rows.items():
            state = permission_state(key)
            if state == "allowed":
                label, color = "Allowed", c["lime"]
            elif state == "unavailable":
                label, color = "Not available", c["muted"]
            elif state == "ask" and key not in self.asked:
                label, color = "Allow", c["sky"]
            else:
                label, color = "Open Settings", c["sky"]
            self.canvas.itemconfigure(action, text=label, fill=color)
        self.window.after(1000, self.refresh_permissions)

    def permission_clicked(self, key):
        state = permission_state(key)
        if state in ("allowed", "unavailable"):
            return
        request_permission(key, "ask" if state == "ask" and key not in self.asked else "denied")
        self.asked.add(key)
        if key == "messages":
            self.set_status("After turning on Full Disk Access for Twin, Messages will work the next time Twin starts.")

    def page_done(self):
        c = self.c
        result = create_database()
        self.canvas.itemconfigure(self.title, text="you're all set")
        self.canvas.itemconfigure(self.subtitle, text="Here's how Twin is set up.")
        rows = (("Name", self.name), ("Provider", self.client.label), ("Buddy", PERSONAS[self.persona]["name"]),
                ("Your data", DB_DISPLAY_PATH))
        for index, (label, value) in enumerate(rows):
            y = 222 + index * 32
            self.text(30, y, label, size=11, color=c["muted"], mono=True)
            self.text(150, y, value, size=14, width=self.WIDTH - 180)
        database_line = {
            "created": f"Your own local database was just created at {DB_DISPLAY_PATH}. It's yours, and nothing in it is shared.",
            "found": f"Your local database is at {DB_DISPLAY_PATH}. It's yours, and nothing in it is shared.",
            "failed": f"Twin couldn't create its database at {DB_DISPLAY_PATH}. It will try again next time it starts.",
        }[result]
        line = self.text(30, 362, database_line, size=13, color=c["pink"] if result == "failed" else c["text"],
                         width=self.WIDTH - 60, anchor="nw")
        below = self.canvas.bbox(line)[3] + 14
        tip = self.text(30, below, "Press Cmd+Shift+Space anytime to show or hide Twin.", size=13, color=c["muted"],
                        width=self.WIDTH - 60, anchor="nw")
        self.after_done_y = self.canvas.bbox(tip)[3] + 14
        self.config.update(name=self.name, persona=self.persona, provider=self.client.provider, onboarded=True)
        try:
            save_config(self.config)
        except OSError as e:
            print(f"[buddy] couldn't save {CONFIG_PATH}: {e}", file=sys.stderr)
            self.text(30, self.after_done_y, SAVE_FAILED_NOTE, size=13, color=c["pink"], width=self.WIDTH - 60,
                      anchor="nw")
        self.window.focus_set()

    def set_status(self, text, kind="info"):
        color = {"info": self.c["muted"], "error": self.c["pink"], "ok": self.c["lime"]}[kind]
        self.canvas.itemconfigure(self.status, text=text, fill=color)

    def hover_button(self, on):
        if not self.checking and not self.finishing:
            self.canvas.itemconfigure("button_fill", fill=mix(self.c["lime"], "#FFFFFF", 0.15) if on else self.c["lime"])
        self.canvas.config(cursor="pointinghand" if on else "")

    def set_busy(self, busy):
        self.checking = busy
        c = self.c
        self.canvas.itemconfigure("button_fill", fill=c["line"] if busy else c["lime"])
        self.canvas.itemconfigure(self.button_text, text="Checking..." if busy else "Continue",
                                  fill=c["muted"] if busy else c["face"])
        self.key_entry.configure(state="disabled" if busy else "normal")

    def go_back(self):
        if self.checking or self.finishing:
            return
        if self.cancellable and self.page == "provider":
            self.close()
            return
        index = self.pages.index(self.page)
        if index > 0 and self.page != "done":
            if self.page == "name":
                self.name = self.name_entry.get().strip() or self.name
            self.show_page(self.pages[index - 1])

    def next(self):
        if self.checking or self.finishing:
            return
        if self.page == "provider":
            self.check_key()
        elif self.page == "name":
            name = extract_name(self.name_entry.get())
            if not name:
                self.set_status("Type the name you'd like Twin to use, like Sam.", "error")
                self.name_entry.focus_set()
                return
            self.name = name
            self.show_page("buddy")
        elif self.page == "buddy":
            self.show_page("permissions")
        elif self.page == "permissions":
            self.show_page("done")
        elif self.page == "done":
            self.finish(self.client, None)

    def check_key(self):
        key = self.key_entry.get().strip()
        problem = llm_providers.precheck_key(self.provider, key)
        if problem:
            self.set_status(problem, "error")
            if self.provider:
                self.key_entry.focus_set()
            return
        if self.client is not None and self.validated_key == key and self.client.provider == self.provider:
            self.show_page(self.pages[1])
            return
        provider = self.provider
        model = llm_providers.model_for(provider, self.config)
        self.set_busy(True)
        self.set_status(f"Checking your key with {PROVIDERS[provider]['label']}. This sends one small test request.")
        threading.Thread(
            target=lambda: self.results.put((provider, key, *llm_providers.validate_key(provider, key, model))),
            daemon=True,
        ).start()

    def poll(self):
        if not self.window.winfo_exists():
            return
        try:
            provider, key, client, problem = self.results.get_nowait()
        except queue.Empty:
            self.window.after(100, self.poll)
            return
        self.set_busy(False)
        if client is None:
            self.set_status(problem, "error")
            self.key_entry.focus_set()
            self.key_entry.select_range(0, "end")
            self.window.after(100, self.poll)
            return
        self.client = client
        self.validated_key = key
        self.notice = None if llm_providers.keychain_save(provider, key) else KEY_NOT_SAVED_NOTE
        self.config["provider"] = provider
        try:
            save_config(self.config)
        except OSError as e:
            print(f"[buddy] couldn't save {CONFIG_PATH}: {e}", file=sys.stderr)
        if self.cancellable:
            self.finish(client, self.notice)
            return
        self.show_page("name")
        self.window.after(100, self.poll)

    def finish(self, client, notice):
        if self.finishing:
            return
        self.finishing = True
        notice = notice or getattr(self, "notice", None)

        def fade(alpha):
            if alpha <= 0 or not self.window.winfo_exists():
                if self.window.winfo_exists():
                    self.window.destroy()
                self.on_done(client, notice)
                return
            try:
                self.window.attributes("-alpha", alpha)
            except tk.TclError:
                pass
            self.window.after(16, lambda: fade(round(alpha - 0.12, 2)))

        fade(1.0)

    def close(self):
        if self.cancellable:
            self.window.destroy()
        else:
            self.root.destroy()


TRIAL_ENDING_NOTE = "Heads up: your Twin trial ends in {days} day{plural}. Enter a license key any time from the menu to keep going without a gap."


def main():
    if HOTKEY_FLAG in sys.argv:
        setup_logging(child=True)
        run_hotkey_listener()
        return
    if CALENDAR_FLAG in sys.argv:
        setup_logging(child=True)
        try:
            calendar_reader.request_permission()
        except calendar_reader.CalendarAccessError as e:
            print(f"[buddy] calendar access: {e}", file=sys.stderr)
        return
    setup_logging()
    configure_bundled_tcl()
    register_fonts()
    config = load_config()
    config = licensing.ensure_trial_started(config)
    save_config(config)

    hotkey_events = queue.Queue()
    listener = start_hotkey_listener(hotkey_events)
    root = tk.Tk()
    root.withdraw()
    root.title("Twin")

    def shutdown(*_):
        root.quit()

    root.createcommand("::tk::mac::Quit", shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    license_state = licensing.state(config)
    if license_state["config_changed"]:
        config = license_state["config"]
        save_config(config)

    def proceed(current_config):
        persona_key = resolve_persona_key(current_config)
        startup_notice = refresh_activity() if current_config.get("onboarded") else None

        def start(client, notice=None, fresh=False):
            current = load_config() if fresh else current_config
            buddy = Buddy(root, hotkey_events, resolve_persona_key(current), startup_notice, client, fresh=fresh)
            if notice:
                buddy.notify(notice)
            if not accessibility_trusted():
                buddy.notify(ACCESSIBILITY_NOTE)
            days = license_state.get("trial_days_left")
            if license_state["reason"] == "trial" and days is not None and days <= 3:
                buddy.notify(TRIAL_ENDING_NOTE.format(days=days, plural="" if days == 1 else "s"))

        client = llm_providers.saved_client(current_config)
        if client is None or not current_config.get("onboarded"):
            SetupScreen(root, current_config, on_done=lambda c, n: start(c, n, fresh=True), saved=client)
        else:
            start(client)

    if not license_state["allowed"]:
        message = {
            "trial_expired": f"Your {licensing.TRIAL_DAYS}-day trial has ended. Enter a license key to keep using Twin.",
            "revoked": "This license key is no longer valid. Enter a different key, or reach out if that's a mistake.",
            "revalidation_overdue": (
                "Twin couldn't confirm your license for a while. Reconnect to the internet, or re-enter your key."
            ),
        }.get(license_state["reason"], "Enter a license key to continue.")
        LicenseGate(root, config, save_config, proceed, LANDING).show(message)
    else:
        proceed(config)

    try:
        root.mainloop()
    finally:
        if listener is not None:
            listener.terminate()
    sys.exit(0)


if __name__ == "__main__":
    main()
