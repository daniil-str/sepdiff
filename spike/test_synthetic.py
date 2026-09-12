"""Офлайн-проверка конвейера спайка на синтетических SEP-подобных страницах.

Не заменяет прогон на настоящих снимках — вёрстка SEP тут воспроизведена по
памяти, — но ловит ошибки в extract/normalize/diff/classify до похода в сеть.

Запуск:  uv run python spike/test_synthetic.py
"""

from __future__ import annotations

import sys

from analyze import _find_date, block_diff, classify, extract, normalize, pair_stats, render_ops

PAGE = """<!DOCTYPE html><html><head><title>Kant (SEP)</title>
<script>window.MathJax = {{}};</script><style>body{{}}</style></head><body>
<div id="container">
 <div id="header-wrapper"><nav>{nav} Browse About Support SEP</nav></div>
 <div id="article">
  <div id="aueditable">
   <h1>Immanuel Kant</h1>
   <div id="pubinfo"><em>First published Thu May 20, 2010; substantive revision {rev}</em></div>
   <div id="preamble"><p>Immanuel Kant (1724{dash}1804) is the central figure in modern philosophy.</p></div>
   <div id="toc"><ul><li><a href="#Life">1. Life and works</a></li></ul></div>
   <div id="main-text">
    <h2 id="Life">1. Life and works</h2>
    <p>Kant was born in K{o}nigsberg and {lived} there all his life.</p>
    {extra_para}
    <p>{q_open}Sapere aude!{q_close} {ws}is the motto of enlightenment.</p>
    <!-- editorial comment -->
    <h2 id="Project">2. Kant{apos}s project</h2>
    <ul><li>First item.</li><li>Second item<ul><li>Nested item.</li></ul></li></ul>
    <div class="legacy">Bare text inside a div, <em>with emphasis</em>.</div>
    {deleted_para}
   </div>
   <div id="bibliography"><h2>Bibliography</h2>
    <ul><li>Allison, H., 2004, <em>Kant{apos}s Transcendental Idealism</em>.</li>
        <li>Guyer, P., 1987, <em>Kant and the Claims of Knowledge</em>.</li>{extra_bib}</ul></div>
   <div id="academic-tools"><h2>Academic Tools</h2><p>How to cite this entry.</p></div>
   <div id="other-internet-resources"><h2>Other Internet Resources</h2><p>Links.</p></div>
   <div id="related-entries"><h2>Related Entries</h2><p>Hume, David</p>{extra_rel}</div>
   {ack}
  </div>
 </div>
 <div id="article-copyright"><p>Copyright &copy; 2020 by Michael Rohlf</p></div>
</div></body></html>"""

BASE = dict(
    nav="", rev="Tue Jul 28, 2020", dash="–", o="ö", lived="lived",
    q_open="“", q_close="”", ws="", apos="’",
    extra_para="", deleted_para="<p>A paragraph that will be removed later.</p>",
    extra_bib="", extra_rel="", ack="",
)


def page(**over: str) -> bytes:
    return PAGE.format(**{**BASE, **over}).encode("utf-8")


v1 = page()
# Только вёрстка/типографика: другая навигация, прямые кавычки вместо
# типографских, &nbsp; и лишние пробелы. Текст статьи тот же.
v2 = page(nav="NEW SITE DESIGN", q_open='"', q_close='"', ws="&nbsp;  ", dash="-", apos="'")
# Minor correction: одно слово, дата ревизии не менялась.
v3 = page(nav="NEW SITE DESIGN", lived="spent", q_open='"', q_close='"', dash="-", apos="'")
# Substantive: новая дата ревизии, абзац добавлен, абзац удалён, +1 источник.
V4 = dict(nav="NEW SITE DESIGN", lived="spent", q_open='"', q_close='"', dash="-", apos="'",
          rev="Wed Jul 31, 2024",
          extra_para="<p>Kant never travelled more than a hundred miles from home.</p>",
          deleted_para="",
          extra_bib="<li>Rohlf, M., 2024, <em>A New Book</em>.</li>")
