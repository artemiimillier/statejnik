#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Общие помощники скриптов Статейника: .env, HTTP без внешних зависимостей.

Секреты живут только в `.env` рабочей папки (или в окружении). Скрипты
никогда не печатают их значения - только имена переменных.
"""
from __future__ import annotations

import ipaddress
import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request

UA = "Mozilla/5.0 (compatible; statejnik/1.0)"


_FILE_ENV = {}


def env(name):
    """Значение переменной: окружение важнее файла .env."""
    return os.getenv(name) or _FILE_ENV.get(name)


def load_env(path=None):
    """Подхватить KEY=VALUE из .env. Уже заданные переменные окружения важнее файла."""
    path = path or os.environ.get("STATEJNIK_ENV") or os.path.join(os.getcwd(), ".env")
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key.startswith("export "):
                key = key[7:].strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            _FILE_ENV.setdefault(key, value)


def need_env(*names):
    """Вернуть значения переменных или остановиться с понятной ошибкой (без значений)."""
    missing = [n for n in names if not env(n)]
    if missing:
        raise SystemExit(
            "не заданы переменные: " + ", ".join(missing)
            + ". Впишите их в файл .env рабочей папки (см. templates/env.example)."
        )
    return [env(n) for n in names]


def set_env_line(name, value, path=None):
    """Записать/заменить строку NAME=value в .env, не печатая значение. Права 0600."""
    path = path or os.path.join(os.getcwd(), ".env")
    lines = []
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            lines = [l for l in f.read().splitlines() if not l.startswith(name + "=")]
    lines.append(f"{name}={value}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def is_private_host(host):
    """True, если имя резолвится во внутренний/служебный адрес (защита при обходе чужих сайтов)."""
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError):
        return False
    for info in infos:
        ip = ipaddress.ip_address(str(info[4][0]).split("%")[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return True
    return False


class _GuardedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        host = urllib.parse.urlsplit(newurl).hostname or ""
        if is_private_host(host):
            raise urllib.error.URLError(f"редирект на внутренний адрес заблокирован: {host}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def request(method, url, headers=None, body=None, timeout=30, allow_private=True):
    """HTTP-запрос. Возвращает (status, headers, bytes). Ошибки HTTP не бросает - отдаёт статус."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise ValueError(f"поддерживаются только http/https: {url}")
    if not allow_private and is_private_host(parts.hostname or ""):
        raise ValueError(f"внутренний адрес заблокирован: {parts.hostname} (флаг --allow-private снимает защиту)")
    data = None
    hdrs = {"User-Agent": UA, "Accept": "*/*"}
    hdrs.update(headers or {})
    if body is not None:
        if isinstance(body, (dict, list)):
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/json; charset=utf-8")
        elif isinstance(body, str):
            data = body.encode("utf-8")
        else:
            data = body
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    opener = urllib.request.build_opener() if allow_private else urllib.request.build_opener(_GuardedRedirect())
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read() or b""


BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")


def fetch(url, headers=None, timeout=30, allow_private=False, browser_ua=True, max_bytes=20_000_000):
    """GET с защитой от внутренних адресов. Возвращает (status, headers, bytes, final_url).

    final_url - адрес после всех редиректов. Ошибки HTTP не бросает - отдаёт статус
    (для бесконечного редиректа это 3xx). Сетевые ошибки бросает (URLError/OSError).
    """
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise ValueError(f"поддерживаются только http/https: {url}")
    if not allow_private and is_private_host(parts.hostname or ""):
        raise ValueError(f"внутренний адрес заблокирован: {parts.hostname} (флаг --allow-private снимает защиту)")
    hdrs = {"User-Agent": BROWSER_UA if browser_ua else UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.7"}
    hdrs.update(headers or {})
    req = urllib.request.Request(url, headers=hdrs, method="GET")
    opener = urllib.request.build_opener() if allow_private else urllib.request.build_opener(_GuardedRedirect())
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read(max_bytes), resp.geturl()
    except urllib.error.HTTPError as e:
        try:
            body = e.read() or b""
        except Exception:
            body = b""
        return e.code, dict(e.headers or {}), body, e.geturl() or url


def request_json(method, url, headers=None, body=None, timeout=30, ok=(200, 201, 202, 204)):
    """Запрос с JSON-ответом. При статусе вне `ok` - RuntimeError с началом тела ответа."""
    status, _, raw = request(method, url, headers=headers, body=body, timeout=timeout)
    text = raw.decode("utf-8", "replace")
    if status not in ok:
        raise RuntimeError(f"HTTP {status} от {urllib.parse.urlsplit(url).netloc}: {text[:400]}")
    if not text.strip():
        return {}
    try:
        return json.loads(text)
    except ValueError:
        raise RuntimeError(f"ответ не JSON (HTTP {status}): {text[:200]}")


def decode_body(raw, headers=None):
    """Декодировать HTML/текст: charset из заголовка, затем utf-8, затем cp1251."""
    charset = None
    ctype = (headers or {}).get("Content-Type") or (headers or {}).get("content-type") or ""
    if "charset=" in ctype:
        charset = ctype.split("charset=")[-1].split(";")[0].strip()
    for enc in filter(None, (charset, "utf-8", "cp1251")):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace")
