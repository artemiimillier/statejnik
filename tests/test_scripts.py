# -*- coding: utf-8 -*-
"""Офлайн-тесты скриптов Статейника: python3 -m unittest discover tests"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "skills", "statejnik", "scripts")
sys.path.insert(0, SCRIPTS)

from _config import _mini_yaml  # noqa: E402
from _article import md_to_html, split_frontmatter  # noqa: E402
from _ru import stem, intent, article_type  # noqa: E402


def run(args, cwd):
    return subprocess.run([sys.executable] + args, cwd=cwd, capture_output=True, text=True, timeout=60)


ARTICLE = """---
slug: test-divan
title: "Как выбрать диван: тест"
meta_description: "Описание"
tags: [диваны]
---

## Раздел

> Коротко: **ответ**.

- пункт 1
- пункт 2

| a | b |
|---|---|
| 1 | 2 |
"""


class TestParsing(unittest.TestCase):
    def test_yaml_subset(self):
        d = _mini_yaml('a:\n  b: "x" # c\n  l: [1, 2]\np: |\n  one\n  two: x\nq: true\n')
        self.assertEqual(d["a"]["b"], "x")
        self.assertEqual(d["a"]["l"], [1, 2])
        self.assertEqual(d["p"], "one\ntwo: x")
        self.assertIs(d["q"], True)

    def test_markdown(self):
        meta, body = split_frontmatter(ARTICLE)
        self.assertEqual(meta["slug"], "test-divan")
        h = md_to_html(body)
        self.assertIn("<h2>Раздел</h2>", h)
        self.assertIn("<strong>ответ</strong>", h)
        self.assertIn("<table>", h)
        self.assertIn("<li>пункт 2</li>", h)

    def test_russian(self):
        self.assertEqual(stem("диваны"), stem("диван"))
        self.assertEqual(intent("купить диван недорого"), "commercial")
        self.assertEqual(intent("как выбрать диван"), "informational")
        self.assertEqual(article_type("диван или кровать что лучше"), "сравнение")


class TestPublish(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        with open(os.path.join(self.dir, "statejnik.yaml"), "w", encoding="utf-8") as f:
            f.write("project:\n  domain: example.com\n  content_path: /blog\npublish:\n  targets:\n"
                    "    files:\n      type: manual\n    dzen:\n      type: dzen_rss\n"
                    "      feed_path: public/dzen.xml\n      default_image: https://example.com/c.jpg\n")
        os.makedirs(os.path.join(self.dir, "work"))
        with open(os.path.join(self.dir, "work", "final.md"), "w", encoding="utf-8") as f:
            f.write(ARTICLE)
        self.pub = os.path.join(SCRIPTS, "publish.py")

    def test_manual(self):
        r = run([self.pub, "send", "files", "work/final.md"], self.dir)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.isfile(os.path.join(self.dir, "work", "ready", "test-divan.html")))
        led = json.load(open(os.path.join(self.dir, "work", "published.json"), encoding="utf-8"))
        self.assertIn("files", led["test-divan"])

    def test_dzen_rss_no_duplicates(self):
        for _ in range(2):
            r = run([self.pub, "send", "dzen", "work/final.md"], self.dir)
            self.assertEqual(r.returncode, 0, r.stderr)
        root = ET.parse(os.path.join(self.dir, "public", "dzen.xml")).getroot()
        items = root.findall("./channel/item")
        self.assertEqual(len(items), 1)
        cats = [c.text for c in items[0].findall("category")]
        self.assertIn("native-draft", cats)
        self.assertIn("format-article", cats)

    def test_unknown_target(self):
        r = run([self.pub, "send", "nope", "work/final.md"], self.dir)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("не описана", r.stderr)

    def test_blocked_article(self):
        with open(os.path.join(self.dir, "work", "bad.md"), "w", encoding="utf-8") as f:
            f.write(ARTICLE.replace("tags:", "status: blocked\ntags:"))
        r = run([self.pub, "send", "files", "work/bad.md", "--status", "publish"], self.dir)
        self.assertNotEqual(r.returncode, 0)


class TestGap(unittest.TestCase):
    def test_gap_marks_covered(self):
        d = tempfile.mkdtemp()
        site = {"url": "https://example.com", "urls": ["https://example.com/blog/kak-vybrat-divan/"], "pages": []}
        kws = [{"phrase": "как выбрать диван", "count": 1000, "sources": ["wordstat"], "cluster": "как выбрать диван",
                "article_fit": 1.0, "article_type": "гид по выбору"},
               {"phrase": "как почистить диван", "count": 500, "sources": ["wordstat"], "cluster": "как почистить диван",
                "article_fit": 1.0, "article_type": "инструкция"}]
        json.dump(site, open(os.path.join(d, "s.json"), "w"))
        json.dump(kws, open(os.path.join(d, "k.json"), "w"), ensure_ascii=False)
        r = run([os.path.join(SCRIPTS, "site.py"), "gap", "s.json", "k.json", "--out", "g.json", "--plan", "p.md"], d)
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = {x["cluster"]: x for x in json.load(open(os.path.join(d, "g.json"), encoding="utf-8"))}
        self.assertTrue(rows["как выбрать диван"]["covered_by"])
        self.assertIsNone(rows["как почистить диван"]["covered_by"])
        self.assertIn("как почистить диван", open(os.path.join(d, "p.md"), encoding="utf-8").read())


if __name__ == "__main__":
    unittest.main()
