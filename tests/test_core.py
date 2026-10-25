"""Unit tests for the GTK-free core (run: python3 -m unittest discover -s tests)."""

import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from siteforge import blocks, components, formatter  # noqa: E402
from siteforge.htmldoc import HtmlDocument, strip_editor_attributes  # noqa: E402
from siteforge.project import (Project, deobfuscate, obfuscate, stripe_runtime,  # noqa: E402
                               validate_stripe)

ICON = os.path.join(ROOT, "data", "icons", "hicolor", "256x256", "apps", "siteforge.png")


def node_check(test, source):
    """Syntax-check JavaScript with node when it is installed."""
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        return
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(source)
    try:
        result = subprocess.run([node, "--check", fh.name], capture_output=True, text=True)
        test.assertEqual(result.returncode, 0, result.stderr)
    finally:
        os.unlink(fh.name)


class HtmlDocTests(unittest.TestCase):
    SRC = ('<header data-sf-id="a"><h1>T</h1></header>\n'
           '<p data-sf-id="b">one</p>\n<p data-sf-id="c">two</p>\n')

    def test_spans(self):
        doc = HtmlDocument(self.SRC)
        node = doc.find("b")
        self.assertEqual(node.outer(self.SRC), '<p data-sf-id="b">one</p>')
        self.assertEqual(node.inner(self.SRC), "one")

    def test_insert_positions(self):
        doc = HtmlDocument(self.SRC)
        self.assertIn('<p data-sf-id="b">one</p>\n<i>x</i>', doc.insert("<i>x</i>", "b", "after"))
        self.assertIn('<i>x</i>\n<p data-sf-id="b">', doc.insert("<i>x</i>", "b", "before"))
        inside = doc.insert("<i>x</i>", "a", "inside")
        self.assertIn("<i>x</i>", HtmlDocument(inside).find("a").inner(inside))
        self.assertTrue(doc.insert("<i>x</i>").endswith("<i>x</i>\n"))

    def test_remove_and_move(self):
        doc = HtmlDocument(self.SRC)
        self.assertNotIn("one", doc.remove("b"))
        moved = doc.move("c", -1)
        self.assertLess(moved.index("two"), moved.index("one"))

    def test_unclosed_and_void(self):
        src = '<div data-sf-id="x"><p>open<input data-sf-id="y"></div><p data-sf-id="z">z</p>'
        doc = HtmlDocument(src)
        self.assertEqual(doc.find("y").outer(src), '<input data-sf-id="y">')
        self.assertEqual(doc.find("z").inner(src), "z")

    def test_strip(self):
        self.assertEqual(strip_editor_attributes('<p data-sf-id="1" data-sf-stripe>x</p>'),
                         "<p data-sf-stripe>x</p>")


class ComponentTests(unittest.TestCase):
    def test_round_trip_every_component(self):
        for comp in components.COMPONENTS:
            props = comp.defaults()
            props["id"] = "thing"
            html = comp.render(props, "sf-9")
            node = HtmlDocument(html).find("sf-9")
            self.assertIs(components.detect(node), comp, comp.key)
            back = comp.props_from_node(node, html)
            for prop in comp.props:
                self.assertEqual(back[prop.name], props[prop.name], f"{comp.key}.{prop.name}")

    def test_attribute_escaping(self):
        comp = components.BY_KEY["button"]
        html = comp.render(dict(comp.defaults(), id='a"b', onclick='alert("x")'), "sf-1")
        node = HtmlDocument(html).find("sf-1")
        self.assertEqual(node.attrs["onclick"], 'alert("x")')
        self.assertEqual(node.attrs["id"], 'a"b')

    def test_style_size_keeps_other_declarations(self):
        p = Project()
        sf = p.add_component("textbox")
        p.html = p.html.replace('aria-label="Message"', 'aria-label="Message" style="color: red"')
        p.update_element(sf, {"width": 240, "height": 32})
        node = p.document().find(sf)
        self.assertEqual(node.attrs["style"], "color: red; width: 240px; height: 32px")
        self.assertEqual(p.element(sf)[2]["width"], 240)


class BlockTests(unittest.TestCase):
    def compile(self, scripts, variables=()):
        return blocks.compile_blocks({"scripts": scripts, "variables": list(variables)})

    def test_every_block_compiles(self):
        scripts = []
        for definition in blocks.BLOCK_DEFS.values():
            block = blocks.new_block(definition.type)
            if definition.shape == blocks.HAT:
                block["next"] = blocks.new_block("var_log")
                scripts.append(block)
            elif definition.shape in (blocks.REPORTER, blocks.BOOLEAN):
                log = blocks.new_block("var_log")
                log["inputs"]["value"] = block
                scripts.append({"type": "event_page_load", "next": log})
            else:
                scripts.append({"type": "event_page_load", "next": block})
        js = self.compile(scripts, [{"name": "v", "initial": 1}])
        self.assertIn("sf.ready", js)
        node_check(self, js)

    def test_detached_stacks_are_ignored(self):
        self.assertEqual(self.compile([blocks.new_block("looks_show")]), "")

    def test_injection_safe(self):
        hat = {"type": "event_click", "inputs": {"el": '"); alert(1); ("'},
               "next": {"type": "var_set", "inputs": {"var": "a'b\"c", "value": "</script>"}}}
        js = self.compile([hat])
        self.assertIn(json.dumps('"); alert(1); ("'), js)
        node_check(self, js)

    def test_semantics(self):
        hat = {"type": "event_page_load", "next": {
            "type": "logic_repeat", "inputs": {"times": "3"},
            "substacks": {"body": {"type": "var_change", "inputs": {"var": "n", "value": 2}}}}}
        js = self.compile([hat], [{"name": "n", "initial": "5"}])
        self.assertIn('vars: {"n": 5}', js)
        self.assertIn("await sf.frame();", js)  # loops yield every iteration (Scratch-like)

    def test_sanitize_drops_unknown(self):
        data = blocks.sanitize({"scripts": [{"type": "nope"}, {"type": "event_page_load",
                                                               "next": {"type": "bogus"}}],
                                "variables": [{"name": ""}, {"name": "ok"}, "junk"]})
        self.assertEqual(len(data["scripts"]), 1)
        self.assertIsNone(data["scripts"][0]["next"])
        self.assertEqual(data["variables"], [{"name": "ok", "initial": 0}])


