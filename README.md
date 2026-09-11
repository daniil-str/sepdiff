# SEPDiff

История правок статей [Stanford Encyclopedia of Philosophy](https://plato.stanford.edu/) —
как `git log` и `git diff`. Источник — квартальные архивные издания SEP
(`/archives/<edition>/entries/<slug>/`, с 1997 года).

Локальный инструмент для чтения: тексты SEP защищены копирайтом, скачанные
снимки лежат только в `data/` и в git не попадают.

## Установка

Нужны Python 3.13+ и [uv](https://docs.astral.sh/uv/).

```bash
uv sync
```

## Использование

```bash
uv run sepdiff init                  # база + список изданий (1 запрос)
uv run sepdiff fetch kant            # скачать все издания статьи (до ~10 мин)
uv run sepdiff log kant              # история ревизий, новые сверху
uv run sepdiff diff kant fall2024    # ревизия относительно предыдущего снимка
uv run sepdiff diff kant spr2016 fall2020
uv run sepdiff show kant fall2024    # извлечённый текст версии
uv run sepdiff seed                  # оглавление SEP для поиска (1 запрос)
uv run sepdiff search modal
```

Веб-интерфейс — поиск, история статьи, diff двух изданий, сканирование в фоне:

```bash
uv run sepdiff serve                 # http://localhost:8000/
```

Сервер слушает только `127.0.0.1`. Страницы работают и без JavaScript;
[htmx](https://htmx.org/) 2.0.4 лежит в `src/sepdiff/web/static/`, так что
интерфейс работает офлайн.

`fetch` соблюдает `robots.txt` SEP: не больше одного запроса в 5 секунд (в том
числе между запусками), никаких `/cgi-bin/` и `/search/`. Скачанное не
перекачивается никогда; прерванный `fetch` продолжает с того же места.

Если `plato.stanford.edu` недоступен напрямую, задайте прокси:
`HTTPS_PROXY=http://127.0.0.1:2080`. Каталог данных — `./data` или `$SEPDIFF_DATA`.
Контакт для User-Agent краулера — `$SEPDIFF_CONTACT` (по умолчанию не отправляется).

## Виды ревизий

| значок | вид | что значит |
|---|---|---|
| ● | substantive | SEP сменил дату «substantive revision» |
| ○ | minor | текст, ссылки или разметка поменялись, дата та же |
| ◐ | changed | текст поменялся, но на странице нет даты (ранние издания) |
| · | markup_only | поменялась только вёрстка сайта (скрыто, `log --all`) |
| ◇ | created | первое издание со статьёй |
| ✕ | removed | статья пропала из издания |

Как это определяется и насколько совпадает с данными самого SEP — в
[PLAN.md](PLAN.md) (§1.2, §13).

## Разработка

```bash
uv run pytest
```

`tests/test_real_snapshots.py` сверяет классификатор с эталоном SEP на
настоящих снимках из `data/spike/` (скачать: `uv run python spike/fetch.py`);
без них тест пропускается.
