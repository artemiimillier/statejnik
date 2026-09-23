#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ai-cadence-check.py — механический детектор AI-почерка в русском тексте.

Ловит то, что вычитка пропускает «на глаз»: накопительную плотность симметрий и
шаблонов, которыми нейросеть выдаёт себя. Живой автор пишет криво и асимметрично;
модель — гладко и симметрично. Считаем ПЛОТНОСТЬ на 1000 слов (а не «подряд / не
подряд»): одна антитеза на абзац по всему тексту — это уже почерк.

Категории маркеров:
  A) антитезы «не X, а Y» / разорванная «Это не А. Это Б» / стартовое «а не …»
  B) одиночные слова через точку («Просто. Понятно. Эффективно.»)
  C) литературные обороты («эпоха X закончилась», «настоящая революция», «фундаментально»…)
  D) канцелярит («таким образом», «важно отметить», «следует учитывать»…)
  E) безличные конструкции / крючки-подзаголовки («вы узнаете», «и самое мощное»…)
  F) длинное тире (—) и en-dash (–) — типографика, которую любит генерация
  G) параллельные пары — два предложения подряд с одинаковым контентным стартом

Главная метрика — СУММАРНАЯ плотность маркеров A–E,G на 1000 слов (категория F
считается отдельно как hard-флаг типографики). Порог:
  tools.cadence.max_per_1k   (по умолч. 8.0)

Калибровка под себя (опционально). Если у вас есть папка с собственными текстами
(`config.voice.source` или флаг --baseline-dir), можно прогнать её и увидеть, какая
плотность маркеров у ВАШЕГО живого письма — и выставить порог чуть выше неё. Папка
ваша, не зашита в скрипт.

Зависимости: только стандартная библиотека Python 3 (re, json, os, argparse).
PyYAML не требуется.

Использование:
  python3 scripts/ai-cadence-check.py <статья.md>
  python3 scripts/ai-cadence-check.py <статья.md> --max-per-1k 6 --json
  python3 scripts/ai-cadence-check.py <статья.md> --config ./statejnik.yaml
  python3 scripts/ai-cadence-check.py --baseline-dir ./my-texts/   # печать плотностей корпуса
  python3 scripts/ai-cadence-check.py --baseline <файл.txt>        # печать плотностей файла

Коды выхода: 0 = CLEAN (в пределах порога), 1 = REVIEW (превышение / длинное тире), 3 = ошибка.
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

DEFAULT_MAX_PER_1K = 8.0

TEXT_EXTS = (".md", ".markdown", ".txt", ".mdx", ".html", ".htm", ".rst", ".text")

# Разговорные/служебные старты — их повтор в начале предложений НЕ считаем
# параллелизмом (это нормальная связка, а не AI-симметрия).
CONNECTOR_STARTS = {
    "и", "а", "но", "ну", "вот", "да", "нет", "так", "это", "я", "ты", "мы",
    "он", "она", "они", "потому", "что", "значит", "то", "тут", "там", "если",
    "когда", "ещё", "еще", "или", "уже", "также", "тоже", "здесь", "при",
}

# Короткие предложения, которые в норме — живые реплики, а не AI-«панчи».
ALLOWED_SHORT = {
    "именно", "точно", "наоборот", "и наоборот", "первое", "второе", "третье",
    "например", "итог", "вывод", "коротко", "кстати", "и всё", "вот так",
}

# C) Литературные обороты и пафос (методология, Раздел 4 + Раздел 8/9).
LITERARY = [
    r"эпоха\s+\w+\s+(?:закончил|заканчивает|ушл|прошл)",
    r"эра\s+(?:ушл|закончил|настал|пришл)",
    r"настала\s+эра", r"наступила\s+эра", r"новая\s+эра",
    r"настоящая\s+революция", r"революция\s+в\s+мире",
    r"в\s+мире,?\s+где", r"в\s+эпоху",
    r"новая\s+грамотность",
    r"\bфундаментальн", r"\bкардинальн", r"\bрадикальн",
    r"по-настоящему", r"\bистина\b",
    r"будущее\s+за\s+теми,?\s+кто",
    r"это\s+меняет\s+всё", r"меняет\s+всё\b",
    r"навсегда\s+изменил", r"изменил\s+мир",
]

