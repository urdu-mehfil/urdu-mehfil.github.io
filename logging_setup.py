#!/usr/bin/env python3
"""
logging_setup.py — one place to configure logging for the whole
project. archive.py calls setup_logging() once at startup; every
other module just does `logger = logging.getLogger(__name__)` and
uses logger.info/.warning/.error instead of print().

Console output stays close to the old print()-based look (just the
message, no timestamp clutter) so nothing feels different day-to-day.
The log file gets full detail — timestamps, level, module name — so a
run you walked away from overnight can be reviewed afterward instead
of only existing as scrollback you didn't see.
"""

import logging

from config import LOG_FILE


def setup_logging(verbose=False):
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    root.handlers.clear()

    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"
    ))
    root.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG if verbose else logging.INFO)
    console_handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(console_handler)
