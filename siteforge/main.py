"""Command line entry point and the Adw.Application."""

from __future__ import annotations

import argparse
import os
import sys

from . import APP_ID, __version__


def _parse_args(argv):
    parser = argparse.ArgumentParser(prog="siteforge", description="Visual HTML website builder")
    parser.add_argument("file", nargs="?", help="a .siteforge project to open")
    parser.add_argument("--version", action="version", version=f"SiteForge {__version__}")
    parser.add_argument("--export", metavar="DIR",
                        help="export FILE to DIR without opening a window")
    parser.add_argument("--split", action="store_true",
                        help="with --export: write separate style.css and script.js")
    parser.add_argument("--smoke-test", action="store_true",
                        help="open the window, wait for the preview to load, then quit "
                             "(used by test.sh)")
    return parser.parse_args(argv)


def export_cli(args) -> int:
    from .project import Project

    if not args.file:
        print("siteforge: --export needs a project FILE", file=sys.stderr)
        return 2
    try:
        project = Project.load(args.file)
        written = project.export(args.export, split=args.split)
    except (OSError, ValueError) as err:
        print(f"siteforge: {err}", file=sys.stderr)
        return 1
    for path in written:
        print(path)
    return 0


def main(argv=None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    if args.export:
        return export_cli(args)

    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    gi.require_version("GtkSource", "5")
    gi.require_version("WebKit", "6.0")
    from gi.repository import Adw, Gdk, Gio, GLib, Gtk, GtkSource

    GLib.set_prgname("siteforge")
    GLib.set_application_name("SiteForge")
    GtkSource.init()

    class Application(Adw.Application):
        def __init__(self):
            super().__init__(application_id=APP_ID,
                             flags=Gio.ApplicationFlags.HANDLES_OPEN
                             | (Gio.ApplicationFlags.NON_UNIQUE if args.smoke_test else 0))
            self.smoke_ok = False
            # Follow the system light/dark preference (the default) explicitly.
            self.get_style_manager().set_color_scheme(Adw.ColorScheme.DEFAULT)
            for name, handler, accels in (("quit", lambda *_: self.quit_all(), ["<Control>q"]),
                                          ("about", self.on_about, [])):
                action = Gio.SimpleAction.new(name, None)
                action.connect("activate", handler)
                self.add_action(action)
                if accels:
                    self.set_accels_for_action(f"app.{name}", accels)

        def do_startup(self):
            Adw.Application.do_startup(self)
            provider = Gtk.CssProvider()
            provider.load_from_path(os.path.join(os.path.dirname(__file__), "ui", "style.css"))
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            icon_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                    "data", "icons")
            if os.path.isdir(icon_dir):  # running from a source checkout
                Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).add_search_path(icon_dir)
            Gtk.Window.set_default_icon_name("siteforge")

        def _new_window(self, path=None):
            from .ui.window import SiteForgeWindow

            window = SiteForgeWindow(self)
            if path:
                window.load_file(path)
            window.present()
            if args.smoke_test:
                self._arm_smoke_test(window)
            return window

        def do_activate(self):
            window = self.get_active_window()
            if window is None:
                self._new_window()
            else:
                window.present()

        def do_open(self, files, _n, _hint):
            for gfile in files:
                window = self.get_active_window()
                # Reuse a pristine window instead of stacking an empty one.
                if window is not None and not window.project.dirty and not window.project.path:
                    window.load_file(gfile.get_path())
                    window.present()
                else:
                    self._new_window(gfile.get_path())

        def quit_all(self):
            for window in list(self.get_windows()):
                window.close()

        def on_about(self, *_args):
            about = Adw.AboutDialog(
                application_name="SiteForge", application_icon="siteforge", version=__version__,
                comments="Visual HTML website builder with Scratch-style blocks and Stripe "
                         "Checkout.",
                license_type=Gtk.License.GPL_3_0, developer_name="SiteForge contributors")
            about.present(self.get_active_window())

        def _arm_smoke_test(self, window):
            from gi.repository import WebKit

            def finished(webview, event):
                if event == WebKit.LoadEvent.FINISHED and not self.smoke_ok:
                    GLib.timeout_add(500, check)

            def check():
                mapped = window.get_mapped() and window.get_width() > 0
                if mapped and not self.smoke_ok:
                    self.smoke_ok = True
                    print(f"SITEFORGE_WINDOW_OK title={window.get_title()!r} "
                          f"size={window.get_width()}x{window.get_height()} "
                          f"elements={len(window.project.elements())}", flush=True)
                    window.project.dirty = False
                    self.quit()
                return GLib.SOURCE_REMOVE

            def timeout():
                if not self.smoke_ok:
                    print("SITEFORGE_WINDOW_TIMEOUT", flush=True)
                    self.quit()
                return GLib.SOURCE_REMOVE

            window.preview.webview.connect("load-changed", finished)
            GLib.timeout_add_seconds(60, timeout)

    app = Application()
    status = app.run([sys.argv[0]] + ([os.path.abspath(args.file)] if args.file else []))
    if args.smoke_test:
        return 0 if app.smoke_ok else 1
    return status


if __name__ == "__main__":
    sys.exit(main())
