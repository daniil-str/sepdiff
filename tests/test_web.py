import pytest
from fakes import KANT, FakeFetcher, site
from fastapi.testclient import TestClient
from pages import LIVE_PENDING, page

from sepdiff.web.app import create_app

HX = {"HX-Request": "true"}
CONTENTS = b'<a href="entries/kant/">Kant, Immanuel</a> <a href="entries/logic-modal/">logic: modal</a>'


@pytest.fixture
def web(tmp_path):
    pages = site({"kant": KANT})
    pages["/archives/sum2021/contents.html"] = CONTENTS
    app = create_app(tmp_path, fetcher_factory=lambda: FakeFetcher(pages), start_worker=False)  # type: ignore[arg-type, return-value]
    with TestClient(app) as client:
        yield client, app.state.worker


def test_search_and_seed(web):
    client, worker = web
    r = client.get("/")
    assert r.status_code == 200 and "Загрузить оглавление" in r.text
    # до загрузки оглавления статью можно открыть по slug
    assert 'href="/e/kant"' in client.get("/search", params={"q": "kant"}, headers=HX).text

    r = client.post("/seed", headers=HX)
    assert "В очереди" in r.text
    assert worker.run_once()
    r = client.get("/jobs/1/progress", headers=HX)
    assert "Готово" in r.text and r.headers.get("HX-Refresh") == "true"

    r = client.get("/search", params={"q": "modal"}, headers=HX)
    assert "logic: modal" in r.text and "Загрузить оглавление" not in r.text
    assert client.get("/search", params={"q": "kant"}, follow_redirects=False).status_code == 303


def test_scan_history_and_diff(web):
    client, worker = web
    assert "Построить историю" in client.get("/e/kant").text

    first = client.post("/e/kant/scan", headers=HX)
    again = client.post("/e/kant/scan", headers=HX)
    assert 'id="job-1"' in first.text and 'id="job-1"' in again.text   # та же задача, не вторая
    assert 'hx-trigger="every 2s"' in first.text
    # пока задача идёт, история на странице статьи подтягивается сама
    assert 'hx-get="/e/kant/history" hx-trigger="every 10s"' in client.get("/e/kant").text
    assert worker.run_once() and not worker.run_once()
    assert "every 10s" not in client.get("/e/kant").text

    page = client.get("/e/kant").text
    assert "Immanuel Kant" in page and "существенная" in page and "Докачать" in page
    assert "только вёрстка" not in page.split("<tbody>")[1]       # markup_only свёрнуты
    assert "только вёрстка" in client.get("/e/kant/history", params={"all": 1}, headers=HX).text

    r = client.get("/e/kant/diff", params={"b": "win2020"})
    assert "<del>lived</del>" in r.text and "<ins>spent</ins>" in r.text

    r = client.get("/e/kant/diff/pane", params={"a": "spr2020", "b": "spr2021"}, headers=HX)
    assert "существенная" in r.text and "Kant never travelled" in r.text
    assert r.headers["HX-Push-Url"] == "/e/kant/diff?a=spr2020&b=spr2021&view=line"

    r = client.get("/e/kant/diff/pane", params={"a": "fall2019", "b": "spr2021"}, headers=HX)
    assert r.status_code == 200 and 'class="err"' in r.text      # снимка нет — ошибка в панели

    r = client.get("/e/kant/diff")                                   # по умолчанию — последняя правка
    assert 'value="sum2021" selected' in r.text

    r = client.get("/e/kant/v/spr2021")
    assert "Immanuel Kant" in r.text and "Kant never travelled" in r.text


def test_diff_side_by_side_view(web):
    # T3: переключатель «построчно / рядом», состояние хранится в ?view=.
    client, worker = web
    client.post("/e/kant/scan", headers=HX)
    assert worker.run_once()

    r = client.get("/e/kant/diff", params={"b": "win2020"})               # по умолчанию — построчно
    assert 'name="view" value="line" checked' in r.text
    assert 'class="diff">' in r.text and "<del>lived</del>" in r.text

    r = client.get("/e/kant/diff", params={"b": "win2020", "view": "side"})
    assert 'name="view" value="side" checked' in r.text
    assert 'class="diff side">' in r.text
    assert "<del>lived</del>" in r.text and "<ins>spent</ins>" in r.text  # разнесены по разным колонкам

    r = client.get("/e/kant/diff/pane", params={"a": "spr2020", "b": "spr2021", "view": "side"}, headers=HX)
    assert 'class="diff side">' in r.text
    assert r.headers["HX-Push-Url"] == "/e/kant/diff?a=spr2020&b=spr2021&view=side"

    # неизвестное значение view не ломает страницу — трактуется как «построчно»
    r = client.get("/e/kant/diff", params={"b": "win2020", "view": "bogus"})
    assert 'class="diff">' in r.text


def test_live_banner(tmp_path):
    pages = site({"kant": KANT})
    pages["/entries/kant/"] = page(**LIVE_PENDING)
    app = create_app(tmp_path, fetcher_factory=lambda: FakeFetcher(pages), start_worker=False)  # type: ignore[arg-type, return-value]
    with TestClient(app) as client:
        client.post("/e/kant/scan", headers=HX)
        assert app.state.worker.run_once()
        text = client.get("/e/kant").text
        assert "ещё нет в архиве" in text and "/e/kant/diff?a=sum2021&amp;b=live" in text
        r = client.get("/e/kant/diff", params={"a": "sum2021", "b": "live"})
        assert "Текущая версия (сайт)" in r.text and "существенная" in r.text
        assert "plato.stanford.edu/entries/kant/" in client.get("/e/kant/v/live").text


def test_errors_and_csrf(web):
    client, _ = web
    assert client.get("/e/Not_A_Slug").status_code == 404
    assert client.get("/e/nope/diff").status_code == 404
    assert client.post("/e/kant/scan", headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post("/e/kant/scan", follow_redirects=False).status_code == 303


def test_watch_toggle_and_atom_feed(web):
    client, worker = web
    client.post("/e/kant/scan", headers=HX)
    assert worker.run_once()
    assert "☆ отслеживать" in client.get("/e/kant").text

    assert "★ отслеживается" in client.post("/e/kant/watch", params={"on": 1}, headers=HX).text
    assert "Отслеживаемые" in client.get("/").text
    feed = client.get("/feed.atom")
    assert feed.headers["content-type"].startswith("application/atom+xml")
    assert "<entry>" in feed.text and "/e/kant/diff?b=spr2021" in feed.text

    assert "☆ отслеживать" in client.post("/e/kant/watch", params={"on": 0}, headers=HX).text
    assert "<entry>" not in client.get("/feed.atom").text
    assert client.post("/e/kant/watch", follow_redirects=False).status_code == 303
