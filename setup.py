import os
import sys
import sysconfig
import zlib

from setuptools import setup

sys.setrecursionlimit(20000)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
for package in ("ingest", "parse", "db"):
    sys.path.insert(0, os.path.join(BASE_DIR, "packages", package))

with open(os.path.join(BASE_DIR, "VERSION")) as f:
    VERSION = f.read().strip()

if not hasattr(zlib, "__file__"):
    placeholder = os.path.join(BASE_DIR, "build", "zlib-is-builtin")
    os.makedirs(os.path.dirname(placeholder), exist_ok=True)
    open(placeholder, "w").close()
    zlib.__file__ = placeholder

PYTHON_LIB_DIR = sysconfig.get_config_var("LIBDIR")
TCL_TK_FILES = [
    os.path.join(PYTHON_LIB_DIR, name)
    for name in ("libtcl9.0.dylib", "libtcl9tk9.0.dylib", "tcl9.0", "tk9.0")
]

FONT_DIR = os.path.join(BASE_DIR, "assets", "fonts")
FONT_FILES = sorted(
    os.path.join(FONT_DIR, name) for name in os.listdir(FONT_DIR) if name.endswith((".ttf", ".txt"))
)

PERSONA_ICON_DIR = os.path.join(BASE_DIR, "assets", "icon", "personas")
PERSONA_ICON_FILES = sorted(
    os.path.join(PERSONA_ICON_DIR, name) for name in os.listdir(PERSONA_ICON_DIR) if name.endswith(".png")
)

APP = ["buddy.py"]

PLIST = {
    "CFBundleName": "Twin",
    "CFBundleDisplayName": "Twin",
    "CFBundleIdentifier": "dev.twin.buddy",
    "CFBundleShortVersionString": VERSION,
    "CFBundleVersion": VERSION,
    "LSMinimumSystemVersion": "13.0",
    "LSApplicationCategoryType": "public.app-category.productivity",
    "NSHighResolutionCapable": True,
    "NSRequiresAquaSystemAppearance": False,
    "NSCalendarsFullAccessUsageDescription": (
        "Twin reads today's and tomorrow's events so it can mention what's coming up. It never changes "
        "your calendar, and nothing leaves your Mac except event titles and times when you chat."
    ),
    "NSCalendarsUsageDescription": (
        "Twin reads today's and tomorrow's events so it can mention what's coming up. It never changes "
        "your calendar, and nothing leaves your Mac except event titles and times when you chat."
    ),
    "NSMicrophoneUsageDescription": (
        "Twin records what you say while you hold the talk key or the mic dot, so it can turn it into text. "
        "The recording stays on your Mac and is deleted right after it's transcribed."
    ),
    "NSSpeechRecognitionUsageDescription": (
        "Twin turns your voice into text using Apple's on-device Speech framework, right here on your Mac. "
        "Nothing you say is sent anywhere to do this."
    ),
}

OPTIONS = {
    "argv_emulation": False,
    "iconfile": os.path.join(BASE_DIR, "assets", "icon", "Twin.icns"),
    "plist": PLIST,
    "includes": [
        "calendar_reader",
        "screen_reader",
        "imessage_export",
        "nudges",
        "digest",
        "voice",
        "sms_parser",
        "db",
        "run_pipeline",
        "llm_providers",
        "sqlite3",
        "tkinter",
        "tkinter.font",
    ],
    "packages": [
        "anthropic",
        "pynput",
        "duckdb",
        "dotenv",
        "httpx2",
        "httpcore2",
        "h11",
        "certifi",
        "pydantic",
        "pydantic_core",
        "jiter",
        "anyio",
        "truststore",
        "docstring_parser",
        "objc",
        "AppKit",
        "Foundation",
        "Quartz",
        "Vision",
        "EventKit",
        "ApplicationServices",
        "Speech",
        "AVFoundation",
        "CoreMedia",
        "CoreAudio",
    ],
    "excludes": [
        "PIL", "streamlit", "pandas", "numpy", "pyarrow", "polars", "matplotlib", "IPython", "pytest",
        "torch", "torchgen", "jax", "jaxlib", "onnx", "onnxruntime", "tensorflow", "sympy", "scipy",
        "networkx", "mpmath", "fsspec", "uvloop", "httpx", "httpcore", "requests", "email_validator",
        "setuptools", "pkg_resources", "jaraco", "more_itertools", "markupsafe", "attr", "cffi",
    ],
}

setup(
    app=APP,
    name="Twin",
    data_files=[("lib", TCL_TK_FILES), ("fonts", FONT_FILES), ("persona_icons", PERSONA_ICON_FILES)],
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
