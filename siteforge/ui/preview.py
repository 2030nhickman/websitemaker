"""Live preview: a WebKitGTK view with selection, drop targeting and device sizes."""

from __future__ import annotations

import json

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Graphene, Gtk, WebKit

DEVICE_WIDTHS = {"desktop": None, "tablet": 768, "mobile": 375}

# Injected into the preview only (never exported).  Talks to Python through
# the "siteforge" script message handler.
BRIDGE_JS = r"""
(function () {
  if (window.__sf) return;
  const post = (msg) => {
    try { window.webkit.messageHandlers.siteforge.postMessage(JSON.stringify(msg)); } catch (e) {}
  };
  const css = document.createElement('style');
  css.textContent = `
    .__sf-hover { outline: 2px dashed rgba(53,132,228,.75) !important; outline-offset: 2px; cursor: pointer; }
    .__sf-selected { outline: 2px solid #3584e4 !important; outline-offset: 2px; }
    #__sf-drop { position: absolute; z-index: 2147483647; pointer-events: none; display: none;
                 background: #3584e4; height: 3px; border-radius: 2px; }
    #__sf-drop.inside { background: rgba(53,132,228,.15); border: 2px dashed #3584e4; height: auto; }
    #__sf-resize { position: absolute; z-index: 2147483647; width: 14px; height: 14px;
                   margin: -7px 0 0 -7px; box-sizing: border-box; border-radius: 4px;
                   background: #3584e4; border: 2px solid #fff; box-shadow: 0 0 0 1px rgba(0,0,0,.3);
                   cursor: nwse-resize; display: none; }
    #__sf-size { position: absolute; z-index: 2147483647; pointer-events: none; display: none;
                 font: 600 11px system-ui, sans-serif; color: #fff; background: #3584e4;
                 padding: 2px 6px; border-radius: 6px; }`;
  (document.head || document.documentElement).appendChild(css);
  let hover = null, selected = null, indicator = null;
  // Drag-to-resize: images, canvases, iframes, text boxes and buttons.
  const RESIZABLE = new Set(['IMG', 'CANVAS', 'IFRAME', 'INPUT', 'TEXTAREA', 'BUTTON']);
  const ATTR_SIZED = new Set(['IMG', 'CANVAS', 'IFRAME']);
  const handle = document.createElement('div');
  handle.id = '__sf-resize';
  handle.title = 'Drag to resize (Shift: free aspect ratio for images)';
  const sizeLabel = document.createElement('div');
  sizeLabel.id = '__sf-size';
  let resizing = null, justResized = false;

  function placeHandle() {
    const ok = selected && window.__sf.mode === 'select' && RESIZABLE.has(selected.tagName)
      && document.contains(selected);
    if (!handle.isConnected) document.body.append(handle, sizeLabel);
    handle.style.display = ok ? 'block' : 'none';
    if (!ok) return;
    const r = selected.getBoundingClientRect();
    handle.style.left = (r.right + scrollX) + 'px';
    handle.style.top = (r.bottom + scrollY) + 'px';
  }

  function applySize(el, w, h) {
    if (ATTR_SIZED.has(el.tagName)) {
      el.setAttribute('width', w); el.setAttribute('height', h);
      el.style.width = w + 'px'; el.style.height = h + 'px';  // beat CSS during the drag
    } else {
      el.style.width = w + 'px'; el.style.height = h + 'px';
    }
  }

  handle.addEventListener('mousedown', (e) => {
    if (!selected) return;
    e.preventDefault();
    e.stopPropagation();
    const r = selected.getBoundingClientRect();
    resizing = { x: e.clientX, y: e.clientY, w: r.width, h: r.height,
                 ratio: r.width > 0 ? r.height / r.width : 1 };
  }, true);
  document.addEventListener('mousemove', (e) => {
    if (!resizing || !selected) return;
    e.preventDefault();
    let w = Math.max(8, Math.round(resizing.w + e.clientX - resizing.x));
    let h = Math.max(8, Math.round(resizing.h + e.clientY - resizing.y));
    if (selected.tagName === 'IMG' && !e.shiftKey) h = Math.max(8, Math.round(w * resizing.ratio));
    applySize(selected, w, h);
    resizing.last = [w, h];
    placeHandle();
    sizeLabel.textContent = w + ' × ' + h;
    sizeLabel.style.display = 'block';
    sizeLabel.style.left = (parseFloat(handle.style.left) + 10) + 'px';
    sizeLabel.style.top = (parseFloat(handle.style.top) + 10) + 'px';
  }, true);
  document.addEventListener('mouseup', (e) => {
    if (!resizing) return;
    const last = resizing.last;
    resizing = null;
    sizeLabel.style.display = 'none';
    justResized = true;
    setTimeout(() => { justResized = false; }, 0);
    e.preventDefault();
    e.stopPropagation();
    if (last && selected) post({ type: 'resize', id: selected.dataset.sfId, width: last[0], height: last[1] });
  }, true);
  window.addEventListener('resize', placeHandle);
  new ResizeObserver(placeHandle).observe(document.documentElement);
  const sfOf = (node) => (node && node.closest) ? node.closest('[data-sf-id]') : null;
  const CONTAINERS = new Set(['HEADER', 'FOOTER', 'DIV', 'SECTION', 'MAIN', 'NAV', 'ARTICLE', 'ASIDE']);

  function target(x, y) {
    let el = sfOf(document.elementFromPoint(x, y));
    if (!el) {
      const all = Array.from(document.querySelectorAll('[data-sf-id]'))
        .filter((e) => !sfOf(e.parentElement));
      let best = null, bestDist = Infinity;
      for (const e of all) {
        const r = e.getBoundingClientRect();
        const d = y < r.top ? r.top - y : (y > r.bottom ? y - r.bottom : 0);
        if (d < bestDist) { best = e; bestDist = d; }
      }
      if (!best) return { id: null, where: 'end', rect: null };
      const r = best.getBoundingClientRect();
      return { id: best.dataset.sfId, where: y < r.top + r.height / 2 ? 'before' : 'after', rect: r };
    }
    const r = el.getBoundingClientRect();
    const container = CONTAINERS.has(el.tagName) && !el.hasAttribute('data-sf-stripe');
    const rel = (y - r.top) / Math.max(1, r.height);
    let where = rel < 0.5 ? 'before' : 'after';
    if (container && rel > 0.25 && rel < 0.75) where = 'inside';
    return { id: el.dataset.sfId, where: where, rect: r };
  }

  window.__sf = {
    mode: '__MODE__',
    setMode(mode) {
      this.mode = mode;
      if (hover) hover.classList.remove('__sf-hover');
      hover = null;
      placeHandle();
    },
    select(id) {
      if (selected) selected.classList.remove('__sf-selected');
      selected = id ? document.querySelector('[data-sf-id="' + CSS.escape(id) + '"]') : null;
      if (selected) {
        selected.classList.add('__sf-selected');
        const r = selected.getBoundingClientRect();
        if (r.bottom < 0 || r.top > innerHeight) selected.scrollIntoView({ block: 'center' });
      }
      placeHandle();
    },
    dropTarget(x, y) {
      const t = target(x, y);
      return JSON.stringify({ id: t.id, where: t.where });
    },
    dragOver(x, y) {
      if (!indicator) {
        indicator = document.createElement('div');
        indicator.id = '__sf-drop';
        document.body.appendChild(indicator);
      }
      const t = target(x, y);
      indicator.className = t.where === 'inside' ? 'inside' : '';
      let r = t.rect;
      if (!r) {
        const b = document.body.getBoundingClientRect();
        r = { left: b.left + 8, width: Math.max(40, b.width - 16), top: b.bottom, bottom: b.bottom, height: 0 };
      }
      indicator.style.display = 'block';
      indicator.style.left = (r.left + scrollX) + 'px';
      indicator.style.width = r.width + 'px';
      if (t.where === 'inside') {
        indicator.style.top = (r.top + scrollY) + 'px';
        indicator.style.height = r.height + 'px';
      } else {
        const yy = t.where === 'before' ? r.top - 4 : r.bottom + 2;
        indicator.style.top = (yy + scrollY) + 'px';
        indicator.style.height = '';
      }
    },
    dragLeave() { if (indicator) indicator.style.display = 'none'; },
  };

  document.addEventListener('mouseover', (e) => {
    if (window.__sf.mode !== 'select') return;
    const el = sfOf(e.target);
    if (hover && hover !== el) hover.classList.remove('__sf-hover');
    hover = el;
    if (el) el.classList.add('__sf-hover');
  }, true);
  document.addEventListener('click', (e) => {
    if (window.__sf.mode !== 'select') return;
    e.preventDefault();
    e.stopPropagation();
    if (e.target === handle || justResized) return;
    const el = sfOf(e.target);
    const id = el ? el.dataset.sfId : null;
    window.__sf.select(id);
    post({ type: 'select', id: id });
  }, true);
  document.addEventListener('submit', (e) => {
    if (window.__sf.mode === 'select') e.preventDefault();
  }, true);
  window.addEventListener('error', (e) => post({ type: 'error', message: String(e.message) }));
})();
"""


