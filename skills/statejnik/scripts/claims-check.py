#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
claims-check.py - механическая проверка реестра фактов work/<slug>/claims.json.

    python3 claims-check.py work/<slug>
    python3 claims-check.py work/<slug> --text work/<slug>/final.md
    python3 claims-check.py work/<slug> --json

Что проверяет (схема - references/process/05-write.md, «Реестр фактов»):
  1. Структура: объект {slug, updated, claims: [...]}; у каждой записи поля
     id, claim, where (список), kind, source_url, source_file, quote, checked_at,
     status; необязательные version, conditions, reason. id уникальны.
  2. kind - fact | number | price | limit | date | version | availability | command |
     quote | person | comparison | promise; status - verified | limited | unverified | removed.
  3. Для verified/limited: source_file существует (путь от work/<slug>/), quote
     непустой и ДОСЛОВНО встречается в source_file. Допускается только разница в
     пробелах (переносы строк, двойные пробелы, неразрывный пробел) - это
     помечается как замечание. Разница в кавычках, тире, регистре, опечатках -
     ошибка (подсказка «похожая строка есть» печатается).
  4. source_url - http(s), без трекинговых параметров (utm_*, yclid, gclid...).
  5. checked_at - дата ГГГГ-ММ-ДД, не в будущем.
  6. unverified с непустым where (утверждение стоит в тексте) - ошибка;
     removed без reason - замечание.

Режим --text ФАЙЛ (можно несколько раз): находит в тексте числа и проценты
(вне кода и адресов) и предупреждает о тех, которых нет ни в одной действующей
записи реестра (claim/quote/conditions/version). Числа меньше 10 без % и
единиц пропускаются (--all-numbers - проверять и их). С --strict-text такие
числа дают exit 1.

Коды выхода: 0 - существенных проблем нет (замечания возможны);
1 - есть неподтверждённое: опора не найдена, файла источника нет, неверный
    status/kind, unverified в тексте, дубли id, битая структура записи;
2 - нет claims.json или это не JSON.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import unicodedata
import urllib.parse

KINDS = ("fact", "number", "price", "limit", "date", "version", "availability", "command",
         "quote", "person", "comparison", "promise")
STATUSES = ("verified", "limited", "unverified", "removed")
REQUIRED = ("id", "claim", "where", "kind", "source_url", "source_file", "quote", "checked_at", "status")
OPTIONAL = ("version", "conditions", "reason", "note", "notes", "sources", "source_title")
WHERE = ("body", "h1", "title", "title_variants", "meta_title", "meta_description", "excerpt",
         "hero_promise", "cta", "caption", "image", "alt", "code", "table", "faq", "toc", "lead",
         "sources", "frontmatter", "slug", "cta_offer")
TRACKING = re.compile(r"^(utm_\w+|yclid|gclid|fbclid|_openstat|from|ref|referrer|clid|mc_cid|mc_eid|ysclid)$", re.I)
WS = re.compile(r"[\s\u00a0\u202f\u2007\u2009\u200b\ufeff]+")


def ws_norm(s):
    return WS.sub(" ", s).strip()


def loose_norm(s):
    """Для подсказки «похожая строка есть»: регистр, ё, кавычки, тире, пробелы."""
    s = unicodedata.normalize("NFKC", s).lower().replace("ё", "е")
    s = re.sub(r"[«»“”„\"'‘’`]", '"', s)
    s = re.sub(r"[‐‑‒–—―−-]", "-", s)
    return ws_norm(s)


def read_text(path, cache):
    if path not in cache:
        with open(path, encoding="utf-8", errors="replace") as f:
            raw = f.read()
        cache[path] = (raw, ws_norm(raw), loose_norm(raw))
    return cache[path]


def resolve_source(work, source_file):
    for base in (work, os.path.dirname(work.rstrip("/")) or ".", os.getcwd()):
        p = os.path.normpath(os.path.join(base, source_file))
        if os.path.isfile(p):
            return p
    return None


