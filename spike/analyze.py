"""Спайк, шаг 2: извлечь текст из закешированных снимков и сравнить соседние версии.

Работает полностью офлайн по data/spike/ — сеть не трогает, поэтому
по экстрактору можно итерировать сколько угодно.

Что проверяем этим спайком:
  1. Отсекается ли боилерплейт (навигация, Academic Tools, Related Entries...).
  2. Даёт ли нормализация ОДИНАКОВЫЙ хеш для изданий без изменений
     (контрольные пары вроде kant fall2023 -> win2023).
  3. Совпадает ли наша классификация substantive/minor с archinfo.cgi.
     Гипотеза: substantive <=> сменилась дата "substantive revision" в #pubinfo.
  4. Читаем ли diff глазами (data/spike/report.<entry>.html).

Запуск:  uv run python spike/analyze.py [entry ...]   (по умолчанию все из ARCHINFO)
"""

from __future__ import annotations

import hashlib
import html
import re
import sys
import time
import unicodedata
from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path

from fetch import CACHE
from selectolax.lexbor import LexborHTMLParser, LexborNode

# --------------------------------------------------------------------------
# Эталон: снят вручную с archinfo.cgi?entry=<slug> (автоматически туда нельзя —
# /cgi-bin/ закрыт в robots.txt). Там перечислены только издания, в которых
# статья менялась; в остальных лежит копия предыдущей версии.
# Ожидание для пары (a, b) — самое сильное изменение в изданиях (a, b];
# "unchanged" = identical или markup_only.
# --------------------------------------------------------------------------
_S, _M = "substantive", "minor"
ARCHINFO: dict[str, dict[str, str]] = {
    "kant": {
        "sum2010": "first", "fall2010": _M, "sum2014": _M, "spr2016": _S, "sum2018": _M,
        "spr2020": _M, "fall2020": _S, "fall2023": _M, "fall2024": _S,
    },
    "qualia": {
        "fall1997": "first", "win1997": _S, "spr2003": _S, "sum2003": _S, "fall2007": _S,
        "sum2008": _M, "fall2008": _M, "sum2009": _M, "sum2013": _S, "fall2013": _M,
        "fall2015": _S, "win2016": _M, "win2017": _S, "sum2018": _M, "fall2021": _S,
        "fall2025": _S,
    },
    "logic-paraconsistent": {
        "fall1997": "first", "win2000": _S, "sum2004": _M, "win2004": _S, "win2007": _S,
        "fall2008": _M, "spr2009": _S, "sum2009": _M, "spr2013": _M, "sum2013": _S,
        "fall2013": _M, "spr2015": _M, "win2016": _S, "fall2017": _S, "sum2018": _S,
        "spr2022": _S, "spr2025": _M, "sum2026": _S,
    },
    "logic-modal": {
        "spr2000": "first", "win2001": _S, "win2003": _S, "sum2005": _M, "sum2007": _S,
        "spr2008": _M, "sum2008": _S, "fall2008": _M, "win2008": _M, "spr2009": _M,
        "fall2009": _S, "win2009": _S, "win2012": _M, "spr2013": _M, "sum2014": _S,
        "spr2016": _M, "fall2018": _S, "sum2021": _M, "spr2023": _S, "spr2024": _M,
    },
}
_SEASONS = {"spr": 0, "sum": 1, "fall": 2, "win": 3}   # 21 марта / июня / сентября / декабря
_EDITION_RE = re.compile(r"(spr|sum|fall|win)(\d{4})")


def ed_key(edition: str) -> tuple[int, int]:
    m = _EDITION_RE.fullmatch(edition)
    if m is None:
        raise ValueError(f"не издание SEP: {edition!r}")
    return int(m.group(2)), _SEASONS[m.group(1)]


