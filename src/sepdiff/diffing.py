"""Двухуровневый diff (блоки, затем слова) и классификация пары снимков (docs/domain.md, разделы 2 и 7)."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from difflib import SequenceMatcher
from typing import Protocol

from .extract import Block, Doc, is_related_entry

SIMILARITY = 0.5         # ниже — «удалён + добавлен», а не «изменён»
ALIGN_BUDGET = 40_000    # предохранитель от квадратичного взрыва на больших переписках

# Виды ревизий в истории статьи.
KINDS = ("created", "substantive", "minor", "changed", "markup_only", "removed")


@dataclass
class Op:
    kind: str           # equal | del | ins | mod
    a: Block | None
    b: Block | None

    @property
    def section(self) -> str:
        return (self.b or self.a).section  # type: ignore[union-attr]


def _align(a: list[Block], b: list[Block]) -> list[Op]:
    """Как difflib._fancy_replace: самая похожая пара -> «изменён», рекурсия слева и справа."""
    if not a:
        return [Op("ins", None, y) for y in b]
    if not b:
        return [Op("del", x, None) for x in a]
    if len(a) * len(b) > ALIGN_BUDGET:
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


def filter_section(ops: list[Op], section: str) -> list[Op]:
    """Только операции внутри указанного раздела (T9, «diff по разделам»):

    op.section — заголовок ближайшего вышестоящего <h2> у блока (см. Op.section).
    """
    return [op for op in ops if op.section == section]


WordOp = tuple[str, list[str], list[str]]   # (equal|replace|delete|insert, слова из a, слова из b)


def word_ops(a: list[str], b: list[str]) -> list[WordOp]:
    sm = SequenceMatcher(None, a, b, autojunk=False)
    return [(tag, a[i1:i2], b[j1:j2]) for tag, i1, i2, j1, j2 in sm.get_opcodes()]


# --------------------------------------------------------------------------
# Классификация
# --------------------------------------------------------------------------

class Versioned(Protocol):
    """Всё, что нужно классификатору: снимок из БД или только что извлечённый Doc."""

    @property
    def raw_sha(self) -> str | None: ...
    @property
    def text_sha(self) -> str | None: ...
    @property
    def apparatus_sha(self) -> str | None: ...
    @property
    def struct_sha(self) -> str | None: ...
    @property
    def links_sha(self) -> str | None: ...
    @property
    def supplements_sha(self) -> str | None: ...
    @property
    def revision_date(self) -> str | None: ...
    @property
    def date_source(self) -> str | None: ...


def _family(source: str | None) -> str:
    return "pubinfo" if source and source.startswith("pubinfo") else (source or "")


def same_date(a: Versioned, b: Versioned) -> bool:
    if a.revision_date == b.revision_date:
        return True
    if a.revision_date is None or b.revision_date is None or _family(a.date_source) == _family(b.date_source):
        return False
    # Одно и то же событие в вёрстке разных эпох записано с расхождением в день:
    # qualia "last modified Nov 1, 1997" -> "content revised NOV 2 1997".
    days = date.fromisoformat(a.revision_date) - date.fromisoformat(b.revision_date)
    return abs(days.days) <= 1


def links_kind(a: Doc, b: Doc) -> str:
    """Текст статьи тот же, поменялись Related Entries / Other Internet Resources: minor или markup_only.

    Эвристики по данным этапа 3 (docs/journal.md §16):
    - Related Entries: надписи SEP подставляет сам из названий целевых статей и
      меняет при их переименовании без правки этой статьи (russell-paradox
      win2014 -> spr2015: «frege-logic» -> «Frege, Gottlob: theorem…»). Если адреса
      известны (вёрстка 2016+), сравниваем их; иначе — надписи.
    - Other Internet Resources: мёртвые ссылки SEP вычищает, не отмечая правку
      (frege fall2009 -> win2009), а новая ссылка — minor (logic-modal sum2007 ->
      spr2008). Исправленный адрес — тоже minor, хотя SEP здесь непоследователен:
      отметил frege fall2024 (http -> https) и russell-paradox win2014 (новый адрес
      журнала), не отметил russell-paradox sum2018 и turing-machine sum2016 (копия
      в Wayback Machine). Правка на странице реальная — показываем.
    """
    def texts(d: Doc, related: bool) -> list[str]:
        return [x.text for x in d.apparatus if is_related_entry(x) == related]

    if a.related_links and b.related_links:
        related_changed = a.related_links != b.related_links
    else:
        related_changed = texts(a, True) != texts(b, True)
    if related_changed:
        return "minor"
    added = Counter(texts(b, False)) - Counter(texts(a, False))
    if a.oir_links and b.oir_links:
        added += Counter(b.oir_links) - Counter(a.oir_links)
    return "minor" if added else "markup_only"


def classify(a: Versioned, b: Versioned) -> str:
    """Вид изменения между двумя соседними снимками (таблица в docs/domain.md, раздел 2).

    Правкам только в ссылках нужны блоки (links_kind): по снимкам из БД (одни
    хеши) такая пара выходит minor, и Library.rebuild доуточняет её по Doc.
    """
    if a.raw_sha == b.raw_sha:
        return "identical"
    dated = a.revision_date is not None and b.revision_date is not None
    same_content = (a.text_sha == b.text_sha and a.apparatus_sha == b.apparatus_sha
                    and a.struct_sha == b.struct_sha)
    # Дата — это и есть метка SEP: сменилась -> substantive, даже если текст тот же
    # (logic-paraconsistent fall1997 -> win2000: поменялись только ссылки). Кроме
    # случая, когда даты взяты из разных мест, а содержимое то же: подвал хранит
    # дату любой правки, плашка — существенной (turing-machine fall2002 -> win2002:
    # «last modified 2002-03-09» -> «content revised FEB 11 2000»).
    if dated and not same_date(a, b):
        if not (same_content and _family(a.date_source) != _family(b.date_source)):
            return "substantive"
    if a.text_sha == b.text_sha:
        if a.struct_sha != b.struct_sha:
            return "minor"
        # адреса ссылок сравниваем, только если вёрстка обоих снимков их выделяет:
        # на смене вёрстки (kant sum2014 -> fall2014) они появляются без всякой правки
        links_changed = bool(a.links_sha and b.links_sha and a.links_sha != b.links_sha)
        # содержимое доп. документов знает только Snap из БД (--supplements), не Doc
        supplements_changed = bool(
            a.supplements_sha and b.supplements_sha and a.supplements_sha != b.supplements_sha)
        if a.apparatus_sha != b.apparatus_sha or links_changed or supplements_changed:
            if isinstance(a, Doc) and isinstance(b, Doc):
                return links_kind(a, b)
            return "minor"
        return "markup_only"
    return "minor" if dated else "changed"   # без даты вид правки не определить


# --------------------------------------------------------------------------
# Сводка
# --------------------------------------------------------------------------

@dataclass
class PairStats:
    words_added: int = 0
    words_removed: int = 0
    blocks_changed: int = 0
    sections: list[str] = field(default_factory=list)
    biblio_added: int = 0
    biblio_removed: int = 0
    biblio_modified: int = 0
    apparatus_changed: int = 0


def pair_stats(body_ops: list[Op], biblio_ops: list[Op], apparatus_ops: list[Op]) -> PairStats:
    st = PairStats()
    for op in body_ops:
        if op.kind == "equal":
            continue
        st.blocks_changed += 1
        if op.section and op.section not in st.sections:
            st.sections.append(op.section)
        if op.kind == "ins":
            st.words_added += len(op.b.words)  # type: ignore[union-attr]
        elif op.kind == "del":
            st.words_removed += len(op.a.words)  # type: ignore[union-attr]
        else:
            for _, wa, wb in word_ops(op.a.words, op.b.words):  # type: ignore[union-attr]
                st.words_removed += len(wa) if _ != "equal" else 0
                st.words_added += len(wb) if _ != "equal" else 0
    st.biblio_added = sum(1 for o in biblio_ops if o.kind == "ins")
    st.biblio_removed = sum(1 for o in biblio_ops if o.kind == "del")
    st.biblio_modified = sum(1 for o in biblio_ops if o.kind == "mod")
    st.apparatus_changed = sum(1 for o in apparatus_ops if o.kind != "equal")
    return st
