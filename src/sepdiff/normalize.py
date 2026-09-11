"""Нормализация текста и хеши.

SEP между изданиями меняет типографику и переносы строк исходника, не трогая
текст. Всё это сводится здесь к одному виду, иначе каждое издание выглядело бы
правкой (PLAN.md §5.3, §13).
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

_PUNCT = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "`": "'",                                   # `qualia' в изданиях 1997 года
    "“": '"', "”": '"', "„": '"',
    "–": "-", "—": "-", "−": "-",  # en/em dash, минус
    " ": " ", " ": " ", " ": " ",  # nbsp, тонкие пробелы
    "​": "",                               # zero-width space
})


def normalize(s: str) -> str:
    s = unicodedata.normalize("NFC", s).translate(_PUNCT).replace("…", "...")   # frege fall2003 -> win2003
    return re.sub(r"\s+", " ", s).strip()


# Пробелы вокруг знаков формул («∀xA∨∀xB» -> «∀xA ∨ ∀xB») НЕ нормализуем:
# SEP считает такую правку minor correction (logic-modal sum2008 -> fall2008).


def sha(data: str | bytes) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()
