#!/usr/bin/env python3
"""End-to-end GUI test. Run under a display, e.g. ``xvfb-run python3 tests/gui_test.py``.

Drives the real window: drops a palette component onto the WebKit preview,
selects it through the in-page bridge, edits a property, snaps blocks
together on the canvas, runs them in the preview and saves/reloads the
project.  Prints one PASS/FAIL line per check; exit code 0 means all passed.
"""

import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GtkSource", "5")
gi.require_version("WebKit", "6.0")
from gi.repository import Adw, GLib, Graphene, GtkSource  # noqa: E402

from siteforge.ui import blockeditor  # noqa: E402
from siteforge.ui.preview import ComponentDrag  # noqa: E402
from siteforge.ui.window import SiteForgeWindow  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}: {name}" + (f" — {detail}" if detail and not ok else ""),
          flush=True)


def wait(predicate, timeout=15.0):
    """Spin the main loop until predicate() is true or the timeout expires."""
    deadline = GLib.get_monotonic_time() + int(timeout * 1e6)
    ctx = GLib.MainContext.default()
    while not predicate():
        if GLib.get_monotonic_time() > deadline:
            return False
        ctx.iteration(True)
    return True


def js(win, code):
    box = {}

    def done(webview, result, _data):
        try:
            value = webview.evaluate_javascript_finish(result)
            box["value"] = value.to_string() if value is not None else None
        except GLib.Error as err:
            box["value"] = f"ERROR {err.message}"

    win.preview.webview.evaluate_javascript(code, -1, None, None, None, done, None)
    wait(lambda: "value" in box)
    return box.get("value")


def settle(ms=400):
    flag = []
    GLib.timeout_add(ms, lambda: flag.append(1) and False)
    wait(lambda: flag)


def page_loaded(win):
    return win.preview._loaded and not win.preview.webview.is_loading()


def drag_canvas(canvas, start, end):
    canvas._on_drag_begin(None, *start)
    dx, dy = end[0] - start[0], end[1] - start[1]
    for i in range(1, 6):
        canvas._on_drag_update(None, dx * i / 5, dy * i / 5)
    canvas._on_drag_end(None, dx, dy)
    canvas.queue_draw()
    settle(150)


