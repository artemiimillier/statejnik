# Площадки публикации

Каждая площадка - блок в `statejnik.yaml → publish.targets.<имя>`. Имя придумываете сами (`site`, `dzen`, `blog2`). Ключи хранятся только в `.env`.

Проверка без публикации: `python3 $SKILL_DIR/scripts/publish.py check <имя>`.
Отправка: `python3 $SKILL_DIR/scripts/publish.py send <имя> work/<slug>/final.md --status draft|publish`. Для `--status publish` нужен `work/<slug>/accepted.md` (запись приёмки, `process/06-review.md`) - без него скрипт откажет.

Повторная отправка той же статьи обновляет запись, а не создаёт дубль (id хранятся в `work/published.json`).

## Как выбрать способ

| Что у вас | Тип |
|---|---|
| WordPress (свой или хостинг) | `wordpress` |
| Blogspot / Blogger | `blogger` |
| Канал в Дзене | `dzen_rss` (нужен сайт, который раздаёт файл) или `manual` |
| У сервиса есть MCP-сервер (WordPress MCP, Notion, Ghost, свой) | `mcp` |
| Tilda, n8n, Make, свой бэкенд с приёмом JSON | `webhook` |
| Ничего из этого / хочу руками / «готовыми файлами» | `manual` (псевдоним `files`) |

## manual (files)

```yaml
files:                   # имя площадки - любое
  type: manual           # type: files - то же самое
  dir: work/ready        # по умолчанию
```

Кладёт `<slug>.md` и `<slug>.html`. Подходит для любой CMS: копируете и вставляете в редактор. Служебные поля (title, meta, excerpt) владелец вставляет в CMS руками - перечислить их в отчёте. Сверка и «готово» для этого типа - короткий путь в `process/07-delivery.md`: сверить файлы, сдать владельцу, после ручной публикации - `publish.py record <имя> <slug> <id> <url>` и `publish.py ping <url>`.

## wordpress

1. В админке WordPress: Пользователи → Профиль → «Пароли приложений» → ввести имя «statejnik» → «Добавить». Скопировать пароль (показывается один раз).
2. В `.env`: `WP_USER=<логин>` и `WP_APP_PASSWORD=<пароль приложения>`.
3. Конфиг:

```yaml
site:
  type: wordpress
  url: https://example.com
  categories: [Статьи]      # необязательно; создаст, если нет
```

Если check отвечает 401: сайт не на HTTPS, или плагин безопасности закрыл REST API, или хостинг режет заголовок Authorization. Для нескольких WP-сайтов задайте свои переменные: `user_env: WP2_USER`, `password_env: WP2_APP_PASSWORD`.

## blogger (Blogspot)

1. `https://console.cloud.google.com` → новый проект → «APIs & Services» → включить **Blogger API v3**.
2. «OAuth consent screen»: тип External, добавить себя в тестовые пользователи, затем **Publish app** (иначе токен умрёт через 7 дней).
3. «Credentials» → «Create credentials» → «OAuth client ID» → тип **Desktop app**. В `.env`: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`.
4. Получить refresh-токен: открыть в браузере
   `https://accounts.google.com/o/oauth2/v2/auth?client_id=<ID>&redirect_uri=http://localhost&response_type=code&scope=https://www.googleapis.com/auth/blogger&access_type=offline&prompt=consent`,
   разрешить доступ, из адресной строки (страница не откроется - это нормально) скопировать `code=...`, затем:
   ```bash
   curl -s https://oauth2.googleapis.com/token -d client_id=<ID> -d client_secret=<SECRET> \
     -d code=<CODE> -d grant_type=authorization_code -d redirect_uri=http://localhost
   ```
   Значение `refresh_token` → `.env` как `BLOGGER_REFRESH_TOKEN`.
5. `blog_id` - число из адреса редактора Blogger (`blogger.com/blog/posts/<blog_id>`).

```yaml
blogspot:
  type: blogger
  blog_id: "1234567890123456789"
  labels: [статьи]
```

## dzen_rss (Дзен)

Дзен сам забирает статьи из RSS-ленты сайта. Скрипт дописывает статью в файл ленты в формате Дзена: `content:encoded`, обложка `enclosure` от 700 px, `format-article`. При `--status draft` статья попадает в черновики Дзена (`native-draft`), при `publish` сразу публикуется.

```yaml
dzen:
  type: dzen_rss
  feed_path: /var/www/example.com/dzen.xml   # файл, который раздаёт ваш сайт
  feed_url: https://example.com/dzen.xml
  site_url: https://example.com
  default_image: https://example.com/cover.jpg
  max_items: 50
```

Один раз: Студия Дзена → настройки канала → RSS → вставить `feed_url`. Адрес ленты не должен быть закрыт в robots.txt. Требования Дзена: полный текст от 300 знаков, адреса без UTM. Если сайта нет, используйте `manual` и вставляйте HTML в редактор Дзена.

## mcp

Если у площадки есть MCP-сервер, агент публикует сам через него. Сначала подключить сервер к агенту:

- Hermes: `hermes mcp add <имя> --url <адрес> --auth header` (или `--command ... --args ...` для локального)
- Claude Code: `claude mcp add --transport http <имя> <адрес> -H "<заголовок авторизации из документации сервера>"` (ключ владелец подставляет сам, в чат не присылает)

Затем посмотреть список инструментов сервера и выбрать тот, что создаёт запись.

```yaml
notion:
  type: mcp
  server: notion
  tool: create-page
  field_map: {title: title, html: content}   # как назвать поля для этого инструмента
```

`publish.py send` сохранит `work/<slug>/mcp-<имя>.json`. Агент вызывает инструмент с этими данными, а полученный id записывает командой `publish.py record <имя> <slug> <id> [url]`.

## webhook

```yaml
tilda:
  type: webhook
  url: https://hook.example.com/statejnik
  token_env: WEBHOOK_TOKEN     # необязательно, уйдёт как Bearer
```

Отправляет POST с JSON: `title, slug, status, html, markdown, excerpt, tags, meta_title, meta_description`. Ответ может вернуть `id` и `url`.

## Ускорение индексации (IndexNow)

Яндекс и Bing принимают IndexNow. Сгенерируйте ключ (32 символа, латиница и цифры), положите файл `<ключ>.txt` с тем же ключом в корень сайта, в `.env` впишите `INDEXNOW_KEY=<ключ>`. После публикации: `publish.py ping <url статьи>`. Для Google: карта сайта в Search Console.