v4 = page(**V4)
# Как kant fall2020 -> fall2023: текст статьи тот же, добавлена Related Entry.
v5 = page(**V4, extra_rel="<p>Kant, Immanuel: transcendental idealism</p>")

failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        failures.append(msg)


d1, d2, d3, d4 = (extract(f"v{i}", raw) for i, raw in enumerate((v1, v2, v3, v4), 1))

print("Извлечение:")
check(d1.extractor == "modern", f"экстрактор modern (получили {d1.extractor})")
check(d1.title == "Immanuel Kant", f"заголовок (получили {d1.title!r})")
check(d1.revision_date == "2020-07-28", f"дата ревизии (получили {d1.revision_date!r})")
body = " | ".join(b.text for b in d1.body)
for junk in ("Browse", "Academic Tools", "How to cite", "Related Entries", "Copyright",
             "Hume", "MathJax", "editorial comment", "1. Life and works | 1. Life"):
    check(junk not in body, f"в тексте нет {junk!r}")
for want in ("Second item", "Nested item.", "Bare text inside a div, with emphasis.",
             "Königsberg", "central figure"):
    check(want in body, f"в тексте есть {want!r}")
check(len(d1.biblio) == 2, f"2 источника в библиографии (получили {len(d1.biblio)})")
check(all("Bibliography" != b.text for b in d1.biblio), "заголовок Bibliography не считается источником")
check(0.9 <= d1.coverage <= 1.05, f"покрытие текста ~100% (получили {d1.coverage:.1%})")
sections = [b.section for b in d1.body if b.kind != "heading"]
check(sections[0] == "" and "2. Kant's project" in sections,
      f"разделы проставлены (получили {sorted(set(sections))})")

print("\nКлассификация:")
check(classify(d1, d1) == "identical", "тот же снимок -> identical")
check(classify(d1, d2) == "markup_only", f"v1->v2 markup_only (получили {classify(d1, d2)})")
check(d1.text_sha == d2.text_sha, "нормализация сводит типографику к одному хешу")
check(classify(d2, d3) == "minor", f"v2->v3 minor (получили {classify(d2, d3)})")
check(classify(d3, d4) == "substantive", f"v3->v4 substantive (получили {classify(d3, d4)})")

print("\nСсылки (Other Internet Resources + Related Entries):")
d5 = extract("v5", v5)
app1 = [b.text for b in d1.apparatus]
check(app1 == ["Links.", "Hume, David"], f"apparatus извлечён без заголовков (получили {app1})")
check(not any("cite" in t or "Academic" in t for t in app1), "Academic Tools в apparatus не попадает")
check(d4.text_sha == d5.text_sha, "v4->v5: текст статьи не менялся")
check(classify(d4, d5) == "minor", f"v4->v5 (+1 Related Entry) -> minor (получили {classify(d4, d5)})")
st45 = pair_stats(d4, d5, block_diff(d4.body, d5.body), block_diff(d4.biblio, d5.biblio),
                  block_diff(d4.apparatus, d5.apparatus))
check(st45.apparatus_changed == 1 and st45.blocks_changed == 0,
      f"v4->v5: 1 изменение в ссылках, 0 в тексте (получили {st45.apparatus_changed}, {st45.blocks_changed})")

print("\nDiff:")
ops23 = block_diff(d2.body, d3.body)
changed23 = [o for o in ops23 if o.kind != "equal"]
check(len(changed23) == 1 and changed23[0].kind == "mod",
      f"v2->v3: ровно один изменённый блок (получили {[o.kind for o in changed23]})")
st23 = pair_stats(d2, d3, ops23, block_diff(d2.biblio, d3.biblio))
check((st23.words_added, st23.words_removed) == (1, 1),
      f"v2->v3: +1/−1 слово (получили +{st23.words_added}/−{st23.words_removed})")
check(st23.sections == ["1. Life and works"], f"v2->v3: затронут §1 (получили {st23.sections})")

ops34 = block_diff(d3.body, d4.body)
kinds34 = sorted(o.kind for o in ops34 if o.kind != "equal")
check(kinds34 == ["del", "ins"], f"v3->v4: один ins и один del (получили {kinds34})")
st34 = pair_stats(d3, d4, ops34, block_diff(d3.biblio, d4.biblio))
check(st34.biblio_added == 1 and st34.biblio_removed == 0,
      f"v3->v4: +1 источник (получили +{st34.biblio_added} −{st34.biblio_removed})")

