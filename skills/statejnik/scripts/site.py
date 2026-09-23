#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Сайт: чтение главной и карты сайта, проверка покрытия ключей, черновой план статей.

    python3 site.py scan https://example.com --out work/site.json
    python3 site.py scan https://example.com --content-path /idei-i-trendy --out work/site.json
    python3 site.py gap work/site.json work/keywords.json --out work/gap.json --plan work/plan.md

scan - главная (title, description, заголовки) + URL из sitemap.xml (со вложенными
       индексами) + заголовки страниц (до --max-pages, статьи - первыми).
       Раздел статей: --content-path, иначе project.content_path из statejnik.yaml
       (если сканируется домен проекта), плюс автоопределение по типичным путям
       (blog, articles, stati, poleznye-stati, informacija, idei-i-trendy, journal,
       zhurnal, news, sovety...) и по «словесным» адресам. Статейные URL лимитом
       --max-urls не режутся. Региональные копии (sitemap.<город>.xml, /<город>/путь,
       <город>.site.ru/путь) сводятся к одной.
gap  - для каждого кластера ключей ищет статью сайта, которая уже его покрывает
       (доля основ запроса в title/H1 и в адресе, порог --threshold). Товары,
       категории, фильтры покрытием не считаются. Коммерческие запросы (купить,
       цена, доставка, в наличии, адрес, город...) уходят в gap.json → commercial,
       а не в план. В плане у каждой темы - ближайшая статья сайта с оценкой.

Коды выхода scan: 0 - готово (0 статейных URL - предупреждение в stderr);
3 - сайт не читается (антибот, пустая страница, ошибка HTTP/сети, редирект на чужой домен).
Конкурентов тем же `scan` можно прочитать: python3 site.py scan https://competitor.ru ...
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures
import html
import json
import os
import re
import sys
import urllib.parse
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _net import fetch, decode_body  # noqa: E402
from _ru import stems, normalize, intent, translit_norm, stem_latin, city_geo  # noqa: E402
from _config import find_config, load_config, load_for, get, as_list  # noqa: E402

DEFAULT_MAX_URLS = 20000
DEFAULT_MAX_PAGES = 1000
DEFAULT_WORKERS = 6
PAGE_TIMEOUT = 15
EXIT_BLOCKED = 3

# Типичные разделы статей (первый сегмент пути).
ARTICLE_SECTIONS = (
    "blog", "blogs", "article", "articles", "stati", "statyi", "statya", "stat", "poleznye-stati",
    "poleznoe", "polezno", "polezno-znat", "informacija", "informaciya", "informatsiya", "info",
    "idei", "idei-i-trendy", "ideas", "journal", "zhurnal", "magazine", "news", "novosti",
    "sovety", "sovet", "tips", "guides", "guide", "wiki", "knowledge", "baza-znanij",
    "baza-znaniy", "media", "posts", "post", "press", "publikacii", "obzory", "reviews",
    "instrukcii", "faq", "voprosy", "encyclopedia", "enciklopediya", "spravochnik", "learn",
    "academy", "insights", "stories", "istorii", "podborki", "tpost",
)
# Сегменты коммерческих страниц (товары, категории, фильтры, корзина).
COMMERCE_SEGMENTS = (
    "product", "products", "catalog", "catalogue", "katalog", "category", "categories", "p",
    "shop", "store", "goods", "tovar", "tovary", "item", "items", "cart", "basket", "korzina",
    "order", "checkout", "brand", "brands", "collection", "collections", "filter", "sale",
    "rasprodazha", "akcii", "akcii-skidki", "actions", "promo", "compare", "wishlist", "lk",
    "account", "login", "search", "tag", "tags",
)
COMMERCE_TITLE = re.compile(r"купить|\bцен[аыу]\b|по низкой цене|в наличии|₽|\bруб\.|интернет-магазин|"
                            r"заказать|распродаж", re.I)
# Для страниц из раздела статей «купить» в заголовке ещё не коммерция («Какую миску купить кошке»):
# коммерческими их делает только явный признак витрины.
COMMERCE_TITLE_STRONG = re.compile(r"купить\b.{0,40}\b(в интернет-магазине|с доставкой|недорого|по цене)|"
                                   r"по низкой цене|в наличии|₽|\bруб\.|интернет-магазин|распродаж", re.I)
NON_ARTICLE = set(COMMERCE_SEGMENTS) | {"about", "contacts", "kontakty", "o-kompanii", "delivery", "dostavka",
                                        "oplata", "payment", "b2b", "static-page", "site", "help", "policy",
                                        "privacy", "vacancy", "vakansii", "sitemap", "en", "ru"}
LISTING_SEGMENTS = ("category", "categories", "tag", "tags", "page", "author", "rubric", "rubrika", "archive",
                    "arhiv", "archives")
# Акции, распродажи, промо - в любом месте пути (например /news/actions/...): не статьи.
PROMO_SEGMENTS = ("actions", "action", "akcii", "akciya", "akcija", "aktsii", "akcii-skidki", "sale", "sales",
                  "promo", "promos", "promotions", "rasprodazha", "skidki", "discounts", "offers", "specpredlozheniya")
LISTING_TITLE = re.compile(r"^\s*(акци[ияй]\b|скидки|распродаж|архив|все статьи|страница \d+|page \d+|"
                           r"результаты поиска)|\bстраница \d+ из\b", re.I)