def expected(entry: str, a: str, b: str) -> str | None:
    hist = ARCHINFO.get(entry)
    if hist is None:
        return None
    labels = [lab for ed, lab in hist.items() if ed_key(a) < ed_key(ed) <= ed_key(b)]
    if _S in labels:
        return _S
    return _M if labels else "unchanged"

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
# Идут после Related Entries, но это снова текст статьи.
BODY_HEADINGS = {"acknowledgments", "acknowledgements", "acknowledgment", "acknowledgement"}

_PUNCT = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "`": "'",   # `qualia' в изданиях 1997 года
    "“": '"', "”": '"', "„": '"',
    "–": "-", "—": "-", "−": "-",
    " ": " ", " ": " ", " ": " ", "​": "",
})
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_MDY = r"([a-z]{3})[a-z]*\.?\s+(\d{1,2}),?\s+(\d{4})"   # "Jul 28, 2020" / "JUN 6 2003" / "September 8, 1997"
# Где SEP держал дату последней существенной правки — по эпохам вёрстки.
# Порядок важен: сначала явная «substantive», потом менее точные метки.
_DATE_PATTERNS = [
    rf"substantive revision\s+[a-z]{{3}}\s+{_MDY}",          # 2007+: #pubinfo
    rf"last substantive content change\s+{_MDY}",            # 2003-2006: плашка в шапке
    rf"content revised\s+{_MDY}",                            # ~2002: плашка в шапке
    rf"content last modified:?\s+{_MDY}",                    # 1996-2001: подвал
    rf"first published\s+[a-z]{{3}}\s+{_MDY}",              # 2007+, правок ещё не было
]


def _find_date(text: str) -> tuple[str | None, int]:
    """(дата последней существенной правки в ISO, номер шаблона) или (None, -1)."""
    for i, pat in enumerate(_DATE_PATTERNS):
        m = re.search(pat, text, re.I)
        if m and m.group(1).lower() in _MONTHS:
            return f"{m.group(3)}-{_MONTHS[m.group(1).lower()]:02d}-{int(m.group(2)):02d}", i
    return None, -1


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
    date_source: int = -1    # индекс в _DATE_PATTERNS: из какой эпохи вёрстки взята дата

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
    def struct_sha(self) -> str:
        """Разбиение на блоки без текста: ловит правки вроде «голый текст обернули в <p>»."""
        return sha(" ".join(b.kind for b in self.body + self.biblio))

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
            if key in BODY_HEADINGS:
                mode = "body"
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


def _drop_before(node: LexborNode, stop: LexborNode) -> None:
    """Удалить всё, что в документе идёт раньше node (в пределах stop)."""
    while node is not None and node is not stop:
        prev = node.prev
        while prev is not None:
            nxt = prev.prev
            prev.decompose()
            prev = nxt
        node = node.parent


_FOOTER_RE = re.compile(r"^copyright\s*(©|\(c\))", re.I)


def _cut_footer(blocks: list[Block]) -> list[Block]:
    """Старая вёрстка: всё от последнего «Copyright ©» — подвал сайта (A–Z, даты, ссылки)."""
    for i in range(len(blocks) - 1, -1, -1):
        if _FOOTER_RE.match(blocks[i].text):
            return blocks[:i]
    return blocks


# До ~2012 логические символы в SEP — картинки <img src="Box.gif">. Меняем на
# Unicode, иначе они молча пропадают из текста, а переход GIF -> Unicode
# (logic-modal win2003 -> sum2005) выглядит как правка в 132 абзацах.
SYMBOL_IMAGES = {
    "box": "□", "diamond": "◊", "ra": "→", "uc-rightarrow": "⇒", "lra": "↔",
    "forall": "∀", "exists": "∃", "e": "∃", "vel": "∨", "models": "⊨", "vdash": "⊢",
    "element": "∈", "not-in": "∉", "not-element": "∉", "supset": "⊃", "circ": "∘",
    "fishhook": "⥽", "perp": "⊥", "prime": "′", "omega": "ω",
}