# D) Канцелярит и связки-штампы (методология, Раздел 5).
CLERICAL = [
    r"\bоднако\b", r"таким\s+образом", r"важно\s+отметить", r"стоит\s+отметить",
    r"стоит\s+сказать", r"следует\s+понимать", r"необходимо\s+учитывать",
    r"следует\s+учитывать", r"как\s+было\s+сказано\s+выше", r"вышеупомянут",
    r"кроме\s+того", r"более\s+того", r"при\s+этом\s+важно", r"также\s+стоит",
    r"в\s+заключение", r"подвед[ёе]м\s+итог", r"в\s+современном\s+мире",
    r"играет\s+(?:ключевую|важную)\s+роль", r"представляет\s+собой",
    r"является\s+неотъемлемой", r"с\s+одной\s+стороны.*?с\s+другой\s+стороны",
]

# E) Безличные конструкции, крючки-подзаголовки, шаблонные старты/финалы
#    (методология, Разделы 6, 7, 10).
IMPERSONAL = [
    r"вам\s+расскаж", r"вы\s+узнаете", r"здесь\s+объясн", r"здесь\s+покаж",
    r"давайте\s+разбер[ёе]м", r"давайте\s+рассмотрим", r"рассмотрим\s+подробнее",
    r"обратите\s+внимание", r"следует\s+помнить", r"важно\s+понимать,?\s+что",
    r"и\s+самое\s+(?:мощное|важное|интересное)", r"вот\s+в\s+ч[ёе]м\s+суть",
    r"а\s+теперь\s+представьте", r"и\s+тут\s+начинается\s+интересное",
    r"самое\s+(?:интересное|важное|мощное)\b",
    r"в\s+этой\s+статье\s+вы\s+узнаете", r"сегодня\s+поговорим",
    r"сегодня\s+я\s+хочу\s+поделиться",
    r"спасибо\s+за\s+внимание", r"и\s+теперь\s+вы\s+понимаете",
]


def read(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def strip_markup(text):
    """Снять markup/код/html, но СОХРАНИТЬ тире и пунктуацию (их мы и проверяем)."""
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"`[^`]*`", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"^---\n.*?\n---\n", " ", text, flags=re.DOTALL)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    # маркеры списков/заголовков убираем, но НЕ трогаем «—» и «–»
    text = re.sub(r"^\s*#{1,6}\s*", " ", text, flags=re.MULTILINE)
    text = re.sub(r"[*_>|~`]", " ", text)
    return text


def split_sentences(text):
    parts = re.split(r"(?<=[.!?…])\s+|\n+", text)
    return [p.strip() for p in parts if p.strip()]


def first_word(sent):
    m = re.match(r"[«»\"'\-—–\s]*([a-zа-яё]+)", sent.lower())
    return m.group(1) if m else ""


def count_antithesis(text, sentences):
    n, hits = 0, []
    low = text.lower()
    # «не X, а Y» — негация, затем «, а » в пределах ~45 символов
    for m in re.finditer(r"(?<![a-zа-яё])не\s+[^,.!?:;]{1,45}?,\s+а\s+", low):
        n += 1
        hits.append("«не…, а…»: …" + low[m.start():m.end()].strip())
    # стартовое «а не …» отдельным предложением
    for s in sentences:
        if re.match(r"^[«»\"'\-—–\s]*а\s+не\s+", s.lower()):
            n += 1
            hits.append("стартовое «а не …»: " + s[:50])
    # разорванная «это не … . это …» через границу предложений
    for i in range(len(sentences) - 1):
        s1, s2 = sentences[i].lower(), sentences[i + 1].lower()
        if re.search(r"\bэто\s+не\b", s1) and \
           re.match(r"^[«»\"'\-—–\s]*(?:и\s+|а\s+)?это\b", s2):
            n += 1
            hits.append("«это не…/это…»: " + sentences[i][:32] + " || " + sentences[i + 1][:32])
    return n, hits


def count_single_word_period(sentences):
    """Одиночные слова-предложения, идущие цепочкой («Просто. Понятно. Эффективно.»)."""
    n, hits = 0, []
    run = 0
    chain = []
    for s in sentences:
        core = re.sub(r"[«»\"'\-—–.,:;!?…]", "", s).strip().lower()
        words = core.split()
        is_single = (len(words) == 1 and len(core) >= 3
                     and s.rstrip().endswith(".") and core not in ALLOWED_SHORT
                     and re.fullmatch(r"[а-яё]+", core) is not None)
        if is_single:
            run += 1
            chain.append(s.strip())
        else:
            if run >= 3:  # 3+ одиночных подряд = классический AI-стаккато
                n += run
                hits.append("одиночные через точку: " + " ".join(chain[:5]))
            run = 0
            chain = []
    if run >= 3:
        n += run
        hits.append("одиночные через точку: " + " ".join(chain[:5]))
    return n, hits