ANTIBOT = re.compile(r"variti|ddos-guard|cf-chl|cf_chl|challenge-platform|checking your browser|"
                     r"captcha|__js_p_|antibot|servicepipe|qrator|доступ ограничен|не робот|"
                     r"проверка браузера|access denied|enable javascript", re.I)


def _log(msg):
    print(msg, file=sys.stderr)


def _get(url, allow_private=False, timeout=25):
    """(status, text, final_url); при сетевой ошибке - (None, '', url).

    Сначала честный User-Agent скрипта, при отказе (сеть/403/429) - браузерный:
    часть сайтов рвёт соединение одному из них."""
    last = None
    for browser in (False, True):
        try:
            status, headers, raw, final = fetch(url, timeout=timeout, allow_private=allow_private,
                                                browser_ua=browser)
        except ValueError as e:  # внутренний адрес, не http(s)
            _log(f"  ! {url}: {e}")
            return None, "", url
        except Exception as e:  # сеть, DNS, SSL, петля редиректов
            last = e
            continue
        if status in (403, 429) and not browser:
            last = f"HTTP {status}"
            continue
        return status, decode_body(raw, headers), final
    _log(f"  ! {url}: {last}")
    return (int(str(last)[5:]) if str(last).startswith("HTTP ") else None), "", url


def _text(fragment):
    fragment = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", fragment)
    t = re.sub(r"(?s)<[^>]+>", " ", fragment)
    return re.sub(r"\s+", " ", html.unescape(t)).strip()


def parse_page(doc):
    def one(rx):
        m = re.search(rx, doc, re.I | re.S)
        return _text(m.group(1)) if m else ""
    title = one(r"<title[^>]*>(.*?)</title>")
    desc = ""
    m = re.search(r'<meta[^>]+name=["\']description["\'][^>]*content=["\']([^"\']*)', doc, re.I)
    if not m:
        m = re.search(r'<meta[^>]+content=["\']([^"\']*)["\'][^>]*name=["\']description', doc, re.I)
    if m:
        desc = html.unescape(m.group(1)).strip()
    heads = [_text(h) for h in re.findall(r"(?is)<h[1-3][^>]*>(.*?)</h[1-3]>", doc)]
    heads = [h for h in heads if 2 < len(h) < 200][:40]
    h1 = one(r"<h1[^>]*>(.*?)</h1>")
    og = ""
    m = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]*content=["\']([^"\']*)', doc, re.I) or \
        re.search(r'<meta[^>]+content=["\']([^"\']*)["\'][^>]*property=["\']og:title', doc, re.I)
    if m:
        og = html.unescape(m.group(1)).strip()
    body = _text(doc)
    return {"title": title, "description": desc, "h1": h1, "og_title": og, "headings": heads, "text": body[:6000]}


def assign_headings(pages):
    """Поле heading - настоящий заголовок страницы для сверки с запросами.

    Если один и тот же title стоит у многих статей (например, у всех «Статьи»),
    это заголовок раздела, а не статьи: берём h1, иначе og:title."""
    arts = [p for p in pages if p.get("kind") == "article"]
    cnt = collections.Counter((p.get("title") or "").strip().lower() for p in arts)
    lim = max(3, int(0.1 * len(arts)))
    generic = {t for t, n in cnt.items() if t and n >= lim}
    for p in pages:
        t = (p.get("title") or "").strip()
        if not t or t.lower() in generic:
            p["heading"] = p.get("h1") or p.get("og_title") or t
            p["title_generic"] = bool(t)
        else:
            p["heading"] = t
    return generic


# ── домены, пути, регионы ────────────────────────────────────────────────────

def _host(url):
    return (urllib.parse.urlsplit(url if "://" in url else "https://" + url).hostname or "").lower()


def base_domain(host):
    """Грубый регистрируемый домен: spb.shop.ru → shop.ru, www.a.co.uk → a.co.uk."""
    parts = (host or "").lower().strip(".").split(".")
    if len(parts) >= 3 and parts[-2] in ("co", "com", "org", "net", "msk", "spb") and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def same_site(a, b):
    return base_domain(_host(a)) == base_domain(_host(b))


def _path(url):
    p = urllib.parse.urlsplit(url).path.strip("/")
    return "/" + p if p else "/"


def _segments(url):
    return [s for s in urllib.parse.urlsplit(url).path.lower().split("/") if s]


def dedupe_regional(urls, base_host):
    """Свести региональные копии к одной.

    1) одинаковый путь на разных поддоменах-городах → остаётся основной хост;
    2) префиксы-города /<город>/<путь>: если от 3 таких путей (и не меньше 30%
       префикса) есть и без префикса, префикс признаётся региональным и отбрасывается.
    Возвращает (urls, {"prefixes": [...], "dropped": N}).
    """
    base_host = (base_host or "").lower()
    by_path = collections.OrderedDict()
    dropped = 0
    for u in urls:
        key = _path(u) + ("?" + urllib.parse.urlsplit(u).query if urllib.parse.urlsplit(u).query else "")
        cur = by_path.get(key)
        if cur is None:
            by_path[key] = u
            continue
        dropped += 1
        if _host(u) == base_host and _host(cur) != base_host:
            by_path[key] = u
    paths = set(by_path)
    hits, totals = collections.Counter(), collections.Counter()
    for p in paths:
        segs = p.strip("/").split("/")
        if not segs[0]:
            continue
        totals[segs[0]] += 1
        rest = "/" + "/".join(segs[1:]) if len(segs) > 1 else "/"
        if rest in paths:
            hits[segs[0]] += 1
    regional = sorted(s for s, n in hits.items()
                      if n >= 3 and n >= 0.3 * totals[s] and s not in ARTICLE_SECTIONS)
    out = []
    for p, u in by_path.items():
        if p.strip("/").split("/")[0] in regional:
            dropped += 1
            continue
        out.append(u)
    return out, {"prefixes": regional, "dropped": dropped}


