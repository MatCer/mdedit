#!/usr/bin/env python3
"""md2pdf - Markdown -> A4 PDF, styled exactly like the pdf_translator
"md-to-pdf" web tool, so exports match wherever you make them.

usage:
    md2pdf file.md [out.pdf]
    md2pdf --html file.md       standalone HTML instead of PDF
    md2pdf --template=NAME file.md   design template (default: the
                                front matter's `template:`, else default)
    md2pdf --templates          list templates in ~/.config/mdedit/templates
    md2pdf --sync               re-pull the stylesheet from pdf_translator
    md2pdf --css                print the stylesheet paths
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mdcore


def mermaid_svgs(text: str) -> list[str | None] | None:
    """Draw ```mermaid blocks with QtWebEngine, loaded only when there are any.

    Without PyQt6 (CLI-only install) the diagrams stay as their source.
    """
    sources = mdcore.mermaid_sources(text)
    if not sources:
        return None
    try:
        import PyQt6.QtWebEngineCore  # noqa: F401  (mermaid_qt imports Qt lazily)
        import mermaid_qt
    except ImportError:
        print("note: PyQt6-WebEngine not installed, mermaid diagrams left as source", file=sys.stderr)
        return None
    svgs = mermaid_qt.render_svgs(sources)
    if None in svgs:
        print(f"note: {svgs.count(None)} mermaid diagram(s) failed to parse, left as source", file=sys.stderr)
    return svgs


def main(argv: list[str]) -> int:
    flags = {a for a in argv if a.startswith("-")}
    args = [a for a in argv if not a.startswith("-")]

    if flags & {"--help", "-h"}:
        print(__doc__.strip())
        return 0
    if "--sync" in flags:
        return 0 if mdcore.sync_css() else 1
    if "--templates" in flags:
        print("\n".join(["default", *mdcore.templates()]))
        return 0
    if "--css" in flags:
        print(mdcore.DOC_CSS_FILE)
        print(mdcore.PAGE_CSS_FILE)
        return 0
    if not args:
        print("usage: md2pdf <file.md> [out.pdf] [--html]", file=sys.stderr)
        return 1

    src = Path(args[0])
    if not src.exists():
        print(f"no such file: {src}", file=sys.stderr)
        return 1

    template = next((f.split("=", 1)[1] for f in flags if f.startswith("--template=")), None)
    want_html = "--html" in flags
    out = Path(args[1]) if len(args) > 1 else src.with_suffix(".html" if want_html else ".pdf")
    text = src.read_text(encoding="utf-8", errors="replace")
    try:
        mdcore.resolve_template(text, template)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    svgs = mermaid_svgs(text)

    if want_html:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            mdcore.build_html(text, src.stem, inline_css=True, mermaid_svgs=svgs, template=template), encoding="utf-8"
        )
        print(out)
        return 0

    pages = mdcore.write_pdf(text, out, src.stem, base_dir=src.parent, mermaid_svgs=svgs, template=template)
    print(f"{out}  ({pages} pages)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
