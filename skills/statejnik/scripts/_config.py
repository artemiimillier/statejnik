#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_config.py - крошечный загрузчик statejnik.yaml для инструментов «Скилла статей».

Зачем отдельный модуль. Инструменты config-driven: пороги и пути берутся из
statejnik.yaml проекта. Но ставить ради этого PyYAML - лишняя зависимость. Здесь
самодостаточный мини-парсер ПОДМНОЖЕСТВА YAML, которого хватает для нашего
конфига: вложенные секции через отступы, скаляры (строки/числа/булевы),
инлайн-списки `[a, b]` и блочные списки (`- item`). Чистый stdlib.

Если в проекте уже стоит PyYAML - модуль использует его (точнее и полнее).
Если нет - падает на встроенный мини-парсер и продолжает работать.

Поддерживаемая форма конфига (всё опционально, берутся только нужные ключи):

    tools:
      originality:
        max_cosine: 0.50      # порог косинуса TF-IDF: выше → клон-риск
        max_ngram: 0.30       # порог Jaccard по n-граммам (дословные куски)
        ngram_n: 5            # длина словесной n-граммы
      read_aloud:
        max_sentence_words: 28
        max_word_len: 17
        max_consonant_run: 5
      cadence:
        max_per_1k: 8.0       # суммарная плотность AI-маркеров на 1000 слов
    voice:
      source: "./voice-samples" # папка с образцами голоса (опц., для калибровки)

Все функции чистые, кроме чтения самого файла конфига.
"""
from __future__ import annotations

import os
import re


CONFIG_NAME = "statejnik.yaml"


def find_config(explicit=None):
    """Найти конфиг: явный путь → $STATEJNIK_CONFIG → ./statejnik.yaml в рабочей папке.

    Чужой ./statejnik.yaml намеренно не читаем: в репозитории сайта он почти всегда
    означает что-то другое.
    """
    for c in (explicit, os.environ.get("STATEJNIK_CONFIG"), os.path.join(os.getcwd(), CONFIG_NAME)):
        if c and os.path.isfile(c):
            return c
    return None


def parse_yaml(raw):
    """Разобрать YAML-текст: PyYAML, если есть, иначе встроенный мини-парсер."""
    try:
        import yaml  # type: ignore
        data = yaml.safe_load(raw)
        return data if isinstance(data, dict) else {}
    except ImportError:
        return _mini_yaml(raw)
    except Exception:
        return _mini_yaml(raw)


def load_config(path):
    """Прочитать конфиг в dict. Нет файла или ошибка чтения - пустой dict (работаем на дефолтах)."""
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
    except OSError:
        return {}
    # Если есть настоящий PyYAML - берём его, он надёжнее мини-парсера.
    try:
        import yaml  # type: ignore
        data = yaml.safe_load(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return _mini_yaml(raw)


def get(cfg, dotted, default=None):
    """Достать вложенный ключ по точечному пути: get(cfg, 'tools.cadence.max_per_1k')."""
    node = cfg
    for part in dotted.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return default
    return node if node is not None else default


# ── Мини-парсер YAML-подмножества (fallback, когда PyYAML недоступен) ────────────

def _scalar(token):
    """Привести строковый скаляр к bool/int/float/str/None, снять кавычки и хвостовой #комментарий."""
    s = token.strip()
    if not s:
        return ""
    # обрезать хвостовой комментарий, если значение не в кавычках
    if s[0] not in "\"'":
        hashpos = s.find(" #")
        if hashpos != -1:
            s = s[:hashpos].strip()
    if s[0] in "\"'":
        end = s.find(s[0], 1)
        if end != -1:
            return s[1:end]
    low = s.lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    if low in ("null", "~", "none", ""):
        return None
    if re.fullmatch(r"[-+]?\d+", s):
        return int(s)
    if re.fullmatch(r"[-+]?\d*\.\d+", s):
        return float(s)
    return s


