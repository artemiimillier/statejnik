#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_config.py - загрузчик statejnik.yaml для инструментов Статейника.

Инструменты берут пороги и пути из statejnik.yaml проекта. PyYAML не нужен:
здесь самодостаточный парсер того подмножества YAML, которым написан
templates/statejnik.example.yaml и конфиги проектов:

  - вложенные словари через отступы;
  - скаляры: строки (в кавычках и без), числа, true/false/yes/no, null/~;
  - комментарии `# ...` (в том числе после значения и после `key:`);
  - блочные списки `- item`, в том числе списки словарей (`- name: x`);
  - инлайн-списки `[a, b]` и инлайн-словари `{a: 1, b: [x, y]}` любой вложенности;
  - многострочные строки `|` и `>`.

Если установлен PyYAML - используется он (полный YAML). Если нет - встроенный
парсер. Строки, которые парсер не понял, и ключи, которых нет в схеме шаблона,
НЕ игнорируются молча: load_config печатает предупреждение в stderr.
"""
from __future__ import annotations

import os
import re
import sys

CONFIG_NAME = "statejnik.yaml"

# Схема шаблона templates/statejnik.example.yaml. None - содержимое свободное.
SCHEMA = {
    "project": {"name": None, "domain": None, "content_path": None, "region": None, "brands": None},
    "audience": {"profile": None, "jargon_level": None},
    "voice": {"person": None, "address": None, "source": None, "forbidden": None},
    "cta": {"offer": None, "url": None},
    "editorial": {"banned_words": None, "notes": None},
    "seo": {"seeds": None, "regions": None, "competitors": None, "exclude": None},
    "publish": {"auto": None, "default": None, "targets": None},
    "report": {"channel": None},
    "tools": {
        "originality": {"max_cosine": None, "max_ngram": None, "ngram_n": None},
        "read_aloud": {"max_sentence_words": None, "max_word_len": None, "max_consonant_run": None},
        "cadence": {"max_per_1k": None},
        "structure": {"min_internal_links": None},
        "site": {"max_urls": None, "max_pages": None},
    },
}


def find_config(explicit=None):
    """Найти конфиг: явный путь → $STATEJNIK_CONFIG → ./statejnik.yaml в рабочей папке."""
    for c in (explicit, os.environ.get("STATEJNIK_CONFIG"), os.path.join(os.getcwd(), CONFIG_NAME)):
        if c and os.path.isfile(c):
            return c
    return None


def parse_yaml(raw, warnings=None):
    """Разобрать YAML-текст: PyYAML, если есть, иначе встроенный парсер."""
    try:
        import yaml  # type: ignore
        data = yaml.safe_load(raw)
        return data if isinstance(data, dict) else {}
    except Exception:  # ImportError или ошибка разбора - пробуем свой парсер
        pass
    return _mini_yaml(raw, warnings)


def load_config(path, warn=True):
    """Прочитать конфиг в dict. Нет файла - пустой dict (работаем на дефолтах).

    Непонятые строки и неизвестные ключи печатаются предупреждением в stderr.
    """
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
    except OSError as e:
        if warn:
            print(f"{path}: не читается ({e}); работаю на значениях по умолчанию", file=sys.stderr)
        return {}
    warnings = []
    data = parse_yaml(raw, warnings)
    warnings += schema_warnings(data)
    if warn:
        for w in warnings:
            print(f"{os.path.basename(path)}: предупреждение: {w}", file=sys.stderr)
    return data


def schema_warnings(data, schema=SCHEMA, prefix=""):
    """Список предупреждений: неизвестные ключи и разделы неверного типа."""
    out = []
    if not isinstance(data, dict):
        return out
    for key, val in data.items():
        path = f"{prefix}{key}"
        if key not in schema:
            out.append(f"неизвестный ключ «{path}» - скрипты его не читают (опечатка?)")
            continue
        sub = schema[key]
        if isinstance(sub, dict) and val is not None:
            if not isinstance(val, dict):
                out.append(f"«{path}» должен быть разделом (вложенные ключи), а прочитано: {str(val)[:60]!r}")
                continue
            out += schema_warnings(val, sub, path + ".")
    return out


def get(cfg, dotted, default=None):
    """Достать вложенный ключ по точечному пути: get(cfg, 'tools.cadence.max_per_1k')."""
    node = cfg
    for part in dotted.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return default
    return node if node is not None else default


def as_list(value):
    """Значение конфига как список строк: None → [], строка → [строка]."""
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if v is not None and str(v).strip()]
    return [str(value)]


# ── Встроенный парсер YAML-подмножества ──────────────────────────────────────

def _strip_comment(s):
    """Отрезать хвостовой комментарий ` #...` вне кавычек."""
    quote = None
    for i, ch in enumerate(s):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            if i == 0 or s[i - 1] in " \t[{,:":
                quote = ch
        elif ch == "#" and (i == 0 or s[i - 1] in " \t"):
            return s[:i].rstrip()
    return s.rstrip()


def _scalar(token):
    """Привести строковый скаляр к bool/int/float/str/None, снять кавычки."""
    s = _strip_comment(token.strip()).strip()
    if not s:
        return None
    if s[0] in "\"'" and len(s) >= 2 and s[-1] == s[0]:
        inner = s[1:-1]
        if s[0] == '"':
            inner = inner.replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\")
        else:
            inner = inner.replace("''", "'")
        return inner
    low = s.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if low in ("null", "~", "none"):
        return None
    if re.fullmatch(r"[-+]?\d+", s):
        return int(s)
    if re.fullmatch(r"[-+]?(\d+\.\d*|\.\d+|\d+(\.\d*)?[eE][-+]?\d+)", s):
        return float(s)
    return s


class _FlowError(ValueError):
    pass


def _parse_flow(s):
    """Разобрать инлайн-значение: [..], {..} любой вложенности или скаляр."""
    s = s.strip()
    val, pos = _flow_value(s, 0)
    if s[pos:].strip():
        raise _FlowError(f"лишнее после значения: {s[pos:].strip()[:30]!r}")
    return val


def _skip_ws(s, i):
    while i < len(s) and s[i] in " \t":
        i += 1
    return i


def _flow_value(s, i, stop=",]}"):
    i = _skip_ws(s, i)
    if i >= len(s):
        return None, i
    ch = s[i]
    if ch == "[":
        items, i = [], _skip_ws(s, i + 1)
        if i < len(s) and s[i] == "]":
            return items, i + 1
        while True:
            v, i = _flow_value(s, i)
            items.append(v)
            i = _skip_ws(s, i)
            if i >= len(s):
                raise _FlowError("не закрыт список [")
            if s[i] == ",":
                i = _skip_ws(s, i + 1)
                if i < len(s) and s[i] == "]":
                    return items, i + 1
                continue
            if s[i] == "]":
                return items, i + 1
            raise _FlowError(f"ожидалась запятая или ] (символ {s[i]!r})")
    if ch == "{":
        d, i = {}, _skip_ws(s, i + 1)
        if i < len(s) and s[i] == "}":
            return d, i + 1
        while True:
            k, i = _flow_value(s, i, stop=":,}")
            i = _skip_ws(s, i)
            if i < len(s) and s[i] == ":":
                v, i = _flow_value(s, i + 1)
            else:
                v = None
            d[str(k)] = v
            i = _skip_ws(s, i)
            if i >= len(s):
                raise _FlowError("не закрыт словарь {")
            if s[i] == ",":
                i = _skip_ws(s, i + 1)
                if i < len(s) and s[i] == "}":
                    return d, i + 1
                continue
            if s[i] == "}":
                return d, i + 1
            raise _FlowError(f"ожидалась запятая или }} (символ {s[i]!r})")
    if ch in "\"'":
        j = i + 1
        while j < len(s):
            if s[j] == ch:
                if ch == "'" and j + 1 < len(s) and s[j + 1] == "'":
                    j += 2
                    continue
                if ch == '"' and s[j - 1] == "\\":
                    j += 1
                    continue
                break
            j += 1
        if j >= len(s):
            raise _FlowError("не закрыта кавычка")
        return _scalar(s[i:j + 1]), j + 1
    j = i
    while j < len(s) and s[j] not in stop.replace(":", ""):
        # «:» - разделитель ключа, только если за ним пробел/конец (иначе это URL)
        if s[j] == ":" and ":" in stop and (j + 1 >= len(s) or s[j + 1] in " \t,}"):
            break
        j += 1
    return _scalar(s[i:j]), j


_KEY_RE = re.compile(r"""^("(?:[^"\\]|\\.)*"|'(?:[^']|'')*'|[^\s#'"\[\]{}\-][^#]*?|-\S[^#]*?)\s*:(?:\s+(.*)|)$""")


def _split_key(text):
    """'key: value' → (key, value) или None, если строка не ключевая."""
    m = _KEY_RE.match(text)
    if not m:
        return None
    key = m.group(1)
    if key[0] in "\"'":
        key = _scalar(key)
    return str(key), m.group(2) or ""


def _mini_yaml(raw, warnings=None):
    """Разобрать YAML-подмножество по отступам. Непонятое - в warnings (список строк)."""
    if warnings is None:
        warnings = []
    lines = []
    for n, rl in enumerate(raw.replace("\t", "    ").splitlines(), 1):
        lines.append([len(rl) - len(rl.lstrip(" ")), rl.strip(), rl, n])
    p = _Parser(lines, warnings)
    i = p.next_sig(0)
    if i >= len(lines):
        return {}
    val, i = p.block(i, lines[i][0])
    i = p.next_sig(i)
    while i < len(lines):
        warnings.append(f"строка {lines[i][3]}: не разобрана: {lines[i][1][:60]!r}")
        i = p.next_sig(i + 1)
    return val if isinstance(val, dict) else {}


class _Parser:
    def __init__(self, lines, warnings):
        self.l = lines
        self.w = warnings

    def next_sig(self, i):
        while i < len(self.l) and (not self.l[i][1] or self.l[i][1].startswith("#")):
            i += 1
        return i

    @staticmethod
    def _is_item(text):
        return text == "-" or text.startswith("- ")

    def block(self, i, ind):
        if self._is_item(self.l[i][1]):
            return self.seq(i, ind)
        return self.mapping(i, ind)

    def value_after_key(self, i, ind, rest, lineno):
        """Значение ключа: rest - текст после «key:»; i - индекс следующей строки."""
        rest_nc = _strip_comment(rest).strip()
        if rest_nc in ("|", ">", "|-", ">-", "|+", ">+"):
            return self.block_scalar(i, ind, rest_nc[0] == ">")
        if rest_nc == "":
            j = self.next_sig(i)
            if j < len(self.l):
                cind, ctext = self.l[j][0], self.l[j][1]
                if cind > ind:
                    return self.block(j, cind)
                if cind == ind and self._is_item(ctext):
                    return self.seq(j, ind)
            return None, i
        if rest_nc[0] in "[{":
            try:
                return _parse_flow(rest_nc), i
            except _FlowError as e:
                self.w.append(f"строка {lineno}: инлайн-значение не разобрано ({e}): {rest_nc[:60]!r}")
                return rest_nc, i
        return _scalar(rest_nc), i

    def mapping(self, i, ind):
        d = {}
        while True:
            i = self.next_sig(i)
            if i >= len(self.l):
                return d, i
            cind, text, _, lineno = self.l[i]
            if cind < ind:
                return d, i
            if cind > ind:
                self.w.append(f"строка {lineno}: лишний отступ, строка пропущена: {text[:60]!r}")
                i += 1
                continue
            if self._is_item(text):
                return d, i
            kv = _split_key(text)
            if kv is None:
                self.w.append(f"строка {lineno}: ожидался «ключ: значение», строка пропущена: {text[:60]!r}")
                i += 1
                continue
            key, rest = kv
            if key in d:
                self.w.append(f"строка {lineno}: ключ «{key}» повторяется, беру последнее значение")
            val, i = self.value_after_key(i + 1, ind, rest, lineno)
            d[key] = val

    def seq(self, i, ind):
        out = []
        while True:
            i = self.next_sig(i)
            if i >= len(self.l):
                return out, i
            cind, text, _, lineno = self.l[i]
            if cind != ind or not self._is_item(text):
                if cind > ind:
                    self.w.append(f"строка {lineno}: лишний отступ в списке, строка пропущена: {text[:60]!r}")
                    i += 1
                    continue
                return out, i
            content = text[1:].lstrip(" ")
            if not content or content.startswith("#"):
                j = self.next_sig(i + 1)
                if j < len(self.l) and self.l[j][0] > ind:
                    v, i = self.block(j, self.l[j][0])
                else:
                    v, i = None, i + 1
                out.append(v)
                continue
            if content[0] not in "[{\"'" and _split_key(content) is not None:
                # «- key: v» - элемент-словарь; его ключи стоят на отступе offset
                offset = ind + (len(text) - len(content))
                self.l[i] = [offset, content, self.l[i][2], lineno]
                v, i = self.mapping(i, offset)
                out.append(v)
                continue
            if content[0] in "[{":
                try:
                    out.append(_parse_flow(_strip_comment(content)))
                except _FlowError as e:
                    self.w.append(f"строка {lineno}: элемент списка не разобран ({e})")
                    out.append(content)
            else:
                out.append(_scalar(content))
            i += 1

    def block_scalar(self, i, ind, fold):
        body = []
        while i < len(self.l):
            cind, text, rl, _ = self.l[i]
            if text and cind <= ind:
                break
            body.append(rl)
            i += 1
        while body and not body[-1].strip():
            body.pop()
        pad = min((len(x) - len(x.lstrip(" ")) for x in body if x.strip()), default=0)
        body = [x[pad:] for x in body]
        if fold:
            return " ".join(x.strip() for x in body if x.strip()), i
        return "\n".join(body), i
