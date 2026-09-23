#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
structure-check.py - механическая проверка структуры черновика статьи.

Проверяет то, что ловится без чтения смысла (дополняет ai-cadence-check.py):

  ОШИБКИ (exit 1):
  1. Заголовок страницы (H1). Засчитывается ЛЮБОЙ из вариантов:
       - `title` во frontmatter (площадка рисует H1 сама) - H1 в теле не нужен;
       - ровно одна строка `# Заголовок` в теле.
     Ошибка: нет ни title, ни H1; H1 в теле больше одного. Если есть и title,
     и H1 в теле, и они различаются - замечание (публикуется title, H1 тела
     выбрасывается при отправке, см. publish.py).
  2. Есть хотя бы один H2, и у каждого H2 есть контент до следующего H2.
  3. CTA: ссылка на оффер (cta.url из statejnik.yaml) есть в тексте или в
     frontmatter (cta_url). Без cta.url - хотя бы одна внешняя ссылка.
  4. У всех картинок ![alt](url) непустой alt.
  5. Внутренние ссылки на РАЗНЫЕ страницы сайта (относительные /путь или домен
     project.domain; ссылка на cta.url и якоря не считаются): не меньше
     tools.structure.min_internal_links (по умолч. 3, как в 05-write).
     --min-internal N перебивает конфиг (0 - не проверять), если подходящих
     опубликованных статей меньше и причина записана в чек-лист.
  6. Запретные слова: editorial.banned_words и voice.forbidden в публичном тексте
     (тело + title, title_variants, meta_title, meta_description, excerpt,
     hero_promise, подписи). Поиск по границам слов и основам: «руб» не находится
     в «грубой», «аскона» находится в «асконы». Код, адреса ссылок и cta_url
     не проверяются.

  ЗАМЕЧАНИЯ (exit 0):
  - нет раздела «Источники» (квоты на источники нет; раздел нужен, только если
    в статье есть внешние опоры для читателя);
  - title и H1 тела различаются.

Коды выхода: 0 - ошибок нет (замечания возможны); 1 - есть ошибки; 2 - нет файла.
Использование:
  python3 scripts/structure-check.py work/<slug>/draft.md
  python3 scripts/structure-check.py work/<slug>/final.md --min-internal 2
  python3 scripts/structure-check.py draft.md --config statejnik.yaml --json
"""
import argparse
import json
import os
import re
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _config import load_config, find_config, get, as_list  # noqa: E402
from _article import split_frontmatter  # noqa: E402
from _ru import term_matches  # noqa: E402

DEFAULT_MIN_INTERNAL = 3
PUBLIC_FIELDS = ("title", "title_variants", "h1_variants", "meta_title", "meta_description", "excerpt",
                 "hero_promise", "description", "subtitle", "lead", "cta_offer", "caption", "tags")


def strip_code_fences(text):
    """Убрать fenced-блоки ``` ``` - в коде свои # и ## не считаем заголовками."""
    return re.sub(r"(?ms)^\s*(```|~~~).*?^\s*\1[^\n]*$", "", text)


def _flatten(v):
    if isinstance(v, dict):
        return " \n".join(_flatten(x) for x in v.values())
    if isinstance(v, (list, tuple)):
        return " \n".join(_flatten(x) for x in v)
    return "" if v is None else str(v)


def public_text(meta, body):
    """Текст, который увидит читатель: тело без кода и адресов + публичные поля frontmatter."""
    t = strip_code_fences(body)
    t = re.sub(r"`[^`]*`", " ", t)
    t = re.sub(r"<!--.*?-->", " ", t, flags=re.S)
    t = re.sub(r"(!?\[[^\]]*\])\([^)]*\)", r"\1", t)  # адреса ссылок и картинок
    t = re.sub(r"https?://\S+", " ", t)
    t = re.sub(r"<[^>]+>", " ", t)
    fields = [(k, _flatten(meta.get(k))) for k in PUBLIC_FIELDS if meta.get(k)]
    return t, fields


def find_banned(meta, body, terms):
    """[(термин, где, найденная форма, контекст)] по границам слов и основам."""
    body_t, fields = public_text(meta, body)
    hits = []
    for term in terms:
        for where, text in [("тело", body_t)] + [(f"frontmatter.{k}", v) for k, v in fields]:
            for s, e, frag in term_matches(term, text):
                ctx = re.sub(r"\s+", " ", text[max(0, s - 35):e + 35]).strip()
                hits.append((term, where, frag, ctx))
    return hits


def _is_internal(href, domain, content_path):
    if href.startswith("#") or href.startswith("mailto:") or href.startswith("tel:"):
        return False
    if href.startswith("/") and not href.startswith("//"):
        return True
    host = (urllib.parse.urlsplit(href if "://" in href or href.startswith("//") else "//" + href).hostname or "")
    if not domain or not host:
        return False
    d = re.sub(r"^www\.", "", str(domain).lower().split("/")[0].split("://")[-1])
    h = re.sub(r"^www\.", "", host.lower())
    return h == d or h.endswith("." + d)


