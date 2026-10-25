"""Palette components: how each one renders to HTML and reads back from it.

Every component is a plain HTML element tagged with ``data-sf-id``.  Its
properties are never stored separately; they are always read back from the
HTML source (``props_from_node``), so edits made in the Code tab and edits
made in the properties panel stay in sync automatically.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

from .htmldoc import SF_ID_ATTR, Node

SANDBOX_FLAGS = (
    "allow-scripts",
    "allow-same-origin",
    "allow-forms",
    "allow-popups",
    "allow-modals",
    "allow-downloads",
    "allow-top-navigation",
)

TEXT_TAGS = ("p", "span", "h1", "h2", "h3", "h4", "h5", "h6")


@dataclass
class Prop:
    name: str
    label: str
    kind: str = "text"  # text | multiline | int | choice | flags | image
    default: object = ""
    options: tuple = ()
    minimum: int = 0
    maximum: int = 10000


@dataclass
class Component:
    key: str
    title: str
    icon: str
    description: str
    props: list = field(default_factory=list)
    css: str = ""  # stylesheet appended to the project once, on first drop

    def defaults(self) -> dict:
        return {p.name: p.default for p in self.props}

    def render(self, props: dict, sf_id: str) -> str:  # pragma: no cover - abstract
        raise NotImplementedError

    def matches(self, node: Node) -> bool:  # pragma: no cover - abstract
        raise NotImplementedError

    def props_from_node(self, node: Node, source: str) -> dict:
        props = self.defaults()
        for prop in self.props:
            if prop.name in node.attrs and prop.kind != "multiline":
                value = node.attrs[prop.name]
                props[prop.name] = _coerce(prop, value)
        return props


def _coerce(prop: Prop, value):
    if prop.kind == "int":
        try:
            return int(str(value).strip().rstrip("px"))
        except ValueError:
            return prop.default
    if prop.kind == "flags":
        return [f for f in str(value).split() if f]
    return value


def attr(name: str, value) -> str:
    # Attribute values are always double-quoted, so single quotes stay readable.
    return f' {name}="{html.escape(str(value), quote=False).replace(chr(34), "&quot;")}"'


def common_attrs(props: dict, sf_id: str, extra_class: str = "") -> str:
    out = attr(SF_ID_ATTR, sf_id)
    if props.get("id"):
        out += attr("id", props["id"])
    classes = " ".join(c for c in (extra_class, props.get("class", "")) if c)
    if classes:
        out += attr("class", classes)
    return out


def style_size_attr(props: dict) -> str:
    """Inline ``style`` for components sized with CSS (text boxes, buttons).

    Width/height of 0 mean "auto".  Declarations other than width/height that
    were already in the element's style attribute are kept (``_style_rest``).
    """
    decls = [d for d in (props.get("_style_rest") or "").split(";") if d.strip()]
    for name in ("width", "height"):
        value = int(props.get(name) or 0)
        if value > 0:
            decls.append(f"{name}: {value}px")
    return attr("style", "; ".join(d.strip() for d in decls)) if decls else ""


def style_size_props(node: Node) -> dict:
    out = {"width": 0, "height": 0, "_style_rest": ""}
    rest = []
    for decl in node.attrs.get("style", "").split(";"):
        if ":" not in decl:
            continue
        key, value = (part.strip() for part in decl.split(":", 1))
        match = re.fullmatch(r"(\d+(?:\.\d+)?)px", value)
        if key.lower() in ("width", "height") and match:
            out[key.lower()] = round(float(match.group(1)))
        else:
            rest.append(f"{key}: {value}")
    out["_style_rest"] = "; ".join(rest)
    return out


def _class_without(node: Node, remove: str) -> str:
    return " ".join(c for c in node.attrs.get("class", "").split() if c != remove)


ID = Prop("id", "Element ID")
CLASS = Prop("class", "CSS class")
AUTO_WIDTH = Prop("width", "Width in px (0 = auto)", "int", 0, maximum=4096)
AUTO_HEIGHT = Prop("height", "Height in px (0 = auto)", "int", 0, maximum=4096)

# Shown until the user picks a picture: a small self-contained SVG.
PLACEHOLDER_IMAGE = (
    "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='320' height='200'%3E"
    "%3Crect width='320' height='200' rx='12' fill='%23deddda'/%3E"
    "%3Cpath d='M110 140l40-50 30 36 20-22 30 36z' fill='%239a9996'/%3E"
    "%3Ccircle cx='205' cy='70' r='14' fill='%239a9996'/%3E%3C/svg%3E")


class CanvasComponent(Component):
    def render(self, props, sf_id):
        return (f"<canvas{common_attrs(props, sf_id)}{attr('width', props['width'])}"
                f"{attr('height', props['height'])}></canvas>")

    def matches(self, node):
        return node.tag == "canvas"


class IframeComponent(Component):
    def render(self, props, sf_id):
        flags = " ".join(props.get("sandbox") or [])
        return (f"<iframe{common_attrs(props, sf_id)}{attr('src', props['src'])}"
                f"{attr('title', props['title'])}{attr('width', props['width'])}"
                f"{attr('height', props['height'])}{attr('sandbox', flags)}></iframe>")

    def matches(self, node):
        return node.tag == "iframe"


class TextBoxComponent(Component):
    def render(self, props, sf_id):
        common = (common_attrs(props, sf_id) + attr("name", props["name"])
                  + attr("placeholder", props["placeholder"]))
        if props.get("label"):
            common += attr("aria-label", props["label"])
        common += style_size_attr(props)
        rows = int(props.get("rows") or 1)
        if rows > 1:
            return f"<textarea{common}{attr('rows', rows)}></textarea>"
        return f'<input type="text"{common}>'

    def matches(self, node):
        return node.tag == "textarea" or (
            node.tag == "input" and node.attrs.get("type", "text") == "text")

    def props_from_node(self, node, source):
        props = super().props_from_node(node, source)
        props["label"] = node.attrs.get("aria-label", "")
        props["rows"] = _coerce(self.props[4], node.attrs.get("rows", 2)) if node.tag == "textarea" else 1
        props.update(style_size_props(node))
        return props


class ButtonComponent(Component):
    def render(self, props, sf_id):
        out = f'<button type="button"{common_attrs(props, sf_id)}'
        if props.get("onclick"):
            out += attr("onclick", props["onclick"])
        out += style_size_attr(props)
        return out + f">{props['label']}</button>"

    def matches(self, node):
        return node.tag == "button" and node.parent is not None and not (
            "sf-stripe-button" in node.attrs.get("class", "").split())

    def props_from_node(self, node, source):
        props = super().props_from_node(node, source)
        props["label"] = node.inner(source)
        props.update(style_size_props(node))
        return props


class ImageComponent(Component):
    def render(self, props, sf_id):
        out = f"<img{common_attrs(props, sf_id)}{attr('src', props['src'])}{attr('alt', props['alt'])}"
        for name in ("width", "height"):
            if int(props.get(name) or 0) > 0:
                out += attr(name, int(props[name]))
        return out + ">"

    def matches(self, node):
        return node.tag == "img"


class TextComponent(Component):
    def render(self, props, sf_id):
        tag = props.get("tag") if props.get("tag") in TEXT_TAGS else "p"
        return f"<{tag}{common_attrs(props, sf_id)}>{props['content']}</{tag}>"

    def matches(self, node):
        return node.tag in TEXT_TAGS

    def props_from_node(self, node, source):
        props = super().props_from_node(node, source)
        props["tag"] = node.tag
        props["content"] = node.inner(source)
        return props


class SectionComponent(Component):
    """``<header>`` and ``<footer>``: a landmark wrapping free-form content."""

    tag = "header"

    def render(self, props, sf_id):
        content = props.get("content", "")
        if "\n" in content.strip():
            body = "\n".join("  " + line if line else line
                             for line in content.strip("\n").split("\n"))
            return f"<{self.tag}{common_attrs(props, sf_id)}>\n{body}\n</{self.tag}>"
        return f"<{self.tag}{common_attrs(props, sf_id)}>{content}</{self.tag}>"

    def matches(self, node):
        return node.tag == self.tag

    def props_from_node(self, node, source):
        props = super().props_from_node(node, source)
        inner = node.inner(source)
        lines = inner.strip("\n").split("\n")
        if len(lines) > 1:
            # Undo the two-space indent added by render() / the code editor.
            import textwrap
            inner = textwrap.dedent(inner.strip("\n"))
        props["content"] = inner.strip() if len(lines) <= 1 else inner
        return props


class HeaderComponent(SectionComponent):
    tag = "header"


class FooterComponent(SectionComponent):
    tag = "footer"


class StripeComponent(Component):
    """Checkout button + hidden form.

    The publishable key and price live in the project's Stripe settings and
    are injected by the generator at preview/export time, never in the HTML.
    """

    def render(self, props, sf_id):
        wrapper = common_attrs(props, sf_id, extra_class="sf-stripe")
        return (
            f"<div{wrapper} data-sf-stripe>\n"
            f'  <button type="button" class="sf-stripe-button">{props["label"]}</button>\n'
            f'  <form class="sf-stripe-form" hidden>\n'
            f'    <input type="hidden" name="price">\n'
            f'    <input type="hidden" name="email">\n'
            f"  </form>\n"
            f"</div>"
        )

    def matches(self, node):
        return node.tag == "div" and "data-sf-stripe" in node.attrs

    def props_from_node(self, node, source):
        props = super().props_from_node(node, source)
        props["class"] = _class_without(node, "sf-stripe")
        button = node.find_first("button")
        if button is not None:
            props["label"] = button.inner(source)
        return props


STRIPE_CSS = """/* sf:stripe — SiteForge checkout button */
.sf-stripe-button {
  background: #635bff;
  color: #fff;
  border: none;
  border-radius: 8px;
  padding: 12px 24px;
  font: 600 16px system-ui, sans-serif;
  cursor: pointer;
}
.sf-stripe-button:hover {
  background: #5146e5;
}
.sf-stripe-button:disabled {
  opacity: 0.6;
  cursor: progress;
}
"""

COMPONENTS = [
    CanvasComponent(
        "canvas", "Canvas", "applications-graphics-symbolic",
        "Drawing surface for blocks or scripts",
        [ID, Prop("width", "Width", "int", 300, maximum=4096),
         Prop("height", "Height", "int", 150, maximum=4096), CLASS]),
    IframeComponent(
        "iframe", "Iframe", "web-browser-symbolic", "Embed another page",
        [ID, Prop("src", "Source URL", default="https://example.com"),
         Prop("title", "Accessible title", default="Embedded page"),
         Prop("width", "Width", "int", 480, maximum=4096),
         Prop("height", "Height", "int", 320, maximum=4096),
         Prop("sandbox", "Sandbox flags", "flags", ["allow-scripts"], options=SANDBOX_FLAGS),
         CLASS]),
    TextBoxComponent(
        "textbox", "Text Box", "insert-text-symbolic", "Single or multi-line input",
        [ID, Prop("name", "Name", default="message"),
         Prop("placeholder", "Placeholder", default="Type here…"),
         Prop("label", "Accessible label", default="Message"),
         Prop("rows", "Rows (1 = single line)", "int", 1, minimum=1, maximum=50),
         AUTO_WIDTH, AUTO_HEIGHT, CLASS]),
    ButtonComponent(
        "button", "Button", "input-mouse-symbolic", "Clickable button",
        [ID, Prop("label", "Label", default="Click me"), CLASS,
         Prop("onclick", "onclick handler"), AUTO_WIDTH, AUTO_HEIGHT]),
    ImageComponent(
        "image", "Image", "image-x-generic-symbolic", "Picture from a file or URL",
        [ID, Prop("src", "Image", "image", PLACEHOLDER_IMAGE),
         Prop("alt", "Alternative text", default="Describe the image"),
         Prop("width", "Width (0 = natural)", "int", 320, maximum=8192),
         Prop("height", "Height (0 = natural)", "int", 0, maximum=8192), CLASS]),
    TextComponent(
        "text", "Text", "format-text-bold-symbolic", "Paragraph, span or heading",
        [ID, Prop("tag", "Tag", "choice", "p", options=TEXT_TAGS),
         Prop("content", "Content", "multiline", "Edit this text."), CLASS]),
    HeaderComponent(
        "header", "Header", "go-top-symbolic", "Page header landmark",
        [ID, Prop("content", "Inner content", "multiline", "<h1>My Website</h1>"), CLASS]),
    FooterComponent(
        "footer", "Footer", "go-bottom-symbolic", "Page footer landmark",
        [ID, Prop("content", "Inner content", "multiline", "<p>&copy; 2026 My Website</p>"),
         CLASS]),
    StripeComponent(
        "stripe", "Stripe", "auth-smartcard-symbolic", "Stripe Checkout button",
        [ID, Prop("label", "Button label", default="Buy now"), CLASS],
        css=STRIPE_CSS),
]

BY_KEY = {c.key: c for c in COMPONENTS}

# Components that get a drag-to-resize handle in the preview.
RESIZABLE = ("image", "canvas", "textbox", "button", "iframe")

# Detection order matters: Stripe (a div) and the stripe button must be
# recognised before the generic button.
_DETECT_ORDER = ["stripe", "canvas", "iframe", "image", "textbox", "button", "header", "footer",
                 "text"]


def detect(node: Node) -> Component | None:
    for key in _DETECT_ORDER:
        if BY_KEY[key].matches(node):
            return BY_KEY[key]
    return None


ID_PREFIX = {"image": "image", "canvas": "canvas", "iframe": "frame", "textbox": "input", "button": "button",
             "text": "text", "header": "header", "footer": "footer", "stripe": "checkout"}
