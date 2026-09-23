#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Сбор семантического ядра: Вордстат (если есть токен) + подсказки Яндекса и Google.

Примеры:
    python3 keywords.py "как выбрать диван" "диван для сна" --out work/keywords.json
    python3 keywords.py --seeds-file seeds.txt --region 213 --out work/keywords.json

Источники:
  wordstat  - официальный API Вордстата (api.wordstat.yandex.net), нужен WORDSTAT_TOKEN
              в .env. Даёт частоту за 30 дней. Без токена пропускается.
  suggest   - поисковые подсказки Яндекса и Google. Без ключей, без частоты,
              зато показывают живые формулировки людей.

Результат: JSON-список фраз с полями phrase, count (или null), sources, intent,
article_fit, article_type, cluster. Секреты не печатаются.

Фильтр мусора (отсеянное пишется в <out>.excluded.json с причиной):
  - кроссворды/сканворды/«N букв»/«ответ»;
  - «скачать», «бесплатно скачать», торренты;
  - запросы только за фото/картинками/видео («диван фото», «картинки диван») -
    «своими руками», «как ... видео» с задачей остаются;
  - другие страны и их города (минск, беларусь, казахстан, украина...), если
    project.region - Россия (или не задан);
  - бренды и исключения: --brand, --exclude, project.brands, домены из
    seo.competitors (mebelion.ru → mebelion), seo.exclude, editorial.banned_words.
    Сравнение по границам слов и основам: «аскона» ловит «асконы», но не «маскона».
  --keep-brands оставляет брендовые запросы в ядре с intent=navigational.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import re
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _net import load_env, request, env  # noqa: E402
from _ru import stems, normalize, intent, ARTICLE_FIT, article_type, contains_term, foreign_geo  # noqa: E402
from _config import find_config, load_config, load_for, get, as_list  # noqa: E402

WORDSTAT_URL = "https://api.wordstat.yandex.net/v1/topRequests"


