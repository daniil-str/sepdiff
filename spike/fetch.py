"""Спайк, шаг 1: скачать несколько архивных снимков одной статьи SEP.

Сеть трогает только этот скрипт. Всё остальное работает офлайн по кешу,
чтобы можно было итерировать по экстрактору не долбя чужой сервер.

Правила вежливости (robots.txt SEP): crawl-delay 5, внятный User-Agent,
никаких /cgi-bin/. Архивные издания иммутабельны -> скачанное не перекачиваем.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import httpx

CACHE = Path(__file__).resolve().parent.parent / "data" / "spike"
BASE = "https://plato.stanford.edu"
CRAWL_DELAY = 5.0
# Контакт в UA — хороший тон для краулера, но только если владелец сам его задал.
_contact = os.environ.get("SEPDIFF_CONTACT")
UA = "SEPDiff/0.1 (personal research tool" + (f"; contact: {_contact}" if _contact else "") + ")"
# После стольких сетевых ошибок подряд сдаёмся, а не висим на каждом издании.
MAX_CONSECUTIVE_ERRORS = 2

ENTRY = "kant"

# Набор изданий подобран по archinfo.cgi для kant, чтобы покрыть все случаи:
#   sum2010  - первое архивное издание
#   spr2016  - substantive content change
#   fall2020 - substantive content change
#   fall2023 - minor correction
#   win2023  - изменений НЕ было -> контрольная пара с fall2023,
#              нормализация обязана дать одинаковый хеш
#   fall2024 - substantive content change (последнее)
EDITIONS = ["sum2010", "spr2016", "fall2020", "fall2023", "win2023", "fall2024"]


def archive_url(edition: str, entry: str) -> str:
    return f"{BASE}/archives/{edition}/entries/{entry}/"


def fetch_all(entry: str, editions: list[str]) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    client = httpx.Client(
        headers={"User-Agent": UA},
        timeout=httpx.Timeout(30.0, connect=10.0),
        follow_redirects=True,
    )
    first = True
    errors_in_row = 0
    with client:
        for edition in editions:
            dest = CACHE / f"{entry}.{edition}.html"
            if dest.exists():
                print(f"[skip]  {edition:9s} уже в кеше ({dest.stat().st_size:,} B)")
                continue

            if not first:
                time.sleep(CRAWL_DELAY)
            first = False

            url = archive_url(edition, entry)
            try:
                r = client.get(url)
            except httpx.HTTPError as exc:
                print(f"[ERR]   {edition:9s} {type(exc).__name__}: {exc}", file=sys.stderr)
                errors_in_row += 1
                if errors_in_row >= MAX_CONSECUTIVE_ERRORS:
                    sys.exit(f"{errors_in_row} сетевых ошибки подряд — останавливаюсь.")
                continue
            errors_in_row = 0

            if r.status_code == 404:
                # Не ошибка: статьи в этом издании ещё (или уже) нет.
                (CACHE / f"{entry}.{edition}.404").write_text("", encoding="utf-8")
                print(f"[404]   {edition:9s} статьи нет в этом издании")
                continue
            if r.status_code != 200:
                print(f"[{r.status_code}]   {edition:9s} {url}", file=sys.stderr)
                continue

            dest.write_bytes(r.content)
            enc = r.encoding or "?"
            print(f"[ok]    {edition:9s} {len(r.content):>9,} B  encoding={enc}  -> {dest.name}")


if __name__ == "__main__":
    entry = sys.argv[1] if len(sys.argv) > 1 else ENTRY
    editions = sys.argv[2:] or EDITIONS
    print(f"Статья: {entry}; изданий: {len(editions)}; кеш: {CACHE}")
    fetch_all(entry, editions)
