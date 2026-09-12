"""Регрессия на настоящих снимках SEP против эталона archinfo.

Снимки в репозиторий не кладём (копирайт). Тест берёт их из локальной базы
data/ (sepdiff fetch — полные истории) или из кеша спайка data/spike/ и
пропускается, если нет ни того, ни другого. Базу открывает только на чтение.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
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


def _from_db(entry: str) -> dict[str, tuple[str, str | None]]:
    """edition -> (blob_sha, supplements_sha) для снимков с HTTP 200."""
    if not DB.exists():
        return {}
    conn = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT edition_slug, blob_sha, supplements_sha FROM snapshots "
                            "WHERE entry_slug = ? AND http_status = 200", (entry,)).fetchall()
    except sqlite3.Error:
        return {}
    finally:
        conn.close()
    return {edition: (blob, supp) for edition, blob, supp in rows}


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
    got = SOURCES[entry].get(edition)
    raw = BlobStore(DATA / "blobs").get(got[0]) if got else (SPIKE / f"{entry}.{edition}.html").read_bytes()
    return extract(raw)


def _supplements_sha(entry: str, edition: str) -> str | None:
    got = SOURCES[entry].get(edition)
    return got[1] if got else None


@dataclass
class _Signature:
    """Versioned для classify(): хеши Doc плюс supplements_sha из БД (Doc сам его не знает)."""
    raw_sha: str
    text_sha: str
    apparatus_sha: str
    struct_sha: str
    links_sha: str | None
    supplements_sha: str | None
    revision_date: str | None
    date_source: str | None


def _signature(entry: str, edition: str, d: Doc) -> _Signature:
    return _Signature(d.raw_sha, d.text_sha, d.apparatus_sha, d.struct_sha, d.links_sha,
                      _supplements_sha(entry, edition), d.revision_date, d.date_source)


def _classify(entry: str, a: str, b: str) -> str:
    """Как Library._classify_pair: сперва по хешам (с реальным supplements_sha), доуточнить по Doc."""
    da, db_ = _doc(entry, a), _doc(entry, b)
    sa, sb = _signature(entry, a, da), _signature(entry, b, db_)
    kind = classify(sa, sb)
    if (kind == "minor" and sa.text_sha == sb.text_sha and sa.struct_sha == sb.struct_sha
            and sa.supplements_sha == sb.supplements_sha):
        kind = classify(da, db_)
    return kind


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
    got = _classify(entry, a, b)
    got = "unchanged" if got in ("identical", "markup_only") else got
    want = expected(entry, a, b)
    if (entry, a, b) in ARCHINFO_MISSES:
        # эталон молчит, но правка в тексте есть и просмотрена вручную
        assert (want, got) == ("unchanged", "minor"), ARCHINFO_MISSES[(entry, a, b)]
        return
    assert got == want
