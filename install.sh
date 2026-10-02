#!/usr/bin/env bash
# install.sh - install mdedit + md2pdf into ~/.local (no root required).
#
#   ./install.sh            install or upgrade
#   ./install.sh --check    verify an existing install, change nothing
#   ./install.sh --uninstall
#
# Everything lands under ~/.local, in a self-contained virtualenv, so nothing
# is installed system-wide and no sudo is needed.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="${MDEDIT_VENV:-$HOME/.local/share/mdedit-venv}"
SHARE="${MDEDIT_SHARE:-$HOME/.local/share/md2pdf}"
BIN="${MDEDIT_BIN:-$HOME/.local/bin}"
APPS="$HOME/.local/share/applications"

ok()   { printf '  \033[32mok\033[0m    %s\n' "$1"; }
warn() { printf '  \033[33mwarn\033[0m  %s\n' "$1"; }
bad()  { printf '  \033[31mfail\033[0m  %s\n' "$1"; FAILED=1; }

# ── check ───────────────────────────────────────────────────────────────
if [ "${1:-}" = "--check" ]; then
    FAILED=0
    echo "checking mdedit install"
    [ -x "$BIN/mdedit" ]      && ok "launcher $BIN/mdedit"   || bad "missing $BIN/mdedit"
    [ -x "$BIN/md2pdf" ]      && ok "cli $BIN/md2pdf"        || bad "missing $BIN/md2pdf"
    [ -f "$SHARE/mdcore.py" ] && ok "shared renderer"        || bad "missing mdcore.py"
    [ -f "$SHARE/mdedit.py" ] && ok "editor"                 || bad "missing mdedit.py"
    [ -f "$SHARE/mermaid.min.js" ] && ok "bundled mermaid.js" \
        || warn "no bundled mermaid.js: diagrams load from the CDN (needs network)"
    [ -f "$SHARE/doc.css" ] && [ -f "$SHARE/page.css" ] \
        && ok "stylesheets" || bad "missing stylesheets"
    "$VENV/bin/python" -c 'import markdown_it, mdit_py_plugins.gfm, weasyprint' 2>/dev/null \
        && ok "cli dependencies" || bad "venv broken (markdown-it-py/mdit-py-plugins/weasyprint)"
    "$VENV/bin/python" -c 'import PyQt6.QtWebEngineWidgets' 2>/dev/null \
        && ok "gui dependencies" || warn "PyQt6 missing: CLI works, editor will not"
    case "$(uname -s)" in
      Linux) [ -f "$VENV/extra-lib/libxcb-cursor.so.0" ] \
                && ok "bundled libxcb-cursor" \
                || warn "no bundled libxcb-cursor (needed by Qt 6.5+ on some distros)" ;;
    esac
    case ":$PATH:" in *":$BIN:"*) ok "$BIN is on PATH" ;;
                      *) warn "$BIN is not on PATH; add it to your shell rc" ;; esac
    if command -v xdg-mime >/dev/null 2>&1; then
        h=$(xdg-mime query default text/markdown 2>/dev/null || true)
        [ "$h" = "mdedit.desktop" ] && ok "default .md handler" \
            || warn "default .md handler is '${h:-none}'"
    fi
    exit "${FAILED:-0}"
fi

# ── uninstall ───────────────────────────────────────────────────────────
if [ "${1:-}" = "--uninstall" ]; then
    rm -rf "$VENV" "$SHARE" "$BIN/mdedit" "$BIN/md2pdf" "$APPS/mdedit.desktop"
    command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APPS" 2>/dev/null || true
    echo "removed mdedit and md2pdf (settings in ~/.config/matcer left alone)"
    exit 0
fi

# ── install ─────────────────────────────────────────────────────────────
echo "==> installing mdedit + md2pdf"
command -v python3 >/dev/null 2>&1 || { echo "python3 is required" >&2; exit 1; }

mkdir -p "$SHARE" "$BIN" "$APPS"

echo "==> python environment"
[ -x "$VENV/bin/python" ] || python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet --upgrade "markdown-it-py>=4.1" "mdit-py-plugins>=0.6" weasyprint
# The editor needs Qt; the CLI does not. Do not fail the whole install if the
# wheels are unavailable for this platform, just leave the user with md2pdf.
if ! "$VENV/bin/pip" install --quiet PyQt6 PyQt6-WebEngine; then
    echo "   PyQt6 unavailable here: installing CLI only (md2pdf)"
