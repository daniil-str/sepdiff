"""Офлайн-проверка конвейера спайка на синтетических SEP-подобных страницах.

Не заменяет прогон на настоящих снимках — вёрстка SEP тут воспроизведена по
памяти, — но ловит ошибки в extract/normalize/diff/classify до похода в сеть.

Запуск:  uv run python spike/test_synthetic.py
"""

from __future__ import annotations

import sys

from analyze import block_diff, classify, extract, pair_stats, render_ops

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
  </div>
 </div>
 <div id="article-copyright"><p>Copyright &copy; 2020 by Michael Rohlf</p></div>
</div></body></html>"""

BASE = dict(
    nav="", rev="Tue Jul 28, 2020", dash="–", o="ö", lived="lived",
    q_open="“", q_close="”", ws="", apos="’",
    extra_para="", deleted_para="<p>A paragraph that will be removed later.</p>",
    extra_bib="", extra_rel="",
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
check(d1.revision_date == "Tue Jul 28, 2020", f"дата ревизии (получили {d1.revision_date!r})")
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
check((st23.words_added, st23.words_removed) == (1, 1), f"v2->v3: +1/−1 слово (получили +{st23.words_added}/−{st23.words_removed})")
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
check(dl.revision_date == "Thu May 20, 2010",
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

print(f"\n{'ВСЁ ЗЕЛЁНОЕ' if not failures else f'ПРОВАЛОВ: {len(failures)}'}")
sys.exit(1 if failures else 0)