def _split_top_commas(s):
    """Разбить строку по запятым верхнего уровня, уважая кавычки."""
    out, buf, quote = [], [], None
    for ch in s:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            buf.append(ch)
        elif ch == ",":
            out.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        out.append("".join(buf))
    return [p.strip() for p in out]


def _inline_list(token):
    inner = token.strip()[1:-1].strip()
    if not inner:
        return []
    return [_scalar(p) for p in _split_top_commas(inner)]


def _mini_yaml(raw):
    """
    Разобрать вложенные секции / скаляры / списки по отступам.

    Модель: стек кадров [indent, parent_dict, key]. Каждый кадр описывает «куда
    кладётся содержимое этого уровня» - в parent_dict под key. Ключ с пустым
    значением открывает новый уровень: его наполнение (вложенные ключи ИЛИ
    блочный список `- item`) определяется первой строкой-потомком. Поэтому dict
    создаётся лениво - только когда реально приходит вложенный `key:`, а список -
    когда приходит `- `. Так `voice.forbidden:` + `  - item` корректно становится
    списком на любой глубине.

    Покрывает наш statejnik.yaml; экзотику YAML (якоря, многострочные блоки `|`/`>`)
    сознательно не трогает.
    """
    root = {}
    # base-кадр: всё верхнего уровня кладётся в root[None]→root напрямую через _put
    stack = [[-1, {"__root__": root}, "__root__"]]

    def _container(frame):
        """Лениво вернуть dict, лежащий в parent[key], создав его при нужде."""
        parent, key = frame[1], frame[2]
        cur = parent.get(key)
        if not isinstance(cur, dict):
            cur = {}
            parent[key] = cur
        return cur

    def _list(frame):
        """Лениво вернуть list, лежащий в parent[key], создав его при нужде."""
        parent, key = frame[1], frame[2]
        cur = parent.get(key)
        if not isinstance(cur, list):
            cur = []
            parent[key] = cur
        return cur

    block = None  # [indent_of_key, container, key, lines, fold] для `|` / `>`
    for rawline in raw.splitlines():
        if block is not None:
            ind = len(rawline) - len(rawline.lstrip(" "))
            if not rawline.strip() or ind > block[0]:
                block[3].append(rawline)
                continue
            _close_block(block)
            block = None
        if not rawline.strip() or rawline.lstrip().startswith("#"):
            continue
        indent = len(rawline) - len(rawline.lstrip(" "))
        line = rawline.strip()

        # подняться до кадра, чей уровень строго меньше текущего отступа
        while len(stack) > 1 and indent <= stack[-1][0]:
            stack.pop()
        frame = stack[-1]

        # элемент блочного списка: «- value» - наполняет список текущего кадра
        if line.startswith("- ") or line == "-":
            item = _scalar(line[2:]) if len(line) > 2 else None
            _list(frame).append(item)
            continue

        if ":" not in line:
            continue
        key, _, rest = line.partition(":")
        key = key.strip()
        rest = rest.strip()
        container = _container(frame)  # вложенный ключ → текущий уровень это dict

        if rest == "":
            # открыть новый уровень; чем он наполнится (dict/список) - решит потомок
            stack.append([indent, container, key])
            continue

        if rest in ("|", ">", "|-", ">-", "|+", ">+"):
            block = [indent, container, key, [], rest[0] == ">"]
            continue

        if rest.startswith("[") and rest.endswith("]"):
            container[key] = _inline_list(rest)
            continue

        container[key] = _scalar(rest)

    if block is not None:
        _close_block(block)
    return root


def _close_block(block):
    """Закрыть многострочный скаляр `|` (как есть) или `>` (склеить в строку)."""
    _, container, key, lines, fold = block
    while lines and not lines[-1].strip():
        lines.pop()
    body = [l for l in lines]
    pad = min((len(l) - len(l.lstrip(" ")) for l in body if l.strip()), default=0)
    body = [l[pad:] for l in body]
    container[key] = " ".join(l.strip() for l in body if l.strip()) if fold else "\n".join(body)