def wordstat(phrase, token, regions=None, limit=50):
    body = {"phrase": phrase}
    if regions:
        body["regions"] = [int(r) for r in regions]
    try:
        status, _, raw = request("POST", env("WORDSTAT_API_URL") or WORDSTAT_URL, body=body, timeout=30,
                                 headers={"Authorization": "Bearer " + token})
    except Exception as e:  # сеть, DNS, сертификат
        print(f"  ! Вордстат недоступен ({type(e).__name__}): {str(e)[:160]}. Продолжаю с подсказками.", file=sys.stderr)
        return []
    if status == 401 or status == 403:
        raise SystemExit("Вордстат отклонил токен (HTTP %d). Проверьте WORDSTAT_TOKEN в .env." % status)
    if status == 429:
        time.sleep(2)
        return []
    if status != 200:
        print(f"  ! Вордстат HTTP {status} для «{phrase}»", file=sys.stderr)
        return []
    data = json.loads(raw.decode("utf-8", "replace") or "{}")
    rows = data.get("topRequests") or []
    out = []
    for r in rows[:limit]:
        try:
            out.append((r["phrase"], int(r["count"])))
        except (KeyError, ValueError, TypeError):
            continue
    for r in (data.get("associations") or [])[:limit // 2]:
        try:
            out.append((r["phrase"], int(r["count"])))
        except (KeyError, ValueError, TypeError):
            continue
    return out


def suggest_yandex(phrase):
    q = urllib.parse.quote(phrase)
    status, _, raw = request("GET", f"https://suggest.yandex.ru/suggest-ff.cgi?part={q}&uil=ru&v=3&sn=10", timeout=15)
    if status != 200:
        return []
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
        return [s for s in data[1] if isinstance(s, str)]
    except (ValueError, IndexError, TypeError):
        return []


def suggest_google(phrase):
    q = urllib.parse.quote(phrase)
    status, _, raw = request("GET", f"https://suggestqueries.google.com/complete/search?client=firefox&hl=ru&q={q}", timeout=15)
    if status != 200:
        return []
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
        return [s for s in data[1] if isinstance(s, str)]
    except (ValueError, IndexError, TypeError):
        return []


EXPANSIONS = ("как", "что", "какой", "почему", "сколько", "лучше")

_JUNK = (
    (re.compile(r"(?<!\w)(кроссворд\w*|сканворд\w*|\d+\s*букв\w*|букв|ответ|ответы|разгадк\w*)(?!\w)"),
     "кроссворд/ответы"),
    (re.compile(r"(?<!\w)(скачать|скачай|торрент\w*|torrent|pdf|djvu|mp3)(?!\w)"), "скачать"),
    (re.compile(r"(?<!\w)(текст песни|аккорды|раскраск\w*|сонник\w*|приснил\w*|снится|гдз|реферат\w*)(?!\w)"),
     "не по теме"),
)
_MEDIA = re.compile(r"(?<!\w)(фото\w*|фотк\w*|картинк\w*|изображени\w*|видео\w*|ютуб|youtube|смотреть|png|jpe?g)(?!\w)")
_TASK = re.compile(r"(?<!\w)(как|своими руками|почему|зачем|что|какой|какая|какие|чем|сколько|можно ли|"
                   r"инструкц\w*|схем\w*|чертеж\w*|размер\w*|идеи|пример\w*|ремонт\w*|сборк\w*|собрать|"
                   r"разобрать|разложить|сложить|почистить|отличи\w*|выбрать|выбор)(?!\w)")


def brand_from_domain(domain):
    """mebelion.ru → mebelion; www.shop.ru → shop; andrea-mebel.ru → andrea mebel."""
    d = str(domain).lower().strip()
    d = re.sub(r"^[a-z]+://", "", d).split("/")[0]
    parts = [p for p in d.split(".") if p and p != "www"]
    if len(parts) >= 2:
        parts = parts[:-1]
    name = parts[-1] if parts else d
    return name.replace("-", " ").strip()


def junk_reason(phrase, excludes, region="Россия"):
    """Причина исключения запроса или None."""
    p = normalize(phrase)
    for rx, why in _JUNK:
        if rx.search(p):
            return why
    if _MEDIA.search(p):
        rest = _MEDIA.sub(" ", p)
        if not _TASK.search(rest):
            return "только фото/видео"
    reg = str(region or "Россия").lower()
    if reg in ("россия", "рф", "russia", "ru", "225") and foreign_geo(p):
        return "другая страна"
    for term in excludes:
        if contains_term(term, p):
            return f"бренд/исключение: {term}"
    return None


def cluster(items):
    """Жадная кластеризация по пересечению основ слов (Жаккар >= 0.5)."""
    clusters = []
    for it in sorted(items, key=lambda x: -(x["count"] or 0)):
        s = stems(it["phrase"])
        best = None
        for c in clusters:
            inter = len(s & c["stems"])
            union = len(s | c["stems"]) or 1
            if inter / union >= 0.5:
                best = c
                break
        if best is None:
            best = {"head": it["phrase"], "stems": set(s), "n": 0}
            clusters.append(best)
        it["cluster"] = best["head"]
        best["n"] += 1
    return items


def main():
    p = argparse.ArgumentParser(description="Сбор семантического ядра: Вордстат + подсказки Яндекса и Google, "
                                            "с фильтром мусора, брендов и чужих регионов",
                                formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    p.add_argument("seeds", nargs="*", help="стартовые фразы (маркеры ниши)")
    p.add_argument("--seeds-file", help="файл со стартовыми фразами, по одной в строке")
    p.add_argument("--region", action="append", help="ID региона Вордстата (213 - Москва, 225 - Россия); можно несколько")
    p.add_argument("--no-suggest", action="store_true", help="не брать поисковые подсказки")
    p.add_argument("--expand", action="store_true", help="расширять подсказки вопросами (как/что/какой...)")
    p.add_argument("--brand", action="append", default=[],
                   help="бренд (свой или конкурента); запросы с ним исключаются из ядра. Можно много раз. "
                        "Дополняет project.brands и домены seo.competitors")
    p.add_argument("--exclude", action="append", default=[],
                   help="слово/фраза для исключения (по основам и границам слов). Можно много раз. "
                        "Дополняет seo.exclude и editorial.banned_words")
    p.add_argument("--keep-brands", action="store_true",
                   help="не выкидывать брендовые запросы, а оставить с intent=navigational")
    p.add_argument("--no-filter", action="store_true", help="не фильтровать мусор (кроссворды, скачать, чужие страны)")
    p.add_argument("--config", default=None)
    p.add_argument("--out", required=True, help="куда сохранить JSON")
    a = p.parse_args()

    load_env()
    _, cfg = load_for(a.config, near=a.out)
    seeds = list(a.seeds)
    if a.seeds_file:
        with open(a.seeds_file, encoding="utf-8") as f:
            seeds += [l.strip() for l in f if l.strip() and not l.startswith("#")]
    if not seeds:
        seeds = list(get(cfg, "seo.seeds", []) or [])
    if not seeds:
        raise SystemExit("нет стартовых фраз: передайте аргументами, --seeds-file или seo.seeds в statejnik.yaml")
    regions = a.region or [str(r) for r in (get(cfg, "seo.regions", []) or [])]
    brands = a.brand + as_list(get(cfg, "project.brands", []))
    brands += [brand_from_domain(d) for d in as_list(get(cfg, "seo.competitors", []))]
    own = str(get(cfg, "project.domain", "") or "")
    if own:
        brands.append(brand_from_domain(own))
    brands = list(dict.fromkeys(b.strip().lower() for b in brands if b and b.strip()))
    # бренд, который встречается в самих стартовых фразах (домен диван.рф → «диван»), ядро не режет
    clash = [b for b in brands if any(contains_term(b, s) for s in seeds)]
    if clash:
        print("Не исключаю (есть в стартовых фразах): " + ", ".join(clash), file=sys.stderr)
        brands = [b for b in brands if b not in clash]
    excludes = list(dict.fromkeys(x.strip().lower() for x in
                                  a.exclude + as_list(get(cfg, "seo.exclude", []))
                                  + as_list(get(cfg, "editorial.banned_words", [])) if x and x.strip()))
    region = get(cfg, "project.region", "Россия")

    token = env("WORDSTAT_TOKEN")
    found = {}

    def add(phrase, count, source):
        key = normalize(phrase)
        if not key or len(key) > 120:
            return
        it = found.setdefault(key, {"phrase": key, "count": None, "sources": []})
        if count is not None:
            it["count"] = max(it["count"] or 0, count)
        if source not in it["sources"]:
            it["sources"].append(source)

    print(f"Стартовых фраз: {len(seeds)}. Вордстат: {'да' if token else 'нет токена - только подсказки'}.", file=sys.stderr)
    for seed in seeds:
        if token:
            for ph, cnt in wordstat(seed, token, regions):
                add(ph, cnt, "wordstat")
            time.sleep(0.4)
        if not a.no_suggest:
            queries = [seed] + ([f"{w} {seed}" for w in EXPANSIONS] if a.expand else [])
            for q in queries:
                for s in suggest_yandex(q):
                    add(s, None, "yandex_suggest")
                for s in suggest_google(q):
                    add(s, None, "google_suggest")
                time.sleep(0.3)

    items = list(found.values())
    excluded = []
    if not a.no_filter:
        kept = []
        stop = excludes + ([] if a.keep_brands else brands)
        seed_norm = {normalize(x) for x in seeds}
        for it in items:
            why = None if it["phrase"] in seed_norm else junk_reason(it["phrase"], stop, region)
            if why:
                excluded.append({"phrase": it["phrase"], "reason": why, "sources": it["sources"]})
            else:
                kept.append(it)
        items = kept
    for it in items:
        it["intent"] = intent(it["phrase"], brands)
        it["article_fit"] = ARTICLE_FIT[it["intent"]]
        it["article_type"] = article_type(it["phrase"])
    items = cluster(items)
    items.sort(key=lambda x: (-(x["count"] or 0), -len(x["sources"])))

    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=1)
    ex_path = os.path.splitext(a.out)[0] + ".excluded.json"
    with open(ex_path, "w", encoding="utf-8") as f:
        json.dump(excluded, f, ensure_ascii=False, indent=1)
    if excluded:
        why = {}
        for e in excluded:
            k = e["reason"].split(":")[0]
            why[k] = why.get(k, 0) + 1
        print("Отсеяно: " + ", ".join(f"{k} - {v}" for k, v in why.items()) + f". Список: {ex_path}", file=sys.stderr)
    n_cl = len({i["cluster"] for i in items})
    fit = sum(1 for i in items if i["article_fit"] >= 0.7)
    print(f"Фраз: {len(items)}, кластеров: {n_cl}, под статью подходят: {fit}. Сохранено: {a.out}")


if __name__ == "__main__":
    main()
