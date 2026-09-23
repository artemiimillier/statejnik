# -*- coding: utf-8 -*-
"""Русский текст для SEO-скриптов: токены, стемминг (Snowball), транслит, интент.

Только стандартная библиотека. Стеммер - реализация алгоритма Snowball для
русского языка (snowballstem.org/algorithms/russian/stemmer.html).
"""
from __future__ import annotations

import re

_VOWELS = "аеиоуыэюя"

_PERFECTIVE_GERUND_1 = ("вшись", "вши", "в")          # после а/я
_PERFECTIVE_GERUND_2 = ("ившись", "ывшись", "ивши", "ывши", "ив", "ыв")
_ADJECTIVE = ("ими", "ыми", "его", "ого", "ему", "ому", "ее", "ие", "ые", "ое", "ей", "ий",
              "ый", "ой", "ем", "им", "ым", "ом", "их", "ых", "ую", "юю", "ая", "яя", "ою", "ею")
_PARTICIPLE_1 = ("ем", "нн", "вш", "ющ", "щ")             # после а/я
_PARTICIPLE_2 = ("ивш", "ывш", "ующ")
_REFLEXIVE = ("ся", "сь")
_VERB_1 = ("ла", "на", "ете", "йте", "ли", "й", "л", "ем", "н", "ло", "но", "ет", "ют", "ны",
           "ть", "ешь", "нно")                             # после а/я
_VERB_2 = ("ила", "ыла", "ена", "ейте", "уйте", "ите", "или", "ыли", "ей", "уй", "ил", "ыл", "им",
           "ым", "ен", "ило", "ыло", "ено", "ят", "ует", "уют", "ит", "ыт", "ены", "ить", "ыть",
           "ишь", "ую", "ю")
_NOUN = ("иями", "ями", "ами", "иях", "ией", "ием", "иям", "ев", "ов", "ие", "ье", "еи", "ии",
         "ей", "ой", "ий", "ям", "ем", "ам", "ом", "ах", "ях", "ию", "ью", "ия", "ья", "а", "е",
         "и", "й", "о", "у", "ы", "ь", "ю", "я")
_SUPERLATIVE = ("ейше", "ейш")
_DERIVATIONAL = ("ость", "ост")


def _region_after(word, start):
    """Позиция после первой согласной, идущей за гласной, начиная со start."""
    for i in range(start + 1, len(word)):
        if word[i - 1] in _VOWELS and word[i] not in _VOWELS:
            return i + 1
    return len(word)


def _longest(word, pos, endings):
    """Самое длинное окончание из endings, целиком лежащее в регионе с позиции pos."""
    best = ""
    for e in endings:
        if len(e) > len(best) and word.endswith(e) and len(word) - len(e) >= pos:
            best = e
    return best


def _strip_grouped(word, rv, group1, group2):
    """Snowball among(): берём самое длинное совпадение; для группы 1 нужна а/я перед ним."""
    e1 = _longest(word, rv, group1)
    e2 = _longest(word, rv, group2)
    if not e1 and not e2:
        return None
    if len(e2) >= len(e1):
        return word[: -len(e2)]
    base = word[: -len(e1)]
    if base and base[-1] in "ая" and len(base) - 1 >= rv:
        return base
    return None


