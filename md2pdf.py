#!/usr/bin/env python3
"""md2pdf - Markdown -> A4 PDF, styled exactly like the pdf_translator
"md-to-pdf" web tool, so exports match wherever you make them.

usage:
    md2pdf file.md [out.pdf]
    md2pdf --html file.md       standalone HTML instead of PDF
    md2pdf --sync               re-pull the stylesheet from pdf_translator
    md2pdf --css                print the stylesheet paths
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mdcore


def main(argv: list[str]) -> int:
    flags = {a for a in argv if a.startswith("-")}
    args = [a for a in argv if not a.startswith("-")]

    if flags & {"--help", "-h"}:
        print(__doc__.strip())
        return 0
    if "--sync" in flags:
        return 0 if mdcore.sync_css() else 1
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

    want_html = "--html" in flags
    out = Path(args[1]) if len(args) > 1 else src.with_suffix(".html" if want_html else ".pdf")
    text = src.read_text(encoding="utf-8", errors="replace")

    if want_html:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(mdcore.build_html(text, src.stem, inline_css=True), encoding="utf-8")
        print(out)
        return 0

    pages = mdcore.write_pdf(text, out, src.stem, base_dir=src.parent)
    print(f"{out}  ({pages} pages)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
