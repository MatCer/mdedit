#!/usr/bin/env python3
"""mdedit - lightweight Qt Markdown editor with live preview and PDF export.

The preview and the exported PDF both use the stylesheet from the
pdf_translator "md-to-pdf" tool, so what you see is what you get.

usage:
    mdedit [file.md | folder]

shortcuts:
    Ctrl+S    save            Ctrl+O    open
    Ctrl+E    export PDF      Ctrl+N    new
    Ctrl+P    toggle preview  Ctrl+Q    quit
    Ctrl+D    dark preview    F11       fullscreen
    Ctrl+1/2  Markdown / PDF preview tab
    Ctrl+Shift+O  open folder   Ctrl+B  toggle sidebar
    F5        refresh preview
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, QUrl, QSettings, QSize, pyqtSignal
from PyQt6.QtGui import QAction, QFileSystemModel, QFont, QKeySequence, QTextOption
from PyQt6.QtPdf import QPdfDocument
from PyQt6.QtPdfWidgets import QPdfView
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import (
    QApplication,
    QDockWidget,
    QFileDialog,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QTabBar,
    QToolBar,
    QToolButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mdcore
import mermaid_qt

PREVIEW_SHELL = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
html {{ background: {backdrop}; }}
body {{ margin: 0; padding: 22px 0 40px; }}
.page {{
    width: 210mm;
    min-height: 297mm;
    margin: 0 auto;
    padding: {margin};
    background: #fff;
    box-sizing: border-box;
    box-shadow: 0 2px 14px rgba(0,0,0,.4);
    font-size: {font_size};
}}
{doc_css}
.markdown-body .meta {{ display: none !important; }}
{mode_css}
{dark_css}
</style>
<script src="{mermaid_js}"></script></head>
<body><div class="page"><div class="markdown-body">
{body}
</div></div>
<script>
if (window.mermaid) {{
  mermaid.initialize({mermaid_config});
  mermaid.run({{querySelector: "pre.mermaid"}}).catch(() => {{}});
}}
</script></body></html>
"""

# Markdown tab: the same typography as a plain readable column, no A4 sheet.
PLAIN_CSS = """
html { background: #fff; }
body { padding: 0; }
.page {
    width: auto;
    max-width: 920px;
    min-height: 0;
    padding: 28px 40px 48px;
    background: transparent;
    box-shadow: none;
}
"""
PLAIN_DARK_CSS = """
html { background: #2b2e33; }
.page { background: transparent; box-shadow: none; }
"""

MARKDOWN_TAB, PDF_TAB = 0, 1
MD_GLOBS = ["*.md", "*.markdown", "*.mdown"]

# Dark preview is a viewing aid only: it never touches the exported PDF.
# Rather than inverting the page (which turns white into harsh black), this is a
# hand-tuned warm grey palette layered over DOC_CSS, so the document keeps its
# structure while being comfortable to read on screen at night.
DARK_CSS = """
/* dark-preview */
html { background: #202225; }
.page {
    background: #2b2e33;
    box-shadow: 0 2px 18px rgba(0,0,0,.55);
}
.markdown-body { color: #ccd0d6; }
.markdown-body h1,
.markdown-body h2,
.markdown-body h3,
.markdown-body h4 { color: #e9ecf0; }
.markdown-body h5,
.markdown-body h6 { color: #a8aeb8; }
.markdown-body h1 { border-bottom-color: #40454d; }
.markdown-body h2 { border-bottom-color: #3a3f46; }
.markdown-body hr { border-top-color: #40454d; }

.markdown-body a { color: #86b0f5; }

.markdown-body blockquote {
    background: #31353b;
    border-left-color: #5a616b;
    color: #b3b9c2;
}

.markdown-body :not(pre) > code {
    background: #35393f;
    border-color: #454b53;
    color: #dfe3e8;
}
.markdown-body pre {
    background: #31353b;
    border-color: #414047;
    color: #dfe3e8;
}
.markdown-body pre code { color: inherit; }

.markdown-body th,
.markdown-body td { border-color: #414750; }
.markdown-body th { background: #363b42; color: #e9ecf0; }
.markdown-body tbody tr:nth-child(even) { background: #2f333a; }

.markdown-body .admonition,
.markdown-body .markdown-alert {
    background: #31363d;
    border-color: #414750;
    border-left-color: #5b8def;
}

.markdown-body .footnotes {
    color: #a8aeb8;
    border-top-color: #40454d;
}

/* images keep their own colours, just take the edge off the brightness */
.markdown-body img { filter: brightness(.92); }
"""


