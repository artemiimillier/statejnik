#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch-source.py - сохранить источник для реестра фактов: скачать страницу и
записать читаемый текст с шапкой в work/<slug>/sources/<имя>.md.

    python3 fetch-source.py https://example.com/pricing --out work/<slug>/sources/C1-pricing.md
    python3 fetch-source.py https://example.com/doc --out work/<slug>/sources/doc.md --raw work/<slug>/sources/doc.html

Что делает:
  - GET через urllib с браузерным User-Agent (при отказе - повтор с User-Agent
    скрипта), по редиректам; внутренние/частные адреса запрещены на каждом шаге
    (_net.py; --allow-private снимает защиту для локального сайта);
  - из HTML берёт основной текст: заголовки (# ..), абзацы, списки (- ..),
    таблицы (| .. |), цитаты, код; выбрасывает script/style/nav/footer/формы;
    если есть <article> или <main> - берёт его (--full-page - всю страницу);
  - text/plain, markdown, JSON сохраняет как есть; PDF - только --raw (текст из
    PDF не извлекается, код 2);
  - пишет шапку:
        <!-- statejnik:source
        url: <исходный адрес>
        final_url: <адрес после всех редиректов>   ← его писать в claims.json → source_url
        fetched_at: <UTC ISO>
        http_status: <код>
        content_type: <тип>
        title: <title страницы>
        sha256_raw: <хеш полученных байтов>
        chars: <длина текста>
        -->
    затем текст. Текст сохраняется целиком, без обрезки (источник не режут).

Коды выхода: 0 - сохранено; 1 - сеть/HTTP-ошибка (>=400)/запрещённый адрес;
2 - текста меньше порога --min-chars (по умолч. 500): страница рисуется скриптами,
    закрыта антиботом или это PDF. Файл всё равно записан с пометкой
    `status: too_short`. Что делать: открыть страницу браузером (browser-инструмент
    агента) и сохранить видимый текст в тот же файл вручную, сохранив шапку.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import os
import re
import sys
from html.parser import HTMLParser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _net import fetch, decode_body  # noqa: E402

SKIP = {"script", "style", "noscript", "svg", "template", "iframe", "canvas", "form", "button", "select",
        "option", "nav", "footer", "header", "aside", "menu", "dialog", "object", "embed"}
BLOCK = {"p", "div", "section", "article", "main", "br", "hr", "li", "ul", "ol", "dl", "dt", "dd", "tr",
         "table", "thead", "tbody", "tfoot", "blockquote", "pre", "figure", "figcaption", "h1", "h2", "h3",
         "h4", "h5", "h6", "address", "details", "summary"}
VOID = {"br", "hr", "img", "input", "meta", "link", "area", "base", "col", "source", "track", "wbr"}


class TextExtractor(HTMLParser):
    """HTML → читаемый Markdown-подобный текст."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []          # готовые блоки
        self.buf = []          # текущая строка
        self.skip = 0          # глубина внутри пропускаемого элемента
        self.skip_tag = None   # какой тег открыл пропуск
        self.pre = 0
        self.list_stack = []
        self.table = None      # список строк, строка - список ячеек
        self.cell = None
        self.prefix = ""
        self.quote = 0

    # служебное
    def _flush(self):
        text = "".join(self.buf)
        self.buf = []
        if self.pre:
            if text.strip("\n"):
                self.out.append(text.rstrip())
            return
        text = re.sub(r"[ \t\r\n\u00a0]+", " ", text).strip()
        if text:
            line = self.prefix + text
            if self.quote:
                line = "> " + line
            self.out.append(line)
        self.prefix = ""

    def handle_starttag(self, tag, attrs):
        if self.skip:
            if tag == self.skip_tag:
                self.skip += 1
            return
        a = dict(attrs)
        hidden = a.get("hidden") is not None or "display:none" in (a.get("style") or "").replace(" ", "")
        if tag in SKIP or hidden:
            if tag not in VOID:
                self.skip, self.skip_tag = 1, tag
            return
        if self.table is not None and tag in ("td", "th"):
            self.cell = []
            return
        if self.table is not None and tag == "tr":
            self.table.append([])
            return
        if tag == "table":
            self._flush()
            self.table = []
            return
        if tag in BLOCK:
            self._flush()
        if re.fullmatch(r"h[1-6]", tag):
            self.prefix = "#" * int(tag[1]) + " "
        elif tag in ("ul", "ol"):
            self.list_stack.append([tag, 0])
        elif tag == "li":
            depth = max(len(self.list_stack) - 1, 0)
            if self.list_stack and self.list_stack[-1][0] == "ol":
                self.list_stack[-1][1] += 1
                self.prefix = "  " * depth + f"{self.list_stack[-1][1]}. "
            else:
                self.prefix = "  " * depth + "- "
        elif tag == "blockquote":
            self.quote += 1
        elif tag == "pre":
            self.pre += 1
            self.out.append("```")
        elif tag == "br":
            self._flush()
        elif tag == "img" and a.get("alt"):
            self.buf.append(f"[картинка: {a['alt']}]")

    def handle_startendtag(self, tag, attrs):
        if not self.skip and tag not in SKIP:
            self.handle_starttag(tag, attrs) if tag in VOID else None

    def handle_endtag(self, tag):
        if self.skip:
            if tag == self.skip_tag:
                self.skip -= 1
                if not self.skip:
                    self.skip_tag = None
            return
        if self.table is not None and tag in ("td", "th"):
            if self.cell is not None:
                txt = re.sub(r"\s+", " ", "".join(self.cell)).strip().replace("|", "/")
                if not self.table:
                    self.table.append([])
                self.table[-1].append(txt)
            self.cell = None
            return
        if tag == "table" and self.table is not None:
            rows = [r for r in self.table if any(c for c in r)]
            if rows:
                width = max(len(r) for r in rows)
                rows = [r + [""] * (width - len(r)) for r in rows]
                self.out.append("| " + " | ".join(rows[0]) + " |")
                self.out.append("|" + "---|" * width)
                for r in rows[1:]:
                    self.out.append("| " + " | ".join(r) + " |")
            self.table = None
            return
        if tag == "pre" and self.pre:
            self._flush()
            self.pre -= 1
            self.out.append("```")
            return
        if tag in ("a", "span", "label", "td", "th", "button"):
            (self.cell if self.cell is not None else self.buf).append(" ")
        if tag in BLOCK:
            self._flush()
        if tag in ("ul", "ol") and self.list_stack:
            self.list_stack.pop()
        if tag == "blockquote" and self.quote:
            self.quote -= 1

    def handle_data(self, data):
        if self.skip:
            return
        if self.cell is not None:
            self.cell.append(data)
        elif self.table is not None:
            return
        else:
            self.buf.append(data)

    def text(self):
        self._flush()
        lines, prev_blank = [], False
        for line in self.out:
            lines.append(line)
            lines.append("")
        txt = "\n".join(lines)
        txt = re.sub(r"\n(\|[^\n]*)\n\n(?=\|)", r"\n\1\n", txt)   # строки таблицы подряд
        while re.search(r"\n(\|[^\n]*)\n\n(?=\|)", txt):
            txt = re.sub(r"\n(\|[^\n]*)\n\n(?=\|)", r"\n\1\n", txt)
        txt = re.sub(r"\n{3,}", "\n\n", txt)
        return txt.strip() + "\n"


def main_region(doc, full_page=False):
    if full_page:
        return doc
    for tag in ("article", "main"):
        blocks = re.findall(rf"(?is)<{tag}\b[^>]*>.*?</{tag}>", doc)
        if blocks:
            best = max(blocks, key=len)
            if len(re.sub(r"(?s)<[^>]+>", "", best)) > 400:
                return best
    m = re.search(r"(?is)<body\b[^>]*>(.*)</body>", doc)
    return m.group(1) if m else doc


def html_to_text(doc, full_page=False):
    p = TextExtractor()
    p.feed(main_region(doc, full_page))
    p.close()
    return p.text()


def page_title(doc):
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", doc)
    return re.sub(r"\s+", " ", html.unescape(m.group(1))).strip() if m else ""


def main():
    ap = argparse.ArgumentParser(description="Скачать источник и сохранить читаемый текст с шапкой (url, дата, HTTP-код).",
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("url")
    ap.add_argument("--out", required=True, help="куда сохранить, обычно work/<slug>/sources/<имя>.md")
    ap.add_argument("--raw", help="дополнительно сохранить полученные байты как есть (html/pdf)")
    ap.add_argument("--min-chars", type=int, default=500, help="порог «текста мало» (по умолч. 500 знаков)")
    ap.add_argument("--full-page", action="store_true", help="не искать <article>/<main>, брать всю страницу")
    ap.add_argument("--timeout", type=int, default=40)
    ap.add_argument("--allow-private", action="store_true", help="разрешить внутренние адреса (локальный сайт)")
    a = ap.parse_args()

    status = headers = raw = final = None
    last_err = None
    for browser in (True, False):
        try:
            status, headers, raw, final = fetch(a.url, timeout=a.timeout, allow_private=a.allow_private,
                                                browser_ua=browser)
        except ValueError as e:
            print(f"ошибка: {e}", file=sys.stderr)
            sys.exit(1)
        except Exception as e:  # сеть, SSL, петля редиректов
            last_err = e
            continue
        if status in (403, 429, 503) and browser:
            last_err = f"HTTP {status}"
            continue
        break
    if raw is None:
        print(f"ошибка: {a.url} не скачался: {last_err}. Откройте браузером и сохраните текст вручную.",
              file=sys.stderr)
        sys.exit(1)

    ctype = (headers or {}).get("Content-Type") or (headers or {}).get("content-type") or ""
    if a.raw:
        os.makedirs(os.path.dirname(os.path.abspath(a.raw)), exist_ok=True)
        with open(a.raw, "wb") as f:
            f.write(raw)
    title, text = "", ""
    if "pdf" in ctype.lower() or raw[:5] == b"%PDF-":
        text = ""
        note = "PDF: текст не извлекается этим скриптом - сохраните PDF (--raw) и извлеките текст отдельно"
    else:
        doc = decode_body(raw, headers)
        if "html" in ctype.lower() or re.search(r"(?i)<(html|body|p|div)\b", doc[:5000]):
            title = page_title(doc)
            text = html_to_text(doc, a.full_page)
        elif "json" in ctype.lower():
            try:
                text = json.dumps(json.loads(doc), ensure_ascii=False, indent=1) + "\n"
            except ValueError:
                text = doc
        else:
            text = doc if doc.endswith("\n") else doc + "\n"
        note = ""
    chars = len(re.sub(r"\s+", "", text))
    too_short = chars < a.min_chars
    fetched = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    head = ["<!-- statejnik:source", f"url: {a.url}", f"final_url: {final}", f"fetched_at: {fetched}",
            f"http_status: {status}", f"content_type: {ctype}", f"title: {title}",
            f"sha256_raw: {hashlib.sha256(raw).hexdigest()}", f"chars: {chars}"]
    if too_short:
        head.append("status: too_short")
    if note:
        head.append(f"note: {note}")
    head.append("-->")
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write("\n".join(head) + "\n\n" + text)

    if (status or 0) >= 400:
        print(f"ошибка: HTTP {status} для {final}. Сохранено как есть в {a.out}, но это не источник.", file=sys.stderr)
        sys.exit(1)
    redirect = f" (после редиректов: {final})" if final != a.url else ""
    print(f"{a.out}: HTTP {status}, {chars} знаков текста{redirect}. «{title[:70]}»")
    if final != a.url:
        print(f"  В claims.json → source_url пишите конечный адрес: {final}")
    if too_short:
        print(f"ТЕКСТА МАЛО ({chars} < {a.min_chars}): страница рисуется скриптами, закрыта антиботом или это PDF.\n"
              "  Откройте страницу браузером (browser-инструмент агента), скопируйте видимый текст целиком\n"
              f"  и сохраните в {a.out} под той же шапкой (исправьте fetched_at, уберите status: too_short).",
              file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
