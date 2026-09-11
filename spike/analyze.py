"""Спайк, шаг 2: извлечь текст из закешированных снимков и сравнить соседние версии.

Работает полностью офлайн по data/spike/ — сеть не трогает, поэтому
по экстрактору можно итерировать сколько угодно.

Что проверяем этим спайком:
  1. Отсекается ли боилерплейт (навигация, Academic Tools, Related Entries...).
  2. Даёт ли нормализация ОДИНАКОВЫЙ хеш для изданий без изменений
     (контрольная пара fall2023 -> win2023).
  3. Совпадает ли наша классификация substantive/minor с archinfo.cgi.
     Гипотеза: substantive <=> сменилась дата "substantive revision" в #pubinfo.
  4. Читаем ли diff глазами (data/spike/report.html).

Запуск:  uv run python spike/analyze.py
"""

from __future__ import annotations

import hashlib
import html
import re
import time
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from selectolax.lexbor import LexborHTMLParser, LexborNode

from fetch import CACHE, EDITIONS, ENTRY

# --------------------------------------------------------------------------
# Эталон: снят вручную с archinfo.cgi?entry=kant (автоматически туда нельзя —
# /cgi-bin/ закрыт в robots.txt). "unchanged" = identical или markup_only.
# --------------------------------------------------------------------------
EXPECTED = {
    ("sum2010", "spr2016"): "substantive",   # между ними ещё fall2010, sum2014 (minor)
    ("spr2016", "fall2020"): "substantive",  # между ними sum2018, spr2020 (minor)
    ("fall2020", "fall2023"): "minor",
    ("fall2023", "win2023"): "unchanged",    # контрольная пара
    ("win2023", "fall2024"): "substantive",
}

HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
LEAF_BLOCKS = {"p", "li", "blockquote", "dd", "dt", "pre", "td", "th", "figcaption"} | HEADINGS
# Теги, наличие которых внутри узла означает «это контейнер, спускайся глубже».
STRUCT_TAGS = LEAF_BLOCKS | {"div", "table", "ul", "ol", "dl", "section"}
# Блоки внутри статьи, которые к тексту статьи не относятся.
JUNK_IDS = ["toc", "academic-tools", "other-internet-resources", "related-entries",
            "article-copyright", "pubinfo"]
# В старой вёрстке (напр. sum2010) у этих разделов нет своих div — только <h2>
# внутри #aueditable. Режем поток блоков по заголовкам.
BIBLIO_HEADINGS = {"bibliography"}
# Разделы со ссылками: в текст статьи не входят, но правки в них SEP считает
# "minor correction" (у kant fall2020 -> fall2023 изменились только они).
# Храним отдельным потоком со своим хешем. Сравниваем только видимый текст:
# голая починка URL без смены текста ссылки пока не ловится.
APPARATUS_HEADINGS = {"other internet resources", "related entries"}
APPARATUS_IDS = ["other-internet-resources", "related-entries"]
DROP_HEADINGS = {"academic tools"}  # генерирует сайт, а не автор

_PUNCT = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"',
    "–": "-", "—": "-", "−": "-",
    " ": " ", " ": " ", " ": " ", "​": "",
})
_DATE = r"\w{3}\s+\w{3}\s+\d{1,2},\s+\d{4}"   # "Tue Jul 28, 2020"


def normalize(s: str) -> str:
    s = unicodedata.normalize("NFC", s).translate(_PUNCT)
    return re.sub(r"\s+", " ", s).strip()


def sha(data: str | bytes) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------
# Извлечение
# --------------------------------------------------------------------------

@dataclass
class Block:
    kind: str       # heading | p | li | ...
    section: str    # ближайший вышестоящий заголовок
    text: str       # нормализованный текст

    @property
    def words(self) -> list[str]:
        return self.text.split()


@dataclass
class Doc:
    edition: str
    extractor: str
    encoding: str
    title: str
    pubinfo: str
    revision_date: str | None
    body: list[Block]
    biblio: list[Block]
    apparatus: list[Block]   # Other Internet Resources + Related Entries
    raw_sha: str
    coverage: float
    div_ids: list[str]

    @property
    def body_sha(self) -> str:
        return sha("\n".join(b.text for b in self.body))

    @property
    def biblio_sha(self) -> str:
        return sha("\n".join(b.text for b in self.biblio))

    @property
    def apparatus_sha(self) -> str:
        return sha("\n".join(b.text for b in self.apparatus))

    @property
    def text_sha(self) -> str:
        return sha(self.body_sha + self.biblio_sha)

    @property
    def word_count(self) -> int:
        return sum(len(b.words) for b in self.body)


