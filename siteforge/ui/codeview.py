"""Code tab: HTML, CSS and JavaScript editors with syntax highlighting."""

from __future__ import annotations

from gi.repository import Adw, GLib, GObject, Gtk, GtkSource

from .. import formatter

PANES = (
    # key, title, GtkSourceView language id, formatter
    ("html", "HTML", "html", formatter.format_html),
    ("css", "CSS", "css", formatter.format_css),
    ("js", "JavaScript", "js", formatter.format_js),
)

EDIT_DELAY_MS = 300


class CodePane(Gtk.Box):
    __gsignals__ = {
        # part (html/css/js), new text
        "code-changed": (GObject.SignalFlags.RUN_FIRST, None, (str, str)),
    }

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.buffers: dict[str, GtkSource.Buffer] = {}
        self.views: dict[str, GtkSource.View] = {}
        self._pending: dict[str, int] = {}
        self._updating = False
        lang_manager = GtkSource.LanguageManager.get_default()

        outer = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, wide_handle=True,
                          vexpand=True, shrink_start_child=False, shrink_end_child=False)
        inner = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, wide_handle=True,
                          shrink_start_child=False, shrink_end_child=False)
        columns = []
        for key, title, lang_id, fmt in PANES:
            buffer = GtkSource.Buffer(language=lang_manager.get_language(lang_id),
                                      highlight_syntax=True, highlight_matching_brackets=True)
            buffer.connect("changed", self._on_buffer_changed, key)
            view = GtkSource.View(
                buffer=buffer, monospace=True, show_line_numbers=True, auto_indent=True,
                indent_on_tab=True, tab_width=2, indent_width=2, insert_spaces_instead_of_tabs=True,
                highlight_current_line=True, smart_backspace=True, vexpand=True, hexpand=True,
                top_margin=6, bottom_margin=6, left_margin=4)
            view.update_property([Gtk.AccessibleProperty.LABEL], [f"{title} source"])
            self.buffers[key] = buffer
            self.views[key] = view

            header = Gtk.Box(spacing=6)
            header.add_css_class("code-pane-header")
            label = Gtk.Label(label=title, xalign=0, hexpand=True)
            label.add_css_class("heading")
            header.append(label)
            button = Gtk.Button(icon_name="format-justify-left-symbolic",
                                tooltip_text=f"Format {title}")
            button.add_css_class("flat")
            button.update_property([Gtk.AccessibleProperty.LABEL], [f"Format {title}"])
            button.connect("clicked", self._on_format, key, fmt)
            header.append(button)

            scroller = Gtk.ScrolledWindow(child=view, vexpand=True, hexpand=True)
            column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, width_request=220)
            column.append(header)
            column.append(Gtk.Separator())
            column.append(scroller)
            columns.append(column)

        inner.set_start_child(columns[1])
        inner.set_end_child(columns[2])
        outer.set_start_child(columns[0])
        outer.set_end_child(inner)
        self.append(outer)
        self._paned = (outer, inner)
        self.connect("map", lambda *_: GLib.idle_add(self._balance_panes))

        style_manager = Adw.StyleManager.get_default()
        style_manager.connect("notify::dark", lambda *_: self._apply_scheme())
        self._apply_scheme()

    def _balance_panes(self, *_args):
        outer, inner = self._paned
        width = self.get_width()
        if width > 0 and not outer.get_property("position-set"):
            outer.set_position(width // 3)
            inner.set_position(width // 3)
        return GLib.SOURCE_REMOVE

    def _apply_scheme(self):
        dark = Adw.StyleManager.get_default().get_dark()
        schemes = GtkSource.StyleSchemeManager.get_default()
        scheme = schemes.get_scheme("Adwaita-dark" if dark else "Adwaita") or \
            schemes.get_scheme("classic-dark" if dark else "classic")
        for buffer in self.buffers.values():
            buffer.set_style_scheme(scheme)

    def load_text(self, key: str, text: str):
        """Replace the text without an undo step (opening a project)."""
        self._updating = True
        try:
            buffer = self.buffers[key]
            buffer.begin_irreversible_action()
            buffer.set_text(text)
            buffer.end_irreversible_action()
            buffer.place_cursor(buffer.get_start_iter())
        finally:
            self._updating = False

    def set_text(self, key: str, text: str):
        buffer = self.buffers[key]
        if buffer.get_property("text") == text:
            return
        self._updating = True
        try:
            # Keep the cursor roughly where it was when the model changes.
            offset = buffer.get_iter_at_mark(buffer.get_insert()).get_offset()
            _replace_text(buffer, text)
            buffer.place_cursor(buffer.get_iter_at_offset(min(offset, len(text))))
        finally:
            self._updating = False

    def text(self, key: str) -> str:
        return self.buffers[key].get_property("text")

    def flush(self):
        """Emit pending edits immediately (before save/export)."""
        for key, source_id in list(self._pending.items()):
            GLib.source_remove(source_id)
            self._emit(key)

    def focus(self, key: str):
        self.views[key].grab_focus()

    def _on_buffer_changed(self, _buffer, key):
        if self._updating:
            return
        if key in self._pending:
            GLib.source_remove(self._pending[key])
        self._pending[key] = GLib.timeout_add(EDIT_DELAY_MS, self._emit, key)

    def _emit(self, key):
        self._pending.pop(key, None)
        self.emit("code-changed", key, self.text(key))
        return GLib.SOURCE_REMOVE

    def _on_format(self, _button, key, fmt):
        text = self.text(key)
        if not text.strip():
            return
        try:
            formatted = fmt(text)
        except Exception as err:  # noqa: BLE001 - never lose user code to a formatter bug
            root = self.get_root()
            if hasattr(root, "toast"):
                root.toast(f"Could not format: {err}")
            return
        _replace_text(self.buffers[key], formatted)


def _replace_text(buffer, text):
    """Replace the whole buffer as one undoable step (set_text() clears undo)."""
    buffer.begin_user_action()
    start, end = buffer.get_bounds()
    buffer.delete(start, end)
    buffer.insert(buffer.get_start_iter(), text)
    buffer.end_user_action()