fi

# Qt 6.5+ needs libxcb-cursor for its xcb platform plugin, and many distros do
# not ship it by default. Vendor it into the venv so no root install is needed.
if [ "$(uname -s)" = "Linux" ] && [ ! -f "$VENV/extra-lib/libxcb-cursor.so.0" ]; then
    if ! ldconfig -p 2>/dev/null | grep -q libxcb-cursor.so.0; then
        if command -v apt-get >/dev/null 2>&1; then
            echo "==> vendoring libxcb-cursor"
            mkdir -p "$VENV/extra-lib"
            tmp=$(mktemp -d)
            if (cd "$tmp" && apt-get download libxcb-cursor0 >/dev/null 2>&1 && dpkg-deb -x ./*.deb .); then
                cp -a "$tmp"/usr/lib/*/libxcb-cursor.so.0* "$VENV/extra-lib/" 2>/dev/null || true
            fi
            rm -rf "$tmp"
        else
            echo "   note: install libxcb-cursor0 if the editor fails to start"
        fi
    fi
fi

echo "==> files"
install -m 644 "$REPO_DIR/mdcore.py" "$SHARE/mdcore.py"
install -m 644 "$REPO_DIR/mermaid_qt.py" "$SHARE/mermaid_qt.py"
install -m 755 "$REPO_DIR/mdedit.py" "$SHARE/mdedit.py"
install -m 644 "$REPO_DIR/doc.css"   "$SHARE/doc.css"
install -m 644 "$REPO_DIR/page.css"  "$SHARE/page.css"
install -m 755 "$REPO_DIR/install.sh" "$SHARE/install.sh"

# Mermaid is bundled so diagrams render offline. Version is pinned in mdcore.py.
MERMAID_VERSION=$(sed -n 's/^MERMAID_VERSION = "\(.*\)"/\1/p' "$REPO_DIR/mdcore.py")
if curl -fsSL -o "$SHARE/mermaid.min.js.part" \
    "https://cdn.jsdelivr.net/npm/mermaid@$MERMAID_VERSION/dist/mermaid.min.js"; then
    mv "$SHARE/mermaid.min.js.part" "$SHARE/mermaid.min.js"
else
    rm -f "$SHARE/mermaid.min.js.part"
    echo "   note: could not download mermaid.js; diagrams will load from the CDN"
fi

# Launchers are generated so the venv/share paths are baked in correctly.
cat > "$BIN/mdedit" <<EOF
#!/bin/sh
# mdedit - Qt Markdown editor with live preview and PDF export.
VENV="$VENV"
export LD_LIBRARY_PATH="\$VENV/extra-lib\${LD_LIBRARY_PATH:+:\$LD_LIBRARY_PATH}"
exec "\$VENV/bin/python" "$SHARE/mdedit.py" "\$@"
EOF
chmod 755 "$BIN/mdedit"

cat > "$BIN/md2pdf" <<EOF
#!/bin/sh
# md2pdf - Markdown to A4 PDF on the command line.
exec "$VENV/bin/python" "$SHARE/md2pdf.py" "\$@"
EOF
chmod 755 "$BIN/md2pdf"
install -m 755 "$REPO_DIR/md2pdf.py" "$SHARE/md2pdf.py"

echo "==> desktop integration"
sed "s|^Exec=.*|Exec=$BIN/mdedit %f|" "$REPO_DIR/mdedit.desktop" > "$APPS/mdedit.desktop"
chmod 644 "$APPS/mdedit.desktop"
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APPS" 2>/dev/null || true
if command -v xdg-mime >/dev/null 2>&1; then
    for m in text/markdown text/x-markdown; do
        xdg-mime default mdedit.desktop "$m" 2>/dev/null || true
    done
fi

echo
echo "installed:"
echo "  mdedit  <file.md>    editor with live preview"
echo "  md2pdf  <file.md>    command-line PDF export"
case ":$PATH:" in
  *":$BIN:"*) ;;
  *) echo; echo "note: add $BIN to your PATH" ;;
esac
echo
echo "verify with: $SHARE/install.sh --check"
