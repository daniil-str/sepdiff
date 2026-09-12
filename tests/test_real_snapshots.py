"""Регрессия на настоящих снимках SEP против эталона archinfo.

Снимки в репозиторий не кладём (копирайт). Тест берёт их из локальной базы
data/ (sepdiff fetch — полные истории) или из кеша спайка data/spike/ и
пропускается, если нет ни того, ни другого. Базу открывает только на чтение.
"""

from __future__ import annotations

import sqlite3
from functools import cache
from pathlib import Path

import pytest
from archinfo import ARCHINFO, ARCHINFO_MISSES, KNOWN_GAPS, KNOWN_MISLABELS, expected

from sepdiff.blobs import BlobStore
from sepdiff.diffing import classify
from sepdiff.editions import EDITION_RE, Edition
from sepdiff.extract import Doc, extract

DATA = Path(__file__).resolve().parents[1] / "data"
SPIKE = DATA / "spike"
DB = DATA / "sepdiff.db"


def _from_db(entry: str) -> dict[str, str]:
    """edition -> blob_sha для снимков с HTTP 200."""
    if not DB.exists():
        return {}
    conn = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT edition_slug, blob_sha FROM snapshots "
                            "WHERE entry_slug = ? AND http_status = 200", (entry,)).fetchall()
    except sqlite3.Error:
        return {}
    finally:
        conn.close()
    return dict(rows)


def _from_spike(entry: str) -> list[str]:
    if not SPIKE.exists():
        return []
    eds = [p.name[len(entry) + 1:-len(".html")] for p in SPIKE.glob(f"{entry}.*.html")]
    return [e for e in eds if EDITION_RE.fullmatch(e)]


SOURCES = {entry: _from_db(entry) for entry in ARCHINFO}


def _editions(entry: str) -> list[str]:
    eds = set(SOURCES[entry]) or set(_from_spike(entry))
    return sorted(eds, key=lambda e: Edition.parse(e).key)


@cache
def _doc(entry: str, edition: str) -> Doc:
    blob = SOURCES[entry].get(edition)
    raw = BlobStore(DATA / "blobs").get(blob) if blob else (SPIKE / f"{entry}.{edition}.html").read_bytes()
    return extract(raw)


PAIRS = [
    (entry, a, b) for entry in ARCHINFO for a, b in zip(_editions(entry), _editions(entry)[1:], strict=False)
]


@pytest.mark.skipif(not PAIRS, reason="нет локальных снимков (data/sepdiff.db или data/spike/)")
@pytest.mark.parametrize(("entry", "a", "b"), PAIRS or [("-", "-", "-")])
def test_matches_archinfo(entry, a, b):
    if (entry, a, b) in KNOWN_GAPS:
        pytest.xfail("правка, которую мы намеренно не видим (см. archinfo.KNOWN_GAPS)")
    if (entry, a, b) in KNOWN_MISLABELS:
        pytest.xfail(KNOWN_MISLABELS[(entry, a, b)])
    got = classify(_doc(entry, a), _doc(entry, b))
    got = "unchanged" if got in ("identical", "markup_only") else got
    want = expected(entry, a, b)
    if (entry, a, b) in ARCHINFO_MISSES:
        # эталон молчит, но правка в тексте есть и просмотрена вручную
        assert (want, got) == ("unchanged", "minor"), ARCHINFO_MISSES[(entry, a, b)]
        return
    assert got == want
