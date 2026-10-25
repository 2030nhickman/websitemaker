"""Scratch-style block definitions and the block → JavaScript compiler.

This module is pure Python (no GTK) so the compiler can be unit tested and
used for exports from the command line.  See DESIGN.md for the rules.

Block JSON shape (as stored in ``.siteforge`` files)::

    {"type": "looks_show", "inputs": {"el": "button1"},
     "next": {...} | None, "substacks": {"body": {...}}, "x": 40, "y": 60}

An input value is either a literal (string / number) or a reporter block
dict (one with a ``"type"`` key).  ``x``/``y`` only matter for the first
block of a top-level stack.
"""

from __future__ import annotations

import itertools
import json
import re
from dataclasses import dataclass, field

CATEGORIES = [
    # key, label, fill colour, border colour
    ("events", "Events", "#FFBF00", "#CC9900"),
    ("motion", "Motion", "#4C97FF", "#3373CC"),
    ("looks", "Looks", "#9966FF", "#774DCB"),
    ("logic", "Logic", "#FFAB19", "#CF8B17"),
    ("variables", "Variables", "#2D4DB2", "#1F3780"),
    ("stripe", "Stripe", "#2EAD5B", "#208A45"),
]
CATEGORY_COLORS = {key: (fill, border) for key, _label, fill, border in CATEGORIES}

# Shapes
HAT, STACK, C, C2, REPORTER, BOOLEAN, CAP = "hat", "stack", "c", "c2", "reporter", "boolean", "cap"

COMPARE_OPS = ("=", "≠", "<", ">", "≤", "≥")
MATH_OPS = ("+", "−", "×", "÷", "mod")
KEYS = ("space", "Enter", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight",
        *"abcdefghijklmnopqrstuvwxyz", *"0123456789")


@dataclass
class InputSpec:
    kind: str  # element | text | number | color | variable | choice | boolean
    default: object = ""
    options: tuple = ()


@dataclass
class BlockDef:
    type: str
    category: str
    shape: str
    label: str  # "set text color of {el} to {color}"
    inputs: dict = field(default_factory=dict)
    substacks: tuple = ()  # names of C mouths, in order
    mouth_labels: tuple = ()  # label shown above each extra mouth ("else")
    help: str = ""

    def parts(self):
        """Split the label into ("text", str) and ("input", name) parts."""
        out = []
        for piece in re.split(r"(\{\w+\})", self.label):
            if not piece:
                continue
            if piece.startswith("{") and piece.endswith("}"):
                out.append(("input", piece[1:-1]))
            else:
                out.append(("text", piece.strip()))
        return [p for p in out if p[1]]


