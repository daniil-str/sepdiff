import pytest
from fakes import EDS, KANT, FakeFetcher, site
from pages import FOOTER_NOV1, LIVE_PENDING, MINOR, SUBSTANTIVE, old, page, retired
from typer.testing import CliRunner

from sepdiff.cli import app
from sepdiff.diffing import PairStats
from sepdiff.fetcher import Response
from sepdiff.service import Library, SepDiffError


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
    assert len([c for c in calls if c.startswith("/archives/") and "/entries/" in c]) == 7   # 4 пробы + 3
    assert "/entries/kant/" in calls                           # и текущая версия на сайте
    assert len(set(calls)) == len(calls)                       # ничего не качаем дважды
    assert lib.scan("kant") == 0                               # второй раз — всё из кеша


def _long_site() -> tuple[list[str], dict[str, bytes]]:
    """24 издания 2000–2005; статья с 3-го, minor в 8-м, substantive в 16-м."""
    eds = [f"{s}{y}" for y in range(2000, 2006) for s in ("spr", "sum", "fall", "win")]
    versions = {}
    for i, ed in enumerate(eds[2:], 2):
        variant = SUBSTANTIVE if i >= 15 else MINOR if i >= 7 else {}
        versions[ed] = page(**{**variant, "nav": ed})   # вёрстка меняется в каждом издании
    pages = site({"kant": versions})
    pages["/archives/"] = "".join(f'<a href="{e}/">{e}</a>' for e in eds).encode()
    return eds, pages


def test_quick_scan_finds_every_revision_with_few_requests(tmp_path):
    eds, pages = _long_site()
    events: list[str] = []
    with Library(tmp_path, fetcher=FakeFetcher(pages)) as lib:  # type: ignore[arg-type]
        lib.init_editions()
        n = lib.scan("kant", deep=False, progress=lambda ev: events.append(ev.phase))
        hist = lib.history("kant")
        assert [(r.edition.slug, r.kind) for r in hist.revisions if r.kind != "markup_only"] == [
            (eds[2], "created"), (eds[7], "minor"), (eds[15], "substantive")]
        assert n < 22 - 5                           # заметно меньше, чем все издания подряд
        assert "partial" in events and "deep" not in events
        assert lib.conn.execute("SELECT scan_state FROM entries WHERE slug = 'kant'").fetchone()[0] == "partial"
        quick = [(r.edition.slug, r.kind) for r in hist.revisions]

        lib.scan("kant")                              # доводим до полного: те же правки
        full = lib.history("kant")
        assert [(r.edition.slug, r.kind) for r in full.revisions if r.kind != "markup_only"] == [
            (s, k) for s, k in quick if k != "markup_only"]
        assert full.unchecked_after == 0 and all(r.unchecked_before == 0 for r in full.revisions)


def test_live_version_not_yet_archived(tmp_path):
    pages = site({"kant": KANT})
    pages["/entries/kant/"] = page(**LIVE_PENDING)   # на сайте уже новая редакция
    with Library(tmp_path, fetcher=FakeFetcher(pages)) as lib:  # type: ignore[arg-type]
        lib.init_editions()
        lib.scan("kant")
        live = lib.history("kant").live
        assert live is not None and (live.base.slug, live.kind) == ("sum2021", "substantive")  # type: ignore[union-attr]
        d = lib.diff("kant", "live")
        assert (d.a.slug, d.b.slug, d.kind) == ("sum2021", "live", "substantive")
        assert lib.show("kant", "live").revision_date == "2026-08-01"
        assert "live" in [e.slug for e in lib.snapshot_editions("kant")]
        assert lib.refresh_live("kant") == "cached"                 # не чаще раза в сутки
        assert lib.refresh_live("kant", force=True) == "same"


def test_live_uses_conditional_get_and_sees_removal(tmp_path):
    class ConditionalSite(FakeFetcher):
        gone = False

        def get(self, path: str, headers: dict[str, str] | None = None) -> Response:
            if path == "/entries/kant/":
                self.calls.append(path)
                if self.gone:
                    return Response(404, b"", path)
                if headers and headers.get("If-None-Match") == '"v1"':
                    return Response(304, b"", path)
                return Response(200, KANT["sum2021"], path, {"etag": '"v1"'})
            return super().get(path, headers)

    fetcher = ConditionalSite(site({"kant": KANT}))
    with Library(tmp_path, fetcher=fetcher) as lib:  # type: ignore[arg-type]
        lib.init_editions()
        lib.scan("kant")
        assert lib.history("kant").live.kind == "identical"  # type: ignore[union-attr]
        assert lib.refresh_live("kant", force=True) == "not_modified"
        fetcher.gone = True
        assert lib.refresh_live("kant", force=True) == "gone"
        assert lib.history("kant").live.kind == "removed"  # type: ignore[union-attr]


