"""Scratch-like block editor: a Cairo canvas with a palette and a workspace.

The canvas owns a working copy of the project's block data and emits
``changed`` after every edit.  Layout is recomputed on every draw; the
geometry recorded while drawing drives hit testing and snapping.
"""

from __future__ import annotations

import copy
import math

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, GtkSource, Pango, PangoCairo

from .. import blocks as B

PALETTE_W = 260
CAT_ROW_H = 30
CAT_AREA_H = 3 * (CAT_ROW_H + 6) + 12
PAD = 10
GAP = 6
ROW_MIN = 40
HAT_H = 18
ARM = 16
BOTTOM_ARM = 24
MID_ROW = 32
MOUTH_MIN = 24
FIELD_H = 24
SNAP_DIST = 30

TEXT_DARK = (0.34, 0.37, 0.46)


def _rgb(hex_color: str):
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i:i + 2], 16) / 255 for i in (0, 2, 4))


class Geo:
    __slots__ = ("block", "x", "y", "w", "h", "shape", "link", "rects", "mouths")

    def __init__(self, block, x, y, w, h, shape, link):
        self.block, self.x, self.y, self.w, self.h = block, x, y, w, h
        self.shape, self.link = shape, link
        self.rects = [(x, y, w, h)]
        self.mouths = []  # (name, x, y)


class Slot:
    __slots__ = ("block", "name", "spec", "x", "y", "w", "h", "occupied")

    def __init__(self, block, name, spec, x, y, w, h, occupied=False):
        self.block, self.name, self.spec = block, name, spec
        self.x, self.y, self.w, self.h, self.occupied = x, y, w, h, occupied


def _inside(px, py, rect, pad=0):
    x, y, w, h = rect
    return x - pad <= px <= x + w + pad and y - pad <= py <= y + h + pad


