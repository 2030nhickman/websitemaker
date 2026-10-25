"""Main SiteForge window."""

from __future__ import annotations

import os

from gi.repository import Adw, Gio, GLib, Gtk

from ..project import Project, validate_stripe
from .blockeditor import BlocksPane
from .codeview import CodePane
from .preview import PreviewPane
from .properties import PropertiesPanel
from .sidebar import Sidebar
from .stripe_dialog import StripeDialog

RENDER_DELAY_MS = 150


class SiteForgeWindow(Adw.ApplicationWindow):
    def __init__(self, app, project: Project | None = None):
        super().__init__(application=app, title="SiteForge", default_width=1280,
                         default_height=820, width_request=360, height_request=294)
        self.set_icon_name("siteforge")
        self.project: Project = None  # set by _set_project
        self.selected_id: str | None = None
        self._render_source = 0
        self._close_confirmed = False
        self._loading = False

        self._build_ui()
        self._install_actions()
        self._set_project(project or Project.blank())
        self.connect("close-request", self._on_close_request)

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        header = Adw.HeaderBar()
        self.window_title = Adw.WindowTitle(title="SiteForge")
        header.set_title_widget(self.window_title)

        def icon_button(icon, action, tooltip):
            button = Gtk.Button(icon_name=icon, action_name=action, tooltip_text=tooltip)
            button.update_property([Gtk.AccessibleProperty.LABEL], [tooltip])
            return button

        header.pack_start(icon_button("document-new-symbolic", "win.new", "New Project (Ctrl+N)"))
        header.pack_start(icon_button("document-open-symbolic", "win.open", "Open Project (Ctrl+O)"))
        save = Gtk.Button(label="Save", action_name="win.save", tooltip_text="Save Project (Ctrl+S)")
        header.pack_start(save)

        menu = Gio.Menu()
        section = Gio.Menu()
        section.append("Save As…", "win.save-as")
        section.append("Export as Single HTML File…", "win.export-single")
        section.append("Export with Separate CSS/JS…", "win.export-split")
        menu.append_section(None, section)
        section = Gio.Menu()
        section.append("Insert Image from File…", "win.insert-image")
        section.append("Stripe Settings…", "win.stripe-settings")
        section.append("Open in System Browser", "win.open-in-browser")
        section.append("Web Inspector", "win.inspector")
        menu.append_section(None, section)
        section = Gio.Menu()
        section.append("Keyboard Shortcuts", "win.show-help-overlay")
        section.append("About SiteForge", "app.about")
        menu.append_section(None, section)
        menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu,
                                     tooltip_text="Main Menu", primary=True)
        menu_button.update_property([Gtk.AccessibleProperty.LABEL], ["Main menu"])
        header.pack_end(menu_button)
        props_toggle = Gtk.ToggleButton(icon_name="sidebar-show-right-symbolic", active=True,
                                        tooltip_text="Show Properties (F9)",
                                        action_name="win.toggle-properties")
        props_toggle.update_property([Gtk.AccessibleProperty.LABEL], ["Show properties panel"])
        header.pack_end(props_toggle)
        stripe = icon_button("auth-smartcard-symbolic", "win.stripe-settings", "Stripe Settings")
        header.pack_end(stripe)

        # sidebar
        self.sidebar = Sidebar()
        self.sidebar.connect("insert-component", self._on_insert_component)
        self.sidebar.connect("element-activated", lambda _s, sf_id: self.select_element(sf_id))

        # tabs
        self.stack = Adw.ViewStack(vexpand=True, hexpand=True)
        self.preview = PreviewPane()
        self.preview.connect("element-selected", lambda _p, sf_id: self.select_element(
            sf_id or None, from_preview=True))
        self.preview.connect("component-dropped", self._on_component_dropped)
        self.preview.connect("zoom-changed", lambda *_: self._update_status())
        self.preview.connect("element-resized", self._on_element_resized)
        self.preview.connect("files-dropped", self._on_files_dropped)
        self.preview.connect("open-in-browser", lambda *_: self.open_in_browser())
        self.preview.connect("page-error", lambda _p, msg: self.toast(f"Page error: {msg}"))
        preview_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        preview_page.append(self._preview_toolbar())
        preview_page.append(Gtk.Separator())
        preview_page.append(self.preview)
        self.stack.add_titled_with_icon(preview_page, "preview", "Preview", "view-reveal-symbolic")

        self.code = CodePane()
        self.code.connect("code-changed", self._on_code_changed)
        self.stack.add_titled_with_icon(self.code, "code", "Code", "text-x-generic-symbolic")

        self.blocks = BlocksPane()
        self.blocks.compile = lambda: self.project.compiled_blocks()
        self.blocks.canvas.element_ids = lambda: self.project.html_ids()
        self.blocks.connect("changed", self._on_blocks_changed)
        self.blocks.connect("run", lambda *_: self.run_blocks())
        self.blocks.connect("export-js", lambda *_: self.export_blocks_js())
        self.stack.add_titled_with_icon(self.blocks, "blocks", "Blocks", "view-app-grid-symbolic")
        self.stack.connect("notify::visible-child-name", lambda *_: self._on_tab_changed())

        switcher = Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE)
        tab_bar = Gtk.Box(halign=Gtk.Align.CENTER)
        tab_bar.add_css_class("tab-strip")
        tab_bar.append(switcher)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        content.append(tab_bar)
        content.append(Gtk.Separator())
        content.append(self.stack)

        self.properties = PropertiesPanel()
        self.properties.connect("prop-changed", self._on_prop_changed)
        self.properties.connect("page-changed", self._on_page_changed)
        self.properties.connect("move", lambda _p, sf_id, d: self.project.move_element(sf_id, d))
        self.properties.connect("delete", lambda _p, sf_id: self.delete_element(sf_id))
        self.properties.connect("choose-image", lambda _p, sf_id, name: self.choose_image(
            sf_id, name))
        self.properties_revealer = Gtk.Revealer(
            child=self.properties, reveal_child=True,
            transition_type=Gtk.RevealerTransitionType.SLIDE_LEFT)
        props_box = Gtk.Box()
        props_box.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        props_box.append(self.properties_revealer)

        body = Gtk.Box(vexpand=True)
        body.append(self.sidebar)
        body.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        body.append(content)
        body.append(props_box)

        # status bar
        status = Gtk.Box(spacing=18)
        status.add_css_class("statusbar")
        self.status_elements = Gtk.Label(xalign=0)
        self.status_zoom = Gtk.Label(xalign=0)
        self.status_save = Gtk.Label(xalign=1, hexpand=True)
        for label in (self.status_elements, self.status_zoom, self.status_save):
            label.add_css_class("caption")
            status.append(label)

        self.toasts = Adw.ToastOverlay(child=body)
        view = Adw.ToolbarView(content=self.toasts)
        view.add_top_bar(header)
        view.add_bottom_bar(status)
        self.set_content(view)

        builder = Gtk.Builder.new_from_string(_SHORTCUTS_UI, -1)
        self.set_help_overlay(builder.get_object("help_overlay"))

    def _preview_toolbar(self):
        bar = Gtk.Box(spacing=6)
        bar.add_css_class("toolbar")
        mode = Gtk.Box()
        mode.add_css_class("linked")
        self.select_toggle = Gtk.ToggleButton(active=True, tooltip_text=(
            "Select mode: click elements to edit them. Turn off to interact with the page."))
        self.select_toggle.set_child(Adw.ButtonContent(icon_name="find-location-symbolic",
                                                       label="Select"))
        self.select_toggle.connect("toggled", lambda b: self.preview.set_mode(
            "select" if b.get_active() else "interact"))
        bar.append(self.select_toggle)

        bar.append(Gtk.Box(hexpand=True))
        first = None
        for key, label in (("desktop", "Desktop"), ("tablet", "Tablet"), ("mobile", "Mobile")):
            button = Gtk.ToggleButton(label=label, active=key == "desktop",
                                      tooltip_text=f"Preview at {label.lower()} width")
            if first is None:
                first = button
            else:
                button.set_group(first)
            button.connect("toggled", lambda b, k=key: b.get_active() and self.preview.set_device(k))
            mode.append(button)
        bar.append(mode)
        bar.append(Gtk.Box(hexpand=True))

        for icon, action, tip in (("view-refresh-symbolic", "win.reload-preview", "Reload Preview"),
                                  ("web-browser-symbolic", "win.open-in-browser",
                                   "Open in System Browser")):
            button = Gtk.Button(icon_name=icon, action_name=action, tooltip_text=tip)
            button.update_property([Gtk.AccessibleProperty.LABEL], [tip])
            bar.append(button)
        return bar

    def _install_actions(self):
        actions = {
            "new": (self.on_new, ["<Control>n"]),
            "open": (self.on_open, ["<Control>o"]),
            "save": (self.on_save, ["<Control>s"]),
            "save-as": (self.on_save_as, ["<Control><Shift>s"]),
            "export-single": (lambda *_: self.on_export(split=False), ["<Control>e"]),
            "export-split": (lambda *_: self.on_export(split=True), ["<Control><Shift>e"]),
            "stripe-settings": (self.on_stripe_settings, []),
            "open-in-browser": (lambda *_: self.open_in_browser(), ["<Control>b"]),
            "reload-preview": (lambda *_: self.render(force=True), ["<Control>r"]),
            "run-blocks": (lambda *_: self.run_blocks(), ["F5"]),
            "zoom-in": (lambda *_: self.preview.zoom(0.1), ["<Control>plus", "<Control>equal"]),
            "zoom-out": (lambda *_: self.preview.zoom(-0.1), ["<Control>minus"]),
            "zoom-reset": (lambda *_: self.preview.zoom(None), ["<Control>0"]),
            "inspector": (lambda *_: self.preview.inspect(), ["<Control><Shift>i"]),
            "tab-preview": (lambda *_: self.stack.set_visible_child_name("preview"), ["<Alt>1"]),
            "tab-code": (lambda *_: self.stack.set_visible_child_name("code"), ["<Alt>2"]),
            "tab-blocks": (lambda *_: self.stack.set_visible_child_name("blocks"), ["<Alt>3"]),
            "choose-image": (lambda *_: self.choose_image(self.selected_id), []),
            "insert-image": (lambda *_: self.choose_image(None), ["<Control>i"]),
            "delete-element": (lambda *_: self.selected_id and self.delete_element(
                self.selected_id), []),
        }
        app = self.get_application()
        for name, (handler, accels) in actions.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", handler)
            self.add_action(action)
            if accels and app is not None:
                app.set_accels_for_action(f"win.{name}", accels)
        toggle = Gio.SimpleAction.new_stateful("toggle-properties", None,
                                               GLib.Variant.new_boolean(True))
        toggle.connect("change-state", self._on_toggle_properties)
        self.add_action(toggle)
        if app is not None:
            app.set_accels_for_action("win.toggle-properties", ["F9"])
            app.set_accels_for_action("win.show-help-overlay", ["<Control>question"])

    def _on_toggle_properties(self, action, value):
        action.set_state(value)
        self.properties_revealer.set_reveal_child(value.get_boolean())
        if value.get_boolean() and self.stack.get_visible_child_name() == "blocks":
            self.stack.set_visible_child_name("preview")

    # ------------------------------------------------------------- project
    def _set_project(self, project: Project):
        self._loading = True
        self.project = project
        project.connect(self._on_project_changed)
        self.selected_id = None
        for part in ("html", "css", "js"):
            self.code.load_text(part, getattr(project, part))
        self.blocks.load(project.blocks)
        self._loading = False
        self._refresh_elements()
        self._show_properties()
        self._update_title()
        self._update_status()
        self.render(force=True)

    def _on_project_changed(self, project, part, origin):
        if project is not self.project:
            return
        if part in ("html", "css", "js") and origin != "code":
            self.code.set_text(part, getattr(project, part))
        if part == "html":
            if self.selected_id and project.element(self.selected_id) is None:
                self.selected_id = None
            self._refresh_elements()
            if origin != "props":
                self._show_properties()
        if part == "blocks" or part == "html":
            self.blocks.refresh_js()
        self._update_title()
        self._update_status()
        if part in ("html", "css", "js", "blocks", "stripe", "meta", "assets"):
            self.schedule_render()

    def _refresh_elements(self):
        self.sidebar.set_elements(self.project.elements(), self.selected_id)
        self.blocks.canvas.queue_draw()

    def _show_properties(self):
        if self.selected_id:
            entry = self.project.element(self.selected_id)
            if entry:
                sf_id, comp, props, _depth = entry
                self.properties.show_element(sf_id, comp, props)
                return
        self.properties.show_page_settings(self.project.title, self.project.lang,
                                           len(self.project.elements()))

    def _update_title(self):
        name = os.path.basename(self.project.path) if self.project.path else "Unsaved project"
        self.window_title.set_subtitle(("• " if self.project.dirty else "") + name)
        self.set_title(f"{name} – SiteForge")

    def _update_status(self):
        count = len(self.project.elements())
        self.status_elements.set_label(f"{count} element{'s' if count != 1 else ''}")
        self.status_zoom.set_label(f"Zoom {round(self.preview.zoom_level() * 100)}%")
        if self.project.dirty:
            self.status_save.set_label("Unsaved changes")
        elif self.project.path:
            self.status_save.set_label(f"Saved — {self.project.path}")
        else:
            self.status_save.set_label("New project")

    def toast(self, message: str, button=None, action=None):
        toast = Adw.Toast(title=GLib.markup_escape_text(message), timeout=4)
        if button:
            toast.set_button_label(button)
            toast.set_action_name(action)
        self.toasts.add_toast(toast)

    # ------------------------------------------------------------ elements
    def select_element(self, sf_id: str | None, from_preview: bool = False):
        self.selected_id = sf_id
        self.sidebar.select(sf_id)
        if not from_preview:
            self.preview.select(sf_id)
        self._show_properties()

    def _insert(self, key, target_id, where):
        sf_id = self.project.add_component(key, target_id or None, where)
        self.selected_id = sf_id
        self._refresh_elements()
        self._show_properties()
        self.preview.selected_id = sf_id
        comp_title = self.project.element(sf_id)[1].title if self.project.element(sf_id) else key
        if key == "image":
            self.toast("Added Image", button="Choose File…", action="win.choose-image")
        else:
            self.toast(f"Added {comp_title}")

    def _on_component_dropped(self, _preview, key, target_id, where):
        self._insert(key, target_id, "after" if where == "end" else where)

    def _on_insert_component(self, _sidebar, key):
        self._insert(key, self.selected_id, "after")
        self.stack.set_visible_child_name("preview")

    def _on_prop_changed(self, _panel, sf_id, name, value):
        entry = self.project.element(sf_id)
        if entry is None:
            return
        # origin="props" keeps the panel's widgets (and keyboard focus) alive.
        self.project.update_element(sf_id, {name: value}, origin="props")

    def _on_element_resized(self, _preview, sf_id, width, height):
        entry = self.project.element(sf_id)
        if entry is None or not {"width", "height"} <= {p.name for p in entry[1].props}:
            return
        self.project.update_element(sf_id, {"width": width, "height": height})
        self._update_status()

    def choose_image(self, sf_id=None, prop="src"):
        """Pick an image file, store it in the project and use it for *sf_id*.

        Without *sf_id* a new Image component is inserted after the selection.
        """
        dialog = Gtk.FileDialog(title="Choose Image")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        images = Gtk.FileFilter(name="Images")
        images.add_mime_type("image/*")
        filters.append(images)
        dialog.set_filters(filters)
        dialog.open(self, None, self._on_image_chosen, (sf_id, prop))

    def _on_image_chosen(self, dialog, result, data):
        try:
            gfile = dialog.open_finish(result)
        except GLib.Error:
            return
        if gfile is not None:
            self.use_image_file(gfile.get_path(), *data)

    def _on_files_dropped(self, _preview, paths, target_id, where):
        where = "after" if where == "end" else where
        target = target_id or None
        added = 0
        for path in paths:
            try:
                url = self.project.add_asset(path)
            except (OSError, ValueError) as err:
                self.toast(f"Skipped {os.path.basename(path)}: {err}")
                continue
            target = self.project.add_component("image", target, where, props={
                "src": url, "alt": os.path.splitext(os.path.basename(path))[0]})
            where = "after"  # keep several dropped images in order
            added += 1
        if added:
            self.select_element(target)
            self.toast(f"Added {added} image{'s' if added > 1 else ''}")

    def use_image_file(self, path, sf_id=None, prop="src"):
        try:
            url = self.project.add_asset(path)
        except (OSError, ValueError) as err:
            self.toast(f"Could not add image: {err}")
            return
        entry = self.project.element(sf_id) if sf_id else None
        if entry is None or entry[1].key != "image":
            sf_id = self.project.add_component("image", self.selected_id, "after",
                                               props={"src": url})
            self.select_element(sf_id)
        else:
            self.project.update_element(sf_id, {prop: url})
        self.toast(f"Added {os.path.basename(path)} to the project")

    def _on_page_changed(self, _panel, key, value):
        if key == "title":
            self.project.set_meta(title=value)
        elif key == "lang":
            self.project.set_meta(lang=value)

    def delete_element(self, sf_id):
        self.project.remove_element(sf_id)
        self.selected_id = None
        self._refresh_elements()
        self._show_properties()
        self.toast("Element deleted")

    # ---------------------------------------------------------------- edits
    def _on_code_changed(self, _pane, part, text):
        if self._loading:
            return
        getattr(self.project, f"set_{part}")(text, origin="code")

    def _on_blocks_changed(self, *_args):
        if self._loading:
            return
        self.project.set_blocks(self.blocks.data(), origin="blocks")

    def _on_tab_changed(self):
        name = self.stack.get_visible_child_name()
        if name == "blocks":
            self.blocks.canvas.queue_draw()
        self.code.flush()
        # The properties panel edits page elements; give the block canvas the room.
        action = self.lookup_action("toggle-properties")
        wanted = action.get_state().get_boolean() if action else True
        self.properties_revealer.set_reveal_child(wanted and name != "blocks")

    # --------------------------------------------------------------- render
    def _base_uri(self):
        directory = os.path.dirname(os.path.abspath(self.project.path)) if self.project.path \
            else GLib.get_home_dir()
        return GLib.filename_to_uri(directory + os.sep, None)

    def schedule_render(self):
        if self._render_source:
            GLib.source_remove(self._render_source)
        self._render_source = GLib.timeout_add(RENDER_DELAY_MS, self._render_now)

    def _render_now(self):
        self._render_source = 0
        self.render()
        return GLib.SOURCE_REMOVE

    def render(self, force=False):
        page = self.project.generate()["index.html"]
        self.preview.show_page(page, self._base_uri(), force=force)

    def run_blocks(self):
        self.code.flush()
        compiled = self.project.compiled_blocks()
        if not compiled:
            self.toast("Add an Events block (yellow) with blocks under it to run.")
            return
        page = self.project.generate(include_blocks=False)["index.html"]
        self.stack.set_visible_child_name("preview")
        self.select_toggle.set_active(False)  # let clicks reach the running script
        self.preview.show_page(page, self._base_uri(), after_load_js=compiled, force=True)
        self.toast("Running blocks in the preview (interact mode)")

    def open_in_browser(self):
        self.code.flush()
        directory = os.path.join(GLib.get_user_cache_dir(), "siteforge", "browser-preview")
        try:
            paths = self.project.export(directory, split=False)
        except OSError as err:
            self.toast(f"Could not write preview: {err.strerror}")
            return
        launcher = Gtk.FileLauncher.new(Gio.File.new_for_path(paths[0]))
        launcher.launch(self, None, self._on_launched)

    def _on_launched(self, launcher, result):
        try:
            launcher.launch_finish(result)
        except GLib.Error as err:
            self.toast(f"Could not open browser: {err.message}")

    # ------------------------------------------------------------ file ops
    def _file_filter(self):
        filters = Gio.ListStore.new(Gtk.FileFilter)
        project_filter = Gtk.FileFilter(name="SiteForge projects")
        project_filter.add_pattern("*.siteforge")
        project_filter.add_mime_type("application/x-siteforge")
        filters.append(project_filter)
        all_filter = Gtk.FileFilter(name="All files")
        all_filter.add_pattern("*")
        filters.append(all_filter)
        return filters, project_filter

    def confirm_discard(self, then):
        """Run *then()* after the user saved or discarded unsaved changes."""
        self.code.flush()
        self.properties.flush()
        if not self.project.dirty:
            then()
            return
        dialog = Adw.AlertDialog(heading="Save Changes?",
                                 body="The current project has unsaved changes. "
                                      "Changes which are not saved will be permanently lost.")
        dialog.add_response("cancel", "_Cancel")
        dialog.add_response("discard", "_Discard")
        dialog.add_response("save", "_Save")
        dialog.set_response_appearance("discard", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("save")
        dialog.set_close_response("cancel")

        def on_response(_dialog, response):
            if response == "discard":
                then()
            elif response == "save":
                self.on_save(after=then)

        dialog.connect("response", on_response)
        dialog.present(self)

    def on_new(self, *_args):
        self.confirm_discard(lambda: self._set_project(Project.blank()))

    def on_open(self, *_args):
        def pick():
            dialog = Gtk.FileDialog(title="Open Project")
            filters, default = self._file_filter()
            dialog.set_filters(filters)
            dialog.set_default_filter(default)
            dialog.open(self, None, self._on_open_done)

        self.confirm_discard(pick)

    def _on_open_done(self, dialog, result):
        try:
            gfile = dialog.open_finish(result)
        except GLib.Error:
            return
        if gfile is not None:
            self.load_file(gfile.get_path())

    def load_file(self, path: str):
        try:
            project = Project.load(path)
        except (OSError, ValueError) as err:
            self.toast(f"Could not open {os.path.basename(path)}: {err}")
            return False
        self._set_project(project)
        self.toast(f"Opened {os.path.basename(path)}")
        return True

    def on_save(self, *_args, after=None):
        self.code.flush()
        self.properties.flush()
        if not self.project.path:
            self.on_save_as(after=after)
            return
        self._save_to(self.project.path, after)

    def on_save_as(self, *_args, after=None):
        dialog = Gtk.FileDialog(title="Save Project")
        filters, default = self._file_filter()
        dialog.set_filters(filters)
        dialog.set_default_filter(default)
        name = os.path.basename(self.project.path) if self.project.path else \
            (self.project.title or "website").strip().replace(" ", "-").lower() + ".siteforge"
        dialog.set_initial_name(name)
        dialog.save(self, None, self._on_save_done, after)

    def _on_save_done(self, dialog, result, after):
        try:
            gfile = dialog.save_finish(result)
        except GLib.Error:
            return
        if gfile is None:
            return
        path = gfile.get_path()
        if not path.endswith(".siteforge"):
            path += ".siteforge"
        self._save_to(path, after)

    def _save_to(self, path, after=None):
        try:
            self.project.save(path)
        except OSError as err:
            self.toast(f"Could not save: {err.strerror or err}")
            return
        self.toast(f"Saved {os.path.basename(path)}")
        if after:
            after()

    def on_export(self, split=False):
        self.code.flush()
        dialog = Gtk.FileDialog(title="Choose Export Folder", accept_label="Export")
        dialog.select_folder(self, None, self._on_export_done, split)

    def _on_export_done(self, dialog, result, split):
        try:
            folder = dialog.select_folder_finish(result)
        except GLib.Error:
            return
        if folder is None:
            return
        try:
            written = self.project.export(folder.get_path(), split=split)
        except OSError as err:
            self.toast(f"Export failed: {err.strerror or err}")
            return
        problems = validate_stripe(self.project.stripe) if self.project.uses_stripe() else []
        names = ", ".join(os.path.basename(p) for p in written)
        self.toast(f"Exported {names}" + (" — check Stripe Settings" if problems else ""))

    def export_blocks_js(self):
        compiled = self.project.compiled_blocks()
        if not compiled:
            self.toast("There are no runnable block scripts yet.")
            return
        dialog = Gtk.FileDialog(title="Export Block JavaScript", initial_name="blocks.js")
        dialog.save(self, None, self._on_export_js_done, compiled)

    def _on_export_js_done(self, dialog, result, compiled):
        try:
            gfile = dialog.save_finish(result)
        except GLib.Error:
            return
        try:
            with open(gfile.get_path(), "w", encoding="utf-8") as fh:
                fh.write(compiled)
        except OSError as err:
            self.toast(f"Could not save: {err.strerror}")
            return
        self.toast(f"Saved {os.path.basename(gfile.get_path())}")

    def on_stripe_settings(self, *_args):
        def save(values):
            if values.get("publishable_key", "").startswith(("sk_", "rk_")):
                values["publishable_key"] = ""
                self.toast("Secret keys must never be put in a website — the key was not saved.")
            if values != self.project.stripe:
                self.project.set_stripe(values)

        StripeDialog(self.project.stripe, save).present(self)

    def _on_close_request(self, *_args):
        if self._close_confirmed:
            return False
        self.code.flush()
        self.properties.flush()
        if not self.project.dirty:
            return False

        def close():
            self._close_confirmed = True
            self.close()

        self.confirm_discard(close)
        return True


_SHORTCUTS_UI = """<?xml version="1.0" encoding="UTF-8"?>
<interface>
  <object class="GtkShortcutsWindow" id="help_overlay">
    <property name="modal">True</property>
    <child>
      <object class="GtkShortcutsSection">
        <property name="section-name">shortcuts</property>
        <child>
          <object class="GtkShortcutsGroup">
            <property name="title">Project</property>
            <child><object class="GtkShortcutsShortcut">
              <property name="title">New project</property>
              <property name="accelerator">&lt;Control&gt;n</property></object></child>
            <child><object class="GtkShortcutsShortcut">
              <property name="title">Open project</property>
              <property name="accelerator">&lt;Control&gt;o</property></object></child>
            <child><object class="GtkShortcutsShortcut">
              <property name="title">Save project</property>
              <property name="accelerator">&lt;Control&gt;s</property></object></child>
            <child><object class="GtkShortcutsShortcut">
              <property name="title">Export single HTML file</property>
              <property name="accelerator">&lt;Control&gt;e</property></object></child>
            <child><object class="GtkShortcutsShortcut">
              <property name="title">Export with separate CSS/JS</property>
              <property name="accelerator">&lt;Control&gt;&lt;Shift&gt;e</property></object></child>
          </object>
        </child>
        <child>
          <object class="GtkShortcutsGroup">
            <property name="title">Editing</property>
            <child><object class="GtkShortcutsShortcut">
              <property name="title">Insert palette item after selection</property>
              <property name="accelerator">Return</property></object></child>
            <child><object class="GtkShortcutsShortcut">
              <property name="title">Insert image from file</property>
              <property name="accelerator">&lt;Control&gt;i</property></object></child>
            <child><object class="GtkShortcutsShortcut">
              <property name="title">Run blocks in preview</property>
              <property name="accelerator">F5</property></object></child>
            <child><object class="GtkShortcutsShortcut">
              <property name="title">Delete selected block (Blocks tab)</property>
              <property name="accelerator">Delete</property></object></child>
            <child><object class="GtkShortcutsShortcut">
              <property name="title">Duplicate selected block (Blocks tab)</property>
              <property name="accelerator">&lt;Control&gt;d</property></object></child>
            <child><object class="GtkShortcutsShortcut">
              <property name="title">Toggle properties panel</property>
              <property name="accelerator">F9</property></object></child>
          </object>
        </child>
        <child>
          <object class="GtkShortcutsGroup">
            <property name="title">View</property>
            <child><object class="GtkShortcutsShortcut">
              <property name="title">Preview / Code / Blocks tab</property>
              <property name="accelerator">&lt;Alt&gt;1 &lt;Alt&gt;2 &lt;Alt&gt;3</property></object></child>
            <child><object class="GtkShortcutsShortcut">
              <property name="title">Reload preview</property>
              <property name="accelerator">&lt;Control&gt;r</property></object></child>
            <child><object class="GtkShortcutsShortcut">
              <property name="title">Zoom in / out / reset</property>
              <property name="accelerator">&lt;Control&gt;plus &lt;Control&gt;minus &lt;Control&gt;0</property></object></child>
            <child><object class="GtkShortcutsShortcut">
              <property name="title">Open in system browser</property>
              <property name="accelerator">&lt;Control&gt;b</property></object></child>
          </object>
        </child>
      </object>
    </child>
  </object>
</interface>
"""