html_out = render_ops(ops23)
check("<del>lived</del>" in html_out and "<ins>spent</ins>" in html_out, "в HTML пословная правка lived→spent")
check("неизменённых блоков" in html_out, "неизменённые блоки свёрнуты")

print("\nСтарая вёрстка (как sum2010: без #main-text и #bibliography):")
LEGACY = b"""<html><body><div id="content">
<div id="archivebanner">This is a file in the archives of the SEP.</div>
<div id="aueditable"><h1>Immanuel Kant</h1>
<div id="pubinfo"><em>First published Thu May 20, 2010</em></div>
<p>Preamble text.</p>
<ul><li><a href="#Life">1. Life</a></li>
    <li><a href="#Proj">2. Project</a><ul><li><a href="#Sub">2.1 Sub</a></li></ul></li>
    <li><a href="#Bib">Bibliography</a></li></ul>
<h2><a name="Life">1. Life</a></h2><p>Life text.</p>
<ul><li>A real list item with <a href="#Life">an internal link</a>.</li><li>Second real item.</li><li>Third.</li></ul>
<h2><a name="Bib">Bibliography</a></h2><h3>Primary Literature</h3>
<ul><li>Kant, I., 1781, Critique.</li><li>Kant, I., 1788, Second Critique.</li></ul>
<h2><a name="Oth">Other Internet Resources</a></h2><ul><li><a href="http://x.org">Some link</a></li></ul>
<h2><a name="Rel">Related Entries</a></h2><p>Hume, David</p>
</div></div><div id="foot">Copyright 2010</div></body></html>"""
dl = extract("legacy", LEGACY)
lbody = " | ".join(b.text for b in dl.body)
check(dl.extractor == "aueditable", f"экстрактор aueditable (получили {dl.extractor})")
check(dl.revision_date == "2010-05-20",
      f"без substantive revision берём дату первой публикации (получили {dl.revision_date!r})")
check(not any(b.text == "Immanuel Kant" for b in dl.body), "h1 статьи не попадает в тело")
check("2.1 Sub" not in lbody, "оглавление (список из якорных ссылок) выброшено")
check("A real list item with an internal link." in lbody, "обычный список с внутренней ссылкой сохранён")
check([b.text for b in dl.biblio] == ["Kant, I., 1781, Critique.", "Kant, I., 1788, Second Critique."],
      f"библиография отделена по заголовку, без подзаголовков (получили {[b.text for b in dl.biblio]})")
for junk in ("Primary Literature", "Some link", "Hume", "Other Internet Resources",
             "archives of the SEP", "Copyright"):
    check(junk not in lbody, f"в теле нет {junk!r}")
lapp = [b.text for b in dl.apparatus]
check(lapp == ["Some link", "Hume, David"], f"старая вёрстка: apparatus по заголовкам (получили {lapp})")

print("\nБлагодарности (#acknowledgments вне #main-text):")
d6 = extract("v6", page(**V4, ack='<div id="acknowledgments"><h3>Acknowledgments</h3>'
                                   "<p>Thanks to a reader.</p></div>"))
check("Thanks to a reader." in [b.text for b in d6.body], "благодарности входят в текст статьи")
check(classify(d4, d6) == "minor", f"v4->v6 (+благодарность, дата та же) -> minor (получили {classify(d4, d6)})")

print("\nДаты по эпохам вёрстки:")
for text, want in [
    ("First published Thu May 20, 2010; substantive revision Tue Jul 28, 2020", "2020-07-28"),
    ("First published Thu May 20, 2010", "2010-05-20"),
    ("last substantive content change JUN 6 2003 Qualia", "2003-06-06"),
    ("content revised NOV 2 1997", "1997-11-02"),
    ("First published: August 20, 1997 Content last modified: September 8, 1997", "1997-09-08"),
]:
    got = _find_date(text)[0]
    check(got == want, f"{text[:45]!r}... -> {want} (получили {got})")
