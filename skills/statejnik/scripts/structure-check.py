#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
structure-check.py - механическая проверка структуры черновика статьи.

Проверяет то, что ловится без чтения смысла (дополняет ai-cadence-check.py,
который ловит AI-почерк):

  1. Ровно один H1 (# ) - заголовок страницы.
  2. У каждого H2 (## ) есть контент до следующего H2 (нет пустых разделов).
  3. Есть раздел «Источники» / «Sources» (доверие + SEO).
  4. Есть хотя бы один CTA - ссылка на ваш оффер (cta.url из statejnik.yaml).
  5. У всех картинок ![alt](url) непустой alt (доступность + SEO).
  6. Минимум внутренних ссылок (tools.structure.min_internal_links, дефолт 1).

Это советующая проверка структуры, а не смысла. Финальное решение - за человеком.

Выход: 0 - структура в порядке; 1 - есть замечания.
Использование:
  python3 scripts/structure-check.py work/<slug>/draft.md
  python3 scripts/structure-check.py draft.md --config statejnik.yaml
"""
import argparse
import os
import re
import sys

try:
    from _config import load_config, find_config, get
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _config import load_config, find_config, get


def strip_code_fences(text):
    """Убрать fenced-блоки ``` ``` - в коде свои # и ## не считаем заголовками."""
    return re.sub(r"```.*?```", "", text, flags=re.DOTALL)


def check_structure(text, cfg):
    issues = []
    body = strip_code_fences(text)
    lines = body.splitlines()

    # 1. Ровно один H1
    h1 = [ln for ln in lines if re.match(r"^#\s+\S", ln)]
    if len(h1) == 0:
        issues.append("нет H1 (строка вида `# Заголовок`)")
    elif len(h1) > 1:
        issues.append(f"H1 несколько ({len(h1)}), должен быть один")

    # 2. H2 без контента
    h2_idx = [i for i, ln in enumerate(lines) if re.match(r"^##\s+\S", ln)]
    for n, i in enumerate(h2_idx):
        end = h2_idx[n + 1] if n + 1 < len(h2_idx) else len(lines)
        chunk = "\n".join(lines[i + 1:end]).strip()
        if not chunk:
            issues.append(f"пустой раздел H2: «{lines[i].lstrip('# ').strip()}»")
    if not h2_idx:
        issues.append("нет ни одного H2 (## ) - статья без структуры разделов")

    # 3. Раздел источников
    if not re.search(r"^#{2,3}\s+(источник|sources)", body, re.I | re.M):
        issues.append("нет раздела «Источники» / «Sources»")

    # 4. CTA на оффер
    cta_url = get(cfg, "cta.url", "")
    if cta_url:
        if cta_url not in text:
            issues.append(f"нет ссылки на оффер (cta.url: {cta_url})")
    else:
        if not re.search(r"\]\(https?://", text):
            issues.append("нет ни одной внешней ссылки-CTA (и cta.url не задан в config)")

    # 5. Alt у картинок
    for m in re.finditer(r"!\[(.*?)\]\((.*?)\)", text):
        if not m.group(1).strip():
            issues.append(f"картинка без alt-текста: ({m.group(2)[:50]})")

    # 6. Внутренние ссылки
    min_internal = int(get(cfg, "tools.structure.min_internal_links", 1))
    domain = get(cfg, "project.domain", "")
    content_path = get(cfg, "project.content_path", "")
    internal = 0
    for m in re.finditer(r"\]\((.*?)\)", text):
        href = m.group(1)
        if href.startswith("/") or (domain and domain in href) or (content_path and content_path in href):
            internal += 1
    if internal < min_internal:
        issues.append(f"внутренних ссылок {internal}, минимум {min_internal} "
                      "(перелинковка на ваши же материалы)")

    return issues


def main():
    p = argparse.ArgumentParser(
        description="Механическая проверка структуры черновика статьи.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Коды выхода: 0 - структура в порядке; 1 - есть замечания.",
    )
    p.add_argument("draft", help="путь к черновику (.md)")
    p.add_argument("--config", default=None, help="путь к statejnik.yaml (по умолч. ./statejnik.yaml в рабочей папке)")
    args = p.parse_args()

    if not os.path.isfile(args.draft):
        sys.stderr.write(f"нет файла: {args.draft}\n")
        sys.exit(2)

    text = open(args.draft, encoding="utf-8").read()
    cfg = load_config(args.config or find_config())
    issues = check_structure(text, cfg)

    print(f"Структура: {os.path.basename(args.draft)}")
    if not issues:
        print("  OK. Структурных замечаний нет.")
        sys.exit(0)
    print(f"  Замечаний: {len(issues)}")
    for it in issues:
        print(f"  ! {it}")
    sys.exit(1)


if __name__ == "__main__":
    main()
