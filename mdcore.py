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

# Local design templates: <name>/style.css, layered over the default stylesheet.
# Only the default ships in the repo; a document picks one with `template: <name>`
# in its front matter.
TEMPLATES_DIR = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "mdedit" / "templates"
META_KEY = re.compile(r"[A-Za-z][\w-]*")

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


def templates() -> list[str]:
    """Names of the installed templates."""
    if not TEMPLATES_DIR.is_dir():
        return []
    return sorted(d.name for d in TEMPLATES_DIR.iterdir() if (d / "style.css").is_file())


def template_path(name: str | None) -> Path | None:
    """style.css of a template; None for the default. Raises ValueError if unknown."""
    if not name or name == "default":
        return None
    path = TEMPLATES_DIR / name / "style.css"
    # the name comes from the document, so keep it to one directory level
    if not re.fullmatch(r"[\w-][\w.-]*", name) or not path.is_file():
        available = ", ".join(["default", *templates()])
        raise ValueError(f"unknown template {name!r} (available: {available}; see {TEMPLATES_DIR})")
    return path


def template_css(path: Path) -> str:
    """A template's CSS for inlining: relative url()s become absolute file URIs,
    so a logo beside style.css still loads in the preview and HTML export."""
    def absolute(m: re.Match) -> str:
        return f'url("{(path.parent / m.group(2)).as_uri()}")'

    css = path.read_text(encoding="utf-8")
    return re.sub(r"""url\(\s*(['"]?)(?![a-zA-Z][\w+.-]*:|/|#)([^'")]+)\1\s*\)""", absolute, css)


def parse_meta(content: str) -> dict[str, str]:
    """`key: value` lines of a front matter block. Deliberately not full YAML."""
    meta = {}
    for line in content.splitlines():
        key, sep, value = line.partition(":")
        if sep and META_KEY.fullmatch(key.strip()):
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            meta[key.strip()] = value
    return meta


def front_matter(text: str) -> dict[str, str]:
    """Front matter of a document, {} if it has none."""
    tokens = _parser().parse(text)
    return parse_meta(tokens[0].content) if tokens and tokens[0].type == "front_matter" else {}


def set_template(text: str, name: str, today: str) -> str:
    """Point the document at a template: rewrite or add the front matter's
    `template:` line, or prepend a front matter skeleton with the keys the
    templates use (title is also the PDF/HTML title)."""
    m = re.match(r"---\n((?:.*\n)*?)---[ \t]*(?:\n|$)", text)
    line = f"template: {name}\n"
    if not m:
        return f"---\n{line}title: \nheader: \nfooter: {today}\n---\n\n{text}"
    body, n = re.subn(r"^template\s*:.*\n", line, m.group(1), count=1, flags=re.M)
    return "---\n" + (body if n else line + body) + text[m.end(1):]


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
    from mdit_py_plugins.front_matter import front_matter_plugin
    from mdit_py_plugins.gfm import gfm_plugin

    md = (
        MarkdownIt("commonmark")
        .use(gfm_plugin)
        .use(deflist_plugin)
        .use(admon_plugin)
        .use(anchors_plugin, max_level=6)
        .use(front_matter_plugin)
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

    def meta(self, tokens, idx, options, env):
        # Hidden by default; a template can show one, e.g. as a running page header.
        return "".join(
            f'<div class="meta meta-{key}">{escape(value)}</div>\n'
            for key, value in parse_meta(tokens[idx].content).items()
        )

    def ordered_list_open(self, tokens, idx, options, env):
        # WeasyPrint ignores <ol start>; a counter-reset says the same thing to it.
        start = tokens[idx].attrGet("start")
        if start is not None:
            tokens[idx].attrSet("style", f"counter-reset: list-item {int(start) - 1}")
        return self.renderToken(tokens, idx, options, env)

    md.add_render_rule("fence", fence)
    md.add_render_rule("ordered_list_open", ordered_list_open)
    md.add_render_rule("front_matter", meta)
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


def resolve_template(text: str, template: str | None = None) -> Path | None:
    """An explicit template wins over the document's `template:` front matter."""
    return template_path(template or front_matter(text).get("template"))


def build_html(
    text: str,
    title: str,
    inline_css: bool = False,
    mermaid_svgs: list[str | None] | None = None,
    template: str | None = None,
) -> str:
    """Full HTML document. With inline_css it is standalone and self-contained."""
    style = ""
    if inline_css:
        doc_css, page_css = load_css()
        tpl = resolve_template(text, template)
        tpl_css = template_css(tpl) if tpl else ""
        style = f"<style>\n{page_css}\n{doc_css}\n{tpl_css}\n</style>"
    return HTML_SHELL.format(
        title=escape(front_matter(text).get("title", title)), style=style, body=render_body(text, mermaid_svgs)
    )


def write_pdf(
    text: str,
    out_path: str | Path,
    title: str,
    base_dir: str | Path = ".",
    mermaid_svgs: list[str | None] | None = None,
    template: str | None = None,
) -> int:
    """Render Markdown text to an A4 PDF. Returns the page count.

    Without mermaid_svgs the diagrams print as their source. template overrides
    the document's front matter; an unknown one raises ValueError.
    """
    from weasyprint import CSS, HTML

    doc_css, page_css = load_css()
    tpl = resolve_template(text, template)
    stylesheets = [CSS(string=page_css), CSS(string=doc_css)]
    if tpl:
        # loaded by filename so url(logo.png) resolves inside the template dir
        stylesheets.append(CSS(filename=str(tpl)))
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    # base_url lets relative images in the Markdown resolve.
    doc = HTML(string=build_html(text, title, mermaid_svgs=mermaid_svgs), base_url=str(base_dir)).render(
        stylesheets=stylesheets
    )
    doc.write_pdf(str(out))
    return len(doc.pages)
