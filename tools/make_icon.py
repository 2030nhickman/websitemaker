#!/usr/bin/env python3
"""Draw the SiteForge app icon (256x256 PNG) with Cairo. Run from the repo root."""

import math
import os
import sys

import cairo

SIZE = 256
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    "data", "icons", "hicolor", "256x256", "apps", "siteforge.png")


def rounded(cr, x, y, w, h, r):
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 1.5 * math.pi)
    cr.close_path()


def main():
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, SIZE, SIZE)
    cr = cairo.Context(surface)

    # Base plate with a subtle bottom "depth" like GNOME app icons.
    rounded(cr, 16, 28, 224, 212, 40)
    cr.set_source_rgb(0.11, 0.33, 0.69)
    cr.fill()
    rounded(cr, 16, 16, 224, 212, 40)
    grad = cairo.LinearGradient(0, 16, 0, 228)
    grad.add_color_stop_rgb(0, 0.27, 0.58, 0.95)
    grad.add_color_stop_rgb(1, 0.16, 0.43, 0.84)
    cr.set_source(grad)
    cr.fill()

    # Browser window card.
    rounded(cr, 44, 48, 168, 128, 14)
    cr.set_source_rgb(0.98, 0.98, 1.0)
    cr.fill()
    cr.rectangle(44, 70, 168, 2)
    cr.set_source_rgb(0.86, 0.88, 0.93)
    cr.fill()
    for i, color in enumerate(((0.93, 0.33, 0.31), (0.98, 0.76, 0.2), (0.2, 0.78, 0.48))):
        cr.arc(62 + i * 14, 59, 4.5, 0, 2 * math.pi)
        cr.set_source_rgb(*color)
        cr.fill()

    # "</>" glyph.
    cr.set_line_width(9)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    cr.set_source_rgb(0.16, 0.43, 0.84)
    cr.move_to(102, 98)
    cr.line_to(82, 122)
    cr.line_to(102, 146)
    cr.stroke()
    cr.move_to(154, 98)
    cr.line_to(174, 122)
    cr.line_to(154, 146)
    cr.stroke()
    cr.set_source_rgb(0.57, 0.25, 0.67)
    cr.move_to(136, 94)
    cr.line_to(120, 150)
    cr.stroke()

    # Scratch-style yellow block snapping onto the card.
    x, y, w, h = 64, 168, 128, 40
    cr.new_path()
    cr.move_to(x + 6, y)
    cr.line_to(x + 22, y)
    cr.line_to(x + 28, y + 6)
    cr.line_to(x + 46, y + 6)
    cr.line_to(x + 52, y)
    cr.line_to(x + w - 6, y)
    cr.arc(x + w - 6, y + 6, 6, -math.pi / 2, 0)
    cr.line_to(x + w, y + h - 6)
    cr.arc(x + w - 6, y + h - 6, 6, 0, math.pi / 2)
    cr.line_to(x + 52, y + h)
    cr.line_to(x + 46, y + h + 6)
    cr.line_to(x + 28, y + h + 6)
    cr.line_to(x + 22, y + h)
    cr.line_to(x + 6, y + h)
    cr.arc(x + 6, y + h - 6, 6, math.pi / 2, math.pi)
    cr.line_to(x, y + 6)
    cr.arc(x + 6, y + 6, 6, math.pi, 1.5 * math.pi)
    cr.close_path()
    cr.set_source_rgb(1.0, 0.75, 0.0)
    cr.fill_preserve()
    cr.set_line_width(3)
    cr.set_source_rgb(0.8, 0.6, 0.0)
    cr.stroke()
    rounded(cr, x + 70, y + 11, 44, 18, 9)
    cr.set_source_rgb(1, 1, 1)
    cr.fill()
    rounded(cr, x + 14, y + 16, 46, 8, 4)
    cr.set_source_rgba(1, 1, 1, 0.9)
    cr.fill()

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    surface.write_to_png(OUT)
    print(OUT)


if __name__ == "__main__":
    main()
