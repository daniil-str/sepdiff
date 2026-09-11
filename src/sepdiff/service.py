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
from datetime import UTC, datetime
from pathlib import Path

from selectolax.lexbor import LexborHTMLParser

from . import config, db
from .blobs import BlobStore
from .diffing import Op, PairStats, block_diff, classify, pair_stats
from .editions import Edition, parse_index
from .extract import EXTRACT_VERSION, Doc, extract
from .fetcher import FetchError, Fetcher
from .normalize import normalize

SLUG_RE = re.compile(r"[a-z0-9][a-z0-9.-]*")
_IMPORT_RE = re.compile(r"([a-z0-9][a-z0-9.-]*)\.((?:spr|sum|fall|win)\d{4})\.(html|404)")
_NOT_ENTRIES = {"contents", "archives", "report"}   # служебные файлы в кеше спайка


class SepDiffError(Exception):
    """Ошибка, которую интерфейс показывает пользователю как есть."""


@dataclass
class ScanEvent:
    phase: str               # probe (ищем первое издание) | plan | fetch
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
class History:
    slug: str
    title: str | None
    revisions: list[Revision]              # по порядку выхода изданий
    unchanged_after: int = 0               # после последней ревизии до последнего издания
    unchecked_after: int = 0
    snapshots: int = 0
    editions: int = 0


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
        "struct_sha": doc.struct_sha, "title": doc.title or None,
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
        latest = self.editions()[-1]
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
        return len(found)

    def search(self, query: str, limit: int = 20) -> list[tuple[str, str | None]]:
        like = f"%{query.lower()}%"
        rows = self.conn.execute(
            "SELECT slug, title FROM entries WHERE slug LIKE ? OR lower(title) LIKE ? ORDER BY slug LIMIT ?",
            (like, like, limit),
        ).fetchall()
        return [(r["slug"], r["title"]) for r in rows]

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

    def scan(self, slug: str, only: list[str] | None = None, progress: Progress | None = None) -> int:
        """Скачать недостающие снимки статьи и пересчитать историю. Возвращает число запросов.

        Без `only` — полное сканирование: сперва последнее издание, потом
        бинарный поиск первого издания со статьёй (~7 запросов вместо десятков
        404), потом всё от первого до последнего. Предполагается, что статья,
        раз появившись, из архива не пропадает; пропуски всё равно видны как 404.
        """
        report = progress or (lambda _e: None)
        self._ensure_entry(slug)
        eds = self.editions()
        known = self._statuses(slug)
        requests = 0

        def status(ed: Edition, event: ScanEvent) -> int:
            nonlocal requests
            if ed.slug not in known:
                known[ed.slug] = self._fetch_snapshot(slug, ed.slug)
                requests += 1
                event.edition, event.status = ed.slug, known[ed.slug]
                report(event)
            return known[ed.slug]

        if only:
            targets = [self._edition(s) for s in only]
        else:
            last = len(eds) - 1
            if status(eds[last], ScanEvent("probe")) != 200:
                raise SepDiffError(
                    f"статьи {slug!r} нет в последнем издании {eds[last].slug}. Опечатка в slug "
                    f"(поиск: sepdiff search) или статья удалена — тогда укажите издания явно: -e fall2010")
            lo, hi = 0, last
            while lo < hi:
                mid = (lo + hi) // 2
                if status(eds[mid], ScanEvent("probe")) == 200:
                    hi = mid
                else:
                    lo = mid + 1
            targets = eds[lo:]

        todo = [e for e in targets if e.slug not in known]
        report(ScanEvent("plan", total=len(todo)))
        for i, ed in enumerate(todo, 1):
            status(ed, ScanEvent("fetch", done=i, total=len(todo)))

        state = "partial" if only else "complete"
        with self.conn:
            self.conn.execute(
                "UPDATE entries SET scan_state = CASE WHEN scan_state = 'complete' THEN 'complete' ELSE ? END, "
                "last_scanned_at = ? WHERE slug = ?", (state, _now(), slug))
        self.rebuild(slug)
        return requests

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
                       snapshots=sum(1 for s in snaps.values() if s.http_status == 200), editions=len(eds))
        if prev_i is not None and revisions[-1].kind != "removed":
            hist.unchanged_after, hist.unchecked_after = gap(prev_i + 1, len(eds))
        return hist

    # ------------------------------------------------------------------
    # Diff и просмотр
    # ------------------------------------------------------------------

    def _snapshot(self, slug: str, edition: str) -> Snap:
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
        return PairDiff(slug, db_.title or slug, Edition.parse(a), Edition.parse(b), classify(sa, sb),
                        pair_stats(body, biblio, apparatus), body, biblio, apparatus)

    def show(self, slug: str, edition: str) -> Doc:
        check_slug(slug)
        return self._doc(self._snapshot(slug, edition).blob_sha)  # type: ignore[arg-type]