def _has_block_descendant(node: LexborNode) -> bool:
    for child in node.iter(include_text=False):
        if child.tag in STRUCT_TAGS or _has_block_descendant(child):
            return True
    return False


class _Walker:
    """Обходит поддерево и собирает блоки текста, запоминая текущий раздел.

    Инлайновое содержимое (текстовые узлы, <em>, <a>, ...) копится в буфер и
    сбрасывается блоком, когда встречается структурный дочерний узел. Так не
    теряется ни текст у <li>текст<ul>...</ul></li>, ни голый текст внутри
    <div> в вёрстке старых изданий.
    """

    def __init__(self) -> None:
        self.section = ""
        self.out: list[Block] = []

    def walk(self, node: LexborNode, kind: str = "text") -> None:
        buf: list[str] = []

        def flush() -> None:
            text = normalize("".join(buf))
            buf.clear()
            if text:
                self.out.append(Block(kind, self.section, text))

        for child in node.iter(include_text=True):
            tag = child.tag
            if tag == "-text":
                buf.append(child.text_content or "")
            elif tag.startswith(("-", "!", "_")) or tag in ("script", "style", "noscript"):
                continue  # комментарии и прочие служебные узлы
            elif tag in HEADINGS:
                flush()
                text = normalize(child.text(deep=True, separator=""))
                if text:
                    self.section = text
                    self.out.append(Block("heading", text, text))
            elif tag in STRUCT_TAGS or _has_block_descendant(child):
                flush()
                self.walk(child, kind=tag if tag in LEAF_BLOCKS else "text")
            elif tag == "br":
                buf.append(" ")
            else:
                buf.append(child.text(deep=True, separator=""))
        flush()


def _walk(roots: list[LexborNode]) -> list[Block]:
    w = _Walker()
    for r in roots:
        w.walk(r)
    return w.out


def _heading_key(text: str) -> str:
    return re.sub(r"^[\d.\s]+", "", text).strip(" .:").lower()


def _split_by_headings(blocks: list[Block]) -> tuple[list[Block], list[Block], list[Block]]:
    """Поток блоков -> тело | библиография | ссылки. Academic Tools выбрасывается.

    Подзаголовки внутри библиографии и ссылок ("Primary Literature" и т.п.)
    блоками не считаются.
    """
    out: dict[str, list[Block]] = {"body": [], "biblio": [], "apparatus": [], "drop": []}
    mode = "body"
    for b in blocks:
        if b.kind == "heading":
            key = _heading_key(b.text)
            if key in BIBLIO_HEADINGS:
                mode = "biblio"
                continue
            if key in APPARATUS_HEADINGS:
                mode = "apparatus"
                continue
            if key in DROP_HEADINGS:
                mode = "drop"
                continue
            if mode != "body":
                continue
        out[mode].append(b)
    return out["body"], out["biblio"], out["apparatus"]


def _drop_anchor_lists(root: LexborNode) -> None:
    """Выбросить оглавление: список, где каждый пункт — ссылка на якорь внутри страницы."""
    while True:
        for lst in root.css("ul, ol"):
            items = lst.css("li")
            links = lst.css("a")
            if (len(items) >= 3 and len(links) >= len(items)
                    and all((a.attributes.get("href") or "").startswith("#") for a in links)):
                lst.decompose()  # css() отдаёт в порядке документа -> внешний список раньше вложенного
                break
        else:
            return


def _decode(raw: bytes) -> tuple[str, str]:
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace"), "cp1252"