def page_geometry(page_css: str) -> tuple[str, str]:
    """Pull the @page margin and root font-size out of PAGE_CSS so the preview
    page matches the PDF instead of hardcoding a second copy of the numbers."""
    margin = "20mm 18mm"
    font_size = "11pt"
    # drop margin boxes (@top-left {...}) so their own margins are not mistaken
    # for the page's; then the last @page with a margin wins, as in the cascade
    page_css = re.sub(r"@(?:top|bottom|left|right)[\w-]*\s*\{[^{}]*\}", "", page_css)
    for block in re.findall(r"@page[^{]*\{([^{}]*)\}", page_css):
        m = re.search(r"(?:^|[;\s])margin:\s*([^;}]+)", block)
        if m:
            # drop the bottom margin: the preview scrolls as one continuous page
            parts = m.group(1).split()
            margin = " ".join(parts[:2]) if len(parts) >= 4 else m.group(1).strip()
    for m in re.finditer(r"html\s*\{[^}]*font-size:\s*([^;]+);", page_css):
        font_size = m.group(1).strip()
    return margin, font_size


class Editor(QMainWindow):
    # (generation, pdf path or None, page count or error message), from the worker thread
    pdf_ready = pyqtSignal(int, object, object)

    def __init__(self, path: str | None = None):
        super().__init__()
        self.path: Path | None = None
        self.settings = QSettings("matcer", "mdedit")
        self.doc_css, self.page_css = mdcore.load_css()

        self.edit = QPlainTextEdit()
        self.edit.setFont(QFont("JetBrains Mono", 11))
        self.edit.setWordWrapMode(QTextOption.WrapMode.WordWrap)
        self.edit.setTabStopDistance(32)
        self.edit.textChanged.connect(self.schedule_render)

        self.view = QWebEngineView()
        mermaid_qt.allow_remote(self.view.page())

        # Markdown (default): plain readable render. PDF: the A4 page as exported.
        self.tabs = QTabBar()
        self.tabs.addTab("Markdown")
        self.tabs.addTab("PDF")
        self.tabs.setToolTip("Ctrl+1 / Ctrl+2")
        self.tabs.currentChanged.connect(lambda _: self.render())
        preview = QWidget()
        box = QVBoxLayout(preview)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)
        box.addWidget(self.tabs)
        # PDF tab: the real WeasyPrint output, so page breaks, @page headers
        # and footers look exactly like the export.
        self.pdf_doc = QPdfDocument(self)
        self.pdf_view = QPdfView(None)
        self.pdf_view.setDocument(self.pdf_doc)
        self.pdf_view.setPageMode(QPdfView.PageMode.MultiPage)
        self.pdf_view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        self.pdf_view.setPageSpacing(16)
        self.pdf_pool = ThreadPoolExecutor(max_workers=1)
        self.pdf_dir = tempfile.TemporaryDirectory(prefix="mdedit-")
        self.pdf_gen = 0  # bumped per request; stale results are dropped
        self.pdf_busy = False
        self.pdf_again = False  # text changed while a render was running
        self.pdf_ready.connect(self.show_pdf)
        self._mermaid_cache: tuple[list[str], list[str | None]] = ([], [])
        self.stack = QStackedWidget()
        self.stack.addWidget(self.view)
        self.stack.addWidget(self.pdf_view)
        box.addWidget(self.stack)

        self.split = QSplitter(Qt.Orientation.Horizontal)
        self.split.addWidget(self.edit)
        self.split.addWidget(preview)
        self.split.setSizes([600, 700])
        self.setCentralWidget(self.split)

        self.setStatusBar(QStatusBar())
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(300)
        self.timer.timeout.connect(self.render)

        self.dark = self.settings.value("darkPreview", False, type=bool)

        self._build_sidebar()
        self._build_menu()
        self._build_toolbar()
        self.restore_session()

        # the dock's visibility comes from restoreState(); `mdedit somedir` forces it open
        folder = self.settings.value("folder", "", type=str)
        if path and Path(path).is_dir():
            folder, path = path, None
            self.dock.show()
        if folder and Path(folder).is_dir():
            self.set_folder(Path(folder))
        else:
            self.dock.hide()  # an unrooted tree would list the whole filesystem
        if path:
            self.load(Path(path))
        else:
            self.new_file()

    # ── session state ───────────────────────────────────────────────────
    def restore_session(self) -> None:
        """Restore window geometry, split ratio, preview zoom and theme.

        Maximized and fullscreen are stored as explicit flags. restoreGeometry()
        alone is unreliable here: the window is not mapped yet, so the window
        manager can drop the maximized state and we would come back as a plain
        1400x900 window every time.
        """
        geo = self.settings.value("geometry")
        if geo:
            self.restoreGeometry(geo)
        else:
            self.resize(1400, 900)

        state = self.settings.value("windowState")
        if state:
            self.restoreState(state)

        sizes = self.settings.value("splitSizes")
        if sizes:
            try:
                sizes = [int(s) for s in sizes]
                # Ignore a fully collapsed editor: it would look like a hung app.
                if sum(sizes) > 0 and sizes[0] > 0:
                    self.split.setSizes(sizes)
            except (TypeError, ValueError):
                pass

        zoom = self.settings.value("previewZoom", 1.0, type=float)
        if 0.3 <= zoom <= 5.0:
            self.view.setZoomFactor(zoom)

        # Re-apply once the window is actually on screen. Doing it here (before
        # the window is mapped) lets the window manager override us, so the real
        # work happens in showEvent(); this only records what we want.
        self._want_fullscreen = self.settings.value("fullScreen", False, type=bool)
        self._want_maximized = self.settings.value("maximized", False, type=bool)
        self._state_applied = False

    def save_session(self) -> None:
        # saveGeometry() records the *current* frame, which for a maximized
        # window is the screen size. Store the normal geometry as well so
        # un-maximizing later returns to a sensible window instead of fullscreen.
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("windowState", self.saveState())
        self.settings.setValue("maximized", self.isMaximized())
        self.settings.setValue("fullScreen", self.isFullScreen())
        self.settings.setValue("splitSizes", self.split.sizes())
        self.settings.setValue("previewZoom", self.view.zoomFactor())
        self.settings.setValue("darkPreview", self.dark)

    # ── sidebar ─────────────────────────────────────────────────────────
    def _build_sidebar(self) -> None:
        """Folder tree showing only Markdown files; a click opens the file."""
        self.fs = QFileSystemModel(self)
        self.fs.setNameFilters(MD_GLOBS)
        self.fs.setNameFilterDisables(False)  # hide non-matching files, not grey them out
        self.tree = QTreeView()
        self.tree.setModel(self.fs)
        self.tree.setHeaderHidden(True)
        for col in (1, 2, 3):  # size, type, date
            self.tree.hideColumn(col)
        self.tree.clicked.connect(self.open_index)
        self.tree.activated.connect(self.open_index)  # Enter key
        self.dock = QDockWidget("Files", self)
        self.dock.setObjectName("FilesDock")  # required for saveState/restoreState
        self.dock.setWidget(self.tree)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.dock)
        self.dock.hide()

    def set_folder(self, folder: Path) -> None:
        self.fs.setRootPath(str(folder))
        self.tree.setRootIndex(self.fs.index(str(folder)))
        self.dock.setWindowTitle(folder.name or str(folder))
        self.settings.setValue("folder", str(folder))

    def open_folder(self) -> None:
        start = self.fs.rootPath() or (str(self.path.parent) if self.path else str(Path.home()))
        name = QFileDialog.getExistingDirectory(self, "Open Folder", start)
        if name:
            self.set_folder(Path(name))
            self.dock.show()

    def open_index(self, index) -> None:
        if self.fs.isDir(index):
            return
        path = Path(self.fs.filePath(index))
        if path != self.path and self.maybe_save():
            self.load(path)
        self.select_current()  # undo the highlight if the switch was cancelled

    def select_current(self) -> None:
        if self.path and self.fs.rootPath():
            self.tree.setCurrentIndex(self.fs.index(str(self.path)))

    # ── menu ────────────────────────────────────────────────────────────
    def _build_menu(self) -> None:
        def act(name, shortcut, slot, menu):
            a = QAction(name, self)
            a.setShortcut(QKeySequence(shortcut))
            a.triggered.connect(slot)
            menu.addAction(a)
            self.addAction(a)
            return a

        m = self.menuBar()
        f = m.addMenu("&File")
        act("&New", "Ctrl+N", self.new_file, f)
        act("&Open...", "Ctrl+O", self.open_file, f)
        act("Open &Folder...", "Ctrl+Shift+O", self.open_folder, f)
        act("&Save", "Ctrl+S", self.save, f)
        act("Save &As...", "Ctrl+Shift+S", self.save_as, f)
        f.addSeparator()
        act("&Export PDF", "Ctrl+E", self.export_pdf, f)
        act("Export &HTML", "Ctrl+Shift+E", self.export_html, f)
        f.addSeparator()
        act("&Quit", "Ctrl+Q", self.close, f)

        v = m.addMenu("&View")
        act("Toggle &Preview", "Ctrl+P", self.toggle_preview, v)
        sidebar = self.dock.toggleViewAction()
        sidebar.setText("Toggle &Sidebar")
        sidebar.setShortcut(QKeySequence("Ctrl+B"))
        v.addAction(sidebar)
        self.addAction(sidebar)
        act("&Refresh", "F5", self.render, v)
        act("&Markdown Preview", "Ctrl+1", lambda: self.tabs.setCurrentIndex(MARKDOWN_TAB), v)
        act("P&DF Preview", "Ctrl+2", lambda: self.tabs.setCurrentIndex(PDF_TAB), v)
        v.addSeparator()
        self.dark_action = act("&Dark preview", "Ctrl+D", self.toggle_dark, v)
        self.dark_action.setCheckable(True)
        act("&Fullscreen", "F11", self.toggle_fullscreen, v)
        v.addSeparator()
        act("Zoom &In", "Ctrl++", lambda: self.zoom(1), v)
        act("Zoom &Out", "Ctrl+-", lambda: self.zoom(-1), v)
        act("Reset &Zoom", "Ctrl+0", lambda: self.zoom(0), v)
        v.addSeparator()
        act("Reload &Stylesheet", "Ctrl+R", self.reload_css, v)

    # ── toolbar ─────────────────────────────────────────────────────────
    def _build_toolbar(self) -> None:
        """One-click export without going through the File menu.

        Export is a split button: clicking it exports PDF, the arrow offers the
        other formats. Dark preview sits next to it as a plain toggle.
        """
        bar = QToolBar("Main")
        bar.setObjectName("MainToolBar")  # required for saveState/restoreState
        bar.setMovable(False)
        bar.setIconSize(QSize(16, 16))
        bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.addToolBar(bar)

        save = QAction("Save", self)
        save.setToolTip("Save the Markdown file (Ctrl+S)")
        save.triggered.connect(self.save)
        bar.addAction(save)

        bar.addSeparator()

        export_menu = QMenu(self)
        pdf = export_menu.addAction("Export as PDF")
        pdf.triggered.connect(self.export_pdf)
        html = export_menu.addAction("Export as HTML")
        html.triggered.connect(self.export_html)

        self.export_button = QToolButton(self)
        self.export_button.setText("Export PDF ")
        self.export_button.setToolTip("Export to PDF (Ctrl+E). Use the arrow for other formats.")
        self.export_button.setMenu(export_menu)
        self.export_button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.export_button.clicked.connect(self.export_pdf)
        bar.addWidget(self.export_button)

        spacer = QWidget(self)
        spacer.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        bar.addWidget(spacer)

        self.dark_button = QToolButton(self)
        self.dark_button.setText("Dark preview")
        self.dark_button.setToolTip("Dark preview, affects the screen only (Ctrl+D)")
        self.dark_button.setCheckable(True)
        self.dark_button.clicked.connect(self.toggle_dark)
        bar.addWidget(self.dark_button)

        self.sync_dark_ui()

    # ── rendering ───────────────────────────────────────────────────────
    def schedule_render(self) -> None:
        self.timer.start()
        self.update_title()

    def render(self) -> None:
        if self.tabs.currentIndex() == PDF_TAB:
            self.stack.setCurrentWidget(self.pdf_view)
            self.render_pdf()
            return
        self.stack.setCurrentWidget(self.view)
        text = self.edit.toPlainText()
        try:
            body = mdcore.render_body(text)
        except Exception as exc:  # keep the last good preview on error
            self.statusBar().showMessage(f"render error: {exc}", 4000)
            return
        note = ""
        try:
            tpl = mdcore.resolve_template(text)
            tpl_css = mdcore.template_css(tpl) if tpl else ""
        except (ValueError, OSError) as exc:  # half-typed name, unreadable file: use the default
            tpl_css, note = "", f"template: {exc} · "
        margin, font_size = page_geometry(self.page_css + tpl_css)
        mermaid_config = {"startOnLoad": False, "theme": "dark" if self.dark else "default"}
        html = PREVIEW_SHELL.format(
            doc_css=self.doc_css + tpl_css,
            body=body,
            margin=margin,
            font_size=font_size,
            mode_css=PLAIN_CSS,
            dark_css=DARK_CSS + PLAIN_DARK_CSS if self.dark else "",
            backdrop="#1b1b1b" if self.dark else "#6b6b6b",
            mermaid_js=mermaid_qt.mermaid_script_url(),
            mermaid_config=json.dumps(mermaid_config),
        )
        base = QUrl.fromLocalFile(str((self.path or Path.cwd()).parent) + "/")
        self.view.setHtml(html, base)
        words = len(text.split())
        self.statusBar().showMessage(f"{note}{words} words · {len(text)} chars")

    def mermaid_svgs(self, text: str) -> list[str | None]:
        """Diagrams for the PDF tab, redrawn only when a mermaid block changed."""
        sources = mdcore.mermaid_sources(text)
        if sources != self._mermaid_cache[0]:
            try:
                svgs = mermaid_qt.render_svgs(sources)
            except TimeoutError:
                svgs = [None] * len(sources)
            self._mermaid_cache = (sources, svgs)
        return self._mermaid_cache[1]

    def render_pdf(self) -> None:
        """Render the PDF off the UI thread; at most one job runs, the latest text wins."""
        if self.pdf_busy:
            self.pdf_again = True
            return
        self.pdf_busy, self.pdf_again = True, False
        self.pdf_gen += 1
        gen, text = self.pdf_gen, self.edit.toPlainText()
        out = Path(self.pdf_dir.name) / f"preview-{gen}.pdf"
        base = self.path.parent if self.path else Path.cwd()
        title = self.path.stem if self.path else "untitled"
        svgs = self.mermaid_svgs(text)  # Qt: must stay on this thread

        def job() -> None:
            try:
                pages = mdcore.write_pdf(text, out, title, base, mermaid_svgs=svgs)
                self.pdf_ready.emit(gen, out, pages)
            except Exception as exc:  # half-typed template name, bad CSS, ...
                self.pdf_ready.emit(gen, None, str(exc))

        self.pdf_pool.submit(job)

    def show_pdf(self, gen: int, out: Path | None, info: object) -> None:
        self.pdf_busy = False
        if out is None:
            self.statusBar().showMessage(f"PDF preview: {info}", 6000)
        else:
            # swapping the document resets the view, so keep the reading position
            bar = self.pdf_view.verticalScrollBar()
            pos = bar.value()
            old = self.pdf_doc.status() == QPdfDocument.Status.Ready
            self.pdf_doc.load(str(out))
            if old:
                QTimer.singleShot(0, lambda: bar.setValue(pos))
            for stale in Path(self.pdf_dir.name).glob("preview-*.pdf"):
                if stale != out:
                    stale.unlink(missing_ok=True)
            self.statusBar().showMessage(f"{info} pages", 3000)
        if self.pdf_again and self.tabs.currentIndex() == PDF_TAB:
            self.render_pdf()

    def reload_css(self) -> None:
        mdcore.sync_css(quiet=True)
        self.doc_css, self.page_css = mdcore.load_css()
        self.render()
        self.statusBar().showMessage("stylesheet reloaded", 3000)

    def toggle_dark(self) -> None:
        """Dark preview is a screen-only setting; exports stay light."""
        self.dark = not self.dark
        # Persist immediately so the choice survives a crash or a kill.
        self.settings.setValue("darkPreview", self.dark)
        self.sync_dark_ui()
        self.render()
        self.statusBar().showMessage(
            "dark preview on (screen only, PDF stays light)" if self.dark else "dark preview off",
            3000,
        )

    def sync_dark_ui(self) -> None:
        """Keep the toolbar button and the menu item showing the same state."""
        self.dark_button.setChecked(self.dark)
        self.dark_action.setChecked(self.dark)

    def zoom(self, direction: int) -> None:
        if self.tabs.currentIndex() == PDF_TAB:
            v = self.pdf_view
            if direction == 0:
                v.setZoomMode(QPdfView.ZoomMode.FitToWidth)
            else:
                f = v.zoomFactor()
                v.setZoomMode(QPdfView.ZoomMode.Custom)
                v.setZoomFactor(max(0.3, min(5.0, f + 0.1 * direction)))
            return
        f = self.view.zoomFactor()
        self.view.setZoomFactor(1.0 if direction == 0 else max(0.3, min(5.0, f + 0.1 * direction)))

    def showEvent(self, event) -> None:
        """Apply the saved maximized/fullscreen state once the window is mapped.

        The window manager overrides an early showMaximized(): measured on this
        setup, calling it before ~100ms after show() gets silently reverted,
        while a short delay sticks. Hence the deferred re-assert rather than
        setting the state in __init__.
        """
        super().showEvent(event)
        if self._state_applied:
            return
        self._state_applied = True
        if self._want_fullscreen:
            QTimer.singleShot(250, self.showFullScreen)
        elif self._want_maximized:
            QTimer.singleShot(250, self.showMaximized)

    def toggle_fullscreen(self) -> None:
        """F11 toggles; Esc also leaves fullscreen so you cannot get stuck."""
        if self.isFullScreen():
            self.showNormal()
            if self.settings.value("maximized", False, type=bool):
                self.showMaximized()
        else:
            self.showFullScreen()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape and self.isFullScreen():
            self.toggle_fullscreen()
            return
        super().keyPressEvent(event)

    def toggle_preview(self) -> None:
        """Hide or show the preview pane, remembering the last split ratio."""
        sizes = self.split.sizes()
        if sizes[1] == 0:
            self.split.setSizes(getattr(self, "_last_split", [600, 700]))
        else:
            self._last_split = sizes
            self.split.setSizes([sum(sizes), 0])

    # ── file ops ────────────────────────────────────────────────────────
    def update_title(self) -> None:
        name = self.path.name if self.path else "untitled.md"
        dirty = "*" if self.edit.document().isModified() else ""
        self.setWindowTitle(f"{dirty}{name} - mdedit")

    def new_file(self) -> None:
        if not self.maybe_save():
            return
        self.path = None
        self.edit.setPlainText("# Untitled\n\n")
        self.edit.document().setModified(False)
        self.update_title()
        self.render()

    def load(self, path: Path) -> None:
        """Open a file, or start an empty buffer at that path if it is new.

        Opening a non-existent path is normal (`mdedit notes.md` to create it),
        so that is not an error; only a genuinely unreadable file is.
        """
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            self.path = path
            self.edit.setPlainText("")
            self.edit.document().setModified(False)
            self.update_title()
            self.render()
            self.statusBar().showMessage(f"new file: {path}", 4000)
            return
        except OSError as exc:
            QMessageBox.critical(self, "Cannot open file", f"{path}\n\n{exc}")
            return

        self.path = path
        self.edit.setPlainText(text)
        self.edit.document().setModified(False)
        self.update_title()
        self.render()
        self.select_current()

    def open_file(self) -> None:
        start = str(self.path.parent) if self.path else str(Path.home())
        name, _ = QFileDialog.getOpenFileName(
            self, "Open Markdown", start, "Markdown (*.md *.markdown *.mdown *.txt);;All files (*)"
        )
        if name and self.maybe_save():
            self.load(Path(name))

    def save(self) -> bool:
        if not self.path:
            return self.save_as()
        try:
            self.path.write_text(self.edit.toPlainText(), encoding="utf-8")
        except OSError as exc:
            # Never report success on a failed write: the close handler uses this
            # return value to decide whether it is safe to discard the buffer.
            QMessageBox.critical(self, "Save failed", f"{self.path}\n\n{exc}")
            return False
        self.edit.document().setModified(False)
        self.update_title()
        self.statusBar().showMessage(f"saved {self.path}", 3000)
        return True

    def save_as(self) -> bool:
        start = str(self.path) if self.path else str(Path.home() / "untitled.md")
        name, _ = QFileDialog.getSaveFileName(self, "Save Markdown", start, "Markdown (*.md)")
        if not name:
            return False
        self.path = Path(name)
        return self.save()

    def export_pdf(self) -> None:
        default = str(self.path.with_suffix(".pdf")) if self.path else str(Path.home() / "document.pdf")
        name, _ = QFileDialog.getSaveFileName(self, "Export PDF", default, "PDF (*.pdf)")
        if not name:
            return
        text = self.edit.toPlainText()
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            base = self.path.parent if self.path else Path.cwd()
            svgs = mermaid_qt.render_svgs(mdcore.mermaid_sources(text))
            pages = mdcore.write_pdf(text, name, Path(name).stem, base, mermaid_svgs=svgs)
            self.statusBar().showMessage(f"exported {name} ({pages} pages)", 5000)
        except Exception as exc:
            QMessageBox.critical(self, "Export failed", str(exc))
        finally:
            QApplication.restoreOverrideCursor()

    def export_html(self) -> None:
        default = str(self.path.with_suffix(".html")) if self.path else str(Path.home() / "document.html")
        name, _ = QFileDialog.getSaveFileName(self, "Export HTML", default, "HTML (*.html)")
        if not name:
            return
        text = self.edit.toPlainText()
        try:
            svgs = mermaid_qt.render_svgs(mdcore.mermaid_sources(text))
            Path(name).write_text(
                mdcore.build_html(text, Path(name).stem, inline_css=True, mermaid_svgs=svgs),
                encoding="utf-8",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Export failed", str(exc))
            return
        self.statusBar().showMessage(f"exported {name}", 4000)

    def maybe_save(self) -> bool:
        """Ask about unsaved changes; False means the user cancelled (or save failed)."""
        if not self.edit.document().isModified():
            return True
        r = QMessageBox.question(
            self,
            "Unsaved changes",
            f"Save changes to {self.path.name if self.path else 'untitled.md'}?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
        )
        if r == QMessageBox.StandardButton.Cancel:
            return False
        return r == QMessageBox.StandardButton.Discard or self.save()

    def closeEvent(self, event) -> None:
        self.save_session()
        if self.maybe_save():
            event.accept()
        else:
            event.ignore()


def main() -> int:
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__.strip())
        return 0
    app = QApplication(sys.argv)
    app.setApplicationName("mdedit")
    app.setDesktopFileName("mdedit")
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    win = Editor(args[0] if args else None)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
