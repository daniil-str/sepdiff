from pages import BOX_JUN6, BOX_NOV2, FOOTER_NOV1, MARKUP, MINOR, RELATED, SUBSTANTIVE, old, page

from sepdiff.diffing import block_diff, classify, pair_stats, word_ops
from sepdiff.extract import extract

v1, v2, v3, v4, v5 = (extract(page(**o)) for o in ({}, MARKUP, MINOR, SUBSTANTIVE, RELATED))


def _stats(a, b):
    return pair_stats(block_diff(a.body, b.body), block_diff(a.biblio, b.biblio),
                      block_diff(a.apparatus, b.apparatus))


def test_classification_modern():
    assert classify(v1, v1) == "identical"
    assert v1.text_sha == v2.text_sha
    assert classify(v1, v2) == "markup_only"
    assert classify(v2, v3) == "minor"
    assert classify(v3, v4) == "substantive"
    assert classify(v4, v5) == "minor"     # только +1 Related Entry


def test_dates_from_different_places_alone_are_not_a_revision():
    # подвал — дата любой правки, плашка — существенной; содержимое то же
    foot = extract(old(foot="<I>Content last modified: March 9, 2002</I>"))
    box = extract(old(box="content revised<br><table><tr><td>FEB<br>11<br>2000</td></tr></table>"))
    assert classify(foot, box) == "markup_only"
    # тот же источник даты — смена даты по-прежнему substantive
    foot2 = extract(old(foot="<I>Content last modified: March 9, 2003</I>"))
    assert classify(foot, foot2) == "substantive"


def test_classification_old_layouts():
    o1 = extract(old(foot=FOOTER_NOV1))
    o2 = extract(old(box=BOX_NOV2, vary="differ"))
    o3 = extract(old(box=BOX_JUN6, vary="differ greatly"))
    o4 = extract(old(vary="differ slightly"))
    assert classify(o1, o2) == "minor"         # Nov 1 в подвале = NOV 2 в плашке
    assert classify(o2, o3) == "substantive"
    assert classify(o2, o4) == "changed"       # даты нет — вид правки неизвестен


def test_formula_spacing_is_a_change():
    # SEP считает это minor correction (logic-modal sum2008 -> fall2008)
    a = extract(old(foot=FOOTER_NOV1, vary="∀xA∨∀xB"))
    b = extract(old(foot=FOOTER_NOV1, vary="∀xA ∨ ∀xB"))
    assert classify(a, b) == "minor"


def test_pruned_links_are_not_a_revision():
    dead = extract(page(**SUBSTANTIVE, extra_oir="<p>Dead link.</p>"))
    assert classify(dead, v4) == "markup_only"          # мёртвую ссылку убрали
    added = extract(page(**SUBSTANTIVE, extra_oir="<p>New link.</p>"))
    assert classify(v4, added) == "minor"               # новую добавили
    assert classify(v4, v5) == "minor"                  # Related Entries — всегда minor


def test_related_entry_renamed_by_sep_is_not_a_revision():
    a = extract(page(extra_rel='<p><a href="../frege-logic/">frege-logic</a></p>'))
    b = extract(page(extra_rel='<p><a href="../frege-logic/">Frege, Gottlob: theorem</a></p>'))
    assert classify(a, b) == "markup_only"      # надпись сменил сам SEP, цель ссылки та же
    c = extract(page(extra_rel='<p><a href="../frege-logic/">frege-logic</a> | <a href="../hume/">Hume</a></p>'))
    assert classify(a, c) == "minor"            # добавили Related Entry


def test_one_word_minor():
    ops = [o for o in block_diff(v2.body, v3.body) if o.kind != "equal"]
    assert [o.kind for o in ops] == ["mod"]
    st = _stats(v2, v3)
    assert (st.words_added, st.words_removed, st.sections) == (1, 1, ["1. Life and works"])
    assert [(t, a, b) for t, a, b in word_ops(ops[0].a.words, ops[0].b.words) if t != "equal"] == [
        ("replace", ["lived"], ["spent"])]


def test_substantive_stats():
    assert sorted(o.kind for o in block_diff(v3.body, v4.body) if o.kind != "equal") == ["del", "ins"]
    st = _stats(v3, v4)
    assert (st.biblio_added, st.biblio_removed) == (1, 0)
    st45 = _stats(v4, v5)
    assert (st45.apparatus_changed, st45.blocks_changed) == (1, 0)
