"""Left sidebar: draggable component palette and the page outline."""

from __future__ import annotations

from gi.repository import Gdk, GObject, Gtk

from .. import components
from .preview import ComponentDrag


class Sidebar(Gtk.Box):
    __gsignals__ = {
        # component key — inserted after the selection (keyboard / double click)
        "insert-component": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "element-activated": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, width_request=200)
        self.add_css_class("siteforge-sidebar")
        self._syncing = False

        self.append(self._heading("Components"))
        palette = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        palette.add_css_class("navigation-sidebar")
        palette.update_property([Gtk.AccessibleProperty.LABEL], ["Component palette"])
        palette.connect("row-activated", lambda _lb, row: self.emit("insert-component", row.key))
        for comp in components.COMPONENTS:
            palette.append(self._palette_row(comp))
        self.append(palette)

        self.append(Gtk.Separator(margin_top=6))
        self.append(self._heading("Page Outline"))
        self.outline = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.outline.add_css_class("navigation-sidebar")
        self.outline.update_property([Gtk.AccessibleProperty.LABEL], ["Page outline"])
        self.outline.connect("row-selected", self._on_outline_selected)
        empty = Gtk.Label(label="No components yet", margin_top=12)
        empty.add_css_class("dim-label")
        self.outline.set_placeholder(empty)
        self.append(Gtk.ScrolledWindow(child=self.outline, vexpand=True,
                                       hscrollbar_policy=Gtk.PolicyType.NEVER))

    def _heading(self, text):
        label = Gtk.Label(label=text, xalign=0, margin_start=12, margin_top=10, margin_bottom=4)
        label.add_css_class("heading")
        label.add_css_class("dim-label")
        return label

    def _palette_row(self, comp):
        row = Gtk.ListBoxRow()
        row.key = comp.key
        row.set_tooltip_text(f"{comp.description}. Drag onto the preview, or press Enter to "
                             "insert after the selected element.")
        row.update_property([Gtk.AccessibleProperty.LABEL], [f"{comp.title} component"])
        row.update_property([Gtk.AccessibleProperty.DESCRIPTION], [comp.description])
        box = Gtk.Box(spacing=10)
        box.add_css_class("palette-card")
        box.append(Gtk.Image(icon_name=comp.icon))
        box.append(Gtk.Label(label=comp.title, xalign=0, hexpand=True))
        handle = Gtk.Image(icon_name="list-drag-handle-symbolic")
        handle.add_css_class("dim-label")
        box.append(handle)
        row.set_child(box)

        source = Gtk.DragSource(actions=Gdk.DragAction.COPY)
        source.connect("prepare", self._on_drag_prepare, comp.key)
        source.connect("drag-begin", self._on_drag_begin, comp)
        row.add_controller(source)
        return row

    def _on_drag_prepare(self, _source, _x, _y, key):
        value = GObject.Value(ComponentDrag.__gtype__, ComponentDrag(key))
        return Gdk.ContentProvider.new_for_value(value)

    def _on_drag_begin(self, source, _drag, comp):
        icon = Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).lookup_icon(
            comp.icon, None, 32, 1, Gtk.TextDirection.NONE, 0)
        source.set_icon(icon, 16, 16)

    # -- outline ---------------------------------------------------------
    def set_elements(self, elements, selected_id):
        self._syncing = True
        try:
            while (row := self.outline.get_row_at_index(0)) is not None:
                self.outline.remove(row)
            for sf_id, comp, props, depth in elements:
                row = Gtk.ListBoxRow()
                row.sf_id = sf_id
                label = props.get("id") or sf_id
                box = Gtk.Box(spacing=8, margin_start=depth * 14)
                box.append(Gtk.Image(icon_name=comp.icon))
                text = Gtk.Label(label=comp.title, xalign=0)
                box.append(text)
                ident = Gtk.Label(label=f"#{label}", xalign=0, hexpand=True,
                                  ellipsize=3)  # Pango.EllipsizeMode.END
                ident.add_css_class("dim-label")
                ident.add_css_class("caption")
                box.append(ident)
                row.set_child(box)
                row.update_property([Gtk.AccessibleProperty.LABEL], [f"{comp.title} {label}"])
                self.outline.append(row)
                if sf_id == selected_id:
                    self.outline.select_row(row)
        finally:
            self._syncing = False

    def select(self, sf_id):
        self._syncing = True
        try:
            index = 0
            while (row := self.outline.get_row_at_index(index)) is not None:
                if row.sf_id == sf_id:
                    self.outline.select_row(row)
                    break
                index += 1
            else:
                self.outline.unselect_all()
        finally:
            self._syncing = False

    def _on_outline_selected(self, _listbox, row):
        if not self._syncing and row is not None:
            self.emit("element-activated", row.sf_id)
