"""Shared Markdown rendering core for md2pdf (CLI) and mdedit (GUI).

Both tools render through the same code path and stylesheet, so a document looks
identical in the editor preview and in the exported PDF.

Stylesheets are bundled (``doc.css`` and ``page.css`` next to this file) so the
tools work standalone. They can optionally be re-synced from a local checkout of
the pdf_translator project, which is where this styling originally comes from;
set ``MDEDIT_CSS_SOURCE`` to point at its ``md_to_pdf.py`` if you have one.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

SHARE = Path(__file__).resolve().parent
DOC_CSS_FILE = SHARE / "doc.css"
PAGE_CSS_FILE = SHARE / "page.css"

# Optional upstream source of truth. Only used by --sync; absent on most systems.
DEFAULT_SOURCE = Path.home() / "projects/personal/pdf_translator/src/tools/md_to_pdf.py"
SOURCE = Path(os.environ.get("MDEDIT_CSS_SOURCE", DEFAULT_SOURCE))

MD_EXTENSIONS = ["extra", "sane_lists", "admonition", "toc"]
MD_EXTENSION_CONFIGS = {"toc": {"permalink": False}}

HTML_SHELL = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
{style}
</head>
<body>
<div class="markdown-body">
{body}
</div>
</body>
</html>
"""


def sync_css(quiet: bool = False) -> bool:
    """Re-extract DOC_CSS and PAGE_CSS from a pdf_translator checkout.

    Optional: the bundled stylesheets are used when no source is available.
    """
    if not SOURCE.exists():
        if not quiet:
            print(
                f"no CSS source at {SOURCE}\n"
                "(set MDEDIT_CSS_SOURCE to a pdf_translator md_to_pdf.py, "
                "or just use the bundled stylesheet)",
                file=sys.stderr,
            )
        return False
    src = SOURCE.read_text(encoding="utf-8")
    ok = True
    for name, dest in (("DOC_CSS", DOC_CSS_FILE), ("PAGE_CSS", PAGE_CSS_FILE)):
        m = re.search(rf'^{name}\s*=\s*"""(.*?)"""', src, re.S | re.M)
        if not m:
            print(f"could not find {name} in {SOURCE}", file=sys.stderr)
            ok = False
            continue
        SHARE.mkdir(parents=True, exist_ok=True)
        dest.write_text(m.group(1), encoding="utf-8")
        if not quiet:
            print(f"{dest}  ({len(m.group(1))} bytes)")
    return ok


def load_css() -> tuple[str, str]:
    """Return (doc_css, page_css) from the bundled stylesheets."""
    if not DOC_CSS_FILE.exists() or not PAGE_CSS_FILE.exists():
        # Only reachable if the install is damaged; try the optional source.
        if not sync_css(quiet=True):
            raise SystemExit(
                f"stylesheet missing from {SHARE}; reinstall or run: md2pdf --sync"
            )
    return (
        DOC_CSS_FILE.read_text(encoding="utf-8"),
        PAGE_CSS_FILE.read_text(encoding="utf-8"),
    )


def render_body(text: str) -> str:
    """Markdown text -> HTML fragment (no wrapper element)."""
    import markdown

    md = markdown.Markdown(
        extensions=MD_EXTENSIONS,
        extension_configs=MD_EXTENSION_CONFIGS,
        output_format="html5",
    )
    return md.convert(text)


def escape(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_html(text: str, title: str, inline_css: bool = False) -> str:
    """Full HTML document. With inline_css it is standalone and self-contained."""
    style = ""
    if inline_css:
        doc_css, page_css = load_css()
        style = f"<style>\n{page_css}\n{doc_css}\n</style>"
    return HTML_SHELL.format(title=escape(title), style=style, body=render_body(text))


def write_pdf(text: str, out_path: str | Path, title: str, base_dir: str | Path = ".") -> int:
    """Render Markdown text to an A4 PDF. Returns the page count."""
    from weasyprint import CSS, HTML

    doc_css, page_css = load_css()
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    # base_url lets relative images in the Markdown resolve.
    doc = HTML(string=build_html(text, title), base_url=str(base_dir)).render(
        stylesheets=[CSS(string=page_css), CSS(string=doc_css)]
    )
    doc.write_pdf(str(out))
    return len(doc.pages)