class BlockCanvas(Gtk.DrawingArea):
    __gsignals__ = {
        "changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self):
        super().__init__(hexpand=True, vexpand=True, focusable=True, can_focus=True,
                         width_request=PALETTE_W + 200)
        self.update_property([Gtk.AccessibleProperty.LABEL],
                             ["Block workspace. Drag blocks from the palette on the left."])
        self.data = {"scripts": [], "variables": []}
        self.category = "events"
        self.palette_scroll = 0.0
        self.pan = [-20.0, -20.0]
        self.scale = 1.0
        self.selected = None
        self.element_ids = lambda: []
        self._geos: list[Geo] = []
        self._slots: list[Slot] = []
        self._palette: list[tuple] = []  # (rect, block_type)
        self._cats: list[tuple] = []  # (rect, category)
        self._drag = None
        self._press = None
        self._snap = None
        self._font = Pango.FontDescription.from_string("Sans Bold 9.5")
        self._field_font = Pango.FontDescription.from_string("Sans 9.5")

        self.set_draw_func(self._draw)

        drag = Gtk.GestureDrag(button=Gdk.BUTTON_PRIMARY)
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", self._on_drag_end)
        self.add_controller(drag)

        menu_click = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        menu_click.connect("pressed", self._on_context_click)
        self.add_controller(menu_click)

        scroll = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.BOTH_AXES)
        scroll.connect("scroll", self._on_scroll)
        self.add_controller(scroll)

        self._pointer_x = PALETTE_W + 1
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", lambda _c, x, _y: setattr(self, "_pointer_x", x))
        self.add_controller(motion)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)

        self.popover = Gtk.Popover(has_arrow=True)
        self.popover.set_parent(self)
        self.popover.connect("closed", self._on_popover_closed)
        self._editing = None

        self.menu = Gtk.PopoverMenu.new_from_model(self._context_model())
        self.menu.set_parent(self)
        self.menu.set_has_arrow(False)
        group = Gio.SimpleActionGroup()
        for name, handler in (("duplicate", self._act_duplicate), ("delete", self._act_delete),
                              ("cleanup", lambda *_: self.clean_up())):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", handler)
            group.add_action(action)
        self.insert_action_group("blocks", group)
        self._group = group
        self._menu_target = None

        Adw.StyleManager.get_default().connect("notify::dark", lambda *_: self.queue_draw())
        self.connect("unrealize", self._on_unrealize)

    def _on_unrealize(self, *_args):
        self.popover.popdown()
        self.menu.popdown()

    # -- public ----------------------------------------------------------
    def load(self, data: dict):
        self.data = copy.deepcopy(B.sanitize(data))
        self.selected = None
        for i, script in enumerate(self.data["scripts"]):
            script.setdefault("x", 20)
            script.setdefault("y", 20 + i * 120)
        self.queue_draw()

    def export_data(self) -> dict:
        return copy.deepcopy(self.data)

    def set_category(self, category: str):
        self.category = category
        self.palette_scroll = 0
        self.queue_draw()

    def zoom(self, delta: float | None):
        self.scale = 1.0 if delta is None else min(2.0, max(0.5, round(self.scale + delta, 2)))
        self.queue_draw()

    def clean_up(self):
        y = 20
        for script in self.data["scripts"]:
            script["x"], script["y"] = 20, y
            y += self._stack_height(script) + 32
        self.pan = [-20.0, -20.0]
        self._changed()

    def _changed(self):
        self.queue_draw()
        self.emit("changed")

    # -- coordinates ---------------------------------------------------
    def to_world(self, sx, sy):
        return ((sx - PALETTE_W) / self.scale + self.pan[0], sy / self.scale + self.pan[1])

    def to_screen(self, wx, wy):
        return ((wx - self.pan[0]) * self.scale + PALETTE_W, (wy - self.pan[1]) * self.scale)

    # -- measuring -----------------------------------------------------
    def _text_size(self, text, font=None):
        layout = self.create_pango_layout(text)
        layout.set_font_description(font or self._font)
        return layout.get_pixel_size()

    def _value_text(self, spec, value):
        if spec.kind in ("element", "variable") and not value:
            return "(pick)" if spec.kind == "element" else "(variable)"
        return str(value)

    def _items(self, block):
        definition = B.BLOCK_DEFS[block["type"]]
        items = []
        for kind, value in definition.parts():
            if kind == "text":
                w, h = self._text_size(value)
                items.append(("text", value, w, h))
                continue
            spec = definition.inputs[value]
            current = block.get("inputs", {}).get(value)
            if isinstance(current, dict):
                w, h = self._measure(current)
                items.append(("reporter", value, w, h, current, spec))
            elif spec.kind == "boolean":
                items.append(("bool", value, 40, 22, spec))
            else:
                text = self._value_text(spec, current if current is not None else spec.default)
                tw, _ = self._text_size(text, self._field_font)
                w = max(28, tw + (24 if spec.kind in ("element", "variable", "choice") else 14))
                if spec.kind == "color":
                    w = max(w, tw + 30)
                items.append(("field", value, w, FIELD_H, spec, text))
        return items

    def _row_size(self, block, items, shape):
        reporter = shape in (B.REPORTER, B.BOOLEAN)
        pad = (14 if shape == B.BOOLEAN else 10) if reporter else PAD
        width = pad * 2 + sum(i[2] for i in items) + GAP * max(0, len(items) - 1)
        tallest = max((i[3] for i in items), default=16)
        height = max(28 if reporter else ROW_MIN, tallest + (8 if reporter else 14))
        return width, height

    def _measure(self, block):
        """(w, h) of a single block (C-blocks include their substacks)."""
        definition = B.BLOCK_DEFS[block["type"]]
        items = self._items(block)
        w, rh = self._row_size(block, items, definition.shape)
        shape = definition.shape
        if shape in (B.REPORTER, B.BOOLEAN):
            return w, rh
        if shape == B.HAT:
            return max(w, 110), rh + HAT_H
        if shape in (B.C, B.C2):
            h = rh
            for idx, name in enumerate(definition.substacks):
                if idx > 0:
                    h += MID_ROW
                sub = (block.get("substacks") or {}).get(name)
                h += max(MOUTH_MIN, self._stack_height(sub))
            return max(w, 120), h + BOTTOM_ARM
        return max(w, 60), rh

    def _stack_height(self, block):
        return sum(self._measure(b)[1] for b in B.iter_stack(block))

    def _stack_width(self, block):
        return max((self._measure(b)[0] for b in B.iter_stack(block)), default=0)

    # -- drawing -------------------------------------------------------
    def _dark(self):
        return Adw.StyleManager.get_default().get_dark()

    def _draw(self, _area, cr, width, height):
        dark = self._dark()
        bg = (0.14, 0.14, 0.15) if dark else (0.97, 0.97, 0.98)
        dots = (0.26, 0.26, 0.28) if dark else (0.86, 0.86, 0.89)
        cr.set_source_rgb(*bg)
        cr.paint()

        # workspace
        cr.save()
        cr.rectangle(PALETTE_W, 0, width - PALETTE_W, height)
        cr.clip()
        step = 24 * self.scale
        ox = (-self.pan[0] * self.scale) % step
        oy = (-self.pan[1] * self.scale) % step
        cr.set_source_rgb(*dots)
        x = PALETTE_W + ox
        while x < width:
            y = oy
            while y < height:
                cr.rectangle(x, y, 1.6, 1.6)
                y += step
            x += step
        cr.fill()
        cr.translate(PALETTE_W, 0)
        cr.scale(self.scale, self.scale)
        cr.translate(-self.pan[0], -self.pan[1])
        self._geos, self._slots = [], []
        for index, script in enumerate(self.data["scripts"]):
            self._draw_stack(cr, script, script.get("x", 0), script.get("y", 0),
                             ("script", index), record=True)
        if not self.data["scripts"] and not self._drag:
            cr.restore()
            cr.save()
            layout = self.create_pango_layout("Drag blocks here to build a script.\n"
                                              "Start with a yellow Events block.")
            layout.set_alignment(Pango.Alignment.CENTER)
            tw, th = layout.get_pixel_size()
            cr.set_source_rgba(*(0.6, 0.6, 0.65), 1)
            cr.move_to(PALETTE_W + (width - PALETTE_W - tw) / 2, (height - th) / 2)
            PangoCairo.show_layout(cr, layout)
        else:
            if self._snap and self._drag:
                self._draw_snap_hint(cr)
        cr.restore()

        self._draw_palette(cr, height, dark)

        if self._drag:
            cr.save()
            if self._drag["over_palette"]:
                cr.push_group()
            cr.translate(PALETTE_W, 0)
            cr.scale(self.scale, self.scale)
            cr.translate(-self.pan[0], -self.pan[1])
            wx, wy = self._drag["pos"]
            self._draw_stack(cr, self._drag["stack"], wx, wy, None, record=False, shadow=True)
            if self._drag["over_palette"]:
                cr.pop_group_to_source()
                cr.paint_with_alpha(0.5)
            cr.restore()

    def _draw_palette(self, cr, height, dark):
        cr.save()
        cr.rectangle(0, 0, PALETTE_W, height)
        cr.set_source_rgb(*((0.19, 0.19, 0.2) if dark else (1, 1, 1)))
        cr.fill()
        cr.set_source_rgba(0, 0, 0, 0.15 if not dark else 0.5)
        cr.rectangle(PALETTE_W - 1, 0, 1, height)
        cr.fill()

        # category chips, two columns
        self._cats = []
        for idx, (key, label, fill, _border) in enumerate(B.CATEGORIES):
            col, row = idx % 2, idx // 2
            x = 10 + col * ((PALETTE_W - 30) / 2 + 10)
            y = 10 + row * (CAT_ROW_H + 6)
            w = (PALETTE_W - 30) / 2
            rect = (x, y, w, CAT_ROW_H)
            self._cats.append((rect, key))
            selected = key == self.category
            self._rounded(cr, x, y, w, CAT_ROW_H, 8)
            r, g, b = _rgb(fill)
            if selected:
                cr.set_source_rgb(r, g, b)
            else:
                cr.set_source_rgba(r, g, b, 0.18)
            cr.fill()
            cr.arc(x + 14, y + CAT_ROW_H / 2, 5, 0, 2 * math.pi)
            cr.set_source_rgb(*((1, 1, 1) if selected else (r, g, b)))
            cr.fill()
            layout = self.create_pango_layout(label)
            layout.set_font_description(self._font)
            _tw, th = layout.get_pixel_size()
            if selected:
                cr.set_source_rgb(1, 1, 1)
            else:
                cr.set_source_rgb(*((0.9, 0.9, 0.92) if dark else (0.2, 0.2, 0.25)))
            cr.move_to(x + 26, y + (CAT_ROW_H - th) / 2)
            PangoCairo.show_layout(cr, layout)

        cr.rectangle(0, CAT_AREA_H, PALETTE_W - 1, height - CAT_AREA_H)
        cr.clip()
        self._palette = []
        y = CAT_AREA_H + 8 - self.palette_scroll
        for definition in B.defs_in_category(self.category):
            block = self._palette_block(definition.type)
            w, h = self._measure(block)
            self._draw_block(cr, block, 12, y, None, record=False)
            self._palette.append(((12, y, w, h), definition.type))
            y += h + 14
        self._palette_height = y + self.palette_scroll - CAT_AREA_H
        cr.restore()

    def _palette_block(self, block_type):
        block = B.new_block(block_type)
        definition = B.BLOCK_DEFS[block_type]
        ids = self.element_ids()
        names = [v["name"] for v in self.data["variables"]]
        for name, spec in definition.inputs.items():
            if spec.kind == "element" and ids:
                block["inputs"][name] = ids[0]
            elif spec.kind == "variable":
                block["inputs"][name] = names[0] if names else "my variable"
        return block

    def _draw_stack(self, cr, block, x, y, link, record, shadow=False):
        for b in B.iter_stack(block):
            y += self._draw_block(cr, b, x, y, link, record, shadow)
            link = ("next", b)

    def _draw_block(self, cr, block, x, y, link, record, shadow=False):
        definition = B.BLOCK_DEFS[block["type"]]
        shape = definition.shape
        fill, border = (_rgb(c) for c in B.CATEGORY_COLORS[definition.category])
        items = self._items(block)
        row_w, row_h = self._row_size(block, items, shape)
        w, h = self._measure(block)
        top = y + (HAT_H if shape == B.HAT else 0)

        mouth_spans = []
        if shape in (B.C, B.C2):
            cy = top + row_h
            for idx, name in enumerate(definition.substacks):
                if idx > 0:
                    cy += MID_ROW
                sub = (block.get("substacks") or {}).get(name)
                mh = max(MOUTH_MIN, self._stack_height(sub))
                mouth_spans.append((name, cy, mh, sub))
                cy += mh

        if shadow:
            cr.save()
            cr.translate(3, 4)
            self._block_path(cr, shape, x, y, w, h, row_h, mouth_spans)
            cr.set_source_rgba(0, 0, 0, 0.25)
            cr.fill()
            cr.restore()
        self._block_path(cr, shape, x, y, w, h, row_h, mouth_spans)
        cr.set_source_rgb(*fill)
        cr.fill_preserve()
        cr.set_line_width(1)
        cr.set_source_rgb(*border)
        cr.stroke()
        if record and block is self.selected:
            self._block_path(cr, shape, x, y, w, h, row_h, mouth_spans)
            cr.set_line_width(3)
            cr.set_source_rgb(1, 0.85, 0.2)
            cr.stroke()

        geo = None
        if record:
            geo = Geo(block, x, y, w, h, shape, link)
            if shape in (B.C, B.C2):
                geo.rects = [(x, y, w, top + row_h - y)]
                for name, my, mh, _sub in mouth_spans:
                    geo.rects.append((x, my, ARM, mh))
                    geo.mouths.append((name, x + ARM, my))
                for i in range(1, len(mouth_spans)):
                    _n, my, _mh, _s = mouth_spans[i]
                    geo.rects.append((x, my - MID_ROW, w, MID_ROW))
                geo.rects.append((x, y + h - BOTTOM_ARM, w, BOTTOM_ARM))
            self._geos.append(geo)

        # row contents
        reporter = shape in (B.REPORTER, B.BOOLEAN)
        cx = x + ((14 if shape == B.BOOLEAN else 10) if reporter else PAD)
        for item in items:
            iw, ih = item[2], item[3]
            iy = top + (row_h - ih) / 2
            if item[0] == "text":
                cr.set_source_rgb(1, 1, 1)
                layout = self.create_pango_layout(item[1])
                layout.set_font_description(self._font)
                cr.move_to(cx, iy)
                PangoCairo.show_layout(cr, layout)
            elif item[0] == "field":
                self._draw_field(cr, cx, iy, iw, ih, item[4], item[5], fill, border)
                if record:
                    self._slots.append(Slot(block, item[1], item[4], cx, iy, iw, ih))
            elif item[0] == "bool":
                self._hexagon(cr, cx, iy, iw, ih)
                cr.set_source_rgb(*[c * 0.75 for c in fill])
                cr.fill()
                if record:
                    self._slots.append(Slot(block, item[1], item[4], cx, iy, iw, ih))
            elif item[0] == "reporter":
                if record:
                    self._slots.append(Slot(block, item[1], item[5], cx, iy, iw, ih, occupied=True))
                self._draw_block(cr, item[4], cx, iy, ("input", block, item[1]), record)
            cx += iw + GAP

        # else label and substacks
        if shape == B.C2 and len(mouth_spans) > 1:
            _n, my, _mh, _s = mouth_spans[1]
            layout = self.create_pango_layout(definition.mouth_labels[0])
            layout.set_font_description(self._font)
            _tw, th = layout.get_pixel_size()
            cr.set_source_rgb(1, 1, 1)
            cr.move_to(x + PAD, my - MID_ROW + (MID_ROW - th) / 2)
            PangoCairo.show_layout(cr, layout)
        for name, my, _mh, sub in mouth_spans:
            if sub is not None:
                self._draw_stack(cr, sub, x + ARM, my, ("sub", block, name), record, shadow=False)
        return h

    def _draw_field(self, cr, x, y, w, h, spec, text, fill, border):
        dropdown = spec.kind in ("element", "variable", "choice")
        if dropdown:
            self._rounded(cr, x, y, w, h, 6 if spec.kind != "choice" else h / 2)
            cr.set_source_rgb(*border)
            cr.fill()
            color = (1, 1, 1)
        else:
            self._rounded(cr, x, y, w, h, h / 2)
            cr.set_source_rgb(1, 1, 1)
            cr.fill_preserve()
            cr.set_source_rgba(*border, 0.6)
            cr.set_line_width(1)
            cr.stroke()
            color = TEXT_DARK
        tx = x + 7
        if spec.kind == "color":
            rgba = Gdk.RGBA()
            if rgba.parse(text):
                cr.arc(x + 12, y + h / 2, 7, 0, 2 * math.pi)
                cr.set_source_rgba(rgba.red, rgba.green, rgba.blue, rgba.alpha)
                cr.fill_preserve()
                cr.set_source_rgba(0, 0, 0, 0.3)
                cr.stroke()
            tx = x + 23
        layout = self.create_pango_layout(text)
        layout.set_font_description(self._field_font)
        _tw, th = layout.get_pixel_size()
        cr.set_source_rgb(*color)
        cr.move_to(tx, y + (h - th) / 2)
        PangoCairo.show_layout(cr, layout)
        if dropdown:
            cr.move_to(x + w - 13, y + h / 2 - 2)
            cr.rel_line_to(4, 4)
            cr.rel_line_to(4, -4)
            cr.set_line_width(1.6)
            cr.set_source_rgb(1, 1, 1)
            cr.stroke()

    def _rounded(self, cr, x, y, w, h, r):
        r = min(r, h / 2, w / 2)
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
        cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
        cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
        cr.arc(x + r, y + r, r, math.pi, 1.5 * math.pi)
        cr.close_path()

    def _hexagon(self, cr, x, y, w, h):
        cr.new_sub_path()
        cr.move_to(x + h / 2, y)
        cr.line_to(x + w - h / 2, y)
        cr.line_to(x + w, y + h / 2)
        cr.line_to(x + w - h / 2, y + h)
        cr.line_to(x + h / 2, y + h)
        cr.line_to(x, y + h / 2)
        cr.close_path()

    def _notch_right_to_left(self, cr, x, y):
        """Bottom bump / mouth notch drawn while moving leftwards at height y."""
        cr.line_to(x + 32, y)
        cr.line_to(x + 28, y + 4)
        cr.line_to(x + 16, y + 4)
        cr.line_to(x + 12, y)

    def _block_path(self, cr, shape, x, y, w, h, row_h, mouths):
        r = 4
        cr.new_path()
        if shape == B.REPORTER:
            self._rounded(cr, x, y, w, h, h / 2)
            return
        if shape == B.BOOLEAN:
            self._hexagon(cr, x, y, w, h)
            return
        if shape == B.HAT:
            top = y + HAT_H
            cr.move_to(x, top)
            cr.curve_to(x + 25, y - 4, x + 75, y - 4, x + 100, top)
            cr.line_to(x + w - r, top)
            cr.arc(x + w - r, top + r, r, -math.pi / 2, 0)
        else:
            cr.move_to(x, y + r)
            cr.arc(x + r, y + r, r, math.pi, 1.5 * math.pi)
            cr.line_to(x + 12, y)
            cr.line_to(x + 16, y + 4)
            cr.line_to(x + 28, y + 4)
            cr.line_to(x + 32, y)
            cr.line_to(x + w - r, y)
            cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
        if shape in (B.C, B.C2):
            for _name, my, mh, _sub in mouths:
                # right edge down to the mouth, then into it
                cr.line_to(x + w, my - r)
                cr.arc(x + w - r, my - r, r, 0, math.pi / 2)
                self._notch_right_to_left(cr, x + ARM, my)
                cr.line_to(x + ARM, my)
                cr.line_to(x + ARM, my + mh)
                cr.line_to(x + w - r, my + mh)
                cr.arc(x + w - r, my + mh + r, r, -math.pi / 2, 0)
        cr.line_to(x + w, y + h - r)
        cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
        if shape != B.CAP:
            self._notch_right_to_left(cr, x, y + h)
        cr.line_to(x + r, y + h)
        cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
        cr.close_path()

    def _draw_snap_hint(self, cr):
        kind, target, *rest = self._snap
        stack = self._drag["stack"]
        w = self._stack_width(stack)
        if kind == "slot":
            slot = target
            self._rounded(cr, slot.x - 2, slot.y - 2, slot.w + 4, slot.h + 4, (slot.h + 4) / 2)
            cr.set_source_rgba(1, 1, 1, 0.9)
            cr.set_line_width(3)
            cr.stroke()
            return
        x, y = rest[0], rest[1]
        self._rounded(cr, x, y - 3, max(60, w), 6, 3)
        if self._dark():
            cr.set_source_rgba(1, 1, 1, 0.9)
        else:
            cr.set_source_rgba(0.1, 0.1, 0.1, 0.55)
        cr.fill()

    # -- hit testing ---------------------------------------------------
    def _hit_slot(self, wx, wy):
        for slot in reversed(self._slots):
            if not slot.occupied and _inside(wx, wy, (slot.x, slot.y, slot.w, slot.h)):
                return slot
        return None

    def _hit_block(self, wx, wy):
        for geo in reversed(self._geos):
            for rect in geo.rects:
                if _inside(wx, wy, rect):
                    return geo
        return None

    def _hit_palette(self, sx, sy):
        if sy < CAT_AREA_H:
            for rect, key in self._cats:
                if _inside(sx, sy, rect):
                    return ("category", key)
            return None
        for rect, block_type in self._palette:
            if _inside(sx, sy, rect):
                return ("block", block_type, rect)
        return None

    # -- data edits ----------------------------------------------------
    def _detach(self, geo: Geo):
        """Remove geo.block (and blocks after it) from the tree; return the stack."""
        block, link = geo.block, geo.link
        kind = link[0]
        if kind == "script":
            stack = self.data["scripts"].pop(link[1])
        elif kind == "next":
            link[1]["next"] = None
            stack = block
        elif kind == "sub":
            link[1]["substacks"][link[2]] = None
            stack = block
        elif kind == "input":
            parent, name = link[1], link[2]
            spec = B.BLOCK_DEFS[parent["type"]].inputs[name]
            if spec.kind == "boolean":
                parent["inputs"].pop(name, None)
            else:
                parent["inputs"][name] = spec.default
            stack = block
        else:
            return None
        stack.pop("x", None)
        stack.pop("y", None)
        return stack

    def _last(self, stack):
        last = stack
        for b in B.iter_stack(stack):
            last = b
        return last

    def _ensure_variables(self, stack):
        known = {v["name"] for v in self.data["variables"]}

        def visit(block):
            for b in B.iter_stack(block):
                definition = B.BLOCK_DEFS[b["type"]]
                for name, spec in definition.inputs.items():
                    value = b.get("inputs", {}).get(name)
                    if spec.kind == "variable" and value and value not in known:
                        self.data["variables"].append({"name": value, "initial": 0})
                        known.add(value)
                    if isinstance(value, dict):
                        visit(value)
                for sub in (b.get("substacks") or {}).values():
                    visit(sub)

        visit(stack)

    def _find_snap(self, stack, wx, wy):
        definition = B.BLOCK_DEFS[stack["type"]]
        if definition.shape in (B.REPORTER, B.BOOLEAN):
            h = self._measure(stack)[1]
            px, py = wx + 8, wy + h / 2
            best, best_d = None, SNAP_DIST
            for slot in self._slots:
                if slot.spec.kind in ("element", "variable", "choice"):
                    continue
                if slot.spec.kind == "boolean" and definition.shape != B.BOOLEAN:
                    continue
                if _inside(px, py, (slot.x, slot.y, slot.w, slot.h), pad=10):
                    d = abs(px - slot.x) + abs(py - (slot.y + slot.h / 2)) * 0.5
                    if d < best_d or best is None:
                        best, best_d = slot, d
            return ("slot", best) if best else None
        last = self._last(stack)
        ends_with_cap = B.BLOCK_DEFS[last["type"]].shape == B.CAP
        is_hat = definition.shape == B.HAT
        candidates = []
        for geo in self._geos:
            if geo.shape in (B.REPORTER, B.BOOLEAN):
                continue
            if not is_hat:
                if geo.shape != B.CAP and not (ends_with_cap and geo.block.get("next")):
                    candidates.append(("after", geo.block, geo.x, geo.y + geo.h))
                for name, mx, my in geo.mouths:
                    occupied = (geo.block.get("substacks") or {}).get(name)
                    if not (ends_with_cap and occupied):
                        candidates.append(("mouth", geo.block, mx, my, name))
            if geo.link and geo.link[0] == "script" and geo.shape != B.HAT and \
                    B.BLOCK_DEFS[last["type"]].shape != B.CAP:
                h = self._stack_height(stack)
                candidates.append(("prepend", geo.link[1], geo.x, geo.y - h))
        best, best_d = None, SNAP_DIST
        for cand in candidates:
            d = math.hypot(wx - cand[2], wy - cand[3])
            if d < best_d:
                best, best_d = cand, d
        if best and best[0] == "prepend":
            return ("prepend", best[1], best[2], best[3] + self._stack_height(stack))
        return best

    def _apply_drop(self, stack, wx, wy):
        snap = self._find_snap(stack, wx, wy)
        last = self._last(stack)
        if snap is None:
            stack["x"], stack["y"] = round(wx), round(wy)
            self.data["scripts"].append(stack)
        elif snap[0] == "slot":
            slot = snap[1]
            old = slot.block.get("inputs", {}).get(slot.name)
            slot.block.setdefault("inputs", {})[slot.name] = stack
            if isinstance(old, dict):
                old["x"], old["y"] = round(wx + 30), round(wy + 40)
                self.data["scripts"].append(old)
        elif snap[0] == "after":
            target = snap[1]
            if B.BLOCK_DEFS[last["type"]].shape != B.CAP:
                last["next"] = target.get("next")
            target["next"] = stack
        elif snap[0] == "mouth":
            target, name = snap[1], snap[4]
            subs = target.setdefault("substacks", {})
            if B.BLOCK_DEFS[last["type"]].shape != B.CAP:
                last["next"] = subs.get(name)
            subs[name] = stack
        elif snap[0] == "prepend":
            index = snap[1]
            old_top = self.data["scripts"][index]
            stack["x"], stack["y"] = old_top.get("x", 0), round(old_top.get("y", 0)
                                                              - self._stack_height(stack))
            old_top.pop("x", None)
            old_top.pop("y", None)
            last["next"] = old_top
            self.data["scripts"][index] = stack
        self._ensure_variables(stack)
        self.selected = stack

    # -- gestures ------------------------------------------------------
    def _on_drag_begin(self, gesture, sx, sy):
        self.grab_focus()
        self.popover.popdown()
        self._press = (sx, sy)
        self._drag = None

    def _start_drag(self, sx, sy):
        if sx < PALETTE_W:
            hit = self._hit_palette(sx, sy)
            if not hit or hit[0] != "block":
                return False
            rect = hit[2]
            block = self._palette_block(hit[1])
            off = ((sx - rect[0]) / self.scale, (sy - rect[1]) / self.scale)
            self._drag = {"stack": block, "offset": off, "pos": self.to_world(sx, sy),
                          "over_palette": True}
            return True
        wx, wy = self.to_world(sx, sy)
        geo = self._hit_block(wx, wy)
        if geo is None:
            self._drag = {"pan": tuple(self.pan)}
            return True
        stack = self._detach(geo)
        if stack is None:
            return False
        self._drag = {"stack": stack, "offset": (wx - geo.x, wy - geo.y), "pos": (geo.x, geo.y),
                      "over_palette": False}
        self.selected = stack
        return True

    def _on_drag_update(self, gesture, dx, dy):
        if self._press is None:
            return
        sx, sy = self._press[0] + dx, self._press[1] + dy
        if self._drag is None:
            if math.hypot(dx, dy) < 4:
                return
            if not self._start_drag(*self._press):
                self._press = None
                return
        if "pan" in self._drag:
            px, py = self._drag["pan"]
            self.pan = [px - dx / self.scale, py - dy / self.scale]
            self.queue_draw()
            return
        wx, wy = self.to_world(sx, sy)
        ox, oy = self._drag["offset"]
        self._drag["pos"] = (wx - ox, wy - oy)
        self._drag["over_palette"] = sx < PALETTE_W
        self._snap = None if sx < PALETTE_W else self._find_snap(self._drag["stack"], wx - ox, wy - oy)
        self.queue_draw()

    def _on_drag_end(self, gesture, dx, dy):
        press = self._press
        self._press = None
        drag, self._drag, self._snap = self._drag, None, None
        if press is None:
            return
        if drag is None:
            self._click(*press)
            return
        if "pan" in drag:
            return
        sx = press[0] + dx
        if sx < PALETTE_W:
            if self.selected is drag["stack"]:
                self.selected = None
        else:
            self._apply_drop(drag["stack"], *drag["pos"])
        self._changed()

    def _click(self, sx, sy):
        if sx < PALETTE_W:
            hit = self._hit_palette(sx, sy)
            if hit and hit[0] == "category":
                self.set_category(hit[1])
            return
        wx, wy = self.to_world(sx, sy)
        slot = self._hit_slot(wx, wy)
        geo = self._hit_block(wx, wy)
        self.selected = geo.block if geo else None
        self.queue_draw()
        if slot is not None and slot.spec.kind != "boolean":
            self._edit_slot(slot)

    def _on_scroll(self, controller, dx, dy):
        state = controller.get_current_event_state()
        if state & Gdk.ModifierType.CONTROL_MASK:
            self.zoom(-0.1 if dy > 0 else 0.1)
            return True
        if self._pointer_x < PALETTE_W:
            max_scroll = max(0, getattr(self, "_palette_height", 0) - (self.get_height() - CAT_AREA_H))
            self.palette_scroll = min(max_scroll, max(0, self.palette_scroll + dy * 40))
        else:
            self.pan[0] += dx * 40 / self.scale
            self.pan[1] += dy * 40 / self.scale
        self.queue_draw()
        return True

    def _on_key(self, _ctrl, keyval, _code, state):
        if keyval in (Gdk.KEY_Delete, Gdk.KEY_BackSpace) and self.selected is not None:
            self._delete_block(self.selected)
            return True
        if keyval == Gdk.KEY_d and state & Gdk.ModifierType.CONTROL_MASK and self.selected:
            self._duplicate(self.selected)
            return True
        return False

    # -- context menu ----------------------------------------------------
    def _context_model(self):
        menu = Gio.Menu()
        menu.append("Duplicate", "blocks.duplicate")
        menu.append("Delete Block", "blocks.delete")
        section = Gio.Menu()
        section.append("Clean Up Blocks", "blocks.cleanup")
        menu.append_section(None, section)
        return menu

    def _on_context_click(self, gesture, n_press, sx, sy):
        if sx < PALETTE_W:
            return
        wx, wy = self.to_world(sx, sy)
        geo = self._hit_block(wx, wy)
        self._menu_target = geo
        self.selected = geo.block if geo else None
        for name in ("duplicate", "delete"):
            self._group.lookup_action(name).set_enabled(geo is not None)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(sx), int(sy), 1, 1
        self.menu.set_pointing_to(rect)
        self.menu.popup()
        self.queue_draw()

    def _act_duplicate(self, *_args):
        if self._menu_target:
            self._duplicate(self._menu_target.block)

    def _act_delete(self, *_args):
        if self._menu_target:
            self._delete_block(self._menu_target.block)

    def _find_geo(self, block):
        for geo in self._geos:
            if geo.block is block:
                return geo
        return None

    def _duplicate(self, block):
        geo = self._find_geo(block)
        # Like Scratch, duplicating copies the block and everything below it.
        clone = copy.deepcopy(block)
        x, y = (geo.x, geo.y) if geo else (20, 20)
        clone["x"], clone["y"] = round(x + 24), round(y + 24)
        self.data["scripts"].append(clone)
        self.selected = clone
        self._changed()

    def _delete_block(self, block):
        geo = self._find_geo(block)
        if geo is None:
            return
        following = block.get("next")
        kind = geo.link[0]
        definition = B.BLOCK_DEFS[block["type"]]
        if kind == "script":
            index = geo.link[1]
            if following is not None and definition.shape != B.HAT:
                following["x"], following["y"] = block.get("x", 0), block.get("y", 0)
                self.data["scripts"][index] = following
            elif following is not None:
                following["x"], following["y"] = block.get("x", 0), block.get("y", 0) + HAT_H + ROW_MIN
                self.data["scripts"][index] = following
            else:
                self.data["scripts"].pop(index)
        elif kind == "next":
            geo.link[1]["next"] = following
        elif kind == "sub":
            geo.link[1]["substacks"][geo.link[2]] = following
        elif kind == "input":
            self._detach(geo)
        self.selected = None
        self._changed()

    # -- input editing ---------------------------------------------------
    def _edit_slot(self, slot: Slot):
        spec = slot.spec
        current = slot.block.get("inputs", {}).get(slot.name, spec.default)
        sx, sy = self.to_screen(slot.x, slot.y)
        rect = Gdk.Rectangle()
        rect.x, rect.y = int(sx), int(sy)
        rect.width, rect.height = max(1, int(slot.w * self.scale)), max(1, int(slot.h * self.scale))
        self.popover.set_pointing_to(rect)
        self._editing = (slot.block, slot.name)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        box.set_margin_start(6)
        box.set_margin_end(6)
        if spec.kind in ("element", "variable", "choice"):
            if spec.kind == "element":
                options = self.element_ids()
                empty = "No elements with an id on the page yet."
            elif spec.kind == "variable":
                options = [v["name"] for v in self.data["variables"]]
                empty = "No variables yet. Add one in the Variables panel."
            else:
                options = list(spec.options)
                empty = ""
            listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            listbox.add_css_class("boxed-list")
            for option in options:
                row = Gtk.ListBoxRow()
                label = Gtk.Label(label=option, xalign=0, margin_start=10, margin_end=10,
                                  margin_top=6, margin_bottom=6)
                row.set_child(label)
                row.option = option
                listbox.append(row)
            listbox.connect("row-activated", lambda _lb, row: self._commit(row.option, close=True))
            if options:
                scroller = Gtk.ScrolledWindow(child=listbox, propagate_natural_height=True,
                                              max_content_height=300, min_content_width=180)
                box.append(scroller)
            elif empty:
                box.append(Gtk.Label(label=empty, wrap=True, max_width_chars=28))
            if spec.kind == "element":
                entry = Gtk.Entry(text=str(current or ""), placeholder_text="Or type an element id")
                entry.update_property([Gtk.AccessibleProperty.LABEL], ["Element id"])
                entry.connect("activate", lambda e: self._commit(e.get_text().strip(), close=True))
                box.append(entry)
        else:
            entry = Gtk.Entry(text=str(current), width_chars=14)
            entry.update_property([Gtk.AccessibleProperty.LABEL], ["Block input value"])
            if spec.kind == "number":
                entry.set_input_purpose(Gtk.InputPurpose.NUMBER)
            entry.connect("activate", lambda e: self._commit(e.get_text(), close=True))
            entry.connect("changed", lambda e: self._commit(e.get_text(), close=False))
            box.append(entry)
            if spec.kind == "color":
                swatches = Gtk.FlowBox(max_children_per_line=6, selection_mode=Gtk.SelectionMode.NONE)
                for color in ("#e01b24", "#ff7800", "#f6d32d", "#33d17a", "#3584e4", "#9141ac",
                              "#000000", "#5e5c64", "#ffffff", "transparent"):
                    button = Gtk.Button(tooltip_text=color)
                    button.update_property([Gtk.AccessibleProperty.LABEL], [f"Color {color}"])
                    swatch = Gtk.DrawingArea(content_width=18, content_height=18)
                    swatch.set_draw_func(self._draw_swatch, color)
                    button.set_child(swatch)
                    button.add_css_class("flat")
                    button.connect("clicked", lambda _b, c=color, e=entry: e.set_text(c))
                    swatches.append(button)
                box.append(swatches)
            GLib.idle_add(entry.grab_focus)
        self.popover.set_child(box)
        self.popover.popup()

    def _draw_swatch(self, _area, cr, width, height, color):
        rgba = Gdk.RGBA()
        if not rgba.parse(color):
            return
        cr.arc(width / 2, height / 2, min(width, height) / 2 - 1, 0, 2 * math.pi)
        cr.set_source_rgba(rgba.red, rgba.green, rgba.blue, rgba.alpha)
        cr.fill_preserve()
        cr.set_source_rgba(0, 0, 0, 0.25)
        cr.set_line_width(1)
        cr.stroke()

    def _commit(self, value, close):
        if not self._editing:
            return
        block, name = self._editing
        spec = B.BLOCK_DEFS[block["type"]].inputs[name]
        if spec.kind == "variable" and value:
            self._ensure_variables({"type": "var_get", "inputs": {"var": value}, "next": None})
        block.setdefault("inputs", {})[name] = value
        self._changed()
        if close:
            self.popover.popdown()

    def _on_popover_closed(self, *_args):
        self._editing = None
        self.grab_focus()


