#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Публикация статьи в площадки из statejnik.yaml (раздел publish.targets).

    python3 publish.py list                                   # какие площадки настроены
    python3 publish.py check <target>                         # проверить доступ, ничего не публикуя
    python3 publish.py send <target> work/<slug>/final.md --status draft
    python3 publish.py send <target> work/<slug>/final.md --status publish
    python3 publish.py ping https://site.ru/blog/slug         # IndexNow (Яндекс, Bing)

Типы площадок (publish.targets.<имя>.type):
  manual     - кладёт готовые .md и .html в папку, публикуете руками (подходит для любой CMS).
  wordpress  - REST API WordPress + пароль приложения. Черновик/публикация, повтор без дублей по slug.
  blogger    - Blogger (Blogspot) API v3, OAuth refresh-токен. Черновик/публикация.
  dzen_rss   - дописывает статью в RSS-ленту в формате Дзена; ленту раздаёт ваш сайт.
  webhook    - POST JSON на ваш адрес (Tilda/n8n/Make/свой бэкенд).
  mcp        - публикует сам агент через MCP-инструмент; скрипт только готовит полезную нагрузку.

Повтор без дублей: результат каждой отправки пишется в work/published.json
(slug → площадка → id). Второй вызов обновляет запись, а не создаёт новую.
Секреты - только в .env; скрипт печатает имена переменных, не значения.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import email.utils
import hashlib
import html
import json
import os
import re
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _net import load_env, need_env, request, request_json  # noqa: E402
from _config import find_config, load_config, get  # noqa: E402
from _article import load_article  # noqa: E402
from _ru import translit  # noqa: E402

LEDGER = os.path.join("work", "published.json")