def _defs():
    el = InputSpec("element", "")
    num = lambda d: InputSpec("number", d)  # noqa: E731
    txt = lambda d: InputSpec("text", d)  # noqa: E731
    col = lambda d: InputSpec("color", d)  # noqa: E731
    var = InputSpec("variable", "")
    cond = InputSpec("boolean")
    return [
        # Events
        BlockDef("event_page_load", "events", HAT, "when page loads",
                 help="Runs once the page has finished loading."),
        BlockDef("event_click", "events", HAT, "when {el} clicked", {"el": el},
                 help="Runs every time the element is clicked."),
        BlockDef("event_key", "events", HAT, "when {key} key pressed",
                 {"key": InputSpec("choice", "space", KEYS)}),
        BlockDef("event_every", "events", HAT, "every {secs} seconds", {"secs": num(1)}),
        # Motion / layout
        BlockDef("motion_set_position", "motion", STACK, "set position of {el} to x: {x} y: {y}",
                 {"el": el, "x": num(0), "y": num(0)}),
        BlockDef("motion_move_by", "motion", STACK, "move {el} by x: {dx} y: {dy}",
                 {"el": el, "dx": num(10), "dy": num(0)}),
        BlockDef("motion_set_size", "motion", STACK, "set size of {el} to w: {w} h: {h}",
                 {"el": el, "w": num(200), "h": num(100)}),
        BlockDef("motion_resize_canvas", "motion", STACK, "resize canvas {el} to w: {w} h: {h}",
                 {"el": el, "w": num(300), "h": num(150)}),
        # Looks
        BlockDef("looks_text_color", "looks", STACK, "set text color of {el} to {color}",
                 {"el": el, "color": col("#e01b24")}),
        BlockDef("looks_bg_color", "looks", STACK, "set background of {el} to {color}",
                 {"el": el, "color": col("#ffffff")}),
        BlockDef("looks_show", "looks", STACK, "show {el}", {"el": el}),
        BlockDef("looks_hide", "looks", STACK, "hide {el}", {"el": el}),
        BlockDef("looks_toggle", "looks", STACK, "toggle visibility of {el}", {"el": el}),
        BlockDef("looks_set_text", "looks", STACK, "set text of {el} to {text}",
                 {"el": el, "text": txt("Hello!")}),
        BlockDef("looks_clear_canvas", "looks", STACK, "clear canvas {el}", {"el": el}),
        BlockDef("looks_draw_circle", "looks", STACK,
                 "draw circle on {el} at x: {x} y: {y} radius: {r} color: {color}",
                 {"el": el, "x": num(50), "y": num(50), "r": num(20), "color": col("#3584e4")}),
        BlockDef("looks_draw_rect", "looks", STACK,
                 "draw rect on {el} at x: {x} y: {y} w: {w} h: {h} color: {color}",
                 {"el": el, "x": num(10), "y": num(10), "w": num(50), "h": num(30),
                  "color": col("#2ec27e")}),
        BlockDef("looks_text_of", "looks", REPORTER, "text of {el}", {"el": el}),
        BlockDef("looks_value_of", "looks", REPORTER, "value of {el}", {"el": el}),
        # Logic
        BlockDef("logic_if", "logic", C, "if {cond} then", {"cond": cond}, ("then",)),
        BlockDef("logic_if_else", "logic", C2, "if {cond} then", {"cond": cond},
                 ("then", "else"), ("else",)),
        BlockDef("logic_repeat", "logic", C, "repeat {times}", {"times": num(10)}, ("body",)),
        BlockDef("logic_forever", "logic", C, "forever", {}, ("body",)),
        BlockDef("logic_wait", "logic", STACK, "wait {secs} seconds", {"secs": num(1)}),
        BlockDef("logic_wait_until", "logic", STACK, "wait until {cond}", {"cond": cond}),
        BlockDef("logic_stop", "logic", CAP, "stop this script"),
        BlockDef("logic_compare", "logic", BOOLEAN, "{a} {op} {b}",
                 {"a": txt(""), "op": InputSpec("choice", "=", COMPARE_OPS), "b": txt("50")}),
        BlockDef("logic_and", "logic", BOOLEAN, "{a} and {b}", {"a": cond, "b": cond}),
        BlockDef("logic_or", "logic", BOOLEAN, "{a} or {b}", {"a": cond, "b": cond}),
        BlockDef("logic_not", "logic", BOOLEAN, "not {a}", {"a": cond}),
        BlockDef("op_math", "logic", REPORTER, "{a} {op} {b}",
                 {"a": num(""), "op": InputSpec("choice", "+", MATH_OPS), "b": num("")}),
        BlockDef("op_random", "logic", REPORTER, "pick random {a} to {b}",
                 {"a": num(1), "b": num(10)}),
        BlockDef("op_join", "logic", REPORTER, "join {a} {b}", {"a": txt("hello "), "b": txt("world")}),
        # Variables
        BlockDef("var_set", "variables", STACK, "set {var} to {value}", {"var": var, "value": txt(0)}),
        BlockDef("var_change", "variables", STACK, "change {var} by {value}",
                 {"var": var, "value": num(1)}),
        BlockDef("var_get", "variables", REPORTER, "{var}", {"var": var}),
        BlockDef("var_log", "variables", STACK, "log {value}", {"value": txt("Hello")}),
        # Stripe
        BlockDef("stripe_checkout", "stripe", STACK, "open Stripe checkout",
                 help="Redirects to Stripe Checkout using the configured Price ID."),
        BlockDef("stripe_success", "stripe", HAT, "when payment succeeds",
                 help="Runs on page load when Stripe redirected back after a payment."),
        BlockDef("stripe_cancel", "stripe", HAT, "when payment is canceled"),
    ]


