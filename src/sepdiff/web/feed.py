"""Atom-лента правок отслеживаемых статей."""

from __future__ import annotations

from xml.etree.ElementTree import Element, SubElement, tostring

from ..present import KIND_LABEL, stats_line
from ..service import Change

TITLE = "SEPDiff: правки отслеживаемых статей"


def rfc3339(when: str) -> str:
    """Дата издания (2024-09-21) или момент проверки -> время в формате Atom."""
    if len(when) == 10:
        return f"{when}T12:00:00Z"
    return when.replace("+00:00", "Z")


def change_url(base_url: str, change: Change) -> str:
    if change.edition.is_live and change.prev_edition:
        return f"{base_url}/e/{change.slug}/diff?a={change.prev_edition}&b=live"
    return f"{base_url}/e/{change.slug}/diff?b={change.edition.slug}"


def atom(changes: list[Change], base_url: str, updated: str) -> bytes:
    feed = Element("feed", {"xmlns": "http://www.w3.org/2005/Atom"})
    SubElement(feed, "title").text = TITLE
    SubElement(feed, "id").text = f"{base_url}/feed.atom"
    SubElement(feed, "updated").text = updated
    SubElement(feed, "link", {"rel": "self", "href": f"{base_url}/feed.atom"})
    SubElement(feed, "link", {"rel": "alternate", "href": base_url})
    for c in changes:
        entry = SubElement(feed, "entry")
        SubElement(entry, "title").text = f"{c.title}: {c.edition.label} — {KIND_LABEL.get(c.kind, c.kind)}"
        SubElement(entry, "id").text = f"urn:sepdiff:{c.slug}:{c.edition.slug}:{c.when}"
        SubElement(entry, "updated").text = rfc3339(c.when)
        SubElement(entry, "link", {"rel": "alternate", "href": change_url(base_url, c)})
        summary = stats_line(c.kind, c.stats) or KIND_LABEL.get(c.kind, c.kind)
        if c.stats.sections:
            summary += " · " + ", ".join(c.stats.sections[:5])
        SubElement(entry, "summary").text = summary
    return tostring(feed, encoding="utf-8", xml_declaration=True)