def extract(edition: str, raw: bytes) -> Doc:
    text, encoding = _decode(raw)
    tree = LexborHTMLParser(text)
    for n in tree.css("script, style, noscript"):
        n.decompose()

    div_ids = [n.attributes.get("id") or "" for n in tree.css("div[id]")]

    title_node = tree.css_first("#aueditable h1") or tree.css_first("h1")
    title = normalize(title_node.text(separator=" ")) if title_node else ""

    pub_node = tree.css_first("#pubinfo")
    pubinfo = normalize(pub_node.text(separator=" ")) if pub_node else ""
    if not pubinfo and tree.body is not None:
        m = re.search(r"First published.{0,160}", normalize(tree.body.text(separator=" ")))
        pubinfo = m.group(0) if m else ""
    m = re.search(rf"substantive revision\s+({_DATE})", pubinfo) or \
        re.search(rf"First published\s+({_DATE})", pubinfo)
    revision_date = m.group(1) if m else None

    # Библиографию снимаем первой и вырезаем, чтобы она не попала в тело,
    # если в какой-то эпохе вёрстки она лежит внутри #main-text.
    biblio: list[Block] = []
    biblio_root = tree.css_first("#bibliography")
    if biblio_root is not None:
        biblio = [b for b in _walk([biblio_root]) if b.kind != "heading"]
        biblio_root.decompose()
    apparatus: list[Block] = []
    for aid in APPARATUS_IDS:
        node = tree.css_first(f"#{aid}")
        if node is not None:
            apparatus += [b for b in _walk([node]) if b.kind != "heading"]
            node.decompose()

    main = tree.css_first("#main-text")
    if main is not None:
        extractor = "modern"
        roots = [n for n in (tree.css_first("#preamble"), main) if n is not None]
    else:
        root = tree.css_first("#aueditable") or tree.css_first("#article")
        extractor = "aueditable" if root is not None else "fallback-body"
        root = root or tree.body
        for jid in JUNK_IDS:
            for n in root.css(f"#{jid}"):
                n.decompose()
        for n in root.css("h1"):
            n.decompose()  # заголовок статьи уже взят в title
        _drop_anchor_lists(root)
        roots = [root]

    walked = _walk(roots)
    container_words = sum(len(normalize(r.text(separator=" ")).split()) for r in roots)
    block_words = sum(len(b.words) for b in walked)
    coverage = block_words / container_words if container_words else 0.0
    body, tail_biblio, tail_apparatus = _split_by_headings(walked)
    biblio += tail_biblio
    apparatus += tail_apparatus

    return Doc(edition, extractor, encoding, title, pubinfo, revision_date,
               body, biblio, apparatus, sha(raw), coverage, div_ids)


# --------------------------------------------------------------------------
# Diff
# --------------------------------------------------------------------------

@dataclass
class Op:
    kind: str           # equal | del | ins | mod
    a: Block | None
    b: Block | None

    @property
    def section(self) -> str:
        return (self.b or self.a).section  # type: ignore[union-attr]


SIMILARITY = 0.5         # ниже — считаем «удалён + добавлен», а не «изменён»
ALIGN_BUDGET = 40_000    # предохранитель от квадратичного взрыва на больших переписках
_budget_hits = 0


def _align(a: list[Block], b: list[Block]) -> list[Op]:
    """Как difflib._fancy_replace: находим самую похожую пару, рекурсия слева и справа."""
    global _budget_hits
    if not a:
        return [Op("ins", None, y) for y in b]
    if not b:
        return [Op("del", x, None) for x in a]
    if len(a) * len(b) > ALIGN_BUDGET:
        _budget_hits += 1
        return [Op("del", x, None) for x in a] + [Op("ins", None, y) for y in b]

    best, bi, bj = SIMILARITY, -1, -1
    sm = SequenceMatcher(None, autojunk=False)
    for j, y in enumerate(b):
        sm.set_seq2(y.words)
        for i, x in enumerate(a):
            sm.set_seq1(x.words)
            if sm.real_quick_ratio() > best and sm.quick_ratio() > best:
                r = sm.ratio()
                if r > best:
                    best, bi, bj = r, i, j
    if bi < 0:
        return [Op("del", x, None) for x in a] + [Op("ins", None, y) for y in b]
    return _align(a[:bi], b[:bj]) + [Op("mod", a[bi], b[bj])] + _align(a[bi + 1:], b[bj + 1:])


