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
        rows = {x["cluster"]: x for x in json.load(open(os.path.join(d, "g.json"), encoding="utf-8"))["clusters"]}
        self.assertTrue(rows["как выбрать диван"]["covered_by"])
        self.assertIsNone(rows["как почистить диван"]["covered_by"])
        self.assertIn("как почистить диван", open(os.path.join(d, "p.md"), encoding="utf-8").read())


TEMPLATE = os.path.join(ROOT, "skills", "statejnik", "templates", "statejnik.example.yaml")


def load_script(name):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name.replace("-", "_").replace(".py", ""),
                                                  os.path.join(SCRIPTS, name))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class TestConfig(unittest.TestCase):
    def test_flow_mappings_and_nesting(self):
        w = []
        d = _mini_yaml("tools:\n  originality: {max_cosine: 0.5, ngram_n: 5}  # c\n  structure: {min_internal_links: 3}\n"
                       "publish:\n  targets:\n    files:\n      type: files\n      dir: out\n"
                       "list:\n  - name: a\n    v: [1, {b: \"x, y\"}]\n  - plain\n"
                       "url: {u: https://x.ru/a?b=1}\n", w)
        self.assertEqual(d["tools"]["originality"], {"max_cosine": 0.5, "ngram_n": 5})
        self.assertEqual(d["tools"]["structure"]["min_internal_links"], 3)
        self.assertEqual(d["publish"]["targets"]["files"]["dir"], "out")
        self.assertEqual(d["list"], [{"name": "a", "v": [1, {"b": "x, y"}]}, "plain"])
        self.assertEqual(d["url"]["u"], "https://x.ru/a?b=1")
        self.assertEqual(w, [])

    def test_template_reads_without_pyyaml(self):
        import _config
        raw = open(TEMPLATE, encoding="utf-8").read()
        w = []
        d = _mini_yaml(raw, w)
        self.assertEqual(w, [], w)
        self.assertEqual(_config.schema_warnings(d), [])
        for key in ("tools.originality.max_cosine", "tools.read_aloud.max_sentence_words",
                    "tools.cadence.max_per_1k", "tools.structure.min_internal_links"):
            self.assertIsNotNone(_config.get(d, key), key)
        self.assertEqual(_config.get(d, "tools.structure.min_internal_links"), 3)
        self.assertIn("files", _config.get(d, "publish.targets"))

    def test_unknown_keys_and_bad_lines_warn(self):
        import _config
        w = []
        d = _mini_yaml("tools:\n  strucure:\n    min_internal_links: 3\njunk line\n", w)
        self.assertTrue(any("junk" in x for x in w))
        self.assertTrue(any("strucure" in x for x in _config.schema_warnings(d)))


class TestRu(unittest.TestCase):
    def test_term_matches_word_bounds_and_stems(self):
        from _ru import term_matches
        self.assertEqual(term_matches("руб", "грубой ткани"), [])
        self.assertTrue(term_matches("руб", "100 руб."))
        self.assertTrue(term_matches("аскона", "матрасы Асконы"))
        self.assertEqual(term_matches("аскона", "маскона"), [])
        self.assertTrue(term_matches("много мебели", "в «Много мебели»"))


class TestKeywordsFilter(unittest.TestCase):
    def test_junk_and_brands(self):
        kw = load_script("keywords.py")
        ex = ["аскона", "леруа мерлен", kw.brand_from_domain("https://www.mebelion.ru/")]
        self.assertEqual(ex[-1], "mebelion")
        self.assertTrue(kw.junk_reason("ткань для дивана кроссворд 6 букв", ex))
        self.assertTrue(kw.junk_reason("диван купить минск", ex))
        self.assertTrue(kw.junk_reason("диван аскона отзывы", ex))
        self.assertTrue(kw.junk_reason("диван леруа мерлен", ex))
        self.assertTrue(kw.junk_reason("диван еврокнижка фото", ex))
        self.assertTrue(kw.junk_reason("скачать чертеж дивана", ex))
        self.assertIsNone(kw.junk_reason("диван еврокнижка своими руками", ex))
        self.assertIsNone(kw.junk_reason("диван аккордеон как раскладывается видео", ex))
        self.assertIsNone(kw.junk_reason("диван аккордеон", ex))
        self.assertIsNone(kw.junk_reason("купить минск", ex, region="Беларусь"))


