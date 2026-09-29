# Twin

A small, local-first AI buddy that lives on your Mac.

Twin floats on your desktop in a small widget, shows and hides with a global hotkey (`Cmd+Shift+Space`), and chats with you in one of five personas. It builds a rough picture of your day from your own Mac, reading bank SMS that arrive through Messages and events from your calendars, and keeps that data in a DuckDB file on your machine. Twin has no server of its own. When you chat, one request goes directly to the AI provider you picked (Anthropic, OpenAI, Google Gemini, or xAI), and that request is scrubbed before it leaves.

This started as a hackathon project. It's about 3,500 lines of Python and meant for anyone to install and change.

## Contents

- [How it works](#how-it-works)
- [Repository layout](#repository-layout)
- [Requirements](#requirements)
- [Installation](#installation)
- [Building the app](#building-the-app)
- [First run](#first-run)
- [Usage](#usage)
- [AI providers](#ai-providers)
- [Personas](#personas)
- [Configuration](#configuration)
- [macOS permissions](#macos-permissions)
- [Privacy](#privacy)
- [Local data monitor](#local-data-monitor)
- [Database schema](#database-schema)
- [Components in detail](#components-in-detail)
- [Landing page](#landing-page)
- [Troubleshooting](#troubleshooting)
- [Limitations](#limitations)

## How it works

```
 ~/Library/Messages/chat.db              your calendars (EventKit)        your screen (on request)
            |                                    |                                 |
            v                                    v                                 v
 packages/ingest/imessage_export.py   packages/ingest/calendar_reader.py   packages/ingest/screen_reader.py
            |                                    |                                 |
            v                                    |                    one vague sentence, made locally
 packages/parse/sms_parser.py                    |                                 |
            |                                    |                                 |
            v                                    |                                 |
 packages/db/db.py -> ~/.twin/twin.duckdb        |                                 |
            ^               |                    |                                 |
            |               v                    v                                 |
    run_pipeline.py      buddy.py  <-------------+---------------------------------+
                            |
                            |  scrubbed request only
                            v
          the provider you picked (Anthropic, OpenAI, Google Gemini, or xAI)
```

1. **Ingest.** Every time Twin starts, it runs the pipeline in `run_pipeline.py`: copy your Messages database to a temp folder, read the last 200 messages, and delete the copy. You can also run the pipeline by hand.
2. **Parse.** Each message goes through a regex parser that picks out bank transaction SMS: debit or credit, amount, merchant or UPI handle, and reference number. Everything else is ignored.
3. **Store.** Parsed transactions go into `~/.twin/twin.duckdb`. Every insert also writes a row to an `access_log` table that records what was read and when. Messages already stored are skipped.
4. **Chat.** `buddy.py` reads the five most recent transactions, turns them into vague phrases like "spent money on food and dining earlier this week", adds today's calendar events, and sends that with your message to your provider. The reply appears in the widget.

5. **Nudge.** On launch and every 15 minutes, Twin compares upcoming calendar events (next 24 hours) with your last three days of Messages, all on your Mac. If a message about the same kind of thing (travel, an appointment, a meeting, plans) also carries a cue (a payment, something to bring, a change of plans, a reminder), the bubble gets one line such as "Flight to X is tomorrow, and a recent message mentions something about a payment." No provider request is made for it.

6. **Catch-up.** Ask "what did I miss?" or "catch me up" and Twin counts today's incoming messages by vague topic (plans, payments, travel, appointments, work, deliveries, bank alerts, other) and answers with something like "Today you got a few messages about plans and one message about a payment. Nothing looks urgent." It's built on your Mac from counts only, never quotes a message, and makes no provider request, so it works without an API key.

7. **Voice.** Hold Cmd+Shift+V, or hold the small dot next to the input field, and say something. Twin records while you hold it, turns it into text on-device with Apple's Speech framework, and runs it through the same `submit()` flow as anything you type, so it gets the same scrubbing and the same answer. This works regardless of whether spoken replies are on. Reading replies back out loud with macOS's built-in `say` is opt-in and off by default: turn it on with `/voice on` for that session, or off again with `/voice off`. When it's on, every persona shares the same voice, since persona character lives in what's written, not in text-to-speech. The first time you use voice input, Twin explains what's about to happen before macOS asks for Microphone and Speech Recognition access. If either is denied, Twin says so in the bubble instead of failing silently, and never sends audio or transcribed text anywhere except the same redacted request that already goes to your provider for typed messages.

Filing lookup and document ingestion follow a separate path:

```
 SEC EDGAR (data.sec.gov)                 your files (PDF, JPG, PNG)
            |                                        |
            v                                        v
   research_search.py                       document_ingest.py
            |                                        |
  filing excerpt, unscrubbed               pypdfium2 (PDF text layer)
  (public disclosure)                      or Donut OCR (scanned pages, photos)
            |                                        |
            |                     packages/db/db.py -> ~/.twin/twin.duckdb
            |                                        |
            |                     excerpt, redacted, one document active at a time
            v                                        v
                              buddy.py
                                  |
                          scrubbed request only
                                  v
           the provider you picked (Anthropic, OpenAI, Google Gemini, or xAI)
```

8. **Filing lookup.** Ask something like "Tesla's last 10-K" or "summarize Apple's most recent 8-K" and `research_search.py` resolves the company name or ticker, fetches the filing from EDGAR, and selects an excerpt from the section the question needs. The excerpt is a public company disclosure, so `scrub_system()` passes it through unscrubbed. Every request needs a contact string, set through `SEC_EDGAR_CONTACT` or `/setup edgar`. Without one, Twin asks for a contact instead of looking anything up.

9. **Document ingestion.** `/ingest <path>` reads a PDF or a JPG/PNG photo with `document_ingest.py`, stores the full extracted text in the `documents` table in `~/.twin/twin.duckdb`, and keeps a truncated excerpt active for chat until `/forget` or the next `/ingest` replaces it. A document is your own data, so `redact.py`'s categories run on it before anything reaches your provider. The currency, account number, and generic number patterns built for bank SMS text are skipped for this excerpt specifically, so a receipt's total or a document's date reaches your provider as a real value.

## Repository layout

```
digital-twin/
├── buddy.py                    desktop app: setup window, widget, personas, hotkey, prompt building, request scrubbing
├── document_ingest.py          PDF and receipt-photo extraction: pypdfium2 for PDF text, Donut OCR for images and scanned pages
├── llm_providers.py            provider clients, key validation, Keychain storage
├── redact.py                   regex-based PII redaction: email, phone, government-ID-shaped numbers, credit cards, addresses
├── research_search.py          SEC EDGAR filing lookup: company resolution, filing fetch, excerpt selection
├── run_pipeline.py             ingest: Messages -> parser -> twin.duckdb
├── setup.py                    py2app build script for Twin.app
├── VERSION                     single source of truth for the app version (setup.py, build_dmg.sh, release.sh all read it)
├── scripts/
│   ├── build_dmg.sh            builds, signs, and packages Twin.app into a distributable .dmg
│   ├── check_version_sync.sh   fails if landing/index.html's download link doesn't match VERSION
│   └── release.sh              tags, pushes, and publishes a GitHub Release with the built .dmg attached
├── packages/
│   ├── ingest/
│   │   ├── imessage_export.py  reads recent messages from ~/Library/Messages/chat.db
│   │   ├── digest.py           counts today's messages by vague topic for the catch-up summary
│   │   ├── nudges.py           matches upcoming events to recent messages, returns only a vague cue
│   │   ├── calendar_reader.py  reads today's and tomorrow's events through EventKit
│   │   └── screen_reader.py    one screenshot, on-device text recognition, deleted right after
│   ├── parse/
│   │   └── sms_parser.py       regex parser for bank transaction SMS (Rs / INR, UPI)
│   ├── db/
│   │   └── db.py               DuckDB schema, inserts, dedup check, access log
│   └── ui/
│       └── app.py              Streamlit monitor for everything in twin.duckdb
├── assets/
│   ├── icon/                   make_icon.py, Twin.icns, and a 1024px preview
│   ├── dmg/                    make_dmg_background.py and the generated .dmg installer background
│   └── fonts/                  Bricolage Grotesque and JetBrains Mono, with their OFL licenses
├── landing/
│   ├── index.html              static landing page (single file, no build step)
│   └── package.json            Vercel CLI for deploying the page (the page itself has no dependencies)
├── LICENSE                     MIT
└── docs/
    └── RELEASING.md            ad-hoc signing, opening a downloaded build, release steps, optional notarization
```

Your data lives outside the repo, in `~/.twin/`:

```
~/.twin/
├── twin.duckdb                 your transactions, ingested documents, and access log (created during setup)
├── config.json                 your name, persona, provider, and setup state
└── buddy.log                   log output when running as Twin.app
```

Your API key is not in either folder. It's stored in your macOS Keychain.

## Requirements

- **macOS.** Twin reads the Messages database, reads your calendars through EventKit, and uses AppKit for the translucent window. It won't work on Linux or Windows.
- **Python 3 with Tk.** Developed on Python 3.12 with Tk 9. The python.org installer includes Tk. With Homebrew, also run `brew install python-tk`.
- **An API key** from one of the [supported providers](#ai-providers). You paste it into the setup window on first run.
- **Internet access for the Donut model, once.** The first time you run `/ingest` on an image or a scanned PDF page, Twin downloads an 800 MB checkpoint from Hugging Face to `~/.cache/huggingface`. Every call after that loads from the local cache.
- **A contact for SEC EDGAR, only for filing lookup.** Set `SEC_EDGAR_CONTACT`, or Twin asks for one the first time you look up a filing and saves it to `config["edgar_contact"]`.
- **`/ingest` needs a source install, not the downloadable app.** `document_ingest.py`'s dependencies (`pypdfium2`, `torch`, `transformers`, `sentencepiece`, `protobuf`, `Pillow`) aren't bundled into `Twin.app` or the `.dmg`: installing them, plus everything they transitively pull in, would take the app from about 150 MB to nearly 1 GB for a feature most people won't use. `buddy.py` imports `document_ingest` lazily, so the packaged app launches and runs normally either way; typing `/ingest` in it just explains that it needs a source install with those packages (see [Installation](#installation)). Everything else in this README works the same in both the packaged app and a source install.

Python packages:

| Package | Used by | Why |
|---|---|---|
| `pyobjc-framework-Speech`, `pyobjc-framework-AVFoundation` | `voice.py` | on-device speech-to-text and microphone recording |
| `anthropic` | `llm_providers.py` | Anthropic API client. The other providers use plain HTTPS. |
| `duckdb` | `buddy.py`, `packages/db`, `packages/ui` | local database |
| `python-dotenv` | `buddy.py` | loads `.env` when running from a terminal |
| `pynput` | `buddy.py` | global hotkey |
| `pyobjc-framework-Cocoa`, `pyobjc-framework-Quartz` | `buddy.py` | translucent window, fonts, and app focus (optional, Twin falls back to a plain window without them) |
| `pyobjc-framework-EventKit` | `calendar_reader.py` | reading calendar events |
| `pyobjc-framework-Vision` | `screen_reader.py` | on-device text recognition for screen questions |
| `streamlit`, `pandas` | `packages/ui/app.py` | data monitor (optional) |
| `py2app` | `setup.py` | building Twin.app (optional) |
| `pypdfium2` | `document_ingest.py` | reads a PDF's embedded text layer and rasterizes scanned pages for OCR (optional, only for `/ingest`) |
| `transformers`, `torch` | `document_ingest.py` | loads and runs the Donut OCR model, CPU only, no GPU required (optional, only for `/ingest`) |
| `sentencepiece`, `protobuf` | `document_ingest.py` | tokenizer support for the Donut model (optional, only for `/ingest`) |
| `Pillow` | `document_ingest.py`, `assets/icon/make_icon.py` | opens receipt photos for OCR, regenerates the icon (optional unless you use `/ingest`) |

## Installation

```bash
git clone https://github.com/woustachemax/twin.git
cd twin

python3 -m venv .venv
source .venv/bin/activate

pip install anthropic duckdb python-dotenv pynput pyobjc-framework-Cocoa pyobjc-framework-Quartz pyobjc-framework-Vision pyobjc-framework-EventKit pyobjc-framework-Speech pyobjc-framework-AVFoundation
pip install streamlit pandas
pip install pypdfium2 torch transformers sentencepiece protobuf Pillow
```

The second `pip install` is only needed for the data monitor. The third is only needed for `/ingest` (receipt and document OCR): it pulls in `torch` and `transformers`, so it's a much bigger download than everything else here combined. Skip it if you don't need `/ingest`; the rest of Twin works fine without it. This is also why it's left out of the downloadable `.dmg` (see [Requirements](#requirements)).

Then run it from the terminal:

```bash
python3 buddy.py
```

You don't need a `.env` file. The setup window asks for your key. If you do have a key in `.env` or your shell (see [Configuration](#configuration)), the setup window fills it in for you.

## Building the app

To get a double-clickable `Twin.app`:

```bash
pip install py2app
rm -rf build dist && python3 setup.py py2app && codesign --force --deep -s - dist/Twin.app
open dist/Twin.app
```

The app bundles its own Python, Tcl/Tk, fonts, and icon, so it doesn't depend on your Python install. When running as the app, logs go to `~/.twin/buddy.log`.

A few things to know:

- The app is ad-hoc signed, not signed with a paid Developer ID or notarized. That's the deliberate, current shipping state, not a placeholder. On the Mac that built it, that's invisible: it just opens. On any other Mac, the first launch needs clearing Gatekeeper's block; see [Installing a release build](#installing-a-release-build) for the one-line install script (which clears it for you) or the manual steps. See [docs/RELEASING.md](docs/RELEASING.md) for why, and what real Developer ID signing would take if that's ever worth it.
- macOS ties permissions to the app's signature, and every rebuild gets a new one. After rebuilding, turn Twin back on under Full Disk Access, Accessibility, and Screen Recording.
- `setup.py` works around a few py2app issues with uv-managed Python: it raises the recursion limit, handles a built-in `zlib`, and copies the Tcl/Tk libraries into the bundle.

To change the icon, edit `assets/icon/make_icon.py`, run `python3 assets/icon/make_icon.py`, and rebuild.

## Building a distributable .dmg

`scripts/build_dmg.sh` runs the py2app build above, signs the result, and packages it into `dist/Twin-<version>.dmg`, a normal drag-into-Applications installer with a background image and an Applications shortcut (see `assets/dmg/`):

```bash
brew install create-dmg   # optional; the script falls back to a plain hdiutil .dmg without it
scripts/build_dmg.sh
```

With no environment variables set (the normal way to run it), this produces the same ad-hoc-signed build as above, just in `.dmg` form, and that's what actually ships (see the download on the [landing page](#landing-page)). Anyone opening it on a Mac other than the one that built it needs to clear Gatekeeper's block the same way described in [Installing a release build](#installing-a-release-build); that's expected, not a bug to chase down. `docs/RELEASING.md` covers the optional `CODESIGN_IDENTITY`/`NOTARY_PROFILE` path if a paid Developer ID account ever becomes worth it.

The version number for the `.dmg` filename, the app bundle, the git tag, and the landing page's download link all come from the single `VERSION` file at the repo root; see [Versioning](docs/RELEASING.md#versioning) for how to cut a release with `scripts/release.sh` without them drifting apart.

## Installing a release build

This is for anyone who just wants to run Twin, not build it from source.

**Quick install (recommended):**

```bash
curl -fsSL https://raw.githubusercontent.com/woustachemax/twin/main/scripts/install.sh | bash
```

`scripts/install.sh` downloads the latest release's `.dmg` from GitHub, mounts it, copies `Twin.app` into `/Applications`, and clears the quarantine flag so Gatekeeper doesn't block the first launch. It's safe to re-run (an existing install is replaced, not skipped), and it fails loudly (clear error, non-zero exit) if any step doesn't work rather than continuing silently.

**Manual download**, if you'd rather not pipe a script into `bash`: grab the `.dmg` from the [latest release](https://github.com/woustachemax/twin/releases/latest), drag `Twin.app` into Applications, then clear the first-launch block yourself:

1. Double-click Twin. macOS blocks it: "Twin can't be opened because Apple cannot check it for malicious software." Click Done.
2. Open **System Settings → Privacy & Security**.
3. Scroll down to the Security section. You'll see "Twin was blocked to protect your Mac" with an **Open Anyway** button next to it.
4. Click **Open Anyway**, then confirm **Open** in the dialog that follows (Touch ID or your password may be requested).

Or skip that with `xattr -cr /Applications/Twin.app` in Terminal after dragging it in.

Either way you install it, this build doesn't include `/ingest` (receipt and document OCR); see [Requirements](#requirements) for why and how to get it via a source install.

## First run

The first time Twin starts (or any time `~/.twin/config.json` is missing or setup wasn't finished), it opens a setup window before the chat widget. It has four steps and a summary:

1. **Provider.** Pick Anthropic, OpenAI, Google Gemini, or xAI, and paste your API key. Twin checks the key with one small test request, then saves it to your macOS Keychain. If the key is wrong, out of credits, or from a different provider, it says so and lets you try again.
2. **Name.** What Twin should call you. It's filled in with the first name from your Mac account.
3. **Buddy.** Pick one of the five personas. Twin is selected by default.
4. **Permissions.** Buttons for Calendar, the keyboard shortcut, and Messages. macOS only prompts when you click one. You can skip this page.
5. **Done.** A summary of your choices. Twin creates your database at `~/.twin/twin.duckdb` here.

Closing the window before finishing quits Twin, and it starts from step 1 next time. Your provider and saved key are filled in again.

## Usage

| Action | How |
|---|---|
| Show or hide Twin | `Cmd+Shift+Space` from anywhere |
| Ask something | Type in the box and press Return |
| Move the widget | Drag it |
| List personas | `/persona`, then click one to switch |
| Switch persona | `/persona <key>`, for example `/persona gengar` |
| Change provider or key | `/setup` |
| Catch up on today's Messages | "what did I miss?", "catch me up" |
| Ask about your screen | "what am I looking at?", "what's on my screen?" |
| Look up an SEC filing | "Tesla's last 10-K", "summarize Apple's most recent 8-K" |
| Read a document | `/ingest <path>`, for example `/ingest ~/Downloads/receipt.jpg` |
| Stop referencing that document | `/forget` |
| Check what would be redacted before sending | `/redact-test <text>` |
| Talk instead of typing | hold Cmd+Shift+V, or hold the dot by the input field |
| Turn spoken replies on (off by default) or off | `/voice on`, `/voice off` |

Twin fills in context on its own. Ask "what should I be doing?" and it may mention your next calendar event. Ask "have I been spending a lot?" and it answers from the vague summary, never with amounts.

Every launch refreshes your transactions from Messages before the widget opens. To refresh by hand:

```bash
python3 run_pipeline.py
```

You'll see something like `Inserted 12 transactions (3 duplicates skipped)`. It only adds messages it hasn't stored before.

## AI providers

| Provider | Default model | API used | Key from |
|---|---|---|---|
| Anthropic | `claude-sonnet-4-6` | Anthropic Python SDK | console.anthropic.com/settings/keys |
| OpenAI | `gpt-6-luna` (reasoning off, storage off) | Responses API | platform.openai.com/api-keys |
| Google Gemini | `gemini-3.5-flash-lite` (thinking set to minimal) | OpenAI-compatible Chat Completions | aistudio.google.com/apikey |
| xAI | `grok-4.3` | Responses API | console.x.ai |

Keys are stored in the macOS Keychain as "Twin &lt;provider&gt; API key". To remove one:

```bash
security delete-generic-password -s "Twin Anthropic API key"
```

To use a different model, set `"model"` in `~/.twin/config.json` or the `TWIN_MODEL` environment variable.

## Personas

Pick one during setup, switch live with `/persona <key>`, or start with the `PERSONA` environment variable. Commands and the variable take the **key** in the first column, not the display name. Your choice is saved and used on the next launch.

| Key | Name | Avatar | Personality | Background | Accent | Text |
|---|---|---|---|---|---|---|
| `twin` | Twin | bun | Warm, cheerful, easygoing. The default. | `#0E0D12` | `#C6FF4A` | `#F7F2E8` |
| `gengar` | Shade | ghost | Mischievous shadow-ghost, sly and teasing. Eyes `#FF4F6E`. | `#17111F` | `#A77BFF` | `#EEE8F7` |
| `ember` | Ember | ghost | Bright, energetic spark who hypes you up. Eyes `#FFD166`. | `#1E120D` | `#FF7A3D` | `#FFEDE4` |
| `calm` | Luna | ghost | Gentle, patient, soothing. | `#141828` | `#A5B4FF` | `#E7EBFA` |
| `plain` | Assistant | monogram | Neutral and professional, no quirks. | `#1E1E20` | `#8E8E93` | `#F2F2F7` |

```bash
PERSONA=calm python3 buddy.py
```

Only personas with `"resizable": True` (currently Luna) can be resized, by dragging the grip in the bottom-right corner, between 360x350 and 640x720. Luna opens larger (420x460) and remembers the size you drag it to until you quit. Every other persona keeps the compact 360x350 widget and shows no grip.

The Dock icon follows the active persona and changes when you switch with `/persona`. The icons are PNGs in `assets/icon/personas/`, one per persona key. `python3 assets/icon/make_icon.py` regenerates them from each persona's palette, along with `Twin.icns`.

An unknown key prints a warning and is ignored. Every other widget color (border, bubble, input field, muted text, avatar) comes from these three palette values through `theme_for()` in `buddy.py`.

Every message Twin shows, including errors and refusals, is in the current persona's voice. When the provider can't be reached, each persona has its own written lines for that.

To add your own persona, add an entry to the `PERSONAS` dict with `name`, `tagline`, `avatar` (`bun`, `ghost`, or `monogram`), `resizable`, optional `size` (width, height), `system_prompt`, `palette`, `greeting` (may use `{name}`), `idle`, `busy`, `done`, `frame`, and `offline`.

## Configuration

| Variable | Default | Effect |
|---|---|---|
| `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `XAI_API_KEY` | none | Filled into the setup window, and used instead of the Keychain key for that provider when set. |
| `PERSONA` | saved choice, then `twin` | Persona key to start with. |
| `TWIN_MODEL` | provider default | Model to use for the chosen provider. |
| `TWIN_DB_PATH` | `~/.twin/twin.duckdb` | Database used by the pipeline, `buddy.py`, and the monitor. |
| `TWIN_CONFIG_PATH` | `~/.twin/config.json` | Where your name, persona, provider, and setup state are saved. `research_search.py` also reads this path for a saved SEC EDGAR contact. |
| `TWIN_OCR_SHORTCUT` | `Twin Extract Text` | Shortcut used for text recognition if the Vision package isn't installed. |
| `TWIN_NUDGE_MINUTES` | `15` (or `nudge_minutes` in `config.json`) | How often Twin checks Messages against upcoming events. `0` turns nudges off. |
| `SEC_EDGAR_CONTACT` | none, falls back to `config["edgar_contact"]` | Contact string sent in the `User-Agent` header on every SEC EDGAR request. Required before filing lookup works; set here, through onboarding, or with `/setup edgar`. |
| `BUDDY_DEBUG` | off | Set to `1` to print every outgoing API request to stderr. See [Privacy](#privacy). |

Constants near the top of `buddy.py` cover the rest: `MAX_TOKENS`, `HOTKEY`, widget size, and how often the calendar is refreshed (every 5 minutes, or 30 seconds after an error).

## macOS permissions

The setup window's permissions page lets you grant these up front. When running from a terminal, grant them to the terminal app. When running `Twin.app`, grant them to Twin.

| Permission | Needed for | Where to grant it |
|---|---|---|
| Full Disk Access | reading `~/Library/Messages/chat.db` | System Settings > Privacy & Security > Full Disk Access |
| Accessibility | the global hotkey (`pynput`) | System Settings > Privacy & Security > Accessibility |
| Calendars (full access) | reading calendar events | Allow when prompted, or System Settings > Privacy & Security > Calendars |
| Screen Recording | answering questions about your screen | System Settings > Privacy & Security > Screen & System Audio Recording |
| Microphone, Speech Recognition | holding Cmd+Shift+V to talk | Allow when prompted, or System Settings > Privacy & Security > Microphone / Speech Recognition |

Twin keeps working without any of them. It says in the widget which feature is off and where to turn it on. Calendar permission requests run in a separate helper process, so the prompt never interrupts the widget.

## Privacy

### What stays on your Mac

- `~/.twin/twin.duckdb`, which holds the parsed transactions, the full raw SMS text, and the full text of anything loaded with `/ingest`.
- `~/.twin/config.json`, which holds your name, persona, provider, and your SEC EDGAR contact if you set one.
- Your API key, in the macOS Keychain.
- The temporary copy of the Messages database, which is deleted right after reading.
- Screenshots, which are read on-device and deleted right away.
- The Donut OCR model, downloaded once to `~/.cache/huggingface` and reused after that.

There are no accounts, no sync, no analytics, and no Twin server. Everyone who installs Twin gets their own `twin.duckdb`, created in their own home folder during setup. It lives outside the repo, so it never ends up in git. Nothing is shared or pooled between users.

### What is sent to your provider

Each chat message makes one request to the provider you picked, containing:

- your message
- your name
- the current time and day of the week
- the titles and times of today's calendar events
- a vague summary of up to five recent transactions, such as `- received a payment yesterday`
- when you ask about your screen, one sentence describing it, such as "They're in Mail, looking at what seems to be an email inbox."
- when you look up a filing, an excerpt from the filing itself, up to 16,000 characters, unredacted
- when a document is active, an excerpt of what `/ingest` extracted, up to 20,000 characters, redacted first

Nudges are built and shown entirely on your Mac and are never sent anywhere. They contain the event title (scrubbed like any other) and one of four fixed phrases, never message text. Which nudges were shown is kept in `~/.twin/nudged.json` as hashes so the same one isn't repeated.

Outside of a filing excerpt or a document excerpt, no request includes amounts, balances, account numbers, reference numbers, merchant names, UPI handles, raw SMS text, screenshots, or text read from your screen.

`buddy.py` treats a filing excerpt and a document excerpt differently once `build_request()` adds them to the system prompt. A filing excerpt is a public company disclosure filed with the SEC, so `buddy.py` marks it for `FILING_LINE_RE` and `scrub_system()` passes it through unchanged. A document excerpt is your own data, so `buddy.py` marks it for `DOCUMENT_LINE_RE` instead, and `scrub_system()` runs `redact.py`'s categories on it in full: email addresses, phone numbers, government-ID-shaped numbers, credit card numbers, and street addresses each come back as a label like `[email]` or `[card]`. `scrub_system()` skips the currency, account number, and generic number patterns for a document excerpt specifically, the same patterns that turn a transaction amount into `[amount]` elsewhere in the request, so a receipt's total or a document's date reaches your provider as a real value rather than a placeholder.

Two other requests go to the provider: the small test request that checks your key during setup, and short requests that rephrase Twin's status messages in the persona's voice. Neither contains your data.

### What SEC EDGAR requests contain

Looking up a filing sends requests to SEC's own servers at sec.gov and data.sec.gov, not to your AI provider: one to resolve a company name or ticker to a CIK number, one to read the company's recent filings, and one to fetch the filing document itself. Each request carries a `User-Agent` header built from a contact string, since SEC's fair-access policy requires one. `research_search.py` reads that contact from `SEC_EDGAR_CONTACT` in the environment, or from `config["edgar_contact"]` if you set one through the onboarding prompt or `/setup edgar`. It has no hardcoded fallback contact. Until one is set, filing lookup fails and Twin asks for a contact email instead of guessing one.

### How that's enforced

`buddy.py` doesn't rely on the prompt alone. It checks each request in several layers:

1. **Allowlisted vocabulary.** Each line of the transaction summary must exactly match a fixed list of phrases (an activity like "spent money on groceries" plus a time phrase like "earlier this week"). Any line that doesn't match is dropped.
2. **Targeted PII detection.** `redact.py` runs on every message and on every system prompt line that isn't a filing excerpt, ahead of the pattern matching in the next layer. It matches email addresses, phone numbers, government-ID-shaped numbers (US Social Security numbers, Indian Aadhaar numbers), credit card numbers checked with a Luhn checksum, and street addresses, and replaces each with a label such as `[email]` or `[card]`.
3. **Scrubbing.** Before sending, the system prompt and every message are scrubbed. Sentences that look like bank SMS are replaced with `[removed: SMS text]`. Currency amounts, account numbers, email or UPI handles, long IDs, and numbers are replaced with `[amount]`, `[account]`, `[id]`, and `[number]`. Calendar titles are cut to 80 characters and scrubbed too. A document excerpt skips only the currency, account number, and number patterns in this layer; the PII detection in the layer above still runs on it.
4. **Field allowlist.** Only the system prompt, the messages, and a token limit are passed to the provider client. Anything else is dropped.
5. **Local screen summary.** The screen sentence is built on your Mac from the app name and a guess at the kind of content. The recognized text never leaves `screen_reader.py`.

To see exactly what leaves your machine, run:

```bash
BUDDY_DEBUG=1 python3 buddy.py
```

Every request is printed to stderr before it's sent, along with the provider, the model, and a list of what was redacted.

## Local data monitor

A Streamlit dashboard that shows everything in `~/.twin/twin.duckdb`: parsed transactions next to the access log. It refreshes every 3 seconds, so you can watch rows appear while the pipeline runs.

```bash
streamlit run packages/ui/app.py --browser.gatherUsageStats false
```

It opens the database read-only and respects `TWIN_DB_PATH`. The dashboard's own code makes no network calls. Streamlit itself collects anonymous usage statistics by default, which the flag above turns off. To turn them off permanently, add `gatherUsageStats = false` under `[browser]` in `~/.streamlit/config.toml`.

While the dashboard is open, the startup refresh may find the database busy. Twin says so and tries again on the next launch.

## Database schema

`~/.twin/twin.duckdb` has three tables, created by `packages/db/db.py` if they don't exist yet.

**`transactions`**

| Column | Type | Notes |
|---|---|---|
| `id` | VARCHAR | UUID, primary key |
| `timestamp` | TIMESTAMP | when the SMS arrived |
| `type` | VARCHAR | `debited` or `credited` |
| `amount` | DOUBLE | |
| `merchant` | VARCHAR | merchant name or UPI handle, may be null |
| `ref_number` | VARCHAR | bank reference, may be null |
| `raw_text` | VARCHAR | full SMS text, also used to skip duplicates |

**`documents`**

| Column | Type | Notes |
|---|---|---|
| `id` | VARCHAR | UUID, primary key |
| `ingested_at` | TIMESTAMP | UTC, when `/ingest` ran |
| `filename` | VARCHAR | original file name only, not the full path |
| `doc_type` | VARCHAR | `pdf` or `image` |
| `extraction_method` | VARCHAR | `pdf_text`, `donut`, or `pdf_text+donut` for a PDF with both kinds of page |
| `content` | VARCHAR | full extracted text |
| `content_hash` | VARCHAR | SHA-256 of `content`, used to skip duplicates |
| `char_count` | INTEGER | length of `content` |

**`access_log`**

| Column | Type | Notes |
|---|---|---|
| `id` | VARCHAR | UUID, primary key |
| `timestamp` | TIMESTAMP | UTC |
| `source` | VARCHAR | for example `imessage_pipeline` or `document_ingest` |
| `action` | VARCHAR | for example `insert_transaction` or `insert_document` |
| `data_touched` | VARCHAR | human-readable summary of what was written |

To look at it yourself:

```bash
python3 -c "import duckdb, os; print(duckdb.connect(os.path.expanduser('~/.twin/twin.duckdb'), read_only=True).sql('SELECT * FROM transactions'))"
```

## Components in detail

### `buddy.py`

The desktop app. On first run it shows the setup window described in [First run](#first-run). After that it shows a borderless Tk widget, made translucent through AppKit when PyObjC is available, with an animated avatar, a speech bubble, and an input field. The fonts match the landing page.

Two helper processes run alongside it, both started by relaunching the same program with a flag: `--hotkey-listener` watches for the global hotkey, and `--request-calendar` asks macOS for calendar access. Keeping them out of the widget's process means neither can freeze or crash the window. Calendar events and API calls run in background threads.

### `document_ingest.py`

Extracts text from a PDF or a JPG/PNG photo so Twin can answer questions about it. `ingest_document()` reads a PDF's embedded text layer directly with `pypdfium2` when a page has one, and treats a page with fewer than 20 characters of extractable text as scanned: it renders that page to an image and reads it with Donut, a vision-to-sequence model. A JPG or PNG goes straight to Donut. The checkpoint is `naver-clova-ix/donut-base-finetuned-cord-v2`, an 800 MB model fine-tuned on receipts and licensed under MIT. It runs on CPU, with no GPU required, at a cost of a few seconds per image, and downloads once on first use to `~/.cache/huggingface`. Because the checkpoint targets receipts specifically, extraction quality on other kinds of document photos is weaker. `ingest_document()` returns the full extracted text for storage and a copy truncated to 20,000 characters for the prompt. `format_document_context()` marks each line for `buddy.py`'s `scrub_system()`, which runs `redact.py`'s categories on it in full but skips the currency, account number, and generic number patterns, so a receipt's total or a document's date reaches the model as a real value.

Load a file and ask about it in the widget:

```
/ingest ~/Downloads/receipt.jpg
```

### `llm_providers.py`

One client per provider behind a common `complete()` method. Anthropic uses the official SDK. OpenAI and xAI use the Responses API, and Gemini uses its OpenAI-compatible Chat Completions endpoint, all over plain HTTPS. It also checks keys, maps provider errors (bad key, rate limit, offline, server error) to plain messages, and stores keys in the Keychain.

### `redact.py`

Redacts personal information from text before `buddy.py` includes it in a request to any provider. It matches five categories: email addresses; phone numbers, in a compact 10-digit form and a looser grouped form that covers formats like the UK's or India's; government-ID-shaped numbers, US Social Security numbers and Indian Aadhaar numbers; credit card numbers, a 13 to 19 digit candidate that must pass a Luhn checksum before it counts as a match; and street addresses, a number followed by words and a recognized suffix such as "St" or "Avenue". `redact()` returns the redacted text along with a list of what it found, which makes it usable as a dry run with no request sent anywhere. `buddy.py`'s `scrub()` calls it on every message and every system prompt line before its own currency, account number, and generic number patterns run.

Check what a piece of text would trigger, without sending it anywhere:

```
/redact-test call me at 555-123-4567 or email jane@example.com
```

### `research_search.py`

Looks up SEC filings by company name or ticker and returns excerpt text pulled directly from the filing document. `parse_filing_query()` matches phrasings like "what changed in Apple's last 10-K" or "summarize Tesla's most recent 8-K" against two patterns, company name before the form type and form type before the company name, and recognizes 10-K, 10-K/A, 10-Q, 8-K, DEF 14A, S-1, and spelled-out aliases such as "annual report". `resolve_company()` matches the name or ticker against SEC's `company_tickers.json` and keeps it cached in memory after the first request. `latest_filing()` reads the company's submissions from EDGAR and returns the most recent filing of the requested type. It only searches EDGAR's "recent" filings window, so a form type the company has not filed in roughly the last year comes back as not found even if an older one exists. `fetch_filing_excerpt()` downloads the filing document, strips its HTML, and jumps to the section that a "what changed" or "summarize" question usually needs, the Management's Discussion and Analysis section for a 10-K or 10-Q. It finds that section by matching the last occurrence of the item header in the document rather than the first, since the first occurrence is usually the filing's own table of contents. Every request carries the contact set through `SEC_EDGAR_CONTACT` or `/setup edgar`, and stays under 10 requests per second.

Ask Twin directly, for example:

```
Tesla's last 10-K
```

### `run_pipeline.py`

Ties the pipeline together: fetch messages, parse them, skip anything already stored, and insert the rest with `source="imessage_pipeline"`. `ingest()` returns the counts, and running the file prints them.

### `packages/ingest/imessage_export.py`

Copies `chat.db` (plus its `-wal` and `-shm` files) to a temp folder so it never touches the live database, opens the copy read-only, and returns the most recent messages oldest first. Newer macOS versions store some message text only in the `attributedBody` blob, so it decodes that too, with `plutil` as a fallback. Timestamps are converted from Apple's 2001 epoch.

```bash
python3 packages/ingest/imessage_export.py
```

### `packages/ingest/calendar_reader.py`

Uses Apple's EventKit framework to read every event today or tomorrow across all calendars, including each occurrence of repeating events. It reads the calendar data directly, so Calendar.app never opens. It returns the title, start time, all-day flag, and calendar name. Permission problems become a readable `CalendarAccessError`, with a separate message when Twin only has add-only access.

```bash
python3 packages/ingest/calendar_reader.py
```

### `packages/ingest/screen_reader.py`

Takes one screenshot with `screencapture` into a private temp folder, reads the text with Apple's Vision framework (or a Shortcut if Vision isn't installed), and deletes the folder right away. `describe_screen()` then turns the text and the app name into one vague sentence. Twin hides its own window while the screenshot is taken so it doesn't read itself.

```bash
python3 packages/ingest/screen_reader.py
```

### `packages/parse/sms_parser.py`

Regex parser for bank transaction SMS in the formats Indian banks commonly use: `Rs.`, `Rs`, or `INR` amounts, `debited`/`credited`/`spent` keywords, UPI handles like `name@upi`, merchants in "at SWIGGY using UPI" phrasing, and `Ref No`, `RRN`, `UPI Ref`, or `Txn ID` references. A message is only kept if it has both a transaction type and an amount.

```bash
python3 packages/parse/sms_parser.py
```

### `packages/db/db.py`

Opens the database (creating `~/.twin` with mode `700` if needed), creates the schema, and provides `insert_transaction`, `transaction_exists`, `insert_access_log`, and `get_recent_access_log`. Every transaction insert writes an access log entry.

### `packages/ui/app.py`

The Streamlit monitor described [above](#local-data-monitor).

## Landing page

`landing/index.html` is the project's marketing page: one static HTML file with inline CSS and vanilla JS, no build step, and no runtime dependencies. `package.json` only pulls in the Vercel CLI for deploying. Open the file directly in a browser or deploy the `landing/` folder to any static host.

```bash
open landing/index.html
```

It covers the pitch, the "why local" explanation, an animated features grid, persona preview cards, and a quick-install/manual-download toggle with a copy button for the install command. Animations respect the "reduce motion" system setting.

## Troubleshooting

| Problem | Fix |
|---|---|
| The setup window says the key didn't work | Check you copied the whole key and picked the matching provider. If it says the key is out of credits, check billing with that provider. |
| Twin says your provider didn't accept the key | Type `/setup` in the widget and paste a new key. |
| The setup window shows up again on launch | Setup wasn't finished, or `~/.twin/config.json` was removed. Finish the steps once. |
| Twin can't read Messages | Give Full Disk Access to Twin (or your terminal), then restart Twin. |
| Hotkey does nothing | Allow Twin (or your terminal) under Accessibility, then restart Twin. |
| "Calendar access isn't allowed yet" | Use the Calendar button on the setup window's permissions page, or turn on full access under Privacy & Security > Calendars. |
| "I can only add calendar events right now" | Twin has add-only access. Switch it to Full Access under Privacy & Security > Calendars. |
| Twin can't see your screen | Allow Twin (or your terminal) under Screen & System Audio Recording. |
| Permissions stopped working after rebuilding the app | Each rebuild gets a new signature. Turn Twin back on in each Privacy & Security list. |
| Twin knows nothing about your spending | Check Full Disk Access, and make sure `TWIN_DB_PATH` (if set) is the same for the pipeline and for Twin. |
| `ModuleNotFoundError: No module named '_tkinter'` | Install a Python with Tk (python.org installer, or `brew install python-tk`). |
| Plain opaque window instead of a translucent one | Install `pyobjc-framework-Cocoa` and `pyobjc-framework-Quartz`. |
| Pipeline inserts 0 transactions | It only reads the last 200 messages, and only bank SMS in the supported formats count. |

## Limitations

- macOS only.
- The SMS parser targets Indian bank and UPI message formats. Other formats are ignored until someone adds patterns for them.
- The pipeline reads only the 200 most recent messages per run.
- Twin only looks at the five most recent transactions when chatting.
- Each chat message is answered on its own. Twin doesn't remember earlier turns of the conversation.
- Answers about your screen are vague on purpose, since only a one-sentence summary is sent.
- Twin keeps one ingested document active at a time. Loading a new one with `/ingest` replaces the active document; older ones stay in `~/.twin/twin.duckdb` but are no longer part of the conversation until re-ingested.
- Filing lookup only searches EDGAR's "recent" filings window, roughly the last year of a company's activity. A form type filed further back comes back as not found even if it exists.
- Donut is fine-tuned on receipts. Extraction quality on other kinds of document photos is weaker.
- `Twin.app` ships ad-hoc signed, not signed with a paid Developer ID or notarized: a deliberate call for a portfolio project without real public download volume, not a gap to fill later. On a Mac other than the one that built it, the first launch needs clearing Gatekeeper's block; `scripts/install.sh` does this automatically, or see [Installing a release build](#installing-a-release-build) for the manual System Settings steps (or `xattr -cr`). See [docs/RELEASING.md](docs/RELEASING.md).
- Licensed under MIT (see `LICENSE`). The bundled fonts are under the SIL Open Font License.