def test_retired_entry_is_detected_and_cross_referenced(tmp_path):
    # T5: SEP не редиректит снятую/переименованную статью — вместо текста
    # /entries/<slug>/ отдаёт «Document Retired» (docs/journal.md §22).
    pages = site({"kant": KANT})
    pages["/entries/kant/"] = retired(successor="new-entry", last_edition="sum2021")
    with Library(tmp_path, fetcher=FakeFetcher(pages)) as lib:  # type: ignore[arg-type]
        lib.init_editions()
        lib.scan("kant")
        assert lib.refresh_live("kant", force=True) == "retired"

        live = lib.live_revision("kant")
        assert live is not None
        assert (live.kind, live.successor) == ("retired", "new-entry")
        assert live.stats == PairStats()                    # сравнивать с уведомлением нечего

        hist = lib.history("kant")
        assert hist.live is not None and hist.live.kind == "retired"   # type: ignore[union-attr]

        lib._ensure_entry("new-entry")                        # noqa: SLF001 — сама статья ещё не скачана
        assert lib.predecessors("new-entry") == [("kant", "Immanuel Kant")]
        assert lib.predecessors("kant") == []

        assert lib.refresh_live("kant") == "cached"            # раз в сутки, как и раньше


def test_symbol_report_finds_unmapped_images(tmp_path):
    # T6: [img:fig1] в old() — картинка-символ, для которой нет пары в SYMBOL_IMAGES.
    pages = site({"kant": KANT, "qualia": {"spr2020": old(foot=FOOTER_NOV1)}})
    with Library(tmp_path, fetcher=FakeFetcher(pages)) as lib:  # type: ignore[arg-type]
        lib.init_editions()
        lib.scan("kant", live=False)
        lib.scan("qualia", only=["spr2020"], live=False)
        assert lib.symbol_report() == [("fig1", 1, [("qualia", "spr2020")])]
        assert lib.symbol_report("kant") == []                      # в kant незнакомых картинок нет
        assert lib.symbol_report("qualia") == lib.symbol_report()


def test_cli_symbols(lib, monkeypatch):
    lib.scan("kant", live=False)
    monkeypatch.setenv("SEPDIFF_DATA", str(lib.root))
    runner = CliRunner()
    out = runner.invoke(app, ["symbols"])
    assert out.exit_code == 0, out.output
    assert "Незнакомых картинок-символов не найдено." in out.output


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


def test_blame(lib):
    # T4: у каждого абзаца последней версии — издание его последней правки.
    lib.scan("kant")
    by_text = {blk.text: ed for blk, ed in lib.blame("kant")}

    assert by_text["Immanuel Kant (1724-1804) is the central figure in modern philosophy."] == "spr2020"
    assert by_text["1. Life and works"] == "spr2020"                       # заголовок не менялся
    assert by_text["Kant was born in Königsberg and spent there all his life."] == "win2020"  # lived -> spent
    assert by_text["Kant never travelled more than a hundred miles from home."] == "spr2021"   # добавлен
    assert not any("will be removed later" in t for t in by_text)          # удалённый абзац не попал в blame


def test_blame_is_cached(lib, monkeypatch):
    lib.scan("kant")
    first = lib.blame("kant")

    calls: list[str] = []
    orig_show = Library.show

    def counting_show(self: Library, slug: str, edition: str):
        calls.append(edition)
        return orig_show(self, slug, edition)

    monkeypatch.setattr(Library, "show", counting_show)
    second = lib.blame("kant")                          # из blame_cache, документы не перечитаны
    assert calls == []
    assert [(b.text, ed) for b, ed in second] == [(b.text, ed) for b, ed in first]

    lib.rebuild("kant")                                  # цепочка ревизий та же -> кеш всё ещё годится
    third = lib.blame("kant")
    assert calls == []
    assert [(b.text, ed) for b, ed in third] == [(b.text, ed) for b, ed in first]


def test_unknown_entry(lib):
    with pytest.raises(SepDiffError, match="нет ни в одном"):
        lib.scan("no-such-entry")
    with pytest.raises(SepDiffError):
        lib.scan("../etc")


def test_entry_gone_from_latest_edition(tmp_path):
    """Статьи нет в последнем издании (удалена/переименована): история всё равно строится."""
    eds, _ = _long_site()
    versions = {ed: page(**({"lived": "spent"} if i >= 6 else {})) for i, ed in enumerate(eds[3:11], 3)}
    pages = site({"gone": versions})
    pages["/archives/"] = "".join(f'<a href="{e}/">{e}</a>' for e in eds).encode()
    with Library(tmp_path, fetcher=FakeFetcher(pages)) as lib:  # type: ignore[arg-type]
        lib.init_editions()
        lib.scan("gone")
        revs = [(r.edition.slug, r.kind) for r in lib.history("gone").revisions]
        assert revs == [(eds[3], "created"), (eds[6], "minor"), (eds[11], "removed")]
        fetched = {c.split("/")[2] for c in lib.fetcher.calls if "/entries/" in c}
        assert set(eds[3:12]) <= fetched and eds[20] not in fetched   # хвост после удаления не качаем подряд


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