_SM_TOKEN = re.compile(r"^(.*?sitemap)[._-]([a-z0-9-]+)\.xml(?:\.gz)?$", re.I)
_ARTICLE_SM = re.compile(r"blog|article|stat|post|news|journal|zhurnal|idei|sovet|info|wiki|guide|polez|media", re.I)
_MAIN_REGION = ("msk", "moscow", "moskva", "main", "ru", "default", "www")


def order_child_sitemaps(locs):
    """Статейные карты - первыми; из семьи региональных копий sitemap.<город>.xml
    (от 5 штук) - только одна: msk/moscow или первая. Возвращает (порядок, пропущенные)."""
    fams = collections.defaultdict(list)
    for loc in locs:
        name = urllib.parse.urlsplit(loc).path.rsplit("/", 1)[-1]
        m = _SM_TOKEN.match(name)
        if m:
            fams[m.group(1).lower()].append((m.group(2).lower(), loc))
    skip = set()
    for members in fams.values():
        geo = [(t, loc) for t, loc in members if not _ARTICLE_SM.search(t) and not re.search(r"\d", t)]
        if len(geo) >= 5:
            keep = next((loc for t, loc in geo if t in _MAIN_REGION), geo[0][1])
            skip.update(loc for _, loc in geo if loc != keep)
    ordered = sorted((l for l in locs if l not in skip), key=lambda l: 0 if _ARTICLE_SM.search(l) else 1)
    return ordered, [l for l in locs if l in skip]


# ── статьи и коммерция ───────────────────────────────────────────────────────

def _wordy(seg):
    """Сегмент похож на «словесный» slug статьи: 3+ слов через дефис, без длинных чисел."""
    words = [w for w in re.split(r"[-_]+", seg) if w]
    return len(words) >= 3 and not re.search(r"\d{3,}", seg) and sum(len(w) for w in words) >= 12


def in_sections(url, sections):
    p = _path(url).lower()
    return any(p == s or p.startswith(s.rstrip("/") + "/") for s in sections)


def is_listing(url, sections):
    """Страница-список раздела (сам раздел, категория, тег, пагинация, акции), а не статья."""
    p = _path(url).lower()
    if any(p == s for s in sections):
        return True
    segs = _segments(url)
    if any(s in PROMO_SEGMENTS for s in segs):
        return True
    return any(s in LISTING_SEGMENTS for s in segs[1:]) or bool(re.fullmatch(r"page-?\d+|\d{1,3}", segs[-1]))


def drop_parent_listings(urls):
    """Убрать «корни подразделов»: адрес, под которым лежат 3+ других статьи
    (/encyclopedia/cats/ при /encyclopedia/cats/<порода>/), - это страница-список."""
    paths = [_path(u).rstrip("/") for u in urls]
    kids = collections.Counter()
    for p in paths:
        parts = p.split("/")
        for i in range(2, len(parts)):
            kids["/".join(parts[:i])] += 1
    keep, dropped = [], []
    for u, p in zip(urls, paths):
        (dropped if kids.get(p, 0) >= 3 else keep).append(u)
    return keep, dropped


def is_commerce_url(url):
    parts = urllib.parse.urlsplit(url)
    if parts.query:
        return True
    segs = _segments(url)
    if segs and segs[0] in COMMERCE_SEGMENTS:
        return True
    if any(s in PROMO_SEGMENTS for s in segs):
        return True
    if len(segs) > 1 and segs[1] in COMMERCE_SEGMENTS and segs[0] not in ARTICLE_SECTIONS:
        return True
    return any(re.fullmatch(r"\d{4,}", s) or re.fullmatch(r"(p|id|item|sku)-?\d+", s) or
               re.search(r"-\d{5,}$", s) for s in segs)


def detect_sections(urls, content_paths=()):
    """Разделы статей: заданные (content_path), типичные и найденные по «словесным» URL."""
    found = collections.OrderedDict()
    for cp in content_paths:
        cp = "/" + cp.strip("/").lower()
        if cp != "/":
            found[cp] = "content_path"
    first = collections.defaultdict(list)
    for u in urls:
        segs = _segments(u)
        if len(segs) >= 2:
            first[segs[0]].append(segs)
    for seg, items in first.items():
        key = "/" + seg
        if any(key == f or key.startswith(f + "/") for f in found):
            continue
        if seg in ARTICLE_SECTIONS:
            found[key] = "typical"
        elif seg not in NON_ARTICLE and len(items) >= 10:
            if sum(1 for s in items if _wordy(s[-1])) / len(items) >= 0.6:
                found[key] = "auto"
    return found


def is_article_url(url, sections):
    if is_commerce_url(url) or not in_sections(url, sections):
        return False
    return not is_listing(url, sections)


# ── sitemap ──────────────────────────────────────────────────────────────────

