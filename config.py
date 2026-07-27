#!/usr/bin/env python3
"""
config.py — central configuration for the whole project. Every other
module imports from here instead of hardcoding these values, so a
change in one place (e.g. bumping the bot version, moving the DB) is
guaranteed to apply everywhere rather than needing to be hunted down
across files.
"""

# --- Identity ---
USER_AGENT = "Urdu-Mehfil-Archive-Bot/2.0 (Python 3.10+)"

# --- Site ---
BASE_URL = "https://www.urduweb.org/mehfil/"

# --- Storage ---
DB_PATH = "mehfil.db"
ARCHIVE_OUT_DIR = "archive"

# --- Networking defaults ---
DEFAULT_MAX_WORKERS = 1          # opt-in concurrency; 1 = old sequential behavior
REQUEST_TIMEOUT = 30
REQUEST_DELAY_MIN = 1.5          # polite per-request delay range (seconds)
REQUEST_DELAY_MAX = 2.5
RETRY_ATTEMPTS = 3
RETRY_BACKOFF_BASE = 5           # seconds; multiplied by attempt number
DEFAULT_BACKOFF_SECONDS = 30     # used when a 429/503 has no Retry-After header

# --- Fill-page-counts checkpointing (now less critical with SQLite, kept
#     for progress-reporting cadence rather than write-cost reasons) ---
DEFAULT_SAVE_EVERY = 20

# --- Logging ---
LOG_FILE = "archive.log"

# --- Retry (HTTPAdapter + urllib3.util.Retry) ---
# Transport-level failures (connection resets, 500/502/504) are retried
# automatically by the session's mounted adapter, with exponential
# backoff. 429/503 are deliberately NOT in this list — they're handled
# manually in common.py because they need to pause every worker thread,
# not just retry the one request that got throttled.
RETRYABLE_STATUS = [500, 502, 504]
RETRY_BACKOFF_FACTOR = 1  # urllib3: 1s, 2s, 4s between transport-level retries