def count_patterns(text, patterns, label):
    n, hits = 0, []
    low = text.lower()
    for pat in patterns:
        for m in re.finditer(pat, low, flags=re.DOTALL):
            n += 1
            frag = re.sub(r"\s+", " ", low[m.start():m.end()]).strip()
            hits.append(f"{label}: " + frag[:60])
    return n, hits


def count_parallel(sentences):
    n, hits = 0, []
    for i in range(len(sentences) - 1):
        w1, w2 = first_word(sentences[i]), first_word(sentences[i + 1])
        if not w1 or w1 != w2:
            continue
        if len(w1) < 2 or w1 in CONNECTOR_STARTS:
            continue
        n += 1
        hits.append("параллель «%s…/%s…»: %s || %s" % (
            w1, w1, sentences[i][:28], sentences[i + 1][:28]))
    return n, hits


def count_dashes(text):
    """Длинное тире (—) и en-dash (–): hard-флаг типографики генерации."""
    em = len(re.findall(r"—", text))
    en = len(re.findall(r"–", text))
    hits = []
    if em:
        hits.append(f"длинное тире (—) ×{em}")
    if en:
        hits.append(f"en-dash (–) ×{en}")
    return em + en, hits


def analyse(raw, max_per_1k):
    text = strip_markup(raw)
    words = len(re.findall(r"[a-zа-яёA-ZА-ЯЁ]+", text))
    sentences = split_sentences(text)
    if words == 0:
        return None

    a, a_hits = count_antithesis(text, sentences)
    b, b_hits = count_single_word_period(sentences)
    c, c_hits = count_patterns(text, LITERARY, "литоборот")
    d, d_hits = count_patterns(text, CLERICAL, "канцелярит")
    e, e_hits = count_patterns(text, IMPERSONAL, "безличное/крючок")
    g, g_hits = count_parallel(sentences)
    f, f_hits = count_dashes(raw)  # тире считаем по сырому тексту

    marker_total = a + b + c + d + e + g  # F (тире) — отдельный hard-флаг
    per1k = round(marker_total * 1000.0 / words, 2)

    return {
        "words": words, "sentences": len(sentences),
        "antithesis": a, "single_word_period": b, "literary": c,
        "clerical": d, "impersonal": e, "parallel": g, "dashes": f,
        "marker_total": marker_total, "per_1k": per1k, "max_per_1k": max_per_1k,
        "_hits": {"antithesis": a_hits, "single_word_period": b_hits,
                  "literary": c_hits, "clerical": d_hits, "impersonal": e_hits,
                  "parallel": g_hits, "dashes": f_hits},
    }


def collect_text_files(path):
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


def run_baseline(target, max_per_1k):
    """Печать сырых плотностей для калибровки по своему корпусу (папка или файл)."""
    files = collect_text_files(target)
    if not files:
        sys.stderr.write(f"в '{target}' нет текстов для калибровки\n")
        sys.exit(3)
    rows = []
    for fp in files:
        try:
            m = analyse(read(fp), max_per_1k)
        except OSError:
            continue
        if m and m["words"] >= 50:
            rows.append((fp, m))
    if not rows:
        sys.stderr.write("файлы слишком короткие для калибровки (< 50 слов)\n")
        sys.exit(3)
    print(f"Калибровка по {len(rows)} файлам (плотность маркеров /1000 слов):")
    total_per1k = 0.0
    for fp, m in rows:
        total_per1k += m["per_1k"]
        print(f"  {m['per_1k']:6.2f}  {os.path.basename(fp)}  "
              f"(слов {m['words']}, тире {m['dashes']})")
    avg = round(total_per1k / len(rows), 2)
    print(f"\nСредняя плотность по корпусу: {avg} /1k")
    print(f"Рекомендация: max_per_1k ≈ {round(avg + 2, 1)} "
          f"(чуть выше вашего живого письма, чтобы не ловить ложные срабатывания).")
    sys.exit(0)


