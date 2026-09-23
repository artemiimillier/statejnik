# Справочник скриптов

Все скрипты: `python3 $SKILL_DIR/scripts/<имя>`, запуск из рабочей папки. Только стандартная библиотека Python 3.9+. `--help` у каждого скрипта показывает полный список флагов.

**Конфиг.** Порядок поиска: `--config`, `$STATEJNIK_CONFIG`, `./statejnik.yaml`, затем `statejnik.yaml` вверх от папки файла-аргумента (`work/<slug>/draft.md` → рабочая папка). Не нашёлся - скрипт печатает в stderr одну строку-предупреждение и работает на значениях по умолчанию. `publish.py` вверх не ищет: пути площадок и `work/` считаются от текущей папки.

## status.py

`status.py [--config PATH]` - с него начинается любая работа. Печатает режим (`setup` / `ready`), найденный конфиг, последнюю запись `work/setup-state.md`, есть ли `work/plan.md` и сколько тем в нём ещё не взято, площадки, статьи в `work/`.
- `ready` - есть `statejnik.yaml` (не черновик), `project.domain` задан и это не `example.com`.
- Коды: 0 - ready; 1 - setup.

## check-all.py

`check-all.py work/<slug>/<файл>.md [--round N|final] [--min-internal N] [--out-dir DIR] [--config PATH]`
- Прогоняет structure-check, claims-check (`--text <файл>`), originality-check (корпус `work/<slug>/sources/`), ai-cadence-check, read-aloud-check.
- Вывод каждой - `work/<slug>/checks/<N>/<имя>.txt` (команда, код выхода, вывод), сводка - `checks/<N>/summary.md` (sha256 файла, таблица). В консоль - таблица «блокирующее / справочное».
- Блокирующие: structure-check, claims-check, originality-check (exit 2 - разные языки - решает проверяющий), read-aloud-check только при exit 1 (длинные предложения). ai-cadence-check - справка. Проверка, которая не запустилась (нет файла, пустые `sources/`), считается непройденной.
- `--round` по умолчанию - последний числовой круг в `checks/` или 1. После любой правки - снова `check-all.py` перед следующим кругом.
- `--config` передаётся всем проверкам.
- Коды: 0 - блокирующие прошли; 1 - хотя бы одна нет; 2 - нет файла.

## prompt.py

`prompt.py review|final-editor|final-verify|select --slug S [--round N] [--strict] > work/<slug>/_prompts/<роль>-<N>.md`
- Подставляет в шаблон `templates/prompts/<роль>.md` плейсхолдеры `{{WORKDIR}}`, `{{SLUG}}`, `{{ROUND}}`, `{{PREV_ROUND}}`, `{{SKILL_DIR}}`, `{{CONFIG}}` (фрагмент `statejnik.yaml`: project, audience, voice, cta, editorial). Блок `{{#REPEAT}}...{{/REPEAT}}` остаётся только на кругах 2-3.
- Материалы передаются путями; в stderr - предупреждение о каждом недостающем файле (`checks/<N>/summary.md`, прошлый вердикт, `fixes-<N-1>.md`, `diff.txt`). Промпт `final-verify` не может ссылаться на `editor.md` - иначе exit 2.
- Коды: 0 - промпт напечатан; 1 - только с `--strict`, если не хватает материалов; 2 - ошибка аргументов, шаблона или изоляции.

## site.py

`site.py scan <url> --out work/site.json`
- `--content-path /ПУТЬ` - раздел статей сайта, можно несколько раз. По умолчанию `project.content_path`. Разделы вроде `/blog/`, `/stati/`, `/poleznye-stati/`, `/informacija/`, `/idei-i-trendy/`, `/journal/` находятся сами.
- `--max-urls N` - сколько НЕ статейных адресов сохранить (по умолчанию 20000 или `tools.site.max_urls`). Статейные собираются всегда.
- `--max-pages N` - сколько страниц открыть ради заголовков, статьи первыми (по умолчанию 1000 или `tools.site.max_pages`). Для gap нужны заголовки всех статей; если прочитаны не все, скан и gap предупреждают.
- `--workers N` (по умолчанию 6, до 16) - параллельные запросы; `--page-timeout S` (по умолчанию 15). Сотни статей - 2-5 минут: запускать в фоне.
- Заголовок статьи (`pages[].heading`): `title`; если у большинства статей title одинаковый («Статьи», «Блог») - h1, иначе og:title.
- Из статей отсеиваются акции (`/akcii`, `/sale`, `/promo`), архивы тегов и авторов, пагинация (`/page/N`, `?page=`), корни разделов, страницы без h1 с десятками карточек (подборки) - список в `not_articles`.
- Региональные копии (города в пути и поддоменах) отсеиваются.
- Печатает число найденных статей; 0 - предупреждение: задать `--content-path`.
- Коды: 0 - готово; 3 - сайт не читается (антибот, капча, пустой ответ, ошибка HTTP, редирект на чужой домен). Тогда темы конкурента берём через `web_search` с `site:домен` и браузер.

`site.py gap work/site.json work/keywords.json --out work/gap.json --plan work/plan.md [--top 30] [--threshold 0.75] [--maybe 0.6]`
- Сравнение запроса с заголовками статей сайта и их адресами по основам слов. Слова намерения («как», «лучше», «выбрать») и самые частые слова ниши весят меньше предметных. Статьи - список `article_urls` из скана. Товары, категории и фильтры покрытием не считаются.
- Оценка ≥ `--threshold` - тема покрыта; между `--maybe` и `--threshold` - раздел плана «Похоже на занятые» с ближайшей статьёй: открыть и решить.
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
- Редакционные выводы (`kind: promise` или `comparison` без `quote`) не требуют `quote`, `source_url` и `source_file`, но требуют `basis` - список id опорных записей; каждая опора должна быть в реестре со статусом `verified` или `limited`. Та же схема - в `references/process/05-write.md`.
- `where` - список мест, как в 05-write: заголовок H2 (с `## ` или без), служебное поле (`title`, `meta_description`, `excerpt`, `lead`, `faq`, `cta` и др.) или `вводная часть`. С `--text` заголовки сверяются с реальными H2 текста - несуществующий раздел даёт замечание.
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
  - Хеш считается от файла как есть; поле `status` в выходном файле или записи площадки ставит сам скрипт по `--status`. В `final.md` статус не менять - это правка, хеш не совпадёт.
  - Без приёмки скрипт останавливается: «пройдите приёмку по 06-review или отправьте черновиком». Обход гейта `--force-without-acceptance` (в `--help` не показан) - **только по прямому указанию владельца**; в `work/published.json` пишется `forced_without_acceptance: true`.
  - Тип `files` = `manual`: `.md` сохраняет frontmatter и поле `status`, `.html` получает `<meta name="statejnik:status">`, черновик - `noindex`.
  - Код 4 - нет приёмки.
- `publish.py record <площадка> <slug> <id> [url]` - записать ручную публикацию в `work/published.json` (id - номер или адрес записи на площадке).
- `publish.py ping <url>` - IndexNow (нужен `INDEXNOW_KEY`).
