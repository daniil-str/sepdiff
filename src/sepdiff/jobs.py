"""Очередь фоновых задач в SQLite и один воркер (PLAN.md §4.3).

Никакого Celery: задачи — строки таблицы jobs, воркер — один asyncio.Task,
который по одной забирает задачи и выполняет их в потоке (Library.scan
синхронный и может идти минутами). Запросы к SEP всё равно нельзя
параллелить, так что одного воркера достаточно по определению.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass, fields
from datetime import UTC, datetime
from pathlib import Path

from .fetcher import FetchError, Fetcher
from .present import TEXT_KINDS
from .service import Library, ScanEvent, SepDiffError

log = logging.getLogger(__name__)

SECONDS_PER_REQUEST = 6   # 5 с паузы robots.txt + сам запрос
SCAN = "scan_entry"
SEED = "seed_index"


@dataclass
class Job:
    id: int
    kind: str
    target: str
    state: str           # queued | running | done | error
    done_units: int
    total_units: int | None
    message: str | None
    created_at: str
    updated_at: str

    @property
    def active(self) -> bool:
        return self.state in ("queued", "running")

    @property
    def eta_seconds(self) -> float | None:
        if self.state != "running" or not self.total_units:
            return None
        return (self.total_units - self.done_units) * SECONDS_PER_REQUEST


_FIELDS = [f.name for f in fields(Job)]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _job(row: sqlite3.Row | None) -> Job | None:
    return Job(**{k: row[k] for k in _FIELDS}) if row is not None else None


def get(conn: sqlite3.Connection, job_id: int) -> Job | None:
    return _job(conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone())


def latest(conn: sqlite3.Connection, kind: str, target: str) -> Job | None:
    return _job(conn.execute("SELECT * FROM jobs WHERE kind = ? AND target = ? ORDER BY id DESC LIMIT 1",
                             (kind, target)).fetchone())


def active(conn: sqlite3.Connection, kind: str, target: str) -> Job | None:
    job = latest(conn, kind, target)
    return job if job is not None and job.active else None


def enqueue(conn: sqlite3.Connection, kind: str, target: str) -> Job:
    """Поставить задачу; если такая уже ждёт или идёт — вернуть её."""
    with conn:
        existing = active(conn, kind, target)
        if existing is not None:
            return existing
        now = _now()
        cur = conn.execute("INSERT INTO jobs (kind, target, state, created_at, updated_at) "
                           "VALUES (?, ?, 'queued', ?, ?)", (kind, target, now, now))
    job = get(conn, cur.lastrowid)  # type: ignore[arg-type]
    assert job is not None
    return job


def position(conn: sqlite3.Connection, job: Job) -> int:
    """Сколько задач выполнится раньше этой."""
    if job.state != "queued":
        return 0
    # job может быть устаревшим снимком: воркер уже взял его в работу — себя не считаем.
    (n,) = conn.execute("SELECT count(*) FROM jobs WHERE id != ? AND (state = 'running' OR (state = 'queued' AND id < ?))",
                        (job.id, job.id)).fetchone()
    return n


def claim(conn: sqlite3.Connection) -> Job | None:
    with conn:
        row = conn.execute(
            "UPDATE jobs SET state = 'running', updated_at = ? WHERE id = "
            "(SELECT id FROM jobs WHERE state = 'queued' ORDER BY id LIMIT 1) RETURNING *", (_now(),)).fetchone()
    return _job(row)


def update(conn: sqlite3.Connection, job_id: int, **values: object) -> None:
    values["updated_at"] = _now()
    sets = ", ".join(f"{k} = ?" for k in values)
    with conn:
        conn.execute(f"UPDATE jobs SET {sets} WHERE id = ?", [*values.values(), job_id])


def requeue_interrupted(conn: sqlite3.Connection) -> None:
    """После перезапуска сервера недоделанные задачи продолжаются: скачанное уже в базе."""
    with conn:
        conn.execute("UPDATE jobs SET state = 'queued' WHERE state = 'running'")


class Cancelled(Exception):
    pass


class Worker:
    def __init__(self, root: Path, fetcher_factory: Callable[[], Fetcher] | None = None) -> None:
        self.root = root
        self.fetcher_factory = fetcher_factory
        self._stop = threading.Event()
        self._wake: asyncio.Event | None = None

    def notify(self) -> None:
        """Разбудить воркер (вызывать из event loop)."""
        if self._wake is not None:
            self._wake.set()

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        self._wake = asyncio.Event()
        try:
            while not self._stop.is_set():
                if await asyncio.to_thread(self.run_once):
                    continue
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=5)
                except TimeoutError:
                    pass
                self._wake.clear()
        finally:
            self._stop.set()   # идущее сканирование прервётся на следующем издании

    def run_once(self) -> bool:
        """Выполнить одну задачу из очереди. False — очередь пуста."""
        fetcher = self.fetcher_factory() if self.fetcher_factory else None
        with Library(self.root, fetcher=fetcher) as lib:
            job = claim(lib.conn)
            if job is None:
                return False
            try:
                message = self._execute(lib, job)
            except Cancelled:
                update(lib.conn, job.id, state="queued", message="прервано — продолжится при следующем запуске")
            except (SepDiffError, FetchError) as exc:
                update(lib.conn, job.id, state="error", message=str(exc))
            except Exception as exc:  # noqa: BLE001 — задача не должна ронять воркер
                log.exception("задача %s упала", job.id)
                update(lib.conn, job.id, state="error", message=f"внутренняя ошибка: {exc!r}")
            else:
                update(lib.conn, job.id, state="done", message=message)
            return True

    def _execute(self, lib: Library, job: Job) -> str:
        if job.kind == SEED:
            return f"в оглавлении {lib.seed()} статей"
        if job.kind != SCAN:
            raise SepDiffError(f"неизвестный вид задачи: {job.kind}")

        def progress(ev: ScanEvent) -> None:
            if self._stop.is_set():
                raise Cancelled
            if ev.phase == "probe":
                update(lib.conn, job.id, message=f"ищу первое издание со статьёй: {ev.edition}")
            elif ev.phase == "plan":
                update(lib.conn, job.id, done_units=0, total_units=ev.total or 0,
                       message=f"скачиваю изданий: {ev.total}" if ev.total else "всё уже скачано")
            else:
                update(lib.conn, job.id, done_units=ev.done, total_units=ev.total,
                       message=f"{ev.edition}: {'есть' if ev.status == 200 else 'статьи нет'}")

        requests = lib.scan(job.target, progress=progress)
        hist = lib.history(job.target)
        real = sum(1 for r in hist.revisions if r.kind in TEXT_KINDS)
        return f"запросов: {requests}, снимков: {hist.snapshots}, ревизий с правками текста: {real}"
