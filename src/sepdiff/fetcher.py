"""HTTP к plato.stanford.edu с соблюдением robots.txt (PLAN.md §1.4, §4.1).

- не больше одного запроса в 5 с — в том числе между запусками CLI подряд:
  время последнего запроса хранится в файле рядом с базой;
- только разрешённые пути: /cgi-bin/, /diffs/, /search/ … запрещены, как и
  любые регистровые варианты /archives/, кроме строчного;
- Retry-After и экспоненциальный backoff на 429/5xx, не больше 3 повторов;
- fail-fast: после 2 сетевых ошибок подряд — стоп, а не зависание на каждом
  издании (с машины разработки Stanford доступен только через прокси;
  httpx сам берёт HTTPS_PROXY / HTTP_PROXY из окружения).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx

from . import config

DISALLOWED = ("/cgi-bin/", "/diffs/", "/search/", "/rss/", "/perl/")
MAX_BACKOFF = 120.0


class FetchError(Exception):
    pass


class NetworkDown(FetchError):
    pass


@dataclass
class Response:
    status: int
    content: bytes
    url: str

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")


class Fetcher:
    def __init__(
        self,
        *,
        delay: float = config.CRAWL_DELAY,
        state_file: Path | None = None,
        retries: int = 3,
        max_network_errors: int = 2,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.client = client or httpx.Client(
            headers={"User-Agent": config.user_agent()},
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=True,
        )
        self.delay = delay
        self.state_file = state_file
        self.retries = retries
        self.max_network_errors = max_network_errors
        self.sleep = sleep
        self.clock = clock
        self._last = 0.0
        self._network_errors = 0

    def close(self) -> None:
        self.client.close()

    def _wait_turn(self) -> None:
        last = self._last
        if self.state_file is not None:
            try:
                last = max(last, float(self.state_file.read_text()))
            except (OSError, ValueError):
                pass
        wait = last + self.delay - self.clock()
        if wait > 0:
            self.sleep(wait)
        self._last = self.clock()
        if self.state_file is not None:
            try:
                self.state_file.write_text(repr(self._last))
            except OSError:
                pass

    def _backoff(self, r: httpx.Response, attempt: int) -> float:
        header = r.headers.get("Retry-After")
        if header:
            try:
                return min(float(header), MAX_BACKOFF)
            except ValueError:
                try:
                    return min(max(parsedate_to_datetime(header).timestamp() - self.clock(), 0.0), MAX_BACKOFF)
                except (TypeError, ValueError):
                    pass
        return min(self.delay * 2 ** attempt, MAX_BACKOFF)

    def get(self, path: str) -> Response:
        if not path.startswith("/") or path != path.lower() or path.startswith(DISALLOWED):
            raise ValueError(f"путь запрещён robots.txt SEP или не абсолютный: {path}")
        url = config.BASE_URL + path
        for attempt in range(self.retries + 1):
            self._wait_turn()
            try:
                r = self.client.get(url)
            except httpx.TransportError as exc:
                self._network_errors += 1
                if self._network_errors >= self.max_network_errors:
                    raise NetworkDown(
                        f"{self._network_errors} сетевых ошибки подряд ({type(exc).__name__}: {exc}). "
                        "Если plato.stanford.edu недоступен напрямую — задайте HTTPS_PROXY."
                    ) from exc
                continue
            self._network_errors = 0
            if r.status_code == 429 or r.status_code >= 500:
                if attempt < self.retries:
                    self.sleep(self._backoff(r, attempt))
                    continue
                raise FetchError(f"{url}: HTTP {r.status_code}, повторы исчерпаны")
            return Response(r.status_code, r.content, str(r.url))
        raise FetchError(f"{url}: повторы исчерпаны")
