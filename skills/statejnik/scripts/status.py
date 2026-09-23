#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""status.py - в каком режиме Статейник и что уже есть в рабочей папке.

    python3 status.py            # из рабочей папки проекта
    python3 status.py --json

Печатает:
  - режим: setup (первая настройка) или ready (можно писать статьи);
    ready = найден statejnik.yaml и project.domain в нём задан и не example.com
    (комментарии шаблона не в счёт);
  - какой конфиг найден (./statejnik.yaml, $STATEJNIK_CONFIG, черновик work/statejnik.draft.yaml);
  - work/setup-state.md: последняя строка (где остановилась настройка);
  - work/plan.md: есть ли и сколько тем в таблице ещё не опубликовано/не сдано;
  - площадки из publish.targets;
  - статьи в work/<slug>/: черновик, приёмка, финал.

Коды выхода: 0 - ready; 1 - setup.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _config import CONFIG_NAME, find_config, load_config, get, is_configured  # noqa: E402

DONE_WORDS = re.compile(r"опубликован|сдано|сдан |готово|done|published|занято|заблокирован|отклонен", re.I)


def plan_topics(path):
    """(всего тем в таблице плана, осталось). Строка темы: «| <номер> | ...»."""
    total = left = 0
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except OSError:
        return None, None
    for line in lines:
        if not re.match(r"^\|\s*\d+\s*\|", line):
            continue
        total += 1
        if not DONE_WORDS.search(line):
            left += 1
    return total, left


def articles(work="work"):
    out = []
    if not os.path.isdir(work):
        return out
    for d in sorted(os.listdir(work)):
        p = os.path.join(work, d)
        if not os.path.isdir(p) or d.startswith((".", "_")) or d in ("competitors", "demand", "topics", "reports"):
            continue
        has = {f: os.path.isfile(os.path.join(p, f)) for f in ("card.md", "draft.md", "final.md", "accepted.md")}
        if not any(has.values()):
            continue
        stage = ("принято" if has["accepted.md"] and has["final.md"] else "финал" if has["final.md"]
                 else "черновик" if has["draft.md"] else "карточка")
        out.append({"slug": d, "stage": stage, **{k.replace(".md", ""): v for k, v in has.items()}})
    return out


def main():
    ap = argparse.ArgumentParser(description="Режим Статейника и состояние рабочей папки",
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("--config", default=None, help="путь к statejnik.yaml")
    ap.add_argument("--json", action="store_true", help="вывод JSON")
    a = ap.parse_args()

    cfg_path = find_config(a.config)
    cfg = load_config(cfg_path, warn=False) if cfg_path else {}
    draft = os.path.join("work", "statejnik.draft.yaml")
    from_env = bool(os.environ.get("STATEJNIK_CONFIG")) and cfg_path == os.environ.get("STATEJNIK_CONFIG")
    is_root = bool(cfg_path) and os.path.basename(cfg_path) == CONFIG_NAME and not from_env
    ready = is_root and is_configured(cfg)
    setup_state = os.path.join("work", "setup-state.md")
    last_step = None
    if os.path.isfile(setup_state):
        rows = [l.strip() for l in open(setup_state, encoding="utf-8") if l.strip() and not l.startswith("#")]
        last_step = rows[-1] if rows else None
    total, left = plan_topics(os.path.join("work", "plan.md"))
    targets = get(cfg, "publish.targets", {}) or {}
    info = {
        "mode": "ready" if ready else "setup",
        "config": cfg_path, "config_from_env": from_env,
        "draft_config": draft if os.path.isfile(draft) else None,
        "domain": get(cfg, "project.domain", None),
        "setup_state": setup_state if os.path.isfile(setup_state) else None, "setup_last": last_step,
        "plan": os.path.join("work", "plan.md") if total is not None else None,
        "plan_topics": total, "plan_left": left,
        "targets": {k: (v or {}).get("type") for k, v in targets.items()} if isinstance(targets, dict) else {},
        "publish_auto": bool(get(cfg, "publish.auto", False)),
        "articles": articles(),
    }
    if a.json:
        print(json.dumps(info, ensure_ascii=False, indent=1))
        sys.exit(0 if ready else 1)

    print(f"Режим: {'ready - можно писать статьи (режим 2)' if ready else 'setup - первая настройка (режим 1)'}")
    if cfg_path:
        src = "$STATEJNIK_CONFIG" if from_env else "рабочая папка"
        print(f"  конфиг: {cfg_path} ({src}); project.domain: {info['domain'] or 'не задан'}")
        if not ready and is_root:
            print("  ! project.domain пуст или example.com - настройка не закончена")
        if from_env:
            print("  ! конфиг взят из $STATEJNIK_CONFIG (черновик настройки); режим 2 - только с ./statejnik.yaml")
    else:
        print(f"  конфиг: ./{CONFIG_NAME} не найден")
    if info["draft_config"]:
        print(f"  черновик конфига: {draft}")
    if info["setup_state"]:
        print(f"  настройка: {setup_state}" + (f" - последняя запись: {last_step[:120]}" if last_step else ""))
    elif not ready:
        print("  настройка не начиналась: шаг 0 по references/onboarding.md")
    if total is None:
        print("  план: work/plan.md нет")
    else:
        print(f"  план: work/plan.md - тем {total}, осталось {left}")
    if info["targets"]:
        print("  площадки: " + ", ".join(f"{k} ({v})" for k, v in info["targets"].items())
              + f"; автопубликация: {'да' if info['publish_auto'] else 'нет'}")
    else:
        print("  площадки: не настроены (publish.targets)")
    arts = info["articles"]
    if arts:
        print("  статьи: " + ", ".join(f"{x['slug']} ({x['stage']})" for x in arts[:12])
              + (f" и ещё {len(arts) - 12}" if len(arts) > 12 else ""))
    sys.exit(0 if ready else 1)


if __name__ == "__main__":
    main()