class TestSiteLogic(unittest.TestCase):
    def setUp(self):
        self.site = load_script("site.py")

    def test_regional_prefix_dedupe(self):
        base = "https://www.shop.ru"
        paths = ["/blog/a-b-c", "/category/x", "/product/y", "/about", "/"]
        urls = [base + p for p in paths] + [base + "/spb" + p for p in paths] + [base + "/kazan" + p for p in paths]
        urls += ["https://spb.shop.ru/blog/a-b-c"]
        out, info = self.site.dedupe_regional(urls, "www.shop.ru")
        self.assertEqual(sorted(info["prefixes"]), ["kazan", "spb"])
        self.assertEqual(len(out), len(paths))

    def test_regional_sitemaps(self):
        locs = [f"https://x.ru/sitemap.{c}.xml" for c in ("msk", "spb", "kazan", "tula", "tver", "sochi")]
        locs.append("https://x.ru/sitemap-blog.xml")
        order, skipped = self.site.order_child_sitemaps(locs)
        self.assertEqual(order[0], "https://x.ru/sitemap-blog.xml")
        self.assertIn("https://x.ru/sitemap.msk.xml", order)
        self.assertEqual(len(skipped), 5)

    def test_sections_and_commerce(self):
        urls = ["https://x.ru/idei-i-trendy/kak-vybrat-divan-dlya-sna", "https://x.ru/idei-i-trendy/category/polezno",
                "https://x.ru/product/divan-biani-velvet-terra", "https://x.ru/category/divany",
                "https://x.ru/poleznye-stati/kak-pochistit-divan", "https://x.ru/informacija/uhod-za-kozhej"]
        secs = list(self.site.detect_sections(urls, ["/idei-i-trendy"]))
        self.assertIn("/idei-i-trendy", secs)
        self.assertIn("/poleznye-stati", secs)
        self.assertIn("/informacija", secs)
        arts = [u for u in urls if self.site.is_article_url(u, secs)]
        self.assertEqual(len(arts), 3)
        self.assertTrue(self.site.is_commerce_url("https://x.ru/product/divan-biani-velvet-terra"))
        self.assertTrue(self.site.is_commerce_url("https://x.ru/divany?color=red"))

    def test_blocked_reasons(self):
        b = self.site.blocked_reason
        self.assertTrue(b(307, "", "https://a.ru", "https://a.ru"))
        self.assertTrue(b(200, "<html><title>x</title>ok</html>", "https://a.ru", "https://other.com/"))
        self.assertTrue(b(200, "<html><script src=smartcaptcha></script></html>", "https://a.ru", "https://a.ru"))
        self.assertTrue(b(200, "<html></html>", "https://a.ru", "https://a.ru"))
        ok = "<html><title>Сайт</title><body>" + "<p>текст статьи</p>" * 50 + "</body></html>"
        self.assertIsNone(b(200, ok, "https://a.ru", "https://www.a.ru/"))

    def test_scan_blocked_exit_code(self):
        # внутренний адрес без --allow-private - сайт «не читается», код 3
        r = run([os.path.join(SCRIPTS, "site.py"), "scan", "http://127.0.0.1:9/", "--out", "s.json"], tempfile.mkdtemp())
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIn("не читается", r.stderr)

    def test_scan_help_mentions_max_urls(self):
        r = run([os.path.join(SCRIPTS, "site.py"), "scan", "--help"], tempfile.mkdtemp())
        self.assertIn("--max-urls", r.stdout)
        self.assertIn("--content-path", r.stdout)