def check_claims(work, claims_path, today=None):
    """Вернуть (errors, warnings, rows). errors/warnings - списки строк."""
    today = today or dt.date.today()
    errors, warnings, rows = [], [], []
    with open(claims_path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        warnings.append("claims.json - голый список; ожидается объект {slug, updated, claims: [...]}")
        claims = data
    elif isinstance(data, dict):
        claims = data.get("claims")
        if not isinstance(claims, list):
            errors.append("нет списка claims в claims.json")
            return errors, warnings, rows
        slug = os.path.basename(os.path.normpath(work))
        if data.get("slug") and data["slug"] != slug:
            warnings.append(f"slug в claims.json «{data['slug']}» не совпадает с папкой «{slug}»")
        if not data.get("updated"):
            warnings.append("нет поля updated (дата последнего обновления реестра)")
    else:
        errors.append("claims.json должен быть объектом")
        return errors, warnings, rows

    seen, cache = {}, {}
    for n, c in enumerate(claims, 1):
        if not isinstance(c, dict):
            errors.append(f"запись #{n}: не объект")
            continue
        cid = str(c.get("id") or f"#{n}")
        row = {"id": cid, "status": c.get("status"), "problems": []}
        rows.append(row)

        def err(msg):
            errors.append(f"{cid}: {msg}")
            row["problems"].append(msg)

        def warn(msg):
            warnings.append(f"{cid}: {msg}")

        if cid in seen:
            err(f"id повторяется (уже был в записи #{seen[cid]})")
        seen[cid] = n
        status = c.get("status")
        missing = [k for k in REQUIRED if k not in c]
        if status == "removed":
            missing = [k for k in missing if k in ("id", "claim", "status")]
        if missing:
            err("нет полей: " + ", ".join(missing))
        extra = [k for k in c if k not in REQUIRED + OPTIONAL]
        if extra:
            warn("неизвестные поля: " + ", ".join(extra))
        if status not in STATUSES:
            err(f"status «{status}» не из допустимых: {', '.join(STATUSES)}")
        kind = c.get("kind")
        if kind is not None and kind not in KINDS:
            err(f"kind «{kind}» не из допустимых: {', '.join(KINDS)}")
        where = c.get("where")
        if where is not None and not isinstance(where, list):
            err("where должен быть списком мест в статье, например [\"body\", \"meta_description\"]")
            where = [where]
        where = where or []
        unknown_where = [w for w in where if str(w).split(":")[0].split(".")[0] not in WHERE]
        if unknown_where:
            warn("непривычные значения where: " + ", ".join(map(str, unknown_where)))
        if not str(c.get("claim") or "").strip():
            err("пустой claim")

        if status == "removed":
            if not str(c.get("reason") or "").strip():
                warn("removed без reason (причина снятия)")
            if where:
                warn(f"removed, но where не пуст ({', '.join(map(str, where))}) - утверждение ещё в тексте?")
            continue
        if status == "unverified":
            if where:
                err("unverified, но стоит в тексте (where не пуст) - подтвердить, сузить (limited) или снять (removed)")
            else:
                warn("unverified (в тексте не используется)")
            continue

        # verified / limited
        url = str(c.get("source_url") or "")
        parts = urllib.parse.urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            err(f"source_url не http(s)-адрес: {url[:80]!r}")
        else:
            bad = [k for k, _ in urllib.parse.parse_qsl(parts.query, keep_blank_values=True) if TRACKING.match(k)]
            if bad:
                warn(f"source_url содержит трекинговые параметры ({', '.join(bad)}) - нужен чистый адрес")
        ca = str(c.get("checked_at") or "")
        try:
            d = dt.date.fromisoformat(ca[:10])
            if d > today:
                err(f"checked_at {ca} в будущем")
            elif kind in ("price", "limit", "availability") and (today - d).days > 30:
                warn(f"{kind} проверен {ca} - больше 30 дней назад; цены/лимиты сверяют в день написания")
        except ValueError:
            err(f"checked_at «{ca}» - нужна дата ГГГГ-ММ-ДД")
        if status == "limited" and not str(c.get("conditions") or "").strip():
            warn("limited без conditions (до чего сужено утверждение)")

        sf = str(c.get("source_file") or "").strip()
        quote = str(c.get("quote") or "")
        if not sf:
            err("пустой source_file")
            continue
        path = resolve_source(work, sf)
        if not path:
            err(f"нет файла источника {sf} (ищу от {work})")
            continue
        if not quote.strip():
            err("пустой quote - нужна дословная опора из источника")
            continue
        raw, raw_ws, raw_loose = read_text(path, cache)
        if quote in raw:
            row["found"] = "exact"
            continue
        if ws_norm(quote) and ws_norm(quote) in raw_ws:
            row["found"] = "whitespace"
            warn("quote найден только с точностью до пробелов/переносов строк - это допустимо")
            continue
        hint = ""
        if loose_norm(quote) in raw_loose:
            hint = " (похожая строка есть: отличаются кавычки, тире, регистр или ё - скопируйте дословно)"
        err(f"quote не найден дословно в {sf}{hint}: «{quote[:80]}»")
    return errors, warnings, rows


# ── числа в тексте ───────────────────────────────────────────────────────────

NUM = re.compile(r"(?<![\w.,/])(\d{1,3}(?:[ \u00a0\u202f]\d{3})+|\d+(?:[.,]\d+)?)(?:\s?(%|‰|процент\w*))?(?![\w/])")
UNIT_AFTER = re.compile(r"^\s?(°|мм|см|м|км|кг|г|л|мл|руб|₽|\$|€|шт|ч|час|мин|сек|дн|лет|год|раз|тыс|млн|млрд|"
                        r"цикл|см²|м²|кв|вт|квт|мбит|гб|мб|x|х)", re.I)


def norm_num(s):
    s = re.sub(r"[ \u00a0\u202f]", "", s).replace(",", ".")
    if "." in s:
        s = s.rstrip("0").rstrip(".") or "0"
    return s.lstrip("0") or "0"


def numbers_in(text):
    return {norm_num(m.group(1)) for m in NUM.finditer(text or "")}


def strip_for_numbers(text):
    t = re.sub(r"(?s)```.*?```", " ", text)
    t = re.sub(r"`[^`]*`", " ", t)
    t = re.sub(r"<!--.*?-->", " ", t, flags=re.S)
    t = re.sub(r"\]\([^)]*\)", "]", t)                     # адреса ссылок
    t = re.sub(r"https?://\S+", " ", t)
    t = re.sub(r"(?m)^\s*(\d+)[.)]\s", " ", t)             # нумерация списков
    t = re.sub(r"(?m)^(slug|cta_url|url|image|cover|date|updated)\s*:.*$", " ", t)
    return t


def text_numbers(text, all_numbers=False):
    """[(норм. число, как в тексте, контекст)] для проверки."""
    out = []
    t = strip_for_numbers(text)
    for m in NUM.finditer(t):
        raw, pct = m.group(1), m.group(2)
        val = norm_num(raw)
        after = t[m.end():m.end() + 6]
        has_unit = bool(pct) or bool(UNIT_AFTER.match(after))
        try:
            small = float(val) < 10
        except ValueError:
            small = False
        if small and not has_unit and not all_numbers:
            continue
        ctx = ws_norm(t[max(0, m.start() - 40):m.end() + 30])
        out.append((val, m.group(0).strip(), ctx))
    return out


def check_text(paths, claims_path, all_numbers=False):
    with open(claims_path, encoding="utf-8") as f:
        data = json.load(f)
    claims = data.get("claims", []) if isinstance(data, dict) else data
    known = set()
    for c in claims:
        if isinstance(c, dict) and c.get("status") != "removed":
            for k in ("claim", "quote", "conditions", "version"):
                known |= numbers_in(str(c.get(k) or ""))
    missing = []
    for p in paths:
        text = open(p, encoding="utf-8", errors="replace").read()
        seen = set()
        for val, shown, ctx in text_numbers(text, all_numbers):
            if val in known or (val, p) in seen:
                continue
            seen.add((val, p))
            missing.append({"file": p, "number": shown, "context": ctx})
    return missing


def main():
    ap = argparse.ArgumentParser(description="Проверка реестра фактов claims.json: схема, файлы источников, "
                                             "дословность цитат; числа текста без записи в реестре.",
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("work", help="папка статьи work/<slug> (или путь к claims.json)")
    ap.add_argument("--claims", help="путь к claims.json (по умолч. <work>/claims.json)")
    ap.add_argument("--text", action="append", default=[],
                    help="файл статьи (draft.md/final.md): предупредить о числах, которых нет в реестре; можно несколько")
    ap.add_argument("--all-numbers", action="store_true", help="в --text проверять и числа меньше 10 без единиц")
    ap.add_argument("--strict-text", action="store_true", help="числа без записи в реестре дают exit 1")
    ap.add_argument("--json", action="store_true", help="вывод JSON")
    a = ap.parse_args()

    work = a.work
    if work.endswith(".json") and os.path.isfile(work):
        a.claims = a.claims or work
        work = os.path.dirname(work) or "."
    claims_path = a.claims or os.path.join(work, "claims.json")
    if not os.path.isfile(claims_path):
        print(f"нет файла {claims_path}", file=sys.stderr)
        sys.exit(2)
    try:
        errors, warnings, rows = check_claims(work, claims_path)
    except ValueError as e:
        print(f"{claims_path}: не JSON ({e})", file=sys.stderr)
        sys.exit(2)
    missing = []
    for t in a.text:
        if not os.path.isfile(t):
            print(f"нет файла {t}", file=sys.stderr)
            sys.exit(2)
    if a.text:
        missing = check_text(a.text, claims_path, a.all_numbers)
    fail = bool(errors) or (a.strict_text and bool(missing))

    if a.json:
        print(json.dumps({"claims": claims_path, "ok": not fail, "errors": errors, "warnings": warnings,
                          "numbers_without_claim": missing,
                          "counts": {s: sum(1 for r in rows if r["status"] == s) for s in STATUSES}},
                         ensure_ascii=False, indent=1))
        sys.exit(1 if fail else 0)

    counts = ", ".join(f"{s}: {sum(1 for r in rows if r['status'] == s)}" for s in STATUSES)
    print(f"Реестр фактов: {claims_path} - записей {len(rows)} ({counts})")
    if errors:
        print(f"\n  НЕПОДТВЕРЖДЕНО / ОШИБКИ ({len(errors)}):")
        for e in errors:
            print(f"  ✗ {e}")
    if warnings:
        print(f"\n  Замечания ({len(warnings)}):")
        for w in warnings:
            print(f"  ! {w}")
    if a.text:
        if missing:
            print(f"\n  Числа в тексте без записи в реестре ({len(missing)})"
                  + (" - блокер (--strict-text):" if a.strict_text else " - проверьте, это существенные факты?:"))
            for m in missing[:60]:
                print(f"  ? {m['number']}  … {m['context']} …  [{os.path.basename(m['file'])}]")
        else:
            print("\n  Все числа текста есть в реестре.")
    print("\n  Итог: " + ("ЕСТЬ НЕПОДТВЕРЖДЁННОЕ (exit 1)" if fail else "опоры на месте (exit 0)"))
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
