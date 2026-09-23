# -*- coding: utf-8 -*-
"""Черновик статьи: frontmatter + Markdown → HTML. Только стандартная библиотека.

Поддерживается то, что пишет методология: заголовки, абзацы, списки, цитаты,
fenced-код, таблицы, ссылки, картинки, **жирный**, *курсив*, `код`.
"""
from __future__ import annotations

import html
import re

from _config import parse_yaml


def split_frontmatter(text):
    text = text.lstrip("\ufeff")
    m = re.match(r"^(?:```ya?ml\s*\n)?---\s*\n(.*?)\n---\s*\n(?:```\s*\n)?", text, re.S)
    if not m:
        return {}, text
    return parse_yaml(m.group(1)) or {}, text[m.end():]


def _inline(s):
    s = html.escape(s, quote=False)
    codes = []
    s = re.sub(r"`([^`]+)`", lambda m: codes.append(m.group(1)) or f"\x00{len(codes) - 1}\x00", s)
    s = re.sub(r"!\[([^\]]*)\]\(([^)\s]+)\)", r'<img src="\2" alt="\1">', s)
    s = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", r'<a href="\2">\1</a>', s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<![*\w])\*([^*\n]+)\*(?![*\w])", r"<em>\1</em>", s)
    s = re.sub(r"\x00(\d+)\x00", lambda m: "<code>" + codes[int(m.group(1))] + "</code>", s)
    return s


def md_to_html(md):
    lines = md.splitlines()
    out, i = [], 0
    para = []

    def flush():
        if para:
            out.append("<p>" + _inline(" ".join(para)) + "</p>")
            para.clear()

    while i < len(lines):
        line = lines[i]
        st = line.strip()
        if st.startswith("<!--"):
            while i < len(lines) and "-->" not in lines[i]:
                i += 1
            i += 1
            continue
        fence = re.match(r"^(`{3,}|~{3,})\s*([\w+-]*)", st)
        if fence:
            flush()
            mark, lang = fence.group(1), fence.group(2)
            buf = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith(mark):
                buf.append(lines[i])
                i += 1
            i += 1
            cls = f' class="language-{lang}"' if lang else ""
            out.append(f"<pre><code{cls}>" + html.escape("\n".join(buf)) + "</code></pre>")
            continue
        if not st:
            flush()
            i += 1
            continue
        h = re.match(r"^(#{1,6})\s+(.*)$", st)
        if h:
            flush()
            n = len(h.group(1))
            out.append(f"<h{n}>{_inline(h.group(2).strip('# '))}</h{n}>")
            i += 1
            continue
        if st.startswith(">"):
            flush()
            buf = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                buf.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            out.append("<blockquote>" + md_to_html("\n".join(buf)) + "</blockquote>")
            continue
        if st.startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|?[\s:|-]+\|?\s*$", lines[i + 1]) and "-" in lines[i + 1]:
            flush()
            cells = lambda l: [c.strip() for c in l.strip().strip("|").split("|")]
            head = cells(st)
            i += 2
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(cells(lines[i]))
                i += 1
            t = "<table><thead><tr>" + "".join(f"<th>{_inline(c)}</th>" for c in head) + "</tr></thead><tbody>"
            t += "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>" for r in rows)
            out.append(t + "</tbody></table>")
            continue
        lm = re.match(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$", line)
        if lm:
            flush()
            ordered = lm.group(2)[0].isdigit()
            items = []
            while i < len(lines):
                m2 = re.match(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$", lines[i])
                if m2:
                    items.append(m2.group(3))
                elif lines[i].strip() and lines[i].startswith("  ") and items:
                    items[-1] += " " + lines[i].strip()
                else:
                    break
                i += 1
            tag = "ol" if ordered else "ul"
            out.append(f"<{tag}>" + "".join(f"<li>{_inline(x)}</li>" for x in items) + f"</{tag}>")
            continue
        if re.match(r"^(-{3,}|\*{3,})$", st):
            flush()
            out.append("<hr>")
            i += 1
            continue
        para.append(st)
        i += 1
    flush()
    return "\n".join(out)


def load_article(path):
    with open(path, encoding="utf-8") as f:
        meta, body = split_frontmatter(f.read())
    title = str(meta.get("title") or "").strip()
    if not title:
        m = re.search(r"^#\s+(.+)$", body, re.M)
        title = m.group(1).strip() if m else ""
    # H1 в теле не дублируем: площадки рисуют заголовок сами
    body = re.sub(r"^#\s+.+\n", "", body, count=1) if title else body
    return meta, title, body, md_to_html(body)
