import shutil
import subprocess

import pytest
from fakes import KANT, FakeFetcher, site
from pages import LIVE_PENDING, page

from sepdiff.export import export_git
from sepdiff.service import Library, SepDiffError

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="нужен git")


def git(repo, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True,
                          encoding="utf-8").stdout


def test_export_git(tmp_path):
    pages = site({"kant": KANT})
    pages["/entries/kant/"] = page(**LIVE_PENDING)
    with Library(tmp_path / "data", fetcher=FakeFetcher(pages)) as lib:  # type: ignore[arg-type]
        lib.init_editions()
        lib.scan("kant")
        repo = tmp_path / "repo"
        n = export_git(lib, "kant", repo)

        # created, minor, substantive, minor + текущая версия; markup_only пропущен
        assert n == 5
        subjects = git(repo, "log", "--reverse", "--format=%s").splitlines()
        assert subjects[0] == "Spring 2020: первая версия" and subjects[-1].startswith("Текущая версия")
        assert set(git(repo, "tag").split()) == {"spr2020", "win2020", "spr2021", "sum2021", "live"}
        assert "lived there" in git(repo, "show", "spr2020:text.md")
        assert "spent there" in git(repo, "show", "win2020:text.md")
        assert git(repo, "log", "-1", "--format=%ad", "--date=short", "spr2021").strip() == "2021-03-21"
        assert "Rohlf, M., 2024" in git(repo, "show", "spr2021:bibliography.md")
        assert "[-lived-]{+spent+}" in git(repo, "diff", "spr2020", "win2020", "--word-diff", "--", "text.md")

        with pytest.raises(SepDiffError, match="не пуст"):
            export_git(lib, "kant", repo)