def _img_text(img: LexborNode) -> str:
    """Символ для картинки-символа, иначе устойчивая метка: смена картинки видна в diff."""
    src = img.attributes.get("src") or ""
    name = re.sub(r"\.\w+$", "", src.rsplit("/", 1)[-1]).lower()
    return SYMBOL_IMAGES.get(name) or f"[img:{name}]"


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
    for img in tree.css("img"):
        img.replace_with(_img_text(img))

    div_ids = [n.attributes.get("id") or "" for n in tree.css("div[id]")]

    title_node = tree.css_first("#aueditable h1") or tree.css_first("h1")
    title = normalize(title_node.text(separator=" ")) if title_node else ""

    pub_node = tree.css_first("#pubinfo")
    pubinfo = normalize(pub_node.text(separator=" ")) if pub_node else ""
    if pubinfo:
        revision_date, date_source = _find_date(pubinfo)
    elif tree.body is not None:  # до 2007: дата в плашке шапки или в подвале
        revision_date, date_source = _find_date(normalize(tree.body.text(separator=" ")))
    else:
        revision_date, date_source = None, -1

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
        # Благодарности лежат вне #main-text, после Related Entries, но их пишет
        # автор и правит SEP (кто нашёл опечатку) — это часть текста статьи.
        roots = [n for n in (tree.css_first("#preamble"), main, tree.css_first("#acknowledgments"))
                 if n is not None]
    else:
        root = tree.css_first("#aueditable") or tree.css_first("#article")
        extractor = "aueditable" if root is not None else "fallback-body"
        root = root or tree.body
        if extractor == "fallback-body":
            # До 2007 статья лежит прямо в <body>: шапка сайта (баннер архива,
            # «how to cite», A–Z, плашка с датой) — всё, что выше <h1>.
            h1 = root.css_first("h1")
            if h1 is not None:
                _drop_before(h1, root)
        for jid in JUNK_IDS:
            for n in root.css(f"#{jid}"):
                n.decompose()
        for n in root.css("h1"):
            n.decompose()  # заголовок статьи уже взят в title
        _drop_anchor_lists(root)
        roots = [root]

    walked = _walk(roots)
    # Покрытие считаем по непробельным символам, а не по словам: в формулах
    # "(A→B)" с разметкой <sub>/<em> иначе рассыпается на лишние «слова».
    container_chars = sum(len(re.sub(r"\s", "", normalize(r.text(separator="")))) for r in roots)
    block_chars = sum(len(b.text.replace(" ", "")) for b in walked)
    coverage = block_chars / container_chars if container_chars else 0.0
    if extractor == "fallback-body":
        walked = _cut_footer(walked)
    body, tail_biblio, tail_apparatus = _split_by_headings(walked)
    biblio += tail_biblio
    apparatus += tail_apparatus

    return Doc(edition, extractor, encoding, title, pubinfo, revision_date,
               body, biblio, apparatus, sha(raw), coverage, div_ids, date_source)


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
            ops += [Op("equal", a[i], b[j]) for i, j in zip(range(i1, i2), range(j1, j2), strict=True)]
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


def _same_date(a: Doc, b: Doc) -> bool:
    if a.revision_date == b.revision_date:
        return True
    if a.date_source == b.date_source:
        return False
    # Одно и то же событие в вёрстке разных эпох записано с расхождением в день:
    # qualia "last modified Nov 1, 1997" -> "content revised NOV 2 1997",
    # logic-paraconsistent "Dec 5, 2000" -> "last substantive content change DEC 6 2000".
    days = date.fromisoformat(a.revision_date) - date.fromisoformat(b.revision_date)  # type: ignore[arg-type]
    return abs(days.days) <= 1


def classify(a: Doc, b: Doc) -> str:
    if a.raw_sha == b.raw_sha:
        return "identical"
    dated = a.revision_date is not None and b.revision_date is not None
    # Дата — это и есть метка SEP: сменилась -> substantive, даже если текст тот же
    # (logic-paraconsistent fall1997 -> win2000: поменялись только ссылки).
    if dated and not _same_date(a, b):
        return "substantive"
    if a.text_sha == b.text_sha:
        if a.apparatus_sha != b.apparatus_sha or a.struct_sha != b.struct_sha:
            return "minor"
        return "markup_only"
    return "minor" if dated else "changed"   # без даты вид правки не определить


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


