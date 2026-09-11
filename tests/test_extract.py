import pytest
from pages import BOX_JUN6, BOX_NOV2, FOOTER_NOV1, LEGACY, RELATED, SUBSTANTIVE, old, page

from sepdiff.extract import extract, find_date
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


def test_normalize_typography():
    assert normalize("the term `qualia'") == "the term 'qualia'"
    assert normalize("“Sapere aude!”   is the – motto") == '"Sapere aude!" is the - motto'
    assert normalize("zero​width") == "zerowidth"