def block_diff(a: list[Block], b: list[Block]) -> list[Op]:
    sm = SequenceMatcher(None, [x.text for x in a], [y.text for y in b], autojunk=False)
    ops: list[Op] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            ops += [Op("equal", a[i], b[j]) for i, j in zip(range(i1, i2), range(j1, j2))]
        elif tag == "delete":
            ops += [Op("del", a[i], None) for i in range(i1, i2)]
        elif tag == "insert":
            ops += [Op("ins", None, b[j]) for j in range(j1, j2)]
        else:
            ops += _align(a[i1:i2], b[j1:j2])
    return ops


def word_diff(a: list[str], b: list[str]) -> tuple[str, int, int]:
    """HTML с <ins>/<del> по словам + (добавлено, удалено) слов."""
    sm = SequenceMatcher(None, a, b, autojunk=False)
    parts: list[str] = []
    added = removed = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            parts.append(html.escape(" ".join(a[i1:i2])))
            continue
        if tag in ("delete", "replace"):
            parts.append(f"<del>{html.escape(' '.join(a[i1:i2]))}</del>")
            removed += i2 - i1
        if tag in ("insert", "replace"):
            parts.append(f"<ins>{html.escape(' '.join(b[j1:j2]))}</ins>")
            added += j2 - j1
    return " ".join(parts), added, removed


@dataclass
class PairStats:
    kind: str
    words_added: int
    words_removed: int
    blocks_changed: int
    sections: list[str]
    biblio_added: int
    biblio_removed: int
    biblio_modified: int
    apparatus_changed: int


def classify(a: Doc, b: Doc) -> str:
    if a.raw_sha == b.raw_sha:
        return "identical"
    if a.text_sha == b.text_sha:
        return "minor" if a.apparatus_sha != b.apparatus_sha else "markup_only"
    return "substantive" if a.revision_date != b.revision_date else "minor"


def pair_stats(a: Doc, b: Doc, body_ops: list[Op], biblio_ops: list[Op],
               apparatus_ops: list[Op] | None = None) -> PairStats:
    added = removed = changed = 0
    sections: list[str] = []
    for op in body_ops:
        if op.kind == "equal":
            continue
        changed += 1
        if op.section and op.section not in sections:
            sections.append(op.section)
        if op.kind == "ins":
            added += len(op.b.words)  # type: ignore[union-attr]
        elif op.kind == "del":
            removed += len(op.a.words)  # type: ignore[union-attr]
        else:
            _, ad, rm = word_diff(op.a.words, op.b.words)  # type: ignore[union-attr]
            added += ad
            removed += rm
    count = lambda k: sum(1 for o in biblio_ops if o.kind == k)  # noqa: E731
    return PairStats(classify(a, b), added, removed, changed, sections,
                     count("ins"), count("del"), count("mod"),
                     sum(1 for o in (apparatus_ops or []) if o.kind != "equal"))


# --------------------------------------------------------------------------
# Отчёт
# --------------------------------------------------------------------------

CSS = """
:root { --bg:#fbfaf7; --fg:#1d1d1f; --mute:#6b6b70; --line:#e4e2dc;
        --ins:#d7f5dd; --del:#ffdcd8; --insfg:#0b5d1e; --delfg:#8e1b10; --chip:#eeece6; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#17171a; --fg:#e8e6e1; --mute:#9a988f; --line:#2d2d31;
          --ins:#123b1e; --del:#4a1814; --insfg:#9be3ad; --delfg:#ffb4a8; --chip:#26262a; } }
body { background:var(--bg); color:var(--fg); font:16px/1.55 Georgia, "Times New Roman", serif;
       max-width:62rem; margin:2rem auto; padding:0 1.25rem; }
h1,h2,h3,.meta,table,.chip,.skip,.sum { font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }
table { border-collapse:collapse; font-size:13px; width:100%; margin:1rem 0 2rem; }
th,td { text-align:left; padding:.35rem .6rem; border-bottom:1px solid var(--line); vertical-align:top; }
code { font-size:12px; color:var(--mute); }
.pass { color:var(--insfg); font-weight:600; } .fail { color:var(--delfg); font-weight:600; }
section.pair { border-top:2px solid var(--line); margin-top:3rem; padding-top:1rem; }
.sum { font-size:14px; color:var(--mute); margin-bottom:1rem; }
.chip { display:inline-block; background:var(--chip); border-radius:4px; padding:0 .45rem;
        margin:.1rem .25rem .1rem 0; font-size:12px; }
.blk { padding:.35rem .75rem; margin:.25rem 0; border-left:3px solid transparent; }
.blk.ctx { color:var(--mute); }
.blk.ins { background:var(--ins); border-color:var(--insfg); }
.blk.del { background:var(--del); border-color:var(--delfg); text-decoration:line-through; }
.blk.mod { border-color:var(--mute); }
.blk.heading { font-family: system-ui, sans-serif; font-weight:700; }
ins { background:var(--ins); color:var(--insfg); text-decoration:none; }
del { background:var(--del); color:var(--delfg); }
.skip { color:var(--mute); font-size:12px; text-align:center; margin:.6rem 0; }
.secmark { font-size:12px; color:var(--mute); margin:1rem 0 .2rem; font-family:system-ui,sans-serif; }
details { margin-top:1.25rem; } summary { cursor:pointer; font-family:system-ui,sans-serif; font-size:14px; }
"""


