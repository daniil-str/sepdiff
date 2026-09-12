"""Прикладная логика: сканирование статьи, история ревизий, diff.

Общая для CLI и (на этапе 2) веба. Ядро не знает, кто его вызывает: прогресс
отдаётся через колбэк, ошибки для пользователя — исключением SepDiffError.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field, fields
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from selectolax.lexbor import LexborHTMLParser

from . import config, db
from .blobs import BlobStore
from .diffing import Op, PairStats, block_diff, classify, pair_stats
from .editions import LIVE, Edition, parse_index
from .extract import EXTRACT_VERSION, Doc, extract
from .fetcher import Fetcher, FetchError
from .normalize import normalize

SLUG_RE = re.compile(r"[a-z0-9][a-z0-9.-]*")
_IMPORT_RE = re.compile(r"([a-z0-9][a-z0-9.-]*)\.((?:spr|sum|fall|win)\d{4})\.(html|404)")
_NOT_ENTRIES = {"contents", "archives", "report"}   # служебные файлы в кеше спайка
COARSE_STEP = 8   # грубый проход: каждое 8-е издание, примерно раз в два года
LIVE_MAX_AGE = timedelta(hours=24)   # текущую версию на сайте спрашиваем не чаще раза в сутки


class SepDiffError(Exception):
    """Ошибка, которую интерфейс показывает пользователю как есть."""


@dataclass
class ScanEvent:
    # probe | coarse | refine | deep | fetch (издания из -e) — скачан снимок;
    # plan — известно, сколько качать (total); partial — история пересчитана
    phase: str
    edition: str | None = None
    status: int | None = None
    done: int = 0
    total: int | None = None


Progress = Callable[[ScanEvent], None]


@dataclass
class Snap:
    """Строка таблицы snapshots."""
    entry_slug: str
    edition_slug: str
    http_status: int
    blob_sha: str | None = None
    raw_sha: str | None = None
    text_sha: str | None = None
    body_sha: str | None = None
    biblio_sha: str | None = None
    apparatus_sha: str | None = None
    struct_sha: str | None = None
    links_sha: str | None = None
    title: str | None = None
    revision_date: str | None = None
    date_source: str | None = None
    word_count: int | None = None
    coverage: float | None = None
    extractor: str | None = None
    extract_version: int | None = None
    suspect: int = 0
    fetched_at: str = ""


_SNAP_FIELDS = [f.name for f in fields(Snap)]


def _snap(row: sqlite3.Row) -> Snap:
    return Snap(**{k: row[k] for k in _SNAP_FIELDS})


@dataclass
class Revision:
    edition: Edition
    prev_edition: str | None
    kind: str
    stats: PairStats
    revision_date: str | None
    suspect: bool
    unchanged_before: int = 0   # скачанные издания без изменений между предыдущей ревизией и этой
    unchecked_before: int = 0   # нескачанные издания в том же промежутке


@dataclass
class LiveRevision:
    """Текущая версия статьи на сайте относительно последнего скачанного издания."""
    base: Edition | None       # с чем сравниваем; None — архивных снимков нет
    kind: str                  # как у classify; removed — на сайте статьи нет (404)
    stats: PairStats
    revision_date: str | None
    checked_at: str            # когда последний раз спрашивали сайт
    # сохранённая копия сайта старше последнего скачанного издания: сравнивать
    # их бессмысленно (покажет «правку назад»), нужно спросить сайт заново
    stale: bool = False


@dataclass
class Change:
    """Правка для ленты: ревизия издания или правка на сайте, ещё не в архиве."""
    slug: str
    title: str
    edition: Edition
    kind: str
    stats: PairStats
    when: str                   # дата выхода издания или момент, когда заметили правку
    prev_edition: str | None


@dataclass
class History:
    slug: str
    title: str | None
    revisions: list[Revision]              # по порядку выхода изданий
    unchanged_after: int = 0               # после последней ревизии до последнего издания
    unchecked_after: int = 0
    snapshots: int = 0
    editions: int = 0
    live: LiveRevision | None = None       # текущая версия на сайте, если скачана


@dataclass
class PairDiff:
    slug: str
    title: str
    a: Edition
    b: Edition
    kind: str
    stats: PairStats
    body: list[Op] = field(default_factory=list)
    biblio: list[Op] = field(default_factory=list)
    apparatus: list[Op] = field(default_factory=list)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _doc_fields(doc: Doc) -> dict[str, object]:
    return {
        "raw_sha": doc.raw_sha, "text_sha": doc.text_sha, "body_sha": doc.body_sha,
        "biblio_sha": doc.biblio_sha, "apparatus_sha": doc.apparatus_sha,
        "struct_sha": doc.struct_sha, "links_sha": doc.links_sha, "title": doc.title or None,
        "revision_date": doc.revision_date, "date_source": doc.date_source,
        "word_count": doc.word_count, "coverage": doc.coverage,
        "extractor": doc.extractor, "extract_version": EXTRACT_VERSION,
    }


def check_slug(slug: str) -> str:
    if not SLUG_RE.fullmatch(slug):
        raise SepDiffError(f"не похоже на slug статьи SEP: {slug!r} (пример: kant, logic-modal)")
    return slug


class Library:
    """База снимков и ревизий в каталоге данных (по умолчанию ./data)."""

    def __init__(self, root: Path | None = None, fetcher: Fetcher | None = None) -> None:
        self.root = root or config.data_dir()
        self.root.mkdir(parents=True, exist_ok=True)
        self.conn = db.connect(self.root / "sepdiff.db")
        self.blobs = BlobStore(self.root / "blobs")
        self._fetcher = fetcher
        self._own_fetcher = fetcher is None
        self._docs: dict[str, Doc] = {}
        self._editions: list[Edition] | None = None
        self._editions_checked = False

    @property
    def fetcher(self) -> Fetcher:
        if self._fetcher is None:
            self._fetcher = Fetcher(state_file=self.root / ".last_request")
        return self._fetcher

    def close(self) -> None:
        self.conn.close()
        if self._own_fetcher and self._fetcher is not None:
            self._fetcher.close()

    def __enter__(self) -> Library:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Издания и оглавление
    # ------------------------------------------------------------------

    def init_editions(self) -> list[Edition]:
        """Загрузить список изданий со страницы /archives/ (1 запрос)."""
        r = self.fetcher.get("/archives/")
        if r.status != 200:
            raise FetchError(f"/archives/: HTTP {r.status}")
        eds = parse_index(r.text)
        if not eds:
            raise SepDiffError("на странице /archives/ не нашлось ни одного издания — вёрстка изменилась?")
        with self.conn:
            self.conn.executemany(
                "INSERT INTO editions (slug, season, year, released_on, ordinal) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(slug) DO UPDATE SET ordinal = excluded.ordinal",
                [(e.slug, e.season, e.year, e.released_on.isoformat(), i) for i, e in enumerate(eds)],
            )
        self._editions = None
        self._editions_checked = True
        return eds

    def ensure_editions(self) -> list[Edition]:
        """Список изданий; загрузить, если его нет, и обновить, если пора выйти новому (раз в квартал)."""
        try:
            eds = self.editions()
        except SepDiffError:
            self.init_editions()
            return self.editions()
        if not self._editions_checked and (date.today() - eds[-1].released_on).days > 92:
            self.init_editions()
            eds = self.editions()
        return eds

    def editions(self) -> list[Edition]:
        if self._editions is None:
            rows = self.conn.execute("SELECT slug FROM editions ORDER BY ordinal").fetchall()
            if not rows:
                raise SepDiffError("список изданий пуст — сначала выполните: sepdiff init")
            self._editions = [Edition.parse(r["slug"]) for r in rows]
        return self._editions

    def _edition(self, slug: str) -> Edition:
        try:
            ed = Edition.parse(slug)
        except ValueError as exc:
            raise SepDiffError(str(exc)) from None
        if ed not in self.editions():
            raise SepDiffError(f"издания {slug} нет в списке (обновить список: sepdiff init)")
        return ed

    def seed(self) -> int:
        """Оглавление последнего издания -> таблица entries (1 запрос). Возвращает число статей."""
        latest = self.ensure_editions()[-1]
        r = self.fetcher.get(f"/archives/{latest.slug}/contents.html")
        if r.status != 200:
            raise FetchError(f"оглавление {latest.slug}: HTTP {r.status}")
        found: dict[str, str] = {}
        for a in LexborHTMLParser(r.text).css("a[href]"):
            m = re.fullmatch(r"(?:\.\./)*entries/([a-z0-9][a-z0-9.-]*)/?", a.attributes.get("href") or "")
            if m and m.group(1) not in found:
                found[m.group(1)] = normalize(a.text(separator=" "))
        with self.conn:
            self.conn.executemany(
                "INSERT INTO entries (slug, title) VALUES (?, ?) "
                "ON CONFLICT(slug) DO UPDATE SET title = COALESCE(entries.title, excluded.title)",
                list(found.items()),
            )
            self.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('seeded_at', ?)", (_now(),))
        return len(found)

    def has_index(self) -> bool:
        """Загружено ли оглавление SEP (sepdiff seed) — без него поиск только по скачанным статьям."""
        return self.conn.execute("SELECT 1 FROM meta WHERE key = 'seeded_at'").fetchone() is not None

    def search(self, query: str, limit: int = 20) -> list[tuple[str, str | None]]:
        like = f"%{query.lower()}%"
        rows = self.conn.execute(
            "SELECT slug, title FROM entries WHERE slug LIKE ? OR lower(title) LIKE ? "
            "ORDER BY slug NOT LIKE ?, slug LIMIT ?",
            (like, like, f"{query.lower()}%", limit),
        ).fetchall()
        return [(r["slug"], r["title"]) for r in rows]

    def scanned_slugs(self) -> set[str]:
        return {r[0] for r in self.conn.execute("SELECT DISTINCT entry_slug FROM snapshots")}

    def entry_title(self, slug: str) -> str | None:
        row = self.conn.execute("SELECT title FROM entries WHERE slug = ?", (slug,)).fetchone()
        return row["title"] if row else None

    def recent_entries(self, limit: int = 20) -> list[dict[str, object]]:
        """Статьи со скачанной историей, последние отсканированные сверху."""
        rows = self.conn.execute(
            "SELECT e.slug, e.title, e.last_scanned_at, e.scan_state, "
            "(SELECT count(*) FROM revisions r WHERE r.entry_slug = e.slug "
            " AND r.kind IN ('substantive', 'minor', 'changed')) AS revisions "
            "FROM entries e WHERE EXISTS (SELECT 1 FROM snapshots s WHERE s.entry_slug = e.slug) "
            "ORDER BY e.last_scanned_at DESC, e.slug LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Отслеживаемые статьи и лента правок
    # ------------------------------------------------------------------

    def set_watched(self, slug: str, on: bool = True) -> None:
        self._ensure_entry(slug)
        with self.conn:
            self.conn.execute("UPDATE entries SET watched = ? WHERE slug = ?", (int(on), slug))

    def is_watched(self, slug: str) -> bool:
        row = self.conn.execute("SELECT watched FROM entries WHERE slug = ?", (slug,)).fetchone()
        return bool(row and row["watched"])

    def watched(self) -> list[dict[str, object]]:
        rows = self.conn.execute(
            "SELECT e.slug, e.title, e.last_scanned_at, e.scan_state, "
            "(SELECT checked_at FROM live l WHERE l.entry_slug = e.slug) AS live_checked_at "
            "FROM entries e WHERE e.watched = 1 ORDER BY e.slug").fetchall()
        return [dict(r) for r in rows]

    def _live_changes(self, slugs: list[str]) -> list[Change]:
        out = []
        for slug in slugs:
            live = self.live_revision(slug)
            if live is not None and not live.stale and live.kind not in ("identical", "markup_only"):
                out.append(Change(slug, self.entry_title(slug) or slug, Edition.parse(LIVE), live.kind,
                                  live.stats, live.checked_at, live.base.slug if live.base else None))
        return out

    def changes(self, limit: int = 50, watched_only: bool = True) -> list[Change]:
        """Последние правки: ревизии изданий плюс правки на сайте, которых ещё нет в архиве."""
        cond = "e.watched = 1 AND " if watched_only else ""
        rows = self.conn.execute(
            f"SELECT r.*, e.title, ed.released_on FROM revisions r "
            f"JOIN entries e ON e.slug = r.entry_slug "
            f"JOIN editions ed ON ed.slug = r.edition_slug "
            f"WHERE {cond}r.kind != 'markup_only' "
            f"ORDER BY ed.released_on DESC, r.entry_slug LIMIT ?", (limit,)).fetchall()
        out = [Change(r["entry_slug"], r["title"] or r["entry_slug"], Edition.parse(r["edition_slug"]), r["kind"],
                      PairStats(r["words_added"], r["words_removed"], r["blocks_changed"],
                                json.loads(r["sections_touched"]), r["biblio_added"], r["biblio_removed"],
                                r["biblio_modified"], r["apparatus_changed"]),
                      r["released_on"], r["prev_edition"]) for r in rows]
        slugs = [str(w["slug"]) for w in self.watched()] if watched_only else sorted(self.scanned_slugs())
        out += self._live_changes(slugs)
        out.sort(key=lambda c: c.when, reverse=True)
        return out[:limit]

    def watch_tick(self, progress: Progress | None = None) -> list[Change]:
        """Обойти отслеживаемые статьи: новые издания и текущая версия на сайте.

        Возвращает найденные правки. Запросы те же, раз в 5 с: у отслеживаемой
        статьи это один запрос в сутки на текущую версию плюс по одному на
        каждое вышедшее издание.
        """
        watched = [str(w["slug"]) for w in self.watched()]
        if not watched:
            return []
        eds = self.ensure_editions()
        found: list[Change] = []
        for slug in watched:
            known = self._statuses(slug)
            state = self.conn.execute("SELECT scan_state FROM entries WHERE slug = ?", (slug,)).fetchone()
            missing = [e.slug for e in eds if e.slug not in known][-4:]
            new_edition = bool(missing) and bool(state) and state["scan_state"] == "complete"
            if new_edition:
                # у полностью просканированной статьи это издания, вышедшие с тех пор
                self.scan(slug, only=missing, progress=progress, live=False)
                found += [c for c in self.changes(limit=len(missing) + 4, watched_only=False)
                          if c.slug == slug and c.edition.slug in missing]
            before = self.live_revision(slug)
            # после нового издания сохранённая копия сайта устарела — спрашиваем заново
            if self.refresh_live(slug, force=new_edition) in ("updated", "gone"):
                after = self.live_revision(slug)
                if after is not None and after.kind not in ("identical", "markup_only") and (
                        before is None or (before.kind, before.stats) != (after.kind, after.stats)):
                    found += self._live_changes([slug])
        return found

    def snapshot_editions(self, slug: str) -> list[Edition]:
        """Издания, в которых статья скачана и есть (HTTP 200), по порядку выхода."""
        have = {r[0] for r in self.conn.execute(
            "SELECT edition_slug FROM snapshots WHERE entry_slug = ? AND http_status = 200", (slug,))}
        out = [e for e in self.editions() if e.slug in have]
        live = self._live_row(slug)
        if live is not None and live["http_status"] == 200:
            out.append(Edition.parse(LIVE))
        return out

    # ------------------------------------------------------------------
    # Сканирование
    # ------------------------------------------------------------------

    def _ensure_entry(self, slug: str) -> None:
        with self.conn:
            self.conn.execute("INSERT OR IGNORE INTO entries (slug) VALUES (?)", (check_slug(slug),))

    def _statuses(self, slug: str) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT edition_slug, http_status FROM snapshots WHERE entry_slug = ?", (slug,))
        return {r["edition_slug"]: r["http_status"] for r in rows}

    def _store(self, slug: str, edition: str, status: int, raw: bytes | None) -> None:
        row: dict[str, object] = {"entry_slug": slug, "edition_slug": edition,
                                  "http_status": status, "fetched_at": _now()}
        if raw is not None:
            blob = self.blobs.put(raw)
            doc = extract(raw)
            self._docs[blob] = doc
            row |= {"blob_sha": blob, **_doc_fields(doc)}
        cols = ", ".join(row)
        marks = ", ".join("?" * len(row))
        with self.conn:
            self.conn.execute(f"INSERT OR REPLACE INTO snapshots ({cols}) VALUES ({marks})", list(row.values()))

    def _fetch_snapshot(self, slug: str, edition: str) -> int:
        r = self.fetcher.get(f"/archives/{edition}/entries/{slug}/")
        if r.status == 404:
            self._store(slug, edition, 404, None)   # не ошибка: статьи ещё (или уже) нет
        elif r.status == 200:
            self._store(slug, edition, 200, r.content)
        else:
            raise FetchError(f"{r.url}: HTTP {r.status}")
        return r.status

    def scan(self, slug: str, only: list[str] | None = None, progress: Progress | None = None,
             deep: bool = True, live: bool = True) -> int:
        """Скачать недостающие снимки статьи и пересчитать историю. Возвращает число запросов.

        Без `only` — по фазам (docs/journal.md §16); история пересчитывается после
        каждой, так что её можно показывать, не дожидаясь конца:

          probe   последнее издание (заодно проверка slug) и бинарный поиск
                  первого издания со статьёй (~7 запросов вместо десятков 404);
          coarse  каждое COARSE_STEP-е издание — скелет истории за ~минуту;
          refine  между соседними скачанными снимками с разным содержимым —
                  бинарный поиск издания, где оно поменялось, для каждой правки;
          deep    всё остальное: проверка, что между одинаковыми снимками правок
                  не было (правка и откат). deep=False — пропустить.

        Предполагается, что статья, раз появившись, из архива не пропадает;
        пропуски всё равно видны как 404 в deep-фазе.
        """
        report = progress or (lambda _e: None)
        self._ensure_entry(slug)
        eds = self.ensure_editions()
        known = self._statuses(slug)
        requests = done = 0
        total: int | None = None
        rebuilt_at = -1

        def fetch(ed: Edition, phase: str) -> int:
            nonlocal requests, done
            if ed.slug not in known:
                known[ed.slug] = self._fetch_snapshot(slug, ed.slug)
                requests += 1
                done += total is not None
                report(ScanEvent(phase, ed.slug, known[ed.slug], done, total))
            return known[ed.slug]

        def checkpoint() -> None:
            nonlocal rebuilt_at
            if requests != rebuilt_at:
                self.rebuild(slug)
                rebuilt_at = requests
                report(ScanEvent("partial"))

        if only:
            targets = [self._edition(s) for s in only]
            total = sum(1 for e in targets if e.slug not in known)
            report(ScanEvent("plan", total=total))
            for ed in targets:
                fetch(ed, "fetch")
        else:
            first, last = self._locate(eds, lambda ed: fetch(ed, "probe"))
            if first is None:
                raise SepDiffError(f"статьи {slug!r} нет ни в одном из проверенных изданий — опечатка в slug? "
                                   "(поиск: sepdiff search)")
            # статья удалена или переименована (SEP не делает редиректов) — берём и
            # следующее издание с 404, чтобы в истории была ревизия «удалена»
            span = eds[first:min(last + 2, len(eds))]
            total = sum(1 for e in span if e.slug not in known)   # при deep=False — верхняя граница
            report(ScanEvent("plan", total=total))
            for ed in span[::COARSE_STEP]:
                fetch(ed, "coarse")
            self._mark_scanned(slug, "partial")
            checkpoint()
            self._refine(slug, span, known, fetch)
            checkpoint()
            if deep:
                for ed in span:
                    fetch(ed, "deep")

        self._mark_scanned(slug, "complete" if deep and not only else "partial")
        self.rebuild(slug)
        if live and not only:
            # плюс текущая версия на сайте — правки, которых ещё нет в архиве (раз в сутки)
            requests += self.refresh_live(slug) != "cached"
        return requests

    @staticmethod
    def _locate(eds: list[Edition], status: Callable[[Edition], int]) -> tuple[int | None, int]:
        """(первое, последнее) издание со статьёй — бинарным поиском, ~2·log2(N) запросов.

        Обычно статья есть в последнем издании. Если нет (удалена или
        переименована — SEP не делает редиректов), идём назад шагом COARSE_STEP,
        пока не найдём издание со статьёй. Считается, что статья присутствует
        в архиве одним непрерывным отрезком изданий.
        """
        n = len(eds)
        found = next((i for i in range(n - 1, -1, -COARSE_STEP) if status(eds[i]) == 200), None)
        if found is None:
            return None, -1
        lo, hi = 0, found                     # первое издание со статьёй
        while lo < hi:
            mid = (lo + hi) // 2
            if status(eds[mid]) == 200:
                hi = mid
            else:
                lo = mid + 1
        first = lo
        lo, hi = found, n - 1                 # последнее
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if status(eds[mid]) == 200:
                lo = mid
            else:
                hi = mid - 1
        return first, lo

    def _mark_scanned(self, slug: str, state: str) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE entries SET scan_state = CASE WHEN scan_state = 'complete' THEN 'complete' ELSE ? END, "
                "last_scanned_at = ? WHERE slug = ?", (state, _now(), slug))

    def _signature(self, slug: str, edition: str) -> tuple[object, ...]:
        """Всё, по чему classify отличает снимки, кроме сырого HTML (вёрстку сайта не ищем)."""
        row = self.conn.execute(
            "SELECT http_status, text_sha, apparatus_sha, struct_sha, links_sha, revision_date FROM snapshots "
            "WHERE entry_slug = ? AND edition_slug = ?", (slug, edition)).fetchone()
        return tuple(row)

    def _refine(self, slug: str, span: list[Edition], known: dict[str, int],
                fetch: Callable[[Edition, str], int]) -> None:
        """Между соседними скачанными снимками с разным содержимым — бинарный поиск точек правок."""
        have = sorted(i for i, e in enumerate(span) if e.slug in known)
        stack = list(reversed(list(zip(have, have[1:], strict=False))))
        while stack:
            i, j = stack.pop()
            if j - i < 2 or self._signature(slug, span[i].slug) == self._signature(slug, span[j].slug):
                continue
            m = (i + j) // 2
            fetch(span[m], "refine")
            stack += [(m, j), (i, m)]   # сначала левая половина — правки находятся по порядку

    def import_dir(self, directory: Path) -> dict[str, int]:
        """Импорт файлов <slug>.<edition>.html / .404 (например, кеш спайка) без сети."""
        known = {e.slug for e in self.editions()}
        counts: dict[str, int] = {}
        for path in sorted(directory.iterdir()):
            m = _IMPORT_RE.fullmatch(path.name)
            if not m or m.group(2) not in known or m.group(1) in _NOT_ENTRIES:
                continue
            slug, edition, ext = m.groups()
            self._ensure_entry(slug)
            if ext == "404":
                self._store(slug, edition, 404, None)
            else:
                self._store(slug, edition, 200, path.read_bytes())
            counts[slug] = counts.get(slug, 0) + 1
        for slug in counts:
            self.rebuild(slug)
        return counts

    # ------------------------------------------------------------------
    # История
    # ------------------------------------------------------------------

    def _doc(self, blob_sha: str) -> Doc:
        doc = self._docs.get(blob_sha)
        if doc is None:
            doc = self._docs[blob_sha] = extract(self.blobs.get(blob_sha))
        return doc

    def _snaps(self, slug: str) -> list[Snap]:
        order = {e.slug: i for i, e in enumerate(self.editions())}
        rows = self.conn.execute("SELECT * FROM snapshots WHERE entry_slug = ?", (slug,)).fetchall()
        return sorted((_snap(r) for r in rows), key=lambda s: order[s.edition_slug])

    def _pair_stats(self, a: Snap, b: Snap) -> PairStats:
        da, db_ = self._doc(a.blob_sha), self._doc(b.blob_sha)  # type: ignore[arg-type]
        return pair_stats(block_diff(da.body, db_.body), block_diff(da.biblio, db_.biblio),
                          block_diff(da.apparatus, db_.apparatus))

    def rebuild(self, slug: str) -> None:
        """Пересчитать ревизии статьи по снимкам (и переизвлечь снимки старой версии экстрактора)."""
        snaps = self._snaps(slug)
        for s in snaps:
            if s.http_status == 200 and s.extract_version != EXTRACT_VERSION:
                self._docs.pop(s.blob_sha, None)  # type: ignore[arg-type]
                values = _doc_fields(self._doc(s.blob_sha))  # type: ignore[arg-type]
                for k, v in values.items():
                    setattr(s, k, v)
                sets = ", ".join(f"{k} = ?" for k in values)
                self.conn.execute(f"UPDATE snapshots SET {sets} WHERE entry_slug = ? AND edition_slug = ?",
                                  [*values.values(), slug, s.edition_slug])

        revisions: list[tuple[str, str | None, str, PairStats]] = []
        suspects: list[tuple[int, str]] = []
        prev: Snap | None = None
        prev_status: int | None = None
        for s in snaps:
            if s.http_status != 200:
                if prev is not None and prev_status == 200:
                    revisions.append((s.edition_slug, prev.edition_slug, "removed", PairStats()))
                prev_status = s.http_status
                continue
            suspect = False
            if prev is None or prev_status != 200:
                revisions.append((s.edition_slug, prev.edition_slug if prev else None, "created",
                                  PairStats(words_added=s.word_count or 0)))
            else:
                kind = classify(prev, s)
                if kind == "minor" and prev.text_sha == s.text_sha and prev.struct_sha == s.struct_sha:
                    # правка только в ссылках: «вычистили мёртвые» от правки по хешам не отличить
                    kind = classify(self._doc(prev.blob_sha), self._doc(s.blob_sha))  # type: ignore[arg-type]
                if kind != "identical":
                    stats = PairStats() if kind == "markup_only" else self._pair_stats(prev, s)
                    revisions.append((s.edition_slug, prev.edition_slug, kind, stats))
                if prev.word_count and s.word_count:
                    ratio = s.word_count / prev.word_count
                    suspect = ratio > 2 or ratio < 0.5
            suspects.append((int(suspect), s.edition_slug))
            prev, prev_status = s, 200

        with self.conn:
            self.conn.execute("DELETE FROM revisions WHERE entry_slug = ?", (slug,))
            self.conn.executemany(
                "INSERT INTO revisions (entry_slug, edition_slug, prev_edition, kind, words_added, words_removed, "
                "blocks_changed, biblio_added, biblio_removed, biblio_modified, apparatus_changed, sections_touched) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(slug, ed, prev_ed, kind, st.words_added, st.words_removed, st.blocks_changed,
                  st.biblio_added, st.biblio_removed, st.biblio_modified, st.apparatus_changed,
                  json.dumps(st.sections, ensure_ascii=False))
                 for ed, prev_ed, kind, st in revisions])
            self.conn.executemany("UPDATE snapshots SET suspect = ? WHERE entry_slug = ? AND edition_slug = ?",
                                  [(flag, slug, ed) for flag, ed in suspects])
            if prev is not None and prev.title:
                self.conn.execute("UPDATE entries SET title = ? WHERE slug = ?", (prev.title, slug))

    def _ensure_fresh(self, slug: str) -> None:
        stale = self.conn.execute(
            "SELECT 1 FROM snapshots WHERE entry_slug = ? AND http_status = 200 "
            "AND (extract_version IS NULL OR extract_version != ?) LIMIT 1", (slug, EXTRACT_VERSION)).fetchone()
        if stale:
            self.rebuild(slug)

    def history(self, slug: str) -> History:
        check_slug(slug)
        self._ensure_fresh(slug)
        eds = self.editions()
        order = {e.slug: i for i, e in enumerate(eds)}
        snaps = {s.edition_slug: s for s in self._snaps(slug)}
        if not snaps:
            raise SepDiffError(f"у статьи {slug!r} нет скачанных снимков — сначала: sepdiff fetch {slug}")
        title_row = self.conn.execute("SELECT title FROM entries WHERE slug = ?", (slug,)).fetchone()
        rows = sorted(self.conn.execute("SELECT * FROM revisions WHERE entry_slug = ?", (slug,)).fetchall(),
                      key=lambda r: order[r["edition_slug"]])

        def gap(lo: int, hi: int) -> tuple[int, int]:
            fetched = sum(1 for e in eds[lo:hi] if e.slug in snaps)
            return fetched, (hi - lo) - fetched

        revisions: list[Revision] = []
        prev_i: int | None = None
        for r in rows:
            i = order[r["edition_slug"]]
            unchanged, unchecked = gap(prev_i + 1, i) if prev_i is not None and r["kind"] != "created" else (0, 0)
            snap = snaps[r["edition_slug"]]
            revisions.append(Revision(
                edition=eds[i], prev_edition=r["prev_edition"], kind=r["kind"],
                stats=PairStats(r["words_added"], r["words_removed"], r["blocks_changed"],
                                json.loads(r["sections_touched"]), r["biblio_added"], r["biblio_removed"],
                                r["biblio_modified"], r["apparatus_changed"]),
                revision_date=snap.revision_date, suspect=bool(snap.suspect),
                unchanged_before=unchanged, unchecked_before=unchecked))
            prev_i = i
        hist = History(slug, title_row["title"] if title_row else None, revisions,
                       snapshots=sum(1 for s in snaps.values() if s.http_status == 200), editions=len(eds),
                       live=self.live_revision(slug))
        if prev_i is not None and revisions[-1].kind != "removed":
            hist.unchanged_after, hist.unchecked_after = gap(prev_i + 1, len(eds))
        return hist

    # ------------------------------------------------------------------
    # Текущая версия на сайте
    # ------------------------------------------------------------------

    def _live_row(self, slug: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM live WHERE entry_slug = ?", (slug,)).fetchone()

    def _last_archived(self, slug: str) -> Snap | None:
        return next((s for s in reversed(self._snaps(slug)) if s.http_status == 200), None)

    def refresh_live(self, slug: str, force: bool = False) -> str:
        """Скачать текущую версию статьи с сайта (/entries/<slug>/), не чаще раза в сутки.

        Условный запрос (If-None-Match / If-Modified-Since): если страница не
        менялась, сайт отвечает 304 без тела. Возвращает, что произошло:
        cached (спрашивали меньше суток назад) | not_modified | same | updated | gone.
        """
        check_slug(slug)
        self._ensure_entry(slug)
        row = self._live_row(slug)
        if row is not None and not force:
            if datetime.now(UTC) - datetime.fromisoformat(row["checked_at"]) < LIVE_MAX_AGE:
                return "cached"
        headers: dict[str, str] = {}
        if row is not None and row["http_status"] == 200:
            if row["etag"]:
                headers["If-None-Match"] = row["etag"]
            if row["last_modified"]:
                headers["If-Modified-Since"] = row["last_modified"]
        r = self.fetcher.get(f"/entries/{slug}/", headers=headers or None)
        stamp = _now()
        if r.status == 304:
            with self.conn:
                self.conn.execute("UPDATE live SET checked_at = ? WHERE entry_slug = ?", (stamp, slug))
            return "not_modified"
        if r.status not in (200, 404):
            raise FetchError(f"{r.url}: HTTP {r.status}")
        values: dict[str, object] = {
            "entry_slug": slug, "http_status": r.status, "etag": r.headers.get("etag"),
            "last_modified": r.headers.get("last-modified"), "fetched_at": stamp, "checked_at": stamp}
        outcome = "gone"
        if r.status == 200:
            blob = self.blobs.put(r.content)
            outcome = "same" if row is not None and row["blob_sha"] == blob else "updated"
            doc = extract(r.content)
            self._docs[blob] = doc
            values |= {"blob_sha": blob, **_doc_fields(doc)}
        cols = ", ".join(values)
        with self.conn:
            self.conn.execute(f"INSERT OR REPLACE INTO live ({cols}) VALUES ({', '.join('?' * len(values))})",
                              list(values.values()))
        return outcome

    def live_revision(self, slug: str) -> LiveRevision | None:
        """Чем текущая версия на сайте отличается от последнего скачанного издания."""
        row = self._live_row(slug)
        if row is None:
            return None
        base = self._last_archived(slug)
        base_ed = Edition.parse(base.edition_slug) if base else None
        if row["http_status"] != 200:
            return LiveRevision(base_ed, "removed", PairStats(), None, row["checked_at"]) if base else None
        live_doc = self._doc(row["blob_sha"])
        if base is None:
            return LiveRevision(None, "created", PairStats(words_added=live_doc.word_count),
                                live_doc.revision_date, row["checked_at"])
        stale = bool(base_ed and str(row["fetched_at"])[:10] < base_ed.released_on.isoformat())
        base_doc = self._doc(base.blob_sha)  # type: ignore[arg-type]
        kind = classify(base_doc, live_doc)
        stats = PairStats()
        if kind not in ("identical", "markup_only"):
            stats = pair_stats(block_diff(base_doc.body, live_doc.body), block_diff(base_doc.biblio, live_doc.biblio),
                               block_diff(base_doc.apparatus, live_doc.apparatus))
        return LiveRevision(base_ed, kind, stats, live_doc.revision_date, row["checked_at"], stale)

    # ------------------------------------------------------------------
    # Diff и просмотр
    # ------------------------------------------------------------------

    def _snapshot(self, slug: str, edition: str) -> Snap:
        if edition == LIVE:
            live = self._live_row(slug)
            if live is None:
                raise SepDiffError(f"текущая версия {slug} не скачана — скачать: sepdiff live {slug}")
            if live["http_status"] != 200:
                raise SepDiffError(f"на сайте статьи {slug} сейчас нет (HTTP {live['http_status']})")
            return Snap(edition_slug=LIVE, **{k: live[k] for k in _SNAP_FIELDS if k in live.keys()})
        self._edition(edition)
        row = self.conn.execute("SELECT * FROM snapshots WHERE entry_slug = ? AND edition_slug = ?",
                                (slug, edition)).fetchone()
        if row is None:
            raise SepDiffError(f"снимка {slug} в издании {edition} нет — скачать: sepdiff fetch {slug} -e {edition}")
        if row["http_status"] != 200:
            raise SepDiffError(f"в издании {edition} статьи {slug} нет (HTTP {row['http_status']})")
        return _snap(row)

    def diff(self, slug: str, a: str, b: str | None = None) -> PairDiff:
        """Diff двух изданий; с одним изданием — его ревизия относительно предыдущего снимка."""
        check_slug(slug)
        self._ensure_fresh(slug)
        if b is None and a == LIVE:
            base = self._last_archived(slug)
            if base is None:
                raise SepDiffError(f"у {slug} нет архивных снимков, сравнивать текущую версию не с чем")
            a, b = base.edition_slug, LIVE
        if b is None:
            b = a
            row = self.conn.execute("SELECT prev_edition FROM revisions WHERE entry_slug = ? AND edition_slug = ?",
                                    (slug, b)).fetchone()
            if row is None:
                raise SepDiffError(f"в {b} у {slug} нет изменений относительно предыдущего снимка "
                                   f"(ревизии: sepdiff log {slug})")
            if row["prev_edition"] is None:
                raise SepDiffError(f"{b} — первая версия статьи, сравнивать не с чем")
            a = row["prev_edition"]
        sa, sb = self._snapshot(slug, a), self._snapshot(slug, b)
        da, db_ = self._doc(sa.blob_sha), self._doc(sb.blob_sha)  # type: ignore[arg-type]
        body = block_diff(da.body, db_.body)
        biblio = block_diff(da.biblio, db_.biblio)
        apparatus = block_diff(da.apparatus, db_.apparatus)
        return PairDiff(slug, db_.title or slug, Edition.parse(a), Edition.parse(b), classify(da, db_),
                        pair_stats(body, biblio, apparatus), body, biblio, apparatus)

    def show(self, slug: str, edition: str) -> Doc:
        check_slug(slug)
        return self._doc(self._snapshot(slug, edition).blob_sha)  # type: ignore[arg-type]