def check_structure(text, cfg, min_internal=None):
    """Вернуть (errors, warnings, info)."""
    errors, warnings, info = [], [], {}
    meta, body = split_frontmatter(text)
    code_free = strip_code_fences(body)
    lines = code_free.splitlines()

    # 1. H1: title во frontmatter ИЛИ ровно один H1 в теле
    h1 = [ln for ln in lines if re.match(r"^#\s+\S", ln)]
    title = str(meta.get("title") or "").strip()
    info["h1_body"] = len(h1)
    info["title_frontmatter"] = bool(title)
    if len(h1) > 1:
        errors.append(f"H1 в теле несколько ({len(h1)}), допустим один (или ни одного, если есть title во frontmatter)")
    elif not h1 and not title:
        errors.append("нет заголовка страницы: ни title во frontmatter, ни строки `# Заголовок` в теле")
    elif h1 and title:
        h1_text = h1[0].lstrip("# ").strip()
        if h1_text.strip(" .!?").lower() != title.strip(" .!?").lower():
            warnings.append(f"H1 тела («{h1_text[:60]}») отличается от title («{title[:60]}»): "
                            "площадка покажет title, H1 тела при отправке выбрасывается")

    # 2. H2
    h2_idx = [i for i, ln in enumerate(lines) if re.match(r"^##\s+\S", ln)]
    for n, i in enumerate(h2_idx):
        end = h2_idx[n + 1] if n + 1 < len(h2_idx) else len(lines)
        if not "\n".join(lines[i + 1:end]).strip():
            errors.append(f"пустой раздел H2: «{lines[i].lstrip('# ').strip()}»")
    if not h2_idx:
        errors.append("нет ни одного H2 (## ) - статья без структуры разделов")

    # замечание: источники
    if not re.search(r"^#{2,3}\s+(источник|sources|литература|ссылки)", code_free, re.I | re.M):
        warnings.append("нет раздела «Источники» (не ошибка: квоты нет; нужен, если читателю полезны первоисточники)")

    # 3. CTA
    cta_url = str(get(cfg, "cta.url", "") or "")
    if cta_url:
        if cta_url not in text:
            errors.append(f"нет ссылки на оффер (cta.url: {cta_url})")
    elif not re.search(r"\]\(https?://", body):
        errors.append("нет ни одной внешней ссылки-CTA (и cta.url не задан в statejnik.yaml)")

    # 4. alt
    for m in re.finditer(r"!\[(.*?)\]\((.*?)\)", code_free):
        if not m.group(1).strip():
            errors.append(f"картинка без alt-текста: ({m.group(2)[:50]})")

    # 5. внутренние ссылки на разные страницы
    if min_internal is None:
        min_internal = int(get(cfg, "tools.structure.min_internal_links", DEFAULT_MIN_INTERNAL))
    domain = get(cfg, "project.domain", "")
    content_path = get(cfg, "project.content_path", "")
    targets = set()
    cta_path = urllib.parse.urlsplit(cta_url).path.rstrip("/") if cta_url else ""
    for m in re.finditer(r"(?<!!)\[[^\]]*\]\(\s*<?([^)\s>]+)", code_free):
        href = m.group(1)
        if not _is_internal(href, domain, content_path):
            continue
        path = (urllib.parse.urlsplit(href).path or "/").rstrip("/") or "/"
        if cta_path and path == cta_path:
            continue  # ссылка на оффер - это CTA, а не перелинковка
        targets.add(path)
    info["internal_links"] = sorted(targets)
    info["min_internal"] = min_internal
    if len(targets) < min_internal:
        errors.append(f"внутренних ссылок на разные страницы сайта {len(targets)}, минимум {min_internal} "
                      "(tools.structure.min_internal_links). Подходящих опубликованных статей меньше - "
                      "запишите причину в чек-лист и запустите с --min-internal N")

    # 6. запретные слова
    terms = list(dict.fromkeys(as_list(get(cfg, "editorial.banned_words", []))
                               + as_list(get(cfg, "voice.forbidden", []))))
    hits = find_banned(meta, body, terms)
    info["banned_hits"] = [{"term": t, "where": w, "found": f, "context": c} for t, w, f, c in hits]
    for t, w, f, c in hits:
        errors.append(f"запретное слово «{t}» ({w}): «{f}» … {c} …")
    return errors, warnings, info


def main():
    p = argparse.ArgumentParser(
        description="Механическая проверка структуры черновика статьи и запретных слов.",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    p.add_argument("draft", help="путь к черновику (.md)")
    p.add_argument("--config", default=None, help="путь к statejnik.yaml (по умолч. ./statejnik.yaml в рабочей папке)")
    p.add_argument("--min-internal", type=int, default=None,
                   help="минимум внутренних ссылок (перебивает tools.structure.min_internal_links; по умолч. 3)")
    p.add_argument("--json", action="store_true", help="вывод JSON")
    args = p.parse_args()

    if not os.path.isfile(args.draft):
        sys.stderr.write(f"нет файла: {args.draft}\n")
        sys.exit(2)
    text = open(args.draft, encoding="utf-8").read()
    cfg = load_config(args.config or find_config())
    errors, warnings, info = check_structure(text, cfg, args.min_internal)

    if args.json:
        print(json.dumps({"file": args.draft, "ok": not errors, "errors": errors, "warnings": warnings, **info},
                         ensure_ascii=False, indent=1))
        sys.exit(1 if errors else 0)
    print(f"Структура: {os.path.basename(args.draft)}  (внутренних ссылок: {len(info['internal_links'])}, "
          f"минимум {info['min_internal']})")
    for e in errors:
        print(f"  ✗ {e}")
    for w in warnings:
        print(f"  ! {w}")
    print("  OK. Ошибок нет." if not errors else f"  Ошибок: {len(errors)} (exit 1)")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