def _render_block(op: Op) -> str:
    blk = op.b or op.a
    extra = " heading" if blk.kind == "heading" else ""  # type: ignore[union-attr]
    if op.kind == "mod":
        body, _, _ = word_diff(op.a.words, op.b.words)  # type: ignore[union-attr]
        return f'<div class="blk mod{extra}">{body}</div>'
    cls = {"equal": "ctx", "ins": "ins", "del": "del"}[op.kind]
    return f'<div class="blk {cls}{extra}">{html.escape(blk.text)}</div>'  # type: ignore[union-attr]


def render_ops(ops: list[Op], context: int = 1) -> str:
    show: set[int] = set()
    for i, op in enumerate(ops):
        if op.kind != "equal":
            show.update(range(i - context, i + context + 1))
    out: list[str] = []
    skipped = 0
    last_section = None
    for i, op in enumerate(ops):
        if op.kind == "equal" and i not in show:
            skipped += 1
            continue
        if skipped:
            out.append(f'<div class="skip">… {skipped} неизменённых блоков …</div>')
            skipped = 0
        if op.section != last_section and (op.b or op.a).kind != "heading":  # type: ignore[union-attr]
            out.append(f'<div class="secmark">§ {html.escape(op.section or "(преамбула)")}</div>')
        last_section = op.section
        out.append(_render_block(op))
    if skipped:
        out.append(f'<div class="skip">… {skipped} неизменённых блоков …</div>')
    return "\n".join(out) or '<div class="skip">изменений нет</div>'


def _verdict(pair: tuple[str, str], kind: str) -> tuple[str, str]:
    exp = EXPECTED.get(pair)
    if exp is None:
        return "—", ""
    got = "unchanged" if kind in ("identical", "markup_only") else kind
    return exp, ("PASS" if got == exp else "FAIL")


def write_report(docs: list[Doc],
                 pairs: list[tuple[Doc, Doc, list[Op], list[Op], list[Op], PairStats]]) -> Path:
    rows = "".join(
        f"<tr><td><b>{d.edition}</b></td><td>{d.extractor}</td><td>{d.encoding}</td>"
        f"<td>{len(d.body)}</td><td>{d.word_count:,}</td><td>{d.coverage:.1%}</td>"
        f"<td>{len(d.biblio)}</td><td>{len(d.apparatus)}</td>"
        f"<td>{html.escape(d.revision_date or '—')}</td>"
        f"<td><code>{d.raw_sha[:10]}</code></td><td><code>{d.text_sha[:10]}</code></td></tr>"
        for d in docs
    )
    parts = [
        f"<title>SEPDiff spike — {ENTRY}</title><style>{CSS}</style>",
        f"<h1>{html.escape(docs[-1].title or ENTRY)}</h1>",
        f'<div class="meta">спайк: {len(docs)} изданий, {len(pairs)} пар · '
        f"{html.escape(docs[-1].pubinfo)}</div>",
        "<h2>Снимки</h2><table><tr><th>издание</th><th>экстрактор</th><th>кодировка</th>"
        "<th>блоков</th><th>слов</th><th>покрытие</th><th>библ.</th><th>ссылки</th><th>ревизия</th>"
        f"<th>raw_sha</th><th>text_sha</th></tr>{rows}</table>",
    ]
    for a, b, body_ops, biblio_ops, app_ops, st in pairs:
        exp, verdict = _verdict((a.edition, b.edition), st.kind)
        vcls = verdict.lower()
        chips = "".join(f'<span class="chip">{html.escape(s)}</span>' for s in st.sections[:12])
        more = f" +{len(st.sections) - 12}" if len(st.sections) > 12 else ""
        parts.append(
            f'<section class="pair" id="{a.edition}-{b.edition}">'
            f"<h2>{a.edition} → {b.edition} · {st.kind}</h2>"
            f'<div class="sum">+{st.words_added:,} / −{st.words_removed:,} слов · '
            f"{st.blocks_changed} блоков изменено · библиография: +{st.biblio_added} −{st.biblio_removed} "
            f"~{st.biblio_modified} · ссылки/related: {st.apparatus_changed} изм. · эталон archinfo: {exp} "
            f'<span class="{vcls}">{verdict}</span><br>{chips}{more}</div>'
            f"{render_ops(body_ops)}"
            f"<details><summary>Библиография</summary>{render_ops(biblio_ops, context=0)}</details>"
            f"<details><summary>Other Internet Resources · Related Entries</summary>"
            f"{render_ops(app_ops, context=0)}</details>"
            f"</section>"
        )
    out = CACHE / "report.html"
    out.write_text("\n".join(parts), encoding="utf-8")
    return out


