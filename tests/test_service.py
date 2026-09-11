import pytest
from pages import MARKUP, MINOR, RELATED, SUBSTANTIVE, page
from typer.testing import CliRunner

from sepdiff.cli import app
from sepdiff.fetcher import Response
from sepdiff.service import Library, SepDiffError

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

    def get(self, path: str) -> Response:
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


@pytest.fixture
def lib(tmp_path):
    fetcher = FakeFetcher(site({"kant": KANT}))
    with Library(tmp_path, fetcher=fetcher) as lib:  # type: ignore[arg-type]
        lib.init_editions()
        yield lib


def test_scan_finds_first_edition_by_bisection(lib):
    lib.scan("kant")
    calls = lib.fetcher.calls
    assert "/archives/fall2019/entries/kant/" not in calls   # раньше первого издания не ходим подряд
    assert len([c for c in calls if "/entries/" in c]) == 7    # 4 пробы + 3 оставшихся издания
    assert len(set(calls)) == len(calls)                       # ничего не качаем дважды
    assert lib.scan("kant") == 0                               # второй раз — всё из кеша


def test_history(lib):
    lib.scan("kant")
    hist = lib.history("kant")
    assert hist.title == "Immanuel Kant"
    assert [(r.edition.slug, r.kind) for r in hist.revisions] == [
        ("spr2020", "created"), ("sum2020", "markup_only"), ("win2020", "minor"),
        ("spr2021", "substantive"), ("sum2021", "minor")]
    by_ed = {r.edition.slug: r for r in hist.revisions}
    assert by_ed["win2020"].unchanged_before == 1              # fall2020 без изменений
    assert (by_ed["win2020"].stats.words_added, by_ed["win2020"].stats.words_removed) == (1, 1)
    assert by_ed["spr2021"].stats.biblio_added == 1
    assert by_ed["sum2021"].stats.apparatus_changed == 1


def test_diff(lib):
    lib.scan("kant")
    d = lib.diff("kant", "win2020")
    assert (d.a.slug, d.b.slug, d.kind) == ("fall2020", "win2020", "minor")
    assert lib.diff("kant", "spr2020", "spr2021").kind == "substantive"
    with pytest.raises(SepDiffError):
        lib.diff("kant", "spr2020")      # первая версия
    with pytest.raises(SepDiffError):
        lib.diff("kant", "fall2019", "spr2021")   # снимка нет
    assert lib.show("kant", "spr2021").title == "Immanuel Kant"


def test_unknown_entry(lib):
    with pytest.raises(SepDiffError, match="нет в последнем издании"):
        lib.scan("no-such-entry")
    with pytest.raises(SepDiffError):
        lib.scan("../etc")


def test_removed_entry(tmp_path):
    gone = {"spr2020": page(), "sum2020": page(**MINOR)}
    with Library(tmp_path, fetcher=FakeFetcher(site({"gone": gone}))) as lib:  # type: ignore[arg-type]
        lib.init_editions()
        lib.scan("gone", only=["spr2020", "sum2020", "fall2020"])
        kinds = [r.kind for r in lib.history("gone").revisions]
        assert kinds == ["created", "minor", "removed"]


def test_stale_extraction_is_redone(lib):
    lib.scan("kant")
    with lib.conn:
        lib.conn.execute("UPDATE snapshots SET extract_version = 0, text_sha = 'stale' "
                         "WHERE entry_slug = 'kant' AND http_status = 200")
    lib._docs.clear()
    assert [r.kind for r in lib.history("kant").revisions][-1] == "minor"
    assert lib.conn.execute("SELECT count(*) FROM snapshots WHERE text_sha = 'stale'").fetchone()[0] == 0


def test_seed_and_search(tmp_path):
    pages = site({})
    pages["/archives/sum2021/contents.html"] = (
        b'<a href="entries/kant/">Kant, Immanuel</a> <a href="entries/logic-modal/">logic: modal</a>')
    with Library(tmp_path, fetcher=FakeFetcher(pages)) as lib:  # type: ignore[arg-type]
        lib.init_editions()
        assert lib.seed() == 2
        assert lib.search("modal") == [("logic-modal", "logic: modal")]


def test_import_dir(tmp_path):
    src = tmp_path / "cache"
    src.mkdir()
    (src / "kant.spr2020.html").write_bytes(page())
    (src / "kant.win2020.html").write_bytes(page(**MINOR))
    (src / "kant.fall2019.404").write_text("")
    (src / "report.kant.html").write_text("not a snapshot")
    (src / "contents.fall2019.html").write_text("edition contents, not an entry")
    with Library(tmp_path / "data", fetcher=FakeFetcher(site({}))) as lib:  # type: ignore[arg-type]
        lib.init_editions()
        assert lib.import_dir(src) == {"kant": 3}
        assert [r.kind for r in lib.history("kant").revisions] == ["created", "minor"]


def test_cli_log_and_diff(lib, monkeypatch):
    lib.scan("kant")
    monkeypatch.setenv("SEPDIFF_DATA", str(lib.root))
    runner = CliRunner()
    out = runner.invoke(app, ["log", "kant", "--all"])
    assert out.exit_code == 0, out.output
    assert "spr2021" in out.output and "substantive" in out.output and "markup_only" in out.output
    out = runner.invoke(app, ["diff", "kant", "win2020"])
    assert out.exit_code == 0, out.output
    assert "[-lived-]" in out.output and "{+spent+}" in out.output
    out = runner.invoke(app, ["diff", "kant", "fall2019", "win2020"])
    assert out.exit_code == 1 and "Ошибка" in out.output
