#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check-all.py - все проверки статьи одной командой, с сохранением вывода.

    python3 check-all.py work/<slug>/draft.md --round 1
    python3 check-all.py work/<slug>/draft.md --round 2 --min-internal 1
    python3 check-all.py work/<slug>/final.md --round final

Запускает по очереди:
  structure-check   <файл> [--min-internal N]                 - блокирует (exit 1)
  claims-check      work/<slug> --text <файл>                  - блокирует (exit 1, нет claims.json)
  originality-check <файл> work/<slug>/sources/                - блокирует (exit 1); exit 2 (разные
                                                                 языки) - решает проверяющий
  ai-cadence-check  <файл>                                     - справка
  read-aloud-check  <файл>                                     - блокирует только exit 1 (слишком
                                                                 длинные предложения); цифры - справка
Ошибка запуска (нет файла, пустой корпус источников, exit 2-3 у блокирующей) - тоже провал:
проверка не выполнена, значит не пройдена.

Вывод каждой проверки - work/<slug>/checks/<N>/<имя>.txt (с командой и кодом выхода),
сводка - checks/<N>/summary.md (sha256 проверенного файла, таблица). --round по умолчанию -
последний существующий числовой круг в checks/ или 1. --config передаётся всем проверкам.

Коды выхода: 0 - все блокирующие прошли; 1 - хотя бы одна блокирующая не прошла;
2 - нет файла статьи.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from _config import find_config  # noqa: E402


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def default_round(checks_dir):
    nums = []
    if os.path.isdir(checks_dir):
        nums = [int(d) for d in os.listdir(checks_dir) if d.isdigit() and os.path.isdir(os.path.join(checks_dir, d))]
    return str(max(nums)) if nums else "1"


def plan(article, work, cfg, min_internal):
    """[(имя, argv, роль)]; роль: block | info | readaloud | originality."""
    c = ["--config", cfg] if cfg else []
    mi = ["--min-internal", str(min_internal)] if min_internal is not None else []
    sources = os.path.join(work, "sources")
    return [
        ("structure-check", ["structure-check.py", article] + mi + c, "block"),
        ("claims-check", ["claims-check.py", work, "--text", article], "block"),
        ("originality-check", ["originality-check.py", article, sources] + c, "originality"),
        ("ai-cadence-check", ["ai-cadence-check.py", article] + c, "info"),
        ("read-aloud-check", ["read-aloud-check.py", article] + c, "readaloud"),
    ]


def verdict(role, code):
    """(блокирует ли, текст статуса)."""
    if role == "info":
        return False, "справка" + (f" (exit {code})" if code else "")
    if role == "readaloud":
        if code == 0:
            return False, "ок"
        if code == 1:
            return True, "длинные предложения (exit 1)"
        return True, f"не выполнена (exit {code})"
    if role == "originality":
        if code == 0:
            return False, "ок"
        if code == 1:
            return True, "слишком близко к источнику (exit 1)"
        if code == 2:
            return False, "разные языки - решает проверяющий (exit 2)"
        return True, f"не выполнена (exit {code})"
    if code == 0:
        return False, "ок"
    if code == 1:
        return True, "ошибки (exit 1)"
    return True, f"не выполнена (exit {code})"


def main():
    ap = argparse.ArgumentParser(description="Все проверки статьи одной командой; вывод - в work/<slug>/checks/<N>/",
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("article", help="файл статьи: work/<slug>/draft.md или work/<slug>/final.md")
    ap.add_argument("--round", dest="round", default=None,
                    help="номер круга (1, 2, 3) или метка (final); по умолч. последний в checks/ или 1")
    ap.add_argument("--out-dir", default=None, help="куда писать вывод вместо work/<slug>/checks/<N>/")
    ap.add_argument("--work", default=None,
                    help="папка статьи work/<slug>, если файл лежит не в ней (например, копия в checks/)")
    ap.add_argument("--min-internal", type=int, default=None,
                    help="передать в structure-check (нехватка ссылок с причиной в status.md)")
    ap.add_argument("--config", default=None, help="путь к statejnik.yaml (передаётся всем проверкам)")
    a = ap.parse_args()

    if not os.path.isfile(a.article):
        print(f"нет файла {a.article}", file=sys.stderr)
        sys.exit(2)
    work = a.work or os.path.dirname(a.article) or "."
    slug = os.path.basename(os.path.abspath(work))
    checks = os.path.join(work, "checks")
    if a.round:
        rnd = str(a.round)
    elif a.out_dir:
        rnd = os.path.basename(os.path.normpath(a.out_dir))
    else:
        rnd = default_round(checks)
    if not a.out_dir and not re.fullmatch(r"[\w.-]+", rnd):
        print(f"--round: недопустимое значение {rnd!r}", file=sys.stderr)
        sys.exit(2)
    out = a.out_dir or os.path.join(checks, rnd)
    os.makedirs(out, exist_ok=True)
    cfg = a.config or find_config(near=a.article)
    if not cfg:
        print("предупреждение: statejnik.yaml не найден - проверки работают на значениях по умолчанию "
              "(домен и запреты проекта не учитываются); запускайте из рабочей папки", file=sys.stderr)

    rows, failed = [], False
    for name, argv, role in plan(a.article, work, cfg, a.min_internal):
        cmd = [sys.executable, os.path.join(HERE, argv[0])] + argv[1:]
        shown = "python3 $SKILL_DIR/scripts/" + " ".join(argv)
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            code, stdout, stderr = r.returncode, r.stdout, r.stderr
        except Exception as e:  # noqa: BLE001
            code, stdout, stderr = 99, "", f"не запустилась: {e}"
        block, status = verdict(role, code)
        failed = failed or block
        with open(os.path.join(out, name + ".txt"), "w", encoding="utf-8") as f:
            f.write(f"$ {shown}\n\n{stdout}")
            if stderr.strip():
                f.write(f"\n--- stderr ---\n{stderr}")
            f.write(f"\nEXIT {code}\n")
        kind = "справочная" if role == "info" else "блокирующая"
        rows.append((name, kind, code, status, block))

    digest = sha256(a.article)
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"# Проверки: {slug}, круг {rnd}", "",
             f"- файл: {a.article}", f"- sha256: {digest}", f"- когда: {now}",
             f"- конфиг: {cfg or 'не найден (значения по умолчанию)'}",
             f"- итог: {'ЕСТЬ БЛОКИРУЮЩИЕ (exit 1)' if failed else 'блокирующие пройдены (exit 0)'}", "",
             "| Проверка | Тип | Exit | Итог |", "|---|---|---|---|"]
    lines += [f"| {n} | {k} | {c} | {'✗ ' if b else ''}{s} |" for n, k, c, s, b in rows]
    lines += ["", "Вывод каждой проверки - <имя>.txt в этой папке. После любой правки текста или claims.json "
              "прогнать check-all.py снова перед следующим кругом."]
    with open(os.path.join(out, "summary.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    w = max(len(r[0]) for r in rows)
    print(f"Проверки {a.article} (круг {rnd}) → {out}/")
    for n, k, c, s, b in rows:
        mark = "✗" if b else ("·" if k == "справочная" else "✓")
        print(f"  {mark} {n.ljust(w)}  {k.ljust(11)}  exit {c}  {s}")
    print("Итог: " + ("ЕСТЬ БЛОКИРУЮЩИЕ - исправить и прогнать снова (exit 1)" if failed
                      else "блокирующие пройдены (exit 0); справочные - см. файлы"))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
