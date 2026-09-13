"""Общие для CLI и веба подписи: виды ревизий, склонения, строка сводки."""

from __future__ import annotations

from .diffing import PairStats

KIND_MARK = {
    "substantive": "●", "minor": "○", "changed": "◐",
    "markup_only": "·", "created": "◇", "removed": "✕", "retired": "→",
}
KIND_LABEL = {
    "substantive": "существенная",
    "minor": "мелкая",
    "changed": "изменение",
    "markup_only": "только вёрстка",
    "created": "первая версия",
    "removed": "удалена",
    "retired": "снята",
}
# Пояснения — всплывающие подсказки в вебе.
KIND_HINT = {
    "substantive": "SEP сменил дату «substantive revision»",
    "minor": "minor correction: текст, ссылки или разметка поменялись, дата та же",
    "changed": "текст поменялся, но на странице нет даты — вид правки не определить",
    "markup_only": "поменялась только вёрстка сайта, текст статьи тот же",
    "created": "первое издание со статьёй",
    "removed": "статья пропала из издания",
    "retired": "SEP снял статью с сопровождения (страница жива, 200, но текста больше нет)",
}
TEXT_KINDS = {"substantive", "minor", "changed"}   # ревизии, где есть что показать в diff


def plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def editions_word(n: int) -> str:
    return f"{n} {plural(n, 'издание', 'издания', 'изданий')}"


def gap_texts(unchanged: int, unchecked: int) -> list[str]:
    out = []
    if unchanged:
        out.append(f"{editions_word(unchanged)} без изменений")
    if unchecked:
        out.append(f"{editions_word(unchecked)} {plural(unchecked, 'не скачано', 'не скачаны', 'не скачано')}"
                   " — не проверено")
    return out


def stats_line(kind: str, st: PairStats) -> str:
    if kind == "created":
        return f"{st.words_added:,} слов"
    if kind in ("markup_only", "removed", "retired"):
        return ""
    changed = st.words_added or st.words_removed or st.blocks_changed
    parts = [f"+{st.words_added:,} / −{st.words_removed:,}" if changed else "текст тот же"]
    if st.biblio_added or st.biblio_removed or st.biblio_modified:
        parts.append(f"библ. +{st.biblio_added} −{st.biblio_removed} ~{st.biblio_modified}")
    if st.apparatus_changed:
        parts.append(f"ссылки: {st.apparatus_changed}")
    return " · ".join(parts)


def eta_text(seconds: float) -> str:
    minutes = round(seconds / 60)
    return "меньше минуты" if minutes < 1 else f"~{minutes} мин"
