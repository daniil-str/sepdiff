"""Фальшивый сайт SEP для тестов сервиса и веба: пути -> HTML, остальное 404."""

from __future__ import annotations

from pages import MARKUP, MINOR, RELATED, SUBSTANTIVE, page

from sepdiff.fetcher import Response

EDS = ["fall2019", "win2019", "spr2020", "sum2020", "fall2020", "win2020", "spr2021", "sum2021"]
INDEX = "".join(f'<a href="{s}/">{s}</a>' for s in reversed(EDS)).encode()

# kant появляется в spr2020; fall2020 — байт в байт как sum2020.
KANT = {
    "spr2020": page(),
    "sum2020": page(**MARKUP),
    "fall2020": page(**MARKUP),
    "win2020": page(**MINOR),
    "spr2021": page(**SUBSTANTIVE),
    "sum2021": page(**RELATED),
}


class FakeFetcher:
    def __init__(self, pages: dict[str, bytes]) -> None:
        self.pages = pages
        self.calls: list[str] = []

    def get(self, path: str, headers: dict[str, str] | None = None) -> Response:
        self.calls.append(path)
        body = self.pages.get(path)
        return Response(200 if body is not None else 404, body or b"", "https://plato.stanford.edu" + path)

    def close(self) -> None:
        pass


def site(entries: dict[str, dict[str, bytes]]) -> dict[str, bytes]:
    pages = {"/archives/": INDEX}
    for slug, versions in entries.items():
        for ed, raw in versions.items():
            pages[f"/archives/{ed}/entries/{slug}/"] = raw
    return pages
