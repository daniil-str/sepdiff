"""Регрессия на настоящих снимках SEP против эталона archinfo.

Снимки в репозиторий не кладём (копирайт), поэтому тест берёт их из локального
кеша data/spike/ (spike/fetch.py) и пропускается, если его нет.
"""

from functools import cache
from pathlib import Path

import pytest
from archinfo import ARCHINFO, KNOWN_GAPS, expected

from sepdiff.diffing import classify
from sepdiff.editions import EDITION_RE, Edition
from sepdiff.extract import Doc, extract

CACHE = Path(__file__).resolve().parents[1] / "data" / "spike"


def _editions(entry: str) -> list[str]:
    if not CACHE.exists():
        return []
    eds = [p.name[len(entry) + 1:-len(".html")] for p in CACHE.glob(f"{entry}.*.html")]
    return sorted((e for e in eds if EDITION_RE.fullmatch(e)), key=lambda e: Edition.parse(e).key)


PAIRS = [(entry, a, b) for entry in ARCHINFO for a, b in zip(_editions(entry), _editions(entry)[1:])]


@cache
def _doc(entry: str, edition: str) -> Doc:
    return extract((CACHE / f"{entry}.{edition}.html").read_bytes())


@pytest.mark.skipif(not PAIRS, reason="нет локального кеша снимков data/spike/")
@pytest.mark.parametrize(("entry", "a", "b"), PAIRS or [("-", "-", "-")])
def test_matches_archinfo(entry, a, b):
    if (entry, a, b) in KNOWN_GAPS:
        pytest.xfail("archinfo не отмечает правку, которая в тексте есть")
    got = classify(_doc(entry, a), _doc(entry, b))
    got = "unchanged" if got in ("identical", "markup_only") else got
    assert got == expected(entry, a, b)