def stem(word):
    """Основа русского слова. Латиница и цифры возвращаются как есть."""
    w = word.lower().replace("ё", "е")
    if not re.fullmatch(r"[а-я]+", w):
        return w
    rv = len(w)
    for i, ch in enumerate(w):
        if ch in _VOWELS:
            rv = i + 1
            break
    r1 = _region_after(w, 0)
    r2 = _region_after(w, r1)

    # Шаг 1
    res = _strip_grouped(w, rv, _PERFECTIVE_GERUND_1, _PERFECTIVE_GERUND_2)
    if res is not None:
        w = res
    else:
        e = _longest(w, rv, _REFLEXIVE)
        if e:
            w = w[: -len(e)]
        e = _longest(w, rv, _ADJECTIVE)
        if e:
            w = w[: -len(e)]
            res = _strip_grouped(w, rv, _PARTICIPLE_1, _PARTICIPLE_2)
            if res is not None:
                w = res
        else:
            res = _strip_grouped(w, rv, _VERB_1, _VERB_2)
            if res is not None:
                w = res
            else:
                e = _longest(w, rv, _NOUN)
                if e:
                    w = w[: -len(e)]
    # Шаг 2
    if w.endswith("и") and len(w) - 1 >= rv:
        w = w[:-1]
    # Шаг 3
    e = _longest(w, r2, _DERIVATIONAL)
    if e:
        w = w[: -len(e)]
    # Шаг 4
    if w.endswith("нн") and len(w) - 2 >= rv:
        w = w[:-1]
    else:
        e = _longest(w, rv, _SUPERLATIVE)
        if e:
            w = w[: -len(e)]
            if w.endswith("нн") and len(w) - 2 >= rv:
                w = w[:-1]
        elif w.endswith("ь") and len(w) - 1 >= rv:
            w = w[:-1]
    return w


# Слова, которые не несут темы запроса (служебные и вопросительные).
STOPWORDS = frozenset("""
в во на для и или с со по от до из к ко у о об обо а но ли же бы при за под над про без
через между к около после перед the a an of to in on for and or with
как что где когда куда откуда почему зачем сколько какой какая какое какие каким каких
чем кто чей чья чье чьи ли можно нужно надо ли это этот эта эти тот та те то так такой
мой моя мои твой свой себя сам сама самый самая самые весь вся все всё его ее её их им
""".split())

QUESTION_WORDS = ("как", "что", "где", "когда", "почему", "зачем", "сколько", "какой", "какая",
                  "какое", "какие", "чем", "кто", "можно ли", "нужно ли", "стоит ли", "надо ли")

_TOKEN_RE = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)


def normalize(phrase):
    """Нижний регистр, ё→е, один пробел; операторы Вордстата (+ ! " [ ]) убираются."""
    p = phrase.lower().replace("ё", "е")
    p = re.sub(r"[+!\"\[\]]", " ", p)
    return " ".join(p.split())


def tokens(phrase):
    return _TOKEN_RE.findall(normalize(phrase))


def stems(phrase):
    """Множество основ значимых слов запроса (без служебных и вопросительных)."""
    return frozenset(stem(t) for t in tokens(phrase) if t not in STOPWORDS and len(t) > 1)


_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z",
    "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
    "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def translit(text):
    return "".join(_TRANSLIT.get(ch) or ("" if ch in "ъь" else ch) for ch in text.lower())


def latin_skeleton(token):
    """Свести разные схемы транслита к одной: kh→h, ts→c, shch→sch, j→y, yo→e."""
    t = token.lower()
    for a, b in (("shch", "sch"), ("kh", "h"), ("ts", "c"), ("yo", "e"), ("ju", "yu"), ("ja", "ya"),
                 ("j", "y"), ("x", "ks"), ("w", "v")):
        t = t.replace(a, b)
    return t


def slug_tokens(url_path):
    """Слова из пути URL: /blog/kak-vybrat-kreslo/ → [kak, vybrat, kreslo]."""
    parts = re.split(r"[/\-_.]+", url_path.lower())
    return [latin_skeleton(p) for p in parts if p and not p.isdigit() and p not in ("html", "htm", "php")]


def prefix_match(a, b, need=5):
    """Совпадают ли слова по началу (терпимо к окончаниям): kreslo ~ kresla."""
    n = min(need, len(a), len(b))
    return n >= 3 and a[:n] == b[:n]


_COMMERCIAL = ("купить", "цена", "цены", "стоимост", "заказать", "заказ ", "доставк", "недорого",
               "дешев", "распродаж", "скидк", "магазин", "прайс", "аренд", "услуг", "под ключ",
               "оптом", "в наличии", "интернет-магазин", "руб", "₽")
