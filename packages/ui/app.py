import os
import time
from datetime import datetime

import duckdb
import pandas as pd
import streamlit as st

POLL_SECONDS = 3

DEFAULT_DB_PATH = os.path.expanduser("~/.twin/twin.duckdb")


def resolve_db_path():
    return os.environ.get("TWIN_DB_PATH") or DEFAULT_DB_PATH


def load_data(db_path):
    con = duckdb.connect(db_path, read_only=True)
    try:
        txns = con.execute(
            "SELECT timestamp, type, amount, merchant, ref_number, raw_text, id FROM transactions ORDER BY timestamp DESC"
        ).df()
        logs = con.execute(
            "SELECT timestamp, source, action, data_touched, id FROM access_log ORDER BY timestamp DESC LIMIT 200"
        ).df()
    finally:
        con.close()
    return txns, logs


st.set_page_config(page_title="Digital Twin Monitor", layout="wide")

db_path = resolve_db_path()

st.title("Digital Twin: Local Data Monitor")
st.markdown(
    f"**Reading:** `{os.path.abspath(db_path)}` &nbsp;·&nbsp; "
    "**Network calls made by this process: 0** &nbsp;·&nbsp; 🔒 everything below stays on this machine"
)

placeholder = st.empty()

try:
    txns, logs = load_data(db_path)

    with placeholder.container():
        m1, m2, m3 = st.columns(3)
        m1.metric("Transactions parsed", len(txns))
        m2.metric("Access log entries", len(logs))
        m3.metric("Last refresh", datetime.now().strftime("%H:%M:%S"))

        col1, col2 = st.columns(2)
        with col1:
            st.subheader("Transactions")
            st.dataframe(txns, use_container_width=True, height=520, hide_index=True)
        with col2:
            st.subheader("Access Log: what was read/touched, and when")
            st.dataframe(logs, use_container_width=True, height=520, hide_index=True)

except Exception as e:
    with placeholder.container():
        st.warning(f"Waiting for database at `{db_path}`: {e}")

st.caption(f"Polling every {POLL_SECONDS}s · no data leaves this device")

time.sleep(POLL_SECONDS)
st.rerun()
