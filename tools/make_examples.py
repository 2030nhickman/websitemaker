#!/usr/bin/env python3
"""Regenerate the example projects in examples/ (run from the repo root)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from siteforge.project import Project  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "examples")


def b(block_type, inputs=None, next_block=None, **substacks):
    block = {"type": block_type, "inputs": inputs or {}, "next": next_block}
    if substacks:
        block["substacks"] = substacks
    return block


def chain(*blocks):
    """Link blocks into a stack and return the first one."""
    for first, second in zip(blocks, blocks[1:]):
        first["next"] = second
    return blocks[0]


def _bread_png():
    """A small drawn illustration so the example ships a real image asset."""
    import base64
    import io
    import math

    import cairo

    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 320, 200)
    cr = cairo.Context(surface)
    cr.set_source_rgb(0.99, 0.93, 0.83)
    cr.paint()
    cr.set_source_rgb(0.62, 0.42, 0.25)  # board
    cr.rectangle(30, 140, 260, 22)
    cr.fill()
    cr.save()  # loaf
    cr.translate(160, 110)
    cr.scale(110, 45)
    cr.arc(0, 0, 1, math.pi, 2 * math.pi)
    cr.restore()
    cr.close_path()
    cr.set_source_rgb(0.83, 0.55, 0.26)
    cr.fill()
    cr.set_source_rgb(0.95, 0.78, 0.5)
    cr.set_line_width(5)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    for x in (110, 150, 190):
        cr.move_to(x, 95)
        cr.line_to(x + 22, 80)
        cr.stroke()
    buf = io.BytesIO()
    surface.write_to_png(buf)
    return base64.b64encode(buf.getvalue()).decode()


def simple_page():
    p = Project()
    p.title = "Bakery on Main"
    p.html = """<header data-sf-id="sf-1" id="header1" class="hero">
  <h1>Bakery on Main</h1>
  <p>Fresh bread every morning since 1998.</p>
</header>
<img data-sf-id="sf-12" id="image1" src="assets/bakery.png" alt="Loaf of bread on a board" width="320" height="200">
<h2 data-sf-id="sf-2" id="text1">Opening hours</h2>
<p data-sf-id="sf-3" id="text2">Monday to Saturday, 7:00 – 18:00. Closed on Sundays.</p>
<h2 data-sf-id="sf-4" id="text3">Find us</h2>
<iframe data-sf-id="sf-5" id="frame1" src="https://www.openstreetmap.org/export/embed.html?bbox=-0.13,51.50,-0.12,51.51" title="Map of the bakery" width="480" height="300" sandbox="allow-scripts allow-same-origin"></iframe>
<h2 data-sf-id="sf-6" id="text4">Pre-order a cake</h2>
<input type="text" data-sf-id="sf-7" id="input1" name="name" placeholder="Your name" aria-label="Your name">
<textarea data-sf-id="sf-8" id="input2" name="order" placeholder="Which cake, and when?" aria-label="Order details" rows="4"></textarea>
<button type="button" data-sf-id="sf-9" id="button1" class="primary" onclick="sendOrder()">Send order</button>
<p data-sf-id="sf-10" id="text5" class="note"></p>
<footer data-sf-id="sf-11" id="footer1"><p>&copy; 2026 Bakery on Main · 12 Main Street</p></footer>
"""
    p.css = """body {
  font-family: Georgia, serif;
  margin: 0 auto;
  max-width: 760px;
  padding: 0 24px 24px;
  color: #3d2b1f;
  background: #fffaf3;
}

