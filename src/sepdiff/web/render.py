"""Блочный diff -> HTML с <ins>/<del> (текст экранируется через Markup.format)."""

from __future__ import annotations

from markupsafe import Markup, escape

from ..diffing import Op, word_ops
from ..present import plural


def words_html(a: list[str], b: list[str]) -> Markup:
    parts: list[Markup] = []
    for tag, wa, wb in word_ops(a, b):
        if tag == "equal":
            parts.append(escape(" ".join(wa)))
            continue
        if wa:
            parts.append(Markup("<del>{}</del>").format(" ".join(wa)))
        if wb:
            parts.append(Markup("<ins>{}</ins>").format(" ".join(wb)))
    return Markup(" ").join(parts)


def words_side(a: list[str], b: list[str]) -> tuple[Markup, Markup]:
    """Как words_html, но раздельно для колонок «было»/«стало»: слева только
    убранные слова помечены <del>, справа — только добавленные <ins>."""
    left: list[Markup] = []
    right: list[Markup] = []
    for tag, wa, wb in word_ops(a, b):
        if tag == "equal":
            left.append(escape(" ".join(wa)))
            right.append(escape(" ".join(wb)))
            continue
        if wa:
            left.append(Markup("<del>{}</del>").format(" ".join(wa)))
        if wb:
            right.append(Markup("<ins>{}</ins>").format(" ".join(wb)))
    return Markup(" ").join(left), Markup(" ").join(right)


def _skip(n: int, full: bool = False) -> Markup:
    word = plural(n, "абзац", "абзаца", "абзацев")
    cls = "skip full" if full else "skip"
    return Markup('<div class="{}">… {} {} без изменений …</div>').format(cls, n, word)


def ops_html(ops: list[Op], context: int = 1) -> Markup:
    """Изменённые блоки плюс `context` соседних; остальное свёрнуто."""
    show: set[int] = set()
    for i, op in enumerate(ops):
        if op.kind != "equal":
            show.update(range(i - context, i + context + 1))
    out: list[Markup] = []
    skipped = 0
    last_section: str | None = None
    for i, op in enumerate(ops):
        if op.kind == "equal" and i not in show:
            skipped += 1
            continue
        if skipped:
            out.append(_skip(skipped))
            skipped = 0
        blk = op.b or op.a
        assert blk is not None
        heading = " heading" if blk.kind == "heading" else ""
        if op.section != last_section and not heading:
            out.append(Markup('<div class="secmark">§ {}</div>').format(op.section or "преамбула"))
        last_section = op.section
        if op.kind == "mod":
            out.append(Markup('<div class="blk mod{}">{}</div>').format(
                heading, words_html(op.a.words, op.b.words)))  # type: ignore[union-attr]
        else:
            cls = {"equal": "ctx", "ins": "ins", "del": "del"}[op.kind]
            out.append(Markup('<div class="blk {}{}">{}</div>').format(cls, heading, blk.text))
    if skipped:
        out.append(_skip(skipped))
    return Markup("\n").join(out)


def ops_html_side(ops: list[Op], context: int = 1) -> Markup:
    """Тот же выбор блоков, что и ops_html, но парами колонок «было» / «стало» —
    для CSS-грида в две колонки (T3, PLAN.md); на узком экране CSS сам
    схлопывает их в одну без переупорядочивания разметки."""
    show: set[int] = set()
    for i, op in enumerate(ops):
        if op.kind != "equal":
            show.update(range(i - context, i + context + 1))
    out: list[Markup] = []
    skipped = 0
    last_section: str | None = None
    for i, op in enumerate(ops):
        if op.kind == "equal" and i not in show:
            skipped += 1
            continue
        if skipped:
            out.append(_skip(skipped, full=True))
            skipped = 0
        blk = op.b or op.a
        assert blk is not None
        heading = " heading" if blk.kind == "heading" else ""
        if op.section != last_section and not heading:
            out.append(Markup('<div class="secmark full">§ {}</div>').format(op.section or "преамбула"))
        last_section = op.section
        if op.kind == "mod":
            left, right = words_side(op.a.words, op.b.words)  # type: ignore[union-attr]
            out.append(Markup('<div class="blk mod{}">{}</div><div class="blk mod{}">{}</div>').format(
                heading, left, heading, right))
        elif op.kind == "equal":
            out.append(Markup('<div class="blk ctx{}">{}</div><div class="blk ctx{}">{}</div>').format(
                heading, blk.text, heading, blk.text))
        elif op.kind == "ins":
            out.append(Markup('<div class="blk empty"></div><div class="blk ins{}">{}</div>').format(heading, blk.text))
        else:
            out.append(Markup('<div class="blk del{}">{}</div><div class="blk empty"></div>').format(heading, blk.text))
    if skipped:
        out.append(_skip(skipped, full=True))
    return Markup("\n").join(out)


def changed(ops: list[Op]) -> bool:
    return any(op.kind != "equal" for op in ops)
