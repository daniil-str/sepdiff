"""Командная строка: sepdiff init / seed / search / fetch / log / diff / show / import / serve."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated

import typer

from .diffing import Op, word_ops
from .fetcher import FetchError
from .present import editions_word as _editions_word
from .present import gap_texts
from .present import plural as _plural
from .present import stats_line as _stats_line
from .service import History, Library, PairDiff, ScanEvent, SepDiffError

app = typer.Typer(
    help="История правок статей Stanford Encyclopedia of Philosophy — как git log.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)

# Вид ревизии -> (значок, цвет)
KIND_STYLE: dict[str, tuple[str, str | None]] = {
    "substantive": ("●", "yellow"),
    "minor": ("○", "cyan"),
    "changed": ("◐", "magenta"),
    "markup_only": ("·", None),
    "created": ("◇", "green"),
    "removed": ("✕", "red"),
}
SECONDS_PER_REQUEST = 6   # 5 с паузы robots.txt + сам запрос


@app.callback()
def _setup() -> None:
    # Консоль Windows по умолчанию не UTF-8, а в тексте SEP полно □ ∀ ’.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


@contextmanager
def _library() -> Iterator[Library]:
    lib = Library()
    try:
        yield lib
    except (SepDiffError, FetchError) as exc:
        typer.secho(f"Ошибка: {exc}", fg="red", err=True)
        raise typer.Exit(1) from None
    except KeyboardInterrupt:
        typer.secho("\nПрервано. Скачанное сохранено — повторный запуск продолжит с того же места.", err=True)
        raise typer.Exit(130) from None
    finally:
        lib.close()


# ----------------------------------------------------------------------
# Команды
# ----------------------------------------------------------------------

@app.command()
def init() -> None:
    """Создать базу и загрузить список изданий SEP (1 запрос)."""
    with _library() as lib:
        eds = lib.init_editions()
        typer.echo(f"База: {lib.root / 'sepdiff.db'}")
        typer.echo(f"Изданий: {len(eds)} ({eds[0].label} — {eds[-1].label})")


@app.command()
def seed() -> None:
    """Загрузить оглавление последнего издания — для поиска статей (1 запрос)."""
    with _library() as lib:
        n = lib.seed()
        typer.echo(f"Статей в оглавлении: {n}. Поиск: sepdiff search <слово>")


@app.command()
def search(query: str) -> None:
    """Найти статью по slug или названию (нужен sepdiff seed)."""
    with _library() as lib:
        found = lib.search(query)
        if not found:
            typer.echo("Ничего не нашлось." + ("" if lib.search("") else " Оглавление пусто — выполните: sepdiff seed"))
        for slug, title in found:
            typer.echo(f"{slug:32s} {title or ''}")


@app.command()
def fetch(
    slug: str,
    edition: Annotated[list[str] | None, typer.Option(
        "--edition", "-e", help="Скачать только эти издания (можно повторять). Без опции — все.")] = None,
) -> None:
    """Скачать снимки статьи из архива SEP и построить её историю.

    Уже скачанное не перекачивается никогда. Запросы идут не чаще раза в 5 с
    (robots.txt SEP), так что полная история статьи — до ~10 минут.
    """
    def progress(ev: ScanEvent) -> None:
        if ev.phase == "plan":
            if ev.total:
                minutes = ev.total * SECONDS_PER_REQUEST / 60
                typer.echo(f"Нужно скачать {_editions_word(ev.total)}, ~{minutes:.0f} мин. "
                           "Ctrl+C можно нажать в любой момент — скачанное сохранится.")
            return
        where = f"[{ev.done}/{ev.total}]" if ev.phase == "fetch" else "[поиск]"
        status = typer.style("есть", fg="green") if ev.status == 200 else typer.style("нет (404)", dim=True)
        typer.echo(f"{where:>10} {ev.edition:9s} {status}")

    with _library() as lib:
        n = lib.scan(slug, edition, progress)
        hist = lib.history(slug)
        real = sum(1 for r in hist.revisions if r.kind != "markup_only")
        typer.echo(f"Запросов: {n}. Снимков: {hist.snapshots}, ревизий: {real} "
                   f"(ещё {len(hist.revisions) - real} — только вёрстка сайта). История: sepdiff log {slug}")


@app.command(name="import")
def import_(directory: Path) -> None:
    """Импортировать файлы <slug>.<edition>.html без сети (например, data/spike)."""
    with _library() as lib:
        counts = lib.import_dir(directory)
        for slug, n in counts.items():
            typer.echo(f"{slug:32s} {n} снимков")
        if not counts:
            typer.echo("Подходящих файлов не найдено.")


def _gap_lines(unchanged: int, unchecked: int) -> None:
    for i, text in enumerate(gap_texts(unchanged, unchecked)):
        unchecked_line = i == 1 or not unchanged
        typer.secho(f"  ┆ {text}", fg="yellow" if unchecked_line else None, dim=True)


def _print_log(hist: History, show_all: bool) -> None:
    typer.secho(f"{hist.title or hist.slug} ({hist.slug})", bold=True)
    typer.secho(f"снимков: {hist.snapshots} из {hist.editions} изданий", dim=True)
    _gap_lines(hist.unchanged_after, hist.unchecked_after)
    hidden = 0
    for rev in reversed(hist.revisions):
        if rev.kind == "markup_only" and not show_all:
            hidden += 1
            rev_line = None
        else:
            mark, color = KIND_STYLE[rev.kind]
            sections = ", ".join(rev.stats.sections[:4]) + (" …" if len(rev.stats.sections) > 4 else "")
            rev_line = (
                typer.style(f"{mark} {rev.edition.slug:9s}", fg=color, bold=rev.kind == "substantive")
                + typer.style(f" {rev.edition.released_on}  ", dim=True)
                + typer.style(f"{rev.kind:12s}", fg=color, dim=rev.kind == "markup_only")
                + f" {_stats_line(rev.kind, rev.stats):28s}"
                + typer.style(f" {sections}", dim=True)
                + (typer.style("  ⚠ объём текста скачет — возможна ошибка разбора", fg="red") if rev.suspect else "")
            )
        if rev_line:
            typer.echo(rev_line)
        _gap_lines(rev.unchanged_before, rev.unchecked_before)
    if hidden:
        what = _plural(hidden, "скрыта 1 ревизия", f"скрыто {hidden} ревизии", f"скрыто {hidden} ревизий")
        typer.secho(f"({what} markup_only — только вёрстка сайта; показать: --all)", dim=True)


@app.command()
def log(
    slug: str,
    show_all: Annotated[bool, typer.Option("--all", "-a", help="Показывать и markup_only.")] = False,
) -> None:
    """История ревизий статьи, новые сверху."""
    with _library() as lib:
        _print_log(lib.history(slug), show_all)


def _clip(text: str, n: int = 160) -> str:
    return text if len(text) <= n else text[:n].rstrip() + " …"


def _word_line(a: list[str], b: list[str], keep: int = 8) -> str:
    """Пословная правка в стиле git --word-diff: [-было-]{+стало+}, длинный контекст обрезан."""
    ops = word_ops(a, b)
    out: list[str] = []
    for n, (tag, wa, wb) in enumerate(ops):
        if tag == "equal":
            first, last = n == 0, n == len(ops) - 1
            if first and len(wa) > keep:
                wa = ["…", *wa[-keep:]]
            elif last and len(wa) > keep:
                wa = [*wa[:keep], "…"]
            elif not first and not last and len(wa) > 2 * keep + 1:
                wa = [*wa[:keep], "…", *wa[-keep:]]
            out.append(" ".join(wa))
            continue
        if wa:
            out.append(typer.style("[-" + " ".join(wa) + "-]", fg="red"))
        if wb:
            out.append(typer.style("{+" + " ".join(wb) + "+}", fg="green"))
    return " ".join(out)


def _print_ops(ops: list[Op], context: int) -> None:
    show: set[int] = set()
    for i, op in enumerate(ops):
        if op.kind != "equal":
            show.update(range(i - context, i + context + 1))
    skipped = 0
    last_section: str | None = None
    for i, op in enumerate(ops):
        if op.kind == "equal" and i not in show:
            skipped += 1
            continue
        if skipped:
            typer.secho(f"    … {skipped} без изменений …", dim=True)
            skipped = 0
        blk = op.b or op.a
        assert blk is not None
        if op.section != last_section and blk.kind != "heading":
            typer.secho(f"§ {op.section or '(преамбула)'}", bold=True)
        last_section = op.section
        if op.kind == "equal":
            typer.secho("  " + _clip(blk.text), dim=True)
        elif op.kind == "ins":
            typer.secho("+ " + blk.text, fg="green")
        elif op.kind == "del":
            typer.secho("- " + blk.text, fg="red")
        else:
            typer.echo("~ " + _word_line(op.a.words, op.b.words))  # type: ignore[union-attr]
    if skipped:
        typer.secho(f"    … {skipped} без изменений …", dim=True)


def _print_diff(d: PairDiff, context: int) -> None:
    st = d.stats
    mark, color = KIND_STYLE.get(d.kind, ("=", None))
    typer.secho(f"{d.title} ({d.slug})", bold=True)
    typer.echo(typer.style(f"{mark} {d.a.slug} → {d.b.slug} · {d.kind}", fg=color, bold=True)
               + f" · +{st.words_added:,} / −{st.words_removed:,} слов · {st.blocks_changed} блоков · "
               + f"{len(st.sections)} разделов")
    if not any(op.kind != "equal" for op in d.body + d.biblio + d.apparatus):
        typer.secho("Текст не изменился: отличается только вёрстка сайта.", dim=True)
        return
    if st.blocks_changed:
        typer.echo()
        _print_ops(d.body, context)
    for name, ops in (("Библиография", d.biblio), ("Other Internet Resources · Related Entries", d.apparatus)):
        if any(op.kind != "equal" for op in ops):
            typer.echo()
            typer.secho(f"── {name} ──", bold=True)
            _print_ops(ops, 0)


@app.command()
def diff(
    slug: str,
    a: Annotated[str, typer.Argument(help="Издание «было» (или единственное — тогда с предыдущим снимком).")],
    b: Annotated[str | None, typer.Argument(help="Издание «стало».")] = None,
    context: Annotated[int, typer.Option("--context", "-C", help="Сколько неизменённых абзацев показывать вокруг правки.")] = 1,
) -> None:
    """Что изменилось в статье между двумя изданиями.

    sepdiff diff kant spr2016 fall2020 — между двумя изданиями;
    sepdiff diff kant fall2024 — ревизия fall2024 относительно предыдущего снимка.
    """
    with _library() as lib:
        _print_diff(lib.diff(slug, a, b), context)


@app.command()
def show(slug: str, edition: str) -> None:
    """Извлечённый текст статьи в издании (то, что сравнивает diff)."""
    with _library() as lib:
        doc = lib.show(slug, edition)
        typer.secho(doc.title, bold=True)
        typer.secho(doc.pubinfo or f"дата ревизии: {doc.revision_date or 'неизвестна'}", dim=True)
        for blk in doc.body:
            typer.echo()
            if blk.kind == "heading":
                typer.secho(blk.text, bold=True)
            else:
                typer.echo(blk.text)
        for name, blocks in (("Bibliography", doc.biblio), ("Other Internet Resources · Related Entries", doc.apparatus)):
            if blocks:
                typer.echo()
                typer.secho(f"── {name} ──", bold=True)
                for blk in blocks:
                    typer.echo(f"• {blk.text}")


@app.command()
def serve(
    host: Annotated[str, typer.Option(help="Адрес. По умолчанию — только этот компьютер.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Порт.")] = 8000,
) -> None:
    """Веб-интерфейс: поиск, история, diff; сканирование идёт в фоне."""
    import uvicorn

    from .web.app import create_app

    if host not in ("127.0.0.1", "localhost", "::1"):
        typer.secho("Внимание: сервер будет виден из сети. Тексты SEP под копирайтом — не публикуйте их.",
                    fg="yellow")
    typer.echo(f"SEPDiff: http://{'localhost' if host == '127.0.0.1' else host}:{port}/  (Ctrl+C — остановить)")
    uvicorn.run(create_app(), host=host, port=port, log_level="warning")


if __name__ == "__main__":
    app()
