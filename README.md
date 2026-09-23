# Статейник - SEO-агент для Яндекса и Google

Навык для ИИ-агента (Hermes, Claude Code, Codex и других). Он:

1. читает ваш сайт и сам понимает нишу;
2. смотрит конкурентов;
3. собирает ядро запросов: Вордстат + подсказки Яндекса и Google;
4. проверяет, на какие запросы у сайта уже есть страницы, а на какие нет;
5. составляет план статей;
6. пишет статьи по процессу из 16 этапов с проверками на факты, структуру и признаки ИИ-текста;
7. публикует в WordPress, Blogspot, Дзен (RSS), через MCP, вебхук или готовым файлом.

Скриптам нужен Python 3.9+, сторонние библиотеки не требуются.

## Установка

**Hermes**

```bash
hermes skills install artemiimillier/statejnik/skills/statejnik
```

**Claude Code**

```bash
git clone https://github.com/artemiimillier/statejnik.git
mkdir -p ~/.claude/skills && cp -r statejnik/skills/statejnik ~/.claude/skills/
```

**Codex и другие агенты**: скопируйте папку `skills/statejnik` в папку навыков агента (для Codex это `~/.codex/skills/`) или просто скажите агенту: «прочитай skills/statejnik/SKILL.md и действуй по нему».

## Первый запуск

Напишите агенту: **«Настрой статейник для сайта https://ваш-сайт.ru»**.

Агент пройдёт настройку по шагам (`skills/statejnik/references/onboarding.md`), задаст вопросы по одному и создаст рабочую папку:

```
~/statejnik-ваш-сайт.ru/
  statejnik.yaml   # ниша, аудитория, голос, площадки
  .env             # ключи (не коммитить)
  work/            # ядро, план, черновики, опубликованное
```

Дальше: «напиши следующую статью из плана», «опубликуй», «собери ядро заново».

## Что понадобится

| Для чего | Что | Обязательно |
|---|---|---|
| Частоты запросов | токен API Вордстата | нет, без него - подсказки без частот |
| WordPress | пароль приложения | если публикуете в WP |
| Blogspot | OAuth-клиент Google | если публикуете в Blogger |
| Дзен | RSS-лента на вашем сайте | если публикуете в Дзен |
| MCP | подключённый к агенту MCP-сервер | если у площадки есть MCP |

Пошагово: `skills/statejnik/references/publishers.md`.

## Состав

```
skills/statejnik/
  SKILL.md                 # точка входа для агента
  references/
    onboarding.md          # первая настройка
    publishers.md          # площадки публикации
    methodology.md         # процесс статьи, 16 этапов
    topic-selection.md     # выбор темы
    editorial/             # редакционный стандарт, финальная вычитка
    checklists/            # анти-ИИ, SEO/GEO, соответствие, юр. рамки РФ, CTA
  scripts/
    site.py                # чтение сайта, покрытие запросов, план
    keywords.py            # ядро: Вордстат + подсказки
    publish.py             # публикация во все площадки
    structure-check.py ai-cadence-check.py read-aloud-check.py originality-check.py
  templates/               # конфиг, .env, скелет черновика, чек-лист статьи
tests/                     # python3 -m unittest discover tests
```

## Лицензия

См. `LICENSE`.