def sitemap_urls(base, limit, allow_private, is_priority):
    """URL из карт сайта. Лимит `limit` - только на НЕприоритетные (не статьи):
    статейные собираются всегда. Возвращает (urls, info)."""
    info = {"sitemaps_read": 0, "sitemaps_skipped_regional": 0, "truncated": False, "sources": []}
    seen, out, extra = set(), [], 0
    queue = []
    status, robots, _ = _get(urllib.parse.urljoin(base, "/robots.txt"), allow_private)
    if status == 200:
        queue = [l.split(":", 1)[1].strip() for l in robots.splitlines() if l.lower().startswith("sitemap:")]
    if not queue:
        queue = [urllib.parse.urljoin(base, "/sitemap.xml"), urllib.parse.urljoin(base, "/sitemap_index.xml")]
    docs_left = 80
    while queue and docs_left > 0:
        sm = queue.pop(0)
        if sm in seen:
            continue
        seen.add(sm)
        docs_left -= 1
        status, body, final = _get(sm, allow_private, timeout=60)
        if status != 200 or not body.strip():
            continue
        try:
            root = ET.fromstring(body.lstrip().encode("utf-8"))
        except ET.ParseError:
            continue
        info["sitemaps_read"] += 1
        info["sources"].append(final)
        tag = root.tag.split("}")[-1]
        locs = [e.text.strip() for e in root.iter() if e.tag.split("}")[-1] == "loc" and e.text]
        if tag == "sitemapindex":
            ordered, skipped = order_child_sitemaps(locs)
            info["sitemaps_skipped_regional"] += len(skipped)
            queue = ordered + queue
            continue
        for u in locs:
            if is_priority(u):
                out.append(u)
            elif extra < limit:
                out.append(u)
                extra += 1
            else:
                info["truncated"] = True
    return out, info


def home_links(doc, base):
    links = []
    for href in re.findall(r'(?i)<a[^>]+href=["\']([^"\'#]+)', doc):
        u = urllib.parse.urljoin(base, html.unescape(href))
        if u.startswith("http") and same_site(u, base):
            links.append(u.split("#")[0])
    return list(dict.fromkeys(links))


def blocked_reason(status, doc, base, final):
    """Почему сайт не читается скриптом, или None."""
    if status is None:
        return "сайт не ответил (сеть, DNS, таймаут или петля редиректов)"
    if 300 <= status < 400:
        return f"HTTP {status}: редирект по кругу - обычно антибот (проверка браузера)"
    if status in (401, 403, 429, 503):
        return f"HTTP {status}: доступ закрыт (антибот или запрет для роботов)"
    if status >= 400:
        return f"HTTP {status}: главная не открылась"
    if final and not same_site(final, base):
        return f"редирект на чужой домен: {_host(final)}"
    pg = parse_page(doc or "")
    if ANTIBOT.search((doc or "")[:30000]) and len(pg["text"]) < 1500:
        return "страница проверки браузера (антибот) вместо сайта"
    if not pg["title"] and len(pg["text"]) < 200:
        return "пустая страница (сайт рисуется скриптами или закрыт антиботом)"
    return None


def read_pages(order, art_set, allow_private=False, workers=DEFAULT_WORKERS, timeout=PAGE_TIMEOUT):
    """Прочитать страницы параллельно (вежливо: несколько потоков, короткий таймаут).
    Порядок результата = порядок order; недоступные страницы пропускаются."""
    def one(u):
        st, d, _ = _get(u, allow_private, timeout=timeout)
        if st != 200:
            return None
        pg = parse_page(d)
        return {"url": u, "title": pg["title"], "h1": pg["h1"], "og_title": pg["og_title"],
                "kind": "article" if u in art_set else "other"}
    out = [None] * len(order)
    workers = max(1, min(int(workers or 1), 16))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(one, u): i for i, u in enumerate(order)}
        done = 0
        for fut in concurrent.futures.as_completed(futs):
            done += 1
            try:
                out[futs[fut]] = fut.result()
            except Exception as e:  # noqa: BLE001 - одна страница не должна ронять скан
                _log(f"  ! {order[futs[fut]]}: {e}")
            if len(order) > 200 and done % 200 == 0:
                _log(f"  ... прочитано {done}/{len(order)}")
    return [p for p in out if p]


def reclassify_pages(pages):
    """После чтения: какие «статьи» на деле списки, акции или коммерция.

    - title/h1 с «купить», «цена», «интернет-магазин»... - коммерция;
    - заголовок вида «Акции», «Архив», «Страница 2» - список;
    - один и тот же заголовок (h1/heading) у 3+ страниц - заголовок раздела, значит это списки;
    - раздел без h1 почти на всех прочитанных страницах (подборки товаров) - не статьи.
    Меняет kind на "other" и возвращает список отсеянных адресов."""
    arts = [p for p in pages if p.get("kind") == "article"]
    heads = collections.Counter((p.get("heading") or "").strip().lower() for p in arts)
    by_sec = collections.defaultdict(list)
    for p in arts:
        segs = _segments(p["url"])
        by_sec[segs[0] if segs else ""].append(p)
    no_h1_secs = {sec for sec, ps in by_sec.items()
                  if len(ps) >= 5 and sum(1 for p in ps if not (p.get("h1") or "").strip()) >= 0.8 * len(ps)}
    dropped = []
    for p in arts:
        head = (p.get("heading") or "").strip()
        segs = _segments(p["url"])
        why = None
        if COMMERCE_TITLE_STRONG.search(" ".join([head, p.get("h1", "")])):
            why = "commerce_title"
        elif LISTING_TITLE.search(head):
            why = "listing_title"
        elif head and heads[head.lower()] >= 3:
            why = "same_heading"
        elif (segs[0] if segs else "") in no_h1_secs:
            why = "no_h1_section"
        if why:
            p["kind"] = "other"
            p["not_article"] = why
            dropped.append(p["url"])
    return dropped