_INVESTIGATION = ("отзыв", "рейтинг", "лучш", "топ ", "топ-", "сравнен", " vs ", " или ", "что лучше",
                  "какой выбрать", "какую выбрать", "какие выбрать", "обзор", "плюсы и минусы")
_INFORMATIONAL = ("как ", "что ", "что такое", "почему", "зачем", "сколько", "когда", "можно ли",
                  "нужно ли", "стоит ли", "чем ", "своими руками", "инструкц", "пошагов", "способ",
                  "виды", "правил", "ошибк", "отличие", "разница", "выбрать", "выбор", "признак",
                  "причин", "симптом", "схема", "пример", "образец", "шаблон", "значит", "это",
                  "для чего", "советы", "идеи", "уход", "как ")
_NAVIGATIONAL = ("официальный сайт", "личный кабинет", "вход", "войти", "телефон горячей",
                 "авито", "avito", "озон", "ozon", "wildberries", "вайлдберриз", "вб ", "яндекс маркет",
                 "маркетплейс", "адрес", "режим работы", ".ru", ".com")


def intent(phrase, brands=()):
    """Грубая классификация: navigational | commercial | investigation | informational | general."""
    p = " " + normalize(phrase) + " "
    if any(b and b.lower() in p for b in brands) or any(m in p for m in _NAVIGATIONAL):
        return "navigational"
    if any(m in p for m in _INVESTIGATION):
        return "investigation"
    if any(m in p for m in _COMMERCIAL):
        return "commercial"
    if any((" " + m) in p for m in _INFORMATIONAL):
        return "informational"
    return "general"


# Подходит ли запрос под статью (а не под карточку товара/страницу услуги).
ARTICLE_FIT = {"informational": 1.0, "investigation": 1.0, "general": 0.7, "commercial": 0.35,
               "navigational": 0.0}


def article_type(phrase):
    p = " " + normalize(phrase) + " "
    if any(m in p for m in (" или ", " vs ", "что лучше", "сравнен", "отличие", "разница")):
        return "сравнение"
    if any(m in p for m in ("лучш", "топ ", "топ-", "рейтинг", "идеи", "виды", "способ")):
        return "подборка"
    if any(m in p for m in ("отзыв", "обзор", "плюсы и минусы")):
        return "обзор"
    if any(m in p for m in ("как ", "своими руками", "инструкц", "пошагов", "настро", "установ")):
        return "инструкция"
    if any(m in p for m in ("что такое", "что значит", "это ", "зачем", "почему")):
        return "объяснение"
    if any(m in p for m in ("выбрать", "выбор", "какой ", "какую ", "какие ")):
        return "гид по выбору"
    return "разбор"


# ── Сопоставление по основам и границам слов ─────────────────────────────────

_WORD_RE = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)


def word_tokens(text):
    """Слова текста с позициями: [(слово_в_нижнем_регистре, start, end)], ё→е."""
    return [(m.group(0).lower().replace("ё", "е"), m.start(), m.end()) for m in _WORD_RE.finditer(text)]


def term_matches(term, text):
    """Найти вхождения слова/фразы term в text по границам слов и основам.

    «руб» не находится в «грубой» (граница слова), «аскона» находится в «асконы»
    (одна основа). Многословная фраза ищется как последовательность слов подряд.
    Возвращает список (start, end, найденный_фрагмент).
    """
    t_words = [w for w, _, _ in word_tokens(term)]
    if not t_words:
        return []
    t_stems = [stem(w) for w in t_words]
    toks = word_tokens(text)
    out = []
    n = len(t_words)
    for i in range(len(toks) - n + 1):
        ok = True
        for j in range(n):
            w = toks[i + j][0]
            if not (w == t_words[j] or (len(t_stems[j]) >= 3 and stem(w) == t_stems[j])):
                ok = False
                break
        if ok:
            s, e = toks[i][1], toks[i + n - 1][2]
            out.append((s, e, text[s:e]))
    return out