class VariablesPanel(Gtk.Box):
    """Declare / rename / remove block variables and their initial values."""

    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self, canvas: BlockCanvas):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6, width_request=220,
                         hexpand=False)
        self.canvas = canvas
        self.add_css_class("variables-panel")
        title = Gtk.Label(label="Variables", xalign=0)
        title.add_css_class("heading")
        self.append(title)
        add_box = Gtk.Box(spacing=6)
        add_box.add_css_class("linked")
        self.entry = Gtk.Entry(placeholder_text="New variable name", hexpand=True,
                               width_chars=10)
        self.entry.update_property([Gtk.AccessibleProperty.LABEL], ["New variable name"])
        self.entry.connect("activate", self._on_add)
        add = Gtk.Button(icon_name="list-add-symbolic", tooltip_text="Add variable")
        add.update_property([Gtk.AccessibleProperty.LABEL], ["Add variable"])
        add.connect("clicked", self._on_add)
        add_box.append(self.entry)
        add_box.append(add)
        self.append(add_box)
        self.shown = []
        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.listbox.add_css_class("boxed-list")
        self.placeholder = Gtk.Label(label="No variables yet", margin_top=12, margin_bottom=12)
        self.placeholder.add_css_class("dim-label")
        self.listbox.set_placeholder(self.placeholder)
        self.append(Gtk.ScrolledWindow(child=self.listbox, vexpand=True,
                                       hscrollbar_policy=Gtk.PolicyType.NEVER))

    def refresh(self):
        self.shown = [v["name"] for v in self.canvas.data["variables"]]
        while (row := self.listbox.get_row_at_index(0)) is not None:
            self.listbox.remove(row)
        for var in self.canvas.data["variables"]:
            row = Adw.EntryRow(title=var["name"], text=str(var.get("initial", 0)))
            row.set_tooltip_text("Initial value")
            row.update_property([Gtk.AccessibleProperty.LABEL],
                                [f"Initial value of {var['name']}"])
            row.connect("changed", self._on_initial_changed, var)
            remove = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER,
                                tooltip_text=f"Remove {var['name']}")
            remove.add_css_class("flat")
            remove.update_property([Gtk.AccessibleProperty.LABEL], [f"Remove variable {var['name']}"])
            remove.connect("clicked", self._on_remove, var)
            row.add_suffix(remove)
            self.listbox.append(row)

    def _on_add(self, *_args):
        name = self.entry.get_text().strip()
        if not name or any(v["name"] == name for v in self.canvas.data["variables"]):
            self.entry.add_css_class("error")
            return
        self.entry.remove_css_class("error")
        self.canvas.data["variables"].append({"name": name, "initial": 0})
        self.entry.set_text("")
        self.refresh()
        self.canvas._changed()

    def _on_initial_changed(self, row, var):
        var["initial"] = row.get_text()
        self.canvas.emit("changed")

    def _on_remove(self, _button, var):
        self.canvas.data["variables"] = [v for v in self.canvas.data["variables"] if v is not var]
        self.refresh()
        self.canvas._changed()


