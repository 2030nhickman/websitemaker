"""The SiteForge project model, ``.siteforge`` file format and HTML generator.

This module has no GTK dependency.  The window owns one :class:`Project` and
subscribes to change notifications with :meth:`Project.connect`.
"""

from __future__ import annotations

import base64
import hashlib
import html as html_lib
import json
import mimetypes
import os
import re

from . import blocks as blocks_mod
from . import components
from .htmldoc import HtmlDocument, strip_editor_attributes

FORMAT_NAME = "siteforge"
FORMAT_VERSION = 1
STRIPE_JS_URL = "https://js.stripe.com/v3/"

_ASSET_RE = re.compile(r"""(["'(]\s*)assets/([\w.-]+)""")

STRIPE_FIELDS = ("publishable_key", "price_id", "success_url", "cancel_url",
                 "customer_email", "mode", "payment_link")
STRIPE_DEFAULTS = {
    "publishable_key": "",
    "price_id": "",
    "success_url": "https://example.com/?checkout=success",
    "cancel_url": "https://example.com/?checkout=cancel",
    "customer_email": "",
    "mode": "payment",
    "payment_link": "",
}

DEFAULT_HTML = """<header data-sf-id="sf-1" id="header1">
  <h1>Welcome to SiteForge</h1>
</header>
<p data-sf-id="sf-2" id="text1">Drag components from the sidebar onto the preview to build your page.</p>
"""

DEFAULT_CSS = """body {
  font-family: system-ui, sans-serif;
  margin: 0 auto;
  max-width: 960px;
  padding: 24px;
  line-height: 1.5;
}

header, footer {
  padding: 16px 0;
}
"""


# --------------------------------------------------------------------------
# Stripe settings obfuscation
# --------------------------------------------------------------------------
# The Stripe settings are stored in their own section of the project file
# and never inside the page HTML.  They are XOR-ed with a SHA-256 keystream
# so they are not greppable as plain text.  This is obfuscation, not real
# encryption (the key ships with the app); publishable keys are public by
# design and secret keys are refused outright.

_OBF_SCHEME = "sf-obf-1"
_OBF_KEY = hashlib.sha256(b"siteforge project stripe settings v1").digest()


def _keystream(length: int, nonce: bytes):
    out = bytearray()
    counter = 0
    while len(out) < length:
        out += hashlib.sha256(_OBF_KEY + nonce + counter.to_bytes(8, "big")).digest()
        counter += 1
    return bytes(out[:length])


def obfuscate(data: dict) -> dict:
    raw = json.dumps(data, sort_keys=True).encode()
    nonce = os.urandom(12)
    cipher = bytes(a ^ b for a, b in zip(raw, _keystream(len(raw), nonce)))
    return {"scheme": _OBF_SCHEME, "nonce": base64.b64encode(nonce).decode(),
            "data": base64.b64encode(cipher).decode()}


def deobfuscate(blob) -> dict:
    if not isinstance(blob, dict):
        return {}
    if blob.get("scheme") != _OBF_SCHEME:
        # Hand-written project files may contain a plain mapping.
        return {k: blob[k] for k in STRIPE_FIELDS if isinstance(blob.get(k), str)}
    try:
        nonce = base64.b64decode(blob["nonce"])
        cipher = base64.b64decode(blob["data"])
        raw = bytes(a ^ b for a, b in zip(cipher, _keystream(len(cipher), nonce)))
        data = json.loads(raw.decode())
    except (KeyError, ValueError, UnicodeDecodeError):
        return {}
    return {k: data[k] for k in STRIPE_FIELDS if isinstance(data.get(k), str)}