class TestGapQuality(unittest.TestCase):
    def test_commerce_pages_and_queries(self):
        d = tempfile.mkdtemp()
        site = {"url": "https://x.ru", "article_sections": [{"path": "/idei-i-trendy", "source": "content_path"}],
                "urls": ["https://x.ru/product/divan-prodavilsya-biani",
                         "https://x.ru/category/ppu-ili-pruzhinnyj-blok",
                         "https://x.ru/idei-i-trendy/kakoj-divan-lucse-vybrat-uglovoj-ili-pramoj"],
                "pages": []}
        def k(ph):
            return {"phrase": ph, "count": None, "sources": ["yandex_suggest"], "cluster": ph,
                    "article_fit": 1.0, "article_type": "разбор"}
        kws = [k("почему продавился диван"), k("ппу или пружинный блок"), k("какой диван лучше угловой или прямой"),
               k("диван еврокнижка купить"), k("диван для сна минск"), k("диван в москве")]
        json.dump(site, open(os.path.join(d, "s.json"), "w"))
        json.dump(kws, open(os.path.join(d, "k.json"), "w"), ensure_ascii=False)
        r = run([os.path.join(SCRIPTS, "site.py"), "gap", "s.json", "k.json", "--out", "g.json", "--plan", "p.md"], d)
        self.assertEqual(r.returncode, 0, r.stderr)
        g = json.load(open(os.path.join(d, "g.json"), encoding="utf-8"))
        rows = {x["cluster"]: x for x in g["clusters"]}
        self.assertIsNone(rows["почему продавился диван"]["covered_by"])      # товар - не покрытие
        self.assertIsNone(rows["ппу или пружинный блок"]["covered_by"])      # категория - не покрытие
        self.assertTrue(rows["какой диван лучше угловой или прямой"]["covered_by"])  # транслит lucse/pramoj
        comm = [c["cluster"] for c in g["commercial"]]
        for q in ("диван еврокнижка купить", "диван для сна минск", "диван в москве"):
            self.assertIn(q, comm)
            self.assertNotIn(q, rows)
        plan = open(os.path.join(d, "p.md"), encoding="utf-8").read()
        self.assertIn("Ближайшая статья", plan)
        self.assertIn("Коммерческие запросы", plan)


class TestPublishGate(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        write(os.path.join(self.dir, "statejnik.yaml"),
              "project:\n  domain: example.com\npublish:\n  targets:\n    files:\n      type: files\n      dir: out\n")
        self.art = os.path.join("work", "test-divan", "final.md")
        write(os.path.join(self.dir, self.art), ARTICLE.replace("slug: test-divan\n", "")
              .replace("tags:", "hero_promise:\n  what_you_get:\n    - пункт\ntags:"))
        self.pub = os.path.join(SCRIPTS, "publish.py")

    def test_files_alias_check_and_draft(self):
        r = run([self.pub, "check", "files"], self.dir)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.isdir(os.path.join(self.dir, "out")))
        r = run([self.pub, "send", "files", self.art, "--status", "draft"], self.dir)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("accepted.md", r.stderr)       # предупреждение о приёмке
        md = open(os.path.join(self.dir, "out", "test-divan.md"), encoding="utf-8").read()
        self.assertTrue(md.startswith("---\n"))
        self.assertIn("status: draft", md)
        self.assertIn("hero_promise", md)
        h = open(os.path.join(self.dir, "out", "test-divan.html"), encoding="utf-8").read()
        self.assertIn('name="statejnik:status" content="draft"', h)
        self.assertIn("noindex", h)
        self.assertIn("hero-promise", h)

    def test_publish_requires_acceptance(self):
        r = run([self.pub, "send", "files", self.art, "--status", "publish"], self.dir)
        self.assertEqual(r.returncode, 4)
        self.assertIn("accepted.md", r.stderr)
        import hashlib
        sha = hashlib.sha256(open(os.path.join(self.dir, self.art), "rb").read()).hexdigest()
        write(os.path.join(self.dir, "work", "test-divan", "accepted.md"), f"# Приёмка\n- версия: final.md, sha256 {sha}\n")
        r = run([self.pub, "send", "files", self.art, "--status", "publish"], self.dir)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("status: publish", open(os.path.join(self.dir, "out", "test-divan.md"), encoding="utf-8").read())
        with open(os.path.join(self.dir, self.art), "a", encoding="utf-8") as f:
            f.write("\nправка после приёмки\n")
        r = run([self.pub, "send", "files", self.art, "--status", "publish"], self.dir)
        self.assertEqual(r.returncode, 4)
        r = run([self.pub, "send", "files", self.art, "--status", "publish", "--force-without-acceptance"], self.dir)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("БЕЗ ПРИЁМКИ", r.stderr)
        led = json.load(open(os.path.join(self.dir, "work", "published.json"), encoding="utf-8"))
        self.assertTrue(led["test-divan"]["files"]["forced_without_acceptance"])

    def test_check_unwritable_dir(self):
        write(os.path.join(self.dir, "statejnik.yaml"),
              "publish:\n  targets:\n    files:\n      type: manual\n      dir: /proc/statejnik-nope\n")
        r = run([self.pub, "check", "files"], self.dir)
        self.assertNotEqual(r.returncode, 0)