def contains_term(term, text):
    return bool(term_matches(term, text))


def translit_norm(latin):
    """Свести разные схемы транслита к одной форме для сравнения слов адреса.

    luchshe/lucse → lucse, kakoy/kakoj → kakoy, dlya/dla → dla, myagkoy/magkoj → magkoy,
    ezhednevnogo/ezednevnogo → ezednevnogo. Шипящие без h, я/ю без y, j → y.
    """
    t = latin.lower()
    for a, b in (("shch", "s"), ("sch", "s"), ("shh", "s"), ("zh", "z"), ("ch", "c"), ("sh", "s"),
                 ("kh", "h"), ("ts", "c"), ("tz", "c"), ("yo", "e"), ("jo", "e"), ("ya", "a"), ("ja", "a"),
                 ("yu", "u"), ("ju", "u"), ("ia", "a"), ("iu", "u"), ("j", "y"), ("x", "ks"), ("w", "v"),
                 ("iy", "y"), ("yy", "y"), ("'", ""), ("`", "")):
        t = t.replace(a, b)
    return re.sub(r"(.)\1+", r"\1", t)


def stem_latin(ru_stem):
    """Основа русского слова в нормализованном транслите: stem('лучше') → 'lucs'."""
    return translit_norm(translit(ru_stem))


# ── География в запросах ─────────────────────────────────────────────────────

# Другие страны (для проекта с регионом «Россия» такие запросы - мусор).
FOREIGN_GEO = ("минск", "беларус", "белорус", "гомел", "брест", "витебск", "могилев", "гродн", "бобруйск",
               "казахстан", "алмат", "астан", "караганд", "шымкент", "украин", "киев", "харьков", "одесс",
               "днепр", "львов", "запорож", "узбекистан", "ташкент", "бишкек", "кыргыз", "киргиз", "молдов",
               "кишинев", "армени", "ереван", "грузи", "тбилиси", "азербайджан", "баку")
# Города России (запрос с городом - локальный, коммерческий интент).
RU_CITIES = ("москв", "подмосков", "спб", "питер", "санкт-петербург", "петербург", "екатеринбург",
             "новосибирск", "казан", "челябинск", "самар", "омск", "ростов", "красноярск", "воронеж",
             "перми", "пермь", "волгоград", "краснодар", "саратов", "тюмен", "тольятт", "ижевск", "барнаул",
             "ульяновск", "иркутск", "хабаровск", "ярославл", "владивосток", "махачкал", "томск", "оренбург",
             "кемеров", "новокузнецк", "рязан", "астрахан", "пенз", "липецк", "чебоксар", "калининград",
             "брянск", "курск", "магнитогорск", "твери", "тверь", "ставропол", "белгород", "сочи", "калуг",
             "смоленск", "мытищ", "химк", "балаших", "подольск", "мурманск", "архангельск", "вологд",
             "сургут", "набережн", "уфа", "уфе", "уфы", "тула", "туле", "тулы", "киров", "мск")
_GEO_PHRASES = ("нижний новгород", "нижнем новгороде", "нижнего новгорода", "великий новгород",
                "ростов-на-дону", "набережные челны")


def _geo_hit(words, roots):
    for w in words:
        for r in roots:
            if w == r or (len(r) >= 4 and w.startswith(r) and len(w) - len(r) <= 4):
                return True
    return False


def foreign_geo(phrase):
    """Запрос упоминает другую страну/её город (минск, казахстан, украина...)."""
    return _geo_hit(tokens(phrase), FOREIGN_GEO)


def city_geo(phrase):
    """Запрос упоминает город России или другой страны - локальный интент."""
    p = normalize(phrase)
    if any(g in p for g in _GEO_PHRASES):
        return True
    words = tokens(p)
    return _geo_hit(words, RU_CITIES) or _geo_hit(words, FOREIGN_GEO)
