"""Экспорт истории статьи в настоящий git-репозиторий (PLAN.md §9).

Каждая ревизия — коммит с датой выхода издания, каждое издание — тег, так что
работают `git log`, `git diff spr2016 fall2020 --word-diff`, `git blame text.md`
(«в каком издании появился этот абзац»). Абзац — одна строка: blame и diff
работают по абзацам, --word-diff — внутри них.

Репозиторий содержит тексты SEP (копирайт авторов) — только для себя, не публиковать.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .extract import Doc
from .present import KIND_LABEL, TEXT_KINDS, stats_line
from .service import Library, SepDiffError

README = """# {title}

История статьи Stanford Encyclopedia of Philosophy `{slug}`, собранная
SEPDiff из архивных изданий: каждая ревизия — коммит с датой выхода издания,
каждое издание — тег.

    git log --oneline
    git diff spr2016 fall2020 --word-diff
    git blame text.md

Тексты — © их авторов и SEP. Локальная копия для чтения: не публикуйте её.
Оригинал: https://plato.stanford.edu/entries/{slug}/
"""


def doc_files(doc: Doc) -> dict[str, str]:
    """Документ -> файлы репозитория: абзац на строку, заголовки разделов — markdown."""
    lines = [f"# {doc.title}", ""]
    for blk in doc.body:
        lines += ([f"## {blk.text}", ""] if blk.kind == "heading" else [blk.text, ""])
    files = {"text.md": "\n".join(lines)}
    if doc.biblio:
        files["bibliography.md"] = "# Bibliography\n\n" + "\n".join(f"- {b.text}" for b in doc.biblio) + "\n"
    if doc.apparatus:
        files["links.md"] = ("# Other Internet Resources · Related Entries\n\n"
                             + "\n".join(f"- {b.text}" for b in doc.apparatus) + "\n")
    return files


def export_git(lib: Library, slug: str, dest: Path, include_markup: bool = False,
               include_live: bool = True) -> int:
    """Создать репозиторий в пустом (или несуществующем) каталоге dest. Возвращает число коммитов."""
    git = shutil.which("git")
    if git is None:
        raise SepDiffError("не найден git — установите его, чтобы экспортировать историю")
    if dest.exists() and any(dest.iterdir()):
        raise SepDiffError(f"каталог {dest} не пуст — экспорт создаёт новый репозиторий")
    hist = lib.history(slug)
    dest.mkdir(parents=True, exist_ok=True)

    def run(*args: str, date: str | None = None) -> None:
        env = dict(os.environ)
        env.update(GIT_AUTHOR_NAME="SEPDiff", GIT_AUTHOR_EMAIL="sepdiff@localhost",
                   GIT_COMMITTER_NAME="SEPDiff", GIT_COMMITTER_EMAIL="sepdiff@localhost")
        if date:
            env.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
        try:
            subprocess.run([git, *args], cwd=dest, env=env, check=True, capture_output=True)
        except subprocess.CalledProcessError as exc:
            err = exc.stderr.decode("utf-8", "replace").strip()
            raise SepDiffError(f"git {args[0]} не удался: {err}") from None

    run("init", "-q", "-b", "main")
    (dest / "README.md").write_text(README.format(title=hist.title or slug, slug=slug), encoding="utf-8")
    commits = 0

    def commit(files: dict[str, str] | None, tag: str, message: str, date: str) -> None:
        nonlocal commits
        for old in ("text.md", "bibliography.md", "links.md"):
            (dest / old).unlink(missing_ok=True)
        for name, content in (files or {}).items():
            (dest / name).write_text(content, encoding="utf-8", newline="\n")
        run("add", "-A")
        run("commit", "-q", "--allow-empty", "-m", message, date=date)
        run("tag", tag)
        commits += 1

    for rev in hist.revisions:
        if rev.kind == "markup_only" and not include_markup:
            continue
        ed = rev.edition
        url = f"https://plato.stanford.edu/archives/{ed.slug}/entries/{slug}/"
        summary = stats_line(rev.kind, rev.stats)
        sections = ", ".join(rev.stats.sections[:8])
        body = "\n".join(x for x in (summary, f"Разделы: {sections}" if sections else "", "", url) if x is not None)
        files = None if rev.kind == "removed" else doc_files(lib.show(slug, ed.slug))
        commit(files, ed.slug, f"{ed.label}: {KIND_LABEL[rev.kind]}\n\n{body}", f"{ed.released_on}T12:00:00+0000")

    live = hist.live
    if include_live and live is not None and live.kind in TEXT_KINDS | {"removed"}:
        files = None if live.kind == "removed" else doc_files(lib.show(slug, "live"))
        commit(files, "live", f"Текущая версия на сайте (ещё не в архиве): {KIND_LABEL[live.kind]}\n\n"
               f"{stats_line(live.kind, live.stats)}\n\nhttps://plato.stanford.edu/entries/{slug}/",
               live.checked_at)
    return commits
