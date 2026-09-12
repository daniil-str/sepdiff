"""SQLite: схема и подключение. Без ORM — запросов мало, схема простая."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 5

SCHEMA = """
-- квартальные издания SEP, по порядку выхода
CREATE TABLE editions (
    slug        TEXT PRIMARY KEY,       -- 'win2023'
    season      TEXT NOT NULL,          -- spr|sum|fall|win
    year        INTEGER NOT NULL,
    released_on TEXT NOT NULL,          -- 2023-12-21
    ordinal     INTEGER NOT NULL        -- 0..N, порядок по времени
);

CREATE TABLE entries (
    slug            TEXT PRIMARY KEY,   -- 'kant'
    title           TEXT,
    scan_state      TEXT NOT NULL DEFAULT 'new',   -- new|partial|complete
    last_scanned_at TEXT
);

-- один снимок статьи в одном издании; архивные издания иммутабельны
CREATE TABLE snapshots (
    entry_slug      TEXT NOT NULL REFERENCES entries(slug),
    edition_slug    TEXT NOT NULL REFERENCES editions(slug),
    http_status     INTEGER NOT NULL,   -- 200 | 404 (статьи ещё/уже нет)
    blob_sha        TEXT,               -- сырой HTML в data/blobs
    raw_sha         TEXT,
    text_sha        TEXT,               -- тело + библиография, нормализованные
    body_sha        TEXT,
    biblio_sha      TEXT,
    apparatus_sha   TEXT,               -- Other Internet Resources + Related Entries
    struct_sha      TEXT,               -- типы блоков без текста
    title           TEXT,
    revision_date   TEXT,               -- дата последней существенной правки (§1.2)
    date_source     TEXT,               -- pubinfo | header-substantive | header-revised | footer | pubinfo-first
    word_count      INTEGER,
    coverage        REAL,
    extractor       TEXT,               -- modern | aueditable | fallback-body
    extract_version INTEGER,
    suspect         INTEGER NOT NULL DEFAULT 0,   -- объём текста скачет > 2x: вероятно, ошибка экстрактора
    fetched_at      TEXT NOT NULL,
    PRIMARY KEY (entry_slug, edition_slug)
);

-- вычисляемая «история коммитов»: только издания, где что-то изменилось
CREATE TABLE revisions (
    entry_slug        TEXT NOT NULL REFERENCES entries(slug),
    edition_slug      TEXT NOT NULL REFERENCES editions(slug),   -- издание, В КОТОРОМ появилось изменение
    prev_edition      TEXT REFERENCES editions(slug),            -- предыдущий скачанный снимок
    kind              TEXT NOT NULL,    -- created|substantive|minor|changed|markup_only|removed
    words_added       INTEGER NOT NULL DEFAULT 0,
    words_removed     INTEGER NOT NULL DEFAULT 0,
    blocks_changed    INTEGER NOT NULL DEFAULT 0,
    biblio_added      INTEGER NOT NULL DEFAULT 0,
    biblio_removed    INTEGER NOT NULL DEFAULT 0,
    biblio_modified   INTEGER NOT NULL DEFAULT 0,
    apparatus_changed INTEGER NOT NULL DEFAULT 0,
    sections_touched  TEXT NOT NULL DEFAULT '[]',   -- JSON-список заголовков
    PRIMARY KEY (entry_slug, edition_slug)
);

-- фоновые задачи веб-интерфейса (этап 2)
CREATE TABLE jobs (
    id          INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL,          -- scan_entry | seed_index
    target      TEXT NOT NULL,
    state       TEXT NOT NULL,          -- queued|running|done|error
    done_units  INTEGER NOT NULL DEFAULT 0,
    total_units INTEGER,
    message     TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
"""


# Миграция на версию N — скрипт MIGRATIONS[N]; SCHEMA — версия 1.
MIGRATIONS = {
    2: "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);",   # seeded_at и т.п.
    3: "ALTER TABLE snapshots ADD COLUMN links_sha TEXT;",   # адреса ссылок apparatus (вёрстка 2016+)
    # текущая версия статьи на сайте (/entries/<slug>/): одна строка на статью,
    # перезаписывается при обновлении; etag/last_modified — для условных запросов
    4: """
    CREATE TABLE live (
        entry_slug      TEXT PRIMARY KEY REFERENCES entries(slug),
        http_status     INTEGER NOT NULL,
        blob_sha        TEXT,
        raw_sha         TEXT,
        text_sha        TEXT,
        body_sha        TEXT,
        biblio_sha      TEXT,
        apparatus_sha   TEXT,
        struct_sha      TEXT,
        links_sha       TEXT,
        title           TEXT,
        revision_date   TEXT,
        date_source     TEXT,
        word_count      INTEGER,
        coverage        REAL,
        extractor       TEXT,
        extract_version INTEGER,
        etag            TEXT,
        last_modified   TEXT,
        fetched_at      TEXT NOT NULL,   -- когда содержимое последний раз скачано
        checked_at      TEXT NOT NULL    -- когда последний раз спрашивали сайт (в т.ч. 304)
    );
    """,
    # отслеживаемые статьи: воркер сам проверяет их раз в час (новые издания,
    # текущая версия на сайте), найденные правки идут в Atom-ленту
    5: "ALTER TABLE entries ADD COLUMN watched INTEGER NOT NULL DEFAULT 0;",
}


def connect(path: Path) -> sqlite3.Connection:
    # Веб открывает соединение на запрос, а FastAPI может выполнить зависимость
    # и обработчик в разных потоках пула; одновременно соединением пользуется один.
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    (version,) = conn.execute("PRAGMA user_version").fetchone()
    if version > SCHEMA_VERSION:
        raise RuntimeError(f"база новее программы (схема {version} > {SCHEMA_VERSION})")
    if version == 0:
        conn.executescript(SCHEMA)
        version = 1
    for v in range(version + 1, SCHEMA_VERSION + 1):
        conn.executescript(MIGRATIONS[v])
        version = v
    conn.execute(f"PRAGMA user_version = {version}")
    conn.commit()