def _ledger():
    if os.path.isfile(LEDGER):
        with open(LEDGER, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_ledger(data):
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    with open(LEDGER, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def _slugify(title):
    s = re.sub(r"[^a-z0-9]+", "-", translit(title)).strip("-")
    return s[:80] or "article"


def _env_name(tcfg, key, default):
    return tcfg.get(key) or default


# ── manual ───────────────────────────────────────────────────────────────────

def manual_send(name, tcfg, art, status, cfg):
    folder = tcfg.get("dir") or os.path.join("work", "ready")
    os.makedirs(folder, exist_ok=True)
    base = os.path.join(folder, art["slug"])
    with open(base + ".md", "w", encoding="utf-8") as f:
        f.write(f"# {art['title']}\n\n{art['body']}")
    doc = (f"<!doctype html><meta charset=\"utf-8\"><title>{html.escape(art['title'])}</title>"
           f"<meta name=\"description\" content=\"{html.escape(art['description'])}\">"
           f"<h1>{html.escape(art['title'])}</h1>\n{art['html']}\n")
    with open(base + ".html", "w", encoding="utf-8") as f:
        f.write(doc)
    return {"id": art["slug"], "url": None, "status": "ready", "files": [base + ".md", base + ".html"]}


# ── WordPress ────────────────────────────────────────────────────────────────

def _wp(tcfg):
    url = (tcfg.get("url") or "").rstrip("/")
    if not url:
        raise SystemExit("для wordpress нужен publish.targets.<имя>.url (адрес сайта)")
    user, pw = need_env(_env_name(tcfg, "user_env", "WP_USER"), _env_name(tcfg, "password_env", "WP_APP_PASSWORD"))
    token = base64.b64encode(f"{user}:{pw}".encode()).decode()
    return url + "/wp-json/wp/v2", {"Authorization": "Basic " + token}


def _wp_terms(api, hdr, kind, names):
    ids = []
    for n in names or []:
        found = request_json("GET", f"{api}/{kind}?search={urllib.parse.quote(str(n))}&per_page=20", headers=hdr)
        hit = next((t["id"] for t in found if t.get("name", "").lower() == str(n).lower()), None)
        if hit is None:
            hit = request_json("POST", f"{api}/{kind}", headers=hdr, body={"name": str(n)})["id"]
        ids.append(hit)
    return ids


def wordpress_check(name, tcfg, cfg):
    api, hdr = _wp(tcfg)
    me = request_json("GET", api + "/users/me?context=edit", headers=hdr)
    caps = me.get("capabilities") or {}
    ok = caps.get("publish_posts") or caps.get("edit_posts")
    return f"вход как «{me.get('name')}», права на записи: {'да' if ok else 'НЕТ'}"


def wordpress_send(name, tcfg, art, status, cfg):
    api, hdr = _wp(tcfg)
    post = {"title": art["title"], "content": art["html"], "slug": art["slug"],
            "status": "publish" if status == "publish" else "draft", "excerpt": art["description"]}
    cats = art["meta"].get("categories") or tcfg.get("categories")
    tags = art["meta"].get("tags")
    if cats:
        post["categories"] = _wp_terms(api, hdr, "categories", cats if isinstance(cats, list) else [cats])
    if tags:
        post["tags"] = _wp_terms(api, hdr, "tags", tags if isinstance(tags, list) else [tags])
    pid = art["known_id"]
    if not pid:
        found = request_json("GET", f"{api}/posts?slug={urllib.parse.quote(art['slug'])}&status=any&context=edit", headers=hdr)
        pid = found[0]["id"] if found else None
    res = request_json("POST", f"{api}/posts/{pid}" if pid else f"{api}/posts", headers=hdr, body=post)
    return {"id": res["id"], "url": res.get("link"), "status": res.get("status")}


# ── Blogger (Blogspot) ───────────────────────────────────────────────────────

def _blogger(tcfg):
    blog = tcfg.get("blog_id")
    if not blog:
        raise SystemExit("для blogger нужен publish.targets.<имя>.blog_id (число из адресной строки редактора Blogger)")
    cid, secret, refresh = need_env("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET",
                                    _env_name(tcfg, "refresh_token_env", "BLOGGER_REFRESH_TOKEN"))
    status, _, raw = request("POST", "https://oauth2.googleapis.com/token", body=urllib.parse.urlencode({
        "client_id": cid, "client_secret": secret, "refresh_token": refresh, "grant_type": "refresh_token"}),
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    if status != 200:
        raise SystemExit(f"Google не выдал доступ (HTTP {status}). Обычно протух refresh-токен: "
                         "получите новый (см. references/publishers.md, раздел Blogger).")
    token = json.loads(raw)["access_token"]
    return f"https://www.googleapis.com/blogger/v3/blogs/{blog}", {"Authorization": "Bearer " + token}


def blogger_check(name, tcfg, cfg):
    api, hdr = _blogger(tcfg)
    b = request_json("GET", api, headers=hdr)
    return f"блог «{b.get('name')}» ({b.get('url')}), записей: {b.get('posts', {}).get('totalItems')}"


def blogger_send(name, tcfg, art, status, cfg):
    api, hdr = _blogger(tcfg)
    body = {"kind": "blogger#post", "title": art["title"], "content": art["html"]}
    labels = art["meta"].get("tags") or tcfg.get("labels")
    if labels:
        body["labels"] = labels if isinstance(labels, list) else [labels]
    pid = art["known_id"]
    if pid:
        res = request_json("PUT", f"{api}/posts/{pid}", headers=hdr, body=body)
    else:
        res = request_json("POST", f"{api}/posts/?isDraft=true", headers=hdr, body=body)
        pid = res["id"]
    if status == "publish" and res.get("status") != "LIVE":
        res = request_json("POST", f"{api}/posts/{pid}/publish", headers=hdr)
    return {"id": pid, "url": res.get("url"), "status": res.get("status", "DRAFT")}


# ── Дзен через RSS ───────────────────────────────────────────────────────────

_RSS_HEAD = ('<?xml version="1.0" encoding="UTF-8"?>\n'
             '<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/" '
             'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:media="http://search.yahoo.com/mrss/" '
             'xmlns:atom="http://www.w3.org/2005/Atom" xmlns:georss="http://www.georss.org/georss">\n<channel>\n')


def _dzen_item(art, tcfg, status, link):
    cats = (["native-draft"] if status != "publish" else []) + ["format-article",
            "index" if tcfg.get("index", True) else "noindex", tcfg.get("comments", "comment-all")]
    image = art["meta"].get("image") or art["meta"].get("cover") or tcfg.get("default_image")
    if not image:
        raise SystemExit("Дзену нужна обложка шириной от 700 px: поле image во frontmatter или default_image у площадки")
    guid = hashlib.sha1(art["slug"].encode()).hexdigest()
    body = art["html"].replace("]]>", "]]]]><![CDATA[>")
    return ("<item>\n"
            f"<title>{html.escape(art['title'])}</title>\n<link>{html.escape(link)}</link>\n"
            f"<guid>{guid}</guid>\n<pubDate>{email.utils.format_datetime(dt.datetime.now(dt.timezone.utc))}</pubDate>\n"
            '<media:rating scheme="urn:simple">nonadult</media:rating>\n'
            + "".join(f"<category>{c}</category>\n" for c in cats)
            + f'<enclosure url="{html.escape(image)}" type="image/{"png" if image.lower().endswith(".png") else "jpeg"}"/>\n'
            f"<description><![CDATA[{art['description']}]]></description>\n"
            f"<content:encoded><![CDATA[{body}]]></content:encoded>\n</item>\n"), guid


def dzen_check(name, tcfg, cfg):
    path = tcfg.get("feed_path")
    if not path:
        raise SystemExit("для dzen_rss нужен feed_path - куда писать файл ленты (папка, которую раздаёт сайт)")
    url = tcfg.get("feed_url")
    msg = f"лента пишется в {path}"
    if url:
        st, _, _ = request("GET", url, timeout=20)
        msg += f"; {url} отвечает HTTP {st}"
    return msg


def dzen_send(name, tcfg, art, status, cfg):
    path = tcfg.get("feed_path")
    if not path:
        raise SystemExit("для dzen_rss нужен feed_path")
    site = (tcfg.get("site_url") or "https://" + str(get(cfg, "project.domain", ""))).rstrip("/")
    link = art["meta"].get("url") or f"{site}{get(cfg, 'project.content_path', '/blog')}/{art['slug']}"
    item, guid = _dzen_item(art, tcfg, status, link)
    items = []
    if os.path.isfile(path):
        old = open(path, encoding="utf-8").read()
        items = [m for m in re.findall(r"(?s)<item>.*?</item>\n?", old) if f"<guid>{guid}</guid>" not in m]
    items = [item] + items[: int(tcfg.get("max_items", 50)) - 1]
    head = (_RSS_HEAD + f"<title>{html.escape(str(tcfg.get('title') or get(cfg, 'project.name', site)))}</title>\n"
            f"<link>{html.escape(site)}</link>\n<language>ru</language>\n")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(head + "".join(items) + "</channel>\n</rss>\n")
    return {"id": guid, "url": link, "status": "в ленте (" + ("черновик Дзена" if status != "publish" else "публикация") + ")"}


# ── webhook ──────────────────────────────────────────────────────────────────

def webhook_send(name, tcfg, art, status, cfg):
    url = tcfg.get("url")
    if not url:
        raise SystemExit("для webhook нужен url")
    hdr = {}
    if tcfg.get("token_env"):
        hdr["Authorization"] = "Bearer " + need_env(tcfg["token_env"])[0]
    res = request_json("POST", url, headers=hdr, body=_payload(art, status))
    return {"id": res.get("id") or art["slug"], "url": res.get("url"), "status": res.get("status", "sent")}


def webhook_check(name, tcfg, cfg):
    return f"адрес {tcfg.get('url')}; проверка - только реальной отправкой черновика"


# ── mcp ──────────────────────────────────────────────────────────────────────

def mcp_send(name, tcfg, art, status, cfg):
    out = os.path.join("work", art["slug"], f"mcp-{name}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    payload = {"server": tcfg.get("server"), "tool": tcfg.get("tool"), "arguments": _payload(art, status),
               "field_map": tcfg.get("field_map") or {}}
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print(f"MCP: агент должен вызвать инструмент «{tcfg.get('tool')}» сервера «{tcfg.get('server')}» "
          f"с аргументами из {out} (переименовав поля по field_map), затем записать id: "
          f"python3 publish.py record {name} {art['slug']} <id> [url]")
    return None


def mcp_check(name, tcfg, cfg):
    return f"сервер «{tcfg.get('server')}», инструмент «{tcfg.get('tool')}» - проверяет агент списком своих MCP-инструментов"


def _payload(art, status):
    return {"title": art["title"], "slug": art["slug"], "status": status, "html": art["html"],
            "markdown": art["body"], "excerpt": art["description"], "tags": art["meta"].get("tags") or [],
            "meta_title": art["meta"].get("meta_title"), "meta_description": art["meta"].get("meta_description")}


ADAPTERS = {
    "manual": (lambda n, t, c: f"папка {t.get('dir') or 'work/ready'}", manual_send),
    "wordpress": (wordpress_check, wordpress_send),
    "blogger": (blogger_check, blogger_send),
    "dzen_rss": (dzen_check, dzen_send),
    "webhook": (webhook_check, webhook_send),
    "mcp": (mcp_check, mcp_send),
}


def _target(cfg, name):
    targets = get(cfg, "publish.targets", {}) or {}
    if name not in targets:
        raise SystemExit(f"площадка «{name}» не описана в statejnik.yaml → publish.targets. Есть: {', '.join(targets) or 'ничего'}")
    t = targets[name] or {}
    if t.get("type") not in ADAPTERS:
        raise SystemExit(f"у площадки «{name}» неизвестный type «{t.get('type')}». Доступно: {', '.join(ADAPTERS)}")
    return t


def main():
    p = argparse.ArgumentParser(description="Публикация статьи")
    p.add_argument("--config", default=None)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    c = sub.add_parser("check")
    c.add_argument("target")
    s = sub.add_parser("send")
    s.add_argument("target")
    s.add_argument("article")
    s.add_argument("--status", choices=("draft", "publish"), default="draft")
    s.add_argument("--slug")
    r = sub.add_parser("record")
    r.add_argument("target")
    r.add_argument("slug")
    r.add_argument("id")
    r.add_argument("url", nargs="?")
    pg = sub.add_parser("ping")
    pg.add_argument("urls", nargs="+")
    a = p.parse_args()

    load_env()
    cfg = load_config(find_config(a.config))

    if a.cmd == "list":
        targets = get(cfg, "publish.targets", {}) or {}
        if not targets:
            print("площадок нет: заполните publish.targets в statejnik.yaml")
        for n, t in targets.items():
            print(f"- {n}: {(t or {}).get('type')}")
        return
    if a.cmd == "check":
        t = _target(cfg, a.target)
        print(f"{a.target}: {ADAPTERS[t['type']][0](a.target, t, cfg)}")
        return
    if a.cmd == "record":
        led = _ledger()
        led.setdefault(a.slug, {})[a.target] = {"id": a.id, "url": a.url, "status": "recorded",
                                               "at": dt.datetime.now().isoformat(timespec="seconds")}
        _save_ledger(led)
        print("записано")
        return
    if a.cmd == "ping":
        key = need_env("INDEXNOW_KEY")[0]
        host = urllib.parse.urlsplit(a.urls[0]).hostname
        st, _, raw = request("POST", "https://yandex.com/indexnow", body={
            "host": host, "key": key, "urlList": a.urls}, headers={"Content-Type": "application/json; charset=utf-8"})
        print(f"IndexNow: HTTP {st}" + ("" if st in (200, 202) else " " + raw.decode("utf-8", "replace")[:200]))
        return

    t = _target(cfg, a.target)
    meta, title, body, html_body = load_article(a.article)
    if not title:
        raise SystemExit("у статьи нет заголовка: поле title во frontmatter или строка «# Заголовок»")
    slug = a.slug or meta.get("slug") or _slugify(title)
    if a.status == "publish" and str(meta.get("status", "")).lower() in ("blocked", "rejected"):
        raise SystemExit("статья помечена как непрошедшая проверку (status во frontmatter) - публикация остановлена")
    led = _ledger()
    known = (led.get(slug, {}).get(a.target) or {}).get("id")
    art = {"meta": meta, "title": title, "body": body, "html": html_body, "slug": slug, "known_id": known,
           "description": str(meta.get("meta_description") or meta.get("excerpt") or "")}
    res = ADAPTERS[t["type"]][1](a.target, t, art, a.status, cfg)
    if res is None:
        return
    res["at"] = dt.datetime.now().isoformat(timespec="seconds")
    led.setdefault(slug, {})[a.target] = res
    _save_ledger(led)
    print(f"{a.target}: {res['status']}" + (f" → {res['url']}" if res.get("url") else "") + f" (id {res['id']})")


if __name__ == "__main__":
    main()
