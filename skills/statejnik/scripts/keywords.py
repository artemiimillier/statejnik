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
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _net import load_env, request, env  # noqa: E402
from _ru import stems, normalize, intent, ARTICLE_FIT, article_type  # noqa: E402
from _config import find_config, load_config, get  # noqa: E402

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
    p = argparse.ArgumentParser(description="Сбор семантического ядра")
    p.add_argument("seeds", nargs="*", help="стартовые фразы (маркеры ниши)")
    p.add_argument("--seeds-file", help="файл со стартовыми фразами, по одной в строке")
    p.add_argument("--region", action="append", help="ID региона Вордстата (213 - Москва, 225 - Россия); можно несколько")
    p.add_argument("--no-suggest", action="store_true", help="не брать поисковые подсказки")
    p.add_argument("--expand", action="store_true", help="расширять подсказки вопросами (как/что/какой...)")
    p.add_argument("--brand", action="append", default=[], help="бренды (запросы с ними - навигационные)")
    p.add_argument("--config", default=None)
    p.add_argument("--out", required=True, help="куда сохранить JSON")
    a = p.parse_args()

    load_env()
    cfg = load_config(find_config(a.config))
    seeds = list(a.seeds)
    if a.seeds_file:
        with open(a.seeds_file, encoding="utf-8") as f:
            seeds += [l.strip() for l in f if l.strip() and not l.startswith("#")]
    if not seeds:
        seeds = list(get(cfg, "seo.seeds", []) or [])
    if not seeds:
        raise SystemExit("нет стартовых фраз: передайте аргументами, --seeds-file или seo.seeds в statejnik.yaml")
    regions = a.region or [str(r) for r in (get(cfg, "seo.regions", []) or [])]
    brands = a.brand + list(get(cfg, "project.brands", []) or [])

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
    for it in items:
        it["intent"] = intent(it["phrase"], brands)
        it["article_fit"] = ARTICLE_FIT[it["intent"]]
        it["article_type"] = article_type(it["phrase"])
    items = cluster(items)
    items.sort(key=lambda x: (-(x["count"] or 0), -len(x["sources"])))

    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=1)
    n_cl = len({i["cluster"] for i in items})
    fit = sum(1 for i in items if i["article_fit"] >= 0.7)
    print(f"Фраз: {len(items)}, кластеров: {n_cl}, под статью подходят: {fit}. Сохранено: {a.out}")


if __name__ == "__main__":
    main()
