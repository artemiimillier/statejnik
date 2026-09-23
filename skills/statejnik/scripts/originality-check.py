#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
originality-check.py - измеримая проверка оригинальности черновика статьи.

Превращает «оригинально на глаз» в число: насколько текст черновика лексически
совпадает с уже опубликованными материалами (или с эталонным корпусом). Считаем
по КАЖДОМУ документу корпуса и берём максимум - так ловим самоповтор даже против
одной похожей статьи, а не «в среднем по больнице».

Метрики:
  - cosine    - TF-IDF косинусное сходство по мешку слов (поверхностное копирование).
  - ngram     - Jaccard по словесным n-граммам (дословные куски подряд, по умолч. 5).
  - jaccard   - Jaccard по множеству слов (общий лексический пул, справочно).

Пороги (берутся из statejnik.yaml → CLI → дефолт):
  tools.originality.max_cosine   (по умолч. 0.50)
  tools.originality.max_ngram    (по умолч. 0.30)
  tools.originality.ngram_n      (по умолч. 5)
PASS, если cosine ≤ max_cosine И ngram ≤ max_ngram. Иначе FAIL (клон-риск → переписать).

ЧЕСТНОЕ ОГРАНИЧЕНИЕ (скрипт сам его репортит):
  Лексические метрики осмысленны ТОЛЬКО когда черновик и эталон на одном языке.
  Если эталон на другом языке (другой алфавит) - косинус тривиально ≈0 и это НЕ
  доказывает оригинальность смысла. В таком случае ставится cross_language=true,
  вердикт уходит человеку/агенту (оценить структурную близость: порядок мыслей,
  свои примеры), а не решается этим скриптом.

Зависимости: только стандартная библиотека Python 3 (re, json, math, os, argparse).
PyYAML не требуется - если statejnik.yaml есть, читается встроенным мини-парсером.

Использование:
  python3 scripts/originality-check.py <черновик.md> <корпус/>            # папка с эталонами
  python3 scripts/originality-check.py <черновик.md> <эталон.txt>          # один файл
  python3 scripts/originality-check.py <черновик.md> <корпус/> --max-cosine 0.45 --json
  python3 scripts/originality-check.py <черновик.md> --config ./statejnik.yaml <корпус/>

