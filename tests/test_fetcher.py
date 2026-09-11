import httpx
import pytest

from sepdiff.fetcher import Fetcher, FetchError, NetworkDown


class FakeClock:
    def __init__(self) -> None:
        self.t = 1_000_000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.t += s


def make(handler, clock: FakeClock | None = None, **kw) -> tuple[Fetcher, FakeClock, list[str]]:
    clock = clock or FakeClock()
    calls: list[str] = []

    def record(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return handler(request)

    client = httpx.Client(transport=httpx.MockTransport(record))
    return Fetcher(client=client, sleep=clock.sleep, clock=clock, **kw), clock, calls


def ok(_req: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=b"hello")


def test_rate_limit_between_requests():
    f, clock, calls = make(ok)
    f.get("/archives/")
    f.get("/archives/fall2024/entries/kant/")
    assert clock.sleeps == [pytest.approx(5.0)]
    assert calls[1] == "https://plato.stanford.edu/archives/fall2024/entries/kant/"


def test_rate_limit_survives_restart(tmp_path):
    state = tmp_path / ".last_request"
    clock = FakeClock()
    make(ok, clock, state_file=state)[0].get("/archives/")
    clock.t += 2
    f2, _, _ = make(ok, clock, state_file=state)
    f2.get("/archives/")
    assert clock.sleeps == [pytest.approx(3.0)]


def test_404_is_a_normal_answer():
    f, _, _ = make(lambda _r: httpx.Response(404))
    assert f.get("/archives/fall1997/entries/kant/").status == 404


def test_retry_after_on_503():
    answers = iter([httpx.Response(503, headers={"Retry-After": "7"}), httpx.Response(200, content=b"x")])
    f, clock, calls = make(lambda _r: next(answers))
    assert f.get("/archives/").status == 200
    assert len(calls) == 2
    assert 7.0 in clock.sleeps


def test_gives_up_on_persistent_5xx():
    f, _, calls = make(lambda _r: httpx.Response(500), retries=2)
    with pytest.raises(FetchError):
        f.get("/archives/")
    assert len(calls) == 3


def test_fail_fast_when_network_is_down():
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("reset", request=request)

    f, _, calls = make(down)
    with pytest.raises(NetworkDown):
        f.get("/archives/")
    assert len(calls) == 2


@pytest.mark.parametrize("path", ["/cgi-bin/encyclopedia/archinfo.cgi?entry=kant", "/diffs/x",
                                  "/search/searcher.py", "/Archives/fall2024/", "archives/"])
def test_refuses_paths_disallowed_by_robots(path):
    f, _, calls = make(ok)
    with pytest.raises(ValueError):
        f.get(path)
    assert calls == []