def validate_stripe(settings: dict) -> list:
    """Return a list of human readable problems (empty when fine)."""
    problems = []
    key = settings.get("publishable_key", "").strip()
    if key.startswith(("sk_", "rk_")):
        problems.append("That is a secret key. Only publishable keys (pk_test_… / pk_live_…) "
                        "may be used in a website.")
    elif key and not re.fullmatch(r"pk_(test|live)_[A-Za-z0-9]+", key):
        problems.append("Publishable keys look like pk_test_… or pk_live_….")
    price = settings.get("price_id", "").strip()
    if price and not re.fullmatch(r"price_[A-Za-z0-9]+", price):
        problems.append("Price IDs look like price_….")
    for field in ("success_url", "cancel_url", "payment_link"):
        url = settings.get(field, "").strip()
        if url and not re.match(r"https?://", url):
            problems.append(f"The {field.replace('_', ' ')} must start with https:// or http://.")
    email = settings.get("customer_email", "").strip()
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        problems.append("The customer email does not look like an email address.")
    if settings.get("mode", "payment") not in ("payment", "subscription"):
        problems.append("Mode must be payment or subscription.")
    return problems


def stripe_runtime(settings: dict) -> str:
    cfg = {
        "publishableKey": settings.get("publishable_key", ""),
        "priceId": settings.get("price_id", ""),
        "successUrl": settings.get("success_url", ""),
        "cancelUrl": settings.get("cancel_url", ""),
        "customerEmail": settings.get("customer_email", ""),
        "mode": settings.get("mode", "payment") or "payment",
        "paymentLink": settings.get("payment_link", ""),
    }
    # json.dumps + escaping "</" keeps the config from closing the <script>.
    config = json.dumps(cfg, indent=2).replace("</", "<\\/")
    return f"""// SiteForge Stripe Checkout (injected at export time)
window.SITEFORGE_STRIPE = {config};
(function () {{
  'use strict';
  const cfg = window.SITEFORGE_STRIPE;
  let stripe = null;

  function fail(message) {{
    console.error('[SiteForge Stripe]', message);
    alert(message);
  }}

  window.siteforgeStripeCheckout = async function (priceId) {{
    if (cfg.paymentLink && !cfg.publishableKey) {{
      window.location.href = cfg.paymentLink;
      return;
    }}
    if (typeof Stripe !== 'function') return fail('Stripe.js could not be loaded (are you offline?).');
    if (!cfg.publishableKey) return fail('Set a publishable key in Stripe Settings.');
    const price = priceId || cfg.priceId;
    if (!price) return fail('Set a Price ID in Stripe Settings.');
    stripe = stripe || Stripe(cfg.publishableKey);
    const options = {{
      lineItems: [{{ price: price, quantity: 1 }}],
      mode: cfg.mode,
      successUrl: cfg.successUrl,
      cancelUrl: cfg.cancelUrl,
    }};
    if (cfg.customerEmail) options.customerEmail = cfg.customerEmail;
    const result = await stripe.redirectToCheckout(options);
    if (result && result.error) fail(result.error.message);
  }};

  window.siteforgeCheckoutStatus = function () {{
    const status = new URLSearchParams(window.location.search).get('checkout');
    if (status) return status;
    const here = window.location.href.split('#')[0];
    if (cfg.successUrl && here === cfg.successUrl) return 'success';
    if (cfg.cancelUrl && here === cfg.cancelUrl) return 'cancel';
    return null;
  }};

  function wire() {{
    document.querySelectorAll('[data-sf-stripe]').forEach(function (box) {{
      const form = box.querySelector('.sf-stripe-form');
      if (form) {{
        if (form.elements.price) form.elements.price.value = cfg.priceId;
        if (form.elements.email) form.elements.email.value = cfg.customerEmail;
      }}
      const button = box.querySelector('.sf-stripe-button');
      if (!button) return;
      button.addEventListener('click', async function () {{
        button.disabled = true;
        try {{
          await window.siteforgeStripeCheckout(form && form.elements.price ? form.elements.price.value : '');
        }} finally {{
          button.disabled = false;
        }}
      }});
    }});
  }}
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', wire);
  else wire();
}})();
"""