Коды выхода: 0 = PASS, 1 = FAIL, 2 = разные языки (решение отдано человеку), 3 = ошибка ввода.
"""
import argparse
import json
import math
import os
import re
import sys
from collections import Counter

try:
    import _config as cfgmod
except ImportError:  # запуск из другой директории - добавим папку скрипта в путь
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import _config as cfgmod

CYRILLIC = re.compile(r"[а-яё]")
LATIN = re.compile(r"[a-z]")
# слово = последовательность букв (кириллица/латиница) и цифр длиной ≥ 2
TOKEN = re.compile(r"[a-zа-яё0-9]{2,}", re.IGNORECASE)

# дефолты (используются, если нет ни statejnik.yaml, ни CLI-флага)
DEFAULT_COSINE_MAX = 0.50
DEFAULT_NGRAM_MAX = 0.30
DEFAULT_NGRAM_N = 5

TEXT_EXTS = (".md", ".markdown", ".txt", ".mdx", ".html", ".htm", ".rst", ".text")


def read(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def strip_markup(text):
    """Снять markup/служебную разметку, чтобы сравнивать смысл, а не синтаксис."""
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)          # код-блоки
    text = re.sub(r"`[^`]*`", " ", text)                             # inline-код
    text = re.sub(r"<[^>]+>", " ", text)                             # html-теги
    text = re.sub(r"^---\n.*?\n---\n", " ", text, flags=re.DOTALL)   # yaml-фронтматтер
    text = re.sub(r"^\s*#{1,6}\s*", " ", text, flags=re.MULTILINE)   # заголовки
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)                # картинки
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)             # ссылки → текст
    text = re.sub(r"[*_>|#~]", " ", text)                            # маркеры разметки
    text = re.sub(r"\d{1,2}:\d{2}", " ", text)                       # тайм-коды 0:15
    return text


def tokens(text):
    return [t.lower() for t in TOKEN.findall(text)]


def detect_lang(toks):
    cyr = sum(1 for t in toks if CYRILLIC.search(t))
    lat = sum(1 for t in toks if LATIN.search(t))
    total = cyr + lat
    if total == 0:
        return "unknown"
    ratio = cyr / total
    if ratio >= 0.6:
        return "ru"
    if ratio <= 0.4:
        return "en"
    return "mixed"


def tfidf_cosine(toks_a, toks_b):
    """Косинус по TF-IDF над объединённым словарём двух документов (df ∈ {1,2})."""
    ca, cb = Counter(toks_a), Counter(toks_b)
    vocab = set(ca) | set(cb)
    if not vocab:
        return 0.0
    idf = {}
    for term in vocab:
        df = (1 if term in ca else 0) + (1 if term in cb else 0)
        idf[term] = math.log((1 + 2) / (1 + df)) + 1.0  # сглаженный idf
    va = {t: ca[t] * idf[t] for t in ca}
    vb = {t: cb[t] * idf[t] for t in cb}
    dot = sum(va[t] * vb.get(t, 0.0) for t in va)
    na = math.sqrt(sum(v * v for v in va.values()))
    nb = math.sqrt(sum(v * v for v in vb.values()))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def ngram_jaccard(toks_a, toks_b, n):
    def grams(toks):
        if len(toks) < n:
            return set()
        return set(tuple(toks[i:i + n]) for i in range(len(toks) - n + 1))
    ga, gb = grams(toks_a), grams(toks_b)
    if not ga or not gb:
        return 0.0
    union = len(ga | gb)
    return len(ga & gb) / union if union else 0.0


def matching_ngrams(toks_a, toks_b, n, limit=10):
    """Вернуть до limit совпадающих словесных n-грамм (для отчёта - что именно списано)."""
    if len(toks_a) < n or len(toks_b) < n:
        return []
    ga = set(tuple(toks_a[i:i + n]) for i in range(len(toks_a) - n + 1))
    gb = set(tuple(toks_b[i:i + n]) for i in range(len(toks_b) - n + 1))
    common = ga & gb
    return [" ".join(g) for g in list(common)[:limit]]


def word_jaccard(toks_a, toks_b):
    sa, sb = set(toks_a), set(toks_b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def collect_reference_files(path):
    """Папка → список текстовых файлов внутри (рекурсивно). Файл → [файл]."""
    if os.path.isfile(path):
        return [path]
    if os.path.isdir(path):
        out = []
        for root, _, names in os.walk(path):
            for name in sorted(names):
                if name.lower().endswith(TEXT_EXTS) and not name.startswith("."):
                    out.append(os.path.join(root, name))
        return out
    return []


def parse_args():
    p = argparse.ArgumentParser(
        prog="originality-check.py",
        description="Проверка оригинальности черновика статьи: TF-IDF косинус + "
                    "Jaccard по n-граммам против корпуса уже опубликованного.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Пороги: statejnik.yaml (tools.originality.*) → флаги CLI → дефолты "
               "(cosine 0.50, ngram 0.30, n=5). Коды выхода: 0=PASS, 1=FAIL, "
               "2=разные языки, 3=ошибка ввода.",
    )
    p.add_argument("draft", help="файл черновика статьи (.md/.txt/.html и т.п.)")
    p.add_argument("corpus",
                   help="папка с уже опубликованными статьями ИЛИ один эталонный файл")
    p.add_argument("--config", default=None,
                   help="путь к statejnik.yaml (по умолчанию ./statejnik.yaml в рабочей папке)")
    p.add_argument("--max-cosine", type=float, default=None,
                   help="порог косинуса (перебивает statejnik.yaml)")
    p.add_argument("--max-ngram", type=float, default=None,
                   help="порог Jaccard по n-граммам (перебивает statejnik.yaml)")
    p.add_argument("--ngram-n", type=int, default=None,
                   help="длина словесной n-граммы (перебивает statejnik.yaml)")
    p.add_argument("--json", action="store_true", help="вывод машинно-читаемым JSON")
    return p.parse_args()


def main():
    args = parse_args()
    _, cfg = cfgmod.load_for(args.config, near=args.draft)

    cosine_max = args.max_cosine if args.max_cosine is not None \
        else float(cfgmod.get(cfg, "tools.originality.max_cosine", DEFAULT_COSINE_MAX))
    ngram_max = args.max_ngram if args.max_ngram is not None \
        else float(cfgmod.get(cfg, "tools.originality.max_ngram", DEFAULT_NGRAM_MAX))
    ngram_n = args.ngram_n if args.ngram_n is not None \
        else int(cfgmod.get(cfg, "tools.originality.ngram_n", DEFAULT_NGRAM_N))

    try:
        draft_raw = read(args.draft)
    except OSError as e:
        sys.stderr.write(f"ошибка чтения черновика: {e}\n")
        sys.exit(3)

    ref_files = collect_reference_files(args.corpus)
    if not ref_files:
        sys.stderr.write(
            f"в '{args.corpus}' не найдено эталонных текстов "
            f"({', '.join(TEXT_EXTS)}). Нечего сравнивать.\n")
        sys.exit(3)

    dt = tokens(strip_markup(draft_raw))
    if len(dt) < 50:
        sys.stderr.write("предупреждение: черновик < 50 слов - метрики ненадёжны\n")
    lang_d = detect_lang(dt)

    # сравнить с каждым эталоном, запомнить самый похожий по косинусу
    worst = None  # самый похожий документ = наибольший риск
    per_file = []
    for rf in ref_files:
        try:
            rt = tokens(strip_markup(read(rf)))
        except OSError:
            continue
        if len(rt) < 20:
            continue
        cos = tfidf_cosine(dt, rt)
        ngr = ngram_jaccard(dt, rt, ngram_n)
        rec = {"file": rf, "lang": detect_lang(rt), "cosine": round(cos, 4),
               "ngram": round(ngr, 4), "_rt": rt}
        per_file.append(rec)
        if worst is None or cos > worst["cosine"]:
            worst = rec

    if worst is None:
        sys.stderr.write("эталоны слишком короткие (< 20 слов) - сравнение невозможно\n")
        sys.exit(3)

    lang_r = worst["lang"]
    cross = (lang_d in ("ru", "en") and lang_r in ("ru", "en") and lang_d != lang_r)
    cosine = worst["cosine"]
    ngram = worst["ngram"]
    jacc = round(word_jaccard(dt, worst["_rt"]), 4)
    shared = matching_ngrams(dt, worst["_rt"], ngram_n)

    if cross:
        verdict, passed, code = "CROSS_LANG", None, 2
        note = (f"Черновик ({lang_d}) и ближайший эталон ({lang_r}) на разных языках. "
                f"Лексический косинус неинформативен. Структурную близость "
                f"(порядок мыслей, свои примеры) оценивает человек/агент, не скрипт.")
    else:
        passed = (cosine <= cosine_max) and (ngram <= ngram_max)
        verdict = "PASS" if passed else "FAIL"
        code = 0 if passed else 1
        note = ("Оригинал: лексически разошёлся с корпусом." if passed else
                f"Клон-риск против '{os.path.basename(worst['file'])}': "
                f"cosine {cosine} (порог ≤{cosine_max}) / ngram {ngram} "
                f"(порог ≤{ngram_max}). Переписать угол, дать свою фактуру.")

    result = {
        "draft": args.draft,
        "draft_words": len(dt),
        "lang_draft": lang_d,
        "corpus_files": len(per_file),
        "closest_file": worst["file"],
        "lang_closest": lang_r,
        "cosine": cosine, "cosine_max": cosine_max,
        "ngram_jaccard": ngram, "ngram_max": ngram_max, "ngram_n": ngram_n,
        "word_jaccard": jacc,
        "cross_language": cross,
        "shared_ngrams": shared,
        "verdict": verdict, "passed": passed, "note": note,
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"Оригинальность: {verdict}")
        print(f"  ближайший эталон  : {os.path.basename(worst['file'])} "
              f"(из {len(per_file)} файлов)")
        print(f"  cosine (TF-IDF)   : {cosine}  (порог ≤{cosine_max})")
        print(f"  ngram-{ngram_n} jaccard   : {ngram}  (порог ≤{ngram_max})")
        print(f"  word jaccard      : {jacc}")
        print(f"  язык черн./эталон : {lang_d}/{lang_r}"
              f"{'  (РАЗНЫЕ ЯЗЫКИ)' if cross else ''}")
        print(f"  слов в черновике  : {len(dt)}")
        if shared and not passed:
            print("  совпавшие n-граммы:")
            for g in shared[:6]:
                print(f"     • …{g}…")
        print(f"  → {note}")
    sys.exit(code)


if __name__ == "__main__":
    main()