def test_cli_shows_retirement_and_cross_reference(lib, monkeypatch):
    # sepdiff live/watch --check делают настоящий сетевой запрос (не в тесте!) —
    # готовим ретир через FakeFetcher фикстуры, а CLI дальше только читает базу.
    lib.scan("kant")
    lib.fetcher.pages["/entries/kant/"] = retired(successor="new-entry", last_edition="sum2021")  # type: ignore[attr-defined]
    assert lib.refresh_live("kant", force=True) == "retired"
    lib.fetcher.pages["/archives/sum2021/entries/new-entry/"] = page(nav="new-entry")  # type: ignore[attr-defined]
    lib.scan("new-entry")   # у преемницы своя история — иначе sepdiff log ей не покажет ничего

    monkeypatch.setenv("SEPDIFF_DATA", str(lib.root))
    runner = CliRunner()

    out = runner.invoke(app, ["log", "kant"])
    assert out.exit_code == 0, out.output
    assert "снята" in out.output and "new-entry" in out.output

    out = runner.invoke(app, ["log", "new-entry"])
    assert out.exit_code == 0, out.output
    assert "kant" in out.output and "sepdiff log kant" in out.output


def test_watch_list_and_feed(tmp_path):
    pages = site({"kant": KANT})
    pages["/entries/kant/"] = page(**LIVE_PENDING)      # на сайте правка, которой нет в архиве
    with Library(tmp_path, fetcher=FakeFetcher(pages)) as lib:  # type: ignore[arg-type]
        lib.init_editions()
        lib.scan("kant")
        assert lib.changes() == []                     # пока не отслеживаем — лента пуста
        lib.set_watched("kant")
        assert lib.is_watched("kant") and [w["slug"] for w in lib.watched()] == ["kant"]
        changes = lib.changes()
        assert (changes[0].edition.slug, changes[0].kind) == ("live", "substantive")
        assert [c.edition.slug for c in changes[1:]] == ["sum2021", "spr2021", "win2020", "spr2020"]
        lib.set_watched("kant", False)
        assert lib.changes() == []


def test_watch_tick_picks_up_a_new_edition(tmp_path):
    pages = site({"kant": KANT})
    pages["/entries/kant/"] = KANT["sum2021"]          # на сайте то же, что в последнем издании
    with Library(tmp_path, fetcher=FakeFetcher(pages)) as lib:  # type: ignore[arg-type]
        lib.init_editions()
        lib.scan("kant")
        lib.set_watched("kant")

    # вышло новое издание с правкой
    pages["/archives/"] = "".join(f'<a href="{s}/">{s}</a>' for s in [*EDS, "fall2021"]).encode()
    pages["/archives/fall2021/entries/kant/"] = page(**LIVE_PENDING)
    pages["/entries/kant/"] = page(**LIVE_PENDING)     # сайт показывает то же, что новое издание
    with Library(tmp_path, fetcher=FakeFetcher(pages)) as lib:  # type: ignore[arg-type]
        found = lib.watch_tick()
        assert [(c.edition.slug, c.kind) for c in found] == [("fall2021", "substantive")]
        assert lib.changes()[0].edition.slug == "fall2021"
        assert lib.watch_tick() == []                  # второй раз нового нет


def _with_notes(**over: str) -> bytes:
    return page(**over).replace(
        b'<div id="toc"><ul>', b'<div id="toc"><ul><li><a href="notes.html">Notes</a></li>')


def test_supplements_are_off_by_default_and_content_edit_is_minor(tmp_path):
    # spr2020 и sum2020: одна и та же статья (текст, набор блоков и супплементов те же),
    # различается только навигация сайта (как MARKUP) — сама по себе не правка;
    # notes.html при этом у изданий разный.
    pages = site({"kant": {"spr2020": _with_notes(), "sum2020": _with_notes(nav="NEW SITE DESIGN")}})
    pages["/archives/spr2020/entries/kant/notes.html"] = b"<html><body><p>Note v1</p></body></html>"
    pages["/archives/sum2020/entries/kant/notes.html"] = b"<html><body><p>Note v2</p></body></html>"
    with Library(tmp_path, fetcher=FakeFetcher(pages)) as lib:  # type: ignore[arg-type]
        lib.init_editions()
        lib.scan("kant", only=["spr2020", "sum2020"], live=False)
        # без --supplements правка внутри notes.html не видна: только вёрстка сайта
        assert [r.kind for r in lib.history("kant").revisions] == ["created", "markup_only"]

        n = lib.fetch_supplements("kant")
        assert n == 2   # notes.html обеих изданий — впервые
        assert [r.kind for r in lib.history("kant").revisions] == ["created", "minor"]
        assert lib.fetch_supplements("kant") == 0   # второй раз всё из кеша, требований нет
