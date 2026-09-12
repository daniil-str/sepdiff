"""Синтетические страницы в форме вёрстки SEP разных эпох (без текстов SEP — копирайт)."""

from __future__ import annotations

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
   <div id="other-internet-resources"><h2>Other Internet Resources</h2><p>Links.</p>{extra_oir}</div>
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
    extra_bib="", extra_rel="", extra_oir="", ack="",
)

# Только вёрстка/типографика: другая навигация, прямые кавычки, &nbsp;. Текст тот же.
MARKUP = dict(nav="NEW SITE DESIGN", q_open='"', q_close='"', ws="&nbsp;  ", dash="-", apos="'")
# Minor correction: одно слово, дата ревизии та же.
MINOR = dict(nav="NEW SITE DESIGN", lived="spent", q_open='"', q_close='"', dash="-", apos="'")
# Substantive: новая дата, абзац добавлен, абзац удалён, +1 источник.
SUBSTANTIVE = dict(MINOR, rev="Wed Jul 31, 2024",
                   extra_para="<p>Kant never travelled more than a hundred miles from home.</p>",
                   deleted_para="", extra_bib="<li>Rohlf, M., 2024, <em>A New Book</em>.</li>")
# Как kant fall2020 -> fall2023: текст статьи тот же, добавлена Related Entry.
RELATED = dict(SUBSTANTIVE, extra_rel="<p>Kant, Immanuel: transcendental idealism</p>")
# Текущая версия на сайте: новая существенная редакция, ещё не попавшая в архив.
LIVE_PENDING = dict(RELATED, rev="Fri Aug 1, 2026")


def page(**over: str) -> bytes:
    return PAGE.format(**{**BASE, **over}).encode("utf-8")


# Вёрстка 2007–2015 (как kant sum2010): всё в #aueditable, разделы — только <h2>.
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

# Вёрстка 1997–2006: статья прямо в <body>, шапка сайта выше <h1>, дата в плашке
# ({box}) или в подвале ({foot}), логические символы — картинки.
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

FOOTER_NOV1 = "<I>Content last modified: November 1, 1997</I>"
BOX_NOV2 = "content revised<br><table><tr><td>NOV<br>2<br>1997</td></tr></table>"
BOX_JUN6 = "last substantive content change<table><tr><td>JUN<br>6<br>2003</td></tr></table>"


def old(box: str = "", foot: str = "", vary: str = "vary") -> bytes:
    return OLD.format(box=box, foot=foot, vary=vary).encode("utf-8")
