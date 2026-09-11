"""Квартальные издания SEP: slug вида 'fall2024', выходят 21 марта/июня/сентября/декабря."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from selectolax.lexbor import LexborHTMLParser

SEASONS = ("spr", "sum", "fall", "win")
_MONTH = {"spr": 3, "sum": 6, "fall": 9, "win": 12}
_LABEL = {"spr": "Spring", "sum": "Summer", "fall": "Fall", "win": "Winter"}
EDITION_RE = re.compile(r"(spr|sum|fall|win)(\d{4})")


@dataclass(frozen=True)
class Edition:
    slug: str
    season: str
    year: int

    @classmethod
    def parse(cls, slug: str) -> Edition:
        m = EDITION_RE.fullmatch(slug)
        if m is None:
            raise ValueError(f"не издание SEP: {slug!r} (ожидается вида fall2024)")
        return cls(slug, m.group(1), int(m.group(2)))

    @property
    def key(self) -> tuple[int, int]:
        return self.year, SEASONS.index(self.season)

    @property
    def released_on(self) -> date:
        return date(self.year, _MONTH[self.season], 21)

    @property
    def label(self) -> str:
        return f"{_LABEL[self.season]} {self.year}"


def parse_index(html: str) -> list[Edition]:
    """Список изданий со страницы /archives/, по порядку выхода."""
    found: set[str] = set()
    for a in LexborHTMLParser(html).css("a[href]"):
        m = re.search(r"(?:^|/)((?:spr|sum|fall|win)\d{4})/?(?:index\.html)?$", a.attributes.get("href") or "")
        if m:
            found.add(m.group(1))
    return sorted((Edition.parse(s) for s in found), key=lambda e: e.key)
