from dataclasses import replace

import pytest
from pages import (
    BOX_JUN6,
    BOX_NOV2,
    FOOTER_NOV1,
    LEGACY,
    RELATED,
    SUBSTANTIVE,
    old,
    page,
    retired,
    retired_no_successor,
)

from sepdiff.diffing import classify
from sepdiff.extract import extract, find_date, parse_retirement
from sepdiff.normalize import normalize


def _text(blocks) -> str:
    return " | ".join(b.text for b in blocks)


def test_modern_layout():
    d = extract(page())
    assert d.extractor == "modern"
    assert d.title == "Immanuel Kant"
    assert (d.revision_date, d.date_source) == ("2020-07-28", "pubinfo")
    body = _text(d.body)
    for junk in ("Browse", "Academic Tools", "How to cite", "Related Entries", "Copyright",
                 "Hume", "MathJax", "editorial comment", "1. Life and works | 1. Life"):
        assert junk not in body
    for want in ("Second item", "Nested item.", "Bare text inside a div, with emphasis.",
                 "Königsberg", "central figure"):
        assert want in body
    assert len(d.biblio) == 2
    assert 0.9 <= d.coverage <= 1.05
    sections = [b.section for b in d.body if b.kind != "heading"]
    assert sections[0] == "" and "2. Kant's project" in sections


def test_apparatus_is_separate_stream():
    d = extract(page())
    assert [b.text for b in d.apparatus] == ["Links.", "Hume, David"]
    d4, d5 = extract(page(**SUBSTANTIVE)), extract(page(**RELATED))
    assert d4.text_sha == d5.text_sha
    assert d4.apparatus_sha != d5.apparatus_sha


def test_acknowledgments_count_as_body():
    d = extract(page(ack='<div id="acknowledgments"><h3>Acknowledgments</h3><p>Thanks to a reader.</p></div>'))
    assert "Thanks to a reader." in [b.text for b in d.body]


def test_layout_2010():
    d = extract(LEGACY)
    body = _text(d.body)
    assert d.extractor == "aueditable"
    assert (d.revision_date, d.date_source) == ("2010-05-20", "pubinfo-first")
    assert not any(b.text == "Immanuel Kant" for b in d.body)
    assert "2.1 Sub" not in body   # оглавление из якорных ссылок выброшено
    assert "A real list item with an internal link." in body
    assert [b.text for b in d.biblio] == ["Kant, I., 1781, Critique.", "Kant, I., 1788, Second Critique."]
    assert [b.text for b in d.apparatus] == ["Some link", "Hume, David"]
    for junk in ("Primary Literature", "Some link", "Hume", "archives of the SEP", "Copyright"):
        assert junk not in body


def test_layout_1997():
    d = extract(old(foot=FOOTER_NOV1))
    body = _text(d.body)
    assert d.extractor == "fallback-body"
    assert (d.revision_date, d.date_source) == ("1997-11-01", "footer")
    for junk in ("A | B", "CITATION", "Copyright", "archives of the", "First published",
                 "Stanford Encyclopedia", "Qualia |"):
        assert junk not in body
    for want in ("Feelings and experiences vary widely.", "I. Other Uses of the Term 'Qualia'",
                 "Thanks to Pat Hayes.", "Consider a painting: □(A→B), see [img:fig1]."):
        assert want in body
    assert [b.text for b in d.biblio] == ["Tye, M., 1995, Ten Problems."]
    assert [b.text for b in d.apparatus] == ["consciousness"]


def test_footer_glued_to_last_block():
    raw = b"""<html><body><h1>T</h1><p>Body text.</p>
    <h2>Related Entries</h2>consciousness | qualia Copyright &#169; 1995, 2002 by Someone
    <a href="../../contents.html#a">A</a> | Z
    <p><img src="../../symbols/contents.gif">Table of Contents</p></body></html>"""
    d = extract(raw)
    assert [b.text for b in d.body] == ["Body text."]
    assert [b.text for b in d.apparatus] == ["consciousness | qualia"]


