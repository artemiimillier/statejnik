#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Сайт: чтение главной и карты сайта, проверка покрытия ключей, черновой план статей.

    python3 site.py scan https://example.com --out work/site.json
    python3 site.py gap work/site.json work/keywords.json --out work/gap.json --plan work/plan.md

scan - главная (title, description, заголовки, текст) + все URL из sitemap.xml
       (включая вложенные индексы) + заголовки страниц (до --max-pages).
gap  - для каждого кластера ключей ищет страницу сайта, которая уже его покрывает
       (по основам слов в title/H1/URL). Непокрытые кластеры с высокой частотой и
       информационным интентом - кандидаты в план.
Конкурентов тем же `scan` можно прочитать: python3 site.py scan https://competitor.ru ...
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import urllib.parse
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _net import request, decode_body  # noqa: E402
from _ru import stems, slug_tokens, translit, latin_skeleton, prefix_match  # noqa: E402


def _get(url, allow_private=False):
    try:
        status, headers, raw = request("GET", url, timeout=25, allow_private=allow_private,
                                       headers={"Accept": "text/html,application/xml;q=0.9,*/*;q=0.8"})
    except Exception as e:  # сеть, DNS, SSL
        print(f"  ! {url}: {e}", file=sys.stderr)
        return None, ""
    return status, decode_body(raw[:3_000_000], headers)


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
    body = _text(doc)
    return {"title": title, "description": desc, "h1": h1, "headings": heads, "text": body[:6000]}


def sitemap_urls(base, limit, allow_private):
    seen, out = set(), []
    queue = [urllib.parse.urljoin(base, "/sitemap.xml")]
    status, robots = _get(urllib.parse.urljoin(base, "/robots.txt"), allow_private)
    if status == 200:
        queue = [l.split(":", 1)[1].strip() for l in robots.splitlines()
                 if l.lower().startswith("sitemap:")] or queue
    while queue and len(out) < limit:
        sm = queue.pop(0)
        if sm in seen:
            continue
        seen.add(sm)
        status, body = _get(sm, allow_private)
        if status != 200 or not body.strip():
            continue
        try:
            root = ET.fromstring(body.encode("utf-8"))
        except ET.ParseError:
            continue
        tag = root.tag.split("}")[-1]
        locs = [e.text.strip() for e in root.iter() if e.tag.split("}")[-1] == "loc" and e.text]
        if tag == "sitemapindex":
            queue.extend(locs)
        else:
            out.extend(locs)
    return out[:limit]


def cmd_scan(a):
    base = a.url if "://" in a.url else "https://" + a.url
    status, doc = _get(base, a.allow_private)
    if status is None or status >= 400:
        raise SystemExit(f"не открылась главная {base} (HTTP {status})")
    home = parse_page(doc)
    urls = sitemap_urls(base, a.max_urls, a.allow_private)
    pages = []
    if urls and a.max_pages:
        # сначала - то, что похоже на статьи
        rx = re.compile(r"/(blog|articles?|stat|news|guides?|journal|poleznoe|sovety|info|wiki)/", re.I)
        ordered = sorted(urls, key=lambda u: 0 if rx.search(u) else 1)
        for u in ordered[: a.max_pages]:
            st, d = _get(u, a.allow_private)
            if st == 200:
                pg = parse_page(d)
                pages.append({"url": u, "title": pg["title"], "h1": pg["h1"]})
    result = {"url": base, "home": home, "sitemap_count": len(urls), "urls": urls, "pages": pages}
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print(f"{base}: «{home['title'][:80]}». URL в карте сайта: {len(urls)}, прочитано страниц: {len(pages)}. → {a.out}")


def _page_covers(phrase_stems, page):
    words = stems(" ".join([page.get("title", ""), page.get("h1", "")]))
    if phrase_stems and len(phrase_stems & words) / len(phrase_stems) >= 0.75:
        return True
    slug = slug_tokens(urllib.parse.urlsplit(page["url"]).path)
    lat = [latin_skeleton(translit(s)) for s in phrase_stems]
    hit = sum(1 for s in lat if any(prefix_match(s, t, 4) for t in slug))
    return bool(lat) and hit / len(lat) >= 0.75


def cmd_gap(a):
    site = json.load(open(a.site, encoding="utf-8"))
    kws = json.load(open(a.keywords, encoding="utf-8"))
    pages = site.get("pages") or []
    known = {p["url"] for p in pages}
    pages += [{"url": u, "title": "", "h1": ""} for u in site.get("urls", []) if u not in known]
    clusters = {}
    for k in kws:
        c = clusters.setdefault(k["cluster"], {"cluster": k["cluster"], "phrases": [], "volume": 0,
                                                "fit": 0.0, "type": k.get("article_type"), "sources": set()})
        c["phrases"].append(k["phrase"])
        c["volume"] += k.get("count") or 0
        c["fit"] = max(c["fit"], k.get("article_fit", 0.7))
        c["sources"].update(k.get("sources", []))
    rows = []
    for c in clusters.values():
        st = stems(c["cluster"])
        cover = next((p["url"] for p in pages if _page_covers(st, p)), None)
        demand = c["volume"] if c["volume"] else 10 * len(c["sources"]) * len(c["phrases"])
        score = round(demand * c["fit"] * (0.15 if cover else 1.0), 1)
        rows.append({"cluster": c["cluster"], "phrases": c["phrases"][:12], "volume": c["volume"] or None,
                     "article_type": c["type"], "fit": c["fit"], "covered_by": cover, "score": score})
    rows.sort(key=lambda r: -r["score"])
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    todo = [r for r in rows if not r["covered_by"] and r["fit"] >= 0.7][: a.top]
    covered = sum(1 for r in rows if r["covered_by"])
    print(f"Кластеров: {len(rows)}, уже покрыты сайтом: {covered}, в план: {len(todo)}. → {a.out}")
    if a.plan:
        lines = ["# План статей (черновик)", "",
                 f"Сайт: {site['url']}. Кластеров: {len(rows)}, уже есть на сайте: {covered}.",
                 "Частота - Вордстат за 30 дней; «-» значит частоты нет (только подсказки).", "",
                 "| # | Тема (главный запрос) | Тип | Частота | Доп. запросы |", "|---|---|---|---|---|"]
        for i, r in enumerate(todo, 1):
            extra = "; ".join(p for p in r["phrases"][1:5])
            lines.append(f"| {i} | {r['cluster']} | {r['article_type']} | {r['volume'] or '-'} | {extra} |")
        lines += ["", "## Уже покрыто сайтом (не дублировать, можно обновить)", ""]
        lines += [f"- {r['cluster']} → {r['covered_by']}" for r in rows if r["covered_by"]][:30]
        with open(a.plan, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        print(f"План: {a.plan}")


def main():
    p = argparse.ArgumentParser(description="Сайт: чтение, покрытие ключей, план")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan")
    s.add_argument("url")
    s.add_argument("--out", required=True)
    s.add_argument("--max-urls", type=int, default=5000)
    s.add_argument("--max-pages", type=int, default=60, help="сколько страниц открыть ради заголовков")
    s.add_argument("--allow-private", action="store_true", help="разрешить внутренние адреса (локальный сайт)")
    g = sub.add_parser("gap")
    g.add_argument("site")
    g.add_argument("keywords")
    g.add_argument("--out", required=True)
    g.add_argument("--plan", help="куда записать план в Markdown")
    g.add_argument("--top", type=int, default=30)
    a = p.parse_args()
    cmd_scan(a) if a.cmd == "scan" else cmd_gap(a)


if __name__ == "__main__":
    main()
