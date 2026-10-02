"""Render Mermaid diagrams to SVG in an offscreen QtWebEngine page.

WeasyPrint runs no JavaScript, so PDF export draws the diagrams here first and
hands mdcore.write_pdf() finished SVGs. Used by the editor and, when PyQt6 is
installed, by the md2pdf CLI.
"""

from __future__ import annotations

import json
import os
import sys

import mdcore

# Labels as SVG <text>, not HTML in <foreignObject>: WeasyPrint cannot draw the latter.
EXPORT_CONFIG = {
    "startOnLoad": False,
    "theme": "default",
    "htmlLabels": False,
    "flowchart": {"htmlLabels": False},
}

PAGE = """<!DOCTYPE html><html><head><meta charset="utf-8">
<script src="{script}"></script></head><body><script>
mermaid.initialize({config});
(async () => {{
  const out = [];
  for (const [i, src] of {sources}.entries()) {{
    try {{ out.push((await mermaid.render("mmd" + i, src)).svg); }}
    catch (e) {{ out.push(null); }}
  }}
  window.__svgs = out;
}})();
</script></body></html>"""


def mermaid_script_url() -> str:
    """Bundled mermaid.min.js when installed, otherwise the pinned CDN build."""
    from PyQt6.QtCore import QUrl

    if mdcore.MERMAID_JS.exists():
        return QUrl.fromLocalFile(str(mdcore.MERMAID_JS)).toString()
    return mdcore.MERMAID_CDN


def allow_remote(page) -> None:
    """Let a page loaded from a file:// base fetch the CDN fallback.

    Only when mermaid.js is not bundled: the preview runs whatever raw HTML the
    document contains, so remote access stays off whenever it is not needed.
    """
    from PyQt6.QtWebEngineCore import QWebEngineSettings

    if mdcore.MERMAID_JS.exists():
        return
    page.settings().setAttribute(
        QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True
    )


def render_svgs(sources: list[str], timeout_ms: int = 20000) -> list[str | None]:
    """SVG markup per source, None for a diagram that failed to parse.

    Blocks in a local event loop. Raises TimeoutError if mermaid never finishes,
    e.g. the script could not be loaded.
    """
    if not sources:
        return []
    from PyQt6 import sip
    from PyQt6.QtCore import QEventLoop, QTimer, QUrl
    from PyQt6.QtWebEngineCore import QWebEnginePage
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:  # CLI: no display needed
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication([sys.argv[0]])

    # json.dumps does not escape "</", which would end the <script> early.
    src_js = json.dumps(sources).replace("</", "<\\/")
    html = PAGE.format(
        script=mermaid_script_url(), config=json.dumps(EXPORT_CONFIG), sources=src_js
    )

    page = QWebEnginePage()
    allow_remote(page)
    loop = QEventLoop()
    result: list = []

    def got(value) -> None:
        if value is not None and not result:
            result.append(value)
            loop.quit()

    poll = QTimer()
    poll.setInterval(50)
    poll.timeout.connect(lambda: page.runJavaScript("window.__svgs || null", got))
    deadline = QTimer()
    deadline.setSingleShot(True)
    deadline.timeout.connect(loop.quit)
    deadline.start(timeout_ms)

    page.setHtml(html, QUrl.fromLocalFile(str(mdcore.SHARE) + "/"))
    poll.start()
    loop.exec()
    poll.stop()
    deadline.stop()
    sip.delete(page)  # now, not deleteLater(): the CLI has no loop left to run it

    if not result:
        raise TimeoutError("mermaid did not finish rendering (is mermaid.min.js reachable?)")
    return result[0]
