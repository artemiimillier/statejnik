#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
read-aloud-check.py - механическая проверка текста «на язык» (читаемость вслух).

Хороший текст статьи читается легко: его можно произнести и не запнуться. Часть
«спотыканий» ловится механически, не дожидаясь, пока живой человек реально
запнётся при чтении. Скрипт флагает:

  1. Длинные предложения   - длиннее N слов трудно прочесть на одном дыхании.
  2. Скопления согласных    - 5+ согласных подряд = скороговорка.
  3. Очень длинные слова     - длиннее N букв тяжелы вслух.
  4. Канцелярит / причастия   - суффиксы -вшись/-ующ/-явш и т.п. спотыкают.
  5. Голые цифры              - «4-6» вместо «четыре-шесть»: вслух неоднозначно.
                                ТОЛЬКО ПРЕДУПРЕЖДЕНИЕ: в статье цифры нормальны.
                                Таблицы, код, frontmatter, даты, числа с единицами
                                (°C, %, мм, циклов, руб...) не считаются.

Это НЕ замена чтению вслух живым человеком (финальный судья - он). Это сито,
которое снимает грубые куски до вычитки. Заточено под русский текст.

Пороги (берутся из statejnik.yaml → CLI → дефолт):
  tools.read_aloud.max_sentence_words   (по умолч. 28)
  tools.read_aloud.max_word_len         (по умолч. 17)
  tools.read_aloud.max_consonant_run    (по умолч. 5)

Зависимости: только стандартная библиотека Python 3 (re, json, os, argparse).
PyYAML не требуется.

Использование:
  python3 scripts/read-aloud-check.py <статья.md>
  python3 scripts/read-aloud-check.py <статья.md> --max-sentence 24 --json
  python3 scripts/read-aloud-check.py <статья.md> --config ./statejnik.yaml

Коды выхода:
  0 = блокеров нет (предупреждения возможны: голые цифры, длинные слова,
      скопления согласных, причастия - список на правку, не блокер);
  1 = есть блокер: предложение длиннее max_sentence_words (его нельзя прочесть
      на одном дыхании). С --strict exit 1 дают любые флаги, кроме голых цифр;
  3 = ошибка ввода.