def cmd_scan(a):
    _, cfg = load_for(a.config, near=a.out)
    base = a.url if "://" in a.url else "https://" + a.url
    status, doc, final = _get(base, a.allow_private)
    reason = blocked_reason(status, doc, base, final)
    if reason:
        _log(f"{base}: сайт не читается скриптом - {reason}.\n"
             "  Что делать: откройте сайт браузером (browser-инструмент агента) и выпишите разделы и темы вручную;\n"
             "  для конкурента - замените его другим или берите темы из выдачи (web_search).")
        sys.exit(EXIT_BLOCKED)
    if final and _host(final) != _host(base):
        _log(f"  главная перенаправила на {final}")
    if final:
        base = final
    home = parse_page(doc)

    content_paths = list(a.content_path or [])
    proj_domain = str(get(cfg, "project.domain", "") or "")
    if not content_paths and proj_domain and same_site(proj_domain, base):
        content_paths = as_list(get(cfg, "project.content_path", ""))
    max_urls = a.max_urls if a.max_urls is not None else int(get(cfg, "tools.site.max_urls", DEFAULT_MAX_URLS))
    max_pages = a.max_pages if a.max_pages is not None else int(get(cfg, "tools.site.max_pages", DEFAULT_MAX_PAGES))
    known = ["/" + c.strip("/").lower() for c in content_paths if c.strip("/")]
    typical = ["/" + s for s in ARTICLE_SECTIONS]

    def priority(u):
        if is_commerce_url(u):
            return False
        segs = _segments(u)
        # /<город>/blog/... тоже приоритет: регион свернётся позже
        return in_sections(u, known + typical) or (len(segs) > 1 and "/" + segs[1] in known + typical) \
            or (bool(segs) and _wordy(segs[-1]))

    urls, info = sitemap_urls(base, max_urls, a.allow_private, priority)
    if not urls:
        _log("  карта сайта не найдена или пуста - беру ссылки с главной")
        urls = home_links(doc, base)
        info["fallback"] = "home_links"
    raw_count = len(urls)
    urls, reg = dedupe_regional(urls, _host(base))
    sections = detect_sections(urls, content_paths)
    sec_list = list(sections)
    articles = [u for u in urls if is_article_url(u, sec_list)]
    articles, parents = drop_parent_listings(articles)
    art_set = set(articles)
    urls = articles + [u for u in urls if u not in art_set]

    pages = []
    if max_pages:
        order = [u for u in articles if in_sections(u, known)] + [u for u in articles if not in_sections(u, known)]
        if len(order) < max_pages:
            order += [u for u in urls if u not in art_set and not is_commerce_url(u)][: max_pages - len(order)]
        order = order[:max_pages]
        if len(order) > 100:
            _log(f"  читаю заголовки {len(order)} страниц в {a.workers} потоков (таймаут {a.page_timeout} с)...")
        pages = read_pages(order, art_set, a.allow_private, a.workers, a.page_timeout)
    generic = assign_headings(pages)
    not_articles = reclassify_pages(pages)
    if not_articles:
        drop = set(not_articles)
        articles = [u for u in articles if u not in drop]
        art_set = set(articles)
        urls = articles + [u for u in urls if u not in art_set]

    sec_counts = collections.Counter()
    for u in articles:
        for s in sec_list:
            if in_sections(u, [s]):
                sec_counts[s] += 1
                break
    result = {
        "url": base, "home": home, "sitemap_count": raw_count, "urls": urls,
        "article_sections": [{"path": s, "source": sections[s], "articles": sec_counts.get(s, 0)} for s in sec_list],
        "article_count": len(articles), "article_urls": articles, "pages": pages,
        "regional": reg, "sitemap_info": info, "content_path": content_paths, "max_urls": max_urls,
        "not_articles": {"parent_listings": parents, "by_page": not_articles},
        "generic_titles": sorted(generic),
    }
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)

    regs = reg["prefixes"]
    print(f"{base}: «{home['title'][:80]}».")
    print(f"  URL в карте сайта: {raw_count}; после свёртки региональных копий: {len(urls)}"
          + (f" (префиксы-регионы: {', '.join(regs[:6])}{'…' if len(regs) > 6 else ''})" if regs else "")
          + (f"; региональных карт пропущено: {info['sitemaps_skipped_regional']}"
             if info["sitemaps_skipped_regional"] else ""))
    secs = ", ".join(f"{s['path']} - {s['articles']} ({s['source']})"
                     for s in result["article_sections"] if s["articles"])
    print(f"  Статейных URL: {len(articles)}" + (f"; разделы: {secs}" if secs else ""))
    n_art_pages = sum(1 for p in pages if p["kind"] == "article")
    print(f"  Прочитано страниц: {len(pages)} (статей среди них: {n_art_pages}).  → {a.out}")
    if parents or not_articles:
        print(f"  Отсеяно как списки/акции/коммерция: {len(parents) + len(not_articles)} "
              "(см. not_articles в JSON)")
    if generic:
        print(f"  Одинаковый title у многих статей ({', '.join(sorted(generic))[:80]}) - заголовки взяты из h1/og:title.")
    if len(articles) - n_art_pages > max(5, 0.05 * len(articles)):
        _log(f"  ! заголовки прочитаны у {n_art_pages} из {len(articles)} статей: остальные gap сверит только по адресу. "
             f"Нужны все - увеличьте --max-pages (сейчас {max_pages}).")
    if info["truncated"]:
        _log(f"  ! достигнут лимит --max-urls {max_urls}: часть НЕстатейных URL не записана "
             "(статейные собраны все). Нужен полный список - увеличьте --max-urls.")
    for cp in known:
        if not any(in_sections(u, [cp]) for u in articles):
            _log(f"  ! в разделе {cp} (content_path) статей не найдено - проверьте путь.")
    if not articles:
        _log("  ! ПРЕДУПРЕЖДЕНИЕ: не найдено ни одного статейного URL. Задайте раздел статей: "
             "--content-path /путь или project.content_path в statejnik.yaml. Если раздела статей нет, "
             "gap сравнит ключи только с заголовками прочитанных страниц.")


