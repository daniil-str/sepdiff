"""Веб-интерфейс: FastAPI + Jinja2 + htmx (PLAN.md §7).

Каждая страница работает и без JavaScript (полная перезагрузка); htmx только
подменяет фрагменты: результаты поиска, таблицу истории, панель diff,
полосу прогресса сканирования.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable, Iterator
from datetime import date
from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import config, jobs
from ..fetcher import Fetcher
from ..present import KIND_HINT, KIND_LABEL, KIND_MARK, TEXT_KINDS, eta_text, gap_texts, stats_line
from ..service import SLUG_RE, History, Library, PairDiff, SepDiffError, check_slug
from .feed import atom, rfc3339
from .render import changed, ops_html

HERE = Path(__file__).parent
HTMX_CDN = "https://cdn.jsdelivr.net/npm/htmx.org@2.0.4/dist/htmx.min.js"


def lib_dep(request: Request) -> Iterator[Library]:
    """Своё соединение с базой на каждый запрос."""
    lib = Library(request.app.state.root)
    try:
        yield lib
    finally:
        lib.close()


# На уровне модуля: FastAPI разрешает строковые аннотации по глобальным именам.
Lib = Annotated[Library, Depends(lib_dep)]


def history_rows(hist: History, show_all: bool) -> tuple[list[dict[str, object]], int]:
    """Строки таблицы истории, новые сверху; markup_only сворачиваются в «без изменений»."""
    items: list[dict[str, object]] = []

    def gap(unchanged: int, unchecked: int) -> None:
        items.append({"gap": True, "unchanged": unchanged, "unchecked": unchecked})

    gap(hist.unchanged_after, hist.unchecked_after)
    hidden = 0
    for rev in reversed(hist.revisions):
        if rev.kind == "markup_only" and not show_all:
            hidden += 1
            gap(1, 0)
        else:
            items.append({"gap": False, "rev": rev, "stats": stats_line(rev.kind, rev.stats)})
        gap(rev.unchanged_before, rev.unchecked_before)

    rows: list[dict[str, object]] = []
    for it in items:
        if it["gap"]:
            if not (it["unchanged"] or it["unchecked"]):
                continue
            if rows and rows[-1]["gap"]:
                rows[-1]["unchanged"] += it["unchanged"]  # type: ignore[operator]
                rows[-1]["unchecked"] += it["unchecked"]  # type: ignore[operator]
                continue
        rows.append(dict(it))
    for r in rows:
        if r["gap"]:
            r["text"] = " · ".join(gap_texts(r["unchanged"], r["unchecked"]))  # type: ignore[arg-type]
    return rows, hidden


def create_app(
    root: Path | None = None,
    fetcher_factory: Callable[[], Fetcher] | None = None,
    start_worker: bool = True,
) -> FastAPI:
    root = root or config.data_dir()
    worker = jobs.Worker(root, fetcher_factory)

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        with Library(root) as lib:
            jobs.requeue_interrupted(lib.conn)
        task = asyncio.create_task(worker.run()) if start_worker else None
        yield
        worker.stop()
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    app = FastAPI(title="SEPDiff", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.root = root
    app.state.worker = worker
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.globals.update(
        KIND_LABEL=KIND_LABEL, KIND_MARK=KIND_MARK, KIND_HINT=KIND_HINT, TEXT_KINDS=TEXT_KINDS,
        stats_line=stats_line,
        htmx_src="/static/htmx.min.js" if (HERE / "static" / "htmx.min.js").exists() else HTMX_CDN,
        # Версия в URL стилей: иначе браузер держит старый CSS из кеша после обновления.
        css_version=int((HERE / "static" / "style.css").stat().st_mtime),
    )
    templates.env.filters["eta"] = eta_text

    def page(request: Request, name: str, status_code: int = 200, **ctx: object) -> HTMLResponse:
        return templates.TemplateResponse(request, name, ctx, status_code=status_code)

    def is_htmx(request: Request) -> bool:
        return request.headers.get("HX-Request") == "true"

    def same_origin(request: Request) -> None:
        # Сервер локальный, но чужая вкладка не должна ставить задачи через POST.
        origin = request.headers.get("origin")
        if origin and origin != f"{request.url.scheme}://{request.url.netloc}":
            raise HTTPException(403, "запрос с чужого сайта")

    @app.exception_handler(SepDiffError)
    async def _not_found(request: Request, exc: SepDiffError) -> HTMLResponse:
        return page(request, "error.html", status_code=404, message=str(exc))

    # ------------------------------------------------------------------
    # Поиск
    # ------------------------------------------------------------------

    def search_ctx(lib: Library, q: str) -> dict[str, object]:
        q = q.strip()
        found = lib.search(q) if q else []
        scanned = lib.scanned_slugs()
        results = [{"slug": s, "title": t, "scanned": s in scanned} for s, t in found]
        guess = q.lower()
        slug_guess = guess if SLUG_RE.fullmatch(guess) and guess not in {r["slug"] for r in results} else None
        return {"q": q, "results": results, "slug_guess": slug_guess, "seeded": lib.has_index(),
                "seed_job": jobs.active(lib.conn, jobs.SEED, "latest")}

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request, lib: Lib, q: str = "") -> HTMLResponse:
        return page(request, "index.html", recent=lib.recent_entries(), watched_list=lib.watched(),
                    **search_ctx(lib, q))

    @app.get("/feed.atom")
    def feed(request: Request, lib: Lib) -> Response:
        """Atom-лента правок отслеживаемых статей."""
        changes = lib.changes(limit=50)
        base = str(request.base_url).rstrip("/")
        updated = rfc3339(changes[0].when) if changes else rfc3339(date.today().isoformat())
        return Response(atom(changes, base, updated), media_type="application/atom+xml")

    @app.get("/search", response_class=HTMLResponse)
    def search(request: Request, lib: Lib, q: str = "") -> Response:
        if not is_htmx(request):
            return RedirectResponse("/?" + urlencode({"q": q}), status_code=303)
        return page(request, "_results.html", **search_ctx(lib, q))

    @app.post("/seed")
    async def seed(request: Request) -> Response:
        same_origin(request)
        job = await asyncio.to_thread(_enqueue, root, jobs.SEED, "latest")
        worker.notify()
        if not is_htmx(request):
            return RedirectResponse("/", status_code=303)
        return page(request, "_progress.html", job=job, position=0)

    # ------------------------------------------------------------------
    # Статья: история и сканирование
    # ------------------------------------------------------------------

    def entry_ctx(lib: Library, slug: str, show_all: bool) -> dict[str, object]:
        check_slug(slug)
        try:
            hist: History | None = lib.history(slug)
        except SepDiffError:
            hist = None   # ещё не сканировали
        rows, hidden = history_rows(hist, show_all) if hist else ([], 0)
        job = jobs.active(lib.conn, jobs.SCAN, slug)
        return {
            "slug": slug, "title": (hist.title if hist else None) or lib.entry_title(slug),
            "hist": hist, "rows": rows, "hidden": hidden, "show_all": show_all,
            "job": job, "position": jobs.position(lib.conn, job) if job else 0,
            "watched": lib.is_watched(slug),
            "last_job": jobs.latest(lib.conn, jobs.SCAN, slug),
        }

    @app.get("/e/{slug}", response_class=HTMLResponse)
    def entry(request: Request, slug: str, lib: Lib, all: bool = False) -> HTMLResponse:  # noqa: A002
        return page(request, "entry.html", **entry_ctx(lib, slug, all))

    @app.get("/e/{slug}/history", response_class=HTMLResponse)
    def entry_history(request: Request, slug: str, lib: Lib, all: bool = False) -> HTMLResponse:  # noqa: A002
        return page(request, "_history.html", **entry_ctx(lib, slug, all))

    @app.post("/e/{slug}/scan")
    async def scan(request: Request, slug: str) -> Response:
        same_origin(request)
        check_slug(slug)
        job = await asyncio.to_thread(_enqueue, root, jobs.SCAN, slug)
        worker.notify()
        if not is_htmx(request):
            return RedirectResponse(f"/e/{slug}", status_code=303)
        position = await asyncio.to_thread(_position, root, job)
        return page(request, "_progress.html", job=job, position=position)

    @app.post("/e/{slug}/watch")
    async def watch(request: Request, slug: str, on: bool = True) -> Response:
        same_origin(request)
        check_slug(slug)
        await asyncio.to_thread(_set_watched, root, slug, on)
        if not is_htmx(request):
            return RedirectResponse(f"/e/{slug}", status_code=303)
        return page(request, "_watch.html", slug=slug, watched=on)

    @app.get("/jobs/{job_id}/progress", response_class=HTMLResponse)
    def progress(request: Request, job_id: int, lib: Lib) -> HTMLResponse:
        job = jobs.get(lib.conn, job_id)
        if job is None:
            raise HTTPException(404, "нет такой задачи")
        response = page(request, "_progress.html", job=job, position=jobs.position(lib.conn, job))
        if job.state == "done":
            response.headers["HX-Refresh"] = "true"   # история/оглавление готовы — перерисовать страницу
        return response

    # ------------------------------------------------------------------
    # Diff и текст версии
    # ------------------------------------------------------------------

    def diff_ctx(lib: Library, slug: str, a: str | None, b: str | None) -> dict[str, object]:
        check_slug(slug)
        a, b = a or None, b or None
        if a is None and b is None:
            hist = lib.history(slug)
            candidates = [r for r in hist.revisions if r.kind in TEXT_KINDS and r.prev_edition]
            if candidates:
                b = candidates[-1].edition.slug
        d: PairDiff | None = None
        error: str | None = None
        try:
            if a and b:
                d = lib.diff(slug, a, b)
            elif b:
                d = lib.diff(slug, b)
            elif a:
                error = "выберите, с каким изданием сравнивать"
            else:
                error = "у статьи нет ревизий с правками текста"
        except SepDiffError as exc:
            error = str(exc)
        ctx: dict[str, object] = {"slug": slug, "d": d, "error": error,
                                  "a": d.a.slug if d else a, "b": d.b.slug if d else b,
                                  "title": (d.title if d else None) or lib.entry_title(slug) or slug}
        if d is not None:
            ctx.update(
                body_html=ops_html(d.body), biblio_html=ops_html(d.biblio, 0), apparatus_html=ops_html(d.apparatus, 0),
                body_changed=changed(d.body), biblio_changed=changed(d.biblio),
                apparatus_changed=changed(d.apparatus))
        return ctx

    @app.get("/e/{slug}/diff", response_class=HTMLResponse)
    def diff_page(request: Request, slug: str, lib: Lib, a: str | None = None, b: str | None = None) -> HTMLResponse:
        ctx = diff_ctx(lib, slug, a, b)
        editions = list(reversed(lib.snapshot_editions(slug)))
        if not editions:
            raise SepDiffError(f"у статьи {slug} нет скачанных снимков")
        return page(request, "diff.html", editions=editions, **ctx)

    @app.get("/e/{slug}/diff/pane", response_class=HTMLResponse)
    def diff_pane(request: Request, slug: str, lib: Lib, a: str | None = None, b: str | None = None) -> HTMLResponse:
        ctx = diff_ctx(lib, slug, a, b)
        response = page(request, "_pane.html", **ctx)
        response.headers["HX-Push-Url"] = f"/e/{slug}/diff?" + urlencode({"a": a or "", "b": b or ""})
        return response

    @app.get("/e/{slug}/v/{edition}", response_class=HTMLResponse)
    def version(request: Request, slug: str, edition: str, lib: Lib) -> HTMLResponse:
        doc = lib.show(slug, edition)
        return page(request, "version.html", slug=slug, edition=edition, doc=doc)

    return app


def _set_watched(root: Path, slug: str, on: bool) -> None:
    with Library(root) as lib:
        lib.set_watched(slug, on)


def _enqueue(root: Path, kind: str, target: str) -> jobs.Job:
    with Library(root) as lib:
        return jobs.enqueue(lib.conn, kind, target)


def _position(root: Path, job: jobs.Job) -> int:
    with Library(root) as lib:
        return jobs.position(lib.conn, job)