class Project:
    def __init__(self):
        self.path: str | None = None
        self.title = "My Website"
        self.lang = "en"
        self.html = DEFAULT_HTML
        self.css = DEFAULT_CSS
        self.js = ""
        self.blocks = {"scripts": [], "variables": []}
        self.stripe = dict(STRIPE_DEFAULTS)
        # Images added to the project: name -> {"mime": str, "data": base64 str}.
        # The HTML refers to them as "assets/<name>".
        self.assets: dict = {}
        self.dirty = False
        self._listeners = []
        self._doc_cache: HtmlDocument | None = None

    # -- change notification -------------------------------------------
    def connect(self, callback):
        """``callback(project, part)``; part is html/css/js/blocks/stripe/meta/all."""
        self._listeners.append(callback)

    def _changed(self, part: str, origin=None, dirty=True):
        if dirty:
            self.dirty = True
        for callback in list(self._listeners):
            callback(self, part, origin)

    def mark_saved(self):
        self.dirty = False
        self._changed("meta", dirty=False)

    # -- setters --------------------------------------------------------
    def set_html(self, text: str, origin=None):
        if text != self.html:
            self.html = text
            self._changed("html", origin)

    def set_css(self, text: str, origin=None):
        if text != self.css:
            self.css = text
            self._changed("css", origin)

    def set_js(self, text: str, origin=None):
        if text != self.js:
            self.js = text
            self._changed("js", origin)

    def set_blocks(self, data: dict, origin=None):
        self.blocks = blocks_mod.sanitize(data)
        self._changed("blocks", origin)

    def set_stripe(self, settings: dict):
        self.stripe = {k: str(settings.get(k, STRIPE_DEFAULTS[k])).strip() for k in STRIPE_FIELDS}
        self._changed("stripe")

    def add_asset(self, path: str) -> str:
        """Copy an image file into the project; return its "assets/…" URL."""
        with open(path, "rb") as fh:
            raw = fh.read()
        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if not mime.startswith("image/"):
            raise ValueError(f"{os.path.basename(path)} is not an image")
        data = base64.b64encode(raw).decode()
        for name, asset in self.assets.items():
            if asset["data"] == data:
                return f"assets/{name}"
        stem, ext = os.path.splitext(os.path.basename(path))
        stem = re.sub(r"[^A-Za-z0-9_-]+", "-", stem).strip("-").lower() or "image"
        ext = re.sub(r"[^A-Za-z0-9.]", "", ext.lower())
        name, n = f"{stem}{ext}", 2
        while name in self.assets:
            name, n = f"{stem}-{n}{ext}", n + 1
        self.assets[name] = {"mime": mime, "data": data}
        self._changed("assets")
        return f"assets/{name}"

    def _inline_assets(self, text: str) -> str:
        def sub(match):
            asset = self.assets.get(match.group(2))
            if asset is None:
                return match.group(0)
            return f"{match.group(1)}data:{asset['mime']};base64,{asset['data']}"

        return _ASSET_RE.sub(sub, text)

    def set_meta(self, title=None, lang=None):
        if title is not None:
            self.title = title
        if lang is not None:
            self.lang = lang
        self._changed("meta")

    # -- element tree ---------------------------------------------------
    def document(self) -> HtmlDocument:
        if self._doc_cache is None or self._doc_cache.source != self.html:
            self._doc_cache = HtmlDocument(self.html)
        return self._doc_cache

    def elements(self):
        """Flat list of (sf_id, component, props, depth) in document order."""
        doc = self.document()
        result = []

        def visit(items, depth):
            for item in items:
                node = item["node"]
                comp = components.detect(node)
                if comp is not None:
                    result.append((item["sf_id"], comp, comp.props_from_node(node, doc.source), depth))
                visit(item["children"], depth + 1)

        visit(doc.element_tree(), 0)
        return result

    def element(self, sf_id: str):
        for entry in self.elements():
            if entry[0] == sf_id:
                return entry
        return None

    def html_ids(self) -> list:
        """All ``id`` attributes in the page, for the block element pickers."""
        ids = []
        for node in self.document().root.walk():
            value = node.attrs.get("id")
            if value and value not in ids:
                ids.append(value)
        return ids

    def _next_sf_id(self) -> str:
        numbers = [int(m.group(1)) for m in re.finditer(r'data-sf-id="sf-(\d+)"', self.html)]
        return f"sf-{max(numbers, default=0) + 1}"

    def _next_html_id(self, prefix: str) -> str:
        existing = set(self.html_ids())
        n = 1
        while f"{prefix}{n}" in existing:
            n += 1
        return f"{prefix}{n}"

    def add_component(self, key: str, target_id: str | None = None, where: str = "after",
                      props: dict | None = None) -> str:
        comp = components.BY_KEY[key]
        values = comp.defaults()
        values["id"] = self._next_html_id(components.ID_PREFIX.get(key, key))
        if props:
            values.update(props)
        sf_id = self._next_sf_id()
        snippet = comp.render(values, sf_id)
        self.html = self.document().insert(snippet, target_id, where)
        if comp.css:
            marker = comp.css.split("\n", 1)[0]
            if marker not in self.css:
                self.css = self.css.rstrip("\n") + ("\n\n" if self.css.strip() else "") + comp.css
                self._changed("css")
        self._changed("html")
        return sf_id

    def update_element(self, sf_id: str, props: dict, origin=None):
        entry = self.element(sf_id)
        if entry is None:
            return
        _sid, comp, current, _depth = entry
        merged = dict(current)
        merged.update(props)
        self.set_html(self.document().replace(sf_id, comp.render(merged, sf_id)), origin)

    def remove_element(self, sf_id: str):
        self.set_html(self.document().remove(sf_id))

    def move_element(self, sf_id: str, direction: int):
        self.set_html(self.document().move(sf_id, direction))

    # -- generation -----------------------------------------------------
    def uses_stripe(self) -> bool:
        if any(comp.key == "stripe" for _s, comp, _p, _d in self.elements()):
            return True
        types = blocks_mod.referenced_types(self.blocks)
        return bool(types & {"stripe_checkout", "stripe_success", "stripe_cancel"})

    def compiled_blocks(self) -> str:
        return blocks_mod.compile_blocks(self.blocks)

    def generate(self, for_export: bool = False, include_blocks: bool = True,
                 split: bool = False) -> dict:
        """Build the page. Returns {"index.html": str, ["style.css", "script.js"]}."""
        body = self.html
        css = self.css
        if for_export:
            body = strip_editor_attributes(body)
        if not split:  # single file (and the preview): embed images as data: URLs
            body = self._inline_assets(body)
            css = self._inline_assets(css)
        scripts = []
        external = []
        if self.uses_stripe():
            external.append(STRIPE_JS_URL)
            scripts.append(stripe_runtime(self.stripe))
        if self.js.strip():
            scripts.append(self.js)
        if include_blocks:
            compiled = self.compiled_blocks()
            if compiled:
                scripts.append(compiled)

        title = html_lib.escape(self.title or "Untitled")
        lang = html_lib.escape(self.lang or "en", quote=True)
        head = [
            "<!DOCTYPE html>",
            f'<html lang="{lang}">',
            "<head>",
            '  <meta charset="utf-8">',
            '  <meta name="viewport" content="width=device-width, initial-scale=1">',
            f"  <title>{title}</title>",
        ]
        files = {}
        if split:
            head.append('  <link rel="stylesheet" href="style.css">')
            files["style.css"] = css
        elif css.strip():
            head.append("  <style>\n" + _indent(css.replace("</style", "<\\/style"), "    ")
                        + "\n  </style>")
        head.append("</head>")
        tail = [f'<script src="{src}"></script>' for src in external]
        if split:
            if scripts:
                files["script.js"] = "\n\n".join(s.rstrip("\n") for s in scripts) + "\n"
                tail.append('<script src="script.js"></script>')
        else:
            for script in scripts:
                tail.append("<script>\n" + script.replace("</script", "<\\/script").rstrip("\n")
                            + "\n</script>")
        page = "\n".join(head) + "\n<body>\n" + body.rstrip("\n") + "\n"
        if tail:
            page += "\n".join(tail) + "\n"
        page += "</body>\n</html>\n"
        files["index.html"] = page
        return files

    def export(self, directory: str, split: bool = False) -> list:
        os.makedirs(directory, exist_ok=True)
        files = self.generate(for_export=True, split=split)
        written = []
        for name, content in files.items():
            path = os.path.join(directory, name)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(content)
            written.append(path)
        if split and self.assets:
            asset_dir = os.path.join(directory, "assets")
            os.makedirs(asset_dir, exist_ok=True)
            for name, asset in self.assets.items():
                path = os.path.join(asset_dir, name)
                with open(path, "wb") as fh:
                    fh.write(base64.b64decode(asset["data"]))
                written.append(path)
        return written

    # -- persistence ----------------------------------------------------
    def element_tree_json(self) -> list:
        doc = self.document()

        def convert(items):
            out = []
            for item in items:
                comp = components.detect(item["node"])
                entry = {"sf_id": item["sf_id"], "type": comp.key if comp else None,
                         "props": comp.props_from_node(item["node"], doc.source) if comp else {}}
                children = convert(item["children"])
                if children:
                    entry["children"] = children
                out.append(entry)
            return out

        return convert(doc.element_tree())

    def to_json(self) -> dict:
        return {
            "format": FORMAT_NAME,
            "version": FORMAT_VERSION,
            "title": self.title,
            "lang": self.lang,
            "html": self.html,
            "css": self.css,
            "js": self.js,
            "elements": self.element_tree_json(),
            "blocks": self.blocks,
            "stripe": obfuscate(self.stripe),
            "assets": self.assets,
        }

    @classmethod
    def from_json(cls, data: dict) -> "Project":
        if not isinstance(data, dict) or data.get("format") != FORMAT_NAME:
            raise ValueError("Not a SiteForge project file")
        if int(data.get("version", 1)) > FORMAT_VERSION:
            raise ValueError("This project was made with a newer SiteForge")
        project = cls()
        project.title = str(data.get("title", project.title))
        project.lang = str(data.get("lang", "en"))
        html = data.get("html")
        if not isinstance(html, str):
            html = _html_from_elements(data.get("elements") or [])
        project.html = html
        project.css = str(data.get("css", ""))
        project.js = str(data.get("js", ""))
        project.blocks = blocks_mod.sanitize(data.get("blocks"))
        stripe = dict(STRIPE_DEFAULTS)
        stripe.update(deobfuscate(data.get("stripe")))
        project.stripe = stripe
        for name, asset in (data.get("assets") or {}).items():
            if (isinstance(asset, dict) and re.fullmatch(r"[\w.-]+", str(name))
                    and isinstance(asset.get("data"), str)
                    and str(asset.get("mime", "")).startswith("image/")):
                project.assets[name] = {"mime": asset["mime"], "data": asset["data"]}
        return project

    def save(self, path: str | None = None):
        path = path or self.path
        if not path:
            raise ValueError("No file name given")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.to_json(), fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, path)
        self.path = path
        self.mark_saved()

    @classmethod
    def load(cls, path: str) -> "Project":
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        project = cls.from_json(data)
        project.path = path
        return project

    @classmethod
    def blank(cls) -> "Project":
        return cls()


def _indent(text: str, prefix: str) -> str:
    return "\n".join(prefix + line if line.strip() else "" for line in text.rstrip("\n").split("\n"))


def _html_from_elements(items) -> str:
    """Rebuild HTML from a serialized element tree (when "html" is absent)."""
    out = []
    for item in items:
        comp = components.BY_KEY.get(item.get("type"))
        if comp is None:
            continue
        props = comp.defaults()
        props.update(item.get("props") or {})
        if item.get("children") and comp.key in ("header", "footer"):
            props["content"] = _html_from_elements(item["children"]).strip("\n")
        out.append(comp.render(props, str(item.get("sf_id") or f"sf-{len(out) + 1}")))
    return "\n".join(out) + ("\n" if out else "")