def run(app):
    win = SiteForgeWindow(app)
    win.present()
    check("window is mapped", wait(lambda: win.get_mapped() and win.get_width() > 0))
    check("preview loads", wait(lambda: page_loaded(win)))
    settle()
    project = win.project
    start_count = len(project.elements())

    # -- the palette drag source offers the type the preview drop target accepts
    provider = win.sidebar._on_drag_prepare(None, 0, 0, "button")
    check("palette drag offers a component", provider.ref_formats().contain_gtype(
        ComponentDrag.__gtype__))

    # -- drop a Button onto the preview, just below the header -----------
    header = json.loads(js(win, "JSON.stringify(document.querySelector('[data-sf-id=\"sf-1\"]')"
                               ".getBoundingClientRect())"))
    x = header["left"] + 20
    y = header["bottom"] - 2  # lower part of the header → "after"
    _ok, point = win.preview.webview.compute_point(win.preview.overlay, Graphene.Point().init(x, y))
    win.preview._on_drop(None, ComponentDrag("button"), point.x, point.y)
    check("drop inserts a component", wait(lambda: len(project.elements()) == start_count + 1))
    order = [comp.key for _s, comp, _p, depth in project.elements() if depth == 0]
    check("drop lands after the header", order[:2] == ["header", "button"], str(order))
    new_id = win.selected_id
    check("dropped element is selected", bool(new_id) and project.element(new_id) is not None)

    # -- selection through the in-page bridge -----------------------------
    check("preview re-renders", wait(lambda: page_loaded(win)
                                     and win.preview._last_html
                                     and "button1" in win.preview._last_html))
    settle()
    win.select_element(None)
    js(win, "document.getElementById('text1').click(); 'ok'")
    check("clicking in the preview selects the element",
          wait(lambda: win.selected_id == "sf-2"), str(win.selected_id))
    check("properties panel shows the element", win.properties.sf_id == "sf-2")

    # -- property edit round-trips through the HTML -----------------------
    project.update_element(new_id, {"label": "Pay me", "class": "cta"}, origin="props")
    check("property edit updates HTML", 'class="cta">Pay me</button>' in project.html)
    check("code tab follows property edits", "Pay me" in win.code.text("html"))
    check("preview shows edited label", wait(lambda: page_loaded(win) and js(
        win, "document.getElementById('button1') && document.getElementById('button1').textContent")
        == "Pay me"))

    # -- code tab edits flow back into the element tree -------------------
    html = win.code.text("html").replace("Pay me", "Pay now")
    win.code.buffers["html"].set_text(html)
    win.code.flush()
    check("code edit updates element props",
          project.element(new_id)[2]["label"] == "Pay now", str(project.element(new_id)))

    # -- drag-to-resize handle in the preview ------------------------------
    win.select_element(new_id)
    settle(300)
    size = js(win, """(function () {
      const h = document.getElementById('__sf-resize');
      if (!h || h.style.display !== 'block') return 'no handle';
      const r = h.getBoundingClientRect(), x = r.left + 7, y = r.top + 7;
      const fire = (type, el, dx) => el.dispatchEvent(new MouseEvent(type,
          {bubbles: true, cancelable: true, clientX: x + dx, clientY: y + dx / 2}));
      fire('mousedown', h, 0); fire('mousemove', document, 40); fire('mouseup', document, 40);
      return 'ok';
    })()""")
    check("resize handle shown for the selected button", size == "ok", str(size))
    check("dragging the handle resizes the element", wait(
        lambda: (project.element(new_id) or [None, None, {}])[2].get("width", 0) > 0),
        str(project.element(new_id)))
    check("resize is written as inline style", 'style="width: ' in project.html)

    # -- images: dropped files become project assets -----------------------
    icon = os.path.join(ROOT, "data", "icons", "hicolor", "256x256", "apps", "siteforge.png")
    before = len(project.elements())
    win._on_files_dropped(None, [icon], "sf-1", "after")
    check("dropping an image file adds an Image", len(project.elements()) == before + 1
          and "siteforge.png" in project.assets)
    img_id = win.selected_id
    check("image refers to the project asset", 'src="assets/siteforge.png"' in project.html)
    check("preview shows the embedded image", wait(lambda: page_loaded(win) and js(
        win, "(function(){const i=document.querySelector('img[data-sf-id]');"
             "return i && i.complete && i.naturalWidth > 0 ? 'loaded' : 'no'})()") == "loaded"))
    win.preview.select(img_id)
    settle(300)
    js(win, """(function () {
      const h = document.getElementById('__sf-resize');
      const r = h.getBoundingClientRect(), x = r.left + 7, y = r.top + 7;
      const fire = (type, el, dx) => el.dispatchEvent(new MouseEvent(type,
          {bubbles: true, cancelable: true, clientX: x + dx, clientY: y}));
      fire('mousedown', h, 0); fire('mousemove', document, -100); fire('mouseup', document, -100);
      return 'ok';
    })()""")
    check("image resize keeps aspect ratio", wait(lambda: project.element(img_id)[2]["width"]
                                                  == project.element(img_id)[2]["height"]
                                                  == 220), str(project.element(img_id)[2]))
    project.remove_element(img_id)

    # -- Stripe component + settings injection ----------------------------
    sf_stripe = project.add_component("stripe")
    project.set_stripe({"publishable_key": "pk_test_abc123", "price_id": "price_123abc",
                        "success_url": "https://example.com/?checkout=success",
                        "cancel_url": "https://example.com/?checkout=cancel", "mode": "payment",
                        "customer_email": "", "payment_link": ""})
    check("stripe key is not in the HTML source", "pk_test_abc123" not in project.html)
    exported = project.generate(for_export=True)["index.html"]
    check("stripe config injected at export", "pk_test_abc123" in exported
          and "js.stripe.com/v3" in exported and "redirectToCheckout" in exported)
    check("editor ids stripped on export", "data-sf-id" not in exported)
    project.remove_element(sf_stripe)

    # -- blocks: palette drag, snapping ------------------------------------
    win.stack.set_visible_child_name("blocks")
    canvas = win.blocks.canvas
    settle(300)
    check("properties panel hides on the Blocks tab",
          not win.properties_revealer.get_reveal_child())
    canvas.set_category("events")
    canvas.queue_draw()
    settle(150)
    rect, block_type = canvas._palette[0]
    check("palette lists event blocks", block_type == "event_page_load", block_type)
    drop_x, drop_y = blockeditor.PALETTE_W + 80, 60
    drag_canvas(canvas, (rect[0] + 20, rect[1] + 25), (drop_x + 20, drop_y + 25))
    check("hat block dropped on workspace", len(canvas.data["scripts"]) == 1
          and canvas.data["scripts"][0]["type"] == "event_page_load")

    canvas.set_category("looks")
    settle(150)
    target = next(r for r, t in canvas._palette if t == "looks_set_text")
    hat = canvas.data["scripts"][0]
    hat_geo = canvas._find_geo(hat)
    sx, sy = canvas.to_screen(hat_geo.x, hat_geo.y + hat_geo.h)
    drag_canvas(canvas, (target[0] + 6, target[1] + 6), (sx + 6, sy + 6))
    nxt = canvas.data["scripts"][0].get("next")
    check("stack block snaps under the hat", nxt is not None and nxt["type"] == "looks_set_text",
          json.dumps(canvas.data["scripts"])[:200])
    nxt["inputs"]["el"] = "text1"
    nxt["inputs"]["text"] = "Hello from blocks"
    canvas._changed()
    check("blocks stored in project", project.blocks["scripts"][0]["next"]["type"]
          == "looks_set_text")
    compiled = project.compiled_blocks()
    check("blocks compile to JS", 'sf.setText("text1", "Hello from blocks")' in compiled)

    # dragging a block back to the palette deletes it
    geo = canvas._find_geo(canvas.data["scripts"][0]["next"])
    gx, gy = canvas.to_screen(geo.x + 4, geo.y + 4)
    drag_canvas(canvas, (gx, gy), (40, 400))
    check("dropping on the palette deletes", canvas.data["scripts"][0].get("next") is None)
    canvas.data["scripts"][0]["next"] = nxt
    canvas._changed()

    # -- run in preview ----------------------------------------------------
    win.run_blocks()
    check("run in preview executes compiled blocks", wait(lambda: page_loaded(win) and js(
        win, "document.getElementById('text1').textContent") == "Hello from blocks", 20))

    # -- save / reload -----------------------------------------------------
    path = os.path.join(tempfile.mkdtemp(), "roundtrip.siteforge")
    win._save_to(path)
    with open(path, encoding="utf-8") as fh:
        saved = json.load(fh)
    check("save writes project JSON", saved["format"] == "siteforge" and "elements" in saved)
    check("load restores project", win.load_file(path)
          and win.project.compiled_blocks() == compiled)

    # -- device widths -----------------------------------------------------
    win.stack.set_visible_child_name("preview")
    win.preview.set_device("mobile")
    settle(500)
    check("mobile device width", win.preview.webview.get_width() == 375,
          str(win.preview.webview.get_width()))
    win.preview.set_device("desktop")

    win.project.dirty = False
    win.close()
    app.quit()


def main():
    GtkSource.init()
    app = Adw.Application(application_id="org.siteforge.SiteForge.Test")
    app.connect("activate", lambda a: GLib.idle_add(lambda: run(a) and False))
    app.run([])
    passed = sum(results)
    print(f"\n{passed}/{len(results)} GUI checks passed")
    return 0 if results and all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
