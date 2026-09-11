from datetime import date

import pytest

from sepdiff.editions import Edition, parse_index


def test_parse_edition():
    e = Edition.parse("fall2024")
    assert (e.season, e.year, e.released_on, e.label) == ("fall", 2024, date(2024, 9, 21), "Fall 2024")
    assert Edition.parse("win1997").key < Edition.parse("spr1998").key < Edition.parse("sum1998").key


@pytest.mark.parametrize("bad", ["autumn2024", "fall24", "Fall2024", ""])
def test_rejects_non_editions(bad):
    with pytest.raises(ValueError):
        Edition.parse(bad)


def test_parse_index_sorts_by_release():
    html = """<ul><li><a href="sum2026/">Summer 2026 Edition</a></li>
      <li><a href="win1997/">Winter 1997</a></li><li><a href="fall1997/index.html">Fall 1997</a></li>
      <li><a href="../contents.html">Contents</a></li><li><a href="spr2026/">Spring 2026</a></li></ul>"""
    assert [e.slug for e in parse_index(html)] == ["fall1997", "win1997", "spr2026", "sum2026"]