# ── gap ──────────────────────────────────────────────────────────────────────

_COMMERCIAL_Q = re.compile(
    r"(?<!\w)(купить|куплю|покупк\w*|цен[аыуе]|стоимост\w*|сколько стоит|недорог\w*|дешев\w*|доставк\w*|"
    r"в наличии|наличи[ея]|заказать|закажи|заказ|магазин\w*|интернет-магазин\w*|распродаж\w*|скидк\w*|"
    r"прайс\w*|оптом|рассрочк\w*|кредит|адрес\w*|телефон\w*|салон\w*|шоурум\w*|отзывы о магазин\w*|"
    r"отзывы покупател\w*|руб(?:\.|лей|ля)?|₽|avito|авито|озон|ozon|wildberries|вайлдберриз|"
    r"маркетплейс\w*|яндекс маркет)(?!\w)", re.I)


def is_commercial_phrase(phrase, brands=()):
    """Запрос под карточку/категорию/страницу города, а не под статью."""
    p = normalize(phrase)
    if _COMMERCIAL_Q.search(p) or city_geo(p):
        return True
    return intent(p, brands) in ("commercial", "navigational")


def classify_page(page, sections, article_set=None):
    """article | commerce | other. Товары, категории, фильтры и страницы с «купить/цена»
    в заголовке покрытием информационного запроса не считаются. article_set - список статей
    из скана (site.json → article_urls): если он есть, статья - только адрес из него."""
    url = page.get("url", "")
    if page.get("not_article"):
        return "commerce" if page["not_article"] == "commerce_title" else "other"
    title = " ".join([page.get("heading") or page.get("title", ""), page.get("h1", "")])
    if article_set is not None:
        in_art = url in article_set
    else:
        in_art = page.get("kind") == "article" or bool(sections and is_article_url(url, sections))
    listed = article_set is not None and in_art   # скан уже отнёс адрес к статьям - адресу доверяем
    if (is_commerce_url(url) and not listed) or (COMMERCE_TITLE_STRONG if in_art else COMMERCE_TITLE).search(title):
        return "commerce"
    if in_art:
        return "article"
    segs = _segments(url)
    if not sections and segs and _wordy(segs[-1]):
        return "article"
    return "other"


def _slug_skeletons(url):
    out = []
    for seg in _segments(url)[-1:]:
        for w in re.split(r"[-_.]+", seg):
            if w and not w.isdigit() and w not in ("html", "htm", "php"):
                out.append(translit_norm(w))
    return [s for s in out if s]


def _skel_match(a, b):
    """Основа запроса (в нормализованном транслите) совпадает со словом адреса по началу."""
    if not a or not b:
        return False
    if len(a) < 3:
        return a == b
    return b.startswith(a) or (len(b) >= 4 and a.startswith(b))


# Слова намерения, а не темы: «лучше», «выбрать», «быстро»... - малый вес при сверке.
_MODIFIER_WORDS = ("лучше лучший лучшая лучшие выбрать выбор выбирать правильно правильный быстро быстрый "
                   "самый хороший хорошая топ рейтинг совет советы делать сделать начать первый первые "
                   "надо нужен нужна нужны правила способ способы виды какую чего")
MODIFIER_STEMS = frozenset(stems(_MODIFIER_WORDS))
W_MODIFIER = 0.3
W_COMMON = 0.5      # слово ниши, которое есть в четверти запросов и чаще (кошка, диван)


def stem_eq(a, b):
    """Основы совпадают с учётом беглой гласной и хвостов: лоток~лотк, котенок~котенк, кошк~кошек."""
    if a == b:
        return True
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    if len(short) < 3:
        return False
    if long_.startswith(short) and len(long_) - len(short) <= 3:
        return True
    n = 0
    while n < len(short) and short[n] == long_[n]:
        n += 1
    return n >= 3 and n >= len(short) - 1 and len(long_) - n <= 2


def page_heading(page):
    return page.get("heading") or page.get("h1") or page.get("title") or ""


def match_score(phrase_stems, page, weights=None):
    """Оценка 0..1: взвешенная доля основ запроса, найденных в заголовке страницы
    (heading/h1/title, по основам с учётом беглых гласных) или в последнем сегменте
    адреса (по нормализованному транслиту: lucse ~ luchshe). Слова намерения и самые
    частые слова ниши весят меньше - совпадение по «кошка» и «лучше» темы не закрывает."""
    if not phrase_stems:
        return 0.0
    weights = weights or {}
    w = {s: weights.get(s, 1.0) for s in phrase_stems}
    total = sum(w.values()) or 1.0
    words = stems(" ".join([page_heading(page), page.get("h1", "") if page.get("heading") else ""]))
    t_hit = sum(w[s] for s in phrase_stems if any(stem_eq(s, x) for x in words))
    slug = _slug_skeletons(page.get("url", ""))
    s_hit = 0.0
    if slug:
        s_hit = sum(w[s] for s in phrase_stems if any(_skel_match(stem_latin(s), t) for t in slug))
    return round(max(t_hit, s_hit) / total, 2)


