import glob
import os
import re
import shutil
import sqlite3
import tempfile

import imessage_export

ADDRESS_BOOK_DIR = os.path.expanduser("~/Library/Application Support/AddressBook")
ADDRESS_BOOK_PATH = os.path.join(ADDRESS_BOOK_DIR, "AddressBook-v22.abcddb")
THREAD_LIMIT = 60


class ContactError(Exception):
    def __init__(self, kind, name=""):
        super().__init__(kind)
        self.kind = kind
        self.name = name


def stage_copy(path):
    tmp_dir = tempfile.mkdtemp(prefix="twin_contacts_")
    tmp_db = os.path.join(tmp_dir, os.path.basename(path))
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(path + suffix):
            shutil.copy2(path + suffix, tmp_db + suffix)
    return tmp_dir, tmp_db


def read_only(path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def phone_key(value):
    digits = re.sub(r"\D", "", value or "")
    return digits[-10:] if len(digits) >= 10 else digits


def handle_key(handle_id):
    if "@" in handle_id:
        return handle_id.strip().lower()
    return phone_key(handle_id)


def address_book_paths():
    paths = [ADDRESS_BOOK_PATH] + sorted(glob.glob(os.path.join(ADDRESS_BOOK_DIR, "Sources", "*", "AddressBook-v22.abcddb")))
    return [p for p in paths if os.path.exists(p)]


def matching_records(conn, query):
    return conn.execute(
        """
        SELECT Z_PK, COALESCE(ZFIRSTNAME, ''), COALESCE(ZLASTNAME, '')
        FROM ZABCDRECORD
        WHERE LOWER(TRIM(COALESCE(ZFIRSTNAME, '') || ' ' || COALESCE(ZLASTNAME, ''))) = ?
           OR LOWER(COALESCE(ZFIRSTNAME, '')) = ?
           OR LOWER(COALESCE(ZNICKNAME, '')) = ?
           OR LOWER(COALESCE(ZORGANIZATION, '')) = ?
        """,
        (query, query, query, query),
    ).fetchall()


def handles_for(conn, owner):
    phones = [row[0] for row in conn.execute(
        "SELECT ZFULLNUMBER FROM ZABCDPHONENUMBER WHERE ZOWNER = ?", (owner,))]
    emails = [row[0] for row in conn.execute(
        "SELECT ZADDRESS FROM ZABCDEMAILADDRESS WHERE ZOWNER = ?", (owner,))]
    return {phone_key(p) for p in phones if phone_key(p)} | {e.strip().lower() for e in emails if e}


def find_contact_handles(name):
    paths = address_book_paths()
    if not paths:
        raise ContactError("no_contacts", name)
    query = name.strip().lower()
    keys, displays, display = set(), set(), None
    found_any = False
    for path in paths:
        tmp_dir, tmp_db = stage_copy(path)
        try:
            conn = read_only(tmp_db)
            try:
                for owner, first, last in matching_records(conn, query):
                    found_any = True
                    full = " ".join(part for part in (first, last) if part).strip()
                    displays.add(full.lower() or query)
                    display = display or full or name
                    keys |= handles_for(conn, owner)
            finally:
                conn.close()
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
    if not found_any:
        raise ContactError("not_found", name)
    if len(displays) > 1:
        raise ContactError("ambiguous", name)
    if not keys:
        raise ContactError("no_handles", name)
    return keys, display or name


def fetch_thread(name, limit=THREAD_LIMIT):
    keys, display = find_contact_handles(name)
    chat_db_tmp = imessage_export.stage_db_copy()
    try:
        conn = read_only(chat_db_tmp)
        try:
            handles = conn.execute("SELECT ROWID, id FROM handle").fetchall()
            matching = [rowid for rowid, handle_id in handles if handle_id and handle_key(handle_id) in keys]
            if not matching:
                raise ContactError("no_messages", name)
            placeholders = ",".join("?" * len(matching))
            rows = conn.execute(
                f"""
                SELECT m.ROWID, m.text, m.attributedBody, m.date, m.is_from_me
                FROM message m
                JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
                WHERE cmj.chat_id IN (
                    SELECT chat_id FROM chat_handle_join
                    GROUP BY chat_id
                    HAVING COUNT(*) = 1 AND MAX(handle_id) IN ({placeholders})
                )
                ORDER BY m.date DESC
                LIMIT ?
                """,
                [*matching, limit],
            ).fetchall()
        finally:
            conn.close()
    finally:
        shutil.rmtree(os.path.dirname(chat_db_tmp), ignore_errors=True)

    thread = []
    for _rowid, text, attributed_body, date, is_from_me in reversed(rows):
        if not text:
            text = imessage_export.extract_text_from_attributed_body(attributed_body)
        if not text:
            continue
        thread.append({
            "sender": "Me" if is_from_me else display,
            "text": text.strip(),
            "timestamp": imessage_export.convert_apple_timestamp(date),
        })
    if not thread:
        raise ContactError("no_messages", name)
    return display, thread
