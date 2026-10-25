# SiteForge

SiteForge is a native GNOME app for building HTML websites visually. You drag
components onto a live preview, resize them with the mouse, script the page
with Scratch-style blocks, and take payments with Stripe Checkout. It exports
clean `index.html` (plus optional `style.css`, `script.js` and `assets/`)
ready to deploy.

It's written in Python with GTK4, libadwaita, WebKitGTK 6 and GtkSourceView 5,
and it targets Debian 13 "Trixie". It doesn't use Electron, and it makes no
network calls of its own.

## Features

- **Component palette**: Canvas, Iframe, Text Box, Button, **Image**, Text,
  Header, Footer and Stripe. Drag a component onto the preview and a blue line
  shows where it will go (before, after, or inside a header/footer). You can
  also press <kbd>Enter</kbd> on a palette row to insert it after the selected
  element.
- **Images**: drag image files from Files onto the preview, use
  *Menu → Insert Image from File…* (<kbd>Ctrl</kbd>+<kbd>I</kbd>), or pick a
  file in the properties panel. Images are stored in the project file, so a
  `.siteforge` file is self-contained.
- **Drag-to-resize**: select an image, canvas, text box, button or iframe and
  drag the blue handle at its bottom-right corner. Images keep their aspect
  ratio; hold <kbd>Shift</kbd> to resize freely. The new size is written back
  into the HTML: `width`/`height` attributes for images, canvases and iframes,
  and an inline `style` for text boxes and buttons.
- **Preview** (WebKitGTK): live reload with scroll position kept, click to
  select, Desktop/Tablet/Mobile widths, zoom (<kbd>Ctrl</kbd>+scroll), and
  right-click → *Open in System Browser*. Turn off **Select** to interact with
  the page normally.
- **Code tab**: HTML, CSS and JavaScript editors with syntax highlighting, line
  numbers and a *Format* button for each pane. The HTML is the single source
  of truth: edits in the code, the properties panel and the preview all stay
  in sync.
- **Blocks tab**: a Scratch 3-style editor with six colour-coded categories
  (Events, Motion, Looks, Logic, Variables, Stripe), snap-together stacks,
  C-blocks, reporters and a variables panel. Use *Run in Preview*
  (<kbd>F5</kbd>) to run the blocks, or *Export JS* to save the compiled
  script. See [DESIGN.md](DESIGN.md).
- **Stripe Checkout**: settings are kept in the project file, never in the page
  HTML, and are injected when you preview or export. Secret keys are refused.
- **Projects**: New/Open/Save/Save As and `.siteforge` (JSON) files, with
  export as a single file or with separate files.
- Follows the system light/dark mode and accent colour, uses libadwaita
  widgets throughout, and has accessible labels on all controls.

## Install

### From the .deb (Debian 13)

```sh
./build.sh                          # produces siteforge_1.0.0_amd64.deb
sudo apt install ./siteforge_1.0.0_amd64.deb
# or: sudo dpkg -i siteforge_*.deb && sudo apt -f install
```

Then launch **SiteForge** from the Activities overview, or run `siteforge`.
The example projects are installed in `/usr/share/siteforge/examples/`.

### Without root

Running SiteForge never needs root. To install it for your user only:

```sh
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-webkit-6.0 \
                 gir1.2-adw-1 gir1.2-gtksource-5      # runtime libraries, if missing
./build.sh user-install                               # into ~/.local
./build.sh user-uninstall                             # to remove it
```

### From the source tree

```sh
./bin/siteforge                     # or: python3 -m siteforge
./bin/siteforge examples/block-animation.siteforge
```

### Command line

```sh
siteforge [FILE.siteforge]
siteforge --export OUTDIR [--split] FILE.siteforge   # headless export, no window
siteforge --version
```

## Examples

| File | Shows |
|------|-------|
| `examples/simple-page.siteforge` | A plain page: header, image asset, text, iframe map, text boxes, button with hand-written JS, footer |
| `examples/stripe-checkout.siteforge` | Stripe component, the "open Stripe checkout" block, and "when payment succeeds / is canceled" handlers |
| `examples/block-animation.siteforge` | A bouncing-ball animation built only from blocks: variables, forever, if/else, key and click events |

To try Stripe for real, open *Stripe Settings* and enter your `pk_test_…` key
and `price_…` ID. The example ships with a placeholder price and no key.

## Stripe notes

- The generated code loads `https://js.stripe.com/v3/` and calls
  `stripe.redirectToCheckout({ lineItems: [{ price, quantity: 1 }], mode, successUrl, cancelUrl })`.
  `lineItems` is the current form of the older `items` parameter.
- Stripe has deprecated client-only Checkout (`redirectToCheckout`), and newer
  accounts may need it enabled in the Dashboard. If it's unavailable, put a
  **Payment Link** URL in Stripe Settings. When no publishable key is set,
  the button goes to that link instead.
- Add `?checkout=success` / `?checkout=cancel` to your redirect URLs so the
  "when payment succeeds" blocks can tell what happened.
- In the `.siteforge` file the settings are XOR-obfuscated, so they don't
  appear as plain text. This is obfuscation, not real encryption.
  Publishable keys are public by design, and SiteForge refuses `sk_…`/`rk_…`
  secret keys.
- `python3-stripe` is declared as a dependency as required, but the desktop
  app itself doesn't import it, because everything Stripe-related runs in
  the exported page.

## Testing

```sh
sudo apt install xvfb xauth          # plus the runtime libraries above, and nodejs (optional)
./test.sh                            # everything
./test.sh --no-docker                # skip the clean-container install test
python3 -m unittest discover -s tests   # core unit tests only (no display needed)
```

`test.sh` runs these steps:

1. **Unit tests** (`tests/test_core.py`): the HTML span editor, component
   round-trips, the block compiler (generated JS is syntax-checked with
   `node --check` when Node is installed), formatters, the project format,
   Stripe handling, assets and the examples.
2. **Headless export** through the command line.
3. **Window smoke test**: starts the app under `xvfb-run` with
   `--smoke-test`, waits for the window to map and the preview to load, and
   checks for `SITEFORGE_WINDOW_OK`.
4. **GUI interaction tests** (`tests/gui_test.py`, 34 checks), run in the real
   window: drop onto the preview, select through the page, edit properties
   and code, resize with the handle, drop image files, snap blocks together,
   run blocks in the preview, save and reload, and switch device widths.
5. **Package build** with `./build.sh`.
6. **Clean Debian 13 install**: in a fresh `debian:trixie` container (Docker or
   Podman), installs the .deb with apt, checks the desktop file and icon,
   exports and launches the app as an unprivileged user under xvfb, then
   removes the package.

Containers often block WebKit's bubblewrap sandbox, so `test.sh` sets
`WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS=1` for its own runs. A normal
desktop session doesn't need it.

## Project layout

```
siteforge/            Python package
  project.py          project model, .siteforge format, HTML generator, Stripe runtime
  htmldoc.py          source-span HTML parser used for precise edits
  components.py       palette components (render ⇄ parse)
  blocks.py           block definitions + block→JS compiler
  formatter.py        HTML/CSS/JS prettifiers
  ui/                 GTK4/libadwaita interface (window, preview, code, blocks, panels)
data/                 desktop file, AppStream metainfo, MIME type, icon
packaging/debian/     control template, postinst, postrm
examples/             example projects (regenerate with tools/make_examples.py)
tools/                icon and example generators
tests/                unit and GUI tests
```