def parse_args():
    p = argparse.ArgumentParser(
        prog="ai-cadence-check.py",
        description="Детектор AI-почерка в русском тексте: антитезы «не X, а Y», "
                    "«это не А. это Б», одиночные слова через точку, литобороты, "
                    "канцелярит, безличные конструкции, длинное тире, параллелизмы. "
                    "Плотность на 1000 слов против порога.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Порог: statejnik.yaml (tools.cadence.max_per_1k) → флаг CLI → дефолт 8.0. "
               "Калибровка под себя: --baseline-dir <ваша_папка>. "
               "Коды выхода: 0=CLEAN, 1=REVIEW, 3=ошибка.",
    )
    p.add_argument("article", nargs="?", help="файл статьи для проверки")
    p.add_argument("--config", default=None,
                   help="путь к statejnik.yaml (по умолчанию ./statejnik.yaml в рабочей папке)")
    p.add_argument("--max-per-1k", type=float, default=None,
                   help="порог суммарной плотности маркеров на 1000 слов")
    p.add_argument("--baseline", metavar="FILE",
                   help="режим калибровки: печать плотностей одного файла")
    p.add_argument("--baseline-dir", metavar="DIR",
                   help="режим калибровки: печать плотностей всех текстов в папке "
                        "(например, config.voice.source)")
    p.add_argument("--json", action="store_true", help="вывод машинно-читаемым JSON")
    return p.parse_args()


def main():
    args = parse_args()
    _, cfg = cfgmod.load_for(args.config, near=args.article)
    max_per_1k = args.max_per_1k if args.max_per_1k is not None \
        else float(cfgmod.get(cfg, "tools.cadence.max_per_1k", DEFAULT_MAX_PER_1K))

    # режим калибровки
    base_target = args.baseline_dir or args.baseline
    if not base_target and args.baseline_dir is None and args.baseline is None:
        # возможно, папка калибровки задана только в конфиге — но запускать её
        # самостоятельно не будем: калибровка только по явному флагу
        pass
    if args.baseline_dir or args.baseline:
        run_baseline(args.baseline_dir or args.baseline, max_per_1k)

    if not args.article:
        sys.stderr.write("укажите файл статьи (или --baseline-dir для калибровки). "
                         "Подробнее: --help\n")
        sys.exit(3)

    try:
        raw = read(args.article)
    except OSError as e:
        sys.stderr.write(f"ошибка чтения: {e}\n")
        sys.exit(3)

    m = analyse(raw, max_per_1k)
    if m is None:
        sys.stderr.write("пустой файл\n")
        sys.exit(3)

    fail_density = m["per_1k"] > max_per_1k
    fail_dashes = m["dashes"] > 0
    fails = []
    if fail_density:
        fails.append("density")
    if fail_dashes:
        fails.append("dashes")
    verdict = "REVIEW" if fails else "CLEAN"

    if args.json:
        out = {k: v for k, v in m.items() if k != "_hits"}
        out["article"] = args.article
        out["verdict"] = verdict
        out["fails"] = fails
        out["examples"] = {k: v[:6] for k, v in m["_hits"].items() if v}
        print(json.dumps(out, ensure_ascii=False, indent=2))
        sys.exit(1 if fails else 0)

    print(f"AI-каденция: {verdict}  ({m['words']} слов, {m['sentences']} предложений)")
    print(f"  антитезы «не X,а Y»/«это не…это»/«а не…» : {m['antithesis']:3d}")
    print(f"  одиночные слова через точку              : {m['single_word_period']:3d}")
    print(f"  литобороты «эпоха X закончилась»…        : {m['literary']:3d}")
    print(f"  канцелярит «таким образом»…              : {m['clerical']:3d}")
    print(f"  безличное/крючки «вы узнаете»…           : {m['impersonal']:3d}")
    print(f"  параллельные пары (одинак. старт)        : {m['parallel']:3d}")
    print(f"  ─ сумма маркеров                         : {m['marker_total']:3d}  "
          f"= {m['per_1k']:.2f} /1k  (порог ≤{max_per_1k}){'  ! ПРЕВЫШЕНО' if fail_density else ''}")
    print(f"  длинное тире (—/–) [hard-флаг]           : {m['dashes']:3d}"
          f"{'  ! ЕСТЬ — заменить на дефис' if fail_dashes else ''}")
    if fails:
        print("\n  Примеры (первые по проваленным осям):")
        order = ["antithesis", "single_word_period", "literary", "clerical",
                 "impersonal", "parallel", "dashes"]
        for key in order:
            hits = m["_hits"].get(key, [])
            # показываем примеры, если ось вносит вклад в провал
            if (fail_density and key != "dashes" and hits) or \
               (fail_dashes and key == "dashes" and hits):
                for h in hits[:5]:
                    print("   • " + h)
    else:
        print("\n  В пределах порога. Финальная вычитка — за человеком.")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
