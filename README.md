# mdedit

A lightweight Markdown editor and PDF exporter for Linux. Qt, not Electron.

Opens `.md` files, edits them with a live preview, and exports a clean A4 PDF
that looks exactly like the preview. The whole thing is about 600 lines of
Python and installs into `~/.local` without root.

![mdedit](docs/screenshot.png)

## Why

Most lightweight Markdown editors hardcode their preview CSS inside the binary,
so you cannot make the PDF look the way you want. mdedit keeps the stylesheet in
a plain CSS file that both the preview and the PDF export read, so what you see
is genuinely what you get, and restyling is just editing CSS.

## Install

```sh
git clone https://github.com/MatCer/mdedit.git
cd mdedit
./install.sh
```

No root needed: it creates a self-contained virtualenv in
`~/.local/share/mdedit-venv` and puts two commands in `~/.local/bin`.

One-liner, if you would rather not keep the checkout:

```sh
git clone --depth 1 https://github.com/MatCer/mdedit.git /tmp/mdedit \
  && /tmp/mdedit/install.sh && rm -rf /tmp/mdedit
```

Then verify:

```sh
~/.local/share/md2pdf/install.sh --check
```

Requires Python 3.10+. On Linux the editor also needs `libxcb-cursor`, which the
installer vendors automatically on Debian/Ubuntu if your system lacks it.

## Use

```sh
mdedit notes.md        # editor with live preview
mdedit                 # empty buffer
md2pdf notes.md        # -> notes.pdf, no GUI
md2pdf notes.md out.pdf
md2pdf --html notes.md # standalone HTML
```

After install, `.md` files open in mdedit on double-click.

### Shortcuts

| | |
|---|---|
| `Ctrl+S` / `Ctrl+O` / `Ctrl+N` | save / open / new |
| `Ctrl+E` | export PDF |
| `Ctrl+Shift+E` | export HTML |
| `Ctrl+P` | toggle preview pane |
| `Ctrl+D` | dark preview |
| `F11` / `Esc` | fullscreen / leave fullscreen |
| `Ctrl+` `+` / `-` / `0` | preview zoom |
| `F5` | refresh preview |
| `Ctrl+R` | reload stylesheet |

The editor remembers window size and position, maximized/fullscreen state, the
split ratio, preview zoom and the dark toggle.

Dark preview only affects the screen. Exported PDFs are always the light
document, byte for byte identical whether dark mode is on or off.

## Markdown support

Tables, fenced code, footnotes, definition lists, abbreviations, admonitions,
table of contents and relative images. Rendered by
[python-markdown](https://python-markdown.github.io/) with the `extra`,
`sane_lists`, `admonition` and `toc` extensions.

Force a page break in the PDF with `<div class="page-break"></div>`.

## Styling

`doc.css` is the document typography, shared by the preview and the PDF.
`page.css` is the A4 page geometry: margins, page size, page numbers.

Edit them in `~/.local/share/md2pdf/`, then hit `Ctrl+R` in the editor or just
re-run `md2pdf`. The preview derives its page margins from `page.css`, so the two
cannot drift apart.

PDF rendering is [WeasyPrint](https://weasyprint.org/), so `@page` rules,
`break-inside`, orphans and widows all work as in a real print stylesheet.

## Uninstall

```sh
~/.local/share/md2pdf/install.sh --uninstall
```

## Credits

The document styling comes from the `md-to-pdf` tool in
[pdf_translator](https://github.com/MatCer/pdf_translator).

## License

MIT
