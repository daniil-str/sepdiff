"""HTML снимка статьи SEP -> документ: три потока блоков текста и метаданные.

Эпохи вёрстки (проверено на изданиях 1997–2025, docs/journal.md §13):

    modern         2016+       #preamble + #main-text (+ #acknowledgments)
    aueditable     2007–2015   всё в #aueditable, разделы размечены только <h2>
    fallback-body  1997–2006   статья прямо в <body>, шапка сайта выше <h1>

Потоки: body (текст статьи), biblio (библиография), apparatus (Other Internet
Resources + Related Entries — в текст не входят, но их правки SEP считает
minor correction). Навигация, Academic Tools, подвал выбрасываются.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from selectolax.lexbor import LexborHTMLParser, LexborNode

from .normalize import normalize, sha

# Повышать при любой правке, меняющей результат извлечения: снимки со старой
# версией переизвлекаются из сохранённого HTML (service.Library.rebuild).
# 2: подвал внутри блока (+ пробная нормализация формул, откачена в 3).
# 4: многоточие, блоки из одних картинок, адреса ссылок в apparatus.
# 5: адреса ссылок — отдельный links_sha. 6: адреса по разделам.
# 7: набор дополнительных документов статьи — в struct_sha.
EXTRACT_VERSION = 7

HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
LEAF_BLOCKS = {"p", "li", "blockquote", "dd", "dt", "pre", "td", "th", "figcaption"} | HEADINGS
# Теги, наличие которых внутри узла означает «это контейнер, спускайся глубже».
STRUCT_TAGS = LEAF_BLOCKS | {"div", "table", "ul", "ol", "dl", "section"}
# Блоки внутри статьи, которые к тексту статьи не относятся.
JUNK_IDS = ["toc", "academic-tools", "other-internet-resources", "related-entries",
            "article-copyright", "pubinfo"]
# В вёрстке до 2016 у этих разделов нет своих div — только заголовки.
BIBLIO_HEADINGS = {"bibliography"}
APPARATUS_HEADINGS = {"other internet resources", "related entries"}
APPARATUS_IDS = ["other-internet-resources", "related-entries"]
DROP_HEADINGS = {"academic tools"}  # генерирует сайт, а не автор
# Идут после Related Entries, но это снова текст статьи: SEP правит их как minor.
BODY_HEADINGS = {"acknowledgments", "acknowledgements", "acknowledgment", "acknowledgement"}

# До ~2012 логические символы — картинки <img src="Box.gif">. Меняем на Unicode,
# иначе они молча пропадают, а переход GIF -> Unicode выглядит правкой.
SYMBOL_IMAGES = {
    "box": "□", "diamond": "◊", "ra": "→", "uc-rightarrow": "⇒", "lra": "↔",
    "forall": "∀", "exists": "∃", "e": "∃", "vel": "∨", "models": "⊨", "vdash": "⊢",
    "element": "∈", "not-in": "∉", "not-element": "∉", "supset": "⊃", "circ": "∘",
    "fishhook": "⥽", "perp": "⊥", "prime": "′", "omega": "ω",
}

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_MDY = r"([a-z]{3})[a-z]*\.?\s+(\d{1,2}),?\s+(\d{4})"   # "Jul 28, 2020" / "JUN 6 2003" / "September 8, 1997"
# Где SEP держал дату последней существенной правки — по эпохам вёрстки.
# Порядок важен: сначала явная «substantive», потом менее точные метки.
DATE_SOURCES: list[tuple[str, str]] = [
    ("pubinfo", rf"substantive revision\s+[a-z]{{3}}\s+{_MDY}"),         # 2007+
    ("header-substantive", rf"last substantive content change\s+{_MDY}"),  # 2003–2006
    ("header-revised", rf"content revised\s+{_MDY}"),                    # ~2002
    ("footer", rf"content last modified:?\s+{_MDY}"),                    # 1996–2001
    ("pubinfo-first", rf"first published\s+[a-z]{{3}}\s+{_MDY}"),        # 2007+, правок не было
]


def find_date(text: str) -> tuple[str | None, str | None]:
    """(дата последней существенной правки в ISO, источник) или (None, None)."""
    for source, pat in DATE_SOURCES:
        m = re.search(pat, text, re.I)
        if m and m.group(1).lower() in _MONTHS:
            return f"{m.group(3)}-{_MONTHS[m.group(1).lower()]:02d}-{int(m.group(2)):02d}", source
    return None, None


@dataclass
class Block:
    kind: str       # heading | p | li | text | ...
    section: str    # ближайший вышестоящий заголовок
    text: str       # нормализованный текст

    @property
    def words(self) -> list[str]:
        return self.text.split()


@dataclass
class Doc:
    extractor: str
    encoding: str
    title: str
    pubinfo: str
    revision_date: str | None
    date_source: str | None
    body: list[Block]
    biblio: list[Block]
    apparatus: list[Block]
    raw_sha: str
    coverage: float   # доля непробельных символов контейнера, попавших в блоки
    # Адреса ссылок Related Entries и Other Internet Resources (только вёрстка 2016+,
    # где у разделов свои div). Правку одного адреса SEP считает minor (frege fall2024:
    # http -> https), а надписи Related Entries подставляет сам — см. diffing.links_kind.
    related_links: list[str] = field(default_factory=list)
    oir_links: list[str] = field(default_factory=list)
    # Дополнительные документы статьи (notes.html, supplement.html, PDF): сами мы их
    # не качаем, но появление или исчезновение документа — правка (russell-paradox
    # win2024 -> spr2025: ссылка на новый supplement.html только в оглавлении).
    supplements: list[str] = field(default_factory=list)

    @staticmethod
    def _sha(blocks: list[Block]) -> str:
        return sha("\n".join(b.text for b in blocks))

    @property
    def body_sha(self) -> str:
        return self._sha(self.body)

    @property
    def biblio_sha(self) -> str:
        return self._sha(self.biblio)

    @property
    def apparatus_sha(self) -> str:
        return self._sha(self.apparatus)

    @property
    def links_sha(self) -> str | None:
        """Хеш адресов ссылок; None, если вёрстка их не выделяет (до 2016) — тогда не сравниваем."""
        if not (self.related_links or self.oir_links):
            return None
        return sha("\n".join(self.related_links) + "\n\n" + "\n".join(self.oir_links))

    @property
    def text_sha(self) -> str:
        return sha(self.body_sha + self.biblio_sha)

    @property
    def struct_sha(self) -> str:
        """Разбиение на блоки без текста (ловит «голый текст обернули в <p>») и набор доп. документов."""
        return sha(" ".join(b.kind for b in self.body + self.biblio) + "\n" + "\n".join(self.supplements))

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

    Инлайновое содержимое копится в буфер и сбрасывается блоком, когда
    встречается структурный дочерний узел. Так не теряется ни текст у
    <li>текст<ul>...</ul></li>, ни голый текст внутри <div> и <body>.
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


_FOOTER_RE = re.compile(r"\bcopyright\s*(©|\(c\))", re.I)


def _cut_footer(blocks: list[Block]) -> list[Block]:
    """Вёрстка до 2007: всё от последнего «Copyright ©» — подвал сайта (A–Z, даты, ссылки).

    Подвал может приклеиться к предыдущему блоку без разделителя (frege 2002:
    «…Frege's logic Copyright © 1995, 2002 by…»), поэтому ищем и внутри блока.
    """
    for i in range(len(blocks) - 1, -1, -1):
        m = _FOOTER_RE.search(blocks[i].text)
        if m:
            head = blocks[i].text[:m.start()].strip()
            return blocks[:i] + ([Block(blocks[i].kind, blocks[i].section, head)] if head else [])
    return blocks


def is_related_entry(block: Block) -> bool:
    """Блок из Related Entries (а не из Other Internet Resources)."""
    return _heading_key(block.section) == "related entries"


def _img_text(img: LexborNode) -> str:
    """Символ для картинки-символа, иначе устойчивая метка: смена картинки видна в diff."""
    src = img.attributes.get("src") or ""
    name = re.sub(r"\.\w+$", "", src.rsplit("/", 1)[-1]).lower()
    return SYMBOL_IMAGES.get(name) or f"[img:{name}]"


_IMAGE_ONLY_RE = re.compile(r"(\[img:[^\]]*\]\s*)+")


def _is_text(block: Block) -> bool:
    """Блок из одних картинок (портрет в шапке и т.п.) — не текст статьи (frege sum2006 -> fall2006)."""
    return not _IMAGE_ONLY_RE.fullmatch(block.text)


_SUPPLEMENT_RE = re.compile(r"(?:\./)?([\w.-]+\.(?:html?|pdf))", re.I)


def _supplements(tree: LexborHTMLParser) -> list[str]:
    """Файлы рядом со статьёй, на которые она ссылается: <a href="notes.html#1">, "supplement.pdf"."""
    found = set()
    for a in tree.css("a[href]"):
        href = (a.attributes.get("href") or "").split("#", 1)[0].split("?", 1)[0].strip()
        m = _SUPPLEMENT_RE.fullmatch(href)
        if m and m.group(1).lower() != "index.html":
            found.add(m.group(1))
    return sorted(found)


def _norm_href(href: str) -> str:
    # ссылки на соседние статьи внутри архива содержат издание — оно меняется каждый квартал
    return re.sub(r"/archives/(?:spr|sum|fall|win)\d{4}/", "/archives/*/", href.strip())


def _decode(raw: bytes) -> tuple[str, str]:
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace"), "cp1252"


def extract(raw: bytes) -> Doc:
    text, encoding = _decode(raw)
    tree = LexborHTMLParser(text)
    for n in tree.css("script, style, noscript"):
        n.decompose()
    for img in tree.css("img"):
        img.replace_with(_img_text(img))
    supplements = _supplements(tree)   # до вырезания оглавления: ссылка может быть только там

    title_node = tree.css_first("#aueditable h1") or tree.css_first("h1")
    title = normalize(title_node.text(separator=" ")) if title_node else ""

    pub_node = tree.css_first("#pubinfo")
    pubinfo = normalize(pub_node.text(separator=" ")) if pub_node else ""
    if pubinfo:
        revision_date, date_source = find_date(pubinfo)
    elif tree.body is not None:  # до 2007: дата в плашке шапки или в подвале
        revision_date, date_source = find_date(normalize(tree.body.text(separator=" ")))
    else:
        revision_date, date_source = None, None

    # Библиографию и ссылки снимаем первыми и вырезаем, чтобы они не попали
    # в тело, если в какой-то эпохе вёрстки лежат внутри #main-text.
    biblio: list[Block] = []
    biblio_root = tree.css_first("#bibliography")
    if biblio_root is not None:
        biblio = [b for b in _walk([biblio_root]) if b.kind != "heading"]
        biblio_root.decompose()
    apparatus: list[Block] = []
    links: dict[str, list[str]] = {aid: [] for aid in APPARATUS_IDS}
    for aid in APPARATUS_IDS:
        node = tree.css_first(f"#{aid}")
        if node is not None:
            apparatus += [b for b in _walk([node]) if b.kind != "heading"]
            links[aid] = [_norm_href(a.attributes.get("href") or "") for a in node.css("a[href]")]
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
        if root is None:
            raise ValueError("в HTML нет <body>")
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
    # Покрытие по непробельным символам, а не по словам: формула "(A→B)"
    # с разметкой <sub>/<em> иначе рассыпается на лишние «слова».
    container_chars = sum(len(re.sub(r"\s", "", normalize(r.text(separator="")))) for r in roots)
    block_chars = sum(len(b.text.replace(" ", "")) for b in walked)
    coverage = block_chars / container_chars if container_chars else 0.0
    if extractor == "fallback-body":
        walked = _cut_footer(walked)
    body, tail_biblio, tail_apparatus = _split_by_headings([b for b in walked if _is_text(b)])

    return Doc(extractor, encoding, title, pubinfo, revision_date, date_source,
               body, biblio + tail_biblio, apparatus + tail_apparatus, sha(raw), coverage,
               links["related-entries"], links["other-internet-resources"], supplements)
