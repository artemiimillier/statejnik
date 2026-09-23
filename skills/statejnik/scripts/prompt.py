#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""prompt.py - собрать готовый промпт независимой роли из templates/prompts/.

    python3 prompt.py review       --slug <slug> --round 1 > work/<slug>/_prompts/review-1.md
    python3 prompt.py review       --slug <slug> --round 2 > work/<slug>/_prompts/review-2.md
    python3 prompt.py final-editor --slug <slug> --round 1 > work/<slug>/_prompts/final-editor-1.md
    python3 prompt.py final-verify --slug <slug> --round 1 > work/<slug>/_prompts/final-verify-1.md
    python3 prompt.py select       --slug <slug>           > work/<slug>/_prompts/select-1.md

Берёт текст шаблона ниже строки «==== ПРОМПТ ====» и подставляет:
  {{WORKDIR}}    - рабочая папка (текущая, абсолютный путь);
  {{SLUG}}       - папка статьи work/<slug>;
  {{ROUND}}      - круг проверки (review) или итерация финальной вычитки (final-*);
  {{PREV_ROUND}} - ROUND - 1;
  {{SKILL_DIR}}  - папка навыка;
  {{CONFIG}}     - фрагмент statejnik.yaml (project, audience, voice, cta, editorial) - как есть,
                   без publish/report/tools и без ключей (ключи лежат в .env, не в конфиге);
  {{CARDS}}, {{SERP}} - только select: пути карточек work/*/card.md и матриц выдачи.
Блок {{#REPEAT}} ... {{/REPEAT}} остаётся только при ROUND >= 2, иначе вырезается целиком.

Материалы в промпт не вклеиваются: в нём пути к файлам, субагент читает их сам.
Скрипт проверяет, что нужные файлы на месте (черновик, brief, claims, вывод check-all
для круга, прошлый вердикт и fixes на повторном круге, diff), и печатает в stderr, чего
не хватает. Для final-verify он останавливается, если в промпт попала ссылка на editor.md
(объяснения редактора сверяющему не передаются).

Коды выхода: 0 - промпт напечатан (предупреждения о нехватке файлов - в stderr);
1 - только с --strict: не хватает обязательных материалов (промпт всё равно напечатан);
2 - ошибка аргументов, нет шаблона или нарушена изоляция.
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from _config import find_config  # noqa: E402

TEMPLATES = os.path.join(SKILL_DIR, "templates", "prompts")
SPLIT = "==== ПРОМПТ ===="
CONFIG_SECTIONS = ("project", "audience", "voice", "cta", "editorial")
ROLES = ("review", "final-editor", "final-verify", "select")


def config_fragment(path, sections=CONFIG_SECTIONS):
    """Нужные верхнеуровневые разделы statejnik.yaml как есть (с комментариями)."""
    if not path or not os.path.isfile(path):
        return "# statejnik.yaml не найден - спросите автора о голосе, аудитории и запретах"
    out, keep = [], False
    for line in open(path, encoding="utf-8").read().splitlines():
        m = re.match(r"^([A-Za-z_][\w-]*)\s*:", line)
        if m:
            keep = m.group(1) in sections
        elif line.strip() and not line.startswith((" ", "\t", "#")):
            keep = False
        if keep:
            out.append(line.rstrip())
    while out and not out[-1].strip():
        out.pop()
    return "\n".join(out) or "# в statejnik.yaml нет разделов " + ", ".join(sections)


def render(text, values, rnd):
    if rnd >= 2:
        text = re.sub(r"\{\{#REPEAT\}\}\n?", "", text)
        text = re.sub(r"\{\{/REPEAT\}\}\n?", "", text)
    else:
        text = re.sub(r"\{\{#REPEAT\}\}.*?\{\{/REPEAT\}\}\n?", "", text, flags=re.S)
    for k, v in values.items():
        text = text.replace("{{" + k + "}}", v)
    return text


def required_files(role, slug, rnd):
    """[(путь, обязателен?)] - что должен увидеть субагент."""
    w = os.path.join("work", slug)
    if role == "review":
        req = [(f"{w}/draft.md", True), (f"{w}/brief.md", True), (f"{w}/claims.json", True),
               (f"{w}/sources", True), (f"{w}/checks/{rnd}/summary.md", True)]
        if rnd >= 2:
            req += [(f"{w}/review-{rnd - 1}.md", True), (f"{w}/fixes-{rnd - 1}.md", True),
                    (f"{w}/checks/{rnd}/diff.txt", True)]
        return req
    it = f"{w}/final-review/iteration-{rnd}"
    if role == "final-editor":
        req = [(f"{w}/draft.md", True), (f"{w}/brief.md", True), (f"{w}/claims.json", True),
               (f"{it}/diagnostics-before.txt", True)]
        if rnd >= 2:
            req.append((f"{w}/final-review/iteration-{rnd - 1}/blockers.md", True))
        return req
    if role == "final-verify":
        return [(f"{w}/draft.md", True), (f"{it}/candidate.md", True), (f"{w}/brief.md", True),
                (f"{w}/claims.json", True), (f"{it}/diagnostics-before.txt", True),
                (f"{it}/diagnostics-after.txt", True)]
    return [("work/plan.md", True)]


def main():
    ap = argparse.ArgumentParser(description="Собрать промпт независимой роли из шаблона",
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("role", choices=ROLES, help="шаблон из templates/prompts/")
    ap.add_argument("--slug", required=True, help="папка статьи work/<slug>")
    ap.add_argument("--round", type=int, default=1, help="круг проверки или итерация финальной вычитки (1-3)")
    ap.add_argument("--config", default=None, help="путь к statejnik.yaml (по умолч. ./statejnik.yaml)")
    ap.add_argument("--strict", action="store_true", help="exit 1, если не хватает обязательных материалов")
    a = ap.parse_args()

    if not re.fullmatch(r"[\w.-]+", a.slug):
        print(f"--slug: недопустимое имя {a.slug!r}", file=sys.stderr)
        sys.exit(2)
    if not 1 <= a.round <= 3:
        print("--round: от 1 до 3 (четвёртого круга нет)", file=sys.stderr)
        sys.exit(2)
    tpl = os.path.join(TEMPLATES, a.role + ".md")
    raw = open(tpl, encoding="utf-8").read()
    if SPLIT not in raw:
        print(f"{tpl}: нет строки «{SPLIT}»", file=sys.stderr)
        sys.exit(2)
    body = raw.split(SPLIT, 1)[1].lstrip("\n")

    cfg = find_config(a.config)
    if not cfg:
        print("предупреждение: statejnik.yaml не найден - фрагмент конфига будет пустым; "
              "запускайте из рабочей папки", file=sys.stderr)
    values = {
        "WORKDIR": os.path.abspath(os.getcwd()), "SLUG": a.slug, "ROUND": str(a.round),
        "PREV_ROUND": str(a.round - 1), "SKILL_DIR": SKILL_DIR, "CONFIG": config_fragment(cfg),
    }
    if a.role == "select":
        accepted = {os.path.basename(os.path.dirname(p)) for p in glob.glob("work/*/accepted.md")}
        cards = [p for p in sorted(glob.glob("work/*/card.md"))
                 if os.path.basename(os.path.dirname(p)) not in accepted]
        serp = sorted(glob.glob("work/competitors/serp-*"))
        values["CARDS"] = ", ".join(f"`{p}`" for p in cards) or "карточек нет"
        values["SERP"] = ", ".join(f"`{p}`" for p in serp) or "нет"
    text = render(body, values, a.round)

    left = re.findall(r"\{\{[^}]+\}\}", text)
    if left:
        print(f"в шаблоне остались неизвестные плейсхолдеры: {', '.join(sorted(set(left)))}", file=sys.stderr)
        sys.exit(2)
    if a.role == "final-verify" and re.search(r"editor\.md|editor-response", text):
        print("изоляция нарушена: промпт сверяющего ссылается на разбор редактора (editor.md) - "
              "уберите ссылку из шаблона", file=sys.stderr)
        sys.exit(2)

    missing = [p for p, must in required_files(a.role, a.slug, a.round) if must and not os.path.exists(p)]
    for p in missing:
        hint = ""
        if "/checks/" in p and p.endswith("summary.md"):
            hint = " - прогоните check-all.py --round " + str(a.round)
        elif p.endswith("diff.txt"):
            hint = " - diff -u прошлой и новой версии"
        print(f"предупреждение: нет {p}{hint}", file=sys.stderr)
    sys.stdout.write(text if text.endswith("\n") else text + "\n")
    sys.exit(1 if (missing and a.strict) else 0)


if __name__ == "__main__":
    main()
