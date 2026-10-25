"""Right-hand properties panel for the selected element (or the page)."""

from __future__ import annotations

from gi.repository import Adw, GLib, GObject, Gtk

APPLY_DELAY_MS = 400


class PropertiesPanel(Gtk.Box):
    __gsignals__ = {
        # sf_id, prop name, value (as Python object)
        "prop-changed": (GObject.SignalFlags.RUN_FIRST, None, (str, str, object)),
        "page-changed": (GObject.SignalFlags.RUN_FIRST, None, (str, str)),
        "move": (GObject.SignalFlags.RUN_FIRST, None, (str, int)),
        "delete": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        # sf_id, prop name — open a file chooser for an image
        "choose-image": (GObject.SignalFlags.RUN_FIRST, None, (str, str)),
    }

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, width_request=300)
        self.add_css_class("properties-panel")
        self.sf_id = None
        self._timers = {}
        self.page = Adw.PreferencesPage()
        self.append(self.page)
        self._groups = []

    def _clear(self):
        for source in self._timers.values():
            GLib.source_remove(source)
        self._timers.clear()
        for group in self._groups:
            self.page.remove(group)
        self._groups = []

    def _add_group(self, group):
        self.page.add(group)
        self._groups.append(group)

    def show_page_settings(self, title: str, lang: str, element_count: int):
        self._clear()
        self.sf_id = None
        group = Adw.PreferencesGroup(
            title="Page", description="Click an element in the preview or the outline to edit it.")
        title_row = Adw.EntryRow(title="Page title", text=title)
        title_row.connect("changed", lambda r: self._debounce(
            "title", lambda: self.emit("page-changed", "title", r.get_text())))
        group.add(title_row)
        lang_row = Adw.EntryRow(title="Language code", text=lang)
        lang_row.connect("changed", lambda r: self._debounce(
            "lang", lambda: self.emit("page-changed", "lang", r.get_text().strip() or "en")))
        group.add(lang_row)
        info = Adw.ActionRow(title="Components", subtitle=str(element_count))
        group.add(info)
        self._add_group(group)

    def show_element(self, sf_id: str, comp, props: dict):
        self._clear()
        self.sf_id = sf_id
        group = Adw.PreferencesGroup(title=comp.title, description=comp.description)
        for prop in comp.props:
            group.add(self._row_for(sf_id, prop, props.get(prop.name, prop.default)))
        self._add_group(group)

        actions = Adw.PreferencesGroup(title="Arrange")
        box = Gtk.Box(spacing=6, homogeneous=True)
        for icon, label, direction in (("go-up-symbolic", "Move Up", -1),
                                       ("go-down-symbolic", "Move Down", 1)):
            button = Gtk.Button()
            button.set_child(Adw.ButtonContent(icon_name=icon, label=label))
            button.connect("clicked", lambda _b, d=direction: self.emit("move", sf_id, d))
            box.append(button)
        actions.add(box)
        delete = Gtk.Button(margin_top=12)
        delete.set_child(Adw.ButtonContent(icon_name="user-trash-symbolic", label="Delete Element"))
        delete.add_css_class("destructive-action")
        delete.connect("clicked", lambda *_: self.emit("delete", sf_id))
        actions.add(delete)
        self._add_group(actions)

    def _debounce(self, key, fn):
        if key in self._timers:
            GLib.source_remove(self._timers[key])

        def fire():
            self._timers.pop(key, None)
            fn()
            return GLib.SOURCE_REMOVE

        self._timers[key] = GLib.timeout_add(APPLY_DELAY_MS, fire)

    def flush(self):
        for key in list(self._timers):
            GLib.source_remove(self._timers[key])
        self._timers.clear()

    def _emit_later(self, sf_id, name, value_fn):
        self._debounce(name, lambda: self.emit("prop-changed", sf_id, name, value_fn()))

    def _row_for(self, sf_id, prop, value):
        if prop.kind == "int":
            adjustment = Gtk.Adjustment(lower=prop.minimum, upper=prop.maximum, step_increment=1,
                                        page_increment=10, value=int(value or 0))
            row = Adw.SpinRow(title=prop.label, adjustment=adjustment, digits=0)
            row.connect("notify::value", lambda r, _p: self._emit_later(
                sf_id, prop.name, lambda: int(r.get_value())))
            return row
        if prop.kind == "choice":
            model = Gtk.StringList.new(list(prop.options))
            row = Adw.ComboRow(title=prop.label, model=model)
            if value in prop.options:
                row.set_selected(list(prop.options).index(value))
            row.connect("notify::selected", lambda r, _p: self.emit(
                "prop-changed", sf_id, prop.name, prop.options[r.get_selected()]))
            return row
        if prop.kind == "flags":
            row = Adw.ExpanderRow(title=prop.label, subtitle=" ".join(value) or "fully sandboxed")
            current = set(value or [])
            for flag in prop.options:
                switch = Adw.SwitchRow(title=flag, active=flag in current)

                def toggled(sw, _p, flag=flag):
                    if sw.get_active():
                        current.add(flag)
                    else:
                        current.discard(flag)
                    flags = [f for f in prop.options if f in current]
                    row.set_subtitle(" ".join(flags) or "fully sandboxed")
                    self.emit("prop-changed", sf_id, prop.name, flags)

                switch.connect("notify::active", toggled)
                row.add_row(switch)
            return row
        if prop.kind == "multiline":
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=12)
            label = Gtk.Label(label=prop.label, xalign=0)
            label.add_css_class("heading")
            box.append(label)
            view = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, monospace=True,
                                top_margin=8, bottom_margin=8, left_margin=8, right_margin=8)
            view.update_property([Gtk.AccessibleProperty.LABEL], [prop.label])
            view.get_buffer().set_text(str(value))
            view.get_buffer().connect("changed", lambda b: self._emit_later(
                sf_id, prop.name, lambda: b.get_text(b.get_start_iter(), b.get_end_iter(), False)))
            frame = Gtk.Frame(child=Gtk.ScrolledWindow(child=view, min_content_height=90,
                                                       propagate_natural_height=True,
                                                       max_content_height=240))
            frame.add_css_class("view")
            box.append(frame)
            hint = Gtk.Label(label="HTML is allowed here.", xalign=0)
            hint.add_css_class("caption")
            hint.add_css_class("dim-label")
            box.append(hint)
            return box
        row = Adw.EntryRow(title=prop.label, text=str(value))
        row.connect("changed", lambda r: self._emit_later(sf_id, prop.name, r.get_text))
        if prop.kind == "image":
            row.set_title("Image URL or project asset")
            choose = Gtk.Button(icon_name="document-open-symbolic", valign=Gtk.Align.CENTER,
                                tooltip_text="Choose an image file…")
            choose.add_css_class("flat")
            choose.update_property([Gtk.AccessibleProperty.LABEL], ["Choose an image file"])
            choose.connect("clicked", lambda *_: self.emit("choose-image", sf_id, prop.name))
            row.add_suffix(choose)
        return row