Таблицы (строки `| ... |`) в проверку не входят: в них цифры и обрывки - норма.
"""
import argparse
import json
import os
import re
import sys

try:
    import _config as cfgmod
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import _config as cfgmod

VOWELS = "аеёиоуыэюяАЕЁИОУЫЭЮЯ"
CONS = "бвгджзйклмнпрстфхцчшщБВГДЖЗЙКЛМНПРСТФХЦЧШЩ"

DEFAULT_MAX_SENTENCE_WORDS = 28
DEFAULT_MAX_WORD_LEN = 17
DEFAULT_CONS_RUN = 5

# Спотыкаемые формы. Деепричастия (-вшись/-ясь) и страдательные причастия
# (-уемых/-ируемых) ловим всегда - они тяжелы вслух. Активные причастия на
# -ующ/-ающ/-ивш ловим ТОЛЬКО в длинных словах (≥12 букв), чтобы не флагать
# обычные читаемые «следующий», «существующий», «текущий».
CLUNKY_SUFFIX = re.compile(
    r"\w+(?:вшись|явшись|ившись|уясь|аясь|ируем\w*|уем\w*|ляем\w*|"
    r"вшихся|вшийся|вшегося)\w*"
    r"|[а-яё]{8,}(?:ующ|ающ|ивш|явш|ующих|ающих)\w*",
    re.IGNORECASE)
DIGIT_TOKEN = re.compile(r"(?<![\w.,])\d{1,3}(?:[ \u00a0\u202f]\d{3})+(?![\w])|(?<![\w.,])\d+(?:[.,]\d+)?(?:[\-–]\d+(?:[.,]\d+)?)?(?![\w])")
UNIT_AFTER = re.compile(r"^\s?(%|°|‰|₽|\$|€|×|x\b|х\b|мм|см|м\b|км|кг|г\b|л\b|мл|руб|тыс|млн|млрд|шт|ч\b|час|мин|сек|"
                        r"дн|день|дня|дней|лет|год|раз|цикл|процент|градус|ватт|вт|квт|гб|мб|см²|м²|кв)", re.I)
DATE_LIKE = re.compile(r"^(19|20)\d\d$")
WORD = re.compile(r"[а-яёa-z]+", re.IGNORECASE)


def read(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def strip_for_speech(text):
    """Оставить только произносимое: убрать markup, код, html, ссылки, тайм-коды."""
    text = text.lstrip("\ufeff")
    text = re.sub(r"^---\s*\n.*?\n---\s*\n", " ", text, flags=re.DOTALL)  # yaml-фронтматтер
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"`[^`]*`", " ", text)
    text = re.sub(r"(?m)^\s*\|.*\|\s*$", " ", text)                  # таблицы
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)                              # html-теги
    text = re.sub(r"^\s*#{1,6}.*$", " ", text, flags=re.MULTILINE)    # заголовки-секции
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)                 # картинки
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)              # ссылки → текст
    text = re.sub(r"\d{1,2}:\d{2}", " ", text)                        # тайм-коды
    text = re.sub(r"[*_>|#~]", " ", text)
    return text


def split_sentences(text):
    parts = re.split(r"(?<=[.!?…])\s+|\n+", text)
    return [p.strip() for p in parts if p.strip() and WORD.search(p)]


def max_cons_run(word):
    run = best = 0
    for ch in word:
        if ch in CONS:
            run += 1
            best = max(best, run)
        else:
            run = 0
    return best


def parse_args():
    p = argparse.ArgumentParser(
        prog="read-aloud-check.py",
        description="Механическая проверка читаемости текста вслух: длинные "
                    "предложения, скопления согласных, длинные слова, канцелярит, "
                    "голые цифры. Заточено под русский текст.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Пороги: statejnik.yaml (tools.read_aloud.*) → флаги CLI → дефолты "
               "(28 слов / 17 букв / 5 согласных). Коды выхода: 0=блокеров нет "
               "(предупреждения возможны), 1=есть предложение длиннее порога (с --strict - и "
               "прочие флаги, кроме цифр), 3=ошибка ввода. Голые цифры и таблицы exit 1 не дают.",
    )
    p.add_argument("article", help="файл статьи (.md/.txt/.html и т.п.)")
    p.add_argument("--config", default=None,
                   help="путь к statejnik.yaml (по умолчанию ./statejnik.yaml в рабочей папке)")
    p.add_argument("--max-sentence", type=int, default=None,
                   help="макс. слов в предложении (перебивает statejnik.yaml)")
    p.add_argument("--max-word-len", type=int, default=None,
                   help="макс. длина слова в буквах (перебивает statejnik.yaml)")
    p.add_argument("--max-consonant-run", type=int, default=None,
                   help="макс. согласных подряд (перебивает statejnik.yaml)")
    p.add_argument("--json", action="store_true", help="вывод машинно-читаемым JSON")
    p.add_argument("--strict", action="store_true",
                   help="exit 1 при любых флагах, кроме голых цифр (для финальной вычитки вслух)")
    return p.parse_args()


def main():
    args = parse_args()
    _, cfg = cfgmod.load_for(args.config, near=args.article)

    max_sent = args.max_sentence if args.max_sentence is not None \
        else int(cfgmod.get(cfg, "tools.read_aloud.max_sentence_words",
                            DEFAULT_MAX_SENTENCE_WORDS))
    max_word = args.max_word_len if args.max_word_len is not None \
        else int(cfgmod.get(cfg, "tools.read_aloud.max_word_len", DEFAULT_MAX_WORD_LEN))
    cons_run = args.max_consonant_run if args.max_consonant_run is not None \
        else int(cfgmod.get(cfg, "tools.read_aloud.max_consonant_run", DEFAULT_CONS_RUN))

    try:
        raw = read(args.article)
    except OSError as e:
        sys.stderr.write(f"ошибка чтения: {e}\n")
        sys.exit(3)

    text = strip_for_speech(raw)
    sentences = split_sentences(text)
    flags = {"long_sentence": [], "consonant_cluster": [], "long_word": [],
             "clunky_morphology": [], "bare_digits": []}

    total_words = 0
    for s in sentences:
        words = WORD.findall(s)
        total_words += len(words)
        snippet = (s[:90] + "…") if len(s) > 90 else s
        if len(words) > max_sent:
            flags["long_sentence"].append({"words": len(words), "text": snippet})
        for w in words:
            if len(w) > max_word:
                flags["long_word"].append(w)
            if max_cons_run(w) >= cons_run:
                flags["consonant_cluster"].append(w)
        for m in CLUNKY_SUFFIX.finditer(s):
            flags["clunky_morphology"].append(m.group(0))
        for m in DIGIT_TOKEN.finditer(s):
            tok = m.group(0)
            if UNIT_AFTER.match(s[m.end():m.end() + 10]) or DATE_LIKE.match(tok):
                continue
            flags["bare_digits"].append(tok)

    for k in ("long_word", "consonant_cluster", "clunky_morphology", "bare_digits"):
        flags[k] = sorted(set(flags[k]))

    total_flags = sum(len(v) for v in flags.values())
    blocking = ["long_sentence"] + (["consonant_cluster", "long_word", "clunky_morphology"] if args.strict else [])
    n_block = sum(len(flags[k]) for k in blocking)
    avg_ws = round(total_words / len(sentences), 1) if sentences else 0
    result = {
        "article": args.article,
        "sentences": len(sentences), "total_words": total_words,
        "avg_words_per_sentence": avg_ws,
        "thresholds": {"max_sentence_words": max_sent, "max_word_len": max_word,
                       "max_consonant_run": cons_run},
        "flag_count": total_flags, "flags": flags,
        "blocking_flags": n_block, "blocking_kinds": blocking,
        "verdict": "CLEAN" if total_flags == 0 else ("BLOCK" if n_block else "WARN"),
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"Чтение вслух: {result['verdict']}  ({total_flags} флагов, "
              f"средн. {avg_ws} слов/предложение, {len(sentences)} предложений)")
        if flags["long_sentence"]:
            print(f"\n  ! Длинные предложения (>{max_sent} слов) - режь на дыхание:")
            for f in flags["long_sentence"][:12]:
                print(f"     [{f['words']}] {f['text']}")
        if flags["consonant_cluster"]:
            print(f"\n  ! Скопления согласных (≥{cons_run}) - скороговорка: "
                  + ", ".join(flags["consonant_cluster"][:15]))
        if flags["long_word"]:
            print(f"\n  ! Длинные слова (>{max_word} букв): "
                  + ", ".join(flags["long_word"][:15]))
        if flags["clunky_morphology"]:
            print("\n  ! Причастия/канцелярит (спотыкают вслух): "
                  + ", ".join(flags["clunky_morphology"][:15]))
        if flags["bare_digits"]:
            print("\n  ~ Голые цифры - предупреждение, не ошибка (проговори словами, если это текст для чтения вслух): "
                  + ", ".join(flags["bare_digits"][:15]))
        if total_flags == 0:
            print("  Грубых спотыканий нет. Финальная проверка - чтение вслух человеком.")
        if n_block:
            print(f"\n  Блокеров: {n_block} ({', '.join(k for k in blocking if flags[k])}) - exit 1.")
        elif total_flags:
            print("\n  Блокеров нет; флаги выше - список на правку (exit 0).")
    sys.exit(1 if n_block else 0)


if __name__ == "__main__":
    main()
