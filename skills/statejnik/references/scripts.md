# Справочник скриптов

Все скрипты: `python3 $SKILL_DIR/scripts/<имя>`, запуск из рабочей папки. Только стандартная библиотека Python 3.9+. `--help` у каждого скрипта показывает полный список флагов.

## site.py

`site.py scan <url> --out work/site.json`
- `--content-path /ПУТЬ` - раздел статей сайта, можно несколько раз. По умолчанию `project.content_path`. Разделы вроде `/blog/`, `/stati/`, `/poleznye-stati/`, `/informacija/`, `/idei-i-trendy/`, `/journal/` находятся сами.
- `--max-urls N` - сколько НЕ статейных адресов сохранить (по умолчанию 20000 или `tools.site.max_urls`). Статейные собираются всегда.
- `--max-pages N` - сколько страниц открыть ради заголовков, статьи первыми (по умолчанию 60 или `tools.site.max_pages`).
- Региональные копии (города в пути и поддоменах) отсеиваются.
- Печатает число найденных статей; 0 - предупреждение: задать `--content-path`.
- Коды: 0 - готово; 3 - сайт не читается (антибот, капча, пустой ответ, ошибка HTTP, редирект на чужой домен). Тогда темы конкурента берём через `web_search` с `site:домен` и браузер.

`site.py gap work/site.json work/keywords.json --out work/gap.json --plan work/plan.md [--top 30] [--threshold 0.75]`
- Сравнение по основам слов. Товары, категории и фильтры покрытием не считаются.
- Коммерческие запросы (купить, цена, доставка, адрес...) уходят в раздел «Коммерческие запросы», а не в план статей.
- У каждой темы в плане - «Ближайшая статья» сайта с оценкой: её нужно открыть и решить, занята тема или нет.

## keywords.py

`keywords.py <фразы> | --seeds-file work/seeds.txt [--expand] [--region ID] --out work/keywords.json`
- Вордстат при `WORDSTAT_TOKEN` в `.env`; иначе подсказки Яндекса и Google без частот. Сбой Вордстата не останавливает сбор.
- `--brand X`, `--exclude X` - исключить слово или бренд, можно много раз. Также исключаются `project.brands`, домены из `seo.competitors`, `seo.exclude`, `editorial.banned_words`.
- `--keep-brands` - оставить брендовые запросы (с пометкой `navigational`).
- `--no-filter` - выключить фильтр мусора (кроссворды, «N букв», скачать, только фото/видео, чужие страны при регионе Россия).

## fetch-source.py

`fetch-source.py <url> --out work/<slug>/sources/<имя>.md`
- Сохраняет читаемый текст с шапкой: `url`, `final_url`, дата, HTTP-код, sha256. В `claims.json → source_url` писать `final_url`.
- `--min-chars 500` - порог «текста мало»; `--full-page` - не вырезать меню и подвал; `--timeout 40`.
- Коды: 0 - сохранено; 1 - сеть, HTTP ≥ 400 или внутренний адрес; 2 - текста мало (страница рисуется скриптами): открыть браузером и сохранить текст вручную с той же шапкой.

## claims-check.py

`claims-check.py work/<slug> [--text work/<slug>/final.md] [--strict-text] [--all-numbers] [--json]`
- Проверяет схему `claims.json`, наличие `source_file`, дословность `quote` в источнике (нормализуются только пробелы), допустимый `status`.
- `--text` - находит в тексте числа, которых нет в реестре (предупреждение); `--strict-text` - делает их ошибкой.
- Коды: 0 - ок; 1 - есть неподтверждённое; 2 - нет или не читается `claims.json`.
- Запускать до проверяющего и перед выпуском.

## structure-check.py

`structure-check.py work/<slug>/draft.md [--min-internal N] [--json]`
- H1: `title` во frontmatter или ровно один `# H1` в тексте. Больше одного H1 - ошибка.
- Внутренние ссылки: минимум `tools.structure.min_internal_links` (по умолчанию 3), ссылка на `cta.url` не считается. `--min-internal 0` - не проверять (если причина нехватки записана в `status.md`).
- Запретные слова `editorial.banned_words` и `voice.forbidden` - по границам слов и основам, в тексте и публичных полях.
- Коды: 0 - ок; 1 - ошибки; 2 - нет файла.

## read-aloud-check.py, ai-cadence-check.py, originality-check.py

- `read-aloud-check.py <файл> [--strict]` - вердикт CLEAN / WARN / BLOCK. Голые цифры - только предупреждение; exit 1 - лишь при слишком длинных предложениях.
- `ai-cadence-check.py <файл>` - ритм и штампы, справочная.
- `originality-check.py <файл> work/<slug>/sources/` - близость к источникам; exit 1 - слишком близко к одному источнику, переписать.

## publish.py

- `publish.py list` - площадки из конфига.
- `publish.py check <площадка>` - проверка доступа (для `files`/`manual` - что папка есть и доступна на запись).
- `publish.py send <площадка> work/<slug>/final.md [--status draft|publish] [--slug S]`
  - `--status draft` (по умолчанию) работает без приёмки, с предупреждением.
  - `--status publish` требует `work/<slug>/accepted.md`; если там указан sha256, он должен совпасть с хешем файла (правка после приёмки снимает приёмку).
  - `--force-without-acceptance` - обход только по прямому указанию владельца; в `work/published.json` пишется `forced_without_acceptance: true`.
  - Тип `files` = `manual`: `.md` сохраняет frontmatter и поле `status`, `.html` получает `<meta name="statejnik:status">`, черновик - `noindex`.
  - Код 4 - нет приёмки.
- `publish.py record <площадка> <slug> <id> [url]` - записать ручную публикацию в `work/published.json` (id - номер или адрес записи на площадке).
- `publish.py ping <url>` - IndexNow (нужен `INDEXNOW_KEY`).
