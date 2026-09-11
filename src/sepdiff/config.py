"""Настройки: где лежат данные, адрес SEP, правила вежливости краулера."""

from __future__ import annotations

import os
from pathlib import Path

BASE_URL = "https://plato.stanford.edu"
CRAWL_DELAY = 5.0  # robots.txt SEP: crawl-delay 5 — на весь процесс, не на поток


def data_dir() -> Path:
    """Каталог с базой и снимками: $SEPDIFF_DATA или ./data."""
    env = os.environ.get("SEPDIFF_DATA")
    return Path(env) if env else Path.cwd() / "data"


def user_agent() -> str:
    # Контакт в UA — хороший тон для краулера, но только если владелец сам его задал.
    contact = os.environ.get("SEPDIFF_CONTACT")
    return "SEPDiff/0.1 (personal research tool" + (f"; contact: {contact}" if contact else "") + ")"
