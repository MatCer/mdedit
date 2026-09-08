#!/usr/bin/env python3
"""mdedit - lightweight Qt Markdown editor with live preview and PDF export.

The preview and the exported PDF both use the stylesheet from the
pdf_translator "md-to-pdf" tool, so what you see is what you get.

usage:
    mdedit [file.md]

shortcuts:
    Ctrl+S    save            Ctrl+O    open
    Ctrl+E    export PDF      Ctrl+N    new
    Ctrl+P    toggle preview  Ctrl+Q    quit
    Ctrl+D    dark preview    F11       fullscreen
    F5        refresh preview
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, QUrl, QSettings, QSize
from PyQt6.QtGui import QAction, QFont, QKeySequence, QTextOption
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QSizePolicy,
    QSplitter,
    QStatusBar,
    QToolBar,
    QToolButton,
    QWidget,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mdcore

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
{dark_css}
</style></head>
<body><div class="page"><div class="markdown-body">
{body}
</div></div></body></html>
"""

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

.markdown-body .admonition {
    background: #31363d;
    border-color: #414750;
    border-left-color: #5b8def;
}

.markdown-body .footnote {
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
    block = re.search(r"@page\s*\{(.*?)\n\}", page_css, re.S)
    if block:
        m = re.search(r"margin:\s*([^;]+);", block.group(1))
        if m:
            # drop the bottom margin: the preview scrolls as one continuous page
            parts = m.group(1).split()
            margin = " ".join(parts[:2]) if len(parts) >= 4 else m.group(1).strip()
    m = re.search(r"html\s*\{[^}]*font-size:\s*([^;]+);", page_css)
    if m:
        font_size = m.group(1).strip()
    return margin, font_size


class Editor(QMainWindow):
    def __init__(self, path: str | None = None):
        super().__init__()
        self.path: Path | None = None
        self.settings = QSettings("matcer", "mdedit")
        self.doc_css, self.page_css = mdcore.load_css()
        self.margin, self.font_size = page_geometry(self.page_css)

        self.edit = QPlainTextEdit()
        self.edit.setFont(QFont("JetBrains Mono", 11))
        self.edit.setWordWrapMode(QTextOption.WrapMode.WordWrap)
        self.edit.setTabStopDistance(32)
        self.edit.textChanged.connect(self.schedule_render)

        self.view = QWebEngineView()

        self.split = QSplitter(Qt.Orientation.Horizontal)
        self.split.addWidget(self.edit)
        self.split.addWidget(self.view)
        self.split.setSizes([600, 700])
        self.setCentralWidget(self.split)

        self.setStatusBar(QStatusBar())
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(300)
        self.timer.timeout.connect(self.render)

        self.dark = self.settings.value("darkPreview", False, type=bool)

        self._build_menu()
        self._build_toolbar()
        self.restore_session()

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
        act("&Save", "Ctrl+S", self.save, f)
        act("Save &As...", "Ctrl+Shift+S", self.save_as, f)
        f.addSeparator()
        act("&Export PDF", "Ctrl+E", self.export_pdf, f)
        act("Export &HTML", "Ctrl+Shift+E", self.export_html, f)
        f.addSeparator()
        act("&Quit", "Ctrl+Q", self.close, f)

        v = m.addMenu("&View")
        act("Toggle &Preview", "Ctrl+P", self.toggle_preview, v)
        act("&Refresh", "F5", self.render, v)
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
        text = self.edit.toPlainText()
        try:
            body = mdcore.render_body(text)
        except Exception as exc:  # keep the last good preview on error
            self.statusBar().showMessage(f"render error: {exc}", 4000)
            return
        html = PREVIEW_SHELL.format(
            doc_css=self.doc_css,
            body=body,
            margin=self.margin,
            font_size=self.font_size,
            dark_css=DARK_CSS if self.dark else "",
            backdrop="#1b1b1b" if self.dark else "#6b6b6b",
        )
        base = QUrl.fromLocalFile(str((self.path or Path.cwd()).parent) + "/")
        pos = self.view.page().scrollPosition()
        self.view.setHtml(html, base)
        words = len(text.split())
        self.statusBar().showMessage(f"{words} words · {len(text)} chars")

    def reload_css(self) -> None:
        mdcore.sync_css(quiet=True)
        self.doc_css, self.page_css = mdcore.load_css()
        self.margin, self.font_size = page_geometry(self.page_css)
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

    def open_file(self) -> None:
        start = str(self.path.parent) if self.path else str(Path.home())
        name, _ = QFileDialog.getOpenFileName(
            self, "Open Markdown", start, "Markdown (*.md *.markdown *.mdown *.txt);;All files (*)"
        )
        if name:
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
        try:
            base = self.path.parent if self.path else Path.cwd()
            pages = mdcore.write_pdf(self.edit.toPlainText(), name, Path(name).stem, base)
            self.statusBar().showMessage(f"exported {name} ({pages} pages)", 5000)
        except Exception as exc:
            QMessageBox.critical(self, "Export failed", str(exc))

    def export_html(self) -> None:
        default = str(self.path.with_suffix(".html")) if self.path else str(Path.home() / "document.html")
        name, _ = QFileDialog.getSaveFileName(self, "Export HTML", default, "HTML (*.html)")
        if not name:
            return
        Path(name).write_text(
            mdcore.build_html(self.edit.toPlainText(), Path(name).stem, inline_css=True),
            encoding="utf-8",
        )
        self.statusBar().showMessage(f"exported {name}", 4000)

    def closeEvent(self, event) -> None:
        self.save_session()
        if self.edit.document().isModified():
            r = QMessageBox.question(
                self,
                "Unsaved changes",
                f"Save changes to {self.path.name if self.path else 'untitled.md'}?",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
            )
            if r == QMessageBox.StandardButton.Cancel:
                event.ignore()
                return
            if r == QMessageBox.StandardButton.Save and not self.save():
                event.ignore()
                return
        event.accept()


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