class FormatterTests(unittest.TestCase):
    def test_html(self):
        out = formatter.format_html("<header><h1>a</h1><nav><a>x</a></nav></header><p>t</p>")
        self.assertEqual(out, "<header>\n  <h1>a</h1>\n  <nav><a>x</a></nav>\n</header>\n<p>t</p>\n")

    def test_css(self):
        self.assertEqual(formatter.format_css("a{color:red;margin:0}"),
                         "a {\n  color: red;\n  margin: 0;\n}\n")

    def test_js_keeps_strings_and_regex(self):
        src = 'function f(a){if(a){x="{;}";}else{return /a{2}/.test(a);}}'
        out = formatter.format_js(src)
        self.assertIn('x="{;}";', out)
        self.assertIn("/a{2}/.test(a);", out)
        self.assertIn("} else {", out)
        node_check(self, out)

    def test_formatters_are_stable(self):
        for fmt, src in ((formatter.format_html, Project().html),
                         (formatter.format_css, Project().css)):
            once = fmt(src)
            self.assertEqual(fmt(once), once)


class ProjectTests(unittest.TestCase):
    def test_add_component_ids(self):
        p = Project()
        a = p.add_component("button")
        b = p.add_component("button", a, "after")
        self.assertNotEqual(a, b)
        ids = [props["id"] for _s, c, props, _d in p.elements() if c.key == "button"]
        self.assertEqual(ids, ["button1", "button2"])

    def test_stripe_css_added_once(self):
        p = Project()
        p.add_component("stripe")
        p.add_component("stripe")
        self.assertEqual(p.css.count("sf:stripe"), 1)

    def test_json_round_trip(self):
        p = Project()
        p.add_component("canvas")
        p.stripe["price_id"] = "price_123"
        p.set_blocks({"scripts": [{"type": "event_page_load", "next": None, "x": 5, "y": 6}]})
        q = Project.from_json(json.loads(json.dumps(p.to_json())))
        self.assertEqual((q.html, q.css, q.blocks, q.stripe), (p.html, p.css, p.blocks, p.stripe))

    def test_stripe_never_plaintext_in_file(self):
        p = Project()
        p.stripe["publishable_key"] = "pk_test_SECRETLOOKING"
        self.assertNotIn("pk_test_SECRETLOOKING", json.dumps(p.to_json()))
        self.assertEqual(deobfuscate(obfuscate(p.stripe)), p.stripe)

    def test_stripe_validation(self):
        self.assertTrue(validate_stripe({"publishable_key": "sk_live_abc"}))
        self.assertFalse(validate_stripe({"publishable_key": "pk_test_abc", "price_id": "price_1"}))
        self.assertTrue(validate_stripe({"success_url": "javascript:alert(1)"}))

    def test_stripe_runtime_escapes_script(self):
        js = stripe_runtime({"success_url": "</script><script>alert(1)</script>"})
        self.assertNotIn("</script>", js)
        node_check(self, js)

    def test_export_single_and_split(self):
        p = Project()
        sf = p.add_component("image")
        p.update_element(sf, {"src": p.add_asset(ICON)})
        p.js = "console.log('hi');"
        with tempfile.TemporaryDirectory() as out:
            p.export(out)
            with open(os.path.join(out, "index.html"), encoding="utf-8") as fh:
                page = fh.read()
            self.assertNotIn("data-sf-id", page)
            self.assertIn("data:image/png;base64,", page)
            self.assertIn("console.log('hi');", page)
        with tempfile.TemporaryDirectory() as out:
            written = {os.path.relpath(f, out) for f in p.export(out, split=True)}
            self.assertEqual(written, {"index.html", "style.css", "script.js",
                                       os.path.join("assets", "siteforge.png")})
            with open(os.path.join(out, "index.html"), encoding="utf-8") as fh:
                page = fh.read()
            self.assertIn('src="assets/siteforge.png"', page)
            self.assertIn('<script src="script.js"></script>', page)

    def test_assets_dedupe_and_reject_non_images(self):
        p = Project()
        self.assertEqual(p.add_asset(ICON), p.add_asset(ICON))
        with self.assertRaises(ValueError):
            p.add_asset(os.path.join(ROOT, "README.md"))

    def test_html_rebuilt_from_element_tree(self):
        p = Project()
        p.add_component("footer")
        data = p.to_json()
        del data["html"]
        q = Project.from_json(data)
        self.assertEqual([c.key for _s, c, _p, _d in q.elements()],
                         [c.key for _s, c, _p, _d in p.elements()])

    def test_rejects_other_files(self):
        with self.assertRaises(ValueError):
            Project.from_json({"format": "something-else"})


class ExampleTests(unittest.TestCase):
    def test_examples_load_and_export(self):
        files = sorted(glob.glob(os.path.join(ROOT, "examples", "*.siteforge")))
        self.assertGreaterEqual(len(files), 3)
        for path in files:
            project = Project.load(path)
            page = project.generate(for_export=True)["index.html"]
            self.assertIn("<!DOCTYPE html>", page)
            for chunk in page.split("<script>")[1:]:
                node_check(self, chunk.split("</script>")[0])


if __name__ == "__main__":
    unittest.main()