BLOCK_DEFS = {d.type: d for d in _defs()}


def defs_in_category(category: str):
    return [d for d in BLOCK_DEFS.values() if d.category == category]


def new_block(block_type: str) -> dict:
    definition = BLOCK_DEFS[block_type]
    block = {"type": block_type, "inputs": {}, "next": None}
    for name, spec in definition.inputs.items():
        if spec.kind != "boolean":
            block["inputs"][name] = spec.default
    if definition.substacks:
        block["substacks"] = {name: None for name in definition.substacks}
    return block


def is_reporter(block: dict) -> bool:
    return BLOCK_DEFS[block["type"]].shape in (REPORTER, BOOLEAN)


def iter_stack(block: dict | None):
    while block is not None:
        yield block
        block = block.get("next")


def sanitize(data) -> dict:
    """Drop unknown block types / malformed values from loaded JSON."""

    def clean_block(block):
        if not isinstance(block, dict) or block.get("type") not in BLOCK_DEFS:
            return None
        definition = BLOCK_DEFS[block["type"]]
        out = {"type": block["type"], "inputs": {}, "next": None}
        inputs = block.get("inputs") or {}
        for name, spec in definition.inputs.items():
            value = inputs.get(name, None if spec.kind == "boolean" else spec.default)
            if isinstance(value, dict):
                value = clean_block(value)
                if value is not None and not is_reporter(value):
                    value = None
            elif value is not None and not isinstance(value, (str, int, float)):
                value = spec.default
            if value is not None:
                out["inputs"][name] = value
        if definition.substacks:
            subs = block.get("substacks") or {}
            out["substacks"] = {n: clean_block(subs.get(n)) for n in definition.substacks}
        if definition.shape not in (CAP, REPORTER, BOOLEAN):
            out["next"] = clean_block(block.get("next"))
        for key in ("x", "y"):
            if isinstance(block.get(key), (int, float)):
                out[key] = block[key]
        return out

    data = data if isinstance(data, dict) else {}
    scripts = [s for s in (clean_block(b) for b in data.get("scripts") or []) if s]
    variables = []
    for var in data.get("variables") or []:
        if isinstance(var, dict) and isinstance(var.get("name"), str) and var["name"].strip():
            initial = var.get("initial", 0)
            if not isinstance(initial, (str, int, float)):
                initial = 0
            variables.append({"name": var["name"].strip(), "initial": initial})
    return {"scripts": scripts, "variables": variables}


# --------------------------------------------------------------------------
# Compiler
# --------------------------------------------------------------------------