class TestClaimsCheck(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.work = os.path.join(self.dir, "work", "s")
        write(os.path.join(self.work, "sources", "a.md"), "Шапка\n\nFree plan includes up to 50\nrequests per day. Цена 990 ₽.\n")
        self.cc = os.path.join(SCRIPTS, "claims-check.py")

    def claims(self, items):
        base = {"id": "C1", "claim": "лимит 50 запросов", "where": ["body"], "kind": "limit",
                "source_url": "https://example.com/pricing", "source_file": "sources/a.md",
                "quote": "Free plan includes up to 50", "checked_at": "2026-01-01", "status": "verified"}
        data = {"slug": "s", "updated": "2026-01-01", "claims": [dict(base, **it) for it in items]}
        write(os.path.join(self.work, "claims.json"), json.dumps(data, ensure_ascii=False))

    def test_ok_and_whitespace(self):
        self.claims([{}, {"id": "C2", "quote": "up to 50 requests per day"}])
        r = run([self.cc, "work/s"], self.dir)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("пробелов", r.stdout)

    def test_missing_quote_file_status(self):
        self.claims([{"quote": "Free plan includes up to 60"}, {"id": "C2", "source_file": "sources/none.md"},
                     {"id": "C3", "status": "maybe"}, {"id": "C4", "status": "unverified"},
                     {"id": "C5", "quote": "free plan includes up to 50"}])
        r = run([self.cc, "work/s"], self.dir)
        self.assertEqual(r.returncode, 1)
        for cid in ("C1", "C2", "C3", "C4"):
            self.assertIn(f"{cid}:", r.stdout)
        self.assertIn("похожая строка", r.stdout)

    def test_text_numbers(self):
        self.claims([{"claim": "лимит 50 запросов, цена 990 ₽"}])
        write(os.path.join(self.work, "final.md"), "---\ntitle: t\n---\n## Лимит\n\nДо 50 запросов, 990 ₽ "
              "и ещё 35% пользователей. Шаг 2.\n\n| a | 777 |\n```\n12345\n```\n")
        r = run([self.cc, "work/s", "--text", "work/s/final.md"], self.dir)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("35", r.stdout)
        self.assertNotIn("12345", r.stdout)
        r = run([self.cc, "work/s", "--text", "work/s/final.md", "--strict-text"], self.dir)
        self.assertEqual(r.returncode, 1)

    def test_no_claims(self):
        r = run([self.cc, "work/nope"], self.dir)
        self.assertEqual(r.returncode, 2)


class TestFetchSource(unittest.TestCase):
    def test_html_to_text(self):
        fs = load_script("fetch-source.py")
        doc = ("<html><head><title>T</title><script>var x=1</script></head><body><nav>меню</nav>"
               "<article><h1>Заголовок</h1><p>Первый <b>абзац</b>.</p><ul><li>раз</li><li>два</li></ul>"
               "<table><tr><th>a</th><th>b</th></tr><tr><td>1</td><td>2</td></tr></table>"
               "<div style='display:none'><p>скрыто</p></div><p>" + "Длинный текст. " * 40 + "</p></article>"
               "<footer>подвал</footer></body></html>")
        t = fs.html_to_text(doc)
        self.assertIn("# Заголовок", t)
        self.assertIn("Первый абзац.", t)
        self.assertIn("- раз", t)
        self.assertIn("| a | b |", t)
        self.assertIn("| 1 | 2 |", t)
        for bad in ("var x", "меню", "подвал", "скрыто"):
            self.assertNotIn(bad, t)

    def test_private_address_blocked(self):
        r = run([os.path.join(SCRIPTS, "fetch-source.py"), "http://127.0.0.1:9/x", "--out", "s/x.md"], tempfile.mkdtemp())
        self.assertEqual(r.returncode, 1)
        self.assertIn("заблокирован", r.stderr)


class TestChecks(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        write(os.path.join(self.dir, "statejnik.yaml"),
              "project:\n  domain: example.com\ncta:\n  url: https://example.com/offer\n"
              "editorial:\n  banned_words: [руб, аскона]\n")
        self.sc = os.path.join(SCRIPTS, "structure-check.py")
        self.ra = os.path.join(SCRIPTS, "read-aloud-check.py")

    def doc(self, links=3, title=True, h1=False, extra=""):
        fm = '---\ntitle: "Заголовок"\n---\n' if title else ""
        body = ("# Заголовок\n\n" if h1 else "") + "## Раздел\n\nГрубой ткани нет. " + extra + "\n\n"
        body += " ".join(f"[л{i}](/blog/a{i})" for i in range(links)) + " [оффер](https://example.com/offer)\n"
        write(os.path.join(self.dir, "d.md"), fm + body)
        return run([self.sc, "d.md"], self.dir)

    def test_title_or_h1(self):
        self.assertEqual(self.doc(title=True, h1=False).returncode, 0)
        self.assertEqual(self.doc(title=False, h1=True).returncode, 0)
        self.assertEqual(self.doc(title=True, h1=True).returncode, 0)
        r = self.doc(title=False, h1=False)
        self.assertEqual(r.returncode, 1)

    def test_min_internal_default_3_and_override(self):
        r = self.doc(links=2)
        self.assertEqual(r.returncode, 1)
        self.assertIn("минимум 3", r.stdout)
        r = run([self.sc, "d.md", "--min-internal", "2"], self.dir)
        self.assertEqual(r.returncode, 0, r.stdout)
        write(os.path.join(self.dir, "statejnik.yaml"), "cta:\n  url: https://example.com/offer\n"
              "tools:\n  structure: {min_internal_links: 1}\n")
        self.assertEqual(run([self.sc, "d.md"], self.dir).returncode, 0)

    def test_banned_words_word_bounds(self):
        self.assertEqual(self.doc().returncode, 0)      # «грубой» не содержит слова «руб»
        r = self.doc(extra="Стоит 100 руб. Как у Асконы.")
        self.assertEqual(r.returncode, 1)
        self.assertIn("«руб»", r.stdout)
        self.assertIn("аскона", r.stdout)

    def test_read_aloud_digits_not_blocking(self):
        write(os.path.join(self.dir, "a.md"), "## Сравнение\n\nСтойкость 30 000 циклов при 30 °C, 4-6 слоёв, 2026 год.\n\n"
              "| Ткань | Циклы |\n|---|---|\n| велюр | 73 000 |\n")
        r = run([self.ra, "a.md"], self.dir)
        self.assertEqual(r.returncode, 0, r.stdout)
        long = " ".join(["слово"] * 40) + "."
        write(os.path.join(self.dir, "b.md"), long + "\n")
        self.assertEqual(run([self.ra, "b.md"], self.dir).returncode, 1)


if __name__ == "__main__":
    unittest.main()