def stem_weights(keywords):
    """Веса основ: слова намерения - W_MODIFIER, слова из 25%+ запросов - W_COMMON, прочие - 1."""
    df = collections.Counter()
    n = 0
    for k in keywords:
        n += 1
        for st in stems(k.get("phrase", "")):
            df[st] += 1
    w = {}
    for st, c in df.items():
        if st in MODIFIER_STEMS:
            w[st] = W_MODIFIER
        elif n >= 20 and c / n >= 0.25:
            w[st] = W_COMMON
    for st in MODIFIER_STEMS:
        w.setdefault(st, W_MODIFIER)
    return w


def cmd_gap(a):
    _, cfg = load_for(a.config, near=a.site)
    site = json.load(open(a.site, encoding="utf-8"))
    kws = json.load(open(a.keywords, encoding="utf-8"))
    brands = as_list(get(cfg, "project.brands", []))
    sections = [s["path"] for s in site.get("article_sections", [])]
    pages = list(site.get("pages") or [])
    seen = {p["url"] for p in pages}
    pages += [{"url": u, "title": "", "h1": ""} for u in site.get("urls", []) if u not in seen]
    if pages and not any("heading" in p for p in pages):  # site.json старого формата
        assign_headings(pages)
    kinds = collections.Counter()
    pool = {"article": [], "other": []}
    art_set = set(site["article_urls"]) if isinstance(site.get("article_urls"), list) else None
    for p in pages:
        k = classify_page(p, sections, art_set)
        kinds[k] += 1
        if k != "commerce":
            p["_kind"] = k
            pool[k].append(p)
    art_with_head = sum(1 for p in pool["article"] if page_heading(p))
    weights = stem_weights(kws)

    clusters, commercial = collections.OrderedDict(), collections.OrderedDict()
    for k in kws:
        ph = k["phrase"]
        ckey = k.get("cluster") or ph
        if is_commercial_phrase(ph, brands):
            c = commercial.setdefault(ckey, {"cluster": ckey, "phrases": [], "volume": 0})
            c["phrases"].append(ph)
            c["volume"] += k.get("count") or 0
            continue
        c = clusters.setdefault(ckey, {"cluster": None, "phrases": [], "volume": 0, "fit": 0.0,
                                       "type": None, "sources": set()})
        if c["cluster"] is None:  # голова - первая некоммерческая фраза группы
            c["cluster"] = ckey if not is_commercial_phrase(ckey, brands) else ph
            c["type"] = k.get("article_type")
        c["phrases"].append(ph)
        c["volume"] += k.get("count") or 0
        c["fit"] = max(c["fit"], k.get("article_fit", 0.7))
        c["sources"].update(k.get("sources", []))

    rows = []
    for c in clusters.values():
        st = stems(c["cluster"])
        best, best_score = None, 0.0
        for kind in ("article", "other"):
            for p in pool[kind]:
                sc = match_score(st, p, weights)
                if sc > best_score:
                    best, best_score = p, sc
            if best_score >= a.threshold:
                break
        cover = best["url"] if best is not None and best_score >= a.threshold else None
        maybe = (not cover and best is not None and best["_kind"] == "article" and best_score >= a.maybe
                 and bool(page_heading(best)))
        demand = c["volume"] if c["volume"] else 10 * len(c["sources"]) * len(c["phrases"])
        rows.append({"cluster": c["cluster"], "phrases": c["phrases"][:12], "volume": c["volume"] or None,
                     "article_type": c["type"], "fit": c["fit"], "covered_by": cover,
                     "maybe_covered": maybe,
                     "nearest": ({"url": best["url"], "title": page_heading(best),
                                  "score": best_score, "kind": best["_kind"]} if best is not None else None),
                     "score": round(demand * c["fit"] * (0.15 if cover else 1.0), 1)})
    rows.sort(key=lambda r: -r["score"])
    comm = sorted(commercial.values(), key=lambda c: -(c["volume"] or len(c["phrases"])))
    result = {"site": site.get("url"), "threshold": a.threshold,
              "pages": {"articles": kinds["article"], "commerce_ignored": kinds["commerce"],
                        "other": kinds["other"]},
              "clusters": rows, "commercial": comm}
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    todo = [r for r in rows if not r["covered_by"] and not r["maybe_covered"] and r["fit"] >= 0.7][: a.top]
    maybe_rows = [r for r in rows if r["maybe_covered"] and r["fit"] >= 0.7]
    covered = sum(1 for r in rows if r["covered_by"])
    print(f"Кластеров под статьи: {len(rows)}, уже покрыты сайтом: {covered}, в план: {len(todo)}; "
          f"коммерческих групп отложено: {len(comm)}. Статей сайта для сверки: {kinds['article']}, "
          f"товаров/категорий пропущено: {kinds['commerce']}; похоже на занятые (проверить): {len(maybe_rows)}. → {a.out}")
    if pool["article"] and art_with_head < 0.8 * len(pool["article"]):
        _log(f"  ! заголовки есть только у {art_with_head} из {len(pool['article'])} статей сайта: остальные сверены "
             "лишь по адресу (транслит, английские адреса дают ложные «не покрыто»). "
             "Пересканируйте: site.py scan ... --max-pages <число статей>.")
    if not kinds["article"]:
        _log("  ! среди страниц сайта нет статей - покрытие почти наверняка занижено. "
             "Пересканируйте с --content-path (site.py scan --help).")
    if a.plan:
        def near(r):
            n = r["nearest"]
            if not n or n["score"] < 0.3:
                return "-"
            t = (n.get("title") or "")[:70]
            return (f"«{t}» " if t else "") + f"{n['url']} ({n['score']:.2f})"
        lines = ["# План статей (черновик)", "",
                 f"Сайт: {site.get('url')}. Кластеров под статьи: {len(rows)}, уже есть на сайте: {covered} "
                 f"(порог совпадения {a.threshold}). Коммерческие запросы - в конце, в план не входят.",
                 "Частота - Вордстат за 30 дней; «-» значит частоты нет (только подсказки).",
                 "«Ближайшая статья» - самая похожая страница сайта и доля совпавших основ 0..1: "
                 "откройте и проверьте, не закрыт ли интент.", "",
                 "| # | Тема (главный запрос) | Тип | Частота | Ближайшая статья сайта (оценка) | Доп. запросы |",
                 "|---|---|---|---|---|---|"]
        for i, r in enumerate(todo, 1):
            extra = "; ".join(p for p in r["phrases"][:6] if p != r["cluster"])[:200]
            lines.append(f"| {i} | {r['cluster']} | {r['article_type']} | {r['volume'] or '-'} | {near(r)} | {extra} |")
        if maybe_rows:
            lines += ["", f"## Похоже на занятые - открыть статью и решить (порог {a.maybe}-{a.threshold})", ""]
            lines += [f"- {r['cluster']} → {near(r)}" for r in maybe_rows[:40]]
        lines += ["", "## Уже покрыто сайтом (не дублировать, можно обновить)", ""]
        lines += [f"- {r['cluster']} → «{(r['nearest'].get('title') or '')[:70]}» {r['covered_by']} ({r['nearest']['score']:.2f})"
                  for r in rows if r["covered_by"]][:40]
        if comm:
            lines += ["", "## Коммерческие запросы (не для статей: карточки, категории, города)", ""]
            lines += [f"- {c['cluster']}: " + "; ".join(c["phrases"][:4]) for c in comm[:30]]
        with open(a.plan, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        print(f"План: {a.plan}")


def main():
    p = argparse.ArgumentParser(description="Сайт: чтение, покрытие ключей, черновой план статей",
                                formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan", help="прочитать сайт и карту сайта",
                       formatter_class=argparse.RawDescriptionHelpFormatter,
                       epilog="Коды выхода: 0 - готово (0 статейных URL - предупреждение в stderr); "
                              "3 - сайт не читается (антибот, пусто, HTTP-ошибка, редирект на чужой домен).")
    s.add_argument("url")
    s.add_argument("--out", required=True, help="куда записать JSON (обычно work/site.json)")
    s.add_argument("--content-path", action="append", metavar="/ПУТЬ",
                   help="раздел статей, например /blog или /idei-i-trendy; можно несколько раз. "
                        "По умолчанию - project.content_path из statejnik.yaml, если сканируется домен проекта")
    s.add_argument("--max-urls", type=int, default=None,
                   help=f"сколько НЕстатейных URL из карты сайта сохранить (по умолч. {DEFAULT_MAX_URLS} "
                        "или tools.site.max_urls). Статейные URL собираются всегда")
    s.add_argument("--max-pages", type=int, default=None,
                   help=f"сколько страниц открыть ради заголовков, статьи - первыми (по умолч. {DEFAULT_MAX_PAGES} "
                        "или tools.site.max_pages). Для gap нужны заголовки всех статей")
    s.add_argument("--workers", type=int, default=DEFAULT_WORKERS,
                   help=f"сколько страниц читать параллельно (по умолч. {DEFAULT_WORKERS}, максимум 16)")
    s.add_argument("--page-timeout", type=int, default=PAGE_TIMEOUT,
                   help=f"таймаут одной страницы, с (по умолч. {PAGE_TIMEOUT})")
    s.add_argument("--allow-private", action="store_true", help="разрешить внутренние адреса (локальный сайт)")
    s.add_argument("--config", default=None, help="путь к statejnik.yaml (по умолч. ./statejnik.yaml)")
    g = sub.add_parser("gap", help="сверить ключи с сайтом и собрать план")
    g.add_argument("site")
    g.add_argument("keywords")
    g.add_argument("--out", required=True, help="куда записать gap.json ({clusters, commercial, ...})")
    g.add_argument("--plan", help="куда записать план в Markdown")
    g.add_argument("--top", type=int, default=30, help="сколько тем в план (по умолч. 30)")
    g.add_argument("--threshold", type=float, default=0.75,
                   help="доля основ запроса в заголовке/адресе статьи, при которой тема считается покрытой "
                        "(по умолч. 0.75)")
    g.add_argument("--maybe", type=float, default=0.6,
                   help="с какой оценки тема попадает в раздел «похоже на занятые» вместо плана (по умолч. 0.6)")
    g.add_argument("--config", default=None, help="путь к statejnik.yaml (для project.brands)")
    a = p.parse_args()
    cmd_scan(a) if a.cmd == "scan" else cmd_gap(a)


if __name__ == "__main__":
    main()