RUNTIME = r"""const sf = {
  vars: __VARS__,
  el(id) {
    const e = typeof id === 'string' ? document.getElementById(id) : id;
    if (!e) console.warn('[SiteForge blocks] no element with id', id);
    return e;
  },
  num(v) { const n = Number(v); return Number.isFinite(n) ? n : 0; },
  wait(secs) { return new Promise(r => setTimeout(r, Math.max(0, sf.num(secs)) * 1000)); },
  frame() { return new Promise(r => requestAnimationFrame(() => r())); },
  ready(fn) {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', fn);
    else fn();
  },
  run(fn, ev) {
    Promise.resolve().then(() => fn(ev)).catch(err => console.error('[SiteForge blocks]', err));
  },
  on(id, type, fn) {
    sf.ready(() => { const e = sf.el(id); if (e) e.addEventListener(type, ev => sf.run(fn, ev)); });
  },
  setPosition(id, x, y) {
    const e = sf.el(id); if (!e) return;
    if (getComputedStyle(e).position === 'static') e.style.position = 'relative';
    e.style.left = sf.num(x) + 'px'; e.style.top = sf.num(y) + 'px';
  },
  moveBy(id, dx, dy) {
    const e = sf.el(id); if (!e) return;
    sf.setPosition(e, (parseFloat(e.style.left) || 0) + sf.num(dx),
                   (parseFloat(e.style.top) || 0) + sf.num(dy));
  },
  setSize(id, w, h) {
    const e = sf.el(id); if (!e) return;
    e.style.width = sf.num(w) + 'px'; e.style.height = sf.num(h) + 'px';
  },
  resizeCanvas(id, w, h) { const e = sf.el(id); if (e) { e.width = sf.num(w); e.height = sf.num(h); } },
  style(id, prop, value) { const e = sf.el(id); if (e) e.style[prop] = String(value); },
  show(id, visible) {
    const e = sf.el(id); if (!e) return;
    if (visible === null) visible = e.hidden || e.style.display === 'none';
    e.hidden = !visible; e.style.display = visible ? '' : 'none';
  },
  setText(id, text) { const e = sf.el(id); if (e) e.textContent = String(text); },
  text(id) { const e = sf.el(id); return e ? e.textContent : ''; },
  value(id) { const e = sf.el(id); return e && 'value' in e ? e.value : ''; },
  ctx(id) { const e = sf.el(id); return e && e.getContext ? e.getContext('2d') : null; },
  clear(id) { const c = sf.ctx(id); if (c) c.clearRect(0, 0, c.canvas.width, c.canvas.height); },
  circle(id, x, y, r, color) {
    const c = sf.ctx(id); if (!c) return;
    c.beginPath(); c.arc(sf.num(x), sf.num(y), Math.max(0, sf.num(r)), 0, Math.PI * 2);
    c.fillStyle = String(color); c.fill();
  },
  rect(id, x, y, w, h, color) {
    const c = sf.ctx(id); if (!c) return;
    c.fillStyle = String(color); c.fillRect(sf.num(x), sf.num(y), sf.num(w), sf.num(h));
  },
  random(a, b) {
    let lo = sf.num(a), hi = sf.num(b);
    if (lo > hi) [lo, hi] = [hi, lo];
    if (Number.isInteger(lo) && Number.isInteger(hi)) return lo + Math.floor(Math.random() * (hi - lo + 1));
    return lo + Math.random() * (hi - lo);
  },
  math(a, op, b) {
    a = sf.num(a); b = sf.num(b);
    switch (op) {
      case '+': return a + b;
      case '-': return a - b;
      case '*': return a * b;
      case '/': return b === 0 ? 0 : a / b;
      case 'mod': return b === 0 ? 0 : ((a % b) + b) % b;
    }
    return 0;
  },
  compare(a, op, b) {
    const na = Number(a), nb = Number(b);
    const numeric = String(a).trim() !== '' && String(b).trim() !== '' && !isNaN(na) && !isNaN(nb);
    const x = numeric ? na : String(a).toLowerCase(), y = numeric ? nb : String(b).toLowerCase();
    switch (op) {
      case '=': return x === y;
      case '!=': return x !== y;
      case '<': return x < y;
      case '>': return x > y;
      case '<=': return x <= y;
      case '>=': return x >= y;
    }
    return false;
  },
  stripeCheckout() {
    if (typeof window.siteforgeStripeCheckout === 'function') return window.siteforgeStripeCheckout();
    console.warn('[SiteForge blocks] Stripe is not configured (add a Stripe component or settings)');
  },
  checkoutStatus() {
    if (typeof window.siteforgeCheckoutStatus === 'function') return window.siteforgeCheckoutStatus();
    return new URLSearchParams(location.search).get('checkout');
  },
};
window.siteforgeVars = sf.vars;"""

_OP_JS = {"=": "=", "≠": "!=", "<": "<", ">": ">", "≤": "<=", "≥": ">=",
          "+": "+", "−": "-", "×": "*", "÷": "/", "mod": "mod"}

_KEY_JS = {"space": " "}

_NUMBER_RE = re.compile(r"^\s*-?(\d+(\.\d*)?|\.\d+)([eE][-+]?\d+)?\s*$")