check(normalize("the term `qualia'") == "the term 'qualia'", "обратная кавычка 1997 года = апостроф")

print("\nВёрстка 1997-2006 (статья прямо в <body>, дата в плашке или подвале):")
OLD = """<html><body>
<table width="100%"><td>This is a file in the archives of the <a href="../../../../index.html">SEP</a>.</td></table>
<table><tr><td>how to cite<br>this entry <a href="x">CITATION<br>INFO</a></td>
<td><center><h4>Stanford Encyclopedia of Philosophy</h4></center>
<center><a href="../../contents.html#a">A</a> | <a href="../../contents.html#b">B</a> |
 <a href="../../contents.html#z">Z</a></center></td>
<td>{box}</td></tr></table>
<hr><H1>Qualia</H1>
Feelings and experiences {vary} widely.
<P>The entry is divided into sections.
<h2><a name="Uses">I. Other Uses of the Term `Qualia'</a></h2>
<p>Consider a painting: <IMG SRC="Box.gif">(A<img src="ra.gif">B), see <img src="fig1.gif">.</p>
<h2><a name="Bib">Bibliography</a></h2><ul><li>Tye, M., 1995, Ten Problems.</li></ul>
<h2><a name="Rel">Related Entries</a></h2>consciousness
<h3>Acknowledgments</h3><p>Thanks to Pat Hayes.</p>
<P><center><A HREF="../../info.html#c">Copyright &#169; 1997</A> by<br>Michael Tye</center><hr>
<center><a href="../../contents.html#a">A</a> | <a href="../../contents.html#z">Z</a></center>
<P><I>First published: August 20, 1997</I><br>{foot}
</body></html>"""


def old(box: str = "", foot: str = "", vary: str = "vary") -> bytes:
    return OLD.format(box=box, foot=foot, vary=vary).encode("utf-8")


o1 = extract("o1", old(foot="<I>Content last modified: November 1, 1997</I>"))
o2 = extract("o2", old(box="content revised<br><table><tr><td>NOV<br>2<br>1997</td></tr></table>",
                       vary="differ"))
o3 = extract("o3", old(box="last substantive content change<table><tr><td>JUN<br>6<br>2003</td></tr></table>",
                       vary="differ greatly"))
o4 = extract("o4", old(vary="differ slightly"))
obody = " | ".join(b.text for b in o1.body)
check(o1.extractor == "fallback-body", f"экстрактор fallback-body (получили {o1.extractor})")
check([o.revision_date for o in (o1, o2, o3, o4)] == ["1997-11-01", "1997-11-02", "2003-06-06", None],
      f"даты из подвала/плашек (получили {[o.revision_date for o in (o1, o2, o3, o4)]})")
for junk in ("A | B", "CITATION", "Copyright", "archives of the", "First published",
             "Stanford Encyclopedia", "Qualia |"):
    check(junk not in obody, f"в теле нет {junk!r}")
for want in ("Feelings and experiences vary widely.", "I. Other Uses of the Term 'Qualia'",
             "Thanks to Pat Hayes.", "Consider a painting: □(A→B), see [img:fig1]."):
    check(want in obody, f"в теле есть {want!r}")
check([b.text for b in o1.biblio] == ["Tye, M., 1995, Ten Problems."],
      f"библиография (получили {[b.text for b in o1.biblio]})")
check([b.text for b in o1.apparatus] == ["consciousness"],
      f"Related Entries (получили {[b.text for b in o1.apparatus]})")
check(classify(o1, o2) == "minor",
      f"Nov 1 (подвал) vs NOV 2 (плашка) — одна и та же дата -> minor (получили {classify(o1, o2)})")
check(classify(o2, o3) == "substantive", f"новая дата -> substantive (получили {classify(o2, o3)})")
check(classify(o2, o4) == "changed", f"без даты вид правки неизвестен (получили {classify(o2, o4)})")

print(f"\n{'ВСЁ ЗЕЛЁНОЕ' if not failures else f'ПРОВАЛОВ: {len(failures)}'}")
sys.exit(1 if failures else 0)