.hero {
  text-align: center;
  padding: 48px 16px;
  margin: 0 -24px 24px;
  background: linear-gradient(135deg, #f6d5a8, #e8a87c);
}

.hero h1 {
  margin: 0;
  font-size: 2.6em;
}

input, textarea {
  display: block;
  width: 100%;
  box-sizing: border-box;
  margin: 8px 0;
  padding: 10px;
  border: 1px solid #d9c3a5;
  border-radius: 8px;
  font: inherit;
}

button.primary {
  background: #8b4513;
  color: white;
  border: 0;
  border-radius: 8px;
  padding: 10px 20px;
  font: inherit;
  cursor: pointer;
}

img, iframe {
  max-width: 100%;
  border: 0;
  border-radius: 12px;
}

footer {
  margin-top: 48px;
  border-top: 1px solid #e6d5bf;
  color: #7a6250;
  font-size: 0.9em;
}
"""
    p.assets["bakery.png"] = {"mime": "image/png", "data": _bread_png()}
    p.js = """function sendOrder() {
  const name = document.getElementById('input1').value.trim();
  const note = document.getElementById('text5');
  note.textContent = name ? 'Thanks, ' + name + '! We will call you to confirm.'
                          : 'Please enter your name first.';
}
"""
    return p


def stripe_checkout():
    p = Project()
    p.title = "Handmade Mug — Checkout"
    p.html = """<header data-sf-id="sf-1" id="header1"><h1>Clay &amp; Co.</h1></header>
<div class="product">
  <canvas data-sf-id="sf-2" id="canvas1" width="220" height="220"></canvas>
  <div>
    <h2 data-sf-id="sf-3" id="text1">Handmade stoneware mug</h2>
    <p data-sf-id="sf-4" id="text2">Wheel-thrown, glazed in speckled oat. 350 ml. Ships in 3 days.</p>
    <p data-sf-id="sf-5" id="price" class="price">€24.00</p>
    <div data-sf-id="sf-6" id="checkout1" class="sf-stripe" data-sf-stripe>
      <button type="button" class="sf-stripe-button">Buy now</button>
      <form class="sf-stripe-form" hidden>
        <input type="hidden" name="price">
        <input type="hidden" name="email">
      </form>
    </div>
    <button type="button" data-sf-id="sf-7" id="gift" class="secondary">Buy as a gift</button>
  </div>
</div>
<p data-sf-id="sf-8" id="status" class="status" hidden></p>
<footer data-sf-id="sf-9" id="footer1"><p>Payments are processed securely by Stripe.</p></footer>
"""
    p.css = """body {
  font-family: system-ui, sans-serif;
  margin: 0 auto;
  max-width: 820px;
  padding: 24px;
  color: #1f2328;
}

.product {
  display: flex;
  gap: 32px;
  align-items: center;
  flex-wrap: wrap;
}

.product canvas {
  border-radius: 16px;
  background: #f4efe8;
}

.price {
  font-size: 1.6em;
  font-weight: 700;
}

.secondary {
  margin-left: 8px;
  padding: 12px 20px;
  border-radius: 8px;
  border: 1px solid #635bff;
  background: transparent;
  color: #635bff;
  font: 600 16px system-ui, sans-serif;
  cursor: pointer;
}

.status {
  padding: 12px 16px;
  border-radius: 8px;
  background: #e7f7ee;
  color: #1a7f4b;
}
"""
    from siteforge.components import STRIPE_CSS
    p.css += "\n" + STRIPE_CSS
    p.stripe.update({
        "publishable_key": "",
        "price_id": "price_1ExampleReplaceMe",
        "success_url": "https://example.com/mug?checkout=success",
        "cancel_url": "https://example.com/mug?checkout=cancel",
        "mode": "payment",
    })
    p.blocks = {
        "variables": [],
        "scripts": [
            dict(chain(
                b("event_page_load"),
                b("looks_draw_circle", {"el": "canvas1", "x": 110, "y": 120, "r": 70,
                                        "color": "#c8a27a"}),
                b("looks_draw_rect", {"el": "canvas1", "x": 70, "y": 60, "w": 80, "h": 120,
                                      "color": "#d9b48f"}),
                b("looks_draw_circle", {"el": "canvas1", "x": 172, "y": 120, "r": 26,
                                        "color": "#b8916a"}),
            ), x=20, y=20),
            dict(chain(
                b("event_click", {"el": "gift"}),
                b("var_log", {"value": "Gift checkout"}),
                b("stripe_checkout"),
            ), x=20, y=260),
            dict(chain(
                b("stripe_success"),
                b("looks_set_text", {"el": "status", "text": "Thank you! Your mug is on its way."}),
                b("looks_show", {"el": "status"}),
                b("looks_hide", {"el": "checkout1"}),
            ), x=380, y=20),
            dict(chain(
                b("stripe_cancel"),
                b("looks_set_text", {"el": "status",
                                     "text": "Checkout was canceled — your cart is still here."}),
                b("looks_bg_color", {"el": "status", "color": "#fdf0e1"}),
                b("looks_text_color", {"el": "status", "color": "#9a5b13"}),
                b("looks_show", {"el": "status"}),
            ), x=380, y=230),
        ],
    }
    return p


def animation():
    p = Project()
    p.title = "Bouncing Ball"
    p.html = """<header data-sf-id="sf-1" id="header1"><h1>Bouncing ball</h1></header>
<p data-sf-id="sf-2" id="text1">Built entirely with visual blocks. Press <kbd>space</kbd> to change colour.</p>
<canvas data-sf-id="sf-3" id="canvas1" width="480" height="270"></canvas>
<p data-sf-id="sf-4" id="score">Bounces: 0</p>
<button type="button" data-sf-id="sf-5" id="start">Start</button>
<button type="button" data-sf-id="sf-6" id="stop">Stop</button>
"""
    p.css = """body {
  font-family: system-ui, sans-serif;
  margin: 0 auto;
  max-width: 560px;
  padding: 24px;
}

canvas {
  display: block;
  background: #1e1e2e;
  border-radius: 12px;
  max-width: 100%;
}

button {
  padding: 8px 18px;
  border-radius: 8px;
  border: 0;
  background: #3584e4;
  color: white;
  font: inherit;
  cursor: pointer;
}
"""
    num = lambda v: b("var_get", {"var": v})  # noqa: E731

    def bounce(axis, limit_hi, speed):
        return b("logic_if", {"cond": b("logic_or", {
            "a": b("logic_compare", {"a": num(axis), "op": ">", "b": limit_hi}),
            "b": b("logic_compare", {"a": num(axis), "op": "<", "b": 20}),
        })}, then=chain(
            b("var_set", {"var": speed, "value": b("op_math", {"a": 0, "op": "−", "b": num(speed)})}),
            b("var_change", {"var": "bounces", "value": 1}),
            b("looks_set_text", {"el": "score",
                                 "text": b("op_join", {"a": "Bounces: ", "b": num("bounces")})}),
        ))

    loop = chain(
        b("logic_if", {"cond": b("logic_compare", {"a": num("running"), "op": "=", "b": 1})},
          then=chain(
              b("looks_clear_canvas", {"el": "canvas1"}),
              b("looks_draw_circle", {"el": "canvas1", "x": num("x"), "y": num("y"), "r": 20,
                                      "color": num("color")}),
              b("var_change", {"var": "x", "value": num("dx")}),
              b("var_change", {"var": "y", "value": num("dy")}),
              bounce("x", 460, "dx"),
              bounce("y", 250, "dy"),
          )),
    )
    p.blocks = {
        "variables": [
            {"name": "x", "initial": 60}, {"name": "y", "initial": 60},
            {"name": "dx", "initial": 4}, {"name": "dy", "initial": 3},
            {"name": "running", "initial": 1}, {"name": "bounces", "initial": 0},
            {"name": "color", "initial": "#f6d32d"},
        ],
        "scripts": [
            dict(chain(b("event_page_load"), b("logic_forever", {}, body=loop)), x=20, y=20),
            dict(chain(b("event_click", {"el": "start"}),
                       b("var_set", {"var": "running", "value": 1})), x=560, y=20),
            dict(chain(b("event_click", {"el": "stop"}),
                       b("var_set", {"var": "running", "value": 0})), x=560, y=150),
            dict(chain(
                b("event_key", {"key": "space"}),
                b("logic_if_else",
                  {"cond": b("logic_compare", {"a": num("color"), "op": "=", "b": "#f6d32d"})},
                  then=b("var_set", {"var": "color", "value": "#ff7800"}),
                  **{"else": b("var_set", {"var": "color", "value": "#f6d32d"})}),
            ), x=560, y=280),
        ],
    }
    return p


def main():
    os.makedirs(OUT, exist_ok=True)
    for name, factory in (("simple-page", simple_page), ("stripe-checkout", stripe_checkout),
                          ("block-animation", animation)):
        project = factory()
        project.set_blocks(project.blocks)
        path = os.path.join(OUT, f"{name}.siteforge")
        project.save(path)
        print(path)


if __name__ == "__main__":
    main()