class Compiler:
    def __init__(self, variables=()):
        self.variables = list(variables)
        self._counter = itertools.count()

    # -- expressions -----------------------------------------------------
    def expr(self, block: dict, name: str) -> str:
        definition = BLOCK_DEFS[block["type"]]
        spec = definition.inputs[name]
        value = block.get("inputs", {}).get(name, None if spec.kind == "boolean" else spec.default)
        if isinstance(value, dict):
            return self.reporter(value)
        if spec.kind == "boolean":
            return "false"
        if spec.kind == "element":
            return json.dumps(str(value))
        if spec.kind == "variable":
            return json.dumps(str(value))
        if spec.kind == "choice":
            text = str(value)
            if text not in spec.options:
                text = str(spec.default)
            return json.dumps(_OP_JS.get(text, _KEY_JS.get(text, text)))
        if spec.kind == "number":
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return repr(value)
            if _NUMBER_RE.match(str(value)):
                return str(value).strip()
            return f"sf.num({json.dumps(str(value))})"
        # text / color: numbers stay numbers (Scratch-like), everything else is a string
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return repr(value)
        if spec.kind == "text" and _NUMBER_RE.match(str(value)):
            return str(value).strip()
        return json.dumps(str(value))

    def reporter(self, block: dict) -> str:
        t = block["type"]
        e = lambda n: self.expr(block, n)  # noqa: E731
        if t == "var_get":
            return f"sf.vars[{e('var')}]"
        if t == "op_math":
            return f"sf.math({e('a')}, {e('op')}, {e('b')})"
        if t == "op_random":
            return f"sf.random({e('a')}, {e('b')})"
        if t == "op_join":
            return f"(String({e('a')}) + String({e('b')}))"
        if t == "looks_text_of":
            return f"sf.text({e('el')})"
        if t == "looks_value_of":
            return f"sf.value({e('el')})"
        if t == "logic_compare":
            return f"sf.compare({e('a')}, {e('op')}, {e('b')})"
        if t == "logic_and":
            return f"(Boolean({e('a')}) && Boolean({e('b')}))"
        if t == "logic_or":
            return f"(Boolean({e('a')}) || Boolean({e('b')}))"
        if t == "logic_not":
            return f"!({e('a')})"
        return "undefined"

    # -- statements ------------------------------------------------------
    def stack(self, block: dict | None, indent: str) -> list:
        lines = []
        for b in iter_stack(block):
            lines.extend(self.statement(b, indent))
        return lines

    def substack(self, block: dict, name: str, indent: str) -> list:
        return self.stack((block.get("substacks") or {}).get(name), indent)

    def statement(self, block: dict, ind: str) -> list:
        t = block["type"]
        e = lambda n: self.expr(block, n)  # noqa: E731
        simple = {
            "motion_set_position": lambda: f"sf.setPosition({e('el')}, {e('x')}, {e('y')});",
            "motion_move_by": lambda: f"sf.moveBy({e('el')}, {e('dx')}, {e('dy')});",
            "motion_set_size": lambda: f"sf.setSize({e('el')}, {e('w')}, {e('h')});",
            "motion_resize_canvas": lambda: f"sf.resizeCanvas({e('el')}, {e('w')}, {e('h')});",
            "looks_text_color": lambda: f"sf.style({e('el')}, 'color', {e('color')});",
            "looks_bg_color": lambda: f"sf.style({e('el')}, 'background', {e('color')});",
            "looks_show": lambda: f"sf.show({e('el')}, true);",
            "looks_hide": lambda: f"sf.show({e('el')}, false);",
            "looks_toggle": lambda: f"sf.show({e('el')}, null);",
            "looks_set_text": lambda: f"sf.setText({e('el')}, {e('text')});",
            "looks_clear_canvas": lambda: f"sf.clear({e('el')});",
            "looks_draw_circle": lambda: (f"sf.circle({e('el')}, {e('x')}, {e('y')}, {e('r')}, "
                                          f"{e('color')});"),
            "looks_draw_rect": lambda: (f"sf.rect({e('el')}, {e('x')}, {e('y')}, {e('w')}, "
                                        f"{e('h')}, {e('color')});"),
            "logic_wait": lambda: f"await sf.wait({e('secs')});",
            "logic_wait_until": lambda: f"while (!({e('cond')})) await sf.frame();",
            "logic_stop": lambda: "return;",
            "var_set": lambda: f"sf.vars[{e('var')}] = {e('value')};",
            "var_change": lambda: (f"sf.vars[{e('var')}] = sf.num(sf.vars[{e('var')}]) + "
                                   f"sf.num({e('value')});"),
            "var_log": lambda: f"console.log({e('value')});",
            "stripe_checkout": lambda: "await sf.stripeCheckout();",
        }
        if t in simple:
            return [ind + simple[t]()]
        inner = ind + "  "
        if t in ("logic_if", "logic_if_else"):
            lines = [f"{ind}if ({e('cond')}) {{"] + self.substack(block, "then", inner)
            if t == "logic_if_else":
                lines += [f"{ind}}} else {{"] + self.substack(block, "else", inner)
            return lines + [ind + "}"]
        if t == "logic_repeat":
            n = next(self._counter)
            return ([f"{ind}for (let i{n} = 0, n{n} = sf.num({e('times')}); i{n} < n{n}; i{n}++) {{"]
                    + self.substack(block, "body", inner)
                    + [f"{inner}await sf.frame();", ind + "}"])
        if t == "logic_forever":
            return (["{}for (;;) {{".format(ind)] + self.substack(block, "body", inner)
                    + [f"{inner}await sf.frame();", ind + "}"])
        # A reporter dropped into a stack (not possible from the UI) is ignored.
        return []

    # -- scripts ---------------------------------------------------------
    def script(self, hat: dict) -> list:
        t = hat["type"]
        body = self.stack(hat.get("next"), "    ")
        if not body:
            return []
        if t == "event_page_load":
            head, tail = "sf.ready(() => sf.run(async () => {", "}));"
        elif t == "event_click":
            head, tail = f"sf.on({self.expr(hat, 'el')}, 'click', async (event) => {{", "});"
        elif t == "event_key":
            key = self.expr(hat, "key")
            head = f"document.addEventListener('keydown', (event) => {{ if (event.key === {key}) sf.run(async () => {{"
            tail = "}); });"
        elif t == "event_every":
            head = (f"sf.ready(() => setInterval(() => sf.run(async () => {{")
            tail = f"}}), Math.max(0.01, sf.num({self.expr(hat, 'secs')})) * 1000));"
        elif t in ("stripe_success", "stripe_cancel"):
            status = "success" if t == "stripe_success" else "cancel"
            head = f"sf.ready(() => {{ if (sf.checkoutStatus() === '{status}') sf.run(async () => {{"
            tail = "}); });"
        else:
            return []
        return ["  " + head] + body + ["  " + tail]

    def compile(self, scripts) -> str:
        variables = {}
        for var in self.variables:
            initial = var.get("initial", 0)
            if isinstance(initial, str) and _NUMBER_RE.match(initial):
                initial = float(initial) if "." in initial or "e" in initial.lower() else int(initial)
            variables[var["name"]] = initial
        chunks = []
        for i, hat in enumerate(scripts):
            if BLOCK_DEFS.get(hat.get("type"), None) is None:
                continue
            if BLOCK_DEFS[hat["type"]].shape != HAT:
                continue  # stacks without a hat never run, like in Scratch
            lines = self.script(hat)
            if lines:
                label = re.sub(r"\{(\w+)\}", lambda m: "[" + str(
                    (hat.get("inputs") or {}).get(m.group(1), ""))[:40].replace("\n", " ") + "]", BLOCK_DEFS[hat["type"]].label)
                chunks.append(f"  // Script {i + 1}: {label}\n"
                              + "\n".join(lines))
        if not chunks:
            return ""
        runtime = RUNTIME.replace("__VARS__", json.dumps(variables, ensure_ascii=False))
        runtime = "\n".join("  " + line if line else line for line in runtime.split("\n"))
        return ("// Generated by SiteForge from visual blocks — edits here are overwritten.\n"
                "(function () {\n  'use strict';\n" + runtime + "\n\n"
                + "\n\n".join(chunks) + "\n})();\n")


def compile_blocks(block_data: dict) -> str:
    """Compile ``{"scripts": [...], "variables": [...]}`` to JavaScript."""
    data = sanitize(block_data)
    return Compiler(data["variables"]).compile(data["scripts"])


def referenced_types(block_data: dict) -> set:
    found = set()

    def visit(block):
        while isinstance(block, dict):
            found.add(block.get("type"))
            for value in (block.get("inputs") or {}).values():
                if isinstance(value, dict):
                    visit(value)
            for sub in (block.get("substacks") or {}).values():
                visit(sub)
            block = block.get("next")

    for script in (block_data or {}).get("scripts") or []:
        visit(script)
    return found
