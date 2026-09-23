# -*- coding: utf-8 -*-
"""Тесты status.py, check-all.py, prompt.py, поиска конфига, заголовков скана и gap по заголовкам."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
import importlib.util

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "skills", "statejnik", "scripts")
sys.path.insert(0, SCRIPTS)

import _config  # noqa: E402


def run(args, cwd, env=None):
    e = dict(os.environ)
    e.pop("STATEJNIK_CONFIG", None)
    if env:
        e.update(env)
    return subprocess.run([sys.executable] + args, cwd=cwd, capture_output=True, text=True, timeout=120, env=e)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def load_script(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_").replace(".py", ""),
                                                  os.path.join(SCRIPTS, name))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CFG_READY = "project:\n  name: Тест\n  domain: shop.test\n# example.com в комментарии не в счёт\n"
CFG_TEMPLATE = "project:\n  name: Тест\n  domain: example.com\n"

DRAFT = """---
title: "Как выбрать коврик"
meta_description: "Описание"
status: draft
---

Вводный абзац про коврик.

## Какой размер выбрать

> Коротко: берите на 10 см шире.

Коврик должен быть шире на 10 см. Подробнее [тут](https://shop.test/a), [там](https://shop.test/b) и [ещё](https://shop.test/c).

## Источники

- [Источник](https://example.org/a)
"""


class TestConfigDetection(unittest.TestCase):
    def test_is_configured(self):
        self.assertTrue(_config.is_configured({"project": {"domain": "shop.test"}}))
        self.assertFalse(_config.is_configured({"project": {"domain": "example.com"}}))
        self.assertFalse(_config.is_configured({"project": {"domain": "https://www.example.com/"}}))
        self.assertFalse(_config.is_configured({"project": {"domain": ""}}))
        self.assertFalse(_config.is_configured({}))

    def test_find_config_upwards_from_file(self):
        d = tempfile.mkdtemp()
        write(os.path.join(d, "statejnik.yaml"), CFG_READY)
        write(os.path.join(d, "work", "s", "draft.md"), DRAFT)
        old = os.getcwd()
        os.chdir(tempfile.mkdtemp())
        try:
            env_old = os.environ.pop("STATEJNIK_CONFIG", None)
            self.assertIsNone(_config.find_config())
            self.assertEqual(os.path.realpath(_config.find_config(near=os.path.join(d, "work", "s", "draft.md"))),
                             os.path.realpath(os.path.join(d, "statejnik.yaml")))
        finally:
            os.chdir(old)
            if env_old:
                os.environ["STATEJNIK_CONFIG"] = env_old

    def test_warning_without_config(self):
        d = tempfile.mkdtemp()
        write(os.path.join(d, "a.md"), DRAFT)
        r = run([os.path.join(SCRIPTS, "structure-check.py"), "a.md"], d)
        warn = [l for l in r.stderr.splitlines() if "statejnik.yaml не найден" in l]
        self.assertEqual(len(warn), 1, r.stderr)

    def test_no_warning_when_found_upwards(self):
        d = tempfile.mkdtemp()
        write(os.path.join(d, "statejnik.yaml"), CFG_READY)
        write(os.path.join(d, "work", "s", "draft.md"), DRAFT)
        r = run([os.path.join(SCRIPTS, "structure-check.py"), os.path.join(d, "work", "s", "draft.md")],
                tempfile.mkdtemp())
        self.assertNotIn("не найден", r.stderr)


class TestStatus(unittest.TestCase):
    def test_setup_when_template_domain(self):
        d = tempfile.mkdtemp()
        write(os.path.join(d, "statejnik.yaml"), CFG_TEMPLATE)
        r = run([os.path.join(SCRIPTS, "status.py")], d)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("setup", r.stdout)

    def test_setup_without_config_and_draft(self):
        d = tempfile.mkdtemp()
        write(os.path.join(d, "work", "statejnik.draft.yaml"), CFG_READY)
        write(os.path.join(d, "work", "setup-state.md"), "шаг 3 (ядро запросов) - готово\n")
        r = run([os.path.join(SCRIPTS, "status.py")], d)
        self.assertEqual(r.returncode, 1)
        self.assertIn("setup", r.stdout)
        self.assertIn("шаг 3", r.stdout)

    def test_ready_with_plan(self):
        d = tempfile.mkdtemp()
        write(os.path.join(d, "statejnik.yaml"), CFG_READY)
        write(os.path.join(d, "work", "plan.md"),
              "| # | Тема | Запрос |\n|---|---|---|\n| 1 | как выбрать коврик | x |\n| 2 | как мыть коврик | y |\n")
        r = run([os.path.join(SCRIPTS, "status.py")], d)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("ready", r.stdout)
        self.assertIn("plan.md", r.stdout)
        self.assertIn("2", r.stdout)


class TestCheckAll(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        write(os.path.join(self.d, "statejnik.yaml"), CFG_READY)
        self.w = os.path.join(self.d, "work", "s")
        write(os.path.join(self.w, "draft.md"), DRAFT)
        write(os.path.join(self.w, "sources", "a.md"), "Шапка\n\nСовсем другой текст источника про ткани и уход. " * 30)
        claims = {"slug": "s", "updated": "2026-01-01", "claims": [
            {"id": "C1", "claim": "шире на 10 см", "where": ["## Какой размер выбрать"], "kind": "fact",
             "source_url": "https://example.org/a", "source_file": "sources/a.md",
             "quote": "Совсем другой текст источника", "checked_at": "2026-01-01", "status": "verified"}]}
        write(os.path.join(self.w, "claims.json"), json.dumps(claims, ensure_ascii=False))
        self.ca = os.path.join(SCRIPTS, "check-all.py")

    def test_saves_outputs_and_summary(self):
        r = run([self.ca, "work/s/draft.md", "--round", "1"], self.d)
        out = os.path.join(self.w, "checks", "1")
        for name in ("structure-check", "claims-check", "originality-check", "ai-cadence-check",
                     "read-aloud-check"):
            self.assertTrue(os.path.isfile(os.path.join(out, name + ".txt")), name)
        summary = open(os.path.join(out, "summary.md"), encoding="utf-8").read()
        self.assertIn("sha256", summary)
        self.assertIn("блокир", r.stdout.lower())
        self.assertIn(r.returncode, (0, 1))

    def test_missing_claims_blocks(self):
        os.remove(os.path.join(self.w, "claims.json"))
        r = run([self.ca, "work/s/draft.md", "--round", "2"], self.d)
        self.assertEqual(r.returncode, 1, r.stdout)

    def test_long_sentence_blocks(self):
        long = " ".join(["слово"] * 80) + "."
        write(os.path.join(self.w, "draft.md"), DRAFT.replace("Вводный абзац про коврик.", long))
        r = run([self.ca, "work/s/draft.md", "--round", "1"], self.d)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("read-aloud", r.stdout)

    def test_no_file(self):
        r = run([self.ca, "work/s/none.md"], self.d)
        self.assertEqual(r.returncode, 2)

    def test_out_dir(self):
        run([self.ca, "work/s/draft.md", "--out-dir", "checks-x"], self.d)
        self.assertTrue(os.path.isfile(os.path.join(self.d, "checks-x", "summary.md")))


class TestPrompt(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        write(os.path.join(self.d, "statejnik.yaml"),
              CFG_READY + "voice:\n  tone: просто\npublish:\n  targets:\n    f:\n      type: files\n")
        self.p = os.path.join(SCRIPTS, "prompt.py")

    def test_review_round1_no_repeat_block(self):
        r = run([self.p, "review", "--slug", "s", "--round", "1"], self.d)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("{{", r.stdout)
        self.assertIn("work/s", r.stdout)
        self.assertNotIn("review-0.md", r.stdout)
        self.assertIn("tone: просто", r.stdout)
        self.assertNotIn("targets", r.stdout)        # publish-раздел конфига в промпт не идёт
        self.assertIn("check-all", r.stderr)          # нет checks/1/summary.md - подсказка

    def test_review_round2_has_previous(self):
        r = run([self.p, "review", "--slug", "s", "--round", "2"], self.d)
        self.assertIn("review-1.md", r.stdout)
        self.assertIn("fixes-1.md", r.stdout)
        self.assertNotIn("{{", r.stdout)

    def test_final_verify_isolated(self):
        r = run([self.p, "final-verify", "--slug", "s", "--round", "1"], self.d)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("editor.md", r.stdout)
        self.assertNotIn("{{", r.stdout)

    def test_strict_and_bad_round(self):
        self.assertEqual(run([self.p, "review", "--slug", "s", "--strict"], self.d).returncode, 1)
        self.assertEqual(run([self.p, "review", "--slug", "s", "--round", "4"], self.d).returncode, 2)
        self.assertEqual(run([self.p, "review", "--slug", "../x"], self.d).returncode, 2)

    def test_templates_uniform_placeholders(self):
        import glob
        import re
        allowed = {"{{WORKDIR}}", "{{SLUG}}", "{{ROUND}}", "{{PREV_ROUND}}", "{{SKILL_DIR}}", "{{CONFIG}}",
                   "{{CARDS}}", "{{SERP}}", "{{#REPEAT}}", "{{/REPEAT}}"}
        for f in glob.glob(os.path.join(ROOT, "skills", "statejnik", "templates", "prompts", "*.md")):
            body = open(f, encoding="utf-8").read().split("==== ПРОМПТ ====")[1]
            self.assertTrue(set(re.findall(r"\{\{[^}]+\}\}", body)) <= allowed, f)
            self.assertFalse(re.search(r"<slug>|<N>", body), f)


class TestClaimsWhereBasis(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.w = os.path.join(self.d, "work", "s")
        write(os.path.join(self.w, "sources", "a.md"), "Шапка\n\nКоврик должен быть шире на 10 см.\n")
        write(os.path.join(self.w, "draft.md"), DRAFT)
        self.cc = os.path.join(SCRIPTS, "claims-check.py")

    def claims(self, items):
        base = {"id": "C1", "claim": "шире на 10 см", "where": ["## Какой размер выбрать"], "kind": "fact",
                "source_url": "https://example.org/a", "source_file": "sources/a.md",
                "quote": "шире на 10 см", "checked_at": "2026-01-01", "status": "verified"}
        write(os.path.join(self.w, "claims.json"),
              json.dumps({"slug": "s", "claims": [dict(base, **i) for i in items]}, ensure_ascii=False))

    def test_where_formats_accepted(self):
        self.claims([{}, {"id": "C2", "where": ["Какой размер выбрать", "meta_description", "вводная часть"]}])
        r = run([self.cc, "work/s", "--text", "work/s/draft.md"], self.d)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertNotIn("where", r.stdout)

    def test_where_unknown_heading_warns(self):
        self.claims([{"where": ["## Раздел, которого нет"]}])
        r = run([self.cc, "work/s", "--text", "work/s/draft.md"], self.d)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("where", r.stdout)

    def test_inference_needs_basis_not_quote(self):
        inf = {"id": "C2", "kind": "promise", "claim": "берите шире", "basis": ["C1"]}
        for k in ("quote", "source_url", "source_file"):
            inf[k] = None
        self.claims([{}, inf])
        data = json.load(open(os.path.join(self.w, "claims.json"), encoding="utf-8"))
        for k in ("quote", "source_url", "source_file"):
            data["claims"][1].pop(k)
        write(os.path.join(self.w, "claims.json"), json.dumps(data, ensure_ascii=False))
        r = run([self.cc, "work/s"], self.d)
        self.assertEqual(r.returncode, 0, r.stdout)
        data["claims"][1].pop("basis")
        write(os.path.join(self.w, "claims.json"), json.dumps(data, ensure_ascii=False))
        r = run([self.cc, "work/s"], self.d)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("basis", r.stdout)
        data["claims"][1]["basis"] = ["C9"]
        write(os.path.join(self.w, "claims.json"), json.dumps(data, ensure_ascii=False))
        r = run([self.cc, "work/s"], self.d)
        self.assertEqual(r.returncode, 1)


class TestSiteHeadings(unittest.TestCase):
    def setUp(self):
        self.site = load_script("site.py")

    def test_generic_title_uses_h1(self):
        pages = [{"url": f"https://x.test/blog/stat-{i}-pro-kovriki", "kind": "article", "title": "Статьи",
                  "h1": f"Как выбрать коврик {i}", "og_title": ""} for i in range(5)]
        pages.append({"url": "https://x.test/blog/og", "kind": "article", "title": "Статьи", "h1": "",
                      "og_title": "Заголовок из og"})
        self.site.assign_headings(pages)
        self.assertEqual(pages[0]["heading"], "Как выбрать коврик 0")
        self.assertEqual(pages[-1]["heading"], "Заголовок из og")

    def test_listing_urls(self):
        secs = ["/blog"]
        for u in ("https://x.test/blog", "https://x.test/blog/tag/kovriki", "https://x.test/blog/page/2",
                  "https://x.test/akcii/skidka-na-kovriki-letom", "https://x.test/blog/sale/leto-kovriki-skidki"):
            self.assertTrue(self.site.is_listing(u, secs), u)
        self.assertFalse(self.site.is_listing("https://x.test/blog/kak-vybrat-kovrik-dlya-doma", secs))

    def test_parent_listings_dropped(self):
        urls = ["https://x.test/enc/cats"] + [f"https://x.test/enc/cats/poroda-{i}" for i in range(4)]
        keep, dropped = self.site.drop_parent_listings(urls)
        self.assertEqual(dropped, ["https://x.test/enc/cats"])

    def test_reclassify_same_heading_and_commerce(self):
        pages = [{"url": f"https://x.test/blog/list-{i}", "kind": "article", "heading": "Акции и скидки", "h1": "x"}
                 for i in range(3)]
        pages.append({"url": "https://x.test/blog/kupit", "kind": "article",
                      "heading": "Купить коврик по низкой цене", "h1": "Купить коврик"})
        pages.append({"url": "https://x.test/blog/ok", "kind": "article", "heading": "Как стирать коврик", "h1": "y"})
        dropped = self.site.reclassify_pages(pages)
        self.assertEqual(len(dropped), 4)
        self.assertEqual(pages[-1]["kind"], "article")


class TestGapByHeadings(unittest.TestCase):
    def test_heading_covers_topic_despite_url(self):
        d = tempfile.mkdtemp()
        site = {"url": "https://x.test", "article_sections": [{"path": "/blog", "source": "auto"}],
                "urls": ["https://x.test/blog/12345", "https://x.test/blog/67890"],
                "article_urls": ["https://x.test/blog/12345", "https://x.test/blog/67890"],
                "pages": [{"url": "https://x.test/blog/12345", "kind": "article", "title": "Статьи",
                           "heading": "Какой лоток выбрать для кошки", "h1": "Какой лоток выбрать для кошки"},
                          {"url": "https://x.test/blog/67890", "kind": "article", "title": "Статьи",
                           "heading": "Как приучить щенка к поводку", "h1": "Как приучить щенка к поводку"}]}

        def k(ph):
            return {"phrase": ph, "count": None, "sources": ["s"], "cluster": ph, "article_fit": 1.0,
                    "article_type": "гид"}
        kws = [k("какой лоток лучше для кошки"), k("как выбрать лежанку для кошки"), k("кошка лоток выбор")]
        json.dump(site, open(os.path.join(d, "s.json"), "w", encoding="utf-8"), ensure_ascii=False)
        json.dump(kws, open(os.path.join(d, "k.json"), "w", encoding="utf-8"), ensure_ascii=False)
        r = run([os.path.join(SCRIPTS, "site.py"), "gap", "s.json", "k.json", "--out", "g.json", "--plan", "p.md"], d)
        self.assertEqual(r.returncode, 0, r.stderr)
        g = json.load(open(os.path.join(d, "g.json"), encoding="utf-8"))
        rows = {x["cluster"]: x for x in g["clusters"]}
        lot = rows.get("какой лоток лучше для кошки") or rows.get("кошка лоток выбор")
        self.assertTrue(lot["covered_by"] or lot.get("maybe_covered"), lot)
        self.assertFalse(rows["как выбрать лежанку для кошки"]["covered_by"])


class TestPublishGateMessage(unittest.TestCase):
    def test_no_bypass_hint(self):
        d = tempfile.mkdtemp()
        write(os.path.join(d, "statejnik.yaml"),
              CFG_READY + "publish:\n  targets:\n    files:\n      type: files\n      dir: out\n")
        write(os.path.join(d, "work", "s", "final.md"), DRAFT)
        r = run([os.path.join(SCRIPTS, "publish.py"), "send", "files", "work/s/final.md", "--status", "publish"], d)
        self.assertEqual(r.returncode, 4, r.stderr)
        self.assertNotIn("force", r.stderr)
        self.assertIn("06-review", r.stderr)
        self.assertIn("--status draft", r.stderr)
        h = run([os.path.join(SCRIPTS, "publish.py"), "send", "--help"], d)
        self.assertNotIn("force-without-acceptance", h.stdout)


if __name__ == "__main__":
    unittest.main()
