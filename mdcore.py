"""Shared Markdown rendering core for md2pdf (CLI) and mdedit (GUI).

Both tools render through the same code path and stylesheet, so a document looks
identical in the editor preview and in the exported PDF.

Stylesheets are bundled (``doc.css`` and ``page.css`` next to this file) so the
tools work standalone. They can optionally be re-synced from a local checkout of
the pdf_translator project, which is where this styling originally comes from;
set ``MDEDIT_CSS_SOURCE`` to point at its ``md_to_pdf.py`` if you have one.
"""

from __future__ import annotations

import base64
import functools
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

MERMAID_VERSION = "11.17.2"
# Installed next to this file by install.sh; a checkout without it uses the CDN.
MERMAID_JS = SHARE / "mermaid.min.js"
MERMAID_CDN = f"https://cdn.jsdelivr.net/npm/mermaid@{MERMAID_VERSION}/dist/mermaid.min.js"

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


def _is_mermaid(token) -> bool:
    return token.info.strip().split(maxsplit=1)[:1] == ["mermaid"]


@functools.cache
def _parser():
    """CommonMark + GFM (tables, task lists, footnotes, alerts, strikethrough),
    plus definition lists, `!!! note` admonitions and heading ids.

    CommonMark rather than python-markdown because the latter needs a blank line
    before a list and 4-space nesting, so ordinary Markdown lost its bullets.
    """
    from markdown_it import MarkdownIt
    from mdit_py_plugins.admon import admon_plugin
    from mdit_py_plugins.anchors import anchors_plugin
    from mdit_py_plugins.deflist import deflist_plugin
    from mdit_py_plugins.gfm import gfm_plugin

    md = (
        MarkdownIt("commonmark")
        .use(gfm_plugin)
        .use(deflist_plugin)
        .use(admon_plugin)
        .use(anchors_plugin, max_level=6)
    )
    default_fence = md.renderer.rules["fence"]

    def fence(self, tokens, idx, options, env):
        token = tokens[idx]
        if _is_mermaid(token):
            svg = next(env["mermaid_svgs"], None) if "mermaid_svgs" in env else None
            if svg:
                return svg_img(svg) + "\n"
            return f'<pre class="mermaid">{escape(token.content)}</pre>\n'
        return default_fence(tokens, idx, options, env)

    md.add_render_rule("fence", fence)
    return md


def render_body(text: str, mermaid_svgs: list[str | None] | None = None) -> str:
    """Markdown text -> HTML fragment (no wrapper element).

    mermaid_svgs (see mermaid_qt.render_svgs) replace the ```mermaid blocks in
    order; a missing or None entry leaves that block as source.
    """
    env = {"mermaid_svgs": iter(mermaid_svgs)} if mermaid_svgs else {}
    return _parser().render(text, env)


def mermaid_sources(text: str) -> list[str]:
    """Source of every ```mermaid block, in document order."""
    return [t.content for t in _parser().parse(text) if t.type == "fence" and _is_mermaid(t)]


def escape(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def svg_img(svg: str) -> str:
    """Mermaid SVG -> <img> with an explicit width, since its root says width="100%"."""
    m = re.search(r"max-width:\s*([\d.]+)px", svg)
    if m:
        svg = re.sub(r'(<svg[^>]*?)\swidth="100%"', rf'\1 width="{m.group(1)}"', svg, count=1)
    data = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f'<figure class="mermaid"><img src="data:image/svg+xml;base64,{data}" alt="diagram"></figure>'


def build_html(text: str, title: str, inline_css: bool = False, mermaid_svgs: list[str | None] | None = None) -> str:
    """Full HTML document. With inline_css it is standalone and self-contained."""
    style = ""
    if inline_css:
        doc_css, page_css = load_css()
        style = f"<style>\n{page_css}\n{doc_css}\n</style>"
    return HTML_SHELL.format(
        title=escape(title), style=style, body=render_body(text, mermaid_svgs)
    )


def write_pdf(
    text: str,
    out_path: str | Path,
    title: str,
    base_dir: str | Path = ".",
    mermaid_svgs: list[str | None] | None = None,
) -> int:
    """Render Markdown text to an A4 PDF. Returns the page count.

    Without mermaid_svgs the diagrams print as their source.
    """
    from weasyprint import CSS, HTML

    doc_css, page_css = load_css()
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    # base_url lets relative images in the Markdown resolve.
    doc = HTML(string=build_html(text, title, mermaid_svgs=mermaid_svgs), base_url=str(base_dir)).render(
        stylesheets=[CSS(string=page_css), CSS(string=doc_css)]
    )
    doc.write_pdf(str(out))
    return len(doc.pages)
