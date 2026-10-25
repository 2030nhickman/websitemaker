"""Stripe Settings dialog."""

from __future__ import annotations

from gi.repository import Adw, Gtk

from ..project import STRIPE_DEFAULTS, validate_stripe

MODES = ("payment", "subscription")


class StripeDialog(Adw.PreferencesDialog):
    def __init__(self, settings: dict, on_save):
        super().__init__(title="Stripe Settings", search_enabled=False)
        self._on_save = on_save
        self.rows = {}
        page = Adw.PreferencesPage(title="Stripe", icon_name="auth-smartcard-symbolic")

        keys = Adw.PreferencesGroup(
            title="Checkout",
            description=("Stored in the project file (obfuscated), never in the page HTML. "
                         "It is injected into the page when you preview or export."))
        self._entry(keys, "publishable_key", "Publishable key (pk_test_… / pk_live_…)", settings)
        self._entry(keys, "price_id", "Price ID (price_…)", settings)
        mode = Adw.ComboRow(title="Mode", model=Gtk.StringList.new(["One-time payment",
                                                                    "Subscription"]))
        mode.set_selected(MODES.index(settings.get("mode", "payment"))
                          if settings.get("mode") in MODES else 0)
        keys.add(mode)
        self.mode_row = mode
        page.add(keys)

        urls = Adw.PreferencesGroup(
            title="Redirects",
            description=("Add ?checkout=success / ?checkout=cancel so the "
                         "\"when payment succeeds\" blocks can detect the result."))
        self._entry(urls, "success_url", "Success URL", settings)
        self._entry(urls, "cancel_url", "Cancel URL", settings)
        page.add(urls)

        optional = Adw.PreferencesGroup(title="Optional")
        self._entry(optional, "customer_email", "Customer email", settings)
        self._entry(optional, "payment_link", "Payment Link URL (used when no key is set)", settings)
        page.add(optional)

        self.problems = Adw.PreferencesGroup()
        self.problem_label = Gtk.Label(wrap=True, xalign=0)
        self.problem_label.add_css_class("error")
        self.problems.add(self.problem_label)
        self.problems.set_visible(False)
        page.add(self.problems)

        self.add(page)
        self.connect("closed", self._on_closed)
        self._validate()

    def _entry(self, group, key, title, settings):
        row = Adw.EntryRow(title=title, text=settings.get(key, STRIPE_DEFAULTS[key]))
        row.connect("changed", lambda *_: self._validate())
        group.add(row)
        self.rows[key] = row

    def values(self) -> dict:
        data = {key: row.get_text().strip() for key, row in self.rows.items()}
        data["mode"] = MODES[self.mode_row.get_selected()]
        return data

    def _validate(self):
        problems = validate_stripe(self.values())
        self.problem_label.set_label("\n".join(problems))
        self.problems.set_visible(bool(problems))
        for key in ("publishable_key", "price_id"):
            row = self.rows[key]
            bad = any(key.split("_")[0] in p.lower() for p in problems) if problems else False
            if bad:
                row.add_css_class("error")
            else:
                row.remove_css_class("error")

    def _on_closed(self, *_args):
        self._on_save(self.values())