# --------------------------------------------------------------------------

def main() -> int:
    t0 = time.perf_counter()
    docs: list[Doc] = []
    for ed in EDITIONS:
        path = CACHE / f"{ENTRY}.{ed}.html"
        if path.exists():
            docs.append(extract(ed, path.read_bytes()))
    if len(docs) < 2:
        print(f"В кеше {len(docs)} снимков из {len(EDITIONS)} — сначала spike/fetch.py.")
        return 1

    print(f"{'издание':9s} {'экстр.':13s} {'блоков':>6s} {'слов':>7s} {'покрыт.':>7s} "
          f"{'библ':>4s} {'ссыл':>4s}  {'ревизия':17s} text_sha")
    for d in docs:
        print(f"{d.edition:9s} {d.extractor:13s} {len(d.body):>6d} {d.word_count:>7,d} "
              f"{d.coverage:>7.1%} {len(d.biblio):>4d} {len(d.apparatus):>4d}  "
              f"{d.revision_date or '—':17s} {d.text_sha[:10]}")
    if any(d.extractor != "modern" for d in docs):
        print("\ndiv[id] на страницах без #main-text (для настройки экстрактора):")
        for d in docs:
            if d.extractor != "modern":
                print(f"  {d.edition}: {', '.join(i for i in d.div_ids if i)}")

    pairs = []
    print(f"\n{'пара':20s} {'наш вердикт':12s} {'archinfo':12s}      {'+слов':>7s} {'−слов':>7s} "
          f"{'блоков':>6s}  разделы  ссылки")
    fails = 0
    for a, b in zip(docs, docs[1:]):
        body_ops = block_diff(a.body, b.body)
        biblio_ops = block_diff(a.biblio, b.biblio)
        app_ops = block_diff(a.apparatus, b.apparatus)
        st = pair_stats(a, b, body_ops, biblio_ops, app_ops)
        exp, verdict = _verdict((a.edition, b.edition), st.kind)
        fails += verdict == "FAIL"
        print(f"{a.edition + ' → ' + b.edition:20s} {st.kind:12s} {exp:12s} {verdict:4s} "
              f"{st.words_added:>7,d} {st.words_removed:>7,d} {st.blocks_changed:>6d}  "
              f"{len(st.sections):>7d}  {st.apparatus_changed:>6d}")
        pairs.append((a, b, body_ops, biblio_ops, app_ops, st))

    report = write_report(docs, pairs)
    print(f"\nПроверок против archinfo: {sum(1 for p in pairs if (p[0].edition, p[1].edition) in EXPECTED)}, "
          f"провалов: {fails}")
    if _budget_hits:
        print(f"ВНИМАНИЕ: предохранитель выравнивания сработал {_budget_hits} раз — "
              f"часть правок показана как удалить+вставить.")
    print(f"Отчёт: {report}")
    print(f"Время: {time.perf_counter() - t0:.2f} с")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