def _verdict(entry: str, pair: tuple[str, str], kind: str) -> tuple[str, str]:
    exp = expected(entry, *pair)
    if exp is None:
        return "—", ""
    if kind == "changed":
        return exp, "?"
    got = "unchanged" if kind in ("identical", "markup_only") else kind
    return exp, ("PASS" if got == exp else "FAIL")


def write_report(entry: str, docs: list[Doc],
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
        f"<title>SEPDiff spike — {entry}</title><style>{CSS}</style>",
        f"<h1>{html.escape(docs[-1].title or entry)}</h1>",
        f'<div class="meta">спайк: {len(docs)} изданий, {len(pairs)} пар · '
        f"{html.escape(docs[-1].pubinfo)}</div>",
        "<h2>Снимки</h2><table><tr><th>издание</th><th>экстрактор</th><th>кодировка</th>"
        "<th>блоков</th><th>слов</th><th>покрытие</th><th>библ.</th><th>ссылки</th><th>ревизия</th>"
        f"<th>raw_sha</th><th>text_sha</th></tr>{rows}</table>",
    ]
    for a, b, body_ops, biblio_ops, app_ops, st in pairs:
        exp, verdict = _verdict(entry, (a.edition, b.edition), st.kind)
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
    out = CACHE / f"report.{entry}.html"
    out.write_text("\n".join(parts), encoding="utf-8")
    return out


# --------------------------------------------------------------------------

def cached_editions(entry: str) -> list[str]:
    eds = [p.name[len(entry) + 1:-len(".html")] for p in CACHE.glob(f"{entry}.*.html")]
    return sorted((e for e in eds if _EDITION_RE.fullmatch(e)), key=ed_key)


def analyze_entry(entry: str) -> tuple[int, int]:
    """Таблицы в консоль + отчёт по одной статье. -> (проверок против archinfo, провалов)."""
    docs = [extract(ed, (CACHE / f"{entry}.{ed}.html").read_bytes())
            for ed in cached_editions(entry)]
    print(f"\n=== {entry}: {len(docs)} снимков ===")
    if len(docs) < 2:
        print("мало снимков в кеше — сначала spike/fetch.py")
        return 0, 0

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
    checks = fails = 0
    for a, b in zip(docs, docs[1:], strict=False):
        body_ops = block_diff(a.body, b.body)
        biblio_ops = block_diff(a.biblio, b.biblio)
        app_ops = block_diff(a.apparatus, b.apparatus)
        st = pair_stats(a, b, body_ops, biblio_ops, app_ops)
        exp, verdict = _verdict(entry, (a.edition, b.edition), st.kind)
        checks += verdict in ("PASS", "FAIL")
        fails += verdict == "FAIL"
        print(f"{a.edition + ' → ' + b.edition:20s} {st.kind:12s} {exp:12s} {verdict:4s} "
              f"{st.words_added:>7,d} {st.words_removed:>7,d} {st.blocks_changed:>6d}  "
              f"{len(st.sections):>7d}  {st.apparatus_changed:>6d}")
        pairs.append((a, b, body_ops, biblio_ops, app_ops, st))

    print(f"Отчёт: {write_report(entry, docs, pairs)}")
    return checks, fails


def main(argv: list[str]) -> int:
    t0 = time.perf_counter()
    results = [analyze_entry(e) for e in (argv or list(ARCHINFO))]
    checks = sum(c for c, _ in results)
    fails = sum(f for _, f in results)
    print(f"\nИтого проверок против archinfo: {checks}, провалов: {fails}")
    if _budget_hits:
        print(f"ВНИМАНИЕ: предохранитель выравнивания сработал {_budget_hits} раз — "
              f"часть правок показана как удалить+вставить.")
    print(f"Время: {time.perf_counter() - t0:.2f} с")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