@pytest.mark.parametrize(("raw", "want"), [
    (old(box=BOX_NOV2), ("1997-11-02", "header-revised")),
    (old(box=BOX_JUN6), ("2003-06-06", "header-substantive")),
    (old(), (None, None)),
])
def test_old_date_badges(raw, want):
    d = extract(raw)
    assert (d.revision_date, d.date_source) == want


@pytest.mark.parametrize(("text", "want"), [
    ("First published Thu May 20, 2010; substantive revision Tue Jul 28, 2020", "2020-07-28"),
    ("First published Thu May 20, 2010", "2010-05-20"),
    ("last substantive content change JUN 6 2003 Qualia", "2003-06-06"),
    ("content revised NOV 2 1997", "1997-11-02"),
    ("First published: August 20, 1997 Content last modified: September 8, 1997", "1997-09-08"),
    ("no dates here", None),
])
def test_find_date(text, want):
    assert find_date(text)[0] == want


def test_image_only_blocks_are_not_text():
    d = extract(page(extra_para='<p><img src="portrait.jpg"></p>'))
    assert not any("[img:" in b.text for b in d.body)
    assert d.text_sha == extract(page()).text_sha


def test_apparatus_links_count():
    a = extract(page(extra_oir='<p><a href="http://example.org/frege.html">Web page</a></p>'))
    b = extract(page(extra_oir='<p><a href="https://example.org/frege.html">Web page</a></p>'))
    assert a.text_sha == b.text_sha and a.apparatus_sha == b.apparatus_sha
    assert a.links_sha != b.links_sha
    assert classify(a, b) == "minor"                 # поменялся только адрес — всё равно правка
    c = extract(page(extra_rel='<p><a href="../../archives/fall2024/entries/hume/">Hume</a></p>'))
    d = extract(page(extra_rel='<p><a href="../../archives/win2024/entries/hume/">Hume</a></p>'))
    assert c.links_sha == d.links_sha                # издание в адресе — не правка
    # вёрстка до 2016 адресов не выделяет: их появление на смене вёрстки — не правка
    assert classify(replace(a, oir_links=[], related_links=[], raw_sha="old-layout"), a) == "markup_only"


def test_supplementary_documents():
    base = page()
    with_supp = base.replace(
        b'<div id="toc"><ul>', b'<div id="toc"><ul><li><a href="supplement.html">Supplement</a></li>'
    )
    a, b = extract(base), extract(with_supp)
    assert b.supplements == ["supplement.html"] and a.supplements == []
    assert a.text_sha == b.text_sha and classify(a, b) == "minor"   # новый доп. документ — правка
    extra_para = '<p>See <a href="notes.html#note-1">note 1</a> and <a href="../hume/">Hume</a>.</p>'
    notes = extract(page(extra_para=extra_para))
    assert notes.supplements == ["notes.html"]


def test_normalize_typography():
    assert normalize("A1,…,An") == "A1,...,An"
    assert normalize("the term `qualia'") == "the term 'qualia'"
    assert normalize("“Sapere aude!”   is the – motto") == '"Sapere aude!" is the - motto'
    assert normalize("zero​width") == "zerowidth"


def test_parse_retirement():
    # T5: SEP не редиректит снятую/переименованную статью — вместо текста
    # отдаёт «Document Retired» (docs/journal.md §22), это не обычная статья.
    r = parse_retirement(retired(successor="new-entry", last_edition="sum2018"))
    assert r is not None
    assert (r.successor, r.last_edition) == ("new-entry", "sum2018")

    r2 = parse_retirement(retired_no_successor(last_edition="win2020"))
    assert r2 is not None
    assert (r2.successor, r2.last_edition) == (None, "win2020")

    assert parse_retirement(page()) is None   # обычная статья — не «Document Retired»