class ComponentDrag(GObject.Object):
    """Value carried by palette drags (a custom GType so WebKit ignores it)."""

    __gtype_name__ = "SiteForgeComponentDrag"

    def __init__(self, key: str):
        super().__init__()
        self.key = key


class PreviewPane(Gtk.Box):
    __gsignals__ = {
        # sf_id or "" when clicking empty space
        "element-selected": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        # component key, target sf_id ("" = none), where
        "component-dropped": (GObject.SignalFlags.RUN_FIRST, None, (str, str, str)),
        # list of file paths, target sf_id ("" = none), where
        "files-dropped": (GObject.SignalFlags.RUN_FIRST, None, (object, str, str)),
        "zoom-changed": (GObject.SignalFlags.RUN_FIRST, None, (float,)),
        # sf_id, width, height — from the resize handle
        "element-resized": (GObject.SignalFlags.RUN_FIRST, None, (str, int, int)),
        "open-in-browser": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "page-error": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.mode = "select"
        self.device = "desktop"
        self.selected_id: str | None = None
        self._scroll = (0, 0)
        self._after_load_js: str | None = None
        self._loaded = False
        self._last_html = None

        self.manager = WebKit.UserContentManager()
        self.manager.register_script_message_handler("siteforge", None)
        self.manager.connect("script-message-received::siteforge", self._on_message)
        self._install_bridge()

        self.webview = WebKit.WebView(user_content_manager=self.manager)
        settings = self.webview.get_settings()
        settings.set_enable_developer_extras(True)
        settings.set_javascript_can_open_windows_automatically(False)
        self.webview.set_vexpand(True)
        self.webview.set_hexpand(True)
        self.webview.update_property([Gtk.AccessibleProperty.LABEL], ["Page preview"])
        self.webview.connect("load-changed", self._on_load_changed)
        self.webview.connect("context-menu", self._on_context_menu)
        self.webview.connect("decide-policy", self._on_decide_policy)
        self.webview.connect("notify::zoom-level",
                             lambda *_: self.emit("zoom-changed", self.webview.get_zoom_level()))

        self.frame = Gtk.Box(hexpand=True, vexpand=True)
        self.frame.add_css_class("preview-device")
        self.frame.set_overflow(Gtk.Overflow.HIDDEN)
        self.frame.append(self.webview)

        # The clamp caps the page width for the tablet / mobile previews.
        self.clamp = Adw.Clamp(child=self.frame, hexpand=True, vexpand=True)
        backdrop = Gtk.Box(hexpand=True, vexpand=True)
        backdrop.add_css_class("preview-backdrop")
        backdrop.append(self.clamp)

        self.overlay = Gtk.Overlay(child=backdrop)
        drop = Gtk.DropTarget.new(ComponentDrag.__gtype__, Gdk.DragAction.COPY)
        drop.connect("drop", self._on_drop)
        drop.connect("motion", self._on_drag_motion)
        drop.connect("leave", lambda *_: self._run_js("window.__sf && __sf.dragLeave()"))
        self.overlay.add_controller(drop)

        # Image files dragged in from Files.  Capture phase so the drop reaches
        # us before WebKit's own file handling (which would navigate away).
        file_drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        file_drop.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        file_drop.connect("drop", self._on_file_drop)
        file_drop.connect("motion", self._on_drag_motion)
        file_drop.connect("leave", lambda *_: self._run_js("window.__sf && __sf.dragLeave()"))
        self.overlay.add_controller(file_drop)

        zoom_scroll = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
        zoom_scroll.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        zoom_scroll.connect("scroll", self._on_scroll)
        self.overlay.add_controller(zoom_scroll)

        self.append(self.overlay)
        self.set_device("desktop")

    # -- public API -----------------------------------------------------
    def show_page(self, html: str, base_uri: str, after_load_js: str | None = None,
                  force: bool = False):
        if html == self._last_html and after_load_js is None and not force:
            return
        self._last_html = html

        def do_load(*_args):
            self._after_load_js = after_load_js
            self._loaded = False
            self.webview.load_html(html, base_uri)

        if self._loaded:
            self.webview.evaluate_javascript(
                "JSON.stringify([window.scrollX, window.scrollY])", -1, None, None, None,
                self._on_scroll_saved, do_load)
        else:
            do_load()

    def set_mode(self, mode: str):
        self.mode = mode
        self._install_bridge()
        self._run_js(f"window.__sf && __sf.setMode({json.dumps(mode)})")

    def select(self, sf_id: str | None):
        self.selected_id = sf_id
        self._run_js(f"window.__sf && __sf.select({json.dumps(sf_id)})")

    def set_device(self, device: str):
        self.device = device
        width = DEVICE_WIDTHS.get(device)
        if width is None:
            self.clamp.set_maximum_size(1_000_000)
            self.clamp.set_tightening_threshold(1_000_000)
            self.frame.remove_css_class("framed")
        else:
            self.clamp.set_maximum_size(width)
            self.clamp.set_tightening_threshold(width)
            self.frame.add_css_class("framed")

    def zoom(self, delta: float | None):
        if delta is None:
            level = 1.0
        else:
            level = min(3.0, max(0.3, round(self.webview.get_zoom_level() + delta, 2)))
        self.webview.set_zoom_level(level)

    def zoom_level(self) -> float:
        return self.webview.get_zoom_level()

    def inspect(self):
        self.webview.get_inspector().show()

    # -- internals ------------------------------------------------------
    def _install_bridge(self):
        self.manager.remove_all_scripts()
        script = WebKit.UserScript.new(
            BRIDGE_JS.replace("__MODE__", self.mode),
            WebKit.UserContentInjectedFrames.TOP_FRAME,
            WebKit.UserScriptInjectionTime.END, None, None)
        self.manager.add_script(script)

    def _run_js(self, code: str, callback=None, user_data=None):
        if not self._loaded:
            return
        self.webview.evaluate_javascript(code, -1, None, None, None, callback, user_data)

    def _on_scroll_saved(self, webview, result, do_load):
        try:
            value = webview.evaluate_javascript_finish(result)
            self._scroll = tuple(json.loads(value.to_string()))
        except (GLib.Error, ValueError, TypeError):
            self._scroll = (0, 0)
        do_load()

    def _on_load_changed(self, webview, event):
        if event != WebKit.LoadEvent.FINISHED:
            return
        self._loaded = True
        x, y = self._scroll
        code = f"window.scrollTo({float(x)}, {float(y)});"
        if self.selected_id:
            code += f"window.__sf && __sf.select({json.dumps(self.selected_id)});"
        self._run_js(code)
        if self._after_load_js:
            js, self._after_load_js = self._after_load_js, None
            self._run_js(js, self._on_injected_done)

    def _on_injected_done(self, webview, result, _data=None):
        try:
            webview.evaluate_javascript_finish(result)
        except GLib.Error as err:
            self.emit("page-error", err.message)

    def _on_message(self, _manager, value):
        try:
            message = json.loads(value.to_string())
        except (ValueError, TypeError):
            return
        if message.get("type") == "select":
            self.selected_id = message.get("id")
            self.emit("element-selected", message.get("id") or "")
        elif message.get("type") == "resize" and message.get("id"):
            try:
                width, height = int(message["width"]), int(message["height"])
            except (KeyError, TypeError, ValueError):
                return
            self.emit("element-resized", str(message["id"]), width, height)
        elif message.get("type") == "error":
            self.emit("page-error", str(message.get("message", "")))

    def _page_point(self, x: float, y: float):
        ok, point = self.overlay.compute_point(self.webview, Graphene.Point().init(x, y))
        if not ok:
            return None
        zoom = self.webview.get_zoom_level() or 1.0
        return point.x / zoom, point.y / zoom

    def _on_drag_motion(self, _target, x, y):
        pt = self._page_point(x, y)
        if pt:
            self._run_js(f"window.__sf && __sf.dragOver({pt[0]:.1f}, {pt[1]:.1f})")
        return Gdk.DragAction.COPY

    def _on_drop(self, _target, value, x, y):
        if not isinstance(value, ComponentDrag):
            return False
        key = value.key
        pt = self._page_point(x, y)
        if pt is None or not self._loaded:
            self.emit("component-dropped", key, "", "end")
            return True
        code = f"window.__sf ? __sf.dropTarget({pt[0]:.1f}, {pt[1]:.1f}) : null; "
        self._run_js("window.__sf && __sf.dragLeave()")
        self.webview.evaluate_javascript(code, -1, None, None, None, self._on_drop_target, key)
        return True

    def _on_file_drop(self, _target, value, x, y):
        paths = [f.get_path() for f in value.get_files() if f.get_path()]
        if not paths:
            return False
        self._run_js("window.__sf && __sf.dragLeave()")
        pt = self._page_point(x, y)
        if pt is None or not self._loaded:
            self.emit("files-dropped", paths, "", "end")
            return True
        code = f"window.__sf ? __sf.dropTarget({pt[0]:.1f}, {pt[1]:.1f}) : null; "
        self.webview.evaluate_javascript(code, -1, None, None, None, self._on_drop_target,
                                         ("files", paths))
        return True

    def _on_drop_target(self, webview, result, key):
        target_id, where = "", "end"
        try:
            value = webview.evaluate_javascript_finish(result)
            if value is not None and value.is_string():
                data = json.loads(value.to_string())
                target_id = data.get("id") or ""
                where = data.get("where") or "after"
        except (GLib.Error, ValueError):
            pass
        if isinstance(key, tuple):
            self.emit("files-dropped", key[1], target_id, where)
        else:
            self.emit("component-dropped", key, target_id, where)

    def _on_scroll(self, controller, _dx, dy):
        state = controller.get_current_event_state()
        if state & Gdk.ModifierType.CONTROL_MASK:
            self.zoom(-0.1 if dy > 0 else 0.1)
            return True
        return False

    def _on_context_menu(self, _webview, menu, _hit):
        action = Gio.SimpleAction.new("sf-open-in-browser", None)
        action.connect("activate", lambda *_: self.emit("open-in-browser"))
        menu.append(WebKit.ContextMenuItem.new_separator())
        menu.append(WebKit.ContextMenuItem.new_from_gaction(action, "Open in System Browser", None))
        return False

    def _on_decide_policy(self, _webview, decision, decision_type):
        # Keep the preview on the generated page: real navigations (links,
        # Stripe redirects) go to the system browser instead.
        if decision_type == WebKit.PolicyDecisionType.NAVIGATION_ACTION:
            action = decision.get_navigation_action()
            request = action.get_request()
            uri = request.get_uri() or ""
            if action.get_navigation_type() != WebKit.NavigationType.OTHER and \
                    uri.startswith(("http://", "https://")) and action.is_user_gesture():
                decision.ignore()
                Gtk.UriLauncher.new(uri).launch(self.get_root(), None, None, None)
                return True
        elif decision_type == WebKit.PolicyDecisionType.NEW_WINDOW_ACTION:
            decision.ignore()
            return True
        return False