class BlocksPane(Gtk.Box):
    """Blocks tab: toolbar, canvas, variables panel and compiled-JS view."""

    __gsignals__ = {
        "changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "run": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "export-js": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.compile = lambda: ""

        toolbar = Gtk.Box(spacing=6)
        toolbar.add_css_class("toolbar")
        run = Gtk.Button()
        run.set_child(Adw.ButtonContent(icon_name="media-playback-start-symbolic",
                                        label="Run in Preview"))
        run.add_css_class("suggested-action")
        run.set_tooltip_text("Compile the blocks and run them in the preview (F5)")
        run.connect("clicked", lambda *_: self.emit("run"))
        toolbar.append(run)
        export = Gtk.Button()
        export.set_child(Adw.ButtonContent(icon_name="document-save-as-symbolic", label="Export JS"))
        export.set_tooltip_text("Save the compiled JavaScript to a file")
        export.connect("clicked", lambda *_: self.emit("export-js"))
        toolbar.append(export)
        cleanup = Gtk.Button(icon_name="view-grid-symbolic", tooltip_text="Clean up blocks")
        cleanup.update_property([Gtk.AccessibleProperty.LABEL], ["Clean up blocks"])
        toolbar.append(cleanup)

        spacer = Gtk.Box(hexpand=True)
        toolbar.append(spacer)

        zoom_box = Gtk.Box()
        zoom_box.add_css_class("linked")
        for icon, delta, tip in (("zoom-out-symbolic", -0.1, "Zoom out blocks"),
                                 ("zoom-original-symbolic", None, "Reset block zoom"),
                                 ("zoom-in-symbolic", 0.1, "Zoom in blocks")):
            button = Gtk.Button(icon_name=icon, tooltip_text=tip)
            button.update_property([Gtk.AccessibleProperty.LABEL], [tip])
            button.connect("clicked", lambda _b, d=delta: self.canvas.zoom(d))
            zoom_box.append(button)
        toolbar.append(zoom_box)

        self.show_js = Gtk.ToggleButton()
        self.show_js.set_child(Adw.ButtonContent(icon_name="text-x-generic-symbolic",
                                                 label="JavaScript"))
        self.show_js.set_tooltip_text("Show the compiled JavaScript")
        toolbar.append(self.show_js)
        self.append(toolbar)
        self.append(Gtk.Separator())

        self.canvas = BlockCanvas()
        cleanup.connect("clicked", lambda *_: self.canvas.clean_up())
        self.variables = VariablesPanel(self.canvas)
        self.canvas.connect("changed", self._on_canvas_changed)

        buffer = GtkSource.Buffer(
            language=GtkSource.LanguageManager.get_default().get_language("js"),
            highlight_syntax=True)
        self.js_buffer = buffer
        js_view = GtkSource.View(buffer=buffer, editable=False, monospace=True,
                                 show_line_numbers=True, top_margin=6, left_margin=4)
        js_view.update_property([Gtk.AccessibleProperty.LABEL], ["Compiled JavaScript"])
        js_scroller = Gtk.ScrolledWindow(child=js_view, width_request=360, hexpand=False)
        self.js_revealer = Gtk.Revealer(child=js_scroller,
                                        transition_type=Gtk.RevealerTransitionType.SLIDE_LEFT)
        self.show_js.bind_property("active", self.js_revealer, "reveal-child",
                                   GObject.BindingFlags.SYNC_CREATE)
        self.show_js.connect("toggled", lambda *_: self.refresh_js())
        style = Adw.StyleManager.get_default()
        style.connect("notify::dark", lambda *_: self._apply_scheme())
        self._apply_scheme()

        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=False)
        side.add_css_class("blocks-side")
        side.append(self.variables)

        body = Gtk.Box(vexpand=True)
        body.append(self.canvas)
        body.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        body.append(side)
        body.append(self.js_revealer)
        self.append(body)

    def _apply_scheme(self):
        dark = Adw.StyleManager.get_default().get_dark()
        scheme = GtkSource.StyleSchemeManager.get_default().get_scheme(
            "Adwaita-dark" if dark else "Adwaita")
        if scheme:
            self.js_buffer.set_style_scheme(scheme)

    def load(self, data):
        self.canvas.load(data)
        self.variables.refresh()
        self.refresh_js()

    def data(self):
        return self.canvas.export_data()

    def refresh_js(self):
        if self.show_js.get_active():
            self.js_buffer.set_text(self.compile() or "// Add an Events block to start a script.\n")

    def _on_canvas_changed(self, *_args):
        names = [v["name"] for v in self.canvas.data["variables"]]
        if names != self.variables.shown:
            self.variables.refresh()
        self.emit("changed")
