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
    # Этап 3: ещё три статьи с 1997 года (снято 2026-09-11).
    "frege": {
        "fall1997": "first", "spr1998": _S, "fall1999": _S, "win1999": _S, "spr2002": _S,
        "spr2004": _S, "win2004": _M, "spr2005": _S, "sum2005": _S, "win2006": _M,
        "spr2007": _M, "spr2008": _S, "sum2008": _S, "fall2008": _S, "win2008": _M,
        "spr2009": _M, "sum2009": _M, "win2010": _M, "sum2011": _M, "win2011": _M,
        "spr2012": _M, "win2012": _S, "spr2013": _M, "fall2013": _M, "win2013": _M,
        "spr2014": _M, "fall2014": _M, "fall2015": _S, "spr2016": _M, "fall2016": _S,
        "win2016": _S, "spr2017": _M, "spr2018": _M, "sum2018": _M, "sum2019": _M,
        "win2019": _S, "fall2020": _M, "spr2022": _S, "fall2022": _S, "spr2023": _M,
        "sum2023": _M, "spr2024": _M, "fall2024": _M, "sum2025": _M,
    },
    "russell-paradox": {
        "fall1997": "first", "fall2000": _S, "sum2001": _S, "fall2001": _M, "fall2002": _S,
        "sum2003": _S, "sum2004": _M, "fall2008": _M, "sum2009": _S, "spr2013": _M,
        "win2013": _S, "sum2014": _M, "fall2014": _S, "win2014": _M, "win2016": _S,
        "win2020": _S, "spr2021": _M, "win2024": _S, "spr2025": _M, "fall2025": _M,
        "win2025": _M, "spr2026": _S, "sum2026": _M,
    },
    "turing-machine": {
        "fall1997": "first", "spr2000": _S, "spr2002": _M, "sum2003": _S, "win2004": _S,
        "spr2005": _M, "sum2007": _M, "win2007": _M, "fall2008": _M, "win2008": _M,
        "spr2009": _M, "win2010": _M, "spr2011": _S, "fall2012": _S, "win2012": _M,
        "sum2013": _M, "spr2016": _M, "win2016": _M,
        "win2018": _S,   # «new, rewritten entry»
        "win2019": _M, "fall2021": _M, "win2021": _M, "fall2024": _M, "win2024": _M,
        "sum2025": _S,
    },
}

# Пары, где мы видим правку текста, а archinfo — нет. Каждая просмотрена
# вручную (diff настоящий, не шум разбора); SEP до ~2010 minor-правки
# записывал неполно. Тест требует, чтобы мы здесь показывали minor.
ARCHINFO_MISSES = {
    ("qualia", "win1997", "win2002"): "('The -> (The, дата в подвале Nov 1 -> Nov 2",
    ("frege", "spr1998", "sum1998"): "+предложение про Frege's Theorem, +Related Entry",
    ("frege", "sum1998", "fall1998"): "переписана Related Entry",
    ("frege", "fall1998", "win1998"): "переписана ссылка в тексте и Related Entry",
    ("frege", "win1998", "spr1999"): "переставлено слово logic, в тексте и Related Entries",
    ("frege", "sum2000", "fall2000"): "criticised -> criticized, пробелы в формулах",
    ("frege", "win2000", "spr2001"): "переписаны Related Entries",
    ("frege", "spr2003", "sum2003"): "добавлена нумерация разделов",
    ("frege", "fall2003", "win2003"): "формула-картинка стала текстом",
    ("frege", "sum2004", "fall2004"): "+слово under",
    ("frege", "spr2006", "sum2006"): "+Supplementary Document в библиографии, пробелы в формуле",
    ("frege", "fall2007", "win2007"): "+Related Entry",
    ("russell-paradox", "win1998", "spr1999"): "+Related Entry",
    ("russell-paradox", "spr1999", "sum1999"): "+разделитель | в Related Entries",
    ("russell-paradox", "win1999", "spr2000"): "+строка в Other Internet Resources",
    ("russell-paradox", "win2000", "spr2001"): "переписаны Related Entries",
    ("russell-paradox", "fall2006", "win2006"): "первый абзац обёрнут в <p>",
    ("russell-paradox", "win2006", "spr2007"): "Acknowledgements -> Acknowledgments",
    ("russell-paradox", "win2014", "spr2015"): "битая Related Entry стала ссылкой на frege-theorem",
    ("turing-machine", "win2000", "spr2001"): "Related Entries упорядочены по алфавиту",
    ("turing-machine", "win2003", "spr2004"): "добавлена нумерация разделов",
    # адрес ссылки в Other Internet Resources: такие правки SEP то отмечает
    # (frege fall2024, russell-paradox win2014), то нет
    ("russell-paradox", "spr2018", "sum2018"): "новый адрес журнала в Other Internet Resources",
    ("turing-machine", "spr2016", "sum2016"): "мёртвая ссылка заменена копией из Wayback Machine",
    ("turing-machine", "fall2025", "win2025"): "цвет в формулах orange -> Sienna, подписи к анимации",
}
# Пары, где мы видим правку, но называем её иначе, чем archinfo (xfail):
KNOWN_MISLABELS = {
    # дата в подвале («content last modified») сменилась, а SEP называет правку minor;
    # в подвальную эпоху смена даты в 8 случаях из 9 — substantive, правило не меняем
    ("turing-machine", "win2001", "spr2002"): "substantive вместо minor",
}
# Пары, где archinfo отмечает правку, которую мы намеренно не видим (xfail):
KNOWN_GAPS = {
    # в главной странице статьи ничего; вероятно, правили дополнительный документ
    # (notes.html и т.п.) — их содержимое не качаем
    ("frege", "spr2025", "sum2025"),
    ("turing-machine", "fall2021", "win2021"),
    # только кавычки ' -> ’ (типографика) и адрес автора в подвале сайта
    ("russell-paradox", "sum2001", "fall2001"),
    # новый пункт оглавления: ссылка на supplement.html, который в тексте был и раньше
    ("russell-paradox", "win2024", "spr2025"),
}


def expected(entry: str, a: str, b: str) -> str:
    """Самое сильное изменение в изданиях (a, b]; "unchanged" = identical или markup_only."""
    ka, kb = Edition.parse(a).key, Edition.parse(b).key
    labels = [lab for ed, lab in ARCHINFO[entry].items() if ka < Edition.parse(ed).key <= kb]
    if _S in labels:
        return _S
    return _M if labels else "unchanged"
