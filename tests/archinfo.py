"""Эталон: сняты вручную с archinfo.cgi?entry=<slug> (автоматически туда нельзя —
/cgi-bin/ закрыт в robots.txt). Там перечислены только издания, в которых
статья менялась; в остальных лежит копия предыдущей версии."""

from __future__ import annotations

from sepdiff.editions import Edition

_S, _M = "substantive", "minor"
ARCHINFO: dict[str, dict[str, str]] = {
    "kant": {
        "sum2010": "first", "fall2010": _M, "sum2014": _M, "spr2016": _S, "sum2018": _M,
        "spr2020": _M, "fall2020": _S, "fall2023": _M, "fall2024": _S,
    },
    "qualia": {
        "fall1997": "first", "win1997": _S, "spr2003": _S, "sum2003": _S, "fall2007": _S,
        "sum2008": _M, "fall2008": _M, "sum2009": _M, "sum2013": _S, "fall2013": _M,
        "fall2015": _S, "win2016": _M, "win2017": _S, "sum2018": _M, "fall2021": _S,
        "fall2025": _S,
    },
    "logic-paraconsistent": {
        "fall1997": "first", "win2000": _S, "sum2004": _M, "win2004": _S, "win2007": _S,
        "fall2008": _M, "spr2009": _S, "sum2009": _M, "spr2013": _M, "sum2013": _S,
        "fall2013": _M, "spr2015": _M, "win2016": _S, "fall2017": _S, "sum2018": _S,
        "spr2022": _S, "spr2025": _M, "sum2026": _S,
    },
    "logic-modal": {
        "spr2000": "first", "win2001": _S, "win2003": _S, "sum2005": _M, "sum2007": _S,
        "spr2008": _M, "sum2008": _S, "fall2008": _M, "win2008": _M, "spr2009": _M,
        "fall2009": _S, "win2009": _S, "win2012": _M, "spr2013": _M, "sum2014": _S,
        "spr2016": _M, "fall2018": _S, "sum2021": _M, "spr2023": _S, "spr2024": _M,
    },
}

# Пары, где archinfo, по-видимому, ошибается (PLAN.md §13, прогон 2, п. 5).
KNOWN_GAPS = {("qualia", "win1997", "win2002")}


def expected(entry: str, a: str, b: str) -> str:
    """Самое сильное изменение в изданиях (a, b]; "unchanged" = identical или markup_only."""
    ka, kb = Edition.parse(a).key, Edition.parse(b).key
    labels = [lab for ed, lab in ARCHINFO[entry].items() if ka < Edition.parse(ed).key <= kb]
    if _S in labels:
        return _S
    return _M if labels else "unchanged"
